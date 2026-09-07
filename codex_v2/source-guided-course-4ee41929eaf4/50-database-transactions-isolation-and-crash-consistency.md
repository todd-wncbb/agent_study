# 50：数据库事务、隔离级别与崩溃一致性——不是“写进数据库”就自动安全了

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方文档没有公开 Codex 本地 SQLite 的事务和崩溃恢复细节。本章的 Codex 行为依据当前仓库源码与测试；ACID、WAL 和隔离异常部分是用于理解源码的通用数据库知识。

## 1. 本章解决什么问题

程序把数据放进 SQLite，并不自动回答下面的问题：

- 三条相关记录只成功写入两条怎么办？
- 两个进程同时更新同一行，谁覆盖谁？
- 先读取再更新期间，别人是否已经改变数据？
- 进程在 `commit` 前后崩溃，重启会看到什么？
- 数据库文件完好，但业务状态只完成一半，算不算一致？
- migration 执行到中间失败，旧程序还能否启动？
- SQLite 报 locked、busy 和 corrupt 时，恢复策略是否相同？

本章的核心认识是：

> 事务保护的是明确边界内的一组数据库变化；崩溃一致性还需要权威日志、检查点、约束、幂等重放和恢复流程共同完成。

## 2. 先用“银行转账”理解事务

从 A 向 B 转 100 元，需要：

```text
A 余额减 100
B 余额加 100
```

如果只完成第一步程序就崩溃，钱凭空消失；只完成第二步，钱凭空增加。

事务希望提供：

```text
BEGIN
  UPDATE account A ...
  UPDATE account B ...
COMMIT
```

外部最终只能看到：

- 两步都没发生；或
- 两步都发生。

但如果转账后还要发邮件、调用远端 API，数据库事务并不能自动撤回这些外部副作用。

## 3. `BEGIN`、`COMMIT`、`ROLLBACK` 是什么

| SQL 词 | 中文理解 | 作用 |
|---|---|---|
| `BEGIN` | 开始事务 | 建立一组原子数据库操作的边界 |
| `COMMIT` | 提交 | 让事务中的变化成为持久可见结果 |
| `ROLLBACK` | 回滚 | 放弃事务中尚未提交的变化 |

Rust/SQLx 中常见写法：

```rust
let mut tx = pool.begin().await?;
query_a.execute(&mut *tx).await?;
query_b.execute(&mut *tx).await?;
tx.commit().await?;
```

关键不是有没有创建 `tx`，而是所有必须共同成功的语句是否真的都使用了同一个 `tx`。

## 4. ACID 不要只背缩写

`ACID` 是四类保证：

| 字母 | 英文 | 实用理解 |
|---|---|---|
| A | Atomicity | 一组变化全成或全不成 |
| C | Consistency | 提交前后都满足已声明的不变量 |
| I | Isolation | 并发事务的中间步骤不会任意互相干扰 |
| D | Durability | 已提交结果在约定故障模型下不会轻易消失 |

这里的 `Consistency` 不是“数据库替你理解全部业务规则”。数据库只能强制它知道的 PRIMARY KEY、UNIQUE、CHECK、FOREIGN KEY 等约束；其他不变量仍需应用逻辑和测试表达。

## 5. 原子性保护的是事务边界

假设函数里做三件事：

1. 更新 SQLite；
2. 追加 JSONL；
3. 调用远端服务。

即使第 1 步内部用了事务，也没有自动把三种系统变成一个原子事务。

跨边界一致性通常要依靠：

- authoritative log + projection；
- transactional outbox；
- 幂等操作 ID；
- 状态机和补偿；
- 重试与 reconciliation。

先画清“一个事务到底覆盖哪些资源”，再谈原子性。

## 6. 当前实例：Codex 使用六个 SQLite 数据库

`codex-rs/state/src/sqlite.rs` 定义了六个 runtime DB：

