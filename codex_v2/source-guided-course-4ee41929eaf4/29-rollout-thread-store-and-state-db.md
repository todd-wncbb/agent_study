# Rollout、Thread Store 与状态数据库：任务记录保存在哪里

> 本章解决的问题：关闭 Codex 后，一条任务为什么还能出现在历史列表中？点击恢复时，系统从哪里找回对话、工具结果和配置？Rollout 文件、Thread Store、SQLite 数据库和内存中的 Session 分别保存什么？

阅读本章前，建议先看：

- [02：生命周期与状态所有权](02-thread-turn-step.md)
- [05：上下文、持久化与恢复](05-context-state-resume.md)
- [17：长期 Memory](17-long-term-memory.md)
- [28：TUI 与客户端状态管理](28-tui-and-client-state.md)

---

## 1. 先用“账本、目录和正在办公的人”理解

这一章最容易卡住的原因，是源码中有好几种都像“保存 thread”的东西。

先建立一个简化类比：

| Codex 概念 | 类比 | 主要作用 |
|---|---|---|
| Rollout JSONL | 按时间追加的原始账本 | 保存可重放的执行记录 |
| SQLite state DB | 图书馆目录卡 | 快速搜索、排序和读取 thread 元数据 |
| Thread history DB | 按 turn/item 整理的分页索引 | 不必读取整本账本就能分页浏览历史 |
| `ThreadStore` | 图书管理员统一窗口 | 隐藏底层是文件、数据库还是别的后端 |
| `ThreadManager` | 正在管理办公室的人 | 管理当前进程中已加载的运行对象 |
| `Session` / `CodexThread` | 已经打开并正在使用的档案 | 接收输入、运行 turn、持有 channel 和工具状态 |

最重要的区别是：

> Rollout 和数据库属于持久状态；Session、task、channel 属于当前进程中的运行状态。

关闭程序后：

- 运行 task 和 channel 消失；
- rollout 与数据库仍在磁盘上；
- 下次 resume 会根据持久记录创建新的运行对象；
- 不是把旧进程的内存“原封不动复活”。

---

## 2. 一张完整状态地图

```text
                    当前进程中的运行层
┌─────────────────────────────────────────────────────┐
│ ThreadManager                                       │
│   └── CodexThread / Session                         │
│         ├── active turn / tasks                     │
│         ├── channels / cancellation tokens          │
│         ├── context manager                         │
│         └── live ThreadStore writer                 │
└──────────────────────┬──────────────────────────────┘
                       │ append / flush / read
                       ▼
                    持久化抽象层
┌─────────────────────────────────────────────────────┐
│ ThreadStore trait                                   │
│   create / resume / append / flush / load / list    │
│   update metadata / archive / delete / prepare fork │
└───────────────┬──────────────────┬──────────────────┘
                │                  │
                ▼                  ▼
       Rollout JSONL          SQLite databases
       完整可重放记录          元数据与分页投影
```

不要把箭头理解为“每次都同步写完所有地方”。实际实现有后台 writer、延迟 materialize、projection 和 repair，因此不同持久层可能短时间不同步。

设计重点不是要求它们永远在同一 CPU 指令中更新，而是：

- 哪份数据是可重放依据；
- 哪份数据用于快速查询；
- 不一致时如何检测和修复；
- 需要可靠持久化时在哪里等待 flush。

---

## 3. Rollout 到底是什么

在当前本地实现中，rollout 是一份 JSONL 文件。JSONL 是 JSON Lines：每一行是一个独立 JSON 对象。

概念示例：

```jsonl
{"timestamp":"...","ordinal":0,"type":"session_meta","payload":{"id":"thread-123","cwd":"/repo"}}
{"timestamp":"...","ordinal":1,"type":"event_msg","payload":{"type":"turn_started","turn_id":"turn-1"}}
{"timestamp":"...","ordinal":2,"type":"response_item","payload":{"type":"message","role":"user","content":"修复测试"}}
{"timestamp":"...","ordinal":3,"type":"response_item","payload":{"type":"function_call","name":"exec_command"}}
{"timestamp":"...","ordinal":4,"type":"response_item","payload":{"type":"function_call_output","output":"test passed"}}
{"timestamp":"...","ordinal":5,"type":"event_msg","payload":{"type":"turn_complete"}}
```

上面只是帮助理解的数据形状，不保证字段与当前序列化输出逐字一致。真实类型定义在：

- `codex-rs/protocol/src/protocol.rs` 的 `RolloutLine`；
- 同文件的 `RolloutItem`；
- 同文件的 `SessionMetaLine`。

一条 `RolloutLine` 主要包含：

- `timestamp`：写入记录的时间；
- `ordinal`：该 rollout 中有顺序的编号；
- 一个被 flatten 到行上的 `RolloutItem`。

JSONL 很适合这种场景，因为它可以按顺序追加，不需要每次重写整个会话文件。

---

## 4. Rollout 保存的不是“屏幕录像”

如果把每一个实时事件都保存下来，文件会包含大量临时数据：

- 每个模型文字 delta；
- 命令输出的每一小段；
- spinner 或状态栏变化；
- 已经结束的临时审批 UI；
- MCP startup 的中间进度；
- TUI 的每一次 redraw。

这些信息大多不适合长期恢复。

`codex-rs/rollout/src/policy.rs` 中的持久化策略会选择哪些 `RolloutItem` 应进入耐久历史。

