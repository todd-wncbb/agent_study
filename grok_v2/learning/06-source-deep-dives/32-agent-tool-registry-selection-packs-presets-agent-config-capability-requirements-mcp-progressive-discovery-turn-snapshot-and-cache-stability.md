# 源码精读 32：Agent Tool Registry Selection——Tool Pack、Preset、Agent 配置、Capability、Requirements、MCP Discovery、Turn Snapshot 与缓存稳定性

> 源码基线：`ed6d543`
>
> 上一篇解释单个 Tool 的 Schema Contract。本篇回答更靠前的问题：代码明明注册了一个工具，为什么某个 Agent、某个 Session、甚至某一轮请求里仍可能看不到它？

---

## 1. 本篇解决什么问题

读完后应能回答：

- “代码中存在”“Registry 已注册”“Session 已启用”“本轮已公告”有什么区别？
- `ToolRegistryBuilder::new()` 为什么注册大量工具，却不把它们全发给模型？
- Tool Pack 和 Toolset Preset 分别扩展什么？
- AgentDefinition 的 `tool_config`、`tools`、`disallowed_tools` 如何组合？
- `inject_default_tools` 为什么是严格 harness 的重要开关？
- Memory、Web、LSP、图片、视频、Plan、Subagent 工具怎样按环境加入或删除？
- Capability Mode 为什么按 ToolKind 过滤，而不是硬编码工具名？
- 未知 kind 的动态工具为何在 capability filter 中被保留？
- allowlist 出现无法解析的条目时，为什么 function tools 选择 fail-open？
- session operator clamp 为什么必须最后应用？
- Tool Requirements 怎样验证依赖工具、配置参数和可见输入字段？
- MCP 工具为何注册进 FinalizedToolset，却不直接进入每轮模型 Tool 列表？
- `search_tool`、BM25 ToolIndex 与 `use_tool` 怎样组成稳定的动态发现面？
- Blocking 与 Progressive MCP initialization 如何影响首轮可见信息？
- 每个 Turn 在哪里取得 Tool Definition 快照？
- Forked session 怎样继承与父请求一致的 ToolSpec？
- Hosted Search 与本地 Function Tool 如何避免重复公告？

---

## 2. 先给出选择漏斗

```text
编译期/进程期可注册全集
ToolRegistryBuilder::new()
  + external Tool Packs
                │
                ▼
Agent Definition / Toolset Preset 候选集
                │
                ▼
AgentBuilder 环境注入与功能 Gate
  memory / web / lsp / image / video / plan / task / workflow
                │
                ▼
Agent allowlist + denylist + subagent directive
                │
                ▼
Capability Mode + Session operator clamp
                │
                ▼
Requirements validation + Finalize
                │
                ▼
FinalizedToolset
  built-ins + runtime MCP registrations
                │
                ▼
prepare_tool_definitions_inner
  built-ins only + plan-mode filter
                │
                ▼
turn_base_tool_specs
  backend-search collision drop
                │
                ├─ optional StructuredOutput synthetic tool
                ▼
ConversationRequest.tools
```

因此：

```text
registered ⊃ configured ⊃ finalized ⊃ advertised this turn
```

这些集合有时相等，但概念上必须分开。

---

## 3. 核心源码地图

| 领域 | 主要源码 |
| --- | --- |
| Tool Pack 与 Registry Builder | `xai-grok-tools/src/registry/types.rs` |
| Tool Requirement 表达式 | `xai-grok-tools/src/types/requirements.rs` |
| ToolKind / ToolNamespace | `xai-grok-tools/src/types/tool.rs`、`tool_taxonomy.rs` |
| Capability Mode filter | `xai-grok-tools/src/implementations/grok_build/task/types.rs` |
| ToolBridge | `xai-grok-tools/src/bridge.rs` |
| Agent Toolset Presets | `xai-grok-agent/src/config.rs` |
| AgentBuilder 选择流水线 | `xai-grok-agent/src/builder.rs` |
| Agent Tool Definitions API | `xai-grok-agent/src/agent.rs` |
| Subagent capability 交集 | `xai-grok-shell/src/agent/subagent/handle_request.rs` |
| Turn Tool 快照 | `xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs` |
| Turn 请求组装 | `xai-grok-shell/src/session/acp_session_impl/turn.rs` |
| Session Tool snapshot command | `xai-grok-shell/src/session/acp_session_impl/run_loop.rs` |
| MCP 注册 | `xai-grok-shell/src/session/acp_session_impl/mcp.rs` |
| MCP ToolIndex 注入 | `xai-grok-shell/src/session/acp_session_impl/spawn.rs` |
| MCP BM25 Index | `xai-grok-shell/src/session/tool_index.rs` |
| search_tool | `xai-grok-tools/src/implementations/search_tool/mod.rs` |
| use_tool | `xai-grok-tools/src/implementations/use_tool/mod.rs` |

---

# 第一部分：四个“工具存在”层级

## 4. 代码存在

仓库中有 Tool struct、Args、Output 和 `run()`，只说明 binary 可以编译该实现。

它不保证 Registry Builder 注册，也不保证 Agent 会启用。

## 5. Builder 已注册

`ToolRegistryBuilder` 知道 fully-qualified ID、schema、parser、metadata 和 local dispatch handle。

这是候选能力全集。

## 6. FinalizedToolset 已启用

只有出现在 `ToolServerConfig.tools`、通过所有过滤和 Requirements 的条目，才进入当前 Agent 的 FinalizedToolset。

## 7. 当前 Turn 已公告

Session 还会从 FinalizedToolset 取得 built-ins-only 快照，应用 backend/模式策略，再构建 `ConversationRequest.tools`。

这才是模型本轮实际看到的集合。

## 8. 工具可以“能执行但未直接公告”

动态 MCP Tool 注册在 FinalizedToolset，供 `use_tool` 目标 dispatch；但本轮 Function Tool list 故意不直接列出每个 MCP Tool。

## 9. 工具可以“公告入口而非真实目标”

模型始终看见稳定 `search_tool` 和 `use_tool`；真实第三方 Tool name/schema 通过搜索结果按需出现。

## 10. 四层表格

| 层级 | 数据结构 | 典型问题 |
| --- | --- | --- |
| 代码 | Rust modules/types | binary 是否具备实现？ |
| 注册 | `ToolRegistryBuilder.tools` | Registry 是否知道它？ |
| 启用 | `FinalizedToolset.tools` | 本 Session 能否 lookup/dispatch？ |
| 公告 | `ConversationRequest.tools` | 模型本轮是否能直接选择？ |

---

# 第二部分：Registry Builder 是进程内能力目录

## 11. `ToolRegistryBuilder::new()` 注册什么

