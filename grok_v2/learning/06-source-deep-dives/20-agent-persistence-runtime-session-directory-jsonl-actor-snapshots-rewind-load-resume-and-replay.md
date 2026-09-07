# 源码精读 20：Agent Persistence Runtime 如何写盘、恢复、回放与回退一个 Session

> 本篇把 Prompt、Agentic Loop、Chat State、Compaction、Tool、配置与可观测性重新放到“进程可能随时退出”的条件下观察：一次会话究竟被拆成哪些文件，谁决定写入顺序，哪些数据是事件日志、哪些是当前快照，`session/load` 怎样把它们恢复成可继续工作的 Agent，以及 Rewind 为什么既不能简单删除历史，也不能只截断内存数组。

## 0. 本文回答什么

1. Session 为什么是一个目录，而不是一个 JSON 文件；
2. `summary.json`、`updates.jsonl` 与 `chat_history.jsonl` 分别是什么真相；
3. Plan、Signals、Goal、Workflow 为什么使用独立 sidecar；
4. `PersistenceHandle` 与 `SessionPersistence` Actor 怎样串行协调并发写请求；
5. ACP 文本 chunk 为什么合并，什么时候必须 flush；
6. buffered append、durable append、atomic replace 分别保证什么；
7. `NotCommitted`、`Committed` 与 `AcknowledgementLost` 为什么必须区分；
8. `load`、`load_light`、resume、reconnect 与 replay 有什么区别；
9. Rewind 怎样同时处理 Conversation、文件与 append-only timeline；
10. Compaction、Remote、Fork、Subagent 与 Workflow 给恢复增加了什么约束。

源码基线：

```text
ed6d543
```

---

## 1. 先给出核心结论

Grok Build 没有把 Session 当成一个可整体序列化的 Rust struct，而是使用一组互补的数据结构：

```text
Session Directory
  ├── identity / metadata snapshot
  ├── model-visible conversation snapshot
  ├── client-visible protocol event log
  ├── file rollback log
  ├── runtime state snapshots
  └── diagnostic / compaction / workflow artifacts
```

最重要的阅读模型是：

> `chat_history.jsonl` 回答“模型下一次请求应看见什么”，`updates.jsonl` 回答“客户端历史上看见了什么”，各类 JSON sidecar 回答“某个状态机现在处于什么状态”。

三个问题相近，却不能由同一份数据低成本、无歧义地回答。

---

## 2. 核心源码地图

```text
crates/codegen/xai-grok-shell/src/session/persistence.rs
  PersistenceMsg / PersistenceHandle / SessionPersistence
  Summary / PersistedInfo / PersistedInfoLight
  new / load / load_light

crates/codegen/xai-grok-shell/src/session/storage/mod.rs
  StorageAdapter / SessionUpdate / SessionUpdateEnvelope
  prepare_replay_lines

crates/codegen/xai-grok-shell/src/session/storage/jsonl/mod.rs
  JsonlStorageAdapter / AppendDurability

crates/codegen/xai-chat-state/src/persistence.rs
crates/codegen/xai-chat-state/src/actor/{mod,state,mutations}.rs

crates/codegen/xai-grok-shell/src/session/chat_persistence.rs
crates/codegen/xai-grok-shell/src/session/replay_events.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/rewind.rs
crates/codegen/xai-grok-shell/src/agent/mvp_agent/session_setup.rs

crates/codegen/xai-grok-shell/src/session/{signals,plan_mode,goal_tracker}.rs
crates/codegen/xai-grok-shell/src/session/workflow/store.rs
```

---

## 3. Session 目录布局

默认 JSONL 后端把目录解析为：

```text
{grok_home}/sessions/{url_encoded_cwd}/{session_id}/
```

典型内容：

```text
summary.json
updates.jsonl
chat_history.jsonl
plan.json
plan_mode.json
signals.json
announcement_state.json
goal/state.json
rewind_points.jsonl
feedback.jsonl
btw_history.jsonl
plan.md
workflows/{run_id}/...
compaction_checkpoints/{checkpoint_id}.json
compaction_requests/{request_id}.json
recap_requests/{request_id}.json
compaction/segment_NNN.md
```

不是每个 Session 都有所有文件。Sidecar 通常“用到才创建”，加载时不存在一般表示旧会话或功能从未启用，而不一定是损坏。

---

## 4. 按语义而不是扩展名分类

| 类别 | 代表文件 | 写入模式 | 回答的问题 |
| --- | --- | --- | --- |
| 元数据快照 | `summary.json` | patch + atomic replace | 这是谁、在哪、最近使用什么模型 |
| Conversation 快照 | `chat_history.jsonl` | append；压缩/回退时 replace | 模型下一轮看见什么 |
| 协议事件日志 | `updates.jsonl` | append-only | 客户端曾看见哪些更新 |
| 文件回退日志 | `rewind_points.jsonl` | append；回退时 rewrite | 每轮前后文件是什么 |
| 状态机快照 | `signals.json` 等 | atomic replace | 当前状态是什么 |
| Artifact | checkpoint、request、segment | 独立文件 | 如何审计、重建或分析 |

`chat_history.jsonl` 虽然使用 JSONL 编码，却经常被整体重写，因此它更接近“数组快照的流式编码”，不是完整 event sourcing。

