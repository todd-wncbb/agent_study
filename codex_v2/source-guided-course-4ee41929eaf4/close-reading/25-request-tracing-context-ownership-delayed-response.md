# 源码精读 25：Request tracing、context ownership 与延迟回包

> 源码基线：`4ee41929eaf4`。本文只描述该固定提交中的实现。以后源码移动时，请搜索符号名，不要依赖行号。

上一篇讲完 initialize 以后，一条普通 request 已经可以进入 App-server handler。

但请求进入以后，会立刻出现几个很容易混淆的问题：

- 客户端带来的 `traceparent` 是什么？
- `tracing::Span` 与普通日志有什么区别？
- `RequestContext` 是模型上下文吗？
- handler 已经返回，为什么稍后发送的 response 仍能属于原来的 request span？
- `responsesapi_client_metadata` 看起来也是 metadata，它和 tracing 又有什么关系？
- connection 断开后，谁来清除永远等不到最终回包的 context？

本文要建立的核心心智模型是：

> 一条 incoming request 不只是一次函数调用。它可能跨越排队、Core submission、后台任务和终态事件。`RequestContext` 把 request 的连接级身份与 tracing span 保存到最终 response/error；W3C trace carrier 则把这段本地工作接到调用方和 Core 的分布式 trace 上。

---

## 1. 先给最重要的结论

固定提交中的 request tracing 主线可以压缩成九步：

1. transport 收到带 `id`、`method`、`params` 和可选 `trace` 的 raw request；
2. App-server 创建名为 `app_server.request` 的 server span；
3. 若 wire 上有合法 `traceparent`，把 server span 接到该远端 parent；
4. 以 `(connection_id, request_id)`、span 和原始 parent trace 构造 `RequestContext`；
5. **先注册 context，再执行 request future**；
6. handler、serialization queue 和后台任务用同一个 span 或其后代 span 执行；
7. 需要进入 Core 时，从 request span 导出新的 W3C carrier，放入 Core `Submission`；
8. 最终 `send_response` 或 `send_error` 从 registry 中原子取走 context，并在 request span 下执行出站入队；
9. 如果连接先关闭，connection cleanup 会批量移除该连接遗留的 contexts。

最值得记住的四句话：

> `RequestContext` 不是发给模型的 prompt context。

> handler future 完成，不一定等于 request 已经产生最终 response。

> registry 的终结动作是 `take/remove`，因此 context 的正常寿命由最终 response/error 决定，而不是由入口函数返回决定。

> 在 request span 下成功把 response 放入出站 channel，只证明“发送尝试被追踪且已入队”，不证明客户端已经收到。

---

## 2. 贯穿全文的案例

假设 WebSocket connection 41 上的客户端发送：

```json
{
  "id": 7,
  "method": "turn/start",
  "params": {
    "threadId": "019abc...",
    "input": [
      {"type": "text", "text": "解释这个错误"}
    ],
    "responsesapiClientMetadata": {
      "ticket": "INC-314"
    }
  },
  "trace": {
    "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
    "tracestate": "vendor=example"
  }
}
```

可以先把它想成三条不同的数据线：

```text
调用链追踪线
remote traceparent
  → app_server.request server span
  → Core submission span
  → turn/tool/model 子 spans

请求回包所有权线
(connection 41, request 7)
  → RequestContext registry
  → send_response/send_error 时 remove

业务 metadata 线
responsesapiClientMetadata.ticket = INC-314
  → Op::UserInput
  → Responses API 请求相关 metadata
```

三条线起点在同一个 JSON-RPC request，却有完全不同的用途：

- trace 线回答“这段工作是谁调用的、它又调用了谁”；
- registry 线回答“哪个最终回包属于哪个连接上的哪个 request”；
- Responses API metadata 回答“业务调用需要携带哪些客户端 metadata”。

---

## 3. 第一张源码地图

| 层级 | 固定提交路径 | 重点符号 |
|---|---|---|
| wire trace 类型 | `protocol/src/protocol.rs` | `W3cTraceContext` |
| request wire envelope | `app-server-protocol/src/rpc.rs` | `JSONRPCRequest.trace` |
| span 创建 | `app-server/src/app_server_tracing.rs` | `request_span`、`typed_request_span` |
| parent 解析 | `otel/src/trace_context.rs` | `context_from_w3c_trace_context`、`set_parent_from_w3c_trace_context` |
| request context | `app-server/src/outgoing_message.rs` | `ConnectionRequestId`、`RequestContext` |
| context registry | 同上 | `request_contexts`、register/take/cleanup |
| 入口注册 | `app-server/src/message_processor.rs` | `process_request`、`run_request_with_context` |
| Core trace 传播 | `app-server/src/request_processors` | `request_trace_context`、`submit_with_trace` |
| Core submission | `core/src/codex_thread.rs`、`core/src/session` | `Submission.trace`、dispatch span parent |
| 延迟 thread/start | `app-server/src/request_processors/thread_processor.rs` | background task、delayed response |
| 延迟 interrupt | `app-server/src/request_processors/turn_processor.rs`、`bespoke_event_handling.rs` | `pending_interrupts`、`respond_to_pending_interrupts` |
| 连接清理 | `app-server/src/message_processor.rs` | `connection_closed` |
| trace 集成测试 | `app-server/src/message_processor_tracing_tests.rs` | thread/start 与 turn/start tests |

---

## 4. 先把 log、event、span、trace 分开

这些词常常同时出现在 observability 代码里，但含义不同。

| 名词 | 先说人话 | 典型用途 |
|---|---|---|
| log | 一条文字或结构化记录 | “无法解析 trace carrier” |
| event | 某一时刻发生的事情 | request received、turn completed |
| span | 一段有开始、有结束的工作区间 | 处理一次 `turn/start` |
| trace | 多个有父子关系的 spans 构成的整条调用链 | client → App-server → Core → model/tool |

一个 span 可以包含属性和 events；多个 spans 共享同一个 trace ID，并通过 parent span ID 形成树。

因此 `app_server.request` 不是一条“开始处理”的普通日志，而是承载整个请求工作区间及其父子关系的对象。

---

## 5. W3C Trace Context 解决什么问题

假设客户端、App-server 和 Core 各自只记录本地日志：

```text
client: request 7 took 820 ms
app-server: turn/start took 790 ms
core: user_input took 730 ms
```

人可以猜它们相关，但系统无法可靠地自动拼接。

W3C Trace Context 用标准 carrier 把调用链身份跨边界传递：

```text
traceparent = version-trace_id-parent_span_id-flags
```

本文示例：

```text
00-11111111111111111111111111111111-2222222222222222-01
```

可以教学性地拆成：

| 片段 | 含义 |
|---|---|
| `00` | 格式版本 |
| 32 位十六进制数 | 整条 trace 的 ID |
| 16 位十六进制数 | 直接调用方 span 的 ID |
| `01` | trace flags；通常包含 sampled bit |

不要手写或靠字符串长度自行验证它。当前源码把解析与合法性判断交给 OpenTelemetry 的 W3C propagator。

---

## 6. `tracestate` 是补充，不是 parent 的替代品

类型定义很小：

```rust
pub struct W3cTraceContext {
    pub traceparent: Option<String>,
    pub tracestate: Option<String>,
}
```

