# 源码精读 28：Error taxonomy、structured payload 与恢复策略

> 源码基线：`4ee41929eaf4`。本文只描述这个固定提交中的实现。以后源码移动时，请优先搜索符号名，不要依赖本文中的路径位置或行号。

上一篇分析 analytics 时，我们看到 `ErrorResponse`、Turn error fact 和最终 Turn event 并不是同一件事。

这一篇把问题继续向业务主链路推进：

> Codex 内部的一次失败，怎样从底层错误逐层变成 Core event、App-server notification、JSON-RPC error 和最终 Turn 状态？客户端看到错误以后，究竟应该重试原请求、继续等待当前 Turn、创建新 Turn，还是只提示用户？

初看源码时，下面这些名字很容易被理解成“都是报错”：

```text
ApiError
TransportError
CodexErr
CodexErrorDetails
CodexErrorInfo
ErrorEvent
StreamErrorEvent
TurnError
JSONRPCErrorError
ErrorNotification
TurnStatus::Failed
TurnStatus::Interrupted
```

但它们处在完全不同的层级，承担的责任也不同。

本文最重要的结论是：

> Codex 没有一个放之四海而皆准的“error 对象”，而是按边界转换错误。每次转换都会保留当前消费者真正需要的信息，同时主动丢掉不应跨边界暴露的实现细节。

---

## 1. 先说人话：一次失败至少要回答四个问题

不要一看到 `Err(...)` 就只问“错误内容是什么”。

还应继续问：

1. **谁失败了？** 是一条 JSON-RPC request，还是已经运行中的 Turn？
2. **失败是否已经终结？** 系统还会自动 retry，还是已经放弃？
3. **谁负责恢复？** App-server、Core、客户端，还是用户？
4. **客户端靠什么判断？** 靠结构化字段，还是只能显示 message？

这四个问题会直接决定 UI 行为。

例如：

```text
error notification + willRetry=true
```

它的正确含义不是“Turn 失败了”，而是：

```text
当前尝试遇到了临时错误
→ Core 仍拥有这个 Turn
→ Core 会自动重试
→ 客户端不要再次发送 turn/start
→ 客户端继续等待后续 notification
```

相反：

```text
turn/completed + status=failed
```

才表示这个 Turn 已进入失败终态。

---

## 2. 三个最容易混淆的错误出口

固定提交中，App-server 客户端最常见的错误出口有三类。

| 出口 | 它回答的问题 | 是否一定存在 Turn | 典型恢复方式 |
|---|---|---:|---|
| JSON-RPC error response | 这次 request 为什么没有成功 | 否 | 修改请求、稍后重发，或停止 |
| `error` notification | 正在运行的 Turn 发生了什么 | 是 | 看 `willRetry`，继续等或展示终态错误 |
| `turn/completed` | Turn 最终是什么状态 | 是 | 以 `completed`、`failed`、`interrupted` 收口 UI |

先记住一句简化版：

```text
RPC error 终结 request
error notification 描述 Turn 过程
turn/completed 终结 Turn
```

同一个用户操作可能只经过其中一类，也可能依次经过两类。

---

## 3. 案例一：输入过长时，Turn 根本没有开始

假设客户端发送：

```json
{
  "id": 17,
  "method": "turn/start",
  "params": {
    "threadId": "thread-1",
    "input": [
      { "type": "text", "text": "一个超出限制的巨大字符串" }
    ]
  }
}
```

`TurnProcessor::validate_v2_input_limit` 会先统计所有文本输入的字符数。

如果超出 `MAX_USER_INPUT_TEXT_CHARS`，它返回一个 JSON-RPC error：

```json
{
  "id": 17,
  "error": {
    "code": -32602,
    "message": "Input exceeds the maximum length of ... characters.",
    "data": {
      "input_error_code": "input_too_large",
      "max_chars": 123,
      "actual_chars": 456
    }
  }
}
```

这里最重要的不是具体数字，而是生命周期：

```text
request 收到
→ 输入校验失败
→ 返回 JSON-RPC error
→ 没有提交 Core Op
→ 没有 turn/started
→ 没有 turn/completed
```

因此客户端不能傻等这个 Turn 的终态，因为这个 Turn 从未被创建。

测试 `turn_start_rejects_combined_oversized_text_input` 不仅断言错误 code 和 `data`，还特意等待一小段时间，确认没有收到 `turn/started`。

---

## 4. 案例二：流临时断开，但 Turn 仍然活着

模型 sampling 已经开始后，Responses stream 可能临时断开。

如果底层错误被 `CodexErr::is_retryable()` 判断为可重试，Core 不会立即结束 Turn，而会进入 retry handler：

```text
sampling request 失败
→ CodexErr::is_retryable() == true
→ handle_retryable_response_stream_error(...)
→ 可选切换 WebSocket → HTTPS
→ 或等待 backoff 后重试
→ 发送 StreamErrorEvent 告知 UI
```

App-server 将 `StreamErrorEvent` 投影为：

```json
{
  "method": "error",
  "params": {
    "threadId": "thread-1",
    "turnId": "turn-1",
    "willRetry": true,
    "error": {
      "message": "Reconnecting... 2/5",
      "codexErrorInfo": {
        "responseStreamDisconnected": {
          "httpStatusCode": 502
        }
      },
      "additionalDetails": "底层连接错误的诊断文本"
    }
  }
}
```

这条 notification 的含义是：

- 当前 Turn 仍是 `inProgress`；
- App-server/Core 将自动恢复；
- 客户端可以展示“正在重连”；
- 客户端不应重新发送原来的 `turn/start`；
- 它不应写入本 Turn 的最终 `last_error`。

---

## 5. 案例三：自动恢复失败，Turn 才进入 failed

如果错误不可重试，或者 retry handler 最终返回 `Err`，主 Turn loop 会把 `CodexErr` 转成终态错误：

```text
CodexErr
→ to_codex_protocol_error()
→ emit extension lifecycle
→ track analytics error
→ to_error_event()
→ EventMsg::Error
→ App-server ErrorNotification(willRetry=false)
→ Core TurnComplete
→ App-server turn/completed(status=failed, error=...)
```

客户端通常会先看到一条：

```json
{
  "method": "error",
  "params": {
    "willRetry": false,
    "error": {
      "message": "...",
      "codexErrorInfo": "serverOverloaded",
      "additionalDetails": null
    }
  }
}
```

然后看到最终：

```json
{
  "method": "turn/completed",
  "params": {
    "turn": {
      "status": "failed",
      "error": {
        "message": "...",
        "codexErrorInfo": "serverOverloaded",
        "additionalDetails": null
      }
    }
  }
}
```

第一条适合及时展示错误；第二条负责确定业务终态。

---

## 6. 案例四：用户中断不是失败

用户调用 `turn/interrupt` 时，Core 的任务被取消，最终产生 `TurnAbortedEvent`。

App-server 将它投影为：

```json
{
  "method": "turn/completed",
  "params": {
    "turn": {
      "status": "interrupted",
      "error": null
    }
  }
}
```

这里没有 `TurnError`，因为：

- 中断是用户或控制流的明确意图；
- 它不表示模型服务、协议或业务逻辑失败；
- Thread 仍可以继续接收下一次输入。

因此不要把 `interrupted` 渲染成红色“系统错误”。

---

## 7. 一张总流程图

```text
                     ┌─ 参数/准入失败 ──────────────┐
Client request ──────┤                              ├→ JSON-RPC error
                     └─ 已接纳并创建 Turn ─────────┘
                                      │
                                      ▼
                         API/transport/tool failure
                                      │
                                      ▼
                                  CodexErr
                                      │
                     ┌────────────────┴────────────────┐
                     │                                 │
             is_retryable = true                不可重试/重试耗尽
                     │                                 │
                     ▼                                 ▼
        StreamErrorEvent                     ErrorEvent
                     │                                 │
                     ▼                                 ▼
      error notification                    error notification
         willRetry=true                        willRetry=false
                     │                                 │
              Core 自动恢复                            ▼
                     │                    turn/completed(status=failed)
          ┌──────────┴──────────┐
          │                     │
        成功                最终仍失败
          │                     │
          ▼                     ▼
 turn/completed          进入右侧终态路径
 status=completed

用户 interrupt ─→ TurnAbortedEvent ─→ turn/completed(status=interrupted)
```

---

## 8. 为什么不能只设计一个 `retryable: bool`

看起来最简单的做法是给所有错误统一加一个布尔字段：

```rust
struct Error {
    message: String,
    retryable: bool,
}
```

但这个设计无法回答“谁来 retry”。

至少有四种不同语义：

1. App-server ingress 满了，**客户端**稍后重发 request；
2. Responses stream 断了，**Core**自动重连；
3. WebSocket transport 不稳定，**Core**切换到 HTTPS；
4. 某个 model 的 compaction 失败，**compaction 逻辑**可能换 model 再试。

它们都可以被口语称为“可重试”，但 action owner、重试单位和幂等风险完全不同。

所以固定提交中，retry policy 分散在具体 workflow：

- `CodexErr::is_retryable()`：sampling/stream loop 的分类；
- `handle_retryable_response_stream_error`：stream recovery 执行器；
- App-server `-32001`：客户端 request retry 契约；
- `should_retry_guardian_review`：guardian review 自己的分类；
- `should_retry_with_current_model`：compaction model fallback 的分类。

这不是“重复代码一定不好”，而是说明 retry 是上下文相关的业务策略。

---

## 9. 本篇源码地图

| 层次 | 固定提交中的文件 | 重点符号 |
|---|---|---|
| API/transport → Core error | `codex-rs/codex-api/src/api_bridge.rs` | `map_api_error` |
| Core error taxonomy | `codex-rs/protocol/src/error.rs` | `CodexErr`、`CodexErrorDetails`、`is_retryable` |
| Client-safe error category | `codex-rs/protocol/src/protocol.rs` | `CodexErrorInfo`、`affects_turn_status` |
| Retry loop | `codex-rs/core/src/responses_retry.rs` | `handle_retryable_response_stream_error` |
| Sampling loop | `codex-rs/core/src/session/turn.rs` | `run_sampling_request` 附近 |
| Error persistence/terminal capture | `codex-rs/core/src/session/mod.rs` | `send_event`、`notify_stream_error` |
| Turn terminal event | `codex-rs/core/src/tasks/mod.rs` | `on_task_finished` |
| JSON-RPC envelope | `codex-rs/app-server-protocol/src/rpc.rs` | `JSONRPCError`、`JSONRPCErrorError` |
| JSON-RPC codes | `codex-rs/app-server/src/error_code.rs` | standard codes、`-32001` |
| V2 error payload | `codex-rs/app-server-protocol/src/protocol/v2` | `TurnError`、`ErrorNotification` |
| Core → App-server projection | `codex-rs/app-server/src/bespoke_event_handling.rs` | Error/StreamError/TurnComplete branches |
| Request validation examples | `codex-rs/app-server/src/request_processors/turn_processor.rs` | input limit、TurnSteer errors |
| Ingress overload | `codex-rs/app-server-transport/src/transport/mod.rs` | `OVERLOADED_ERROR_CODE` |
| History reconstruction | `codex-rs/app-server-protocol/src/protocol/thread_history.rs` | `handle_error`、`handle_turn_complete` |

