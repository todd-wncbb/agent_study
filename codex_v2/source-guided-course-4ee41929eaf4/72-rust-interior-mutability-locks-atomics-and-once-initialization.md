# 72：Rust 内部可变性、锁、原子类型与一次初始化

> 源码基线：`4ee41929eaf4`。本章解释 `Arc<Mutex<HashMap<...>>>` 每一层的职责，以及何时应换成 RwLock、Atomic 或 OnceLock。

## 1. 本章解决什么问题

共享状态为什么能通过 `&self` 修改？同步 Mutex 与 Tokio Mutex 有何区别？AtomicBool 的 Relaxed/Acquire/Release 在保证什么？为什么 OnceLock 不是只读 Mutex？

## 2. 先说人话：共享 Owner 不等于共享修改权

Arc 只让多个 owner 共同延长 value 寿命。要修改共享 value，还需 Mutex、RwLock、Atomic 等同步机制。

## 3. 外部可变性

普通 `&mut T` 表示调用者持有独占修改权。编译器在静态作用域内证明没有其他访问者。

## 4. 内部可变性

某些类型允许通过 `&self` 修改内部状态，把独占检查移动到运行时或硬件原子操作。这叫 interior mutability。

## 5. `UnsafeCell<T>`

它是 Rust 内部可变性的底层原语。Mutex、RwLock、Cell 等在其上提供安全 API；业务代码通常不应直接使用 UnsafeCell。

## 6. `Cell<T>`

适合单线程、小型 Copy value 的 get/set。它不借出内部引用，常用于计数或 flag，但不是跨线程同步工具。

## 7. `RefCell<T>`

单线程运行时执行 shared/mutable borrow rule。违反规则会 panic，而非编译失败。

## 8. RefCell 的 Borrow Guard

`borrow()`/`borrow_mut()` 返回 guard；guard drop 时释放运行时借用。尽量缩短 scope，避免意外重入导致 panic。

## 9. `Rc<RefCell<T>>`

常见于单线程共享可变图。它通常既非 Send 也非 Sync，不能直接进入多线程 Tokio spawn。

## 10. `Mutex<T>`

同一时刻只允许一个持锁者访问 T。`lock` 返回 guard，guard 的 Drop 释放锁。

## 11. Mutex 保护的是不变量

不要只说“保护 HashMap”；应说明哪些字段必须一起变化。例如 queue 创建、首项入队和是否启动 drainer 必须在同一临界区决定。

## 12. Critical Section

从取得 guard 到释放 guard 的代码段。越短越少竞争，但不能短到破坏原子业务不变量。

## 13. `std::sync::Mutex`

Lock 会阻塞当前 OS thread。适合短小、不跨 await 的同步临界区；poisoning 会记录持锁线程 panic。

## 14. `tokio::sync::Mutex`

Lock 是 async，可在等待时让出 executor；guard 可按其 API 跨 await，但这通常扩大串行区，应谨慎。

## 15. 选择同步还是异步 Mutex

关键不是代码是否在 async fn，而是 guard 是否必须跨 await、临界区是否会阻塞。短纯内存更新可使用 std 锁；需要异步持锁语义才考虑 Tokio 锁。

## 16. 不要持 std Mutex 跨 Await

它可能阻塞 executor thread，Future 也可能变成 non-Send。先 clone/move 出所需 owned state，drop guard，再 await。

## 17. Tokio Guard 也不应随意跨 Await

技术上允许不代表业务上合适。网络或长任务期间持锁会让其他 task 长时间排队，造成队首阻塞。

## 18. 请求队列案例

Request serialization 在锁内 pop 出可运行 batch，然后离开 block 释放锁，锁外 `join_all(...).await`。这是标准 lock-snapshot-act 模式。

## 19. Cancellation Safety

若 Future 在 await 点被 drop，锁 guard 会释放，但临界区外的多步业务更新可能只完成一半。锁安全不等于操作取消安全。

## 20. Poisoning

std Mutex/RwLock 持锁 panic 后后续 lock 可得到 PoisonError，提示内部不变量可能损坏。盲目 `into_inner` 等于主动接受风险。

## 21. Tokio Mutex 不使用同样 Poisoning 模型

Panic 时 guard drop 释放锁，但 T 可能处于部分更新状态。调用方仍要设计更新顺序和不变量。

## 22. `RwLock<T>`

允许多个 reader 或一个 writer。适合读多写少且读临界区有实际并行价值的状态。

