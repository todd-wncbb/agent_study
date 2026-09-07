# 86：App-server JSON-RPC、请求响应、通知、ID 与双向调用生命周期

> 源码基线：4ee41929eaf4。本章解决“一行 JSON 进入 App-server 后怎样被辨认为 request、response、notification 或 error，以及客户端请求服务端和服务端请求客户端这两条反向链路怎样靠 ID、callback、连接和清理逻辑闭环”。

## 1. 本章解决什么问题

很多人把 App-server 想成普通 HTTP API：客户端问，服务端答。但它使用长连接，服务端还会反过来询问客户端“允许执行吗”“用户选择什么”，并持续发送无需回复的流式通知。

不区分四类 envelope、两个请求方向和 ID 作用域，就容易把 notification 当 response、把 server request 当 event，或在断线后留下永远等待的 Future。

## 2. 资料边界

官方 OpenAI 文档检索没有找到与固定提交逐行对应的独立 App-server JSON-RPC 页面。本章实现事实以 4ee41929eaf4 的 protocol、transport、message processor 和测试为准。

通用 JSON-RPC 知识只作解释模型；若标准习惯与固定源码不同，以固定源码为准。

## 3. 先说人话：一条双向总机线路

把连接想成双方都能拨号的总机线路：

- Request：拨号并留下回拨号码；
- Response/Error：带同一个号码回拨结果；
- Notification：广播一句话，不等回拨；
- Connection：号码只在相应通信范围内有意义。

App-server 既接听客户端的电话，也会主动给客户端打电话。

## 4. 四类 Wire Message

JSONRPCMessage 是无标签枚举，包含 Request、Notification、Response 和 Error。Serde 根据对象字段形状选择 variant，而不是读取额外的 message-type 字段。

## 5. Request 的形状

~~~json
{
  "id": 7,
  "method": "thread/read",
  "params": {"threadId": "thr_123", "includeTurns": false}
}
~~~

Request 有 id 和 method；params 可省略；固定提交还允许可选 trace。

## 6. Success Response 的形状

~~~json
{
  "id": 7,
  "result": {"thread": {}}
}
~~~

Response 没有 method，靠 id 与之前的 request 对位。result 在通用层是任意 JSON value，再由具体 response 类型解释。

## 7. Error Response 的形状

~~~json
{
  "id": 7,
  "error": {
    "code": -32602,
    "message": "invalid parameters"
  }
}
~~~

Error 也必须带原 request ID。内部 error object 包含数字 code、人类可读 message 和可选 data。

## 8. Notification 的形状

~~~json
{
  "method": "turn/completed",
  "params": {"threadId": "thr_123", "turn": {}}
}
~~~

Notification 没有 ID，因此接收方不应回复。它适合状态变化、流式 delta 和生命周期事件。

## 9. 为什么没有 jsonrpc 字段

rpc.rs 顶部明确说明：它不是真正完整的 JSON-RPC 2.0，因为双方都不发送也不期待 jsonrpc: 2.0 字段。

源码虽然定义 JSONRPC_VERSION，但 wire structs 没有对应字段。不要让通用 SDK 的默认设置替代本协议事实。

## 10. “类似 JSON-RPC”是什么意思

它沿用 request/response/error/notification、ID 和常见错误码等核心模型，但拥有自己的 envelope 细节、transport、capability 和扩展字段。

集成时应看生成 schema 和仓库说明，而不是只看通用 JSON-RPC 教程。

## 11. Request ID 的两种类型

RequestId 可以是 String 或 Integer(i64)。Wire 因此允许字符串或整数 ID，但整数不是任意浮点 number。

## 12. ID 是 Correlation

Request ID 负责把 response/error 交回某一次 request。它不是 thread ID、turn ID、item ID 或 process ID。

业务身份位于 params/result 中，RPC correlation 位于 envelope 顶层。

## 13. ID 必须原样返回

客户端发送字符串 ID abc，服务端也应返回字符串 abc；不能换成整数、重新编号或返回 method 名。

整数 7 与字符串 7 是两个不同的 RequestId key。

## 14. Pending 期间 ID 应唯一

旧 request 尚未完成时复用 ID，接收方无法判断 response 属于哪次调用。固定提交的 in-process client 会明确拒绝重复 pending ID。

网络路径也会在相同 ConnectionRequestId 的 request context 被替换时 warning；客户端应主动避免重用。

