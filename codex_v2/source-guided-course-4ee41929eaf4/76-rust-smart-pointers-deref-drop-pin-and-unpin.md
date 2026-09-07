# 76：Rust 智能指针、Deref、Drop、Pin 与 Unpin

> 源码基线：`4ee41929eaf4`。本章解决“值究竟由谁拥有、指针移动是否会移动目标、资源何时释放，以及异步 Future 为什么经常需要 Pin”。

## 1. 本章解决什么问题

看到 `Box<T>`、`Arc<T>`、`Weak<T>`、`Pin<Box<dyn Future>>` 和 `impl Drop` 时，只翻译成“指针、引用计数、固定、析构”仍不够。真正需要判断的是 owner 数量、pointee 地址、释放触发条件和可否取得 `&mut T`。

## 2. 先说人话：Smart Pointer 是带规则的 Owner

普通引用主要表示临时借用；智能指针通常既保存一个指向目标的 handle，又规定目标怎样拥有、共享、释放或访问。例如 Box 独占，Arc 共享，Weak 观察但不保活，Pin 约束目标不能再被安全移动。

## 3. Pointer 与 Pointee

Pointer/handle 是 `Box<T>`、`Arc<T>` 等小 value；pointee 是它们指向的 T。移动 pointer value 与移动 pointee 不是同一件事，这是理解 Box 和 Pin 的关键。

## 4. `Box<T>` 的基本形状

```text
stack/local                    heap
+------------+             +---------+
| Box handle | ----------> |    T    |
+------------+             +---------+
```

Box 独占 heap 中的 T。Box 自身通常是固定大小，T 可以很大或在编译期大小被擦除。

## 5. 移动 Box 不会搬动 Heap 中的 T

```rust
let a = Box::new(value);
let b = a;
```

这里移动的是 Box handle 的 ownership；heap allocation 中的 T 通常仍在原地址。`a` 失效，`b` 成为新 owner。

## 6. Box 不等于“引用”

`&T` 不负责释放 T，并受另一个 owner 的 lifetime 约束；`Box<T>` 拥有 T，Box 离开作用域时会 drop T 并释放 allocation。

## 7. Box 的常见用途

- 把大 value 放到 heap；
- 让递归类型拥有固定大小；
- 保存 `dyn Trait` 等动态大小类型；
- 统一不同具体 Future 的返回形状；
- 配合 Pin 保证 pointee 地址稳定。

## 8. `Box<dyn Trait>`

Box handle 同时携带数据地址和动态分发 metadata。它拥有具体实现，却只向调用者暴露 Trait 方法；Box 解决 ownership，`dyn Trait` 解决类型擦除，两者职责不同。

## 9. Box 不是性能优化的默认答案

Heap allocation、间接访问和可能的动态分发都有成本。只有当 ownership、size、递归或稳定地址确有需要时才 Box，不要把“避免 stack”自动等同于“更快”。

## 10. `Deref`

```rust
trait Deref {
    type Target: ?Sized;
    fn deref(&self) -> &Self::Target;
}
```

Deref 让智能指针提供对目标的共享引用，也是 `*pointer` 和 deref coercion 的基础之一。

## 11. Deref Coercion

当函数需要 `&T` 而拿到 `&Box<T>`、`&String` 或合适 wrapper 时，编译器可沿 Deref 自动调整。它不复制 pointee，也不改变 owner。

## 12. Method Resolution 也会 Deref

`boxed.method()` 可能实际调用 T 的 method。读源码时如果 wrapper 定义处找不到方法，应检查 Deref target 和 Trait imports。

## 13. `DerefMut`

它返回 `&mut Target`，允许通过 wrapper 修改目标。对保存 invariant 的 newtype，DerefMut 可能让调用者绕过验证，所以比只读 Deref 更危险。

## 14. Codex 的 `AbsolutePathBuf` Deref

该类型实现 `Deref<Target = Path>`，因此可方便使用 Path 的只读 API；它没有暴露任意 `&mut Path`，避免外部原地把绝对路径改成破坏 invariant 的表示。

## 15. 不要把 Deref 当普通委托机制

Deref 会影响大量方法调用和隐式调整。领域 wrapper 只想提供几个底层能力时，明确的 `as_path()`、`as_str()` 通常比 Deref 更清楚。