## 23. RwLock 不保证一定更快

它有更复杂的协调和公平策略；写频繁或临界区很小时可能不如 Mutex。用 workload 和 profile 决定。

## 24. Writer Starvation

锁实现的公平策略影响 writer 是否长期等不到。不要假设所有平台和同步/异步 RwLock 行为完全相同。

## 25. Read-to-write Upgrade

持 read guard 后再请求 write lock 容易死锁。通常释放 read、获取 write、重新检查条件；这会形成 check/recheck 模式。

## 26. `Arc<Mutex<T>>`

逐层翻译：Arc 共享 ownership；Mutex 串行修改；T 是受保护状态。Arc 不加锁，Mutex 不负责 owner 寿命。

## 27. Clone Arc 不 Clone T

`Arc::clone` 增加引用计数，多个 Arc 指向同一 Mutex/T。想要数据快照需在锁内显式 clone T 或部分字段。

## 28. Lock Ordering

若代码可能同时持 A、B 两把锁，所有路径必须使用一致顺序，否则可能 A 等 B、B 等 A。

## 29. 避免 Nested Locks

能用单 owner task、消息传递、先取 snapshot 再释放第一把锁时，通常比嵌套锁更易证明。

## 30. Actor/Owner Task

让一个 task 独占状态，其他 task 通过 channel 请求变更，可把锁竞态转成消息顺序问题；仍需容量、取消和响应协议。

## 31. Atomic 类型

AtomicBool/Usize/U64 提供不可撕裂的单值 load/store/read-modify-write。适合 flag、计数器、ID 和一次 winner 选择。

## 32. Atomic 不适合多字段不变量

两个 Atomic 独立安全，不代表组合 snapshot 一致。若字段必须共同变化，使用锁、版本协议或封装的原子状态机。

## 33. `load` 与 `store`

Load 读取，store 写入。都必须指定 memory ordering，因为硬件/编译器可能重排其他内存操作。

## 34. `fetch_add` / `fetch_sub`

原子修改并返回旧值。适合 ID/计数，但仍需处理溢出、配对 decrement 和业务上限。

## 35. `swap`

原子替换并返回旧值。Codex 用 `flag.swap(true, ...)` 判断“我是否是第一个到达终态的人”。

## 36. Compare-and-swap

`compare_exchange(expected, new, ...)` 只有当前值等于 expected 才更新，并返回成功/当前值。适合竞争式状态转换。

## 37. CAS Loop

复杂原子更新常 load→计算→compare_exchange，失败则用新 current 重试。循环必须重算条件和处理饥饿。

## 38. `Relaxed`

只保证该原子值自身操作原子性，不为其他内存建立同步顺序。统计计数或独立取消 flag 常可用，但必须能解释为何其他数据无需同步。

## 39. `Release` 与 `Acquire`

发布方在 store Release 前的写入，对读取到该值的 Acquire 方可见。它常表达“初始化数据完成后发布 ready flag”。

## 40. `AcqRel`

用于读改写操作，同时承担 Acquire 和 Release 约束。Codex 的终态 winner/活动计数中可见。

## 41. `SeqCst`

提供最强的单一全局顺序模型，更易推理但可能限制优化。测试计数常用它；生产逻辑仍应按所需保证选择。

## 42. Ordering 不是性能猜谜

不能通过“看起来只是 bool”随意改 Relaxed。先写 happens-before 需求；无法证明时应让熟悉内存模型的人审查。

## 43. Cancellation Flag 案例

Fuzzy search 共享 `Arc<AtomicBool>`；替换搜索时设置旧 flag，新搜索在计算点 load 并退出。它表达协作取消，不强制终止线程。

## 44. Winner Flag 案例

并行工具用 `swap(true, AcqRel)` 保证只有一个 terminal outcome 被接受。Atomic 使“检查并设置”成为一次不可分割操作。

## 45. Counter 与 Permit

Atomic counter 若先判断 `< max` 再 fetch_add，多个线程可能同时通过。需要 CAS、Semaphore 或锁把“检查+占位”原子化。

## 46. RAII Decrement Guard

成功占位后创建 guard，Drop 时 fetch_sub，覆盖正常、错误、取消路径。仍要确保 guard 只创建一次且不被遗忘。

## 47. `OnceLock<T>`

初始为空，成功 set 后只能保持一个 value，并允许共享读取。适合“生命周期中只确定一次”的状态。

## 48. OnceLock 不是 Mutex 替代品

