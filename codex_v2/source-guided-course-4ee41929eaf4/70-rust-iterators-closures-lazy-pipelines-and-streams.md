# 70：Rust Iterator、Closure、惰性管线与 Stream

> 源码基线：`4ee41929eaf4`。本章承接第 68 章的 Trait/closure 和第 69 章的 pattern，目标是让长链式表达式变成可以逐段翻译的数据流程。

## 1. 本章解决什么问题

Codex 中常见：

```rust
let sessions = items
    .iter()
    .filter_map(|item| item.details.as_ref())
    .flat_map(|details| details.sessions.iter().cloned())
    .collect::<Vec<_>>();
```

初学者容易把它看成一条咒语。本章会讲清每一段输入、输出、ownership、执行时机和停止条件。

## 2. 先说人话：Iterator 是传送带，不是新容器

Iterator 描述“下一件东西怎么来”。`map`、`filter` 等是在传送带上增加工位；通常直到 `collect`、`for`、`next` 等消费动作出现，数据才真正流动。

## 3. Iterator Trait 的核心

简化后：

```rust
trait Iterator {
    type Item;
    fn next(&mut self) -> Option<Self::Item>;
}
```

每次 `next` 返回一个元素或 None。

## 4. `Item` 是 Associated Type

每种 iterator 决定自己产出什么。`slice::Iter<'a, T>` 的 Item 是 `&'a T`，`vec::IntoIter<T>` 的 Item 是 T。

## 5. Iterator 是有状态的

调用 `next` 会推进当前位置，因此需要 `&mut self`。它消费 iterator 的进度，不一定消费底层集合。

## 6. Iterable 与 Iterator

Rust 用 `IntoIterator` 表示“能转换成 iterator”。Vec 是集合；`vec.into_iter()` 才得到逐项产生元素的 iterator。

## 7. `for` 会调用 IntoIterator

```rust
for item in values { }
```

概念上先执行 `IntoIterator::into_iter(values)`，再不断 next。for 并不是与 iterator 无关的另一套循环系统。

## 8. `iter()`

对集合共享借用：

```rust
values.iter() // Item = &T
```

循环后集合仍可使用，元素不能通过这些引用修改。

## 9. `iter_mut()`

对集合独占借用：

```rust
values.iter_mut() // Item = &mut T
```

可原地修改元素；迭代期间不能同时从别处访问集合。

## 10. `into_iter()`

按值取得集合：

```rust
values.into_iter() // Vec<T> 时 Item = T
```

元素 ownership 被移出，原 Vec 通常不能再用。

## 11. 三者速查

| 写法 | Item | 原集合 |
|---|---|---|
| `iter()` | `&T` | 保留、共享借用 |
| `iter_mut()` | `&mut T` | 保留、独占借用 |
| `into_iter()` | `T` | 被消费 |

## 12. 不要靠方法名猜 Ownership

真正查看 Item 类型。数组、引用和自定义 IntoIterator 实现可能让简化印象失准；IDE 类型提示或编译器错误最可靠。

## 13. Iterator Adapter

`map`、`filter`、`chain` 等接收 iterator 并返回新 iterator，称为 adapter。它们通常是 lazy 的。

## 14. Consumer

`collect`、`fold`、`sum`、`count`、`for_each` 等推动 iterator 到结束，称为消费者或终结操作。

## 15. 惰性求值

```rust
let mapped = values.iter().map(expensive);
```

这里只构造管线，`expensive` 通常尚未执行。若 mapped 从未消费，编译器还会提示 unused iterator。

## 16. 逐项融合

`map(...).filter(...).take(3)` 不必先创建三个中间 Vec。每个源元素逐层经过 adapter，收够三个后停止。

## 17. Lazy 不等于完全零成本

Closure、分支、clone 和最终容器仍有成本；优化器通常能内联很多层，但性能结论仍需测量。

## 18. Closure 基本形状

```rust
|item| item.name.clone()
```

竖线内是参数，后面是表达式或 block。参数类型常由 iterator 的 Item 推断。

