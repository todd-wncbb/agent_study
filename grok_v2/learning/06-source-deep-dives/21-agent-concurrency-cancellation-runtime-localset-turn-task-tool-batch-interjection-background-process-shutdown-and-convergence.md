# 源码精读 21：Agent Concurrency & Cancellation Runtime 如何启动、打断并收敛一次 Turn

> 上一篇研究 Session 如何跨进程存活。本篇转向进程仍活着时更难处理的问题：SessionActor 如何在持续接收命令的同时运行一个长 Turn；多个 Tool Call 如何并发但仍按正确顺序回填；Interjection、Send Now、Ctrl+C、关闭 Session 与删除 Session 为什么有不同的取消范围；被 abort 的 Rust Future、前台进程、后台任务、Subagent、Workflow 和队列又怎样分别清理，最终只产生一个可信终态。

## 0. 本文回答什么

1. SessionActor 为什么运行在 `LocalSet`，Turn 却另起 `AgentTask`；
2. `spawn_local`、`tokio::spawn`、`AbortHandle` 与 `CancellationToken` 怎样分工；
3. 为什么同一 Session 同时只能有一个 running turn，却可以有多个并发 Tool Call；
4. Prompt Queue 如何防止双重启动、错误出队和迟到 completion；
5. Interjection、Send Now、Side Question 与普通排队分别是什么并发语义；
6. Ctrl+C、Esc、Shutdown、Session Close/Delete 的取消范围有何区别；
7. 为什么 abort Turn Future 之前必须先杀前台进程；
8. Background Task 为什么通常不随交互式 Turn 取消；
9. Tool batch 如何用 `FuturesUnordered` 并发执行并串行 post-flight；
10. 同一路径的写 Tool 为什么仍要加锁；
11. 等待类 Tool 怎样被 Interjection 打断；
12. Terminal timeout、auto-background、显式 kill 与 process reap 有何区别；
13. Subagent 与 Workflow 怎样按 owner、prompt、session、run 分层取消；
14. replay buffer、usage、signals、TurnCompleted 怎样在取消时收敛；
15. Session shutdown 为什么还要 drain Workflow、Hook、Memory、Feedback 与 Persistence。

源码基线：

```text
ed6d543
```

---

## 1. 先给出核心结论

Grok Build 的并发不是“所有事情都 spawn，然后谁先完成算谁”。它更像一个分层运行时：

```text
SessionActor（单线程、串行控制面）
  ├── AgentTask（至多一个 running turn）
  │     ├── sampler stream
  │     ├── concurrent tool dispatch futures
  │     └── turn-local subagents / waits
  ├── TerminalActor（进程生命周期，能跨 Turn）
  ├── Workflow tasks（run-owned CancellationToken）
  ├── background memory / recap / suggestion tasks
  └── Persistence / Signals / ChatState actors
```

核心原则是：

> 控制面串行决定“谁拥有状态和终态”，数据面可以并发执行；所有并发结果最终必须回到一个 ownership gate，迟到或失去所有权的结果只能丢弃，不能再次结束 Turn。

---

## 2. 核心源码地图

### Session 控制面

```text
crates/codegen/xai-grok-shell/src/session/commands.rs
crates/codegen/xai-grok-shell/src/session/handle.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/run_loop.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/tasks_cancel.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/prompt_queue.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/notification_drain.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn_end.rs
```

### Turn、Tool 与 Interjection

```text
crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/tool_calls.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/tool_dispatch.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/interjection.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/recap.rs
```

### Process 与后台任务

```text
crates/codegen/xai-grok-tools/src/computer/local/terminal.rs
crates/codegen/xai-grok-tools/src/computer/local/lifecycle.rs
crates/codegen/xai-grok-tools/src/computer/local/shell_state.rs
crates/codegen/xai-grok-shell/src/tools/notification_bridge.rs
```

### Subagent 与 Workflow

```text
crates/codegen/xai-grok-tools/src/implementations/grok_build/task/
crates/codegen/xai-grok-shell/src/agent/subagent/
crates/codegen/xai-grok-shell/src/session/workflow/manager.rs
```

---

## 3. 先区分六种“并发单位”

| 单位 | 所有者 | 生命周期 | 取消方式 |
| --- | --- | --- | --- |
| SessionActor | Agent/leader | Session resident 期间 | mailbox 关闭或 Shutdown |
| AgentTask | SessionActor | 一次 prompt turn | `AbortHandle::abort` |
| Tool Dispatch Future | AgentTask | 一次 tool batch | 父 Future drop / drainer abort |
| Terminal Process | TerminalActor | 可短于或长于 Turn | signal process group |
| Subagent | Coordinator | child Session/turn | typed cancel target/token |
| Workflow Run | WorkflowManager | 可跨多个 child | run-owned `CancellationToken` |

这些单位不能用同一个 cancel bit 管理。Turn Future 被 abort，并不自动证明 OS 子进程已死；Session 关闭也不一定意味着持久化历史不能再次 resume。

---

## 4. 为什么 SessionActor 使用 `LocalSet`

`SessionActor` 包含 `Rc`、`RefCell`、`Cell` 等 `!Send` 状态，因此运行在 Tokio 单线程 `LocalSet`。这带来一个很实用的保证：

```text
同一个 SessionActor 的 command handler 不会在两个线程同时执行
```

它仍然是异步的：handler 在 `.await` 时可以让出执行权，但 actor loop 每次只选择一个 ready 分支继续处理。

“单线程”不等于“不会并发”。Actor 会 spawn Turn、Tool drainer、Memory、Recap 等子任务；只是 Session 权威状态的修改重新汇聚到 Actor 或专属子 Actor。

---

## 5. `run_session` 是控制平面的事件循环

主循环使用 `tokio::select! { biased; ... }` 同时等待：

- idle memory flush timer；
- dream timer；
- model switch watch；
- ChatStateActor events；
- replay-buffer events；
- turn completion channel；
- SessionCommand mailbox。

`biased` 表示分支同时 ready 时按源码顺序偏好上方分支。它不是严格优先级队列，也不保证某个持续 ready 分支永不影响下方分支；这里的顺序必须结合每个分支是否会快速让出来理解。

---

## 6. 为什么 Turn 不能直接在 Actor 命令分支里 await

一次 Turn 可能等待模型几十秒、执行工具数分钟、等待用户权限或后台任务。如果命令分支直接：

```rust
session.handle_prompt(...).await
```

Actor 将无法处理：

- Cancel；
- Interject；
- Queue 编辑；
- Background task completion；
- Session Shutdown；
- MCP 更新。

因此 `maybe_start_running_task` 把 Turn spawn 为独立 `AgentTask`，Actor 立即回到 select loop。

---

## 7. `AgentTask` 的结构很小，但语义很重

```rust
struct AgentTask {
    prompt_id: String,
    handle: tokio::task::AbortHandle,
}
```

