# Walkthrough：Slash Command 如何从注册、补全走到执行与透传

> 场景：用户输入 `/his`，补全菜单应找到 `/history`；输入 `/model ` 后应进入参数补全；完整输入一个当前 Mode 不支持的命令时，应给出明确拒绝，而不是把文本泄漏给模型；ACP 可以在运行时公布新命令和 Skill，但不能覆盖 Pager Builtin；Tier 受限命令需要可发现却不可执行；Command Palette 发起命令时还必须保住 Composer 中已有的 Draft。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
Pager startup
  -> construct builtin SlashCommand objects
  -> CommandRegistry::new
  -> build canonical-name / alias index and completion triggers

ACP session update
  -> AvailableCommandsUpdate { commands, tools }
  -> set_acp_state
  -> replace ACP entries only; preserve builtins
  -> resolve collisions / qualify selected skill names
  -> rebuild lookup index and triggers once

User types "/..."
  -> SlashController snapshots prompt and AppCtx
  -> command_offered applies surface / session / mode / tool gates
  -> Matcher fuzzy-ranks command triggers, MRU influences ordering
  -> optional argument phase calls suggest_args
  -> dropdown / ghost suffix / palette render

User submits
  -> parse_invocation
  -> restricted-command interception
  -> registry.get_for_dispatch
  -> central ModeSupport refusal
  -> SlashCommand::run(CommandExecCtx, args)
  -> CommandResult
  -> Action / queue / message / skill injection / pass-through
  -> consume typed input, or preserve an unrelated palette draft
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| 命令接口与结果 | `crates/codegen/xai-grok-pager/src/slash/command.rs` | `SlashCommand`、`CommandResult` |
| 注册表与动态更新 | `crates/codegen/xai-grok-pager/src/slash/registry.rs` | `CommandRegistry` |
| 补全控制器 | `crates/codegen/xai-grok-pager/src/slash/mod.rs` | `SlashController`、`command_offered` |
| 模糊匹配 | `crates/codegen/xai-grok-pager/src/slash/matcher.rs` | `Matcher` |
| ACP 命令 | `crates/codegen/xai-grok-pager/src/slash/acp_command.rs` | `AcpSlashCommand` |
| Mode 契约 | `crates/codegen/xai-grok-pager/src/slash/mode_support.rs` | `ModeSupport` |
| MRU 排序 | `crates/codegen/xai-grok-pager/src/slash/mru.rs` | 最近使用记录与持久化 |
| Builtin 实现 | `crates/codegen/xai-grok-pager/src/slash/commands/` | `/history`、`/compact` 等 |
| 提交与结果映射 | `crates/codegen/xai-grok-pager/src/app/dispatch/prompt.rs` | `dispatch_send_prompt_inner` |
| Action 定义 | `crates/codegen/xai-grok-pager/src/app/actions.rs` | `SendSlashCommandPreservingDraft` |

## 3. Slash Command 不是一个单层 HashMap

阅读时可以把系统分成四层：命令声明、Registry、交互 Controller、提交 Dispatcher。每层回答的问题不同。

## 4. 命令声明回答“我是什么”

`SlashCommand` 提供名称、别名、描述、Usage、参数契约、可见性、Mode 支持、所需 Tool 与同步 `run()`。

## 5. Registry 回答“当前 Catalog 里有什么”

它合并 Builtin 与 ACP 动态条目，维护字符串到命令对象的索引，并区分硬隐藏、菜单隐藏和 Tier 限制。

## 6. Controller 回答“此刻给用户看什么”

它结合 Prompt、Session Surface、Screen Mode、运行时 Context、Fuzzy Match 与 MRU 形成 Dropdown Snapshot。

## 7. Dispatcher 回答“提交后做什么”

它解析完整输入、阻止受限命令泄漏、执行命令，并把 `CommandResult` 映射成 Action、队列项或 Scrollback Message。

## 8. Glossary（一）：核心角色

- **Slash command**：以 `/` 开头、由客户端或 Shell 赋予特殊语义的输入。
- **Builtin**：编译进 Pager 的内建命令实现。
- **ACP command**：Agent Client Protocol 在运行时公布的命令。
- **Registry**：保存命令对象及其名称索引的注册表。
- **Controller**：根据当前输入和 UI Context 生成补全状态的控制器。
- **Dispatcher**：把已提交意图映射成应用状态变化的分发层。
- **Catalog**：某一时刻系统知道的全部命令集合。

## 9. `SlashCommand` 为什么是 Trait

不同命令共享发现、补全和执行协议，却拥有不同 State Gate 与 Result。Trait 让 Builtin 与运行时 ACP Wrapper 都能存入 `Arc<dyn SlashCommand>`。

## 10. 名称不带 `/`

`name()` 返回 `history` 而非 `/history`。斜杠属于输入语法，不属于 Registry Key。

## 11. Alias 与 Canonical Name 分离

`aliases()` 可以让多个 Token 指向同一个实现。Canonical Name 用于稳定标识，Alias 只提供入口。

## 12. 为什么返回 `&str` 而非 `&'static str`

Builtin 名称通常是静态字符串，但 ACP 命令来自运行时数据。较宽松的借用签名让两者共享一个 Trait。

## 13. Description 与 Usage 用途不同

Description 适合菜单中快速解释；Usage 表达完整调用格式，例如是否有 `<required>` 或 `[optional]` 参数。

