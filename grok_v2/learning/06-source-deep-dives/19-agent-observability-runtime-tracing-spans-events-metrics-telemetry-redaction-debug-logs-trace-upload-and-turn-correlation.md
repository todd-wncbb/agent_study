# 源码精读 19：Agent Observability Runtime 如何记录、关联、脱敏并导出一次 Turn

> 前几篇已经追踪 Prompt、Agentic Loop、Tool、Hook 和配置。本篇研究另一个贯穿所有模块的横切系统：可观测性。重点不是“有哪些日志”，而是回答一次 Agent turn 发生后，哪些事实会进入本地事件、Tracing Span、Unified Log、产品事件、Session Metrics、内部 OTLP、客户 External OTEL 与 Trace Artifact；这些记录如何关联、在哪里脱敏、何时可能丢失，以及排障时该信任哪一条管道。

## 0. 本文回答什么

1. Grok Build 为什么同时存在多套“事件”和“日志”；
2. `tracing::event!`、`tracing::span!`、Typed Telemetry Event 有什么区别；
3. Pager 怎样初始化一个由多个 `Layer` 组成的 Subscriber；
4. `TelemetryCtx` 怎样把 `session_id`、`turn_number` 和 `prompt.id` 注入异步任务；
5. `#[tracing::instrument]` 怎样覆盖 Session、Prompt、Inference、Tool 和 Permission；
6. 为什么 `SessionStarted`、`SessionHarness` 和 `SessionNew` 不是同一件事；
7. `log_event`、`log_session_event`、`log_event_dual` 分别受什么开关控制；
8. 产品事件怎样异步发送到 Events API 和 Mixpanel；
9. Internal OTLP Span 与 External OTEL Log/Metric 为什么必须严格分开；
10. 内部 Span 怎样通过 allowlist、URL 降维和 Secret Scrub 防止内容泄露；
11. External OTEL 怎样用 Curated Schema、Content Gate 和 Double Opt-in 控制客户侧导出；
12. Unified Log 为什么是跨 Shell、Pager、Desktop 的 JSONL，而不是普通 tracing 文件；
13. `GROK_DEBUG_LOG` 怎样借助 Session Span 将 Firehose 路由到每个 Session；
14. Sampling、Hooks、Memory 和 Instrumentation 专用日志如何启用；
15. `events.jsonl` 和 `ObservabilityBridge` 为什么属于运行语义，而不只是分析埋点；
16. Turn 的完成、取消、失败怎样得到一致的 Outcome 与 Error Category；
17. Trace Artifact 上传与 OTLP Trace Export 有什么根本区别；
18. 进程退出、崩溃和短命令为什么必须显式 Flush；
19. 怎样从一个 `session_id` 反查一轮 Agent 的完整时间线；
20. 新增一条观测信号时，应该选择哪条管道以及怎样避免隐私和基数事故。

源码基线：

```text
ed6d543
```

---

## 1. 先看全景：同一件事会投影到多条管道

```text
Agent Runtime Fact
  │
  ├── tracing Event / Span
  │     ├── Pager tracing pane
  │     ├── Debug firehose
  │     ├── Internal OTLP spans
  │     ├── instrumentation.jsonl / Chrome trace
  │     └── sampling / hooks / memory 专用日志
  │
  ├── Typed TelemetryEvent
  │     ├── Product Events API
  │     ├── Mixpanel
  │     └── Curated External OTEL logs + metrics
  │
  ├── Unified Log
  │     └── ~/.grok/logs/unified.jsonl
  │
  ├── EventTracker
  │     └── session-dir/events.jsonl
  │
  ├── ObservabilityBridge
  │     └── Tool Protocol SessionEvent → connected server
  │
  ├── Trace Artifact
  │     └── turn/session state、messages、permissions、manifest 等对象
  │
  └── Sentry / Crash Handler
        └── panic、exception、native crash 诊断
```

核心结论：

> “可观测性”是一个总称，不是一个统一队列。各管道的消费者、可靠性、数据内容、权限边界和生命周期都不同。

---

## 2. 核心源码地图

### 2.1 Telemetry Engine

```text
crates/codegen/xai-grok-telemetry/src/
  lib.rs
  config.rs
  client.rs
  events.rs
  session_ctx.rs
  session_metrics.rs
  prompt_timing.rs
  instrumentation.rs
  unified_log.rs
  debug_log.rs
  sampling_log.rs
  hooks_log.rs
  memory_log.rs
  sentry.rs
  otel_layer/
  external/
```

### 2.2 Shell 的运行期发射点

```text
crates/codegen/xai-grok-shell/src/
  agent/init.rs
  agent/mvp_agent/session_setup.rs
  session/telemetry.rs
  session/acp_session_impl/spawn.rs
  session/acp_session_impl/turn.rs
  session/acp_session_impl/turn_end.rs
  session/acp_session_impl/tool_calls.rs
  upload/turn.rs
  upload/trace.rs
```

### 2.3 Pager 的 Subscriber 与跨进程日志

```text
crates/codegen/xai-grok-pager/src/
  tracing.rs
  unified_log.rs
  app/event_loop.rs

crates/codegen/xai-grok-pager-bin/src/main.rs
```

### 2.4 运行语义事件

```text
crates/codegen/xai-file-utils/src/events/
  types.rs
  tracker.rs
  log.rs

crates/common/xai-tool-protocol/src/session_event.rs
crates/common/xai-computer-hub-sdk/src/observability.rs
```

---

## 3. 先区分六种最容易混淆的对象

| 对象 | 本质 | 主要用途 |
| --- | --- | --- |
| `tracing::Event` | 某一瞬间的结构化记录 | 调试、日志、Span 内事件 |
| `tracing::Span` | 有开始、结束和父子关系的时间区间 | 延迟、调用树、上下文传播 |
| `TelemetryEvent` | 强类型业务事件 | 产品分析与 Curated External OTEL |
| `EventTracker::Event` | Session 运行语义事件 | 本地时间线、恢复与精确状态分析 |
| `SessionEvent` | 跨 Tool Protocol 的生命周期通知 | 连接到 Server 的统一运行观测 |
| `Unified LogEntry` | 跨组件 JSONL 诊断行 | Shell/Pager/Desktop 关联排障 |

它们可以描述同一个事实，但不是互相替代。

例如 Tool 完成时，代码可以同时：

```text
EventTracker::ToolCompleted
ObservabilityBridge::SessionEvent::ToolCallCompleted
TelemetryEvent::ToolCallCompleted
tracing span/event
Unified Log（只在部分关键路径）
```

每条记录的字段和消费者都不同。

---

## 4. 为什么不能只保留一种 Event

如果只使用一个万能事件类型，会产生几个问题：

1. 产品分析字段和运行恢复字段被迫共享隐私策略；
2. 本地高频事件会污染远端统计；
3. Server Protocol 的兼容性会受 Dashboard Schema 约束；
4. 调试消息的自由文本会进入不应承载内容的指标系统；
5. 某个远端 Sink 故障可能反向阻塞 Agent 主循环。

源码采用的策略是：

> 事实在靠近业务处产生，但按用途投影为多个受控表示。

---

## 5. `tracing` 的基本心智模型

Rust `tracing` 不是简单的 `println!`。

```text
Callsite
  ├── metadata: level / target / file / line
  ├── fields
  └── 当前 Span Context
        ↓
Subscriber Registry
  ├── Layer A
  ├── Layer B
  └── Layer C
```

同一个 `tracing::info!` 可以被多个 Layer 同时观察。

因此：

- 发射点不需要知道最终写到哪里；
- Layer 可以独立过滤 Target；
- Span 父子关系由当前 Context 决定；
- 一个全局 Subscriber 可以扇出到 UI、本地文件和 OTLP。

---

## 6. Event 与 Span 的差别

### Event

```rust
tracing::info!(model_id = %model, "response completed");
```

表达“此刻发生了一件事”。

### Span

```rust
let span = tracing::info_span!("tool.execute", tool_name = %name);
```

表达“接下来一段工作属于某个操作”。进入和退出后可以计算时长，并形成调用树。

### 何时选哪个

- 状态瞬变、告警、离散结果：Event；
- 延迟、嵌套调用、上下文关联：Span；
- 需要 Dashboard 稳定 Schema：Typed `TelemetryEvent`；
- 需要持久运行语义：`EventTracker`。

---

## 7. Pager 如何组装 Subscriber

`xai-grok-pager/src/tracing.rs::init_tracing` 是主入口之一。

它创建：

```text
Registry
  ├── fmt_layer → bounded channel → tracing pane
  ├── instrumentation_layer
  ├── sampling_log_layer
  ├── hooks_log_layer
  └── otel_layer
        ↓
debug_log::install_firehose(registry, "tui")
```

之后再初始化 External OTEL。

这里有一个重要边界：

> External OTEL 不是 Subscriber Layer。它由 Typed Telemetry Event 显式扇出，因此不会自动接收任意 tracing 内容。

---

## 8. Pager Tracing Pane 的数据流

```text
tracing fmt Layer
  ↓ ANSI formatted line
TracingChannelWriter
  ↓ bounded Tokio mpsc
TracingModel
  ↓ ANSI parse + plain text
ListPane
```

`TracingEntry` 同时保存：

- `raw_ansi`：主题切换时重新渲染；
- `styled`：Ratatui 展示；
- `plain`：搜索和过滤；
- `seq`：稳定递增 ID。

这是一条 UI 调试管道，不是业务遥测。

---

## 9. 为什么 Tracing Channel 必须有界

`LOG_CHANNEL_CAPACITY` 是 `16 * 1024`。

Writer 使用 `try_send`：

```text
Channel 有空间 → 入队
Channel 已满   → 丢弃最新日志并增加 DROPPED_LOG_LINES
Channel 已关闭 → 返回 I/O Error
```

设计取舍：

> 日志消费者变慢时，宁可丢日志，也不能让 Agent Runtime 被日志背压或因无界内存而 OOM。

`dropped_log_lines()` 让 UI 或诊断代码能报告丢失数量。

---

## 10. `TracingModel` 的有界历史

`TracingModel` 使用 `Vec`，而不是 `VecDeque`。

原因是 `ListPane` 需要连续的 `&[TracingEntry]`。

淘汰策略：

