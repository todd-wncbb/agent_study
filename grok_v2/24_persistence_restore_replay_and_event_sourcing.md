# Grok Build 持久化、恢复、Replay 与事件溯源：从磁盘事件到可继续运行的 Agent

> 本文研究的不是“聊天记录保存在哪里”这一个问题，而是一个正在执行工具、压缩上下文、派生 Subagent、切换工作目录的 Agent，如何把状态安全地落盘，并在崩溃、重连或进程重启后恢复成一个可继续运行的系统。

---

## 1. 先给出结论

Grok Build 没有把整个 Session 序列化成一个巨大的 `session.json`。它采用的是多轨持久化：

1. `updates.jsonl` 保存按时间追加的会话通知，是客户端界面重建、事件游标续传和跨压缩 rewind 的主要事件轨道。
2. `chat_history.jsonl` 保存模型可以直接消费的 `ConversationItem`，是运行时恢复所需的规范化对话缓存。
3. `summary.json` 保存 Session 身份、索引、计数器、当前模型、Agent、Sandbox 等元数据。
4. Plan、Goal、Signals、Workflow、Rewind point 等子系统分别保存自己的快照或日志。
5. `events.jsonl` 保存诊断与统计事件，但它不是恢复 Session 的事实来源。

因此它不是纯粹的 Event Sourcing，也不是纯粹的 Snapshot Persistence，而是一套“追加事件 + 派生缓存 + 领域快照”的混合架构。

最重要的判断是：

```text
updates.jsonl       面向协议和用户可见历史
chat_history.jsonl  面向模型上下文恢复
summary.json        面向 Session 索引和当前元数据
领域状态文件         面向各状态机恢复
events.jsonl        面向观测，不参与语义恢复
```

---

## 2. 为什么不能只保存一个 Session 对象

内存里的 Session 包含几类性质完全不同的状态：

- 可以持久化的事实：用户消息、最终工具调用、工具结果、模式变化。
- 可以从事实重新计算的缓存：模型对话数组、token 统计的一部分、UI 展示状态。
- 只能保存快照的状态机：Plan、Goal、Workflow 当前步骤。
- 必须重新建立的外部资源：MCP 连接、凭证、网络客户端、hook、channel。
- 不应恢复的瞬时状态：正在 await 的 Rust future、锁、socket、进程句柄。

如果把这些全部塞进一个 JSON：

- 高频 token chunk 会反复重写整个文件；
- 写到一半崩溃可能损坏全部状态；
- 结构升级会导致庞大的兼容问题；
- 很难区分哪些字段是事实、缓存和临时状态；
- 多个 actor 更新同一个对象时容易发生 lost update。

多轨设计让每种数据采用适合自己的写入语义。

---

## 3. Session 磁盘目录

本地 Session 通常位于：

```text
{grok_home}/sessions/{url_encoded_cwd}/{session_id}/
```

典型目录可抽象为：

```text
session-id/
├── summary.json
├── updates.jsonl
├── chat_history.jsonl
├── events.jsonl
├── plan.json
├── plan_mode.json
├── signals.json
├── announcement_state.json
├── feedback.jsonl
├── btw_history.jsonl
├── rewind_points.jsonl
├── goal/
│   └── state.json
├── compaction_checkpoints/
│   └── <checkpoint-id>.json
├── workflows/
│   └── <run-id>/
│       ├── args.json
│       ├── script.rhai
│       ├── 0000.rhai
│       └── state.json
└── subagents/
    └── <subagent-id>/
        └── ...另一套 Session 文件
```

目录不是普通的“聊天导出”。它更像一个小型、按领域拆分的数据库。

---

## 4. 五条持久化轨道

| 轨道 | 主要文件 | 写入模型 | 主要消费者 | 是否事实来源 |
| --- | --- | --- | --- | --- |
| 协议事件 | `updates.jsonl` | append-only | ACP 客户端、replay、rewind | 是 |
| 模型历史 | `chat_history.jsonl` | append + replace | ChatState、Sampling | 派生事实/缓存 |
| Session 元数据 | `summary.json` | 锁内 patch + 原子替换 | Session 列表、load | 当前状态 |
| 领域状态 | Plan/Goal/Workflow 等 | 快照或专用日志 | 各领域 actor | 各自事实来源 |
| 观测事件 | `events.jsonl` | best-effort append | 调试、指标、上传 | 否 |

“事实来源”不是全局唯一的。对客户端时间线而言，`updates.jsonl` 最权威；对 Workflow 当前执行步骤而言，`workflows/.../state.json` 才权威。

---

## 5. `updates.jsonl` 保存什么

核心类型在：

```text
crates/codegen/xai-grok-shell/src/session/persistence.rs
crates/codegen/xai-grok-shell/src/session/storage/jsonl/mod.rs
```

`SessionUpdate` 有两大类：

```rust
enum SessionUpdate {
    Acp(Box<acp::SessionNotification>),
    Xai(Box<SessionNotification>),
}
```

它们分别对应：

```text
session/update
_x.ai/session/update
```

落盘时外面再包一层 envelope：

```rust
struct SessionUpdateEnvelope {
    timestamp: u64,
    method: String,
    params: Value,
}
```

所以一行大致是：

```json
{"timestamp":1770000000000,"method":"session/update","params":{"sessionId":"...","update":{...}}}
```

timestamp 是磁盘事件元数据；网络协议不必暴露同样的包装结构。

---

## 6. 为什么选择 JSONL

JSONL 是“一行一个 JSON 值”。它适合事件流：

- 新事件只需 append；
- 不必重写旧历史；
- 可以流式读取，避免把整个会话载入内存；
- 单行损坏时仍可继续读取后续行；
- 便于人工检查和迁移；
- 可以用 byte offset 实现 replay 后的尾部追赶。

它的代价是没有数据库事务、索引和 schema enforcement。因此代码必须自行处理锁、半写入、提交状态和版本兼容。

---

## 7. `chat_history.jsonl` 与 `updates.jsonl` 为什么并存

两者看起来重复，实际服务不同抽象。

