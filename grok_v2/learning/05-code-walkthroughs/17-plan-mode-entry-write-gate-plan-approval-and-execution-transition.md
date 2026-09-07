# Walkthrough：一次 Plan Mode 如何进入、限制写入、审批计划并切回执行

> 场景：用户把当前 Session 切到 Plan Mode，或模型主动调用 `enter_plan_mode`。模型可以搜索、阅读代码，并且只能把方案写入 Session 专属的 `plan.md`。完成后，模型调用 `exit_plan_mode`；Shell 先确保同批次中的计划编辑已落盘，再把磁盘上的最终计划交给客户端审批。用户可以批准、要求修改或放弃。批准后模式切回 Agent，后续工具调用重新获得执行语义。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。Plan Mode 涉及 Shell、Tools、ACP 客户端和持久化四层；未来阅读时，应先核对本文的类型与函数，再参考行号。

---

## 1. 最终调用链

```text
用户从客户端选择 Plan
  -> ACP session/set_mode("plan")，或 prompt._meta.mode = "plan"
  -> SessionMode::Plan / PromptMode::Plan
  -> PlanModeTracker::enter_pending()
  -> Inactive -> Pending
  -> 第一条 Plan prompt 到来
  -> inject_plan_mode_reminders()
  -> Pending -> Active
  -> 注入 plan path、只写 plan.md、结束工具约束

模型探索代码
  -> read/search/list/bash 等走常规权限路径
  -> edit-class tool 进入 plan_mode_edit_gate()
     -> 目标恰为 plan.md：允许，并可自动批准
     -> 其他目标：在权限系统之前拒绝
  -> todo_write 可更新可见任务列表，但不改变 Plan Mode

模型编辑 plan.md 并调用 exit_plan_mode
  -> execute_tool_calls() 把 ExitPlan-kind 调用拆到批次尾部
  -> 先执行同批次普通调用，使 plan.md 落盘
  -> prepare_tool_call() 重新读取磁盘 plan.md
  -> request_plan_approval()
  -> ACP reverse request: x.ai/exit_plan_mode
     -> approved：真正执行 exit_plan_mode
        -> PlanModeExited notification
        -> Active -> Inactive
        -> PromptMode::Agent + CurrentModeUpdate(default)
     -> cancelled：保持 Active，把反馈交给模型继续修订
     -> abandoned：退出 Plan Mode，但不自动开始实现
     -> client disconnect：保存 awaiting_plan_approval，恢复后重新审批
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| Plan 状态机 | `xai-grok-shell/src/session/plan_mode.rs` | `PlanModeTracker`、`PlanModeState` |
| 模式入口与提醒 | `.../acp_session_impl/session_mode.rs` | `handle_session_mode`、`inject_plan_mode_reminders` |
| Tool Call 编排 | `.../acp_session_impl/tool_calls.rs` | `execute_tool_calls`、`prepare_tool_call` |
| 工具通知桥 | `xai-grok-shell/src/tools/notification_bridge.rs` | `PlanModeEntered`、`PlanModeExited` 分支 |
| Session 模式类型 | `xai-grok-tools/src/types/session_mode.rs` | `SessionMode` |
| 主动进入工具 | `.../grok_build/enter_plan_mode/mod.rs` | `EnterPlanModeTool` |
| 主动退出工具 | `.../grok_build/exit_plan_mode/mod.rs` | `ExitPlanModeTool` |
| 审批协议 | `.../exit_plan_mode/types.rs` | `ExitPlanModeExtRequest/Response` |
| Todo 工具 | `.../grok_build/todo/mod.rs` | `TodoWriteTool`、`TodoState` |
| 状态持久化 | `xai-grok-shell/src/session/storage/jsonl/mod.rs` | `write_plan_mode_state` |
| 写门禁测试 | `.../plan_mode_edit_gate_tests.rs` | plan file allow/reject cases |
| 批次屏障测试 | `.../plan_exit_batch_barrier_tests.rs` | mixed edit + exit ordering |
| 审批恢复测试 | `.../plan_approval_resume_tests.rs` | disconnect/re-park cases |

## 3. 先区分四个名字相近的对象

| 对象 | 本质 | 是否切换模式 | 是否写入 `plan.md` |
| --- | --- | --- | --- |
| `SessionMode::Plan` | 客户端协议层模式 | 是，作为入口信号 | 否 |
| `PromptMode::Plan` | 当前/本 Turn 的语义标签 | 间接反映切换 | 否 |
| `PlanModeTracker` | Shell 内的生命周期状态机 | 是，真正决定 Active | 只保存路径，不保存正文 |
| `TodoState` | 工具 Resources 中的任务列表 | 否 | 否 |

因此，“更新计划”可能指两件完全不同的事：

- 更新 `todo_write` 列表，用于展示工作进度；
- 编辑 `plan.md`，形成等待用户审批的实施方案。

只有后者参与 `exit_plan_mode` 审批。

## 4. Plan Mode 不是一个 AgentDefinition

`PlanModeTracker` 的注释明确说明，它是 Session-scoped mutable state，与 `session_yolo_mode`、`active_agent_type` 并列，不属于 `AgentDefinition`。

这意味着 Plan Mode 可以覆盖在当前 Agent 之上。切换它不必重新发现或创建另一个 Agent，而是改变当前 Session 的提示、写门禁和退出流程。

## 5. 为什么同时需要 SessionMode 与 PromptMode

`SessionMode` 是 ACP 边界上的闭集：

```rust
pub enum SessionMode {
    Default,
    Plan,
    Ask,
}
```

它负责把 `"default"`、`"plan"`、`"ask"` 这些 wire ID 转成类型。未知 ID 回退到 `Default`，避免新客户端模式让旧 Shell 彻底失败。

`PromptMode` 则位于 Session 内部，取值为 `Agent`、`Ask`、`Plan`，用于：

- 标记 prompt 的预期行为；
- 记录 Turn 开始和结束时的模式；
- 判断 Fork 是否需要可写 Worktree；
- 在审批恢复时启动 Plan 或 Agent synthetic turn。

## 6. current_prompt_mode 与 turn_prompt_mode 也不是同一个值

SessionActor 持有两份相关状态：

- `current_prompt_mode`：Session 级别，决定下一 Turn 从什么模式开始；
- `turn_prompt_mode`：当前 Turn 级别，可被本 Turn 内的 `enter_plan_mode` / `exit_plan_mode` 改变。

`session/set_mode` 主要改变下一 Turn 的入口；模型在 Turn 中主动调用模式工具，则会同时更新当前 Turn 的结束模式。

这个拆分让 telemetry 能回答：“Turn 从 Agent 开始，却在中途进入了 Plan”。

## 7. PlanModeState 有四个状态

```text
Inactive
Pending
Active
ExitPending
```

它们不是四种 UI，而是四种并发语义。

| 状态 | 模型是否已经看到 Plan 约束 | 写门禁是否生效 | 典型来源 |
| --- | --- | --- | --- |
| `Inactive` | 否 | 否 | 默认状态或已退出 |
| `Pending` | 否 | 尚未 Active | 用户刚切换，首个 prompt 未到 |
| `Active` | 是 | 是 | 提醒已注入或工具已进入 |
| `ExitPending` | 是 | 当前 Turn 结束前仍视作退出延迟 | 用户在运行中的 Turn 关闭 Plan |

## 8. Pending 解决“UI 已切换、模型尚不知道”的间隙

如果用户在空闲时点击 Plan，Shell 不能立刻假定模型已经收到约束。模型只有在下一次采样看到 reminder 后，才真正知道自己处于 Plan Mode。

因此入口先执行：

```text
Inactive -> Pending
```

第一条用户 prompt 被处理时，提醒在用户正文之前注入，然后才执行：

```text
Pending -> Active
```

状态描述和模型上下文由此保持一致。

## 9. 第一条 Prompt 是激活边界

`inject_plan_mode_reminders()` 按固定优先级检查：

1. `Pending`：激活并注入 full 或 reentry reminder；
2. 已经 `Active`：注入本 Turn 的 full/sparse reminder；
3. 已退出：注入一次性 exit reminder。

三个分支有顺序，防止同一 Turn 同时收到“进入”和“仍在 Plan”两份重复提示。

## 10. Reminder 为什么伪装成 user-side system reminder

Shell 把提醒包装成 `<system-reminder>` 形式的 Conversation item，并在真正用户消息之前推入。

它不是改写最初 system prompt，而是把 Session 动态状态附着到当前 Turn。这样每次切换、重入、压缩后恢复都能即时生效，不需要重建完整 Agent。

## 11. Full reminder 传递四条核心规则

完整提醒会告诉模型：

- Plan Mode 已激活，禁止系统写操作；
- plan file 的绝对路径；
- `plan.md` 是唯一允许编辑的文件；
- Turn 只能以询问用户或 `exit_plan_mode` 结束。

若 plan file 已有非零长度内容，提示模型读取和编辑；否则提示模型创建计划。

## 12. 工具名不是硬编码到提醒里的

模板使用类似：

```text
${{ tools.by_kind.edit }}
${{ tools.by_kind.ask_user }}
${{ tools.by_kind.exit_plan }}
```

`TemplateRenderer` 根据实时 Tool Registry 把 `ToolKind` 映射为客户端实际看到的名字。

这使工具被 rename 或不同 harness 使用不同 wire name 时，提醒仍与实际工具表一致。

## 13. Full 与 Sparse 提醒交替出现

`reminder_count` 为偶数时使用 full reminder，为奇数时使用 sparse reminder。

Sparse 版本只重申：Plan Mode 仍然开启，除 plan file 外不要写入。

交替的目的不是放松限制，而是节省重复 token：约束每 Turn 都出现，完整路径和工具说明无需每次重发。

## 14. Compaction 后为什么重置 reminder_count

上下文压缩可能让较早的完整说明变得不可见。因此 `reset_after_compaction()` 在 Active 状态把计数重置为 0，让下一次注入重新使用 full reminder。

这是“恢复语义信息”的机制，不只是计数器清零。

## 15. 重入有专用 reminder

`was_previously_active` 记录本 Session 是否曾真正进入过 Plan Mode。

再次进入时，reentry reminder 会告诉模型：

- 这是返回 Plan Mode；
- 上一轮的 plan file 仍存在；
- 应继续修改原计划，而不是默认从空白开始。

退出不会清除该历史位。

## 16. 用户入口有两条协议路径

当前实现兼容：

- `session/set_mode` 调用 `handle_session_mode()`；
- prompt `_meta.mode` 调用 `reconcile_plan_mode_with_prompt()`。

两条路径最终都调用幂等的 `enter_pending()` 或 `user_exit()`。因此客户端只发 prompt mode，或显式先 set_mode，都不会重复进入。

## 17. enter_pending 是幂等的

只有以下转换返回 `true`：

```text
Inactive -> Pending
ExitPending -> Active
```

从 `Pending` 或 `Active` 再次进入不会重复计数、重复持久化或重复发 mode update。

## 18. ExitPending -> Active 是取消延迟退出

用户在 Turn 运行中关闭 Plan，会进入 `ExitPending`。若在 Turn 完成前又重新打开，模型本来就仍拥有 Plan 上下文，因此直接回到 `Active`，不需要再注入一份激活提醒。

这是一条并发纠偏路径，而不是普通重入。

## 19. Mid-turn 进入需要 buffered activation

如果用户在模型 Turn 正运行时开启 Plan，Shell 会：

1. 先从 `Inactive` 进入 `Pending`；
2. 预渲染 activation reminder；
3. 调用 `activate_mid_turn()` 进入 `Active`；
4. 把 reminder 保存在 `pending_activation`；
5. 等运行中 Turn 的安全 drain point 再交给模型。

这样无需粗暴取消整个 Turn，也不会在任意并发位置直接修改对话。

## 20. 未送达的 mid-turn activation 可以撤回

若用户在 buffered reminder 送达前又关闭 Plan，`user_exit()` 会取走 buffer，并恢复进入前的 `was_previously_active`。

模型从未看到 Plan 指令，所以系统不应该再向它发送“你已经退出 Plan”的提醒，也不应把这次短暂点击算成真实重入历史。

## 21. 模型也能主动调用 enter_plan_mode

`EnterPlanModeTool` 用于任务存在方案歧义，或用户明确要求先写计划的情况。

它是 agent-initiated 入口，与用户 UI 切换不同：工具执行结果本身已经告诉模型进入 Plan，因此 Notification Bridge 调用 `activate_from_tool()`，直接：

```text
Inactive -> Active
```

无需经过 `Pending`。

## 22. Enter 与 Exit 工具互相要求存在

两个工具各自的 `requires_expr()` 要求另一个工具已注册：

- Enter 要求 Exit；
- Exit 要求 Enter。

这样不会出现“可以进入却无法退出”或“从未能进入却暴露退出”的半套工具配置。

## 23. EnterPlanMode 为什么标记为 read-only

它的 capabilities 声明 `is_read_only: true`、scope 为 Read，方便权限 UI 按模式控制工具。

但它内部可能创建空 plan file。这是受控的 Session artifact 初始化，不代表模型获得任意 workspace 写权限。

因此 capability 是产品级权限语义，而不是“函数内部绝无任何磁盘写入”的字面承诺。

## 24. Enter 工具先发通知，再准备 plan file

运行时首先通过 `NotificationHandle` 发出 `PlanModeEntered`，其中带 tool call ID。Notification Bridge 收到后更新：

- `PlanModeTracker`；
- `current_prompt_mode`；
- `turn_prompt_mode`；
- 持久化 snapshot；
- 客户端 `CurrentModeUpdate(plan)`。

工具层负责声明事件，Shell 编排层负责解释事件，这是通知桥的解耦作用。

## 25. plan file 是 Session 专属 artifact

`PlanModeTracker::new(session_dir)` 把路径固定为：

```text
<session_dir>/plan.md
```

注释给出的实际形态类似：

```text
~/.grok/sessions/<encoded-cwd>/<session-id>/plan.md
```

它不是仓库根目录下的普通产品文件，也不应该和代码库自己的设计文档混淆。

## 26. PlanModeTracker 只保存路径，不缓存正文

Tracker 中有 `plan_file_path`，没有 `plan_content`。

正文以磁盘文件为真相源。提醒判断、退出审批和恢复审批都会在需要时重新读文件，避免内存副本与最后一次编辑结果发生漂移。

## 27. Enter 工具采用“探测后创建”，绝不截断

`probe_or_create_empty_plan_file()` 的策略是：

- 文件存在且为空：返回 `Empty`；
- 文件存在且非空：返回 `NonEmpty`；
- 仅在 `NotFound` 时写入空字节；
- 其他读取错误：失败关闭，不尝试写。

尤其不能用无条件 create/truncate，否则重新进入 Plan Mode 会抹掉上一轮方案。

## 28. 非 NotFound 错误为何不能尝试修复

如果路径是目录、权限不足或 I/O 异常，继续 write 可能覆盖不该覆盖的对象，或把真实故障误判成“文件不存在”。

代码把这些情况映射为 `NotAFile`、`Inaccessible`、`NotCreated` 等 seed status，交给模型/调用方理解，而不是冒险写入。

## 29. plan_file_has_content 与退出读取略有差异

提醒阶段用 metadata length 判断 `> 0`，所以纯空白文件会被认为“有内容”。

退出阶段读取字符串并 `trim()`，纯空白被视为 empty plan。

源码明确记录了这个差异；空 seed 恒为零字节，所以正常路径不受影响。

## 30. 当前 build 并没有通过隐藏工具实现 Plan

`filter_cursor_tools_by_plan_mode()` 当前是 pass-through：传入什么 ToolDefinition，就返回什么。

这点很关键。Plan Mode 的安全性不能建立在“模型看不到写工具”上；当前实现允许工具仍出现在列表中，然后在执行前使用硬门禁拒绝不合法编辑。

## 31. 提示约束不是安全边界

Full reminder 会要求模型不要写，但模型输出不是可信权限决策。模型仍可能错误调用 edit tool，或构造批量调用。

因此真正的安全边界是 `prepare_tool_call()` 中的 `plan_mode_edit_gate()`。

## 32. 写门禁发生在常规权限之前

门禁的设计目标包括：即使用户开启 always-approve / YOLO，Plan Mode 仍必须只允许计划文件。

若先进入 permission manager，YOLO fast path 可能直接批准。所以代码先判定 Plan edit gate，再走一般权限逻辑。

## 33. 门禁按 AccessKind 判断写操作

核心规则可简化为：

```rust
if tracker.is_active()
   && access_kind is Edit(path)
   && path != tracker.plan_file_path()
{
    RejectNonPlanFile
}
```

非 edit-class 工具不会被这一层拒绝，仍交给原有 Hook/权限/执行流水线。

## 34. 为什么不只按工具名黑名单

工具可以改名，不同 toolset 也可能提供不同 edit 实现。按 `AccessKind::Edit(path)` 判断，能把策略绑定到行为分类与目标路径，而不是某几个字符串名称。

这也使 Gate 与 permission layer 共享相同的访问语义。

## 35. plan file 路径必须精确相等

`is_plan_file_write(target_path, plan_file)` 当前只做 `target_path == plan_file`。

它不会因为扩展名是 `.md` 就自动允许，也不会允许 plan file 同目录的其他文件。这是 Grok toolset 的严格 plan-file-only 规则。

## 36. apply_patch 在 Plan Mode 中为什么会被拒绝

当前 `apply_patch` 的访问分类使用占位目标 `Edit("apply_patch")`，在 patch 真正解析前无法把多个文件目标安全对应到唯一 plan file。

因此它永远不等于 plan file 的绝对路径，采用保守策略拒绝。模型应使用能明确给出单文件路径的 edit/search_replace 工具编辑计划。

## 37. Plan Mode 并非禁止所有命令

注释明确写到：bash、read、grep、MCP、web 等非 edit 工具不会由 Plan edit gate 拦截。

它们仍经过普通权限规则。也就是说 Plan Mode 的核心硬约束是 workspace edit，不等于彻底禁止所有有副作用可能性的外部工具；外围 permission policy 仍承担剩余风险控制。

## 38. plan.md 编辑可以自动批准

`should_auto_approve_edit()` 要求：

```text
state == Active && edit_path == plan_file_path
```

这让计划写入无需每一次都弹权限框，同时确保自动批准条件与硬门禁使用同一个路径判定，不会出现一层允许、另一层拒绝的策略漂移。

## 39. 被拒绝时模型会收到可恢复信息

`plan_mode_edit_rejected_template()` 不只返回“permission denied”，还告诉模型：

- 当前处于 Plan Mode；
- 只有哪个绝对 plan path 可编辑。

错误结果因此具有 steering 作用，模型下一轮能改用正确文件，而不是盲目重试原调用。

## 40. TodoWrite 属于 ToolKind::Plan，但不是模式开关

`TodoWriteTool::kind()` 返回 `ToolKind::Plan`，表示它是计划/进度类工具。

运行时它只读取和更新 `Resources` 中的 `State<TodoState>`，不调用 `PlanModeTracker`，不发送 `PlanModeEntered/Exited`，也不修改 PromptMode。

所以 ToolKind 名字表达工具分类，不等于 PlanModeState 转换。

## 41. TodoState 为什么使用 IndexMap

Todo 按 ID 存储在 `IndexMap` 中，既提供 keyed update，又保留插入顺序。

用户看到的任务列表顺序因此稳定；模型可以只按 ID 更新状态，而不必每次重新排序整张表。

## 42. Todo 有四种状态

```text
pending
in_progress
completed
cancelled
```

摘要输出分别显示 `[pending]`、`[in_progress]`、`[completed]`、`[cancelled]`。

这些是任务展示状态，不影响 Plan Mode 生命周期。

## 43. merge=false 是完整替换

`apply_replace()` 先清空原状态，再按请求依次插入。

缺少 content 时用 ID 兜底，缺少 status 时默认为 Pending。它适合模型一次提交完整的新计划列表。

## 44. merge=true 是按 ID 的部分更新

`apply_merge()` 对已有 ID：

- content 缺失则保留原文本；
- status 缺失则保留原状态。

新 ID 则创建项目。模型因此可只发送：

```json
{"id":"inspect","status":"completed"}
```

而无需重复任务描述。

## 45. TodoWrite 默认 merge=true

默认选择 merge，可以降低模型漏传未变化项目导致整表丢失的概率。

代码还有容错升级：即便输入显式看似 replace，只要已有状态非空、所有更新都命中已有 ID 且不带 content，就把它识别成明显的部分状态更新。

## 46. 同一请求不能出现重复 Todo ID

`validate_no_duplicate_ids()` 在修改状态前检查重复 ID。发现重复时返回 `DuplicateId` 输出，避免同一个请求内“后者覆盖前者”制造难以察觉的结果。

这是请求内不变量；跨请求使用相同 ID 正是 merge 的正常行为。

## 47. TodoState 的持久化与 plan_mode.json 不同

Todo 通过 Tool Resources 的 serde 状态跨调用保存。

Plan Mode 生命周期则由 `PlanModeSnapshot` 写入 Session sidecar `plan_mode.json`。`plan.md` 又是独立正文 artifact。

一个完整 Session 因而可能同时存在三种“计划相关状态”：

```text
Resources: TodoState
sidecar:   plan_mode.json
artifact:  plan.md
```

## 48. PlanModeSnapshot 保存什么

Snapshot 包含：

- `state`；
- `was_previously_active`；
- `reminder_count`；
- `pending_exit_reminder`；
- `awaiting_plan_approval`。

`plan_file_path` 不持久化，因为恢复时根据新的 Session directory 重新计算，避免复制/Fork 后仍指向源 Session。

## 49. pending_activation 为什么不持久化

它保存的是“运行中 Turn 下一个安全点要注入的预渲染文本”，依赖当时的 in-flight execution。

进程重启后那个执行上下文不存在，恢复旧 buffer 没有可靠的投递位置。所以 snapshot 不保存它；Active 状态下下一 Turn 的正常 reminder 会补回语义。

## 50. Pending 恢复时折叠为 Inactive

Pending 表示客户端已经点了切换，但尚未有 prompt 触发激活。进程重启后，原来的瞬时交互不再可信。

`from_snapshot()` 把它折叠为 `Inactive`，避免恢复后无缘无故约束下一条对话。

## 51. ExitPending 恢复时也折叠

ExitPending 依赖“某个 Turn 正在运行”。重启后该 Turn 已不存在，因此恢复为：

```text
Inactive + pending_exit_reminder = true
```

下一 Turn 会收到一次“已经退出，可以执行”的明确提示。

## 52. 用户在空闲时关闭 Plan

`Active` 且没有运行中的 Turn：

```text
Active -> Inactive
pending_exit_reminder = true
```

下次 prompt 前注入 exit reminder，然后清除 flag。模型由此知道规则已经放开。

## 53. 用户在运行中关闭 Plan

`Active` 且 Turn in flight：

```text
Active -> ExitPending
```

当前 Turn 仍按已经看到的 Plan 约束收尾。Turn 完成后 `complete_deferred_exit()` 才转为 Inactive 并安排 exit reminder。

这防止 UI 模式变化在工具执行半途中突然改变权限语义。

## 54. Agent 主动退出与用户 Toggle 退出不同

Agent 调用 `exit_plan_mode` 后，工具结果本身会告诉模型计划已批准、可以编码，因此 `deactivate_approved()` 默认不设置 next-turn exit reminder。

用户 Toggle 没有同 Turn 的 tool result，所以必须安排一次提醒。

相同终态需要不同的信息补偿，这是 `pending_exit_reminder` 不能无条件设置的原因。

## 55. exit_plan_mode 不接受计划正文参数

输入是空对象：

```json
{}
```

工具从磁盘读取 plan file。这样审批 UI 展示的是实际落盘内容，而不是模型额外传入、可能与文件不同的另一份文本。

## 56. 磁盘是 Exit 审批的快照来源

准备退出时，Shell 再次读取 Tracker 的 plan path：

- 非空文本 -> `PlanFileRead::Present(content)`；
- 空白或 NotFound -> `Absent`；
- 其他 I/O 错误 -> `Unreadable`。

Unreadable 不会被当成“没有计划所以直接退出”，而是 fail closed，仍进入审批。

## 57. ExitPlan 工具为什么必须成为批次尾部

模型可能在一次响应中同时返回：

```text
1. edit plan.md
2. exit_plan_mode
```

工具执行框架可能并发准备/运行同一批次。如果退出先读文件，审批 UI 会看到编辑前的旧计划。

`split_exit_plan_tail()` 因此把所有 `ToolKind::ExitPlan` 分到 tail batch。

## 58. Tail barrier 与模型给出的调用顺序无关

无论模型输出：

```text
[edit, exit]
```

还是：

```text
[exit, edit]
```

Shell 都先执行非 Exit body，再执行 Exit tail。测试分别覆盖了两种顺序，审批快照必须都是新计划正文。

## 59. 为什么按 ToolKind 拆分，不按名字

Registry 可能给工具配置 `name_override`。如果只判断字符串 `exit_plan_mode`，改名后屏障会失效。

`execute_tool_calls()` 通过 live ToolBridge 查询 wire name 对应的 `ToolKind`，再判断是否为 `ExitPlan`。

## 60. 前序批次拒绝时不能继续审批

若同批次的命令或编辑被用户拒绝，`final_result` 会记录 PermissionReject/Cancelled/Followup。

处理 tail 时，Exit 调用只获得“因前序取消而未执行”的 tool result，不能再弹计划审批。否则用户拒绝了生成计划所需的操作，却会看到一个不完整审批框。

## 61. prepare_tool_call 在真正执行 Exit 前拦截

Exit 先完成通用流程：注册 tool call、解析参数、Hook/权限检查。随后 `prepare_tool_call()` 识别 `ToolInput::ExitPlanMode`，读取计划并调用审批 reverse request。

只有用户 approved，调用才成为 `PreparedToolCall` 并进入真实工具 dispatch。

## 62. Reverse request 是什么

通常是客户端向 Agent 发请求；这里方向相反：Shell 通过 ACP gateway 请求客户端展示交互 UI并等待用户决定。

方法名为：

```text
x.ai/exit_plan_mode
```

因此称为 reverse request，而不是普通 Session notification。

## 63. 审批请求包含三个字段

`ExitPlanModeExtRequest` 以 camelCase 序列化：

```json
{
  "sessionId": "...",
  "toolCallId": "...",
  "planContent": "# Plan ..."
}
```

Session ID 用于路由，Tool Call ID 用于 UI/对话关联，planContent 是审批快照。

## 64. 审批响应有三种语义

| wire outcome | 内部枚举 | 行为 |
| --- | --- | --- |
| `approved` | `Approved` | 执行 Exit，转 Agent |
| `cancelled` | `Cancelled` | 保持 Plan，继续修改 |
| `abandoned` | `Abandoned` | 离开 Plan，不自动实现 |

未知字符串与反序列化异常都回退为 Cancelled，保持 Plan Mode，避免错误响应意外批准实施。

## 65. cancelled 不是普通取消工具

在这个协议中，cancelled 表示“要求修改计划”。如果响应带 feedback，Shell 构造：

```text
The user wants to revise the plan. The user said:
<feedback>
```

并作为 tool result 反馈模型。模式继续 Active，模型可以更新 plan.md 后再次申请审批。

## 66. 空 feedback 会引导模型询问用户

若用户选择修改但没有输入文字，Shell 不猜修改内容，而是要求模型询问用户想改什么。

这避免模型自行发明审批意见。

## 67. abandoned 与 cancelled 的区别

Cancelled：计划方向仍保留，只是需要继续修订。

Abandoned：用户放弃整个计划流程，Shell 调用 `leave_plan_mode_to_default()`，并明确告诉模型除非用户再次要求，否则不要立即重进 Plan。

## 68. approved 后为何还要真正运行 ExitPlanModeTool

审批拦截只决定“是否允许退出”。真正工具执行还负责：

- 再读取标准 plan resource；
- 生成结构化 `ExitPlanModeOutput`；
- 发送 `PlanModeExited` notification；
- 把成功消息写回模型对话。

审批与状态变更因此仍通过正常工具生命周期收敛，而不是在 UI 回调中复制整套 post-flight。

## 69. PlanModeExited 由 Notification Bridge 收口

Bridge 收到通知后：

1. `deactivate_approved()`；
2. `current_prompt_mode = Agent`；
3. `turn_prompt_mode = Agent`；
4. 持久化 Plan snapshot；
5. 发 `CurrentModeUpdate(default)` 给客户端。

UI、模型语义和持久状态由一个 chokepoint 同步更新。

## 70. deactivation 也是幂等的

`deactivate_approved()` 仅从 Active 返回 true。若审批路径已经先处理了 abandon，后续重复 notification 不会再次发送 mode update 或制造额外状态变化。

## 71. 工具输出提供同 Turn 的退出信号

有计划时，Exit 工具返回 `PlanReady`，消息说明计划已批准，可以开始编码；没有正文时返回 `EmptyPlan`，说明退出已批准但未找到计划内容。

正因为模型在同一上下文看到这条结果，Grok-build 配置通常不再排队 next-turn exit reminder。

## 72. awaiting_plan_approval 是可持久状态

在 reverse request 发出前：

```text
awaiting_plan_approval = true
persist snapshot
```

正常收到任何结果时，`AwaitingApprovalGuard` 在 drop 时清除 flag 并持久化。

这使用 RAII 保证早返回分支不容易漏清理。

## 73. 为什么断线时 Guard 要 disarm

如果请求已经送达客户端，但客户端在回答前断线，不能把它当成拒绝，也不能自动批准。

错误路径调用 `clear_awaiting.disarm()`，故意保留 true，使重连恢复时知道有一个真实未完成审批。

## 74. “没有客户端”和“客户端中途断线”不同

ACP channel 的 typed failure 用于分类：

- `SendFailed`：请求根本未送达，没有交互客户端，headless 流程允许工具继续执行；
- `RecvFailed`：已经送达但响应通道断开，保持 Plan 并等待恢复；
- 其他异常：保守地视作仍待审批。

不能靠错误字符串模糊匹配这两个安全语义。

## 75. Resume 会重新 park 审批

Session 恢复后，若 snapshot 的 `awaiting_plan_approval == true`：

1. 检查当前没有重复 parked interaction；
2. 重新读取 plan.md；
3. 构造新的 synthetic tool call ID；
4. 再发 `x.ai/exit_plan_mode`；
5. 根据新决定启动后续行为。

这让 UI 恢复的审批框背后有真实 waiter，而不是只有静态装饰。

## 76. Resume 时计划消失会清除等待

若 plan.md 不存在或为空，恢复逻辑无法重建原审批内容，于是清除 awaiting flag 并返回。

它不会展示一个来源不明的旧正文，也不会凭 snapshot 猜测计划。

## 77. Resume approved 会启动新的实现 Turn

恢复场景没有原先 in-flight Turn 可继续。因此 approved 后：

- 离开 Plan；
- 注入 synthetic 用户消息“用户已批准，实施 plan.md”；
- 以 `PromptMode::Agent` 排队；
- 触发 scheduler 启动 Turn。

这与原 Turn 内审批成功后由 tool loop 自然继续不同。

## 78. Resume cancelled 会启动修订 Turn

要求修改时仍保持 Active，并把 feedback 作为 synthetic prompt，以 `PromptMode::Plan` 启动一个新 Turn。

所以恢复路径也保留“修改计划”和“执行计划”的模式隔离。

## 79. Resume abandoned 不自动启动 Turn

放弃只切回 Default，然后等待用户下一条真实输入。

系统不能把“不要这个计划”解释成“请直接无计划实现”。

## 80. CurrentModeUpdate 是客户端投影，不是状态真相源

真正状态已经在 Tracker 和 PromptMode 中改变；`CurrentModeUpdate(plan/default)` 用于让 UI 模式选择器与 Shell 对齐。

若只发 UI update 而不改 Tracker，写门禁不会生效；若只改 Tracker 不发 update，用户看到的模式会过时。因此两者在同一 transition path 更新。

## 81. 退出后工具列表未必重建

因为当前 build 的 plan filter 是 pass-through，进入和退出主要改变执行语义，而不是注册表结构。

退出后同一个 edit tool 之所以能修改代码，是 `PlanModeTracker` 已 Inactive，硬门禁不再拒绝；常规 permission policy 仍照常执行。

## 82. 一条完整用户进入路径示例

```text
T0: tracker=Inactive, prompt=Agent
T1: UI set_mode(plan)
    tracker=Pending, current_prompt=Plan
