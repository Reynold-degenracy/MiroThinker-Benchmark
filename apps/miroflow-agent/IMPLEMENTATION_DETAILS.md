# Session-to-Sandbox Mapping Implementation

## Problem Statement (Chinese)
添加如下机制：
服务端维护一个session-id到sandbox-id，使得同一个用户在后续访问的时候不需要主动指定sandbox-id就可以访问其中的文件

Translation: "Add the following mechanism: The server maintains a mapping from session-id to sandbox-id, so that the same user in subsequent visits does not need to actively specify sandbox-id to access files within it."

## Solution Overview

Successfully implemented a session-to-sandbox mapping mechanism that allows users to:
1. Upload files to a sandbox once (sandbox is automatically created)
2. Access those files in subsequent API calls without manually specifying sandbox_id
3. Have the system automatically inject the correct sandbox_id for all sandbox-related operations

## Changes Made

### 1. ToolManager (`libs/miroflow-tools/src/miroflow_tools/manager.py`)

**Added:**
- `default_sandbox_id` attribute to store the session's default sandbox
- `set_default_sandbox_id(sandbox_id)` method to configure the default sandbox for a session
- Auto-injection logic in `execute_tool_call()` for the following sandbox tools:
  - `run_command`
  - `run_python_code`
  - `upload_file_from_local_to_sandbox`
  - `download_file_from_internet_to_sandbox`
  - `download_file_from_sandbox_to_local`

**How it works:**
When a tool that requires sandbox_id is called without one, and a default_sandbox_id has been set, the ToolManager automatically injects it into the tool call arguments.

### 2. API Server (`apps/miroflow-agent/api_server.py`)

**Added:**
- `sandbox_id` field in session dictionary to persist sandbox across requests
- Logic in `stream_generator()` to set default sandbox_id in all tool managers when a session has one
- `GET /get_sandbox_id` endpoint to query a session's associated sandbox_id

**Enhanced:**
- `/upload_file` now stores the created sandbox_id in the session
- `/get_response` automatically configures tool managers with the session's sandbox_id

### 3. Documentation

**Created:**
- `apps/miroflow-agent/docs/SESSION_SANDBOX_MAPPING.md` - Comprehensive guide covering:
  - Architecture and key components
  - How the mechanism works
  - API reference
  - Usage examples
  - Implementation details
  - Testing instructions
  - Benefits and limitations

**Updated:**
- `apps/miroflow-agent/API_README.md` - Added documentation for:
  - `GET /get_sandbox_id` endpoint
  - Updated `/upload_file` response format to include sandbox_id
  - Enhanced sandbox notes with session-to-sandbox mapping information

### 4. Testing

**Created:**
- `apps/miroflow-agent/test_session_sandbox_mapping.py` - Comprehensive test suite validating:
  - ToolManager sandbox_id injection mechanism
  - API server session-to-sandbox structure
  - All sandbox tools are properly listed
  - upload_file session tracking functionality

## Usage Example

```python
import requests

session_id = "user-session-12345"
headers = {"X-Session-Id": session_id}

# Step 1: Upload a file (sandbox is auto-created)
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

# The LLM can now use sandbox tools like run_python_code() without
# specifying sandbox_id - it's automatically injected!
```

## Benefits

1. **Improved User Experience**: Users no longer need to track and manage sandbox IDs
2. **Simplified API**: Cleaner API calls without repetitive sandbox_id parameters
3. **Session Persistence**: Files remain accessible throughout the entire session
4. **Automatic Recovery**: Gracefully handles sandbox expiration by creating new sandboxes
5. **Backward Compatible**: Explicit sandbox_id parameters still work if provided

## Testing Results

All tests pass successfully:

```
✓ API Structure Test - 8/8 checks passed
✓ Session-to-Sandbox Mapping Test - All 4 test suites passed
  • ToolManager supports default_sandbox_id
  • API server maintains session-to-sandbox mapping
  • Automatic sandbox_id injection for sandbox tools
  • upload_file endpoint tracks sandbox_id in session
  • GET /get_sandbox_id endpoint available
```

## Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| `apps/miroflow-agent/api_server.py` | +35 | Session-sandbox mapping in API |
| `libs/miroflow-tools/src/miroflow_tools/manager.py` | +33 | Auto-injection in ToolManager |
| `apps/miroflow-agent/API_README.md` | +58, -4 | Updated API documentation |
| `apps/miroflow-agent/docs/SESSION_SANDBOX_MAPPING.md` | +269 (new) | Comprehensive feature guide |
| `apps/miroflow-agent/test_session_sandbox_mapping.py` | +178 (new) | Test suite |

**Total: 5 files changed, 573 insertions(+), 4 deletions(-)**

## Security Considerations

- Sandbox_id validation remains in place (invalid IDs are rejected)
- Path traversal protection in file uploads
- Session isolation (each session has its own sandbox)
- Automatic sandbox recreation if expired/unavailable

## Limitations & Future Work

Current limitations:
- In-memory session storage (lost on server restart)
- One sandbox per session
- Sandboxes expire after 1 hour by default

Potential improvements:
- Persistent session storage (Redis/database)
- Multi-sandbox support per session
- Sandbox pooling for efficiency
- Dynamic TTL management

## Conclusion

The implementation successfully addresses the requirement by maintaining a server-side session-to-sandbox mapping, eliminating the need for users to manually specify sandbox_id in subsequent API calls. The solution is well-tested, documented, and backward compatible.
