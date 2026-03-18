import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from src.core import pipeline as pipeline_module
from src.llm.providers.anthropic_client import AnthropicClient
from src.llm.providers.openai_client import OpenAIClient


class DummyTaskLog:
    def __init__(self, log_dir: str) -> None:
        self.log_dir = log_dir
        self.entries = []

    def log_step(self, info_level, step_name, message, metadata=None):
        self.entries.append(
            {
                "info_level": info_level,
                "step_name": step_name,
                "message": message,
                "metadata": metadata,
            }
        )


def build_openai_like_cfg(provider: str):
    return OmegaConf.create(
        {
            "llm": {
                "provider": provider,
                "model_name": "test-model",
                "temperature": 0.0,
                "top_p": 1.0,
                "min_p": 0.0,
                "top_k": 0,
                "max_context_length": 8192,
                "max_tokens": 1024,
                "async_client": True,
                "keep_tool_result": -1,
                "api_key": "test-key",
                "base_url": "https://example.invalid",
            }
        }
    )


def test_openai_client_uses_api_session_id_for_upstream_header(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_http_client(**kwargs):
        captured["http_client_kwargs"] = kwargs
        return SimpleNamespace()

    def fake_async_openai(**kwargs):
        captured["client_kwargs"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(
        "src.llm.providers.openai_client.DefaultAsyncHttpxClient",
        fake_http_client,
    )
    monkeypatch.setattr(
        "src.llm.providers.openai_client.AsyncOpenAI",
        fake_async_openai,
    )

    OpenAIClient(
        run_id="run-123",
        api_session_id="session-abc",
        cfg=build_openai_like_cfg("openai"),
        task_log=DummyTaskLog(str(tmp_path / "logs")),
    )

    assert (
        captured["http_client_kwargs"]["headers"]["x-upstream-session-id"]
        == "session-abc"
    )


def test_anthropic_client_uses_api_session_id_for_upstream_header(
    monkeypatch, tmp_path: Path
):
    captured = {}

    def fake_http_client(**kwargs):
        captured["http_client_kwargs"] = kwargs
        return SimpleNamespace()

    def fake_async_anthropic(**kwargs):
        captured["client_kwargs"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(
        "src.llm.providers.anthropic_client.DefaultAsyncHttpxClient",
        fake_http_client,
    )
    monkeypatch.setattr(
        "src.llm.providers.anthropic_client.AsyncAnthropic",
        fake_async_anthropic,
    )

    AnthropicClient(
        run_id="run-123",
        api_session_id="session-abc",
        cfg=build_openai_like_cfg("anthropic"),
        task_log=DummyTaskLog(str(tmp_path / "logs")),
    )

    assert (
        captured["http_client_kwargs"]["headers"]["x-upstream-session-id"]
        == "session-abc"
    )


@pytest.mark.asyncio
async def test_execute_task_pipeline_separates_api_session_id_and_run_id(
    monkeypatch, tmp_path: Path
):
    captured = {}

    class FakeToolManager:
        def set_task_log(self, task_log):
            captured.setdefault("tool_manager_task_logs", []).append(task_log)

    class FakeLLMClient:
        def __init__(self, run_id, api_session_id):
            self.run_id = run_id
            self.api_session_id = api_session_id
            self.task_log = None

        def close(self):
            return None

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            captured["orchestrator_kwargs"] = kwargs

        async def run_main_agent(
            self, task_description, task_file_name, run_id, initial_message_history=None
        ):
            captured["run_main_agent_kwargs"] = {
                "task_description": task_description,
                "task_file_name": task_file_name,
                "run_id": run_id,
                "initial_message_history": initial_message_history,
            }
            return "summary", "boxed"

    def fake_client_factory(run_id, api_session_id, cfg, task_log=None, **kwargs):
        captured["client_factory"] = {
            "run_id": run_id,
            "api_session_id": api_session_id,
            "task_log": task_log,
        }
        return FakeLLMClient(run_id=run_id, api_session_id=api_session_id)

    monkeypatch.setattr(pipeline_module, "ClientFactory", fake_client_factory)
    monkeypatch.setattr(pipeline_module, "Orchestrator", FakeOrchestrator)

    cfg = OmegaConf.create(
        {
            "llm": {
                "provider": "openai",
                "model_name": "test-model",
                "temperature": 0.0,
                "top_p": 1.0,
                "min_p": 0.0,
                "top_k": 0,
                "max_context_length": 8192,
                "max_tokens": 1024,
                "async_client": True,
                "keep_tool_result": -1,
                "repetition_penalty": 1.0,
                "api_key": "test-key",
                "base_url": "https://example.invalid",
            },
            "agent": {
                "keep_tool_result": -1,
                "main_agent": {"max_turns": 8},
                "sub_agents": None,
            },
        }
    )

    _, _, log_file_path = await pipeline_module.execute_task_pipeline(
        cfg=cfg,
        api_session_id="session-xyz",
        run_id="run-xyz",
        task_description="demo task",
        task_file_name="",
        main_agent_tool_manager=FakeToolManager(),
        sub_agent_tool_managers={},
        output_formatter=object(),
        log_dir=str(tmp_path / "logs"),
    )

    assert captured["client_factory"]["api_session_id"] == "session-xyz"
    assert captured["client_factory"]["run_id"] == "run-xyz"
    assert captured["run_main_agent_kwargs"]["run_id"] == "run-xyz"

    log_data = json.loads(Path(log_file_path).read_text(encoding="utf-8"))
    assert log_data["run_id"] == "run-xyz"
    assert log_data["task_id"] == "run-xyz"
    assert log_data["input"]["api_session_id"] == "session-xyz"
    assert log_data["input"]["run_id"] == "run-xyz"
