# 源码精读 23：Incoming transport、envelope 分类与 handler 准入

> 源码基线：`4ee41929eaf4`。本文只描述该固定提交中的实现。以后源码移动时，请搜索符号名，不要依赖行号。

上一篇沿着发送方向，追踪了一条 typed 消息怎样经过 outbound router 和 transport writer。

这一篇把箭头反过来：客户端送来一行 JSONL 或一个 WebSocket text frame 后，服务器怎样判断它是什么、能不能接、应该交给谁？

```text
bytes / WebSocket frame
  → 一条消息的物理边界
  → JSON 解析
  → 四类 envelope 分类
  → connection 存活检查
  → request typed decode
  → initialize / experimental gate
  → serialization queue
  → connection RPC gate
  → typed handler
  → response / error
```

本篇最想消除一个误解：

> “JSON 能解析”只表示外层信封看起来合法，远远不表示里面的方法存在、参数正确、连接已初始化，更不表示 handler 已经开始执行。

---

## 1. 先给最重要的结论

固定提交把 incoming 路径拆成多道职责不同的门：

1. transport 确定消息边界；
2. Serde 把 JSON 分成 request、notification、response 或 error；
3. 主循环确认 `connection_id` 仍然存在；
4. request 再由 `ClientRequest::try_from` 变成具体 typed variant；
5. 除 `initialize` 外的请求必须通过已初始化与实验能力检查；
6. 有资源冲突的请求进入 serialization queue；
7. 真正执行前再经过每连接的 `ConnectionRpcGate`；
8. exhaustive `match` 把 typed request 交给具体 processor；
9. dispatcher 根据 `Some(response)`、`None` 或 `Err(error)` 决定谁负责回包。

其中最容易混淆的是三个词：

| 名称 | 它解决什么问题 | 它不解决什么问题 |
|---|---|---|
| initialize gate | 客户端是否先完成握手 | 同资源请求是否冲突 |
| serialization queue | 同一资源上的请求以什么顺序执行 | 连接断开后是否还准许启动 |
| connection RPC gate | 断线后禁止尚未开始的 handler 启动，并跟踪已开始 handler | 不负责业务资源互斥 |

一句话记忆：

> initialize gate 检查“你有没有报到”，serialization queue 安排“你何时轮到”，RPC gate 最后确认“你的连接现在还准不准开工”。

---

## 2. 贯穿全文的案例

假设 WebSocket 连接 A 依次发送：

```json
{"id":1,"method":"thread/start","params":{}}
{"id":2,"method":"initialize","params":{"clientInfo":{"name":"study-client","version":"1.0"}}}
{"id":3,"method":"thread/resume","params":{"threadId":"T"}}
```

第一条消息：

- 是合法 JSON；
- 外形是 request；
- method 也能解码成 typed request；
- 但连接还没初始化；
- 因此返回 `-32600`，消息是 `Not initialized`。

第二条消息：

- 被识别为特殊的 `ClientRequest::Initialize`；
- 不经过“必须已经初始化”的检查；
- 校验 client info，写入一次性的 connection session state；
- 发送 initialize response；
- 主循环再发布该连接的初始化通知与发送侧能力状态。

第三条消息：

- 通过 typed decode 与初始化门；
- `thread/resume` 计算出 thread T 的 serialization scope；
- 进入 T 对应的 FIFO 队列；
- 轮到它时，先由 A 的 RPC gate 判断是否仍允许启动；
- 若 A 尚存活，进入 thread handler；若 A 已断开，则 future 被直接丢弃而不执行 handler body。

这三条消息说明：同样是“服务器读到一段 JSON”，其最终命运可能完全不同。

---

## 3. 第一张源码地图

| 层级 | 固定提交路径 | 重点符号 |
|---|---|---|
| wire envelope | `app-server-protocol/src/rpc.rs` | `JSONRPCMessage`、四种 struct、`RequestId` |
| transport 公共入口 | `app-server-transport/src/transport/mod.rs` | `forward_incoming_message`、`enqueue_incoming_message` |
| stdio reader | `app-server-transport/src/transport/stdio.rs` | `lines()`、`start_stdio_connection` |
| WebSocket reader | `app-server-transport/src/transport/websocket.rs` | `run_websocket_inbound_loop` |
| connection 主循环 | `app-server/src/lib.rs` | `TransportEvent::IncomingMessage` match |
| raw→typed request | `app-server-protocol/src/protocol/common.rs` | `ClientRequest`、`TryFrom<JSONRPCRequest>` |
| request 协调 | `app-server/src/message_processor.rs` | `process_request`、`handle_client_request` |
| 初始化 | `app-server/src/request_processors/initialize_processor.rs` | `InitializeRequestProcessor::initialize` |
| handler 准入 | `app-server/src/message_processor.rs` | `dispatch_initialized_client_request` |
| 资源排队 | `app-server/src/request_serialization.rs` | queue key、access、`QueuedInitializedRequest` |
| 断线执行门 | `app-server/src/connection_rpc_gate.rs` | `run`、`close`、`shutdown` |
| typed 分派 | `app-server/src/message_processor.rs` | `handle_initialized_client_request` |

---

## 4. 先分清 framing 与 parsing

`framing` 回答：一条消息从哪里开始，到哪里结束。

`parsing` 回答：这条消息里的字符能不能解释成目标数据结构。

例如：

```text
{"id":1,"method":"initialize",...}\n
```

stdio 的 newline 解决 framing；`serde_json::from_str` 才解决 parsing。

如果没有 framing，连续两段 JSON：

```text
{"id":1,...}{"id":2,...}
```

接收方甚至不知道该把哪些 bytes 交给一次 JSON 解析。

---

## 5. stdio：一行就是一个 payload

stdio reader 使用：

```rust
let reader = BufReader::new(stdin);
let mut lines = reader.lines();
```

随后每次 `next_line()` 得到一条不含结尾换行符的 `String`，交给 `forward_incoming_message`。

因此 stdio 协议是 JSONL：

```text
第一行 JSON
第二行 JSON
第三行 JSON
```

它不是“从一个任意 JSON 字节流里自动找对象边界”。

