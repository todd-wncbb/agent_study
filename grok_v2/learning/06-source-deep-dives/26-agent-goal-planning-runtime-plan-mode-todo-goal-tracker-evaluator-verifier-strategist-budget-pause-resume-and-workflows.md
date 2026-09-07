# 源码精读 26：Agent Goal & Planning Runtime——Plan Mode、Todo、Goal Tracker、Evaluator、Verifier、Strategist、Budget、Pause/Resume 与 Workflow

> 源码基线：`ed6d543`
>
> 上一篇研究 Runtime 怎样仲裁“继续还是结束”；本篇深入其中最复杂的长期任务控制层：短期计划、可见 Todo、长期 Goal 和通用 Workflow 分别解决什么问题，以及 Goal 怎样通过规划、执行、评估、对抗验证、策略调整、预算与暂停恢复形成可持续闭环。

---

## 1. 先给结论：代码中至少有四种“计划”

第一次读源码时，下面四者很容易混为一谈：

| 机制 | 核心问题 | 权威状态 |
|---|---|---|
| Prompt Mode | 这条 Prompt 希望 Agent、Ask 还是 Plan？ | `PromptMode` |
| Plan Mode | 当前 Session 是否只允许规划，不允许普通编辑？ | `PlanModeTracker` |
| Todo | 当前任务有哪些短期步骤和状态？ | Resources 中的 `State<TodoState>` |
| Goal | 长期目标怎样跨多轮执行、验证、暂停和恢复？ | `GoalTracker` 中的 `GoalOrchestration` |
| Named Workflow | 一个声明式多 Agent 流程怎样后台运行和恢复？ | `WorkflowManager` + `WorkflowTracker` + Journal |

它们可以同时存在，但生命周期、执行权和持久化完全不同。

一个最实用的心智模型是：

```text
PromptMode  = 客户端对本次请求的工作形态提示
PlanMode    = Session 级动作约束与 plan.md 生命周期
Todo        = 模型维护的短期工作队列
Goal        = 长期自治闭环和完成证明
Workflow    = 由脚本驱动的后台多 Agent 编排运行
```

---

## 2. 本篇要回答的核心问题

- `PromptMode::Plan` 和 `PlanModeState::Active` 是不是一回事？
- Plan Mode 为什么既靠 Reminder，又必须在 Tool Runtime 中硬拦截编辑？
- 为什么 plan 文件可以自动批准，但其他写入不能？
- Todo 为什么使用 `IndexMap`？`merge=true/false` 分别意味着什么？
- Turn 结束时 UI 把 InProgress 显示成 Completed，真实状态是否也改变？
- `/goal` 如何创建 Goal，`update_goal` 为什么不能创建 Goal？
- Goal 的 Phase 和 Status 有何不同？
- 为什么恢复一个 Active Goal 时会先降为 Paused？
- Goal Planner、Evaluator、Verifier、Strategist 和 Summarizer 各自拥有哪种权力？
- 主模型说“完成了”之后，为什么还要隐藏 Evaluator 和 Skeptic Panel？
- Skeptic 0 为什么先跑、能恢复，却不能用自己的通过票决定多数？
- 相同 gap、不同 gap、真正不可修复 blocker 分别怎样处理？
- Goal Token Budget 怎样避免 Compaction 后计数倒退？
- legacy `update_goal` 路线与新 Goal round evaluator 路线怎样共存？
- `goal_runs_on_workflow_engine()` 与 Named Workflow 是什么关系？

---

## 3. 核心源码地图

| 主题 | 主要源码 |
|---|---|
| Prompt/Plan Mode 类型与状态机 | `xai-grok-shell/src/session/plan_mode.rs` |
| Plan Mode 注入 | `acp_session_impl/session_mode.rs` |
| Plan Mode Tool Gate | `acp_session_impl/tool_calls.rs`：`plan_mode_edit_gate` |
| Enter/Exit Plan Tool | `xai-grok-tools/src/implementations/grok_build/enter_plan_mode/`、`exit_plan_mode/` |
| Todo 类型与工具 | `xai-grok-tools/src/implementations/grok_build/todo/mod.rs` |
| Todo UI 投影 | `xai-grok-shell/src/session/acp_conversion.rs` |
| Todo Turn-end cleanup | `acp_session_impl/turn_end.rs` |
| TodoGate | `acp_session_impl/reminders.rs` |
| Goal slash 命令 | `slash_commands.rs`、`acp_session_impl/slash_exec.rs` |
| Goal Session 接线 | `acp_session_impl/goal.rs`、`goal_support.rs` |
| Goal 纯状态机 | `session/goal_tracker.rs` |
| Goal 通知投影 | `session/goal_orchestrator.rs` |
| Goal Planner | `session/goal_planner.rs` |
| 每轮隐藏 Evaluator | `session/goal_evaluator.rs` |
| 对抗验证 Panel | `session/goal_classifier.rs`、`goal_classifier/evidence.rs` |
| Strategist | `session/goal_strategist.rs` |
| 下一步提取 | `session/goal_next_step.rs` |
| 过早停止检测 | `session/goal_stop_detector.rs` |
| Goal 总结 | `session/goal_summarizer.rs` |
| 模型汇报 Goal | `xai-grok-tools/src/implementations/grok_build/update_goal/mod.rs` |
| Named Workflow | `session/workflow/`、`xai-workflow` |

---

## 4. 三套状态机先并排看

### 4.1 Plan Mode

```text
Inactive
  └─ toggle on ─→ Pending
                    └─ 首个 Prompt / 中途安全点 ─→ Active

Active
  ├─ agent exit approved ─→ Inactive
  └─ user toggle off during turn ─→ ExitPending
                                      └─ turn complete ─→ Inactive
```

### 4.2 Todo Item

```text
Pending → InProgress → Completed
    └───────────────→ Cancelled
```

代码没有强制只能按这些箭头迁移；模型可以按 ID 更新任意 status。上图只是推荐工作语义。

### 4.3 Goal Status

```text
                        ┌→ UserPaused ─────┐
                        ├→ BackOffPaused ──┤
Active ─────────────────├→ NoProgressPaused├─ /goal resume → Active
  ├─────────────────────├→ InfraPaused ────┤
  ├─────────────────────└→ Blocked ────────┘
  ├→ Complete
  └→ BudgetLimited
```

`Complete` 和 `BudgetLimited` 不能直接 resume；需要 clear 后开始新 Goal。

---

## 5. Prompt Mode 不是 Plan Mode

`PromptMode` 来自客户端 `_meta.mode`：

```rust
Agent
Ask
Plan
```

它主要表达本次 Prompt 的工作形态：

- `Agent`：预期完整工具和编辑；
- `Ask`：问答、只读；
- `Plan`：规划、只读。

`PromptMode::is_read_only()` 对 Ask 和 Plan 返回 true，主要影响 forked session 是否需要 worktree 等外围决策。

而 `PlanModeTracker` 是 Session 级可变状态，能跨 Prompt、持久化、恢复，并直接参与 Tool Call Gate。

所以：

```text
PromptMode::Plan
    ≠
PlanModeState::Active
```

一个是请求 metadata，一个是运行时状态机。

---

## 6. PlanModeTracker 为什么设计成纯状态机

`plan_mode.rs` 开头明确说明：

- 不引用 `SessionActor`；
- 不访问 Conversation；
- 不做 async I/O；
- SessionActor 在合适的生命周期点调用它。

好处是状态迁移可以独立测试：

- toggle；
- 首轮激活；
- mid-turn 激活；
- deferred exit；
- resume collapse；
- reminder alternation；
- approval 恢复。

Prompt 注入、文件检查和持久化由 Session 层完成，Tracker 只决定状态。

---

## 7. Plan Mode 的四个状态

### 7.1 `Inactive`

普通模式，没有 Plan Mode 写入限制。

### 7.2 `Pending`

客户端已开启，但还没有 Prompt：

- 模型尚不知道 Plan Mode；
- 还没有 Reminder；
- 还没有 Tool Call；
- 可以在首个 Prompt 前无痕取消。

### 7.3 `Active`

模型已收到 Plan Mode instruction，Tool Runtime 开始限制编辑。

### 7.4 `ExitPending`

用户在 Turn 进行中关闭 Plan Mode：

- 当前 in-flight request 不能被原地重写；
- 等当前 Turn 收敛；
- 再切到 Inactive；
- 下一 Turn 注入一次 exit reminder。

---

## 8. 为什么 `Pending` 很重要

如果 toggle on 立刻变 Active，但还没向模型注入 Reminder，会出现：

```text
Runtime 已禁止写入
模型却不知道自己进入了 Plan Mode
```

反过来，如果先写 Conversation 再等待用户真正提交 Prompt，又会污染一个可能立即取消的模式切换。

`Pending` 把“用户意图已改变”与“模型上下文已获得改变”分开。

---

## 9. Turn 开始时怎样激活 Plan Mode

`handle_prompt` 在组装用户消息期间调用：

```rust
inject_plan_mode_reminders().await
```

当状态为 Pending：

1. 检测是否 reentry；
2. `activate()` 切为 Active；
3. 检查 plan file 是否有内容；
4. 选择首次/重入模板；
5. 用当前 Tool 名称渲染；
6. 追加 System Reminder；
7. 记录 reminder count；
8. 持久化 snapshot。

状态迁移和上下文注入由同一个 Session 路径协调。

---

## 10. Mid-turn 开启 Plan Mode 怎样关闭竞态

用户可能在模型正在思考或执行 Tool 时按下 toggle。

`activate_plan_mode_mid_turn` 会：

1. 只接受 Pending；
2. 提前渲染 activation reminder；
3. 状态立即切 Active，因此后续 Tool Call 已受 Gate 约束；
4. 把完整 reminder 缓存在 Tracker；
5. 在下一 safe drain point 交付到 Conversation。

为什么不直接 push？

- 防止插入到一组 Assistant Tool Call 与 Tool Result 中间；
- toggle off 可以撤回尚未交付的 activation；
- 下一轮安全点再注入可保持历史结构合法。

---

## 11. Mid-turn activation 可以被干净撤回

`PendingActivation` 不只保存 reminder text，还保存：

```rust
prior_was_previously_active
```

如果用户在 reminder 真正送达模型前又关闭模式：

- buffer 被移除；
- 状态恢复 Inactive；
- `was_previously_active` 恢复旧值；
- 不生成 exit reminder。

因为模型从未知道进入过 Plan Mode，向它宣布“你已退出”反而是错误上下文。

---

## 12. Reminder 为什么 full / sparse 交替

Active 时每个 Turn 都注入 Plan Mode Reminder。

Tracker 用 `reminder_count` 交替：

```text
偶数 → full reminder
奇数 → sparse reminder
```

Full 包含：