---

## 5. `summary.json`：身份与索引入口

`Summary` 保存：

- Session ID、cwd、创建/更新时间、消息计数；
- 当前 model、agent name、reasoning effort、sandbox profile；
- 标题与手工重命名标记；
- parent、fork kind、继承前缀、display cwd；
- Git root、remote、HEAD；
- collection ID、next trace turn；
- cwd relocation generation。

列表页以 `summary.json` 为入口，可避免只有临时文件的残缺目录出现在历史记录中。它只保存适合快速枚举、定位和恢复配置的字段，避免每个 token chunk 都重写巨大对象。

---

## 6. `updates.jsonl`：客户端可见事件时间线

统一类型：

```rust
enum SessionUpdate {
    Acp(Box<acp::SessionNotification>),
    Xai(Box<SessionNotification>),
}
```

落盘时包成 `SessionUpdateEnvelope`：

```text
timestamp
method = "session/update" | "_x.ai/session/update"
params
```

ACP 与 xAI 扩展共享文件，因为它们共同构成客户端看到的总顺序；typed variant 则保留合并、过滤和 replay 行为差异。读取器兼容没有 envelope 的旧 ACP 行。

---

## 7. `chat_history.jsonl`：模型可见 Conversation

Chat State 持有 `Vec<ConversationItem>`，通过 `ChatPersistence` 写入历史。

```text
updates.jsonl       面向客户端协议，流式且细粒度
chat_history.jsonl  面向下一次模型请求，规范化且完整
```

一个 Assistant 回答在 Updates 中可能是几十个 text/thought/tool chunk，在 Chat History 中是最终 canonical Assistant item 加 ToolResult。

`CHAT_FORMAT_VERSION = 1` 使用 `ConversationItem`；v0 是旧 `ChatRequestMessage`。加载时先看 Summary 中的版本，而不是永远按当前 struct 猜测解析。

---

## 8. 一次消息为什么走两条持久化通路

```text
Sampler stream
  ├── ACP AgentMessageChunk
  │     └── updates.jsonl（客户端 replay）
  │
  └── canonical final ConversationItem::Assistant
        └── ChatState
              └── chat_history.jsonl（下一轮模型输入）
```

这是为不同消费者维护两个投影，不是无意义重复。

---

## 9. `ChatPersistence` 的所有权设计

`ChatPersistence` 被 Chat State Actor 独占，方法使用 `&mut self`：

```text
ChatState Actor
  └── Box<dyn ChatPersistence>
        └── ChannelChatPersistence
              └── PersistenceMsg
```

因此 Conversation 变更与“请求写盘”在同一个串行边界发生，不必再给 adapter 加锁。`ChannelChatPersistence` 只是桥，真正 I/O 由 Session Persistence Actor 完成。

---

## 10. `PersistenceMsg`：写盘意图的命令集

按职责可分为：

```text
日志追加：Update / DurableUpdateAndAck / Chat / Feedback / Btw / RewindPoint
快照替换：ReplaceChatHistory / Plan / PlanMode / Signals / Goal / Workflow
元数据 Patch：CurrentModel / CollectionId / NextTraceTurn / GitHead / Title
Barrier：Flush / FlushAndAck / CopyFile / CwdSwitchAndAck
时间线：TruncateRewindPoints / MergeRewindPoints / Compaction artifacts
同步控制：UpgradeToWriteback
```

调用者只拿到 `PersistenceHandle` 的 sender，不能随意打开文件。这同时形成顺序、API 与测试边界。

`noop()` 可以吸收普通 fire-and-forget 请求，但 durable append 返回 `Unsupported`，不能假装已经耐久。

---

## 11. `SessionPersistence` Actor 真正负责什么

它持有 StorageAdapter、pending ACP notification、remote/relay sync、标题生成器和 gateway，因此同时是：

```text
ordering coordinator
chunk coalescer
durability barrier owner
local/remote fan-out point
summary generation trigger
archive flush point
```

Unbounded channel 让高频流式路径不必逐 chunk 等磁盘，但积压风险由 chunk merge、合理事件粒度和 barrier 控制；它不是“天然没有背压”。

---

## 12. ACP 文本 chunk 的第二层合并

连续的以下更新可以合并：

```text
AgentMessageChunk(Text)
AgentThoughtChunk(Text)
```

双方必须没有 annotations/meta 且类型相同。空文本且无 meta 的 chunk 丢弃。

Actor 保存一个 pending item：

```text
incoming A -> pending A
incoming B compatible -> pending A+B
incoming C incompatible -> write A+B, pending C
Flush/channel close -> write C
```

因此 Copy、Durable Append 与关闭路径必须先 drain pending，否则最后一个 chunk 仍只在内存。

---

## 13. 三种 Flush/Sync 边界

| 操作 | 写出 pending | caller 等确认 | sync 文件 |
| --- | --- | --- | --- |
| `Flush` | 是 | 否 | 否 |
| `FlushAndAck` | 是 | 是 | 否 |
| `flush_and_sync` | 是 | 内部等待 | 是 |

`CopyFile` 复制目录前使用 `flush_and_sync`，避免 archive 缺最后一块，或复制到尚未同步的内容。

