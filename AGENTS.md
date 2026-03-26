# MockFastAPI - Agent Documentation

## Environment Setup

### 1. Activate UV Environment
```bash
cd /mnt/DATA/Work/mockfastapi
source .venv/bin/activate
# or use uv
uv venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies
**Always use `uv` for package management:**
```bash
uv pip install -r requirements.txt
# or
uv add <package>
```

**Important packages:**
- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `jsonschema` - JSON schema validation
- `aiosqlite` - Async SQLite support
- `websockets` - WebSocket support (optional)

## Project Structure

```
/mnt/DATA/Work/mockfastapi/
├── main.py              # FastAPI application
├── index.html           # Main IDE UI
├── functions.html       # Functions editor UI
├── logs.html           # API logs UI
├── requirements.txt     # Python dependencies
├── Dockerfile          # Docker configuration
├── docker-compose.yml   # Docker Compose
├── Makefile           # Build/run commands
├── data/              # Data directory
│   ├── api_logs.db    # SQLite database for API logs
│   ├── schema/        # JSON schema definitions
│   ├── functions/     # Function definitions (*.json)
│   └── *.json         # Resource data files
└── .venv/             # Virtual environment
```

## Running the Application

### Development
```bash
cd /mnt/DATA/Work/mockfastapi
source .venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Docker
```bash
docker-compose up --build
# or
make up
```

## API Structure

### Resources (use `/api/v1/r/{resource}`)
- `GET /api/v1/r/{resource}` - List items
- `POST /api/v1/r/{resource}` - Create item
- `GET /api/v1/r/{resource}/{item_id}` - Get single item
- `PUT /api/v1/r/{resource}/{item_id}` - Update item
- `DELETE /api/v1/r/{resource}/{item_id}` - Delete item
- `POST /api/v1/r/{resource}/bulk/update` - Bulk overwrite
- `GET /api/v1/r/{resource}/schema` - Get schema
- `PUT /api/v1/r/{resource}/schema` - Set schema
- `DELETE /api/v1/r/{resource}/schema` - Delete schema

### Functions
- `GET /api/v1/functions` - List all functions
- `POST /api/v1/functions` - Create function
- `GET /api/v1/functions/{name}` - Get function details
- `PUT /api/v1/functions/{name}` - Update function
- `DELETE /api/v1/functions/{name}` - Delete function
- `POST /api/v1/functions/{name}/run` - Run function
- `POST /api/v1/functions/{name}/test` - Test function

### Other
- `GET /api/v1/resources` - List all resource files
- `DELETE /api/v1/resources/{resource}` - Delete resource file
- `GET /api/v1/logs` - Get API logs (paginated, filterable)
- `DELETE /api/v1/logs` - Clear all logs
- `GET /api/v1/banners` - List banner images

## Important Conventions

### 1. File Storage
- **Resources**: Store in `data/{resource}.json`
- **Schemas**: Store in `data/schema/{resource}.json`
- **Functions**: Store in `data/functions/{name}.json` (ONE FILE PER FUNCTION)
- **DO NOT use**: `data/functions.json` (legacy, should not exist)

### 2. Route Order
**IMPORTANT**: More specific routes MUST come before generic routes!
- `/api/v1/functions/{name}` must be before `/api/v1/r/{resource}/{item_id}`
- `/api/v1/r/{resource}` must be after specific routes like `/api/v1/r/{resource}/bulk/update`

### 3. Middleware
- Logging middleware captures request/response bodies
- Does NOT log calls to `/api/v1/logs` endpoint
- Does NOT log WebSocket connections (`/ws/*`)
- Uses ASGI middleware for proper body capture

### 4. Database
- SQLite database at `data/api_logs.db`
- Created automatically on startup via `init_db()`
- Logs all API calls with:
  - timestamp, method, endpoint
  - params, headers, query_params
  - request_body, response_body
  - status_code, duration_ms

### 5. Function Execution
- Python code execution with sandboxed `exec()`
- Available modules: `json`, `re`, `datetime`, `math`, `random`, `collections`, `itertools`, `functools`, `uuid`, `hashlib`, `base64`, `time`, `calendar`, `copy`, `string`, `textwrap`
- `print()` outputs are captured as logs
- Return value via `result` variable

## Common Issues & Solutions

### 1. File Permissions
**Problem**: `PermissionError: [Errno 13] Permission denied`
**Solution**: Files created by Docker (root) need to be deleted and recreated:
```bash
cd data
rm -f *.json functions.json
# Restart server - it will recreate files with proper permissions
```

### 2. Module Not Found
**Problem**: `ModuleNotFoundError: No module named 'aiosqlite'`
**Solution**: Install with uv:
```bash
uv pip install aiosqlite fastapi uvicorn jsonschema
```

### 3. Database Locked
**Problem**: `database is locked`
**Solution**: Ensure only one server instance is running:
```bash
pkill -f uvicorn
# Then restart
```

### 4. Route Conflicts
**Problem**: 404 errors for specific routes like `/api/v1/functions/{name}`
**Solution**: Check route order - specific routes must come before generic `{resource}` routes

## Testing

### Manual Testing
```bash
# Start server
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# In another terminal, test APIs:
curl http://localhost:8000/api/v1/resources
curl http://localhost:8000/api/v1/r/users
curl -X POST http://localhost:8000/api/v1/r/users -H "Content-Type: application/json" -d '{"id":"1","name":"Test"}'
curl http://localhost:8000/api/v1/logs
```

### Check Database
```bash
python3 -c "
import asyncio
import aiosqlite

async def check():
    async with aiosqlite.connect('data/api_logs.db') as db:
        cursor = await db.execute('SELECT COUNT(*) FROM api_logs')
        count = await cursor.fetchone()
        print(f'Logs: {count[0]}')

asyncio.run(check())
"
```

## UI Pages
- `/` - Main IDE (index.html)
- `/functions` - Functions editor (functions.html)
- `/logs` - API logs viewer (logs.html)
- `/docs/{resource}` - Swagger docs for resource

## Code Style
- Use async/await for database operations
- Use `aiosqlite` for async SQLite
- Catch specific exceptions (not bare `except:`)
- Add proper error handling in middleware
- Keep routes organized by category
- Use proper type hints
- No comments unless explicitly requested
