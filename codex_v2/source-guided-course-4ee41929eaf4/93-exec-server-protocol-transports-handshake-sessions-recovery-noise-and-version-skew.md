# 93：Exec-server 协议、传输、握手、Session 恢复、Noise 与版本偏差

> 源码基线：4ee41929eaf4。本章解决“App-server 选中远程 Environment 之后，命令、文件和网络请求究竟怎样跨进程/跨机器传输；连接断开后，为什么进程还能恢复而且输出与 stdin 不重复”。

## 1. 本章解决什么问题

第 92 章把 Environment 解释成一整套资源工作台。

本章打开远程工作台内部：从 transport、JSON-RPC、初始化握手一直追到 process/fs/http RPC、断线重连和 Noise 加密中继。

## 2. 资料边界

当前[官方 OpenAI App Server 文档](https://learn.chatgpt.com/docs/app-server)公开描述 App-server 的 Environment 边界；exec-server 的内部 wire、恢复和 Noise relay 细节不属于该公开页面的完整合同。

因此本章的 exec-server 实现事实以固定提交 4ee41929eaf4、`codex-rs/exec-server` 与 `codex-rs/exec-server-protocol` 为准。

## 3. 先说人话：它像一条远程工坊专线

App-server 是总控室，exec-server 是远程工坊里的值班员。

总控室不直接摸远程磁盘，也不直接创建远程 OS 进程；它通过一条有身份、有顺序、有恢复规则的专线发请求。

## 4. 不要把三套协议混在一起

| 层 | 负责什么 | 典型数据 |
|---|---|---|
| App-server API | App客户端控制任务 | thread/turn/item/environment |
| Exec-server JSON-RPC | Codex控制执行机 | process/fs/http/environment |
| Relay/Noise transport | 跨网络安全送字节 | stream、seq、ciphertext、ack |

上层业务方法相同，不代表底层传输帧相同。

## 5. 最重要的一句话

Environment ID 是资源身份，exec-server session ID 是可恢复执行会话，WebSocket/stdio 是一次物理连接。

三者生命周期不同。

## 6. Exec-server 的定位

固定提交的 crate README 称它为“小型 JSON-RPC server”，主要负责：

- 创建和控制子进程
- 远程文件操作
- executor-side HTTP
- 环境信息与能力发现
- transport、协议和恢复

## 7. 它不负责什么

exec-server 不拥有完整 Thread、Turn、模型推理或 App UI 状态机。

那些属于 App-server/Core；exec-server只提供执行侧能力。

## 8. 为什么协议要独立 crate

`codex-exec-server-protocol` 同时被 client 与 server 使用。

共享类型能让 method、camelCase 字段、default 和枚举保持一致，并支持版本偏差测试。

## 9. 一次远程执行的总链路

```text
Agent tool
  -> TurnEnvironment
  -> RemoteProcess/RemoteFileSystem
  -> ExecServerClient
  -> RpcClient
  -> JsonRpcConnection
  -> WebSocket / stdio / Noise relay
  -> ConnectionProcessor
  -> RequestDispatcher
  -> ExecServerHandler
  -> LocalProcess / LocalFileSystem on executor
```

“RemoteProcess”最终仍在远端调用 LocalProcess，只是 local 指 exec-server 所在机器。

## 10. Harness 与 executor

本章中：

- harness：发起远程控制的一侧，通常在 App-server/Environment client。
- executor：真正执行命令与文件操作的一侧，即 exec-server。

这是角色名，不等于固定的操作系统。

## 11. JSON-RPC dialect

Exec-server 使用 Codex 的 JSON-RPC 方言。

固定提交 wire 上省略：

```json
"jsonrpc":"2.0"
```

但结构仍区分 request、notification、response 和 error。

## 12. 四类消息

| 类型 | 关键字段 | 是否期待响应 |
|---|---|---|
| Request | id + method | 是 |
| Notification | method，无id | 否 |
| Response | id + result | 对位request |
| Error | id + error | 对位失败request |

## 13. RequestId

固定提交接受：

- String
- i64 Integer

Client自身通常用递增正整数生成 outbound request ID。

## 14. 消息分类规则

反序列化时：

- 有 method + id -> Request
- 有 method 无 id -> Notification
- 无 method、有 result -> Response
- 其他对象 -> Error尝试

这使 envelope 的字段组合成为 tag。

## 15. 为什么拒绝重复 JSON key

标准 JSON parser 有时对重复 key 采用“后者覆盖前者”。

固定提交显式拒绝重复对象 key，避免 `method`、`id` 或权限字段出现两种解释。

## 16. JSON value node 上限

消息不只限制 bytes，也限制展开后的 JSON values 数量：固定提交上限为 256K nodes。

这样很短的嵌套/紧凑数组也不能反序列化成无限多堆对象。

## 17. 为什么 bytes cap 不够

字符串大主要消耗原始 bytes；大量 `null`、小数组和对象还会制造大量 allocation、递归和 CPU 成本。

资源防护需要同时看 byte size 与结构复杂度。

## 18. trace 字段

Exec-server Request 可携带 W3C trace context。

Server dispatcher把它设为 request span 的 parent；无效 carrier 被忽略并告警，不让诊断字段破坏业务请求。

## 19. 方法家族

固定提交大致分为：

- `initialize` / `initialized`
- `environment/*`
- `process/*`
- `fs/*`
- `http/*`
- `capabilityRoots/*`
- server -> client network policy request

## 20. Transport 抽象

`JsonRpcConnection` 对上层只暴露：

- outbound message channel
- inbound event channel
- disconnected watch
- transport tasks
- transport owner/termination handle

上层不需要知道一条消息来自 WebSocket frame 还是 stdio line。

## 21. 三种 transport

固定提交支持：

- 普通 WebSocket
- command-backed stdio
- authenticated Noise rendezvous over WebSocket

它们最后都适配为同一个 `JsonRpcConnection`。

## 22. 普通 WebSocket framing

本地 WebSocket 模式是一条 JSON-RPC message 对应一个 WebSocket frame。

Text/Binary合法业务frame由适配器解析；Ping/Pong属于WebSocket控制帧。

## 23. WebSocket 不是自动可靠业务恢复

TCP/WebSocket保证一条连接内有序传输，但连接关闭后，不会自动重建 exec-server session 或补 process events。

这些由 client recovery 层完成。

## 24. WebSocket channel 容量

`JsonRpcConnection` inbound/outbound mpsc 使用有界容量，固定提交为 128。

有界队列防止生产方无限占内存，也会把慢消费者暴露为背压或断线。

## 25. WebSocket keepalive

Server-side Axum WebSocket 可定期发 Ping；固定提交生产间隔为 30 秒。

普通 outbound client连接不一定在这一适配器层启用相同 ping interval，需按具体构造路径判断。

## 26. Ping 与 environment/status 不同

Ping/Pong证明 WebSocket peer 与网络通道仍响应。

`environment/status`证明已初始化 exec-server 能处理应用请求；两层健康含义不同。

## 27. Stdio framing

Stdio transport 一行一个 JSON-RPC object：

```text
{...}\n
```

Reader接受 LF 或 CRLF，忽略空行。

## 28. Stdio 消息大小

固定提交将单行 JSON-RPC 消息上限设为 64 MiB。

Reader多看一个 byte，以便对“超过上限但一直不换行”的输入及时失败。

## 29. 为什么 stderr 单独读取

Command-backed exec-server 的 stdout 是协议通道。

诊断日志若写入 stdout 会污染 JSON-RPC framing，所以 child stderr 被逐行读取并写入 debug log。

## 30. Stdio child ownership

连接带 `StdioTransport` owner。

终止或 Drop 会监督 child process；先请求优雅终止，2 秒 grace period 后杀进程树。

## 31. 为什么杀进程树

只杀直接 exec-server child，child创建的后代可能继续持有pipe、端口或资源。

Unix使用process group，Windows使用 `taskkill /T /F` 兜底。

## 32. Stdio 为什么不预连接

启动 stdio transport本身就会创建子进程。

Environment因此保持 lazy，直到真正选择使用，避免未用环境也启动 helper。

## 33. 连接打开不等于协议可用

WebSocket handshake完成或stdio child启动，只说明transport存在。

Exec-server还必须完成应用层initialize/initialized握手。

## 34. 三步握手

```text
client -> initialize request
server -> InitializeResponse { sessionId }
client -> initialized notification
```

完成后才能调用process、fs、http和environment方法。

## 35. InitializeParams

固定提交字段：

```json
{
  "clientName":"codex-environment",
  "resumeSessionId":null
}
```

`resumeSessionId`省略/空表示新会话，非空表示尝试恢复。

## 36. InitializeResponse

```json
{"sessionId":"..."}
```

它不是空对象；session ID 是重连恢复的核心身份。

## 37. initialized 为何还需要

Response说明server已创建/附加session。

Notification说明client已收到response并准备进入正常业务阶段，避免双方对握手完成时点理解不同。

## 38. initialize 只能一次

`ExecServerHandler` 用 atomic flag拒绝同一连接第二次 initialize。

恢复应创建新物理连接并在新连接initialize，不是在旧连接重复调用。

## 39. initialized 不能先发

若未initialize就收到initialized，server返回protocol error并关闭连接路径。

顺序是协议不变量，不是建议。

## 40. 业务方法的双重 gate

`require_initialized_for` 同时要求：

- initialize 已请求并附加session
- initialized notification 已收到

只过第一步仍不能exec或读文件。

## 41. 未知 notification

固定提交router只注册 `initialized` notification。

收到其他notification会被视为协议错误并关闭connection，而不是悄悄忽略。

## 42. SessionRegistry

Server进程共享一个session registry。

每个entry保存：

- session ID
- ProcessHandler
- 当前connection ID
- detached connection ID
- detached expiration deadline

## 43. 新 Session

没有 `resumeSessionId` 时，server生成UUID session ID，并创建独立 ProcessHandler。

Connection被记录为当前attachment owner。

## 44. Session 与 connection 的区别

Connection可断开；Session可在短时间内保持detached并持有运行进程。

这正是“连接断了但远程dev server没有立刻消失”的基础。

## 45. Detach

连接shutdown时，handler把notification sender设为None，并将entry标记detached。

进程仍由SessionRegistry/ProcessHandler持有，但暂时没有notification接收者。

## 46. Detached TTL

固定提交生产TTL为30秒。

30秒内可恢复；超过deadline仍未重新attach，registry移除session并shutdown其中进程。

## 47. TTL 不是命令 timeout

它限制断线后session保留时间。

命令本身的timeout、yield或自然exit是另一套生命周期。

## 48. 恢复未知/过期 Session

未知ID和已经过期ID都返回invalid request。

过期entry在返回错误前会shutdown，确保遗留进程不泄漏。

## 49. 同时 attach 冲突

一个session同一时间只能有一个active connection。

若旧connection仍attached，新connection恢复相同session会收到 `session already attached` 错误。

## 50. 新连接接管后的旧连接

恢复attach会更新notification sender与connection ID。

旧connection loop检查 `is_session_attached()`，发现被替换后退出，避免两个连接同时控制同一session。

## 51. Client 的 session ID 不变量

首次InitializeResponse的session ID存入OnceLock。

重连时server若返回不同ID，client报Protocol error；它不会悄悄把恢复变成新session。

## 52. 为什么恢复前先启动 reader

Server在resume initialize期间可能把运行进程通知重定向到新connection。

Client先启动RPC reader再await initialize，避免通知burst塞满有界channel，反过来阻塞initialize response。

## 53. 初次连接结果共享

`LazyRemoteExecServerClient` 用OnceCell保存startup attempt。

第一个caller执行连接，其他caller等待同一结果，防止并发工具各开一条初始连接。

## 54. 初次失败为何是final

固定提交保存首个startup失败，后续 `wait_until_ready` 返回同一connection-attempt error。

这避免初始配置错误被无界自动重试掩盖。

## 55. 后续断线为何可重试

一旦有过成功client，后续outage走共享 reconnect attempt。

该attempt完成后被清除，下一次独立操作可再次尝试；初始失败和运行期断线策略有意不同。

## 56. Wait 与 FailFast recovery policy

- Wait：允许等待/发起recovery。
- FailFast：只使用当前可调用connection，否则立即报错。

Status与cleanup等场景需要明确选择副作用。

## 57. 单次outage共享reconnect

多个并发请求发现同一断线时，共用一个OnceCell reconnect attempt。

否则会出现重连风暴、session attach冲突和不确定winner。

## 58. Reconnect strategy

可恢复transport保留重开策略：

- WebSocket：重连同一endpoint。
- Noise：保留harness identity，但每次获取新bundle。

Stdio普通connect路径没有相同reconnect strategy。

## 59. 恢复连接的握手

新transport打开后使用：

```json
{"resumeSessionId":"old-session-id"}
```

server重新attach，client验证返回ID，发送initialized，然后恢复进程状态。

## 60. 连接恢复不等于进程恢复完成

Session attach只恢复控制面。

Client还要为每个recoverable process调用 `process/read`，从最后已发布seq补齐事件。

## 61. Process session registry

Client connection上的notifications是共享流。

Client维护 `processId -> SessionState` 映射，把连接级notification路由到每个进程订阅者。

## 62. Process ID 作用域

它是session内逻辑handle，不是OS PID。

重复 `process/start` 使用相同ID会被拒绝，避免通知和控制对位冲突。

## 63. process/start

请求携带：

- processId
- argv
- PathUri cwd
- env policy与env
- tty/pipeStdin/arg0
- Sandbox intent
- managed network配置

响应确认process ID和可选实际Sandbox type。

## 64. start 的 recoverable 切换

Client在start结果确定前把本地SessionState标为不可恢复。

成功且确认process存在后才标为recoverable；失败则移除route，避免恢复一个从未真正启动的进程。

## 65. start 的竞态保护

Client先登记pending process session，再发start，以免极快输出先于响应到达时找不到route。

若调用途径提前退出，Drop guard清理未完成登记。

## 66. process/output

Notification包含：

```json
{
  "processId":"p1",
  "seq":1,
  "stream":"stdout",
  "chunk":"base64..."
}
```

chunk是raw bytes，不保证一行或完整UTF-8。

## 67. 三种 output stream

- stdout
- stderr
- pty

PTY输出具有终端语义，不能强行拆成stdout/stderr两条普通pipe。

## 68. process/exited

它表示进程已经退出，携带exitCode与可选sandboxDenied。

但它不一定表示所有输出资源和server handle已经关闭。

## 69. process/closed

Closed是更晚的terminal notification：输出关闭且process handle从server移除。

Client看到有序Closed后才删除session route。

## 70. 为什么 Exited 与 Closed 分开

OS进程退出后，pipe/PTY仍可能有待排空bytes；server也需要完成资源释放。

分开后客户端既能尽快显示exit，又不会过早丢最后输出。

## 71. 单调 seq

output、exited、closed共享每进程单调sequence。

Client不能假定不同server task产生的notifications按发送任务顺序到达。

## 72. Client reorder buffer

`OrderedSessionEvents` 用BTreeMap暂存未来seq。

只有 `last_published_seq + 1` 到达时才发布，并连续排空后续已缓存事件。

## 73. 重复事件

seq小于等于已发布值，或pending已有同seq时被忽略。

这让replay与live notification重叠时不重复输出。

## 74. Reorder buffer 上限

固定提交限制：

- 最多256个pending events
- 最多1 MiB pending event bytes
- 单event也不能超过1 MiB

远端不能靠制造永久gap无限占内存。

## 75. gap关闭的特殊处理

正好是next expected seq的事件可直接发布并排空buffer。

它不因buffer已满而被拒绝，否则系统永远无法关闭缺口。

## 76. process/read

Read是notifications之外的权威补偿接口。

参数：

- afterSeq
- maxBytes
- waitMs

响应带chunks、nextSeq、exited、exitCode、closed、failure与sandboxDenied。

## 77. afterSeq

Client恢复时传最后已发布seq，只请求更新内容。

这把at-least-once恢复结果和本地dedup序号连接起来。

## 78. Long poll

`waitMs`允许read等待新事件或terminal state。

它不是进程timeout，只是这次读取最多等多久。

## 79. Notification 不是唯一真相

实时notification用于低延迟。

断线后由read补齐，因此server需保留有界process event log和terminal facts。

## 80. 恢复算法

```text
reconnect + initialize(resumeSessionId)
  -> initialized
  -> for each recoverable process:
       process/read(afterSeq=lastPublished)
       merge/deduplicate by seq
       if closed: remove route
```

## 81. 恢复失败的处理

单进程read恢复失败时，client尝试terminate清理，再移除route并发布synthesized failure。

若错误是transport再次关闭，则整次recovery失败并交给下一轮重连。

## 82. Synthesized failure

Client可构造：

- exited=true
- closed=true
- failure=message
- 无chunks

它给消费者一个terminal结果，避免永远等待。

## 83. process/write

请求包含processId、base64 bytes和 `writeId`。

固定提交的WriteStatus有 Accepted、UnknownProcess、StdinClosed、Starting。

## 84. writeId 解决什么问题

设想server已经写入stdin，但response在网络中丢失。

重连后若用新ID重试，同一命令会收到两次输入；复用writeId允许server识别同一次逻辑写入。

## 85. 重试必须复用原 writeId

Client为每个SessionState递增生成writeId。

transport closed时，它重试相同chunk和相同writeId，而不是重新分配。

## 86. writeId 与 RequestId 不同

RequestId标识一次JSON-RPC问答；重连后RPC request ID可变化。

writeId标识跨重试的业务写入，必须保持不变。

## 87. process/signal

固定提交暴露 `Interrupt`，表达类似交互终端Ctrl-C的中断意图。

它与terminate不同：signal允许进程自己处理，terminate用于结束managed process。

## 88. process/terminate

响应 `running: bool`。

False表示未知或已移除，适合cleanup幂等调用；不应把它自动解释成错误。

## 89. Cleanup 调用的保留容量

RpcClient把常规calls和cleanup calls分开配额。

常规槽满时，terminate仍可使用保留cleanup slots；若两者都满，client关闭transport以避免无法清理的僵局。

## 90. 为什么控制请求有独立server lane

Concurrent dispatcher把info/status/signal/terminate/fs-close放进control semaphore。

普通long-running请求占满ordinary lane时，健康检查和资源关闭仍有机会执行。

## 91. Server 默认顺序处理

固定提交默认Inline：请求依次执行。

CLI `--concurrent-requests COUNT` 才启用并发dispatcher。

## 92. 为什么握手仍强制inline

即使启用并发，initialize或尚未initialized阶段的请求仍同步完成。

并发业务不能观察半创建的session状态。

## 93. 并发请求数

大于1才构成Concurrent mode；1等价Inline。

Semaphore限制在有效范围，避免无界spawn执行。

## 94. 队列仍需字节上限

源码TODO指出并发dispatcher等待permit的request bytes尚需单独bound。

并发数限制运行中任务，但不自动限制所有排队payload内存。

## 95. Request router

`build_router()`把method字符串注册到typed handler。

Router先反序列化params，再调用handler，最后序列化result/error；未知method返回method-not-found。

## 96. Handler 的职责

`ExecServerHandler`拥有：

- session attachment
- ProcessHandler
- FileSystemHandler
- HTTP runner/stream tasks
- initialize flags
- background shutdown token

它是每connection协议状态owner。

## 97. ProcessHandler 的职责

它是server adapter，把typed process RPC转给executor-local `LocalProcess`。

SessionRegistry持有它，所以connection detach后进程仍能继续。

## 98. FileSystemHandler 的职责

它持有LocalFileSystem和file-read handle manager。

Connection shutdown会close全部文件read handles，但session process可在TTL内保留。

## 99. 为什么文件handle不随Session恢复

固定提交FileSystemHandler属于connection Handler，而ProcessHandler属于SessionEntry。

因此跨断线恢复的是managed processes，不应假定 `fs/open` handle也能跨连接继续使用。

## 100. fs/readFile

适合可一次buffer的文件，response用base64字符串返回全部bytes。

大文件更适合open/readBlock/close流式读取。

## 101. fs/open

Client生成最多32-byte handle ID并传入path与sandbox。

Server打开文件后把handle登记在本connection的FileReadHandleManager。

## 102. fs/readBlock

请求带handleId、offset、len。

响应带ByteChunk与eof；Client验证返回块不超过约定chunk size。

## 103. 空的非终结 block

若 `eof=false` 但chunk为空，RemoteFileStream把它视为invalid data。

否则offset不前进，会形成无限读取循环。

## 104. fs/close 与 Drop

正常EOF后client调用close。

若consumer提前Drop stream，registration会best-effort异步close，防止server handle泄漏。

## 105. handle ID 上限

Server限制file read handle ID最多32 bytes。

业务标识也需要大小上限，否则攻击者可用巨型key污染HashMap与日志。

## 106. readDirectory 上限

固定提交最多返回50,000 entries。

这个上限与共享256K JSON-node decoder预算协调，保证同版本producer不会制造合法但无法解码的response。

## 107. fs错误映射

- NotFound -> not_found
- InvalidInput/PermissionDenied -> invalid_request
- 其他IO -> internal_error

客户端应区分协议无效、资源不存在和server内部故障。

## 108. Sandbox 随每个fs请求

PathUri之外，fs request可带FileSystemSandboxContext。

ReadOnly/WorkspaceWrite操作可通过隐藏helper进程执行，共享Codex平台Sandbox转换路径。

## 109. http/request

Executor可以执行HTTP请求，参数含method、URL、ordered headers、body bytes、timeout、redirect policy、requestId和streamResponse。

这样remote环境的网络请求从执行侧发出。

## 110. HTTP buffered 与 streaming

- buffered：response携带完整body。
- streaming：response先返回status/headers，body经notification增量发送。

两者使用同一request envelope。

## 111. HTTP body notification

`http/request/bodyDelta`包含requestId、单调seq、delta bytes、done和terminal error。

done之后不应再有delta。

## 112. HTTP stream ID唯一性

同一connection内，旧stream到terminal delta前，requestId必须保持唯一。

Server维护active ID set，重复ID返回invalid params。

## 113. HTTP stream 背压

Client为body stream维护有界mpsc和全局byte semaphore。

消费者过慢不能让任意数量远程bytes无限堆在内存。

## 114. 双向RPC

Exec-server通常接收client request，但managed network可能反向向client询问policy decision。

因此同一连接也必须处理server Request与client Response/Error。

## 115. Server request ID

Server-side sender用正整数生成ID，先登记oneshot callback，再把Request入outbound queue。

Response回来后dispatcher按ID完成pending call。

## 116. 反向RPC并发上限

固定提交最多256个in-flight server calls。

达到上限快速返回错误，不继续无限登记pending callbacks。

## 117. Client inbound request防护

Client只接受非负integer server request ID，拒绝string/负数、重复ID和超出capacity的请求。

这比“收到任何Request都spawn”更安全。

## 118. 断线时pending request

连接关闭后pending oneshot全部完成为Closed。

但reader先按有序incoming queue处理已经在EOF前收到的response，避免“response已经到了却被disconnect抢先判失败”。

## 119. 为什么不直接select disconnect watch

Response和disconnect若来自不同观察路径会竞态。

固定提交让connection reader按同一ordered queue先处理消息、再drain pending，从而保留wire到达顺序。

## 120. Malformed message

Server收到无法解析消息时发送request ID `-1` 的invalid request error。

Client侧malformed server message则关闭reader/connection；不继续在不可信 framing 后猜测边界。

## 121. 普通 JSON-RPC error codes

固定提交常见：

- `-32600` invalid request
- `-32602` invalid params
- `-32603` internal error

Method-not-found使用相应JSON-RPC错误构造。

## 122. Noise relay 解决什么问题

普通WebSocket可直连exec-server；远程环境还可能经过rendezvous service。

Noise层让中继负责路由，却看不到JSON-RPC plaintext，也不能冒充持有固定executor key的端点。

## 123. Registry bundle

一次Noise连接bundle把这些材料绑定在一起：

- websocket URL
- environment ID
- executor registration ID
- executor public key
- harness key authorization

不能混用不同registry response的字段。

## 124. 为什么bundle是single-use

每次物理连接重新从provider获取授权材料。

Reconnect保留harness identity，但不重用旧URL/authorization bundle。

## 125. 401的一次刷新

初次Noise rendezvous WebSocket若返回HTTP 401，固定提交重新获取bundle并重试一次。

它不无界重放同一个可能已失效的授权。

## 126. Noise prologue

Handshake transcript绑定：

- environment ID
- executor registration ID
- stream ID

捕获的一次handshake不能被拼接到另一个环境或虚拟stream。

## 127. Pinned executor key

Harness从registry bundle取得executor public key，并在JSON-RPC开始前验证对端持有相应private key。

验证失败直接关闭，不降级plaintext。

## 128. Rendezvous 能看到什么

中继能看到路由所需metadata，如stream ID和frame大小/时序。

它看不到Noise加密后的JSON-RPC内容或端点private keys。

## 129. stream_id

一个physical environment WebSocket可复用承载多个虚拟harness sessions。

每个stream ID有独立ConnectionProcessor和Noise transport状态。

## 130. stream ID重用保护

ID由不可信relay peer提供，可能延迟重用。

Executor为virtual stream再分配instance ID，旧writer的close通知不能删除同ID的新stream。

## 131. Relay protobuf frame

`RelayMessageFrame`包含version、stream_id、ack、ack_bits与oneof body。

Body可为data、ack、resume、reset、heartbeat或handshake。

## 132. Relay data segmentation

Data包含：

- seq
- segment_index
- segment_count
- payload

Relay层可把应用消息分段并以sequence恢复连续范围。

## 133. ack 与 ack_bits

`ack`表示最高连续收到的segment seq。

`ack_bits`位图表示其后的离散已收segment；ack随outbound frame冗余发送，本身不再需要ack。

## 134. Resume/Reset/Heartbeat

- Resume：声明下一发送seq并认领stream。
- Reset：结束/重置虚拟stream。
- Heartbeat：保持relay层活性。

这些是relay control，不是JSON-RPC methods。

## 135. Reset reason 不可信

Reset是cleartext relay control，reason未受Noise认证。

Harness保留“stream reset”可用性信号，但用固定诊断文本替换攻击者提供的reason。

## 136. Noise record 与 JSON message边界不同

Clatter单Noise message受约65,535 bytes限制，而合法JSON-RPC可更大。

固定提交把JSON message加4-byte大端length prefix，再切成最多60 KiB plaintext records。

## 137. 为什么length prefix也加密

Prefix与payload一起进入Noise record。

接收方解密后才知道JSON message长度，减少向rendezvous暴露应用边界。

## 138. Noise JSON message cap

单JSON-RPC message上限64 MiB。

Decoder在完整payload到达前就校验length，避免恶意authenticated peer声明超大长度导致无限等待/分配。

## 139. 一个record可跨多种边界

- 一条JSON message可跨多个records。
- 一个record可结束上一条并包含下一条的一部分。
- decoder一次push可产出多条完整messages。

因此不能假定WebSocket/relay/Noise/JSON四层frame一一对应。

## 140. 为什么先排序再解密

Noise transport使用隐式receive nonce。

若未来seq或重复ciphertext直接送入decrypt，nonce会错位，后续合法消息也无法解密。

## 141. OrderedCiphertextFrames

它按relay record seq重排、去重，再把连续ciphertexts交给Noise。

固定提交reorder distance最多64，pending ciphertext最多1 MiB。

## 142. 虚拟stream隔离

Executor physical WebSocket读loop不能被某个慢/弃用stream阻塞。

单stream inbound queue使用try_send；满或关闭时只失败该virtual stream。

## 143. Noise backpressure deadline

Harness向application event queue交付也有deadline。

若App层长期不消费，关闭connection比无限缓存更可控。

## 144. Keepalive与Pong watchdog

Noise harness周期发WebSocket Ping，生产Pong timeout为60秒。

计时从Ping成功flush后开始；等待sink capacity使用单独write deadline。

## 145. 为什么在大消息fragment间yield

若连续写完64 MiB才读socket，Pong和inbound control会长期饥饿。

固定提交每个60 KiB record后产生调度点，并优先排空已排队incoming frames。

## 146. Pong deadline后的有界宽限

deadline到达时，Pong可能已经排在data frames之后。

Harness最多再检查32个已排队frames；仍无Pong则以 `pong_timeout` 断开。

## 147. Noise异常不降级

错误key、早到data、文本frame、非法post-handshake frame、decrypt失败或sequence越界都关闭stream/connection。

安全通道失败时不会尝试普通未加密JSON。

## 148. Version skew 是什么

App-server/harness与exec-server/executor可能来自不同Codex版本。

固定提交声明最老支持版本为 `0.145.0`，并用真实released binary做双向兼容测试。

## 149. Initialize 没有 protocolVersion 字段

固定提交的InitializeParams只有clientName与resumeSessionId。

兼容主要依靠Serde defaults、可选字段、capability flags和跨版本集成测试，而不是握手协商单一版本号。

## 150. 宽读与保守写

旧peer缺少新字段时：

- cwd可为None
- capabilities default false
- sandboxType可为None
- sandboxDenied有default

新client读取旧response时保守解释，不假定新能力存在。

## 151. Capability negotiation

EnvironmentInfo capabilities是细粒度协商。

例如远端未声明network proxy launch或sandboxed capability discovery，client不发送/启用相应新行为。

## 152. writeId 是版本演进的例子

恢复安全字段不仅要能被双方序列化，还要有跨版本语义。

Version-skew测试必须覆盖“调用成功”之外的断线/重试路径，否则重复side effect可能漏测。

## 153. 双向版本偏差测试

固定提交测试两种方向：

- current App-server -> released exec-server
- released App-server -> current exec-server

只测一个方向无法证明新reader兼容旧writer和旧reader兼容新writer。

## 154. 测试为什么走真实Noise

脚本下载发布版Codex binary，启动远程executor、mock registry/rendezvous和App-server，最后真实运行命令并验证relay data为密文。

这同时覆盖CLI、registry、Noise、JSON-RPC和process链，而不是只测Serde round-trip。

## 155. 最小可靠恢复不变量

1. 逻辑session ID在重连前后不变。
2. 同一session不能双attach。
3. event按seq至多发布一次。
4. write重试复用writeId。
5. terminal Closed后route才移除。
6. TTL后无人恢复必须shutdown。

## 156. 最小资源边界不变量

- JSON node和message bytes有上限。
- channels与in-flight calls有上限。
- reorder buffers有entry/byte上限。
- file/HTTP/process handles有清理路径。
- control/cleanup保留容量。
- 慢virtual stream不能拖死所有streams。

## 157. 最小安全不变量

- Noise失败不降级plaintext。
- key和authorization bundle不可混配。
- prologue绑定environment/registration/stream。
- foreign path只以PathUri交给target解释。
- Sandbox intent在executor侧落地。
- relay的未认证reason不进入可信诊断。

## 158. 常见误解一：WebSocket重连会自动恢复进程

WebSocket只恢复字节通道。

还需要resume session handshake、server reattach、client process/read replay与seq merge。

## 159. 常见误解二：Notification不会丢

连接断开窗口中的notification可能无法送达。

可靠性来自server event log + process/read，而不是假定notification exactly-once。

## 160. 常见误解三：RequestId可以去重stdin

RPC RequestId只对位一次transport call。

跨重连业务重试必须用writeId等idempotency identity。

## 161. 常见误解四：Exited就是全部结束

Exited表示进程退出，Closed才表示输出与handle收口。

过早删route会漏最后bytes。

## 162. 常见误解五：Noise record就是JSON message

JSON先length-frame，再切Noise records，再装relay data，再进WebSocket binary frame。

任何两层边界都不保证一一对应。

## 163. 常见误解六：健康检查可以顺便重连

固定提交的environment status刻意fail-fast，不触发recovery。

状态观察不应改变被观察系统。

## 164. 调试顺序

遇到远程失败时从外到内：

1. Environment configured/selected/Ready？
2. transport连通？
3. Noise handshake或stdio child正常？
4. initialize/initialized完成？
5. session attach/resume成功？
6. JSON-RPC method/result如何？
7. process/fs/http业务状态如何？

## 165. 日志应带什么

- environment ID
- session ID安全摘要
- connection/stream ID
- transport kind
- method
- process/handle/request identity
- seq与recovery phase
- terminal error category

不要记录auth token、private key、完整授权URL或敏感stdin/body。

## 166. 指标建议

可观察：

- connect/initialize/reconnect latency
- detached sessions与TTL expiry
- pending RPC count
- channel/backpressure failures
- process replayed events/bytes
- Noise handshake、pong timeout与reset
- version-skew测试结果

## 167. 测试矩阵

| 维度 | 值 |
|---|---|
| Transport | WS / stdio / Noise |
| Version | current-current / current-old / old-current |
| Lifecycle | new / active / detached / resumed / expired |
| Process | starting / output / exited / closed |
| Failure | malformed / timeout / disconnect / reorder gap |
| Platform | POSIX / Windows / cross-OS |

## 168. 动手练习一：画四层frame

用一条70 KiB JSON-RPC response说明：

```text
JSON message -> length-framed bytes -> Noise records -> relay frames -> WebSocket frames
```

标出每层的长度、seq和加密可见性。

## 169. 动手练习二：恢复时间线

构造：process已输出seq 1、2；seq 3在断线时丢给client；server随后产生Exited 4、Closed 5。

写出reconnect、initialize、read(afterSeq=2)和本地发布顺序。

## 170. 动手练习三：重复写入

模拟server接受writeId=7后response丢失。

证明用新writeId=8重试会重复stdin，而复用7可实现业务幂等。

## 171. 动手练习四：背压故障注入

让一个Noise virtual stream停止消费，另一个继续请求status。

断言慢stream独立失败，physical websocket上的其他stream仍能推进。

## 172. 动手练习五：版本偏差

构造旧EnvironmentInfo缺cwd/capabilities、旧ExecResponse缺sandboxType、旧Exited缺sandboxDenied。

写出新client的保守解释，并指出哪些新请求必须被capability gate阻止。

## 173. 理解检查

1. Environment ID、session ID和physical connection分别标识什么？
2. initialized notification为什么不能省略？
3. detached TTL与命令timeout有什么不同？
4. 为什么恢复后还要process/read？
5. Exited与Closed的差异是什么？
6. writeId为什么不能用RPC RequestId替代？
7. 为什么先重排ciphertext再Noise decrypt？
8. 为什么Noise length prefix也必须有上限？
9. control lane和cleanup slots各防什么死锁？
10. 当前协议为何需要真实双向version-skew测试？

## 174. 源码阅读路线一：wire

先读：

- `exec-server-protocol/src/rpc.rs`
- `exec-server-protocol/src/protocol.rs`
- `exec-server-protocol/src/lib.rs`

整理envelope、methods、default、optional字段与minimum supported release。

## 175. 路线二：transport与RPC client

读：

- `exec-server/src/connection.rs`
- `exec-server/src/rpc.rs`
- `exec-server/src/client_transport.rs`

追踪message如何进入channel、pending callback如何登记、disconnect如何drain。

## 176. 路线三：握手与恢复

读：

- `exec-server/src/client.rs`
- `exec-server/src/client_recovery.rs`
- `exec-server/src/server/session_registry.rs`

画出new session、detach、resume、process/read replay和TTL shutdown。

## 177. 路线四：server dispatch

读：

- `exec-server/src/server/processor.rs`
- `exec-server/src/server/request_dispatcher.rs`
- `exec-server/src/server/registry.rs`
- `exec-server/src/server/handler.rs`

理解route、initialized gate、inline/concurrent lanes与disconnect cleanup。

## 178. 路线五：资源handler

读：

- `exec-server/src/server/process_handler.rs`
- `exec-server/src/server/file_system_handler.rs`
- `exec-server/src/remote_file_stream.rs`
- `exec-server/src/client/http_client.rs`

比较process session、connection-local file handle和HTTP stream的owner。

## 179. 路线六：Noise

读：

- `exec-server/src/proto/codex.exec_server.relay.v1.proto`
- `exec-server/src/noise_channel.rs`
- `exec-server/src/noise_relay/harness.rs`
- `exec-server/src/noise_relay/executor_stream.rs`
- `exec-server/src/noise_relay/message_framing.rs`
- `exec-server/src/noise_relay/ordered_ciphertext.rs`
- `exec-server/src/websocket_pong_watchdog.rs`

逐层标注plaintext、ciphertext、sequence与buffer cap。

## 180. 路线七：版本偏差测试

读：

- `exec-server/testing/run_version_skew.sh`
- `exec-server/tests/relay/version_skew.rs`

观察真实released binary如何与current双向组合，并走完整Noise执行链。

## 181. 源码检查点

- `codex-rs/exec-server/README.md`
- `codex-rs/exec-server-protocol/src/lib.rs`
- `codex-rs/exec-server-protocol/src/rpc.rs`
- `codex-rs/exec-server-protocol/src/protocol.rs`
- `codex-rs/exec-server/src/client.rs`
- `codex-rs/exec-server/src/client_api.rs`
- `codex-rs/exec-server/src/client_transport.rs`
- `codex-rs/exec-server/src/client_recovery.rs`
- `codex-rs/exec-server/src/connection.rs`
- `codex-rs/exec-server/src/rpc.rs`
- `codex-rs/exec-server/src/rpc_server_requests.rs`
- `codex-rs/exec-server/src/server/processor.rs`
- `codex-rs/exec-server/src/server/request_dispatcher.rs`
- `codex-rs/exec-server/src/server/registry.rs`
- `codex-rs/exec-server/src/server/handler.rs`
- `codex-rs/exec-server/src/server/session_registry.rs`
- `codex-rs/exec-server/src/server/process_handler.rs`
- `codex-rs/exec-server/src/server/file_system_handler.rs`
- `codex-rs/exec-server/src/local_process.rs`
- `codex-rs/exec-server/src/remote_process.rs`
- `codex-rs/exec-server/src/remote_file_stream.rs`
- `codex-rs/exec-server/src/noise_channel.rs`
- `codex-rs/exec-server/src/noise_relay/harness.rs`
- `codex-rs/exec-server/src/noise_relay/executor_stream.rs`
- `codex-rs/exec-server/src/noise_relay/message_framing.rs`
- `codex-rs/exec-server/src/noise_relay/ordered_ciphertext.rs`
- `codex-rs/exec-server/src/proto/codex.exec_server.relay.v1.proto`
- `codex-rs/exec-server/src/websocket_pong_watchdog.rs`
- `codex-rs/exec-server/testing/run_version_skew.sh`
- `codex-rs/exec-server/tests/relay/version_skew.rs`

## 182. 本章词汇表

| 术语/代码词 | 字面含义 | 本章中的具体意思 |
|---|---|---|
| Exec-server | 执行服务 | 在目标机器处理process、fs、HTTP和environment RPC的服务 |
| Harness | 线束/控制端 | 发起并控制远程exec-server连接的一侧 |
| Executor | 执行器 | 真正运行命令和访问文件的目标侧 |
| Wire protocol | 线协议 | 跨transport序列化后双方共同遵守的消息合同 |
| JSON-RPC dialect | JSON-RPC方言 | 省略jsonrpc字段、仍区分四类envelope的Codex变体 |
| RequestId | 请求标识 | 一次JSON-RPC request与response/error的对位身份 |
| Method | 方法 | 如process/start、fs/readFile的RPC动作名 |
| Params/result | 参数/结果 | Request输入与成功Response输出的JSON payload |
| Transport | 传输 | WebSocket、stdio或Noise rendezvous承载消息的通道 |
| Physical connection | 物理连接 | 一次具体socket、WebSocket或stdio child生命周期 |
| JsonRpcConnection | JSON-RPC连接 | 把不同transport适配成消息channel与disconnect watch的对象 |
| Framing | 分帧 | 决定连续bytes中一条消息从哪里开始/结束的规则 |
| WebSocket frame | WebSocket帧 | 普通WS transport中承载一条JSON-RPC消息或控制信息的frame |
| Stdio line | 标准IO行 | command transport以换行分隔的一条JSON-RPC消息 |
| Initialize | 初始化 | 创建或恢复exec-server session的首个request |
| initialized | 已初始化 | client确认握手完成、允许进入业务方法的notification |
| Session ID | 会话标识 | 断线后重新attach同一managed process集合的身份 |
| Connection ID | 连接标识 | Server内部区分某次physical attachment的UUID |
| Attach/detach | 挂接/脱离 | Connection成为Session owner或断线后暂时离开 |
| Detached TTL | 脱离存活期 | Session断线后等待恢复、到期则shutdown的30秒期限 |
| Resume | 恢复 | 新连接携带旧session ID重新attach并补process events |
| Reconnect strategy | 重连策略 | 为WebSocket或Noise重开transport并恢复session的方法 |
| RecoveryPolicy | 恢复策略 | Wait允许恢复，FailFast只使用当前连接 |
| OnceCell attempt | 一次初始化尝试 | 让并发caller共享同一次startup/reconnect结果 |
| Process session | 进程会话 | Client为一个process ID保存events、seq、write和恢复状态 |
| Recoverable | 可恢复 | start已确认、断线后可以process/read重建状态 |
| process/output | 进程输出 | 携带seq、stream和base64 bytes的实时notification |
| process/exited | 进程退出 | OS进程已结束但输出/handle未必完全收口的事件 |
| process/closed | 进程关闭 | 输出关闭且server handle已移除的最终事件 |
| Sequence number | 序号 | 每进程或relay stream单调增长、用于排序去重的数字 |
| Reorder buffer | 重排缓冲 | 暂存未来seq直到缺口关闭的有界BTreeMap |
| process/read | 进程读取 | 按afterSeq读取缓存事件和terminal facts的恢复接口 |
| Long poll | 长轮询 | Read在waitMs内等待新事件再返回 |
| writeId | 写入标识 | 跨RPC重试保持不变、避免stdin重复side effect的业务ID |
| Idempotency | 幂等性 | 同一逻辑操作重复提交仍只产生一次预期作用 |
| Control lane | 控制通道 | 为status/signal/terminate/fs-close保留的并发semaphore |
| Cleanup slot | 清理槽 | 常规RPC饱和时仍允许terminate等收尾调用的配额 |
| Router | 路由器 | 将method字符串映射到typed ExecServerHandler函数 |
| Handler | 处理器 | 拥有connection协议状态并调用process/fs/http实现的对象 |
| File handle | 文件句柄 | fs/open登记、readBlock使用、close释放的connection-local ID |
| HTTP body delta | HTTP正文增量 | streaming response按seq发送的base64 body bytes |
| Bidirectional RPC | 双向RPC | client和server都能发Request并接收Response的连接 |
| Pending callback | 待处理回调 | RequestId到oneshot sender的在途调用登记 |
| Backpressure | 背压 | 有界下游变慢后让发送等待、失败或关闭的反馈机制 |
| Noise | Noise协议框架 | 在rendezvous之上认证端点并加密JSON-RPC字节流的协议 |
| Rendezvous | 会合中继 | 按stream ID路由双方frame但不读取Noise plaintext的服务 |
| Registry bundle | 注册表连接材料包 | URL、环境/注册ID、pinned key和authorization的原子组合 |
| Pinned key | 固定公钥 | Harness预先信任并要求executor证明持有私钥的public key |
| Prologue | 前导绑定数据 | Noise transcript中绑定environment、registration与stream ID的材料 |
| stream_id | 流标识 | Physical relay WebSocket内一条虚拟JSON-RPC session的路由ID |
| Virtual stream | 虚拟流 | 独立Noise状态和ConnectionProcessor的逻辑连接 |
| Relay frame | 中继帧 | Protobuf包装的data/ack/resume/reset/heartbeat/handshake消息 |
| Ciphertext/plaintext | 密文/明文 | Noise加密后中继可见bytes与端点解密后的协议bytes |
| Length prefix | 长度前缀 | 加密字节流中标记JSON message长度的4-byte大端整数 |
| Noise record | Noise记录 | 最多60KiB plaintext、独立消耗nonce的一段加密记录 |
| Nonce | 一次性序数 | Noise按顺序加解密使用、错序会破坏后续通道的隐式状态 |
| ack/ack_bits | 累积确认/位图确认 | Relay可靠性层描述连续和离散已收segments的字段 |
| Reset | 重置 | 结束虚拟relay stream的cleartext控制frame |
| Keepalive | 保活 | 周期Ping/heartbeat验证闲置连接仍可通信 |
| Pong watchdog | Pong看门狗 | Ping发出后限定时间等待Pong、超时断线的状态对象 |
| Version skew | 版本偏差 | Harness/App-server与executor使用不同Codex release |
| Wide read | 宽读取 | 用default/optional接受旧peer缺失的新字段 |
| Capability gate | 能力门控 | 远端明确声明支持后才启用新协议行为 |
| Fail closed | 封闭失败 | 认证、排序、能力或恢复不确定时关闭/拒绝而不放宽 |
