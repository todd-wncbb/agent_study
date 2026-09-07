# 23：事件、Telemetry 与可观测性

## 先从一个调试问题开始

用户说：“Codex 卡住了。”这句话还无法定位问题。你至少想知道：

- `turn/start` 是否被接受？
- Turn 是否真的开始？
- 模型请求有没有发出？
- 首个 token 是否到达？
- 是否在等待审批？
- Tool Call 是否开始、输出是否持续产生？
- 是否发生重试或 compaction？
- Turn 最终 completed、aborted 还是 failed？

可观测性的作用，就是让这些内部阶段能被安全、结构化地看见。

## 1. 五种容易混淆的信息

| 名称 | 主要用途 | 典型消费者 |
|---|---|---|
| Protocol Event | 告诉客户端发生了什么产品事件 | TUI、桌面 App、app-server |
| Log | 给开发者阅读的一条诊断记录 | 本地日志、调试人员 |
| Trace/Span | 关联一次请求内跨函数、跨服务的执行路径 | tracing/OTel backend |
| Metric | 聚合计数、耗时和分布 | 监控与告警系统 |
| Analytics fact | 对产品行为进行结构化统计 | 分析管线 |

它们可能描述同一件事，但不是互相替代。例如 Tool 开始时可以同时产生 UI event、trace span 和计时 metric。

## 2. Protocol Event 是产品契约

`EventMsg` 是 Core 向外报告生命周期和状态变化的协议。常见类别包括：

### 生命周期

- `TurnStarted`；
- `TurnComplete`；
- `TurnAborted`；
- `SessionConfigured`；
- environment connect/disconnect。

### 内容和流式进度

- `ItemStarted` / `ItemCompleted`；
- `AgentMessageContentDelta`；
- reasoning/message events；
- raw response item events。

### 工具和副作用

- `ExecCommandBegin/OutputDelta/End`；
- `PatchApplyBegin/Updated/End`；
- MCP/WebSearch/ImageGeneration begin/end；
- approval/request-user-input events。

### 状态和诊断

- `TokenCount`；
- `StreamError`；
- `Warning` / `Error`；
- `ContextCompacted`；
- `TurnDiff`。

客户端可以依靠这些事件更新 UI，因此新增、删除或改变字段要按外部 API 风险对待。

## 3. Started、Delta 和 Completed 为什么分开

假设命令运行 30 秒：

```text
ExecCommandBegin      UI 创建一条正在运行的命令项
OutputDelta × N       持续追加 stdout/stderr
ExecCommandEnd        标记退出码和完成状态
```

如果只有最终 End，用户会以为系统卡住；如果只有 Delta 没有 End，UI 不知道何时停止 loading。Started/Delta/Completed 是一个小型生命周期协议。

## 4. Event 和 Notification 的关系

Core 产生 `EventMsg`，App-server 将其投影成 v2 notification 或 server request，客户端再转成界面状态：

```text
Core EventMsg
  → app-server mapper
  → v2 notification/server request
  → client routing
  → UI item
```

不是每个 Core event 都必须一对一暴露；映射层可能聚合、兼容或补充关联信息。但不能让同一事件在两层重复合成，造成 UI 重复消息。

## 5. Log 适合诊断，不适合充当产品状态

日志可以写：

```text
INFO turn started thread_id=... turn_id=...
WARN stream disconnected attempt=2
```

但客户端不应解析日志文字判断 Turn 是否完成，因为：

- 日志格式不是稳定 API；
- 不同构建可能设置不同 level；
- 文本难以可靠关联 item ID；
- 日志可能被采样或关闭。

产品状态应使用 typed events，日志用于解释内部原因。

## 6. Trace 与 Span 是什么

可以把 Trace 想成一次请求的“整条办事链”，Span 是其中一个有开始和结束时间的步骤：

```text
Trace: turn-xyz 的完整执行
├── Span: submission dispatch
├── Span: build world state
├── Span: model sampling attempt 1
├── Span: tool exec
└── Span: model sampling attempt 2
```

父子 Span 让你看到时间花在哪里，以及错误从哪个步骤传播出来。W3C trace context 可以在 HTTP/进程边界传播，让远端服务的 Span 仍关联到同一 Trace。

## 7. 各种 ID 分别关联什么

| ID | 关联范围 |
|---|---|
| JSON-RPC request ID | 一次客户端请求与 response |
| Thread ID | 一条长期对话任务线 |
| Turn/submission ID | 一次用户目标的运行生命周期 |
| Item ID | UI/协议中的某个可追踪条目 |
| Tool call ID | 一次 Tool Call 与 Tool Output |
| Trace ID | 一条跨组件诊断链 |
| Span ID | Trace 中某个具体执行步骤 |

排查问题时，先确定你手里的 ID 属于哪一层。拿 call ID 搜 thread store，或拿 JSON-RPC ID 配 Tool Output，通常得不到结果。

## 8. Metric 回答“整体是否异常”

Metric 通常是聚合数字，而不是完整故事：

