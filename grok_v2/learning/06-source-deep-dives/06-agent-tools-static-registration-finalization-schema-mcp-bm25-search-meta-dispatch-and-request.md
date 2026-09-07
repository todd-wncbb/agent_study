# 源码精读 06：Tool 如何注册、Finalize、生成 Schema、搜索 MCP Tool 并进入模型请求

> 本篇沿着 Tool 的完整生命线阅读：Rust Tool 类型如何进入候选 Registry，Agent 配置如何选出本 Session 的工具，Finalize 如何生成模型可见 Schema，MCP Tool 为什么被隐藏在稳定的 `search_tool` / `use_tool` 后面，以及最终哪些 Tool Definition 真正进入一次 `ConversationRequest`。
>
> 源码基线：`ed6d543`。源码变化后优先按本文列出的类型与函数重新定位。

---

## 1. 先给结论：Tool 也有多个“加载”阶段

“Tool 已加载”至少可能指四件不同的事：

1. 二进制知道某个 Rust Tool 类型。
2. 当前 Agent 配置选择了这个 Tool。
3. Finalize 后它已可在本地 Registry 中 Dispatch。
4. 当前模型请求携带了它的完整 Function Schema。

这四层并不等价。

特别是 MCP Tool：它可以已经连接、注册并可执行，但完整 Schema 不直接放进每次模型请求，而是通过 `search_tool` 按需取回。

---

## 2. Skill Search 与 Tool Search 的根本差异

上一篇确认 Skill 没有独立语义搜索服务；模型根据 Prompt Catalog 自行判断。

Tool Search 则真的有检索实现：

```text
Bm25ToolSearchIndex
```

它对 MCP Tool 的 Server Name、Tool Name、Description 和 Parameter Names 建立 BM25 文档，并返回完整 Input Schema。

因此：

| 对象 | 发现方式 | 搜索方式 | 全文/Schema 获取 |
| --- | --- | --- | --- |
| Skill | 文件系统扫描 | 模型阅读摘要目录 | Read `SKILL.md` |
| Native Tool | 编译期类型注册 + Config | 不需要搜索，Schema 常驻请求 | 请求中的 `ToolSpec` |
| MCP Tool | Server Handshake/ListTools | `search_tool` + BM25 | 搜索结果携带 Input Schema |

---

## 3. 全链路地图

```text
Rust Tool Type
  |
  | ToolRegistryBuilder::register<T>()
  v
候选 ToolEntry Map
  |
  | AgentDefinition + Session Feature Gates
  v
ToolServerConfig.tools
  |
  | validate_config + finalize_with_trunc_config
  v
FinalizedToolset
  |              \
  |               \ MCP Handshake/ListTools
  |                \-> register_tool(runtime)
  |                    + ToolMetadataSnapshot
  |
  +--> Native ToolDefinition -- prepare_tool_definitions_builtins_only
  |                              |
  |                              v
  |                         Request.tools
  |
  +--> MCP ToolDefinition ----> BM25 Search Index
                                 |
                         search_tool(query)
                                 |
                     Name + Description + Schema
                                 |
                     use_tool(name, arguments)
                                 |
                     InnerDispatch / Gateway Client
                                 |
                              MCP Tool
```

---

## 4. 主要 crate 的职责

| crate | 责任 |
| --- | --- |
| `xai-grok-agent` | 根据 Agent Definition 和 Session 能力组装 Tool Config |
| `xai-grok-tools` | Tool 类型、Registry、Finalize、Resources、Dispatch、Search/Use Meta Tool |
| `xai-grok-shell` | MCP 生命周期、BM25 索引、每 Turn Tool Definition、Tool Call 调度 |
| `xai-chat-state` | 把 ToolSpec 放入最终 `ConversationRequest` |
| `xai-grok-sampling-types` | 请求级 `ToolSpec`、HostedTool 和转换类型 |

Tool 的生命周期比 Skill 更靠近执行层，因此状态分布也更广。

---

## 5. 第一核心 Trait：`xai_tool_runtime::Tool`

每个 Tool 的执行面由 Runtime Trait 表达，核心关联类型通常包括：

```rust
type Args
type Output
```

Tool 还提供：

- 稳定 ID。
- Description。
- Capabilities。
- 异步 `run()`。

`Args` 同时要求可 Deserialize 和 `JsonSchema`，使同一个 Rust 类型既能解析模型参数，也能生成 Function Calling Schema。

---

## 6. 第二核心 Trait：`ToolMetadata`

文件：

```text
crates/codegen/xai-grok-tools/src/types/tool_metadata.rs
```

每个内置 Tool 除 Runtime Trait 外，还实现 Grok Build 自己的 `ToolMetadata`：

```rust
fn kind(&self) -> ToolKind
fn tool_namespace(&self) -> ToolNamespace
fn description_template(&self) -> &str
```

这三个字段分别回答：

- 它属于哪种语义能力。
- 它属于哪个 Harness/命名空间。
- 模型看到的说明模板是什么。

---

## 7. `ToolKind` 不是 Tool Name

`ToolKind::Read` 表示语义角色，而 `read_file`、`Read` 或随机化名称是客户端可见名字。

同一种 Kind 可能有多个 Harness 实现：

```text
GrokBuild:read_file
Codex:read_file
OpenCode:read
GrokBuildConcise:read_file
```

Prompt 和 Description Template 引用 Kind，而不是硬编码某个名字：

```text
${{ tools.by_kind.read }}
```

---

## 8. `ToolNamespace` 的作用

Builder 候选表使用 fully-qualified ID：

```text
GrokBuild:read_file
Codex:apply_patch
OpenCode:bash
```

Namespace 解决“多个 Harness 有同名 Tool”的注册冲突。

Finalize 后发送给模型的是 Client Name，通常不带 Namespace。

---

## 9. `ToolRegistryBuilder::new()` 做了什么

`new()` 注册大量候选实现：

- Grok Build Bash、Read、Edit、List、Grep。
- Task、Wait、Kill、Todo、Goal、Workflow。
- Web、LSP、Image、Video。
- Plan Mode 与 Ask User。
- Codex 风格文件工具。
- OpenCode 风格工具。
- Memory Tool。
- `search_tool` 与 `use_tool`。
- Concise 与 Hashline 变体。
- Cross-cutting Reminders。

最后还运行 Process-global Tool Packs。

这不是说一个 Agent 会得到全部工具；它只是构造二进制可选目录。

---

## 10. Tool Pack 的扩展点

外部包可调用：

```rust
register_tool_pack(pack_fn)
```

每次新的 `ToolRegistryBuilder::new()` 会运行已登记 Pack。

顺序契约很严格：Pack 必须在进程第一次创建 Builder 之前注册；之后注册不会追溯修改已存在 Builder。

---

## 11. `register<T>()`

无额外配置参数的 Tool 使用：

```rust
register<T>()
```

它实际委托给：

```rust
register_with_params::<T, ()>()
```

所以 Registry 内部只维护一条通用注册路径。

---

## 12. `register_with_params<T, P>()`

这里同时捕获两个类型：

- `T`：Tool 本身，含 Args/Output/Run。
- `P`：Session 级 Tool Configuration，例如 BashParams。

调用时生成并保存：

- Namespace 与 ID。
- ToolKind。
- Requirement Expression。
- 默认配置参数。
- Args JSON Schema。
- Metadata。
- Output Converter。
- Params Validator/Applicator。
- Input Parser。
- Local Registry Registration Closure。