它注册多套实现：

- GrokBuild 标准工具；
- GrokBuildConcise；
- Hashline 文件工具；
- Codex 工具；
- OpenCode 工具；
- Memory；
- SearchTool/UseTool；
- Plan、Scheduler、Goal、Workflow；
- 图片、视频、Web、LSP 等。

## 12. 为什么注册多套互斥实现

Builder 是“可选零件仓库”，不是一个 Agent 的最终工具箱。

不同 Agent preset 从同一 binary 中选择不同协议和行为风格。

## 13. 注册使用 fully-qualified ID

典型 key：

```text
GrokBuild:read_file
Codex:read_file
OpenCode:read
GrokBuildHashline:hashline_read
```

Namespace 让内部 ID 不因 client-facing 同名而冲突。

## 14. 同一个 ToolKind 可有多个实现

标准 read、Codex read、Hashline read 都可以属于 Read kind。

ToolKind 表示能力语义，不是唯一实现 ID。

## 15. Builder 还注册 Reminder

例如：

- LspDiagnosticsReminder；
- TaskCompletionReminder；
- SkillDiscoveryReminder。

Reminder 也有 Requirements，只在满足工具组合时激活。

## 16. `get_tools_config_raw`

Builder 可导出注册目录，包括：

- namespace/id/kind；
- default params；
- input schema；
- requires expression。

它描述候选目录，不是当前 Agent 的公告列表。

## 17. Builder 构造后集合基本固定

Finalize 会消费 Builder，产生 Session 专属 Toolset。

这避免 Agent 运行中随意改变 canonical built-in registry。

---

# 第三部分：Tool Pack 扩展“可注册全集”

## 18. 什么是 Tool Pack

```rust
pub type ToolPack = fn(&mut ToolRegistryBuilder);
```

外部 crate 可把额外 Tool 注册逻辑挂进进程级列表。

## 19. 为什么需要反向依赖

核心 `xai-grok-tools` 不应依赖每个外部 harness/plugin crate。

外部包在启动时主动调用 `register_tool_pack`，Builder 创建时再执行它们。

## 20. Tool Pack 的时序合同

必须在第一次 `ToolRegistryBuilder::new()` 前注册。

后注册不会追溯修改已经创建的 Builder。

## 21. Tool Pack 不负责自动启用

Pack 只是让 Builder “认识”额外 ID。

Agent 的 ToolServerConfig 或外部 Toolset Preset仍需选择这些 ID。

## 22. Pack 重复注册的责任

源码注释明确：idempotency 由 caller 负责。

同一个 pack 加两次会运行两次，可能造成重复注册/覆盖风险。

## 23. Pack 与动态 MCP 注册不同

```text
Tool Pack → Builder 创建期，通常是编译进程能力
MCP Tool  → FinalizedToolset 运行期，来自远端 server
```

两者生命周期和 schema 来源不同。

---

# 第四部分：Toolset Preset 扩展“候选启用集”

## 24. Toolset Preset 的类型

```rust
pub type ToolsetPresetBuilder = fn() -> ToolServerConfig;
```

它返回一组 ToolConfig，而不是向 Registry 注册实现。

## 25. 内置 Preset

例如：

- default grok-build；
- concise；
- hashline；
- codex；
- opencode；
- explore；
- plan；
- orchestrator。

## 26. Preset 是工具组合产品

Explore preset 只给 read/list/grep，安全保证来自工具集合本身，而不只是 Prompt 声称“不要修改”。

## 27. Default preset 包含 meta tools

默认 GrokBuild 工具集包含 `search_tool` 和 `use_tool`，为 MCP 提供稳定发现/执行入口。

## 28. Workspace preset 更大

`workspace_grok_build_toolset()` 加入 AgentBuilder 通常动态注入的可选工具，用于 workspace server/proxy 能执行完整集合的场景。

## 29. 外部 Preset Registry

进程可注册 public 或 internal preset。

两者都能按名解析，但 internal preset 不进入公开枚举。

## 30. 为什么区分 public/internal

Harness 内部协议组合需要可解析，却不应泄露到 manifest、产品 preset 列表或用户选择界面。

## 31. Preset 注册也有时序合同

应在首次解析相关 Agent 配置前完成。

早先已经解析的 ToolServerConfig 不会因后注册而自动变化。

## 32. Preset name normalization

`toolset_for_preset`：

- trim；
- ASCII lowercase；
- 空格和下划线替换为 `-`。

减少配置表面上的非语义差异。

## 33. Preset 不等于 AgentDefinition

Preset 只描述 ToolServerConfig。

AgentDefinition 还包含 Prompt、Skills、Permission、MCP inheritance、Model、Hooks、max turns 等。

---

# 第五部分：AgentDefinition 有三套工具限制

## 34. `tool_config`

它是具体候选 ToolConfig 列表，包含内部 ID、params、rename、version 等。

这是 Finalize 的直接原料。

## 35. `tools`

Agent 作者的 allowlist。

空列表表示继承 tool_config 中全部候选；非空时按名称、兼容 ToolKind 和 Agent directive 收窄。

## 36. `disallowed_tools`

Agent 作者的 denylist。

它比 allowlist 更早删除 matching ToolConfig，也能携带 `Agent(type)` deny directive。

## 37. Session operator clamp

`session_tools_allowlist` / `session_tools_denylist` 来自 Session 操作者，例如 CLI `--tools`。

它与 Agent 作者配置分离，最后做交集。

## 38. 为什么需要三层

```text
tool_config       → Agent 能力基础
agent allow/deny  → Agent 作者意图
session clamp     → 当前运行者的最终上限
```

后层不能被前层后续注入绕过。

## 39. deny wins

`session_tools_allowed` 先检查 denylist，再检查 optional allowlist。

即使某工具同时出现在 allow 与 deny，deny 仍生效。

## 40. Function 与 Hosted 分开过滤

Hosted tools 不在 ToolServerConfig 中，因此 `hosted_tool_allowed` 单独按名字应用：

- Agent denylist；
- Agent allowlist；
- Session clamp。

## 41. Hosted path 更严格

Hosted allowlist 不做兼容名称映射，也没有 function path 的 unresolved fail-open。

因为 hosted names 很少且明确，模糊放行会扩大服务端能力。

---

# 第六部分：`inject_default_tools` 决定配置是“基底”还是“精确合同”

## 42. 默认值为 true

Stock GrokBuild Agent 允许 AgentBuilder 根据 Session 环境添加可用工具。

## 43. true 时可能注入什么

- Memory search/get；
- WebSearch/WebFetch；
- LSP；
- ImageGen/ImageEdit；
- Video tools；
- Write fallback；
- Enter/ExitPlan；
- AskUserQuestion。