---

## 14. Buffered、Durable 与 Atomic

### Buffered append

追加完整 JSON 行，适合高频日志，不承诺断电后已进入稳定介质。

### Durable append

追加后执行耐久同步；macOS 路径使用 `F_FULLFSYNC`，其他 Unix/Windows 使用 `sync_all`。

### Atomic replace

写同目录唯一临时文件，再 rename 覆盖目标。适合 `signals.json`、`plan_mode.json` 等最新快照，可避免读到半个 JSON。

必须区分：

```text
atomicity  = 读者看见完整旧版或完整新版
durability = 成功返回后崩溃/断电仍尽量保住数据
```

---

## 15. JSONL 尾部修复

Append 不是天然 crash-atomic。进程可能在一行写到一半时退出，或 ENOSPC 形成 torn tail。

Jsonl adapter 在下次追加前修复不完整尾部；Replay 读取则 best-effort 跳过无法解析的行：

```text
write path -> heal before append
read path  -> tolerate corrupt tail
```

---

## 16. Commit-aware Error

写入失败可发生在记录写入前、写入后 bookkeeping 时，或 caller 等 ack 时。源码保留三种语义：

| 错误 | 已知提交？ | 直接重试？ |
| --- | --- | --- |
| `NotCommitted` | 否 | 通常安全 |
| `Committed` | 是 | 会重复，不能盲重试 |
| `AcknowledgementLost` | 未知 | 必须按不确定提交处理 |

Durable append 必须先 drain pending：若 A 仍 pending，却先 durable B，磁盘顺序会错误地成为 B→A。Barrier 既是耐久边界，也是顺序边界。

---

## 17. 本地提交与远端同步顺序

ACP update 只有在本地 append 成功，或返回“已 committed 但后续失败”时才 queue 到 remote/relay。

若是 `NotCommitted`：

- 恢复 pending；
- 不发 remote；
- 避免远端出现本地没有的事件。

这不是跨本地与远端的原子事务，而是明确的“本地日志先行”策略。

---

## 18. Summary Patch 与竞态

标题、模型、计数、Git HEAD 与 trace turn 可能并发更新。若各自 read-modify-write 整个 Summary，会互相覆盖。

JSONL adapter 使用 summary lock + typed patch，在锁内读取、修改并原子替换。自动标题使用 `set_generated_title_if_absent`，确保与手工 `/rename` 竞态时手工标题获胜。

---

## 19. Cwd Switch 为什么严格确认

工作目录切换牵涉 authoritative cwd、generation、previous cwd、reminder、chat bookkeeping 和目录 relocation。

`append_cwd_switch_commit_aware` 返回 `StrictAppendAck`，用 generation 做幂等识别。Actor 在确认前消失时，调用者得到不确定状态，而不是假成功。

---

## 20. 状态机为什么用 Sidecar Snapshot

```text
TodoState           -> plan.json
PlanModeSnapshot    -> plan_mode.json
SessionSignals      -> signals.json
AnnouncementState   -> announcement_state.json
GoalOrchestration   -> goal/state.json
WorkflowManifest    -> workflows/{id}/state.json
```

它们关心最新状态，不必回放每个内部字段变化。独立文件也允许 fork 时选择复制或清除。

---

## 21. Plan、Plan File 与 Plan Mode

```text
plan.json       Todo tool 条目
plan.md         Plan Mode 允许模型编辑的正文
plan_mode.json  Plan Mode 生命周期状态
```

`PlanModeSnapshot` 保存 state、是否曾 active、reminder count、pending exit reminder 和 awaiting approval；路径不保存，因为能从新机器的 Session 目录重算。

恢复不是机械反序列化：

```text
Pending     -> Inactive
ExitPending -> Inactive + pending_exit_reminder
```

这些状态依赖旧进程的 in-flight turn，重启后不能原样继续。这揭示一般规则：stable state 可保存，transient state 必须在 restore constructor 中迁移。

---

## 22. Signals、Announcement 与 Goal

`SessionSignals` 保存累计 turn、消息、错误、工具失败、取消、压缩、LOC、Git/PR 等事实。Signals 自己也是 Actor；Turn 结束的 `TakeTurnEndSnapshot` 同时产出累计值和本轮 delta，再持久化累计快照。

`AnnouncementState` 保存能力公告 fingerprint，用于避免 reconnect 重复刷屏，同时识别配置变化后的新公告。

`GoalOrchestration` 保存的不只是 objective，还包括 phase、status、budget、planner/verifier 进度和 pause reason。删除 Goal 使用 ack，避免 UI 已退出但磁盘状态下次又复活。

---

## 23. Workflow 为什么是子目录

每个 run 需要：

```text
state.json
args.json
script.rhai
scripts/{revision}.rhai
cleared tombstone
```

`WorkflowRunManifest` 包含版本、运行状态与脚本 revision。恢复时还有安全约束：

- 验证 run ID，阻止路径穿越；
- 限制 manifest、args 大小和 restored run 数量；
- 旧版本缺 Agent budget accounting 时标为 `Interrupted`，不安全续跑；
- cleared tombstone 防止残留 state 复活。

---

