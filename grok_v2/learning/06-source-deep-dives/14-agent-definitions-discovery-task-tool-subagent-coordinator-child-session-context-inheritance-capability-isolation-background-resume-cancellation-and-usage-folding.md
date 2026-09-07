# 源码精读 14：Subagent 如何发现、启动、继承、隔离、回传与收敛

> 本篇继续精读 Agent Runtime 的基础设施：Agent Definition 如何从 Built-in、Project、User、Bundled 与 Plugin 中发现并解决重名；`task` Tool 如何验证 Type、Depth、Model、CWD、Capability 和 Isolation；Shared Coordinator Actor 如何维护 Pending、Active、Completed、Waiter、Deadline 与 Cancellation；Shell 又如何真正创建一个拥有独立 Prompt、Chat State、Sampler、Persistence、Tool Registry 与 Context Window 的 Child Session。最后追踪 Fresh、Fork、Resume 三种上下文启动方式，以及 Background Completion、Usage Folding、Worktree Snapshot 和 Session Teardown 如何回到 Parent。
>
> 源码基线：`ed6d543`。源码变化后优先按本文列出的类型、函数和测试名重新定位。

---

## 1. 先给结论：Subagent 不是“在 Tool 里再调用一次模型”

从 Parent Agent 看，Subagent 由一次 `task` Tool Call 启动。

从 Runtime 看，它是一条完整嵌套 Session：

```text
Parent Agentic Loop
  -> task Tool
  -> SubagentBackend
  -> Coordinator Actor
  -> Shell ChildRunner
  -> Child Session
       -> Child System Prompt
       -> Child Chat State
       -> Child Sampler
       -> Child Tool Registry
       -> Child Persistence
       -> Child Agentic Loop
  -> SubagentResult
  -> Parent Tool Result / Completion Reminder
```

因此 Child 不只是“一个并行函数”，而是拥有独立会话状态、模型请求、工具循环、取消边界和持久化目录的 Agent Runtime。

---

## 2. 本篇主线文件

| 文件 | 责任 |
| --- | --- |
| `xai-grok-agent/src/config.rs` | `AgentDefinition`、Built-in Agent、Tool/Permission/Memory/MCP 等声明 |
| `xai-grok-agent/src/discovery.rs` | Project/User/Bundled/Plugin Agent 发现、优先级与重名处理 |
| `xai-grok-agent/src/prompt/subagent_prompts.rs` | Built-in General-purpose、Explore、Plan Prompt |
| `xai-tool-types/src/task.rs` | 模型侧 `task` 输入、Capability/Isolation Enum 与完成输出 |
| `xai-grok-tools/.../task/mod.rs` | `TaskTool` 参数清洗、前置验证、Foreground/Background 返回 |
| `xai-grok-tools/.../task/backend.rs` | Tool 与 Coordinator 之间的 Backend Trait/Channel Adapter |
| `xai-grok-tools/.../task/types.rs` | Request、Result、Snapshot、Cancel、Event 与 Resource 类型 |
| `xai-grok-tools/.../task/coordinator.rs` | Single-writer 生命周期 Actor |
| `xai-grok-tools/.../task/coordinator_state.rs` | Pending/Active/Completed State 与 Deadline Helper |
| `xai-grok-tools/.../task/coordinator/query.rs` | Poll、Blocking Wait、Inspect 与 Live Progress |
| `xai-grok-subagent-resolution/src/definition.rs` | Definition 解析、Gate、Tool Policy 与 Prompt Context |
| `xai-grok-subagent-resolution/src/overrides.rs` | Role/Persona/Spawn Override 合并与 Capability 交集 |
| `xai-grok-subagent-resolution/src/context.rs` | Forked Parent Context 的清洗、摘要与格式化 |
| `xai-grok-subagent-resolution/src/resume.rs` | Resume Type/Persona Identity 校验 |
| `xai-grok-shell/src/agent/subagent/mod.rs` | Shell Spawn Context 与 Child Runtime Adapter 类型 |
| `xai-grok-shell/src/agent/subagent/handle_request.rs` | Worktree、Model、Prompt、Session Spawn、等待与清理主线 |

---

## 3. 先区分五个容易混淆的概念

### 3.1 Agent Definition

描述一个 Agent 的静态声明：Name、Description、Prompt、Toolset、Model Hint、Permission、Skills、MCP、Memory 等。

### 3.2 Subagent Type

`task.subagent_type` 选择的 Agent Definition Name，例如 `explore`。

### 3.3 Role

运行期配置中的角色预设，可提供默认 Capability、Model、Reasoning、Prompt File 与 Isolation。

### 3.4 Persona

对语气、行为和输入/输出契约的命名层，可以提供 Instruction、Model、Reasoning 与 Isolation Default。

### 3.5 Child Session

某次具体 Spawn 产生的运行实例，有 UUID、CWD、Conversation、Chat State、Tools 和生命周期。

Definition/Role/Persona 是配置层，Child Session 是实例层。

---

## 4. Built-in Subagent 只有三个被默认公告

`BuiltinAgentName` 包含多种可作为顶层 Profile 的 Agent，但 `subagent_variants()` 只返回：

- `general-purpose`
- `explore`
- `plan`

这三个进入默认 `task` Description。

其他 Built-in Name 即使能由内部代码解析，也不等于自动作为模型可选 Subagent Type 公告。

---

## 5. Built-in Type 的基本意图

### 5.1 `general-purpose`

适合多步骤研究和实现，Toolset 较完整。

### 5.2 `explore`

面向快速只读代码探索；只读不只是 Prompt 建议，Toolset/Capability 也会限制写入。

### 5.3 `plan`

面向只读架构分析与实施计划，Permission Mode 也是 Plan-oriented。

选择 Type 的本质不是给同一模型换个标签，而是选择 Prompt、Tool Policy 和默认 Runtime Behavior 的组合。

---

## 6. `AgentDefinition` 为什么是可版本控制契约

源码称它为 Portable Agent Identity，可以来自 `.grok/agents/*.md`。

它刻意不包含所有 Session-level Policy，例如实际 Compaction Runtime、System Reminder State。这些由 Shell 和 Agent Builder 在 Spawn 时注入。

```text
AgentDefinition + Session Context + Runtime Overrides
                -> Effective Child Agent
```

---

## 7. Agent Markdown 文件的结构

```markdown
---
name: reviewer
description: Reviews changes
tools: ...
model: ...
---

You are a reviewer...
```

YAML Frontmatter 解析成 `AgentDefinition` 字段，Markdown Body 保存为 `prompt_body`。

`PromptMode::Extend` 会扩展 Base Template；`PromptMode::Full` 把 Body 当成完整 System Prompt。

---

## 8. Agent Definition 的关键字段

| 分组 | 字段示例 |
| --- | --- |
| 身份 | `name`、`description`、`color` |
| Prompt | `prompt_mode`、`prompt_body`、`initial_prompt` |
| Tool | `tool_config`、`tools`、`disallowed_tools`、`inject_default_tools` |
| 执行 | `model`、`effort`、`max_turns`、`background`、`isolation` |
| 安全 | `permission_mode`、`capability_mode` |
| 上下文 | `agents_md`、`skills`、`discover_skills`、`inherit_skills` |
| 扩展 | `mcp_servers`、`mcp_inheritance`、`hooks`、`memory` |
| 完成协议 | `completion_requirement` |

