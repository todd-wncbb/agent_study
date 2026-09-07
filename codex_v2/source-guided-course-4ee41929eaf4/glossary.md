# Codex 源码术语总表

这份表用来解决两种不同的“不认识”：

1. 英文单词字面是什么意思；
2. 它在 Codex 源码里具体扮演什么角色。

同一个英文词在不同项目中可能含义不同，阅读时应以“本仓库含义”为主。各章节还有更小的“本章词汇表”。

## 1. 生命周期与任务结构

| 词语 | 字面意思 | 在 Codex 中的意思 |
|---|---|---|
| Thread | 线、线程 | 一条可持续多轮、可恢复的对话任务线；不要直接等同于操作系统线程 |
| Session | 会话 | Core 中承载一条 thread 运行状态、接收操作并管理 turn 的运行实例 |
| Turn | 轮次、回合 | 围绕一次用户目标持续工作，直到完成、失败或中断 |
| Step | 步骤 | 一次模型采样及其工具视图所使用的稳定快照 |
| Sampling request | 采样请求 | 向模型请求下一步输出的一次网络请求 |
| Submission | 提交物 | 送进 Session 输入队列的一次操作及其身份信息 |
| Active turn | 活跃轮次 | 当前正在运行、尚未结束的 Turn |
| Pending input | 待处理输入 | Turn 运行期间新到达、还未正式并入下一步的输入 |
| Steer | 引导、转向 | 在进行中的任务边界处补充信息，改变接下来的方向 |

## 2. 模型与上下文

| 词语 | 字面意思 | 在 Codex 中的意思 |
|---|---|---|
| Prompt | 提示、提示词 | 实际发送给模型的结构化请求内容，不只是一个字符串 |
| Instructions | 指令 | 对模型行为的高层约束和工作说明 |
| History | 历史 | 经过规范化后可用于构造模型输入的对话项目 |
| Context | 上下文 | 模型本次判断可利用的信息总和；也可能指程序中的环境对象 |
| Context window | 上下文窗口 | 模型一次请求能容纳的 token 空间 |
| World state | 世界状态 | cwd、权限、时间、环境等模型需要知道的当前外部状态 |
| Fragment | 片段 | 可按角色、优先级和大小限制注入上下文的一小块内容 |
| Modality | 模态 | 文本、图片、音频等输入形式 |
| Compaction | 压缩 | 用有语义的较短表示替换过长上下文，使任务能继续 |
| Token budget | Token 预算 | 为上下文剩余空间、提醒和压缩设置的容量策略 |

## 3. 工具与执行

| 词语 | 字面意思 | 在 Codex 中的意思 |
|---|---|---|
| Tool | 工具 | 模型可请求、Runtime 可执行的一项结构化能力 |
| Tool spec | 工具规格 | 给模型看的工具名、说明和参数 schema |
| Tool call | 工具调用 | 模型生成的“请调用某工具并传这些参数”的结构化输出 |
| Tool output/result | 工具输出/结果 | Runtime 执行后写回 history、供模型继续判断的结果 |
| call ID | 调用编号 | 把某次 tool call 与对应结果关联起来的唯一标识 |
| Handler | 处理器 | 接收某类请求或工具调用并实现处理逻辑的代码对象 |
| Router | 路由器 | 根据名称或类型把调用送到正确 Handler |
| Registry | 注册表 | 保存“有哪些实现可用”以及如何找到它们的集合 |
| Runtime | 运行时 | 真正负责执行能力、维护执行状态的程序部分 |
| Backend | 后端 | 某个抽象接口背后的具体实现，例如本地或远端进程执行 |
| Dispatch | 分派 | 判断目标后把任务交给对应实现 |
| Orchestrator | 编排器 | 按审批、沙箱、执行、重试等顺序协调多个组件 |
| Nested tool | 嵌套工具 | Code Mode 的 JS 在一次 `exec` 内再次调用的普通授权工具 |

## 4. 权限与安全

| 词语 | 字面意思 | 在 Codex 中的意思 |
|---|---|---|
| Approval | 批准 | 用户或策略允许 Runtime 尝试某个动作 |
| Sandbox | 沙箱 | 执行端实际强制文件、网络和进程访问边界的机制 |
| Permission profile | 权限配置档 | 对允许读写范围、网络等能力的结构化描述 |
| Policy | 策略 | 用规则决定动作是否允许、是否需要审批或怎样执行 |
| Escalation | 权限提升 | 请求使用比当前默认边界更高的权限执行 |
| Denial | 拒绝 | 权限、沙箱或执行端明确阻止某项访问 |
| Trust boundary | 信任边界 | 数据或控制权从一种信任等级进入另一种等级的位置 |
| Prompt injection | 提示注入 | 不可信内容试图诱导模型忽略上层指令或执行越权动作 |
| Fail closed | 失败时关闭 | 无法确认安全时拒绝操作，而不是默认放行 |

## 5. 状态、持久化与恢复

| 词语 | 字面意思 | 在 Codex 中的意思 |
|---|---|---|
| Rollout | 展开、运行轨迹 | 持久保存的一条执行事件记录，可用于恢复、审计和 Memory 提取 |
| Thread store | Thread 存储 | 用于索引和加载 thread 元数据或持久状态的存储层 |
| Snapshot | 快照 | 某一时刻冻结的状态视图 |
| Baseline | 基线 | 后续 diff 或增量更新用来比较的起点 |
| Diff | 差异 | 相对某个 baseline 新增、删除或变化的内容 |
| Resume | 恢复、继续 | 从持久记录重建新的运行对象并继续工作 |
| Fork | 分叉 | 从已有 history 某个边界创建独立的新 thread |
| Flush | 冲刷、写出 | 把内存中尚未持久化的内容可靠写到存储层 |
| Materialize | 实体化 | 把延迟、引用或分页表示还原成当前操作所需的完整对象 |
| Residency | 驻留状态 | Agent/thread 的运行实例当前是否留在内存中 |

## 6. 网络、缓存与错误处理

| 词语 | 字面意思 | 在 Codex 中的意思 |
|---|---|---|
| Transport | 传输层 | HTTP/SSE、WebSocket 等承载请求和事件的方式 |
| Stream | 流 | 结果不是一次返回，而是按事件持续到达 |
| SSE | 服务端发送事件 | 服务器沿一个 HTTP 响应持续推送事件 |
| WebSocket | Web 套接字 | 可在同一长连接上双向发送消息的协议 |
| Retry | 重试 | 同一语义操作失败后，在规则和预算内再次尝试 |
| Fallback | 回退方案 | 首选路径不可用时改用另一条兼容路径 |
| Cache | 缓存 | 保存可复用结果，减少重复读取、计算或网络请求 |
| Cache hit | 缓存命中 | 当前请求找到了可直接复用的缓存内容 |
| Incremental | 增量式 | 只处理相对已有状态新增加或变化的部分 |
| Preconnect | 预连接 | 正式请求前先建立网络连接，但不发送 prompt |
| Prewarm | 预热 | 提前进行更多准备，使后续正式请求减少延迟 |
| Sticky state | 粘性状态 | 只应在特定作用域内持续沿用的路由或请求状态 |

## 7. 扩展与外部能力

| 词语 | 字面意思 | 在 Codex 中的意思 |
|---|---|---|
| Skill | 技能 | 教模型完成某类任务的方法、约束和资源入口 |
| Plugin | 插件 | 将 Skills、MCP servers、Apps 等组合交付的能力包 |
| MCP | Model Context Protocol | 连接外部工具和资源的一套协议 |
| Connector | 连接器 | 已授权连接某项外部服务的产品层能力 |
| Hook | 钩子 | 在明确生命周期节点运行的外部检查或脚本 |
| Extension | 扩展 | 通过 Rust API 向 Codex 贡献上下文、工具或生命周期逻辑的模块 |
| Contributor | 贡献者 | Extension 中负责提供某一类能力的实现 |
| Catalog | 目录 | 有界展示当前可发现能力的清单 |
| Exposure | 暴露方式 | 某项工具怎样对模型可见：直接、延迟发现或 Code Mode 等 |
| Deferred | 延迟的 | 先不把完整能力放进 prompt，需要时再发现或加载 |
| Binding | 绑定 | 将名称、元数据与某个具体连接/实现固定关联的对象 |

## 8. 多 Agent 与并发

| 词语 | 字面意思 | 在 Codex 中的意思 |
|---|---|---|
| Parent / child agent | 父/子 Agent | 通过持久拓扑关联、但拥有独立 thread 的协作 Agent |
| Spawn | 生成、创建 | 创建子 Agent 的 thread、状态和初始任务 |
| Follow-up | 后续任务 | 给已空闲或已完成目标 Agent 启动新的工作轮次 |
| Interrupt | 中断 | 停止当前 Turn，但通常保留 thread 供以后继续 |
| Mailbox | 邮箱 | Agent 间消息到达和唤醒所使用的通信队列概念 |
| Concurrency | 并发 | 多项工作在时间上重叠推进，不保证真的同时执行 CPU 指令 |
| Lease | 租约 | 在限定时间内声明某项工作由当前 worker 负责 |
| Claim | 领取 | 原子地取得一项待处理工作的所有权 |
| Backoff | 退避 | 失败后逐渐延长再次尝试的等待，防止热循环 |

## 9. API 与源码结构常用词

| 词语 | 字面意思 | 在源码里的常见意思 |
|---|---|---|
| Request / response | 请求/响应 | 一次有 ID 的调用及其直接回答 |
| Notification | 通知 | 服务端主动发出的单向状态消息，不等待客户端回复 |
| Event | 事件 | 表示系统中已经发生某件事的结构化消息 |
| Item | 项目、条目 | Turn 或模型响应中可独立追踪的一项内容 |
| Protocol | 协议 | 不同组件之间约定的数据结构、方法和时序 |
| Schema | 模式、结构说明 | 对 JSON/参数允许哪些字段和类型的机器可读定义 |
| Wire format | 线上格式 | 跨进程或网络实际发送的字段名称和编码形状 |
| Projection | 投影 | 把内部状态转换成某个客户端或 UI 所需视图 |
| Adapter | 适配器 | 在两种接口或数据表示之间转换的组件 |
| Manager | 管理器 | 持有一组对象或状态，并协调其生命周期的组件 |
| Builder | 构建器 | 分步骤收集参数，最后创建复杂对象的辅助类型 |
| Provider | 提供方 | 提供模型、资源或某类实现的具体来源 |
| Payload | 载荷 | 请求、事件或调用中实际携带的数据部分 |
| Serialize | 序列化 | 把内存对象转换成 JSON 等可传输表示 |
| Deserialize | 反序列化 | 把传输数据解析并验证为程序类型 |
| Provenance | 来源、出处 | 内容从哪里产生、由谁提供，用于判断作用域和可信度 |

## 10. 错误处理与可观测性

| 词语 | 字面意思 | 在 Codex 中的意思 |
|---|---|---|
| Retryable | 可重试的 | 在明确条件和预算内可安全再次尝试的错误类别 |
| Terminal error | 终止错误 | 会结束当前 Turn 或请求生命周期的最终错误 |
| Idempotent | 幂等的 | 相同操作重复执行不会产生额外不同副作用 |
| Grace period | 宽限期 | 强制终止前留给任务自行清理的短时间 |
| Recovery | 恢复 | 改变认证、上下文或运行状态后重新回到可工作状态 |
| Observability | 可观测性 | 通过外部信号理解系统内部状态的能力 |
| Telemetry | 遥测 | 系统自动产生并导出的追踪、指标或事件数据 |
| Trace | 追踪链 | 一次任务跨多个步骤或服务的完整关联路径 |
| Span | 跨度 | Trace 中一个有开始、结束和属性的操作单元 |
| Metric | 指标 | 可聚合的计数、耗时或分布数据 |
| Attribute | 属性 | 附在 Span、Event 或 Metric 上的结构化键值 |
| Cardinality | 基数 | 某个标签可能出现的不同值数量；无界高基数会增加监控成本 |
| TTFT | 首 Token 时间 | 从 Turn 开始到模型第一个 token 到达的耗时 |
| TTFM | 首消息时间 | 从 Turn 开始到首个用户可见 message/item 的耗时 |

## 11. 工程修改与设计

| 词语 | 字面意思 | 在源码工作中的意思 |
|---|---|---|
| Impact surface | 影响面 | 一项改动可能触及的类型、运行路径、协议、客户端和测试集合 |
| Acceptance criterion | 验收标准 | 用可观察行为判断修改是否完成的条件 |
| Happy path | 正常路径 | 输入和依赖都成功时的主要执行路径 |
| Edge case | 边界情况 | 空值、超限、并发、取消等容易遗漏的输入或状态 |
| Breaking change | 破坏性变更 | 使已有客户端、配置或持久数据不再兼容的改动 |
| Mechanical change | 机械改动 | 可按固定规则批量完成、基本不增加新语义的改动 |
| Intermediate representation | 中间表示 | 外部协议和具体 Runtime 之间便于内部处理的结构 |
| Abstraction | 抽象 | 用统一接口隐藏本地/远端或不同实现的差异 |
| Persistence | 持久化 | 将状态写入进程退出后仍可读取的存储 |
| Pseudocode | 伪代码 | 用于解释设计、不承诺可直接编译运行的代码 |

## 12. Rust 代码阅读常用词

| 写法 | 普通话解释 |
|---|---|
| `Arc<T>` | 多个异步任务可共同持有同一个对象；最后一个引用消失时释放 |
| `Arc::clone(&value)` | 创建一个新的共享所有权句柄，不是深度复制整个 `value` |
| move | 把值的所有权转交给新变量、闭包或异步任务 |
| borrow / reference | 临时借用值，通过 `&T` 只读或 `&mut T` 可变访问，不取得所有权 |
| `Weak<T>` | 不阻止对象释放的弱引用；使用前需要尝试升级成 `Arc<T>` |
| `Mutex<T>` | 同一时刻只允许一个访问者进入受保护状态的互斥锁 |
| `RwLock<T>` | 允许多个读者或一个写者访问受保护状态的读写锁 |
| interior mutability | 即使外层只有共享引用，也通过锁等受控机制修改内部状态 |
| `Option<T>` | 值可能存在，也可能明确不存在 |
| `Result<T, E>` | 操作要么成功得到 `T`，要么失败得到错误 `E` |
| `?` | 成功时取出值，失败时把错误转换后提前返回 |
| `trait` | 一组行为接口，不同类型可提供各自实现 |
| `impl` | 为某个类型实现方法或 trait |
| `dyn Trait` | 运行时通过 trait object 调用某个具体但被隐藏的实现 |
| generic / `<T>` | 编译时允许同一代码适配满足约束的不同具体类型 |
| `enum` | 一组互斥变体，例如成功、失败、中断 |
| `match` | 根据 enum 或模式穷尽处理不同情况 |
| `async fn` | 返回异步工作，调用者需要 `.await` 等待完成 |
| `Future<Output = T>` | 一项尚未完成、被运行时推进后最终产生 `T` 的计算 |
| `Pin<Box<dyn Future + Send + 'a>>` | 把具体 Future 隐藏在堆上，固定其位置，并允许在生命周期 `'a` 内跨线程推进 |
| lifetime / `'a` | 编译器用来证明引用不会比所借用对象活得更久的关系标记 |
| `Send + Sync` | 类型可在并发任务/线程边界安全传递或共享的约束 |
| `From` / `Into` | 两种类型之间的标准转换约定 |
| derive | 让编译器生成 `Debug`、`Clone`、序列化等常见实现的属性 |
| `Drop` | 对象生命周期结束时自动执行的清理逻辑 |
| `RAII guard` | 创建时占有资源，离开作用域时自动归还或结束计时的对象 |
| `snapshot test` | 将完整输出保存为快照，未来变化时显示整体差异的测试 |

## 13. Async Rust 与并发

| 词语 | 字面意思 | 在源码里的常见意思 |
|---|---|---|
| Task | 任务 | 由 Tokio 等异步运行时调度和推进的轻量工作单元，不等同于操作系统线程 |
| Executor | 执行器 | 检查哪些 Future 已准备好并继续推进它们的调度器 |
| Poll | 轮询、询问 | 询问 Future 已完成还是暂时待定 |
| Waker | 唤醒器 | Future 有进展时通知 Executor 再次 poll 的机制 |
| Channel | 通道 | 不同 task 之间传递消息、状态或唤醒信号的管道 |
| `mpsc` | 多生产者、单消费者 | 多个 sender 把消息排队交给一个 receiver 处理 |
| `oneshot` | 一次发射 | 只传递一个结果的一次性请求回复通道 |
| `watch` | 观察 | 保存最新状态，接收者不保证看到中间每次变化 |
| `broadcast` | 广播 | 让每个订阅者都收到同一事件；慢订阅者可能落后并漏掉旧消息 |
| `Notify` | 通知 | 不承载业务数据，只负责叫醒一个或多个等待者 |
| Backpressure | 背压 | 下游处理不过来时，通过有界队列让上游减慢产生速度 |
| `tokio::select!` | 选择 | 同时等待多个 Future，执行最先准备好的分支 |
| Cancellation safety | 取消安全 | Future 在等待中被丢弃，不会导致关键数据丢失或协议进入半完成状态 |
| Cooperative cancellation | 协作式取消 | task 观察取消信号，主动清理并返回，而不是立即被强杀 |
| Cancellation token | 取消令牌 | 可在多个 task 间共享、用于广播取消状态的对象 |
| Child token | 子令牌 | 父级取消会向它传播，但局部取消不必反向停止父级 |
| JoinHandle | 汇合句柄 | 用于等待、观察结果或中止已 spawn task 的句柄 |
| Detached task | 脱离任务 | 没有保留可管理句柄、继续在后台运行的 task |
| Graceful shutdown | 优雅关闭 | 停止接收新工作，取消或完成在途工作，释放资源后退出 |
| Terminal event | 终态事件 | 表示一次生命周期最终完成、失败或中断的事件，不应互相冲突地重复发出 |

## 14. TUI 与客户端状态

| 词语 | 字面意思 | 在源码里的常见意思 |
|---|---|---|
| TUI | 终端用户界面 | 在终端中接收输入，并把 app-server 状态绘制成文字界面的客户端 |
| Client projection | 客户端投影 | 从后端权威状态派生出的客户端视图，不是 Runtime 状态本身 |
| `ServerNotification` | 服务端通知 | app-server 主动发送给客户端的单向状态消息 |
| `ServerRequest` | 服务端请求 | app-server 发起并等待客户端回答的请求，例如工具审批 |
| `AppEvent` | 应用事件 | TUI 内部表达用户意图、界面操作或后台结果的消息 |
| Routing | 路由 | 根据 thread ID 和事件类型把消息交给正确状态对象 |
| `ThreadEventStore` | Thread 事件存储 | 保存单条 thread 的客户端事件、turn、交互请求和输入状态 |
| Replay | 回放 | 把保存的事件重新应用于 UI，以恢复状态而不重新执行 Agent |
| `ChatWidget` | 聊天组件 | 把协议事件转换成 transcript、状态栏、弹窗等界面状态的主要状态机 |
| Transcript | 文字记录 | 用户、Agent、工具和系统提示组成的可见会话记录 |
| History cell | 历史单元格 | Transcript 中一块可独立渲染的稳定内容 |
| Delta | 增量 | 流式结果中新到达的一小段内容，只适合作为实时预览 |
| Stream controller | 流控制器 | 缓冲并按节奏把增量内容提交到可见界面的对象 |
| Live tail | 实时尾部 | 仍在增长、尚未固定为历史单元格的末尾内容 |
| Commit tick | 提交节拍 | 定期推进流式展示队列的一次客户端时钟事件 |
| Finalize | 最终化、收尾 | 把运行中的临时状态转换为稳定完成状态 |
| Consolidation | 合并整理 | 把多段临时流式 cell 收束为一个稳定 Markdown cell |
| Canonical | 规范、权威的 | 冲突时用于最终对账的协议表示 |
| Scrollback | 回滚查看区 | 终端中已经提交、用户可以向上滚动查看的历史画面 |
| Reflow | 重新排版 | 因终端宽度或最终内容改变而重新计算换行和布局 |
| Redraw | 重新绘制 | 根据最新 UI state 请求生成下一帧终端画面 |
| Composer | 编辑器、创作区 | 用户输入和编辑下一条消息的界面区域 |
| Overlay | 覆盖层 | 显示在聊天主区域之上的审批框、选择器或临时弹窗 |

## 15. Rollout 与持久化存储

| 词语 | 字面意思 | 在源码里的常见意思 |
|---|---|---|
| JSONL | JSON 行格式 | 每行一个独立 JSON 对象、适合顺序追加的文件格式 |
| `RolloutLine` | Rollout 记录行 | 带 timestamp、ordinal 和一个 RolloutItem 的耐久记录 |
| `RolloutItem` | Rollout 项目 | 消息、工具结果、TurnContext 或关键事件等可持久化语义单位 |
| Canonical history | 规范历史 | 恢复和冲突对账时作为权威依据的历史表示 |
| Durable | 耐久的 | 进程退出后仍可靠存在、以后可以重新读取的 |
| Persistence policy | 持久化策略 | 决定哪些运行事件进入 rollout、哪些临时事件被过滤的规则 |
| `RolloutRecorder` | Rollout 记录器 | 通过有界 channel 和后台 writer 顺序写入 JSONL 的对象 |
| Lazy materialization | 延迟实体化 | 先保存路径和 pending items，确定需要时才真正创建文件或投影 |
| Pending item | 待写项目 | 已被 writer 接收、但尚未完成耐久写出的记录 |
| Drain | 排空 | 关闭前把队列中剩余的项目处理完成 |
| Acknowledgement | 确认信号 | 接收方完成 persist、flush 或 shutdown 阶段后返回的 ack |
| `ThreadStore` | Thread 存储接口 | 统一创建、追加、读取、分页、Fork、归档和删除的存储中立边界 |
| `LocalThreadStore` | 本地 Thread Store | 组合 rollout JSONL、SQLite projection 和兼容回退的本地实现 |
| State DB | 状态数据库 | 保存 thread 元数据及其他运行状态的一组 SQLite 数据库 |
| Thread history DB | Thread 历史数据库 | 按 turn/item 物化 paginated history 的独立 SQLite 数据库 |
| Backfill | 回填 | 扫描旧 rollout，为数据库补建缺失 metadata 或 projection |
| Read-repair | 读取修复 | 在读取时顺便校正发现的缺失、过期或错误索引 |
| Reconcile | 协调一致 | 比较 rollout 与数据库记录，并修复可以确认的差异 |
| Ordinal | 顺序号 | 标识 rollout item 逻辑位置的递增编号 |
| Byte offset | 字节偏移 | 标识 JSONL 中具体读取边界的文件位置 |
| Legacy history | 旧式历史 | 主要围绕较完整 rollout 历史读取的兼容契约 |
| Paginated history | 分页历史 | 把 turns/items 投影到数据库后按页读取的历史契约 |
| Cold resume | 冷恢复 | thread 未加载时，从持久记录创建新的 Session 和 task |
| Copied fork | 复制式分叉 | 把来源历史复制进新 thread 的 Fork |
| Referenced fork | 引用式分叉 | 用有边界的 `HistoryPosition` 引用来源 rollout 前缀 |
| Lineage | 血缘、来源链 | Fork 后 source 与 child thread 之间的持久关系 |
| Reference integrity | 引用完整性 | 保证来源 history 未在 child 仍依赖它时被删除 |
| Hard delete | 硬删除 | 删除 rollout 及关联索引、通常不可恢复的操作 |
| Migration | 迁移 | 将旧数据库 schema 或 rollout 格式升级为当前结构 |

## 16. MCP 生命周期

| 词语 | 字面意思 | 在源码里的常见意思 |
|---|---|---|
| MCP | 模型上下文协议 | Codex Host 与外部工具、资源服务通信的标准协议 |
| Host | 宿主 | 管理模型、thread、安全策略和 MCP clients 的 Codex 应用 |
| MCP Client | MCP 客户端 | 连接一个 server、握手、发现工具并发送协议请求的对象 |
| MCP Server | MCP 服务端 | 通过 MCP 暴露工具、资源或其他协议能力的外部服务 |
| Transport | 传输通道 | MCP 消息实际使用的 stdio 或 Streamable HTTP 连接 |
| Handshake | 握手 | `initialize` 阶段交换身份、版本和 capability |
| Tool discovery | 工具发现 | 通过 `tools/list` 获取 server 当前工具目录 |
| Effective server | 有效服务 | 合并配置、Plugin、环境、认证和策略后真正生效的 server |
| `McpRuntime` | MCP 运行时 | 一条 thread 拥有、可原子刷新的 MCP 连接状态 |
| `McpConnectionSet` | MCP 连接集合 | 同一个 runtime generation 中的一组精确 server 连接 |
| `ToolInfo` | 工具信息 | 同时保存原始 server/tool 身份与模型可见身份的元数据 |
| `ToolFilter` | 工具过滤器 | 依次应用 enabled allow-list 和 disabled deny-list |
| Name normalization | 名称规范化 | 清理、限长、去重并为模型生成稳定可调用名称 |
| Catalog revision | 目录版本 | 防止旧 step 在工具目录变化后继续执行过期调用的编号 |
| `McpBinding` | MCP 绑定 | Step 捕获的不可变工具目录、exact clients 和 prepared calls |
| `PreparedMcpCall` | 已准备 MCP 调用 | 冻结 exact client、tool、policy、timeout 和 revision 的执行授权 |
| Exact client | 精确客户端 | 与当前配置、身份、环境和目录完全对应的 ready client |
| Elicitation | 信息引出请求 | MCP server 反向要求 Host 或用户补充字段、选择或确认 |
| Tool annotation | 工具注解 | Server 声明的只读、破坏性、公开世界影响等提示元数据 |
| `CallToolResult` | 工具调用结果 | Server 返回的内容块、结构化内容、业务错误标记和 metadata |
| Business error | 业务错误 | 协议请求成功，但 Server 用 `is_error` 表示动作本身失败 |
| Transport error | 传输错误 | 未收到正常结果，例如断线、超时或握手失败 |
| Dirty refresh | 脏状态刷新 | 配置或认证变化后重新计算并发布 MCP runtime |
| `LazyWhenCached` | 有缓存时延迟启动 | 先利用缓存目录，真正执行前再准备 exact live client |
| Tool catalog cache | 工具目录缓存 | 加速发现的 metadata 缓存，不等于连接或执行权限 |
| TOCTOU | 检查到使用的时间差 | 校验后、执行前状态改变造成旧权限被误用的竞态 |

## 17. Prompt Injection 与安全防线

| 词语 | 字面意思 | 在源码或安全分析中的意思 |
|---|---|---|
| Prompt Injection | 提示注入 | 把不可信资料写成指令，诱导模型越过原始任务或授权 |
| Direct injection | 直接注入 | 当前用户直接给出的越权诱导 |
| Indirect injection | 间接注入 | 恶意指令藏在网页、工单、日志、仓库或工具结果中 |
| Untrusted evidence | 不可信证据 | 可以辅助理解任务，但不能单独建立或扩大用户授权的内容 |
| User authorization | 用户授权 | 用户明确允许的动作、对象、范围、目的和目的地 |
| Risk level | 风险等级 | 待执行动作可能造成的后果严重度 |
| Data exfiltration | 数据外传 | 未经授权把敏感 payload 送到外部 destination |
| Credential probing | 凭据探测 | 寻找或读取当前任务不需要的密码、token、私钥等材料 |
| Persistent security weakening | 持久安全弱化 | 修改策略、启动项或控制文件，使防护在当前动作后仍被削弱 |
| Defense in depth | 纵深防御 | 用多层相互独立的限制避免一个判断错误直接造成最终损害 |
| Least privilege | 最小权限 | 只授予完成当前任务所必需的工具、文件、网络和账号能力 |
| `deny_read_matchers` | 拒绝读取匹配器 | 文件系统策略中明确阻止读取敏感路径的规则 |
| Protected metadata | 受保护元数据 | `.git`、`.agents`、`.codex` 等在可写根下仍默认只读的控制目录 |
| Approval | 审批 | 本来不能自动执行的具体动作获得额外允许的过程 |
| Guardian | 守护审查器 | 对已经进入审批路径的确切动作进行专门安全评估的 review session |
| Exact planned action | 确切待执行动作 | 包含真实工具、命令、参数、目标和账号，而不是模糊意图 |
| Fail closed | 失败时关闭 | 超时、错误或结果不明时不执行动作 |
| Circuit breaker | 熔断器 | 重复拒绝达到条件后停止无休止地再次发起同类审查 |
| Egress | 外发出口 | 数据离开当前环境或信任边界的网络、MCP、App 等通道 |
| Payload | 载荷 | 被读取、写入或发送的实际数据 |
| Destination | 目的地 | 数据或外部动作最终到达的域名、服务、账号或人 |
| Blast radius | 影响半径 | 一次错误或攻击成功后最多能够影响的资源范围 |
| Supply chain | 供应链 | Plugin、MCP server、依赖、配置来源及发布渠道构成的能力来源链 |
| Detection | 检测 | 发现可疑或已经发生的行为 |
| Recovery | 恢复 | 回滚文件、撤销外部操作或修复受影响状态 |

## 18. 系统化调试

| 词语 | 字面意思 | 在 Codex 调试中的意思 |
|---|---|---|
| Symptom | 症状 | 用户可见的异常现象，还不是经过验证的根因 |
| Root cause | 根因 | 能完整解释症状、且修复后症状消失的底层原因 |
| Last known boundary | 最后已知边界 | 最后一个有事件、状态或日志证明已经正常通过的阶段 |
| Expected next event | 预期下一事件 | 根据协议生命周期，当前事件之后应出现的事件 |
| Paired events | 配对事件 | 使用同一 ID 关联的 Begin/End 或 Started/Completed 事件 |
| Forward progress | 向前进展 | 状态仍在产生新事件并向终态移动 |
| Stall | 停滞 | 没有已知等待原因，也没有继续产生预期进展 |
| Waiting flag | 等待标志 | `WaitingOnApproval`、`WaitingOnUserInput` 等明确等待状态 |
| Terminal event | 终态事件 | 闭合一次 turn 的 `TurnComplete` 或 `TurnAborted` |
| Lifecycle closure | 生命周期闭合 | Begin/Started 最终出现相同身份的 End/Completed/Aborted |
| Correlation ID | 关联 ID | 把不同组件中的记录对应到同一 thread、turn、item 或 call |
| `thread_id` | 任务线 ID | 关联同一条长期可恢复对话任务的标识 |
| `turn_id` | 执行轮 ID | 关联一次用户输入触发的执行生命周期 |
| `call_id` | 调用 ID | 关联工具建议、审批、Begin、输出和 End |
| `item_id` | 项目 ID | 关联消息、推理或工具 item 的增量与最终内容 |
| TTFT | 首 Token 时间 | 从 turn 开始到模型首个 token 的耗时 |
| Turn Profile | Turn 耗时画像 | 把耗时拆分到 sampling、tool、compaction 等阶段 |
| Backoff | 退避 | 重试前等待，并通常随失败次数增长 |
| Transport fallback | 传输回退 | WebSocket 等主传输失败后切到备用传输 |
| Minimal reproduction | 最小复现 | 保留问题特征、去除无关配置和任务的最小案例 |
| Control variable | 控制变量 | 对照实验中一次只改变一个的因素 |
| Targeted logging | 定向日志 | 只提高相关 crate/module 的日志级别 |
| Redaction | 脱敏 | 分享前移除凭据、隐私、内部地址和无关内容 |
| Watch mode | 监视模式 | 持续监听变化、按设计不会自行结束的命令模式 |

## 19. 从诊断到最小修复

| 词语 | 字面意思 | 在源码修改中的意思 |
|---|---|---|
| Defect classification | 缺陷分类 | 判断现象属于产品 bug、配置/指导缺口还是预期行为 |
| Hypothesis | 假设 | 能被具体实验推翻的根因解释 |
| Falsifiable | 可证伪 | 存在一种观察结果可以证明假设错误 |
| Invariant | 不变量 | 状态或实现变化时始终必须成立的行为规则 |
| Code ownership | 代码所有权 | 最早负责维持某项不变量的 crate、module 或组件 |
| Impact surface | 影响面 | 改动可能波及的调用点、协议、存储、构建和平台 |
| Regression test | 回归测试 | 固定缺陷条件与正确结果、防止问题再次出现的测试 |
| Red/Green/Refactor | 红绿重构 | 先让测试正确失败，再最小修复通过，最后整理结构 |
| Test harness | 测试脚手架 | 提供环境、mock、fixture、动作和观察接口的复用设施 |
| Fixture | 固定样例 | 为测试准备的可重复输入、文件或服务响应 |
| Mock server | 模拟服务 | 用可控响应替代真实模型或外部服务的测试 server |
| Assertion | 断言 | 测试对实际结果必须满足条件的声明 |
| Deep equality | 深度相等 | 比较完整嵌套对象，而非零散字段 |
| Integration test | 集成测试 | 验证多个真实组件通过公开边界连接后的行为 |
| Snapshot test | 快照测试 | 保存完整输出并在变化时提供可审查 diff 的测试 |
| Flaky test | 不稳定测试 | 相同条件下偶发成功或失败的测试 |
| Public boundary | 公开边界 | 客户端、其他 crate 或持久数据实际依赖的 API/wire 行为 |
| Churn | 无关扰动 | 与本次目标无关的格式、移动、重命名或重构变化 |
| Generated artifact | 生成产物 | schema、snapshot、lock 等由源定义派生并需同步的文件 |
| Targeted test | 定向测试 | 针对单个行为、test 或 crate 的快速验证 |
| Diff review | 差异审查 | 提交前逐行核对最终修改、范围和风险 |

## 20. Pull Request 与 CI

| 词语 | 字面意思 | 在协作开发中的意思 |
|---|---|---|
| Working tree | 工作树 | 当前磁盘文件相对 Git 快照的修改、暂存和未跟踪状态 |
| Commit | 提交 | 带父节点、作者和说明的一份 Git 内容快照 |
| Branch | 分支 | 指向一串 commit 最新位置的可移动引用 |
| Pull Request | 拉取请求 | 请求评审并把 head branch 的差异合入 base branch 的协作对象 |
| Base branch | 基础分支 | PR 准备合入的目标分支 |
| Head branch | 头部分支 | 提供 PR 改动的来源分支 |
| Topic branch | 主题分支 | 只承载一个清晰功能或修复的分支 |
| Atomic commit | 原子提交 | 可独立理解、编译、测试和回滚的逻辑修改 |
| Draft PR | 草稿 PR | 尚未声明可合并、用于早期协作的状态 |
| Ready for review | 可评审 | 作者认为实现、验证和说明已经达到合并评审标准 |
| Reviewability | 可评审性 | 评审者理解、验证和判断改动风险的难易程度 |
| Semantic diff | 语义差异 | 真正改变程序行为或契约的代码变化 |
| Mechanical diff | 机械差异 | 按固定规则生成、重命名或格式化的变化 |
| Stacked PR | 堆叠 PR | 后一个 PR 依赖尚未合并的前一个 PR |
| Rebase | 变基 | 把 commit 重新应用到更新后的 base history 上 |
| Merge conflict | 合并冲突 | Git 无法自动判断两条历史应怎样组合的区域 |
| CODEOWNERS | 代码所有者 | 将路径映射到负责 review 的用户或团队的仓库规则 |
| Blocking CI | 阻塞式 CI | 必须成功才能满足分支保护并合并的检查集合 |
| Path filter | 路径过滤 | 根据 PR 改了哪些文件决定哪些 CI job 需要运行 |
| CI matrix | CI 矩阵 | 在多个 OS、target 或配置上展开同类检查 |
| Shard | 分片 | 把大型测试集合拆给多个并行 job |
| Flaky test | 不稳定测试 | 相同代码和输入下偶发通过或失败的测试 |
| Infrastructure failure | 基础设施失败 | runner、缓存、网络或外部服务导致的非代码故障 |
| Squash and merge | 压缩合并 | 将 PR 的多个 commit 压成一个后合入 base |
| Force push | 强制推送 | 重写远端分支历史，使已有 commit identity 变化 |
| CLA | 贡献者许可协议 | 外部代码贡献者需要接受的法律授权协议 |
| Security disclosure | 安全披露 | 通过受控私密渠道报告并协调修复漏洞的流程 |

## 21. 代码审查实战