Rust 泛型在这里被转成后续可统一处理的 type-erased closures。

---

## 13. `ToolEntry` 是候选期容器

`ToolEntry` 只存在于 Builder 阶段。

它既包含元数据，也包含将来如何完成具体操作的闭包：

```text
validate_params
apply_params
register_params
parse_input
register_in_local
output_converter
```

Finalize 不需要再知道原始 `T` 和 `P` 的具体类型。

---

## 14. 为什么要 type erase

候选 Registry 中同时存 BashTool、ReadFileTool、TaskTool 等完全不同的泛型类型。

Rust Vec/HashMap 需要统一元素类型，因此 Builder 在注册点利用编译期类型信息生成闭包，然后把它们擦除为 Trait Object。

这是“注册点强类型、运行期统一容器”的典型模式。

---

## 15. Args Schema 在注册时生成

`generate_schema::<T::Args>()` 使用 Schemars：

```text
JSON Schema Draft 07
inline_subschemas = true
```

之后：

- 删除 Root `title`。
- 删除 Root `description`。
- Object Root 缺少时补空 `properties`。
- Object Root 缺少时补空 `required`。

这样输出更适合作为 Function Tool Parameters，而不是独立文档 Schema。

---

## 16. Args Schema 与 Config Params 不同

容易混淆的两类参数：

| 类型 | 谁提供 | 用途 |
| --- | --- | --- |
| `T::Args` | 模型每次 Tool Call | 例如 `target_file`、`query` |
| `P` / Tool Params | Session Config | 例如是否允许后台执行 |

前者进入模型 JSON Schema；后者在 Finalize 时合并、验证并写入 Resources。

---

## 17. `ToolConfig`

Agent/Client 对一个候选 Tool 的选择和覆盖由 `ToolConfig` 表达：

```rust
id
params
name_override
params_name_overrides
description_override
behavior_version
kind
```

`id` 指向 Builder 中的 fully-qualified candidate。

---

## 18. `ToolConfig::for_tool<T>()`

内置 Tool 应优先通过类型创建 Config：

```rust
ToolConfig::for_tool::<ReadFileTool>()
```

它自动得到 Namespace、ID 和 Kind，重构时由编译器帮助发现错误。

运行期才知道名字的 MCP/Custom Tool 使用 `from_id()`，其 Kind 留空。

---

## 19. `ToolServerConfig`

它是当前 Agent 真正想启用的 Tool 集：

```rust
pub struct ToolServerConfig {
    tools: Vec<ToolConfig>,
    behavior_preset: Option<String>,
}
```

Builder 的 Candidate Map 可以很大，Finalize 只消费 `config.tools` 中的条目。

---

## 20. Agent Definition 先产生基础 Tool Config

`AgentDefinition.tool_config` 是版本可控的静态声明。

`inject_default_tools=true` 时，`AgentBuilder::build` 还会根据 Session 能力叠加：

- Memory Search/Get。
- Web Search/Fetch。
- LSP。
- Image/Video。
- Write fallback。
- Plan Mode Tools。

因此 Agent Definition 不是最终 Toolset。

---

## 21. Feature Gate 会删除不可用 Tool

Builder 会按真实依赖裁剪：

- 没有 Memory Backend，就删除 Memory Tool。
- Ask User 未启用，就删除对应 Tool。
- 无 Subagent 或没有可用 Subagent Definition，就删除 Task。
- Task 被删除且无后台创建能力时，再删除部分生命周期 Tool。
- Web/LSP/Image 等按资源与配置决定。

模型不应看到一个当前 Session 注定无法工作的 Tool。

---

## 22. Agent Tool Allowlist

`AgentDefinition.tools` 非空时，Builder 尝试把兼容名称映射到 ToolKind 或 fully-qualified ID。

若全部可解析，就保留：

- 明确匹配的 Tool。
- 匹配 Kind 的 Tool。
- 必需的 Task 依赖。
- `search_tool` 与 `use_tool` Meta Tool。

如果存在无法映射的条目，源码选择警告并保留完整 Grok Toolset，避免错误别名意外把 Agent 裁成不可用状态。

---

## 23. 为什么 Meta Tool 被保留

即使 Agent Allowlist 只写某些 MCP/兼容能力，Builder 仍保留：

```text
ToolKind::SearchTool
ToolKind::UseTool
```

因为 MCP 具体 Tool 不在静态 Config 中逐个列出；没有这两个入口，运行期发现的集成能力无法被模型使用。

---

## 24. Denylist 与最终 Session Clamp

`disallowed_tools` 先从已组装 Config 删除匹配项。

之后 `session_tools_allowed` 再应用 CLI/Session Operator 约束。

这体现两层控制：

- Agent 作者声明能力。
- Session 操作者设置上限。

后者是最终 Clamp，不能被前面的默认注入重新加回来。

---

## 25. Capability Mode 与 Kind

Subagent Capability Mode 按 `ToolKind` 过滤，而不是依赖字符串名称。

内置 ToolConfig 自动携带 Kind；运行期原始 ID 配置可能 Kind=None，为保持扩展性，限制模式对 None 的处理刻意谨慎。

这也是 Kind 作为独立语义层的价值。

---

## 26. Finalize 是真正的分界线

入口：

```rust
ToolRegistryBuilder::finalize_with_trunc_config
```

Finalize 之后得到：

```rust
FinalizedToolset
```

候选 Builder 被消费，当前 Session 的 Toolset 才正式成立。

---

## 27. Finalize 第一步：验证 Config

`validate_config` 检查：

- Tool ID 是否存在。
- Behavior Preset 是否有效。
- Params 能否解析成对应 `P`。
- Tool 间 Requirement Expression 是否成立。
- 互斥工具组是否冲突。

错误会返回结构化 `RequirementError`，包含 Field Path、Expected、Bad Value 与 Category。

---

## 28. Requirement Expression

Tool 和 Reminder 都可声明 `Expr<ToolRequirement>`。

Finalize 把拟启用 Tool 转成 `ProposedTool`，然后为每个候选计算条件。

这使依赖关系由声明式表达式维护，例如“至少存在一种 Read/Edit/List 能力”，而不是 Builder 到处写名字判断。

---

## 29. Finalize 先建立 Kind-to-Name Map

对 Config 中每个 Tool：

```text
ToolKind -> Client-facing Name
```

若有 `name_override`，使用 Override；否则用 Tool ID。

同 Kind 多个实现时，第一个取得默认模板名称。

---

## 30. Parameter Name Map

Finalize 从 Args Schema 的 `properties` 建立默认 identity map：

```text
canonical param -> canonical param
```

再应用 `params_name_overrides`：

```text
target_file -> path
```

这个 Map 同时影响模型 Schema、Description Template 与运行时反向映射。

---

## 31. `TemplateRenderer`

Finalize 用最终 Tool/Param 名称一次性创建 Renderer。

模板可写：

```text
${{ tools.by_kind.read }}
${{ params.edit.old_string }}
```

还可按平台 Shell 和 System Reminder 能力分支。

Renderer 之后存入 Resources，也保存在 FinalizedToolset 的 Arc 中。

---

## 32. 为什么 Description 必须在 Finalize 后渲染

一个 Edit Tool 的说明可能告诉模型“先调用 Read”。

