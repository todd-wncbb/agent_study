# 跨平台 Shell、Terminal、PTY 与进程生命周期：命令语法、TTY 隔离、终止和终端恢复

> 本篇同时使用四个容易混淆的词：Shell 解释命令文本，TerminalBackend 管理命令进程，TTY/PTY 提供交互终端语义，Pager/TUI 管理屏幕与输入模式。先把它们拆开，跨平台代码才读得明白。

## 1. 先给结论

1. `run_terminal_cmd` 通常通过 pipe 捕获 stdout/stderr，并不是给每条命令分配 PTY；需要真正交互终端语义的是 `grok wrap` 与 Pager PTY harness。
2. `TerminalBackend` 抽象本地和 ACP 远端命令执行，统一前台、后台、快照、等待、终止和 session owner 隔离。
3. Unix 只正式支持 Bash/Zsh 的持久 shell state；Fish、dash、ksh 用户会回退到 Bash。
4. Windows 自动选择 `pwsh → powershell.exe → Git Bash → fallback`，也允许 `GROK_SHELL` 覆盖；不同 shell 的 `&&`、`&`、Unix 工具和参数转换语义不同。
5. PowerShell 优先于 Git Bash，是为了避免 MSYS2 把 `/t:Build`、`/nologo` 等原生 Windows 参数误做路径转换。
6. Unix 子进程通过 `setsid`/`setpgid` 脱离控制 TTY，并用进程组执行 `SIGTERM → 1 秒 → SIGKILL`；Windows 使用新 process group 和 Job Object 一类机制控制后代。
7. `Stdio::null()` 只能重定向 fd 0，不能阻止程序重新打开 `/dev/tty`；这就是 `xai-tty-utils` 统一 detach helper 存在的原因。
8. 持久 Shell 不意味着复用同一个永生 shell 进程；实现会捕获 cwd、环境、选项、函数和 alias，随后在新进程中回放快照。
9. `grok wrap` 建立本地 PTY、转发输入输出、Unix 上处理 SIGWINCH，并跟踪 child 留下的终端模式；异常退出时精确恢复 raw mode、鼠标、粘贴、focus、alternate screen 和 Kitty keyboard stack。
10. “进程退出”与“输出完全排空”“子孙进程终止”“终端已恢复”是不同事件，代码和测试必须分别证明。

## 2. 四层心智模型

```text
模型命令文本
  → Shell adapter（bash/zsh/pwsh/powershell/cmd/Git Bash）
  → TerminalBackend（任务、timeout、进程树、输出）
  → OS process / ACP remote terminal

用户键盘与真实屏幕
  ↔ TTY/PTY transport
  ↔ Pager/TUI raw mode、ANSI modes、resize、mouse/paste
```

前一条链路解决“执行一个命令并收集结果”，后一条解决“让一个交互程序像直接运行在终端中”。

## 3. 源码地图

```text
crates/codegen/xai-grok-config/src/shell.rs
└── Shell 探测、调用参数与语义能力

crates/codegen/xai-grok-tools/src/computer/
├── types.rs                  # TerminalBackend、请求/结果/TaskSnapshot
├── local/terminal.rs         # 本地 terminal actor 与进程生命周期
├── local/shell_state.rs      # Bash/Zsh 状态捕获与回放
├── local/static_shell.rs     # 静态快照执行路径
└── task_log.rs               # 后台输出日志

crates/codegen/xai-tty-utils/src/lib.rs
└── TTY detach、pager env、parent-death 等进程工具

crates/codegen/xai-grok-pager/src/
├── pty_wrap.rs               # grok wrap 的真实 PTY plumbing
├── wrap_filter.rs            # PTY 输出过滤/OSC 处理
├── wrap_restore.rs           # 终端模式跟踪与精确恢复
├── event_loop.rs             # TUI 事件与 resize
└── input/                    # 键盘、鼠标、paste

crates/codegen/xai-grok-pager-pty-harness/src/
├── pty.rs                    # 测试 PTY 控制器
├── lib.rs                    # 虚拟屏幕、注入、resize、等待
├── scripted.rs              # 场景 DSL
└── scenarios/               # resize/scroll/streaming stress
```

