# 56：进程标准输入输出、Pipe、PTY、实时输出与背压——Codex 怎样和正在运行的命令对话

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方文档说明 Codex CLI 可以在本地仓库中运行已经安装的工具、展示执行中的命令和 diff，并支持交互式终端工作流；但它没有规定 Pipe、PTY、channel 容量或输出排空的内部实现。本章中的数值、状态机和跨平台细节以当前源码为准。
>
> 官方对照资料：[Codex CLI](https://learn.chatgpt.com/docs/codex/cli)。

## 1. 本章解决什么问题

当 Codex 执行：

```bash
npm run dev
```

或者启动 Python REPL 后继续输入：

```text
print("hello")
```

背后需要回答：

- 子进程的 stdin、stdout、stderr 分别接到哪里？
- Pipe 和 PTY 有什么区别？
- 为什么同一程序被 Codex 启动后，颜色或缓冲行为可能变化？
- stdout 没内容、stderr 疯狂输出时，会不会卡死？
- 读取到的一个 chunk 是否等于一行？
- UI 来不及消费实时输出时，生产者等待还是丢数据？
- 进程已经退出，为什么还不能马上发布最终结果？
- `write_stdin` 是把字符串放进 shell，还是写给已有进程？
- Ctrl-C、关闭 stdin、kill 和退出分别意味着什么？
- 远程 exec-server 丢了一条实时通知后，怎样补回？

核心认识是：

> 运行命令不是“调用函数并得到一个 String”，而是管理一个同时拥有输入流、输出流、退出状态和资源生命周期的异步系统。

## 2. 用“舞台演出”建立直觉

把子进程想成舞台上的演员：

- stdin 是后台递给演员的纸条；
- stdout 是演员对观众说的话；
- stderr 是演员向技术人员报告的问题；
- exit status 是演出结束后交回的结果单；
- Pipe 像三条独立传声筒；
- PTY 像真的舞台和观众席，演员知道自己面对终端；
- channel 是工作人员之间的有限传送带；
- backpressure 是后方来不及搬时，前方必须减速；
- output drain 是落幕后仍把最后几句话收完。

“演员离场”和“最后一句话已经传到观众耳中”不是同一事件。

## 3. 三条标准流

传统进程默认有三条标准流：

| 名称 | Unix fd | 方向 | 常见用途 |
|---|---:|---|---|
| stdin | 0 | 父进程 → 子进程 | 用户输入、管道数据 |
| stdout | 1 | 子进程 → 父进程 | 正常结果 |
| stderr | 2 | 子进程 → 父进程 | 诊断、警告、错误 |

它们是 byte streams，不自带：

- 行边界保证；
- UTF-8 保证；
- 消息边界；
- “一写对应一读”的保证。

## 4. `stdio` 不是“终端界面”的同义词

`stdio` 是 standard input/output 的统称。它可以连接到：

- 当前终端；
- OS pipe；
- 文件；
- `/dev/null`；
- PTY slave；
- 网络代理背后的远程进程。

因此看到 `stdout`，只能知道它是标准输出通道，不能推断它一定显示在真实屏幕上。

## 5. Codex 当前有三种本地启动选择

`codex-rs/sandboxing/src/spawn.rs` 的主分支可以概括为：

```text
tty == true
    → PTY spawn
tty == false && stdin_open == true
    → Pipe spawn，stdin 保持可写
否则
    → Pipe spawn，stdin 立即关闭/接 null
```

这里 `tty` 与 `stdin_open` 是两个不同问题：

- 是否让程序看到一个终端？
- 是否允许以后继续写输入？

## 6. Pipe 是什么

Pipe 是由 OS 提供的单向 byte channel。普通 pipe 模式中：

```rust
command.stdin(Stdio::piped());
command.stdout(Stdio::piped());
command.stderr(Stdio::piped());
```

父进程拿到另一端，就可以异步写 stdin、读 stdout、读 stderr。

Pipe 很适合：

- 非交互式编译和测试；
- 机器消费 stdout；
- 单独保存 stderr；
- 不需要终端控制序列的命令。

## 7. `Stdio::piped()` 实际做了什么

它不是说“现在立刻读取输出”，而是要求 spawn 时建立 pipe 并把子进程端接到相应 stdio。

spawn 后父进程再取得：

```rust
let stdin = child.stdin.take();
let stdout = child.stdout.take();
let stderr = child.stderr.take();
```

`take()` 把句柄所有权从 `Child` 结构中移出，交给独立 reader/writer task。

## 8. Pipe 能保留 stdout 与 stderr 身份

Codex 的 pipe helper 分别建立：

```text
stdout reader → stdout channel
stderr reader → stderr channel
```

因此较底层调用方可以知道某块输出来自哪条流。`ExecOutputStream` 也明确有：

```rust
Stdout
Stderr
Pty
```

但是将两条流聚合后，跨流的精确先后顺序只能是实际观察到的顺序，不是程序源代码中的绝对顺序。

## 9. 为什么 stdout 和 stderr 必须并发读取

错误示例：

```text
先把 stdout 读到 EOF
再开始读 stderr
```

如果子进程大量写 stderr，stderr pipe 的 OS buffer 可能先填满；子进程阻塞在 stderr write，无法退出，也无法关闭 stdout。父进程又在等 stdout EOF，于是形成死锁。

源码为 stdout 和 stderr 分别启动 reader task。测试 `pipe_drains_stderr_without_stdout_activity` 专门让 stderr 大量输出而 stdout 没活动，验证它仍能被排空。

## 10. 每次读取 8192 bytes，不代表每块是一行

Pipe 和 PTY reader 都使用大约 8192-byte 的临时 buffer：

```rust
let mut buf = vec![0u8; 8_192];
let n = reader.read(&mut buf).await?;
```

一次得到的 chunk 可能：

- 只有半行；
- 包含十几行；
- 在一个 UTF-8 字符中间结束；
- 把 ANSI escape sequence 切成两块；
- 与子进程的一次 `write()` 大小不同。

chunk 是运输单位，不是业务记录。

## 11. `read()` 返回 0 才表示当前流 EOF

Reader loop 的主要分支是：

```text
Ok(0)        → 该输出流关闭，结束 reader
Ok(n)        → 发送这 n 个 bytes
Interrupted  → 重试
其他错误     → 结束 reader
```

EOF 说明“这条 pipe/PTY reader 没有更多 bytes”，不自动说明整个进程刚刚退出。后台后代进程若继承了输出 fd，流甚至可能在根进程退出后继续保持打开。

## 12. 普通 Pipe 输入是怎样写进去的

源码没有让每个调用者直接抢占 `ChildStdin`，而是建立 writer channel：

```text
调用者 → mpsc<Vec<u8>> → 单一 writer task → child stdin
```

writer task 对每块执行 `write_all` 和 `flush`。这样同一 stdin 只有一个顺序 owner，避免多个 task 同时改写同一 writer。

## 13. 为什么 writer channel 容量是有限的

Pipe 和 PTY 的本地 stdin queue 当前容量为 128 个 `Vec<u8>`。

当 queue 满时：

```rust
writer_tx.send(bytes).await
```

会等待空位，而不是继续无限堆积。这就是一种 backpressure：下游写不动，压力通过 await 传回上游。

但容量按“消息个数”计算；如果单个 `Vec<u8>` 没有独立大小限制，128 个超大消息仍可能占很多内存。

## 14. 关闭 stdin 与发送空字符串不同

关闭最后一个 writer sender 会让 writer task 结束并释放 child stdin。子进程随后可能读到 EOF。

这和下面几件事都不同：

- 发送 `""`：没有新 byte；
- 发送 `"\n"`：提交一个换行；
- 发送 Ctrl-D byte：是否被解释为 EOF 取决于终端模式；
- kill：强制终止进程。

`ProcessHandle::close_stdin()` 通过移除内部 sender 来关闭输入通道。

## 15. 为什么非交互命令常把 stdin 接到 null

若一个本应自动完成的命令意外等待用户输入，它可能永远挂住。`spawn_process_no_stdin` 把 stdin 设为 `Stdio::null()`，让读取立即得到 EOF。

这是一种很实用的 fail-fast：

```text
自动化命令不应等待人 → 启动时就关闭 stdin
```

需要交互时再显式选择 PTY 或 pipe stdin。

## 16. PTY 是什么

PTY 是 pseudo-terminal，中文常称“伪终端”。它模拟真实终端设备，通常分为：

- master：Codex 持有，用来读写和 resize；
- slave：子进程看到，连接其 stdin/stdout/stderr。

它不只是另一种 pipe。子进程调用 `isatty()` 时会得到不同答案，并据此改变行为。

## 17. PTY 的 master/slave 数据流

```text
Codex 写 master ───────────────► child 从 slave stdin 读

Codex 读 master ◄────────────── child 向 slave stdout/stderr 写

Codex resize master ───────────► child 观察新的 rows/cols
```

终端驱动还可能处理 echo、行编辑、控制字符和信号。

## 18. PTY 为什么把三条 stdio 接到同一设备

Unix 保留 inherited fd 的 PTY 路径明确 clone slave：

```text
stdin  → slave clone
stdout → slave clone
stderr → slave clone
```

三者是三个 owned handle，但指向同一个 PTY slave device。结果是 stdout 和 stderr 在 master 输出侧自然合并。

所以 PTY 模式通常不能像 pipe 一样可靠拆回“这一段来自 stderr”。

## 19. 为什么程序在 PTY 中表现不同

许多程序会检测 stdout 是否是 terminal：

- terminal：显示颜色、spinner、进度条、交互提示；
- pipe：关闭颜色，采用机器友好输出；
- terminal：stdout 可能按行 flush；
- pipe/file：stdout 可能积累更大 buffer 才 flush。

因此“命令明明已经打印，Codex 为什么暂时没看到”可能不是 Codex reader 慢，而是子进程自己的用户态 buffering 尚未 flush。

## 20. 一个最小的行为差异例子

教学示意：

```python
import os, sys
print("isatty:", sys.stdout.isatty())
```

可能得到：

```text
Pipe: isatty: False
PTY:  isatty: True
```

真实结果由平台和启动方式决定，但这个例子说明程序可以感知连接类型。

## 21. PTY 中的输入可能被 echo

终端 line discipline 常会把键入字符回显到输出。于是 Codex 写入：

```text
print("hello")\n
```

聚合输出里可能既有输入的回显，又有程序产生的 `hello`。这不是重复执行，而是终端交互语义。

具体 echo/canonical mode 可由子进程和终端设置改变，不能假设所有 PTY 都完全相同。

## 22. PTY 适合哪些任务

典型用途：

- Python、Node 等 REPL；
- 需要回答提示的 CLI；
- 依赖 TTY 检测的程序；
- 需要 Ctrl-C 或 terminal resize 的长进程；
- 全屏或半交互终端程序。

纯编译、格式化和测试通常更适合 pipe，因为输出更稳定、stdout/stderr 可分离，也较少 ANSI 控制序列。

## 23. 默认终端大小是 24 × 80

`TerminalSize::default()` 当前返回：

```rust
rows: 24
cols: 80
```

终端宽度会影响：

- 自动换行；
- 表格布局；
- progress bar；
- 全屏程序渲染；
- snapshot 测试结果。

因此 rows/cols 是进程输入环境的一部分，不只是 UI 装饰。

## 24. Resize 是怎样传递的

`ProcessHandle::resize` 有两种后端：

- 本地 PTY master：调用 portable-pty resize，或 Unix `TIOCSWINSZ` ioctl；
- driver-backed session：调用 backend 提供的 resizer callback。

若进程没有连接 PTY，也没有 resizer，返回“process is not attached to a PTY”。Pipe 没有终端尺寸概念。

## 25. Windows 使用 ConPTY

Windows 的 pseudo-console 由 ConPTY 提供。Codex 的 PTY abstraction 尽量让上层仍看到同一套：

```text
writer sender
stdout receiver
exit receiver
resize
interrupt/terminate
```

但底层控制字符、进程树和 handle 生命周期仍有平台差异，不能把 Unix terminal 细节直接套到 Windows。

## 26. Windows 输入为什么需要 normalize

`WindowsTtyInputNormalizer` 处理两个重要差异：

- `\n` 转为 carriage return `\r`，但已有 CRLF 不重复提交；
- backspace `0x08` 转为 DEL `0x7f`。

它还要记住“上一块是否以 `\r` 结束”，因为 CR 和 LF 可能分别落在两次 `write_stdin` 中。

这再次说明 chunk 边界不能当作字符或按键边界。

## 27. Pipe 和 PTY 为什么共享 `SpawnedProcess`

两种 spawn 最终都返回：

```rust
pub struct SpawnedProcess {
    pub session: ProcessHandle,
    pub stdout_rx: mpsc::Receiver<Vec<u8>>,
    pub stderr_rx: mpsc::Receiver<Vec<u8>>,
    pub exit_rx: oneshot::Receiver<i32>,
}
```

上层可以用同一流程管理输入、输出和退出。差异封装在底层：PTY 的 `stderr_rx` 通常为空，因为输出已经合并到 master。

## 28. `ProcessHandle` 拥有哪些资源

它集中拥有：

- stdin writer sender；
- child terminator；
- reader task 和 abort handles；
- writer task；
- wait task；
- exit flag 与 exit code；
- PTY master/slave keepalive handles；
- 可选 resize callback。

这是一份“资源所有权清单”。读生命周期代码时，先列 owner，比只追函数调用更容易发现泄漏。

## 29. 为什么 PTY handle 必须保持存活

源码注释指出 slave 过早关闭可能使进程收到 Control+C 等终端影响。因此 `ProcessHandle` 保存 `_pty_handles`，即使上层暂时不直接调用它们。

下划线不是“不重要”，而是告诉 Rust：“字段主要为了生命周期持有，不一定被普通逻辑读取。”

## 30. `request_terminate` 与 `terminate` 不一样

`request_terminate`：

```text
请求 kill child/process group
保留 reader/writer helper tasks
允许调用方继续读到 EOF
```

`terminate`：

```text
先 request_terminate
再 abort reader/writer/wait helper tasks
```

如果目标是收集最后输出，过早 abort reader 会丢 tail；如果目标是强制清理卡死资源，则需要完整 terminate。

## 31. Drop 为什么会 terminate

`ProcessHandle` 与 `UnifiedExecProcess` 的 `Drop` 都走终止清理路径。这是 RAII：owner 消失时，尽量不让子进程和 helper task 成为孤儿。

但 Drop 不能异步等待完整网络协议，所以远端确认式终止还有 `terminate_confirmed().await` 这样的显式路径。

## 32. 为什么要终止进程组而不只杀根 PID

Shell 常启动编译器、测试 runner、watcher 等子进程。只 kill shell PID，后代可能继续：

- 占用端口；
- 写文件；
- 保持 stdout fd 打开；
- 消耗 CPU；
- 让任务一直不能 close。

Unix pipe/PTY backend 按 process group 中断或终止；Windows 尝试用 Job Object 管理进程树，并在做不到时降级为 root process termination。

## 33. Interrupt、kill 与 exit code

三者不要混淆：

- interrupt：请求进程像收到 Ctrl-C 一样协作中断；
- kill/terminate：更强制地结束进程或进程组；
- exit code：进程结束后的结果，不是终止动作。

Unix 中若进程由 signal 结束，helper 会把它映射为 `128 + signal`；没有可用 code 时使用 `-1`。

## 34. 本地输出经过哪些层

```text
child stdout/stderr 或 PTY master
        │ 每次最多约 8192 bytes
        ▼
各自有界 mpsc channel
        │
        ▼
combine_output_receivers
        │ broadcast channel
        ▼
UnifiedExecProcess local output task
        ├── HeadTailBuffer
        ├── output_notify
        └── 再 broadcast 给实时 watcher
```

每一层都有不同职责，不要只看到最后的 `String`。

## 35. `mpsc` 与 `broadcast` 的区别

| Channel | 消费者模型 | 本章用途 |
|---|---|---|
| `mpsc` | 多 producer、一个 receiver | reader → 聚合器，writer queue |
| `broadcast` | 每个 subscriber 各有游标 | 同一输出给多个实时消费者 |
| `oneshot` | 只发送一次 | exit code |
| `watch` | 只关心最新状态并可等变化 | process state、exit seen、pause |
| `Notify` | 不携带 payload 的唤醒 | “有输出/已关闭，请重新检查状态” |

选择 channel 前应先问：每条消息必须只处理一次、让所有人看见，还是只需知道最新状态？

## 36. 有界 `mpsc` 怎样形成真正背压

`read_output_stream` 使用：

```rust
output_tx.send(chunk).await
```

若下游 mpsc 满，reader task 暂停读取。接着 OS pipe buffer 可能变满，最终子进程的 `write()` 也会阻塞。

这是一条完整压力传播链：

```text
慢消费者 → mpsc 满 → reader 停读 → OS buffer 满 → child write 变慢
```

它保护内存，但也意味着消费者若永久停止，子进程可能无法继续。

## 37. `broadcast` 满时为什么不是同一种背压

Tokio broadcast 为每个 receiver 保存有限历史。慢 receiver 落后太多时，旧消息被覆盖；下一次 `recv()` 返回 `Lagged(n)`。

这不是等待生产者，而是让慢消费者承认自己丢过消息。

源码多个本地 bridge 对 `Lagged(_)` 选择继续，因此实时观察链可能缺少部分 chunk。最终是否还能恢复，要看是否存在独立 retained/replay source。

## 38. 本地输出链与远程输出链的恢复能力不同

本地 PTY/pipe 合并后主要依赖 broadcast 和当前进程内的 head-tail transcript；subscriber lag 时，没有协议 sequence 回读源替它自动补齐所有原始 bytes。

远程 exec-server 输出则带 `seq`，并保留有界 replay buffer。Core 检测到：

- `Lagged`；
- sequence gap；
- 某些旧 peer 字段缺失；

会调用 `process/read(after_seq=last_seq)` 对账。

## 39. Sequence number 解决什么问题

每个远程 output、Exited、Closed event 共享递增 `seq`：

```text
1 stdout chunk
2 stderr chunk
3 exited
4 closed
```

消费者保存 `last_seq`，就能判断：

- `seq <= last_seq`：重复或迟到，忽略；
- `seq == last_seq + 1`：连续；
- `seq > last_seq + 1`：有缺口，需要 read/reconcile。

Sequence 不是时间戳；它表达该 process event log 内的逻辑顺序。

## 40. Exec-server 保留多少可重放输出

每个进程当前同时受两个上限约束：

```rust
RETAINED_OUTPUT_BYTES_PER_PROCESS = 1 MiB
RETAINED_OUTPUT_CHUNKS_PER_PROCESS = 50_000
```

加入新 chunk 后，从队首淘汰，直到 byte 数和 chunk 数都不超限。

双上限很重要：即使每个 chunk 只有 1 byte，海量小对象也会带来结构和 JSON value 开销。

## 41. `process/read` 是 push 的补充

实时 notification 是 push；`process/read` 是 pull/replay。请求包含：

```rust
after_seq: Option<u64>
max_bytes: Option<usize>
wait_ms: Option<u64>
```

它可以：

- 从某个 seq 之后读取；
- 限制本次响应规模；
- 没变化时等待一段时间，形成 long polling；
- 返回 exited、exit code、closed 和 failure 状态。

可靠流常需要 push 的低延迟和 pull 的恢复能力同时存在。

## 42. `max_bytes` 有一个值得注意的语义

`process/read` 为避免把首个新 chunk 永远饿死，当前逻辑允许第一个 chunk 即使超过 `max_bytes` 也进入响应；只有已经收集了至少一个 chunk 后，下一块才因超预算停止。

因此 `max_bytes` 更像“响应批次目标上限”，不是任意单 chunk 的绝对硬上限。协议生产者还需要单独限制 chunk size。

## 43. `exited` 和 `closed` 为什么分开

`exited` 表示根进程已有 exit code。

`closed` 表示：

```text
exit code 已知
并且 stdout/stderr 两个输出 reader 都已到 EOF
```

只有两者都满足，exec-server 才发布 Closed。这避免把“根进程退出”错误地当成“最后输出已经完全传完”。

## 44. `open_streams` 是一个小型 barrier

`RunningProcess` 初始记录 `open_streams: 2`。每个 stdout/stderr forwarding task 结束时减一。

`maybe_emit_closed` 只有在：

```text
closed == false
open_streams == 0
exit_code.is_some()
```

时才发布 Closed。

这就是 barrier：三个条件的到达顺序可以不同，但最终状态只发布一次。

## 45. 为什么退出后还可能有输出

几种常见原因：

- exit notification 比 reader drain 更早到达；
- OS pipe 中已有 bytes 尚未读完；
- backend 转发队列里还有 chunk；
- 子进程的后代继承了 stdout/stderr handle；
- 网络 event 重排。

`spawn_from_driver` 在看到 exit 后不使用固定 timer 猜测完成，而是继续等 driver 关闭 broadcast sender。源码测试明确验证 exit 后 50 ms 才到达的 `tail` 仍被保留。

## 46. Core 的实时 watcher 为什么还有 100 ms grace

Unified exec streaming watcher 收到 exit cancellation 后启动：

```rust
TRAILING_OUTPUT_GRACE = 100 ms
```

如果 `output_closed` 很快到达，它立即做 final drain；如果 backend 永远不给 close，100 ms 是防止 watcher 永久等待的 fallback。

所以这里是“双路径”：明确 close 优先，grace 只负责兜底。

## 47. 为什么 AtomicBool 和 Notify 要配合

`Notify` 只表示“可能发生过变化”，不是持久状态。若先检查 bool、后注册等待，中间恰好 notify，就可能错过唤醒。

源码先创建/enable notified future，再检查 `output_closed` 的 atomic，并用 Release/Acquire 顺序发布和读取状态。

心智模型是：

```text
AtomicBool 保存事实
Notify 负责叫醒睡眠者
```

只用其中一个通常不足以同时表达状态和无丢失唤醒。

## 48. 实时 delta 怎样处理 UTF-8 边界

Watcher 先把 bytes 放入 `pending: VecDeque<u8>`，再尝试每次最多取 8192 bytes 的 prefix。

若 prefix 是合法 UTF-8，就整段发出；若前面存在完整字符、结尾切到多 byte 字符，则先取 `valid_up_to()` 之前的部分。

这会尽量避免在已有合法前缀时切断字符，但它不是一个会无限等待字符补齐的完整 streaming decoder。

## 49. 真正非法 UTF-8 会怎样

`split_valid_utf8_prefix_with_max` 在 `valid_up_to() == 0` 时至少取 1 byte，防止坏 byte 或恰好位于队首的不完整字符永远堵住 pending。

因此：

- 前面已有合法字符时，合法 prefix 会先被分出；
- 队首无法形成合法 UTF-8 时，至少发送 1 byte，而不是无限等待；
- 真正无效的数据仍会向前推进；
- 最终聚合展示通过 `from_utf8_lossy` 处理无法解释的 byte。

“保持活性”和“完美保存文本”在任意 shell bytes 场景中需要取舍。

## 50. 为什么实时 delta 数量也有限

单次 command 当前最多发 10,000 个 `ExecCommandOutputDelta` event。超过后，源码仍把有效 prefix 写进最终 transcript，但不再逐块发 UI delta。

这把两件事分开：

- live animation/event 数量有界；
- terminal result 仍可从有界 transcript 得到。

否则一个不断输出极小 chunk 的程序会制造无界事件风暴。

## 51. 最终 transcript 为什么仍使用 HeadTailBuffer

实时 channel 有容量，远程 replay 有容量，最终聚合也必须有容量。Unified exec 的 `HeadTailBuffer` 当前保留最多 1 MiB 主体：

```text
前半段 head + 省略标记 + 后半段 tail
```

这保证命令输出再大，送入模型或最终 event 的主体不会无限增长。第 55 章已经详细解释 byte 截断和 UTF-8 转换。

## 52. `write_stdin` 写给谁

它不是启动一条新 shell command，而是找到 `process_id` 对应的现有 session，然后把 bytes 写进该 session 的输入通道。

所以必须先有仍存活的后台进程：

```text
exec_command → process_id
write_stdin(process_id, "...")
```

进程已经退出、被淘汰或属于别的 session 时，会得到 unknown/stdin-closed 类错误。

## 53. Unified exec 为什么只允许向 TTY 写普通输入

当前 unified `write_stdin` 逻辑中：

- `tty == true`：普通 input 写给进程；
- `tty == false`：只把单个 Ctrl-C (`\u{3}`) 解释为 interrupt；
- 其他非空输入：返回 stdin closed。

底层 exec-server 本身支持显式 `pipe_stdin`，但 unified exec 本地启动时传的是 `stdin_open: tty`。不要把底层能力自动等同于当前工具暴露能力。

## 54. 远程 stdin 为什么需要 `write_id`

网络断开时可能出现：

```text
server 已写入 "yes\n"
response 在途中丢失
client 重试同一请求
```

如果没有幂等键，子进程会收到两次输入。Exec-server 为每次 write 携带 `write_id`，每个进程记住最近 4096 个已接受 ID；重复 ID 返回 Accepted，但不重复写 bytes。

## 55. `reserve()` 为什么出现在远程写入路径

源码先：

```rust
let permit = writer_tx.reserve().await?;
```

取得 queue 容量，再重新检查 `write_id`，然后同步 `permit.send(...)` 并在下一次 await 前记住 ID。

它同时处理：

- queue backpressure；
- 并发重复 write；
- RPC handler 在 await 点被取消；
- “已经写入但没记幂等状态”的窗口。

## 56. `write_stdin` 的空输入实际上是 poll

当 input 为空时，调用不会写任何 byte，而是等待并收集新输出。

当前时间规则大意是：

- 初始/非空交互最小 yield 250 ms，非空最多 30 s；
- 空 poll 最小 5 s；
- 空 poll 的最大值由 background timeout 配置限制；
- Windows 初次 exec 还有 10 s 的 yield floor。

因此工具返回“仍在运行”不代表出错，只代表本次观察窗口结束。

## 57. 为什么同一进程的 poll 和 write 要串行

`write_stdin` 获取该 process 的 `interaction_lock`。源码注释说明，不同 terminal session 可以并发，但同一 session 的 read/write 不能重叠，因为它们共享：

- 会被 drain 的 output buffer；
- process lifecycle；
- terminal event 发布；
- process store 清理。

若两个 poll 同时 drain，一个可能拿走另一个预期的输出。按 process 串行化让“哪次调用消费哪些 bytes”可推理。

## 58. 输出收集如何等待而不忙轮询

`collect_output_until_deadline` 的循环是：

```text
锁住 output buffer
→ drain 当前内容
→ 若为空，先注册 Notify future
→ 等 output / exit / close / deadline / pause change
→ 被唤醒后重新检查权威状态
```

它不会每 10 ms 主动查看一次，从而避免 busy polling。

## 59. 进程退出后的短暂 drain

Poll 收到 exit signal 后，不一定立即返回。如果 output 尚未标记 closed，它最多再等本次剩余时间和 50 ms 中较小者，让最后 bytes 有机会进入 buffer。

这与实时 watcher 的 100 ms grace 是不同层的策略：

- poll 收集器：`POST_EXIT_CLOSE_WAIT_CAP = 50 ms`；
- streaming watcher：`TRAILING_OUTPUT_GRACE = 100 ms` fallback；
- exec-server：以 Exited + 两条 stream EOF 形成明确 Closed。

看到两个相似 timer 时，不要默认它们重复。

## 60. 一条端到端背压链应怎样审查

按以下顺序逐层问：

```text
child write
  ↓ OS pipe/PTY buffer 是否有限
reader.read
  ↓ reader → mpsc 是 await 还是 try_send
merge/bridge
  ↓ broadcast lag 是丢失还是有 replay
retained buffer
  ↓ byte 和 chunk count 是否双重有界
RPC notification
  ↓ outbound queue 满时是否 await
UI event
  ↓ event count、payload size 是否有限
model result
  ↓ byte/token truncation 是否明确标记
```

“某一层有界”不能证明整条链有界或无损。

## 61. 哪些地方可能丢实时输出

准确区分三种情况：

1. **生产端还没 flush**：bytes 根本没进入 OS pipe；
2. **transport subscriber lag**：bounded broadcast 覆盖旧消息；
3. **产品主动截断**：head-tail 或 event count policy 有意省略。

远程 seq/replay 可以修复仍在 retention window 内的通知缺口；超过 replay 上限的最老 chunk 无法凭 sequence 自动重建。

## 62. 实时输出、最终输出和模型输出不是同一个集合

| 表面 | 目标 | 可能的限制 |
|---|---|---|
| 实时 delta | 让用户看到进展 | 8192-byte chunk、10,000 events、channel lag |
| 最终 terminal event | 给 UI 一个终态 | HeadTailBuffer、late drain |
| tool response | 给模型继续推理 | output token/byte policy |
| exec-server replay | 修复通知缺口 | 1 MiB、50,000 chunks |

调试“UI 看到了但模型没看到”时，要沿这些不同消费者分别检查。

## 63. 常见故障如何定位

### 情况一：命令长时间没有输出

检查：

- 程序是否因 stdout 不是 TTY 而采用全缓冲？
- 是否正在等待 stdin，而本次启动把 stdin 接到 null？
- 是否只写 stderr，但 stderr reader 没启动？
- 是否卡在下游 backpressure？

### 情况二：只缺最后几行

检查 Exited 与 Closed 的顺序、reader EOF、output task close signal、final drain 和 grace timer。

### 情况三：输出中有重复输入

检查 PTY echo，而不是先断定命令执行两次；远程写则检查 `write_id` 是否复用。

### 情况四：输出颜色或排版不一样

检查 Pipe/PTY、`isatty()`、TERM/NO_COLOR 和 terminal rows/cols。

### 情况五：远程输出出现 seq gap

调用 `process/read(after_seq)` 对账，并确认缺口是否仍处于 retention window。

## 64. 常见错误

### 错误一：把一个 chunk 当成一行

必须自己按 newline 或协议 frame 重组。

### 错误二：顺序读完 stdout 再读 stderr

可能因另一条 pipe 填满而死锁；两条流并发 drain。

### 错误三：认为 bounded channel 一定无损

`mpsc.send().await` 通常施加背压；`broadcast` 的慢 receiver 可能收到 Lagged。

### 错误四：收到 exit 就立即关闭 reader

OS/transport 中可能还有 tail；等待明确 output close 或有限 grace。

### 错误五：把 PTY 当成“能输入的 Pipe”

PTY 会改变 `isatty`、缓冲、颜色、echo、信号和 resize 语义。

### 错误六：只 kill 根进程

后代可能继续持有资源和输出 fd；按平台管理 process group/job。

### 错误七：远程 stdin 重试没有幂等键

响应丢失会导致输入执行两次。

### 错误八：给每层都放一个无界 queue

突发输出会转化为无界内存；容量和满载策略必须明确。

### 错误九：静默停止实时 event

最终结果必须仍明确表示截断/省略，避免把 partial output 当完整输出。

### 错误十：把工具 poll 超时当成进程超时

yield window 结束后进程可以仍在后台运行；真正 terminate 是另一动作。

## 65. 理解检查

### 问题一

为什么一个大量写 stderr、完全不写 stdout 的进程，可能让“先读 stdout 再读 stderr”的实现死锁？

<details>
<summary>参考答案</summary>

stderr pipe buffer 填满后，子进程阻塞在 stderr write，无法退出并关闭 stdout；父进程却一直等 stdout EOF。并发 drain 两条流才能打破这个环。

</details>

### 问题二

为什么 PTY 模式很难重新分离 stdout 和 stderr？

<details>
<summary>参考答案</summary>

子进程的 stdin、stdout、stderr 都接到同一个 PTY slave device；Codex 从 master 侧看到的是已经合并的 byte stream。

</details>

### 问题三

`mpsc` channel 满和 `broadcast` receiver lag 有什么不同？

<details>
<summary>参考答案</summary>

`mpsc.send().await` 通常让 producer 等待，形成背压；broadcast producer 继续前进，慢 receiver 的旧消息被覆盖并收到 Lagged。

</details>

### 问题四

为什么有 `Exited` 后还需要 `Closed`？

<details>
<summary>参考答案</summary>

根进程退出不代表 stdout/stderr 已经 EOF；pipe 中可能仍有 bytes，后代也可能继续持有 fd。Closed 表示退出已知且所有输出流都结束。

</details>

### 问题五

远程实时通知丢了一条后，Codex 怎样发现并补救？

<details>
<summary>参考答案</summary>

事件带递增 seq。消费者发现 Lagged 或 seq gap 后，按 last_seq 调用 process/read，从 exec-server 的有界 retained output 中回读缺失事件。

</details>

### 问题六

空字符串 `write_stdin` 会关闭 stdin 吗？

<details>
<summary>参考答案</summary>

不会。在 unified exec 中它用于 poll 新输出。关闭 stdin 是移除/关闭 writer sender 的独立操作。

</details>

### 问题七

为什么远程写入要在 `permit.send` 后、下一次 await 前记录 `write_id`？

<details>
<summary>参考答案</summary>

这样 bytes 一旦同步进入 queue，幂等记录也在 handler 再次可能被取消前完成，减少“已经写入但重试又写一次”的窗口。

</details>

## 66. 本章词汇表与代码名称翻译

| 英文或代码名 | 字面意思 | 在本章中的具体含义 |
|---|---|---|
| Process | 进程 | 正在运行并拥有地址空间、stdio 和退出状态的程序实例 |
| Child process | 子进程 | 由 Codex/exec-server spawn 的命令进程 |
| stdio | 标准输入输出 | stdin、stdout、stderr 三条约定通道的总称 |
| stdin | 标准输入 | fd 0，父进程写给子进程的 bytes |
| stdout | 标准输出 | fd 1，子进程产生的正常结果 bytes |
| stderr | 标准错误 | fd 2，子进程产生的诊断 bytes |
| File descriptor / fd | 文件描述符 | Unix 进程引用打开文件、pipe、terminal 等资源的整数句柄 |
| Pipe | 管道 | OS 提供的单向 byte stream，可分别连接三条 stdio |
| `Stdio::piped()` | 建立管道 stdio | spawn 时为某条标准流创建 pipe |
| `Stdio::null()` | 空设备 stdio | stdin 立即 EOF，或输出被丢弃的设备连接 |
| PTY | Pseudo-terminal | 模拟真实终端、支持 isatty、控制字符和 resize 的设备对 |
| TTY | Teletype/terminal | 程序所观察到的终端设备语义 |
| PTY master/slave | 伪终端主/从端 | Codex 持有 master，子进程 stdio 接 slave |
| ConPTY | Console Pseudo Terminal | Windows 的伪控制台设施 |
| `isatty()` | 是否为终端 | 程序判断某个 fd 是否连接 terminal 的检查 |
| Line discipline | 终端行规程 | 处理 echo、行编辑和控制字符的终端规则 |
| Echo | 回显 | 把输入字符再次出现在终端输出中的行为 |
| Canonical mode | 规范/按行模式 | 终端累计到换行后再把输入交给程序的模式 |
| ANSI escape sequence | ANSI 转义序列 | 控制颜色、光标和终端显示的 byte sequence |
| Terminal size | 终端尺寸 | rows/cols，影响程序布局和换行 |
| Resize / ioctl | 调整尺寸/设备控制 | 更新 PTY 行列数的操作 |
| Chunk | 数据块 | reader 或 channel 一次运送的 bytes，不等于一行 |
| EOF | End Of File | 某条输入/输出流已没有后续 bytes |
| Flush | 刷新 | 请求把用户态缓冲继续写向底层 stream |
| Buffering | 缓冲 | 暂存 bytes 后批量读写的机制 |
| Line buffered | 行缓冲 | 遇到 newline 时通常 flush 的输出策略 |
| Backpressure | 背压 | 下游变慢后让上游等待或减速的压力传播 |
| `mpsc` | Multi-producer, single-consumer | 多发送者、单接收者的有界工作 channel |
| `broadcast` | 广播 channel | 每个 subscriber 各自接收消息，慢者可能 Lagged |
| `oneshot` | 单次 channel | 只交付一个 exit code 等结果 |
| `watch` | 最新值 channel | 保存最新 process 状态并通知变化 |
| `Notify` | 唤醒通知 | 不携带业务 payload，只提示重新检查状态 |
| `Lagged(n)` | 落后 n 条 | broadcast receiver 的旧消息已被容量覆盖 |
| Sequence / `seq` | 顺序号 | 远程 process event log 的递增逻辑位置 |
| Replay | 重放/回读 | 按 after_seq 从 retained output 重新取得事件 |
| Reconciliation | 对账 | 发现实时事件缺口后读取权威状态补齐 |
| Retention | 保留 | 为重放暂存的有限输出窗口 |
| Eviction | 淘汰 | 超过 byte/chunk 上限时移除最老输出 |
| Long polling | 长轮询 | 没变化时让 read 请求等待一段时间 |
| `after_seq` | 此顺序之后 | 请求只返回某个已见 event 以后的变化 |
| `write_id` | 写入幂等 ID | 重试同一 stdin write 时避免重复投递 bytes |
| `reserve()` / permit | 预留容量/许可 | 等待并占有 mpsc queue 的一个空位 |
| Exit status / code | 退出状态/码 | 子进程结束后报告的结果 |
| Interrupt | 中断 | 类似 Ctrl-C 的协作式信号动作 |
| Terminate / kill | 终止/杀死 | 强制停止进程或进程组的动作 |
| Process group | 进程组 | Unix 中用于整体 signal shell 及其后代的单位 |
| Job Object | 作业对象 | Windows 中用于管理和终止一组进程的机制 |
| Drain | 排空 | 进程退出后继续消费已经在途的输出 |
| Grace period | 宽限期 | 等最后输出/close 的有限额外时间 |
| `open_streams` | 未关闭输出流数 | stdout/stderr EOF barrier 的计数器 |
| `output_closed` | 输出已关闭 | reader forwarding 已结束的持久状态标志 |
| HeadTailBuffer | 头尾缓冲 | 固定保存输出前部和尾部、丢弃中间的有界结构 |
| Delta | 增量 | 相对于先前状态新增的一小块实时输出 |
| Transcript | 执行记录 | 为最终终态聚合保留的命令输出 |
| Poll / yield | 轮询/暂时返回 | 等一段时间收集输出，进程可继续在后台运行 |
| Interaction lock | 交互锁 | 让同一 process 的 write、poll 和终态发布串行化 |
| RAII | 资源随 owner 生命周期管理 | handle Drop 时自动 terminate/cleanup |

## 67. 源码检查点

建议按以下顺序阅读：

1. `codex-rs/sandboxing/src/spawn.rs`
   - 看 `tty`、`stdin_open` 怎样选择 PTY、可写 pipe 或 null stdin。
2. `codex-rs/utils/pty/src/pipe.rs`
   - 看三条 `Stdio`、8192-byte reader、三个 mpsc channel、并发 stdout/stderr drain。
3. `codex-rs/utils/pty/src/pty.rs`
   - 看 master/slave、三条 stdio 接同一 slave、writer、resize 和平台分支。
4. `codex-rs/utils/pty/src/process.rs`
   - 看 `ProcessHandle` owner、close stdin、request/force terminate、Drop 和公共返回结构。
5. `codex-rs/utils/pty/src/windows_input.rs`
   - 看跨 chunk 的 CRLF 与 backspace normalization。
6. `codex-rs/utils/pty/src/tests.rs`
   - 看 REPL、pipe stdin round-trip、stderr-only drain、split streams、late output、resize 和进程组测试。
7. `codex-rs/core/src/unified_exec/process.rs`
   - 看本地/远程统一 handle、broadcast、HeadTailBuffer、state、Lagged 和 reconciliation。
8. `codex-rs/core/src/unified_exec/async_watcher.rs`
   - 看 8192-byte UTF-8 prefix、无法解码时至少推进 1 byte、10,000 delta cap、100 ms grace 与 output drain barrier。
9. `codex-rs/core/src/unified_exec/head_tail_buffer.rs`
   - 看最终 transcript 的 1 MiB head/tail 保留。
10. `codex-rs/core/src/unified_exec/process_manager.rs`
    - 看 `write_stdin`、interaction lock、yield window、50 ms post-exit drain 和 process pruning。
11. `codex-rs/exec-server-protocol/src/protocol.rs`
    - 看 `ExecParams`、`ProcessOutputChunk`、`ReadResponse`、`WriteParams`、stream 和状态字段。
12. `codex-rs/exec-server/src/local_process.rs`
    - 看 seq、1 MiB/50,000-chunk retention、long poll、write ID、Exited/Closed barrier。
13. `codex-rs/exec-server/src/client.rs`
    - 看远程 event reorder buffer、gap、transport reconnect 与 read fallback。
14. `codex-rs/core/src/unified_exec/async_watcher_tests.rs`
    - 看 close 优先和 grace fallback 的时序测试。
15. `codex-rs/core/src/unified_exec/mod_tests.rs`
    - 看 stdin、poll、terminate race、pipe exit code 与 output retention。

## 68. 一句话总结

阅读命令执行与终端代码时，依次追问：

```text
子进程看到的是 Pipe 还是 PTY，stdin 是否打开？
stdout 与 stderr 分开还是已经在 PTY 中合并？
reader 是否并发排空两条流，chunk 是否被误当成一行？
每层 channel 是 mpsc、broadcast、watch、oneshot 还是 Notify？
channel 满时会 await、丢旧消息、拒绝，还是有 seq/replay 恢复？
根进程 Exited 后，何时才能确认所有输出流 Closed？
最后输出通过明确 close 还是 grace timer 排空？
stdin 重试是否有 write_id，queue reservation 与记录顺序是否原子可推理？
interrupt、close stdin、terminate 和 Drop 分别清理哪些资源？
实时 delta、最终 transcript、远程 replay 和模型 tool output 各有什么独立上限？
```

能回答这些问题，就能解释一条命令为什么可交互、为什么会卡住、为什么少了尾部输出，以及 Codex 怎样在速度、内存和完整性之间做取舍。