典型会保存：

- Session 元数据；
- 用户和 Agent 的模型消息；
- tool call 与 tool output；
- compaction 记录；
- TurnContext；
- WorldState；
- turn started、completed、aborted 等关键生命周期事件；
- token count、thread settings 等恢复需要的状态。

典型不会保存为 rollout 历史：

- 各种内容 delta；
- command output delta；
- `ItemStarted`；
- 部分临时 warning；
- approval request 的过程事件；
- UI 或连接层的短暂状态。

所以 rollout 更像：

> 足以恢复和审计关键语义的事件账本，而不是重播用户当时看到的每一帧动画。

---

## 5. `RolloutItem` 的主要类别

当前 `RolloutItem` 包含这些主要变体：

| 变体 | 作用 |
|---|---|
| `SessionMeta` | thread/session 身份、cwd、来源、provider、基础指令等会话级信息 |
| `ResponseItem` | 模型输入输出中的消息、工具调用和工具结果 |
| `InterAgentCommunication` | Agent 之间可恢复的通信内容 |
| `InterAgentCommunicationMetadata` | Agent 通信的本地传递元数据 |
| `Compacted` | 上下文被压缩后的替代历史 |
| `TurnContext` | 某轮使用的配置、指令和运行上下文 |
| `WorldState` | cwd、权限和环境等世界状态的完整快照或 patch |
| `EventMsg` | 被策略选中的关键 Core 生命周期事件 |

这里再次体现了三个不同需求：

- 模型下一次要看到什么；
- 恢复运行时需要知道什么；
- 用户界面曾经显示过什么。

它们有交集，但不完全相同。

---

## 6. `SessionMeta` 为什么通常出现在前面

新 rollout 的 `SessionMeta` 保存 thread 级身份和初始条件，例如：

- `session_id` 和 thread `id`；
- 是否从另一 thread fork；
- parent thread ID；
- 创建时间；
- cwd；
- 来源和 originator；
- Codex 版本；
- model provider；
- base instructions；
- dynamic tools；
- memory mode；
- history mode；
- 多 Agent 版本；
- Git 信息。

恢复文件时，读取器把遇到的第一个 `SessionMeta` 当作这个 rollout 自己的 canonical thread 身份。

为什么强调“第一个”？

因为旧式 copied fork 可能把来源 history 也复制进新 rollout，其中可能出现来源 thread 的 `SessionMeta`。新 rollout 自己的元数据必须优先确定身份。

---

## 7. Rollout 文件在哪里

`codex-rs/rollout/src/lib.rs` 定义了两个相对目录：

- `sessions`：活跃或普通保存的 rollout；
- `archived_sessions`：已归档 rollout。

通常它们位于 Codex home 下。具体 Codex home 和 SQLite home 可以由配置决定，因此阅读源码和排查问题时，不应把某个绝对路径永久写死。

普通 session 文件还会按日期组织子目录。归档时，本地 store 会移动 rollout；取消归档时再移回 `sessions/<year>/<month>/<day>/` 对应位置。

文件可能是普通 `.jsonl`，也存在压缩后的 `.jsonl.zst` 支持。读取逻辑通过统一的 rollout line reader 处理可用形式。

---

## 8. RolloutRecorder：为什么用后台 writer

模型和工具可能频繁产生记录。如果每次都在 Agent 主循环里执行阻塞文件写入：

- 网络流处理会被拖慢；
- 取消响应会变迟；
- UI 事件可能积压；
- 多个调用方需要争用同一个文件句柄。

`RolloutRecorder` 因此把写入交给一个后台 Tokio task：

```text
Session / ThreadStore
       │ RolloutCmd
       ▼
bounded mpsc channel
       │
       ▼
Rollout writer task
       │
       ▼
JSONL file
```

当前 writer channel 容量是 256。容量数字可能变化，设计含义是：

- 有界，避免无限占用内存；
- 满时异步发送方会等待，形成背压；
- 文件句柄由一个 writer task 持有，写入顺序更清楚。

---

## 9. 新 thread 的 rollout 可以延迟创建

`RolloutRecorder::new` 对新 thread 会先计算路径和元数据，但延迟到显式 `persist()` 才创建或打开文件。

这叫 lazy materialization，延迟实体化。

为什么要这样设计？

- 某个 session 可能初始化后很快失败，还没有值得保存的内容；
- 不必为从未真正使用的 thread 留下空文件；
- 可以先把项目放在内存 pending queue，等确定需要持久化时再落盘。

而 resume 旧 thread 时，需要立即打开已有 rollout 以继续追加。

因此“已经创建 `RolloutRecorder`”不一定意味着磁盘上已经出现文件。

---

## 10. Add、Persist、Flush、Shutdown 有什么区别

这几个词很像，但耐久保证不同。

### 10.1 Add items

`record_canonical_items` 或 Thread Store 的 `append_items` 把项目排进 writer。

它表示：

> 请按顺序记录这些内容。

调用成功首先证明命令进入 writer channel，不应把它直接理解成“磁盘已经可靠写完”。

### 10.2 Persist

`persist()` 会：

- materialize rollout 文件；
- 写出当前缓冲项目；
- 等待 writer 返回结果。

它是幂等的，失败时会保留 pending items，之后仍可重试。

### 10.3 Flush

`flush()` 会等待此前排队的写入完成，并确保 writer 已经把它们提交为可读取的持久内容。

