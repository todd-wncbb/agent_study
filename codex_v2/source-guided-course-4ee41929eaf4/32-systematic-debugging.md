# 32：系统化调试——Codex 看起来“卡住”时怎样找到真正停住的一层

第 20 章沿正常任务追踪了数据，第 22 章区分了错误、取消与恢复，第 23 章介绍了事件和可观测性。本章把这些知识变成一套可操作的排查方法。

我们不再只问“报了什么错”，而是问：

> **最后一个能够确认的事实是什么？它之后本来应该出现什么？**

> 源码基线：`4ee41929eaf4`。日志字段和 UI 文案可能变化，本章应优先记住事件配对、ID 关联和分层定位方法。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分“真的卡死”和“正在等待”；
2. 根据最后一个事件判断问题在模型、工具、审批、MCP 还是客户端；
3. 使用 `thread_id`、`turn_id`、`call_id`、`item_id` 和 `trace_id` 串起证据；
4. 分清 UI 没更新与 Core 没继续运行；
5. 为一次问题收集最小但足够的诊断包；
6. 使用针对性的 `RUST_LOG`，避免一上来就开全量 trace；
7. 用最小复现验证假设，而不是反复盲试。

---

## 2. 运行案例：任务一直显示“进行中”

用户让 Codex：

> 打开 `src/parser.rs`，修复解析错误，然后运行测试。

界面依次显示：

```text
正在分析……
已修改 src/parser.rs
正在运行测试……
```

然后五分钟没有最终回答。

“卡住”至少可能表示：

- 测试本身还在运行；
- 命令在等待 stdin；
- Codex 正在等用户批准；
- 模型流断开，正在退避重试；
- MCP server 没返回；
- Core 已完成，但 app-server 没转发结束通知；
- app-server 已转发，但 TUI/客户端路由到了错误 thread；
- terminal event 没有生成或没有被消费；
- 只是最后一段 UI 没重绘。

所以“页面五分钟没变化”只是**症状**，还不是根因。

---

## 3. 第一原则：先区分“等待”与“失去进展”

一个 active turn 可以合法地等待：

- 用户审批；
- 用户补充输入；
- 长时间命令；
- MCP server；
- 模型响应；
- 网络重试 backoff；
- Compaction；
- 子 Agent 结果。

App-server v2 的 `ThreadStatus::Active` 还可以携带：

- `WaitingOnApproval`；
- `WaitingOnUserInput`。

这两个 active flag 的意义是：任务没有丢失，它在等一个明确的外部决定。

### 3.1 “正在等”不等于“卡死”

例如：

```text
ExecApprovalRequest(call_id = call-7)
```

已经发出，但客户端没有提交审批回答。此时 Core 正常等待 `oneshot` response。正确动作是找到审批 UI 或客户端请求处理问题，不是重启模型。

### 3.2 怎样定义“失去进展”

一个实用定义是：

> 在合理时间内，既没有新事件，也没有已知等待原因，且预期的配对/终态事件没有出现。

“合理时间”必须结合动作：读取小文件与编译整个 Rust workspace 不能用同一阈值。

---

## 4. 第二原则：寻找最后一个可信边界

不要从猜测开始。先列出已经确认的事件：

```text
TurnStarted(turn-42)
ItemStarted(reasoning-1)
ItemCompleted(reasoning-1)
PatchApplyBegin(call-1)
PatchApplyEnd(call-1)
ExecCommandBegin(call-2)
ExecCommandOutputDelta(call-2, "running 120 tests")
```

最后一个事实是：`call-2` 的命令已经开始，而且至少输出过一次。

下一项预期通常是：

```text
更多 ExecCommandOutputDelta(call-2)
或 ExecCommandEnd(call-2)
```

所以第一检查对象应是 command/process/runtime，而不是模型 Prompt。

---

## 5. Started / Delta / End 配对是最强的定位线索

许多 Codex 行为都有生命周期事件：

| 开始 | 中间 | 结束 |
|---|---|---|
| `TurnStarted` | items、tools、messages | `TurnComplete` / `TurnAborted` |
| `ExecCommandBegin` | `ExecCommandOutputDelta`、`TerminalInteraction` | `ExecCommandEnd` |
| `McpToolCallBegin` | server 内部不可见等待 | `McpToolCallEnd` |
| `WebSearchBegin` | — | `WebSearchEnd` |
| `PatchApplyBegin` | `PatchApplyUpdated` | `PatchApplyEnd` |
| `ItemStarted` | delta | `ItemCompleted` |

