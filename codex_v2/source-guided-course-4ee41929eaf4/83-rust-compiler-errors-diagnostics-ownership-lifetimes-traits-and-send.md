# 83：Rust 编译错误阅读方法、所有权、生命周期、Trait 与 Send 诊断

> 源码基线：`4ee41929eaf4`。本章解决“面对几十行 Rust 编译错误时，怎样找到第一个真正不满足的约束，并把诊断翻译成 owner、scope、type 和 API 设计问题”。

## 1. 本章解决什么问题

Rust 诊断经常同时列出错误位置、类型、Trait bound、生命周期、宏展开、await 点和调用链。初学者容易只看最后一句 suggestion，或随手添加 clone、`'static`、`Send`，结果掩盖真正的 ownership 设计。

## 2. 先说人话：编译错误是一份约束冲突报告

编译器不是在说“这段代码整体很差”，而是在说：某个位置要求条件 A，但从当前代码只能证明条件 B。你的任务是找到 A 从哪里引入、B 为什么不足。

## 3. 四个固定问题

每次先回答：

1. Expected 是什么？
2. Found/actual 是什么？
3. 哪个函数、Trait、spawn 或返回类型引入 expected constraint？
4. 应改变 value 的 owner/scope/type，还是改变 API contract？

## 4. 不要从 Error Code 开始背

`E0308` 等编号便于搜索和 `rustc --explain`，但同一编号可由不同设计问题触发。先读类型与数据流，再用错误代码补背景。

## 5. 一条诊断的常见组成

```text
error[E....]: 摘要
 --> file.rs:line:column
  | primary span
  | secondary span / constraint introduced here
note: 原因链或 required bound
help: 可能的局部修复
```

Primary span 不一定是根因定义处，只是冲突最终暴露的位置。

## 6. Primary Span

通常标出无法类型检查的 expression、move、borrow 或 call。先看这一行在做什么，再沿 note 查 expected constraint 从哪里来。

## 7. Secondary Span

它常标出“value 在这里 move”“borrow 在这里开始”“bound 由此函数要求”“await 后仍使用”。这些 span 连起来就是编译器推导的时间线。

## 8. Note

Note 往往比第一句更接近根因，例如 `required by a bound in tokio::spawn`、`future is not Send as this value is used across an await`。

## 9. Help/Suggestion

Suggestion 是根据局部语法生成的候选，不理解完整领域语义。加 `clone()` 也许能编译，却可能复制大对象、改变 identity 或隐藏错误 owner；必须先验证意图。

## 10. 第一个错误优先

一个缺失 import 或错误 generic type 可产生几十个后续 method-not-found、inference failure。先修最早、最靠近本次修改的根因，再重新运行，不要一次处理全部红字。

## 11. Error Cascade

宏生成的 enum 少一个 variant 后，后面的 match、From、schema derive 都可能报错。多个错误共享同一新类型或 symbol 时，先假设 cascade 并找共同上游。

## 12. 缩小检查范围

```bash
cargo check -p codex-core
cargo check -p codex-app-server-protocol
```

`check` 跳过最终 codegen，适合快速类型反馈；仓库最终验证仍应按项目约定运行对应 `just test`、生成器、lint 和 format。

## 13. 保留完整诊断

终端只截最后几十行容易丢失第一条 error 和 bound origin。保存完整输出、关闭无关 warning 噪声或用结构化 message format，让同一错误的 note 不被拆散。

## 14. 修改前先写一句人话

例如：“Queue 要保存 Future 到当前函数返回以后，但 Future 借用了局部 `request`。”这句话比“E0597 lifetime error”更直接指向解决方向。

## 15. Syntax Error

Parser error 常由少一个括号、逗号、分号或 macro grammar 不匹配引起。后续报错位置可能远离真正缺失 token，先检查最近修改和 delimiter pairing。

## 16. Macro Error

诊断可能说 error originates in macro。先找到业务 invocation，再找到匹配的 macro arm；不要试图编辑 expanded/generated file 中的临时代码。

## 17. Name Resolution Error

`cannot find value/type/function` 或 unresolved import 先检查：拼写、module visibility、`use` 路径、re-export、feature/cfg 以及 generated symbol 是否真的存在。

