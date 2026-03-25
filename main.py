from fastapi import FastAPI, HTTPException, Body, Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from typing import List, Dict, Any, Optional
import json
import os
import glob

app = FastAPI(title="JSON Project IDE")
DATA_DIR = "./data"

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# --- CORE UTILITIES ---

def get_project_data(project: str) -> List[Dict[str, Any]]:
    path = os.path.join(DATA_DIR, f"{project}.json")
    if not os.path.exists(path):
        return []
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
    """Generates a full CRUD OpenAPI definition for Swagger UI to consume"""
    items_path = f"/api/v1/{project}/items"
    item_detail_path = f"/api/v1/{project}/items/{{item_id}}"
    
    spec = {
        "openapi": "3.0.0",
        "info": {"title": f"CRUD API: {project}", "version": "1.0.0"},
        "paths": {
            items_path: {
                "get": {
                    "tags": [project],
                    "summary": "List all items",
                    "responses": {"200": {"description": "List of items"}}
                },
                "post": {
                    "tags": [project],
                    "summary": "Bulk Overwrite or Create New Array",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "array"}}}},
                    "responses": {"200": {"description": "Saved successfully"}}
                }
            },
            item_detail_path: {
                "get": {
                    "tags": [project],
                    "summary": "Get a single item by ID",
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Item found"}, "404": {"description": "Not found"}}
                },
                "put": {
                    "tags": [project],
                    "summary": "Update an item by ID",
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Updated"}, "404": {"description": "Not found"}}
                },
                "delete": {
                    "tags": [project],
                    "summary": "Delete an item by ID",
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Deleted"}, "404": {"description": "Not found"}}
                }
            }
        }
    }
    return spec

# --- SWAGGER UI ROUTES ---

@app.get("/docs/{project}", include_in_schema=False)
async def get_project_docs(project: str):
    """Serves a dedicated Swagger UI for a specific project file"""
    return get_swagger_ui_html(
        openapi_url=f"/openapi/{project}.json",
        title=f"{project} - API Docs"
    )

@app.get("/openapi/{project}.json", include_in_schema=False)
async def get_open_api_endpoint(project: str):
    """The JSON endpoint Swagger UI calls to get the structure above"""
    return generate_openapi_spec(project)

# --- ACTUAL API ENDPOINTS ---

@app.get("/api/v1/projects")
async def list_projects():
    """Returns list of filenames in /data"""
    files = glob.glob(os.path.join(DATA_DIR, "*.json"))
    return [os.path.basename(f).replace(".json", "") for f in files]

@app.delete("/api/v1/projects/{project}")
async def delete_project_file(project: str):
    """Deletes the physical .json file"""
    path = os.path.join(DATA_DIR, f"{project}.json")
    if os.path.exists(path):
        os.remove(path)
        return {"message": f"File {project}.json deleted"}
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/api/v1/{project}/items")
async def read_all(project: str):
    return get_project_data(project)

@app.post("/api/v1/{project}/items")
async def save_all_items(project: str, items: Any = Body(...)):
    save_project_data(project, items)
    return {"status": "ok", "project": project, "count": len(items) if isinstance(items, list) else 1}

@app.get("/api/v1/{project}/items/{item_id}")
async def read_one(project: str, item_id: str):
    data = get_project_data(project)
    item = next((i for i in data if str(i.get("id")) == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.put("/api/v1/{project}/items/{item_id}")
async def update_item(project: str, item_id: str, updated_item: Dict[str, Any] = Body(...)):
    data = get_project_data(project)
    found = False
    for i, item in enumerate(data):
        if str(item.get("id")) == item_id:
            data[i] = updated_item
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Item not found")
    save_project_data(project, data)
    return updated_item

@app.delete("/api/v1/{project}/items/{item_id}")
async def delete_item(project: str, item_id: str):
    data = get_project_data(project)
    new_data = [item for item in data if str(item.get("id")) != item_id]
    if len(new_data) == len(data):
        raise HTTPException(status_code=404, detail="Item not found")
    save_project_data(project, new_data)
    return {"message": "Deleted successfully"}

@app.get("/")
async def ui():
    return FileResponse('index.html')