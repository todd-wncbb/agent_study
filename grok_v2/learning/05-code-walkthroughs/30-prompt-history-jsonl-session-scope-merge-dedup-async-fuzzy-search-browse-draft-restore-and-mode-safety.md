# Walkthrough：Prompt History 如何持久化、合并、搜索，并安全恢复 Draft

> 场景：用户在空 Prompt 中按 Up，希望立即取回本 Session 最近输入；也可以运行 `/history`，输入模糊 Query 搜索更早内容。刚发送的 Prompt 可能还没写入磁盘，后台 History Fetch 可能晚到，重复 Prompt 可能仅空白不同，Bash History 需要恢复 `! ` Mode，搜索线程不能阻塞 UI，而 Esc、Down、输入字符、Enter、Tab、Mouse 和 Page Key 在 Browse 与 Search Mode 中含义不同。系统必须保住打开面板前的 Draft，不让 `@` 或 `/` Dropdown 抢走导航，也不能把 Skill 内部 XML 暴露给用户。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
Shell accepts real user prompt
  -> append PromptEntry to per-CWD prompt_history.jsonl
  -> {timestamp, session_id, prompt, is_bash}

Pager creates / loads session
  -> Effect::FetchPromptHistory { cwd, filter_session_id }
  -> ACP x.ai/prompt_history
  -> spawn_blocking load JSONL
  -> filter current session, dedup adjacent, reverse newest-first
  -> TaskResult::PromptHistoryLoaded
  -> sanitize skill display text
  -> session.prompt_history

Recall
  -> combine newest Scrollback UserPrompt blocks + fetched history
  -> trim-key dedup, Scrollback wins
  -> Up on empty Prompt: browse mode, newest live-populated
  -> /history: search mode, Composer is fuzzy query
  -> background nucleo daemon publishes max 100 results
  -> tick polls generation snapshot
  -> navigate / accept / cancel / detach-to-edit
  -> restore Bash prefix semantics, cursor, dropdown boundaries
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| JSONL 存储 | `crates/codegen/xai-grok-shell/src/session/prompt_history.rs` | append、load、truncate |
| Prompt 接受时落盘 | `crates/codegen/xai-grok-shell/src/session/acp_session_impl/prompt_queue.rs` | `PromptEntry` |
| ACP 扩展 | `crates/codegen/xai-grok-shell/src/extensions/prompt_history.rs` | fast/scoped/slow paths |
| Pager Fetch Effect | `crates/codegen/xai-grok-pager/src/app/effects/mod.rs` | `FetchPromptHistory` |
| 历史归并 | `crates/codegen/xai-grok-pager/src/app/agent_view/prompt.rs` | `combined_prompt_history` |
| 键盘行为 | 同上 | activate、browse、accept、cancel、detach |
| 搜索 Daemon | `crates/codegen/xai-grok-pager/src/views/history_search.rs` | nucleo、generation、snapshot |
| 结果归并 | `crates/codegen/xai-grok-pager/src/app/dispatch/task_result.rs` | `PromptHistoryLoaded` |
| 本地即时记录 | `crates/codegen/xai-grok-pager/src/app/dispatch/prompt.rs` | move-to-front、cap 200 |
| Interject 记录 | `crates/codegen/xai-grok-pager/src/app/dispatch/interject.rs` | `record_interject_prompt_history` |
| Overlay 渲染 | `crates/codegen/xai-grok-pager/src/app/agent_view/render.rs` | history panel、Loading、highlights |

## 3. Prompt History 与 Conversation History 不同

Conversation History 保存模型上下文、Assistant、Tool 和 System Item；Prompt History 是为了快速 Recall 的用户输入索引，只保存 Prompt 文本与少量元数据。

## 4. Prompt History 与 Shell Completion History 也不同

Bash Completion 会额外读取 `.bash_history`、`.zsh_history` 或 Fish History；本篇关注 Pager Up 与 `/history` 使用的 Grok Prompt History。

## 5. 两层 History 各自解决什么

Shell JSONL 提供跨进程持久化；Pager 内存 History 与 Scrollback 提供当前进程立即可见、抗 Fetch 竞态的 Recall。

## 6. 为什么不能每次 Up 都扫描 Session Storage

Session 可能包含大量 Conversation Event。独立、Append-only 的小 JSONL 能快速读取用户 Prompt，不必反序列化 Tool、Assistant 或 Plan State。

## 7. History Recall 不等于自动发送

Accept 只把选中文本写回 Composer，用户仍需再次按 Enter。Browse 中第一次 Up 也只编辑 Draft。

## 8. Glossary（一）：History 类型

- **Prompt history**：用于取回用户曾输入 Prompt 的轻量索引。
- **Conversation history**：完整对话上下文与事件记录。
- **Recall**：把旧输入重新放回 Composer 继续编辑。
- **Persistent history**：跨进程保存在磁盘上的历史。
- **In-memory history**：当前 Pager 进程维护的快速列表。
- **Scrollback**：当前 Agent UI 已渲染或保存的消息时间线。

## 9. 每个 CWD 有独立 JSONL

`prompt_history_path(cwd)` 位于该 Working Directory 对应的 Grok Session 目录，文件名固定为 `prompt_history.jsonl`。

