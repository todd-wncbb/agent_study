# Grok Build 可观测性、日志、Trace、指标与诊断：一次 Agent Turn 如何留下证据

> 本文回答一个工程上极其实际的问题：当用户说“它卡住了”“工具明明执行了却没显示”“为什么这次特别慢”“重连以后消息重复了”，Grok Build 到底留下了哪些证据，应该查哪一套日志，又有哪些数据出于隐私或性能考虑根本不会记录。

---

## 1. 先给结论

Grok Build 的可观测性不是一个 logger，而是多套用途不同的系统：

1. Rust `tracing`：开发日志、span 与通用错误诊断。
2. Debug firehose：按 Session 路由的详细本地日志。
3. Unified log：Shell、Pager、Desktop 跨进程统一时间线。
4. `events.jsonl`：每个 Session 的 turn/tool/permission 生命周期事件。
5. Sampling log：专门记录模型采样链路的结构化调试信息。
6. Instrumentation：低开销 timing，可写 JSONL、Chrome Trace 或发往内部 OTLP。
7. Product telemetry：产品事件和 Mixpanel。
8. Internal OTEL traces：发往 xAI 可观测后端的 span trace。
9. External OTEL：双重 opt-in 后发往客户自己 collector 的日志与指标。
10. Sentry、trace artifact upload、memory/hook 专用日志等补充通道。

它们不能互相替代。`events.jsonl` 有 ToolCompleted，不代表 Unified Log 会有工具完整输出；OTEL span 有耗时，也不代表它包含用户 prompt；`updates.jsonl` 能恢复会话，却不是诊断性能的最佳数据源。

---

## 2. 为什么 Agent 特别需要多层可观测性

普通请求通常是：输入、处理、响应。Agent turn 则可能经历：

```text
用户输入
  ↓
排队 / 合并插话
  ↓
MCP 初始化与工具收集
  ↓
模型流式 Sampling
  ↓
工具调用 / 权限等待 / 重试
  ↓
再次 Sampling
  ↓
压缩、TodoGate、Goal 验证
  ↓
持久化 / 客户端发送 / trace 上传
```

“慢”可能是模型慢、MCP 慢、用户审批慢、工具慢或上传 drain 慢。只有一条文本日志很难可靠拆分这些阶段。

---

## 3. 总体数据流

```text
业务调用点
   ├── tracing::{debug,info,warn,error}! / spans
   │      ├── stderr/TUI subscriber
   │      ├── debug firehose
   │      ├── instrumentation target filter
   │      └── internal OTEL trace layer
   │
   ├── unified_log::{info,warn,error}
   │      └── ~/.grok/logs/unified.jsonl
   │
   ├── EventTracker::emit
   │      └── session/events.jsonl
   │
   ├── log_event / log_session_event
   │      ├── product events endpoint
   │      ├── Mixpanel
   │      └── external OTEL mapping
   │
   └── target: sampling_log
          └── ~/.grok/logs/sampling.jsonl
```

关键设计是“一次业务动作可以向多个独立 sink 发射不同粒度的数据”，而不是把同一 payload 广播到所有地方。

---

## 4. 观测通道总表

| 通道 | 默认位置/去向 | 内容 | 主要用途 | 失败策略 |
| --- | --- | --- | --- | --- |
| stderr tracing | 终端/TUI | 过滤后的开发日志 | 即时排错 | 不影响业务 |
| debug firehose | `~/.grok/debug/*.txt` | first-party debug + ACP 摘要 | 深度本地诊断 | 非阻塞、best-effort |
| unified log | `~/.grok/logs/unified.jsonl` | 跨组件结构化事件 | 事故时间线 | 限长、自动修复 writer |
| Session events | `session/events.jsonl` | turn/tool/permission 生命周期 | 行为分析 | best-effort |
| sampling log | `~/.grok/logs/sampling.jsonl` | Sampling 专用事件 | 模型链路调试 | 默认关闭 |
| instrumentation | log/chrome/server | timing/span | 性能剖析 | 模式化开关 |
| product telemetry | xAI events/Mixpanel | 类型化产品事件 | 产品指标 | fire-and-forget |
| internal OTEL | xAI OTLP endpoint | 脱敏 spans | 服务端诊断 | batch export |
| external OTEL | 客户 collector | 明确 schema 的 logs/metrics | 企业自有监控 | 双 opt-in、默认无内容 |

---

## 5. 启动时如何组装 tracing subscriber

非 TUI 模式入口位于：

```text
crates/codegen/xai-grok-pager-bin/src/main.rs
```

`init_tracing_simple()` 将多个 layer 叠加：

```rust
tracing_subscriber::registry()
    .with(stderr_fmt_layer)
    .with(sampling_log::layer())
    .with(instrumentation::layer())
    .with(hooks_log::layer())
    .with(otel_layer::build_otel_layer(...));
```

然后 `debug_log::install_firehose` 再安装 debug 路由，最后初始化 external OTEL。

每个 layer 都能按 target、level 和配置独立过滤同一个 tracing event。

---

## 6. 日志级别不是数据分类

`debug/info/warn/error` 描述严重性或调试价值，但不决定数据去哪。

真正路由通常取决于：

- subscriber layer 是否启用；
- target 名称，例如 `sampling_log`；
- `EnvFilter` / `RUST_LOG`；
- instrumentation mode；
- telemetry mode；
- external OTEL content gate；
- ZDR 和 managed requirements。

