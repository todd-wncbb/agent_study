# 79：Rust Tracing、日志、Span、结构化诊断与 OTEL 上下文

> 源码基线：`4ee41929eaf4`。本章解决“代码怎样留下可查询、可关联、容量有界且不泄漏敏感信息的运行证据”。

## 1. 本章解决什么问题

看到 `tracing::info!`、`#[instrument]`、`info_span!`、`.instrument(...)` 和 `otel.name` 时，不能只把它们理解成“打印日志”。它们分别创建结构化 event、时间范围、父子关系和跨进程 trace 上下文。

## 2. 先说人话：Observability 是运行时证据

源码描述可能发生什么；日志、trace 和 metric 记录实际发生了什么。好的证据能回答：哪个请求、哪个 thread/turn/tool、在哪个阶段、耗时多久、以什么终态结束。

## 3. Log Line

传统日志常是一段文本：

```text
failed to reload config for thread abc
```

人容易读，但机器要解析 thread ID、error type 和阶段很困难。

## 4. Structured Event

```rust
tracing::warn!(
    %thread_id,
    %err,
    "failed to reload thread configuration"
);
```

Message 保留给人，`thread_id`、`err` 是独立字段，collector 可筛选、聚合或格式化。

## 5. Event

Event 是某一瞬间发生的记录，如请求收到、retry 开始、工具失败、配置被忽略。它有 level、target、fields、message，并处于某个当前 span context 中。

## 6. Span

Span 表示一段有开始和结束的工作，如 `app_server.request`、`run_turn`、tool dispatch。它携带字段，并可成为其他 span/event 的父级。

## 7. Trace

Trace 是同一次分布式操作相关 span 的树或图。一个 app-server request 可向下包含 thread 操作、model request、tool call，并通过 W3C context 延伸到其他进程。

## 8. Metric

Metric 是适合聚合的数值时间序列，如请求计数、错误率、TTFT histogram、in-flight gauge。它不保存每次执行的完整故事，和 trace/log 互补。

## 9. Event、Span、Metric 的分工

- Event：这一刻发生了什么；
- Span：这段工作如何开始、嵌套、结束；
- Metric：大量执行总体怎样变化；
- Error object：失败原因链；
- Stable ID：把不同证据关联起来。

## 10. Level：TRACE

TRACE 适合非常细的内部阶段和高频控制流，通常生产默认关闭。它应帮助深度诊断，而不是承载唯一的关键失败证据。

## 11. Level：DEBUG

DEBUG 面向开发和现场排查，记录选择、cache 分支、queue key 等。开启时量可能很大，因此仍需避免完整 payload 和无界 collection。

## 12. Level：INFO

INFO 记录正常生命周期中的重要节点：请求、任务、工具调用、启动/关闭。若每个循环元素都打 info，真正重要事件会被淹没。

## 13. Level：WARN

WARN 表示系统仍能继续，但出现降级、无效配置、可恢复失败或值得关注的异常。长期常态出现的 warn 会训练操作者忽略警报。

## 14. Level：ERROR

ERROR 表示当前操作失败或关键不变量受损。不要在每一层重复记录同一个 error；通常由真正处理或终结该失败的边界记录一次，并保留 source chain。

## 15. `%field` 与 `?field`

Tracing 宏中 `%value` 使用 Display，适合稳定、人类可读表示；`?value` 使用 Debug，适合开发结构。两者都不自动保证脱敏。

## 16. Field Shorthand

```rust
tracing::info!(thread_id = %session.thread_id, "turn started");
```

字段名应稳定且表达语义。不要把 `format!("thread={id}")` 塞进 message，再让查询系统解析文本。

## 17. Message 与 Field 不要重复承担合同

Message 可以改进措辞；dashboard/query 应依赖稳定 field/target/event name。若自动化依赖整句英文，文案修改会意外破坏诊断系统。

## 18. Target

Target 是 event/span 的逻辑来源，默认常接近 Rust module path，也可显式指定。Filter 和 exporter routing 可按 target 开关不同数据流。

## 19. Codex OTEL Target Routing

`codex-rs/otel/src/targets.rs` 区分 log-only 与 trace-safe target。并非所有 event fields 都适合进入 trace exporter；target 也是数据治理边界。

## 20. Callsite Metadata

Tracing 宏位置产生相对静态的 metadata，如 name、target、level 和 field names；每次执行填入 field values。稳定字段 schema 比动态拼接字段名更易聚合。

## 21. Subscriber