两个字段都用 `Option`，但建立 parent 的关键是 `traceparent`。

`context_from_trace_headers` 第一件事是：

```rust
let traceparent = traceparent?;
```

所以只有 `tracestate`、没有 `traceparent`，无法建立远端 parent。

`tracestate` 更像 vendor-specific 补充状态。它必须依附于有效的主 trace context，而不是独立的调用链身份证。

---

## 7. `trace` 是 JSON-RPC envelope 的扩展字段

标准 JSON-RPC 的核心字段通常是 `jsonrpc`、`id`、`method`、`params`。这里额外允许 request 携带 `trace` carrier。

它不是业务 params 的一部分：

```text
request envelope
  ├─ id              回包关联
  ├─ method          调哪个 RPC
  ├─ params          业务参数
  └─ trace           observability 调用链 carrier
```

这使任意 method 都能使用同一套 tracing 入口，而不必给每个 params struct 重复添加 trace fields。

---

## 8. `request_span` 创建统一的 server span

wire request 进入 `process_request` 后，会调用：

```rust
request_span(&request, transport, connection_id, &session)
```

span 模板包含：

```rust
info_span!(
    "app_server.request",
    otel.kind = "server",
    otel.name = method,
    rpc.system = "jsonrpc",
    rpc.method = method,
    rpc.transport = transport,
    rpc.request_id = %request_id,
    app_server.connection_id = %connection_id,
    app_server.api_version = "v2",
    app_server.client_name = field::Empty,
    app_server.client_version = field::Empty,
    turn.id = field::Empty,
)
```

这些字段不是重复装饰，而是在回答不同的检索问题。

---

## 9. span 字段逐个翻译

| 字段 | 字面意思 | 实际用途 |
|---|---|---|
| `app_server.request` | span 内部名称 | tracing subscriber 看到的创建名 |
| `otel.kind=server` | 服务端 span | 表示它接收了一次远端调用 |
| `otel.name=method` | 导出名称 | 导出后通常以 `turn/start` 等 method 展示 |
| `rpc.system=jsonrpc` | RPC 系统 | 表明协议族是 JSON-RPC |
| `rpc.method` | RPC 方法 | 按 `thread/start`、`turn/start` 聚合 |
| `rpc.transport` | 传输方式 | stdio、websocket、unix_socket、off 或 in-process |
| `rpc.request_id` | 客户端 request ID | 单次请求关联；不是全局唯一 |
| `app_server.connection_id` | 连接 ID | 与 request ID 合起来定位请求 |
| `app_server.api_version` | API version 标签 | 当前模板固定记录 v2 |
| `app_server.client_name/version` | 客户端身份 | 初始化参数或已提交 session 提供 |
| `turn.id` | 后来才知道的 Turn ID | `turn/start` 成功提交后补录 |

---

## 10. `field::Empty` 表示“预留以后记录”

`client_name`、`client_version` 和 `turn.id` 创建 span 时可能还不知道，因此模板用 `field::Empty` 预先声明。

之后才能写：

```rust
span.record("turn.id", turn_id);
```

这不是给 span 动态发明任意新字段，而是给预先声明的空槽填值。

`turn.id` 的时序尤其典型：request 刚进入时还没有 Turn；Core 接受 `Op::UserInput` 并返回 submission ID 后，App-server 才能把它补到 request span。

---

## 11. initialize 为什么能在 session 提交前记录 client identity

普通请求可从已初始化 session 读取 client name/version。

但 initialize request 本身发生在 session 提交之前。`request_span` 因此会尝试把 initialize params 单独解析一次，只用于记录 client identity：

```text
如果 method == initialize 且 params 可解析
  → 使用 params.clientInfo
否则
  → 使用 session 中的 client info
```

这次“提前解析”失败时静默返回 `None`；真正的 typed decode 稍后仍会生成协议错误。

换句话说，tracing enrichment 失败不能抢走业务解码的错误所有权。

---

## 12. transport 名称是低成本的重要切片

`transport_name` 将 runtime enum 映射为稳定字符串：

```text
Stdio       → stdio
UnixSocket  → unix_socket
WebSocket   → websocket
Off         → off
typed path  → in-process
```

如果某类延迟只出现在 WebSocket，或者 embedded/in-process 调用没有 wire parent，这个字段使 telemetry 能按 transport 分组，而不必从日志文本猜测。

---

## 13. parent 的优先级

对于 wire request，逻辑可以写成：

```text
如果 request.trace 中存在 traceparent
  尝试把它设为 span parent
否则如果进程环境有合法 TRACEPARENT
  使用环境 parent
否则
  创建没有远端 parent 的本地 trace root
```

这里要注意：“trace object 存在”还不够，代码要求其中 `traceparent` 存在，才把它当成显式 parent carrier。

---

## 14. 一个细微但真实的无效 carrier 行为

如果 request 明确带了非空 `traceparent`，但字符串无效：

- OpenTelemetry 解析返回无效 context；
- `set_parent_from_w3c_trace_context` 返回 false；
- App-server 记录 warning：忽略无效 inbound carrier；
- 该分支不会再回头使用环境 `TRACEPARENT` fallback。

因此优先级不是“第一个合法来源”，而更接近：

```text
显式来源存在 → 只尝试显式来源
显式来源不存在 → 才尝试环境来源
```

这避免一个明确但错误的调用方 carrier 被悄悄替换成进程启动者的 parent，从而产生意外归属。

---

## 15. 环境 parent 被缓存

`traceparent_context_from_env` 使用 `OnceLock<Option<Context>>`。

这意味着：

- 首次读取后结果会缓存；
- 运行中修改 `TRACEPARENT`/`TRACESTATE` 不应被理解为每请求动态切换；
- 无效环境值只会被忽略，并记录 warning。

环境 parent 更适合“这个 App-server 进程整体由某个已追踪父任务启动”的场景。

---

## 16. `ConnectionRequestId` 为什么是二元组

客户端 request ID 只要求在自己的连接语境中可关联。两个客户端完全可以都发送 `id: 7`。

因此 incoming request 的稳定键是：

```rust
ConnectionRequestId {
    connection_id,
    request_id,
}
```

示例：

```text
(connection 41, request 7) ≠ (connection 92, request 7)
```

只用 request ID 作为全服务器 map key，会让两个连接相互覆盖。

---

## 17. `RequestContext` 到底保存什么

源码结构是：

```rust
pub(crate) struct RequestContext {
    request_id: ConnectionRequestId,
    span: Span,
    parent_trace: Option<W3cTraceContext>,
}
```

三个字段分别回答：

| 字段 | 回答的问题 |
|---|---|
| `request_id` | 最终 response/error 应回到哪个连接和哪个 ID？ |
| `span` | 后续工作和回包应进入哪个 request span？ |
| `parent_trace` | 当前 span 无法导出 context 时，还有没有原始 carrier 可回退？ |

它不保存 raw params、不保存完整 prompt、不保存模型 history，也不保存认证 secret。

---

## 18. 这里的 context 不是模型 context

Codex 源码中 `context` 一词有多种含义。