## 4. Shell、Terminal、TTY、PTY 分别是什么

| 名词 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| Shell | 解析 `&&`、管道、变量、重定向 | 屏幕绘制 |
| TerminalBackend | 启动/管理命令与任务 | 解释 shell 语法细节 |
| TTY | OS 暴露的终端设备语义 | Agent 状态 |
| PTY | 一对 master/slave 模拟真实终端 | 自动理解 ANSI 内容 |
| TUI | 用 ANSI/终端协议绘制界面 | 杀死任意命令进程树 |

普通 pipe 能传字节，但没有 terminal size、raw/canonical mode、job control 等 TTY 属性。很多 CLI 在检测 `isatty=false` 后还会改变颜色、缓冲或交互行为。

## 5. `TerminalBackend` 的统一合同

trait 包含：

- `run`：前台等待完成或 timeout；
- `run_background`：立即返回 `BackgroundHandle`；
- `get_task`、`list_tasks`：查询快照；
- `wait_for_completion`：带上限等待；
- `kill_task`；
- kill foreground/background all 或按 owner；
- `background_foreground_command`：自动转后台；
- `reparent_notifications`：子 session 结束后把存活任务通知转给父 session；
- `warm_shell` 与 `get_shell_cwd`。

本地实现生成 OS 进程，ACP 实现把调用交给远端客户端。默认 trait 方法允许能力较弱的后端安全 no-op，但上层不能因此假设所有后端都支持持久 cwd 或进程 PID。

## 6. `TerminalRunRequest` 是进程合同

关键字段包括：

| 字段 | 作用 |
| --- | --- |
| `command` | 真正执行的命令，可能含隔离包装 |
| `display_command` | 面向模型的原命令 |
| `working_directory` | 本次启动 cwd |
| `env` | session/调用环境 |
| `timeout` | 进程终止上限 |
| `foreground_block_budget` | 本轮最多同步等待多久 |
| `auto_background_on_timeout` | 到等待边界后是否转后台 |
| `output_file` | 全量增量日志 |
| `output_byte_limit` | 内存/模型结果预算 |
| `notification_handle` | 输出块与完成事件 |
| `owner_session_id` | 进程归属 |
| `kind` | Bash 或 Monitor |

`display_command` 与 `command` 分离，避免把 `unshare`、mount wrapper 等内部隔离细节当成用户命令显示。

## 7. Unix Shell 的选择

`detect_unix_shell_kind` 只识别 `$SHELL` 中的 zsh；其他情况选 Bash。二者的实际路径按顺序解析：

1. `GROK_SHELL`，且文件名匹配目标 kind、可执行；
2. `$SHELL`，且匹配并可执行；
3. `which`；
4. `/bin`、`/usr/bin`、`/usr/local/bin`、`/opt/homebrew/bin`；
5. 历史 `/bin/bash` 或 `/bin/zsh` fallback。

结果按 shell kind 存入 `OnceLock`，进程生命周期内不重复探测。

### 7.1 为什么检查“可执行”不只看 mode bit

Nix/overlay 环境的 mode 表现可能误导。解析器在必要时尝试 `<shell> --version`。探测子进程必须 detach 且 stdio 指向 null，防止一个异常 shell 在 TUI 启动时向控制终端写 escape 或发起 prompt。

## 8. Windows Shell 的选择

Windows `GROK_SHELL` 可选：

- `pwsh`；
- `powershell`；
- `bash`/`gitbash`；
- `cmd`。

未覆盖时自动探测：

```text
pwsh.exe
  → Windows PowerShell 5.1
  → Git Bash
  → PowerShell fallback
```

