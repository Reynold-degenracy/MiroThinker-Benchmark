# MiroFlow Agent API v1

本文件描述 FastAPI 接口。

## 启动

```bash
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## 健康检查

```bash
curl http://localhost:8000/health
# HTTP 200 OK
# {"status": "healthy"}
```

## 消息格式

### 非流式消息格式

```json
{"type": "xxx", "step": -1, "content": "xxx"}
```

### 流式消息格式

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

## API 接口

### POST /v1/api/plan

用途：提交问题，输出分步骤规划。

#### 请求 Headers

```
Content-Type: application/json
X-Session-Id: asdfghjkl
Authorization: Bearer xxx
```

#### 请求示例

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

#### 请求参数

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| query | string | 是 | 当前用户问题或修改意见 |
| history | array | 否 | 所有历史消息 |

#### 返回示例

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

#### 返回参数（见流式消息格式）

### POST /v1/api/execute

用途：执行规划，得到结果。

#### 请求 Headers

```
Content-Type: application/json
X-Session-Id: asdfghjkl
Authorization: Bearer xxx
```

#### 请求示例

```json
{
  "plan": [
    {"type": "plan", "step": 1, "content": "xxx"},
    {"type": "plan", "step": 2, "content": "xxx"},
    {"type": "plan", "step": 3, "content": "xxx"}
  ]
}
```

#### 请求参数

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| plan | array | 是 | 最新的 plan |

#### 返回示例

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

#### 返回参数（见流式消息格式）

### POST /v1/api/upload

用途：上传文件到工作区。

#### 请求 Headers

```
Content-Type: multipart/form-data
X-Session-Id: asdfghjkl
Authorization: Bearer xxx
```

#### 请求示例

```bash
curl http://localhost:8000/v1/api/upload \
  -H "X-Session-Id: sess_001" \
  -F "file=@/path/to/example.txt"  # 客户端本地文件路径

# HTTP 200 OK
```

#### 请求参数

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| file | file | 是 | 要上传的文件（客户端本地文件） |

#### 返回示例

```json
{
  "data": {
    "path": "/home/user/example.txt"
  }
}
```

## Sandbox 说明

- 每个 X-Session-Id 对应一个 sandbox。
- sandbox 生命周期为 3600 秒（默认 TTL），到期后会自动创建新的 sandbox。

## 对话状态说明

- 服务端无状态，不保存对话历史。
- 客户端需自行维护历史，并在请求中通过 history 传入。
- history 参数将被传递给底层 pipeline，用于保持对话上下文。

## 示例

### 使用 curl 调用 /v1/api/plan

```bash
curl http://localhost:8000/v1/api/plan \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_001" \
  -d '{
    "query": "What is 2+2?",
    "history": []
  }'
```

### 使用 curl 调用 /v1/api/execute

```bash
curl http://localhost:8000/v1/api/execute \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_001" \
  -d '{
    "plan": [
      {"type": "plan", "step": 1, "content": "Calculate 2+2"},
      {"type": "plan", "step": 2, "content": "Return the result"}
    ]
  }'
```

### 使用 Python 调用 API

```python
import requests
import json

url = "http://localhost:8000/v1/api/plan"
headers = {
    "Content-Type": "application/json",
    "X-Session-Id": "sess_001"
}
data = {
    "query": "What is 2+2?",
    "history": []
}

response = requests.post(url, headers=headers, json=data, stream=True)

for line in response.iter_lines():
    if line:
        event = json.loads(line)
        print(f"[{event['type']}] step {event.get('step', -1)}: {event.get('delta', '')}")
```

## 配置

服务器使用 Hydra 配置系统。可以通过以下方式覆盖配置：

```bash
# 使用不同的 LLM 配置启动
python -m api.main llm=qwen-3 llm.base_url=http://localhost:61002/v1

# 使用多个配置覆盖
python -m api.main llm=qwen-3 llm.api_key=xxxxx llm.base_url=http://localhost:61002/v1 llm.temperature=0.7
```

## 注意事项

- 服务器需要安装 `pyproject.toml` 中的所有依赖
- 环境变量应在 `.env` 文件中配置
- 所有接口都支持流式响应
- 客户端负责维护对话历史