## 44. false 的含义

`tool_config` 被视为 curated exact toolset。

适合外部 harness：每个模型可见 schema 必须与训练协议严格匹配。

## 45. curated empty 是配置错误

若 `inject_default_tools=false` 且 tool list 为空，AgentBuilder 直接失败。

错误提示外部 preset provider 必须在构建 Agent 前注册。

## 46. 为什么不能把空 curated 当“无工具”

这通常表示 harness Tool Pack/Preset 初始化顺序错误，而不是作者真想无工具。

Fail-fast 比静默运行一个能力残缺 Agent 更可诊断。

## 47. strict harness 判定

AgentDefinition 将 curated toolset、定制 system prompt 或定制 user template 视为 non-interchangeable wire format。

客户端 profile 不应随意覆盖这种严格组合。

---

# 第七部分：环境能力注入与删除

## 48. Memory

只有存在 Memory backend 时才加入 memory_search/get。

若 backend 不存在，即使 preset 曾包含，也会再次删除。

## 49. 为什么既注入又清理

配置来源可能多样：内置 preset、用户文件、恢复状态、外部注册。

最终 Runtime dependency 缺失时必须以安全删除为准。

## 50. Web Search

本地 WebSearch Tool 只在配置 enabled 时加入。

若采用 backend hosted search，Turn 层还会从 function list 删除同名 `web_search`，避免双公告。

## 51. Web Fetch

只有有效配置时加入，并把解析后的 WebFetch params 合并进对应 ToolConfig。

## 52. LSP

只有 Session 提供 LSP backend 时加入。

向模型公告一个没有后端的导航工具只会制造确定性失败。

## 53. Image/Video

分别根据配置能力加入 ImageGen、ImageEdit、ImageToVideo、ReferenceToVideo。

不同能力独立 Gate，不把“图片功能开启”粗略等同于所有多媒体工具都可用。

## 54. Write fallback

若 write_file_enabled 且候选集中还没有 write kind实现，Builder 加入 OpenCodeWriteTool。

先检查存在性，避免重复功能入口。

## 55. Plan tools

默认注入模式下 `ensure_plan_mode_tools` 补齐 enter、exit、ask。

它先收集现有 ID，只添加缺项。

## 56. AskUser 独立总开关

若当前 Session 不允许 ask_user_question，后续会删除它。

因此“默认注入”不是不可撤销授权。

## 57. Workflow 与 UpdateGoal 互斥 Gate

`apply_workflow_tool_gates` 根据 background workflow 模式保留 Workflow 或 GoalUpdate 路径。

避免模型同时看到两个相互竞争的目标控制协议。

## 58. Tool params 注入

Bash、WebFetch、AskUser 等 Session-resolved params 合并进 matching ToolConfig。

这些 params 后续影响 Requirements、description 与 schema。

---

# 第八部分：Subagent 工具是一组依赖，不是一个按钮

## 59. Task Tool 的两个前提

- 全局 subagents_enabled；
- 当前 CWD/Plugin discovery 能找到至少一个 subagent definition。

任一不满足就删除 Task Tool。

## 60. Task description 是运行时构造的

主 Agent description 会列出可用 subagent personas/model；子 Agent 使用另一份更受限说明。

因此 Task Tool schema 可稳定，description 按 Session 变化。

## 61. Task 被删除后的 lifecycle pruning

如果同时不存在 background-capable Bash，Builder 删除：

- get_task_output；
- wait_tasks；
- kill_task。

## 62. 为什么必须清理 orphan helpers

没有任何工具能创建 background task 时，保留“查询/等待/取消任务”只会诱导模型调用永远无 ID 的工具。

## 63. Bash 是否能后台化取决于 params

判断不仅看 Bash Tool 是否存在，还看 `enabled_background`。

Tool selection 依赖 effective behavior，而不是名字存在即可。

## 64. Agent(type) directive

AgentDefinition.tools 中的 `Agent(researcher)` 等不是普通 Tool name，而是允许的 subagent types 声明。

Builder 从中解析、去重并写 `allowed_subagent_types`。

## 65. 没有 directive 的非空 allowlist

若 tools 非空却没有 Agent directive，`allowed_subagent_types = Some([])`，表示禁止 spawn。

这是最小权限默认。

## 66. Bare Agent deny

denylist 中裸 Agent directive 会清空 allowed subagent types。

带类型 deny 则从已有 allowed list 中删除对应类型。

## 67. spawn 被完全禁止后的二次清理

Task 与 lifecycle helpers 被移除；Bash params 关闭 background 和 timeout auto-background。

这避免通过 Bash background 间接留下需要已删除 helper 管理的任务。

---

# 第九部分：Agent allowlist 的兼容与 Fail-open

## 68. 直接 ID/name match

若 allowlist entry 能匹配当前 ToolConfig ID，它直接保留该工具。

比较函数理解 fully-qualified 与 short name。

## 69. Vendor compatibility name

`Read`、`Bash`、`Edit` 等外部名称通过共享 alias table 解析为 ToolKind。

若当前候选集中存在该 kind，则允许所有符合 kind 的相关实现。

## 70. recognized but unavailable

名称在 Registry/compat 表中有效，但当前 tool_config 没启用相应实现。

Builder 记录 debug 并忽略，不凭空新增能力。

## 71. unresolved

名称既不匹配候选、也不匹配 Registry ID、也无法映射 ToolKind。

它可能是拼写错误，也可能是来自尚未支持的外部生态。

## 72. function allowlist 的 fail-open

只要存在 unresolved entry，Builder 不应用整个 allowlist，保留完整 Grok candidate toolset并 warning。

## 73. 为什么不是只忽略未知项再收窄

外部 Agent 文件可能使用另一生态的名称。

部分解析后强行收窄，可能生成一个缺读/写/执行核心能力但表面构建成功的 Agent。

## 74. Fail-open 的安全边界

它只针对 Agent 作者 function allowlist 的兼容解析。

先前 denylist和最后 Session operator clamp仍生效；Permission/Sandbox也没有被移除。

## 75. 所有条目可解析时才 retain

保留条件包括：

- 名称直接 match；
- ToolKind 在 allow_kinds；
- Agent directive 对应的 Task dependencies；
- SearchTool/UseTool kinds。

## 76. 为什么 SearchTool/UseTool 总保留

MCP access 是稳定元能力，不应因一个只写 `Read` 的传统 allowlist 被意外剥离。

源码测试明确固定这一行为。

## 77. ToolSearch compatibility

外部 `ToolSearch` 映射到 Grok `search_tool`。

这让显式只允许工具搜索的定义仍能得到正确 meta tool。