- plan file 绝对路径；
- 当前是否已有内容；
- 可用 Edit Tool 名；
- Ask User / Exit Plan Tool 名；
- 只允许编辑 plan file 的规则。

Sparse 只重申只读约束，节省 token。

Compaction 后 count 重置，下一次一定使用 full，因为旧完整规则可能已被压缩。

---

## 13. `plan_file_has_content` 的一个细节

它使用：

```rust
metadata.len() > 0
```

因此 whitespace-only 文件在 Reminder 中算“有内容”。

而 `exit_plan_mode` 会 trim 后判断是否为空。

源码注释承认这一轻微差异；通常无害，因为 Enter Plan Mode 创建的是零字节 seed，不是空白文本。

---

## 14. Plan Mode 不能只靠 Prompt

Full Reminder 会告诉模型“不要写系统，只能写 plan file”。

但 Prompt 是软约束：

- 模型可能忘记；
- Tool 参数可能瞄准错误路径；
- Prompt injection 可能诱导越权；
- Always Approve 模式可能跳过普通 Permission UI。

所以真正的安全边界在：

```rust
plan_mode_edit_gate(...)
```

它位于 Tool Prepare 阶段，早于 Dispatch。

---

## 15. Plan Edit Gate 在 YOLO 模式下仍生效

源码注释特别强调：

> Plan mode is read-only in every permission mode, including always-approve.

Permission Manager 的 YOLO fast path 不知道 Plan Mode；因此必须先经过独立 Gate。

顺序是：

```text
Tool typed parse
→ AccessKind
→ Plan Mode Edit Gate
→ PreToolUse Hook
→ Permission
→ Dispatch
```

即使 Permission 最终会 Allow，Plan Mode Gate 也能先拒绝非 plan file 编辑。

---

## 16. 哪些动作会被 Plan Mode 硬拦截

当前核心判断是：

```rust
AccessKind::Edit(path)
    && path != tracker.plan_file_path()
```

则 `RejectNonPlanFile`。

结果：

- Tool 不执行；
- 写入模型可见 Tool Result；
- Turn 继续；
- 模型应改写 plan.md 或选择只读动作。

---

## 17. `apply_patch` 为什么在 Plan Mode 中总被拒绝

`apply_patch` 在 AccessKind 层映射到占位路径：

```text
Edit("apply_patch")
```

真实逐文件目标只有解析 Patch 后才知道，而 Gate 不冒险深度解析。

它不可能等于 plan file 绝对路径，因此保守拒绝。

这体现了安全规则：信息不足时不要假设写入安全。

---

## 18. 非 Edit Tool 并不都被 Plan Mode 禁止

`plan_mode_edit_gate` 只硬拦 `AccessKind::Edit`。

Read、Grep、Bash、MCP、Web 等仍进入正常 Permission 流程。

Prompt 可能要求只规划，但 Runtime 的这一个 Gate 并不是通用“所有副作用禁止器”。真正能执行什么仍受：

- Tool Catalog；
- AccessKind；
- Permission Policy；
- Hook；
- Sandbox；
- 具体 Tool 语义。

不要把 `PlanEditGate` 夸大成完整 capability sandbox。

---

### 源码阅读警报：这里存在“注释与实现漂移”

`plan_mode_edit_gate` 上方的长注释仍写着一条兼容工具集规则：`Write` / `StrReplace` 可以编辑任意 Markdown 文件。但当前函数体已经不再实现这条例外：

```rust
let _ = tool_input;
match access_kind {
    AccessKind::Edit(path)
        if !tracker.should_auto_approve_edit(Path::new(path)) => {
            PlanEditGate::RejectNonPlanFile
        }
    _ => PlanEditGate::Allow,
}
```

`let _ = tool_input` 说明具体 Tool 类型已被有意忽略；实际唯一判据是 `AccessKind::Edit` 中的路径能否通过 `should_auto_approve_edit`。同文件测试也明确断言：即使 `Write` 的目标是 `/tmp/README.md`，只要不是本 Session 的 `plan.md`，仍然必须拒绝。

因此，本篇按“可执行代码 + 测试”建立结论：**当前基线没有任意 Markdown carve-out，只有精确 plan file 例外。** 阅读大型代码库时，注释是线索，不是最终事实；当注释、实现与测试冲突，应优先追踪实际调用路径和可执行断言，并把这种漂移记录为后续清理项。

---

## 19. Plan file edit 为什么自动批准

`PlanModeTracker::should_auto_approve_edit` 只有在：

```text
state == Active
&& edit_path == plan_file_path
```

时返回 true。

Gate 与 auto-approval 使用同一个 path predicate，避免出现：

- Gate 认为合法；
- Permission 层却认为不是 plan file；
- 或反过来。

这是“单一判定源”在安全代码中的价值。

---

## 20. Exit Plan Mode 是一个审批协议

Agent 调用 `exit_plan_mode` 并不自动恢复写权限。

系统可能向用户显示计划审批 UI，并把：

```rust
awaiting_plan_approval
```

写入 Plan Mode snapshot。

已知 outcome 被 typed view 归一化：

```rust
Approved
Cancelled
Abandoned
```

未知值按 Cancelled 处理，即 fail closed：保持 Plan Mode，而不是误批准。

---

## 21. 为什么 ExitPlan Tool Call 要放在 batch 尾部

同一个模型 Response 可以产生多个 Tool Call。

`split_exit_plan_tail` 把 ExitPlan-kind 调用拆到尾部批次：

```text
普通 body Tool Calls
→ Exit Plan tail
```

否则 Exit Plan 可能与 plan file 最后一次编辑并发，导致审批读取旧计划或模式边界不确定。

---

## 22. Plan Mode 的恢复语义

`PlanModeSnapshot` 持久化：

- state；
- was_previously_active；
- reminder_count；
- pending_exit_reminder；
- awaiting_plan_approval。

不持久化：

- plan file path：由 session dir 重新计算；
- pending activation：它依赖 in-flight safe point。

恢复时瞬态状态折叠：

```text
Pending     → Inactive
ExitPending → Inactive + pending exit reminder
```

因为原来的客户端交互和 Turn 已不存在。

---

## 23. Todo 是独立于 Plan Mode 的结构化工作队列

Todo 核心类型：

```rust
TodoState {
    todos: IndexMap<TodoId, TodoItem>
}
```

每项包含：

```rust
content
priority
status
meta
```

Todo Tool 名通常为 `todo_write`，ToolKind 为 `Plan`，但它并不等于 Plan Mode 的 `plan.md`。

---

## 24. 为什么 TodoState 使用 IndexMap

Todo 既需要：

- 按 ID O(1) 更新；
- 保留插入顺序给 UI；
- 为 TodoGate 提供稳定的 backed/unbacked 分区顺序。

普通 HashMap 不保证顺序；Vec 更新 ID 又需线性扫描。`IndexMap` 同时满足两者。

---

## 25. Todo 的四种状态

```rust
Pending
InProgress
Completed
Cancelled
```

Priority 有：

```rust
High
Medium // 默认
Low
```

当前 `TodoWriteInput` 只提供 ID、content 和 status，创建时 priority 使用默认 Medium；结构保留 priority/meta，是为了 UI 和兼容投影不丢语义。

---

## 26. Todo Tool 为什么声明为 Read-only Capability

`TodoWriteTool.capabilities()` 返回 read scope。

这里的“read-only”指工作区权限层：它不修改用户项目文件。

它确实会修改 Session 内 Resources 状态，因此：

```text
workspace read-only
    ≠
runtime state immutable
```

Permission 分类描述的是外部资源风险，不是 Rust 对象是否发生变化。

---

## 27. `merge=false`：完整替换

Replace 路线会先：

```rust
state.clear()
```

再按输入顺序插入全部 Todo。

缺省规则：

- content 缺失/空 → 使用 ID 作为 fallback；
- status 缺失 → Pending；
- priority → Medium。

所以 `merge=false` 表达“这就是新的完整权威列表”。

---

## 28. `merge=true`：按 ID 局部更新

默认 `merge=true`。

对已存在 ID：

- content 缺失 → 保留旧 content；
- 非空 content → 替换；
- status 缺失 → 保留旧 status；
- status 存在 → 更新。

对新 ID：

- content 缺失 → ID fallback；
- status 缺失 → Pending；
- 插入到末尾。

模型可以只发送：

```json
{"id":"tests","status":"completed"}
```

而不用重复正文。

---

## 29. 自动升级为 Merge 的容错

即使模型显式或意外给出 replace 意图，只要：

- 当前 state 非空；
- updates 非空；
- 所有 update 都指向已有 ID；
- 所有 update 都没有 content；

Runtime 会把它视为明显的局部状态翻转，自动采用 merge。

否则一个“只把 tests 标记 completed”的调用可能误清空全部其他 Todo。

---

## 30. Duplicate ID 为什么是输出错误而不是基础设施错误

同一请求里重复 ID 会返回 `TodoWriteOutput::DuplicateId`。

这样模型能区分：

- 输入列表有逻辑错误；
- Tool Runtime 自身崩溃或资源缺失。

错误进入 Tool Result 后可由模型修正。

---

## 31. TodoState 的权威存储在哪里

新架构将其存入 ToolBridge Resources：

```rust
State<TodoState>
```

注册资源可序列化，并由 `ResourcesPersistence` 写入 Session 的：

```text
tool_state.json
```

旧的 `plan_state` sidecar/PersistenceMsg 仍有兼容痕迹，但运行时读取 Todo 的权威位置是 Resources。

---

## 32. Todo 输出怎样进入 UI

`TodoWriteSuccess` 同时返回：

- `summary_for_prompt`；
- `todos`；
- `state` snapshot。

Shell 的 `acp_plan_update` 将 Todo items 转成 ACP `PlanEntry`：

- 保留顺序；
- 投影 content、priority、status；
- ID 不直接存在于 ACP PlanEntry。

模型通过 Tool Result 看摘要，用户通过 Plan notification 看列表。

---

## 33. Turn-end Todo UI cleanup 不改变真实 Todo

如果模型最终回复时仍有 InProgress Todo，UI spinner 可能永远转动。

`emit_turn_end_plan_cleanup` 会临时发送一个 Plan notification：

```text
InProgress → UI 中显示 Completed
```

但源码明确保证：

- 不修改 `TodoState`；
- 不持久化这个 notification；
- Resume 后不会把它当事实；
- 模型下次仍看到真实 status。

这是 presentation cleanup，不是 state transition。

---

## 34. Todo 如何影响 Agent 是否结束

Todo 不只是 UI。

候选结束时 TodoGate 会检查：

- Pending；
- 没有 background task/subagent 支撑的 InProgress。

有未推进工作时注入 Reminder 并继续采样。

Goal 的 premature-stop detector 也会检查是否存在 Pending/InProgress Todo；只有还有工作时，诸如“稍后再来”“我先停在这里”的末段文本才会触发 Goal continuation signal。

