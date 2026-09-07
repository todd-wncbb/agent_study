# 源码精读 13：Agent Memory 如何存储、检索、注入、沉淀与隔离

> 本篇继续沿着 Agent Runtime 的基础链路下钻，精读 grok-build 的长期记忆系统：Memory 与 Chat History、Compaction Summary、`AGENTS.md` 到底有什么区别；Markdown 如何按 Global、Workspace、Session 和 Agent Scope 落盘；文件怎样被切块、增量索引并通过 FTS5 与 Vector KNN 混合召回；首轮注入和 `memory_search`/`memory_get` 工具如何把记忆送回模型；Flush、Session-end Hook 与 Dream 又如何把当前会话沉淀成跨会话知识。最后还会讨论路径校验、凭据作用域、过期提示、并发 Claim、临时目录和 Subagent 边界。
>
> 源码基线：`ed6d543`。源码变化后优先按本文列出的类型、函数和测试名重新定位。

---

## 1. 先给结论：Memory 是一条“异步知识循环”，不是加长的聊天记录

最容易产生的错误理解是：

```text
Memory = 把所有历史对话永远塞进 Prompt
```

grok-build 的实现更接近：

```text
当前会话
   |
   | Flush / Session-end Hook
   v
Markdown Session Logs
   |
   | Chunk + Index + Embedding
   v
SQLite FTS5 / sqlite-vec
   |
   | First-turn Injection / memory_search / memory_get
   v
未来会话的 Prompt
   |
   | Dream Consolidation
   v
Workspace MEMORY.md
```

它的核心不是“保存”，而是五个连续阶段：

1. **选择**：哪些会话内容值得长期保留。
2. **持久化**：以人可读 Markdown 写到什么 Scope。
3. **索引**：怎样把长文档变成可检索 Chunk。
4. **召回**：怎样综合关键词、语义、时间、来源和多样性。
5. **消费**：何时自动注入，何时让模型主动搜索与展开。

---

## 2. 本篇主线文件

| 文件 | 责任 |
| --- | --- |
| `xai-grok-config-types/src/memory.rs` | Index、Embedding、Search、Injection、Flush、Dream 等配置值与默认值 |
| `xai-grok-shell/src/config/mod.rs` | 多来源配置合并与 Memory 总开关 |
| `xai-grok-memory/src/storage.rs` | Global/Workspace Markdown 路径、读写、范围校验与 GC |
| `xai-grok-memory/src/chunker.rs` | Markdown Header/Paragraph/Line 感知切块与 Overlap |
| `xai-grok-memory/src/index.rs` | SQLite Schema、FTS5、sqlite-vec、增量 Reindex 与 Access 记录 |
| `xai-grok-memory/src/search.rs` | Keyword/Vector 合并、时间衰减、来源权重、Access Boost 与 MMR |
| `xai-grok-memory/src/backend.rs` | Tool/Injection 共用 Backend、Watcher Sync、Embedding 与 Search Telemetry |
| `xai-grok-memory/src/watcher.rs` | 外部 Markdown 修改的 Dirty Set |
| `xai-grok-memory/src/dream.rs` | Dream Gate、Prompt、输出质检、合并写入与 Session Cleanup |
| `xai-grok-memory/src/dream_lock.rs` | 多进程 Dream Lock 与最近 Consolidation 状态 |
| `xai-grok-tools/src/types/memory_backend.rs` | Tool 层面对 Memory 的抽象 Trait 与 Result 类型 |
| `xai-grok-tools/src/implementations/memory/search_tool.rs` | `memory_search` Tool |
| `xai-grok-tools/src/implementations/memory/get_tool.rs` | `memory_get` Tool |
| `xai-grok-shell/src/session/acp_session_impl/spawn.rs` | Session 启动时创建 Storage、Index、Watcher、Backend 与运行态 |
| `xai-grok-shell/src/session/helpers/memory_context.rs` | 首轮查询选择、`<memory-context>` 格式与重复注入判断 |
| `xai-grok-shell/src/session/acp_session_impl/turn.rs` | 首轮自动检索与 Reminder 注入入口 |
| `xai-grok-shell/src/session/helpers/memory_flush.rs` | Flush 阈值、窗口、响应质检与 Semantic Dedup |
| `xai-grok-shell/src/session/memory/hooks.rs` | Session 结束时的确定性 Metadata Summary |
| `xai-grok-shell/src/session/acp_session_impl/memory_dream.rs` | Dream Model Call 与 Session Actor 编排 |
| `xai-grok-shell/src/agent/subagent/handle_request.rs` | Agent Memory Scope、Prompt 注入与子 Session 配置 |

---

## 3. 先区分四种“让 Agent 记住东西”的机制

### 3.1 Chat History

当前 Session 的逐项 Conversation，通常包含 User、Assistant、Tool Call 与 Tool Result。

它最完整，但受 Context Window 限制，并且默认只服务当前会话。

### 3.2 Compaction Summary

当前长会话接近窗口上限时，对旧 Conversation 做的一次有损压缩。

它解决的是“同一个 Session 如何继续”，并不等同于跨 Session 的知识库。

### 3.3 `AGENTS.md`

项目或目录提供的规范性指令，例如构建方式、代码风格和作用域规则。

它回答“应该怎样做”，优先级和加载位置由 Prompt Assembly 决定；Memory 主要回答“过去发现过什么”。

### 3.4 Memory

跨 Session 的可检索、可编辑 Markdown 知识。

它可能是模型提炼的经验，也可能已经过时，所以检索结果包含 Source、Score、Path、Line Range 和 Staleness 提示，而不是被当作不可质疑的规则。

---

## 4. 四者的生命周期对比

| 机制 | 典型生命周期 | 是否完整 | 是否自动进入 Prompt | 主要用途 |
| --- | --- | --- | --- | --- |
| Chat History | 当前 Session | 相对完整 | 是 | 当前任务连续性 |
| Compaction Summary | 当前 Session 后半程 | 有损 | 是 | 窗口恢复 |
| `AGENTS.md` | 文件存在期间 | 人工维护 | 是 | 规范与约束 |
| Memory | 多个 Session | 选择性、有损 | 首轮少量注入，其余按需搜索 | 经验与长期事实 |

这个区分解释了为什么 Memory 不能替代任何一个机制。

---

## 5. Memory 的三条数据路径

为了读源码，可以把系统拆成三条相对独立的路径。

### 5.1 写入路径

```text
Conversation -> Flush / Session End -> sessions/*.md
sessions/*.md + MEMORY.md -> Dream -> workspace/MEMORY.md
```

### 5.2 索引与检索路径

```text
Markdown -> Chunk -> SQLite FTS5 + sqlite-vec -> Hybrid Ranking
```

### 5.3 Prompt 消费路径

```text
First User Query -> Initial Injection
Model Decision -> memory_search -> memory_get
```

分开阅读很重要：写入成功不代表已经入索引；入索引也不代表一定自动进入 Prompt。

---

## 6. 一个必须纠正的直觉：模型没有通用 `memory_write` Tool

当前模型侧注册的 Memory Tool 是：

- `memory_search`：搜索 Chunk。
- `memory_get`：读取命中文件的具体行。

二者都是 Read Scope。

源码中没有一个对等的、通用的 `memory_write` Tool 让模型随时直接修改长期记忆。写入由宿主编排的生命周期负责：

- Pre-compaction Flush。
- Idle/User-requested Flush。
- Session-end Metadata Summary。
- Dream Consolidation。
- Agent 自身拿到普通文件编辑工具时，对专用 Memory Directory 的显式文件编辑。

这个设计把“模型在推理时想搜索”与“系统何时允许形成长期事实”分开了。

---

## 7. 总开关如何解析

`MemoryConfig::resolve` 给出的优先级是：

1. `--no-memory`：绝对禁用。
2. `--experimental-memory`：启用。
3. `GROK_MEMORY`：`1/true` 启用，`0/false` 强制禁用。
4. 本地 TOML 的 `[memory]` 与 `[compaction]`。
5. Remote Settings。

其中 `--no-memory` 在最后再次把 `result.enabled` 设为 `false`，因此不会被前面的来源反向覆盖。

---

## 8. 为什么 Remote Settings 采用 Section-level 覆盖

若本地存在完整的 `[memory.search]` Section，Remote Search Settings 不再逐字段混入。

