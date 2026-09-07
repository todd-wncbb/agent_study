# Walkthrough：后台终端任务如何启动、转入后台、等待、取消并自动唤醒模型

本文追踪一个终端命令成为后台任务后的完整生命周期。

第 2 篇已经解释了 Bash Tool Call 如何通过参数解析、Hooks、Permission、
`TerminalBackend::run` 并把普通前台结果返回模型。本文不重复那条主链，专门回答：

- `is_background=true` 如何直接创建后台任务；
- 前台命令如何被 Ctrl+G 或时间预算转入后台；
- 为什么后台任务同时有内存预览和磁盘完整日志；
- `get_task_output` 的 poll 与 wait 有什么不同；
- 多任务的 wait-all / wait-any 如何避免轮询和 zombie waiter；
- `kill_task` 如何区分 killed、already exited 和 not found；
- 任务完成后为什么有时自动唤醒模型，有时必须抑制唤醒；
- Session、Subagent 和进程退出时如何避免遗留进程。

本文以当前源码基线为准。通用工具分发流程见第 12 篇；普通 Bash 权限链见第 2 篇。

---

## 1. 先看完整调用链

显式后台路径：

```text
model BashToolInput { is_background: true }
  -> BashTool::run
  -> validate background capability / '&' operator
  -> resolve background timeout
  -> TerminalBackend::run_background
  -> LocalTerminalBackend sends RunBackground
  -> LocalTerminalActor::handle_run_background
  -> spawn child + ProcessGroup + output file
  -> processes[generated task_id] = ProcessState(Backgrounded::Explicit)
  -> BackgroundHandle returned immediately
  -> BashExecutionBackgrounded notification
  -> BackgroundTaskStarted tool result returned to model
```

前台转后台路径：

```text
BashTool::run -> TerminalBackend::run
  -> actor stores foreground ProcessState under internal UUID
  -> one of:
       user Ctrl+G -> SessionCommand::BackgroundForegroundCommand
       foreground block budget elapsed
       timeout elapsed while auto_background_on_timeout=true
  -> transition_to_background(internal_id, reason)
  -> notify foreground oneshot waiter
  -> re-key ProcessState under tool_call_id
  -> BashTool receives TerminalRunResult(signal=backgrounded/auto_backgrounded)
  -> BackgroundTaskStarted returned to model
```

完成与消费路径：

```text
actor periodic tick
  -> poll_process
  -> drain stdout/stderr + append log + update preview
  -> child exits
  -> TaskSnapshot(completed=true)
  -> TaskCompleted notification
  -> either:
       get_task_output / wait_tasks consumes result
       kill_task consumes result
       idle notification drain creates synthetic model turn
       between-turn reminder injects completion into current model context
```

核心思想是：**Tool Call 结束不等于 OS process 结束**。后台化把两者的生命周期拆开了。

---

## 2. 建议同时打开的源码

| 关注点 | 源码 |
| --- | --- |
| Bash 输入、参数和前后台分支 | `crates/codegen/xai-grok-tools/src/implementations/grok_build/bash/mod.rs` |
| Terminal 抽象、request/result/snapshot | `crates/codegen/xai-grok-tools/src/computer/types.rs` |
| 本地进程 actor | `crates/codegen/xai-grok-tools/src/computer/local/terminal.rs` |
| 生命周期状态机 | `crates/codegen/xai-grok-tools/src/computer/local/lifecycle.rs` |
| 单/多任务输出工具 | `crates/codegen/xai-grok-tools/src/implementations/grok_build/task_output/mod.rs` |
| 兼容版 wait_tasks | `crates/codegen/xai-grok-tools/src/implementations/grok_build/task_output/wait_tasks.rs` |
| kill_task | `crates/codegen/xai-grok-tools/src/implementations/grok_build/kill_task/mod.rs` |
| 共享 task wire types | `crates/common/xai-tool-types/src/task.rs` |
| 工具通知类型与 fan-out | `crates/codegen/xai-grok-tools/src/notification/` |
| 完成提醒与去重 | `crates/codegen/xai-grok-tools/src/reminders/task_completion.rs` |
| Session 通知缓存与 drain | `crates/codegen/xai-grok-shell/src/session/acp_session_impl/notification_drain.rs` |
| tool result 消费 completion | `crates/codegen/xai-grok-shell/src/session/acp_session_impl/tool_calls.rs` |
| Session command 与 Ctrl+G 路由 | `crates/codegen/xai-grok-shell/src/session/commands.rs`、`handle.rs`、`acp_session_impl/run_loop.rs` |
| 跨平台进程组回收 | `crates/codegen/xai-tty-utils/src/lib.rs`、`process_scope.rs` |

---

## 3. 三种“进入后台”不是同一种入口

`BackgroundReason` 有三种：

| 原因 | 入口 | actor 初始状态 |
| --- | --- | --- |
| `Explicit` | 模型传 `is_background=true` | 创建时即 Backgrounded |
| `UserSignal` | 用户对运行中的前台命令触发 Ctrl+G | Foreground -> Backgrounded |
| `ForegroundTimeout` | 前台阻塞预算或 timeout 到期且允许 auto-bg | Foreground -> Backgrounded |

三者都会让工具先返回一个 `BackgroundTaskStarted`，而进程继续运行，但 ID 生成、
初始输出、signal 文本和 timeout 处理并不完全相同。

---

## 4. `is_background` 先经过能力配置

`BashParams.enabled_background` 决定本轮工具是否允许后台执行。

它不是只在运行时判断：工具 finalize 时还会：

- 从模型可见 schema 中移除 `is_background`；
- 从 required 数组移除对应字段；
- 改写 Bash 工具描述；
- 校验 `auto_background_on_timeout` 不能在 background disabled 时开启；
- 确认 `get_task_output` 与 `kill_task` 等配套工具存在。

因此“模型没有传后台参数”和“模型根本看不到后台参数”是两种不同配置状态。

---

## 5. 为什么不鼓励命令自己写 `&`

Grok Build 希望后台生命周期由 `TerminalBackend` 管，而不是由 shell 私自产生一个脱离管理的 job。

当命令使用 POSIX 裸 `&` 或 PowerShell 尾随 `&` 时，Bash 工具会根据：

- 当前 shell 的 ampersand 语义；
- `allow_background_operator`；
- `enabled_background`；
- contract 是否 legacy；
- 当前调用是否已传 `is_background`；

给出针对性的拒绝信息。

推荐形式是：

```json
{
  "command": "npm run dev",
  "is_background": true,
  "description": "local development server"
}
```

这样系统能得到 PID、task ID、日志路径、owner session、完成通知与 kill 能力。

---

## 6. `TerminalRunRequest` 是进程后端的完整契约

后台相关字段包括：

| 字段 | 含义 |
| --- | --- |
| `command` | 真正交给 shell 的命令，可能含配置 prefix |
| `display_command` | 对模型/UI 更友好的原始命令；普通 Bash 当前通常为 None |
| `working_directory` | 运行目录 |
| `env` | Session 环境快照与调用级覆盖 |
| `timeout` | 进程最大运行时间；不是单纯的工具等待时间 |
| `foreground_block_budget` | 前台最多阻塞 turn 多久，只决定后台化，不杀进程 |
| `output_byte_limit` | 内存中返回/展示的输出预算 |
| `output_file` | 完整日志目标路径 |
| `notification_handle` | 输出 chunk、后台化、完成等事件通道 |
| `tool_call_id` | 把进程事件关联回原始工具调用 |
| `owner_session_id` | 让 scoped cleanup 只处理所属 Session 的任务 |
| `kind` | Bash 或 Monitor |
| `description` | 模型提供的人类可读标签 |

一个常见误解是把 `timeout` 和 `foreground_block_budget` 当成同一个计时器。它们作用不同。