Git Bash 会搜索 Program Files、LocalAppData 和 PATH；PATH 搜索只接受路径中含 Git 的 `bash.exe`，避免误选 WSL bash。

## 9. Windows 的调用 argv

| Shell | program | args |
| --- | --- | --- |
| Git Bash | 实际 bash.exe | `-c <command>` |
| pwsh | `pwsh` | `-NoProfile -NonInteractive -Command <command>` |
| PowerShell 5.1 | `powershell.exe` | 同上 |
| cmd | `cmd` | `/C <command>` |

所有 Windows 分支默认设置：

- `PYTHONUTF8=1`；
- `PYTHONIOENCODING=utf-8:surrogateescape`。

这避免 Python 子进程用 cp1252 等 legacy codepage 解码 UTF-8 命令输出。显式 request env 仍可覆盖默认值。

Git Bash 额外设置：

- `MSYS_NO_PATHCONV=1`；
- `MSYS2_ARG_CONV_EXCL=*`。

否则 MSYS2 可能把原生编译器 `/flag` 当 POSIX 路径转换。

## 10. Shell 能力矩阵

| 能力 | Unix Bash/Zsh | Git Bash | pwsh 7+ | PowerShell 5.1 | cmd.exe |
| --- | --- | --- | --- | --- | --- |
| `&&` 推荐 | 是 | 是 | 是 | 否 | 统一文案用 `;` |
| 常见 Unix utilities | 是 | 是 | 否 | 否 | 否 |
| leading `&` | 不作为调用运算符；`&` 通常放在命令之后表示后台执行 | 不作为调用运算符 | 调用运算符 | 调用运算符 | 分隔符 |
| trailing `&` | 后台 | 后台 | background job | parse error | 分隔符 |

这张表驱动：

- Bash 工具对后台操作符的验证；
- 模型工具描述；
- 命令 chaining separator；
- 是否建议 grep/sed/head 等命令。

跨平台不能靠在 prompt 里简单替换单词解决，运行时 validator 也必须用同一能力来源。

## 11. Pipe 执行为什么仍要脱离 TTY

本地命令通常配置：

```text
stdin  = null
stdout = pipe
stderr = pipe
```

但 `ssh`、`pinentry` 或交互 shell 可以主动打开 `/dev/tty`，绕过 stdin 重定向，和 Pager 抢键盘或向屏幕写控制序列。

`xai-tty-utils::detach_from_tty` 在 Unix `pre_exec` 中调用 `setsid()`；若因进程已是 group leader 返回 EPERM，则退回 `setpgid(0,0)`。Windows 使用 `CREATE_NO_WINDOW`，刻意不加 `DETACHED_PROCESS`，因为后者会破坏 cmd→node 等孙进程的 stdio pipe 继承。

## 12. `pre_exec` 为什么危险

Unix `pre_exec` 运行在 fork 后、exec 前。多线程进程中，绝大多数会分配内存、拿锁或调用复杂 runtime 的代码都不安全。

detach hook 只调用 POSIX async-signal-safe 的 `setsid/setpgid`。Linux child network filter 也必须满足相同约束。

任何人在这里加入 tracing、format、Tokio 或普通 Mutex，都可能造成 fork 后死锁。

## 13. 本地 Terminal actor 的所有权

本地 backend 把进程交给一个 actor 管理。每个 `ProcessState` 保存：

- child handle；
- process group；
- stdout/stderr reader；
- 截断缓冲与全量日志；
- start/end time；
- lifecycle；
- timeout/kill deadline；
- background、block_waited、explicitly_killed；
- owner session 与通知句柄。

所有查询、waiter 注册、kill 和 poll 在同一所有权域中协调，避免多个任务同时 `wait()` 同一 child。

## 14. 前台、后台与自动转后台

### 前台

调用者等待 `TerminalRunResult`。actor 仍持续 drain pipes，避免 OS pipe buffer 填满使 child 阻塞。

### 后台

启动后返回 task ID，actor 继续拥有 child、日志和通知。调用 future 结束不等于进程结束。

