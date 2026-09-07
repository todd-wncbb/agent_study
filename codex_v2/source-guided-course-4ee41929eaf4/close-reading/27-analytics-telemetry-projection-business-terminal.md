# 源码精读 27：Analytics、telemetry projection 与业务终态

> 源码基线：`4ee41929eaf4`。本文只描述该固定提交中的实现。以后源码移动时，请搜索符号名，不要依赖行号。

上一篇讲 shutdown 时提到：analytics queue 可以 flush，但 flush 并不等于所有业务工作都成功。

这一篇继续追问：App-server 中所谓“记录 analytics”究竟记录了什么？

如果看到下面几种代码：

```rust
track_request(...)
track_response(...)
track_notification(...)
track_server_request_aborted(...)
```

很容易产生几个错误理解：

- 每个 RPC 都会对应一条远端 analytics event；
- `track_response` 表示客户端已经收到 response；
- `TurnStartResponse` 表示 Turn 已经完成；
- `ErrorResponse` fact 一定会导出一条包含错误详情的 event；
- `flush()` 会把不完整的 Turn 自动补成完整事件；
- analytics、OpenTelemetry trace、metrics 和普通日志只是四个名字不同的同一套数据。

固定提交中的真实设计更像一个**异步投影系统**：

```text
App-server/Core 在不同阶段产生 AnalyticsFact
  → 有界、非阻塞 queue
  → 单一 AnalyticsReducer 保存关联状态
  → 多块事实齐全后生成 TrackEventRequest
  → auth/filter/batch
  → best-effort HTTP 或 debug capture file
```

本文要解决的核心问题是：

> 一次 request、response、notification、Core Turn 和审批结果怎样被 reducer 拼成最终 analytics event？每个记录点证明的是“看见”“接纳”“发起回包”“业务终态”还是“远端交付”？为什么 analytics 缺失不能反向改变 Codex 的主流程？

---

## 1. 先给最重要的结论

固定提交中的 analytics 主线可以压缩成十一点：

1. App-server 创建一个可 clone 的 `AnalyticsEventsClient`；多个 processor 与 Core threads 共享同一 queue；
2. `track_*` 方法先筛掉与 analytics 无关的协议 variants；
3. 被保留的数据先成为内部 `AnalyticsFact`，而不是直接成为远端 JSON event；
4. fact 通过容量 256 的 `mpsc` queue 用 `try_send` 入队；满时丢 analytics 并告警，不阻塞业务；
5. 单一 `AnalyticsReducer` 按 connection、request、thread、turn、item 和 reverse request ID 保存关联状态；
6. request/response 主要建立关联，notification/Core facts 主要补充生命周期与结果；
7. Turn event 必须等 thread ID、图片数、resolved config、profile 和 completed state 齐全才生成；
8. 生成 event 后还要经过认证类型过滤、batching 和 HTTP/capture destination；
9. HTTP 失败、无 auth、queue full 或缺失关联 context 都只造成 analytics 缺失，不让业务请求失败；
10. `track_response` 发生在 response 进入 outgoing queue 之前，因此不证明 transport 写出或客户端收到；
11. analytics 是 best-effort 产品投影，不是协议正确性、持久化或 exactly-once 的权威账本。

最值得记住的四句话：

> `AnalyticsFact` 是 reducer 的输入事实，不一定一对一导出 event。

> `TurnStartResponse` 只证明 Turn submission 已被接纳；`TurnCompleted` notification 才提供 Turn 业务终态。

> analytics 缺失通常意味着观测链路不完整，不自动意味着业务没发生。

> analytics event 存在也不自动证明客户端收到了对应 response/notification。

---

## 2. 贯穿全文的案例

假设桌面客户端初始化后执行：

```text
thread/start request  id=10
turn/start request    id=11, input=[一张图片]
Core 开始 Turn        turn=T1
模型发起 shell tool   item=I1
服务器请求用户审批    reverse request=R1
用户批准
shell 完成
Turn 完成
```

analytics 并不是按上述每一行立刻发一个完整 event，而是逐步积累：

```text
Initialize fact
  → connection metadata

ThreadStart response fact
  → thread metadata
  → emit codex_thread_initialized

TurnStart request fact
  → pending request[(connection, id=11)]

TurnStart response fact
  → request id 11 ↦ turn T1

Core resolved-config fact
Core profile fact
TurnStarted notification
ItemStarted notification
ServerRequest approval fact
ServerResponse approval fact
ItemCompleted notification
TurnCompleted notification
  → pieces become complete
  → emit review/tool/turn events
```

---

## 3. 第一张源码地图

| 层级 | 固定提交路径 | 重点符号 |
|---|---|---|
| client/queue | `analytics/src/client.rs` | `AnalyticsEventsClient`、`AnalyticsEventsQueue` |
| internal facts | `analytics/src/facts.rs` | `AnalyticsFact`、custom facts |
| stateful projection | `analytics/src/reducer.rs` | `AnalyticsReducer` |
| wire event shapes | `analytics/src/events.rs` | `TrackEventRequest` variants |
| App-server client creation | `app-server/src/analytics_utils.rs` | `analytics_events_client_from_config` |
| shared client wiring | `app-server/src/message_processor.rs` | processor/ThreadManager/extensions clones |
| request tracking | 同上、`initialize_processor.rs` | initialized request dispatch |
| response/server request tracking | `app-server/src/outgoing_message.rs` | send/notify/cancel paths |
| typed error classification | `app-server/src/request_processors/turn_processor.rs` | `track_error_response` |
| notification tracking | `app-server/src/outgoing_message.rs` | thread-scoped/global paths |
| Core Turn facts | `core/src/session/turn.rs`、`core/src/tasks/mod.rs` | resolved config、usage、profile、error |
| business terminal projection | `analytics/src/reducer.rs` | `maybe_emit_turn_event` |
| end-to-end tests | `app-server/tests/suite/v2` | turn start/steer/thread resume analytics |

---

## 4. 先区分 log、trace、metric、analytics event

| 类型 | 回答的典型问题 | 本篇示例 |
|---|---|---|
| log | 某处发生了什么异常或诊断信息？ | analytics queue full warning |
| trace/span | 一次调用的父子路径和耗时在哪里？ | `app_server.request` → Core operation |
| metric | 一段时间内数量、分布、速率怎样？ | token usage histogram |
| analytics event | 哪种产品行为以哪些业务维度发生？ | `codex_turn_event` |

它们可以引用相同 thread/turn ID，但数据模型、发送渠道和丢失语义不同。

上一篇的 W3C trace context 不会自动替代本篇 reducer 的 request/thread/turn maps。

---

## 5. telemetry 是上位词

`telemetry` 常被泛指系统自动产生的观测数据，可能包含 logs、traces、metrics 和 analytics events。

本文标题中的 telemetry projection 强调：源码把内部 protocol/Core 事实投影成外部可消费的业务事件。

不要看到一个类型名含 `Telemetry` 就假设它一定走 `codex-analytics` 的 HTTP endpoint；例如 `SessionTelemetry` 还负责 OpenTelemetry metrics。

---

## 6. `AnalyticsEventsClient` 很轻，真正 owner 是 queue task

client 结构只有：

```rust
pub struct AnalyticsEventsClient {
    queue: Option<AnalyticsEventsQueue>,
}
```

它可以 clone。clone 后共享：

- 同一个 `mpsc::Sender` 所指向的 queue worker；
- app/plugin dedupe sets。

真正持有 reducer 并串行 ingest facts 的，是 `AnalyticsEventsQueue::new` 中 spawn 的 receiver task。

---