---

## 35. Compaction 如何处理 Todo

Compaction 前会从 Resources 快照 Todo：

```text
id + content + status
```

把它作为运行态上下文的一部分交给压缩重建。

Compaction 后旧兼容 PlanState 会被清空，而 Resources 自身有独立 tool-state persistence。阅读这里要区分：

- 对话摘要中重新告诉模型的 Todo；
- ToolBridge 中真正的 TodoState；
- 旧 persistence sidecar 兼容字段。

---

## 36. Goal 的创建权在用户入口，不在 `update_goal`

`/goal <objective> [--budget N]` 解析为：

```rust
BuiltinAction::GoalSet {
    objective,
    token_budget,
}
```

`setup_goal` 创建 UUID、时间、token baseline、Git baseline commit，然后调用：

```rust
GoalTracker::create_goal(...)
```

模型侧 `update_goal` 只能：

- 写 progress message；
- 请求 completed；
- 声明 blocked_reason。

它不能自行创建或替换长期 Goal。

---

## 37. `/goal --budget` 的解析很保守

只有尾部独立形式才会被消费：

```text
/goal 修复全部测试 --budget 500000
```

要求：

- `--budget` 是独立 token；
- 值是最后一个 token；
- 全数字；
- 可解析为正 i64；
- objective 非空。

目标正文里只是提到 `--budget` 不会被静默截断。

---

## 38. 创建 Goal 时建立哪些基线

`setup_goal` 收集：

- `goal_id`：UUID；
- `objective`；
- optional token budget；
- `token_baseline`：创建时 Session total tokens；
- `created_at`；
- `baseline_commit`：有界 1 秒 `git rev-parse HEAD`。

此外 Tracker 创建：

- 12 hex `verifier_id`；
- owner-only scratch root；
- implementer scratch dir；
- Goal history；
- evaluator/verifier/strategist counters；
- plan、strategy 和 details artifact slots。

---

## 39. Goal 替换现有 Goal 时怎样清理

`create_goal` 会替换旧 orchestration。

替换前：

1. 尝试把用户已看到路径指向的 classifier details 从临时 scratch root 救到 durable goal dir；
2. 删除旧 scratch root；
3. 为新 Goal 生成独立 verifier ID 和目录。

这样新 Goal 不继承旧验证临时文件，也不会让已发出的 details pointer 立即失效。

---

## 40. GoalOrchestration 是长期 Goal 的完整快照

它至少包含这些逻辑组：

### 身份与生命周期

- goal_id、objective；
- status、phase；
- created_at、elapsed_ms；
- history。

### 预算

- token_budget；
- token_baseline；
- parent_tokens_spent；
- last_session_tokens_seen；
- tokens_used_high_water。

### 执行与 UI

- current_subagent_id/role；
- worker/verify rounds；
- live token/context/turn/tool counters；
- planning/verifying latches。

### 验证

- verifier_id；
- classifier attempts/cap/verdict/details/gaps；
- first final response；
- skeptic 0 session；
- skeptic model assignment；
- gap fingerprint/stall。

### 策略与计划

- plan file / baseline plan；
- strategist streak/bonus/path/recommendation；
- Git changes baseline。

---

## 41. Goal Phase 与 Status 是正交维度

`GoalPhase`：

```rust
Idle
Planning
Executing
```

`GoalStatus`：

```rust
Active
五类 paused
BudgetLimited
Complete
```

Phase 描述正在做哪一类工作；Status 描述整个 Goal 是否还能自主运行。

但当前生产主线创建 Goal 时直接设 `phase=Executing`；Planner/Verifier 的 UI 更依赖 `planning_in_flight`、`verifying_in_flight` latch。不要仅凭 Phase 判断界面是否显示 “Planning…” 或 “Verifying…”。

---

## 42. Goal History 是有界审计日志

`GoalEvent` 包含：

- GoalCreated；
- Planning/Worker start/complete/fail；
- ContextRotated；
- GoalPaused/Resumed/Completed/Cleared；
- BudgetExceeded；
- PrematureStopDetected。

每项可带：

- timestamp；
- detail；
- round；
- tokens used；
- unmet。

最多保留 64 项，超出删除最旧项，避免长期 Goal 的 snapshot 无限增长。

---

## 43. 未知持久化状态为什么恢复成 UserPaused

`GoalStatus::from_wire_str` 对未知值返回：

```rust
UserPaused
```

而不是 Active。

这是安全的 forward compatibility：旧二进制无法理解新状态时，最多要求用户 resume，绝不能把未知状态恢复成自动驾驶。

---

## 44. 恢复 Active Goal 时也先降为 UserPaused

`GoalTracker::from_snapshot` 会：

- Planning/Executing phase → Idle；
- 清 current subagent；
- Active status → UserPaused；
- 清 planning/verifying in-flight latch；
- 清 skeptic 0 resume id；
- 校验 verifier ID；
- 必要时重建安全 scratch root。

原因是：进程重启后旧 Turn、subagent、token accounting anchor 都不再可靠，不能自动假装原执行仍连续。

---

## 45. Goal Planner 的职责

Goal 创建后，如果 planner enabled 且有 coordinator，`maybe_run_goal_planner` 启动一个 harness-internal foreground subagent。

它必须：

- 研究 workspace；
- 将结构化计划写到 `<session>/goal/plan.md`；
- 包含可验证 outcome、acceptance criteria、task checklist、shared verification plan 等；
- 最终返回 `Done`。

真正成功条件不是终端文本，而是 plan file 存在且非空。

---

## 46. Planner 为什么是 fail closed

`GoalPlannerOutcome`：

```rust
Planned { plan_file, latency_ms }
FailClosed { reason, latency_ms }
```

Transport、runtime、cancel、无法写文件、缺失/空计划都 FailClosed。

调用方会暂停 Goal，并写 canonical message：

```text
Planning failed; resume with /goal to retry.
```

没有计划契约时继续自治，会让 verifier 缺少明确 judge target，因此宁可暂停。

---

## 47. Planner 的 `GOAL_PLANNER_MAX_RUNS=1` 只是 telemetry

Fresh Goal 的一次 setup 只尝试一次。

但用户每次 `/goal resume`，如果 Goal active 且 `plan_file` 仍为空，会再次运行 Planner。

源码注释明确：resume retries 不受这个常量限制。因此它不是 Goal 生命周期的绝对 planner attempt cap。

---

## 48. Planner 为什么强制使用父模型

当前实现把 Planner 作为 verbatim mirror-child fork：

- fork parent history；
- 为 prefix/cache reuse 强制父 Session model；
- 配置的 planner role model 会被忽略并记 breadcrumb；
- Tool 名按父 Toolset 动态渲染。

Strategist 与 Skeptic 可以有 role override，Planner 这条路径则优先保持 fork prompt/cache 同构。

---

## 49. Plan baseline 为什么不可变

Planner 成功后：

1. `o.plan_file = Some(plan.md)`；
2. 首次复制为 `plan.baseline.md`；
3. 后续 verifier 比较 baseline → current plan。

Agent 可以在执行中更新当前计划，但原始验收契约仍有不可变基线，便于发现目标漂移、删减标准或重新解释任务。

---

## 50. Goal Rules 怎样进入主模型上下文

`setup_goal` 解析实际 Tool 名：

- Goal Update；
- Task/Subagent；
- Todo。

随后渲染：

- objective；
- plan path；
- scratch dir；
- workflow/legacy 对应规则模板；
- “Start now”。

最终以 `<system-reminder>` 包裹，进入当前 User Turn 的模型上下文。

Goal 不是切换成另一个顶层 System Prompt，而是给当前 Session 注入结构化长期任务规则。

---

## 51. Goal 主执行者仍然是普通 Agentic Loop

Goal Harness 没有替代 Tool Runtime。

主模型仍然：

- 读取/搜索/编辑文件；
- 执行 Bash；
- 维护 Todo；
- 启动 subagent；
- 接收 Tool Result；
- 输出候选完成。

Goal 层只在外面增加：

- continuation discipline；
- completion evaluation；
- verifier panel；
- budget 和 pause policy；
- planner/strategist/summarizer roles。

---

## 52. 新 Goal 路线的隐藏 Evaluator

当主模型本轮没有 Tool Call并候选完成时，新路线运行：

```rust
evaluate_goal_round()
```

Evaluator 不是 coding Agent，而是一个隐藏 completion evaluator。

它只返回严格 JSON：

```rust
decision
evidence
next_step
blocker_key
```

Decision 有：

```rust
Continue
CandidateComplete
Blocked
```

---

## 53. Evaluator 看到的内容经过有界投影

`bounded_goal_transcript`：

- 排除 System；
- 排除 Reasoning；
- 排除 BackendToolCall；
- 保留 User、Assistant、ToolResult；
- 单 item 最多 4 KiB；
- 总 transcript 最多 32 KiB；
- 从最新向前选，再恢复时间顺序。

此外输入包含：

- objective；
- optional plan，最多 16 KiB；
- tool-free request；
- native JSON schema；
- 独立 request id。

Transcript 被明确标为 untrusted data，避免其中指令劫持 evaluator。

---

## 54. Evaluator 为什么优先小模型、失败再回退

它先尝试预设小型 suggest model；如果不可用或失败，再使用当前 active model。

最多两次尝试，并检查：

- Client 准备；
- 30 秒 timeout；
- Sampling error；
- usage 是否存在；
- JSON parse；
- 字段语义 validation。

两次都失败后，Goal 以 InfraPaused 停下，而不是把“无法评估”当完成。

---

## 55. Evaluator JSON 还有二次语义校验

除了 Schema，Rust 还验证：

- evidence 非空；
- next_step 非空；
- Blocked 必须有 blocker_key；
- blocker_key 只能 lowercase snake_case；
- Continue/CandidateComplete 必须没有 blocker_key；
- deny unknown fields。

结构合法不代表业务语义合法，因此二次 validation 很重要。

---

## 56. Evaluator 的三种决定怎样推进

### Continue

- 清 evaluator blocker streak；
- 记录 WorkerCompleted evidence；
- 生成 continuation directive。

### CandidateComplete

- 清 blocker streak；
- 记录 progress；
- 进入 adversarial verifier panel；
- 只有 verifier Achieved 才真正 Complete。

### Blocked

- 记录 evidence；
- 以稳定 blocker_key 累积 streak；
- 同一 blocker 连续 3 次后暂停 Goal；
- pause message 带 exact user action。

Evaluator 的 Blocked 不会一次就永久阻塞，减少偶发误判。

---

## 57. `blocker_key` 为什么必须稳定

Runtime 不是简单数“连续三次 Blocked”，而是比较：

```text
evaluator_blocker_key
```

相同 key：streak +1；不同 key：streak 重置为 1。

