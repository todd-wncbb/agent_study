# 77：Rust Future、Poll、Waker、Async 状态机与取消

> 源码基线：`4ee41929eaf4`。本章解决“async 函数何时真正执行、await 为什么能暂停、Pending 后怎样再次运行，以及竞争分支被放弃时发生什么”。

## 1. 本章解决什么问题

会写 `.await` 不等于能读异步系统。排查“任务卡住、取消没生效、并发变串行、Future not Send”时，需要知道 Future 是惰性状态机，executor 通过 poll 推进它，Waker 只负责请求再次调度，而取消通常就是停止 poll 并 drop 状态机。

## 2. 先说人话：Future 是尚未完成的计算

Future 不是后台线程，也不是已经算好的结果。它是一个 value，保存“计算目前进行到哪里、继续运行需要哪些局部状态”，由 executor 反复询问是否完成。

## 3. `async fn` 返回 Future

```rust
async fn load() -> Result<Data> {
    // async body
}

let future = load();
```

调用 async fn 会创建 Future；body 中的异步计算通常要等 Future 被 poll 才推进。仅把 Future 存入变量而从不 await、spawn 或 poll，它不会自行完成。

## 4. Argument 先求值，Body 后推进

调用表达式的参数仍会先计算并移入生成的 Future；但 async body 中的语句由后续 poll 执行。区分这两步有助于判断副作用何时发生。

## 5. `Future` Trait

```rust
trait Future {
    type Output;
    fn poll(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Self::Output>;
}
```

Output 是最终结果类型，poll 每次尝试让计算尽可能向前推进。

## 6. `Poll::Ready`

Ready(output) 表示 Future 已完成并交出最终 Output。正常代码不应继续 poll 已完成的 Future；某些实现会 panic，但不得依赖任何二次结果。

## 7. `Poll::Pending`

Pending 表示“现在还不能完成”。它不是 sleep 指令，也不表示 executor 会自动定时重试；Future 必须确保进展可能发生时会唤醒当前 task。

## 8. 一次 Poll 时间线

```text
executor poll task
  → Future 运行到 I/O 未就绪
  → 注册当前 Waker
  → 返回 Pending
  → executor 运行其他 task
  → I/O 就绪并调用 wake
  → task 回到 runnable queue
  → executor 再次 poll
  → Ready(output)
```

## 9. `Context`

Context 最重要的内容是当前 task 的 Waker。底层 Future 把它注册到 socket、channel、timer 或自定义状态中，让将来的就绪事件知道唤醒谁。

## 10. `Waker`

Waker 是“请 executor 再 poll 这个 task”的能力。它不是线程句柄，不直接执行 Future，也不携带业务结果。

## 11. Wake 不是立即执行

`waker.wake()` 通常只把 task 标记为 runnable 或放回调度队列；何时、在哪个 worker thread poll 由 runtime 决定，多次 wake 也可能被合并。

## 12. Lost Wakeup

若 Future 检查“尚未就绪”后、注册 Waker 前事件恰好发生，就可能永远睡下。自定义 Future 必须遵守组件规定的检查/注册协议，必要时注册后再次检查状态。

## 13. Spurious Poll

Executor 可以在条件尚未真正改变时再次 poll。实现必须重新检查当前状态，不能假设“既然被唤醒就一定 Ready”。

## 14. Pending 的核心合同

返回 Pending 前，应保存继续所需状态，并安排未来进展时 wake。若没有任何事件源持有/触发 Waker，Future 可能永久停住。

## 15. `.await` 的概念展开

Await 大致表示：把子 Future pin 住并 poll；Ready 时取结果继续；Pending 时保存当前 async 函数状态并把 Pending 向上返回。真实编译器展开更复杂，但这个模型足以读大多数代码。

## 16. Await 不阻塞线程

Pending 时当前 task 让出执行权，runtime thread 可运行其他 task。若代码调用同步阻塞 I/O 或长时间 CPU 循环而没有 await，线程仍会被占住。

## 17. Async 状态机

```rust
async fn example() {
    let a = prepare();
    wait().await;
    use_value(a);
}
```

因为 a 跨 await 仍要使用，生成的 Future state 必须保存 a，并记录处于“开始、等待、继续、完成”中的哪个状态。

## 18. Await Point 决定保存内容

只在 await 前使用完的局部值通常无需跨暂停保存；await 后仍会读取的值成为 Future state 字段。大对象、guard 和 reference 跨 await 会影响 Future size、Send 与取消行为。

## 19. `async move`

它把捕获值 move 进 Future，使 Future 自己拥有这些状态。常用于 spawn，因为新 task 不能借用创建函数即将结束的 stack frame。