## 15. 完成后也不宜立即复用

虽然 callback 被移除后理论上可以复用，但迟到 response、并发和日志会造成歧义。单调递增或 UUID 风格更容易诊断。

服务端主动请求使用 AtomicI64 从 0 开始分配。

## 16. Connection ID

Transport 为每条连接分配 ConnectionId(u64)。它是进程内部身份，不出现在 JSON wire。

它决定 response、error 和定向 notification 应送到哪个 client。

## 17. ConnectionRequestId

不同连接都可能发送 request ID 1，因此 App-server 把 connection_id 与 request_id 组合成 ConnectionRequestId。

这让连接 A 的 1 与连接 B 的 1 成为两个不同的内部 key。

## 18. Namespace

每条 client connection 有自己的 inbound request-ID namespace。Server 主动请求则使用自己的进程内编号空间。

看到相同数字时，必须先问它属于哪个方向、哪个连接和哪种 envelope。

## 19. Client 到 Server 的主流程

~~~mermaid
sequenceDiagram
    participant C as Client
    participant T as Transport
    participant P as MessageProcessor
    participant H as Typed Handler
    C->>T: id + method + params
    T->>T: JSON parse and queue
    T->>P: connection_id + request
    P->>P: typed decode and gates
    P->>H: typed params
    H-->>P: payload or error
    P-->>T: same connection and same id
    T-->>C: result or error
~~~

每层都可能失败，并由最接近失败原因的层负责投影错误。

## 20. Transport Framing

JSON 自己没有流式消息边界。Stdio 用 JSON Lines，一行一条消息；WebSocket 用一个 text frame 一条消息；Unix socket 使用 WebSocket upgrade 和 frame。

把多行 pretty JSON 写进 stdio 会被拆成多个无效消息。

## 21. Stdio 输入循环

Stdio reader 使用 BufReader.lines 每次读取一行，反序列化 JSONRPCMessage，再发送 TransportEvent::IncomingMessage。

EOF 会产生 ConnectionClosed 事件。

## 22. Stdio 输出循环

OutgoingMessage 序列化成单行 JSON，追加换行后写 stdout。Tracing/log 应写 stderr，避免污染 JSONL channel。

Client 也应逐行读取 stdout，而不是等进程退出后一次解析。

## 23. WebSocket Framing

WebSocket 的 text frame 承载协议 JSON。Binary、ping、pong 和 close 属于 transport control，不是业务 JSONRPCMessage。

固定提交 README 将 WebSocket 标成 experimental/unsupported，存在实现不等于稳定承诺。

## 24. Parse Failure

Transport 解析失败时记录 error 并继续连接。若连合法 request ID 都取不到，就无法可靠发出与请求对位的 error response。

因此 client 必须保持每个 JSONL record 完整、合法，且不能把日志写到 stdout。

## 25. Untagged Enum 如何区分消息

大体规则是：

- id + method：request；
- method 且无 id：notification；
- id + result：response；
- id + error：error。

带冲突字段的畸形对象不应依赖 Serde 尝试顺序获得碰巧解释。

## 26. Envelope Decode 与 Typed Decode

第一层只得到 JSONRPCRequest 的 id、method、params。第二层 ClientRequest::try_from 根据 method 把 params 解成具体 Rust 类型。

所以 JSON 语法合法仍可能 Invalid request：外壳合法，具体 method/params 不合法。

## 27. ClientRequest 是 Tagged Enum

Request DSL 生成以 method 为 tag 的 ClientRequest。每个 variant 同时绑定 wire method、params、response、serialization scope 和实验 metadata。

Handler 因此不必到处从 Value 手取字段。

## 28. Optional Params

通用 request 的 params 是 Option<Value>；具体 method 是否允许缺席，仍由其 typed variant 决定。

无参数 method 也应有统一明确的 None/unit 约定，而不是让不同 handler 各自猜 null、空对象和缺席。

## 29. Trace 扩展

Inbound request 可带 W3C trace context。Message processor 构造 RequestContext，保存 request span 和 parent trace，直到最终 response/error。

通用 notification/response/error structs 没有同样的 trace field。

## 30. Request Context 生命周期

开始时 register_request_context；response/error 时 take 并移除；连接断开时按 connection 清理尚未完成的 context。

