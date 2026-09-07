# 87：App-server 请求并发、Serialization Scope、队列、公平性与竞态

> 源码基线：4ee41929eaf4。本章解决“多个客户端同时修改同一 thread、配置或进程时，App-server 怎样决定谁可以并行、谁必须排队，以及连接关闭、后台任务和读写公平性怎样参与这套顺序合同”。

## 1. 本章解决什么问题

异步 server 能同时收到很多请求。全部串行会很慢；全部并行又可能发生两个 turn 同时启动、配置读到半成品、同一 process 的 write/kill 顺序颠倒。

真正需要的不是“并发或不并发”二选一，而是按资源身份判断冲突。

## 2. 资料边界

官方 OpenAI 文档检索没有找到与固定提交逐行对应的 Serialization Scope 说明。本章实现事实来自 4ee41929eaf4 的 request DSL、request_serialization.rs、ConnectionRpcGate 和测试。

## 3. 先说人话：银行有很多窗口

不同客户办理互不相关的业务，可以去不同窗口并行。同一个账户上的转账必须按顺序；连续查询可以一起做，但一笔已经排队的转账不能被后来查询无限插队。

Queue key 相当于账户号，Exclusive/SharedRead 相当于写/读业务。

## 4. 并发问题的三个维度

每个请求至少问：

1. 它操作哪一个资源？
2. 它是读还是可能改变资源？
3. 它是否属于某条连接，还是跨连接共享？

Serialization Scope 正在回答这三件事。

## 5. Race Condition

竞态指结果依赖不可控的执行交错。例如两个请求都先读旧配置，再各自写回，后完成者会覆盖前者更新。

代码没有 data race，业务仍可能存在 race condition。

## 6. Data Race 与 Logical Race

Rust Mutex 能防止内存同时非法访问，却不能自动保证“读—计算—写”整个业务事务不可穿插。

Serialization queue 将保护范围扩展到完整 async handler。

## 7. 为什么不能把 Mutex Guard 跨整个 Handler

Handler 可能 await 文件、网络、数据库和用户输入。长期持有普通共享 Mutex 会阻塞无关 map 操作，并增加死锁风险。

队列先决定执行资格，真正 handler 运行时不必一直占住 queue-map lock。

## 8. ClientRequestSerializationScope

协议 DSL 为 request 计算可选 scope。固定提交包含：

- Global
- GlobalSharedRead
- Thread
- ThreadPath
- CommandExecProcess
- Process
- FuzzyFileSearchSession
- FsWatch
- McpOauth

None 表示不进入这套跨请求队列。

## 9. Scope 是冲突域

Scope 不描述请求所有业务细节，只描述“哪些请求必须互相看见顺序”。

两个 request 转成同一个 queue key，才会进入同一 FIFO。

## 10. Queue Key 与 Scope 的区别

Scope 来自协议层，尚未加入 connection identity。RequestSerializationQueueKey 是 server 内部的最终 HashMap key。

from_scope 会把某些 scope 与 connection ID 组合。

## 11. Global Key

Global 带一个静态类别字符串，例如 config、account-auth、thread-sections、remote-control。

同类别的所有相关请求跨 thread、跨 connection 串行。

## 12. 为什么 Global 仍有名字

若只有一个 Global 大锁，配置写入会阻塞账号登录、远程控制和环境操作。类别字符串把真正独立的全局资源分开。

Global 不等于整个进程只能做一件事。

## 13. Thread Key

Thread scope 使用 thread ID。turn/start、turn/steer、turn/interrupt、thread/read和多个 thread mutation进入同一 thread queue。

不同 thread ID 的请求可以并发。

## 14. ThreadPath Key

Resume/fork 有时只有存储 path、还没有有效 thread ID。ThreadPath 让同一路径上的操作仍能排队。

Path 与 thread ID 是不同 key variant；其等价关系不会被自动推断。

## 15. thread_or_path 选择规则

若 params.thread_id 非空，优先 Thread；否则有 path则 ThreadPath；两者都没有时仍构造空 thread ID的 Thread key，让后续 validation处理输入错误。

Scope selection不等同业务 validation。

## 16. Connection-local Process Key

CommandExecProcess key包含 connection ID与process ID。两个客户端都使用 process ID 1时，不应互相串行或控制对方进程。

Connection ID形成资源 namespace。

## 17. Process Handle Key

新的 Process key同样加入 connection ID与process handle，使 writeStdin、kill、resize对同一连接内同一 process顺序执行。