- `state_5.sqlite`；
- `logs_2.sqlite`；
- `goals_1.sqlite`；
- `memories_1.sqlite`；
- `queue_1.sqlite`；
- `thread_history_1.sqlite`。

`StateRuntime` 的注释说明，Logs 和 paginated Thread History 放进独立文件是为了减少与其他 state 写入的锁竞争。

这也意味着：

> 分属不同数据库文件的变化，不在一个普通 SQLx transaction 中原子提交。

应用必须能容忍某个数据库成功、另一个失败，并根据各自权威来源修复。

## 7. 拆数据库的收益与代价

| 收益 | 代价 |
|---|---|
| 不同业务写入较少争抢同一 writer | 跨文件不能自然组成单事务 |
| 日志维护不阻塞主要状态 | 读取综合状态时可能观察到不同更新时间 |
| 某个文件损坏时可单独恢复 | migration、备份和生命周期管理更复杂 |
| 可为不同数据选择维护策略 | 连接池和 sidecar 文件数量增加 |

架构判断不是“一个大库一定好”或“拆得越多越好”，而是权衡锁域、恢复域和原子边界。

## 8. 当前 SQLite 连接配置

`SqliteConfig::open_read_write_pool` 统一设置：

- `create_if_missing(true)`；
- `journal_mode(WAL)`；
- `synchronous(NORMAL)`；
- `auto_vacuum(INCREMENTAL)`；
- `busy_timeout(5 seconds)`；
- 最多 5 个连接。

只读连接则不创建文件，使用单连接 pool。

这些不是独立的“性能开关”，而是共同定义读写并发、等待、磁盘同步与维护行为。

## 9. WAL 是什么

`WAL` 是 **Write-Ahead Log**，即预写日志。

简化理解：修改不是立即就地覆盖主数据库页，而是先追加到 WAL；读者可以继续从稳定快照读取，之后 checkpoint 再把 WAL 内容合并回主文件。

```text
writer → append WAL frames
reader → 读取主库 + 自己快照可见的 WAL
checkpoint → 将可合并内容写回主库
```

WAL 常改善“一写多读”并发，但 SQLite 仍主要是单 writer 模型；它不是让多个写事务无限并行。

## 10. `-wal` 和 `-shm` 也是数据库状态的一部分

WAL 模式常伴随：

- `database.sqlite-wal`：尚未 checkpoint 的 WAL 内容；
- `database.sqlite-shm`：共享内存索引等协调数据。

复制、移动或恢复数据库时，如果只处理主 `.sqlite` 文件，可能留下不匹配的 sidecar。

`codex-rs/state/src/runtime/recovery.rs` 的恢复路径会把目标数据库、`-wal` 和 `-shm` 一起移动到备份目录。

## 11. `synchronous=NORMAL` 表达一种权衡

SQLite 的 synchronous 设置控制关键步骤要求操作系统同步到稳定存储的严格程度。

`NORMAL` 通常在性能与耐久性间折中：数据库仍以事务和 WAL 协议维护结构一致性，但极端掉电模型下，最近一次已返回的提交可能没有最高等级的落盘保证。

因此“Durability”必须带故障模型来读：

- 进程崩溃；
- 操作系统崩溃；
- 突然断电；
- 磁盘或文件损坏。

不能只说“commit 后绝对永不丢失”。

## 12. Checkpoint 是什么

Checkpoint 会尝试把 WAL 中的页合并回主数据库，并使 WAL 有机会缩小或复用。

`codex-rs/state/src/runtime/logs.rs` 的启动维护使用：

```sql
PRAGMA wal_checkpoint(PASSIVE)
```

注释明确要求启动清理不要等待或阻塞前台工作。`PASSIVE` 会复制当下可处理的内容，遇到 active reader/writer 所需帧则跳过等待。

Checkpoint 是存储维护，不等于业务 checkpoint。Memory backfill 的 watermark 则是业务进度检查点，两者不要混淆。

## 13. `busy_timeout` 解决什么

