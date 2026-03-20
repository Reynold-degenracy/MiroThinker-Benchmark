# MiroFlow Agent API

This API exposes:

- a streaming execution endpoint
- a session-scoped file upload endpoint
- automatic sandbox management bound to `X-Session-Id`

The server now uses a two-level identity model internally:

- **API session ID**: the stable client-supplied `X-Session-Id`
- **Run ID**: a server-generated unique identifier for each `/v1/api/execute` call

`X-Session-Id` is used for session/sandbox continuity.  
The internal run ID is used for logs and traces.

## Running the Server

### Basic Usage

```bash
# From the miroflow-agent directory
python3 api_server.py
```

The server will start on `http://localhost:8000` by default.

### With Hydra Configuration Overrides

You can override Hydra configuration settings when starting the server using command-line arguments:

```bash
# Override LLM provider and settings
python3 api_server.py llm=qwen-3 llm.base_url=http://localhost:61002/v1

# Override multiple settings
python3 api_server.py llm=qwen-3 llm.api_key=xxxxx llm.base_url=http://localhost:61002/v1 llm.temperature=0.7

# Use Claude with custom API key
python3 api_server.py llm=claude-3-7 llm.api_key=your-api-key-here

# Use GPT-5
python3 api_server.py llm=gpt-5 llm.api_key=your-openai-key
```