## 7. 为什么 App-server 与 Core 要共享同一个 client

MessageProcessor 创建时把同一个 client clone 传给：

- initialize/turn/config 等 processors；
- `OutgoingMessageSender`；
- `ThreadManager`；
- 每个 Core Session；
- extensions 与 plugins manager。

这样 request/response facts 和 Core profile/token/error facts 能进入同一个 reducer state。

若每层各自创建 reducer，App-server request ID 与 Core Turn facts 无法在内存中汇合。

---

## 8. 配置禁用的精确语义

client constructor 使用：

```rust
queue: (analytics_enabled != Some(false))
    .then(|| AnalyticsEventsQueue::new(...))
```

因此对这条 analytics-event client 路径：

| `analytics_enabled` | queue |
|---|---|
| `Some(false)` | 不创建 |
| `Some(true)` | 创建 |
| `None` | 创建 |

`disabled()` 则显式得到 `queue: None`，大量 unit tests 使用它避免真实 analytics side effect。

---

## 9. 不要把 event queue 默认与 OTEL metrics 默认混为一谈

App-server analytics tests 还会验证 OTEL metrics provider 的 `default_analytics_enabled` flag。

那是 provider/metrics 初始化层的默认逻辑；本节展示的是 `AnalyticsEventsClient::new` 自己是否创建 event queue 的判断。

同一个配置名可能被不同 telemetry plane 消费，必须沿具体 constructor 读取，不能只凭一个 integration test 推导所有通道。

---

## 10. 从事实到远端事件的完整管线

```text
track_* call
  → filter relevant variant
  → construct AnalyticsFact
  → record_fact
  → queue.try_send(Fact)
  → reducer.ingest(fact)
  → zero/one/many TrackEventRequest
  → auth filter
  → batching
  → HTTP POST or capture file
```

一项 fact 可以：

- 只更新 reducer state，不立刻产生 event；
- 产生一个 event；
- 产生多个 events；
- 因缺少 context 被丢弃；
- 因 auth policy 在发送前被过滤。

---

## 11. `AnalyticsFact` 与 `TrackEventRequest` 为什么分两层

内部事实更贴近源码事件发生的位置：

```text
ClientRequest
ClientResponse
Notification
TurnProfile
TurnTokenUsage
ServerRequestAborted
```

外部 event 更贴近分析问题：

```text
codex_thread_initialized
codex_turn_event
codex_turn_steer_event
codex_review_event
codex_command_execution
```

分层使调用点只报告“我知道的局部事实”，由 reducer 统一决定怎样组合成稳定业务事件。

---

## 12. queue 容量是 256

固定常量：

```rust
const ANALYTICS_EVENTS_QUEUE_SIZE: usize = 256;
```

这是 facts queue，不是 HTTP batch 大小，也不是 App-server incoming/outgoing queue。

有界 queue 防止 telemetry producer 无限快于 reducer/network consumer 时内存无上限增长。

---

## 13. `try_send` 表达“业务优先”

`record_fact` 最终调用 queue 的：

```rust
sender.try_send(...)
```

queue full 或 closed 时不 await、不重试，只记录 warning：

```text
dropping analytics events: queue is full
```

因此 tool loop、RPC handler、Core Turn 不会因为 analytics HTTP 很慢而背压主业务。

代价是高负载时 analytics 可以缺失。

---

## 14. warning 文案比实际错误范围略宽

代码对 `try_send(...).is_err()` 统一打印“queue is full”。

但 `TrySendError` 理论上还可能是 channel closed。

读日志时应以控制流为准：该 warning 证明 fact 没入队，不足以百分之百断言唯一根因就是容量满。

---

## 15. analytics failure 不反向改变业务结果

以下情况都只 warning/return：

- queue 入队失败；
- 没有 auth；
- auth 类型不允许发送该 event；
- HTTP timeout；
- HTTP 非 2xx；
- JSON capture append 失败；
- reducer 缺 connection/thread metadata；
- response 无法被 analytics 预序列化。

这些路径不会让原 JSON-RPC response 改成 error，也不会中断 Turn。

---

## 16. reducer 是单一串行 owner

queue worker 内部创建：

```rust
let mut reducer = AnalyticsReducer::default();
```

然后逐个接收 facts 并调用 `ingest`。

这避免让多个 producers 共同锁一大堆关联 maps。并发层只负责入队；复杂状态转换由一个 task 串行执行。

---

## 17. reducer 保存哪些关联状态

主要 maps：

| map | key | 用途 |
|---|---|---|
| `requests` | `(connection_id, request_id)` | TurnStart/Steer/Interrupt request 与 response 关联 |
| `connections` | connection ID | client/runtime metadata |
| `threads` | thread ID | connection、session/source/originator metadata |
| `turns` | turn ID | config、timing、usage、profile、terminal、tool counts |
| `tool_items_started_at_ms` | thread+turn+item | ItemStarted/Completed 配对 |
| `pending_reviews` | reverse request ID | approval request/result/abort 配对 |
| `item_review_summaries` | thread+turn+item | 把 guardian/user review 汇入 tool event |
| `tool_response_states` | thread+turn | 等 response/cell correlation 的 tool events |

这是一套 analytics projection state，不是 App-server 业务权威状态。

---

## 18. connection ID 为什么仍然不可少

JSON-RPC request IDs 可被不同 connections 重复使用。reducer 的 request key 必须是：

```text
(connection_id, request_id)
```

这与 `RequestContext` registry 的复合键原则相同，但两份 map 用途不同：

- RequestContext map 管 tracing/最终回包；
- analytics requests map 管产品事件关联。

---

## 19. initialize fact 建立 connection metadata

Initialize processor 在 session commit 成功后调用：

```rust
track_initialize(
    connection_id,
    params,
    originator,
    rpc_transport,
)
```

reducer 保存：

- product client ID；
- client name/version；
- stdio/WebSocket/in-process transport；
- experimental API opt-in；
- Codex Rust version、OS、OS version、architecture。

后续 thread/turn/tool events 都会引用这份 connection metadata。

---

## 20. initialize analytics 的记录边界

调用发生在：

```text
参数验证
  → session OnceLock commit
  → process identity update attempt
  → track_initialize
  → response construction
  → send_response
```

因此 fact 表示服务器接受并提交了 initialize state；它不证明 initialize response 已被客户端收到。

失败于 name validation 或 duplicate initialization 的请求不会建立这份 fact。

---

## 21. 普通 request 在哪里 track

非 initialize request 先经过：

- session initialized gate；
- experimental API gate。

之后、进入 serialization queue/RPC gate 之前调用：

```rust
track_initialized_request(connection_id, request_id, &request)
```

所以它表示“通过基础协议 gates 并进入已初始化 dispatch”，不表示 handler body 已经开始。

---

## 22. queued request 可能已 track，却从未 poll handler

上一篇讲过：connection close 后 `ConnectionRpcGate` 会 drop queued future without polling。

时序可能是：

```text
track_request fact 入队
  → request 等 serialization queue
  → connection closes
  → gate refuses to poll handler body
  → 没有 response/error analytics fact
```

reducer 中对应 pending request state 可以一直留到 analytics client/reducer 被 drop。

这证明 analytics request fact 不是“业务 handler 已执行”的权威证据。

---

## 23. 哪些 client requests 会被保留

`track_request` 只处理：

- `TurnStart`；
- `TurnSteer`；
- `TurnInterrupt`，且 `turn_id` 非空。

其他 requests 直接 return，例如 thread archive request 本身不会进入 `ClientRequest` fact。

选择性 tracking 降低数据量，并让 reducer 只保存确实能构成目标产品事件的状态。