| 词语 | 字面意思 | 在代码审查中的意思 |
|---|---|---|
| Finding | 发现项 | 有触发条件、错误行为、用户影响、位置和修复方向的问题 |
| Blocking finding | 阻塞问题 | 合并前必须修复或得到明确结论的问题 |
| Non-blocking suggestion | 非阻塞建议 | 能改善维护性但不应阻止当前正确改动合并的意见 |
| Review question | 评审问题 | 证据不足时向作者确认约束或意图，不直接断言为 bug |
| Priority | 优先级 | P0–P3，综合影响、可能性和可恢复性的处理紧迫度 |
| Severity | 严重度 | 问题一旦发生会造成多大后果 |
| Trigger | 触发条件 | 让缺陷实际发生的输入、状态或事件顺序 |
| User impact | 用户影响 | 缺陷造成的错误状态、数据、安全、性能或兼容结果 |
| Inline comment | 行内评论 | 关联到具体 diff 行的 finding |
| Tight line range | 紧凑行范围 | 只标记直接引入问题的最少代码行 |
| False positive | 误报 | 看起来有风险、实际被类型不变量或现有 guard 排除的结论 |
| Root-cause deduplication | 根因去重 | 合并由同一代码根因造成的重复症状评论 |
| Event schedule | 事件交错 | 用编号步骤证明并发执行怎样触发错误 |
| Late event | 迟到事件 | 属于旧 turn/call，却在新状态建立后才到达的事件 |
| Idempotence | 幂等性 | 同一事件重复应用不会继续改变正确结果 |
| Unbounded retention | 无界留存 | 数据没有硬上限并随输入或时间持续占用资源 |
| History rewrite | 历史重写 | 删除或修改已经建立的模型可见上下文项 |
| Prompt cache miss | Prompt 缓存未命中 | 不稳定前缀使已有推理请求缓存无法复用 |
| Wire contract | 线路契约 | 跨进程/网络序列化数据的结构与语义约定 |
| Staged landing | 分阶段落地 | 将大改动按依赖拆成可独立合并、验证和回滚的阶段 |

## 22. 性能回归与基准测试

| 词语 | 字面意思 | 在性能工程中的意思 |
|---|---|---|
| Performance regression | 性能回归/退化 | 相同工作负载在新版本中耗时更长、吞吐更低或资源更多 |
| Benchmark | 基准测试 | 用固定工作负载重复运行和统计性能的程序 |
| Microbenchmark | 微基准 | 隔离单个函数或小组件，尽量排除其他系统成本 |
| Macrobenchmark | 宏基准 | 覆盖完整进程或多个真实组件的较大路径 |
| End-to-end benchmark | 端到端基准 | 从用户/进程入口一直测到可观察结果边界 |
| Latency | 延迟 | 一次操作从开始到指定完成边界所需时间 |
| Throughput | 吞吐量 | 单位时间能够完成的工作数量 |
| Wall-clock time | 墙钟时间 | 现实时间经过量，包括 CPU、I/O、锁和调度等待 |
| CPU time | CPU 时间 | 处理器真正执行当前工作的时间 |
| User-perceived latency | 用户感知延迟 | 从用户动作到首次反馈、首 token 或最终结果的等待 |
| Workload | 工作负载 | 被测输入、状态、规模、格式、并发和 cache 条件的组合 |
| Fixture | 固定样例 | 为测试或 benchmark 提供的可重复输入 |
| Synthetic fixture | 合成样例 | 由程序构造、保留真实数据关键特征的稳定输入 |
| Baseline | 基线 | 用来与新版本比较的原版本、提交或测量结果 |
| Candidate | 候选版本 | 正在评估的新实现、补丁或提交 |
| Sample | 样本 | Benchmark 收集的一次或一组测量观察 |
| Iteration | 迭代 | 重复执行一次被测操作 |
| Warm-up | 预热 | 正式采样前运行，使初始化完成或进入稳定状态 |
| Cold path | 冷路径 | 首次初始化、首次处理或 cache 未命中的路径 |
| Hot path | 热路径 | 已完成初始化、经常执行或 cache 可命中的路径 |
| Cache hit | 缓存命中 | Cache 中已有对应结果，可避免完整计算 |
| Cache miss | 缓存未命中 | Cache 中没有对应结果，必须执行完整工作 |
| Digest | 内容摘要 | 从内容计算的稳定标识，常用于 cache key |
| Mean | 均值 | 所有样本总和除以样本数量 |
| Median / p50 | 中位数 | 排序后位于中间、50% 样本不超过的值 |
| Percentile | 百分位数 | 指定比例样本不超过的数值，如 p95、p99 |
| Tail latency | 尾延迟 | 分布中较慢末端请求的耗时 |
| Outlier | 离群值 | 明显偏离大多数样本的观察值，可能是噪声也可能是真慢路径 |
| Noise | 噪声 | 与目标代码变化无关的测量波动 |
| Jitter | 抖动 | 相邻测量值随调度、环境等因素出现的不稳定变化 |
| Histogram | 直方图 | 把样本按多个数值范围累计，以观察完整分布 |
| Bucket | 桶 | Histogram 中的一个数值范围及其样本计数 |
| Profile | 性能剖析 | 定位 CPU、内存或阶段耗时集中位置的分析 |
| Hotspot | 性能热点 | 消耗大量时间、CPU 或 allocation 的代码位置 |
| Critical path | 关键路径 | 决定端到端结果最早完成时间的依赖链 |
| Contention | 资源争用 | 多个线程或 task 同时竞争锁、channel 或共享资源 |
| Allocation | 内存分配 | 为新对象申请内存的操作和成本 |
| Peak RSS | 峰值常驻内存 | 进程驻留物理内存达到的最大近似值 |
| Benchmark harness | 基准框架 | 负责重复执行、计时、采样与统计的运行器 |
| Smoke run | 冒烟运行 | 只验证 benchmark 能编译、启动和完成，不形成性能结论 |
| Dead-code elimination | 死代码消除 | 编译器删除结果没有可观察用途的计算 |
| Black box | 黑盒屏障 | 阻止编译器预先假定输入/结果无关并消除被测工作 |
| Amdahl’s Law | 阿姆达尔定律 | 总体提升受被优化部分原本占总耗时的比例限制 |

## 23. 性能剖析

| 词语 | 字面意思 | 在性能调查中的意思 |
|---|---|---|
| Profiling | 性能剖析 | 采集调用栈、时间或内存证据，定位资源消耗位置 |
| Trace | 踪迹 | 一次操作跨组件、进程和服务的完整因果时间线 |
| Span | 时间跨度 | Trace 中一个有开始、结束和结构化属性的操作区间 |
| Event | 事件 | Span 时间线中的瞬时记录，没有独立持续时间 |
| Attribute | 属性 | 用于筛选、分组和解释观测对象的结构化字段 |
| Parent/child span | 父子跨度 | 表示上层操作与其发起的下层操作之间的因果关系 |
| Trace context | 追踪上下文 | 跨调用传播 trace 与 parent 身份的元数据 |
| `traceparent` | 追踪父标头 | W3C Trace Context 中携带 trace ID、parent span ID 等的字段 |
| `tracestate` | 追踪状态 | W3C Trace Context 中携带附加系统/厂商状态的字段 |
| Context propagation | 上下文传播 | 将 Trace 身份通过 header、envelope 或环境带到下个边界 |
| Waterfall | 瀑布时间图 | 按真实时间展示 Span 起止、嵌套和重叠的图 |
| Sampling profiler | 采样剖析器 | 周期性记录调用栈，以样本比例估计 on-CPU 分布 |
| Flame graph | 火焰图 | 将采样调用栈聚合，用框宽表示样本占比的图 |
| Stack frame | 栈帧 | 一条调用栈中的一个函数调用位置 |
| Inclusive time | 包含时间 | 函数自身和所有子调用的总成本 |
| Self/exclusive time | 自身时间 | 函数自身执行、不含子调用的成本 |
| On-CPU | CPU 上执行 | 线程/任务当前真正使用处理器执行指令 |
| Off-CPU | CPU 外等待 | 因锁、I/O、休眠或调度而没有执行 CPU 指令 |
| CPU-bound | CPU 受限 | 性能主要受计算工作或处理器能力限制 |
| I/O-bound | I/O 受限 | 性能主要受磁盘、网络、子进程或远端等待限制 |
| Lock contention | 锁争用 | 多个线程或 task 同时等待同一个同步锁 |
| Critical section | 临界区 | 获得锁后到释放锁前的受保护代码区域 |
| Lock wait | 锁等待时间 | 请求锁到成功获得锁之间的耗时 |
| Lock hold | 持锁时间 | 成功获得锁到释放锁之间的耗时 |
| Queue wait | 排队时间 | 工作入队后到消费者真正开始处理的等待 |
| Service time | 服务时间 | 不含排队、worker 实际处理一次工作的时间 |
| Backlog | 积压 | 已进入队列但尚未完成的工作集合 |
| Backpressure | 背压 | 下游容量不足时限制上游继续产生工作的机制 |
| In-flight | 处理中 | 已经开始或入队、但尚未终结的工作 |
| Semaphore | 信号量 | 用有限许可证控制同时执行数量的同步原语 |
| Allocation profile | 分配剖析 | 统计内存申请次数、字节和来源调用栈 |
| Live bytes | 存活字节 | 当前仍被程序引用、不能回收的内存 |
| Retained memory | 保留内存 | 因引用链或 cache 等原因继续存活的内存 |
| Memory leak | 内存泄漏 | 本应释放的数据持续失去回收机会而增长 |
| Cardinality | 基数 | Metric tag 或 attribute 可能出现的不同值数量 |
| Observer effect | 观察者效应 | Instrumentation 和 profiler 本身改变被测系统表现 |
| Head sampling | 头部采样 | 请求开始时就决定是否保留 Trace |
| Tail sampling | 尾部采样 | 得知请求耗时、错误等结果后决定是否保留 Trace |
| Rollout trace | Rollout 追踪 | Codex 本地原始运行证据和离线语义图，不是 CPU profile |

## 24. 容量规划与过载保护

| 词语 | 字面意思 | 在容量设计中的意思 |
|---|---|---|
| Capacity planning | 容量规划 | 根据工作负载、SLO 和资源预算确定安全承载边界 |
| Overload | 过载 | 到达工作持续超过系统可接受处理能力 |
| Saturation | 饱和 | CPU、permit、连接或其他关键资源接近全部占用 |
| Utilization | 利用率 | 某项资源当前被使用的比例 |
| Concurrency | 并发 | 同一时刻正在执行或尚未完成的工作数量 |
| In-flight | 处理中 | 已被系统接纳但尚未到达终态的工作 |
| Queue depth | 队列深度 | 已到达但尚未开始处理的工作数量 |
| Backlog | 积压 | 消费速度不足而累计的未完成工作 |
| Burst | 突发 | 短时间内集中到达的大量工作 |
| Steady state | 稳态 | 输入、输出和资源使用长期近似平衡的状态 |
| Headroom | 容量余量 | 为突发、故障、恢复和测量误差预留的资源 |
| Bounded queue | 有界队列 | 有明确最大 item 或字节容量的等待队列 |
| Semaphore | 信号量 | 用有限 permit 限制同时进入某区域的执行者 |
| Permit | 许可证 | Semaphore 中一个可占用并最终归还的并发名额 |
| Rate limiter | 速率限制器 | 限制一个时间窗口内准入工作数量的机制 |
| Connection pool | 连接池 | 管理和复用网络连接或 HTTP client 的资源集合 |
| Admission control | 准入控制 | 在工作进入系统前根据预算决定接纳或拒绝 |
| Load shedding | 负载卸载 | 过载时主动拒绝部分工作保护整体可用性 |
| Overload rejection | 过载拒绝 | 不接纳请求并立即返回明确 busy/overloaded 错误 |
| Head-of-line blocking | 队首阻塞 | 最前面的慢工作阻挡后续快工作 |
| Fairness | 公平性 | 不同调用方或工作类型合理分享有限资源 |
| Starvation | 饥饿 | 某类工作因持续竞争而长期得不到执行 |
| Deadline | 截止预算 | 整条操作必须完成的最终时间或剩余时间预算 |
| Timeout | 超时 | 某个局部等待或操作允许持续的最长时间 |
| Retry | 重试 | 一次失败后重新尝试同一逻辑工作 |
| Exponential backoff | 指数退避 | 重试间隔随尝试次数按倍数增加 |
| Jitter | 随机抖动 | 给退避时间增加随机偏移以打散同步重试 |
| Retry storm | 重试风暴 | 大量重试流量进一步压垮已经失败或过载的依赖 |
| Thundering herd | 惊群 | 大量等待者在同一时刻醒来并冲击同一资源 |
| Fallback | 回退 | 主路径失败后切换到备用传输或实现 |
| Circuit breaker | 熔断器 | 重复失败达到条件后暂时停止继续尝试 |
| Cooldown | 冷却期 | 失败后主动等待、不允许立即再次尝试的时间 |
| Singleflight | 单飞 | 多个同类并发请求只执行一份真实加载或恢复工作 |
| Graceful degradation | 优雅降级 | 过载时减少非关键能力，同时保持正确和安全契约 |
| Cascading failure | 级联故障 | 一个组件的故障或过载沿调用链扩散到其他组件 |
| Durable queue | 持久队列 | 写入数据库/磁盘、重启后仍保留的等待工作 |
| Drain | 排空 | 停止接纳新工作并完成或终止已有工作 |
| Survivor bias | 幸存者偏差 | 只看成功请求而忽略拒绝、丢弃和超时工作 |

## 25. 发布、灰度、回滚与事故响应

| 英文 | 常见中文 | 在本课程中的含义 |
|---|---|---|
| Release | 发布 | 给一组构建制品赋予版本并使其可分发 |
| Deployment | 部署 | 把一个确切版本安装或启动到具体环境 |
| Rollout | 灰度/逐步放量 | 控制新版本或功能从小范围逐渐扩大 |
| Artifact | 制品 | 构建产生的二进制、压缩包、tarball、wheel 等文件 |
| Build matrix | 构建矩阵 | 对 OS、CPU、bundle 等参数组合分别执行构建 |
| Release gate | 发布门禁 | 前置校验成功后才允许签名、发布或扩大范围 |
| Git tag | Git 标签 | 指向确切 Git 对象并可触发版本发布的命名引用 |
| Commit SHA | 提交摘要 | 精确标识一份源码快照的哈希值 |
| Checksum / digest | 校验摘要 | 从文件字节计算、用于检查完整性的值 |
| Code signing | 代码签名 | 用可信身份认证制品来源和签名后完整性 |
| Notarization | 公证 | 平台服务对已签名软件的检查和登记 |
| Debug symbols | 调试符号 | 将机器地址还原为函数和源码位置的信息 |
| Prerelease | 预发布 | alpha、beta 等尚未进入稳定通道的版本 |
| Canary | 金丝雀 | 最早验证新变化的一小组对象或专门验证任务 |
| Control group | 对照组 | 保持旧版本/旧行为以供同期比较的对象 |
| Observation window | 观察窗口 | 每一 rollout 阶段必须持续观测的时间范围 |
| Guardrail metric | 护栏指标 | 越界时应暂停、关闭或回退的伤害边界指标 |
| Abort threshold | 中止阈值 | 发布前定义的暂停或回退触发条件 |
| Blast radius | 影响半径 | 一次故障可能伤害的对象数量和范围 |
| Kill switch | 紧急关闭开关 | 经验证能快速关闭风险路径并确认收敛的机制 |
| Rollback | 回滚 | 将 artifact 或配置恢复到旧的兼容版本 |
| Roll-forward | 向前修复 | 发布包含最小修复的新版本替代问题版本 |
| Data repair | 数据修复 | 修正版本切换无法消除的既有错误状态 |
| Migration | 迁移 | 演进数据库 schema、文件格式或持久化数据 |
| Backfill | 回填 | 为已有记录补齐新结构需要的数据 |
| Dual write | 双写 | 过渡期间同时写入新旧两套结构 |
| Expand–migrate–contract | 扩展—迁移—收缩 | 先兼容增加，再转移数据，最后删除旧结构 |
| Mixed-version operation | 混合版本运行 | 新旧 client、server 或进程在一段时间内并存 |
| Incident | 事故 | 对用户、数据、安全或服务目标造成显著影响的事件 |
| Mitigation | 缓解/止血 | 先限制当前伤害，不一定已经修复根因 |
| Incident commander | 事故指挥 | 维护全局状态、优先级和关键决定的角色 |
| Runbook | 操作手册 | 可执行、可验证的发布、止血和恢复步骤 |
| Postmortem | 事故复盘 | 事后分析影响、机制、防线和改进行动 |
| Root cause | 根因 | 导致系统失效的核心机制 |
| Trigger | 触发因素 | 使潜在缺陷在特定时刻显现的事件 |
| Contributing factor | 促成因素 | 增加事故概率、影响面或恢复时间的条件 |
| Blameless | 非责备式 | 不把分析停在个人粗心，而是改进系统条件 |
| Action item | 行动项 | 有负责人、期限和可验证完成条件的改进任务 |
| Provenance | 来源证明 | 制品由哪份源码经什么可信过程产生的证据 |

## 26. SLO、错误预算与可靠性决策

| 英文 | 常见中文 | 在本课程中的含义 |
|---|---|---|
| Reliability | 可靠性 | 系统持续提供符合用户预期行为的能力 |
| Availability | 可用性 | 服务在需要时能否接受并完成工作 |
| Durability | 持久性 | 已确认保存的数据能否长期保留并恢复 |
| Correctness | 正确性 | 输出或状态是否真正符合任务要求 |
| Safety invariant | 安全不变量 | 不允许用普通错误预算交换的硬边界 |
| SLI | 服务水平指标 | 实际测得的 good events 与 valid events 关系 |
| SLO | 服务水平目标 | 团队在特定窗口内希望达到的服务标准 |
| SLA | 服务水平协议 | 对外正式承诺、责任、排除和补偿条款 |
| Error budget | 错误预算 | SLO 所允许的不良事件或坏时间额度 |
| Numerator | 分子 | SLI 中满足目标的 good events 数量 |
| Denominator | 分母 | SLI 统计范围内的 valid events 总数 |
| Good event | 好事件 | 满足该用户旅程目标的有效事件 |
| Bad event | 坏事件 | 进入分母但没有满足目标的事件 |
| Excluded event | 排除事件 | 根据预先规则不进入某个 SLI 的事件 |
| Unknown | 未知 | 因缺少终态或 telemetry 无法可靠分类的事件 |
| Objective window | 目标窗口 | SLO 计算采用的滚动或固定日历区间 |
| Threshold-based SLI | 阈值型指标 | 按每个事件是否低于延迟等阈值分类 good/bad |
| Burn rate | 燃烧速率 | 当前预算消耗速度相对允许速度的倍数 |
| Budget remaining | 剩余预算 | 当前窗口尚可容忍的不良事件或时间 |
| Multi-window alert | 多窗口告警 | 同时用长短窗口确认持续性和当前严重度 |
| User journey | 用户旅程 | 用户跨多个系统边界真正想完成的端到端目标 |
| Logical operation | 逻辑操作 | 对用户只算一次、内部可能含多次 attempt 的工作 |
| Failure taxonomy | 故障分类体系 | 对错误原因和责任边界作稳定划分的规则 |
| Dependency budget | 依赖预算 | 顶层可靠性风险分配给某个下游或阶段的份额 |
| Error budget policy | 错误预算政策 | 根据预算状态决定发布和工程优先级的规则 |
| Synthetic probe | 合成探测 | 自动执行固定用户旅程以检查系统能力 |
| Instrumentation | 埋点/观测代码 | 产生 metric、log、trace 和事件的实现 |
| Cardinality | 基数 | 一个 metric label 可能出现的不同值数量 |
| Telemetry gap | 遥测缺口 | 预期观测事件丢失、无法配对或覆盖不完整 |
| Rage cancel | 等待过久取消 | 用户因系统迟缓主动中断，可能是延迟退化信号 |

## 27. 故障注入、混沌工程与韧性测试

| 英文 | 常见中文 | 在本课程中的含义 |
|---|---|---|
| Fault | 故障原因 | 被注入或自然出现的底层异常条件 |
| Error | 错误状态 | Fault 在系统内部造成的不正确状态 |
| Failure | 服务失败 | 系统最终没有向用户提供预期行为 |
| Fault injection | 故障注入 | 在受控条件下主动制造某个具体失败 |
| Chaos engineering | 混沌工程 | 基于稳态假设和护栏，在真实组合条件下验证韧性 |
| Resilience | 韧性 | 故障期间限制影响并在之后恢复的能力 |
| Steady state | 稳态 | 实验前后应保持的可接受服务范围 |
| Hypothesis | 假设 | 实验准备证伪的明确预期结果 |
| Blast radius | 影响半径 | 实验最多可影响的实例、请求、用户和数据范围 |
| Abort condition | 中止条件 | 触发后必须立即停止实验的预定义边界 |
| Fault containment | 故障隔离 | 阻止一个连接或组件的错误向其他部分扩散 |
| Omission | 遗漏故障 | 预期消息、响应、写入或终态没有发生 |
| Delay | 延迟故障 | 操作最终可能完成，但远晚于预期 |
| Duplication | 重复故障 | 相同消息、请求或副作用被执行多次 |
| Reordering | 乱序故障 | 事件到达顺序不同于逻辑顺序 |
| Corruption | 数据损坏 | 数据存在但格式、内容或校验无效 |
| Resource exhaustion | 资源耗尽 | 队列、连接、内存、磁盘或 permit 达到上限 |
| Network partition | 网络分区 | 组件各自运行，但相互不能通信 |
| Failpoint | 故障点 | 让测试在特定代码位置暂停、失败或崩溃的控制点 |
| Mock server | 模拟服务器 | 可编程返回固定响应并记录请求的测试服务 |
| Fake clock | 假时钟 | 由测试手动推进而不依赖真实等待的时间源 |
| Determinism | 确定性 | 相同输入和条件稳定产生相同测试结果 |
| Flaky test | 不稳定测试 | 代码未变时也可能随机通过或失败的测试 |
| Safety | 安全性 | 故障期间不发生不可接受副作用或损坏 |
| Liveness | 活性 | 故障解除后系统最终能够继续推进 |
| Boundedness | 有界性 | 资源、重试和等待不会无限增长 |
| Idempotency | 幂等性 | 操作重复执行仍产生等价最终状态 |
| Failover | 故障转移 | 从失败主节点切换到冗余节点 |
| Fallback | 备用回退 | 切换到能力可能不同的备用实现或传输 |
| Recovery | 恢复 | 系统重新回到可接受稳态 |
| Repair | 数据修复 | 改正已经形成的损坏或不一致状态 |
| RTO | 恢复时间目标 | 故障后恢复服务允许的最长时间 |
| RPO | 恢复点目标 | 最多允许丢失多新的已确认数据 |
| Crash consistency | 崩溃一致性 | 任意中断点之后数据仍可识别并恢复 |
| Journal | 事务日志/标记 | 记录未完成持久步骤、供重启恢复的耐久线索 |
| Soak test | 浸泡测试 | 长时间重复运行以发现慢性泄漏和累积退化 |
| Game day | 故障演练日 | 多角色共同验证告警、响应、权限和恢复流程 |
| Property-based test | 属性测试 | 生成许多输入以验证一般不变量 |
| Model-based test | 模型测试 | 将真实状态转换与简化状态机比较 |
| Metamorphic test | 变形测试 | 验证输入变化前后的结果关系 |

## 28. 依赖管理与软件供应链安全

| 英文 | 常见中文 | 在本课程中的含义 |
|---|---|---|
| Dependency | 依赖 | 构建、测试或运行所需的内部或外部组件 |
| Direct dependency | 直接依赖 | 第一方 manifest 直接声明的 package/crate |
| Transitive dependency | 传递依赖 | 由其他 dependency 继续引入的下游组件 |
| Manifest | 依赖清单 | 声明版本范围、feature、script 和来源的文件 |
| Resolver | 解析器 | 根据全部约束计算实际依赖版本图的逻辑 |
| Lockfile | 锁文件 | 固定版本、来源、checksum 和传递关系的解析结果 |
| Registry | 注册表 | 发布、索引和下载 package/crate 的服务 |
| Version constraint | 版本约束 | Resolver 被允许选择的版本范围 |
| Feature | 功能特性 | 选择性编译某些依赖代码和能力的 Cargo 开关 |
| Build script | 构建脚本 | 编译或安装期间自动执行的程序 |
| Lifecycle script | 生命周期脚本 | npm 在 install、prepare、publish 等阶段执行的 script |
| Procedural macro | 过程宏 | Rust 编译期执行并产生或变换 token 的代码 |
| Git revision / `rev` | Git 修订点 | 固定 Git dependency 的确切不可变 commit |
| Source allowlist | 来源白名单 | 只准依赖从已审查 registry 或 Git URL 获取 |
| Checksum / digest | 校验摘要 | 用 SHA-256 等算法核对下载和 artifact 字节 |
| Signature | 数字签名 | 认证签名身份并检测签名后字节变化的证据 |
| Provenance | 来源证明 | 连接源码、builder、workflow、输入和 artifact 的证据 |
| SBOM | 软件物料清单 | 列出产品包含的组件、版本和来源 |
| Advisory | 安全公告 | 描述受影响版本、风险条件和修复的记录 |
| Reachability | 可达性 | 受影响代码在实际 feature、target 和调用路径中能否执行 |
| SPDX | 软件包数据交换标准 | 标准化许可证标识和软件物料信息的体系 |
| Frozen lockfile | 冻结锁文件 | Manifest 与 lock 不一致时失败，不允许 CI 现场重算 |
| Minimum release age | 最短发布时间 | 新依赖版本进入解析前必须经过的冷却期 |
| Dependency confusion | 依赖混淆 | 内外部同名包让 resolver 选中攻击者版本 |
| Typosquatting | 拼写抢注 | 用近似正规包名诱导错误安装 |
| OIDC | OpenID Connect | Workflow 用短期身份令牌向发布或签名服务认证 |
| Trusted publishing | 可信发布 | Registry 按受信 CI 身份授权发布而非使用长期 token |
| Least privilege | 最小权限 | Job 只拥有完成当前工作所必需的能力 |
| Cache poisoning | 缓存投毒 | 恶意内容进入后续受信构建会恢复的 cache |
| Hermetic build | 密封构建 | 只读取明确声明输入、不依赖隐式宿主状态的构建 |
| Reproducible build | 可复现构建 | 独立构建能得到逐字节相同输出 |
| Evidence status | 证据状态 | Confirmed、needs verification、ruled out 等事实可信度 |
| Credential rotation | 凭据轮换 | 撤销可能泄漏的旧凭据并签发新凭据 |

## 29. 跨平台工程与远程执行测试

| 英文 | 常见中文 | 在本课程中的含义 |
|---|---|---|
| Host OS | 主机操作系统 | 运行当前 app-server、测试或控制进程的 OS |
| Target OS | 目标操作系统 | 真正解释路径和执行命令的 executor OS |
| Placement | 执行位置 | 工作在当前进程本地还是 remote exec-server |
| Executor | 执行器 | 操作目标文件系统、启动和终止进程的组件 |
| Control plane | 控制平面 | 选择、配置、编排和观察 environment 的层 |
| Data plane | 数据平面 | 真正执行命令、访问文件和回传输出的层 |
| EnvironmentInfo | 环境信息 | Exec-server 报告的 target Shell、cwd 和 capability |
| `PathBuf` | 路径对象 | 使用当前 host 语义的 Rust 标准路径类型 |
| `AbsolutePathBuf` | 绝对路径对象 | 保证 host-native path 为绝对的 Codex wrapper |
| `LegacyAppPathString` | 旧应用路径字符串 | App-server 兼容 native/foreign path spelling 的边界类型 |
| `PathUri` | 路径 URI | 跨 host 保存和操作 `file:` path 的不可变类型 |
| POSIX path | POSIX 路径 | Linux/macOS 常见 `/repo/file` 路径语法 |
| Drive path | 驱动器路径 | Windows `C:\repo\file` 路径语法 |
| UNC path | 网络共享路径 | Windows `\\server\share\file` 路径语法 |
| Foreign path | 外来平台路径 | 属于另一 OS/executor、不能按当前 host 规则解释的路径 |
| Lexical operation | 词法路径操作 | 不访问磁盘，只按 segment 进行 join/parent 等运算 |
| Canonicalization | 规范解析 | 访问真实文件系统解析 symlink 和实际存在路径 |
| Fail closed | 失败即关闭 | 权限路径无法可靠转换时拒绝继续执行 |
| Shell | 命令解释器 | Bash、zsh、PowerShell、cmd 等目标脚本环境 |
| Argv | 参数向量 | 不经额外 Shell 拆词、直接传给程序的参数列表 |
| Script string | 脚本字符串 | 还要由目标 Shell 再次解析的命令文本 |
| PTY | 伪终端 | Unix 为交互式程序模拟 terminal 的机制 |
| ConPTY | Windows 伪控制台 | Windows 的伪终端 API |
| Process group | 进程组 | Unix 中统一接收 signal 的相关进程集合 |
| Process tree | 进程树 | 父进程和所有 descendants 的层级 |
| Wine | Windows 兼容层 | 在 Linux host 运行 Windows exec-server binary 的环境 |
| `WINEPREFIX` | Wine 前缀 | 隔离的一套 Wine drive、registry 和 runtime 状态 |
| Auto-env | 自动环境 | 让测试按配置选择 local、Docker 或 Wine executor 的 harness |
| Target-native fixture | 目标原生夹具 | 通过 executor filesystem 在目标环境创建的测试资源 |
| Skip macro | 跳过宏 | 按 host、target、remote 或 Wine 条件跳过测试的宏 |
| Test matrix | 测试矩阵 | Host、target、placement 和 architecture 的验证组合 |
| Materialize | 具体化 | 把 portable intent 转成目标平台路径、wrapper 和参数 |

## 30. 协议演进、Schema 与向后兼容测试

| 英文 | 常见直译 | 在 Codex 源码阅读中的含义 |
|---|---|---|
| Wire format | 线上格式 | 跨进程实际传输的 JSON 名字、tag 和数据形状 |
| Behavioral contract | 行为契约 | Schema 之外的默认值、顺序、错误、幂等和生命周期约定 |
| Backward compatibility | 向后兼容 | 新 reader/server 继续支持旧数据或旧 client |
| Forward compatibility | 向前兼容 | 旧 reader/client 能安全容忍未来的可扩展表示 |
| Rollback compatibility | 回滚兼容 | 新 writer 写入后，旧版本仍有能力接管 |
| Breaking change | 破坏性变更 | 让既有客户端、脚本、配置或历史记录无法正确工作的修改 |
| Omitted | 省略 | JSON 中没有该 key，不一定等于 `null` 或空集合 |
| Nullable | 可为空 | 字段存在时允许以 `null` 作为值 |
| Tagged union | 带标签联合 | 通过 `type` 等字段决定 enum variant/payload 的协议形状 |
| Capability negotiation | 能力协商 | 初始化时确认客户端是否理解某类实验 API |
| Schema fixture | 模式固定样本 | 提交并审核的生成输出，用来暴露 API 漂移 |
| Opaque cursor | 不透明游标 | 客户端只原样传回、不自行解释的分页位置 |
| Canonical form | 规范形式 | legacy 输入经 alias/migration 后统一采用的内部表示 |
| Deprecation | 弃用 | 仍兼容但已提示调用者迁移、未来准备移除的接口 |
| Expand–Migrate–Contract | 扩展—迁移—收缩 | 先同时读取新旧格式，再迁移消费者，最后删除旧路径 |
| Round-trip test | 往返测试 | 当前 writer 写出再由当前 reader 读回，不能单独证明跨版本兼容 |
| Legacy fixture | 旧版固定样本 | 不随当前 serializer 改变、用于验证历史格式的测试输入 |

## 31. 大规模重构、模块边界与渐进式迁移

| 英文 | 常见直译 | 在 Codex 源码阅读中的含义 |
|---|---|---|
| Cohesion | 内聚 | 一个模块是否围绕同一职责和变化原因 |
| Coupling | 耦合 | 模块之间必须了解彼此实现和状态的程度 |
| Fan-in / Fan-out | 扇入/扇出 | 调用者数量，以及一个模块依赖的模块数量 |
| Owner | 所有者 | 对状态和不变量负主要责任的 module/crate |
| Facade | 外观入口 | 为复杂子系统提供的窄公共入口 |
| Adapter | 适配器 | 把旧接口或数据转换为新接口 |
| Shim | 兼容薄层 | 迁移期间暂时保留、计划删除的桥接代码 |
| Strangler pattern | 绞杀者模式 | 让新实现逐步接管旧实现的调用流量 |
| Branch by abstraction | 通过抽象分支 | 在主干中并存、验证和切换新旧实现 |
| Characterization test | 特征测试 | 重构前固定当前可观察行为的测试 |
| Architecture test | 架构测试 | 自动阻止代码绕过依赖或策略边界的测试 |
| Compatibility field | 兼容字段 | 调用方分批迁移期间保留的旧访问路径 |
| Migration map | 迁移地图 | 调用簇、顺序、桥接、剩余工作和删除条件 |
| Exit criteria | 退出条件 | 可以安全删除旧路径时必须满足的证据 |
| Shadow execution | 影子执行 | 新实现旁路计算、比较结果但不产生真实副作用 |
| Mechanical move | 机械移动 | 原则上不改变行为的代码搬迁阶段 |
| API surface | API 表面 | 外部可见并形成兼容责任的类型和函数集合 |

## 32. 安全删除、废弃代码与技术债治理

| 英文 | 常见直译 | 在 Codex 源码阅读中的含义 |
|---|---|---|
| Safe deletion | 安全删除 | 处理消费者、数据、回滚和制品后再物理移除 |
| Dead / dormant code | 死/休眠代码 | 永远不可达的代码，与仅在特定条件可达的代码 |
| Deprecated | 已弃用 | 仍兼容但已要求迁移的接口或输入 |
| No-op compatibility | 无操作兼容 | 旧输入可加载，但不再改变当前规范行为 |
| Stop producing | 停止产生 | writer/server 不再写旧字段或发送旧事件 |
| Stop accepting | 停止接受 | reader/server 不再兼容旧输入 |
| Tombstone | 墓碑 | 保留旧名称记录，防止它被赋予新语义 |
| Fail closed | 失败时关闭 | 无法证明安全时拒绝，而不是放宽权限继续 |
| Stale artifact | 过期制品 | 已不再生成但仍残留、可能被打包的旧输出 |
| Usage telemetry | 使用遥测 | 观察旧 alias、fallback、reader 真实命中情况 |
| Long-tail client | 长尾客户端 | 升级较慢、离线或低频执行的旧客户端 |
| Technical debt | 技术债 | 为交付/兼容承担、持续增加未来成本或风险的设计负担 |
| Principal / Interest | 本金/利息 | 彻底修复成本，以及债务存在期间的持续额外成本 |
| Debt ratchet | 债务棘轮 | 允许历史债务暂存，但禁止新改动继续恶化 |
| Resurrection | 复活 | 已退休入口、配置或模式被新代码重新引入 |

## 33. 幂等性、重复消息与分布式状态一致性

| 英文 | 常见直译 | 在 Codex 源码阅读中的含义 |
|---|---|---|
| Idempotency | 幂等性 | 同一业务意图重复处理，最终可观察效果仍等价于一次 |
| Ambiguous outcome | 结果不确定 | 响应丢失后无法判断副作用是否已经成功 |
| At-most/at-least-once | 至多/至少一次 | 分别允许丢失，或允许重复的投递保证 |
| Correlation ID | 关联 ID | 把响应配回请求，不自动等于幂等键 |
| Idempotency key | 幂等键 | 重试同一意图时保持稳定、用于复用既有结果的标识 |
| Terminal outcome | 终态结果 | Completed/Failed/Cancelled 中唯一生效的最终结论 |
| Coalescing | 合并 | 将多个状态失效信号折叠为一次重新计算 |
| Generation fencing | 世代栅栏 | 阻止旧 task 在新 owner 建立后继续修改或清理状态 |
| Snapshot/live gap | 快照/实时缺口 | 读完历史到订阅建立之间可能丢失的更新 |
| Optimistic concurrency | 乐观并发 | 用 expected version 在提交时发现并发修改 |
| Lost update | 丢失更新 | 后写入者基于旧快照覆盖先写入者的变化 |
| Ordinal | 序号 | 持久日志中的顺序位置，不自动是业务去重键 |
| Authoritative log | 权威日志 | 冲突时作为事实、可用于重建 projection 的耐久记录 |
| Projection | 投影 | 从事实日志派生的查询索引或 UI 状态 |
| Reconciliation | 对账 | 比较权威状态和投影并修复差异 |
| Transactional outbox | 事务发件箱 | 同事务写业务状态与待发消息，再由可重试 worker 投递 |
| Commit point | 提交点 | 从可安全重做跨到必须识别已有结果的边界 |

