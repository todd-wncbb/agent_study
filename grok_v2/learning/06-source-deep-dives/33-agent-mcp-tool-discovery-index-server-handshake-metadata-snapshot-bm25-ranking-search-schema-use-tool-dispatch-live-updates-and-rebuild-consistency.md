# 源码精读 33：MCP Tool Discovery Index——Server Handshake、Metadata Snapshot、BM25、Search Schema、UseTool Dispatch 与动态一致性

> 源码基线：`ed6d543`
>
> 上一篇解释了工具候选集如何逐层收敛，以及为什么动态 MCP Tool 不直接进入每轮顶层 Tool 列表。本篇继续追踪这条隐藏路径：第三方工具怎样连接、被索引、按需暴露 Schema，并最终被执行。

---

## 1. 本篇解决什么问题

读完后应能回答：

- MCP server 的 `initialize`、`tools/list` 与本地 Tool 注册分别做什么？
- `McpToolRegistration` 为什么同时保存 Tool、Schema、Meta 和 `model_visible`？
- 搜索目录为何不直接从 `McpState` 查询，而从 `ToolBridge` 重新投影？
- `ToolMetadataSnapshot` 怎样保证一次搜索看到自洽的数据？
- 为什么代码名叫 BM25 index，却在每次搜索时重建 `SearchEngine`？
- snake_case、kebab-case、camelCase 和 qualified name 怎样被拆词？
- 精确匹配为什么先于 BM25？裸工具名精确匹配有什么歧义？
- `search_tool` 为什么返回完整 `input_schema`，却截断 description？
- `ready`、`partial`、空目录和无匹配怎样区分？
- `use_tool` 为什么要求 `server__tool`，以及怎样纠正误传入的 native tool？
- `InnerDispatch` 为什么能避免 ToolBridge 死锁？
- `call_raw()` 为什么不执行 Reminder 和 Persistence？
- Local MCP 与 Managed Gateway 同名时谁优先？
- tool toggle、重认证、连接恢复和 Agent rebuild 怎样保持搜索与执行一致？
- 这套设计真正保证了什么，又没有保证什么？

---

## 2. 先给出完整链路

```text
MCP server
  initialize
  tools/list (可能分页)
       │
       ▼
McpToolRegistration
  qualified name / description / input schema / meta / executable tool
       │
       ├─ app-only ───────────────► UI notification / x.ai/mcp/call
       │
       └─ model-visible
              │
              ▼
ToolBridge.register_mcp_tools
              │
              ▼
FinalizedToolset dynamic entries
  可执行，但不会逐个进入模型顶层 tools
              │
              ▼
refresh_mcp_snapshot_and_schedule_reminder_with
  从 ToolBridge definitions 重新投影
              │
              ▼
Arc<Mutex<ToolMetadataSnapshot>>
  tools + servers + mcp_initialized
              │
              ▼
Bm25ToolSearchIndex.search_snapshot
  clone snapshot → exact fast path / rebuild BM25 → top K
              │
              ▼
search_tool
  grouped results + full input_schema + ready/partial
              │
              ▼
模型构造 use_tool { tool_name, tool_input }
              │
              ▼
InnerDispatch → FinalizedToolset.call_raw
              │
              ▼
McpErasedTool → McpClient.call_tool → MCP server
```

最重要的阅读模型是：

```text
执行目录是事实源；搜索目录是执行目录的可搜索投影。
```

---

## 3. 核心源码地图

| 领域 | 主要源码与符号 |
| --- | --- |
| MCP Tool 拉取与注册记录 | `xai-grok-mcp/src/servers.rs`：`McpClient::get_tool_registrations`、`McpTool::into_registration` |
| Session 注册策略 | `xai-grok-shell/src/session/acp_session_impl/mcp.rs`：`register_mcp_tool` |
| 快照刷新 | `xai-grok-shell/src/session/acp_session_impl/mcp_snapshot.rs`：`refresh_mcp_snapshot_and_schedule_reminder_with` |
| 搜索元数据和 BM25 | `xai-grok-shell/src/session/tool_index.rs` |
| 后端无关搜索接口 | `xai-grok-tools/src/types/tool_index.rs` |
| 搜索入口 | `xai-grok-tools/src/implementations/search_tool/mod.rs` |
| 搜索输入 | `xai-grok-tools/src/implementations/search_tool/types.rs` |
| 执行入口 | `xai-grok-tools/src/implementations/use_tool/mod.rs` |
| Inner dispatch | `xai-grok-tools/src/registry/types.rs`：`InnerDispatchForToolset`、`call_raw` |
| 动态注册/删除 | `xai-grok-tools/src/bridge.rs`、`registry/types.rs` |
| Session 初始注入 | `xai-grok-shell/src/session/acp_session_impl/spawn.rs` |
| Bridge rebuild 恢复 | `mcp_snapshot.rs`：`re_register_mcp_tools_on_rebuilt_bridge` |

---

# 第一部分：Handshake 生产的不是“名字列表”，而是可执行注册记录

## 4. `get_tool_registrations()` 先保证 Client Ready

`McpClient::get_tool_registrations()` 首先调用 `ensure_initialized()`。

因此它不是对未连接对象做静态读取，而是在 Ready service 上执行协议请求。

## 5. `initialize` 与 `tools/list` 不同

`initialize` 建立协议能力与 peer 信息；真正的工具目录来自 `tools/list`。

Server 的 `instructions` 则来自 initialize handshake 的 peer info，后续用于 server summary。

## 6. `tools/list` 支持分页

代码维护 `cursor: Option<String>`：

```text
cursor = None
loop:
  list_tools(cursor)
  append tools
  next_cursor ? continue : break
```

所以一次 server discovery 不是默认只有一页。

## 7. Tool Schema 会被最低限度修复

有些 server 对无参数 Tool 返回 `{}`。

代码为 object schema 补齐：

```json
{
  "type": "object",
  "properties": {}
}
```

这不是业务参数推断，而是让下游 Provider 接受该 JSON Schema。

## 8. 原始 Tool 被包装成 `McpTool`

每项工具携带：

- server name；
- bare tool name；
- description；
- input schema；
- `_meta`；
- 指向共享 `McpState` 的引用。

这个包装把协议元数据和后续执行所需的 Client lookup 联系起来。

## 9. Qualified name 在注册前生成

形式为：

```text
{server_name}__{tool_name}
```

例如：

```text
linear__save_issue
```

## 10. Qualified name 不是随便拼完就接受

`McpTool::into_registration()` 同时执行：

- MCP qualified-name 结构校验；
- Provider tool-name 合法性校验。

