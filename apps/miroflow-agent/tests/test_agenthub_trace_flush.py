#!/usr/bin/env python3
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agenthub.integration.tracer import Tracer
from src.llm.providers import agenthub_client as agenthub_client_module


class _StubTaskLog:
    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        self.current_subagent_run_id = None

    def log_step(self, *_args, **_kwargs):
        return None


class _FakeAutoLLMClient:
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        self._model = model
        self._history = []
        self.api_key = api_key
        self.base_url = base_url

    async def streaming_response_stateful(self, message, config):
        events = [
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
                    "prompt_tokens": 1,
                    "response_tokens": 1,
                    "cached_tokens": 0,
                },
                "finish_reason": "stop",
            },
        ]

        for event in events:
            yield event

        self._history.append(message)
        self._history.append(
            {
                "role": "assistant",
                "content_items": [{"type": "text", "text": "hello"}],
                "usage_metadata": events[-1]["usage_metadata"],
                "finish_reason": "stop",
            }
        )

        if config.get("trace_id"):
            Tracer().save_history(self._model, self._history, config["trace_id"], config)

    def clear_history(self):
        self._history.clear()

    def get_history(self):
        return list(self._history)


class AgentHubTraceFlushTests(unittest.TestCase):
    def _make_cfg(self):
        return OmegaConf.create(
            {
                "llm": {
                    "provider": "agenthub",
                    "model_name": "glm-5-agenthub",
                    "temperature": 0,
                    "top_p": 1,
                    "min_p": 0,
                    "top_k": 0,
                    "max_context_length": 32000,
                    "max_tokens": 1024,
                    "async_client": True,
                    "keep_tool_result": -1,
                    "api_key": "test-key",
                    "base_url": "https://example.invalid",
                    "use_tool_calls": False,
                    "repetition_penalty": 1.0,
                }
            }
        )

    async def _run_two_turns(self, client):
        for turn in (1, 2):
            client._current_agent_type = "main"
            client._current_step_id = turn
            response, history = await client._create_message(
                system_prompt="system",
                messages_history=[{"role": "user", "content": f"turn {turn}"}],
                tools_definitions=[],
            )
            self.assertEqual(response.choices[0].message.content, "hello")
            self.assertEqual(history[0]["content"], f"turn {turn}")

    def test_trace_files_flush_only_on_close_and_keep_last_turn_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_log = _StubTaskLog(log_dir=tmpdir)

            with patch.object(agenthub_client_module, "_AutoLLMClient", _FakeAutoLLMClient):
                client = agenthub_client_module.AgentHubClient(
                    run_id="run-123",
                    api_session_id="session-456",
                    cfg=self._make_cfg(),
                    task_log=task_log,
                )

                asyncio.run(self._run_two_turns(client))

                trace_root = Path(tmpdir) / "agenthub_traces"
                self.assertFalse(
                    any(trace_root.rglob("*.json")),
                    "trace files should not be written before close()",
                )
                self.assertFalse(
                    any(trace_root.rglob("*.txt")),
                    "trace text files should not be written before close()",
                )

                client.close()

                json_files = sorted(
                    str(path.relative_to(trace_root)) for path in trace_root.rglob("*.json")
                )
                txt_files = sorted(
                    str(path.relative_to(trace_root)) for path in trace_root.rglob("*.txt")
                )

                self.assertEqual(
                    json_files,
                    ["run-123/main/turn_2_attempt_1.json"],
                )
                self.assertEqual(
                    txt_files,
                    ["run-123/main/turn_2_attempt_1.txt"],
                )


if __name__ == "__main__":
    unittest.main()