---

# 第十部分：Capability Mode 按能力类别收窄

## 78. Capability Mode 用于什么

Subagent 可请求 ReadOnly、ReadWrite、Execute 或 All。

它是 Runtime ceiling，不只是 Prompt 建议。

## 79. mode 交集

Spawn 路径会把请求 override、角色默认和 AgentDefinition capability mode求交集。

子 Agent 不能通过请求一个更宽模式超过定义上限。

## 80. `filter_tool_config`

除 All 外，mode 取得 allowed ToolKinds，然后 retain ToolConfig。

```rust
match tc.kind {
    Some(k) => allowed.contains(&k),
    None => true,
}
```

## 81. 为什么按 ToolKind

不同 preset 可能用不同 Read/Edit/Bash 实现。

按名称硬编码会漏掉 Codex、Hashline、OpenCode 或未来 Tool Pack。

## 82. ReadOnly 并非只含 Read

它还可包含 List、Search、LSP、Plan、Memory、Web、Task、Plan mode、AskUser、Skill 等受控能力。

“ReadOnly”表示不直接修改 workspace/执行任意命令，不代表只有一个 read_file。

## 83. ReadWrite

增加 Edit、Write、Delete、Move 和多媒体生成等，但不包含任意 Execute。

## 84. Execute

允许 Execute，但具体完整集合仍以源码 `allowed_tool_kinds()` 为准。

模式名不是凭直觉推导的权限表。

## 85. `kind: None` 被保留

动态/custom ToolConfig 可能没有 kind。

过滤器选择保留，以免 capability system 破坏扩展性。

## 86. None 保留的代价

未知 kind 无法被 ReadOnly 精确分类。

扩展工具若实际有副作用，最好提供正确 ToolKind；Permission/Sandbox仍需作为后续安全边界。

## 87. 未知字符串 kind 变 Other

Serde 遇到未知 kind string 会 sink 到 `Some(Other)` 并 warning。

restrictive mode会删除 Other，而不是像真正 None 一样保留。

## 88. 为什么区分 None 与 Other

```text
None  → 来源确实没有分类信息，兼容扩展
Other → 声称提供了分类但值未知/特殊，应受限制模式约束
```

## 89. Capability filter 后再次 prune helpers

如果 Task/background Bash 被模式删除，生命周期 helper 也删除。

能力过滤必须保持工具图闭包。

---

# 第十一部分：Session Operator Clamp 是最终上限

## 90. 应用时点

AgentBuilder 完成 Agent allow/deny 和环境注入后，调用 `definition.session_tools_allowed` 最终 retain。

## 91. 为什么必须最后

若先 clamp，后续 Memory/Web/Plan 注入可能重新加入操作者禁止的工具。

最终 retain保证任何较早来源都不能越过运行者上限。

## 92. Clamp 是交集，不是替换

Session allowlist 不会恢复已被 Agent denylist、环境 Gate 或 capability mode删除的工具。

## 93. Hosted tools 同样受 clamp

Agent 构建 hosted search list 时调用 `hosted_tool_allowed`。

否则 CLI 禁止 web_search 只会删除本地 Function，却仍保留服务端 hosted search。

---

# 第十二部分：Requirements 验证工具图是否自洽

## 94. 为什么选择后还要验证

单个 Tool 可能依赖：

- 另一个特定 Tool；
- 某类 ToolKind；
- 目标工具某个 params 值；
- 自己 params 开关成立时的条件依赖；
- 某类工具可见的 input param。

## 95. `Expr<T>`

通用布尔树：

```text
Value / And / Or / Not / True / False
```

Tool、params 和 JSON value requirements复用同一个结构。

## 96. `ProposedTool`

Validator 把每个候选解析成：

- namespace/id/kind；
- effective params；
- canonical input schema。

它是“如果接受该配置，世界会是什么样”的快照。

## 97. `EvalContext`

含全体 proposed tools，以及当前正在验证 Tool 自己的 params。

后者用于 IfParams。

## 98. Require specific Tool

按 namespace + id 检查某实现是否存在，可选再检查目标 Tool params。

## 99. Require ToolKind

只要求存在任一指定能力类别，不绑定具体实现。

这让 SearchReplace 可依赖 Read kind，而不强制某个 read_file protocol。

## 100. IfParams

只有当前 Tool 自己的参数满足 condition 时，才施加某 requirement。

条件不成立时 vacuously true。

## 101. Bash background 依赖例子

若 Bash `enabled_background=true`，它要求 BackgroundTaskAction 和 KillTaskAction。

关闭 background 后，这些依赖可消失。

## 102. InputParam requirement

检查某 ToolKind 的 canonical input schema 是否含指定 property。

适合 description template 引用 `${{ params.<kind>.<param> }}` 的情况。

## 103. 为什么验证可见字段

若说明引用一个已被版本/配置隐藏的字段，TemplateRenderer 即使有工具名也无法生成真实合同。

## 104. Effective params 先合并

Requirements 用 default params + ToolConfig overrides 的结果，而非只看原始 override map。

缺省行为也是依赖图的一部分。

## 105. 验证顺序

概念上：

1. preset/version；
2. Tool ID existence；
3. params validation；
4. duplicate client names；
5. file toolset conflict；
6. requirements graph。

## 106. Mixed file toolsets 被拒绝

标准 file trio 与 Hashline trio不能混用。

不同读取锚点/编辑协议混合会让模型和 Runtime 对文件状态的引用不一致。

## 107. Requirements failure 不是自动补依赖

Finalize 返回结构化 errors，不偷偷添加缺失工具。

自动补齐只发生在明确的 Builder policy，例如 plan tools；依赖 Validator 保持纯检查。

## 108. 为什么不自动修复所有图

自动加入 Tool 会扩大 Agent 能力，可能违反 curated harness、capability ceiling 或 Session clamp。

Fail-fast 更安全。

---

# 第十三部分：Finalize 把候选集冻结成 Session Toolset

## 109. `ToolBridge::finalize_builder`

它调用 Builder.finalize，将 RequirementError 转成 ToolError，并缓存 terminal handle 供取消路径使用。

## 110. Finalize 消费 Builder

built-in registration map 不再被修改；只为 config 中选中的 entries创建 FinalizedTool。

## 111. kind-to-name 只来自启用集

TemplateRenderer 的 `${{ tools.by_kind.read }}` 指向当前 Agent 实际有的第一个 Read tool，而不是 Builder 全集。

## 112. kind params 也只来自启用集

Renderer 的参数名映射根据 enabled tools、canonical schema 和 param overrides构建。

Prompt 不会引用一个被过滤掉的工具参数。

## 113. Active reminders

