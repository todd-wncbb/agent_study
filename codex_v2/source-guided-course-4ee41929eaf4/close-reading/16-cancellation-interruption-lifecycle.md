# Cancellation 与 interruption lifecycle：用户按下停止以后，Codex 怎样真正停下来

> 源码基线：`4ee41929eaf4`  
> 本篇重点：`Op::Interrupt`、`turn/interrupt`、`CancellationToken`、`RunningTask`、`abort_all_tasks`、`TurnAborted`、`steer_input`、pending waiters、background terminal、`Op::Shutdown`。  
> 建议先读：[run_turn](04-turn-main-loop.md)、[Unified exec](06-unified-exec.md)、[Tool output 与 context feedback](15-tool-output-context-feedback.md)。

## 1. 先说人话：点击“停止”不是把一切瞬间抹掉

假设 Codex 正在做这些事情：

```text
模型正在流式回答
→ 模型请求运行测试
→ 测试进程正在输出日志
→ 另一个工具正在等待用户审批
→ 部分消息已经写入 conversation 和 rollout
```

此时用户点击“停止”。系统不能简单地把内存中的任务对象删掉，因为：

- 模型网络流可能仍在返回数据；
- 工具可能已经产生文件或进程副作用；
- 等待审批的 `oneshot::Receiver` 仍然悬挂；
- UI 需要收到一个明确终态；
- rollout 需要说明这次 Turn 为什么没有正常完成；
- 新输入可能已经被追加到当前 Turn；
- 后台 terminal 可能是用户有意保留的长进程。

因此，停止是一段生命周期，不是一个瞬时动作。

## 2. 本篇最重要的五个区别

先记住下面五组区别，后面源码会反复用到。

| 容易混淆的概念 | 实际区别 |
|---|---|
| 请求取消 / 已经取消 | `cancel()` 只是发出通知；收到 `TurnAborted` 才表示 Turn 已进入取消终态 |
| interrupt / steer | interrupt 结束当前 Turn；steer 把新输入加入同一个正在运行的 Turn |
| interrupt / shutdown | interrupt 停当前工作；shutdown 拆除整个 Session runtime |
| Turn 内前台执行 / 后台 terminal | interrupt 会取消当前工具路径，但不会自动清理已脱离 Turn 的后台 terminal |
| cooperative cancellation / task abort | 前者让代码自行观察 token 并收尾；后者直接停止 Tokio task 的继续 poll |

## 3. 贯穿案例

用户发出：

```text
请修改配置并运行测试。
```

Codex 的 Turn `turn-42` 正在执行：

```text
Response stream
  └─ tool call: exec_command("just test -p codex-core")
       ├─ command 正在运行
       └─ approval-A 正在等待决定
```

这时可能发生三种不同操作。

### 3.1 用户 steer

```text
先只运行 config 相关测试。
```

结果：输入进入 `turn-42` 的 pending input；当前 Turn 不被取消。下一次 sampling 会看到它。

### 3.2 用户 interrupt

结果：`turn-42` 的 cancellation token 被取消；模型流、工具和等待点开始退出；最终发出：

```text
TurnAborted {
    turn_id: "turn-42",
    reason: Interrupted,
}
```

### 3.3 应用 shutdown Session

结果：先取消活动 Turn，再终止后台进程、MCP runtime、Code Mode、Guardian review session，运行 SessionEnd hook，关闭持久化，最后发出 `ShutdownComplete`。

## 4. 一句话全景

```text
用户按停止 / client 调用 turn/interrupt
→ UI 或 App-server 产生 Op::Interrupt
→ Session submission_loop 分派 interrupt
→ Session::interrupt_task
→ abort_all_tasks(Interrupted)
→ 从 active_turn 取走 RunningTask
→ cancellation_token.cancel()
→ 最多等待 100ms 协作退出
→ AbortOnDropHandle::abort()
→ task-specific abort hook
→ 写入 interrupted history marker（按配置）
→ flush rollout
→ EventMsg::TurnAborted
→ App-server 映射成 turn/completed(status=interrupted)
→ interrupt RPC 才得到成功响应
```

## 5. 公开协议与内部实现的边界