---

## 10. 第一层：`ApiError` 和 `TransportError` 描述底层发生了什么

`codex-api` 更接近 Responses API 和 HTTP transport。

这一层关心的是：

- HTTP status 是多少；
- body 中有没有结构化 error code；
- 是 timeout、network、stream，还是 retry limit；
- header 中有没有 request ID、Cloudflare ray、retry delay；
- 429 是使用额度耗尽，还是一般速率限制。

这些信息非常适合诊断底层请求，却不适合直接作为稳定客户端协议。

例如不同 provider 的错误 body 可能完全不同。

因此 `map_api_error` 会把它们转换成统一的 `CodexErr`。

---

## 11. `map_api_error` 是第一个语义归一化点

一些典型映射如下：

| API/transport 现象 | `CodexErr` |
|---|---|
| context window exceeded | `ContextWindowExceeded` |
| quota/usage not included | `QuotaExceeded` / `UsageNotIncluded` |
| API 明确标记 retryable | `Stream(message)`，可附 server delay |
| stream 断开 | `Stream(message)` |
| provider overloaded | `ServerOverloaded` |
| HTTP 400 invalid request | `InvalidRequest(body)` |
| HTTP 400 cyber policy | `CyberPolicy { message }` |
| HTTP 500 | `InternalServerError` |
| HTTP 429 usage limit | `UsageLimitReached(...)` |
| 其他 HTTP 429 | `RetryLimit(...)` |
| timeout | `RequestTimeout` |
| network/build failure | `Stream(message)` |

注意：同样是 HTTP 429，也可能被拆成不同语义。

代码会尝试解析 body 中的 usage error，保留 plan、reset time、rate-limit snapshot 等信息；解析不到特定 usage 类型时，才落到 `RetryLimit`。

---

## 12. `CodexErr` 不是一个巨大 enum，而是 wrapper

固定提交中的结构是：

```rust
pub struct CodexErr {
    details: CodexErrorDetails,
    retry_delay: Option<Duration>,
}
```

它把两个维度分开：

- `details`：发生了哪一类语义失败，以及诊断 payload；
- `retry_delay`：上游是否建议等待特定时长。

为什么不把 delay 放进每个可重试 variant？

因为 delay 是跨多种错误的附加调度信息，不是错误分类本身。

例如：

```rust
CodexErr::Stream("retry later".to_string())
    .with_retry_delay(Duration::from_secs(2))
```

错误仍然是 `Stream`，只是恢复调度多了一个 server-provided hint。

---

## 13. `CodexErrorDetails` 才是 Core 内部的完整分类

`CodexErrorDetails` 包含很多只适合内部使用的 variant，例如：

```text
TurnAborted
SessionBudgetExceeded
Stream(String)
ContextWindowExceeded
ThreadNotFound(ThreadId)
AgentLimitReached { max_threads }
UnexpectedStatus(UnexpectedResponseError)
InvalidRequest(String)
ToolCollision(String)
UsageLimitReached(UsageLimitReachedError)
ResponseStreamFailed(ResponseStreamFailed)
ConnectionFailed(ConnectionFailedError)
RetryLimit(RetryLimitReachedError)
Sandbox(SandboxErr)
Io(io::Error)
Json(serde_json::Error)
TokioJoin(JoinError)
Fatal(String)
```

它同时承担三类信息：

1. 控制流语义，例如 `TurnAborted`；
2. 产品语义，例如 `UsageLimitReached`；
3. 实现诊断，例如 `Io`、`Json`、`TokioJoin`。

这些信息不能原样全部暴露给客户端。

---

## 14. `CodexErrKind` 是无 payload 的 analytics 分类

`CodexErrorDetails` 通过 `EnumDiscriminants` 生成 `CodexErrKind`。

可以把两者理解为：

```text
CodexErrorDetails::ThreadNotFound(thread_id)
                  │
                  └─ 去掉具体 payload
                     → CodexErrKind::ThreadNotFound
```

Analytics 通常需要统计“哪类错误出现多少次”，不需要也不应上传任意错误正文、路径、request ID 或用户内容。

所以：

- `CodexErrorDetails` 适合运行时处理和诊断；
- `CodexErrKind` 适合低基数分类统计。

这也是错误 taxonomy 不只服务 UI 的例子。

---

## 15. `Display`、`Debug` 和 structured category 是三种不同视图

同一个 `CodexErr` 可以有三种表现：

```text
Display → 给日志或用户看的文本
Debug   → 给开发者看的结构形状
CodexErrorInfo → 给客户端逻辑判断的稳定分类
```

例如 `Stream` 的 `Debug` 特意保留 retry delay：

```text
Stream("retry later", Some(2s))
```

而 `Display` 主要展示 message。

客户端不应解析这两种字符串来决定 retry，因为文本可能变化、本地化或被截断。

---

## 16. 为什么错误 message 不是 API discriminator

下面这种客户端代码很脆弱：

```ts
if (error.message.includes("context window")) {
  showNewThreadButton();
}
```

问题包括：

- message 改写后逻辑失效；
- 不同 provider 文案不同；
- 本地化后关键词消失；
- 诊断详情可能包含相同短语；
- 用户输入甚至可能出现在错误正文中。

更稳妥的是：

```ts
if (error.codexErrorInfo === "contextWindowExceeded") {
  showNewThreadButton();
}
```

带 payload 的 enum variant 则按生成 schema 的实际形状判断。

---

## 17. `retry_delay` 是建议，不是完成承诺

`CodexErr::retry_delay()` 返回 `Option<Duration>`。

retry handler 的逻辑是：

```rust
let delay = err.retry_delay().unwrap_or_else(|| backoff(retry_count));
```

因此：

- 有 server hint：采用 server 建议；
- 没有 hint：采用本地 exponential backoff + jitter；
- 等完 delay：只表示允许发起下一次尝试；
- 不表示下一次一定成功。

“等待两秒”不是“系统保证两秒后恢复”。

---

## 18. `is_retryable()` 是当前 sampling workflow 的分类

`CodexErr::is_retryable()` 使用 exhaustive `match`，明确列出每个 variant。

大致可分为：

**当前 request/stream loop 会尝试恢复：**

```text
Stream
Timeout
RequestTimeout
UnexpectedStatus
ResponseStreamFailed
ConnectionFailed
InternalServerError
InternalAgentDied
Io
Json
TokioJoin
```

**当前 loop 不会自动重试：**

```text
ContextWindowExceeded
SessionBudgetExceeded
UsageLimitReached
ServerOverloaded
CyberPolicy
InvalidRequest
ToolCollision
Sandbox
RetryLimit
ThreadNotFound
AgentLimitReached
Fatal
TurnAborted
...
```

这张表不能脱离调用点理解。

例如 `ServerOverloaded` 在 `CodexErr::is_retryable()` 中是 false，但 guardian review 或 model fallback 仍可能把相同的 client-safe category 视作值得在自己的 workflow 中重试。

---

## 19. “不可由这个 loop 重试”不等于“永远不可恢复”

这是整篇最重要的概念之一。

```text
is_retryable() == false
```

只意味着：

> 当前 Responses sampling retry loop 不应原地、自动、按同一策略继续尝试。

它不一定意味着：

- 用户永远不能再试；
- 换一个 model 也不行；
- 额度重置后仍不行；
- 修正输入后仍不行；
- guardian 或 compaction 的专用策略不能处理。

更准确的恢复描述应是一个向量：

```text
(责任方, 重试单位, 前置修复, 最大次数, backoff, 幂等要求)
```

而不是一个脱离上下文的 bool。

---

## 20. `to_codex_protocol_error()` 是有意的有损转换

Core 内部错误最终需要给各种客户端消费。

`CodexErr::to_codex_protocol_error()` 将丰富的 `CodexErrorDetails` 压缩为较稳定的 `CodexErrorInfo`：

| Core details | Client-safe `CodexErrorInfo` |
|---|---|
| `ContextWindowExceeded` | `ContextWindowExceeded` |
| `SessionBudgetExceeded` | `SessionBudgetExceeded` |
| usage/quota/not included | `UsageLimitExceeded` |
| `ServerOverloaded` | `ServerOverloaded` |
| `CyberPolicy` | `CyberPolicy` |
| `RetryLimit` | `ResponseTooManyFailedAttempts { http_status_code }` |
| `ConnectionFailed` | `HttpConnectionFailed { http_status_code }` |
| `ResponseStreamFailed` | `ResponseStreamConnectionFailed { http_status_code }` |
| refresh token failed | `Unauthorized` |
| selected internal failures | `InternalServerError` |
| unsupported/thread missing/agent limit | `BadRequest` |
| sandbox failure | `SandboxError` |
| 其他大量内部类型 | `Other` |

这个转换会丢失信息，但这是边界设计，不是 bug。

客户端通常不需要知道失败来自 `io::Error` 还是 Tokio join failure；它需要知道是否显示“网络连接失败”“额度耗尽”“上下文满了”等产品动作。

---

## 21. 为什么 `CodexErrorInfo` 的 variant 数量更少

公开 category 越细，并不一定越好。

每增加一个公开 variant，都意味着：

- TypeScript schema 要稳定支持它；
- 各客户端可能要新增 UI 和恢复分支；
- 以后合并或改名会成为 breaking change；
- 旧客户端必须能处理新值；
- provider-specific 细节可能泄漏为产品契约。

所以合理策略是：

```text
内部 taxonomy 足够细，用于修复和观测
公开 taxonomy 足够稳，用于产品行为
message/additionalDetails 保留必要诊断
```