## 34. 资源生命周期、RAII 与泄漏防护

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Resource lifecycle | 资源生命周期 | 资源从创建、活跃、关闭准入、排空到最终释放的完整状态变化 |
| RAII | Resource Acquisition Is Initialization | 把资源责任绑定到 Rust 值的作用域，由 Drop 覆盖提前返回和取消路径 |
| Drop | 丢弃/析构钩子 | 值离开作用域时同步执行的兜底逻辑，不能直接等待异步清理 |
| Explicit shutdown | 显式关闭 | 调用者主动执行并等待一套可报告结果的异步 teardown 协议 |
| Cooperative cancellation | 协作式取消 | 发送 token 或消息，请任务在安全点自行停止 |
| Abort-on-drop | 随 owner 丢弃而强制终止 | 防止辅助 task 比拥有它的对象活得更久 |
| Guard | 守卫对象 | 在 Drop 时执行解锁、减计数、删除登记或失败回滚的 RAII 值 |
| Armed / disarm | 已布防/解除责任 | guard 是否仍负责回滚；disarm 通常意味着责任已转移给新 owner |
| Ownership transfer | 所有权交接 | cleanup 责任从创建方转到接收方，常需回执明确交接点 |
| Acknowledgment | 确认回执 | 证明另一方已接收或已完成某个生命周期步骤的信号 |
| Close / drain / join | 关门/排空/等待结束 | 停止新工作、完成已有工作、确认执行实体结束 |
| Reserved cleanup capacity | 清理保留容量 | 普通请求饱和时仍供 terminate、close 使用的并发容量 |
| Orphan process | 孤儿进程 | owner 已消失但 OS child 或其后代仍继续运行 |
| Registration leak | 登记泄漏 | route、session、active request ID 未从本地或远端删除 |
| Task/channel leak | 任务/通道泄漏 | task 不退出，或 sender clone 残留导致 receiver 永不关闭 |
| Weak | 弱引用 | 不增加 Arc 强引用计数，避免 worker 延长 owner 生命周期 |
| Arc cycle | 强引用环 | 对象和 task 互相强持有，导致引用计数不能归零 |
| Best effort | 尽力而为 | 尝试清理但不承诺完成，常见于无法 await 的 Drop 路径 |
| Teardown | 拆除/收尾 | 按依赖顺序停止准入、取消、排空、终止、等待和释放资源 |

## 35. 缓存设计、失效策略与一致性

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Cache | 缓存 | 为降低读取或计算成本而保存的权威状态副本 |
| Cache key | 缓存键 | 精确描述 value 所依赖身份、配置和版本的查找键 |
| Cache entry | 缓存项 | key 对应的 value 以及时间、版本、ETag 等验证元数据 |
| Hit / miss | 命中/未命中 | 找到可用 entry，或因不存在、过期、版本错而不能使用 |
| False hit / false miss | 错误命中/错误未命中 | 错用不属于当前输入的值，或错失本来安全的复用 |
| TTL | Time To Live | entry 可被视为 fresh 的最长时间 |
| Fresh / stale | 新鲜/陈旧 | 仍可直接使用，或已需重新验证的缓存状态 |
| Stale-while-revalidate | 先用旧值再后台验证 | 为降低延迟暂时服务 stale value，同时刷新下一份结果 |
| ETag | Entity Tag | 服务端用于确认资源内容版本是否改变的验证标识 |
| Invalidation | 失效 | 因时间、事件、身份或版本变化让缓存不再可用 |
| Generation fencing | 世代栅栏 | 防止失效前启动的慢加载在失效后重新写入旧值 |
| Singleflight | 同键单次在途加载 | 并发 miss 共享一个 leader 的加载结果 |
| Cache stampede | 缓存击穿/惊群 | 大量 miss 同时访问权威源造成流量尖峰 |
| Negative caching | 负结果缓存 | 短暂复用不存在或失败结果，避免持续重复查询 |
| Eviction | 淘汰 | 达到数量、字节或时间边界后移除 entry |
| Cache-aside | 旁路缓存 | 业务逻辑先查缓存，miss 后读权威源并回填 |
| Read-through | 穿透式读取 | 缓存接口内部负责加载权威数据 |
| Write-through / write-back | 同步写穿/延迟回写 | 同步更新权威源，或先缓存后异步落地 |
| Warming / prefetch | 预热/预取 | 用户真正请求前主动加载可能需要的数据 |
| Jitter | 随机抖动 | 随机化过期或刷新时刻，避免大量实例同时请求 |
| Prompt Caching | Prompt 缓存 | OpenAI API 对可复用输入前缀计算的缓存，不是本地业务 value 缓存 |

## 36. 数据库事务、隔离级别与崩溃一致性

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Transaction | 事务 | 一组共同 commit 或 rollback 的数据库操作 |
| ACID | 原子、一致、隔离、持久 | 描述事务在失败、并发和持久化方面的四类保证 |
| Commit / rollback | 提交/回滚 | 发布事务变化，或放弃尚未提交的变化 |
| WAL | Write-Ahead Log | SQLite 先追加变更、后 checkpoint 回主库的预写日志模式 |
| WAL sidecar | WAL 伴随文件 | 与主库配套的 `-wal` 和 `-shm` 文件 |
| Checkpoint | 检查点 | 合并 WAL 页；也可泛指记录业务任务已处理进度的位置 |
| Synchronous mode | 同步落盘模式 | SQLite 在性能和断电耐久性间选择的同步等级 |
| Busy timeout | 锁等待超时 | writer 暂时繁忙时允许连接等待的最长时间 |
| Deferred transaction | 延迟取得写锁的事务 | 开始时不立即占 writer，首次写入时才竞争 |
| `BEGIN IMMEDIATE` | 立即写事务 | 事务开始时就取得 writer slot，保护后续 read-modify-write |
| Isolation anomaly | 隔离异常 | dirty read、lost update、write skew 等并发交错问题 |
| Constraint | 数据库约束 | PRIMARY KEY、UNIQUE、CHECK、FOREIGN KEY 等存储层不变量 |
| UPSERT | Insert or update | 插入冲突时忽略或按条件更新已有行 |
| Affected rows | 受影响行数 | 判断条件更新/CAS 是否真正改变目标行的结果 |
| Lease | 租约 | worker 所有权的有限有效期，过期后允许接管 |
| Ownership token | 所有权令牌 | 每次 claim 生成的新标识，阻止旧 worker 迟到提交 |
| Migration | 数据库迁移 | 按 version 和 checksum 演进 schema 与已有数据 |
| Corruption | 数据库损坏 | 与 lock、permission、disk full 分流处理的文件/页结构错误 |
| Integrity check | 完整性检查 | SQLite 对数据库结构执行的内建诊断 |
| Crash consistency | 崩溃一致性 | 进程在任意中间点终止后，重启仍能识别或修复状态 |
| Projection lag | 投影滞后 | 查询数据库落后于权威 Rollout 的 offset/ordinal 距离 |

## 37. 认证凭据、密钥与敏感数据生命周期

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Authentication | 身份认证 | 证明调用者是谁，而不是自动授予所有权限 |
| Authorization | 权限授权 | 已认证主体被允许访问哪些资源和动作 |
| Credential | 凭据 | API key、token、private key 等能代表主体取得权限的材料 |
| Secret | 秘密数据 | 泄漏后可能造成访问、冒充或隐私损害的数据 |
| Access token | 访问令牌 | 通常较短期、放进目标 API 请求的 bearer token |
| Refresh token | 刷新令牌 | 向认证 authority 换取新 access token 的高价值凭据 |
| ID token | 身份令牌 | 携带账号、用户等身份 claims，不应与 access token 混用 |
| Bearer token | 持有者令牌 | 持有字符串即可代表调用方使用的 token |
| `AuthDotJson` | Codex 认证存储结构 | 聚合 API key、OAuth tokens、agent identity、PAT 等敏感字段 |
| Store mode | 存储模式 | File、Keyring、Auto 或 Ephemeral 的凭据保存策略 |
| Keyring | 系统凭据库 | 借助操作系统凭据设施保存认证数据的后端 |
| Ephemeral storage | 临时内存存储 | 不由该后端持久化、进程结束后消失的认证 map |
| File mode `0600` | owner 可读写权限 | 限制 Unix 文件访问，但不等于内容加密 |
| Encryption at rest | 静态加密 | 存储字节泄漏后仍需额外解密能力才能读取内容 |
| Secret injection | 秘密注入 | 通过 env、stdin、secret manager 等在运行时提供值 |
| Data minimization | 数据最小化 | 诊断只记录所需的来源或存在性，不记录 secret value |
| Redaction | 脱敏 | 在日志、错误和报告中移除或替换敏感部分 |
| Token rotation | 令牌轮换 | 新 token 生效并替换旧 token 的过程 |
| Proactive refresh | 主动刷新 | 在 token 临近过期时提前换新 |
| Unauthorized recovery | 未授权恢复 | 收到 401 后重载或刷新认证并有限重试 |
| Refresh singleflight | 刷新合并 | 并发失效只允许一个刷新 leader 调用 authority |
| Identity fencing | 身份栅栏 | 防止旧账号的迟到刷新覆盖新账号凭据 |
| Revocation | 服务端撤销 | 让认证 authority 不再接受某个 token |
| Local deletion | 本地删除 | 清除 file、keyring、fallback 和内存缓存中的凭据 |
| `bearer_token_env_var` | bearer token 环境变量名 | MCP 配置保存变量名，运行时再解析 secret value |
| Provider command | 凭据提供命令 | 通过受信外部程序 stdout 取得并按策略刷新 token |
| Secret canary | 秘密哨兵值 | 用于断言日志、报告和 snapshot 中绝不出现的测试值 |

## 38. 时间、时钟、超时与分布式时间语义

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Wall clock | 墙上时间 | 可映射到现实日期、时区和 Unix timestamp 的时间 |
| Monotonic clock | 单调时钟 | 不因系统校时倒退、适合测量当前进程内间隔的时钟 |
| `SystemTime` | 系统时间 | Rust 的 wall-clock 类型，可与 Unix epoch 转换 |
| `DateTime<Utc>` | UTC 日期时间 | 适合协议、持久化和跨进程比较的明确时区时间 |
| `Instant` | 单调时刻 | 用于 elapsed、deadline、进程内 TTL，不能跨进程持久化 |
| `Duration` | 持续长度 | 不含起点、日期或时区的一段非负时间 |
| Timeout | 相对超时 | 从当前阶段开始最多等待多久 |
| Deadline | 截止点 | 整项操作最迟结束的单调时刻，可向下游传播剩余预算 |
| Remaining budget | 剩余预算 | deadline 与当前 Instant 的非负差值 |
| Idle timeout | 空闲超时 | 连续没有有效活动达到上限后才超时 |
| Connect timeout | 连接超时 | 限制 DNS/TCP/TLS/WebSocket 等连接建立阶段 |
| Drain timeout | 排空超时 | 取消后等待终态、pipe 或剩余输出的独立上限 |
| Grace period | 宽限期 | 到期前允许协作式完成或清理的额外窗口 |
| Clock skew | 时钟偏差 | 不同机器或时间 authority 对“现在”的差异 |
| Clock rollback | 时钟回拨 | wall clock 变小，导致负 age、延长 TTL 或 lease 异常 |
| Unix epoch | Unix 纪元 | timestamp 的共同起点，仍需明确 seconds/milliseconds 单位 |
| TTL | Time To Live | cache 或 session 从起点起可复用的最长时间 |
| `expires_at` | 到期时刻 | 可持久化的绝对 expiry，需说明单位与 authority |
| Refresh skew | 刷新提前量 | 为在途延迟预留、在真正到期前刷新 token 的窗口 |
| Lease | 租约 | 依赖时间到期后允许其他 worker 接管的有限所有权 |
| Retry-After | 稍后重试 | 服务端以 delta seconds 或 HTTP date 给出的重试建议 |
| Exponential backoff | 指数退避 | 随 attempt 增长重试等待时间，降低故障流量 |
| Jitter | 随机抖动 | 打散大量客户端的同步重试时刻 |
| Cancellation-safe | 可安全取消 | future 被 drop 时不会留下错误的半完成状态 |
| Yield | 暂时交还控制 | 返回阶段性结果，但后台 cell 或任务仍可能继续 |
| TTFT / TTFM | 首 token/首消息耗时 | 使用 monotonic time 计算的 turn 用户体验指标 |
| `TimeProvider` | 时间提供者 | 为 thread 统一提供 current time 和 sleep 语义的边界 |
| Paused time | 暂停的测试时间 | Tokio 测试中由 `advance` 确定性推进的虚拟 timer 时钟 |

## 39. 文件系统原子性、路径、临时文件与跨平台持久化

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| `Path` / `PathBuf` | 借用/拥有的路径 | 保留操作系统路径语义，不假设文件名一定是 UTF-8 |
| `OsStr` / `OsString` | OS 字符串 | 可表达平台原生文件名的字符串类型 |
| Absolute path | 绝对路径 | 不依赖当前目录即可解释的路径，不自动代表目标存在或安全 |
| Lexical normalization | 词法规范化 | 不访问磁盘地处理 base、`.` 和 `..` |
| Canonicalization | 真实路径规范化 | 访问文件系统并解析 symlink，通常要求目标存在 |
| Logical / physical path | 逻辑/物理路径 | 用户使用的入口名称与解析链接后的真实目标 |
| Symlink | 符号链接 | 保存另一个 target 路径的特殊目录项 |
| `symlink_metadata` | 链接自身 metadata | 不跟随最终 symlink 地判断目录项类型 |
| Symlink cycle | 链接环 | 链接链再次到达已访问路径，无法解析最终目标 |
| Atomic publish | 原子发布 | 读者只观察到旧版或完整新版，不观察发布中间态 |
| Staged file | 暂存文件 | 在最终发布前完整生成和校验的新内容 |
| `NamedTempFile` | 有名字的临时文件 | 通常在目标父目录创建、由 RAII 清理的 staging 文件 |
| `persist` | 发布临时文件 | 将 NamedTempFile 移动/替换到最终目标名 |
| `rename` | 改名/移动 | 修改目录项，同一 filesystem 内常用于原子发布 |
| Cross-device rename | 跨设备改名 | 源目标位于不同 volume 时可能失败的 rename |
| Short write | 短写 | 一次 `write` 只接受部分 buffer |
| `write_all` | 写完全部 | 循环写入整个 buffer 或返回错误 |
| `flush` | 刷新缓冲 | 排空用户态 buffer，不自动等于断电耐久 |
| `sync_all` / fsync | 同步落盘 | 请求 OS 同步文件内容和 metadata |
| Parent directory sync | 父目录同步 | 同步 create/rename/remove 改变的目录项 |
| Atomicity | 原子性 | 操作表现为未发生或完整发生，不暴露一半 |
| Durability | 持久性 | 成功后即使崩溃恢复仍应保留结果 |
| Lost update | 丢失更新 | 后 writer 基于旧快照覆盖先 writer 的修改 |
| Advisory lock | 建议锁 | 只有遵守同一协议的进程才会受约束的文件锁 |
| Shared / exclusive lock | 共享/独占锁 | 多 reader 可共享，writer 需要独占临界区 |
| `create_new(true)` | 仅不存在时创建 | 原子创建名字，已存在则返回 `AlreadyExists` |
| TOCTOU | Time Of Check To Time Of Use | 验证和实际使用之间对象被替换的竞态 |
| Journal / marker | 恢复日志/标记 | 让重启逻辑识别跨步骤操作的未完成状态 |
| `.pending` | 待完成 | Rollout migration 仍需恢复或清理的 durable marker |
| Optimistic concurrency | 乐观并发 | 发布前检查源 version/size/mtime 是否仍匹配 |
| `spawn_blocking` | 阻塞线程池执行 | 避免文件锁、同步 I/O、压缩和 fsync 阻塞 Tokio worker |
| JSONL | 每行一个 JSON | 支持追加、逐条读取和按完整行裁剪的文件格式 |
| File mode `0o600` | owner 读写 | Unix `rw-------` 权限，不等于静态加密 |
| UNC path | Windows 网络路径 | `\\server\share\...` 形式的共享路径 |
| Verbatim path | Windows 原样路径 | `\\?\...` device namespace 路径 |
| WSL drive mount | WSL 盘符挂载 | `/mnt/c/...` 等映射到 Windows filesystem 的路径 |

## 40. 目录遍历、文件搜索、忽略规则与变化检测

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Directory walk | 目录遍历 | 从一个或多个 root 递归枚举路径候选 |
| Search root | 搜索根 | 相对结果的基准目录，multi-root 结果需保留 root 身份 |
| `WalkBuilder` | 遍历构建器 | 配置 threads、ignore、hidden、symlink 和 override 的 walker |
| Candidate / index | 候选/索引 | walker 发现并注入 Nucleo、供多次 query 复用的路径 |
| Fuzzy match | 模糊匹配 | query 字符无需连续但保持顺序的路径相关度匹配 |
| Score / top-N | 分数/前 N 项 | 按相关度排序并只展示 limit 个候选 |
| Match indices | 命中下标 | query 字符在路径中的位置，供 UI 高亮 |
| Snapshot | 快照 | 一次 matcher tick 的 query、matches、count 与完成状态 |
| `walk_complete` | 遍历完成 | 当前搜索 session 是否已完成所有 root 的目录扫描 |
| Streaming update | 流式更新 | 全量 walk 前先返回部分结果，后续 snapshot 校正 top-N |
| `.gitignore` | Git 忽略文件 | 在 repository scope 内按层级规则过滤候选路径 |
| Negation rule | 否定规则 | 用 `!` 将先前忽略的具体路径重新纳入 |
| `require_git` | 要求 Git 上下文 | 避免非仓库祖先 `.gitignore` 意外吞掉全部文件 |
| Override glob | 覆盖 glob | 调用者为本次搜索额外提供的 exclude policy |
| Hidden entry | 隐藏条目 | 点文件等默认可能被 walker 过滤的路径 |
| Follow links | 跟随链接 | 继续遍历 symlink 指向的目录内容 |
| `Nucleo` / `Injector` | 匹配引擎/注入器 | 增量接收路径并复用 index 处理高频 query |
| Query update | 查询更新 | 不重走目录，只让 matcher 重算已有候选的操作 |
| Session generation | 搜索会话世代 | 防止旧 query/session 的迟到 snapshot 覆盖新 UI |
| Cancellation flag | 取消标志 | walker/matcher 周期读取并提前退出的 atomic 状态 |
| File watcher | 文件观察器 | 通过 OS backend 提供粗粒度 changed-path 提示 |
| Subscriber | 订阅者 | 拥有独立 watch registration 和 receiver 的逻辑消费者 |
| Watch registration | 观察注册 guard | Drop 时自动取消底层路径引用的 RAII 对象 |
| Recursive watch | 递归监听 | 接收 watch root 所有后代路径的变化提示 |
| Reference count | 引用计数 | 多 subscriber 共享同一底层 OS watch 的数量 |
| Requested/matched/actual | 请求/匹配/实际路径 | 客户端逻辑路径、canonical event namespace、OS 当前监听目标 |
| Fallback ancestor | 回退祖先 | requested 不存在时临时监听的最近已存在目录 |
| Coalescing | 合并 | changed paths 排序、去重并批量交付 |
| Throttle | 节流 | 限制两次事件 batch 之间的最小时间 |
| Debounce | 防抖 | 从首事件开始等待固定窗口并合并同一阵变化 |
| Mutating event | 修改事件 | Create、Modify、Remove，不含普通 Access/Open |
| Cache invalidation | 缓存失效 | watcher 提示后清除派生状态并从权威源重载 |
| Git fsmonitor | Git 文件监控加速 | 用变化提示减少 status/diff 的全量文件扫描 |
| Built-in daemon | 内建守护进程 | Git 自带且经 capability probe 确认的安全 fsmonitor 模式 |
| Helper executable | 辅助可执行程序 | repository config 可指定、内部 Git 命令不得盲目执行的程序 |

## 41. 文件读取、文本编码、二进制识别与有界内容加载

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Byte / `u8` | 字节 | 文件与进程输出的原始数据单位，不自带文字含义 |
| `Vec<u8>` | byte vector | 可无损保存任意文本或二进制内容的拥有型缓冲区 |
| `String` / `&str` | UTF-8 字符串 | Rust 中必须始终合法 UTF-8 的拥有/借用文本 |
| Encoding / code page | 字符编码/代码页 | 定义 byte sequence 怎样映射为 Unicode 字符 |
| Strict / lossy decode | 严格/有损解码 | 非法序列时报错，或用 `�` 替代后继续展示 |
| Encoding detection | 编码探测 | 用 heuristic 猜测非 UTF-8 bytes 的传统编码 |
| `read_to_end` | 读到末尾 | 将 reader 剩余 bytes 追加到 vector，不自带上限 |
| `read_to_string` | 读到字符串 | 整读并验证 UTF-8，适合受控文本而非任意文件 |
| `BufReader` | 缓冲读取器 | 以较大内部 buffer 支持高效的小块/逐行读取 |
| Metadata / `stat` | 文件元数据 | size、mtime 等某一时刻的属性，只适合预检 |
| Bounded read | 有界读取 | 对载入内存或交给下游的内容设置硬上限 |
| `take(MAX + 1)` | 上限加哨兵读取 | 同时限制内存增长并用额外一 byte 判断超限 |
| Chunk / block | 数据块 | 流式或随机读取的一段有界 bytes |
| File handle | 文件句柄 | open 后持有的读取资源，需限制数量并最终 close |
| Offset / cursor | 偏移/游标 | 显式 byte 位置与顺序读取的隐式下一位置 |
| Base64 | 二进制 ASCII 表示 | 在 JSON 中无损携带 bytes，不是加密或文本编码 |
| JSONL record | JSON 行记录 | 以 newline 分隔、可逐条处理的 JSON 对象 |
| Record resynchronization | 记录重新同步 | 跳过超大/坏行直至下一换行，避免污染后续解析 |
| Tail read | 尾读 | seek 到接近 EOF 的位置，只加载最近内容 |
| Character boundary | 字符边界 | UTF-8 string 可安全切片而不切断多 byte 字符的位置 |
| Byte/token budget | 字节/token 预算 | 分别保护内存/协议容量与模型上下文容量 |
| Head-tail retention | 头尾保留 | 保留输出开头和结尾，丢弃中间以固定内存 |
| Omission marker | 省略标记 | 明确告诉用户或模型内容因预算被截断 |
| Binary heuristic | 二进制启发式识别 | NUL、扩展名、MIME、magic 等非绝对判断线索 |
| Streaming | 流式处理 | 读取一块即消费一块，避免完整内容同时驻留内存 |
| zstd | Zstandard 压缩格式 | Rollout migration 使用的流式压缩格式 |
| Decompression bomb | 解压炸弹 | 压缩输入很小、展开输出异常巨大的资源风险 |
| `io::sink` | 数据黑洞 | 接收并丢弃解压 bytes，用于验证流可完整解码 |
| Snapshot semantics | 快照语义 | 文件变化期间一次读取究竟代表哪个时刻的内容 |

## 42. 进程标准输入输出、Pipe、PTY、实时输出与背压

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| stdio | 标准输入输出 | stdin、stdout、stderr 三条约定进程通道 |
| stdin/stdout/stderr | 标准输入/输出/错误 | Unix fd 0/1/2 对应的 byte streams |
| File descriptor | 文件描述符 | 进程引用 pipe、terminal、file 等打开资源的整数句柄 |
| Pipe | 管道 | 分别连接 stdio、适合非交互命令的单向 OS byte stream |
| PTY / TTY | 伪终端/终端 | 让进程看到 terminal 语义并支持交互、信号和 resize 的设备 |
| Master / slave | 主端/从端 | Codex 持有 PTY master，子进程三条 stdio 接 slave |
| ConPTY | Windows pseudo-console | Windows 平台的 PTY 实现设施 |
| `isatty()` | 是否连接终端 | 程序决定颜色、缓冲和交互行为的常见检查 |
| Line discipline / echo | 终端行规程/回显 | PTY 对行输入、控制字符和输入显示的处理 |
| Chunk | 数据块 | 一次 read/channel 运输的 bytes，不保证等于一行或一次 write |
| EOF | 流末尾 | 某条 reader 已无后续 bytes，不等于所有进程状态都结束 |
| Backpressure | 背压 | 下游变慢使 queue、reader、最终 child write 逐层减速 |
| `mpsc` | 多生产者单消费者 | writer 和 output forwarding 使用的有界工作 channel |
| `broadcast` / Lagged | 广播/接收落后 | 多 subscriber 各读输出，慢者可能丢失最老消息 |
| `oneshot` / `watch` / `Notify` | 单次/最新值/唤醒 | 分别传 exit code、状态和“请重新检查”信号 |
| Sequence / replay | 顺序号/重放 | 远程输出发现通知缺口后按 seq 从保留窗口回读 |
| Retention / eviction | 保留/淘汰 | 有界保存输出并在超限后移除最老 chunk |
| Long polling | 长轮询 | `process/read` 在无变化时等待有限时间 |
| `write_id` | 写入幂等标识 | 远程 stdin 重试时防止同一 bytes 投递两次 |
| Permit / `reserve()` | 队列许可/预留 | 等到 mpsc 有容量并占住一个位置 |
| Interrupt / terminate | 中断/终止 | 类 Ctrl-C 的协作请求与更强制的停止动作 |
| Process group / Job Object | 进程组/作业对象 | Unix/Windows 管理根进程及后代的单位 |
| Exited / Closed | 已退出/已关闭 | 根进程有结果，与所有输出 stream 已 EOF 的不同终态 |
| Drain / grace | 排空/宽限 | 退出后继续收集在途输出，并设置有限兜底时间 |
| Delta / transcript | 增量/执行记录 | 实时输出块与最终有界聚合结果 |
| Poll / yield | 轮询/暂时返回 | 在观察窗口内收集输出，后台进程仍可继续 |
| Interaction lock | 交互锁 | 同一进程的 write、poll、终态发布和清理串行边界 |

## 43. Shell、argv、引号转义、环境变量与工作目录

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Shell | 命令解释器 | 解析脚本文本并执行 expansion、pipe、redirect 和命令组合 |
| Script / command string | 脚本/命令字符串 | 尚未被 shell 解释的一整段文本 |
| argv / argument vector | 参数向量 | 启动程序时传入的有边界字符串数组 |
| Program / executable | 程序/可执行文件 | argv 对应的实际进程映像 |
| `-c` / `-lc` | 执行脚本/以 login 语义执行 | POSIX shell 接收 script 的常见参数形状 |
| `-Command` / `/c` | 执行命令 | PowerShell/cmd.exe 接收 script 的入口参数 |
| Quoting / escaping | 引用/转义 | 控制字符在哪一解析层作为语法还是字面数据 |
| Expansion | 展开 | shell 对变量、glob、command substitution 等做替换 |
| Glob | 通配展开 | 把文件 pattern 展开成多个路径参数 |
| Redirection | 重定向 | 在进程启动前把 stdio 接到文件或其他 fd |
| Control operator | 控制运算符 | `|`、`&&`、`||`、`;` 等 shell 组合符号 |
| Command substitution | 命令替换 | 执行内层命令并把 stdout 放入外层文本 |
| cwd / workdir | 当前/指定工作目录 | 相对路径解析起点与工具选择本次起点的参数 |
| Environment / env | 环境变量集合 | 启动时传给子进程的 key/value map |
| `env_clear()` | 清空隐式继承 | 先移除父环境，再加入策略明确生成的变量 |
| Inherit / exclude / set / include only | 继承/排除/设置/仅保留 | ShellEnvironmentPolicy 构造 env map 的顺序化规则 |
| PATH / PATHEXT | 程序路径/Windows 后缀 | 影响非绝对程序名最终解析到哪个 executable |
| Login shell / profile | 登录式 shell/配置档 | 可能加载启动文件并改变 PATH、alias、function 与 env |
| Shell snapshot | shell 快照 | 缓存部分本地 POSIX 初始化状态供后续命令恢复 |
| Source / dot command | 在当前 shell 加载 | 用 `. file` 将 snapshot 内容读入当前 shell |
| Direct mode | 普通直接后端 | 与 zsh-fork 相对，不表示一定绕过 shell |
| ZshFork | zsh fork 后端 | 尝试通过特定 zsh 机制启动本地命令 |
| `shlex_join` | shell 风格展示拼接 | 把 argv 格式化为可读文本，不代替原始 argv |
| Command injection | 命令注入 | 不可信数据突破数据边界成为 shell 代码 |
| Fail closed | 失败时保守拒绝 | 无法证明复杂命令安全时不作乐观猜测 |
| Path convention / `PathUri` | 路径约定/路径 URI | 区分 POSIX、Windows 与远程环境的路径命名空间 |

## 44. 退出码、信号、超时、取消与进程树回收

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Exit status / exit code | 终止状态/退出码 | OS 原始终止原因与交给上层的整数结果 |
| Signal | 信号 | Unix 内核发给进程的异步通知 |
| SIGINT / SIGTERM / SIGKILL | 中断/终止请求/强制杀死 | 从协作中断到不可忽略强杀的不同级别 |
| Grace period | 宽限期 | 协作退出失败后升级强制终止前的有限等待 |
| Timeout / deadline | 超时/截止点 | 外部限制操作完成时间的机制 |
| Yield | 暂时返回 | 结束一次输出观察，不结束后台进程 |
| CancellationToken | 取消令牌 | Rust 异步任务之间传播取消意图的协作通知 |
| PID / PGID | 进程/进程组标识 | 标识单个 Unix 进程或可整体发送 signal 的组 |
| Process tree / descendant | 进程树/后代 | root、child、grandchild 构成的启动关系 |
| `setsid` / `setpgid` | 建会话/设进程组 | 建立独立 job-control 与终止边界 |
| Parent-death signal | 父进程死亡信号 | Linux 父进程消失时由内核通知 child |
| Job Object | Windows 作业对象 | 对一组 Windows 进程实施 containment 和整体终止 |
| Kill on job close | Job 关闭即杀 | 最后 Job handle 关闭时终止受控成员 |
| Breakaway | 脱离 Job | Windows 后代离开原 Job containment 的能力 |
| Suspended spawn | 挂起式启动 | 先建立 containment，再允许新进程运行 |
| Reap / wait | 回收/等待 | 获取终止状态并释放 OS 进程记录 |
| Drain | 排空 | 退出后继续读取剩余 stdout/stderr |
| Synthetic exit status | 合成终止状态 | 为 timeout/cancel 等外部终止路径建立内部结果 |
| Idempotent terminate | 幂等终止 | 重复停止请求仍安全地满足目标已停止条件 |
| Identity fencing | 身份栅栏 | 防止旧异步结果作用于复用 ID 的新实例 |
| Best effort | 尽力而为 | 尽可能完成，但承认平台、权限与竞态限制 |

## 45. 子进程错误分类、错误传播、重试与用户可读诊断

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Outcome | 执行结果 | 程序已运行并返回 output/exit code，不一定是执行系统错误 |
| Infrastructure error | 基础设施错误 | spawn、process manager、transport 或 RPC 未按契约完成 |
| Error category / variant | 错误类别/分支 | 供 Rust `match`、重试和协议稳定判断的结构化语义 |
| Diagnostic context | 诊断上下文 | command、cwd、method、ID、duration 等定位信息 |
| Error source chain | 错误原因链 | 外层操作说明连接到底层 `io`、JSON 或 transport 原因 |
| `Display` / `Debug` | 显示/调试格式 | 面向可读提示与面向内部结构的两种错误格式 |
| `UnifiedExecError` | 统一执行错误 | unified exec 的启动、会话、stdin、sandbox 和 path 错误 |
| `ToolError` | 工具错误 | 区分策略拒绝与结构化 Codex runtime 错误 |
| `SandboxErr` | sandbox 错误 | denial、timeout、signal 和平台 sandbox 设置失败 |
| `CodexErr` / details | Codex 错误/详情 | 语义 category、payload 与可选 retry delay 的包装 |
| `FunctionCallError` | 工具调用控制错误 | 决定错误返回模型还是作为 fatal 终止 |
| `RespondToModel` | 返回模型 | 把失败写成 tool output，让模型可修正请求 |
| `CodexErrorInfo` | 客户端错误信息 | 从复杂内部错误投影出的较小 wire category |
| Approval rejection | 审批拒绝 | 执行前未被授权，与 OS sandbox denial 不同 |
| Sandbox denial heuristic | sandbox 拒绝启发式 | 用 sandbox type、输出关键词、exit/signal 估计原因 |
| False positive / negative | 假阳性/假阴性 | 启发式误判为拒绝或漏掉真实拒绝 |
| Retryability | 可重试性 | 故障是否可能瞬时恢复，不自动表示操作可安全重放 |
| Escalated retry | 升级后重试 | 经 policy/approval 后改变 sandbox 策略做第二次 attempt |
| Idempotency key | 幂等键 | 让服务端识别重复业务操作的稳定标识 |
| Backoff / jitter | 退避/抖动 | 拉长并打散失败后的重复连接尝试 |
| Singleflight | 合并同类并发请求 | 多 caller 共享同一次 startup 或 reconnect attempt |
| Recovery deadline | 恢复截止点 | 限制远程 session 恢复总时间的硬边界 |
| `ExecServerError` | 远程执行服务错误 | WebSocket、handshake、registry、RPC 与 protocol 错误 |
| `RpcCallError` | RPC 调用错误 | transport closed、JSON、server、timeout 与容量失败 |
| Aggregated output | 聚合输出 | 按到达顺序组合 stdout/stderr 的执行记录 |
| Truncation metadata | 截断元数据 | 原 token 数与省略 byte 数等不完整性证据 |
| Redaction | 脱敏 | 在日志和提示中移除 token、密码等 secret |
| Reconciliation | 对账 | 结果未知时先查询权威状态，再决定补偿或重试 |

## 46. 网络请求、DNS、代理、TLS、HTTP、WebSocket 与 SSE

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| URL / scheme | 资源定位符/协议方案 | 将目标拆成 http/https/ws/wss、host、port、path、query |
| Origin | 源 | scheme、host、effective port 组成的安全边界 |
| DNS / resolver | 域名系统/解析器 | 将 hostname 转成一个或多个 IP address |
| Happy Eyeballs | 双栈竞速连接 | 交错尝试 IPv6/IPv4，避免坏地址族拖慢连接 |
| TCP | 传输控制协议 | TLS、HTTP、WebSocket 下方的有序可靠 byte stream |
| Proxy route | 代理路线 | TransportDefault、Direct 或具体 Proxy 的 outbound 路径 |
| Forward proxy | 正向代理 | 代表 client 访问外部目标的企业或 managed 中间节点 |
| PAC / WPAD | 代理自动配置/发现 | 按 URL 选择 DIRECT/PROXY 的系统机制 |
| `HTTP_PROXY` / `HTTPS_PROXY` | HTTP/HTTPS 代理变量 | 系统代理解析不可用时的环境配置来源 |
| `ALL_PROXY` / `NO_PROXY` | 通用代理/绕过代理 | fallback proxy 与 direct target pattern |
| `HttpClientFactory` | HTTP client 工厂 | 统一应用 outbound proxy、custom CA 与 cookie policy |
| `RouteAwareClientPool` | 路线感知连接池 | 每个 URL/redirect 重新选 route，并按 route 复用 client |
| Redirect | HTTP 重定向 | 通过 Location 转到新 URL，需重新 route 并清理敏感 header |
| TLS / handshake | 传输安全/握手 | 验证 server certificate 并建立加密 session |
| CA / root store | 证书机构/根信任库 | TLS certificate chain 的 trust anchors |
| Custom CA | 自定义 CA | 企业 TLS inspection 环境添加的明确信任根 |
| MITM | 中间人式 TLS 终止 | Proxy 解密 HTTPS 以实施 method/hook policy 的高风险机制 |
| HTTP method/status | HTTP 方法/状态 | GET/POST 等动作与 101/407/429/5xx 等响应结果 |
| `HttpTransport` | HTTP 传输抽象 | 区分完整 execute 与 streaming response |
| Connect timeout | 建连超时 | 限制 DNS/TCP/TLS connection establishment |
| Request timeout | 请求超时 | 覆盖 route、连接、发送与等待响应的总预算 |
| Idle timeout | 空闲超时 | 已建立 stream 等待下一 event/message 的预算 |
| SSE | Server-Sent Events | HTTP body 上的 server→client 流式事件格式 |
| Event framing | 事件分帧 | 从任意 byte chunks 组合出完整 SSE event |
| `response.completed` | 响应完成事件 | Responses SSE/WebSocket 的协议成功终态 |
| WebSocket / WSS | Web 套接字/安全套接字 | HTTP upgrade 后的双向 message connection |
| Ping / Pong / Close | 探测/回应/关闭 | WebSocket connection control messages |
| Tunnel / CONNECT | 隧道/建立隧道 | 让 proxy 转发 target TCP/TLS bytes |
| Mid-stream failure | 流中途失败 | Headers 成功后、completed 前的 EOF、timeout 或 parse error |
| Route cache / client pool | 路线缓存/client 池 | 缓存代理决策与复用 transport connection 的不同层 |
| Managed proxy | 受管理代理 | 为 child traffic 强制 domain、method、IP 与 credential policy |
| Limited mode | 受限网络模式 | 只允许 GET、HEAD、OPTIONS 等方法 |
| Blocked request | 被拦请求 | 含 host、method、reason、source 的 policy 审计记录 |
| Hermetic test | 封闭测试 | 只依赖测试声明的本地 server、CA、proxy 与 env |