---

## 24. startup interrupt 为什么不 track

`TurnInterrupt` 的空 `turn_id` 表示 startup cancellation，没有具体 Turn 可关联。

client 方法遇到空 turn ID 直接 return，不建立 `ExplicitClientInterruptRequest` fact。

这避免创建一个无法加入 `codex_turn_event` 的 pending interrupt state。

---

## 25. TurnStart request fact 保存什么

reducer 不把完整 request 永久保存，而是抽取：

```text
PendingTurnStartState
  ├─ thread_id
  └─ num_input_images
```

request 原始文本、additional context、全部 client metadata 不会原样留在这个 pending state。

这是投影式存储：只保留生成目标 event 所需字段。

---

## 26. TurnSteer request fact 保存什么

```text
PendingTurnSteerState
  ├─ thread_id
  ├─ expected_turn_id
  ├─ num_input_images
  └─ created_at
```

response/error 到来后，reducer 用它生成 accepted/rejected `codex_turn_steer_event`。

这里的 `created_at` 在 reducer ingest request 时取 wall-clock seconds，不是 transport 收到 bytes 的绝对起点。

---

## 27. Explicit interrupt request fact 保存什么

```text
PendingTurnInterruptState
  ├─ turn_id
  └─ requested_at_ms
```

它使用 `now_unix_millis()` 记录显式中断请求时间。最终 interrupt response 到来时，reducer 把最早请求时间写进 TurnState。

这支持分析某个 Interrupted Turn 是否由客户端明确请求以及何时请求。

---

## 28. response tracking 发生在出站入队之前

`send_response_as_inner` 的顺序是：

```text
track_response / track_response_with_thread_originator
  → take RequestContext
  → construct OutgoingMessage::Response
  → sender.send(ToConnection).await
```

因此 response analytics fact 可以存在，即使后面的 outgoing channel send 失败，或 router 已删除 connection。

它证明“业务层发起了这种 response”，不证明 peer delivery。

---

## 29. 哪些 client responses 会被保留

只处理：

- ThreadStart；
- ThreadResume；
- ThreadFork；
- TurnStart；
- TurnSteer；
- TurnInterrupt。

例如 ThreadArchive response 被忽略；archive/unarchive analytics 来自对应 notification。

request/response 的选择集合不完全相同，这是按目标 event schema设计的。

---

## 30. response 为什么先做 serialization preflight

`track_response_inner` 会先：

```rust
serde_json::to_writer(std::io::sink(), response)
```

若序列化失败，analytics fact 不入队。

原因是 reducer 稍后还要把 `ClientResponsePayload` 转成 typed `ClientResponse`。保留无法形成合法 wire representation 的 response 只会污染关联状态。

注意这仍不是实际 transport serialization；只是写入 sink 的可序列化检查。

---

## 31. 预序列化成功也不保证真实 response 写出

它只证明该 payload 在检查时可编码。

后面仍可能失败于：

- outgoing global queue closed；
- router 中没有 connection；
- per-connection writer queue full/closed；
- transport write error；
- peer 断开。

所以 analytics response fact 位于“构造成功”和“尝试发送”之间。

---

## 32. ThreadStart response 为什么可产生 thread-initialized event

reducer 收到 ThreadStart/Resume/Fork response 后，抽取：

- thread ID；
- session ID；
- model；
- ephemeral；
- thread source/parent/fork source；
- created_at；
- initialization mode。

再与 Initialize 建立的 connection/runtime metadata组合，立即生成 `codex_thread_initialized`。

这里的 initialized 指 Core thread 已创建/恢复/派生，不是 connection initialize handshake。

---

## 33. Thread initialization mode

| response | analytics mode |
|---|---|
| ThreadStart | `new` |
| ThreadResume | `resumed` |
| ThreadFork | `forked` |

相同 event type 用 mode 区分三种来源，方便统一分析 thread 建立而不丢失语义。

---

## 34. `thread_originator` 解决什么归属问题

App-server connection 的 initialize client 可能不是 thread 的真实产品来源。

例如 thread config/source 表示它由 `codex_work_desktop` 创建，但当前 response 经过另一个 daemon connection 返回。

Thread start/resume/fork 的专门发送函数把 config snapshot 中的 originator 传给 analytics：

```text
send_response_with_thread_originator(...)
```

reducer 保存到 `ThreadAnalyticsState.originator`。

---

## 35. originator 怎样覆盖 product client ID

生成 thread 后续 event 时：

```text
clone connection app_server_client metadata
if thread originator exists:
  app_server_client.product_client_id = thread originator
```

client name/version/transport 仍来自当前 connection，但产品归属 ID 可以来自 thread。

这是一种字段级 projection，而不是把整个 connection metadata替换掉。

---

## 36. TurnStart response 只是建立 request→turn 关联

reducer 用 `(connection_id, request_id)` remove pending TurnStart state，然后：

- 取得 response 中的 Turn ID；
- 在 `turns[turn_id]` 写 connection ID；
- 写 thread ID；
- 写 request 时统计的图片数；
- 调 `maybe_emit_turn_event`。

通常此刻没有 completed/profile 等 facts，因此不会立即导出 Turn event。

---

## 37. `maybe_emit` 是一个 readiness gate

Turn event 的必需 pieces：

```text
thread_id
num_input_images
resolved_config
profile
completed
```

任一缺失就 return，保留 TurnState 等未来 facts。

这使不同 async producers 的到达顺序不必完全固定。

---

## 38. 哪些 Turn 字段不是 readiness 必需条件

当前 gate 不要求：

- token usage；
- started_at；
- latest diff；
- typed Codex error；
- explicit interrupt request time；
- connection ID 直接写在 TurnState 中。

connection 可从 thread state fallback；其他字段允许在 event 中为空或使用默认/可选表示。

---

## 39. Core resolved-config fact 提供什么

Turn 真正开始后，Core 报告：

- model/provider；
- permission profile 与 cwd；
- reasoning effort/summary；
- service tier；
- approval policy/reviewer；
- sandbox network access；
- collaboration mode/personality；
- workspace kind；
- ephemeral、session source；
- 是否 first turn；
- Core 观察到的输入图片数。

它反映**解析后的有效配置**，比单纯记录 client request override 更接近实际执行语境。

---

## 40. 为什么 reducer 还保存 request 侧图片数

TurnStart/Steer 请求先提供输入图片数量；Core resolved config fact 也包含输入统计。

当前 reducer 用不同路径的 facts服务不同投影与 fallback。测试还会验证 image preparation metadata，例如原始/处理后尺寸和 effective detail。

这体现了 analytics 不是简单复制一个 payload，而是在多个边界选择更有解释力的字段。

---

## 41. Turn profile 在终态阶段产生

Core task 完成时计算 profile，包括：

- first sampling 前时间；
- sampling 总时间；
- compaction 时间；
- sampling 间 overhead；
- tool blocking 时间；
- last sampling 后时间；
- sampling request/retry count。

reducer 把它写入 TurnState，再尝试 emit。

---

## 42. TurnCompleted notification 提供业务终态

notification 中 reducer 读取：

- Completed/Failed/Interrupted status；
- structured Turn error；
- completed_at；
- duration_ms。

然后设置 `CompletedTurnState` 并调用 `maybe_emit_turn_event`。

`InProgress` 会映射为 analytics status `None`，但正常 completed notification 应携带终态 status。

---

## 43. TurnStartResponse 与 TurnCompleted 的承诺差别

```text
TurnStartResponse
  → Core 接受 submission，返回 Turn ID 和 InProgress snapshot

TurnCompleted notification
  → Turn 状态机到达 Completed/Failed/Interrupted
```

