# 71：Rust 集合——Vec、Map、Set、Queue、顺序与 Entry API

> 源码基线：`4ee41929eaf4`。本章不把集合当成 API 清单，而是从访问模式、顺序、唯一性、容量和 ownership 选择数据结构。

## 1. 本章解决什么问题

为什么 Tool Registry 使用 `IndexMap`，Thread 状态使用 `HashMap`，配置使用 `BTreeMap`，请求队列的 value 又是 `VecDeque`？容器名称不同，实际上是在声明不同业务语义。

## 2. 先说人话：容器是业务规则的一部分

选择容器等于回答：按位置还是 key 查？是否去重？顺序是否稳定？头部是否频繁删除？是否需要范围查询？最多允许多少项？

## 3. 选择前的六个问题

1. 主要操作是什么？
2. key 是否唯一？
3. 遍历顺序有没有意义？
4. 是否频繁从两端操作？
5. 是否需要有序/range 查询？
6. 容量如何受限？

## 4. `Vec<T>`

连续、可增长序列。擅长尾部 push/pop、按 index 读取、顺序遍历和 cache-friendly 扫描。

## 5. Vec 的 Length 与 Capacity

Length 是已初始化元素数；capacity 是不重新分配可容纳的数量。capacity 大于 length 不代表那些位置已有值。

## 6. Vec 扩容

超过 capacity 时可能申请更大内存并移动元素。引用到 Vec 元素时不能同时进行可能扩容的可变操作，借用检查会保护这一点。

## 7. `with_capacity`

已知合理上界时可减少扩容，但它不限制最大长度，也不初始化元素。外部输入仍需硬上限。

## 8. 尾部操作

`push`/`pop` 通常摊销 O(1)。Vec 很适合作为 stack：后进先出。

## 9. 头部删除

`remove(0)` 需要搬移后续元素，通常 O(n)。频繁 FIFO 不应默认用 Vec。

## 10. 中间 Insert/Remove

同样可能搬移尾部。若数据量小或操作少，Vec 仍可能比复杂结构更简单、更快；复杂度不是唯一因素。

## 11. Slice `&[T]`

Slice 是一段连续元素的借用视图，不拥有分配。API 只需读取序列时，接收 slice 通常比强迫 Vec 更通用。

## 12. `VecDeque<T>`

双端队列，擅长 `push_back/pop_front` 和两端操作。内部可能环绕，不保证所有元素处在一段连续内存。

## 13. FIFO Queue

入队 `push_back`、出队 `pop_front`，形成先进先出。请求序列化队列正需要这一顺序。

## 14. BFS Queue

Agent subtree 恢复用 VecDeque：父节点出队，将子节点放到尾部，形成 breadth-first traversal。

## 15. 滑动窗口

Guardian 保存 recent denials：尾部加入新 bool，超过窗口就从头部丢弃。容器结构直接对应“最近 N 项”。

## 16. 有界不是 VecDeque 自动提供的

VecDeque 会无限增长。必须在写入路径显式检查长度、驱逐旧项、拒绝新项或施加背压。

## 17. `HashMap<K, V>`

按 key 平均 O(1) 查找的 map。它表达 key 唯一，但默认迭代顺序不稳定、也不是插入顺序。

## 18. Hash 与 Eq

HashMap key 通常需实现 Hash + Eq。相等的 key 必须产生相同 hash，否则容器语义被破坏。

## 19. Hash Collision

不同 key 可以有同一 hash，HashMap 仍用 Eq 区分。Collision 不是“两个 key 自动互相覆盖”。

## 20. HashMap 顺序不可作为协议

若把直接迭代结果写入 snapshot/API，输出可能不稳定。需要稳定输出时排序、使用有序 map，或明确保留插入顺序。

## 21. Request Queue 的二层结构

```rust
HashMap<RequestSerializationQueueKey, VecDeque<Request>>
```

外层按资源 key 找队列；内层保持该资源请求 FIFO。两种容器分别表达分桶和顺序。

## 22. Per-key Serialization

不同 key 可独立 drain；同一 key 的请求按队列规则协调。理解这里不能只看到 Mutex，还要读 map 和 deque 的组合。

## 23. `BTreeMap<K, V>`

按 key 排序的树 map，查找通常 O(log n)。遍历顺序稳定为 key order，并支持 range 查询。