### 自动转后台

到达 foreground block budget 时，actor 把调用 waiter 解锁，但不杀进程；ProcessState 标为 backgrounded，继续由 task 工具管理。

因此：

```text
前台 waiter 生命周期 ≠ child 生命周期
```

## 15. Unix 进程组终止

单杀 shell PID 可能留下 cargo、node、python 等后代。Unix 为命令创建/附加进程组，终止过程是：

```text
向 process group 发 SIGTERM
  → 等 1 秒
  → 仍存活则 SIGKILL
  → 最多再等 5 秒回收 child
```

退出后尽早丢弃保存 leader PID 的 process group handle，避免未来 `kill_all` 对已经被 OS 复用的 PID 调用 `killpg`。

通过 `setsid`/`nohup` 主动脱离的孙进程可能不在该 group，不能承诺全部杀死。

## 16. Windows 进程控制

Windows 启动 flags 包括：

- `CREATE_NO_WINDOW`；
- `CREATE_NEW_PROCESS_GROUP`；
- 条件允许时 `CREATE_BREAKAWAY_FROM_JOB`。

若 breakaway 因 `ERROR_ACCESS_DENIED` 失败，代码会退回不带该 flag 重试。随后 ProcessGroup 抽象可用 Job Object 一类 handle 管理整个后代集合。

与 Unix PID 不同，Job Object handle 不存在相同的 PID recycled `killpg` 风险，但仍需正确关闭 handle 与 wait child。

## 17. 等待与完成不是 sleep

`wait_for_completion` 把 oneshot waiter 注册到 actor，并记录 deadline，然后立刻让 actor loop 继续处理其他命令。poll 发现完成或 deadline 后回复。

若 receiver 因 turn cancel 被 drop：

- 不应继续把 `block_waited=true` 当作模型已看见结果；
- actor 会恢复先前标记，让自动完成提醒仍可发生。

这说明“等待”本身也有投递确认语义，不是单纯 `sleep(timeout)` 后读 map。

## 18. 持久 Shell State

Unix 可选持久状态保存：

- cwd；
- export 环境变量；
- shell options；
- functions；
- aliases。

首次使用启动 interactive login shell，加载 rc 文件，输出带 marker 的 base64 快照。以后启动新 shell 时通过额外 fd 回放快照，执行命令，再从另一个 fd 捕获新状态。

### 18.1 为什么用 marker

`.bashrc`/`.zshrc` 可能打印 MOTD 或其他文本。初始化只解析专用 start/end marker 之间的状态，避免把登录噪声当 snapshot。

### 18.2 为什么 stderr 用 null

初始化代码不会读取 stderr；若设成 pipe，而 rc 文件写超过 pipe capacity，child 会阻塞、父任务却等 stdout，形成死锁。

### 18.3 初始化失败

登录脚本慢或卡住时有 timeout；marker 缺失时退化为空 snapshot 与请求 cwd，而不是让所有 terminal 命令永久不可用。

### 18.4 环境策略缺口

base env 会先经过 `ShellEnvironmentPolicy`，但 rc 文件随后 export 的变量进入 replay snapshot，当前 persistent backend 不会再次过滤。源码会在策略非空时告警；非持久 backend 没有同一缺口。

这是明确的安全边界，不能把 environment policy 宣传成对 rc 副作用的完全隔离。

## 19. WSL 不是普通 Linux 的同义词

`xai-tty-utils::is_wsl` 优先检查 `WSL_DISTRO_NAME`/`WSL_INTEROP`，再读取 kernel/osrelease 中 Microsoft 标识，并缓存结果。

当前明显差异包括 grep 超时：普通平台 20 秒，WSL 60 秒，因为跨 Windows 文件系统访问常慢 3–5 倍。

WSL 仍运行 Unix 进程和 signal，但路径、性能、剪贴板与宿主终端行为可能具有 Windows 影响。平台分支不应只用 `cfg(unix)` 思考。

