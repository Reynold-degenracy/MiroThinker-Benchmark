from pathlib import Path
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from src.llm.providers.agenthub_client import AgentHubClient


class DummyTaskLog:
    def __init__(self, log_dir: str) -> None:
        self.entries = []
        self.log_dir = log_dir
        self.current_sub_agent_session_id = None

    def log_step(self, info_level, step_name, message, metadata=None):
        self.entries.append(
            {
                "info_level": info_level,
                "step_name": step_name,
                "message": message,
                "metadata": metadata,
            }
        )


class AgentHubClientForTest(AgentHubClient):
    def _create_client(self):
        # Avoid initializing real SDK clients in unit tests.
        return None


class FakeStatefulClient:
    def __init__(self, events):
        self._events = events
        self.calls = []

    async def streaming_response_stateful(self, message, config):
        self.calls.append({"message": message, "config": config})
        for event in self._events:
            yield event


def build_cfg():
    return OmegaConf.create(
        {
            "llm": {
                "provider": "agenthub",
                "model_name": "Qwen/Qwen3-8B",
                "temperature": 0.0,
                "top_p": 1.0,
                "min_p": 0.0,
                "top_k": 0,
                "max_context_length": 32768,
                "max_tokens": 1024,
                "async_client": True,
                "keep_tool_result": -1,
                "api_key": "test-key",
                "base_url": "https://example.invalid",
            }
        }
    )


@pytest.fixture
def client_and_log(tmp_path: Path):
    task_log = DummyTaskLog(log_dir=str(tmp_path / "logs"))
    client = AgentHubClientForTest(
        task_id="task-1",
        cfg=build_cfg(),
        task_log=task_log,
    )
    return client, task_log


@pytest.mark.asyncio
async def test_counts_usage_when_usage_metadata_is_on_delta_event(client_and_log):
    client, _ = client_and_log
    stateful_client = FakeStatefulClient(
        [
            {
                "event_type": "delta",
                "content_items": [{"type": "text", "text": "hello"}],
                "usage_metadata": None,
                "finish_reason": None,
            },
            {
                "event_type": "delta",
                "content_items": [],
                "usage_metadata": {
                    "prompt_tokens": 11,
                    "response_tokens": 7,
                    "cached_tokens": 3,
                },
                "finish_reason": None,
            },
            {
                "event_type": "stop",
                "content_items": [],
                "usage_metadata": None,
                "finish_reason": "stop",
            },
        ]
    )

    response = await client._consume_uni_stream(
        stateful_client=stateful_client,
        uni_message={
            "role": "user",
            "content_items": [{"type": "text", "text": "What is 2+2?"}],
        },
        config={"trace_id": "task-1/main/turn_1_attempt_1"},
    )
    client._update_token_usage(response.usage)

    assert client.token_usage["total_input_tokens"] == 11
    assert client.token_usage["total_output_tokens"] == 7
    assert client.token_usage["total_cache_read_input_tokens"] == 3
    assert client.last_call_tokens == {"prompt_tokens": 11, "completion_tokens": 7}
    assert stateful_client.calls[0]["config"]["trace_id"] == "task-1/main/turn_1_attempt_1"


@pytest.mark.asyncio
async def test_counts_usage_when_usage_metadata_is_on_stop_event(client_and_log):
    client, _ = client_and_log
    stateful_client = FakeStatefulClient(
        [
            {
                "event_type": "delta",
                "content_items": [{"type": "text", "text": "hello"}],
                "usage_metadata": None,
                "finish_reason": None,
            },
            {
                "event_type": "stop",
                "content_items": [],
                "usage_metadata": {
                    "prompt_tokens": 20,
                    "response_tokens": 9,
                    "cached_tokens": 2,
                },
                "finish_reason": "stop",
            },
        ]
    )

    response = await client._consume_uni_stream(
        stateful_client=stateful_client,
        uni_message={
            "role": "user",
            "content_items": [{"type": "text", "text": "hello"}],
        },
        config={"trace_id": "task-1/main/turn_1_attempt_1"},
    )
    client._update_token_usage(response.usage)

    assert client.token_usage["total_input_tokens"] == 20
    assert client.token_usage["total_output_tokens"] == 9
    assert client.token_usage["total_cache_read_input_tokens"] == 2
    assert client.last_call_tokens == {"prompt_tokens": 20, "completion_tokens": 9}


@pytest.mark.asyncio
async def test_logs_warning_when_usage_metadata_is_missing(client_and_log):
    client, task_log = client_and_log
    stateful_client = FakeStatefulClient(
        [
            {
                "event_type": "delta",
                "content_items": [{"type": "text", "text": "hello"}],
                "usage_metadata": None,
                "finish_reason": None,
            },
            {
                "event_type": "stop",
                "content_items": [],
                "usage_metadata": None,
                "finish_reason": "stop",
            },
        ]
    )

    response = await client._consume_uni_stream(
        stateful_client=stateful_client,
        uni_message={
            "role": "user",
            "content_items": [{"type": "text", "text": "hello"}],
        },
        config={"trace_id": "task-1/main/turn_1_attempt_1"},
    )
    client._update_token_usage(response.usage)

    assert client.token_usage["total_input_tokens"] == 0
    assert client.token_usage["total_output_tokens"] == 0
    assert client.token_usage["total_cache_read_input_tokens"] == 0

    warning_messages = [
        entry["message"]
        for entry in task_log.entries
        if entry["info_level"] == "warning" and entry["step_name"] == "LLM | Token Usage"
    ]
    assert "No usage_data received." in warning_messages