Reminder requirements 针对 finalized proposed tools评估，只激活依赖满足的 reminder。

## 114. FinalizedToolset 并非完全不可变

Built-in core 已冻结，但 `tools` 使用 RwLock，允许 MCP 运行期 register/unregister。

这是受控动态扩展点。

## 115. 并发读写策略

tool lookup短暂持有 parking_lot RwLock read；动态 MCP 更新持有 write。

调用执行不会跨 await 长时间持有该锁。

---

# 第十四部分：MCP 工具注册进 Runtime，但不直接污染模型前缀

## 116. MCP handshake 得到什么

每个远端工具有：

- qualified name；
- server name；
- description；
- input schema；
- model_visible/meta；
- passthrough implementation。

## 117. `model_visible`

只有 model-visible registration 才调用 `register_mcp_tools`。

App-only tool可以供 UI/应用使用，但不会开放给模型 Runtime。

## 118. 动态注册位置

MCP Tool 直接加入当前 `FinalizedToolset.tools` 和 local registry。

它不回到已消费的 ToolRegistryBuilder。

## 119. Disable/Enable

Session toggle 可 stash registration、unregister；重新启用时复用 schema和 tool重新注册，无需完整 MCP re-init。

## 120. Model switch / Bridge rebuild

重建 Agent ToolBridge 后，Session 从已有 MCP clients重新 list/re-register，避免动态工具随着模型切换丢失。

## 121. 但 Turn 只取 built-ins-only

`prepare_tool_definitions_inner()` 调用：

```text
bridge.tool_definitions_builtins_only()
```

它过滤 client name 含 MCP delimiter 的工具。

## 122. 为什么还要把 MCP Tool 注册进 Toolset

`use_tool` 收到目标 qualified name 后，需要 FinalizedToolset lookup、reverse dispatch、output conversion 与 Tool context。

注册提供执行能力，不等于直接模型广告。

## 123. 稳定工具前缀

若每连接一个 MCP server就把几十/几百个 schemas加入请求：

- Prompt token激增；
- KV/prompt cache前缀变化；
- 模型选择噪声增加；
- server progressive连接会使每轮工具列表抖动。

## 124. Search/Use 两级接口

```text
固定 schema: search_tool(query, limit)
  → 返回少量匹配 tool names + descriptions + input schemas

固定 schema: use_tool(tool_name, tool_input)
  → dispatch 到已注册动态 MCP Tool
```

## 125. Schema 按需进入对话

MCP schema不是顶层 tools数组的永久前缀，而是 search_tool 的 Tool Result内容。

模型只为当前任务承担相关 schemas 的 context cost。

---

# 第十五部分：BM25 ToolIndex 是动态发现的数据面

## 126. ToolIndex resource

Session spawn 创建 `Bm25ToolSearchIndex`，包装共享 `tool_metadata_snapshot`，注入 ToolBridge Resources。

SearchTool 通过 typed resource读取它。

## 127. 为什么用 Resource 注入

SearchTool 实现不依赖 Shell Session具体类型。

测试可注入 StaticToolIndex，主 Session 注入 BM25 implementation。

## 128. ToolIndex 内容

搜索结果包含：

- qualified tool name；
- server name；
- description；
- input schema；
- BM25 score。

## 129. Search result grouping

SearchTool 按 server分组，同时保持 server内 BM25 score顺序；server groups按各自最高 score排序。

## 130. 返回 status

```text
ready   → 所有预期 server状态稳定
partial → 仍有 server连接中
```

## 131. `total_hidden_tools`

告诉模型当前动态目录规模，即使只返回 top-k。

模型能判断是否需要换 query继续搜索。

## 132. Ready but empty 的指导

返回 note区分：

- 没有 MCP 工具/继承关闭；
- 只是当前 query没匹配。

## 133. SearchTool 没有 ToolIndex

它不 hard error，而返回空结果和“未配置 integration tools”说明。

稳定 meta tool即使无 MCP也可存在。

---

# 第十六部分：Blocking 与 Progressive MCP 初始化

## 134. Blocking

`prepare_tool_definitions_timed` 在首次准备时等待 `mcp_state.is_initialized()`。

虽然 direct MCP schemas不进入顶层列表，但 ToolIndex、server reminder和可执行 registrations可以在模型首次决策前稳定。

## 135. Progressive

不等待 MCP 完成，立即准备 built-in tool definitions。

模型可以先做本地工作，稍后 search_tool看到 partial/updated catalog。

## 136. 为什么 Progressive 不更新顶层 MCP list

顶层本来就只有稳定 built-ins。

动态变化发生在 ToolIndex和 search result，不破坏每轮 Function Tool prefix。

## 137. 调用尚未 ready 的 MCP Tool

上一篇说明：Progressive下若 MCP 尚未初始化，直接 qualified call会得到可行动错误，提示使用 search_tool。

## 138. Blocking wait 计时

Session记录 `mcp_wait_ms` 与 total tool prep time，区分 MCP连接延迟和普通 definition preparation成本。

---

# 第十七部分：每个 Turn 如何取得真正 Tool Snapshot

## 139. Turn 开始时准备一次

`process_conversation_turn` 在 Agentic loop外调用：

```text
prepare_tool_definitions_timed()
```

所得 `tool_definitions` 在这个用户 Turn的多次 Sampling循环中复用。

## 140. 为什么不是每个 Tool loop重新读取

同一个用户 Turn内保持 Function Tool schema稳定，有利于：

- retry/Tool Result后的协议一致；
- Prompt cache；
- 避免中途 toggle改变模型可行动作集合；
- 可复现 trace。

## 141. `prepare_tool_definitions_inner`

当前实现：

1. clone ToolBridge；
2. 取 builtins-only definitions；
3. 读取 Plan Mode active；
4. 应用 plan-mode filter。

## 142. 当前 plan filter 是 pass-through

`filter_cursor_tools_by_plan_mode` 在此 build中返回原 Vec。

Plan Mode 的编辑限制由 Tool execution gate执行，而不是删 schema。

## 143. 为什么保留 filter seam

其他 integration/build variant可能在 Schema 层隐藏 Plan 工具；统一 call site使差异可局部实现。

## 144. `turn_base_tool_specs`

它是 Turn与 Snapshot command共享的单一转换：

- backend search active时删除本地 `web_search` function；
- ToolDefinition → ToolSpec。

## 145. StructuredOutput 后追加

合成 StructuredOutput Tool是当前 Turn特定能力，在 base snapshot之后按请求 schema追加。

因此 SnapshotToolDefinitions明确不包含它。

## 146. Forked tool override

若 `forked_tool_override` 存在，Turn直接克隆这份 ToolSpec，而不使用本地 base definitions。