## 19. Closure 会捕获环境

```rust
items.filter(|item| allowed.contains(item))
```

Closure 使用外部 allowed。它可能按共享借用、可变借用或 ownership 捕获，取决于用法和 `move`。

## 20. `Fn`、`FnMut`、`FnOnce`

Iterator adapter 多数接收 FnMut，因为 closure 会被多次调用，且可能更新捕获状态。若 closure 消费捕获值，它可能只能 FnOnce。

## 21. `move` Closure

`move` 让捕获变量按值进入 closure，常用于 async/spawn。它不等于 closure 一定只能调用一次；还要看函数体如何使用捕获值。

## 22. 方法引用

```rust
.map(String::as_str)
```

当 closure 只是调用同一个方法时，可用方法引用。仓库约定也偏好这种简洁形式。

## 23. `map`

一进一出，元素数量不变：

```rust
[1, 2, 3].into_iter().map(|x| x * 2)
```

Item 从整数转换为另一个整数。

## 24. Map 不负责过滤

Closure 若返回 Option，管线 Item 就变成 Option；None 仍是一个元素。想同时去掉 None，使用 filter_map。

## 25. `filter`

按 predicate 保留原元素：

```rust
items.iter().filter(|item| item.enabled)
```

注意 closure 参数常是 `&&T`，因为 filter 借用 iterator 的 Item 来测试而不先消费它。

## 26. 为什么 Filter 常出现双引用

若 iterator Item 已是 `&T`，filter 的 predicate 接收 `&Item`，即 `&&T`。Match ergonomics 和自动解引用常隐藏细节，报错时应写出真实类型。

## 27. `filter_map`

Closure 返回 Option：Some 产出元素，None 丢弃。

它等价于“先 map 成 Option，再只保留并拆出 Some”，但语义更直接。

## 28. Migration 案例第一段

```rust
items.iter().filter_map(|item| item.details.as_ref())
```

输入 `&Item`，只保留 details 存在的项，输出 `&Details`。没有 clone，也没有取得 details ownership。

## 29. `flat_map`

每个输入产生零到多个输出 iterator，再把它们摊平成一条流。

## 30. Migration 案例第二段

```rust
.flat_map(|details| details.sessions.iter().cloned())
```

每个 Details 有一组 session；iter 借用，cloned 把 `&Session` 变成 owned Session，flat_map 合并所有组。

## 31. `flatten`

当 Item 本身就是 IntoIterator，可用 flatten 压平一层。`map(f).flatten()` 常可改为 `flat_map(f)`。

## 32. Flatten Option

Option 也能 IntoIterator：Some 产生一个元素，None 产生零个。因此 flatten 可以去掉一层 Option，但 filter_map 往往更易表达“转换并过滤”。

## 33. `cloned()`

对 Item=`&T` 的 iterator 调用 cloned，产出 T，要求 T: Clone。它明确指出 ownership 转换发生在这里。

## 34. `copied()`

对 `&T` 产出 T，要求 T: Copy。适合数字、bool、小型 Copy ID，不会调用可能昂贵的 Clone。

## 35. Clone 的位置影响成本

先 filter 再 cloned 通常只复制保留元素；先 cloned 再 filter 可能复制最终会丢弃的元素。先缩小集合，再取得 ownership。

## 36. `collect`

把 iterator 消费到某个实现 FromIterator 的目标类型：Vec、HashMap、String、Result 等。

## 37. Collect 为什么常需要类型提示

同一 Item 可收集成多种容器。可写：

```rust
let values: Vec<_> = iterator.collect();
// 或
iterator.collect::<Vec<_>>()
```

## 38. Turbofish

`::<Vec<_>>` 因形状像鱼而俗称 turbofish。它给泛型方法显式提供类型参数；内部 `_` 仍让编译器推断元素类型。

## 39. Collect HashMap

Iterator Item 若是 `(K, V)`，可收集为 HashMap。重复 key 的覆盖行为由目标集合实现决定，不能假设自动报错。