## 10. 为什么按 CWD 分区

项目 A 的 Prompt 通常不适合在项目 B 中 Recall；按工作目录隔离减少无关结果，也缩小每次读取量。

## 11. 每行是一个 `PromptEntry`

字段为 UTC Timestamp、Session ID、Prompt String 与 `is_bash`。JSONL 一行一个完整对象，便于 Append 和容错跳过坏行。

## 12. `is_bash` 有向后兼容默认值

旧 Entry 缺少该字段时 Serde 默认为 false。新 Schema 不要求迁移整个文件。

## 13. Append 会先确保 Parent Directory

`ensure_sessions_cwd_dir` 创建所需目录，然后以 `create + append` 打开文件，不重写既有 Entry。

## 14. 序列化后显式追加换行

每个 Entry 用 `serde_json::to_vec` 编码，再 push `\n`，保证下一个 Append 从新行开始。

## 15. Append 在 Blocking Pool 执行

`append_prompt_async` 使用 `spawn_blocking`，避免同步文件 I/O 卡住 Agent Async Runtime。

## 16. 真正 Prompt 接受后才落盘

Shell 在 Handle Prompt 流程中排除 Synthetic Origin、空文本和 Subagent Startup Hint，再 Await Append；这样 Quit 不会丢掉刚接受的真实输入。

## 17. 为什么 Synthetic Auto-wake 不进入 History

它是系统为继续后台流程生成的控制 Prompt，不是用户想通过 Up 找回的输入。

## 18. Subagent 内部 Prompt 也不污染用户 History

Subagent Startup/Coordination 输入属于系统编排，混入父工作区 Recall 会让用户看到内部操作文本。

## 19. Bash Entry 保存原始显示语义

Shell 从 Prompt Blocks 判断是否 Bash，并记录 `is_bash`；Pager 本地列表则用 `! command` 形式保留 Mode 恢复线索。

## 20. Glossary（二）：JSONL 与写入

- **CWD**：Current Working Directory，当前工作目录。
- **JSONL**：每行一个独立 JSON Object 的文本格式。
- **Append-only**：正常写入只追加新记录，不原地修改旧记录。
- **UTC timestamp**：与时区无关的统一时间戳。
- **Synthetic origin**：不是用户直接输入，而由系统自动生成的 Prompt 来源。
- **Blocking pool**：专门执行可能阻塞线程的文件 I/O 任务池。

## 21. 文件最多维护 10000 条

`MAX_PROMPT_HISTORY_ENTRIES = 10_000`。维护任务发现超限时只保留文件尾部最近 10000 个 Entry。

## 22. Truncate 不是每次 Append 都同步运行

它作为后台 Maintenance 调用，避免正常 Prompt 发送每次都重读整个文件。

## 23. Truncate 会跳过空行和坏 JSON

读取使用 `map_while(Result::ok)`、过滤 Empty，并只保留能反序列化的 `PromptEntry`；局部损坏不阻止修复其余内容。

## 24. 临时文件后 Rename

保留 Entry 先写入 `.jsonl.tmp`，完成后 Rename 替换原文件，避免直接 Truncate 时崩溃留下半文件。

## 25. “Atomic Rename”保护的边界

同一文件系统中 Rename 通常提供全旧或全新视图；它不等于跨平台事务数据库，但显著优于原地覆盖。

## 26. 读取不存在的文件返回空列表

新项目第一次使用没有 History 是正常状态，不应变成 Error Toast。

## 27. 读取逐行容忍非法 JSON

I/O Line Error 会返回 Err；某行 JSON Parse Error 只跳过该行。结构损坏与底层读取失败被区别处理。

## 28. 只去重相邻重复

`prompts.dedup()` 在文件正序读取后只移除 Consecutive Equal Strings；相隔很久的相同 Prompt 仍保留时间位置。

## 29. 最后 Reverse 成 Newest-first

文件顺序是 Append Chronological；读取后反转，使 Index 0 是最近 Entry，符合 Pager Merge 与 Recall 输入契约。

## 30. Glossary（三）：维护与容错

- **Maintenance**：不在主请求关键路径上执行的后台整理。
- **Truncation**：丢弃旧 Entry，把文件限制在最大规模。
- **Atomic rename**：先完整写新文件，再一次替换路径指向。
- **Partial corruption**：文件只有部分行损坏，其余数据仍可读取。
- **Consecutive dedup**：只合并相邻、完全相同的项目。
- **Newest-first**：列表头部是最新项目的排序约定。

## 31. ACP 方法名是 `x.ai/prompt_history`

Pager 不直接访问 Shell 的文件路径，而是通过 ACP Extension 请求，让持久化所有权留在 Agent/Shell 侧。

## 32. 请求至少携带 CWD

此外可以带 `session_id` 或 `filter_session_id`。两个字段看似相近，实际选择不同数据路径和排序语义。

## 33. 无 ID 是 Fast All-session Path

直接读取该 CWD 的 JSONL，返回所有 Session Prompt，Newest-first，适合全局历史客户端。

## 34. `filter_session_id` 是 Fast Scoped Path

同样读取 JSONL，但按 Entry Session ID 过滤，并保持 Newest-first。Pager Up 与 `/history` 使用这条路径。