## 20. Async Block 的具体类型

每个 async block/async fn 产生匿名具体 Future 类型，不同源位置的类型通常不同。即使 Output 相同，也不能直接放入同一个 Vec，除非用 enum、泛型或 boxed trait object 统一。

## 21. Future 惰性的后果

```rust
let a = do_a();
let b = do_b();
a.await;
b.await;
```

创建 a、b 不代表二者已并发。这里通常先驱动 a 完成，再开始 poll b；并发需要 join、select、FuturesUnordered、spawn 等驱动结构。

## 22. Task 与 Future

Future 是计算状态 value；task 是 runtime 调度单位，持有并 poll 顶层 Future。`tokio::spawn(future)` 把 Future 注册为独立 task，并返回 JoinHandle。

## 23. Executor

Executor 管理 runnable task queue、调用 poll、响应 Waker 并在线程上调度任务。Tokio runtime 还提供 timer、I/O reactor 和 blocking pool，但 Future Trait 本身不规定具体 runtime。

## 24. Cooperative Scheduling

Rust async 通常是协作式：task 要在 await、yield 或 Pending 时交回控制权。没有 await 的无限循环不会被语言自动抢占，可能饿死同一 worker 上的其他 task。

## 25. CPU-bound 与 Blocking Work

大量计算或同步阻塞应拆分、yield 或移到 `spawn_blocking`/专用线程池。把函数标记 async 不会自动把阻塞系统调用变成非阻塞。

## 26. `JoinHandle`

Await JoinHandle 得到 task 完成结果，外层还可能包含 JoinError，表示 panic 或 cancellation。业务 Result 与 task 调度 Result 是不同层。

## 27. Drop JoinHandle 不一定取消 Task

Tokio 中丢弃普通 JoinHandle 通常会 detach，task 继续运行；显式 `abort` 或 `AbortOnDropHandle` 才会请求取消。必须阅读 wrapper 行为。

## 28. Cancellation 的基本模型

Rust Future 没有统一强制 cancel method。常见取消方式是停止 poll 并 drop Future、abort task，或让 CancellationToken 作为等待分支就绪后协作退出。

## 29. Drop Future 会发生什么

状态机中已保存的 String、Arc、channel receiver、guard 等字段被 drop；尚未执行的后续语句永远不会运行。因此关键清理要由 RAII guard 或外层显式协议保证。

## 30. Cancellation Safety

Future 若在任意 await 被 drop，外部可见状态是否仍合理？“先取队首、await 写出、再确认删除”的操作可能在中途取消后丢数据，需要重排提交边界。

## 31. Cancellation Safety 不等于可重试

取消后不破坏不变量是一层；重新调用是否重复副作用、是否幂等是另一层。要同时检查暂停点与 operation identity。

## 32. `tokio::select!`

Select 同时 poll 多个 branch Future，某个 branch Ready 时执行对应代码。未选中的 branch 通常被 drop，因此每个 branch 的 cancellation safety 都很重要。

## 33. Select 不是 Spawn

Branches 在同一 task 内交替被 poll，并不自动在多个 CPU core 并行。它提供并发等待；独立调度或 CPU 并行仍需 task/thread。

## 34. Select 中的 Borrow

多个 branch 同时存在于宏展开状态中，可能同时借用变量。借用冲突常来自 branch Future 的 lifetime 覆盖整个 select。

## 35. Select 公平性

轮询顺序和公平策略由具体宏规定；在循环中偏置总是就绪的 branch 可能饿死其他 branch。关键优先级需要明确并在持续负载下测试。

## 36. 工具调度的完成/取消竞争

Codex parallel tool path 用 select 等待 dispatch task 或 cancellation token。若任务已达到 terminal outcome 或已经完成，就收敛真实结果；否则按 runtime 取消能力等待清理或 abort handle。

## 37. Terminal Winner

取消与完成可能几乎同时发生。代码用 atomic terminal flag、`is_finished()` 和 JoinHandle 结果决定谁拥有最终响应，避免同时产生成功和 aborted 两个终态。

## 38. CancellationToken

Token 可 clone 给多个参与者；`cancel()` 发布状态，`cancelled().await` 形成等待分支。它是广播式协作信号，不会自动停止不检查 token 的代码。

## 39. Response Mapper 案例

`map_response_events` 的 task 在循环中 select：consumer-dropped token 就绪则记录取消并返回，否则等待 provider stream 下一项。下游停止读取后，上游映射不会无限工作。

## 40. Stream

```rust
trait Stream {
    type Item;
    fn poll_next(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Self::Item>>;
}
```