不是每个字段都原样进入 Prompt；很多字段控制 Spawn Wiring。

---

## 9. Agent 发现从哪里开始

Project-level 发现会从当前 CWD 一直向上走到 Git Worktree Root，并检查：

```text
.grok/agents/
.claude/agents/
```

这允许子目录拥有更局部的 Agent Definition，也兼容 Claude-style 目录。

---

## 10. 完整发现来源顺序

普通发现的优先来源是：

1. 当前 CWD 到 Repo Root 的 Project Agent Directory。
2. Grok Home 的 `agents/`。
3. Legacy `~/.grok/agents/`，当 Grok Home 已迁移时。
4. `~/.claude/agents/`。
5. Grok Home/Legacy Root 的 `bundled/agents/`。

按 Name Dedup，先发现者保留。

---

## 11. Runtime Lookup 的关键优先级

按名称 Spawn 时，Native Resolution 是：

```text
Project > Built-in > User > Bundled
```

一个 Project `explore.md` 可以 Shadow Built-in `explore`；User-level 同名文件不能。

这条规则允许项目做明确局部定制，同时防止用户缓存悄悄改变常见 Built-in Type。

---

## 12. 为什么“可见列表”必须和“可调用结果”一致

如果 Tool Description 展示 User-level `explore`，Runtime Lookup 却调用 Built-in `explore`，模型看到的能力说明就是假的。

`merge_subagents` 按与 `by_name_in_cwd` 一致的 Shadow Rule 构建可见列表。

源码把这个不变量写成：

```text
visible == callable
```

---

## 13. Toggle Gate

`[subagents.toggle]` 可以按 Name 启用或禁用 Type，没有条目默认启用。

Toggle 同时作用于 Roster 和 Spawn Validation，避免“Description 说可用，调用才发现禁用”。

---

## 14. Parent Allowlist 是第二道 Type Gate

Parent Agent 可派生 `allowed_subagent_types`：

- `None`：不限制 Type。
- `Some([a,b])`：只允许列出的 Type。
- `Some([])`：禁止 Spawn。

Validation 会区分 Unknown、Disabled 和 Not Allowed，让模型采取不同恢复策略。

---

## 15. Plugin Agent 如何命名

Plugin Agent 在 Roster 中使用：

```text
plugin-name:agent-name
```

Bare Name 只有在恰好一个 Enabled Plugin 提供该 Agent 时才解析；多个 Plugin 同名会拒绝并要求 Qualified Name。

---

## 16. 未信任 Plugin 为什么只解析 Frontmatter

Untrusted Plugin 的 Agent File 只读取 Frontmatter，不把 Markdown Prompt Body 送给模型。

否则仅仅“发现 Agent”就可能把未获信任的指令带进 Prompt。Trust 不只限制 Tool，也限制 Instruction Supply Chain。

---

## 17. `task` Description 不是固定字符串

Agent Builder 根据当前 Live Roster 构建 Tool Description，包括：

- Type Name。
- Description。
- Tool Capability 摘要。
- Background、Resume、Isolation 使用说明。

所以模型看到的是本次 Session 真正可用的 Type。

---

## 18. Tool Name 可以被 Profile 重命名

某些 Profile 将：

```text
task -> spawn_subagent
run_in_background -> background
get_task_output -> get_command_or_subagent_output
```

Description Template 使用 Tool Kind 和 Parameter Mapping 渲染，不能假定内部 Rust ID 等于模型可见名字。

---

## 19. `TaskTool` 的依赖要求

Registry 要求 `TaskTool` 同时存在：

- Background Task Output 能力。
- Kill Task 能力。

既然 Child 可后台运行，模型必须有查询和终止它的方式。只暴露 Spawn 会形成不可管理任务。

---

## 20. `task` 为什么是 Write Scope

即使 Spawn `explore`，`task` 本身也会创建 Child Session State、可能创建 Worktree，并可能运行有写权限的工具。

因此 `TaskTool` 不是 Read-only Tool，Tool Scope 是 Write。

---

## 21. `TaskToolInput` 的核心参数

| 参数 | 含义 |
| --- | --- |
| `prompt` | Child 的完整任务描述 |
| `description` | 3–5 词短标签 |
| `subagent_type` | Agent Name，默认 `general-purpose` |
| `run_in_background` | 是否立即返回 Handle，默认 `true` |
| `capability_mode` | Read-only/Read-write/Execute/All |
| `isolation` | Shared Workspace 或 Worktree |
| `resume_from` | 从已完成 Peer Child 继续 |
| `cwd` | Child 显式工作目录 |
| `model` | 显式 Model Slug |

`task_id` 是 Server-injected 字段，不向模型暴露。

---

## 22. Background 的真实默认值

`run_in_background` 的 Serde Default 是 `true`。

模型省略参数时，Tool 立即返回 Subagent ID，而不是阻塞 Parent Turn。

个别旧注释若写“blocking mode default”，应以 Default Function 与 Schema Description 为准。

---

## 23. 参数清洗为什么处理 Sentinel String

模型有时为 Optional String 生成：

```text
""
"null"
"none"
"undefined"
```

`sanitize_optional_arg` 将它们视为 `None`，并 Trim 普通值。

---

## 24. CWD 清洗还处理哪些情况

`sanitize_cwd_value` 会：

- Trim Whitespace。
- 去掉外围引号或 Backtick。
- 展开 Leading `~`。
- 排除 Sentinel。

Tool 层先清洗，Shell ChildRunner 再做 Defense-in-depth 验证。

---

## 25. CWD 与 Worktree 为什么互斥

二者都决定 Effective CWD：

- `cwd` 指向现有目录。
- `isolation=worktree` 创建隔离目录。

同时提供真实目录时返回 Invalid Arguments。

若 CWD 不存在且同时请求 Worktree，源码视为模型噪声并清掉 CWD，让 Worktree 胜出。

---

## 26. Resume 时为什么忽略 CWD

Resume 要继承 Source Child 的 CWD 或 Worktree State。

若新请求再指定 CWD，会让 Transcript 中的旧路径和当前文件环境不一致，所以 Source CWD 优先。

---

## 27. Resume 时为什么忽略 Model

Resume Contract 要求 Pin Source Model。模型即使传了新 `model`，Tool 会软忽略。

若 Source Model 已从 Catalog 消失，Resume 失败，而不是悄悄换模型。

---

## 28. Depth Gate 在 Tool 层先执行

Resources 保存：

- `SubagentDepthCounter`
- `MaxSubagentDepth`

若 `current_depth >= max_depth`，直接返回 Invalid Arguments，不发送 Spawn。

默认 Max Depth 是 1。

---

## 29. 为什么 Child Toolset 还要再 Strip `task`

只靠运行时 Depth Error，模型仍会看到并尝试调用 `task`。

若 `child_depth >= max_depth`，`apply_child_tool_policy` 会从 Child Tool Config 移除 Task Tool。

这是 Schema-level Gate 与 Runtime Gate 双保险。

---

## 30. Eager Type Validation 为什么必须在后台 Spawn 前