---

## 6. 一条 stdio 行里不能放两个请求

下面虽然含有两个各自合法的 JSON object，但整行不是一个合法 JSON value：

```text
{"id":1,"method":"initialize"}{"id":2,"method":"thread/start"}
```

正确写法是两个物理行：

```text
{"id":1,"method":"initialize","params":{...}}
{"id":2,"method":"thread/start","params":{...}}
```

这也是为什么 stdout writer 在序列化结果后显式 `push('\n')`。

---

## 7. WebSocket：一个 text frame 是一个 payload

WebSocket 自身已经保存 message boundary。

reader 对 `IncomingWebSocketMessage::Text(text)` 调用同一个 `forward_incoming_message`。因此从 JSON 层开始，stdio 与 WebSocket 复用同一条管线。

区别只在物理 framing：

| transport | 一条 payload 的边界 |
|---|---|
| stdio | newline |
| WebSocket | text frame |

不要在一个 WebSocket text frame 内塞两条相邻 JSON object，也不要假设一条 JSON 可以拆成多个 text frame 后由这里自动拼接。

---

## 8. WebSocket control frame 不进入 JSON-RPC

inbound loop 还会见到：

- `Ping`：尝试向 control queue 放一个 Pong；
- `Pong`：忽略；
- `Close`：退出 reader；
- binary：记录 warning 后丢弃；
- receive error：记录 warning 后退出。

这些是 WebSocket transport 层事件，不是 JSON-RPC envelope。

如果 Pong control queue 已满，代码会关闭连接，而不是无限等待。这里优先保护连接任务不会被 control traffic 卡死。

---

## 9. `forward_incoming_message` 是 JSON 入口

核心逻辑可以简化为：

```rust
match serde_json::from_str::<JSONRPCMessage>(payload) {
    Ok(message) => enqueue_incoming_message(..., message).await,
    Err(err) => {
        log(err);
        true
    }
}
```

返回 `true` 表示 reader 可以继续；`false` 表示负责接收 processor events 的 channel 已关闭，reader 应结束。

---

## 10. malformed input 在固定提交中的真实行为

如果 payload：

- 不是合法 JSON；或
- 是合法 JSON，但不能匹配四种 `JSONRPCMessage` shape；

固定实现会：

1. 记录反序列化错误；
2. 不创建 `TransportEvent::IncomingMessage`；
3. 不发送 JSON-RPC parse error；
4. 返回 `true`，让连接继续读取后续消息。

所以不要凭标准印象推断“坏 JSON 一定返回 `-32700`”。本篇描述的是固定源码，而不是理想化的完整 JSON-RPC 2.0 server。

---

## 11. 它并不是真正的 JSON-RPC 2.0 envelope

`rpc.rs` 顶部明确说明：这里既不发送，也不期待：

```json
{"jsonrpc":"2.0"}
```

常见请求实际长这样：

```json
{"id":7,"method":"thread/read","params":{"threadId":"T"}}
```

要注意措辞：这表示协议不依赖、也不主动产生 `jsonrpc` 字段；不要进一步编造“多带这个字段一定被拒绝”，因为当前 Serde struct 没有使用 `deny_unknown_fields`。

---

## 12. 四类 envelope

```rust
#[serde(untagged)]
pub enum JSONRPCMessage {
    Request(JSONRPCRequest),
    Notification(JSONRPCNotification),
    Response(JSONRPCResponse),
    Error(JSONRPCError),
}
```

`untagged` 表示 wire 上没有额外的 Rust variant tag。Serde 根据字段形状尝试分类。

| envelope | 关键字段 | 意义 |
|---|---|---|
| Request | `id` + `method` | 对端要求服务器回答 |
| Notification | `method`，无 `id` | 对端不等待回答 |
| Response | `id` + `result` | 对端成功回答服务器此前发出的反向请求 |
| Error | `id` + `error` | 对端以错误回答反向请求 |

---

## 13. envelope 分类不是看 method 名

下面是 request：

```json
{"id":9,"method":"does/not/exist","params":{}}
```

即使 method 不存在，它仍然能先被识别成 `JSONRPCMessage::Request`。

“这是 request”与“这是受支持的 ClientRequest”属于两次不同的解码：

```text
JSON object
  ──外层 shape──> JSONRPCRequest
  ──method + params──> ClientRequest::<具体 variant>
```

---

## 14. request ID 可以是字符串或整数

`RequestId` 是 untagged enum：

```rust
pub enum RequestId {
    String(String),
    Integer(i64),
}
```

所以两种都合法：

```json
{"id":17,"method":"initialize","params":{...}}
{"id":"init-17","method":"initialize","params":{...}}
```

服务器回包要保留对应 ID，以便客户端把答案配回原请求。

---

## 15. 为什么 notification 没有 ID

notification 的协议含义就是“不期待 response”。

固定提交当前不期待客户端发送 notification；`process_notification` 只记录日志，不进入 typed business handler。

因此下面这条即使外形合法：

```json
{"method":"some/client/event","params":{"x":1}}
```

也不会像 request 一样返回 method-not-found error。没有 ID，本来也没有一个等待答案的请求需要闭合。

---

## 16. response 与 error 是另一条方向的答案

App-server 有时会主动向客户端发请求，例如审批或用户输入请求。

客户端回来的是：

```json
{"id":42,"result":{"decision":"accept"}}
```

或：

```json
{"id":42,"error":{"code":-1,"message":"cancelled"}}
```

它们不是新的 client request，而是服务器先前 reverse request 的答案。`process_response` / `process_error` 会通知 pending callback owner。

这条生命周期已在[源码精读 21](21-server-request-pending-response-lifecycle.md)展开。

---

## 17. transport event 为消息补上 connection identity

解析成功后，transport 创建：

```rust
TransportEvent::IncomingMessage {
    connection_id,
    message,
}
```

wire request ID 是客户端自己选的，可能多个连接都使用 `1`。`connection_id` 是服务器内部生成的连接身份。

因此真正唯一标识一条 incoming client request 的是：

```text
(connection_id, request_id)
```

---

## 18. incoming channel 为什么有界