若 handler 永远不发送终结，context 会保留到断线，所以“每次 request 恰好一次终结”也是资源不变量。

## 31. Response 与 Error 互斥

一个 request 最终应发送一个 success response 或一个 error。第一个终结会移除 context/callback；第二个迟到终结只会成为 unmatched 或 warning。

## 32. ClientResponsePayload

Handler 的具体 response 类型通过 Into 转成统一 ClientResponsePayload，再装入 OutgoingResponse 的 result。

宏将 request variant 与 response type 集中绑定，减少配错类型。

## 33. Response 为什么没有 Method

Caller 已按 ID 保存原 method，所以成功 response 只需要 ID 与 result。丢失 pending metadata 后，只看 result 很难知道应该解成哪种 response。

## 34. Response 定向连接

send_response 从 ConnectionRequestId 取出 connection 与原 request ID，产生 ToConnection envelope。

即使其他连接订阅相同 thread，也不能收到这次 request 的 response。

## 35. Broadcast Notification

send_server_notification 产生 Broadcast intent，router 再筛选已初始化连接、实验能力和 notification opt-out。

Broadcast 不代表无条件发给所有 socket。

## 36. Targeted Notification

指定 connection IDs 时，sender 对每条连接创建 ToConnection。初始化 warning、调用者专属弃用提示等适合定向通知。

Audience 是协议语义的一部分。

## 37. Notification Timestamp

Server notification 被 ServerNotificationEnvelope 包装，并增加顶层可选 emittedAtMs。当前发送路径填入 Unix 毫秒时间。

它保持 optional，使新 client 仍能读取旧版本没有 timestamp 的通知。

## 38. Emitted Time 不是 Received Time

emittedAtMs 在 fan-out 前生成。Queue 和网络延迟会让收到时间更晚。

它可辅助衡量延迟，不代表 client 已处理时间。

## 39. Notification 不可回复

Notification 没有 ID，因此没有 correlation target。若 server 需要客户端做决定，必须发送 server-initiated request。

## 40. Client Notification

协议当前定义 client notification Initialized。固定提交的 message processor 对 client notification 目前只记录日志。

初始化 session state 已在成功处理 initialize request 时建立。

## 41. 当前无副作用不等于可省略

README 仍要求 initialize response 后发送 initialized acknowledgment。未来 transport 或版本可能依赖它，客户端应遵循协议序列。

## 42. Server 为什么反过来 Request Client

执行命令、修改文件、MCP elicitation 和用户输入需要外部 UI/用户决定。单向 notification 无法承载必须返回的答案。

因此 App-server 成为 caller，client 暂时成为 responder。

## 43. Server 到 Client 的主流程

~~~mermaid
sequenceDiagram
    participant Core as Core Event
    participant S as App-server
    participant M as Callback Map
    participant C as Client
    Core->>S: approval or input needed
    S->>M: allocate id and store oneshot
    S->>C: id + method + params
    C-->>S: same id + result or error
    S->>M: remove callback
    M-->>S: wake waiting task
    S-->>Core: typed decision or fallback
~~~

这条链与 client request 的方向相反，但使用相同 envelope 模型。

## 44. ServerRequestPayload

业务先构造不带 ID 的 typed payload。Sender 分配 ID 后调用 request_with_id，得到完整 ServerRequest。

集中分配可避免各 handler 自己维护冲突计数器。

## 45. Server Request ID 分配

AtomicI64.fetch_add 以 Relaxed ordering 生成唯一数字。它只需编号，不借此同步其他内存。

这个 ID 不承诺跨进程重启稳定。

## 46. Pending Callback Map

request_id_to_callback 是 RequestId 到 PendingCallbackEntry 的 HashMap，受 Tokio Mutex 保护。

Entry 保存 oneshot sender、可选 thread ID 和完整 typed request。

## 47. Entry 三部分分别做什么

- Oneshot sender：唤醒等待业务；
- Thread ID：按 thread 查询、重放和取消；
- Typed request：知道 result 应对应哪种 response，并支持 analytics。

## 48. 为什么用 Oneshot

一次 RPC 只应完成一次。Oneshot 精确表达单次 result/error，并让 async task await，不需轮询共享状态。

Sender drop 时 receiver 得知没有正常完成。

## 49. 必须先注册再发送

实现先将 callback 插入 map，再将 request 放进 outgoing queue。极快 client 回答时，callback 已存在。

