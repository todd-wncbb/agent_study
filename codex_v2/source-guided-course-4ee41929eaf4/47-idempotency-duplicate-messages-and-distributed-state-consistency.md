# 47：幂等性、重复消息与分布式状态一致性——同一件事做两次，系统还能保持正确吗

第 27 章讲异步 task 与 channel，第 29 章讲 Rollout 和状态数据库，第 41 章讲故障注入。本章把它们组合起来，讨论分布式和异步系统中一个很反直觉的事实：发送方只发送了一次，不代表接收方只会处理一次；发送方超时，也不代表操作没有成功。

一次 app-server 请求可能在响应送达前断线；一个工具 handler 可能在完成的同时收到取消；旧 thread listener 可能在新 listener 建立后才退出；历史快照与实时订阅之间可能出现缝隙；同一个 started/completed item 也可能同时出现在不同投影中。系统必须用 identity、顺序、原子状态和可重建事实来处理这些竞态，而不能依赖“时间刚好正确”。

> 源码基线：`4ee41929eaf4`。本章以 app-server request ID、thread/turn/item/call ID、tool terminal outcome 原子争用、MCP refresh 合并、thread listener generation、resume 原子订阅、status change 抑制、config optimistic concurrency、Rollout ordinal/idempotent persist、SQLite conflict handling 和 TUI item-ID 去重为证据。官方 OpenAI 文档检索没有找到覆盖这些 Codex 内部机制的公开专题，因此具体保证仅指本提交源码。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 解释为什么网络超时会产生 ambiguous outcome；
2. 区分 at-most-once、at-least-once 和 effectively-once；
3. 理解“exactly once”通常需要限定边界；
4. 区分 correlation ID、entity ID、sequence number 和 idempotency key；
5. 解释 JSON-RPC request ID 为什么不自动防止重复执行；
6. 为读取、覆盖、追加和外部副作用选择不同重试策略；
7. 使用状态机和 compare-and-set 保证唯一终态；
8. 理解 `AtomicBool::swap` 如何决定完成/取消谁获胜；
9. 解释 generation token 如何防止旧 task 清理新资源；
10. 理解历史快照与实时订阅之间的 gap；
11. 解释 optimistic concurrency 怎样防止 lost update；
12. 区分 upsert、insert-if-absent 与无条件 overwrite；
13. 理解 Rollout ordinal 能证明顺序，但不自动等于去重键；
14. 区分 authoritative log 与 rebuildable projection；
15. 设计重复、乱序、断线、崩溃中间态测试；
16. 处理外部工具、命令和消息等不可重复副作用；
17. 用 stable ID 在 UI 投影中折叠重复事件；
18. 为一个操作写出完整的一致性契约。

---

## 2. 先说人话：付款超时，不代表钱没扣

你在购物 App 点击“支付”：

```text
手机 → 支付服务器：扣款 100 元
服务器：扣款成功
服务器 → 手机：成功响应
                 ↑
            这里网络断了
```

手机只看到超时。它不知道：

```text
A. 请求根本没到服务器
B. 请求到了，但扣款失败
C. 扣款成功，只是响应丢了
```

如果手机直接重新发送“扣款 100 元”，服务器可能扣两次。

解决办法不是假设网络可靠，而是给这次业务操作一个稳定订单号：

```text
payment_id = order-123-payment
```

服务器第二次看到相同 ID 时返回第一次结果，而不是再扣一次。

Codex 中执行工具、删除 thread、写配置、发送通知也面对同类问题，只是副作用不同。

---

## 3. 三种 Delivery 语义

### 3.1 At-most-once

最多处理一次，也可能完全没处理。

```text
丢失可以发生
重复不会发生
```

适合宁可少一次，也不能重复的场景，但调用方必须接受丢失。

### 3.2 At-least-once

至少处理一次，但可能处理多次。

```text
通过重试减少丢失
重复必须由接收方处理
```

很多消息/重试系统更容易提供这种保证。

### 3.3 Exactly-once

业务副作用恰好一次。这句话必须问：

- 在哪个系统边界内？
- 数据库提交一次，还是外部邮件也只发一次？
- 崩溃恢复后怎么算？
- 多长时间保留去重记录？

跨网络、数据库和外部服务的全局 exactly-once 通常很昂贵。工程中常实现 effectively-once：允许重复投递，但借助稳定 identity、去重表和幂等状态转换，让可观察结果像只执行一次。

---

## 4. “重复”从哪里来

### 4.1 Client retry

响应超时，客户端重发同一意图。

### 4.2 Reconnect and replay

连接断开后，客户端恢复历史并重新订阅；历史和实时事件可能重叠。

### 4.3 Producer retry

writer 不确定写入是否落盘，再次写入。

### 4.4 Concurrent paths

正常完成和取消同时尝试发布 terminal outcome。

### 4.5 Multiple projections

同一 item 的 started 和 completed 都进入 UI 缓冲，摘要只需要显示一项。

### 4.6 Crash recovery

进程在“副作用成功”和“完成标记写入”之间崩溃，恢复后再次执行。

### 4.7 Stale task cleanup

旧 task 延迟退出，误以为自己仍是 owner，清除新 task 的状态。

重复不是异常边角，而是异步边界的正常可能性。