Future 最终只产出一次 Output；Stream 可异步产出零到多项。

## 41. Stream 的三种结果

- `Ready(Some(item))`：得到一项，还可能继续；
- `Ready(None)`：流永久结束；
- `Pending`：当前没项，未来可能被唤醒。

## 42. `.next().await`

StreamExt::next 创建一个 Future，poll 内部 Stream 直到得到 Some/None。它一次只取一项，不自动把整个流收集到内存。

## 43. `ResponseStream`

Codex ResponseStream 包装 Tokio mpsc Receiver，`poll_next` 直接委托 `rx_event.poll_recv(cx)`。Channel 负责 Waker 注册；新消息到达或 sender 关闭时唤醒 task。

## 44. Stream Drop 传播取消

ResponseStream 的 Drop 调用 `consumer_dropped.cancel()`。消费者不再 poll 时，mapper task 的 select 分支醒来并退出，把“下游消失”反向传到生产侧。

## 45. Backpressure 与 Poll

有界 mpsc 的 send Future 在容量不足时 Pending 并等待容量 Waker。Receiver 取走元素后唤醒 sender；背压不是 busy loop，而是 producer task 暂停。

## 46. Custom Future：Windows Child

`WinChild` 实现 Future。poll 先检查进程状态；已退出则 Ready，查询错误则 Ready(Err)，仍运行则 clone process handle 和 Waker，让等待线程在 OS process signal 后 wake task。

## 47. Blocking OS API 的桥接

Windows `WaitForSingleObject(..., INFINITE)` 是阻塞等待，不能直接占住 async runtime poll。源码把它放到单独线程，完成时只唤醒 task，再由下一次 poll 读取 exit status。

## 48. Waker 必须 Clone 到事件源

`cx` 只在本次 poll 借用有效；未来线程需要拥有可调用的 Waker clone。事件源不能保存 `&Context` 或短期引用。

## 49. Poll Implementation 不应阻塞

Poll 应快速检查、注册、返回。若 poll 内等待网络、锁住线程或做大量计算，整个 executor worker 无法调度其他 task。

## 50. `poll_fn`

`std::future::poll_fn` 或 futures 版本把接收 Context、返回 Poll 的 closure 包装成 Future，适合桥接小型 polling 协议，无需定义完整 struct。

## 51. AtomicWaker 测试案例

Exec-server 的受控 WebSocket 测试用 AtomicWaker 保存等待 writer-ready 的 task Waker；test handle 把 atomic 状态改成 ready 后调用 wake，使 `poll_ready` 再次被驱动。

## 52. Atomic State 与 Waker 缺一不可

Atomic bool 保存“条件已经发生”，Waker 保存“条件变化时通知哪个 task”。只 wake 不保存状态可能丢事件；只存状态不 wake 则 task 不会及时再 poll。

## 53. Register 最新 Waker

Future 可能被不同 context poll，组件通常应更新注册的 Waker。AtomicWaker 等工具封装并发注册与唤醒协调；手写时还要处理 register 与状态变化的竞态。

## 54. `join!`

Join 同时推进多个 Future，等待全部完成并保留每个输出。某一项较慢会拖住整体完成，但其他项在等待期间仍被 poll。

## 55. `try_join!`

针对 Result Future，在某项 Err 时提前返回；其余未完成 Future 被 drop，所以同样需要 cancellation safety。它不是数据库事务回滚。

## 56. `join_all`

对集合中的 Futures 等待全部完成，通常按输入位置返回结果。集合很大时应考虑状态内存、唤醒成本与并发上限。

## 57. `FuturesUnordered`

它保存动态 Future 集合，并按完成顺序产生结果。适合完成顺序不重要、任务数动态变化的场景。

## 58. Thread Shutdown 案例

`shutdown_all_threads_bounded` 把每个 thread shutdown Future 收集进 FuturesUnordered，再通过 `next().await` 按实际完成顺序归类 Complete、SubmitFailed、TimedOut。

## 59. 完成顺序与最终确定性

并发完成顺序不稳定，源码最后按 ThreadId 排序 report 列表。运行可并发，外部输出仍可通过明确排序获得确定性。

## 60. `buffer_unordered(n)`

它从上游 Stream 取得 Future，最多同时驱动 n 个，并按完成顺序输出。与一次创建全部 Future 相比，它限制 in-flight 数量和资源压力。

## 61. Session Import 案例

外部 session import 使用 `buffer_unordered(SESSION_IMPORT_CONCURRENCY)`，循环 next 收集结果。快 session 无需等待前面的慢 session，但下游不能假设结果仍按输入顺序。

## 62. Semaphore 与 Buffer 上限