同一个 `info!` 可能进入 debug 文件和 OTEL，也可能只在本地，不能单看宏名推断数据外发。

---

## 7. 默认 stderr 策略

Headless 默认 filter 可以是 `off`，其他非 TUI 模式默认只显示 `error`，避免结构化协议 stdout/stderr 被大量日志污染。

若设置 `RUST_LOG`，使用用户 filter，同时对已知高频噪声 target 增加抑制规则。例如 rmcp 每次 SSE 重连可能持续 warn，订阅器可把它提高到 error 门槛。

这是“保留源代码事件、在 sink 侧降噪”，而不是删掉诊断点。

---

## 8. Debug firehose 的两种模式

代码位于：

```text
crates/codegen/xai-grok-telemetry/src/debug_log.rs
```

### Per-session 模式

`GROK_DEBUG_LOG=1` 时，按 Session 写入：

```text
~/.grok/debug/<session_id>.txt
~/.grok/debug/<role>-<pid>.txt
~/.grok/debug/latest.txt -> 最近打开的 Session 文件
```

### Single-file 模式

显式设置 `GROK_LOG_FILE=<path>` 或 `GROK_DEBUG_LOG=<path>` 时写一个平面文件。

前者适合长驻 leader 同时处理多个 Session；后者适合一次性复现。

---

## 9. Firehose 如何知道事件属于哪个 Session

`with_session_ctx` 创建 tracing span：

```rust
tracing::info_span!("session", session_id = %session_id)
```

Debug routing layer 在 `on_new_span` 读取固定字段 `session_id`，清洗成文件名安全的 key，存入 span extension。任何发生在该 span 后代中的事件都写入对应 Session 文件。

路由依赖字段名而非 span 名，源码用测试固定 `SESSION_ID_FIELD` 与构造点，防止静默漂移。

---

## 10. Span context 为什么比手工传 session ID 好

若每个深层函数都显式传 `session_id`：

- API 污染；
- 容易遗漏；
- spawn 后相关性丢失；
- library 层被 Session 概念耦合。

Tracing span 随 instrumented future 传播，子事件自动继承上下文。真正跨 channel、进程或未 instrument 的 task 时，再显式携带 traceparent/session metadata。

---

## 11. Session ID 文件名清洗

虽然 Session ID 通常是 UUID，路由器仍不信任输入：

- 只保留字母数字、`-`、`_`、`.`；
- 其他字符替换为 `_`；
- 空字符串和纯点值映射为安全常量；
- 不允许 `/`、`..` 逃逸 debug 目录。

可观测性文件同样是文件写入攻击面，不能因为“只是日志”跳过路径安全。

---

## 12. 为什么 debug writer 是 non-blocking

Debug firehose 可能处在模型 chunk、工具通知等热路径。同步磁盘写会把日志 IO 变成 Agent 延迟。

`tracing_appender::non_blocking` 将格式化后的行送到后台 worker。WorkerGuard 被集中保存，进程结束时 flush。

取舍是队列压力或突然退出时可能丢尾部日志；debug sink 的优先级低于业务正确性。

---

## 13. Firehose 的过滤策略

它对 first-party crate 开 debug，对依赖通常只开 info，并显式关闭 `sampling_log`，避免采样专用大流量混入普通 firehose。

ACP 有两个 target：

- `acp_update`：紧凑摘要，记录 kind、ID、status、payload size；
- `acp_update_payload`：完整 payload dump，容量和隐私风险更高。

普通发布过滤器只保留摘要，详细 payload 留给显式 debug 场景。

---

## 14. Firehose 资源代价

Per-session router 为每个见过的 Session 保留一个 writer、worker 和 file descriptor，进程生命周期内不回收。

这是 opt-in debug 功能的明确取舍：简单可靠的路由优先于无限长 leader 的 fd 最优使用。诊断长期进程时需要意识到这一点。

---

## 15. Unified Log 解决什么问题

Shell、Pager 和 Desktop 可能是不同进程。各自 stderr 时间线无法回答：

```text
用户按键发生在什么时候？
Pager 何时发 ACP？
Shell 何时开始模型请求？
通知何时返回 UI？
```

Unified Log 把三端事件归并到：

```text
~/.grok/logs/unified.jsonl
```

Shell 直接写；Pager/Desktop 通过 ACP `x.ai/log` notification 转发，由 Shell 代写。

---

## 16. Unified Log Entry schema

```rust
struct LogEntry {
    ts: String,
    src: LogSource,
    pid: Option<u32>,
    ver: Option<String>,
    lvl: LogLevel,
    sid: Option<String>,
    msg: String,
    ctx: Option<Value>,
}
```

字段意义：

- `ts`：毫秒 UTC 时间；
- `src`：shell、grok-pager 或 grok-desktop；
- `pid`：区分同时运行的进程；
- `ver`：识别 zombie/旧版本进程；
- `sid`：关联 Session；
- `msg`：稳定可读的动作标签；
- `ctx`：结构化上下文。

---

## 17. 为什么必须同时记录 src、pid 和 version

多个 Shell 可以同时 append 同一文件。只有时间戳和 source 时，不知道两条 shell 记录是否来自同一进程。

版本字段能发现：用户以为已经升级，但后台仍有旧 leader 写日志。`Option` 只为旧 wire 兼容；当前组件都会填写。

---

## 18. Unified Log 的大小治理

单文件最大约 5 MB。打开或维护时超过上限会 trim，而不是无限增长。