## 14. 参数是两个 Bit，而不是一个 Boolean

`takes_args()` 表示是否接受参数，`args_required()` 表示是否必须有参数。二者组合才能区分无参数、可选参数和必填参数。

## 15. 三种参数契约

| `takes_args` | `args_required` | 示例 | 空参数 Enter |
| --- | --- | --- | --- |
| false | false | `/exit` | 立即执行 |
| true | false | `/compact [context]` | 可执行 |
| true | true | `/model <id>` | 尚未完整 |

## 16. `takes_args_now` 是运行时补全契约

有些命令是否提供子命令取决于鉴权或 Surface。它影响尾随空格、参数阶段和建议，不重写静态 Enter 完整性。

## 17. 为什么完整性仍依赖静态契约

Enter 行为需要稳定、可预测；如果 Runtime Context 暂时未同步，动态参数能力不应让同一文本在相邻 Tick 间突然从可提交变成不可提交。

## 18. `suggest_args` 返回 `ArgItem`

每个参数候选分别携带 Display、Match Text、Insert Text 与 Description，因此“显示什么”“匹配什么”“写回什么”可以不同。

## 19. 参数校验最终仍在 `run()`

接口没有独立 `validate_args()`；补全只是帮助输入，真正执行必须再次解释和拒绝非法参数。

## 20. Glossary（二）：参数模型

- **Canonical name**：命令唯一的规范名称。
- **Alias**：映射到同一命令的替代名称。
- **Usage**：面向用户的调用格式说明。
- **Args**：命令 Token 后的原始参数字符串。
- **Argument completion**：命令已确定后，对参数候选继续补全。
- **Static contract**：不依赖运行态、用于稳定判断的接口承诺。
- **Runtime contract**：随鉴权、Surface 或 Session 状态变化的能力。

## 21. `AppCtx` 是只读建议 Context

它被 `visible()` 与 `suggest_args()` 使用，包含 Model State、CWD、Announcement、Billing、Workflow 与 Screen Mode 等只读信息。

## 22. 为什么建议阶段不拿整个 App

缩小 Context 让命令不能在用户只是打字时意外修改应用，也使补全函数更容易测试。

## 23. `CommandExecCtx` 是执行快照

它包含 Session ID、Bundle State、Screen Mode、Billing 与 Pager Local Settings Snapshot，供 `run()` 计算明确结果。

## 24. 名称中的 Mutable 不代表任意修改 App

`run(&mut CommandExecCtx, ...)` 可以使用执行上下文，但主要状态改变仍经类型化 `Action` 回到 Dispatcher。

## 25. `run()` 故意是同步函数

Pager 的 Dispatch Model 是同步 State Transition。命令若需要异步 ACP 或 I/O，不在 Trait 内 Await，而返回 `CommandResult::Action`。

## 26. Action 是异步边界的“票据”

命令负责决定“应该 Switch Model”或“应该打开 External Editor”，Dispatcher/Effect 层负责实际启动对应流程。

## 27. 这避免命令持有长生命周期 App 借用

如果 `run()` 跨 Await 持有 UI State 借用，会让 Rust Lifetime、并发取消和状态新鲜度都更复杂。

## 28. `PagerLocalSnapshot` 避免双重真相

设置类命令读取 Snapshot 计算新值，再返回类型化 Action；实际 Mutation 仍集中在 Dispatcher，而不是命令对象直接改缓存。

## 29. Glossary（三）：Context 与同步边界

- **Context**：一次计算所需的最小环境数据集合。
- **Snapshot**：某时刻状态的只读副本。
- **Synchronous dispatch**：一次分发不跨异步等待，立即产生下一步结果。
- **Action**：描述应用应执行何种状态变化的类型化值。
- **Effect**：状态机之外执行 I/O 或异步工作的步骤。
- **Source of truth**：某项状态被认为权威的唯一位置。
- **Lifetime**：Rust 中引用保持有效的范围。

## 30. Registry 同时保存对象、来源和索引

`commands` 保存 Trait Object，`sources` 标记 Builtin/ACP，`key_to_index` 让 Canonical Name 与 Alias 快速定位，`triggers` 服务补全。

## 31. Builtin 创建时先进入 Registry

`CommandRegistry::new(builtins)` 为每个初始条目标记 `CommandSource::Builtin`，随后构建索引与 Trigger。

## 32. Builtin 冲突属于编程错误

两个 Builtin 共用名称或 Alias 会破坏确定性，因此 Registry 构造阶段采用 Fail-fast 语义，而不是静默挑一个。

## 33. ACP Catalog 是可替换集合

`set_acp_commands()` 先移除旧 ACP 来源条目，再加入新列表；Builtin 始终保留。

## 34. 为什么不是逐条 Patch

ACP Update 表达“当前完整可用列表”。Replace 语义能自然删除服务端已撤回的命令，也不需要维护复杂 Diff。

## 35. `set_acp_state` 同时更新 Commands 与 Tools

它把 Catalog 与 Agent Advertised Toolset 作为同一代状态更新，并只在末尾重建一次 Trigger。

## 36. 一次重建的重要性

若先更新 Command 重建、再更新 Tool 重建，UI 可能短暂看到不满足 Tool Requirement 的候选，还会多做一次全量工作。

## 37. ACP 不能覆盖 Builtin 语义