Buffer 控制该 pipeline 内同时被驱动的 Future 数；外层 Semaphore 可能控制跨调用的全局名额。多层限制保护的资源不同。

## 63. Spawn-all 与 Bounded Concurrency

为一百万项立即 spawn 一百万 task 会先产生巨大 task/state 开销。Stream + buffer_unordered 只保持有限 in-flight 工作，是背压式并发。

## 64. Timeout

`tokio::time::timeout(duration, future)` 竞争 inner Future 与 timer；超时返回 Err，并 drop inner Future。它限制等待时间，但不保证外部副作用已经撤销。

## 65. Deadline 与嵌套 Timeout

多层各自重新给完整 timeout 会放大总时长。高层最好建立总 deadline，向下传剩余预算；时间专题对此已有详细说明。

## 66. Sleep Future

Timer 首次 poll 未到期时注册 Waker 并 Pending，到期后 runtime 唤醒 task。同步 `std::thread::sleep` 则直接阻塞当前线程。

## 67. Yield

`tokio::task::yield_now().await` 主动让当前 task 回到调度队列，改善协作公平性；它不是延时保证，也不能代替拆分长期 CPU 工作。

## 68. Lock Guard 跨 Await

若 guard 被 Future state 保存，其他 task 会在整个 await 期间无法进入临界区，还可能让 Future not Send。锁内 snapshot/mutation、释放后 await 是重要模式。

## 69. Future Not Send

多线程 spawn 常要求 Future: Send。跨 await 保存的 Rc、RefCell borrow、非 Send guard 等会使整个 Future 不 Send；编译错误通常指出哪个 value 跨 await 存活。

## 70. Future Size

状态机要容纳各 suspension state 所需字段，布局近似“最大状态 + tag”。多个大局部跨 await 会增大 Future；Box 可移到 heap，但仍有 allocation 成本。

## 71. Boxed Future

`Pin<Box<dyn Future<Output = T> + Send + 'a>>` 统一不同具体 Future，适合 Trait 边界和异构容器。代价是 allocation、间接调用和类型信息擦除。

## 72. 递归 Async

直接递归 async fn 会让 Future 类型无限递归；通常需要 Box 某一层打断静态大小展开，或改写为显式 stack/loop。

## 73. Instrumentation Across Await

Tracing span 应随 Future poll 生命周期正确进入，而不是只包住 Future 创建瞬间。仓库约定优先在 async 函数定义处使用 `#[tracing::instrument]`。

## 74. 卡住诊断清单

- Future 是否真正被 await、spawn 或 poll？
- Pending 前是否注册 Waker？
- 事件源是否会 wake，状态是否已保存？
- Channel sender 是否仍存在，receiver 是否还 poll？
- 是否在 poll/async worker 内同步阻塞？
- Lock/permit 是否跨 await 被长期持有？
- Select loser 是否安全取消？
- Timeout 后外部任务是否仍独立运行？

## 75. 并发语义清单

- 是依次 await，还是 join/select/spawn？
- 需要全部结果还是首个结果？
- 输出按输入顺序还是完成顺序？
- 最大 in-flight 数量是多少？
- 失败是否取消其他工作？
- 取消后副作用是否已提交？
- JoinHandle drop、abort 和 awaited teardown 分别做什么？

## 76. 常见编译错误翻译

- “future is not Send”：非 Send value 跨 await 被保存并要跨线程调度。
- “borrow may still be in use when coroutine yields”：引用或 guard 跨 suspension point。
- “cannot borrow more than once”：select branches 同时持有冲突 borrow。
- “cannot be unpinned”：next/poll API 需要 pin 或 Unpin。
- “async recursion requires boxing”：状态机静态大小递归。
- “unused implementer of Future”：创建 Future 却没有驱动。

## 77. 源码检查点

1. `codex-rs/utils/pty/src/win/mod.rs`：WinChild 手工 Future、process wait 与 Waker bridge。
2. `codex-rs/exec-server/src/connection.rs`：测试 ControlledWebSocket、AtomicWaker 和 poll_fn。
3. `codex-rs/core/src/client_common.rs`：mpsc-backed ResponseStream 的 poll_next 与 Drop cancel。
4. `codex-rs/core/src/client.rs`：mapper task 竞争 consumer cancellation 与 provider Stream。
5. `codex-rs/core/src/tools/parallel.rs`：dispatch completion、cancellation 和 terminal winner。
6. `codex-rs/core/src/thread_manager.rs`：FuturesUnordered shutdown 与最终排序。
7. `codex-rs/app-server/src/external_agent_migration/session_importer.rs`：buffer_unordered 有界导入。
8. `codex-rs/core/src/stream_events_utils.rs`：boxed in-flight tool Future。