它不支持反复更新。若值需要刷新、撤销或多阶段变化，应使用锁、watch 或专门状态机。

## 49. Connection Initialize 案例

Message processor 用 OnceLock 保存 initialized connection state，类型直接表达“最多成功初始化一次”。重复初始化不应静默覆盖。

## 50. `get_or_init`

首次调用执行 closure 并保存结果，之后返回同一引用。同步 initializer 不应做异步等待。

## 51. `LazyLock<T>`

带初始化 closure 的 lazy static/field，首次访问才构造。Agent role 内置配置使用 LazyLock<BTreeMap<...>>。

## 52. LazyLock 与 OnceLock

LazyLock 在创建时就绑定 initializer；OnceLock 允许稍后从外部 set 或 get_or_init，适合初始化值来源不同的场景。

## 53. Tokio `OnceCell`

支持异步初始化，适合创建 Code Mode session 等必须 await 的资源。并发调用者共享一次初始化结果/过程语义。

## 54. 初始化失败

确认 API 在 initializer 返回 Err 或 panic 后能否重试、是否缓存失败。不同 Once/Lazy API 和包装逻辑可能不同。

## 55. 初始化取消

异步 initializer 被取消时，cell 是否保持未初始化、其他 waiter 如何继续，是必须测试的生命周期行为。

## 56. 锁与 Channel 的区别

锁让调用者直接进入共享状态；channel 把命令交给 owner。前者适合短原地更新，后者适合串行生命周期和异步动作。

## 57. Watch Channel

保存“最新值”并通知观察者，适合状态投影；它不是完整事件日志，慢 receiver 可能看不到每个中间状态。

## 58. Notify

表达“某事发生/条件可能改变”，通常不携带完整状态。醒来后应重新检查受保护条件，避免把通知当数据本身。

## 59. Semaphore

限制并发 permit 数，不保护任意 T。它适合资源配额；Mutex 适合互斥状态，二者不可仅因“都能等待”互换。

## 60. Barrier

让固定数量参与者在某点汇合，常用于测试/阶段同步；不适合动态生产者消费者队列。

## 61. 并发原语选择表

| 需求 | 常见选择 |
|---|---|
| 单线程 Copy flag | Cell |
| 单线程运行时借用 | RefCell |
| 短同步互斥状态 | std Mutex |
| 必须异步等待的互斥状态 | Tokio Mutex |
| 多读单写 | RwLock |
| 独立 flag/counter/CAS | Atomic |
| 同步只初始化一次 | OnceLock/LazyLock |
| 异步只初始化一次 | Tokio OnceCell |
| 限制 in-flight | Semaphore |
| 单 owner 串行状态 | Channel + owner task |

## 62. 常见错误：持锁跨网络 Await

其他任务全部等待，取消时还可能留下部分业务更新。提取 owned request/snapshot 后释放锁，再执行外部动作。

## 63. 常见错误：复制两步 Check-and-set

`if !flag.load() { flag.store(true) }` 不是原子 check-and-set。使用 swap 或 compare_exchange。

## 64. 常见错误：Atomic 计数代表完整事实

计数与实际 map/task 生命周期若不在同一协议更新，会漂移。提供对账、RAII guard 和整体对象测试。

## 65. 常见错误：锁粒度过大或过小

过大导致竞争，过小导致不变量跨锁暴露。先定义必须原子观察/更新的字段集合，再决定粒度。

## 66. 常见错误：OnceLock 隐藏可重置需求

测试、logout、provider refresh 或 thread resume 若需要重新配置，一次初始化模型可能根本不合适。

## 67. 常见错误：用 Sleep 修并发测试

Sleep 依赖机器时序。使用 Barrier、Notify、channel 或可控 fake 明确建立 happens-before，再断言状态。

## 68. 测试并发不变量

覆盖两个竞争写者、取消 waiter、initializer 失败、重复 set、终态只一次、guard drop decrement、无 receiver 和 shutdown。比较最终完整 state，而非只看单个 counter。

## 69. 源码检查点

1. `codex-rs/app-server/src/request_serialization.rs`：Tokio Mutex、锁内 batch、锁外 await。
2. `codex-rs/app-server/src/fuzzy_file_search.rs`：Relaxed cancellation flag。
3. `codex-rs/core/src/tools/parallel.rs`：terminal outcome AtomicBool/swap。
4. `codex-rs/core/src/agent/control/execution.rs`：Atomic counter、RAII decrement、OnceLock limit。
5. `codex-rs/app-server/src/message_processor.rs`：OnceLock connection initialization。
6. `codex-rs/core/src/agent/role.rs`：LazyLock 内置配置。
7. `codex-rs/core/src/tools/code_mode/mod.rs`：Tokio OnceCell async session。
8. `codex-rs/app-server/src/config_manager.rs`：std RwLock 的同步配置投影。