## 16. `Rc<T>`

Rc 使用非原子的引用计数，允许单线程中有多个 owner。Clone Rc 只增加 strong count，不会深度 clone T；最后一个 strong owner 消失时才 drop T。

## 17. `Arc<T>`

Arc 是 atomically reference-counted pointer，可在满足 Send/Sync 条件时跨线程共享 ownership。Arc 的原子计数更昂贵，但适合 Tokio task、线程和长期共享服务对象。

## 18. `Arc::clone` 的含义

```rust
let another = Arc::clone(&shared);
```

它复制 handle 并增加 strong count，两个 Arc 指向同一 T。显式写 `Arc::clone` 比普通 `.clone()` 更容易提醒读者“共享而非深复制”。

## 19. `Arc<T>` 不自动让 T 可变

多个 owner 只能直接共享读取。需要修改时常组合 `Arc<Mutex<T>>`、`Arc<RwLock<T>>` 或内部原子字段；Arc 负责 ownership，不负责业务同步。

## 20. Strong Count 决定 Pointee Lifetime

任意 strong Arc 仍存在，T 就保持存活。一个局部变量离开作用域不代表对象立即释放，因为其他 task、queue 或 registry 可能还持有 clone。

## 21. 引用计数 Cycle

若 A 以 Arc 拥有 B，B 又以 Arc 拥有 A，strong count 永远无法归零，即使外部都放弃。Rust 内存安全仍成立，但会形成逻辑泄漏。

## 22. `Weak<T>`

Weak 指向同一 Arc allocation，却不增加 strong count，因此不负责让 T 存活。它适合 parent/back-reference、cache key 和后台观察者。

## 23. `Arc::downgrade`

从 `&Arc<T>` 创建 Weak。该操作表达“我需要以后尝试联系对象，但不应因为我而延长它的业务生命周期”。

## 24. `Weak::upgrade`

```rust
let Some(owner) = weak.upgrade() else {
    return;
};
```

若仍有 strong owner，upgrade 返回 `Some(Arc<T>)` 并暂时增加 strong count；目标已 drop 则返回 None。这不是异常内存访问，而是正常生命周期分支。

## 25. Weak Allocation 可能暂时保留

最后一个 strong owner 消失后 T 会被 drop；控制块可能等最后一个 Weak 也消失才完全释放。Weak 不保活 T，但自身仍需要引用计数 metadata。

## 26. App-server 时间 Provider 案例

`AppServerTimeProvider` 保存 `Weak<OutgoingMessageSender>`。发起 current-time request 时才 upgrade；若 app-server sender 已结束，就返回“provider unavailable”，而不是让 provider 反向维持整个发送系统。

## 27. 为什么不是直接保存 Arc

若 provider 的生命周期可能超过连接/发送器，保存 Arc 会让后者迟迟不 drop；Weak 让真正 owner 决定 shutdown，provider 只处理目标已不存在的情况。

## 28. Models Refresh Worker 案例

后台 task 持有 models manager 的 Weak，每轮 upgrade；主 owner 消失后 upgrade 返回 None，loop 主动结束。后台刷新不会因为自己仍运行而永久保活 manager。

## 29. 在 Await 前后持有 Arc

refresh worker upgrade 后在 `list_models(...).await` 期间持有 strong Arc，确保本轮调用安全完成；随后显式 `drop(models_manager)`，避免 sleep 周期也延长 manager 生命周期。

## 30. `Drop` Trait

```rust
trait Drop {
    fn drop(&mut self);
}
```

Value 生命周期结束时，Rust 自动调用 drop，然后继续释放字段和底层存储。Drop 适合同步、必须执行且不能返回错误的收尾。

## 31. Deterministic Destruction

普通局部 value 通常在离开 scope、被覆盖或显式 drop 时立刻运行析构，而不是等待垃圾回收器周期。这让 guard 能可靠绑定 lexical scope。

## 32. `std::mem::drop(value)`

这是一个接收 ownership 的普通函数，用于提前结束 value 生命周期。不能直接调用 `value.drop()`；Rust 禁止显式调用 Drop method，以避免随后自动 drop 两次。

## 33. Drop 与 Move

Value move 后，由新位置负责最终 drop；旧 binding 不再析构同一资源。Ownership 保证正常 safe Rust 中每个 owned value 最终只被 drop 一次。