因此在这些边界前尤其重要：

- 读取 fork snapshot；
- 用户中断后保存终态；
- 关闭 thread；
- 某个调用方马上要从文件恢复。

### 10.4 Shutdown

`shutdown()` 先 drain pending items，再停止 writer task。

如果 drain 失败，writer 保持存活，调用方仍有机会重试，而不是错误发生后直接丢掉全部 pending 状态。

### 10.5 Discard

`ThreadStore::discard_thread` 用于初始化已经失败、又不应强制把未实体化内容保存下来的场景。它释放 live writer，同时保留此前已经耐久写出的数据。

---

## 11. `ThreadStore` 解决了什么问题

如果 Core 到处直接操作：

- JSONL 文件路径；
- SQLite 查询；
- archive 目录；
- 压缩文件；
- 远端 RPC；

那么任何存储变化都会穿透整个系统。

`codex-rs/thread-store/src/store.rs` 定义的 `ThreadStore` trait 提供存储中立边界。应用层主要使用稳定的 `ThreadId`，具体实现负责把它解析到：

- 本地 rollout；
- SQLite；
- 内存测试 store；
- 或其他可实现相同契约的后端。

它的主要能力包括：

```text
创建/继续写入：create_thread, resume_thread, append_items
耐久边界：persist_thread, flush_thread, shutdown_thread
恢复读取：load_history, load_latest_model_context
浏览查询：read_thread, list_threads, list_turns, list_items
修改元数据：update_thread_metadata
生命周期：archive_thread, unarchive_thread, delete_thread
Fork：prepare_fork
搜索：search_threads, search_thread_occurrences
```

把它称为 store，并不表示所有数据都放在同一数据库表里。它是统一行为接口。

---

## 12. `LocalThreadStore` 怎样组合文件与 SQLite

`codex-rs/thread-store/src/local/mod.rs` 对本地实现有一段非常关键的说明：

- rollout JSONL 是耐久 replay 格式；
- 没有 SQLite 时，旧文件仍然可读；
- SQLite state DB 可用时，作为可查询的元数据索引；
- live append 仍写 canonical JSONL；
- 追加产生的元数据通过明确的 metadata patch 更新 SQLite；
- 旧文件和 SQLite 缺失场景依然保留兼容读取与修复路径。

可以总结成：

```text
Rollout：完整语义记录，偏顺序追加和恢复
SQLite：查询投影，偏筛选、排序、分页和元数据更新
ThreadStore：协调二者，并提供一致 API
```

这里的 projection 与第 28 章 UI projection 类似：它从更原始记录中派生出适合某种用途的视图。

---

## 13. State DB 不是只有一个物理文件

日常交流中常说“状态数据库”，但当前 `StateRuntime` 管理多份 SQLite 数据库，以降低不同工作之间的锁竞争。

`SqliteConfig` 可以返回例如：

- primary state DB；
- logs DB；
- goals DB；
- memories DB；
- durable user-message queue DB；
- paginated thread-history DB。

与本章最直接相关的是：

### 13.1 Primary state DB

保存 thread 元数据和其他运行状态。当前文件名常量是 `state_5.sqlite`，但版本数字属于实现细节，未来迁移时可能变化。

### 13.2 Thread history DB

保存分页 thread history projection。当前文件名常量是 `thread_history_1.sqlite`。

把分页历史放到独立文件，可以减少它与其他 state 表争用同一个 SQLite 锁。

不要把“所有数据库文件”简单复制成一个长期稳定的用户协议；它们由 migrations 和 StateRuntime 管理。

---

## 14. Primary state DB 的 `threads` 表保存什么

最初的 `threads` 表包含例如：

- thread ID；
- rollout path；
- created/updated time；
- source；
- model provider；
- cwd；
- title；
- sandbox policy；
- approval mode；
- token usage；
- 是否有用户事件；
- archived 状态与时间；
- Git 信息。

后续 migration 又增加了例如：

- 精确到毫秒的时间；
- agent nickname、role 和 path；
- model 和 reasoning effort；
- preview；
- history mode；
- name；
- pinned 状态；
- section 和 section order；
- recency time。

为什么这些字段适合数据库？

因为历史列表经常需要：

```text
查未归档 thread
限定当前 cwd
按最近活动时间倒序
搜索名称或预览
取一页 20 条
```

用 SQL 索引完成这类查询，比打开所有 JSONL 文件逐行扫描高效得多。

---

## 15. Thread history DB 保存什么

分页历史数据库主要包含三类表：

### 15.1 `thread_turns`

保存：

- thread ID；
- turn ID；
- rollout ordinal；
- status 和 error；
- 开始、完成、耗时；
- 首个用户 item ID；
- 最终 Agent item ID；
- 后续 migration 添加的 rollout byte positions。

### 15.2 `thread_items`

保存：

- thread ID；
- turn ID；
- item ID；
- rollout ordinal；
- 创建时间；
- 序列化 item；
- 后续更新位置。

### 15.3 `thread_history_projection_state`

记录 projection 已经处理到 rollout 的哪个 byte offset 和 ordinal。

这让 materializer 可以从上次位置继续，而不是每次从文件开头重新投影全部历史。

---

## 16. Legacy 与 Paginated history mode

当前协议中有两种 `ThreadHistoryMode`：

- `Legacy`；
- `Paginated`。

### Legacy