这用于 verbatim/fork场景保持父上下文工具合同。

## 147. Dynamic MCP 仍不改变 Turn base

MCP server在 Turn中途 ready，不会把其 definitions插入当前 effective_tools。

search_tool的数据结果可以更新，但固定 Function入口不变。

---

# 第十八部分：SnapshotToolDefinitions 防止 Fork 漂移

## 148. Session command

`SnapshotToolDefinitions` 返回当前 Session会发送的 base ToolSpecs。

## 149. 复用同一 helper

Handler 调用：

```text
prepare_tool_definitions_inner
→ turn_base_tool_specs
```

与真实 Turn完全相同。

## 150. 为什么不能直接调用 Agent.tool_definitions

那会漏掉：

- builtins-only规则；
- Plan filter；
- backend search local-tool drop；
- ToolSpec mapping策略。

Fork child会得到父模型从未真正看到的工具列表。

## 151. Snapshot 不含 StructuredOutput

它依赖某次 prompt附带的 json_schema，不是 Session长期 base能力。

调用者应在具体 Turn层追加。

## 152. Snapshot 是协议事实，不是诊断列表

它服务 fork/inheritance，需要与模型 wire前缀一致；用于 UI展示的“所有可执行 MCP tools”是另一种查询。

---

# 第十九部分：Hosted Search 与 Function Search

## 153. 两种执行位置

```text
Function web_search → 本地 Tool Runtime执行
Hosted web_search   → Provider agentic sampler执行
```

## 154. backend search active 的双 Gate

需要同时满足：

- Agent build时 backend_search_enabled；
- 当前 model supports_backend_search。

## 155. Hosted list 的构建

AgentBuilder根据 Web config、Agent allow/deny与 session clamp创建 HostedTool list。

Turn再应用 per-turn overrides。

## 156. Function collision drop

backend search active时，`turn_base_tool_specs` 删除本地 `web_search`。

Responses converter还有 hosted/function同名冲突的最后防线。

## 157. 为什么两层都防

Session层保持模型合同清晰；Provider adapter层防止其他调用者构造冲突请求。

跨层安全检查允许独立复用组件。

## 158. Hosted Tool 不进入 built-in ToolDefinition token统计

Session context统计 Tool definitions时单独考虑 backend search active的 function drop；Hosted native结构在请求另一字段中。

读 context usage时要知道两类 tool cost不完全同一口径。

---

# 第二十部分：Tool 列表与 Prompt Cache

## 159. Tool Schema 是请求前缀的大头之一

几十个长 description/schema会占用大量输入 token。

列表变化还会让 Provider无法复用稳定前缀。

## 160. 为什么每 Turn复用 snapshot

一个用户 Turn可能 Sampling多次。保持 tools Vec不变，让 Assistant ToolCall/ToolResult循环只在消息尾部增长。

## 161. 为什么 MCP 用 search/use

MCP目录可能频繁变化且数量大。将动态内容放到按需 Tool Result尾部，稳定 system + built-in tools前缀。

## 162. 为什么 Tool order 也重要

FinalizedToolset按 config.tools顺序生成 definitions。

即使语义集合相同，无意义重排也可能改变序列化前缀与模型偏好。

## 163. Preset 应保持确定顺序

Preset builders使用 Vec明确排列；不要从无序 HashMap直接生成 model-facing tool list。

## 164. 动态注册不应插入 base列表

否则 MCP连接完成时机将决定下一轮 schema顺序和 cache key，使相同用户任务难以复现。

---

# 第二十一部分：五个 Walkthrough

## 165. Walkthrough A：普通 GrokBuild 主 Agent

1. Builder注册所有内置实现；
2. default preset选择标准 file/bash/task/search/use等；
3. Session有 Web/LSP/Image能力时注入相应工具；
4. Agent allow/deny收窄；
5. session clamp最后收窄；
6. Requirements验证依赖；
7. Finalize生成 ToolDefinitions；
8. Turn取 builtins-only；
9. backend search active时删本地 web_search；
10. 转成 ToolSpecs发给模型。

## 166. Walkthrough B：ReadOnly Explore Subagent

1. Explore preset本身只有 read/list/grep；
2. Capability mode与角色默认求交集；
3. filter按 ToolKind移除写/执行能力；
4. orphan background helpers被清理；
5. Requirements通过；
6. Prompt与 Tool Schema都不提供修改入口。

## 167. Walkthrough C：传统 allowlist `tools: [Read]`

1. `Read`不直接匹配 fully-qualified ID；
2. compatibility table解析为 Read kind；
3. 当前候选中的 Read implementations保留；
4. SearchTool/UseTool也保留；
5. 其他 kinds删除；
6. 若没有 Agent directive，subagent types被清空，task依赖删除。

## 168. Walkthrough D：Progressive MCP

1. Turn不等待 MCP；
2. 模型立即获得稳定 search_tool/use_tool；
3. 首次 search可能 status=partial；
4. server handshake完成，工具动态注册并更新 ToolIndex；
5. 后续 search返回 qualified name/schema；
6. 模型调用 use_tool；
7. FinalizedToolset按 qualified name dispatch；
8. 顶层 Function Tool prefix始终未增加几十个 MCP schemas。

## 169. Walkthrough E：Verbatim Fork

1. 父 Session通过 SnapshotToolDefinitions取得真实 base ToolSpecs；
2. snapshot已经执行 builtins-only与 backend search drop；
3. child保存为 forked_tool_override；
4. child Turn优先使用这份 specs；
5. 即使 child本地动态 MCP状态稍有不同，模型前缀仍与父 fork点一致。

---

# 第二十二部分：最容易误解的地方

## 170. 误解：Builder 注册的工具都会发给模型

错误。Builder只是候选目录，ToolServerConfig选择子集。

## 171. 误解：AgentDefinition.tools 是 ToolConfig

错误。它是额外 allowlist/directive；具体 schema/version/params在 tool_config。

## 172. 误解：inject_default_tools=false 表示禁用默认名字

错误。它表示不做 Session环境工具注入，tool_config被视为精确 curated set。

## 173. 误解：ReadOnly 只看 Prompt

错误。Capability filter真实删除不允许的 ToolKinds。

## 174. 误解：未知 kind一定删除

错误。`None`为扩展性保留；未知字符串 sink成 Other，在 restrictive mode中删除。

## 175. 误解：allowlist有拼写错误就得到空工具集

错误。function path对 unresolved entry fail-open并告警。

## 176. 误解：Requirements会自动添加依赖

错误。它验证并失败，不扩大能力。

## 177. 误解：MCP Tool注册后会直接进入下一轮 tools数组