## 24. `TurnCapture` 不是 Session 恢复文件

Chat State 的 `TurnCapture` 只保存本轮追加的 `ConversationItem` 和本轮是否发生 Compaction，服务 turn trace/upload。

开始一轮时记录 Conversation 长度，结束时 bulk clone 尾部，避免每次 append 都复制。

Compaction 或 snapshot restore 会中途替换 Vec，因此 `TurnCaptureState` 还保存：

```text
turn_start_offset
pre_replacement_messages
compaction_occurred
```

替换前收走旧尾部，替换后 rebase offset；Take 时拼接两段。它是短期 capture，不是长期 Session persistence。

Shell 的 `StreamingTurnCapture` 又是另一份流式 reasoning/text 与诊断 artifact，也不能和 Chat History 混淆。

---

## 25. 新 Session 创建路径

```text
persistence::new
  -> JsonlStorageAdapter
  -> init_session，创建目录与 summary
  -> 更新 model（若不同）
  -> actor_channel
  -> init remote sync（可选）
  -> spawn SessionPersistence::run
  -> 返回 PersistenceHandle
```

`created_fresh=true` 用于之后从 Local 升级 Writeback 时判断能否 backfill 历史。

Subagent 的 `new_with_explicit_dir` 直接写 `{parent}/subagents/{id}`，并关闭 cloud sync、relay 与 gateway lifecycle，因为 child 由 coordinator 管理。

---

## 26. `load` 与 `load_light`

| API | Updates | Rewind Points | 用途 |
| --- | --- | --- | --- |
| `load` | 全量 Vec | 全量 Vec | 需要完整内存数据的专用路径 |
| `load_light` | 返回文件路径 | 返回文件路径 | 正常 resume |

Light load 仍读取 Summary、Chat History、Plan/Plan Mode、Signals、Announcement、Goal 与 Workflow。

Chat History 很快要用于下一次模型请求，必须在内存；Updates 主要供 UI replay，可按行处理；含完整文件内容的 rewind points 只在真正 rewind 时 lazy materialize。

---

## 27. `session/load` 总流程

```text
若 resident：先 flush，暂时关闭 live gateway
  -> load_light
  -> 读取 snapshots
  -> 恢复 telemetry turn counter
  -> 可选恢复 Git code
  -> replay_transcript_gate
  -> 若非 resident：spawn SessionActor
       ├── Chat History
       ├── lazy rewind path
       ├── Signals / Plan Mode / Goal
       ├── Workflow / Announcement
       └── persisted model + agent
  -> 恢复命令目录、模型、pending approval
  -> heal orphaned subagents
  -> 返回 LoadSessionResponse
```

Resume/cold load 需要新建 Actor；reconnect 的 Actor 仍 resident，主要工作是 flush、重放客户端缺失历史和更新连接能力。

---

## 28. Replay Gate 解决顺序竞态

若历史还没发完，Actor 就发送 live update，客户端可能看到 old1、old2、new、old3。

Load path 因此建立 gate：先准备并发送历史，等待 gateway completion，再开放 live 流量，并补发起点之后新追加的 delta。Replay 是一套 notification ordering 协议，不只是读文件循环 send。

---

## 29. `prepare_replay_lines`

它对原始 Updates 做：

1. 去空行；
2. 应用 RewindMarker，得到当前活跃时间线；
3. 扫描最大 `eventId`，重新播种进程全局 counter；
4. 取得最后一个 `totalTokens`；
5. 解析 reconnect cursor；
6. 丢弃冗余 Available Commands Update；
7. 收集 post-cursor tail；
8. 找出 spawned 但未 finished 的 subagent。

返回的行借用原始 String，避免先为每行分配 owned String。

---

## 30. Replay 恢复的是谁

在正常 `session/load` 中，Updates replay 主要恢复客户端 scrollback/UI，而不是重建 Chat State 的模型上下文；后者来自 Chat History。

只有跨 Compaction rewind 等特殊场景，才用 update log + checkpoint 重建历史版本 Conversation。

---

## 31. Full Replay 与 Cursor Replay

```text
无 cursor / 找不到 -> full replay，标记 isReplay
cursor 找到        -> 只发之后的 tail
```

若 cursor 后有会被转发、却没有 eventId 的行，则退回 full replay，因为未来 cursor 无法覆盖或去重它。

历史 Available Commands Update 最终会跳过，因为 load 后会按当前配置重发完整目录；但 cursor 查找必须先包含它，因为客户端的最后 cursor 可能正来自该事件。

恢复时还用最大 event counter 调用 `ensure_event_counter_at_least(max+1)`，防止新进程从 0 开始生成重复 ID。

---

## 32. Rewind 用 Marker 表达时间线分叉

Rewind 后不删除 `updates.jsonl` 尾部，而是 append xAI `RewindMarker { target_prompt_index, created_at }`。

Replay filter 根据 marker 截断逻辑时间线，之后的新事件形成新分支。这样：

- 保持 append-only；
- 保留审计事实；
- 避免原地截断大文件；
- 可顺序处理多次 rewind。

---

## 33. 三种 Rewind Mode

```text
All              Conversation + Files
ConversationOnly Conversation only
FilesOnly        Files only
```