`updates.jsonl` 记录的是协议事件，例如文本 chunk、ToolCall 状态更新、模式通知。它适合重建 UI 时间线，但不等于模型下一次请求所需的消息数组。

`chat_history.jsonl` 保存规范化 `ConversationItem`：

- user message；
- assistant message；
- reasoning；
- backend tool call；
- tool result；
- compaction 后替换出的历史。

如果每次恢复都从数万条展示事件归约模型消息，会增加延迟和兼容复杂度。因此 `chat_history.jsonl` 是一个为模型运行时准备的物化视图。

可以类比：

```text
updates.jsonl            原始业务事件流
chat_history.jsonl       为 Sampling 优化的 materialized view
```

但这个类比不是严格数据库定义，因为部分对话替换会直接持久化到 chat 文件。

---

## 8. `summary.json` 不是聊天摘要

这里的 `Summary` 更接近 Session manifest。它包含：

- Session ID、cwd、创建和更新时间；
- title、generated title、是否人工修改；
- 用户/Agent 消息计数；
- 当前 model、agent name、reasoning effort；
- sandbox profile；
- parent/fork/worktree/git 元数据；
- collection ID、next trace turn；
- chat format version；
- cwd generation、previous cwd 和 relocation bookkeeping；
- last active time、hidden、session kind 等。

恢复页面需要快速列出 Session 时，不应扫描全部更新；读取一个小型 `summary.json` 即可。

---

## 9. 领域状态为什么单独保存

Plan、Goal 和 Workflow 并不是普通聊天文本。例如 Workflow 需要知道：

- 当前 run ID；
- 原始参数；
- 使用哪个不可变脚本版本；
- 哪一步完成；
- token lease 和预算；
- 是否被中断、能否恢复。

把这些编码成一串 UI 消息再反推状态既脆弱又昂贵。领域状态机因此拥有自己的 schema、版本和恢复策略。

---

## 10. 持久化 Actor 是写入总线

Session 没有让各调用点随意同时改文件，而是把写入意图发到 persistence actor。

`PersistenceMsg` 覆盖的操作包括：

- append Session update；
- durable append 并回 Ack；
- append chat/content chunk；
- replace chat history；
- 更新 model、agent、reasoning；
- 保存 Plan/PlanMode/Goal/Signals；
- 添加、截断、合并 RewindPoint；
- 保存/删除 Workflow 状态；
- 保存 Feedback、BTW、Git HEAD；
- 保存 compaction checkpoint 和 segment；
- flush、flush-and-ack、copy files；
- 从本地模式升级到 writeback。

Actor 的价值不只是异步写盘，而是把顺序语义集中在一个地方：先发生的消息应先进入对应持久化轨道。

---

## 11. ChatState 如何接入持久化

ChatState 不需要知道 Session 目录结构。`ChannelChatPersistence` 把它的持久化操作翻译成 `PersistenceMsg` 发给 actor。

这形成边界：

```text
ChatState
   │ ConversationItem / replace history
   ▼
ChannelChatPersistence
   │ PersistenceMsg
   ▼
Session persistence actor
   │
   ├── chat_history.jsonl
   ├── summary.json
   └── remote writeback
```

底层 `ChatPersistence` trait 使用 actor 独占的 `&mut self`，因此文件句柄和缓冲区不需要再套一层细粒度锁。

---

## 12. Chunk 合并：减少写放大

模型流式输出时可能产生大量很小的文本 chunk。每个 chunk 都单独写一行会造成：

- syscall 数量增加；
- 文件膨胀；
- replay 事件数量过大；
- 远端同步压力增大。

持久化层会合并相邻、同类型、没有特殊 metadata/annotation 的 `AgentMessageChunk` 或 `AgentThoughtChunk`。

以下情况会刷新 pending chunk：

- 收到不同类型更新；
- 收到 durable barrier；
- 显式 `Flush`；
- actor channel 关闭；
- chunk 带 metadata，不能安全合并。

空文本且没有 metadata 的 chunk 可以直接忽略。

这只是存储层合并，不应改变用户看到的语义。

---

## 13. 普通 flush 与 durable append

普通 append 通常保证数据进入进程/操作系统缓冲并调用 flush，但不承诺断电后一定存在。

durable append 更强：

- 先排空此前 pending chunk，保持顺序；
- 写入事件；
- `sync_all`，macOS 上尽可能使用更强的 `F_FULLFSYNC`；
- 必要时同步父目录；
- 完成后才回 Ack。

需要“调用者必须知道是否真正落盘”的状态转换，应使用 durable 路径，而不是把 channel send 成功误认为持久化成功。

---

## 14. 三态提交错误

写文件失败不能简单返回一个 `io::Error`，因为调用者必须知道能否重试。

代码区分：

```text
NotCommitted        确认没有提交，可以重试
Committed(error)    事件已提交，但后续 bookkeeping 失败，不能重复 append
AcknowledgementLost / Indeterminate
                    无法确认调用者是否收到结果，盲目重试可能重复
```

典型场景：

1. update 行已经 append 到 `updates.jsonl`；
2. 随后更新 `summary.json` 的计数器失败；
3. 整体操作不是完全成功，但事件已经存在；
4. 若按普通错误重试，会产生重复消息。

因此返回 `Committed(error)`，让上层修复元数据而不是重放业务事件。

---

## 15. `FlushAndAck` 是同步屏障

普通 `Flush` 是 fire-and-forget 控制消息。`FlushAndAck` 则让调用方等待：

- pending chunk 已合并并写出；
- actor 在消息顺序上已经处理到这个 barrier；
- Ack 已返回。

重连 replay 前需要这个屏障，否则磁盘读取可能落后于仍在 actor 队列中的最新事件。

---

## 16. JSONL 如何处理崩溃留下的半行

假设进程在写这一行中途崩溃：

```text
{"timestamp":123,"method":"session/up
```

下次直接 append 新 JSON 会把两条记录粘在一起。实现会检查文件末尾是否为 `\n`：

- 若是，正常追加；
- 若不是，先补一个换行，再写新记录。

结果是坏尾巴被隔离成一条无法解析的行，后续完整事件仍然可读。

读取器逐行反序列化，坏行记录 warning 后跳过。这是一种“局部牺牲、整体可恢复”的策略。