创建时 `spawn_local(run_task(...))`；`run_task` 执行 `handle_prompt`，然后通过 `completion_tx` 发回 `(prompt_id, PromptTurnResult)`。

Actor 不保存完整 JoinHandle，而保存 AbortHandle，因为正常结果走 completion channel；控制面主要需要：

- 判断任务是否 finished；
- 在取消时 abort；
- 用 `prompt_id` 做所有权归因。

---

## 8. 一次 Turn 的启动协议

```text
SessionCommand::Prompt
  -> queue_input
  -> pending_inputs
  -> maybe_start_running_task
       -> re-check no running task
       -> choose/merge queue front
       -> pin current_prompt_id
       -> publish CurrentPromptIdResource
       -> state.rewindable = true
       -> broadcast promoting queue state
       -> state.running_task = AgentTask::new_prompt(...)
```

先广播 promoting 状态，再 spawn Turn，可让客户端先画出 running row，避免 UserMessageChunk 更早到达造成 UI 竞态。

---

## 9. 为什么 `pending_inputs.front()` 在运行期间不能 pop

运行中的 prompt 仍留在 queue front，直到 completion：

```text
pending_inputs.front() == running turn
state.running_task.prompt_id == front.prompt_id
```

好处是 front 同时持有原 RPC 的 `respond_to`、trace config、queue metadata 等。Turn 完成时一次性 pop 并回应 caller。

代价是所有 queue 编辑都必须把 running front 当作 pinned row，绝不能删除、重排或被 Send Now 插到前面。

---

## 10. `current_prompt_id` 与 `running_task` 为什么同时存在

`current_prompt_id` 是跨模块同步读取的 pin，供 Subagent 归因、roster、外部 cancel 使用；`running_task` 在 Actor state lock 下，是队列所有权的权威依据。

Completion 处理中，pin 可能先清空，而 front 尚未 pop。若 queue 编辑只看 pin，就会误以为 front 不再运行。因此源码明确要求：

```text
队列一致性 -> 看 state.running_task / running_prompt_id()
外部快速归因 -> 看 current_prompt_id
```

---

## 11. 启动必须在 await 后重新检查

`maybe_start_running_task` 为读取配置可能暂时释放 state lock。期间另一个路径可能已启动 Turn或清空队列。

所以它执行：

```text
lock: 快速检查
unlock: async config I/O
lock: 再检查 running_task / queue
```

这叫 double-check after await gap。Rust 的借用检查器只能防内存不安全，不能自动防“await 期间业务前提已变化”。

---

## 12. Completion Channel 是 Turn 回到 Actor 的唯一正常入口

Turn Future 完成后不直接修改 queue，而发送：

```text
(prompt_id, PromptTurnResult)
```

Actor completion 分支先 flush replay buffer，再：

1. 计算 Goal degradation plan；
2. `handle_completion`；
3. drain monitor buffer；
4. 处理 Goal continuation；
5. 把迟到 Interjection 转成 prompt；
6. 启动下一 queued prompt；
7. 尝试 drain notifications；
8. 若真正 idle，发 idle 状态。

---

## 13. Completion Ownership Gate

`handle_completion` 只有在：

```text
pending_inputs.front().prompt_id == completion.prompt_id
```

时才拥有该 completion：pop front、回应 RPC、清 running task、发 `TurnCompleted`。

若 Cancel 已经移除并终结该 prompt，迟到 completion 会落入 unknown/stale 分支，只记录日志，不再发第二个 terminal，也不能清掉后来启动的新 Turn。

这是全篇最重要的不变量之一：

> 完成结果本身不携带终结权；只有仍拥有 queue front 的结果才能提交终态。

---

## 14. 为什么会出现迟到 Completion

`AbortHandle::abort()` 请求 Tokio 丢弃任务，但竞态窗口中：

- Future 可能刚好已完成；
- completion 已发送但 Actor 尚未消费；
- Cancel 与 completion 两个 select 分支同时 ready；
- 当前 pin 已清，queue front 尚未 pop。

因此正确性不能依赖“abort 后 completion 永远不会来”，必须在提交点检查所有权。

---

## 15. RAII Guard 保护 Turn 活跃标志

`TurnActiveGuard` 激活时写 true，Drop 时写 false。它同时用于全局 ToolContext flag 和每 Session flag。

无论正常 return、`?`、panic unwind 还是 Future abort，只要 guard 被 drop，flag 就被清理。

`TurnSubagentScopeGuard` 类似：Drop 时仅在 pin 仍等于自己的 prompt ID 时清空，避免旧 Turn 的清理覆盖新 Turn 的 pin。

RAII 的意义是把 cleanup 绑定到所有权，不要求每条 return 路径记得手工执行。

---

## 16. `TaskSlot<T>`：可替换的单任务槽

`TaskSlot` 用 `Cell<Option<JoinHandle<T>>>` 管理 deferred prefix、idle notification debounce 等 Session-local 子任务：

- `arm(new)`：abort old，保存 new；
- `take()`：转移所有权，允许 await；
- `cancel()`：abort 并清空。

它表达的是 latest-wins background computation，不适合需要保留所有结果的任务。

---

## 17. 取消不是单个布尔值

`CancelOptions` 包含：

```text
cancel_subagents
kill_background_tasks
rewind_if_no_output
trigger
user_initiated
```

`CancelTrigger` 区分：

```text
Esc / CtrlC / SendNow / Shutdown
SessionClose / SessionDelete / Client(custom)
```

Trigger 多数用于报告，但 Ctrl+C 还会抑制 queued task wakes；不同入口通过 Options 明确选择清理范围。

---

## 18. ACP Cancel 的默认语义

外部 `cancel` notification：

- 等待 Session load 完成；
- 从 meta 解析 `cancelTrigger`；
- `cancelSubagents` 默认 true；
- `rewindIfPristine` 默认 false；
- `user_initiated=true`；
- 在 per-session dispatch lock 下投递 `SessionCommand::Cancel`。

Dispatch lock 让 cancel 与同 Session 的并发入口按受控顺序进入 Actor，而不是在 load/attach 切换时投错旧 handle。

---

## 19. Cancel 命令进入 Actor 后先做什么

顺序是：

1. flush replay buffer；
2. 清 pending interjections；
3. `cancel_running_task(options)`；
4. 自动暂停 active Goal；
5. 尝试启动保留下来的下一个 queued prompt；
6. 非 Ctrl+C 时尝试 drain notification。

先 flush 是为了让已流式展示的 reasoning/text 尾部写入 Updates；否则 trace snapshot 和 replay 都会少掉 Cancel 前最后一段。

---

## 20. Cancel 清理的分层顺序

`cancel_running_task` 大致执行：

```text
request compaction cancel
  -> optional task-wake suppression
  -> capture prompt identity
  -> optional abort producer + cancel subagents
  -> record cancellation signal
  -> kill foreground terminal processes
  -> optional kill background tasks
  -> drain monitor buffer / transform queue
  -> clear tool/goal active resource
  -> emit TurnEnded cancellation fact
  -> abort AgentTask
  -> reset turn-active and blocking-wait state
  -> finalize usage
  -> emit durable TurnCompleted
  -> resolve cancelled prompt RPCs
```