但 Read Tool 在不同 Harness 中可能叫：

```text
read_file
Read
read
```

只有选定完整 Toolset、应用 Rename 后，说明文字才能引用正确的模型可见名称。

---

## 33. Description Render Failure

Debug/Test 构建通过 `debug_assert` 强制暴露模板错误。

Release 构建会记录 Warning，并删除未解析的 `${{...}}` / `${%...%}` 标记，确保模型不会看到内部模板语法。

降级文本可能不够自然，但仍是纯说明文字。

---

## 34. `SessionContext`

Finalize 的另一个输入不是 Tool，而是 Session 依赖集合：

```text
Terminal Backend
Async FileSystem
CWD
Session Folder/Env
Notification Handle
Subagent Backend
Skills
Memory Backend
Web/LSP/Image/Video Client Config
Auth Providers
State Path
```

Finalize 把这些具体依赖转成 type-erased `Resources`。

---

## 35. Resources 是运行时依赖注入容器

Tool 不直接持有整个 SessionActor。

调用时从 `ToolCallContext.extensions` 取得 `SharedResources`，再按类型读取需要的依赖：

```rust
resources.get::<Cwd>()
resources.get::<FileSystem>()
resources.get::<MemoryBackend>()
```

这降低 Tool 与 Shell 的直接耦合。

---

## 36. Params 写入 Resources

Finalize 会：

1. 合并默认 `P` 与 ToolConfig Override。
2. 验证合并后的 JSON。
3. Deserialize 成具体 `P`。
4. 写入 `Resources` 的 `Params<P>`。

Tool 执行时读的是已经验证的强类型配置。

---

## 37. Persistent State 与 Ephemeral Resource

Resources 中有两类对象：

- 可持久化 State，例如 Todo、Scheduler State。
- 仅当前进程有效的 Backend/Client/Index。

Finalize 从 `resources_state.json` 恢复已注册 State；每次 Tool 完成后再保存。

ToolIndex 等带连接或 Trait Object 的对象不会按同样方式序列化。

---

## 38. Behavior Version

全局 `behavior_preset` 与每 Tool `behavior_version` 决定 Tool Contract Version。

每 Tool Override 优先于 Preset。

Version 可以改变：

- Description。
- Schema。
- 运行时行为。

最终版本会写入 Dispatch Context 的 `BehaviorVersion`。

---

## 39. `versioned_definition`

默认实现执行：

1. 选 Description Override 或 Metadata Template。
2. 用 TemplateRenderer 渲染。
3. 重命名 Schema Properties。
4. 构造 `ToolDefinition::function`。

复杂 Tool 可以 Override，根据 Effective Params 删除字段或改变说明。

---

## 40. Output Truncation 也影响 Schema/Description

Finalize 会把 Truncation Config 中的限制插入说明，并调整 Schema 中相关边界。

这保证模型看到的契约与 Tool 实际输出限制一致。

如果只在执行层截断、不更新说明，模型会持续请求不可能获得的输出规模。

---

## 41. Local Registry Registration

只有 `config.tools` 中选中的 Tool 才会执行：

```rust
(entry.register_in_local)(&local_registry)
```

候选 Builder 中未选 Tool 不会进入当前 LocalRegistry。

这再次证明 `ToolRegistryBuilder::new()` 不是“全部启用”。

---

## 42. `FinalizedTool`

Finalize 后每项包含：

```text
namespace/id
registry_id
client_name
metadata
definition
effective_params
canonical input_schema
reverse_params
parse_input
contract_version
```

同时保留 Canonical Schema 和 Exported Definition，是因为内部 Requirement/解析和模型可见重命名各有用途。

---

## 43. `registry_id` 与 `client_name`

`client_name` 是模型 Tool Call 使用的名字。

`registry_id` 是 LocalRegistry 查找具体执行句柄的名字。

Native Tool 通常二者相关；动态 MCP Tool 的远端/注册 ID 与模型 Qualified Name 可能不同，因此必须分开保存。

---

## 44. Reverse Param Mapping

模型按 Client-facing Schema 提交：

```json
{"path": "src/lib.rs"}
```

若 Canonical Arg 字段叫 `target_file`，Dispatch 前会用 `reverse_params` 还原：

```json
{"target_file": "src/lib.rs"}
```

模型契约可以兼容不同 Harness，Rust Args 类型保持稳定。

---

## 45. Active Reminder 的筛选

Builder 也注册 LSP、Task Completion、Skill Discovery 等 Cross-cutting Reminder。

Finalize 用最终 `ProposedTool` 评估每个 Reminder Requirement，只保留当前 Toolset 确实能触发的 Reminder。

没有文件 Tool 的 Agent 不需要携带文件路径发现 Reminder。

---

## 46. `FinalizedToolset` 为什么用 `RwLock<Vec<_>>`

Native Toolset 在 Finalize 后基本稳定，但 MCP Tool 会运行期加入和移除。

因此：

- Dispatch/Definition Snapshot 使用短暂 Read Lock。
- MCP Register/Unregister 使用 Write Lock。
- 锁绝不跨 `.await`。

源码注释把它描述为“高频读、低频写”。

---

## 47. `tool_definitions()`

这个函数克隆当前全部 Finalized Definition：

```text
Native + 动态注册的 MCP
```

它用于内部快照、MCP 索引和诊断。

不要直接推断这些 Definition 都会发送给模型。

---

## 48. `tool_definitions_builtins_only()`

Shell 每 Turn 使用的是这个函数。

它过滤 Client Name 中包含 `__` 的 Tool：

```rust
!t.client_name.contains("__")
```

约定上，MCP Tool 使用：

```text
server__tool
```

因此具体 MCP Schema 不进入普通 Turn Request。

---

## 49. Native Tool Schema 常驻请求

`prepare_tool_definitions_inner`：

1. 取 Builtins-only Definitions。
2. 根据 Plan Mode 过滤。
3. 转成 `ToolSpec`。

每个 `ToolSpec` 包含：

```rust
name
description
parameters
```

这是模型 Function Calling 真正看到的契约。

---

## 50. Request 中 Tool 的最终落点

Turn 调用：

```rust
chat_state_handle.build_request(effective_tools, ...)
```

`ChatStateActor::build_conversation_request` 最终构造：

```rust
ConversationRequest {
    items,
    tools: tool_definitions,
    hosted_tools: vec![],
    ...
}
```

Shell 随后再设置 Hosted Tools。

---

## 51. Tool Schema 也消耗 Context

Token 估算公式近似统计：

```text
name bytes + description bytes + serialized parameters bytes
-------------------------------------------------------------
                              4
```

几十或几百个 MCP Schema 若全部常驻，会显著增大每次请求并破坏前缀效率。

---

## 52. 为什么 MCP Tool 被延迟公开

设计目标包括：

- Native Tool Prefix 跨 Turn 保持稳定。
- MCP Progressive 连接时不反复改变 Tool Array。
- 避免所有远端 Schema 消耗 Token。
- 避免新 Tool 接入导致 KV Cache Prefix 变化。
- 让模型先搜索，再用确切参数调用。

`use_tool` 源码注释直接指出稳定 Toolset 与 KV Cache 的关系。

---

## 53. MCP Handshake 后得到什么

MCP Client 从 Server 获取 Tool Registration，典型内容包括：