这是一个重要的配置语义：

```text
本地显式配置 Section -> 该 Section 由本地拥有
本地没有 Section       -> Remote 可以补默认
```

这样可以避免用户只改一个本地字段，却在不知情时得到一半本地、一半远端的 Search Policy。

---

## 9. 默认配置透露了产品取舍

关键默认值包括：

| 配置 | 默认值 |
| --- | ---: |
| `index.max_chunk_chars` | 1600 |
| `index.chunk_overlap_chars` | 320 |
| `search.max_results` | 6 |
| `search.min_score` | 0.35 |
| `search.vector_weight` | 0.7 |
| `search.text_weight` | 0.3 |
| Session Temporal Half-life | 7 天 |
| MMR | 关闭，`lambda = 0.7` |
| Initial Injection | 开启 |
| Session Save-on-end | 开启 |
| Watcher | 开启 |
| Dream | 开启，至少 4 小时且 3 个 Session |
| Flush Soft Headroom | 4000 Token |
| Flush Max Write | 8000 字符 |
| Semantic Dedup | 默认 0.92 |

可以看到系统默认偏向“小规模自动召回、长期文档稳定、Session 记忆随时间衰减”。

---

## 10. Storage 目录的真实布局

普通 Session 使用：

```text
~/.grok/memory/
├── MEMORY.md
└── {project-slug}-{hash8}/
    ├── MEMORY.md
    ├── index.sqlite
    └── sessions/
        └── YYYY-MM-DD-{slug}-{sid8}.md
```

这里 Workspace Directory 不是只有 Hash，而是：

```text
{可读项目名}-{Blake3 路径哈希前 8 位}
```

可读 Slug 方便人浏览，Hash 解决同名项目碰撞。

---

## 11. 为什么不把 Workspace Memory 写进仓库

普通 Workspace Memory 位于 `~/.grok/memory/`，主要收益是：

- 不污染 Git Working Tree。
- 不会误提交包含个人上下文的文件。
- 同一仓库可继续通过路径 Hash 找回。
- 索引数据库不进入项目。

项目级 Agent Memory 是另一套显式 Scope，后文单独讨论。

---

## 12. `MemoryStorage` 的四个核心字段

```text
global_dir      全局 Memory Root
workspace_dir   当前 Workspace 的存储目录
workspace_path  原始工作区路径
ephemeral       是否为临时工作区
```

`MemoryStorage` 自身不持有 SQLite Connection；它只负责路径和 Markdown I/O，因此可以廉价 Clone 并交给 Backend。

---

## 13. `new` 与 `new_flat` 的差异

`MemoryStorage::new` 会在 Root 下再加 Workspace Hash Directory。

`MemoryStorage::new_flat` 则把传入 Root 直接当作当前 Workspace Root，不再 Hash。

后者用于已经天然按项目隔离的 Agent Memory：

```text
<project>/.grok/agent-memory/<agent-name>/
```

若再 Hash 一次，会制造没有必要的双层目录。

---

## 14. 目录为什么 Lazy Create

构造 `MemoryStorage` 时不创建目录，首次实际写入才创建。

这样一个只启动、只读、随即退出的 Memory-enabled Session 不会在用户目录留下空目录，也降低了初始化失败的表面积。

---

## 15. 两个同名 `MemoryScope` 不要混淆

代码里存在两个不同概念：

### 15.1 Storage Write Scope

`xai_grok_memory::storage::MemoryScope`：

- `Global`
- `Workspace`

它回答“长期 Markdown 写到全局还是当前工作区”。

### 15.2 Agent Memory Directory Scope

`xai_grok_agent::config::MemoryScope`：

- `User`
- `Project`
- `Local`

它回答“某个 Agent Definition 的专属 Memory Root 放在哪里”。

源码注释专门强调二者不同，阅读时应始终带上所属 Crate。

---

## 16. Session Log 的文件名如何形成

路径格式：

```text
sessions/YYYY-MM-DD-{slug}-{sid8}.md
```

- Date 便于按时间排序。
- Slug 来自首个真实 User Query，方便人识别。
- `sid8` 来自 Session ID 前八位，避免同日同主题覆盖。

Session-end 写入用覆盖模式；Flush 可使用追加模式形成多个时间段。

---

## 17. Append 为什么增加 Timestamp 与分隔线

同一 Session 可能发生多次 Flush，例如 Idle Flush 和 Pre-compaction Flush。

Append 模式不是简单把两段 Markdown 粘在一起，而是加入时间标记和 `---`。这让后续 Dream 能辨别多个沉淀时点，也让人手工阅读时看出边界。

---

## 18. 临时目录为什么跳过 Workspace 写入

`MemoryStorage` 会识别 Temporary-directory CWD，并设置 `ephemeral`。

对临时 Checkout、测试目录或短生命周期沙箱写 Workspace Memory，通常只会产生：

- 大量很快失效的目录。
- 没有未来 Session 会复用的索引。
- GC 压力。

因此 Workspace 写入被静默跳过。这个标志只描述 Workspace 目标，不能粗略理解成“所有 Global 行为都必然消失”。

---

## 19. Source Classification

`classify_source` 将文件标成：

- Workspace 目录内且文件名为 `MEMORY.md`：`workspace`。
- Workspace 目录内的其他 Memory File：`session`。
- Global Root 下且不在当前 Workspace 子目录：`global`。

Source 不只是展示字段，后续还参与 Temporal Decay、Source Weight 和 Evergreen Supplemental Search。

---

## 20. `memory_get` 的路径安全边界

Storage 读取会：

1. Canonicalize 请求路径。
2. Canonicalize Global Memory Root。
3. 检查真实请求路径是否以真实 Root 开头。
4. 从 Canonical Path 读取。

因此 `../`、Symlink Escape 等不能把 `memory_get` 变成任意文件读取器。

注意“检查后读取的仍是 Canonical Path”这一细节，它减少了验证一个路径、读取另一个路径的风险。

---

## 21. Memory 文件为什么坚持 Markdown

Markdown 同时满足三类消费者：

- 用户可以直接阅读和编辑。
- Chunker 可以利用 Heading 与 Paragraph 结构。
- 模型天然理解 Topic Header、List 与 Code Fence。

SQLite 是派生索引，Markdown 才是持久化事实源。索引损坏时可以从 Markdown 重建。

---

## 22. 初始化模板与真正知识的区别

`ensure_initialized` 可能创建带提示语的 `MEMORY.md` Scaffold。

Dream 在构建输入时用 `is_scaffold_template` 识别短小且包含模板 Marker 的文档，不把它当作已有知识喂回模型。

判定同时要求：

- Trim 后小于 500 Bytes。
- 包含已知 Scaffold Marker。

长文档即使残留 Marker 也不会被误判为空模板。

---

## 23. Chunk 为什么是检索的基本单位

整份 `MEMORY.md` 直接作为一个 Vector 会把多个主题压进同一个表示；整份文档作为 FTS Result 又太长。

Chunk 在三个目标之间折中：

- 足够小，主题集中。
- 足够大，保留决策上下文。
- 保留原文件 Line Range，便于 `memory_get` 展开。

---

## 24. `Chunk` 的三个字段

```rust
text: String
start_line: usize
end_line: usize
```

其中 Line Range 是：

```text
[start_line, end_line)
```

即零基、左闭右开。

Tool 对用户显示时再处理一基行号，这个边界转换不能散落在 Storage 内部。

---

## 25. Markdown-aware Chunking 的四层策略

`chunk_markdown` 依次执行：

1. 空内容返回空 Chunk。
2. 全文不超过上限则返回一个 Chunk。
3. 按 `##` 及更深 Heading 切 Section。
4. 过大的 Section 按 Paragraph，再按 Line 切。

这比固定每 1600 字符硬切更能保住语义边界。

---

## 26. 为什么 `#` 也能成为 Header Context

源码的 Section 边界由 Heading Level 识别，注释强调 `##`，实现则能识别合法 Markdown `#...` Heading。

Header Stack 会维护祖先层级，例如：

```text
# Architecture
## Memory
### Search
```

切到 `### Search` 时，Continuation 可携带上层 Context，避免独立检索后只看见“Search”却不知道属于 Memory。

---

## 27. Header Context 的格式

祖先标题会被格式化成类似：

```text
[Context: # Architecture > ## Memory]

### Search
...
```