恢复和读取通常围绕 rollout 中较完整的历史工作。旧客户端或旧文件依赖这条兼容路径。

### Paginated

rollout 仍是耐久记录，但 turns/items 会物化到 thread history DB，客户端可以按页读取，不必一次加载整条长对话。

它还改变部分事件的持久化策略。例如 paginated rollout 会保存 canonical `ItemCompleted` 中的 `TurnItem`，而 legacy 模式仍要保留一些旧式事件以兼容重建。

History mode 是持久化契约，不应仅根据“现在有没有数据库”临时猜测。它被写入 `SessionMeta`，恢复时从 canonical 元数据读取。

---

## 17. Projection 怎样生成

对 paginated thread，live writer 完成 JSONL 写入后，Thread Store 会把已耐久的 rollout 内容 materialize 到 SQLite projection。

简化流程：

```text
append RolloutItems
      │
      ▼
按 persistence policy 过滤
      │
      ▼
写 canonical JSONL
      │
      ▼
从 projection_state 的 byte offset 继续读取
      │
      ▼
把 turn/item 更新写入 thread_history DB
      │
      ▼
推进 next byte offset / ordinal
```

这两个位置很重要：

- byte offset：快速跳到 JSONL 文件中的读取位置；
- ordinal：保持 rollout 项目的逻辑顺序。

如果 projection 落后，JSONL 中可能已经有新记录，但分页查询暂时还没看到；materialization 和 repair 负责追平。

---

## 18. 为什么数据库可以从 rollout 回填

SQLite 可能出现这些情况：

- 第一次升级到带 state DB 的版本；
- 旧 rollout 已存在，但数据库中还没有对应 row；
- rollout 被归档或移动，数据库路径过期；
- 数据库损坏后重建；
- 某次进程退出发生在文件与索引更新之间。

`codex-rs/rollout/src/state_db.rs` 的初始化路径会运行 rollout metadata backfill。它扫描已有持久记录，把缺失元数据填入数据库。

读和 list 路径还会执行 read-repair 或 reconcile：

```text
发现 rollout
   │
   ├── DB 没 row → 从 rollout head 建 row
   ├── DB path 旧 → 校正 path
   ├── metadata 过期 → 重新协调
   └── DB 不可用 → 回退文件系统读取
```

这说明 state DB 更像可重建索引，而不是唯一保存完整对话的地方。

---

## 19. Read 为什么还要验证 rollout path

SQLite row 中可能保存一个 rollout path，但文件后来被移动、删除或被另一个文件占用。

`LocalThreadStore` 在需要 history 时，不会盲目信任路径。它会检查：

1. 文件是否存在；
2. 读取到的 canonical thread ID 是否等于请求的 thread ID；
3. archived 状态是否符合调用参数；
4. history mode 是否允许当前读取方式。

这避免一个严重错误：数据库指向了存在的文件，但那个文件实际属于另一条 thread。

如果 SQLite 路径不能可信地提供历史，代码会尝试按 thread ID 重新解析 rollout 位置。

---

## 20. List Threads 为什么优先数据库又保留文件回退

数据库适合 list，因为可以高效分页、排序和过滤。但仅依赖数据库又会让旧文件、缺 row 或临时不一致的 thread 消失。

当前 rollout listing 路径会综合使用：

- 文件系统扫描；
- state DB page；
- read-repair；
- reconcile；
- 数据库失败时的 fallback。

简化理解：

```text
快速路径：SQLite 索引返回结果
校验路径：文件系统确认或修复关键记录
兼容路径：SQLite 不可用时仍从 rollout 列表读取
```

带复杂 metadata filter 或 search 时，选择哪一页作为最终结果会更细致；学习时先掌握“数据库加速、rollout 兜底、repair 保持一致”这条原则。

---

## 21. Resume 到底恢复了什么

点击 resume 并不是让旧 task 从上次 CPU 指令继续执行。

Cold resume 的简化流程是：

```text
客户端请求 thread/resume(thread_id)
              │
              ▼
app-server 先检查 thread 是否已经加载
              │
       ┌──────┴──────┐
       ▼             ▼
 已在运行          当前未加载
 复用运行对象       ThreadStore read/load history
                         │
                         ▼
                重建 InitialHistory
                         │
                         ▼
                ThreadManager spawn 新运行对象
                         │
                         ▼
                重新建立 Session、channels、tools
```

恢复得到的主要内容包括：

- durable history；
- thread/session 元数据；
- model-visible context 或其可重建来源；
- 保存的 provider、指令、approval policy 等；
- 必要的 world state / compaction 信息。

不会从磁盘恢复的内容包括：

- 原进程的 Tokio task；
- 原 socket 连接；
- 原文件句柄；
- 原 CancellationToken；
- 原 TUI 动画帧；
- 已经不存在的外部子进程控制句柄。

Resume 是“从记录重新构造”，不是“冻结内存后解冻”。

---

## 22. 为什么恢复要处理未正常结束的 Turn

进程可能在这些时刻退出：

- `TurnStarted` 已写入，但 `TurnComplete` 尚未写入；
- tool call 已持久化，但对应 output 不完整；
- 用户中断终态还没 flush；
- 子 Agent 的活动记录只有一部分。

恢复逻辑不能假设最后一轮总是完整。它需要识别终态边界，并对未完成 history 添加或推导 interrupted 语义，避免模型把半截工具调用当成正常完成。