## 78. 搜索命令

```bash
rg -n 'impl .*Future for|fn poll\(|cx\.waker|AtomicWaker|poll_fn' codex-rs
rg -n 'tokio::select!|CancellationToken|\.abort\(\)' codex-rs/core/src codex-rs/app-server/src
rg -n 'FuturesUnordered|buffer_unordered|join_all|try_join!' codex-rs/core/src codex-rs/app-server/src
rg -n 'impl Stream for|fn poll_next' codex-rs/core/src codex-rs/tui/src
```

## 79. 小练习：手工模拟 Poll

对“等待 channel 一项后写数据库”的 async 函数，画出 Start、WaitingMessage、Writing、Done 四个状态。标出每次 Pending 前谁保存 Waker、哪些局部值跨 await，以及在 WaitingMessage/Writing 被 drop 的后果。

## 80. 小练习：比较三种并发

让三个 Future 分别耗时 1、3、2 秒，比较顺序 await、join_all、buffer_unordered(2) 的启动时机、总时间、输出顺序和最大 in-flight 数；再加入第二项失败，分析哪些 Future 继续或被 drop。

## 81. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Future / Output | 未来值/输出 | 尚未完成的单次计算状态机与最终结果类型 |
| Lazy | 惰性 | 创建 Future 不等于执行，需被 poll 才推进 |
| Poll / Ready / Pending | 轮询/就绪/待定 | 一次推进尝试的完成或暂停结果 |
| Context / Waker | 上下文/唤醒器 | Poll 调用环境，以及请求 task 再次调度的能力 |
| Wakeup / lost wakeup | 唤醒/丢失唤醒 | 条件变化通知，以及注册竞态造成的永久停顿 |
| Executor / runtime | 执行器/运行时 | 管理 runnable tasks 并反复 poll Future 的系统 |
| Reactor | 事件反应器 | 监听 I/O/timer 就绪并触发 Waker 的 runtime 部分 |
| Task / JoinHandle | 任务/连接句柄 | 顶层被调度 Future 及其完成、取消观察 handle |
| Suspension point | 暂停点 | Await 可能让状态机返回 Pending 的位置 |
| Async state machine | 异步状态机 | 保存程序位置和跨 await locals 的编译器生成类型 |
| Cooperative scheduling | 协作式调度 | Task 在 await、yield 或 Pending 时交回线程 |
| Cancellation safety | 取消安全 | Future 在暂停点被 drop 后仍保持外部不变量 |
| `select!` / loser | 选择竞争/未选分支 | 同时 poll branches 并 drop 未完成分支 |
| Stream / poll_next | 异步流/轮询下一项 | 可多次产生 Item 的异步状态机接口 |
| Backpressure | 背压 | 下游容量不足使 producer Future Pending |
| `poll_fn` / AtomicWaker | 轮询函数/原子唤醒器 | 用 closure 构建 Future，以及协调 Waker 注册 |
| `join` / `try_join` | 全部等待/可失败全部等待 | 并发驱动全部 Future，后者错误时提前结束 |
| FuturesUnordered | 无序 Future 集合 | 动态并发并按完成顺序产出结果的 Stream |
| `buffer_unordered` / in-flight | 无序缓冲/进行中 | 限制同时驱动数量并按完成顺序输出 |
| Timeout / deadline | 超时/截止点 | Timer 与 inner Future 竞争，以及绝对时间预算 |
| Spurious poll | 假性轮询 | 条件未变化却再次 poll，Future 必须安全重查 |

## 82. 常见误解

- “调用 async fn 就开始执行”：它首先产生惰性 Future。
- “await 会阻塞线程”：正常 Pending 会暂停 task 并让出 worker。
- “Pending 后 runtime 会自动重试”：Future 必须安排 Waker。
- “wake 会立即运行 Future”：它通常只请求重新调度。
- “select 会在多个 CPU core 并行”：它在一个 task 内并发 poll。
- “drop JoinHandle 就会取消 task”：普通 Tokio JoinHandle 通常 detach。
- “timeout 会回滚外部副作用”：它只停止等待并放弃 inner Future。
- “创建多个 Future 再依次 await 就是并发”：驱动方式决定并发。
- “Future not Send 是 async 本身的问题”：通常是非 Send value 跨 await。

## 83. 一句话收束

Async fn 把代码编译成惰性 Future 状态机，executor 通过 poll 推进它，Pending 前注册的 Waker 把未来就绪重新接回调度；await、Stream、select 和并发集合只是不同的状态机组合方式，而取消要求你设计任意暂停点被 drop 后的结果。