transport 到 processor 的 channel 容量是 128。

有界队列的目标是：当 processor 跟不上 reader 时，内存占用不能随着输入无限增长。

但“满了怎么办”不能对所有 envelope 一刀切，因为它们的协议职责不同。

---

## 19. 入队先使用 `try_send`

`enqueue_incoming_message` 先尝试非阻塞入队：

```text
try_send(event)
  ├─ success → 继续读取
  ├─ channel closed → 结束 reader
  └─ channel full → 按 envelope 分类处理
```

这样正常情况下 reader 不必等待；拥塞时才进入专门策略。

---

## 20. queue Full + Request：快速拒绝

如果满的是一个新的 request，transport 不等待 processor 腾位，而是尝试直接向该连接的 writer queue 放入：

```json
{
  "id": "原请求 ID",
  "error": {
    "code": -32001,
    "message": "Server overloaded; retry later."
  }
}
```

这样做有三个好处：

1. 不让 reader 被新增工作持续阻塞；
2. 客户端拿到明确、可重试的失败；
3. 请求没有半进入 processor，不会以后突然执行。

---

## 21. overload error 自己也可能发不出去

transport 对 writer queue 也使用 `try_send`。

如果 writer queue 同样 Full：

- overload response 被丢弃；
- 记录 warning；
- reader 仍继续。

这揭示一个现实边界：系统过载到输入与输出队列都满时，无法保证把“我过载了”可靠告诉对端。

测试 `enqueue_incoming_request_does_not_block_when_writer_queue_is_full` 专门证明这里不会反过来卡住 reader。

---

## 22. queue Full + Response/Error：不能随便丢

非 request 的 Full 分支会：

```rust
transport_event_tx.send(event).await
```

也就是等待队列出现空间。

特别是 response/error，可能是客户端对服务器反向请求的唯一答案。随手丢弃会让审批、用户输入或其他 callback 无法按正常响应路径完成，只能等取消或生命周期清理收口。

所以这里选择传播 backpressure，而不是快速丢弃。

---

## 23. notification 也走等待分支

当前代码的 generic non-request 分支同时覆盖 notification。虽然 `process_notification` 目前只记录日志，它仍会在 incoming queue Full 时等待入队。

这是“当前分支形状的事实”，不应夸大成“客户端 notification 一定拥有可靠交付语义”。代码只是没有在 transport 满载路径里将它单独丢弃。

---

## 24. 三个过载测试分别证明什么

| 测试 | 它真正证明的性质 |
|---|---|
| `enqueue_incoming_request_returns_overload_error_when_queue_is_full` | Full request 会尝试产生同 ID 的 `-32001` error |
| `enqueue_incoming_response_waits_instead_of_dropping_when_queue_is_full` | Full response 在有空间前等待，之后成功入队 |
| `enqueue_incoming_request_does_not_block_when_writer_queue_is_full` | overload response 队列也 Full 时，reader 不被阻塞 |

测试名不是装饰；它们恰好固定了三种不同的背压合同。

---

## 25. 主 processor loop 是 connection state 的 owner

`lib.rs` 的主循环依次处理：

- `ConnectionOpened`；
- `ConnectionClosed`；
- `IncomingMessage`。

每个 open event 会建立 `ConnectionState`，其中包含：

- connection origin；
- `ConnectionSessionState`；
- 给 outbound router 使用的 initialized atomic；
- experimental capability atomic；
- notification opt-out projection。

这张 connection map 是 incoming message 能否继续的第一道逻辑门。

---

## 26. unknown connection 为什么直接丢弃

四类 envelope 分支都会先确认 `connection_id` 仍存在。

若 transport close event 已经让主循环移除了连接，随后迟到的 incoming event 会被记录并丢弃。

这是一道世代/存活围栏：

> 已经不再拥有 connection state 的消息，不能重新激活该连接，也不能借尸还魂进入 handler。

request 在这里不会收到 error，因为向一个已经不存在的 connection 回包没有可靠目标。

---

## 27. 四种 envelope 在主循环里分叉

```text
Request      → process_request
Response     → process_response
Notification → process_notification
Error        → process_error
```

只有 Request 会继续经历 typed `ClientRequest` 解码、initialize gate、experimental gate、serialization scope 与 handler dispatch。

Response/Error 要尽快去闭合已有 callback；Notification 目前只记日志。

---

## 28. 主循环串行，不等于所有 handler 串行

主循环逐个读取 `TransportEvent`，因此 connection open/close 与 request admission 的状态转换有清晰顺序。

但初始化后的普通 request 在准入后：

- 有 scope：进入 per-resource queue 的 drainer task；
- 无 scope：直接 `tokio::spawn`。

所以 `process_request(...).await` 返回时，常常只表示请求已完成解码和准入，handler 可能仍在后台运行。

例外是 initialize：它在当前调用路径中内联完成，包括发送 response 和写入 session state。

---

## 29. `process_request` 先组合连接与 request ID

```rust
let request_id = ConnectionRequestId {
    connection_id,
    request_id: request.id.clone(),
};
```

假设 A、B 两个连接都发送 `id: 1`：

```text
(A, 1) ≠ (B, 1)
```

响应必须回到发起它的那个 connection，而不是只靠客户端自选 ID 做全局查找。

---

## 30. request context 在 typed decode 前注册

`process_request` 还会创建 `RequestContext`：

- connection-scoped request ID；
- tracing span；
- 可选 W3C parent trace。

`run_request_with_context` 先把 context 注册到 outgoing owner，再运行 decode/dispatch future。

这样即使 typed decode 失败，返回的 error 也能关联同一个 request context；延迟回包的 handler 也能延续该 span。

---

## 31. 外层 request 与 typed request 是两种类型

第一层类型：

```rust
JSONRPCRequest {
    id: RequestId,
    method: String,
    params: Option<Value>,
    trace: Option<W3cTraceContext>,
}
```

它只知道 method 是任意字符串，params 是任意 JSON value。

第二层类型：

```rust
ClientRequest::ThreadResume {
    request_id,
    params: ThreadResumeParams,
}
```

它已经知道方法类别和参数字段的 Rust 类型。