`force=false` 只 preview：计算哪些文件会恢复，以及当前磁盘是否被外部修改。`force=true` 才执行。

冲突判断使用 Session snapshot：earliest before 是目标内容，latest after 是 Agent 最后留下的内容，当前磁盘与 latest after 不同则标记 created/deleted/modified externally。

---

## 34. 普通 Conversation Rewind

未发生 Compaction 时：

1. 取 Chat State Conversation；
2. 计算“prompt N 执行前”的 cut；
3. truncate；
4. replace Conversation，同时重写 Chat History；
5. 修正 prompt index/texts；
6. 清除预算型 compaction suppression；
7. append RewindMarker。

目标 N 的语义是保留 prompts 0..N-1。

---

## 35. Compaction 后为什么必须 Replay

Compaction 把许多 turn 折叠成少量摘要 item，此时 `prompt_index` 不等于当前 Conversation 中 User item 的简单计数。任何按第 N 个 User 截断的方案都可能切错。

一旦历史发生过 Compaction，rewind 统一调用 `replay_to_prompt`，结合：

```text
updates.jsonl
compaction checkpoint
target prompt index
```

Checkpoint 存活时恢复 compacted history 并 replay 后续事件；目标更早时累计原始更新并补 System/User preamble。Checkpoint 缺失则明确失败，不回退到危险的简单 truncate。

---

## 36. Rewind Point 后处理

All/FilesOnly 已回退文件，未来 snapshots 失效：

```text
FileStateTracker.truncate_from(target)
PersistenceMsg::TruncateRewindPoints
```

ConversationOnly 没回退文件，却回到了旧对话。目标后的文件效果必须 merge 到前一 point，保证未来 `/rewind 0` 仍能撤销全部文件变化：

```text
merge_and_remove_from(target)
PersistenceMsg::MergeRewindPointsFrom
```

磁盘 merge 重新读取完整权威文件，不依赖可能只 lazy-load 一部分的内存 tracker。

---

## 37. Remote Pull 与本地 Child

Load 先查本地。只有错误是 `NotFound` 且有 backend client，才 remote pull、hydrate 本地文件，再从本地 adapter 加载。权限、损坏等错误不会伪装成 miss。

远端 Session 的原 cwd 在本机可能无效，因此恢复可创建带 `parent_session_id` 的本地 child。再次解析同一个 parent 时选择最近更新的 child，并稳定 tie-break，避免重复复制。

---

## 38. Local 升级 Writeback

配置可能晚于 Session 创建。`UpgradeToWriteback`：

1. flush pending；
2. 重新 load 本地历史；
3. 初始化 RemoteSync；
4. fresh Session backfill 已有 ACP updates；
5. resumed Session 只 forward 新事件。

Resumed 不 backfill，因为远端可能已有旧历史，而 backend 按内容追加、缺少逐消息幂等 ID，重发会重复。ZDR team 与 Subagent 默认跳过对应云同步。

---

## 39. 恢复 Model、Agent 与 Sandbox

Summary 保存 `current_model_id`、`agent_name`、`reasoning_effort`、`sandbox_profile`。

尤其不能每次仅从当前 model catalog 推导 agent name：catalog 可能升级、模型删除或映射变化。恢复时优先持久化名称，旧 Session 缺失才 fallback 推导，尽量保持原 harness 语义。

---

## 40. 哪些东西不会原样恢复

- Tokio task、channel receiver 与 mutex guard；
- in-flight HTTP/SSE；
- 正在等待的 oneshot；
- Tool 子进程句柄；
- Plan Mode pending activation buffer；
- gateway completion；
- 当前 MCP 连接；
- 当前客户端 capabilities。

恢复的是可序列化事实和可重建配置，不是旧进程的执行栈。

---

## 41. 恢复边界的完整性修复

进程可能在 Assistant 写入 Tool Call、ToolResult 尚未落入 Chat History 时退出。

`ChatState::new` 会去重 ToolResult，并修复悬空 Tool Call，避免下一次模型请求携带违反 provider schema 的历史。

Replay 还在 rewind-filtered 时间线里配对 `SubagentSpawned`/`SubagentFinished`；剩余项交给 `heal_orphaned_subagents`，不能假设旧内存 coordinator 仍存在。

---

## 42. Fork 不是 `cp -R`

Fork 需要：

- transform Session ID；
- 可选截断到 prompt index；
- 改 cwd 或保留 display cwd；
- 写 parent/fork metadata；
- 选择是否复制 Plan Mode 等 sidecar；
- 过滤 Updates 与 checkpoints；
- 转换 Conversation 路径；
- 设置 inherited prefix 与 model override。

`CopySessionOptions` 把这些语义显式化，防止得到文件齐全但内部身份不一致的 child。

---

## 43. “权威”取决于查询

| 问题 | 首选事实 |
| --- | --- |
| Session 身份、模型与位置 | `summary.json` |
| 下一次模型输入 | `chat_history.jsonl` |
| 客户端 scrollback | rewind-filtered `updates.jsonl` |
| 当前 Plan/Goal/Workflow | 对应 sidecar + restore migration |
| 文件回退 | `rewind_points.jsonl` + 当前磁盘 |
| 跨 Compaction 历史 | Updates + checkpoint |
| 本轮 trace messages | TurnCapture artifact |