## 35. `session_id` 是 Slow Storage Path

它从 Session Summary 与 JSONL Session Storage 重建 User Prompts，保持单 Session Chronological Index 稳定，主要供 Rewind 等客户端使用。

## 36. 两个 ID 同时出现时 Filter 优先

Handler 先检查 `filter_session_id`，明确保证 Pager 的 Fast Scoped 语义不被 Slow Path 覆盖。

## 37. Slow Path 为什么使用 `buffered(32)`

多个 Session 可并发读取，但 `buffered` 保留输入顺序；`buffer_unordered` 会按完成时间打乱 Session Timeline。

## 38. 单 Session Slow Path 不 Reverse

它要维持从第一个 Prompt 开始的稳定 0-based Index；All-session Slow View 才最终 Reverse 为 Newest-first。

## 39. Pager Fetch 发送的是 `filter_session_id`

Effect 参数包含 Agent ID、CWD 和当前 Session ID；Effect Runner 构造 Extension Request，在 JoinSet Task 中等待响应。

## 40. 响应兼容两种 Envelope

解析会先查 `result.prompts`，再查顶层 `prompts`，兼容 ACP Response 包装差异。

## 41. 非 String 元素被过滤

JSON Array 中只收集 `as_str()` 成功的元素；Malformed Field 不会令整个 Pager Task Panic。

## 42. Fetch 失败降级为空列表

记录 Warning 后仍产生 `PromptHistoryLoaded { prompts: [] }`，确保 Loading Flag 能结束，UI 不会永久显示 Loading。

## 43. Glossary（四）：ACP 查询路径

- **ACP extension**：标准 ACP 之外、通过方法名与 JSON 参数扩展的请求。
- **Fast path**：直接读取轻量索引，不重建完整 Session。
- **Scoped path**：只返回指定 Session ID 的结果。
- **Slow path**：从 Session Storage 重新提取 Prompt 的较重路径。
- **Stable index**：同一 Session Prompt 的序号不会因排序方式改变。
- **Envelope**：协议在真实 Payload 外包裹的 `result` 等结构。

## 44. Session 创建与 Load 都会 Fetch

Pager 在新 Session 创建、恢复和 Load 的生命周期中设置 `prompt_history_loading=true`，并发出 Fetch Effect。

## 45. Loading Flag 只在空 Composer 时显示

`prompt_history_loading()` 还要求 `prompt.text().is_empty()`；用户已开始输入时不应用 Loading 文案覆盖实际 Query 体验。

## 46. Task Result 绑定 Agent ID

异步响应完成时只更新原 Agent；用户切换 Active View 不会把某 Session History 填入另一个 Agent。

## 47. Loaded 会替换 Fetched List

`session.prompt_history = prompts...collect()`，不是 Append。每次 Fetch 是当前 Shell-side scoped Snapshot。

## 48. Skill XML 在进入 UI 前清理

每条 Prompt 尝试 `extract_skill_display_text`，成功时用用户可读文本替换包含内部 Skill Tag 的存储表示。

## 49. 为什么清理发生在 Pager Result Arm

Shell 存储保留协议所需原始 Prompt；UI Recall 展示用户当初看到的文本。显示转换不污染持久化事实。

## 50. Open Overlay 会被 Late Fetch 刷新

如果 History Search 正 Active，Task Result 重建 Combined History 并 `refresh_items`，无需关闭重开面板。

## 51. Search Mode 还会重发当前 Query

Daemon Items 替换后，Pager 从 Composer 取得 Query 再 `update_query`；Browse Mode 不这样做，因为 Composer 装的是候选，不是 Query。

## 52. 同一 Query 不重置用户 Selection

`HistorySearchState.last_query` 区分用户新输入与 Late Fetch 的重复 Query；后者不会重新打开 `stick_to_bottom`。

## 53. Glossary（五）：异步加载

- **Loading flag**：后台 Fetch 尚未完成的 UI 状态。
- **Late result**：面板打开或用户操作后才到达的异步响应。
- **Result arm**：Task Result 枚举中处理某类完成消息的分支。
- **Display sanitization**：保留存储原文，只在 UI 层转换内部标记。
- **Refresh in place**：不关闭当前交互，替换其 Items 并保留 Query/Selection。

## 54. Pager 内存列表最多 200 条

正常 Send、Server-authoritative Send、Bash、Interject、Send Now 和部分 Queue Edit 会即时更新 `session.prompt_history`。

## 55. 即时记录使用 Move-to-front

先按 `p.trim() != new.trim()` Retain 删除旧副本，再把原始 Text Insert 到 Index 0，最后 Truncate 200。

## 56. 为什么用 Trim Key、保留原文

`"fix bug"` 与 `" fix bug \n"` 对 Recall 视为同一意图，但显示与再次编辑仍保留最近一次的实际 Whitespace。

## 57. 空白 Prompt 不记录

Trim 后 Empty 立即返回，避免 Up 取回一个看似无内容的 Draft。

## 58. 只记录用户真正输入的 Dispatch

`consume_input=false` 的 Modal-driven Command 不加入 Recall；否则用户会看到自己从未在 Composer 输入的内部动作。