Background Path 会启动 Detached Tokio Task 后立即返回 Handle。

若 Unknown/Disabled/NotAllowed 到后台才发现，模型会先拿到一个假的有效 ID。

所以 `TaskTool` 先 Round-trip 验证 Type。

---

## 31. `ValidationUnavailable` 为什么不是 Unknown

Coordinator Channel 断开时，Type 可能合法，只是基础设施不可达。

源码返回 Custom `validation_unavailable`，避免模型误以为换 Type Name 能修复 Transport Fault。

---

## 32. Model 也在 Spawn 前验证

显式 `task.model` 需要 `TaskModelValidator`，并必须解析到 Available Model Catalog。

验证器缺失是 Infrastructure Error；未知 Model 是 Invalid Argument。

---

## 33. Subagent ID 为什么使用 UUID v7

未注入 `task_id` 时生成 UUID v7，同时作为：

- Coordinator Key。
- Child Session ID。
- Metadata Directory Name。
- Query/Kill/Resume Handle。

UUID v7 带时间有序性，便于日志和存储观察。

---

## 34. `SubagentRequest` 比模型输入多了什么

Tool 会扩展出：

- Parent Session ID。
- Parent Prompt ID。
- Runtime Overrides 与 Provenance。
- Surface Completion Policy。
- Await Policy。
- Fork Context Flag。
- Owner：Task 或 Workflow。
- Cancellation Token。

这些是宿主控制面，不由模型伪造。

---

## 35. Parent Prompt ID 为什么重要

它支持：

- 只取消当前 Turn 启动的 Child。
- 将 Usage 归入正确 Prompt。
- 记录该 Prompt Spawn 的 Child。
- 区分以前 Turn 的 Background Child。

Session ID 提供 Ownership，Prompt ID 提供 Turn Scope。

---

## 36. `SubagentBackend` 是 IoC 边界

Tool 只依赖 Spawn、Query、Cancel、Validate 和 Describe 等能力，不知道 Shell 怎样创建 Session、Worktree 或 Sampler。

所有 Host 使用 Channel Backend 和共享 Coordinator State Machine，只替换 `ChildRunner`。

---

## 37. 为什么 Backend 用 Channel 而不是共享 Mutex Map

生命周期包含 Promotion、Deadline、Waiter、Completion Buffer、Cancel Race、Resume 和 Teardown。

若多个 Tool 直接锁 Map 修改，很难保证 Transition 原子性。Single-writer Actor 让所有变化经过一个 Mailbox。

---

## 38. Coordinator 的三张核心表

```text
pending:   已接受 Spawn，Child Runtime 尚未完成 Promotion
active:    Child Session 已创建，可查询 Progress/Cancel
completed: 已终止，可 Poll/Resume
```

Transition：

```text
Spawn -> Pending -> Active -> Completed
             \--------------> Completed
```

Pending 对外显示 `Initializing`。

---

## 39. Coordinator 还维护哪些状态

- Completed Order Queue。
- Blocking Waiters。
- Workflow Cancel Waiters。
- Spawn-blocked Sessions。
- Usage-not-applied Prompt Set。
- Buffered Completions。
- Running/Validation/Description/Progress Futures。

它既是 Registry，也是 Lifecycle Scheduler。

---

## 40. Actor Loop 为什么使用 Biased Select

Loop 优先处理 Internal Promotion/Resume、Child Completion、Validation/Progress，再处理外部 Command 与 Deadline。

Internal Transition 优先可缩短“Child 已启动但仍显示 Pending”的窗口，并减少完成与查询交错的不确定性。

---

## 41. `FuturesUnordered` 的作用

多个 Child 并发运行，任意 Child 完成即产生 `(subagent_id, output)`。

并发发生在执行层，Coordinator 仍串行提交 Terminal State。

---

## 42. Duplicate ID 怎样处理

Spawn 前检查 ID 是否存在于 Pending、Active 或 Completed。

若存在直接 Failure。ID 同时是 Session 与 Resume Handle，不能复用。

---

## 43. Pending 到 Active 的 Promotion Handshake

Shell 创建 Child Session 后，通过 `ChildReporter::started` 发送 Session ID、Control Handle、Persona、CWD、Worktree 与 Model Metadata。

Coordinator 返回 Bool Ack。

若 Pending 已取消或移除，Ack 为 `false`，Shell 拆掉半初始化 Child。

---

## 44. Promotion Ack 关闭哪种 Race

```text
Spawn accepted
  -> Worktree/Session creating
  -> User cancels Pending child
  -> Shell finishes creating Session
```

没有 Ack，Child 会在 Cancel 后进入 Active，成为 Orphan。Ack 让 Coordinator 当前状态成为最终判定。

---

## 45. `ChildRunner` 为什么有 Associated Future Type

不同 Host 可能需要 Multi-thread `Send` Future 或 Local Non-`Send` Future。

Trait 不无条件要求 Future 为 `Send`，Coordinator Future 自然继承 Runner 的执行约束。

---

## 46. Nested Child 为什么 Reparent 到 Root Session

Child Spawn Grandchild 时，Coordinator 将 Request Parent ID 改成 Root Parent。

收益：

- Root Teardown 能找到整棵树。
- Process Scope 统一。
- Intermediate Child 结束不会让 Grandchild 失去 Ownership。

执行关系仍嵌套，生命周期所有权被扁平化。

---

## 47. Nested Completion 为什么默认不直接 Surface

Reparent 时 `surface_completion=false`。

Grandchild Result 应先由直接 Parent Child 消费；若直接提醒 Root，会产生重复和越层。

---

## 48. Workflow Lineage 为什么保留

Workflow-owned Child 的后代继承 Workflow Run ID。

这样 Parent Stop 不误杀 Workflow Work，按 Workflow ID Cancel 又能覆盖后代。

---

## 49. Parent Stop 后为什么封锁新 Spawn

`ParentSession` Cancel 会把 Session ID 加入 `spawn_blocked_sessions`。

否则晚到的 Detached `TaskTool` Future 可能在 Stop 后才发送 Spawn，逃过取消。下一 Turn 再 `OpenSpawnAdmission`。

---

## 50. Foreground 与 Background 是 Delivery Policy

二者由同一 Coordinator 和 ChildRunner 执行。

- Background：立即拿 ID，之后 Poll/Reminder。
- Foreground：先等待 Spawn Reply。

Child Prompt、Tools、Sampler、Persistence 都是完整版本。

---

## 51. Background Spawn 为什么用 Detached Tokio Task

Tool 要立即返回 Handle，但 Backend `spawn()` Future 要等 Coordinator 最终 Reply。

因此 Tool 在 Tokio Task 中 Await Backend，当前 Tool Call先返回 Started Notice。

---

## 52. Background 不等于 Fire-and-forget

Request 的 `surface_completion=true`。

完成后可进入 Reminder、Auto-wake、Poll，并可被 Kill。Background 只是 Parent 不同步等待。

---

## 53. Foreground Budget 为什么默认 45 秒

同步等待长 Child 会让 Parent 看起来冻结。

非 Background 且非 `await_to_completion` 的 Child 默认 45 秒后自动改成 Background。

超时不是取消，也不会重启 Child。

---