---

## 17. 为什么读取 chat history 按字节切行

撕裂写入甚至可能把 UTF-8 字符切到一半。如果先把整个文件当合法 UTF-8 字符串读取，尾部一个坏字节会导致全文件失败。

chat history 的恢复路径先按原始字节的 `\n` 分行，再分别解析。这样损坏只污染一行。

遇到损坏或非法图片数据时，系统会保留第一份原始文件为类似：

```text
chat_history.jsonl.corrupt
```

随后对在线文件进行清理式重写。保留 corrupt 副本便于诊断，也避免“自动修复”抹掉唯一证据。

---

## 18. JSONL append 也需要锁

append-only 不等于天然并发安全。两个进程或两个重连中的 actor 仍可能交错写字节。

存储层使用文件锁/sidecar lock 序列化 append。锁保护的是一条逻辑记录从检查文件尾、补换行到完整写入的临界区。

否则可能出现：

```text
writer A: {"event":"abc
writer B: {"event":"xyz"}
writer A: "}
```

即使每个 writer 自己生成合法 JSON，最终文件也无法解析。

---

## 19. 快照文件使用临时文件 + rename

`summary.json`、Plan、Goal 等快照不适合原地覆盖。安全模式是：

```text
serialize new state
      ↓
write unique sibling temp file
      ↓
flush / required durability sync
      ↓
rename temp -> target
```

rename 在同一文件系统内通常具有原子可见性：读者看到旧版本或新版本，而不是半个 JSON。

需要注意，“原子可见”与“断电持久”不是同一概念；只有显式 fsync/sync 的路径才提供更强的 durability。

---

## 20. `SummaryPatch` 解决 lost update

`summary.json` 可能被旧 actor、重连 actor、标题生成器和模型切换同时更新。如果每个写者：

1. 读取完整 Summary；
2. 修改自己的字段；
3. 覆盖整个文件；

后写者就可能把别人的新字段覆盖回旧值。

实现采用：

```text
acquire summary.json.lock
read freshest summary
apply field-level SummaryPatch
write temp
rename
release lock
```

Patch 表达“意图”，而不是一个可能已经过期的完整对象。

---

## 21. Summary 中的单调字段

有些字段不能因为迟到写入而倒退，例如：

- `last_active_at`；
- `next_trace_turn`；
- chat format version；
- 某些计数器。

Patch 在持锁读到最新值后执行 max、增量或条件更新，而不是盲目赋值。

标题也有竞争规则：自动生成标题只在标题仍为空时原子写入；人工标题优先，迟到的自动标题不能覆盖它。

---

## 22. 新建 Session 的初始化顺序

概念上，新建流程要先建立最小可识别身份，再允许增量事件进入：

```text
resolve cwd / session id / storage mode
              ↓
create session directory and initial summary
              ↓
open updates/chat writers
              ↓
spawn persistence actor
              ↓
construct ChatState and Session actor
              ↓
accept prompt
```

若在目录和 summary 建立前就接受消息，崩溃后可能留下无法被 Session 列表发现的孤立事件文件。

---

## 23. `load_light` 为什么“轻”

长会话的 `updates.jsonl` 可能很大。Load 时如果一次性反序列化全部事件，会造成明显内存和延迟峰值。

`load_light` 主要加载：

- `summary.json`；
- `chat_history.jsonl`；
- Plan/Goal/Signals/Workflow 等当前快照；
- 文件路径形式的 `updates_file_path`；
- 文件路径形式的 `rewind_points_file_path`。

事件 replay 和 rewind point 在需要时流式读取。这是典型的 eager metadata + lazy history。

---

## 24. `load_session` 与 `resume_session`

两者都把磁盘状态恢复到运行时，但客户端策略可能不同：

- load 往往需要把历史 replay 给新连接；
- resume 可以请求 no-replay，只恢复运行时并继续；
- 即使 no-replay，仍需修复 stale task、orphan subagent 等后台状态。

服务端不会仅凭可伪造 metadata 改变 attach 语义，而是把操作类型显式传入 `AttachOperation`。

---

## 25. Load 竞争如何处理

同一 Session 可能同时收到 load、prompt、model change 或 mode change。

`begin_session_load` 使用 RAII attach guard 标记加载进行中。其他请求遇到相同 Session 时，不立即报 unknown session，而是等待正在进行的加载完成，带有约 60 秒上限。

RAII 的好处是：即使 load 中途 `?` 返回或 panic unwind，guard drop 仍会清除“加载中”状态，避免永久卡死。

---

## 26. 为什么重载前要 drain 旧 Session actor

同一个 Session 可能已有驻留线程。直接启动新 actor 会形成两个写者：

- 旧 actor 队列里还有未写事件；
- 新 actor 开始读取旧磁盘快照；
- 两者竞争 summary 和 JSONL。

因此 reload 会先让旧线程退出并等待它排空，设有大约 5 秒的上限。超时后为了可用性继续，但会警告持久化可能不完整。

这体现了明确取舍：不能无限等僵死 actor，也不能假装强一致。

---

## 27. 恢复时哪些状态直接读取

| 状态 | 恢复来源 |
| --- | --- |
| 模型对话 | `chat_history.jsonl` |
| model / agent / reasoning | `summary.json` |
| sandbox profile | `summary.json` |
| trace turn | `summary.json` |
| Plan / PlanMode | 独立快照 |
| Goal | `goal/state.json` |
| signals | `signals.json` |
| Workflow runs | `workflows/*/state.json` 等 |
| rewind points | `rewind_points.jsonl`，按需加载 |
| announcement 去重 | `announcement_state.json` |
| compaction 边界 | checkpoint/更新记录 |

保存 `agent_name` 很重要：恢复不应依赖当前可变的 model catalog 再猜一次 Agent。

---

## 28. 哪些状态重新计算或重建