## 34. Struct 的 Drop 过程

若类型实现 Drop，先运行其 drop method，再按语言规则 drop 字段。清理逻辑不应依赖复杂、脆弱的隐式次序；有严格协议时用显式 shutdown。

## 35. Panic 期间的 Drop

在 unwind 模式下，stack unwinding 会 drop 已初始化局部值，因此 guard 常能覆盖 `?`、early return 和 panic。但进程 abort、强制 kill、断电时不能指望 Drop。

## 36. Async Cancellation 也是 Drop Future

Future 被 `select!` 输掉、task abort 或 owner 放弃时，Future state 被 drop，其中已存字段也依次清理。RAII guard 因此能覆盖许多 async cancellation 路径。

## 37. Drop 不能 `await`

Drop 是同步函数，不能可靠执行需要 await 的协议关闭。异步资源通常需要 `shutdown().await` 负责完整握手，Drop 只做 cancel、abort 或 best-effort fallback。

## 38. `AgentExecutionGuard`

获取执行名额时 atomic count 加一，guard Drop 时 `fetch_sub(1)`。无论正常完成、错误返回还是 Future 取消，只要 guard 被 drop，计数名额就会归还。

## 39. Guard 把责任交给 Ownership

调用者不必记住在每个 return 分支手写 decrement。谁持有 guard，谁就持有配额；scope 结束即释放。这是 RAII 的核心。

## 40. Fuzzy Search Session Drop

`FuzzyFileSearchSession` Drop 时设置共享 cancellation atomic。丢弃 session handle 就表达“此搜索不再需要”，后台计算能够合作式停止。

## 41. Models Worker Drop

`ModelsRefreshWorker` Drop 调用 cancellation token 的 `cancel()`；它没有在 Drop 中等待 task join。这里保证发出停止信号，不保证异步工作已经完全结束。

## 42. Drop 不应做什么

不要在 Drop 中 panic、长时间阻塞、依赖 async 网络成功或吞掉重要持久化错误。Drop 无法向调用者返回 Result，关键失败应在显式方法中处理。

## 43. Arc 与 Drop 的组合排错

“为什么 Drop 没执行”常不是析构失效，而是 strong Arc 仍存在。搜索 Arc::clone、task capture、channel queue 和 cache，再用 strong_count 仅作诊断，不把瞬时 count 当业务同步条件。

## 44. `Pin<P>` 先说人话

Pin 包装一个指针 P，并建立承诺：只要 pointee 属于不能随意移动的类型，safe code 就不能把它从当前地址搬走。Pin 主要约束通过该指针访问目标的方式。

## 45. Pin 不是“锁住变量名”

它不是 Mutex，不防并发，不把内存设成只读，也不阻止 pointer handle 自身移动。它关心的是 pointee 的地址稳定性。

## 46. 为什么地址稳定会重要

某些状态在建立后可能含有指向自身内部字段的引用或依赖固定地址的底层协议。如果整个 value 被搬到新地址，内部关系就会失效。

## 47. Async Future 与自引用状态

编译器把 async function 变成状态机；跨 await 保存的局部状态可能形成地址敏感关系。调用 poll 后，不能任意移动这类 Future，因此 Future::poll 接收 `Pin<&mut Self>`。

## 48. `Future::poll` 的核心签名

```rust
fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
```

Runtime 可反复 poll 同一个 Future，但每次必须遵守 pin contract；Pending 表示以后由 Waker 再唤醒。

## 49. `Pin<Box<T>>`

`Box::pin(value)` 一次完成 heap allocation 与 pin。之后 Box handle 可以在变量、队列间 move，但 heap 中 T 的地址保持稳定，直到被 drop。

## 50. 移动 Pin<Box<T>> 为什么可以

移动的是外层 handle，不是 pointee。多个队列节点可以接力拥有同一个 pinned heap allocation，而不会改变 Future state 的地址。

## 51. `Pin<&mut T>`

它借用一个已经 pinned 的 T，不拥有 T。Lifetime 决定借用多久，原 owner 必须保证在这段时间内目标继续满足 pin contract。

## 52. `tokio::pin!`

Codex command execution 对 expiration Future 和 exit receiver 使用 `tokio::pin!`。它把局部 value 固定在当前 stack frame，便于 `select!` 或手工 poll，无需 heap allocation。