---

## 5. 四种 ID 不要混为一谈

| ID 类型 | 回答的问题 | 例子 |
|---|---|---|
| Correlation ID | 哪个响应属于哪个请求？ | JSON-RPC request ID |
| Entity ID | 这是哪个长期对象？ | thread ID、turn ID、item ID |
| Operation/Call ID | 这是哪次业务动作？ | tool `call_id` |
| Sequence/Ordinal | 它在日志中的位置？ | Rollout ordinal |
| Idempotency key | 这是否与之前是同一业务意图？ | 客户端稳定生成的 operation key |

同一个字段有时承担多种职责，但不能仅凭名字假设。

### 5.1 Stable 的含义

重试同一业务意图时，幂等键必须保持相同；用户发起第二次独立操作时必须不同。

```text
重试第一次付款 → 同 key
用户再次购买   → 新 key
```

如果每次网络重试都生成新 UUID，服务器无法识别重复。

---

## 6. JSON-RPC Request ID 只是相关性吗

当前 app-server 协议把 request ID 保留到 response/error：

```json
{ "id": 7, "method": "thread/read", "params": {} }
{ "id": 7, "result": {} }
```

它首先保证 correlation：客户端能把并发响应配回等待中的请求。

### 6.1 它不自动提供什么

除非服务端明确缓存并重放同 ID 的结果，否则：

```text
发送 id=7
连接断开
新连接再次发送 id=7
```

不一定会被识别为同一次操作。Request ID 的作用域还可能只在单连接内。

所以评审时要查实现契约，而不能说“有 request ID，所以请求幂等”。

### 6.2 ConnectionRequestId

App-server 内部把 connection identity 与 JSON-RPC request ID 组合，避免不同连接都使用 `id=7` 时互相混淆。这加强相关性隔离，但仍不等同于跨连接业务去重。

---

## 7. 先按副作用给操作分类

### 7.1 Pure read

例如读取模型列表。重复通常只增加成本，但结果可能因时间变化。

### 7.2 Set-to-value

```text
把 thread 名设置为 "demo"
```

重复设置同一值通常天然幂等。

### 7.3 Increment / append

```text
计数 +1
向 history 追加一行
```

重复会改变结果，通常不幂等。

### 7.4 Create

如果客户端提供稳定 entity ID，可做到重复 create 返回既有对象；如果 ID 由服务端每次生成，重试可能创建两个对象。

### 7.5 Destructive action

删除已不存在对象可以定义为成功，也可以返回 not found。两者都可能合理，但 API 必须明确。

### 7.6 External side effect

发邮件、发 Slack、运行命令、调用 MCP write tool。重复可能无法由本地事务撤销，风险最高。

---

## 8. 幂等函数的直观定义

数学式：

```text
f(f(x)) = f(x)
```

例如：

```text
set enabled = true
```

做两次的最终状态与做一次相同。

但工程中还要考虑附带行为：

```text
状态相同
通知是否发两次？
审计记录是否加两条？
计费是否发生两次？
updated_at 是否变化？
```

所以要明确观察边界。数据库行相同，不代表对用户“完全幂等”。

---

## 9. 状态机比“先检查再执行”可靠

容易竞态的写法：

```text
if status != completed:
    perform_side_effect()
    status = completed
```

两个 task 可以同时读到未完成，然后都执行副作用。

更可靠的方向：

```text
原子地把 pending → executing
只有成功转换者获得执行权
其他调用读取现有状态/结果
```

合法状态转换应显式：

```text
Pending → Running → Completed
                  ↘ Failed
Pending/Running → Cancelled
```

terminal state 一旦确定，后续竞争者只能观察，不能再次提交另一个终态。

---

## 10. 当前实例：Tool Terminal Outcome 只能有一个 Owner

工具执行可能正常结束，也可能在同一时刻收到 cancellation。

当前 tools parallel/registry 路径共享：

```rust
Arc<AtomicBool>
```

正常 finish 和 abort 都尝试：

```rust
reached.swap(true, Ordering::AcqRel)
```

### 10.1 `swap` 返回什么

它原子地：

1. 读取旧值；
2. 写入 `true`；
3. 返回旧值。

如果返回 `false`，当前路径是第一个抢到 terminal outcome 的 owner；返回 `true`，说明另一条路径已经完成终态处理。

### 10.2 为什么普通 bool 不够

```text
Task A: 读 false
Task B: 读 false
Task A: 写 true，发送 completed
Task B: 写 true，发送 aborted
```

Atomic read-modify-write 把“检查并占有”合成一个不可分割动作。

### 10.3 它保证的边界

它避免同一次本地 dispatch 同时发布正常完成与取消终态。但它不是跨进程、跨重启的幂等记录；进程崩溃后这个 AtomicBool 不存在。

---

## 11. Memory Ordering 先理解到哪一步

当前使用 `Acquire` / `AcqRel`。初学阶段先记住：

- atomicity 保证争用操作不会被撕裂；
- ordering 约束相关内存读写的可见顺序；
- `swap` 决定唯一 winner；
- 不能因为用了 AtomicBool 就认为所有周边状态自动线程安全。

如果 terminal payload 还存放在另一块内存中，必须确保 winner 发布和 loser 读取之间有正确同步；不能只盯一个 flag。