这段文本进入 FTS 与 Embedding，因此不仅帮助显示，也帮助召回。

---

## 28. Overlap 解决什么问题

过大 Section 被切成多个 Chunk 时，后一个 Chunk会携带前一个 Chunk 尾部最多 `chunk_overlap_chars` 个 Unicode Character。

它降低“关键条件在前一块末尾、结论在后一块开头”导致的语义断裂。

代价是：

- 索引中存在重复文本。
- 相邻命中可能高度相似。

这也是 MMR Diversity 有价值的原因之一。

---

## 29. Byte 上限与 Character Overlap 并不完全同单位

源码判断 Chunk 大小时大量使用 `String::len()`，即 UTF-8 Bytes；提取 Overlap 时使用 `.chars()`。

所以对中文等多字节文本：

- 1600 的大小上限更接近 1600 Bytes。
- 320 Overlap 是最多 320 Unicode Characters。

阅读注释中的“characters”时，应以具体实现为准。

---

## 30. Chunk Hash 的作用

每个 Chunk Text 用 Blake3 生成 Hash。

Reindex 时比较旧 Hash 与新 Hash，可以区分：

- 未变化：保留已有 Row 与 Embedding。
- 内容变化：更新 FTS，并删除/替换旧 Embedding。
- 新增：插入。
- 消失：删除。

这避免每次启动都重新请求 Embedding API。

---

## 31. Index 的两层检索结构

`MemoryIndex` 维护：

1. **FTS5**：关键词、短语和 BM25 排名。
2. **sqlite-vec**：Embedding Vector 的 KNN Search。

Vector Extension 不可用时，系统仍保留 FTS5 能力，不把 Memory 整体判为不可用。

---

## 32. 为什么 SQLite Connection 不放进共享 Backend

`MemoryBackendImpl` 只保存 Path、Storage 与配置；每次 Query 重新打开 `MemoryIndex`。

源码注释指出 `MemoryIndex` 可 `Send` 但非 `Sync`，内部 `rusqlite::Connection` 也不适合让多个异步查询通过共享引用跨 `.await` 使用。

Open-per-query 的约 1ms 成本换来了更简单的并发所有权。

---

## 33. Sync/Async/Sync Phase 是 Rust 所有权驱动的架构

Search 被明确拆成：

```text
Sync 1: 打开 Index、Watcher Reindex、收集待 Embed Chunk
Async : 调 Embedding API
Sync 2: 回写 Embedding、FTS Search
Async : Embed Query
Sync 3: Vector Search、Merge、Record Access
```

原则是：任何 `.await` 前都不持有 `&MemoryIndex` Borrow。

这不是纯粹代码风格，而是为了让整个 Future 保持 `Send`。

---

## 34. sqlite-vec 初始化为什么用全局 Once

Vector Extension 的注册是进程级动作，重复初始化没有意义，还可能造成 ABI/版本不一致。

源码使用一次性初始化，并对打包版本有固定预期；如果 Extension 不可用，则创建 FTS-only Index。

---

## 35. Embedding Dimension 为什么写进 Meta

Vector Table 的列宽与 Embedding Dimensions 强绑定。

若配置从 1024 改成其他维度，旧 Vector 无法与新 Query Vector 比较。Index 检测维度不匹配后重建 Vector Table，而不是把不兼容数据继续使用。

---

## 36. `reindex_file` 的事务边界

单文件 Reindex 会在 Transaction 中同步修改：

- Chunk Metadata。
- FTS Row。
- 已消失或已变化 Chunk 的 Embedding。

目标是避免“Chunk Table 已更新，但 FTS 还是旧内容”这种中间状态被搜索看到。

---

## 37. Chunk ID 为什么包含 Path 与 Index

逻辑形式是：

```text
{path}:{chunk-index}
```

它让同一文件的 Chunk 顺序稳定可定位，也让 File Delete 能按 Path 清理全部 Chunk。

内容变化是否需要重算 Embedding则由 Hash 决定，而不是只靠 ID。

---

## 38. Watcher 的职责非常克制

Watcher 不直接在文件事件线程里 Reindex。

它只把 Create/Modify/Delete 路径合并进 Dirty Set；下一次 `memory_search` 才尝试同步。

这样避免：

- 文本编辑器一次 Save 触发多次昂贵操作。
- Watcher Callback 中执行网络 Embedding。
- Index 与 Session Actor 形成复杂锁关系。

---

## 39. 为什么 Sync-on-search

用户手工修改 `MEMORY.md` 后，最需要新内容的时刻就是下一次 Search。

Search 前同步提供了“按需一致性”：

- 不追求文件一变 Index 就立即一致。
- 但尽量保证查询看到最新内容。

这是典型的 Lazy Synchronization。

---

## 40. Reindex Claim 防什么

多个 grok-build Session 可能共享同一个 Workspace Index，并同时观察到 Dirty File。

若都开始 Reindex 和 Embed，会产生 Stampede。

SQLite Claim 让一个进程获得本轮工作权；Claim 超过 `stale_claim_secs` 后可被回收，避免进程 Crash 后永久锁死。

---

## 41. Delete Event 为什么不能调用普通 Reindex 就结束

文件已经不存在时，普通读取会失败。

如果只忽略错误，旧 Chunk 会永远留在 Index，产生“幽灵记忆”。因此 Backend 对不存在的 Dirty Path 明确调用 `delete_path`。

这是 Watcher-based Index 常见但容易遗漏的边界。

---

## 42. Missing Embeddings 怎样补齐

Reindex 后 Backend 查询没有 Vector 的 Chunk，并按 32 个一批调用 Embedding Provider。

每批失败只记录 Warning 并跳过，不阻断 FTS Search；成功结果再逐个 Upsert。

因此系统允许一个 Index 暂时处于：

```text
所有 Chunk 有 FTS
部分 Chunk 有 Vector
```

---

## 43. `MemoryBackendParams` 为什么存在

Tool Search、First-turn Injection 和 Post-compaction Recovery 都要构造 Backend。

若各自手写 Builder Chain，很容易出现：

- Tool 有 Embedding，Injection 退化成 FTS-only。
- 一条路径忘记 Search Config。
- Telemetry Source 混在一起。

`MemoryBackendParams` 把 Session ID、Embedding、Credentials、Search Config、Watcher、Claim TTL 与 Source Label 放在一个可 Clone 参数包中。

---

## 44. Search Source Label

Backend Telemetry 区分：

- `tool`
- `injection`
- `compaction_recovery`

同样的检索算法被不同运行路径调用时，指标仍能回答“是谁触发了搜索”。

---

## 45. Endpoint-scoped Credentials 为什么重要

Embedding API 需要认证，但 Session Credentials 不能因为配置了任意 Base URL 就被转发出去。

Backend 在运行时检查 Credential Scope 与实际 `base_url` 是否匹配：

- 匹配：允许动态 Auth 或 API Key Provider。
- 不匹配：Fail Closed，丢弃受作用域保护的 Credential。
- 独立显式配置的 Embedding Key 仍可按自己的路径使用。

这道检查存在于 Release Runtime，不依赖 `debug_assert!`。

---

## 46. Credential Debug 为什么手工实现

凭据参数可能持有 Auth Manager 或 API Key Provider Handle。

手工 Debug 只展示安全的状态与 Endpoint，不展开 Secret。对承载认证信息的配置对象，派生 `Debug` 往往不是安全默认。

---

## 47. Hybrid Search 的候选阶段

Search 首先取：

```text
candidate_limit = max_results * 3
```

候选来源包括：

- 普通 FTS Query。
- 仅限 Global/Workspace 的 Supplemental Evergreen FTS Query。
- 若可用，则 Query Embedding 的 Vector KNN。

先扩大候选再统一排序，给 Temporal、Source Weight 和 MMR 留出调整空间。

---

## 48. 为什么额外查询 Evergreen Source

Session Logs 会快速增长，普通 FTS Top N 可能被近期 Session Chunk 挤满。

Global/Workspace `MEMORY.md` 是人工或 Dream 整理出的长期知识，密度更高。Supplemental Query 确保它们至少进入候选池，再与其他结果统一竞争。

这不是直接保证它们进入最终 Top 6，而是防止在候选阶段被流量淹没。

---

## 49. FTS Score 怎样归一化