Writer 每隔约 2 秒检查：

- path 是否仍存在；
- 当前 file descriptor 是否仍指向该 path 的 inode；
- 文件是否超过限制。

时间驱动维护使低频进程也能发现 log rotate/unlink，而不是必须累计若干字节才检查。

---

## 19. Detached file descriptor 问题

另一个进程 trim 时可能 rename/unlink 文件。当前进程仍持有旧 fd，继续写只会把字节送进无路径 inode，用户永远看不到。

Unified writer 比较 `(dev, ino)`：

- path 指向新 inode时 reopen；
- path 丢失时尝试恢复；
- reopen 失败则标记 detached 并暂时丢弃写入；
- 后续 maintenance 再重试。

宁愿丢不可见日志，也不持续增长一个磁盘上没人能找到的文件。

---

## 20. Unified Log 与 Trace artifact

每 turn trace 上传可以按 Session 筛选 Unified Log 快照，上传路径类似：

```text
{session_id}/turn_{N}/unified_log.jsonl
```

这样服务端诊断一次模型 trace 时，也能看到前后端跨组件事件。但上传仍受 trace upload、凭证、ZDR 等门控。

---

## 21. `events.jsonl` 的定位

代码位于：

```text
crates/codegen/xai-file-utils/src/events/log.rs
crates/codegen/xai-file-utils/src/events/tracker.rs
crates/codegen/xai-file-utils/src/events/types.rs
```

它是每 Session 的领域事件日志，重点描述 turn 生命周期，而不是任意开发日志。

每行包含 RFC3339 毫秒时间和 tagged `Event`，schema version 当前为 `1.0`。

---

## 22. 基础 Turn 事件

核心事件包括：

```text
turn_started
phase_changed
first_token
loop_started
tool_started
tool_completed
permission_requested
permission_resolved
interjected
yolo_toggled
turn_ended
```

此外还有 TodoGate、LazinessDetector、Goal classifier/planner/strategist/summarizer 等专用事件。

这种 enum schema 比任意字符串日志更适合稳定统计。

---

## 23. EventWriter 的失败策略

EventWriter：

- 打开 `events.jsonl` append 文件；
- 用 `Arc<Mutex<Option<File>>>` 支持 Clone + Send + Sync；
- emit 时添加时间戳并写一行；
- 打开或写入失败只 `warn!`；
- 可创建 noop writer。

它是 best-effort 观测，不允许因为指标日志磁盘满而让用户 turn 失败。

---

## 24. EventTracker 为什么是 `!Send`

Tracker 包含 `Cell`、`RefCell` 和 actor-local 状态：

- turn end 是否已发；
- 当前 active tool；
- 本 turn tool count；
- 上一 turn interruption marker；
- 下一 turn reminder。

它只活在 Session actor。后台 task 只拿 `tracker.writer()` 得到可 Clone、Send、Sync 的 EventWriter。

这避免多个线程竞态修改“当前 active tool”语义。

---

## 25. TurnEnded 防重

正常完成、取消、错误清理等多条路径都可能尝试结束 turn。`emit_turn_ended` 使用 `Cell<bool>` 的 replace guard：

```rust
if self.turn_ended_emitted.replace(true) {
    return;
}
```

这保证一个 turn 最多一个结束事件，否则成功率、取消率和 duration join 都会被双计。

---

## 26. Tool 取消时的补偿事件

Tracker 保存 active tool 的：

- tool name；
- tool call ID；
- dispatch wall duration。

Turn 被取消时，如果工具在 flight，先发 `ToolCompleted(cancelled)`，再发 TurnEnded。否则分析系统会看到永远不闭合的 ToolStarted。

若工具仍在 dispatch、尚未被标为 active，则不会伪造 completed 行。观测代码承认状态边界，而不是追求表面配对率。

---

## 27. Permission 等待如何计时

请求审批时发：

```text
PhaseChanged(PermissionPrompt)
PermissionRequested(tool_name)
```

并返回 `Instant`。决定后发：

```text
PermissionResolved(tool_name, decision, wait_ms)
PhaseChanged(ToolExecution)
```

所以 turn 慢可以区分为用户审批等待，而不是错误归到 tool execution。

---

## 28. Interruption marker 的跨 turn 语义

取消原因不能在 `begin_turn` 时清空，因为下一条真实用户 prompt 需要知道上一 turn 被中断。

Tracker 保存 one-shot：

- prior interrupt category；
- redirect kind：cancel-then-send / queued-after-cancel；
- pending interrupt reminder。

消费者 `take_*` 后清除。它们既服务 telemetry，也影响下一 turn 给模型的提醒。

---

## 29. `events.jsonl` 不是 Session 恢复日志

它可能缺行、写失败、被关闭，并且不保存完整 ConversationItem。恢复使用 `updates.jsonl`、chat history 和领域快照。

`events.jsonl` 回答“turn 经历了哪些阶段”，`updates.jsonl` 回答“客户端历史是什么”。名字相近，职责完全不同。

---

## 30. Sampling Log

Sampling 专用 layer 只接收：

```rust
target: "sampling_log"
```

通过 `GROK_LOG_SAMPLING=1` 或 CLI 开关启用，写入：

```text
~/.grok/logs/sampling.jsonl
```

默认关闭，因为模型流事件容量高、可能包含更敏感的推理上下文，也会干扰普通 debug 信噪比。

---

## 31. Sampling Log 的文件治理

