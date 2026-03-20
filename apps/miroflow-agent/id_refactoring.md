# ID Refactoring Notes for `miroflow-agent`

## 当前实现状态（2026-03）

目前代码已经完成到以下状态：

- `X-Session-Id` 在 API 层显式对应 `api_session_id`
- 每次 `/v1/api/execute` 会生成内部 `run_id`
- provider 上游 header 使用 `api_session_id`
- AgentHub conversation key 使用 `api_session_id`
- AgentHub trace id 使用 `run_id`
- 运行态 `task_id` 已逐步收敛为 `run_id`

为了兼容旧日志/旧调用方，当前仍保留少量兼容层：

- `BaseClient.task_id -> run_id` 的别名
- `TaskLog.task_id -> run_id` 的别名
- `TaskLog` 导出的 JSON 同时包含 `run_id` 和 `task_id`

这些兼容字段是有意保留的，不再代表新的主语义。

## 背景

在排查“同一个会话的不同轮次实际落到不同沙箱”问题时，最初直觉是怀疑 `api_server.py` 里的 session/sandbox 绑定逻辑有问题，例如 `_sessions` 是否没有真正按 `X-Session-Id` 复用。

但沿着 `session_id -> sandbox_id -> task_id -> llm client -> provider/agenthub` 这条链往下看后，发现 **API 层的 session/sandbox 绑定基本是对的，真正的问题出在 ID 语义混用**。

---

## 一、定位到的核心 Bug

### 1. API 层本地 session/sandbox 绑定本身没有明显问题

在 `apps/miroflow-agent/api_server.py` 中：

- `X-Session-Id` 被用于 `_get_or_create_session(session_id=...)`
- `_sessions[session_id]` 按这个 key 缓存 session
- `session["sandbox_id"]` 也挂在这个 session 下
- `SessionAwareSandboxManager` 负责为该 session 自动创建/复用 sandbox

也就是说，**本地 API server 这一层的“session -> sandbox”关系是按 `X-Session-Id` 绑定的**。

### 2. 真正的问题发生在 LLM / AgentHub 下游层

在 `apps/miroflow-agent/src/core/pipeline.py` 中，存在这样的逻辑：

```python
random_uuid = str(uuid.uuid4())
unique_id = f"{task_id}-{random_uuid}"
llm_client = ClientFactory(task_id=unique_id, cfg=cfg, task_log=task_log)
```

这里的 `unique_id` 实际上是：

- 每次 `/execute` 都会变化
- 本质上是 **run 级唯一 ID**

但是它被继续以 `task_id` 的名字传给 LLM client。

### 3. 这个动态 `task_id/unique_id` 被下游错误当成 session identity 使用

#### OpenAI / Anthropic client

在：

- `apps/miroflow-agent/src/llm/providers/openai_client.py`
- `apps/miroflow-agent/src/llm/providers/anthropic_client.py`

都有类似逻辑：

```python
http_client_args = {"headers": {"x-upstream-session-id": self.task_id}}
```

这里 `self.task_id` 实际已经是 `unique_id`，也就是 **每次请求都会变化的 run id**，却被用作：

- `x-upstream-session-id`

这会导致：

- API 层是稳定 session
- 但下游收到的是每轮变化的“session id”

#### AgentHub client

在 `apps/miroflow-agent/src/llm/providers/agenthub_client.py`：

- `_conversation_key()` 使用 `self.task_id` 作为 stateful conversation 的根
- `_build_trace_id()` 也使用 `self.task_id`

这意味着：

- **conversation root** 和 **trace root** 共用了同一个 id
- 而这个 id 实际上是 run 级别的、动态变化的

结果就是：

- 同一个 `X-Session-Id` 的不同轮次
- 到 AgentHub 层变成了不同 conversation
- 如果下游状态、缓存或工具环境绑定到这个 conversation/session 上
- 就会表现成“不同轮次跑到不同沙箱/不同状态里”

---

## 二、问题本质

这不是简单的“某个 session_id 写错了”，而是 **一个字段承担了多种语义**。

当前至少混在一起的概念有：

1. API 会话身份（应跨轮次稳定）
2. sandbox 绑定身份（应从 API 会话派生）
3. 一次 `/execute` 的运行身份（每次请求唯一）
4. 单次 LLM 调用的 trace 身份（更细粒度）
5. sub-agent 内部局部执行身份