```text
len <= capacity + hysteresis → 不处理
len >  capacity + hysteresis → 批量删除最旧项，回到 capacity
```

Hysteresis 避免每插入一行就搬移整个 `Vec`。

这是一个典型的 UI 性能模型：允许短时超额，换取摊销后的低成本。

---

## 11. Target 是一条轻量路由键

`tracing` 的 `target` 通常默认是 Rust 模块路径，也可以显式指定：

```rust
tracing::info!(target: "sampling_log", ...);
```

仓库中重要的专用 Target 包括：

| Target | 用途 |
| --- | --- |
| `xai_grok_instrumentation` | 性能计时 |
| `sampling_log` | 模型请求采样诊断 |
| `xai_memory` | Memory 生命周期 |
| `xai_grok_hooks` | Hook 运行日志 |
| `xai_grok_agent::plugins` | Plugin 运行日志 |
| `acp_update` | 紧凑 ACP 更新摘要 |
| `acp_update_payload` | 完整 ACP Payload |

Target 不是安全边界；真正导出前还需要独立脱敏和 allowlist。

---

## 12. 为什么 `TargetFilterLayer` 不直接使用 `with_filter`

`instrumentation.rs` 封装了 `TargetFilterLayer<L, S>`。

源码注释指出：把带 `Filtered` 的 Layer 擦除为 `Box<dyn Layer<S>>` 后，可能丢失 Filter ID 注册所需的类型信息，引发 panic。

因此它在 `Layer::enabled`、`on_new_span` 和 `on_event` 中手动检查：

```text
metadata.target() == expected_target
```

这是一个值得学习的 Rust 设计点：

> 动态分发不只是性能问题，也可能破坏依赖静态类型注册的框架协议。

---

## 13. Disabled Layer 为什么仍返回 `NoOpLayer`

多个 Layer Builder 返回统一的 `Box<dyn Layer<S>>`。

功能关闭时返回 `NoOpLayer`，调用方就不需要为每种组合构造不同的复杂泛型类型。

但 `NoOpLayer` 也带来一个细节：

- 某些 filterless Layer 会让 Callsite 保持 enabled；
- 宏参数可能在最终没有输出时仍被求值；
- 因此昂贵 Payload 必须懒序列化。

Pager 的 `LazyJson` 就是为此存在。

---

## 14. `LazyJson` 防止“关了日志仍做重活”

错误写法：

```rust
tracing::debug!(payload = %serde_json::to_string(&huge_value)?);
```

即便目标 Layer 最终过滤掉事件，宏参数也可能已经求值。

源码把序列化放进 `Display::fmt`：

```text
LazyJson(&value)
  ↓ 只有真正记录字段时调用 fmt
serde_json::to_string
```

这对高频 ACP 流尤其重要，因为单条 Bash `raw_output` 可能很大。

---

## 15. Session Context 是观测关联的根

`TelemetryCtx` 包含：

```rust
pub struct TelemetryCtx {
    pub session_id: String,
    pub prompt_index: Arc<tokio::sync::Mutex<usize>>,
    pub prompt_id: Arc<parking_lot::Mutex<Option<String>>>,
}
```

三个关联维度分别回答：

- 这是哪个 Session；
- 这是第几个 Turn；
- 这是当前 Prompt 的哪个随机关联 ID。

---

## 16. 为什么使用 Tokio Task Local

`TELEMETRY_CTX` 通过 `tokio::task_local!` 定义。

它不是线程本地变量，因为异步任务会在线程间迁移；也不是全局变量，因为进程中可能同时运行多个 Session。

```text
OS Thread Local  × 任务迁移后可能错
Process Global   × 多 Session 相互污染
Tokio Task Local ✓ 随 Future Scope 传播
```

这也是理解现代 Agent Runtime 的关键：Session Context 通常属于异步任务树，而不是某个固定线程。

---

## 17. `with_session_ctx` 同时建立两种上下文

```rust
pub async fn with_session_ctx(ctx, fut) -> Output
```

它做两件事：

1. 用 `TELEMETRY_CTX.scope` 安装 Task-local typed context；
2. 用 `fut.instrument(session_span)` 安装 tracing Session Span。

因此一次包装同时服务：

- Typed Telemetry 的 `session_id` / `turn_number` 注入；
- Tracing 父子树；
- Debug Firehose 的按 Session 文件路由。

---

## 18. Session Actor 在哪里进入 Context

`spawn_session_actor` 构造：

```text
TelemetryCtx::new(
  session.session_info.id,
  session.tool_context.prompt_index
)
```

然后：

```text
spawn_local
  └── with_session_ctx(telemetry_ctx, run_session(...))
```

也就是说，Session Actor 主循环及在其 Scope 中执行的 Turn 逻辑都能读取 Ambient Context。

---

## 19. 为什么 Debug Router 依赖字段而不是 Span 名称

Session Span 名为 `session`，但 Router 真正查找的是字段：

```text
session_id
```

`SESSION_ID_FIELD` 和测试固定了这个约定。

好处是：

- Span 名称未来可以调整；
- 任何携带 `session_id` 的父 Span 都可提供路由信息；
- Router 从当前 Event Scope 由叶到根找最近的 Session ID。

字段名就是跨模块协议，因此源码用测试防止静默漂移。

---

## 20. `begin_prompt_id` 为什么每轮旋转 UUID

Turn 开始处调用：

```rust
begin_prompt_id();
```

它把新的 UUID 写入当前 `TelemetryCtx.prompt_id`。

该 ID：

- 用于 External OTEL Event 关联；
- 不附加到 Metrics，避免无界 Cardinality；
- 在 Prompt 外可以是 `None`；
- 与递增的 `turn_number` 不承担同一种语义。

`turn_number` 适合排序；随机 `prompt.id` 适合跨记录唯一关联。

---

## 21. 快照为什么必须同步发生

`log_event` 最终会异步 `tokio::spawn` 发送网络请求。

如果发送任务稍后才读取 `prompt_index`，Session 可能已经进入下一轮。

因此 `emit_event_with_origin` 先同步快照：

```text
调用时读取 session_id + turn_number
  ↓
move 进异步发送任务
  ↓
网络发送
```

这避免了“上一轮事件被标成下一轮”的竞态。

---

## 22. 锁竞争时为何宁可缺少 Turn Number

`external_ctx_snapshot` 使用 `try_lock` 读取 `prompt_index`。

锁竞争时：

```text
turn_number = None
```

而不是等待。

观测代码的基本原则是：

> 可以降级字段完整性，不能在 Agent 热路径中引入不可控阻塞。

Session ID 和 Prompt ID 仍能保留部分关联能力。

---

## 23. Turn 的核心 Span

`process_conversation_turn` 使用 `#[tracing::instrument]`，Span 名为：

```text
session.process_conversation_turn
```

预声明的字段包括：

```text
session_id
model_id
turn_tool_count
turn_model_calls
input_tokens / output_tokens / cache_read_tokens
stop_reason
response.has_tool_call
request_id
ttft_ms
mcp_server.name / mcp_tool.name
agent.name / skill.name
query_source
effort
attempt
parent_agent_id
```

部分字段初始为 `Empty`，在运行中逐步 `record`。

---

## 24. 为什么 Span 字段要提前声明

Tracing Span 的字段集合由 Callsite Metadata 固定。

不能等请求完成后临时添加新字段，所以源码先声明：

```rust
input_tokens = tracing::field::Empty
```

等响应到达再：

```text
Span::current().record("input_tokens", value)
```

这种“先声明、后填充”非常适合长流程。

---

## 25. 一次 Turn 的观测时间线

```text
handle_prompt span
  │
  ├── EventTracker::begin_turn
  ├── TurnStarted → events.jsonl
  ├── SessionEvent::TurnStarted → ObservabilityBridge
  ├── SessionMetrics::Turn
  ├── begin_prompt_id
  ├── PromptSubmitted
  │
  └── process_conversation_turn span
        ├── prepare tools / MCP wait
        ├── Unified Log: tool_prep_done
        ├── model request span(s)
        ├── ModelResponseReceived
        ├── Unified Log: inference_done
        ├── tool spans / permission spans
        ├── ToolCallCompleted
        └── loop until terminal outcome
              ├── TurnCompleted
              ├── ApiError（失败时）
              ├── TurnCompletedLifecycle
              ├── TurnEnded → local EventTracker
              └── SessionEvent::TurnEnded → server
```

注意：Agentic Turn 可以包含多个模型请求，因此 `ModelResponseReceived` 与 `TurnCompleted` 不是一一对应。

---

## 26. Session 启动为什么有多个事件

### `SessionStarted`

最小生命周期信号：

```text
session_id
```

在 `Enabled` 和 `SessionMetrics` 模式都可以发送。

### `SessionNew`

描述新 Session 的用户侧配置：Client、Git Repo、Permission Mode。

### `SessionHarness`

描述 Agent Harness 的详细能力库存：Model、Agent、MCP、Plugin、Skill、LSP、Hook、AGENTS.md、Memory 等。

### `SessionLoad`

描述从磁盘恢复的 Session，包括 Turn、Tool、Compaction 数量等。

因此不能仅凭事件名相似就做等价 Join。

---

## 27. Harness Inventory 为什么异步构造

`SessionHarnessMetrics::into_event` 会：

- 遍历启用 Plugin；
- 发射 `plugin.loaded` Span；
- 遍历 Hook 并发射 `hook.registered` Span；
- 读取 AGENTS.md 路径；
- 收集 Git Context。

Session Spawn 不应被这些辅助收集阻塞，所以它在后台任务中构造事件。

但事件自己携带 `session_id`，因为该任务可能不在 Session Task-local Scope 内。

---

## 28. `TelemetryEvent` 如何绑定稳定事件名

Trait：

```rust
pub trait TelemetryEvent: Serialize + Send + 'static {
    const NAME: &'static str;
    fn external_record(&self) -> Option<ExternalRecord> { None }
}
```

宏：

```text
telemetry_event!(Type, "stable_name")
telemetry_event!(Type, "stable_name", external = mapper)
```

这带来两层显式审查：

1. 类型与产品事件名绑定；
2. 只有声明了 `external = mapper` 的事件才进入 External OTEL。

新增普通产品事件不会自动暴露给客户 Collector。

---