若先发送再注册，会产生经典 lost response race。

## 50. Send Failure

Outgoing channel 发送失败时，sender warning 并从 map 删除 callback。Entry 中 oneshot sender drop，receiver 得到 cancellation。

等待业务必须处理 receiver.await 的 Err。

## 51. Client Success Response

Inbound JSONRPCResponse 进入 process_response，再由 notify_client_response 移除 matching callback，并发送 Ok(result)。

移除动作让第一个终结者获胜。

## 52. Client Error Response

Inbound JSONRPCError 进入 notify_client_error，移除 callback并发送 Err(JSONRPCErrorError)。

结构化 client error 与 oneshot channel cancellation 是两类失败。

## 53. Unmatched Response

ID 不存在时 server warning。原因可能是 ID 错误、重复回答、请求已取消或另一连接已先完成。

它不会依据 result shape 猜等待者。

## 54. Raw Result 到 Typed Response

Pending entry 保存原 ServerRequest，其 response_from_result 可按 variant 解码具体 response。业务等待函数也会将 Value 转成审批或用户输入响应。

Malformed success 通常采用拒绝/失败 fallback，而不是视为批准。

## 55. 命令审批案例

Core 产生 ExecApprovalRequest，App-server 构造 params、分配 ID、注册 callback、发送给 thread subscribers，然后 spawn task 等待 oneshot。

回答最终被映射成 ReviewDecision 并提交回 Codex thread。

## 56. 为什么等待要 Spawn

Event listener 若原地等待用户，便无法处理其他事件与取消。Spawn 把等待外部回答变成并发 task。

代价是 turn 完成、取消和 shutdown 时必须清理 pending task。

## 57. Thread-scoped Request

ThreadScopedOutgoingMessageSender 保存 thread ID 和 connection IDs，pending entry 也记录 thread ID。

这允许只重放或取消某一 thread 的 requests。

## 58. 多连接看到同一 Request

同一 thread request 可发给多个订阅连接，但 callback map 只有一个 ID entry。第一个有效回答移除 callback。

其他连接的迟到回答会 unmatched。

## 59. First Responder Wins

多个 UI 都看到审批卡时，某一个完成后，其他 UI 必须知道请求已解决，否则仍允许用户操作陈旧按钮。

只移除 callback 不足以同步所有观察者。

## 60. serverRequest/resolved

等待结束后，thread listener 向订阅者发 ServerRequestResolvedNotification，其中包含 threadId 和原 server requestId。

它是 fan-out notification，不是原 request 的第二份 response。

## 61. Resolved 中的 Request ID

Client 用 requestId 找到待处理 UI 并关闭它。Envelope 本身没有 RPC response ID，因此不要把这条 notification 放进 response callback map。

## 62. Pending Request Replay

Client resume 一个已加载 thread 后，server 可把该 thread 尚未完成的 requests 重发给新 connection。

Replay 保持原 ID 和 params，让新 UI 加入同一个 logical pending request。

## 63. Replay 为什么排序

pending_requests_for_thread 从 HashMap 收集后按 ID 排序，使输出确定。HashMap 原始迭代顺序不稳定。

确定顺序有利于 UI、测试和复现。

## 64. Replay 不是新请求

它不分配新 ID，也不创建第二 callback。否则同一审批会分裂成多个互不相识的等待项。

## 65. Turn Transition Cancellation

Turn complete、abort 或状态改变后，旧审批可能失去意义。abort_pending_server_requests 按 thread 取消，并可向 receiver 注入结构化 error。

这防止旧 turn 回答影响新状态。

## 66. turnTransition Reason

Error data.reason 使用 turnTransition discriminator。等待逻辑据此判断这是正常状态转换并返回，而不是匹配 message 文本。

结构化 data 比字符串 substring 稳定。

## 67. 单个 Cancel

cancel_request 按 ID 移除一个 callback并记录 aborted analytics。迟到 response 不能重新激活已结束操作。

## 68. 按 Thread Cancel

cancel_requests_for_thread 在锁内找出并移除相关 entries，再在锁外唤醒 callbacks。

锁只保护 map mutation，不在锁内执行可能唤醒别的 task 的工作。

## 69. Cancel All

Runtime shutdown 时 drain 全部 entries，可向每个 receiver 发送同一 shutdown error；若不发送 error，sender drop使 receiver看到 canceled。