不存在一份对所有问题都绝对权威的万能文件。

---

## 44. 五个常见误读

### 44.1 “Updates 足以替代 Chat History”

从 chunk 推导 canonical Conversation 必须处理重试、取消、tool、rewind、compaction 和兼容格式，热路径太昂贵且脆弱。

### 44.2 “Snapshot 总比 Log 可靠”

Snapshot 擅长当前状态，Log 擅长过程与顺序。选择取决于查询和写入频率。

### 44.3 “Actor 让所有文件成为事务”

Actor 只保证 mailbox 顺序。Update 可能已提交而 Summary bookkeeping 失败，因此仍需要 `Committed(error)`。

### 44.4 “Flush Ack 等于稳定落盘”

普通 Ack 主要表示 Actor 越过命令；强耐久需要 durable append，归档前需要 flush + sync。

### 44.5 “Resume 等于 Reconnect”

Resume 需要从文件 spawn 新 Actor；Reconnect 的 Actor 仍 resident，主要恢复客户端连接与缺失事件。

---

## 45. 正常 Turn 的持久化时间线

```text
Begin turn
  -> append User ConversationItem to Chat History
  -> emit UserMessageChunk to Updates
  -> begin TurnCapture

Sampling
  -> merge/persist text and thought chunks
  -> persist tool notifications

Tool execution
  -> append canonical Assistant / ToolResult
  -> append RewindPoint
  -> update Signals

Turn convergence
  -> final Assistant ConversationItem
  -> take TurnCapture / StreamingCapture
  -> snapshot changed state machines
  -> execute required barriers
```

---

## 46. 冷恢复时间线

```text
resolve Info
  -> local load_light
  -> optional remote pull on NotFound
  -> repair/read Chat History
  -> read runtime sidecars
  -> prepare rewind/cursor-filtered replay
  -> forward history and await completions
  -> spawn SessionActor
  -> wire lazy Rewind Points
  -> restore model/agent/commands/approval
  -> open live gate
  -> heal orphaned work
```

---

## 47. Rewind 时间线

```text
mark reverted signal
  -> validate target
  -> build file preview/conflicts
  -> if !force: return preview
  -> restore/delete files by mode
  -> truncate or replay Conversation
  -> replace Chat History
  -> repair prompt index/texts
  -> append RewindMarker
  -> truncate/merge Rewind Points
```

---

## 48. 测试真正应固定的不变量

1. legacy 与 envelope update 都能读；
2. torn tail 不污染下次 append；
3. committed failure 不会恢复成未提交 pending；
4. durable append 先 drain merge buffer；
5. cursor tail 有 id-less 行时回退 full replay；
6. ACU 被跳过但仍能作为 cursor；
7. 多次 Rewind 得到正确 live timeline；
8. cross-compaction rewind 必须使用 checkpoint；
9. ConversationOnly merge 不丢历史 file snapshots；
10. TurnCapture 在 replacement/repair 后仍完整；
11. Plan transient state 恢复时被折叠；
12. Workflow 旧版本不会不安全续跑；
13. title race 中 manual rename 获胜；
14. resumed Writeback 不重复 backfill。

---

## 49. 排障：UI 历史缺失但模型记得

通常是 Chat History 正常，而 Updates replay/gateway/cursor 异常。检查：

- Updates 是否存在；
- cursor 是否误定位；
- RewindMarker 是否过滤了事件；
- eventId 是否缺失或重复；
- replay completion 是否 drain；
- 非 ACU 行是否被错误跳过。

---

## 50. 排障：UI 看得见但模型失忆

通常是 Updates 正常，而 Chat History 缺失、损坏或被错误 replace。检查：

- `chat_format_version`；
- rebuild 是否触发；
- Compaction replacement；
- dangling Tool Call repair；
- fork inherited prefix；
- Rewind 是否重写 Chat History。

---

## 51. 排障：Plan/Goal/Workflow 恢复错误

分别检查：

```text
plan_mode.json
goal/state.json
workflows/{run_id}/state.json
workflows/{run_id}/cleared
```

确认版本、transient-state migration、delete ack、workflow source revision/args，以及 spawn 是否真正接收 persisted state。只看 Updates 无法诊断 sidecar 状态机。

---

## 52. 排障：Rewind 后历史与文件错位

检查：

1. target 是否理解为“prompt N 之前”；
2. `last_compaction_prompt_index` 是否存在；
3. checkpoint 是否还在；
4. Rewind Points 应 truncate 还是 merge；
5. Updates 末尾的 RewindMarker 顺序。

---

## 53. 新增持久化状态的设计清单

1. 这是历史事件还是当前快照？
2. 消费者是模型、客户端、控制器还是诊断？
3. 写失败后能否安全重试？
4. 是否需要 ack 或 durable barrier？
5. 旧 Session 缺文件时如何默认？
6. transient state 如何迁移？
7. fork/subagent 是否复制？
8. rewind/compaction 是否改变？
9. remote sync 是否包含？
10. 文件数量、大小、路径如何限制？
11. torn write 与 schema version 如何处理？
12. 删除是否需 tombstone 防复活？

---