名称碰撞时 Registry 保住 Pager 自己理解的命令，避免同样 `/history` 因 Agent 不同而改变本地行为。

## 38. 部分 Skill 冲突会被 Qualify

来自 Client/Plugin 的 Skill 必要时加来源限定名，既保留 Builtin，也让动态 Skill 仍有可调用入口。

## 39. Alias 也参与碰撞判断

只检查 Canonical Name 不够；ACP 名称撞上 Builtin Alias 同样会让 Lookup 含糊。

## 40. 重建生成一份一致的读模型

`rebuild_triggers()` 重新建立 Key Index 和补全 Trigger，使对象集合、Lookup 与 Menu 数据不发生代际错位。

## 41. Glossary（四）：注册与更新

- **Trait object**：通过统一 Trait 接口动态调用不同具体类型的对象。
- **Provenance**：条目来源，例如 Builtin 或 ACP。
- **Dynamic catalog**：运行时可整体变化的命令集合。
- **Replace semantics**：新列表被视为完整真相，旧动态列表整体替换。
- **Collision**：两个命令竞争同一名称或 Alias。
- **Qualified name**：附加来源前缀或限定信息以消除冲突的名称。
- **Trigger**：供补全匹配使用的可搜索入口。

## 42. Hidden 不是一个概念

Registry 分别维护 `hidden`、`menu_hidden`、`restricted`，另有 Tool Requirement。它们的执行语义不同。

## 43. `hidden` 是硬隐藏

Feature Gate 未开启的 `/voice`、`/dashboard`、`/auto` 等既不显示，也不能通过完整手输绕过。

## 44. 硬隐藏采用 Fail-closed 初值

Registry 创建时先隐藏需运行时确认的功能，只有 Gate 明确成功后才 Reveal。

## 45. 为什么不能默认可用再异步关闭

启动窗口中用户可能立即执行尚未确认的能力。Fail-closed 避免 Remote Kill Switch 或权限信息晚到时产生短暂越权。

## 46. `menu_hidden` 只影响提供

例如 `/share` 可以不出现在菜单里，但完整手输仍应到达本地 Handler，显示 Client Disable Message。

## 47. `get` 服务补全语义

它应用菜单隐藏在内的所有 Gate，适合回答“是否应作为候选出现”。

## 48. `get_for_dispatch` 服务手输语义

它忽略 `menu_hidden`，但仍尊重硬隐藏、Tier 限制和 Tool Requirement，适合完整 Invocation。

## 49. 为什么两个 Lookup 都需要

若只有宽松 Lookup，菜单会泄露不应推广的功能；若只有严格 Lookup，完整手输会被误判 Unknown 并透传给 Shell/Model。

## 50. `restricted` 是商业权限 Gate

Tier 受限命令需要在产品中可发现，但不能执行。它与 Feature 根本不存在不同。

## 51. 注释与代码要一起读

Registry 注释强调 Restricted 的 Discoverability；具体 Menu 构建会保留相应展示信息，而执行 Lookup 仍拒绝，Dispatcher 再触发 Upsell。

## 52. `is_restricted` 独立扫描命令对象

它不只依赖当前 Key Map，因为命令可能同时因 Tool Handshake 未到而不在可执行索引中；Tier 拦截仍必须优先识别。

## 53. 限制名称会被 Normalize

`usage`、`/usage` 与 `Usage` 都被整理成小写、无前导斜杠的形式，Canonical Name 与 Alias 都会检查。

## 54. Glossary（五）：可见性语义

- **Hard hidden**：既不展示也不可执行的硬隐藏。
- **Menu hidden**：不主动提供，但完整手输仍可执行或获得本地提示。
- **Restricted**：用户可知道功能存在，但当前 Tier 不允许执行。
- **Fail-closed**：状态未知时默认拒绝能力。
- **Gate**：决定能力是否可见或可执行的条件。
- **Discoverability**：用户能否在 UI 中发现某项能力。
- **Upsell**：受限功能触发的升级说明界面。

## 55. Tool Requirement 是能力协商 Gate

命令可通过 `required_tools()` 声明依赖。只有 Agent Advertised Toolset 包含全部所需工具时才满足。

## 56. Unknown Toolset 不等于 Empty Toolset

`None` 表示尚未收到能力列表，`Some(empty)` 表示 Agent 明确没有工具；对有依赖的命令二者都拒绝，但含义不同。

## 57. Pre-session 也必须 Fail-closed

Home/Dashboard 尚无 Session 时不能先承诺 `/loop`，否则新 Session 绑定到无 Scheduler Tool 的 Agent 后才失败。

## 58. `visible(ctx)` 是命令自己的动态 Gate

Registry 无法知道 Announcement、Billing Surface 等全部 Context，因此命令可按 `AppCtx` 决定当前是否提供。

## 59. Session Scoped 与 Dashboard Only 是 Surface Gate

会话压缩、Fork、Rewind 等需要当前 Session；`/cd` 一类 Dashboard Command 则只应在无 Session 的入口出现。

## 60. `offered_when_session_less` 表达例外

某些命令虽与 Session 有关，却允许在 Session 尚未建立的 Surface 先出现；不要仅凭名称猜它是否可用。

## 61. `command_offered` 是组合 Gate

Controller 在一个中心函数组合 Registry 状态、`visible()`、Session/Surface、Mode 与 Required Tools，避免不同补全 Surface 各写一套规则。