## 53. 栈 Pin 的 Lifetime

局部 stack slot 只在当前 scope 有效，因此 pinned reference 不能逃出该 scope。若 Future 需要存进长期队列或作为 trait object 返回，通常使用 Pin<Box<...>>。

## 54. `Box::pin` 与 `tokio::pin!` 怎样选择

- 需要 owned、可跨 scope、可入集合：Box::pin；
- 当前函数临时 poll，想避免 heap allocation：pin macro；
- 具体类型与 API bound 也会影响选择。

## 55. Codex `TimeFuture`

```rust
type TimeFuture<'a> =
    Pin<Box<dyn Future<Output = Result<DateTime<Utc>>> + Send + 'a>>;
```

Pin 满足 Future poll 的地址要求，Box 提供 ownership 和 heap 稳定地址，dyn Future 擦除具体 async block 类型，Send 允许跨线程调度，`'a` 允许借用 provider。

## 56. 为什么不直接写 `dyn Future`

Trait object 本身是 dynamically sized，不能作为普通局部/返回值按值放置；需要 Box 等指针。Future 又可能 !Unpin，因此常见完整形状是 Pin<Box<dyn Future...>>。

## 57. Request Queue 的 Boxed Future

App-server `BoxFutureUnit` 是 `Pin<Box<dyn Future<Output = ()> + Send + 'static>>`。不同请求产生不同 async block 类型，经 Box + dyn 统一后才能放进同一个 queue struct。

## 58. `'static` 在这里表示什么

Queued Future 不能借用短生命周期 stack 数据，才能安全存入稍后执行的队列。它不表示 Future 永远运行；完成、取消或被丢弃时仍会 drop。

## 59. Box::pin 也可缓解大 Future Stack

`ConfigBuilder::build` 用 `Box::pin(self.build_inner()).await`，注释说明要把大型 config-loading Future state 放离较小的 runtime thread stack。这里 Box 同时影响放置位置和 poll 稳定性。

## 60. `Unpin`

Unpin 是 auto Trait，表示该类型即使位于 Pin 中，也不依赖地址稳定，因此可按普通规则安全移动。大多数日常类型自动 Unpin；许多 async Future 则可能不是。

## 61. Pin 对 Unpin 类型几乎不增加限制

若 `T: Unpin`，可以安全地从 `Pin<&mut T>` 取得 `&mut T`，因为移动 T 不会破坏它的语义。Pin API 据此区分普通类型和地址敏感类型。

## 62. `Pin::new`

安全的 `Pin::new(&mut value)` 通常要求 pointee Unpin；若类型可能 !Unpin，就需要能保证地址的构造方式，例如 Box::pin 或 pin macro。

## 63. `PhantomPinned`

自定义类型可包含 `PhantomPinned` 来阻止自动 Unpin，声明它可能依赖固定地址。建立内部引用和投影通常涉及 unsafe，普通业务类型不应仅为“更安全”随意添加。

## 64. Projection

拿到 `Pin<&mut Struct>` 后，不能一般性地直接获得任意字段的 `&mut`，因为移动某个 pinned 字段也可能破坏合同。把外层 Pin 安全映射到字段称为 pin projection。

## 65. Projection 为什么常用辅助库

手写 projection 容易在 unsafe 边界犯错。`pin-project` 类工具可生成符合规则的字段投影和 pinned drop 支持；使用前仍需区分哪些字段应 pinned、哪些字段是 Unpin。

## 66. Codex TUI 的显式 `Unpin`

`TuiEventStream<S>` 在 `S: Unpin` 时显式实现 Unpin。它的 Stream impl 接收 `Pin<&mut Self>`，随后能修改 `poll_draw_first` 并调用普通 `&mut self` helper，因为该整体允许安全移动。

## 67. `AsyncRead + Unpin` Bound

许多 codec 函数需要反复通过 `&mut reader` poll 输入；要求 reader Unpin 后，可以使用简单 `Pin::new(reader)`。若不要求 Unpin，API 就要接收 pinned reader 或自行保存 pin guarantee。

## 68. Unpin 不是“不使用 Pin”

Future/Stream 的 poll 接口仍统一使用 Pin，即使具体实现 Unpin。Unpin 只是允许在 Pin 包装中退回更普通的可变访问和移动能力。

