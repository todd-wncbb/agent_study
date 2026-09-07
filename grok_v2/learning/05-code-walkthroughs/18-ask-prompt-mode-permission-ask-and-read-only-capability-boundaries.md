# Walkthrough：Ask Prompt Mode 如何传播，以及它为什么不等于 Permission Ask

> 场景：客户端在一条 Prompt 的 `_meta.mode` 中发送 `"ask"`，希望模型只回答问题，不修改代码。请求经过 ACP、Session prompt queue 和 Turn 初始化，最终被记录为 `PromptMode::Ask`。但当模型提出 Tool Call 时，当前 build 并没有根据 Ask 自动过滤工具或增加执行门禁。与此同时，设置中的 `permission_mode = "ask"` 仍会在敏感工具调用时询问用户——这是另一套权限状态机。本文沿两条同名链路分别追踪，并说明真正的 read-only capability 在哪里实现。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。特别注意：本文记录的是该版本的实际实现，而不把类型注释中的设计意图误写成已经落地的安全保证。

---

## 1. 本文最重要的结论

仓库里至少有三种容易被叫作“Ask/只读”的概念：

| 概念 | 所属层 | 当前实际作用 |
| --- | --- | --- |
| `PromptMode::Ask` | Prompt / Turn | 标记这条 Prompt 的语义与 telemetry；进入时会退出 Plan |
| `PermissionMode::Ask` | Tool permission | 敏感访问不自动放行，交给策略与用户确认 |
| `CapabilityMode::ReadOnly` | Workspace Session / Subagent | 创建工具集时真正移除 Edit、Execute、Task 等类别 |

它们名字相近，却没有自动互相转换。

## 2. Ask Prompt 的当前调用链

```text
Client prompt
  meta.mode = "ask"
  -> MvpAgent::prompt()
  -> PromptMode::from_meta_str("ask")
  -> PromptMode::Ask
  -> SessionCommand::Prompt
  -> SessionActor::queue_input(..., PromptMode::Ask, ...)
  -> InputItem.prompt_mode = Ask
  -> scheduler promotes queue front
  -> SessionActor::handle_prompt(..., Ask, ...)
     -> turn_start_prompt_mode = Ask
     -> turn_prompt_mode = Ask
     -> reconcile_plan_mode_with_prompt(Ask)
        -> if Plan engaged: user_exit(false)
     -> normal slash/prompt preparation
     -> prepare_tool_definitions_inner()
        -> current build does not inspect Ask
     -> normal sampling + Tool Call loop
  -> TurnDeltaSnapshot
     start_prompt_mode = "ask"
     end_prompt_mode = "ask"（除非本 Turn 内模式工具改变）
```

## 3. Permission Ask 是另一条链

```text
Tool Call
  -> parse ToolInput
  -> classify AccessKind
  -> Hook / Plan edit gate
  -> PermissionManager
     effective PermissionMode::Ask
     -> policy allow: 直接允许
     -> policy deny: 拒绝
     -> policy ask / needs-user: 向客户端请求决定
  -> Decision
     -> Allow / Ask: dispatch
     -> Reject / Cancelled / Followup: 不执行
```

这里的 Ask 回答的是“这次工具是否需要用户批准”，不是“这一条 Prompt 是否只回答问题”。

## 4. ReadOnly Capability 又是第三条链

```text
创建或 Fork Workspace Session / Subagent
  -> CapabilityMode::ReadOnly
  -> resolve_session_toolset()
  -> CapabilityMode::filter(tool config)
  -> 按 ToolKind 保留 read/search/inspect/meta
  -> 丢弃 edit/write/delete/move/execute/task/workflow 等
  -> child session 从一开始就看不到这些工具
```

这是工具集构造期约束，不是每条 Prompt 的动态标签。

## 5. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| PromptMode 类型 | `xai-grok-shell/src/session/plan_mode.rs` | `PromptMode` |
| ACP Prompt 入口 | `.../agent/mvp_agent/acp_agent.rs` | `prompt`、`set_session_mode` |
| Prompt Queue | `.../acp_session_impl/prompt_queue.rs` | `queue_input`、`InputItem` |
| Turn 初始化 | `.../acp_session_impl/turn.rs` | `handle_prompt` |
| 模式协调 | `.../acp_session_impl/session_mode.rs` | `reconcile_plan_mode_with_prompt` |
| 工具定义准备 | `.../acp_session_impl/sampler_turn.rs` | `prepare_tool_definitions_inner` |
| SessionMode 类型 | `xai-grok-tools/src/types/session_mode.rs` | `SessionMode` |
| 权限类型 | `xai-grok-workspace/src/permission/types.rs` | `AccessKind`、`Decision` |
| 权限配置解析 | `xai-grok-shell/src/util/config/permissions.rs` | `resolve_permission_mode` |
| Capability 过滤 | `xai-grok-workspace/src/capability.rs` | `CapabilityMode`、`kind_allowed` |
| Toolset 解析 | `xai-grok-workspace/src/session/tool_config.rs` | `resolve_session_toolset` |
| Prompt 模式测试 | `.../prompt_mode_transition_tests.rs` | mapping/reconcile cases |