FTS5 的 BM25 原始分数方向和尺度不适合直接与 Vector Similarity 相加。

Search 层把最佳 Keyword Match 归一到约 1.0，再与 `[0,1]` 的 Vector Similarity 组合。

归一化使 Config Weight 表达的是相对偏好，而不是被两个引擎的任意尺度支配。

---

## 50. Vector Distance 怎样变成 Similarity

对归一化 Vector，源码采用：

```text
similarity = clamp(1 - L2_distance / 2, 0, 1)
```

`2` 是单位向量间的最大相关距离尺度。

同一转换也用于 Flush Semantic Dedup，因此 Search 与 Dedup 对“接近”的解释保持一致。

---

## 51. 两个 Signal 如何合并

概念上：

```text
hybrid = text_weight * fts + vector_weight * vector_similarity
```

但实现还保护强 Keyword Match：当两种信号都存在时，不让加权组合把纯 FTS 强匹配反而压低；只有 Vector 的候选则按 Vector Weight 缩放。

这说明 Weight 不是一个天真的线性公式，而带有 Keyword Floor。

---

## 52. Embedding 失败为什么退回 FTS-only

失败可能来自：

- 没配置 Model。
- 没有 Credential。
- Endpoint Scope 不匹配。
- API 暂时失败。
- sqlite-vec 不可用。

这些情况都不应让 Markdown Memory 完全不可搜索。Backend 把 `search_mode` 记录为 `fts_only`，继续返回 Keyword Result。

这是 Graceful Degradation，而不是静默假装 Hybrid 仍然工作。

---

## 53. Temporal Decay 只作用于 Session Chunk

默认公式：

```text
decayed_score = base_score * exp(-ln(2) * age_days / half_life_days)
```

默认 Half-life 是 7 天。

Global 与 Workspace 被视为 Evergreen，不做时间衰减，因为它们已经经过人工或 Dream Curate。

---

## 54. 为什么“过期提示”和“时间降权”同时存在

二者解决不同问题：

- Temporal Decay 决定结果是否容易进入 Top N。
- Staleness Note 告诉模型即使它命中了，也应验证事实。

Session Memory 超过一天会出现轻度验证提示，超过七天出现更强警告；Global/Workspace 不显示这种 Session Staleness。

---

## 55. Source Weight

配置可给 `global`、`workspace`、`session` 各自乘权。

它提供部署级偏好，例如更相信 Curated Workspace Memory，或让近期 Session Discovery 更容易浮现。

默认三者都是 1.0，因此默认行为不暗中偏置来源。

---

## 56. Access Boost

返回过的 Chunk 会记录 Access Count 与 Last Accessed。

排名中使用近似：

```text
1 + ln(1 + access_count) * 0.05
```

Logarithm 让常用知识获得温和提升，但不会因重复命中几十次而无限统治结果。

Access 记录失败是 Non-fatal，不影响已经算出的 Search Response。

---

## 57. 为什么排序用 Raw Score，展示用 Clamped Score

内部多个 Boost 相乘后可能超过 1。

如果排序前先 Clamp，大量候选会一起变成 1.0，失去相对顺序。因此实现：

```text
Raw Score -> 排序
Clamped [0,1] -> Threshold / 展示
```

这是一种常见的 Ranking/Presentation 分离。

---

## 58. MMR 解决重复结果

MMR 即 Maximal Marginal Relevance：

```text
MMR(d) = λ * relevance(d)
       - (1 - λ) * max_similarity(d, selected)
```

实现使用 Tokenized Snippet 的 Jaccard Similarity 衡量结果间重复。

默认关闭；开启后 `lambda=0.7`，偏相关性，同时对相邻 Overlap Chunk 和同主题重复 Summary 做惩罚。

---

## 59. Content-free Chunk 为什么在 Search 时再过滤

初始化模板、只有 Header 的 Section 或没有实际信息的 Chunk 可能进入 Index。

Search-time Filter 阻止它们占用最终 Result Slot。即使历史 Index 中已有这些 Row，也能立即修正用户体验，不必等待完整 Reindex。

---

## 60. Tool 层为什么定义 `MemoryBackend` Trait

`xai-grok-tools` 不应依赖 Shell Session 的 SQLite 构造细节。

Trait 只暴露：

```text
async search(query, max_results, min_score)
sync  get(path, from, lines)
sync  total_chunks()
```

Tool 只面向能力编程；实际 `MemoryBackendImpl` 由 Shell 创建并放入共享 Resources。

---

## 61. Registry Candidate 与 Runtime Resource 是两层 Gate

Memory Tool Implementation 可以作为 Registry Candidate 存在，但实际执行还需要 Resources 中的 `Arc<dyn MemoryBackend>`。

Memory 关闭时：

- Agent Finalization 可以不暴露对应 Tool。
- 即使错误调用到 Implementation，也会得到明确的 Disabled Message，而不是 Panic。

候选注册与运行依赖注入不应混为一谈。

---

## 62. `memory_search` 的输入默认值来自哪里

Tool 参数可以传：

- Query。
- Max Results。
- Min Score。

未传的值由 Backend/Search Config 决定，默认通常是 6 和 0.35，而不是让 Tool Implementation 自己维护另一套常量。

统一默认值避免 Initial Injection、Tool Search 和 Remote Config 各说各话。

---

## 63. `memory_search` 的输出为什么包含这么多元数据

每条 Result 包含：

- Score。
- Source。
- Path。
- Line Range。
- Staleness Note。
- Fenced Snippet。

模型需要的不只是“答案文本”，还需要判断：

- 它来自长期 Curated Memory 还是旧 Session。
- 是否值得进一步 `memory_get`。
- 是否需要回到源码或当前工作区验证。

---

## 64. `memory_get` 为什么是第二阶段 Tool

Search Snippet 有长度限制，只负责发现相关 Chunk。

`memory_get` 再按 Path 和 Line Range 展开原文，形成两阶段 Retrieval：

```text
Search 广召回 -> Get 精确读取
```

这样不必把完整 Memory File 放入每次 Tool Result。

---

## 65. 行号基准转换

Tool 的 `from` 对模型是 1-based；Storage Backend 使用 0-based。

实现通过 `saturating_sub(1)` 转换，因此 `from=0` 也会安全地落到第一行，而不是整数下溢。

输出时每行带可见 Line Number 与箭头，帮助模型继续请求相邻范围。

---

## 66. 首轮自动注入解决什么问题

如果完全依赖模型主动调用 `memory_search`，模型首先必须意识到“过去可能有相关知识”。

Initial Injection 在当前 Session 的第一次真实任务前主动执行一次 Search，把少量高相关结果作为 `<memory-context>` Reminder 放进 Prompt。

它是 Recall Bootstrap，不是把整个知识库自动加载。

---

## 67. Initial Injection 的一次性 Latch

Session Runtime 有 `context_injected: AtomicBool`。

Turn 入口发现它已为真就直接返回；第一次检查时会先置真，因此即使配置关闭、没有结果或 Search 失败，也不会在每个 Turn 重试。

这是“每个 Session Segment 至多决策一次”的 Process-level Gate。

---

## 68. 为什么还要检查首个 System Item 的 Marker

只有 Atomic Latch 不足以处理恢复、Compaction 或 Segment 重建。

`conversation_has_memory_context` 检查 Conversation 第一个 System Item 是否已包含 `<memory-context>`：

- 已存在：复用原来的 Block。
- 不存在：才允许搜索与注入。

这是 Persisted-history-level Idempotency。

---

## 69. 重复搜索为什么会破坏 Prompt Cache

同一个 Query 在不同时刻可能得到：

- 不同排序。
- 不同 Score。
- 新增 Session Chunk。
- 不同时间戳或 Staleness。

如果恢复 Session 时重搜并改写开头 System Prompt，整个后续 Prefix 都变化，Provider KV Cache 无法复用。

所以源码宁可复用已经持久化的 `<memory-context>` 文本，也不追求每次恢复都最新。

---

## 70. 首轮 Search Query 如何选择

正常情况使用最后一个真实 User Query。

以下情况改用通用 Query：

```text
project conventions preferences architecture
```

- 没有真实 Query。
- Query 少于 20 Bytes。
- Query 是 Greeting/Continue/Start 等短启动语。

这是避免用“好”“继续”“hello”检索出无意义结果。

---