## 18. FsWatch Key

Watch ID可能由客户端选择，因此 FsWatch key也加入 connection ID，避免不同连接的同名 watch冲突。

## 19. Fuzzy Session Key

FuzzyFileSearchSession使用 session ID但不加 connection ID。固定实现因此把同名 session视为共享冲突域。

阅读 key shape可以反推出资源 identity假设。

## 20. MCP OAuth Key

McpOauth按 server name串行。同一 MCP server的login相关操作不能随意交错，不同 server name可以并发。

## 21. Access Mode

RequestSerializationAccess只有 Exclusive与SharedRead。

绝大多数scope映射为Exclusive；GlobalSharedRead映射到与对应Global相同的key，但access为SharedRead。

## 22. 为什么 SharedRead 不能使用另一把 Key

若 config/read使用 GlobalRead(config)，config/write使用 Global(config)，二者不会冲突。

正确设计是同一个 Global(config) key，access mode不同。

## 23. Exclusive 的语义

同 key的 Exclusive一次只运行一个。它前面的请求完成后才能开始，后面的请求也必须等它完成。

它适合 mutation或需要稳定snapshot的复合操作。

## 24. SharedRead 的语义

队首为SharedRead时，drainer会把其后连续的SharedRead一起取出，用join_all并行等待。

它们共享同一资源的只读窗口。

## 25. 连续两个字很重要

队列是 Read1、Read2、Write、Read3时，Read1与Read2一批并发；Write等待这一批；Read3再等待Write。

后来的读不能穿过已经排队的写。

## 26. Writer Fairness

若允许新读不断加入正在运行的读批，写操作可能永远饥饿。只合并队首连续reads使排队write成为边界。

这是一种简单的writer fairness。

## 27. Fairness 不等于严格耗时公平

两个SharedRead中一个很慢，join_all会等待整个批次完成，write才开始。短read完成也不能提前释放整个读窗口。

FIFO保护到达顺序，不保证每个请求等待时间相同。

## 28. RequestSerializationQueues 的存储

核心结构是：

~~~text
Mutex<HashMap<QueueKey, VecDeque<QueuedRequest>>>
~~~

HashMap分资源，VecDeque保存每个资源的FIFO。

## 29. 为什么使用 VecDeque

入队push_back、出队pop_front都适合队列语义。Vec从头删除需要移动剩余元素。

## 30. 为什么外层需要 Mutex

多个连接可能同时入队、drainer也会出队/删除key。Mutex保护HashMap和每个VecDeque的结构一致性。

## 31. 锁内只做短操作

enqueue在锁内只查key、push和决定是否需要spawn；drain在锁内只取一个batch或删除空queue。

真正future在锁外运行。

## 32. First Request 启动 Drainer

若key不存在，enqueue创建queue、插入首项并返回should_spawn=true，然后锁外spawn一个drain task。

若key已存在，只push，不再spawn第二drainer。

## 33. One Drainer per Key

同key只有一个drainer，是FIFO成立的核心。两个drainer同时pop同queue可能让Exclusive并行执行。

Map中key存在也充当“该资源已有drainer”的标记。

## 34. Different Keys Concurrent

每个新key会spawn自己的drainer。Global(config)被阻塞时，Global(account-auth)仍可运行。

测试用一个blocked key和另一个key证明这种并行。

## 35. Drain Loop

每轮：

1. 锁map；
2. 找queue；
3. pop队首；
4. 若是SharedRead，再取连续reads；
5. 解锁；
6. join_all运行batch；
7. 下一轮。

## 36. Exclusive Batch 大小

队首Exclusive时batch只有该request，即使后面也是Exclusive也不合并。

合并Exclusive会破坏串行。

## 37. SharedRead Batch

join_all会并发poll所有read futures，并等待全部结束。其返回值不重要，因为Queued request的Output是unit，handler自己发送response/error。

## 38. Queue Empty Cleanup

pop发现queue空时，从HashMap删除key并return。这样已经不活跃的thread/process不会永久占用queue map。

## 39. Empty Cleanup 与新 Enqueue 的 Race

Drainer检查空queue、删除key时持有同一Mutex。并发enqueue只能在之后取得锁，看到key不存在并创建新drainer。

因此不会出现“请求入队但无人drain”的普通空队列race。

## 40. QueuedInitializedRequest

它把一个Send + static、Output=unit的boxed future与可选ConnectionRpcGate绑在一起。

RPC request有gate；app-owned background work可以没有gate。