## 40. Collect String

char iterator 可以收集成 String；`&str` 片段通常也可按相应 FromIterator/extend 规则组合。留意中间分配和编码边界。

## 41. `collect::<Result<Vec<_>, E>>()`

当 Item 是 `Result<T, E>`，collect 会：

- 全部 Ok → `Ok(Vec<T>)`；
- 遇到第一个 Err → 立即返回 Err。

## 42. Thread item 案例

Codex 把每个 stored item 反序列化为 Result，再 collect 成 `Result<Vec<_>, _>`，最后 `?` 传播首个错误。它不会静默跳过坏 item。

## 43. 与 Filter_map Result 不同

`filter_map(|x| parse(x).ok())` 会丢弃错误；`map(parse).collect::<Result<...>>()` 会保留失败语义并短路。二者不能随意互换。

## 44. `collect::<Option<Vec<_>>>()`

同理，一串 Option 全部 Some 才得到 Some(Vec)；遇到 None 短路为 None。

## 45. `any`

遇到第一个 true 立即停止，返回是否至少一项满足条件。它不会为结论已经确定后继续扫描。

## 46. `all`

遇到第一个 false 立即停止。空 iterator 的 all 为 true，这是逻辑上的 vacuous truth，业务上需确认是否符合期望。

## 47. 增量请求案例

Codex 把上一请求 items 与 response additions `chain`，再与当前请求前缀 `zip`，最后 `all` 比较对应项。任一不等便停止并拒绝增量复用。

## 48. `chain`

先产出第一个 iterator 的全部元素，再产出第二个。两边 Item 类型必须一致。

## 49. Chain 不去重

它只是连接。需要唯一性应显式使用 Set、dedup 或业务 key 逻辑。

## 50. `zip`

把两个 iterator 对应位置组成 tuple；任一侧结束时停止。

## 51. Zip 的静默截断风险

如果长度必须相等，单用 zip+all 可能漏掉较长一侧尾部。Codex 案例先计算并校验长度/切片，再 zip，因此语义完整。

## 52. `enumerate`

为元素附加从 0 开始的 usize index。排序后想恢复原顺序时，可先 enumerate 保存 ordinal。

## 53. `rev`

反向迭代，要求 iterator 支持 DoubleEndedIterator。它不一定创建反转后的新 Vec。

## 54. `skip` 与 `take`

Skip 跳过前 N 项，take 最多取 N 项。配合 lazy iterator 可形成容量边界，但源 iterator 的实现仍决定取得每项的成本。

## 55. `take_while`

持续取 predicate 为 true 的前缀，首次 false 后停止。它不是过滤整个序列中的所有 true。

## 56. `skip_while`

只跳过开头连续满足条件的项，首次 false 后后续全部保留。

## 57. `find`

返回第一个满足 predicate 的原元素；短路停止。

## 58. `find_map`

逐项执行返回 Option 的转换，返回第一个 Some。Shell argv 解析用 `windows(3).find_map(...)` 找到第一个 `-c`/`-lc` 三元组并构造结果。

## 59. Slice `windows`

`command.windows(3)` 产生重叠的长度 3 切片。它是 slice 方法，结果仍是 iterator；不足 3 个元素时为空。

## 60. Slice `chunks`

产生不重叠块，最后一块可以不足指定大小。适合把 API 请求按最大批量拆分。

## 61. `peekable`

为 iterator 增加查看下一个元素而不消费的能力。适合 parser 和相邻元素分组。

## 62. `next_if`

若下一个元素满足条件就消费并返回，否则保留它。`history_item_groups` 用它把紧随 source 的 resize notice 附上。

## 63. `from_fn`

用 closure 定义每次 next 的行为。Closure 返回 Some(item) 继续，None 结束。

## 64. History 分组案例

它把输入转成 peekable，每轮先取 source，再有条件取一个 attached notice，产出 `HistoryItemGroup`。这是手写小型 iterator state machine。

## 65. `once`

