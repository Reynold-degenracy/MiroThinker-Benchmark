# AgentHub SDK 集成到 MiroFlow-Agent 详细计划

## 目录
1. [项目概述](#1-项目概述)
2. [架构对比分析](#2-架构对比分析)
3. [数据结构映射](#3-数据结构映射)
4. [需要新建的文件](#4-需要新建的文件)
5. [需要修改的文件](#5-需要修改的文件)
6. [详细函数设计](#6-详细函数设计)
7. [多模态支持设计](#7-多模态支持设计)
8. [配置文件设计](#8-配置文件设计)
9. [测试计划](#9-测试计划)
10. [实现步骤](#10-实现步骤)

---

## 1. 项目概述

### 1.1 目标
将 AgentHub SDK (`/home/shinonome/AgentHub/src_py/agenthub`) 集成到 MiroFlow-Agent 中，使其可以通过 `provider=agenthub` 切换到 AgentHub 客户端，同时保持与现有 OpenAI/Anthropic 客户端的完全兼容。

### 1.2 关键要求
- ✅ 完全兼容现有的 `BaseClient` 接口
- ✅ 支持流式响应 (streaming)
- ✅ 支持工具调用 (tool calls)
- ✅ **保留 AgentHub UniMessage 的 Base64 多模态附件能力**
- ✅ 支持 token 统计
- ✅ 支持所有 AgentHub 支持的模型 (Claude, GPT, Gemini, GLM, Qwen)

---

## 2. 架构对比分析

### 2.1 MiroFlow-Agent LLM Client 架构

```
BaseClient (base_client.py)
    ├── OpenAIClient (providers/openai_client.py)
    └── AnthropicClient (providers/anthropic_client.py)

ClientFactory (factory.py)
    └── 根据 provider 字符串选择具体 client
```

**核心接口方法：**
| 方法名 | 输入 | 输出 | 用途 |
|--------|------|------|------|
| `_create_client()` | None | SDK Client 实例 | 创建底层 API 客户端 |
| `_create_message()` | system_prompt, messages_history, tools_definitions, ... | (response, message_history) | 发送请求到 LLM |
| `process_llm_response()` | llm_response, message_history, agent_type | (text, should_break, message_history) | 处理 LLM 响应 |
| `extract_tool_calls_info()` | llm_response, assistant_response_text | List[Dict] | 提取工具调用 |
| `update_message_history()` | message_history, tool_results | message_history | 更新消息历史 |
| `generate_agent_system_prompt()` | date, mcp_servers | str | 生成系统提示词 |
| `ensure_summary_context()` | message_history, summary_prompt | (bool, message_history) | 检查上下文长度 |
| `format_token_usage_summary()` | None | (List[str], str) | 格式化 token 统计 |

### 2.2 AgentHub SDK 架构

```
LLMClient (base_client.py) - 抽象基类
    ├── Claude4_5Client (claude4_5/client.py)
    ├── GPT5_2Client (gpt5_2/client.py)
    ├── Gemini3Client (gemini3/client.py)
    ├── GLM4_7Client (glm4_7/client.py)
    └── Qwen3Client (qwen3/client.py)

AutoLLMClient (auto_client.py)
    └── 根据 model 名称自动路由到具体 client
```

**核心接口方法：**
| 方法名 | 输入 | 输出 | 用途 |
|--------|------|------|------|
| `streaming_response()` | messages: List[UniMessage], config: UniConfig | AsyncIterator[UniEvent] | 流式生成响应 |
| `streaming_response_stateful()` | message: UniMessage, config: UniConfig | AsyncIterator[UniEvent] | 有状态流式响应 |
| `concat_uni_events_to_uni_message()` | events: List[UniEvent] | UniMessage | 合并事件为消息 |
| `transform_uni_config_to_model_config()` | config: UniConfig | Any | 配置转换 |
| `transform_uni_message_to_model_input()` | messages: List[UniMessage] | Any | 消息格式转换 |

---

## 3. 数据结构映射

### 3.1 消息格式映射

**MiroFlow Message History 格式：**
```python
# OpenAI 格式
[
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there!"},
    {"role": "user", "content": "Tool result..."}
]

# Anthropic 格式
[
    {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
    {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]},
]
```

**AgentHub UniMessage 格式：**
```python
{
    "role": "user" | "assistant",
    "content_items": [
        {"type": "text", "text": "Hello"},
        {"type": "image_url", "image_url": "data:image/png;base64,..."},  # 多模态
        {"type": "thinking", "thinking": "...", "signature": "..."},
        {"type": "tool_call", "name": "...", "arguments": {...}, "tool_call_id": "..."},
        {"type": "tool_result", "text": "...", "images": [...], "tool_call_id": "..."}
    ],
    "usage_metadata": {...} | None,
    "finish_reason": "stop" | "length" | None
}
```

### 3.2 配置格式映射

**MiroFlow Config (DictConfig):**
```python
cfg.llm.provider = "agenthub"
cfg.llm.model_name = "claude-sonnet-4-5-20250514"
cfg.llm.temperature = 0.7
cfg.llm.max_tokens = 16384
cfg.llm.max_context_length = 200000
cfg.llm.api_key = "..."
cfg.llm.base_url = "..."
cfg.llm.thinking_level = "medium"  # 新增：支持 thinking
```

**AgentHub UniConfig:**
```python
{
    "max_tokens": 16384,
    "temperature": 0.7,
    "tools": [...],
    "thinking_level": "medium",  # none, low, medium, high
    "tool_choice": "auto",
    "system_prompt": "...",
    "prompt_caching": "enable"
}
```

### 3.3 工具调用格式映射

**MiroFlow Tool Definition:**
```python
[
    {
        "name": "server-name",
        "tools": [
            {
                "name": "tool-name",
                "description": "...",
                "schema": {"type": "object", "properties": {...}}
            }
        ]
    }
]
```

**AgentHub ToolSchema:**
```python
{
    "name": "server-name-tool-name",
    "description": "...",
    "parameters": {"type": "object", "properties": {...}}
}
```

---

## 4. 需要新建的文件

### 4.1 `src/llm/providers/agenthub_client.py` (主文件)

**文件位置：** `/home/shinonome/MiroThinker/apps/miroflow-agent/src/llm/providers/agenthub_client.py`

**职责：**
- 继承 `BaseClient`
- 封装 AgentHub SDK 的 `AutoLLMClient`
- 实现所有必需的接口方法
- 处理 MiroFlow ↔ AgentHub 格式转换

### 4.2 `conf/llm/agenthub-claude.yaml` (配置文件)

**文件位置：** `/home/shinonome/MiroThinker/apps/miroflow-agent/conf/llm/agenthub-claude.yaml`

### 4.3 `conf/llm/agenthub-gemini.yaml` (配置文件)

**文件位置：** `/home/shinonome/MiroThinker/apps/miroflow-agent/conf/llm/agenthub-gemini.yaml`

---

## 5. 需要修改的文件

### 5.1 `src/llm/factory.py`

**修改内容：**
```python
# 添加导入
from .providers.agenthub_client import AgentHubClient

# 修改 client_creators 字典
client_creators = {
    "anthropic": lambda: AnthropicClient(...),
    "qwen": lambda: OpenAIClient(...),
    "openai": lambda: OpenAIClient(...),
    "agenthub": lambda: AgentHubClient(...),  # 新增
}
```

### 5.2 `pyproject.toml`

**修改内容：**
```toml
[project.dependencies]
# 添加 agenthub 依赖
agenthub = { path = "../../../../AgentHub/src_py", develop = true }
# 或者
# agenthub = ">=0.1.0"
```

---

## 6. 详细函数设计

### 6.1 `AgentHubClient` 类设计

```python
@dataclasses.dataclass
class AgentHubClient(BaseClient):
    """
    AgentHub SDK 适配器，兼容 MiroFlow-Agent 的 BaseClient 接口。
    
    特性：
    - 支持所有 AgentHub 支持的模型 (Claude, GPT, Gemini, GLM, Qwen)
    - 支持 Base64 编码的多模态附件
    - 支持 Thinking/Reasoning 模式
    - 支持工具调用
    """
    
    # 继承自 BaseClient 的属性
    # task_id: str
    # cfg: DictConfig
    # task_log: Optional[TaskLog]
    
    # AgentHub 特有属性
    _agenthub_client: AutoLLMClient = dataclasses.field(init=False)
    _thinking_level: str = dataclasses.field(init=False)
    _client_type: str = dataclasses.field(init=False)
```

### 6.2 核心方法详细设计

#### 6.2.1 `__post_init__(self)`
```python
def __post_init__(self):
    """初始化 AgentHub 客户端"""
    
    # 输入：self.cfg (DictConfig)
    # 输出：初始化 self._agenthub_client 和其他属性
    
    # 步骤：
    # 1. 调用父类 __post_init__
    # 2. 读取 thinking_level 配置（默认 "none"）
    # 3. 读取 client_type 配置（可选，用于强制指定客户端类型）
    # 4. 初始化 token 使用统计
    # 5. 初始化 last_call_tokens
```

#### 6.2.2 `_create_client(self) -> AutoLLMClient`
```python
def _create_client(self) -> AutoLLMClient:
    """
    创建 AgentHub AutoLLMClient 实例
    
    输入：
        - self.model_name: str  (如 "claude-sonnet-4-5-20250514")
        - self.api_key: Optional[str]
        - self.base_url: Optional[str]
        - self._client_type: Optional[str]  (强制指定客户端类型)
    
    输出：
        - AutoLLMClient 实例
    
    实现：
        from agenthub import AutoLLMClient
        return AutoLLMClient(
            model=self.model_name,
            api_key=self.api_key,
            base_url=self.base_url,
            client_type=self._client_type
        )
    """
```

#### 6.2.3 `_convert_miroflow_messages_to_uni_messages(self, messages: List[Dict]) -> List[UniMessage]`
```python
def _convert_miroflow_messages_to_uni_messages(
    self, 
    messages: List[Dict]
) -> List[UniMessage]:
    """
    将 MiroFlow 消息格式转换为 AgentHub UniMessage 格式
    
    输入：
        messages: List[Dict]
        - OpenAI 格式: {"role": "user", "content": "text"}
        - Anthropic 格式: {"role": "user", "content": [{"type": "text", "text": "..."}]}
    
    输出：
        List[UniMessage]
        - {"role": "user", "content_items": [{"type": "text", "text": "..."}]}
    
    关键处理：
        1. 检测 content 是 str 还是 List
        2. 如果是 str，转换为 [{"type": "text", "text": content}]
        3. 如果是 List，遍历并转换每个 item：
           - {"type": "text", "text": "..."} -> 保持不变
           - {"type": "tool_use", "id": ..., "name": ..., "input": ...} 
             -> {"type": "tool_call", "tool_call_id": id, "name": name, "arguments": input}
           - {"type": "tool_result", "tool_use_id": ..., "content": ...}
             -> {"type": "tool_result", "tool_call_id": tool_use_id, "text": content}
        4. 处理多模态内容（如果有）
    """
```

#### 6.2.4 `_convert_uni_message_to_miroflow_format(self, uni_message: UniMessage) -> Dict`
```python
def _convert_uni_message_to_miroflow_format(
    self, 
    uni_message: UniMessage
) -> Dict:
    """
    将 AgentHub UniMessage 转换为 MiroFlow 消息格式
    
    输入：
        uni_message: UniMessage
        - {"role": "assistant", "content_items": [...], "usage_metadata": {...}}
    
    输出：
        Dict (MiroFlow 格式)
        - {"role": "assistant", "content": "text..."} 或
        - {"role": "assistant", "content": [{"type": "text", "text": "..."}]}
    
    关键处理：
        1. 提取所有 text 类型的 content_items，合并为字符串
        2. 处理 tool_call 类型，转换为 MiroFlow 工具调用格式
        3. 处理 thinking 类型（如果需要保留）
    """
```

#### 6.2.5 `_convert_miroflow_tools_to_uni_tools(self, tools_definitions: List[Dict]) -> List[ToolSchema]`
```python
def _convert_miroflow_tools_to_uni_tools(
    self, 
    tools_definitions: List[Dict]
) -> List[ToolSchema]:
    """
    将 MiroFlow 工具定义转换为 AgentHub ToolSchema 格式
    
    输入：
        tools_definitions: List[Dict]
        - [{"name": "server", "tools": [{"name": "tool", "description": "...", "schema": {...}}]}]
    
    输出：
        List[ToolSchema]
        - [{"name": "server-tool", "description": "...", "parameters": {...}}]
    
    实现：
        uni_tools = []
        for server in tools_definitions:
            for tool in server.get("tools", []):
                uni_tools.append({
                    "name": f"{server['name']}-{tool['name']}",
                    "description": tool["description"],
                    "parameters": tool["schema"]
                })
        return uni_tools
    """
```

#### 6.2.6 `_build_uni_config(self, system_prompt: str, tools_definitions: List[Dict]) -> UniConfig`
```python
def _build_uni_config(
    self, 
    system_prompt: str, 
    tools_definitions: List[Dict]
) -> UniConfig:
    """
    构建 AgentHub UniConfig
    
    输入：
        system_prompt: str
        tools_definitions: List[Dict]
    
    输出：
        UniConfig
        - {
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system_prompt": system_prompt,
            "tools": self._convert_miroflow_tools_to_uni_tools(tools_definitions),
            "thinking_level": self._thinking_level,
            "tool_choice": "auto"
          }
    """
```

#### 6.2.7 `async def _create_message(self, ...) -> Tuple[Any, List[Dict]]`
```python
async def _create_message(
    self,
    system_prompt: str,
    messages_history: List[Dict[str, Any]],
    tools_definitions: List[Dict],
    keep_tool_result: int = -1,
    stream_queue: Optional[Any] = None,
) -> Tuple[Any, List[Dict]]:
    """
    发送消息到 LLM 并获取响应（核心方法）
    
    输入：
        system_prompt: str - 系统提示词
        messages_history: List[Dict] - MiroFlow 格式的消息历史
        tools_definitions: List[Dict] - MiroFlow 格式的工具定义
        keep_tool_result: int - 保留多少条工具结果 (-1 = 全部)
        stream_queue: Optional[Any] - 流式输出队列
    
    输出：
        Tuple[response_object, updated_message_history]
        - response_object: 模拟的响应对象，包含 content 和 usage
        - updated_message_history: 更新后的消息历史
    
    实现步骤：
        1. 过滤工具结果（如果 keep_tool_result != -1）
           messages_for_llm = self._remove_tool_result_from_messages(messages_history, keep_tool_result)
        
        2. 转换消息格式
           uni_messages = self._convert_miroflow_messages_to_uni_messages(messages_for_llm)
        
        3. 构建配置
           uni_config = self._build_uni_config(system_prompt, tools_definitions)
        
        4. 调用 AgentHub 流式 API
           events = []
           async for event in self._agenthub_client.streaming_response(uni_messages, uni_config):
               events.append(event)
               # 如果有 stream_queue，发送流式更新
               if stream_queue:
                   await self._send_stream_update(stream_queue, event)
        
        5. 合并事件为完整消息
           uni_message = self._agenthub_client.concat_uni_events_to_uni_message(events)
        
        6. 更新 token 统计
           self._update_token_usage(uni_message.get("usage_metadata"))
        
        7. 构建模拟响应对象（兼容现有 process_llm_response）
           response = self._build_mock_response(uni_message)
        
        8. 返回
           return response, messages_history
    """
```

#### 6.2.8 `def process_llm_response(self, ...) -> Tuple[str, bool, List[Dict]]`
```python
def process_llm_response(
    self, 
    llm_response: Any, 
    message_history: List[Dict], 
    agent_type: str = "main"
) -> Tuple[str, bool, List[Dict]]:
    """
    处理 LLM 响应
    
    输入：
        llm_response: Any - _create_message 返回的模拟响应对象
        message_history: List[Dict] - 消息历史
        agent_type: str - agent 类型
    
    输出：
        Tuple[assistant_response_text, should_break, updated_message_history]
        - assistant_response_text: str - 提取的文本响应
        - should_break: bool - 是否应该中断循环
        - updated_message_history: List[Dict] - 更新后的消息历史
    
    实现：
        1. 检查响应有效性
           if not llm_response or not llm_response.content_items:
               return "", True, message_history
        
        2. 提取文本内容
           assistant_response_text = ""
           for item in llm_response.content_items:
               if item["type"] == "text":
                   assistant_response_text += item["text"]
               elif item["type"] == "thinking":
                   # 可选：是否包含 thinking 内容
                   pass
        
        3. 更新消息历史
           message_history.append({
               "role": "assistant",
               "content": assistant_response_text
           })
        
        4. 检查是否应该中断
           should_break = llm_response.finish_reason == "length"
        
        5. 返回
           return assistant_response_text, should_break, message_history
    """
```

#### 6.2.9 `def extract_tool_calls_info(self, ...) -> List[Dict]`
```python
def extract_tool_calls_info(
    self, 
    llm_response: Any, 
    assistant_response_text: str
) -> List[Dict]:
    """
    从 LLM 响应中提取工具调用信息
    
    输入：
        llm_response: Any - 响应对象
        assistant_response_text: str - 响应文本
    
    输出：
        List[Dict] - 工具调用列表
        - [{"server_name": "...", "tool_name": "...", "arguments": {...}, "id": "..."}]
    
    实现：
        1. 优先从 llm_response.content_items 中提取 tool_call 类型
           tool_calls = []
           for item in llm_response.content_items:
               if item["type"] == "tool_call":
                   # 分割 name 为 server_name 和 tool_name
                   server_name, tool_name = item["name"].rsplit("-", 1)
                   tool_calls.append({
                       "server_name": server_name,
                       "tool_name": tool_name,
                       "arguments": item["arguments"],
                       "id": item["tool_call_id"]
                   })
        
        2. 如果没有找到，回退到解析 MCP 标签
           if not tool_calls:
               from ...utils.parsing_utils import parse_llm_response_for_tool_calls
               tool_calls = parse_llm_response_for_tool_calls(assistant_response_text)
        
        3. 返回
           return tool_calls
    """
```

#### 6.2.10 `def update_message_history(self, ...) -> List[Dict]`
```python
def update_message_history(
    self, 
    message_history: List[Dict], 
    all_tool_results_content_with_id: List[Tuple]
) -> List[Dict]:
    """
    使用工具调用结果更新消息历史
    
    输入：
        message_history: List[Dict] - 当前消息历史
        all_tool_results_content_with_id: List[Tuple] - 工具结果
            - [(call_id, {"type": "text", "text": "..."}), ...]
    
    输出：
        List[Dict] - 更新后的消息历史
    
    实现：
        # 合并所有工具结果文本
        merged_text = "\n".join([
            item[1]["text"]
            for item in all_tool_results_content_with_id
            if item[1]["type"] == "text"
        ])
        
        # 添加到消息历史
        message_history.append({
            "role": "user",
            "content": merged_text
        })
        
        return message_history
    """
```

#### 6.2.11 `def generate_agent_system_prompt(self, ...) -> str`
```python
def generate_agent_system_prompt(
    self, 
    date: Any, 
    mcp_servers: List[Dict]
) -> str:
    """
    生成 Agent 系统提示词
    
    输入：
        date: datetime.date - 当前日期
        mcp_servers: List[Dict] - MCP 服务器工具定义
    
    输出：
        str - 系统提示词
    
    实现：
        # 复用现有的 MCP 提示词生成
        from ...utils.prompt_utils import generate_mcp_system_prompt
        return generate_mcp_system_prompt(date, mcp_servers)
    """
```

#### 6.2.12 `def _update_token_usage(self, usage_data: UsageMetadata) -> None`
```python
def _update_token_usage(self, usage_data: Optional[UsageMetadata]) -> None:
    """
    更新 token 使用统计
    
    输入：
        usage_data: UsageMetadata | None
        - {"prompt_tokens": int, "thoughts_tokens": int, "response_tokens": int, "cached_tokens": int}
    
    输出：
        None (更新 self.token_usage 和 self.last_call_tokens)
    
    实现：
        if usage_data:
            input_tokens = usage_data.get("prompt_tokens", 0) or 0
            output_tokens = usage_data.get("response_tokens", 0) or 0
            cached_tokens = usage_data.get("cached_tokens", 0) or 0
            
            self.last_call_tokens = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
            }
            
            self.token_usage["total_input_tokens"] += input_tokens
            self.token_usage["total_output_tokens"] += output_tokens
            self.token_usage["total_cache_read_input_tokens"] += cached_tokens
            
            self.task_log.log_step(
                "info",
                "LLM | Token Usage",
                f"Input: {input_tokens}, Output: {output_tokens}, Cached: {cached_tokens}"
            )
    """
```

#### 6.2.13 `def ensure_summary_context(self, ...) -> Tuple[bool, List[Dict]]`
```python
def ensure_summary_context(
    self, 
    message_history: List[Dict], 
    summary_prompt: str
) -> Tuple[bool, List[Dict]]:
    """
    检查上下文长度是否会超出限制
    
    输入：
        message_history: List[Dict] - 消息历史
        summary_prompt: str - 总结提示词
    
    输出：
        Tuple[pass_check, updated_message_history]
        - pass_check: bool - True=可以继续, False=需要总结
        - updated_message_history: List[Dict] - 可能被回滚的消息历史
    
    实现：
        # 复用 OpenAI/Anthropic 的实现逻辑
        # 使用 tiktoken 估算 token 数量
        # 比较 estimated_total vs max_context_length
    """
```

#### 6.2.14 `def format_token_usage_summary(self) -> Tuple[List[str], str]`
```python
def format_token_usage_summary(self) -> Tuple[List[str], str]:
    """
    格式化 token 使用统计
    
    输入：
        None
    
    输出：
        Tuple[summary_lines, log_string]
        - summary_lines: List[str] - 用于显示的摘要行
        - log_string: str - 用于日志的字符串
    """
```

### 6.3 辅助方法

#### 6.3.1 `def _build_mock_response(self, uni_message: UniMessage) -> MockResponse`
```python
def _build_mock_response(self, uni_message: UniMessage) -> "MockResponse":
    """
    构建模拟响应对象，兼容现有的 process_llm_response 接口
    
    输入：
        uni_message: UniMessage
    
    输出：
        MockResponse 对象，包含：
        - content_items: List[ContentItem]
        - usage_metadata: UsageMetadata
        - finish_reason: FinishReason
    """
```

#### 6.3.2 `async def _send_stream_update(self, stream_queue, event: UniEvent)`
```python
async def _send_stream_update(
    self, 
    stream_queue: Any, 
    event: UniEvent
) -> None:
    """
    发送流式更新到队列
    
    输入：
        stream_queue: asyncio.Queue - 流式输出队列
        event: UniEvent - AgentHub 事件
    
    输出：
        None
    
    实现：
        for item in event["content_items"]:
            if item["type"] == "text" and item["text"]:
                await stream_queue.put({
                    "type": "message_delta",
                    "delta": item["text"]
                })
    """
```

---

## 7. 多模态支持设计

### 7.1 保留 AgentHub 多模态能力

AgentHub UniMessage 支持以下多模态内容类型：

```python
# 图像（Base64 编码）
{
    "type": "image_url",
    "image_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA..."
}

# 工具结果中的图像
{
    "type": "tool_result",
    "text": "Screenshot captured",
    "images": ["data:image/png;base64,..."],  # 支持多张图片
    "tool_call_id": "call_123"
}
```

### 7.2 多模态转换函数

#### 7.2.1 `def _convert_multimodal_content(self, content: Any) -> List[ContentItem]`
```python
def _convert_multimodal_content(self, content: Any) -> List[ContentItem]:
    """
    转换可能包含多模态内容的消息
    
    输入：
        content: str | List[Dict]
        - 纯文本: "Hello"
        - 多模态列表: [{"type": "text", "text": "..."}, {"type": "image_url", "url": "..."}]
    
    输出：
        List[ContentItem] - AgentHub 格式的内容列表
    
    支持的转换：
        1. 纯文本 -> [{"type": "text", "text": content}]
        2. OpenAI 图像格式:
           {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
           -> {"type": "image_url", "image_url": "data:image/png;base64,..."}
        3. Base64 图像字符串:
           "data:image/png;base64,..."
           -> {"type": "image_url", "image_url": "data:image/png;base64,..."}
    """
```

### 7.3 在 `input_handler.py` 中的集成点

当前 MiroFlow 将多模态内容转换为纯文本描述。为了利用 AgentHub 的原生多模态能力，可以：

**方案 A（保守）：** 保持现有行为，多模态内容仍然转换为文本描述

**方案 B（增强）：** 添加配置选项，允许直接传递 Base64 图像

```yaml
# conf/llm/agenthub-claude.yaml
provider: "agenthub"
model_name: "claude-sonnet-4-5-20250514"
pass_multimodal_directly: true  # 新增配置项
```

---

## 8. 配置文件设计

### 8.1 `conf/llm/agenthub-claude.yaml`
```yaml
# AgentHub Claude 配置
defaults:
  - default
  - _self_

provider: "agenthub"
model_name: "claude-sonnet-4-5-20250514"
client_type: "claude-4-5"  # 可选，强制指定客户端类型
api_key: ${oc.env:ANTHROPIC_API_KEY}
base_url: null  # 使用默认
max_context_length: 200000
max_tokens: 16384
temperature: 1.0  # Claude thinking 模式需要 temperature=1.0
thinking_level: "medium"  # none, low, medium, high
prompt_caching: "enable"  # enable, disable, enhance
```

### 8.2 `conf/llm/agenthub-gemini.yaml`
```yaml
# AgentHub Gemini 配置
defaults:
  - default
  - _self_

provider: "agenthub"
model_name: "gemini-3-flash-preview"
client_type: "gemini-3"
api_key: ${oc.env:GOOGLE_API_KEY}
base_url: null
max_context_length: 1000000
max_tokens: 32768
temperature: 0.7
thinking_level: "low"
```

### 8.3 `conf/llm/agenthub-gpt.yaml`
```yaml
# AgentHub GPT 配置
defaults:
  - default
  - _self_

provider: "agenthub"
model_name: "gpt-5.2"
client_type: "gpt-5.2"
api_key: ${oc.env:OPENAI_API_KEY}
base_url: null
max_context_length: 128000
max_tokens: 16384
temperature: 0.7
thinking_level: "none"
```

---

## 9. 测试计划

### 9.1 单元测试

**文件：** `tests/test_agenthub_client.py`

```python
# 测试用例列表
class TestAgentHubClient:
    def test_create_client(self):
        """测试客户端创建"""
        pass
    
    def test_convert_messages_simple(self):
        """测试简单消息转换"""
        pass
    
    def test_convert_messages_with_tools(self):
        """测试带工具调用的消息转换"""
        pass
    
    def test_convert_messages_multimodal(self):
        """测试多模态消息转换"""
        pass
    
    def test_convert_tools_definition(self):
        """测试工具定义转换"""
        pass
    
    def test_build_uni_config(self):
        """测试配置构建"""
        pass
    
    def test_extract_tool_calls(self):
        """测试工具调用提取"""
        pass
    
    async def test_create_message_streaming(self):
        """测试流式消息创建"""
        pass
    
    def test_token_usage_tracking(self):
        """测试 token 统计"""
        pass
```

### 9.2 集成测试

```python
# 测试与 Orchestrator 的集成
async def test_agenthub_with_orchestrator():
    """测试 AgentHub 客户端与 Orchestrator 的完整集成"""
    pass

async def test_agenthub_tool_execution():
    """测试工具执行流程"""
    pass

async def test_agenthub_multimodal_input():
    """测试多模态输入处理"""
    pass
```

---

## 10. 实现步骤

### 阶段 1：基础架构 (1-2 天)

1. [ ] 安装 AgentHub SDK 依赖
2. [ ] 创建 `agenthub_client.py` 基础框架
3. [ ] 实现 `_create_client()` 方法
4. [ ] 实现消息格式转换函数
5. [ ] 修改 `factory.py` 添加 agenthub provider

### 阶段 2：核心功能 (2-3 天)

6. [ ] 实现 `_create_message()` 方法
7. [ ] 实现 `process_llm_response()` 方法
8. [ ] 实现 `extract_tool_calls_info()` 方法
9. [ ] 实现 `update_message_history()` 方法
10. [ ] 实现流式输出支持

### 阶段 3：完善功能 (1-2 天)

11. [ ] 实现 token 统计
12. [ ] 实现 `ensure_summary_context()` 方法
13. [ ] 实现 `format_token_usage_summary()` 方法
14. [ ] 创建配置文件

### 阶段 4：测试与优化 (1-2 天)

15. [ ] 编写单元测试
16. [ ] 编写集成测试
17. [ ] 测试多模态支持
18. [ ] 性能优化
19. [ ] 文档完善

---

## 附录 A：文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/llm/providers/agenthub_client.py` | 新建 | AgentHub 客户端实现 |
| `src/llm/providers/__init__.py` | 修改 | 添加 AgentHubClient 导出 |
| `src/llm/factory.py` | 修改 | 添加 agenthub provider |
| `conf/llm/agenthub-claude.yaml` | 新建 | Claude 配置 |
| `conf/llm/agenthub-gemini.yaml` | 新建 | Gemini 配置 |
| `conf/llm/agenthub-gpt.yaml` | 新建 | GPT 配置 |
| `pyproject.toml` | 修改 | 添加 agenthub 依赖 |
| `tests/test_agenthub_client.py` | 新建 | 测试文件 |

---

## 附录 B：依赖关系图

```
MiroFlow-Agent
    │
    ├── src/llm/
    │   ├── base_client.py (BaseClient 抽象类)
    │   ├── factory.py (ClientFactory)
    │   └── providers/
    │       ├── openai_client.py
    │       ├── anthropic_client.py
    │       └── agenthub_client.py  ←── 新增
    │           │
    │           └── 依赖: AgentHub SDK
    │               ├── AutoLLMClient
    │               ├── UniMessage
    │               ├── UniEvent
    │               ├── UniConfig
    │               └── ToolSchema
    │
    └── src/core/orchestrator.py
        └── 调用 llm_client 的各种方法
```

---

## 附录 C：类型定义汇总

```python
# MiroFlow 类型
TokenUsage = TypedDict("TokenUsage", {
    "total_input_tokens": int,
    "total_output_tokens": int,
    "total_cache_read_input_tokens": int,
    "total_cache_write_input_tokens": int,
})

# AgentHub 类型 (从 agenthub.types 导入)
from agenthub.types import (
    UniMessage,
    UniEvent,
    UniConfig,
    ToolSchema,
    ContentItem,
    UsageMetadata,
    ThinkingLevel,
    FinishReason,
)
```

---

## 附录 D：错误处理策略

| 错误类型 | 处理方式 |
|---------|---------|
| API 密钥无效 | 抛出明确的认证错误 |
| 模型不支持 | 回退到默认模型或抛出错误 |
| 网络超时 | 使用现有的 `@with_timeout` 装饰器 |
| Token 超限 | 触发 `ensure_summary_context` 逻辑 |
| 工具调用解析失败 | 回退到 MCP 标签解析 |
| 流式连接中断 | 重试或返回部分结果 |