Subscriber 接收 span/event，决定是否启用、怎样记录 parent、如何格式化和输出。业务代码发 tracing event，不应直接耦合某一种终端或 OTLP exporter。

## 22. Layer

Tracing subscriber 可组合 formatting、EnvFilter、OpenTelemetry、测试 capture 等 layers。同一 event 可被日志层显示，也可被 trace 层导出，具体取决于 filter 和 target policy。

## 23. Filter

Filter 按 level、target 等选择是否启用 callsite。关闭高频 TRACE 可降低成本，但不能把系统正确性依赖于某个 subscriber 一定存在。

## 24. Disabled 不等于完全免费

Tracing 会尽量在 callsite 早期判断是否启用，但昂贵 payload 预处理若写在宏外仍会执行。先做有界、必要的摘要，并避免为了日志 clone 整个大对象。

## 25. Span Parent

Child span 继承 parent 的 trace identity 和上下文字段关系。树形结构让查询者从一次失败 tool call 回到所属 turn/request，而不只靠时间邻近猜测。

## 26. Contextual Parent

在当前 span 内创建的新 span 通常以当前 span 为 parent。异步 task、channel 和跨进程边界可能脱离当前 context，需要显式传播。

## 27. Explicit Parent

Tracing 宏支持 `parent: &span` 指定父级。Codex response handling 为 receiving child span 显式指定 handle-responses parent，避免依赖当时 ambient context。

## 28. Enter Guard

同步代码可 `let _entered = span.enter();`，guard scope 内事件属于该 span。Guard 不应跨 await 随意持有，因为 task 可能切换并污染线程上的当前 context。

## 29. Instrumented Future

`.instrument(span)` 包装 Future，使每次被 poll 时进入对应 span，Pending 后退出。它追踪异步执行，而不只是创建 Future 的那一瞬间。

## 30. `#[tracing::instrument]`

Attribute 自动为函数调用建立 span，并可配置 name、level、skip、fields、ret、err。仓库偏好在 async 函数定义上 instrument，让 context 与被测工作绑定。

## 31. `skip_all`

默认自动记录函数参数可能带来巨大 Debug 输出或敏感信息。Codex 广泛使用 `skip_all`，再显式添加安全、高价值 fields。

## 32. `err`

`#[instrument(..., err)]` 在 Result 返回 Err 时记录 error。它减少手写重复，但仍要确认 error formatting、level 和 source chain 是否适合 exporter。

## 33. Function Span Name

可用 `name = "agents_md.refresh"` 等稳定领域名，不必暴露 Rust module 重构路径。Span 名称应描述操作，而不是把动态 ID 拼进名称。

## 34. Dynamic Values 应放 Field

错误：`info_span!(format!("turn-{thread_id}"))`。正确方向是稳定 name `turn`，动态 `thread.id = %thread_id`。这让聚合不会为每个 ID 创建新 operation name。

## 35. `field::Empty`

某些字段在 span 创建时未知，可先声明 `field::Empty`，后续用 `span.record(...)` 填入。字段必须在 callsite schema 中预先存在。

## 36. App-server Request Span

`app_server_request_span_template` 统一 name 为 `app_server.request`，记录 `otel.kind=server`、RPC method、transport、request/connection ID、API version，并预留 client 和 turn fields。

## 37. 跨 Transport 保持同一 Shape

Stdio、Unix socket、WebSocket 和 in-process caller 使用可比较的 request span fields，只把 `rpc.transport` 改为对应值。这样 dashboard 不会因入口不同而拆成不兼容数据。

## 38. Late Record Client Info

Request span 创建后，初始化请求或 session state 提供 client name/version，再通过 `span.record` 写入预声明字段。Late binding 不需要重建 span。

## 39. Tool Dispatch Span

工具路径创建 `dispatch_tool_call_with_code_mode_result` trace span，记录 `otel.name`、tool name、call ID 和 `aborted=false`，让同一工具执行的等待、运行和取消归在一处。

## 40. Mutable Span Field

取消分支胜出时，源码对同一 span 执行 `record("aborted", true)`。Span 描述一段工作的最终属性，不必另造互不关联的“开始/取消”文本行。

## 41. `.in_current_span()`

返回的 async block 用 `in_current_span()` 继承调用处当前 span。它适合保持 ambient context；若需要专门操作名和 fields，显式 `.instrument(span)` 更清楚。

## 42. Spawn 的 Context