### 5.1 为什么必须用 ID 配对

同一个 turn 可能并行运行多个工具。不能看到任意一个 `ExecCommandEnd` 就认为刚才的命令完成了，必须比较 `call_id`。

```text
ExecCommandBegin(call-A)
ExecCommandBegin(call-B)
ExecCommandEnd(call-B)
```

这里 `call-A` 仍未结束。

### 5.2 缺 End 事件意味着什么

它只说明“在当前证据中，生命周期没有闭合”，可能是：

- 动作仍在运行；
- 执行层崩溃或连接断开；
- End 已生成但传输/客户端丢失；
- 观察窗口不完整；
- 日志过滤级别没有记录它。

因此缺事件是定位线索，不应立即写成根因结论。

---

## 6. 第三原则：先证明哪一层正常，再看下一层

一条典型链路可以压缩成：

```text
Client input
  → app-server request
  → Core submission
  → TurnStarted
  → model sampling
  → tool call proposal
  → approval / sandbox
  → tool execution
  → tool result
  → next sampling
  → final message
  → TurnComplete
  → app-server notification
  → client rendering
```

如果已经看见 Core 的 `TurnComplete`，就不要继续怀疑模型是否还在生成；应检查 app-server 投影、通知传输和客户端状态。

如果连 `TurnStarted` 都没有，就不要先查 MCP tool timeout；请求可能根本没进入 Core turn。

---

## 7. 八层调试地图

| 层 | 它负责什么 | 常见症状 | 关键证据 |
|---|---|---|---|
| Client/UI | 输入、订阅、路由、渲染 | 后端完成但界面仍转圈 | notification、thread target、UI state |
| App-server | JSON-RPC、Core 事件投影 | 请求成功但客户端缺通知 | request ID、server notification |
| Core/Turn | Agent loop 与终态 | 有开始无完成 | `TurnStarted`、active task、terminal event |
| Model transport | 请求、流、重试 | 长时间无首 token、重连 | `StreamError`、TTFT、retry count |
| Tool routing | 名称、参数、handler | 模型调用后立即失败 | call args、router error |
| Approval/Sandbox | 决策与强制边界 | 等批准、被拒、重试升级 | approval request、decision、sandbox error |
| Executor/MCP | 真实命令或外部工具 | Begin 后无 End | process ID、output delta、MCP duration |
| Persistence | rollout、DB、恢复 | 重启后历史缺失/状态错 | flush、rollout、projection/backfill |

这张表不是调用栈，而是排查时的责任边界。

---

## 8. 情况一：连 `TurnStarted` 都没有

可能问题：

- 客户端没有真正发出 `turn/start`；
- JSON-RPC 参数不合法；
- thread ID 不存在或状态不允许；
- request 到达 app-server，但没有转成 Core submission；
- 当前 active turn 不接受这类 steer。

### 应检查

1. 客户端 request ID 是否收到 response/error；
2. `thread_id` 是否与当前页面一致；
3. app-server 是否记录了 `turn/start`；
4. Core submission channel 是否接收 `Op`；
5. 是否得到 `ActiveTurnNotSteerable` 等结构化错误。

### 不应先做

- 调高模型 timeout；
- 重启 MCP server；
- 修改 Prompt。

因为模型尚未开始工作。

---

## 9. 情况二：有 `TurnStarted`，没有第一个模型 token

这说明 turn 已创建，但第一轮 sampling 尚未产生可观测 token。

可能原因：

- Prompt/工具目录构造耗时；
- MCP required server 启动等待；
- 请求正在排队或连接；
- 模型服务端响应慢；
- WebSocket/SSE 断线并重试；
- 上下文过大，正在 Compaction；
- 认证或用量限制最终将失败。

### 看 TTFT

`TurnCompleteEvent.time_to_first_token_ms` 在 turn 完成后提供已知的首 token 耗时。运行中则应看 sampling/stream 事件和日志。

### 看 `StreamError`

模型流出现可重试错误时，Core 会按预算和 backoff 重试，并可发出类似 “Reconnecting...” 的 `StreamError`，让客户端不至于把正常重连显示成静止。

### WebSocket fallback

当前实现可以在重试预算耗尽后尝试从 WebSocket fallback 到 HTTPS。看到 transport warning 时，不能把它误判成整个 turn 已失败；要继续等待终态事件。