| 状态 | 恢复方式 |
| --- | --- |
| MCP 连接 | 重新连接和发现 |
| Tool registry | 根据当前配置重新装配 |
| credentials / clients | 重新读取、构造 |
| system prompt | 按当前定义重新渲染 |
| hooks | 重新注册 |
| Rust futures / channels | 重新创建 |
| prompt index | 从持久事件选择性扫描/校准 |
| total token metadata | 从 replay/checkpoint 元数据恢复或重算 |
| orphan task 状态 | 从事件和子目录 reconcile |

恢复不是把进程内存“解冻”，而是用持久事实构造一个新的运行时实例。

---

## 29. Folder Trust 在恢复后仍然生效

Session 曾经执行过某个项目，不代表恢复时可以无条件加载该目录的新 `.envrc` 或配置。

项目环境仍受 Folder Trust 限制。磁盘历史证明“过去发生过什么”，不自动授权“现在可以执行什么”。这是恢复与安全边界之间的重要不变量。

---

## 30. 客户端 Replay 的完整时间线

驻留 Session 重连时的关键流程可概括为：

```text
temporarily close/disable live gateway
                  ↓
FlushAndAck persistence actor
                  ↓
capture updates file and initial end offset
                  ↓
stream historical replay
                  ↓
read newly appended tail after captured offset
                  ↓
enqueue delta replay
                  ↓
reopen live gateway
                  ↓
await replay completion barriers
```

如果不关闭 live gateway，历史事件和实时事件会交错；如果只读取一次文件，又会漏掉 replay 期间生成的新事件。

---

## 31. Replay 的 snapshot + tail catch-up

这是一个常见在线日志订阅算法：

```text
T0: 记录文件尾 offset=N
T1: 回放 [0, N)
T2: 读取 [N, current_end)
T3: 开启 live stream
```

实际实现还通过 gateway 队列和完成 Ack 保证 T2 与 T3 之间没有空洞。

它解决两个错误：

- gap：历史读取期间产生的事件被漏掉；
- duplicate/interleave：同一事件同时由历史和 live 通道送达，或顺序颠倒。

---

## 32. Event ID 与 reconnect cursor

发送更新时会附加唯一 event ID，形式大致为：

```text
{session-id}-{monotonic-counter}
```

恢复会扫描最大后缀，并把进程内全局计数器 reseed 到 `max + 1`。否则进程重启后从零计数，客户端 dedup 可能误删新事件。

客户端可以提交最后确认的 event ID 作为 cursor：

- 找到 cursor：只发送它之后的事件；
- 找不到或不安全：退回完整 replay；
- 完整 replay 的事件注入 `_meta.isReplay=true`。

---

## 33. 为什么 cursor 可能“不安全”

旧格式事件可能没有 event ID。假设 cursor 之后的尾部混入无 ID 事件，即使这次发送成功，下一次客户端也无法稳定表达“我收到哪一条”。

因此只要增量尾部包含无法可靠去重的 eventId-less 记录，实现宁愿退回完整 replay。

这比冒险少发消息更保守：重复展示可以用 replay 标志处理，静默丢历史则很难修复。

---

## 34. AvailableCommandsUpdate 的特殊顺序

重连时可能不需要重复发送旧的 `AvailableCommandsUpdate`，因为命令集合会按当前配置重新发布。

但 cursor 解析必须先在包含这些事件的完整集合上进行。客户端最后确认的 cursor 本身可能恰好属于一条 AvailableCommandsUpdate；若先过滤，cursor 会“凭空消失”，导致错误全量 replay。

正确顺序是：

```text
resolve cursor against original event set
then filter redundant command updates
```

---

## 35. Replay 不是逐行原样转发

历史协议会经过兼容和压缩处理：

- `ToolCall` 与多个 `ToolCallUpdate` 合并成一个已完成 ToolCall；
- Pending/InProgress 中间态不必作为最终历史重演；
- 旧版本的超大 completion 尝试缩减到连接允许的大小；
- 无法安全缩减的极端记录可以被丢弃并告警；
- replay metadata 和目标 leader client 会被注入。

因此磁盘日志是输入，重连发送流是经过 `prepare_replay_lines` 归约后的协议视图。

---

## 36. 为什么 ToolCall delta 不持久化

xAI 的 `ToolCallDeltaChunk` 是流式拼接 `raw_input` 的中间产物。若把所有 delta 当永久事实：

- 文件急剧膨胀；
- 恢复必须再次正确拼接；
- 中途断流会留下不完整 JSON 参数；
- 与最终 ACP ToolCall 重复。

系统选择最终、已组装的规范 ACP ToolCall 作为 replay 事实源。delta 只用于在线体验。

---

## 37. CurrentModeUpdate 为什么走 FIFO

某些更新可以直接发送，但 Mode 更新必须与其他带 event ID 的事件共享有序队列。

若它越过早先 chunk 到达客户端，客户端可能先推进 cursor，再把迟到的较早 chunk 判为重复而丢弃。

所以“数据很小”不代表可以绕过队列；是否直发取决于协议顺序语义。

---

## 38. Replay 完成 Ack

服务端不能把“已经 enqueue”当作“replay 完成”。历史事件可能仍在 gateway 队列中。

replay 路径收集 completion receiver 并等待完成，再返回 load 成功。这保证客户端收到 load response 时，关键历史不会仍滞留在服务端队列后方。

如果发送了事件却没有任何 completion，代码会发出警告，这通常意味着 gateway 接线或 Ack 传播出现问题。

---

## 39. Leader target 防止广播污染

Session 可能关联多个观察者。重连 replay 是针对发起 load 的 leader client 的历史补发，不应广播给所有已在线客户端。

prepare 阶段给事件标注目标 client，使 replay 与普通 live broadcast 区分开。

---

## 40. 三种不同含义的 Replay

这是阅读代码时最容易误判的地方。

### 40.1 客户端事件 replay

数据源：`updates.jsonl`。

目标：重建 TUI/ACP 客户端看到的时间线。

### 40.2 模型对话恢复

数据源：主要是 `chat_history.jsonl`。

目标：构造下一次 Sampling 的 ConversationItems。

### 40.3 Rewind replay

数据源：`updates.jsonl` + compaction checkpoint + rewind marker。

目标：把对话恢复到某个 prompt 边界，并可能同步恢复文件。

三者共享历史数据，但不能用同一个简单“逐行播放”函数替代。