## 18. Cfg 引起的 Missing Symbol

Symbol 在源码中可见，不代表当前 target/feature 下存在。对 definition、module、use 和 callsite 分别查看 `#[cfg]`，确认 predicates 对齐。

## 19. Private Item

`E0603`/private field 错误表示调用跨过了 visibility contract。优先使用现有 public method 或在 owner module 内实现行为，不要本能地把 field 改成 `pub`。

## 20. Mismatched Types：E0308

```rust
let id: ThreadId = raw_string;
```

Expected `ThreadId`，found `String`。这不是让你随便 cast；应查是否存在 `TryFrom<String>`、parse 或 domain constructor，以及转换为何可能失败。

## 21. 从 Expected 反推 API

如果 callee 要 `Arc<dyn TimeProvider>`，它表达共享、动态实现和线程边界；传 `AppServerTimeProvider` value 报错时，应判断是否要 `Arc::new` + coercion，而不是改变 callee 为具体类型。

## 22. Newtype 错误的意义

`ThreadId`、`AbsolutePathBuf` 等故意不与 `String`/`PathBuf` 自动混用。类型错误在阻止未验证 raw value 越过领域边界；正确修复通常是调用验证构造器。

## 23. Codex `ThreadId` 案例

`protocol/src/thread_id.rs` 为 `&str` 和 `String` 实现 `TryFrom`，错误类型是 `uuid::Error`；从 `ThreadId` 到 String 才是不可失败的 `From`。

## 24. Inference Error：E0282/E0283

编译器知道存在多个合法类型或 conversion，却无法从上下文唯一确定。常见于 `collect()`、`parse()`、`into()`、`map_err` closure 和泛型关联类型。

## 25. 给最小边界标注类型

```rust
let ids: Vec<ThreadId> = values.collect::<Result<_, _>>()?;
```

通常在 collection、parse 目标或 closure 参数处加一处标注即可，不必给每个 local 写冗余类型。

## 26. `into()` 为什么容易歧义

`source.into()` 依赖目标类型推断。若一个 value 可转换成多个目标，而返回/参数上下文不够具体，可改用 `Target::from(source)` 或显式 variable type。

## 27. Numeric Literal

整数 literal 可推断为多种类型；与 duration、PID、wire field 或 FFI 类型组合时，应由 API 签名或显式安全 conversion 决定，不要用 `as` 只为消除报错。

## 28. Method Not Found：E0599

可能不是方法真的不存在，而是：Trait 没 import、receiver type 与想象不同、generic bound 不满足、方法被 cfg 删除，或前一条 inference error 让 receiver 变成 unknown。

## 29. 先确认 Receiver Type

诊断会写 `no method named ... found for struct X`。确认当前 expression 是 `T`、`&T`、`Arc<T>`、`Option<T>` 还是 `Result<T,E>`；链式调用上一环常改变 receiver。

## 30. Trait Method 的 Import

Trait 已为类型实现，但 Trait 不在 scope 时，method syntax 可能不可用。查方法定义来自 inherent impl 还是 Trait，再导入真正的 Trait，而非复制同名 helper。

## 31. Associated Function 与 Method

没有 `self` 参数的关联函数用 `Type::new()` 调用，不能写成 `value.new()`。反过来，接收 self 的方法需要 receiver。

## 32. Trait Bound Error：E0277

常见信息是 `the trait bound T: Trait is not satisfied`。关键不是立刻给 T derive Trait，而是找到谁要求该 bound，以及该类型语义上是否应该实现它。

## 33. Bound Origin

```rust
pub(crate) fn add<T>(&mut self, handler: T)
where
    T: CoreToolRuntime + 'static,
```

Tool Registry 的 `add` 明确要求 handler 实现 runtime contract 并能被长期存储。错误 note 会沿调用指回这个 where clause。

## 34. Derive 造成的 Bound

`#[derive(Clone)]` 可能为 generic fields 引入 `T: Clone`。如果 clone wrapper 本不需要 clone T，检查 derive expansion 或手写更精确 impl，而不是向整个类型图传播 Clone。

## 35. `Send` 与 `Sync` 也是 Trait Bound