## 20. `grok wrap` 为什么需要 PTY

wrap 的目标是把一个 child 置于本地 pseudo-terminal 中，同时让外层真实终端保持交互：

```text
outer stdin  → 单 writer thread → PTY master → child slave
child output → PTY reader → OSC/mode filter → outer stdout
outer resize → SIGWINCH loop → PTY master.resize
```

使用单 writer thread 避免多个线程同时 write 造成 payload 交错。master reader、writer 与 resize owner 分离，各有明确职责。

## 21. Raw mode 与终端恢复

进入 wrap 后启用 raw mode，让按键逐字节透传。异常退出若不恢复，用户可能看到：

- 输入不回显；
- Enter 不工作；
- 鼠标点击变成 escape 字符；
- 光标消失；
- 屏幕停留在 alternate buffer；
- bracketed paste 保持开启。

因此建立 `TerminalRestoreGuard`，在正常返回、错误和 panic 的 Drop 路径恢复。Unix SIGHUP/SIGINT/SIGTERM 会走独立 signal thread：先转发 child、恢复终端，再以 `128+signal` 退出。

## 22. ModeTracker：只恢复 child 真正留下的状态

盲目输出一套固定 reset 有副作用，尤其 Kitty keyboard protocol 是 stack：多 pop 一次可能破坏外层终端上下文。

`ModeTracker` 解析 child→terminal 的完整 CSI 序列，跟踪：

- mouse 1000/1002/1003；
- mouse encoding 1005/1006/1015/1016；
- focus 1004；
- bracketed paste 2004；
- synchronized update 2026；
- alternate screen 47/1047/1049；
- hidden cursor 25；
- Kitty keyboard push/pop 净深度。

clean child 已自行 disable 时 tracker 清掉相应 bit，最终不输出多余 reset，从而保持 byte-transparent。

### 22.1 恢复顺序

1. 结束 synchronized update，避免 multiplexer 继续缓存；
2. 显示光标；
3. 关闭 mouse/paste/focus；
4. 精确 pop Kitty depth；
5. 最后离开 alternate screen。

### 22.2 一次性 gate

Drop guard 与 signal thread 可能竞争恢复。`restore_claimed`/`restore_done` 原子 gate 让一个赢家输出，输家最多等待 100ms，避免重复 reset 或进程在恢复半途 exit。

已知限制是 Kitty stack 按 screen buffer 独立，而 tracker 只保存一个净深度。

## 23. SIGWINCH 与 macOS 陷阱

Unix 外层终端 resize 通过 SIGWINCH 通知。旧实现曾 block signal 后用 `sigwait`；POSIX 看似合理，但 macOS 会丢弃默认 disposition 为 ignore 的 blocked SIGWINCH，导致内部 PTY 永不 resize。

当前使用 `signal-hook` 安装真实 handler/self-pipe，改变默认 ignore disposition，再调用 master resize。

Windows ConPTY 没有 SIGWINCH；当前 wrap 保持 master 存活并支持 I/O/OSC bridge，但没有同样的 live resize 路径。

这是“符合抽象标准”仍可能平台失效的典型例子，必须有真实 PTY E2E。

## 24. PTY Harness 如何测试真实界面

Harness 使用 `portable_pty` 启动 pager binary，维护虚拟屏幕，并提供：

- 注入键盘 bytes；
- bracketed paste；
- SGR mouse click/drag/wheel；
- resize PTY 与虚拟 screen；
- pump child output；
- 等待文本/marker；
- 查询 exit code；
- Unix signal；
- 保存 cast/场景日志。

场景层覆盖 resize storm、scroll stress、streaming render、mixed interaction 和 idle cost。它验证的是控制序列经过真实 PTY 后的最终 screen，而不只是 renderer 的中间 buffer。

## 25. 错误与恢复矩阵