---

## 22. `http_status_code` 只出现在相关 variant 中

`CodexErrorInfo` 没有一个全局 `status` 字段，而是在这些 variant 中携带可选值：

```text
HttpConnectionFailed
ResponseStreamConnectionFailed
ResponseStreamDisconnected
ResponseTooManyFailedAttempts
```

这表达了一个类型约束：

> 只有与 HTTP/stream transport 有直接关系的 category，HTTP status 才有明确语义。

如果给 `ContextWindowExceeded` 也放一个通用 `httpStatusCode`，客户端很容易误以为 status 是业务恢复的主分类。

---

## 23. `http_status_code_value()` 也不是万能提取器

Core 只从部分 details 读取 status：

```text
RetryLimit
UnexpectedStatus
ConnectionFailed
ResponseStreamFailed
```

其他 variant 返回 `None`。

因此：

- `None` 不等于“没有经过 HTTP”；
- 它只表示当前 error shape 没有保留一个可安全传播的 status；
- 客户端不应把 `None` 当作某种隐藏的 200。

---

## 24. `to_error_event()` 同时生成文本与结构化分类

`CodexErr::to_error_event(message_prefix)` 做两件事：

```text
Display text → ErrorEvent.message
protocol mapping → ErrorEvent.codex_error_info
```

可选的 prefix 只影响 message：

```text
prefix: underlying error message
```

不会改变 `CodexErrorInfo`。

这让调用者可以补充场景信息，又不破坏结构化分类。

---

## 25. 第二层：Core protocol 的 `ErrorEvent`

Core 发出的终态错误结构很小：

```rust
pub struct ErrorEvent {
    pub message: String,
    pub codex_error_info: Option<CodexErrorInfo>,
}
```

这里的 `Option` 有兼容性意义。

旧 rollout 或某些手工构造的 error event 可能只有 message，没有结构化 category。

因此读取逻辑不能假设它永远是 `Some`。

---

## 26. 没有 `codex_error_info` 时，默认仍影响 Turn 状态

`ErrorEvent::affects_turn_status()` 的实现是：

```rust
self.codex_error_info
    .as_ref()
    .is_none_or(CodexErrorInfo::affects_turn_status)
```

换成人话：

```text
没有结构化 category → 默认把它当作真正的 Turn error
有 category          → 由 category 决定
```

为什么默认是 true？

因为兼容旧数据时，宁可保留“发生过错误”的历史语义，也不能因为旧记录缺少新字段就把失败 Turn 重放成成功。

---

## 27. 两类 `CodexErrorInfo` 明确不应弄失败当前 Turn

固定提交中：

```rust
ThreadRollbackFailed
ActiveTurnNotSteerable { .. }
```

返回 `affects_turn_status() == false`。

原因是它们描述的是旁路操作失败，而不是正在运行的主 Turn 失败。

例如：

- 用户在 review Turn 上发起 `turn/steer`，steer request 被拒绝；
- 用户请求 rollback，但 rollback 操作失败。

原来的 Turn 可能仍然健康运行。

如果把这些错误记到当前 Turn 的 terminal error，会错误地终止无辜的业务流程。

---

## 28. `ActiveTurnNotSteerable` 为什么带 `turn_kind`

它不是简单字符串，而是：

```rust
ActiveTurnNotSteerable {
    turn_kind: NonSteerableTurnKind,
}
```

当前固定提交中的 kind 包括：

```text
Review
Compact
```

V2 wire format 使用 camelCase，例如：

```json
{
  "activeTurnNotSteerable": {
    "turnKind": "review"
  }
}
```

客户端可以据此解释：“当前正在做代码审查，不能把普通消息 steer 进去”，而不必从英文 message 中猜测。

---

## 29. 第三层：`StreamErrorEvent` 是中间态，不是 `ErrorEvent`

Core 为自动重试单独定义：

```rust
pub struct StreamErrorEvent {
    pub message: String,
    pub codex_error_info: Option<CodexErrorInfo>,
    pub additional_details: Option<String>,
}
```

它没有复用 `ErrorEvent`，因为生命周期语义不同：

| 类型 | 是否写入 Turn terminal error | App-server `willRetry` | 是否应导致 `failed` |
|---|---:|---:|---:|
| `ErrorEvent` | 通常是 | false | 通常是 |
| `StreamErrorEvent` | 否 | true | 否 |

类型分开以后，调用点不容易忘记“这是中间态”。

---

## 30. `notify_stream_error()` 为什么重写 category

收到可重试的 `CodexErr` 后，`Session::notify_stream_error` 不直接把原始 protocol category 发出去。

它统一构造：

```rust
CodexErrorInfo::ResponseStreamDisconnected {
    http_status_code: codex_error.http_status_code_value(),
}
```

同时把原始错误文本放进：

```text
additional_details
```

这表示：

- 对 UI 来说，当前产品状态是“stream disconnected，正在恢复”；
- 原始错误具体是 timeout、HTTP status 还是 network text，只是诊断详情；
- 客户端不需要理解 Core 内部每个 retryable variant。

---

## 31. 为什么第一轮 WebSocket retry 可能不通知 UI

`handle_retryable_response_stream_error` 有一个降噪条件。

release build 中，如果 Responses WebSocket 已启用，第一次 retry notification 可能被隐藏。

只有满足下列之一才报告：

```text
retry_count > 1
或 debug build
或当前没有启用 Responses WebSocket
```

目的不是掩盖失败，而是避免把瞬时 WebSocket reconnect 变成用户频繁看到的噪声。

但无论是否通知 UI，retry 和 backoff 仍然发生。

所以：

> 没看到 `willRetry=true` notification，不代表底层从未发生自动 retry。

---

## 32. retry 次数来自 provider，并且有硬上限

sampling loop 获取：

```rust
let max_retries = turn_context.provider.info().stream_max_retries();
```

ProviderInfo 的规则是：

- 未配置时默认 `5`；
- 用户可覆盖；
- 最终被 hard cap 到 `100`。

这同时解决两个问题：

- provider 可以根据 transport 特性调整；
- 错误配置不能制造无界 retry。

注意 `max_retries` 表示重试次数，不一定等于总 request 尝试次数。

---

## 33. 本地 backoff 是指数增长并带 jitter

`util::backoff(attempt)` 的固定提交参数是：

```text
initial delay = 200 ms
factor = 2.0
jitter = 0.9..1.1
```

大致序列可能是：

```text
第 1 次：约 200 ms
第 2 次：约 400 ms
第 3 次：约 800 ms
第 4 次：约 1600 ms
```

为什么要 jitter？

如果大量客户端同时失败，又在完全相同的时间点重试，会形成 thundering herd：

```text
服务恢复一点
→ 所有客户端同时冲上来
→ 服务再次过载
```

随机抖动让重试时间错开。

---

## 34. server-provided delay 优先于本地 backoff

处理顺序是：

```text
err.retry_delay()
  ├─ Some(delay) → 使用它
  └─ None        → backoff(retry_count)
```

这体现了一个常见协议原则：

> 如果服务端知道自己何时可能恢复，客户端应优先尊重服务端的节流提示。

但 Core 仍保留本地 fallback，避免没有提示时热循环重试。

---

## 35. WebSocket → HTTPS fallback 发生在 retry budget 用完时

handler 首先判断：

```text
retries >= max_retries
并且 try_switch_fallback_transport(...) 成功
```

若成功：

- 发一条 `WarningEvent`；
- 把 retry count 重置为 0；
- 返回 `Ok(())`，让外层 request loop 再试；
- 后续 transport 改为 HTTPS。

所以 retry budget 用完不一定立即意味着 Turn failed。

它可能触发一次 transport strategy 变化。

---

## 36. fallback warning 与 stream error 是不同 UI 信号

切换 transport 时发送的是 `WarningEvent`：

```text
Falling back from WebSockets to HTTPS transport. ...
```

普通 retry 可发送 `StreamErrorEvent`：

```text
Reconnecting... 2/5
```

两者的区别是：

- warning 描述策略降级；
- stream error 描述一次可恢复故障；
- 两者都不必然终结 Turn。

客户端可以用不同视觉层级展示，但都不应提前把 Turn 标记为 failed。

---

## 37. sampling loop 为什么保存 `original_input`

第一次 sampling request 会使用已经准备好的 input。

如果失败后重试，代码会重新从 Session history 构建 prompt，而不是盲目复用一份可能已经过时的输入。

同时 `original_input` 保存首次 prompt input，用于成功返回后的后续逻辑。

这提醒我们：

> retry 不只是“再次调用同一个函数”，还要明确哪些输入必须快照、哪些状态应重新读取。

尤其当工具调用或 pending input 可能改变 history 时，这一点很重要。

---

## 38. Context window 与 usage limit 在 loop 中被提前特殊处理

sampling error 分支先检查：

```text
ContextWindowExceeded
UsageLimitReached
```

前者会把 total token state 标为 full；后者会更新 rate-limit snapshot。

然后才把错误返回。

也就是说，错误处理不只是“显示报错”，还可能更新后续 UI 和 session 决策所需的状态。

如果只在最外层统一 `log(err)`，这些语义副作用就会丢失。

---

## 39. 不可重试错误怎样离开 sampling loop

当：

```rust
if !err.is_retryable() {
    return Err(err);
}
```

外层 agent loop 会处理这个 `CodexErr`。

常规分支依次执行：

```text
记录 Turn error log
→ 转换 CodexErrorInfo
→ 通知 extension lifecycle
→ 记录 analytics fact
→ 生成 ErrorEvent
→ send_event
→ break 当前 agent loop
```

注释写着：

```text
let the user continue the conversation
```

也就是说失败结束的是当前 Turn，不一定结束 Thread。

---

## 40. Invalid image 为什么有专门的用户文案

`InvalidImageRequest` 在 agent loop 中有专门分支。

内部错误的 `Display` 是较技术化的 `Image poisoning`，但发给用户的 message 被改成：

```text
Invalid image in your last message. Please remove it and try again.
```

结构化 category 则是：

```text
BadRequest
```

这是“诊断文本”和“用户行动提示”分离的好例子。

客户端不需要知道内部 variant 名，只需展示可操作建议。

---

## 41. `TurnAborted` 在 agent loop 中必须单独处理

当错误 details 是 `TurnAborted` 时，代码直接：

```rust
return Err(err);
```

它不会走普通 `ErrorEvent` 分支。