- Qualified Name。
- Description。
- Input Schema。
- Runtime Tool Adapter。
- Optional Meta/UI 信息。
- `model_visible` 标记。

Shell 的 `register_mcp_tool` 决定是否注册到 ToolBridge。

---

## 54. MCP Qualified Name

格式：

```text
server__tool
```

例如：

```text
linear__save_issue
grafana__search_dashboards
```

双下划线同时承担：

- Server Namespace。
- Model/Runtime 稳定调用名。
- Builtins-only Filter 的识别约定。

---

## 55. Disabled 与 App-only MCP Tool

若 MCP Tool 在配置中 Disabled：

- Registration 被 stash。
- 不注册到 ToolBridge。
- UI 可仍显示 Disabled 状态。

若 `model_visible=false`：

- 可服务 App UI。
- 不进入模型 Tool Runtime。

UI 能看到不等于模型可调用。

---

## 56. 运行期 `register_tool`

`FinalizedToolset::register_tool`：

1. 取得 Tools Write Lock。
2. 拒绝重复 Client Name。
3. 使用远端 Schema Override，而不是 `serde_json::Value` 的泛化 Schema。
4. 注册到 LocalRegistry。
5. Push 新 `FinalizedTool`。

动态 Tool 没有 Param Rename 或 Behavior Version。

---

## 57. 为什么 MCP Schema 必须 Override

MCP Adapter 的 Rust Args 往往只是：

```rust
serde_json::Value
```

从这个类型无法推导 Jira、Slack、Linear 各自字段。

所以真正 Schema 来自远端 `tools/list`，通过 `input_schema_override` 写入 Definition。

---

## 58. MCP Tool 既注册又隐藏并不矛盾

注册的目的：

- Runtime 能按 Qualified Name Dispatch。
- Search Snapshot 能读取完整 Definition。

隐藏的目的：

- 不把每个 Schema放入 Request.tools。

“运行时存在”和“模型常驻看到”是两个投影。

---

## 59. MCP Snapshot

`ToolMetadataSnapshot` 保存：

```text
tools: Vec<ToolMetadata>
servers: Vec<ServerMetadata>
mcp_initialized: bool
```

ToolMetadata 包括：

```text
qualified_name
server_name
tool_name
description
parameters
input_schema
```

它是 Search Index 的一致性快照。

---

## 60. Snapshot 如何刷新

`refresh_mcp_snapshot_and_schedule_reminder_with`：

1. 读取 ToolBridge 全部 Definitions。
2. 选 Name 含 `__` 的本地 MCP Tool。
3. 拆出 Server/Tool Name。
4. 提取 Schema Properties 名称。
5. 合并 Managed Gateway Catalog Tool。
6. 补 Server Instructions。
7. 原子替换 Snapshot。
8. 更新 Gateway Resource Catalog。
9. 标记 MCP Reminder Dirty。

---

## 61. Snapshot 锁为什么是 `std::sync::Mutex`

BM25 Trait 方法是同步函数。

锁只用于快速克隆 Snapshot，不执行 I/O，不跨 `.await`。

使用 Tokio `blocking_lock()` 在单线程 Runtime 中可能 Panic，所以这里选择标准同步 Mutex。

---

## 62. ToolIndex 的依赖反转

Trait 定义在 `xai-grok-tools`：

```rust
trait ToolSearchIndex
```

具体 BM25 实现在 `xai-grok-shell`。

Shell 创建：

```rust
Bm25ToolSearchIndex::new(snapshot)
```

再以：

```rust
ToolIndex(Arc<dyn ToolSearchIndex>)
```

注入 Resources。

Tools crate 不需要反向依赖 Shell。

---

## 63. `search_tool` 是 Native Meta Tool

它本身是普通内置 Function Tool，ID：

```text
search_tool
```

Args：

```json
{
  "query": "linear create issue",
  "limit": 5
}
```

它的 Definition 常驻模型请求，因此模型无需知道任何具体 MCP Tool 就能开始发现。

---

## 64. Search 文档由哪些字段组成

每个 Tool 的 BM25 Document：

```text
server_name
tool_name
description
parameter_names
identifier split components
```

参数名也进入索引，所以查询“issue assignee priority”可能找到创建/更新 Issue Tool。

---

## 65. Identifier 拆分

索引与 Query Normalizer 处理：

- `server__tool`。
- `snake_case`。
- `kebab-case`。
- `camelCase` / `PascalCase`。

例如：

```text
SearchDashboards -> Search Dashboards
read_slack_thread -> read slack thread
```

这提升模型直接复制标识符或使用自然语言时的召回率。

---

## 66. Exact Match 快速路径

BM25 前先进行忽略大小写的精确匹配：

```text
query == qualified_name
or
query == bare tool_name
```

命中后直接返回单项，Score=1.0。

当模型已经知道名字时，不应让相关性排序把它换成近似 Tool。

---

## 67. BM25 每次查询重建

实现从 Snapshot 的几十到低几百个 Tool 动态构建 Search Engine。

源码认为这一规模下耗时低于毫秒级，换来：

- 无长期索引同步状态。
- Snapshot 更新立即生效。
- 实现简单且一致。

这是针对当前数量级的工程取舍。

---

## 68. Search Result 返回完整 Schema

每项包含：

```text
tool_name
server_name
description
score
parameters
input_schema
```

`search_tool` 输出按 Server 分组，但 Server Group 顺序仍由该组最高分决定。

模型拿到 Schema 后才能构造 `use_tool.tool_input`。

---

## 69. Search Readiness

`SearchSnapshot` 还有：

```text
total_hidden_tools
is_ready
```

输出状态：

- `ready`：MCP 初始化完成。
- `partial`：Progressive 连接尚未全部完成，结果可能不完整。

空结果时 Note 会区分“仍在连接”和“当前 Session 根本没有 MCP Tool”。

---

## 70. Description Truncation

Search Output 中 MCP Description 最长 2048 Characters，超出加：

```text
… [truncated]
```

Input Schema 不因这个 Description 上限被替换成摘要。

搜索结果需要足够解释能力，但不能让异常长远端描述占满 Tool Result。

---

## 71. MCP Server Reminder 不是 Tool Catalog

Session 会向模型公告：

```text
Connected MCP servers:
- linear (12 tools): ...
- slack (8 tools): ...
```

它只告诉模型有哪些 Server、Tool 数量和 Server 说明，不列出所有完整 Schema。

具体 Tool 仍要求通过 `search_tool` 发现。

---

## 72. 为什么 Reminder 强调先 Search

注入提示明确要求：

```text
先 search_tool 获取精确 Schema
再 use_tool
不要猜参数名
```

远端 Tool Schema 可随 Server 版本变化；模型训练记忆中的字段不可靠。

---

## 73. `use_tool` 是稳定 Meta Dispatch Tool

它的 Args：

```rust
tool_name: String
tool_input: serde_json::Value
```

`tool_name` 必须是 Qualified MCP Name；`tool_input` 应严格符合 Search Result 的 Schema。

其 Function Definition 也常驻每个 Turn。

---

## 74. “必须先搜索”是不是 Runtime 强制状态机

不是。

`UseToolInput` 文档要求目标应先经 `search_tool` 发现，但 `UseTool::run` 没有维护“本 Turn 搜索过哪些名称”的集合。

如果模型已经知道一个真实 Qualified Name 和正确参数，它可以直接尝试 Dispatch。