---

## 7. 显式后台 timeout 的特殊规则

`resolve_effective_timeout` 对后台任务采用独立语义：

- positive timeout：使用模型给出的值，但受 absolute maximum 限制；
- `0` 或省略：`Duration::MAX`，不受前台默认 timeout 限制；
- `max_timeout_secs` 是前台配置，不会裁掉后台正数 timeout；
- 后台任务的生命通常由进程自然退出或 `kill_task` 结束。

这正适合 dev server、watcher 和长时间 build，但也意味着调用方必须保留可管理它的 task ID。

---

## 8. 显式后台会注入 `PYTHONUNBUFFERED=1`

显式后台分支会在没有既有覆盖时加入：

```text
PYTHONUNBUFFERED=1
```

原因是 Python 在非交互 pipe/file 场景可能积累约一个缓冲块后才输出。后台任务依赖增量日志与完成前观察，
如果输出长期留在 Python 用户态 buffer 中，`get_task_output` 看起来会像“卡住”。

这不是全语言通用的无缓冲保证，只是针对 Python 的实用补偿。

---

## 9. 输出文件路径从 tool call ID 派生

Bash 工具先创建：

```text
<session_folder>/terminal/<tool_call_id>.log
```

显式后台 actor 可以生成另一个 task ID，但日志文件仍与原始 tool call 关联。

这解释了为什么以下标识不要混用：

| 标识 | 用途 |
| --- | --- |
| `tool_call_id` | conversation、ACP/TUI 原始工具卡片关联 |
| `task_id` | 后台 registry 查询、等待和 kill |
| `pid` | 本机 OS 进程诊断；远端 backend 可能没有 |
| `output_file` | 完整输出的持久读取位置 |

---

## 10. `run_background` 返回的是 Handle，不是执行结果

`TerminalBackend::run_background` 返回 `BackgroundHandle`：

```rust
pub struct BackgroundHandle {
    pub task_id: String,
    pub output_file: PathBuf,
    pub pid: Option<u32>,
}
```

这里没有 exit code，因为进程尚未完成；也没有最终 output，因为工具调用必须立即返回。

`BackgroundHandle` 是后续管理能力的凭证，不是任务结果。

---

## 11. Local backend 是 Handle + Actor，而不是一把大锁

`LocalTerminalBackend` 只持有：

- `mpsc::Sender<TerminalCommand>`；
- `CancellationToken`。

所有可变进程状态由 `LocalTerminalActor` 单独拥有。公开方法把请求发送给 actor，再通过 oneshot 接收响应。

优点是：

- `get_task`、`kill_task` 和 `run` 不共享 async mutex；
- `ProcessState` 不会被多个任务同时修改；
- 命令处理、输出 poll、timeout、reap 有一个序列化状态机；
- Session 可共享一个 `Arc<dyn TerminalBackend>` 而不暴露内部 child handle。

---

## 12. Actor command channel 为什么容量为 32

本地 actor 使用 bounded mpsc，容量 `COMMAND_CHANNEL_SIZE = 32`。

这是 actor 的第一层背压：调用者发送过快时会 await，而不是无限积累 run/kill/query 请求。

但是进程输出不经过这个 command channel；它由 actor ticker 直接从 pipe 读取，再通过 notification handle 发出。

---

## 13. `ProcessState` 才是后台任务的真实运行态

一个 `ProcessState` 同时持有：

- `tokio::process::Child`；
- `ProcessGroup`；
- stdout/stderr pipe；
- 内存头部/尾部 buffer；
- 日志 file handle；
- `Lifecycle`；
- `BackgroundStatus`；
- completion waiters；
- timeout 与 foreground budget；
- wall/monotonic time；
- tool call ID、owner session ID、description；
- notification handle；
- persistent shell state dump handle。

`TaskSnapshot` 是它的只读投影，不拥有 child，也不能直接操作进程。

---

## 14. 为什么要同时有 `Lifecycle` 和 `BackgroundStatus`

它们回答两个正交问题：

- `Lifecycle`：OS process 还在运行、正在退出、已收集还是已 sweep？
- `BackgroundStatus`：当前 Tool Call 是否仍在等待这个 process？它为什么进入后台？

因此一个进程可以是：

```text
Running + Foreground
Running + Backgrounded
Finished + Backgrounded
Swept + Backgrounded
```

把两个维度塞进一个 enum 会造成大量难以维护的组合状态。

---

## 15. 进程树通过 `ProcessGroup` 管理

只 kill shell leader 常常不够：命令可能启动编译器、测试 runner、server 或孙进程。

Grok Build 把 child 加入跨平台进程树管理结构：

- Unix：进程组，终止时使用 group signal；
- Windows：Job Object；
- `ProcessScope` 保存 Weak 引用，支持更上层统一清理；
- actor 的 `ProcessState` 持有强 `Arc`，正常 reap 后释放，降低 PID reuse 风险。

这是“关闭 Session 后 dev server 不应继续偷偷运行”的基础。

---

## 16. 显式后台 actor 会生成新的 task ID

`handle_run_background` 生成 registry key，创建 `ProcessState`，并立即把 `BackgroundHandle` 发回调用方。

此时：

```text
Tool Call 已终止
OS process 仍运行
actor processes map 持有任务
日志持续写入
模型拿到 task_id
```

这条路径中的 task ID 与 tool call ID 是不同标识。

---

## 17. `BashExecutionBackgrounded` 与 Tool Result 各服务谁

后台启动后会产生两个输出面：

1. `BashExecutionBackgrounded` notification：给 Session/UI 注册后台任务卡片；
2. `BackgroundTaskStarted` tool output：给模型知道 task ID、日志路径、状态、PID 与检索提示。

只发 notification，模型不知道如何管理任务；只发 tool result，UI 的后台任务列表可能没有对应运行态。

---

## 18. Retrieval hint 不能硬编码工具名

`background_retrieval_hint` 通过 `TemplateRenderer` 查找：

- 当前 `BackgroundTaskAction` 的真实工具名；
- 它的 `task_ids` 实际参数名。

工具可能被改名或使用不同 namespace/contract，因此返回给模型的建议必须是本轮真实 schema：

```text
Use <resolved tool> with <resolved param>=["<task id>"] ...
```

---

## 19. 前台命令最初用内部 UUID 做 map key

`handle_run` 为前台命令生成内部 UUID，并把 tool call 的 oneshot reply 放进 `completion_waiters`。

内部 ID 的意义是：前台命令尚未承诺成为可查询后台任务，调用者只在等待 `TerminalRunResult`。

如果它正常结束，该 entry 回复 waiter 后很快从 actor map 删除；模型从来不需要知道内部 UUID。

---

## 20. Ctrl+G 如何一路到达 actor

控制路径是：

```text
client/TUI action
  -> SessionHandle::background_foreground_command(tool_call_id)
  -> SessionCommand::BackgroundForegroundCommand
  -> SessionActor run loop
  -> ToolBridge::background_foreground_command
  -> TerminalBackend::background_foreground_command
  -> TerminalCommand::BackgroundForeground
  -> LocalTerminalActor::handle_background_foreground
```

actor 会扫描 `processes`，找到 `ProcessState.tool_call_id` 匹配且仍是 foreground 的 entry。

这条控制请求不需要等待原 Bash Tool Call 自己返回，因此能解除正在阻塞的 turn。

---

## 21. 前台阻塞预算不是进程 timeout

默认 `FOREGROUND_BLOCK_BUDGET` 是 15 秒，并可由环境或 Bash 参数覆盖。

当：

```text
auto_background_on_timeout = true
elapsed > foreground_block_budget
```

actor 只执行 `transition_to_background`，不会杀进程。

这个预算的目标是避免一个本来可以后台管理的长命令把 agent turn 卡住。