---

## 12. 当前实例：MCP Refresh 合并重复 Invalidation

`McpRefresh` 包含：

```text
pending: AtomicBool
gate: Semaphore(1)
```

多个来源可以调用：

```rust
invalidate() → pending = true
```

refresh worker 通过：

```rust
claim() → pending.swap(false)
```

合并一批重复失效信号。

### 12.1 Coalescing

如果在 refresh 开始前来了十次 invalidation，不一定需要执行十次完整刷新；只要最终发布包含最新状态，一次即可覆盖。

这叫 coalescing：把多个“重新计算最新值”的请求合并。

### 12.2 Semaphore 的职责

它限制同一时刻只有一个 publisher 进入刷新临界区，避免多个刷新交错发布。

### 12.3 Cancellation Guard

claim 后如果任务在 publish 前取消，RAII guard 的 `Drop` 会重新 `invalidate()`。否则 pending 已被清掉，更新会永久丢失。

```text
claim pending
  → 任务取消
  → guard drop
  → pending restored
```

这是“处理权已领取，但尚未提交”必须恢复的实例。

---

## 13. Coalescing 何时安全

适合：

```text
重新读取最新 MCP tool catalog
重新计算当前状态快照
刷新缓存
```

因为中间每次触发的独立内容不重要，只关心最终最新状态。

不适合：

```text
用户发送的十条消息
十次付款
十次 append-only 审计事件
```

这些事件每个都有独立业务语义，不能压成一个 `pending=true`。

判断问题：

> 这是“发生了 N 次”的 event，还是“当前状态可能过期”的 invalidation？

---

## 14. Generation Token 解决旧 Task 清理新状态

Thread listener 被替换时：

```text
listener A 运行
建立 listener B，取消 A
A 可能稍后才真正退出
```

如果 A 退出时无条件 `clear_listener()`，会把 B 的 sender、watch registration 和状态一起清掉。

当前 `ThreadState` 每次安装 listener 都增加：

```rust
listener_generation: u64
```

task 退出时只有在：

```text
current_generation == my_generation
```

才执行清理。

### 14.1 这是什么模式

也叫 epoch/generation fencing：旧 owner 即使还活着，也因 generation 落后而失去修改当前资源的资格。

### 14.2 为什么 cancellation 不够

取消通常是协作式的：task 可能在 await、清理或 IO 后才观察到。Generation 让“迟到的旧任务”无法伤害新状态。

---

## 15. Fencing Token 与普通版本号

共同点：都是单调变化的世代标识。

不同用途：

- optimistic version：拒绝基于旧快照提交修改；
- fencing token：拒绝旧 owner 对资源继续操作；
- ordinal：表示日志顺序位置；
- schema version：表示数据结构版本。

不要只看到 `u64` 就认为它们可互换。每种数字的作用域和比较规则不同。

---

## 16. 历史 Snapshot 与实时订阅之间的 Gap

重连/恢复常见错误流程：

```text
1. 读取历史到位置 100
2. 此时事件 101 发生
3. 建立实时订阅
```

如果订阅只收到 102 以后，事件 101 永久丢失。

反过来，先订阅再读取历史，也可能让事件 101 同时出现在历史和 live stream，产生重复。

### 16.1 两种可接受策略

```text
无 gap，允许 overlap → 客户端按 ID/ordinal 去重
```

或者：

```text
在同一个序列化 owner 中原子确定 snapshot boundary 并注册订阅
```

当前 thread listener command 的注释明确：running-thread resume 会发送历史并原子订阅新更新，且与 listener 事件顺序串行化。

---

## 17. Serial Owner 比分布式锁更容易推理

`ThreadListenerCommand` 在 thread listener 的上下文中执行：

- 发送 resume response；
- 订阅 live updates；
- emit goal update/clear；
- resolve server request。

这些动作通过同一 listener event loop 排序，减少“两个 task 各拿一半状态”的竞态。

这是一种 actor/serial-owner 思路：

```text
多个调用者 → command channel → 单一 owner 顺序处理
```

它不意味着系统没有并发，而是把需要一致顺序的状态变化收敛到一个 owner。

---

## 18. 当前实例：Status 只在有效变化时通知

`ThreadWatchState` 在 mutation 前记录 `previous_status`，更新 runtime facts 后计算新 status：

```text
previous == current → 不发通知
previous != current → thread/status/changed
```

watch sender 也使用 `send_if_modified`，相同值不重复广播。

### 18.1 这是幂等吗

重复调用 `upsert_thread` 可能仍更新内部 facts，但如果最终 status 不变，观察者不会收到重复状态变化。

这是 projection-level deduplication：对外投影按“有效状态是否变化”折叠重复输入。

### 18.2 Event 与 State Notification 的区别

```text
State: 当前是 Running
Event: 又执行了一次命令
```

状态相同可以合并，独立事件不能随便丢。API 名称和文档必须说明发送的是 state snapshot 还是 occurrence event。

---

## 19. 当前实例：TUI 按 Item ID 折叠 Started/Completed

Agent status preview 从最近事件倒序扫描，可能同时看到：

```text
ItemStarted(id=A)
ItemCompleted(id=A)
```