“先 Search”主要由 Prompt/Description 契约约束，并通过错误引导恢复。

---

## 75. 为什么不在 `use_tool` 重复校验远端 JSON Schema

本地 MCP Adapter/远端 Server 是最终参数契约执行者。

`use_tool.tool_input` 的外层 Schema允许任意 Object，因为每个目标 Tool 结构不同。

具体 Schema通过 Search Result 交给模型，而不是动态改变 `use_tool` 自身 Schema。

---

## 76. `InnerDispatch`

每次普通 Tool Call 的 `prepare_dispatch` 都在 Context Extensions 放入：

```rust
InnerDispatch(Arc<dyn ToolDispatch>)
```

`use_tool` 从中再次调用真正 MCP Target。

这样 Meta Tool 不需要持有 ToolBridge，也不会形成 crate 层面的反向依赖。

---

## 77. 为什么 Inner Dispatch 绕开 Outer Bridge

若 `use_tool` 在 ToolBridge 的执行锁/路径内部再次调用 ToolBridge，可能自锁。

Inner Dispatch 直接进入 `FinalizedToolset::call_raw`：

- 查 Target FinalizedTool。
- 反向映射参数。
- 找 LocalRegistry Handle。
- 执行并转换 Output。

不重复走 Outer Mutex。

---

## 78. `call_raw` 为什么跳过 Reminder/Persistence

外层调用是 `use_tool`。

若 Inner Target 和 Outer Meta Tool 都执行 Post-processing，会：

- Reminder 重复。
- Resource State 保存两次。
- Tool Result 包装两次。

所以 Inner `call_raw` 只执行 Target；Outer `call("use_tool")` 统一完成一次 Finalize Output。

---

## 79. Effective Tool Name

`prepare_dispatch` 发现调用名是 `use_tool` 时，会解析内部 `tool_name`，保存为 `effective_tool_name`。

最终 `ToolRunResult` 因而同时知道：

- Wire 上调用的是 `use_tool`。
- 实际目标是 `linear__save_issue`。

Hooks、Telemetry、Permission 和 UI 才能呈现真实行为。

---

## 80. Native Tool 误走 `use_tool`

若模型写：

```json
{"tool_name": "read_file", ...}
```

`EnabledNativeToolNames` 会识别它是 Native Tool，并返回定向纠错：直接调用 `read_file`，不要经过 `use_tool`。

这比笼统说“无效 MCP 名称”更容易让模型恢复。

---

## 81. Unknown Unqualified Name

若名称既不是 Native，也不含 `__`，`use_tool` 返回：

- MCP 名称必须是 `server__tool`。
- 使用实际 Client-facing `search_tool` 名称发现。

错误文本中的 Tool Name 通过 TemplateRenderer 获取，因此 Rename 后仍正确。

---

## 82. Local MCP 与 Managed Gateway

`dispatch_mcp_tool` 支持两类 Target：

1. Local/普通 MCP：通过 InnerDispatch 到 LocalRegistry。
2. Managed Gateway Tool：通过 Catalog 找到 Call ID，再调用 Gateway Client。

两类 Tool 共享 Search Result 和 `use_tool` 模型契约。

---

## 83. Gateway Tool 不一定注册进 LocalRegistry

Gateway Catalog 项会写入 Search Snapshot 与 `ManagedGatewayToolCatalog`。

调用时 `use_tool` 根据 Catalog 路由到远端 Gateway Client。

所以“可被搜索”不必意味着“存在本地 Tool Handle”。

---

## 84. Local 与 Gateway 同名冲突

若同一个 `server__tool` 同时存在本地 MCP 和 Gateway：

1. 先尝试 Local Dispatch。
2. Local 真正执行并报业务错误时，错误直接传播。
3. 只有 NotFound/ID Reject 才 fallback Gateway。

不能把真实 Local 失败静默改成另一套远端调用。

---

## 85. Gateway 参数归一化

`normalize_mcp_arguments` 处理：

- JSON String 若内部可解析为 Object，则解包成 Object。
- Null 转空 Object。
- 其他值原样保留。

这是兼容模型或中间层偶尔把 JSON Object 再编码成字符串的情况。

---

## 86. MCP Output 截断

`use_tool` 完成 Target Dispatch 后调用 MCP 专用 Truncation。

它使用当前 Tool Context 和 Config 决定上限，并可能把完整结果 Dump 到文件，只把预算化内容放回模型。

搜索 Schema 与执行结果分别有独立预算。

---

## 87. Progressive MCP 初始化

`McpInitStrategy::Progressive` 不阻塞第一 Prompt。

此时：

- Native Toolset 已可用。
- Search Snapshot 可能 `is_ready=false`。
- MCP 后台连接完成后刷新 Snapshot。
- 下一次推理前注入 Server Delta Reminder。
- Request Tool Array 仍不需要加入每个新 MCP Schema。

---

## 88. Blocking MCP 初始化

`McpInitStrategy::Blocking` 在首轮准备 Tool Definition 时等待 MCP Initialization。

但最终发送给模型仍是 Builtins-only Definitions。

Blocking 保证搜索目录完整，不代表把所有 MCP Tool 直接放入 Request。

---

## 89. MCP 动态变化为什么不改 Request Tool Array

连接、断线、认证恢复、Tools Changed 都会刷新内部 Toolset/Snapshot。

模型常驻 Tool Array 继续保留相同的：

```text
search_tool
use_tool
```

变化通过 Reminder 和 Search Result 暴露，从而保持 Prompt Cache Prefix 更稳定。

---

## 90. Model Switch/Rebuild 后的 MCP 恢复

Agent/ToolBridge 重建会失去动态注册项。

`re_register_mcp_tools_on_rebuilt_bridge`：

1. 快照现有 MCP Client Arc。
2. 锁外重新调用 ListTools。
3. 将 Registration 注册进新 Bridge。
4. 刷新 Search Snapshot。
5. 发送 UI Tools Changed。

连接可复用，Tool Runtime 必须重新挂接。

---

## 91. Direct Native Tool Dispatch

模型调用 Native Tool 时：

```text
Function Name + JSON Arguments
        |
        v
try_parse / Prepare Tool Call
        |
权限、Hooks、并发调度
        |
ToolBridge.call
        |
FinalizedToolset.prepare_dispatch
        |
LocalRegistry.execute
```

本篇重点是注册与搜索，但这个执行入口用于验证 Schema 最终如何落回 Rust Args。

---

## 92. `try_parse`

在真正执行前：

1. 按 Client Name 查 FinalizedTool。
2. Client Params 反向重命名。
3. 调用注册时捕获的 `parse_input`。
4. Deserialize 为具体 `T::Args`。
5. 转成统一 `ToolInput` Enum。

无效参数在进入 Tool Run 前就可被拒绝。

---

## 93. `prepare_dispatch`

它在短暂 Read Lock 内复制：

- Registry ID。
- Output Converter。
- Reverse Params。

锁释放后再进入异步执行。

同时构造 ToolCallContext，注入：

- SharedResources。
- TemplateRenderer。
- CWD Override。
- Cancellation。
- Behavior Version。
- InnerDispatch。
- Workspace Viewer Context。

---

## 94. Streaming Dispatch

LocalRegistry 返回 `ToolStream`：