两种终结语义不同。

## 70. 一个连接断开不等于取消全部审批

Server request 可能广播给多个 subscribers。一个连接断开后，其他连接仍能回答，所以 pending callback 不能简单绑定到最初某个 connection。

Connection cleanup 与 thread request cleanup 是不同 scope。

## 71. Inbound Context 的断开清理

OutgoingMessageSender.connection_closed 会清掉该 connection 尚未终结的 client request contexts。

Message processor还通知 fs、command exec、process exec 和 thread processor清理各自资源。

## 72. RPC Gate

连接关闭时先 close ConnectionRpcGate，并最多等待 30 秒让当前 RPC drain，再进入资源清理。

Timeout 防止断线清理无限等待。

## 73. 为什么清理分散给多个 Owner

Context、watch、process session和thread subscription各有 owner。统一入口调用各 owner的 cleanup，比一个巨型全局 map 更清楚。

## 74. Outbound Backpressure

每个 connection 有 bounded outgoing queue。可断开的连接若 queue满，会被视为 slow connection并断开，避免无限内存增长。

## 75. Ingress Overload

Transport ingress queue满时，新 request尝试立即收到 code -32001、message Server overloaded; retry later。

README建议 client exponential backoff并加入 jitter。

## 76. 为什么 Overload 保留 ID

Transport已成功解析 request ID，所以即使 request未进入 processor，也能用同 ID终结 client pending call。

比静默丢弃更可靠。

## 77. Overload Error 也可能写不出去

若 outbound queue同样满，transport只能 warning并丢弃 error。Client仍需 transport close和自己的 deadline作为最终保障。

协议 error不是任何故障下都保证可达。

## 78. Response 序列化失败

成功 response序列化失败时，transport尝试用相同 ID改发 internal error，说明 failed to serialize response。

这尽量保住 correlation，避免 client永远等待。

## 79. Notification 序列化失败

Notification没有等待它的 RPC callback，无法用相同通用 response fallback。实现记录错误并可能丢弃。

Typed protocol与生成测试应尽早发现不可序列化 shape。

## 80. Write Completion

某些定向 notification附带 write_complete oneshot。Writer写出后发 signal。

它只证明本地写循环处理过，不代表远端已解析或应用。

## 81. 三层 Delivery

- Enqueued：已放入本地 queue；
- Written：已写进 stdout/socket；
- Applied：远端业务已处理。

只有 response或显式 acknowledgment才能提供更强的应用证据。

## 82. In-process Transport

In-process embedder绕过 JSON文本解析，但仍使用 typed ClientRequest、同一 handler语义与 OutgoingMessage路由。

它不应形成另一套业务协议。

## 83. In-process Pending Client Calls

Embedder也用 RequestId 到 oneshot sender的 map等待自己发给 server的请求。收到 response/error后按 ID移除并唤醒 caller。

这是 client 到 server方向的 callback镜像。

## 84. In-process Duplicate ID

请求入队前使用 Entry API检查。ID已 pending时立即返回 duplicate request id error。

这是 pending ID唯一性最直观的实现。

## 85. In-process Server Request

Server主动 request被转换为 InProcessServerEvent。Embedder再用 respond_to_server_request或error接口返回 ID。

若 event consumer queue关闭/满，runtime会使 pending callback失败。

## 86. Notification 的交付等级

In-process routing对某些 requires-delivery notifications使用 await send；普通通知 queue满时可以 warning并丢弃。

“无 response”不代表所有 notification都可任意丢。

## 87. Shutdown 顺序

Loop退出后关闭 writer/processor通道、取消 server requests、给 pending client calls发送 shutdown error，再等待或终止 background tasks。

目标是让所有 waiter明确收敛。

## 88. Error 与业务拒绝不同

用户点击拒绝通常是合法 success result，其中 decision=decline；client无法展示请求或解码失败才是 error。

把业务否决当 transport error会丢失审计语义。

## 89. Timeout 在哪里

通用 envelope没有 deadline字段。具体调用者可在 oneshot await外包 timeout。

Timeout后还需移除 callback，或接受迟到 response成为 unmatched。

## 90. Retry 应使用新 RPC ID

Retry是新 attempt，使用新 ID可避免与旧迟到 response碰撞。防重复副作用应使用业务 idempotency ID，而不是 RPC correlation ID。