它用 `HashSet` 保存 `seen_item_ids`，同一 item 只加入摘要一次。

### 19.1 为什么从后向前

completed 通常比 started 更新，倒序优先选择最新状态；随后再 reverse 恢复展示顺序。

### 19.2 去重键必须代表同一实体

如果用显示文本去重，两次不同的 `cargo test` 会被误合并；使用 stable item ID 才能区分：

```text
同一 item 的多阶段事件
不同 item 恰好文本相同
```

---

## 20. Optimistic Concurrency 防止 Lost Update

两个客户端同时编辑 config：

```text
A 读取 version=10
B 读取 version=10
A 写 model=X → version=11
B 基于旧快照写 sandbox=Y
```

若 B 无条件覆盖整份文件，可能把 A 的 model 修改丢掉。

当前 config write 支持 `expected_version`：

```text
expected == current → 允许应用 edits
expected != current → ConfigVersionConflict
```

错误提示调用方重新读取最新版本再重试。

### 20.1 Compare-and-set

概念上：

```text
if version == expected:
    apply(change)
    version++
else:
    reject
```

这不自动合并冲突。它首先防止 silent overwrite，把冲突显式交给调用方重新决策。

---

## 21. 重试 Config Edit 时不能盲目复用结果

版本冲突后正确流程：

1. 重新读取 current config/version；
2. 判断用户原意在新状态下是否仍有效；
3. 重新计算 edit；
4. 用新 expected version 提交。

如果只是把 expected version 改成最新、却复用整份旧配置，仍可能覆盖别人修改。

对独立 key 的 merge 可以自动重算；对同一 key 冲突应让上层明确选择。

---

## 22. Upsert、Insert-if-absent 与 Overwrite

### 22.1 Insert-if-absent

当前 SQLite backfill state 初始化使用：

```sql
INSERT ... ON CONFLICT(id) DO NOTHING
```

多个初始化者竞争固定 `id=1` 时，只有第一个创建；后续保持已有状态。

### 22.2 Upsert

存在则更新，不存在则插入。它适合 rebuildable projection，但必须定义哪些字段覆盖、怎样处理旧版本。

### 22.3 Unconditional overwrite

最后写入者获胜，容易导致 lost update，只适合明确的 single writer 或整份值就是最新权威快照。

### 22.4 数据库约束比应用层先查更可靠

```text
SELECT 不存在
INSERT
```

两个进程可能同时通过 SELECT。Unique key + conflict clause 把竞争交给数据库原子处理。

---

## 23. Rollout `persist()` 的幂等边界

`RolloutRecorder::persist()` 的文档明确称其 idempotent：物化文件并持久化缓冲项，重复调用不会重复写同一批 pending items。

测试连续调用两次 `persist()`，最终 ordinal 仍是预期的三条，而不是重复追加。

### 23.1 不要扩大解释

这不表示 recorder 的所有操作都幂等：

```text
record_canonical_items([item])
record_canonical_items([item])
```

调用两次可能本来就代表追加两条。只有 `persist` 对同一 pending buffer 的物化承诺是幂等的。

一句“组件是幂等的”太宽；应精确到 method、输入 identity 和观察结果。

---

## 24. Rollout Ordinal 解决什么

Paginated Rollout 为记录分配递增 ordinal：

```text
0, 1, 2, 3, ...
```

恢复 append 时读取最后一个有效记录，从下一 ordinal 继续；使用 `checked_add` 防止溢出。

### 24.1 它能帮助发现

- 顺序；
- gap；
- 子 agent inherited prefix 是否完整；
- 分页边界；
- 新 append 应从哪里开始。

### 24.2 它不自动保证

- payload 不重复；
- 同一业务 item 只写一次；
- 两个 writer 不会同时争用相同 ordinal；
- 外部副作用只发生一次。

Ordinal 是 ordering identity，不一定是 business idempotency key。

---

## 25. Authoritative Log 与 Projection

可以把状态分成：

```text
Rollout / durable event history  → 事实账本
SQLite thread metadata/index     → 查询投影
TUI state                         → 显示投影
```

Projection 允许通过 authoritative source 重建。当前 state DB 路径包含 backfill、upsert 和 read repair，正是处理索引缺失或漂移。

### 25.1 为什么这有助于一致性

如果 SQLite projection 写失败，但 Rollout 已正确持久化，可以稍后重新扫描修复；不需要假装两个存储永远原子提交。

### 25.2 必须明确 source of truth

如果 Rollout 和 DB 冲突时没有优先级，read repair 可能把错误方向同步。每个字段都应知道权威来源。

---

## 26. Dual Write Problem

一次操作同时写：

```text
A. Rollout file
B. SQLite index
```

可能发生：

```text
A 成功，B 失败
A 失败，B 成功
进程在两者之间崩溃
```

除非两者位于同一事务系统，不能简单宣称原子。

常见策略：

- 一个是权威账本，另一个可重建；
- transactional outbox；
- write-ahead log；
- reconciliation/read repair；
- 幂等 upsert。

Codex 当前 Rollout/State DB 的学习重点是“耐久历史与查询投影职责不同”，而不是假设跨文件和 SQLite 的全局事务。

---

## 27. Transactional Outbox 的通用模型

当数据库更新后还要发通知：

错误窗口：