## 54. Auto-background 的结果字段

`SubagentResult.backgrounded=true` 表示 Child 仍在运行，当前 Reply 只负责返回 Poll Handle。

它不是 Completed、Failed 或 Cancelled。

---

## 55. Definition-level `background` 又是什么

Agent Definition 可声明 `background: true`。

即使 Spawn Caller Block Await，它在 Outstanding Accounting 中仍被视为 Background，不冻结 Parent 对 Foreground Child 的等待。

---

## 56. Caller Gone 为什么自动 Background

Foreground Spawn 的 Reply Receiver 可能被 Drop。

Coordinator 检测后不立即杀 Child，而是改成 Background Handle，避免已经进行的工作丢失。显式 Cancel 走另一条路径。

---

## 57. Completion Disposition 记录什么

- 是否已交付 Foreground Reply。
- 是否 Backgrounded。
- 是否交付给 Waiter。
- 是否显式 Kill。
- 是否仍应 Surface。

同一个结果据此选择消费路径，避免三次重复出现。

---

## 58. Completed Registry 为什么有上限

最多保留 1024 个 Completed Entry，超出移除最老项。

大 Output 可持久化到磁盘，Registry 只保留 Reference。

---

## 59. Buffered Completion 为什么也有限制

未 Drain 的 Reminder 最多 256 条，超出丢最老。

异常卸载而没 Teardown 的 Session 不应让共享 Coordinator 无限增长。

---

## 60. `Arc<str>` 为什么用于 Output

Output 会被 Result、Snapshot、Completion Summary 多处引用。

`Arc<str>` Clone 只增加引用计数；到 Serde-visible 输出边界才分配 `String`。

---

## 61. Query 为什么按 Parent Session 限定

Coordinator 只返回属于该 Parent Session 的 Child，防止跨 Session 读取或取消。

Workflow-owned Child 还对普通 Task Query 隐藏。

---

## 62. Snapshot 的五种状态

```text
Initializing
Running
Completed
Failed
Cancelled
```

Running 还包含 Turn、Tool Calls、Token、Context Usage、Tools Used 和 Error Count。

---

## 63. Blocking Query 怎样工作

`block=true` 时，Pending/Active Child 注册 `BlockingWaiter`，默认 Timeout 30 秒。

Child 完成时所有 Waiter 获得 Terminal Snapshot；Timeout 不取消 Child。

---

## 64. 为什么 Progress Query 不阻塞 Actor

`control.progress()` 被放进 Progress `FuturesUnordered`，而不是在 Command Handler 中 Await。

若返回时 Child 已完成，则改取 Terminal Snapshot。

---

## 65. Cancellation 的四种 Target

- `SubagentId`：一个 Child。
- `ParentPromptId`：某 Turn Spawn 的 Child。
- `ParentSession`：Stop/Esc 下的非 Workflow Child。
- `WorkflowRunId`：一组 Workflow Child。

它们反映不同所有权层级。

---

## 66. Pending 和 Active 的取消方式为何不同

Pending 尚无 Child Control，只能 Cancel Token。

Active 同时 Cancel Token 并调用 `ChildControl::cancel()`，让 I/O 和 Actor 两侧都观察到取消。

---

## 67. Explicit Kill 为什么单独记录

显式 Kill 的 Child 完成后不应再自动 Surface 普通 Completion Reminder。

`explicitly_killed` 参与 `should_surface` 判定，和 Caller Gone 自动转后台区分。

---

## 68. Parent Teardown 做什么

- 清除该 Parent 的 Pending Completion。
- 关闭 Child `surface_completion`。
- 取消 Pending/Active Child。
- 移除 Spawn Block State。

恢复同一 Session ID 时不会收到旧实例遗留提醒。

---

## 69. Coordinator Drop 是最后一道清理

Actor 退出后 `Drop` 再 `cancel_all_children`，尽量不留下活跃 Child。

---

## 70. Shell `SubagentSpawnContext` 包含什么

- Parent Model/Auth/Catalog。
- CWD/Session ID/Depth。
- Shared FS、Terminal、Hunk Tracker、Process Scope。
- Memory、Skills、MCP、Plugin、Hooks。
- Permission 与 Managed Policy。
- Web/Image/Video/Deploy Config。
- Upload/Worktree/Persistence Settings。
- Parent Chat State 与 Usage Channel。

它避免 Coordinator 依赖整个 `MvpAgent`。

---

## 71. 共享 FS 不等于共享 Chat State

Isolation None 时 Parent/Child 使用同一工作目录，所以文件修改立即可见。

但 Child 有独立 Conversation、Chat State Actor、Token Counter、Sampler Turn 和 Persistence Directory。

---

## 72. Hunk Tracker 为什么共享

Child 编辑需要出现在 Parent Change Tracking 和最终归因中。

Parent Hunk Tracker Handle 被 Clone 给 Child，背后是同一个 Actor Channel。

---

## 73. Terminal Backend 为什么共享

Child 启动的 Background Command、Monitor 和 Scheduled Work 可能比 Child 活得更久。

Child 退出时把存活任务的 Notification Handle Reparent 给 Parent，避免事件发往已关闭 Bridge。

---

## 74. Worktree Isolation 的创建语义

`isolation=worktree` 时：

1. 解析 Source Repository。
2. 为 Subagent ID 选择目录。
3. 用 Fast Worktree Builder。
4. Preserve Working Tree State。
5. Child CWD 指向 Worktree。

Parent Workspace 不直接看到 Child Edit。

---

## 75. Worktree 创建失败为什么降级 Shared Workspace

创建失败或 Blocking Task Panic 时，源码 Warning 后继续 Shared Workspace。

这是一项 Availability-first 取舍。请求隔离不等于隔离一定成功，安全敏感场景应额外检查结果。

---

## 76. Fresh Worktree 为什么要标记

`worktree_freshly_created` 区分本次新建和 Resume 复用。

Promotion 被取消时，只有本次新建半成品适合自动清理；Source Worktree 不应误删。

---

## 77. Child Model Resolution 的来源

可能来自：

1. Explicit Spawn Override。
2. Role Default。
3. Persona Default。
4. Agent Definition。
5. `[subagents.models]`。
6. Parent Model。

最终必须映射到 Available Model Entry 和 Sampling Config。

---

## 78. 未知有效 Model 为什么退回 Parent

普通 Spawn 若解析 Model 不在 Catalog，Warning 后读取 Parent Live Sampling Config。

Resume 是例外：Source Model 不可用必须失败。

---

## 79. 为什么读取 Parent Live Chat State

Parent 可能已切换 Model 或更新 Auth。Spawn Context 初始快照可能过时。

继承路径读取 Parent Chat State 当前值，避免 Child 使用启动时旧模型。

---

## 80. Reasoning Effort 何时生效

只有 Effective Model 支持 Reasoning Effort 才 Parse 并写入 Sampling Config。

Parse 失败 Warning 并忽略，不阻止 Spawn。

---

## 81. Capability Mode 不是简单覆盖

Spawn、Role 和 Definition Ceiling 会取交集：

```text
All ∩ X               = X
ReadOnly ∩ X          = ReadOnly
ReadWrite ∩ Execute   = ReadOnly
ReadWrite ∩ ReadWrite = ReadWrite
```