---

## 41. `replay_to_prompt` 的任务

跨压缩 rewind 的核心代码位于：

```text
crates/codegen/xai-grok-shell/src/session/helpers/replay.rs
```

它流式扫描 updates，目标是计算“在目标 prompt 之后尚未发生时，模型对话应该是什么”。

它必须识别：

- 用户 prompt 边界；
- assistant/tool 消息；
- 历史 RewindMarker；
- CompactionCheckpoint；
- prompt index metadata；
- 无编号的插话或兼容事件。

这已经接近一个日志归约器，而不是文件读取器。

---

## 42. Prompt 计数为什么不能只数 UserMessage

一个真实用户 turn 可能被拆成多个 chunk；某些 user-like 更新可能是插话、恢复标记或旧格式事件。

实现优先使用 `_meta.promptIndex`：

- 相同 promptIndex 的多个 chunk 属于同一 turn；
- 新 index 才推进真实 prompt；
- checkpoint 之后从压缩边界继续计数；
- 缺失编号的事件按兼容规则处理，避免制造 phantom turn。

这说明 prompt index 是恢复语义的一部分，不只是 UI 计数器。

---

## 43. RewindMarker 为什么追加而不删除旧事件

当用户回退到较早 turn 并继续对话，可以物理截断 `updates.jsonl`，也可以追加一个 marker。

Grok Build 选择追加 `RewindMarker`。后续归约器看到它时丢弃逻辑时间线中目标点之后的分支。

优点：

- 保留审计历史；
- 避免危险的原地截断；
- remote/local 同步更容易表达；
- 可以理解多次 rewind 形成的新分支。

代价是所有 replay 实现都必须理解 marker，不能把日志当简单线性聊天。

---

## 44. CompactionCheckpoint 的作用

上下文压缩会把很长的早期对话替换成紧凑历史。只看压缩后的 `chat_history.jsonl`，无法回退到压缩前某个 turn。

checkpoint 文件保存：

- compacted history；
- 原始 user info 或恢复所需边界信息；
- schema version；
- 与 prompt index 的关联。

`updates.jsonl` 中的 CompactionCheckpoint 事件引用该文件，使事件时间线和外部大对象关联起来。

---

## 45. 目标在 checkpoint 前后时算法不同

### 目标早于 checkpoint

不能直接采用 compacted history，因为它已经概括了目标之后的内容。归约器继续使用原始更新，并读取 checkpoint 中保存的原始信息校准边界。

### 目标等于或晚于 checkpoint

可以把 compacted history 作为不透明前缀，然后只累计 checkpoint 之后的更新。

```text
target < checkpoint  -> reconstruct from pre-compaction events
target >= checkpoint -> compacted prefix + post-checkpoint events
```

---

## 46. Checkpoint 损坏时为什么拒绝 rewind

checkpoint 缺失、JSON 损坏或 schema version 高于当前实现时，代码选择安全失败。

它不会猜测一段可能错误的历史，因为错误 rewind 可能导致模型相信不存在的工具结果，或忘记仍然存在的文件修改。

恢复系统的重要原则是：无法证明重建正确时，显式拒绝比悄悄制造一个貌似可用的 Session 更安全。

---

## 47. Rewind 的三种模式

```text
All               对话和文件一起回退
FilesOnly         只恢复工作区文件
ConversationOnly  只恢复对话状态
```

ConversationOnly 不能简单截断所有 future rewind points，因为磁盘日志仍然权威，且未来点可能需要合并/重新解释。

Preview 与 force 也不同：preview 先报告将发生的改变和冲突，force 才真正执行。

---

## 48. 文件 Rewind 的 before/after snapshot

FileStateTracker 在工具修改前保存 before snapshot，修改后保存 after snapshot。

恢复时：

- before snapshot 提供要写回的旧内容；
- after snapshot 用来判断文件是否仍处于 Agent 修改后的状态；
- 若用户在之后手工修改，当前内容与 after snapshot 不同，就报告 external modification conflict。

这避免 rewind 无声覆盖用户后续编辑。

---

## 49. Rewind points 为什么懒加载

文件快照可能很大，且多数 Session load 后不会立刻 rewind。`load_light` 只保留 `rewind_points.jsonl` 路径，FileStateTracker 在实际需要时读取。

路径解析兼容：

- 新格式优先使用相对 Session 目录的路径，便于移动/同步；
- 旧格式绝对路径保留兼容 fallback。

---

## 50. Chat history 的格式升级

当前 chat 格式有显式版本，例如 `CHAT_FORMAT_VERSION = 1`。读取器可以逐行识别：

- 当前 `ConversationItem`；
- legacy v0 的 `ChatRequestMessage`；
- 同一个文件中的混合格式。

逐行 fallback 很重要，因为升级可能发生在会话中途，或者旧进程写完部分记录后由新进程继续。

兼容层还可把旧 message 内嵌的 reasoning/raw output 在内存中展开成相邻的 `Reasoning`、`BackendToolCall` item，而不强制立即改写原文件。

---

## 51. 非法图片如何处理

历史中的 image item 可能引用已经无效或无法解码的数据。让整个 Session 因一张坏图无法加载并不合理。

恢复路径会剥离非法图片并保留其余文本/消息，同时标记或备份损坏源文件。这样下一次 Sampling 至少能继续使用有效上下文。

---

## 52. 从 updates 重建 chat cache

如果 `chat_history.jsonl` 丢失或需要修复，`chat_rebuild::rebuild_chat_history` 可以从 `updates.jsonl` 归约：

- 合并连续 user/agent chunks；
- 形成 assistant messages；
- 收集 tool calls 与 tool results；
- 对 tool result 去重；
- 识别 compaction 边界；
- 忽略纯展示 thought、retry、plan 等不属于模型历史的事件。

输出先写临时文件，再 rename 替换。这证明 chat history 在相当程度上是事件轨道的物化缓存，但并不意味着每个运行场景都应现场重建。

---

## 53. 为什么 UI 历史和模型历史不能完全等价

UI 需要展示：

- 流式 thought；
- pending/in-progress/completed 状态；
- retry 提示；
- Plan 更新；
- permission 交互；
- 模式变化。