## 71. Greeting Fallback 是启发式，不是语义模型

实现会做小写、Trim 和标点归一，并匹配有限 Greeting 集合。

它的目标不是理解所有自然语言寒暄，而是覆盖最常见的 Session Start。未命中的短文本仍会被 `<20 Bytes` Gate 捕获一部分。

---

## 72. Initial Injection 的 Score Threshold 为什么默认 0

普通 Tool Search 默认 `min_score=0.35`。

Initial Injection 为兼容历史行为，`min_score` 未配置时使用 0.0，而不是继承普通 Search Threshold；配置显式 Override 后才使用指定值。

这让首轮 Bootstrap 更重 Recall，但仍受最多 6 条和 Snippet Cap 约束。

---

## 73. Initial Injection Result 如何限长

每条 Snippet 最多 500 个 Unicode Character。

最多 6 条时，Memory Block 的主体仍有明确上界，不会因为一个巨大 Chunk 吃掉首轮 Prompt。

截断按 `.chars()` 执行，避免切坏 UTF-8。

---

## 74. `<memory-context>` 为什么是 Reminder 而非权威 System Rule

注入文本会标明它是 Past Sessions 的 Relevant Memory，并带 Source、Score 与 Staleness。

正确阅读方式是：

```text
这是可能有帮助的历史证据，需要结合当前代码验证
```

而不是：

```text
这是不可覆盖的当前项目指令
```

---

## 75. Compaction Recovery 为什么复用同一 Backend Factory

Compaction 可能丢掉一部分细节，恢复 Reminder 可以再次查询 Memory。

这一搜索路径只修改 `search_source` 为 `compaction_recovery`，其余 Embedding、Watcher、Credentials 与 Search Config 均来自同一 Params。

这是减少多条 Runtime Path 配置漂移的实例。

---

## 76. Memory Flush 是什么

Flush 是一次额外 LLM Call，把最近 Conversation 提炼成适合跨 Session 保存的 Markdown。

它与 Compaction Summary 的差异是：

- Flush 面向未来 Session 的 durable knowledge。
- Compaction 面向当前 Session 的 continuation state。
- Flush 写入 Memory File。
- Compaction 替换 Chat Conversation。

---

## 77. Flush 的触发来源

源码路径覆盖：

- Pre-compaction。
- Idle Timeout。
- 用户显式请求，例如 `/flush`。
- 其他生命周期入口可带 Trigger Label。

所有来源经过同一个 `is_flushing` Atomic Gate，避免并发 Flush。

---

## 78. Pre-compaction Flush Threshold

概念公式：

```text
flush_boundary = compact_boundary - soft_threshold_tokens
```

默认在自动压缩阈值前预留 4000 Token，先把即将被压缩的细节沉淀到 Memory。

阈值判断使用共享的比例缩放整数算法，避免 Float Boundary Drift。

---

## 79. 为什么同一 Compaction Cycle 只 Flush 一次

Session Memory 记录：

- 当前 Compaction Cycle。
- 上次成功/尝试 Flush 所属 Cycle。

如果每轮 Tool Result 都超过 Soft Boundary 而反复 Flush，会制造额外成本与重复 Memory。Cycle Gate 把它限制成一次前置动作。

---

## 80. Flush 期间为什么抑制自动 Compaction

Flush 自己也是一次模型请求，可能暂时提高运行态 Token 计数。

若自动 Compaction 同时触发，两个状态迁移会争用 Conversation Snapshot。`is_flushing` 被 Compaction Trigger 检查，用来维持操作顺序：

```text
先 Flush -> 完成或失败 -> 再 Compact
```

---

## 81. Flush Model 看见哪些消息

Conversation 先被简化成 Chat Request Message，然后：

- 去掉原 System Message，因为 Flush 自己有专用 System Prompt。
- 默认取最近 20 条。
- 若窗口起点落在 Assistant/Tool Result，会向前扩到最近 User Boundary。

所以返回窗口可能略多于 20 条，但不会从孤立的 Tool Result 开始。

---

## 82. 首次 Flush 与 Delta Flush

第一次使用完整 `FLUSH_SYSTEM_PROMPT`。

若 Session 已有上次 Flush Content，后续 Prompt 会附上 Previous Output，并要求只写新增且真正有价值的内容。

这比每次重新总结同一个 Conversation Window 更能减少重复。

---

## 83. Flush Prompt 要保留什么

Prompt 强调：

- 决策及其理由。
- 技术上下文与架构。
- Debug 方法与工具。
- 问题/解决方案。

并要求排除：

- 普通寒暄。
- 短期进度噪声。
- 没有跨 Session 价值的细节。
- 已无新增价值时返回 `NO_REPLY`。

这是“长期知识选择器”的 Policy。

---

## 84. Flush Response 的四道质检

`process_flush_response` 依次：

1. 空或纯空白 -> `NothingToStore`。
2. 匹配 `NO_REPLY` -> `NothingToStore`。
3. 超过 `max_flush_write_chars` -> 按 Unicode Character 截断。
4. 没有 Markdown Heading -> `Rejected`。

只有 `Accepted(String)` 才进入 Semantic Dedup。

---

## 85. 为什么必须有 Markdown Header

Dream 和 Chunker 都依赖结构化 Topic。

一段没有 Heading 的长散文即使内容正确，也更难：

- 按主题切块。
- 与旧 Memory 合并。
- 由人快速浏览。

因此结构不是装饰，而是下游协议。

---

## 86. Semantic Dedup 的完整过程

对 Accepted Flush Content：

1. 检查 Embedding Provider 与 Vector Index 是否可用。
2. Embed 整段候选内容。
3. KNN 查询最近 3 个现有 Chunk。
4. 将 L2 Distance 转为 Similarity。
5. 任一 Similarity 大于 Threshold 即判重复。

默认 Threshold 是 0.92，可由配置覆盖并 Clamp 到 `[0,1]`。

---

## 87. Dedup 为什么失败时允许写入

Embedding 和 Vector Search 是增强能力，不是 Memory Durability 的单点依赖。

若 Provider 不可用、Vec Extension 缺失或查询失败，`is_semantically_duplicate` 返回 `false`，即“未证明重复”。

这是 Availability-first 取舍：可能多写一点重复内容，但不会因搜索基础设施故障而丢失唯一知识。

---

## 88. Flush 写到哪里

通过质检且不重复的内容追加到当前 Session Daily Log，而不是直接覆盖 Workspace `MEMORY.md`。

这样：

- 原始提炼结果按 Session 留痕。
- 多次 Flush 不互相覆盖。
- Dream 之后再承担跨 Session 去噪与合并。

---

## 89. Flush 后为什么立即 Reindex

写入成功但不 Reindex，会导致同一 Session 后续 `memory_search` 看不到刚保存的内容。

因此 Flush 后执行 Reindex，并在 Provider 可用时补 Embedding。写入是 Source-of-truth Commit，索引更新是派生状态同步。

---

## 90. Session-end Hook 为什么不用 LLM

关闭 Session 的路径应快速、可靠，可能发生于 Channel Close 或 SIGTERM Handling。

`on_session_end` 因此生成确定性 Metadata Summary：

- User/Assistant/Tool Result 数量。
- UTC Date。
- 前几个真实 User Topic。

没有网络调用，也不会让 Shutdown 等待一次新采样。

---

## 91. Session-end 的最小内容 Gate

必须同时满足：

- 至少 3 个真实 User Prompt。
- 真实 Query 总计至少 50 Bytes。
- `save_on_end = true`。

它排除“hi / ok / thanks”一类技术上有多条消息、实际没有长期价值的 Session。

---

## 92. 什么是“真实 User Query”

源码复用 Compaction Helper 排除：

- 启动时包含 `<user_info>`、`<git_status>` 的 Synthetic Prefix。
- 内部 `__auto_continue__` Sentinel。

否则 Session Slug 与 Topic 很可能变成机器注入的环境元数据，而不是用户实际任务。

---

## 93. Session-end Summary 为什么只能算兜底

它只保存前 5 个 User Topic，各最多 100 个 Unicode Character，以及数量元数据。

它不会理解：

- 最终采用了哪个架构。
- 哪个 Bug Root Cause 被证实。
- 哪种尝试失败。

所以丰富知识依赖 Flush；Session-end Hook 保证即使没触发 Flush，也至少留下可检索线索。