| 故障 | 期望行为 |
| --- | --- |
| shell 不存在 | spawn typed I/O error |
| rc 初始化卡住 | timeout，退化空状态 |
| stdout/stderr 很大 | 持续 drain，截断模型输出但保留日志 |
| foreground timeout | 杀组或转后台 |
| kill waiter 被取消 | 不错误抑制自动完成通知 |
| child 脱离 group | 明确可能残留，不虚假承诺 |
| PTY EOF/ssh 断连 | restore guard 清理 latched modes |
| SIGTERM wrap | 转发 child、恢复、128+N |
| SIGWINCH | Unix resize inner PTY |
| Windows breakaway denied | 去 flag 重试 |
| ACP backend 无 PID/cwd | 返回 None/能力退化 |

## 26. 安全边界

### 控制 TTY 隔离

detach 防止 child 直接污染 Pager TTY，但不是文件/network sandbox。

### 环境变量

`ShellEnvironmentPolicy` 控制继承范围；persistent rc export 缺口需要单独认识。敏感 env 还可能被用户 shell function/alias 引用。

### 网络

Linux child 可在 `pre_exec` 安装网络过滤；其他平台和 ACP 路径有不同实现，不能从 TerminalBackend trait 推断统一网络隔离。

### 进程清理

owner session ID 让子 agent teardown 只杀自己的进程。全局 kill 会破坏父/兄弟 session 隔离。

### ANSI/OSC

PTY 输出不是可信纯文本。wrap filter 需要处理 OSC 52 等序列，ModeTracker 只观察明确支持的 CSI；未知控制序列仍需终端输出策略审查。

## 27. 常见误解

### “Terminal 工具就是 PTY”

不是。普通工具执行主要用 pipes；wrap/Pager harness 才创建 PTY。

### “stdin=null 就不会碰 TTY”

程序可重新打开 `/dev/tty`，必须 detach controlling terminal。

### “kill shell PID 会杀掉所有后代”

不会，需 process group/Job Object；主动 detach 的孙进程仍可能逃逸。

### “持久 shell 是一直运行的同一个 shell”

当前关键模型是状态快照回放到新进程。

### “PowerShell 和 Bash 只差可执行文件名”

`&&`、`&`、argv、Unix utility、编码与路径转换都不同。

### “终端退出时统一发 reset 最安全”

多余 Kitty pop 会破坏外层 stack；应按 latched state 精确恢复。

### “Unix 测试通过就覆盖 WSL/macOS”

WSL 性能与路径不同，macOS SIGWINCH 行为也证明 POSIX 直觉不足。

## 28. 修改清单

### 修改 Shell 探测

- `GROK_SHELL` override；
- PATH/Nix/Homebrew；
- cache 时机；
- Windows四种 variant；
- argv/env；
- TemplateRenderer 能力文案；
- Bash `&` validator。

### 修改进程启动

- stdio drain；
- TTY detach；
- process group enrollment；
- Linux network pre_exec；
- Windows creation flags；
- spawn failure ErrorKind；
- child handle 注册先后。

### 修改终止

- foreground/background；
- 按 owner kill；
- SIGTERM grace/SIGKILL reap；
- PID reuse；
- completed tombstone；
- 通知去重；
- child/descendant 残留测试。

### 修改 PTY/TUI

- raw mode enter/leave；
- EOF、panic、signal 所有 exit path；
- resize；
- mouse/paste/focus；
- alternate screen；
- Kitty depth；
- clean-exit byte transparency；
- macOS/Windows 真机测试。

## 29. 推荐实验

1. 比较 `echo $PWD; cd /tmp` 连续两次调用，在 persistent on/off 时观察 cwd。
2. 用 child 打开 `/dev/tty`，比较只设 stdin null 与调用 detach 的行为。
3. 启动会生成孙进程的命令，验证 kill group 后的进程树；再让孙进程 `setsid`，观察已知边界。
4. 用 PTY child 开启 bracketed paste、隐藏光标后直接退出，检查 restore bytes。
5. 让 child 正常自行关闭所有 modes，确认 wrap 不额外输出 reset。
6. 在 macOS resize wrap，验证内部 terminal size 变化。
7. 对 Windows pure builder 分别生成四种 invocation，不依赖当前机器安装的 shell。