## 69. Pin 不延长 Lifetime

Pinned reference 仍可能过期，Pin<Box<T>> 仍会被 drop。Pin 只保证存活期间不被移动，不保证目标活得更久；lifetime 由 owner、Arc count 和 scope 决定。

## 70. Pin 不自动保证 Self-reference 正确

先 pin 再按正确方式建立内部关系才有意义。Pin 阻止后续 safe move，却不会验证任意 raw pointer 是否指向正确字段，也不会修复构造阶段错误。

## 71. Drop 与 Pin 的关系

Pinned value 最终仍必须 drop，并且 drop 时仍位于固定地址。自引用类型的析构必须避免在清理完成前移动 pinned 字段；这也是手写 pinned types 需要谨慎设计的原因。

## 72. 常见编译错误翻译

- “cannot be unpinned”：某 API 想移动或取得普通 `&mut T`，但 T 可能 !Unpin。
- “trait Unpin is not implemented”：改为 pin input、Box::pin，或重新设计 bound，不要盲目强行 impl。
- “cannot move out of dereference of Pin”：正在尝试从 pinned pointee 移走字段。
- “future cannot be sent”：这是 Send 问题，不等同于 Pin；检查跨 await 捕获。
- “method not found”：可能需要 Pin method、Deref target method 或 Trait import。

## 73. 阅读 Smart Pointer 类型的步骤

1. 外层是 Box、Arc、Weak、Pin 还是组合？
2. 谁是 pointee，谁是实际 owner？
3. Clone 是深复制还是增加 strong count？
4. 目标什么时候 drop？
5. Weak upgrade 失败是否被当成正常分支？
6. 是否需要同步 interior mutability？
7. Pin 约束 pointer 还是 pointee，T 是否 Unpin？

## 74. 阅读 Boxed Future 的步骤

从内向外读：Future 的 Output 是什么；是否 Send；能借用多久；dyn 擦除了什么；Box 是否提供 owned storage；Pin 是否满足 poll 稳定性；调用点是马上 await、存入 queue 还是跨 task 传递。

## 75. 设计 Drop Guard 的清单

- 获取资源与创建 guard 是否原子对应？
- guard 是否覆盖所有 early return 和 cancellation scope？
- Drop 动作是否同步、快速、幂等且不 panic？
- 是否仍需要显式 async shutdown？
- Arc clone 会不会让真正 owner 意外延寿？
- 测试是否覆盖错误和取消路径的资源归还？

## 76. 源码检查点

1. `codex-rs/utils/absolute-path/src/lib.rs`：Deref<Target = Path> 与不暴露任意可变底层路径。
2. `codex-rs/app-server/src/current_time.rs`：Weak sender、upgrade 与 boxed TimeFuture。
3. `codex-rs/app-server/src/models_refresh_worker.rs`：Weak manager、显式 drop strong Arc、worker Drop cancellation。
4. `codex-rs/core/src/agent/control/execution.rs`：Arc limiter 与 Drop guard 配额归还。
5. `codex-rs/app-server/src/fuzzy_file_search.rs`：session Drop 设置 cancel flag。
6. `codex-rs/core/src/current_time.rs`：Pin<Box<dyn Future + Send + 'a>> aliases。
7. `codex-rs/app-server/src/request_serialization.rs`：`'static` boxed futures 进入异构队列。
8. `codex-rs/app-server/src/command_exec.rs`：expiration 与 exit receiver 的 stack pin。
9. `codex-rs/tui/src/tui/event_stream.rs`：Unpin bound、Pin<&mut Self> 与手工 Stream poll。
10. `codex-rs/core/src/config/mod.rs`：大型配置加载 Future 在立即 await 前 Box::pin。

## 77. 搜索命令

```bash
rg -n 'Arc::downgrade|Weak<|\.upgrade\(\)' codex-rs/core/src codex-rs/app-server/src
rg -n 'impl Drop for|struct .*Guard' codex-rs/core/src codex-rs/app-server/src
rg -n 'Pin<Box|Box::pin|tokio::pin!' codex-rs/core/src codex-rs/app-server/src
rg -n 'Unpin|Pin<&mut Self>' codex-rs/tui/src/tui/event_stream.rs codex-rs/code-mode-protocol/src
```