| 名称 | 它是什么 |
|---|---|
| model context | 发给模型的对话历史、instructions、tool results |
| OpenTelemetry context | trace/span 的传播信息 |
| `RequestContext` | App-server incoming request 的 ID + span + parent carrier |
| additional context | 客户端或 Hook 给当前 Turn 增补的业务文本 |

判断方法不是看单词，而是看类型、owner、消费者和生命周期。

---

## 19. `RequestContext::request_trace` 为什么先看 span

实现是：

```rust
span_w3c_trace_context(&self.span)
    .or_else(|| self.parent_trace.clone())
```

优先导出 **当前 App-server request span** 的 context，而不是原封不动继续传远端 parent。

正确的父子关系应是：

```text
remote client span
  └─ App-server request span
       └─ Core submission span
```

如果直接把 remote parent 交给 Core，可能变成：

```text
remote client span
  ├─ App-server request span
  └─ Core submission span
```

这样 Core 与 App-server 会错误地变成兄弟，而不是后者的子工作。

只有当前 span 无法导出有效 context 时，才回退到保存的原始 parent carrier，至少不完全丢掉链路。

---

## 20. 为什么 span 可能导不出 W3C context

`span_w3c_trace_context` 会检查 OpenTelemetry span context 是否有效。

如果 tracing subscriber/provider 没有建立有效 OTEL context，它返回 `None`。这也是 `parent_trace` fallback 存在的原因之一。

因此：

- Rust `tracing::Span` handle 存在；
- 不等于它必然能导出合法 W3C `traceparent`。

本地结构化 tracing 与可跨进程传播的 OTEL context 是相关但不完全相同的层。

---

## 21. register-before-run 是关键时序

统一 helper：

```rust
async fn run_request_with_context(...) {
    outgoing.register_request_context(request_context.clone()).await;
    request_fut.instrument(request_context.span()).await;
}
```

顺序不能随意交换。

若先运行 future：

```text
handler 立即失败
  → send_error
  → registry 还没有 context
  → error send 无法取得原 request span
然后才 register
  → 留下永远不会正常 remove 的 context
```

先注册再运行消除了“快速回包抢在登记之前”的窗口。

---

## 22. typed decode 失败也能被追踪

raw JSON 已经被识别成 request 后，`process_request` 先创建并注册 context，再调用 `deserialize_client_request`。

所以 method/params 的第二阶段 typed decode 即使失败：

```text
context 已注册
  → result = Err(protocol error)
  → send_error
  → take context
  → error enqueue 在 request span 下执行
```

错误路径与成功路径共享相同的 tracing 生命周期，不会只追踪“业务成功”的请求。

---

## 23. `instrument` 不是把 span 数据复制进 future

`request_fut.instrument(request_context.span()).await` 的直观含义是：

> 每次这个 future 被 poll 时，都在该 span 的上下文中执行。

异步 future 可能多次暂停和恢复，也可能换执行线程。`Instrument` 帮助 tracing context 随 future 的 poll 边界恢复，而不是依赖某个 OS thread-local 一直不变。

这也是 async tracing 与简单“函数开头打印 start、结尾打印 end”不同的地方。

---

## 24. clone span handle 不等于复制出另一个 span

`RequestContext` 可以 clone，`span()` 也返回 clone 的 `Span` handle。

这些 clone 通常指向同一个逻辑 span，而不是每 clone 一次就创建一个兄弟 span。

因此同一个 request span 可以同时被：

- registry 保存；
- 当前 handler future 使用；
- background task 使用；
- 最终 send future 使用。

当最后相关 handle 被释放且 span 生命周期结束时，exporter 才能看到完整区间。

---

## 25. registry 的 owner 为什么放在 OutgoingMessageSender

`OutgoingMessageSender` 同时拥有：

- `send_response`；
- `send_error`；
- connection disconnect cleanup；
- `request_contexts` map。

这是一种 owner 对齐：负责所有终结出口的对象，也拥有等待终结的状态。

如果 context map 放在入口 processor，而 response 可由后台 task、event listener 或其他 processor 发送，就会需要跨很多层协调删除，容易泄漏或提前清理。

---

## 26. registry key 与 value

概念结构是：

```text
HashMap<ConnectionRequestId, RequestContext>

(connection 41, request 7)
  ↦ {same key, request span, optional original parent trace}
```

map 的存在并不表示 request 必须一直占着原 handler task。它只表示这条 incoming request 尚未通过 response/error 正常终结，或尚未因断线被清理。

---

## 27. 普通立即回包路径

多数 handler 可以在一次调用里得出 response：

```text
register context
  → handler executes
  → returns Some(response)
  → central dispatcher calls send_response
  → take_request_context
  → response enters outgoing queue under request span
```

这类请求中，handler future 的完成时间与 context removal 很接近，所以不容易看出 registry 的必要性。

延迟回包路径才真正展示它的价值。

---

## 28. `send_response` 的终结动作是 take

核心顺序是：

```rust
let request_context = self.take_request_context(&request_id).await;
let outgoing_message = OutgoingMessage::Response(...);
self.send_outgoing_message_to_connection(
    request_context,
    connection_id,
    outgoing_message,
    "response",
).await;
```

`take` 等于从 map 中 remove 并取得 ownership。

它同时实现两个效果：

1. context 不再被视为未决；
2. 本次发送仍可使用刚取出的 span。

---

## 29. error 使用同一终结模型

`send_error` 也是：

```text
take_request_context
  → construct OutgoingMessage::Error
  → send under context span
```

所以“不成功”不等于“不完成”。一个正式 JSON-RPC error 是请求的一种最终结果，也应消费 request context。

---

## 30. 为什么要先 take，再 await channel send

如果持有 map lock 跨越 channel `.send().await`：

- channel 背压可能让所有 registry 操作一起等待；
- connection cleanup 和其他 responses 可能被无关慢发送阻塞；
- 更容易形成锁顺序问题。

当前实现先短暂锁 map、remove context、释放锁，再执行异步 send。

这是常见模式：共享状态中的 ownership 转移应尽量在短临界区完成。

---

## 31. 第一次 response/error 尝试就消费 context

context 在出站 channel send 之前已经 remove。

如果 channel 随后关闭、send 失败：

- warning 会记录发送失败；
- context 不会自动放回 registry；
- 这条请求对 App-server 来说已经进行过终结发送尝试。

这符合“最终结果只能由一个 owner 发起”的设计，但它不提供可靠消息队列式重试。

---

## 32. context map 不是防止双重 wire response 的保险箱

第二次误调用 `send_response` 时：

- `take_request_context` 得到 `None`；
- 当前代码仍会构造并发送 response；
- 只是第二次 send 不再被原 request span instrument。

因此 registry 能表示和消费 tracing context，却不等于强制协议层“最多发送一次”。

真正避免重复回包仍依赖 response ownership 约定：中央 dispatcher 与专门 processor 必须明确谁负责最终发送。

---

## 33. 重复 in-flight request ID 会发生什么

注册使用 `HashMap::insert`。如果同一 connection 上尚未完成的 request ID 再次被使用：

```text
旧 RequestContext 被新 RequestContext 替换
  → warning: replaced unresolved request context
```

这不是一个完整的 duplicate request rejection gate。

结果是两项并发工作可能竞争同一个关联键，后续 response 的 span 归属会变得含混。客户端应保证同一连接的 in-flight request IDs 不重复。