## 59. Clear Prompt 也会记录非空 Draft

Esc Esc 清空前调用同一 History Recorder，让用户误清后仍能 Up 找回，兼作轻量恢复机制。

## 60. Interjection 需要 Recall

虽然它在 Turn 中途发送，不走普通 Prompt Queue，仍是用户输入，因此 `record_interject_prompt_history` 统一加入列表。

## 61. Bash 使用 `! ` Prefix

本地 History Key 构造为 `! {command.trim()}`，Recall 时识别 Prefix 并恢复 Bash Input Mode，而不是把它作为普通 AI Prompt。

## 62. 为什么还要与 Scrollback 合并

Pager Local List 可能被刚到达的 `PromptHistoryLoaded` 整体替换，而 Shell Append 与 Fetch 存在竞态；刚发送 Prompt 已经在 Scrollback，却可能暂时不在 Fetch Snapshot。

## 63. Glossary（六）：即时记录

- **Move-to-front**：命中旧项目时删除旧位置，并把最新版本移到列表头。
- **Trim key**：去除首尾空白后用于相等判断的 Key。
- **Consume input**：本次动作是否消费了用户 Composer 中的文字。
- **Modal-driven dispatch**：由 UI 选项触发，而不是用户输入 Prompt 的动作。
- **Local recall cap**：Pager 内存中最多保留 200 条候选。

## 64. Combined History 先扫描 Scrollback

从 `scrollback.len()-1` 逆向到 0，只收集 `RenderBlock::UserPrompt`，因此当前 Session 最新 UI Prompt 最先进入结果。

## 65. Tool、Assistant 和 System Block 被排除

类型匹配只接受 UserPrompt，避免 Recall 工具输出、错误消息或模型答案。

## 66. Scrollback 后追加 Fetched History

遍历 `session.prompt_history` 的 Newest-first 列表；已经由 Scrollback Seen 的 Trim Key 会跳过。

## 67. Scrollback-first 是 Load-bearing 顺序

它不是单纯偏好：Fresh Prompt 可能只存在 Scrollback，Fetched List 晚到且旧。换序会让旧副本压过刚发送原文。

## 68. Combined Dedup 使用 HashSet Trim Key

全列表去重，而不是只去重相邻；每个等价 Prompt 只保留最高优先级、最新来源的一份。

## 69. Empty Trim Key 被排除

无论 Scrollback 还是 Fetched，纯空白都不进入 HistoryEntry。

## 70. Combined History 每次打开时构建

不是每次 Search Keystroke 都重新扫描 Scrollback；Activate 时传给 Daemon，后续 Query 只在 Cached Items 上匹配。

## 71. Glossary（七）：归并语义

- **Combined history**：由当前 Scrollback 与 Fetched List 合成的 Recall 候选。
- **Authoritative newest source**：在竞态中最可信、最能代表最新输入的来源。
- **Full-list dedup**：使用 Seen Set 消除任何位置的重复。
- **Source precedence**：多个来源有相同 Key 时决定谁获胜的顺序。
- **Load-bearing order**：改变后会破坏正确性、并非仅影响显示的排序。

## 72. Up Browse 只从空 Prompt 打开

条件包括 Prompt Mode Normal、Text Empty、File Search 不可见、Input Mode Normal 和精确 Up Key。

## 73. 非空 Prompt 的 Up 保留编辑语义

它不会突然替换用户正在写的 Draft。TextArea 可按自身光标/行导航规则处理。

## 74. Bash/Remember Mode 不自动 Browse

把普通 Chat Prompt Recall 到 Bash Composer 可能导致错误执行；入口要求 `PromptInputMode::Normal`。

## 75. Down 从不打开 History

只有 Up 是入口；Down 在面板内用于走向更新条目或退出。这符合常见 Shell History 心智模型。

## 76. Matcher Thread 不可用时不填 Composer

Daemon Spawn 失败会让 `is_available=false`；入口即使有 History 也不 Populate，避免产生无法正常关闭/导航的半面板。

## 77. 空 History 的 Up 仍被消费

空 Composer 中 Up 没有其他有意义 Cursor Motion，返回 Changed 可避免它穿透成 Agent-level Action。

## 78. Browse 激活会保存打开前 Draft

`saved_text=current_text`；正常入口虽要求 Empty，统一保存仍让 State Machine 可复用并支持严谨 Cancel。

## 79. Newest Entry 同步填入 Composer

Daemon 是异步的，Agent 不等待初始 Snapshot，而是确定性使用 Combined History Index 0 立即 Populate。

## 80. Browse Panel 仍异步得到完整 Items

`activate_browse` 同时把 History 发送给 Daemon；Tick Poll 后 Panel 显示全部结果并把 Selection 锚到底部 Newest。

## 81. Glossary（八）：Browse 入口

- **Browse mode**：Up 打开的历史浏览，Composer 显示当前选中候选。
- **Entry condition**：允许某操作启动必须同时满足的状态条件。
- **Live population**：Selection 移动时立即替换 Composer Text。
- **Deterministic first fill**：不等待后台结果，直接用已知 Newest Item 填充。
- **Half-open state**：UI 部分激活但后台能力不可用的错误状态。