## 47. Rust Workspace、Cargo、Just、Bazel 与构建图

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Workspace | 工作空间 | 共享 Cargo resolver、lockfile、target directory 和政策的一组 packages |
| Manifest | 清单 | `Cargo.toml`、`MODULE.bazel` 等构建声明文件 |
| Rust module | Rust 模块 | crate 内的命名空间、可见性和源码组织，不等于 Bazel module |
| Crate | Rust 编译单位 | library、binary、proc-macro 或 test 的 rustc 输入单位 |
| Cargo package | Cargo 包 | 由一份 package `Cargo.toml` 管理、可产生多个 crates 的单位 |
| Cargo target | Cargo 目标 | package 中的 lib、bin、test、bench、example 或 build script |
| Bazel package | Bazel 包 | 由 `BUILD.bazel` 划定的 label 命名边界 |
| Bazel target / label | Bazel 目标/标签 | `//codex-rs/core:core` 形式的构建图节点身份 |
| Dependency graph | 依赖图 | 由 packages/crates/targets 和依赖边构成的有向图 |
| Manifest / lockfile | 声明/锁文件 | 可接受来源与范围，以及 resolver 选出的具体版本图 |
| Direct / transitive dependency | 直接/传递依赖 | manifest 直接声明的依赖，以及通过其他节点间接到达的依赖 |
| Feature | 功能特性 | 构建期条件能力，会改变 API、代码和 dependency graph |
| Resolver 2 | 第二版解析器 | 更精细地区分 target、build 和 dev feature 上下文 |
| Target triple | 目标平台串 | CPU、OS、ABI 等组成的 Rust 编译目标标识 |
| Host / exec / target platform | 主机/执行/目标平台 | 发起构建、运行 action、运行最终制品的三种平台角色 |
| Build script / proc macro | 构建脚本/过程宏 | 编译期间在执行平台运行的 Rust 代码 |
| Edition / toolchain | 语言纪元/工具链 | Rust 兼容语义选择，以及具体 compiler/components 集合 |
| Profile | 构建配置档 | dev/test/release 等优化、debug、LTO 和 codegen 设置 |
| Just / recipe | 任务运行器/配方 | 为 Cargo、Bazel、Python 等命令提供团队统一入口 |
| Nextest | Rust 测试运行器 | 按 profile、重试和并发组调度 Cargo test executables |
| Schema / snapshot fixture | 模式/快照固件 | 从源码或预期输出派生、需 review 并提交的表示 |
| Bzlmod | Bazel module system | 用 `MODULE.bazel` 管理外部 modules、extensions 和 toolchains |
| Starlark / `.bzl` | Bazel 配置语言/扩展 | 定义可复用 rule、macro 和 helper |
| Rule / macro / action | 规则/宏/动作 | target 类型、生成声明的函数、实际执行命令 |
| `crate.from_cargo` | 从 Cargo 导入 crates | 用 Cargo.toml/Cargo.lock 建立 Bazel 的 Rust dependency metadata |
| `rules_rs` | Rust 构建规则 | Codex Bazel 图采用的 Rust rules/toolchain module |
| Hermetic toolchain | 封闭工具链 | 尽量只依赖显式、固定 compiler、linker 和 inputs |
| Constraint / `select()` | 约束/配置选择 | 按 OS、CPU、ABI 等平台条件选择 rule 属性 |
| Runfiles | 运行文件集合 | Bazel 为 binary/test 提供的声明式 runtime 文件视图 |
| `compile_data` / `build_script_data` | 编译/构建脚本数据 | 编译 crate 或执行 build.rs 时显式可见的非 Rust inputs |
| Remote cache / execution | 远程缓存/执行 | 复用相同 action 输出，或在匹配 worker 上运行 action |
| Cargo/Bazel parity | Cargo/Bazel 对齐 | 两条构建路径尽量维持相同产品、依赖与测试语义 |

## 48. Rust 编译、链接、原生依赖与制品

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Cargo / rustc / linker | 构建编排/编译器/链接器 | 解析调度 graph、检查生成 Rust code、合并 objects/libraries |
| Parsing / macro expansion | 语法解析/宏展开 | 建立语法结构并生成实际参与编译的 items |
| Type / borrow checking | 类型/借用检查 | 证明调用、trait、ownership、reference 与 lifetime 合法 |
| MIR / LLVM IR | 中层/LLVM 中间表示 | Rust 语义到目标 machine code 间的主要抽象层 |
| Codegen / codegen unit | 代码生成/生成单元 | 产生目标机器码及其并行优化粒度 |
| Monomorphization | 单态化 | 为 generic 的具体类型组合产生专门机器码 |
| LTO / ThinLTO | 链接时优化 | 跨传统 crate/codegen 边界进行优化 |
| `.rmeta` / `.rlib` | Rust 元数据/库制品 | 下游编译信息，以及含 metadata/object 的 Rust archive |
| Object file | 目标文件 | 已有机器码但仍需解析 symbols/relocations 的 `.o/.obj` |
| Symbol / relocation | 符号/重定位 | Linker 识别的定义/引用，以及最终地址修正记录 |
| Static / dynamic library | 静态/动态库 | Link 时抽取的 archive，或运行时加载的 shared library |
| ELF / Mach-O / PE | 平台 image 格式 | Linux、macOS、Windows 的主要 executable 容器 |
| API / ABI | 编程/二进制接口 | 源码契约与 calling、layout、symbol 等机器契约 |
| FFI | 外部函数接口 | Rust 与 C 等外部 ABI 的 unsafe 交互边界 |
| Header / include path | 头文件/包含路径 | Native compiler 读取的声明及其搜索目录 |
| Library search path | 库搜索路径 | Linker 寻找 native archives/shared libraries 的目录 |
| `build.rs` / `OUT_DIR` | 构建脚本/输出目录 | Cargo 编译前程序及其受管理生成物位置 |
| `rustc-link-*` / `rustc-cfg` | 链接/配置指令 | Build script 交给 Cargo/rustc 的 library、arg 和 cfg |
| `cc` / `pkg-config` | C 构建/包发现 | 编译 native source，并从 `.pc` 查询 header/library flags |
| Sysroot | 系统根 | Cross build 使用的 target headers、libraries 与 runtime 根视图 |
| glibc / musl | Linux C libraries | 不同 Linux libc、loader 与发布工具链选择 |
| MSVC / gnullvm | Windows 工具链 | 不同 Windows ABI、linker 与 native artifact 路径 |
| CRT | C runtime | 程序启动、标准运行库与平台 ABI 支持 |
| `-ObjC` / `-lc++` | macOS linker flags | Objective-C categories 与 C++ runtime linking |
| Debug information | 调试信息 | 将 machine address 映射回函数、文件与行号 |
| DWARF / dSYM / PDB | 平台调试制品 | Linux、macOS、Windows 常见 symbol/debug formats |
| Strip / symbol archive | 剥离/符号归档 | 缩小 release binary 并离线保存诊断信息 |
| Symbolication | 符号化 | 将 crash address 还原为函数名与源码位置 |
| Dynamic loader | 动态装载器 | 进程启动时映射 image/libraries 并解析 imports |
| Compile/link/load/runtime error | 编译/链接/装载/运行错误 | 四个必须分开定位的失败阶段 |
| Cross compilation | 交叉编译 | Host/exec 与最终 target platform 不同的构建 |

## 49. Rust 运行时内存、布局、分配与 OOM

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Stack / stack frame | 栈/栈帧 | Thread 调用链的局部状态区域，有有限 stack budget |
| Heap / allocator | 堆/分配器 | 动态 allocation 区域及其申请、复用、释放管理组件 |
| Virtual memory / RSS | 虚拟内存/驻留集 | 进程地址空间，以及当前驻留 physical pages 的近似值 |
| Inline size / object graph | 内联大小/对象图 | Handle 自身 bytes，以及沿 owning pointers 可达的全部 allocations |
| Alignment / padding | 对齐/填充 | Value 地址约束及 compiler 为满足布局加入的空隙 |
| Fat pointer | 胖指针 | 除 data pointer 外还携带 length 或 vtable metadata 的 handle |
| Move / Copy / Clone | 移动/复制/克隆 | 转移 owner、按位复制、或由类型定义的新 value 语义 |
| `String` / `Vec<T>` | 字符串/向量 | 概念上由 pointer、length、capacity 指向 heap payload |
| Length / capacity | 长度/容量 | 当前初始化 elements 与无需 reallocate 可容纳的 elements |
| Reallocation | 重新分配 | 申请更大 block、移动 elements、释放旧 block |
| `clear` / `shrink_to_fit` | 清空/尽量收缩 | Drop elements 并保留 capacity，或请求减少 capacity |
| `Box<T>` / trait object | 盒装值/Trait 对象 | 单 owner heap payload，以及 data pointer + vtable 动态分派 |
| `Arc<T>` / `Rc<T>` | 原子/普通引用计数 | 跨 thread 或单 thread 的 shared ownership |
| Strong / Weak reference | 强/弱引用 | 决定 T 存活的 owner，以及不延长寿命的 observer |
| Strong cycle | 强引用环 | Arc owners 互相阻止 strong count 归零的 leak |
| `Arc<Mutex<T>>` | 共享互斥状态 | Arc 管 ownership，Mutex 管 exclusive mutable access |
| `Pin<Box<Future>>` | 固定盒装 Future | 将 async state 放 heap 并限制 poll 后移动 |
| `Bytes` | 共享字节视图 | Clone 通常共享 immutable backing allocation |
| `VecDeque` | 双端队列 | Ring buffer，适合 FIFO retention/eviction |
| Per-entry overhead | 单项固定开销 | Collection slot、handle、hash/control 与 allocator metadata |
| Transient copy / peak | 瞬时副本/峰值 | Snapshot、serialization、compression 中短时共存的 allocations |
| Memory leak / retention | 泄漏/保留 | 无用但不可回收，与 cache/capacity/owner 仍有意持有 |
| Fragmentation | 碎片 | Free blocks/pages 的形状使其难复用或难归还 OS |
| Allocator retention | 分配器保留 | Freed blocks 暂存 arena/cache 供下次复用 |
| High-water mark | 高水位 | 峰值负载后 RSS 或 reserved memory 保持较高 |
| Live bytes / allocation rate | 存活字节/分配速率 | 当前被 owner 持有量与单位时间累计申请量 |
| OOM / OOM killer | 内存耗尽/内核终止 | Allocation/limit 耗尽，以及 Linux 压力下杀进程机制 |
| Stack overflow | 栈溢出 | 递归、大 frame 等耗尽 thread call stack |
| Use-after-free / double free | 释放后使用/重复释放 | Safe Rust 阻止、unsafe/FFI 仍可能制造的 memory corruption |
| `mem::forget` / `Box::leak` | 忘记/泄漏 | 明确跳过 Drop 或永久交出 Box 自动释放 |
| Heap snapshot/profile | 堆快照/剖析 | 比较 live allocations、retainers 与 allocation callsites |

## 50. Serde、JSON/TOML、反序列化边界与 Schema

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Serialization / Deserialization | 序列化/反序列化 | Rust value 与外部 format representation 之间的两个独立方向 |
| Serde data model | Serde 数据模型 | Struct/map/sequence/string/integer 等连接 Rust type 与具体格式的抽象层 |
| Format / framing / transport | 格式/分帧/传输 | JSON/TOML 语法、JSONL newline 边界，以及 HTTP/stdio/file 载体 |
| Wire shape | 线上形状 | Consumer 真正看到的 key、tag、nesting、missing/null 与 numeric representation |
| `serde_json::Value` / DOM | 动态 JSON 值/对象树 | Owned JSON 节点树，灵活但有延迟验证和 heap amplification 成本 |
| `rename_all` / `rename` / `alias` | 批量重命名/精确重命名/别名 | 决定 canonical wire 名称与兼容读取名称 |
| Missing / null / empty | 缺失/空值/空集合 | Object 无 key、key 值为 null、以及存在但元素数为零的三种状态 |
| `default` / `skip_serializing_if` | 默认/条件跳过写出 | 分别影响读取 missing 字段与写出时是否省略字段 |
| Tagged / untagged enum | 带标签/无标签枚举 | 通过 discriminator 或输入 shape 选择 Rust variant |
| Discriminator | 判别字段 | `type`、`method` 等显式指出 union variant 的 wire field |
| `flatten` | 铺平 | 把嵌套 fields 合并进同一 JSON/TOML object/table |
| Custom deserializer | 自定义反序列化器 | 在边界执行兼容转换、单字段 validation 或资源控制的代码 |
| `Visitor` / `DeserializeSeed` | 访问者/带状态反序列化 | 逐项接收 format values，并携带节点预算等共享 decode state |
| Duplicate key | 重复键 | 同一 object 中出现同名 key，可能造成不同 parser 解释不一致 |
| Node/depth/byte limit | 节点/深度/字节上限 | 分别约束 tree 宽度总量、nesting 和原始/展开后的 payload 大小 |
| Heap amplification | 堆放大 | 紧凑 wire 解码成大量 enums、vectors、strings 后占用更多内存 |
| Deserialization bomb | 反序列化炸弹 | 利用宽、深、压缩或编码放大 CPU/内存消耗的恶意输入 |
| Arbitrary precision | 任意精度 | 保留超出普通 integer/float 直接表示范围的 JSON number 词法值 |
| JSONL / record delimiter | 逐行 JSON/记录分隔符 | 每行一条 JSON value，newline 定义相邻 rollout records 的边界 |
| Partial line | 截断行 | Crash 或短写造成的尾部不完整 JSONL record |
| `TomlValue` / config layer | TOML 动态值/配置层 | Typed decode 前用于来源优先级、合并、alias 和 trust 处理的 tree |
| `serde_ignored` | 忽略字段追踪 | 收集 typed target 未消费的配置路径，用于严格未知字段诊断 |
| `serde_path_to_error` | 错误路径追踪 | 为 nested type error 保留具体 field/index path |
| JSON Schema / `schemars` | JSON 模式/生成库 | 描述 wire shape 的机器可读生成物，不等于 runtime validator |
| `ts-rs` / `TS` | TypeScript 类型生成 | 从 Rust protocol types 生成客户端静态声明 |
| Schema drift | 模式漂移 | Serde runtime wire、Schema fixture 与 TypeScript declaration 不一致 |
| Canonical encoding | 规范编码 | 为签名或 exact-byte hash 额外定义唯一 JSON bytes 的规则 |
| Wire fixture / round trip | 线上样本/往返 | 直接固定外部形状的测试，以及 encode 后 decode 的配对测试 |

## 51. Rust 生命周期、Borrow、Send/Sync 与异步边界

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Owner / borrow / reference | 所有者/借用/引用 | 决定资源释放的 value，以及不取得释放权的临时 view |
| Shared / mutable reference | 共享/可变引用 | `&T` 多读与 `&mut T` 独占访问能力 |
| Lifetime / outlives | 生命周期/活得更久 | Compiler 证明 reference 有效范围及 `'a: 'b` 关系的语言机制 |
| Lifetime elision / NLL | 生命周期省略/非词法生命周期 | 隐式推导 annotation，以及借用在最后使用点结束 |
| Reborrow / deref coercion | 再借用/解引用转换 | 从已有 reference 建更短借用，以及自动适配 `&String`→`&str` |
| `Cow<'a, T>` | 写时克隆 | 可同时表达 borrowed 与 owned data 的类型 |
| `&'static T` / `T: 'static` | 静态引用/静态类型约束 | 具体引用永久有效，以及 type 不依赖短 borrow；后者不保证永不 drop |
| `Send` / `Sync` | 可发送/可同步共享 | Ownership 可跨 thread 移动，以及 `&T` 可跨 thread 使用 |
| Auto trait | 自动 trait | Compiler 根据 fields 自动推导 Send/Sync 等性质 |
| `Rc` / `Arc` / `Weak` | 引用计数/原子引用计数/弱引用 | 单线程共享、跨线程共享 owner 与不延长存活的 observer |
| `RefCell` / `Mutex` | 动态借用盒/互斥锁 | 单线程 runtime borrow check 与多线程 mutual exclusion |
| Future state / suspension point | Future 状态/挂起点 | Async state machine 保存的 captures、locals 和 `.await` 恢复位置 |
| `async move` | 移动式异步块 | 按 value 捕获 binding；捕获 reference 时仍保留原短 lifetime |
| `tokio::spawn` / `spawn_local` | 多线程/本地任务生成 | Send + static task 边界，以及显式单线程 non-Send task 边界 |
| `spawn_blocking` | 阻塞任务生成 | 将 owned Send closure 放入 blocking thread pool |
| `Pin<Box<...>>` | 固定盒装值 | 固定 Future storage，并用 heap pointer 统一大小 |
| `BoxFuture<'a, T>` | 盒装 Future | `Pin<Box<dyn Future<Output=T> + Send + 'a>>` |
| RPITIT | Trait 返回位置 impl Trait | 让 trait method 显式返回静态分派 `impl Future + Send` |
| Type erasure / object safety | 类型擦除/对象安全 | 用 `dyn Trait` 统一 concrete types，以及 trait 能否这样使用的规则 |
| Blanket implementation | 覆盖式实现 | 为所有满足 bound 的 T 提供统一 adapter implementation |
| HRTB / `for<'a>` | 高阶生命周期约束 | 对任意调用者选择的 lifetime 均实现某 trait |
| `DeserializeOwned` | 可拥有式反序列化 | Decode result 不借用临时输入 buffer |
| Variance / invariant | 型变/不变 | Generic container 是否允许相关 lifetime/type 的安全替换 |
| Borrowed data escapes | 借用数据逃逸 | 短 reference 被放进更长寿的 field、queue、callback 或 task |
| Future is not Send | Future 不可发送 | 跨 await state 保存 non-Send value，不能进入多线程 spawn |
| Lock guard / critical section | 锁守卫/临界区 | 以 scope/drop 释放锁，以及持锁保护 shared state 的代码 |
| Scoped / structured concurrency | 有作用域/结构化并发 | Child 不逃离 parent，并由 parent join/cancel 的任务所有权结构 |
| Detached task / JoinHandle | 分离任务/等待句柄 | 无 caller 等待的后台 task，以及观察 completion/panic/abort 的 owner |

## 52. Rust Result、thiserror、anyhow、Panic 与错误边界

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| `Result<T,E>` / `Option<T>` | 结果/可选值 | 显式表达成功或 typed failure，以及存在或正常缺失的 enums |
| `?` / early return | 问号/提前返回 | 解包成功值，失败时通过 From 转换并返回 Err/None |
| `map` / `map_err` / `and_then` | 映射成功/错误/然后 | 分别转换 success、failure，或串联另一个会失败的步骤 |
| `ok_or_else` / `transpose` | 缺失转错误/翻转 | Lazy 构造 Option error，以及翻转 Option<Result>/Result<Option> |
| `From` / `Into` | 从/转入 | Source error 与当前函数 target error 之间的转换 trait |
| Typed / type-erased error | 类型化/擦除类型错误 | 可 match 的 enum/struct，以及统一容纳多类 source 的 anyhow/dyn Error |
| `thiserror` | 错误派生库 | 生成 Display、Error、source 和 From implementations |
| `#[error]` / `#[source]` / `#[from]` | 展示/原因/转换属性 | 定义文案、连接 cause chain 并生成 source conversion |
| Transparent error | 透明错误 | Wrapper 直接委托内部 error 的 Display/source |
| Anyhow / Context | 应用错误容器/上下文 | 在 orchestration 层包住多种 source 并补 operation 信息 |
| Cause/source chain | 原因链 | 从外层 operation context 连到底层 typed error 的结构 |
| Downcast | 向下转换 | 从 erased error chain 恢复具体 source type |
| Stringify | 字符串化 | 将 error 降为文本并通常切断 typed chain |
| Display / Debug / alternate Display | 展示/调试/交替展示 | 简洁消息、工程细节和 anyhow 完整上下文格式 |
| Error projection | 错误投影 | 将丰富内部错误映射为 stable code 和 safe bounded message |
| Panic / unwind / abort | 恐慌/展开/中止 | 非普通 Result 的 invariant failure 及其两种进程处理策略 |
| `unwrap` / `expect` | 强制取值/带说明强制取值 | 非 Ok/Some 时 panic 的局部不变量断言 |
| `catch_unwind` | 捕获展开 | 隔离 unwind panic 的特殊边界，不保证业务 state 可恢复 |
| `JoinHandle` / `JoinError` | 任务句柄/任务等待错误 | 区分正常业务 Result 与 task panic/cancel 的 Tokio 运行层 |
| Nested Result | 嵌套结果 | Timeout、channel、task runtime 与业务 operation 各自形成的多层结果 |
| Cancelled task | 已取消任务 | Abort/shutdown 等造成的 JoinError 终态，不必等同故障 |
| Primary / cleanup error | 主错误/清理错误 | 原 operation failure 与收尾阶段的次级 failure |
| Error aggregation | 错误聚合 | 有界保存批量或并发操作的多个 failures |
| `io::ErrorKind` / raw OS error | I/O 类别/原始系统错误 | 可移植分类，以及平台 errno/code 细节 |
| Backtrace | 回溯 | Error/panic 捕获的调用位置辅助信息 |
| Fault injection | 故障注入 | 主动制造 I/O、parse、timeout、panic、cancel 检查错误 contract |

## 53. Rust 宏、Attributes、cfg、Cargo Features 与生成代码

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Macro / expansion | 宏/展开 | Compile time 接收 tokens 并生成 Rust program 的机制 |
| Declarative / procedural macro | 声明式/过程宏 | `macro_rules!` pattern expansion，以及 host 上执行的 TokenStream 程序 |
| Derive / attribute macro | 派生/属性宏 | 根据 item 添加 impl，或接收并替换整个 item 的 proc macros |
| Matcher / arm / transcriber | 匹配器/分支/转写器 | `macro_rules!` 输入 pattern、候选规则与输出 template |
| Metavariable / fragment | 元变量/语法片段 | `$name:ident`、`$ty:ty`、`$expr:expr` 等捕获的 typed syntax |
| Token tree / `tt` | 词法树 | 单 token 或平衡 delimiter 中 tokens 的宽泛 macro fragment |
| Repetition / separator | 重复/分隔符 | `$(...)*`、`+`、`?` 与 comma 等列表展开规则 |
| Macro DSL | 宏领域语言 | 由 matcher arms 定义的项目专用声明语法 |
| Hygiene / `$crate` | 卫生性/定义 crate | 控制生成名称冲突与 exported macro 自身 helper path |
| Helper attribute | 辅助属性 | `serde`、`arg`、`error` 等由对应 derive macro 读取的 metadata |
| Outer / inner attribute | 外部/内部属性 | 修饰后续 item，或修饰所在 crate/module/container |
| `#[cfg]` / `cfg!` | 条件属性/条件宏 | 删除不匹配 items，以及生成 compile-time bool 而不删除普通分支 |
| `cfg_attr` | 条件属性 | Predicate 成立时才应用另一个 attribute |
| Target cfg | 目标条件 | target OS/architecture/environment/family 的编译选择，不等于 host |
| `cfg(test)` / `debug_assertions` | 测试/调试条件 | 当前 crate test build 与 profile settings 产生的 predicates |
| Cargo feature | Cargo 功能 | Manifest resolution 阶段选择依赖和编译 capability 的 flags |
| Dependency feature | 依赖功能 | 为 Serde/Tokio 等依赖启用 derive/macros/runtime API |
| Feature unification | 功能合并 | Resolution context 中汇总同一 package version 的 enabled features |
| Product feature | 产品功能开关 | Codex binary 内由 config/policy 决定的 runtime `Feature` |
| Build script / custom cfg | 构建脚本/自定义条件 | Host 上运行并向 rustc 发 link 与自定义 predicate metadata |
| `rustc-check-cfg` / `rustc-cfg` | 条件声明/启用 | 声明合法 custom cfg，并为当前 compilation 实际设置它 |
| Compile-time include | 编译期嵌入 | `include_str!`/`include_bytes!` 读取并嵌入 source-tree data |
| Compile data | 编译数据 | Bazel action 中允许 compile-time 文件读取的显式 inputs |
| Generated fixture | 生成样本 | Schema、TS、snapshot 等写入仓库并参与 drift review 的 artifacts |
| `cargo expand` | Cargo 展开工具 | 查看 macro/derive/attribute 展开后 Rust 的诊断工具 |
| Feature graph / platform matrix | 功能图/平台矩阵 | Features 的真实启用来源，以及多 target/profile 组合覆盖 |
| Compile-fail test | 编译失败测试 | 证明非法 macro/type/cfg usage 必须被 compiler 拒绝 |
| Facade / `compile_error!` | 门面/编译错误 | 集中平台差异的统一 API，以及主动拒绝非法 cfg 组合 |

## 54. Rust Trait、泛型、关联类型与动态分发

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Trait / implementation | 行为契约/实现 | 定义共同能力，以及具体类型兑现该能力的方法体 |
| Required / default method | 必需/默认方法 | 实现者必须提供的方法，以及可继承或 override 的通用行为 |
| Receiver / `Self` | 接收者/自身类型 | `self` 权限形状，以及当前实现对应的具体类型 |
| Supertrait / trait bound | 上级 Trait/Trait 约束 | 实现或泛型参数还必须满足的能力集合 |
| Generic / type parameter | 泛型/类型参数 | 以 T、I 等占位符编写并由调用点确定具体类型的代码 |
| `where` clause | 条件子句 | 集中声明泛型、lifetime 和 associated type 限制 |
| Associated type / constant | 关联类型/常量 | 每份 Trait 实现选择的配套类型或稳定类型级值 |
| Static / dynamic dispatch | 静态/动态分发 | 编译时确定具体实现，以及运行时经 vtable 选择实现 |
| Monomorphization | 单态化 | 为实际使用的具体泛型类型生成专门代码 |
| `impl Trait` / `dyn Trait` | 实现某 Trait/动态 Trait | 隐藏但保留具体静态类型，以及擦除具体类型的运行时接口 |
| Trait object / vtable | Trait 对象/虚方法表 | `dyn Trait` 指针及其用于定位具体方法实现的 metadata |
| Type erasure | 类型擦除 | 编译期验证具体实现后，仅向消费者暴露共同 Trait 能力 |
| Dyn compatibility / object safety | 动态兼容性/对象安全 | Trait 能否形成并通过 `dyn Trait` 调用的规则 |
| `Sized` / `?Sized` / DST | 固定大小/可非固定大小/动态大小类型 | 泛型默认大小约束、其放宽，以及 str/slice/dyn Trait 等类型 |
| Blanket impl / adapter | 毯式实现/适配器 | 为所有满足 bound 的 T 自动提供 typed→erased 转换 |
| RPITIT / boxed Future | Trait 返回 impl Trait/装箱 Future | 强类型 Future contract，以及统一不同 Future 的动态返回形状 |
| `Fn` / `FnMut` / `FnOnce` | 共享/可变/单次调用闭包 | Closure 如何读取、修改或消费捕获环境的契约 |
| Coercion / downcast | 自动调整/向下转换 | 具体指针转 Trait object，以及从 erased 对象尝试恢复具体类型 |
| `Any` / escape hatch | 任意类型接口/逃生口 | 为少量实现特有需求提供受控 runtime 类型恢复入口 |
| Coherence / orphan rule | 实现一致性/孤儿规则 | 保证 Trait impl 唯一可选并限制两个外部项的本地实现 |
| Enum dispatch | 枚举分发 | 用封闭 variants 和穷尽 match 替代开放 Trait object 的方案 |
| Contract test | 契约测试 | 对多个实现运行同一组公共行为断言 |

## 55. Rust Enum、模式匹配与状态机

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Enum / variant | 枚举/变体 | 从多个互斥 shape 中选择一个的类型及其具体情况 |
| Unit/tuple/struct variant | 单元/元组/结构变体 | 无 payload、位置 payload 和具名字段 payload |
| Sum type / payload | 和类型/载荷 | 择一存在的数据类型，以及随 variant 携带的数据 |
| Pattern / destructuring | 模式/解构 | 判断 shape 并从中取出字段或内部值 |
| Scrutinee / match arm | 被检查值/匹配分支 | match 输入，以及 `pattern => expression` 规则 |
| Exhaustiveness / wildcard | 穷尽性/通配符 | 覆盖全部可能 shape，以及 `_` fallback |
| Rest / or-pattern | 剩余/或模式 | `..` 忽略当前结构剩余字段，`|` 合并多个 shape |
| Match guard / `@` binding | 匹配守卫/整体绑定 | 命中后附加条件，以及保留整体同时解构字段 |
| Refutable / irrefutable | 可反驳/不可反驳 | Pattern 可能失败或一定成功 |
| `if let` / `let else` / `while let` | 条件/失败退出/循环匹配 | 单形状执行、失败提前离开和持续取值的 pattern 控制流 |
| Match ergonomics / partial move | 匹配人体工学/部分移动 | 引用绑定自动调整，以及取走部分 payload 后的 ownership 状态 |
| Never type / `!` | 永不返回类型 | return、break、panic 等发散表达式的类型 |
| State machine / transition | 状态机/转换 | 状态、输入、合法变化、effects 和 owner 的完整模型 |
| Terminal state | 终态 | 当前生命周期语义下不再继续运行的状态 |
| Derived state / projection | 派生状态/投影 | 从权威事件或 runtime facts 计算出的消费者视图 |
| Active flags | 活跃标志 | 可在同一 Active 主状态中并存的正交等待事实 |
| Tagged enum / discriminator | 带标签枚举/判别字段 | Wire JSON 用 type 等字段选择 variant payload shape |
| `non_exhaustive` | 非穷尽 API | 强制外部 crate 为未来新增 variant 保留 fallback |
| Typestate | 类型状态 | 以不同 Rust 类型在编译期限制状态可用操作 |
| Enum dispatch | 枚举分发 | 对封闭 variant 集合使用穷尽 match 选择行为 |

## 56. Rust Iterator、Closure、惰性管线与 Stream

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Iterator / IntoIterator / Item | 迭代器/转为迭代器/元素 | 逐项 next 的状态机、生成它的契约和 associated element type |
| Adapter / consumer | 适配器/消费者 | 构造新 lazy iterator，以及真正驱动管线的终结操作 |
| Lazy / materialize | 惰性/物化 | 按需拉取计算，以及 collect 成实际集合快照 |
| Closure / capture | 闭包/捕获 | 管线中的匿名函数及其借用、修改或拥有的外部环境 |
| `iter` / `iter_mut` / `into_iter` | 共享/可变/所有权迭代 | 常见产出 `&T`、`&mut T`、T 的集合入口 |
| `map` / `filter` / `filter_map` | 映射/筛选/筛选映射 | 一进一出、保留原项，以及用 Option 转换并丢弃 None |
| `flat_map` / `flatten` | 展开映射/压平 | 每项产生零到多项并合并，以及去掉一层序列 |
| `cloned` / `copied` | 克隆/复制 | 将 reference items 显式变成 owned Clone/Copy values |
| `collect` / FromIterator | 收集/从迭代器构造 | 按目标 Vec/Map/String/Result/Option 等类型消费序列 |
| Short-circuit | 短路 | any/all/find 或 Result collect 在结论确定后停止 |
| `chain` / `zip` | 串接/配对 | 先后连接两序列，以及逐位置组合到较短侧结束 |
| `peekable` / `next_if` | 可偷看/条件取下项 | 检查相邻项并仅在 predicate 成立时推进 |
| `fold` / accumulator | 折叠/累加器 | 以显式初值反复组合每项得到一个结果 |
| Size hint / FusedIterator | 大小提示/熔合迭代器 | 预分配信息，以及首次 None 后永久结束保证 |
| Stream / `StreamExt` | 异步流/流扩展 | 下一项可 Pending 的异步序列及其组合方法 |
| `buffer_unordered` | 无序缓冲 | 有界并发驱动 inner futures 并按完成顺序产出 |
| `FuturesUnordered` / in-flight | 无序 Future 集合/进行中 | 动态轮询并发任务，以及尚未完成的工作数量 |
| Turbofish | 涡轮鱼 | `::<Type>` 显式指定泛型类型参数的俗称 |

## 57. Rust 集合、Map、Set、Queue 与 Entry API

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Vec / slice / capacity | 向量/切片/容量 | 连续 owned sequence、借用视图与免重分配空间 |
| VecDeque / FIFO | 双端队列/先进先出 | 适合头尾操作的环形序列与请求队列顺序 |
| HashMap / collision | 哈希映射/碰撞 | 平均快速 key lookup，以及不同 key 共享 hash |
| BTreeMap / range | B 树映射/范围 | 按 Ord 稳定遍历并支持区间查询的 map |
| IndexMap | 索引映射 | 同时提供 key lookup 与插入/显式移动顺序 |
| HashSet / BTreeSet | 哈希/有序集合 | 无独立 value 的唯一 membership 集合 |
| Entry / Vacant / Occupied | 入口/空位/占用 | 一次定位后集中处理缺失、插入和重复 key |
| Duplicate / stable order | 重复/稳定顺序 | Eq 相同业务 key，以及可重复的遍历输出语义 |
| Bucket / queue | 桶/队列 | 按 key 分组的子集合，以及有先后顺序的待处理项 |
| Eviction / LRU | 驱逐/最近最少使用 | 满载删除策略，以及按访问新旧选择 victim |
| Composite key / membership | 复合键/成员关系 | 覆盖全部身份维度的 key，以及是否存在判断 |
| Drain / retain | 排空/保留 | 移出元素交接 ownership，以及原地筛除 |
| Tie-breaker | 平局规则 | 主排序值相同时建立确定性全序的次级 key |

## 58. Rust 内部可变性、锁、原子类型与一次初始化

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Interior mutability | 内部可变性 | 通过共享引用由 wrapper 在运行时控制修改 |
| Cell / RefCell | 值单元/引用单元 | 单线程 Copy get/set 与运行时 borrow checking |
| Mutex / guard / critical section | 互斥锁/守卫/临界区 | 独占状态访问、RAII 解锁及受保护范围 |
| RwLock / poisoning | 读写锁/中毒 | 多读单写与 std 锁 panic 后的不变量警告 |
| Atomic / CAS | 原子类型/比较交换 | 单值不可分割操作与条件式状态更新 |
| Relaxed / Acquire / Release | 宽松/获取/发布 | 原子值本身保证及跨数据可见性顺序 |
| Happens-before | 先发生于 | 一方写入被另一方可靠观察的同步关系 |
| OnceLock / LazyLock / OnceCell | 一次锁/惰性锁/一次单元 | 同步赋值一次、lazy 初始化和异步初始化一次 |
| Semaphore / permit | 信号量/许可 | 有界并发配额及其 RAII ownership |
| Cancellation safety | 取消安全 | Future 在 await 被 drop 后仍保持业务不变量 |

## 59. Rust Module、路径、可见性与 Re-export

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Crate root / module tree | 包根/模块树 | `lib.rs` 或 `main.rs` 形成的根，以及由 `mod` 声明连接起来的名称层级 |
| Module / `mod` | 模块/模块声明 | Rust 的名称空间与可见性边界；既可写在当前文件内，也可从约定文件加载 |
| Inline/out-of-line module | 内联/文件模块 | `{ ... }` 中直接定义的模块，以及由 `foo.rs` 或 `foo/mod.rs` 提供内容的模块 |
| Path / `crate` / `self` / `super` | 路径/包根/当前/父级 | 定位 item 的名称序列，以及三种常见相对起点 |
| Visibility / privacy | 可见性/私有性 | 哪些 module 可以通过路径命名某个 item；未写 `pub` 时默认私有 |
| `pub(crate)` / `pub(super)` / `pub(in ...)` | 包内/父级/指定范围公开 | 把可见范围限制在整个 crate、父 module 或某个祖先 module |
| `use` / import | 使用/导入 | 在当前 scope 建立较短名称；本身通常不扩大原 item 的公开范围 |
| `pub use` / re-export | 公开导入/再导出 | 把别处 item 作为当前 module 的公开路径再次提供 |
| Facade | 门面 | 用少量稳定入口隐藏内部文件和 module 布局的 API 组织方式 |
| Public API surface | 公开 API 表面积 | 外部 crate 能命名和依赖的类型、函数、Trait、字段、variant 与路径集合 |
| Encapsulation | 封装 | 隐藏实现细节，只暴露调用者完成任务所需的最小契约 |
| Prelude / glob import | 预导入集合/通配导入 | 自动或显式一次带入一组常用名称；方便但会弱化名称来源线索 |
| `doc(hidden)` | 文档隐藏 | 公共 item 仍可被命名，但通常不出现在生成文档中；它不等于 private |
| Deprecated alias | 已弃用别名 | 暂时保留旧名称并引导调用者迁移到新路径的兼容层 |
| Feature/platform-gated module | 功能/平台条件模块 | 只有对应 `cfg` 条件成立时才进入本次编译的 module 或实现 |
| Sealed Trait | 封闭 Trait | 借助调用者无法实现的私有 supertrait，限制外部 crate 新增实现 |