它复用 Unified Log 的 5 MB 上限和 trim helper，使用非阻塞 appender，输出 JSON、RFC3339 时间和 span ancestor 列表。

普通 debug filter 显式 `sampling_log=off`，避免同一事件重复写进两个大文件。

---

## 32. Instrumentation 与普通 tracing 的区别

Instrumentation 只收固定 target：

```text
xai_grok_instrumentation
```

目标是稳定记录耗时，而不是描述任意业务状态。`InstrumentationTimer` 在构造时记 `Instant`，Drop 时发：

```text
event="timing"
name=<operation>
elapsed_us=<duration>
fields=<optional structured fields>
```

RAII Drop 保证早退路径也有 timing。

---

## 33. Instrumentation 四种模式

```text
Disabled   真正 NoOp layer
Log        JSONL instrumentation.log
Chrome     直接生成 Chrome trace event
Server     通过 OTEL layer 发内部 server
```

`GROK_INSTRUMENTATION` 可选 `off/log/chrome/server` 等值。未显式设置时默认 Server。

`GROK_INSTRUMENTATION_LOG` 可以覆盖本地输出路径。

---

## 34. Log 模式

默认写：

```text
~/.grok/logs/instrumentation.log
```

JSON layer 包含 UTC 时间、thread ID、thread name、target 和完整祖先 spans。只过滤 instrumentation target，避免普通日志混入性能数据。

后台 non-blocking writer 的 guard 在 finalize 时释放和 flush。

---

## 35. Chrome 模式

Chrome layer 写：

```text
~/.grok/logs/instrumentation.trace.json
```

使用 async trace style，保留 args，可在 Chrome/Perfetto trace viewer 中查看并发时间线。

在 Chrome 模式下 Timer 主要通过 span enter/exit 表达 duration，不再额外保存 fields map，减少双重事件。

---

## 36. 从 JSON timing 转 Chrome Trace

`generate_chrome_trace` 也能离线读取 instrumentation JSONL：

1. 跳过损坏行；
2. 只保留正确 target 和 `event=timing`；
3. 支持新 `elapsed_us` 与 legacy `elapsed_ms`；
4. 用记录时间减 duration 得到 start；
5. 输出 Chrome `ph="X"` complete event。

若没有任何 timing event，明确报错，而不是生成空 trace 误导用户。

---

## 37. Panic 观测

Instrumentation 安装 panic hook：

- 提取 panic message 与 source location；
- 创建 `internal_error` span；
- internal pipeline 可保留较丰富信息；
- external OTEL 只发 error class，不发 message/location；
- 再调用默认 panic hook。

同一 panic 对不同 sink 采用不同数据最小化策略。

---

## 38. Prompt Timing 的阶段拆分

`PromptTiming` 记录：

- total turn duration；
- MCP wait；
- tool collection；
- model call；
- pre-model；
- MCP server/tool 数量；
- init strategy；
- model ID。

其中：

```text
tool_collection_ms = total_prep_ms - mcp_wait_ms
pre_model_ms        = total_ms - model_call_ms
```

使用 saturating subtraction 避免时钟/计量边界造成 unsigned underflow。

---

## 39. Product Telemetry 模式

`TelemetryMode` 有三档：

```text
Disabled        不发产品 telemetry
SessionMetrics  只发无内容的 Session 生命周期指标
Enabled         完整产品事件 + Mixpanel
```

配置接受 legacy bool，也接受字符串 `session_metrics`。未知值 fail closed，当作 Disabled 并警告。

---

## 40. 类型化 TelemetryEvent

调用点不是任意拼 JSON event name，而是定义实现 `TelemetryEvent` 的 struct：

```rust
log_event(PromptLatency { ... });
log_session_event(SessionStarted { ... });
```

优点：

- 字段由编译器检查；
- event name 可由测试固定；
- schema 变更容易审查；
- external 映射能与同一类型关联；
- 避免 dashboard key 随手漂移。

---

## 41. Product sink 路由

`TelemetryClient::track` 可以同时发送：

1. Product events HTTP endpoint；
2. Mixpanel。

自动补充：

- agent/user/team/deployment ID；
- shell version；
- client type/version；
- subscription tier；
- country/language/timestamp。

API key 在 Debug 实现中只显示 `***`，避免无意泄漏。

---

## 42. 事件名与 `$insert_id`

Shell/Workspace 使用不同 prefix：

```text
grok-shell-*
grok-workspace-*
```

wire `event_value` 去掉 prefix，便于跨 origin 聚合。

Mixpanel `$insert_id` 使用独立 UUID，不拼 event name，避免 36 字节截断后多个事件退化成同一个 ID 而被错误 dedup。

---

## 43. Fire-and-forget 与 drain

Product event 通过 `tokio::spawn` 异步发送，不阻塞 turn。全局 `PENDING_EVENTS` 计数未完成 post，guard 在成功、取消或 panic 路径都会递减。

一次性命令退出前可调用 `drain_pending(timeout)`。超时则记录 debug 并退出，不无限阻塞用户。

没有 Tokio runtime 时直接丢弃并 debug，不允许 `spawn` panic。

---

## 44. Ambient TelemetryCtx

`TelemetryCtx` 通过 `tokio::task_local!` 保存：

- session ID；
-共享 prompt index；
- 每 prompt UUID。

`with_session_ctx` 同时建立 task-local 和 tracing Session span。调用 `log_event` 时同步快照上下文，自动注入 session/turn correlation。