## 82. `/history` 打开 Search Mode

Slash Pipeline 先消费并清空命令文本，再 Dispatch `OpenHistorySearch`；面板以 Empty Query 展示完整 History。

## 83. Search Mode 中 Composer 是 Query

普通字符、Backspace 等传给 TextArea，随后把整个 Text 发送给 Daemon Filter；候选不会自动写入 Composer。

## 84. Prefix 会变成 `? `

Prompt Widget 只在 Active 且非 Browse 时使用 Search Indicator；Browse 保持普通 Prompt/Bash Prefix，因为 Composer 是真实候选 Draft。

## 85. History Intercept 优先于 File/Slash Dropdown

Recall Candidate 可能以 `/` 开头或以 `@path` 结尾；由候选文字派生出的 Dropdown 不能抢走 Arrow、Enter、Tab 或 Esc。

## 86. Populate 后主动清 File Search Context

`set_text` 会重算 `@` Context，History Flow 随后 Clear，保证 Panel 是唯一可见的候选 owner。

## 87. Search Query 不逐键扫描 Scrollback

Daemon 已拥有 Items 的 UTF-32 表示，UI 只发送 Query Message；这让大型 Scrollback 下输入延迟保持稳定。

## 88. Glossary（九）：Search Mode

- **Search mode**：`/history` 打开的模糊搜索，Composer 充当 Query 输入框。
- **Filter query**：用于筛选候选而非待发送内容的文字。
- **Input intercept**：某 Panel Active 时在其他 Dropdown/Action 之前处理按键。
- **Derived dropdown**：由当前 Composer Text 自动出现的 Slash 或 File 候选。
- **Cached items**：Activate 时交给搜索线程、每次 Query 重用的候选集合。

## 89. Daemon 使用独立 `std::thread`

它拥有 Nucleo Matcher、MultiPattern 和 UTF-32 Items；UI Thread 不执行 Score Loop。

## 90. Channel 是容量 256 的 Sync Channel

消息包括 SetItems、SetItemsAndQuery、SetQuery 与 Stop。有限容量避免无限堆积，但当前 Send 调用忽略失败以安全降级。

## 91. 为什么需要 UTF-32 String

Nucleo 按 Unicode Code Point 做匹配与 Highlight Indices，避免把多字节 UTF-8 中间 Byte 当成字符位置。

## 92. Query 使用 Smart Case 与 Smart Normalization

Matcher 根据 Query 决定大小写敏感性，并做合理 Unicode Normalization，兼顾自然搜索与显式大写意图。

## 93. 连续 Query 会 Drain 到最新

Worker 收到第一条 Message 后 `try_recv` 清空积压；连续 SetQuery 只保留最后一条，避免用户快速输入时逐个计算过期中间 Query。

## 94. Items Refresh 与 Query 合成原子消息

SetItems 紧跟 SetQuery 会变成 SetItemsAndQuery，保证 Late Fetch 的新 Items 直接按当前 Query 发布，不先闪现全量结果。

## 95. Stop 永远获胜

Drain 看到 Stop 立即返回 Stop；Daemon Drop 发送它，让 Worker 正常退出。

## 96. 每次 Publish 增加 Generation

Shared Snapshot 包含 Arc Slice 与 Generation；UI Poll 只在 Generation 变化时替换当前结果。

## 97. 为什么 Snapshot 使用 Arc Slice

UI Clone Snapshot 时无需深拷贝所有 Result Text；Worker 发布新不可变数组，旧 Render 可安全持有旧 Arc。

## 98. Mutex 临界区很短

耗时 Matching 在 Worker Local State 完成，最终只在替换 Snapshot 时 Lock；UI Poll 也只是 Clone。

## 99. Glossary（十）：搜索线程

- **Nucleo**：用于快速模糊匹配与排序的 Rust Matcher。
- **UTF-32**：每个 Unicode Code Point 使用固定宽度表示的字符串形式。
- **Sync channel**：带固定容量、可同步发送消息的标准库 Channel。
- **Message coalescing**：合并积压请求，只计算最新有意义状态。
- **Generation**：每次发布递增的版本号。
- **Immutable snapshot**：发布后不再修改、可安全共享的结果集合。

## 100. Empty Query 不做 Fuzzy Score

Worker 取前 100 个 Newest-first Items，再 Reverse，使 Panel 从 Oldest 到 Newest 由上到下排列。

## 101. 为什么 Newest 显示在底部

Panel 紧邻下方 Composer；最近 Prompt 位于最靠近 Composer 的底部，并默认 Selected，视觉与 Up Recall 方向一致。

## 102. 非空 Query 按 Score 降序取前 100

先 Best-first Sort/Truncate，再 Reverse，让 Best Match 出现在底部并默认选中。

## 103. `MAX_RESULTS=100` 只限制展示

Worker 仍遍历全部 Cached Items 计算 Score，但只发布最相关 100 条，限制 Render 和 Snapshot 大小。

## 104. Highlight Indices 来自 Column Pattern

每个 Result 保存命中的 Unicode Positions，Renderer 可对 Query 命中的字符使用强调 Style。

## 105. Append Optimization 有保守条件