但代码里主要靠：

- `session_id`
- `task_id`

这两个名字在勉强承载，导致 `task_id` 在不同层反复变义：

- API/pipeline 层像“逻辑任务 id”
- pipeline 下传时变成“run id”
- provider 层又把它当“upstream session id”
- AgentHub 又同时把它当“conversation root + trace root”

**根因就是 ID 语义设计没有拆清楚。**

---

## 三、现状梳理：当前各个 ID 的名字与实际含义

### 1. `X-Session-Id`

- 来源：客户端 header
- 当前实际意义：API 层会话 ID
- 正确直觉：应是跨轮次稳定的 conversation/session identity

### 2. `_sessions` key 中的 `session_id`

- 位置：`api_server.py`
- 当前实际意义：进程内 session registry 主键
- 实际等价于：`X-Session-Id`

### 3. `session["sandbox_id"]`

- 位置：`api_server.py`, `SessionAwareSandboxManager`
- 当前实际意义：底层 E2B/python sandbox 实例 ID
- 这部分语义基本正确

### 4. `task_id`（API 场景）

在 `api_server.py` 中：

```python
task_id = f"api_{session_id}"
```

- 当前实际意义：像是从 session 派生出来的逻辑任务 ID
- 但后续又被再加工，语义不稳定

### 5. `unique_id = f"{task_id}-{random_uuid}"`

- 位置：`pipeline.py`
- 当前实际意义：**run 级唯一 ID**
- 实际上更应该叫 `run_id`
- 但它被继续命名为 `task_id` 传给 LLM client

### 6. `BaseClient.task_id`

- 来源：上面的 `unique_id`
- 当前实际意义：run id
- 但被错误地继续用成 session id / conversation root / trace root

### 7. `x-upstream-session-id`

- 位置：OpenAI/Anthropic provider
- 名字上应该表示稳定 session id
- 现在实际传的是动态 run id

### 8. AgentHub `_conversation_key()`

- 当前应该代表 stateful conversation key
- 现在实际用 run id 做 root，导致不同轮次不连续

### 9. AgentHub `_build_trace_id()`

- 当前应代表单次调用 trace id
- 使用 run id 反而是合理的
- 问题在于它和 conversation root 共用了同一个根字段

### 10. `current_sub_agent_session_id`

- 位置：`task_logger.py`
- 当前实际意义：sub-agent 局部执行/局部会话 id
- 不是 API session，也不是 sandbox session
- 名字里带 `session` 但容易误导

---

## 四、设计目标

重新设计一套 **无歧义、符合直觉** 的 ID 体系，使不同层的 ID 只承担一种职责。

设计原则：

- **稳定会话相关** 的地方，只用稳定 session id
- **一次运行相关** 的地方，只用 run id
- **一次模型调用追踪** 的地方，只用 trace id
- 不再让同一个字段同时承担 session root / run root / trace root

---

## 五、建议的新 ID 体系

### 1. `api_session_id`

#### 含义
客户端会话 ID，来自 `X-Session-Id`

#### 特性
- 跨轮次稳定
- 是会话身份，而不是运行身份

#### 用途
- `_sessions[api_session_id]`
- session -> sandbox 绑定根键
- upstream session header
- AgentHub conversation key root

---

### 2. `sandbox_id`

#### 含义
底层 E2B/python 沙箱实例 ID

#### 特性
- 可以失效、重建
- 但总是挂在某个 `api_session_id` 下面

#### 用途
- tool-python 执行环境

---

### 3. `run_id`

#### 含义
一次 `/execute` 请求对应的唯一运行 ID

#### 特性
- 每次请求唯一
- 不跨轮次复用

#### 用途
- TaskLog 主键（至少在过渡期如此）
- 本次执行的 debug log / request trace root
- provider 级 trace root

#### 示例

```text
api_run_<api_session_id>_<uuid>
```

---

### 4. `trace_id`

#### 含义
单次 LLM 调用的细粒度 trace ID

#### 特性
- 比 `run_id` 更细
- 一个 run 内会有多个 trace_id

#### 用途
- AgentHub trace
- 单次调用调试链路

#### 示例

```text
{run_id}/{agent_type}/turn_{turn}_attempt_{attempt}
```

---