请求 `All` 不能提升 Definition 限制。

---

## 82. ReadWrite 与 Execute 的交集为何是 ReadOnly

ReadWrite 允许文件修改但不保证执行；Execute 允许命令但不保证写入。

共同保证只剩读取能力。这是 Least Privilege 的 Capability Algebra。

---

## 83. Capability 怎样变成 Tool Filter

- ReadOnly：Read/List/Search/LSP/Memory/Web 等。
- ReadWrite：增加 Edit/Write/Delete/Move。
- Execute：增加 Execute，但无普通 Write/Edit。
- All：完整集合。

Tool Config 按 `ToolKind` Retain。

---

## 84. Filter 后为何清理孤儿管理工具

若删除 Bash/Task Spawn 却留下 Kill/Output Helper，模型会看到没有对象可管理的工具。

`prune_orphaned_background_task_tools` 让 Toolset 保持闭合。

---

## 85. Permission 与 Capability 的差别

Capability 决定 Child 拥有哪些 Tool Class。

Permission 决定拥有工具后，敏感操作怎样 Approval。

ReadOnly Child 即使 Permission 宽松也没有写 Tool。

---

## 86. Bypass 为什么仍受 Managed Policy

Agent Definition 请求 Bypass 时，若组织 Policy 禁用 Always-approve，Shell 会忽略并 Warning。

Plugin Agent 还有更严格限制。配置不能越过宿主安全 Ceiling。

---

## 87. Hook 为什么也有 Trust Gate

Plugin Agent 某些 Hook 不支持；Untrusted Project Folder 的 Inline Hook 被拒绝。

Hook 能执行宿主动作，风险高于普通 Prompt Body。

---

## 88. Role 与 Persona 的合并优先级

```text
Explicit Spawn Override
  > Role Default
  > Persona Default
  > Parent Inheritance / None
```

Capability 还要 Intersection，不是纯覆盖。

---

## 89. Persona File 为什么 Fail-closed

显式 Persona 的 `instructions_file` 无法读取时，Resolver 返回 Fatal Error。

否则 Child 会以名义 Persona 运行，却缺少真正规则。

---

## 90. Role Prompt File 为什么 Soft-degrade

Role Prompt File 失败只 Warning，Spawn 继续且不加入 Role Prompt。

Role 被视为增强预设，Persona 被视为更强身份协议；两者失败语义不同。

---

## 91. `max_turns` 怎样继承

Definition 可设置 Non-zero `max_turns`。

Child Final Limit 与 Parent Limit 组合，不能绕过更严格 Ceiling。达到上限会产生明确 Max-turns Result。

---

## 92. 三种 Initial Context Source

- `New`
- `Forked`
- `Resumed`

它们是不同 Bootstrap Protocol，不只是“继承多少”的三个等级。

---

## 93. 普通模型 `task` 默认是 Fresh

Model-emitted Request：

```text
fork_context = false
resume_from  = None
```

所以 Child 不自动复制 Parent Conversation，只得到新 System Prompt、项目上下文和 `task.prompt`。

---

## 94. 为什么默认不继承 Parent History

- Child Context 更小。
- 不带无关 Tool Output/Reasoning。
- Parent 必须给出清晰任务边界。
- 降低 Instruction Conflict 传播。
- 多个 Child 可拿不同最小上下文。

若 Parent 漏写关键事实，Child 不会神奇知道。

---

## 95. Fresh Child 仍继承什么

- CWD 和项目文件。
- 自己重新渲染的 User Info/Git Status。
- 允许的 `AGENTS.md`。
- Skills/MCP/Memory Policy。
- Shared FS/Terminal。
- Parent Tool Override 和配置 Ceiling。

Fresh 指不复制逐条 Conversation。

---

## 96. Fork Context 由谁使用

`fork_context` 不是模型侧参数，而是 Harness-only Runtime Field。

Goal/Orchestrator 等内部路径可要求 Child 以 Parent Conversation 为背景。

---

## 97. Fork 不是原样复制 Conversation

标准 Normalization 生成：

```text
System(placeholder)
User(<background_context>...</background_context>)
User(task prompt)
```

Parent 历史被投影成一个 Background User Item，Child System Prompt 重新构建。

---

## 98. 为什么 Task Prompt 最后追加

把真正任务放在最后提高 Recency Attention，并区分“过去发生了什么”和“现在要做什么”。

---

## 99. Fork 保留多少 Verbatim Turn

最多保留最近 3 个完整 Turn 原文。

更早 Turn 做确定性元数据摘要，不额外调用模型，因此 Fork Context 大小更可预测。

---

## 100. 什么算 Fork 中的完整 Turn

Scanner 识别：

```text
User+
Reasoning/BackendToolCall*
Assistant
(ToolResult/Reasoning/BackendToolCall)*
```

它必须理解 Reasoning Sibling，否则真实历史可能被误判为零 Turn。

---

## 101. Fork 为什么剥离 System-like Tag

包括 `system-reminder`、`user_info`、`git_status`、`project_layout`、`attached_files`。

这些由 Child Prompt Builder 按当前环境重建；复制旧版本浪费 Token且可能过时。

---

## 102. Skill Instruction 为什么从 Fork 移除

Fork 保留 Command Name/Args，但去掉 Slash Skill 的完整 Instruction Body。

那是 Parent 编排指令；Child 若继承 Skill，应通过自己的 Skill Loading Path 获取当前版本。

---

## 103. Malformed Tag 为什么保留原文

未闭合 Tag 会 Warning 并保留，而不是删除到字符串尾。

这避免格式错误把后续真实对话静默吃掉。

---

## 104. Verbatim Mirror Fork 是特殊路径

部分 Harness 需要严格镜像 Parent Prefix 和 Tool Definitions。

此路径可保留 Inherited System，并避免重复 Memory/Persona Injection。它不是普通 Subagent 默认。

---

## 105. Resume 与 Fork 的根本差异

Fork：Parent Conversation 被清洗折叠成 Background。

Resume：复制已完成 Peer Subagent 的 Raw Transcript 和 Tool State，继续原工作。

---

## 106. Resume Source 的条件

- 属于同一个 Root Parent。
- 已 Completed，不能 Active。
- Requested Type 一致。
- 显式 Persona 一致。
- Source Model 仍可用。

---

## 107. 为什么不传 Persona 可 Resume 有 Persona 的 Source

不传代表没有覆盖，因此继承 Source Persona。

只有显式传不同 Persona 才是 Identity Conflict。

---

## 108. Resume 为什么重绘 System Prompt

Resume 复制 Raw Conversation/Tool State，但 System Prompt 和 Prompt Context 使用当前 Definition 重新生成。

这样项目指令、Tool Policy 和环境可更新，同时 Transcript 连续。

---

## 109. Resume Worktree 的三种情况

1. Source Directory 仍在：复用。
2. Directory 已删但有 Snapshot Ref：Rehydrate。
3. 两者都没有：Warning 后 Shared Workspace。

---

## 110. Child Persistence 布局

Child 有自己的 Session Directory；Parent 下另存：

```text
subagents/{subagent_id}/
```

记录 Metadata、Completion、Output Reference 与 Snapshot Ref。