- `Send`：value 的 ownership 可安全移到另一线程；
- `Sync`：`&T` 可安全在线程间共享；
- 它们不等于“内部完全无锁”或“业务操作原子”。

## 36. `'static` 不是 Trait

`T: 'static` 表示 T 不含必须早于程序结束失效的非 static borrows；owned String、Arc 等通常满足。它不表示 value 一定存活到进程结束。

## 37. `dyn Trait` 与 Concrete Type

`Arc<dyn CoreToolRuntime>` 是类型擦除后的 trait object；`Arc<MyTool>` 是具体类型。Coercion 需要 MyTool 实现 Trait，Trait 可 dyn 使用，并满足 Send/Sync/lifetime 等全部 supertraits。

## 38. Trait Object Compatibility

含 generic method、返回依赖 Self 的不兼容接口等 Trait 可能不能转成 `dyn Trait`。现代诊断常称 not dyn compatible；应设计 object-safe facade 或用 enum/generic static dispatch。

## 39. Codex 的 Erasure Adapter

`SessionTask::run` 返回 `impl Future + Send`，不同实现有不同具体 Future 类型；`AnySessionTask` 则返回 `BoxFuture`，把它们装箱成统一 shape 供动态存储。

## 40. 为什么分两个 Trait

Typed `SessionTask` 保留静态分发和自然 async implementation；blanket impl 为所有 SessionTask 生成 object-safe adapter。若直接把复杂 opaque Future 接口塞进 dyn boundary，编译器会指出 object compatibility 问题。

## 41. Associated Type Mismatch

Iterator/Future/Trait 常通过 `Item`、`Output`、`Error` 等关联类型表达合同。错误可能显示 `<T as Trait>::Item` expected X；沿 Trait impl 查真实关联类型，而非只看 T 名称。

## 42. Non-exhaustive Match：E0004

Enum 新增 variant 后，exhaustive match 会拒绝编译。对内部 state/protocol enum，这常是有价值的变更清单：每个消费者必须明确决定新状态含义。

## 43. Codex Queue Key 案例

`RequestSerializationQueueKey::from_scope` exhaustive match 把每种协议 serialization scope 映射为内部 key/access。新 scope 加入后，编译错误提醒维护者补齐一致性规则。

## 44. 不要随手加 `_`

Wildcard 能消除错误，却可能让新 protocol state 静默走旧 fallback。只有未知值确实共享同一稳定语义时才使用 `_`，外部 non-exhaustive enum 则需按兼容合同处理。

## 45. Move Error：E0382

```rust
let a = value;
use_value(value); // value 已 move
```

先问：谁应成为唯一 owner？后一次使用是误用、应借用，还是确实需要两个独立 owned values？

## 46. Move 不是复制 Bytes

对 String/Vec，move 通常转移 pointer/length/capacity ownership，不复制 heap payload。编译器禁止旧变量继续被当 owner 使用，从而避免 double free。

## 47. `clone()` 的正确问题

只有语义上确实需要两个独立 owned handles/values，且 clone 成本和 identity 合适时才 clone。若只是临时读取，传 `&T`；若应共享同一 owner，考虑 `Arc<T>`。

## 48. Partial Move

从 struct move 一个非 Copy field 后，整个 struct 可能无法继续整体使用，但未移动字段仍可在允许场景单独访问。Destructure 时要明确哪些字段 move、哪些 borrow。

## 49. Cannot Move Out of Borrowed Content：E0507

通过 `&T` 访问字段时，不能把非 Copy owned field 直接拿走，因为 borrower 无权拆掉 owner。可借用字段、clone、让 API 接收 self，或用 `Option::take` 在 `&mut` 下显式替换。

## 50. Move While Borrowed：E0505

Value 仍有活跃 borrow 时不能把 owner move 走。缩短 borrow scope、先完成 borrowed computation，或重新组织 return value；不要用不必要 clone 绕过未理解的引用关系。

## 51. Mutable Borrow Conflict：E0499

同一 value 在重叠范围内不能有两个活跃 `&mut`。常见于 map entry、循环和 async call；可拆分不相交字段、缩短第一个 borrow、或让一个 owner 串行完成变更。

## 52. Mutable 与 Shared Conflict：E0502