若 prompt index lock 正被占用，external snapshot 宁愿得到 `turn_number=None`，也不阻塞观测调用。

---

## 45. Prompt ID 与 Turn number 的区别

- turn number 是 Session 内单调业务位置，适合聚合和排序；
- prompt ID 是每 prompt 新 UUID，适合跨 event 精确关联；
- prompt ID 不进入 metrics，避免无限高 cardinality；
- event 可以同时带二者。

`begin_prompt_id()` 在 prompt index 增加的 turn start 位置轮换 UUID。

---

## 46. Internal OTEL Trace

内部 OTEL layer 把 tracing spans 桥接到 OpenTelemetry，发往 xAI trace endpoint。

Resource 包含：

- client name/version；
- service version；
- entrypoint；
- export 时动态补充 deployment/API key/org/team/user ID。

`GROK_OTEL_FILTER` 控制 span filter，默认 info，并关闭 sampling_log。

---

## 47. 动态凭证刷新

OTLP exporter 不能只在启动时复制 bearer token，因为长 Session 中 token 会刷新。

`RefreshableSpanExporter` 每批 export 读取 credential snapshot：

- 生成新的 Authorization header；
- 按凭证类型加入 token-auth header；
- 动态写 tenant resource attributes；
- 失败时尝试 refresh 后重试一次。

Debug 输出永远不显示 token 明文。

---

## 48. 为什么预建 blocking OTLP client

BatchSpanProcessor 的 export 可能运行在非 Tokio 标准线程。若在那里首次构造依赖 Tokio reactor 的 HTTP client/DNS 组件，会出现 “no reactor” panic。

实现初始化阶段预建 blocking-capable client，export 线程只组装当前凭证和 exporter 输入。

这是一类典型“可观测性代码反过来让业务崩溃”的陷阱。

---

## 49. Internal Trace 的默认拒绝脱敏

OTEL export 前对 SpanData 做批量 scrub：

- string attribute 只有 allowlist key 能保留；
- numeric/bool 标量默认安全；
- URL 降为 `scheme://host[:port]`；
- home path、secret 等被清洗；
- event free-text name 替换为静态 callsite；
- error description 保留但脱敏；
- link attributes 同样处理。

新出现的未知 content 类型默认视为敏感，fail closed。

---

## 50. 为什么 event message 要 neuter

Tracing event 的 name 往往就是格式化 message，它可能包含用户输入或错误响应，无法靠 attribute key allowlist 控制。

export 前把 event name 替换为由 `code.filepath`/`code.lineno` 构造的静态 callsite ID，再对路径清洗。

这样仍能聚合同一代码点的错误数量，但不外发任意 message。

---

## 51. External OTEL 是另一套系统

External OTEL 面向客户自己的 collector，提供 logs/events 和 metrics；它不是 internal xAI trace exporter 的别名。

两者方向和控制者不同：

```text
Internal OTEL  -> xAI observability backend
External OTEL  -> customer's OTLP collector
```

代码还防止 internal pipeline 已消费同一 `OTEL_*` 环境变量时 external 再启用造成 double-send。

---

## 52. External OTEL 双重 opt-in

激活必须同时满足：

1. `GROK_EXTERNAL_OTEL=1` 或对应本地配置启用；
2. `OTEL_METRICS_EXPORTER` / `OTEL_LOGS_EXPORTER` 至少一个为 `otlp` 或 `console`。

只有 master switch 不发送；只有 exporter 设置也不发送。

解析失败、未知 protocol 或两个 exporter 都为 none 时，模块不构造线程、socket 或分配资源。

---

## 53. External exporter 与 transport

每个 signal 可选择：

```text
none     不生成
console  脱敏后写 stderr，不碰 stdout 协议通道
otlp     发 collector
```

支持 `http/protobuf` 与 `grpc`。HTTP 默认 base 为 `localhost:4318` 并追加 `/v1/logs`、`/v1/metrics`；gRPC 默认 `localhost:4317`。

Signal-specific endpoint 优先于 base endpoint。

---

## 54. Collector headers 为什么只允许环境变量

External config 刻意没有 headers 文件字段。认证 header 只从 `OTEL_EXPORTER_OTLP_HEADERS` 及 signal-specific env 读取。

理由是 collector token 不应被持久写进 `~/.grok/config.toml`，更不应被 Session artifact、配置备份或问题报告带走。

Header parser 接受 `k=v,k2=v2`，跳过空 key。

---

## 55. External 内容门控

默认关闭：

```text
OTEL_LOG_USER_PROMPTS
OTEL_LOG_TOOL_DETAILS
```

即使 logs exporter 开启，也不会自动发送 prompt 文本、完整路径、tool 参数、MCP/skill/plugin 原名等细节。

管理员 managed requirements 可以把 gate pin 为 false；远端策略只能从 true 收紧到 false，不能远程替用户开启内容采集。

---

## 56. External 事件的映射流程

类型化 TelemetryEvent 映射成 `ExternalRecord`：

```text
event name
default attrs
gated attrs
metric increments
```

emit 时：

1. 根据 content gate 合并 gated attrs；
2. 注入 ambient session/turn/prompt context；
3. scrub secret/path；
4. 截断字符串；
5. 添加 event sequence；
6. 写 log record；
7. 更新预创建 metric counter。

这条路径同步且轻量，BatchLogProcessor 后台发送，不为每条 external event `tokio::spawn`。

---

## 57. External 字符串限制

所有字符串先 secret/path scrub。