---

## 111. Spawn Metadata 何时写

在 Sampling Client/Session Actor 构造前先写 `status=running`，并发 `SubagentSpawned` Notification。

后续即使失败，也留下“尝试启动且失败”的记录。

---

## 112. Child 为什么重新创建 Sampling Client

Child 可使用不同 Model、Reasoning、Auth Attribution 和 Context Window。

它不能复用 Parent 正在进行的 Sampler Turn，必须按 Effective Config 创建独立 Client。

---

## 113. Child Prompt 的真正发送方式

Promotion 后，Shell 向 Child Actor 发送正常 `SessionCommand::Prompt`：

- 新 UUID v7 Prompt ID。
- Text 是 `task.prompt`。
- Agent Mode。
- `verbatim=true`。
- 可带 JSON Schema。

Child 随后进入完整 Agentic Loop。

---

## 114. 为什么先 Promotion 再发 Prompt

先执行 Prompt 会使 Cancel 无 Control Handle、Query 看不到 Progress，甚至 Child 未被 Registry 接纳就修改文件。

先 Promotion Ack，再开始执行。

---

## 115. Child Tool Override 怎样继承

Parent 当前 Tool Override Snapshot 可在 Child 首轮前通过 `SetToolOverrides` 写入 Child Actor。

临时禁用或修改不能因 Spawn Child 被绕过。

---

## 116. `AGENTS.md` 是否继承

`agents_md` 控制 Child 是否按自己的 Effective CWD 重新发现 Project Instructions。

它不是复制 Parent 已拼好的文本。

---

## 117. Skills 的两类控制

- `discover_skills`：Child 是否基于 CWD 自己发现。
- `inherit_skills`：是否继承 Parent 已发现 Set/Config。

需要继承而 Parent Snapshot 尚无时，Spawn Path 先异步发现 Parent Skills。

---

## 118. MCP Inheritance 的四种模式

- `All`
- `None`
- `Named([..])`
- `Except([..])`

Child 还可声明自己拥有的 MCP Server，所以最终 Pool 是 Inherited Policy 加 Agent-owned Servers。

---

## 119. MCP Named Ref 找不到为什么只 Warning

Agent Definition 可能跨环境使用，Parent MCP 不总存在。

单个 Missing Ref 或 Inline Parse Failure 被跳过，不让整个 Spawn 失败。

---

## 120. Memory 怎样继承或替换

无专属 Scope 时，Child Clone Parent Memory Config。

有 Agent Memory Scope 时替换 Root；Project/Local 使用 Flat Root，User 可继续按 Workspace Hash 分层。

---

## 121. Child 为什么不执行 Dream

Child 标记 `is_subagent=true`，Memory Dream 跳过。

短生命周期 Child 不应与 Parent 竞争重写共享 Workspace `MEMORY.md`。

---

## 122. Ask-user Tool 为什么由 Parent 决定

是否暴露 Ask User 继承 Parent Gate。

客户端不支持时，Definition 不能强行构造无法完成的交互。Child 通常应把问题回传 Parent。

---

## 123. Child Completion Requirement

Definition 可要求 Turn 结束前调用某 Tool，并提供 Reminder 与 Recovery Policy。

它约束 Child Agentic Loop 的 Terminal Condition，不由 Coordinator 假装完成。

---

## 124. Structured Output 怎样影响成功

Request 可带 JSON Schema。

Child Turn 正常结束但没有要求的 Structured Output，Result 仍可失败：

```text
structured output requested but none produced
```

模型无异常不等于满足协议。

---

## 125. Max-turns 与 Cancellation 为什么分开

达到 Turn Limit 会说明 Limit、Tool Calls 和 Turns。

它不是用户主动 Cancel，也不是 Sampling Crash；Parent 可据此选择 Resume 或缩小任务。

---

## 126. Shell 怎样等待 Child Turn 与 Cancel

发送 Prompt 后同时等待 Prompt Completion Oneshot 与 Cancellation Token。

Cancel 先到时读取当前 Signals Counts；Prompt 先到则按 Completion Kind、Output、Error 和 Usage 构造 Result。

---

## 127. Child Result 包含什么

```text
success / cancelled / error
output
subagent_id / child_session_id
tool_calls / turns / duration_ms
tokens_used / output_tokens / total_tokens
output_usage_incomplete
worktree_path
backgrounded
```

它同时服务模型、Billing、Telemetry、UI 和 Resume。

---

## 128. 完成文本为什么带 `<subagent_meta>`

Foreground Success 返回 Output、ID、Type、Tool Calls、Turns、Duration，以及 Resume Footer。

Parent 模型可直接拿 ID 做 `resume_from`，无需从自然语言猜身份。

---

## 129. Completion Output Cap 在哪里应用

Request 可设置 Child Output Cap；Coordinator Buffer 还有 Host-level Cap。

前者约束该 Child，后者约束未 Drain Reminder 的内存/Prompt。磁盘可持久化更完整输出。

---

## 130. Background Completion 怎样回 Parent

1. Coordinator 生成 Completion Summary。
2. 按 Parent Session Buffer。
3. Shell 按 Disposition 通知或 Auto-wake。
4. Parent 下次 Turn Drain，或模型主动 Poll。

有 Poll Tool 时 Reminder 可只给 Metadata；无 Poll Tool 时需要 Inline Output。

---

## 131. 为什么完成结果可能不留在内存

Shell 把 Output 写到 `subagents/{id}` 并保存 Reference。

Coordinator 可清空内存正文，Query 时按 Reference 加载；失败返回明确 Placeholder。

---

## 132. Usage 为什么要 Fold 回 Parent

用户看到一个 Parent Turn，但成本包含 Child 多个模型请求。

Child 按 Model 汇总 Usage，通过 Parent Command Fold 到 Session Ledger，并关联 Prompt ID。

---

## 133. Background Usage 为什么暂时不完整

Parent Prompt 结束时 Child 可能仍运行。

当前 Report 标 `background_live` 或 `subagent_usage_not_applied`；Child 后续结束再进 Session Ledger。

---

## 134. Usage Apply 失败为什么有 Sticky Marker

若 Parent 未确认 Fold，Runtime 先尝试 Parent Actor Marker，再用 Coordinator Prompt Scope 标记。

账单会声明 Incomplete，而不是给出错误精确值。

---

## 135. Cancellation 为什么可能隐藏 Usage

Cancel 可能发生在 Provider 产出 Token、最终 Usage 尚未提交的窗口。

已有 Turn/Tool Activity 时设置 `cancellation_may_hide_usage`；零不一定代表没消费。

---

## 136. Child 结束为何 Reparent Terminal Notification

Child Background Command 可能继续运行。

结束前将 Owner Session 和 Notification Handle 改成 Parent，之后事件仍可被接收。

---

## 137. Child Scheduler 为什么通常共享

普通 Child 可复用 Parent Scheduler，使 Scheduled Task 在 Child 退出后继续。

Workflow Child 可能不继承，避免扩散无法归属的任务。

---

## 138. Graceful Shutdown 的顺序

1. 持久化 Result/Trace。
2. Fold Usage。
3. Reparent Background Resource。
4. 给 Child 发送 Graceful Shutdown。
5. End Workspace Local Session。
6. Snapshot/Remove Worktree。