`foreground_block_budget_ms=0` 会转换成 `Duration::MAX`，表示禁用短预算，只让真正 timeout 决定后台化。

---

## 22. Timeout 到期有两个完全不同的终点

如果进程到达 `request.timeout`：

| auto background | 行为 |
| --- | --- |
| true | 转后台，signal=`auto_backgrounded`，进程继续 |
| false | 向进程组发终止信号，signal=`timeout`，进程进入退出/回收流程 |

所以看到“timeout”字样时必须核对 signal，而不能只看耗时。

---

## 23. `transition_to_background` 做了四件关键事

该函数：

1. 从旧 key 移出 `ProcessState`；
2. 把 `bg_status` 改为带 reason 的 Backgrounded；
3. 把后续 timeout 设置为 `BACKGROUND_MAX_RUNTIME`；
4. 用当前 `TerminalRunResult` 通知原 foreground waiter；
5. 以 `tool_call_id` 作为新 key 重新插入 map。

第四步让 Bash Tool Call 立即返回，第五步使 task 后续能按模型已经知道的 ID 查询。

---

## 24. 当前 task ID 注释与真实行为有偏差

`BashExecutionBackgrounded.task_id` 的注释写着 task ID 与 tool call ID “always different”。

当前源码实际上分两种：

| 路径 | task ID |
| --- | --- |
| 显式 `is_background=true` | actor 新生成，通常不同于 tool call ID |
| 前台经 Ctrl+G / auto-bg | `transition_to_background` 直接以 tool call ID re-key，因此相同 |

阅读日志、写 UI 关联或修改 wire contract 时，应按真实分支处理，不能依赖“总是不同”的注释。

---

## 25. 前台 waiter 如何知道“不是正常完成”

`BackgroundReason::as_signal` 映射为：

- Explicit / UserSignal：`backgrounded`；
- ForegroundTimeout：`auto_backgrounded`。

Bash 工具从 `TerminalRunResult.signal` 识别后台化，并把普通 Bash result 改造成 `BackgroundTaskStarted`。

这是一种 typed result 之前的内部 sentinel 协议：字符串只存在于 Terminal result 边界，外部再升级为结构化输出。

---

## 26. 为什么后台化时要带上已经产生的输出

前台运行几秒后才转后台时，可能已经输出编译进度或 server 地址。

actor 的 `to_result()` 会返回当前：

- `combined_output`；
- `total_bytes`；
- `truncated`；
- `output_file`；
- `pid`。

`BashExecutionBackgrounded.base` 因此能包含后台化之前的输出，UI 不必丢掉已显示内容。

---

## 27. Actor 的 ticker 只在有进程时激活

主循环是 biased `tokio::select!`：

1. cancellation；
2. incoming command；
3. periodic tick，但只在 `processes` 非空时启用。

命令和 cancel 优先于 tick，保证 kill 不会长期排在慢 poll 后面。

当一个 Session 没有进程时，ticker arm 根本不被 poll，避免每个空闲 tab 每秒无意义唤醒多次。

---

## 28. 输出读取使用周期性 non-blocking poll

`poll_process` 对 stdout 和 stderr 调用 `try_read_nonblocking`，使用 noop waker。

这在普通 async stream 代码里看起来反常，但这里安全，因为 actor 自己有周期 ticker，不依赖 pipe waker 唤醒。

它避免旧实现对每个 stream 做 10ms timeout，导致 N 个任务每 tick 产生 `N × 20ms` 开销。

---

## 29. stdout 与 stderr 当前会合并

每个 tick 将两个 pipe 当前可读字节追加到同一个 `new_bytes`：

```text
stdout available bytes
stderr available bytes
  -> new_bytes
  -> output_buffer
  -> output_file
```

因此最终是 combined output，不保留严格的跨 pipe 原始时间顺序，也不是分别返回两个字段。

做诊断工具时不要假设能从结果还原 stdout/stderr 来源。

---

## 30. 为什么每个 tick 都 flush 日志文件

actor 写入本 tick 新字节后会 flush，使 `get_task_output` 返回的 `output_file` 能被 `read_file` 及时读取。

如果只依赖操作系统缓冲直到任务结束，后台任务的“完整日志文件”在运行中可能看不到最新内容。

代价是更多 flush；这是可观察性与吞吐量之间的明确取舍。

---

## 31. 内存输出与完整日志为什么分开

无限增长的进程输出不能永远保留在 actor 内存里。

`ProcessState` 采用两层存储：

- 内存：受 `output_byte_limit` 限制，保留前部和最新尾部；
- 磁盘：持续写 `output_file`，受更大的 file cap 约束。

模型通常只需要短预览判断状态；需要完整证据时再用 read/search 工具读取日志文件。

---

## 32. Truncation 保留头尾而不是只保留尾部

输出超过内存预算后，`front_buffer` 冻结最早一段，`output_buffer` 保留最新尾部，中间插入 truncation marker。

这样同时保留：

- 启动配置、第一条错误等开头信息；
- 最近进度、最终错误等尾部信息。

`total_bytes` 始终单调增长，不能用当前 buffer 长度代替它。

---

## 33. Streaming notification 为什么以 `total_bytes` 判定增量

每个 tick 只有当：

```text
process.total_bytes > process.last_notified_total
```

才发送 `BashOutputChunk`。

如果按 `output_buffer.len()` 判断，buffer 进入头尾截断后长度会接近固定值，后续输出明明继续增长却不再触发通知。

因此 `total_bytes` 既是统计字段，也是 streaming progress 的单调游标。

---

## 34. 快速退出为什么还要做最后一次 drain

child 可能已经 exit，但 stdout/stderr pipe 中还有内核缓冲数据。

检测到 `try_wait()` 返回 exit status 后，actor 先调用 `drain_remaining_output`，再 finalize result。

否则像 `python -c "print('x')"` 这样的短命令可能在第一次周期 poll 前退出，最终结果却错误地显示空输出。

---

## 35. Completion waiter 不会阻塞 actor loop

`WaitForCompletion` 不会让 actor 自己 await 进程完成。

actor 把：

```rust
CompletionWaiter {
    reply,
    deadline,
}
```

存入 `completion_waiters[task_id]`，继续处理其他 command 和 tick。后续 poll 检查 task 完成或 waiter deadline，再发送 snapshot。

这避免一个长达数分钟的 wait 卡死所有 kill、query 和其他进程输出处理。

---

## 36. `TaskSnapshot` 是跨 backend 的统一只读视图

Local 与 ACP backend 都通过 `TaskSnapshot` 暴露任务：

| 字段 | 主要用途 |
| --- | --- |
| `task_id` | query/kill key |
| `command` / `display_command` | 执行事实与友好展示 |
| `start_time` / `end_time` | duration 与状态展示 |
| `output` | 当前内存预览 |
| `output_file` | 完整日志入口 |
| `truncated` / `output_total_bytes` | 判断预览是否完整以及真实进度 |
| `exit_code` / `signal` / `completed` | terminal status |
| `block_waited` | 阻止重复 auto-wake |
| `explicitly_killed` | kill 已向模型返回，阻止重复 auto-wake |
| `owner_session_id` | scoped cleanup |
| `is_backgrounded` | 区分正在阻塞 turn 的 foreground process |

它是 UI、工具和 reminder 之间的稳定数据交换层。

---

## 37. `get_task_output` 的输入刻意做了宽松反序列化

标准 schema 是：

```json
{
  "task_ids": ["id-1"],
  "timeout_ms": 0
}
```

但 wire parser 还接受：

- singular alias `task_id`；
- 单个 string/number，而不只 array；
- 多个 ID 中的空白与重复值。

`resolve_task_ids` 会 trim、去空、按首次出现顺序去重。