SQLite 写锁暂时被占用时，立即失败会让短暂竞争大量暴露给业务。5 秒 `busy_timeout` 允许连接在限定时间内等待锁释放。

它解决短暂 lock contention，不解决：

- 长事务一直持锁；
- 死循环；
- 两个业务操作逻辑冲突；
- 超时后是否安全重试；
- 重试是否重复外部副作用。

`database is locked` / `database is busy` 与 corruption 也不是同一种错误，不能用“删库重建”处理锁竞争。

## 14. Deferred transaction 与 `BEGIN IMMEDIATE`

普通 `begin()` 在 SQLite 中通常对应 deferred transaction：开始时不立即取得 writer slot，第一次写时才竞争。

`BEGIN IMMEDIATE` 会更早申请写事务位置。它适合：

```text
先读取当前状态
根据读取结果做决定
再写回
```

如果不提前取得 writer slot，两个事务可能都读到同一旧值，然后同时认为自己可以更新。

代价是更早阻塞其他 writer，所以只应在确实需要 read-modify-write 原子判断时使用。

## 15. 当前实例：Thread History 用 `BEGIN IMMEDIATE`

`codex-rs/thread-store/src/local/thread_history.rs` 的 `apply_projection` 会在 `BEGIN IMMEDIATE` 事务里：

1. 读取当前 `next_rollout_byte_offset` 和 ordinal；
2. 检查它是否等于本次 expected start offset；
3. 应用 turn/item change set；
4. 更新 projection state 的 next offset 和 ordinal；
5. commit。

这保证投影行和“我已经处理到哪里”的游标一起前进。

## 16. 为什么数据和进度游标必须同事务

如果先写游标再写数据：

```text
offset 前进
程序崩溃
投影行未写
```

重启会误以为那段日志已经物化，从而永久漏数据。

如果先写数据再单独写游标：

```text
投影行写入
程序崩溃
offset 未前进
```

重启会重放同一段，必须依赖幂等 upsert 才不重复。

把两者放在同一 transaction 后，只会一起 commit 或一起 rollback。

## 17. 权威 Rollout 与可重建 Projection

Thread History 注释把 durable JSONL rollout 放在权威位置，SQLite 是可查询 projection。

其不变量是：

> SQLite 可以落后于 rollout，但不能声称已经投影了实际没有写入的数据。

因此 projection failure 后，可以从保存的 offset 继续重放。测试 `append after simulated projection failure` 会人工回退投影状态，再确认后续追加能恢复缺失行。

这是一种典型 crash-consistent projection 设计。

## 18. Isolation 到底隔离什么

隔离不是让事务“完全看不见其他事务”，而是规定并发执行可观察到哪些交错。

常见异常：

| 异常 | 含义 |
|---|---|
| Dirty read | 读到别人尚未提交的数据 |
| Non-repeatable read | 同事务两次读同一行得到不同值 |
| Phantom read | 同一条件查询第二次出现/消失了行 |
| Lost update | 基于旧值的写覆盖了别人的更新 |
| Write skew | 两事务分别更新不同记录，却共同破坏跨行约束 |

SQLite 的具体行为还受 WAL、事务开始方式和连接设置影响。阅读 Codex 时，更实用的是先找 read-modify-write 是否放在同一 write transaction。

## 19. 一个经典 lost update

错误写法：

```text
Task A: SELECT tokens_used → 100
Task B: SELECT tokens_used → 100
Task A: UPDATE tokens_used = 110
Task B: UPDATE tokens_used = 120
```

A 的 +10 被 B 覆盖。

常见修法：

- 单 SQL：`SET tokens_used = tokens_used + ?`；
- transaction 内 read-modify-write；
- `WHERE version = expected_version` 的 optimistic concurrency；
- 串行 owner；
- 数据库约束或原子 upsert。

## 20. 单条 SQL 本身也可以是原子边界

不是所有正确并发更新都需要手写多语句 transaction。