## 60. Rust Struct、Impl、Newtype、Builder 与领域建模

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Struct / field | 结构体/字段 | 将一组同时存在的数据组成一个类型，以及每个组成部分 |
| Named/tuple/unit struct | 具名/元组/单元结构体 | 有字段名、按位置存字段和不存字段的三种 Struct shape |
| Struct literal | 结构体字面量 | 用 `Type { field: value }` 直接构造实例的语法 |
| `impl` / receiver | 实现块/接收者 | 类型相关行为的定义位置，以及 self、&self、&mut self 参数 |
| Associated function | 关联函数 | 通过 `Type::function` 调用但不接收具体实例的函数 |
| Smart constructor | 智能构造器 | 在创建 value 时完成验证、规范化或身份生成的入口 |
| Invariant | 不变量 | 一个有效实例在构造后和所有公开操作后都应成立的规则 |
| Newtype / type identity | 新类型包装/类型身份 | 单字段 Struct 建立的新编译期类型及其领域含义 |
| Type alias | 类型别名 | 不产生新类型身份、只提供另一种拼写的名称 |
| Primitive obsession | 基础类型迷恋 | 用裸字符串、数字和布尔值承载多种不可互换的业务语义 |
| Fallible constructor | 可失败构造器 | 以 Option 或 Result 明确表示输入可能无法成为有效实例 |
| Aggregate invariant | 聚合不变量 | 涉及多个字段或多个集合元素的整体规则 |
| DTO / domain model | 数据传输对象/领域模型 | 侧重边界搬运的数据 shape，以及侧重身份和业务规则的类型 |
| Builder / product | 构建器/产品 | 分步收集输入的准备态对象，以及最终成功产生的目标对象 |
| Fluent API / typestate | 流式接口/类型状态 | 返回 self 的链式配置，以及用类型编码构建阶段的方法 |
| Parse, don't validate | 解析而非只检查 | 将验证结果转换并保存成更精确的可信类型 |
| Composition | 组合 | 通过字段复用其他类型的数据、能力或不变量 |
| Transparent serialization | 透明序列化 | Rust 中保留 wrapper 类型，wire 上仍按内部单值编码 |

## 61. Rust From、TryFrom、AsRef、Borrow、Cow 与类型转换

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Conversion / coercion / cast | 转换/自动调整/强制转换 | Trait 驱动变换、引用自动调整和语言级 `as` 运算符 |
| `From` / `Into` | 从……转换/转入 | 标准、不可失败的 owned 转换和自动获得的调用侧形式 |
| `TryFrom` / `TryInto` | 尝试从/尝试转入 | 返回 Result 并保留验证错误的转换 |
| `FromStr` / parse | 从字符串/解析 | 把文本语法验证成目标类型的标准入口 |
| Type inference / annotation | 类型推断/标注 | 从上下文确定目标类型，以及显式写出类型消除歧义 |
| `AsRef` / `AsMut` | 作为共享/可变引用 | 不消费 owner 的廉价 borrowed view 接口 |
| `Borrow` | 借用等价形式 | 与 owned value 保持 Eq、Hash、Ord 语义一致的视图 |
| `Clone` / `ToOwned` | 克隆/转为拥有 | 复制同类型 value，以及从 borrowed form 产生 owned form |
| `Cow` | Clone-on-Write，写时克隆 | 用 Borrowed/Owned 两个 variant 统一按需分配的结果 |
| `into_owned` / `to_mut` | 转 owned/转可变 | 必要时复制借用内容，以及修改前确保持有 owned value |
| Deref coercion | 解引用自动调整 | 编译器在引用上下文中自动取得 wrapper target reference |
| Infallible/fallible | 不可失败/可失败 | 总能产生目标 value，以及必须由 Option/Result 表达失败 |
| Lossless/lossy | 无损/有损 | 完整保留信息，以及可能替换、截断或舍弃内容 |
| Allocation / clone / move | 分配/克隆/移动 | 创建存储、复制 value 和转交 ownership 三种不同成本 |
| Round trip | 往返转换 | A→B→A 后验证信息和身份是否仍保持 |
| Boundary conversion | 边界转换 | 在输入输出处集中 raw、domain、DTO 和 wire value 变换 |
| Blanket impl / orphan rule | 毯式实现/孤儿规则 | 条件式批量 Trait impl，以及外部 Trait/类型的实现限制 |

## 62. Rust 智能指针、Deref、Drop、Pin 与 Unpin

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Smart pointer / pointee | 智能指针/被指向值 | 附带 ownership 规则的 handle，以及它实际指向的 T |
| `Box<T>` / allocation | 盒指针/分配 | Heap 中 T 的唯一 owner 与承载 T 的存储 |
| `Rc<T>` / `Arc<T>` | 引用计数/原子引用计数 | 单线程和可跨线程的共享 ownership handle |
| Strong count | 强引用计数 | 决定共享 pointee 是否继续存活的 owner 数量 |
| `Weak<T>` / upgrade | 弱引用/升级 | 不保活的观察 handle，以及尝试取得临时 strong owner |
| Reference cycle | 引用环 | Strong ownership 形成闭环导致对象无法释放 |
| `Deref` / `DerefMut` | 解引用/可变解引用 | 向 target 暴露共享或可变 reference 的 Trait |
| Deref coercion | 解引用自动调整 | 编译器在引用参数和方法查找中沿 Deref 调整类型 |
| `Drop` / destructor | 析构/析构器 | Value 生命周期结束时执行的同步清理逻辑 |
| RAII guard | 资源获取即初始化守卫 | 以 ownership scope 自动归还配额、锁或临时状态 |
| Unwind / abort | 栈展开/立即终止 | 逐层运行析构的 panic 过程与跳过常规清理的终止 |
| `Pin<P>` | 固定指针包装 | 限制 safe code 移动 pointee 的地址稳定性合同 |
| Heap/stack pin | 堆/栈固定 | Box::pin 的 owned 长期固定与 pin macro 的当前 scope 固定 |
| `Unpin` | 可解除固定约束 | 表示类型不依赖固定地址、仍可安全普通移动的 auto Trait |
| Self-reference / projection | 自引用/投影 | 内部指向自身状态，以及从 pinned outer value 访问字段 |
| Poll / Waker | 轮询/唤醒器 | 增量推进 Future/Stream，以及就绪后请求再次调度 |
| Boxed Future / type erasure | 装箱 Future/类型擦除 | 用 Pin、Box 和 dyn 统一不同 async state machine 的 owned shape |

## 63. Rust Future、Poll、Waker、Async 状态机与取消

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Future / Output | 未来值/输出 | 惰性的单次计算状态机与最终结果类型 |
| Poll / Ready / Pending | 轮询/就绪/待定 | Executor 的推进尝试及完成、暂停结果 |
| Context / Waker | 上下文/唤醒器 | Poll 环境与请求 task 再次进入 runnable queue 的能力 |
| Lost wakeup / spurious poll | 丢失唤醒/假性轮询 | 注册竞态导致停顿，以及条件未变却再次 poll |
| Executor / reactor | 执行器/反应器 | 调度并 poll tasks，以及监听 I/O/timer 就绪的 runtime 部分 |
| Task / JoinHandle | 任务/连接句柄 | 被独立调度的顶层 Future 及其完成和取消观察 handle |
| Suspension point | 暂停点 | Await 可能保存状态并返回 Pending 的位置 |
| Async state machine | 异步状态机 | 编译器生成的程序位置与跨 await locals 容器 |
| Cooperative scheduling | 协作式调度 | Task 在 await、yield 或 Pending 时主动交回 worker |
| Cancellation safety | 取消安全 | Future 在任意暂停点被 drop 后仍保持外部不变量 |
| `select!` / terminal winner | 选择竞争/终态胜者 | 竞争多个 branch 并决定唯一最终结果的逻辑 |
| Stream / poll_next | 异步流/轮询下一项 | 可多次产生 Item、以 None 表示结束的状态机 |
| Backpressure | 背压 | 下游容量耗尽后 producer 等待 Waker 而暂停 |
| `poll_fn` / AtomicWaker | 轮询函数/原子唤醒器 | 用 closure 构建 Future，以及并发注册和触发 Waker |
| Join / FuturesUnordered | 全部等待/无序 Future 集合 | 等待一组完成，以及按完成顺序驱动动态集合 |
| `buffer_unordered` / in-flight | 无序缓冲/进行中 | 限制并发驱动数量并按完成顺序输出 |
| Timeout / deadline | 超时/截止点 | Timer 竞争 inner Future，以及跨层总时间预算 |

## 64. Rust Unit、Integration、Mock、Snapshot 与 Property Test

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Test case / oracle | 测试案例/判定依据 | 给定前提和动作，以及判断结果正确性的事实来源 |
| Arrange/Act/Assert | 准备/执行/断言 | 测试故事的环境建立、行为触发与结果观察 |
| Unit/integration/E2E | 单元/集成/端到端 | 局部逻辑、公开组合边界和完整旅程测试 |
| Regression test | 回归测试 | 在旧错误实现上失败、固定历史缺陷的案例 |
| Fixture / harness | 夹具/工具架 | 可复用环境数据，以及驱动系统和收集结果的基础设施 |
| Mock/stub/fake/spy | 模拟/桩/假实现/记录器 | 预设返回、简化实现、交互预期和调用记录等 test doubles |
| Hermetic/deterministic | 封闭/确定性 | 不依赖外部机器状态并能稳定重复结果 |
| Deep equality | 深度相等 | 比较整个对象而非容易遗漏的逐字段断言 |
| Snapshot / golden | 快照/金文件 | 保存复杂稳定预期并通过可审阅 diff 检查变化 |
| Property test / generator | 性质测试/生成器 | 对广泛生成输入验证普遍不变量 |
| Shrinking / counterexample | 缩减/反例 | 把失败输入最小化，以及证明性质不成立的具体值 |
| Seed | 随机种子 | 复现 property/random 测试输入序列的标识 |
| Flaky test | 不稳定测试 | 代码不变却受时间、顺序或环境影响偶发变化的测试 |
| Watchdog timeout | 看门狗超时 | 防止测试永久挂住的外界限，而不是完成信号 |
| Test isolation / teardown | 测试隔离/拆除 | 防止 case 相互污染并回收 task、文件与全局状态 |
| Wiremock / SSE fixture | 网络模拟器/SSE 夹具 | 本地捕获请求并返回受控模型事件流的协议替身 |
| Red/green | 红/绿 | 修复前按正确原因失败，以及修复后通过 |

## 65. Rust Tracing、日志、Span 与 OTEL 上下文

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Event / field / message | 事件/字段/消息 | 瞬时结构化事实、可查询键值和人类摘要 |
| Span / parent / child | 时间范围/父/子 | 一段工作及其嵌套因果关系 |
| Trace / trace ID / span ID | 追踪/追踪 ID/范围 ID | 跨组件操作树、整树身份和单节点身份 |
| Level / target / callsite | 级别/目标/调用点 | 诊断重要性、逻辑路由来源和静态宏 metadata |
| Subscriber / layer / filter | 订阅器/层/过滤器 | 接收、组合处理并选择 tracing 数据的组件 |
| Instrumentation | 插桩 | 将函数或 Future 的执行生命周期绑定到 Span |
| Ambient/explicit parent | 环境/显式父级 | 从当前 context 继承或直接指定因果父级 |
| `field::Empty` / record | 空字段/补录 | 预声明 field schema 并在值已知后写入 |
| Correlation ID | 关联 ID | 连接请求、thread、turn、call 与其他证据的稳定身份 |
| W3C trace context | W3C 追踪上下文 | traceparent/tracestate 跨进程传播格式 |
| Extract / inject | 提取/注入 | 从载体读取和向 header 写入 parent context |
| Sampling | 采样 | 选择保留哪些完整 trace 以控制成本 |
| Cardinality | 基数 | Field 或 metric label 的不同值数量 |
| OTLP / exporter | OTEL 协议/导出器 | 把 trace、log、metric 发送到收集系统 |
| Redaction / PII | 脱敏/个人信息 | 移除 secret 和可识别用户的数据 |
| Payload preview / hard cap | 载荷预览/硬上限 | 有界诊断摘要及不可超过的容量限制 |
| TTFT / TTFM | 首 token/首消息时间 | 从固定开始点到首次 token/message 的 duration |
| Flush / shutdown | 刷出/关闭 | 提交缓冲 telemetry 并有界终止 provider |

## 66. Rust Unsafe、FFI、原始指针与 OS Handle

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Unsafe block/operation | 不安全块/操作 | 局部执行需要程序员额外证明前提的操作 |
| Safety contract/invariant | 安全合同/不变量 | 调用前条件和对象生命周期内持续成立的事实 |
| Soundness / UB | 健全性/未定义行为 | Safe caller 不会触发 UB，以及违反语言前提后的无保证状态 |
| Raw pointer / pointee | 原始指针/被指向值 | 不携带完整 reference 保证的地址及其目标对象 |
| Null / dangling | 空/悬垂 | 不指向对象的 sentinel 和目标已失效的 pointer |
| Alignment / initialization | 对齐/初始化 | 地址边界要求和存储中是否已有合法 T |
| Provenance / aliasing | 来源权/别名 | Pointer 合法来源和同一内存多条访问路径的规则 |
| FFI / ABI | 外部函数接口/二进制接口 | 跨语言调用边界与机器级调用、布局合同 |
| `extern "C"` / `repr(C)` | C ABI/C 表示 | 固定函数调用约定和数据布局的标记 |
| `CString` / `CStr` / NUL | C 字符串/借用 C 字符串/零字节 | Owned/borrowed 的 NUL-terminated byte string |
| fd / HANDLE | 文件描述符/Windows 句柄 | 操作系统资源的底层进程标识 |
| `as_raw_*` / `into_raw_*` / `from_raw_*` | 查看/交出/接管原始资源 | 借用、转移和建立资源 ownership 的三类 API |
| RAII / allocator pairing | 自动资源管理/分配器配对 | 用 Drop 清理并调用创建方指定的释放函数 |
| Double close / leak | 重复关闭/泄漏 | 多个 owner 清理同一资源和资源无人清理 |
| Checked arithmetic | 检查式算术 | 构造 raw buffer view 前拒绝 offset/length overflow |
| `pre_exec` / async-signal-safe | 执行前钩子/异步信号安全 | Unix fork 后、exec 前的受限执行环境及允许操作性质 |
| Miri / sanitizer / fuzzing | MIR 解释器/检测器/模糊测试 | 检查 unsafe 语义、原生内存错误和边界输入的互补工具 |

## 67. Rust 宏系统、声明宏、过程宏与 Derive

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Macro / expansion | 宏/展开 | 编译期 token 变换和生成出的普通 Rust 代码 |
| Invocation / callsite | 调用/调用点 | 宏或 attribute 写在源码中的位置 |
| Declarative macro | 声明宏 | 使用 macro_rules pattern/transcriber 的 token 变换 |
| Matcher / transcriber | 匹配器/转写模板 | Macro arm 的输入规则和输出代码模板 |
| Metavariable / fragment | 元变量/片段 | `$name` 捕获的 ident、expr、ty、tt 等语法类别 |
| Token tree / `tt` | Token 树 | 单个 token 或括号包围的嵌套 token 集合 |
| Repetition / separator | 重复/分隔符 | `$()*`、`$()+`、`$()?` 及其逗号等分隔 |
| DSL | 领域专用语言 | Macro grammar 为协议 entry 定义的紧凑声明格式 |
| Hygiene / `$crate` | 卫生/定义 crate | 减少名字捕获并稳定引用 macro provider 的机制 |
| Built-in macro | 内建宏 | include_str、concat、env 等工具链宏 |
| Derive/helper attribute | 派生/辅助属性 | 为类型生成 impl 的过程宏及其 serde/experimental 配置 |
| Attribute macro | 属性宏 | 接收并改写完整 item 的过程宏 |
| TokenStream / Span | Token 流/源码范围 | Proc macro 输入输出及用于诊断定位的来源信息 |
| `syn` / `quote!` | 语法解析/Token 生成 | Proc macro 读取 AST 和构造输出代码的常用库 |
| No-op derive | 空操作派生 | 接受 helper annotations 但不生成实现的过程宏 |
| Compile environment | 编译环境 | env!/option_env! 在 callsite 构建时读取的变量 |
| `cargo expand` | Cargo 展开工具 | 查看特定 feature/target 下 generated Rust 的工具 |

## 68. Rust 条件编译、Cfg、Cargo Feature 与 Target

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Conditional compilation | 条件编译 | 按构建配置决定某段代码是否存在 |
| `cfg` / predicate | 配置/谓词 | 条件 attribute 与 target/feature/test 等事实 |
| `cfg!` / `cfg_attr` | 配置布尔/条件属性 | 生成常量 bool 和有条件应用 attribute 的机制 |
| Host / target | 构建主机/目标平台 | 执行工具的机器和制品准备运行的平台 |
| Target triple | 目标三元组 | Architecture、vendor、OS、environment 的组合标识 |
| Target family/OS/arch/env | 平台族/系统/架构/环境 | 不同粒度的 rustc 内建 target predicates |
| Cross-compilation | 交叉编译 | Host 与 target 不相同的构建方式 |
| Platform facade | 平台门面 | 对上层提供同签名 API、内部选择 OS 实现 |
| Cargo feature | Cargo 功能开关 | Package 编译期的 additive capability selection |
| Feature forwarding/unification | 转发/合并 | 启用 dependency feature 及全图 feature 请求取并集 |
| Runtime feature flag | 运行时功能开关 | 同一制品内按配置或账户选择行为的机制 |
| Build script | 构建脚本 | 编译 crate 前在 host 运行的 build.rs 程序 |
| Custom cfg / capability probe | 自定义配置/能力探测 | 构建系统验证能力后提供给 Rust 源码的 predicate |
| `rustc-check-cfg` / `rustc-cfg` | 检查/设置配置 | 注册合法 cfg 与实际将它设为 true 的构建指令 |
| `rerun-if-*` / OUT_DIR | 重跑条件/输出目录 | Build script 输入声明和生成文件的受控位置 |
| Target-specific dependency | 目标专用依赖 | 只在 Cargo target predicate 匹配时加入的 dependency |
| Configuration matrix | 配置矩阵 | Test、profile、feature、target 和构建系统的支持组合 |

## 69. Rust 编译错误、所有权、生命周期与 Trait 诊断

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Diagnostic/error code | 诊断/错误代码 | Compiler 的约束冲突报告及其查询编号 |
| Primary/secondary span | 主/次范围 | 冲突点和 bound/move/borrow 来源位置 |
| Expected/found | 期望/实际 | API 要求与当前 expression 能提供的类型事实 |
| Bound origin | 约束来源 | 引入 Trait、lifetime、Send 或 Output 要求的签名 |
| Error cascade | 错误级联 | 一个上游错误引发的多条后续诊断 |
| Inference/annotation | 推断/标注 | 从上下文确定类型和在歧义边界指定类型 |
| Move/partial move | 移动/部分移动 | Ownership 转移及只转移部分 fields |
| Borrow conflict/NLL | 借用冲突/非词法生命周期 | 重叠访问限制和按最后使用缩短 borrow |
| Borrowed data escapes | 借用逃逸 | Reference 被长期 queue、closure 或 Future 保存 |
| `'static` bound | 静态边界 | Value 不含受短 lifetime 限制的 borrow |
| Send/Sync | 可移动/可共享 | 跨线程 ownership 和 shared reference 合同 |
| Future not Send | Future 不可发送 | 跨 await state 中包含 non-Send value |
| Dyn compatibility | 动态兼容性 | Trait 能否形成 `dyn Trait` object 的规则 |
| Type erasure/BoxFuture | 类型擦除/装箱 Future | 统一不同 concrete Future 实现的动态 shape |
| Associated/opaque type | 关联/不透明类型 | Trait 命名类型成员和 impl Trait 隐藏的 concrete type |
| Nested Result/residual | 嵌套结果/剩余值 | 多层失败结构与 `?` 传播的 Err/None 部分 |
| Error conversion | 错误转换 | From/map_err/custom 将 source error 投影到当前合同 |
| Exhaustive match | 穷尽匹配 | 每个 enum state 必须有明确处理分支 |
| Minimal reproduction | 最小复现 | 保留触发错误所需的最小类型与数据流 |

## 70. App-server 代码生成、TypeScript、JSON Schema 与协议同步

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Code generation / codegen | 代码生成 | 从 Rust 协议类型和属性自动产生跨语言合同文件 |
| Source of truth | 事实源 | 发生冲突时应以其为准的协议定义与 wire 属性 |
| Generated artifact | 生成制品 | 可由事实源确定性重建的 TS、JSON 和压缩文件 |
| Wire contract | 线上合同 | Client/server 实际交换的 JSON 名称、形状与语义 |
| Runtime serialization | 运行时序列化 | Serde 在真正请求和响应中进行的编码/解码 |
| TypeScript declaration | TypeScript 声明 | 面向 TS consumer 的静态类型投影 |
| JSON Schema / bundle | JSON 模式/聚合包 | 机器可读的 JSON 结构约束及其完整定义集合 |
| Envelope / discriminator | 外层消息/判别字段 | RPC transport 外壳和选择 union variant 的 tag |
| Optional / nullable | 可省略/可为空 | Key 可以缺席和值可以是 JSON null 两种不同合同 |
| Stable / experimental API | 稳定/实验接口 | 默认承担兼容承诺和需要显式 opt-in 的协议集合 |
| Fixture | 固定样本 | 提交在仓库中供重新生成结果比较的预期文件树 |
| Drift / stale file | 漂移/陈旧文件 | 源码与生成物不同步，或已删除类型的旧文件仍残留 |
| Precomputed exports | 预计算导出 | 真实生成器预先创建、供 production 还原的文件快照 |
| Zstandard / `.zst` | Zstandard 压缩 | 存放稳定与实验预计算导出的压缩格式 |
| Deterministic generation | 确定性生成 | 相同 source/tool options 得到相同路径、内容和顺序 |
| Canonicalization | 规范化 | 消除 JSON key order 等不影响协议含义的差异 |
| Type visitor/import closure | 类型遍历/导入闭包 | 沿入口收集全部依赖并保证 import 都有对应输出 |
| Path traversal | 路径穿越 | 绝对路径或 `..` 使导出写到目标根之外的风险 |
| No-op derive | 空操作派生 | Production 接受 schema 属性但不生成真实实现的宏 |
| Breaking change | 破坏性变更 | 让既有合法新旧 client/server 组合不再工作的变化 |
| Forward/backward compatibility | 前向/后向兼容 | 新旧一方能否继续理解另一代协议数据 |

## 71. App-server 协议版本演进、前后兼容与迁移策略

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Protocol evolution | 协议演进 | 已有 consumer 使用的 wire contract 随时间变化 |
| Backward compatibility | 向后兼容 | 新 reader/server 继续理解旧输入与旧数据 |
| Forward compatibility | 向前兼容 | 旧 reader 能安全面对未来扩展数据 |
| Reader/writer | 读取者/写入者 | 解析 JSON 和产生 JSON 的协议两端角色 |
| Client version/API generation | 客户端版本/API 代际 | 软件版本字符串与 v1/v2 设计阶段两个不同概念 |
| Initialize handshake | 初始化握手 | 建立 connection identity/capabilities 的 request/notification 序列 |
| Capability negotiation | 能力协商 | 对端显式声明支持或选择的具体功能 |
| Per-connection gate | 每连接门控 | Experimental/notification 等选择只作用于当前连接 |
| Safe default | 安全默认值 | 旧输入缺字段时恢复历史且不扩大能力的行为 |
| Additive change | 加法变更 | 保留旧 surface 并新增 method、field 或 variant |
| Alias/canonical writer | 别名/规范写入 | 宽读历史名字、窄写唯一新格式的迁移方式 |
| Compatibility projection | 兼容投影 | 从新 canonical state 计算旧协议视图 |
| Input bridge/precedence | 输入桥/优先级 | 旧输入映射和新旧字段冲突时的明确规则 |
| Deprecation notice | 弃用通知 | 旧功能仍工作时发给调用者的迁移提示 |
| Sunset/tombstone | 终止支持/墓碑 | 移除旧能力的期限和防止旧 wire 名被复用的记录 |
| Open/closed enum | 开放/封闭枚举 | Consumer 是否必须容忍未来新增 variant |
| Persisted-data compatibility | 持久化兼容 | 新 server 能否读取旧 rollout/thread metadata |
| Projection/migration | 投影/迁移 | 读取时转换视图和永久改写存储两种策略 |
| Lazy/eager migration | 惰性/预先迁移 | 按需转换单项和升级时批量转换 |
| Read-old/write-new | 读旧写新 | Reader 保留历史输入，writer 只产生当前格式 |
| Downgrade support | 降级支持 | 旧 binary 能否读取新版本已写出的数据 |
| Fail open/fail closed | 开放失败/封闭失败 | 异常后继续宽松行为或拒绝/采用保守行为 |
| Golden old payload | 黄金旧样本 | 代表真实历史 shape 的固定 regression JSON |
| Four-quadrant matrix | 四象限矩阵 | 旧/新 client 和旧/新 server 的四种连接组合 |

## 72. App-server JSON-RPC、请求响应、通知、ID 与双向调用生命周期

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| JSON-RPC envelope | JSON-RPC外壳 | 用id/method/params/result/error区分消息角色 |
| Request/response/error | 请求/响应/错误 | 期待且必须用同一ID终结的调用链 |
| Notification | 通知 | 没有ID、不期待response的单向事件 |
| Framing/JSONL | 分帧/JSON行 | 换行或WebSocket frame提供消息边界 |
| RequestId/correlation | 请求ID/对位 | String或i64 key把结果交回原请求 |
| ConnectionId | 连接ID | 进程内部识别一条transport connection |
| ConnectionRequestId | 连接请求ID | ConnectionId与client RequestId组成的唯一key |
| Namespace | 命名空间 | 相同ID在不同连接或调用方向中的隔离范围 |
| Untagged/tagged enum | 无标签/有标签枚举 | 按字段shape辨认envelope和按method选择typed variant |
| RequestContext | 请求上下文 | 保存request identity、span与parent trace |
| ToConnection/Broadcast | 定向连接/广播 | Outgoing message的内部routing意图 |
| Server-initiated request | 服务端主动请求 | App-server向client询问审批或用户输入 |
| Pending callback/oneshot | 待定回调/单次通道 | ID到一次性异步完成sender的映射 |
| First responder wins | 首答胜出 | 多client中第一份response终结共享callback |
| Replay/resolved | 重放/已解决 | 新subscriber看见pending request及所有UI关闭它 |
| Unmatched response | 无匹配响应 | ID已取消、完成或错误，找不到等待callback |
| Turn-transition cancellation | Turn转换取消 | 状态改变使旧turn server request失效 |
| Backpressure/overload | 背压/过载 | 有界queue和-32001重试信号 |
| Enqueued/written/applied | 入队/写出/应用 | 消息交付可靠性的三个不同阶段 |
| Idempotency ID | 幂等ID | 防止业务重复副作用，不等同RPC correlation ID |

## 73. App-server 请求并发、Serialization Scope、队列、公平性与竞态

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Serialization scope | 串行化范围 | 定义请求共享执行顺序的资源冲突域 |
| Queue key/access mode | 队列键/访问模式 | 具体资源identity及Exclusive/SharedRead |
| FIFO/VecDeque | 先进先出/双端队列 | 同key按enqueue顺序push_back/pop_front |
| Drainer/batch | 排空器/批次 | 每key唯一task与一次运行的请求组 |
| Exclusive/SharedRead | 独占/共享读 | 单项串行和连续只读请求并发 |
| Writer fairness | 写者公平 | 已排队write不会被后来reads无限插队 |
| Starvation | 饥饿 | 请求因流量持续插队而长期无法执行 |
| Head-of-line blocking | 队首阻塞 | 慢队首使同key后续请求等待 |
| Logical race/data race | 逻辑竞态/数据竞争 | 业务交错错误与非法并发内存访问 |
| Lost update | 更新丢失 | 并发读改写互相覆盖结果 |
| Transaction boundary | 事务边界 | 必须作为不可穿插整体处理的范围 |
| Connection-local key | 连接局部Key | Process/watch identity与ConnectionId组合 |
| ConnectionRpcGate | 连接RPC门 | Close后不再启动尚未开始的handler |
| TaskTracker/inflight | 任务追踪/执行中 | 统计已通过gate但尚未完成的handler |
| Graceful drain | 优雅排空 | 停止新工作并等待inflight完成 |
| Background serialization | 后台串行化 | App-owned mutation也进入RPC使用的同一queue |
| Boxed future | 装箱Future | 擦除handler Future类型后存入异构队列 |
| Key granularity | Key粒度 | 过粗损失并发，过细暴露逻辑竞态 |
| Linearizability | 线性一致性 | 操作仿佛在某个瞬间原子完成 |
| Queue wait | 队列等待 | Enqueue到handler真正开始之间的延迟 |

## 74. App-server Thread、Turn、Item 状态机、快照、事件流与恢复一致性

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Thread | 线程/任务线 | 可持久化、恢复、派生与订阅的对话容器，不是OS thread |
| Turn | 一轮 | 一次用户输入到completed/interrupted/failed的工作轮次 |
| ThreadItem | Thread条目 | Turn中的消息、命令、文件修改、工具调用等tagged variant |
| State machine | 状态机 | 有限状态、事件和合法转换组成的行为合同 |
| Terminal state | 终态 | 不应被旧事件降级回InProgress的完成/中断/失败状态 |
| ThreadStatus | Thread状态 | NotLoaded、Idle、SystemError或带activeFlags的Active运行时视图 |
| TurnStatus | Turn状态 | InProgress、Completed、Interrupted、Failed |
| Snapshot/stream | 快照/事件流 | 某时点物化状态与该时点以后连续到达的通知 |
| Delta | 增量片段 | 用于实时渲染的文字或输出片段，不是最终权威内容 |
| Authoritative completed item | 权威完成条目 | 按相同item ID替换临时状态的item/completed payload |
| TurnItemsView | Turn条目视图 | 声明items是NotLoaded、Summary还是Full |
| Empty vs absent | 空与未取得 | 空列表可能表示没有加载，不能自动解释为确认无数据 |
| Subscription | 订阅 | Connection登记接收某Thread后续通知 |
| Resume/rejoin | 恢复/重新加入 | 获取历史与active snapshot并原子接入后续事件 |
| Atomic snapshot+subscribe | 原子快照加订阅 | 快照响应和订阅相对per-thread listener不可被事件插入空窗 |
| Thread listener | Thread监听器 | 串行处理conversation events和listener commands的async loop |
| Active turn snapshot | 活跃Turn快照 | ThreadHistoryBuilder在内存中物化的当前轮次状态 |
| Reducer | 归约器 | 用旧状态和一个事件计算新状态的共享逻辑 |
| ThreadHistoryBuilder | Thread历史构建器 | 同时服务rollout恢复与运行时active Turn跟踪的reducer |
| Projection | 投影 | 把内部canonical event/item映射成API Turn、Item或change set |
| Canonical rollout | 规范执行记录 | 用于持久化和可靠恢复最终语义的有序记录 |
| Change set | 变更集 | 一次或一批append改变的Turns与Items的物化快照 |
| Coalescing | 合并压缩 | 批内同一对象多次变化只输出最新物化结果 |
| Upsert | 插入或更新 | 按ID存在则替换，不存在则新增的幂等合并 |
| Monotonic terminal state | 单调终态 | terminal状态不会被迟到started/delta倒退 |
| Transient buffer | 临时缓冲 | completed到来前暂存delta的有界客户端状态 |
| Pending request replay | 待处理请求重放 | 新subscriber重新收到尚未解决的审批/用户输入请求 |
| Eventual consistency | 最终一致性 | 临时UI可以不完整，最终向completed item和恢复快照收敛 |
| Persistence lag | 持久化延迟 | live内存事件到durable rollout append之间的间隔 |
| Stale InProgress | 陈旧进行中 | 崩溃或缺终结记录后历史仍显示运行、但runtime已不活跃 |
| Opaque cursor | 不透明游标 | 客户端只能原样回传、不可推断内部结构的分页token |
| Optimistic UI | 乐观界面 | 服务端确认前显示可撤销的本地pending对象 |
| Derived state | 派生状态 | 从完整协议状态计算而非重复存储的isBusy等UI值 |

## 75. App-server 审批状态机、Server Request、用户输入与多客户端协作

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Approval | 审批 | 用户决定是否允许命令、文件修改或额外权限 |
| Server Request | 服务端请求 | App-server主动发送、要求client响应的JSON-RPC request |
| Pending callback | 待处理回调 | requestId到oneshot sender、threadId和原始request的映射 |
| Business/RPC identity | 业务/RPC身份 | thread/turn/item ID定位对象，requestId对位一次问答 |
| approvalId | 审批ID | 一个父command Item内区分特定subcommand callback的可选ID |
| First responder wins | 首答胜出 | 多client第一份response从共享map原子remove callback |
| Replay | 重放 | 用原requestId把尚未解决的请求发送给新subscriber |
| serverRequest/resolved | 服务端请求已解决 | 通知各Thread订阅UI关闭已回答或已清理的交互 |
| Unmatched response | 无匹配响应 | callback已完成/取消，或ID错误的迟到response |
| oneshot | 单次通道 | 最多交付一次client response结果的异步channel |
| Accept | 接受 | 只批准当前操作 |
| AcceptForSession | 本会话接受 | 当前批准并让匹配后续请求使用session approval cache |
| Decline | 拒绝 | 不执行当前操作，但Turn可以继续 |
| Cancel | 取消 | 拒绝操作并中断当前Turn |
| Execpolicy amendment | 执行策略修订 | 为未来匹配命令增加明确允许策略 |
| Network policy amendment | 网络策略修订 | 对目标host持久应用allow或deny规则 |
| availableDecisions | 可用决定 | Server允许client为当前提示展示的决策集合 |
| request_user_input | 请求用户输入 | Agent通过结构化问题暂停并等待用户答案 |
| isBlocking/isSecret | 阻塞/秘密 | 请求是否阻止推进，以及答案是否必须敏感处理 |
| Elicitation | 引导取值 | MCP server向用户索取表单内容或URL操作 |
| Permission profile | 权限配置 | 请求或授予的网络和文件系统能力集合 |
| Permission grant scope | 权限授权作用域 | grant只持续当前Turn或整个Session |
| Intersection | 交集 | 最终权限只保留requested与granted共同部分 |
| Least privilege | 最小权限 | 不授予超出当前需求的能力 |
| Fail closed | 封闭失败 | malformed/error时默认拒绝、空权限或失败 |
| ThreadWatchActiveGuard | Thread活跃守卫 | 创建时增加等待计数、drop时异步减少的RAII对象 |
| WaitingOnApproval | 等待审批 | permission counter大于零产生的Thread active flag |
| WaitingOnUserInput | 等待用户输入 | user-input counter大于零产生的Thread active flag |
| turnTransition | Turn转换 | 新Turn、完成或中断使旧pending request失效的结构化原因 |
| Confused deputy | 糊涂代理 | 未授权client借高权限server执行敏感操作的风险 |
| Pending age | 等待时长 | Server Request从登记到response/cancel的时间 |