analytics 最终 Turn event 的 status 依赖后者，而不是把前者误当成功完成。

---

## 44. Turn event 为什么要等 profile

如果一看到 TurnCompleted 就立刻 emit，稍后到达的 timing profile 无法补进已经发送的 event。

把 profile 纳入 readiness gate，使终态事件携带完整的延迟分解。

这要求 Core terminal path无论成功、失败还是 interrupt 都尽量产生 profile fact。

---

## 45. token usage 是可选 enrichment

Core 有 usage 时调用 `track_turn_token_usage`，reducer保存进 TurnState。

但 readiness gate 不强制要求它。这样没有模型调用、启动即取消或某些错误 Turn 仍能生成终态 analytics event。

“event 中没有 tokens”不等于 Turn 没发生。

---

## 46. typed Codex error 与 protocol error 不同

Core 在 sampling/compaction/task error处可记录 `TurnCodexErrorFact`：

- error kind；
- 可选 HTTP status code。

这是 Turn 执行错误 enrichment。

JSON-RPC `ErrorResponse` fact 则描述 client request 被 error response 收口。两者的作用域和消费者不同。

---

## 47. event emit 后删除 TurnState

`maybe_emit_turn_event` 成功生成 Turn event 与可选 accepted-line events 后：

```rust
self.turns.remove(turn_id);
```

这是 reducer 的 terminal take，防止同一组 facts 重复生成 Turn event，并释放聚合状态。

迟到 fact 若再次创建同 Turn ID state，缺少其他 required pieces 时通常不会再 emit完整 event。

---

## 48. analytics exactly-once 仍然没有成立

内存 reducer 的 remove 防止正常进程内重复 projection，但系统没有：

- durable fact log；
- event delivery acknowledgement ledger；
- process restart恢复；
- HTTP idempotency key/retry state；
- queue full重放。

所以应称为 best-effort single-runtime projection，而不是端到端 exactly-once analytics。

---

## 49. notification tracking 是选择性的

client 只 clone这些 variants：

- ThreadArchived/Closed/Unarchived；
- TurnStarted/Completed/DiffUpdated；
- ItemStarted/Completed；
- Guardian review started/completed。

高频 `CommandExecutionOutputDelta` 等 notification 被忽略，避免把流式碎片全部送进 analytics queue。

---

## 50. Thread-scoped notification 在发送前 track

`ThreadScopedOutgoingMessageSender::send_server_notification` 顺序：

```text
track_notification
  → if connection_ids empty: return
  → send to target connections
```

所以即使当前没有 subscribers，业务 notification fact 仍可能进入 reducer。

这有助于保留 Core/thread 生命周期事实，也再次证明 analytics 不等于客户端投递记录。

---

## 51. global notification 为什么只显式 track archive/unarchive

普通 `OutgoingMessageSender::send_server_notification` 只在 variant 是 ThreadArchived/ThreadUnarchived 时主动 track，然后 broadcast。

多数 Turn/Item notifications 走 thread-scoped sender，那里已统一 track。

这避免所有 global config/status notifications 都进入 analytics，也减少重复记录同一业务 notification 的风险。

---

## 52. ThreadClosed fact 主要用于 reducer cleanup

收到 ThreadClosed 后，reducer：

- flush该 thread 的 pending tool events；
- 删除 code-mode cell correlation state。

它本身不一定生成一个外部 `ThreadClosed` event。

这再次说明某些 tracked notifications 是内部 projection maintenance facts，而不是一对一产品事件。

---

## 53. TurnStarted notification 的作用

reducer 在 `turns[turn_id]` 写可选 `started_at`。

它不立即 emit Turn event，也不是统计 TurnStart request。

request 表示客户端意图；TurnStarted 表示 Core lifecycle 已真正进入运行阶段。

---

## 54. TurnDiffUpdated 为什么只保留 latest

每次 notification 覆盖：

```text
turn_state.latest_diff = Some(notification.diff)
```

最终 Turn event 关心最后可用 diff，而不是保留所有中间版本。

这是 snapshot projection，不是 event-sourcing history。

---

## 55. Tool event 需要 Started/Completed 配对

ItemStarted 对可追踪 tool item 保存：

```text
(thread_id, turn_id, item_id) → started_at_ms
```

ItemCompleted 时：

- 找 TurnState；
- 增加 tool counts；
- remove started timestamp；
- 读取 completed timestamp；
- 结合 thread/client/review context；
- 生成具体 tool event。

缺少 Started 会 warning 并丢该 tool event。

---

## 56. 哪些 items 被当作 tool analytics

包括：

- command execution；
- file change；
- MCP tool call；
- dynamic tool call；
- collab agent tool call；
- web search；
- image generation。

UserMessage、AgentMessage、Reasoning、Plan 等不走这个 tool item event projector。

---

## 57. 为什么还要等待 response/cell correlation

Code Mode 中一个 tool item 可能需要补充：

- cell ID；
- parent call ID；
- originating model response ID。

reducer 可把 tool events 暂存在 `tool_response_states.pending_tool_events`，等 sampling response/cell facts 到来后 enrich。

TurnCompleted 或 ThreadClosed 会 flush仍未完全关联的 pending events，避免永远不发送。

---

## 58. pending tool events 有单独容量上限

固定常量：

```text
MAX_TOOL_RESPONSE_ENTRIES = 256
```

超过时会把最旧 event推出到 output，再保存新的 pending event。

这体现了另一层有界状态：不仅 queue 有界，等待 correlation 的 reducer buffer 也不能无限增长。

---

## 59. server request analytics 追踪反向审批

当 App-server 向指定 connections 发送 reverse request 时，每个成功进入 global outgoing channel 的 target都会调用：

```rust
track_server_request(connection_id, request.clone())
```

reducer只为相关审批 variants 建 `PendingReviewState`，例如 command execution、file change、permissions。

---

## 60. server request tracking 也不是 peer receipt

调用点位于：

```text
sender.send(ToConnection envelope).await 成功
  → track_server_request
```

它比 client response tracking更靠近出站 queue，但仍只到 global router channel，尚未证明 writer 或 peer 收到。

同一个 reverse request 发给多个 targets 时，可能为多个 connection calls 输入重复 facts；pending review map仍以全局 request ID 作为 key，后写状态覆盖同 ID 的等价 review metadata。

---

## 61. Broadcast reverse request 的 tracking 边界

`send_request_to_connections(None, ...)` 走 Broadcast envelope branch，当前分支不调用 `track_server_request`。

thread-scoped requests通常带明确 connection ID slice，能建立 client/thread context；无目标 broadcast缺乏单一 connection attribution。

所以不能仅按“server request 已发送”推断一定存在 pending review analytics state。

---

## 62. server response 怎样完成 review

客户端 response 到达后：

1. callback map 原子 take；
2. 用原始 request typed-decode result；
3. 记录 `completed_at_ms`；
4. 对相关 response调用 `track_server_response`；
5. reducer remove pending review；
6. 生成 `codex_review_event`。

review event因此拥有 started/completed/duration、reviewer、trigger、status、resolution 等完整字段。

---

## 63. Permissions response 为什么走专门入口

普通 `notify_client_response` 明确跳过：

```text
ServerResponse::PermissionsRequestApproval
```

权限审批需要在业务层计算 effective permissions 后，调用：

```text
track_effective_permissions_approval_response
```

这样 analytics记录实际生效的权限结果，而不是只记录客户端原始 payload。

---

## 64. client error response 被投影为 aborted review