## 6. PromptMode 的三个值

```rust
pub enum PromptMode {
    Agent,
    Ask,
    Plan,
}
```

默认值是 `Agent`。Serde 与 Display 使用 snake_case，因此日志和 snapshot 中分别表现为 `agent`、`ask`、`plan`。

## 7. Agent 表示执行型 Prompt

`PromptMode::Agent` 是默认模式，表示这条 Prompt 预期可以使用工具和修改文件。

它并不等于 AlwaysApprove：执行型 Prompt 仍要经过 Hook、Plan gate、PermissionManager 和工具自己的约束。

## 8. Ask 表示问答意图

源码注释把 Ask 描述为：question-answering only, no tool use。

这是类型契约表达的产品意图。然而从安全审计角度，必须继续追踪所有消费点，确认“no tool use”是否由执行层强制。

## 9. Plan 表示规划意图

Plan 同样被注释为 planning/reasoning only, no tool use，但第 17 篇已经看到，它还有独立的 `PlanModeTracker`、提醒、plan file 例外、edit gate 与退出审批。

因此 Plan 的执行保证不只来自 PromptMode 值。

## 10. from_meta_str 的解析规则

```text
"ask"  -> Ask
"plan" -> Plan
其他    -> Agent
```

匹配区分大小写，`"ASK"`、`"Plan"`、`"code"` 和未知值都回退 Agent。

## 11. 未知值为什么回退 Agent

当前实现选择兼容性默认：旧 Shell 收到未来客户端的新模式时，仍按正常 Agent Prompt 处理，而不是拒绝整个请求。

这是一种 fail-open 的功能兼容选择；若模式字符串被当成安全边界，这种回退就不够安全。因此安全约束不能只依赖 `_meta.mode`。

## 12. SessionMode 与 PromptMode 的名称不完全一致

ACP SessionMode 是：

```text
Default / Plan / Ask
```

内部 PromptMode 是：

```text
Agent / Plan / Ask
```

映射时 `SessionMode::Default -> PromptMode::Agent`。

## 13. prompt_mode_from_session_mode_id 使用 typed parser

`prompt_mode_from_session_mode_id()` 先调用 `SessionMode::from_id()`，再匹配 enum，而不是在每个调用点散落字符串判断。

这保证 known ID 的映射集中，也继承了 unknown ID 回退 Default 的规则。

## 14. Prompt 可以携带自己的 mode

ACP `prompt()` 首先读取：

```text
arguments.meta["mode"]
```

若存在字符串，就用 `PromptMode::from_meta_str()` 解析。这使单条 Prompt 可以显式声明模式。

## 15. Prompt 没带 mode 时读取 Session 当前值

入口通过 Session command channel 请求 `GetCurrentPromptMode`，等待 SessionActor 返回当前值。

因此 `session/set_mode("ask")` 可以影响后续未显式带 mode 的 Prompt。

## 16. Prompt metadata 优先于 Session 默认

只要 `_meta.mode` 存在，入口不再读取 Session current mode。

这说明 current mode 是缺省值，Prompt metadata 是该请求的显式覆盖。

## 17. mode 随 InputItem 进入队列

`queue_input()` 把它保存到：

```rust
InputItem {
    prompt_mode,
    ...
}
```

即使 Prompt 因前面已有 Turn 而等待，模式也和这条输入绑定，不会在真正执行时重新读取全局值。

## 18. 为什么模式必须随 Prompt 冻结

考虑：

```text
P1 以 Ask 入队
用户切换 Session 为 Agent
P1 稍后才运行
```

若 promotion 时读取最新 Session mode，P1 的原始意图会被改写。随 InputItem 保存可以保证每条队列项的语义稳定。

## 19. send_now 不改变 prompt_mode

`send_now` 可能把真实用户 Prompt 插到运行项之后，并取消当前 Turn，但它移动的是完整 InputItem。

PromptMode、artifact context、client identifier 和 response channel 都随项目一起移动。

## 20. Synthetic Prompt 也必须显式选择模式

系统自动生成的 Resume、Goal、Task wake 等 Prompt 调用 `queue_input()` 时也传 PromptMode。

例如 Plan 审批恢复：修改意见用 Plan，批准实施用 Agent。这避免 synthetic turn 无意继承用户刚切换的全局模式。

## 21. handle_prompt 在最早阶段记录模式

Turn 开始后，代码先写：

```text
turn_start_prompt_mode = prompt_mode
turn_prompt_mode = prompt_mode
```

然后才进行 slash command、skill rewrite、prompt 构造和 sampling。

## 22. start 与 end 是两份状态