## 62. Completion Gate 与 Execution Gate 必须呼应

菜单隐藏不代表一定不可执行，但 Mode、Tier、Tool 与 Feature 等安全边界必须在提交路径再次校验，不能信任 UI 已经拦过。

## 63. Glossary（六）：能力与 Surface

- **Tool advertisement**：Agent 通过协议声明它当前提供哪些工具。
- **Capability negotiation**：客户端根据对端声明决定可用功能。
- **Session-scoped**：语义依赖某个具体 Agent Session。
- **Dashboard surface**：尚未进入具体 Session 的 Agent 总览输入界面。
- **Surface**：承载同一功能的具体 UI 场景。
- **Runtime visibility**：根据当前状态动态变化的展示条件。
- **Defense in depth**：UI 与执行层重复落实重要边界。

## 64. `ModeSupport` 描述 Screen Mode 兼容性

命令可以支持 Minimal、Fullscreen 或 Both。默认 Both 适合大多数命令，特殊命令显式收窄。

## 65. 补全阶段隐藏不支持的 Mode

用户在 Minimal Mode 不应看到只能在 Fullscreen 使用的候选，这减少点击后失败。

## 66. 手输时不能当 Unknown

完整输入一个不支持当前 Mode 的已知命令，Dispatcher 仍解析到它，并由 `ModeSupport::refusal` 生成可操作提示。

## 67. 为什么不直接 PassThrough

如果当成 Unknown，文本可能进入 Shell 乃至模型；用户既得不到切换 Mode 的办法，也可能泄漏本应由本地解释的控制输入。

## 68. Refusal 是产品指导，不只是 Error

它可以说明命令在哪个 Mode 可用以及如何切换，让用户从失败状态走到可执行状态。

## 69. Mode Gate 的阅读原则

“不出现在菜单”和“完整手输的结果”是两个测试面；只测前者无法保证不会透传。

## 70. Glossary（七）：Mode 安全

- **Screen mode**：Pager 当前的 Minimal 或 Fullscreen 呈现模式。
- **Mode support**：命令声明兼容哪些 Screen Mode。
- **Refusal**：识别了意图，但因明确条件不满足而拒绝执行。
- **Unknown command**：当前客户端 Catalog 无法识别的命令。
- **Leak to model**：本应由客户端处理的控制文本被作为普通 Prompt 发给模型。
- **Actionable message**：不仅报错，还告诉用户如何解决的问题提示。

## 71. Controller 持有补全交互状态

`SlashController` 拥有 Registry、Matcher、Dropdown Snapshot、选择位置、Context Flags 与 MRU，不让 Composer 自己理解每个命令。

## 72. 只有合适的 Prompt 片段才激活 Slash 补全

Controller 从输入和 Cursor 判断当前是否处在命令 Token 或参数阶段；普通正文中的 `/` 不应无条件接管编辑。

## 73. 命令阶段先匹配 Trigger

Canonical Name 与 Alias 形成可搜索 Trigger；匹配结果最终仍指回同一个 Command Object。

## 74. Matcher 使用 Nucleo 模糊匹配

用户不必输入严格前缀，少量非连续字符也可能找到候选；匹配同时产出 Highlight Indices。

## 75. Smart Case 兼顾宽松与精确

普通小写 Query 可宽松匹配；用户显式输入大小写信息时，Matcher 能采用更严格意图。

## 76. 最大可见候选数是 6

Controller 限制 Dropdown 行数，使提示不遮住过多 Scrollback；完整候选集合仍可通过继续输入缩小。

## 77. Fuzzy Match 与 Ghost Suffix 规则不同

Dropdown 可显示任意合理模糊命中；行内 Ghost Suffix 只在已输入文本是候选前缀时出现。

## 78. 为什么 Ghost 必须更保守

Ghost 表达“按键即可补上剩余字符”。对非连续 Fuzzy Match 强行显示后缀，会让视觉字符串与实际替换结果不一致。

## 79. Highlight Indices 来自 Match Text

渲染层可标出 Query 命中的字符，帮助用户理解为什么某候选被排到前面。

## 80. Glossary（八）：匹配与展示

- **Fuzzy matching**：允许字符不完全连续的近似匹配。
- **Prefix match**：Query 是候选开头连续的一段。
- **Smart case**：根据 Query 是否含大写决定大小写敏感度。
- **Highlight indices**：候选中被 Query 命中的字符位置。
- **Ghost suffix**：Composer 行内显示、尚未真正插入的候选尾部。
- **Dropdown snapshot**：某一输入版本对应的菜单候选快照。
- **Selection**：当前键盘操作指向的候选行。

## 81. MRU 让个人常用命令更靠前

纯 Fuzzy Score 无法知道用户习惯。Most Recently Used 数据在匹配接近时提供个性化排序信号。

## 82. MRU 记录发生在确定执行命令后

Dispatcher 解析并找到已知 Command 后调用 `record_command_use`；仅在菜单中移动选择不会污染使用历史。

## 83. Alias 使用会归并到稳定命令

Controller 的测试覆盖 Alias 记录 Canonical Command 的情形，避免 `/quit` 与 `/exit` 被当成两个完全独立偏好。

## 84. 持久化不阻塞 UI

MRU Persistence 在后台排队；命令提交的交互响应不等待磁盘写入。