若 peer 对 reverse request 返回 JSON-RPC error：

- callback entry 被 take；
- analytics 调 `track_server_request_aborted`；
- reducer remove pending review；
- emit status=Aborted、resolution=None。

这里“aborted”表示这次 review没有得到正常决策结果，不等于整个 Turn 必然已经 abort。

---

## 65. 主动取消也走 aborted

以下路径都会记录 server request aborted：

- `cancel_request`；
- `cancel_all_requests`；
- `cancel_requests_for_thread`；
- peer JSON-RPC error。

这样 pending review state能正常终结，而不是永远等待 response。

---

## 66. 初次 send failure 的一个边界

若把 reverse request送入 outgoing channel本身失败，代码会从 callback map remove entry并 warning。

这个分支没有同时调用 `track_server_request_aborted`。

而且只有对某 target send成功后才 track server request；因此通常 reducer没有对应 pending review需要取消。边界与事实创建点是匹配的。

---

## 67. replay pending request 为什么不重复 track

resume 时可以把原 pending reverse request重新发送给新 connection。

`replay_requests_to_connection_for_thread` 只发送 envelope，不调用 `track_server_request`。

原 review analytics state在首次 request时已建立；replay是交付恢复，不是新业务 review，因此避免重复 started state/event。

---

## 68. JSON-RPC ErrorResponse fact 不是通用外部 error event

`AnalyticsFact::ErrorResponse` 包含 error payload和可选 typed error分类。

reducer match 时写的是：

```rust
error: _
```

也就是当前 projection不直接把完整 JSON-RPC error导出。

它主要用来 remove pending request state，并为 TurnSteer rejection生成结构化 event。

---

## 69. 为什么要传 typed error classification

仅靠 message文本很难稳定分析：

```text
"no active turn to steer"
"expected active turn id ..."
"input must not be empty"
```

TurnProcessor 在知道根因的位置传：

```text
AnalyticsJsonRpcError::TurnSteer(...)
AnalyticsJsonRpcError::Input(...)
```

reducer映射成稳定 `TurnSteerRejectionReason` enum。

---

## 70. 哪些 TurnSteer rejection 被结构化

包括：

- no active turn；
- expected Turn mismatch；
- review Turn不可 steer；
- compact Turn不可 steer；
- empty input；
- input too large。

未提供 typed classification 的 error仍可完成 pending state清理，但 rejection reason为 null。

---

## 71. 并非所有 JSON-RPC errors 都会 track

统一 `OutgoingMessageSender::send_error` 本身不调用 `track_error_response`。

TurnProcessor 在特定 TurnStart/Steer/Interrupt失败点显式调用 helper。

因此：

- typed decode error；
- pre-initialize rejection；
- experimental gate rejection；
- 其他 processor error；

不应假设都有 `ErrorResponse` analytics fact。

---

## 72. 为什么 error tracking 放在知道语义的 processor

统一 send_error只知道 code/message/data，不一定知道：

- 这是 empty input还是 no active turn；
- 属于哪个稳定 rejection taxonomy；
- 应生成哪种产品 event。

在 TurnProcessor根因处分类，避免 reducer依赖脆弱字符串解析。

代价是调用点必须记得覆盖相关错误分支。

---

## 73. accepted TurnSteer event 怎样生成

TurnSteer response到达 reducer后：

- remove pending request；
- 若 TurnState已存在，steer_count += 1；
- 写 expected turn ID；
- 写 accepted turn ID；
- result=Accepted；
- rejection reason=null；
- 结合 thread/session/client/runtime metadata；
- emit `codex_turn_steer_event`。

它不等待整个 Turn完成，因为 steer request本身已有明确 accepted/rejected terminal。

---

## 74. rejected TurnSteer event 怎样生成

ErrorResponse fact remove同一 pending request，然后：

- accepted turn ID=null；
- result=Rejected；
- rejection reason来自 typed classification；
- 保留 expected turn ID、图片数和 created_at。

这让成功和失败共享同一个 event schema，而不是分别散落在 response/error日志里。

---

## 75. interrupt analytics 为什么等 delayed response

非 startup `turn/interrupt` response要等 TurnAborted/TurnComplete barrier。

response fact到达 reducer时，才把 `requested_at_ms` 合并进 TurnState，并取多个请求中的最早值。

在 bespoke event handler中，pending interrupt responses先于最终 Turn completed/interrupted notification发出，因此 reducer通常能在 Turn event emit前看到这项 enrichment。

---

## 76. 时间戳来自不同语义边界

| 字段 | 来源 |
|---|---|
| interrupt requested_at_ms | analytics client看到 request时的 wall clock |
| steer created_at | reducer ingest request时的 Unix seconds |
| review started_at_ms | server request params |
| review completed_at_ms | App-server收到 response/cancel时的 wall clock |
| Turn started/completed/duration | Core lifecycle notification/timing state |
| thread created_at | Thread response snapshot |

因此不要把所有 `*_at` 都当成同一进程函数入口时间。

---

## 77. duration 使用 checked subtraction

review/tool observed duration使用：

```rust
completed_at_ms.checked_sub(started_at_ms)
```

若 wall clock回拨导致 completed < started，结果是 `None`，而不是无符号整数下溢成巨大延迟。

对真正进程内精确耗时，Core profile还会使用 monotonic timing机制；wall clock主要用于跨事件时间戳。

---

## 78. 缺失 analytics context 时怎样处理

生成 Turn/tool/review/compaction等 event时，reducer可能缺：

- thread→connection关联；
- connection metadata；
- thread metadata。

它会记录包含 thread/turn/review/item ID 的 warning，然后 drop该 event。

不会为了 analytics补救而重新加载 thread、重发业务请求或让主流程失败。

---

## 79. connection close 没有对应 AnalyticsFact

固定 `AnalyticsFact` enum没有 ConnectionClosed variant。

所以 reducer的 connection metadata不会在单连接 teardown时由 fact显式清除；这允许长于 connection的 thread后续仍引用原 client context，但也说明 reducer不是 connection runtime state的镜像。

它的内存最终由整个 AnalyticsEventsClient queue worker生命周期收口。

---

## 80. ThreadClosed 也不会删除全部 thread analytics metadata

当前 ThreadClosed notification主要清 tool-response/cell correlation；`threads` map中的 session/source/originator metadata仍可服务迟到或后续相关 facts。

因此 analytics reducer状态生命周期有意不同于 App-server ThreadStateManager。

不要用 analytics map是否有 entry判断业务 thread是否仍 loaded。

---

## 81. app/plugin dedupe 是另一类状态

client在 queue旁保存：

```text
(turn_id, connector_id)
(turn_id, plugin_id)
```

同一 Turn中同一 connector/plugin通常只发一次 used event。

集合达到 4096 keys时整体 clear，再重新积累，防止 dedupe内存无限增长。

这是一种有界去重，不是永久 exactly-once ID registry。

---

## 82. auth filter 在 event生成之后执行

`send_track_events` 先向 AuthManager取当前 auth：

- 无 auth：return；
- API key auth：只保留允许的 plugin-related events；
- 非 Codex backend auth：return；
- 合适的 ChatGPT/Codex backend auth：继续发送。

所以 fact成功入队、event成功生成，也可能因发送时 auth状态被过滤。

---

## 83. API key auth 为什么只保留小集合

`TrackEventRequest::can_send_with_api_key_auth` 当前仅允许带 first-party plugin ID关联的：

- PluginUsed；
- SkillInvocation；
- McpToolCall。

其他 events被 retain过滤掉。

这是一条发送政策边界，不是 reducer无法生成那些 events。

---