错误。Turn明确取 builtins-only；MCP通过 search/use暴露。

## 178. 误解：Blocking MCP是为了等待 direct schemas

错误。direct MCP schemas仍不进入 base tools；等待让 ToolIndex、registrations与提醒在首轮稳定。

## 179. 误解：每次模型 Sampling都会重新选择全部工具

错误。同一用户 Turn在进入 loop前准备一次 definitions并复用。

## 180. 误解：Session mode切换一定重建 Tool Registry

当前 `update_policies_from_definition` 注释明确：mid-session policy/tool registry rebuild尚未完整支持；mode可重渲染 Prompt，不能假设工具集合同步重建。

---

# 第二十三部分：如何验证选择逻辑

## 181. Builder allowlist 测试

重点运行 `xai-grok-agent` builder tests，验证：

- Read/Bash/Edit compatibility映射；
- unresolved fail-open；
- recognized unavailable不扩权；
- SearchTool/UseTool保留；
- ToolSearch映射；
- Agent directives。

## 182. Capability tests

`xai-grok-tools` Task types tests覆盖：

- ReadOnly；
- ReadWrite；
- Execute；
- All；
- orphan background helper pruning；
- unclassified tool保留。

## 183. Requirement tests

应覆盖：

- missing specific Tool；
- missing ToolKind；
- IfParams true/false；
- target params mismatch；
- InputParam missing；
- mixed file toolset；
- effective params merge。

## 184. MCP Search tests

SearchTool已有 StaticToolIndex tests，可验证：

- server grouping；
- ready empty note；
- partial status；
- top-k schema输出。

## 185. Snapshot parity 测试

构造 backend search active/false、Plan active/false等组合，断言：

```text
SnapshotToolDefinitions == Turn base ToolSpecs
```

不应分别维护 expected逻辑。

## 186. Turn stability 测试

同一 Turn第一次 Sampling后动态注册 MCP Tool，再检查第二次 Sampling的 base Function tools未变化；search_tool结果可以变化。

## 187. Clamp precedence 测试

让 Session deny一个会被环境后置注入的 Tool，验证最终仍不存在；Hosted同名能力也必须不存在。

## 188. Cache stability 测试

序列化连续两次相同 base tool snapshots，应 byte-stable；改变 MCP catalog不应改变 base bytes。

---

# 第二十四部分：维护与 Review 清单

## 189. 新增 Built-in Tool

检查：

- Builder是否注册；
- 哪些 presets选择；
- ToolKind是否准确；
- Session环境 dependency；
- Requirements；
- allowlist兼容名称；
- Prompt template是否引用；
- tests是否覆盖公告/不公告。

## 190. 新增 Tool Pack

检查启动顺序、重复注册策略、对应 Toolset Preset，以及 curated empty时的错误诊断。

## 191. 修改 Preset

检查工具顺序、依赖闭包、Prompt description引用、Capability filters和 external manifest公开性。

## 192. 修改 Capability Mode

不要只新增 ToolKind到 allowed表。还要判断：

- 是否有孤儿 helper；
- 是否允许间接副作用；
- kind=None扩展的安全含义；
- Permission/Sandbox是否提供后续边界。

## 193. 修改 allowlist解析

明确 direct match、compat kind、recognized unavailable和 unresolved四种结果，避免把兼容失败悄悄变成扩权或残缺工具集。

## 194. 修改 MCP可见性

区分：

- model_visible registration；
- Runtime executable；
- ToolIndex searchable；
- direct Function advertised。

不要用单个 bool混合四层状态。

## 195. 修改 Turn tool helper

同步检查 SnapshotToolDefinitions、fork、compaction、recap、context token统计与 trace upload。

最好让所有消费者调用同一 helper，而不是复制过滤条件。

## 196. 修改 backend hosted tools

同时检查 AgentBuilder hosted filter、turn_base function drop和Provider adapter collision guard。

---

## 197. 一页纸总结

```text
1. Builder 注册的是 binary 候选全集，不是模型工具列表。
2. Tool Pack 扩展注册全集；Toolset Preset选择候选启用集。
3. AgentDefinition.tool_config是具体配置，tools/disallowed_tools是作者过滤。
4. inject_default_tools控制Session是否按环境追加可选工具。
5. Memory/Web/LSP/Image/Video/Plan/Task等都有独立 Gate和清理路径。
6. Agent allowlist支持direct ID与compat ToolKind；unresolved时function path fail-open。
7. SearchTool/UseTool作为稳定MCP元能力不会被普通allowlist意外删除。
8. Capability Mode按ToolKind求交集，并清理孤儿background helpers。
9. Session operator clamp最后应用，deny优先，不能被后续注入绕过。
10. Requirements验证工具、kind、params和visible input fields组成的依赖图。
11. Finalize只实例化选中的工具，并基于启用集渲染Prompt/Schema。
12. MCP Tool运行期注册供use_tool dispatch，但Turn只公告built-ins-only。
13. BM25 ToolIndex让search_tool按需返回少量MCP schemas。
14. Blocking等待目录稳定；Progressive允许partial发现；两者都保持顶层工具前缀稳定。
15. 一个用户Turn只准备一次ToolDefinitions，多次Sampling复用。
16. SnapshotToolDefinitions与真实Turn共用helper，保证Fork合同不漂移。
17. Hosted search启用时删除同名本地Function，并由Provider adapter再次防冲突。
```

---

## 198. 本篇术语表