## 85. MRU 损坏不能破坏命令功能

它只是排序增强，不是执行真相。读取失败或旧数据异常时，Registry 与 Matcher 仍应按基础分数工作。

## 86. 参数阶段复用同一个 Dropdown

当命令接受参数且 Cursor 进入 Args 区域，Controller 调用 `suggest_args(ctx, args_query)` 并把 `ArgItem` 转成候选行。

## 87. `insert_text` 可以与 Display 不同

菜单可以显示人类友好的模型名，实际插入稳定 Model ID；Description 还可补充 Provider 或能力。

## 88. 参数 Query 保留原始文本语义

Command Implementation 最清楚空格、子命令或 Path 如何解释，因此 Controller 不应擅自做业务级 Parse。

## 89. Glossary（九）：排序与参数候选

- **MRU**：Most Recently Used，最近使用记录。
- **Ranking signal**：影响候选排序的一个信号。
- **Canonicalization**：把 Alias 等多种写法归并到稳定标识。
- **Argument phase**：命令 Token 已确定、正在输入参数的补全阶段。
- **Display text**：菜单展示文本。
- **Insert text**：接受候选后实际写入 Composer 的文本。
- **Query**：用户当前用于过滤候选的输入片段。

## 90. `parse_invocation` 只做语法切分

它要求输入以 `/` 开头，把第一个空白前内容作为 Token，其余部分作为 Args；它不在此处判断命令是否存在。

## 91. Bare `/` 没有有效 Token

单独斜杠或 Malformed Slash 不会凭空选择第一条命令；提交路径把原始文本交给 PassThrough 语义。

## 92. `is_command_complete` 服务 Enter 决策

它用 Registry 与两 Bit 参数契约判断当前输入是否已经足以执行，而不是实际运行命令。

## 93. 无参数命令写完即完整

`/exit` 不需要额外空格或参数；Alias 也按目标命令契约判断。

## 94. 可选参数命令空参数也完整

`/compact` 与 `/compact more context` 都可以提交，因为 `takes_args=true` 且 `args_required=false`。

## 95. 必填参数命令需要非空 Args

`/model` 仍处于补全状态，`/model grok-...` 才完整。

## 96. Unknown Slash 被视为可提交

客户端无法知道 Shell 或新 ACP Extension 是否理解它；阻止 Enter 反而会切断向后兼容的透传路径。

## 97. 完整性不等于合法性

它只回答“Enter 应补全还是提交”。参数是否存在、权限是否满足仍由执行路径决定。

## 98. Glossary（十）：解析与完整性

- **Invocation**：一次命令调用，由 Token 和 Args 构成。
- **Token**：斜杠后的命令名称部分。
- **Parser**：把输入切分为结构化片段的逻辑。
- **Completeness**：输入是否足以交给执行层，而非是否一定执行成功。
- **Malformed**：不符合预期语法、无法产生有效结构的输入。
- **Forward compatibility**：旧客户端仍能把未来扩展交给更新的后端理解。

## 99. ACP 普通命令是 Pass-through Wrapper

`AcpSlashCommand` 为动态元数据实现同一 Trait；普通 ACP Command 的 `run()` 返回原样 `/name args` 的 `PassThrough`。

## 100. 为什么 Pager 不直接实现 ACP 命令

Pager 只知道协议公布的名称与描述，不知道 Agent 内部语义。Shell 是真正的解释者。

## 101. ACP Skill 是特殊分支

Skill 元数据允许 Pager 读取 Client-side `SKILL.md`、应用参数替换，并构造 Structured Prompt Blocks。

## 102. `InjectSkill` 分离 Display 与 Wire

`display_text` 给 Scrollback 看，`prompt_blocks` 才送给模型；内部 Skill 指令无需完整暴露在普通用户回显中。

## 103. `display_as_skill` 控制样式而非语义

真正 Skill 可用 Teal Accent；`/loop` 等 Builtin 也可能注入 Structured Prompt，却不一定显示成 Skill。

## 104. Skill Metadata 错误在本地变成 Error

文件缺失、Frontmatter 或替换信息非法时，Pager 返回清晰错误，不发送半构造 Prompt。

## 105. Scheduled Task Preview 是乐观 UI

`InjectSkill` 可附带临时任务信息，让 Tasks Pane 立即出现记录；服务端真实通知到达后再替换 Provisional Entry。

## 106. Glossary（十一）：ACP 与 Skill

- **ACP**：Agent Client Protocol，Pager 与 Agent/Shell 协作的协议。
- **Pass-through wrapper**：客户端提供 UI 元数据，但把执行文本交给后端的包装命令。
- **Skill**：带说明文件和结构化 Prompt 注入逻辑的可调用能力。
- **SKILL.md**：描述 Skill 指令、元数据和使用方式的文件。
- **Wire blocks**：实际通过协议发送给模型端的结构化内容块。
- **Optimistic UI**：权威响应到达前先显示预期结果。
- **Provisional entry**：等待真实 ID/状态替换的临时记录。

## 107. 提交路径先决定是否按 Slash 解释

Literal Send 等路径可以绕过命令解释；正常提交会 Trim 后调用 `parse_invocation`。

## 108. Restricted 检查必须早于 Unknown

Tier 命令被 Registry 执行 Lookup 拒绝后看起来像 `None`。Dispatcher 先用 `is_restricted` 识别，才能打开 Upsell 而不是透传。