共享 borrow 仍被后续使用时，不能同时 mutable borrow。诊断的 secondary span 会标出 immutable borrow 最后使用点；NLL 已尽量缩短范围，剩余冲突通常是真实数据流。

## 53. Non-Lexical Lifetimes

现代 Rust borrow 不必持续到整个花括号结尾，而是到最后一次使用。若仍报冲突，先查看“borrow later used here”，删除无意的晚使用或把计算拆成更小 scope。

## 54. Collection Entry API

先 `get_mut` 再对同一 map insert 容易产生重叠 borrow。`entry` API 常把“查看并可能插入”合成一次独占访问，既符合意图也更易通过 borrow checker。

## 55. Borrow Across Await

Async function 在 await 处暂停并保存仍活跃 locals/borrows。一个看似短的 `&mut` 若 await 后还使用，就会进入 Future state，影响 lifetime 和 Send。

## 56. Lock Guard Across Await

持有 `std::sync::MutexGuard` 跨 await 可能让 Future not Send，或造成阻塞/死锁。常见修复是锁内提取 owned snapshot、释放 guard，再 await；不是盲目换成 async Mutex。

## 57. Codex Queue 的锁范围

`request_serialization.rs` 在小 block 内锁住 queue、push/pop 并形成 owned `requests`，离开 block释放 guard，之后才 `join_all(...).await`。这种结构同时降低锁竞争并避免 guard 跨 await。

## 58. Lifetime Error 的核心

Lifetime error 不是“活得不够久”这么简单，而是某个 reference 被要求在一个范围内有效，但 compiler 只能证明更短范围。画出 owner 创建、borrow 创建、最后使用和 owner drop 四个点。

## 59. Does Not Live Long Enough：E0597

局部 owner 在 reference 最后使用前会 drop。例如把 `&local_string` 存进外层 Vec。修复是让 owner 移到更外层、存 owned value，或缩短 reference 消费范围。

## 60. Returning Reference to Local：E0515

函数不能返回指向自身 local String 的 `&str`，因为 return 后 owner 被销毁。返回 String、从输入 borrow 并绑定 lifetime，或返回指向长期 owner 的引用。

## 61. Temporary Dropped While Borrowed：E0716

```rust
let path = make_path().as_path();
```

Temporary PathBuf 可能在 statement 末尾 drop，reference 却继续使用。先把 owner 绑定到 local，再取得 reference。

## 62. Explicit Lifetime 不是延寿魔法

给函数加 `'a` 只描述输入输出引用关系，不改变 value 实际 drop 时间。若 owner 已销毁，任何 lifetime annotation 都不能让引用合法。

## 63. Lifetime Elision

常见单输入 borrow 的输出 lifetime 可由规则推断。只有多个输入或 Trait object/Future alias 关系不清楚时才需显式标注；标注应表达真实来源。

## 64. Codex `TimeFuture<'a>`

`TimeFuture<'a> = Pin<Box<dyn Future<...> + Send + 'a>>` 允许 provider 返回借用 self 或其他同范围数据的 Future。`current_time(&self) -> TimeFuture<'_>` 把 Future 有效期绑定到 receiver borrow。

## 65. 为什么不是总用 `'static`

TimeProvider operation由调用者立即 await，不必脱离 provider 独立存储。要求 `'static` 会迫使实现无意义 clone/Arc 化，丢失准确的借用合同。

## 66. Borrowed Data Escapes：E0521

把借用参数捕获进要求 `'static` 的 queue/spawn 时，reference 超出函数调用范围。应把必要数据转换成 owned value，或让接收 API 的 lifetime 不再要求脱离调用者。

## 67. Closure May Outlive：E0373

可能长期保存的 closure 默认借用 local 时会报错。`move` closure 转移捕获，但若捕获本身是 `&T`，move 的仍只是 reference，并不会自动变成 owned T。

## 68. `move` 的含义

`async move`/`move ||` 表示按 value 捕获变量；Copy value 被复制，owned value 转移，reference 仍是 reference。它解决捕获方式，不保证 Send、Sync 或 `'static`。

## 69. Spawn 为什么常要求 `'static`