模型只需要影响推理的语义项。把所有 UI 事件塞回模型会浪费 token，甚至让模型把状态通知当用户指令。

相反，模型历史中的 compaction summary 也不一定要伪装成用户曾经看到的原始消息。

---

## 54. Workflow 的恢复目录

每个 run 使用独立目录，保存：

```text
args.json       原始、不可变参数
0000.rhai       某个不可变脚本 revision
script.rhai     当前脚本视图
state.json      versioned run manifest
```

恢复使用原始脚本和参数，而不是重新读取可能已被用户编辑的外部源文件。这保证“恢复同一次执行”，而不是悄悄开始语义不同的新执行。

Run ID 受字符集约束，文件读取带 no-follow、regular-file 和大小检查，防止持久化目录被 symlink 或超大文件利用。

---

## 55. Workflow 旧版本如何降级

旧 manifest 若缺少当前恢复所需的预算或 agent 信息，系统不会假装可以精确续跑。

它可把 run 标为 `Interrupted`、不可恢复，清理 token leases，并把 usage 标记为不完整。

“能显示历史状态”与“能安全继续执行”是两个能力。兼容层可以做到前者，却拒绝后者。

---

## 56. Workflow Journal 与确定性副作用

Rhai Workflow 的 host call 可能产生外部副作用。崩溃后简单从脚本开头重跑，会重复创建任务、重复发送调用或重复修改状态。

Journal 按规范化请求计算 hash，追加记录调用结果。恢复执行遇到已成功记录且请求匹配的调用时可以复用结果，而不是再次执行副作用。

它本质上实现：

```text
deterministic request identity
            +
durable result journal
            =
replay without duplicate side effects
```

Journal 同样要处理 torn tail、终止状态和同步落盘。

---

## 57. Subagent 的持久化边界

Subagent 是独立 Child Session，通常位于父目录：

```text
parent/subagents/<subagent-id>/
```

它拥有自己的：

- summary；
- updates；
- chat history；
- tool state；
- model 信息。

父 Session 保存 spawn/finish 等关联事件。恢复时既要读父时间线，也要检查子 Session 目录，才能识别“已 spawn 但没有 finish”的 orphan。

---

## 58. Subagent 恢复哪些内容

Resume Subagent 会继承原 transcript、tool state 和 source model，但：

- system prompt 按当前代码重新渲染；
- MCP/tool runtime 重新构造；
- 显式请求的 persona/type 必须与源 Subagent 匹配；
- 任意 model override 不应悄悄改变原执行身份。

恢复目标是延续同一任务身份，而不是借 resume 创建另一个 Agent。

---

## 59. Orphan 与 stale task 修复

进程可能在记录 spawn 后、finish 前崩溃。重启后仅恢复聊天会让 UI 永远显示 Running。

恢复会结合：

- replay 中未配对的 spawn/finish；
- 子目录持久状态；
- 当前 coordinator 中的 resident task；

进行 reconcile，去重后持久化修复状态。即使客户端请求 no-replay，也仍要做后台一致性修复。

---

## 60. 本地与远端存储

Session 本地目录仍是恢复的中心表面。配置远端 backend 时：

- 本地 load 命中：直接使用；
- 本地 NotFound：尝试从远端 pull，再按本地格式 load；
- 新 Session 从 local 模式升级 writeback 时，可回填已有本地更新；
- 已恢复 Session 通常采用 forward-only，避免把远端已有历史再回填一次造成重复。

远端同步不是另一个完全独立的 Session 语义，而是围绕相同文件/事件边界提供复制。

---

## 61. 远端发送为什么在本地提交之后

若先把事件发到远端，再写本地失败，会出现本地缺失、远端存在；反过来若本地提交后再排入 remote queue，至少本地事实源完整。

持久化 actor 因此在确认 append 已提交后才推进 relay/remote writeback。即使后续 summary patch 报 `Committed(error)`，也不能重复产生业务事件。

---

## 62. Copy/上传前的一致性屏障

复制 Session 目录用于快照、上传或 fork 前，actor 会先：

- 刷新 pending chunks；
- flush 相关 writer；
- 对要求的 Session 文件执行同步；
- 再递归复制。

否则复制出的目录可能包含旧 summary、新 updates 和仍在内存中的 chat 尾部，是一个从未真实存在过的混合时刻。

这不是完整跨文件事务，但显著缩小不一致窗口。

---

## 63. CWD relocation 与 generation

Session 路径包含编码后的 cwd。切换 cwd 不只是改 `summary.cwd`，还可能意味着目录视图和索引位置变化。

Summary 保存 cwd generation、previous cwd 和 pending reminder 等 bookkeeping。严格 append Ack 使用 generation 判定：

```text
Appended          本次迁移事件首次写入
AlreadyPresent    相同 generation 已存在，幂等成功
Indeterminate     无法确认，不能盲目重复
```

这是把目录迁移当成可恢复状态机，而不是一次脆弱的 rename。

---

## 64. Fork 与普通 Resume 的差别

Resume 延续相同 Session 身份和事件序列；Fork 创建新身份并继承一段历史前缀。

Fork 元数据通常记录 parent/source、继承前缀和 worktree 信息。复制前必须完成持久化屏障，新的 Session 再从稳定快照开始拥有自己的后续事件。

不要把 fork 理解成两个 actor 继续共享同一组可写 JSONL；那会破坏事件身份和 rewind 分支。

---

## 65. `events.jsonl` 到底是什么

它由 `xai-file-utils` 的 EventWriter 维护，每行包含 RFC3339 毫秒时间和 tagged Event，例如：

- TurnStarted / TurnEnded；
- FirstToken；
- Loop iteration；
- Tool started/completed；
- Permission 请求；
- interruption；
- yolo/运行模式相关统计。

EventWriter 是 best-effort：写失败记录日志，不应让 Agent 主流程失败。它没有 Session 更新同等级的 durable 承诺。

---

## 66. `events.jsonl` 为什么不能用于恢复

观测事件可能：

- 被禁用；
- 写入失败；
- 只记录聚合信息；
- 为分析调整 schema；
- 缺少完整消息或工具参数。