注意：进程清理发生在最终 `AgentTask.abort()` 之前。

---

## 21. 为什么先杀前台进程，再 abort Rust Future

Rust Future 被 drop 不等于 OS 子进程被 kill。TerminalActor 可能独立拥有 child handle，甚至进程组中还有 grandchildren。

若先 abort Tool Future：

- 等待 Terminal reply 的 receiver 被丢弃；
- child 仍可能运行并写文件；
- cleanup 上下文更难取得 tool/session owner；
- 用户以为停止，命令却继续产生副作用。

所以 Cancel 先通过 ToolBridge 请求 TerminalBackend 杀前台进程，再 abort Turn Future。

---

## 22. 为什么交互式 Cancel 默认保留后台任务

Foreground command 属于当前模型调用的阻塞工作；Background task 是用户/模型显式选择的长期任务，可能是 dev server、测试、monitor。

普通 Ctrl+C/Esc 默认：

```text
kill foreground
keep background
```

Subagent teardown、Session Close/Delete 等 hard stop 设置 `kill_background_tasks=true`，才连后台任务一起终止。

---

## 23. Owner-scoped Process Cleanup

Subagent 与 parent 可能共享同一个 TerminalBackend。Subagent 退出时若调用全局 `kill_foreground_commands()`，会误杀 parent 或 sibling。

因此 Terminal request 保存 `owner_session_id`，并提供：

```text
kill_foreground_commands_by_owner
kill_all_background_tasks_by_owner
```

Owner scope 是资源隔离的一部分，不只是 telemetry 标签。

---

## 24. Ctrl+C 的 Task Wake Barrier

后台 task completion 可以自动合成新 prompt 唤醒 Agent。用户按 Ctrl+C 的意图通常是“先停下来”，若 completion 恰好到达又立即启动 Turn，会看起来取消无效。

Ctrl+C 因此同时设置：

```text
tool_context.task_wake_suppressed = true
state.notifications_suppressed = true
```

并清除 queued TaskCompleted/WorkflowCompleted synthetic wakes，将其转换成 fallback 记录。下一次真实用户输入或 Send Now 会重新开 gate。

---

## 25. Monitor Buffer 的取消竞态

Monitor event 根据 `is_turn_active` 可能先进入 mid-turn buffer。Cancel 请求 abort 后，RAII guard 尚未 drop 的短窗口内，新事件仍可能误入该 buffer。

所以 Cancel 在 state lock 下调用 `sweep_monitor_buffer_into_pending`，把它们搬到安全队列：

- Ctrl+C：保留但延迟 drain；
- 其他 cancel：稍后可正常 drain；
- hard teardown：后台任务已杀，直接清通知。

---

## 26. Queue 在 Cancel 时不能全部清空

旧实现若 `mem::take(pending_inputs)`，会把用户已经排队的后续 prompts 一起丢掉，而客户端镜像仍显示它们。

当前策略：

- index 0 是 running turn，必须 resolve Cancelled；
- hard teardown 清整个 queue；
- Ctrl+C 额外移除 task/workflow synthetic wakes；
- 普通真实用户 prompts 保留，Cancel 后可立即 promote；
- rewind pristine 单独弹回输入。

Queue 是 server-authoritative，任何删除都必须回应 RPC 并广播新权威状态。

---

## 27. 即使 `running_task=None` 也要取消 Queue Front

存在窄窗口：completion 已 dequeued、下一任务尚未 promote，或 Cancel 早于 `maybe_start_running_task`。Front 可能有 pending RPC，却暂时没有 live task。

若只在 `running_task.is_some()` 时 resolve front，客户端 `session/prompt` 会永远等不到响应，spinner 卡住。

因此 Cancel 总把 index 0 当作需要明确处理的槽位，再用 task/pin 决定终态归因。

---

## 28. `rewind_if_no_output` 是“撤回”，不是普通取消

若 Turn 仍 `rewindable` 且 client 请求 `rewindIfPristine`：

- abort task；
- pop 当前输入；
- prompt index 减一；
- truncate prompt texts/Conversation；
- truncate file rewind tracker；
- completion kind 返回 `Rewound`。

它不记录用户取消率，也不产生普通 MidTurnAbort 标记，因为语义是“这轮像没开始过”。

---

## 29. Send Now 是 Cancel-and-Send，不是 Stop

真实 prompt 在以下情况可成为 Send Now：

- client 明确请求；
- 当前 Turn 卡在 interruptible wait，且没有其他 held user queue；
- 从 queued row 原子 promote。

Send Now 插在 running front 之后，并保持多个 send-now prompts 的 FIFO。若 Goal loop active，只排到前面但不取消 Goal Turn。

它取消当前 Turn 后立即启动新 prompt，同时：

- 不取消 Subagent/后台任务；
- 不记录“用户中止后重定向”提醒；
- 不抑制 task wake；
- terminal meta 带 `cancelTrigger=send_now`。

---

## 30. Interjection 与 Send Now 的根本区别

```text
Interjection -> 当前 Turn 继续，在下个安全点把用户消息注入 Conversation
Send Now     -> 当前 Turn 终止，新消息作为下一独立 Turn
```

Interjection 适合“补充约束”；Send Now 适合“停止当前方向，马上处理新请求”。两者都可能看起来像中途输入，但运行语义完全不同。

---

## 31. Interjection 的到达路径

Actor 收到 `SessionCommand::Interject`：

1. 广播给所有 attached clients；
2. 立刻记录 Interjected telemetry；
3. 若 Turn 正在运行，push 到 `pending_interjections`；
4. 若已经 idle，转换为 front-of-queue fallback prompt 并启动。

Telemetry 在 enqueue 时记录，而不是等 drain，避免随后 Cancel 清 buffer 导致用户动作完全无记录。

---

## 32. 为什么 Interjection 需要 fallback prompt

客户端判断“正在运行”与 server 真正 Turn 结束之间有竞态。消息可能在最终 drain 之后才到达。

若只 push buffer，它将永远无人 drain。源码在两处兜底：

- 到达时发现 idle：立即转 prompt；
- completion bookkeeping 后发现 stranded：按原顺序转 front prompts。

插入时不能越过仍在运行的 pinned front，否则 completion pop 会错位。

---

## 33. Interjection 的安全 Drain Point

Tool batch 结束后与 Agent loop 的适当迭代边界调用 `drain_pending_interjections`：

- 格式化 `<user_query>`；
- 处理图片；
- 解析 slash skill；
- 持久化 UserMessageChunk；
- 追加独立 synthetic User ConversationItem。

它不拼进 ToolResult，因为用户 steering 应在 compaction、replay 和 analytics 中保持独立 User 语义。

---

## 34. 等待类 Tool 可以被 Interjection 合作式打断