## 29. 为什么事件字段优先使用 Enum

`PermissionOutcome`、`Outcome`、`McpTransport`、`HookOutcome` 等使用 Rust Enum，并通过 Serde 输出 `snake_case`。

相较自由字符串：

- Compiler 能检查 Match 是否完整；
- Dashboard 枚举值更稳定；
- 拼写错误不会制造新 Bucket；
- External Mapper 可以穷尽映射。

当字段值是分析维度时，Enum 通常比 String 更安全。

---

## 30. 三个产品事件入口的区别

### `log_event`

```text
先尝试 External OTEL
再检查 TelemetryMode == Enabled
最后发送内部产品事件
```

### `log_session_event`

```text
先尝试 External OTEL
再检查 Enabled 或 SessionMetrics
最后发送 Shell-origin 生命周期事件
```

### `log_event_dual(internal_enabled, data)`

```text
internal_enabled = true  → log_event，两个 Sink 各走自己的 Gate
internal_enabled = false → 仅尝试 External OTEL
```

它用于内部 Sink 有更严格条件，例如 ZDR，而客户自己的 External OTEL 由另一套显式策略控制。

---

## 31. Telemetry Mode 的三级语义

```text
Disabled
  └── 不发送产品事件与 Session Metrics

SessionMetrics
  └── 只发送元数据型生命周期事件

Enabled
  └── 发送完整产品事件
```

`SessionMetrics` 不是 Enabled 的布尔别名。

代码提供：

```text
is_enabled()
is_session_metrics_enabled()
```

不同调用点必须选择正确的 Gate。

---

## 32. External OTEL 为什么独立于 Telemetry Mode

External OTEL 的目的地是客户自己的 Collector，而不是 xAI 产品分析。

它具有自己的：

- `GROK_EXTERNAL_OTEL` 主开关；
- Exporter 选择；
- Endpoint 与 Header；
- Content Gate；
- Remote Restrictive Policy；
- Redaction Validator。

所以 `log_event` 在检查内部 Mode 之前先尝试 External Mapper。

这叫：

> One callsite, two sinks, independent gates。

---

## 33. 产品事件如何异步发送

`emit_event_with_origin`：

1. 拼接事件名前缀；
2. 同步快照 Session Context；
3. 检查当前是否有 Tokio Runtime；
4. 增加 `PENDING_EVENTS`；
5. `tokio::spawn` 发送；
6. Guard 在所有退出路径减少计数。

主逻辑不会等待网络。

代价是：进程紧接着退出时，事件可能尚未发送完成。

---

## 34. `EmitterOrigin` 防止不同产品面混淆

目前包括：

```text
Shell     → grok-shell-
Workspace → grok-workspace-
```

Wire Event Name 带前缀，例如：

```text
grok-shell-turn
```

`event_value` 再剥离已知前缀得到稳定后缀。

`EmitterOrigin::ALL` 与 `EnumCount` 的编译期断言保证新增 Variant 时不会漏掉前缀处理。

---

## 35. Product Events 与 Mixpanel 是两个 Sink

`TelemetryClient::track` 会根据配置分别尝试：

### Product Events API

构造 `viewer_context`、用户属性、设备属性、Event Metadata 后 HTTP POST。

### Mixpanel

添加：

```text
distinct_id
time
$insert_id
app_name
country / language / locale
```

两者失败都不会让 Agent Turn 失败。

---

## 36. 为什么 `$insert_id` 使用裸 UUID

源码明确避免把事件名拼进 `$insert_id`。

原因是分析平台对长度和字符集有限制；长事件名被截断后，可能导致同一用户同一秒的事件错误去重。

裸 UUID：

- 长度固定；
- 字符合法；
- 每次发射唯一；
- 与事件名解耦。

这是观测 Schema 中一个很实际的去重陷阱。

---

## 37. Telemetry Client 的全局可替换状态

客户端存放于：

```text
OnceLock<Mutex<Option<TelemetryClient>>>
```

`init` 可重复调用并替换当前值：

- Disabled 写入 `None`；
- 其他 Mode 写入 Client；
- 然后尝试 Profile Sync。

`init_if_needed` 只在当前没有 Client 时补初始化，适用于启动时尚未认证、认证完成后再接入的场景。

---

## 38. 用户和部署标识怎样进入事件

`track` 自动补充：

```text
agent_id
team_id
deployment_id
shell_version
client_type / client_version
subscription_tier
```

`deployment_id` 由 Deployment Key 通过 UUIDv5 派生，不直接发送 Key。

`agent_id` 是持久设备级 ID，缓存在 `$GROK_HOME/agent_id`，Unix 上使用 `0600` 权限。

---

## 39. 稳定 Agent ID 与 Session ID 的差别

| ID | 生命周期 | 用途 |
| --- | --- | --- |
| `agent_id` | 跨进程、跨 Session | 设备/安装关联 |
| `agent_instance_id` | 当前进程 | WebSocket 重连关联 |
| `session_id` | 一次 Session | 会话关联 |
| `turn_number` | Session 内递增 | 顺序定位 |
| `prompt.id` | 每轮随机 | External Event 精确关联 |
| `request_id` | 单次请求 | Sampler/HTTP 关联 |
| `tool_call_id` | 单次 Tool Call | Tool 生命周期关联 |

排障时首先要问清楚拿到的是哪一种 ID。

---

## 40. `drain_pending` 解决短命令丢事件

普通 Agent 进程运行够久，后台发送通常自然完成。

但 `grok login` 一类命令可能发完事件立刻退出。

`drain_pending(timeout)`：

```text
循环观察 PENDING_EVENTS
  ├── 归零 → 返回
  └── 超过 deadline → Debug Log 后返回
```

它是有界等待，不会无限阻塞退出。

---

## 41. Internal OTLP 的定位

`otel_layer` 把 `tracing` Span 转换为 OpenTelemetry Trace，并发往内部 Observability Backend。

它的输入是广泛的 tracing Span：

```text
tracing Span Tree
  ↓ OpenTelemetryLayer
  ↓ BatchSpanProcessor
  ↓ Redaction
  ↓ RefreshableSpanExporter
  ↓ internal OTLP traces endpoint
```

它不是 Product Event，也不是 External OTEL。

---

## 42. Internal OTLP 的 Resource Attributes

初始化时写入：

```text
service.name = grok-cli
service.version
client.name
client.version
app.entrypoint
terminal.type（若存在）
```

导出时再根据最新 Credential Snapshot 注入：

```text
deployment.id
api_key.id
organization.id
team.id
user.id
```

身份字段延迟到 Export 时解析，支持运行中登录或切换认证。

---

## 43. 为什么 Exporter 每批读取最新 Credential

`RefreshableSpanExporter` 持有 `AuthCredentialProvider`。

每次 `export`：

1. 检查 Session Metrics Mode 与可用凭证；
2. 读取最新 Snapshot；
3. 构造 Authorization 和租户 Header；
4. 创建 One-shot Exporter；
5. 发送 Batch；
6. 失败时尝试刷新 Token 并重试一次。

这避免了启动时缓存的 Token 在长会话中失效。

---

## 44. Internal OTLP 导出 Gate

即使 Span 已创建，导出仍要求：

```text
Telemetry Client 至少处于 SessionMetrics
AND Credential Provider 有可用凭证
AND Exporter enabled
AND InstrumentationMode == Server
```

没有凭证时构造 Provider 仍可成功，之后认证完成的 Batch 可以恢复导出。

---

## 45. Internal OTLP 的隐私原则：字符串默认拒绝

`otel_layer/redact.rs` 的核心规则：

```text
数值 / 布尔字段 → 默认可保留
字符串字段       → Key 必须在 ALLOWED_STRING_KEYS
未知未来 Value   → 当作内容，Fail Closed
```

这与“只屏蔽几个敏感字段”的黑名单思路相反。

> 自由文本是默认危险的；只有经过审查的字段名才允许出站。

---

## 46. Allowlist 后仍然必须 Scrub

字段名获准不代表值一定安全。

例如 `path`、`source`、`endpoint` 虽在 Allowlist 中，值仍经过：

1. Secret Shape Redaction；
2. User/Home Path Redaction；
3. URL Origin Reduction（特定 URL 字段）。

Allowlist 控制“字段能否存在”，Scrubber 控制“值可以长什么样”。

---

## 47. 为什么 Event Message 被替换成 Callsite

OpenTelemetry Span 中的 tracing Event Name 可能就是格式化后的自由消息。

例如：

```text
received prompt: <user content>
```

它绕过 Key Allowlist，因此 Redactor 将 Event Name 替换成：

```text
code.filepath:code.lineno
```

没有位置信息则写成固定的 `event`。

这保留了“哪里发生”的调试价值，同时删除自由消息。

---

## 48. URL 为什么只保留 Origin

URL 的 Path、Query 和 Fragment 可能包含：

- 搜索词；
- 文件路径；
- 临时签名；
- Token；
- 用户 ID。

因此 `url`、`endpoint`、`gcs_url`、`bucket_url` 等字段被降为：

```text
scheme://host[:port]
```

而 `gcs_path`、`object_path` 等明确需要的 Storage Path 不做 Origin Reduction，但仍做 Secret 和 Home Path Scrub。

---

## 49. Internal Span 的 Error Status 为什么仍保留描述

错误描述有较高诊断价值，所以不会完全删除。

处理方式是：

```text
保留 Error Description
  + Secret Scrub
  + User Path Scrub
```

这是风险与诊断价值之间的显式折中，不适用于任意普通消息。

---

## 50. External OTEL 是完全不同的 Schema

External OTEL 不订阅任意 Span。

它只接受：

```text
TelemetryEvent
  └── external_record()
        └── curated mapper
              ├── ExternalEventName
              ├── ExternalKey + AttrValue
              ├── GatedAttr
              └── MetricIncrement
```

只有大约一组明确审查的事件和六类 Counter 会进入客户 Collector。

---

## 51. External OTEL 的 Double Opt-in

激活要求：

```text
GROK_EXTERNAL_OTEL / file master switch = enabled
AND
至少一个 logs 或 metrics exporter 不是 none
```

只设置 Endpoint 不会启用；只打开 Master Switch 但没有 Exporter 也不会启用。

默认路径不构造 Provider、线程或 Socket。