| 名词 | 白话解释 | 在本篇中的精确含义 |
| --- | --- | --- |
| Tool selection | 工具选择 | 从进程候选全集逐层收窄到本轮模型公告集合的过程 |
| candidate universe | 候选全集 | ToolRegistryBuilder认识的所有可注册 built-in/pack tools |
| registered tool | 已注册工具 | Builder或FinalizedToolset有schema、parser和dispatch entry的工具 |
| configured tool | 已配置工具 | 出现在Agent ToolServerConfig候选列表的工具 |
| finalized tool | 已定型工具 | 通过params、version、requirements并进入Session Toolset的工具 |
| advertised tool | 已公告工具 | 当前ConversationRequest.tools实际发给模型的Function Tool |
| direct tool | 直接工具 | 以自己的name/schema出现在模型顶层tools数组的工具 |
| meta tool | 元工具 | search_tool/use_tool这类搜索或转发到真实目标的稳定入口 |
| ToolRegistryBuilder | 工具Registry构建器 | 注册binary可用全集并验证/Finalize配置的对象 |
| ToolEntry | 候选条目 | Builder中保存canonical schema、metadata和typed closures的记录 |
| FinalizedToolset | Session工具集 | 当前Agent可lookup、dispatch和动态注册MCP工具的运行时Registry |
| Tool Pack | 工具包 | 外部crate在Builder创建期追加注册逻辑的函数 |
| Toolset Preset | 工具集预设 | 返回ToolServerConfig的命名工具组合builder |
| public preset | 公开预设 | 可解析且进入产品枚举/manifest的外部preset |
| internal preset | 内部预设 | 可按名解析但不进入公开枚举的harness preset |
| ToolServerConfig | 工具服务器配置 | selected ToolConfigs与behavior preset的集合 |
| ToolConfig | 单工具配置 | fully-qualified ID、params、rename、version和kind |
| fully-qualified ID | 全限定ID | namespace与工具id组合，如`GrokBuild:read_file` |
| client-facing name | 模型侧名称 | Finalize后模型Function Call使用的工具名 |
| ToolKind | 工具能力类别 | Read、Edit、Execute、Search、Task等语义分类 |
| ToolNamespace | 工具命名空间 | GrokBuild、Codex、OpenCode、Hashline、MCP等实现来源 |
| AgentDefinition | Agent定义 | Prompt、工具、skills、model、permission、MCP等可版本化配置 |
| curated toolset | 精确策划工具集 | inject_default_tools=false时不允许环境自动扩充的工具集合 |
| inject_default_tools | 默认工具注入开关 | 是否由AgentBuilder按Session能力添加可选工具 |
| allowlist | 允许列表 | 非空时只保留能被名字/kind/directive解析的工具 |
| denylist | 禁止列表 | 删除matching tools，通常优先于allowlist |
| session clamp | Session钳制 | 当前运行者施加的最终allow/deny能力上限 |
| fail-open | 失败放行 | function allowlist有unresolved项时保留完整候选集并告警 |
| fail-closed | 失败关闭 | 解析不明时拒绝或不提供能力；hosted path更接近此策略 |
| recognized unavailable | 已知但不可用 | 名称有效，但当前候选集没有对应实现 |
| unresolved entry | 无法解析条目 | 不匹配ID、Registry或compat ToolKind的allowlist字符串 |
| compatibility mapping | 兼容映射 | Read/Bash/Edit等外部名称到Grok ToolKind的对应 |
| Agent directive | 子Agent指令 | `Agent(type)`形式、同时存在于tools字段的spawn限制声明 |
| Capability Mode | 能力模式 | ReadOnly、ReadWrite、Execute、All等Subagent Runtime ceiling |
| capability intersection | 能力交集 | 请求override、角色默认和定义上限取最窄结果 |
| unclassified tool | 未分类工具 | ToolConfig.kind=None的动态/custom工具 |
| Other kind | 其他类别 | 未知kind字符串sink后的显式ToolKind::Other |
| orphan helper | 孤儿辅助工具 | 没有任务创建者时仍存在的wait/get/kill生命周期工具 |
| Requirement | 依赖要求 | Tool进入FinalizedToolset前必须满足的工具图约束 |
| Expr | 布尔表达式树 | And/Or/Not/Value/True/False组合的通用Requirement结构 |
| ProposedTool | 拟议工具 | Validator眼中的候选工具、params、kind和schema快照 |
| EvalContext | 验证上下文 | 全体拟议工具和当前工具自身effective params |
| IfParams | 条件依赖 | 仅在当前工具参数满足条件时生效的Requirement |
| InputParam requirement | 可见参数依赖 | 要求某种ToolKind的schema含指定property |
| vacuously true | 空真 | IfParams条件不成立时无需检查内部requirement |
| dependency closure | 依赖闭包 | 工具集合包含所有必要配套能力且无孤儿协议入口 |
| Finalize | 定型 | 验证选择、构建Renderer/Resources并生成FinalizedToolset |
| Reminder | 提醒器 | 工具结果后可能注入模型提示、且自身可有Requirements的组件 |
| model_visible | 模型可见标志 | MCP registration是否允许进入模型可执行Runtime目录 |
| app-only tool | 仅应用工具 | 供应用/UI使用但不向模型开放的MCP工具 |
| dynamic registration | 动态注册 | Session运行期间向FinalizedToolset加入/删除MCP Tool |
| built-ins-only | 仅内置工具 | Turn快照过滤掉MCP-qualified dynamic definitions的规则 |
| MCP delimiter | MCP分隔符 | qualified name中区分server/tool的标志；也用于built-in过滤 |
| ToolIndex | 工具索引资源 | search_tool读取的动态MCP metadata搜索接口 |
| BM25 | 文本相关性算法 | 根据query与工具名/说明计算动态工具搜索排名的方法 |
| SearchSnapshot | 搜索快照 | top results、ready状态、隐藏工具数量等一次原子搜索结果 |
| partial catalog | 部分目录 | 某些MCP servers仍连接中时可搜索的不完整工具集合 |
| Blocking initialization | 阻塞初始化 | 首轮工具准备前等待MCP目录完成初始化 |
| Progressive initialization | 渐进初始化 | 不阻塞首轮，search_tool可返回partial并随后更新 |
| stable tool prefix | 稳定工具前缀 | 多轮请求中保持顺序与内容稳定的built-in Function schemas |
| prompt cache | 提示缓存 | Provider复用相同请求前缀计算的能力 |
| Turn snapshot | Turn工具快照 | 一个用户Turn开始时取得、在内部多次Sampling复用的definitions |
| `turn_base_tool_specs` | Turn基础工具转换 | built-in defs应用backend-search drop并转成ToolSpec的共享helper |
| forked_tool_override | Fork工具覆盖 | Child为保持父协议前缀而直接复用的ToolSpec列表 |
| SnapshotToolDefinitions | 工具快照命令 | 返回与真实Turn base一致的ToolSpecs供Fork等消费者使用 |
| Hosted Tool | 托管工具 | 由Provider agentic sampler服务端执行的原生工具 |
| Function Tool | 函数工具 | 由本地Tool Runtime执行的模型Function Call入口 |
| backend search active | 后端搜索激活 | Agent允许且当前模型支持Hosted Search的联合Gate |
| collision drop | 冲突删除 | Hosted同名能力启用时移除本地Function定义 |
| tool definition tokens | 工具定义Token | name、description和schema占用的模型输入成本 |

---

## 199. 下一篇建议

下一篇继续深入动态工具发现的核心数据结构：

> **源码精读 33：MCP Tool Discovery Index——Server Handshake、Metadata Snapshot、BM25 建索引、查询打分、Search Result Schema、UseTool Dispatch 与目录更新一致性**

它会把本篇的“为什么不直接公告所有 MCP Tool”继续追到实现细节：索引何时更新、一次 search如何得到稳定快照，以及 search结果中的schema怎样最终驱动`use_tool`。