## 109. 然后使用 `get_for_dispatch`

这让 Menu-hidden 命令仍到达 Pager Handler，同时 Hard-hidden、Restricted 和 Tool-unsatisfied 命令不会被宽松绕过。

## 110. Telemetry 区分 Builtin 与 Non-builtin

Dispatcher 在执行前记录命令 Token 与来源类型，用于了解本地命令和动态/未知 Slash 的使用，不改变行为。

## 111. Mode Refusal 在 `run()` 前集中执行

命令实现无需各自复制 Screen Mode Error；已知但不兼容的命令统一生成 Message。

## 112. 成功解析后才记录 MRU

已知命令通过 Gate 并准备运行时记录使用；Unknown PassThrough 不应影响本地命令排序。

## 113. `run()` 接收原始 Args

命令实现按自己的语法解析；Dispatcher 不把所有命令强制塞进一个通用参数 AST。

## 114. Glossary（十二）：提交防线

- **Literal send**：明确要求把文本当普通 Prompt，而不解释控制语法。
- **Execution lookup**：为完整提交解析命令的 Registry 查询。
- **Telemetry**：用于观测产品行为的事件数据。
- **Typed invocation**：用户完整手输并提交的命令调用。
- **AST**：Abstract Syntax Tree，解析后统一表达语法结构的树。
- **Interception**：在通用路径前识别并接管特殊意图。

## 115. `CommandResult` 是命令与 App 的协议

它没有直接修改所有状态，而是穷举命令可能产生的结果类别，让 Dispatcher 统一处理 Draft、Scrollback 与 Queue。

## 116. `Handled` 与 `HandledNoOp`

二者都不产生可见输出；后者表达“请求有效，但状态已经如此”。当前 Dispatcher 对两者行为相同。

## 117. `Error` 与 `Message`

二者都形成 System Render Block；语义上一个是失败，一个是正常的用户可见说明。

## 118. `Doctor` 进入诊断专用分发

它携带 Report、List Fixes 或 Fix Request，复用当前 App/Session Live State 构造诊断结果。

## 119. `Action` 回到统一 Dispatcher

Quit、Switch Model、Open Overlay、External Editor 等继续走应用已有状态机，而不是 Slash 子系统另造副作用通道。

## 120. `QueueCommand` 进入命令队列

`/compact` 等需要按 Shell Queue 顺序执行的命令保存 Raw Command Text，避免绕过现有排队与 Turn State 规则。

## 121. `InjectSkill` 进入 Prompt Queue

Dispatcher 创建 `QueuedPrompt`：Scrollback 用 Display Text，发送时可用 `wire_blocks`，并记录是否按 Skill 样式显示。

## 122. `PassThrough` 进入普通 Prompt Pipeline

ACP 普通命令和 Unknown Slash 暂时共享这一 Variant；Shell 获得原文本后决定能否解释。

## 123. 为什么 ACP 与 Unknown 暂时不拆 Variant

当前两者下游行为完全一致。源码注释也指出，未来若 Telemetry、Confidence UI 或错误语义不同，再拆成两个类型更合适。

## 124. Glossary（十三）：结果类型

- **No-op**：请求合法，但不需要改变当前状态。
- **Render block**：Scrollback 中可独立渲染的一块内容。
- **Queue command**：按 Session 命令队列顺序交给 Shell 的控制输入。
- **Queued prompt**：尚未发送、等待队列调度的 Prompt。
- **Discriminated result**：通过 Enum Variant 明确区分处理方式的结果。
- **Raw command text**：未被改写的原始命令字符串。

## 125. Draft 是否清除由入口语义决定

用户在 Composer 中输入 Slash 并提交时，`consume_input=true`，执行结果处理后清空这段命令文本。

## 126. Images 必须先转移再清 Composer

若 Slash 最终排入 Prompt Queue，Dispatcher 先把当前 Prompt Images Drain 到最后一个 Queue Entry，再清空 Prompt State。

## 127. Palette 命令不应吃掉 Composer Draft

用户可能先写了一半普通 Prompt，再打开 Command Palette 运行命令。这个命令并不来自 Draft，不能清掉已有文本。

## 128. `SendSlashCommandPreservingDraft` 表达来源差异

Palette/Workflow Overlay 使用保留 Draft 的 Action，复用相同命令执行逻辑，但把 `consume_input` 设为 false。

## 129. External Editor 是典型边界案例

手输 `/edit` 时 Composer 里正是命令，应先清掉；Palette 启动 External Editor 时，已有 Draft 恰恰是要交给编辑器的内容。

## 130. Exit Session 仍尊重消费规则

即使 Action 会切换界面，Dispatcher 也先按入口清理命令文本，避免返回原 Session 后看到陈旧 `/exit`。

## 131. Glossary（十四）：Draft 所有权

- **Draft**：Composer 中尚未提交的用户文本及附件状态。
- **Consume input**：命令执行时把当前输入视为自己的调用文本并清除。
- **Preserve draft**：命令来自其他 UI Surface，不修改 Composer 已有草稿。
- **Command palette**：不依赖直接输入 Slash 的命令选择界面。
- **Drain**：把资源所有权从一个状态容器转移到另一个容器。
- **Entry point**：用户进入同一功能的具体入口。

## 132. Unknown PassThrough 是刻意的扩展缝