- Progress Item 原样向外转发。
- Terminal Error 直接结束。
- Terminal Success 进入统一 `finalize_output`。

非流式 `call()` 只是消费这个 Stream，直到 Terminal。

因此流式与非流式不会维护两套执行语义。

---

## 95. `finalize_output`

统一尾处理：

1. 将动态 Value 转回 `ToolOutput`。
2. 执行 Active Cross-cutting Reminders。
3. 把 Output 渲染成模型文本。
4. 拼入 Reminder。
5. 保存 Resources State。
6. 生成 `ToolRunResult`。

这也是 `use_tool` Inner Target 不能重复走的尾处理。

---

## 96. Hosted Tool 是第三种工具形态

除 Native Function Tool 和 MCP Meta-dispatch 外，还有：

```text
HostedTool::WebSearch
HostedTool::XSearch
```

它们由模型后端直接执行，以原生 Responses API Tool 类型放在：

```rust
ConversationRequest.hosted_tools
```

不经过 LocalRegistry Function Dispatch。

---

## 97. Function Web Search 与 Hosted Web Search

源码中可能同时存在本地 Function `web_search` 和 Backend Hosted Web Search 能力。

当 Backend Search Active 时，`turn_base_tool_specs` 会删除 Function `web_search`，避免同一能力以两种协议同时暴露；Hosted Tool 则写入 Request 的独立字段。

---

## 98. Hosted Tool 的双重 Gate

是否发送 Hosted Tool 由：

```text
Agent build-time backend_search_enabled
AND
当前 Model supports_backend_search
```

共同决定。

还会应用 Per-turn Tool Overrides，例如搜索截止时间等。

---

## 99. 三种 Tool 形态对照

| 形态 | 模型如何看到 | 谁执行 | Schema 在哪里 |
| --- | --- | --- | --- |
| Native Function | `request.tools` | LocalRegistry | 每 Turn ToolSpec |
| MCP Integration | `search_tool` 结果 + `use_tool` | Local MCP/Gateway | Search Snapshot |
| Hosted Tool | `request.hosted_tools` | Model Backend | 后端原生协议 |

这三类不能笼统地叫“注册到 ToolBridge”。

---

## 100. Tool Definition 与 Tool Implementation

`ToolDefinition` 只有模型契约：

```text
type=function
name
description
parameters
```

它不包含 Rust `run()`、Backend Client、权限或状态。

模型看见 Definition，并不意味着它能绕过 Runtime 直接执行 Implementation。

---

## 101. Schema 不是权限策略

JSON Schema 验证参数形状。

它不决定：

- 文件路径是否允许。
- 命令是否需要审批。
- MCP 写操作是否被 Hook 拦截。
- Hosted Search 是否受 Override 限制。

这些由 Tool Preparation、Permission、Hook、Sandbox 和 Backend Policy 处理。

---

## 102. Description 也不是强制策略

Description 告诉模型应该怎样调用。

真正强制必须落在：

- Args Deserialize/Schema。
- Requirement Validation。
- Permission Gate。
- Runtime Checks。
- Backend Contract。

例如“必须先 search_tool”主要是行为指导，而不是 `use_tool` 内部状态机。

---

## 103. Tool Name Rename 的端到端不变量

一次 Rename 必须同步影响：

1. Exported ToolDefinition Name。
2. Prompt/Description 中引用的 Tool Name。
3. Model Tool Call 查找。
4. Params Rename。
5. Dispatch 时反向映射。
6. Error/Reminder 中的名称。

`TemplateRenderer` 与 `reverse_params` 是维持这条链的核心。

---

## 104. 为什么不要在 Prompt 里硬编码 Tool 名

Agent 可能运行在 Grok Build、Codex、Cursor Compat 或 OpenCode Harness 下。

硬编码 `read_file` 会在 Tool 被 Rename 为 `Read` 时误导模型。

源码用 Kind-based Placeholder 把“语义能力”与“Wire Name”解耦。

---

## 105. Tool Search 的召回与精确性边界

BM25 适合：

- Tool/Server Name。
- 动作关键词。
- Description 词汇。
- Parameter Name。

它不理解真实账户数据，也不执行远端 Tool 来判断结果。

搜索到“create issue”只说明契约文本匹配，不保证用户具备权限或目标 Workspace 可用。

---

## 106. Search Result 的安全边界

Search 返回 Schema，不执行 Tool。

真正的外部读写发生在后续 `use_tool`，仍经过：

- Tool Call Parsing。
- Hook/Permission 判断。
- Local MCP 或 Gateway Dispatch。
- Output Error/Truncation。

发现能力与使用能力是两步。

---

## 107. 常见误解一：所有 Tool Schema 都在 System Prompt

不对。

Function Tool Definition 是 Request 的独立结构字段，不是简单拼进 System Prompt 字符串。

模型 Provider 最终会把它编码成对应 API 的 Function Tool 协议。

---

## 108. 常见误解二：MCP 连接后模型立刻多了几十个 Tool

Runtime 内部确实多了 Tool Definition 和 Dispatch Handle。

但普通 Turn 的 `request.tools` 仍过滤具体 MCP Tool，只保留稳定 Meta Tool。

模型获得的是“可搜索能力增长”，不是常驻 Function 数量增长。

---

## 109. 常见误解三：Search 后 Tool 才被注册

本地 MCP Tool 通常在 Handshake/ListTools 后已经注册。

Search 只检索现有 Snapshot 并把 Schema交给模型，不负责当场注册目标。

这是“先注册到 Runtime，后按需公开给模型”。

---

## 110. 常见误解四：`use_tool` 是万能 Tool Router

它只面向 MCP Integration。

Native Tool 应直接调用；Hosted Tool 由 Backend 原生执行。

源码专门为 Native Tool 误路由返回纠错信息。

---

## 111. 一个 Native Tool 的完整旅程

以 Read 为例：

```text
ToolRegistryBuilder::new
-> register_with_params<ReadFileTool, ReadFileParams>
-> ToolEntry + Args Schema
-> Agent ToolConfig 选择
-> validate_config
-> Finalize Description/Schema/Params
-> LocalRegistry Register
-> FinalizedTool
-> builtins_only Definition
-> ToolSpec
-> ConversationRequest.tools
-> Model Tool Call
-> reverse params + parse Args
-> LocalRegistry.execute
-> finalize_output
-> ToolResult 回到模型
```

---

## 112. 一个 MCP Tool 的完整旅程

```text
MCP Server connect
-> tools/list
-> McpToolRegistration
-> register_mcp_tool
-> FinalizedToolset.register_tool
-> LocalRegistry + FinalizedTool
-> refresh ToolMetadataSnapshot
-> MCP Server Reminder
-> model calls search_tool
-> BM25 result with full input_schema
-> model calls use_tool
-> InnerDispatch.call_raw(target)
-> MCP Adapter/Server
-> MCP Output truncate
-> outer finalize_output
-> ToolResult 回到模型
```

---

## 113. 一个 Managed Gateway Tool 的旅程

```text
Gateway Catalog
-> Snapshot Metadata + ManagedGatewayToolCatalog Resource
-> search_tool result
-> use_tool
-> catalog lookup call_id
-> Gateway Client.call_tool
-> normalize response content/error
-> truncate
-> ToolResult
```

它不要求 LocalRegistry 中有同名 Handle。

---

## 114. 推荐调试断点：Native Tool 不见了