Spawned task可能在当前函数返回后继续运行，runtime 不能接受指向当前 stack local 的借用。将 Arc/String/PathBuf 等 owner move 入 task，使 Future 自己拥有所需状态。

## 70. Codex Queued Future 案例

`QueuedInitializedRequest::new` 要求 `Future<Output=()> + Send + 'static`，因为 Future 被装箱、进入队列，并可能由随后 spawn 的 drain task 执行。

## 71. `'static` Error 的翻译

不要读成“这个对象必须永远不释放”，而应读成：“这个 queued/spawned value 不能依赖调用 stack 的短借用。”解决点通常在 capture ownership。

## 72. Future Is Not Send

Tokio multi-thread spawn 要求 Future 可在线程间移动。编译器会找出哪个跨 await local 不实现 Send，并标注它在哪里创建、await 后在哪里仍使用。

## 73. Send Error 的阅读顺序

1. 找 `required by tokio::spawn`；
2. 找 `future is not Send as this value is used across an await`；
3. 确认该 local 的具体类型；
4. 缩短 scope、转 owned Send data，或确认任务是否应使用 local executor。

## 74. 不要给外部类型强行 `unsafe impl Send`

Send/Sync 是安全合同。若第三方 handle 不可跨线程，正确方案可能是单线程 owner + channel，而不是为了满足 spawn bound 声称它线程安全。

## 75. `Rc` 与 `Arc`

`Rc<T>` 不是 Send/Sync，跨线程 task 常需 `Arc<T>`；但机械替换只解决引用计数原子性，T 本身仍需满足共享合同，内部可变性还需 Mutex/RwLock/actor ownership。

## 76. `RefCell` 与 Mutex

`RefCell` 在运行时检查单线程 borrow，不是 Sync。跨线程共享 mutation 常使用 Mutex/RwLock/atomics 或串行 task，但应按 invariant 选择，不只是跟着 compiler suggestion。

## 77. Local Task

某些 executor 支持 `spawn_local` 接受 `!Send` Future，但要求 LocalSet/单线程语境。它是有意的执行模型选择，不是通用逃生通道。

## 78. Async Trait Return Error

Trait method 返回 Future 时，caller 可能要求 `+ Send`。若实现的 async body 持有 non-Send local跨 await，错误常同时指向 impl body 和 trait method bound。

## 79. Codex `SessionTask` 合同

Trait 本身是 `Send + Sync + 'static`，`run` 返回 Future 还显式 `+ Send`。这让任务对象能共享、Future 能交给 background Tokio task；实现者必须在 async body 中维持这些约束。

## 80. `self: Arc<Self>`

`SessionTask::run` 消费 Arc receiver，而非借用 `&self`。Future 因而拥有一个 strong handle，可返回 `'static` boxed Future，不依赖调用 stack 的 receiver borrow。

## 81. Boxed Future Type Mismatch

`Box::pin(async {...})` 可 coercion 到 `Pin<Box<dyn Future<Output=T> + Send + 'a>>`，但目标上下文必须足够明确。Output、Send 或 lifetime 任一不同都会产生很长的 expected/found type。

## 82. Opaque `impl Future`

每个 `async` block/函数有匿名具体类型；两个外观相同的 async blocks 也是不同类型。需要放进同一 Vec/map 或 trait object 时，用 enum、boxing 或统一 wrapper。

## 83. Recursive Async：E0733

直接递归 async fn 会让 Future 类型无限嵌套。需通过 BoxFuture/Box::pin 引入间接层，或改成显式 stack/loop；先确认递归是否有界和取消安全。

## 84. Await Outside Async：E0728

`.await` 只能在 async context 内。不要为消除错误随意把整条同步调用链改成 async；先决定操作本质是否异步，以及 API boundary 如何传播 Future。

## 85. `?` 的类型要求

在返回 `Result<T, E>` 的函数里，`expr?` 需要成功值接入当前 expression，并能把源 error 转换成 E；转换通常依赖 `From<SourceError> for E`。

## 86. Codex `ThreadId::from_string`

函数返回 `Result<ThreadId, uuid::Error>`，内部 `Uuid::parse_str(s)?` 的错误类型正好是 uuid::Error，因此 `?` 可直接传播。

## 87. Serde Error Mapping