随后 `on_task_finished` 将它识别成：

```rust
Some(TurnAbortReason::Interrupted)
```

最后发送 `TurnAbortedEvent`，而不是 `ErrorEvent`。

这条分支保证了用户取消不会被错误地持久化为失败。

---

## 42. unexpected task error 仍会被收口为 Turn failure

如果顶层 Session task 返回的不是 `TurnAborted`，而是其他意外 `CodexErr`，`on_task_finished` 会再次兜底：

```text
warn
→ emit_turn_error_lifecycle
→ analytics error
→ ErrorEvent
→ 继续执行统一 terminal cleanup
```

这是一层 defensive boundary。

理想情况下常规 sampling error 已在内层被转换；但顶层仍不能假设所有 future 永远按理想路径返回。

---

## 43. `send_event` 是 terminal error 的 Core 记录点

`Session::send_event` 在持久化和 fan-out 前检查：

```rust
if let EventMsg::Error(error) = &legacy_source
    && error.codex_error_info
        .as_ref()
        .is_some_and(CodexErrorInfo::affects_turn_status)
{
    turn_context.terminal_error.replace(error.clone());
}
```

这段代码只在以下条件同时成立时记录 terminal error：

- event 是 `ErrorEvent`；
- `codex_error_info` 是 `Some`；
- category 会影响 Turn status。

`StreamErrorEvent` 不会进入这里。

---

## 44. Core terminal capture 与兼容回放的默认值略有不同

这里有一个值得精读的细节：

```text
ErrorEvent::affects_turn_status()
  → category=None 时默认 true

Session::send_event terminal capture
  → category=None 时不会写 terminal_error
```

为什么会出现差异？

- `ErrorEvent::affects_turn_status` 要兼容旧 rollout，缺 category 时仍保留失败语义；
- 新的 Core terminal capture 更谨慎，只把明确结构化且影响 Turn 的错误放进 `TurnCompleteEvent.error`。

但 App-server 的 live summary 和 history reconstruction 都会单独处理 `ErrorEvent`，所以旧式无 category error 仍能被重建为 failed。

不要只看 `TurnCompleteEvent.error` 一个字段就推断整套历史语义。

---

## 45. terminal error 为什么还要复制进 `TurnCompleteEvent`

`on_task_finished` 在正常非-interrupt 收口时读取：

```rust
let error = turn_context.terminal_error.lock().await.clone();
```

然后放进：

```rust
TurnCompleteEvent { error, ... }
```

这样 durable canonical terminal record 自己就携带最终 error，不必永远依赖向前扫描所有中间 event。

但 live App-server 同时维护 `turn_summary.last_error`，是为了在流式 projection 中及时发送 notification 并形成最终 Turn。

这两个路径服务不同读取模式：

```text
live reducer：按事件顺序维护 summary
durable projection：terminal record 尽量自包含
```

---

## 46. 第四层：V2 `CodexErrorInfo` 负责 wire naming

Core protocol 的 enum 使用 snake_case 序列化习惯，而 App-server v2 API 规定 camelCase。

因此 v2 定义了自己的 mirror enum，并实现：

```rust
impl From<CoreCodexErrorInfo> for CodexErrorInfo
```

例如：

```text
Core: response_stream_disconnected
V2:   responseStreamDisconnected
```

带字段的 variant 还显式保持：

```text
httpStatusCode
turnKind
```

这层不是机械重复，而是稳定外部 wire contract 的翻译层。

---

## 47. 为什么 Rust 和 wire enum 不能偷懒共用

如果 App-server 直接暴露 Core enum：

- Core 重构可能意外改动 API；
- snake_case/camelCase 约定会耦合；
- App-server 无法独立控制 experimental 或兼容字段；
- TypeScript schema 的归属会混乱。

通过显式 exhaustive conversion：

```rust
match value {
    CoreCodexErrorInfo::ContextWindowExceeded => ...,
    ...
}
```

Core 新增 variant 时，编译器会迫使 App-server 作者决定怎样暴露它。

这比 wildcard 映射到 `Other` 更容易发现协议变化。

---

## 48. `TurnError` 是客户端看到的业务错误对象

V2 的定义是：

```rust
pub struct TurnError {
    pub message: String,
    pub codex_error_info: Option<CodexErrorInfo>,
    pub additional_details: Option<String>,
}
```

三个字段分别面向不同用途：

| 字段 | 主要消费者 | 用法 |
|---|---|---|
| `message` | 用户 | 展示主要说明 |
| `codexErrorInfo` | 客户端逻辑 | 选择图标、按钮和恢复动作 |
| `additionalDetails` | 诊断界面/日志 | 展开查看底层原因 |

不要把 `additionalDetails` 默认直接铺在主 UI 上，它可能技术化、冗长，甚至随实现变化。

---

## 49. `TurnError` 同时出现在 notification 和最终 Turn 中

同一个 shape 被复用在：

```text
ErrorNotification.error
Turn.error
持久化 thread history 的 stored error
```

这样客户端无需维护三套展示模型。

但要注意 envelope 提供了额外语义：

- 在 `ErrorNotification` 中，还要看 `willRetry`；
- 在 `Turn` 中，还要看 `status`；
- 在历史读取中，它描述的是已投影的持久状态。

不能只拿到 `TurnError` 就忽略它所在的上下文。

---

## 50. `ErrorNotification.will_retry` 是恢复责任声明

定义中的注释非常明确：

```text
true 表示错误是临时的，app-server process 将自动重试；
如果为 true，它不会中断 Turn。
```

因此它不只是“可重试性提示”，而是：

> 服务端声明自己正在负责 retry。

客户端的正确响应通常是：

```text
显示临时状态
保持当前 Turn
继续消费 stream
不要重复发送业务输入
```

---

## 51. `willRetry=false` 也不等于客户端立刻重发

`willRetry=false` 只表示 App-server/Core 不再自动恢复本次 Turn。

客户端仍应：

1. 展示 error；
2. 等待或结合 `turn/completed` 收口当前 Turn；
3. 根据 `codexErrorInfo` 决定下一步；
4. 必要时让用户修改输入、登录、充值、换 thread 或手动重试。

如果收到 `willRetry=false` 就自动重发原 `turn/start`，可能导致：

- 重复用户消息；
- 重复工具副作用；
- 同一 Thread 中产生额外 Turn；
- 对额度/策略错误形成无限循环。

---

## 52. JSON-RPC error shape 与 `TurnError` shape 不同

JSON-RPC error 是：

```rust
pub struct JSONRPCErrorError {
    pub code: i64,
    pub data: Option<serde_json::Value>,
    pub message: String,
}
```

它没有固定 `codexErrorInfo` 字段，因为它服务所有 App-server request，不只 Turn。

结构化扩展放在通用 `data` 中。

因此两种错误不能简单合并：

```text
JSONRPCErrorError → protocol request failure
TurnError         → Codex Turn domain failure
```

---

## 53. 固定提交并不在 wire 上发送 `jsonrpc: "2.0"`

`rpc.rs` 顶部明确说明：

```text
We do not do true JSON-RPC 2.0,
as we neither send nor expect the "jsonrpc": "2.0" field.
```

所以 envelope 看起来像 JSON-RPC，但省略版本字段。

客户端应以当前生成 schema 和 App-server README 为准，而不是假设任意通用 JSON-RPC library 的默认行为完全匹配。

---

## 54. App-server 定义的主要 JSON-RPC error code

`error_code.rs` 中有：

| code | 常量 | 含义 |
|---:|---|---|
| `-32600` | `INVALID_REQUEST_ERROR_CODE` | request envelope/语义无效 |
| `-32601` | `METHOD_NOT_FOUND_ERROR_CODE` | method 或操作不支持 |
| `-32602` | `INVALID_PARAMS_ERROR_CODE` | 参数不符合要求 |
| `-32603` | `INTERNAL_ERROR_CODE` | 服务端内部处理失败 |
| `-32001` | `OVERLOADED_ERROR_CODE` | App-server ingress/queue 过载 |

其中 `-32001` 是 server-defined code，不属于标准保留的那组通用错误。

---

## 55. helper 默认把 `data` 设为 `None`

这些 helper：

```text
invalid_request(...)
method_not_found(...)
invalid_params(...)
internal_error(...)
```

最终都调用一个小函数，构造：

```rust
JSONRPCErrorError {
    code,
    message,
    data: None,
}
```

只有调用点确实有稳定结构化信息时，才额外填写 `data`。

这避免每个普通错误都随意塞入不同 JSON shape。

---

## 56. 输入过长的 `data` 是一个很好的 structured payload 示例

`input_too_large_error` 先创建 `invalid_params`，再补：

```json
{
  "input_error_code": "input_too_large",
  "max_chars": 100000,
  "actual_chars": 100001
}
```

客户端可以：

- 用 `input_error_code` 选择专用提示；
- 用 `max_chars` 显示上限；
- 用 `actual_chars` 告诉用户超出多少；
- 不解析英文 message 中的数字。

这是 message + machine-readable data 的标准搭配。

---

## 57. `input_error_code` 为什么是字符串而不是另一个 JSON-RPC code

`-32602` 已经表达“参数无效”这一大类。

`input_too_large` 表达的是更细的产品原因。

可以把它们理解为两级 taxonomy：

```text
transport/protocol class: -32602 invalid params
domain reason:            input_too_large
```

这样通用 JSON-RPC client 能处理大类，Codex-aware client 能处理细类。

---

## 58. `turn/steer` 把 `TurnError` 放进 JSON-RPC `data`

当 active Turn 是 review 或 compact，steer 会失败。

调用点构造：

```rust
TurnError {
    message,
    codex_error_info: Some(ActiveTurnNotSteerable { turn_kind }),
    additional_details: None,
}
```

然后序列化到 JSON-RPC error 的 `data`。

这是一座桥：

```text
request 仍然以 JSON-RPC error 结束
但 data 内复用了 Turn domain 的结构化错误含义
```

原 active Turn 不会因此失败。

---

## 59. 不是所有 `turn/steer` rejection 都有 structured data

固定提交中：

```text
NoActiveTurn              → message，data=None
ExpectedTurnMismatch      → message，data=None
ActiveTurnNotSteerable    → data=serialized TurnError
EmptyInput                → message，data=None
TooLarge                  → input limit structured data
```

这说明 structured taxonomy 是渐进式的。