```text
提交 DB
进程崩溃
通知未发
```

Outbox 思路是在同一 DB transaction 中写：

```text
业务状态
待发送事件 outbox row
```

独立 worker 重复读取 outbox 并发送；消费者按 event ID 去重，成功后标记。

它把“数据库提交与消息发送的原子性”转化为“可重试消息 + 幂等消费”。本节是通用解释，不声称当前所有 Codex 通知都使用 outbox。

---

## 28. 外部副作用最难幂等

工具调用可能：

- 写文件；
- 启动进程；
- 发送 Slack/邮件；
- 修改日历；
- 调用第三方 MCP write tool；
- 创建远程资源。

本地记录 `completed=true` 不能撤销远端已成功的动作。

### 28.1 优先策略

1. 使用远端支持的 idempotency key；
2. 使用调用方提供的 stable resource ID；
3. 先查询远端是否已有目标状态；
4. 将动作设计成 set-to-value 而非 increment；
5. ambiguous outcome 时不要自动盲重试；
6. 向用户报告“可能已成功”，提供核对方法。

### 28.2 Approval 不是去重

审批解决“是否授权做”，不解决“同一授权会不会执行两次”。即使用户批准过，也必须处理重复 dispatch。

---

## 29. Tool `call_id` 应怎样使用

模型工具调用带 `call_id`，tool output 通过同一 ID 回到模型上下文。

它可以帮助：

- started/completed 配对；
- telemetry 关联；
- result 回传；
- 重放时识别同一调用。

但是否能作为跨重启幂等键，要看：

- 重试是否保持同一 call ID；
- 服务端是否持久保存已执行结果；
- key 作用域是否包含 thread/turn；
- 第三方工具是否接受该 key；
- 去重记录保留多久。

不能仅因存在 `call_id` 就自动重放 write tool。

---

## 30. Delete 的幂等语义要明确

两种 API 都可能合理：

### State-oriented delete

目标是“资源最终不存在”。资源已经不存在时仍返回成功，天然适合重试。

### Event-oriented delete

要求“这次必须删除一个当前存在的资源”。不存在返回 not found，帮助发现错误 ID。

当前 app-server README 对 thread deletion 的部分底层情况说明：缺失 rollout file 可视为已经删除；但 root thread 是否存在、state DB 和 descendants 仍有更严格验证。不要把一处幂等处理扩大成“整个 thread/delete 对任意重试总成功”。

---

## 31. Retry Policy 必须按错误分类

### 可以自动重试

- 明确 transient transport error；
- pure read；
- 有可靠 idempotency key 的 mutation；
- CAS conflict 后能安全重算；
- refresh/invalidation 型操作。

### 不应盲重试

- ambiguous external write；
- 无幂等键的 create/increment；
- 权限/validation error；
- deterministic parse failure；
- 用户已取消；
- 状态已进入冲突终态。

### Backoff 仍然重要

即使操作幂等，立即无限重试也会形成重试风暴。应有次数上限、指数退避、jitter、deadline 和 cancellation。

---

## 32. Dedupe Cache 的容量与时间问题

服务端可以保存：

```text
idempotency_key → status/result
```

但必须决定：

- 保存多久；
- 按 tenant/thread 还是全局作用域；
- key 冲突但 payload 不同怎样处理；
- in-progress 重试等待还是返回状态；
- result 太大怎样存；
- 服务重启后是否仍有效；
- 容量如何硬限制。

### 32.1 Key + payload hash

同一个 key 携带不同 payload 应报冲突，而不是悄悄返回第一次结果：

```text
key=abc, amount=100
key=abc, amount=200 → invalid reuse
```

否则调用方 bug 会被隐藏。

---

## 33. 去重集合也必须有界

TUI 短期构建 preview 时使用局部 `HashSet`，生命周期只覆盖一次计算，天然有界于当前 buffer。

服务端若为所有历史 request ID 永久保存 `HashSet`，内存会无限增长。

可用边界：

- per-turn/per-thread 生命周期；
- TTL；
- LRU；
- durable table + retention；
- ordinal watermark；
- 完成后压缩成摘要。

任何注入模型或驻留服务的集合都要有 hard cap。

---

## 34. Out-of-order 与 Duplicate 是两个问题

事件可能：

```text
Completed(A) 先于 Started(A) 到达
Completed(A) 到达两次
Started(B) 插在 A 的事件之间
```

只用 `seen IDs` 解决重复，不能解决乱序。

常见处理：

- sequence/ordinal；
- per-entity state machine；
- terminal state 优先级；
- bounded reorder buffer；
- snapshot reconciliation；
- 丢弃比当前 version 旧的 update。

必须定义同一 item 的合法转换，而不是按到达顺序无条件覆盖。

---

## 35. Last-write-wins 的限制

用 timestamp/version 选择“最新”很方便，但要问：

- 时钟是否同源？
- 两个更新是否可比较？
- 后到的值是否一定更正确？
- 删除 tombstone 会不会被旧 update 复活？
- 并发编辑是否应合并？

对 UI presence 等短暂状态，last-write-wins 可能足够；对审批、资金、安全 policy 和持久历史，它可能丢失重要冲突。

---

## 36. Consistency 模型要说清观察范围