空 segment、歧义 delimiter 或 Provider 不接受的名称会被记录并跳过。

## 11. 为什么必须限定 server

两个 server 都可能有 `search`、`fetch` 或 `create_issue`。

qualified name 同时承担：

- 全局查找 key；
- 模型调用合同；
- 搜索结果 identity；
- disable/enable key；
- permission 与 telemetry identity。

## 12. `McpToolRegistration` 的字段

```text
name
description
input_schema
tool
meta
model_visible
```

它不是纯 metadata DTO，而是一份“可以注册进 Runtime”的完整材料。

## 13. `_meta.ui.visibility` 决定 audience

若 visibility 显式包含 `model`，Tool 可供模型使用。

若只有 `app`，它不会注册进 ToolBridge。

没有 visibility 时默认 model-visible。

## 14. App-visible 不等于无用

App-only Tool 可以通过 UI 的工具变化通知被前端认识，并经专用 ACP 路径调用。

只是它不进入模型的搜索和执行路径。

## 15. Session 再应用 disable mask

`register_mcp_tool()` 在注册前检查 server/tool 是否禁用。

禁用项不会丢失，而是存进 `disabled_tool_registrations`，供日后无须完整 re-init 即可恢复。

## 16. Meta 还有 UI resource 用途

当 `_meta.ui.resourceUri` 存在时，Session 生成 `McpToolEntry` 供 UI notification。

这与 model visibility 是两条相关但不相同的 audience 线路。

## 17. 只有 model-visible 且未禁用的 Tool 进入 Bridge

核心调用：

```text
ToolBridge.register_mcp_tools(name, tool, Some(input_schema))
```

从这里开始，它成为 `FinalizedToolset` 的动态可执行条目。

## 18. 注册失败是 per-tool 降级

一个工具注册失败会告警，不会让整个 server 已成功取得的其他工具一起失败。

这是目录发现中的局部容错。

---

# 第二部分：执行目录怎样投影成搜索快照

## 19. 快照刷新入口

统一核心函数是：

```text
refresh_mcp_snapshot_and_schedule_reminder_with(...)
```

它同时更新搜索事实、Gateway catalog resource、提醒 dirty bit，以及特定 harness 的磁盘 descriptor mirror。

## 20. 为什么从 ToolBridge 读取 definitions

函数先执行：

```text
tool_bridge.tool_definitions().await
```

再筛选 qualified MCP definitions。

因此已禁用、注册失败、app-only、已删除的本地 MCP Tool 不会因为仍存在于 Client 原始目录中而误入搜索结果。

## 21. Bridge 是 local MCP 的执行事实源

源码事实可以写成：

```text
searchable local MCP tools = executable qualified definitions in ToolBridge
```

这让“搜得到却不能执行”的窗口尽量收窄。

## 22. 如何识别 MCP definition

当前投影使用 name 包含 `__` 的规则。

随后 `split_qualified_name()` 取 server/tool 两段。

这依赖注册前更严格的 qualified-name 校验，不能孤立理解成任意带双下划线的名字都是合法 MCP Tool。

## 23. `seen_tools` 做跨来源去重

刷新先加入 Bridge definitions，再加入 Managed Gateway catalog。

HashSet 保证同一 qualified name 只出现一次。

## 24. Local 与 Gateway 冲突时，快照里 local 先占位

因为 local definitions 先插入 `seen_tools`，Gateway 同名项会跳过。

这与执行阶段“local wins”的策略一致。

## 25. `ToolMetadata` 是搜索所需的最小投影

它保存：

- `qualified_name`；
- `server_name`；
- `tool_name`；
- `description`；
- 顶层 parameter names；
- 完整 `input_schema`。

它不持有 Client 或 executable Tool。

## 26. 参数名来自哪里

`extract_parameter_names()` 读取 schema 顶层 `properties` 的 keys。

没有 properties 时返回空 Vec。

## 27. 参数名为何既单独保存又保留完整 Schema

两者服务不同阶段：

- 参数名参加文本召回；
- 完整 Schema 用于模型构造准确调用。

## 28. Gateway metadata 使用稳定 ID

Managed Gateway 搜索合同使用 connector ID 和 tool ID，而不是 UI display label。

这样权限、搜索和执行不依赖可变化的人类可读名称。

## 29. Gateway disable 在投影时应用

Gateway Tool 不一定作为独立 LocalRegistry entry 存在，因此其 disable mask 在 catalog 投影阶段过滤。

被禁用 connector 或 qualified tool 不进入 metadata，也不进入 Gateway resource catalog。

## 30. Server metadata 来自已出现工具的 Client

函数先计算 `servers_with_tools`，再从 `McpState::all_clients()` 读取 server instructions。

只有当前投影中有工具的本地 server 才生成这类 metadata。

## 31. Server instructions 是描述，不是 Tool Schema

它用来告诉模型某个 server 大致提供什么能力，并进入 connected-server reminder。

具体调用参数仍必须来自 Tool 的 input schema。

## 32. `ToolMetadataSnapshot` 三个字段

```rust
tools: Vec<ToolMetadata>
servers: Vec<ServerMetadata>
mcp_initialized: bool
```

这三个字段必须作为一个版本理解。

## 33. 为什么用 `std::sync::Mutex`

读取路径只在锁内 clone 快照，没有 I/O，也没有 `.await`。

Trait 方法 `search_snapshot()` 是同步方法；使用 Tokio blocking lock 还可能在单线程 runtime 上 panic。

## 34. 写锁不能跨 `.await`

刷新函数用显式 block 限定 guard：写完 tools、servers、ready 后立刻释放，再更新异步资源或写 descriptor。

这是 async Rust 中很重要的锁边界。

## 35. 更新采用整体替换

```text
snapshot.tools = mcp_tools
snapshot.servers = server_metadata
snapshot.mcp_initialized = flag
```

不是对共享 Vec 边查边增量修改。

## 36. “一致快照”精确保证什么

一次查询看到的：

- results；
- total count；
- ready flag；

来自同一次 clone。

不会出现结果来自新版而 count 来自旧版的撕裂读取。

## 37. 它不保证永远最新

查询 clone 完成后，连接状态可能立刻变化。

Snapshot 保证内部自洽，不保证跨时间强一致。

## 38. `mcp_initialized` 如何进入快照

Session 查询 `McpState::is_initialized()`，或在 background handshake 全部处理后显式传入 `true`。

它表达“目录源是否完成本轮初始化”，不是“每个 server 都成功”。

## 39. 失败 server 也能得到 ready catalog