客户端必须能够安全 fallback 到：

```text
code + message
```

而不能假设每个 error 的 `data` 都存在或形状相同。

---

## 60. `core_thread_write_error` 展示了边界映射策略

App-server 调用 Core thread API 失败后，不会把任意 `CodexErr` 原样塞进 JSON-RPC。

它按 details 映射：

```text
ThreadNotFound       → invalid request
InvalidRequest       → invalid request
UnsupportedOperation → method not found
其他                 → internal error
```

这说明同一个 `CodexErr` 在不同出口可能有不同表现：

- 发生在运行中 Turn：变成 `ErrorEvent` / `TurnError`；
- 发生在同步 request handler：变成 JSON-RPC error。

边界和生命周期比 Rust 类型名更重要。

---

## 61. `deserialize_client_request` 的错误是 `-32600`

raw `JSONRPCRequest` 转成 typed `ClientRequest` 失败时，代码使用：

```text
Invalid request: ...
code = -32600
```

它不是 `turn/start` 业务参数校验，而是整个 typed request 识别/反序列化失败。

因此客户端调试时可以先分层：

```text
-32600 → envelope/method payload 无法成为合法 typed request
-32602 → 已进入具体 handler，但参数规则失败
```

---

## 62. `-32603` 不应被客户端无限盲重试

`internal_error` 包装很多服务端内部失败，例如：

```text
failed to update thread settings
failed to deserialize stored item
failed to load thread history
```

其中有些可能瞬时恢复，有些是持久数据或程序错误。

固定提交没有为所有 `-32603` 声明统一 retry contract。

因此稳妥策略是：

- 展示/记录 request ID 和上下文；
- 对只读、幂等操作可做有界重试；
- 对有副作用操作先确认是否已经部分成功；
- 不要无限热循环。

---

## 63. `-32001` 是明确的客户端 retry 契约

App-server README 明确写道：

```text
request ingress saturated
→ error code -32001
→ "Server overloaded; retry later."
→ client should use exponential backoff with jitter
```

这和 Core stream retry 完全不同：

| 情形 | 谁重试 | 重试什么 |
|---|---|---|
| App-server `-32001` | 客户端 | 原 JSON-RPC request |
| `ErrorNotification.willRetry=true` | App-server/Core | 当前 Turn 内部的 stream request |

客户端一定要区分这两条路径。

---

## 64. transport overload 发生在 MessageProcessor 之前

当 incoming bounded queue 满时，transport 尝试直接向该连接的 writer queue 写一条 overload error。

这意味着该 request：

- 没有进入 typed deserialization；
- 没有注册普通 handler context；
- 没有到达 initialized/experimental gate；
- 更没有创建 Turn。

所以 `-32001` 的 retry 单位就是整个原 request。

---

## 65. 连 overload error 本身也可能发不出去

transport 使用 `writer.try_send(...)` 尝试返回 overload error。

如果 writer queue 同样已满或关闭，错误 response 也可能无法交付。

这说明：

> error object 被构造，不等于客户端一定收到。

客户端还需要 transport timeout、disconnect detection 和 request correlation timeout，不能永远等待一个理论上应该存在的 response。

---

## 66. In-process transport 也使用 `-32001`

embedded/in-process 路径有自己的 bounded request 和 server-request queues。

队列满时也返回：

```text
code = -32001
```

message 会更具体，例如：

```text
in-process app-server request queue is full
in-process server request queue is full
```

这保持了不同 transport 的大类恢复语义一致：容量过载应有界退避。

---

## 67. `OutgoingMessageSender::send_error` 终结 request context

App-server 发送 JSON-RPC error 时先：

```rust
let request_context = self.take_request_context(&request_id).await;
```

随后把 `OutgoingMessage::Error` 发往目标 connection。

所以从 server ownership 看：

- response 和 error 都是 request 的 terminal protocol outcome；
- context 只能被 take 一次；
- tracing span 会覆盖到出站 enqueue；
- enqueue 成功仍不证明 peer 已处理。

这与第 25 篇的 delayed response ownership 完全衔接。

---

## 68. App-server 收到 `EventMsg::Error` 后先处理特殊旁路错误

`bespoke_event_handling` 的 Error branch 先记录 thread system error，然后检查：

```text
CodexErrorInfo::ThreadRollbackFailed
```

如果匹配：

- 找到 pending rollback request；
- 返回该 request 的 JSON-RPC invalid request error；
- 清除 pending rollback state；
- 不发送普通 `error` notification。

这说明 Core event 不一定一对一投影成同名 App-server notification。

---

## 69. 非 Turn-fatal ErrorEvent 会被 live projection 忽略

排除 rollback special case 后，代码调用：

```rust
if !ev.affects_turn_status() {
    return;
}
```

例如某个 `ActiveTurnNotSteerable` 型 Core error 不应污染当前 Turn summary。

该 request 通常已经通过自己的 JSON-RPC error 获得结果。

这再次体现：

```text
旁路 request failure ≠ active Turn failure
```

---

## 70. 真正的 ErrorEvent 会同时“记录 summary”和“通知客户端”

App-server 构造：

```rust
TurnError {
    message,
    codex_error_info: mapped_v2_info,
    additional_details: None,
}
```

然后 `handle_error_notification` 做两件事：

1. `turn_summary.last_error = Some(error.clone())`；
2. 发送 `ErrorNotification { will_retry: false, ... }`。

第一步服务最终 Turn projection；第二步服务实时 UI。

---

## 71. StreamError 不写 `turn_summary.last_error`

`EventMsg::StreamError` 分支的注释明确说明：

```text
它是 retry 的中间 error state，
不需要更新 turn summary store，
但需要通知客户端。
```

所以它只发送：

```text
ErrorNotification(will_retry=true)
```

如果客户端自己的 state reducer 看到任何 `error` method 就直接设置 `turn.status=failed`，就会违背服务端语义。

---

## 72. `handle_turn_complete` 怎样决定 completed 或 failed

收到 Core `TurnCompleteEvent` 时，App-server 取走当前 `TurnSummary`。

决策非常直接：

```text
last_error = Some(error)
  → status = Failed
  → error = Some(error)
  → last_agent_message = None

last_error = None
  → status = Completed
  → error = None
  → 可带最后一条 assistant message summary
```

因此 live projection 的 failed 判定依赖此前处理过的 fatal `ErrorEvent`。

同一 thread-scoped event channel 的顺序非常重要。

---

## 73. failed Turn 为什么不附带最后 assistant message summary

失败分支显式返回：

```text
(TurnStatus::Failed, Some(error), None)
```

这避免客户端把失败前的半成品 assistant output 误当成完整最终回答。

流式 item 可能已经显示过部分内容，但最终 Turn summary 不把它包装成成功结果。

---

## 74. interrupted Turn 也会清空 summary，并且没有 error

`handle_turn_interrupted` 同样调用 `find_and_remove_turn_summary`，然后发：

```text
status = Interrupted
error = None
last_agent_message = None
```

因此三个终态清晰互斥：

| status | error | 含义 |
|---|---|---|
| `completed` | `null` | 正常完成 |
| `failed` | `TurnError` | 业务失败 |
| `interrupted` | `null` | 被取消/中断 |

`inProgress` 不是终态。

---

## 75. `turn/completed` 的名字不代表 status 一定是 completed

这是命名上最常见的误区。

method：

```text
turn/completed
```

表示“Turn 生命周期已经结束并发出 completion notification”。

payload 内的：

```text
turn.status
```

才表示结束方式：

```text
completed / failed / interrupted
```

所以客户端应写：

```ts
switch (notification.turn.status) {
  case "completed":
  case "failed":
  case "interrupted":
}
```

而不是看到 method 名就直接把状态设成成功。

---

## 76. durable history 为什么还要重新判断错误

Thread 被 resume/read 时，App-server 不能依赖进程内 `TurnSummary`。

它必须从 rollout 重建：

```text
TurnStartedEvent
ErrorEvent
TurnCompleteEvent
TurnAbortedEvent
Item events
```

所以 `thread_history.rs` 有自己的 stateful projector，而新 paginated rollout 还有 stateless line projection。

实时状态与历史状态必须使用相同 taxonomy，否则重启后 UI 会“变脸”。

---

## 77. history `handle_error` 复用 `affects_turn_status()`

回放遇到 `ErrorEvent` 时：

```text
affects_turn_status=false → 忽略 Turn 状态变化
affects_turn_status=true  → current turn 设为 Failed，并保存 TurnError
```

因此 rollback/steer 旁路错误不会在历史中把主 Turn 标红。

这个方法与 live App-server Error branch 使用同一 semantic predicate，是一致性的关键。

---

## 78. history `handle_turn_complete` 优先看 terminal payload

`TurnCompleteEvent` 自己带 `error` 时，history projector 会：

```text
status = Failed
error = payload.error
```

否则，如果当前状态仍是 `Completed` 或 `InProgress`，才设成 `Completed`。

这种条件保留了先前 `ErrorEvent` 已经设置的 Failed，不会被一个 error=None 的旧式 terminal event 覆盖成成功。

这是兼容混合年代 rollout 的重要防线。

---

## 79. paginated stateless projection 更依赖 terminal record 自包含

新的 `thread_history_projection::project_rollout_line` 一次只看一条 canonical line。

看到 `TurnCompleteEvent` 时：

```text
event.error.is_some() → Failed
否则                 → Completed
```

它不能向前扫描内存 summary。

所以把 terminal error 复制进 `TurnCompleteEvent`，能让增量存储投影更可靠、更低成本。

---

## 80. 恢复语义一：validation error 应修改请求

典型 category：

```text
JSON-RPC -32600
JSON-RPC -32602
CodexErrorInfo::BadRequest
ActiveTurnNotSteerable
input_too_large
```

正确动作通常不是原样 retry，而是：

- 修正 JSON shape；
- 缩短输入；
- 等待 review/compact Turn 结束；
- 使用正确 expected Turn ID；
- 删除无效图片；
- 重新选择合法权限配置。

原样重试确定性 validation failure 只会重复失败。

---

## 81. 恢复语义二：capacity/backpressure 应有界退避

典型信号：

```text
JSON-RPC -32001
Server overloaded; retry later.
```

这里的建议是：

- 客户端负责；
- exponential backoff；
- 加 jitter；
- 设置最大次数或 deadline；
- 重试前确认操作幂等性或 request 是否根本没被接纳。