对 `wait_tasks`、`get_task_output(timeout>0)`、`Await` 等 Tool，dispatch 使用：

```text
select!
  tool result
  pending interjection
```

Interjection 到达时返回一个模型可见的 cancelled wait result，让 Agent loop 继续并 drain 用户消息。底层 Background task 没被杀，只是不再阻塞当前 Turn。

这是一种 cooperative interruption：停止等待，不停止被等待对象。

---

## 35. `BlockingWaitGuard` 的作用

Guard 增减共享 wait depth。Queue intake 看到：

```text
turn running
blocking_wait_depth > 0
没有已排队 user prompt
```

时，可把新用户输入自动解释为 Send Now，而不是让它无限排在一个长 wait 后面。

Cancel 时显式 reset depth，因为被 abort 的 nested futures 的 guard 可能稍后才异步 drop；立即接收新输入不能读到陈旧深度。

---

## 36. `/btw` Side Question 是旁路并发

`SideQuestion` snapshot parent Conversation，发起一次 tool-free 模型调用并返回文本：

- 不打断当前 Turn；
- 不修改主 Conversation；
- Client tool calls 被丢弃；
- hosted search 可由采样层处理；
- overload 才重试；
- 结果写 `btw_history.jsonl`。

它与 Interjection 不同：Interjection 改变主 Agent 后续推理，`/btw` 只是从同一上下文旁路提问。

---

## 37. Tool Batch 的三阶段结构

```text
Prepare（串行）
  parse / hook / permission / policy

Dispatch（并发）
  FuturesUnordered

Post-flight（Actor Turn 内串行 drain）
  chat state / events / hooks / signals / followups
```

权限拒绝等 Prepare 终态会阻止后续 calls，并为未执行 calls 补 ToolResult，保证 Conversation schema 完整。

---

## 38. 为什么 Prepare 默认串行

Prepare 可能弹权限 UI、执行 hook、更新 Plan Mode、决定 early ToolLoop。并发准备会造成多个审批竞相出现，或后面的 Tool 在前面已拒绝时仍被放行。

所以先串行获得 `PreparedToolCall`，只有 approved calls 进入数据面并发。

---

## 39. `FuturesUnordered` 怎样执行 Tool

每个 approved call 形成 Future，返回：

```text
(original_index, Result<ToolRunResult, ToolError>, duration_ms)
```

全部放入 `FuturesUnordered`；drainer task 按完成顺序把结果送入 channel。Post-flight 可以尽快处理先完成的 Tool，而无需等待最慢的 call。

Original index 用于准确取回对应 `PreparedToolCall`，不是强制按模型声明顺序等待结果。

---

## 40. `AbortOnDrop` 防止 Tool Drainer 泄漏

Drainer 使用 `tokio::spawn`，其 JoinHandle 包进 `AbortOnDrop`。如果父 Tool batch 因 Turn Cancel 被 drop，guard drop 会 abort drainer。

否则 detached drainer 可能继续等待 futures、发送已经无人接收的结果，甚至让资源比 Turn 活得更久。

这是结构化并发的补偿模式：Tokio task 默认 detached，必须用 owner guard 把子任务生命期重新绑定父 scope。

---

## 41. 并发 Tool 为什么还要 Path Lock

Readonly tools 可以并发；两个写同一路径的 Tool 若同时运行，可能互相覆盖或让 patch 基于陈旧内容。

Batch 先收集 write paths，为同一路径建立共享 `tokio::Mutex<()>`。每个 dispatch 在真正执行前获取对应 lock。

所以并发策略是：

```text
不同资源 -> 并发
同一写资源 -> 串行
```

不是全局禁止并发，也不是无条件并发。

---

## 42. 结果完成顺序与 Conversation 顺序

Dispatch results 按完成顺序进入 post-flight，源码必须确保 ToolResult 与对应 call ID 正确关联，而不是依赖“第一个完成就是第一个声明”。

一些累计状态还需等全部并发结果完成后统一归并，例如 Git/PR attribution、deferred followups 与最终 ToolLoop。设计测试应主动让第二个 Tool 先完成，验证没有顺序假设。

---

## 43. Tool Batch 中的共享恢复

并发 calls 共享 `OnceCell<bool>`，用于认证恢复等“一批只做一次”的昂贵动作。多个 call 同时遇到相同 auth 问题时，只允许一个执行 recovery，其余复用结果。

这叫 single-flight：并发消费者共享同一初始化/恢复 Future，避免登录刷新或 server reconnect 风暴。

---

## 44. 为什么 Exit Plan Mode Tool 被拆到 tail

多个 Tool Call 中若包含 Exit Plan，源码把普通 body 与 exit tail 分批执行。Plan Mode 状态变更可能改变后续写权限和审批语义，不能与普通 calls 无序并发。

这是一个提醒：Tool parallelism 不仅受资源冲突约束，也受控制流 Tool 的状态机语义约束。

---

## 45. TerminalActor 为什么独立于 Turn

TerminalActor 维护：

- child/process group；
- output buffers/files；
- foreground/background 状态；
- completion waiters；
- timeout、output limit、memory monitor；
- owner Session；
- completed snapshots 与 TTL。

它必须持续轮询所有进程，即使启动它的 Tool Future 已返回或 Turn 已结束。这正是后台命令能跨 Turn 存活的基础。

---

## 46. Foreground 与 Background 的身份转换

Foreground run 先以内部 UUID 作为 map key，并有等待 reply。转后台时：

1. 从旧 key remove；
2. 标记 `Backgrounded { reason }`；
3. timeout 改为 `BACKGROUND_MAX_RUNTIME`；
4. 用当前结果解除 foreground waiter；
5. 以 `tool_call_id` 重新插入；
6. 进程继续运行。

转后台不是重新 spawn，而是同一 child 的所有权与等待语义转换。

---

## 47. 三种“超时”不要混淆

| 超时 | 行为 | 进程是否继续 |
| --- | --- | --- |
| Foreground block budget | 提前转后台 | 是 |
| Command timeout + auto-background | 到 timeout 转后台 | 是 |
| Command timeout + no auto-background | kill/finalize | 否 |

此外还有 Background max runtime、output size cap、wait API deadline、pipe drain timeout、MCP/LLM idle timeout。它们保护不同资源，不能统一叫“工具超时”后丢失语义。

---

## 48. 为什么 Background Task 仍需硬上限

后台并非无限制：Terminal poll loop 会检查：

- `BACKGROUND_MAX_RUNTIME`；
- output file cap；
- memory high event；
- explicit kill；
- backend shutdown。

长期任务必须从“前台 latency budget”脱离，但仍受资源预算与清理策略控制。

---

## 49. Process Group 而不是只杀 PID

Shell command 常产生 grandchildren。只 kill direct child 可能留下真正执行工作的子孙。

Local terminal 将命令放入 process group，取消时向 group 发 signal。Process group handle 还需及时释放，避免 PID/PGID 被 OS 重用后，迟到的 kill 误伤新进程。