## 84. destination 有两种

正常模式：

```text
{chatgpt_base_url}/codex/analytics-events/events
```

debug build可通过 capture-file环境变量改为本地 append：

- network delivery disabled；
- payload写 capture file；
- 初始化或 append失败只记录 error。

capture用于测试/调试生成 payload，不代表生产远端已收到。

---

## 85. HTTP POST 是 best-effort

每次 request设置 10 秒 timeout。

结果处理：

- 2xx：无额外动作；
- 非 2xx：读取 body并 warning；
- transport/error/timeout：warning。

没有把失败重新塞回 durable retry queue，也不会通知原业务 caller。

---

## 86. batching 不是简单固定条数切片

普通 events可一起放入 `TrackEventsRequest { events }`。

Accepted-line-fingerprint events要求 isolated request：

- flush前面的普通 batch；
- 每个 fingerprint event单独一个 POST；
- 再继续累积后面的普通 events。

这是按 event隐私/体积/处理语义定义的 batching policy，而不是统一 N 条一包。

---

## 87. reducer通常每个 fact之后就尝试发送生成 events

queue worker对每个 Fact：

```text
reducer.ingest
  → collect events
  → send_track_events(...).await
```

因此 network发送发生在同一个 queue consumer task中。慢 POST会降低 facts消费速度，最终可能使 256 queue满并触发 producer-side drop。

业务不会被阻塞，但 analytics完整性会下降。

---

## 88. `flush` 是 queue barrier

`flush()`：

1. 把 `Flush(done_tx)` 用 `send().await` 放入同一 queue；
2. worker按 FIFO先处理 preceding facts；
3. reducer.flush pending tool events；
4. 发送此次产生的 events；
5. done_tx ack；
6. caller收到 ack后返回。

测试 `flush_waits_for_preceding_fact_delivery` 直接验证这个先后关系。

---

## 89. flush 不是“补全所有不完整事件”

`AnalyticsReducer::flush` 当前只 drain `tool_response_states` 中 pending tool events。

它不会：

- 把缺 profile的 Turn强行 emit；
- 把只有 request没有 response的 TurnStart变成失败；
- 自动 abort pending reviews；
- 为缺 connection metadata编造默认值。

flush保证处理/发送已有可输出数据，不保证业务 projection完整。

---

## 90. flush 自己也有 25 秒 deadline

常量注释说明它覆盖两次顺序 POST加 queue/barrier scheduling，更多排队发送仍是 best-effort。

若 timeout、queue closed或 ack失败，只 warning：

```text
timed out or failed while flushing analytics events
```

shutdown继续，不会无限等待 analytics backend。

---

## 91. disabled client 的 flush 是 no-op

若 `queue=None`，`flush()` 立即 return。

测试先 track一个 notification，确认 queue仍为 None，再调用 flush并正常完成。

这让业务代码可以无条件调用 flush，而不必每处判断 analytics是否启用。

---

## 92. in-process shutdown 为什么显式 flush

上一篇看到：in-process runtime在 processor/outbound收口后调用 analytics `flush().await`，再发送 shutdown ack。

因此 embedded caller的正常 shutdown给 analytics一个有界发送机会。

这仍不保证远端永久保存，只表示本地 queue barrier和HTTP尝试已按代码完成。

---

## 93. Turn end-to-end 测试证明什么

`turn_start_tracks_thread_originator_in_analytics` 建立真实 App-server、mock Responses API与 analytics endpoint，然后断言 `codex_turn_event` 含：

- thread/session/turn IDs；
- thread originator覆盖后的 product client ID；
- model/provider；
- sandbox/workspace/ephemeral/source/mode；
- 图片准备尺寸/detail；
- completed status与时间；
- token usage；
- retry/timing/tool counts等字段。

这证明跨 App-server response、Core facts和notifications的完整 reducer组合。

---

## 94. Turn profile测试证明什么

另一 Turn test让模型先请求用户输入，再继续 sampling，最终检查：

- tool blocking time > 0；
- sampling request count=2；
- retry count=0；
- status=completed。

它证明 analytics profile不是由RPC latency粗略推断，而来自 Core Turn timing state。

---

## 95. TurnSteer tests证明什么

成功测试检查：

- result=accepted；
- expected/accepted Turn IDs；
- rejection reason=null。

失败测试用不存在的 expected Turn，检查：

- JSON-RPC error仍正常返回；
- analytics event result=rejected；
- accepted ID=null；
- rejection reason=`no_active_turn`。

这证明 typed error taxonomy与response/error两条 reducer路径汇合到同一 event schema。

---

## 96. client unit tests证明选择性 tracking

tests直接观察 queue message：

- TurnStart/Steer request会产生 ClientRequest fact；
- 非空 TurnInterrupt产生 explicit interrupt fact；
- ThreadArchive request与空-turn interrupt被忽略；
- 六类 thread/turn response被保留；
- ThreadArchive response被忽略；
- output delta notification被忽略；
- TurnDiffUpdated被保留；
- 无法序列化的 thread response被忽略。

这比仅检查最终 HTTP payload更精确地验证入口filter。

---

## 97. 调试“业务成功但没有 analytics event”

按管线逐层检查：

1. `analytics_enabled` 是否为显式 false；
2. 该 request/response/notification variant是否在filter allowlist；
3. track调用点是否真的经过；
4. queue是否 full/closed；
5. reducer是否只更新state、仍缺 required facts；
6. request/response composite key是否匹配；
7. Initialize/Thread response是否先建立 connection/thread metadata；
8. 是否出现 missing analytics context warning；
9. 当前 auth是否允许发送该 event；
10. destination是HTTP还是debug capture；
11. HTTP是否 timeout/non-2xx；
12. shutdown是否调用并完成flush barrier。

---

## 98. 调试“analytics 有 response，但客户端说没收到”

这是可能的正常边界，因为 track发生在真正send之前。

继续检查：

- `take_request_context` 后 global outgoing send是否成功；
- router中connection是否仍存在；
- per-connection writer queue是否full/closed；
- serialization是否成功；
- socket/stdout write是否成功；
- peer是否已断开；
- client是否按request ID处理该response。

analytics response fact不是transport receipt。

---

## 99. 调试“Turn event一直不出现”

围绕 readiness五件套检查：

```text
thread_id?
num_input_images?
resolved_config?
profile?
completed?
```

再检查 connection/thread context：

- initialize fact；
- thread initialized response fact；
- thread originator/metadata；
- TurnStart request→response关联；
- TurnCompleted notificationtracking；
- Core terminal path是否发profile。

只看到 TurnCompleted notification仍不足以证明 reducer已具备所有必需pieces。

---

## 100. 调试“审批 event 重复或缺失”

检查：

- server request是否定向发送并track；
- 同一全局 request ID是否被多个targets记录但pending map最终只保留一个state；
- response是否首答take callback；
- replay是否正确避免二次track；
- permissions是否走effective result专门入口；
- peer error/Turn transition是否track aborted；
- started/completed timestamps是否合法；
- thread/client metadata是否存在。

多客户端协议的首答语义与analytics pending review语义必须一起理解。

---

## 101. 权威状态与analytics projection对照

| 问题 | 应查询的权威来源 | analytics可做什么 |
|---|---|---|
| request是否已回包 | JSON-RPC/RequestContext/outgoing链 | 说明某类response曾被业务层track |
| client是否收到 | transport/peer ACK或client日志 | 不能单独证明 |
| Turn当前状态 | Core Session/App-server snapshot | 提供最终聚合分析event |
| thread是否loaded | ThreadManager | reducer map不能作为判断 |
| approval当前是否pending | callback/thread state | analytics pending map只服务event关联 |
| 历史是否持久化 | rollout/thread store | analytics不是durable log |
| 产品行为趋势 | analytics backend | 这正是analytics的主要用途 |