不要把“服务器记录 warning”理解为“服务器已安全隔离两个同 ID 请求”。

---

## 34. `request_trace_context` 是向下游导出，而不是借出 map entry

processor 需要向 Core 传播 trace 时调用：

```rust
self.outgoing.request_trace_context(request_id).await
```

它锁 map、找到 context、导出 `W3cTraceContext`，然后释放锁。

调用方拿到的是一个小型 carrier value，而不是持有 map guard 或可修改 registry 的引用。这避免 Core 操作跨 await 绑住 App-server 的共享 mutex。

---

## 35. Turn start 怎样把 request span 传入 Core

`turn/start` 构造：

```rust
Op::UserInput {
    items,
    responsesapi_client_metadata,
    additional_context,
    ...
}
```

随后调用：

```rust
thread.submit_user_input_with_client_user_message_id(
    turn_op,
    self.request_trace_context(&request_id).await,
    client_user_message_id,
).await
```

第二个参数才是 tracing carrier。它最终进入 Core `Submission.trace`。

---

## 36. Core `Submission.trace` 的职责

`Submission` 中的字段注释说明它是：

```text
Optional W3C trace carrier propagated across async submission handoffs.
```

也就是“跨异步 submission 交接传播的可选 W3C carrier”。

Core 消费 submission 时，用该 carrier 作为 operation dispatch span 的 parent。于是 App-server 与 Core 即使通过 channel/queue 解耦，trace 树仍能保持连续。

---

## 37. async handoff 为什么必须显式带 carrier

如果 App-server 只在当前 future 上 `.instrument(request_span)`，但随后只是把 `Submission` 放入另一个 channel：

```text
App-server producer task
  → channel
  → Core consumer task
```

Core consumer 可能很久以后、在另一个 task 中 poll。它不能假设 producer 的 current span 会自然穿过任意 channel。

把 W3C carrier 放进消息本身，是让 trace context 与业务消息一起跨 owner 边界。

---

## 38. 显式 trace 缺失时 Core 还有 current-span fallback

Core 的 submission 入口在未显式提供 trace 时，可以从当前 span 导出 context。

这是一层便利 fallback，但 App-server 明确调用 `submit_with_trace` 更能表达跨层意图，也适用于当前 span 不一定自动存在的交接点。

设计上应区分：

- 显式 carrier：消息协议的一部分；
- current span：执行上下文的隐式来源。

---

## 39. `responsesapi_client_metadata` 不是 tracing metadata

这是本文最容易混淆的一组名字。

| 数据 | 去向 | 用途 |
|---|---|---|
| request `trace` | `RequestContext` → Core `Submission.trace` | OpenTelemetry 父子调用链 |
| `responsesapi_client_metadata` | `Op::UserInput` | Responses API 调用的客户端业务 metadata |
| `clientInfo` | connection session 与 span attributes | App-server 客户端身份/capability 语境 |
| model context | history/prompt | 模型实际可见输入 |

示例中的 `ticket=INC-314` 不会自动成为 trace parent；`traceparent` 也不会自动作为业务 metadata 发给模型。

---

## 40. `additional_context` 也不是 trace context

`additional_context` 会影响当前 Turn 的输入语义，可能对模型可见。

W3C trace context 只用于 observability correlation，通常由 telemetry 系统消费。

简单判断：

```text
它回答“模型还应知道什么”？ → additional/model context
它回答“这段工作从哪次调用派生”？ → trace context
```

---

## 41. 为什么要晚一点记录 `turn.id`

request span 创建时只知道 JSON-RPC request ID：例如 7。

Turn ID 是 Core 接收 `Op::UserInput` 后产生的 submission ID。成功返回后：

```rust
self.outgoing
    .record_request_turn_id(&request_id, &turn_id)
    .await;
```

这使 telemetry 可以同时用两种身份检索：

- `rpc.request_id=7`：连接协议层身份；
- `turn.id=...`：Codex 业务实体身份。

两者不能互相替代。

---

## 42. 晚记录存在合理的 no-op 边界

`record_request_turn_id` 如果找不到 context，会静默不做任何事。

可能原因包括连接已经清理、请求已被提前终结，或错误的生命周期调用。

它不会为了 telemetry enrichment 阻止正常业务继续，也不会重新创建一个已经终结的 context。

这体现了一个优先级：可观测性关联很重要，但不能反向成为业务结果的唯一生存条件。

---

## 43. 延迟回包案例一：`thread/start`

`thread/start` 不是中央 dispatcher 立即构造 response 的普通模式。

简化时序：

```text
process_request
  → register RequestContext
  → thread_start validates/prepares
  → spawn background thread_start_task under request span
  → handler returns None
  → central dispatcher does not send response
  ... background work continues ...
  → thread created
  → background task calls send_response
  → context removed
  → thread/started notification emitted
```

这里 `None` 表示专门 processor 接管回包，不表示“没有 response”。

---

## 44. handler 返回后，为什么 span 没丢

至少有两类 owner 仍保留 span handle：

- `request_contexts` registry 中的 `RequestContext`；
- 被 `.instrument(request_context.span())` 的 background future。

所以入口 future 结束不等于逻辑 request span 已经无 owner。

这就是“函数调用生命周期”与“协议请求生命周期”必须分开的原因。

---

## 45. `thread/start` background task 先捕获 parent trace

thread 创建需要把 request trace 继续传给新 Core Session/Thread 的启动逻辑。

processor 在 context 仍可用时取得：

```text
request_context.request_trace()
  → StartThreadOptions.parent_trace
  → Session::spawn 的 tracing parent
```

于是新 thread 的初始化工作成为当前 App-server request 的后代，而不是孤立 trace。

---

## 46. background response 仍在 request span 下

`thread_start_task` 最终发送 response 时，既有外层 background future 的 instrumentation，又有 outgoing sender 在取出 context 后对 send future 的 instrumentation。

源码还为发送阶段建立了更具体的 child span，例如：

```text
app_server.thread_start.send_response
```

这让 trace 可以区分：

- thread 创建本身耗时；
- response 发送阶段耗时；
- `thread/started` notification 阶段耗时。

---

## 47. `thread/start` 的 notification 与 response 不同

response 通过 request ID 完成一问一答；`thread/started` 是服务器通知。

它们可以处于同一 request trace 的父子结构中，但协议意义不同：

```text
response(id=7)          → 回答原 request
thread/started          → 告知订阅者/连接状态变化
```

notification 不会从 incoming request context registry 中单独 take 一个条目，因为它没有自己的 client request ID 和最终 reply obligation。

---

## 48. 延迟回包案例二：`turn/interrupt`

中断请求的“收到”与“Turn 已经到达终态”不是一回事。

普通路径会把 `ConnectionRequestId` 放入 thread state：

```rust
thread_state.pending_interrupts.push(request_id.clone());
```

然后向 Core 提交 interrupt，但暂不回复客户端。

只有 listener 观察到 `TurnComplete` 或 `TurnAborted` 等终态时，才会调用 `respond_to_pending_interrupts`。

---

## 49. pending interrupt 保存 ID，request registry 保存 span

这里有两份不同状态：

```text
ThreadState.pending_interrupts
  保存：哪些 interrupt requests 等待终态确认

OutgoingMessageSender.request_contexts
  保存：这些 requests 的 tracing context 和连接身份
```