## 30. 自测题

1. Shell、TerminalBackend 和 PTY 各解决什么问题？
2. 为什么 terminal command 默认不需要 PTY？
3. Windows 为何优先 PowerShell 而非 Git Bash？
4. pwsh 与 PowerShell 5.1 对 `&&`/尾随 `&` 有何差异？
5. Git Bash 为什么设置两个 MSYS path conversion 环境变量？
6. `Stdio::null` 为什么不能替代 `setsid`？
7. pre_exec 中为什么不能随意调用普通 Rust 代码？
8. 前台 waiter 与 child 生命周期为何不同？
9. Unix 终止为何使用 process group 与两阶段 signal？
10. PID reuse 会造成什么危险？
11. Windows breakaway denied 如何恢复？
12. persistent shell snapshot 保存哪些状态？
13. rc 文件会给环境策略留下什么缺口？
14. wait receiver drop 后为何要撤回 block_waited？
15. PTY 相比 pipe 增加哪些语义？
16. 为什么 SIGWINCH+sigwait 在 macOS 失败？
17. ModeTracker 为什么比固定 restore sequence 更精确？
18. Kitty keyboard 为什么不能多 pop？
19. clean child exit 的 byte transparency 是什么？
20. 哪些结论必须在真实平台/PTY 中验证？

## 31. 本篇术语表

| 名词 | 白话解释 | 在本篇中的具体含义 |
| --- | --- | --- |
| Shell | 解释命令语言的程序 | Bash、Zsh、pwsh、PowerShell、cmd、Git Bash |
| terminal backend | 管理命令进程的接口 | LocalTerminalBackend 或 ACP backend |
| TTY | 操作系统终端设备 | 可被进程通过 `/dev/tty` 直接打开 |
| PTY | 模拟终端的一对 master/slave | wrap 和 E2E harness 使用 |
| pipe | 单向字节管道 | terminal tool 捕获 stdout/stderr 的主要方式 |
| controlling TTY | session 关联的控制终端 | detach 避免 child 与 Pager 争用 |
| `setsid` | 创建新 session 并脱离控制终端 | Unix detach 首选 syscall |
| `setpgid` | 设置进程组 | setsid EPERM 时的较弱 fallback |
| process group | 可统一收 signal 的进程集合 | Unix 命令树终止边界 |
| Job Object | Windows 的进程集合 handle | 管理后代生命周期 |
| `pre_exec` | fork 后 exec 前的 hook | 只能调用 async-signal-safe 操作 |
| async-signal-safe | signal/fork 脆弱环境仍可安全调用 | setsid、setpgid 等少数 syscall |
| raw mode | 终端不做行缓冲/回显处理 | TUI/wrap 逐字节接收按键 |
| canonical mode | 终端按行处理输入 | 普通 shell prompt 常见模式 |
| ConPTY | Windows pseudo-console | portable_pty 的 Windows 后端概念 |
| SIGWINCH | 终端窗口尺寸变化 signal | Unix wrap 用它 resize inner PTY |
| SIGTERM | 请求进程有机会清理退出 | Unix kill 第一阶段 |
| SIGKILL | 无法捕获的强制终止 | grace 后升级 |
| reap | wait 已退出 child 回收内核状态 | 防 zombie 与 PID 生命周期混乱 |
| PID reuse | OS 重复使用旧 PID | stale killpg 可能杀错新进程 |
| foreground | 当前调用同步等待的命令 | 不一定永远保持前台 |
| background | 由 task handle 管理的命令 | 调用先返回、进程继续 |
| owner session | 逻辑拥有进程的 session | scoped kill 与通知 reparent 依据 |
| shell state snapshot | 可回放的 shell 状态脚本 | cwd、env、options、function、alias |
| login shell | 加载用户登录/rc 配置的 shell | 首次持久状态初始化使用 |
| rc file | shell 启动配置文件 | `.bashrc`、`.zshrc` |
| marker | 输出中的专用边界字符串 | 从 rc 噪声中定位状态 dump |
| MSYS2 path conversion | Git Bash 自动转换类 POSIX 参数 | 可能破坏 `/nologo` 等 Windows flag |
| codepage | Windows 传统字符编码页 | Python UTF-8 env 避免 cp1252 解码 |
| WSL | Windows Subsystem for Linux | Unix API但性能/路径受 Windows 影响 |
| ANSI escape | 控制终端显示的字节序列 | TUI 绘制、颜色和模式切换 |
| CSI | Control Sequence Introducer | ModeTracker 解析的 `ESC [` 序列 |
| OSC 52 | 操作系统命令序列的一种 | 常用于剪贴板传输，wrap filter 处理 |
| DEC private mode | `CSI ? ... h/l` 终端模式 | mouse、paste、alt screen 等 |
| bracketed paste | 粘贴内容带开始/结束标记 | 避免把粘贴误当逐键输入 |
| alternate screen | TUI 使用的备用屏幕 buffer | 退出后应回到原 shell 内容 |
| synchronized update | 终端延迟展示一组更新 | restore 时应最先关闭 |
| Kitty keyboard protocol | 扩展键盘事件协议 | push/pop 是有状态 stack |
| latched mode | child 开启但尚未关闭的模式 | 异常退出需要 repair |
| byte-transparent | 不额外改变 child 干净输出 | clean exit 不发送多余 reset |
| signal forwarding | wrapper 把收到的 signal 发给 child | 终止时保留合理语义 |
| PTY harness | 控制真实伪终端的测试框架 | 注入按键/resize并解析最终 screen |