`turn_start_prompt_mode` 固定记录入口；`turn_prompt_mode` 可以在 Turn 中被 `enter_plan_mode` 或批准后的 `exit_plan_mode` 改变。

因此一次 Turn 可出现：

```text
start = agent
end   = plan
```

Ask Turn 若不发生工具模式切换，通常两者都是 ask。

## 23. 模式写入 TurnDeltaSnapshot

`apply_prompt_modes_to_snapshot()` 把两个值转为字符串，写入：

- `start_prompt_mode`；
- `end_prompt_mode`。

这份 snapshot 会进入 Turn trace/feedback 上传，使离线分析能区分问答、规划和执行型请求。

## 24. PromptMode 首先是一条可观测性维度

从实际消费点看，Ask 最确定的生产作用是：

- 随 Prompt 排队；
- 标记 Turn；
- 影响 Plan tracker reconcile；
- 进入 telemetry/snapshot。

所以它至少是可靠的“用户意图标签”。

## 25. Ask 会让 Plan Mode 退出

`reconcile_plan_mode_with_prompt()` 把 `Agent | Ask` 放在同一分支。如果 Tracker 不是 Inactive，就调用：

```text
user_exit(false)
```

因此带 Ask 的 Prompt 不会继续维持 file-backed Plan 生命周期。

## 26. 为什么 Ask 不应继承 Plan gate

Ask 不是“继续编辑方案”，也没有 plan file 产出和 exit approval。若前一状态是 Plan，进入 Ask 应先退出 Plan，避免仍要求 Turn 只能以 ask-user 或 exit-plan 结束。

## 27. 这里传 false 的含义

Prompt promotion 已经处在新 Turn 的边界，reconcile 把退出视作没有旧 Plan Turn 正在飞行，因此调用 `user_exit(false)`，而不是进入 ExitPending。

## 28. Ask 没有独立 Tracker

仓库没有 `AskModeTracker`、Ask reminder count、Ask sidecar 或 Ask approval state。

Ask 生命周期只由 current PromptMode 与每个 InputItem 的冻结值表示。

## 29. Ask 没有专属 reminder

`inject_plan_mode_reminders()` 只理解 Plan Tracker 状态。

当前代码没有在 Ask Turn 前注入“不要调用工具”的动态 system reminder。

## 30. Ask 没有 plan file 例外

它不创建 artifact，也不允许某个特殊文件可写。`plan.md` 只属于 Plan Mode。

## 31. Ask 没有退出工具

没有 `enter_ask_mode` / `exit_ask_mode`。客户端可以用 Session mode 或单 Prompt metadata 选择 Ask，下一条 Prompt 再选择 Agent 即可。

## 32. 工具定义准备不读取 Ask

当前 `prepare_tool_definitions_inner()` 做的动态模式检查只有：

```text
plan_active = self.plan_mode.lock().is_active()
filter_cursor_tools_by_plan_mode(defs, plan_active)
```

它没有读取 `turn_prompt_mode` 或 `current_prompt_mode` 来过滤 Ask 工具。

## 33. Plan filter 当前本身也是 pass-through

第 17 篇说明过，`filter_cursor_tools_by_plan_mode()` 在本 build 原样返回 definitions。

Plan 依靠执行硬门禁兜底；Ask 则连对应的 Ask gate 也不存在。

## 34. Tool Call 准备流程不检查 PromptMode::Ask

`prepare_tool_call()` 会处理：

- 参数解析；
- MCP；
- Hooks；
- Plan edit gate；
- PermissionManager；
- ExitPlan approval。

当前没有“若 turn mode 为 Ask 则拒绝任何 tool call”的分支。

## 35. is_read_only() 的定义

`PromptMode::is_read_only()` 返回：

```text
Ask  -> true
Plan -> true
Agent-> false
```

这清楚表达设计分类。

## 36. 但 is_read_only() 当前没有生产调用点

对 `xai-grok-shell/src` 的引用搜索只找到方法定义与单元测试断言，没有工具准备、权限或 Fork 路径调用它。

所以不能从这个方法存在，推导 Ask 已被运行时强制只读。

## 37. 注释不是执行保证

代码学习中应区分：

1. enum 注释表达的目标语义；
2. 方法返回的分类；
3. 生产调用点真正采取的动作；
4. 端到端测试固定的行为。

Ask 当前具备前两项和部分 telemetry 测试，但缺少执行门禁链。

## 38. 当前 Ask 更接近 soft intent

它能告诉后续系统“用户想问问题”，但如果模型仍调用 edit/bash，调用会按普通 Agent Turn 的 Hook 和 permission policy 处理。

这不代表调用必定成功；它只是不会因为 PromptMode::Ask 本身而失败。

## 39. PermissionMode::Ask 可能间接阻止写入

若全局权限模式也是 Ask，Edit/Bash 往往会触发用户确认。用户拒绝后，调用不会执行。