这也是为什么关键终态要进入 rollout，并且中断路径会在返回 UI 前尽量记录 interrupted marker 和 flush。

---

## 23. Fork 与 Resume 的区别

### Resume

- 继续同一个 thread ID；
- 将来的记录追加到同一 thread；
- 目标是延续原任务。

### Fork

- 创建新的 thread ID；
- 继承来源 thread 某个边界以前的历史；
- 之后两条 thread 独立发展；
- 保存 `forked_from_id` 或 history reference 等 lineage 信息。

类比：

```text
Resume：继续写同一本笔记
Fork：复印到某一页，然后拿新本子继续写
```

---

## 24. Copied fork 与 referenced fork

不同 history mode 可以采用不同的 fork 持久化策略。

### 24.1 Copied fork

把选定边界前的来源 history 复制进新 thread 的初始 rollout。

优点：新文件相对独立。

成本：长历史复制会增加 I/O、存储和内存。

### 24.2 Reference-backed fork

Paginated history 可以使用 `HistoryPosition` 引用另一条 thread 的 rollout 前缀：

- source thread ID；
- `end_ordinal_exclusive`；
- `end_byte_offset`。

新 thread 只需保存这个有边界的历史基准和自己的后续内容。

```text
source rollout: [0 ... 1200][1201 ...]
                         ▲
                         └── child 继承到 exclusive boundary

child rollout:  history_base(source, boundary) + child own items
```

这避免复制整段历史，但带来新的生命周期约束：只要 child 仍引用 source，source rollout 就不能随意删除。

---

## 25. 为什么删除来源 thread 可能被拒绝

`codex-rs/thread-store/src/local/delete_thread.rs` 会扫描 rollout reference index。

如果其他 thread 的 `history_base` 仍引用待删除 thread，直接删除来源文件会让 child 无法重建历史。因此 store 会拒绝删除，并报告 forked history 仍在引用。

批量删除时，如果引用者和被引用者都在同一个 deletion set 中，内部引用可以随请求一起消失；但集合外仍存在的引用会阻止删除。

这是典型的引用完整性问题，类似数据库不能删除仍被外键引用的父记录。

---

## 26. Archive、Unarchive 与 Delete 不一样

### Archive

- 停止或卸载相关运行对象；
- 把 rollout 从普通 sessions 位置移动到 archived area；
- 更新 SQLite archived metadata；
- 默认 thread list 不再显示它；
- 数据仍然存在，可以恢复查看。

### Unarchive

- 找到 archived rollout；
- 根据文件日期恢复到 sessions 子目录；
- 更新数据库路径和 archived 状态；
- 返回更新后的 thread metadata。

### Delete

- 是 hard delete；
- 检查是否仍被 fork history 引用；
- 删除 plain/compressed rollout；
- 清理 thread history projection；
- 清理 name index；
- app-server 协调整条 thread 及关联元数据的删除。

所以“归档”是可恢复的组织操作，“删除”是破坏性持久化操作。

---

## 27. ThreadManager 与 ThreadStore 不要混淆

`ThreadManager` 负责当前进程中已加载 thread 的运行生命周期，例如：

- start；
- resume 后 spawn；
- fork；
- 查找当前 live thread；
- 中止和 shutdown；
- 从内存 map 移除。

`ThreadStore` 负责持久化契约，例如：

- 保存和读取 history；
- 列表、搜索和分页；
- archive 和 delete；
- 管理 live writer。

一个 thread 可以：

- 在 Thread Store 中存在，但当前未被 ThreadManager 加载；
- 被 ThreadManager 加载，并同时有 live writer；
- 从 ThreadManager 移除，但持久记录仍保留；
- 被归档后仍能被明确 include-archived 的读取找到。

从内存 manager 移除不等于删除磁盘记录。

---

## 28. 一致性不是“所有地方永远完全相同”

涉及后台写入和多个存储投影时，更实际的一致性模型是：

1. rollout 按顺序记录 canonical durable items；
2. metadata 和 history projection 追随这些记录；
3. 关键边界使用 persist/flush/shutdown 等 acknowledgement；
4. 读取时验证 ID、路径和模式；
5. 发现缺失或过期索引时 backfill/reconcile；
6. 数据库不可用时，对兼容功能回退到 rollout。

因此看到两个存储短暂不同步，不要立刻判断“肯定丢数据”。先问：

- 哪个写入已经获得 ack？
- rollout 是否已 materialized？
- SQLite row 是权威数据还是 projection？
- 当前 read path 是否会 repair？
- 这个功能是否支持无数据库 fallback？

---

## 29. 崩溃时可能留下什么状态

### 情况一：项目只进入 writer channel，尚未 flush

进程突然结束时，这部分内存 pending items 可能没有耐久写出。

### 情况二：JSONL 已写，SQLite metadata 未更新

下次启动的 backfill 或 read-repair 可以从 rollout 恢复索引。

### 情况三：JSONL 某行损坏

读取器逐行解析。它会记录 parse error，并尽可能继续读取其他有效行。不过关键 `SessionMeta` 缺失或文件为空仍会导致无法可靠恢复。

### 情况四：SQLite row 存在，rollout 已丢失

元数据可能仍能出现在某些查询中，但需要 history 时必须验证文件。代码不能用一个过期 row 假装完整历史仍然存在。

### 情况五：归档移动成功，数据库更新失败

