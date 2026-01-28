# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

import asyncio
import dataclasses
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

import tiktoken
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI

from ...utils.prompt_utils import generate_mcp_system_prompt
from ..base_client import BaseClient

logger = logging.getLogger("miroflow_agent")


@dataclasses.dataclass
class OpenAIClient(BaseClient):
    def _create_client(self) -> Union[AsyncOpenAI, OpenAI]:
        """Create LLM client"""
        # Enable detailed HTTP logs for debugging
        logging.getLogger("httpx").setLevel(logging.DEBUG)

        if self.api_key:
            masked_key = f"{self.api_key[:6]}...{self.api_key[-4:]}" if len(self.api_key) > 10 else "***"
            logger.info(f"LLM Client Init | Provider: {self.provider} | Model: {self.model_name} | Base URL: {self.base_url} | API Key: {masked_key}")
        else:
            logger.warning(f"LLM Client Init | Provider: {self.provider} | Model: {self.model_name} | Base URL: {self.base_url} | API Key: NOT SET")

        http_client_args = {"headers": {"x-upstream-session-id": self.task_id}}

        try:
            if self.async_client:
                http_client = DefaultAsyncHttpxClient(**http_client_args)
            else:
                http_client = DefaultHttpxClient(**http_client_args)
        except ImportError as e:
            if "socks" in str(e).lower():
                logger.warning(
                    "SOCKS proxy settings detected but 'socksio' is not installed. "
                    "Falling back to direct connection (ignoring proxy environment variables). "
                    "To use SOCKS proxy, install 'httpx[socks]'."
                )
                http_client_args["trust_env"] = False
                if self.async_client:
                    http_client = DefaultAsyncHttpxClient(**http_client_args)
                else:
                    http_client = DefaultHttpxClient(**http_client_args)
            else:
                raise e

        if self.async_client:
            return AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                http_client=http_client,
            )
        else:
            return OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                http_client=http_client,
            )

    def _update_token_usage(self, usage_data: Any) -> None:
        """Update cumulative token usage"""
        if usage_data:
            input_tokens = getattr(usage_data, "prompt_tokens", 0)
            output_tokens = getattr(usage_data, "completion_tokens", 0)
            prompt_tokens_details = getattr(usage_data, "prompt_tokens_details", None)
            if prompt_tokens_details:
                cached_tokens = (
                    getattr(prompt_tokens_details, "cached_tokens", None) or 0
                )
            else:
                cached_tokens = 0

            # Record token usage for the most recent call
            self.last_call_tokens = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
            }

            # OpenAI does not provide cache_creation_input_tokens
            self.token_usage["total_input_tokens"] += input_tokens
            self.token_usage["total_output_tokens"] += output_tokens
            self.token_usage["total_cache_read_input_tokens"] += cached_tokens

            self.task_log.log_step(
                "info",
                "LLM | Token Usage",
                f"Input: {self.token_usage['total_input_tokens']}, "
                f"Output: {self.token_usage['total_output_tokens']}",
            )

    async def _handle_streaming_response(
        self, 
        stream: Any, 
        stream_queue: Optional[Any] = None
    ) -> Any:
        """
        Handle streaming response from OpenAI API.
        Accumulates the full response and sends deltas to stream_queue if provided.
        
        :param stream: The streaming response from OpenAI API
        :param stream_queue: Optional queue for sending streaming updates
        :return: A complete response object compatible with non-streaming response
        """
        # Accumulate response data
        accumulated_content = ""
        accumulated_tool_calls = []
        finish_reason = None
        usage_data = None
        message_id = str(uuid.uuid4())
        
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                    
                choice = chunk.choices[0]
                delta = choice.delta
                
                # Handle content streaming
                if hasattr(delta, "content") and delta.content:
                    accumulated_content += delta.content
                    # Send streaming update
                    if stream_queue:
                        try:
                            await stream_queue.put({
                                "event": "message",
                                "data": {
                                    "message_id": message_id,
                                    "delta": {
                                        "content": delta.content,
                                    },
                                },
                            })
                        except Exception as e:
                            logger.warning(f"Failed to send stream update: {e}")
                
                # Handle tool calls
                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        # Ensure we have enough space in accumulated_tool_calls
                        while len(accumulated_tool_calls) <= tool_call_delta.index:
                            accumulated_tool_calls.append({
                                "id": None,
                                "type": "function",
                                "function": {
                                    "name": "",
                                    "arguments": "",
                                }
                            })
                        
                        tool_call = accumulated_tool_calls[tool_call_delta.index]
                        
                        # Accumulate tool call data
                        if tool_call_delta.id:
                            tool_call["id"] = tool_call_delta.id
                        if hasattr(tool_call_delta, "function"):
                            if tool_call_delta.function.name:
                                tool_call["function"]["name"] = tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                tool_call["function"]["arguments"] += tool_call_delta.function.arguments
                
                # Handle finish reason
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                
                # Handle usage data (typically in the last chunk)
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_data = chunk.usage
        
        except Exception as e:
            logger.error(f"Error processing streaming response: {e}", exc_info=True)
            raise
        
        # Construct a response object compatible with non-streaming response
        # Create a mock response object that matches the structure of non-streaming response
        class MockMessage:
            def __init__(self, content, tool_calls):
                self.role = "assistant"
                self.content = content
                self.tool_calls = tool_calls if tool_calls else None
        
        class MockChoice:
            def __init__(self, message, finish_reason):
                self.message = message
                self.finish_reason = finish_reason
                self.index = 0
        
        class MockResponse:
            def __init__(self, choices, usage):
                self.choices = choices
                self.usage = usage
                self.id = str(uuid.uuid4())
                self.model = None
                self.object = "chat.completion"
        
        # Convert accumulated tool calls to proper format
        tool_calls_formatted = None
        if accumulated_tool_calls and any(tc["id"] for tc in accumulated_tool_calls):
            tool_calls_formatted = []
            for tc in accumulated_tool_calls:
                if tc["id"]:  # Only include tool calls that have an ID
                    class MockToolCall:
                        def __init__(self, id, type, function):
                            self.id = id
                            self.type = type
                            self.function = function
                    
                    class MockFunction:
                        def __init__(self, name, arguments):
                            self.name = name
                            self.arguments = arguments
                    
                    tool_calls_formatted.append(
                        MockToolCall(
                            id=tc["id"],
                            type=tc["type"],
                            function=MockFunction(
                                name=tc["function"]["name"],
                                arguments=tc["function"]["arguments"]
                            )
                        )
                    )
        
        message = MockMessage(accumulated_content, tool_calls_formatted)
        choice = MockChoice(message, finish_reason or "stop")
        response = MockResponse([choice], usage_data)
        
        return response

    async def _handle_sync_streaming_response(
        self, 
        stream: Any, 
        stream_queue: Optional[Any] = None
    ) -> Any:
        """
        Handle synchronous streaming response from OpenAI API.
        Wraps sync stream iteration in async context.
        
        :param stream: The streaming response from OpenAI API (synchronous)
        :param stream_queue: Optional queue for sending streaming updates
        :return: A complete response object compatible with non-streaming response
        """
        # Accumulate response data
        accumulated_content = ""
        accumulated_tool_calls = []
        finish_reason = None
        usage_data = None
        message_id = str(uuid.uuid4())
        
        logger.info(f"[LLM Sync Stream] _handle_sync_streaming_response called, stream_queue={stream_queue is not None}")
        chunk_count = 0
        
        try:
            for chunk in stream:
                chunk_count += 1
                if not chunk.choices:
                    continue
                    
                choice = chunk.choices[0]
                delta = choice.delta
                
                logger.debug(f"[LLM Sync Stream] Received chunk #{chunk_count}, has_content={hasattr(delta, 'content') and delta.content is not None}")
                
                # Handle content streaming
                if hasattr(delta, "content") and delta.content:
                    accumulated_content += delta.content
                    # Send streaming update
                    if stream_queue:
                        try:
                            logger.debug(f"[LLM Sync Stream] Putting message delta into queue: {repr(delta.content[:20])}")
                            await stream_queue.put({
                                "event": "message",
                                "data": {
                                    "message_id": message_id,
                                    "delta": {
                                        "content": delta.content,
                                    },
                                },
                            })
                            logger.debug(f"[LLM Sync Stream] Successfully queued message delta")
                        except Exception as e:
                            logger.warning(f"Failed to send stream update: {e}")
                
                # Handle tool calls
                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        # Ensure we have enough space in accumulated_tool_calls
                        while len(accumulated_tool_calls) <= tool_call_delta.index:
                            accumulated_tool_calls.append({
                                "id": None,
                                "type": "function",
                                "function": {
                                    "name": "",
                                    "arguments": "",
                                }
                            })
                        
                        tool_call = accumulated_tool_calls[tool_call_delta.index]
                        
                        # Accumulate tool call data
                        if tool_call_delta.id:
                            tool_call["id"] = tool_call_delta.id
                        if hasattr(tool_call_delta, "function"):
                            if tool_call_delta.function.name:
                                tool_call["function"]["name"] = tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                tool_call["function"]["arguments"] += tool_call_delta.function.arguments
                
                # Handle finish reason
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                
                # Handle usage data (typically in the last chunk)
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_data = chunk.usage
        
        except Exception as e:
            logger.error(f"Error processing sync streaming response: {e}", exc_info=True)
            raise
        
        # Construct a response object compatible with non-streaming response
        # Create a mock response object that matches the structure of non-streaming response
        class MockMessage:
            def __init__(self, content, tool_calls):
                self.role = "assistant"
                self.content = content
                self.tool_calls = tool_calls if tool_calls else None
        
        class MockChoice:
            def __init__(self, message, finish_reason):
                self.message = message
                self.finish_reason = finish_reason
                self.index = 0
        
        class MockResponse:
            def __init__(self, choices, usage):
                self.choices = choices
                self.usage = usage
                self.id = str(uuid.uuid4())
                self.model = None
                self.object = "chat.completion"
        
        # Convert accumulated tool calls to proper format
        tool_calls_formatted = None
        if accumulated_tool_calls and any(tc["id"] for tc in accumulated_tool_calls):
            tool_calls_formatted = []
            for tc in accumulated_tool_calls:
                if tc["id"]:  # Only include tool calls that have an ID
                    class MockToolCall:
                        def __init__(self, id, type, function):
                            self.id = id
                            self.type = type
                            self.function = function
                    
                    class MockFunction:
                        def __init__(self, name, arguments):
                            self.name = name
                            self.arguments = arguments
                    
                    tool_calls_formatted.append(
                        MockToolCall(
                            id=tc["id"],
                            type=tc["type"],
                            function=MockFunction(
                                name=tc["function"]["name"],
                                arguments=tc["function"]["arguments"]
                            )
                        )
                    )
        
        message = MockMessage(accumulated_content, tool_calls_formatted)
        choice = MockChoice(message, finish_reason or "stop")
        response = MockResponse([choice], usage_data)
        
        return response

    async def _create_message(
        self,
        system_prompt: str,
        messages_history: List[Dict[str, Any]],
        tools_definitions,
        keep_tool_result: int = -1,
        stream_queue: Optional[Any] = None,
    ):
        """
        Send message to OpenAI API with streaming support.
        :param system_prompt: System prompt string.
        :param messages_history: Message history list.
        :param stream_queue: Optional queue for streaming updates.
        :return: OpenAI API response object or None (if error occurs).
        """
        # Create a copy for sending to LLM (to avoid modifying the original)
        messages_for_llm = [m.copy() for m in messages_history]

        # put the system prompt in the first message since OpenAI API does not support system prompt in
        if system_prompt:
            # Check if there's already a system or developer message
            if messages_for_llm and messages_for_llm[0]["role"] in [
                "system",
                "developer",
            ]:
                messages_for_llm[0] = {
                    "role": "system",
                    "content": system_prompt,
                }

            else:
                messages_for_llm.insert(
                    0,
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                )

        # Filter tool results to save tokens (only affects messages sent to LLM)
        messages_for_llm = self._remove_tool_result_from_messages(
            messages_for_llm, keep_tool_result
        )

        # Retry loop with dynamic max_tokens adjustment
        max_retries = 10
        base_wait_time = 30
        current_max_tokens = self.max_tokens

        for attempt in range(max_retries):
            params = {
                "model": self.model_name,
                "temperature": self.temperature,
                "messages": messages_for_llm,
                "tools": [],
                "stream": True,  # Enable streaming
                "top_p": self.top_p,
                "extra_body": {},
            }
            # Check if the model is GPT-5, and adjust the parameter accordingly
            if "gpt-5" in self.model_name:
                # Use 'max_completion_tokens' for GPT-5
                params["max_completion_tokens"] = current_max_tokens
            else:
                # Use 'max_tokens' for GPT-4 and other models
                params["max_tokens"] = current_max_tokens

            # Add repetition_penalty if it's not the default value
            if self.repetition_penalty != 1.0:
                params["extra_body"]["repetition_penalty"] = self.repetition_penalty

            if "deepseek-v3-1" in self.model_name:
                params["extra_body"]["thinking"] = {"type": "enabled"}

            try:
                if self.async_client:
                    stream = await self.client.chat.completions.create(**params)
                    # Process streaming response
                    response = await self._handle_streaming_response(
                        stream, stream_queue
                    )
                else:
                    # For sync client, we need to wrap in async
                    stream = self.client.chat.completions.create(**params)
                    # Convert sync stream to async by wrapping each iteration
                    response = await self._handle_sync_streaming_response(
                        stream, stream_queue
                    )
                
                # Update token count
                self._update_token_usage(getattr(response, "usage", None))
                self.task_log.log_step(
                    "info",
                    "LLM | Response Status",
                    f"{getattr(response.choices[0], 'finish_reason', 'N/A')}",
                )

                # Check if response was truncated due to length limit
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                if finish_reason == "length":
                    # If this is not the last retry, increase max_tokens and retry
                    if attempt < max_retries - 1:
                        # Increase max_tokens by 10%
                        current_max_tokens = int(current_max_tokens * 1.1)
                        self.task_log.log_step(
                            "warning",
                            "LLM | Length Limit Reached",
                            f"Response was truncated due to length limit (attempt {attempt + 1}/{max_retries}). Increasing max_tokens to {current_max_tokens} and retrying...",
                        )
                        await asyncio.sleep(base_wait_time)
                        continue
                    else:
                        # Last retry, return the truncated response instead of raising exception
                        self.task_log.log_step(
                            "warning",
                            "LLM | Length Limit Reached - Returning Truncated Response",
                            f"Response was truncated after {max_retries} attempts. Returning truncated response to allow ReAct loop to continue.",
                        )
                        # Return the truncated response and let the orchestrator handle it
                        return response, messages_history

                # Check if the last 50 characters of the response appear more than 5 times in the response content.
                # If so, treat it as a severe repeat and trigger a retry.
                if hasattr(response.choices[0], "message") and hasattr(
                    response.choices[0].message, "content"
                ):
                    resp_content = response.choices[0].message.content or ""
                else:
                    resp_content = getattr(response.choices[0], "text", "")

                if resp_content and len(resp_content) >= 50:
                    tail_50 = resp_content[-50:]
                    repeat_count = resp_content.count(tail_50)
                    if repeat_count > 5:
                        # If this is not the last retry, retry
                        if attempt < max_retries - 1:
                            self.task_log.log_step(
                                "warning",
                                "LLM | Repeat Detected",
                                f"Severe repeat: the last 50 chars appeared over 5 times (attempt {attempt + 1}/{max_retries}), retrying...",
                            )
                            await asyncio.sleep(base_wait_time)
                            continue
                        else:
                            # Last retry, return anyway
                            self.task_log.log_step(
                                "warning",
                                "LLM | Repeat Detected - Returning Anyway",
                                f"Severe repeat detected after {max_retries} attempts. Returning response anyway.",
                            )

                # Success - return the original messages_history (not the filtered copy)
                # This ensures that the complete conversation history is preserved in logs
                return response, messages_history

            except asyncio.TimeoutError as e:
                if attempt < max_retries - 1:
                    self.task_log.log_step(
                        "warning",
                        "LLM | Timeout Error",
                        f"Timeout error (attempt {attempt + 1}/{max_retries}): {str(e)}, retrying...",
                    )
                    await asyncio.sleep(base_wait_time)
                    continue
                else:
                    self.task_log.log_step(
                        "error",
                        "LLM | Timeout Error",
                        f"Timeout error after {max_retries} attempts: {str(e)}",
                    )
                    raise e
            except asyncio.CancelledError as e:
                self.task_log.log_step(
                    "error",
                    "LLM | Request Cancelled",
                    f"Request was cancelled: {str(e)}",
                )
                raise e
            except Exception as e:
                if "Error code: 401" in str(e):
                    self.task_log.log_step(
                        "error",
                        "LLM | Authentication Error",
                        f"Authentication failed: {str(e)}",
                    )
                    raise e
                elif "Error code: 400" in str(e) and "longer than the model" in str(e):
                    self.task_log.log_step(
                        "error",
                        "LLM | Context Length Error",
                        f"Error: {str(e)}",
                    )
                    raise e
                else:
                    if attempt < max_retries - 1:
                        self.task_log.log_step(
                            "warning",
                            "LLM | API Error",
                            f"Error (attempt {attempt + 1}/{max_retries}): {str(e)}, retrying...",
                        )
                        await asyncio.sleep(base_wait_time)
                        continue
                    else:
                        self.task_log.log_step(
                            "error",
                            "LLM | API Error",
                            f"Error after {max_retries} attempts: {str(e)}",
                        )
                        raise e

        # Should never reach here, but just in case
        raise Exception("Unexpected error: retry loop completed without returning")

    def process_llm_response(
        self, llm_response: Any, message_history: List[Dict], agent_type: str = "main"
    ) -> tuple[str, bool, List[Dict]]:
        """Process LLM response"""
        if not llm_response or not llm_response.choices:
            error_msg = "LLM did not return a valid response."
            self.task_log.log_step(
                "error", "LLM | Response Error", f"Error: {error_msg}"
            )
            return "", True, message_history  # Exit loop, return message_history

        # Extract LLM response text
        if llm_response.choices[0].finish_reason == "stop":
            assistant_response_text = llm_response.choices[0].message.content or ""

            message_history.append(
                {"role": "assistant", "content": assistant_response_text}
            )

        elif llm_response.choices[0].finish_reason == "length":
            assistant_response_text = llm_response.choices[0].message.content or ""
            if assistant_response_text == "":
                assistant_response_text = "LLM response is empty."
            elif "Context length exceeded" in assistant_response_text:
                # This is the case where context length is exceeded, needs special handling
                self.task_log.log_step(
                    "warning",
                    "LLM | Context Length",
                    "Detected context length exceeded, returning error status",
                )
                message_history.append(
                    {"role": "assistant", "content": assistant_response_text}
                )
                return (
                    assistant_response_text,
                    True,
                    message_history,
                )  # Return True to indicate need to exit loop

            # Add assistant response to history
            message_history.append(
                {"role": "assistant", "content": assistant_response_text}
            )

        else:
            raise ValueError(
                f"Unsupported finish reason: {llm_response.choices[0].finish_reason}"
            )

        return assistant_response_text, False, message_history

    def extract_tool_calls_info(
        self, llm_response: Any, assistant_response_text: str
    ) -> List[Dict]:
        """Extract tool call information from LLM response"""
        from ...utils.parsing_utils import parse_llm_response_for_tool_calls

        return parse_llm_response_for_tool_calls(assistant_response_text)

    def update_message_history(
        self, message_history: List[Dict], all_tool_results_content_with_id: List[Tuple]
    ) -> List[Dict]:
        """Update message history with tool calls data (llm client specific).
        
        Supports both text-only and multimodal tool results. When multimodal content
        is present (images, audio, video), constructs appropriate multimodal message format.
        """
        # Separate text and multimodal content
        text_parts = []
        multimodal_parts = []
        
        for item in all_tool_results_content_with_id:
            call_id, content = item
            if isinstance(content, dict):
                if content.get("type") == "text":
                    text_parts.append(content["text"])
                elif content.get("type") in ("image", "image_url"):
                    # Image content
                    multimodal_parts.append(content)
                elif content.get("type") == "input_audio":
                    # Audio content
                    multimodal_parts.append(content)
        
        # Build message content
        if multimodal_parts:
            # Build multimodal message
            message_content = []
            
            # Add text content first
            if text_parts:
                merged_text = "\n".join(text_parts)
                message_content.append({"type": "text", "text": merged_text})
            
            # Add multimodal content
            message_content.extend(multimodal_parts)
            
            message_history.append({
                "role": "user",
                "content": message_content
            })
        else:
            # Text-only message
            merged_text = "\n".join(text_parts)
            message_history.append({
                "role": "user",
                "content": merged_text,
            })

        return message_history

    def update_message_history_with_multimodal(
        self,
        message_history: List[Dict],
        text_content: Optional[str],
        multimodal_content: Optional[Dict]
    ) -> List[Dict]:
        """Update message history with potential multimodal content.
        
        This method is used to add user messages that may include images, audio, or video
        directly in the message content for multimodal LLM processing.
        
        Args:
            message_history: The current message history
            text_content: Text content to include in the message
            multimodal_content: Optional multimodal content dict with keys:
                - type: "image", "audio", or "video"
                - base64_data: Base64 encoded file data
                - mime_type: MIME type for images/videos
                - format: Audio format for audio files (used instead of mime_type for audio)
                
        Returns:
            Updated message history
        """
        if not multimodal_content:
            # No multimodal content, just add text
            message_history.append({
                "role": "user",
                "content": text_content or ""
            })
            return message_history
        
        # Build multimodal message
        content = []
        
        # Add text first
        if text_content:
            content.append({"type": "text", "text": text_content})
        
        # Add multimodal content based on type
        content_type = multimodal_content.get("type")
        base64_data = multimodal_content.get("base64_data")
        
        # Validate base64_data is present and not empty
        if not base64_data:
            logger.warning(f"Multimodal content of type '{content_type}' has empty or missing base64_data")
            # Fall back to text-only message if no valid multimodal data
            if content:
                message_history.append({
                    "role": "user",
                    "content": content
                })
            else:
                message_history.append({
                    "role": "user",
                    "content": text_content or ""
                })
            return message_history
        
        if content_type == "image":
            mime_type = multimodal_content.get("mime_type", "image/jpeg")
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{base64_data}"
                }
            })
        elif content_type == "audio":
            audio_format = multimodal_content.get("format", "mp3")
            content.append({
                "type": "input_audio",
                "input_audio": {
                    "data": base64_data,
                    "format": audio_format
                }
            })
        elif content_type == "video":
            mime_type = multimodal_content.get("mime_type", "video/mp4")
            # Videos are sent using image_url type with video MIME type
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{base64_data}"
                }
            })
        else:
            logger.warning(f"Unknown multimodal content type: {content_type}")
        
        # Ensure we have at least some content to add
        if not content:
            message_history.append({
                "role": "user",
                "content": text_content or ""
            })
        else:
            message_history.append({
                "role": "user",
                "content": content
            })
        
        return message_history

    def generate_agent_system_prompt(self, date: Any, mcp_servers: List[Dict]) -> str:
        return generate_mcp_system_prompt(date, mcp_servers)

    def _estimate_tokens(self, text: str) -> int:
        """Use tiktoken to estimate the number of tokens in text"""
        if not hasattr(self, "encoding"):
            # Initialize tiktoken encoder
            try:
                self.encoding = tiktoken.get_encoding("o200k_base")
            except Exception:
                # If o200k_base is not available, use cl100k_base as fallback
                self.encoding = tiktoken.get_encoding("cl100k_base")

        try:
            return len(self.encoding.encode(text))
        except Exception as e:
            # If encoding fails, use simple estimation: approximately 1 token per 4 characters
            self.task_log.log_step(
                "error",
                "LLM | Token Estimation Error",
                f"Error: {str(e)}",
            )
            return len(text) // 4

    def ensure_summary_context(
        self, message_history: list, summary_prompt: str
    ) -> tuple[bool, list]:
        """
        Check if current message_history + summary_prompt will exceed context
        If it will exceed, remove the last assistant-user pair and return False
        Return True to continue, False if messages have been rolled back
        """
        # Get token usage from the last LLM call
        last_prompt_tokens = self.last_call_tokens.get("prompt_tokens", 0)
        last_completion_tokens = self.last_call_tokens.get("completion_tokens", 0)
        buffer_factor = 1.5

        # Calculate token count for summary prompt
        summary_tokens = int(self._estimate_tokens(summary_prompt) * buffer_factor)

        # Calculate token count for the last user message in message_history
        last_user_tokens = 0
        if message_history[-1]["role"] == "user":
            content = message_history[-1]["content"]
            last_user_tokens = int(self._estimate_tokens(content) * buffer_factor)

        # Calculate total token count: last prompt + completion + last user message + summary + reserved response space
        estimated_total = (
            last_prompt_tokens
            + last_completion_tokens
            + last_user_tokens
            + summary_tokens
            + self.max_tokens
            + 1000  # Add 1000 tokens as buffer
        )

        if estimated_total >= self.max_context_length:
            self.task_log.log_step(
                "info",
                "LLM | Context Limit Reached",
                "Context limit reached, proceeding to step back and summarize the conversation",
            )

            # Remove the last user message (tool call results)
            if message_history[-1]["role"] == "user":
                message_history.pop()

            # Remove the second-to-last assistant message (tool call request)
            if message_history[-1]["role"] == "assistant":
                message_history.pop()

            self.task_log.log_step(
                "info",
                "LLM | Context Limit Reached",
                f"Removed the last assistant-user pair, current message_history length: {len(message_history)}",
            )

            return False, message_history

        self.task_log.log_step(
            "info",
            "LLM | Context Limit Not Reached",
            f"{estimated_total}/{self.max_context_length}",
        )
        return True, message_history

    def format_token_usage_summary(self) -> tuple[List[str], str]:
        """Format token usage statistics, return summary_lines for format_final_summary and log string"""
        token_usage = self.get_token_usage()

        total_input = token_usage.get("total_input_tokens", 0)
        total_output = token_usage.get("total_output_tokens", 0)
        cache_input = token_usage.get("total_cache_input_tokens", 0)

        summary_lines = []
        summary_lines.append("\n" + "-" * 20 + " Token Usage " + "-" * 20)
        summary_lines.append(f"Total Input Tokens: {total_input}")
        summary_lines.append(f"Total Cache Input Tokens: {cache_input}")
        summary_lines.append(f"Total Output Tokens: {total_output}")
        summary_lines.append("-" * (40 + len(" Token Usage ")))
        summary_lines.append("Pricing is disabled - no cost information available")
        summary_lines.append("-" * (40 + len(" Token Usage ")))

        # Generate log string
        log_string = (
            f"[{self.model_name}] Total Input: {total_input}, "
            f"Cache Input: {cache_input}, "
            f"Output: {total_output}"
        )

        return summary_lines, log_string

    def get_token_usage(self):
        return self.token_usage.copy()