依次检查：

1. `ToolRegistryBuilder::known_tool_ids` 是否有 Candidate。
2. `AgentBuilder::build` 最终 `tool_config.tools` 是否保留。
3. `validate_config` 是否拒绝 Requirement。
4. `FinalizedToolset::tool_definitions` 是否存在。
5. `tool_definitions_builtins_only` 是否存在。
6. Plan Mode/Backend Search 是否二次过滤。
7. `ConversationRequest.tools` 是否包含。

---

## 115. 推荐调试断点：MCP Tool 搜不到

依次检查：

1. MCP Handshake/ListTools 是否成功。
2. `model_visible` 是否为 true。
3. 是否被 Disabled Config 过滤。
4. `register_mcp_tools` 是否成功。
5. `tool_definitions()` 中是否有 `server__tool`。
6. `ToolMetadataSnapshot.tools` 是否刷新。
7. `mcp_initialized` 是否仍为 false。
8. Search Query 是否命中 Name/Description/Params。

---

## 116. 推荐调试断点：Search 有结果但 Use 失败

依次检查：

1. `tool_name` 是否完整复制 Qualified Name。
2. `tool_input` 是否符合返回 Schema。
3. Target 是 Local 还是 Gateway。
4. LocalRegistry 是否仍有 Handle。
5. Gateway Catalog 是否有 Source/Client。
6. Server 是否断线或需要认证。
7. Permission/Hook 是否拒绝。
8. 远端返回是否是业务 Error。

---

## 117. 修改 Registry 时的风险清单

新增 Native Tool 至少检查：

1. Runtime Tool Trait。
2. ToolMetadata。
3. Args JsonSchema。
4. ToolOutput Conversion。
5. Builder Registration。
6. Default Tool Config。
7. Feature/Capability Gate。
8. Description Template Name Resolution。
9. Permission/Hook Classification。
10. Targeted Tests。

只实现 `run()` 不会自动出现在模型面前。

---

## 118. 修改 MCP 搜索时的风险清单

至少检查：

- Local 与 Gateway Metadata 合并。
- Disabled Tool 过滤。
- Qualified Name 稳定性。
- Exact Match 快速路径。
- Identifier Normalization。
- Schema 完整性。
- Snapshot Ready 状态。
- Server Reminder Fingerprint。
- Rebuild/Re-register。
- Subagent MCP Inheritance。

---

## 119. 关键测试群：Registry/Finalize

`xai-grok-tools` Registry Tests 覆盖：

- Schema 生成规范化。
- Params 验证与合并。
- Requirement Failure。
- Tool/Param Rename。
- Description Template。
- Behavior Version。
- Dynamic Register/Unregister。
- Builtins-only 过滤。
- Runtime Dispatch 与 Output Conversion。
- Resources 与 Reminder。

---

## 120. 关键测试群：Search Tool

覆盖：

- 无 ToolIndex 的友好空结果。
- Ready/Partial 状态。
- Server 分组与 Score 顺序。
- Description 截断。
- Full Input Schema 返回。
- Gateway Connector Name。
- 空 MCP Session 提示。

---

## 121. 关键测试群：BM25 Index

`session/tool_index.rs` 有大量针对真实命名风格的查询测试：

- Exact Qualified/Bare Name。
- Snake/Kebab/Camel 拆分。
- Server Name Query。
- Natural Language Action。
- Parameter Name Query。
- Grafana/Mattermost 等接近生产命名集合。
- MCP 格式变体。

搜索质量不是只用几个 toy case 验证。

---

## 122. 关键测试群：Use Tool

覆盖：

- InnerDispatch 缺失。
- Local MCP Dispatch。
- Gateway Dispatch。
- Local/Gateway 同名优先级。
- Native Tool 纠错。
- Unknown Unqualified Name。
- Stringified JSON 参数。
- Error Output。
- Large Output Dump/Truncate。
- Effective Target Name。

---

## 123. 阅读练习一：打印当前 Request Toolset

在一次真实 Turn 比较：

```text
bridge.tool_definitions()
bridge.tool_definitions_builtins_only()
request.tools
request.hosted_tools
```

然后连接一个 MCP Server 再比较。

预期：第一项增长，后三者中只有 Search/Reminder 能反映 MCP 变化，具体 MCP Schema 不直接加入 `request.tools`。

---

## 124. 阅读练习二：Rename Tool

把 Read Tool 改名，并把一个参数也改名，检查：

1. Tool Definition Name。
2. JSON Schema Properties。
3. 其他 Tool Description 中的引用。
4. Prompt 中的引用。
5. 模型参数是否反向映射到 Rust Args。
6. 错误提示是否使用新名字。

这能快速建立 TemplateRenderer 的端到端认识。

---

## 125. 阅读练习三：MCP Search

准备三个 Tool：

```text
linear__save_issue
linear__list_issues
slack__send_message
```

尝试查询：

```text
save_issue
linear create ticket
assignee priority
sendMessage
```

观察 Exact Match、Identifier Split、Description 与 Parameter Name 各自如何影响结果。

---

## 126. 本篇最重要的状态分层

| 层 | 权威对象 | 回答的问题 |
| --- | --- | --- |
| Candidate | `ToolRegistryBuilder.tools` | 二进制认识什么 |
| Selected Config | `ToolServerConfig.tools` | 当前 Agent 想启用什么 |
| Runtime | `FinalizedToolset` + LocalRegistry | 当前 Session 能执行什么 |
| Search | `ToolMetadataSnapshot` | 哪些 MCP Tool 可按需发现 |
| Model Native Surface | `ConversationRequest.tools` | 当前请求直接公开什么 Function |
| Model Hosted Surface | `ConversationRequest.hosted_tools` | 后端原生执行什么 |

调试 Tool 时先确定你观察的是哪一层。

---

## 127. 设计收益

- Rust Args 与 JSON Schema 同源。
- Candidate 与 Session Selection 分离。
- Tool Rename/Param Rename 可端到端兼容。
- Description 能引用最终 Tool 名。
- Typed Resources 降低 Tool 与 Session 耦合。
- MCP Tool 可动态加入而不膨胀每次 Request。
- Search 返回实时 Schema，减少模型猜参数。
- `search_tool`/`use_tool` 保持 Prompt Prefix 稳定。
- Hosted Tool 与本地 Function Tool 可以按模型能力切换。

---

## 128. 设计代价

- “已注册”有多层含义。
- Tool Name、Registry ID、Client Name、Qualified Name 容易混淆。
- Native/MCP/Gateway/Hosted 有不同执行面。
- Builder 与 Shell 都会做 Tool Filter。
- MCP Hidden Tool 仍需维护 Runtime Definition 与 Search Snapshot 两套投影。
- Meta Dispatch 需要精心避免锁递归和重复 Post-processing。
- Description 指令并不天然等于 Runtime 强制策略。

---

## 129. 本篇 Glossary