## 91. Event Ordering

同一 writer queue的顺序通常保留，但多个 async producer竞争入队时，因果顺序必须由业务协调层保证。

Timestamp不能凭空建立所有 thread之间的全局顺序。

## 92. Delta 与 Completed 用业务 ID归组

Started、delta、completed/error构成流式状态机。Client按 thread/turn/item IDs归组，而不是按 RPC request ID。

Notification本来就没有 RPC ID。

## 93. Response 不等于长任务完成

turn/start的 response表示请求已接受并创建 turn；后续 turn/started、item deltas与turn/completed才描述执行过程。

收到 request response不能立即把 turn标为 completed。

## 94. Snapshot 与 Stream

Thread start/resume response提供 snapshot，随后 notification提供增量。Client应先建立基线，再有序应用 stream。

Replay和初始化顺序因此属于协议合同。

## 95. Logging 与 Secret

Params/result可能包含路径、prompt、token或工具输出。日志应以 method、request ID、connection ID做关联，对 payload有界并脱敏。

## 96. Trace ID 与 RPC ID

Trace ID连接跨服务因果链；RPC ID只对位一次协议调用。二者不能互相替代。

高基数 ID适合具体日志/span，不适合作为低基数 metric label。

## 97. 常见 Bug：等待 Notification

Notification无 ID、不会回复，放进 pending map会永远等待。若需要确认，应改成 request或明确 acknowledgment协议。

## 98. 常见 Bug：Response 发错 Connection

只保存 request ID会在多客户端时错投 response。ConnectionRequestId正是为解决这个问题。

测试应让两个连接都使用 ID 1，验证结果隔离。

## 99. 常见 Bug：先 Send 再注册 Callback

快 client可能在 callback插入前回答，导致 unmatched response和永久等待。固定提交采用先注册后发送。

## 100. 常见 Bug：只关闭 UI

UI移除审批卡但 server callback仍在 map，会泄漏 task；server取消callback但不发resolved notification，其他 UI会保留陈旧卡片。

逻辑状态与所有观察者显示需要一起收敛。

## 101. 测试层次

- Envelope serde：四种形状和两种 ID；
- Typed macro：method、params、response映射；
- Transport：JSONL、frame、overload、序列化 fallback；
- Routing：connection定向和broadcast filtering；
- Callback：success、error、unmatched、cancel；
- Journey：server request到core decision；
- Shutdown：所有 pending waiter终结。

## 102. Controlled Race Test

不要用 sleep猜时序。用 oneshot/barrier控制 callback已注册、消息入队、取消发生、迟到response到达等阶段。

最终断言完整 map为空、receiver结果和通知集合。

## 103. 源码阅读路线

1. app-server-protocol/src/rpc.rs：四类 envelope与RequestId。
2. app-server-transport/src/transport/mod.rs：连接事件、parse和overload。
3. transport/stdio.rs：JSONL framing。
4. app-server/src/lib.rs：按四类 incoming message分流。
5. message_processor.rs：typed decode、gate和context。
6. outgoing_message.rs：callback、response、notification和cancel。
7. bespoke_event_handling.rs：审批/用户输入旅程。
8. thread_lifecycle.rs：pending replay与resolved。
9. in_process.rs：不经过JSON文本的对称实现。

## 104. 源码检查点

- codex-rs/app-server-protocol/src/rpc.rs：省略jsonrpc、四类消息、string/integer ID。
- codex-rs/app-server-protocol/src/protocol/common.rs：ClientRequest、ServerRequest和notification宏。
- codex-rs/app-server-transport/src/outgoing_message.rs：OutgoingMessage与ConnectionId。
- codex-rs/app-server-transport/src/transport/mod.rs：TransportEvent、parse、overload。
- codex-rs/app-server-transport/src/transport/stdio.rs：一行一消息与write completion。
- codex-rs/app-server/src/lib.rs：incoming四分流和connection close。
- codex-rs/app-server/src/message_processor.rs：ConnectionRequestId和RequestContext。
- codex-rs/app-server/src/outgoing_message.rs：pending callback、ID分配、cancel与replay。
- codex-rs/app-server/src/bespoke_event_handling.rs：server request journey。
- codex-rs/app-server/src/thread_state.rs：listener pending request移除。
- codex-rs/app-server/src/request_processors/thread_lifecycle.rs：resume replay和resolved。
- codex-rs/app-server/src/in_process.rs：duplicate ID和shutdown。
- codex-rs/app-server/src/error_code.rs：错误码。
- codex-rs/app-server/src/server_request_error.rs：turnTransition reason。