## 70. 搜索命令

```bash
rg -n 'std::sync::Mutex|tokio::sync::Mutex|RwLock' codex-rs
rg -n 'Atomic(Bool|Usize|U64)|Ordering::|compare_exchange' codex-rs
rg -n 'OnceLock|LazyLock|OnceCell' codex-rs
```

## 71. 理解检查与答案

1. Arc 与 Mutex 分别解决什么？共享 ownership；互斥修改。
2. Async fn 是否必然用 Tokio Mutex？否，看 guard 是否必须异步等待/跨 await。
3. Atomic 能否保护多字段一致性？通常不能。
4. swap 相比 load+store 多了什么？不可分割的 check-and-set。
5. OnceLock 适合可刷新配置吗？通常不适合。
6. 为什么锁内取 batch、锁外 await？缩短临界区并避免持锁等待外部工作。

## 72. 本章词汇表

| 术语 | 直译或展开 | 实际含义 |
|---|---|---|
| Interior mutability | 内部可变性 | 通过共享引用由安全 wrapper 控制修改 |
| UnsafeCell / Cell / RefCell | 不安全单元/值单元/引用单元 | 底层原语、Copy get/set、单线程运行时借用 |
| Mutex / guard / critical section | 互斥锁/守卫/临界区 | 单写访问、RAII 解锁及其保护代码范围 |
| Poisoning | 中毒 | 持 std 锁 panic 后标记不变量可能损坏 |
| RwLock / starvation | 读写锁/饥饿 | 多 reader 单 writer，以及某方长期等不到锁 |
| Atomic / CAS | 原子类型/比较交换 | 单值不可分割操作和条件更新 |
| Relaxed / Acquire / Release | 宽松/获取/发布 | 仅原子值、读取同步、写入发布的内存顺序 |
| AcqRel / SeqCst | 获取发布/顺序一致 | 读改写双向约束与最强全局顺序模型 |
| Happens-before | 先发生于 | 保证一方写入对另一方可见的内存顺序关系 |
| OnceLock / LazyLock / OnceCell | 一次锁/惰性锁/一次单元 | 同步一次赋值、绑定 lazy initializer、异步一次初始化 |
| Semaphore / permit | 信号量/许可 | 限制同时进行工作的数量及其 RAII 配额 |
| Winner flag | 胜者标志 | 让并发结果只有第一个取得终态提交权 |
| Lock ordering | 加锁顺序 | 多锁路径统一次序以避免循环等待 |
| Cancellation safety | 取消安全 | Future 任意 await 被 drop 后状态仍满足不变量 |

## 73. 常见误解速查

| 误解 | 更准确的理解 |
|---|---|
| Arc 让内部自动可修改 | Arc 只共享 ownership |
| Async 代码都用 Tokio Mutex | 短同步临界区可能更适合 std Mutex |
| Tokio Mutex 跨 await 就一定好 | 会扩大串行区，应有明确需要 |
| AtomicBool 没有内存顺序问题 | 与其他数据的可见性仍取决于 Ordering |
| 两个 Atomic 构成原子快照 | 各自原子不等于组合一致 |
| OnceLock 是更快的 Mutex | 它表达最多初始化一次，不支持普通更新 |
| Guard drop 说明业务完整 | 只说明锁释放，不保证多步操作取消安全 |

## 74. 本章结论

共享状态要分四层阅读：Arc 决定 owner，锁/Atomic 决定并发访问，容器/字段决定数据形状，mutation protocol 决定业务不变量。锁内只做必须原子的读取和更新，外部 I/O 尽量在 guard 释放后进行；Atomic 只用于能被单值协议准确表达的 flag/counter/CAS；OnceLock/LazyLock/OnceCell 只用于真正“一生确定一次”的值。

并发正确性不等于“没有 data race”。还要证明锁顺序、容量、取消、panic、重复终态、counter 配对、初始化失败与 shutdown 路径。最可靠的代码能明确回答：谁拥有状态、哪把锁保护哪些字段、哪个操作是线性化点，以及 Future 在每个 await 被取消后留下什么。