---

## 52. External OTEL 的 Transport 与 Endpoint

支持：

```text
http/protobuf
grpc
```

HTTP 默认 Base：

```text
http://localhost:4318
```

并为不同 Signal 补：

```text
/v1/logs
/v1/metrics
```

gRPC 默认使用 `http://localhost:4317`，不追加 HTTP Path。

Signal-specific Endpoint 优先于 Base Endpoint。

---

## 53. 为什么 External Header 只能来自客户环境变量

External Exporter 只使用客户提供的 `OTEL_EXPORTER_OTLP_HEADERS` 及 Signal-specific Header。

模块不依赖 `AuthCredentialProvider`，因此不存在附带 xAI 内部 Bearer Token 的代码路径。

这是一条结构性安全边界：

> 客户 Collector 管道不应该拥有内部认证能力。

---

## 54. Internal 与 External 的 No-double-send 不变量

历史兼容中，Internal Pipeline 可能读取通用 `OTEL_EXPORTER_OTLP_*`。

若它已经消费这些变量，External 初始化会拒绝激活，并提示迁移到内部专用变量。

目的：

- 防止同一配置同时指向两个 Pipeline；
- 防止一条 Span 被误发往客户 Collector；
- 防止内部 Header 与外部 Header 语义混淆。

这不是文档约定，而是代码中的强制检查。

---

## 55. External Content Gates

两个高风险字段域默认单独控制：

```text
UserPrompts
ToolDetails
```

对应的受控字段包括：

- `prompt`；
- `tool_parameters`；
- `file_path`；
- `skill.name`；
- `plugin_name` / `plugin_version`。

Gate 关闭时字段不存在，而不是写入空字符串。

---

## 56. External Mapper 为什么先做语义降维

例如 Tool Name：

- 默认可映射成安全分类；
- Tool Details Gate 开启后才允许更具体值；
- Parameter 只在 Gate 开启时加入；
- 最终仍执行 Secret Scrub 与截断。

它不是把内部事件原样 Serialize，而是重建一份最小客户 Schema。

---

## 57. External 字符串的二次防线

`emit_record` 对所有字符串执行：

1. Secret / Path Scrub；
2. 普通 Value 截断；
3. Prompt 使用独立的 60 KB 内容上限；
4. Exporter Validator 再次检查 Key、Gate 和长度。

Mapper Discipline 不是唯一保证；出口 Validator 才是最后 Chokepoint。

---

## 58. External Event 自动注入的关联字段

如果 Mapping 没显式携带 Session ID，Ambient Context 会补充：

```text
session.id
turn_number
prompt.id
event.sequence
```

此外可补：

```text
user.id
organization.id
team.id
deployment.id
```

`event.sequence` 是进程内单调计数，可帮助发现丢失或乱序。

---

## 59. 为什么 Metrics 不携带 Prompt ID

Metrics Backend 会按 Attribute 组合生成 Time Series。

如果每个 Prompt UUID 都成为 Label：

```text
每次请求 → 新 Time Series
```

这会造成 Cardinality Explosion。

所以代码明确规定：

- Event 可以带 `prompt.id`；
- Metric 永远不带 `prompt.id`；
- `session.id` 和 Version 也提供可配置的 Cardinality 开关。

---

## 60. External Metrics 的固定集合

`Instruments` 预创建六类 Counter：

```text
grok_code.session.count
grok_code.token.usage
grok_code.turn.count
grok_code.tool.decision
grok_code.tool.usage
grok_code.error.count
```

它们由 Typed Event Mapping 派生，不由任意字符串指标名动态创建。

固定集合便于控制：

- Schema；
- Unit；
- Label 集；
- Cardinality；
- Dashboard 兼容性。

---

## 61. External Remote Policy 只能收紧

运行后 Remote Settings 可以：

- `force_disable`；
- `lock_content_gates`。

不能：

- 从关闭状态启用 External OTEL；
- 打开本地未开启的内容 Gate；
- 放宽已经收紧的策略。

这是 Monotonic Policy：状态只能向更严格方向移动。

---

## 62. Settings Gate 为什么 Fail Closed 但有时间上限

Leader 在等待 Remote Policy 时可以关闭 External Emit Gate。

```text
启动 / 账户切换
  ↓ close gate
等待 Remote Settings
  ├── 收到 → 应用限制后 open
  └── 超过 bounded window → warning 后按本地配置 open
```

默认上限 30 秒。

这样既避免策略尚未到达时过早发送，也避免 Remote 服务永久不可用导致客户明确配置的 Observability 永久沉默。

---

## 63. Unified Log 的定位

`unified_log.rs` 是跨组件 Session 诊断日志：

```text
~/.grok/logs/unified.jsonl
```

每行 `LogEntry` 包含：

```text
ts
src
pid
ver
lvl
sid
msg
ctx
```

它强调跨进程重建时间线，而不是 Span Tree。

---

## 64. 为什么 Unified Log 必须有 `pid` 和 `ver`

多个 Shell、Pager、Desktop 进程可能同时向同一文件写入。

没有 PID 时，交错行无法区分生产者。

Version 则用于识别：

- 旧客户端；
- Zombie Process；
- 升级前后的行为差异；
- Wire Compatibility 问题。

旧版本记录允许 `pid` / `ver` 为 `None`，当前代码总是填入。

---

## 65. Pager 怎样把日志交给 Shell

Pager 不直接共享 Shell 的内存 Writer，而是：

```text
Pager push ClientLogEntry
  ↓ in-memory buffer
达到 16 条或每秒 flush
  ↓ ACP x.ai/log notification
Shell ingest_client_entries
  ↓ stamp source + batch serialize
unified.jsonl
```

进程退出前使用 `flush_blocking` 等待 ACP 发送。

---

## 66. 为什么 Shell 拒绝 Client 声称自己是 Shell

`ingest_client_entries` 接受 Pager 或 Desktop 来源，但拒绝：

```text
LogSource::Shell
```

否则 Client 可以伪造 Shell 日志来源，破坏排障证据的 Provenance。

Source 由接收方认可，而不是完全信任发送方。

---

## 67. Unified Log 如何限制文件增长

上限：

```text
5 MB
```

超过后保留大约后半部分完整行。

重要实现细节：

- 原地 Rewind + Rewrite + Truncate；
- 不使用 Temp + Rename；
- 跨进程用 Advisory Lock 防止多个 Trimmer 交错；
- 维护周期检查 Path 是否仍指向原 Inode。

---

## 68. 为什么不能通过 Rename 轮转 Unified Log

多个进程都持有 `O_APPEND` 文件描述符。

若一个进程 Rename 新文件覆盖旧路径：

```text
其他进程的 FD → 仍指向已 unlink 的旧 inode
```

之后日志继续写入无人能找到、无人会 Trim 的文件。

源码选择原地重写，牺牲严格 Crash Atomicity，换取多进程 FD 一致性。

---

## 69. Writer 如何自愈被替换的文件

每隔约两秒，`LogWriter::maintain` 比较：

```text
当前 Path Identity
vs
打开时捕获的 dev + inode
```

若不同：

- 尝试重新打开 Live Path；
- 失败则进入 `detached`；
- Detached 状态丢弃条目而不是写进不可见旧文件；
- 后续维护周期继续重试。

这是日志系统对外部删除、轮转和磁盘故障的自愈机制。

---

## 70. Unified Log Snapshot 用于什么

API 提供：

```text
snapshot_log()
snapshot_session_log(session_id)
```

后者解析 JSONL，只保留匹配 `sid` 的行。

源码注明 Session Unified Log 只在 401/404 认证失败诊断时上传，不是每轮自动上传。

这限制了诊断数据的常态远端暴露。

---

## 71. Debug Firehose 的两种模式

### Per-session

```text
GROK_DEBUG_LOG=1
→ ~/.grok/debug/<session_id>.txt
→ latest.txt 指向最近打开的 Session 文件
→ 无 Session Context 的事件写 <role>-<pid>.txt
```

### Single-file

```text
GROK_LOG_FILE=/path
或 GROK_DEBUG_LOG=/path
→ 所有记录写一个文件
```

`GROK_LOG_FILE` 优先于 `GROK_DEBUG_LOG`。

---

## 72. Per-session Router 怎样工作

1. `on_new_span` 检查 `session_id` 字段；
2. Sanitizer 把它变成安全文件名；
3. 存进 Span Extensions；
4. `on_event` 从当前 Scope 由近到远查找 Session ID；
5. 写入对应 Non-blocking Sink。

找不到 Session ID 时写 Fallback 文件。

这就是 `with_session_ctx` 创建 Session Span 的第二个作用。

---

## 73. Session ID 为什么还要文件名清洗

正常 Session ID 通常是 UUID，但日志系统不能依赖上游永远正确。

清洗规则只保留：

```text
A-Z a-z 0-9 - _ .
```

其他字符替换为 `_`；空值和纯点字符串映射为安全常量。

防止：

- `/` 路径穿越；
- `..` 特殊目录；
- 意外创建嵌套文件；
- 非法平台文件名。

---

## 74. `latest.txt` 为什么使用原子 Symlink Swap

Unix 下流程：

```text
创建唯一临时 symlink
  ↓
rename 覆盖 latest.txt
```

这样 `tail -f latest.txt` 不会观察到中间“链接不存在”的窗口。

崩溃遗留的临时 Link 会被后续清理逻辑按年龄回收。

---

## 75. Firehose 为什么不回收进程内 Session Writer

每个被观察到的 Session 会保留：

- 一个文件描述符；
- 一个 Non-blocking Worker；
- 一个 Guard。

直到进程结束。

这在超长 Leader 进程中会增长，但它是显式 Opt-in 的 Debug Firehose。保留 Guard 可以确保退出 Flush，不会因为过早 Drop Worker 丢日志。

磁盘文件则有七天 Retention 清理。

---

## 76. Sampling Log

启用：

```text
--log-sampling
或 GROK_LOG_SAMPLING=1
```

输出：

```text
~/.grok/logs/sampling.jsonl
```

Sampler 创建 `sampling_request` Span，包含：

```text
request_id
model
api_backend
base_url
auth_type
auth_prefix
reasoning_effort
output_tokens
reasoning_tokens
```

它用于模型请求级诊断，不应被普通 UI Filter 自动打开。