所有 server 都已成功或失败并收敛后，catalog 可以 ready。

ready 的含义是当前可用集合已稳定，而不是零错误。

## 40. 初始 ToolIndex 何时注入

Session spawn 后创建：

```text
Bm25ToolSearchIndex::new(session.tool_metadata_snapshot.clone())
```

再把 `ToolIndex(Arc<dyn ToolSearchIndex>)` 注入 Bridge resources。

## 41. Index 与快照共享同一个 Arc

之后刷新只替换 Mutex 内数据，不必每次重新向 SearchTool 注入新对象。

这是“稳定资源引用 + 可变内部快照”。

## 42. Agent rebuild 后为何重新注入 ToolIndex

新的 ToolBridge 有新的 Resources 容器。

虽然 Session 的 snapshot Arc 仍在，必须把指向它的新 wrapper 放进新 Bridge，`search_tool` 才能读取。

---

# 第三部分：BM25 搜索到底索引什么

## 43. 后端无关接口位于 tools crate

`ToolSearchIndex` trait 只定义：

```text
search_snapshot(query, limit)
list_server_summaries()
```

tools crate 不需要依赖 Shell 的 `McpState`。

## 44. 这是依赖倒置

`SearchTool` 依赖抽象接口；Shell 注入具体 BM25 实现。

与 Memory Tool 依赖 `MemoryBackend` 的模式相似。

## 45. 搜索第一步是 clone

`search_snapshot()` 先锁住共享 snapshot 并 clone，随后立即释放锁。

BM25 建库和搜索都在私有副本上完成，不阻塞刷新写入。

## 46. 空目录快速返回

若 tools 为空，返回：

- empty results；
- `total_hidden_tools = 0`；
- 当前 `is_ready`。

因此 empty-ready 与 empty-partial 可被上层区分。

## 47. 精确匹配先于 BM25

query trim 后转小写，依次比较：

- qualified name；
- bare tool name。

命中后只返回一个结果，score 固定为 `1.0`。

## 48. 精确路径的价值

模型已从先前结果、Reminder 或用户输入中知道名称时，不必让统计排名再次猜测。

尤其对 `grafana-ai__SearchDashboards` 这类复合标识符更可靠。

## 49. 精确匹配大小写不敏感

`SearchDashboards` 与 `searchdashboards` 可匹配同一 bare name。

返回的始终是 canonical qualified name。

## 50. 裸名精确匹配存在歧义

若 `server_a__fetch` 与 `server_b__fetch` 同时存在，`find()` 返回 Vec 中第一个。

它不会返回两个候选让模型消歧。

## 51. 这是源码明确测试的边界

测试 `search_exact_bare_name_ambiguous_returns_first_match` 固化了当前行为。

因此用户或模型知道 server 时，应优先搜索 qualified name 或带 server 关键词的自然语言。

## 52. BM25 document 的基础字段

每个 Tool document 由以下文本组成：

```text
server_name tool_name description parameter_names
```

## 53. 为什么参数名参加搜索

用户可能不知道 Tool 名，却知道所需字段，如 `channel`、`thread_ts`、`assignee` 或 `dashboard uid`。

参数名为意图召回提供额外信号。

## 54. Identifier decomposition

`split_identifier()` 支持：

- `__`；
- `_`；
- `-`；
- lower-to-upper camelCase boundary。

## 55. 拆词例子

```text
grafana-ai           → grafana, ai
query_prometheus     → query, prometheus
SearchDashboards     → Search, Dashboards
server__save_issue   → server, save, issue
```

## 56. Pascal acronym 并未完全语言学切分

实现只识别“小写字母 → 大写字母”边界。

它不是完整 Unicode 或 acronym tokenizer；例如连续大写的细分能力有限。

## 57. 文档会追加拆词结果

原始名称仍保留，同时加入 decomposed words。

这允许整词和 component 两类查询共同工作。

## 58. Query 也做相同方向的 normalize

只有 query 含 delimiter、underscore、hyphen 或 camel boundary 时，才追加拆分 components。

普通自然语言 query 原样进入 BM25。

## 59. 为什么不直接替换 query

保留原 query 可维持完整标识符匹配信号；追加 components 则扩大召回。

这是兼顾 exact-ish token 和分词 token 的简单做法。

## 60. 每次查询都会重建 SearchEngine

代码流程是：

```text
snapshot clone
→ build documents Vec<String>
→ SearchEngineBuilder.with_corpus(...).build()
→ search(normalized_query, limit)
```

`Bm25ToolSearchIndex` 本身并不缓存一个持久 BM25 engine。

## 61. 为什么仍称 Index

它实现的是逻辑搜索索引接口，并持有可搜索 snapshot。

“Index”描述职责，不代表内部一定维护增量倒排结构。

## 62. 重建策略的收益

- 不需要增量 add/remove 协议；
- 查询天然对应一个 snapshot version；
- 动态连接和禁用后无需同步第二份 engine；
- 数十到低数百 Tool 时实现简单。

## 63. 重建策略的成本

- 每次搜索重新分配 documents；
- 每次重新 tokenize/build；
- Tool 数量或 QPS 很大时成本线性增长。

源码注释给出的目标尺度是几十到低几百工具。

## 64. BM25 result 如何映回 metadata

Document ID 使用 snapshot.tools 的下标。

搜索结果 ID 再索引同一私有 snapshot，构造 `ToolSearchResult`。

## 65. 为什么映射不会和动态刷新错位

Engine documents 和结果映射都基于同一 clone 的 Vec 顺序。

共享 snapshot 后续更新不影响当前查询。

## 66. `filter_map` 是防御性边界

若 BM25 返回异常 document ID，`get()` 失败就跳过，而不是 panic。

正常情况下 builder 与 Vec 一一对应。

## 67. Score 的含义

非精确路径使用 BM25 crate 返回的相关性分数。

它只用于本次候选排序，不是概率，也不是跨 query 可比较的置信度。

## 68. Limit 在哪里应用

`SearchToolInput.limit` 是 `Option<u8>`，默认 5；转为 usize 后传给 index search。

限制的是全局 top K，然后才按 server 分组。

## 69. 分组不是每个 server 各取 K

如果一个 server 占据全局前五，它可以独占结果；其他 server 不会自动获得配额。

这是理解结果多样性的重要细节。

---

# 第四部分：`search_tool` 怎样把检索结果变成模型合同

## 70. SearchTool 从 Resources 取 `ToolIndex`

它不直接访问 SessionActor 或 McpState。

资源缺失时返回可读 JSON，说明当前没有 integration tools。