These CLI overrides set the **default configuration** for the server. All API requests will use these settings unless overridden per-request using the `config_overrides` parameter (see [Configuration Overrides](#configuration-overrides) section).

## API Endpoints

### POST /v1/api/execute

Submit one execution request and receive streamed NDJSON output.

#### Headers

- `Content-Type: application/json`
- `X-Session-Id`: Session identifier (e.g., `sess_001`)

#### Request Body

```json
{
  "message": [
    {"type": "query", "step": 1, "content": "user_files 文件夹里的文件主题是什么"}
  ],
  "config_overrides": {
    "llm": "qwen-3",
    "llm.base_url": "http://localhost:8000/v1"
  }
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| message | array | Yes | Message list. At least one item is required; the primary query should use `{"type":"query","step":1,"content":"..."}` |
| config_overrides | object | No | Hydra configuration overrides (e.g., LLM provider, model, base_url). See [Configuration Overrides](#configuration-overrides) section |

#### Response Format

The response is streamed in NDJSON (Newline Delimited JSON) format:

```json
{"type":"start","step":1,"delta":""}
{"type":"answer","step":1,"delta":"I am"}
{"type":"answer","step":1,"delta":" Manus"}
{"type":"answer","step":1,"delta":"\n"}
{"type":"end","step":1,"delta":""}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| type | `"start"` / `"answer"` / `"end"` | Stream event type |
| step | int | Step number. The current API keeps this at `1` |
| delta | string | Incremental text payload |

## Example Usage

### Using curl

```bash
curl http://localhost:7210/v1/api/execute \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_001" \
  -d '{
    "message": [
      {"type": "query", "step": 1, "content": "user_files 文件夹里的文件主题是什么"}
    ],
    "config_overrides": {
      "llm": "qwen-3"
    }
  }'
```

### Using Python

```python
import requests
import json

url = "http://localhost:7210/v1/api/execute"
headers = {
    "Content-Type": "application/json",
    "X-Session-Id": "sess_001"
}
data = {
    "message": [
        {"type": "query", "step": 1, "content": "user_files 文件夹里的文件主题是什么"}
    ],
}

response = requests.post(url, headers=headers, json=data, stream=True)

for line in response.iter_lines():
    if line:
        event = json.loads(line)
        print(f"[{event['type']}] {event.get('step', '')}: {event['delta']}")
```

### POST /v1/api/upload

Upload a file to the session sandbox under `/home/user/uploaded/`.

### Headers

- `Content-Type: multipart/form-data`
- `X-Session-Id`: Session identifier (e.g., `sess_001`)

### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| file | file | Yes | The file to upload from the client |

### Response Format

```json
{"data":{"path":"/home/user/uploaded/example.txt"}}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| data.path | string | The file path in the sandbox |

### Example Usage

#### Using curl

```bash
curl http://localhost:7210/v1/api/upload \
  -H "X-Session-Id: sess_001" \
  -F "file=@/path/to/example.txt"
```

#### Using Python

```python
import requests

url = "http://localhost:7210/v1/api/upload"
headers = {
    "X-Session-Id": "sess_001"
}

# Upload a file
with open("/path/to/example.txt", "rb") as f:
    files = {"file": f}
    response = requests.post(url, headers=headers, files=files)
    print(response.json())
    # Output: {"data": {"path": "/home/user/uploaded/example.txt"}}
```

## Identity Model

- **`X-Session-Id` / API session ID**  
  Stable across multiple requests from the same client conversation/thread.

- **Sandbox ID**  
  Internal execution environment identifier. Managed automatically and reused per API session when available.

- **Run ID**  
  Internal identifier generated for each `/v1/api/execute` request. Used for logs and traces only. Clients do not supply it.

## Sandbox Notes

- **Each `X-Session-Id` corresponds to a persistent sandbox**: Sandboxes are automatically created and reused across multiple requests with the same session ID
- **Each `/v1/api/execute` call also gets its own internal run ID**: This is separate from `X-Session-Id` and is used for tracing/debugging
- **Automatic sandbox management**: Python tools (like `run_command`, `run_python_code`) automatically use the session's sandbox without requiring explicit `sandbox_id` parameters
- **Sandbox lifecycle**: 3600 seconds (1 hour) default TTL. If a sandbox expires or becomes unavailable, a new one is automatically created on the next Python tool call
- **Session isolation**: Each session has its own sandbox, ensuring isolation between different users or conversation threads
- **No manual management needed**: The `SessionAwareSandboxManager` handles all sandbox creation, reuse, and cleanup automatically

## Health Check

```bash
curl http://localhost:7210/health
```

Returns: `{"status": "healthy"}`

## Configuration Overrides

You can override Hydra configuration settings on a per-request basis using the `config_overrides` parameter. This allows you to dynamically change LLM providers, models, API endpoints, and other settings without modifying configuration files.

### Common Override Examples

**Use a different LLM provider:**
```json
{
  "message": [
    {"type": "query", "step": 1, "content": "What is 2+2?"}
  ],
  "config_overrides": {
    "llm": "qwen-3",
    "llm.base_url": "http://localhost:61002/v1"
  }
}
```

**Override specific LLM parameters:**
```json
{
  "message": [
    {"type": "query", "step": 1, "content": "What is 2+2?"}
  ],
  "config_overrides": {
    "llm.temperature": "0.7",
    "llm.max_tokens": "8192",
    "llm.model_name": "custom-model"
  }
}
```

**Use Claude with custom API key:**
```json
{
  "message": [
    {"type": "query", "step": 1, "content": "What is 2+2?"}
  ],
  "config_overrides": {
    "llm": "claude-3-7",
    "llm.api_key": "your-api-key-here"
  }
}
```

**Complete example with curl:**
```bash
curl http://localhost:7210/v1/api/execute \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_custom_llm" \
  -d '{
    "message": [
      {"type": "query", "step": 1, "content": "What is the capital of France?"}
    ],
    "config_overrides": {
      "llm": "qwen-3",
      "llm.base_url": "http://localhost:61002/v1",
      "llm.temperature": "0.5"
    }
  }'
```

### Available Configuration Keys

The most commonly used configuration keys for overrides:

**LLM Configuration:**
- `llm`: LLM config name (e.g., "qwen-3", "claude-3-7", "gpt-5")
- `llm.provider`: Provider name ("qwen", "anthropic", "openai")
- `llm.model_name`: Model name string
- `llm.base_url`: API base URL
- `llm.api_key`: API key
- `llm.temperature`: Temperature (0.0-1.0)
- `llm.max_tokens`: Maximum tokens to generate
- `llm.top_p`: Top-p sampling parameter
- `llm.top_k`: Top-k sampling parameter

**Agent Configuration:**
- `agent`: Agent config name (e.g., "single_agent_keep5", "mirothinker_v1.5_keep5_max200")
- `agent.main_agent.max_turns`: Maximum agent turns

See the `conf/` directory for all available configuration options.

## Session Management

Each session is identified by the `X-Session-Id` header. The server maintains separate pipeline components and sandbox state for each session, allowing multiple concurrent users.

**Important:** `config_overrides` are only applied when a session is first created. If you reuse an existing `X-Session-Id`, later overrides for that same session are ignored so the session continues to use one stable sandbox/conversation context.

## Configuration

The server uses the default Hydra configuration from the `conf/` directory. You can modify the configuration by editing the config files or passing environment variables.

## Notes

- The server requires all dependencies from `pyproject.toml` to be installed
- Environment variables should be configured in `.env` file
- `X-Session-Id` is the stable client-visible session key
- Each `/v1/api/execute` request also creates an internal run ID for logging/tracing