---

## 10. 情况三：模型已经提出工具调用，但没有 Begin

模型输出了 tool call，不代表工具已经开始执行。中间还可能经过：

- 工具名解析；
- JSON 参数解析与 schema 校验；
- handler 查找；
- `PreToolUse` hook；
- approval requirement；
- Guardian；
- 用户审批；
- Sandbox/权限准备。

### 先查是否在等待审批

如果 thread status 带 `WaitingOnApproval`，主路径并没有消失。`TurnState` 中的 `pending_approvals`、`pending_request_permissions` 会保存等待中的 `oneshot` sender。

### 再查工具错误

参数格式错误或工具不存在时，可能根本不会产生真实执行 Begin。应查模型提交的原始 tool name、arguments 和 router 返回结果。

### 再查 Hook

`PreToolUse` 可以阻止或改写输入。看 hook started/completed 事件及 block reason，不要把组织策略拒绝误写成 executor 崩溃。

---

## 11. 情况四：`ExecCommandBegin` 后没有 `ExecCommandEnd`

这是运行案例最可能的分支。

### 11.1 先看最近的 output delta

| 最后输出 | 可能状态 |
|---|---|
| `Compiling ...` | 编译仍在推进 |
| `Waiting for file lock` | 在等 Cargo/Rust 构建锁 |
| `Enter password:` | 在等不适合自动处理的交互输入 |
| `watching for changes` | 启动了不会自行退出的 watch server |
| 长时间完全无输出 | 计算密集、死锁、子进程或输出未 flush |

### 11.2 看 process/session identity

`ExecCommandBeginEvent` 可以带 `process_id`。统一执行会话还可能允许后续 stdin/poll。要确认观察的是同一个进程，而不是另一条并行命令。

### 11.3 检查命令是否预期结束

以下命令本来就常驻：

```text
npm run dev
cargo watch ...
tail -f ...
测试 runner 的 watch mode
```

让常驻服务自行产生 `ExecCommandEnd` 是错误预期。应改用后台/会话管理方式，或在达到验证目标后有序终止。

### 11.4 超时不等于没有副作用

即使命令最终超时，之前的写文件、数据库请求或远端操作可能已经完成。重试前应先检查状态，避免重复副作用。

---

## 12. 情况五：`ExecCommandEnd` 已出现，但没有后续回答

此时 executor 已经交回结果。下一段通常是：

```text
ExecCommandEnd
→ tool output 写入 model context
→ 下一次 sampling
→ 模型决定继续调用工具或给最终回答
```

应检查：

1. tool output 是否与正确 `call_id` 关联；
2. 结果是否过大并被截断；
3. 下一次 sampling request 是否发出；
4. model stream 是否重试；
5. context window 是否超限；
6. Compaction 是否开始；
7. 模型是否又提出了一个不显眼的等待动作。

不要继续盯着测试进程；它已经结束。

---

## 13. 情况六：`McpToolCallBegin` 后没有 `McpToolCallEnd`

MCP 与本地命令的差别是：等待可能发生在 transport、server、认证或真实外部服务中。

### 13.1 按 MCP 生命周期排查

```text
server config
→ connection ready
→ catalog/binding
→ approval
→ tools/call
→ server response
→ McpToolCallEnd
```

如果 Begin 已发出，工具已经越过大部分“是否暴露”的问题，应重点看：

- exact client 是否仍连接；
- server 是否断线；
- `tool_timeout_sec`；
- OAuth/token 是否失效；
- 外部服务是否卡住；
- server 是否发送了 elicitation；
- turn 是否被取消。

### 13.2 `McpToolCallEnd` 也可能代表失败

End 事件的 `result` 可能是 Rust `Err`，也可能是 `Ok(CallToolResult)` 但 `is_error = true`。所以：

```text
有 End ≠ 调用成功
```

但它至少说明生命周期已经闭合，下一步应查错误怎样返回模型，而不是继续等 MCP server。

### 13.3 Duration 是定位工具，不是成功证明

End 事件携带 duration。它可以帮助比较“网络调用慢”与“调用后模型慢”，但不能说明结果业务上成功。

---

## 14. 情况七：Core 已 `TurnComplete`，UI 仍显示运行中

一旦 Core 发出 `TurnComplete`，主 Agent task 已经进入终态。当前实现还会：

