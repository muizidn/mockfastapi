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
    """Parses 'field op value' logic (e.g., gpa > 1)."""
    try:
        # Regex to split: field, operator, value
        match = re.match(r"(\w+)\s*(==|!=|>=|<=|>|<)\s*(.+)", condition.strip())
        if not match: return False
        
        field, op_str, raw_val = match.groups()
        item_val = item.get(field)
        
        if item_val is None: return False

        # Handle type casting for comparison
        val = raw_val.strip().strip("'").strip('"')
        try:
            if "." in val:
                val = float(val)
                item_val = float(item_val)
            else:
                val = int(val)
                item_val = int(item_val)
        except ValueError:
            val = str(val)
            item_val = str(item_val)

        ops = {
            "==": operator.eq, "!=": operator.ne,
            ">": operator.gt, "<": operator.lt,
            ">=": operator.ge, "<=": operator.le
        }
        return ops[op_str](item_val, val)
    except Exception:
        return False

def apply_complex_filter(data: List[Dict], filter_str: str) -> List[Dict]:
    """Evaluates strings like '(gpa > 1 AND gpa < 3) OR id == zoo'."""
    if not filter_str: return data
    
    filtered_results = []
    for item in data:
        processed_query = filter_str
        # Find all basic conditions: field op value
        conditions = re.findall(r"(\w+\s*[!=><]+\s*[^()&| ]+)", filter_str)
        
        for cond in conditions:
            res = evaluate_condition(item, cond)
            processed_query = processed_query.replace(cond, str(res))
        
        # Normalize logic for Python eval
        processed_query = processed_query.replace("AND", "and").replace("OR", "or")
        
        try:
            # Restricted eval for safety
            if eval(processed_query, {"__builtins__": {}}, {}):
                filtered_results.append(item)
        except Exception:
            continue
    return filtered_results

# --- CORE UTILITIES ---

def get_project_data(project: str) -> List[Dict[str, Any]]:
    path = os.path.join(DATA_DIR, f"{project}.json")
    if not os.path.exists(path): return []
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def save_project_data(project: str, data: Any):
    path = os.path.join(DATA_DIR, f"{project}.json")
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)

# --- DYNAMIC OPENAPI GENERATOR ---

def generate_openapi_spec(project: str):
    base = f"/api/v1/{project}/items"
    return {
        "openapi": "3.0.0",
        "info": {"title": f"API Docs: {project}", "version": "1.1.0"},
        "paths": {
            base: {
                "get": {
                    "tags": [project],
                    "summary": "List items with complex search",
                    "parameters": [{
                        "name": "filter",
                        "in": "query",
                        "required": False,
                        "description": "Example: (gpa > 1 AND gpa < 3) OR id == zoo",
                        "schema": {"type": "string"}
                    }],
                    "responses": {"200": {"description": "Filtered array"}}
                },
                "post": {
                    "tags": [project],
                    "summary": "Bulk Overwrite",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "array"}}}},
                    "responses": {"200": {"description": "Saved"}}
                }
            },
            f"{base}/{{item_id}}": {
                "get": {"tags": [project], "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "OK"}}},
                "put": {"tags": [project], "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}], "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}}, "responses": {"200": {"description": "OK"}}},
                "delete": {"tags": [project], "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "OK"}}}
            }
        }
    }

# --- ROUTES ---

@app.get("/docs/{project}", include_in_schema=False)
async def get_project_docs(project: str):
    return get_swagger_ui_html(openapi_url=f"/openapi/{project}.json", title=f"{project} Docs")

@app.get("/openapi/{project}.json", include_in_schema=False)
async def get_open_api_endpoint(project: str):
    return generate_openapi_spec(project)

@app.get("/api/v1/projects")
async def list_projects():
    return [os.path.basename(f).replace(".json", "") for f in glob.glob(os.path.join(DATA_DIR, "*.json"))]

@app.delete("/api/v1/projects/{project}")
async def delete_project_file(project: str):
    path = os.path.join(DATA_DIR, f"{project}.json")
    if os.path.exists(path):
        os.remove(path)
        return {"message": "Deleted"}
    raise HTTPException(status_code=404)

@app.get("/api/v1/{project}/items")
async def read_all(project: str, filter: Optional[str] = Query(None)):
    data = get_project_data(project)
    if filter:
        return apply_complex_filter(data, filter)
    return data

@app.post("/api/v1/{project}/items")
async def save_all_items(project: str, items: Any = Body(...)):
    save_project_data(project, items)
    return {"status": "ok"}

@app.get("/api/v1/{project}/items/{item_id}")
async def read_one(project: str, item_id: str):
    data = get_project_data(project)
    item = next((i for i in data if str(i.get("id")) == item_id), None)
    if not item: raise HTTPException(status_code=404)
    return item

@app.put("/api/v1/{project}/items/{item_id}")
async def update_item(project: str, item_id: str, updated_item: Dict[str, Any] = Body(...)):
    data = get_project_data(project)
    for i, item in enumerate(data):
        if str(item.get("id")) == item_id:
            data[i] = updated_item
            save_project_data(project, data)
            return updated_item
    raise HTTPException(status_code=404)

@app.delete("/api/v1/{project}/items/{item_id}")
async def delete_item(project: str, item_id: str):
    data = get_project_data(project)
    new_data = [item for item in data if str(item.get("id")) != item_id]
    save_project_data(project, new_data)
    return {"message": "Deleted"}

@app.get("/")
async def ui():
    return FileResponse('index.html')