## 78. 小练习：画 Owner Graph

为 models refresh worker 画三类边：调用方 strong-own worker，worker task weak-observe manager，每轮 upgrade 临时 strong-own manager。然后分别推演调用方 drop worker、manager owner drop、任务正在 await 三种情况。

## 79. 小练习：选择 Pin 方式

判断以下场景：当前函数中临时参加 `select!` 的 sleep、要存进 VecDeque 的异构请求 Future、立即 await 的大型 Future。通常分别考虑 stack pin、Pin<Box<dyn Future>>、Box::pin 后立即 await；最后一个还可能出于 stack-size 考量。

## 80. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Smart pointer / handle | 智能指针/句柄 | 附带 ownership、释放或访问规则的 pointer-like value |
| Pointee / allocation | 被指向值/分配 | Pointer 指向的 T，以及承载它的存储区域 |
| `Box<T>` | 盒指针 | Heap 中 T 的唯一 owner |
| `Rc<T>` / `Arc<T>` | 引用计数/原子引用计数 | 单线程或可跨线程的共享 ownership handle |
| Strong count | 强引用计数 | 决定 pointee 是否继续存活的 owner 数量 |
| `Weak<T>` / upgrade | 弱引用/升级 | 不保活目标的观察 handle，以及尝试取得临时 strong Arc |
| Reference cycle | 引用环 | Strong ownership 互相闭环导致 count 无法归零 |
| `Deref` / `DerefMut` | 解引用/可变解引用 | 提供共享或可变 target reference 的 Trait |
| Deref coercion | 解引用自动调整 | 编译器在引用和方法调用处沿 Deref 转换 |
| `Drop` / destructor | 析构/析构器 | Value 生命周期结束时同步执行的清理逻辑 |
| RAII guard | 资源获取即初始化守卫 | 用 ownership scope 自动归还配额、锁或状态 |
| Unwind / abort | 栈展开/立即终止 | Panic 时逐层析构，以及不运行常规清理的进程终止 |
| `Pin<P>` | 固定指针包装 | 限制通过 P 安全移动 pointee 的地址稳定合同 |
| `Pin<Box<T>>` / `Pin<&mut T>` | 固定 Box/固定可变借用 | Owned heap pin 与非 owning pinned reference |
| `Box::pin` / pin macro | 堆固定/栈固定宏 | 分配并 pin，以及在当前 stack scope 固定局部值 |
| `Unpin` | 可解除固定约束 | 表示类型不依赖地址稳定、可安全普通移动的 auto Trait |
| Self-reference | 自引用 | Value 内部状态直接或间接指向自身其他部分 |
| Projection | 投影 | 从 pinned outer value 安全访问其字段的操作 |
| DST / trait object | 动态大小类型/Trait 对象 | 编译期无固定大小、需经 pointer 使用的 erased value |
| Poll / Pending / Waker | 轮询/未就绪/唤醒器 | Future/Stream 的增量推进状态与再次调度机制 |
| Type erasure / boxed Future | 类型擦除/装箱 Future | 统一不同具体 async state machine 的存储形状 |

## 81. 常见误解

- “移动 Box 会搬动 T”：通常只移动 handle，heap pointee 地址不变。
- “Arc clone 会深度复制对象”：它通常只增加 strong count。
- “Arc 自动解决并发修改”：它解决共享 ownership，修改仍需锁或原子机制。
- “Weak 是可能悬垂的普通指针”：safe Weak 必须 upgrade，目标消失时得到 None。
- “Drop 等于完整 shutdown”：Drop 不能 await，也无法报告关键错误。
- “Pin 是线程锁”：Pin 约束地址移动，与互斥无关。
- “Pin 后 pointer 自己也不能移动”：Pin<Box<T>> handle 可移动，pointee 保持地址。
- “所有 Future 都天生 Unpin”：async Future 可能 !Unpin，应尊重 API bound。
- “实现 Unpin 总能修复错误”：错误的 Unpin 会破坏地址敏感类型的设计假设。

## 82. 一句话收束

Box 决定独占 heap ownership，Arc/Weak 分开“共享保活”和“可失效观察”，Deref 控制 pointer-like 访问，Drop 把同步清理绑定到 scope，而 Pin/Unpin 则精确说明一个异步或地址敏感 value 在存活期间能否再被安全移动。