## 76. App-server Sandbox、权限配置、审批策略与命令执行安全链路

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Sandbox | 沙箱 | 用OS机制限制子进程文件、网络等访问的实际执行边界 |
| Approval policy | 审批策略 | 决定动作直接运行、允许询问或直接拒绝，不定义OS边界 |
| Approvals reviewer | 审批者 | 用户或auto-review；只改变谁作决定 |
| Permission profile | 权限配置 | 可复用的文件系统、网络与平台权限集合 |
| ActivePermissionProfile | 活跃权限配置 | 当前Thread所选profile的ID和extends来源 |
| SandboxMode | 沙箱模式 | thread/start使用的三档legacy简化输入 |
| SandboxPolicy | 沙箱策略 | turn/start可带writable roots/network的legacy详细输入 |
| SandboxType | 沙箱类型 | 真正执行时选择的None、macOS、Linux或Windows后端 |
| Enforcement | 强制执行 | 把声明规则落实到真实子进程，而非仅靠提示遵守 |
| Sticky override | 粘性覆盖 | 从当前Turn开始继续影响同Thread后续Turns的设置 |
| Writable root | 可写根 | 允许写入的根路径以及仍只读的子路径/保护元数据 |
| Protected metadata | 受保护元数据 | `.git`、`.codex`等会影响未来可信执行的位置 |
| Deny carve-out | 拒绝挖空 | 在宽read/write范围内用更具体deny收窄权限 |
| Runtime workspace roots | 运行时工作区根 | 当前Thread用于权限展开的绝对项目根集合 |
| UnlessTrusted/untrusted | 除非可信/不可信模式 | 只自动执行可信集合，其他命令需审批 |
| OnRequest | 按需询问 | 默认在Sandbox内运行，需要越界时可请求审批 |
| Never | 从不询问 | 不弹审批；越界动作失败，不是永远批准 |
| Granular policy | 细粒度策略 | 分别控制Sandbox、rules、skill、permissions和MCP提示 |
| ExecApprovalRequirement | 执行审批要求 | Core计算出的Skip、Forbidden或NeedsApproval |
| Additional permissions | 附加权限 | 单命令申请的有限文件/网络overlay |
| require_escalated | 请求升级 | 单命令请求无Sandbox执行的高风险路径 |
| Effective profile | 有效配置 | 继承、workspace、grant和overlay解析后的最终权限 |
| Managed network | 受管网络 | 由策略代理控制的出站网络路径 |
| NetworkApprovalContext | 网络审批上下文 | 用host/protocol描述被阻断网络目标的专用提示 |
| SandboxManager | 沙箱管理器 | 判断需求、选择平台后端并转换执行请求的组件 |
| SandboxTransformRequest | 沙箱转换请求 | command、profile、cwd、network与平台选项的转换输入 |
| SandboxExecRequest | 沙箱执行请求 | 已准备平台wrapper、即将进入主机启动边界的数据 |
| Seatbelt | 安全带 | macOS平台Sandbox机制 |
| LinuxSeccomp | Linux安全计算 | 固定提交中的Linux SandboxType名称 |
| Restricted token | 受限令牌 | Windows限制子进程能力的一类机制 |
| Wrapper | 包装程序 | 套在真实命令外并施加平台Sandbox策略的helper |
| Spawn | 启动子进程 | 用最终argv、env、cwd和stdio真正创建进程 |
| env_clear | 清空环境 | 先移除继承环境、再加入准备好的子进程环境 |
| Sandbox denial | 沙箱拒绝 | 真实执行触碰文件或网络边界产生的结构化失败 |
| Escalation retry | 升级重试 | 经独立允许后以更高或附加权限再次运行 |
| External sandbox | 外部沙箱 | 由容器/宿主承担隔离的部署合同 |
| Wire/decision/enforcement | 线协议/决策/强制 | 权限功能必须分别验证的三层 |
| Fail closed | 封闭失败 | 解析、审批或执行异常时拒绝而不放宽权限 |

## 77. App-server 命令执行、进程会话、流式输出、PTY 与清理

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| argv | 参数向量 | 已分词program/arguments，不保留shell pipe与redirect语义 |
| Shell string | Shell字符串 | 由shell解释的一整条命令，可包含quote、pipe和redirect |
| commandExecution Item | 命令执行条目 | Thread/Turn中展示命令开始、输出和完成的业务对象 |
| command/exec | 独立命令执行 | 不建Thread、受server Sandbox限制、退出后响应的API |
| process/spawn | 进程启动 | 无Codex Sandbox、启动后立即响应的实验性进程控制API |
| thread/shellCommand | Thread Shell命令 | 用户发起、无Sandbox且进入标准Turn/Item事件流的入口 |
| Unified exec | 统一执行 | Core统一管理本地/远程和短命令/后台PTY的机制 |
| Deferred response | 延迟响应 | 请求被受理后等进程退出与输出收口才发送response |
| processId/processHandle | 进程ID/句柄 | 不同API用于后续控制与输出对位的connection-scoped身份 |
| PTY | Pseudo-terminal | 模拟交互终端、支持尺寸与终端程序行为的伪终端 |
| Pipe | 管道 | 连接父子进程标准字节流的非终端通道 |
| stdin/stdout/stderr | 标准输入/输出/错误 | 进程的三类标准字节流 |
| Streaming/buffered | 流式/缓冲 | 运行中分块通知与结束时集中返回两种输出模式 |
| Delta | 增量块 | 一段输出bytes，不保证是完整行或UTF-8字符 |
| Base64 | Base64编码 | 让任意进程bytes安全通过JSON字符串的编码 |
| Output cap | 输出上限 | 单stream最多保留或发送的bytes数量 |
| capReached | 达到上限 | 说明该stream后续输出被截断的终态标志 |
| Yield time | 让出时间 | Agent等待初始输出后将活跃process ID返回模型的窗口 |
| Timeout/expiration | 超时/到期 | 到达期限或取消信号后请求终止进程 |
| Exit code | 退出码 | 进程业务结果，不是JSON-RPC错误码 |
| Spawn failure | 生成失败 | program未成功创建为运行进程时的错误 |
| IO drain | IO排空 | 进程退出后读取pipe剩余bytes的有界阶段 |
| Control channel | 控制通道 | 将write/resize/terminate串行送到process owner的mpsc |
| Connection-scoped | 连接作用域 | 相同process名字在不同连接隔离，不能跨连接控制 |
| HeadTailBuffer | 头尾缓冲 | 有界保存长输出开始与结尾的结构 |
| Background terminal | 后台终端 | yield后继续存活并由Thread Session持有的进程 |
| Interaction lock | 交互锁 | 协调stdin、事件发布与进程表清理的互斥边界 |
| process/exited | 进程退出 | process/spawn的权威终结notification |
| Terminal signal | 终结信号 | final response、process/exited或item/completed等结束合同 |
| Backpressure | 背压 | 下游慢导致上游等待、排队或丢实时观察数据 |
| kill-on-drop | 丢弃即终止 | owner意外释放时终止进程的资源泄漏保险 |

## 78. App-server Environment、本地与远程执行、路径、文件系统与能力边界

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Environment | 环境 | 绑定执行、文件系统、HTTP、shell、连接与能力的一整套资源后端 |
| Environment ID | 环境标识 | Manager注册和Turn选择逻辑工作台的稳定字符串 |
| EnvironmentManager | 环境管理器 | 保存默认环境、ID映射、本地环境和创建/状态策略的共享注册表 |
| local | 本地 | App-server所在主机的保留环境ID |
| remote | 远程 | 通过exec-server连接提供执行和文件能力的环境 |
| Configured/selected/ready | 已配置/已选择/已就绪 | Manager知道、Turn附加、异步启动成功这三个不同阶段 |
| Primary environment | 主环境 | 有序Turn snapshot中第一个Ready环境，工具省略ID时的默认目标 |
| TurnEnvironmentParams | Turn环境参数 | wire上的environmentId、目标原生cwd和workspace roots |
| TurnEnvironment | Turn环境 | Ready的Environment handle加cwd、roots、shell和权限配置 |
| Sticky environment | 粘性环境 | Turn更新后继续影响同Thread后续Turns的环境选择 |
| Environment selection三态 | 环境选择三态 | 省略=沿用/默认，空数组=禁用，非空=第一项为current |
| cwd | Current working directory | 所选环境中命令与相对路径的基准目录 |
| Runtime workspace roots | 运行时工作区根 | 每个环境中用于项目范围与权限展开的绝对根目录 |
| Legacy fallback cwd | 旧兼容回退目录 | 仍要求本机PathBuf的未迁移消费者使用的兼容值 |
| LegacyAppPathString | 旧App路径字符串 | API边界保留POSIX或Windows原生拼写的path newtype |
| PathUri | 路径URI | 只接受file scheme、可跨宿主校验和词法操作的不可变路径类型 |
| PathConvention | 路径约定 | POSIX或Windows路径语法，不等于具体Environment身份 |
| Host-native/target-native | 宿主原生/目标原生 | 分别符合App-server主机和所选执行环境的路径语法 |
| Foreign path | 外来路径 | 对当前host非原生、但对远程target可能原生的路径 |
| POSIX path | POSIX路径 | 如`/workspace/repo`、用斜杠表示根与segments的路径 |
| Windows drive path | Windows盘符路径 | 如`C:\repo`的Windows绝对路径 |
| UNC path | 通用命名约定路径 | 如`\\server\share`的Windows网络共享路径 |
| file URI | 文件URI | 如`file:///repo`或`file:///C:/repo`的结构化文件资源表示 |
| ExecBackend | 执行后端 | local/remote统一启动和控制命令进程的trait抽象 |
| ExecutorFileSystem | 执行器文件系统 | local/remote统一读写目标环境文件的trait抽象 |
| Exec-server | 执行服务 | 在目标机器处理process、fs、HTTP和environment协议的服务 |
| Provisioning | 置备 | 准备远程工作机和能力材料的阶段 |
| Connecting | 连接中 | 建立并初始化App-server到exec-server通道的阶段 |
| Starting/Pending | 启动中/待定 | 环境已选中或配置、但初始准备尚未完成 |
| Disconnected | 已断开 | 已观察连接失败，但后续正常使用可能恢复 |
| Unknown | 未知 | Manager没有该environment ID |
| environment/info | 环境信息 | 获取目标shell、默认cwd和exec-server capabilities的请求 |
| environment/status | 环境状态 | 不触发启动或恢复的Ready/Pending/Disconnected/Unknown观察 |
| Fail-fast probe | 快速失败探测 | 仅用现有连接检查状态，不等待、不启动、不重连 |
| EnvironmentCapabilities | 环境能力 | 远端显式声明支持的可选协议和执行特性 |
| Capability gate | 能力门控 | 只有远端声明支持才发送新字段或启用新行为 |
| Capability root | 能力根 | 绑定特定环境的skill/plugin等可发现资源根 |
| Version skew | 版本偏差 | App-server与exec-server版本不一致产生的兼容场景 |
| ArcSwap snapshot | 原子共享快照 | 读者保留稳定旧selection、更新者整体发布新列表的机制 |
| Reconnect | 重连 | 保持逻辑Environment身份、替换或恢复物理连接 |
| Fail closed | 封闭失败 | 路径、能力或授权不确定时拒绝，而不是猜测或回退其他环境 |

## 79. Exec-server 协议、传输、握手、Session 恢复、Noise 与版本偏差

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Exec-server | 执行服务 | 在目标机器处理process、fs、HTTP与environment RPC的服务 |
| Harness/executor | 控制端/执行端 | 发起远程控制的一侧与真正运行命令的一侧 |
| Wire protocol | 线协议 | 跨transport序列化后client/server共同遵守的消息合同 |
| JSON-RPC dialect | JSON-RPC方言 | 省略jsonrpc字段、仍区分request/notification/response/error的Codex格式 |
| RequestId | 请求标识 | 一次RPC问答的传输层对位身份 |
| Transport | 传输 | WebSocket、stdio或Noise rendezvous承载消息的通道 |
| Framing | 分帧 | 从连续bytes或frames识别一条完整JSON-RPC消息的规则 |
| JsonRpcConnection | JSON-RPC连接 | 将不同transport适配成消息channel、disconnect watch与owner的对象 |
| Physical connection | 物理连接 | 一次socket、WebSocket或stdio child的具体生命周期 |
| initialize/initialized | 初始化/已初始化 | 创建或恢复session的request与client完成确认notification |
| Session ID | 会话标识 | 跨断线重新attach同一managed process集合的逻辑身份 |
| Attach/detach | 挂接/脱离 | 某connection成为session owner或断开后暂时离开 |
| Detached TTL | 脱离存活期 | 断线session等待恢复、到期shutdown的30秒期限 |
| Resume/reconnect | 恢复/重连 | 重建物理通道并携带旧session ID恢复逻辑会话 |
| RecoveryPolicy | 恢复策略 | Wait允许等待恢复，FailFast只使用当前ready连接 |
| Recoverable process | 可恢复进程 | start已确认、可用process/read在重连后补状态的进程 |
| process/output | 进程输出 | 携带processId、seq、stream与base64 bytes的notification |
| process/exited/closed | 进程退出/关闭 | OS退出事件与输出/handle最终收口事件 |
| process/read | 进程读取 | 按afterSeq返回缓存输出和terminal facts的补偿接口 |
| Sequence number | 序号 | 对进程事件或relay record进行排序、去重和恢复的单调数字 |
| Reorder buffer | 重排缓冲 | 暂存未来seq直到缺口关闭的有界结构 |
| writeId | 写入标识 | 跨RPC重试不变、避免stdin重复side effect的业务ID |
| Idempotency | 幂等性 | 同一逻辑操作重复提交仍只产生一次预期作用 |
| Control lane | 控制通道 | 给status/signal/terminate/fs-close保留的并发容量 |
| Cleanup slot | 清理槽 | 常规RPC饱和时仍供终止和收尾使用的保留配额 |
| Bidirectional RPC | 双向RPC | client与server都可发Request并等待另一方Response |
| Pending callback | 待处理回调 | RequestId到oneshot sender的在途调用登记 |
| File read handle | 文件读取句柄 | fs/open创建、readBlock使用、close释放的connection-local身份 |
| HTTP body delta | HTTP正文增量 | streamed executor HTTP response按seq发送的bytes |
| Backpressure | 背压 | 有界消费者变慢时让上游等待、失败或断开的反馈 |
| Noise | Noise协议框架 | 在rendezvous上认证双方并加密JSON-RPC字节流的协议 |
| Rendezvous | 会合中继 | 按stream ID路由frame但看不到Noise plaintext的服务 |
| Registry bundle | 注册材料包 | URL、environment/registration ID、pinned key和authorization的原子组合 |
| Pinned key | 固定公钥 | Harness要求executor证明持有对应私钥的预信任key |
| Prologue | 前导绑定数据 | 将environment、registration与stream ID绑定进Noise transcript的材料 |
| Virtual stream | 虚拟流 | 一个physical relay WebSocket内独立Noise状态和JSON-RPC处理的逻辑连接 |
| Relay frame | 中继帧 | Protobuf data/ack/resume/reset/heartbeat/handshake消息 |
| Ciphertext/plaintext | 密文/明文 | 中继可见的加密bytes与端点内部解密后的协议bytes |
| Length prefix | 长度前缀 | Noise plaintext流中标记JSON message长度的4-byte整数 |
| Nonce | 一次性序数 | Noise按严格顺序加解密使用的隐式状态，错序会破坏通道 |
| ack/ack_bits | 累积/位图确认 | Relay描述连续及离散已收segments的可靠性字段 |
| Keepalive/Pong watchdog | 保活/Pong看门狗 | 周期Ping并在deadline内等待Pong的连接活性机制 |
| Version skew | 版本偏差 | App-server/harness与exec-server/executor来自不同Codex版本 |
| Wide read | 宽读取 | 用default和optional安全接受旧peer缺少的新字段 |
| Capability gate | 能力门控 | 只有远端声明支持才发送或启用新协议行为 |

## 80. App-server 配置 API、Layer Stack、来源、写入、Requirements 与运行时刷新

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Config layer | 配置层 | 一个独立配置来源及其原始内容、身份、版本和启用状态 |
| ConfigLayerStack | 配置层栈 | 从低到高保存普通配置层并计算有效值与来源的领域对象 |
| ConfigLayerSource | 配置层来源 | MDM、system、enterprise、user、project、session或legacy managed等结构化身份 |
| Precedence | 优先级 | 高层同路径值覆盖低层值的确定顺序 |
| Effective config | 有效配置 | 普通层合并、精确要求与运行时投影后实际采用的值 |
| Origin | 来源 | 一个最终叶子配置值由哪个普通层获胜提供 |
| Layer metadata | 层元数据 | 来源身份与内容版本，不包含整层业务配置 |
| Disabled layer | 禁用层 | 被发现并可向UI展示、但不参与effective/origin计算的层 |
| Thread-agnostic config | 与任务无关配置 | 没有cwd/项目根上下文、因而不加载仓库内.codex层的配置视图 |
| Active user layer | 活跃用户层 | 当前允许写入的用户基础或profile-v2配置层 |
| Profile-v2 | 第二版配置档 | 通过命名config文件叠加在基础用户配置之上的用户层机制 |
| Session flags | 会话标志 | 通过-c/--config加入当前进程的临时高优先级普通层 |
| Legacy managed config | 遗留托管配置 | 迁移期仍兼容、历史上压在普通配置顶端的managed_config.toml机制 |
| Requirements | 管理要求 | 与普通层分开组合的允许集合、精确值和组织安全约束 |
| Exact requirement | 精确要求 | 将配置路径固定为唯一值并使普通用户写入只读的约束 |
| ConfigRequirements/read | 配置要求读取 | App获取管理员允许范围和固定政策的JSON-RPC方法 |
| Key path | 键路径 | 用点与可引号segment定位嵌套TOML配置的编辑地址 |
| Replace | 替换 | 将目标路径设置为新值、不承诺保留目标table旧成员的策略 |
| Upsert | 更新或插入 | 对table递归叠加、不存在时创建的编辑策略 |
| Null clear | 空值清除 | wire中用JSON null表示从TOML删除路径，而非存储null |
| Sparse overlay | 稀疏覆盖 | 只沿目标key path构造最小嵌套table再递归合并 |
| Layer version | 层版本 | 对canonical JSON计算SHA-256所得的稳定内容指纹 |
| Canonical JSON | 规范JSON | 递归排序object key后得到、供稳定hashing使用的JSON |
| Expected version | 预期版本 | 客户端写入时声明自己所依据的用户层指纹 |
| Optimistic concurrency | 乐观并发 | 先允许并行阅读、写时用版本不匹配拒绝丢失更新 |
| ConfigVersionConflict | 配置版本冲突 | 文件在客户端读取后已变化，需要重新读取和合并意图 |
| Batch write | 批量写 | 在一个用户文件上先整体应用和校验多个edit再统一持久化 |
| OkOverridden | 成功但被覆盖 | 用户层文件已写成功，但更高普通层仍提供最终值 |
| OverriddenMetadata | 覆盖元数据 | 第一个覆盖者的来源、说明和实际effective值 |
| ConfigRequirementReadonly | 管理要求只读 | 某路径被exact requirement管理，写入持久化前即被拒绝 |
| ConfigEditsBuilder | 配置编辑构造器 | 将语义set/clear转换为保留注释和顺序的TOML文本编辑 |
| Serialization scope | 串行化作用域 | 让config共享读并发、全局写有序协调的请求调度边界 |
| Runtime feature enablement | 运行时功能启用表 | App-server内存中的实验功能覆盖，不必等同于持久配置层 |
| Cache invalidation | 缓存失效 | 配置写后清除plugin/skill旧派生结果，使下次重新发现 |
| Reload user config | 重载用户配置 | 批量写后可选地为已加载Thread重建可热更新配置 |
| Session defaults only | 仅会话默认值 | model、reasoning、service tier、personality等只面向新会话的批量编辑分类 |
| Preserve session layers | 保留会话层 | Thread热刷新基础配置时不抹掉其创建期局部覆盖 |
| Best-effort reload | 尽力刷新 | 单Thread失败只记录并跳过，不回滚已经成功的文件写入 |

## 81. App-server 模型目录、选择、Reasoning、Service Tier、Provider 能力与刷新

| 术语 | 直译或展开 | 在本课程中的含义 |
|---|---|---|
| Model catalog | 模型目录 | ModelsManager当前掌握并可用于选择和metadata解析的模型集合 |
| ModelInfo | 模型信息 | Core使用的完整窗口、指令、工具、reasoning和运行行为元数据 |
| ModelPreset | 模型预设 | ModelInfo经过排序、认证过滤和默认标记后的picker中间对象 |
| App-server Model | App模型对象 | model/list向客户端暴露的精简、稳定选择器wire投影 |
| Model slug | 模型短名 | `/models`合并、配置保存和实际请求使用的模型身份字符串 |
| Picker | 选择器 | App展示模型、effort、modality与service tier的用户界面 |
| ModelProviderInfo | 模型提供方信息 | endpoint、认证、wire API、重试和连接策略配置 |
| Provider capability | 提供方能力 | 当前Provider是否支持namespace tools、image generation和web search |
| Input modality | 输入模态 | 单模型可接受的text、image、audio用户输入类型 |
| Reasoning effort | 推理强度 | 目录声明、Thread/Turn可选择的有序非空字符串 |
| Custom reasoning effort | 自定义推理强度 | 旧客户端保留后端新增未知effort的前向兼容表示 |
| Progression order | 递进顺序 | supportedReasoningEfforts数组本身定义的强度选择次序 |
| Service tier | 服务档位 | 模型支持的请求路由/速度档位ID及其展示文案 |
| Default service tier | 默认服务档位 | catalog建议值，不保证未配置时Core自动发送该tier |
| FastMode | 快速模式 | Core接受和发送非默认service tier前必须开启的Feature门控 |
| Visibility | 可见性 | ModelInfo的list/hide/none选择器展示状态 |
| Hidden model | 隐藏模型 | 不进默认picker、但仍可能用于显式配置或历史恢复的模型 |
| Priority | 优先级 | 数值越小越靠前、参与picker顺序和默认项选择的字段 |
| supported_in_api | API支持标志 | 非ChatGPT认证下决定preset是否保留的目录字段 |
| Bundled catalog | 内置目录 | 随Codex binary提供的fallback模型元数据集合 |
| Remote catalog | 远端目录 | 当前Provider `/models`请求返回的ModelInfo集合 |
| Static catalog | 静态目录 | Provider进程内提供并可视为权威的固定模型集合 |
| Source of truth | 权威来源 | 满足ChatGPT和可见模型条件时整体替代bundled目录的远端快照 |
| RefreshStrategy | 刷新策略 | Online、Offline和OnlineIfUncached三种目录更新决策 |
| ModelsEndpointClient | 模型端点客户端 | 封装Provider认证与transport、真正执行`/models`请求的接口 |
| Models cache | 模型缓存 | 内存列表和`models_cache.json`可重建目录副本 |
| Cache TTL | 缓存生存期 | 固定提交中模型文件缓存保持fresh的300秒期限 |
| ETag | 实体标签 | 远端目录HTTP版本标识，用来判断是否需要重新下载 |
| Revalidation | 重新验证 | ETag未变时只延长TTL、不替换目录内容的过程 |
| ModelsRefreshWorker | 模型刷新任务 | App-server立即并每3分钟Online刷新目录的可取消后台task |
| Weak manager reference | 弱管理器引用 | worker不反向保活ModelsManager的`Weak`所有权关系 |
| Auth filtering | 认证过滤 | ChatGPT保留全部，其他认证只保留supported_in_api模型的步骤 |
| Picker default | 选择器默认项 | auth过滤后第一个可见preset，没有可见项时退到第一项 |
| Provider model fallback | 提供方模型回退 | 明确允许时权威static catalog把不可用请求模型换成默认 |
| Fallback ModelInfo | 回退模型信息 | 未知显式slug在本地获得的保守metadata，不证明远端存在 |
| Longest-prefix match | 最长前缀匹配 | 带变体后缀模型继承最具体目录slug metadata的算法 |
| Namespaced slug | 命名空间模型名 | `provider/model`形式并只允许剥一层进行metadata重试的名字 |
| Model upgrade info | 模型升级信息 | picker显示的迁移目标与文案，不代表运行模型已改变 |
| Availability NUX | 可用性新手体验 | 模型新开放时的onboarding提示message |
| Sticky model override | 粘性模型覆盖 | Turn设置后延续到后续Turns的model/effort/tier更新 |
| Effective reasoning effort | 有效推理强度 | 显式Thread/Turn effort优先，否则使用ModelInfo default |
| Effort compatibility fallback | 强度兼容回退 | 换模型时旧effort不支持则取新有序列表中间偏低项 |
| Service tier request filtering | 服务档位请求过滤 | FastMode、模型支持范围和`default`特殊值共同决定实际wire字段 |
| Model reroute | 模型重路由 | 服务端在运行时把一次Turn从fromModel改到toModel并通知App |
| Model verification | 模型验证 | 与特定Thread/Turn访问安全相关、去重发送的运行状态 |
| Safety buffering | 安全缓冲 | 当前Turn因安全检测显示等待、原因和可选更快模型的状态 |
| Moderation metadata | 审核元数据 | 服务端随Turn返回、由App用于安全体验的JSON数据 |
| Opaque cursor | 不透明游标 | 客户端原样回传且不依赖当前offset实现的分页令牌 |

## 82. 源码精读 01：`ConfigManager::apply_edits`

| 英文 / 代码词 | 中文 | 在源码精读中的实际含义 |
|---|---|---|
| Close reading | 源码精读 | 选择一个关键函数，逐块追踪输入、状态、副作用、失败和测试证据 |
| `apply_edits` | 应用编辑 | 把单项和批量配置写入统一为同一条安全写入流程 |
| Working copy | 工作副本 | 真正落盘前用来模拟整批结果的 `user_config` clone |
| Semantic tree | 语义树 | 用 `toml::Value` 表示、适合合并与完整校验的配置结构 |
| Text edit | 文本编辑 | 用 `ConfigEdit` 表示、尽量保留原注释和顺序的最小路径修改 |
| Atomic batch semantics | 批量原子语义 | 后一项失败时不把前一项目标编辑留在既有文件中 |
| Optimistic concurrency | 乐观并发控制 | 客户端提交读取时版本，服务端发现内容已变就拒绝旧写入 |
| Content fingerprint | 内容指纹 | 规范化配置内容经过 SHA-256 得到的版本标识 |
| `expected_version` | 预期版本 | 客户端上次读取的用户层指纹；省略则跳过冲突检查 |
| `parsed_segments` | 已解析路径段 | 请求路径的分段集合，最后用于检查是否被高层覆盖 |
| `persist_segments` | 持久化路径段 | 为保持文件表示一致，文本编辑器实际替换的范围 |
| Sparse overlay | 稀疏覆盖树 | 把一个深层路径和值包成可交给通用 merge 的嵌套 TOML |
| Representation switch | 表示切换 | 同一配置语义从旧 TOML 形状迁移到新形状 |
| `Cow::Borrowed` | 借用分支 | 复用层栈里已有用户层，不拥有它 |
| `Cow::Owned` | 拥有分支 | 用户层不存在时持有新创建的层 |
| No-op edit | 无变化编辑 | 输入合法但新旧值相同，因此不触发文件写入 |
| Validate before side effect | 副作用前校验 | 先在内存完成整批合并和整体校验，最后才持久化 |
| `ConfigRequirementReadonly` | requirement 只读 | 字段被管理要求锁定，用户层不允许写 |
| `OkOverridden` | 成功但被覆盖 | 用户文件写成功，但高优先级层决定了不同的有效值 |
| First overridden edit | 第一项被覆盖编辑 | 批量响应当前只报告请求顺序中的第一份覆盖元数据 |

## 83. 源码精读 02：`ModelsManager::list_models`

| 英文 / 代码词 | 中文 | 在源码精读中的实际含义 |
|---|---|---|
| Model catalog | 模型目录 | 模型 slug、能力、可见性、优先级和默认信息的集合 |
| Bundled catalog | 内置目录 | 随 Codex 程序提供、动态刷新失败时可用的保底目录 |
| Active snapshot | 活动快照 | `remote_models` 当前保存并供读者 clone 的目录 |
| `RefreshStrategy` | 刷新策略 | Offline、OnlineIfUncached 与 Online 三种目录更新意图 |
| `OnlineIfUncached` | 无缓存才联网 | 合格缓存命中即返回，否则访问 endpoint |
| Cache hit | 缓存命中 | TTL 和 client version 都满足资格的目录条目 |
| Stale cache | 过期缓存 | 年龄超过 300 秒、不能由普通 load 使用的条目 |
| ETag revalidation | ETag 重新验证 | 目录标签未变时保留 payload，只续期 freshness |
| Remote-only catalog | 仅远端目录 | ChatGPT auth 下由含 visible 项的 remote 整体决定目录 |
| Catalog merge | 目录合并 | 从 bundled 重建，同 slug 用 remote 替换，新 slug 追加 |
| Auth filtering | 认证过滤 | 在 picker 投影阶段删除当前认证不能用的模型 |
| Hidden filtering | 隐藏过滤 | app-server 根据 `include_hidden` 决定是否展示隐藏 preset |
| Picker default | 选择器默认项 | 排序和认证过滤后选出的默认 visible 模型 |
| `ModelsEndpointClient` | 模型端点客户端 | 封装 provider 认证和 `/models` transport 的接口 |
| Graceful degradation | 优雅降级 | cache 或远端失败时继续返回旧内存或 bundled 目录 |
| Singleflight | 在途请求去重 | 多个相同刷新共享一次请求；当前主函数没有显式提供 |
| Weak worker owner | 弱 worker 所有者 | 后台刷新不反向保活 `ModelsManager` 的 `Weak` 关系 |
| Wire projection | 协议投影 | `ModelInfo` 到 `ModelPreset` 再到 app-server `Model` |
| Fallback metadata | 回退元数据 | 未知显式 slug 获得的保守运行参数，不证明远端可用 |
| Longest-prefix match | 最长前缀匹配 | 从候选 slug 中选择最具体 metadata 来源 |

## 84. 源码精读 03：`Session::spawn` 与 `Session::new`

| 英文 / 代码词 | 中文 | 在源码精读中的实际含义 |
|---|---|---|
| Session initialization | Session 初始化 | 从 thread 输入、配置和共享依赖组装出可运行 Agent 的全过程 |
| `Session` | 运行会话 | 保存 thread 状态、长期服务与当前 turn 的核心对象 |
| `SessionIo` | 会话输入输出 | submission、event、status 和 termination 通信句柄的集合 |
| `CodexThread` | Codex thread 句柄 | app-server 和 ThreadManager 持有的 `Session` + `SessionIo` 包装 |
| `SessionSpawnArgs` | Session 创建参数 | ThreadManager 交给 `Session::spawn` 的完整依赖清单 |
| `SessionConfiguration` | Session 运行配置 | 初始化时解析出的 model、provider、权限和 instructions 等合同 |
| `SessionState` | Session 状态 | 历史、token、可变设置及 turn 间延续的数据 |
| `SessionServices` | Session 服务 | MCP、模型、exec、plugins、persistence 等生命周期较长的协作者 |
| `InitialHistory` | 初始历史 | New、Cleared、Forked 或 Resumed 四种起始方式 |
| lineage | 血缘关系 | root、parent、fork 与 spawned subagent 之间的来源关系 |
| session tree | Session 树 | 根 Agent 与其 subagents 构成、共享 session id 的树 |
| `LiveThread` | 活动持久线程 | `ThreadStore` 中负责新建或恢复 rollout 的写入对象 |
| materialize | 物化 | 把逻辑上的 rollout 真正创建为文件或耐久记录 |
| `LiveThreadInitGuard` | 活动线程初始化守卫 | 成功时 commit、失败时 discard 的资源提交边界 |
| `SessionConfigured` handshake | Session 配置握手 | ThreadManager 要求从内部 event queue 读到的第一条启动事件 |
| submission loop | 提交循环 | 后台持续读取 `Op`、分派处理，直到 shutdown 的任务 |
| hard dependency | 硬依赖 | 初始化失败会阻止 Session 对外发布，例如 required MCP server |
| soft dependency | 软依赖 | 失败只产生 warning 或 status，Session 仍可启动 |
| prewarm | 预热 | 第一条 turn 之前提前准备 provider 连接或模型 session |
| lifecycle contributor | 生命周期贡献者 | 接收 thread start、resume 等回调的 extension 组件 |
| commit / discard | 提交 / 丢弃 | 保留成功初始化的资源，或清理尚未发布的中间对象 |

## 85. 源码精读 04：`run_turn`

| 英文 / 代码词 | 中文 | 在源码精读中的实际含义 |
|---|---|---|
| Turn | 回合 / 工作周期 | 一次公开用户工作，从 started 到 completed，可包含多次模型请求 |
| Task | 后台任务 | Core 执行 Turn 生命周期的异步对象 |
| sampling request | 采样请求 | 一次向模型发送 prompt 并消费 response stream |
| Agent loop | Agent 循环 | 模型决策、工具执行、结果回填、模型继续决策 |
| `TurnContext` | Turn 上下文 | 整个 Turn 共享的模型、权限、mode、output schema 与身份快照 |
| `StepContext` | Step 上下文 | 单次 sampling 使用的 MCP、工具与 environment 动态快照 |
| `ActiveTurn` | 活动 Turn | Session 当前运行的 task 与可变 `TurnState` |
| `TurnState` | Turn 状态 | approvals、pending input、permissions、tool count 等运行状态 |
| `RegularTask` | 普通任务 | 执行常规用户 Turn 的 `SessionTask` |
| `ModelClientSession` | 模型客户端会话 | Turn 内复用的连接和 sticky routing 状态 |
| response stream | 响应流 | 上游模型逐事件返回 created、item、delta、completed |
| response item | 响应条目 | message、reasoning、tool call 等模型输出单元 |
| tool call | 工具调用请求 | 模型提出、由 Core 执行的结构化动作 |
| tool output | 工具结果 | 执行后记录到 history、供下一次 sampling 读取的 observation |
| `ToolRouter` | 工具路由器 | 提供 model-visible specs，并把工具名分派到 handler |
| `ToolCallRuntime` | 工具调用运行时 | 固定 StepContext，协调工具执行、并行和取消 |
| `FuturesOrdered` | 有序异步队列 | 并发推进 futures，同时按插入顺序产出结果 |
| `needs_follow_up` | 需要后续请求 | 当前 Turn 尚需再发起一次 sampling |
| pending input | 待处理输入 | Turn 运行中到达的用户消息或 mailbox 内容 |
| steer | 驾驶中修正 | 向 active Turn 追加输入，不创建新的 TurnStarted |
| retry | 重试 | 暂时性错误后重做同一次逻辑 sampling |
| follow-up sampling | 后续采样 | 新增 tool output 或输入后，开始下一逻辑 step |
| compaction | 上下文压缩 | 在仍需继续时为 context window 腾出空间 |
| raw response completion | 原始响应完成 | 一次 sampling response 完成，不等于整个 Turn 完成 |
| terminal event | 终态事件 | Core `TurnComplete` 或 `TurnAborted` |
| durability barrier | 持久化屏障 | 等待此前 rollout 写入真正完成 |
| sticky routing | 粘性路由 | Turn 内尽量复用同一模型后端路由状态 |
| transcript | 对话记录 | 用户、assistant、tool call 与 tool output 的统一历史 |

## 86. 源码精读 05：Tool dispatch

| 英文 / 代码词 | 中文 | 在源码精读中的实际含义 |
|---|---|---|
| Tool dispatch | 工具分发 | 把模型 tool call 交给当前 Step 对应 runtime，并完成通用生命周期处理 |
| `ToolRouter` | 工具路由器 | 同时保存 Registry 和发给模型的可见 tool specs |
| `ToolRegistry` | 工具注册表 | 用规范化 `ToolName` 查找 `CoreToolRuntime` 的有序映射 |
| handler | 处理器 | 解析某个工具自己的参数并完成业务流程的实现 |
| runtime | 运行实现 | Registry 可统一调用的执行合同，不必然启动子进程 |
| `ToolExecutor` | 工具执行 trait | 定义 tool name、spec、handle、并行和 exposure 等基础行为 |
| `CoreToolRuntime` | Core 工具 runtime trait | 增加 hook、telemetry、payload kind、取消和参数增量能力 |
| trait object | trait 对象 | 用 `dyn Trait` 擦除不同 handler 的具体 Rust 类型 |
| dynamic dispatch | 动态分发 | 运行时通过 trait object 调用真实 handler 实现 |
| `ToolName` | 工具身份 | namespace 与 name 组成并经过默认空间规范化的 key |
| namespace | 命名空间 | 隔离不同来源的同名工具 |
| collision | 工具名冲突 | 多个 runtime 试图占用同一规范化名字 |
| `ToolExposure` | 工具暴露方式 | direct、deferred、hidden 或 Code Mode 等模型可见策略 |
| model-visible spec | 模型可见规格 | 当前 sampling 真正收到的工具名、描述和参数 schema |
| hidden tool | 隐藏工具 | Registry 能分派，但当前不向模型广告的工具 |
| `ToolCall` | 工具调用 | 从 ResponseItem 规范化得到的名字、call id 与 payload |
| `ToolInvocation` | 工具调用上下文 | 补齐 Session、Turn、Step、取消 token、tracker 和 source 的可执行调用 |
| payload kind | 载荷种类 | Function、Custom、ToolSearch 等顶层输入形状 |
| `call_id` | 调用关联 ID | 将工具结果与原始模型调用配对的编号 |
| execution gate | 执行门 | 用 RwLock 协调 parallel 与 serial 工具进入 handler |
| readiness | 就绪等待 | 工具在占用并行门之前等待运行依赖可用 |
| `PreToolUse` | 工具使用前 hook | 在 handler 前放行、阻止或改写输入 |
| `PostToolUse` | 工具使用后 hook | 副作用完成后添加上下文、阻止或替换模型可见结果 |
| lifecycle outcome | 生命周期结果 | Completed、Failed、Blocked 或 Aborted |
| terminal ownership | 终态所有权 | 完成与取消竞态中只有一方能发送终态通知 |
| `AnyToolResult` | 类型擦除工具结果 | 保留 call id、payload 与 `Box<dyn ToolOutput>` 的通用容器 |
| `ToolOutput` | 工具输出 trait | 把同一结果投影为日志、Responses item 或 Code Mode JSON |
| `RespondToModel` | 可回复模型错误 | 转成失败 tool output，使 Agent loop 能尝试恢复 |
| `Fatal` | 致命错误 | 内部合同或基础设施严重失败，不能只当普通工具观察值 |
| approval | 审批 | 决定某个具体动作是否获得授权 |
| reviewer | 审批者 | permission hook、Guardian 或用户 |
| `ApprovalAction` | 审批动作 | 带 command、cwd、patch、files 和权限事实的审核对象 |
| approval cache key | 审批缓存键 | 限定“当前 Session 允许”可复用范围的动作身份 |
| `ExecApprovalRequirement` | 执行审批要求 | Skip、NeedsApproval 或 Forbidden |
| `ToolOrchestrator` | 工具编排器 | 统一执行审批、Sandbox 选择、attempt 与可选升级重试 |
| Sandbox | 沙箱 | 在执行层强制限制文件和网络等能力的隔离边界 |
| `SandboxAttempt` | 沙箱执行尝试 | 带确定 Sandbox、权限、cwd、网络与平台参数的一次 run |
| permission profile | 权限配置档 | 当前环境允许的文件、网络和工作区访问能力集合 |
| denied read | 禁止读取 | 即使升级也不能被无沙箱执行绕过的读取限制 |
| escalation | 升级执行 | Sandbox denial 后在策略允许时申请更宽执行方式 |
| sandbox denial | 沙箱拒绝 | 被 Sandbox 策略阻止的结构化失败，不是任意非零退出 |
| teardown | 资源收尾 | 取消后终止进程树、关闭 I/O 并释放本地或远端资源 |
| argument diff | 参数增量 | 模型流式生成工具参数时新到的一段文本 |
| diff consumer | 增量消费者 | 在完整调用前解析参数片段并产生进度事件的对象 |