`std::iter::once(value)` 产生恰好一个元素。Group 用 once(source).chain(optional_notice) 把一或两个 item 统一为 iterator。

## 66. Option 直接参与 Chain

Option 实现 IntoIterator，Some 是一个元素、None 是零个。因此 `.chain(self.attached_notice)` 无需先转 Vec。

## 67. `fold`

把 accumulator 和每个元素反复组合成最终值：

```rust
iter.fold(initial, |acc, item| next_acc)
```

## 68. Sparse overlay 案例

Path 从后往前 fold：初始是 leaf value；每个 segment 再包一层 TOML table，最终构造嵌套 overlay。

## 69. Fold 的阅读法

先写出 accumulator 类型，再逐轮模拟两个元素。若 accumulator 同时承担状态和输出，命名比缩短代码更重要。

## 70. `try_fold`

像 fold，但 closure 返回 Result/Option，可在失败时短路。复杂 fallible accumulator 可考虑它，而不是在 fold 中隐藏错误。

## 71. `reduce`

用第一个元素作为初始 accumulator，空 iterator 返回 None。Fold 有显式初值，空 iterator 也总能给结果。

## 72. `scan`

维护内部状态并为每个输入选择是否产出下一个输出，适合运行累计值；过度使用会比显式循环难读。

## 73. `map_while`

Closure 返回 Some 时继续产出，首次 None 后停止。它表达“转换有效前缀”，不是 filter_map 的“跳过所有 None”。

## 74. `inspect`

在不改变 Item 的情况下观察元素，常用于临时诊断。不要把关键业务副作用藏进 inspect。

## 75. Iterator 消费自己

许多 adapter 方法接收 self，因此旧 iterator name 被 move 到新 adapter。通常这正是组合管线所需行为。

## 76. `by_ref`

若想先消费 iterator 一部分、之后继续用同一 iterator，可通过 by_ref 临时借用它。代码应明确两阶段边界。

## 77. Iterator 可能是 Infinite

`repeat`、`successors` 等可无限产生元素。对无限 iterator 直接 collect/count 会永不结束；先 take 或确保短路 consumer。

## 78. Size hint

Iterator 可报告长度下界和可选上界，容器据此预分配。Hint 是优化信息，除非 ExactSizeIterator，否则不是完整语义保证。

## 79. ExactSizeIterator

知道精确剩余长度的 iterator。并非所有 adapter 保留这一能力。

## 80. FusedIterator

正常 Iterator 在第一次 None 后，原则上未来 next 未必永远 None；FusedIterator/`.fuse()` 提供结束后持续 None 的保证。

## 81. Iterator 与 Collection 的取舍

保持 iterator 可减少中间分配；collect 成 Vec 则能排序、随机访问、多次遍历、跨 await 保存或明确快照边界。

不要为了“函数式”拒绝合理 materialization。

## 82. Materialize

把 lazy sequence 收集成实际容器叫物化。它确定当下快照，也会分配内存并消费源。

## 83. 为什么锁内常先 Collect

Codex 关闭 threads 时在 read lock 内 clone ID/Arc 并 collect，随后释放锁，再异步 shutdown。这样不会持锁跨 await。

## 84. Collect 是并发边界

该 Vec 不只是语法方便，而是从共享 map 捕获一份 owned snapshot，使后续 futures 满足 ownership 条件。

## 85. Iterator 不是 Async

Iterator::next 是同步调用，不能等待尚未到达的网络消息。Future/Stream 解决“下一项可能尚未 ready”。

## 86. Stream

Stream 可近似理解为异步 Iterator：poll_next 可能 Pending，之后被唤醒。通常通过 `StreamExt::next().await` 消费。

## 87. Stream 的 `map`

它仍只把“已有的一项”同步转换。若 closure 产生 Future，得到的是 Stream<Item=Future>，还需并发/顺序执行 adapter。

## 88. `stream::iter`

把同步可迭代集合包装成 Stream。它不会自动让工作并发；只是让后续可使用 Stream combinators。