ThreadId Deserialize 返回 `D::Error`，但 UUID parser 产生 `uuid::Error`。源码用 `.map_err(serde::de::Error::custom)?` 显式投影到 serializer contract，而不是期待任意 From impl。

## 88. `?` Cannot Convert Error

先列出 source error 与 function return error；再选择：增加语义正确的 From、局部 map_err、用 anyhow context 做应用层擦除，或改变当前函数签名。不要把所有错误 `.unwrap()`。

## 89. `Option?` 与 `Result?`

`Option` 的 `?` 表示 None 早退，Result 的 `?` 表示 Err 早退；在返回类型不兼容时需 `ok_or_else`、`.ok()` 或显式 match，选择取决于失败是否需要原因。

## 90. Nested Result

Timeout、channel 和远端 response 可形成 `Result<Result<Result<T,E1>,E2>,Elapsed>`。编译错误能提醒你少展开一层；先给每层命名“超时/通道/远端”，再 match，而不是连续 `???`。

## 91. Codex Current Time 案例

`request_current_time` 对 `timeout_at(deadline, rx).await` 分别处理 timeout、oneshot canceled 和远端 protocol error，最后才把 JSON value 反序列化成 typed response。

## 92. Pattern Type Mismatch

Match arm pattern 必须对应 scrutinee shape，所有 arms 还要产生兼容结果类型。Nested Result 少一个 `Ok` 或某 arm 只做日志返回 `()`，都会在 match 处暴露。

## 93. `return`、`break` 与 Semicolon

Rust block 最后无分号 expression 是返回值；加分号变成 `()`。E0308 expected Result found unit时，检查是否误加分号、遗漏 return，或某个 branch 只执行副作用。

## 94. Iterator/Closure Error 定位

长 iterator chain 的错误常指向 collect 末端。逐段给中间 iterator/item type，或临时改成 for loop，找出哪一步把 `&T` 变成 T、Option 变成 Result 或错误类型改变。

## 95. Closure Capture 与 Fn Traits

- `Fn` 可重复调用且不消费 capture；
- `FnMut` 可修改 capture；
- `FnOnce` 可消费 capture；
- API 要 Fn 但 closure move 掉 captured value 时，会报告 trait bound 不满足。

## 96. Auto Deref/Coercion 不是无限的

`&Arc<T>`、`Arc<T>`、`&T` 在方法调用中有自动调整，但 generic inference 和 trait object conversion 并非处处自动。必要时用 `Arc::clone`、`.as_ref()` 或显式 cast 表达边界。

## 97. Borrowed 与 Owned Return

`AbsolutePathBuf::as_path()` 返回借用 view，`to_path_buf()` clone owned path，`into_path_buf()`消费 wrapper。返回/存储时报错时，先按所需生命周期选择，不要随便在三者间替换。

## 98. 错误消息中的巨大类型

Async、iterator、tower service 和 macro 可生成超长类型，compiler 有时写入 `.long-type-...txt`。先找到最外层 `Future/Result/Arc/dyn` 和第一个不同 component，不必逐字符读完整嵌套。

## 99. Type Alias 帮助阅读

`TimeFuture<'a>`、`BoxFutureUnit` 把复杂类型命名成领域合同。诊断仍可能展开 alias；回到 alias definition 查看 Output、Send 和 lifetime，而不是把它当完全不同类型。

## 100. Macro/Derive Bound Error

Serde/TS/JsonSchema derive 生成 impl 后，字段缺 Trait 会在 derive attribute 附近报错。查哪一个 field/type parameter 缺 bound，并确认 production/test 下 macro provider 是否相同。

## 101. Platform-only Error

本机不存在的 cfg branch 未被检查。CI Windows 报 unresolved Win32 symbol时，先看 target-specific dependency feature 和 cfg，不要在 macOS 上仅凭 IDE 灰色代码猜修复。

## 102. Link Error 不是 Rust Type Error

`undefined symbol`、duplicate symbol 出现在 linker 阶段，说明 Rust 已通过类型检查。转去检查 native library、ABI、build.rs、Bazel deps 和 target artifact，不要继续改 borrow lifetime。

## 103. Runtime Panic 也不是 Compile Error