这是针对模型常把 `kill_task.task_id` 的单数形式迁移到查询工具的真实容错，不代表 schema 应继续宣传多种写法。

---

## 38. Poll 与 Wait 只由 positive `timeout_ms` 区分

`task_output_waits` 的规则非常简单：

| timeout_ms | 行为 |
| --- | --- |
| omitted | non-blocking snapshot |
| 0 | non-blocking snapshot |
| positive | 最多等待指定时长 |

因此省略 timeout 并不意味着默认等待 30 秒。30 秒默认值只在调用方已经选择 wait 语义的 legacy/internal 路径中使用。

---

## 39. 单任务查询先查 Bash，再查 Subagent

统一 `get_task_output` 工具可处理后台 shell task 和 subagent。

解析顺序是：

1. `terminal.get_task(task_id)`；
2. 如未找到，再调用 `SubagentBackendResource.query`；
3. 两边都没有，返回 typed `TaskNotFound`。

因此 ID namespace 最好保持不冲突；若冲突，Bash task 当前优先。

---

## 40. `TaskNotFound` 为什么不算 terminal completion

`TaskOutputOutput::is_terminal()` 对 `TaskNotFound` 返回 false。

找不到可能意味着：

- ID 写错；
- notification 与 registry 存在短暂竞态；
- 使用了另一个 Session 的 task ID；
- 旧任务 tombstone 已被容量淘汰。

它不是“该任务已经失败”的证据，因此不能消费 completion 或触发已完成语义。

---

## 41. Snapshot 如何转换成模型结果

`snapshot_to_result` 把运行态压缩为 `TaskOutputResult`：

- 解析 completed、exit code、signal 与 explicit kill；
- 生成 `running`、`completed`、`failed`、`cancelled` 或 `timed_out`；
- 格式化 wall time 与 duration；
- 裁剪 output，并附完整日志读取提示；
- 保留 `raw_output_bytes`。

一个重要优先级是：explicit kill 要胜过进程终止时留下的其他 signal，让模型看到 `cancelled`。

这里有一个容易误判的当前实现差异：

- 多任务汇总的 `is_terminal_status` 包含 `timed_out`；
- 共享 `TaskOutputResult::is_terminal()` 包含 `completed`、`failed`、`cancelled`，但不包含 `timed_out`；
- completion 去重函数 `consumed_completion_ids` 对普通 task-output 只认 `status == "completed"`，
  kill result 则单独视为已消费。

所以代码中的“terminal”“已完成计数”和“已消费 completion”不是同一个 predicate。修改状态枚举时必须逐个核对，
不能只改其中一个 helper。

---

## 42. `raw_output_bytes` 是 doom-loop 检测所需的进度信号

输出被裁剪后，模型可见 `output.len()` 可能长期不变，即使后台任务仍不断产生新日志。

`TaskOutputResult::progress_signature` 因此 hash：

- status；
- exit code；
- ended 是否存在；
- `raw_output_bytes`。

模型重复 query 时，只要真实字节数增长，就被视为有进展，而不是无意义轮询 doom loop。

---

## 43. 等待时长必须有服务端 ceiling

模型提供的正数 timeout 会经 `capped_wait_timeout` 限制。

默认最大值由 `MAX_WAIT_BLOCK_MS_DEFAULT = 600_000` 给出，也可用 `GROK_MAX_WAIT_BLOCK_MS` 调整。

同一个值同时进入：

- 运行时 cap；
- schema maximum；
- 工具描述中的 `{max_wait_ms}`。

这样模型看到的承诺与 server 实际执行保持一致，也避免 `Instant::now() + Duration` 溢出或 turn 长期卡住。

---

## 44. 多任务 query 的第一阶段总是先做 snapshot

`run_multi_tasks` 首先对每个 ID 调用 `resolve_tasks`，得到：

- 当前 results；
- pending Bash IDs；
- pending subagent IDs。

如果调用只是 poll，直接返回第一阶段结果。

如果是 wait 且仍有 pending，才进入 event-driven wait；结束后再次 resolve，保证返回的是 deadline/完成时刻的最新 snapshot。

---

## 45. Wait-all 不是每 200ms 轮询一次

当前实现为每个 pending Bash task 注册 `TerminalBackend::wait_for_completion`，为 subagent 发 blocking query，
再用 `join_all` 等待全部 helper task 或整体 deadline。

状态变化会唤醒等待者，没有固定 200ms polling loop。

收益是任务安静运行时不会持续制造查询与调度开销。

---

## 46. Wait-any 为什么先注册 `Notify::notified`

wait-any 创建共享 `Notify`，并在 spawn helper waits 之前先取得 `notified()` future。

原因是 task 可能非常快地完成。如果 helper 先完成并调用 `notify_waiters()`，此时还没有已注册 waiter，通知可能丢失。

“先注册，再启动生产者”是使用 edge-like notification 时的重要并发习惯。

---

## 47. `AbortWaitsOnDrop` 防止 zombie waiter

多任务 wait 为竞速会 spawn helper task。如果 wait-any 首个任务完成，或外层工具被取消，剩余 helper 不能继续在后台等待。

否则它们未来可能：

- 消费一个 completion；
- 把 `block_waited` 标为 true；
- 但原工具 future 已不存在，模型根本没收到结果；
- auto-wake 又因 block_waited 被抑制，最终丢失完成消息。

`AbortWaitsOnDrop` 保存每个 helper 的 abort handle，在所有退出路径统一 abort。

---

## 48. Wait deadline 同时 ready 时如何消歧

`tokio::select!` 在 completion 与 deadline 同时 ready 时没有固定选择。

`finalize_wait_outcome` 在 select 后再次检查 `Instant::now() >= deadline`：如果已经到 deadline，统一报告 `DeadlineElapsed`。

这让边界语义不依赖 runtime 某次随机选择。

---

## 49. `wait_tasks` 是兼容工具，主能力已并入 get_task_output

legacy `wait_tasks` 仍支持：

- wait-all；
- wait-any。

但普通多 ID wait-all 已委托给统一 `get_task_output` 多任务路径。区别是 legacy wait 在省略或 0 timeout 时仍采用默认 blocking budget，
而 `get_task_output` 的省略/0 明确是 poll。

读调用日志时必须先确认实际 tool kind，不能只看参数形状。

---

## 50. 等待工具为什么可以被用户新消息打断

第 12 篇提到，正 timeout 的 `get_task_output` 和 `wait_tasks` 会被标为 interruptible wait tool。

Session dispatch 用 biased select 竞速：

- 工具等待结果；
- pending user interjection。

用户发新消息时，系统返回一个 synthetic cancelled `TaskOutputResult`，内容说明等待被打断，但后台 task 本身继续运行。

“取消等待”不等于“取消进程”。真正停止进程必须调用 kill。

---

## 51. `kill_task` 先尝试 Terminal，再尝试 Subagent

统一 kill 流程：

```text
terminal.kill_task(id)
  -> Killed / AlreadyExited: 直接返回
  -> NotFound:
       if subagent backend exists -> cancel subagent
       else -> typed TaskNotFound
```

这与 get-output 的 Bash-first namespace 顺序一致。

---

## 52. Kill 的三种 Terminal outcome

`KillOutcome` 是：

| outcome | 含义 |
| --- | --- |
| `Killed` | 找到仍运行进程，并发起终止 |
| `AlreadyExited` | registry 知道任务，但它已经结束 |
| `NotFound` | 当前 backend/tombstone 中没有该 ID |

`AlreadyExited` 仍被当作 kill tool 成功结果，因为用户目标“它不要再运行”已经满足。

---

## 53. Explicit kill 为什么要写入 snapshot

actor 在显式 kill 前设置 `process.explicitly_killed = true`。

该字段随后影响两件事：