先收敛终态信息，再释放 Session。

---

## 139. Worktree Completion 的两种策略

Preserve：保留 Worktree 给 Parent Review。

Snapshot then Dispose：保存到：

```text
refs/grok/subagents/{subagent_id}
```

Ref 写入 Metadata 后才删除目录。

---

## 140. 为什么先持久化 Ref 再删除

先删目录再写 Metadata 失败，Resume 会失去恢复锚点。

实现只有确认 Snapshot Ref 已持久化才 Remove；否则保留 Worktree。

---

## 141. Snapshot 失败为什么保留 Worktree

- Snapshot 失败：保留目录。
- Snapshot 成功但 Remove 失败：Ref 和目录都在。
- Remove 成功：清空 Result `worktree_path`。

恢复信息优先于磁盘整洁。

---

## 142. Trace Upload 为什么 Non-fatal

Child Prompt、Context、Turn Result、Permission Event 可上传审计。

Upload 失败只 Warning，不改变任务成功与否；业务结果不依赖 Observability Pipeline。

---

## 143. Background Spawn 完整时序

```text
Parent calls task
  -> depth/cwd/type/model validation
  -> UUID v7 + request
  -> detached backend future
  -> immediate id

Coordinator
  -> Pending
  -> resolve definition/runtime
  -> optional worktree
  -> create child session
  -> promotion handshake
  -> Active
  -> child Prompt + agentic loop
  -> persist/fold/cleanup
  -> Completed
  -> reminder/query
```

---

## 144. Foreground Auto-background 时序

```text
task(background=false)
  -> wait
  -> 45s deadline
  -> mark handle-only
  -> return backgrounded handle
  -> child continues unchanged
  -> later reminder/query
```

它是 Delivery Transition，不是 Restart。

---

## 145. Resume 完整时序

```text
task(resume_from=id)
  -> validate same root parent
  -> reject active/missing source
  -> validate type/persona
  -> ignore new model/cwd
  -> pin source model
  -> reuse/rehydrate worktree
  -> copy transcript/tool state
  -> render current system prompt
  -> append new task prompt
  -> run with new child id
```

Resume 创建新 ID，不把旧 Entry 改回 Active。

---

## 146. Parent Stop 完整时序

```text
Stop/Esc
  -> ParentSession cancel
  -> block late spawn
  -> cancel non-workflow pending/active
  -> active: token + control
  -> pending: token
  -> cancelled terminal results
  -> next turn opens admission
```

Workflow 有独立取消路径。

---

## 147. 最值得学习的十点

1. Definition 与 Session 分离。
2. Visible equals Callable。
3. Background Handle 前 Eager Validation。
4. 并发执行、Single-writer 状态提交。
5. Promotion Handshake 关闭 Cancel Race。
6. Capability Intersection 防扩权。
7. Fresh Context by Default。
8. Delivery 与 Execution 分离。
9. Usage Completeness 显式化。
10. Snapshot Ref 建立后才破坏性清理。

---

## 148. 常见误读

### 误读一：Subagent 共享 Parent Chat History

普通 `task` 默认 Fresh；Parent 必须写清 Prompt。

### 误读二：Background 不受 Parent Stop 影响

ParentSession Cancel 覆盖非 Workflow Background Child。

### 误读三：Foreground 会无限阻塞

默认约 45 秒转后台。

### 误读四：请求 All 就获得所有工具

还要经过 Definition Ceiling、Allowlist、Session Clamp 和 Permission。

### 误读五：Worktree 请求失败必然 Spawn 失败

当前会 Warning 后降级 Shared Workspace。

### 误读六：Resume 可以换 Type/Persona/Model

Type/显式 Persona 冲突失败；Model 被 Pin。

### 误读七：Child 完成即 Parent 已拿到账单

Usage Fold 可能延迟或失败。

### 误读八：Child 退出后后台命令一定被杀

共享 Backend 的任务可 Reparent 给 Parent。

---

## 149. 建议的源码阅读顺序

第一遍：

1. `xai-tool-types/src/task.rs`
2. `xai-grok-agent/src/config.rs`
3. `xai-grok-agent/src/discovery.rs`
4. `task/mod.rs`

第二遍：

1. `task/types.rs`
2. `task/backend.rs`
3. `task/coordinator_state.rs`
4. `task/coordinator.rs`
5. `task/coordinator/query.rs`

第三遍：

1. `xai-grok-subagent-resolution/definition.rs`
2. `overrides.rs`
3. `context.rs`
4. `resume.rs`
5. Shell `subagent/mod.rs`
6. Shell `handle_request.rs`

---

## 150. 调试 Subagent 时先问的十二个问题

1. 模型实际看到的 Tool Name 是什么？
2. Type 是否在 Live Roster，是否被 Toggle/Allowlist 移除？
3. Depth/Max Depth 是多少？
4. CWD 是否存在，是否与 Worktree 冲突？
5. Model 来自哪一层？
6. Capability 被哪两个 Mode 相交？
7. Context 是 New、Forked 还是 Resumed？
8. Registry 在 Pending、Active 还是 Completed？
9. Promotion Ack 是否因 Cancel 返回 False？
10. Foreground 是否只是到 Deadline 转后台？
11. Completion 被 Reply、Waiter、Poll 还是 Reminder 消费？
12. Usage 是否 Fold，Worktree 是否 Snapshot？

---

## 151. 值得亲手做的实验

### 实验一：Fresh Context

把关键事实只留在 Parent History、不写进 Task Prompt，观察普通 Child 不知道；再显式加入 Prompt。

### 实验二：Depth Strip

Max Depth 设 1，检查 Depth 1 Child Schema 不含 Task。

### 实验三：Auto-background

运行超过 45 秒的 Foreground Child，确认 ID 不变。

### 实验四：Capability Intersection

Definition ReadWrite，请求 Execute，确认交集 ReadOnly。

### 实验五：Resume Identity

用 `general-purpose` Resume 已完成 `explore`，确认 Type Mismatch。

### 实验六：Cancel Race

Worktree 创建阶段立即 Cancel，确认 Promotion 拒绝。

### 实验七：Delivery 去重

比较 Foreground Reply、Blocking Poll 和 Reminder。

### 实验八：Worktree Snapshot

确认 Ref 持久化后目录才删除，并可 Resume Rehydrate。

---

## 152. 本篇术语表（Glossary）

### Active

Child 已完成 Promotion、可查询实时 Progress 的 Coordinator 状态。

### Admission

Parent 是否允许新 Spawn；Stop 后关闭，下一 Turn 重开。

### Agent Definition

Agent 的静态声明，包含 Prompt、Toolset、Model、Permission 与上下文策略。

### Agentic Loop

模型输出、工具执行、结果回填、再次采样直到终止的循环。

### Background

当前 Tool Call 不等待终态，通过 Handle、Poll 或 Reminder 获取结果。

### Backend

隐藏具体通信实现的接口；`SubagentBackend` 把 Tool 与 Shell 解耦。

### Blocking Waiter

等待 Child Terminal State 或 Timeout 的请求。

### Buffered Completion

完成后等待 Parent 消费的 Summary。