## 71. 为什么资源缺失不抛 Runtime error

“未配置 MCP”是正常产品状态，不是 SearchTool 崩溃。

用空结果让模型可以继续采用其他能力。

## 72. `search_snapshot` 一次返回结果与状态

接口没有把 `search()`、`count()`、`is_ready()` 拆成三个调用。

这样上层 JSON 的三部分来自同一快照。

## 73. Search telemetry 在 grouping 前记录

日志包含 result count，以及每个 tool name/score。

它观测检索本身，不被输出分组格式影响。

## 74. Result 按 server 分组

外层结构形如：

```json
{
  "results": [
    {
      "server": "linear",
      "tools": []
    }
  ]
}
```

## 75. 组内维持 BM25 顺序

遍历原 results 时按出现顺序 push，因此同 server 内保持原 ranking。

## 76. Server group 按最高分排序

新 group 记录其第一个 Tool 的 score；因为原结果降序，这就是该 server 的最高分。

随后 group 按此值降序。

## 77. 每个 Tool 返回哪些字段

```json
{
  "tool_name": "linear__save_issue",
  "description": "...",
  "score": 3.14,
  "input_schema": { "type": "object", "properties": {} }
}
```

## 78. 为什么必须返回完整 `input_schema`

`use_tool.tool_input` 自身只能声明“任意 object”。

真实目标 Tool 的字段、required、enum、nested object 等约束只能由 search result 按需告诉模型。

## 79. Search 是 schema acquisition，不只是找名字

这套协议可抽象为：

```text
discover capability → acquire target contract → invoke target
```

如果省略第二步，模型只能猜参数。

## 80. Description 有长度上限

`truncate_description()` 将过长文本限制在 2048 字符附近，并追加 `… [truncated]`。

实现按 char 截取，避免切坏 UTF-8 多字节字符。

## 81. 为什么 Schema 不同样截断

Schema 是执行合同；任意截断会生成无效 JSON 或丢失 required 约束。

Description 是检索/解释辅助信息，可以降采样。

## 82. `status = partial`

当 snapshot `is_ready = false` 时返回 partial，并附注某些 server 仍在连接。

已有结果仍可使用，只是不保证目录完整。

## 83. `status = ready`

表示当前初始化已收敛。

不表示 query 一定有匹配，也不表示所有配置 server 都成功连接。

## 84. Ready but empty 的专门提示

若 ready、总工具数为零且结果也为空，note 提示：

- 当前 Session 没有 MCP Tool；
- 若是 subagent，应检查 `mcpInheritance`。

## 85. Ready、有工具、但 query 无匹配

此时 total_hidden_tools 大于零，result groups 可以为空，note 为空。

模型可以改写 query 再搜索。

## 86. `total_hidden_tools` 的语义

它是 snapshot 中可搜索 Tool 总数，不是“本次未返回数量”。

名称中的 hidden 指这些 Tool 未作为顶层 Function schemas 全量公告。

## 87. 输出变成 `SearchToolOutput`

除 JSON content 外，还保存 `result_count`。

这让 Runtime/telemetry 无需再次解析模型可读 JSON 即可知道命中数量。

---

# 第五部分：`use_tool` 为什么是一个二次分派器

## 88. `UseToolInput` 只有两个字段

```json
{
  "tool_name": "linear__save_issue",
  "tool_input": {
    "title": "Fix login",
    "team": "ENG"
  }
}
```

## 89. `tool_input` schema 故意宽松

其静态 schema 是 object + `additionalProperties: true`。

因为顶层 `use_tool` 不可能在一个固定 JSON Schema 中预先编码所有动态 MCP Tool 的参数联合。

## 90. 精确合同来自上一步 Search

静态入口负责 transport envelope；动态 search result 负责 target-specific contract。

这就是两阶段协议。

## 91. 未限定名称会被拒绝

Local MCP 通常要求包含 `__`。

错误信息会告诉模型使用 `server__tool`，并引导再次调用当前模板映射后的 SearchTool 名称。

## 92. Native Tool 误路由有专门纠错

Resources 中保存 `EnabledNativeToolNames`。

若模型把 `read_file` 之类 native tool 塞进 `use_tool`，错误明确要求直接调用该工具。

## 93. 为什么要区分 unknown 与 native

两类错误的恢复动作不同：

- native：直接调用同名顶层 Tool；
- unknown/unqualified：重新 search integration Tool。

## 94. 纠错行为可配置

`UseToolParams.native_tool_correction` 默认为 true。

离线评测可关闭，以复现旧行为或避免改变既有 trajectory。

## 95. Tool input 会做轻量归一化

- String 若可解析成 JSON object，则解码成 object；
- Null 变成 `{}`；
- 其他 Value 原样保留。

这兼容某些模型把 nested JSON 再字符串化的情况。

## 96. `InnerDispatch` 从哪里来

外层 `FinalizedToolset::call()` 构造 `ToolCallContext` 时插入：

```text
InnerDispatch(Arc<InnerDispatchForToolset>)
```

所以 `use_tool.run()` 可以访问同一个 FinalizedToolset。

## 97. 为什么不能重新调用外层 ToolBridge

外层调用可能已经处在 Bridge/Toolset 的执行路径中。

若 meta tool 再经相同外层互斥与后处理路径递归进入，可能死锁或重复处理。

## 98. Inner dispatch 的实际路径

```text
UseTool.run
→ dispatch_mcp_tool
→ dispatch_local_mcp
→ InnerDispatch.0.call_terminal
→ InnerDispatchForToolset.call
→ FinalizedToolset.call_raw
```

## 99. 为什么经过 `ToolDispatch` trait

meta tool 不需要知道 FinalizedToolset 的具体类型。

统一 trait 也便于测试时注入 mock dispatch。

## 100. `call_terminal()` 做什么

Inner ToolDispatch 返回 stream；`call_terminal()` 排空它并取得 terminal typed output。

随后 `dispatch_local_mcp()` 把 JSON Value 反序列化回 `ToolOutput`。

## 101. `call_raw()` 先查 client-facing name

它在动态 `tools` RwLock 中找到 `client_name == tool_name` 的 entry。

找不到则产生 typed NotFound。

## 102. 动态注册为什么立即可执行

`FinalizedToolset.tools` 是 `parking_lot::RwLock<Vec<...>>`，LocalRegistry 也支持 runtime register/unregister。

不需要重建整个 Agent 才能加入 MCP Tool。

## 103. `call_raw()` 仍做参数名反向映射

若 entry 有 `reverse_params`，client-facing keys 会还原成 canonical keys。