## 89. Session import 管线

源码流程：

1. `stream::iter(sessions)`；
2. map 每个 session 为 async block；
3. `buffer_unordered(CONCURRENCY)` 执行有界并发；
4. `next().await` 按完成顺序取结果。

## 90. `buffer_unordered`

最多同时 poll N 个 inner futures，谁先完成谁先产出。它控制并发上限，但不保留输入顺序。

## 91. Ordered 与 Unordered

若输出顺序是协议语义，不能随意换成 unordered。可用带 index 的结果排序恢复顺序，或选择保序并发 adapter。

## 92. `FuturesUnordered`

动态维护一组并发 Future，完成即产生结果。Thread manager 用它并行 shutdown，并把 outcome 分类进 report。

## 93. `join_all`

等待一组 Future 全部完成并按输入顺序返回结果。它不天然提供大规模工作负载所需的并发上限。

## 94. Iterator Concurrency 不是自动的

`.map(|x| async move { ... })` 只创建 futures。必须 await、join、buffer 或 spawn，工作才会被驱动。

## 95. 有界并发

处理未知/大量输入时，应明确 maximum in-flight，避免一次构造或启动无限网络、文件和任务操作。

## 96. Stream Short-circuit 与 Cleanup

提前 drop stream 会取消尚未完成的 futures，具体副作用是否已发生取决于 future 实现。设计时要区分可取消计算和外部不可回滚动作。

## 97. Pipeline 的调试法

给每一段写下：

| 阶段 | Item 类型 | ownership | 数量变化 | 是否短路 |
|---|---|---|---|---|
| iter | `&Item` | borrow | 不变 | 否 |
| filter_map | `&Details` | borrow | 减少 | 否 |
| flat_map+cloned | `Session` | owned | 展开 | 否 |
| collect | `Vec<Session>` | owned | 物化 | 消费完 |

## 98. 从最后的 Consumer 倒读

先看最终需要 Vec、bool、Option 还是 Result；再反向追每个 adapter 怎样改变 Item。长链往往比从左向右逐字符读更快理解。

## 99. 拆链不是失败

推断错误或业务复杂时，加入具名中间变量与显式类型。可读性和诊断价值通常大于少一两行。

## 100. 常见错误：Value Used After Move

通常是 into_iter 消费了集合。若后面还需集合，考虑 iter、先提取 snapshot，或确认 clone 的成本和语义。

## 101. 常见错误：Closure May Outlive Borrow

Closure/Future 被存储或 spawn，但捕获短借用。根据边界选择 move owned data、Arc 或缩短作用域，不要盲目加 static。

## 102. 常见错误：Type Annotations Needed

多半是 collect 目标、Ok 的错误类型或 closure 参数不唯一。给最靠近歧义处的变量/collect 加类型，而不是给整条链每处标注。

## 103. 常见错误：Expected FnMut, Found FnOnce

Closure 在第一次调用时消费了捕获值，但 adapter 需要多次调用。改为借用、每轮 clone 合适数据，或重新设计 ownership。

## 104. 常见错误：Async Closure 返回 Future

同步 iterator 的 map 不会 await。确认应顺序 for+await、join_all、FuturesUnordered 还是 buffer_unordered。

## 105. 常见错误：Filter 参数多一层 `&`

写出 Iterator::Item，再记住 filter predicate 借用 Item。必要时用 pattern `|&&x|`、自动解引用或先 copied/cloned，但不要为消除错误无意义 clone。

## 106. 性能检查清单

- Clone 在过滤前还是后？
- 是否创建不必要中间 Vec/String？
- Collect 是否无边界？
- Any/all/find 是否能短路？
- Zip 是否先验证长度？
- Async in-flight 是否有硬上限？
- Unordered 是否破坏顺序语义？
- 锁是否跨 await/昂贵 closure 持有？

## 107. 测试 Iterator 逻辑

优先验证最终完整集合和顺序；另外覆盖空输入、单项、边界项、首个错误、短路和重复 key。不要只测试某个静态 adapter 名字出现。