不要假设新 spawn task 自动拥有期望 parent。Codex 常在 `tokio::spawn(async move {...}.instrument(span))` 中显式绑定，确保 task 在任意 worker thread 被 poll 都属于正确 span。

## 43. Request Context Registry

App-server 在执行 request Future 前注册 RequestContext，并用 request span instrument Future。异步 response 在稍后发送时仍可找到原请求 context，避免 outbound 工作脱离父链。

## 44. Correlation ID

ThreadId、turn sub-ID、call ID、RPC request ID、connection ID 各关联不同实体。字段名必须说明是哪一种 identity，不能都叫 `id`。

## 45. Trace ID 与业务 ID

Trace ID 关联一次观测链，重试可能创建新 span；ThreadId 持续跨多次请求和进程恢复。Trace ID 不能代替业务 identity，业务 ID 也不能表达 parent-child timing。

## 46. Span ID

同一 trace 内每个 span 有独立 span ID，并通过 parent span ID 形成树。查询一次具体 tool dispatch 时，trace ID 找整条链，span ID 找当前工作节点。

## 47. W3C Trace Context

`traceparent` 携带 version、trace ID、parent span ID 和 flags；`tracestate` 携带 vendor state。它们是跨 HTTP、JSON-RPC 或进程环境传递 parent context 的标准 carrier。

## 48. Inbound Context

App-server 从 JSON-RPC request 的 trace carrier 提取 W3C context，并用 `set_parent_from_w3c_trace_context` 设为 request span parent。有效 carrier 让上游和本地 trace 连成一条。

## 49. Invalid Carrier

无效 traceparent 不应让业务请求失败。源码记录带 rpc method/request ID 的 warn，然后忽略 carrier，继续建立本地 span。

## 50. Environment Parent

若请求没有显式 parent，App-server 可读取进程 `TRACEPARENT`/`TRACESTATE` 环境上下文。解析结果存入 OnceLock，避免每次重复读取和歧义变化。

## 51. Outbound Injection

`inject_span_w3c_trace_headers` 从指定 Span 生成 traceparent/tracestate，并替换 header map 中旧值。指定 span 是 source of truth，避免复用 request 时携带陈旧 parent。

## 52. Context Propagation 不是复制所有 Fields

跨进程通常只传 trace identity 和采样状态，不会自动传 thread ID、用户 ID 或所有 span fields。需要的业务字段仍应在下游安全地重新记录。

## 53. Sampling

Trace sampling 决定哪些执行保存完整 span。即使某 trace 未采样，关键错误 counter/log 仍可能需要独立保留；不能把可靠性告警只建立在低比例 sample 上。

## 54. Cardinality

字段不同值数量称 cardinality。Thread ID、call ID 很适合 trace/log 查询，却通常不适合 metric label，否则时间序列数量会爆炸。

## 55. Metric Label

Metric tag 应使用低基数、有限集合，如 outcome、transport、tool category。把完整路径、错误 message 或用户 ID 放入 label 会造成容量和隐私问题。

## 56. Error Classification

聚合需要稳定 error code/category；人类诊断需要有界 message 和 source chain。只记录任意 error string 很难统计，同一错误还可能包含动态路径和 ID。

## 57. `%err` 与 `?err`

Display 通常给当前错误的人类摘要，Debug 可能展示结构或 chain，具体取决于 Error 类型。选择前要查看实现，不能假设 `?err` 一定包含全部 source 或一定安全。

## 58. Error 记录一次

底层若只是加 context 并向上传播，不必每层 error!；最终处理边界记录 error 与 stable fields。中间层只有在降级、重试或吞掉错误时才需要独立 event。

## 59. Retry Event

Retry 不是同一失败的重复 error log。应记录 attempt、max attempts、reason category、backoff 和最终 outcome，让查询能区分短暂恢复与耗尽失败。

## 60. Secret 与 PII

Token、Authorization header、cookie、用户 prompt、完整环境变量、文件内容和外部 tool payload 都可能敏感。结构化日志不会自动脱敏，Debug derive 也可能泄漏字段。

## 61. `skip_all` 只是第一道门

跳过自动参数记录后，手工 event/span fields 仍需审核。尤其不要为了“方便诊断”把整个 request、config 或 error response 直接 `?` 输出到默认生产日志。

## 62. Bounded Payload Preview

Codex 工具日志对 preview 设独立 byte/line 上限。模型上下文有 token limit，不代表 telemetry 可以复用同样大 payload；每个观测出口都要有自己的 hard cap。

## 63. Tool Payload 日志