@pytest.mark.asyncio
async def test_create_message_uses_stateful_trace_id_and_latest_user_only(client_and_log):
    client, task_log = client_and_log
    task_log.current_sub_agent_session_id = "agent-web-1"

    captured = {"messages": [], "trace_ids": []}

    async def fake_consume(stateful_client, uni_message, config, stream_queue=None):
        captured["messages"].append(uni_message)
        captured["trace_ids"].append(config["trace_id"])

        usage = SimpleNamespace(
            get=lambda key, default=None: {
                "prompt_tokens": 3,
                "response_tokens": 2,
                "cached_tokens": 1,
            }.get(key, default)
        )
        usage.__bool__ = lambda self=None: True
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                    index=0,
                )
            ],
            usage=usage,
            id="resp-1",
            model=None,
            object="chat.completion",
        )

    class DummyStateful:
        async def streaming_response_stateful(self, message, config):
            yield {}

    client._consume_uni_stream = fake_consume
    client._get_stateful_client = lambda agent_type: DummyStateful()

    message_history = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "assistant turn 1"},
        {"role": "user", "content": "latest user turn"},
    ]

    await client.create_message(
        system_prompt="system",
        message_history=message_history,
        tool_definitions=[],
        step_id=2,
        agent_type="agent-web",
    )
    await client.create_message(
        system_prompt="system",
        message_history=message_history,
        tool_definitions=[],
        step_id=2,
        agent_type="agent-web",
    )

    assert captured["messages"][0]["role"] == "user"
    assert (
        captured["messages"][0]["content_items"][0]["text"] == "latest user turn"
    )
    assert captured["trace_ids"][0] == "task-1/agent-web/turn_2_attempt_1"
    assert captured["trace_ids"][1] == "task-1/agent-web/turn_2_attempt_2"


def test_trace_root_is_under_task_log_dir(client_and_log):
    client, task_log = client_and_log
    expected = (Path(task_log.log_dir) / "agenthub_traces").resolve()
    assert client._trace_root == expected


def test_sanitize_stateful_history_removes_unsigned_thinking_items(client_and_log):
    client, task_log = client_and_log

    history = [
        {
            "role": "user",
            "content_items": [{"type": "text", "text": "hello"}],
        },
        {
            "role": "assistant",
            "content_items": [
                {"type": "thinking", "thinking": "kept", "signature": '{"id":"x"}'},
                {"type": "thinking", "thinking": "drop me"},
                {"type": "text", "text": "answer"},
            ],
        },
    ]

    class FakeInnerClient:
        def __init__(self, history_ref):
            self._history = history_ref

    class FakeAutoClient:
        def __init__(self, history_ref):
            self._client = FakeInnerClient(history_ref)

    stateful_client = FakeAutoClient(history)
    client._sanitize_stateful_history(stateful_client)

    content_items = history[1]["content_items"]
    assert len(content_items) == 2
    assert content_items[0]["type"] == "thinking"
    assert "signature" in content_items[0]
    assert content_items[1]["type"] == "text"

    warnings = [
        entry
        for entry in task_log.entries
        if entry["info_level"] == "warning"
        and entry["step_name"] == "LLM | AgentHub History Sanitized"
    ]
    assert warnings


def test_sanitize_stateful_history_drops_assistant_replay_for_openrouter_gpt52(
    tmp_path: Path,
):
    cfg = OmegaConf.create(
        {
            "llm": {
                "provider": "agenthub",
                "model_name": "openai/gpt-5.2",
                "temperature": 1.0,
                "top_p": 1.0,
                "min_p": 0.0,
                "top_k": 0,
                "max_context_length": 32768,
                "max_tokens": 1024,
                "async_client": True,
                "keep_tool_result": -1,
                "api_key": "test-key",
                "base_url": "https://openrouter.ai/api/v1",
            }
        }
    )
    task_log = DummyTaskLog(log_dir=str(tmp_path / "logs"))
    client = AgentHubClientForTest(
        task_id="task-1",
        cfg=cfg,
        task_log=task_log,
    )

    history = [
        {
            "role": "user",
            "content_items": [{"type": "text", "text": "question"}],
        },
        {
            "role": "assistant",
            "content_items": [{"type": "text", "text": "answer"}],
        },
        {
            "role": "user",
            "content_items": [{"type": "text", "text": "tool result"}],
        },
    ]

    class FakeInnerClient:
        def __init__(self, history_ref):
            self._history = history_ref

    class FakeAutoClient:
        def __init__(self, history_ref):
            self._client = FakeInnerClient(history_ref)

    stateful_client = FakeAutoClient(history)
    client._sanitize_stateful_history(stateful_client)

    roles = [m["role"] for m in history]
    assert roles == ["user", "user"]

    warnings = [
        entry
        for entry in task_log.entries
        if entry["info_level"] == "warning"
        and entry["step_name"] == "LLM | AgentHub History Sanitized"
    ]
    assert warnings