`SqliteQueueStore::enqueue` 用一条 `INSERT ... SELECT ... WHERE count < limit RETURNING ...` 同时：

- 计算 thread 内下一个 `queue_order`；
- 检查队列未达到 `MAX_QUEUE_ITEMS`；
- 插入并返回记录。

配合 `(thread_id, queue_order)` UNIQUE index，并发测试证明两个 runtime 争最后一个位置时只会一个成功，最终数量不超过上限。

## 21. Database constraint 是最后防线

当前 schema 中可以看到：

- `PRIMARY KEY (thread_id, turn_id)`；
- `PRIMARY KEY (thread_id, turn_id, item_id)`；
- `UNIQUE (thread_id, rollout_ordinal)`；
- `UNIQUE (thread_id, queue_order)`；
- `CHECK (id = 1)`；
- status `CHECK (... IN (...))`；
- `REFERENCES ... ON DELETE CASCADE`。

应用检查能给出友好错误，数据库约束能在并发竞态或代码遗漏时守住底线。两者不是二选一。

## 22. `ON CONFLICT` 不只是“忽略错误”

SQLite UPSERT 常见形式：

```sql
INSERT ...
ON CONFLICT(key) DO NOTHING
```

或：

```sql
INSERT ...
ON CONFLICT(key) DO UPDATE SET ...
```

它能把“查是否存在，再决定 insert/update”的竞态压进单条 SQL。

但 `DO UPDATE` 更新哪些字段、带不带 `WHERE`，决定了业务语义。无条件覆盖可能把新状态写回旧状态。

## 23. 当前实例：Turn 终态更新带条件

Thread History 的 turn upsert 在冲突时更新终态字段，但带有：

```sql
WHERE thread_turns.rollout_end_ordinal IS NULL
  AND thread_turns.status = 'inProgress'
```

它表达：只有原记录仍是进行中且尚无结束 ordinal 时，才接受终态变化。

这比“主键冲突就全部覆盖”更安全，也和第 47 章的唯一终态思想一致。

## 24. `rows_affected` 是轻量 CAS 结果

很多状态更新写成：

```sql
UPDATE jobs
SET status = 'error', ...
WHERE status = 'running'
  AND ownership_token = ?
```

然后检查 `rows_affected() > 0`。

它等价于一次轻量 compare-and-set：

- 1 行：前置条件仍成立，我成功更新；
- 0 行：状态或 owner 已改变，我不能声称成功。

不要忽略 affected rows，否则“SQL 成功执行”可能被误解为“业务状态成功改变”。

## 25. 显式 rollback 让失败语义更清楚

`SqliteQueueStore::reorder` 会在事务中读取全部 item，验证请求必须恰好包含每个 ID 一次。验证失败时显式 `rollback().await` 再返回错误。

事务对象在异常离开作用域时也有回滚保护，但显式 rollback 有两个好处：

- 代码读者清楚知道这是业务拒绝，不是意外遗漏 commit；
- 可以等待回滚完成并及时释放 writer。

测试还确认无效 reorder 不能修改其他 thread 的消息。

## 26. Job claim 是数据库并发控制

Memory job 不是只靠进程内 mutex 抢占。`codex-rs/state/src/runtime/memories.rs` 会把这些状态写进 SQLite：

- `status`；
- `worker_id`；
- `ownership_token`；
- `lease_until`；
- `retry_at`；
- `retry_remaining`；
- input/last-success watermark。

这样多个 runtime 连接同一 SQLite 时，也能通过持久状态协调，而不是各自认为自己是唯一 worker。

## 27. 当前实例：`BEGIN IMMEDIATE` 原子抢占 Memory job

`try_claim_stage1_job` 在 `BEGIN IMMEDIATE` 内：

1. 检查已有 output 或 success watermark 是否足够新；
2. 检查全局 running job 数是否小于上限；
3. 检查旧 lease 是否过期；
4. 检查 retry backoff 与 retry budget；
5. 用 UPSERT 写入新的 worker、ownership token 和 lease；
6. 根据 affected rows 返回 Claimed 或精确 skip 原因。