## 41. 为什么 Future 必须 static

请求可能在原dispatch函数返回后才执行，因此不能借用其栈局部变量。所有所需state必须owned或Arc共享。

## 42. 为什么要 BoxFuture

不同handler产生不同anonymous Future类型，队列需要在同一个VecDeque中统一存储，因此用Pin<Box<dyn Future...>>擦除类型。

## 43. Gate 与 Queue 解决不同问题

Queue解决同资源请求的相对顺序；Gate解决连接关闭后哪些RPC还能开始。

一个request可以已在queue中，却尚未通过gate。

## 44. ConnectionRpcGate 的状态

Gate包含 accepting: Mutex<bool>与TaskTracker。

Accepting控制新handler是否开始；TaskTracker记录已开始且尚未结束的handlers。

## 45. Gate Run

run先在accepting lock下检查是否开放并取得TaskTracker token，然后才await业务future。

Token在future结束后drop，inflight计数随之减少。

## 46. 为什么检查与取 Token 要在同一临界区

若先检查true、释放锁、close，再取token，关闭后仍可能启动新handler。

固定实现在同一accepting guard内取得token，关闭与开始形成清晰顺序。

## 47. Close

close将accepting设false并关闭TaskTracker。它不等待已开始任务完成。

已开始future继续执行，尚未通过gate的future会被drop而不poll。

## 48. Shutdown

shutdown先close，再wait TaskTracker归零。它提供“禁止新开始并等待inflight结束”的组合语义。

## 49. Close 与 Cancel 不同

Close不会强制abort正在执行的handler；它只是阻止新handler进入。

这是一种graceful drain，而非抢占式取消。

## 50. 连接关闭流程

App-server先close gate，再调用gate.shutdown等待，外层有30秒timeout，之后清理connection资源。

Timeout防止一个卡住handler无限阻塞断线清理。

## 51. Closed Gate 如何影响排队请求

请求轮到时，gate.run看到accepting=false，直接drop其future。Drainer随后继续处理同key后面的项。

测试证明closed-gate request不会堵住整个queue。

## 52. 同 Gate 的后续请求

连接shutdown时，已经queue但尚未开始的同gate requests会全部跳过；其他连接使用live gate的同key requests仍可继续。

Queue scope与connection gate在此交叉。

## 53. Background Work 没有 Gate

enqueue_background创建gate=None的request。它是App-owned工作，不属于某条client connection，因此连接断开不应自动跳过。

## 54. Background Work 为什么也进 Queue

插件materialization callback需要修改config trust hooks。若直接spawn，它会与config/value/write等RPC交错。

将其放入Global(config) Exclusive，使内部事件与外部RPC共享同一顺序合同。

## 55. 单独加 Mutex 为什么不够

若后台callback使用另一把Mutex，而RPC走serialization queue，两套锁互不认识，仍会并发。

所有会修改同一logical resource的路径必须进入同一个协调机制。

## 56. Scope 在 Protocol DSL 中声明

每个ClientRequest entry旁边写serialization规则。生成的serialization_scope方法match所有variant。

这样并发合同紧贴method定义，而非散落在handler内部。

## 57. DSL 的优势

新增method时reviewer能同时看到params、response、experimental metadata与serialization scope。

漏写scope仍可能发生，但更容易在一个定义表中审计和测试。

## 58. None Scope

Scope None会直接tokio::spawn request.run，不进resource queue。

None不等于“完全无同步”；handler内部、数据库或其他service仍可能有自己的锁和事务。

## 59. 何时适合 None

真正独立的只读查询、已经由下游提供原子性的操作，或创建全新且尚无key的资源可能使用None。

ThreadStart在固定提交中就是None，因为新thread ID尚未建立。

## 60. None 的风险

若一个mutation误标None，同一资源的两个handler会并行。编译器不会知道业务冲突域。

Serialization scope是一项需要测试和review的语义声明。

## 61. ThreadList 为什么可 None

它是查询操作，并可能由底层store提供一致snapshot。固定提交没有把所有读操作都强制放进Thread或Global queue。

是否排队取决于所需一致性，不只取决于method名字含read/list。

## 62. ConfigRead 为什么 SharedRead

Config read与其他config reads可并行，但必须等待之前的config write，并阻止后续write越过它。

因此使用GlobalSharedRead(config)。

## 63. SkillsList 共享 Config Key

Skills list依赖config相关state，固定提交也使用GlobalSharedRead(config)。它会与config mutations正确排序。

