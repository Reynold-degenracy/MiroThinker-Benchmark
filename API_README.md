# MiroFlow Agent API

本文件描述 FastAPI 接口。

## 启动

**重要：** 服务器必须从仓库根目录启动。

```bash
# 从仓库根目录运行
cd /path/to/MiroThinker-Benchmark
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## 健康检查

```bash
curl http://localhost:8000/health
```

响应示例：
```json
{"status": "healthy"}
```

## 接口

### 消息格式

**非流式消息格式：**
```json
{"type": "xxx", "step": -1, "content": "xxx"}
```

**流式消息格式：**
```json
{"type": "xxx", "step": -1, "delta": "xxx"}
```

### 参数说明

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| type | string | 是 | 消息类型，分为：<br>- query：用户问题<br>- plan：规划<br>- answer：回答<br>- action：行动<br>- start：步骤开始（仅适用于流式输出）<br>- end：步骤终止（仅适用于流式输出） |
| step | int | 否 | 当前所在步数 |
| content | str | 否 | 非流式消息可用，消息文本具体内容 |
| delta | str | 否 | 流式消息可用，消息文本具体内容 |

---

## POST /v1/api/plan

**用途：** 提交问题，输出分步骤规划。

### 请求 Headers

```
Content-Type: application/json
X-Session-Id: asdfghjkl
Authorization: Bearer xxx
```

### 请求示例

```json
{
  "query": "xxx",
  "history": [
    {"type": "query", "step": -1, "content": "xxx"},
    {"type": "plan", "step": 1, "content": "xxx"},
    {"type": "plan", "step": 2, "content": "xxx"},
    {"type": "plan", "step": 3, "content": "xxx"}
  ]
}
```

### 请求参数

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| query | string | 是 | 当前用户问题或修改意见 |
| history | array | 否 | 所有历史消息 |

### 返回示例

```json
{"type": "start", "step": 1, "delta": ""}
{"type": "plan", "step": 1, "delta": "xx"}
{"type": "plan", "step": 1, "delta": "xx"}
{"type": "plan", "step": 1, "delta": "xx"}
{"type": "end", "step": 1, "delta": ""}
{"type": "start", "step": 2, "delta": ""}
{"type": "plan", "step": 2, "delta": "x"}
{"type": "plan", "step": 2, "delta": "xx"}
{"type": "plan", "step": 2, "delta": "xxx"}
{"type": "end", "step": 2, "delta": ""}
```

### 返回参数

见流式消息格式

### 使用示例

```bash
curl -X POST http://localhost:8000/v1/api/plan \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_001" \
  -H "Authorization: Bearer xxx" \
  -d '{
    "query": "What is the capital of France?",
    "history": []
  }'
```

---

## POST /v1/api/execute

**用途：** 执行规划，得到结果。

### 请求 Headers

```
Content-Type: application/json
X-Session-Id: asdfghjkl (可选)
Authorization: Bearer xxx (可选)
```

### 请求示例

```json
{
  "plan": [
    {"type": "plan", "step": 1, "content": "xxx"},
    {"type": "plan", "step": 2, "content": "xxx"},
    {"type": "plan", "step": 3, "content": "xxx"}
  ]
}
```

### 请求参数

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| plan | array | 是 | 最新的 plan |

### 返回示例

```json
{"type": "start", "step": 1, "delta": ""}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "answer", "step": 1, "delta": "xx"}
{"type": "end", "step": 1, "delta": ""}
{"type": "start", "step": 2, "delta": ""}
{"type": "answer", "step": 2, "delta": "x"}
{"type": "answer", "step": 2, "delta": "xx"}
{"type": "answer", "step": 2, "delta": "xxx"}
{"type": "end", "step": 2, "delta": ""}
```

### 返回参数

见流式消息格式

### 使用示例

```bash
curl -X POST http://localhost:8000/v1/api/execute \
  -H "Content-Type: application/json" \
  -d '{
    "plan": [
      {"type": "plan", "step": 1, "content": "Search for information"},
      {"type": "plan", "step": 2, "content": "Summarize results"}
    ]
  }'
```

---

## POST /v1/api/upload

**用途：** 上传文件到工作区。

### 请求 Headers

```
Content-Type: multipart/form-data
X-Session-Id: asdfghjkl
Authorization: Bearer xxx
```

### 请求示例

```bash
curl http://localhost:8000/v1/api/upload \
  -H "X-Session-Id: sess_001" \
  -H "Authorization: Bearer xxx" \
  -F "file=@/path/to/example.txt"
```

### 请求参数

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| file | file | 是 | 要上传的文件 |

### 返回示例

```json
{
  "data": {
    "path": "/home/user/example.txt"
  }
}
```

---

## Sandbox 说明

- 每个 `X-Session-Id` 对应一个 sandbox。
- sandbox 生命周期为 3600 秒（默认 TTL），到期后会自动创建新的 sandbox。
- 文件上传到 sandbox 的 `/home/user/` 目录下。

## 对话状态说明

- 服务端无状态，不保存对话历史。
- 客户端需自行维护历史，并在请求中通过 `history` 传入。

## 技术实现

### 架构
- **FastAPI**: Web 框架
- **Streaming**: 使用 NDJSON (Newline Delimited JSON) 格式进行流式响应
- **Session Management**: 每个 session ID 对应独立的 sandbox 和 pipeline 组件
- **Sandbox**: 使用 E2B 沙箱环境，自动管理生命周期

### 主要组件
- `SessionAwareSandboxManager`: 自动管理每个 session 的 sandbox 创建和复用
- `stream_plan_generator`: 生成规划步骤的流式响应
- `stream_execute_generator`: 生成执行结果的流式响应

### 安全特性
- 文件名验证和路径遍历防护
- Sandbox 隔离
- 自动清理临时文件

## 开发和测试

### 运行结构测试

```bash
python3 test_api_structure.py
```

### 启动服务器（开发模式）

```bash
cd /path/to/MiroThinker-Benchmark
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 测试健康检查

```bash
curl http://localhost:8000/health
```

## 依赖要求

服务器依赖 `apps/miroflow-agent` 中的以下模块：
- `src.core.pipeline`: Pipeline 组件创建和执行
- `src.logging.task_logger`: 日志记录
- Hydra 配置管理
- FastAPI 和 Uvicorn

确保在运行服务器前，已安装所有依赖：

```bash
cd apps/miroflow-agent
uv sync
```