但这是权限系统阻止的，不是 Ask Prompt 的只读保证。

## 40. 为什么“可能询问用户”不等于“只读”

在 Permission Ask 下：

- 静态 allow rule 可能直接允许；
- persisted/session grant 可能允许；
- safe command 可能允许；
- 用户可以在弹框中批准。

只读要求“不论用户确认策略如何，写操作都不可用”，两者强度不同。

## 41. PermissionMode 的三个主要值

```text
Ask
Auto
AlwaysApprove
```

它们控制工具权限决策：交互询问、分类器自动判断或快速批准。

## 42. PermissionMode::Ask 不属于 PromptMode

两者甚至定义在不同 crate：

- PromptMode：Shell Session；
- PermissionMode：Telemetry enum，经 Shell config re-export，供 Workspace permission 使用。

它们没有共同 trait，也没有自动映射函数。

## 43. permission_mode 字符串的解析

```text
"always-approve" -> AlwaysApprove
"auto"           -> Auto
"ask"            -> Ask
"default"        -> Ask
未知              -> Ask
```

这里未知值回退 Ask 是安全方向，因为不会误开 YOLO。

## 44. 两个未知值回退方向不同

| Parser | 未知值回退 | 原因 |
| --- | --- | --- |
| PromptMode | Agent | 功能兼容，继续正常 Turn |
| PermissionMode | Ask | 权限安全，不自动批准 |

这正说明它们解决的是不同问题。

## 45. 配置优先级

`resolve_permission_mode()` 的纯函数优先级为：

```text
effective TOML [ui] explicit keys
  > remote permission_mode
  > Ask
```

CLI 与 managed policy 在更上层继续参与最终 launch 决策。

## 46. 旧配置也会映射到 Permission Ask

兼容入口包括：

- `approval_mode`；
- `yolo = false`；
- `permission_mode = "default"`。

它们最终可投影成 Ask enforcement。

## 47. Permission Request 处理具体访问

`AccessKind` 携带调用事实：

- `Read`；
- `Grep`；
- `Edit(path)`；
- `Bash(command)`；
- `MCPTool { name, input }`；
- WebFetch/WebSearch。

权限决策针对某次访问，而不是只看 Prompt 标签。

## 48. Decision::Ask 是中间决策语义

`Decision` 包括 Allow、Ask、FollowupMessage、Reject、PolicyDeny、Cancelled。

Policy 的 Ask 表示需要交互；Permission actor 负责把请求发送客户端并收敛结果。Tool Call 边界最终对已解决的 Allow/Ask 兼容放行。

## 49. Permission Ask 可以产生记忆授权

用户可能选择 allow once、allow always 或基于命令前缀授权。后续同类调用可命中 persisted/session grant。

Ask Prompt 没有这种授权存储语义。

## 50. Permission Ask 有独立 telemetry

`PermissionEvent` 记录：

- effective permission mode；
- 是否询问用户；
- prompt outcome；
- decision reason；
- classifier source/latency；
- queue depth。

这些字段和 Turn 的 start/end prompt mode 是两套观测维度。

## 51. 同一个 Turn 可以同时是 Ask + AlwaysApprove

PromptMode 和 PermissionMode 正交，所以理论组合包括：

```text
PromptMode::Ask
PermissionMode::AlwaysApprove
```

当前实现下，如果模型提出工具调用，没有 Ask gate；AlwaysApprove 还可能批准常规访问。

这是最能暴露“Ask 并非硬只读”的组合。

## 52. 同一个 Agent Turn 也可以 Permission Ask

```text
PromptMode::Agent
PermissionMode::Ask
```

这是常见交互编码模式：模型可以工作，但敏感操作需用户批准。

不要把 UI 上的“Ask permissions”误解为问答模式。

## 53. Plan + Permission Ask 也同时存在

Plan edit gate 先禁止非 plan file 编辑；合法 plan.md 编辑可自动批准；其他非 edit 工具仍可能进入 Permission Ask。

PlanMode 与 PermissionMode 同样是叠加关系。

## 54. CapabilityMode 才会过滤 Toolset

`CapabilityMode::filter()` 遍历 `ToolServerConfig.tools`，根据每个 `ToolConfig.kind` 决定保留或丢弃，然后返回新的配置副本。

模型在采样前看到的工具表因此已经变窄。

## 55. CapabilityMode 的四种集合

```text
ReadOnly
ReadWrite
Execute
All
```

它们不是一条简单直线：ReadWrite 与 Execute 不可比较。

## 56. Capability 偏序

```text
             All
            /   \
   ReadWrite     Execute
            \   /
           ReadOnly
```

ReadOnly 是两条能力分支的共同子集；ReadWrite 有编辑无 Shell，Execute 有 Shell 无编辑。

## 57. ReadOnly 保留什么

按 `kind_allowed()`，它保留：