### Capability

Child 能使用的 Tool Class 集合。

### Capability Ceiling

Definition 给出的最大能力，Spawn Override 不能突破。

### Cancellation Token

可 Clone 的协作式取消信号。

### Child Control

Coordinator 对 Active Child 的取消和 Progress 接口。

### ChildRunner

把抽象 Spawn Request 变成具体 Child Runtime 的 Host Adapter。

### Child Session

一次 Spawn 创建的完整独立 Session。

### Completed

Child 已结束且可 Query/Resume 的状态。

### Completion Disposition

描述 Result 已从哪个通道交付、是否还应 Surface 的路由信息。

### Completion Requirement

Definition 声明的结束前必调 Tool 协议。

### Coordinator Actor

通过单一 Mailbox 串行维护 Child Lifecycle 的 Actor。

### CWD

Child 文件与命令操作的 Current Working Directory。

### Deadline

Foreground 等待边界；到期转后台而非取消。

### Defense in Depth

重要约束在多个层次重复验证。

### Delivery Policy

Result 通过 Reply、Poll 或 Reminder 回 Parent 的规则。

### Detached Task

当前调用不等待其结束的 Tokio Task。

### Effective Runtime Config

合并 Override、Role、Persona、Definition 和 Parent 后的实际配置。

### Eager Validation

真正启动或返回 Background Handle 前完成验证。

### Fail-closed

无法满足身份/安全条件时拒绝继续。

### Fire-and-forget

启动后不保留管理通道；Background Subagent 并非如此。

### Fold Usage

把 Child Token Usage 合并进 Parent Ledger。

### Foreground

当前 Tool Call 尝试等待 Child 结果。

### Fork Context

把 Parent Conversation 清洗折叠为 Child Background 的内部功能。

### Fresh Context

不复制 Parent Conversation 的默认启动方式。

### FuturesUnordered

任意 Future 完成即可产出结果的并发集合。

### Harness

为 Agent 提供 Prompt、Toolset、完成协议与运行策略的宿主框架。

### Hunk Tracker

记录文件修改区块与归因的共享 Actor。

### Identity Gate

Resume 时要求 Type/Persona 与 Source 一致的检查。

### Initializing

Spawn 已接受、Child 尚未 Promotion 的状态。

### IoC

控制反转；Tool 使用注入 Backend，不自己创建 Shell Child。

### Isolation

Child 在 Shared Workspace 或独立 Worktree 执行。

### Least Privilege

只授予完成任务所需的最小权限。

### Mailbox

Actor 接收 Event 的 Channel。

### Managed Policy

组织或宿主施加、Definition 不能覆盖的安全上限。

### Max Depth

Subagent 嵌套层级上限，默认 1。

### Max Turns

Child Agentic Loop 最多执行的 Turn 数。

### MCP Inheritance

Child 从 Parent 继承哪些 MCP Server 的策略。

### Metadata

记录 Type、Model、CWD、Usage、Worktree 和状态的结构化信息。

### Nested Subagent

由另一个 Child Spawn 的后代 Agent。

### Oneshot Channel

只发送一个值的异步 Channel。

### Orphan

失去 Parent/Coordinator 管理但仍运行的 Child 或任务。

### Parent Prompt ID

启动 Child 的 Parent Turn ID。

### Parent Session ID

Child 所属 Root Parent Session 的身份。

### Pending

Spawn 已接受但 Child 尚未报告 Started 的状态。

### Persona

命名的行为/风格层，可提供 Instruction 与运行默认值。

### Pin Model

Resume 时强制使用 Source Child Model。

### Poll

按 Child ID 查询状态或结果。

### Progress Snapshot

Child 当前 Turn、Tool、Token 和 Error 的即时视图。

### Promotion

Child 从 Pending 原子迁移到 Active。

### Promotion Handshake

Shell 报告 Started 并等待 Bool Ack 的 Cancel-race 协议。

### Reparent

把后代或后台资源的 Lifecycle/Notification Ownership 转给 Root Parent。

### Resume

从 Completed Peer Child 的 Transcript、Tool State、Model 和 CWD 继续。

### Role

按任务类型配置的 Capability、Model、Prompt 和 Isolation 预设。

### Roster

当前公告给模型的可用 Subagent Type 列表。

### Runtime Override

某次 Spawn 动态提供的 Model、Capability、Persona 或 Isolation。

### Sentinel String

模型为 Optional Field 生成的伪空值，如 `"null"`。

### Session Teardown

Parent 卸载时取消 Child、清 Reminder 和释放 Ownership。

### Shadow

高优先级同名 Definition 覆盖低优先级定义。

### Shared Workspace

Parent/Child 使用同一目录，文件修改立即可见。

### Single Writer

只有 Coordinator Actor 可以修改 Registry State。

### Snapshot Ref

保存 Worktree State 的 Git Ref，可用于 Resume Rehydrate。

### Structured Output

按 JSON Schema 约束的 Child 最终输出。

### Subagent Type

模型通过 `task` 选择的 Agent Definition Name。

### Surface Completion

是否主动把后台终态送给 Parent。

### Task Owner

Child 的所有权类别，例如普通 Task 或 Workflow。

### Terminal State

Completed、Failed 或 Cancelled。

### Tool Kind

跨具体 Tool Name 的能力分类。

### Tool Registry

Child 可公告和执行的 Tool 集合。

### Usage Incomplete

当前统计因 Background、Cancel 或 Fold Failure 无法保证完整。

### UUID v7

时间有序 UUID，用作 Subagent/Prompt ID。

### Visible equals Callable

Roster 展示的 Definition 与 Runtime 真正调用的 Definition 一致。

### Worktree

Git Repository 的独立工作目录，用于隔离 Child 修改。

### Workflow Owner

由 Goal/Workflow Harness 管理、取消与 Surface 规则不同的 Child Ownership。

---

## 153. 小结

grok-build 的 Subagent Runtime 可以概括为四层：

1. **Definition Plane**：发现 Agent Markdown，解决 Scope、Shadow、Plugin Trust 和 Toggle。
2. **Tool Plane**：公告 Live Roster，清洗并验证 Spawn 参数。
3. **Coordination Plane**：Single-writer Actor 管理状态、Waiter、Deadline 与 Cancel。
4. **Execution Plane**：Shell 创建独立 Prompt、Chat State、Sampler、Tools、Persistence 和 Context Window。

最关键的源码事实是：

- 普通 `task` 默认不复制 Parent Conversation。
- Background 是结果交付策略，不是无人管理的 Fire-and-forget。
- Capability 取交集，Override 不能扩权。
- Nested Child 在 Lifecycle 上 Reparent 到 Root，但 Completion 不越层 Surface。
- Resume 延续 Source Transcript/Model/CWD，却重绘当前 System Prompt。
- Cancel、Usage、Terminal Task、Worktree 和 Completion 都必须在结束后显式收敛。

理解这些边界后，“让另一个 Agent 去做”就不再是一句 Prompt 技巧，而是一套可定位的 Runtime Protocol：哪个 Definition 被发现、哪个 Gate 允许 Spawn、哪个 State Transition 接管 Child、哪些上下文被继承、结果从哪条通道返回，以及失败或取消时谁负责清理。