前者决定“何时可以回应”；后者决定“回应时使用哪个 span，并怎样找到连接级 request”。

不要因为两边都含 request ID，就认为它们重复。

---

## 50. 终态事件怎样批量完成 interrupts

helper 先在锁内：

```rust
std::mem::take(&mut state.pending_interrupts)
```

然后释放 thread state lock，再逐个：

```rust
outgoing.send_response(request_id, TurnInterruptResponse {}).await;
```

每个 response 都会从全局 request context registry 中 take 自己的 context。

先整体 take 再 async send，同样避免持有 thread mutex 跨 await。

---

## 51. interrupt span 的持续时间表达什么

它不只是“调用 `Op::Interrupt` 花了多久”，而更接近：

> 从客户端请求中断，到 App-server 观察到 Turn 终态并开始发出确认 response 的协议等待区间。

这是一种很有价值的 end-to-end latency 定义。若只在 handler 返回时结束 span，会把最关键的等待时间漏掉。

---

## 52. 延迟回包不是无限许可

把 context 留到未来终态，并不自动保证未来一定有终态。

实现仍需要：

- Turn complete/aborted 的 listener path；
- connection close cleanup；
- shutdown/timeout 等异常收口；
- 明确的 response owner。

request registry 是生命周期载体，不是自动完成业务状态机的魔法。

---

## 53. connection close 时为什么必须清 registry

客户端断开后，某些 delayed requests 可能永远没有机会把 response 交给原连接。

`OutgoingMessageSender::connection_closed` 会：

```rust
request_contexts.retain(|request_id, _| {
    request_id.connection_id != closed_connection
});
```

这批量释放该连接所有未决 request contexts，防止内存和 span handle 无期限残留。

---

## 54. cleanup 的顺序：先关 gate，再等 active RPC

connection cleanup 不会一收到 close 就立即清 contexts。固定提交的大意是：

```text
rpc_gate.close
  → 最多等待 active handlers drain
  → outgoing.connection_closed 清 request contexts
  → 继续清 fs/command/process/thread connection state
```

这样已取得 gate token 的 handler 有机会自然完成并在 context 仍存在时发送 response/error。

---

## 55. drain 有 30 秒边界

cleanup 对 RPC gate shutdown 使用超时。

因此：

- 及时完成的 active handler 可正常 take context；
- 超过等待上限后，cleanup 仍会继续；
- context 被清后，迟到 response 找不到原 span；
- connection 本身已经关闭，迟到消息也没有正常交付目标。

bounded shutdown 避免一个卡死 handler 永远阻塞连接资源回收。

---

## 56. queued 与 active request 的差别

上一篇讲过 connection RPC gate：

- active handler 已取得 TaskTracker token；
- queued handler 可能还没开始执行 body；
- gate close 后 queued body 可以不被 poll。

但 raw request 的 context 已在更早入口注册。因此 close cleanup 最终仍要移除那些没有机会进入业务 body 的 contexts。

这是 registry 不能只依靠 handler 自己 Drop 清理的原因之一。

---

## 57. background task 与 gate tracking 不是同一个 owner

`thread/start` handler 可以启动自己的 background task 后返回。原 handler token 随后释放，但 background task 由 thread processor 的 task tracker 管理。

所以 connection RPC gate drain 并不必然等待所有从 handler 派生的长寿后台工作。

connection cleanup 可能先移除 context，而后台 thread creation 继续执行。稍后发送 response 时：

- registry 返回 `None`；
- outbound connection 已不存在；
- 发送不再证明能交付给原客户端。

这不是“context 使后台任务永不停止”，而是 tracing ownership 与业务 cancellation ownership 仍是两回事。

---

## 58. response send span 不等于交付确认

`send_outgoing_message_to_connection` 追踪的是：

```rust
self.sender.send(OutgoingEnvelope::ToConnection { ... }).await
```

这只是把 envelope 送入全局 outbound router channel。

上一篇已建立的边界仍成立：

```text
构造 response
  → 入全局 queue
  → router 找 connection
  → 入 per-connection writer queue
  → serialize/write
  → peer receives
  → peer applies
```

request span 覆盖到发送尝试，不等于覆盖到客户端 ACK。

---

## 59. connection 已断开时的迟到 response

一种可能时序：

```text
client disconnects
  → router removes writer
  → request context eventually purged
  → background task completes
  → send_response sees no context
  → envelope enters/global router or send fails
  → no live connection consumes it
```

因此 telemetry 中看到“background work completed”或“response enqueue attempted”，不能推导出客户端已收到结果。

---

## 60. 两张 map 千万不要混淆

`OutgoingMessageSender` 里还有 `request_id_to_callback`。

| map | 方向 | key | 等待什么 |
|---|---|---|---|
| `request_contexts` | client → server request | `(connection_id, client request_id)` | App-server 最终 response/error |
| `request_id_to_callback` | server → client reverse request | server-generated request ID | 客户端 response/error/cancel |

前者保存 request span；后者保存 oneshot callback、thread ID 和可重放的 server request。

“pending request map”这种泛称不足以判断方向，必须看 payload 与终结者。

---

## 61. 为什么 server request ID 可以全局分配

reverse request ID 由 App-server 的 `AtomicI64` 产生，并可能广播给多个 connections；首个客户端 response 消费唯一 callback。

incoming client request ID 则由每个客户端自行选择，所以必须加 connection scope。

两套 ID 策略反映的是两种不同 authority：

- 谁创建 ID；
- 谁保证唯一性；
- response 从哪个方向回来。

---

## 62. in-process request 复用同一种 span shape

embedded caller 不经过 JSON text envelope，但 `process_client_request` 仍会：

- 创建 `typed_request_span`；
- 构造 `ConnectionRequestId`；
- 注册 `RequestContext`；
- 用同一个 `handle_client_request`；
- 由 response/error take context。

因此 stdio、WebSocket 与 in-process telemetry 可以用相近字段比较。

---

## 63. in-process 的两个主要差异

第一，`rpc.transport` 是 `in-process`。

第二，typed request 本身没有 wire `trace` field 供这里复制，因此 `RequestContext.parent_trace` 为 `None`。

`typed_request_span` 仍可使用进程环境 `TRACEPARENT` fallback；如果调用方在同进程已有 active span，其他提交层也可能从 current span 导出，但不能把它与 wire carrier 路径混为一谈。

---

## 64. test client 怎样自动注入 trace

固定提交的 App-server test client 在 client request span 作用域内：

```rust
request.trace = current_span_w3c_trace_context();
```

这展示了标准传播模式：

```text
client 当前 span
  → 导出 W3C carrier
  → 放入 request envelope
  → server 解析并设为 parent
```

调用方一般不应手工拼接 trace IDs，而应从 tracing/OpenTelemetry context 导出。

---

## 65. 测试一证明了什么：thread/start

`thread_start_jsonrpc_span_exports_server_span_and_parents_children` 同时验证：

- 没有远端 trace 时也会导出 server span；
- 有远端 carrier 时，server request span 使用相同 trace ID；
- server span 的 parent span ID 等于远端 parent；
- parent 被标记为 remote；
- request span 下存在内部 descendant spans；
- 延迟的 notify-started 工作仍属于同一 trace。