---

## 32. `ClientRequest` 由宏集中定义

`client_request_definitions!` 的每个条目同时声明：

- Rust variant；
- wire method 名；
- params 类型；
- serialization scope；
- response 类型；
- 可选 experimental 标记。

例如：

```rust
ThreadResume => "thread/resume" {
    params: v2::ThreadResumeParams,
    serialization: thread_or_path(params.thread_id, params.path),
    response: v2::ThreadResumeResponse,
}
```

这不是只为少写代码。它把 method、参数、回包和并发规则放在同一份协议 DSL 中，减少手工表之间漂移。

---

## 33. `TryFrom<JSONRPCRequest>` 怎样做 typed decode

宏生成的转换大致做四步：

1. 拆出 `request_id`、`method`、`params`；
2. 重建一个 JSON object；
3. 把 `id`、`method` 与可选 `params` 放进去；
4. 让 Serde 反序列化为 tagged `ClientRequest` enum。

因为 `ClientRequest` 使用 `method` 作为 tag，method 选择 variant；variant 中的 params 类型继续校验参数。

---

## 34. unknown method 与 invalid params 在这里汇合

下面两类失败都会让 `ClientRequest::try_from` 返回 Serde error：

```json
{"id":8,"method":"unknown/method","params":{}}
```

```json
{"id":9,"method":"thread/read","params":{"threadId":123}}
```

`deserialize_client_request` 把它统一映射为：

```text
code: -32600
message: "Invalid request: <serde error>"
```

注意：虽然源码定义了 `-32601 METHOD_NOT_FOUND` 与 `-32602 INVALID_PARAMS` helper，当前这条通用 typed decode 路径使用的是 `invalid_request(-32600)`。

---

## 35. 缺少 params 是否成功由具体类型决定

`TryFrom` 只有在原 request 含 params 时才把 `params` 放进重建 object。

随后能否成功，不由外层 `Option<Value>` 单独决定，而由具体 `ClientRequest` variant 的 Serde shape 决定。

因此不要背一条过度简单的规则，例如“params 永远必填”或“params 永远可省”。应检查该 method 对应的 Params 类型与 serde annotation。

---

## 36. typed decode 失败不会进入初始化门

处理顺序是：

```text
raw JSONRPCRequest
  → ClientRequest::try_from
  → 成功后 handle_client_request
  → initialize / initialized gate
```

所以一个 method 拼错的请求，不会先得到 `Not initialized`；它会先在 typed decode 层得到 `Invalid request: ...`。

顺序会影响客户端看到的错误，也会影响你排查问题时从哪层开始。

---

## 37. initialize 是唯一绕过“已初始化”检查的请求

`handle_client_request` 首先做模式匹配：

```rust
if let ClientRequest::Initialize { request_id, params } = codex_request {
    initialize_processor.initialize(...).await?;
    return Ok(());
}
```

所有其他 variant 才进入 `dispatch_initialized_client_request`。

如果把“已初始化检查”放在这个 if 之前，客户端将永远无法发送第一条 initialize；这就是顺序为何必须如此。

---

## 38. connection session 使用 `OnceLock`

`ConnectionSessionState` 包含：

```rust
initialized: OnceLock<InitializedConnectionSessionState>
```

初始化成功后保存：

- experimental API 是否启用；
- opt-out notification methods；
- client name/version；
- request attestation；
- client MCP extensions。

`OnceLock` 表达的不是“以后可以反复覆盖配置”，而是“连接从未初始化单向转为已初始化”。

---

## 39. duplicate initialize 怎样失败

initialize processor 先检查 `session.initialized()`；写入 `OnceLock` 时还再次处理竞争失败。

两处都会形成：

```text
code: -32600
message: "Already initialized"
```

第二道防线很重要：只做“先看一眼”不是原子的，真正的 `OnceLock::set` 才决定谁成功提交。

---

## 40. initialize 先验证，再提交 session state

例如 `clientInfo.name` 必须能成为合法 HTTP header value。

代码先验证它，再调用 `session.initialize(...)`。若验证失败，连接仍处于未初始化状态，客户端可以修正参数后重试。

这体现了常见事务顺序：

```text
解析 → 验证 → 构造完整状态 → 一次提交 → 外部副作用
```

---

## 41. initialize response 不是发送侧完全放行的最后一步

WebSocket/stdio 主循环在发现 session 从未初始化变成已初始化后，还会：

1. 镜像 opt-out methods；
2. 镜像 experimental flag；
3. 发送 connection-specific config warnings；
4. 发送 remote control status；
5. 向 thread processor 注册 connection capabilities；
6. 最后把 outbound `initialized` atomic 设为 true。

因此“session 已提交”和“普通广播开始可见”是两个有意分开的时刻。

上一篇详细解释了这个发送侧发布顺序；本篇只需记住：initialize 不是一个孤立 boolean。

---

## 42. 普通请求的第一道门：必须 initialized

`dispatch_initialized_client_request` 首先检查：

```rust
if !session.initialized() {
    return Err(invalid_request("Not initialized"));
}
```

这发生在计算 serialization scope 和 spawn handler 之前，因此未初始化请求不会偷偷进入资源队列。

集成测试 `connection_handling_websocket` 明确断言了 `Not initialized`。

---

## 43. 第二道门：experimental capability

若 typed request 或其中某个字段带 experimental reason，而该连接没有在 initialize capabilities 中启用 experimental API，则返回 invalid request。

这是一项 per-connection capability：

```text
同一 app-server
  ├─ connection A opted in  → 可提交实验请求
  └─ connection B 未 opted in → 同样 method 被拒绝
```

能力检查也发生在入队和 handler 启动之前。

---

## 44. 准入成功后才记录 initialized request analytics

代码先通过 initialized 与 experimental checks，再调用 `track_initialized_request`。

因此这项 analytics 更接近“通过连接级协议门的 typed request”，而不是“transport 看见的所有 JSON”。

读指标时必须知道采样点在哪一层，否则会把 malformed、unknown method 或 pre-init rejection 误算成 handler traffic。

---

## 45. serialization scope 是资源冲突声明