[OpenAI Codex CLI 官方页面](https://learn.chatgpt.com/docs/codex/cli)介绍了 Codex CLI 的公开使用界面，但没有承诺固定提交内部采用哪一种 Tokio task、token 或等待时序。

本篇关于以下内容的结论均来自固定提交源码：

- 100ms 的 graceful interruption 等待；
- `CancellationToken` 的父子关系；
- pending approval 的清理顺序；
- App-server 何时响应 `turn/interrupt`；
- interrupted history marker 的写入与 flush；
- shutdown 的资源拆除次序。

这很重要：公开产品行为可以稳定，而内部实现细节可以继续演进。

## 6. 入口一：TUI 的停止键最终产生 `Op::Interrupt`

固定提交的默认 `chat.interrupt_turn` 键位是 `Esc`。TUI 不会看到任何 `Esc` 都直接停止任务：

- 弹窗打开时，`Esc` 可能只关闭弹窗；
- history search 活跃时，`Esc` 可能只取消搜索；
- 正在输入特殊命令时，按键可能由当前 view 消费；
- 只有路由完成后，未被更具体组件处理的停止动作才产生 `Op::Interrupt`。

所以可把 TUI 输入路由理解为：

```text
按键
→ 当前局部 view 是否消费？
→ composer 局部状态是否消费？
→ 当前是否有可取消工作？
→ AppEvent::CodexOp(Op::Interrupt)
```

这解释了为什么“按了 Esc”不必然等于“Turn 被取消”。按键只是可能的交互入口，`Op::Interrupt` 才是 Core 能识别的控制操作。

## 7. 入口二：App-server 的 `turn/interrupt`

App-server 接收：

```json
{
  "method": "turn/interrupt",
  "id": 31,
  "params": {
    "threadId": "thr_123",
    "turnId": "turn_42"
  }
}
```

它不会只按 `threadId` 模糊停止，而会校验 `turnId`。

## 8. 为什么必须带 `turnId`

考虑这个竞态：

```text
客户端认为 turn-A 仍在运行
服务器上 turn-A 已结束
turn-B 已经开始
客户端迟到的停止请求到达
```

如果请求只有 thread ID，服务器可能错误取消 turn-B。

固定提交会比较请求中的 `turnId` 与活动 Turn：

```text
匹配       → 可以提交 interrupt
不匹配     → invalid request
已是终态   → no active turn to interrupt
```

这是一种 generation fencing：旧世代请求不能误伤新世代任务。

## 9. startup interrupt 是特殊情况

`turn_id.is_empty()` 被视为 startup interrupt。

启动期可能还没有真正的 Turn，因此：

- 没有 `TurnAborted` 可等待；
- `Session::interrupt_task` 在没有 active turn 时调用 `cancel_mcp_startup()`；
- App-server 在成功提交 `Op::Interrupt` 后直接响应。

普通 Turn interrupt 则不能这样做，因为客户端需要知道具体 Turn 确实完成取消。

## 10. `Op::Interrupt` 的协议注释已经说明边界

`Op` 中的注释说明：

```text
Abort current task without terminating background terminal processes.
This server sends EventMsg::TurnAborted in response.
```

可翻译为：

```text
停止当前任务，但不终止后台 terminal 进程；
服务器随后发送 TurnAborted。
```

这不是实现偶然，而是协议明确表达的产品语义。

## 11. `submission_loop` 是控制操作的统一分派点

Session 有一条 submission channel。`submission_loop` 持续接收 `Submission`，按 `Op` 变体分派：

```rust
match sub.op.clone() {
    Op::Interrupt => {
        interrupt(&sess).await;
        false
    }
    // ...
    Op::Shutdown => shutdown(&sess, sub.id.clone()).await,
}
```

`false` 表示处理完 interrupt 后 submission loop 继续存在；`Shutdown` 返回 `true` 才会跳出循环。

所以 interrupt 不等于 Session 死亡。

## 12. `interrupt` handler 很薄

Core handler 只是：

```rust
pub async fn interrupt(sess: &Arc<Session>) {
    sess.interrupt_task().await;
}
```

薄 handler 的好处是：

- 协议分派只负责选择动作；
- 真正的状态所有权留在 `Session`；
- TUI、App-server、子 Agent 控制都能复用相同 Core 语义。

## 13. `Session::interrupt_task`

其核心逻辑可写成：

```text
记录日志
→ 先观察是否存在 active turn
→ abort_all_tasks(Interrupted)
→ 如果原本没有 active turn，则取消 MCP startup
```

这里先保存 `had_active_turn`，是为了区分：

- 取消运行中的 Turn；
- 取消尚未进入 Turn 的启动工作。

## 14. 为什么函数名叫 `abort_all_tasks`

当前 `ActiveTurn` 的主要执行对象是一个 `RunningTask`，但 Session 还维护与这个 Turn 绑定的：

- approval waiters；
- request-user-input waiters；
- permission waiters；
- MCP elicitation waiters；
- dynamic tool waiters；
- pending input；
- task-specific cleanup。

所以取消不是只对一个 `JoinHandle` 调 `abort()`。它需要清理整组 Turn-owned 工作。

## 15. `ActiveTurn`、`RunningTask` 和 `TurnState`

三者职责不同。

### 15.1 `ActiveTurn`

```text
当前活动 Turn 的槽位
├─ task: Option<RunningTask>
└─ turn_state: Arc<Mutex<TurnState>>
```

### 15.2 `RunningTask`

保存真正运行任务需要的 owner：

```text
done                    完成通知
kind                    Regular / Review / Compact
task                    dyn AnySessionTask
cancellation_token      协作取消信号
handle                  Tokio task handle
turn_context            Turn 身份和配置
input_persisted         输入持久化确认
agent execution guard   Agent 执行计数 guard
timer                   Turn 生命周期计时器
```

### 15.3 `TurnState`

保存 Turn 期间可变的协调状态：

```text
pending approvals
pending permission requests
pending user-input requests
pending MCP elicitations
pending dynamic tools
pending steer input
mailbox delivery phase
tool call count
token usage baseline
```

## 16. 为什么 task 和 state 分开

任务 Future 负责“正在执行什么”，TurnState 负责“外部响应应该送到哪里”。

例如审批：

```text
工具 Future 等待 rx_approve
TurnState 用 approval_id 保存 tx_approve
客户端批准时，通过 approval_id 取出 sender
```

取消时，即使任务 Future 已经收到 token，也必须清理 TurnState 里的 sender，才能让等待方退出。

## 17. 每个 Turn 创建独立 `CancellationToken`

`start_task` 创建：

```rust
let cancellation_token = CancellationToken::new();
```

随后运行任务时传入 child token：

```text
RunningTask 保存父 token
  └─ spawned task 获得 child_token
       └─ run_turn / tool 获得更深的 child_token
```

取消父 token 会让所有后代 token 都进入 cancelled 状态。

## 18. 为什么使用 token 树

一个 Turn 内可能有多层异步工作：

```text
Turn token
├─ model sampling token
├─ tool dispatch token
│  ├─ shell execution token
│  └─ network approval token
└─ code mode delegate token
```

如果每层都手工保存所有 `JoinHandle`，很容易遗漏。父子 token 让取消信号可以向下广播，同时每层仍能决定自己的清理方式。

## 19. `cancel()` 是同步通知，不是等待完成

调用：

```rust
task.cancellation_token.cancel();
```

只意味着：

```text
从现在起：
is_cancelled() == true
cancelled().await 可以被唤醒
child token 也会观察到取消
```

它不意味着：

- OS 子进程已经退出；
- 文件写入已经回滚；
- 工具 handler 已经释放资源；
- `TurnAborted` 已经发送；
- UI 已经可以安全启动依赖终态的新操作。

## 20. 第一阶段：协作式取消

`handle_task_abort` 先发送 token，然后等待：

```rust
select! {
    _ = task.done.notified() => {},
    _ = sleep(100ms) => { /* warn */ }
}
```

这是给正常取消路径的短暂机会。

模型流、工具 runtime 和其他等待点如果及时观察 token，就可以：

- 停止继续接收；
- 关闭子进程；
- 生成 aborted tool output；
- 释放锁和 guard；
- 通知 `done`。

## 21. 为什么只等 100ms

固定提交常量是：

```rust
const GRACEFULL_INTERRUPTION_TIMEOUT_MS: u64 = 100;
```

这是响应速度和温和收尾之间的折中：

- 无限等待会让停止按钮看起来失效；
- 完全不等待会跳过大量正常清理机会；
- 100ms 后仍可使用更强的 task abort 兜底。

这里的 100ms 是固定提交实现细节，不应被客户端当作协议 SLA。

## 22. 第二阶段：强制 abort Tokio task

等待结束后执行：

```rust
task.handle.abort();
```

它要求 Tokio 不再继续 poll 该 task。Future 被 drop 时，其拥有的普通 Rust 资源会按 RAII 规则释放。

但要注意：task abort 不是数据库事务回滚，也不是撤销已经完成的外部副作用。

## 23. 为什么两阶段优于只 `abort()`

如果直接 abort：

- 工具可能来不及通知底层 runtime；
- 进程 teardown 可能被中途切断；
- terminal outcome 可能缺失；
- 日志和持久化可能处于难解释状态。

如果只依赖合作：

- 某段代码可能没有检查 token；
- 外部库可能永久等待；
- 用户无法及时拿到终态。

两阶段组合提供：

```text
正常路径尽量优雅
+
异常路径保证有界
```

## 24. task-specific `abort` hook

强制 abort handle 后，Core 仍调用：

```text
session_task.abort(session, turn_context).await
```

`SessionTask` 的不同实现可以做自己的语义清理。

这说明 task handle 的取消与 workflow 的取消不是同一层：

- handle 管 Future 是否继续执行；
- task abstraction 管这类工作还需要怎样收尾。

## 25. 模型 stream 怎样观察取消

建立 stream 时使用：

```text
client_session.stream(...).or_cancel(&cancellation_token)
```

读取每个 stream event 时再次使用：

```text
stream.next().or_cancel(&cancellation_token)
```

如果取消赢得竞态，代码返回：

```text
CodexErr::TurnAborted
```

## 26. 为什么建立连接和读取事件都要可取消

有两种不同的卡住位置：

```text
A. 还在等待模型请求建立/返回 response stream
B. 已经得到 stream，但在等待下一条 event
```

只在 B 检查 token，A 仍可能长期阻塞；只在 A 检查，长流读取仍无法及时停下。

## 27. `or_cancel` 是什么

可把它理解成通用包装：

```text
在原 Future 与 cancellation_token.cancelled() 之间 select
谁先完成就采用谁
```

它把许多普通 Future 提升为“可被 Turn 取消的 Future”。

## 28. 工具执行怎样观察取消

`ToolCallRuntime` 在工具 dispatch handle 与 cancellation token 之间 `select!`：

```text
工具先完成 → 使用真实工具结果
取消先到达 → 进入工具 abort 路径
```

但还要防一个竞态：取消信号到达时，工具可能刚刚产生真实终态。

## 29. `terminal_outcome_reached` 防什么

工具路径用 `AtomicBool` 记录终态是否已经被某一方取得。

竞争双方是：

```text
正常 handler 完成
取消路径生成 aborted response
```

目标是不让同一个 call 同时出现：

```text
Completed
和
Aborted
```

否则模型 history、UI item 和 telemetry 会互相矛盾。

## 30. 有的工具要等待 runtime 自己完成取消

当 `wait_for_runtime_cancellation` 为真时，取消路径不会立刻砍掉 dispatch：

```text
取消路径取得终态所有权
→ 等待 runtime 完成 process teardown
→ 忽略已取消的 join error
→ 构造 aborted response
```

这是因为某些 runtime 必须亲自结束进程或远端资源；直接 drop 外层 Future 不足以保证底层完成清理。

## 31. 其他工具可以直接 abort dispatch handle

如果不要求等待 runtime cancellation：

```text
dispatch_handle.abort()
→ 等待 handle 进入取消终态
→ 生成 aborted tool response
```

同一套工具框架允许具体 runtime 声明更适合自己的取消策略。

## 32. `AbortedToolOutput` 为什么仍然是 output

模型已经发出了 call：

```text
FunctionCall(call_id = call-9)
```

即使用户随后取消，也不能让 history 永远只有 call 没有 output。工具层会构造与它配对的 aborted response。

这能：

- 闭合 call/output 协议；
- 让 rollout 更容易恢复；
- 避免后续 normalization 猜测发生了什么；
- 让 telemetry 获得明确的 aborted outcome。

## 33. 取消中的输出 drain

一次模型 response 可能已经启动多个工具 Future。`try_run_sampling_request` 即使离开 stream loop，也会先：

```text
flush assistant text segments
→ drain_in_flight tools
→ 必要时发送 token count
→ 再检查 cancellation_token.is_cancelled()
→ 返回 TurnAborted
```

这与上一篇的 call/output 闭合不变量一致。

## 34. 为什么取消后还要 drain

“停止继续工作”和“丢弃所有已发生事实”不是一回事。

取消到来前已经发生的事实包括：

- 模型已经输出 tool call；
- 工具已经开始执行；
- 某些工具已经完成；
- 某些工具已经产生副作用；
- token usage 已由 completed response 记录。

drain 让这些事实以有界方式进入一致终态。

## 35. Shell timeout 与 cancellation 是不同终态

`ExecExpirationOutcome` 区分：

```text
TimedOut   配置的时间预算耗尽
Cancelled  cancellation token 被取消
```

`TimeoutOrCancellation` 使用 biased `select!`，优先检查 cancellation。

这避免同一时刻既到 deadline 又收到用户停止时，把用户动作错误显示成普通 timeout。

## 36. `cancel_when_either`

底层执行可能同时受两种取消源控制：

```text
Turn cancellation
runtime/network-specific cancellation
```

`cancel_when_either(first, second)` 创建组合 token：任一输入被取消，组合 token 就取消。

这相当于取消条件的逻辑 OR。

## 37. 审批等待为什么容易出错

命令审批通常这样工作：

```text
创建 oneshot channel
→ tx 按 approval_id 放入 TurnState
→ 向客户端发送 approval request
→ 工具 Future 等待 rx
```

如果取消时先删掉 sender，receiver 会结束。

代码通常会把 closed receiver 转成：

```text
ReviewDecision::Abort
```

如果工具 Future 还没观察到 cancellation，它可能把这个 Abort 当成一个模型可见的“用户拒绝工具”，而不是“整个 Turn 正在取消”。

## 38. 关键清理顺序：先让 task 观察取消，再清 waiters

源码注释明确说明：

```text
先 handle_task_abort
后 input_queue.clear_pending
```

原因是：

```text
让 interrupted task 先看到 cancellation
避免 pending approval 被删除后产生一个模型可见 rejection
然后再统一清除仍未退出的 waiter
```

这是一条非常值得记住的并发设计原则：清理 channel sender 的顺序会改变 receiver 对终态的解释。

## 39. `clear_pending_waiters` 清什么

`TurnState::clear_pending_waiters` 清除：

```text
pending_approvals
pending_request_permissions
pending_user_input
pending_elicitations
mcp_tool_approval_metadata
pending_dynamic_tools
```

map 中的 sender 被 drop 后，对应 receiver 被唤醒并得到 channel closed。

## 40. pending steer input 也会被清空

`InputQueue::clear_pending` 除了清 waiters，还执行：

```text
turn_state.pending_input.items.clear()
```

因为这些输入本来属于正在被取消的 Turn。Core 不能在没有明确产品规则时，把它们静默挪到另一个 Turn。

不同客户端可以在 UI 层保留可重新编辑的草稿，但那是客户端投影逻辑，不是 Core 把旧 Turn pending input 自动改绑到新 Turn。

## 41. `steer` 到底做什么

`Session::steer_input`：

```text
锁住 active_turn
→ 确认存在 RunningTask
→ 校验 expected_turn_id
→ 确认 TaskKind::Regular
→ 拒绝空输入
→ 把输入追加到 TurnState.pending_input
→ 发送 InputQueueActivity::Steer
→ 返回同一个 active turn ID
```

它没有取消 token，也没有创建新的 Turn。

## 42. 为什么 Review 和 Compact 不可 steer

`TaskKind` 分为：

```text
Regular
Review
Compact
```

固定提交只允许 Regular 接收 same-turn steering。Review 和 Compact 返回 `ActiveTurnNotSteerable`。

原因可以从状态模型理解：普通 Turn 本来就有多轮 sampling loop；Review/Compact 是更专门的 workflow，任意追加用户输入会破坏其输入与终态合同。

## 43. steer 何时被模型看到

外层 `run_turn` 在每轮 sampling 后计算：

```text
needs_follow_up = model_needs_follow_up || has_pending_input
```

随后下一轮 loop drain pending input、运行 user-prompt hooks、把输入记入 history，并构造下一次模型请求。

因此 steer 的语义不是“打断当前 token 流并立刻改写当前 response”，而通常是“让同一 Turn 在安全的下一采样边界吸收新输入”。

## 44. 一个 steer 时间线

```text
t0 模型开始 response-1
t1 用户发送 steer-S
t2 steer-S 进入 pending_input
t3 response-1 完成，已有文本/工具结果被记录
t4 run_turn 发现 has_pending_input = true
t5 drain steer-S 并记录为 user input
t6 发起 response-2
t7 模型在同一个 Turn 内根据 steer-S 调整工作
```

## 45. steer 为什么需要 admission 状态

客户端收到 `turn/steer` 成功响应时，希望知道消息不仅被 channel 接收，而且确实归属于某个 Turn，并最终写入 durable rollout。

`PendingUserMessageAdmissions` 跟踪：

```text
Immediate
WaitingForAdmission
Admitted(Started / Steered)
Persisted
```

只有 admission 与 persistence 条件满足，调用方才得到稳定成功。

## 46. 取消与 steer 的竞态

可能发生：

```text
steer 已归入 turn-42
但尚未持久化
→ turn-42 被取消
```

任务收尾调用 `complete_task_end(turn_id)`，把仍未完成的 admission 以 `TaskEndedBeforePersistence` 结束。

这避免客户端以为一条消息已经耐久保存，实际 rollout 中却没有它。

## 47. `spawn_task` 为什么先用 `Replaced` 取消旧任务

启动一个新的 SessionTask 前：

```rust
self.abort_all_tasks(TurnAbortReason::Replaced).await;
```

这处理的是“新 workflow 取代旧 workflow”，而不是用户主动停止。

因此 `TurnAbortReason` 不只有 `Interrupted`。

## 48. 四种 `TurnAbortReason`

固定提交定义：

| reason | 含义 |
|---|---|
| `Interrupted` | 用户或控制面主动中断 |
| `Replaced` | 新任务替换当前任务 |
| `ReviewEnded` | Review 生命周期结束导致终止 |
| `BudgetLimited` | 预算限制终止 Turn |

同样都是 `TurnAborted`，但 UI、Agent 状态和后续策略可能不同。

## 49. 为什么 reason 不能只是布尔值

如果只有：

```text
aborted = true
```

调用方无法判断：

- 是否展示“用户已停止”；
- 是否自动继续目标；
- 是否由预算耗尽导致；
- 是否是内部替换而非失败；
- 是否应写 interrupted history marker。

有限枚举让终态原因成为可穷尽分析的数据。

## 50. `handle_task_abort` 的幂等保护

函数开头检查：

```rust
if task.cancellation_token.is_cancelled() {
    return;
}
```

它防止同一个 `RunningTask` 被重复执行完整取消逻辑。

不过更强的保护来自 active task 所有权：`abort_all_tasks` 先把 active turn/task 从共享槽位取走，后续调用通常拿不到相同 task。

## 51. 为什么先从 active slot 取走

如果取消逻辑仍把 task 留在 `active_turn` 中，其他并发请求可能：

- 继续 steer 到一个正在死亡的 Turn；
- 再次取消同一任务；
- 把审批响应送进过期状态；
- 启动新 Turn 时误判仍有活动任务。

先取得 owner，再在锁外执行较慢 cleanup，是常见的异步状态机模式。

## 52. 中断标记为什么写进模型 history

如果只在 UI 显示“已停止”，后续恢复 conversation 时模型可能看见：

```text
用户要求修改
模型发出半截解释/工具调用
下一条用户消息突然改变方向
```

它不知道上一段是用户主动中断的。

配置允许时，Core 会写入 model-visible interrupted-turn marker，明确告诉后续模型不要把中断前的未完成意图当成已经完成。

## 53. V1/V2 multi-agent 的 marker role 不同

`InterruptedTurnHistoryMarker` 有：

```text
Disabled
ContextualUser
Developer
```

固定提交中：

- 配置关闭 → 不写 marker；
- MultiAgent V2 → developer message；
- 其他模式 → contextual user fragment。

这反映了不同上下文协议对控制信息 role 的选择。

## 54. 为什么 marker 要在 `TurnAborted` 前 flush

源码注释说明：某些客户端收到 abort event 后，会同步重新读取 rollout。

所以顺序是：

```text
记录 marker
→ flush rollout
→ 发送 TurnAborted
```

如果反过来，客户端可能在终态通知到达后立即读到一个还没有 interruption marker 的旧快照。

## 55. 终态 event 后为什么还要再 flush

普通 items 可能已经 flush，但 terminal event 自己刚刚追加。带缓冲的 writer 不一定因为再无后续数据而自动立刻落盘。

因此发送 `TurnAborted` 后再次显式 `flush_rollout()`。

这形成两个 durability barrier：

```text
barrier 1：marker 在终态通知前可见
barrier 2：terminal event 本身也尽快耐久
```

## 56. `TurnAbortedEvent` 携带什么

```text
turn_id
reason
started_at
completed_at
duration_ms
```

它不把取消当普通字符串错误，而是带身份、原因和时间的结构化终态。

## 57. 正常结束与取消结束

任务自然完成时，spawned task 调用 `on_task_finished`：

```text
成功或普通错误 → TurnComplete
CodexErr::TurnAborted → TurnAborted(Interrupted)
```

显式 `handle_task_abort` 路径会取消 token，并自己发送 `TurnAborted`。spawned task 退出时发现 task token 已 cancelled，就不会再调用 `on_task_finished`。

这是避免双终态的另一层保护。

## 58. `done` 通知的作用

spawned task 最后：

```text
done_clone.notify_waiters()
```

取消路径在 100ms 等待窗口中监听它。

注意 `done` 不是终态事件本身：

- `done` 是 Core 内部同步原语；
- `TurnAborted` 是对观察者的协议事件。

## 59. App-server 为什么把 interrupt request 暂存

普通 `turn/interrupt` 到达后，App-server：

```text
校验 turn ID
→ pending_interrupts.push(request_id)
→ submit Op::Interrupt
→ 暂时不发送 success response
```

直到 Core 发出 `TurnAborted`，事件处理器才调用：

```text
respond_to_pending_interrupts
```

## 60. 这实现了 acknowledgement barrier

客户端看到 `{result:{}}` 时，可以理解为：

```text
不是“服务器收到了停止意图”
而是“与该请求对应的 Turn 已进入 aborted 终态”
```

同时 App-server 还发出：

```text
turn/completed {
  status: "interrupted"
}
```

客户端仍应把 `turn/completed` 作为 Turn 生命周期的权威终态通知。

## 61. 多个 interrupt request 怎么办

`pending_interrupts` 是一个 request ID 列表。

同一取消终态到达时，App-server `mem::take` 整个列表，并逐个成功响应。

这样多个观察者或重复请求不需要各自触发一套 Core 取消；它们可以共享同一个 `TurnAborted` barrier。

## 62. 为什么响应前清理 pending server requests

收到 `TurnAborted` 时，App-server 先：

```text
abort_pending_server_requests()
```

这些可能包括：

- approval request；
- request_user_input；
- permission request；
- MCP elicitation。

它们都绑定当前 Turn。Turn 已终止后，客户端再回答没有意义，而且可能误投递到新的状态。

## 63. Core event 到 App-server 状态的投影

```text
EventMsg::TurnAborted
→ handle_turn_interrupted
→ TurnStatus::Interrupted
→ turn/completed notification
```

App-server 不把 interrupted 投影成 `failed`：

```text
completed    正常结束
failed       错误终止
interrupted  主动或控制性中止
```

这对 UI 和自动化调用者很重要。

## 64. interrupt 不会自动杀掉后台 terminal

官方 app-server README 在固定提交中明确写道：

```text
turn/interrupt does not terminate background terminals
```

原因是 unified exec 支持命令在 initial yield 后转为 Session-owned background process。

它已经不再只是某个 tool Future 的临时局部资源。

## 65. 前台命令和后台 terminal 的 owner 不同

```text
前台 tool execution
owner: 当前 Turn/tool runtime
取消 Turn: 应观察 token 并收尾

后台 terminal
owner: Session unified_exec_manager
取消 Turn: 默认保留
清理方式: CleanBackgroundTerminals / 单进程 terminate / Session shutdown
```

所以“停止回答”与“停止服务器”必须是两个明确动作。

## 66. 为什么保留后台 terminal 是合理的

用户可能让 Codex 启动：

```text
npm run dev
python -m http.server
日志 watcher
本地数据库
```

这些进程的目的就是跨多次 Turn 存活。如果每次用户停止模型思考都杀掉它们，长进程功能就无法使用。

## 67. `CleanBackgroundTerminals`

协议为这件事提供单独操作：

```text
Op::CleanBackgroundTerminals
→ Session::close_unified_exec_processes
```

App-server 也有 `thread/backgroundTerminals/clean`、list 与 terminate API。

显式分离使资源生命周期与用户意图对齐。

## 68. Session shutdown 比 interrupt 多做什么

`shutdown_session_runtime` 的主要顺序是：

```text
停止 startup prewarm
→ conversation.shutdown
→ abort_all_tasks(Interrupted)
→ terminate_all_processes
→ shutdown Code Mode service
→ stop MCP prewarm worker
→ close and shutdown MCP runtime
→ shutdown Guardian review session
→ run SessionEnd hooks
```

因此 shutdown 是整个 runtime teardown，而不只是当前 Turn 取消。

## 69. 为什么 shutdown 要先取消 active task

如果先拆 MCP、Code Mode 或 process manager，活动 Turn 可能仍在：

- 调用这些服务；
- 等待它们的响应；
- 创建新的子资源。

先阻止业务工作继续扩张，再拆底层依赖，能减少 teardown 竞态。

## 70. 为什么 shutdown 会终止后台进程

Session 即将消失，已经没有 owner 继续管理 background terminal：

- 无人读取输出；
- 无人接收 stdin；
- 无人执行 list/terminate；
- 进程可能泄漏。

所以 interrupt 保留后台进程，而 shutdown 必须清理它们。

## 71. SessionEnd hook 在哪里

资源 teardown 的后半段运行 SessionEnd hooks。固定提交文档说明这些 hooks 对 teardown 是 advisory，不能阻止 Session 关闭。

这避免扩展代码获得“永远不让进程退出”的权力。

## 72. 持久化为什么最后显式 shutdown

`shutdown` 在 runtime teardown 后：

```text
统计 turn count
→ emit thread stop lifecycle
→ live_thread.shutdown()
→ EventMsg::ShutdownComplete
```

持久化 writer 要完成 flush 与关闭，避免测试或下次 resume 读到未落盘尾部。

## 73. `ShutdownComplete` 是什么

`ShutdownComplete` 是 Session 级终态，不是 Turn 级终态。

对比：

| event | 说明 |
|---|---|
| `TurnComplete` | 一个 Turn 正常或带 terminal error 结束 |
| `TurnAborted` | 一个 Turn 因结构化 abort reason 结束 |
| `ShutdownComplete` | 整个 Session runtime 已完成 shutdown 流程 |

## 74. `shutdown_and_wait`

调用方提交 `Op::Shutdown` 后，还等待 `session_loop_termination`：

```text
submit shutdown
→ submission_loop 执行 teardown
→ loop 退出
→ termination signal 完成
```

这比只把 Shutdown 放入 channel 更强：调用方获得一个“Session loop 已结束”的 barrier。

## 75. App-server 怎样批量关闭 threads

`shutdown_all_threads_bounded`：

```text
snapshot 当前 threads
→ 并发调用每个 thread.shutdown_and_wait()
→ 每个 thread 最多等待给定 timeout
→ 分类 Complete / SubmitFailed / TimedOut
→ 只从 manager 移除已 Complete 的 threads
```

未完成的 thread 保留在 manager 中，调用方仍能重试或检查。

## 76. 为什么不是一超时就从 map 删除

从 map 删除只会丢失管理引用，不保证实际 runtime 已停止。

保留 timed-out thread 能避免把“管理器看不见了”误当成“资源已经释放”。

这是资源生命周期中非常重要的区别。

## 77. submission channel 意外关闭时怎么办

`submission_loop` 如果不是因为显式 `Op::Shutdown` 退出，也会运行一条 fallback teardown：

```text
shutdown_session_runtime
→ thread stop lifecycle
→ live thread persistence shutdown
```

这样 producer 意外消失时，不会完全跳过资源清理。

## 78. 取消不能撤销已经发生的副作用

假设 patch 工具已经写入第一个文件，用户才按停止：

```text
文件写入已经发生
→ cancellation 到达
→ 后续工作停止
```

`CancellationToken` 不能把文件自动恢复。

同理：

- 已发送的网络请求可能已经被服务器处理；
- 已启动的非受管外部动作可能已经发生；
- 已打印的日志不会消失；
- 已写入 rollout 的 item 不应被改写删除。

## 79. Cancellation safety 的真正问题

所谓 cancellation safety，不是“取消后世界回到从未执行”。它通常要求：

```text
取消发生在任意 await 点时
→ 状态仍然可解释
→ owner 不泄漏
→ 锁和 permit 被释放
→ 协议拥有唯一终态
→ 已发生副作用有记录
→ 后续操作不会误用旧世代状态
```

## 80. 哪些东西由 RAII 自动帮忙

Future 被 drop 时，普通 Rust owner 会 drop：

- Mutex/RwLock guard；
- Semaphore permit；
- channel sender/receiver；
- registration guard；
- timer guard；
- `AbortOnDropHandle` 包装的子任务。

但异步关闭通常不能只靠 `Drop`，因为 `Drop::drop` 不能普通地 `.await` 完成远端或进程 teardown。

## 81. 为什么显式 async shutdown 仍然必要

需要 await 的清理包括：

- 等待子进程退出；
- 向 MCP server 发送 shutdown；
- flush rollout writer；
- 等待 hook 的有界结果；
- 等待 Session task 的 termination barrier。

因此代码同时使用：

```text
RAII 作为保险
+
显式 async shutdown 作为完整路径
```

## 82. 常见误解一：steer 会立即打断当前回答

不准确。

固定提交把 steer 放入 pending input，在安全的 sampling 边界进入 history。当前 response 可能先自然到达自己的完成点。

如果用户真的要停止当前工作，应使用 interrupt。

## 83. 常见误解二：interrupt 成功回包代表刚刚开始取消

对普通 Turn 恰好相反。

App-server 会等 `TurnAborted` 到达后才回复 pending interrupt request。回包更接近取消完成确认，而不是简单接收确认。

startup interrupt 是没有 Turn event 的特殊例外。

## 84. 常见误解三：停止 Turn 会终止所有命令

不准确。

当前前台 execution 会沿 cancellation 路径收尾；已经成为 Session-owned background terminal 的进程默认保留。要显式 clean 或 shutdown Session。

## 85. 常见误解四：取消就是错误

不准确。

取消有独立的 `TurnAborted` 与 `TurnStatus::Interrupted`，普通失败则进入 error/failed 投影。两者对重试、UI 文案和监控的意义不同。

## 86. 常见误解五：强制 abort 足够了

不够。

只 abort task handle 无法自动处理：

- pending request map；
- rollout marker 和 flush；
- protocol terminal event；
- runtime-specific teardown；
- App-server pending interrupt response；
- Session-owned background processes。

## 87. 一张完整状态图

```text
Running
├─ 自然完成
│  └─ TurnComplete
├─ 普通错误
│  └─ TurnComplete(error) / App-server Failed
├─ steer
│  ├─ pending input
│  └─ 同一 Turn follow-up sampling
├─ interrupt
│  ├─ token cancel
│  ├─ graceful wait
│  ├─ forced task abort
│  ├─ waiters cleanup
│  └─ TurnAborted(Interrupted)
├─ replacement / review end / budget limit
│  └─ TurnAborted(other reason)
└─ Session shutdown
   ├─ interrupt active work
   ├─ terminate background resources
   ├─ close services and persistence
   └─ ShutdownComplete
```

## 88. 一张 owner 表

| 资源 | 主要 owner | interrupt 后 | shutdown 后 |
|---|---|---|---|
| Model response stream | Turn task | 取消读取 | 已取消 |
| In-flight tool Future | Turn/tool runtime | aborted 或完成收尾 | 已取消 |
| Approval waiter | TurnState | 清除 | 清除 |
| Steer pending input | TurnState | 清除或由客户端恢复草稿 | 清除 |
| Foreground command | Tool runtime | 观察 token、结束执行 | 结束 |
| Background terminal | Session unified exec manager | 默认保留 | terminate all |
| MCP startup | Session MCP runtime | 无 active Turn 时可取消 startup | shutdown |
| MCP runtime | Session services | 保留 | shutdown |
| Rollout writer | Live thread | 保留并写终态 | flush + shutdown |
| Submission loop | Session | 继续运行 | 退出 |

## 89. 怎样阅读取消相关 `select!`

每看到一次：

```rust
tokio::select! {
    result = work => ...,
    _ = token.cancelled() => ...,
}
```

都问五个问题：

1. 谁拥有 `work` Future？
2. cancellation 分支获胜后，loser 是 drop、abort 还是继续等待？
3. 已经发生的副作用由谁收尾？
4. 两个分支会不会各自产生一次 terminal event？
5. 取消结果会投影成 error、aborted output 还是 TurnAborted？

## 90. 怎样定位“按了停止但还没停”的问题

按层检查：

```text
1. UI 是否真的产生 interrupt action？
2. App-server 是否通过 turn ID 校验？
3. Op::Interrupt 是否进入 submission loop？
4. RunningTask token 是否被 cancel？
5. 卡住的 Future 是否检查 token？
6. runtime 是否要求等待自己的 teardown？
7. 100ms 后 handle 是否 abort？
8. pending waiter 是否在正确顺序清除？
9. TurnAborted 是否发送并 flush？
10. App-server 是否响应 pending interrupt 并发 turn/completed？
```

## 91. 怎样定位“停止后命令还在跑”

先判断命令身份：

```text
仍是当前 tool 的 foreground command？
还是已经 yield 成 background terminal？
```

然后检查：

- foreground：expiration 是否合并 Turn cancellation；process teardown 是否等待；
- background：产品是否要求保留；用户是否调用 clean/terminate；
- Session shutdown：`terminate_all_processes` 是否执行并完成。

不要只看到 OS process 仍存在就断言 interrupt 失效。

## 92. 怎样定位“停止后 UI 仍显示等待审批”

检查两层 pending 状态：

```text
Core TurnState pending waiter
App-server outgoing pending server request
```

Core 在取消后清 sender；App-server 在 `TurnAborted` 时 `abort_pending_server_requests()` 并发出 resolved/终态通知。任一层漏清都会产生幽灵审批 UI。

## 93. 测试应该证明什么

一份好的 interrupt 集成测试不应只断言函数返回 `Ok`，而应至少验证：

```text
请求目标 turn ID 正确
→ 收到 TurnAborted / interrupted completion
→ 没有第二个冲突终态
→ pending request 被清除
→ 后续新 Turn 可以开始
→ 按产品要求决定 background terminal 是否仍存在
```

## 94. 为什么测试 helper 同时等 response 和 abort event

App-server 测试 helper 的注释特别提醒一个竞态：

```text
TurnAborted 可能在 interrupt response 之前被观察到
```

因此测试应同时等待：

- RPC response；
- terminal notification/event。

消息到达顺序不应被错误地当成业务终态缺失。

## 95. 初学者代码单词拆解

### `cancel`

动词：请求取消。通常强调信号或意图。

### `interrupt`

动词/名词：中断正在进行的工作。产品控制操作使用这个词。

### `abort`

动词：异常或主动终止。源码常用于已经决定结束后的强制/终态处理。

### `shutdown`

关闭整个组件，并清理其拥有的资源。

### `terminate`

让进程或资源结束，语气通常比 cooperative cancel 更强。

### `graceful`

优雅的：给组件机会正常释放资源和写入最终状态。

### `pending`

待处理的：已登记但尚未得到结果。

### `in-flight`

飞行中的：请求或任务已经发出，尚未得到最终结果。

### `steer`

字面是“掌舵、调整方向”；这里指不结束当前 Turn，追加新输入改变后续工作方向。

### `replaced`

已被替换：旧任务因新任务接管同一活动槽而结束。

### `waiter`

等待者：通常是一个等待 channel、Notify 或 future 完成的任务。

### `barrier`

屏障：只有某个状态真正完成后才允许调用方越过的同步边界。

### `teardown`

拆除阶段：按依赖顺序停止工作、关闭服务、释放资源。

### `fencing`

围栏：用 generation、turn ID 或 ownership token 阻止迟到操作影响新状态。

## 96. 本篇局部术语表

| 名词 / 代码词 | 中文理解 | 在固定提交中的作用 |
|---|---|---|
| cancellation | 取消机制 | 通知异步工作尽快停止并进入可解释终态 |
| interruption | 中断生命周期 | 从停止请求到 `TurnAborted` 的完整过程 |
| `Op::Interrupt` | 中断操作 | Session submission loop 可处理的 Core 控制消息 |
| `turn/interrupt` | Turn 中断 RPC | App-server 以 thread/turn ID 请求取消活动 Turn |
| `CancellationToken` | 取消令牌 | 可克隆、可建立父子关系的协作取消信号 |
| child token | 子令牌 | 父 token 取消时也会取消的下游 token |
| cooperative cancellation | 协作式取消 | Future 主动检查 token 并执行自己的收尾 |
| forced abort | 强制中止 | `JoinHandle::abort` 让 Tokio task 不再继续 poll |
| `RunningTask` | 运行任务 owner | 保存 task、token、handle、done、context 和 guards |
| `ActiveTurn` | 活动 Turn 槽位 | 将当前 RunningTask 与 TurnState 绑定 |
| `TurnState` | Turn 可变协调状态 | 保存审批、输入、elicitation 等 pending 映射 |
| `done: Notify` | 完成通知 | cooperative task 结束时唤醒取消路径 |
| graceful timeout | 优雅退出等待上限 | 固定提交中为 100ms |
| `abort_all_tasks` | 取消整组 Turn 工作 | 取得 active owner、取消任务、清 waiters、发终态 |
| `handle_task_abort` | 单任务中止主流程 | token、等待、handle abort、marker、event、flush |
| `TurnAbortedEvent` | Turn 中止事件 | 带 turn ID、reason 和时间的 Core 结构化终态 |
| `TurnAbortReason` | 中止原因枚举 | Interrupted/Replaced/ReviewEnded/BudgetLimited |
| interrupted marker | 中断历史标记 | 让后续模型知道上一 Turn 被主动中断 |
| `steer_input` | 同 Turn 转向输入 | 把新用户输入追加进 Regular Turn pending queue |
| admission | 输入接纳 | 确认消息属于 Started 或 Steered Turn |
| persistence acknowledgement | 持久化确认 | 确认已接纳消息真正进入 rollout |
| pending waiter | 待决等待者 | 通过 oneshot 等待审批、权限、用户输入或工具响应 |
| `clear_pending_waiters` | 清待决等待者 | drop senders，让旧 Turn 的 receivers 结束 |
| terminal outcome | 唯一终态 | Complete、Failed 或 Aborted 中只能有一个生效 |
| acknowledgement barrier | 确认屏障 | App-server 等 `TurnAborted` 后才响应普通 interrupt |
| generation fencing | 世代围栏 | 用 expected turn ID 防迟到取消误伤新 Turn |
| background terminal | 后台终端进程 | 已转为 Session-owned、可跨 Turn 存活的命令 |
| `CleanBackgroundTerminals` | 清后台终端 | 与 interrupt 分离的显式后台进程清理操作 |
| `Op::Shutdown` | Session 关闭操作 | 取消活动工作并拆除所有 Session-owned runtime |
| `ShutdownComplete` | Session 关闭终态 | 表示 submission loop 的显式 shutdown 流程完成 |
| teardown | 资源拆除 | 按 owner/依赖顺序停止并释放 Session 资源 |
| cancellation safety | 取消安全 | 任意取消点都不产生泄漏、双终态或不可解释状态 |
| RAII | 作用域资源管理 | Future drop 时自动释放同步 owner/guard 的机制 |

## 97. 源码导航

### Core 协议

```text
codex-rs/protocol/src/protocol.rs
  Op::Interrupt
  Op::CleanBackgroundTerminals
  Op::Shutdown
  TurnAbortedEvent
  TurnAbortReason
```

### Session 分派与 shutdown

```text
codex-rs/core/src/session/handlers.rs
  interrupt
  shutdown_session_runtime
  shutdown
  submission_loop
```

### 任务 owner 与取消主流程

```text
codex-rs/core/src/tasks/mod.rs
  GRACEFULL_INTERRUPTION_TIMEOUT_MS
  SessionTask / AnySessionTask
  start_task
  abort_all_tasks
  on_task_finished
  handle_task_abort
  interrupted_turn_history_marker
```

### Turn 状态和 steer

```text
codex-rs/core/src/state/turn.rs
  ActiveTurn
  RunningTask
  TurnState
  clear_pending_waiters

codex-rs/core/src/session/mod.rs
  SteerInputError
  steer_input
  interrupt_task
  shutdown_and_wait

codex-rs/core/src/session/input_queue.rs
  InputQueueActivity
  clear_pending
  extend_pending_input_and_accept_mailbox_delivery_for_turn_state
```

### Sampling 和工具取消

```text
codex-rs/core/src/session/turn.rs
  run_turn
  try_run_sampling_request
  or_cancel
  drain_in_flight

codex-rs/core/src/tools/parallel.rs
  ToolCallRuntime
  terminal_outcome_reached
  aborted_response

codex-rs/core/src/exec.rs
  ExecExpiration
  ExecExpirationOutcome
  cancel_when_either
```

### App-server

```text
codex-rs/app-server/src/request_processors/turn_processor.rs
  turn_interrupt_inner

codex-rs/app-server/src/thread_state.rs
  pending_interrupts

codex-rs/app-server/src/bespoke_event_handling.rs
  EventMsg::TurnAborted branch
  handle_turn_interrupted
  respond_to_pending_interrupts

codex-rs/app-server/README.md
  Interrupt an active turn
  Steer an active turn
```

### Session 批量关闭

```text
codex-rs/core/src/thread_manager.rs
  shutdown_all_threads_bounded

codex-rs/app-server/src/request_processors/thread_processor.rs
  shutdown_threads
```

## 98. 建议的固定提交搜索命令

```bash
git grep -n 'Op::Interrupt' 4ee41929eaf4 -- codex-rs
git grep -n 'abort_all_tasks' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'TurnAbortedEvent' 4ee41929eaf4 -- codex-rs
git grep -n 'pending_interrupts' 4ee41929eaf4 -- codex-rs/app-server/src
git grep -n 'steer_input' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'shutdown_all_threads_bounded' 4ee41929eaf4 -- codex-rs
```

## 99. 理解检查

### 问题 1

为什么 `CancellationToken::cancel()` 之后不能立刻向客户端声称 Turn 已经结束？

提示：区分“发出通知”和“资源、协议终态已完成”。

### 问题 2

为什么清 pending approvals 必须排在 task 先观察 cancellation 之后？

提示：receiver closed 会被映射成什么决定？

### 问题 3

为什么 `turn/interrupt` 必须带 expected turn ID？

提示：考虑旧请求迟到时新 Turn 已开始。

### 问题 4

为什么 interrupt 不清理 background terminal，而 shutdown 会？

提示：比较两种资源的 owner 和预期寿命。

### 问题 5

steer 输入在什么时候进入模型请求？

提示：寻找 `has_pending_input` 与 `needs_follow_up`。

### 问题 6

为什么 marker flush 和 terminal event flush 是两个不同 barrier？

提示：客户端可能在什么时刻重新读取 rollout？

## 100. 练习：手动画三条时间线

不要写代码，先画：

### 时间线 A：模型流期间 interrupt

至少包含：

```text
stream.next
token.cancel
or_cancel
TurnAborted
App-server response
```

### 时间线 B：等待审批期间 interrupt

至少包含：

```text
pending sender
receiver wait
token cancel
task abort
clear_pending_waiters
serverRequest resolved
```

### 时间线 C：有后台 terminal 时 shutdown

至少包含：

```text
active Turn cancel
terminate_all_processes
MCP shutdown
rollout shutdown
ShutdownComplete
```

如果能解释每一条边为什么按这个顺序出现，就已经掌握本篇核心。

## 101. 本篇结论

Codex 的 interruption lifecycle 不是“删掉一个任务”，而是一组层层收敛的状态转换：

```text
交互入口产生精确 Turn 控制请求
→ Session 取得 active task 所有权
→ token 向模型流、工具和执行 runtime 广播取消
→ 短暂等待协作式收尾
→ 强制 abort 提供有界兜底
→ 按正确顺序清 pending waiters 和输入
→ 用 marker、flush 与 TurnAborted 固化事实
→ App-server 将它投影为 interrupted completion
→ Session 本身继续可用
```

而 steer 与 shutdown 分别位于这条链的两侧：

```text
steer    比 interrupt 更轻：不结束 Turn，只改变后续方向
shutdown 比 interrupt 更重：结束 Session，并清理后台资源
```

理解这三个动作的 owner、边界和终态，是阅读任何异步 Agent 系统的基础。

下一篇计划精读 **User input admission 与 pending queue**：一条用户消息从 UI/App-server 进入以后，怎样判定是启动新 Turn、steer 当前 Turn、等待持久化，或因竞态被拒绝。

返回[源码精读目录](README.md)或[课程总目录](../README.md)。