## 54. 新增状态机的推荐模式

```text
Pure Tracker
  -> snapshot()
  -> SerializableSnapshot
  -> PersistenceMsg::State
  -> StorageAdapter::write_state
  -> atomic sidecar
  -> load_light
  -> Tracker::from_snapshot(session_dir, snapshot)
```

不要序列化 Path 派生值、Channel、Task Handle、Clock Instant 或连接对象。恢复构造函数必须明确处理旧版本与瞬时状态。

若操作必须确认提交，使用 `OperationAndAck` + oneshot，并区分未提交、已提交后失败、确认丢失；若要求顺序先 drain pending，若要求稳定介质再 durable sync。

---

## 55. 优势与成本

优势：

- 热路径追加成本低；
- UI replay 与模型历史解耦；
- 大日志可 lazy/stream；
- 状态机可独立迁移；
- Rewind 不破坏性截断事件日志；
- Fork 可选择性复制；
- 本地恢复不依赖远端在线。

成本：

- 多个投影可能短暂不一致；
- 开发者必须知道该查询哪份真相；
- 跨文件不是事务；
- Replay reducer 随控制事件变复杂；
- Fork、Rewind、Compaction 都要多文件协同；
- Sidecar schema 需长期兼容；
- Unbounded mailbox 需控制事件粒度。

---

## 56. 十条核心不变量

1. Session 是目录，不是单对象。
2. Chat History 是模型输入投影，Updates 是客户端事件投影。
3. Persistence Actor 保证顺序，不保证跨文件事务。
4. 最后一个 pending chunk 必须在 barrier/关闭时 drain。
5. `Committed` 错误不能盲重试。
6. Snapshot 恢复必须做状态语义迁移。
7. Replay 前必须应用 RewindMarker。
8. Cursor replay 必须有稳定 eventId 覆盖。
9. Compaction 后 Rewind 不能按 User item 计数截断。
10. 恢复的是事实与状态，不是旧进程执行栈。

---

## 57. 推荐阅读顺序

```text
第一遍：storage constants -> JSONL path helpers -> Summary
第二遍：PersistenceMsg -> SessionPersistence::run -> StorageAdapter impl
第三遍：session_setup load -> load_light -> prepare_replay_lines -> spawn
第四遍：rewind.rs -> helpers/replay.rs -> rewind filter -> copy.rs
第五遍：plan_mode.rs -> signals.rs -> goal_tracker.rs -> workflow/store.rs
```

---

## 58. 练习题

1. 为什么 Updates 已存在仍不能删除 Chat History？
2. 为什么 durable B 之前必须 flush pending A？
3. `Committed` 与 `AcknowledgementLost` 差别是什么？
4. 为什么 `ExitPending` 恢复时不能保持原样？
5. 为什么 cursor 要在跳过 ACU 之前解析？
6. 为什么 RewindMarker 追加后还要重写 Chat History？
7. ConversationOnly 为什么 merge file points？
8. 为什么 resumed Writeback 不 backfill？
9. TurnCapture 怎样跨 Compaction 保住本轮 items？
10. 持久化审批弹窗时，哪些事实可保存，哪些连接对象不能保存？

---

## 59. 总结

Grok Build 的持久化运行时可以压缩为四个思想：

```text
多投影：不同消费者拥有不同权威表示
串行协调：Actor 管顺序、合并、barrier 与 fan-out
按访问模式落盘：高频历史 append，当前状态 snapshot，大对象 lazy load
语义恢复：replay、rewind、repair、migration 把磁盘事实变成运行状态
```

真正的 Session resume 不是 `deserialize(session.json)`，而是一套恢复协议：定位目录、读取快照、修复 Conversation、过滤时间线、恢复状态机、重新建立连接、重播客户端历史、处理孤儿任务，并在正确的 ordering gate 之后重新接受 live work。

---

## Glossary：本篇术语表