`unwrap`、index 和 invariant panic 说明程序已编译并执行。区分 compile、link、load、runtime failure，才能选择 rustc diagnostic、link command、loader path 或 backtrace 工具。

## 104. 最小复现

把错误缩成一个类型、一个 function signature 和一个 call，保留触发 bound/lifetime 的关键结构。不要把用户仓库秘密或完整业务代码上传到外部 playground。

## 105. `rustc --explain`

```bash
rustc --explain E0382
rustc --explain E0277
```

它解释通用语言规则，不知道 Codex 的业务 owner。读完再回到具体 bound origin 和数据流。

## 106. 编译器 Suggestion 的审核清单

- 加 clone 是否改变 identity/成本？
- 加 move 是否只移动了 reference？
- 加 `'static` 是否把短借用错误地扩大到整个 API？
- 加 Trait bound 是否把内部实现要求泄漏到公共 API？
- 加 `as` 是否截断/改变 signedness？
- 加 wildcard 是否吞掉新状态？
- 加 unsafe Send/Sync 是否真的有安全证明？

## 107. 修复层级

优先顺序通常是：纠正拼写/类型 → 缩短 borrow scope → 调整 ownership transfer → 在边界转换 raw/domain type → 重新设计 trait/async API。越后面的修改影响面越大，需要更多证据。

## 108. 编译通过后的验证

Compiler 证明类型、借用和部分并发合同，不证明 wire 兼容、授权、业务状态、性能或取消终态。通过后仍要运行目标测试并 review diff，确认没有用 clone/wildcard/no-op 改变语义。

## 109. 源码检查点

1. `codex-rs/protocol/src/thread_id.rs`：TryFrom、From、`?`、Serde error mapping 和 newtype 边界。
2. `codex-rs/utils/absolute-path/src/lib.rs`：borrowed/owned/consuming path APIs、Cow 和 lifetime-bearing views。
3. `codex-rs/core/src/current_time.rs`：`TimeFuture<'a>`、borrowed Future、Send 与 TimeProvider trait object。
4. `codex-rs/app-server/src/current_time.rs`：provider implementation、owned captures 和 nested timeout/channel/protocol Result。
5. `codex-rs/app-server/src/request_serialization.rs`：`Send + 'static` queued Future、lock scope 与 spawn。
6. `codex-rs/core/src/tasks/mod.rs`：SessionTask RPITIT、AnySessionTask BoxFuture adapter、Arc receiver。
7. `codex-rs/core/src/tools/registry.rs`：CoreToolRuntime bound、Arc<dyn Trait> registry 和 test coercion。
8. `codex-rs/app-server-protocol/src/protocol/common.rs`：exhaustive generated matches 与 macro-origin diagnostics。

## 110. 搜索命令

```bash
rg -n 'type .*Future|BoxFuture|Future<Output' codex-rs/core/src codex-rs/app-server/src
rg -n '\+ Send|\+ Sync|\+ .static|self: Arc<Self>' codex-rs/core/src
rg -n 'impl (TryFrom|From)|map_err|ok_or_else' codex-rs/protocol/src codex-rs/utils
rg -n 'Arc<dyn|Box<dyn|trait .*:.*Send' codex-rs/core/src
rg -n 'tokio::spawn|\.lock\(\)\.await' codex-rs/core/src codex-rs/app-server/src
```

## 111. 小练习：翻译 Lifetime Error

假设把 `QueuedInitializedRequest::new` 改为接收一个借用 local request 的 Future。不要先写修复；先画 local owner、borrow、enqueue、函数 return 和 drain execution 五个时间点，再说明为什么 queue 要 owned `'static` capture。

## 112. 小练习：翻译 Future Not Send

在一个 spawn Future 中持有 non-Send guard 跨 await。写出诊断中应寻找的三个 note，然后给出“锁内提取 owned snapshot、释放 guard、再 await”的重构，而不是 unsafe impl Send。

## 113. 小练习：追踪 `?`

从 `ThreadId::deserialize` 出发，写出 String deserialize、UUID parse、serde error 三种类型；解释为何 parse error 需要 `Error::custom`，而 `ThreadId::from_string` 可直接 `?`。