## 87. 源码精读 06：Unified exec

| 英文 / 代码词 | 中文 | 在源码精读中的实际含义 |
|---|---|---|
| Unified exec | 统一执行 | 同时支持短命令、后台命令、交互输入及本地/远端环境的进程系统 |
| `exec_command` | 执行命令工具 | 创建新进程，先等待输出，随后返回退出码或可继续使用的 session id |
| `write_stdin` | 写标准输入工具 | 给已有进程写字符；空字符表示只轮询新输出 |
| `UnifiedExecProcessManager` | 统一进程管理器 | Session 级 owner，负责 ID、store、输出、终止和容量控制 |
| `UnifiedExecProcess` | 统一进程包装 | 抽象本地 PTY/pipes 与远端 exec-server process |
| `ProcessStore` | 进程存储 | process id 到复合进程条目的 Session 内映射 |
| `session_id` | 工具会话 ID | 模型侧参数名，等同于 Core 内部逻辑 `process_id` |
| `process_id` | 逻辑进程 ID | Core 分配的进程会话句柄，不是 OS PID |
| `yield_time_ms` | 让出等待时间 | 本次工具调用等待输出多久，不是杀进程的总超时 |
| initial yield | 初始等待 | `exec_command` 启动后第一次收集输出的窗口 |
| background terminal | 后台终端 | initial yield 后仍存活、可跨 Turn 查询或终止的进程会话 |
| `tty` | 终端开关 | 是否使用 terminal 语义并保持普通 stdin 开放 |
| PTY | 伪终端 | 让交互程序感知终端的 OS 会话机制 |
| pipe | 管道 | 不提供终端语义的标准输入输出字节通道 |
| poll | 轮询 | `write_stdin(chars="")` 等待并取走后续输出 |
| `interaction_lock` | 交互锁 | 串行化同一进程的 write、poll、drain 和终态处理 |
| `ProcessHandle` | 进程 transport | Local 与 ExecServer 两种底层实现 |
| exec-server | 执行服务器 | 在远端目标环境启动、读写和终止进程的服务 |
| output chunk | 输出块 | output task 每次处理的一段 bytes |
| `OutputHandles` | 输出共享句柄 | buffer、notify、closed 状态和 cancellation token 的集合 |
| `HeadTailBuffer` | 首尾缓冲 | 有界保留输出前部和尾部、记录省略中部 bytes 的结构 |
| recent output | 最近输出 | 自上次 drain 后用于本次 tool response 的新增输出 |
| transcript | 过程记录 | 供实时 delta 和最终 CommandExecution item 使用的有界输出视图 |
| output delta | 输出增量 | 命令运行期间逐块发送给客户端的实时事件 |
| sequence number | 序号 | 远端输出事件的位置，用于发现丢失和发起补读 |
| reconciliation | 对账 | 发现事件 gap 后用 read API 恢复 retained 输出和终态 |
| output closed | 输出关闭 | producer 保证不会再写入新 chunk |
| output drained | 输出排空 | consumer 已处理退出前的尾部输出 |
| trailing output | 尾部输出 | exit signal 后才从 pipe 或远端流到达的 bytes |
| exit watcher | 退出观察任务 | 等进程、输出和网络判定收尾后发布唯一最终命令 item |
| `InitialExecCommandGuard` | 初始命令守卫 | 协调 initial response 与显式 terminate 的竞态 |
| `ProcessState` | 进程状态 | exited、exit code、failure 与 sandbox denial 的最新快照 |
| exit code | 退出码 | 程序的业务终态，非零不自动代表执行基础设施失败 |
| `UnifiedExecError` | 统一执行错误 | 对创建、transport、句柄、stdin、Sandbox 和路径错误的分类 |
| deferred network approval | 延迟网络审批 | 进程运行中在实际网络访问发生时才完成的策略判断 |
| late denial | 迟到拒绝 | 进程退出后短时间内才到达的 network denial |
| prune | 修剪 / 淘汰 | store 达到软上限时移除并终止适合的旧进程 |
| LRU | 最近最少使用 | 根据 `last_used` 选择淘汰候选的策略 |
| teardown | 资源收尾 | 终止进程、排空输出、注销审批并删除 store entry |

## App-server JSON-RPC 分派补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| JSONL / JSON Lines | JSON 行格式 | stdio transport 以换行划分消息；一行必须是一条完整 JSON |
| framing | 分帧 | 从连续 stdin/socket 字节中确定每条协议消息的边界 |
| wire shape | 线上数据形状 | 客户端与 App-server 实际交换的字段；当前基线不包含 `jsonrpc` 字段 |
| envelope | 消息信封 | request、notification、response、error 的通用外层字段 |
| `JSONRPCMessage` | JSON-RPC 风格消息 | 用 untagged enum 表示四种入站或出站 envelope |
| `JSONRPCRequest` | 通用 RPC 请求 | 只知道 id、method、可选 params/trace，尚未验证具体 method 的参数类型 |
| `ClientRequest` | 客户端请求枚举 | method 和 params 已解析为具体 Rust variant/type 的强类型请求 |
| `ServerRequest` | 服务器请求枚举 | App-server 反向要求客户端批准、输入或执行客户端侧能力的有响应请求 |
| `ClientResponsePayload` | 客户端请求的响应载荷 | App-server 回答 `ClientRequest` 时放入 result 的强类型业务数据 |
| `ServerResponse` | 服务器请求的响应 | 客户端回答 `ServerRequest` 后，App-server 可恢复出的强类型结果 |
| request | 有答复请求 | 含 id；发起者为它保留 pending 状态并等待 response/error |
| notification | 单向通知 | 不含 id；接收者不应发送 RPC response |
| correlation | 关联 | 使用相同 request id 将乱序返回的 response/error 对应到原请求 |
| `RequestId` | 请求 ID | 字符串或整数；不承诺跨不同客户端连接全局唯一 |
| `ConnectionId` | 连接 ID | transport 为 stdio、WebSocket 等连接分配的进程内稳定编号 |
| `ConnectionRequestId` | 连接内请求键 | `(connection_id, request_id)`，防止多连接使用相同 id 时串线 |
| `TransportEvent` | 传输事件 | 把具体传输统一为 ConnectionOpened、ConnectionClosed、IncomingMessage |
| `untagged` enum | 无显式类型标签枚举 | Serde 根据字段形状尝试匹配 request/notification/response/error |
| internally tagged enum | 内部带标签枚举 | `ClientRequest` 使用现有 `method` 字段选择 enum variant |
| typed dispatch | 强类型分派 | JSON method/params 校验后，通过穷举 match 调用具体 processor |
| exhaustive match | 穷举匹配 | Rust 要求 enum variant 被完整处理，减少新增 method 时漏接路由 |
| initialize gate | 初始化门 | 除 Initialize 外的请求必须等当前连接建立 session capability 后才处理 |
| experimental gate | 实验能力门 | method/params 标为 experimental 时，要求该连接初始化时显式启用 |
| `ConnectionSessionState` | 连接会话状态 | 每个连接自己的初始化信息、实验能力、通知偏好和 RPC gate |
| inbound request context | 入站请求上下文 | 追踪客户端请求直到 App-server 发出唯一 success/error |
| pending callback | 待完成回调 | App-server 发出 server request 后，按 id 保存的客户端答复入口 |
| `oneshot` channel | 一次性异步通道 | 一个 sender 对一个 receiver 传递一次成功或错误终态 |
| outbound router | 出站路由器 | 将定向或广播 envelope 送到相应连接 writer，并执行能力/订阅过滤 |
| `ToConnection` | 定向连接 | 出站消息只发给指定 connection id |
| `Broadcast` | 广播 | 出站 notification 发给符合 initialized/capability/filter 条件的连接 |
| overload response | 过载响应 | 入站有界队列满时，尽量用原 request id 返回“稍后重试”错误 |
| response serialization fallback | 响应序列化兜底 | 成功 payload 无法转 JSON 时，尝试用同 id 改发 internal error |

## App-server 请求串行化补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| request serialization | 请求串行化 | 按逻辑资源协调冲突 handler 的执行顺序，不是 JSON 编码 |
| serialization scope | 串行化范围 | 从 typed request/params 提取的逻辑冲突域 |
| `ClientRequestSerializationScope` | 客户端请求串行化域 | Global、Thread、Process、Watch、OAuth 等协议层分类 |
| queue key | 队列键 | scope 加必要 connection id 后生成的运行时资源身份 |
| `RequestSerializationQueueKey` | 请求队列键 | App-server 内 HashMap 使用的 Global/Thread/Process 等 enum |
| access mode | 访问模式 | `Exclusive` 或 `SharedRead`，决定一批可运行多少请求 |
| `Exclusive` | 独占访问 | 同 key 当前执行批次只包含这一条 handler |
| `SharedRead` | 共享读取 | 同 key 队首连续 shared reads 可一起并发运行 |
| FIFO | 先进先出 | 请求按成功 enqueue 到 `VecDeque` 的次序形成执行批次 |
| read batch | 读批次 | 位于队首且连续的 SharedRead 集合 |
| writer fairness | 写者公平性 | 已排队 Exclusive 不会被后来 SharedRead 越过 |
| writer starvation | 写者饥饿 | 新读持续插队导致写请求无限等待的失败模式 |
| drain | 消费 / 排空 | 每 key task 循环取出下一批、等待完成并在空时删 key |
| drain task | 队列消费任务 | 一个活跃 queue key 唯一的异步 consumer |
| `VecDeque` | 双端队列 | 尾部追加、头部取出的 FIFO 容器 |
| `join_all` | 全部汇合 | 同批 futures 并发推进，全部结束后才进入下一批 |
| key granularity | 键粒度 | 资源身份切分粗细；过粗降吞吐，过细可能破坏一致性 |
| connection-scoped key | 连接级键 | process/watch 句柄与 connection id 组合，避免跨客户端串线 |
| unkeyed request | 无队列键请求 | scope 为 None，由独立 task 运行或依赖其他协调机制 |
| `QueuedInitializedRequest` | 已初始化排队请求 | 将 handler future 与可选 connection gate 包成统一工作单元 |
| `ConnectionRpcGate` | 连接 RPC 门 | 断线后拒绝尚未启动的 handler，并等待已启动 handler 收尾 |
| `TaskTracker` token | 任务追踪令牌 | 表示一条已通过 gate、尚未完成的 inflight handler |
| inflight handler | 执行中处理器 | 已取得 gate token 并开始运行、尚未返回的 RPC handler |
| background enqueue | 后台入队 | App-server 自有工作进入同一资源队列，但不绑定客户端 gate |
| linearization point | 线性化点 | 并发入队/删 key 在锁内可视为原子发生的位置 |
| per-key hard cap | 每键硬上限 | 单一资源最多积压量；当前 VecDeque 没有显式该上限 |

## Rollout resume 源码精读补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| resume | 恢复 / 继续 | 重新加入已加载 thread，或从持久化记录重建可运行 Session |
| hot resume / rejoin | 热恢复 / 重新加入 | 复用现有 CodexThread，不重复创建 Session 或 writer |
| cold resume | 冷恢复 | 内存对象已不存在，从 Thread Store 和 rollout 创建新运行时 Session |
| rollout | 运行记录 | 包含模型 items、生命周期事件、上下文快照和压缩检查点的 JSONL 事实流 |
| canonical rollout | 权威 rollout | Paginated 模式下以 durable JSONL 为准，SQLite 投影落后时可由它重建 |
| projection | 投影 | 从 rollout 事实生成模型 history、API Turn/Item 或数据库查询视图 |
| hydration | 恢复填充 | 用持久化数据补齐 Session 状态或 resume response 字段 |
| `StoredThread` | 已存储任务 | Thread Store 返回的身份、元数据、rollout path、history mode 与可选历史 |
| `InitialHistory::Resumed` | 恢复型初始历史 | 告诉 Core 复用原 conversation id 和 rollout path |
| `InitialHistory::Forked` | 分叉型初始历史 | 以给定 items 为起点，但生成新的 thread id |
| model context projection | 模型上下文投影 | 对 compaction、rollback 和 Responses items 重放后供下一次推理使用的历史 |
| UI history projection | UI 历史投影 | 面向客户端的 Thread、Turn、Item 和状态，不直接充当模型 prompt |
| reverse scan | 反向扫描 | 从 rollout 尾部寻找最新有效 replacement checkpoint 与必要元数据 |
| replay segment | 重放片段 | 按 TurnStarted/Complete/Aborted 边界组织的一组恢复记录 |
| replacement history | 替代历史 | compaction 后整体替换旧模型历史的 ResponseItem 列表 |
| reference context item | 参考上下文项 | 上一次已注入上下文的 TurnContext 基线，用于计算后续 diff |
| world-state baseline | 世界状态基线 | 从 full snapshot 和 patches 正向重放得到的当前比较状态 |
| rollout lineage | rollout 血缘 | fork 或分段记录之间需要一起扫描的继承链 |
| ordinal | 顺序号 | Paginated JSONL 中单调递增的记录位置，恢复 writer 后继续编号 |
| unsafe tail | 不安全尾部 | 崩溃留下的缺少换行或不完整 JSON 末尾，追加前需要修复 |
| writer ownership | 写者所有权 | 防止同一 thread 被两个 recorder/进程同时续写的约束 |
| stale in-progress turn | 过期进行中 Turn | 存储看似运行中、实际已无 live 执行者，恢复视图应标为 interrupted |
| `excludeTurns` | 排除 Turns | 只恢复元数据与运行能力，不为响应加载完整旧 Turn 列表的便宜路径 |
| resume override | 恢复覆盖 | 冷恢复时请求明确给出的 model、cwd、权限等配置；不能随意改写共享活动 Session |

## MCP tool call 源码精读补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| MCP | Model Context Protocol，模型上下文协议 | Host、client 与 server 之间发现并调用外部工具的一套协议 |
| Host | 主程序 / 宿主 | 这里主要指 Codex；它管理 MCP client、向模型暴露工具并执行安全策略 |
| MCP server | MCP 服务端 | 提供工具目录和 `tools/call` 能力的本地进程或远程服务 |
| `McpRuntime` | MCP 运行时 | thread 拥有的入口；以原子 publication 方式发布当前连接与工具目录 |
| publication | 已发布快照 | 读侧一次取得的完整运行时视图，避免看到“新连接配旧目录”等半更新状态 |
| publication gate | 发布门 | required server 未达到可接受状态时，阻止不完整连接集成为公开快照 |
| `McpConnectionSet` | MCP 连接集合 | 管理多个 server client、启动状态、工具目录、复用与关闭行为 |
| managed client | 受管 client | connection manager 持有的协议 client，带启动、缓存和错误状态 |
| discovery | 发现 | 通过 initialize 与 `tools/list` 获得 server 能力、说明和工具定义 |
| catalog | 工具目录 | 已发现并经过名称规范化、过滤和预算限制的 `ToolInfo` 集合 |
| catalog revision | 目录版本 | hard refresh 时递增；用于判断一份 prepared call 是否已经过期 |
| `McpBinding` | MCP 绑定快照 | 某个 Step 捕获的工具目录及其精确执行连接，保证“看见谁就调用谁” |
| `PreparedMcpCall` | 已准备 MCP 调用 | 绑定 exact client、tool metadata、approval authority 和 revision 的调用权 |
| exact client | 精确 client | 与工具被发现时同一 connection/authority 的协议 client，不重新按名字寻找 |
| filter | 过滤规则 | 按 enabled/disabled tools 等配置缩小 server 原始工具集合 |
| exposure | 暴露方式 | 决定工具对模型是 Direct、Deferred，还是 Hidden |
| Direct | 直接暴露 | 工具规格当下就进入模型可见 tool list |
| Deferred | 延迟暴露 | 目录中存在，但需通过后续发现/加载机制进入模型当前上下文 |
| annotation | 注解 / 风险提示 | read-only、destructive、open-world 等 server 提供的工具元数据 |
| approval mode | 审批模式 | auto、prompt、writes、approve 等 server/tool 审批策略 |
| approval authority | 审批权威 | 捕获调用时使用的 config、reviewer、plugin attribution 与复用规则 |
| Guardian | 自动审查者 | 可对 MCP 动作给出批准、拒绝或超时决定的 reviewer |
| permission hook | 权限 Hook | 在真实调用前参与 MCP 动作决策的扩展检查 |
| catalog lease | 目录租约 | revision 读锁覆盖 preparation 与真实调用的权威区间 |
| TOCTOU | 检查后到使用前竞态 | 检查允许后、真正执行前目录或连接被更换的风险 |
| elicitation | 信息征询 | server 在执行期间反向请求 Host 提供信息或让用户做选择 |
| `CallToolResult` | 工具调用结果 | 包含 content、structured content、`isError` 和 meta 的协议结果 |
| business error | 业务错误 | RPC 传输成功，但工具用 `isError=true` 表示任务失败 |
| transport error | 传输错误 | 连接、超时、认证或 JSON-RPC 层失败产生的 Rust `Err` |
| event projection | 事件投影 | 给 UI/rollout 的 `McpToolCallItem`；它有独立于模型上下文的截断预算 |
| `FunctionCallOutput` | 函数调用结果项 | 通过 `call_id` 把工具结果回填给下一次模型采样的 Responses input item |
| dirty refresh | 脏状态刷新 | 配置、认证或环境变化后，标记 runtime 需要重新计算并发布 |
| hard refresh | 强制目录刷新 | 重新执行 `tools/list`，发布新 catalog revision 并使旧 prepared call 失效 |
| `tools/list_changed` | 工具目录变化通知 | server 声称目录已变；在固定提交中 handler 只记日志，不会自动重拉普通目录 |

## Context compaction 源码精读补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| context window | 上下文窗口 | 一次模型请求能够容纳的输入、推理与输出空间 |
| compaction | 上下文压缩 | 用更短的 canonical 新历史整体取代旧模型历史 |
| replacement history | 替代历史 | 最新 checkpoint 之后应作为模型上下文起点的 `ResponseItem` 列表 |
| checkpoint | 检查点 | rollout 中声明“此前模型历史被 replacement 取代”的记录 |
| `ContextManager` | 上下文管理器 | Session 内维护 live model history、token 与 context baseline 的对象 |
| raw history | 原始历史 | 尚未针对某次模型请求执行 normalization 的内存 items |
| prompt history | 请求历史 | `for_prompt` 后真正准备发送给模型的 items |
| `CompactionTrigger` | 压缩触发者 | Manual 或 Auto |
| `CompactionReason` | 压缩原因 | UserRequested、ContextLimit、ModelDownshift 或 CompHashChanged |
| `CompactionPhase` | 压缩阶段 | StandaloneTurn、PreTurn 或 MidTurn |
| auto compact limit | 自动压缩阈值 | 达到后开始压缩的软预算，可能小于模型硬窗口 |
| `BodyAfterPrefix` | 前缀后正文作用域 | 从 active usage 中减去本窗口 prefill baseline 后判断增长量 |
| prefill baseline | 预填充基线 | 当前新窗口第一次请求前已占用的绝对 input token 起点 |
| `AutoCompactWindow` | 自动压缩窗口状态 | 保存窗口编号、血缘 ID、prefill 与一次性触发状态 |
| window lineage | 窗口血缘 | first、previous、current UUIDv7 组成的上下文窗口链 |
| local compaction | 本地编排压缩 | 普通 Responses 推理生成文字摘要，客户端拼出 replacement |
| remote compaction v1 | 远端压缩 v1 | 专门 compact endpoint 返回 compacted transcript |
| remote compaction v2 | 远端压缩 v2 | Responses stream 返回恰好一个 opaque Compaction item |
| opaque compaction item | 不透明压缩项 | 服务端携带关键旧状态的加密 item，客户端不解释其内部语义 |
| retained message | 保留消息 | v2 在 opaque item 之外继续放入新窗口的近期合规消息 |
| `SUMMARY_PREFIX` | 摘要前缀 | 标明一条 user-role message 实际来自 local compaction summary |
| call/output pair | 调用结果对 | 使用相同 `call_id` 的工具调用与工具结果 |
| synthetic output | 合成工具结果 | normalization 为未完成调用补入的稳定 `aborted` output |
| orphan output | 孤儿结果 | 历史中找不到对应 call 的工具 output |
| history normalization | 历史规范化 | 补 missing output、删 orphan，并按模型能力净化 image/audio |
| `CompactionTrigger` item | 压缩控制项 | remote v2 请求末尾的控制信号，不进入长期 live history |
| `ResponseItem::Compaction` | 模型压缩项 | remote v2 返回的 opaque 模型上下文载体 |
| `ContextCompactionItem` | UI 压缩项 | 向客户端发 started/completed 生命周期的 Turn item |
| `CompactedItem` | 持久化压缩记录 | rollout 中保存 replacement、summary 和窗口血缘的 checkpoint |
| initial context reinjection | 初始上下文重注入 | 丢弃 stale wrapper 后从当前 Session 重建指令、环境和状态 |
| `reference_context_item` | 上下文比较基线 | 决定下一普通 Turn 注入完整 context 还是只注入 diff |
| model downshift | 模型降档 | 切换到 context window 更小的模型 |
| comp hash | 压缩兼容哈希 | 模型声明的 compaction 语义兼容标识 |
| recompute token usage | 重算 token 用量 | replacement 安装后根据新窗口重建用量，避免沿用旧历史数字 |

## Sandbox approval lifecycle 源码精读补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| approval | 审批 | 判断一个动作能否执行；与执行时的 OS 权限不是同一个问题 |
| Sandbox | 沙箱 | 用平台机制限制进程可访问的文件、网络和系统能力 |
| approval policy | 审批策略 | Never、OnRequest、UnlessTrusted、Granular 等何时询问的规则 |
| permission profile | 权限档案 | 对文件系统、网络和额外能力的结构化描述 |
| `ExecPolicyManager` | 命令策略管理器 | 解析命令片段并综合显式规则、启发式与 approval policy |
| `ExecApprovalRequirement` | 执行审批要求 | `Skip`、`NeedsApproval`、`Forbidden` 三态决策 |
| `Skip` | 跳过审批 | 不必询问；是否绕过 Sandbox 仍由 `bypass_sandbox` 区分 |
| `NeedsApproval` | 需要审批 | Hook、Guardian 或用户允许后才可启动 |
| `Forbidden` | 禁止 | 策略不允许动作，直接返回拒绝 |
| `bypass_sandbox` | 绕过沙箱 | 第一次 attempt 不套普通平台 Sandbox wrapper |
| `UnlessTrusted` | 除非已信任 | 配置名 `untrusted`；已知安全只读命令可自动允许，其他通常询问 |
| `OnRequest` | 按请求审批 | 普通命令先在受限环境执行，显式越界请求再审批 |
| `Never` | 从不询问 | 不能弹审批；可在现有边界运行就运行，否则拒绝或失败 |
| `GranularApprovalConfig` | 细粒度审批配置 | 分开控制 sandbox、rules、skill、permission request、MCP elicitation |
| `RequireEscalated` | 请求升级权限 | 本次调用明确要求超出默认 Sandbox 的权限 |
| escalation | 权限升级 | 经策略和审批后以更宽、但仍受不变量约束的边界执行 |
| justification | 升级理由 | 给 reviewer 的说明，不是权限令牌 |
| prefix rule | 命令前缀规则 | 按 argv token 前缀匹配 allow、prompt 或 forbidden |
| amendment | 规则修订 | 用户批准后可持久化的 exec policy allow 前缀 |
| reviewer | 审批者 | Guardian 或用户 |
| Guardian | 自动审查者 | 根据结构化 action、approval reason 与 retry reason 给出决定 |
| strict auto-review | 严格自动审查 | 即使普通策略 `Skip` 也强制审查，且无 Sandbox retry 需重新审查 |
| `ReviewDecision` | 审批决定 | Approved、ApprovedForSession、Denied、Abort、TimedOut 等结果 |
| approval cache | 审批缓存 | 当前 Session 内对精确 action key 的 `ApprovedForSession` 复用 |
| approval key | 审批键 | 环境、规范化命令、cwd、Sandbox 权限与额外权限的组合 |
| `ToolOrchestrator` | 工具编排器 | 统一执行审批、Sandbox 选择、attempt 和一次升级重试 |
| attempt | 执行尝试 | 带确定 permission、Sandbox、cwd 和 network 上下文的一次运行 |
| `SandboxAttempt` | 沙箱尝试上下文 | 同时携带本机权限、远端规范权限和平台 Sandbox 参数 |
| `SandboxManager` | 沙箱管理器 | 判断是否需要 Sandbox、选择平台类型并转换启动请求 |
| `sandbox_requested` | 政策要求沙箱 | 即使 host 无具体 wrapper，也保留“本应受限”的政策事实 |
| denied-read restriction | 禁止读取限制 | 不能因为升级或无 Sandbox 重试而丢失的路径拒绝规则 |
| `SandboxErr::Denied` | 沙箱拒绝 | 执行层认为失败很可能由 Sandbox 边界导致的结构化错误 |
| `is_likely_sandbox_denied` | 沙箱拒绝启发式 | 根据 Sandbox 类型、退出码、输出关键词与 Linux 信号保守分类 |
| managed network | 受管网络 | 通过代理、host/protocol policy 与独立审批控制的网络路径 |
| fail closed | 失败时保持拒绝 | 无法构造可靠安全上下文时不猜测放行 |
| idempotent | 幂等 | 同一命令重试不会产生额外不同副作用 |

## Managed network approval 源码精读补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| managed network | 受管网络 | 由代理、Sandbox、policy 与审批控制的网络出口 |
| baseline policy | 基线策略 | 动态审批之前的 allowlist、denylist、私网和 method 规则 |
| allowlist miss | 允许列表未命中 | `NotAllowed`；在条件允许时可进入动态审批 |
| hard deny | 硬拒绝 | 显式 deny、私网、method、proxy disabled 等不可普通覆盖的结果 |
| `NetworkProxySpec` | 代理规格 | 合并用户配置、managed constraints、permission profile 和持久规则 |
| `NetworkPolicyDecider` | 网络决策器 | Allowlist miss 时参与动态审批的 callback |
| `BlockedRequestObserver` | 阻止观察器 | 记录拒绝并将 outcome 关联回 active execution |
| `NetworkPolicyRequest` | 网络策略请求 | Proxy 从真实请求取得的 protocol、host、port、method 和 attribution |
| `HostBlockDecision` | Host 基线决定 | `Allowed` 或 `Blocked(reason)` |
| execution attribution | 执行归属 | 将代理连接可靠映射到对应 tool execution |
| `registration_id` | Execution 注册 ID | NetworkApprovalService 追踪 active call 的身份 |
| `attribution_token` | 归属令牌 | 可信 bridge 获取 execution-scoped proxy state 的秘密 token |
| `HostApprovalKey` | Host 审批键 | environment、host、protocol、port 的精确组合 |
| pending owner | 等待审批所有者 | 为同 execution 同 target 发起 review 的第一个请求 |
| waiter | 审批等待者 | 复用同一 pending decision 的并发连接 |
| inline approval | 内联审批 | 真实网络 I/O 在 proxy 中暂停等待决定 |
| `AllowOnce` | 允许一次 | 只放行当前 pending，不写 Session cache |
| `AllowForSession` | Session 内允许 | 相同精确 HostApprovalKey 后续直接通过 |
| network amendment | 网络规则修订 | 持久化 allow/deny host 的 exec policy network rule |
| `NetworkRuleSaved` | 规则保存上下文 | 告诉模型持久网络规则已经生效的 developer fragment |
| `Immediate` | 立即收尾 | Shell 返回时立刻 finish network registration |
| `Deferred` | 延迟收尾 | Unified exec 的 approval 跟随后台进程 |
| late denial | 迟到拒绝 | Process exit 与 proxy observer outcome 之间的竞态 |
| `OnceCell` | 一次初始化容器 | 让多个 finish 消费者看到同一最终 outcome |
| SSRF | 服务端请求伪造 | 借受控 URL 访问本机或内部网络服务的攻击 |
| MITM | 中间人代理 | 在受控信任下检查 HTTPS 内部 method 的机制 |
| fail closed | 不确定时拒绝 | Attribution、review 或 cleanup 缺失时不默认放行 |

## Apply patch lifecycle 源码精读补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| apply patch | 应用补丁 | 把模型提出的结构化文件编辑真正写入执行环境 |
| freeform tool | 自由格式工具 | 参数是一段 grammar 约束文本，而不是 JSON object |
| Lark grammar | Lark 语法规则 | 固定提交用于约束 patch marker、hunk 和 change line 的工具格式 |
| marker | 标记行 | `*** Begin Patch`、`*** Update File` 等控制行 |
| hunk | 补丁块 | 一个 AddFile、DeleteFile 或 UpdateFile 操作 |
| chunk | 更新片段 | Update hunk 内的一段上下文与 old/new lines |
| streaming preview | 流式预览 | 模型参数尚未完成时由 `PatchApplyUpdatedEvent` 展示的提案 |
| shell interception | Shell 拦截 | 普通 shell 执行前识别 apply_patch 并转到专用安全路径 |
| heredoc | 多行输入重定向 | shell 把多行 patch body 交给命令的形式 |
| `ImplicitInvocation` | 隐式调用错误 | 裸 patch 未明确声明 apply_patch 工具身份 |
| `ApplyPatchArgs` | 补丁解析参数 | 保存规范 patch、hunks、workdir 和 environment id |
| `PathUri` | 路径 URI | 跨本地与远端 environment 表达绝对目标路径 |
| verify | 验证 | 读取当前文件、匹配旧行并预计算新内容和 diff |
| `ApplyPatchAction` | 预期补丁行动 | verification 后用于安全、审批和 UI 的 proposed changes |
| effective cwd | 有效工作目录 | 工具 cwd 与 shell 可选 `cd` 合成的路径基准 |
| `SafetyCheck` | 补丁安全结论 | AutoApprove、AskUser 或 Reject |
| writable root | 可写根 | filesystem sandbox policy 允许修改的目录树 |
| `ApplyPatchApprovalKey` | 补丁审批键 | environment id 与单个受影响 path 的组合 |
| `ApplyPatchRequest` | 补丁运行请求 | action 加 environment、审批、权限和 Sandbox 数据 |
| `ApplyPatchRuntime` | 补丁执行运行时 | 在 orchestrator attempt 中调用底层 apply_patch 并累计 delta |
| `AppliedPatchDelta` | 已应用变化增量 | 按执行顺序记录实际确定提交的文件文本变化 |
| committed prefix | 已提交前缀 | 整体失败前已经成功完成的一段 hunk 序列 |
| exact delta | 精确增量 | 能完整、确定描述已发生文件变化的 delta |
| partial success | 部分成功 | 调用整体失败，但前面 hunk 或 move 目标已经写入 |
| atomicity | 原子性 | 全部成功或完全不变；固定实现的多 hunk patch 不保证它 |
| rollback | 回滚 | 撤销已提交变化；底层不会自动回滚整批 patch |
| TOCTOU | 检查与使用时差 | verification 与 execution 之间文件状态可能变化 |
| `overwritten_content` | 被覆盖内容 | Add 或 move 写目标时记录的原目标文本 |
| `PatchApplyUpdatedEvent` | Patch 更新事件 | 流式参数中已能解析出的 proposed changes |
| `PatchApplyStatus` | Patch 完成状态 | Completed、Failed 或 Declined |
| turn diff tracker | 本轮差异跟踪器 | 使用实际 delta 更新本轮文件变化，必要时失效重算 |
| `CustomToolCallOutput` | 自定义工具输出 | 固定提交中直接 freeform apply_patch 回填模型的 item |

## Tool output 与 context feedback 源码精读补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| tool output | 工具输出 | Handler 执行后准备回给模型的数据 |
| context feedback | 上下文反馈 | 工具结果先写入 conversation，再通过下一次 prompt 交给模型 |
| `call_id` | 调用配对 ID | 连接一次模型 tool call 与对应 output |
| `ResponseInputItem` | 模型输入项 | Codex 主动构造、准备送回 Responses API 的 item |
| `ResponseItem` | 响应/历史项 | 模型输出和 conversation/rollout 保存的规范 item |
| `ToolCall` | 内部工具调用 | 统一保存 tool name、call ID 和 payload |
| `ToolPayload` | 工具输入载荷 | Function、Custom 或 ToolSearch 等输入类别 |
| `ToolOutput` | 工具结果接口 | 定义 logging、Hook、Code Mode 和 model-facing projection |
| `AnyToolResult` | 类型擦除结果 | 保存 call ID、原 payload 和动态 ToolOutput |
| `FunctionToolOutput` | 通用函数结果 | 承载文本、图片、音频等 content items |
| `FunctionCallOutputPayload` | 函数结果载荷 | Wire 上直接序列化为字符串或内容数组 |
| model-facing projection | 面向模型投影 | 同一原始结果为下一次 Responses prompt 生成的表示 |
| lossy conversion | 有损转换 | 文本化时忽略图片、音频或加密内容 |
| `McpToolOutput` | MCP 结果适配 | Direct context 和 Code Mode 获得不同输出投影 |
| `ExecCommandToolOutput` | Unified exec 结果 | 保存 session、exit code、raw output、时间和截断数据 |
| `log_preview` | 日志预览 | 有界 telemetry 内容，不是完整模型结果 |
| `success_for_logging` | 内部成功语义 | Telemetry、Hook 与 lifecycle 使用，不直接编码进 wire body |
| external context | 外部上下文 | 搜索、MCP 或外部服务带回的信息 |
| memory pollution | 记忆污染状态 | 外部上下文存在时禁止生成长期 memory |
| `PostToolUseFeedbackOutput` | Hook 反馈包装 | Original 供 logging/Code Mode，feedback 供 direct model |
| `RespondToModel` | 可反馈错误 | 转成与原 call 配对的 failure output，让模型修正 |
| Fatal | 致命错误 | 内部 contract 或 task runtime 异常，终止正常反馈链 |
| `AbortedToolOutput` | 已取消结果 | 用户停止后用于闭合未完成 call 的 output |
| terminal outcome | 唯一终态 | Completed、Failed、Blocked 或 Aborted |
| parallel gate | 并行准入门 | RwLock 协调可并行与必须串行的工具 |
| `FuturesOrdered` | 有序 Future 流 | 并发执行但按模型 call 插入顺序交付结果 |
| in-flight | 执行中 | 已调度但尚未 drain 到 history 的工具 future |
| drain | 排空 | 等待并记录当前 sampling 的全部工具结果 |
| truncation policy | 截断策略 | 按 bytes 或近似 tokens 限制 model-facing output |
| collection cap | 输出收集上限 | Process output 进入模型截断前的缓冲边界 |
| omission marker | 省略标记 | 告诉模型有内容未被保留 |
| normalization | 历史归一化 | Prompt 前补 call/output、删孤儿并适配模态 |
| synthetic output | 合成输出 | 缺失真实结果时插入的 aborted/empty output |
| orphan output | 孤儿输出 | 找不到对应 call 的 output |
| UUID v5 | 名称派生 UUID | 为 synthetic output 生成稳定、可重复 ID |
| modality | 模态 | Text、Image、Audio 等输入能力 |
| follow-up sampling | 后续采样 | 工具结果进入 history 后重新请求模型 |
| `needs_follow_up` | 需要继续 | 当前 response 含工具调用或其他未完成工作 |
| prompt cache churn | Prompt 缓存扰动 | 等价历史因随机 ID/顺序变化导致缓存失配 |

## Cancellation 与 interruption lifecycle 源码精读补充术语