`handle_output_item_done` 记录 thread ID、tool name 与经过处理的 payload preview，而不是无界输出完整内部对象。不同 tool payload 先标准化为日志视图。

## 64. Path 与 Command

路径、argv、cwd 可能包含用户名、仓库名、secret 参数。若诊断不需要完整值，应记录 basename、类别、hash 或长度；必要明文需要明确数据政策和 retention。

## 65. Log Injection

不可信文本可能包含换行和伪造前缀。结构化 exporter 能减少文本解析风险，但终端 formatter 仍需正确转义；不要让用户输入成为动态 target/field name。

## 66. Event Volume

高频 loop 每项打 event 会增加 CPU、I/O 和存储。优先记录聚合 count、首个错误、状态变化或 bounded sample，并用 metric 表达总体数量。

## 67. Span Duration

Span close time可给 wall-clock 范围，但包含排队、await 和暂停。若要区分 sampling、tool blocking、compaction 等 active phase，需要专门 timing state，而非只看父 span 总时长。

## 68. Turn Timing Guard

`TurnProfileTimingGuard` 在 phase 开始时记录 Instant，Drop 时结束 phase；TurnTimingState 分别累计 sampling、compaction、tool blocking 等时长，再输出 TurnProfile/metrics。

## 69. TTFT 与 TTFM

Time to first token/message 是定义明确的边界 metric。必须固定开始事件、第一项判定和单位；不要从日志行时间戳临时推算一个含义不稳定的指标。

## 70. Monotonic 与 Wall Clock

Duration 使用 Instant 避免系统时钟回拨；跨进程时间戳使用 Unix/SystemTime。Tracing timestamp 与业务 duration 不是同一时间语义。

## 71. Tracing Test

测试可安装 capture subscriber，驱动 request，再断言 span name、parent、fields 或 export route。不要依赖开发终端格式字符串，因为 formatter 可替换。

## 72. App-server Tracing Tests

固定提交有 `message_processor_tracing_tests.rs`，通过独立 sibling test module 覆盖 request context/parent 行为。Tracing contract 也应被测试，而不是靠人工看日志。

## 73. Subscriber 是 Global-ish State

Default subscriber 常受 thread-local/global scope 影响。并行测试安装 subscriber 时应使用 scoped dispatcher/harness，避免多个 case 争抢全局初始化。

## 74. Diagnose Missing Span

- Callsite 是否被 filter 关闭？
- Future 是否真正被 instrument 后 poll？
- Spawn 是否显式携带 span？
- Parent carrier 是否有效？
- Exporter 是否只接受特定 target？
- Provider 是否 flush/shutdown？
- 查的是 trace ID、业务 ID，还是错误时间窗？

## 75. Diagnose Wrong Parent

先画 task/channel/process 边界；检查创建 span 时的 ambient context、显式 parent、spawn wrapper、RequestContext registry 和 W3C extract/inject。仅靠相邻时间戳不能证明 parent 关系。

## 76. Diagnose Noisy Logs

按 target/level 统计 volume，找高频循环、重复 error、动态 operation name、高 cardinality field 和超大 payload。修复应调整事件模型，不只是把全局 level 调高或调低。

## 77. 新 Event 设计清单

- 这是瞬时事件还是有 duration 的 span？
- 正常、降级还是失败，level 是否合适？
- Message 给人看，哪些 fields 给机器查？
- 需要哪些 thread/turn/call/request identities？
- 字段 cardinality、长度和敏感性如何？
- 同一 error 是否已由上层记录？
- Metric 是否更适合表达总量？

## 78. 新 Span 设计清单

- Operation name 是否稳定且低 cardinality？
- Parent 来自 ambient、显式 span 还是远端 carrier？
- Async Future 是否整个 poll 生命周期都 instrument？
- 哪些字段创建时未知，需要 Empty + record？
- 成功、取消、timeout 和 error 终态如何表达？
- Spawn/channel/outbound 边界如何传播 context？

## 79. 源码检查点

1. `codex-rs/app-server/src/app_server_tracing.rs`：统一 request span、transport/client fields 与 W3C parent。
2. `codex-rs/app-server/src/message_processor.rs`：RequestContext registry 和 instrumented request Future。
3. `codex-rs/otel/src/trace_context.rs`：extract、inject、environment fallback 与 trace ID。
4. `codex-rs/otel/src/targets.rs`：log-only/trace-safe routing policy。
5. `codex-rs/core/src/tools/parallel.rs`：tool dispatch span、aborted late record、current span inheritance。
6. `codex-rs/core/src/tools/router.rs`：`#[instrument(skip_all, err)]`。
7. `codex-rs/core/src/stream_events_utils.rs`：bounded tool payload event 与 thread identity。
8. `codex-rs/core/src/turn_timing.rs`：phase guard、TTFT/TTFM 与 profile duration。
9. `codex-rs/app-server/src/message_processor_tracing_tests.rs`：tracing behavior tests。