typed request 可返回：

```rust
Option<ClientRequestSerializationScope>
```

例如：

- `thread/resume` → thread ID 或 path；
- config write → global `config`；
- config read → global shared-read `config`；
- process 操作 → connection + process handle；
- 某些无冲突请求 → `None`。

scope 回答“它与哪些请求不能乱序并发”，不是“它属于哪个连接”。

---

## 46. protocol scope 还要转成运行时 queue key

`RequestSerializationQueueKey::from_scope(connection_id, scope)` 会给 connection-local resource 补上 connection ID。

例如两个连接都使用 process ID `p1`：

```text
(A, p1) 与 (B, p1)
```

应是不同进程命名空间，不能被错误地塞进同一队列。

而 thread ID 和 global config 本来就是跨连接共享资源，因此不增加 connection namespace。

---

## 47. 有 scope 与无 scope 的分叉

```text
Some(scope)
  → 算 key/access
  → enqueue
  → per-key drainer 以后调用 request.run()

None
  → tokio::spawn
  → task 立即调用 request.run()
```

两条路线最终都经过 `QueuedInitializedRequest::run`，所以两者都受 connection RPC gate 约束。

---

## 48. serialization queue 只保证同 key 的顺序

固定实现对每个 key 建立独立队列和 drainer：

- 同 key Exclusive 请求 FIFO；
- 相邻 SharedRead 可以成批并发；
- 排在 Exclusive 后面的 read 不能插队；
- 不同 key 可以并发。

完整算法和 writer fairness 已在[源码精读 08](08-request-serialization-scope.md)展开。本篇关心的是它处在“通过协议门”之后、“进入 RPC gate”之前。

---

## 49. 为什么排队后还要一个 RPC gate

想象：

1. A 的 request R2 已进入 thread T 队列；
2. R1 正占用 T，R2 尚未开始；
3. A 断开；
4. R1 完成，队列轮到 R2。

若只有 serialization queue，R2 仍会执行业务副作用，却已经没有活着的发起连接。

`ConnectionRpcGate` 就是最后一道准入门：连接一旦 close，尚未开始的 queued handler 不再启动。

---

## 50. `ConnectionRpcGate::run` 的关键顺序

等价白话代码：

```text
锁住 accepting
  if accepting == false:
      直接 return，连 future body 都不 poll
  else:
      先取得 TaskTracker token
解锁 accepting
执行 future
释放 token
```

“检查 accepting”和“取得 token”必须在同一次锁保护下完成。否则 close 可能恰好插在两者之间，产生一个未被 shutdown 计数的新 handler。

---

## 51. future 被 drop without polling 是什么意思

Rust async future 是惰性的。构造：

```rust
async move {
    perform_side_effect().await;
}
```

不会立刻执行 body；只有 poll 才开始推进。

gate 已关闭时，`run` 直接 return，传入的 future 随作用域结束被 drop，body 从未 poll。因此其中的 handler 调用、业务写入和 error response 都不会发生。

测试 `run_drops_future_without_polling_after_close` 用 atomic flag 直接证明了这一点。

---

## 52. `close` 不会取消已经开始的 handler

`close()` 做两件事：

- `accepting = false`；
- `TaskTracker::close()`。

它立即返回，不等待已有 token 归还，也不 abort handler。

所以语义是：

```text
未开始的 → 禁止进入
已开始的 → 允许自然完成
```

这比粗暴 abort 更容易保护已经开始的状态修改和资源清理。

---

## 53. `shutdown` 才等待 active handlers

`shutdown()`：

```rust
self.close().await;
self.tasks.wait().await;
```

connection close 主循环先快速 `rpc_gate.close()`，随后把完整 cleanup 放到独立 task；cleanup 中以 30 秒 timeout 等待 `rpc_gate.shutdown()`。

这样主 event loop 不必被一个慢 handler 卡住，同时清理仍尽力等待已开始工作收口。

---

## 54. 30 秒到了会发生什么

若 active RPC 30 秒内没有 drain：

- 记录 warning；
- 继续其他 connection cleanup。

这段代码没有在 timeout 分支中显式 abort handler。timeout 是 cleanup 的等待上限，不应误读为 handler 的强制执行时限。

---

## 55. gate 与 queue 的关系图

```text
request admitted
      │
      ├─ no scope ───────────────┐
      │                          │
      └─ scope → resource queue ─┤
                                 ▼
                       ConnectionRpcGate::run
                         ├─ open → token → handler
                         └─ closed → drop future
```

queue 决定“轮到谁”；gate 决定“轮到时还能不能开始”。二者不能互相替代。

---

## 56. typed handler 是一个 exhaustive match

`handle_initialized_client_request` 对 `ClientRequest` 做完整 match：

```rust
match codex_request {
    ClientRequest::ConfigRead { ... } => ...,
    ClientRequest::ThreadStart { ... } => ...,
    ClientRequest::TurnStart { ... } => ...,
    // 其他 variants
}
```

没有“任意 method 字符串再查 HashMap”的动态分派。

好处是新增 `ClientRequest` variant 后，编译器能提示 handler match 是否遗漏；参数在到达分支时已经是具体 Rust 类型。

---

## 57. 为什么 match 里还有 Initialize panic

exhaustive match 必须覆盖 `ClientRequest::Initialize`，但正常控制流已在 `handle_client_request` 提前处理它。

因此这里写：

```rust
panic!("Initialize should be handled before initialized request dispatch")
```

它表达内部不变量：若运行到这里，说明服务器自身的分派顺序被破坏，而不是客户端普通输入错误。

---

## 58. handler 的统一返回类型

大分派将不同业务 handler 统一成：

```rust
Result<Option<ClientResponsePayload>, JSONRPCErrorError>
```

三种结果的含义：

| 结果 | dispatcher 行为 |
|---|---|
| `Ok(Some(payload))` | 统一序列化并发送成功 response |
| `Ok(None)` | 不自动回包；专门 processor 已接管或延迟完成 response |
| `Err(error)` | 统一发送 error response |

这里的 `None` 不是“忘记响应”。它是 response ownership 的显式转移信号。

---