### Strong consistency

操作成功返回后，后续读取都看到该结果（还需限定节点/边界）。

### Eventual consistency

没有新更新时，各 projection 最终收敛，但短时间可以不同。

### Read-your-writes

同一客户端提交后能看到自己的修改。

### Monotonic reads

同一客户端不会先看到新状态，再看到更旧状态。

### Causal ordering

有因果关系的更新按因果顺序观察。

不要只写“系统最终一致”就结束。应说明哪些实体、哪些 reader、最大延迟和冲突处理。

---

## 37. 通知不是事实数据库

Client 可能漏掉 notification、断线或进程重启。可靠客户端通常需要：

```text
notification → 快速增量更新
read/resume  → 获取权威 snapshot 并校正
```

第 28 章所说“实时但可最终校正的 UI 投影”正适用于此。

如果客户端只能靠每条 notification 从空状态演算，漏一条就可能永久错误。应提供可重新读取的 snapshot/cursor/resume 边界。

---

## 38. Reconciliation

Reconciliation 比较期望/权威状态与当前 projection：

```text
desired/authoritative
        ↕ compare
observed/projection
        ↓
create/update/delete/repair
```

它应：

- 可重复运行；
- 对相同输入无额外副作用；
- 有进度 watermark；
- 对失败项可重试；
- 不因单项损坏阻断全部；
- 记录 repair telemetry。

State DB backfill/read repair 是这种思路的具体入口。

---

## 39. Crash Consistency 的提交点

为一次操作标出时间线：

```text
1. 接收请求
2. 验证
3. 写临时状态
4. 执行外部副作用
5. 写 durable completed
6. 发送响应
```

对每两步之间崩溃都问：

- 恢复后看到什么？
- 会不会重做副作用？
- 能否判断已成功？
- 临时文件/锁怎样清理？
- response 丢失时客户端怎样查询？

真正的 commit point 是系统从“可以安全重做”跨到“必须返回已有结果”的位置。

---

## 40. 测试重复消息的方法

### 40.1 同一请求连续两次

断言最终状态、通知数量和返回结果。

### 40.2 并发重复

用 barrier 同时触发两个相同操作，不能只顺序调用。

### 40.3 成功后响应丢失

让 handler 完成副作用，但 client 看不到 response，然后重试。

### 40.4 处理中重试

第二个请求到达时第一个仍 in-progress，断言 wait/status/conflict 策略。

### 40.5 重启后重试

证明去重状态是否 durable，而不是只存在内存。

### 40.6 相同 key、不同 payload

必须得到明确冲突。

测试应统计实际副作用次数，而不只比较最终返回值。

---

## 41. 测试乱序与 Gap

### 41.1 Notification reorder

输入 `completed` 后 `started`，断言 terminal state 不回退。

### 41.2 Snapshot/live overlap

同一 item 同时出现在 resume history 和 live event，断言 UI 只显示一次。

### 41.3 Snapshot/live gap

在读取历史与订阅之间注入事件，断言不会丢失。

### 41.4 Old listener exits late

安装 B 后让 A 完成退出，断言 generation 检查不会清除 B。

### 41.5 Ordinal gap/overflow

断言恢复能识别不完整 prefix，溢出时不追加。

这些测试需要可控 barrier/channel，而不是依赖随机 sleep 撞竞态。

---

## 42. 一份一致性契约模板

### Operation

业务动作是什么？例如“设置 config key”，不是“处理 HTTP POST”。

### Identity

entity ID、operation ID、request ID 和作用域分别是什么？

### Delivery

可能丢失、重复、乱序吗？重试由谁发起？

### State machine

合法状态与 terminal state 是什么？

### Commit point

何时副作用不可安全重做？

### Duplicate behavior

返回已有结果、等待 in-progress、no-op、conflict 还是拒绝？

### Ordering

用 ordinal、version、serial owner 还是 generation？

### Durability

去重状态是否跨重启？保留多久？

### Reconciliation

丢通知或 projection 漂移后怎样恢复？

### Tests

顺序重复、并发重复、崩溃、重连、乱序和 payload 冲突怎样覆盖？

---

## 43. 常见错误做法

### 43.1 “有 request ID，所以幂等”

Request ID 可能只做 response correlation，服务端未缓存业务结果。

### 43.2 “最终数据库行一样，所以完全幂等”

通知、计费、审计或外部副作用可能已重复。

### 43.3 “先 SELECT 再 INSERT 就不会重复”

并发请求都可能通过检查；需要 unique constraint/atomic transition。

### 43.4 “取消后 abort 就行”

handler 可能同时完成；需要唯一 terminal owner 和 teardown 语义。

### 43.5 “相同文本就是重复 item”

不同调用可以有相同文本；应使用 stable entity ID。

### 43.6 “Ordinal 可以自动去重”

Ordinal 主要表达顺序，不等于业务 identity。

### 43.7 “重试所有错误更可靠”

永久错误和无幂等键 write 会放大故障或副作用。

### 43.8 “通知不丢，所以客户端不用 snapshot”

断线、重启和 buffer 边界都会让纯 notification projection 脆弱。

---

## 44. 源码阅读路线

1. `codex-rs/app-server-protocol/src/protocol/common.rs`
   - 看 request ID 怎样进入 request/response。