### 5. `subagent_run_id`

#### 含义
当前 run 内 sub-agent 的局部执行身份

#### 用途
- sub-agent 内部区分
- task log / agenthub 子会话局部区分

#### 备注
建议未来用它替代 `current_sub_agent_session_id`

---

## 六、推荐的语义映射

| 新名字 | 含义 | 是否跨轮次稳定 | 作用范围 |
|---|---|---:|---|
| `api_session_id` | 客户端会话 id | 是 | API / session / sandbox / upstream conversation |
| `sandbox_id` | 底层沙箱实例 id | 否（可重建） | tool-python / E2B |
| `run_id` | 一次 execute 的唯一运行 id | 否 | pipeline / logs / request debug |
| `trace_id` | 单次 LLM 调用 trace id | 否 | provider / tracing |
| `subagent_run_id` | 当前 run 内 sub-agent 局部 id | 否 | orchestrator / logs |

---

## 七、方案分层：修复方案与设计方案

我们讨论过两类方案：

### 方案 A：最小补丁

只新增一个稳定字段，例如 `upstream_session_id`：

- run id 仍然保留给 `task_id`
- 但 provider / AgentHub conversation 改用稳定的 session id

#### 优点
- 改动小
- 能快速止血

#### 缺点
- `task_id` 继续多义
- 长期仍容易误用

---

### 方案 B：一次性理顺 ID 体系（推荐）

显式拆成：

- `api_session_id`
- `run_id`
- `trace_id`
- `subagent_run_id`

#### 优点
- 语义清晰
- 符合直觉
- 后续不容易再踩“run 当 session”这类坑

#### 缺点
- 改动范围稍大

**最终推荐采用方案 B。**

---

## 八、具体重构方案（映射到当前代码库）

以下是建议的目标态改动。

---

### A. `apps/miroflow-agent/api_server.py`

#### 目标职责
- 只负责 API session/sandbox 管理
- 显式生成 `run_id`
- 不再把 session 派生 ID 混叫为 `task_id`

#### 建议改动

##### 1. `_get_or_create_session(...)`

当前：

```python
async def _get_or_create_session(session_id: str, ...)
```

建议：

```python
async def _get_or_create_session(api_session_id: str, ...)
```

内部对应改成：

- `_sessions.get(api_session_id)`
- `_sessions[api_session_id] = session`

##### 2. `stream_generator(...)`

当前参数：

```python
stream_generator(..., session_id, ...)
```

建议改为：

```python
stream_generator(..., api_session_id, ...)
```

并在函数中：

- 用 `api_session_id` 获取 session
- 生成一个新的 `run_id`
- 不再使用：

```python
task_id = f"api_{session_id}"
```

建议改成：

```python
run_id = f"api_run_{api_session_id}_{uuid.uuid4().hex}"
```

然后传给 pipeline：

```python
execute_task_pipeline(..., api_session_id=api_session_id, run_id=run_id, ...)
```

##### 3. `/v1/api/execute`

虽然 header 还是：

```python
X-Session-Id
```

但函数内部建议尽快转换变量名：

```python
api_session_id = x_session_id
```

##### 4. `/v1/api/upload`

同样建议内部统一命名为 `api_session_id`。

> upload 不需要 `run_id`，因为它不是推理执行，只是 session 级文件操作。

---

### B. `apps/miroflow-agent/src/core/pipeline.py`

#### 目标职责
- 显式接收 `api_session_id + run_id`
- 不再自己把 task_id 再包装成新的 `unique_id`

#### 建议改动

##### 1. `execute_task_pipeline(...)` 签名

当前：

```python
execute_task_pipeline(cfg, task_id, task_description, ...)
```

建议目标态：

```python
execute_task_pipeline(
    cfg,
    api_session_id: str,
    run_id: str,
    task_description: str,
    task_file_name: str,
    ...
)
```

##### 2. `TaskLog(...)`

当前：

```python
task_log = TaskLog(task_id=task_id, ...)
```

建议在过渡期先保留字段名不变，但存 `run_id`：

```python
task_log = TaskLog(
    task_id=run_id,
    input={
        "task_description": task_description,
        "task_file_name": task_file_name,
        "api_session_id": api_session_id,
    },
    ...
)
```

##### 3. 删除 `unique_id` 逻辑

当前：