## 114. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Diagnostic / error code | 诊断/错误代码 | Compiler 的约束冲突报告和可查询编号 |
| Primary/secondary span | 主/次源码范围 | 冲突暴露位置及 move/borrow/bound 来源位置 |
| Expected / found | 期望/实际 | API 要求的类型或约束与当前 expression 提供的事实 |
| Constraint/bound origin | 约束/边界来源 | 引入 Trait、lifetime、Send、Output 要求的签名位置 |
| Error cascade | 错误级联 | 一个上游错误引发的多条后续诊断 |
| Type inference/annotation | 类型推断/标注 | 从上下文确定类型和在歧义边界显式指定类型 |
| Newtype/domain conversion | 新类型/领域转换 | 用独立类型阻止 raw value 未验证混用 |
| Trait bound / associated type | Trait 约束/关联类型 | Generic 必须实现的能力和 Trait 内命名的类型成员 |
| Dyn compatibility | 动态兼容性 | Trait 是否能形成 `dyn Trait` object 的规则 |
| Type erasure / BoxFuture | 类型擦除/装箱 Future | 将不同具体实现统一为动态 shape |
| Move / partial move | 移动/部分移动 | 转移 ownership 和只转移 struct 某些 fields |
| Borrow conflict / NLL | 借用冲突/非词法生命周期 | 重叠访问违反规则和按最后使用缩短 borrow |
| Does not live long enough | 存活不够久 | Owner 在 reference 所需范围结束前被 drop |
| Temporary | 临时值 | Statement 中产生且通常很快 drop 的匿名 owner |
| Borrowed data escapes | 借用数据逃逸 | Reference 被保存到超过调用范围的 Future/closure/queue |
| `'static` bound | 静态边界 | Value 不包含受较短 lifetime 限制的 borrow |
| Send / Sync | 可移动/可共享 | 跨线程 ownership transfer 和 shared reference 合同 |
| Future not Send | Future 不可跨线程移动 | 跨 await state 中含 non-Send local/capture |
| `impl Future` / opaque type | 实现 Future/不透明类型 | 编译器隐藏但每个定义唯一的具体 Future 类型 |
| Nested Result / residual | 嵌套结果/剩余错误 | 多层失败边界及 `?` 传播的 Err/None 部分 |
| Error conversion | 错误转换 | From、map_err、custom 将 source error 投影到当前合同 |
| Exhaustive match | 穷尽匹配 | 要求每个 enum state 都有明确处理分支 |
| Long type | 超长类型 | Async/iterator/macro 组合产生的深层 generic type |
| Minimal reproduction | 最小复现 | 只保留触发约束冲突的最小类型和数据流 |

## 115. 常见误解

- “红线位置就是根因位置”：它常只是冲突最终暴露点，bound 可能来自更远签名。
- “Compiler suggestion 一定是最佳修复”：Suggestion 不了解 identity、成本和领域合同。
- “加 clone 总能解决 ownership”：它可能复制错误的对象或掩盖 owner 设计。
- “`'static` 表示对象永不释放”：它主要限制内部 borrow，不规定实际 drop 时刻。
- “`async move` 会把 reference 变成 owned value”：它只按 value 捕获那个 reference。
- “Arc 自动让一切 Send + Sync”：Inner T 及其 mutation 仍要满足并发合同。
- “Future not Send 就该 unsafe impl Send”：应先找跨 await 的 non-Send state。
- “所有 method not found 都是缺 import”：Receiver/inference/bound/cfg 都可能是原因。
- “给 match 加 `_` 是安全兼容修复”：它可能吞掉必须显式处理的新状态。
- “`?` 能转换任何错误”：需要 compatible residual/From 或显式 mapping。
- “编译成功就证明修复正确”：类型正确不代表协议、业务和安全语义正确。

## 116. 一句话收束

读 Rust 编译错误的关键不是记住更多编号，而是沿 primary span、secondary span 和 notes 找到“哪个边界要求什么”，再把冲突画成 ownership 与时间线；当 expected/found、owner、borrow、await、Trait bound 和 error conversion 都能用一句人话解释时，修复通常会自然落在正确的 scope 或 API 层，而不是堆叠 clone、`'static`、wildcard 和 unsafe 声明。