- snapshot-to-result 把状态呈现为 cancelled；
- completion notification bridge 抑制自动唤醒，因为模型已从 kill tool result 得知结果。

如果只发送 signal 而不记录消费语义，Session 可能在 kill 之后又自动发起一轮“任务完成”模型调用。

---

## 54. Kill 需要处理整个进程树

Unix 路径通常先向 group 发 SIGTERM，之后在 grace window 内未退出会升级 SIGKILL；Windows 通过 Job Object 终止 descendants。

actor 还要：

- 收集 child，避免 zombie；
- drain 剩余 pipe；
- flush/truncate output file；
- 通知 waiters；
- 记录 wall end time 与 terminal status。

kill API 返回只是控制请求结果，资源回收仍由 lifecycle/poll 状态机完成。

---

## 55. 任务完成后为什么不能立即删除所有状态

完成的后台 `ProcessState` 默认在 map 中保留 `COMPLETED_TASK_TTL = 5 minutes`。

原因是模型可能在收到 completion notification 后才查询。如果刚 exit 就删除，会出现错误的 TaskNotFound。

TTL 后 actor 将它变成轻量 `completed_task_snapshots` tombstone：保留 metadata 与日志路径，不再持有 child、pipe、file handle 或完整输出。

---

## 56. Tombstone 为什么不保留 output

长时间后台任务可能产生巨大日志。若每个完成任务的 tombstone 都把 output 留在内存，Session lifetime 内会形成无界内存增长。

eviction snapshot 因此：

- `output = ""`；
- `output_total_bytes` 保留；
- `truncated` 标明内存视图不完整；
- `output_file` 指向磁盘事实源。

另外 tombstone map 还有最大容量，超过时淘汰最老 start time 的条目。

---

## 57. `TaskCompleted` notification 是第二阶段终态

`BashExecutionBackgrounded` 是工具层的第一次终态：原 Tool Call 已经返回。

进程真正结束后，actor 发送 `TaskCompleted(TaskSnapshot)`，这是后台生命周期的第二阶段终态。

同一个业务动作因此有两个 terminal 概念：

```text
tool terminal: background task accepted and registered
process terminal: background task completed/failed/cancelled
```

---

## 58. Notification queue 会优先保护 terminal event

`ToolNotificationHandle` 支持 plain、bounded、capped 和 acknowledged targets。

capped queue 满时：

- lossy progress event 可以被丢弃；
- terminal/critical notification 会淘汰一个旧的非关键事件；
- 如果全是关键事件，仍淘汰最旧项以容纳新终态。

`TaskCompleted` 被列为 critical，因为丢一点 streaming chunk 尚可从日志恢复，丢掉完成状态会让任务永久像在运行。

---

## 59. `block_waited` 表示结果已经被直接交付

当一个真正存活的 blocking waiter 收到完成 snapshot 时，task 会记录 `block_waited=true`。

notification bridge 看到它后不再产生 auto-wake synthetic prompt，因为当前 get-output/wait tool result 已经把完成状态送入模型对话。

注意前文的 `AbortWaitsOnDrop`：只有结果确实有接收者时才能设置此标志，取消掉的 wait 不能误消费完成。

---

## 60. Completion 还有一套 reservation / reported 去重

只靠 `block_waited` 不够，因为完成可能同时穿过：

- tool result reminder；
- notification auto-wake；
- between-turn completion drain；
- pending synthetic prompt；
- monitor event buffer。

系统还维护 completion reservations 与 `ReportedTaskCompletions`，用于在不同投影之间声明“这个 ID 已预留/已报告”。

这是一种跨通道 exactly-once-like 协调；它不是数据库意义的严格 exactly once，但目标是模型只获知一次。

---

## 61. 成功 tool result 会清理同 ID 的待处理自动唤醒

`handle_bridge_tool_success` 调用 `consumed_completion_ids(&result.output)`。

如果 `get_task_output` 或 `kill_task` 已消费完成，Session 会从：

- `pending_inputs`；
- `pending_notifications`；

删除匹配 ID 的 synthetic completion，还会丢掉该 task 的旧 monitor stdout。

否则队列里的自动唤醒稍后落进 chat history，会形成一个没有对应 assistant reply 的尾部 system reminder。

---

## 62. Auto-wake 不是直接在 notification callback 里调用模型

完成通知先变成带 priority/source 的 `PendingNotification`。

Session 只有在满足 idle gate 时才 drain：

- 当前没有 running turn；
- 没有真实 user prompt 排队；
- notification 没有被 cancel/goal gate 抑制。

多个通知可合并成一个 `PromptOrigin::NotificationDrain` synthetic turn，中间用 `---` 分隔。

这样 background completion 不会抢占真实用户输入，也不会为同一时刻完成的十个任务启动十轮模型请求。

---

## 63. Pending notification 有容量 50

`MAX_PENDING_NOTIFICATIONS = 50`。

超出时从最老通知开始删除，并写 warning。

这是 Session 长期繁忙或通知生产失控时的内存保险。上游 critical queue 保护终态，并不意味着下游 pending buffer 可以无限积累。

---

## 64. 用户 Prompt 为什么优先于 completion turn

`maybe_drain_notifications` 复用 session idle predicate，并要求没有 pending user input。

如果用户在后台 build 完成时正好发来问题，用户输入先被 promote；completion 可以：

- 作为 deferred reminder 注入这轮；
- 或等 Session 再次 idle 后 drain。

这避免“后台任务完成了”抢在用户明确指令之前改变 agent 行为。

---

## 65. Goal loop 为什么会抑制后台自动唤醒

Goal harness Active 或 Complete 时，notification drain 会丢弃相关 auto-wake，并把 completion 标为 reported。

原因是 autonomous goal continuation 自己掌握下一步。如果一个被关闭的 dev server 晚到 completion 又拉起弱模型 turn，可能偏离 goal，甚至把刚杀掉的 server 重启。

此外，goal turn 内创建的 task ID 会被记录，来自这些任务的 completion 可按 source task 独立抑制。

---

## 66. Ctrl+C 后为什么暂时抑制 task wake

交互取消建立 `task_wake_suppressed` barrier，并让 state 进入 notifications-suppressed 状态。

否则竞态可能是：

```text
用户 Ctrl+C 取消当前 turn
后台任务同时完成
auto-wake 立即又启动一个 synthetic turn
```

这会让用户觉得“明明取消了，agent 又自己开始”。真正的新用户输入会清除 suppression，允许后续 completion 以正确方式被消费。

---

## 67. Between-turn completion 是另一条交付路径

在 agent tool loop 的轮次边界，Session 会从 bridge drain 已完成但未报告的后台任务。

若没有 goal suppression，它把格式化 completion 作为 system reminder 注入下一轮模型上下文；若 goal loop active，则丢弃并 mark reported。

这条路径覆盖“进程恰好在当前模型轮次之间完成”的场景，不必先排一个全新的 synthetic turn。

---

## 68. Session shutdown 与 Subagent teardown 必须按 owner 清理

共享 backend 中可能同时有 parent、child subagent 和 sibling 的进程。

因此 `TerminalBackend` 同时提供：

- kill all foreground；
- kill foreground by owner；
- kill all background；
- kill background by owner。

Subagent 结束时只能杀自己的任务，不能误杀 parent 的 dev server。`owner_session_id` 是这条安全边界的关键字段。

---

## 69. 为什么有时要把后台任务 reparent 给父 Session

某些 child session 结束后，已明确后台化的进程可以继续由 parent 管理，而不是一律杀掉。

`reparent_notifications` 会：

- 把 `owner_session_id` 从 child 改成 parent；
- 替换 notification handle；
- 向 parent 发送 synthetic backgrounded notification，让 UI 建立 task row；
- 对 Monitor 重新启动输出 pipeline；
- 等 actor 完成 reparent ack 后才允许旧 Session shutdown。