早拿 writer slot 使“检查容量 + 抢占”成为一个不可被另一个 writer 穿插的决策。

## 28. Lease 解决进程崩溃后的永久占用

如果只存 `status = running`，worker 崩溃后任务会永远被认为正在运行。

Lease 增加时间边界：

```text
status = running
lease_until = 未来时间
```

- lease 未过期：其他 worker 不抢；
- lease 过期：原 worker 被视为失去所有权，新 worker 可接管。

Lease 不是“任务一定停止”。旧 worker 可能网络暂停后恢复，所以还需要 ownership token 做 fencing。

## 29. Ownership token 防止旧 worker 迟到提交

每次 claim 生成新的 UUID ownership token。完成或失败更新必须匹配当前 token：

```sql
WHERE status = 'running'
  AND ownership_token = ?
```

场景：

```text
Worker A lease 过期
Worker B 取得新 token
Worker A 恢复，尝试提交旧结果
```

A 的 token 已不匹配，因此 affected rows 为 0，不能覆盖 B 的新世代。这就是持久化 fencing token。

## 30. Retry state 也要原子更新

`mark_stage1_job_failed` 在一条 UPDATE 中：

- 改为 error；
- 写 `finished_at`；
- 清除 lease；
- 设置 `retry_at`；
- `retry_remaining = retry_remaining - 1`；
- 写 `last_error`；
- 同时校验 ownership token。

如果这些字段分成多条独立语句，崩溃后可能出现“状态 error 但 lease 未清”或“已失败但 retry budget 未减少”。

## 31. Migration 也是状态机

数据库 schema 会随版本演进。Migration 系统需要回答：

- 哪些版本已经应用？
- 同一个版本的 SQL 是否被修改过？
- 当前二进制比数据库旧时怎么办？
- migration 中途失败能否重试？
- 多进程同时启动时谁执行？

`codex-rs/state/src/migrations.rs` 使用 SQLx embedded migrator，为 state、logs、goals、memories、queue 和 thread history 分别维护 migration 集合。

## 32. Migration version 和 checksum 各自保护什么

- version：决定顺序和“是否已经应用”；
- checksum：确认已知版本的内容没有被事后悄悄改写。

当前 runtime migrator 设置 `ignore_missing: true`，允许旧 Codex binary 打开已经被更新 binary 迁移得更靠前的数据库。

但已知 migration 仍按 checksum 验证，所以它只放宽“数据库比我新”，不是忽略历史篡改。

## 33. Reader-first 兼容也适用于数据库

新版本写入新 schema，而旧 binary 仍可能并行运行。安全演进通常先让旧 reader 能容忍新数据库，再让新 writer 使用新字段。

Migration `0039_threads_recency_at.sql` 除了增加字段和索引，还创建 insert trigger：旧 binary 插入未设置新 recency 字段的 thread 时，数据库会从旧 timestamp 补出新字段。

这是数据库层的 compatibility bridge：新 schema 暂时补偿旧 writer。

## 34. Table rebuild migration 为什么危险

SQLite 修改某些约束时，常见流程是：

1. 创建 `table_new`；
2. 把旧数据复制进去；
3. 删除旧表；
4. rename 新表；
5. 重建 index/trigger；
6. 恢复 foreign key 设置。

`0033_thread_goal_stopped_statuses.sql` 就会临时关闭 foreign keys、创建新表、复制、删除、rename，再重新打开。

风险包括漏字段、漏索引、约束不兼容、复制失败和旧 binary 并行写入。必须用真实旧库 fixture 做升级测试，而不只是测试空库初始化。

## 35. 不要随意修改已发布 migration

已应用 migration 的 SQL 是历史契约。直接编辑旧文件会导致：

- checksum 与数据库记录不一致；
- 新装数据库和升级数据库得到不同 schema；
- 已升级用户无法按同一路径重现；
- 回滚和诊断困难。