- Read、Search、WebSearch/WebFetch；
- MemoryGet/MemorySearch；
- LSP、ListDir、List；
- Plan、EnterPlan、ExitPlan、AskUser、Skill、SearchTool、GoalUpdate。

这些是读取、检查或元工具。

## 58. ReadOnly 丢弃什么

它不保留：

- Edit、Write、Delete、Move；
- Execute；
- BackgroundTask/Wait/Kill；
- Task、Monitor、Workflow；
- Image/Video/Deploy 等产出型工具；
- `Other`。

## 59. ReadOnly 仍允许 Plan 元工具

Capability 表把 Plan、EnterPlan、ExitPlan 视为 always allowed meta tools。

这允许只读 Subagent 表达计划与提问，但不会因此获得 workspace edit。

## 60. kind=None 是需要警惕的例外

`CapabilityMode::filter()` 对 baseline `kind: None` 工具选择保留，因为无法分类。

但 MCP-origin 的 kind=None 在 `resolve_session_toolset()` 中有不同的保守处理。必须结合工具来源阅读，而不是只看这个纯函数。

## 61. 为什么 Capability 适合 Session/Subagent

它在 Session bind 或 child creation 时冻结工具形状，适合“这个 Agent 整个生命周期只能探索”的角色。

PromptMode 则是每条输入可变化的动态属性，二者生命周期不同。

## 62. Fork 防止 capability widening

Child capability 必须是 parent capability 的子集。

例如 ReadOnly parent 不能 Fork 出 All child；ReadWrite parent 也不能 Fork 出 Execute child，因为后者新增了 Shell 能力。

## 63. Ask Prompt 当前不会自动创建 ReadOnly child

没有生产调用把：

```text
PromptMode::Ask -> CapabilityMode::ReadOnly
```

也没有为主 Session 每 Turn 重绑 Workspace toolset。

所以这两套“read-only”目前只是概念上相关。

## 64. 为什么不能在每 Turn 随便缩工具集

Workspace Session capability 可能在 bind 时冻结，并参与 parent-child subset、MCP 合并和 owner/consumer 顺序。

若单条 Prompt 动态重绑，可能改变共享 Session 的能力、影响排队 Prompt，甚至违反已冻结配置。实现 Ask 硬只读需要设计明确的 per-turn filter/gate，而不是复用 Session bind 粗暴切换。

## 65. 一个可行的 Ask 工具过滤方案

在 `prepare_tool_definitions_inner()` 中读取当前 Turn mode，对 Ask 只发送 read/meta ToolKinds。

优点：模型看不到写工具，减少误调用与 token。

缺点：仅隐藏工具不是安全边界；wire name override、MCP kind=None、缓存 definitions 都需处理。

## 66. 还必须增加执行硬门禁

即使采样请求不包含写工具，模型仍可能幻觉调用某个名字，或恢复/兼容路径带入旧 Tool Call。

因此 `prepare_tool_call()` 还应检查 Turn mode + AccessKind，拒绝 Edit、Bash 和不透明有副作用调用。

## 67. Ask gate 应允许哪些工具是产品决策

“只回答问题”可能有不同定义：

- 纯文本，不允许任何工具；
- 允许 read/search/LSP；
- 允许 web/MCP 读取；
- 允许 AskUser；
- 是否允许 Todo/Plan meta tools。

不能只复用 `ToolMetadata::is_read_only` 而不明确边界。

## 68. Bash 的只读判定尤其困难

某些 Shell 命令只读取，另一些会写文件、发网络请求或启动进程。若 Ask 目标是强保证，按 ToolKind::Execute 整类禁止最简单可靠。

若允许“安全 Bash”，就必须依赖命令分类器，保证强度会低于真正的 capability filtering。

## 69. MCP 的只读判定也困难

远端 MCP tool 可能不声明可靠 kind，名字和 schema 也不能完全证明无副作用。

强 Ask 模式应对未知远程工具 fail closed，或要求显式 read-only capability metadata。

## 70. Hosted tools 也要纳入

`prepare_tool_definitions_inner()` 处理内置 definitions，但 sampling 还可能包含 hosted tools，例如 backend search。

实现 Ask gate 时需要同时审计 `hosted_tools_for_turn()`，避免只过滤一半工具面。

## 71. Direct Bash 是旁路风险

`handle_prompt()` 在普通 prompt 处理前会识别直接 Bash command 并调用 `handle_direct_bash_command()`。

Ask 硬只读若只加在模型 Tool Call pipeline，仍可能漏掉这种用户显式命令路径。产品可能允许用户直接 Bash，但应明确它是否受 Prompt mode 约束。

## 72. Slash Command 也是独立控制面

Slash command 可能触发 Goal、Workflow、Plan 或其他 Session 行为，不一定经过模型 Tool Call。

所以“Ask Turn 无副作用”的强定义还需要审计 slash resolution，而不只是工具表。