---

## 94. Dream 是什么

Dream 是对多个 Session Log 的反思式 Consolidation：

```text
多个 sessions/*.md
      +
已有 workspace/MEMORY.md
      |
      v
Dream Model
      |
      v
新的 workspace/MEMORY.md
```

它不是简单 Concatenate，而是要求 Merge、Resolve Contradiction、绝对化相对日期并移除短期噪声。

---

## 95. Dream Gate 的检查顺序

`check_dream_gates` 按便宜程度检查：

1. `dream.enabled`。
2. 距离上次 Consolidation 是否达到 `min_hours`。
3. 新 Session 数是否达到 `min_sessions`。

默认是至少 4 小时且至少 3 个 Session。

只有 Gate Open 后，Caller 才尝试获取 Lock。

---

## 96. `/dream` 与自动 Dream 的区别

用户显式 `/dream` 可以绕过时间和 Session 数 Gate，但仍要遵守关键安全和并发边界，例如 Lock、输入构建、输出质检与文件清理规则。

“手动强制”不是“跳过所有正确性检查”。

---

## 97. Dream Lock 防什么

多个共享同一 Memory Root 的 Session 可能同时满足 Gate。

没有 Lock 会发生：

- 两个 Model Call 基于同一旧 `MEMORY.md`。
- 后写结果覆盖先写结果。
- 两边重复删除 Session Log。

Lock 支持 Stale Reclaim，默认一小时，处理持锁进程 Crash。

---

## 98. 当前 Session 为什么从 Dream 输入中排除

正在运行的 Session Log 可能还在追加。

Gate 枚举 `sessions_since` 时可接收当前 `sid8` 并排除它，避免 Consolidate 一份尚未稳定的文件。

这也与后面的 Cleanup Recency Guard 共同保护并发 Session。

---

## 99. Dream Input 的 32K Character Cap

`MAX_DREAM_INPUT_CHARS = 32_000`。

已有 Workspace Memory 最多占一半 Budget；Session Logs 逐个加入，达到 Cap 后停止。

返回值不仅有 Prompt Content，还有真正读入的 `processed_stems`。超过 Cap 没被读入的 Session 不会在成功后被删除，留给下次 Dream。

---

## 100. 为什么 Existing Memory 要和新 Session 一起输入

若只让模型总结新增 Session 并覆盖 `MEMORY.md`，早期长期知识会在每轮 Dream 消失。

Prompt 明确要求：

- Merge 旧知识与新知识。
- 新 Session 推翻旧事实时只保留当前 Truth。
- 保留决策、理由、架构、偏好与问题/解法。

Dream 是 Successor Document 生成，不是只总结 Delta。

---

## 101. Dream Output 的质检

输出必须：

- 非空。
- 不是 `NO_REPLY`。
- 含 Markdown Header。
- 最多 16,000 Unicode Character。

通过后直接作为新的 Workspace `MEMORY.md`，保留模型生成的 Markdown 结构。

---

## 102. Dream 的 Session Cleanup 为什么只删“实际处理过”的文件

`sessions_eligible` 可能大于因 32K Cap 真正读入的数量。

Cleanup 只接受 `processed_stems`，并返回真正成功删除的 `cleaned_stems`。Index Cleanup 也只对后者执行。

这条数据链防止“统计上符合 Gate，却从未进入 Prompt 的 Session”被误删。

---

## 103. 五分钟 Recency Guard

即使一个 Stem 在 Dream Input 中，删除前仍检查文件 Mtime。

最近 300 秒内修改的文件不删除，因为另一个并发 Session 可能正在追加。Consolidation 已成功时，单个删除失败也只 Warning，不回滚新的 Workspace Memory。

---

## 104. Dream 后的 Index 修复顺序

成功后需要：

1. Reindex 新的 Workspace `MEMORY.md`。
2. Embed 新增/变化 Chunk。
3. 对真正删除的 Session File 执行 `delete_path`。

否则 Search 会同时看到 Consolidated Memory 与已经删掉的 Session Duplicate。

---

## 105. Dream Model Call 的超时事实

Session 编排代码给 Dream Call 的实际 Timeout 是 30 分钟。

若附近注释仍写着较短旧值，应以 Timeout 构造代码为准。这是读源码时区分“注释意图”和“当前执行事实”的典型例子。

---

## 106. Subagent 为什么默认不执行 Dream

Session-end Dream 路径显式跳过 Subagent。

否则短生命周期 Child Agent 可能：

- 与 Parent 同时 Consolidate。
- 用局部任务视角改写共享长期 Memory。
- 删除 Parent 仍可能依赖的 Session Log。

子 Agent 可以参与检索或写专属 Agent Memory，但不拥有全局 Consolidation 权。

---

## 107. Agent Definition 的三种 Memory Scope

`xai_grok_agent::config::MemoryScope` 解析为：

| Scope | 路径 | 是否已 Project-scoped |
| --- | --- | --- |
| `user` | `~/.grok/agent-memory/<name>/` | 否 |
| `project` | `<project>/.grok/agent-memory/<name>/` | 是 |
| `local` | `<project>/.grok/agent-memory-local/<name>/` | 是 |

`project` 适合可共享项目知识；`local` 路径语义上适合本机项目知识；`user` 可跨项目，但普通 Storage 仍可在其下增加 Workspace Hash。

---

## 108. Agent Memory 怎样进入 Subagent Prompt

若 Agent Definition 配置了 Memory Scope，启动前会读取专属目录的 `MEMORY.md`：

- 最多 200 行。
- 最多 25 KiB。
- 非空才注入。

格式为：

```xml
<agent-memory>
Memory directory: ...

...
</agent-memory>
```

目录也被明确告诉模型，便于使用普通文件工具维护专属 Memory。

---

## 109. 为什么 Agent Memory 会补文件编辑工具

配置 Agent Memory Scope 时，Subagent Definition 会确保具备 Read、Search/Replace 与 Write 类文件工具。

这里的“写 Memory”不是 `memory_write` 专用 Tool，而是 Agent 在被授权的专属目录里使用普通文件工具编辑 `MEMORY.md`。

这再次说明 Retrieval Backend 与 Agent-owned Markdown Maintenance 是两条不同能力路径。

---

## 110. Project/Local Scope 为什么使用 Flat Root

它们的路径已包含 Project 和 Agent Name：

```text
<project>/.grok/.../<agent-name>/
```

Subagent Session Clone Parent Memory Config 后设置：

```text
root_dir_override = resolved path
flat_memory_root = true
enabled = true
```

Spawn 时据此选择 `MemoryStorage::new_flat`。

---

## 111. User Scope 为什么仍可能再按 Workspace 分层

User Agent Memory Root 只由 Agent Name 隔离，尚未绑定某个项目。

它走普通 `new`，在 `~/.grok/agent-memory/<name>/` 下再建立 Workspace Hash，防止同一个 Agent 在多个项目中的 Session Memory 串在一起。

---

## 112. Parent 与 Child 的 Memory 继承逻辑

若 Child Agent 没有自己的 Memory Scope，它通常继承 Parent Session 的 Memory Config。

若有自己的 Scope，则 Clone Parent 的搜索、Embedding、Flush 等配置，但替换 Root 与 Flat Mode。

所以隔离的是 Storage Namespace，不是复制一套完全独立的算法。

---

## 113. Memory 隔离并不是“绝对事实隔离”

边界包括：

- Workspace Path Hash。
- Agent Name Directory。
- Project/Local/User Scope。
- Canonical Path Read Guard。
- Dream 跳过 Subagent。

但如果两个 Session 被配置到同一 Root，它们就是有意共享 Memory。系统提供命名空间与并发协调，不会凭空理解哪些业务事实不该共享。

---

## 114. Garbage Collection 处理什么

Session 初始化会异步扫描 Orphan Workspace Directory：

- 空 `tmp*` 可直接移除。
- 非空 `tmp*` 超过 7 天可移除。
- 没有 Session File 的普通 Workspace 超过 `max_age_days` 可移除。
- 非空且有 Session 的普通 Workspace 不碰。

默认 `max_age_days = 30`。

GC 被放到 Blocking Task，避免目录扫描阻塞 Session Actor。

---

## 115. 为什么 Memory 操作普遍 Best-effort

Memory 是增强 Agent 连续性的能力，不应成为主任务的单点故障：