T2: user prompt arrives
    inject full reminder
    tracker=Active
T3: model read/search
T4: model edits <session>/plan.md
T5: model calls exit_plan_mode
T6: user approves
    tool runs -> PlanModeExited
    tracker=Inactive
    current_prompt=Agent, turn_prompt=Agent
T7: model receives approved tool result and can implement
```

## 83. 一条要求修改路径示例

```text
Active + plan.md v1
  -> exit_plan_mode
  -> approval UI shows v1
  -> cancelled + "补充回滚方案"
  -> tracker remains Active
  -> model receives revise message
  -> edit plan.md to v2
  -> exit_plan_mode again
```

第一次取消不会执行 ExitPlanModeTool，因此也不会发 `PlanModeExited`。

## 84. 一条断线恢复路径示例

```text
Active
  -> request_plan_approval
  -> awaiting=true persisted
  -> client disconnects before response
  -> tool not executed, Plan remains Active
  -> process/session resume
  -> resume_plan_approval reads plan.md
  -> re-issues reverse request
  -> approved
  -> Default + synthetic Agent implement turn
```

## 85. 最重要的不变量

### 不变量 A：Active 时任意 edit 只能命中 plan file

由 `plan_mode_edit_gate()` 在 permission fast path 之前保证。

### 不变量 B：审批内容必须来自磁盘最终版本

由 file-backed Exit input 与 exit tail barrier 共同保证。

### 不变量 C：未明确批准不能进入实现语义

未知响应、解析失败和中途断线均 fail closed。

### 不变量 D：Todo 不得暗中改变 Session mode

Todo 仅更新 Resources state。

### 不变量 E：UI、PromptMode、Tracker 和持久化必须同步

由 Session mode handler 与 Notification Bridge 的集中 transition 保证。

## 86. 常见误读：Plan Mode 等于只读 Toolset

不准确。当前 build 没有隐藏写工具；它允许模型看见工具，再在执行前按目标路径拒绝。

准确说法是：Plan Mode 对 workspace edit 实施强制 plan-file-only gate，同时其他工具仍受常规权限策略约束。

## 87. 常见误读：todo_write 就是在写实施计划

不准确。Todo 是可见任务状态，`exit_plan_mode` 不读取它。

需要审批的正式方案必须写进 plan.md。

## 88. 常见误读：Exit 工具一调用就切回 Agent

不准确。交互客户端存在时，Shell 在真实 dispatch 前拦截并等待审批。

Cancelled 时工具不执行，模式仍是 Active；只有 approved 后才执行工具并通过通知退出。

## 89. 常见误读：同一批次按模型顺序串行就够了

不可靠。执行框架存在批次准备和并发语义，而且模型可能把 exit 放在 edit 前面。

显式 tail partition 才能声明“退出是批次语义屏障”。

## 90. 常见误读：YOLO 会绕过 Plan 限制

不会。Plan edit gate 特意放在 permission manager 之外，并明确覆盖 always-approve 模式。

YOLO 只影响后续常规权限判断，不会把非 plan file edit 变成合法。

## 91. 常见误读：计划正文应缓存进 snapshot

当前设计不这样做。正文以 plan.md 为真相源，snapshot 只保存生命周期元数据。

这使编辑、审批、Fork 和恢复都能围绕一个文件证据收敛。

## 92. 调试：UI 显示 Plan，但模型修改了代码

按顺序检查：

1. Tracker 是否真的为 `Active`，还是仍在 `Pending`；
2. prompt reminder 是否已注入；
3. 工具是否正确归类为 `AccessKind::Edit(path)`；
4. path 是否意外等于 plan path；
5. 调用是否绕开了 `prepare_tool_call()`；
6. 测试使用的 harness 是否有不同 compat carve-out。

不要只检查 UI mode 字符串。

## 93. 调试：审批 UI 显示旧计划

检查：

1. Exit 工具的 ToolKind 是否仍是 `ExitPlan`；
2. wire name override 是否能被 ToolBridge 正确解析；
3. `split_exit_plan_tail()` 是否运行于多调用 batch；
4. 前一批 edit 是否真正完成并 flush 到相同 plan path；
5. Tracker 与 Tool Resources 是否指向同一个 plan file。

## 94. 调试：重连后审批没有回来

检查 `plan_mode.json` 中 `awaiting_plan_approval`，以及断线错误是否被分类成 delivered-then-disconnected。

再检查 plan.md 是否仍有非空正文；恢复逻辑在正文丢失时会主动清除等待标记。

## 95. 调试：批准后 UI 仍停在 Plan

检查 `PlanModeExited` 是否从工具层送到 Notification Bridge，Bridge 是否：

- 成功 `deactivate_approved()`；
- 发出 `CurrentModeUpdate(default)`；
- 更新两份 PromptMode；
- 写入 PlanModeState persistence message。

## 96. 调试：Todo 状态更新丢失

这通常与 PlanModeTracker 无关。应检查：

- 是否误用了 `merge=false`；
- 请求内是否有重复 ID；
- `State<TodoState>` 是否来自同一个 SharedResources；
- 新工具架构与 OpenCode `todowrite` 是否使用了不同替换约定。

## 97. 推荐的源码阅读顺序

第一次阅读按以下顺序：

1. `PlanModeState` 和 Tracker transition；
2. `handle_session_mode()`；
3. `inject_plan_mode_reminders()`；
4. `plan_mode_edit_gate()`；
5. Enter/Exit 工具；
6. Notification Bridge；
7. exit batch barrier；
8. approval/recovery；
9. 最后单独阅读 Todo。

这样能先建立状态机，再理解跨 crate 事件，不会被工具说明文本带偏。

## 98. 推荐的测试阅读顺序

1. `plan_mode.rs` 内纯状态机单元测试；
2. `prompt_mode_transition_tests.rs`；
3. `plan_mode_edit_gate_tests.rs`；
4. `plan_mode_midturn_tests.rs`；
5. `plan_exit_batch_barrier_tests.rs`；
6. `plan_approval_resume_tests.rs`；
7. Enter/Exit/Todo 各自模块测试；
8. notification bridge 的 mode update 测试。

## 99. 可以做的三个学习实验

### 实验一：状态机表驱动

给 Tracker 输入 `enter / prompt / exit(in-flight) / complete / reenter` 序列，逐步打印 state、reminder flag 和 reentry flag。

### 实验二：混合批次

构造 `[exit_plan_mode, edit plan.md]`，确认审批内容仍是 edit 后版本；再让 edit permission reject，确认没有审批 reverse request。

### 实验三：断线恢复

让 reverse request 成功送达后丢弃 response channel，确认 snapshot 保留 awaiting；重建 Session 后确认 UI 重新收到请求。

## 100. 设计上的关键取舍

### 取舍一：工具可见 + 执行硬门禁

优点是不同 harness 和改名配置更兼容；代价是执行链必须保证所有 edit 都经过统一 AccessKind 分类。

### 取舍二：文件作为审批真相源

优点是方案可检查、可恢复、可编辑；代价是必须解决批次顺序、路径一致和 I/O 错误分类。

### 取舍三：审批状态持久化

优点是断线不丢安全门；代价是 Resume 需要重新建立真实交互 waiter。

### 取舍四：Todo 与 plan file 分离

优点是进度展示和正式方案可独立演化；代价是文档和 UI 必须避免都叫“Plan”造成概念混淆。

## 101. Glossary：模式与状态

### Plan Mode

一种 Session 运行模式：允许探索并撰写计划，但用硬门禁限制 workspace 编辑，退出实施前需要用户审批。

### Session Mode

客户端和 Shell 通过 ACP 传递的高层模式，当前包括 default、plan、ask。

### Prompt Mode

Shell 内部给某次 Prompt/Turn 标记的语义模式：Agent、Ask 或 Plan。

### Tracker

保存生命周期状态并提供合法转换方法的对象。这里指 `PlanModeTracker`。

### State Machine（状态机）

把系统表示为有限状态和允许的转换。它帮助并发入口、退出和恢复保持一致。

### Pending

客户端已经请求进入，但模型还没收到 Plan reminder 的过渡状态。

### Active

模型已经获得 Plan 指令，写门禁正式生效的状态。

### ExitPending

用户在运行中的 Turn 请求退出，系统等待当前 Turn 安全结束的过渡状态。

### Reentry

同一 Session 以前进入并退出过 Plan Mode 后再次进入。

## 102. Glossary：提示与执行

### System Reminder

动态插入对话的高优先级约束文本，用于向模型补充当前 Session 状态。

### Full / Sparse Reminder

完整说明版与节省 token 的简短重申版，两者交替注入。

### TemplateRenderer

根据实时 Registry 把模板中的 ToolKind、路径和变量渲染成实际文本的组件。

### Tool Registry / Toolset

当前 Agent 可用工具及其 ID、名称、分类、参数和能力的集合。

### ToolKind

工具的语义分类，如 Edit、Read、Plan、EnterPlan、ExitPlan；比 wire name 更稳定。

### AccessKind

一次具体调用的访问类型，例如 Read 或 `Edit(path)`，供 Hook、权限和 Plan gate 判断。

### Gate（门禁）

在执行前强制检查不变量的代码边界。拒绝发生后，调用不会进入真实 dispatch。

### YOLO / Always-approve

常规权限自动批准模式。它不覆盖 Plan Mode 的写入硬门禁。

### Chokepoint

多个路径必须经过的单一收口点，用来避免策略散落和状态不一致。

## 103. Glossary：文件、批次与审批

### Plan File

Session 专属 `plan.md`，是正式实施方案和审批内容的真相源。

### Artifact

由一次 Session 产生并保存的文件成果，例如 plan.md、压缩摘要或任务输出。

### Truth Source / Source of Truth

发生冲突时被视为权威的那份数据；退出审批以磁盘 plan.md 为准。

### Batch

模型在一次响应中同时提出的一组 Tool Calls。

### Tail Barrier

把改变后续语义的调用拆到批次尾部，确保前序工作全部完成后才执行。

### Snapshot

某一时刻状态的可序列化副本。PlanModeSnapshot 保存生命周期，但不保存正文。

### Sidecar File

与主要对话日志并列保存辅助状态的文件；这里是 `plan_mode.json`。

### Reverse Request

由 Shell/Agent 反向请求客户端执行交互并等待响应，例如展示计划审批 UI。

### Park / Re-park

把执行暂停在等待用户交互的位置；恢复后重新发起同类等待称 re-park。

### Fail Closed

发生未知错误时保持更安全的限制状态，而不是默认放行。审批解析失败会留在 Plan。

### RAII Guard

利用对象析构自动清理状态的模式；`AwaitingApprovalGuard` 正常离开作用域时清除等待标记。

## 104. Glossary：Todo 与持久化

### Todo

展示任务拆分和进度的列表项，不等同于 plan.md，也不控制 Plan Mode。

### Resources

工具运行时共享的类型化状态容器，TodoState 以 `State<TodoState>` 存在其中。

### IndexMap

同时提供哈希键查找与稳定插入顺序的 Map，适合按 ID 更新且顺序可见的 Todo。

### Merge Semantics

按 ID 将部分字段合入已有状态，而不是替换整个列表。

### Full-replace Semantics

请求携带完整目标列表，执行时先清空旧状态再重建。

### Persistence

把内存状态保存到磁盘，使进程或 Session 恢复后能够继续。

### Synthetic Turn

不是用户新输入，而是系统为恢复/续跑构造并排队的一次模型 Turn。

### Idempotent（幂等）

重复执行同一状态转换不会产生额外副作用。例如 Active 时再次 enter 不会重复激活。

## 105. 一页复习版

```text
Plan Mode = 状态机 + reminder + plan.md + edit gate + approval + persistence