客户端与 Shell 可能版本不一致，ACP Catalog 也可能在更新途中。把 Unknown Slash 当普通输入给后端，允许更晚一层理解它。

## 133. 透传必须排在安全 Gate 之后

已知 Restricted、Mode-unsupported 或 Menu-hidden 本地命令不能因 Lookup 差异掉进 Unknown 分支，否则会绕过产品边界或泄漏文本。

## 134. Bare Slash 也透传

Parser 无有效 Invocation 时保留原文本；这保持提交路径简单，但补全交互通常会在用户到达此处前提供候选。

## 135. PassThrough 仍识别 Skill Token Range

普通文本中若包含已识别 Skill Token，Dispatcher 计算 Range，用于 Scrollback Styling 和 Replay 恢复；这不改变 Shell 对全文的解释。

## 136. “透传”不等于“立即发模型”

它仍先进入 Session Prompt Queue，受 Queue、Running Turn、Wire Metadata 与 Drain 规则约束。

## 137. Glossary（十五）：透传与兼容

- **Pass-through**：当前层不解释语义，把原始输入交给下一层。
- **Extension seam**：允许未来能力接入而无需修改当前层的边界。
- **Version skew**：客户端与服务端版本不一致。
- **Token range**：文本中特定 Token 的起止位置，用于样式或元数据。
- **Replay**：重载历史后重新构造原显示语义。
- **Queue semantics**：输入进入队列后遵循的顺序、取消和发送规则。

## 138. 用 `/history` 套一次完整阅读

它作为 Builtin 注册，Trigger 进入 Matcher；当前 Surface 满足 Gate 时 `/his` 可模糊命中；完整提交解析到 Canonical Command，`run()` 返回打开 History Search 的 Action，Dispatcher 消费 `/history` 文本并切换面板。

## 139. 用 `/compact` 看可选参数

它接受但不要求 Args，所以 `/compact` 已完整；执行返回 `QueueCommand`，由 Session Queue 保证与其他输入的顺序。

## 140. 用 `/model` 看必填参数与建议

无参数时 Enter Complete Check 阻止立即执行；参数阶段用 Model State 生成 `ArgItem`；提交后命令再次验证 Model ID，再产生 Switch Action。

## 141. 用 `/voice` 看硬 Gate

Runtime Gate 未确认时 Registry Fail-closed；它不出菜单，也不能靠完整手输执行。Gate 打开后重建 Trigger，能力才出现。

## 142. 用 Tier 命令看 Restricted Gate

产品可让用户发现命令，但提交时 Dispatcher 先识别 Deny List 并进入 Upsell；文本不会到 Shell 或模型。

## 143. 用 ACP 普通命令看动态扩展

它随 Available Commands Update 进入 Registry，获得描述和补全；执行时返回 PassThrough，由 Shell 实现真实行为。

## 144. 用 ACP Skill 看结构化注入

它同样来自动态 Catalog，但 Pager 读取 Skill 内容，生成 Display/Wire 分离的 `InjectSkill`，因此不走普通 Raw Text 透传。

## 145. Glossary（十六）：示例串联

- **Builtin action command**：执行后产生 Pager Action 的内建命令。
- **Optional-args command**：允许零个或多个参数的命令。
- **Required-args command**：没有参数就不完整的命令。
- **Dynamic command**：由运行时协议更新加入或移除的命令。
- **Structured injection**：用协议 Content Block 而非单一字符串构造 Prompt。
- **Deny list**：明确列出当前禁止执行项的集合。

## 146. 测试应该按层分组

Trait/Command 测参数与 Result；Registry 测冲突和 Gate；Controller 测匹配、选择与参数行；Dispatcher 测提交后的 Draft、Queue 和安全行为。

## 147. Registry 必测矩阵

- Builtin Name/Name、Name/Alias 与 Alias/Alias 冲突。
- ACP Update 只替换 ACP，不删除 Builtin。
- Canonical Name 与 Alias 都能 Lookup。
- Hard-hidden、Menu-hidden、Restricted、Unknown Tools、Missing Tools。
- Runtime Gate 改变后 Trigger 是否重建。

## 148. Completion 必测矩阵

- Prefix 与非连续 Fuzzy Match。
- 大小写、Highlight Indices 与最大 6 行。
- Unsupported Mode、Session-less 和 Dashboard-only 过滤。
- Ghost 只对 Prefix Match 出现。
- Args Phase 的 Display/Insert 差异与选择写回。

## 149. Completeness 必测矩阵

- `/exit`、Alias、`/compact`、`/compact x`。
- `/model` 与 `/model id`。
- Unknown Slash、Bare Slash、非 Slash 文本。

## 150. Dispatch 必测矩阵

- Restricted 命令进入 Upsell 而非 PassThrough。
- Unsupported Mode 返回 Message 而非泄漏。
- Menu-hidden 完整手输仍到本地 Handler。
- Unknown/ACP 普通命令保留原文排队。
- Typed Entry 消费 Draft，Palette Entry 保留 Draft。
- Images、Skill Wire Blocks 与 Provisional Task 不丢失。

## 151. Glossary（十七）：验证语言

- **Test matrix**：把多个输入维度组合成系统化测试集合。
- **Unit test**：隔离测试一个模块或函数契约。
- **Integration behavior**：多个层连接后才出现的整体行为。
- **Regression**：已有正确行为被后续修改破坏。
- **Invariant**：无论具体命令如何变化都必须成立的约束。
- **Fail-fast**：遇到开发期不变量破坏时立即失败。