更多通用名词见[全局术语表](../appendices/glossary.md)。

## 32. 源码证据索引

| 结论 | 源码入口 |
| --- | --- |
| Windows/Unix shell 探测与能力 | `crates/codegen/xai-grok-config/src/shell.rs` |
| TerminalBackend 合同 | `crates/codegen/xai-grok-tools/src/computer/types.rs` |
| 本地进程 actor、组终止与 waiter | `.../computer/local/terminal.rs` |
| Bash/Zsh 状态捕获 | `.../computer/local/shell_state.rs` |
| 静态快照执行 | `.../computer/local/static_shell.rs` |
| TTY detach 与 parent-death 工具 | `crates/codegen/xai-tty-utils/src/lib.rs` |
| Bash 平台后台操作符验证 | `.../implementations/grok_build/bash/mod.rs` |
| WSL 探测 | `xai-tty-utils/src/lib.rs` 中 `is_wsl` |
| wrap PTY、signal、resize、raw restore | `crates/codegen/xai-grok-pager/src/pty_wrap.rs` |
| 精确终端模式跟踪 | `xai-grok-pager/src/wrap_restore.rs` |
| PTY 测试控制器 | `crates/codegen/xai-grok-pager-pty-harness/src/` |

## 33. 最小验证命令

```sh
# Shell 语义纯逻辑
cargo test -p xai-grok-config --lib shell

# Bash、后台操作符和 streaming
cargo test -p xai-grok-tools --lib bash

# 持久 shell state
cargo test -p xai-grok-tools --lib shell_state

# TTY detach/WSL 等工具
cargo test -p xai-tty-utils --lib

# wrap mode tracker（无需真实 PTY）
cargo test -p xai-grok-pager --lib wrap_restore

# 真实 PTY 测试通常默认 ignored；先列出再按文件说明构建 binary
cargo test -p xai-grok-pager -- --list --ignored
```

当前平台的纯逻辑测试不能证明其他平台的系统调用；Windows、macOS、Linux/WSL 和真实 PTY 结论需要对应 runner。