2. `codex-rs/app-server/src/message_processor.rs`
   - 看 connection ID 与 request ID 组合及请求路由。
3. `codex-rs/core/src/tools/parallel.rs`
   - 看正常完成和 cancellation 怎样争夺 terminal outcome。
4. `codex-rs/core/src/tools/registry.rs`
   - 看 `notify_tool_finish_if_unclaimed` 的原子 `swap`。
5. `codex-rs/core/src/session/mcp_refresh.rs`
   - 看 invalidation coalescing、Semaphore 与取消恢复 guard。
6. `codex-rs/app-server/src/thread_state.rs`
   - 看 listener generation、serial command 和 turn summary。
7. `codex-rs/app-server/src/request_processors/thread_lifecycle.rs`
   - 看 listener 替换、原子 resume subscription 与 generation cleanup。
8. `codex-rs/app-server/src/thread_status.rs`
   - 看 effective status change 才通知。
9. `codex-rs/app-server/src/config_manager_service.rs`
   - 看 `expected_version` optimistic concurrency。
10. `codex-rs/rollout/src/recorder.rs`
    - 看 idempotent `persist()` 的精确承诺。
11. `codex-rs/rollout/src/ordinal.rs`
    - 看恢复 ordinal、incomplete prefix 与 overflow。
12. `codex-rs/state/src/runtime.rs`
    - 看 `ON CONFLICT(id) DO NOTHING`。
13. `codex-rs/tui/src/app/agent_status_feed.rs`
    - 看 started/completed 按 item ID 折叠。
14. `codex-rs/rollout/src/state_db.rs`
    - 看 backfill、reconcile 和 read repair。

---

## 45. 动手练习

### 练习 1：Request ID 不是幂等键

画出两个 WebSocket connection 都发送 `id=7` 的情况，说明为什么内部还需要 connection identity，以及为何仍不能自动阻止重复 thread 创建。

### 练习 2：终态竞态

画出 handler 正常完成与 cancellation 同时发生的 interleaving。分别说明普通 bool 与 atomic swap 的结果。

### 练习 3：Refresh 合并

给出三次 invalidation 在 claim 前发生、claim 后又发生一次的时间线。说明最终至少需要几次 refresh。

### 练习 4：Listener Generation

模拟 A、B 两个 listener 的建立和退出顺序，写出没有 generation check 时 B 如何被 A 清理。

### 练习 5：Config Lost Update

让两个 client 从 version 10 修改不同 key。比较 unconditional overwrite、CAS reject 和自动 merge 三种方案。

### 练习 6：Rollout Identity

解释 ordinal=42、item ID 和 tool call ID 分别识别什么，为什么不能互相替代。

### 练习 7：外部写工具

为“创建日历事件”设计 idempotency key、payload conflict、in-progress retry、结果保存和过期策略。

---

## 46. 理解检查

1. 为什么 response timeout 会产生 ambiguous outcome？
2. at-most-once 和 at-least-once 各允许什么失败？
3. effectively-once 通常怎样实现？
4. correlation ID 与 idempotency key 有什么区别？
5. JSON-RPC request ID 为什么不自动阻止跨连接重复执行？
6. 哪些操作天然接近幂等，哪些不是？
7. `AtomicBool::swap` 如何选出唯一 terminal owner？
8. 它为什么不提供跨进程幂等？
9. MCP invalidation 为什么可以 coalesce？
10. claim 后取消为什么要恢复 pending？
11. generation token 防止了哪种 stale task 问题？
12. history snapshot 与 live subscription 之间有哪些 gap/overlap 风险？
13. state notification 与 occurrence event 为什么不能同样去重？
14. optimistic concurrency 怎样防止 lost update？
15. `ON CONFLICT DO NOTHING` 比先 SELECT 有什么并发优势？
16. `persist()` 幂等为什么不等于任意 AddItems 都幂等？
17. Rollout ordinal 能证明什么，不能证明什么？
18. authoritative log 与 projection 为什么要区分？
19. 外部副作用 ambiguous 时为什么不能盲重试？
20. 重复消息测试为什么要统计副作用次数？

---

## 47. 本章词汇表