| 名词 | 白话解释 | 在本篇中的具体含义 |
| --- | --- | --- |
| Persistence | 持久化 | 把内存状态写盘，使重启后可恢复 |
| Session Directory | 会话目录 | 保存一个 Session 的日志、快照和 Artifact |
| Snapshot | 快照 | 某时刻状态的完整当前值，通常覆盖旧值 |
| Event Log | 事件日志 | 按发生顺序追加事实的记录 |
| JSONL | JSON Lines | 每行一个 JSON，适合追加和逐行读取 |
| Sidecar | 旁路状态文件 | 与主日志并列、专门保存某类状态的小文件 |
| Projection | 投影 | 同一运行事实为不同消费者形成的表示 |
| Source of Truth | 权威事实源 | 回答某个问题时应优先信任的数据 |
| Actor | Actor 并发模型 | 一个任务独占状态，通过 mailbox 串行处理命令 |
| Mailbox | 邮箱 | Actor 接收命令的 Channel 队列 |
| `PersistenceMsg` | 持久化命令 | 发给 Persistence Actor 的写盘意图 |
| `PersistenceHandle` | 持久化句柄 | 调用者发送持久化命令的 capability |
| `StorageAdapter` | 存储适配器 | 把上层操作映射到具体存储后端的 trait |
| ACP | Agent Client Protocol | Agent 与客户端之间的标准协议 |
| xAI Extension | xAI 扩展 | ACP 外的 Rewind 等私有 Session Update |
| Envelope | 信封 | 给 payload 加 method、timestamp 等元数据的包装 |
| Conversation | 模型对话历史 | 下一次模型请求使用的 `ConversationItem` 序列 |
| Chat History | 模型历史文件 | Conversation 的磁盘投影 |
| Scrollback | 滚动历史 | 客户端 UI 展示的历史事件 |
| Replay | 回放 | 把历史更新重新发送给客户端或 reducer |
| Full Replay | 全量回放 | 从当前有效时间线开头发送 |
| Delta Replay | 增量回放 | 只发送 cursor/offset 后的事件 |
| Cursor | 游标 | 客户端最后确认看见的 event ID |
| `eventId` | 事件标识 | 用于顺序、去重和增量恢复的 ID |
| Rewind | 回退 | 恢复 Conversation、文件或二者到旧 prompt 前 |
| `RewindMarker` | 回退标记 | 在 append-only 日志中声明逻辑截断点的事件 |
| Rewind Point | 文件回退点 | 某轮前后文件内容的 snapshot |
| Timeline Branch | 时间线分叉 | Rewind 后从历史位置产生的新事件分支 |
| Compaction | 上下文压缩 | 将长 Conversation 折叠为摘要 |
| Checkpoint | 检查点 | 用于重建压缩后历史 Conversation 的数据 |
| Turn Capture | 轮次捕获 | 临时收集本轮 ConversationItem 供 trace 使用 |
| Pending Chunk | 待写块 | 为合并连续文本而暂存在 Actor 内的通知 |
| Coalescing | 合并 | 把连续小 chunk 拼成较少磁盘记录 |
| Flush | 冲刷 | 要求处理 pending 数据到指定顺序边界 |
| Barrier | 屏障 | caller 可等待、确认此前操作越过某阶段的同步点 |
| Ack | 确认 | Actor 通过 oneshot 告知操作已处理 |
| Buffered Append | 缓冲追加 | 追加记录但不做最强稳定介质同步 |
| Durable Append | 耐久追加 | 追加后执行 `sync_all`/`F_FULLFSYNC` |
| Atomic Replace | 原子替换 | temp 写完后 rename，使读者只见完整版本 |
| Torn Write | 撕裂写 | 崩溃或磁盘错误只写入部分记录 |
| Commit-aware Error | 提交感知错误 | 错误同时说明记录是否已提交 |
| `NotCommitted` | 未提交失败 | 已知未落盘，通常可安全重试 |
| `Committed` | 已提交失败 | 已落盘但后处理失败，重试会重复 |
| `AcknowledgementLost` | 确认丢失 | caller 无法知道是否已提交 |
| Idempotency | 幂等性 | 重复操作不会产生额外效果 |
| Generation | 代数编号 | 用单调数字识别操作新旧与重复 |
| Writeback | 回写 | 本地持久化后同步到远端 |
| Backfill | 历史补传 | 开启远端同步时补发已有本地更新 |
| Relay Sync | 中继同步 | 把 live 更新同步到实时共享服务 |
| ZDR | Zero Data Retention | 要求不进行相应远端数据保留的策略 |
| Cold Load | 冷加载 | 内存无 Actor，需要从磁盘重建 |
| Resident Session | 驻留会话 | SessionActor 仍在当前进程 |
| Reconnect | 重连 | 客户端重新连接 resident Session |
| Resume | 恢复 | 从磁盘重建非 resident Session |
| Lazy Load | 惰性加载 | 真正需要时才读大文件或反序列化 |
| Replay Gate | 回放闸门 | 防止历史与 live 事件交错的顺序机制 |
| State Migration | 状态迁移 | 加载旧/瞬时状态时转换为可运行语义 |
| Transient State | 瞬时状态 | 依赖旧连接或 in-flight turn、不能原样恢复的状态 |
| Tombstone | 墓碑 | 记录对象已删除，防止残留文件使其复活 |
| Fork | 分叉会话 | 从父 Session 复制选定历史创建 child |
| Inherited Prefix | 继承前缀 | Fork 从父 Conversation 继承并特殊保留的前段 |
| Subagent | 子 Agent | 由父 Agent 协调、拥有 child Session 的 Agent |
| Orphaned Subagent | 孤儿子 Agent | 日志已 spawned 却没有 finished 的子任务 |
| Workflow Manifest | 工作流清单 | 保存 workflow 版本、状态、脚本 revision 的文件 |
| Plan Mode | 计划模式 | 限制写工具并维护进入/活动/退出/审批的状态机 |
| Signals | 会话信号 | Turn、错误、工具、取消、Git/PR 等累计状态 |
| Announcement State | 公告状态 | 记录哪些能力已通知客户端的去重快照 |
| Goal Orchestration | 目标编排 | Goal 多阶段控制器的可恢复状态 |
| Integrity Repair | 完整性修复 | 修补悬空 Tool Call、重复 Result 等非法历史 |
| Stable Media | 稳定介质 | 断电后仍应保留已同步数据的存储层 |
| Event Sourcing | 事件溯源 | 通过重放完整事件序列重建状态；本系统只部分采用 |
