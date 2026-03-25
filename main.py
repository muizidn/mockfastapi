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

app = FastAPI(title="JSON Project IDE")
DATA_DIR = "./data"

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# --- FILTER ENGINE ---


def evaluate_condition(item: Dict, condition: str) -> bool:
    """Parses 'field op value' logic. Now supports 'regex' operator."""
    try:
        # Added 'regex' to the operator list
        match = re.match(r"(\w+)\s*(==|!=|>=|<=|>|<|regex)\s*(.+)", condition.strip())
        if not match:
            return False

        field, op_str, raw_val = match.groups()
        item_val = item.get(field)

        if item_val is None:
            return False

        # Cleaning value quotes
        val = raw_val.strip().strip("'").strip('"')

        # Regex specific logic
        if op_str == "regex":
            return bool(re.search(val, str(item_val), re.IGNORECASE))

        # Standard comparison logic with type casting
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
    """Evaluates logic strings. Supports: (a > 1 AND b regex '^test') OR c == 1"""
    if not filter_str:
        return data

    filtered_results = []
    for item in data:
        processed_query = filter_str
        # Find all conditions including the new 'regex' keyword
        conditions = re.findall(
            r"(\w+\s*(?:==|!=|>=|<=|>|<|regex)\s*[^()&| ]+)", filter_str
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


# --- DYNAMIC OPENAPI GENERATOR ---


def generate_openapi_spec(resource: str):
    """Generates spec WITHOUT /items/ in the paths"""
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
                            "description": "Regex example: username regex '^admin' AND age > 20",
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
    if os.path.exists(path):
        os.remove(path)
        return {"message": "Project Deleted"}
    raise HTTPException(status_code=404)


# --- CLEAN CRUD ROUTES (NO /items/) ---


@app.get("/api/v1/{resource}")
async def read_all(resource: str, filter: Optional[str] = Query(None)):
    data = get_resource_data(resource)
    return apply_complex_filter(data, filter) if filter else data


@app.post("/api/v1/{resource}")
async def create_item(resource: str, item: Dict[str, Any] = Body(...)):
    data = get_resource_data(resource)
    data.append(item)
    save_resource_data(resource, data)
    return {"status": "ok"}


@app.post("/api/v1/{resource}/bulk/update")
async def bulk_overwrite(resource: str, items: Any = Body(...)):
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


@app.get("/")
async def ui():
    return FileResponse("index.html")