通常应新增下一条 migration 修正，而不是改写已经发布的历史。只有明确的兼容修复流程才能调整 migration metadata。

当前 `repair_legacy_recency_migration_version` 就是一个有严格条件的定向修复，并有专门测试。

## 36. Crash consistency 要枚举提交点

对一次写入，至少区分：

```text
构造数据
  ↓
BEGIN
  ↓
执行 SQL
  ↓
COMMIT 请求
  ↓
COMMIT 返回
  ↓
通知外部系统
```

崩溃发生在不同位置，调用者知道的信息不同。最麻烦的是：请求已到数据库，但响应在返回前丢失，调用者不知道 commit 是否成功。

因此重试写入要使用稳定 operation key、UNIQUE constraint、UPSERT 或读取确认，不能假设“收到错误就一定没写”。

## 37. Process crash 与 power loss 不同

- Process crash：操作系统和 SQLite 仍可完成部分已提交 I/O；
- OS crash：内核缓存和进程同时消失；
- Power loss：存储控制器与硬件缓存行为也进入故障模型；
- Corruption：不是正常 rollback，而是页或文件结构损坏。

WAL、transaction 和 synchronous setting 对不同故障的保证不同。测试只 kill 进程不能证明断电耐久性；测试断电模拟也不能代替 schema/业务不变量验证。

## 38. Corruption 与普通业务错误要分流

`recovery.rs` 会识别 SQLite code 11/26，以及 malformed、not a database 等 corruption 信息；lock/busy 则由另一个分类函数识别。

恢复策略不同：

- locked/busy：等待、提示关闭其他进程、有限重试；
- constraint violation：修正输入或并发逻辑；
- migration incompatibility：兼容修复或升级；
- corruption：备份损坏文件，重建可恢复数据库；
- permission/disk full：修复环境，不应假装 corruption。

错误字符串中路径恰好含 `sqlite_corrupt` 也不能据此判断损坏；测试专门覆盖了这种误判。

## 39. 当前实例：只隔离损坏的数据库

`backup_runtime_db_for_fresh_start` 会：

1. 确定实际失败数据库路径；
2. 创建唯一备份目录；
3. 移动该 DB 主文件及 `-wal`、`-shm`；
4. 保留其他 runtime DB；
5. 让启动流程创建新数据库并迁移；
6. 向用户报告原路径和备份目录。

测试 `backup_moves_only_requested_runtime_db_files_to_backup_folder` 明确验证其他数据库文件仍在。

这是“缩小故障域”，不是一遇到损坏就删除整个 SQLite home。

## 40. 为什么先备份再重建

直接删除损坏文件有三个问题：

- 不可恢复；
- 无法事后诊断根因；
- 可能删错仍有价值的数据。

移动到 backup folder 可以：

- 让应用先恢复服务；
- 保留人工取证和未来修复可能；
- 清楚告知用户数据去了哪里；
- 避免覆盖旧备份，因目录名带 timestamp + sequence。

即使数据库被定义为可重建，安全恢复也应优先可逆操作。

## 41. `PRAGMA integrity_check` 是诊断证据

`StateRuntime::sqlite_integrity_check` 用只读 pool 执行：

```sql
PRAGMA integrity_check
```

它能检查 SQLite 结构一致性，正常库通常返回 `ok`。

但 integrity check 不能证明：

- 每个业务 thread 都有正确 title；
- projection 与 JSONL 完全同步；
- job ownership 语义正确；
- 没有跨数据库的逻辑不一致。

因此还需要业务 audit 和 reconciliation。

## 42. 可重建数据库仍需要明确权威源

App-server 的恢复提示说会从 saved data 重建本地数据库。这个承诺成立的前提是：

- 权威 rollout 仍存在；
- projector 能幂等重放；
- offset/watermark 不会越过未物化内容；
- schema migration 能在新空库执行；
- 某些非投影状态是否允许丢失已有明确边界。