- 初始化失败：Warning，Session 继续。
- Embedding 失败：FTS-only。
- Access 记录失败：忽略。
- Flush 失败：不阻止 Compaction。
- Session-end Save 失败：不阻止 Shutdown。
- Dream Cleanup 单文件失败：不回滚 Consolidation。

Best-effort 不等于无观测；各路径仍记录日志和 Telemetry。

---

## 116. Telemetry 关注哪些指标

Search 侧记录：

- Source Label。
- Query Length 与 Keyword Count。
- Result Count / Empty Search。
- Top Score。
- Search Mode。
- Vector Availability。
- Duration。

Watcher 记录 Dirty File、Changed Chunk、Embedded Count 与 Claim 状态。

Session Runtime 还聚合 Injection、Flush、Dream 与 Search Counter，便于判断 Memory 是“启用了”还是“真正发挥作用了”。

---

## 117. 一次普通新 Session 的完整时序

```text
Session Spawn
  -> resolve MemoryConfig
  -> construct MemoryStorage
  -> ensure templates
  -> open/create index
  -> optional watcher
  -> inject Backend into Tool Resources

First Turn
  -> choose real query or fallback query
  -> sync dirty files
  -> hybrid search
  -> format <memory-context>
  -> model request

During Turn
  -> model may call memory_search
  -> model may call memory_get

Near Compaction / Idle
  -> flush recent conversation
  -> quality check + semantic dedup
  -> append session log
  -> reindex/embed

Session End
  -> deterministic metadata summary
  -> optional dream gate
  -> consolidate durable workspace MEMORY.md
```

---

## 118. 一次 `memory_search` 的完整时序

```text
Tool Args
  -> Resource lookup: Arc<dyn MemoryBackend>
  -> open MemoryIndex
  -> watcher dirty?
      -> claim
      -> reindex changed / delete missing
      -> embed missing chunks
      -> release claim
  -> FTS + evergreen FTS
  -> optional query embedding + KNN
  -> hybrid score
  -> temporal/source/access/MMR
  -> min_score + max_results
  -> record access
  -> format source/path/lines/staleness/snippet
```

---

## 119. 一次 Pre-compaction Flush 的完整时序

```text
Token usage crosses soft boundary
  -> same-cycle gate
  -> acquire is_flushing
  -> emit MemoryFlushStarted
  -> snapshot conversation
  -> select recent user-aligned window
  -> call flush model
  -> empty / NO_REPLY / structure / length checks
  -> optional semantic dedup
  -> append daily session log
  -> reindex + embed
  -> save previous flush output
  -> release is_flushing
  -> compaction may continue
```

---

## 120. 最值得学习的设计原则

### 120.1 Markdown 是事实源，SQLite 是派生状态

可重建的索引不应成为唯一持久化格式。

### 120.2 Retrieval 与 Persistence 分权

模型可主动查，但长期写入通过宿主生命周期和质量 Gate。

### 120.3 自动注入要珍惜 Prompt Prefix 稳定性

Persisted Marker 不只是去重，也是在保护 KV Cache。

### 120.4 向量能力必须可降级

关键词检索是 Embedding、Credential 和 Native Extension 故障时的可靠底座。

### 120.5 长期知识需要分层沉淀

Raw Conversation -> Flush Summary -> Session Log -> Dream-curated Memory，是逐级去噪，不是一步到位。

### 120.6 并发正确性要覆盖文件和索引两边

Watcher Claim、Dream Lock、Recency Guard、Canonical Path 与 Cleaned Stem Tracking 分别守不同边界。

---

## 121. 常见误读

### 误读一：Memory enabled 就会把全部历史放进 System Prompt

实际只在首轮注入少量 Top Result，其余按需搜索。

### 误读二：Vector Search 是 Memory 的必要条件

实际可以 FTS-only。

### 误读三：Session-end 会调用模型总结所有决策

实际 Session-end Hook 是确定性 Metadata Summary；丰富总结来自 Flush。

### 误读四：Dream 会删除所有符合 Gate 的 Session

实际只清理真正读入、通过 Recency Guard 且删除成功的 Stem。

### 误读五：`memory_get` 可以读取任意项目文件

实际 Canonical Path 必须位于 Memory Root 内。

### 误读六：Agent Memory Scope 与 Global/Workspace Write Scope 是同一个 Enum

它们属于不同 Crate，回答不同问题。

### 误读七：模型有一个可随时调用的 `memory_write`

当前通用 Memory Tool 只有 Search/Get；写入由 Flush、Hook、Dream 或专属目录的普通文件工具完成。

---

## 122. 建议的源码阅读顺序

第一遍只建立数据流：

1. `storage.rs`
2. `chunker.rs`
3. `index.rs`
4. `search.rs`
5. Tool 的 `memory_backend.rs`、`search_tool.rs`、`get_tool.rs`
6. `memory_context.rs` 与 `turn.rs`

第二遍理解写入与生命周期：

1. `memory_flush.rs`
2. Session `memory_state.rs`
3. `hooks.rs`
4. `dream.rs`
5. `memory_dream.rs`

第三遍看隔离与运维：

1. `config/mod.rs`
2. `backend.rs` 的 Credentials 与 Watcher Sync
3. `dream_lock.rs`
4. Subagent `handle_request.rs`
5. Storage GC Tests

---

## 123. 调试 Memory 时先问的十个问题

1. `MemoryConfig.enabled` 最终是否为真，是否被 `--no-memory` 覆盖？
2. Storage Root 和 Workspace Hash 是否指向预期目录？
3. Markdown Source File 是否真实写入？
4. `index.sqlite` 中是否有对应 Chunk？
5. Chunk Hash 是否变化，从而触发 Re-embedding？
6. Watcher Dirty Set 是否被 Search 消费，Claim 是否被其他 Session 持有？
7. Embedding Model、Dimensions 与 Endpoint Credential Scope 是否匹配？
8. Search 当前是 `hybrid` 还是 `fts_only`？
9. Result 是被 `min_score`、Temporal Decay 还是 MMR 排掉？
10. Conversation Prefix 是否已有 `<memory-context>`，导致设计上不再重注入？

---

## 124. 可以进一步验证的实验

### 实验一：FTS-only 降级

关闭 Embedding Model，写入一个含独特 Keyword 的 Session Log，确认 `memory_search` 仍能命中并报告 FTS-only。

### 实验二：Workspace 隔离

在两个同名但路径不同的仓库初始化 Storage，确认 Slug 相同而 Hash 后缀不同。

### 实验三：外部编辑同步

启动 Session 后手工修改 `MEMORY.md`，下一次 Search 前观察 Watcher Reindex Telemetry。

### 实验四：Prompt Cache Idempotency

保存带 `<memory-context>` 的 Conversation，恢复后修改 Memory Source，确认首个 System Block 仍复用旧文本。

### 实验五：Semantic Dedup

连续 Flush 两段近义 Markdown，观察第二次是否在 0.92 Threshold 以上被跳过。

### 实验六：Dream Input Cap

制造超过 32K 的 Session Logs，确认只有 `processed_stems` 被清理，未读文件保留。

---

## 125. 本篇术语表（Glossary）

### Access Boost

根据某 Chunk 过去被返回的次数，对其相关性做温和对数提升。

### Agent Memory

某个 Agent Definition 专属的 Memory Directory，可按 User、Project 或 Local Scope 解析。

### Backend

Tool 后面的实际能力实现。本篇主要指实现搜索与范围读取的 `MemoryBackendImpl`。

### Best-effort

失败会记录但不让主任务失败的策略。Memory 多数生命周期操作都采用它。

### Blake3

高速密码学 Hash。这里用于 Workspace Path 指纹和 Chunk Content Hash。

### BM25

全文检索常用相关性算法，根据词频、逆文档频率和文档长度衡量 Keyword Match。

### Candidate

进入最终排序前的候选结果。候选数通常大于最终返回数。

### Canonicalize

把路径解析成规范真实路径，包括处理 `..` 与 Symlink，用于验证访问边界。

### Chat History

当前 Session 的 Conversation 历史，不等于跨 Session Memory。

### Chunk

从 Markdown 文档切出的检索单元，保存 Text 与原文件 Line Range。

### Compaction

当前会话接近 Context Window 时，用摘要替换旧 History 的状态迁移。

### Compaction Recovery