这不是只断言“有一条日志”，而是在验证父子拓扑。

---

## 66. 测试二证明了什么：turn/start

`turn_start_jsonrpc_span_parents_core_turn_spans` 验证：

- `turn/start` server span 接到远端 parent；
- Core `codex.op=user_input` span 与它共享 trace ID；
- Core span 是 request span 的后代；
- request span 的 `turn.id` 等于 response 中返回的 Turn ID。

它把 wire request、App-server、Core submission 与业务 Turn 身份串成了一条可查询链。

---

## 67. 单元测试证明 context 会在 response 后清除

`OutgoingMessageSender` tests 包含“发送 response 会清除已注册 request context”的断言。

另一测试验证 `connection_closed` 会清除该连接的 contexts。

这些测试证明的是 registry ownership 与 cleanup，不等同于证明 transport 已把 bytes 写给 peer。

测试命名和断言层级必须与结论匹配。

---

## 68. 怎样从 trace 树定位慢在哪里

假设 client 看到 `thread/start` 需要 4 秒：

```text
remote client span: 4.1s
└─ app-server thread/start: 4.0s
   ├─ config/model setup: 0.2s
   ├─ thread spawn: 3.5s
   ├─ send_response: 0.01s
   └─ notify_started: 0.02s
```

可初步判断主要时间在 thread spawn，而不是 outbound enqueue。

如果 server span 很短、client span 很长，则要继续检查请求到达前、物理 transport 写回、客户端 event loop 或网络边界。

---

## 69. 怎样诊断 trace 断链

按顺序检查：

1. client request envelope 是否真的带 `trace.traceparent`；
2. carrier 是否符合 W3C 格式；
3. server 是否记录 `ignoring invalid inbound request trace carrier`；
4. request span 是否有有效 OTEL context；
5. processor 是否通过 `request_trace_context`/`submit_with_trace` 传到 Core；
6. Core consumer 是否用 submission trace 建 parent；
7. 是否在跨 task/channel 时只依赖了 current span，却没有显式 carrier；
8. exporter 是否已 flush，采样策略是否保留该 trace。

“没在 UI 里看到 span”不一定是代码没创建，也可能是 provider、sampling 或 export 时序问题。

---

## 70. 怎样诊断 request context 泄漏

先寻找这些终结路径：

- 成功是否调用 `send_response`；
- 失败是否调用 `send_error`；
- 专门 processor 返回 `None` 后，未来事件是否保证发送；
- connection close 是否一定调用 outgoing cleanup；
- background task panic/cancel 时是否留下没有终结者的 request；
- 重复 request ID 是否替换了旧 context。

测试中可用 registry count 辅助；生产代码更适合用 pending 数量 metric、长寿 span 或 warning 检测异常，而不是把内部 map 暴露为公共 API。

---

## 71. 怎样诊断“回了两次”

重点不是问 context map 为什么没挡住，而是沿 response ownership 查：

```text
handler return 是 Some、None 还是 Err？
central dispatcher 是否会回？
专门 processor 是否也显式回？
终态 listener 是否可能重复触发？
pending IDs 是否先 take/drain 再 await？
```

`Some(response)` 通常代表中央 owner；`None` 通常代表专门 owner。违反这个合同才是双回包的常见根因。

---

## 72. 怎样诊断“response 有了，但 trace 没关联”

可能原因包括：

- context 在连接 cleanup 时已被移除；
- 相同 in-flight ID 被新 request 替换；
- 某路径绕开统一 `send_response/send_error`；
- span 没有有效 OTEL context，且原 parent 也没有；
- background future 没有 instrument；
- exporter 在最后 span handle drop 前就被检查。

先判断是“业务回包错误”还是“telemetry correlation 错误”，两者的修复位置不同。

---

## 73. ID 的四个层级

本文至少出现四种身份：

| ID | 作用域 | 谁创建 |
|---|---|---|
| connection ID | transport connection | App-server transport |
| JSON-RPC request ID | 一条连接的一问一答 | 调用方 |
| trace ID/span ID | observability 调用树 | tracing/OpenTelemetry |
| thread ID/turn ID | Codex 业务实体 | Core |

它们应通过字段关联，不应强行复用为同一种 ID。

例如 request ID 可以是 7，但它不是 Turn 7，也不是 trace ID 的缩写。

---

## 74. trace context 的安全与隐私边界

trace carrier 是不可信客户端输入：

- 必须解析验证；
- 无效值被忽略而不是导致业务 panic；
- 不应把 traceparent 当认证凭据；
- trace ID 只能用于关联，不能证明调用方身份；
- tracestate 可能含 vendor 数据，应受 telemetry 数据治理约束。

“能把 span 接到某 trace”不等于“已授权该调用”。

---

## 75. 高基数字段要怎样理解

`rpc.request_id`、connection ID、thread ID、turn ID 的可能取值很多，属于高 cardinality 属性。

它们很适合单请求追踪和故障关联，却不适合毫无控制地作为所有 metrics label 做大规模聚合，否则可能造成时序数据库维度爆炸。

当前代码把它们记录到 spans；读 telemetry 设计时仍要区分 span attributes 与 metric dimensions。

---

## 76. 一个完整时序图：立即 `turn/start`

```text
Client
  │ request id=7 + remote trace
  ▼
MessageProcessor
  │ create server span, attach remote parent
  │ register RequestContext[(41,7)]
  ▼
TurnProcessor
  │ export request span as W3C carrier
  │ submit Op::UserInput + carrier
  ▼
Core queue/consumer
  │ create child operation span
  │ return turn_id=T9
  ▼
TurnProcessor
  │ record turn.id=T9 on request span
  │ return response
  ▼
OutgoingMessageSender
  │ remove RequestContext[(41,7)]
  │ enqueue response under request span
  ▼
Outbound router / writer / Client
```

---

## 77. 一个完整时序图：延迟 `turn/interrupt`

```text
Client request (41,8)
  → create/register RequestContext
  → record target turn.id
  → pending_interrupts.push((41,8))
  → submit interrupt to Core
  → handler returns None

RequestContext remains pending

Core emits TurnAborted/TurnComplete
  → listener takes all pending_interrupts
  → send_response((41,8))
  → registry removes context
  → response enqueue runs under original request span
```

这里真正的 acknowledgement barrier 是 Turn 终态，而不是 interrupt op 入队。

---

## 78. 一个完整时序图：连接先断开

```text
delayed request registered
  → connection closes
  → RPC gate stops new work
  → wait active handlers up to timeout
  → purge all contexts for connection
  → background completion arrives later
  → no request context / no live writer
```

清理优先保证资源有界，不承诺断开的客户端还能收到迟到 response。

---

## 79. 常见误解一：span 就是计时器

span 确实能表示 duration，但它还承载：

- trace ID 与 span ID；
- parent 关系；
- server/internal 等 kind；
- method、transport、client、turn 等 attributes；
- 跨 async/task/process 的传播起点。

只把它理解成计时器，会看不懂为什么代码需要 W3C carrier、registry 和 `Instrument`。

---

## 80. 常见误解二：handler return 就是 RPC 完成

对立即回包 handler 常常近似成立；对 `thread/start`、`turn/interrupt` 明确不成立。