“可重建”不是给数据库可靠性降级找借口，而是一份必须有测试支撑的恢复契约。

## 43. Transaction 不应包含慢外部 I/O

不要在持有 SQLite write transaction 时：

- 调用模型 API；
- 等用户审批；
- 执行 shell；
- 下载网络资源；
- 做长时间 CPU 处理。

这会延长 writer lock，引发 busy timeout 和级联等待。

更合适的形状：

```text
事务 A：claim + 写 ownership token
事务外：执行慢工作
事务 B：按 token 提交结果
```

Memory job 的 lease/ownership 设计就是这种分段事务状态机。

## 44. 重试数据库操作前先判断幂等性

可以安全自动重试的候选：

- 纯 SELECT；
- 以稳定主键 `DO NOTHING` 的 insert；
- `WHERE version/token = expected` 的 CAS；
- 明确可重复的 projection upsert。

危险重试：

- `balance = balance + amount` 没有 operation key；
- 数据库写前后夹着外部副作用；
- 每次重试生成新业务 ID；
- 不知道上次 commit 是否成功却再次追加记录。

busy timeout 是等待机制，不等于应用层重试策略。

## 45. 多数据库一致性如何补偿

当状态分散在 state、logs、goals、memories、queue 和 history DB 时，可以依靠：

- 每库独立 transaction；
- 稳定 thread/turn/item ID 关联；
- rollout 作为部分 projection 的权威源；
- 幂等 upsert；
- startup/backfill reconciliation；
- 删除流程逐项记录失败并重试；
- telemetry 指出哪个 DB/phase 失败。

这属于 eventual repair，不等于跨库 strong transaction。文档和 API 应诚实表达保证等级。

## 46. 数据库可观测性应该记录什么

当前 state crate 已定义初始化、错误、backfill、fallback 等 metric。进一步排查时关注：

- DB kind；
- open/migrate/post-init phase；
- query/transaction duration；
- busy/locked count；
- commit/rollback；
- affected rows；
- WAL size/checkpoint result；
- pool wait time；
- migration version；
- recovery backup path；
- projection lag offset/ordinal。

不要把完整 SQL 参数、token、用户内容直接写入日志。

## 47. 测试应覆盖哪些故障点

事务与恢复测试至少包括：

1. 第二条语句失败时第一条不提交；
2. invalid input 显式 rollback；
3. 两个 runtime 并发争同一容量/位置；
4. affected rows 为 0 时不报告成功；
5. lease 过期后新 owner 可接管；
6. 旧 ownership token 不能迟到提交；
7. projection failure 后从旧 offset 重放；
8. migration 从真实旧 schema 升级；
9. newer DB 能被旧 binary 按约定打开；
10. corruption 恢复只移动目标 DB 和 sidecar；
11. lock error 不被误判为 corruption；
12. integrity check 对正常/损坏 fixture 给出预期结果。

## 48. 常见错误

### 错误一：多条 SQL 都成功过，所以无需事务

中途失败或崩溃时仍会留下半完成状态。

### 错误二：先 SELECT，再在事务外 UPDATE

并发 writer 可能在两步之间改变前置条件。

### 错误三：所有写都用 `BEGIN IMMEDIATE`

不必要地提前占 writer slot，降低并发。

### 错误四：忽略 `rows_affected`

SQL 没报错不等于业务 CAS 成功。

### 错误五：把 locked 当 corruption

锁竞争不应触发备份重建。

### 错误六：只备份 `.sqlite`，遗漏 WAL sidecar

可能留下不匹配状态或漏掉尚未 checkpoint 的内容。

### 错误七：编辑已发布 migration

破坏 checksum 和升级路径一致性。

### 错误八：在 write transaction 内等待网络

长时间持锁，使其他请求超时。

## 49. 本章术语表