## 24. 为什么配置常用 BTreeMap

配置、标签和生成文本经常希望确定性顺序，便于测试、diff 和序列化。选择它往往不只是查找性能。

## 25. BTreeMap 不保留插入顺序

它按 Ord 排序。先插入 z 再插入 a，遍历仍通常是 a、z。

## 26. `IndexMap<K, V>`

提供 map 查找，并保存可观察的插入顺序。适合顺序与 key lookup 都是语义的 registry。

## 27. Tool Registry 为什么用 IndexMap

工具按 name 唯一查找，同时暴露/遍历顺序需要稳定。`prepend_trusted` 还能明确把工具插到开头。

## 28. IndexMap 顺序不是 Sorted Order

它是插入/显式移动顺序，不按 key 大小排序。与 BTreeMap 的“稳定”来源不同。

## 29. 删除对顺序的影响

IndexMap 有保持顺序和 swap-remove 等不同删除策略。调用具体 API 前确认是否允许被交换元素改变位置。

## 30. `HashSet<T>`

只保存唯一 key，没有单独 value。适合 membership、去重、交并差；默认迭代顺序不稳定。

## 31. Set Insert 的返回值

Insert 通常返回是否首次插入。可一次完成“检查是否见过 + 标记已见”，避免两次 lookup。

## 32. 去重与顺序

HashSet 去重会失去稳定顺序。想保留首次出现顺序，可用 Set 做 seen，同时把首次值 push 到 Vec。

## 33. `BTreeSet<T>`

唯一且按 Ord 排序。适合需要确定性输出、range 或稳定测试的 key 集合。

## 34. Set 不是 Bool Map 的唯一替代

`HashMap<K, bool>` 可能有 true/false 两种显式状态；Set 只表达存在/不存在。先确认 false 是否与缺失相同。

## 35. Map Insert 的覆盖语义

`insert(key, value)` 若 key 已存在，通常替换旧 value 并返回它。若重复 key 是错误，必须显式拒绝，不能忽略返回值。

## 36. Tool 名称冲突

Registry 对 trusted duplicate 调用 error/panic 策略，对 external duplicate 警告并跳过。相同容器操作背后有不同 trust policy。

## 37. Entry API

```rust
match map.entry(key) {
    Entry::Vacant(entry) => { entry.insert(value); }
    Entry::Occupied(entry) => { /* duplicate */ }
}
```

它只做一次 key 定位，并明确区分空位与已占用。

## 38. `or_insert`

缺失时插入默认值，存在时返回现有值的可变引用。适合随后原地更新。

## 39. `or_default`

缺失时插入 `V::default()`。Thread status 用它创建新的 RuntimeFacts 或 watch sender 所需状态。

## 40. `or_insert_with`

Closure 只在缺失时执行，适合昂贵或依赖 key 的构造。不要提前构造再用 or_insert 浪费成本。

## 41. `and_modify`

存在时原地更新，可与 `or_insert` 组合计数：

```rust
map.entry(key).and_modify(|n| *n += 1).or_insert(1);
```

## 42. Entry 借用范围

持有 Entry 或其 value 引用时，不能随意再次可变借用同一个 map。将更新限制在小 scope，再进行 remove/遍历。

## 43. Lookup 用 Borrowed Key

`HashMap<String, V>` 常可用 `&str` get，避免仅为 lookup 分配 String。具体依赖 Borrow、Hash/Eq 一致性。

## 44. `contains_key` + `insert` 的竞态语义

普通局部 map 内只是重复查找；共享 map 若两步之间释放锁，还会形成 check-then-act 竞态。应在同一锁/entry/事务边界完成。

## 45. `get`、`get_mut`、`remove`

分别共享借用 value、独占借用 value、取得 ownership 并删除。选择反映调用方是否需要修改或接管资源。

## 46. Remove 可能承担资源交接

从 map remove task/session handle 不只是删索引；返回的 owner 可能负责 shutdown、await 或 drop cleanup。

## 47. `retain`

原地保留满足条件的元素，适合批量清理。Closure 中仍受借用规则限制，且大集合操作可能长时间占锁。

## 48. Drain

Drain 移出一批元素并让调用方逐项处理。它常建立“集合不再拥有、清理流程接管”的 teardown 边界。

## 49. Clear

清空并 drop 元素。若 value 的 Drop 只做同步清理而业务还需要 async shutdown，不能以 clear 代替显式收尾。