---

## 50. 显式 Kill 的两阶段协议

`kill_task` 期待返回时任务真的停止，因此使用：

```text
SIGTERM
  -> 等 1 秒 graceful exit
  -> SIGKILL
  -> 最多等 5 秒 reap
  -> drain output
  -> finalize state
```

若 child 卡在不可中断 kernel I/O，reap timeout 后暂时 abandon，由 poll loop 继续尝试。所有 await 都有界，TerminalActor 不会永久卡死。

---

## 51. Kill、Exit、Complete、Settled 不相同

Terminal `Lifecycle`：

```text
Running
  -> Exiting（已有 status，pipes 未读完）
  -> Finished（output final，可能未 reap）
  -> Swept（内存详情已落日志，可能仍未 reap）
```

`has_exited`、`is_complete`、`is_settled` 分别回答不同问题。只有 output final 且 child 已被 wait/reap，才真正 settled。

这避免把 zombie process 当作已彻底清理。

---

## 52. 为什么取消仍要保留部分输出

Timeout/kill 前已产生的 stdout/stderr 对模型和用户仍有价值。Terminal 在 finalization 前 drain remaining output、flush/truncate output file，再通知 waiters。

取消不是删除运行痕迹；正确终态应包含：

- partial output；
- signal/timeout reason；
- truncated flag；
- duration；
- output artifact path。

---

## 53. Background Completion 的 Auto-wake

后台进程完成后 NotificationBridge 产生 TaskCompleted update。Actor-authoritative admission 同时检查：

```text
task_wake_suppressed gate
state.notifications_suppressed
Goal loop state
task completion reservation/report state
```

通过后合成 synthetic prompt；失败则保存 fallback，不可让完成事件既自动唤醒又随后重复注入。

---

## 54. User Prompt 优先于 Synthetic Wake

真实 User prompt 到来时会 sweep 尚未运行的 completion-id-bearing synthetic prompts，并释放 reservation。

这是用户优先规则：后台“测试完成”提醒不能挡在用户刚输入的问题前面。已经 running 的 synthetic front 仍是 pinned，不可直接删除。

---

## 55. Subagent 取消有多个 Scope

```text
ParentPromptId -> 只取消本 Turn 启动的 children
ParentSession  -> 取消该 Session 的非 workflow children
SubagentId     -> 指定 child
Workflow Run   -> 由 run owner 取消自己的 children
```

软取消/max-turn 使用 prompt scope；用户 Stop 且 `cancel_subagents=true` 使用 session-bound backend，不能 wildcard 到其他 Session。

---

## 56. 为什么取消 Subagent 前先 abort Producer

若先 sweep 已存在 children，再 abort parent Turn，Turn 可能在两步之间又 enqueue 新 Task Tool 请求，形成漏网 child。

源码先 abort running producer，再请求 coordinator 的 parent/session sweep，并关闭 spawn admission，直到下一 Turn 再 reopen。

这是经典顺序：

```text
close producer gate -> cancel existing consumers/work -> drain
```

---

## 57. Workflow 使用 Cooperative CancellationToken

Workflow run 拥有 `CancellationToken`。Cancel 时：

- `token.cancel()`；
- 显式取消 run children；
- runner 在 select/checkpoint 感知取消；
- 通过 done oneshot 告知 manager 已退出。

与 Turn 的 AbortHandle 相比，Workflow 更适合 cooperative cancel，因为它需要保存 terminal state、释放 budget/lease，并允许清理逻辑运行。

---

## 58. Workflow Shutdown 有统一 Deadline

`cancel_all_and_drain(timeout)`：

1. drain active runs；
2. cancel tokens 与 children；
3. 收集 done receivers；
4. 使用同一个 absolute deadline 依次等待；
5. 超时 runs 标记 Interrupted 并持久化。

使用共享 deadline 而不是每个 run 各等 N 秒，保证总 shutdown 时长有上界。

---

## 59. Abort 与 CancellationToken 的选择

| 机制 | 特点 | 适用 |
| --- | --- | --- |
| Future drop/AbortHandle | 快、强制，不再运行 async cleanup body | Turn、debounce、可丢计算 |
| CancellationToken | 合作式，任务有机会保存状态和释放资源 | Workflow、relay、长期 loop |
| Actor Command | 由资源 owner 执行有序清理 | Terminal、ChatState、Persistence |
| OS Signal | 作用到外部进程/进程组 | Bash、server、测试进程 |

成熟运行时通常组合四者，而不是寻找一个万能 cancel primitive。

---

## 60. Cancel 的事件与 Usage 收敛

取消路径必须在 Turn Task 已无法正常回传时自行完成：

- `events.cancel_active_tool()`；
- `TurnEnded(Cancelled, MidTurnAbort)`；
- 记录 prior interrupt/redirect kind；
- 若无 dangling Tool Call，arm 下一轮 interrupt reminder；
- snapshot/finalize prompt usage；
- 发 durable replayable `TurnCompleted`；
- resolve running prompt RPC 为 Cancelled。

Send Now 抑制 prior-interrupt marker，因为用户是在继续，而不是结束互动。

---

## 61. 为什么 `TurnCompleted` 由 Completion 与 Cancel 共享 Chokepoint

`emit_turn_completed` 统一从同一 stop result 推导：

- stop reason；
- agent result；
- usage；
- cancel trigger meta。

正常完成由 `handle_completion` 调用；Cancel 在移除 front 后直接调用。Ownership gate 确保两条路径只有一条能提交同一 prompt 的 terminal。

---

## 62. Replay Buffer 必须先于 Terminal Event Flush

最后的 text/thought delta 可能仍在 merge buffer。如果先发 `TurnCompleted`，日志顺序会变成：

```text
TurnCompleted
last AgentThoughtChunk
```

客户端会先结束 Turn 又收到内容。Normal completion、Cancel、Shutdown 与 FlushComplete 都先 flush replay buffer，再发 terminal/ack。

---

## 63. Dangling Tool Call 与 Interrupt Reminder

Cancel 后，若 Conversation 中已有 Assistant ToolCall 但没有 ToolResult，下一轮 integrity repair 会补 cancelled result，这已经告诉模型上轮被打断。

只有没有 dangling Tool Call 时，源码才额外 arm `[Request interrupted]` reminder，避免重复注入两个相同信号。

---

## 64. Session Close/Delete 是 Hard Stop

`hard_stop_resident` 连续发送：

```text
Cancel {
  cancel_subagents: true,
  kill_background_tasks: true,
  trigger: SessionClose/Delete
}
Shutdown(CancelRunningTurn)
```

先 Cancel 负责业务终态和资源回收，后 Shutdown 让 Actor 完成 Session-end 流程并退出。Delete 还必须等待旧 actor thread，防止目录删掉后 Actor 又写回来。

---

## 65. `ShutdownKind::Graceful` 的含义

Graceful 不主动取消 running work，适合 idle unload、process quiesce 或某些 subagent teardown 场景；`CancelRunningTurn` 明确要求终止未完成 Turn。

