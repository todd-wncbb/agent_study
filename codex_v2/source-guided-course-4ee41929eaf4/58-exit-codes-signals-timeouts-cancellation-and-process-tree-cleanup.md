# 58：退出码、信号、超时、取消与进程树回收——怎样可靠地结束一棵进程树

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方文档说明 Codex CLI 可以运行本地工具并支持交互式命令工作流，但没有规定信号编号、进程组、Job Object、取消宽限期或输出排空等内部实现。本章的状态与数值以当前源码为准。
>
> 官方对照资料：[Codex CLI](https://learn.chatgpt.com/docs/codex/cli)。

## 1. 本章解决什么问题

“把命令停掉”可能指七件不同的事：

```text
程序自然 return
发送 Ctrl-C / SIGINT
关闭 stdin，让程序读到 EOF
发送 SIGTERM，请程序清理后退出
等待超时后终止
取消当前 Codex turn
强制杀死根进程及其后代
```

它们的语义、可靠性和输出完整性都不同。本章回答：

- exit code 0、1、124、130、137 分别可能表示什么？
- Unix signal 与 exit code 为什么不能完全等同？
- Ctrl-C 是一个字符、终端事件，还是 SIGINT？
- timeout 与 yield timeout 有什么区别？
- `CancellationToken` 为什么不是“杀进程函数”？
- graceful termination 为什么需要宽限期和升级策略？
- 为什么只杀 shell PID 会留下 node/python 子进程？
- Unix process group 和 Windows Job Object 怎样管理后代？
- 根进程自然退出时，后台后代是否应该保留？
- terminate 后为什么仍需 drain stdout/stderr？
- Codex turn 被中断时，后台 unified-exec 进程是否一定停止？

## 2. 用“关店”建立直觉

把进程系统想成一家店：

- 正常退出：营业完成，员工主动关店；
- stdin EOF：不再接新订单；
- interrupt：按下暂停铃，请正在做事的人停下来；
- terminate：通知全店开始关门清理；
- kill：直接断总电源；
- timeout：规定时间到了，由外部触发关店；
- cancellation：上层工作已不再需要，但要由各层把意图传到底层；
- process tree cleanup：不能只让店长离开，还要处理店内所有员工；
- output drain：关门后把最后的账单收齐。

## 3. 进程结束的三个不同事实

至少要区分：

```text
根进程退出
所有相关后代退出
所有 stdout/stderr 写端关闭并被读取完
```

根 shell 可以退出，而后台 child 继续运行；child 还可能持有 stdout pipe，导致 reader 一直等不到 EOF。

## 4. Exit status 是什么

操作系统提供进程终止状态。常见两类：

- 正常退出并携带整数 code；
- 在 Unix 上被 signal 终止。

Rust 的 `ExitStatus::code()` 在被 Unix signal 终止时可能返回 None，因此不能总假设“每次都有普通退出码”。

## 5. 0 通常表示成功

Unix 和许多跨平台 CLI 约定：

```text
0     成功
非 0  失败、未满足条件或特殊状态
```

但非 0 的精确含义由程序定义。例如 grep 的“没有匹配”与“读取失败”可以使用不同 code。

> Exit code 是协议字段，不是自然语言。应查目标程序约定，而不是把所有非 0 都翻译成同一种错误。

## 6. `exit 1` 与 crash 不一样

```bash
exit 1
```

是程序或 shell 主动返回 code 1。它可能表示验证失败、业务条件不成立或笼统错误。

而 segmentation fault、abort 或 SIGKILL 属于不同终止原因。最终展示可能被规范化为整数，但诊断时应保留原因来源。

## 7. Codex 怎样规范化 signal exit

`codex-rs/utils/pty/src/process.rs` 的 `exit_code_from_status()`：

```text
若 status.code() 存在 → 原 code
Unix signal 存在      → 128 + signal number
否则                  → -1
```

所以常见：

```text
SIGINT  = 2  → 130
SIGTERM = 15 → 143
SIGKILL = 9  → 137
```

这是常见 shell 风格规范化，不表示 OS 原始状态只有一个整数。

## 8. 不要跨平台硬解释所有大于 128 的 code

`128 + signal` 是 Unix/shell 惯例。Windows 的进程终止和 exit code 模型不同，应用也可以主动返回 130 或 137。

因此：

```text
exit_code == 137
```

可以提示“可能是 SIGKILL”，但只有结合平台和终止路径才能确认。

## 9. Signal 是什么

Unix signal 是内核向进程传递的异步通知。不同 signal 的默认行为和可处理性不同：

| Signal | 常见意图 | 程序能否捕获/清理 |
|---|---|---|
| SIGINT | 用户中断当前前台工作 | 通常可以 |
| SIGTERM | 请求程序终止 | 通常可以 |
| SIGKILL | 立即强制终止 | 不可以捕获或忽略 |
| SIGHUP | terminal/session 消失等 | 通常可以 |

精确行为仍取决于程序注册的 handler。

## 10. Interrupt、terminate、kill 是三个强度

可用递增强度理解：

```text
Interrupt
  → 停止当前动作，程序可能继续存在

Terminate
  → 请求整个进程结束，并给清理机会

Kill
  → 不再等待合作，强制结束
```

Codex 当前公共 `ProcessSignal` 只暴露 `Interrupt`；强制停止通过 terminate/kill 路径处理，而不是让任意 signal 穿透所有后端。

## 11. Ctrl-C 不总是 byte 0x03

在真实 terminal 中，用户按 Ctrl-C，terminal line discipline 通常把它转换为发给前台 process group 的 SIGINT，而不是简单把字符交给应用。

但在不同 PTY、pipe、Windows console 或远程代理中，实现路径可能不同：

- 向进程组发送 SIGINT；
- 通过 console control 机制；
- 后端不支持精确 interrupt，只能 terminate；
- 某些交互程序自行读取 raw control byte。

## 12. Codex 的 `write_stdin` 怎样表示 Ctrl-C

Unified exec 的非 TTY 进程不接受普通 stdin 数据，但如果 input 等于内部的 interrupt 表示，就调用：

```rust
process.interrupt().await
```

本地 backend 转为 `PtyProcessSignal::Interrupt`；远程 backend 转为 exec-server protocol 的 `ProcessSignal::Interrupt`。

所以这里不是把任意文本拼进 shell，而是走结构化 signal 路径。

## 13. Unix Pipe 的 interrupt

Pipe backend 保存 process group ID。收到 Interrupt 时调用：

```text
interrupt_process_group(pgid)
  → killpg(pgid, SIGINT)
```

目标是整组，而不只是最外层 shell。

## 14. Unix PTY 的 interrupt

PTY backend 也尽量保存 process group ID，Interrupt 同样发送给该 group。

这符合终端中“中断前台作业”的直觉：shell、pipeline 和同组 child 应看到一致的中断，而不是只停掉任意一个 PID。

## 15. Windows interrupt 的退化

当前 Pipe Windows terminator 对 Interrupt 直接调用 kill。某些 driver/ConPTY 后端也可能把 interrupt 视为终止，或者明确返回 Unsupported。

因此“发送 Ctrl-C 后程序有机会优雅处理”不是跨平台绝对保证。调用方必须接受后端能力差异。

## 16. 关闭 stdin 是另一种协作协议

许多程序以 EOF 作为“输入完成”：

```text
读取 stdin
  → 得到 EOF
  → 处理已收到数据
  → 正常退出
```

这时关闭 stdin 比 SIGINT 更符合协议。例如接收完整输入流的 formatter、compressor 或 parser。

但服务器、REPL 或忽略 stdin 的程序不会因为 EOF 必然退出。

## 17. Timeout 是外部 deadline

Timeout 表示：

> 允许操作运行到某个持续时间；deadline 到达仍未完成，就由外部采取终止路径。

它不等于程序自己返回“超时”，也不保证程序收到一种特定 signal。

## 18. 传统 shell exec 的默认 timeout

`codex-rs/core/src/exec.rs` 当前定义：

```text
DEFAULT_EXEC_COMMAND_TIMEOUT_MS = 10_000
```

`ExecExpiration` 支持：

```text
Timeout(duration)
DefaultTimeout
Cancellation(token)
TimeoutOrCancellation { timeout, cancellation }
```

`wait_with_outcome()` 明确返回 TimedOut 或 Cancelled，使后续可选择不同终止策略。

## 19. Timeout 和 cancellation 同时发生怎么办

`TimeoutOrCancellation` 使用 biased `tokio::select!`，源码将 cancellation 分支写在 timeout 前。

当两者同时 ready 时，优先报告 Cancelled。这种 tie-breaking 是可观察语义，应由代码明确决定而不是依赖调度偶然性。

## 20. 传统 exec 超时时怎样停止

当前 timeout 分支：

1. 强制 kill child process group；
2. 对直接 child 调用 `start_kill()`；
3. 构造 synthetic termination status；
4. 将最终 tool output 规范化为 `timed_out = true`、exit code 124；
5. 返回 timeout 类错误，并附带已收集输出。

124 是常见 timeout 命令约定，不是 Unix signal number。

## 21. 为什么 group kill 后还要 kill child

进程组信息可能已经变化、查询失败或存在平台差异。对 group 和直接 child 同时采取 best-effort 清理，可以覆盖更多边界情况。

清理代码常出现冗余保护，因为“漏杀一个后台进程”的成本比重复请求一个已退出进程更高。

## 22. 传统 exec 取消时更温和

Cancellation 分支当前先：

```text
SIGTERM → process group
```

然后最多等待：

```text
50 ms
```

若未及时退出，再升级为 SIGKILL/group kill + direct child kill。

这体现 graceful-then-forceful 策略：先允许 TERM-aware 程序清理，但给宽限设置硬上限。

## 23. 为什么取消宽限只有 50 ms

这是当前实现选择，不是通用最佳值。交互式 agent 的取消需要快速响应；太长会让用户感觉“明明按了停止却还在跑”。

代价是某些程序来不及完成复杂清理。因此终止协议必须按产品延迟目标和数据安全需求设计。

## 24. CancellationToken 不是 signal

Tokio `CancellationToken` 是进程内协作通知：

```text
token.cancel()
  → 等待 token.cancelled() 的 Rust task 被唤醒
```

它不会自动：

- 发送 SIGTERM；
- 杀 OS child；
- abort 任意 Tokio task；
- 关闭文件或 socket。

每个监听者必须明确响应并执行清理。

## 25. 合并 cancellation token

`cancel_when_either(first, second)` 创建新的 token，并等待任意一个来源取消：

```text
first cancelled  ┐
                  ├→ combined.cancel()
second cancelled ┘
```

这适合组合“用户取消”和“网络策略拒绝”等独立来源，同时保留统一下游接口。

## 26. 取消 Tokio task 也不是杀进程

`JoinHandle::abort()` 只是停止对应异步 task 的继续执行。若 task 已经启动外部 child，而 child handle 的 Drop 没有可靠终止语义，OS 进程可能继续运行。

因此安全设计需要：

- cancellation 通知；
- child owner；
- Drop/explicit shutdown；
- 进程树 containment；
- 最终 wait/reap。

## 27. Turn interruption 的两阶段处理

当前 task abort 流程大意是：

1. `task.cancellation_token.cancel()`；
2. 等 task 自己观察取消并结束；
3. 最多等待 100 ms；
4. `task.handle.abort()`；
5. 调用 task 类型自己的 `abort(...)` cleanup；
6. 记录 `TurnAborted` 与中断历史 marker。

这比直接 abort task 多了一次协作退出机会。

## 28. Turn 被取消不等于所有后台进程被杀

Unified exec 在初次 yield 前，如果进程仍活着，会先存入共享 process store。源码注释明确说明：这样即使 turn 被 interrupt，最后一个 Arc 也不会被丢弃并触发 terminate。

因此：

```text
取消当前模型 turn
```

与：

```text
终止某个后台 terminal process
```

是两个不同操作。

## 29. 为什么保留后台进程

开发服务器、REPL、watcher 和长构建可能在首个观察窗口后继续运行。用户打断模型生成，不一定是在要求关闭这些有状态进程。

把 process 放进 store 后，可以：

- 后续 poll；
- `write_stdin`；
- 单独 terminate；
- session shutdown 时统一清理。

## 30. yield timeout 不是 process timeout

Unified exec 的 `yield_time_ms` 表示：

> 这次工具调用等待输出/退出多久，然后把当前结果交还上层。

到时进程可以继续后台运行。它不是“运行超过该时间就 kill”。

对比：

| 机制 | 到期后的主要动作 |
|---|---|
| 传统 exec timeout | 终止 process group，返回 timeout |
| unified exec yield | 返回当前输出，保留活进程 |
| empty write_stdin poll | 等一段观察窗口，再返回 |
| cancellation | 通知上层工作停止；是否杀进程取决于 owner/path |

## 31. Session shutdown 会终止全部统一执行进程

`shutdown_session_runtime()` 在结束任务和 conversation 后调用：

```rust
unified_exec_manager.terminate_all_processes().await
```

Manager 先从 store drain 所有 entries，再逐个 unregister network approval 并 terminate。

这是 scope owner 清理：进程可以跨 turn，但不应无界跨越其所属 session 生命周期。

## 32. 单独终止后台 terminal

Manager 的 `terminate_process(process_id)`：

- 找不到 ID → false；
- 尚未退出 → `terminate_confirmed().await`；
- 再检查 store 中仍是同一个 Arc；
- 初始 exec 调用仍 active 时暂不移除；
- 否则移除 entry 并清理关联状态。

Arc identity check 避免 process ID 被复用后误删新 entry。

## 33. `terminate` 与 `terminate_confirmed`

Unified process 的普通 `terminate()`：

- 本地同步触发 handle terminate；
- 远程通过 spawned async task 发送 terminate；
- 随后结束本地 output task。

`terminate_confirmed()` 对远程会 await RPC，错误向上传播，再标记 exit 和完成本地终止。

需要向用户确认“已终止”时，应选择能等待远端确认的路径。

## 34. `request_terminate` 与 `terminate`

底层 `ProcessHandle`：

```text
request_terminate
  → 调用 killer.kill()
  → 保留 reader/writer/wait tasks
  → 允许输出继续 drain

terminate
  → 先 request_terminate
  → abort reader、writer、wait helper tasks
```

前者重视尾部输出；后者重视立即释放执行资源。

## 35. Drop 是最后一道安全网

`ProcessHandle::drop()` 调用 `terminate()`。这能降低 owner 意外消失时 child 泄漏的概率。

但 Drop cleanup：

- 不能 async await；
- 错误通常无法返回给调用者；
- 可能牺牲输出排空；
- 不应替代正常显式 shutdown。

## 36. 什么是 PID

PID 是 process identifier，只标识一个进程。Shell 启动 pipeline 时可能形成：

```text
shell PID 100
├─ producer PID 101
├─ consumer PID 102
└─ helper PID 103
```

只杀 PID 100 不保证 101–103 自动退出。

## 37. 什么是 process group

Unix process group 把多个相关进程放在一个 PGID 下。终端和 job control 可对整组发送 signal。

Codex 的目标是让一次命令的 shell 与常规后代处于可整体管理的 group，从而：

```text
killpg(pgid, SIGINT/SIGTERM/SIGKILL)
```

而不是逐个猜测 child PID。

## 38. Pipe child 怎样建立隔离 group

Unix Pipe spawn 的 `pre_exec` 调用 `detach_from_tty()`：

- 优先 `setsid()` 建立新 session；
- 若得到 EPERM，则 fallback 到 `setpgid(0, 0)`；
- Linux 还设置 parent-death signal；
- 关闭不应继承的 file descriptors。

新 session/process group 避免非交互 child 意外继承 Codex 自己的 controlling TTY。

## 39. PTY child 怎样成为终端会话

Unix PTY path 在 `pre_exec` 中：

- 重置若干 signal disposition；
- 清空 inherited signal mask；
- 调用 `setsid()`；
- 将 PTY slave 设为 controlling terminal；
- 记录 child/group ID 供 signal 与 kill。

这既建立交互终端语义，也建立进程组控制边界。

## 40. 为什么要重置信号状态

Child 在 fork 后会继承父进程的一些 signal disposition/mask。如果 Codex 父进程忽略了某 signal，而 child 继续继承，交互程序可能无法按正常 shell 预期响应 Ctrl-C 或 TERM。

因此 exec 前恢复默认处理与空 mask，是把 child 放回正常命令行运行环境的一部分。

## 41. Linux parent-death signal

Pipe child 在 Linux 使用：

```text
prctl(PR_SET_PDEATHSIG, SIGTERM)
```

如果原父进程死亡，内核向 child 发送 SIGTERM。实现还在设置后重新检查 parent PID，处理父进程恰好在 fork/setup 期间退出的竞态。

它是额外安全网，不替代正常 group cleanup。

## 42. 后代可以逃出 process group

程序可以调用 `setsid()`、改变 process group 或启动真正 detached daemon。此后原 PGID 的 kill 不一定覆盖它。

所以“杀进程树”通常是 best effort，可靠性来自多层：

- 组/作业 containment；
- parent-death 机制；
- handle ownership；
- session shutdown；
- 测试 detached child 情况；
- 必要的平台 fallback。

## 43. macOS 为什么有 member fallback

当前 macOS helper 在对整个 group 发 SIGTERM/SIGKILL 遇到 PermissionDenied 时，会：

1. 枚举该 PGID 的成员 PID；
2. 再确认每个 PID 当前仍属于该 group；
3. 逐个发送相同 signal；
4. group leader 最后处理。

再次确认 PGID 可降低 PID 复用或成员变化导致误杀的风险。

## 44. Windows 没有 Unix process group 等价语义

Windows 路径主要使用 Job Object 管理一组进程。Job 可以：

- 接纳 root 和后代；
- 在关闭最后 handle 时终止成员；
- 用 `TerminateJobObject` 终止全部成员；
- 配置是否允许 child break away。

这与 Unix PGID 目标相似，但 API 和边界不同。

## 45. `KILL_ON_JOB_CLOSE`

当前 `JobObject::create()` 设置：

```text
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
JOB_OBJECT_LIMIT_BREAKAWAY_OK
```

前者使最后一个 Job handle 关闭时终止仍受控的成员；后者允许符合条件的后代显式脱离，以兼容某些程序。

`create_without_breakaway()` 会去掉 breakaway 许可，提供更强 containment。

## 46. Windows assignment race

普通 Pipe path 先 spawn root，再把它 assign 到 Job。两步之间存在小竞态：root 可能极快创建后代，后代不一定进入 Job。

源码明确把该路径标为 best effort。

更强路径可使用 suspended spawn：

```text
CREATE_SUSPENDED
  → assign Job
  → resume process
```

从而避免 child 在 containment 建立前运行。

## 47. ConPTY 的原子 Job 绑定

ConPTY 创建进程时通过扩展 startup attribute 把 Job 与 pseudo-console 一起传给 `CreateProcessW`，实现比普通 spawn 后 assign 更紧密的 containment。

测试会同时覆盖 Pipe 的 best-effort 与 ConPTY 的原子路径，确认 terminate 尽量杀死 descendants。

## 48. 根进程自然退出时为什么保留 Windows 后代

开发服务器常由 shell 启动后台 child 后自己退出。如果 root 正常结束就自动关闭 Job 并 kill 全部 child，会破坏这种合法后台行为。

当前 Windows Job 在检测 root 正常完成时调用 `preserve_descendants()`：

- 移除 kill-on-close；
- 禁用该 Job 的显式 terminate；
- 让已启动后代继续运行。

这是一项产品语义选择，而不是 Job Object 的必然行为。

## 49. Preserve 与 terminate 的竞态

`JobObject` 用 Mutex 包住：

```text
检查 preserve 状态
调用 Job API
更新状态
```

因此 root-exit preserve 与外部 terminate 并发时，由先获得锁的一方决定结果，避免出现一半更新的状态。

## 50. 为什么 exit 后输出 pipe 可能不关闭

后台 descendant 可能继承 root 的 stdout/stderr fd：

```text
root 已退出
descendant 仍持有 pipe write end
reader 等 EOF
```

因此传统 exec 的 reader task 不能无限等待。

## 51. 传统 exec 的 I/O drain 上限

当前：

```text
IO_DRAIN_TIMEOUT_MS = 2_000
```

等待 stdout/stderr task 超过 2 秒时，abort reader task，并返回空的该 stream fallback，以避免整个 agent 永久挂住。

这是“最终一定返回”优先于“无限等待所有后代输出”。

## 52. 强制 terminate 为什么可能丢尾部输出

底层 `terminate()` 不仅 kill child，还 abort reader/writer/wait helpers。在途 bytes 可能尚未从 OS pipe/PTY 搬到上层 buffer。

如果需要完整尾部，优先使用：

```text
request terminate
  → 等 exit
  → 等 streams closed/drain
  → 最后释放 helpers
```

但所有等待都应有硬上限。

## 53. 远程 signal 与 terminate 是两个 RPC

Exec-server protocol 分开：

```text
process/signal { processId, signal: interrupt }
process/terminate { processId }
```

SignalResponse 只表示请求处理完成；TerminateResponse 的 `running` 表示请求时是否仍有正在启动/运行的目标。

远程调用成功不应被误解为所有输出已经送达；仍需通过 read/closed 状态完成收尾。

## 54. Starting 状态怎样终止

Exec-server 收到 terminate 时：

- Running：标记 termination_requested，终止 session；
- Starting：从 process map 移除；
- 不存在：返回 running=false。

若启动任务稍后完成，它会检查 map 中是否仍是同一个 Starting token；若已移除，就立即 terminate 新生成的 process，避免“取消启动但进程随后冒出来”。

## 55. 为什么需要 identity check

仅凭 process ID 检查不够：

```text
旧启动 A 被取消
ID 位置发生变化或被复用
旧异步结果晚到
```

Exec-server 用 `Arc::ptr_eq` 验证仍是同一个 start token；core manager 终止后移除 entry 时也验证同一 process Arc。

这是一种 generation/identity fencing。

## 56. Natural exit 与 termination_requested

Exec-server 记录 `termination_requested`，用于区分：

- 进程自然结束；
- 用户/系统明确请求终止；
- backend shutdown 导致终止。

Telemetry 和清理状态需要知道“为什么结束”，不能只看最后 exit code。

## 57. Unknown process 的幂等语义

对 signal，Starting 或不存在当前返回空成功响应；对 terminate，不存在返回 `running=false`。

这是控制 API 常见的幂等风格：目标已经消失时，“让它停止”的最终条件已经满足，不必把每次重复请求都变成严重错误。

## 58. 常见错误：把 timeout 当 sleep

```text
sleep 10 秒
```

只会延迟当前 task，不会自动为目标操作建立 deadline，也不会处理超时后的 cleanup。

真正 timeout 需要：

```text
deadline race
  → 标记原因
  → 发终止请求
  → 等有限宽限
  → 强制升级
  → drain/reap
```

## 59. 常见错误：只 abort reader

停止读取 stdout 并不会停止 child。相反，child 继续写满 pipe 后可能阻塞，并永远留在后台。

Reader cancellation 与 process termination 必须由同一个 owner 协调。

## 60. 常见错误：只杀根 shell

```bash
sh -c 'server & wait'
```

只杀 sh 可能留下 server。应优先建立并终止 process group/Job，而不是事后递归猜测 `/proc` 或进程列表。

## 61. 常见错误：SIGTERM 后无限等待

程序可以：

- 捕获后卡住；
- 忽略 SIGTERM；
- deadlock 在 cleanup；
- child 仍持有资源。

Graceful termination 必须有 deadline，超时后升级为不可忽略的强制路径。

## 62. 常见错误：看到 137 就断言 OOM

137 常由 `128 + SIGKILL` 得到，OOM killer 是产生 SIGKILL 的一种可能来源；手工 kill、timeout cleanup、sandbox 或其他管理器也可能产生同样结果。

正确诊断需要平台日志、终止原因和资源指标。

## 63. 常见错误：取消 turn 就认为服务器已停

Unified exec 会有意把活进程存进 session store。Turn interruption 可能只停止当前模型/工具等待，不关闭后台 server。

应使用后台 terminal 的显式 terminate，或在 session shutdown 时统一清理。

## 64. 常见错误：Drop 是完整 shutdown

Drop 适合兜底，但不能 await remote acknowledgement、完整 drain 或把错误报告给用户。

高质量生命周期通常是：

```text
显式 shutdown
  → 等待确认和排空
Drop
  → 处理遗漏路径
```

## 65. 端到端终止状态机

```text
Running
  ├─ natural exit ───────────────→ Exited
  ├─ stdin EOF → program exits ─→ Exited
  ├─ Interrupt ─→ Running/Exited（取决于程序）
  ├─ Cancel ─→ TERM ─→ Exited
  │                 └─ grace expires → KILL → Exited
  └─ Timeout/Terminate ─→ KILL tree → Exited

Exited
  └─ stdout/stderr drain complete → Closed/Finalized
```

实际实现还有 Starting、Failed、sandbox-denied、remote-disconnected 等状态，但核心问题始终是：谁拥有下一步转换责任？

## 66. 审查终止代码的清单

```text
这是 interrupt、stdin EOF、TERM、KILL、timeout 还是 task cancel？
原因是否被单独记录，而不是只剩一个 exit code？
目标是 PID、process group、Job，还是远程 process ID？
child 在开始运行前是否已进入 containment？
正常 root exit 时应该保留还是杀死后台 descendants？
协作终止的 grace 是多少，何时升级？
取消 Rust task 是否会留下 OS process？
Drop 是否只是兜底，是否还有显式 async shutdown？
Starting 与 terminate 并发时有没有 identity fencing？
exit 后是否继续 drain output？
pipe 被 descendant 持有时有没有 drain deadline？
重复 terminate 是否幂等？
session 结束时是否清理所有跨-turn process？
```

## 67. 理解检查

### 问题 1

为什么 exit code 130 常被解释为 SIGINT？

<details><summary>参考答案</summary>

Unix/shell 常用 `128 + signal` 规范化，SIGINT 编号通常为 2。但仍需结合平台和实际终止路径确认。

</details>

### 问题 2

为什么关闭 stdin 不等于 kill？

<details><summary>参考答案</summary>

它只让读取方看到 EOF。程序可选择正常结束、继续其他工作或完全忽略 stdin。

</details>

### 问题 3

为什么 cancellation token 不能独自清理 child？

<details><summary>参考答案</summary>

Token 只是进程内通知。必须有监听者收到后发送 signal/terminate、等待并回收 child。

</details>

### 问题 4

为什么先 SIGTERM 再 SIGKILL？

<details><summary>参考答案</summary>

TERM 给程序保存状态和释放资源的机会；KILL 为不合作或卡死的程序提供有界结束保证。

</details>

### 问题 5

为什么只等待 root exit 可能永远收不到 stdout EOF？

<details><summary>参考答案</summary>

后台 descendant 可能继承并继续持有 stdout/stderr pipe 写端。

</details>

### 问题 6

Unified exec 的 yield 到时为什么不 kill？

<details><summary>参考答案</summary>

Yield 是观察窗口，不是进程 deadline。活进程会存入 session process store，供后续交互。

</details>

### 问题 7

Windows 为什么要 suspended spawn？

<details><summary>参考答案</summary>

避免 root 在被 assign 到 Job Object 前先运行并创建逃出 containment 的后代。

</details>

### 问题 8

为什么 Drop cleanup 仍不够？

<details><summary>参考答案</summary>

Drop 不能异步等待远程确认和完整排空，也难以报告失败；它应是显式 shutdown 的兜底。

</details>

## 68. 本章词汇表与代码名称翻译

| 代码或术语 | 中文理解 | 在本章中的作用 |
|---|---|---|
| Exit status | 终止状态 | OS 对进程如何结束的记录 |
| Exit code | 退出码 | 正常退出或规范化后交给上层的整数 |
| Signal | 信号 | Unix 内核向进程发送的异步通知 |
| SIGINT | 中断信号 | 通常对应 Ctrl-C，程序可捕获 |
| SIGTERM | 终止请求 | 给程序协作清理机会的常用 signal |
| SIGKILL | 强制杀死 | 程序不能捕获、阻塞或忽略 |
| Interrupt | 中断当前工作 | 不一定意味着整个进程必须退出 |
| Terminate | 终止 | 请求或强制结束目标生命周期 |
| Grace period | 宽限期 | 协作终止后升级强制 kill 前的有限等待 |
| Timeout / deadline | 超时/截止点 | 外部为完成时间设置的硬边界 |
| Yield | 暂时返回 | 结束本次观察，不结束后台 process |
| CancellationToken | 取消令牌 | Rust task 间广播协作取消意图 |
| `JoinHandle::abort` | 中止异步任务 | 停止 Rust future，不天然停止 OS child |
| PID | 进程标识 | 标识单个 OS process |
| PGID / process group | 进程组标识/进程组 | Unix 对相关进程整体发 signal 的单位 |
| Session / `setsid` | OS 会话/建立新会话 | 脱离 controlling TTY 并建立 job-control 边界 |
| Controlling TTY | 控制终端 | Unix session 的终端控制与前台作业来源 |
| Signal mask/disposition | 信号掩码/处理方式 | 决定 signal 被阻塞、忽略、默认处理或捕获 |
| Parent-death signal | 父进程死亡信号 | Linux 父进程消失时由内核通知 child |
| Process tree | 进程树 | root、children、grandchildren 的关系 |
| Descendant | 后代进程 | child 及更深层级进程 |
| Detached process | 脱离进程 | 建立新 session/group 后逃离原控制范围的进程 |
| Job Object | 作业对象 | Windows 管理和整体终止进程集合的内核对象 |
| Kill on job close | Job 关闭即终止 | 最后 handle 关闭时杀死仍受控成员 |
| Breakaway | 脱离 Job | Windows child 从 Job containment 脱离的能力 |
| Suspended spawn | 挂起式启动 | 先创建但不运行，完成 containment 后 resume |
| Reap / wait | 回收/等待 | 取得终止状态并释放 OS process bookkeeping |
| Drain | 排空 | 退出后继续读取在途 stdout/stderr |
| Synthetic exit status | 合成终止状态 | 为 timeout/cancel 等外部路径构造内部状态 |
| Idempotent terminate | 幂等终止 | 重复请求也保持“目标已停止”的最终条件 |
| Identity fencing | 身份栅栏 | 用 Arc/token 确认晚到结果仍属于同一实例 |
| Starting / Running / Exited / Closed | 启动中/运行中/已退出/流已关闭 | 进程与输出生命周期的分离状态 |
| Best effort | 尽力而为 | 尽可能完成，但承认 OS/竞态/权限不能绝对保证 |

## 69. 源码检查点

1. `codex-rs/utils/pty/src/process.rs`
   - 看 `ProcessSignal`、`exit_code_from_status`、request/confirmed terminate、Drop。
2. `codex-rs/utils/pty/src/process_group.rs`
   - 看 setsid/setpgid、SIGINT/SIGTERM/SIGKILL、Linux parent-death 与 macOS fallback。
3. `codex-rs/utils/pty/src/pipe.rs`
   - 看 Pipe group terminator、Windows Job fallback、pre_exec containment。
4. `codex-rs/utils/pty/src/pty.rs`
   - 看 PTY group ID、signal reset、controlling terminal 和 group kill。
5. `codex-rs/utils/pty/src/win/job.rs`
   - 看 Job flags、suspended spawn、assign/resume、preserve 与 terminate 锁。
6. `codex-rs/utils/pty/src/win/mod.rs`
   - 看 root natural exit 时 preserve descendants 与 Job-backed killer。
7. `codex-rs/utils/pty/src/win/psuedocon.rs`
   - 看 ConPTY CreateProcessW 如何同时绑定 pseudo-console 与 Job。
8. `codex-rs/core/src/exec.rs`
   - 看 10 秒默认 timeout、ExecExpiration、50 ms cancel grace、124 与 2 秒 I/O drain。
9. `codex-rs/core/src/unified_exec/process.rs`
   - 看本地/远程 interrupt、terminate、terminate_confirmed 与 output task。
10. `codex-rs/core/src/unified_exec/process_manager.rs`
    - 看 yield、process store、后台保活、单个/全部 terminate 和 Arc identity。
11. `codex-rs/exec-server-protocol/src/protocol.rs`
    - 看 process/signal 与 process/terminate 的独立协议形状。
12. `codex-rs/exec-server/src/local_process.rs`
    - 看 Starting/Running terminate、termination_requested 和启动竞态。
13. `codex-rs/core/src/tasks/mod.rs`
    - 看 turn cancellation、100 ms graceful interruption、handle abort 与 TurnAborted。
14. `codex-rs/core/src/session/handlers.rs`
    - 看 session shutdown 时任务、unified processes、code mode 与 MCP 的清理顺序。
15. `codex-rs/utils/pty/src/tests.rs`
    - 看 background child、detached reader、interrupt、late output 与 group cleanup 测试。
16. `codex-rs/utils/pty/src/windows_tests.rs`
    - 看 Pipe/ConPTY descendant termination、Job assignment 和 Ctrl-C 行为。
17. `codex-rs/core/src/unified_exec/process_manager_tests.rs`
    - 看 terminate confirmation、poll/cancel 与 process store 行为。
18. `codex-rs/exec-server/src/local_process.rs` 的测试模块
    - 看 terminate-before/after-exit、shutdown 与 late output 状态。

## 70. 一句话总结

> 可靠结束命令不是“对一个 PID 调用 kill”，而是先区分自然退出、EOF、interrupt、timeout 与 cancellation，再由明确 owner 对整棵 process group/Job 执行有界的 graceful-to-forceful 清理，同时等待终止状态并有限排空输出。