## 50. Queue Drain 案例

请求队列在锁内 pop 出当前可运行 batch，然后释放锁，锁外 `join_all`。这避免持 Mutex guard 跨 await。

## 51. Shared-read Batching

第一个请求是 SharedRead 时，继续读取队首连续 SharedRead；遇到 Exclusive 停止。FIFO 顺序与 access enum 共同定义调度策略。

## 52. 为什么只合并连续 SharedRead

越过前面的 Exclusive 去取后方 read 会破坏排队公平性或资源时序。容器顺序在这里是并发 correctness 的一部分。

## 53. Map + Mutex 不等于无限安全

Mutex 保护内存竞态，但不能自动提供容量、公平、超时、持久性或业务幂等。每项都需要单独设计。

## 54. Key 设计

好 key 必须包含身份隔离所需维度：账号、环境、thread、generation、namespace 等。漏维度会把不应共享的 value 混在一起。

## 55. Composite Key

Rust 常用 tuple 或 struct key。具名 struct 比长 tuple 更容易审查字段含义，也可集中实现规范化。

## 56. Mutable Key 风险

插入 map 后若 key 的 Hash/Eq/Ord 相关内容通过内部可变性改变，查找结构可能失去一致性。Key 应在容器中保持逻辑不可变。

## 57. Capacity 与 Length

Map/Vec 的 capacity 是分配细节，不是业务 quota。业务上限应以条目数、字节、token、时间或组合预算明确检查。

## 58. 多维上限

固定条目数仍可能被巨型 String 撑爆。Codex 输出缓存常同时需要 bytes 和 chunks/items 上限。

## 59. Eviction

满时可拒绝、删除最旧、删除最近最少使用、按 TTL 清除或背压等待。策略必须符合数据是否可重建、是否含副作用。

## 60. LRU 与 VecDeque

简单队列可近似记录访问顺序，但每次 touch 时线性查找删除旧位置可能昂贵。真实 LRU 通常需要 map + linked order 的组合或成熟实现。

## 61. Resident Agent 案例

Residency 使用 VecDeque 保存顺序：touch 时移到尾部，扫描时 pop_front；若候选不能驱逐再 push_back。要同时阅读队列与状态 map。

## 62. Stable Output

用户界面、snapshot 和 schema fixture 需要稳定顺序。可选择 BTree/IndexMap，或在边界显式 sort；不要依赖 HashMap 偶然输出。

## 63. Sort 与容器选择

偶尔输出一次有序结果时，HashMap + collect + sort 可能合理；频繁 range/有序遍历时 BTreeMap 更自然。

## 64. `sort` 与 `sort_unstable`

Stable sort 保留相等元素原相对顺序；unstable 不保证但可能更快/省内存。若 tie 顺序影响用户输出，必须显式决定。

## 65. Tie-breaker

只按 score 排序会让相同 score 顺序不确定。Codex fuzzy search 使用 score 后再按 path，建立全序和可重复输出。

## 66. Duplicate 与 Collision 不同

Duplicate 是 Eq 意义同一 key；hash collision 是 hash 值相同但 key 不同。业务冲突策略处理前者，HashMap 内部处理后者。

## 67. Collection 与 Serialization

序列化 map 时还要考虑 wire 是否允许非字符串 key、顺序是否规范、重复 key 解码策略。内存容器正确不代表 wire contract 完整。

## 68. Collection 与 Ownership

`Vec<Arc<T>>` 表示容器拥有共享 owner；`Vec<&T>` 借用别处数据；`HashMap<K, JoinHandle>` 可能拥有后台任务。先读 value 类型再判断 drop 影响。

## 69. Collection 与 Lock Scope

常见安全模式：锁内查找/更新/clone 所需 owned handles，释放锁，再 await 或做昂贵工作。

## 70. 分片锁与单锁

单 Mutex<HashMap> 简单且保证跨 key 原子更新，但热点时会竞争。分片/每 key 锁提高并发，却增加生命周期、删除和死锁复杂度。

## 71. 不要在没有证据时换容器

从 HashMap 换 BTreeMap/IndexMap 会改变顺序、复杂度、依赖和可能的 wire/snapshot 输出。先说明要修复的真实语义或性能问题。

## 72. 常见错误：Borrow Map Twice