MCP 动态条目当前通常是空映射，但 dispatch 层保持统一语义。

## 104. Child Tool Context 是新建的

它继承必要的：

- call ID；
- shared Resources；
- renderer；
- cancellation；
- cwd；
- invoking param names。

## 105. Child Context 故意不再插入 `InnerDispatch`

目标 MCP passthrough Tool 不应再次调用 use_tool。

移除它形成栈深边界，也阻止意外递归 meta-dispatch。

## 106. Runtime 最终按 registry ID 找 LocalRegistry handle

```text
local_registry.find(tool_id)
→ handle.execute(ctx, canonical_params)
→ drain stream
→ output_converter
```

这才真正进入 `McpErasedTool` 的执行实现。

## 107. 为什么叫 `call_raw()`

它跳过外层 Reminder 和 Resources Persistence 后处理。

“raw”不是完全绕过 parser/runtime，而是绕过 outer-call tail。

## 108. 后处理为什么只能做一次

最终外层仍是 `call("use_tool")`。

若内层目标也执行 reminders/persistence，再由外层执行一次，就会重复注入提醒或重复保存资源。

## 109. Target 输出归入 outer call

外层 Tool Result 的入口名仍是 `use_tool`，但系统会提取 `effective_tool_name` 记录真实 target name。

这兼顾稳定模型协议和真实工具可观测性。

## 110. 缺少 InnerDispatch 是程序错误

若既无 Gateway source 又无 dispatch，错误指出 `use_tool` 在 tool execution context 外被调用。

生产路径的 FinalizedToolset 外层调用总会注入它。

---

# 第六部分：Managed Gateway 是第二种执行后端

## 111. Gateway Tool 也进入同一搜索结果格式

Gateway catalog 被转换成 `ToolMetadata`，所以 SearchTool 不关心目标最终由 LocalRegistry 还是 gateway client 执行。

## 112. Gateway source resource 保存什么

`ManagedGatewayToolSource` 包含：

- connector ID/name；
- tool ID/name；
- call ID。

稳定 ID 用于合同，display name 用于输出/UI。

## 113. Dispatch 先做 Gateway lookup

`gateway_lookup()` 从 shared Resources 同时取：

- qualified name 对应的 source；
- 可用 gateway client。

只有 source 存在时才保留 client。

## 114. 同名冲突时 Local 优先

若 Gateway source 存在且 name 含 `__`，代码先 probe local dispatch。

Local 成功则直接返回。

## 115. 哪些 Local error 才允许 fallback Gateway

只有：

- NotFound；
- 由 catalog 名称无法构造 local ToolId 的特定 invalid-name error；

才继续 Gateway。

## 116. 真实 Local 执行错误不会被掩盖

若 Local Tool 已找到但返回 validation 或业务错误，错误直接传播。

不能偷偷换到同名 Gateway 再执行一次，否则可能造成重复副作用。

## 117. Indexed but client missing 有专门错误

若 catalog source 存在但没有 Gateway client，返回 `managed_gateway_unavailable`。

这明确暴露短暂资源不一致，而不是伪装成 ToolNotFound。

## 118. Gateway 结果怎样转成 ToolOutput

代码识别 `isError`/`is_error`，提取 content 中的 text、image data URI 或 resource JSON。

再生成成功或失败的 `MCPOutput`。

## 119. Local 与 Gateway 最终共享截断

`UseTool.run()` 在 dispatch 后构造 `McpTruncateContext`，调用 `truncate_tool_output()`。

动态工具返回过大时不会无边界塞回上下文。

---

# 第七部分：动态更新怎样传播

## 120. 初始 background handshake

多个 Client 的 `get_tool_registrations()` 通过 `FuturesUnordered` 并发进行。

结果收集后逐 server 注册 Tool、记录成功/失败，并让 handshaking set 收敛。

## 121. Ready 在所有初始结果处理后发布

Background task 完成 owned/shared Client 注册后调用统一刷新，并传 `mcp_initialized = true`。

于是 progressive 模式下此前 partial 的 SearchTool 之后变成 ready。

## 122. Generation 防止旧初始化覆盖新配置

Background task 保存启动时 generation。

若配置已变化，结果被丢弃并发出 init-cancelled，而不是把旧 server 目录写回当前 Session。

## 123. 禁用 Local Tool 的顺序

```text
重建可恢复 registration
→ stash
→ Bridge unregister
→ 更新 disabled set
→ refresh snapshot
```

刷新从 Bridge 读取，所以禁用项自然从搜索中消失。

## 124. 重新启用 Local Tool 的顺序

```text
从 disabled set 删除
→ 从 stash 取 registration
→ register_mcp_tools
→ refresh snapshot
```

无需重新连接 server。

## 125. Gateway toggle 的不同点

Gateway Tool 不是依赖本地 Bridge entry 作为唯一来源。

刷新函数直接拿 disable map 过滤 Gateway catalog，并同步更新 catalog resource。

## 126. Auth recovery

认证成功后重新 `get_tool_registrations()`、逐项注册、刷新 snapshot、通知 UI。

恢复后的 Tool 因此同时回到执行面和发现面。

## 127. Reactive managed re-auth failure

终局认证失败会 unregister 该 server tools，再刷新快照。

模型不会继续从旧搜索目录得到已经不可执行的工具。

## 128. Shared Client inheritance

子 Session 可共享已连接 Client，但仍需把其 registrations 注册进自己的 ToolBridge。

随后同样调用统一刷新。

连接共享不等于 Toolset 自动共享。

## 129. Bridge rebuild 为什么需要 re-register

Agent definition/model switch 等操作可能创建新 ToolBridge。

旧 Client 连接仍活着，但动态 entries 属于旧 Bridge，必须重新 list/register。

## 130. Rebuild 恢复是 best effort

`re_register_mcp_tools_on_rebuilt_bridge()` 对每个 Client 单独 list tools。

某 server 失败会记录并跳过，最后仍刷新当前成功集合。

## 131. Snapshot refresh 还会标记 Reminder dirty

`mcp_reminder_dirty = true` 让 Session 在 turn boundary 或 agentic loop 的下一次 inference 前宣布 server 集变化。

## 132. 为什么 SearchTool description 本身不动态变化

动态 server/tool 信息通过 snapshot 和 reminder 传递；SearchTool 顶层 definition 保持静态。

这保护多轮请求的 Tool Schema 前缀稳定性。

## 133. Server summary 如何构造

`list_server_summaries()`：

- 按 server 计数；
- 收集并排序 bare tool names；
- 合并 optional description；
- 用 BTreeMap 让 server 顺序稳定。