- prompt 有约 60 KB 内容上限；
-普通 attribute 有更严格的常规长度限制；
- tool name 默认可以是清洗后的类别名；
- gate 开启后才可能用更详细值替换默认值；
- export-time validator 再做一层防御。

内容门控、scrub、truncate、validator 是叠加防线，不是任选其一。

---

## 58. External Metrics schema

预创建 counters 包括：

```text
session count
turn count
token usage
tool decision
tool usage
error count
```

单位分别类似 `{session}`、`{turn}`、`{token}`、`{decision}`、`{call}`、`{error}`。

Metrics 使用预定义 attr keys，避免调用点动态创建高 cardinality instrument。

---

## 59. Metrics cardinality 控制

External 配置可以控制：

- metrics 是否带 session ID，默认可开启但允许关闭；
- 是否带 app version，默认关闭；
- prompt ID 永远不进入 metrics；
-任意 request UUID 不作为默认 label；
- tool/detail 值经过分类和 gate。

高 cardinality 标签会让时序数据库成本和查询性能急剧恶化，因此“能关联”不代表“应该成为 metric dimension”。

---

## 60. Delta 与 Cumulative temporality

External metrics 支持 temporality preference，默认 Delta。

- Delta：每个 export interval 报本区间增量；
- Cumulative：从进程/资源起点累计。

Collector dashboard 必须按 temporality 正确解释，否则会把累计值重复求和或把增量误当总数。

---

## 61. Product telemetry 与 External OTEL 的独立 gate

`log_event` 会先尝试 external emit，再检查内部 product telemetry 是否 Enabled。

所以可能出现：

- product disabled，external active：只发客户 collector；
- product enabled，external inactive：只发 xAI product sinks；
- 两者都开：同一类型事件进入两套经过不同 schema/脱敏的 sink；
- 两者都关：无外发。

“one call site, two independently gated sinks” 避免业务代码写两遍。

---

## 62. ZDR 与 trace upload

Zero Data Retention team 会关闭 trace artifact upload。`TraceUploadReason` 明确记录原因：

```text
zdr_team
feature_off
no_credentials
direct_s3
proxy
direct_gcs
session_not_found
```

这些字符串是 dashboard contract，由测试固定。

ZDR 不应只在 UI 隐藏开关，而要在真正上传决策点阻断。

---

## 63. Trace Artifact Upload

每 turn 可以打包/上传与诊断有关的本地 artifact，例如 Session 片段、请求元数据和 Unified Log snapshot。

生命周期事件包括：

```text
TraceUploadAttempted
TraceUploadSucceeded { fully_uploaded }
TraceUploadSkipped { reason }
TraceUploadFailed { category, status_code }
```

区分 skipped 与 failed 很重要：没有凭证是配置状态，不应计入服务故障率。

---

## 64. W3C Trace Context 传播

`xai-file-utils/src/trace_context.rs` 使用标准 `traceparent`/`tracestate`：

- 从当前 tracing span 提取 context；
- 注入 outbound HTTP headers；
- 从 ACP `_meta.traceparent` 提取 remote parent；
- 创建 `acp_dispatch` child span；
- 在 channel/task 边界可显式携带 traceparent。

这使浏览器/Desktop → ACP → Shell → 模型 HTTP 请求共享同一 trace ID。

---

## 65. 为什么 `with_context_activation(false)`

Tracing-OpenTelemetry layer 不自动依赖 thread-local context 激活，而由 tracing span context 显式管理。异步 Rust task 可能在线程间移动，盲目 thread-local 继承很容易串 trace。

注入 HTTP 时优先取当前 span；仅在没有 span时才 fallback 到 OTel thread-local context，兼容 `spawn_local` 等路径。

---

## 66. Traceparent 输入校验

来自 `_meta` 的 traceparent 先由标准 propagator 解析，只有 span context valid 才设置 parent。

非法 header name/value 在 outbound 注入时被忽略并 debug，而不是 panic 或产生非法 HTTP 请求。

测试验证已知浏览器 trace ID 能一路进入 outbound request header。

---

## 67. 一个 Turn 的关联 ID

一次 turn 可能同时出现：

| ID | 作用 |
| --- | --- |
| session ID | 跨整个对话 |
| turn number / prompt index | Session 内业务顺序 |
| prompt ID | 每 prompt UUID |
| trace ID | 分布式调用树 |
| request ID | 单次后端/telemetry 请求 |
| tool call ID | ToolCall 与 ToolResult 配对 |
| event ID | ACP 更新游标与 dedup |
| collection ID | trace artifact 集合 |

不要尝试用一个 ID 替代所有层级；它们的生命周期与 cardinality 不同。

---

## 68. 如何还原一次慢 Turn

推荐顺序：

1. 用 Session ID 在 Unified Log 找 prompt enqueue/start/end。
2. 查 `events.jsonl` 的 phase、first token、permission wait、tool duration。
3. 查 PromptLatency 的 MCP wait/tool collection/model call。
4. 查 OTEL trace 的跨 HTTP span 和重试。
5. 必要时启用 instrumentation Chrome trace 看 task 并发。
6. 若怀疑 Sampling 内部，再显式启用 sampling log。

先用低敏、低容量证据缩小范围，再开启高容量日志。

---

## 69. 如何诊断“工具卡住”