注释中的“running work survives”必须结合调用者理解：Actor 返回后，能真正存活的是有独立 owner 的资源，例如 shared Terminal background task；Actor-owned Future 若无 owner 则会随 runtime scope drop。

---

## 66. Shutdown Workflow 的顺序

显式 Shutdown 分支：

1. cancel/drain Workflows；
2. flush replay buffer；
3. 必要时 cancel running turn；
4. drop queued synthetic items；
5. dispatch session_end hooks；
6. memory save/reindex；
7. dream consolidation；
8. cancel feedback sync loop；
9. feedback manager final sync/upload drain；
10. persist background task manifest；
11. cleanup scratch；
12. return Actor loop。

这是有界 shutdown protocol，不是简单 drop Arc。

---

## 67. Workflow Persistence Flush

`shutdown_workflows` 最多等 Workflow 7 秒，随后向 Persistence Actor 发送 `FlushAndAck`，再最多等 2 秒。

即使有 run timeout，也将其标记 Interrupted，尽量先持久化不可安全 resume 的事实。Timeout 只限制关机等待，不代表悄悄把 run 当 Completed。

---

## 68. Session Actor Channel 关闭路径

若 `cmd_rx.recv()` 返回 None，说明所有 command senders 已 drop。源码仍执行 SessionEnd hook、Memory、Feedback、Workflow 与 scratch cleanup 的相应路径。

Channel 关闭是生命周期事件，不应被当成“反正进程要退出，什么都不做”。否则 leader mode 卸载单个 Session 时会丢掉 session-scoped cleanup。

---

## 69. Timeout 是 Policy，不是 Cancellation 本身

`tokio::time::timeout(future)` 超时后会 drop inner Future；但内层若已把工作交给 Actor、线程或 OS 进程，外层 drop 不会自动撤销那些资源。

所以每个 timeout 都必须问：

1. 被 drop 的 Future 是否拥有全部工作？
2. 是否需要显式 Cancel command？
3. 是否需要 process signal？
4. 迟到结果如何识别和丢弃？
5. timeout 后状态是 Failed、Interrupted 还是 Backgrounded？

---

## 70. `tokio::spawn` 默认不是结构化并发

Drop JoinHandle 不会自动 abort Tokio task；task 默认 detached。源码因此使用：

- `AbortOnDrop`；
- `TaskSlot`；
- CancellationToken；
- completion/done oneshot；
- explicit shutdown manager。

每次看到 `spawn` 都应追问：谁保存 handle、谁负责 cancel、谁 drain 结果、parent 退出后是否允许 child 存活。

---

## 71. 这套运行时的核心 Ownership Tree

```text
MvpAgent / leader
  └── SessionHandle + SessionActor supervisor
        ├── running AgentTask
        │     ├── sampler future
        │     ├── tool drainer guard
        │     └── prompt-scoped subagent requests
        ├── prompt queue + interjection buffer
        ├── WorkflowManager
        │     └── run token + done receiver + run-owned children
        └── ToolBridge
              └── shared TerminalBackend
                    ├── foreground processes by owner
                    └── background processes by owner
```

取消范围应沿 ownership edge 传播，而不是按“当前能找到哪些 ID”随意广播。

---

## 72. 常见误读一：Abort Turn 会自动停止所有 Tool

只对纯 Rust async computation 接近成立。Terminal process、remote MCP request、Subagent、Workflow 都可能有独立 owner。必须查看各自 cancellation path。

---

## 73. 常见误读二：后台任务就是 detached task

产品语义的 Background Task 是 TerminalActor 管理的可查询任务，有 task ID、output artifact、timeout、owner 和 completion notification。

随手 `tokio::spawn` 一个 Future 只是实现层 detached task，不自动具备这些生命周期能力。

---

## 74. 常见误读三：并发 Tool Result 必须按声明顺序回填

Tool Call 与 Result 通过 call ID 关联，不必为了顺序让快 Tool 等慢 Tool。真正需要保持的是 Conversation schema、终态和共享状态归并不变量。

若 provider 明确要求 positional ordering，则应在 canonicalization 层按 call order 重排，而不是牺牲所有执行并发。

---

## 75. 常见误读四：Ctrl+C 应清空全部 Queue

Ctrl+C 针对 running Turn；已排队的真实用户 prompt 是独立请求。清空它们会丢数据并让客户端镜像失真。

Hard Close/Delete 才有清空整个 queue 的授权和必要性。

---

## 76. 常见误读五：Idle 等于没有后台工作

Session `IdleResident` 只表示没有 running Turn。后台 terminal、monitor、workflow、upload、memory flush 都可能仍在运行。

Roster 因此还需要 background-work breakdown，不能只看 `current_prompt_id`。

---

## 77. 一次正常 Turn 的并发时间线

```text
Actor promotes queue front
  -> spawn_local AgentTask
  -> Actor returns to mailbox

AgentTask samples
  -> receives tool calls
  -> serial prepare
  -> concurrent dispatch
  -> serial post-flight
  -> possibly resample
  -> sends completion

Actor receives completion
  -> flush buffered deltas
  -> ownership check/pop front
  -> durable terminal
  -> promote next prompt
```

---

## 78. 一次 Ctrl+C 时间线

```text
ACP cancel received
  -> dispatch lock
  -> SessionCommand::Cancel
  -> flush replay buffer
  -> clear interjections
  -> suppress task wakes
  -> abort subagent producer + sweep children
  -> kill foreground processes
  -> preserve real queued prompts
  -> emit cancelled events/usage/terminal
  -> abort AgentTask
  -> resolve front RPC
  -> keep actor resident and idle
```

---

## 79. 一次 Send Now 时间线

```text
new prompt enters queue behind pinned front
  -> mark send_now
  -> flush replay buffer
  -> cancel current foreground work
  -> do not arm interrupt reminder
  -> keep background/subagents
  -> resolve old prompt Cancelled(send_now)
  -> promote send-now prompt
```

---

## 80. 一次 Session Close 时间线

```text
lock intake
  -> verify handle not superseded
  -> hard Cancel(close, kill bg + subagents)
  -> Shutdown(CancelRunningTurn)
  -> remove resident handle / mark completed
  -> bounded drain actor thread
  -> finalize remote replica
```

Delete 类似，但重点是 drain 后再删目录，且不把 remote replica 当普通 close finalize。

---

## 81. 测试应固定的并发不变量

1. 一次只能 promote 一个 running task；
2. await gap 后 double-check 阻止 double spawn；
3. stale completion 不清新 Turn 的 task；
4. Cancel 始终 resolve running/front RPC；
5. Ctrl+C 保留 queued user prompts；
6. hard shutdown 清 queued prompts 与 background tasks；
7. Send Now prompts 保持 FIFO；
8. Interjection 在 idle race 下转 fallback，不丢失；
9. interruptible wait 停止等待但不杀 background object；
10. tool drainer 随 parent drop abort；
11. 同一路径 writes 不并发；
12. subagent owner cleanup 不误杀 parent/sibling processes；
13. SIGTERM 超时后升级 SIGKILL；
14. killed child 未 reap 时不能标 settled；
15. workflow shutdown 使用总 deadline 并标 Interrupted；
16. replay delta 必须先于 TurnCompleted。

