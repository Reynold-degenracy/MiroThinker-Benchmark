import pytest

from api_server import SessionAwareSandboxManager


class FakeToolManager:
    def __init__(self):
        self.calls = []
        self.create_calls = 0
        self.run_calls = 0

    async def execute_tool_call(self, server_name, tool_name, arguments):
        self.calls.append((server_name, tool_name, arguments))

        if server_name == "tool-python" and tool_name == "create_sandbox":
            self.create_calls += 1
            return {"result": f"sandbox_id: sbx-{self.create_calls}"}

        if server_name == "tool-python" and tool_name == "run_python_code":
            self.run_calls += 1
            if self.run_calls == 1:
                return {
                    "result": (
                        "[ERROR]: Failed to connect to sandbox AUTO. "
                        "Make sure the sandbox is created and the sandbox_id is correct."
                    )
                }
            return {"result": "ok"}

        return {"result": "ok"}


def test_needs_sandbox_id_treats_auto_case_insensitively():
    manager = SessionAwareSandboxManager(FakeToolManager(), {"sandbox_id": None})

    assert manager._needs_sandbox_id(
        "run_python_code",
        {"sandbox_id": "AUTO", "code_block": "print(1)"},
    )


@pytest.mark.asyncio
async def test_recreate_and_retry_even_when_request_has_existing_sandbox_id():
    tool_manager = FakeToolManager()
    session = {"sandbox_id": "existing-sandbox"}
    manager = SessionAwareSandboxManager(tool_manager, session)

    result = await manager.execute_tool_call(
        server_name="tool-python",
        tool_name="run_python_code",
        arguments={"code_block": "print(1)", "sandbox_id": "existing-sandbox"},
    )

    run_calls = [c for c in tool_manager.calls if c[1] == "run_python_code"]
    create_calls = [c for c in tool_manager.calls if c[1] == "create_sandbox"]

    assert result["result"] == "ok"
    assert len(run_calls) == 2
    assert len(create_calls) == 1
    assert run_calls[0][2]["sandbox_id"] == "existing-sandbox"
    assert run_calls[1][2]["sandbox_id"] == "sbx-1"
    assert session["sandbox_id"] == "sbx-1"


@pytest.mark.asyncio
async def test_blocks_disallowed_tool_python_capabilities():
    tool_manager = FakeToolManager()
    manager = SessionAwareSandboxManager(tool_manager, {"sandbox_id": None})

    result = await manager.execute_tool_call(
        server_name="tool-python",
        tool_name="download_file_from_internet_to_sandbox",
        arguments={"url": "https://example.com/test.txt"},
    )

    assert "error" in result
    assert "disabled" in result["error"]
    assert tool_manager.calls == []