```text
有 ToolStarted，无 ToolCompleted？
  ├── TurnEnded(cancelled) 且有补偿 ToolCompleted -> 用户取消
  ├── PermissionRequested 未 Resolved          -> 审批链路
  ├── dispatch 前无 active tool                -> registry/permission/queue
  └── active tool 一直存在                     -> executor/child process/MCP
```

再用 tool call ID 连接 conversation ToolCall/ToolResult，避免把同名工具的两次调用混在一起。

---

## 70. 如何诊断“UI 没显示但 Shell 已执行”

依次对照：

- Session events：业务工具是否完成；
- `updates.jsonl`：规范 ACP ToolCall/ToolResult 是否落盘；
- debug `acp_update`：Shell 是否发送；
- Pager Unified Log：是否接收/dispatch；
- event ID：是否被 cursor/dedup 丢弃；
- leader target：是否发给正确客户端。

不要因为 ToolCompleted 存在就直接判断 UI bug；中间还包括持久化、gateway、ACP 与前端 reducer。

---

## 71. 如何诊断“Telemetry 没数据”

先区分是哪一种 telemetry：

### Product events

- TelemetryMode 是否 Enabled/SessionMetrics；
- 是否有 endpoint/token；
- Tokio runtime 是否仍在；
- 进程是否在 drain 前退出。

### Internal OTEL trace

- instrumentation 是否 Server；
- traces exporter 是否 enabled；
- credential 是否 usable；
- OTEL filter 是否过滤 span。

### External OTEL

- master switch 是否开；
-至少一个 exporter 是否 active；
- internal pipeline 是否占用了变量；
- endpoint/protocol/header 是否有效；
- 内容 gate 是否只是隐藏了预期字段。

---

## 72. 日志中的时间不能盲目相减

不同进程使用各自 wall clock；跨机器的 workspace/proxy 时钟也可能偏移。`Instant` duration 在单进程内可靠，但 RFC3339 timestamp 跨进程只适合近似排序。

优先使用：

- span duration；
- `wait_ms` / `duration_ms`；
-同一 monotonic clock 计算值；
- trace parent-child 关系。

只有必要时才用两个组件 wall timestamp 相减，并考虑 clock skew。

---

## 73. 观测失败不能制造业务失败

大部分 sink 遵循：

- 写日志失败 warning 或静默 drop；
- HTTP telemetry 结果忽略；
- queue 满时丢 lossy 事件；
- drain 有 timeout；
- invalid config fail closed；
- exporter 线程不运行在核心 actor 热路径。

但仍需防范 logging recursion、锁内日志导致重入、后台 worker panic、无 runtime spawn 等二阶故障。源码多处专门为此绕开锁内 open 和 runtime 假设。

---

## 74. 隐私分层

可以把数据分为：

```text
Level 0  计数/时长/枚举，无用户内容
Level 1  Session/model/tool 分类与技术 ID
Level 2  路径、服务名、错误描述，必须 scrub
Level 3  prompt、tool 参数、完整 payload，显式 opt-in
Level 4  token/credential，永不记录
```

不同 sink 的默认等级不同。External OTEL 默认停在低等级；Sampling/debug payload 只有本地显式打开；credential 在任何 Debug formatter 中都应遮蔽。

---

## 75. 为什么仅靠“不要记录敏感信息”不够

可靠隐私需要机制：

- 默认拒绝 string key allowlist；
- free-text event name neuter；
- URL 只保留 origin；
- secret/path scrub；
- content gate 默认关闭；
- managed policy 只能收紧；
- collector token 只走 env；
- prompt ID 不进 metrics；
- unknown value fail closed；
- export-time 二次 validator。

Code review 口头约定无法覆盖未来新增字段和第三方错误文本。

---

## 76. Circuit Breaker 与重试观测

Circuit breaker observer 对 failure 发 trace，对状态变化发 warn/debug/info：

```text
Closed -> Open
Open -> HalfOpen
HalfOpen -> Closed
```

重试日志应包含 attempt、最终 outcome 和分类，而不是打印完整请求。否则 incident 中只看到大量相同 error，不知道系统是在恢复还是形成重试风暴。

MCP auto-restart 还用专用 tracing target 表达 attempted/succeeded/exhausted/skipped 指标。

---

## 77. Tracing-as-metrics 的局限

例如：

```rust
tracing::info!(target: "metrics.mcp.auto_restart.attempted", ...);
```

优点是无需独立 metrics client，现有 subscriber 可采集。缺点是：

- target 拼写成为隐式 schema；
- filter 可能误删指标；
- counter temporality 由后端推断；
-缺少编译期 instrument contract。

因此关键外部指标采用预创建 OTel Counter，而局部诊断计数仍可使用 tracing target。

---

## 78. 专用 Memory telemetry

Memory 子系统记录：

- 初始化配置与 chunk/file 数；
-搜索 query length、keyword count、result count、score、duration；
- flush outcome 和长度；
- injection 数量和耗时；
- reindex/watcher 统计；
- Session summary。

它明确不记录 query/prompt 内容，只记录长度、分数、数量和配置，属于 Enabled product telemetry。

---

## 79. 可观测性 schema 也是 API

Dashboard、告警、数据 join 依赖：

- event name；
- enum string；
- attribute key；
- metric name/unit；
- upload reason；
- session/turn/tool call ID。

源码测试固定这些值。重命名一个 Rust variant 若改变 serde snake_case，等价于 breaking analytics change。

---

## 80. 测试应该覆盖哪些方面

### 路由