用户进入：
  Inactive -> Pending -> Active

模型工具进入：
  Inactive -> Active

Active 写规则：
  Edit(plan.md) 允许
  Edit(other)   在常规权限前拒绝
  TodoWrite     只更新列表，不切模式

退出：
  mixed batch 先 edit、后 exit
  exit 从磁盘读取 plan.md
  reverse request 等用户决定

approved  -> 执行 Exit -> Inactive + Agent
cancelled -> Active + revision feedback
abandoned -> Inactive + 等用户
disconnect-> Active + awaiting 持久化 + resume re-park
```

## 106. 源码证据索引

| 结论 | 直接证据 |
| --- | --- |
| 四态生命周期 | `session/plan_mode.rs` 的 `PlanModeState` |
| 用户进入先 Pending | `handle_session_mode()` + `enter_pending()` |
| 首 Prompt 激活 | `inject_plan_mode_reminders()` |
| 中途进入 buffer | `activate_mid_turn()` / `take_pending_activation()` |
| 只允许 plan file edit | `plan_mode_edit_gate()` / `should_auto_approve_edit()` |
| 当前工具过滤 pass-through | `filter_cursor_tools_by_plan_mode()` |
| Enter 直接 Active | Notification Bridge 的 `PlanModeEntered` 分支 |
| 文件只在 NotFound 时 seed | `probe_or_create_empty_plan_file()` |
| Exit 不接受正文 | `ExitPlanModeInput {}` |
| Exit 从磁盘读正文 | `ExitPlanModeTool::run()` 与 intercept read |
| Exit 调用位于批次尾 | `split_exit_plan_tail()` |
| 审批采用 reverse request | `request_plan_approval()` |
| 未知 outcome fail closed | `PlanApprovalOutcome::from_response()` |
| 等待审批可恢复 | `awaiting_plan_approval` + `resume_plan_approval()` |
| Todo 只改 Resources | `TodoWriteTool::run()` |

## 107. 阅读完成后应该能回答的问题

1. 为什么用户进入需要 Pending，而工具进入可以直接 Active？
2. 为什么当前 build 即使仍把 edit tool 暴露给模型也能保持 Plan 限制？
3. 为什么 YOLO 不能绕过 plan file gate？
4. TodoState、plan_mode.json 和 plan.md 分别保存什么？
5. 为什么 ExitPlan 必须拆到 batch tail？
6. cancelled、abandoned、disconnect 对状态的影响分别是什么？
7. 为什么 approved exit 不一定需要 next-turn exit reminder？
8. 恢复审批为什么必须重新创建一个真实 reverse-request waiter？
9. PromptMode 的 Session 级与 Turn 级副本为什么同时存在？
10. 审批内容为什么必须重新从磁盘读取？

能独立回答这些问题，就已经抓住了 Plan Mode 的核心：它不是一段提示词，也不是一个 Todo 工具，而是一条跨协议、状态机、权限、文件证据、批次调度和恢复机制的完整控制流。