## 108. 源码检查点

1. `codex-rs/core/src/compact_remote_history.rs`：IntoIterator、peekable、next_if、from_fn、once/chain。
2. `codex-rs/app-server/src/external_agent_migration/processor.rs`：filter_map、flat_map、cloned、collect、any。
3. `codex-rs/app-server/src/request_processors/thread_processor.rs`：collect Result。
4. `codex-rs/core/src/client.rs`：chain、zip、all 与长度前置检查。
5. `codex-rs/app-server/src/config_manager_service.rs`：rev/fold 与 Option zip。
6. `codex-rs/core/src/tools/runtimes/shell/unix_escalation.rs`：windows/find_map。
7. `codex-rs/app-server/src/external_agent_migration/session_importer.rs`：stream、buffer_unordered、next await。
8. `codex-rs/core/src/thread_manager.rs`：锁内 snapshot、FuturesUnordered 和完成顺序。

## 109. 搜索命令

```bash
rg -n '\.(iter|iter_mut|into_iter)\(\)' codex-rs/core/src
rg -n '\.(filter_map|flat_map|find_map|fold|any|all)\(' codex-rs
rg -n 'collect::<Result|collect::<FuturesUnordered' codex-rs
rg -n 'buffer_unordered|FuturesUnordered|join_all' codex-rs
```

## 110. 理解检查

1. iter、iter_mut、into_iter 各产出什么 ownership？
2. 为什么 map 后不 collect 可能什么都不执行？
3. filter_map 和 map(parse).collect Result 有何语义差异？
4. zip 为什么可能静默截断？
5. collect 为什么可成为锁与 async 之间的边界？
6. stream::iter.map(async closure) 是否已经并发执行？
7. buffer_unordered 保证什么、放弃什么？
8. 读长链时应记录哪四个维度？

## 111. 答案

1. 共享引用、可变引用、owned element；具体仍以 IntoIterator 实现为准。
2. Adapter 惰性，只构造管线，consumer 才驱动 next。
3. 前者丢弃 None；后者遇首个 Err 短路并保留失败。
4. 任一侧结束即停止，较长一侧尾部不会进入比较。
5. 锁内复制 owned snapshot 后释放锁，异步工作不再借用 guard/map。
6. 没有，只创建一串 Future；还需 buffer/join/await 驱动。
7. 限制最多 N 个 in-flight，并按完成顺序产出，不保留输入顺序。
8. Item 类型、ownership、元素数量变化、是否/何时短路。

## 112. 常见误解速查

| 误解 | 更准确的理解 |
|---|---|
| Iterator 就是 Vec | Iterator 是逐项产生规则，Vec 是已物化容器 |
| map 立即遍历 | Adapter 通常惰性，consumer 才驱动 |
| into_iter 只是换写法 | 它常取得集合和元素 ownership |
| filter_map 会报告错误 | 它只根据 Option 保留/丢弃；错误需 Result 语义 |
| zip 会验证等长 | 它在较短一侧结束时静默停止 |
| collect 只能得到 Vec | 目标由 FromIterator 决定，也可为 Map/Result/Option |
| async map 自动并发 | 它通常只产生 Future items |
| unordered 只是更快 | 它还改变输出顺序，必须符合业务语义 |

## 113. 本章词汇表