transport ingress 的 `-32001` 在 handler 前产生，因此原 request 没被业务处理，是相对清晰的重试点。

---

## 82. 恢复语义三：`willRetry=true` 时客户端只观察

客户端伪代码可以是：

```ts
function onErrorNotification(n: ErrorNotification) {
  if (n.willRetry) {
    showTransientReconnect(n.error.message);
    keepTurnInProgress(n.turnId);
    return;
  }

  showTerminalError(n.error);
}
```

最关键的是不要调用：

```ts
startTurnAgain(originalInput)
```

因为 Core 已经在 retry。

---

## 83. 恢复语义四：usage limit 需要等待、充值或换权限

内部多个 details 会归一化为：

```text
CodexErrorInfo::UsageLimitExceeded
```

客户端不应自动高频 retry。

更合理的 UI 动作包括：

- 展示额度或 plan 说明；
- 展示 resetsAt（如果其他 rate-limit notification 提供）；
- 提供购买 credits/升级入口；
- 等待配额恢复后由用户再试。

同一个 client-safe category 可以对应 Core 内部 `UsageLimitReached`、`QuotaExceeded` 或 `UsageNotIncluded`，这正是有损归一化的例子。

---

## 84. 恢复语义五：context window 满通常需要改变上下文

`ContextWindowExceeded` 不是普通网络抖动。

原样发送完全相同的 prompt，通常不会自行恢复。

可能动作是：

- 自动或手动 compaction；
- 新建 Thread；
- 清理较早历史；
- 减少输入和附件；
- 使用支持更大 context 的配置。

Core 同时把 total token state 标为 full，方便其他状态投影知道这不是普通失败。

---

## 85. 恢复语义六：Unauthorized 先修复身份状态

`RefreshTokenFailed` 会映射为：

```text
CodexErrorInfo::Unauthorized
```

合理动作是重新登录、刷新凭证或检查账号状态。

不修复身份就反复重试同一模型 request，只会制造噪声和额外负载。

---

## 86. 恢复语义七：SandboxError 不一定是产品崩溃

`SandboxErr` 可能包含：

- policy denied；
- command timeout；
- signal killed；
- sandbox backend setup failure。

对外统一成 `SandboxError` 后，客户端仍需结合 message 和业务上下文判断：

- 是提示用户调整权限；
- 是让模型改用其他做法；
- 还是环境本身损坏，需要重建。

“sandbox error”不是“绕过 sandbox 再试”的授权。

---

## 87. 恢复语义八：CyberPolicy 是策略终态

API bridge 会识别特定 400 error code 并构造：

```text
CodexErrorDetails::CyberPolicy { message }
```

对外是：

```text
CodexErrorInfo::CyberPolicy
```

它在 sampling `is_retryable()` 中是 false。

客户端应展示政策相关说明，引导用户调整任务，而不是通过自动改写或高频重试尝试规避策略。

---

## 88. 恢复语义九：Interrupted 通常只需要恢复可输入状态

看到：

```text
turn.status = interrupted
error = null
```

客户端可以：

- 停止 spinner；
- 保留已经流出的可见 item；
- 把输入框恢复为可编辑；
- 允许用户发送下一条消息；
- 不展示“请报告系统故障”。

除非同时有独立 transport error，否则中断本身不需要 error recovery。

---

## 89. 一个可执行的客户端决策表

| 收到的信号 | 当前 Turn | 谁恢复 | 建议动作 |
|---|---|---|---|
| JSON-RPC `-32600/-32602` | 通常未开始 | 客户端/用户修正 | 不原样重试 |
| JSON-RPC `-32601` | 通常未开始 | 客户端升级或换 method | 检查 capability/version |
| JSON-RPC `-32001` | 未进入 handler | 客户端 | backoff+jitter 后有界重试 |
| JSON-RPC `-32603` | 不确定 | 视操作而定 | 记录诊断，谨慎有界处理 |
| `error`, `willRetry=true` | 仍 in progress | Core/App-server | 展示重连并继续等待 |
| `error`, `willRetry=false` | 即将失败 | 用户/产品流程 | 展示错误，等待 terminal |
| `turn/completed`, `failed` | 已终结 | 后续新动作 | 按 category 给恢复入口 |
| `turn/completed`, `interrupted` | 已终结 | 无需错误恢复 | 恢复可输入 UI |
| `turn/completed`, `completed` | 已终结 | 无 | 展示最终结果 |

---

## 90. 客户端 reducer 应把 request state 和 Turn state 分开

一个常见错误的数据模型是：

```ts
state.error = anyIncomingError;
state.loading = false;
```

更合理的模型是：

```text
pendingRequests[requestId]
turns[turnId].status
turns[turnId].terminalError
turns[turnId].transientNotice
connection.status
```

这样：

- JSON-RPC error 只终结对应 request；
- transient error 只更新 Turn notice；
- final error 更新 terminal state；
- connection failure 不会被误归因到某一个 Turn。

---

## 91. 不要在收到 fatal notification 时抢先制造另一个终态

`ErrorNotification(willRetry=false)` 后面通常还有 `turn/completed(failed)`。

客户端可以立即展示 error，但最好让 server terminal notification 完成权威收口。

否则容易出现：

- 本地先删除 Turn，后续 completed 找不到目标；
- 本地 duration/completedAt 缺失；
- history reload 后状态与内存不一致；
- analytics/UI 对终态时刻理解不同。

可以先设置：

```text
terminalErrorPendingCompletion
```

再由 `turn/completed` 转为真正 terminal。

---

## 92. request retry 还必须考虑幂等性

即使某个 error 看起来瞬时，也不能默认重发所有 request。

例如：

- list/read 通常更容易安全重试；
- start/create 可能已经在服务端生效，只是 response 丢失；
- tool approval 可能触发真实副作用；
- `turn/start` 重发可能产生重复 Turn。

`-32001` 在 ingress handler 前生成，边界较安全；普通 timeout/disconnect 则无法仅凭“没收到 response”证明服务端没执行。

需要结合 request ID、API 契约和查询能力做 reconciliation。

---

## 93. 为什么 `requestId`、`threadId`、`turnId` 都不能省

三类 ID 的职责不同：

```text
requestId → 哪次协议调用成功或失败
threadId  → 哪个会话容器
turnId    → 哪次业务执行
```

一个 `turn/start` request 成功后，request 已经结束，但 Turn 仍继续数分钟。

后续 `error` notification 没有原 request ID，而是带 thread/turn ID，因为它描述的是业务执行，不再是同步 RPC。

---

## 94. 为什么错误转换要尽量 exhaustive

`CodexErrorInfo` 的 Core→V2 转换使用 exhaustive match。

`CodexErr::is_retryable()` 也显式列出 variant。

好处是新增错误类型时，编译器会逼开发者回答：

- 它是否可由 sampling loop retry？
- 对外映射成什么 category？
- 是否影响 Turn status？
- V2 wire 怎样命名？
- history 怎样恢复？

错误 taxonomy 的真正价值之一，就是把这些隐含产品决策变成必须审查的代码分支。

---

## 95. 测试一：`retryability_preserves_error_details_distinctions`

`protocol/src/error_tests.rs` 明确比较：

```text
ServerOverloaded         → false
RetryLimit               → false
UnexpectedStatus(429)    → true
ToolCollision            → false
InternalServerError      → true
```

它告诉读者：

- 不能只按 HTTP status 猜 retryability；
- `RetryLimit` 已经表示某层 retry budget 耗尽；
- `ServerOverloaded` 可能由别的 workflow 处理，但 sampling loop 不原地 retry。

---

## 96. 测试二：protocol mapping 保留 HTTP status

`to_error_event_handles_response_stream_failed` 构造一个 HTTP 429 stream failure，并断言：

```text
message 带 request ID 和 URL 诊断
codex_error_info = ResponseStreamConnectionFailed {
    http_status_code: Some(429)
}
```

这同时验证了：

- 人类可读 message；
- 机器可读 category；
- 可选 HTTP status payload。

---

## 97. 测试三：camelCase wire shape

App-server protocol 测试验证：

```json
{
  "activeTurnNotSteerable": {
    "turnKind": "review"
  }
}
```

也验证带 HTTP status 的 variant 使用：

```text
httpStatusCode
```

这类 serialization test 看似简单，却能防止 Rust rename 与 TypeScript wire shape 漂移。

---

## 98. 测试四：input limit 同时断言“没有 Turn”

`turn_start_rejects_combined_oversized_text_input` 断言：

- code 是 `INVALID_PARAMS_ERROR_CODE`；
- message 正确；
- `data.input_error_code` 正确；
- `max_chars` 和 `actual_chars` 正确；
- 没有 `turn/started` notification。

最后一条尤其重要。

只测试 error JSON 还不足以证明生命周期没有被部分启动。

---

## 99. 测试五：live completion 的三种状态

`bespoke_event_handling.rs` 的测试分别覆盖：

```text
test_handle_turn_complete_emits_completed_without_error
test_handle_turn_complete_emits_failed_with_error
test_handle_turn_interrupted_emits_interrupted_without_error
```

这些测试验证最终 projection，而不是只验证内部 `ErrorEvent`。

对客户端契约来说，最终 `Turn` shape 往往比某个中间函数更重要。

---

## 100. 测试六：non-fatal error 不影响 Turn status

protocol 内有测试：

```text
rollback_failed_error_does_not_affect_turn_status
active_turn_not_steerable_error_does_not_affect_turn_status
generic_error_affects_turn_status
```

这组测试把“error 是否 fatal”从隐含常识变成显式规则。

如果以后新增旁路操作错误，应先决定它是否影响 active Turn，再补对应测试。

---

## 101. 调试方法：先确定失败发生在哪条时间线

排查 App-server 错误时，先画两条时间线：

```text
Protocol request timeline:
request sent → admitted → handler → response/error delivered

Turn business timeline:
turn/start accepted → turn/started → items → retry/error → turn/completed
```

然后把现象放进去。

例如“客户端收到 error，但 Turn 还在跑”并不矛盾：它可能是 `willRetry=true` 的中间通知，也可能是另一个旁路 request 的 JSON-RPC error。

---

## 102. 调试方法：记录 envelope，而不只记录 message

至少保留：

```text
direction
method 或 request ID
JSON-RPC code
error.data
threadId
turnId
willRetry
turn.status
codexErrorInfo
httpStatusCode
timestamp
```

如果日志只剩一句：