所以丢失 `events.jsonl` 会影响诊断和指标，但不应改变恢复后的聊天语义。

判断一个日志是否是 event sourcing 的事实源，不能只看它叫不叫 events，而要看业务状态是否依赖它重建。

---

## 67. EventTracker 的防重逻辑

EventTracker 位于 Session actor 的单线程上下文，追踪当前 turn 和活跃 tool。它用 guard 避免重复 `TurnEnded`，在 turn 异常结束时可为活跃 tool 生成 cancelled completion，并维护一次性的 interruption 信息。

后台任务只拿可 Clone、Send、Sync 的 writer，而不是共享整个非 Send tracker。这保持 actor 状态边界清晰。

---

## 68. 崩溃场景分析：更新写了一半

```text
updates.jsonl 最后一行 torn
```

恢复行为：

1. reader 跳过坏行；
2. 下次 append 先补换行；
3. 新事件不与坏尾拼接；
4. 最多损失一个未完整提交的事件。

如果调用方曾收到 durable Ack，则理论上不应出现该事件只剩半行；这正是 Ack 强度存在的意义。

---

## 69. 崩溃场景分析：update 成功、summary 失败

```text
updates counter actual = 101
summary counter        = 100
```

事件不能重写，否则成为 102 且内容重复。正确策略是把错误标为 Committed，在后续扫描、patch 或维护流程中修复派生计数。

这说明 summary counter 是索引/缓存，不应凌驾于事件事实。

---

## 70. 崩溃场景分析：chat cache 损坏

优先尝试逐行容错和 legacy fallback；保存 corrupt 副本；必要时从 `updates.jsonl` 重建。

但重建并非无损复刻所有内部状态：纯在线 delta、未持久化瞬态以及某些旧版兼容细节可能不存在。因此平时仍应把 chat history 正常持久化，不能把 rebuild 当常规 load 路径。

---

## 71. 崩溃场景分析：replay 期间产生新事件

如果只回放打开文件时看到的内容，新事件会落在历史末尾但 live gateway 当时关闭，造成 gap。

解决方案是保存初始 offset，历史回放后再读取 tail，并在重新开放 live gateway 前把 tail 排队。这是恢复链路最关键的一致性窗口之一。

---

## 72. 崩溃场景分析：Workflow host call 已执行、结果刚要写 journal

这是任何 exactly-once 外部副作用系统的困难窗口：外部动作可能成功，本地 durable 记录却未成功。

请求 hash + journal 能消除“记录已提交后重跑”的重复，但无法普遍解决外部系统成功、本地未记录的原子性鸿沟。真正严格的 exactly-once 仍依赖外部 API 的 idempotency key、事务或可查询结果。

阅读文档时应把“确定性 replay”理解为显著降低重复，不应泛化成跨任意系统的绝对 exactly-once。

---

## 73. 系统维持的关键不变量

### 不变量 A：已提交事件不因元数据失败而重复

通过 commit-aware error 保证。

### 不变量 B：replay 历史先于后续 live 事件

通过 gateway gate、tail catch-up 和 completion Ack 保证。

### 不变量 C：重启后 event ID 不倒退

通过扫描最大 suffix 并 reseed 保证。

### 不变量 D：人工标题不被迟到自动标题覆盖

通过锁内条件 patch 保证。

### 不变量 E：rewind 不覆盖用户外部编辑

通过 after snapshot 冲突检测保证。

### 不变量 F：观测日志缺失不改变 Session 语义

通过把 `events.jsonl` 排除在恢复源之外保证。

---

## 74. 这套架构哪里接近 Event Sourcing

符合之处：

- `updates.jsonl` 追加而非覆盖；
- rewind 通过 marker 表达新分支；
- replay reducer 可从事件恢复 UI/部分 chat；
- event ID、cursor、checkpoint 形成日志位置语义；
- 派生缓存可以重建。

不完全符合之处：

- 领域状态大量使用独立 snapshot；
- chat history 本身也直接写入和替换；
- 不是所有运行时状态都能由一个事件流重建；
- 没有统一事务日志覆盖所有文件；
- `events.jsonl` 虽名为事件，却不参与业务重建。

准确称呼是 hybrid event-log and snapshot persistence。

---

## 75. 为什么不直接使用 SQLite

SQLite 可以提供事务、索引和 WAL，但 JSONL/文件方案也有实际优势：

- Session 目录可直接复制、上传和检查；
- 协议事件天然适合 append 流；
- 单个 Session 隔离，损坏影响范围有限；
- 调试时无需数据库工具；
- 与 remote file storage、fork/worktree 流程容易组合。

代价则由代码承担：跨文件一致性、锁、版本迁移、坏尾处理、索引修复都更复杂。

架构选择并非“JSONL 更先进”，而是可移植性、透明性与事务能力之间的取舍。

---

## 76. 调试 Session 恢复的推荐顺序

### 第一步：确认目录身份

检查 encoded cwd、session ID、`summary.json` 的 cwd/parent/kind。

### 第二步：检查文件尾

查看 `updates.jsonl` 和 `chat_history.jsonl` 最后几十行，确认是否 torn、重复或停在某个 ToolCall。

### 第三步：区分故障表面

- UI 缺历史：查 update replay/cursor/gateway。
- 模型忘上下文：查 chat history/compaction。
- rewind 失败：查 checkpoint/rewind points。
- task 永远 Running：查 orphan reconcile。
- Session 列表消失：查 summary patch/路径 relocation。

### 第四步：检查事实源而非观测日志

不要因为 `events.jsonl` 有一条 ToolCompleted，就断言模型历史中一定有对应 ToolResult。

---

## 77. 值得打日志和断点的位置

```text
session/persistence.rs
  PersistenceMsg dispatch
  pending update flush
  durable append result

session/storage/jsonl/mod.rs
  append lock / torn-tail repair
  UpdatesIterator parse failure

session/storage/summary_write.rs
  SummaryPatch lock/read/apply/rename

session/acp_session_impl/updates.rs
  eventId/meta injection

session/helpers/replay.rs
  prompt counting/checkpoint/rewind marker

agent/mvp_agent/replay.rs
  prepare_replay_lines/cursor/tail

session/chat_persistence.rs
  legacy parse/corrupt backup/rebuild
```

