# 91：App-server 命令执行、进程会话、流式输出、PTY 与清理

> 源码基线：4ee41929eaf4。本章解决“App-server 中四种看起来都像运行命令的入口到底有什么不同，以及进程启动后，输出、stdin、超时、后台存活、取消和断线清理如何组成完整生命周期”。

## 1. 本章解决什么问题

第 90 章讲清了命令“能不能运行、在什么 Sandbox 中运行”。本章从命令真正启动后继续追踪：请求什么时候返回，输出怎样到客户端，长进程存在哪里，谁能继续写 stdin，断线后谁负责结束它。

## 2. 资料边界

当前[官方 OpenAI App Server 文档](https://learn.chatgpt.com/docs/app-server)描述了 `command/exec`、`process/*`、`thread/shellCommand`、后台终端和 `commandExecution` Item 的产品合同。本章内部字段和实现分支以固定提交 4ee41929eaf4 为准。

今天的官方文档可能包含固定提交之后的变化；源码事实以基线提交为准。

## 3. 先说人话：四种“运行命令”

把它们想成四种不同柜台：

- Agent 柜台：模型在 Turn 中调用命令工具。
- 单次命令柜台：客户端直接调用受 Sandbox 的 `command/exec`。
- 裸进程柜台：客户端显式启动无 Codex Sandbox 的 `process/spawn`。
- 用户终端柜台：用户把 shell 字符串挂到已有 Thread 的事件流。

柜台都能启动进程，但授权来源、返回方式、身份和所有者不同。

## 4. 四类入口总表

| 入口 | 谁发起 | Sandbox | 是否属于Thread/Turn Item | 初始请求何时完成 |
|---|---|---|---|---|
| Agent `exec_command` | 模型 | 使用Turn有效权限 | 是 | 工具先等待yield窗口，活着则返回process_id |
| `command/exec` | App客户端 | 使用server Sandbox | 否 | 进程退出、输出delta发完后返回最终response |
| `process/spawn` | App客户端 | 无Codex Sandbox | 否 | 进程成功启动并登记后立即返回 |
| `thread/shellCommand` | 用户客户端 | 明确无Sandbox | 是 | Core Op受理后立即返回，进度走Turn/Item事件 |

读源码前先记住这张表，否则很容易把一个入口的保证套到另一个入口。

## 5. 第一类：Agent commandExecution Item

模型调用 shell 或 unified exec 工具时，Core 发出命令开始、输出增量和结束事件。

App-server 把它们投影成 `item/started`、`item/commandExecution/outputDelta` 和 `item/completed`。

## 6. 第二类：command/exec

`command/exec` 是不创建 Thread 和 Turn 的独立命令 API。

它接收 argv 数组，并按 server 的 Sandbox/permission profile 执行。

## 7. 第三类：process/spawn

`process/spawn` 是实验性显式进程控制 API。

它不经过 Codex Sandbox，客户端必须有意暴露这种本地进程能力。

## 8. 第四类：thread/shellCommand

它接收一条 shell 字符串，并把执行过程放进目标 Thread 的标准 Turn/Item 事件流。

固定提交明确把它定义为本地主机 shell escape hatch：无 Sandbox、全访问。

## 9. argv 和 shell string 的区别

`command/exec` 与 `process/spawn` 接收：

```json
{"command":["git","status","--short"]}
```

`thread/shellCommand` 接收：

```json
{"command":"git status --short | head"}
```

前者已经分好 program/arguments；后者保留 pipe、redirect、quote 等 shell 语法。

## 10. 为什么不能随便互换

把 shell string 直接拆空格会破坏引号、空格参数和管道语义。

把 argv 重新拼成 shell string 又可能引入 quoting 或注入问题。

## 11. commandExecution Item 的字段

固定提交的 Item 包含：

- id
- pluginId/scriptPath
- command 展示字符串
- cwd
- processId
- source
- status
- commandActions
- aggregatedOutput
- exitCode
- durationMs

它是客户端可展示的业务对象，不是底层 `std::process::Child`。

## 12. CommandExecutionStatus

状态有：

- `inProgress`
- `completed`
- `failed`
- `declined`

`declined` 表示审批未允许执行；它与进程启动后返回非零退出码不是一回事。

## 13. Source 为什么重要

固定提交的 source 包含：

- Agent
- UserShell
- UnifiedExecStartup
- UnifiedExecInteraction

它解释同一个 Item UI 是模型命令、用户命令、后台会话首次启动，还是继续向已有终端交互。

## 14. 三段式 Item 生命周期

```text
ExecCommandBegin       -> item/started
ExecCommandOutputDelta -> item/commandExecution/outputDelta
ExecCommandEnd         -> item/completed
```

`call_id` 在三种事件间对位为客户端看到的 itemId。

## 15. Begin 事件

Begin 带命令 argv、cwd、解析后的 command actions、source、可选 process ID 和开始时间。

因此客户端在命令结束前就能创建一张完整的运行卡片。

## 16. OutputDelta 事件

Core delta 保存原始 bytes 和 stdout/stderr stream 标签。

固定提交的 App-server Item delta 用 lossily decoded 字符串向 v2 客户端发送，客户端按到达顺序追加。

## 17. End 事件

End 带 stdout、stderr、aggregated output、exit code、duration、formatted output 和 completion status。

App-server 用它构造权威 `item/completed`。

## 18. Delta 不是最终真相

Delta 适合实时显示，但可能被截断、丢失或在客户端重连前已经发送。

最终 UI 状态应以 completed Item 为准，而不是只依赖本地拼接 delta。

## 19. stdout、stderr 与 aggregated output

stdout 和 stderr 是两个系统输出流。

aggregated output 表示按运行时观察顺序形成的合并展示；它不一定等于“先完整 stdout，再完整 stderr”。

## 20. 为什么 stream 标签仍有价值

客户端可把 stderr 显示成警告样式，或分别保存日志。

但 PTY 模式下终端输出通常被复用到 stdout 标签，不能假定还有独立 stderr。

## 21. exit code

退出码是进程终结结果，不是 App-server JSON-RPC error code。

命令正常启动并返回 1，RPC 仍可能成功，只是业务结果的 `exitCode` 为 1。

## 22. spawn failure 与非零退出

“找不到程序”可能在创建进程阶段失败，映射为 RPC/internal error。

“程序启动成功但测试失败”通常是正常 response/Item，exit code 非零。

## 23. duration

duration 是命令实际执行时间。

它不等于整个 Turn 延迟，因为 Turn 还包括模型推理、审批、其他工具和消息生成。

## 24. commandActions

它是对命令行为的 best-effort 解析，例如一条 pipe 中可能有多个动作。

它适合 UI 说明和审批展示，不应被当成 shell 真正执行语义的完美 AST。

## 25. command/exec 的核心定位

它让客户端在不创建对话的情况下运行一条受 server Sandbox 限制的命令。

适合设置检查、轻量诊断或集成自己的终端组件。

## 26. 最简单的 command/exec

```json
{
  "method":"command/exec",
  "id":50,
  "params":{
    "command":["git","status","--short"],
    "cwd":"/workspace"
  }
}
```

未启用 streaming 时，最终 response 返回 exitCode、stdout 和 stderr。

## 27. 请求是 deferred response

`command/exec` 的 handler 启动后台 task 后不会立刻发送普通结果。

最终 JSON-RPC response 被推迟到进程退出，并且在该 connection 的输出通知全部发送后才发出。

## 28. 为什么 handler 返回 None

App-server 的普通 handler 框架通常会替它发送 response。

这里真正的 response 由 CommandExecManager 在异步生命周期末尾发送，所以 processor 返回 `None` 避免重复响应。

## 29. command/exec 参数分组

可以分成五类：

- 身份：processId
- 交互：tty、streamStdin、size
- 输出：streamStdoutStderr、outputBytesCap
- 生命周期：timeoutMs、disableTimeout
- 环境与安全：cwd、env、sandboxPolicy、permissionProfile

按组理解比背十几个字段更容易。

## 30. command 数组不能为空

空数组没有 program 可启动。

协议在 processor 入口直接返回 invalid request，不把它推迟到后台 task。

## 31. cwd 怎样解析

固定提交把可选 cwd 相对 server 配置 cwd join；省略则使用 server cwd。

这与 `process/spawn` 要求绝对 cwd 的合同不同。

## 32. env override

`env` 的 value 为字符串时覆盖变量，为 null 时删除继承变量。

它是在 server 计算出的 shell 环境上修改，不是完全替换整个环境 map。

## 33. timeout 三态

`command/exec` 使用两个字段表达：

- timeoutMs 有值：指定超时
- 两者都省略：server 默认超时
- disableTimeout=true：只有显式取消/terminate 才结束等待

同时给 timeoutMs 和 disableTimeout 会被拒绝。

## 34. output cap 三态

- outputBytesCap 有值：自定义每个 stream 的字节上限
- 两者省略：默认上限
- disableOutputCap=true：不截断捕获

同时给 cap 和 disableOutputCap 也会被拒绝。

## 35. 为什么 cap 按 stream

stdout 和 stderr 各自维护 observed byte count。

这避免一个大量 stderr 完全挤掉少量 stdout，但总内存预算仍需综合考虑两个流和并发进程。

## 36. processId 可选的原因

纯 buffered 命令不需要客户端后续控制，server 可生成内部 ID。

要 streaming、stdin、PTY、resize 或 terminate 时，客户端必须提供可再次引用的 processId。

## 37. 内部 ID 不对外暴露

省略 processId 时，CommandExecManager 用 AtomicI64 生成 `InternalProcessId::Generated`。

它只负责进程表唯一性，不允许客户端凭这个内部数字发 follow-up 请求。

## 38. 客户端 ID

提供 processId 后保存为 `InternalProcessId::Client(String)`。

输出通知和 follow-up control 都使用这条字符串身份。

## 39. ConnectionProcessId

真正的 map key 是：

```text
(connectionId, processId)
```

因此两个客户端都可使用 `build-1`，只要它们属于不同 connection。

## 40. 为什么必须 connection-scoped

否则客户端 A 可以猜到客户端 B 的 processId 并写 stdin 或终止进程。

把连接身份加入 key 是权限隔离，而不只是避免名字冲突。

## 41. 活跃 ID 重复

同一 connection 中相同 client processId 尚活跃时再次 start 会被拒绝。

旧进程结束、entry 被移除后，这个 ID 才能安全复用。

## 42. streaming 的依赖条件

以下任意一个为真都要求 client-supplied processId：

- tty
- streamStdin
- streamStdoutStderr

没有稳定身份就无法把后续通知或控制消息路由到正确进程。

## 43. tty 的隐含行为

`tty: true` 自动意味着：

- stream stdin
- stream stdout/stderr

因为交互终端若只在结束时返回 buffered output，就无法正常使用。

## 44. size 只对 PTY 有效

提供 rows/cols 却没有 tty 会被拒绝。

rows 或 cols 为 0 也会返回 invalid params。

## 45. Sandbox 输入互斥

`command/exec` 可选择 legacy `sandboxPolicy` 或实验性 `permissionProfile`。

二者同时出现会被拒绝，理由与第 90 章完全相同：不能有两个权威权限来源。

## 46. command/exec 不走Agent审批

这是客户端直接请求，不是模型请求越界。

processor 会验证 policy/profile 是否被有效 requirements 允许，并直接在所选 Sandbox 中运行；不要期待它复用 Item approval UI。

## 47. managed network

processor 根据有效配置启动 network proxy，再把 StartedNetworkProxy 生命周期保存在命令 task 中。

这样代理不会在命令尚运行时因临时变量 drop 而提前结束。

## 48. build_exec_request

在交给 CommandExecManager 前，processor 调用 Core 的 `build_exec_request`。

第 90 章讲过的 PermissionProfile、SandboxManager transform 和平台 wrapper 在这里仍然生效。

## 49. Windows 固定提交限制

当实际 SandboxType 是 WindowsRestrictedToken 时，固定提交拒绝：

- streaming command/exec
- 自定义 output cap
- write/terminate/resize follow-up control

非 streaming 默认 cap 路径则交给 Core sandbox execution。

## 50. 为什么要明确写平台限制

一个字段能通过 JSON Schema 不代表所有平台都实现相同行为。

跨平台客户端必须处理 capability gap 和 runtime invalid request。

## 51. 三种进程启动方式

非 Windows 特殊分支中：

- tty -> spawn_pty_process
- 非 tty 且 streamStdin -> spawn_pipe_process
- 非 tty 且不写 stdin -> spawn_pipe_process_no_stdin

它们的 stdin 和输出语义不同。

## 52. pipe process

Pipe 模式让 stdin/stdout/stderr 使用管道连接。

它适合机器协议或非终端交互，但某些程序检测不到 TTY 后会改变缓冲和颜色行为。

## 53. PTY

PTY 是 pseudo-terminal，模拟真实终端设备。

它让 shell、REPL、进度条、颜色和终端尺寸相关程序按交互方式运行。

## 54. PTY 不是普通三管道

PTY 通常把终端输出复用成一个主流，stderr 不再保持普通 pipe 的独立语义。

客户端渲染终端时应按字节序列处理，而不是只拼“文本行”。

## 55. rows 和 cols

它们是字符单元尺寸，不是像素尺寸。

终端 UI resize 时把新的行列数发到 `command/exec/resize`。

## 56. control channel

每个 active session 保存容量 32 的 mpsc sender。

write、resize、terminate 被包装成 control request，送给唯一拥有 ProcessHandle 的运行 loop。

## 57. 为什么不直接从RPC handler操作Child

多个请求可能并发到达，而底层进程状态需要单一 owner 顺序处理。

control channel 把并发 RPC 转换成运行 loop 内的串行事件。

## 58. control response oneshot

每条 follow-up request 附带一次性 response sender。

运行 loop 真正完成 write/resize/terminate 请求后，handler 才返回 success 或具体错误。

## 59. command/exec/write

参数包含：

- processId
- 可选 base64 delta
- closeStdin

delta 和 close 至少有一个，否则请求没有动作，会被拒绝。

## 60. 为什么 stdin 使用 base64

stdin 是任意 bytes，不保证有效 UTF-8。

JSON 字符串不能无损表示所有 byte sequence，因此 wire 使用 base64。

## 61. 先写后关

同一请求既有 delta 又有 closeStdin 时，固定提交先把 bytes 送入 writer channel，再关闭 stdin。

writer task 会先排空已接受 bytes，随后观察 EOF。

## 62. closeStdin 与发送空字符串

发送空 bytes 不等于 EOF。

很多程序只有看到 stdin 真正关闭才开始处理或退出，因此必须有单独 closeStdin 标记。

## 63. 未启用 streamStdin

若原始 start 没有 tty 或 streamStdin，后续 write 会返回 invalid request。

客户端不能靠 follow-up 请求事后改变进程最初的 stdin 管道结构。

## 64. stdin 已关闭

writer sender 失败会映射成 “stdin is already closed”。

它可能表示客户端已关闭，也可能表示进程已退出并关闭输入端。

## 65. resize

resize 只对支持 PTY resize 的 ProcessHandle 有意义。

底层失败被投影为 invalid request，而不是假装成功。

## 66. terminate

terminate 调用 `request_terminate`，请求进程会话结束。

control response 表示终止请求已处理，不必然等同最终 response 已经发给客户端。

## 67. run_command 的竞争事件

主 loop 同时等待：

- control_rx
- expiration
- exit_rx

哪个先发生就推动对应状态，但 loop 会继续直到取得最终退出结果。

## 68. timeout exit code 124

固定提交把超时结果规范化为 124。

这是 shell 生态常见的 timeout code；客户端仍应结合上下文判断，不只用数字猜原因。

## 69. terminate 与 timeout 的区别

timeout 会设置 `expiration_outcome = TimedOut`，最终强制报告 124。

显式 terminate 没有这个标记，最终 code 来自进程退出 channel，失败时可能是 -1。

## 70. 为什么退出后还要 drain 输出

子进程退出时，stdout/stderr reader channel 中可能仍有尚未处理的 bytes。

若立刻发最终 response，客户端会先看到完成、后看到旧输出，或者直接丢失尾部日志。

## 71. IO drain timeout

固定提交给输出 reader 一个有界宽限时间。

孙进程可能继承 pipe fd，导致父进程退出后 pipe 长期不关闭；无限等待会挂住最终 response。

## 72. 输出 chunk 合并

reader 收到一个 chunk 后，会尽量用 try_recv 合并到约 64 KiB。

这减少 notification 数量，但不承诺每条 delta 恰好是一行或一个系统 read。

## 73. 客户端不能按行假设

一行可能跨多个 delta，一个 delta 也可能包含多行。

UTF-8 多字节字符甚至可能跨 chunk；二进制安全路径应先 base64 解码成 bytes 再做增量 decoder。

## 74. cap_reached

当每 stream 观察字节达到 cap 时，最后一条 streamed delta 标记 `capReached: true`。

reader 随后停止继续收集该 stream。

## 75. cap 为零的边界

cap=0 意味着第一轮可保留长度为零，并立刻达到上限。

客户端应根据 capReached 告知用户“输出被截断”，不能把空输出解释成命令什么也没写。

## 76. Streaming 与 buffered 互斥输出位置

启用 streamStdoutStderr 后，bytes 放在 delta notification 中，最终 response 的 stdout/stderr 为空。

未启用时，delta 不发，捕获文本放在最终 response。

## 77. 为什么不重复两份

如果既 stream 又在最终 response 重复完整输出，会放大带宽和内存，并迫使客户端去重。

固定提交选择单一权威传输位置。

## 78. 最终 response 排序保证

CommandExecManager 使用 “send notification and wait” 发送 delta，输出 task 完成后才发送 JSON-RPC response。

因此同一 connection 上客户端可把最终 response 当作输出流已收口的界标。

## 79. 断线清理

connection 关闭时，manager 按 connectionId 找出所有 session，从 map 移除，再发送 terminate control。

这防止一个已经没有任何客户端 owner 的进程长期泄漏。

## 80. 为什么先从 map 移除

移除后新的 follow-up 请求立即找不到 session。

即使 terminate 异步处理，也不会继续接受对一个正在清理对象的控制操作。

## 81. process/spawn 的核心差异

它与 command/exec 复用了许多 PTY、pipe、控制和输出收集思想，但安全与响应合同不同：

- 无 Codex Sandbox
- 必须有 processHandle
- start response 在成功 spawn 后立即发送
- 最终状态由 process/exited notification 发送

## 82. processHandle 必填

因为 start response 不等待退出，后续所有输出和控制都必须有稳定 handle。

空字符串被拒绝，同连接活跃重复 handle 也被拒绝。

## 83. process/spawn 的 cwd

固定提交协议使用 `AbsolutePathBuf`，因此要求绝对工作目录。

这比 command/exec 的可相对 cwd 更严格。

## 84. process/spawn 的环境基线

固定提交从 App-server 进程 `std::env::vars()` 开始，再应用 overrides。

command/exec 则使用配置的 shell environment policy；两者不要假定继承集合完全相同。

## 85. process/spawn 的双 Option

timeoutMs 和 outputBytesCap 使用三态字段：

- 字段省略：server default
- 字段为 null：关闭限制
- 字段为数字：指定限制

这是 `Option<Option<T>>` 在 wire 上的典型用途。

## 86. 为什么 command/exec 不用同一形状

固定提交的 command/exec 使用 `disableTimeout`/`disableOutputCap` 布尔值表达第三态。

两个 API 历史和演进不同，客户端不能因为语义相似就发送相同 JSON shape。

## 87. process/spawn 响应时间线

```text
client -> process/spawn
server: validate + register handle + spawn
server -> success response {}
server -> zero or more process/outputDelta
server -> process/exited
```

start response 只证明进程已开始，不证明它成功完成。

## 88. command/exec 响应时间线

```text
client -> command/exec
server: validate + spawn + wait
server -> zero or more command/exec/outputDelta
server -> final response {exitCode, stdout, stderr}
```

这里同一个请求 ID 一直 pending 到进程结束。

## 89. 为什么需要两种合同

短命令适合 request/response；终端和服务进程需要启动确认后继续交互。

一个 API 很难同时让这两种客户端都保持简单。

## 90. process 控制方法

- `process/writeStdin`
- `process/resizePty`
- `process/kill`

它们使用 `(connectionId, processHandle)` 查找 session，与 command/exec 的 connection scope 原理相同。

## 91. process/exited 内容

包含：

- processHandle
- exitCode
- stdout 与 stdoutCapReached
- stderr 与 stderrCapReached

Streaming 模式下 stdout/stderr 为空，cap 状态也会在最终 delta 上报告。

## 92. process/spawn 的 owner

owner 是发起它的 connection。

连接关闭时 ProcessExecManager 从表中移除该连接所有 handle，并发送 Kill。

## 93. process/spawn 的风险

它不是“Sandbox 更宽一点”，而是明确没有 Codex Sandbox 的本地主机控制 API。

必须结合 experimental capability、transport authentication 和产品 UI 授权审查。

## 94. thread/shellCommand 时间线

请求验证命令非空并确认 local environment 存在，然后向目标 CodexThread 提交：

```text
Op::RunUserShellCommand { command }
```

RPC 随即返回空 success，后续进度通过标准 Turn/Item 事件流出现。

## 95. 为什么 trim 命令

processor 先 trim，空白字符串会被当作空命令拒绝。

它避免创建一个没有实际动作的 UserShell Turn/Item。

## 96. active Turn 时发生什么

官方合同说明：若 Thread 已有 active Turn，用户命令作为辅助动作运行，格式化输出注入该 Turn 消息流。

Thread 空闲时，系统为它启动独立 shell command Turn。

## 97. thread/shellCommand 的价值

它让用户在同一个任务历史和 UI 中运行诊断命令，并让 Agent 看见相关结果。

代价是它是明确的无 Sandbox escape hatch，不能由模型静默替用户调用。

## 98. Agent unified exec

模型的 `exec_command` 不等于 App-server `command/exec`。

它属于 Core 工具系统，经过 Turn approval/Sandbox，并由 `UnifiedExecProcessManager` 管理长期 PTY session。

## 99. yield_time

Agent `exec_command` 会等待一段有限 yield window 收集初始输出。

命令在窗口内退出则返回退出码；仍存活则返回 process ID，让模型以后 `write_stdin` 继续交互。

## 100. yield 不是 timeout

yield time 只是本次工具调用等待多久后把控制权还给模型。

timeout 是多久以后终止进程；长服务可以早 yield、继续后台存活。

## 101. 为什么先 store 再等待 yield

固定提交在初始 wait 前就把仍活跃进程存入 manager。

否则 Turn 在 wait 中被中断、最后一个 Arc drop，后台进程可能意外终止并且无法列出或清理。

## 102. process ID 分配

UnifiedExecProcessManager 生产环境随机选择 1000 到 99999 范围内未保留 ID；测试/确定性模式递增。

ID 是 Thread 内后台会话的引用，不等于 OS PID。

## 103. 为什么不是 OS PID

本地和远程 exec-server 都要使用同一抽象。

远程进程的宿主 PID 对 App-server 本机没有直接控制意义，内部 process ID 提供稳定协议身份。

## 104. UnifiedExecProcess

它统一包装：

- 本地 ExecCommandSession
- 远程 ExecProcess trait object

上层 write、exit state、output 和 terminate 不必为 transport 写两套业务逻辑。

## 105. 输出结构

UnifiedExecProcess 同时维护：

- HeadTailBuffer 快照
- broadcast sender 给实时观察者
- notify/closed flag
- cancellation token

这支持实时 Item delta、有限响应快照和后台 watcher。

## 106. 为什么是 HeadTailBuffer

长命令输出可能非常大。

保留头尾能让模型同时看见启动上下文与最终错误，而不让单个工具输出无界占用 context 或内存。

## 107. broadcast lag

broadcast receiver 落后时可能收到 Lagged。

固定提交的输出 task 跳过丢失段并继续；因此实时观察不是可靠持久日志，最终聚合和进程状态仍需独立维护。

## 108. interaction lock

写 stdin、发布终端事件和清理/裁剪可能需要同一 interaction lock 协调。

它防止进程正被交互时被后台 pruning 同时移除。

## 109. Drop 保险

`UnifiedExecProcess::drop` 调用 terminate。

这是资源泄漏保险，但正常生命周期仍应显式通过 manager 结束，以便完成事件、网络审批和 store 清理。

## 110. BackgroundTerminalInfo

Core 对外提供：

- itemId
- processId
- command
- cwd

它只列出仍未退出的 stored unified exec processes。

## 111. thread/backgroundTerminals/list

App-server 把 Core 信息映射为分页结果，并额外保留 osPid、cpuPercent、rssKb 字段。

固定提交中后三项填 `None`，客户端不能假装它们总有监控数据。

## 112. list 的稳定顺序

Core 先按 numeric process ID 排序，App-server 再做 cursor/limit 分页。

稳定排序是分页不会随机重复或遗漏的重要前提。

## 113. processId 的字符串转换

App-server list 把 Core i32 process ID 转成字符串。

terminate 再把字符串 parse 回 i32；无效字符串返回 invalid request。

## 114. terminate 的 bool

`thread/backgroundTerminals/terminate` 返回 `terminated`。

false 可能表示 ID 不存在或无法确认终止；它不是 JSON-RPC transport failure。

## 115. clean

`thread/backgroundTerminals/clean` 向 Core 提交 `Op::CleanBackgroundTerminals`。

Session handler 调用 manager 的 `terminate_all_processes`，一次终止该 Thread 所有 unified exec 进程。

## 116. clean 为什么是 Core Op

后台进程属于 Session/Core 状态，而不是 App-server processor 自己的 map。

通过 Op 让真正 owner 执行清理，避免 App-server 复制状态或直接碰内部 ProcessHandle。

## 117. 四种身份不要混淆

- Item ID：Turn 中命令卡片身份
- Agent unified exec process ID：后台终端身份
- command/exec processId：客户端连接内的独立命令身份
- process/spawn processHandle：无 Sandbox 裸进程身份

它们可能都叫 process，但不能跨 API 使用。

## 118. 四种终结信号不要混淆

- item/completed
- command/exec final JSON-RPC response
- process/exited notification
- background terminal terminate/clean response

客户端 reducer 必须按入口选择正确终结事件。

## 119. 取消的层次

取消可能来自：

- Turn interrupt
- command/exec terminate
- process/kill
- connection close
- timeout
- manager clean/prune
- owner drop

每条路径都应最终让进程、输出 reader、session map 和 UI 收敛。

## 120. 连接关闭不等于 Turn interrupt

connection-scoped command/exec 和 process/spawn 在连接关闭时被清理。

Thread 本身可有其他 subscriber，Agent Turn/后台终端是否继续由 Thread/Session owner 决定，不应机械套用同一规则。

## 121. 背压在哪里出现

- transport outgoing queue
- control mpsc channel
- process output channel
- broadcast receiver
- 客户端渲染缓冲

任何一层过慢都可能增加延迟、丢实时观察数据或触发有界策略。

## 122. 为什么控制队列有界

容量 32 防止客户端无限堆积 stdin/resize/kill 请求占满内存。

但有界队列也意味着发送方可能等待，取消安全和断线清理必须纳入设计。

## 123. 输出为何不能无限保留

一个打印 `/dev/zero` 的命令可在很短时间制造大量数据。

output cap、HeadTailBuffer、chunk 合并和 transport queue 都是资源安全合同，而不只是性能优化。

## 124. Binary output

command/exec 和 process/spawn 的 streaming wire 使用 base64，可无损传 bytes。

Agent Item delta 在固定提交中转成 lossy UTF-8 字符串，更适合人类日志而非二进制协议。

## 125. 选择哪个 API

需要 Agent 理解并继续工作：用 Turn 工具或显式用户 `thread/shellCommand`。

需要受 Sandbox 的独立诊断：用 `command/exec`。

需要构建真正的本地终端/进程控制器且明确接受无 Sandbox：才考虑实验性 `process/*`。

## 126. 不应使用 process/spawn 的场景

如果目的只是读取仓库状态或运行受限测试，`command/exec` 的 Sandbox 更合适。

不要因为 process API 更容易交互，就无意中取消安全边界。

## 127. 客户端状态机：command/exec

建议状态：

```text
Starting -> Running -> Terminating -> Exited
                  \-> FailedToStart
```

最终 response 到来后关闭输出 decoder，且不再接受 write/resize。

## 128. 客户端状态机：process/spawn

```text
Starting -> Running -> Killing -> Exited
     \-> SpawnFailed
```

start response 进入 Running；只有 process/exited 才进入 Exited。

## 129. 客户端状态机：Item

```text
item/started(inProgress)
  + ordered deltas
  -> item/completed(completed|failed|declined)
```

重连时依靠 Thread snapshot/completed Item 收敛，不盲目续接本地 delta buffer。

## 130. 错误分类

至少区分：

- invalid request/params
- spawn failure
- Sandbox failure
- process nonzero exit
- timeout
- explicit termination
- output truncation
- session no longer active
- connection closed

把它们都显示成“命令失败”会失去可操作信息。

## 131. 排错：没有收到输出

依次检查：

1. 是否启用了 streaming。
2. 是否提供了 processId/handle。
3. 是否在监听正确 notification method。
4. 是否 base64 decode。
5. 是否 PTY 输出统一走 stdout。
6. cap 是否为零或已达到。
7. 最终 buffered 字段是否才是输出位置。

## 132. 排错：write 找不到进程

可能是：

- 使用了另一个 connection
- processId/handle 类型混用
- 进程已退出并从 map 移除
- connection close 已触发清理
- start 从未成功
- 使用了 server 内部生成 ID

## 133. 排错：最终 response 一直不来

对 command/exec 检查进程是否仍在等待 stdin、timeout 是否被禁用、是否有孙进程持有 pipe，以及 transport 是否能排出 delta。

不要把 deferred response 当成 server 已丢失 request。

## 134. 排错：进程退出但 UI 仍 Running

检查 reducer 是否等待错终结信号：process/spawn 要等 process/exited，command/exec 等 final response，Agent Item 等 item/completed。

也要处理断线后的本地状态清理。

## 135. 安全不变量一

`process/spawn` 和 `thread/shellCommand` 的无 Sandbox 性质必须在 API 和 UI 上显式可见。

不能因为它们也输出 commandExecution UI 就让用户误以为继承 Thread Sandbox。

## 136. 安全不变量二

follow-up control 只能命中同一 connection 创建的进程。

process ID 本身不是 bearer capability。

## 137. 生命周期不变量一

活跃 session 的 map entry 最终必须在退出、spawn 失败或 connection close 时移除。

否则 ID 永久占用并泄漏 control sender。

## 138. 生命周期不变量二

最终完成信号必须在输出收口后发送，或明确说明输出仍由另一条流继续。

command/exec 固定提交选择先 drain delta、再 final response。

## 139. 生命周期不变量三

关闭 stdin、终止进程和关闭 connection 必须能唤醒所有等待任务。

不能留下永久等待 output/exit/control channel 的后台 task。

## 140. 生命周期不变量四

timeout、kill 和自然退出竞争时，只能发布一个权威终结结果。

`tokio::select!` loop、单 exit receiver 和 session removal共同承担这一点。

## 141. 资源不变量

输出、队列、进程表和等待时间都必须有界，或由调用者非常明确地选择取消上限。

“允许无限”是高风险选项，不应成为无意默认值。

## 142. 测试矩阵：模式

至少覆盖：

- buffered pipe
- streaming pipe
- stdin pipe
- PTY
- Sandbox command/exec
- unsandboxed process/spawn
- Agent short command
- Agent background command

## 143. 测试矩阵：终结

至少覆盖：

- exit 0
- nonzero exit
- spawn failure
- timeout
- explicit kill
- stdin EOF
- connection disconnect
- child exit但孙进程保留pipe

## 144. 测试矩阵：输出

至少覆盖：

- stdout only
- stderr only
- interleaved streams
- invalid UTF-8
- cap below first chunk
- cap exactly at boundary
- cap disabled
- slow client/backpressure

## 145. 测试矩阵：身份

至少覆盖：

- 同connection重复ID拒绝
- 不同connection相同ID隔离
- 退出后ID复用
- 错ID follow-up
- command processId与processHandle混用

## 146. 源码阅读顺序

先看 protocol types 和 response timing 注释，再看两个 processor，然后读 manager run loop，最后进入 Core unified exec。

这样可以先理解合同，再理解实现。

## 147. 第一步：协议

打开：

- `app-server-protocol/src/protocol/v2/command_exec.rs`
- `app-server-protocol/src/protocol/v2/process.rs`
- `app-server-protocol/src/protocol/v2/item.rs`
- `app-server-protocol/src/protocol/v2/thread.rs`

列出每种入口的 start、delta、control 和 terminal message。

## 148. 第二步：独立命令

打开 `app-server/src/request_processors/command_exec_processor.rs` 和 `app-server/src/command_exec.rs`。

重点追踪 validate、effective permissions、start、control、select、drain、final response。

## 149. 第三步：裸进程

打开 `app-server/src/request_processors/process_exec_processor.rs`。

与 command exec 逐段比较，特别标记 response timing、env baseline、三态 limits 和无 Sandbox。

## 150. 第四步：Thread命令和后台终端

打开 `app-server/src/request_processors/thread_processor.rs`。

追踪 `RunUserShellCommand`、CleanBackgroundTerminals、list 和 terminate 到 CodexThread/Session。

## 151. 第五步：Agent unified exec

打开：

- `core/src/tools/handlers/unified_exec/exec_command.rs`
- `core/src/unified_exec/process_manager.rs`
- `core/src/unified_exec/process.rs`

重点理解 yield、store-before-wait、HeadTailBuffer、interaction lock、list 和 Drop。

## 152. 动手练习一：四入口选型

分别为以下需求选 API 并解释原因：

- 显示受限 `git status`
- 做一个本地交互 shell
- 让 Agent 启动 dev server 后继续修改代码
- 用户在当前任务中手动运行一条 pipe 命令

答案必须包含 Sandbox 和 terminal signal。

## 153. 动手练习二：客户端 reducer

为 command/exec 和 process/spawn 各写一个 reducer。

输入 start response、delta、kill response、terminal message 和 disconnect，证明不会永远停在 Running。

## 154. 动手练习三：分块 decoder

构造一个 UTF-8 汉字的三个 bytes 被拆进两个 base64 delta。

证明“每个 chunk 单独转字符串再拼接”会损坏字符，并实现增量 byte decoder。

## 155. 动手练习四：竞态测试

让 timeout、kill 和自然 exit 接近同时发生。

断言只收到一个 terminal state、session entry 被移除、follow-up write 返回 no longer running。

## 156. 理解检查

1. 为什么 `command/exec` 与 Agent `exec_command` 不是同一个 API？
2. 为什么 process/spawn 有 start response 和 process/exited 两个阶段？
3. PTY 为什么隐含 streaming？
4. processId 为什么必须加 connectionId 才能做 map key？
5. yield time 与 timeout 有什么区别？
6. 为什么进程退出后还要等待输出 drain？
7. streaming 时最终 stdout/stderr 为什么为空？
8. Thread 后台 process ID 为什么不是 OS PID？

## 157. 源码检查点

- `codex-rs/app-server-protocol/src/protocol/common.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/command_exec.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/process.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/item.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
- `codex-rs/app-server-protocol/src/protocol/event_mapping.rs`
- `codex-rs/app-server/src/request_processors/command_exec_processor.rs`
- `codex-rs/app-server/src/command_exec.rs`
- `codex-rs/app-server/src/request_processors/process_exec_processor.rs`
- `codex-rs/app-server/src/request_processors/thread_processor.rs`
- `codex-rs/app-server/src/bespoke_event_handling.rs`
- `codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`
- `codex-rs/core/src/unified_exec/process_manager.rs`
- `codex-rs/core/src/unified_exec/process.rs`
- `codex-rs/core/src/session/handlers.rs`
- `codex-rs/core/src/codex_thread.rs`
- `codex-rs/utils/pty/src/lib.rs`
- `codex-rs/app-server/tests/suite/v2/command_exec.rs`

## 158. 本章词汇表

| 术语 | 字面含义 | 本章中的具体意思 |
|---|---|---|
| Command execution | 命令执行 | program和arguments被创建为进程并产生输出/退出状态的生命周期 |
| argv | Argument vector | 已分词的program加参数数组，不保留shell pipe/redirect语义 |
| Shell string | Shell字符串 | 由shell解释、可含引号、管道和重定向的一整条命令 |
| commandExecution Item | 命令执行条目 | Thread/Turn事件流中展示模型或用户命令的业务对象 |
| command/exec | 独立命令执行 | 不建Thread、在server Sandbox中运行并等待最终response的API |
| process/spawn | 进程启动 | 无Codex Sandbox、启动后立即响应的实验性显式进程API |
| thread/shellCommand | Thread shell命令 | 用户发起、无Sandbox并进入标准Turn/Item事件流的命令 |
| Unified exec | 统一执行 | Core为本地/远程、短命令/后台PTY提供的统一会话机制 |
| Deferred response | 延迟响应 | handler受理后暂不回复，等进程和输出结束再发JSON-RPC response |
| processId | 进程ID | command/exec或Core后台会话的协议身份，不等于OS PID |
| processHandle | 进程句柄 | process/spawn中客户端提供的connection-scoped字符串身份 |
| Connection-scoped | 连接作用域 | ID只在创建它的同一客户端connection中可控制 |
| PTY | Pseudo-terminal | 模拟交互终端、支持尺寸和终端程序行为的伪终端 |
| Pipe | 管道 | 将进程stdin/stdout/stderr与父进程连接的字节通道 |
| stdin/stdout/stderr | 标准输入/输出/错误 | 进程的三类标准字节流 |
| Streaming | 流式传输 | 进程运行中以多个delta通知发送输出 |
| Buffered | 缓冲 | 服务端收集输出并在最终response/notification一次返回 |
| Delta | 增量 | 输出流的一段bytes，不保证对应完整行或字符 |
| Base64 | 六十四进制编码 | 在JSON字符串中无损传输任意bytes的编码 |
| capReached | 达到上限 | 表示该stream后续输出因字节cap不再保留/发送 |
| Output cap | 输出上限 | 每个stream最多捕获或stream的bytes数 |
| HeadTailBuffer | 头尾缓冲 | 有界保留输出开头和结尾的结构 |
| Yield time | 让出时间 | Agent初始工具调用等待多久后把活跃process ID返回模型 |
| Timeout | 超时 | 到达期限后请求终止进程的生命周期限制 |
| Expiration | 到期条件 | timeout或cancellation组成的进程终止触发器 |
| Exit code | 退出码 | 进程结束的数值业务结果，不是JSON-RPC error code |
| Spawn failure | 启动失败 | program尚未成功成为运行进程时发生的错误 |
| IO drain | 输入输出排空 | 进程退出后继续读取pipe中剩余bytes的阶段 |
| Grandchild | 孙进程 | 子进程再启动且可能继续持有stdout/stderr fd的进程 |
| Control channel | 控制通道 | 把并发write/resize/terminate请求送到唯一进程owner的mpsc |
| oneshot response | 单次响应 | control loop把一次操作结果送回RPC handler的channel |
| ProcessHandle | 进程控制对象 | 对本地PTY或远程exec process执行write/resize/terminate的抽象 |
| process/exited | 进程已退出 | process/spawn生命周期的权威终结notification |
| Background terminal | 后台终端 | yield后仍存活并存入Thread Session的unified exec进程 |
| Interaction lock | 交互锁 | 防止stdin交互、事件发布和pruning同时破坏会话状态 |
| Pruning | 裁剪 | 在进程表达到策略限制时移除合适的旧entry |
| Broadcast lag | 广播落后 | receiver消费太慢导致部分实时chunk被跳过 |
| Cancellation token | 取消令牌 | 在async任务间传播终止意图的可克隆信号 |
| kill-on-drop | 丢弃即终止 | owner意外释放时避免子进程泄漏的资源保险 |
| Terminal signal | 终结信号 | item/completed、final response或process/exited等权威结束事件 |
| Reducer | 归约器 | 将start、delta、control和terminal事件合成客户端状态的函数 |
| Backpressure | 背压 | 下游消费变慢反向限制上游生产或排队速度的现象 |