文件位置仍能反映 archived 状态，后续查找和 repair 应协调 metadata。

这说明恢复能力来自“可验证的耐久格式 + 索引修复”，而不是假设每次退出都完美。

---

## 30. 为什么不能把 SQLite 当普通缓存直接随便删

从架构上说，部分 thread metadata 可以由 rollout 重建，但当前 StateRuntime 还管理：

- goals；
- memories；
- durable input queue；
- logs；
- thread sections、pin 和排序信息；
- Agent jobs 和其他运行状态。

并非每张表都只是 rollout 的冗余副本。

因此“state DB 是 projection”不能推导出“可以随便删除所有 SQLite 文件”。数据库损坏恢复有专门代码和迁移流程；人工删除可能丢失不能从 rollout 完整重建的状态。

安全结论是：

> 理解哪些表可回填，不等于获得了手工清理整个 SQLite home 的许可。

---

## 31. 用一个端到端例子串起来

用户第一次发送：

> 请修复配置加载测试。

### 创建阶段

1. ThreadManager 生成 thread ID；
2. ThreadStore `create_thread`；
3. LocalThreadStore 创建一个延迟 materialize 的 RolloutRecorder；
4. recorder 预计算 rollout path 和 SessionMeta；
5. 当前进程保存 live recorder。

### 运行阶段

1. TurnStarted、用户消息、tool call、tool result 陆续形成 RolloutItems；
2. persistence policy 筛掉 transient delta；
3. items 进入 bounded writer channel；
4. writer 按 ordinal 追加 JSONL；
5. metadata observer 更新 title、preview、token 等 SQLite 字段；
6. paginated mode 还把 turn/item materialize 到 history DB。

### 正常结束

1. 记录 TurnComplete；
2. flush 确保终态可读取；
3. shutdown drain writer；
4. Session 可以从当前进程卸载，磁盘记录仍保留。

### 第二天恢复

1. UI 通过 thread list 查询 SQLite；
2. 用户选中 thread ID；
3. app-server 调用 ThreadStore 读取 metadata/history；
4. 路径和 canonical ID 被校验；
5. rollout reconstruction 生成 InitialHistory；
6. ThreadManager 创建新的运行对象；
7. TUI 根据 app-server thread/turn projection 重建客户端界面。

这条链把前面几章连在了一起：持久化恢复 Runtime，Runtime 产生事件，事件再恢复 UI 投影。

---

## 32. 常见误解

### 误解一：Rollout 就是模型下一次看到的完整 Prompt

不是。Rollout 是更广的耐久记录；恢复后还要经过 history normalization、compaction 和 Prompt 构造。

### 误解二：State DB 保存了全部原始对话，所以 rollout 可以不要

本地实现明确把 rollout JSONL 当作 durable replay format。数据库主要提供元数据和分页 projection。

### 误解三：`append_items().await` 返回就代表已经 fsync 到磁盘

它首先表示 writer 接受了命令。需要明确耐久边界时使用 persist/flush/shutdown 的 ack 语义；不要自行假设比源码承诺更强的保证。

### 误解四：Resume 会让原来的异步 task 接着运行

不会。它从持久记录重建新的 Session 和 task。

### 误解五：Archive 等于 Delete

Archive 会移动并隐藏记录，仍可 unarchive；Delete 会移除持久数据，并可能受 fork 引用阻止。

### 误解六：SQLite row 存在就证明 rollout history 一定存在

不一定。读取 history 时还会验证文件和 canonical thread ID。

### 误解七：Fork 永远复制完整历史

Legacy 路径可以 copied fork；paginated history 支持 reference-backed fork，只记录有边界的来源引用。

### 误解八：State DB 只是一个文件

StateRuntime 当前管理多份 SQLite 文件，把 logs、goals、memories、queue 和 thread history 等分开。

### 误解九：数据库能回填，所以删除 SQLite home 没风险

错误。不是所有状态都能从 rollout 完整重建，应使用正式恢复和迁移机制。

---

## 33. 故障排查路线

### 33.1 历史列表里找不到 thread

检查：

1. rollout 文件是否存在于 sessions 或 archived_sessions；
2. thread 是否被 archived；
3. state DB backfill 是否完成；
4. list filter 是否限制 cwd、source、provider 或 archived；
5. SQLite row 是否缺失或路径过期；
6. filesystem fallback 是否返回该文件；
7. SessionMeta 中的 thread ID 是否可解析。

### 33.2 列表里有，resume 却失败

检查：

1. SQLite `rollout_path` 是否存在；
2. 文件里的第一个 SessionMeta ID 是否匹配；
3. rollout 是否为空或严重损坏；
4. history mode 是否被当前读取方式支持；
5. archived 参数是否允许；
6. thread 是否已经处于 closing 或 loaded 状态；
7. rollout reconstruction 在哪个 item 失败。

### 33.3 Resume 后缺少最近一轮

检查：

1. 最近项目是否只进入 writer queue；
2. TurnComplete/TurnAborted 是否记录；
3. 中断或关闭路径是否调用 flush；
4. JSONL 尾部是否有 parse error；
5. paginated projection 是否落后于 rollout byte offset；
6. 客户端是否只加载了第一页历史。

### 33.4 无法删除 thread

检查：

1. 是否仍有 live writer；
2. 是否有 child rollout 的 `history_base` 引用它；
3. 是否应删除整个引用子树；
4. plain 和 compressed rollout 是否同时存在；
5. 请求是否只删除 metadata 而漏掉 rollout lifecycle。