只处理仍运行且已经 backgrounded 的任务；foreground process 保持 child owner，随后由 scoped cleanup 回收。

---

## 70. Reparent 使用 Weak backend 的原因

Monitor pipeline 需要能再次查询 backend，但它不应因为自己仍活着就永远 pin 住整个 terminal backend。

接口因此接收 `Weak<dyn TerminalBackend>`：

- parent 的正常 strong Arc 存在时，pipeline 可 upgrade；
- parent/backend 消失后，upgrade 失败，pipeline 自然停止；
- 不形成“任务为了观察自己而让 backend 永不释放”的所有权环。

---

## 71. Persistent shell state 给后台任务带来额外边界

Unix persistent shell 会通过专用 fd 恢复旧 snapshot，并从另一个 fd 读取新状态。

后台子进程可能继承 state-output fd，导致 reader 长期看不到 EOF。源码因此在 kill/shutdown 路径显式 abort state dump handle，
并对读取设置收敛策略。

这提醒我们：进程生命周期不仅是 stdout/stderr 和 child PID，还包括为 shell state 建立的辅助 pipe/task。

---

## 72. Output file 也不是无限的

完整日志相对于内存 preview 更完整，但仍有 `MAX_OUTPUT_FILE_BYTES`，默认上限很大且可由环境调整。

收敛时 actor 会 flush 并按 cap truncate 文件。文档和 UI 应把它理解为“可供进一步读取的主要日志”，而不是绝对永不丢字节的审计存储。

对于需要永久审计的 workload，应由命令自身写业务日志或接入专用日志系统。

---

## 73. OOM 与普通 exit 如何进入同一 snapshot

Linux 可选 cgroup memory guard 把 spawned process 加入受限 cgroup，并通过 memory monitor 观察压力/OOM。

无论是 exit code、signal、timeout、explicit kill 还是 OOM，最终都投影成：

- lifecycle exit status；
- `TaskSnapshot.exit_code/signal/completed`；
- `TaskOutputResult.status`；
- notification 和 tool output。

上层无需直接理解 cgroup 内部对象，但必须保留足够 signal 让模型区分失败类型。

---

## 74. 两个状态机的合并时间线

下面以“前台 build 超过 15 秒自动后台，后来成功”为例：

| 时刻 | Tool Call | Process | Registry key | Model/UI |
| --- | --- | --- | --- | --- |
| T0 | InProgress | Running | internal UUID | Bash card running |
| T0+15s | terminal result ready | Running | re-key to tool_call_id | backgrounded event |
| T0+15s+ | Terminal tool result | Running | tool_call_id | model gets task ID |
| T1 | 已结束 | Running + output grows | tool_call_id | get-output may poll |
| T2 | 已结束 | Finished/Swept | tool_call_id | TaskCompleted |
| T3 | completion consumed | tombstone/retained | tool_call_id | model gets final status once |
| T4+TTL | 已结束 | heavy state evicted | lightweight snapshot | log file remains reference |

---

## 75. 为什么“工具成功”不代表命令成功

后台 Bash tool 成功只说明：

- 命令通过校验；
- backend 成功 spawn；
- registry 已保存 task；
- task ID 已返回。

之后进程可能 exit 1、超时、OOM 或被 kill。

因此不能在收到 `BackgroundTaskStarted` 时记录“build succeeded”或触发依赖最终产物的动作。

---

## 76. 常见误解

### 误解 1：`timeout_ms` 是后台命令的运行 timeout

`get_task_output.timeout_ms` 只限制这次等待；Bash input 的 timeout 才限制进程。

### 误解 2：取消 get-output 会杀任务

它只取消 waiter；任务继续运行。

### 误解 3：`output` 是完整日志

它是受限 preview；核对 `truncated`、`raw_output_bytes` 和 `output_file`。

### 误解 4：后台任务一定有本地 PID

ACP/remote backend 可以返回 `None`。

### 误解 5：task ID 永远不同于 tool call ID

前台转后台路径当前相同。

### 误解 6：TaskNotFound 等于任务失败

它只是查询 namespace 中没有该 ID。

### 误解 7：完成通知会立刻抢占用户 turn

它受 idle gate、queue priority、cancel suppression 和 goal suppression 约束。

### 误解 8：kill 返回后 child 一定已经完全 reap

control outcome 与 lifecycle cleanup 是相关但分层的步骤。

### 误解 9：所有地方对 terminal status 的集合定义相同

当前单结果 helper、多任务汇总和 completion 消费分别使用不同 predicate，尤其对 `timed_out`、failed/cancelled 的处理不同。

---

## 77. 调试：后台命令没有出现在任务列表

按顺序检查：

1. 最终ized tool schema 是否暴露 `is_background`；
2. `enabled_background` 是否开启；
3. 命令是否因裸 `&` 被拒绝；
4. `BashTool::run` 是否走 `run_background` 分支；
5. actor 是否收到 `RunBackground`；
6. spawn 是否失败；
7. `BashExecutionBackgrounded` 是否发出；
8. UI 使用的是 task ID 还是 tool call ID；
9. 是否被错误的 owner-scoped teardown 立即清理。

---

## 78. 调试：任务一直 running 但进程已经退出

重点检查：

- actor ticker 是否仍运行且 `processes` 非空；
- `try_wait` 是否返回异常；
- stdout/stderr 是否被逃逸孙进程持有，导致 EOF 不到达；
- process group 是否正确 attach；
- lifecycle 是否停在 Exiting 而没有进入 settled；
- `REAP_GRACE` 后的 escalation/abandoned collection 是否触发；
- 查询的是 live map 还是旧 tombstone/另一个 backend。

---

## 79. 调试：日志文件有内容，但模型 preview 不更新

检查：

1. `total_bytes` 是否增长；
2. `last_notified_total` 是否过早推进；
3. `BashOutputChunk` 是否被 per-call sink 接收；
4. capped notification queue 是否丢弃 lossy chunk；
5. in-memory `maybe_truncate` 后是否仍按 total bytes 判增量；
6. `raw_output_bytes` 是否进入 TaskOutputResult；
7. UI 是否错误地用 preview string length 判断进度。

丢 streaming chunk 不一定丢事实，日志文件和最终 snapshot 仍可恢复。

---

## 80. 调试：任务完成后模型收到两次消息

重复交付通常来自去重边界失效：

- blocking waiter 已收到，但 `block_waited` 未写；
- get-output 已返回 terminal result，但 `consumed_completion_ids` 未识别；
- pending synthetic input 没有按 task ID sweep；
- notification drain 没有 mark reported；
- between-turn drain 与 auto-wake 同时认领；
- ID 在显式后台与前台转后台路径被错误映射。

最有效的日志字段是 task ID、tool call ID、PromptOrigin、notification source 和 reported/reservation state。

---

## 81. 调试：用户 Ctrl+C 后 agent 又自动开始

检查 task wake 三层 gate：

- cancel 时 `task_wake_suppressed` 是否 set；
- Session state 的 `notifications_suppressed` 是否 set；
- synthetic Prompt admission 是否尊重 barrier；
- rejected admission 是否把内容保存到 fallback/pending notification；
- 只有真实 user intake 是否清除了 suppression。

不要简单删除 completion notification；正确行为是延迟交付，而不是无条件丢事实。

---

## 82. 修改后台生命周期时必须守住的不变量