持有 `get_mut` 得到的引用又调用 map 方法。缩小引用 scope、使用 Entry，或先取出所需 owned 数据。

## 73. 常见错误：Moved Key

`entry(key)` 取得 key ownership，Occupied 分支又想记录 key。可从 entry.key() 借用，Tool Registry 正这样做。

## 74. 常见错误：错误依赖迭代顺序

测试偶尔通过但跨进程失败。把期待顺序写进类型/排序逻辑，并对完整有序输出做断言。

## 75. 常见错误：无界 Pending Map

Request ID → callback 若超时、断连、取消不 remove，会形成逻辑泄漏。每个 insertion 必须对应完成、取消、超时和连接关闭清理路径。

## 76. 常见错误：Counter Underflow

Map value 中的 pending counter 在 guard drop 时递减。重复释放或缺失 acquire 会下溢/状态错误；RAII、checked/saturating 策略和测试要匹配不变量。

## 77. 测试集合逻辑

覆盖空/单项、重复 key、删除不存在、顺序、容量边界、驱逐、相同 sort key、取消清理和并发更新。优先比较完整集合。

## 78. 容器选择表

| 需求 | 常见选择 |
|---|---|
| 连续序列、按 index、尾部追加 | Vec |
| FIFO、双端操作 | VecDeque |
| 快速 key lookup、顺序无关 | HashMap |
| key 排序/range/确定性遍历 | BTreeMap |
| key lookup + 插入顺序 | IndexMap |
| 快速 membership/去重 | HashSet |
| 有序唯一集合 | BTreeSet |

## 79. 源码检查点

1. `codex-rs/core/src/tools/registry.rs`：IndexMap、Entry、重复工具策略、prepend。
2. `codex-rs/app-server/src/request_serialization.rs`：HashMap 分桶、VecDeque FIFO、锁外 drain。
3. `codex-rs/app-server/src/thread_status.rs`：HashMap Entry、or_default、watcher removal。
4. `codex-rs/core/src/guardian/mod.rs`：recent denials 固定窗口。
5. `codex-rs/core/src/agent/control/residency.rs`：VecDeque 顺序与 eviction scan。
6. `codex-rs/app-server/src/config_manager.rs`：BTreeMap/BTreeSet 确定性配置集合。
7. `codex-rs/app-server/src/request_processors/apps_processor/read.rs`：HashSet 保序去重。
8. `codex-rs/app-server/src/fuzzy_file_search.rs`：score/path tie-break 排序。

## 80. 搜索命令

```bash
rg -n 'IndexMap|Entry::Vacant|Entry::Occupied' codex-rs
rg -n 'HashMap<.*VecDeque|push_back|pop_front' codex-rs
rg -n 'BTreeMap|BTreeSet|HashSet' codex-rs/core/src codex-rs/app-server/src
rg -n 'with_capacity|\.capacity\(\)|sort_by' codex-rs
```

## 81. 理解检查

1. 为什么 FIFO 通常选 VecDeque 而非 Vec remove(0)？
2. BTreeMap、IndexMap 的稳定顺序分别是什么？
3. Entry API 除少一次 lookup 外还改善什么？
4. HashSet 去重如何保留首次出现顺序？
5. 为什么 Queue 的长度必须另设业务上限？
6. 请求序列化为什么只合并队首连续 shared reads？
7. Map 放在 Mutex 中还缺哪些可靠性设计？
8. 为何 collect/clone 后应释放锁再 await？

## 82. 答案

1. Vec 头删需搬移，VecDeque 为双端操作设计。
2. BTreeMap 按 key 排序；IndexMap 按插入/显式调整顺序。
3. 把缺失/已占用策略集中为一次原子式局部决策，并提供 value/key 引用。
4. 用 Set 记录 seen，首次 insert 返回 true 时才 push 到 Vec。
5. 容器本身可无限增长，capacity 也不是 quota。
6. 越过 Exclusive 会破坏 FIFO 公平和资源时序。
7. 容量、超时、公平、幂等、持久性、取消清理等。
8. 避免持锁跨 await，并让异步工作拥有稳定 snapshot/handle。

## 83. 常见误解速查