---

## 34. 怎样阅读这一部分源码

### 第一遍：只建立职责边界

1. 看 `ThreadStore` trait 有哪些动作；
2. 看 `LocalThreadStore` 顶部注释；
3. 看 `RolloutRecorder` 顶部注释；
4. 看 StateRuntime 管理哪些数据库。

### 第二遍：追一条写入

```text
ThreadStore::append_items
→ local/live_writer.rs
→ persistence policy
→ RolloutRecorder::record_canonical_items
→ RolloutCmd::AddItems
→ rollout_writer
→ JSONL
→ paginated projection
```

### 第三遍：追一次恢复

```text
thread/resume
→ app-server thread_processor
→ ThreadStore read/load_history
→ StoredThreadHistory
→ InitialHistory::Resumed
→ ThreadManager::resume_thread_with_history
→ spawn new runtime Session
```

### 第四遍：研究不一致

从测试搜索这些词：

- `sqlite_state`；
- `rollout_list_find`；
- `load_history`；
- `materialization`；
- `archive` / `unarchive`；
- `reference` / `delete`；
- `resume` / `fork_thread`。

从失败和修复测试反向理解，比只读正常路径更容易掌握持久化设计。

---

## 35. 本章名词表

完整总表见 [课程术语表](glossary.md)。

| 名词 | 代码中的常见写法 | 通俗解释 |
|---|---|---|
| Rollout | rollout JSONL | 按顺序保存、可用于重放和恢复的耐久执行记录 |
| JSONL | `.jsonl` | 每一行都是独立 JSON 对象的文本格式 |
| Rollout line | `RolloutLine` | 带时间、ordinal 和一个 RolloutItem 的文件记录 |
| Rollout item | `RolloutItem` | 可持久化语义项目，例如消息、工具结果或 TurnContext |
| Canonical | canonical history/item | 发生冲突时用于恢复和对账的规范表示 |
| Durable | durable | 进程退出后仍可靠存在并可读取的 |
| Persistence policy | `is_persisted_rollout_item` | 决定哪些运行事件值得进入长期记录的规则 |
| Recorder | `RolloutRecorder` | 接收 items 并通过后台 writer 写入 rollout 的对象 |
| Materialize | `persist()` / materialization | 把只存在于内存或原始记录中的内容实体化到文件或数据库 |
| Pending item | `pending_items` | 已接收但尚未成功耐久写出的记录 |
| Flush | `flush()` | 等待此前排队写入完成并成为可读取的持久内容 |
| Drain | drain pending items | 在关闭前把队列中的剩余工作处理完 |
| Acknowledgement | ack | 接收方完成某个阶段后返回的确认信号 |
| Thread Store | `ThreadStore` | 屏蔽具体存储后端的 thread 持久化接口 |
| Local Thread Store | `LocalThreadStore` | 组合本地 rollout、SQLite 和兼容回退的实现 |
| State DB | `StateRuntime` | 管理 thread 元数据及其他本地运行状态的 SQLite runtime |
| Metadata | `ThreadMetadata` | 用于识别、筛选和展示 thread 的概要字段 |
| Projection | thread history projection | 从 rollout 派生、适合快速查询或分页的数据库视图 |
| Backfill | metadata backfill | 扫描已有 rollout，补建数据库中缺少的记录 |
| Read-repair | read repair | 读取时顺便修复发现的过期或缺失索引 |
| Reconcile | reconcile rollout | 比较 rollout 和数据库并协调成一致状态 |
| Fallback | filesystem fallback | 数据库不可用时改用 rollout 文件读取 |
| Cursor | pagination cursor | 指向下一页起点的不透明分页位置 |
| Ordinal | rollout ordinal | 表示 rollout 项目逻辑顺序的递增编号 |
| Byte offset | byte offset | 指向 JSONL 文件具体字节位置的数值 |
| History mode | `ThreadHistoryMode` | thread 选择 legacy 或 paginated 持久化契约 |
| Cold resume | cold resume | thread 未加载时，从持久记录创建新的运行对象 |
| Live thread | live thread/writer | 当前进程中已加载并可继续追加记录的 thread |
| Copied fork | `ForkPersistence::Copied` | 把来源历史复制进新 thread 的 fork |
| Referenced fork | history reference | 新 thread 通过有边界引用继承来源 rollout 前缀 |
| Lineage | fork lineage | thread 从哪条 thread 分叉而来的关系 |
| Reference integrity | 引用完整性 | 防止被引用来源提前删除而破坏 child history |
| Archive | `archive_thread` | 移动并隐藏持久记录，但保留以后恢复能力 |
| Hard delete | `delete_thread` | 删除 rollout 和相关索引的不可逆操作 |
| Migration | SQL/rollout migration | 把旧持久格式升级到当前 schema 或历史契约 |

### 代码单词和短语拆解

