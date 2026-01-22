# MiroFlow Agent API v1

This document describes the FastAPI v1 endpoints for the MiroFlow Agent.

## Starting the Server

Start the server using uvicorn:

```bash
uv run uvicorn api_server_v1:app --host 0.0.0.0 --port 8000
```

Or run directly with Hydra configuration:

```bash
python api_server_v1.py
```

## Health Check

Check server status:

```bash
curl http://localhost:8000/health
```

Returns:
```json
{"status": "healthy"}
```

## API Endpoints

### Message Format

#### Non-streaming Message Format
```json
{"type": "xxx", "step": -1, "content": "xxx"}
```

#### Streaming Message Format
```json
{"type": "xxx", "step": -1, "delta": "xxx"}
```

#### Message Parameters

| Parameter | Type   | Required | Description |
|-----------|--------|----------|-------------|
| type      | string | Yes      | Message type: `query`, `plan`, `answer`, `action`, `start`, `end` |
| step      | int    | No       | Current step number |
| content   | str    | No       | Message text content (non-streaming) |
| delta     | str    | No       | Message text content (streaming) |

### POST /v1/api/plan

**Purpose**: Submit a question and generate a step-by-step plan.

#### Request Headers
- `Content-Type: application/json`
- `X-Session-Id: asdfghjkl`
- `Authorization: Bearer xxx` (optional)

#### Request Body
```json
{
  "query": "xxx",
  "history": [
    {"type": "query", "step": -1, "content": "xxx"},
    {"type": "plan", "step": 1, "content": "xxx"},
    {"type": "plan", "step": 2, "content": "xxx"},
    {"type": "plan", "step": 3, "content": "xxx"}
  ]
}
```

#### Request Parameters

| Parameter | Type   | Required | Description |
|-----------|--------|----------|-------------|
| query     | string | Yes      | Current user question or modification request |
| history   | array  | No       | All historical messages |

#### Response Format (Streaming NDJSON)
```json
{"type": "start", "step": 1, "delta": ""}
{"type": "plan", "step": 1, "delta": "xx"}
{"type": "plan", "step": 1, "delta": "xx"}
{"type": "plan", "step": 1, "delta": "xx"}
{"type": "end", "step": 1, "delta": ""}
{"type": "start", "step": 2, "delta": ""}
{"type": "plan", "step": 2, "delta": "x"}
{"type": "plan", "step": 2, "delta": "xx"}
{"type": "plan", "step": 2, "delta": "xxx"}
{"type": "end", "step": 2, "delta": ""}
```

#### Example Usage

```bash
curl -X POST http://localhost:8000/v1/api/plan \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_001" \
  -H "Authorization: Bearer your_token" \
  -d '{
    "query": "What is the weather today?",
    "history": []
  }'
```

### POST /v1/api/execute

**Purpose**: Execute a plan and generate results.

#### Request Headers
- `Content-Type: application/json`
- `X-Session-Id: asdfghjkl` (optional)
- `Authorization: Bearer xxx` (optional)

#### Request Body
```json
{
  "plan": [
    {"type": "plan", "step": 1, "content": "xxx"},
    {"type": "plan", "step": 2, "content": "xxx"},
    {"type": "plan", "step": 3, "content": "xxx"}
  ]
}
```

#### Request Parameters

| Parameter | Type  | Required | Description |
|-----------|-------|----------|-------------|
| plan      | array | Yes      | Latest plan array |

#### Response Format (Streaming NDJSON)
```json
{"type": "start", "step": 1, "delta": ""}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "end", "step": 1, "delta": ""}
{"type": "start", "step": 2, "delta": ""}
{"type": "answer", "step": 2, "delta": "x"}
{"type": "answer", "step": 2, "delta": "xx"}
{"type": "answer", "step": 2, "delta": "xxx"}
{"type": "end", "step": 2, "delta": ""}
```

#### Example Usage

```bash
curl -X POST http://localhost:8000/v1/api/execute \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_001" \
  -d '{
    "plan": [
      {"type": "plan", "step": 1, "content": "Search for weather information"},
      {"type": "plan", "step": 2, "content": "Format the results"}
    ]
  }'
```

### POST /v1/api/upload

**Purpose**: Upload files to the workspace.

#### Request Headers
- `Content-Type: multipart/form-data`
- `X-Session-Id: asdfghjkl`
- `Authorization: Bearer xxx` (optional)

#### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| file      | file | Yes      | The file to upload |

#### Response Format
```json
{
  "status": "success",
  "message": "File uploaded successfully"
}
```

#### Example Usage

```bash
curl -X POST http://localhost:8000/v1/api/upload \
  -H "X-Session-Id: sess_001" \
  -H "Authorization: Bearer your_token" \
  -F "file=@/path/to/example.txt"
```

## Sandbox Management

- **Each `X-Session-Id` corresponds to a sandbox**: Sandboxes are automatically created and reused for requests with the same session ID
- **Sandbox lifecycle**: 3600 seconds (1 hour) default TTL. If a sandbox expires, a new one is automatically created
- **Session isolation**: Each session has its own isolated sandbox environment
- **Automatic management**: The `SessionAwareSandboxManager` handles all sandbox creation, reuse, and cleanup

## Conversation State

- **Server is stateless**: The server does not save conversation history
- **Client maintains history**: Clients must maintain their own history and pass it via the `history` parameter in requests

## Python Usage Example

### Using the /v1/api/plan endpoint

```python
import requests
import json

url = "http://localhost:8000/v1/api/plan"
headers = {
    "Content-Type": "application/json",
    "X-Session-Id": "sess_001",
    "Authorization": "Bearer your_token"
}
data = {
    "query": "What is the weather today?",
    "history": []
}

response = requests.post(url, headers=headers, json=data, stream=True)

for line in response.iter_lines():
    if line:
        event = json.loads(line)
        print(f"[{event['type']}] Step {event.get('step', 'N/A')}: {event.get('delta', '')}")
```

### Using the /v1/api/execute endpoint

```python
import requests
import json

url = "http://localhost:8000/v1/api/execute"
headers = {
    "Content-Type": "application/json",
    "X-Session-Id": "sess_001"
}
data = {
    "plan": [
        {"type": "plan", "step": 1, "content": "Search for information"},
        {"type": "plan", "step": 2, "content": "Format the results"}
    ]
}

response = requests.post(url, headers=headers, json=data, stream=True)

for line in response.iter_lines():
    if line:
        event = json.loads(line)
        print(f"[{event['type']}] Step {event.get('step', 'N/A')}: {event.get('delta', '')}")
```

### Using the /v1/api/upload endpoint

```python
import requests

url = "http://localhost:8000/v1/api/upload"
headers = {
    "X-Session-Id": "sess_001",
    "Authorization": "Bearer your_token"
}

with open("/path/to/example.txt", "rb") as f:
    files = {"file": f}
    response = requests.post(url, headers=headers, files=files)
    print(response.json())
    # Output: {"status": "success", "message": "File uploaded successfully"}
```

## Configuration

The server uses Hydra configuration from the `conf/` directory. You can override configuration settings when starting the server:

```bash
# Override LLM provider and settings
python api_server_v1.py llm=qwen-3 llm.base_url=http://localhost:61002/v1

# Override multiple settings
python api_server_v1.py llm=qwen-3 llm.api_key=xxxxx llm.base_url=http://localhost:61002/v1 llm.temperature=0.7
```

## Notes

- All endpoints support CORS for cross-origin requests
- The server requires all dependencies from `pyproject.toml` to be installed
- Environment variables should be configured in the `.env` file
- Sessions are automatically managed and isolated per `X-Session-Id`