## 59. 为什么有些 handler 自己持有 request ID

某些请求会启动异步工作，真正回答时间不在函数立即返回时。例如 process/session 类操作可能需要 specialized processor 持有 `ConnectionRequestId` 并在自己的生命周期点回包。

这类分支返回 `Ok(None)`，避免 dispatcher 再发一次空成功结果。

读到 `None` 时应继续问：

1. 谁拿走了 request ID？
2. 它在哪条成功路径回包？
3. error、cancel、disconnect 时怎样收口？

---

## 60. error response 会保留 connection identity

dispatcher 构造：

```rust
ConnectionRequestId {
    connection_id,
    request_id: codex_request.id().clone(),
}
```

随后 `send_response_as` 或 `send_error` 定向发送。

因此同为 `id: 7` 的两个客户端请求不会串包。Connection identity 不必出现在 wire JSON 中，却是服务器内部路由不可缺少的一部分。

---

## 61. response/error 为什么不经过 initialize gate

它们不是要求服务器开始新业务，而是回答服务器已经发出的 reverse request。

如果强迫 response/error 也排在普通 client request 的资源队列后面，可能出现：

- handler 正等待客户端审批；
- 审批 response 却被同一资源的 handler 队列挡住；
- 双方互相等待。

当前实现让它们直接去 pending callback owner，有助于及时解除等待。

---

## 62. response callback 为什么只靠 request ID

主循环先用 connection map 拒绝 unknown connection，之后 `process_response` 把 `id + result` 交给 outgoing callback map。

服务器发出的 reverse request ID 由进程级 atomic 分配，并可广播给多个客户端；首个合法回答胜出。因此 callback key 的设计与 incoming client request 的 `(connection_id, client_id)` 不同。

两类 ID 不要混为一谈：

| 调用方向 | 内部关联键 |
|---|---|
| client → server request | `ConnectionRequestId` |
| server → client reverse request | server-generated global request ID |

---

## 63. 一张完整时序图

```text
Client             Transport          Main loop         MessageProcessor       Queue/Gate       Handler
  | text frame          |                  |                    |                   |               |
  |-------------------->|                  |                    |                   |               |
  |                     | parse envelope   |                    |                   |               |
  |                     | enqueue event    |                    |                   |               |
  |                     |----------------->| connection lookup  |                   |               |
  |                     |                  | process_request    |                   |               |
  |                     |                  |------------------->| typed decode      |               |
  |                     |                  |                    | init/capability   |               |
  |                     |                  |                    | enqueue/spawn     |               |
  |                     |                  |                    |------------------>| gate.run      |
  |                     |                  |                    |                   |-------------->|
  |                     |                  |                    |                   |   result      |
  |                     |                  |                    |<----------------------------------|
  |<---------------- response routed and written ------------------------------------------------|
```

图中 `process_request` 到主循环返回，可能发生在 Queue/Gate/Handler 全部完成之前。

---

## 64. 失败分层总表

| 失败位置 | 示例 | 固定提交的处理 |
|---|---|---|
| framing/read | stdin EOF、WebSocket close | 发 ConnectionClosed，结束 reader |
| JSON/envelope parse | 坏 JSON、shape 不匹配 | 记日志，无 error response，继续连接 |
| incoming queue Full + request | processor 过载 | 尝试同 ID `-32001` |
| incoming queue Full + response/error | 回调答案到达 | 等待入队，不主动丢 |
| unknown connection | close 后迟到 event | 记 warning，丢弃 |
| typed decode | unknown method、参数类型错 | 同 ID `-32600 Invalid request` |
| initialize gate | 普通请求先于 initialize | 同 ID `-32600 Not initialized` |
| duplicate initialize | 同连接再次 initialize | 同 ID `-32600 Already initialized` |
| experimental gate | 未声明 capability | 同 ID invalid request |
| queued request 遇断线 | 尚未进入 gate | future 不 poll，无 handler 副作用 |
| handler error | 业务验证/运行失败 | 同连接同 ID error response |
| success serialization error | payload 无法编码 | 由 outgoing 层降级为 internal error，见第 22 篇 |

---

## 65. 三种“没有响应”不能混为一谈

### 情况 A：malformed payload

没有形成可用 request ID，所以固定 transport 只记日志。

### 情况 B：connection 已不存在

即使原消息含 ID，也没有可路由回包的活连接。

### 情况 C：queued handler 因 gate closed 被 drop

请求曾经通过准入，但断线后不再启动 handler；回包也没有意义。

它们表面都是“客户端没收到 response”，根因却分别位于 parse、connection ownership 和 execution admission。

---

## 66. 常见误读一：Serde 成功等于请求合法

错。第一次 Serde 成功只说明能成为四种外层 envelope 之一。

还需要：

- method 能映射到 `ClientRequest` variant；
- params 能映射到具体 Params 类型；
- connection 已初始化；
- experimental capability 足够；
- handler 业务检查成功。

---

## 67. 常见误读二：`process_request().await` 等 handler 完成

错。普通 initialized request 通常只是被 enqueue 或 spawn。

若你要判断“业务操作真的完成”，应观察：

- 同 ID 的 response/error；
- 或该方法定义的后续 terminal notification；
- 而不是仅观察主循环已处理 incoming event。

---

## 68. 常见误读三：queue 与 gate 是重复保护

错。

- queue 管理跨请求的资源顺序；
- gate 管理连接生命周期上的执行许可；
- queue key 甚至可能跨连接共享，例如同一 thread；
- gate 永远是 per connection。

它们是两个正交维度。

---

## 69. 常见误读四：close 会取消所有已开始 RPC

错。`close` 只阻止新 handler 取得 token。

已经取得 token 的 handler 会继续；cleanup 最多等待 30 秒，然后不再阻塞清理流程。是否有更深层业务 cancellation，要看具体 processor 或 thread/turn 生命周期，不能从 RPC gate 推断。

---

## 70. 常见误读五：所有协议错误都用最精确标准 code

错。固定源码确实定义了多个 code helper，但 raw request 到 typed `ClientRequest` 的通用转换失败统一映射为 `-32600`。