## 152. 调试“不显示”先看四层 Gate

依次检查 Registry Hard/Menu/Restricted、Toolset、Command `visible()`、Surface 与 `ModeSupport`。不要一开始只改 Matcher。

## 153. 调试“显示但不能执行”先区分预期语义

Restricted 本来就应可发现不可执行；Menu-hidden 则相反——不提供但可手输。先确定命令属于哪一种产品语义。

## 154. 调试“选中写回不对”看 `ArgItem`

确认渲染使用 Display，Filter 使用 Match Text，接受候选使用 Insert Text；三者混用会产生看似随机的补全错误。

## 155. 调试“命令被发给模型”看查找顺序

重点检查 Restricted Interception、`get_for_dispatch`、Mode Refusal 和 Unknown 分支。通常不是模型问题，而是已知命令提前变成了 `None`。

## 156. 调试“Palette 清空草稿”看入口 Action

确认 Palette 使用 `SendSlashCommandPreservingDraft`，并让同一核心函数收到 `consume_input=false`，而不是复制一套执行实现。

## 157. 调试“ACP 命令消失”看整代更新

确认最新 `AvailableCommandsUpdate` 是否包含它、Client/Plugin Collision 是否改了名称、Toolset 是否同步，以及 Trigger 是否在状态合并后重建。

## 158. Glossary（十八）：调试定位

- **Lookup order**：多个识别和拒绝步骤的先后顺序。
- **Stale trigger**：命令集合已变化但补全索引仍是旧版本。
- **Generation**：一批应保持一致的状态更新版本。
- **Symptom**：用户看到的表面问题。
- **Root cause**：真正导致症状的底层原因。
- **Instrumentation**：日志、Telemetry 或诊断信息等观测手段。

## 159. 新增 Builtin 的最小检查表

1. 在 `commands/` 实现 `SlashCommand`，定义稳定 Name、Alias、Description 与 Usage。
2. 正确设置参数两 Bit、Session/Surface、Mode、Visibility 和 Required Tools。
3. 让 `run()` 返回最窄的 `CommandResult`，异步工作返回 Action。
4. 注册到 Builtin Catalog，检查名称与 Alias 冲突。
5. 增加 Completeness、Completion、Gate 和 Dispatch 测试。
6. 分别验证手输、Dropdown、Palette 与不兼容 Mode。

## 160. 不要在 `visible()` 中产生副作用

它会随每次 Prompt 编辑和 Context Refresh 高频调用；副作用会被重复触发，也会让输入延迟不可控。

## 161. 不要把权限只做在菜单层

用户可以完整手输、粘贴、从 History Recall，甚至由其他 UI Entry 触发。执行层必须重新落实 Gate。

## 162. 不要让命令自己异步改全局 App

返回 Action/Result 可以保留统一状态机、Draft 清理、日志和测试边界。

## 163. 不要把所有 Unknown 都改成本地 Error

这会破坏 ACP 动态扩展和新 Shell/旧 Pager 的兼容性。只有已知受限或不兼容命令应在本地明确拒绝。

## 164. 不要用同一个 Hidden Boolean 表达所有产品状态

“不存在”“不推广”“可发现但需升级”“缺能力”“当前 Surface 不适用”有不同提交行为，压成一个值后必然出现透传或绕过错误。

## 165. Glossary（十九）：实现守则

- **Side effect**：除返回值外对外部状态产生的变化。
- **Idempotent**：重复执行仍得到等价状态的性质。
- **Authorization**：判断当前主体是否允许执行操作。
- **Typed state machine**：用 Enum/Struct 表达合法状态与转移的状态机。
- **Backward compatibility**：新实现仍支持旧输入或旧对端。
- **Semantic distinction**：外观相似但行为契约不同的概念边界。

## 166. 一句话记住整个系统

Registry 决定命令身份与基础能力，Controller 决定当前是否提供以及如何补全，Dispatcher 对完整提交重新做安全解释并统一管理副作用。

## 167. 最重要的五个不变量

1. ACP 更新不能删除或劫持 Builtin。
2. UI 不展示不等于执行层可以不校验。
3. 已知受限/Mode 不兼容命令绝不能落入 Unknown PassThrough。
4. 命令 Result 必须经过统一 Dispatcher 才能正确处理 Draft、Queue 和 Scrollback。
5. Palette 发起的命令不能消费与它无关的 Composer Draft。

## 168. 推荐的源码阅读顺序

先读 `command.rs` 建立 Trait/Result 词汇，再读 `registry.rs` 理解 Catalog 与四种 Gate，然后读 `slash/mod.rs` 的刷新、匹配、`command_offered`、Parse 与 Completeness，最后沿 `app/dispatch/prompt.rs` 看每个 Result 如何真正改变应用。

## 169. 最终心智模型

Slash Command 并非“看到 `/` 就查表执行”。它是一条带版本化 Catalog、能力协商、Surface/Mode/Tier 防线、模糊交互、参数契约、同步决策与队列副作用的输入协议。真正的可靠性来自：同一个命令在“是否存在”“是否提供”“是否可执行”“执行后消费谁的 Draft”四个问题上分别作答，又通过中心 Dispatcher 收拢为一致行为。