新 Query 必须以前一 Query Bytes 开头、前一 Query 非空，并且末尾不是反斜杠或 ASCII Whitespace，才告诉 Nucleo 这是 Append。

## 106. 新 Query 会重新 Stick Bottom

`update_query` 发现 String 真正变化时设置 `stick_to_bottom=true`，结果到达后 Selection 自动选择 Best Match。

## 107. 用户导航会解除 Sticky

Move Up/Down、Page Move 或 Click 都设 false；Late Same-query Refresh 只 Clamp Index，不抢回 Bottom。

## 108. Result 变空时 Selected 归零

避免 Index 下溢；`selected_text()` 返回 None，Accept 会关闭并恢复 Saved Draft。

## 109. Glossary（十一）：结果排序

- **Fuzzy score**：Query 与候选相似程度的数值。
- **Highlight index**：候选中被 Query 命中的字符位置。
- **Best-first**：最高分排在数组前部的计算顺序。
- **Bottom anchoring**：UI 把最佳/最新结果放在面板底部并默认选择。
- **Sticky selection**：结果异步变化时 Selection 持续跟随 Bottom。
- **Clamp**：把 Index 限制在合法范围内。

## 110. Up/Ctrl-P/Ctrl-K 向 Older 移动

Panel 内这三种按键调用 `move_up`；到顶部后不 Wrap，保持 Oldest Entry。

## 111. Down/Ctrl-N/Ctrl-J 向 Newer 移动

若已在 Bottom Newest，Move 返回 false，Caller 关闭 Panel 并恢复打开前 Draft。

## 112. 为什么 Down at Newest 表示退出

Up 打开后已经选中 Newest；用户立刻按 Down 的直觉是回到尚未 Browse 的空 Draft，而不是 Wrap 到 Oldest。

## 113. PageUp/PageDown 每次移动半页

`page_move` 使用 `(visible_rows / 2).max(1)`；Key Handler 当前传 8，因此一次移动 4 项，并 Clamp 到边界。

## 114. Browse Navigation 会 Live-populate

Selection 成功移动后调用 `populate_prompt_from_history_selection`；Search Mode 只改变 Highlight，不修改 Query。

## 115. Mouse Hover 只是视觉状态

`set_hovered` Clamp Index；Click 才 `select_hovered`、取消 Sticky 并进入 Accept 逻辑。

## 116. Panel 最多显示 8 行

Renderer 根据 Selected 计算 Scroll Offset，让当前项保持可见；超过 8 项显示 Scrollbar。

## 117. Empty Result 区分 Loading 与 No Match

Session Flag 且 Composer Empty 时显示 `Loading...`，否则显示 `no matching history`。

## 118. Glossary（十二）：导航

- **Older/Newer direction**：Panel 向上是更旧、向下是更新。
- **No wrap**：到边界不跳到另一端。
- **Half-page navigation**：每次移动可见行数的一半。
- **Hover**：鼠标经过产生的视觉选中，不等于接受。
- **Scroll offset**：结果总列表中当前可见窗口的起点。

## 119. Enter 与 Tab 接受当前 Result

有 Selected Text 时关闭 Panel、写入 Prompt、Cursor 到末尾并 Clear File Search Context；没有结果则 Cancel 并恢复 Saved Text。

## 120. Accept `! ` Entry 恢复 Bash Mode

在非 Feedback/Remember Mode 中 Strip Prefix，把命令正文写入 Composer，并切 `PromptInputMode::Bash`。

## 121. 普通 Entry 可退出旧 Bash Mode

若当前 Mode 是 Bash 而 Entry 无 `! `，Accept 切回 Normal，避免普通 Chat Prompt 被当 Shell Command。

## 122. Feedback/Remember Mode 被保护

Accept 不用 Bash Prefix 覆盖这两个更具体的 Input Mode，也不会无条件重置它们。

## 123. Esc 或 Ctrl-C 是 Cancel

调用 `close_history_restoring_saved`，不是清空当前 Populated Text；Browse 还会把可能被 `! ` 切换的 Bash Mode 恢复为 Normal。

## 124. Browse 中普通字符表示 Detach-to-edit

Panel 关闭但保留当前 Populated Candidate，然后把该 Key 作为 TextArea Input 应用，用户可直接修改旧 Prompt。

## 125. Detach 后刷新 Slash

候选可能是 Slash Command；新字符编辑后重算 Dropdown，但 History Panel 已不再拥有输入。

## 126. Search 中普通字符只更新 Query

同一个 Key 写入 TextArea，再调用 `update_query`；直到 Accept，候选内容都不会替换 Query。

## 127. Shortcuts Help 会先关闭 History

打开 Help 前恢复 Saved Draft，避免 History Overlay 从 Popup 周围漏画，也避免 Esc 先恢复 Browse 而不是关闭 Help。

## 128. Glossary（十三）：接受、取消与脱离

- **Accept**：确认候选并把它变成普通 Composer Draft。
- **Cancel**：关闭 Panel 并恢复打开前 Saved Draft。
- **Detach-to-edit**：Browse Candidate 保留在 Composer，但退出 History 状态后继续编辑。
- **Mode restoration**：按 History Prefix 与原 Input Mode 恢复正确解释方式。
- **Input capture**：Panel Active 时所有 Key 先由它处理。