- Session span 进入正确 debug 文件；
- span 外事件进入 role-pid fallback；
-非法 Session ID 不逃逸目录；
- `latest.txt` 原子切换。

### 生命周期

- TurnEnded 只发一次；
- cancel active tool 生成 completed；
- permission wait 正确；
- one-shot interruption 跨 begin_turn。

### Unified Log

- 多进程 append；
- trim 后 stale fd reopen；
- path unlink/replacement；
- 5 MB 上限；
- 测试写入私有临时目录。

### 隐私

-未知 string key 被删除；
- URL path/query 消失；
- secret/home path 被 scrub；
- prompt/tool detail gate 默认关闭；
- remote policy 不能开启 gate；
- panic external record 无 message/location。

### Trace

- `_meta.traceparent` 到 outbound header；
-非法 traceparent 被拒绝；
-重启/异步 task 不串 trace；
- token refresh 更新 export header。

---

## 81. 推荐源码阅读顺序

第一组，启动装配：

```text
crates/codegen/xai-grok-pager-bin/src/main.rs
crates/codegen/xai-grok-pager/src/tracing.rs
crates/codegen/xai-grok-telemetry/src/lib.rs
```

第二组，本地日志：

```text
crates/codegen/xai-grok-telemetry/src/debug_log.rs
crates/codegen/xai-grok-telemetry/src/unified_log.rs
crates/codegen/xai-grok-telemetry/src/sampling_log.rs
crates/codegen/xai-grok-telemetry/src/instrumentation.rs
```

第三组，Session 领域事件：

```text
crates/codegen/xai-file-utils/src/events/types.rs
crates/codegen/xai-file-utils/src/events/log.rs
crates/codegen/xai-file-utils/src/events/tracker.rs
```

第四组，产品 telemetry：

```text
crates/codegen/xai-grok-telemetry/src/client.rs
crates/codegen/xai-grok-telemetry/src/session_ctx.rs
crates/codegen/xai-grok-telemetry/src/events.rs
crates/codegen/xai-grok-telemetry/src/session_metrics.rs
crates/codegen/xai-grok-telemetry/src/prompt_timing.rs
```

第五组，Trace 与隐私：

```text
crates/codegen/xai-grok-telemetry/src/otel_layer/mod.rs
crates/codegen/xai-grok-telemetry/src/otel_layer/redact.rs
crates/codegen/xai-grok-telemetry/src/external/config.rs
crates/codegen/xai-grok-telemetry/src/external/emit.rs
crates/codegen/xai-grok-telemetry/src/external/schema.rs
crates/codegen/xai-file-utils/src/trace_context.rs
```

---

## 82. 常见误区

### 误区一：Telemetry disabled 等于没有任何本地日志

错。产品 telemetry、debug、Unified Log、Session events 是不同 gate。

### 误区二：开 OTEL exporter 就会发送 prompt

错。External OTEL 还需要 master switch，prompt/detail gate 默认关闭。

### 误区三：`events.jsonl` 可以恢复聊天

错。它是生命周期观测，不是 conversation event source。

### 误区四：所有 `info!` 都会外发

错。是否外发取决于 layer、target、filter 和脱敏。

### 误区五：日志越详细越好

错。高容量日志增加性能、磁盘、隐私和分析噪声成本。

### 误区六：有 timestamp 就能精确跨进程算耗时

错。优先使用 monotonic duration 和 trace parent-child。

---

## 83. 仍值得优化的方向

### 83.1 统一 Correlation Context

定义共享结构承载 session/turn/prompt/trace/request/tool-call ID，减少各通道字段名漂移。

### 83.2 本地诊断命令

自动读取 Unified Log、events 和 updates，按 Session/turn 生成阶段瀑布图和缺失配对报告。

### 83.3 Debug writer 的 LRU

为长期 leader 增加安全的 writer/guard 回收，避免每 Session 一个 fd 永久驻留。

### 83.4 Drop counter 自观测

非阻塞队列满、telemetry 无 runtime、drain timeout、detached writer drop 应有低成本累计计数。

### 83.5 Schema registry

集中登记 event/metric 名称、字段、privacy level、cardinality budget 和 owner，自动生成文档与兼容测试。

### 83.6 Trace sampling policy

对普通成功 turn 低比例采样，对错误、超时、doom loop、permission 卡顿自适应提高采样率，同时遵守 ZDR。

---

## 84. 最终心智模型

排查问题时不要问“日志在哪里”，而要依次问：

1. 我想证明的是业务状态、性能阶段、跨进程传输，还是产品统计？
2. 哪个 sink 对这个事实最权威？
3. 这个 sink 是否默认开启，失败是否会丢数据？
4. 用哪个 ID 把记录 join 起来？
5. 内容是否因 gate、脱敏或 cardinality 规则被刻意省略？

一条可靠的诊断链通常是：

```text
Unified Log 确定跨组件时间线
        ↓
events.jsonl 确定 Turn/Tool/Permission 状态
        ↓
PromptTiming / Instrumentation 确定慢在哪一阶段
        ↓
OTEL Trace 确定跨服务调用与重试
        ↓
updates/chat persistence 验证用户可见与模型状态
```

Grok Build 这套实现最值得学习的不是“接了 OpenTelemetry”，而是它把可观测性当成一组有不同可靠性、隐私级别、容量和消费者的产品。好的观测系统不是记录一切，而是在业务失败时留下足够证据，同时确保记录行为本身不会拖慢 Agent、泄漏用户内容或成为新的故障源。