- 请求次数；
- retry count；
- tool call 成功/失败数；
- token usage；
- TTFT/TTFM；
- sampling、compaction、tool blocking 耗时。

Metric 很适合回答“今天 stream 错误率是否上升”，但单凭一个平均值不一定能还原某位用户的具体失败路径，这时要结合 Trace 和 Event。

## 9. TTFT 与 TTFM

- **TTFT（Time To First Token）**：从 Turn 开始到模型第一个 token 到达；
- **TTFM（Time To First Message）**：从 Turn 开始到第一条用户可见 message/item 出现。

两者可能差很多。例如模型很快开始产生 reasoning/tool planning，但用户可见 message 迟迟没出现：TTFT 正常，TTFM 较高。

## 10. Turn Profile 把总时间拆开

`TurnTimingState`/`TurnProfile` 记录：

- first sampling 前准备；
- sampling；
- compaction；
- sampling 之间的开销；
- tool blocking；
- sampling request/retry 数量；
- pending/idle 等阶段。

RAII timing guard 在离开作用域时结束阶段计时，即使函数提前 return，也不容易忘记停止计时。

## 11. 怎样根据数据定位“卡住”

### 没有 `TurnStarted`

检查 App-server request、thread lookup、submission admission 和 queue。

### 有 `TurnStarted`，没有 sampling span

检查 pre-turn compaction、environment readiness、MCP/Skill 准备和 Prompt 构造。

### Sampling span 很长，TTFT 高

检查网络、provider 排队、请求大小和重试。

### TTFT 正常，但工具长期没有 End

检查 approval wait、remote executor、外部服务或持续进程。

### `StreamError` 多次出现

检查 attempt 数、backoff、WebSocket fallback 和最终 `RetryLimit`。

### 已有 `TurnComplete`，UI 仍 loading

检查 app-server event projection、thread/turn routing 和客户端 item reducer。

## 12. 可观测性本身也有安全边界

日志、Trace 和 Telemetry 不能无限记录所有内容。尤其避免：

- API key、OAuth token、Authorization header；
- 完整环境变量；
- 未截断的命令输出或用户文件；
- 不必要的 Prompt/模型原文；
- 外部工具返回的秘密；
- 高基数、无界属性。

更安全的做法是记录类型、长度、状态、耗时和稳定 ID；需要内容诊断时使用明确授权、有界且可清理的机制。

## 13. 为什么 Telemetry 失败不应拖垮用户 Turn

Telemetry 通常是辅助路径。导出服务不可用时，应在有界队列、batch 和 shutdown 规则下处理，而不是让核心任务一直等待监控系统。

但“辅助”不等于可以完全不测试：不正确的 span 生命周期、无界标签或敏感字段同样会造成严重问题。

## 14. 一个最小排查记录

遇到问题时，保存以下结构比复制几百行日志更有用：

```markdown
Thread ID:
Turn ID:
最后一个产品事件:
最后一个已结束 Span:
当前仍打开的 Span/工具 Item:
Sampling attempt / retry count:
TTFT / TTFM:
是否在等待 approval 或 user input:
终止事件（Complete / Aborted / Error）:
```

## 本章词汇表

| 词语 | 直译 | 在本章中的意思 |
|---|---|---|
| Observability | 可观测性 | 通过外部信号理解系统内部状态的能力 |
| Telemetry | 遥测 | 系统自动产生并导出的事件、指标或追踪数据 |
| Trace | 追踪链 | 一次请求跨多个步骤/服务的完整关联路径 |
| Span | 跨度 | Trace 中一个有起止时间的操作单元 |
| Metric | 指标 | 可聚合的计数、耗时或分布数值 |
| Attribute | 属性 | 附在 Event/Span/Metric 上的结构化键值 |
| Cardinality | 基数 | 某标签可能出现多少不同值；无界高基数会损害监控系统 |
| Reducer | 归约器 | 根据连续事件计算当前 UI/分析状态的组件 |

完整解释见[术语总表](glossary.md)。

## 读完后自测

1. 为什么客户端不能解析日志文字判断 Turn 是否完成？
2. Trace ID、Turn ID 和 Tool call ID 分别解决什么关联问题？
3. TTFT 正常、TTFM 高可能说明什么？
4. 为什么 Telemetry 不应完整记录 Prompt、环境变量和工具输出？

## 源码检查点

- `codex-rs/protocol/src/protocol.rs::EventMsg`；
- `codex-rs/app-server/src/bespoke_event_handling.rs`：Core event 投影；
- `codex-rs/core/src/turn_timing.rs::TurnTimingState`；
- `codex-rs/analytics/src/facts.rs::TurnProfile`；
- `codex-rs/otel/src/events/session_telemetry.rs::SessionTelemetry`；
- `codex-rs/otel/src/trace_context.rs`：W3C trace context；
- `codex-rs/core/tests/suite/otel.rs`；
- `codex-rs/core/src/session/tests.rs`：submission/turn trace 继承测试。