## 129. Browse 与 Search 的 Saved Text 用途不同

Browse 通常保存空 Draft，Cancel 回到未浏览状态；Search 保存打开时 Query/Draft，尽管 Typed `/history` 正常已清空，State API 仍支持通用恢复。

## 130. 外部编辑器回写会 Deactivate History

第 29 篇的 `apply_prompt_text` 先关闭 Search，再完整替换 Draft，防止 External Result 与 History Query/Selection 并存。

## 131. History Active 会阻止 External Editor

`any_dropdown_open()` 把它归入 OwnedElsewhere；外部编辑器不能在面板正拥有 Composer 时启动。

## 132. History Active 影响 Esc/Rewind 优先级

Esc 先关闭 Panel 并恢复 Draft，不会触发 Agent Cancel、Clear Prompt 或 Idle Rewind。

## 133. Paste/Suggestion 与 History 必须失效隔离

History Populate 后清 File Context；Panel Intercept 高于 Completion；Accept/Detach 后才允许 Slash/Suggestion 重新根据普通 Draft 工作。

## 134. Late Fetch 不应破坏 Browse Composer

Task Result 在 Browse Mode 只 Refresh Items，不把 Composer Candidate 当 Query 重发，也不自动重新 Populate；用户当前编辑视图保持稳定。

## 135. Glossary（十四）：子系统边界

- **Dropdown ownership**：History、File、Slash、Suggestion 中当前唯一有权解释导航键的面板。
- **Priority ordering**：多个输入子系统同时可能响应时的处理先后。
- **State isolation**：一个异步子系统更新时不污染另一个子系统的当前语义。
- **Saved draft**：面板打开前保存、Cancel 时还原的 Composer Text。
- **Late-refresh stability**：后台 Items 更新不打断用户当前 Selection 或 Candidate。

## 136. 手工推演：Fresh Prompt 后立即 Up

1. Pager Send 立即 Move-to-front 本地列表；
2. UI Push UserPrompt Scrollback；
3. Shell Append 可能仍在进行；
4. 用户在空 Composer 按 Up；
5. Combined History 先逆序扫描 Scrollback；
6. Fresh Prompt 成为 Index 0；
7. Fetched List 的旧重复被 Trim-key Seen 跳过；
8. Browse 同步 Populate Fresh Prompt；
9. 不依赖磁盘 Fetch 是否已完成。

## 137. 手工推演：`/history` 搜索

1. Slash Registry 解析 Builtin Command；
2. Slash Pipeline 清空 `/history`；
3. Dispatch 构建 Combined History；
4. Activate Search 保存 Empty Text、发送 SetItems；
5. Daemon 发布最多 100 个全量 Result，Newest 在 Bottom；
6. 用户输入 `auth fix`；
7. TextArea 成为 Query；
8. SetQuery 在 Worker 合并到最新；
9. Nucleo Score、Highlight、Reverse；
10. Tick Poll 新 Generation；
11. Best Match 在 Bottom Selected；
12. Enter 写回候选但不发送。

## 138. 手工推演：Browse 后修改

空 Prompt Up 后 Newest Candidate 已在 Composer。用户再按 Up 选择 Older，Composer Live Update；随后输入 `!` 之外的普通字符，Panel Deactivate，该字符追加到 Candidate，Slash State Refresh。此后 Up 不再 Browse，因为 Prompt 非空。

## 139. 手工推演：Late Fetch

用户已打开 Search、输入 Query、向上移动 Selection。`PromptHistoryLoaded` 到达，替换 Fetched List、与 Scrollback 重新合并，Daemon 收到 Items 与相同 Query 的原子更新；`last_query` 不变，因此 Sticky 不重新开启，Selection 只在超范围时 Clamp。

## 140. 常见误读一：Up 直接读磁盘

错误。Up 使用 Pager Combined History；磁盘只通过异步 ACP Fetch 更新内存列表。

## 141. 常见误读二：`/history` 搜索所有项目 Session

错误。Pager 请求携带 `filter_session_id`，默认只取当前 Session；无 ID 的 Extension Fast Path 才跨该 CWD 所有 Session。

## 142. 常见误读三：内存 200 是磁盘上限

错误。Pager Local Recall Cap 是 200；每 CWD JSONL Maintenance Cap 是 10000；Overlay Publish Cap 是 100。

## 143. 常见误读四：Browse 和 Search 只是不同入口

错误。它们对 Composer、Typing、Navigation、Late Refresh 与 Prefix Render 的语义均不同。

## 144. 常见误读五：History Dedup 都是同一规则

错误。JSONL Load 只 Consecutive Exact Dedup；Pager Local Record 做 Trim Move-to-front；Combined Merge 做全列表 Trim-key Dedup。

## 145. 调试“Up 找不到刚发送 Prompt”的顺序

1. Composer 是否真正 Empty；
2. Prompt Mode/Input Mode 是否 Normal；
3. File Search 是否可见；
4. History Daemon 是否 Available；
5. Scrollback 是否已有 UserPrompt Block；
6. Local List 是否记录 consume-input Send；
7. Combined History 是否 Scrollback-first；
8. Trim Key 是否意外 Empty；
9. 是否错误使用另一个 Agent/Session History。