---

## 102. 设计新 analytics event 的检查表

1. 目标是log、trace、metric还是product event？
2. 调用点能知道完整字段，还是只能产生局部fact？
3. correlation key包含connection/thread/turn/item哪些维度？
4. request IDs是否需要connection scope？
5. success/error/cancel/timeout是否都有terminal fact？
6. 何时才能诚实地称为业务终态？
7. 哪些fields是readiness必需，哪些可选enrichment？
8. facts乱序到达时reducer能否收敛？
9. pending state何时remove或有界淘汰？
10. 高频delta是否应filter而非全部clone？
11. queue full时能否安全丢弃而不影响业务？
12. auth policy允许哪些event类型？
13. PII/secret是否被排除或归一化？
14. test应观察fact、reducer output还是end-to-end HTTP payload？
15. event存在能证明到哪个delivery boundary？

---

## 103. 本章词汇表

| 代码词/短语 | 字面翻译 | 本篇中的具体含义 |
|---|---|---|
| analytics | 分析数据 | 为产品行为与质量分析生成的结构化events，不是业务权威账本 |
| telemetry | 遥测 | logs、traces、metrics、analytics等自动观测数据的上位概念 |
| projection | 投影 | 从多源内部facts构建面向分析的较稳定event shape |
| `AnalyticsFact` | 分析事实 | request/response/notification/Core阶段产生的内部reducer输入 |
| `TrackEventRequest` | 事件发送请求 | reducer生成、可序列化并进入HTTP/capture的外部event variant |
| reducer | 归约器 | 串行保存关联state，把多块facts组合成零个、一个或多个events |
| correlation key | 关联键 | connection+request、thread+turn+item等把事实配对的稳定身份 |
| pending state | 待决状态 | 已看到起点fact、仍等待response/terminal/enrichment的内存条目 |
| readiness gate | 就绪门 | required facts齐全后才允许生成最终event的条件 |
| enrichment | 信息增补 | token/profile/diff/error/originator/review等后来加入的字段 |
| business terminal | 业务终态 | Turn Completed/Failed/Interrupted或review accepted/rejected/aborted |
| protocol terminal | 协议终态 | JSON-RPC response/error完成一问一答，可能早于业务Turn终态 |
| best-effort | 尽力而为 | queue/network失败可丢数据，不阻塞或改变主业务结果 |
| non-blocking tracking | 非阻塞记录 | producer用try_send，queue满时丢fact而不是await背压业务 |
| selective tracking | 选择性记录 | 只保留能构成目标events的request/response/notification variants |
| serialization preflight | 序列化预检 | 写入sink验证response可编码，不代表实际transport write |
| thread originator | Thread产品来源 | 覆盖connection product_client_id，使后续events归属于真实创建产品 |
| wall clock | 现实时间 | Unix seconds/millis时间戳，可跨组件比较但可能回拨 |
| monotonic duration | 单调耗时 | 进程内elapsed/profile测量，不受系统时钟回拨影响 |
| checked subtraction | 检查式相减 | completed早于started时返回None，避免整数下溢 |
| queue barrier | 队列屏障 | Flush消息保证它之前的facts先由worker处理 |
| flushable event | 可刷出事件 | pending tool correlation可在flush时输出；不等于所有不完整state |
| auth filter | 认证过滤 | event生成后按无auth/API key/Codex backend决定能否发送 |
| capture file | 捕获文件 | debug模式把payload写本地并禁用network delivery |
| isolated request | 独立请求 | accepted-line fingerprint event单独占一个HTTP POST |
| dedupe key | 去重键 | 同一Turn+connector/plugin避免重复used event的内存key |
| missing context drop | 缺上下文丢弃 | reducer无法找到connection/thread metadata时warning并跳过event |

---

## 104. 代码单词拆解

### `track_response_with_thread_originator`

```text
track_response          记录业务层准备返回的response fact
with                    同时携带
thread_originator       Thread真实产品来源
```

它不是另一种发送response的方法；差异只在analytics归属metadata。

### `maybe_emit_turn_event`

```text
maybe       条件可能尚未满足
emit        生成并交给输出集合
turn_event  聚合后的Turn业务事件
```

名字明确提醒调用者：每个fact到来都可尝试，但不保证立即输出。

### `track_server_request_aborted`

```text
server_request  App-server向client发出的反向request
aborted         没有以正常typed response完成
```

它终结review analytics state，不自动表示Core Turn也已abort。

### `analytics_events_client_from_config`

```text
analytics_events_client  产生facts并持有queue sender的轻量client
from_config              base URL和enabled option来自Config
```

真正 reducer worker由constructor内部spawn。

---

## 105. 理解检查

### 练习一

`track_response` 已执行，能否断言客户端收到response？

<details>
<summary>参考答案</summary>

不能。track发生在take request context和global outgoing channel send之前；后面仍有router、writer、serialization、transport和peer处理边界。

</details>

### 练习二

为什么 TurnStartResponse不会立刻生成 `codex_turn_event`？

<details>
<summary>参考答案</summary>

它只建立request→turn关联并表示submission已接纳。最终Turn event还要求resolved config、profile和completed等业务终态facts。

</details>

### 练习三

queue full时为什么不让RPC handler `.await` 等analytics？

<details>
<summary>参考答案</summary>

analytics是best-effort旁路；用try_send可避免慢网络/reducer给主业务施加背压。代价是高负载下数据可能缺失。

</details>

### 练习四

`AnalyticsFact::ErrorResponse` 是否一定导出包含code/message的error event？

<details>
<summary>参考答案</summary>

不是。当前reducer忽略完整error payload，主要remove pending request，并为TurnSteer按typed classification生成rejected event。

</details>

### 练习五

为什么 permissions approval不使用普通server response analytics？

<details>
<summary>参考答案</summary>

业务层还要计算实际生效的effective permissions；专门入口记录最终有效结果，避免把客户端原始grant误当成系统实际采用权限。

</details>

### 练习六

flush能否让只有TurnCompleted、但缺profile的Turn event强制发出？

<details>
<summary>参考答案</summary>

不能。当前flush只drain pending tool events，不绕过Turn readiness gate，也不为缺失facts编造数据。

</details>

### 练习七

为什么replay pending reverse request不再次track server request？

<details>
<summary>参考答案</summary>

replay只是交付恢复，业务review仍是原request ID对应的同一项待决操作。重复track会覆盖/重复started state并扭曲事件。

</details>

### 练习八

analytics event缺失时，第一结论应是“业务没发生”吗？

<details>
<summary>参考答案</summary>

不应。还可能是variant被过滤、queue丢弃、关联facts不完整、缺metadata、auth过滤、HTTP失败或shutdown未flush。业务事实应查Core/App-server/transport权威状态。

</details>

---

## 106. 源码导航