## 134. 为什么 fingerprint 包含 tool names hash

只比较 count 会漏掉“一删一增、数量不变”。

Reminder change detection 使用 count、description hash 和 sorted tool names hash。

## 135. Tool names 不一定直接显示在 Reminder

它们主要用于 fingerprint；Reminder 只列 server、数量和描述，并要求模型用 SearchTool 发现具体工具。

这样动态公告也保持紧凑。

## 136. Description 会先 sanitize

Server instructions 的换行和多余空白被折叠，再截断。

避免 server 提供的大段 instructions 破坏 reminder 布局或占用过多上下文。

## 137. `tools/list_changed` 当前只闭合了状态通知链

MCP Client event dispatcher 对短时间突发事件做 50ms window coalescing，并把它投影成 `config_changed` server-status push。

但当前 dispatcher 不会因此重新执行 `tools/list`、重注册 ToolBridge 或直接刷新 metadata snapshot。`RefreshMcpSearchIndex` 命令存在，但目前主要由 Managed Gateway catalog 更新广播触发。也就是说，本地 MCP server 主动改变工具列表时，“收到变化信号”已经接通，“自动重同步模型搜索/执行目录”并未在这条事件路径中闭合；其他显式 re-init、认证恢复、toggle 或 Bridge rebuild 路径仍会调用统一 snapshot refresh。

## 138. UI notification 与模型目录刷新不是同一件事

- UI notification 告诉客户端重绘/重新拉取；
- snapshot refresh 改变模型 SearchTool 的结果；
- Bridge register/unregister 改变真实可执行集合。

完整更新必须照顾三者。

---

# 第八部分：完整 Walkthrough——“在 Linear 创建 issue”

## 139. 初始状态

假设 Session 使用 progressive MCP initialization：

```text
ToolMetadataSnapshot {
  tools: [],
  servers: [],
  mcp_initialized: false
}
```

模型顶层已看到稳定 `search_tool`、`use_tool`。

## 140. Linear handshake

Client 完成 initialize，server instructions 可能是 “Project management”。

`tools/list` 返回 `save_issue`、`list_issues` 等，可能跨多个 page。

## 141. 生成 registration

`save_issue` 变成：

```text
name = linear__save_issue
description = Create or update a Linear issue
input_schema.properties = title/team/description/...
model_visible = true
```

## 142. 注册执行入口

Session 检查它未禁用，然后调用 `register_mcp_tools()`。

FinalizedToolset 此时能按 `linear__save_issue` 查到动态 entry。

## 143. 刷新搜索投影

刷新从 Bridge definitions 取回该 entry，生成：

```text
ToolMetadata {
  qualified_name: linear__save_issue,
  server_name: linear,
  tool_name: save_issue,
  parameters: [title, team, description, ...],
  input_schema: full schema
}
```

## 144. Snapshot 原子替换

Linear tools、server description 和最终 ready flag 一起写入 Mutex 内对象。

已持有 ToolIndex 的 SearchTool 无需重新注册。

## 145. 模型第一次搜索

```json
{
  "query": "linear create issue",
  "limit": 5
}
```

不是 exact name，于是构建 documents 和 BM25 engine。

## 146. BM25 为什么能命中

Document 同时含：

```text
linear save_issue Create or update a Linear issue title team ... save issue ...
```

query 与 server、action、domain object 都有重叠。

## 147. SearchTool 返回 schema

模型得到 canonical `linear__save_issue`，并看到 title/team 等字段的 type、required 规则。

到此它才具备可靠构造调用的合同。

## 148. 模型发起 use_tool

```json
{
  "tool_name": "linear__save_issue",
  "tool_input": {
    "title": "Fix login",
    "team": "ENG"
  }
}
```

## 149. Outer call 注入 InnerDispatch

FinalizedToolset 为 `use_tool` 构造 Context，并插入指回自身的 trait object。

## 150. Local dispatch 命中动态 entry

`dispatch_local_mcp()` 构造 ToolId，调用 terminal dispatch；`call_raw()` 在 runtime tools Vec 中找到 `linear__save_issue`。

## 151. MCP Tool 真正执行

LocalRegistry handle 执行 `McpErasedTool`，后者通过其 server name 在共享 state 中定位 Client，并发送 MCP `tools/call`。

## 152. 结果回到模型

MCP result 转换成 `ToolOutput`，经过统一 MCP 截断，再由 outer `use_tool` call 完成一次 Reminder/Persistence tail。

Agentic loop 把配对 Tool Result 加入 Conversation，进行下一次 sampling。

## 153. 若期间 Tool 被禁用

Bridge entry 被删除，snapshot 刷新后搜索不再返回它。

已经持有旧搜索结果的模型仍可能尝试调用，此时 `call_raw()` 返回 NotFound；这体现的是动态系统不可消除的时间竞争。

---

# 第九部分：一致性模型与设计边界

## 154. 系统采用的是最终一致，不是事务

Handshake、Bridge register、snapshot refresh、UI notification 分属多个步骤。

代码通过统一顺序和重建入口缩短不一致窗口，但没有跨所有组件的数据库事务。

## 155. Searchability 通常滞后于 executability

初次注册顺序是先 Bridge、后 snapshot refresh。

短窗口内 Tool 可能已可执行但尚未可搜索；这比反过来更安全。

## 156. Disable 时尽量先取消执行再取消发现

Local toggle 先 unregister，再 refresh。

短窗口内旧 snapshot 可能仍显示 Tool，但真实 dispatch 会 NotFound，不会执行已禁用能力。

## 157. 一次 Search 内部是强自洽的

clone snapshot 后：document、ranking、metadata mapping、count、ready 都来自同一版。

这是本模块最强的局部保证。

## 158. Search 与后续 Use 之间不是原子的

模型思考和发起下一次 Tool Call 期间，目录可以变化。

因此 UseTool 必须独立验证 lookup，不能认为“搜到过”就必定还存在。

## 159. `previously discovered` 主要是行为协议

UseTool input 文档要求先 search，但源码没有保存 per-model 的“已发现名称集合”并做 membership enforcement。

真正的硬校验是 qualified format、Gateway catalog lookup 和 Runtime entry lookup。

## 160. SearchTool 不验证业务 arguments

它只返回 JSON Schema。

实际 schema 解析/目标 Tool/MCP server 仍会在执行时拒绝不合法输入。

## 161. Exact bare name 是便利而非唯一标识

它适合名称全局唯一的常见情况，却不能解决同名 server 冲突。

Canonical qualified name 才是执行 identity。

