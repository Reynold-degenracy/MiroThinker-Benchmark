# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

import asyncio
import copy
import dataclasses
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tiktoken
from agenthub import AutoLLMClient as _AutoLLMClient
from agenthub.types import (
    ImageContentItem,
    TextContentItem,
    ToolCallContentItem,
    ToolResultContentItem,
    UniConfig,
    UniMessage,
)

from ...utils.prompt_utils import generate_mcp_system_prompt
from ..base_client import BaseClient

logger = logging.getLogger("miroflow_agent")

# Regex for <miro_image_data> markers (same pattern as openai_client.py)
_IMG_MARKER_PATTERN = re.compile(
    r"<miro_image_data>(.*?)</miro_image_data>", re.DOTALL
)


def to_uni_config(
    temperature: float,
    max_tokens: int,
    system_prompt: str,
) -> UniConfig:
    """Convert MiroThinker params to UniConfig.

    Note: tools_definitions are not passed to the API because MiroThinker
    uses XML-based tool calls (<use_mcp_tool> tags) rather than native tool calls.
    """
    config: UniConfig = UniConfig(
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if system_prompt:
        config["system_prompt"] = system_prompt
    return config


def to_uni_messages(messages_history: List[Dict[str, Any]]) -> List[UniMessage]:
    """Convert MiroThinker messages_history to list[UniMessage].

    Handles the following MiroThinker content formats:
    - str content → TextContentItem
    - list[{"type": "text"}] → TextContentItem
    - list[{"type": "image_url", "image_url": {"url": ...}}] → ImageContentItem
    - list[{"type": "tool_use", ...}] (assistant) → ToolCallContentItem
    - list[{"type": "tool_result", ...}] (user) → ToolResultContentItem
    - str with <miro_image_data>URL</miro_image_data> markers → mixed text/image items
    """
    uni_messages: List[UniMessage] = []

    for msg in messages_history:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue

        content = msg.get("content", "")
        content_items: List[Any] = []

        if isinstance(content, str):
            # Check for <miro_image_data> markers
            if "<miro_image_data>" in content:
                last_pos = 0
                for match in _IMG_MARKER_PATTERN.finditer(content):
                    pre_text = content[last_pos : match.start()]
                    if pre_text.strip():
                        content_items.append(
                            TextContentItem(type="text", text=pre_text)
                        )
                    image_url = match.group(1).strip()
                    if image_url:
                        content_items.append(
                            ImageContentItem(type="image_url", image_url=image_url)
                        )
                    last_pos = match.end()
                post_text = content[last_pos:]
                if post_text.strip():
                    content_items.append(
                        TextContentItem(type="text", text=post_text)
                    )
            elif content:
                content_items.append(TextContentItem(type="text", text=content))

        elif isinstance(content, list):
            for item in content:
                item_type = item.get("type")

                if item_type == "text":
                    text = item.get("text", "")
                    if text:
                        content_items.append(
                            TextContentItem(type="text", text=text)
                        )

                elif item_type == "image_url":
                    img = item.get("image_url", {})
                    url = img.get("url", "") if isinstance(img, dict) else str(img)
                    if url:
                        content_items.append(
                            ImageContentItem(type="image_url", image_url=url)
                        )

                elif item_type == "tool_use":
                    # Anthropic-style tool call from assistant
                    content_items.append(
                        ToolCallContentItem(
                            type="tool_call",
                            name=item.get("name", ""),
                            arguments=item.get("input", {}),
                            tool_call_id=item.get("id") or str(uuid.uuid4()),
                        )
                    )

                elif item_type == "tool_result":
                    # Anthropic-style tool result in user message
                    result_content = item.get("content", [])
                    if isinstance(result_content, list):
                        result_text = " ".join(
                            r.get("text", "")
                            for r in result_content
                            if r.get("type") == "text"
                        )
                    else:
                        result_text = str(result_content)
                    content_items.append(
                        ToolResultContentItem(
                            type="tool_result",
                            result=result_text,
                            tool_call_id=item.get("tool_use_id") or "",
                        )
                    )

        if content_items:
            uni_messages.append(
                UniMessage(role=role, content_items=content_items)
            )

    return uni_messages


@dataclasses.dataclass
class AgentHubClient(BaseClient):
    """LLM client that routes requests through the agenthub AutoLLMClient.

    Converts MiroThinker's internal message/config format to the
    agenthub UniConfig/UniMessage format, then maps the UniEvent stream
    back to an OpenAI-compatible MockResponse so that the existing
    orchestrator logic works without modification.
    """

    def __post_init__(self):
        super().__post_init__()
        # Initialize last_call_tokens so ensure_summary_context never fails
        # on the first call before _update_token_usage has been invoked.
        self.last_call_tokens: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        # Keep separate stateful clients per conversation key.
        self._stateful_clients: Dict[str, _AutoLLMClient] = {}
        # Track attempts per (agent_type, turn) for trace_id generation.
        self._trace_attempt_counters: Dict[Tuple[str, int], int] = {}
        # Keep only the final successful trace payload and flush it on close().
        self._pending_trace: Optional[Dict[str, Any]] = None
        # Persist all AgentHub traces under task log directory.
        base_log_dir = Path(getattr(self.task_log, "log_dir", "logs")).expanduser()
        self._trace_root = (base_log_dir / "agenthub_traces").resolve()
        self._trace_root.mkdir(parents=True, exist_ok=True)
        os.environ["AGENTHUB_CACHE_DIR"] = str(self._trace_root)

    # ------------------------------------------------------------------
    # BaseClient interface
    # ------------------------------------------------------------------

    def _create_client(self) -> _AutoLLMClient:
        """Create AutoLLMClient from agenthub."""
        return _AutoLLMClient(
            model=self.model_name,
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def _conversation_key(self, agent_type: str) -> str:
        """Build an isolated stateful conversation key."""
        root = self.api_session_id
        if agent_type == "main":
            return f"{root}/main"
        subagent_run_id = getattr(self.task_log, "current_subagent_run_id", None)
        if subagent_run_id:
            return f"{root}/{subagent_run_id}"
        return f"{root}/{agent_type}"

    def _get_stateful_client(self, agent_type: str) -> _AutoLLMClient:
        """Get or create a stateful AutoLLMClient for current conversation."""
        key = self._conversation_key(agent_type)
        client = self._stateful_clients.get(key)
        if client is None:
            client = _AutoLLMClient(
                model=self.model_name,
                api_key=self.api_key,
                base_url=self.base_url,
            )
            self._stateful_clients[key] = client
        return client

    # def _sanitize_stateful_history(self, stateful_client: Any) -> None:
    #     """Remove malformed reasoning items that break AgentHub GPT-5.2 replay.

    #     OpenRouter may emit reasoning summary deltas without a signature field.
    #     AgentHub GPT-5.2 expects every `thinking` item in history to include
    #     `signature`, otherwise replay on the next turn raises KeyError('signature').
    #     """
    #     is_openrouter_gpt52 = (
    #         "openrouter.ai" in (self.base_url or "").lower()
    #         and "gpt-5.2" in (self.model_name or "").lower()
    #     )
    #     inner_client = getattr(stateful_client, "_client", None)
    #     history = getattr(inner_client, "_history", None)
    #     if not isinstance(history, list):
    #         return

    #     removed_items = 0
    #     removed_messages = 0
    #     new_history: List[Dict[str, Any]] = []

    #     for message in history:
    #         if not isinstance(message, dict):
    #             continue
    #         role = message.get("role")
    #         # OpenRouter Responses API rejects assistant-role replay items in input.
    #         if is_openrouter_gpt52 and role == "assistant":
    #             removed_messages += 1
    #             continue

    #         content_items = message.get("content_items")
    #         if not isinstance(content_items, list):
    #             new_history.append(message)
    #             continue

    #         filtered_items: List[Dict[str, Any]] = []
    #         for item in content_items:
    #             if (
    #                 isinstance(item, dict)
    #                 and item.get("type") == "thinking"
    #                 and not item.get("signature")
    #             ):
    #                 removed_items += 1
    #                 continue
    #             filtered_items.append(item)

    #         if len(filtered_items) != len(content_items):
    #             new_message = message.copy()
    #             new_message["content_items"] = filtered_items
    #             message = new_message

    #         # If everything got filtered out, skip this message.
    #         if not filtered_items:
    #             removed_messages += 1
    #             continue
    #         new_history.append(message)

    #     history[:] = new_history

    #     if removed_items > 0 or removed_messages > 0:
    #         self.task_log.log_step(
    #             "warning",
    #             "LLM | AgentHub History Sanitized",
    #             (
    #                 "Removed "
    #                 f"{removed_items} malformed thinking item(s) and "
    #                 f"{removed_messages} message(s) from stateful history."
    #             ),
    #         )

    def _build_trace_id(self, agent_type: str, turn: int) -> str:
        """Build per-call trace id in required format."""
        counter_key = (agent_type, turn)
        attempt = self._trace_attempt_counters.get(counter_key, 0) + 1
        self._trace_attempt_counters[counter_key] = attempt
        return f"{self.run_id}/{agent_type}/turn_{turn}_attempt_{attempt}"

    def _remember_pending_trace(
        self,
        stateful_client: _AutoLLMClient,
        trace_id: str,
        config: Dict[str, Any],
    ) -> None:
        """Snapshot the latest successful stateful history for flush-on-close."""
        history_getter = getattr(stateful_client, "get_history", None)
        if history_getter is None:
            return

        history = history_getter()
        if not history:
            return

        self._pending_trace = {
            "model": self.model_name,
            "trace_id": trace_id,
            "config": copy.deepcopy(config),
            "history": copy.deepcopy(history),
        }

    def _flush_pending_trace(self) -> None:
        """Persist only the last successful AgentHub trace."""
        if not self._pending_trace:
            return

        from agenthub.integration.tracer import Tracer

        pending_trace = self._pending_trace
        tracer = Tracer(cache_dir=self._trace_root)
        tracer.save_history(
            pending_trace["model"],
            pending_trace["history"],
            pending_trace["trace_id"],
            pending_trace["config"],
        )
        self._pending_trace = None

    @staticmethod
    def _get_latest_user_message(
        messages_history: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent user message."""
        for msg in reversed(messages_history):
            if msg.get("role") == "user":
                return msg
        return None

    def _update_token_usage(self, usage_data: Any) -> None:
        """Update cumulative token usage from a UniEvent usage_metadata dict."""
        if not usage_data:
            self.task_log.log_step(
                "warning", "LLM | Token Usage", "No usage_data received."
            )
            return

        # usage_data may be a plain dict or a lightweight wrapper object.
        get_value = usage_data.get if hasattr(usage_data, "get") else None
        prompt_tokens: int = (get_value("prompt_tokens") if get_value else 0) or 0
        response_tokens: int = (get_value("response_tokens") if get_value else 0) or 0
        cached_tokens: int = (get_value("cached_tokens") if get_value else 0) or 0

        self.token_usage["total_input_tokens"] += prompt_tokens
        self.token_usage["total_output_tokens"] += response_tokens
        self.token_usage["total_cache_read_input_tokens"] += cached_tokens

        self.last_call_tokens = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": response_tokens,
        }

        self.task_log.log_step(
            "info",
            "LLM | Token Usage",
            f"Input: {prompt_tokens}, Output: {response_tokens}, Cached: {cached_tokens}",
        )

    async def _create_message(
        self,
        system_prompt: str,
        messages_history: List[Dict[str, Any]],
        tools_definitions: Any,
        keep_tool_result: int = -1,
        stream_queue: Optional[Any] = None,
    ) -> Tuple[Any, List[Dict[str, Any]]]:
        """Send messages via AgentHub AutoLLMClient with streaming support."""
        self.task_log.log_step(
            "info",
            "LLM | Call Start",
            f"Calling LLM via AgentHub (model={self.model_name})",
        )

        # Filter tool results to save tokens (same as other clients)
        messages_for_llm = self._remove_tool_result_from_messages(
            messages_history, keep_tool_result
        )

        # For stateful API calls, only send the newest user message and let
        # AgentHub maintain long-term conversation history internally.
        latest_user_msg = self._get_latest_user_message(messages_for_llm)
        if latest_user_msg is None:
            raise ValueError(
                "AgentHub stateful call requires at least one user message."
            )
        uni_messages = to_uni_messages([latest_user_msg])
        if not uni_messages:
            raise ValueError("Failed to convert latest user message to UniMessage.")

        agent_type = str(getattr(self, "_current_agent_type", "main"))
        turn = int(getattr(self, "_current_step_id", 1) or 1)

        # Convert to agenthub formats
        config = to_uni_config(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            system_prompt=system_prompt,
        )
        trace_id = self._build_trace_id(agent_type=agent_type, turn=turn)
        trace_config = dict(config)
        trace_config["trace_id"] = trace_id
        stateful_client = self._get_stateful_client(agent_type)
        # Ensure prior turns won't crash on GPT-5.2 replay due to malformed reasoning items.
        # self._sanitize_stateful_history(stateful_client)

        try:
            response = await self._consume_uni_stream(
                stateful_client, uni_messages[0], config, stream_queue
            )
            # AgentHub appends assistant message into stateful history after streaming.
            # self._sanitize_stateful_history(stateful_client)
            self._update_token_usage(response.usage)
            self.task_log.log_step(
                "info",
                "LLM | Call Status",
                f"finish_reason: {response.choices[0].finish_reason}",
            )
            self._remember_pending_trace(
                stateful_client=stateful_client,
                trace_id=trace_id,
                config=trace_config,
            )
            # Return the original messages_history (not the filtered copy) so the
            # complete conversation history is preserved in logs.
            return response, messages_history

        except asyncio.CancelledError:
            self.task_log.log_step(
                "warning",
                "LLM | Call Cancelled",
                "LLM API call was cancelled during execution",
            )
            raise
        except Exception as e:
            self.task_log.log_step(
                "error",
                "LLM | Call Failed",
                f"AgentHub LLM call failed: {str(e)}",
            )
            raise

    # ------------------------------------------------------------------
    # Stream consumer
    # ------------------------------------------------------------------

    async def _consume_uni_stream(
        self,
        stateful_client: _AutoLLMClient,
        uni_message: UniMessage,
        config: UniConfig,
        stream_queue: Optional[Any] = None,
    ) -> Any:
        """Consume an agenthub UniEvent stream and return an OpenAI-style MockResponse.

        Accumulates text and tool-call deltas from the stream, forwards text
        deltas to *stream_queue* when provided, and builds a MockResponse
        object whose structure matches what OpenAI's streaming handler returns
        so that the existing process_llm_response / extract_tool_calls_info
        methods work without modification.
        """
        accumulated_content = ""
        accumulated_tool_calls: List[Dict[str, Any]] = []
        finish_reason: Optional[str] = None
        usage_data: Optional[Dict[str, Any]] = None
        message_id = str(uuid.uuid4())

        async for event in stateful_client.streaming_response_stateful(
            message=uni_message,
            config=config,
        ):
            event_type = event.get("event_type")
            # Some agenthub backends emit usage_metadata in non-stop events.
            if event.get("usage_metadata") is not None:
                usage_data = event.get("usage_metadata")

            if event_type == "delta":
                for item in event.get("content_items", []):
                    item_type = item.get("type")

                    if item_type == "text":
                        text: str = item.get("text", "")
                        accumulated_content += text
                        if stream_queue and text:
                            try:
                                await stream_queue.put(
                                    {
                                        "event": "message",
                                        "data": {
                                            "message_id": message_id,
                                            "delta": {"content": text},
                                        },
                                    }
                                )
                            except Exception as exc:
                                logger.warning(
                                    f"Failed to send stream update: {exc}"
                                )

                    elif item_type == "partial_tool_call":
                        tc_id: str = item.get("tool_call_id", "")
                        tc_name: str = item.get("name", "")
                        tc_args: str = item.get("arguments", "")
                        existing = next(
                            (
                                tc
                                for tc in accumulated_tool_calls
                                if tc["id"] == tc_id
                            ),
                            None,
                        )
                        if existing:
                            existing["function"]["arguments"] += tc_args
                        else:
                            accumulated_tool_calls.append(
                                {
                                    "id": tc_id,
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": tc_args,
                                    },
                                }
                            )

                    elif item_type == "tool_call":
                        # Complete (non-partial) tool call in a delta event
                        tc_id = item.get("tool_call_id") or str(uuid.uuid4())
                        tc_name = item.get("name", "")
                        tc_args_raw = item.get("arguments", {})
                        tc_args_str = (
                            json.dumps(tc_args_raw)
                            if isinstance(tc_args_raw, dict)
                            else str(tc_args_raw)
                        )
                        accumulated_tool_calls.append(
                            {
                                "id": tc_id,
                                "type": "function",
                                "function": {
                                    "name": tc_name,
                                    "arguments": tc_args_str,
                                },
                            }
                        )

            elif event_type == "stop":
                if event.get("finish_reason"):
                    finish_reason = event.get("finish_reason")

        # ------------------------------------------------------------------
        # Build an OpenAI-compatible MockResponse
        # ------------------------------------------------------------------

        class _MockFunction:
            def __init__(self, name: str, arguments: str) -> None:
                self.name = name
                self.arguments = arguments

        class _MockToolCall:
            def __init__(self, id: str, type: str, function: Any) -> None:
                self.id = id
                self.type = type
                self.function = function

        class _MockMessage:
            def __init__(self, content: str, tool_calls: Optional[List]) -> None:
                self.role = "assistant"
                self.content = content
                self.tool_calls = tool_calls or None

        class _MockChoice:
            def __init__(self, message: Any, finish_reason: str) -> None:
                self.message = message
                self.finish_reason = finish_reason
                self.index = 0

        class _MockUsage:
            """Wraps the usage_metadata dict so _update_token_usage can call .get()."""

            def __init__(self, data: Optional[Dict[str, Any]]) -> None:
                self._data = data or {}

            def get(self, key: str, default: Any = None) -> Any:
                return self._data.get(key, default)

            def __bool__(self) -> bool:
                # Make empty usage behave like falsy so callers can detect missing usage.
                return bool(self._data)

        class _MockResponse:
            def __init__(self, choices: List, usage: Any) -> None:
                self.choices = choices
                self.usage = usage
                self.id = str(uuid.uuid4())
                self.model = None
                self.object = "chat.completion"

        tool_calls_formatted: Optional[List[_MockToolCall]] = None
        if accumulated_tool_calls:
            tool_calls_formatted = [
                _MockToolCall(
                    id=tc["id"],
                    type=tc["type"],
                    function=_MockFunction(
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    ),
                )
                for tc in accumulated_tool_calls
                if tc.get("id")
            ]

        # Map agenthub finish reasons to OpenAI finish reasons
        fr_map = {"stop": "stop", "length": "length", "unknown": "stop"}
        mapped_finish = fr_map.get(finish_reason or "stop", "stop")

        message = _MockMessage(accumulated_content, tool_calls_formatted)
        choice = _MockChoice(message, mapped_finish)
        return _MockResponse([choice], _MockUsage(usage_data))

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_llm_response(
        self,
        llm_response: Any,
        message_history: List[Dict],
        agent_type: str = "main",
    ) -> Tuple[str, bool, List[Dict]]:
        """Process LLM response (OpenAI-style MockResponse)."""
        if not llm_response or not llm_response.choices:
            self.task_log.log_step(
                "error",
                "LLM | Response Error",
                "LLM did not return a valid response.",
            )
            return "", True, message_history

        finish_reason = llm_response.choices[0].finish_reason
        assistant_response_text: str = (
            llm_response.choices[0].message.content or ""
        )

        if (
            finish_reason == "length"
            and "Context length exceeded" in assistant_response_text
        ):
            self.task_log.log_step(
                "warning",
                "LLM | Context Length",
                "Detected context length exceeded, returning error status",
            )
            message_history.append(
                {"role": "assistant", "content": assistant_response_text}
            )
            return assistant_response_text, True, message_history

        message_history.append(
            {"role": "assistant", "content": assistant_response_text}
        )
        self.task_log.log_step(
            "info", "LLM | Response", f"LLM Response: {assistant_response_text}"
        )
        return assistant_response_text, False, message_history

    def extract_tool_calls_info(
        self, llm_response: Any, assistant_response_text: str
    ) -> List[Dict]:
        """Extract tool call information from the LLM response.

        Prefers native (structured) tool calls when present; falls back to
        XML-based <use_mcp_tool> parsing otherwise.
        """
        from ...utils.parsing_utils import parse_llm_response_for_tool_calls

        # Use native tool calls if the model returned them
        if (
            llm_response
            and llm_response.choices
            and llm_response.choices[0].message.tool_calls
        ):
            return parse_llm_response_for_tool_calls(
                llm_response.choices[0].message.tool_calls
            )

        # Fall back to XML parsing (standard MiroThinker path)
        return parse_llm_response_for_tool_calls(assistant_response_text)

    def update_message_history(
        self,
        message_history: List[Dict],
        all_tool_results_content_with_id: List[Tuple],
    ) -> List[Dict]:
        """Update message history with tool call results (OpenAI-style)."""
        merged_text = "\n".join(
            [
                item[1]["text"]
                for item in all_tool_results_content_with_id
                if item[1]["type"] == "text"
            ]
        )
        message_history.append({"role": "user", "content": merged_text})
        return message_history

    def generate_agent_system_prompt(
        self, date: Any, mcp_servers: List[Dict]
    ) -> str:
        return generate_mcp_system_prompt(date, mcp_servers)

    # ------------------------------------------------------------------
    # Token estimation / context management
    # ------------------------------------------------------------------

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count using tiktoken."""
        if not hasattr(self, "encoding"):
            try:
                self.encoding = tiktoken.get_encoding("o200k_base")
            except Exception:
                self.encoding = tiktoken.get_encoding("cl100k_base")
        try:
            return len(self.encoding.encode(text))
        except Exception as e:
            self.task_log.log_step(
                "error",
                "LLM | Token Estimation Error",
                f"Error: {str(e)}",
            )
            return len(text) // 4

    def ensure_summary_context(
        self, message_history: list, summary_prompt: str
    ) -> Tuple[bool, list]:
        """Stateful AgentHub manages context; skip local rollback/cropping."""
        self.task_log.log_step(
            "debug",
            "LLM | Context Check Skipped",
            "AgentHub stateful mode enabled; skipping local context rollback.",
        )
        return True, message_history

    def close(self):
        """Clear all stateful AgentHub histories."""
        self._flush_pending_trace()
        for client in self._stateful_clients.values():
            if hasattr(client, "clear_history"):
                client.clear_history()
        self._stateful_clients.clear()

    def format_token_usage_summary(self) -> Tuple[List[str], str]:
        """Format token usage statistics."""
        token_usage = self.get_token_usage()

        total_input = token_usage.get("total_input_tokens", 0)
        total_output = token_usage.get("total_output_tokens", 0)
        cache_read = token_usage.get("total_cache_read_input_tokens", 0)

        summary_lines = [
            "\n" + "-" * 20 + " Token Usage " + "-" * 20,
            f"Total Input Tokens: {total_input}",
            f"Total Cache Read Tokens: {cache_read}",
            f"Total Output Tokens: {total_output}",
            "-" * (40 + len(" Token Usage ")),
            "Pricing is disabled - no cost information available",
            "-" * (40 + len(" Token Usage ")),
        ]
        log_string = (
            f"[{self.model_name}] Total Input: {total_input}, "
            f"Cache Read: {cache_read}, Output: {total_output}"
        )
        return summary_lines, log_string

    def get_token_usage(self) -> Dict[str, int]:
        return self.token_usage.copy()