---

## 82. 排障：点击取消但界面一直 Cancelling

按顺序查：

1. 是否记录 `shell.cancel.received`；
2. 是否记录 `shell.cancel.processing`；
3. cancel 落到哪个 prompt ID；
4. Terminal kill 是否卡在 bounded wait；
5. queue front 的 respond_to 是否被 resolve；
6. `TurnCompleted` 是否发出；
7. 是否有 stale completion 警告；
8. gateway/prompt_complete 是否送达客户端。

Received 有、Processing 无，通常是 dispatch/mailbox 问题；Processing 有、Terminal 无，通常是 cleanup/ownership 问题。

---

## 83. 排障：取消后命令仍在运行

检查：

- 命令是否已 auto-background；
- 本次 Cancel 是否 `kill_background_tasks=false`；
- process 的 owner_session_id；
- 是 direct child 还是 escaped process group；
- TerminalActor 是否仍 resident；
- kill 使用 PID 还是 PGID；
- child 是否处于 D-state，reap 是否 timeout。

“Turn 已结束”本身不能证明 Background task 应被杀。

---

## 84. 排障：取消后又自动开始一轮

检查：

- trigger 是 Ctrl+C 还是 Esc/Send Now；
- task-wake gate 与 state suppression；
- queue 中是否已有真实用户 prompt；
- completion notification 是否在 Cancel 前已 admission；
- Goal/Workflow completion 是否合成 prompt；
- `maybe_start_running_task` 在 Cancel 后是否正确 promote preserved queue。

这可能是预期的 queued user prompt，而不是取消失败。

---

## 85. 排障：Tool 并发导致历史损坏

检查：

- 每个 Result 是否使用 call ID，而非 completion position；
- `approved_slots[idx]` 是否恰好消费一次；
- 同一路径 write lock key 是否正确提取；
- deferred followups 是否等 batch 后统一追加；
- early PermissionReject 是否为剩余 calls 补 Result；
- post-flight hook 是否在正确 Tool identity 上运行。

---

## 86. 新增异步任务的设计清单

1. 任务属于 Session、Turn、Tool、Workflow 还是进程？
2. 是否允许 parent 结束后继续？
3. 谁保存 JoinHandle/AbortHandle/token？
4. 正常结果回到哪个 ownership gate？
5. 迟到结果如何识别？
6. Cancel 是 cooperative 还是 forced？
7. 有无 OS/remote side effect 需要显式撤销？
8. 所有 await 是否有界？
9. 是否需要 drain、flush、reap？
10. 用户可见终态由谁且只由谁提交？
11. owner scope 是否会误伤 sibling？
12. shutdown 总时间是否有 absolute deadline？

---

## 87. 新增 Cancel Trigger 的设计清单

不要只往 enum 加名字，还要决定：

- 是否计入 user cancellation metric；
- 是否 cancel subagents；
- 是否 kill background tasks；
- 是否抑制 task wakes；
- 是否清 pending interjections；
- 是否保留 queued user prompts；
- 是否 arm interrupt reminder；
- 是否 pause Goal；
- terminal meta 暴露什么 trigger；
- 是否属于 rewind/replacement 而非 cancellation。

---

## 88. 推荐源码阅读顺序

```text
第一遍：commands.rs -> run_loop.rs select arms
第二遍：prompt_queue.rs -> notification_drain::maybe_start_running_task
第三遍：tasks_cancel.rs -> turn_end.rs
第四遍：tool_calls.rs batch -> tool_dispatch.rs
第五遍：terminal.rs -> lifecycle.rs
第六遍：interjection.rs -> notification bridge
第七遍：subagent coordinator -> workflow manager
```

---

## 89. 练习题

1. 为什么 Actor 不能直接 await 整个 `handle_prompt`？
2. 为什么 running prompt 留在 queue front？
3. `current_prompt_id` 与 `running_task` 哪个更适合 queue 编辑判断？
4. Abort 后为什么仍可能收到 completion？
5. 为什么 Completion 必须检查 front ownership？
6. 为什么 Cancel 要先 kill Terminal 再 abort Turn？
7. Ctrl+C 为什么保留真实 queued prompts，却清 synthetic wakes？
8. Interjection 为什么只打断 wait，不杀被等待 task？
9. `FuturesUnordered` 为什么还返回 original index？
10. Workflow 为什么使用 token，而 Turn 使用 AbortHandle？
11. `Finished` 为什么不一定 `Settled`？
12. Session Close 为什么要 Cancel 再 Shutdown？

---

## 90. 总结

Grok Build 的 Concurrency Runtime 可以概括为：

```text
串行控制面
  SessionActor 决定 queue、ownership、terminal

并发数据面
  sampler、tool、process、subagent、workflow 分层执行

范围化取消
  Turn / owner / prompt / session / workflow run 各有边界

有序收敛
  flush delta -> cleanup resources -> finalize usage -> one terminal
```

真正可靠的取消不是“让 Future 尽快停止”，而是让每个资源 owner 都得到正确指令，让仍应存活的后台工作不被误杀，让被取消的 RPC 都有响应，让迟到结果失去提交权，并让客户端、Conversation、事件日志与进程表最终同意同一个结果。

---

## Glossary：本篇术语表