不同method可共享同一logical key。

## 64. ThreadSections 读写

threadSection/list使用GlobalSharedRead(thread-sections)，create/update/delete使用Global(thread-sections)。

这是最清楚的同key读写模型。

## 65. Remote Control 读写

Enable/disable为Global(remote-control) Exclusive，status/read为同keySharedRead。

Pairing与client list又使用更细的remote-control-pairing和remote-control-clients keys。

## 66. Account Auth Key

Login、cancel login、logout、account/read和部分mutation共享account-auth key，避免身份状态变更与读取互相穿插。

## 67. Process Key 的顺序

command/exec/write、terminate、resize对同process必须按arrival FIFO。否则terminate先执行后又写stdin会产生难以解释的错误。

## 68. Optional Process ID

command/exec仅在params提供process_id时获得process queue scope；缺少时serialization_scope返回None。

这反映一次性命令与可持续session process的不同冲突模型。

## 69. Optional Thread ID

MCP resource read只有提供thread_id时才按Thread排队；没有thread时返回None并直接并行。

Optional scope让同一method按params选择协调强度。

## 70. Queue Key Equality

Rust derive Eq + Hash决定两个key是否冲突。PathBuf、String、ConnectionId和variant名称都参与相等性。

Thread("x")与FuzzySession("x")不会冲突，因为enum variant不同。

## 71. Path Normalization

两个不同PathBuf文本可能指向同一文件，例如符号链接或相对路径变化。若上游未canonicalize，它们可能进入不同ThreadPath queues。

这是identity normalization必须在key构造前考虑的通用风险；是否已处理要沿params类型与handler验证。

## 72. Scope Key 太粗

把所有config操作放一个Exclusive key最安全但并发较低。可以拆key，但只有能证明子资源互不影响时才做。

性能优化不能破坏invariant。

## 73. Scope Key 太细

若两个操作实际修改同一文件，却用不同key，它们会并行并发生lost update。

Key设计本质上是在定义transaction boundary。

## 74. Arrival Order 是什么

FIFO顺序由请求调用enqueue并取得queue Mutex的先后决定，不一定等于网络发送时间或客户端本地时间。

并发连接几乎同时发送时，没有跨连接的外部绝对顺序。

## 75. Response Order

同key Exclusive的handler完成顺序与执行顺序一致；不同keys或同batch SharedReads的response可任意先后到达。

Client必须按RequestId匹配，不能假设response按发送顺序返回。

## 76. Queue 并不限制总并发

每个key可有一个drainer，None scope直接spawn，SharedRead batch也可很大。系统总并发还受transport queues、下游semaphore和runtime调度影响。

Serialization queue是正确性工具，不是全局concurrency limiter。

## 77. Unbounded Per-key Queue 风险

VecDeque本身在此没有显式每key长度上限。Transport ingress虽有bounded channel，但长期慢handler仍可能积累后续requests。

生产监控应观察queue wait与slow operations；需要限额时必须定义拒绝/取消语义。

## 78. Head-of-line Blocking

同key队首request很慢，后面即使很短也必须等待。这是保持顺序的成本。

若短操作实际独立，应重新设计key；若不独立，就不能为速度跳过。

## 79. SharedRead 批次也会 HOL

一批read中最慢者决定下一write何时开始。join_all等待所有reads。

可以考虑read timeout，但超时后的下游side effect和cancel safety仍需分析。

## 80. Writer Starvation 被怎样避免

后来SharedRead不会越过已经queued的Exclusive。测试专门安排read、write、later read，证明later read必须等write。

## 81. Reader Starvation 呢

FIFO意味着queued read前的writes会依次执行，但后来的write不能越过read。因此在请求持续到达时，双方都按队列位置获得机会。

## 82. Cancellation Before Start

Gate close会让request future在未poll前被drop。它的业务body没有任何side effect。

但是request context与client连接的终结由connection cleanup承担，因为client已经断开。

## 83. Cancellation During Handler

Gate close不会cancel已开始handler。只有外层timeout、task abort、CancellationToken或handler自己的select才可能中止。

不要把gate误当通用cancellation token。

## 84. Future Drop Safety

若handler可能被其他机制drop，它在每个await点都要保持invariant：临时文件、map entry、process child和partial update必须有RAII或事务保护。

Queue只控制开始顺序，不自动保证cancel safety。

## 85. Panic 的影响

Queue drainer假设request future正常以Output=unit结束。作为源码推论，若future panic并展开到drainer task，该key的drain循环可能异常终止。