---

## 77. Sampling Auth Prefix 的风险意识

源码记录的是 `auth_prefix`，而不是完整认证凭证。

即便如此，Sampling Log 仍是显式开启的诊断文件，不属于最小元数据管道。

学习建议：对任何“截断后的秘密”都不要自动认为完全无敏感性；它仍可能用于关联或识别凭证类型。

---

## 78. Hooks / Plugins 专用日志

启用：

```text
GROK_HOOKS_LOG=1
GROK_HOOKS_LOG=/custom/path
```

默认输出：

```text
~/.grok/logs/hooks.log
```

Filter：

```text
xai_grok_hooks=debug
xai_grok_agent::plugins=debug
```

时间格式是进程启动后的相对 Uptime，适合观察 Discovery、Dispatch、Execution 和错误的相对耗时。

---

## 79. Memory 专用日志

Memory Log Target 为：

```text
xai_memory
```

Layer 只在 `memory-log` Feature 编译时存在。

这意味着：

- Callsite 可以始终写 `target: xai_memory`；
- 没有 Feature/Layer 时近似无输出成本；
- Debug Build 可选择将其写入 `memory.log`；
- 产品 Memory Telemetry 仍是另一条 Typed Event 管道。

不要把 `memory.log` 与 `MemorySearch` 产品事件混为一谈。

---

## 80. Instrumentation 的四种模式

`GROK_INSTRUMENTATION` 解析为：

```text
Disabled
Log
Chrome
Server
```

### Log

只写 `xai_grok_instrumentation` Target 的 JSONL。

### Chrome

生成 Chrome Trace 格式，可在 Trace Viewer 中查看时间轴。

### Server

Instrumentation Layer 自己 No-op，由 Internal OTEL Layer 负责远端 Span。

### Disabled

返回 No-op Layer。

未设置时默认 Server。

---

## 81. RAII Timer 如何记录性能

`InstrumentationTimer` 在创建时记录 `Instant`，Drop 时发射：

```text
event = timing
name
elapsed_us
fields
```

使用方式：

```text
let timer = instrumentation_timer!("mcp_merge_managed");
// scope ends
// Drop 自动记录
```

RAII 保证正常 Early Return 时也会记录时长。

Panic Unwind 是否记录取决于进程和 Unwind 行为，但 Drop 模型比手写结束调用更不易遗漏。

---

## 82. Chrome 模式为什么持有 Entered Span

普通 Log 模式只需在 Drop 时输出一次 Duration。

Chrome Trace 需要真实 Begin/End Span，因此 Timer 持有 `EnteredSpan` Guard。

Drop 时释放 Guard，Tracing Chrome Layer 得到完整区间。

同一个高层 Timer API 因 Mode 不同映射成两种底层表示。

---

## 83. Panic Hook 的两条输出

Instrumentation Panic Hook：

1. 创建 `internal_error` Span，写 `error_type=panic` 与 Source Location；
2. 给 External OTEL 发送只含错误类别的 `InternalError`；
3. 写本地 `tracing::error!`，包含更丰富 Panic Message；
4. 调回默认 Panic Hook。

External Stream 明确不包含 Panic Message 和 Location，内部管道保留更多诊断信息。

---

## 84. Sentry 与 Instrumentation Panic Hook 的关系

Sentry 在进程早期初始化，返回的 Guard 必须活到进程结束。

Instrumentation Panic Hook 是结构化 tracing 路径。

两者可以同时观察崩溃，但职责不同：

- Sentry：错误聚合、Exception、Stacktrace；
- tracing：Span Context 和本地/OTLP 观测；
- Native Crash Handler：Signal 级崩溃文件和终端恢复。

多层 Crash Observability 是为了覆盖 Rust Panic、异常退出和 Native Signal 的不同失败模型。

---

## 85. Sentry 的独立开关

Sentry 使用同步启动期解析：

```text
Requirements
> Managed Settings force-off
> DISABLE_ERROR_REPORTING
> GROK_ERROR_REPORTING
> merged config
> inherit telemetry
```

它默认继承 Telemetry 是否启用，但可以被单独关闭。

原因是 Sentry 初始化发生在 Tokio Runtime 和完整 Config Runtime 之前。

---

## 86. Sentry 出站前怎样清洗

`before_send`：

- Secret Redaction；
- Home Prefix 替换；
- Username Path Segment 替换；
- 清洗 Message、Exception、Stack Frame、Breadcrumb、Extra 和 Tag；
- 删除 `cwd` Extra；
- 清空 Server Name；
- 丢弃 Broken Pipe 和 Disk Full Panic。

Broken Pipe / No Space 被视为用户环境噪声，避免污染错误聚合。

---

## 87. 为什么 Username 只匹配完整路径 Segment

若用户名是 `alice`，直接字符串替换会破坏：

```text
aliceapp.log
```

源码只替换 `/` 或 `\` 分隔的完整 Segment。

这是脱敏中的常见原则：

> Redaction 要足够强，也必须避免大面积误伤正常诊断文本。

---

## 88. EventTracker 是运行语义日志

每个 Session 目录有：

```text
events.jsonl
```

`EventTracker` 维护：

- 当前 Turn 是否已发出终态；
- 当前 Active Tool；
- 本轮 Tool Count；
- 上一轮 Interrupt Category；
- Redirect Kind；
- Pending Interrupt Reminder。

这些状态会影响 Agent 后续行为，因此它不只是 Analytics。

---

## 89. `turn_ended_emitted` 的 Exactly-once Guard

取消、错误、正常结束可能从不同控制路径收敛。

`emit_turn_ended` 使用：

```text
if turn_ended_emitted.replace(true) { return; }
```

确保本地运行语义时间线只有一个 Turn Terminal。

这与产品事件“可能 Fire-and-forget 丢失”形成鲜明对比：运行语义更强调本地一致性。

---

## 90. Active Tool 为什么由 Tracker 维护

取消发生时，Tool 执行 Future 可能没有机会走正常完成路径。

Tracker 保存：

```text
tool_name
tool_call_id
dispatch_duration_ms
```

取消时可以补写 `ToolCompleted(cancelled)`，然后清除 Active Tool。

否则 `events.jsonl` 会出现永不闭合的 ToolStarted。

---

## 91. `begin_turn` 为什么不能清除所有状态

它只重置：

- `turn_ended_emitted`；
- `turn_tool_count`。

上一轮 Interrupt / Redirect / Reminder 必须跨 Turn 保留，直到下一次真实用户 Prompt 消费。

这说明一条“观测字段”也可能是下一轮 Prompt 语义的输入，不能草率当成可删除埋点。

---

## 92. `events.jsonl` 的写入模型

`EventWriter`：

- 打开 Session Directory 下的文件；
- 每条 Event 加 RFC3339 毫秒时间；
- `#[serde(flatten)]` 合并事件字段；
- 追加一行 JSON；
- Clone 共享 `Arc<Mutex<Option<File>>>`；
- 第一次写失败告警，之后抑制重复错误。

它是同步本地 Append，不经过网络。

---

## 93. ObservabilityBridge 的定位

`ObservabilityBridge` 把 `SessionEvent` 发送到连接的 Server：

```text
SessionEvent
  ↓ ToolHarness::emit_session_event
ToolNotificationFrame Custom(kind=session_event)
  ↓ connected server
```

没有 Harness 时 No-op；Server 失败被忽略，不能影响 Sampler 主循环。

调用方仍必须单独写本地 `EventTracker`。

---

## 94. 为什么 Bridge 不替代 Local Sink

Shell 本地使用 `EventTracker`；另一种 Sampler 可能使用 `EventProcPublisher`。

若 Bridge 强行抽象所有本地 Sink：

- Trait 表面会膨胀；
- 不同 Runtime 的语义被迫求交集；
- 发送 Server 与本地一致性耦合。

所以源码明确要求调用点双写：

```text
Local Sink
AND
ObservabilityBridge
```

---

## 95. `SessionEvent::Unknown` 的兼容性作用

`SessionEvent` 使用内部 Tag：

```text
event_type
```

旧消费者遇到新 Variant 时反序列化为 `Unknown`，并应静默忽略。

这让 Protocol 可以向前扩展，而不会因为新版 Event Type 让旧端整个消息解析失败。

---

## 96. Model Response 与 Turn Completion 的关系

每次模型响应完成会发射 `ModelResponseReceived`，包括：

```text
model_id
duration_ms
stop_reason
prompt_tokens
completion_tokens
reasoning_tokens
cached_prompt_tokens
```

但一个 Turn 可能：

```text
Model Response → Tool Call → Model Response → Tool Call → Model Response
```

最终只发一个 `TurnCompleted`。

因此统计模型调用次数不能直接统计 Turn 数。

---

## 97. Prompt Timing 怎样拆分延迟

`PromptTiming` 记录：

```text
total_ms
mcp_wait_ms
tool_collection_ms
model_call_ms
pre_model_ms
```

其中：

```text
tool_collection_ms = total_tool_prep_ms - mcp_wait_ms
pre_model_ms = total_ms - model_call_ms
```

并附加 MCP Server 数、注册 Tool 数、初始化策略和 Model ID。

这使“模型慢”和“模型调用前准备慢”可以分开分析。

---

## 98. Unified Log 中的 Turn Milestone

源码在关键点写语义稳定的 Message：

```text
shell.handle_prompt.start
shell.turn.tool_prep_done
shell.turn.inference_done
```

Context 含耗时、Token、TTFT、ITL、Attempt 等。

它们的优势是：

- 人能直接 grep；
- 跨进程统一；
- 不依赖远端 Export 成功；
- 可以与 PID、Version、Session ID 一起定位。

---

## 99. Tool 完成时的四次投影

`tool_calls.rs` 完成 Tool 后：

1. `signals_handle.record_tool_duration`；
2. `EventTracker::ToolCompleted`；
3. `ObservabilityBridge::SessionEvent::ToolCallCompleted`；
4. `TelemetryEvent::ToolCallCompleted`。

External OTEL Active 时还会解析 Tool 参数，提取常见文件路径字段，并把完整参数交给受 Content Gate 控制的 Mapper。

只有 External 需要时才做额外 JSON Parse，避免常态热路径成本。

---

## 100. Permission 的 Span 与 Typed Event