因此 prompt 要求 key 包含具体缺失 prerequisite 和受影响系统/资源，并在 blocker 不变时复用。

否则不同问题会被错误合并，或同一问题因文案变化永远无法达到暂停阈值。

---

## 58. Legacy Goal 路线由 `update_goal` 驱动完成申请

Legacy 路线中，模型调用：

```json
{"completed":true,"message":"..."}
```

Tool 不会即时声称成功，而是：

1. 创建 oneshot ack；
2. 通过 `GoalUpdateHandle` 发给 SessionActor；
3. 等待真实 classifier verdict/transition；
4. 把 Ack 精确转换成 Tool Output/Error。

这避免“Tool 已回 success，但 verifier 后来拒绝完成”的谎报。

---

## 59. `update_goal` 为什么始终暴露也可能返回 HarnessDisabled

Tool 和 GoalUpdateHandle 可被 Toolset 注册，即使当前没有 `/goal`。

如果模型在普通 Session 中误调用，drain 会返回：

```text
goal_update_harness_disabled
```

而不是丢掉 ack，导致含糊的 “response channel dropped”。

模型看见 Tool 不等于当前存在可更新 Goal。

---

## 60. `update_goal` 的三类输入

```rust
completed: Option<bool>
message: Option<String>
blocked_reason: Option<String>
```

- message-only：记录/回显进展，不结束；
- completed=true：申请验证完成；
- blocked_reason：声明外部阻塞；
- completed=false：基本等同无完成请求。

Tool 描述明确要求 blocked 只能在同一问题至少 3 次失败后使用。

---

## 61. blocked_reason 也有 Runtime 三次阈值

Session 使用 `goal_blocked_streak`：

- 第 1、2 次：Ack accepted，但要求继续尝试或调整；
- 第 3 次：如果 Goal 仍 Active，转为 Blocked；
- 同一 drain 后续命令因 `block_seen` 被拒绝。

这不是按 blocker reason fingerprint，而是连续 blocked signal 计数；与新 Evaluator 的稳定 blocker_key streak 是不同机制。

---

## 62. Mid-turn completion 为什么延迟到 Turn End

如果 `update_goal(completed=true)` 在父模型采样或 Tool batch 中途到达，不能立即启动 verifier subagents，否则会和 parent sampler 竞态。

`DrainPurpose::MidTurn` 会：

- 将 input 放进 pending queue；
- 立即 Ack `DeferredToTurnEnd`；
- 告诉模型不要重复调用；
- Turn End 按 FIFO 先处理这些 pending completions。

Ack 必须立即解析，不能把 Tool future 卡住等待同一个 actor 的 turn-end。

---

## 63. Pending completion queue 为什么上限是 4

常量：

```rust
GOAL_CLASSIFIER_PENDING_QUEUE_CAP = 4
```

满时移除最旧项，并记录 fail-closed event。

模型反复申请 completed 不能无限积累 verifier 工作。真正的防重复还包括 `goal_classifier_in_flight` atomic gate。

---

## 64. 并发 completion 申请怎样处理

如果 classifier 已在运行，第二个申请不会启动第二个 panel。

Runtime 将它记为 synthetic NotAchieved：

- 不调用 Sampler；
- 不消耗 verifier token；
- 仍占一次 attempt；
- 写 synthetic details；
- 可能因此触达 cap。

设计目的不是惩罚，而是为 completion spam 提供有界成本和明确反馈。

---

## 65. 新旧 Goal 路线的切换

外层通过：

```rust
goal_runs_on_workflow_engine()
```

选择：

```text
true  → run_goal_round_end()：隐藏 Evaluator 自动决定继续/候选完成/阻塞
false → run_goal_round_end_legacy()：依赖 update_goal drain 和 continuation
```

该函数当前直接返回 `background_workflows_enabled`。

这是迁移开关命名，不要仅凭名字推断 `/goal` 已被转换成 Named Workflow/Rhai run。

---

## 66. Goal Harness availability 也因路线而异

新路线开启时：

```text
goal_harness_enabled = goal_enabled
```

Legacy 路线则还要求实际 Toolset 中存在 `update_goal`。

如果恢复到 Active Goal，但 legacy Toolset 不再提供 update_goal，one-shot reconciliation 会将 Goal 安全暂停，提示注册 Tool 后 resume。

---

## 67. Verifier Panel 的目标不是再问一次“完成了吗”

Verifier 是 adversarial skeptic panel：

- 不相信主模型最终文案；
- 读取 objective 和 plan；
- 查看 Git baseline 到当前 worktree 的 diff；
- 查看 changed files；
- 检查 implementer scratch artifacts；
- 独立重跑 verification plan；
- 输出带 evidence/confidence/blocking/findings 的 JSON；
- 写各自 Markdown details。

Evaluator 是便宜的候选分类；Verifier 是昂贵的对抗证据审计。

---

## 68. Verifier 的证据包怎样构造

一次 stage 固定生成共享输入：

- sanitized final response；
- 首次 final response breadth anchor + 当前 round response；
- Git baseline → current diff patch；
- changed file 全列表；
- plan baseline → current plan changes；
- Goal kind lens；
- prior gaps；
- implementer scratch path；
- 每个 skeptic 独立 scratch 路径。

Diff 最多 256 KiB；Panel details 最多 512 KiB；路径必须位于经过验证的 owner-only scratch root。

---

## 69. 为什么 Git baseline capture 只有 1 秒

Goal 创建不能被非 Git repo、网络文件系统或卡住的 Git 阻塞。

因此 baseline capture 有 1 秒预算：

- 成功则 verifier 获得精确基线；
- 失败则标记 unavailable；
- Goal 仍能创建；
- Verifier 根据 workspace 和其他证据降级。

这属于 evidence enrichment，不是 Goal 启动的硬依赖。

---

## 70. 每个 Skeptic 的输出为什么 fail closed

Skeptic verdict JSON 要求：

- refuted；
- 非空 evidence；
- confidence；
- optional blocking/findings/details。

缺失、malformed、spawn failure 会合成：

```text
refuted = true
```

也就是说，单个 Judge 没有提供可信证据时不能贡献“通过票”。

但整套 verifier 基础设施完全不可用时，在旧 enum 名义上可能是 `FailOpenAchieved`；新调用路径会将这类 infra outcome 拦截并转为 InfraPaused，避免真正误完成。

---

## 71. Skeptic 数量怎样控制

默认：

```rust
GOAL_VERIFIER_SKEPTIC_COUNT = 3
```

配置被 clamp 到：

```text
1..=5
```

默认 3 的目标是让一个异常宽松或异常严格 Judge 不独自决定结果，同时控制并发成本。

---

## 72. Skeptic 0 是持久 Reject Gatekeeper

当 N > 1：

1. Skeptic 0 先单独运行；
2. 后续 attempt 尽可能 resume 其 child session；
3. 它能基于上次 gaps 做 delta re-check；
4. 其新 session id 被保存供下次继续。

当 N=1 时不 resume：唯一 Judge 若跨轮恢复，可能积累承诺偏差，变成越来越倾向通过的同一观察者。

---

## 73. High-confidence refute 可以短路

如果 Skeptic 0：

```text
refuted=true
confidence=high
blocking=普通可修复 gap
```

则跳过剩余 N-1 skeptics，直接不可 Achieved。

如果它声称 contradiction/unverifiable blocker，则仍 fan out 全 Panel，让其他 judges 判断：

- 全是不可修复 blocker → Blocked；
- 存在可修复 gap → NotAchieved。

无论 fan out 与否，高置信 refute 都绑定最终不能 Achieved。

---

## 74. 为什么 Skeptic 0 的通过票不计入冷 Panel 多数

当 total > 1，approval 需要：

```text
strict majority of skeptics with idx >= 1
```

Skeptic 0 是持续 resume 的 gatekeeper，更熟悉历史，也可能因之前的互动产生通过偏差。

因此：

- 它的 refute 仍计数、甚至可 decisive；
- 它的 not-refuted 不负责抬过 quorum；
- 真正通过需要 cold independent judges。

这是非对称的 adversarial bias。

---

## 75. Cold Panel quorum 怎样计算

对 total > 1：

```rust
cold_count = count(idx >= 1)
needed = cold_count / 2 + 1
achieved = cold_not_refuted >= needed
```

典型值：

| 总 N | Cold 数 | 所需 Cold 通过 |
|---:|---:|---:|
| 2 | 1 | 1 |
| 3 | 2 | 2 |
| 4 | 3 | 2 |
| 5 | 4 | 3 |

如果结果里意外缺少 skeptic 0，仍按真实 cold size 算严格多数，不退化成 plurality。

---

## 76. Achieved、NotAchieved、Blocked 的区别

### Achieved

- quorum 通过；
- 没有 decisive refute；
- 保存 verdict/details；
- Goal Complete；
- 清 gaps、strategist state；
- 可运行 Summarizer。

### NotAchieved

- 至少有可由模型修复的 gap；
- 生成 gaps summary；
- 计算 fingerprint；
- 下一 continuation 把 gaps 内联给主模型。

### Blocked

- 至少一个 refuter；
- 所有 refuter 都是 contradiction/unverifiable；
- 没有 model-fixable gap；
- Goal 暂停等待用户决定。

---

## 77. 为什么一个 blocking refuter 就能触发 Blocked

Blocked 是可 resume 的暂停，不是通过或永久失败。

如果一个高可信 Judge 提出环境不可验证或目标矛盾，而没有任何 Judge 指出可由模型修复的 gap，继续自动迭代只会烧预算。

过度暂停可由用户低成本 resume；对不可修复问题无限 nudge 则成本更高。

---

## 78. Gap Summary 怎样进入下一轮

真实 NotAchieved 会：

```rust
last_classifier_gaps = Some(curated_summary)
```

`prepare_goal_continuation` 在每轮 directive 中持续内联最新 gaps，直到：

- 新 verdict 覆盖；
- Achieved 清除；
- Blocked route 清除；
- Goal 重建。

模型不必主动读 details file 才知道最关键的修复点；完整报告仍保留给深挖和用户审计。

---

## 79. Gap Fingerprint 为什么不直接 hash 全文

Verifier 文案可能因：

- scratch 临时路径；
- Panel 顺序；
- confidence wording；
- 大小写；
- 重复 finding；

发生无关变化。

`gap_fingerprint` 抽取和归一化 evidence/path:line token，忽略易变装饰。

目标是判断“实质 gap 是否没变”，不是判断报告字节是否相同。

---

## 80. 相同 Gap 连续两次为什么自动暂停

普通阈值：

```rust
GOAL_CLASSIFIER_STALL_THRESHOLD = 2
```

第一次 fingerprint A：count=1；第二次仍 A：count=2 → NoProgressPaused。

这比等到默认 10 次 classifier cap 更便宜，因为证据表明主模型没有改变被指出的问题。