因此业务错误应返回Result并发送error，不应以panic处理外部输入。

## 86. Error 不会自动停止 Queue

Handler把错误投影为JSON-RPC error后，future仍正常结束，drainer继续下一个request。

一个无效请求不应毒死整个resource queue。

## 87. Queue 与数据库事务

Queue可保证当前进程内handler顺序，但不能替代数据库atomic transaction、跨进程lock或remote compare-and-swap。

若两个App-server进程共享存储，内存queue彼此不可见。

## 88. Queue 与文件系统

同样地，外部程序可绕过App-server修改config文件。读取/写入仍需atomic replace、version check或reload机制。

In-process serialization只解决一个并发层。

## 89. Linearizability

若操作看起来像在某个瞬间原子发生，称为linearizable。FIFO Exclusive queue能为单key handler提供明显顺序，但底层外部side effects未必整体原子。

要声明linearizable必须分析完整调用链。

## 90. Snapshot Consistency

SharedRead batch中的两个read可能在不同时间观察下游动态状态；queue只保证没有同key queued write在它们之间开始。

它不冻结外部世界。

## 91. Background Event Ordering

Effective plugin change callback将trust hook写入与config RPC放同queue，但cache invalidation和MCP runtime invalidation有各自时序。

阅读background flow时要区分哪些步骤被queue覆盖，哪些在入队前独立spawn。

## 92. Instrumentation

每个新key drainer带debug span app_server.serialized_request_queue并记录key。Request本身也继承request span。

可观察性应区分queue wait、handler run和下游wait时间。

## 93. Queue Wait Metric

若只测handler runtime，会漏掉用户最明显的排队延迟。合理metric包括enqueue timestamp、start timestamp、finish timestamp和key类别。

不要把具体thread ID当低基数metric label。

## 94. Slow Key Diagnosis

看到所有config请求变慢时，先找Global(config)队首；只有某thread慢时，找Thread key；不同keys都慢则检查runtime、下游I/O或全局资源。

Key结构本身提供故障定位维度。

## 95. 新增 Method 的 Scope Checklist

1. 修改什么logical resource？
2. 资源identity来自哪个param？
3. Identity是否connection-local？
4. 读能否与读并发？
5. 必须与哪些现有methods共享key？
6. Background paths是否也修改它？
7. None时下游靠什么保证一致性？
8. Close/cancel时未开始请求如何终结？

## 96. 修改 Scope 是行为变更

从None改Thread会降低并发并确定顺序；从Thread改None可能暴露race；从Exclusive改SharedRead需要证明无mutation。

它虽不改变JSON schema，却会改变可观察时序与延迟。

## 97. Scope 的兼容性

Client通常不依赖并发完成顺序，但可能隐含依赖两个mutation的arrival order。改变scope可能修正或破坏这种行为。

应在release note或test中记录重要ordering contract。

## 98. 测试：Same Key FIFO

测试依次enqueue 1、2、3 Exclusive，收集运行顺序并比较完整vector。

它证明同key最基本的不变量。

## 99. 测试：Different Keys Concurrent

一个key的future被oneshot阻塞，另一个key仍能发送ran signal。

没有sleep猜测，而是显式控制阶段。

## 100. 测试：Closed Gate Skip

队列先有live请求，再有closed-gate请求，再有live请求。释放首项后，期望只观察第三项，证明第二项跳过且queue继续。

## 101. 测试：Shared Reads Concurrent

在blocker后排两个SharedRead，用broadcast统一释放。测试先观察两者都started，再释放，证明它们同batch运行。

## 102. 测试：Write Waits

两个SharedRead运行期间，Exclusive的started receiver必须timeout；释放reads后write才开始。

短timeout在这里是负向watchdog，并配合显式barrier构造状态。

## 103. 测试：Later Read Cannot Jump

顺序为Read、Write、LaterRead。测试分别证明write等read、later read等write。

这正是writer fairness的回归证据。

## 104. 源码阅读路线

1. protocol/common.rs：scope enum、scope expression与每个method声明。
2. request_serialization.rs：key/access/queue/drain与测试。
3. connection_rpc_gate.rs：open、close、shutdown和TaskTracker。
4. message_processor.rs：scope分流与None直接spawn。
5. effective_plugin_change.rs：background work共享config queue。
6. lib.rs/message_processor.rs：连接关闭与gate drain。

## 105. 源码检查点