| 英文/代码 | 字面翻译 | 实际含义 |
|---|---|---|
| Iterator / IntoIterator | 迭代器/转为迭代器 | 逐项 next 的状态机，以及可生成它的类型契约 |
| Item | 元素 | Iterator 每次 next 产生的 associated type |
| Adapter / consumer | 适配器/消费者 | 返回新 iterator 的 lazy 操作，以及驱动执行的终结操作 |
| Lazy / eager | 惰性/立即 | 需求出现才计算，以及立刻完成计算/物化 |
| Closure / capture | 闭包/捕获 | 匿名函数及其使用的外部环境 |
| `iter` / `iter_mut` / `into_iter` | 共享/可变/所有权迭代 | 产出 `&T`、`&mut T` 或 T 的常见集合入口 |
| `map` / `filter` | 映射/筛选 | 一进一出转换，以及保留原 Item 的 predicate |
| `filter_map` | 筛选映射 | Option 同时表达转换结果和是否保留 |
| `flat_map` / `flatten` | 展开映射/压平 | 每项产生多项并合并，以及压平一层序列 |
| `cloned` / `copied` | 克隆/复制 | 把引用 Item 转为 owned Clone/Copy value |
| `collect` / FromIterator | 收集/从迭代器构造 | 消费序列并按目标类型物化 |
| Short-circuit | 短路 | 结论或失败确定后停止拉取元素 |
| `chain` / `zip` | 串接/拉链配对 | 先后连接序列，以及按位置配对到较短侧结束 |
| `peekable` / `next_if` | 可偷看/条件取下项 | 观察相邻元素且仅在条件满足时消费 |
| `fold` / accumulator | 折叠/累加器 | 以状态反复组合每项得到单一结果 |
| Materialize | 物化 | 把 lazy sequence 变为实际集合快照 |
| Size hint / exact size | 大小提示/精确大小 | 预分配辅助信息和精确剩余长度保证 |
| Stream / poll | 异步流/轮询 | 下一项可 Pending 的异步序列及其驱动机制 |
| `buffer_unordered` | 无序缓冲 | 有界并发执行 inner futures 并按完成顺序输出 |
| `FuturesUnordered` | 无序 Future 集合 | 动态并发轮询并产出已完成任务的容器 |
| In-flight | 进行中 | 已启动但尚未完成的异步工作数量 |
| Turbofish | 涡轮鱼 | `::<Type>` 显式泛型类型参数语法俗称 |

## 114. 代码短语拆解

| 代码短语 | 如何理解 |
|---|---|
| `details.as_ref()` | Option 内 owned value 转成可选引用，不取得 ownership |
| `sessions.iter().cloned()` | 先借用浏览，再为选中 session 创建 owned clone |
| `collect::<Result<Vec<_>, _>>()?` | 全部成功才得到 Vec，首错短路并向外传播 |
| `previous.iter().chain(added)` | 不分配地顺序浏览旧输入和新增响应项 |
| `.zip(current).all(equal)` | 对位比较并在首个不等时停止 |
| `path.iter().rev().fold(...)` | 从叶到根逐层包装嵌套结构 |
| `windows(3).find_map(...)` | 查看每个重叠三元组，返回首个可解析结果 |
| `stream::iter(sessions)` | 把同步 session 序列提升为异步 Stream |
| `buffer_unordered(N)` | 最多 N 个并发，完成顺序即输出顺序 |
| `collect::<FuturesUnordered<_>>()` | 建立一组并发推进、逐个完成的 futures |

## 115. 与前后章节的关系

- 第 55 章讲 bytes/lines 的流式有界读取。
- 第 63 章讲中间 Vec、clone 和高水位内存。
- 第 68 章讲 Iterator associated type、generic bound 和 Fn traits。
- 第 69 章讲 closure 内使用的 match、if let 与 pattern。
- 第 27/38 章讲 async task、背压和有界并发。

## 116. 本章结论

读 Iterator 管线时，永远先写出当前 Item 类型和 ownership，再判断 adapter 如何改变数量与类型，最后找到真正驱动执行的 consumer。`iter`、`iter_mut`、`into_iter` 决定借用还是移动；`filter_map`、`flat_map`、`collect<Result>` 决定缺失、展开和失败语义；`any/all/find` 决定短路；`collect` 常同时建立内存快照和异步 ownership 边界。

Stream 看起来像 Iterator，但下一项可以尚未 ready。把 async closure 放进 map 只产生 futures，必须显式选择顺序 await、join、FuturesUnordered 或有界 `buffer_unordered`，并明确是否保序、如何取消以及最多允许多少 in-flight 工作。