Permission 流程记录：

- Access Kind；
- Permission Mode；
- Decision；
- Decision Source；
- 等待时间；
- Tool Name；
- Outcome。

`permission_decision_source` 不会臆测缺失的 Provenance：

- Policy Deny → `config`；
- Reject → `user_reject`；
- Cancel → `user_abort`；
- Followup → `user_followup`；
- 无法区分配置 Allow 与用户 Allow → 中性 `allowed`。

观测代码宁可保守分类，也不制造虚假精度。

---

## 101. Error Category 的收敛

Turn Error 先映射为 `StopFailureKind`：

```text
RateLimit
AuthenticationFailed
InvalidRequest
ServerError
MaxOutputTokens
Unknown
```

Telemetry 再映射为：

```text
rate_limit
auth
invalid_request
internal
max_tokens
unknown
```

Hook 与 Telemetry 复用同一个分类入口，避免同一错误在两个系统中得到不同标签。

---

## 102. Error 分类优先级

`stop_failure_error_type` 大致按：

1. 结构化 MaxTokens Marker；
2. Error Data 中的 HTTP Status；
3. JSON-RPC Error Code；
4. Unknown。

HTTP Status 比外层 JSON-RPC Code 更具体。

例如：

```text
401 → AuthenticationFailed
429 / 503 / 529 → RateLimit
其他 4xx → InvalidRequest
5xx → ServerError
```

---

## 103. Error Detail 与 Error Category 不同

Category 用于稳定聚合；Detail 用于人类诊断。

`turn_error_detail` 优先从 Error Data 提取细节，回退到 Message。

`format_turn_error_message` 则生成 Stop Hook 或 UI 可读信息。

不要把自由 Detail 当成 Metric Label，否则会产生隐私风险和高 Cardinality。

---

## 104. Turn 的五类终态分支

源码分别处理：

```text
Completed
StationarityEnded
Cancelled
MaxTurnsReached
Error
```

每个分支都会组合：

- Local TurnEnded；
- AfterTurn Hook；
- Typed `TurnCompleted`；
- 必要时 `ApiError`；
- 最终 `TurnCompletedLifecycle`。

Stationarity 在业务上完成，但带 `action_stationarity` Category；Max Turns 在观测上是 Cancelled。

---

## 105. `TurnCompletedLifecycle` 为什么独立存在

`TurnCompleted` 是完整产品事件，仅 `Enabled` 模式内部发送。

`TurnCompletedLifecycle` 是最小生命周期事件，在 `SessionMetrics` 模式也可发送。

拆开后企业可以只保留：

```text
Session / Turn 数量和边界
```

而不启用更完整的产品行为分析。

---

## 106. Trace Artifact 不是 OTLP Trace

两者名字都叫 Trace，但数据模型完全不同。

### OTLP Trace

```text
Span Tree + Attributes + Timings
```

### Prompt Trace Artifact

```text
Turn Messages
Streaming Partial
Session State Before/After
Permission Events
Tool Definitions
Memory Archive
Upload Manifest
必要时 Unified Log
```

前者用于分布式性能观测，后者用于重放、恢复和深度诊断。

---

## 107. Prompt Trace 的生命周期事件

上传流程发射：

```text
TraceUploadAttempted
TraceUploadSucceeded
TraceUploadSkipped
TraceUploadFailed
```

字段包括 Session、Turn、Upload Method、Failure Category、Status Code 和是否完全上传。

Upload Method 可能是：

- Proxy；
- Direct S3；
- Direct GCS；
- No Credentials。

---

## 108. Trace Upload 为什么有 Confirm 与 Defer

### Confirm

后台任务可以等待每个 Artifact 云端确认，不影响用户可见延迟。

### Defer

Turn 结束路径只等待 Durable Queue Accept，并在一个 Deadline 内做有限 Flush。

原则：

> 用户 Turn 的完成不能无限等待诊断上传，但恢复语义又需要区分“已持久确认”和“仅排队”。

只有 Confirmed 才能推进 `restorable_turn_number`。

---

## 109. Trace Manifest 为什么重要

各 Artifact 并行上传后，Manifest 汇总：

- 哪些文件预期存在；
- 哪些成功；
- 哪些跳过及原因；
- 使用什么上传方法；
- 是否完全完成。

没有 Manifest，仅看到 Bucket 中少一个文件时无法判断是“未生成、明确跳过、排队超时还是上传失败”。

---

## 110. Streaming Partial 的特殊处理

Turn Capture 会始终从 Live Slot 中取走，防止后续 Turn 继承旧内容。

- 已提交 Assistant Message：通常丢弃 Partial，因为 History 已包含；
- 未提交：保留 Partial 供失败诊断；
- Doom-loop Recovery：即便最终提交，也可保留被丢弃尝试的片段并标明原因。

这是一种“只保存无法从权威状态重建的信息”的 Artifact 策略。

---

## 111. Trace Upload 本身也不能阻塞 Agent

`spawn_upload_task`：

- 捕获当前 Parent Span；
- Spawn 异步任务；
- 用 `catch_unwind` 捕获上传任务 Panic；
- Panic 只记录错误，不传播到 Agent Turn。

观测/上传子系统故障不应让核心工作失败。

---

## 112. 退出时为什么要 Flush 多套系统

不同管道有不同 Buffer：

```text
Sentry Client
Internal OTEL BatchSpanProcessor
External OTEL Batch Providers
Debug Firehose Non-blocking Writer
Pager Unified Log Buffer
Product Event Spawn Tasks
Instrumentation Writer / Chrome Guard
```

没有一个万能 `flush_all()` 自动覆盖所有生命周期。

源码在 Signal Handler、Normal Guard Drop 和 `process::exit` 前分别调用对应 Shutdown。

---

## 113. `OtelGuard` 的 RAII 退出

正常路径持有 `OtelGuard`。

Drop 时调用 `shutdown_otel`：

- 先关闭 External OTEL；
- 再 Shutdown Internal Tracer Provider。

直接 `process::exit` 不运行 Destructor，所以 Signal Handler 必须显式调用。

---

## 114. External Shutdown 为什么有 Watchdog

Provider Shutdown 可能因 Collector 或网络库异常而卡住。

External Shutdown：

1. 设置 Active=false；
2. 发射 Export Health 摘要；
3. 在独立线程 Shutdown Provider；
4. 最多等待 2 秒；
5. 超时则放弃等待。

退出可靠性优先于保证每个 Observability Batch 都落地。

---

## 115. Export Health 避免反馈回路

External OTEL 自己的：

```text
records_dropped
metric_exports_dropped
export_failures
export_successes
```

会作为内部 Product Event 报告，但不会再次发回 External OTEL。

否则“报告 Exporter 失败”的事件也交给同一个失败的 Exporter，形成无意义反馈回路。

---

## 116. 观测管道的可靠性矩阵

| 管道 | 本地/远端 | 是否允许丢失 | 是否可阻塞 Turn | 主要一致性 |
| --- | --- | --- | --- | --- |
| Tracing Pane | 内存/UI | 是 | 否 | 有界显示 |
| Debug Firehose | 本地文件 | 是 | 否 | 最佳努力诊断 |
| Unified Log | 本地 JSONL | 部分 | 短锁写入 | 跨组件时间线 |
| `events.jsonl` | Session 本地 | 尽量不丢 | 短锁写入 | 运行终态 Exactly-once |
| Product Events | 远端 | 是 | 否 | 分析统计 |
| Internal OTLP | 远端 Batch | 是 | 否 | Span Tree |
| External OTEL | 客户 Collector | 是 | 否 | Curated Logs/Metrics |
| Trace Artifact | 云对象 | 可延期/失败 | 有界 | 重放和恢复 |
| Sentry | 远端 | 是 | 退出有界 Flush | 错误聚合 |

“允许丢失”不代表不重要，而是不能牺牲 Agent 正常执行。

---

## 117. 排查一个慢 Turn 的推荐顺序

1. 拿到 `session_id` 和大致 `turn_number`；
2. 查 `events.jsonl`，确认 Turn/Tool 的运行语义边界；
3. 查 `unified.jsonl` 中 `shell.turn.tool_prep_done` 与 `inference_done`；
4. 若启用 Internal OTLP，查看 `session.process_conversation_turn` Span Tree；
5. 分解 MCP Wait、Tool Collection、TTFT、Model Duration、Tool Duration；
6. 若 Sampler 问题，开启 Sampling Log 复现；
7. 只有认证异常时查看按 Session Snapshot 的 Unified Log Artifact。

先看稳定里程碑，再下钻 Firehose，能减少噪声。

---

## 118. 排查“用户说 Turn 没结束”

依次核对：

```text
events.jsonl
  ├── 是否有 turn_started
  ├── 是否有 active tool 没闭合
  └── 是否有唯一 turn_ended

Unified Log
  ├── handle_prompt.start
  ├── tool_prep_done
  └── inference_done

Tracing / Firehose
  ├── process_conversation_turn span 是否关闭
  ├── Permission 是否等待
  └── Tool Task 是否仍存活

ACP SessionEvent
  └── Server 是否收到 TurnEnded
```

本地已结束但 Server 未收到，通常是 Bridge/Transport 问题；本地也没有终态，则是 Runtime 收敛问题。

---

## 119. 排查 Telemetry “为什么没有数据”

不要只问一个开关，应逐层检查：

```text
调用点是否执行？
  ↓
事件是 log_event 还是 log_session_event？
  ↓
TelemetryMode 是 Disabled / SessionMetrics / Enabled？
  ↓
是否受 ZDR / internal_enabled 更严格 Gate？
  ↓
TelemetryClient 是否在认证后成功 init？
  ↓
Events URL / API Key / Mixpanel 是否配置？
  ↓
进程是否在发送完成前退出？
```

External OTEL 则需要走完全不同的 Double Opt-in 和 Exporter 检查。

---

## 120. 排查 External OTEL “为什么没有数据”

检查：

1. `GROK_EXTERNAL_OTEL` 或本地配置是否启用；
2. Logs/Metrics 至少一个 Exporter 是否非 `none`；
3. Protocol 是否受支持；
4. Endpoint 是否正确；
5. Internal Pipeline 是否已消费通用 OTEL 变量导致 No-double-send 拒绝；
6. Settings Gate 是否仍关闭；
7. Remote Policy 是否 Force Disable；
8. Event 是否声明 External Mapper；
9. Content Field 是否因 Gate 关闭被删除；
10. Export Health 是否显示 Drop/Failure。