1. Tool terminal 与 process terminal 必须明确分离。
2. 每个可管理后台任务必须有稳定 task ID。
3. tool call ID、task ID、PID 和 output path 不能混为一个概念。
4. actor 是 `ProcessState` 的唯一可变所有者。
5. kill/cancel/shutdown 必须作用于进程树，而不是只作用于 shell leader。
6. 输出 preview 必须有界，真实进度计数必须单调。
7. 完成后的完整输出路径必须继续可发现。
8. blocking wait 取消后不能留下会消费 completion 的 helper。
9. 模型已通过 tool result 获知终态时，auto-wake 必须去重。
10. 用户 Prompt 的优先级必须高于后台 notification turn。
11. child Session cleanup 不能误杀其他 owner 的任务。
12. terminal notification 即使在拥塞时也应优先于 lossy progress。

---

## 83. 修改 Bash 后台分支的检查清单

- [ ] schema 与运行时 `enabled_background` 一致；
- [ ] `&` 检测符合 bash/pwsh/cmd 当前平台语义；
- [ ] background zero/omitted timeout 仍为 unbounded；
- [ ] positive background timeout 不被 foreground max 意外裁剪；
- [ ] `PYTHONUNBUFFERED` 不覆盖用户显式值；
- [ ] description 与 owner session 正确进入 request/snapshot；
- [ ] explicit background 和 transitioned background 都返回检索提示；
- [ ] remote backend 无 PID 时输出仍合法；
- [ ] notification 与 typed tool output 同步更新。

---

## 84. 修改 Terminal Actor 的检查清单

- [ ] command branch 不在 actor 内长期 await process completion；
- [ ] command/cancel 仍优先于 ticker；
- [ ] 空闲 actor 不注册周期 timer；
- [ ] stdout/stderr final drain 不丢快进程尾部；
- [ ] total bytes 与 preview truncation 独立；
- [ ] foreground->background re-key 原子完成；
- [ ] waiter 只在真实交付时标记 consumed；
- [ ] exited child/process-group handle 及时释放，避免 PID reuse；
- [ ] TTL eviction 留下可查询 tombstone；
- [ ] tombstone 不复制无界 output；
- [ ] shutdown abort 辅助 state-dump task；
- [ ] scoped kill 校验 owner session。

---

## 85. 推荐实验

### 实验一：显式后台

执行一个每秒输出一行、最终 exit 0 的命令，传 `is_background=true`。记录 tool call ID、task ID、PID 和日志路径，验证 ID 关系。

### 实验二：Ctrl+G 转后台

以前台启动长命令，触发 Ctrl+G。验证 process 未重启、PID 不变、task ID 等于 tool call ID，并能继续 query。

### 实验三：短 foreground budget

测试 backend 使用 50ms budget，验证工具先返回 auto-backgrounded，但进程随后自然完成。

### 实验四：Poll 与 Wait

对同一任务分别使用 omitted、0 和 positive timeout，测量调用耗时与 status。

### 实验五：Wait-any 清理

启动一个快任务和一个慢任务，wait-any 返回后确认慢任务的 helper waiter 已 abort，慢任务完成时仍能 auto-wake。

### 实验六：大输出

制造超过 preview limit 的输出，验证 head/tail marker、raw bytes 与日志文件；继续输出时 progress signature 应变化。

### 实验七：Kill 去重

kill 运行中任务，确认模型得到 cancelled，且不会再收到同一任务 completion synthetic turn。

### 实验八：TTL tombstone

缩短 test TTL，等待 eviction 后 query，验证 metadata 和 output path 仍在、内存 output 已清空。

### 实验九：Owner isolation

parent/child 各启动任务，执行 child scoped teardown，确认 parent task 存活。

### 实验十：取消竞态

让 completion 与 Ctrl+C 同时发生，确认 synthetic wake 被 gate，真实下一条用户输入能消费 deferred completion。

---

## 86. 推荐定向测试

| 命令 | 主要覆盖 |
| --- | --- |
| `cargo test -p xai-grok-tools --lib computer::local::terminal::tests` | actor、前后台转换、timeout、wait、kill、TTL |
| `cargo test -p xai-grok-tools --lib computer::local::terminal_snapshot_tests` | snapshot 与输出裁剪 |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::bash::tests` | Bash 参数、background 分支、timeout 与 `&` 检测 |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task_output::tests` | poll/wait、多任务、not-found 与 cancellation safety |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::kill_task::tests` | kill outcome 与 subagent fallback |
| `cargo test -p xai-grok-tools --lib notification::handle::tests` | capped queue 与 critical notification |
| `cargo test -p xai-tool-types --lib task::tests` | wire input、terminal status、progress signature |
| `cargo test -p xai-grok-shell --lib auto_wake_suppression_tests` | Session completion 消费、去重与 cancel gate |

实际结果见下一节。Shell lib test 可能受仓库中其他测试模块的编译状态影响。

---

## 87. 本文编写时的实际验证

| 命令 | 结果 |
| --- | --- |
| `cargo test -p xai-grok-tools --lib computer::local::terminal::tests` | 通过：54 passed，5 ignored（源码已标注为 CI flaky） |
| `cargo test -p xai-grok-tools --lib computer::local::terminal_snapshot_tests` | 通过：4 passed |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::bash::tests` | 通过：144 passed |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task_output::tests` | 通过：33 passed |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::kill_task::tests` | 通过：13 passed |
| `cargo test -p xai-grok-tools --lib notification::handle::tests` | 通过：5 passed |
| `cargo test -p xai-tool-types --lib task::tests` | 通过：26 passed |
| `cargo test -p xai-grok-shell --lib auto_wake_suppression_tests` | 阻塞：Shell lib test 编译失败，E0599 |

可执行的工具层与共享类型测试共 279 项通过、5 项 ignored、0 failed。5 项 ignored 不是本次运行临时跳过，
而是源码已分别标记为进程共租户或 CI 输出/flush 时序 flaky。

Shell 目标在收集 `auto_wake_suppression_tests` 前，被仓库现有文件
`crates/codegen/xai-grok-shell/src/session/acp_session_tests/tool_layer_images_bridge_tests.rs:15`
阻塞：测试调用 `STANDARD.encode(buf)`，却没有将 `base64::Engine` trait 引入作用域。
该错误与本文文档改动无关，因此本文没有修改这段代码，也不把 Shell 自动唤醒测试记作通过。

---

## 88. 自测题

1. Tool Call 已返回后，哪个对象继续拥有 OS child？
2. 显式后台与前台转后台的 task ID 如何不同？
3. foreground block budget 与 process timeout 分别做什么？
4. 为什么 output preview 截断后仍用 total bytes 推送 progress？
5. omitted `get_task_output.timeout_ms` 会不会等待 30 秒？
6. TaskNotFound 为什么不是 terminal status？
7. wait-any 为什么需要 `AbortWaitsOnDrop`？
8. `block_waited` 与 `explicitly_killed` 分别抑制哪种重复通知？
9. 为什么 completion notification 不能直接调用模型？
10. tombstone 为什么保存 output file 但清空 output？
11. owner session ID 如何保护 parent/sibling task？
12. Ctrl+C 与 task completion 同时发生时，哪一层负责阻止立即 auto-wake？

---

## 89. 本篇术语表

