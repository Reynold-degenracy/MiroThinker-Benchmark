# Session-to-Sandbox Mapping Feature

## Overview

The MiroFlow Agent API Server now maintains a persistent mapping between session IDs and sandbox IDs. This allows users to upload files to a sandbox once and then access those files in subsequent API calls without having to manually specify the sandbox_id each time.

## Architecture

### Key Components

1. **ToolManager (`libs/miroflow-tools/src/miroflow_tools/manager.py`)**
   - Added `default_sandbox_id` attribute to store the session's default sandbox
   - Added `set_default_sandbox_id()` method to configure the default sandbox
   - Modified `execute_tool_call()` to automatically inject `sandbox_id` for tools that require it

2. **API Server (`apps/miroflow-agent/api_server.py`)**
   - Extended session dictionary to include `sandbox_id` field
   - Modified `stream_generator()` to set default sandbox_id in tool managers
   - Added `GET /get_sandbox_id` endpoint to query a session's sandbox

3. **Sandbox Tools**
   - The following tools automatically receive the session's sandbox_id when not explicitly provided:
     - `run_command`
     - `run_python_code`
     - `upload_file_from_local_to_sandbox`
     - `download_file_from_internet_to_sandbox`
     - `download_file_from_sandbox_to_local`

## How It Works

### 1. File Upload Flow

When a user uploads a file via `POST /upload_file`:

```
1. User uploads file with X-Session-Id header
2. If session doesn't have a sandbox:
   - Create new sandbox via create_sandbox tool
   - Store sandbox_id in session: session["sandbox_id"] = created_id
3. Upload file to the sandbox
4. Return file path and sandbox_id to user
```

### 2. Subsequent API Calls

When a user makes requests via `POST /get_response`:

```
1. User sends query with X-Session-Id header
2. API retrieves session and its stored sandbox_id
3. Tool managers are configured with default sandbox_id
4. When LLM calls sandbox tools without sandbox_id:
   - ToolManager automatically injects session's sandbox_id
   - Tool executes in the correct sandbox
5. User can access files uploaded earlier without manual sandbox_id
```

### 3. Sandbox Recovery

If a sandbox becomes unavailable (expired or crashed):

```
1. Tool call fails with "sandbox does not exist" error
2. API detects the error pattern
3. Automatically creates a new sandbox
4. Updates session with new sandbox_id
5. Retries the operation
```

## API Reference

### POST /upload_file

Upload a file to the session's sandbox.

**Headers:**
- `X-Session-Id`: Unique session identifier

**Request:**
- Multipart form-data with file attachment

**Response:**
```json
{
  "data": {
    "path": "/home/user/example.txt",
    "sandbox_id": "abc123xyz"
  }
}
```

### GET /get_sandbox_id

Query the sandbox_id associated with a session.

**Headers:**
- `X-Session-Id`: Unique session identifier

**Response:**
```json
{
  "sandbox_id": "abc123xyz"
}
```

Or if no sandbox exists yet:
```json
{
  "sandbox_id": null
}
```

### POST /get_response

Execute a task with automatic sandbox_id injection.

**Headers:**
- `X-Session-Id`: Unique session identifier

**Request:**
```json
{
  "query": "Read the file I uploaded earlier and summarize it",
  "history": [],
  "is_confirmed": true,
  "config_overrides": {}
}
```

**Response:**
NDJSON stream with plan and answer events

## Usage Examples

### Example 1: Upload and Process File

```python
import requests

session_id = "user-session-12345"
headers = {"X-Session-Id": session_id}

# Step 1: Upload a file
with open("data.csv", "rb") as f:
    files = {"file": f}
    response = requests.post(
        "http://localhost:8000/upload_file",
        headers=headers,
        files=files
    )
    print(response.json())
    # {"data": {"path": "/home/user/data.csv", "sandbox_id": "xyz789"}}

# Step 2: Process the file (sandbox_id is auto-injected!)
response = requests.post(
    "http://localhost:8000/get_response",
    headers=headers,
    json={
        "query": "Read data.csv and calculate the average of column A",
        "history": [],
        "is_confirmed": True
    },
    stream=True
)

for line in response.iter_lines():
    if line:
        print(line.decode())
```

### Example 2: Check Session's Sandbox

```python
import requests

session_id = "user-session-12345"
headers = {"X-Session-Id": session_id}

# Query current sandbox_id
response = requests.get(
    "http://localhost:8000/get_sandbox_id",
    headers=headers
)
print(response.json())
# {"sandbox_id": "xyz789"}
```

## Implementation Details

### ToolManager Auto-Injection

The `execute_tool_call` method in ToolManager checks if:
1. The tool being called is in the `SANDBOX_TOOLS` set
2. The `sandbox_id` argument is not already provided
3. A `default_sandbox_id` has been set

If all conditions are met, it automatically injects the sandbox_id:

```python
if (
    tool_name in SANDBOX_TOOLS
    and "sandbox_id" not in arguments
    and self.default_sandbox_id is not None
):
    arguments = dict(arguments)  # Create a copy
    arguments["sandbox_id"] = self.default_sandbox_id
```

### Session Lifecycle

- Sessions are stored in-memory in the `_sessions` dictionary
- Each session maintains:
  - `main_agent_tool_manager`: ToolManager for main agent
  - `sub_agent_tool_managers`: Dict of sub-agent ToolManagers
  - `output_formatter`: Output formatter instance
  - `cfg`: Hydra configuration
  - `sandbox_id`: Persistent sandbox identifier

- Sessions persist for the lifetime of the API server process
- Sandboxes have a default timeout of 3600 seconds (1 hour)

### Error Handling

The system handles several error scenarios:

1. **Invalid Sandbox ID**: Returns clear error message
2. **Sandbox Expired**: Automatically creates new sandbox and retries
3. **Upload Failures**: Detailed error messages with troubleshooting info
4. **Connection Errors**: Graceful degradation with retry logic

## Testing

Run the comprehensive test suite:

```bash
cd apps/miroflow-agent
python test_session_sandbox_mapping.py
```

This tests:
- ToolManager sandbox_id injection mechanism
- API server session structure
- Sandbox tools list completeness
- upload_file session tracking

## Benefits

1. **Improved User Experience**: Users don't need to track sandbox IDs manually
2. **Simplified API**: Cleaner API calls without repetitive sandbox_id parameters
3. **Session Persistence**: Files remain accessible throughout the session
4. **Automatic Recovery**: Handles sandbox expiration gracefully
5. **Backward Compatible**: Explicit sandbox_id parameters still work

## Limitations

1. **In-Memory Storage**: Sessions are lost on server restart
2. **Single Sandbox Per Session**: Each session has one active sandbox
3. **No Cross-Session Access**: Sandboxes are isolated per session
4. **TTL Constraints**: Sandboxes expire after 1 hour of inactivity

## Future Enhancements

Potential improvements for future versions:

1. **Persistent Storage**: Store session-sandbox mappings in Redis/database
2. **Multi-Sandbox Support**: Allow multiple sandboxes per session with namespaces
3. **Sandbox Pooling**: Reuse sandboxes across sessions for efficiency
4. **Extended TTL Management**: Dynamic TTL adjustment based on usage patterns
5. **Cross-Session Sharing**: Optional shared sandboxes for collaborative workflows