```python
random_uuid = str(uuid.uuid4())
unique_id = f"{task_id}-{random_uuid}"
llm_client = ClientFactory(task_id=unique_id, ...)
```

建议：

```python
llm_client = ClientFactory(
    run_id=run_id,
    api_session_id=api_session_id,
    cfg=cfg,
    task_log=task_log,
)
```

##### 4. `orchestrator.run_main_agent(...)`

短期可以先把原来的 `task_id` 参数继续保留，但实际传 `run_id`。

---

### C. `apps/miroflow-agent/src/llm/base_client.py`

#### 问题
当前 `task_id` 被同时当成：

- run id
- upstream session id
- conversation root
- trace root

#### 建议目标态

```python
@dataclasses.dataclass
class BaseClient(ABC):
    run_id: str
    api_session_id: str
    cfg: DictConfig
    task_log: Optional["TaskLog"] = None
```

#### 过渡策略
如果想降低一次性改动量，可以先保留 `task_id` 字段，但明确约定：

- `task_id == run_id`
- 新增 `api_session_id`

并逐步把内部实现改为使用：

- `self.run_id`
- `self.api_session_id`

最终再彻底删除/替换 `task_id`。

---

### D. `apps/miroflow-agent/src/llm/factory.py`

#### 当前问题
工厂只接收一个 `task_id`

#### 建议目标态

```python
def ClientFactory(
    run_id: str,
    api_session_id: str,
    cfg: DictConfig,
    task_log: Optional[TaskLog] = None,
    **kwargs,
)
```

并把两个 ID 分别传给：

- `OpenAIClient`
- `AnthropicClient`
- `AgentHubClient`

---

### E. `apps/miroflow-agent/src/llm/providers/openai_client.py`

#### 当前问题

```python
http_client_args = {"headers": {"x-upstream-session-id": self.task_id}}
```

这里实际传了 run id，而不是稳定 session id。

#### 建议改动
改为：

```python
http_client_args = {"headers": {"x-upstream-session-id": self.api_session_id}}
```

---

### F. `apps/miroflow-agent/src/llm/providers/anthropic_client.py`

与 OpenAI 同理：

```python
http_client_args = {"headers": {"x-upstream-session-id": self.api_session_id}}
```

---

### G. `apps/miroflow-agent/src/llm/providers/agenthub_client.py`

#### 目标原则
- conversation key 用稳定的 `api_session_id`
- trace id 用 `run_id`

#### 建议改动

##### 1. `_conversation_key(self, agent_type: str)`

当前用 `self.task_id`。

建议改为：

```python
root = self.api_session_id
```

然后：

- main agent:

```python
return f"{root}/main"
```

- sub-agent:

```python
return f"{root}/{subagent_run_id}"
```

如没有 subagent 局部 id，再 fallback 到：

```python
return f"{root}/{agent_type}"
```

##### 2. `_build_trace_id(self, agent_type: str, turn: int)`

这里建议继续使用 `run_id`：

```python
return f"{run_id}/{agent_type}/turn_{turn}_attempt_{attempt}"
```

原因：

- trace 是本次执行内部的唯一标识
- 不应该和跨轮次会话 key 复用同一个 root

---

### H. `apps/miroflow-agent/src/logging/task_logger.py`

#### 当前命名问题

```python
current_sub_agent_session_id
```

它并不是 API session/sandbox session/upstream session。

#### 建议
未来改名为：

```python
current_subagent_run_id
```

这样更符合它的真实语义：

- 当前 run 内某个 sub-agent 的局部执行身份

---

### I. `apps/miroflow-agent/src/core/orchestrator.py`

#### 过渡期建议
短期不做大范围重命名，但将原有运行级 `task_id` 语义明确视为 `run_id`。

#### 长期建议
逐步把运行级的 `task_id` 改名为 `run_id`。

---

## 九、最小可落地重构路径

为了控制风险，建议分阶段实施。

---

### Phase 0：定规则

先统一约定：

- `api_session_id`：稳定会话身份
- `run_id`：一次 execute 的唯一身份
- `trace_id`：单次模型调用身份
- 过渡期里，运行层的旧 `task_id` 一律按 `run_id` 理解

---

### Phase 1：先修功能 bug（最关键）

#### 目标
恢复“同一个 API session 在下游也是同一个 session”。