## 146. 调试“搜索结果不更新”的顺序

1. History State 是否 Active 且非 Browse；
2. Key 是否被 History Intercept 捕获；
3. Composer Query 是否变化；
4. SetQuery Channel 是否发送；
5. Worker Thread 是否存在；
6. Message 是否 Coalesce 到期望 Query；
7. Snapshot Generation 是否递增；
8. App Tick 是否调用 `poll()`；
9. Query 是否实际没有 Match。

## 147. 调试“选择会跳回底部”的顺序

1. 用户 Navigation 是否把 `stick_to_bottom=false`；
2. `last_query` 是否被意外清空；
3. Late Fetch 重发的是相同 Query 还是新 Query；
4. Items 更新后 Selected 是否只 Clamp；
5. Mouse Hover 是否被误当 Click；
6. 是否真的输入了新字符——新 Query 设计上会重选 Best Match。

## 148. 调试“Bash History 变普通 Prompt”的顺序

1. Pager Local Record 是否保存 `! ` Prefix；
2. Shell Entry 是否标记 `is_bash`；
3. Combined History Text 是否仍含 Prefix；
4. Populate/Accept 是否进入 `strip_prefix("! ")`；
5. 当前是否 Feedback/Remember Mode；
6. Cancel 时是否恢复 Normal；
7. File Search Context 是否在 Populate 后清除。

## 149. 值得长期守住的测试不变量

- JSONL Append/Load Newest-first；
- 不存在文件返回 Empty；坏 JSON 行可跳过；
- Session Filter 与 Bash Filter 正确；
- 10000 Entry Truncate 保留最新并 Rename；
- Synthetic 与 Subagent Internal Prompt 不落盘；
- Pager Fetch 使用 `filter_session_id`；
- Fetch Error 结束 Loading；
- Skill XML 转换为 Display Text；
- Local Record Trim Move-to-front、Cap 200；
- Clear、Interject 与 Bash 都可 Recall；
- Scrollback-first 抗 Fresh Append/Fetch Race；
- Combined Merge Trim-key 全局去重；
- Up 只从 Empty Normal Composer 打开；Down 不打开；
- Browse 首项同步 Populate，Daemon 失败不半开；
- Empty Query Oldest-top/Newest-bottom；
- Query Best Match 在 Bottom，最多 100；
- Query Coalesce，Items+Query 原子刷新；
- Generation Poll 不阻塞 UI；
- User Navigation 关闭 Sticky；Same-query Late Fetch 不抢 Selection；
- Browse Navigation Live-populate，Typing Detach；
- Search Typing只改 Query；
- Esc/Ctrl-C 与 Down-at-newest 恢复 Saved Draft；
- Enter/Tab Accept 不发送；
- `! ` 恢复 Bash，普通 Entry 退出旧 Bash；
- History Intercept 高于 Slash/File/Completion；
- Mouse、Page Key、Scrollbar 和 Empty/Loading Render 正确。

## 150. 一句话记忆

Prompt History 是一条“双层索引、三段去重、两种交互模式”的 Recall 管线：Shell 把真实用户 Prompt 作为带 Session/Bash 元数据的 Entry Append 到每 CWD JSONL，以 10000 条和原子 Rename 做维护；Pager 用 Fast Scoped ACP 异步取回当前 Session，又把最多 200 条即时记录与最新 Scrollback UserPrompt 按 Trim Key 合并，让刚发送内容不受落盘竞态影响；History Daemon 在线程中用 Nucleo、Generation Snapshot 和 Query Coalescing 发布最多 100 个结果，Up Browse 把 Selection Live-populate 到 Composer，`/history` Search 则让 Composer 只做 Query，并通过 Saved Draft、Mode 恢复、Dropdown Priority 和 Late-refresh Selection 保护，做到快速、可取消、可编辑且永不自动发送。

## 151. 复习问题

1. Prompt History、Conversation History 与 Shell History 分别保存什么？
2. 为什么按 CWD 建独立 JSONL，而不是每次读取 Session Conversation？
3. Pager 为什么使用 `filter_session_id`，而不是 `session_id`？
4. JSONL、Pager Local List 与 Overlay 分别为什么是 10000、200、100？
5. 三处 Dedup 的规则为什么不同？
6. 为什么 Combined History 必须 Scrollback-first？
7. Fresh Prompt 如何在 Shell Append 尚未完成时仍能被 Up 找到？
8. Browse 与 Search 中 Composer 分别代表 Candidate 还是 Query？
9. 为什么 Up 只允许 Empty Normal Composer，而 Down 不作为入口？
10. Daemon 为什么使用线程、UTF-32、Message Coalescing 和 Generation Snapshot？
11. 为什么 Result 要 Reverse，让 Newest/Best Match 位于 Bottom？
12. `stick_to_bottom` 与 `last_query` 如何共同保护 Late Fetch Selection？
13. Browse 中输入普通字符为什么是 Detach，而 Search 中只是更新 Query？
14. `! ` Prefix 如何恢复 Bash Mode，又为什么保护 Feedback/Remember？
15. Esc、Ctrl-C、Down-at-newest、Enter 和 Tab 对 Saved Draft 各做什么？