---

## 121. 排查 Debug Firehose “为什么进错文件”

重点看：

- Event 是否发生在 `with_session_ctx` Scope 内；
- 当前 Span Ancestor 是否携带 `session_id` 字段；
- Event 是否由脱离 Parent Span 的 Background Task 发出；
- Spawn 时是否 `.instrument(parent_span)`；
- Session ID 是否被 Sanitizer 转换；
- 未关联事件是否进入 `<role>-<pid>.txt`。

异步 `tokio::spawn` 不一定自动保留当前 Span，显式 Instrument 很重要。

---

## 122. 新增观测信号的选择树

```text
是否影响运行恢复或协议语义？
  ├── 是 → EventTracker / SessionEvent
  └── 否
       │
       ├── 需要持续时间和父子关系？
       │    └── tracing Span
       │
       ├── 只是调试信息？
       │    └── tracing Event / Unified Log
       │
       ├── 需要稳定产品分析？
       │    └── Typed TelemetryEvent
       │
       ├── 客户 Collector 也需要？
       │    └── 显式 External Mapper + Schema Review
       │
       └── 需要重放原始状态？
            └── Trace Artifact
```

同一个事实可能需要两个投影，但不应默认全管道复制。

---

## 123. 新增 Span 字段的检查清单

1. 字段是否数值/布尔，还是自由字符串；
2. 是否会进入 Internal OTLP；
3. 字符串 Key 是否已在 Allowlist；
4. 值是否真的不含用户内容；
5. URL 是否应该只保留 Origin；
6. ID 是否会产生高 Cardinality；
7. `u64` 是否应转换为 `i64`，避免被当成字符串后丢弃；
8. 字段是否应预声明为 `Empty` 再补写；
9. 是否需要测试固定 Wire 名称；
10. 是否能用 Enum 替代自由 String。

---

## 124. 新增 Typed Event 的检查清单

1. 定义 `Serialize + Send + 'static` Struct；
2. 用 `telemetry_event!` 绑定稳定 Name；
3. 字段避免原始 Prompt、Command、Tool Result；
4. 分类值使用 Enum；
5. 确认应走 `log_event` 还是 `log_session_event`；
6. 确认 ZDR 下是否允许内部发送；
7. 若需要 External OTEL，新增显式 Mapper；
8. Mapper 只选择最小字段；
9. 内容字段必须使用 Gated Attr；
10. 评估 Metric Label Cardinality；
11. 用独立字面量测试固定 Schema；
12. 检查退出前是否需要 Drain。

---

## 125. 常见误解一：`tracing::info!` 一定会写到磁盘

错误。

它只把 Event 交给当前 Subscriber。

是否写磁盘取决于：

- 是否安装 File Layer；
- Filter 是否允许；
- Target 是否匹配；
- Writer 是否成功打开；
- Buffer 是否在退出前 Flush。

没有 Subscriber 时，Event 可以完全没有外部效果。

---

## 126. 常见误解二：`TelemetryMode::Disabled` 会关闭所有 Observability

错误。

它主要控制内部产品事件和相关 Internal OTLP Export Gate。

仍可能存在：

- Local `events.jsonl`；
- Unified Log；
- Debug Firehose；
- Pager Tracing Pane；
- Explicit External OTEL；
- Crash Handler；
- Sentry 的独立配置结果。

每个系统有自己的 Authority。

---

## 127. 常见误解三：External OTEL 是把所有 tracing 发给客户

错误。

External OTEL 只接收显式 `TelemetryEvent → ExternalRecord` Mapping。

任意 tracing Message、Span Attribute 和 Tool Output 不会自动进入客户 Collector。

这正是它和 Internal OTLP 的最大架构差别。

---

## 128. 常见误解四：本地事件和产品事件应该数量完全一致

错误。

原因包括：

- 不同 Gate；
- SessionMetrics 与 Enabled 差异；
- 产品发送是 Fire-and-forget；
- Runtime Event 有 Exactly-once Guard；
- 一次 Turn 含多个 Model Response；
- Background Task 可能不在 Session Context；
- External Mapper 只覆盖事件子集。

比较前必须先定义预期的一致性关系。

---

## 129. 常见误解五：日志越详细越好

错误。

高频大 Payload 会造成：

- 内存队列膨胀；
- 序列化 CPU 浪费；
- 磁盘增长；
- UI 消费不及；
- 隐私风险；
- Metric Cardinality 爆炸；
- 真正重要信号被噪声淹没。

源码用 Bounded Channel、Lazy Serialization、Target Filter、Content Gate、Allowlist 和 Retention 共同限制它。

---

## 130. 常见误解六：Flush 必须保证所有数据上传完成

错误。

Flush 也是 Best-effort，并且通常有 Deadline：

- Sentry 2 秒；
- External OTEL Shutdown Watchdog 2 秒；
- Product Event Drain 有调用方 Timeout；
- Trace Upload Defer 有 Turn Deadline；
- Debug Writer Drop 会 Join，但异常退出仍可能丢。

观测系统不能让进程永远无法退出。

---

## 131. 源码体现的设计模式

### 模式 A：Ambient Context + Explicit Snapshot

Task-local 提供 Context，异步发送前同步快照，避免时间漂移。

### 模式 B：One Callsite, Multiple Independently Gated Sinks

业务调用点不重复，但每个 Sink 保持独立策略。

### 模式 C：Default-deny Content Export

字符串字段只有显式 Allowlist 才能出站。

### 模式 D：Curated Projection

External Schema 不是内部对象直序列化，而是显式 Mapper。

### 模式 E：Observability Must Not Backpressure Runtime

有界队列、Try Lock、Fire-and-forget、有界 Flush。

### 模式 F：Terminal Chokepoint

Turn Completion、Error Classification 和 Upload Result 在少数入口收敛。

### 模式 G：RAII Lifecycle

Span Guard、Timer、OTel Guard、Appender Guard 用 Drop 完成结束和 Flush。

---

## 132. 推荐的源码阅读顺序

### 第一遍：理解管道

1. `xai-grok-telemetry/src/lib.rs`
2. `events.rs`
3. `session_ctx.rs`
4. `client.rs`

### 第二遍：理解一次 Turn

5. `acp_session_impl/spawn.rs`
6. `acp_session_impl/turn.rs`
7. `acp_session_impl/tool_calls.rs`
8. `turn_end.rs`

### 第三遍：理解远端边界

9. `otel_layer/mod.rs`
10. `otel_layer/redact.rs`
11. `external/mod.rs`
12. `external/schema.rs`
13. `external/emit.rs`

### 第四遍：理解本地诊断

14. `unified_log.rs`
15. `debug_log.rs`
16. `pager/src/tracing.rs`
17. `file-utils/src/events/*`

---

## 133. 建议动手实验一：画出单轮 Span Tree

1. 使用 Chrome Instrumentation 模式运行一个最小 Prompt；
2. 打开生成的 Trace JSON；
3. 找到 `session.process_conversation_turn`；
4. 标记模型请求、Tool、Permission、MCP 子 Span；
5. 对照 `events.jsonl` 的 Turn/Tool 顺序；
6. 对照 Unified Log 的里程碑时间。

目标：亲眼确认三种时间线为什么相似但不完全相同。

---

## 134. 建议动手实验二：验证 Session Firehose 路由

1. 设置 `GROK_DEBUG_LOG=1`；
2. 同时启动两个 Session；
3. 分别执行一个 Tool；
4. 查看 `~/.grok/debug/<session_id>.txt`；
5. 找出没有 Session Context 的启动日志；
6. 确认它们进入 `<role>-<pid>.txt`；
7. 检查 `latest.txt` 指向。

目标：理解 Span Ancestor 怎样成为文件路由上下文。

---

## 135. 建议动手实验三：验证 Typed Event Gate

针对同一个最小 Session 分别运行：

```text
Disabled
SessionMetrics
Enabled
```

观察：

- `SessionStarted`；
- `Turn`；
- `TurnCompletedLifecycle`；
- `PromptSubmitted`；
- `ToolCallCompleted`。

目标：确认 Lifecycle 与 Full Product Event 的边界。

---

## 136. 建议动手实验四：验证 External Content Gate

使用本地测试 Collector：

1. External OTEL 开启但两个 Content Gate 关闭；
2. 发一个含 Prompt 和文件路径的 Tool Event；
3. 检查默认输出中不存在原始内容；
4. 分别打开 Prompt / Tool Details Gate；
5. 检查新增字段经过截断和 Secret Scrub；
6. 确认 Metrics 没有 `prompt.id`。

目标：区分“事件存在”与“内容字段获准存在”。

---

## 137. 建议动手实验五：模拟慢消费者

1. 构造大量 Tracing Event；
2. 暂停 UI Receiver；
3. 观察 `dropped_log_lines()` 增长；
4. 恢复 Receiver；
5. 验证 Agent 主循环没有因日志阻塞。

目标：理解 Bounded Drop-on-full 为什么是可靠性设计，而不是简单缺陷。

---

## 138. 建议动手实验六：验证 Turn Error 分类

构造包含以下信号的 ACP Error：

```text
HTTP 401
HTTP 429
HTTP 403
HTTP 500
MaxTokens Marker
仅 JSON-RPC -32603
未知 Code
```

检查：

- `StopFailureKind`；
- Telemetry `error_category`；
- Goal Infra Pause 判断；
- 用户可读 Detail。

目标：理解同一个 Error 在控制流和分析中的不同投影。

---

## 139. 一张最终心智模型

```text
                     ┌──────────────────────────┐
                     │      Agent Runtime       │
                     └────────────┬─────────────┘
                                  │ runtime facts
          ┌───────────────────────┼────────────────────────┐
          │                       │                        │
          ▼                       ▼                        ▼
  tracing Span/Event       Typed TelemetryEvent     Runtime Event
          │                       │                        │
     Subscriber            external mapper          ├─ events.jsonl
     Registry                    │                   └─ SessionEvent Bridge
   ┌──────┼────────┐       ┌─────┴──────┐
   ▼      ▼        ▼       ▼            ▼
 UI/debug Internal OTLP  Product      External OTEL
 logs       spans         Analytics    curated logs/metrics
   │          │              │            │
   │      default-deny       │       double opt-in
   │      redaction          │       content gates
   │                         │       validation
   └───────────────┬─────────┴────────────┘
                   ▼
           best-effort / bounded
           never block Agent forever

Separate side channels:
  Unified Log → cross-process diagnosis
  Trace Artifact → replay / recovery
  Sentry + Crash Handler → failures
```