| 名词 / 代码词 | 中文理解 | 在当前源码中的具体含义 |
|---|---|---|
| cancellation | 取消机制 | 通知异步工作尽快停止，并保证状态仍可解释 |
| interruption | 中断生命周期 | 从用户停止请求到 Turn 进入结构化终态的完整过程 |
| `Op::Interrupt` | 中断操作 | 经 Session submission loop 分派的 Core 控制消息 |
| `turn/interrupt` | Turn 中断 RPC | App-server 按 thread ID 和 turn ID 请求取消活动 Turn |
| `CancellationToken` | 取消令牌 | 可克隆并建立父子关系的协作式取消信号 |
| child token | 子令牌 | 父 token 被取消时一同取消的下游信号 |
| cooperative cancellation | 协作式取消 | Future 观察 token 后主动停止和清理 |
| forced abort | 强制中止 | 使用 task handle 阻止 Tokio task 继续被 poll |
| `RunningTask` | 运行任务 owner | 保存 task、token、handle、done、TurnContext 和 guards |
| `ActiveTurn` | 活动 Turn 槽位 | 绑定当前 RunningTask 与 TurnState |
| `TurnState` | Turn 可变协调状态 | 保存审批、权限、用户输入、elicitation 和 dynamic tool waiters |
| `done: Notify` | 完成通知 | cooperative task 结束时唤醒取消路径 |
| graceful interruption | 优雅中断 | 先给任务机会自行观察取消并释放资源 |
| `abort_all_tasks` | 取消 Turn 工作组 | 取得 active owner、取消 task、清待决状态并触发终态 |
| `handle_task_abort` | 任务中止主流程 | token、短暂等待、handle abort、marker、event 与 flush |
| `TurnAbortedEvent` | Turn 中止事件 | 携带 turn ID、原因和时间数据的结构化终态 |
| `TurnAbortReason` | 中止原因枚举 | Interrupted、Replaced、ReviewEnded 或 BudgetLimited |
| interrupted history marker | 中断历史标记 | 让后续模型知道上一段工作没有正常完成 |
| `steer_input` | 同 Turn 转向输入 | 将新输入加入 Regular Turn 的 pending queue，而不取消 Turn |
| input admission | 输入接纳 | 确认消息启动了新 Turn 或进入了当前 Turn |
| persistence acknowledgement | 持久化确认 | 确认已接纳消息真正写入 rollout |
| pending waiter | 待决等待者 | 通过 channel 等待审批、权限、用户输入或外部响应的任务 |
| `clear_pending_waiters` | 清待决等待者 | drop sender，使旧 Turn 的 receiver 退出 |
| acknowledgement barrier | 完成确认屏障 | App-server 等到 `TurnAborted` 后才回复普通 interrupt |
| generation fencing | 世代围栏 | 使用 expected turn ID 防止迟到请求误伤新 Turn |
| terminal outcome | 唯一终态 | 同一工作只能由 completed、failed、aborted 中一种收尾 |
| background terminal | 后台终端进程 | 已成为 Session-owned、可跨 Turn 存活的命令 |
| `CleanBackgroundTerminals` | 清后台进程操作 | 与 Turn interrupt 分离的显式进程清理 |
| `Op::Shutdown` | Session 关闭操作 | 取消活动工作并拆除 Session-owned runtime |
| `ShutdownComplete` | Session 关闭终态 | 表示显式 shutdown 流程已经完成 |
| teardown | 资源拆除 | 按 owner 和依赖顺序停止工作、关闭服务、释放资源 |
| cancellation safety | 取消安全 | 任意取消点都不会形成资源泄漏、双终态或不可解释状态 |

## User input admission 与 pending queue 源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| user message admission | 用户消息准入 | 决定一条 `Op::UserInput` 是启动新 Turn，还是 steer 现有 Turn |
| `UserMessageAdmission` | 用户消息准入结果 | 用 `Started { turn_id }` 或 `Steered { turn_id }` 明确实际归属 |
| submission ID | 提交编号 | 标识一次提交；新 Turn 路径中也成为 Turn ID |
| client user message ID | 客户端消息编号 | 跨 submission、pending queue、UI item 和 rollout 对齐同一用户消息 |
| pending input | 待处理输入 | 已归属活动 Turn，但尚未在安全点并入下一次模型 prompt 的输入 |
| durable admission | 耐久准入 | 同时等到 Started/Steered 结果与对应用户消息的 rollout flush |
| `Immediate` | 立即模式 | admission 一确定便回复，不等待持久化屏障 |
| `WaitingForAdmission` | 等待准入 | durable waiter 尚未收到 Turn 归属结果 |
| `Admitted` | 已准入 | Turn 归属已确定，仍等待 rollout persistence |
| `Persisted` | 已持久化 | 持久化已完成，仍等待 admission 信号 |
| pending admission guard | 待准入守卫 | 调用者取消或提前退出时，通过 Drop 自动移除 waiter |
| generation fence | 世代围栏 | 使用 `expectedTurnId` 阻止迟到 steer 误入新的活动 Turn |
| `TurnInput` | Turn 输入 | 统一表示 UserInput、ResponseItem 与 Agent 间通信 |
| Turn-local queue | Turn 局部队列 | 保存当前活动 Turn 的 steer 输入 |
| Session mailbox | Session 信箱 | 保存跨 Turn 调度的 Agent 间通信 |
| queue drain | 排空队列 | 在锁内原子取走当前全部 pending items |
| input activity watch | 输入活动观察 | 只负责以 Mailbox/Steer 唤醒等待者；真实数据仍在队列中 |
| persistence barrier | 持久化屏障 | `flush_rollout` 成功后才允许 durable waiter 收到成功 |
| `RejectedByHook` | 被 Hook 拒绝 | 消息已进入处理范围，但没有成为耐久、模型可见的用户输入 |
| `TaskEndedBeforePersistence` | 持久化前任务结束 | Turn 或 Session 先终止，等待者不再无限等待 |
| idle reservation | 空闲槽预留 | extension 自动启动 Turn 前先占位，防止并发启动竞态 |

## UserPromptSubmit Hook 与 additional context 源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| `UserPromptSubmit` | 用户提示提交 | 用户消息已归属 Turn、尚未记录为模型历史时运行的 Hook |
| Hook discovery | Hook 发现 | 从配置层、管理要求和 plugin 来源收集 Hook 定义 |
| Hook trust | Hook 信任 | 只有 Managed、Trusted 或明确绕过信任检查的命令才进入执行列表 |
| `ConfiguredHandler` | 可执行处理器 | 已通过 enabled、类型与 trust 检查的 command Hook |
| Hook preview | Hook 预览 | 执行前生成 Running summary 并发出 HookStarted |
| `FuturesUnordered` | 无序 Future 集合 | 并发运行同一事件的多个 command handlers |
| configured order | 配置顺序 | 并发完成后恢复的稳定汇总、事件与 context 顺序 |
| `decision:block` | 阻止决定 | 带非空 reason 时拒绝当前用户 prompt |
| `continue:false` | 停止处理 | 以 Stopped 状态终止本条 prompt 的另一种输出语法 |
| Hook fail-open | Hook 失败时放行 | timeout、spawn error、无效 JSON 等 Failed 状态默认不拒绝用户 |
| Hook output entry | Hook 输出条目 | Warning、Stop、Feedback、Context 或 Error 类型的 UI/审计信息 |
| Hook additional context | Hook 附加上下文 | 即使原 prompt 被 block，也可作为 developer message 保留 |
| client additional context | 客户端附加上下文 | App-server 请求传入的 keyed snapshot，分 Untrusted/Application |
| `HookAdditionalContext` | Hook 上下文 fragment | 无 marker、developer role 的模型历史项 |
| `AdditionalContextStore` | 客户端上下文存储 | 比较 keyed snapshot，只为变化值生成新 fragment |
| output spill | 输出溢写 | 超长 Hook context 保存到临时文件，模型只见预览和路径 |
| `additionalContextLimit` | 附加上下文限制 | 每 handler 的 spill 阈值；默认 2500 token，0 表示禁用 spill |
| blocked batch | 含阻止项的批次 | 仍逐项处理已 drain 输入，避免允许消息被连带丢失 |

## Turn/Item event projection 与客户端状态重建源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| event projection | 事件投影 | 把 Core EventMsg 转换为 App-server v2 Turn/ThreadItem 通知 |
| lifecycle event | 生命周期事件 | 描述 Turn/Item 的 started、progress 与 terminal 状态 |
| snapshot | 快照 | read/resume 或 history builder 物化出的某时刻状态 |
| delta | 增量 | 用于实时展示的一段变化，不保证等同最终 item 内容 |
| upsert | 更新或插入 | 按 item ID 用 completed snapshot 替换 started 状态 |
| canonical TurnItem | 规范 TurnItem | v2 客户端应优先消费的统一 item 生命周期 |
| legacy event | 旧事件 | 为 raw event 与 rollout 兼容继续保留的 begin/end 事件 |
| `TurnItemsView` | Turn items 完整度 | NotLoaded、Summary、Full 三种 payload 合同 |
| `ThreadHistoryBuilder` | Thread 历史构建器 | 让 live EventMsg 与 persisted RolloutItem 使用同一重建规则 |
| `TurnSummary` | 运行中 Turn 摘要 | 保存开始时间、最后错误、最后 AgentMessage 与命令去重集合 |
| active Turn snapshot | 活动 Turn 快照 | 补齐运行中 resume 里可能领先于持久读取的状态 |
| resume gap | 恢复缺口 | 读取历史和订阅通知之间可能漏事件的窗口 |
| listener command | listener 命令 | 与 next_event 在同一串行 owner 中处理的 resume/goal/resolve 操作 |
| listener generation | listener 世代 | 防止已被替换的 listener 继续投影陈旧事件 |
| `willRetry` | 将重试 | 区分中间 stream error 与最终 Turn failure |
| change set | 变化集 | 增量返回 changed items、changed turns 和 removed Turn IDs |
| rebase | 重设状态基线 | 客户端以新 snapshot 替换旧推导状态并选择性重放事件 |
| bounded event buffer | 有界事件缓冲 | TUI 为 thread 切换保存有限、可重放的通知 |

## Thread watch、订阅与多客户端路由源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| connection subscription | 连接订阅 | 决定一个 App-server connection 是否接收指定 thread 的通知 |
| subscriber | 订阅者 | 当前附着到某 thread 的 live connection |
| bidirectional index | 双向索引 | 同时保存 thread→connections 与 connection→threads，分别服务广播和断线清理 |
| live connection | 活连接 | 已初始化且尚未关闭，允许继续附着和路由的连接 |
| fan-out | 扇出广播 | listener 把一条 Core 事件发给当前所有 thread subscribers |
| `ThreadStateManager` | Thread 路由状态 owner | 保存订阅关系、per-thread state 与 listener command sender |
| `ThreadWatchManager` | Thread 运行状态 owner | 从 running、待决请求和错误等事实投影 `ThreadStatus` |
| runtime facts | 运行事实 | loaded、running、pending permission/user-input counters 与 system error |
| `ThreadWatchActiveGuard` | 活跃原因守卫 | 构造时增加待决请求计数，Drop 时异步减少 |
| active flag | 活跃原因标记 | WaitingOnApproval 或 WaitingOnUserInput 等公开状态原因 |
| watch channel | 最新状态观察通道 | 保存最新 boolean/status 并在变化时唤醒内部任务，不保存完整历史 |
| listener generation | listener 世代 | 防止退出中的旧 listener 清掉已经替换它的新 listener |
| generation fencing | 世代围栏 | 只有 generation 仍匹配的异步任务才能清理共享槽位 |
| implicit subscription | 隐式订阅 | start/resume/read/attach 等流程自动建立连接与 thread 的关系 |
| `thread/unsubscribe` | Thread 取消订阅 RPC | 只删除调用连接的路由关系，不中断 Turn，也不立即卸载 thread |
| unloading delay | 卸载宽限期 | 无订阅且非 Active 两条件均成立后等待的 30 分钟 |
| unload reservation | 卸载预订 | `pending_thread_unloads` 中排除重复卸载并阻挡竞态 attach 的占位 |
| authoritative recheck | 权威状态复核 | timer 到期后检查 watch 当前值和 Core `agent_status()`，而非盲目卸载 |
| `WatchRegistration` | 文件监听注册 | 保持配置/技能文件 watch 存活；与 connection subscription 不同 |
| unsubscribe / interrupt / unload | 退订 / 中断 / 卸载 | 分别改变通知路由、结束活动 Turn、拆除 thread runtime owner |

## App-server 反向请求与待决响应源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| reverse request | 反向请求 | App-server 主动请求客户端完成审批、输入或能力操作 |
| `ServerRequestPayload` | 无 ID 请求载荷 | 业务 handler 构造的 typed variant，由统一 owner 分配 request ID |
| `ServerRequest` | 完整服务器请求 | 已含 request ID，可序列化并发送到客户端的 typed enum |
| server request ID | 服务器请求编号 | App-server 用 AtomicI64 全局递增分配的一问一答关联键 |
| `ConnectionRequestId` | 连接请求编号 | connection ID 与客户端自选 request ID 的组合，用于 incoming client requests |
| `PendingCallbackEntry` | 待决回调条目 | 保存 oneshot sender、可选 thread ID 和可供重放的完整 request |
| pending callback map | 待决回调表 | server request ID 到唯一 callback entry 的 owner map |
| oneshot | 单次通道 | 在 request response/error/cancel 后只交付一个最终结果 |
| register-before-send | 先登记后发送 | 在消息可能被客户端回答前先建立 callback，消除 lost response 窗口 |
| first response wins | 首答胜出 | 多客户端回答同一 ID 时，首个原子移除 map entry 的结果生效 |
| late response | 迟到响应 | callback 已移除后到达的第二个 response，只记录 warning |
| typed decode | 强类型解码 | 把 JSON-RPC result 转成请求方法对应的 response struct/enum |
| fail closed | 失败时不放权 | 无效审批或权限结果默认拒绝、空权限或安全降级 |
| `serverRequest/resolved` | 请求已收口通知 | 告诉所有 thread subscribers 关闭同一 request ID 的 UI，不等于批准 |
| listener-ordered resolution | listener 排序的收口 | 通过 `ResolveServerRequest` command 把 resolved 通知放回 per-thread 串行 owner |
| `turnTransition` | Turn 转换原因 | 结构化取消 reason，阻止 waiter 向已经结束的旧 Turn 回填 fallback |
| pending replay | 待决请求重放 | resume 后以原 ID、原 payload 向新 connection 重发未决 request |
| callback drain | 回调排空 | Turn、unload 或 shutdown 时批量移除 pending entries 并终结 waiters |
| permission intersection | 权限交集 | 客户端 grant 只能落在 Core 原始 requested permissions 内 |
| request context | 请求上下文 | incoming client request 的 connection-scoped trace 与 response 状态，区别于反向 callback |

## Outgoing router、传输写入与交付边界源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| `OutgoingMessage` | 出站消息 | JSON 序列化前的 request、response、error 或 notification typed value |
| `OutgoingEnvelope` | 出站路由信封 | 用 `ToConnection` 或 `Broadcast` 为消息附加目标 |
| outbound router | 出站路由任务 | 维护 writer 表、执行初始化/capability 过滤并向每连接队列入队 |
| `OutboundConnectionState` | 发送侧连接投影 | 保存 initialized、实验能力、opt-out、writer 与 disconnect token |
| `QueuedOutgoingMessage` | 写队列项 | typed message 加可选 write-complete oneshot sender |
| global outgoing queue | 全局出站队列 | 容量 128，隔离业务 producer 与 outbound router |
| per-connection writer queue | 每连接写队列 | 隔离不同物理连接的积压和断开 |
| initialization gate | 初始化门 | Broadcast 只选择已经完成 initialize 的连接 |
| capability projection | 能力投影 | 按 connection 过滤实验通知或裁剪实验 request 字段 |
| notification opt-out | 通知退订 | 按 method 跳过客户端明确不接收的 notification |
| backpressure | 背压 | consumer 跟不上时等待、拒绝、丢弃或断开以限制生产速度 |
| slow consumer | 慢消费者 | 无法及时排空 writer queue 的客户端 |
| headroom | 队列余量 | WebSocket 为正常 burst 保留的额外有界容量 |
| framing | 消息分帧 | stdio 的 JSONL newline、WebSocket text frame 或 in-process typed item |
| serialization fallback | 序列化降级 | response 无法编码时，以相同 request ID 返回 internal error |
| write-complete barrier | 写处理屏障 | 等到 writer 处理队列项或 completion sender 被丢弃，不是客户端 ACK |
| enqueue | 入队 | channel 接受消息，不代表已经序列化或物理写出 |
| peer acknowledgement | 对端确认 | 客户端明确返回已完成某协议动作的消息，强于 writer completion |
| transport event | 传输事件 | ConnectionOpened/Closed 或带 connection ID 的 incoming message |
| overload response | 过载响应 | incoming request queue Full 时尝试返回的 `-32001` error |
| durable replay | 耐久重放 | 由 snapshot/pending 业务层恢复消息；普通 transport queue 不提供 |

## Incoming transport、envelope 分类与 handler 准入源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| incoming pipeline | 入站管线 | 从 stdio/WebSocket payload 到 typed handler 的完整接收路径 |
| physical framing | 物理分帧 | 用 stdio newline 或 WebSocket text frame 确定一条 payload 的边界 |
| wire envelope | 线上信封 | Request、Notification、Response、Error 四种外层消息 shape |
| `JSONRPCMessage` | JSON-RPC 外层消息 | 用 untagged enum 对四类 wire envelope 分类 |
| shape classification | 形状分类 | 根据 `id/method/result/error` 等字段识别 envelope，而非验证业务 method |
| raw request | 原始请求 | method 仍是 String、params 仍是 JSON Value 的 `JSONRPCRequest` |
| typed request | 强类型请求 | method 已成为 `ClientRequest` variant、params 已成为具体 Rust 类型 |
| second-stage decode | 第二阶段解码 | `ClientRequest::try_from` 对 method 与具体 params 做进一步验证 |
| incoming event queue | 入站事件队列 | transport 到主 processor loop 的容量 128 channel |
| differentiated overload | 差异化过载 | Full 时 request 快速拒绝，response/error 等待入队 |
| `ConnectionRequestId` | 连接请求编号 | connection ID 与客户端 request ID 的组合，避免多连接 ID 冲突 |
| connection owner map | 连接 owner 表 | 主循环保存 live `ConnectionState`，拒绝 close 后的迟到 event |
| connection session | 连接会话状态 | 保存 initialize 后的 client info、capabilities、opt-out 与 MCP extensions |
| initialize commit | 初始化提交 | 用 `OnceLock` 将连接从未初始化单向转成已初始化 |
| pre-initialize rejection | 初始化前拒绝 | 普通 typed request 在入队前得到 `Not initialized` |
| experimental gate | 实验能力门 | 未在 initialize opt in 的连接不能使用实验 method/field |
| handler admission | handler 准入 | typed request 通过协议门、资源排队和 connection gate 后才允许执行 |
| `ConnectionRpcGate` | 连接 RPC 门 | close 后阻止 queued handler 启动，并跟踪已开始 handler |
| accepting flag | 接纳标记 | gate 是否允许新 RPC 取得 TaskTracker token |
| TaskTracker token | 任务追踪凭证 | 表示一个已开始且 shutdown 应等待的 RPC handler |
| drop without polling | 不轮询即丢弃 | gate 关闭后 future body 完全不执行，避免 queued request 产生副作用 |
| RPC drain | RPC 排空 | close 后等待已取得 token 的 handler 自然完成 |
| response ownership | 回包所有权 | `Some` 由中央 dispatcher 回包，`None` 表示专门 processor 接管 |
| invalid request mapping | 无效请求映射 | raw→typed Serde error 在当前通用路径中转换为 `-32600` |
| late incoming event | 迟到入站事件 | connection 已从 owner map 移除后才被主循环看到的消息 |

## Initialize 握手、capability 协商与连接状态发布源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| initialize handshake | 初始化握手 | connection 从未初始化变成可提交普通请求的一次性 request/response 流程 |
| `InitializeParams` | 初始化参数 | 携带必需的 ClientInfo 和可选 capabilities |
| `ClientInfo` | 客户端信息 | name、可选 title 与 version；当前 session 保存 name/version |
| `InitializeCapabilities` | 初始化能力声明 | 客户端声明 experimental、attestation、notification opt-out 与 MCP extensions |
| conservative default | 保守默认 | 未声明 capability 时按 false/empty 处理，不猜测客户端支持 |
| client declaration | 客户端声明 | capability 的输入来源；仍需服务器验证、筛选和归一化 |
| accepted capability list | 已接受能力清单 | 当前 InitializeResponse 不提供这种双向协商结果 |
| capability normalization | 能力归一化 | Option/default、legacy form 与 extension map 转成统一 runtime state |
| MCP extension allowlist | MCP 扩展允许表 | 只保留 `openai/form` 和 `io.modelcontextprotocol/ui` |
| legacy form capability | 旧版表单能力 | 归一为 value `{}` 的 `openai/form` extension |
| connection session commit | 连接会话提交 | `OnceLock::set` 一次发布完整 `InitializedConnectionSessionState` |
| first writer wins | 首写胜出 | 并发 initialize 只有首个 `OnceLock::set` 成功 |
| normalized session state | 归一化会话状态 | 保存 HashSet opt-out、filtered extensions、identity 与 boolean capabilities |
| header-value validation | Header 值验证 | client name 进入 originator/UA 前拒绝非法控制字符 |
| originator | 发起方标识 | 上游 HTTP 请求使用的进程级宿主身份，首次成功设置后固定 |
| non-originating client | 非发起客户端 | daemon/backend 名称不修改进程 originator 与 UA suffix |
| `USER_AGENT_SUFFIX` | User-Agent 后缀 | 进程级保存的 `clientName; clientVersion` metadata |
| residency requirement | 数据驻留要求 | 由服务器 requirements/config 写入默认 HTTP client metadata |
| connection-local state | 连接局部状态 | 每连接独立的 initialized session 与能力 |
| process-global state | 进程全局状态 | 多连接共享的 originator、UA suffix、residency metadata |
| capability projection | 能力投影 | 将权威 session 的 experimental/opt-out 复制给 outbound router |
| bootstrap notification | 启动补齐通知 | 初始化后定向发送当前 config warnings 与 remote-control status |
| outbound-ready | 出站就绪 | 普通 Broadcast 可以把该连接选为目标 |
| two-stage publication | 两阶段发布 | 先提交权威 session，再准备各 owner 投影并发布 outbound ready |
| targeted bootstrap | 定向启动补齐 | 绕过 Broadcast ready gate，但仍经过 capability/opt-out filter |
| live connection capability | 活连接能力 | thread state 中登记的 request-attestation 等连接级 metadata |
| attestation responder | 证明响应方 | 订阅目标 thread、声明能力且被选中回答 `attestation/generate` 的客户端 |
| state scope promotion | 状态作用域升级 | 把 connection client info/extensions 快照复制到更长寿的 thread |
| startup hint | 启动提示 | stdio 提前提取 client name 的旁路，不代表正式 session commit |
| uncertain handshake outcome | 不确定握手结果 | 客户端 timeout 时服务器可能已提交，不能假设同连接重试安全 |

## Request tracing、context ownership 与延迟回包源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| distributed trace | 分布式调用追踪 | 用共享 trace ID 和 parent chain 串联 client、App-server、Core 等跨边界工作 |
| span | 工作区间 | 表示一次 request、Core operation 或发送阶段的开始、结束、属性和父子关系 |
| `W3cTraceContext` | W3C 追踪载体 | 保存可选 traceparent/tracestate，跨 wire 或异步 submission handoff 传播 |
| `traceparent` | 追踪父级头 | 提供 trace ID、直接 parent span ID、flags；建立 parent 的必需主载体 |
| `tracestate` | 追踪补充状态 | 保存 vendor-specific 状态，不能在缺少 traceparent 时单独建立 parent |
| remote parent | 远端父 span | 从客户端或外部进程 carrier 恢复的上游 parent |
| `app_server.request` | App-server 请求 span | 统一记录 JSON-RPC method、transport、request/connection/client/turn identity |
| span enrichment | Span 信息增补 | initialize 前从 params 记录 client info，Turn 创建后再记录 `turn.id` |
| `RequestContext` | 入站请求上下文 | 保存 compound request ID、request span 和原 parent fallback；不是模型上下文 |
| request context registry | 请求上下文注册表 | 保存尚未由最终 response/error 终结的 incoming requests |
| register-before-run | 先注册再执行 | 消除快速 response/error 先于 tracing context 登记的竞态 |
| terminal take | 终态取走 | send_response/send_error 从 registry remove context 后带着 span 发起出站入队 |
| span-first propagation | Span 优先传播 | 向 Core 导出当前 request span，使 Core 成为它的后代；失败才回退原 parent |
| async handoff carrier | 异步交接载体 | `Submission.trace` 随业务消息跨 channel 进入另一个 task owner |
| delayed response | 延迟回包 | handler 先返回 None，由 background task 或 Turn 终态 listener 以后回应 |
| pending interrupt | 待确认中断 | 已向 Core 提交 interrupt，但仍等待 TurnComplete/TurnAborted 才回复的 request |
| trace topology | Trace 拓扑 | 不只比较 trace ID，还验证 remote parent 与祖先/后代关系 |
| context cleanup | 上下文清理 | response/error 正常 take，或 connection close 批量 purge 遗留 contexts |
| `responsesapi_client_metadata` | Responses API 客户端元数据 | `Op::UserInput` 的业务 metadata，和 W3C trace carrier 是两条独立数据线 |
| high cardinality | 高基数 | request/connection/thread/turn ID 等拥有大量不同取值，适合 trace 关联而需谨慎用于 metric label |

## Connection teardown、resource ownership 与有界排空源码精读补充术语

| 术语 | 中文理解 | 在 Codex 当前源码中的职责 |
|---|---|---|
| teardown | 运行时拆除 | 停止入口、撤销 ownership、发停止信号、等待终态并释放资源的完整协议 |
| logical removal | 逻辑移除 | 先从 live/routable map 删除 connection，阻止 teardown 中途重新准入工作 |
| physical cleanup | 物理资源清理 | 后续关闭 writer、watch、process 和其他真实 runtime resources |
| connection-scoped ownership | 连接作用域所有权 | key 含 connection ID，只清目标 peer 的 request/watch/process/subscription |
| `ConnectionRpcGate` | 连接 RPC 准入门 | close 后 drop 尚未开始的 handler，TaskTracker 继续统计已开始 handler |
| gate token | 准入追踪凭证 | 表示 handler 已越过 admission boundary，shutdown 应等待它完成 |
| close / shutdown | 关闭接纳 / 关闭并等待 | gate close 立即停止新准入；shutdown 还等待 inflight token 归零 |
| bounded drain | 有界排空 | 停止新增后只在 deadline 内等待已有工作，避免无限卡住 |
| `ConnectionCleanupTasks` | 连接清理任务 owner | 用 JoinSet 并发管理、reap、drain 或 abort per-connection cleanup futures |
| reap | 回收 task 结果 | 从 JoinSet 取得已结束 task，记录非取消失败并释放 task bookkeeping |
| disconnect token | 断开令牌 | WebSocket inbound/outbound loops 与 router state 共享的 CancellationToken |
| `OutboundControlEvent` | 出站控制事件 | Opened/Closed/DisconnectAll 在独立 router owner 中维护 writer 集合 |
| control-plane priority | 控制面优先级 | biased select 在普通 outgoing envelope 前优先处理连接状态变化 |
| request context purge | 请求上下文清除 | RPC drain 后按 connection ID 删除未终结 incoming contexts |
| reverse callback lifetime | 反向回调生命周期 | server→client pending request 可能跨连接，由 Turn/thread/timeout/runtime owner 收口 |
| RAII watch cleanup | 守卫式监听清理 | drop WatchEntry 中 subscriber/registration/oneshot sender，使 watch task 退出 |
| ownership transfer before await | await 前转移所有权 | mutex 内 remove sessions，锁外发送 Terminate/Kill，避免跨 await 占锁 |
| `Terminate` / `Kill` control | 终止/杀进程控制消息 | 向 command/process runner 发布停止意图，不等于 OS process 已退出确认 |
| connection generation fence | 连接世代围栏 | 从 live_connections 删除后，迟到 auto-subscribe 不能复活旧连接 |
| graceful restart drain | 优雅重启排空 | 第一次 signal 等 running assistant Turns 归零，再断开 connections 和排空 owners |
| forceable signal | 可强制信号 | 第二次 Ctrl-C/SIGTERM 将 shutdown 从 requested 升级为 forced |
| graceful-only signal | 仅优雅信号 | 重复 SIGHUP 不进入 forced，继续等待 running Turns |
| `ThreadShutdownReport` | Thread 关闭报告 | 把全部 Core thread 结果分类为 completed、submit_failed、timed_out |
| explicit router shutdown | 显式路由器关闭 | in-process 用 oneshot 退出 outbound router，不只依赖最后 sender drop |
| shutdown acknowledgement | 关闭确认 | embedded runtime 完成约定 cleanup/flush 阶段后返回的 oneshot ack |
| timeout vs abort | 超时与中止 | timeout 只停止等待；JoinHandle abort 明确停止 future 的后续 polling |

## Analytics、telemetry projection 与业务终态源码精读补充术语

| 名词或短语 | 中文理解 | 在本系列中的具体含义 |
|---|---|---|
| analytics | 产品分析数据 | 为理解功能使用情况和结果而生成的结构化事件；不是普通运行日志，也不等于完整 trace |
| telemetry | 遥测 | 程序运行时向观测系统提供的信号总称，可以包括 log、metric、trace 和 analytics event |
| `AnalyticsFact` | 分析事实 | App-server 或 Core 在某个局部时刻已经知道的一小块事实，还不一定足以形成最终事件 |
| `TrackEventRequest` | 待发送分析事件 | reducer 拼装完成、可以交给过滤和发送层的外部事件结构 |
| reducer | 归并器/状态归约器 | 串行接收 facts，保存关联状态，并在条件满足时投影出 events 的唯一 owner |
| projection state | 投影状态 | 为等待后续事实而暂存的 request、thread、turn、tool、review 等关联数据 |
| correlation key | 关联键 | 用 connection ID、request ID、thread ID、turn ID 或 item ID 把分散事实认作同一件事 |
| readiness gate | 就绪门槛 | reducer 判断一条 event 所需关键事实是否已经齐全的条件 |
| selective tracking | 选择性记录 | track API 只接受有分析价值的 request/response/notification variant，而不是复制所有协议流量 |
| best-effort | 尽力而为 | analytics 可以因队列满、缺上下文、权限过滤或网络失败而丢失，但不能阻塞或破坏业务 |
| try-send queue | 尝试发送队列 | 使用非阻塞 `try_send` 写入的有界 channel；容量耗尽时立即放弃该 fact |
| response track boundary | 响应记录边界 | `track_response` 发生在出站 channel send 之前，只证明服务端开始构造/发送响应，不证明客户端收到 |
| thread originator | Thread 发起产品 | 标记 Thread 真正来源产品的 client ID，可覆盖连接级 product client ID 而保留其他连接元数据 |
| business terminal | 业务终态 | Turn、tool 或 review 在领域语义上完成、失败或中断，而非仅仅完成某次 RPC 回包 |
| protocol terminal | 协议终态 | 一次 request 收到 response/error 或被取消；它可能早于、晚于或不同于业务终态 |
| tool item pairing | 工具 item 配对 | 用 thread、turn、item 标识将 `ItemStarted` 与 `ItemCompleted` 合并成完整工具事件 |
| pending review | 待决审查 | server→client review/approval request 已发出、尚待 response 或 abort 的 reducer 状态 |
| typed error taxonomy | 类型化错误分类 | 用 enum/variant 表示拒绝原因，不依赖解析可能变化的用户可见错误字符串 |
| auth filter | 鉴权过滤 | 按登录方式和后端决定哪些 analytics event 允许发送；API key 模式只保留代码明确允许的子集 |
| queue barrier | 队列屏障 | `flush` 消息排在先前 facts 之后，ack 表示这些已入队工作处理完，而不是所有业务自动完整 |
| flushable event | 可刷出事件 | reducer 在 flush 时有足够语义安全生成的 pending event；不完整 Turn/review 不会被伪造终态 |
| capture file | 捕获文件 | 调试时把 event 写到本地文件的目的地，可用于检查 payload，且可配置为不发网络 |
| missing context drop | 缺上下文丢弃 | reducer 无法找到 connection/thread/turn 等关联信息时记录 warning 并放弃观测事件 |
| dedupe key | 去重键 | 用于限制同一 Turn 中重复 app/plugin 事件的组合键；达到上限后会清理集合以控制内存 |

## Error taxonomy、structured payload 与恢复策略源码精读补充术语

| 名词或短语 | 中文理解 | 在本系列中的具体含义 |
|---|---|---|
| error taxonomy | 错误分类体系 | 按失败对象、生命周期、恢复责任和公开稳定性组织错误，而不只保存一段 message |
| structured payload | 结构化错误载荷 | 程序可稳定读取的 code、enum tag、`data` 和字段 |
| `ApiError` | API 层错误 | provider/Responses API 返回并由 API bridge 解释的失败 |
| `TransportError` | 传输层错误 | HTTP、timeout、network、stream 或 transport retry limit 等失败 |
| `CodexErr` | Core 错误包装器 | 将完整 `CodexErrorDetails` 和可选 `retry_delay` 放在同一对象中 |
| `CodexErrorDetails` | Core 错误详情 | 带内部诊断 payload 的完整错误 enum |
| `CodexErrKind` | 无载荷错误类别 | 去掉具体路径、文本和 ID 后用于 analytics 的低基数 discriminant |
| `CodexErrorInfo` | 客户端安全错误类别 | 跨 Core/App-server 边界传播、供客户端决定产品动作的稳定粗分类 |
| lossy error mapping | 有损错误映射 | 对外转换时合并内部 variant，主动丢弃不应成为 API 契约的实现细节 |
| `ErrorEvent` | 终态错误事件 | 通常会把当前 Turn 导向 failed 的 Core protocol event |
| `StreamErrorEvent` | 流中间错误事件 | Core 自动恢复期间产生的临时 event，不应终结 Turn |
| `TurnError` | Turn 业务错误 | V2 中由 message、`codexErrorInfo` 和 `additionalDetails` 组成的错误对象 |
| `ErrorNotification` | Turn 错误通知 | 带 thread/turn ID 和 `willRetry` 的异步通知 |
| `JSONRPCErrorError` | JSON-RPC 错误主体 | 以 code、message、可选 data 终结一次 request 的协议对象 |
| `willRetry` | 服务端将重试 | true 表示 Core/App-server 已承担恢复责任，客户端应继续等待而非重复提交 |
| retry owner | 重试责任方 | 实际重新发起工作的客户端、Core、guardian 或 compaction workflow |
| retry unit | 重试单位 | 被再次执行的是原 RPC、sampling stream、transport、review 还是 compaction |
| retry budget | 重试预算 | 自动恢复允许消耗的最大次数或 deadline |
| server-provided delay | 服务端建议延迟 | 上游给出的下次尝试等待时间，优先于本地 backoff |
| exponential backoff | 指数退避 | 失败次数增加时按倍数延长 retry delay |
| jitter | 随机抖动 | 在 delay 上加小范围随机值，避免大量客户端同步重试形成惊群 |
| fallback transport | 后备传输 | 主传输达到重试边界后切换到备用方案，例如 WebSocket 转 HTTPS |
| `affects_turn_status` | 是否影响 Turn 状态 | 判断一个 error 是 active Turn 的致命失败还是旁路操作失败 |
| terminal error | 终态错误 | 不再由当前 workflow 自动恢复，并随 Turn terminal state 保存的错误 |
| request terminal | 请求终态 | 某个 JSON-RPC request 收到 result 或 error |
| business terminal | 业务终态 | Turn 最终进入 completed、failed 或 interrupted |
| non-fatal side-operation error | 非致命旁路错误 | steer、rollback 等操作失败，但原 active Turn 仍可继续 |
| ingress overload | 入口过载 | incoming queue 已满，request 尚未进入 MessageProcessor/handler |
| reconciliation | 状态对账 | response 丢失后查询服务端实际状态，避免盲目重复有副作用操作 |
| history replay | 历史回放 | 从 rollout terminal/error events 重建 Turn status 和 error |
| thundering herd | 惊群 | 大量客户端同时重试，导致刚恢复的服务再次过载 |

## 查词时的三个问题

看到不认识的代码名时，依次问：

1. 字面翻译是什么？
2. 它是数据、动作、状态，还是负责协调的对象？
3. 它的作用域是 request、step、turn、thread，还是整个进程？

仅靠翻译常常不够。例如 `Session` 翻译成“会话”以后，仍需要继续确认它在这段代码里由谁创建、活多久、拥有哪些状态。