| 名词 | 白话解释 | 本篇对应物 |
| --- | --- | --- |
| Tool | 模型可请求调用的一项能力 | Read、Bash、SearchTool 等 |
| Native Tool | 本地编译进二进制的 Function Tool | `read_file`、`apply_patch` |
| MCP Tool | MCP Server 在运行期提供的 Tool | `linear__save_issue` |
| Hosted Tool | 模型后端原生执行的 Tool | WebSearch、XSearch |
| Meta Tool | 用来发现或转发其他 Tool 的稳定入口 | `search_tool`、`use_tool` |
| Candidate Registry | 二进制认识的候选 Tool 集 | `ToolRegistryBuilder.tools` |
| Selected Toolset | Agent 配置选择的 Tool | `ToolServerConfig.tools` |
| Finalize | 把配置和 Session 依赖变成可执行 Toolset | `finalize_with_trunc_config` |
| FinalizedTool | 已解析名称、Schema、配置和执行句柄的 Tool | Runtime Entry |
| LocalRegistry | 保存本地可执行 Tool Handle 的 Registry | `xai_computer_hub_sdk::LocalRegistry` |
| ToolDefinition | 模型可见的 Function 契约 | Name/Description/Parameters |
| ToolSpec | Request 使用的简化 Tool Definition | `ConversationRequest.tools` |
| JSON Schema | Tool 参数结构说明 | Schemars Draft 07 输出 |
| Args | 模型每次调用传入的参数 | `T::Args` |
| Tool Params | Session 对 Tool 行为的配置 | `Params<P>` |
| ToolMetadata | Grok Build 的 Tool 语义元数据 | Kind/Namespace/Description |
| ToolKind | 与名字无关的能力分类 | Read/Edit/Search/UseTool |
| ToolNamespace | Tool 实现所属 Harness | GrokBuild/Codex/OpenCode |
| Fully-qualified ID | Candidate Registry 内的完整身份 | `GrokBuild:read_file` |
| Client Name | 模型实际调用的名字 | `read_file` 或 `Read` |
| Registry ID | LocalRegistry 查找执行句柄的名字 | `FinalizedTool.registry_id` |
| Qualified MCP Name | 带 Server Namespace 的 MCP 名 | `server__tool` |
| Name Override | 修改模型可见 Tool 名 | `ToolConfig.name_override` |
| Param Rename | 修改模型可见参数名 | `params_name_overrides` |
| Reverse Mapping | 执行前还原 Rust 字段名 | `reverse_params` |
| TemplateRenderer | 用最终 Tool/Param 名渲染说明 | `${{ tools.by_kind.read }}` |
| Requirement | Toolset 必须满足的依赖表达式 | `Expr<ToolRequirement>` |
| Resources | Tool 运行时的类型化依赖容器 | CWD、FS、Backend、State |
| Type Erasure | 把不同泛型 Tool 放进同一容器 | Boxed Closures/Trait Objects |
| Dispatch | 根据名字和参数执行 Tool | `FinalizedToolset::call` |
| InnerDispatch | Meta Tool 内部调用目标的通道 | `use_tool -> call_raw` |
| Post-processing | Tool 成功后的统一尾处理 | Reminder、Format、Persist |
| Tool Search Index | MCP Tool 的可搜索元数据接口 | `ToolSearchIndex` |
| BM25 | 基于词频和文档区分度的排名算法 | `Bm25ToolSearchIndex` |
| Search Snapshot | 一致的 MCP Tool/Server 元数据副本 | `ToolMetadataSnapshot` |
| Exact Match | 名称完全相同时绕过排名 | Search Fast Path |
| Progressive Init | MCP 后台连接，不阻塞第一 Prompt | `McpInitStrategy::Progressive` |
| Blocking Init | 首轮前等待 MCP 初始化 | `McpInitStrategy::Blocking` |
| KV Cache | 模型复用稳定输入前缀的缓存 | 隐藏 MCP Schema 的收益之一 |
| Tool Pack | 外部包向 Builder 加候选 Tool 的扩展点 | `register_tool_pack` |
| Behavior Version | Tool 契约的兼容版本 | Preset/Per-tool Override |
| Effective Tool Name | Meta Dispatch 真正调用的目标名 | `linear__save_issue` |
| Gateway Catalog | Managed Tool 名到远端 Call ID 的映射 | `ManagedGatewayToolCatalog` |

---

## 130. 源码定位表

| 主题 | 文件/符号 |
| --- | --- |
| Agent Tool 组装 | `xai-grok-agent/src/builder.rs::AgentBuilder::build` |
| Agent Tool 声明 | `xai-grok-agent/src/config.rs::AgentDefinition` |
| Candidate Builder | `xai-grok-tools/src/registry/types.rs::ToolRegistryBuilder` |
| Generic Registration | `register` / `register_with_params` |
| Config Validation | `validate_config` |
| Finalize | `finalize_with_trunc_config` |
| Runtime Toolset | `FinalizedToolset` / `FinalizedTool` |
| Schema Generation | `generate_schema` |
| Dynamic MCP Register | `FinalizedToolset::register_tool` |
| Native Definition Snapshot | `tool_definitions_builtins_only` |
| Dispatch | `prepare_dispatch` / `call_streaming_with_cancellation` |
| Inner Dispatch | `call_raw` / `InnerDispatchForToolset` |
| Output Tail | `finalize_output` |
| Metadata Trait | `types/tool_metadata.rs::ToolMetadata` |
| Description Renderer | `types/template_renderer.rs::TemplateRenderer` |
| Function Definition | `types/definition.rs::ToolDefinition` |
| Search Tool | `implementations/search_tool/mod.rs::SearchTool` |
| Search Trait | `types/tool_index.rs::ToolSearchIndex` |
| BM25 Index | `xai-grok-shell/src/session/tool_index.rs::Bm25ToolSearchIndex` |
| MCP Registration | `acp_session_impl/mcp.rs::register_mcp_tool` |
| Snapshot Refresh | `acp_session_impl/mcp_snapshot.rs::refresh_mcp_snapshot_and_schedule_reminder_with` |
| Use Tool | `implementations/use_tool/mod.rs::UseTool` |
| MCP Meta Dispatch | `dispatch_mcp_tool` |
| Per-turn Definition | `acp_session_impl/sampler_turn.rs::prepare_tool_definitions_inner` |
| Request Assembly | `xai-chat-state/src/actor/request_builder.rs` |
| Tool Token Estimate | `xai-chat-state/src/actor/state.rs::estimate_tool_definition_tokens` |

---

## 131. 验证命令

```sh
# Registry、Finalize、Schema、Rename、动态注册与 Dispatch
cargo test -p xai-grok-tools registry --lib -- --test-threads=1

# search_tool 输出契约
cargo test -p xai-grok-tools search_tool --lib -- --test-threads=1

# use_tool Meta Dispatch
cargo test -p xai-grok-tools use_tool --lib -- --test-threads=1

# BM25 索引和真实查询集合
cargo test -p xai-grok-shell tool_index --lib -- --test-threads=1

# Agent Tool Allow/Deny、Alias、Hosted Tool
cargo test -p xai-grok-agent tool --lib -- --test-threads=1

# 最终 Request 是否携带 Tool Definitions
cargo test -p xai-chat-state tool_definitions --lib -- --test-threads=1
```

---

## 132. 一句话总结

Grok Build 先用强类型 Rust Tool 构建候选 Registry，再由 Agent Config 与 Session 能力选出并 Finalize 成可执行 Toolset；Native Tool 的完整 Schema 直接进入每次模型请求，而动态 MCP/Gateway Tool 留在运行时与 BM25 Snapshot 中，由稳定的 `search_tool` 按需公开精确 Schema，再由 `use_tool` 通过 InnerDispatch 或 Gateway 完成真实调用。
