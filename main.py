from fastapi import FastAPI, HTTPException, Body, Path, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from typing import List, Dict, Any, Optional
from starlette.responses import Response
import json
import os
import glob
import re
import operator
import jsonschema
from jsonschema import ValidationError
import aiosqlite
import asyncio
import traceback
from datetime import datetime
from collections import deque

app = FastAPI(title="JSON Project IDE")
DATA_DIR = "./data"
SCHEMA_DIR = "./data/schema"
FUNCTIONS_DIR = "./data/functions"
DB_PATH = "./data/api_logs.db"

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
if not os.path.exists(SCHEMA_DIR):
    os.makedirs(SCHEMA_DIR)
if not os.path.exists(FUNCTIONS_DIR):
    os.makedirs(FUNCTIONS_DIR)

# WebSocket clients for real-time updates
ws_clients = set()

# --- DATABASE SETUP ---


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                method TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                params TEXT,
                headers TEXT,
                query_params TEXT,
                request_body TEXT,
                response_body TEXT,
                status_code INTEGER,
                duration_ms REAL,
                function_logs TEXT
            )
        """)
        await db.commit()


async def log_api_call(log_data: Dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO api_logs (timestamp, method, endpoint, params, headers, query_params, request_body, response_body, status_code, duration_ms, function_logs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                log_data.get("timestamp"),
                log_data.get("method"),
                log_data.get("endpoint"),
                log_data.get("params"),
                log_data.get("headers"),
                log_data.get("query_params"),
                log_data.get("request_body"),
                log_data.get("response_body"),
                log_data.get("status_code"),
                log_data.get("duration_ms"),
                log_data.get("function_logs"),
            ),
        )
        await db.commit()
        return log_data


async def broadcast_log(log_data: Dict):
    for client in ws_clients.copy():
        try:
            await client.send_json(log_data)
        except:
            ws_clients.discard(client)


# --- API LOGGER MIDDLEWARE ---


class LoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["path"].startswith("/ws/")
            or scope["path"].startswith("/_next/")
            or scope["path"] == "/api/v1/logs"
            or scope["path"] == "/logs"
            or scope["path"] == "/functions"
            or scope["path"] == "/"
        ):
            await self.app(scope, receive, send)
            return

        start_time = datetime.now()
        request_body = None
        body_bytes = b""

        if scope["method"] in ["POST", "PUT", "PATCH"]:
            while True:
                message = await receive()
                if message["type"] == "http.request":
                    body_bytes += message.get("body", b"")
                if not message.get("more_body"):
                    break

            if body_bytes:
                request_body = body_bytes.decode("utf-8")

        status_code = 200
        response_body = []

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    response_body.append(message["body"])
            await send(message)

        async def receive_wrapper():
            if body_bytes:
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            return await receive()

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            raise

        duration = (datetime.now() - start_time).total_seconds() * 1000

        headers = {}
        for name, value in scope.get("headers", []):
            name_str = name.decode("utf-8")
            if name_str not in ["authorization", "cookie", "host"]:
                headers[name_str] = value.decode("utf-8")

        query_string = scope.get("query_string", b"").decode("utf-8")
        query_params = {}
        if query_string:
            for param in query_string.split("&"):
                if "=" in param:
                    key, value = param.split("=", 1)
                    query_params[key] = value

        full_body = b"".join(response_body).decode("utf-8", errors="replace")[:10000]

        log_data = {
            "timestamp": datetime.now().isoformat(),
            "method": scope["method"],
            "endpoint": scope["path"],
            "params": None,
            "headers": json.dumps(headers),
            "query_params": json.dumps(query_params) if query_params else None,
            "request_body": request_body[:10000] if request_body else None,
            "response_body": full_body,
            "status_code": status_code,
            "duration_ms": round(duration, 2),
            "function_logs": None,
        }

        try:
            await log_api_call(log_data)
        except Exception as e:
            print(f"Log error: {e}")


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


# --- FUNCTION UTILITIES ---


def get_function(name: str) -> Optional[Dict]:
    path = os.path.join(FUNCTIONS_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, PermissionError, OSError):
        return None


def save_function_data(name: str, func_data: Dict):
    path = os.path.join(FUNCTIONS_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(func_data, f, indent=4)


def remove_function_file(name: str):
    path = os.path.join(FUNCTIONS_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)


def execute_function(func_data: Dict, params: Dict) -> Any:
    body = func_data.get("body", "")
    resources = func_data.get("resources", [])
    available_data = {}
    for res in resources:
        available_data[res] = get_resource_data(res)

    logs = []
    try:
        func_code = compile(body, "<string>", "exec")

        safe_builtins = {
            "__builtins__": {
                "__import__": __import__,
                "print": lambda *args, **kwargs: logs.append(
                    " ".join(str(a) for a in args)
                ),
                "set": set,
                "frozenset": frozenset,
            },
            "True": True,
            "False": False,
            "None": None,
            "abs": abs,
            "all": all,
            "any": any,
            "bin": bin,
            "bool": bool,
            "bytes": bytes,
            "chr": chr,
            "dict": dict,
            "dir": dir,
            "divmod": divmod,
            "enumerate": enumerate,
            "filter": filter,
            "float": float,
            "format": format,
            "hash": hash,
            "hex": hex,
            "int": int,
            "isinstance": isinstance,
            "issubclass": issubclass,
            "iter": iter,
            "len": len,
            "list": list,
            "map": map,
            "max": max,
            "min": min,
            "next": next,
            "object": object,
            "oct": oct,
            "ord": ord,
            "pow": pow,
            "range": range,
            "repr": repr,
            "reversed": reversed,
            "round": round,
            "set": set,
            "slice": slice,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
            "zip": zip,
            "type": type,
            "vars": vars,
        }

        safe_builtins.update(
            {
                "json": __import__("json"),
                "re": __import__("re"),
                "datetime": __import__("datetime"),
                "math": __import__("math"),
                "random": __import__("random"),
                "collections": __import__("collections"),
                "itertools": __import__("itertools"),
                "functools": __import__("functools"),
                "uuid": __import__("uuid"),
                "hashlib": __import__("hashlib"),
                "base64": __import__("base64"),
                "time": __import__("time"),
                "calendar": __import__("calendar"),
                "copy": __import__("copy"),
                "string": __import__("string"),
                "textwrap": __import__("textwrap"),
            }
        )

        func_globals = {
            "data": available_data,
            "params": params,
            "result": None,
            **safe_builtins,
        }

        exec(func_code, func_globals)
        return {"result": func_globals.get("result"), "logs": logs}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"❌ Error: {str(e)}\n\n📋 Logs:\n"
            + "\n".join(logs)
            + "\n\n🔍 Trace:\n"
            + traceback.format_exc(),
        )


# --- DYNAMIC OPENAPI GENERATOR ---


def generate_openapi_spec(resource: str):
    base = f"/api/v1/r/{resource}"
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
                            "description": "Filter examples: status == Active, stock.quantity > 5",
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

app.add_middleware(LoggingMiddleware)


@app.on_event("startup")
async def startup():
    await init_db()


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


# --- API LOG ROUTES (must be before CRUD routes) ---


@app.get("/api/v1/logs")
async def get_logs(
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    method: Optional[str] = None,
    endpoint: Optional[str] = None,
    status_code: Optional[int] = None,
    sort_by: str = Query(default="timestamp"),
    sort_order: str = Query(default="desc"),
):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM api_logs WHERE 1=1"
        params = []

        if method:
            query += " AND method = ?"
            params.append(method)
        if endpoint:
            query += " AND endpoint LIKE ?"
            params.append(f"%{endpoint}%")
        if status_code:
            query += " AND status_code = ?"
            params.append(status_code)

        order_map = {
            "timestamp": "timestamp",
            "method": "method",
            "endpoint": "endpoint",
            "status_code": "status_code",
            "duration_ms": "duration_ms",
        }
        order = order_map.get(sort_by, "timestamp")
        order_dir = "DESC" if sort_order == "desc" else "ASC"
        query += f" ORDER BY {order} {order_dir}"

        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM api_logs WHERE 1=1"
            + (f" AND method = '{method}'" if method else "")
            + (f" AND endpoint LIKE '%{endpoint}%'" if endpoint else "")
            + (f" AND status_code = {status_code}" if status_code else "")
        )
        count_row = await cursor.fetchone()
        total = count_row[0] if count_row else 0

        return {
            "logs": [dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@app.delete("/api/v1/logs")
async def clear_logs():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM api_logs")
        await db.commit()
    return {"status": "ok", "message": "All logs cleared"}


# --- FUNCTION ROUTES ---


@app.get("/api/v1/functions")
async def list_functions():
    files = glob.glob(os.path.join(FUNCTIONS_DIR, "*.json"))
    funcs = []
    for f in files:
        name = os.path.basename(f).replace(".json", "")
        func_data = get_function(name)
        if func_data:
            funcs.append(
                {
                    "name": name,
                    "description": func_data.get("description", ""),
                    "params": func_data.get("params", []),
                    "resources": func_data.get("resources", []),
                }
            )
    return funcs


@app.post("/api/v1/functions")
async def create_function(func: Dict = Body(...)):
    name = func.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Function name is required")
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        raise HTTPException(status_code=400, detail="Invalid function name")
    if get_function(name):
        raise HTTPException(status_code=400, detail="Function already exists")
    func_data = {
        "name": name,
        "description": func.get("description", ""),
        "params": func.get("params", []),
        "resources": func.get("resources", []),
        "body": func.get("body", ""),
    }
    save_function_data(name, func_data)
    return {"status": "ok", "name": name}


@app.get("/api/v1/functions/{name}")
async def get_function_details(name: str):
    func_data = get_function(name)
    if not func_data:
        raise HTTPException(status_code=404, detail="Function not found")
    return func_data


@app.put("/api/v1/functions/{name}")
async def update_function(name: str, func: Dict = Body(...)):
    if not get_function(name):
        raise HTTPException(status_code=404, detail="Function not found")
    func_data = {
        "name": name,
        "description": func.get("description", ""),
        "params": func.get("params", []),
        "resources": func.get("resources", []),
        "body": func.get("body", ""),
    }
    save_function_data(name, func_data)
    return {"status": "ok"}


@app.delete("/api/v1/functions/{name}")
async def delete_func(name: str):
    if not get_function(name):
        raise HTTPException(status_code=404, detail="Function not found")
    remove_function_file(name)
    return {"status": "ok"}


@app.post("/api/v1/functions/{name}/run")
async def run_function(name: str, params: Dict = Body(default={})):
    func_data = get_function(name)
    if not func_data:
        raise HTTPException(status_code=404, detail="Function not found")
    result = execute_function(func_data, params)
    return result


@app.post("/api/v1/functions/{name}/test")
async def test_function(name: str, params: Dict = Body(default={})):
    func_data = get_function(name)
    if not func_data:
        raise HTTPException(status_code=404, detail="Function not found")
    result = execute_function(func_data, params)
    return result


# --- CLEAN CRUD ROUTES ---


@app.get("/api/v1/r/{resource}")
async def read_all(resource: str, filter: Optional[str] = Query(None)):
    data = get_resource_data(resource)
    return apply_complex_filter(data, filter) if filter else data


@app.post("/api/v1/r/{resource}")
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


@app.post("/api/v1/r/{resource}/bulk/update")
async def bulk_overwrite(resource: str, items: Any = Body(...)):
    schema = get_resource_schema(resource)
    if schema:
        for i, item in enumerate(items):
            valid, error = validate_against_schema(item, schema)
            if not valid:
                raise HTTPException(status_code=400, detail=f"Item {i}: {error}")
    save_resource_data(resource, items)
    return {"status": "ok"}


@app.get("/api/v1/r/{resource}/{item_id}")
async def read_one(resource: str, item_id: str):
    data = get_resource_data(resource)
    item = next((i for i in data if str(i.get("id")) == item_id), None)
    if not item:
        raise HTTPException(status_code=404)
    return item


@app.put("/api/v1/r/{resource}/{item_id}")
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


@app.delete("/api/v1/r/{resource}/{item_id}")
async def delete_item(resource: str, item_id: str):
    data = get_resource_data(resource)
    new_data = [item for item in data if str(item.get("id")) != item_id]
    save_resource_data(resource, new_data)
    return {"message": "Deleted"}


# --- SCHEMA ROUTES ---


@app.get("/api/v1/r/{resource}/schema")
async def get_schema(resource: str):
    schema = get_resource_schema(resource)
    if schema is None:
        raise HTTPException(
            status_code=404, detail="No schema defined for this resource"
        )
    return schema


@app.put("/api/v1/r/{resource}/schema")
async def set_schema(resource: str, schema: Dict = Body(...)):
    try:
        jsonschema.Draft7Validator.check_schema(schema)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid schema: {e}")
    save_resource_schema(resource, schema)
    return {"status": "ok"}


@app.delete("/api/v1/r/{resource}/schema")
async def delete_schema(resource: str):
    delete_resource_schema(resource)
    return {"status": "ok"}


# --- BANNER ROUTES ---

BANNER_DIR = "./data/banner-images"


@app.get("/api/v1/banners")
async def get_banners():
    if not os.path.exists(BANNER_DIR):
        return []
    images = sorted(
        [
            f
            for f in os.listdir(BANNER_DIR)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
        ]
    )
    return [f"/banner-images/{img}" for img in images]


@app.get("/banner-images/{filename}")
async def serve_banner_image(filename: str):
    path = os.path.join(BANNER_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)


# --- WEBSOCKET ---


@app.websocket("/ws/logs")
async def websocket_logs(websocket):
    await websocket.accept()
    ws_clients.add(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except Exception:
        ws_clients.discard(websocket)


# --- UI ROUTES ---


@app.get("/functions")
async def functions_ui():
    return FileResponse("functions.html")


@app.get("/logs")
async def logs_ui():
    return FileResponse("logs.html")


@app.get("/")
async def ui():
    return FileResponse("index.html")