- `rollout`：展开过程；在这里指一次任务逐步形成的持久轨迹。
- `store`：存储层或统一存储接口。
- `recorder`：记录器。
- `persist`：使内容在进程退出后仍保留。
- `durable`：耐久的、可跨进程存在的。
- `append`：追加到末尾，不重写前面内容。
- `flush`：把已排队数据写出并等待完成。
- `drain`：把队列剩余内容依次处理完。
- `materialize`：把延迟或派生表示变成真实可读取数据。
- `metadata`：描述数据的数据，例如名称、时间和路径。
- `projection`：为查询目的从原始数据派生出的视图。
- `backfill`：为旧数据补建后来新增的索引或字段。
- `reconcile`：比较不同来源并协调差异。
- `fallback`：主要路径不可用时的备用路径。
- `legacy`：为旧版本或旧格式保留的兼容模式。
- `paginated`：按页读取的，而不是一次加载全部。
- `ordinal`：顺序编号。
- `offset`：相对文件开头的位置偏移。
- `resume`：继续原 thread。
- `fork`：继承一段历史后创建新 thread。
- `lineage`：来源和后代关系。
- `archive` / `unarchive`：归档 / 取消归档。
- `migration`：持久数据结构升级。
- `repair`：从可信来源修复缺失或过期数据。

---

## 36. 自测题

1. Rollout、State DB、Thread Store 和 ThreadManager 分别是什么？
2. 为什么 rollout 不保存每一个 delta 和 UI 状态变化？
3. JSONL 为什么适合顺序追加的执行记录？
4. `SessionMeta` 中为什么要保存 history mode？
5. 为什么新 RolloutRecorder 创建后，文件可能还不存在？
6. Add、Persist、Flush、Shutdown 和 Discard 的保证有什么区别？
7. StateRuntime 为什么把 thread history 和 logs 放在独立数据库文件？
8. Primary state DB 与 thread history DB 各自主要保存什么？
9. 为什么说 SQLite 是查询投影，但又不能随便删除整个 SQLite home？
10. Backfill、read-repair 和 fallback 分别在什么情况下使用？
11. Cold resume 为什么不是恢复旧 Tokio task？
12. Resume 与 fork 在 thread ID 和后续记录上有什么区别？
13. Reference-backed fork 为什么需要 byte offset 和 ordinal？
14. 为什么仍被 child 引用的 source thread 不能直接删除？
15. Archive 和 hard delete 的恢复能力有什么区别？

---

## 37. 源码检查点

建议按以下顺序对照：

1. `codex-rs/protocol/src/protocol.rs`
   - 找 `ThreadHistoryMode`、`HistoryPosition`、`SessionMetaLine`、`RolloutItem` 和 `RolloutLine`。
2. `codex-rs/rollout/src/lib.rs`
   - 找 `SESSIONS_SUBDIR` 和 `ARCHIVED_SESSIONS_SUBDIR`；
   - 看 rollout crate 对外暴露哪些读取、列表、压缩和 recorder 能力。
3. `codex-rs/rollout/src/policy.rs`
   - 对比 durable 与 transient `EventMsg`；
   - 思考为什么 delta 不进入 rollout。
4. `codex-rs/rollout/src/recorder.rs`
   - 找 `RolloutRecorder`、`RolloutCmd`、`new`、`persist`、`flush`、`shutdown` 和 `rollout_writer`；
   - 观察新 thread 的 lazy materialization。
5. `codex-rs/thread-store/src/store.rs`
   - 按创建、写入、读取、查询、fork 和生命周期给 trait 方法分组。
6. `codex-rs/thread-store/src/local/mod.rs`
   - 阅读 `LocalThreadStore` 顶部职责说明；
   - 找 live recorders、writer locks、state DB 和 history DB。
7. `codex-rs/thread-store/src/local/live_writer.rs`
   - 追 `append_items`、`persist_thread`、`flush_thread` 和 `shutdown_thread`。
8. `codex-rs/thread-store/src/local/read_thread.rs`
   - 看 SQLite metadata、rollout fallback 和 canonical ID 校验怎样配合。
9. `codex-rs/thread-store/src/local/list_threads.rs`
   - 看数据库 page、filesystem scan、repair 与 fallback。
10. `codex-rs/thread-store/src/local/thread_history_materialization.rs`
    - 观察 paginated rollout 怎样推进 SQLite projection。
11. `codex-rs/state/src/sqlite.rs`
    - 找不同 runtime DB 的文件和打开路径。
12. `codex-rs/state/migrations/0001_threads.sql`
    - 看最初 thread metadata schema。
13. `codex-rs/state/thread_history_migrations/0001_thread_history.sql`
    - 看 turns、items 和 projection state 三组表。
14. `codex-rs/rollout/src/state_db.rs`
    - 找初始化、backfill gate 和数据库 fallback。
15. `codex-rs/core/src/thread_manager.rs`
    - 找 resume、fork、remove 和 shutdown；
    - 分清内存生命周期与持久记录。
16. `codex-rs/app-server/src/request_processors/thread_processor.rs`
    - 从 `thread_resume_inner` 观察 running resume 与 cold resume 分支。
17. `codex-rs/thread-store/src/local/delete_thread.rs`
    - 看 reference index 为什么阻止危险删除。
18. `codex-rs/core/tests/suite/sqlite_state.rs`、`rollout_list_find.rs` 和 `resume.rs`
    - 用集成测试验证索引、回退和恢复行为。

---

## 38. 一句话总结

本地 Codex 用 rollout JSONL 保存可验证、可重放的耐久轨迹，用多份 SQLite 数据库提供快速元数据查询和分页历史投影，再由 `ThreadStore` 统一写入、恢复、修复、归档和 fork；Resume 的本质，是从这些持久记录构造一个新的运行 Session，而不是复活旧进程内存。