Fingerprint 变化会把 count 重置为 1。

---

## 81. Strategist 解决的是“不同 Gap 轮流出现”

仅看相同 fingerprint 抓不到 whack-a-mole：

```text
round 1 修 A，暴露 B
round 2 修 B，又破坏 C
round 3 修 C，又回到 D
```

因此还有：

```rust
consecutive_not_achieved
```

它不关心 gap 是否相同。达到配置 cadence N 后触发 Strategist，之后每再前进 N 次可再次触发。

---

## 82. Strategist trigger 为什么不用 `% N == 0`

判定是：

```rust
consecutive >= last_fired + every
```

而不是：

```rust
consecutive % every == 0
```

因为 synthetic concurrent completion 可能让计数从 N-1 跳到 N+1。严格等于倍数会永远漏掉这次触发。

当前形式是 skip-robust。

---

## 83. Claim Strategist Fire 是原子状态迁移

`claim_strategist_fire` 在同一把 Tracker lock 下：

1. 检查 predicate；
2. 记录 last fired streak；
3. 授予 classifier cap bonus；
4. 重置 gap stall fingerprint；
5. 返回当前 consecutive count。

检查和 claim 不分离，避免两个并发路径为同一 streak 启动两次 Strategist。

---

## 84. Strategist 获得三个额外验证机会

常量：

```rust
GOAL_STRATEGIST_CAP_BONUS = 3
```

一旦成功 claim，effective classifier max：

```text
configured max + 3
```

并把相同 gap stall threshold 从 2 放宽到：

```text
2 + 3 = 5
```

重构策略需要几轮才能显现，不能刚给建议就被原来的窄保险丝立即暂停。

Bonus 不叠加。

---

## 85. Strategist 为什么 fail open

它只是 advisory role，不是完成证明。

Transport、runtime、cancel、missing strategy file 都返回：

```rust
GoalStrategistOutcome::FailOpen
```

Goal 继续执行，不因辅助顾问失败而暂停。

若没有成功交付 restructure，Drop Guard 会撤销预先授予的 cap bonus，防止失败顾问仍扩大成本预算。

---

## 86. Strategist 可以读什么、写什么

它读取：

- objective；
- current plan；
- Session trace dir；
- Goal scratch root；
- 失败轮数。

它应把建议写入：

```text
<session>/goal/strategy.md
```

而不是改 `plan.md`。

成功后 Runtime 读取有界 recommendation，保存 path 和正文，下一 continuation 内联给主模型一次。

---

## 87. PlanGuard 怎样保护 verifier contract

Strategist 运行前：

- 用 `symlink_metadata` 检查 plan.md；
- 若普通文件，保存全部 bytes；
- 若不存在，记 Absent；
- 若 symlink/读取异常，记 Unsafe。

运行后：

- 原文件被改 → byte-for-byte 恢复；
- 原本不存在但被创建 → 删除；
- 路径变 symlink → 拒绝跟随并报警；
- future 被取消 → Drop 中仍尝试恢复。

Strategist 能提建议，但不能偷偷重写被 Verifier 审判的契约。

---

## 88. Strategist recommendation 为什么只消费一次

Continuation 构造时读取：

```rust
last_strategy_recommendation
```

真正提交 directive 后，`consume_strategist_note` 清：

- recommendation；
- strategy path。

避免同一策略提示在每个后续 round 重复占 token、压过更新后的 verifier gaps。

---

## 89. 下一步怎样从 plan.md 提取

`resolve_goal_next_step` 最终调用 `first_unchecked_plan_item`：

- 最多读 8 KiB；
- 截断时删除可能不完整的末行；
- 识别 `- [ ]`、`* [ ]`、`+ [ ]`；
- 跳过 `[x]` / `[X]`；
- 优先只读 `## Task checklist`；
- 否则扫描全文件，但排除 Non-goals 和 Deviations；
- 不从 Acceptance criteria 提取。

找不到时 fallback 为“检查 todo list”。

---

## 90. Acceptance Criteria 为什么不能当“下一步”

Acceptance Criteria 描述最终必须满足什么，通常不会被勾选。

如果把 criterion 1 当下一步，每轮都会重复同一句抽象要求，无法推进执行。

Task Checklist 才是可消费的动作队列；这也是 Planner Prompt 强制两类 section 分离的原因。

---

## 91. Premature Stop Detector 只检查末段

Goal stop detector 识别：

- unable to proceed；
- giving up；
- stopping here；
- agents still in flight；
- check back later；
- commit/push/PR handoff；
- ready for review；
- please-do-it-yourself deflection 等。

它主要检查最后一个非空 paragraph，避免正文中引用这些短语就误触发。

只有 Todo 仍 Pending/InProgress 时才把匹配当作提前停止信号。

---

## 92. Continuation Directive 包含什么

每轮 continuation 汇总：

- objective；
- tokens used；
- elapsed；
- premature-stop preface；
- plan pointer；
- verifier gaps；
- strategist recommendation；
- reverify escalation；
- next concrete plan item；
- Todo Tool 名；
- update_goal Tool 名（legacy）；
- implementer scratch dir。

它以 `GoalSummary` synthetic User item 注入，并先删除旧 continuation，避免累积多份过时 directive。

---

## 93. Goal Reverify escalation

Tracker 维护：

```rust
rounds_since_verify
```

每次构造 continuation +1；预约 classifier attempt 时清 0。

当已经被 refute 且继续工作达到配置轮数，directive 会强化“再次提交验证”的要求。

这避免主模型修复 gaps 后永远只继续做事，却不重新进入完成验证。

---

## 94. Goal Token 预算包含哪些来源

`goal_tokens` 合计：

```text
parent_tokens_spent
+ 所有 Goal-scoped subagent marginal tokens
```

Subagent 包括：

- finished；
- in-flight。

用于 pager 的 `finished_subagent_tokens` 只含 sealed records，因为 UI 会另外叠加 live subagent tokens，避免双算。

---

## 95. 为什么不能简单用 `current - baseline`

Auto Compaction 可能让 Session current token total 下降。

简单差值会：

- 预算使用量倒退；
- 或直到重新超过旧峰值前冻结。

当前实现只累加正 delta：

```rust
if current > last_seen {
    parent_tokens_spent += current - last_seen
}
last_seen = current
```

再用 `tokens_used_high_water` 单调 ratchet。

---

## 96. Token 计数为什么仍是 best effort

如果两次 `goal_tokens` 调用之间：

1. context 增长很多；
2. 又被 Compaction 消耗；
3. 最终 current 没有比 last_seen 高；

中间增长不会被观察到。

此外 crash 前尚未持久化的增量可能丢失。

所以它保证“不倒退、尽量累计”，不是 billing-grade 完整账本。

---

## 97. BudgetLimited 为什么不是普通 Pause

达到 budget 时：

- Goal `budget_limit()`；
- phase → Idle；
- 清 active subagent fields；
- 清 verifier resume/model assignment/plan baseline；
- 清 strategist/evaluator state；
- rescue details；
- 删除 scratch root；
- 记录 BudgetExceeded；
- 通知用户 clear 后创建新 Goal。

`BudgetLimited` 不属于 `is_paused()`，因此 `/goal resume` 不会直接恢复并绕过预算。

---

## 98. 五种 Pause 分别表达什么

| 状态 | 原因 | 可 Resume |
|---|---|---:|
| UserPaused | 用户 pause、Ctrl+C、Planner fail 等 | 是 |
| BackOffPaused | verifier attempt cap | 是 |
| NoProgressPaused | 相同 gap fingerprint 达阈值 | 是 |
| InfraPaused | evaluator/verifier/turn 基础设施失败或 refusal | 是 |
| Blocked | 需要用户动作或不可修复验证阻塞 | 是 |

`pause_message` 与 status 正交，主要用于保留 Blocked/Infra 等具体人类可读原因。

---

## 99. Pause 怎样停止计时

Goal Tracker 保存：

```rust
active_since: Option<Instant>
elapsed_ms
```

Pause 时：

1. 将 active_since 到当前的 duration 累加到 elapsed_ms；
2. active_since 置 None；
3. status 变具体 paused variant；
4. 写 GoalPaused history。

Paused 期间 wall time 不计入 active elapsed。

---

## 100. Resume 重置哪些尝试级状态

任意 paused variant resume 时：

- status → Active；
- 清 pause_message；
- classifier_runs_attempted → 0；
- rounds_since_verify → 0；
- 清 strategist streak/note/bonus；
- 清 gap fingerprint stall；
- 清 evaluator blocker streak；
- 重新开始 active_since；
- 记录 GoalResumed。

用户 resume 是一次明确 re-arm，重新给予尝试预算。

---

## 101. Resume 保留什么

不会全部重建 Goal。通常保留：

- goal ID、objective；
- plan.md；
- durable history；
- Git baseline；
- last verifier details；
- first final response anchor；
- skeptic model assignment；
- skeptic 0 session id（进程内 pause/resume；进程重启会清）。

因此 resume 不是从零开始，而是清“失败计数器”，保留工作成果与审计证据。

---

## 102. Active Goal 上执行 `/goal resume` 是 Nudge

如果 Goal 已 Active：

- 不做 status transition；
- 返回 “Goal nudged — refreshing context”；
- 重置 continuation/blocked streak；
- 重新注入完整 Goal rules/state；
- 进入 inference。

用户可以用 resume 让看似卡住但仍 Active 的 Goal刷新上下文，而不必先 pause。

---

## 103. `/goal pause` 与 `/goal clear` 的差异

### Pause

- 只接受 Active；
- 保留 orchestration、plan、history 和 artifacts；
- 清 pending classifier completions；
- 可 resume。

### Clear

- 先要求 Persistence Actor 确认 durable goal state 已删除；
- 删除失败则内存 Goal 仍保留；
- 成功后才清 Tracker、token records、task IDs、pending completions；
- 发送 GoalCleared notification。

Clear 使用“先耐久删除、后内存清理”，避免 UI 说已清但磁盘恢复出旧 Goal。

---

## 104. Complete 时怎样收尾

`GoalTracker::complete()`：

- status → Complete；
- phase → Idle；
- 清 current subagent；
- 清 pause_message；
- 清 skeptic 0 resume id；
- 清 model assignment；
- 清 plan baseline reference；
- 清 strategist/evaluator state；
- rescue last classifier details；
- 删除 scratch root；
- 记录 GoalCompleted。

当前 plan.md 仍可作为 durable session artifact；临时 verifier scratch 被清理。

---

## 105. Summarizer 为什么在 Goal Complete 之后运行

Goal Summarizer 的职责只是生成一次更好的用户最终消息。

它：

- 只读；
- 最佳努力；
- 可以读取 objective、plan、details 和 traces；
- 失败不会撤销 Complete；
- 不拥有 verification 权力。