## 80. 搜索命令

```bash
rg -n '#\[(tracing::)?instrument|info_span!|trace_span!|\.instrument\(' codex-rs/core/src codex-rs/app-server/src
rg -n 'tracing::(trace|debug|info|warn|error)!' codex-rs/core/src codex-rs/app-server/src
rg -n 'traceparent|tracestate|set_parent|inject_span' codex-rs/otel/src codex-rs/app-server/src
rg -n 'field::Empty|\.record\(' codex-rs/core/src codex-rs/app-server/src
```

## 81. 小练习：设计 Request Trace

为一次“turn/start → model → tool → completed”画 span tree。给出低基数 operation name，并把 ThreadId、turn ID、call ID、RPC request ID 放到正确字段；再标出跨 app-server/core/model 边界的 traceparent 传播点。

## 82. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Event / field / message | 事件/字段/消息 | 瞬时结构化记录、机器字段和人类摘要 |
| Span / parent / child | 时间范围/父/子 | 一段工作及其嵌套因果关系 |
| Trace / trace ID / span ID | 追踪/追踪 ID/范围 ID | 跨组件操作树、整树身份和单节点身份 |
| Level | 级别 | TRACE、DEBUG、INFO、WARN、ERROR 的诊断重要性 |
| Target / callsite | 目标/调用点 | 逻辑路由来源与宏位置的静态 metadata |
| Subscriber / layer / filter | 订阅器/层/过滤器 | 接收、组合处理和选择 tracing 数据的组件 |
| `instrument` / current span | 插桩/当前范围 | 将函数或 Future poll 绑定到 span context |
| Ambient/explicit parent | 环境/显式父级 | 从当前 context 继承或直接指定因果 parent |
| `field::Empty` / record | 空字段/补录 | 预声明 schema 并在值已知后写入 |
| Correlation ID | 关联 ID | 连接不同事件、请求和业务实体的稳定身份 |
| W3C trace context | W3C 追踪上下文 | traceparent/tracestate 跨进程 parent carrier |
| Extract / inject | 提取/注入 | 从 carrier 读取和向 header 写入 trace context |
| Sampling | 采样 | 选择保留哪些 trace 以控制成本 |
| Cardinality | 基数 | 字段或 metric label 的不同值数量 |
| OTLP / exporter | OpenTelemetry 协议/导出器 | 将 trace、log、metric 发送到收集后端的组件 |
| Redaction / PII | 脱敏/个人信息 | 删除或替代秘密及可识别用户的数据 |
| Payload preview / hard cap | 载荷预览/硬上限 | 有界诊断摘要和不可超过的容量限制 |
| TTFT / TTFM | 首 token/首消息时间 | 从固定开始边界到首次 token/message 的 duration |
| Flush / shutdown | 刷出/关闭 | 将缓冲 telemetry 交给 exporter 并有界结束 provider |

## 83. 常见误解

- “Tracing 就是彩色 println”：它还保存 fields、span 关系和跨进程 context。
- “有 thread_id 就不需要 trace ID”：两者关联的生命周期不同。
- “Spawn 会自动继承正确 parent”：异步边界应显式验证/instrument。
- “skip_all 就完成脱敏”：手工字段和错误仍可能泄密。
- “Debug 比 Display 信息多所以总用 `?`”：它也可能暴露内部和敏感数据。
- “Span 总时长等于 CPU 时间”：它可能包含 await、排队和阻塞。
- “所有 ID 都适合 metric label”：高 cardinality 会制造大量时间序列。
- “每层记录 error 更容易排查”：重复日志会放大噪声并扭曲错误率。
- “关闭 TRACE 后日志完全无成本”：宏外预处理和错误的 payload clone 仍会执行。

## 84. 一句话收束

Event 记录瞬时事实，Span 保存一段工作的字段与父子关系，W3C context 把关系跨进程延续，Metric 汇总总体趋势；稳定 name/ID、显式 async context、有界低敏 fields、正确 level 和单一终态记录，才能让运行证据既可查询又可信。