## 73. Interjection 需要冻结模式语义

运行中插话可能使用当前 Plan Tracker 推导 PromptMode。Ask 没有 Tracker，所以 fallback/interjection 是否继续 Ask 取决于调用点传值。

这也是单 Prompt Ask 与 Session mode Ask 需要区分的地方。

## 74. Pager 当前主要暴露二值 Plan 开关

Pager 的 `PlanModeKind` 只有 On/Off，对应 SessionMode Plan/Default。

源码注释明确：关闭 Plan 总是回 Default；若 Session 曾由外部注入 Ask，Pager 的 Plan toggle 不保留那个偏好。

## 75. Ask 可能来自非 Pager 客户端

Canonical `SessionMode::Ask` 和 `_meta.mode = "ask"` 为其他 ACP 客户端保留协议入口。

不能因为 Pager 没有 Ask 按钮，就认为 Shell 中 Ask enum 是死代码；Prompt metadata 路径仍可产生它。

## 76. Session set_mode 的 Ask 行为

`handle_session_mode("ask")`：

1. `current_prompt_mode = Ask`；
2. 若此前在 Plan，执行用户退出逻辑并持久化；
3. 尝试按 mode ID 发现同名 AgentDefinition；通常没有则停止；
4. 后续无显式 mode 的 Prompt 继承 Ask。

它不会设置独立 Ask active flag。

## 77. 单 Prompt Ask 与 Session Ask

| 入口 | 影响范围 |
| --- | --- |
| `_meta.mode = ask` | 该 InputItem/Turn；handle_prompt 也会更新 current_prompt_mode |
| `session/set_mode(ask)` | Session 当前缺省，供后续 Prompt 读取 |

注意 reconcile 会写 `current_prompt_mode`，所以显式 Prompt mode 也会使 Session 当前值随之变化。

## 78. Ask 切回 Agent

客户端可以：

- `session/set_mode(default)`；
- 下一条 Prompt 显式 `_meta.mode = "agent"` 或未知/默认字符串路径；
- 不带 meta，但先把 current mode 设为 Default。

没有审批和 exit tool。

## 79. 模式切换与权限切换的 UI 也不同

Plan/Session mode 使用 `SetSessionMode` 与 `CurrentModeUpdate`。

Permission mode 使用 settings、YOLO/auto 通知和 PermissionManager 状态。两个 UI 控件可能同时存在，不应互相覆盖。

## 80. 常见误读：Ask Mode 会弹权限框

不一定。弹框由 PermissionMode、policy、AccessKind、已有 grant 和 client capability 决定。

PromptMode::Ask 本身不发 permission request。

## 81. 常见误读：Permission Ask 让模型只回答

不对。模型仍看到工具并可发调用，只是在需要时等待用户决定。

用户批准后调用可以执行。

## 82. 常见误读：is_read_only 返回 true 就是安全保证

不对。一个 predicate 只有被执行路径消费，才能改变系统行为。

当前它是有价值的设计信号和测试断言，但不是 Ask 的 runtime gate。

## 83. 常见误读：ReadOnly Capability 与 Ask 自动绑定

不对。Capability 由 Session/Subagent 创建配置决定，不读取 PromptMode。

## 84. 常见误读：Plan 与 Ask 的只读强度相同

不对。Plan 有独立 edit gate，Ask 当前没有。Plan 还允许唯一 plan file 例外并要求退出审批。

## 85. 调试：Ask Turn 为什么出现 Tool Call

先确认预期：当前实现允许这种情况进入正常流水线。

再检查：

1. trace 的 `start_prompt_mode` 是否为 ask；
2. sampling 请求是否仍包含工具 definitions；
3. PermissionMode 是 Ask、Auto 还是 AlwaysApprove；
4. 调用最终是否被权限拒绝；
5. 是否来自 hosted/MCP tool。

## 86. 调试：明明选择 Ask，为什么变成 Agent

检查：

- `_meta.mode` 大小写是否严格为 `ask`；
- 是否发送了未知字符串；
- 入队 InputItem 保存的值；
- 是否有 synthetic Prompt 显式使用 Agent；
- Turn 中是否执行了 ExitPlan/模式通知更新 `turn_prompt_mode`。

## 87. 调试：Ask Prompt 退出了 Plan

这是当前预期行为。`reconcile_plan_mode_with_prompt()` 把 Ask 与 Agent 都作为非 Plan 模式，调用 `user_exit(false)`。

如果希望“在 Plan 内问一个问题后继续 Plan”，应让 Prompt 保持 Plan mode，并使用 AskUser 工具或普通对话，而不是切换到 PromptMode::Ask。

## 88. 调试：Permission UI 上的 Ask 与 trace 不一致

检查的不是同一个字段：

- Turn trace：`start_prompt_mode/end_prompt_mode`；
- Permission event：`permission_mode`、`user_prompted`、`decision_reason`。