| 名词 | 白话解释 | 在本篇中的具体含义 |
| --- | --- | --- |
| Concurrency | 并发 | 多项工作在时间上交错推进，不一定多线程同时执行 |
| Parallelism | 并行 | 多项工作真正同时占用不同执行资源 |
| Runtime | 运行时 | 管理任务、队列、I/O、取消和生命周期的执行层 |
| Control Plane | 控制面 | 决定顺序、所有权、策略与终态的 SessionActor |
| Data Plane | 数据面 | 执行采样、Tool、进程等实际工作的并发任务 |
| Actor | Actor | 独占状态、通过 mailbox 串行处理命令的任务 |
| LocalSet | 本地任务集合 | Tokio 中允许运行 `!Send` Future 的单线程调度范围 |
| `spawn_local` | 本地 spawn | 在当前 LocalSet 启动无需 `Send` 的异步任务 |
| `tokio::spawn` | Tokio spawn | 启动必须 `Send`、可由多线程 runtime 调度的任务 |
| Future | Future | 尚未完成、可被 poll 推进的异步计算 |
| JoinHandle | 任务句柄 | 等待或控制 spawned task 的所有权对象 |
| AbortHandle | 中止句柄 | 请求 Tokio 停止并 drop 某个任务的句柄 |
| CancellationToken | 取消令牌 | 可克隆的合作式取消信号，任务需主动观察 |
| Cooperative Cancellation | 合作式取消 | 任务收到信号后自行运行清理并退出 |
| Forced Cancellation | 强制取消 | 直接 abort/drop Future，不再执行其普通 async 清理代码 |
| Structured Concurrency | 结构化并发 | 子任务生命周期被绑定在清晰的父 scope 内 |
| Detached Task | 脱离任务 | Parent 不再持有或等待，但任务仍可继续执行 |
| RAII Guard | RAII 守卫 | 创建时设置状态、Drop 时无条件清理的对象 |
| `AgentTask` | Turn 任务 | SessionActor 为一次 prompt spawn 的独立 Future |
| `TaskSlot` | 单任务槽 | 保存至多一个 JoinHandle，新任务会 abort 旧任务 |
| Completion Channel | 完成通道 | AgentTask 把 prompt ID 与结果送回 Actor 的 Channel |
| Ownership Gate | 所有权闸门 | 检查某结果是否仍有权提交状态的条件 |
| Stale Completion | 迟到完成 | Turn 已取消/替换后才到达 Actor 的旧结果 |
| Prompt Queue | Prompt 队列 | Server 维护的权威待运行输入 FIFO |
| Pinned Front | 固定队首 | 正在运行、不可删除或重排的 queue front |
| Promotion | 提升运行 | 把 queue front 标为 running 并 spawn AgentTask |
| Double-check | 二次检查 | await 释放锁后重新验证业务前提 |
| Dispatch Lock | 分发锁 | 序列化同 Session 外部入口与 handle 切换的锁 |
| Cancellation Scope | 取消范围 | Cancel 应传播到 Turn、prompt、owner、session 或 run 的边界 |
| Cancel Trigger | 取消触发源 | Esc、Ctrl+C、Send Now、Close 等取消来源 |
| Hard Stop | 硬停止 | 同时取消 Turn、Subagent、后台任务并关闭 Actor |
| Soft Cancel | 软取消 | 停止当前 Turn，但保留 Session 与部分后台工作 |
| Send Now | 立即发送 | Cancel 当前 Turn，并把新 prompt 放到下一执行位 |
| Interjection | 中途插话 | 不结束 Turn，在安全点注入一条用户 steering 消息 |
| Side Question | 旁路问题 | 快照主上下文做独立模型调用，不修改主 Conversation |
| Fallback Prompt | 兜底 Prompt | 无 active Turn 可接收 Interjection 时创建的独立 Turn |
| Blocking Wait | 阻塞式等待工具 | 等后台 task/subagent 完成、可能长时间不返回的 Tool |
| `BlockingWaitGuard` | 等待深度守卫 | 记录当前 Turn 是否卡在可打断 wait 中 |
| Tool Batch | 工具批次 | 模型一次响应中产生的一组 Tool Calls |
| Prepare | 准备阶段 | Parse、Hook、Permission、Policy 等串行前置步骤 |
| Dispatch | 分发阶段 | 真正并发执行已批准 Tool 的阶段 |
| Post-flight | 执行后阶段 | 处理结果、事件、Hook、Conversation 与统计的阶段 |
| `FuturesUnordered` | 无序 Future 集 | 按完成先后产出并发 Future 结果的集合 |
| Single-flight | 单航班 | 多个并发请求共享一次初始化或恢复操作 |
| Path Lock | 路径锁 | 防止同一文件上的并发写互相覆盖的 Mutex |
| `AbortOnDrop` | Drop 中止守卫 | Owner 被 drop 时自动 abort child task 的包装 |
| TerminalActor | 终端 Actor | 独立管理所有命令进程、输出、等待与回收的 Actor |
| Foreground Process | 前台进程 | 当前 Tool 正等待其结果的命令 |
| Background Task | 后台任务 | Tool 已返回但进程继续、可用 task ID 查询的命令 |
| Auto-background | 自动转后台 | 超过前台等待预算后让进程继续但解除 Tool 阻塞 |
| Foreground Block Budget | 前台阻塞预算 | 允许命令占住当前 Tool 的最长交互时间 |
| Command Timeout | 命令超时 | 命令请求配置的终止或转后台期限 |
| Process Group | 进程组 | 可一次向 shell 命令及其子孙发送 signal 的 OS 分组 |
| Owner Session | 所有者 Session | 用于隔离 parent、subagent、sibling 进程清理的 ID |
| SIGTERM | 温和终止信号 | 允许进程自行清理并退出的 Unix signal |
| SIGKILL | 强制终止信号 | 由内核立即终止进程、无法捕获的 Unix signal |
| Reap | 回收进程 | 调用 wait 获取退出状态并释放 zombie 资源 |
| Zombie | 僵尸进程 | 已退出但 parent 尚未 wait 回收的进程条目 |
| Lifecycle | 生命周期状态机 | Running→Exiting→Finished→Swept 的 Terminal 状态 |
| Settled | 完全收敛 | 输出已完成且 child 已被 wait/reap |
| Partial Output | 部分输出 | Cancel/Timeout 前已经产生、仍应保留的 stdout/stderr |
| Task Wake | 任务唤醒 | 后台完成后自动合成 Prompt 让 Agent 处理结果 |
| Wake Suppression | 唤醒抑制 | Ctrl+C 后暂时禁止后台完成立即启动新 Turn |
| Synthetic Prompt | 合成 Prompt | 由 task/goal/workflow 等系统事件创建的非真人输入 |
| Reservation | 预留 | 防止同一 completion 被多个唤醒路径重复消费的状态 |
| Subagent Scope | 子 Agent 范围 | 按 prompt、session 或 workflow run 归属的 child 集合 |
| Spawn Admission | 启动准入 | 决定新的 Subagent 请求是否仍允许进入 coordinator |
| Workflow Run | 工作流运行 | 拥有 cancel token、children 与 done ack 的长期编排任务 |
| Drain | 排空 | 等待已取消任务完成清理或把缓冲事件搬到安全位置 |
| Absolute Deadline | 绝对截止时间 | 多个等待共享同一最终时刻，限制总耗时 |
| Replay Buffer | 回放缓冲 | 合并 Session Update chunk、需在 terminal 前 flush 的缓冲 |
| Terminal Event | 终态事件 | 表示 Turn 已 Completed/Cancelled/Failed 的唯一结束信号 |
| Chokepoint | 汇聚点 | 多条完成路径共同调用、保持语义一致的唯一函数 |
| Dangling Tool Call | 悬空工具调用 | Conversation 中有 ToolCall 但还没有对应 ToolResult |
| Interrupt Reminder | 中断提醒 | 下一轮告诉模型上次请求被用户打断的 system reminder |
| Idle Resident | 驻留空闲 | SessionActor 在内存，但没有 running Turn |
| Shutdown Protocol | 关机协议 | 有序取消、排空、持久化、Hook、Memory、Feedback 和退出流程 |
| TOCTOU | 检查使用竞态 | 检查条件后到实际操作前，状态被其他并发路径改变 |