---

## 140. 本篇结论

1. Grok Build 的 Observability 是多管道系统，不存在一个万能 Telemetry Bus；
2. `tracing` 负责结构化时间和上下文，Typed Event 负责稳定业务 Schema；
3. `TelemetryCtx` 通过 Tokio Task Local 为并发 Session 提供 Ambient Context；
4. Session Span 同时服务 OTLP 父子树和 Debug Firehose 路由；
5. 产品事件、Session Metrics 和 External OTEL 使用独立 Gate；
6. Internal OTLP 观察广泛 Span，但字符串字段采用默认拒绝和统一脱敏；
7. External OTEL 只接收显式 Curated Mapper，并通过 Double Opt-in 与 Content Gate 控制；
8. Unified Log 用于跨进程人工排障，`events.jsonl` 用于本地运行语义；
9. Turn、Tool 和 Error 在少数 Chokepoint 收敛，避免终态或分类漂移；
10. Trace Artifact 是重放/恢复数据，不等同于 OTLP Trace；
11. 所有远端和高频管道都遵循有界、非阻塞、可降级原则；
12. 隐私不是调用点自觉，而是 Allowlist、Mapper、Gate、Scrubber 和出口 Validator 的组合保证。

---

## 141. Glossary

### Observability（可观测性）

通过日志、事件、指标、Trace 和诊断文件理解系统内部发生了什么的能力。它比“日志”范围更大。

### Telemetry（遥测）

系统自动收集并传递的运行数据。在本文中它可能指产品事件、生命周期指标或 OTEL 数据，必须结合具体管道理解。

### Tracing

Rust 的结构化诊断框架。它产生 Event 和 Span，再由 Subscriber/Layer 决定怎样处理。

### Event

某一个瞬间发生的结构化记录，例如“Tool 完成”或“认证失败”。不同模块中存在多个同名但不同用途的 Event 类型。

### Span

具有开始、结束、字段和父子关系的一段操作，例如一次 Turn 或一次 HTTP Request。

### Callsite

源码中某个 `tracing` 宏调用位置。它携带固定 Metadata 和字段集合。

### Subscriber

接收 `tracing` Event/Span 的顶层消费者。通常由 Registry 加多个 Layer 组成。

### Layer

附加在 Subscriber 上的一层处理逻辑，可以过滤、格式化、写文件或导出 OTLP。

### Target

Tracing Metadata 中的路由标签，通常是模块路径，也可以是 `sampling_log` 等显式字符串。

### Filter / EnvFilter

决定哪些 Level、Target 或模块事件能进入某个 Layer 的规则。

### Ambient Context

不通过每层函数参数显式传递、但在当前异步执行 Scope 中可读取的上下文，例如 Session ID。

### Task-local

绑定到 Tokio 异步任务 Scope 的局部状态。不同于 Thread-local，任务在线程间迁移时语义仍正确。

### Correlation ID

用来把不同记录关联到同一操作的 ID，例如 `session_id`、`prompt.id` 或 `request_id`。

### Cardinality（基数）

一个指标标签可能取多少种不同值。把随机 UUID 当 Metric Label 会产生极高基数和大量 Time Series。

### TelemetryEvent

仓库中的强类型业务事件 Trait，类型绑定稳定事件名，并可选择提供 External OTEL Mapping。

### Product Event

用于产品行为分析的 Typed Event，可发往 Events API 和 Mixpanel。

### Session Metrics

只包含最小 Session/Turn 生命周期和元数据的 Telemetry 模式，介于 Disabled 与完整 Enabled 之间。

### Sink

数据最终到达的目的地，例如文件、UI Channel、Events API、Mixpanel 或 OTLP Collector。

### Fan-out

一个调用点把同一业务事实投影到多个独立 Sink 的过程。

### Gate

决定某条管道或某组字段当前是否允许发送的开关或策略判断。

### ZDR

Zero Data Retention，零数据保留策略。内部数据收集可能受它限制；客户 External OTEL 有独立配置边界。

### OpenTelemetry / OTEL

开放的 Observability 标准，包括 Trace、Metric、Log、Context Propagation 等数据模型。

### OTLP

OpenTelemetry Protocol，用 HTTP/Protobuf 或 gRPC 将 OTEL 数据发给 Collector。

### Collector

接收 OTLP 数据并进一步存储、转换或转发的服务。

### Internal OTLP

发往内部 Observability Backend 的 Span Pipeline，输入来自广泛的 Rust tracing Span。

### External OTEL

由客户显式配置、发往客户自己 Collector 的 Curated Log/Metric Pipeline，不会自动接收任意 Span。

### Resource Attribute

描述产生遥测数据的进程或服务的公共属性，例如 Service Version、Client Name、Team ID。

### Attribute

附加在 Span、Log Record 或 Metric 上的键值字段。

### Metric

面向聚合的数值信号。本文主要是 Counter，例如 Turn Count 或 Token Usage。

### Counter

只递增的 Metric Instrument，用来累计事件次数或数量。

### Log Record

OTEL 的结构化日志记录。External OTEL 的事件使用 Curated Log Record 表示。

### Mapper

把内部 Typed Event 显式转换成 External Record 的函数。它选择字段并做语义降维。

### Curated Schema

经过人工审查、字段集合受控的对外 Schema，不是内部对象的完整序列化。

### Content Gate

专门控制 Prompt、Tool Parameter、File Path 等高风险内容字段是否允许进入 External OTEL 的开关。

### Allowlist

允许通过的字段白名单。Internal OTLP 中字符串属性默认拒绝，只有 Key 在 Allowlist 中才保留。

### Redaction / Scrub（脱敏）

删除或替换 Secret、用户目录、用户名、URL Query 等敏感内容。

### Fail Closed

遇到未知类型或策略尚未确认时默认不发送，而不是默认放行。

### Double Opt-in

需要同时打开主开关和选择实际 Exporter 才激活 External OTEL。

### Monotonic Policy

运行时策略只允许向更严格方向变化，例如 Remote Policy 可以关闭 Gate，但不能重新打开。

### Firehose

高覆盖度调试日志流。它信息丰富、噪声较大，通常需要显式开启。

### Unified Log

Shell、Pager、Desktop 共同使用的 JSONL 诊断日志，用 Source、PID、Version 和 Session ID 重建跨组件时间线。

### JSONL / NDJSON

每行一个 JSON 对象的文本格式。单行损坏通常不会影响其他行，适合追加日志。

### EventTracker

Session Actor 内维护本地运行语义、终态 Guard、Active Tool 和跨 Turn Marker 的组件。

### ObservabilityBridge

把规范化 SessionEvent 通过 Tool Protocol 发给连接 Server 的轻量桥接器；不负责本地 Sink。

### Exactly-once Guard

通过状态位确保某个逻辑终态最多发射一次，例如 `TurnEnded`。

### Best-effort

尽最大努力发送或写入，但失败不会阻塞或中止 Agent 主流程。

### Backpressure（背压）

消费者速度不足反过来阻塞生产者。观测管道通常通过有界队列和丢弃避免把背压传给 Agent。

### Drop-on-full

队列满时丢弃新记录，而不是等待或无限扩容。

### Hysteresis（滞后区间）

允许容器暂时超过目标容量，到更高阈值后批量回收，以减少频繁搬移成本。

### Fire-and-forget

启动异步发送后不等待结果。延迟低，但进程过快退出时数据可能丢失。

### Drain

退出前等待当前 In-flight Task 数量归零，通常带 Timeout。

### Flush

请求 Buffer 尽快写入底层文件或网络。Flush 不一定等于远端已永久存储。

### Guard / RAII

对象创建时获得资源，Drop 时释放或完成操作。用于 Span 关闭、Timer 记录和 Writer Flush。

### Inode

Unix 文件系统中的文件实体标识。路径被 Rename 后，旧文件描述符仍可能指向旧 Inode。

### Advisory Lock

进程之间自愿遵守的文件锁。Unified Log Trim 用它避免多个进程同时重写文件。

### Trace

语义依上下文而异。OTEL Trace 是 Span Tree；Prompt Trace Artifact 是一组可重放/诊断文件。

### Trace Artifact

与某个 Turn 关联的 Session State、Messages、Permission Events、Manifest 等对象文件。

### Manifest

描述预期 Artifact、上传状态和跳过原因的汇总文件。

### TTFT

Time To First Token，从模型请求开始到首个 Token 到达的时间。

### ITL

Inter-token Latency，相邻 Token 到达之间的延迟。

### Wire Contract

跨进程、跨版本或 Dashboard 依赖的稳定字段名、事件名和枚举值。

### Chokepoint

多个控制路径最终汇合的少数出口。适合统一分类、脱敏、终态和 Flush 行为。

### Sentry

错误与异常聚合系统，接收经过脱敏的 Panic、Exception 和 Stacktrace。

### Crash Handler

处理 Native Signal、保存 Crash Report 并恢复终端状态的底层机制，与 Rust Panic Telemetry 不完全相同。

### Broken Pipe

向已关闭管道写数据产生的常见环境错误。Sentry 将它视为噪声并丢弃。

### Lazy Serialization

只有某个 Layer 真正记录字段时才序列化大对象，避免日志被过滤后仍消耗 CPU 和内存。

---

## 142. 下一篇建议

下一篇可以继续 Agent 基础源码精读：

> **源码精读 20：Agent Persistence Runtime——Session 如何把消息、事件、Turn Capture、Rewind Point、Signals、Plan/Goal/Workflow 与恢复游标安全写盘，并在 Load/Resume/Replay 时重建运行状态。**

它会把本文的 `events.jsonl`、Trace Artifact 和 Session Lifecycle 与前面的 Chat State、Compaction、Subagent 串成一套完整的“可恢复 Agent”模型。