两者可以合法地分别是 `agent` 与 `ask`。

## 89. 推荐的验证矩阵

| Prompt | Permission | 预期观察 |
| --- | --- | --- |
| Agent | Ask | 工具可见，敏感调用可能询问 |
| Ask | Ask | 当前工具仍可见；敏感调用可能询问 |
| Ask | AlwaysApprove | 当前 Ask 本身不拦写，暴露缺口最明显 |
| Plan | AlwaysApprove | 非 plan edit 仍被 Plan gate 拒绝 |
| Agent child | ReadOnly capability | 写/执行工具从 child toolset 消失 |

## 90. 推荐的测试补强

若要把 Ask 变成强保证，至少增加：

1. Ask sampling definitions 不含 edit/execute；
2. 幻觉 edit call 被 preflight gate 拒绝；
3. AlwaysApprove 不能绕过 Ask gate；
4. MCP kind=None fail closed；
5. hosted tools 遵守 Ask allowlist；
6. queued Ask 在 Session mode 改变后仍保持 Ask；
7. Agent/Plan/Ask 连续转换不会污染 Tracker；
8. direct Bash/slash 行为有明确测试。

## 91. 设计建议：给 soft 与 hard 语义不同名字

如果产品只需要 telemetry/回答风格，可把它明确叫 `PromptIntent::Ask`。

如果产品承诺不可修改，应引入 `TurnCapability::ReadOnly` 或明确 Ask enforcement，避免一个 enum 同时承担“意图”和“权限”的模糊期待。

## 92. 设计建议：单一工具允许矩阵

Ask filter、Ask gate 与 CapabilityMode 应共享一张按 ToolKind 的允许矩阵，或共享明确的 policy helper。

否则容易出现：模型看不到某工具，但幻觉调用却能执行；或 Toolset 允许而 Gate 拒绝的漂移。

## 93. 设计建议：未知工具 fail closed

强只读模式下，`kind=None`、MCP `Other`、未识别 wire name 都应默认拒绝，除非有可信 read-only metadata。

这与普通 Agent 为兼容性保留 opaque 工具的策略可以不同。

## 94. 设计建议：把 mode 放进 PreparedToolCall 审计

在 preflight 时捕获 `turn_prompt_mode`，并写入 tool telemetry，可让后续回答：

- Ask Turn 是否产生写请求；
- 哪些工具最常违反 Ask 意图；
- Gate 是否正确拦截；
- 模式是否在 Tool Call 前发生变化。

## 95. 设计建议：明确模式切换边界

若同一 Turn 中工具可从 Agent 切 Ask/Plan，应定义已准备但未 dispatch 的调用使用哪个模式：模型发出时、prepare 时还是 dispatch 时。

Plan 使用 batch barrier 处理语义变化；Ask 若加入动态工具，也需要相似并发规则。

## 96. Glossary：Prompt 与 Session

### Prompt

一次提交给模型的输入及其 metadata、附件和结构化输出要求。

### Turn

从一条 Prompt 开始，经历一个或多个模型采样与 Tool Call round，最终产生结束结果的执行单位。

### Prompt Mode

描述某条 Prompt/Turn 预期语义的内部枚举：Agent、Ask、Plan。

### Session Mode

客户端通过 ACP 设置的 Session 级默认模式：Default、Ask、Plan。

### Prompt Metadata

随请求携带、但不属于正文的结构化字段，例如 mode、promptId、trace context。

### InputItem

Session prompt queue 中的完整队列项，冻结 Prompt 内容、模式、来源和响应通道。

### Promotion

Scheduler 把队列头从等待状态提升为正在运行 Turn 的过程。

### Synthetic Prompt

系统而非用户直接输入创建的 Prompt，例如恢复审批、Goal 续跑或后台任务唤醒。

## 97. Glossary：权限

### Permission Mode

决定工具访问如何获得批准的策略模式：Ask、Auto、AlwaysApprove。

### Permission Ask

遇到未自动允许的访问时向用户请求决定，不代表模型只能问答。

### AccessKind

一次 Tool Call 的具体访问类别和目标，例如 Edit(path) 或 Bash(command)。

### Decision

PermissionManager 对请求给出的结果，如 Allow、Reject、PolicyDeny 或 Cancelled。

### Policy

预先配置的允许、询问和拒绝规则，可按工具、命令或路径匹配。

### Grant

用户批准后保存的一次性、Session 级或持久授权，后续匹配调用可复用。

### YOLO / AlwaysApprove

尽量跳过交互确认的权限模式；仍不能绕过更外层的硬安全门禁。

### Auto Mode

通过分类器和启发式自动判断工具风险的权限模式，不等同于 AlwaysApprove。

## 98. Glossary：能力与安全

### Capability Mode

Session 创建时用于限制可用 ToolKind 集合的能力配置。

### ReadOnly Capability