压缩后重新补充运行上下文的过程，其中可再次查询 Memory。

### Consolidation

把多个 Session Memory 合并、去噪、解冲突成长期 Workspace Memory。

### Conversation

发送给模型或保存在 Chat State 中的 User、Assistant、Tool 等有序项目。

### Credential Scope

认证信息获准发送到的 Endpoint 边界。

### Curated Memory

经过人工或 Dream 整理的 Global/Workspace `MEMORY.md`，相对 Session Log 更稳定。

### Dedup

Duplicate Elimination，判断新 Memory 是否与已有内容重复。

### Delta Flush

已有上次 Flush 输出时，只提取后续新增长期信息的 Flush。

### Dirty Set

Watcher 收集的已创建、修改或删除 Memory File 路径集合。

### Dream

用模型将多个 Session Log 与旧 Workspace Memory 合并成新长期文档的后台过程。

### Embedding

把文本映射为数字向量，使语义相近但词面不同的文本可以被检索。

### Endpoint

网络 API 地址。Embedding Credential 只能发送给获准 Endpoint。

### Ephemeral Workspace

位于临时目录、预期很快消失的工作区；其 Workspace Memory 写入会被跳过。

### Evergreen

不参与 Session Temporal Decay 的长期来源，即 Global 和 Workspace Memory。

### Fail Closed

安全检查不确定或不匹配时拒绝敏感动作。本篇指不向不匹配 Endpoint 转发 Credential。

### FTS5

SQLite Full-text Search 5，Memory 的关键词检索底座。

### Flush

用专用模型请求从近期 Conversation 提炼长期知识并写入 Session Log。

### GC

Garbage Collection，清理过期、空或孤立的 Workspace Memory Directory。

### Global Memory

跨 Workspace 共享的 `~/.grok/memory/MEMORY.md`。

### Graceful Degradation

增强组件失败时退化到较弱但可用的能力，例如 Vector 失败后使用 FTS-only。

### Hash Collision

不同输入产生同一 Hash 的情况；可读 Slug 后加路径 Hash主要用于降低同名 Workspace 冲突。

### Heading

Markdown 的 `#`、`##` 等标题，Chunker 用它识别主题层级。

### Hybrid Search

把 FTS Keyword Signal 与 Vector Semantic Signal 合并的搜索。

### Idempotency

重复执行不会重复产生效果。`<memory-context>` Marker 用于跨恢复避免重复注入。

### Initial Injection

首轮根据 User Query 自动 Search 少量 Memory，并注入 Prompt 的过程。

### IoC / Dependency Injection

控制反转/依赖注入。本篇表现为 Shell 创建 `MemoryBackend`，再作为 Trait Object 放进 Tool Resources，而不是 Tool 自己构造数据库。

### Jaccard Similarity

两个 Token Set 的交集大小除以并集大小；这里供 MMR 衡量 Result Snippet 重复度。

### KNN

K Nearest Neighbors，在 Vector Index 中找最接近 Query Vector 的若干项。

### KV Cache / Prompt Cache

Provider 对相同 Prompt Prefix 的计算缓存；重复重搜并改写开头 Memory Block 会破坏复用。

### Latch

一次置位后阻止同一流程重复执行的状态位，例如 `context_injected`。

### Lazy Creation

直到首次写入才创建目录。

### Lazy Synchronization

直到下一次 Search 才同步 Watcher Dirty File，而不是文件变化瞬间处理。

### Line Range

Chunk 在原 Markdown 中的起止行；内部通常零基且 End-exclusive。

### L2 Distance

向量间欧氏距离，源码将其转换为 `[0,1]` Similarity。

### Marker

用于识别已注入结构的特殊 Tag，例如 `<memory-context>`。

### MMR

Maximal Marginal Relevance，在相关性与结果多样性间平衡的重排算法。

### Namespace

用于隔离资源的路径空间，例如 Workspace Hash 或 Agent Name Directory。

### `NO_REPLY`

模型表示“没有值得写入内容”的约定输出，Flush 与 Dream 会将其视为正常空结果。

### Non-fatal

操作失败不改变主调用返回结果，例如 Access Count 更新失败不影响 Search Result。

### Overlap

相邻 Chunk 间重复保留的文本尾部，用于减少语义边界断裂。

### Prompt Reminder

动态加入模型请求、用于提醒运行态信息的上下文块，不等同于永久规范。

### Raw Score

包含各类 Boost、尚未 Clamp 的内部排序分数。

### Recall

相关内容被候选检索找出的能力。Initial Injection 默认低 Threshold 偏向 Recall。

### Recency Guard

Cleanup 前保护最近修改文件的时间窗口，本篇 Dream 使用 300 秒。

### Reindex

重新读取 Markdown、切块并让 SQLite 派生状态与文件一致。

### Resource

Tool Registry 运行时共享对象容器；Memory Backend 以 `Arc<dyn MemoryBackend>` 注入其中。

### Scaffold

初始化生成的空白提示模板，不应被 Dream 当成真实旧知识。

### Scope

数据或配置的可见/存储范围。本篇既有 Global/Workspace Write Scope，也有 User/Project/Local Agent Scope。

### Semantic Dedup

使用 Embedding Similarity 判断文字不同但语义高度重复的内容。

### Session Log

当前 Workspace 下 `sessions/*.md` 的单会话记忆文档。

### Session Segment

一次进程内或一次恢复阶段的会话片段；Atomic Latch 主要在该边界内有效。

### SID8

Session ID 的前八个字符，用于文件名去碰撞。

### Source Weight

根据 Global、Workspace、Session 来源对相关性乘以不同权重。

### Stampede

多个进程同时发现同一工作并一起执行，造成重复 Reindex/Embedding 的现象。

### Staleness

知识可能因时间而过时的程度；Tool 会对较旧 Session Result 提示验证。

### Sync-on-search

在 Search 开始时处理外部文件变化并同步 Index。

### Temporal Decay

随 Session Memory 年龄指数降低 Score 的机制，默认 Half-life 7 天。

### Tool Resource Injection

把 Backend 实例从 Session 构造层注入 Tool 执行层的 IoC 方式。

### Trait Object

Rust 中通过 `dyn Trait` 在运行时调用某个接口实现的对象；这里是 `Arc<dyn MemoryBackend>`。

### Transaction

SQLite 中原子提交一组修改，保证 Chunk、FTS 和 Embedding 清理不会只完成一半。

### Vector Dimension

Embedding Vector 的元素个数；变化后旧 sqlite-vec Table 不再兼容。

### Vector KNN

基于向量距离检索语义最近 Chunk 的查询。

### Watcher

监听 Memory Markdown 外部修改并记录 Dirty Path 的文件系统观察器。

### Workspace Hash

由完整 Workspace Path 生成的 Blake3 短 Hash，用于隔离同名项目。

### Workspace Memory

当前项目长期知识文件 `{workspace-dir}/MEMORY.md`，由用户或 Dream Curate。

---

## 126. 小结

grok-build 的 Memory 不是一个“把聊天记录存起来”的附件，而是一套完整的 Agent Knowledge Runtime：

1. Markdown 提供人可读、可编辑、可重建的事实源。
2. Workspace Hash、Agent Scope 与 Canonical Path 建立存储边界。
3. Markdown-aware Chunker 保留主题层级和原文件定位。
4. FTS5 提供可靠底座，sqlite-vec 提供可降级的语义召回。
5. Temporal、Source、Access 与 MMR 把候选变成更有用的 Top Results。
6. Initial Injection 负责首轮启动，Search/Get Tool 负责模型按需探索。
7. Flush 负责提炼，Session-end Hook 负责兜底，Dream 负责跨 Session 整理。
8. Claim、Lock、Recency Guard 与精确 Cleanup 让多 Session 共享时仍尽量正确。
9. Credentials 按 Endpoint 限定，读取按 Canonical Root 限定。
10. 模型可读与宿主可写的分权，降低了长期记忆被随意污染的风险。

理解这套链路后，再看“Agent 为什么下一次还知道上次发现的 Root Cause”，答案就不再是抽象的“模型有记忆”，而是可以精确落到：哪条生命周期写了哪份 Markdown、哪个 Chunk Hash 触发了 Index Update、哪种 Search Signal 把它召回、又通过哪个 Prompt 或 Tool 进入了下一轮推理。