正确问题是：

> 谁拥有最终 response/error？它在什么业务 barrier 后调用 outgoing sender？

函数返回只是 Rust control flow，最终回包才是 JSON-RPC lifecycle terminal。

---

## 81. 常见误解三：context map 保证 exactly-once

它没有：

- 拒绝 duplicate IDs；
- 阻止第二次 response enqueue；
- 持久化到磁盘；
- 在进程重启后恢复；
- 等待客户端 ACK；
- 自动重试 send failure。

它主要保证 tracing/correlation state 能从入口活到正常终结或断线 cleanup。

---

## 82. 常见误解四：有相同 trace ID 就一定是直接父子

同一个 trace 可以有很多兄弟、祖先和后代 spans。

测试不仅比较 trace ID，还检查：

- server span 的 `parent_span_id`；
- parent 是否 remote；
- Core span 是否从 request span 向下可达。

判断拓扑必须同时看 trace ID 和 parent chain。

---

## 83. 常见误解五：traceparent 能替代业务 ID

trace ID 适合 observability；thread/turn/request IDs 适合协议和业务状态。

sampling、export、跨系统支持程度都会影响 trace 是否可见，所以业务正确性不能依赖 telemetry backend 中一定存在某条 trace。

业务 ID 应继续出现在 response、events 和持久状态中。

---

## 84. 设计新延迟回包 method 时的检查表

1. 入口是否在任何快速错误前注册 context？
2. central dispatcher 是 `Some` 回包，还是 handler 返回 `None` 转交 ownership？
3. 延迟 owner 保存的是完整 `ConnectionRequestId` 吗？
4. 成功与失败是否都最终调用统一 outgoing sender？
5. pending state 是否在 await 前先原子 take/drain？
6. connection close/shutdown 时怎样清理？
7. background future 是否在 request span 下 instrument？
8. 向 Core/其他 task 过 channel 时是否显式携带 trace？
9. duplicate/late terminal event 是否可能导致二次回包？
10. 测试是否检查 parent topology、context cleanup 和真正的业务 barrier？

---

## 85. 本章词汇表

| 代码词/短语 | 字面翻译 | 本篇中的具体含义 |
|---|---|---|
| tracing | 追踪 | 记录 spans、events、属性和调用关系的可观测性机制 |
| OpenTelemetry / OTEL | 开放遥测 | 跨库/系统表达与导出 trace、metric、log 的生态标准 |
| W3C Trace Context | W3C 追踪上下文 | 用 traceparent/tracestate 跨边界传播调用链身份的标准 |
| carrier | 载体 | 装载 trace 字符串并跨 wire/channel 传递的小数据结构 |
| propagation | 传播 | 从当前 span 导出 carrier，并在下游恢复 parent 的过程 |
| span | 跨度/工作区间 | 一段有父子关系和属性的可追踪工作 |
| trace | 追踪链 | 共享 trace ID 的 span 树 |
| parent span | 父 span | 直接发起当前工作区间的上游 span |
| remote parent | 远端父级 | 通过进程/transport 边界传来的 parent |
| span context | span 上下文 | trace ID、span ID、flags、tracestate 等传播信息 |
| `traceparent` | 追踪父级 | W3C 主 carrier，包含 trace ID 与 parent span ID |
| `tracestate` | 追踪状态 | vendor-specific 补充传播状态，不能独立建立 parent |
| `RequestContext` | 请求上下文 | incoming request 的 connection-scoped ID、span 与 parent fallback |
| context ownership | 上下文所有权 | 谁保存 context，直到谁用 response/error/cleanup 终结它 |
| `ConnectionRequestId` | 连接请求编号 | connection ID + client request ID 的复合键 |
| registry | 注册表 | 尚待最终回包的 request contexts map |
| register | 登记 | 在 handler 执行前把 context 放入 map |
| take | 取走 | 从 map remove 并取得 value ownership |
| instrument | 加入追踪作用域 | future 每次执行时进入指定 span |
| enrichment | 信息增补 | 稍后向已有 span 记录 client info 或 turn ID |
| delayed response | 延迟回包 | handler 先返回，由后台任务或终态事件以后发送 response |
| response ownership | 回包所有权 | 哪个组件唯一负责发送最终 response/error |
| submission handoff | 提交交接 | 业务消息通过 channel 从 App-server owner 转给 Core owner |
| fallback | 回退来源 | 首选 span context 无效时使用原 parent carrier |
| cardinality | 基数 | 某字段可能拥有多少种不同值 |
| exporter | 导出器 | 把完成 spans 送往测试 collector 或 telemetry backend 的组件 |
| force flush | 强制刷新 | 要求 provider 尽快导出尚未批量发送的 spans |
| descendant | 后代 span | 通过一条或多条 parent 边从某 span 派生的 span |
| acknowledgement barrier | 确认屏障 | 满足后才能诚实回复“该动作已达到所承诺状态”的边界 |
| `responsesapi_client_metadata` | Responses API 客户端元数据 | Turn 业务请求携带的 metadata，不是 W3C tracing carrier |

---

## 86. 代码单词拆解

### `span_w3c_trace_context`

```text
span                 某个工作区间
w3c_trace_context     标准传播格式
```

合起来：从指定 span 导出可跨边界发送的 W3C context。

### `set_parent_from_w3c_trace_context`

```text
set_parent            给当前 span 设置父级
from                  来源是
w3c_trace_context     W3C carrier
```

返回 boolean，是因为输入 carrier 可能无效。

### `record_request_turn_id`

```text
record                向已存在字段写入属性
request               哪条 RPC request
turn_id               后来得到的业务 Turn ID
```

它不是创建 Turn，也不是修改 response，只是补充 telemetry 关联。

### `send_outgoing_message_to_connection`

```text
send                  发送/入队
outgoing_message      出站 typed message
to_connection         目标是一个特定连接
```

函数名没有 `write` 或 `ack`，不要把它扩张解释成物理交付完成。

---

## 87. 理解检查

### 练习一

为什么 `request_trace()` 优先导出 App-server request span，而不是直接返回客户端传来的 parent？

<details>
<summary>参考答案</summary>

下游 Core 工作应成为 App-server request span 的子孙。直接继续传远端 parent 会让 Core 与 App-server 可能变成兄弟 span。只有当前 span 无法导出有效 context 时才回退到原 parent。

</details>

### 练习二

为什么 `run_request_with_context` 必须先 register，再运行 future？

<details>
<summary>参考答案</summary>

handler 可能立即成功或失败并发送最终结果。若先执行，send 会找不到 context，之后的迟到 register 反而泄漏。先登记关闭这段竞态窗口。

</details>

### 练习三

`thread/start` handler 返回 `None` 是否表示客户端不会收到 response？

<details>
<summary>参考答案</summary>

不是。`None` 表示中央 dispatcher 不回包，专门 processor 已把 ownership 转交给 background task；后者完成 thread 创建后调用统一 send_response。

</details>

### 练习四

为什么 `pending_interrupts` 和 `request_contexts` 都需要存在？

<details>
<summary>参考答案</summary>

前者属于 thread 状态机，记录哪些 interrupt 要等 Turn 终态；后者属于 outgoing owner，保存这些 incoming requests 的 tracing context 和 connection-scoped identity，供最终发送和断线清理。