| 名词 | 白话解释 | 本篇中的具体含义 |
| --- | --- | --- |
| foreground | 当前调用方还在等它 | Bash tool future 尚未收到 terminal result，进程会阻塞当前工具轮次 |
| background | 调用方已拿到 task handle，进程继续 | Tool Call 已结束，actor registry 继续管理 OS process |
| auto-background | 到时间后不杀进程，而是解除前台等待 | `ForegroundTimeout` reason，返回 `auto_backgrounded` sentinel |
| foreground block budget | 一个 turn 最多被可后台化命令占住多久 | 默认 15s；到期只后台化，不是 process kill timeout |
| process timeout | 进程允许运行的上限 | 到期时按 auto-bg 配置选择后台化或终止进程组 |
| actor | 独占一组状态并串行处理消息的任务 | `LocalTerminalActor` 是所有 `ProcessState` 的唯一可变 owner |
| handle | 用来继续操作异步对象的凭证 | `BackgroundHandle` 带 task ID、日志路径和可选 PID |
| task ID | 后台 registry 的逻辑主键 | 交给 get-output、wait 和 kill；不保证等于 tool call ID |
| tool call ID | 模型工具调用的关联 ID | 连接 conversation、ACP card、notification 和原始 Bash call |
| PID | 操作系统进程号 | 只适合本机诊断，远端 backend 可为空，也存在复用风险 |
| `ProcessState` | actor 内一个进程的完整可变态 | child、pipe、buffer、file、lifecycle、waiter 与 owner 的集合 |
| `TaskSnapshot` | 任务当前状态的只读照片 | Local/ACP backend 对 query、UI 和 reminder 的统一输出 |
| `Lifecycle` | 进程从运行到收集的状态机 | 与 foreground/background 维度正交 |
| `BackgroundStatus` | 工具调用是否仍等待进程以及后台原因 | Foreground 或 Backgrounded(reason) |
| process group | 可以整体发信号的一棵相关进程 | Unix group 或 Windows Job Object，防止只杀 leader 留 descendants |
| `ProcessScope` | 更上层统一回收多个进程组的容器 | 保存 Weak 注册，让 TUI/Session teardown 能兜底清理 |
| oneshot | 只能发送一次结果的 channel | 前台 run reply、query reply 和 actor command ack |
| waiter | 等待某个任务终态的登记项 | actor 保存 deadline 与 reply，不阻塞主 loop |
| poll | 立即看一眼当前状态 | get-output timeout omitted/0 |
| blocking wait | 最多等到任务完成或 deadline | get-output positive timeout 或 legacy wait tool |
| wait-all | 所有指定任务都完成才提前返回 | 多任务 get-output 的 positive-timeout 语义 |
| wait-any | 任一指定任务完成就提前返回 | legacy `wait_tasks` 的兼容模式 |
| zombie waiter | 原调用已走，但辅助 wait 仍在后台消费结果 | 用 `AbortWaitsOnDrop` 避免 |
| tombstone | 重资源清理后保留的轻量完成记录 | 保存 metadata/status/output path，不保存 child 和完整 output |
| TTL | 一条完成运行态保留多久 | 默认 5 分钟后从 heavy ProcessState 转 tombstone |
| ring/head-tail buffer | 有界内存输出预览 | 冻结开头、持续保留最新尾部，舍弃中间 |
| raw output bytes | 任务真实累计输出字节数 | 即使 preview 长度固定也能证明任务仍有进展 |
| notification | 工具层向 Session/UI 发的异步事件 | output chunk、backgrounded、TaskCompleted 等 |
| critical notification | 拥塞时优先保留的终态事件 | `TaskCompleted` 等不能像 progress chunk 一样随便丢弃 |
| auto-wake | 后台完成后自动建立模型可处理的 synthetic turn | 受 idle、user queue、cancel 和 goal gate 控制 |
| synthetic turn | 不是用户主动输入的内部 Prompt | completion/notification drain 驱动的模型轮次 |
| completion reservation | 某通道预声明将交付一个完成结果 | 防止其他 reminder/auto-wake 同时认领 |
| reported completion | 已经向模型报告过的 task ID | 后续 completion 投影据此去重 |
| `block_waited` | blocking waiter 已实际收到结果的标记 | 阻止随后再为同一完成 auto-wake |
| `explicitly_killed` | 模型已通过 kill 操作消费任务终态 | 把状态呈现为 cancelled，并抑制完成自动唤醒 |
| reparent | 把 child Session 的存活后台任务转交 parent | 改 owner、notification handle 并重建 UI/monitor routing |
| Weak reference | 不延长目标生命周期的引用 | reparented pipeline 可观察 backend，但不能永久 pin 它 |
| reap | 收集已退出 child 的 OS 状态与资源 | 避免 zombie process，并安全释放 process-group handle |
| fail-safe cleanup | 正常路径失效时的兜底回收 | actor cancel、session scope、global process scope 多层协作 |

更多通用术语见[全局术语表](../appendices/glossary.md)。

---

## 90. 源码证据索引

| 结论 | 符号/位置 |
| --- | --- |
| Terminal backend 契约 | `computer/types.rs::TerminalBackend` |
| Handle 与 snapshot 字段 | `BackgroundHandle`、`TaskSnapshot` |
| actor command 与单 owner | `computer/local/terminal.rs::TerminalCommand`、`LocalTerminalActor` |
| 三种后台原因 | `BackgroundStatus`、`BackgroundReason` |
| 显式后台创建 | `BashTool::run` background branch、`handle_run_background` |
| 前台创建与 waiter | `LocalTerminalActor::handle_run` |
| Ctrl+G 路由 | `SessionHandle::background_foreground_command`、`BackgroundForegroundCommand` |
| 前台转后台/re-key | `transition_to_background`、`handle_background_foreground` |
| block budget 与 timeout 分支 | `poll_process` |
| non-blocking pipe 读取 | `try_read_nonblocking`、`poll_process` |
| final output drain | `drain_remaining_output` |
| preview 与 snapshot | `ProcessState::maybe_truncate`、`ring_output`、`to_task_snapshot` |
| 完成 TTL/tombstone | `poll_all_processes` eviction phase |
| poll/wait 输入语义 | `TaskOutputToolInput`、`task_output_waits` |
| 单/多任务 resolve | `run_single_task`、`run_multi_tasks`、`resolve_tasks` |
| wait helper cancellation | `AbortWaitsOnDrop` |
| wait-any/all | `wait_any_event_driven`、`wait_all_event_driven` |
| result status/进度签名 | `snapshot_to_result`、`TaskOutputResult::progress_signature` |
| kill terminal/subagent fallback | `KillTaskTool::run` |
| critical notification | `notification/handle.rs::is_critical_notification` |
| completion 去重 | `consumed_completion_ids`、`drop_pending_items_for_consumed_completions` |
| idle auto-wake | `maybe_drain_notifications`、`drain_notifications_into_turn` |
| cancel admission gate | `admit_task_completion_wake`、`task_wake_suppressed` |
| between-turn delivery | `drain_between_turn_completions` |
| owner-scoped cleanup | `kill_foreground_commands_by_owner`、`kill_tasks_by_owner` |
| reparent | `TerminalBackend::reparent_notifications`、actor implementation |
| process tree recovery | `xai-tty-utils::ProcessGroup`、`ProcessScope` |

相关学习资料：

- [Bash Tool Call 如何经过权限、执行并回到下一轮采样](02-bash-tool-call-permission-sandbox-and-result-loop.md)
- [一条 Tool Call 如何经过解析、权限、Hooks、执行并返回模型](12-tool-call-parse-permission-hooks-dispatch-output-and-model-feedback.md)
- [内置工具实现与执行语义](../03-subsystems/18-builtin-tools-execution-semantics-and-output-contracts.md)
- [跨平台 Shell、Terminal、PTY 与进程生命周期](../03-subsystems/21-cross-platform-shell-terminal-pty-and-process-lifecycle.md)
- [并发模型、Actor、Channel 与取消传播](../03-subsystems/15-concurrency-actors-channels-cancellation-and-shutdown.md)

---

## 91. 一句话复盘

后台终端任务的本质，是把“模型这次 Bash Tool Call 已经拿到可管理 handle”与“操作系统进程最终退出并被回收”拆成两个终态：LocalTerminalActor 独占 child、进程组、日志、输出预览、waiter 和 owner 状态，显式后台或前台转换产生稳定 task ID，get-output/wait/kill 通过 snapshot 管理它，而 completion reservation、reported 标记、idle notification drain、取消与 goal gate 再确保最终状态只在合适时机、尽量只一次地回到模型。