- codex-rs/app-server-protocol/src/protocol/common.rs：ClientRequestSerializationScope与serialization_scope_expr。
- codex-rs/app-server-protocol/src/protocol/common.rs：各method的serialization声明与覆盖测试。
- codex-rs/app-server/src/request_serialization.rs：QueueKey、Access、HashMap<VecDeque>和drain。
- codex-rs/app-server/src/request_serialization.rs：FIFO、跨key、shared read、writer fairness和closed gate tests。
- codex-rs/app-server/src/connection_rpc_gate.rs：accepting、TaskTracker、close/shutdown tests。
- codex-rs/app-server/src/message_processor.rs：QueuedInitializedRequest构造、enqueue或spawn。
- codex-rs/app-server/src/effective_plugin_change.rs：Global(config) background Exclusive。
- codex-rs/app-server/src/lib.rs：connection close、RPC gate close和cleanup task。

## 106. 练习一：画 Queue

给同key顺序R1、R2、W1、R3、W2，画出batch边界和可能并发的future。再加入另一个key X1，说明它何时可运行。

## 107. 练习二：选择 Key

为“读取账号状态”“取消登录”“向process写stdin”“读取另一个thread”选择key、是否含connection ID和access mode，并与固定源码核对。

## 108. 练习三：连接关闭

同key队列含A连接正在运行、A连接尚未开始、B连接尚未开始和background request。关闭A后说明四项分别怎样处理。

## 109. 练习四：找隐藏 Writer

任选Global(config)，搜索所有RPC与background callback。证明它们是否全部进入同key；若某路径None，判断它是真正read/独立操作还是潜在race。

## 110. 本章结论

App-server并发控制不是一把全局锁，而是Resource key、Access mode、FIFO VecDeque、每key单drainer和ConnectionRpcGate的组合。相同key的Exclusive严格串行；连续SharedRead成批并发；queued write阻止后来read插队；不同key保持并行；连接gate又保证断线后不启动尚未执行的RPC。

设计或审查并发时，不能只看Rust是否data-race free。必须确认logical identity、transaction boundary、后台写入、跨连接namespace、排队公平性、取消安全和底层跨进程一致性。

## 111. 本章 Glossary

| 术语/代码短语 | 直译或展开 | 在本章中的含义 |
|---|---|---|
| Serialization scope | 串行化范围 | 定义哪些请求必须共享执行顺序的冲突域 |
| Queue key | 队列键 | Global/Thread/Process等具体资源身份 |
| Access mode | 访问模式 | Exclusive或SharedRead |
| Exclusive | 独占 | 同key一次只运行一个请求 |
| SharedRead | 共享读 | 同key连续只读请求可成批并行 |
| FIFO | 先进先出 | 按enqueue进入队列的顺序处理 |
| VecDeque | 双端队列 | push_back/pop_front高效实现FIFO |
| Drainer | 排空器 | 每个key唯一、取batch并运行future的task |
| Batch | 批次 | 一次join_all执行的一个Exclusive或连续reads |
| Writer fairness | 写者公平 | 已排队write不被后来reads无限插队 |
| Starvation | 饥饿 | 请求因其他流量持续插队而永远不能运行 |
| Head-of-line blocking | 队首阻塞 | 队首慢操作让同key后续请求等待 |
| Logical race | 逻辑竞态 | 内存安全但业务结果依赖异步交错 |
| Lost update | 更新丢失 | 两个读改写操作互相覆盖结果 |
| Transaction boundary | 事务边界 | 必须作为不可穿插整体看待的操作范围 |
| Connection-local identity | 连接局部身份 | process/watch ID需与ConnectionId组合 |
| ConnectionRpcGate | 连接RPC门 | 关闭后阻止queued handler开始 |
| TaskTracker | 任务追踪器 | 统计已开始handler并等待其完成 |
| Graceful drain | 优雅排空 | 停止接收新工作并等待inflight结束 |
| Inflight | 执行中 | 已通过gate但尚未结束的handler |
| Background work | 后台工作 | 不属于某connection但需共享resource queue的任务 |
| Boxed future | 装箱Future | 统一不同handler future类型以存入同一队列 |
| Key granularity | Key粒度 | 冲突域过粗损失并发、过细暴露race |
| Linearizability | 线性一致性 | 操作仿佛在某一瞬间原子发生 |
| Snapshot consistency | 快照一致性 | 一组读取是否观察同一逻辑状态 |
| Queue wait | 队列等待 | enqueue到真正开始handler之间的延迟 |