| 误解 | 更准确的理解 |
|---|---|
| HashMap 会按插入顺序遍历 | 默认顺序不稳定 |
| BTreeMap 保留插入顺序 | 它按 key 的 Ord 排序 |
| IndexMap 就是排序 Map | 它保存插入/移动顺序 |
| Set insert 只能写入 | 返回值还可表明是否首次出现 |
| Vec capacity 是业务上限 | 它只是当前分配容量 |
| Mutex 让队列完整可靠 | 它只解决内存互斥的一部分 |
| Entry 只是语法糖 | 它集中重复 key 策略并避免重复定位 |
| Clear 等于异步 shutdown | Drop 未必完成协议级收尾 |

## 84. 本章词汇表

| 英文/代码 | 字面翻译 | 实际含义 |
|---|---|---|
| Collection | 集合 | 拥有或组织一组 values 的数据结构 |
| Vec / slice | 向量/切片 | 连续 owned buffer 与其借用视图 |
| Length / capacity | 长度/容量 | 已有元素数与免重分配可容纳数 |
| Amortized | 摊销 | 将偶发昂贵扩容成本分摊到多次操作 |
| VecDeque / FIFO | 双端队列/先进先出 | 适合头尾操作的环形序列与队列顺序 |
| HashMap / hash collision | 哈希映射/哈希碰撞 | 平均常数 key lookup，以及不同 key 同 hash |
| BTreeMap / range | B 树映射/范围 | 按 Ord 排列并支持有序区间查询的 map |
| IndexMap | 索引映射 | 保留插入顺序并支持 key lookup 的 map |
| HashSet / BTreeSet | 哈希/有序集合 | 无独立 value 的唯一 membership 集合 |
| Entry / Vacant / Occupied | 入口/空位/占用 | 一次 key 定位后的缺失或存在操作视图 |
| `or_insert` / `or_default` | 不存在则插入/默认插入 | 取得 value 可变引用的 Entry helpers |
| Duplicate / collision | 重复/碰撞 | Eq 相同的业务 key，以及 hash 相同但 key 不同 |
| Stable order / tie-breaker | 稳定顺序/平局规则 | 可重复遍历输出，以及主排序相同时的次级键 |
| Queue / bucket | 队列/桶 | 有序待处理项，以及按 key 分组的子集合 |
| Eviction / LRU | 驱逐/最近最少使用 | 满载时删除项，以及按最近访问选择 victim |
| Membership | 成员关系 | 某 key 是否存在于 Set/Map |
| Composite key | 复合键 | 由多个身份维度构成的 tuple/struct key |
| Drain / retain | 排空/保留 | 移出一批元素，以及原地删除不满足条件项 |

## 85. 代码短语拆解

| 代码短语 | 如何理解 |
|---|---|
| `IndexMap<ToolName, RegisteredTool>` | 工具名唯一查找且注册顺序可见 |
| `Entry::Vacant(entry)` | 名称尚未注册，可在同一定位结果插入 |
| `Entry::Occupied(entry)` | 重复名称，按 trusted/external 策略处理 |
| `HashMap<Key, VecDeque<Request>>` | 按资源分队列，每队列内部 FIFO |
| `queue.front()` | 借用查看队首而不消费 |
| `queue.pop_front()` | 取得最早请求 ownership 并推进队列 |
| `entry(key).or_default()` | 首次见 key 创建默认状态，之后原地复用 |
| `seen.insert(id)` | 同时记录 id 并获知是否首次出现 |
| `sort_by(score then path)` | 相关度相同时以路径建立确定性总序 |

## 86. 与前后章节的关系

- 第 38 章讲队列、准入和过载。
- 第 47/49 章讲 key、幂等、缓存与 eviction。
- 第 63 章讲 Vec/Map 的分配和内存开销。
- 第 70 章讲 Iterator 如何物化成这些集合。

## 87. 本章结论

集合类型不是实现细节标签。Vec 声明连续顺序，VecDeque 声明双端/FIFO，HashMap 声明唯一 key lookup 但不承诺顺序，BTreeMap 声明 key order，IndexMap 声明插入顺序，Set 声明 membership 与去重。Entry API 则把首次创建、原地更新和重复冲突放在一次 key 定位中。

在异步 Codex 中还必须继续追踪：谁拥有集合、锁作用域多大、value drop 是否足够、pending entry 如何在成功/失败/取消时删除、顺序是否属于协议、容量按条目还是字节受限。选对容器只是开始；把这些不变量写进所有 mutation path 才是完整设计。