## 105. 练习一：判断 Message Kind

对 id+method、method、id+result、id+error分别判断 variant。再解释为何同时含method和result的对象不应依赖untagged顺序。

## 106. 练习二：追踪命令审批

从 ExecApprovalRequest开始，记录ID分配、callback注册、connections、callback移除、typed decision回到core和resolved notification。

## 107. 练习三：两个 Client 竞争

两个订阅者都收到request ID 12。A先批准，B后拒绝。解释callback map、两份response、resolved notification和最终decision。

## 108. 练习四：设计断线测试

创建一个未完成client request、一个thread-scoped approval和一个process session，再断开一个connection。列出按connection、thread和runtime清理的状态。

## 109. 本章结论

App-server JSON-RPC不是单向函数调用，而是长期存在的双向消息总线。Request与response/error靠ID闭环，notification靠method和业务ID推进状态，connection ID隔离客户端，pending callback map把服务端主动请求变成可等待Future，cancel/replay/resolved/disconnect让它们在状态变化时收敛。

阅读时始终问五件事：消息是哪种 envelope、谁是caller、ID在哪个namespace唯一、谁拥有pending state、成功/错误/取消/断线由谁终结。

## 110. 本章 Glossary

| 术语/代码短语 | 直译或展开 | 在本章中的含义 |
|---|---|---|
| JSON-RPC | JSON远程过程调用 | 本协议借用的request/response/notification模型 |
| Envelope | 信封/外层消息 | 包含id、method、params、result或error的顶层对象 |
| Framing | 分帧 | JSONL换行或WebSocket frame提供消息边界 |
| Request | 请求 | 带id和method、期待response/error |
| Response | 成功响应 | 带同一id和result |
| Error response | 错误响应 | 带同一id和结构化error |
| Notification | 通知 | 有method无id、不期待response |
| RequestId | 请求编号 | String或i64形式的correlation key |
| Correlation | 对位 | 用相同ID把结果交回原request |
| ConnectionId | 连接编号 | 进程内识别transport connection的u64 |
| ConnectionRequestId | 连接请求编号 | connection ID加client request ID |
| Namespace | 命名空间 | 相同数字在不同连接/方向中互不冲突的范围 |
| Untagged enum | 无标签枚举 | Serde按字段shape辨认消息 |
| Tagged enum | 有标签枚举 | 以method选择typed variant |
| Typed dispatch | 强类型分派 | Raw params解成对应Rust类型后调用handler |
| RequestContext | 请求上下文 | 保存身份、span和parent trace |
| OutgoingEnvelope | 发出信封 | ToConnection或Broadcast routing intent |
| Fan-out | 扇出 | 一条消息分发给多个connections |
| Server-initiated request | 服务端发起请求 | Server询问client审批/输入并等待 |
| Pending callback | 待定回调 | ID到oneshot sender的等待映射 |
| Oneshot | 单次通道 | 只完成一次结果的异步channel |
| First responder wins | 首答胜出 | 多client中第一个response终结callback |
| Replay | 重放 | 新subscriber收到原pending request和原ID |
| Resolved notification | 已解决通知 | 通知所有observer关闭pending UI |
| Unmatched response | 无匹配响应 | 找不到ID callback的迟到/重复结果 |
| Cancellation | 取消 | 正常response前移除pending request |
| Turn transition | Turn状态转换 | 使旧turn request失效的结构化原因 |
| Backpressure | 背压 | 有界queue限制生产速度与内存 |
| Overload | 过载 | Ingress满，以-32001提示重试 |
| Backoff/jitter | 退避/抖动 | 递增并随机化重试间隔 |
| Write completion | 写入完成 | 本地writer完成，不等于远端应用 |
| Enqueued/written/applied | 入队/写出/应用 | 三种不同交付阶段 |
| JSONL | JSON Lines | Stdio上一行一条JSON |
| W3C Trace Context | W3C追踪上下文 | 跨服务传播traceparent/tracestate |
| Idempotency ID | 幂等编号 | 防重复业务副作用，不是RPC ID |