阅读实现时，应该沿实际调用的 helper，而不是只看常量表中“存在什么 code”。

---

## 71. 调试时按哪一层找证据

当客户端说“请求没有反应”，推荐按以下顺序：

1. transport 是否读到一整行/text frame？
2. 是否出现 `Failed to deserialize JSONRPCMessage`？
3. incoming channel 是否 Full，是否尝试 `-32001`？
4. connection 是否已经被移除？
5. 是否出现 `Invalid request`、`Not initialized` 或 experimental error？
6. request 是否进入某个 serialization key？
7. connection RPC gate 是否在它轮到前关闭？
8. 具体 handler 是返回 `Some`、`None` 还是 `Err`？
9. outgoing router 与 writer 是否真的交付？

这比直接在业务 handler 里打断点更有效，因为很多请求根本到不了那里。

---

## 72. 如何阅读一个新 method 的 incoming 路径

假设你要研究 `thread/archive`：

1. 在 `client_request_definitions!` 找 wire method；
2. 记录 Params、Response、experimental 和 serialization；
3. 在 `handle_initialized_client_request` 找对应 match arm；
4. 追入具体 processor；
5. 确认它返回 `Some`、`None` 还是 `Err`；
6. 找集成测试，核对 wire 请求与 response；
7. 若有断线/并发风险，再看 queue key 与 ownership cleanup。

这套顺序把“协议合同”和“业务实现”分开，不容易在大文件中迷路。

---

## 73. 测试证据应该怎样组合

没有一个测试单独证明整条链路。需要组合：

| 层 | 代表性证据 |
|---|---|
| transport backpressure | `enqueue_incoming_*` 三个测试 |
| pre-init rejection | WebSocket connection handling integration test |
| experimental gate | `v2/experimental_api.rs` |
| gate close semantics | `connection_rpc_gate.rs` 单元测试 |
| serialization ordering | `request_serialization.rs` FIFO/shared-read tests |
| concrete handler | 对应 v2 suite integration test |

“整条链路正确”通常是多组局部性质共同建立的，不是某个测试名足够响亮就自动成立。

---

## 74. 贯穿案例的完整复盘

回到开头三条消息。

### request 1：pre-init `thread/start`

```text
text frame
→ Request envelope 成功
→ connection 存在
→ ClientRequest::ThreadStart 成功
→ initialized gate 失败
→ -32600 Not initialized
```

### request 2：initialize

```text
text frame
→ Request envelope 成功
→ ClientRequest::Initialize
→ 特殊分支
→ 验证 client info
→ OnceLock 提交 session state
→ initialize response
→ 主循环发布 connection-scoped 初始状态
→ outbound initialized = true
```

### request 3：`thread/resume` T

```text
typed decode
→ initialized / experimental checks
→ scope = Thread(T)
→ T queue
→ gate open 时取得 token
→ thread processor
→ Some(response) / None / Err
→ 定向回包
```

若 A 在 T queue 等待期间断开：

```text
ConnectionClosed
→ remove connection state
→ gate.close
→ queued request 以后轮到
→ gate 拒绝，future 不 poll
→ 不进入 thread processor
```

---

## 75. 局部术语表

| 代码词/短语 | 字面意思 | 本篇中的实际含义 |
|---|---|---|
| incoming | 进入的 | 从客户端进入 App-server 的消息方向 |
| transport | 传输 | stdio、WebSocket 等物理消息载体 |
| frame / framing | 帧 / 分帧 | 确定一条 payload 的物理边界 |
| payload | 载荷 | 某一帧/行承载的文本或数据 |
| envelope | 信封 | request/response/error/notification 的外层协议形状 |
| untagged enum | 无显式标签枚举 | 依靠字段 shape 尝试识别 variant |
| deserialize | 反序列化 | 从 JSON value 构造 Rust typed value |
| typed decode | 强类型解码 | 从任意 method/params 变成具体 `ClientRequest` variant |
| admission | 准入 | 决定工作是否允许进入后续执行路径 |
| initialize gate | 初始化门 | 普通请求必须先完成 connection handshake |
| capability | 能力声明 | 客户端在 initialize 中声明支持/启用的协议能力 |
| experimental | 实验性 | 需要客户端显式 opt in 的不稳定 API |
| serialization scope | 串行化作用域 | 声明请求冲突的资源身份 |
| queue key | 队列键 | 运行时用来选择 per-resource FIFO 的键 |
| shared read | 共享读 | 同 key 上连续读取可并发，写入仍独占 |
| gate | 门 | 真正执行前再次检查 connection 是否仍接受 RPC |
| accepting | 正在接纳 | gate 是否允许新 handler 获得 token |
| token | 追踪凭证 | 表示一个已开始、shutdown 应等待的 handler |
| poll | 推进 Future | async body 真正开始或继续执行的动作 |
| drop without polling | 未 poll 就丢弃 | future body 完全未运行，没有其中的副作用 |
| drain | 排空 | 等待/处理已开始或已排队的工作直至收口 |
| dispatch | 分派 | 把 typed variant 交给对应 processor/handler |
| exhaustive match | 穷尽匹配 | 编译器要求覆盖 enum 的所有 variants |
| response ownership | 回包所有权 | 由中央 dispatcher 还是 specialized processor 负责最终 response |
| backpressure | 背压 | 下游变慢时让上游等待或拒绝，而非无限堆积 |
| overload | 过载 | bounded incoming queue 已满，无法接收新 request |
| malformed | 格式损坏 | 不是合法 JSON 或不符合外层 envelope shape |
| unknown connection | 未知连接 | event 携带的 connection ID 已不在 owner map |
| reverse request | 反向请求 | App-server 主动请求客户端回答的 RPC |
| W3C trace context | W3C 追踪上下文 | 让跨组件 request span 关联父 trace 的 metadata |
| `OnceLock` | 仅设置一次的槽 | connection session 从未初始化单向提交为已初始化 |
| `Some/None/Err` | 有值/无值/错误 | 中央回成功、已转移回包所有权、回错误三种结果 |

---

## 76. 理解检查

### 练习一

一条合法 JSON 含 `id` 与未知 `method`。它在哪两步分别被“接受”和“拒绝”？