调试时记录 session ID、file offset、event ID、prompt index 和 checkpoint ID，比只打印消息文本更有效。

---

## 78. 测试矩阵应该覆盖什么

### JSONL 层

- 正常多行；
- 最后一行缺换行；
- 半个 UTF-8 字符；
- 中间坏行；
- 两个 writer 竞争；
- append 成功、sync 失败。

### Summary 层

- 并发计数增量；
- manual title 与 auto title 竞争；
- last_active 不倒退；
- rename 前崩溃；
- lock owner 异常退出。

### Replay 层

- cursor 存在/不存在；
- cursor 位于 AvailableCommandsUpdate；
- 尾部含 eventId-less 事件；
- replay 期间追加 live 事件；
- event counter 重启 reseed；
- ToolCall 多次状态更新合并。

### Rewind 层

- target 在 checkpoint 前/后/等于边界；
- checkpoint 缺失、损坏、版本过新；
- 多次 rewind marker；
- user chunk 共享 promptIndex；
- 文件 after snapshot 被用户修改。

### 领域恢复

- Workflow 旧 manifest；
- journal torn tail；
- orphan Subagent；
- no-replay resume 仍 reconcile；
- remote pull-on-miss。

---

## 79. 推荐源码阅读顺序

第一组，建立存储地图：

```text
crates/codegen/xai-grok-shell/src/session/storage/mod.rs
crates/codegen/xai-grok-shell/src/session/persistence.rs
crates/codegen/xai-grok-shell/src/session/chat_persistence.rs
```

第二组，理解 JSONL 和 Summary 一致性：

```text
crates/codegen/xai-grok-shell/src/session/storage/jsonl/mod.rs
crates/codegen/xai-grok-shell/src/session/storage/summary_write.rs
crates/codegen/xai-chat-state/src/persistence.rs
```

第三组，理解 load/reconnect：

```text
crates/codegen/xai-grok-shell/src/session/acp_session_impl/spawn.rs
crates/codegen/xai-grok-shell/src/agent/mvp_agent/session_setup.rs
crates/codegen/xai-grok-shell/src/agent/mvp_agent/replay.rs
crates/codegen/xai-grok-shell/src/session/replay_events.rs
```

第四组，理解 rewind：

```text
crates/codegen/xai-grok-shell/src/session/helpers/replay.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/rewind.rs
crates/codegen/xai-grok-workspace/src/session/file_state.rs
crates/codegen/xai-grok-workspace/src/session/checkpoint.rs
```

第五组，理解领域恢复和观测：

```text
crates/codegen/xai-grok-shell/src/session/workflow/store.rs
crates/codegen/xai-workflow/src/journal.rs
crates/codegen/xai-grok-subagent-resolution/src/resume.rs
crates/codegen/xai-file-utils/src/events/log.rs
crates/codegen/xai-file-utils/src/events/tracker.rs
```

---

## 80. 阅读代码时常见误区

### 误区一：`events.jsonl` 就是事件溯源日志

错。真正驱动客户端 replay 的是 `updates.jsonl`。

### 误区二：恢复就是读取 chat history

错。还要恢复领域快照、事件计数器、compaction 边界，并重建外部连接。

### 误区三：channel send 成功等于落盘成功

错。只有 Ack/durable 路径能表达更强保证。

### 误区四：原子 rename 等于断电绝不丢失

错。原子可见性与持久性需要分别分析。

### 误区五：replay 必须原样播放每一行

错。协议 replay 会合并 ToolCall 状态、注入 metadata 并执行兼容过滤。

### 误区六：rewind 可以直接删除日志尾部

错。系统用 marker 保留分支历史，并结合 checkpoint 归约。

---

## 81. 可以继续优化的方向

### 81.1 跨文件一致性 manifest

给 updates/chat/summary/domain snapshots 标注同一 generation，可更容易识别复制出的混合快照。

### 81.2 周期性校验和

JSONL segment 加 checksum，可区分逻辑坏行与静默磁盘损坏。

### 81.3 显式物化视图版本

为 chat rebuild reducer 保存版本，避免升级后同一事件流归约出不同模型历史却无法识别。

### 81.4 Replay 索引

为大文件建立 event ID、prompt index、checkpoint 到 byte offset 的稀疏索引，降低 reconnect 和 rewind 扫描成本。

### 81.5 自动一致性审计

提供只读诊断命令，比较 summary counters、updates 最大 event ID、chat 尾部和 workflow manifest，输出可修复项。

### 81.6 外部副作用幂等键

Workflow journal 与工具协议统一传播 idempotency key，缩小“外部成功、本地未记账”的窗口。

---

## 82. 最终心智模型

可以把整个系统理解为：

```text
                     ┌─────────────────────┐
live Session state ─▶│ persistence actor   │
                     └───┬────┬────┬───────┘
                         │    │    │
              append log│    │    │snapshot/patch
                         ▼    ▼    ▼
                  updates  chat  summary + domains
                     │       │          │
                     │       │          └── restore state machines
                     │       └───────────── restore model context
                     ├───────────────────── replay client timeline
                     └───────────────────── rewind reducer

events.jsonl ────────────────────────────── diagnostics only
```

恢复的本质不是“把所有字段读回来”，而是：

1. 找到每个状态域的权威持久化来源；
2. 容忍尾部撕裂和旧格式；
3. 重建新的 actor、channel、连接和工具环境；
4. 用 gate、cursor、offset 和 Ack 把历史无缝接到 live stream；
5. 在无法证明正确时安全失败，而不是伪造一个看似完整的 Session。

这套设计最值得学习的地方，不是某个 JSON 文件，而是它始终在区分四件事：**事件是否已经提交、状态是否可以重建、客户端是否已经看见、外部副作用是否可以安全重试**。这四个问题一旦混在一起，Agent 的恢复就会出现重复消息、丢事件、错乱 rewind 或重复执行工具；Grok Build 的大部分复杂度，正是在逐层把它们拆开。