</details>

### 练习五

`send_response().await` 成功能否证明客户端应用了 response？

<details>
<summary>参考答案</summary>

不能。这里至多证明 envelope 成功进入全局 outgoing channel；后面还有 router、per-connection queue、serialization、transport write、peer receive 和应用处理等边界。

</details>

### 练习六

只有 `tracestate` 没有 `traceparent`，能否建立远端 parent？

<details>
<summary>参考答案</summary>

不能。当前解析函数要求 traceparent 存在且合法；tracestate 只是补充状态。

</details>

### 练习七

为什么 JSON-RPC request ID、trace ID 和 Turn ID 不能合并成一个 ID？

<details>
<summary>参考答案</summary>

它们由不同 authority 创建、作用域不同、生命周期不同：request ID 服务连接协议关联，trace ID 服务遥测调用树，Turn ID 服务 Codex 业务实体。通过字段相关联比复用更安全。

</details>

---

## 88. 源码导航

| 阅读目标 | 固定提交路径 | 重点符号 |
|---|---|---|
| W3C carrier struct | `codex-rs/protocol/src/protocol.rs` | `W3cTraceContext` |
| Core submission carrier | 同上 | `Submission.trace` |
| Span 导出 carrier | `codex-rs/otel/src/trace_context.rs` | `span_w3c_trace_context` |
| 当前 span 导出 | 同上 | `current_span_w3c_trace_context` |
| Carrier 解析 | 同上 | `context_from_w3c_trace_context`、`context_from_trace_headers` |
| 设置 span parent | 同上 | `set_parent_from_w3c_trace_context` |
| 环境 fallback | 同上 | `traceparent_context_from_env`、`load_traceparent_context` |
| Tracestate merge | 同上 | `merge_tracestate_entries` |
| Server span template | `codex-rs/app-server/src/app_server_tracing.rs` | `app_server_request_span_template` |
| Wire request span | 同上 | `request_span` |
| In-process span | 同上 | `typed_request_span` |
| Parent 优先级 | 同上 | `attach_parent_context` |
| Initialize identity enrichment | 同上 | `initialize_client_info`、`record_client_info` |
| Compound request key | `codex-rs/app-server/src/outgoing_message.rs` | `ConnectionRequestId` |
| Request context fields | 同上 | `RequestContext` |
| Span-first carrier export | 同上 | `RequestContext::request_trace` |
| Turn ID late record | 同上 | `record_turn_id`、`record_request_turn_id` |
| Context registry | 同上 | `request_contexts` |
| Register/replace warning | 同上 | `register_request_context` |
| Response terminal take | 同上 | `send_response_as_inner` |
| Error terminal take | 同上 | `send_error` |
| Send instrumentation | 同上 | `send_outgoing_message_to_connection` |
| Disconnect cleanup | 同上 | `connection_closed` |
| Reverse callback map 对照 | 同上 | `request_id_to_callback`、`PendingCallbackEntry` |
| Wire request entry | `codex-rs/app-server/src/message_processor.rs` | `process_request` |
| Typed entry | 同上 | `process_client_request` |
| Register-before-run | 同上 | `run_request_with_context` |
| Connection cleanup order | 同上 | processor `connection_closed` |
| Request admission/gate owner | `codex-rs/app-server/src/lib.rs` | transport event loop、RPC gate |
| Turn trace helper | `codex-rs/app-server/src/request_processors/turn_processor.rs` | `request_trace_context`、`submit_core_op` |
| Turn start propagation | 同上 | `submit_user_input_with_client_user_message_id` |
| Turn ID enrichment | 同上 | `record_request_turn_id` |
| Interrupt pending ID | 同上 | `pending_interrupts.push` |
| Interrupt delayed responses | `codex-rs/app-server/src/bespoke_event_handling.rs` | `respond_to_pending_interrupts` |
| Thread trace helper | `codex-rs/app-server/src/request_processors/thread_processor.rs` | `request_trace_context`、`submit_core_op` |
| Thread start handoff | 同上 | `thread_start_inner`、background task spawn |
| Thread creation parent | 同上 | `StartThreadOptions.parent_trace` |
| Delayed response child span | 同上 | `app_server.thread_start.send_response` |
| Started notification child span | 同上 | `app_server.thread_start.notify_started` |
| Core submission API | `codex-rs/core/src/codex_thread.rs`、`codex-rs/core/src/session/mod.rs` | `submit_with_trace`、submission enqueue |
| Submission dispatch parent | `codex-rs/core/src/session/handlers.rs` | `submission_dispatch_span` |
| Session spawn parent | `codex-rs/core/src/session/mod.rs`、`thread_manager.rs` | parent trace validation 与 span parent |
| Test client injection | `codex-rs/app-server-test-client/src/lib.rs` | `write_request` |
| Trace integration harness | `codex-rs/app-server/src/message_processor_tracing_tests.rs` | `RemoteTrace`、in-memory exporter |
| Thread start trace topology | 同上 | `thread_start_jsonrpc_span_exports_server_span_and_parents_children` |
| Turn start/Core topology | 同上 | `turn_start_jsonrpc_span_parents_core_turn_spans` |
| Context response cleanup test | `codex-rs/app-server/src/outgoing_message.rs` tests | `send_response_clears_registered_request_context` |
| Context disconnect cleanup test | 同上 | `connection_closed_clears_registered_request_contexts` |

---

## 89. 本篇收束

固定提交把一条 incoming request 的可观测性和回包生命周期组织成了一个清晰的 owner 模型：

- wire `trace` 用 W3C carrier 把客户端调用链带进 App-server；
- `request_span` 建立统一 server span，并记录 method、transport、connection、client 等属性；
- 合法显式 parent 优先于进程环境 fallback，无效显式 carrier 被警告并忽略；
- `ConnectionRequestId` 用 connection scope 消除不同客户端 request ID 冲突；
- `RequestContext` 只保存 request identity、span 与 parent fallback，不是模型上下文；
- context 在 handler 前注册，使快速 success/error 也能正确终结；
- request future、serialization/handler 和 background task 可复用同一个 span handle；
- 下游 Core 通过 `Submission.trace` 显式跨 async channel 继续父子链；
- `responsesapi_client_metadata`、additional context、client info 与 trace context 各有不同消费者；
- Turn ID 在 Core 接受 submission 后补录到 request span；
- `thread/start` 与 `turn/interrupt` 证明 handler return 和协议 request terminal 可以分离；
- response/error 通过 `take_request_context` 同时完成 registry removal 和 traced send；
- context map 不拒绝重复 ID，也不阻止第二次 wire response，最终正确性仍靠 response ownership；
- connection close 在有界 drain 后批量清理遗留 contexts；
- request span 下的 channel send 只到达出站入队边界，不等于 peer delivery；
- 测试检查的是 trace ID、remote parent、descendant topology、Turn ID enrichment 和 registry cleanup，而不只是日志文字。

下一篇适合继续精读 connection teardown：RPC gate、后台任务、pending callback、thread subscription、进程与 writer 分别由谁关闭，为什么清理顺序会决定有没有泄漏、迟到副作用或卡死 shutdown。

返回[源码精读目录](README.md)，或查看[课程术语总表](../glossary.md)。
