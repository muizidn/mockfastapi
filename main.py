from fastapi import FastAPI, HTTPException, Body, Path, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from typing import List, Dict, Any, Optional
import json
import os
import glob
import re
import operator
import jsonschema
from jsonschema import ValidationError

app = FastAPI(title="JSON Project IDE")
DATA_DIR = "./data"
SCHEMA_DIR = "./data/schema"

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
if not os.path.exists(SCHEMA_DIR):
    os.makedirs(SCHEMA_DIR)

# --- FILTER ENGINE ---


def fuzzy_match(text: str, pattern: str) -> bool:
    text = str(text).lower()
    pattern = pattern.lower()
    pattern_idx = 0
    for char in text:
        if pattern_idx < len(pattern) and char == pattern[pattern_idx]:
            pattern_idx += 1
    if pattern_idx == len(pattern):
        return True
    if len(pattern) < 3:
        return pattern in text
    return False


def get_nested_value(item: Dict, field: str) -> Any:
    keys = field.split(".")
    val = item
    for key in keys:
        if isinstance(val, dict):
            val = val.get(key)
        else:
            return None
        if val is None:
            return None
    return val


def evaluate_condition(item: Dict, condition: str) -> bool:
    try:
        match = re.match(
            r"([\w.]+)\s*(==|!=|>=|<=|>|<|regex|fuzzy)\s*(.+)", condition.strip()
        )
        if not match:
            return False

        field, op_str, raw_val = match.groups()
        item_val = get_nested_value(item, field)

        if item_val is None:
            return False

        val = raw_val.strip().strip("'").strip('"')

        if op_str == "regex":
            return bool(re.search(val, str(item_val), re.IGNORECASE))

        if op_str == "fuzzy":
            return fuzzy_match(item_val, val)

        try:
            if "." in val:
                val, item_val = float(val), float(item_val)
            else:
                val, item_val = int(val), int(item_val)
        except ValueError:
            val, item_val = str(val), str(item_val)

        ops = {
            "==": operator.eq,
            "!=": operator.ne,
            ">": operator.gt,
            "<": operator.lt,
            ">=": operator.ge,
            "<=": operator.le,
        }
        return ops[op_str](item_val, val)
    except Exception:
        return False


def apply_complex_filter(data: List[Dict], filter_str: str) -> List[Dict]:
    if not filter_str:
        return data

    filtered_results = []
    for item in data:
        processed_query = filter_str
        conditions = re.findall(
            r"([\w.]+\s*(?:==|!=|>=|<=|>|<|regex|fuzzy)\s*[^()&| ]+)", filter_str
        )

        for cond in conditions:
            res = evaluate_condition(item, cond)
            processed_query = processed_query.replace(cond, str(res))

        processed_query = processed_query.replace("AND", "and").replace("OR", "or")

        try:
            if eval(processed_query, {"__builtins__": {}}, {}):
                filtered_results.append(item)
        except Exception:
            continue
    return filtered_results


# --- CORE UTILITIES ---