先完成状态迁移，再运行 presentation role，可以防止总结器故障把已证明的成果重新变成未完成。

---

## 106. Role Model Override 如何选择

Strategist 和 Skeptic 可配置 `{model, agent_type}`。

父侧会检查：

- model 是否在 catalog；
- user selectable/authorized；
- harness flavor 是否可表示；
- subagent type 是否可用；
- Toolset 是否 allowed/enabled；
- Role 所需 capability 是否满足。

Skeptic 至少要 read + search；Strategist 还需 execute。

失败时回退继承当前 model/session harness，并记录 typed reason。

---

## 107. Explicit Role Spawn 失败为什么再用父配置重试一次

`spawn_with_fail_open_retry`：

- inherit path：只尝试一次；
- explicit override：先用配置 pair；
- 非 cancellation failure：用 current model + session harness 再试一次；
- cancellation：不重试；
- retry prompt 重新按 fallback Toolset 名称渲染。

这样远程角色配置故障不会立即破坏 Goal，但用户取消也不会被系统偷偷重新启动。

---

## 108. Skeptic Model Assignment 为什么冻结

配置的 skeptic pool 按 index round-robin 展开。

第一次 panel 后保存：

```text
index → model + agent_type
```

后续：

- 已提交 index 不重写；
- N 增大只追加；
- remote pool 被清空时已有 assignment 仍保留；
- Goal terminal transition 才清。

这保证 skeptic 0 resume 时仍是同一模型，也让跨 attempt 对比更稳定。

---

## 109. Goal Notification 为什么有 durable 和 ephemeral 两条路

`GoalNotifySender` 可以：

- `emit_goal_updated`：持久化 snapshot + update，并广播；
- `persist_goal_state`：只持久化 snapshot；
- `emit_goal_updated_ephemeral`：只广播 live tick；
- `send_update`：持久化和广播指定 update。

高频 subagent progress 不写 JSONL，避免无限增长；下一次状态迁移会持久化权威 totals，UI 也会被后续 tick 自愈。

---

## 110. Wire `GoalUpdated` 是状态投影，不是权威对象

它投影：

- status/phase string；
- token budget/used；
- elapsed；
- worker/verify rounds；
- live subagent/context/tool stats；
- pause message；
- classifier counters/verdict/details；
- planning/verifying badge；
- last history event。

多模型 token breakdown 只有至少两个 model 时才发送，单模型 UI 保持简洁。

权威状态仍是 `GoalOrchestration` snapshot。

---

## 111. Goal 的并发安全点

几个关键保护：

- GoalTracker 纯同步状态放在 Mutex；
- 不跨 subagent `.await` 持有 tracker lock；
- planner/verifier latches 用 Drop Guard 清理；
- classifier in-flight 用 AtomicBool；
- mid-turn completion 延迟到 turn-end；
- pending queue 有上限；
- status 在 verifier 返回后再次核对；
- clear 先等 durable ack；
- strategist plan restore 用 RAII guard；
- role spawn cancellation 不自动重试。

Goal 是多模型、多 actor 协作，安全主要来自边界处重复验证状态。

---

## 112. 为什么 Verifier 返回后还要检查 Status

Verifier 运行期间用户可能：

- `/goal pause`；
- clear；
- cancel Turn；
- 触发其他 terminal transition。

因此 outcome 返回后必须确认 Goal 仍 Active。

如果 status 已变：

- 不应用旧 verdict；
- Ack `StatusChangedDuringClassifier`；
- Guard 回滚 attempt/latch；
- 用户的新决定优先。

异步结果不能覆盖更新后的权威状态。

---

## 113. Named Workflow 是另一套编排系统

`session/workflow/` 提供：

- Workflow Registry；
- Rhai script；
- Host service；
- Journal；
- WorkflowManager；
- WorkflowTracker；
- background run；
- pause/resume/cancel/save；
- agent lease/budget；
- 多 active runs。

它可以运行 `/deep-research` 等命名流程，但不是 `GoalTracker` 的同一个状态对象。

---

## 114. `goal_runs_on_workflow_engine` 的命名陷阱

当前函数只是：

```rust
pub fn goal_runs_on_workflow_engine(&self) -> bool {
    self.background_workflows_enabled
}
```

它选择 Goal 新旧 round-end orchestration：

- 新：隐藏 Evaluator 自动推进；
- 旧：update_goal 驱动 classifier drain。

在本基线中，`/goal` 仍由 GoalTracker、Planner、Evaluator、Verifier、Strategist 代码运行，并没有调用 `WorkflowManager::launch` 创建一个 `wf_*` run。

命名反映迁移方向/feature family，不等于对象身份。

---

## 115. Named Workflow 的状态比 Goal 更通用

`WorkflowRunStatus` 包含：

- Active；
- 五类 pause；
- BudgetLimited；
- Interrupted；
- Complete；
- Failed；
- Cancelled。

它还维护：

- revision；
- phases/current phase；
- agent budget/agents used；
- token leases；
- journal path；
- result summary；
- agent rows；
- execution epoch。

Goal 专注于一个当前自主目标；Workflow 支持一个 Session 中最多 4 个 active/retiring runs。

---

## 116. Workflow Budget 是 Agent 数，不是 Goal Token 数

Named Workflow 的主要预算字段：

```text
agent_budget
agents_used
```

默认由 `xai_workflow::DEFAULT_AGENT_BUDGET` 提供。

BudgetLimited resume 时必须提高 absolute cap，且不能超过 global max。

Goal 的 budget 则是 token budget，合计 parent + subagent marginal。两种“预算”单位完全不同。

---

## 117. Workflow Resume 为什么保留 immutable launch args/script

恢复 run 时：

- 原 args 必须与新 spec 一致；
- script 从 run store 读取原版本；
- Journal 从固定路径加载；
- Failed run 可 prune trailing host error；
- agent reservation count 与 tracker reconcile；
- execution epoch 防迟到结果污染。

Resume 是继续同一个声明式运行，不是用同名 Workflow 启动一个新任务。

---

## 118. Goal、Todo、Workflow 怎样组合

一个 Goal Turn 中可以：

- 用 Todo 管理主模型短期步骤；
- 用 Task Tool 启动 subagent；
- 用 Named Workflow Tool 启动独立后台 run；
- Goal Harness 自己再启动 hidden planner/skeptics/strategist。

但归属必须区分：

- Todo 状态不会自动成为 Workflow phase；
- Workflow complete 不会自动让 Goal complete；
- Goal verifier 必须审查最终 objective evidence；
- background work 可能只让 TodoGate 把 InProgress 视为 backed。

---

## 119. 一次新 Goal 的完整时间线

```text
用户: /goal 修复所有 flaky tests --budget 200000
  ↓
解析 objective/budget
  ↓
capture token + git baseline
  ↓
GoalTracker::create_goal(Active, Executing)
  ↓
Planner subagent 写 goal/plan.md
  ├─ 失败 → UserPaused
  └─ 成功 → snapshot plan.baseline.md
  ↓
注入 Goal Rules + objective + paths
  ↓
主 Agentic Loop 执行 / Todo / Tool / Subagent
  ↓
候选结束
  ↓
Evaluator
  ├─ Continue → directive → 下一轮
  ├─ Blocked × 同 key 三次 → Blocked
  └─ CandidateComplete → Skeptic Panel
                         ├─ Achieved → Complete → Summarizer
                         ├─ NotAchieved → gaps → 下一轮
                         └─ Blocked → pause 等用户
```

---

## 120. 一次 Legacy Goal completion 时间线

```text
主模型调用 update_goal(completed=true)
  ↓
Tool 把 input + oneshot ack 发给 SessionActor
  ↓
若 mid-turn：先 DeferredToTurnEnd
  ↓
Turn-end FIFO drain
  ↓
reserve classifier attempt
  ↓
Skeptic Panel
  ├─ Achieved → ack success + Complete
  ├─ NotAchieved → ToolError，模型继续
  ├─ cap/stall/blocked → ToolError + auto-pause
  └─ concurrent → synthetic NotAchieved
```

Tool Result 能准确反映最终状态，而不是乐观预确认。

---

## 121. 一次相同 Gap stall 时间线

```text
Attempt 1
  Panel: NotAchieved, fingerprint=A
  stall_count=1
  continuation includes gaps A

主模型修改，但没有解决 A

Attempt 2
  Panel: NotAchieved, fingerprint=A
  stall_count=2
  → NoProgressPaused

用户检查 details 后 /goal resume
  → attempts/stall/strategist reset
  → plan、worktree、history 保留
```

---

## 122. 一次 Strategist 重构时间线

```text
连续 NotAchieved 达 cadence N
  ↓
claim fire + cap bonus 3 + reset fingerprint
  ↓
Strategist 读取 traces/plan/scratch
  ↓
PlanGuard 阻止它改变 plan.md
  ↓
写 strategy.md
  ├─ 失败 → 撤销 bonus，Goal 继续
  └─ 成功 → 保存 recommendation
  ↓
下一 continuation 内联 recommendation 一次
  ↓
主模型按新策略重构
```

---

## 123. 调试“Plan Mode 为什么还写了文件”

依次检查：

1. `PromptMode` 还是 `PlanModeState`？
2. Tracker 是否 Active，而不是 Pending/ExitPending/Inactive？
3. mid-turn activation reminder 是否交付？
4. ToolInput 映射出的 AccessKind 是什么？
5. 动作是否根本不属于 `AccessKind::Edit`？
6. path 是否恰好等于 plan file；
7. Tool 是否 Hosted/Server-side，绕过本地 Gate；
8. 是否通过 Bash 间接写文件；
9. Permission/Sandbox 是否另有限制；
10. Plan Mode Prompt 和 Runtime Gate 是否覆盖同一 Toolset。

PlanEditGate 只硬拦本地 typed Edit，不是通用无副作用证明。

---

## 124. 调试“Todo 为什么被清空”

检查：

1. 输入 `merge` 最终值；
2. 是否发送完整 replace list；
3. 自动 merge 容错条件是否满足；
4. update 是否都指向 existing ID；
5. content 是否真的缺失；
6. 是否在 Compaction/Resume 中读取了错误 sidecar；
7. ToolBridge Resources 是否载入 tool_state.json；
8. 是否 fork 时禁用了 copy_tool_state；
9. UI cleanup 是否仅改变显示；
10. ACP PlanEntry 没有 ID 是否导致客户端错误关联。

---

## 125. 调试“Goal 为什么一直继续”

检查：

1. status 是否 Active；
2. 新路线 Evaluator decision/evidence/next_step；
3. legacy route 是否从未调用 `update_goal(completed=true)`；
4. Todo 是否仍 Pending/InProgress；
5. premature-stop detector 是否触发；
6. verifier 是否持续 NotAchieved；
7. last gaps 是否每轮变化，绕过相同 fingerprint stall；
8. strategist cadence/bonus；
9. classifier max 是否被 bonus 提高；
10. token budget 是否 None 或很大；
11. Stop Hook 在 Goal 后，因此 active Goal continuation 会先运行；
12. background tasks 是否让 Todo 看起来仍被支撑。