#### 改动文件

1. `apps/miroflow-agent/api_server.py`
2. `apps/miroflow-agent/src/core/pipeline.py`
3. `apps/miroflow-agent/src/llm/factory.py`
4. `apps/miroflow-agent/src/llm/base_client.py`
5. `apps/miroflow-agent/src/llm/providers/openai_client.py`
6. `apps/miroflow-agent/src/llm/providers/anthropic_client.py`
7. `apps/miroflow-agent/src/llm/providers/agenthub_client.py`

#### Phase 1 做法

- API 层显式生成：
  - `api_session_id`
  - `run_id`
- pipeline 显式接收：
  - `api_session_id`
  - `run_id`
- provider header 改用：
  - `api_session_id`
- AgentHub conversation key 改用：
  - `api_session_id`
- AgentHub trace id 保持使用：
  - `run_id`
- `TaskLog.task_id` 暂时仍保存 `run_id`

#### Phase 1 收益

- 快速修复 bug
- 不同轮次不会再因为 run id 变化而被下游视作不同 session
- 改动面可控

---

### Phase 2：清理命名歧义

#### 目标
让代码名字本身就表达正确语义。

#### 建议改动

- `BaseClient.task_id` -> `run_id`
- provider / factory / pipeline 中所有运行级 `task_id` -> `run_id`
- `current_sub_agent_session_id` -> `current_subagent_run_id`

#### 收益

- 读代码直观
- 后续开发不容易误用

---

### Phase 3：文档与日志语义统一

#### 建议更新

- `API_README.md`
- sandbox/session 说明
- debug log / trace 目录说明
- task log 字段说明

#### 收益

- 文档与代码一致
- 排查问题更容易

---

## 十、推荐的执行策略

### 最推荐的现实做法
本轮改动建议至少完成：

- **Phase 1 全做**
- **Phase 2 做关键部分**
  - 至少把 LLM 层的 `task_id` 正式理顺为 `run_id`

而 orchestrator / task_logger 的大规模 rename 可以在下一步做。

这样兼顾：

- 修复当前 bug
- 不再继续扩大歧义
- 改动成本可控

---

## 十一、建议的最终落地规则

### API 层
- `X-Session-Id` -> `api_session_id`

### Session / Sandbox 管理层
- `api_session_id -> session -> sandbox_id`

### Pipeline / 执行层
- `run_id` 表示本次 execute

### Provider 层
- `x-upstream-session-id = api_session_id`

### AgentHub 层
- `conversation_key` 使用 `api_session_id`
- `trace_id` 使用 `run_id`

### Sub-agent 层
- 使用 `subagent_run_id` 表示局部身份

---

## 十二、结论

本次排查发现的根因不是简单的 session 表没复用，而是：

> **稳定会话 ID 与动态运行 ID 被混用，导致下游把一次次 run 当成不同 session。**

具体表现为：

- API 层按 `X-Session-Id` 稳定复用 session/sandbox
- 但 pipeline 把 `task_id` 再包装成带随机 UUID 的 `unique_id`
- provider / AgentHub 又错误地把这个 `unique_id` 当成 session identity 使用

最终导致：

- 同一个会话在下游不连续
- 表现成不同轮次跑到不同状态/不同沙箱

因此，建议不只做补丁，而是把 ID 体系一次性理顺为：

- `api_session_id`
- `sandbox_id`
- `run_id`
- `trace_id`
- `subagent_run_id`

并按职责分层使用，避免继续让一个字段承担多种语义。

---

## 十三、AgentHub trace 行为与注意事项

在新的 ID 体系下，AgentHub 相关行为建议明确拆成两部分：

- **conversation identity**：使用 `api_session_id`
- **trace identity**：使用 `run_id`

### 1. 推荐行为

#### conversation key
`AgentHubClient._conversation_key()` 应改为使用稳定的 `api_session_id` 作为根：

- main agent:

```text
{api_session_id}/main
```

- sub-agent:

```text
{api_session_id}/{subagent_run_id}
```

这样它表达的是：

- 这是哪个会话
- 而不是这次执行是哪一轮 run

#### trace id
`AgentHubClient._build_trace_id()` 应继续使用 `run_id` 作为根：

```text
{run_id}/{agent_type}/turn_{turn}_attempt_{attempt}
```

这样它表达的是：