def get_resource_data(resource: str) -> List[Dict[str, Any]]:
    path = os.path.join(DATA_DIR, f"{resource}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_resource_data(resource: str, data: Any):
    path = os.path.join(DATA_DIR, f"{resource}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


def get_resource_schema(resource: str) -> Optional[Dict]:
    path = os.path.join(SCHEMA_DIR, f"{resource}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def save_resource_schema(resource: str, schema: Dict):
    path = os.path.join(SCHEMA_DIR, f"{resource}.json")
    with open(path, "w") as f:
        json.dump(schema, f, indent=4)


def delete_resource_schema(resource: str):
    path = os.path.join(SCHEMA_DIR, f"{resource}.json")
    if os.path.exists(path):
        os.remove(path)


def validate_against_schema(data: Any, schema: Dict) -> tuple[bool, Optional[str]]:
    try:
        jsonschema.validate(instance=data, schema=schema)
        return True, None
    except ValidationError as e:
        return False, e.message
    except Exception as e:
        return False, str(e)


# --- DYNAMIC OPENAPI GENERATOR ---


def generate_openapi_spec(resource: str):
    base = f"/api/v1/{resource}"
    return {
        "openapi": "3.0.0",
        "info": {"title": f"API Docs: {resource}", "version": "1.2.0"},
        "paths": {
            base: {
                "get": {
                    "tags": [resource],
                    "summary": "List/Search resource",
                    "parameters": [
                        {
                            "name": "filter",
                            "in": "query",
                            "description": "Filter examples: status == Active, stock.quantity > 5, name fuzzy 'john'",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "tags": [resource],
                    "summary": "Create Single Item",
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
            f"{base}/bulk/update": {
                "post": {
                    "tags": [resource],
                    "summary": "Bulk Overwrite",
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "array"}}}
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
            f"{base}/{{item_id}}": {
                "get": {
                    "tags": [resource],
                    "parameters": [
                        {
                            "name": "item_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
                "put": {
                    "tags": [resource],
                    "parameters": [
                        {
                            "name": "item_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    },
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "tags": [resource],
                    "parameters": [
                        {
                            "name": "item_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            },
            f"{base}/schema": {
                "get": {
                    "tags": [resource],
                    "summary": "Get Resource Schema",
                    "responses": {"200": {"description": "OK"}},
                },
                "put": {
                    "tags": [resource],
                    "summary": "Set Resource Schema",
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    },
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "tags": [resource],
                    "summary": "Delete Resource Schema",
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    }


# --- ROUTES ---


@app.get("/docs/{resource}", include_in_schema=False)
async def get_resource_docs(resource: str):
    return get_swagger_ui_html(
        openapi_url=f"/openapi/{resource}.json", title=f"{resource} Docs"
    )


@app.get("/openapi/{resource}.json", include_in_schema=False)
async def get_open_api_endpoint(resource: str):
    return generate_openapi_spec(resource)


@app.get("/api/v1/resources")
async def list_resources():
    files = glob.glob(os.path.join(DATA_DIR, "*.json"))
    return [os.path.basename(f).replace(".json", "") for f in files]


@app.delete("/api/v1/resources/{resource}")
async def delete_resource_file(resource: str):
    path = os.path.join(DATA_DIR, f"{resource}.json")
    schema_path = os.path.join(SCHEMA_DIR, f"{resource}.json")
    if os.path.exists(path):
        os.remove(path)
    if os.path.exists(schema_path):
        os.remove(schema_path)
    return {"message": "Resource Deleted"}


# --- CLEAN CRUD ROUTES ---


@app.get("/api/v1/{resource}")
async def read_all(resource: str, filter: Optional[str] = Query(None)):
    data = get_resource_data(resource)
    return apply_complex_filter(data, filter) if filter else data


@app.post("/api/v1/{resource}")
async def create_item(resource: str, item: Dict[str, Any] = Body(...)):
    schema = get_resource_schema(resource)
    if schema:
        valid, error = validate_against_schema(item, schema)
        if not valid:
            raise HTTPException(
                status_code=400, detail=f"Schema validation failed: {error}"
            )

    data = get_resource_data(resource)
    data.append(item)
    save_resource_data(resource, data)
    return {"status": "ok"}


@app.post("/api/v1/{resource}/bulk/update")
async def bulk_overwrite(resource: str, items: Any = Body(...)):
    schema = get_resource_schema(resource)
    if schema:
        for i, item in enumerate(items):
            valid, error = validate_against_schema(item, schema)
            if not valid:
                raise HTTPException(status_code=400, detail=f"Item {i}: {error}")

    save_resource_data(resource, items)
    return {"status": "ok"}


@app.get("/api/v1/{resource}/{item_id}")
async def read_one(resource: str, item_id: str):
    data = get_resource_data(resource)
    item = next((i for i in data if str(i.get("id")) == item_id), None)
    if not item:
        raise HTTPException(status_code=404)
    return item


@app.put("/api/v1/{resource}/{item_id}")
async def update_item(
    resource: str, item_id: str, updated_item: Dict[str, Any] = Body(...)
):
    schema = get_resource_schema(resource)
    if schema:
        valid, error = validate_against_schema(updated_item, schema)
        if not valid:
            raise HTTPException(
                status_code=400, detail=f"Schema validation failed: {error}"
            )

    data = get_resource_data(resource)
    for i, item in enumerate(data):
        if str(item.get("id")) == item_id:
            data[i] = updated_item
            save_resource_data(resource, data)
            return updated_item
    raise HTTPException(status_code=404)


@app.delete("/api/v1/{resource}/{item_id}")
async def delete_item(resource: str, item_id: str):
    data = get_resource_data(resource)
    new_data = [item for item in data if str(item.get("id")) != item_id]
    save_resource_data(resource, new_data)
    return {"message": "Deleted"}


# --- SCHEMA ROUTES ---


@app.get("/api/v1/{resource}/schema")
async def get_schema(resource: str):
    schema = get_resource_schema(resource)
    if schema is None:
        raise HTTPException(
            status_code=404, detail="No schema defined for this resource"
        )
    return schema


@app.put("/api/v1/{resource}/schema")
async def set_schema(resource: str, schema: Dict = Body(...)):
    try:
        jsonschema.Draft7Validator.check_schema(schema)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid schema: {e}")

    save_resource_schema(resource, schema)
    return {"status": "ok"}


@app.delete("/api/v1/{resource}/schema")
async def delete_schema(resource: str):
    delete_resource_schema(resource)
    return {"status": "ok"}


@app.get("/")
async def ui():
    return FileResponse("index.html")