- 计算 duration 与 TTFT；
- 清理 Guardian rejection circuit breaker；
- 清除匹配的 active turn；
- 发出 thread idle lifecycle；
- flush rollout，使 terminal event 落盘。

App-server 收到 `TurnComplete` 后，会中止/清理该 turn 的 pending server requests、更新 thread watch 状态，并发送 `turn/completed` notification。

如果 UI 仍转圈，按这个边界排查：

```text
Core TurnComplete
→ app-server event handler
→ TurnCompletedNotification
→ client transport
→ thread routing
→ ChatWidget/state reducer
→ redraw
```

### 14.1 常见客户端问题

- notification 的 `thread_id` 被路由到后台 thread；
- 客户端断线后没有正确 replay；
- delta 临时状态没有被 final item 对账；
- turn lifecycle state 没清除；
- redraw 没触发；
- UI 使用了旧快照。

这时重试模型只会制造第二个 turn，甚至让证据更乱。

---

## 15. 情况八：有 Error，但 Thread 仍然 Active

不是每个 `Error` 都立即等于 terminal event。

`CodexErrorInfo` 中既有：

- `ContextWindowExceeded`；
- `UsageLimitExceeded`；
- `ServerOverloaded`；
- `Unauthorized`；
- `SandboxError`；
- `ResponseStreamDisconnected`；
- `ResponseTooManyFailedAttempts`；
- `Other`；

也有不应把当前 turn 标记失败的控制类错误，例如 `ActiveTurnNotSteerable`、`ThreadRollbackFailed`。

### 调试规则

不要只复制错误字符串。至少记录：

- 结构化 error kind；
- 是否 retryable；
- 是否出现后续 retry/Warning/StreamError；
- 最终是 `TurnComplete(error=...)` 还是 `TurnAborted`；
- thread status 最终是否回到 Idle/SystemError。

---

## 16. Terminal event 是本轮生命线

一个正常 turn 最终应由下面之一闭合：

- `TurnComplete`：任务完成，可能成功，也可能携带 terminal error；
- `TurnAborted`：被中断或以取消语义结束。

`TurnComplete` 不是“必定成功”的同义词，因为它包含可选 `error`。

### 16.1 为什么要显式 terminal event

它让下游能够：

- 停止 loading；
- 清理 pending requests；
- 固定最终 transcript；
- 计算耗时；
- 更新 thread status；
- flush 持久化；
- 决定是否启动队列中的下一项工作。

### 16.2 没有 terminal event 的排查顺序

1. task 是否仍存在；
2. 是否正在等待 approval/input/tool；
3. cancellation token 是否触发；
4. task body 是否返回；
5. task runner 的收尾路径是否执行；
6. `send_event` 是否成功；
7. consumer/channel 是否仍连接；
8. rollout flush 是否只是持久化失败，而不是事件没发出。

---

## 17. ID 是怎样把证据串起来的

| ID | 关联范围 | 最适合回答的问题 |
|---|---|---|
| `thread_id` | 一条长期任务线 | 事件属于哪个任务？ |
| `turn_id` | 一次用户驱动执行 | 这次输入到终态发生了什么？ |
| `call_id` | 一次具体工具调用 | Begin、output、approval、End 是否对应？ |
| `item_id` | 一个消息/推理/tool item | delta 与 final item 是否对上？ |
| JSON-RPC request ID | 一次客户端请求/回复 | app-server 是否答复这个请求？ |
| `trace_id` | 分布式追踪 | 跨组件 span 是否属于同一执行链？ |
| `process_id` | 一个执行进程/PTY | 哪个命令仍在运行？ |
| MCP server/tool identity | 一个外部能力 | 慢的是哪个 server 的哪个工具？ |

### 17.1 最小关联记录

```text
thread_id: 019...
turn_id: turn-42
last event: ExecCommandOutputDelta
call_id: call-2
process_id: process-9
elapsed: 312 s
expected next event: ExecCommandEnd(call-2)
known wait reason: none
```

这比“Codex 卡住了，帮忙看看”有用得多。

---

## 18. 使用日志，但不要被日志淹没

官方诊断说明中，`RUST_LOG` 控制 CLI 和 app-server 的 Rust 日志过滤级别，可使用 `error`、`warn`、`info`、`debug`、`trace`，也可以只打开特定模块。

例如：

```bash
RUST_LOG=debug codex -c log_dir=./.codex-log
tail -F ./.codex-log/codex-tui.log
```