- 这是哪一次 execute
- 这次 execute 中第几轮、第几次 attempt 的调用

---

### 2. 改完后的 trace 目录行为

如果 trace id 改成以 `run_id` 为根，那么 AgentHub trace 会自然按每次 execute 分组。

例如同一个 `api_session_id = sess_001` 的两次执行：

- 第 1 次：`run_id = api_run_sess_001_a1b2c3`
- 第 2 次：`run_id = api_run_sess_001_d4e5f6`

那么 trace 目录形态会类似：

```text
logs/debug/agenthub_traces/
  api_run_sess_001_a1b2c3/
    main/
      turn_1_attempt_1.json
      turn_2_attempt_1.json
    web/
      turn_1_attempt_1.json

  api_run_sess_001_d4e5f6/
    main/
      turn_1_attempt_1.json
      turn_2_attempt_1.json
```

这意味着：

- 每个 `run_id` 对应一组独立 trace
- 同一个 session 的不同轮次不会混在同一个 trace 目录里
- 非常适合按“单次执行”做调试、回放和差异比较

这也符合最初希望“加上 `run_id` 后就围绕它做 trace”的设计目标。

---

### 3. 关于 AgentHub stateful 的关键说明

这里需要明确：

> **AgentHub 当前的 stateful 行为仅是本地的，与云端 API 提供商解耦。**

也就是说：

- stateful conversation 依赖的是本地 `AutoLLMClient` / `AgentHubClient` 实例内维护的状态
- 它并不是通过 OpenAI / Anthropic / 其他云端 API 提供商去托管会话历史
- 因而云端 provider 本身不会替你记住这个 stateful conversation

这点非常重要，因为它直接决定了“跨两次 `/execute` 是否会真正延续本地 stateful 历史”。

---

### 4. 这意味着什么

即使我们把 `_conversation_key()` 改成 `api_session_id`，如果：

- 每次 `/execute` 都重新创建一个新的 `AgentHubClient`
- 而 `AgentHubClient` 内部的 `_stateful_clients` 又只是实例级内存

那么：

- **同一次 run 内**：stateful 没问题
- **跨不同 `/execute` run**：本地 stateful 历史仍然不会自动延续

因为新的 `AgentHubClient` 实例启动后，本地内存状态已经是空的。

换句话说：

- `conversation_key = api_session_id` 可以把“语义”纠正
- 但它**不会自动让本地 stateful 内存跨实例持久化**

---

### 5. 因此要区分两件事

#### A. trace 行为
这一点可以立即做到，而且应该马上做：

- trace 按 `run_id` 切分
- 每次 execute 单独成组

#### B. 本地 stateful 会话延续
这一点不能只靠改 key，需要额外设计：

- 如果希望跨 `/execute` 也延续本地 AgentHub stateful 历史
- 那么需要把 `AgentHubClient` 或其 stateful backend 挂到 session 级别缓存上
- 而不是每次在 `execute_task_pipeline(...)` 中重新创建

也就是说，真正的跨轮本地 stateful 延续，需要类似：

```text
api_session_id -> session object -> persistent AgentHub stateful client
```

而不是：

```text
每次 run -> 新建一个 AgentHubClient -> 本地状态从空开始
```

---

### 6. 当前重构的边界建议

本轮 ID 重构建议先聚焦于：

1. 修复 session / run / trace 语义混用
2. 让 AgentHub trace 严格围绕 `run_id`
3. 让 conversation key 语义上改用 `api_session_id`

而“是否要让 AgentHub 本地 stateful 跨 execute 真正持久化”建议单独作为下一阶段问题处理。

原因是这已经不只是 ID 命名问题，而会涉及：

- session 生命周期
- 内存占用与回收策略
- 本地 stateful client 的缓存与并发安全
- session 关闭/过期时如何清理 stateful history

---

### 7. 最终建议

因此，对 AgentHub 的推荐落地方式是：

- **trace**：始终使用 `run_id`
- **conversation key**：使用 `api_session_id`
- **stateful 跨 execute 持久化**：作为单独功能设计，不和这次 ID 重构混在一起

这样可以保证：

- 本次重构先把 ID 语义彻底理顺
- trace 行为马上变得清晰可用
- 后续如果要做跨 execute 的本地 stateful 复用，也有清楚的扩展边界
