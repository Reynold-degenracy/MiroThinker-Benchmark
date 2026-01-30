# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

"""
AgentHub SDK adapter for MiroFlow-Agent.

This module provides an AgentHub client that is fully compatible with the existing
BaseClient interface, allowing seamless integration with MiroFlow-Agent's orchestration.

Key features:
- Supports all AgentHub models (Claude, GPT, Gemini, GLM, Qwen)
- Supports Base64 encoded multimodal attachments via UniMessage
- Supports streaming responses
- Supports tool calls
- Supports token statistics
"""

import asyncio
import dataclasses
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

import tiktoken

from ...utils.prompt_utils import generate_mcp_system_prompt
from ..base_client import BaseClient

logger = logging.getLogger("miroflow_agent")


@dataclasses.dataclass
class AgentHubClient(BaseClient):
    """
    AgentHub SDK adapter that implements the BaseClient interface.

    This client wraps the AgentHub AutoLLMClient and provides compatibility
    with MiroFlow-Agent's message format and orchestration system.

    Attributes:
        _agenthub_client: The underlying AgentHub AutoLLMClient instance
        _thinking_level: The thinking/reasoning level for models that support it
        _client_type: Optional client type override for forcing specific backend
    """

    def __post_init__(self):
        """Initialize the AgentHub client with configuration from cfg."""
        super().__post_init__()

        # AgentHub-specific configuration
        self._thinking_level: str = self.cfg.llm.get("thinking_level", "none")
        self._client_type: Optional[str] = self.cfg.llm.get("client_type", None)
        self._prompt_caching: str = self.cfg.llm.get("prompt_caching", "enable")

        # Token usage tracking specific to AgentHub
        self.last_call_tokens: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    def _create_client(self) -> Any:
        """
        Create an AgentHub AutoLLMClient instance.

        Returns:
            AutoLLMClient instance configured with the model and API settings
        """
        try:
            from agenthub import AutoLLMClient
        except ImportError as e:
            raise ImportError(
                "agenthub-sdk is not installed. "
                "Please install it with: pip install agenthub-sdk"
            ) from e

        client_kwargs = {
            "model": self.model_name,
        }

        # Add optional configuration
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        if self._client_type:
            client_kwargs["client_type"] = self._client_type

        return AutoLLMClient(**client_kwargs)

    def _convert_miroflow_content_to_uni_content_items(
        self, content: Any
    ) -> List[Dict[str, Any]]:
        """
        Convert MiroFlow message content to AgentHub UniMessage content_items format.

        This method handles:
        - Plain text strings
        - OpenAI-style content lists
        - Anthropic-style content lists
        - Base64 encoded multimodal content (images)
        - Tool use/call blocks
        - Tool result blocks

        Args:
            content: The message content (str or List[Dict])

        Returns:
            List of content items in AgentHub UniMessage format
        """
        if isinstance(content, str):
            return [{"type": "text", "text": content}]

        if not isinstance(content, list):
            return [{"type": "text", "text": str(content)}]

        content_items = []
        for item in content:
            if not isinstance(item, dict):
                content_items.append({"type": "text", "text": str(item)})
                continue

            item_type = item.get("type", "")

            if item_type == "text":
                content_items.append({"type": "text", "text": item.get("text", "")})

            elif item_type == "image_url":
                # Handle OpenAI-style image format
                # {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
                image_url = item.get("image_url", {})
                if isinstance(image_url, dict):
                    url = image_url.get("url", "")
                else:
                    url = str(image_url)
                content_items.append({"type": "image_url", "image_url": url})

            elif item_type == "image":
                # Handle alternative image format
                # Direct base64 or URL
                source = item.get("source", {})
                if source.get("type") == "base64":
                    media_type = source.get("media_type", "image/png")
                    data = source.get("data", "")
                    content_items.append({
                        "type": "image_url",
                        "image_url": f"data:{media_type};base64,{data}"
                    })
                else:
                    url = item.get("url", "")
                    content_items.append({"type": "image_url", "image_url": url})

            elif item_type == "tool_use":
                # Convert Anthropic-style tool_use to AgentHub tool_call
                content_items.append({
                    "type": "tool_call",
                    "tool_call_id": item.get("id", str(uuid.uuid4())),
                    "name": item.get("name", ""),
                    "arguments": item.get("input", {}),
                })

            elif item_type == "tool_result":
                # Convert tool_result format
                tool_result_item = {
                    "type": "tool_result",
                    "tool_call_id": item.get("tool_use_id", ""),
                    "text": "",
                }
                # Handle content which can be string or list
                result_content = item.get("content", "")
                if isinstance(result_content, str):
                    tool_result_item["text"] = result_content
                elif isinstance(result_content, list):
                    texts = []
                    images = []
                    for sub_item in result_content:
                        if isinstance(sub_item, dict):
                            if sub_item.get("type") == "text":
                                texts.append(sub_item.get("text", ""))
                            elif sub_item.get("type") == "image":
                                # Extract base64 image
                                source = sub_item.get("source", {})
                                if source.get("type") == "base64":
                                    media_type = source.get("media_type", "image/png")
                                    data = source.get("data", "")
                                    images.append(f"data:{media_type};base64,{data}")
                    tool_result_item["text"] = "\n".join(texts)
                    if images:
                        tool_result_item["images"] = images
                content_items.append(tool_result_item)

            else:
                # Unknown type, try to extract text
                text = item.get("text", item.get("content", str(item)))
                content_items.append({"type": "text", "text": str(text)})

        return content_items

    def _convert_miroflow_messages_to_uni_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert MiroFlow message history to AgentHub UniMessage format.

        Args:
            messages: List of MiroFlow format messages

        Returns:
            List of UniMessage dictionaries
        """
        uni_messages = []

        for msg in messages:
            role = msg.get("role", "user")

            # Skip system messages as they're handled separately in UniConfig
            if role == "system" or role == "developer":
                continue

            content = msg.get("content", "")
            content_items = self._convert_miroflow_content_to_uni_content_items(content)

            uni_message = {
                "role": role,
                "content_items": content_items,
            }

            uni_messages.append(uni_message)

        return uni_messages

    def _convert_uni_message_to_miroflow_format(
        self, uni_message: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Convert an AgentHub UniMessage back to MiroFlow message format.

        Args:
            uni_message: UniMessage dictionary

        Returns:
            MiroFlow format message dictionary
        """
        role = uni_message.get("role", "assistant")
        content_items = uni_message.get("content_items", [])

        # For simple text-only responses, return string content
        text_parts = []
        complex_content = []

        for item in content_items:
            item_type = item.get("type", "")

            if item_type == "text":
                text = item.get("text", "")
                text_parts.append(text)
                complex_content.append({"type": "text", "text": text})

            elif item_type == "thinking":
                # Optionally include thinking content
                thinking = item.get("thinking", "")
                if thinking:
                    text_parts.append(f"[Thinking: {thinking}]")

            elif item_type == "tool_call":
                complex_content.append({
                    "type": "tool_use",
                    "id": item.get("tool_call_id", str(uuid.uuid4())),
                    "name": item.get("name", ""),
                    "input": item.get("arguments", {}),
                })

        # Use simple string content if there are no tool calls
        has_tool_calls = any(c.get("type") == "tool_use" for c in complex_content)
        if has_tool_calls:
            return {"role": role, "content": complex_content}
        else:
            return {"role": role, "content": "\n".join(text_parts)}

    def _convert_miroflow_tools_to_uni_tools(
        self, tools_definitions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert MiroFlow tool definitions to AgentHub ToolSchema format.

        MiroFlow format:
        [{"name": "server", "tools": [{"name": "tool", "description": "...", "schema": {...}}]}]

        AgentHub format:
        [{"name": "server-tool", "description": "...", "parameters": {...}}]

        Args:
            tools_definitions: MiroFlow format tool definitions

        Returns:
            List of AgentHub ToolSchema dictionaries
        """
        uni_tools = []

        for server in tools_definitions:
            server_name = server.get("name", "")
            tools = server.get("tools", [])

            for tool in tools:
                # Skip tools that failed to load
                if "error" in tool and "name" not in tool:
                    continue

                tool_name = tool.get("name", "")
                description = tool.get("description", "")
                schema = tool.get("schema", {})

                uni_tools.append({
                    "name": f"{server_name}-{tool_name}",
                    "description": description,
                    "parameters": schema,
                })

        return uni_tools

    def _build_uni_config(
        self, system_prompt: str, tools_definitions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Build AgentHub UniConfig from system prompt and tool definitions.

        Args:
            system_prompt: The system prompt string
            tools_definitions: MiroFlow format tool definitions

        Returns:
            UniConfig dictionary
        """
        uni_config = {
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system_prompt": system_prompt,
            "tools": self._convert_miroflow_tools_to_uni_tools(tools_definitions),
            "thinking_level": self._thinking_level,
            "tool_choice": "auto",
        }

        # Add prompt caching configuration
        if self._prompt_caching:
            uni_config["prompt_caching"] = self._prompt_caching

        # Add top_p if not default
        if self.top_p != 1.0:
            uni_config["top_p"] = self.top_p

        return uni_config

    def _update_token_usage(self, usage_data: Optional[Dict[str, Any]]) -> None:
        """
        Update cumulative token usage from AgentHub UsageMetadata.

        Args:
            usage_data: UsageMetadata dictionary from AgentHub response
        """
        if usage_data:
            input_tokens = usage_data.get("prompt_tokens", 0) or 0
            output_tokens = usage_data.get("response_tokens", 0) or 0
            cached_tokens = usage_data.get("cached_tokens", 0) or 0
            thoughts_tokens = usage_data.get("thoughts_tokens", 0) or 0

            # Update last call tokens
            self.last_call_tokens = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens + thoughts_tokens,
            }

            # Update cumulative totals
            self.token_usage["total_input_tokens"] += input_tokens
            self.token_usage["total_output_tokens"] += output_tokens + thoughts_tokens
            self.token_usage["total_cache_read_input_tokens"] += cached_tokens

            self.task_log.log_step(
                "info",
                "LLM | Token Usage",
                f"Input: {input_tokens}, Output: {output_tokens}, "
                f"Thoughts: {thoughts_tokens}, Cached: {cached_tokens}",
            )
        else:
            self.task_log.log_step(
                "warning",
                "LLM | Token Usage",
                "Warning: No valid usage_data received from AgentHub.",
            )

    async def _handle_streaming_response(
        self,
        uni_messages: List[Dict[str, Any]],
        uni_config: Dict[str, Any],
        stream_queue: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Handle streaming response from AgentHub API.

        Args:
            uni_messages: List of UniMessage dictionaries
            uni_config: UniConfig dictionary
            stream_queue: Optional queue for streaming updates

        Returns:
            Complete UniMessage response
        """
        message_id = str(uuid.uuid4())
        events = []

        try:
            async for event in self.client.streaming_response(uni_messages, uni_config):
                events.append(event)

                # Send streaming updates if queue is provided
                if stream_queue:
                    content_items = event.get("content_items", [])
                    for item in content_items:
                        if item.get("type") == "text" and item.get("text"):
                            try:
                                await stream_queue.put({
                                    "event": "message",
                                    "data": {
                                        "message_id": message_id,
                                        "delta": {
                                            "content": item["text"],
                                        },
                                    },
                                })
                            except Exception as e:
                                logger.warning(f"Failed to send stream update: {e}")

        except Exception as e:
            logger.error(f"Error processing AgentHub streaming response: {e}", exc_info=True)
            raise

        # Concatenate events to form complete message
        uni_message = self.client.concat_uni_events_to_uni_message(events)

        return uni_message

    async def _create_message(
        self,
        system_prompt: str,
        messages_history: List[Dict[str, Any]],
        tools_definitions: List[Dict[str, Any]],
        keep_tool_result: int = -1,
        stream_queue: Optional[Any] = None,
    ) -> Tuple[Any, List[Dict[str, Any]]]:
        """
        Send message to AgentHub API and get response.

        Args:
            system_prompt: System prompt string
            messages_history: MiroFlow format message history
            tools_definitions: MiroFlow format tool definitions
            keep_tool_result: Number of tool results to keep (-1 = all)
            stream_queue: Optional queue for streaming updates

        Returns:
            Tuple of (response object, message history)
        """
        self.task_log.log_step(
            "info",
            "LLM | Call Start",
            f"Calling AgentHub LLM (model: {self.model_name})",
        )

        # Filter tool results if needed
        messages_for_llm = self._remove_tool_result_from_messages(
            messages_history, keep_tool_result
        )

        # Convert to AgentHub formats
        uni_messages = self._convert_miroflow_messages_to_uni_messages(messages_for_llm)
        uni_config = self._build_uni_config(system_prompt, tools_definitions)

        try:
            # Call AgentHub streaming API
            uni_message = await self._handle_streaming_response(
                uni_messages, uni_config, stream_queue
            )

            # Update token usage
            self._update_token_usage(uni_message.get("usage_metadata"))

            # Build mock response object for compatibility
            response = self._build_mock_response(uni_message)

            self.task_log.log_step(
                "info",
                "LLM | Call Status",
                f"LLM call status: {uni_message.get('finish_reason', 'N/A')}",
            )

            return response, messages_history

        except asyncio.CancelledError:
            self.task_log.log_step(
                "warning",
                "LLM | Call Cancelled",
                "⚠️ AgentHub API call was cancelled during execution",
            )
            raise
        except Exception as e:
            self.task_log.log_step(
                "error",
                "LLM | Call Failed",
                f"AgentHub LLM call failed: {str(e)}",
            )
            raise

    def _build_mock_response(self, uni_message: Dict[str, Any]) -> Any:
        """
        Build a mock response object compatible with process_llm_response.

        This creates an object that mimics the structure expected by the
        orchestration system, allowing seamless integration.

        Args:
            uni_message: UniMessage dictionary from AgentHub

        Returns:
            MockResponse object with content_items, usage, and finish_reason
        """
        class MockResponse:
            def __init__(self, uni_msg: Dict[str, Any]):
                self.content_items = uni_msg.get("content_items", [])
                self.usage_metadata = uni_msg.get("usage_metadata")
                self.finish_reason = uni_msg.get("finish_reason", "stop")
                self.id = str(uuid.uuid4())
                self.model = None
                self.role = "assistant"

        return MockResponse(uni_message)

    def process_llm_response(
        self, llm_response: Any, message_history: List[Dict], agent_type: str = "main"
    ) -> Tuple[str, bool, List[Dict]]:
        """
        Process LLM response and update message history.

        Args:
            llm_response: Response object from _create_message
            message_history: Current message history
            agent_type: Type of agent making the request

        Returns:
            Tuple of (response text, should_break flag, updated message history)
        """
        if not llm_response:
            self.task_log.log_step(
                "error",
                "LLM | Response Processing",
                "❌ AgentHub call failed, skipping this response.",
            )
            return "", True, message_history

        content_items = getattr(llm_response, "content_items", [])
        if not content_items:
            self.task_log.log_step(
                "error",
                "LLM | Response Processing",
                "❌ AgentHub response is empty or contains no content.",
            )
            return "", True, message_history

        # Extract text content and build assistant response
        assistant_response_text = ""
        assistant_response_content = []

        for item in content_items:
            item_type = item.get("type", "")

            if item_type == "text":
                text = item.get("text", "")
                assistant_response_text += text + "\n"
                assistant_response_content.append({"type": "text", "text": text})

            elif item_type == "thinking":
                # Optionally log thinking content
                thinking = item.get("thinking", "")
                if thinking:
                    self.task_log.log_step(
                        "debug",
                        "LLM | Thinking",
                        f"Model thinking: {thinking[:200]}...",
                    )

            elif item_type == "tool_call":
                assistant_response_content.append({
                    "type": "tool_use",
                    "id": item.get("tool_call_id", str(uuid.uuid4())),
                    "name": item.get("name", ""),
                    "input": item.get("arguments", {}),
                })

        # Add assistant response to history
        message_history.append({
            "role": "assistant",
            "content": assistant_response_content if assistant_response_content else assistant_response_text.strip(),
        })

        self.task_log.log_step(
            "info",
            "LLM | Response",
            f"LLM Response: {assistant_response_text[:500]}..."
            if len(assistant_response_text) > 500
            else f"LLM Response: {assistant_response_text}",
        )

        # Check for context length issues
        should_break = getattr(llm_response, "finish_reason", "") == "length"

        return assistant_response_text.strip(), should_break, message_history

    def extract_tool_calls_info(
        self, llm_response: Any, assistant_response_text: str
    ) -> List[Dict]:
        """
        Extract tool call information from LLM response.

        Args:
            llm_response: Response object from _create_message
            assistant_response_text: The text content of the response

        Returns:
            List of tool call dictionaries with server_name, tool_name, arguments, id
        """
        from ...utils.parsing_utils import parse_llm_response_for_tool_calls

        # First try to extract from response content_items
        tool_calls = []
        content_items = getattr(llm_response, "content_items", [])

        for item in content_items:
            if item.get("type") == "tool_call":
                # Parse the combined name back to server_name and tool_name
                full_name = item.get("name", "")
                if "-" in full_name:
                    # Split on first hyphen to get server and tool name
                    parts = full_name.split("-", 1)
                    server_name = parts[0]
                    tool_name = parts[1] if len(parts) > 1 else full_name
                else:
                    server_name = ""
                    tool_name = full_name

                tool_calls.append({
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "arguments": item.get("arguments", {}),
                    "id": item.get("tool_call_id", str(uuid.uuid4())),
                })

        # If no tool calls found in content_items, fall back to parsing MCP tags
        if not tool_calls:
            tool_calls = parse_llm_response_for_tool_calls(assistant_response_text)

        return tool_calls

    def update_message_history(
        self, message_history: List[Dict], all_tool_results_content_with_id: List[Tuple]
    ) -> List[Dict]:
        """
        Update message history with tool call results.

        Args:
            message_history: Current message history
            all_tool_results_content_with_id: List of (call_id, result_content) tuples

        Returns:
            Updated message history
        """
        # Merge all tool results into a single user message
        merged_text = "\n".join([
            item[1]["text"]
            for item in all_tool_results_content_with_id
            if item[1]["type"] == "text"
        ])

        # Also collect any images from tool results
        images = []
        for item in all_tool_results_content_with_id:
            result = item[1]
            if "images" in result and result["images"]:
                images.extend(result["images"])

        # Build content - use list format if there are images, string otherwise
        if images:
            content = [{"type": "text", "text": merged_text}]
            for img in images:
                content.append({"type": "image_url", "image_url": img})
            message_history.append({"role": "user", "content": content})
        else:
            message_history.append({"role": "user", "content": merged_text})

        return message_history

    def generate_agent_system_prompt(self, date: Any, mcp_servers: List[Dict]) -> str:
        """
        Generate the system prompt for the agent.

        Args:
            date: Current date
            mcp_servers: List of MCP server configurations

        Returns:
            System prompt string
        """
        return generate_mcp_system_prompt(date, mcp_servers)

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate the number of tokens in text using tiktoken.

        Args:
            text: Text to estimate tokens for

        Returns:
            Estimated token count
        """
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
            # Fallback: approximately 1 token per 4 characters
            return len(text) // 4

    def ensure_summary_context(
        self, message_history: List[Dict], summary_prompt: str
    ) -> Tuple[bool, List[Dict]]:
        """
        Check if current context will exceed limits and rollback if needed.

        Args:
            message_history: Current message history
            summary_prompt: Summary prompt to be added

        Returns:
            Tuple of (pass_check, updated_message_history)
            - pass_check: True if context is OK, False if rolled back
        """
        # Get token usage from the last LLM call
        last_prompt_tokens = self.last_call_tokens.get("prompt_tokens", 0)
        last_completion_tokens = self.last_call_tokens.get("completion_tokens", 0)
        buffer_factor = 1.5

        # Calculate token count for summary prompt
        summary_tokens = int(self._estimate_tokens(summary_prompt) * buffer_factor)

        # Calculate token count for the last user message
        last_user_tokens = 0
        if message_history and message_history[-1]["role"] == "user":
            content = message_history[-1]["content"]
            if isinstance(content, str):
                last_user_tokens = int(self._estimate_tokens(content) * buffer_factor)
            else:
                last_user_tokens = int(self._estimate_tokens(str(content)) * buffer_factor)

        # Calculate total estimated tokens
        estimated_total = (
            last_prompt_tokens
            + last_completion_tokens
            + last_user_tokens
            + summary_tokens
            + self.max_tokens
            + 1000  # Buffer
        )

        if estimated_total >= self.max_context_length:
            self.task_log.log_step(
                "info",
                "LLM | Context Limit Reached",
                "Context limit reached, proceeding to step back and summarize",
            )

            # Remove the last user message (tool call results)
            if message_history and message_history[-1]["role"] == "user":
                message_history.pop()

            # Remove the second-to-last assistant message (tool call request)
            if message_history and message_history[-1]["role"] == "assistant":
                message_history.pop()

            self.task_log.log_step(
                "info",
                "LLM | Context Limit Reached",
                f"Removed last assistant-user pair, history length: {len(message_history)}",
            )

            return False, message_history

        self.task_log.log_step(
            "info",
            "LLM | Context Check",
            f"{estimated_total}/{self.max_context_length}",
        )
        return True, message_history

    def format_token_usage_summary(self) -> Tuple[List[str], str]:
        """
        Format token usage statistics for display and logging.

        Returns:
            Tuple of (summary_lines, log_string)
        """
        token_usage = self.get_token_usage()

        total_input = token_usage.get("total_input_tokens", 0)
        total_output = token_usage.get("total_output_tokens", 0)
        total_cache_read = token_usage.get("total_cache_read_input_tokens", 0)

        summary_lines = []
        summary_lines.append("\n" + "-" * 20 + " Token Usage " + "-" * 20)
        summary_lines.append(f"Total Input Tokens: {total_input}")
        summary_lines.append(f"Total Cache Read Input Tokens: {total_cache_read}")
        summary_lines.append(f"Total Output Tokens: {total_output}")
        summary_lines.append("-" * (40 + len(" Token Usage ")))
        summary_lines.append("Pricing is disabled - no cost information available")
        summary_lines.append("-" * (40 + len(" Token Usage ")))

        log_string = (
            f"[AgentHub/{self.model_name}] Total Input: {total_input}, "
            f"Cache Read: {total_cache_read}, "
            f"Output: {total_output}"
        )

        return summary_lines, log_string

    def get_token_usage(self) -> Dict[str, int]:
        """Get current token usage statistics."""
        return self.token_usage.copy()