## 162. Ready 是初始化 barrier，不是健康证明

Server 后续仍可能断线、过期或更新目录。

健康状态由 liveness、status dispatcher、restart/auth 等其他机制继续维护。

## 163. BM25 是 lexical retrieval

它依赖名称、说明和参数的词面重叠，不理解深层语义。

描述质量差、同义词完全不同或非英文分词，都可能降低召回。

## 164. 当前实现没有 score threshold

Search engine 返回的候选由 crate 行为和 limit 决定；SearchTool 本身未设统一最低分阈值。

不要把“有结果”误读为高置信度。

## 165. Schema token 成本被推迟而非消失

顶层不公告全部 Schema 节省每轮固定成本；每次 search 仍会返回命中 Tool 的完整 Schema。

收益来自按需、小 K 和缓存友好的稳定前缀。

## 166. 动态目录没有破坏 Turn Tool snapshot

顶层 Function list 保持 search/use 等 built-ins；snapshot 内 MCP metadata 可以随时更新。

两者是两套不同可见面。

---

# 第十部分：如何验证与调试

## 167. 先验证协议发现

关注：

- server 是否 initialize 成功；
- `tools/list` 是否所有分页完成；
- schema 是否补齐 object/properties；
- qualified name 是否通过校验；
- `model_visible` 是否符合 `_meta`。

## 168. 再验证执行目录

检查 `ToolBridge.tool_definitions()` 中是否出现 qualified name。

若没有，搜索快照不应包含它。

## 169. 再验证 metadata snapshot

确认：

- tools 数量；
- server/tool 拆分；
- parameter names；
- full schema；
- initialized flag。

## 170. 再验证 search output

区分以下状态：

| 状态 | total | status | 解释 |
| --- | ---: | --- | --- |
| 尚在连接 | 0 或部分 | partial | 目录可能继续增长 |
| 已就绪但无 MCP | 0 | ready | 当前 Session 无可用 Tool |
| 有目录但无匹配 | >0 | ready | 应改写 query |
| 有匹配 | >0 | ready/partial | 可按 schema 构造 use_tool |

## 171. 最后验证 dispatch

若 Search 有结果但 Use 失败，依次看：

- 名称是否 canonical；
- Tool 是否在查询后被禁用/删除；
- InnerDispatch 是否存在；
- LocalRegistry 是否有 handle；
- Gateway source/client 是否同时存在；
- MCP server 是否仍 Ready；
- arguments 是否符合目标 schema。

## 172. 快速源码检索

```sh
rg "get_tool_registrations|into_registration|model_visible" crates/codegen/xai-grok-mcp
rg "refresh_mcp_snapshot_and_schedule_reminder" crates/codegen/xai-grok-shell/src/session
rg "search_snapshot|normalize_query|split_identifier" crates/codegen/xai-grok-shell/src/session/tool_index.rs
rg "dispatch_mcp_tool|InnerDispatch|call_raw" crates/codegen/xai-grok-tools/src
```

## 173. 目标测试

```sh
cargo test -p xai-grok-tools search_tool --lib
cargo test -p xai-grok-tools use_tool --lib
cargo test -p xai-grok-shell tool_index --lib
```

最后一组会编译较大的 Shell test target；若仓库当前存在无关编译错误，应记录 blocker，不要为了跑本文测试修改无关源码。

---

# 第十一部分：阅读结论

## 174. 一句话模型

```text
MCP discovery 不是把所有动态 Schema 塞给模型，
而是先把可执行 Tool 投影成一致 metadata snapshot，
再用稳定 Search/Use 两阶段入口按需取得合同并二次分派。
```

## 175. 关键不变量

1. 只有合法、model-visible、未禁用且注册成功的 local MCP Tool 才应进入 Bridge。
2. Local 搜索快照从 Bridge 重新投影，尽量以可执行事实为准。
3. 一次 Search 的结果、总数和 ready 状态来自同一 snapshot clone。
4. 精确 qualified name 优先于统计搜索。
5. BM25 document 同时包含 server、tool、description 和顶层参数名。
6. Search 返回完整目标 Schema；UseTool 的静态 nested input 只能保持宽松。
7. UseTool 经 InnerDispatch/call_raw 执行目标，避免外层锁递归和双重后处理。
8. Local 与 Gateway 同名时 Local 优先，Local 真实错误不能触发 Gateway 重试。
9. 动态更新最终回到 register/unregister + unified snapshot refresh。
10. Search 与 Use 之间只能最终一致，执行时必须再次 lookup。

---

## 176. 本篇术语表