| 阅读目标 | 固定提交路径 | 重点符号 |
|---|---|---|
| Analytics client | `codex-rs/analytics/src/client.rs` | `AnalyticsEventsClient` |
| Queue constants | 同上 | queue size、HTTP/flush timeouts、dedupe max |
| Queue worker | 同上 | `AnalyticsEventsQueue::new` |
| Non-blocking producer | 同上 | `try_send`、`record_fact` |
| Enabled/disabled behavior | 同上 | `new`、`disabled` |
| Flush barrier | 同上 | `flush` |
| Initialize tracking | 同上 | `track_initialize` |
| Client request filter | 同上 | `track_request` |
| Client response filter/preflight | 同上 | `track_response_inner` |
| Error fact | 同上 | `track_error_response` |
| Reverse request/response facts | 同上 | `track_server_request`、`track_server_response` |
| Effective permissions fact | 同上 | `track_effective_permissions_approval_response` |
| Reverse abort fact | 同上 | `track_server_request_aborted` |
| Notification filter | 同上 | `track_notification` |
| Auth filtering | 同上 | `send_track_events` |
| Batching policy | 同上 | `track_event_request_batches` |
| HTTP sender | 同上 | `send_track_events_request` |
| Debug capture | 同上 | `AnalyticsEventsDestination::CaptureFile` |
| Internal fact enum | `codex-rs/analytics/src/facts.rs` | `AnalyticsFact` |
| Typed JSON-RPC error classes | 同上 | `AnalyticsJsonRpcError`、input/steer enums |
| Turn resolved config fact | 同上 | `TurnResolvedConfigFact` |
| Turn profile/token/error facts | 同上 | corresponding structs |
| External event enum | `codex-rs/analytics/src/events.rs` | `TrackEventRequest` |
| Client/runtime metadata | 同上 | `CodexAppServerClientMetadata`、`CodexRuntimeMetadata` |
| Thread initialized payload | 同上 | `ThreadInitializedEventParams` |
| Turn/review/tool payloads | 同上 | event params structs |
| Reducer state maps | `codex-rs/analytics/src/reducer.rs` | `AnalyticsReducer` |
| Connection metadata ingest | 同上 | `ingest_initialize` |
| Request state ingest | 同上 | `ingest_request` |
| Response correlation | 同上 | `ingest_response` |
| Thread originator override | 同上 | `ThreadAnalyticsState::app_server_client` |
| Thread initialized event | 同上 | `emit_thread_initialized` |
| Notification projection | 同上 | `ingest_notification` |
| Tool item pairing | 同上 | ItemStarted/ItemCompleted branches |
| Reverse review start | 同上 | `ingest_server_request` |
| Review completion/abort | 同上 | server response/effective/aborted methods |
| TurnSteer accepted/rejected | 同上 | response/error ingest methods |
| Turn readiness gate | 同上 | `maybe_emit_turn_event` |
| Missing context warning | 同上 | `thread_context_or_warn`、`warn_missing_analytics_context` |
| Reducer flush | 同上 | `flush` |
| Config→client constructor | `codex-rs/app-server/src/analytics_utils.rs` | `analytics_events_client_from_config` |
| Shared client wiring | `codex-rs/app-server/src/message_processor.rs` | MessageProcessor/ThreadManager args |
| Initialized request track point | 同上 | `dispatch_initialized_client_request` |
| Initialize fact timing | `codex-rs/app-server/src/request_processors/initialize_processor.rs` | `initialize` |
| Request tracking wrapper | 同上 | `track_initialized_request` |
| Response track-before-send | `codex-rs/app-server/src/outgoing_message.rs` | `send_response_as_inner` |
| Thread originator response | 同上 | `send_response_with_thread_originator` |
| Thread-scoped notification track | 同上 | `ThreadScopedOutgoingMessageSender::send_server_notification` |
| Global archive tracking | 同上 | `send_server_notification` |
| Reverse request track point | 同上 | `send_request_to_connections` |
| Reverse response/abort | 同上 | notify/cancel methods |
| Replay no-retrack path | 同上 | `replay_requests_to_connection_for_thread` |
| Typed Turn errors | `codex-rs/app-server/src/request_processors/turn_processor.rs` | `track_error_response` calls |
| Steer rejection classification | 同上 | `turn_steer_inner` |
| Interrupt delayed response order | `codex-rs/app-server/src/bespoke_event_handling.rs` | pending interrupt response before terminal notification |
| Effective permissions callback | 同上 | `track_effective_permissions_approval_response` |
| Core resolved config | `codex-rs/core/src/session/turn.rs` | `track_turn_resolved_config_analytics` |
| Core token/profile terminal facts | `codex-rs/core/src/tasks/mod.rs` | terminal tracking calls |
| Core Codex error facts | `codex-rs/core/src/session/mod.rs`、compact/tasks | `track_turn_codex_error` |
| In-process terminal flush | `codex-rs/app-server/src/in_process.rs` | analytics flush before shutdown ack |
| Client filter tests | `codex-rs/analytics/src/client_tests.rs` | request/response/notification/flush tests |
| Reducer/event tests | `codex-rs/analytics/src/analytics_client_tests.rs` | thread/turn/review/tool projections |
| HTTP test helpers | `codex-rs/app-server/tests/suite/v2/analytics.rs` | capture/wait/assert helpers |
| Turn end-to-end analytics | `codex-rs/app-server/tests/suite/v2/turn_start.rs` | originator/images/profile tests |
| TurnSteer end-to-end analytics | `codex-rs/app-server/tests/suite/v2/turn_steer.rs` | accepted/rejected tests |
| ThreadResume analytics | `codex-rs/app-server/tests/suite/v2/thread_resume.rs` | resumed initialized event |

---

## 107. 本篇收束

固定提交中的 analytics 是一条与业务解耦、但能跨层拼接丰富语义的投影管线：

- App-server、Core、extensions和outgoing共享同一个cloneable client与queue worker；
- 显式 `analytics=false`关闭event queue，disabled client让调用点保持无条件可用；
- track方法先按variant选择，避免高频或无目标价值payload进入queue；
- facts用容量256的try-send queue非阻塞入队，满/闭合时宁可丢analytics也不阻塞业务；
- 单一 reducer按compound request、thread、turn、item和review ID串行维护关联state；
- initialize fact建立connection/client/runtime context，但发生在response交付之前；
- initialized request在serialization/RPC gate之前track，因此fact不证明handler已poll；
- response在outgoing send之前track，因此fact不证明client已收到；
- ThreadStart/Resume/Fork response生成统一thread initialized event，并用mode区分来源；
- thread originator可只覆盖product client ID，使事件归属于真实产品来源；
- TurnStart response只建立request→turn关联，不是Turn业务成功；
- resolved config、profile、completed是Turn event readiness关键pieces，token/diff/error等是可选enrichment；
- TurnCompleted提供Completed/Failed/Interrupted终态，emit后reducer remove TurnState；
- ItemStarted/Completed、reverse approval request/response/abort等通过配对生成tool/review events；
- effective permissions使用专门入口，记录系统实际采用结果；
- JSON-RPC ErrorResponse fact主要清pending state和结构化TurnSteer rejection，不是通用error event；
- typed error taxonomy在知道根因的TurnProcessor产生，避免解析message文本；
- notifications即使没有subscriber也可能先track，analytics不等于delivery log；
- reducer缺context、auth过滤、HTTP失败、queue full都只导致观测缺失；
- API key auth只允许当前代码定义的小部分plugin-related events；
- HTTP发送有10秒timeout，flush有25秒queue barrier，但没有durable retry/exactly-once；
- flush只输出已有和特定pending tool events，不会伪造缺失的Turn/review terminal facts；
- end-to-end tests验证thread originator、图片准备、profile、token、成功/失败TurnSteer等跨层组合。

下一篇适合继续精读 App-server 与 Core 的错误 taxonomy：JSON-RPC code、`CodexErr`、`TurnError`、structured `CodexErrorInfo`、retryable stream error 和 user-facing message 怎样逐层转换，为什么“同一句报错”在不同边界可能代表完全不同的恢复策略。

返回[源码精读目录](README.md)，或查看[课程术语总表](../glossary.md)。