保留读取、搜索、检查和部分元工具，移除编辑、Shell 与任务执行工具的 capability mode。

### ToolKind

工具的稳定语义分类，供工具过滤、权限与批次策略使用。

### Tool Definition

发送给模型的工具名称、描述和输入 schema。模型根据它决定如何调用。

### Tool Filtering

在采样前从 Tool Definition 集合中移除不允许的工具。

### Runtime Gate

在真实执行前再次验证调用是否合法的硬边界，可防止幻觉调用和绕过过滤。

### Soft Intent

表达期望行为但不强制阻止违规动作的标签。当前 Ask Prompt 更接近此概念。

### Hard Guarantee

所有执行路径都无法绕过的不变量，通常需要过滤、门禁和测试共同支撑。

### Fail Open / Fail Closed

遇到未知情况时分别选择继续放行或保持限制。Prompt unknown 回退 Agent；Permission unknown 回退 Ask。

## 99. Glossary：可观测性与代码阅读

### TurnDeltaSnapshot

记录一次 Turn 的模式、模型、工具和变更等摘要，用于 trace/feedback。

### Telemetry Dimension

用于聚合分析的分类字段，例如 start_prompt_mode 或 permission_mode。

### Production Call Site

实际运行路径中调用某函数的位置；只有定义而无生产调用的方法不会改变运行行为。

### Predicate

返回真假分类的函数，例如 `PromptMode::is_read_only()`。

### Design Intent

注释、类型名或 API 形状表达的目标行为，可能尚未完整落地。

### Enforcement

执行路径中真正拒绝或移除不允许行为的机制。

### Orthogonal（正交）

两个维度可独立组合。PromptMode 与 PermissionMode 就是正交状态。

### Partial Order（偏序）

某些元素可比较、另一些不可比较的集合关系；ReadWrite 与 Execute capability 互不包含。

## 100. 一页复习版

```text
PromptMode::Ask
  = 一条 Prompt/Turn 的问答意图标签
  = 可排队、可继承、可记录 telemetry
  = 会退出 Plan Mode
  != 当前已强制的 no-tool runtime gate

PermissionMode::Ask
  = 工具权限策略
  = 敏感调用可能询问用户
  = 用户批准后仍可执行
  != 问答模式

CapabilityMode::ReadOnly
  = Session/Subagent 工具集能力
  = 构造时移除 edit/execute/task 工具
  = 真正缩小模型可见与可 dispatch 的能力面
  != 自动由 Ask Prompt 启用

当前关键缺口：
  PromptMode::is_read_only() 有定义和测试，
  但 Ask 没有连接 tool filtering / prepare_tool_call gate。
```

## 101. 源码证据索引

| 结论 | 直接证据 |
| --- | --- |
| PromptMode 三值与解析 | `session/plan_mode.rs` 的 `PromptMode` |
| Ask metadata 优先 | `mvp_agent/acp_agent.rs` 的 prompt 入口 |
| 无 metadata 读取 Session 当前值 | `GetCurrentPromptMode` command |
| mode 随 Prompt 排队 | `queue_input()` / `InputItem.prompt_mode` |
| Turn start/end 两份记录 | `handle_prompt()` |
| Ask 退出 Plan | `reconcile_plan_mode_with_prompt()` |
| Ask 未参与工具定义过滤 | `prepare_tool_definitions_inner()` |
| is_read_only 无生产消费 | Shell 引用搜索 + 单元测试 |
| Permission unknown 回退 Ask | `parse_permission_mode_canonical()` |
| Permission 针对 AccessKind | `permission/types.rs` |
| ReadOnly 真正按 ToolKind 过滤 | `capability.rs` 的 `filter/kind_allowed` |
| Capability 防止 child widening | `CapabilityMode::is_subset_of()` + fork gate |
| Pager Plan 关闭回 Default | `dispatch/modes.rs::set_plan_mode` |

## 102. 阅读完成后应该能回答的问题

1. `_meta.mode = "ask"` 如何一路进入 InputItem 和 Turn snapshot？
2. 为什么 SessionMode::Default 对应 PromptMode::Agent？
3. Ask Prompt 为什么会退出 file-backed Plan Mode？
4. 当前 `PromptMode::is_read_only()` 为什么不是安全保证？
5. Prompt Ask 与 Permission Ask 分别控制什么？
6. 为什么 Ask + AlwaysApprove 是重要测试组合？
7. CapabilityMode::ReadOnly 实际移除了哪些工具？
8. ReadWrite 与 Execute 为什么互不可比较？
9. 要实现强 Ask Mode，为什么既需过滤又需 runtime gate？
10. Direct Bash、MCP 与 hosted tools 为什么必须单独审计？

能回答这些问题，就掌握了本篇最重要的代码阅读方法：不要根据名字推断安全属性，要沿着状态的生产者、存储位置、消费者和最终拒绝点逐层验证。