| 名词 | 白话解释 | 在本篇中的精确含义 |
| --- | --- | --- |
| MCP | 模型上下文协议 | Agent 与外部 server 协商能力、列举并调用工具的协议 |
| MCP server | 外部工具服务 | 通过 stdio/HTTP 等 transport 提供 initialize、tools/list、tools/call 的进程或服务 |
| MCP client | Agent 侧连接对象 | 管理 handshake、协议 service、认证、liveness 和实际调用的 `McpClient` |
| handshake | 握手 | Client 与 server 初始化协议版本、能力和 peer 信息的过程 |
| peer info | 对端信息 | initialize 返回的 server 身份及可选 instructions |
| `tools/list` | 工具列举请求 | 从 server 分页取得 Tool 名称、描述、schema 和 meta |
| pagination | 分页 | 通过 cursor/next_cursor 多次请求完整目录 |
| registration | 注册材料 | 包含 metadata、schema、visibility 和 executable wrapper 的 `McpToolRegistration` |
| qualified name | 全限定工具名 | `{server}__{tool}` 形式的全局 identity |
| bare tool name | 裸工具名 | 不含 server prefix 的 MCP 原始工具名 |
| delimiter | 分隔符 | qualified name 中连接 server 与 tool 的 `__` |
| input schema | 输入合同 | 描述目标 Tool 参数类型、properties、required 等的 JSON Schema |
| schema repair | Schema 修复 | 为 `{}` 等 MCP schema 补上 object/properties 的兼容处理 |
| `_meta` | 扩展元数据 | MCP Tool 附带的 UI visibility/resource 等非核心字段 |
| model-visible | 模型可见 | Tool 允许注册进模型可执行 Runtime 的 audience 状态 |
| app-only | 仅应用可见 | 可供 UI 展示/调用、但不注册给模型的 Tool |
| disable mask | 禁用掩码 | 按 server/tool 配置排除动态 Tool 的集合 |
| stash | 暂存 | 保存 disabled registration，以便恢复时无须完整重连 |
| ToolBridge | 工具桥 | Session 使用的工具定义、资源和调用外观 |
| FinalizedToolset | 定型工具集 | 同时保存内置工具和 runtime 动态 MCP entries 的可执行 Registry |
| execution source of truth | 执行事实源 | 决定某 local MCP Tool 当前是否真正可 lookup/dispatch 的 Bridge/Toolset 状态 |
| projection | 投影 | 从执行 definitions 提取搜索所需 metadata 的过程 |
| ToolMetadata | 工具搜索元数据 | qualified/server/tool name、description、parameters 和 schema 的记录 |
| ServerMetadata | 服务搜索元数据 | server name 与 initialize instructions 的记录 |
| ToolMetadataSnapshot | 工具元数据快照 | tools、servers、initialized flag 的一个一致版本 |
| snapshot clone | 快照克隆 | 锁内复制整份 metadata 后在锁外搜索 |
| torn read | 撕裂读取 | 结果、数量、状态分别读到不同版本；本设计用单次 clone 避免 |
| `std::sync::Mutex` | 同步互斥锁 | 用于极短、无 await 的 snapshot clone/replace 临界区 |
| ToolIndex | 搜索资源包装 | 注入 Resources 的 `Arc<dyn ToolSearchIndex>` |
| dependency inversion | 依赖倒置 | SearchTool 依赖 trait，Shell 提供了解 MCP 的具体实现 |
| BM25 | 词面相关性排名 | 根据 query 与文档词项统计关系计算排序分数的算法 |
| corpus | 语料集合 | 当前 snapshot 中每个 Tool 转换出的搜索文档集合 |
| document | 搜索文档 | server、tool、description、parameter names 及拆词结果组成的字符串 |
| tokenizer | 分词逻辑 | 本文中特指 identifier 的 delimiter/snake/kebab/camel 拆分 |
| query normalization | 查询归一化 | 保留原 query 并追加复合标识符 component 的处理 |
| exact fast path | 精确快速路径 | qualified 或 bare name 大小写无关命中时跳过 BM25 |
| ambiguous bare name | 歧义裸名 | 多个 server 拥有相同 tool name，当前实现只返回首项 |
| relevance score | 相关性分数 | BM25 对本次 query 的排序值，不是概率或全局置信度 |
| top K | 前 K 项 | BM25 全局结果应用 limit 后留下的候选 |
| grouped results | 分组结果 | SearchTool 把全局候选按 server 聚合后的 JSON |
| hidden tool | 隐藏工具 | 不作为顶层 Function schema 全量公告、需 Search 发现的 MCP Tool |
| ready | 目录已收敛 | 当前初始化批次已经完成成功/失败处理 |
| partial | 部分目录 | 某些 server 尚在连接，已有结果可用但不完整 |
| SearchTool | 搜索元工具 | 稳定顶层入口 `search_tool`，按需返回 MCP 名称和 Schema |
| UseTool | 调用元工具 | 稳定顶层入口 `use_tool`，把 envelope 二次分派到真实 MCP Tool |
| meta tool | 元工具 | 不代表单一业务能力，而是搜索/转发其他工具的稳定入口 |
| schema acquisition | 合同获取 | Search 阶段取得具体目标 input schema 的过程 |
| two-stage protocol | 两阶段协议 | 先 search 获取合同，再 use 执行目标 |
| native tool | 原生工具 | 直接出现在模型顶层 tools 中的 built-in Function Tool |
| corrective error | 纠错错误 | 告诉模型 native Tool 应直接调用或未知 Tool 应重新搜索的错误 |
| InnerDispatch | 内层分派器 | Context 中指回当前 FinalizedToolset 的 ToolDispatch trait object |
| trait object | Trait 对象 | 以 `Arc<dyn ToolDispatch>` 隐藏具体 dispatch 类型的运行时多态 |
| terminal output | 终态输出 | Tool stream 排空后唯一的最终 typed result |
| `call_raw()` | 原始内层调用 | 执行目标 Tool、但跳过外层 reminder/persistence tail 的路径 |
| post-processing | 后处理 | Tool 成功/失败后统一执行的 reminder、render、resource persistence 等逻辑 |
| deadlock | 死锁 | 外层执行持锁时又递归取得同一锁而永久等待的状态 |
| recursion boundary | 递归边界 | Child Context 不带 InnerDispatch，阻止目标 Tool 再次 meta-dispatch |
| LocalRegistry | 本地执行注册表 | 根据 registry ToolId 找到实际 runtime handle 的目录 |
| Managed Gateway | 托管工具网关 | 由共享 gateway client 按 connector/tool catalog 远程执行的后端 |
| Gateway catalog | 网关目录 | qualified name 到 ManagedGatewayToolSource 的 resource map |
| local wins | 本地优先 | Local MCP 与 Gateway qualified name 冲突时先执行 Local |
| fallback | 回退 | Local 明确 NotFound/名称拒绝时才尝试 Gateway 的策略 |
| live update | 动态更新 | 连接、认证、toggle、list_changed 等导致目录在 Session 运行中改变 |
| generation | 配置代数 | 防止旧 background handshake 覆盖新配置的版本号 |
| reminder dirty bit | 提醒脏标记 | 表示 server catalog 变化、应在下一推理边界公告的 AtomicBool |
| server fingerprint | 服务指纹 | count、description hash、sorted names hash 组成的变更检测值 |
| bridge rebuild | Bridge 重建 | Agent/model/definition 变化后创建新 ToolBridge 的过程 |
| re-registration | 重新注册 | 从仍连接的 Client 重新 list tools 并镜像到新 Bridge |
| eventual consistency | 最终一致 | 多步骤传播短期可不同步，但稳定后收敛到同一目录状态 |
| atomic snapshot | 原子快照 | 单次搜索内部数据来自同一版本，并非 Search 与 Use 跨调用事务 |
| TOCTOU | 检查与使用时差 | Search 后、Use 前 Tool 状态改变导致旧结果失效的竞争 |
| prompt cache stability | 提示缓存稳定性 | 顶层 Tool definitions 不随 MCP 目录频繁变化，便于复用模型请求前缀 |

---

## 177. 下一篇建议

下一篇可以继续沿执行端深入：

> **源码精读 34：MCP Client Runtime——Transport、Initialize State Machine、OAuth、`tools/call`、Liveness、ListChanged、Auto Restart 与错误恢复**

它会从本篇最后一步 `McpErasedTool → McpClient` 继续向下，解释 stdio/HTTP transport 如何建立，Client state 如何从 Uninitialized 走到 Ready，调用、断线、重启和认证恢复怎样收敛。