---

## 126. 调试“Goal 为什么突然暂停”

先看具体 status：

- UserPaused：用户动作、Planner fail、兼容 reconciliation；
- BackOffPaused：classifier cap；
- NoProgressPaused：相同 gap fingerprint；
- InfraPaused：Evaluator/Verifier/Turn error、Provider refusal；
- Blocked：Evaluator stable blocker 或 Panel 全部不可修复 blocker。

再看：

- pause_message；
- last history event/detail；
- classifier details；
- evaluator blocker key/streak；
- planner/verifier events；
- pending completion queue；
- status 是否在 async verifier 中途被用户改变。

---

## 127. 调试“Verifier 为什么不同意完成”

查看：

1. objective 原文；
2. plan baseline 与 current plan；
3. final response breadth anchor；
4. Git baseline 是否 available；
5. changed files 是否完整；
6. patch 是否截断；
7. implementer scratch 是否有验证证据；
8. per-skeptic verdict JSON；
9. per-skeptic Markdown details；
10. confidence/blocking/findings；
11. skeptic 0 是否 decisive high refute；
12. cold quorum；
13. malformed output 是否被 synthetic refute；
14. path safety 是否让 stage infra fail。

不要只看 aggregate headline。

---

## 128. 调试“Goal Token 为什么和 Session Token 不一样”

检查：

- Goal 创建时 baseline；
- parent positive-delta accumulator；
- last_session_tokens_seen；
- tokens_used_high_water；
- finished 和 in-flight subagent marginal；
- resume anchor cumulative；
- live UI 是否又叠加 running tokens；
- Compaction 是否改变 Session context total；
- crash 是否发生在 snapshot cadence 之间；
- per-model breakdown 是否因只有一个 model 被折叠。

Goal Token 是自治成本估计，不是当前 context size。

---

## 129. 适合下断点的最小路径

```text
PlanModeTracker::enter_pending / activate / user_exit
SessionActor::inject_plan_mode_reminders
plan_mode_edit_gate
TodoWriteTool::run
evaluate_todo_gate
SessionActor::setup_goal
GoalTracker::create_goal / pause / resume / complete / budget_limit
SessionActor::maybe_run_goal_planner
SessionActor::evaluate_goal_round
SessionActor::run_goal_round_end
SessionActor::verify_goal_candidate
run_verification_stage
aggregate_skeptic_verdicts
GoalTracker::record_classifier_stall
GoalTracker::claim_strategist_fire
SessionActor::maybe_run_goal_strategist
SessionActor::goal_tokens
SessionActor::resume_goal
```

关键变量：

```text
plan_mode.state
TodoState.todos
goal.status / phase
classifier_runs_attempted
rounds_since_verify
last_classifier_gaps
last_gap_fingerprint / classifier_stall_count
consecutive_not_achieved / last_strategist_fired_at
strategist_cap_bonus
evaluator_blocker_key / streak
parent_tokens_spent / high_water
```

---

## 130. 测试中最值得记住的不变量

### Plan Mode

- Pending 可以无痕取消；
- mid-turn reminder exactly once；
- 未交付 activation 可撤回；
- ExitPending 完成后 one-shot exit reminder；
- Compaction 后 full reminder；
- 恢复时瞬态状态折叠；
- plan file path exact match。

### Todo

- replace 清旧列表；
- merge 保留 content；
- lost state 使用 ID fallback；
- duplicate ID 返回 typed output；
- state snapshot 与 Resources 一致。

### Goal Tracker

- Active restore → UserPaused；
- unknown status → UserPaused；
- pause elapsed accounting；
- resume reset attempts/streaks；
- terminal cleanup；
- history cap；
- verifier ID/path safety。

### Verifier

- JSON evidence mandatory；
- malformed → refute；
- cold quorum table N=1..5；
- skeptic 0 pass 不计 quorum；
- decisive refute；
- all-blocking route；
- fingerprint 对临时路径/排序稳定。

### Strategist

- skip-robust cadence；
- fail-open；
- cap bonus revoke；
- plan bytes restore；
- symlink tamper refusal；
- recommendation 有界且一次消费。

---

## 131. 设计优点一：执行、计划和完成证明分权

主模型负责实现；Planner 负责构造契约；Evaluator 负责便宜筛选；Skeptics 负责对抗验证；Strategist 只给建议；Summarizer 只负责表达。

没有一个模型同时拥有：

- 定义标准；
- 写实现；
- 宣布完成；
- 审核证据；
- 修改审核标准。

这种角色分离降低自我确认偏差。

---

## 132. 设计优点二：软纪律与硬状态机组合

Prompt/Reminder 负责：

- 教模型怎样规划；
- 指出下一步；
- 说明何时申请验证；
- 展示 gaps 和 strategy。

Runtime 负责：

- 拒绝 Plan Mode 非法编辑；
- 强制 Goal status transition；
- 计数 budget/cap/stall；
- 校验 Evaluator JSON；
- 计算 verifier quorum；
- 阻止迟到异步结果；
- 持久化和恢复。

能被机器验证的约束不会只留在 Prompt 中。

---

## 133. 设计优点三：失败语义按角色区分

| 角色/机制 | 失败策略 | 原因 |
|---|---|---|
| Planner | Fail closed，暂停 | 没契约不能安全自治 |
| Evaluator | Fail closed 为 InfraPaused | 不能把无法判断当完成 |
| 单个 Skeptic | Fail closed 为 refute | 没证据不能贡献通过票 |
| 整体 verifier infra | 新路径转 InfraPaused | 基础设施失败不能误完成 |
| Strategist | Fail open | 辅助建议失败不应阻止执行 |
| Summarizer | Fail open | 表达失败不撤销已验证完成 |
| Named Workflow notification | 多为异步可恢复 | 权威状态在 tracker/journal |

“Fail open/closed”必须先问：对哪个安全目标？

---

## 134. 设计代价一：新旧 Goal 路径同时存在

当前同时有：

- `update_goal` Ack/queue/drain/classifier 逻辑；
- 新 `GoalEvaluatorDecision` 自动 round-end 逻辑；
- `goal_runs_on_workflow_engine` feature switch；
- 名字保留 classifier 前缀的 skeptic panel；
- 独立 Named Workflow 系统。

维护者很容易根据旧名字误判当前调用关系。

阅读时应先定位 feature flag 和实际 caller，再解释类型名。

---

## 135. 设计代价二：同一“卡住”有多套计数器

- `goal_blocked_streak`：legacy 模型 blocked_reason；
- evaluator blocker streak：稳定外部 blocker key；
- classifier stall count：相同 gap；
- consecutive NotAchieved：任何 verifier rejection；
- continuation streak/backoff；
- classifier attempt cap；
- Goal token budget；
- Stationarity：相同 Tool Call。

它们观察的信号不同。合并成一个数字会丢语义，但独立存在又增加调试成本。

---

## 136. 设计代价三：完成验证成本很高

一次 CandidateComplete 可能包含：

- Evaluator 模型调用；
- Git diff；
- Skeptic 0；
- N-1 cold skeptics 并发；
- 多次 Tool verification；
- details artifacts；
- 失败后 Strategist；
- 成功后 Summarizer。

因此系统需要：

- cheap evaluator 前置筛选；
- decisive refute 短路；
- N 上限 5；
- attempt cap；
- fingerprint early stall；
- token high-water budget；
- strategist bonus 有界。

---

## 137. 一个适合记忆的七层 Goal 心智模型

```text
1. Contract
   objective + plan baseline + acceptance criteria

2. Work Queue
   Todo + next unchecked plan item

3. Execution
   主 Agentic Loop + tools + subagents

4. Candidate Triage
   hidden Evaluator: Continue / CandidateComplete / Blocked

5. Evidence Audit
   adversarial Skeptic Panel + diff + artifacts

6. Recovery Strategy
   gaps replay + stall + Strategist + pause/resume

7. Governance
   token budget + persistence + async status checks + terminal cleanup
```

---

## 138. 本篇最值得记住的十二条源码结论

1. PromptMode、PlanMode、Todo、Goal、Workflow 是五个不同抽象。
2. PlanMode 是 Session 状态机；Prompt Reminder 与 Tool Edit Gate 同时生效。
3. Todo 的权威状态在 ToolBridge Resources，不是 turn-end 的 UI Plan notification。
4. `/goal` 创建 Goal；`update_goal` 只能汇报或申请完成。
5. Active Goal 从磁盘恢复时会安全降为 UserPaused。
6. Planner 失败会暂停，因为没有计划契约不能安全自治。
7. 新 Goal 路线对每个候选完成先跑 tool-free JSON Evaluator。
8. CandidateComplete 仍必须通过对抗 Skeptic Panel。
9. Skeptic 0 可以高置信否决，但它自己的通过票不计入 cold quorum。
10. 相同 gap 两次触发 NoProgressPaused；不同 gap 连续出现可触发 Strategist。
11. Goal token 使用正 delta + high-water，避免 Compaction 后倒退。
12. `goal_runs_on_workflow_engine` 是新旧 Goal 路线开关，不表示 `/goal` 就是一个 Named Workflow run。

---

## 139. Glossary：本文名词白话解释