| 英文 | 字面翻译 | 在本章中的意思 |
|---|---|---|
| Idempotency | 幂等性 | 相同业务操作重复处理后，可观察结果与一次处理等价 |
| Duplicate delivery | 重复投递 | 同一消息/意图到达接收方多次 |
| Ambiguous outcome | 结果不确定 | 调用方不知道副作用是否已成功，只知道响应未收到 |
| At-most-once | 至多一次 | 不重复，但可能丢失 |
| At-least-once | 至少一次 | 通过重试避免丢失，但可能重复 |
| Exactly-once | 恰好一次 | 在明确边界内业务副作用只提交一次 |
| Effectively-once | 效果上一次 | 允许重复投递，但最终效果像执行一次 |
| Correlation ID | 关联 ID | 将 response/error 配回 request 的标识 |
| Entity ID | 实体 ID | 标识 thread、turn、item 等长期对象 |
| Operation ID | 操作 ID | 标识一次业务动作或 tool call |
| Idempotency key | 幂等键 | 重试同一意图时保持不变、用于复用既有结果的键 |
| Ordinal | 序号 | 表示持久日志顺序位置的单调编号 |
| Sequence number | 序列号 | 用于排序、发现 gap 或拒绝旧 update 的数字 |
| State machine | 状态机 | 合法状态及其转换关系 |
| Terminal state | 终态 | Completed/Failed/Cancelled 等不再继续转换的状态 |
| Compare-and-set | 比较并设置 | 仅当前值等于预期时原子更新 |
| Atomic operation | 原子操作 | 并发观察下不可被拆开的读改写动作 |
| Memory ordering | 内存顺序 | 原子操作周边读写的跨线程可见性约束 |
| Winner / owner | 获胜者/所有者 | 竞争中唯一获得提交终态资格的路径 |
| Coalescing | 合并 | 将多个“状态可能过期”信号折叠成一次刷新 |
| Invalidation | 失效通知 | 表示缓存/目录需重新计算，而非独立业务事件 |
| Semaphore | 信号量 | 限制并发进入临界执行区域的同步原语 |
| Generation / Epoch | 世代/纪元 | 区分新旧 task owner、防止迟到清理的单调标识 |
| Fencing token | 栅栏令牌 | 让旧 owner 即使仍运行也无权修改当前资源的 token |
| Snapshot | 快照 | 某个边界时刻的完整状态 |
| Live subscription | 实时订阅 | 接收 snapshot 边界之后增量更新的通道 |
| Gap | 缺口 | 历史读取与订阅之间永久漏掉的更新 |
| Overlap | 重叠 | 同一更新同时出现在 snapshot 与 live stream |
| Serial owner | 串行所有者 | 通过单一 event loop/channel 顺序修改一组状态 |
| Optimistic concurrency | 乐观并发 | 假设冲突少，提交时用 version 检测冲突 |
| Lost update | 丢失更新 | 后写者基于旧快照覆盖先写者的修改 |
| Upsert | 插入或更新 | 不存在则插入、存在则按规则更新 |
| Insert-if-absent | 不存在才插入 | 冲突时保留已有值 |
| Unique constraint | 唯一约束 | 由数据库原子禁止重复 identity |
| Authoritative source | 权威来源 | 冲突时被视为事实的持久数据源 |
| Projection | 投影 | 从权威事实派生、可重建的查询或 UI 状态 |
| Reconciliation | 对账/协调 | 比较权威状态与投影并修复差异 |
| Read repair | 读取修复 | 读取时发现 projection 漂移并顺便修正 |
| Dual write | 双写 | 一次操作需要写两个不能共同事务提交的系统 |
| Transactional outbox | 事务发件箱 | 同事务记录业务状态和待发送消息，再异步投递 |
| Retry | 重试 | 失败或不确定后再次尝试操作 |
| Backoff / Jitter | 退避/抖动 | 控制重试间隔并避免同步重试风暴 |
| Dedupe cache | 去重缓存 | 保存 operation key 与既有状态/结果的有界存储 |
| Payload hash | 载荷哈希 | 检测相同 key 是否被错误复用于不同输入 |
| Last-write-wins | 后写胜出 | 用版本/时间选择最后更新的冲突策略 |
| Eventual consistency | 最终一致 | 无新变化时多个投影最终收敛 |
| Read-your-writes | 读己所写 | 客户端能在后续读取中看到自己的提交 |
| Commit point | 提交点 | 操作从可安全重做跨到必须识别既有结果的边界 |
| Crash consistency | 崩溃一致性 | 任一步崩溃恢复后数据仍处于可解释、可修复状态 |

---

## 48. 本章小结

异步系统不能靠“通常只来一次”维持正确。更可靠的设计要把每种 identity 和一致性责任说清楚：

```text
request ID     → 响应关联
thread/turn/item ID → 实体身份
call ID        → 业务调用配对
ordinal/version → 顺序和冲突
idempotency key → 重试同一意图
```

当前 Codex 源码展示了多种不同层次的保护：

- tool finish 与 cancellation 用原子 `swap` 争夺唯一终态；
- MCP refresh 将重复 invalidation 合并，并在取消时恢复未发布更新；
- listener generation 阻止旧 task 清除新 listener；
- running-thread resume 把历史响应和实时订阅放进同一顺序 owner；
- status projection 只在有效状态变化时通知；
- TUI 按 item ID 折叠同一实体的 started/completed；
- config `expected_version` 防止基于旧快照覆盖新修改；
- SQLite constraint 原子处理初始化竞争；
- Rollout `persist()` 对物化动作幂等，ordinal 则负责耐久顺序；
- State DB 作为可重建 projection，可通过 backfill/read repair 与权威历史重新对账。

这些机制不能互相替代。AtomicBool 只覆盖当前进程竞态，request ID 只做相关性，ordinal 只表达顺序，upsert 也不自动保护外部副作用。

设计一个可重试操作时，应依次回答：业务 identity 是什么、哪个状态转换是原子的、commit point 在哪里、重复到达返回什么、乱序怎样处理、去重记录是否跨重启、projection 如何校正，以及外部副作用是否支持同一幂等键。

当这些答案被写成状态机、数据约束和故障测试后，“偶尔重复一次”才不会演变成重复命令、丢失更新、幽灵通知或无法恢复的历史。