```text
Connection failed
```

就无法知道它是：

- 某个 request 的同步失败；
- 自动重试中的 stream notification；
- 最终 Turn error；
- App-server transport 断线。

---

## 103. 调试方法：检查 error 之后是否还有 terminal event

常见排查顺序：

```text
1. 找到 error notification
2. 看 willRetry
3. 若 true，继续找下一次 retry/success/error
4. 若 false，继续找 turn/completed
5. 校验 completed 的 turnId 是否相同
6. 校验 status/error 与前一通知是否一致
```

如果 `willRetry=false` 后永远没有 terminal，问题可能在：

- Core task 没有正常执行 `on_task_finished`；
- event fan-out 中断；
- connection/router 已关闭；
- 客户端过滤或 correlation 出错。

---

## 104. 调试方法：区分“服务端没发”和“客户端没收到”

可以分层检查：

```text
Core 是否生成 EventMsg
→ App-server 是否完成 projection
→ outgoing channel 是否 enqueue
→ router 是否选中 connection
→ writer 是否实际写出
→ peer 是否解析
→ client reducer 是否应用
```

上一篇 analytics 已经说明，观测 event 也不能证明网络交付。

同理，源码走到 `send_error` 不等于用户界面一定显示了它。

---

## 105. 新增错误类型时的设计清单

新增一个 Core error 前，至少回答：

1. 它是 control flow、domain failure 还是 implementation failure？
2. `Display` 文案是否适合用户，还是只适合日志？
3. sampling loop 是否应该自动 retry？
4. 是否可能携带 server-provided delay？
5. 应映射成已有 `CodexErrorInfo`，还是需要新公开 variant？
6. 是否影响 active Turn status？
7. HTTP status 是否值得稳定传播？
8. V2 camelCase wire shape 是什么？
9. history replay 后状态是否一致？
10. analytics 应使用哪种无 payload kind？
11. 需要 JSON-RPC error，还是 Turn notification？
12. 客户端恢复责任属于谁？

如果这些问题没有答案，说明 error taxonomy 还没设计完。

---

## 106. 一个更完整的客户端处理骨架

下面只是概念伪代码，不是绑定某个 SDK 的可复制实现：

```ts
function onJsonRpcError(id: RequestId, error: JsonRpcError) {
  finishPendingRequest(id, { kind: "error", error });

  if (error.code === -32001) {
    scheduleBoundedRequestRetry(id, {
      strategy: "exponential-backoff-with-jitter",
    });
    return;
  }

  if (error.code === -32600 || error.code === -32602) {
    showRequestCorrection(error.message, error.data);
    return;
  }

  showRequestFailure(error);
}

function onTurnError(n: ErrorNotification) {
  if (n.willRetry) {
    turns[n.turnId].transientNotice = n.error;
    turns[n.turnId].status = "inProgress";
    return;
  }

  turns[n.turnId].pendingTerminalError = n.error;
  showTurnError(n.error);
}

function onTurnCompleted(n: TurnCompletedNotification) {
  const turn = n.turn;
  turns[turn.id] = turn;
  clearTransientNotice(turn.id);
  clearPendingTerminalError(turn.id);

  switch (turn.status) {
    case "completed":
      renderSuccess(turn);
      break;
    case "failed":
      renderFailure(turn.error);
      break;
    case "interrupted":
      renderInterrupted();
      break;
    case "inProgress":
      reportProtocolInvariantViolation();
      break;
  }
}
```

重点不是函数名，而是三条状态通道彼此独立。

---

## 107. 本章词汇表

| 名词 | 中文理解 | 在本文中的准确含义 |
|---|---|---|
| error taxonomy | 错误分类体系 | 按语义、边界、恢复责任和对外稳定性组织错误类型 |
| structured payload | 结构化载荷 | 可由程序稳定读取的 code、enum、data 和字段，而非解析 message |
| recovery policy | 恢复策略 | 谁在什么条件下，以什么单位、次数和退避方式尝试恢复 |
| `ApiError` | API 层错误 | Responses API 返回或解析出的 provider-facing failure |
| `TransportError` | 传输层错误 | HTTP、网络、timeout 或 transport retry limit 等失败 |
| `CodexErr` | Core 错误包装器 | 持有 `CodexErrorDetails` 和可选 retry delay 的统一错误对象 |
| `CodexErrorDetails` | Core 错误详情 | 带 payload 的完整内部语义分类 |
| `CodexErrKind` | 无载荷错误类别 | 去掉敏感/高基数 payload 后供 analytics 使用的 discriminant |
| `CodexErrorInfo` | 客户端安全错误类别 | 跨 Core/App-server 边界传播的稳定产品分类 |
| `ErrorEvent` | 终态错误事件 | 通常会影响当前 Turn status 的 Core event |
| `StreamErrorEvent` | 流中间错误事件 | 自动 retry 过程中的临时 Core event，不终结 Turn |
| `TurnError` | Turn 业务错误 | V2 的 message、category、additional details 组合 |
| `ErrorNotification` | 错误通知 | 带 `willRetry`、thread ID、turn ID 的异步 Turn notification |
| `JSONRPCErrorError` | JSON-RPC 错误主体 | 终结一次 protocol request 的 code/message/data |
| fatal to turn | 对 Turn 致命 | 应让当前 Turn 最终成为 `failed` |
| non-fatal | 非致命 | 某个旁路操作失败，但不应终结 active Turn |
| retry owner | 重试责任方 | 实际重新发起操作的客户端、Core 或专用 workflow |
| retry unit | 重试单位 | 原 RPC、stream sampling、transport connection、review 或 compaction |
| exponential backoff | 指数退避 | 每次失败后按倍数延长等待时间 |
| jitter | 随机抖动 | 在 delay 上加入小范围随机量，避免同步重试风暴 |
| retry budget | 重试预算 | 允许自动尝试的最大次数或时间范围 |
| terminal error | 终态错误 | 已决定本 Turn 无法继续自动恢复的错误 |
| business terminal | 业务终态 | Turn 最终 completed、failed 或 interrupted |
| request terminal | 请求终态 | 一次 JSON-RPC request 收到 result 或 error |
| error projection | 错误投影 | 将 Core error/event 翻译为 V2 notification、Turn 或历史状态 |
| lossy mapping | 有损映射 | 对外转换时主动合并内部类别、丢弃实现细节 |
| discriminator | 判别字段 | 程序用来稳定区分 variant 的 code、enum tag 或 type 字段 |
| reconciliation | 对账/协调 | response 丢失后查询实际状态，避免盲目重复有副作用操作 |
| thundering herd | 惊群 | 大量客户端同一时刻重试，使服务再次过载 |
| ingress overload | 入口过载 | request 尚未进入 handler，incoming queue 已满 |
| fallback transport | 后备传输 | 主 transport 失败后切换到另一 transport，例如 WebSocket 转 HTTPS |
| history replay | 历史回放 | 从 rollout event 重建 Turn status 和 error |

---

## 108. 代码单词和短语拆解

| 代码词语 | 字面拆解 | 放在源码里的意思 |
|---|---|---|
| `retryable` | retry + able | 当前策略认为可以再次尝试，不代表一定成功 |
| `will_retry` | will + retry | 服务端声明自己会重试，客户端不要重复提交 |
| `retry_delay` | retry + delay | 发起下一次尝试前建议等待多久 |
| `max_retries` | maximum + retries | 自动重试次数上限 |
| `backoff` | back + off | 失败后退开一段时间再尝试 |
| `fallback` | fall + back | 主方案失败后退回备用方案 |
| `overloaded` | over + loaded | 当前承载的工作超过容量 |
| `invalid_params` | invalid + parameters | method 已识别，但参数不符合约束 |
| `invalid_request` | invalid + request | 整体 request 无法按协议或业务准入 |
| `internal_error` | internal + error | 服务端内部失败，未稳定细分给客户端 |
| `additional_details` | additional + details | 主 message 之外的底层诊断信息 |
| `affects_turn_status` | affects + turn + status | 该错误是否应把 active Turn 标记为 failed |
| `terminal_error` | terminal + error | 不再自动恢复、随 Turn terminal record 保存的错误 |
| `to_error_event` | to + error + event | 把内部错误转换成 Core protocol event |
| `to_codex_protocol_error` | to + protocol + error | 把内部细类压缩成 client-safe category |
| `response_stream_disconnected` | response + stream + disconnected | 响应流中途断开，目前正在尝试恢复 |
| `too_many_failed_attempts` | too many + failed + attempts | 某层 retry budget 已耗尽 |
| `active_turn_not_steerable` | active turn + not + steerable | 当前 Turn 类型不允许同 Turn 插入输入 |
| `thread_rollback_failed` | thread + rollback + failed | 回滚旁路操作失败，不应污染 active Turn |
| `take_request_context` | take + request + context | 取走 request 的唯一终态上下文，防止重复回包 |
| `handle_error_notification` | handle + error + notification | 记录 fatal summary 并向客户端发错误通知 |
| `find_and_remove_turn_summary` | find + remove + summary | 取走当前 Turn 聚合状态，用于生成唯一终态 |
| `map_api_error` | map + API + error | 把 provider/transport failure 归一化为 Core error |
| `try_switch_fallback_transport` | try + switch + fallback + transport | 尝试从主传输切换到备用传输 |

---

## 109. 理解检查

### 问题 1

客户端收到 `turn/start` 的 JSON-RPC error 后，为什么不能继续等待 `turn/completed`？

<details>
<summary>参考答案</summary>

同步 JSON-RPC error 表示该 request 被拒绝。像输入过长这样的校验发生在 Core submission 之前，Turn 根本没有创建，因此不会有 `turn/started` 或 `turn/completed`。

</details>

### 问题 2

`ErrorNotification.willRetry=true` 时，客户端为什么不应重发 `turn/start`？

<details>
<summary>参考答案</summary>

因为该字段声明 App-server/Core 正在自动恢复当前 Turn 内部的 stream request。客户端重发会创建重复 Turn 或重复副作用，应保持 Turn in progress 并继续读 notifications。

</details>

### 问题 3

为什么 `CodexErr::is_retryable() == false` 不等于用户永远不能重试？

<details>
<summary>参考答案</summary>

该方法只表达当前 sampling retry loop 的策略。用户修正输入、等待额度恢复、换模型、新建 Thread，或其他 workflow 的 model fallback 都是不同恢复上下文。

</details>