| 名词 | 白话解释 | 本文中的具体含义 |
|---|---|---|
| Planning Runtime | 管理计划、进度和长期目标的运行层 | Plan Mode、Todo、Goal、Workflow 的组合 |
| Prompt Mode | 客户端对当前 Prompt 工作形态的标记 | Agent、Ask、Plan |
| Plan Mode | Session 级只规划模式 | 只允许特定计划文件编辑 |
| PlanModeTracker | Plan Mode 的纯状态机 | 不做 I/O，只维护迁移状态 |
| Inactive | 未开启 Plan Mode | 普通执行模式 |
| Pending | 已开启但模型尚未知 | 首个 Prompt 前的过渡状态 |
| Active | Plan Mode 已注入并生效 | Tool Edit Gate 开始约束 |
| ExitPending | Turn 中途要求退出 | 等安全边界再完成退出 |
| Reentry | 同 Session 再次进入 Plan Mode | 使用专门 reminder |
| PendingActivation | 尚未交付的 mid-turn 激活提醒 | 可在 toggle off 时撤回 |
| Full Reminder | 含路径、Tool 名和完整规则的提醒 | 偶数轮、首次、Compaction 后使用 |
| Sparse Reminder | 节省 token 的简短提醒 | 与 full 交替 |
| Plan File | Plan Mode 允许编辑的文件 | Session 目录中的 `plan.md` |
| Plan Edit Gate | Tool 执行前的硬编辑限制 | 非 plan file Edit 被拒绝 |
| YOLO Mode | Permission 自动批准模式 | 仍不能绕过 Plan Edit Gate |
| AccessKind | Tool 对外部资源的访问分类 | Edit、Read、Bash 等 |
| Auto Approve | 不显示 Permission Prompt 直接允许 | Plan file 精确路径可自动批准 |
| Exit Plan Approval | 用户审核模型计划的交互 | Approved/Cancelled/Abandoned |
| Fail Closed | 不确定时保持限制或不判成功 | 未知 exit outcome 保持 Plan Mode |
| Todo | 模型维护的结构化短期任务项 | 带 ID、正文、priority、status |
| TodoState | 全部 Todo 的权威集合 | `IndexMap<TodoId, TodoItem>` |
| IndexMap | 同时支持键查找和稳定顺序的 Map | 便于按 ID 更新并保持 UI 顺序 |
| Pending Todo | 尚未开始的步骤 | TodoGate 会关注 |
| InProgress Todo | 正在执行的步骤 | 可被 background task 支撑 |
| Completed Todo | 已完成的步骤 | 不阻止候选结束 |
| Cancelled Todo | 明确取消的步骤 | 不阻止候选结束 |
| Merge | 按 ID 局部更新列表 | `merge=true`，默认 |
| Replace | 用新列表完全替换旧状态 | `merge=false` |
| ID Fallback | content 缺失时用 ID 作为正文 | 防 lost-state merge 失败 |
| Resources | ToolBridge 中的 typed 共享状态容器 | 保存 `State<TodoState>` 等 |
| tool_state.json | 可序列化 Resources 的 Session sidecar | Resume/Fork 可恢复 Tool 状态 |
| Plan Notification | ACP 给 UI 的任务列表更新 | TodoState 的展示投影 |
| Cosmetic Cleanup | 只修界面、不改权威状态 | Turn-end 把 spinner 显示为完成 |
| TodoGate | 候选结束时检查未完成 Todo | 可注入 reminder 继续 |
| Goal | 跨多轮自主推进的长期目标 | 由 `/goal` 创建 |
| Goal Harness | Goal 的自动续接和验证控制层 | 管理 evaluator/verifier/budget 等 |
| GoalTracker | Goal 的纯状态机所有者 | 内含 optional GoalOrchestration |
| GoalOrchestration | Goal 的持久化完整快照 | 身份、状态、预算、验证、artifact 等 |
| Goal ID | 一次 Goal 实例的 UUID | 区分替换后的新旧目标 |
| Objective | 用户要求最终实现的目标文本 | Goal 契约最高层输入 |
| Token Baseline | Goal 创建时 Session token 总量 | 排除创建前用量 |
| Git Baseline | Goal 创建时 HEAD commit | Verifier 计算 changes diff 的基准 |
| Verifier ID | 每 Goal 的 12 hex artifact namespace | 限定安全 scratch 路径 |
| Scratch Root | Goal 临时验证目录 | owner-only，包含 implementer/skeptics |
| Implementer Scratch | 主模型保存临时验证证据的目录 | Skeptics 可读取 |
| Goal Phase | Goal 当前工作阶段 | Idle、Planning、Executing |
| Goal Status | Goal 是否可自治及终态原因 | Active、Paused、Complete 等 |
| UserPaused | 用户或安全恢复导致的暂停 | 可 resume |
| BackOffPaused | Verifier 尝试达上限 | 可由用户重新授权尝试 |
| NoProgressPaused | 相同 gap 连续出现 | 说明修复没有实质进展 |
| InfraPaused | 基础设施失败暂停 | 避免把无法验证当完成 |
| Blocked | 需要用户动作或不可修复环境前提 | 可 resume |
| BudgetLimited | Token 预算终止 | 不能直接 resume |
| Complete | Goal 已通过完成验证 | terminal |
| Pause Message | 具体暂停原因 | 与 status 分开持久化 |
| Active Since | 当前 active 区间的计时起点 | Pause 时结算到 elapsed |
| History | Goal 的有界事件日志 | 最多 64 项 |
| Planner | Goal 开始时写计划的隐藏 subagent | 失败则暂停 |
| Plan Baseline | Planner 原始 plan 的不可变副本 | Verifier 检测契约变化 |
| Acceptance Criteria | 判断 Goal 是否完成的标准 | 不是逐轮 next step |
| Task Checklist | 可逐项勾选的执行步骤 | continuation 提取第一个未完成项 |
| Goal Rules | 注入主模型的长期自治规则 | Objective、Tool 名、路径和纪律 |
| Evaluator | 候选完成时的隐藏轻量裁判 | Continue/CandidateComplete/Blocked |
| CandidateComplete | 值得交给对抗 Panel 审核 | 还不是 Complete |
| Blocker Key | 稳定标识同一外部 blocker 的 snake_case | 用于连续三次判定 |
| Bounded Transcript | 截断后的近期对话证据 | Evaluator 输入，排除 System/Reasoning |
| Tool-free Request | 不提供 Tool 的模型调用 | Evaluator 只能返回 JSON 判决 |
| Verifier | 审计 Goal 是否真的完成的验证阶段 | 由多个 Skeptic 组成 |
| Skeptic | 对抗性验证 subagent | 独立读取证据、重跑检查 |
| Panel | 同一次验证中的 Skeptic 集合 | 默认 3，范围 1..=5 |
| Skeptic 0 | 可跨 attempt resume 的 reject gatekeeper | 先跑，pass 不计 cold quorum |
| Cold Skeptic | 每次 attempt 新启动的独立 Judge | 决定 approval majority |
| Refuted | Skeptic 找到足以否定完成的证据 | `refuted=true` |
| Not Refuted | 未找到足以否定的证据 | 不等于绝对数学证明 |
| Confidence | Skeptic 对 refute 的信心 | High 可产生 decisive refute |
| Decisive Refute | Skeptic 0 高置信否决 | 绑定最终不能 Achieved |
| Quorum | Panel 通过所需票数 | cold panel strict majority |
| Finding | 结构化验证问题 | 优先于自由文本 evidence |
| Blocking Class | Gap 是否模型可修复 | contradiction/unverifiable 等 |
| Achieved | Panel 判断目标完成 | Goal 转 Complete |
| NotAchieved | 仍有可修复 gaps | 注入下一轮继续 |
| Gap Summary | 给主模型的有界修复要点 | 持续内联直到更新 |
| Gap Fingerprint | 归一化后的问题身份 | 检测相同问题未推进 |
| Stall | 相同 fingerprint 连续出现 | 达阈值自动暂停 |
| Whack-a-mole | 不同 gap 轮流出现的失败模式 | 用 NotAchieved streak 观察 |
| Strategist | 卡住时分析 traces 并给重构建议的 subagent | Fail open，不拥有契约修改权 |
| Cadence | Strategist 每隔多少次失败触发 | 使用 skip-robust 判定 |
| Claim | 在锁内原子占有一次触发机会 | 防重复 Strategist |
| Cap Bonus | Strategist 成功时额外 verifier 次数 | 固定 3，不叠加 |
| PlanGuard | 保护 plan.md 不被 Strategist 修改的 RAII guard | 正常/取消都恢复 |
| RAII | 对象析构时自动执行清理的 Rust 模式 | Drop 中恢复计划文件 |
| Recommendation | Strategist 的有界策略建议 | 下一 continuation 消费一次 |
| Continuation Directive | 自动要求主模型继续工作的 synthetic User item | 带 objective、gaps、next step 等 |
| Reverify | 修复若干轮后再次申请验证 | 由 rounds_since_verify 驱动提醒 |
| Premature Stop | Goal 仍有工作却出现放弃/交接文本 | 触发更强 continuation preface |
| `update_goal` | Legacy 模型汇报 Goal 的 Tool | message/completed/blocked_reason |
| Ack | SessionActor 对 update_goal 的真实处理结果 | Tool 等它再返回模型 |
| Oneshot | 只发送一次结果的异步 channel | 配对一次 Tool 调用与 verdict |
| Mid-turn Drain | Turn 还在运行时处理 goal update | completion 只入队不验证 |
| Turn-end Drain | 安全的完成验证边界 | FIFO 处理 deferred completion |
| Pending Queue | 延迟完成申请队列 | 上限 4 |
| Synthetic NotAchieved | 未运行模型但记一次失败 attempt | 限制并发 completion spam |
| Classifier | 历史命名，当前指 verifier stage/contract | 类型名为兼容 wire 保留 |
| Retry Cap | 每 Goal verifier 最大尝试数 | 默认 10，可配置和加 bonus |
| Positive Delta | 只累计 token 总量的正增长 | Compaction 下降时只 re-anchor |
| High-water Mark | 历史最大 Goal token 使用量 | 保证显示和预算不倒退 |
| Marginal Tokens | Subagent 相对启动/恢复 anchor 的新增 token | 纳入 Goal 成本 |
| Summarizer | Goal 完成后的只读最终表达角色 | 失败不撤销 Complete |
| Role Model Override | 为 Skeptic/Strategist 指定 model+harness | 经授权和 capability gate |
| Harness | Agent 的 prompt/toolset flavor | 不等于 subagent type |
| Inherit | 使用父 Session model 和 harness | override 失败时的 fallback |
| GoalUpdated | 发给 UI 的 Goal 状态投影 | 不等于权威 Tracker |
| Ephemeral Tick | 不持久化的高频 live update | 防 JSONL 无限增长 |
| Named Workflow | 由脚本驱动的后台多 Agent run | 与 GoalTracker 独立 |
| WorkflowManager | 启动、暂停、恢复、取消 Workflow | 管理 active/retiring runs |
| WorkflowTracker | Named Workflow 的状态机 | 多 run、revision、phase、agent rows |
| Journal | Workflow 可重放的执行记录 | Resume 时加载 |
| Rhai | Workflow 脚本语言 | 如 deep_research workflow |
| Agent Budget | Workflow 最多可使用多少 Agent | 与 Goal Token Budget 不同 |
| Execution Epoch | 一次 Workflow 恢复执行的世代 | 隔离迟到结果 |
| Reconciliation | 恢复后修正不再安全的状态 | Active → Paused、缺 Tool/plan 暂停 |
| Durable Ack | 外部删除成功的确认 | Goal clear 先落盘后清内存 |

---

## 140. 下一篇适合继续精读什么

下一篇可以研究：

> **源码精读 27：Agent Eval & Verification Runtime——如何为 Agent 建立可复现的正确性证明：Goal Skeptic、Classifier、Trace Replay、Fixtures、Snapshots、Protocol Compatibility、Failure Injection 与端到端测试如何组合。**

本篇关注运行时怎样验证一个具体 Goal；下一篇可以把视角提升到“如何验证整个 Agent 系统本身”。