| 英文或代码名 | 中文理解 | 在代码里先问什么 |
|---|---|---|
| transaction | 事务 | 哪些 SQL 必须共同成功？ |
| atomicity | 原子性 | 失败时是否只会全成或全不成？ |
| consistency | 一致性 | 哪些数据库与业务不变量必须成立？ |
| isolation | 隔离性 | 并发事务允许观察到什么？ |
| durability | 持久性 | 对进程崩溃、OS 崩溃、断电分别保证什么？ |
| `BEGIN` | 开始事务 | deferred 还是 immediate？ |
| `COMMIT` | 提交 | 返回前后崩溃怎样确认结果？ |
| `ROLLBACK` | 回滚 | 是否等待回滚完成并释放 writer？ |
| WAL | 预写日志 | 主库、WAL、SHM 是否一起管理？ |
| checkpoint | 检查点 | 是 WAL 合并，还是业务进度 watermark？ |
| `synchronous` | 同步落盘级别 | 采用了什么性能/耐久性权衡？ |
| busy timeout | 锁等待上限 | 超时后能否安全重试？ |
| lock contention | 锁竞争 | 哪个事务持有 writer 太久？ |
| dirty read | 脏读 | 是否读到未提交数据？ |
| lost update | 丢失更新 | 是否基于旧快照覆盖别人的写？ |
| write skew | 写偏差 | 不同行更新是否共同破坏跨行约束？ |
| constraint | 约束 | PRIMARY KEY/UNIQUE/CHECK/FK 守住什么？ |
| UPSERT | 插入或冲突更新 | 冲突时是否会把新状态覆盖成旧状态？ |
| CAS | 比较并交换 | WHERE 前置条件和 affected rows 是什么？ |
| lease | 租约 | worker 崩溃后何时允许接管？ |
| ownership token | 所有权令牌 | 旧 worker 能否迟到提交？ |
| migration | 数据库迁移 | version、checksum、旧库 fixture 是否完整？ |
| sidecar | 伴随文件 | `-wal`/`-shm` 是否随主库一起处理？ |
| corruption | 损坏 | 是否与 locked、permission、disk full 正确分流？ |
| projection lag | 投影滞后 | SQLite 可以落后权威日志多少？ |
| reconciliation | 对账修复 | 怎样发现并重建跨库或投影差异？ |

代码单词可以这样拆：

- `begin_with("BEGIN IMMEDIATE")`：开始事务并立即竞争 writer slot；
- `next_rollout_byte_offset`：下一次应从 Rollout 哪个字节位置继续投影；
- `rows_affected`：这次条件更新真正修改了几行；
- `try_claim_stage1_job`：按 lease、容量和 retry 条件尝试取得 Stage 1 job；
- `repair_legacy_recency_migration_version`：定向修复旧 recency migration 的版本记录；
- `backup_runtime_db_for_fresh_start`：备份单个故障数据库及 sidecar，允许创建新库启动；
- `sqlite_integrity_check`：用 SQLite 内建检查诊断文件结构。

## 50. 本章小结

读数据库代码时，按六层边界检查：

1. **语句边界**：单条 SQL 是否已经能原子表达条件和更新？
2. **事务边界**：哪些多语句变化必须共同 commit？
3. **并发边界**：deferred/immediate、constraint、CAS 和 token 怎样防竞态？
4. **持久边界**：WAL、synchronous 和 checkpoint 针对哪种故障？
5. **系统边界**：跨数据库、JSONL 和远端副作用怎样对账？
6. **演进边界**：migration、checksum、旧 binary 和损坏恢复怎样验证？

最值得记住的一句话是：

> 数据库事务能阻止事务内部的半写入；权威日志、幂等重放、租约栅栏和恢复流程，负责阻止整个系统在崩溃后相信错误的中间状态。

看到 `pool.begin()`、`BEGIN IMMEDIATE`、`ON CONFLICT` 或 `rows_affected()` 时，不要只翻译 SQL；继续追踪它保护的业务不变量、锁范围、崩溃点和重试语义。