官方说明还指出：

- 交互式 CLI 默认把诊断保存在有界本地存储中；
- 明文 `codex-tui.log` 是 opt-in，需要显式设置 `log_dir`；
- `codex exec` 非交互模式会把消息内联打印，而不是另写 TUI log。

参考 [OpenAI Codex diagnostics](https://learn.chatgpt.com/docs/config-file/environment-variables#diagnostics)。

### 18.1 优先使用模块过滤

```bash
RUST_LOG=codex_core=debug,codex_tui=debug codex -c log_dir=./.codex-log
```

如果已经确认是 app-server 通知问题，可以只提高相关模块，而不是记录整个进程的 trace。

### 18.2 从低噪声逐步升级

```text
warn
→ 目标模块 info/debug
→ 极短时间的目标模块 trace
```

全局 trace 会产生大量内容，增加磁盘、隐私和分析成本，也可能改变时序，让并发问题更难复现。

### 18.3 日志不是产品状态

日志可能被过滤、采样、截断或改文案。判断 turn 是否完成，应以 protocol terminal event 和权威状态为主，日志用于解释“为什么”。

---

## 19. Turn Profile 怎样缩小慢点

`TurnTimingState` 会把一次 turn 的时间分为：

- before first sampling；
- sampling；
- compaction；
- between-sampling overhead；
- tool blocking；
- pending idle after sampling；
- sampling request/retry count。

### 19.1 几种典型形状

| Profile 特征 | 首要怀疑 |
|---|---|
| before-first-sampling 很长 | 配置、MCP 启动、Prompt/工具构造 |
| sampling 很长、TTFT 很长 | 模型排队、网络、transport |
| retry count 高 | 流断开、服务不稳定 |
| tool blocking 很长 | 命令、MCP、approval/input wait |
| compaction 很长 | 上下文容量与压缩路径 |
| Core duration 正常、UI 迟迟不结束 | app-server/client 投影 |

Profile 告诉你“时间花在哪类阶段”，仍需事件和 ID 找到具体动作。

---

## 20. 一套从症状到根因的决策树

```text
界面显示任务仍在运行
│
├─ 有 TurnComplete / TurnAborted 吗？
│  ├─ 有 → 查 app-server notification、thread routing、UI finalize
│  └─ 没有
│
├─ ThreadStatus 有 waiting flag 吗？
│  ├─ WaitingOnApproval → 查审批请求是否送达/回应
│  ├─ WaitingOnUserInput → 查输入请求是否可见/回应
│  └─ 没有
│
├─ 最后是否有未配对 Tool Begin？
│  ├─ Exec Begin → 查 process/output/交互/超时
│  ├─ MCP Begin → 查连接/server/auth/tool timeout
│  └─ 没有
│
├─ 是否有模型 StreamError / retry？
│  ├─ 有 → 查 transport、backoff、retry budget、fallback
│  └─ 没有
│
├─ 有 TurnStarted 吗？
│  ├─ 有 → 查 sampling 前准备、task runner 与缺失 terminal
│  └─ 没有 → 查 client request、app-server、submission
│
└─ 后端状态与 UI 不一致 → 查订阅、replay、snapshot、redraw
```

---

## 21. 不同症状对应的“第一条搜索命令”

### Turn 没开始

```bash
rg -n 'TurnStarted|turn/start|handle_turn_start' codex-rs/app-server codex-rs/core
```

### Turn 没结束

```bash
rg -n 'TurnComplete|TurnAborted|complete_profile_and_duration' codex-rs/core/src
```

### 命令 Begin 无 End

```bash
rg -n 'ExecCommandBegin|ExecCommandEnd|process_id' codex-rs/core codex-rs/protocol
```

### MCP Begin 无 End

```bash
rg -n 'McpToolCallBegin|McpToolCallEnd|tool_timeout' codex-rs/core codex-rs/codex-mcp
```

### UI 没收尾

```bash
rg -n 'TurnCompleted|TurnComplete|finalize|consolidat' codex-rs/app-server codex-rs/tui
```

搜索符号名比搜索某条易变 UI 文案更稳定。

---

## 22. 怎样构造最小复现

一个好的最小复现需要控制变量。

### 22.1 先缩小任务

把：

```text
分析整个仓库、修复所有错误、运行全部测试
```

缩成：

```text
读取一个小文件，然后运行一个会立即结束的命令
```

### 22.2 逐步加回能力

```text
无 MCP、无 Hook、默认模型
→ 加目标 MCP
→ 加目标 Hook
→ 加原来的 sandbox/approval policy
→ 加长上下文
```

每次只改变一个因素。

### 22.3 使用可预测的测试动作

可用教学动作表达不同情况：

| 想测试 | 无害动作 |
|---|---|
| 正常命令闭合 | 打印一行并退出 |
| 短暂等待 | 运行几秒后退出的 fixture |
| 大量输出 | 生成固定数量的假日志 |
| 审批等待 | 访问临时测试路径的受控动作 |
| MCP 超时 | 本地 fake server 延迟但不产生外部副作用 |
| 取消 | 在受控长任务中发送 interrupt |

不要用生产数据库、真实凭据或不可恢复删除来复现。

---

## 23. 比较实验比重复点击更有价值

下面的对照可以快速缩小范围：

| 实验 | 如果正常，说明什么 |
|---|---|
| 同一任务换成 `codex exec` | TUI/交互客户端可能是差异点 |
| 同一命令直接在 shell 运行 | Agent executor/Sandbox 可能是差异点 |
| 禁用目标 MCP 后运行 | MCP 启动或目录可能是差异点 |
| 新 thread 运行同一最小任务 | 旧 thread 上下文/状态可能是差异点 |
| 小上下文运行 | Compaction/context window 可能是差异点 |
| 相同配置、不同 repo | 项目配置/AGENTS/hooks 可能是差异点 |

注意：对照实验用于形成证据，不应为了“试试看”而关闭组织安全策略或使用全权限。

---

## 24. 调试时最常见的错误方法

### 错误 1：看到转圈就重启

重启会清除运行中证据，还可能留下已经产生副作用的半完成动作。先记录 ID、最后事件和进程状态。

### 错误 2：看到任何错误就认定根因

某条 warning 可能已被自动恢复。根因应解释最终症状，并位于失去进展的边界附近。

### 错误 3：只看 UI 文案

UI 是事件投影。必须区分后端没完成和前端没更新。

### 错误 4：只看最后一行日志

最后一行可能只是最近打印的无关后台任务。用 thread/turn/call ID 过滤。

### 错误 5：盲目增加 timeout

超时太短确实会失败，但无限延长 timeout 会把死锁和常驻进程伪装成“耐心等待”。

### 错误 6：直接开启全权限

权限错误需要定位具体缺失能力。全权限既扩大风险，也破坏原问题的可复现性。

### 错误 7：同时改模型、Prompt、MCP 和配置

问题即使消失，也无法知道是哪项变化起作用。

### 错误 8：把敏感信息贴进 bug 报告

日志可能含命令、路径、工具参数、内部 URL 和用户内容。分享前必须最小化并脱敏。

---

## 25. 一份可复用的诊断记录模板

```text
现象：
  UI 显示“运行测试”超过 5 分钟，没有最终回答。

环境：
  Codex 版本/commit：
  表面：TUI / App / codex exec / app-server client
  操作系统：
  执行环境：local / remote
  是否使用 MCP、Hook、Plugin：

身份：
  thread_id：
  turn_id：
  call_id / item_id / trace_id：

最后确认事件：
  ExecCommandOutputDelta(call-2, "running 120 tests")

预期下一事件：
  ExecCommandEnd(call-2)

已知等待状态：
  无 WaitingOnApproval / WaitingOnUserInput

时间：
  开始时间：
  最后进展时间：
  已等待：

最小复现：
  具体步骤：
  是否每次发生：

对照实验：
  直接运行命令：正常/异常
  新 thread：正常/异常

脱敏证据：
  相关事件：
  目标模块日志：
  错误类型：
```

模板里最重要的是“最后确认事件”和“预期下一事件”。

---

## 26. 从运行案例得出结论

回到开头：最后看到 `ExecCommandOutputDelta(call-2)`，没有 `ExecCommandEnd(call-2)`。

进一步检查发现测试命令启动了 watch mode：

```text
Tests passed. Watching for file changes...
```

所以：

- 模型请求正常；
- patch 正常完成；
- approval 与 Sandbox 没有卡住；
- executor 也没有崩溃；
- 真正问题是命令按设计不会自行结束；
- UI 显示 active 是正确投影。

修复方向不是“调高超时”，而是让验证命令使用一次性 test mode，或把长驻服务作为可管理 session 启动，并在验证完成后有序停止。

这就是从症状到根因的完整推理：

```text
UI 转圈
→ 无 terminal event
→ 无 waiting flag
→ 有未配对 Exec Begin
→ 进程仍有输出
→ 输出表明 watch mode
→ 命令生命周期预期错误
```

---

## 27. 本章词汇表

完整总表见[课程术语表](glossary.md)。

| 名词 | 常见写法 | 通俗解释 |
|---|---|---|
| Symptom | symptom | 用户观察到的现象，不一定是根因 |
| Root cause | root cause | 足以解释现象的最底层原因 |
| Trusted boundary | last known boundary | 最后一个有证据确认已经通过的阶段 |
| Expected next event | expected event | 按协议在当前事件之后应出现的事件 |
| Paired event | Begin / End | 用同一 ID 表示一个生命周期开始和结束的事件 |
| Progress | forward progress | 状态持续向终态推进 |
| Stall | stalled | 没有已知等待原因且不再向前推进 |
| Waiting flag | active flag | 明确表示正在等审批或用户输入的状态标志 |
| Terminal event | terminal event | 闭合一次 turn 的 `TurnComplete` 或 `TurnAborted` |
| Correlation ID | correlation ID | 将跨组件记录对应到同一动作的 ID |
| TTFT | time to first token | turn 开始到首个模型 token 的时间 |
| Turn Profile | turn profile | 把总耗时分到 sampling、tool、compaction 等阶段 |
| Backoff | retry backoff | 重试之间逐步等待，避免连续冲击 |
| Fallback | transport fallback | 原传输失败后切换到备用传输 |
| Minimal reproduction | minimal repro | 保留问题、去除无关因素的最小案例 |
| Control variable | control variable | 对照实验中保持不变或一次只改变一个的因素 |
| Targeted logging | module filter | 只提高相关模块日志级别 |
| Redaction | redaction | 分享前移除凭据、隐私和内部标识 |
| Watch mode | watch mode | 持续监听变化、按设计不主动退出的进程模式 |
| Lifecycle closure | lifecycle closure | 开始事件最终有匹配的结束/取消事件 |

### 代码单词和短语拆解

- `systematic`：系统化的；按固定框架逐层排除，不靠猜。
- `debug`：调试；收集证据、定位原因并验证修复。
- `diagnostic`：诊断信息；用于解释状态和故障的证据。
- `symptom`：症状；外部可见现象。
- `root cause`：根因；真正导致症状的原因。
- `boundary`：边界；责任或数据形态发生变化的位置。
- `last known good`：最后已知正常点；此前链路有证据通过。
- `expected`：预期的；按协议或状态机下一步应发生的。
- `pair`：配对；用相同 ID 关联 Begin 与 End。
- `correlate`：关联；证明不同日志/事件属于同一动作。
- `terminal`：终态的；该生命周期不会再继续增长。
- `in progress`：进行中；尚未到终态。
- `idle`：空闲；当前没有运行 turn 或明确等待请求。
- `stalled`：停滞；没有合理等待解释且不再进展。
- `pending`：待处理；请求已存在，正在等回应或调度。
- `retry`：重试；失败后再次执行请求。
- `backoff`：退避；重试前等待一段通常逐渐增长的时间。
- `fallback`：回退方案；主路径失败后切到备用路径。
- `timeout`：超时；等待达到上限后结束等待。
- `elapsed`：已经过去的时间。
- `duration`：一个已闭合阶段的持续时间。
- `flush`：刷出；把缓冲内容明确交给下一层或持久化介质。
- `replay`：回放；把历史事件重新应用到状态机。
- `projection`：投影；从权威状态派生出的客户端/数据库视图。
- `finalize`：最终化；把流式临时状态收束为稳定完成状态。
- `redraw`：重绘；让 UI 根据新状态重新生成画面。
- `reproduce`：复现；用明确步骤再次触发同一问题。
- `deterministic`：确定性的；相同条件下产生可预测结果。
- `control variable`：控制变量；实验中用来隔离因果关系的因素。
- `redact`：脱敏；删除或替换不应分享的内容。
- `watch mode`：监视模式；持续运行并等待文件变化。
- `opt-in`：主动启用；默认不开启，需要显式选择。
- `bounded store`：有界存储；容量有上限，不会无限增长。

---

## 28. 自测题

1. 为什么“UI 五分钟没变化”不能直接证明 Core 卡死？
2. `WaitingOnApproval` 与 `WaitingOnUserInput` 分别表示什么？
3. 为什么 Begin/End 必须使用相同 `call_id` 配对？
4. 只有 `ExecCommandBegin` 没有 End 时，第一检查对象是什么？
5. 已有 `ExecCommandEnd` 后为什么应转查下一次 sampling？
6. `McpToolCallEnd` 为什么不一定代表业务成功？
7. `TurnComplete` 为什么也可能携带失败？
8. Core 已发 `TurnComplete`、UI 仍转圈时应查哪些层？
9. `StreamError` 为什么不一定是 terminal error？
10. TTFT 很长与 tool blocking 很长分别提示什么？
11. 日志与 protocol event 哪个更适合判断权威状态？
12. 为什么优先使用模块级 `RUST_LOG`？
13. 什么是“最后一个可信边界”？
14. 一个最小诊断记录至少应包含哪些 ID？
15. 为什么 timeout 后重试前必须检查副作用？
16. Watch mode 为什么会造成“看似卡住”但实现完全正常？
17. 怎样设计只改变一个因素的对照实验？
18. 分享日志前为什么需要 redaction？

---

## 29. 源码检查点

1. `codex-rs/protocol/src/protocol.rs`
   - 找 `EventMsg`、`CodexErrorInfo`、`TurnStartedEvent`、`TurnCompleteEvent`；
   - 列出各类 Begin/End lifecycle event。
2. `codex-rs/core/src/state/turn.rs`
   - 看 `ActiveTurn`、`RunningTask`、`TurnState`；
   - 找 pending approvals、user input、elicitation 和 dynamic tools。
3. `codex-rs/core/src/tasks/mod.rs`
   - 追 task body 结束后怎样生成 `TurnComplete` / `TurnAborted`；
   - 看 terminal event 后 active turn 清理与 rollout flush。
4. `codex-rs/core/src/responses_retry.rs`
   - 看 retry budget、backoff、`StreamError` 和 WebSocket→HTTPS fallback。
5. `codex-rs/core/src/turn_timing.rs`
   - 对照 TTFT、TTFM、sampling、compaction 和 tool blocking。
6. `codex-rs/core/src/tools/orchestrator.rs`
   - 看 approval→sandbox→attempt→retry 的统一顺序。
7. `codex-rs/core/src/unified_exec/`
   - 追交互进程、poll、stdin、process ID 与取消。
8. `codex-rs/core/src/mcp_tool_call.rs`
   - 追 `McpToolCallBegin/End`、approval 和 timeout。
9. `codex-rs/app-server/src/bespoke_event_handling.rs`
   - 看 Core `TurnComplete` 怎样变成 v2 completed notification。
10. `codex-rs/app-server/src/thread_state.rs`
    - 看 current turn history 如何接收开始、item 和 terminal event。
11. `codex-rs/app-server/src/thread_status.rs`
    - 对照 Active、WaitingOnApproval、WaitingOnUserInput、Idle、SystemError。
12. `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
    - 找 `ThreadStatus` 和 `ThreadActiveFlag` wire shape。
13. `codex-rs/tui/src/app/app_server_events.rs`、`thread_routing.rs`
    - 看 notification 怎样进入 active/inactive thread。
14. `codex-rs/tui/src/chatwidget/protocol.rs`、`turn_lifecycle.rs`
    - 看 ChatWidget 怎样清理 turn 状态。
15. `codex-rs/tui/src/chatwidget/streaming.rs`
    - 看 delta、finalize 与 consolidation。
16. `codex-rs/rollout/src/recorder.rs`
    - 看 persist、flush、shutdown acknowledgement。
17. `codex-rs/core/tests/suite/client_websockets.rs`
    - 用测试理解断线重试和 fallback。
18. `codex-rs/app-server/tests/suite/v2/thread_status.rs`
    - 用行为测试理解状态与 active flags。
19. `codex-rs/tui/src/chatwidget/tests/app_server.rs`
    - 用 snapshot/行为测试理解通知、delta、completion 和 replay。

---

## 30. 一句话总结

系统化调试不是从错误文案猜根因，而是用 thread/turn/call/item ID 找到最后一个已确认事件，检查它的配对或终态事件是否出现，再沿 Client→App-server→Core→Model→Approval/Sandbox→Executor/MCP→Persistence 的边界逐层缩小范围，并用目标日志和单变量最小复现验证结论。