### 问题 4

`ActiveTurnNotSteerable` 为什么不影响 Turn status？

<details>
<summary>参考答案</summary>

失败的是旁路 steer request，不是原 active review/compact Turn。把它标成 fatal 会让一个仍正常运行的 Turn 被错误终结。

</details>

### 问题 5

为什么既要 `ErrorEvent`，又要把 error 放进 `TurnCompleteEvent`？

<details>
<summary>参考答案</summary>

`ErrorEvent` 支持实时通知和 stateful summary；terminal record 携带 error 则让 durable/stateless projection 能单独判断最终状态，也提高历史兼容性。

</details>

### 问题 6

为什么 message 不适合决定恢复策略？

<details>
<summary>参考答案</summary>

message 可能改写、本地化、由 provider 产生或包含诊断细节。稳定逻辑应优先使用 JSON-RPC code、`data`、`willRetry`、Turn status 和 `codexErrorInfo`。

</details>

### 问题 7

`turn/completed` method 为什么仍可能携带 `status=failed`？

<details>
<summary>参考答案</summary>

method 名的 completed 表示生命周期已经结束；payload status 才说明结束方式，可以是 completed、failed 或 interrupted。

</details>

### 问题 8

为什么 ingress `-32001` 比普通 connection timeout 更适合直接 retry 原 request？

<details>
<summary>参考答案</summary>

它在 request 进入 MessageProcessor/handler 之前产生，说明业务操作尚未被接纳。普通 timeout 只表示客户端没看到结果，服务端可能已经部分或全部执行，需要幂等性或 reconciliation。

</details>

---

## 110. 源码练习

### 练习 A：给错误分类

在 `CodexErrorDetails` 中任选十个 variant，为每个写出：

```text
内部语义
is_retryable 结果
CodexErrorInfo 映射
是否影响 Turn status
最合理的恢复责任方
```

不要只抄 match，要解释为什么。

### 练习 B：画一次 stream retry 时序

从 `try_run_sampling_request` 返回错误开始，追踪：

```text
is_retryable
handle_retryable_response_stream_error
notify_stream_error
StreamErrorEvent
ErrorNotification(willRetry=true)
下一次 sampling
最终 TurnComplete
```

标注每一步的 owner。

### 练习 C：比较三个结构

把以下结构并排抄写并说明字段为何不同：

```text
JSONRPCErrorError
ErrorEvent
TurnError
```

### 练习 D：验证 wire naming

在生成的 TypeScript schema 中搜索：

```text
CodexErrorInfo
ErrorNotification
TurnError
```

观察 tagged enum 和 camelCase field 的真实形状。

### 练习 E：设计新错误

假设新增“workspace storage temporarily unavailable”：

1. Core details 用什么 variant？
2. sampling 是否重试？
3. App-server request 是否可能用 `-32603`？
4. 是否新增公开 `CodexErrorInfo`？
5. history 中是否应让 Turn failed？
6. UI 提供什么 action？

### 练习 F：找出双重 retry 风险

写一段错误客户端伪代码：收到任何 `error` 都重发 `turn/start`。然后解释它在 `willRetry=true` 场景下如何产生重复 Turn。

---

## 111. 固定提交源码导航

| 要找的内容 | 文件 | 搜索符号 |
|---|---|---|
| API error 归一化 | `codex-rs/codex-api/src/api_bridge.rs` | `map_api_error` |
| HTTP 503 overloaded 特判 | 同上 | `server_is_overloaded`、`slow_down` |
| HTTP 429 usage/retry limit | 同上 | `UsageErrorResponse`、`RetryLimitReachedError` |
| Core error wrapper | `codex-rs/protocol/src/error.rs` | `CodexErr` |
| 内部 error variants | 同上 | `CodexErrorDetails` |
| Analytics discriminant | 同上 | `CodexErrKind` |
| Sampling retry classifier | 同上 | `is_retryable` |
| Client-safe 映射 | 同上 | `to_codex_protocol_error` |
| Error event 构造 | 同上 | `to_error_event` |
| HTTP status 提取 | 同上 | `http_status_code_value` |
| Retry delay | 同上 | `retry_delay`、`with_retry_delay` |
| Client-safe Core enum | `codex-rs/protocol/src/protocol.rs` | `CodexErrorInfo` |
| Fatal predicate | 同上 | `affects_turn_status` |
| Core error event | 同上 | `ErrorEvent` |
| Core stream event | 同上 | `StreamErrorEvent` |
| Retry handler | `codex-rs/core/src/responses_retry.rs` | `handle_retryable_response_stream_error` |
| Transport fallback | 同上 | `try_switch_fallback_transport` 调用 |
| Backoff 实现 | `codex-rs/core/src/util.rs` | `backoff` |
| Provider retry defaults/caps | `codex-rs/model-provider-info/src/lib.rs` | `stream_max_retries` |
| Sampling request loop | `codex-rs/core/src/session/turn.rs` | `is_retryable` 调用处 |
| Invalid image 特判 | 同上 | `InvalidImageRequest` |
| Terminal error capture | `codex-rs/core/src/session/mod.rs` | `send_event` |
| Intermediate stream notification | 同上 | `notify_stream_error` |
| Task terminal reducer | `codex-rs/core/src/tasks/mod.rs` | `on_task_finished` |
| Turn abort classification | 同上 | `CodexErrorDetails::TurnAborted` |
| JSON-RPC envelopes | `codex-rs/app-server-protocol/src/rpc.rs` | `JSONRPCError`、`JSONRPCErrorError` |
| App-server error codes | `codex-rs/app-server/src/error_code.rs` | error code constants |
| V2 client-safe enum | `codex-rs/app-server-protocol/src/protocol/v2/shared.rs` | `CodexErrorInfo` |
| V2 Turn error | `codex-rs/app-server-protocol/src/protocol/v2/thread_data.rs` | `TurnError` |
| V2 error notification | `codex-rs/app-server-protocol/src/protocol/v2/notification.rs` | `ErrorNotification` |
| V2 Turn status | `codex-rs/app-server-protocol/src/protocol/v2/turn.rs` | `TurnStatus` |
| Raw typed request error | `codex-rs/app-server/src/message_processor.rs` | `deserialize_client_request` |
| JSON-RPC error send boundary | `codex-rs/app-server/src/outgoing_message.rs` | `send_error`、`send_error_inner` |
| Input too large payload | `codex-rs/app-server/src/request_processors/turn_processor.rs` | `input_too_large_error` |
| Steer structured rejection | 同上 | `ActiveTurnNotSteerable` branch |
| Core→JSON-RPC mapping | `codex-rs/app-server/src/request_processors/thread_processor.rs` | `core_thread_write_error` |
| Error/StreamError projection | `codex-rs/app-server/src/bespoke_event_handling.rs` | `EventMsg::Error`、`EventMsg::StreamError` |
| Final completion projection | 同上 | `handle_turn_complete`、`handle_turn_interrupted` |
| Rollback special routing | 同上 | `handle_thread_rollback_failed` |
| Ingress overload | `codex-rs/app-server-transport/src/transport/mod.rs` | `OVERLOADED_ERROR_CODE` |
| Client retry documentation | `codex-rs/app-server/README.md` | Backpressure behavior |
| Stateful history projection | `codex-rs/app-server-protocol/src/protocol/thread_history.rs` | `handle_error`、`handle_turn_complete` |
| Stateless history projection | `codex-rs/app-server-protocol/src/protocol/thread_history_projection.rs` | `project_rollout_line` |
| Retry taxonomy tests | `codex-rs/protocol/src/error_tests.rs` | `retryability_preserves_error_details_distinctions` |
| Mapping/status test | 同上 | `to_error_event_handles_response_stream_failed` |
| Fatal predicate tests | `codex-rs/protocol/src/protocol.rs` tests | `does_not_affect_turn_status` |
| V2 serialization tests | `codex-rs/app-server-protocol/src/protocol/v2/tests.rs` | `codex_error_info_serializes_*` |
| Input rejection integration test | `codex-rs/app-server/tests/suite/v2/turn_start.rs` | `turn_start_rejects_combined_oversized_text_input` |
| Completion projection tests | `codex-rs/app-server/src/bespoke_event_handling.rs` tests | `test_handle_turn_complete_*` |

---

## 112. 本篇收束

固定提交中的错误处理不是“捕获异常然后显示字符串”，而是一套跨层状态协议：

- API/provider error 先由 `map_api_error` 归一化为 Core `CodexErr`；
- `CodexErr` 把语义 details 与可选 retry delay 分开；
- `CodexErrorDetails` 保留内部诊断，`CodexErrKind` 提供无 payload analytics 分类；
- `is_retryable` 只代表 sampling workflow 的 retry policy，不是全局真理；
- retry handler 支持 server delay、本地指数退避、jitter 和 WebSocket→HTTPS fallback；
- 自动 retry 使用 `StreamErrorEvent`，App-server 发 `willRetry=true`，不污染 terminal summary；
- 重试耗尽或不可恢复错误变成 `ErrorEvent`，App-server 发 `willRetry=false` 并保存 `last_error`；
- `CodexErrorInfo` 是稳定、较粗、client-safe 的产品分类；
- V2 translation layer 把 Core naming 明确转换成 camelCase wire contract；
- `TurnError` 将 message、structured category 和 additional details 分开；
- JSON-RPC error 终结 request，`error` notification 描述 Turn 过程，`turn/completed` 终结业务；
- input-too-large 在 Core submission 前失败，因此不存在 Turn terminal notification；
- ingress overload `-32001` 明确由客户端使用 backoff+jitter 重试原 request；
- `ActiveTurnNotSteerable` 和 `ThreadRollbackFailed` 是旁路失败，不应把 active Turn 标成 failed；
- `TurnAborted` 投影为 interrupted，而不是错误；
- live summary、terminal event 和 durable history projection 共同保证重启前后状态一致；
- 客户端必须区分 request ID、thread ID、turn ID 和各自的 terminal boundary；
- 恢复策略必须说明责任方、重试单位、预算、退避、修复条件与幂等性。

下一篇适合继续进入“持久化失败与历史修复”：rollout JSONL、state DB、thread store 和 live runtime 各自保存什么；partial write、旧 schema、损坏记录与重放不一致时，Codex 怎样降级、跳过、修复或拒绝恢复。

返回[源码精读目录](README.md)，或查看[课程术语总表](../glossary.md)。
