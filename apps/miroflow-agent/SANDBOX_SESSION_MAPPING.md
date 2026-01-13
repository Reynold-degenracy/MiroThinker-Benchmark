# Sandbox-to-Session Mapping

## Overview

This feature implements automatic sandbox lifecycle management per session, ensuring that all Python tool calls within the same session use the same E2B sandbox without requiring explicit `sandbox_id` management.

## Problem Statement

Previously, when using Python tools (like `run_command`, `run_python_code`, etc.), the agent or user had to:
1. Explicitly call `create_sandbox` to get a `sandbox_id`
2. Remember and pass the `sandbox_id` to every subsequent Python tool call
3. Handle sandbox expiry and recreation manually

This added complexity and potential for errors, especially in multi-turn conversations.

## Solution

The `SessionAwareSandboxManager` class automatically:
1. **Creates a sandbox on first use**: When any Python tool is called for the first time in a session, a sandbox is automatically created
2. **Persists sandbox_id per session**: The sandbox ID is stored in the session dict and reused for all subsequent tool calls
3. **Auto-injects sandbox_id**: Python tool calls automatically receive the session's sandbox_id without explicit specification
4. **Handles sandbox expiry**: If a sandbox becomes unavailable, it's automatically recreated on the next tool call

## Implementation Details

### SessionAwareSandboxManager Class

The `SessionAwareSandboxManager` wraps the standard `ToolManager` and intercepts tool calls to Python tools:

```python
class SessionAwareSandboxManager:
    def __init__(self, tool_manager, session_dict: Dict):
        self.tool_manager = tool_manager
        self.session_dict = session_dict
        self._sandbox_creation_lock = asyncio.Lock()
    
    async def execute_tool_call(self, server_name: str, tool_name: str, arguments: dict):
        # If this is a Python tool that needs a sandbox_id, inject it
        if server_name == "tool-python" and self._needs_sandbox_id(tool_name, arguments):
            sandbox_id = await self._ensure_sandbox_exists()
            arguments = {**arguments, "sandbox_id": sandbox_id}
        
        return await self.tool_manager.execute_tool_call(
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments
        )
```

### Tools with Auto-Injection

The following Python tools automatically receive `sandbox_id`:
- `run_command`
- `run_python_code`
- `run_python_code_stream`
- `upload_file_from_local_to_sandbox`
- `download_file_from_sandbox_to_local`
- `download_file_from_internet_to_sandbox`

### Session Structure

Each session now includes:
```python
session = {
    "sandbox_id": None,  # Created on first use
    "main_agent_tool_manager": SessionAwareSandboxManager(...),
    "sub_agent_tool_managers": {...},
    "cfg": ...,
    "output_formatter": ...
}
```

## Benefits

1. **Simplified API**: Users don't need to manage sandbox IDs explicitly
2. **Automatic persistence**: Sandboxes are reused across multiple requests in the same session
3. **Fault tolerance**: Automatic recreation of expired or unavailable sandboxes
4. **Consistent behavior**: Both `/get_response` and `/upload_file` endpoints use the same sandbox management logic

## Usage Example

### Before (Manual Management)
```python
# User/agent had to:
1. Call create_sandbox() -> get sandbox_id
2. Pass sandbox_id to every tool:
   - run_command(sandbox_id="abc123", command="ls")
   - run_python_code(sandbox_id="abc123", code="print('hello')")
3. Handle sandbox expiry manually
```

### After (Automatic Management)
```python
# User/agent just calls tools directly:
- run_command(command="ls")  # sandbox_id auto-injected
- run_python_code(code="print('hello')")  # same sandbox_id auto-injected
# No manual sandbox management needed!
```

## Testing

Run the test suite to verify the implementation:

```bash
cd apps/miroflow-agent
python test_sandbox_session_mapping.py
```

The tests verify:
- SessionAwareSandboxManager is properly defined
- Sessions maintain sandbox_id mapping
- Sandbox IDs are automatically injected
- Upload endpoint uses the simplified logic

## Implementation Files

- **Modified**: `apps/miroflow-agent/api_server.py`
  - Added `SessionAwareSandboxManager` class
  - Updated session creation to wrap tool managers
  - Simplified `/upload_file` endpoint

- **Added**: `apps/miroflow-agent/test_sandbox_session_mapping.py`
  - Comprehensive tests for the new functionality

## Notes

- Sandbox TTL is set to 3600 seconds (1 hour) by default
- Sandbox verification is performed before reuse to handle expiry
- Thread-safe sandbox creation using asyncio locks
- All attributes of the underlying ToolManager are accessible via delegation