<details>
<summary>参考答案</summary>

外层 `JSONRPCMessage` 反序列化可接受它为 Request；随后 `ClientRequest::try_from` 因 method 无对应 variant 而失败，映射为 `-32600 Invalid request`。

</details>

### 练习二

incoming queue Full 时，为什么 request 快速报 overload，而 response 要等待？

<details>
<summary>参考答案</summary>

request 是新增工作，可以明确拒绝并让客户端重试；response 可能是服务器已发 reverse request 的唯一答案，丢弃会让 pending callback 失去正常完成路径，只能等待取消或清理，因此代码传播背压。

</details>

### 练习三

一个 `thread/resume` 已进入资源队列，随后连接断开。为什么 queue 自己不够？

<details>
<summary>参考答案</summary>

queue 只决定同资源执行顺序，不知道连接是否仍允许启动。轮到请求时，per-connection RPC gate 已关闭，会不 poll 地丢弃 handler future。

</details>

### 练习四

`ConnectionRpcGate::close()` 会不会强制停止已经开始的 handler？

<details>
<summary>参考答案</summary>

不会。它阻止新 token，并让已取得 token 的 handler自然完成；`shutdown` 等待它们，connection cleanup 的等待上限是 30 秒。

</details>

### 练习五

为什么 `Ok(None)` 不能直接解释为“成功但没结果”？

<details>
<summary>参考答案</summary>

在该 dispatcher 中它表示中央分派器不自动回包，通常是 specialized processor 已接管或延迟 response。必须继续追踪 request ID 的 ownership 和终结路径。

</details>

---

## 77. 源码导航

| 阅读目标 | 固定提交路径 | 重点符号 |
|---|---|---|
| 四类 wire envelope | `codex-rs/app-server-protocol/src/rpc.rs` | `JSONRPCMessage` |
| Request ID 类型 | 同上 | `RequestId` |
| Request/Notification/Response/Error shape | 同上 | 四个 JSONRPC structs |
| transport event | `codex-rs/app-server-transport/src/transport/mod.rs` | `TransportEvent` |
| JSON 解析入口 | 同上 | `forward_incoming_message` |
| incoming 背压策略 | 同上 | `enqueue_incoming_message` |
| overload tests | 同上 | 三个 `enqueue_incoming_*` tests |
| stdio framing | `codex-rs/app-server-transport/src/transport/stdio.rs` | `lines()`、reader loop |
| stdio initialize name hint | 同上 | `stdio_initialize_client_name` |
| WebSocket connection owner | `codex-rs/app-server-transport/src/transport/websocket.rs` | `run_websocket_connection` |
| WebSocket inbound | 同上 | `run_websocket_inbound_loop` |
| connection map owner | `codex-rs/app-server/src/lib.rs` | processor event loop |
| open/close state | 同上 | `ConnectionOpened`、`ConnectionClosed` branches |
| envelope routing | 同上 | `IncomingMessage` branch |
| WebSocket initialize publication | 同上 | state mirror、initial notifications、Release store |
| connection session | `codex-rs/app-server/src/message_processor.rs` | `ConnectionSessionState` |
| raw request entry | 同上 | `process_request` |
| request context | 同上 | `run_request_with_context` |
| raw→typed wrapper | 同上 | `deserialize_client_request` |
| initialize special branch | 同上 | `handle_client_request` |
| initialized/experimental gates | 同上 | `dispatch_initialized_client_request` |
| typed handler match | 同上 | `handle_initialized_client_request` |
| Some/None/Err 回包 | 同上 | handler match 后的 result match |
| initialize validation/commit | `codex-rs/app-server/src/request_processors/initialize_processor.rs` | `InitializeRequestProcessor::initialize` |
| error codes | `codex-rs/app-server/src/error_code.rs` | invalid/overloaded constants与helpers |
| ClientRequest DSL | `codex-rs/app-server-protocol/src/protocol/common.rs` | `client_request_definitions!` |
| raw→typed implementation | 同上 | `TryFrom<JSONRPCRequest> for ClientRequest` |
| protocol serialization scope | 同上 | `ClientRequestSerializationScope` |
| runtime queue key | `codex-rs/app-server/src/request_serialization.rs` | `RequestSerializationQueueKey` |
| queued RPC wrapper | 同上 | `QueuedInitializedRequest` |
| FIFO/shared-read drain | 同上 | `enqueue`、`drain` |
| per-connection gate | `codex-rs/app-server/src/connection_rpc_gate.rs` | `ConnectionRpcGate` |
| gate tests | 同上 | open、drop-unpolled、close、shutdown tests |
| pre-init integration proof | `codex-rs/app-server/tests/suite/v2/connection_handling_websocket.rs` | `Not initialized` assertion |
| experimental proof | `codex-rs/app-server/tests/suite/v2/experimental_api.rs` | capability rejection tests |

---

## 78. 本篇收束

一条 incoming 消息不是从 socket 直接跳进业务函数，而是依次跨过多种边界：

- stdio newline 或 WebSocket text frame 给出物理消息边界；
- `JSONRPCMessage` 只做四类外层 envelope 分类；
- bounded channel 用不同策略处理新增 request 与 callback response；
- 主循环以 connection map 拒绝断线后的迟到消息；
- `ClientRequest::try_from` 才校验 method 与具体 params；
- initialize 是唯一特殊准入请求，connection state 由 `OnceLock` 单向提交；
- 普通请求必须通过 initialized 与 experimental capability gates；
- serialization scope 管理资源冲突，不管理连接存活；
- `ConnectionRpcGate` 在真正执行前阻止断线连接的 queued handler；
- exhaustive match 把强类型请求交给业务 processor；
- `Some`、`None`、`Err` 明确中央 dispatcher 与 specialized processor 的回包所有权。

下一篇适合把 initialize 单独放大：从 `InitializeParams` 的 capability 协商、client identity、一次性 session commit，到初始通知与 outbound-ready 的发布顺序，解释“握手成功”究竟建立了哪些连接级合同。

返回[源码精读目录](README.md)，或查看[课程术语总表](../glossary.md)。
