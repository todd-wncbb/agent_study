# Walkthrough：一次 Subagent Task 如何验证、创建 Child Session、完成并回传

本文沿一条具体的模型调用追踪 Subagent：父模型调用 `task`，系统验证类型和参数，把请求交给 Coordinator，
Shell runner 建立真正的 child session，child 独立执行 agent loop，最后把输出、usage、文件成果和生命周期事件交回父 Session。

本文重点回答：

- `TaskTool` 为什么不能直接 spawn child；
- `SubagentRequest`、subagent ID 和 child session ID 分别是什么；
- Coordinator 为什么必须独占 Pending/Active/Completed 状态；
- child session 从父 Session 继承哪些资源，又刻意不继承哪些状态；
- foreground、explicit background 和 auto-background 有何区别；
- resume、fork context、cwd 与 worktree 如何选择；
- cancel、Stop、caller drop、parent teardown 和 runner panic 如何收敛；
- completion 为什么可能通过 foreground result、blocking query、auto-wake 或 between-turn reminder 返回；
- child token usage 和后台进程如何安全归入 parent。

架构概览可先读 `02-runtime-flows/07-subagent-task.md`；Coordinator 的专题设计见
`03-subsystems/08-subagent-coordinator-inheritance-and-background-tasks.md`。本文采用一次真实调用的时间顺序，避免按模块重复介绍。

---

## 1. 最终调用链

```text
parent model emits task tool call
  -> Session tool pipeline / permission / hooks
  -> TaskTool::run
       depth check
       normalize resume/model/cwd
       validate subagent type and model
       build SubagentRequest
  -> SubagentBackendResource
  -> ChannelBackend::spawn
  -> SubagentEvent::Spawn
  -> SubagentCoordinator::handle_command
       reject duplicate / blocked / orphaned spawn
       insert PendingChild
       runner.run(...)
  -> ShellChildRunner::run
  -> run_shell_child
       resolve agent definition + runtime config
       resume/fork/worktree/cwd/model resolution
       create persistence + ToolContext + SessionActor
       reporter.started(StartedChild)
  -> Coordinator Pending -> Active
  -> child SessionCommand::Prompt
  -> child agent loop and tool calls
  -> child result + persistence + usage fold + cleanup
  -> Coordinator finish_child / CompletedChild
  -> foreground tool result, waiter, auto-wake or reminder
  -> parent model sees final child output
```

核心分层是：**工具负责把模型意图变成合法请求；Coordinator 负责生命周期；Shell runner 负责构建并运行真实 child session。**

---

## 2. 建议同时打开的源码

| 层 | 源码 |
| --- | --- |
| 共享输入/输出 wire types | `crates/common/xai-tool-types/src/task.rs` |
| TaskTool | `crates/codegen/xai-grok-tools/src/implementations/grok_build/task/mod.rs` |
| Backend/channel adapter | `.../task/backend.rs` |
| Coordinator actor | `.../task/coordinator.rs` |
| Coordinator 状态与 traits | `.../task/coordinator_state.rs` |
| Query/cancel 类型 | `.../task/types.rs` |
| Shell ChildRunner adapter | `crates/codegen/xai-grok-shell/src/agent/mvp_agent/subagent_coordinator.rs` |
| Child session 构建与执行 | `crates/codegen/xai-grok-shell/src/agent/subagent/handle_request.rs` |
| Shell completion presentation | `crates/codegen/xai-grok-shell/src/agent/subagent/mod.rs` |
| Session 完成提醒与去重 | `crates/codegen/xai-grok-shell/src/session/acp_session_impl/reminders.rs`、`tool_calls.rs` |
| Parent cancel/usage | `crates/codegen/xai-grok-shell/src/session/acp_session_impl/tasks_cancel.rs`、`turn.rs` |

路径表中的 `.../task/` 指 `crates/codegen/xai-grok-tools/src/implementations/grok_build/task/`。

---

## 3. 起点仍然是一个普通 Tool Call

模型看到的 `task` 参数主要包括：

```json
{
  "description": "locate the auth flow",
  "prompt": "Trace authentication from CLI input to request headers",
  "subagent_type": "explore",
  "run_in_background": false,
  "capability_mode": "read_only",
  "isolation": "none",
  "resume_from": null,
  "cwd": null,
  "model": null
}
```

在到达 `TaskTool::run` 前，它仍经过第 12 篇描述的 JSON parse、typed input、Plan/Permission/Hooks 和 tool dispatch。

Subagent 并没有绕过普通工具安全边界。

---

## 4. TaskTool 必须有查询和取消配套工具

`TaskTool::requires_expr` 要求工具集中同时存在：

- `BackgroundTaskAction`，通常是 `get_task_output`；
- `KillTaskAction`，通常是 `kill_task`。

原因是 `run_in_background` 默认可以开启，而且 foreground child 也可能因预算到期自动转后台。

如果能创建后台 child，却不能查询或取消，就会产生不可管理的生命周期。

---

## 5. `run_in_background` 默认值值得特别留意

共享 `TaskToolInput` 把 `run_in_background` 默认设为 true。

这意味着模型省略字段时，语义不是“像普通函数一样等结果”，而是“立即拿 subagent ID，之后由 query/notification 获取结果”。

阅读 trace 时不要把字段缺失误解为 foreground。

---

## 6. 第一道 Gate：深度限制

TaskTool 从 Resources 读取：

- `SubagentDepthCounter`；
- `MaxSubagentDepth`。

如果 `depth >= max_depth`，立即返回 invalid arguments，不向 Coordinator 发 Spawn。

Shell runner 后面还会按 child depth 从 tool config 移除 Task Tool，这是 defense in depth：

- 工具层阻止已经到达的非法调用；
- child toolset 层尽量让模型根本看不到非法能力。

---

## 7. Depth 不是并发数

深度描述调用树：parent -> child -> grandchild。

它不限制同一个 parent 同时创建几个 sibling children。并发控制来自 Coordinator、runtime、goal/workflow policy 和系统资源，不应拿 depth counter 替代并发配额。

---

## 8. Resources 为 TaskTool 提供运行时上下文

TaskTool 一次性读取：

| Resource | 作用 |
| --- | --- |
| `SubagentBackendResource` | spawn/query/cancel 的抽象入口 |
| `SubagentDepthCounter` / `MaxSubagentDepth` | nesting gate |
| `TaskModelValidator` | 验证模型显式指定的 model slug |
| `SessionIdResource` | parent session scope |
| `CurrentPromptIdResource` | 标记 child 属于父 Session 的哪一轮 Prompt |
| `SubagentForegroundWait` | 记录 foreground child 正在阻塞 turn |
| tool cancellation | foreground Task Tool 被取消时转发给 child token |

`parent_prompt_id` 之后允许“只取消本轮创建的 children”，而不误伤更早轮次仍在后台运行的 child。

---

## 9. Resume ID 会先去掉模型占位值

模型有时会输出：

```text
"" / "null" / "none" / "undefined" / whitespace
```

`is_valid_resume_id` 把这些当作 absent。真正的 resume ID 会 trim 后保留。

这属于输入容错，不代表这些字符串是合法 ID。

---

## 10. Resume 时 model override 会被忽略

Resume 的目标是继续原 child 的身份与对话，因此源 child 使用的 model 被视为身份的一部分。

若 `resume_from` 存在：

- TaskTool 对 model override soft-ignore；
- Shell runner 再把 resumed child pin 到 source model；
- source model 已不在 catalog 时，resume fail closed。

如果允许 resume 同时随意换模型，旧 conversation、tool protocol 与新 model 能力可能不兼容。

---

## 11. Cwd 会被清洗和展开

`sanitize_cwd_value` 会：

- trim whitespace；
- 去掉多余的单/双引号或 backtick；
- 拒绝 sentinel placeholder；
- 展开 leading `~`。

非 resume 的显式 cwd 还必须是现存 directory。

TaskTool 和 Shell runner 都做检查，后者是 defense in depth，避免内部调用绕过模型工具层。

---

## 12. Cwd 与 worktree isolation 为什么互斥

两者都在指定 child 的有效工作目录：

- `cwd`：进入一个已有目录；
- `isolation=worktree`：创建新的隔离工作树并进入它。

如果两者都指向真实目录，TaskTool 返回明确错误。

一个细节是：若 cwd 是不存在的垃圾路径而 isolation=worktree，当前实现会清除 cwd，让 worktree 胜出，而不是整次失败。

---

## 13. Subagent type 在后台 spawn 前必须 eager validate

后台分支会立即返回 handle，因此不能等 detached future 里才发现类型不存在。

TaskTool 先调用：

```text
backend.validate_type(subagent_type, parent_session_id)
```

结果分为：

| 结果 | Tool 行为 |
| --- | --- |
| Ok | 继续 |
| Unknown | invalid arguments，并列 available types |
| Disabled | 告知 config toggle 禁用 |
| NotAllowed | 告知当前 agent allowlist |
| ValidationUnavailable | custom transport error，不诱导模型换名字重试 |

---

## 14. Unknown 与 ValidationUnavailable 必须区分

Unknown 说明 Coordinator 正常响应，用户参数确实不合法。

ValidationUnavailable 说明 channel close、responder drop 或 timeout，类型可能完全正确，只是基础设施不可达。

把两者都标 invalid arguments 会让模型不断更换 subagent type，掩盖真实 transport 故障。

---

## 15. 显式 Task.model 也必须先验证

模型提供 model 时，`TaskModelValidator` 必须存在，并检查 catalog/auth compatibility。

缺 validator 是 `validation_unavailable`；已知不合法 model 是 invalid arguments。

只有来自模型工具参数的 override 使用这条严格校验。Harness、role 或 persona 内部选择通过 provenance 区分，不应被误判成不可信模型输入。

---

## 16. Subagent ID 默认是 UUID v7

如果 input 没有内部 `task_id`，TaskTool 生成 UUID v7。

`SubagentRequest` 注释声明这个 ID同时：

- 是 subagent ID；
- 默认成为 child session ID；
- 是 Coordinator pending/active/completed registry key；
- 是 get-output / kill 的管理 ID；
- 是 parent lifecycle notification 的关联 ID。

相比后台 Bash 的多个 ID，这条普通路径尽量统一身份。

---

## 17. `SubagentRequest` 是创建意图，不是运行中对象

请求包含：

- ID、prompt、description、type；
- parent session/prompt scope；
- resume、cwd；
- runtime overrides；
- foreground/background flags；
- context bootstrap flags；
- owner：Task 或 Workflow；
- cancellation token。

它不含 child handle、SessionActor、progress 或最终 output。这些只能在 runtime 初始化后产生。

---

## 18. Task owner 与 Workflow owner 行为不同

`SubagentOwner` 是：

```text
Task
Workflow { run_id }
```

差异包括：

- parent caller 消失时，Task child 可自动后台继续；Workflow child 会 cancel；
- User Stop 通常取消 Task children，但 workflow lineage 有独立 cancel scope；
- workflow children 不进入普通 completion reminder；
- child tool policy 会移除 scheduler tools，避免 workflow child 再操纵 scheduler。

---

## 19. Foreground cancellation forwarder 是单向桥

只有 `run_in_background=false` 时，TaskTool 才建立：

```text
tool cancellation token
  -> spawned forwarder
  -> child cancellation token.cancel()
```

TaskTool 收到 spawn result 后 abort forwarder。

后台 child 不绑定原 Tool Call future 的 cancellation，因为 Tool Call 本来就要立即结束；后续取消由 parent-prompt/session 或 explicit kill scope 管理。

---

## 20. 为什么 foreground cancellation 仍不是唯一取消机制

Tool future 可能丢失、forwarder 可能和 spawn 竞态、child 可能已经 reparent 到 Coordinator。

系统还保留：

- request 自带 cancellation token；
- Coordinator cancel-by-ID；
- cancel-by-parent-prompt；
- cancel-by-parent-session；
- cancel-by-workflow-run；
- Coordinator channel close 时 cancel-all。

这是一组不同授权范围，而不是同一个 cancel API 的别名。

---

## 21. Backend trait 故意很窄

`SubagentBackend` 面向 Tool 层主要暴露：

- `spawn`；
- `query`；
- `cancel`；
- `validate_type`；
- `describe_subagent_type`。

TaskTool 不知道 child 是本进程 SessionActor、远端 worker 还是测试 double。

当前 Shell 使用 `ChannelBackend`，但生命周期语义由共享接口和 Coordinator 保持。

---

## 22. ChannelBackend 只做 transport envelope

`spawn` 创建 oneshot，将 plain `SubagentRequest` 和 `result_tx` 包装成 `SubagentSpawnRequest`，再发送 `SubagentEvent::Spawn`。

它不修改 Coordinator registry，也不构造 child。

把 oneshot 放在 envelope 而不是 plain request 中，能让 runner 只接收纯创建数据，不持有调用者 reply capability。

---

## 23. 后台 TaskTool 是“detached await”，不是不 await Coordinator

background branch 会 `tokio::spawn` 一个 task，在里面调用 `backend.spawn(request).await`，而原 Tool Call 立即返回 formatted handle text。

detached task 仍等待 Coordinator 最终 reply，以便记录：

- transport error；
- Coordinator rejection；
- 正常 completion。

但这些 late error 不会回写已经完成的原 Tool Call，所以 eager validation 与后续 completion surface 很重要。

---

## 24. 后台启动结果为什么是 Text 而不是 Completed output

后台分支返回包含：

- subagent ID；
- type；
- description；
- 真实 get-output 工具名。

它不能返回 `SubagentCompletedOutput`，因为 child 仍运行。

这与第 13 篇后台 Bash 的 `BackgroundTaskStarted` typed variant 不完全对称：Subagent 当前使用格式化 Text 作为启动 handle。

---

## 25. Coordinator 是生命周期唯一写者

`SubagentCoordinator` 独占：

- pending、active、completed maps；
- completed order；
- blocking waiters；
- foreground deadlines；
- workflow cancel waiters；
- spawn-blocked sessions；
- pending completions；
- run/validation/description/progress futures。

Tool、runner 和 Session presentation 都不能直接修改这些 maps，只能发 command/internal event。

---

## 26. Coordinator 使用 unbounded mailbox 的含义

`ChannelBackend` 使用 unbounded mpsc，发送不会因容量 await。

这适合同进程低延迟 control messages，但意味着安全性依赖：

- tool/host 不制造无限事件；
- list/progress/completion buffers各自有界或可清理；
- Coordinator 持续被 LocalSet 调度；
- Session teardown 关闭 producer。

它不是“系统没有背压问题”，而是把背压策略放在更高层。

---

## 27. Coordinator 主循环为什么 biased

大致优先级是：

1. runner internal events；
2. child run completion；
3. validation/description completion；
4. progress；
5. external command；
6. deadline。

Pending->Active acknowledgement 等内部状态推进优先，可以减少 child 已建好但 control event 延迟的窗口。

---

## 28. Spawn 先处理 nested child reparent

如果新 request 的 parent session ID 正好是某个 active child session，Coordinator 会把它重归属到 root parent：

- `parent_session_id` 改为 root；
- `surface_completion=false`，避免每一层重复对用户报告；
- 继承 workflow lineage 和 loop task ID；
- 若 spawner child 已在 cancel，拒绝 late nested spawn。

Coordinator 因而管理一棵逻辑树，但对 root Session 呈现扁平可控的 registry scope。

---

## 29. User Stop 后要阻止 late detached spawn

ParentSession cancel 会把 session 加入 `spawn_blocked_sessions`。

后续非 workflow Task spawn 即使来自已经 detached 的旧 Tool future，也会被拒绝为 cancelled，直到下一 turn 的 `OpenSpawnAdmission`。

这关闭了经典竞态：用户 Stop 后，慢 validation/build 恰好完成并启动一个新 child。

---

## 30. Duplicate ID 在三个 registry 中统一拒绝

Spawn 会同时检查：

- pending；
- active；
- completed。

只要任一存在同 ID，就回复失败。

Completed 也参与检查，避免旧 ID 被复用后，query、resume、completion notification 或持久化目录关联到错误的 child。

---

## 31. Spawn 的第一份运行态是 `PendingChild`

Coordinator 在调用 runner 前先插入 Pending：

| 字段 | 作用 |
| --- | --- |
| request | 完整创建意图 |
| started_at | duration 基准 |
| cancellation | 初始化期间也能取消 |
| spawn_reply | foreground result 或 background detached waiter |
| foreground_deadline | 何时 auto-background |
| handle_only | completion 不应作为 foreground result 重复交付 |
| explicitly_killed | kill 消费去重 |

Pending 不是“请求还没被承认”，而是 Coordinator 已拥有它、runner 正在初始化 runtime。

---

## 32. Foreground deadline 只在特定请求上建立

只有：

```text
!run_in_background && !await_to_completion
```

才建立 `now + foreground_budget`。

Shell host 默认从 `GROK_SUBAGENT_AWAIT_BUDGET_MS` 读取，fallback 为 600 秒；共享 Coordinator 默认配置则是 45 秒。

文档不能笼统说“Subagent 前台预算固定 45 秒”或“固定 10 分钟”，实际值取决于 host config。

---

## 33. Runner future 被放进 `FuturesUnordered`

Coordinator 不为每个 child 建一个能随意写 registry 的 actor。

它把带 subagent ID 的 runner future 放进 `runs: FuturesUnordered`，完成时仍回到同一个 Coordinator loop 调用 `finish_child`。

多个 child 可以并行运行，但生命周期状态提交仍串行。

---

## 34. Runner panic 不会直接杀 Coordinator

runner future 被 `AssertUnwindSafe(...).catch_unwind()` 包裹。

panic 会变成该 child 的 `finish_panicked_child`，而不是 unwind 穿过 Coordinator 主循环。

这让一个 child 构建 bug 不至于让所有其他 child 的 query/cancel/completion registry 一起消失。

---

## 35. Shell ChildRunner 为什么是 `!Send` 友好的

`ShellChildRunner` 持有 `LocalRef<MvpAgent>`，需要访问 LocalSet 内的 Session/runtime 状态。

`ChildRunner` associated future 没有强制 `Send`，Shell 将 Coordinator 用 `spawn_local` 启动。

共享生命周期代码因此不需要为了多线程 trait bound 把大量 `Rc/RefCell/LocalRef` 改成锁。

---

## 36. Parent 已被 evict 时 spawn 会失败

Shell runner 首先调用 `try_build_subagent_spawn_context(parent_sid)`。

找不到 resident parent 时，返回明确失败：parent session 已 evicted 或 teardown，不能 spawn。

它不会用全局默认配置悄悄创建一个与原 parent 无关的 child，否则权限、cwd、hooks、usage 和持久化归属都会错。

---

## 37. Spawn context 是父状态的有意快照

`SubagentSpawnContext` 很大，因为 child 建立需要明确选择继承项：

- live sampling/auth/model catalog；
- cwd、filesystem、terminal、process scope；
- hunk tracker、WorkspaceOps、permission handle；
- hooks、MCP pool/config、managed MCP state；
- Skills/Memory/compat；
- parent command channel 与 notification handle；
- scheduler、goal/auto-wake gates；
- tool overrides、role/persona/subagent config；
- persistence/trace/upload settings。

这是显式依赖打包，不是直接把整个 `&MvpAgent` 交给 child。

---

## 38. 某些 parent 状态必须异步 snapshot

构造基础 context 后，Shell runner 若仍能取得 parent `SessionHandle`，会异步补充：

- 当前 MCP pool；
- client hooks；
- 当前 tool definitions。

这些由 SessionActor 拥有，不能从静态 agent config 猜测。

这也意味着 spawn context 是某一时刻的 snapshot，不会自动跟随父 Session 后续所有配置变化。

---

## 39. Child 重新解析 AgentDefinition

`run_shell_child` 按 subagent type 解析 definition，并再次执行 toggle/allowlist gate。

之后 `resolve_subagent_toolset` 根据 parent harness flavor 和可选 harness override 选择工具集。

TaskTool 的 eager validate 优化模型反馈；runner 的重复验证守住真正创建边界。

---

## 40. Role、Persona 与 runtime override 有分层优先级

`resolve_runtime_config` 综合：

- subagent type；
- request runtime overrides；
- configured roles；
- personas；
- AgentDefinition defaults；
- parent cwd。

persona 解析失败会终止 spawn；role prompt file 失败当前可降级继续，并写 warning。

“角色”不仅是一段 prompt，还可能决定 model、reasoning effort、capability 和 isolation。

---

## 41. 三种 Initial Context

Child 初始对话来源是：

| 来源 | 含义 |
| --- | --- |
| `New` | 全新 conversation，只加入当前 task prompt |
| `Forked` | 复制/规范化 parent conversation，作为 background context |
| `Resumed` | 复制已完成 source subagent 的 transcript/tool state/model |

模型发起普通 Task 时 `fork_context=false`；fork context 主要供受控 harness/workflow 内部使用。

---

## 42. Resume source 先查内存，再查持久化

Runner 通过 `reporter.resume_source` 向 Coordinator 查询：

- Active：拒绝，必须等 source 完成；
- Completed：得到 child identity、cwd、worktree、snapshot、model；

若 Missing，再尝试 durable session metadata 恢复。

因此 completed registry 容量淘汰不一定让 resume 永久失效，只要持久数据仍完整。

---

## 43. Resume 必须验证身份兼容

`validate_resume_identity` 核对 source 与新 request 的 subagent type/persona 等身份。

Resume 不是“任意把一个 Session 的聊天记录拼到另一个 agent”；它要求语义身份相容。

显式 resume copy 失败会 fail closed，不会偷偷降级为 fresh spawn，因为用户期待的是继续旧上下文。

---

## 44. `resume_from` 优先于 `fork_context`

内部调用若同时设置两者，代码记录 warning，并让 resume 胜出。

如果 resume 复制失败，仍终止；不会再退回 fork parent conversation。

这种 fail-closed 顺序避免一次声称“继续旧 reviewer”的任务实际变成另一个 fresh/forked reviewer。

---

## 45. Worktree 创建失败当前会降级到 shared workspace

当 isolation 解析为 worktree，runner 使用 `xai_fast_worktree::WorktreeBuilder` 在 blocking task 中创建。

创建 error 或 spawn_blocking panic 时，当前行为是 warning 后 `worktree_path=None`，继续使用 shared workspace。

这是一条重要安全/隔离语义：请求了 isolation 不代表失败时一定 fail closed。依赖严格隔离的调用方必须核对此设计。

---

## 46. Capability mode 只能收紧

effective runtime capability 与 AgentDefinition capability 通过 intersection 合并。

例如 definition 是 ReadOnly，调用参数不能把它扩大成 All。

过滤后还会调用 `apply_child_tool_policy`：

- 按 capability 删除工具；
- 到 max depth 时删除 Task Tool；
- 删除失去生产者后的 orphaned get-output/kill lifecycle tools。

---

## 47. Permission mode 还有独立安全门

Child permission 并不简单复制 parent yolo。

Runner 综合：

- AgentDefinition permissionMode；
- plugin agent 限制；
- policy 是否禁止 yolo；
- parent yolo 状态；
- inherited PermissionHandle。

Plugin agent 的不受支持 permission bypass 会被忽略并 warning，避免扩展声明自己绕过 host policy。

---

## 48. Model 解析有多层来源

主要来源包括：

- request/role/persona runtime model；
- config `[subagents.models]` pin；
- AgentDefinition model override；
- parent live sampling config fallback；
- resume source model pin。

解析到未知 catalog model 时通常退回 parent；但 resume source model 不再可用时会失败，因为 resume 身份不能静默换模型。

---

## 49. Child session ID 当前与 Subagent ID 相同

Runner 建立：

```rust
let child_session_id = SessionId::new(subagent_id.clone());
```

类型上仍保留两个字段，因为：

- registry identity 与 session storage identity 是不同领域；
- backend/未来实现可能不保持相等；
- lifecycle notification 需要同时表达两层含义。

调用方应按字段语义使用，不应删除其中一个只因当前字符串相同。

---

## 50. Child 有独立持久化目录和 Parent-side metadata

运行时会准备：

- child session directory：保存 child conversation/tool state；
- parent session 下 `subagents/<id>` metadata：让 parent 查询 child 摘要、状态和成果。

`SubagentMeta` 记录 type、description、prompt、context source、persona、model、cwd、worktree、status 等。

这是“child 可独立恢复”和“parent 可管理 child”的两层持久化。

---

## 51. Spawned notification 在 child 真正可运行前后如何定位

Runner 完成 context/persistence 基础准备后发 `SubagentSpawned`，并把 `spawned_notification_emitted=true` 写入 completion data。

如果之后 sampling client、persistence 或 SessionActor spawn 失败，最终仍应发 matching `SubagentFinished`，让 UI 不留下永久 running row。

如果连 Spawned 都没发，completion presentation 会按条件避免制造无起点的普通 foreground lifecycle row。

---

## 52. Child ToolContext 共享基础设施但不是共享 Session 状态

Child 新建自己的 ToolContext/SessionActor/ChatState，却复用或继承：

- filesystem 与 WorkspaceOps；
- terminal backend / process scope；
- hunk tracker；
- LSP；
- environment；
- subagent event channel。

这让 child 的文件编辑出现在同一工作区和 hunk tracking 中，但 conversation queue、compaction、current prompt、usage ledger 仍是 child 自己的状态。

---

## 53. Hooks 和 MCP 是快照式继承

Client hooks 使用 parent actor 的当前 snapshot，使 child tool calls 经过相同 external gates。

MCP 有 owned/inherited policy：child 可以复用 parent pool 或按 definition 建立自己需要的 server 集合。

继承的重点是能力和 trust boundary，而不是把 parent mutable dispatcher 无条件别名给 child。

---

## 54. Skills 与 Memory 不是简单全量复制

是否继承 Skills 取决于 harness/context 语义；某些 verbatim fork 保留精确 parent tool schema，而普通 role child 可重新 finalize skills。

Memory 可按 AgentDefinition scope 解析成 agent/project-specific directory，也可沿用 parent config。

因此 child “知道父 agent 知道的一切”并不成立。

---

## 55. 真正的 Pending→Active 是带 ACK 的两阶段提升

Shell 成功 spawn child SessionActor 后，调用：

```text
reporter.started(StartedChild { control, session_id, cwd, model, ... })
```

Reporter 向 Coordinator 发 internal `Started` event，并等待 bool ack。

Coordinator 只有在 Pending 仍存在且未被 cancellation 抢先处理时才转成 Active，并回复 true。

---

## 56. 为什么不能 spawn 完就直接认为 Active

竞态时间线可能是：

```text
runner 创建 child SessionActor
用户同时 kill/cancel Pending ID
runner 准备公布 Started
```

如果 runner 单方面认为 active，Coordinator 已经取消的 child 会成为 registry 外孤儿。

ACK=false 时 runner 必须关闭半初始化 child、结束 local session，并清理刚创建且未使用的 worktree。

---

## 57. `StartedChild.control` 只暴露运行期控制面

`ShellChildRuntime` 持有 child `SessionHandle` 和 thread ownership。通过 `ChildControl` 对 Coordinator 暴露：

- `progress()`；
- `cancel()`。

Coordinator 不需要知道 SessionActor command enum、persistence 或 ACP gateway 细节。

这是共享生命周期状态机与 Shell runtime adapter 之间的最小 seam。

---

## 58. Active 后才向 child 发送真正 Prompt

promotion 成功后，runner：

- 启动 progress publisher；
- 请求 before-copy snapshot；
- 应用 inherited tool overrides；
- 生成 child prompt ID；
- 发送 `SessionCommand::Prompt`；
- 等待 `PromptTurnResult`。

这样 Coordinator 已具备 cancel/control handle 后，child 才正式进入 agent turn。

---

## 59. Child Prompt 是 verbatim task prompt

Runner 将 `request.prompt` 作为 child 用户输入发送，child system prompt、role/persona prompt 和 inherited context 已在 Session 构建阶段准备。

description 主要服务 UI/registry，不应代替详细 prompt。

模型调用 Task 时，description 应短而可识别，prompt 应包含完整任务、边界和期望输出。

---

## 60. Progress 是 pull-based snapshot

Coordinator/parent 定期通过 `ChildControl::progress()` 获取：

- turn count；
- tool call count；
- tokens used；
- context window/usage percent；
- tools used；
- error count。

Shell progress publisher 默认按变化或 heartbeat 发事件，而不是把 child 每个内部 event 全量广播给 parent。

---

## 61. Foreground TaskTool 如何等待

foreground branch 进入 `SubagentForegroundWait` guard，然后直接 await `backend.spawn(request)`。

Coordinator 最终可能回复：

- 正常 success result；
- child failure/cancelled；
- interim `backgrounded=true` handle；
- duplicate/blocked spawn rejection；
- channel/transport error。

TaskTool 完成后 abort cancellation forwarder，避免已结束 tool 残留等待任务。

---

## 62. Foreground budget 到期只做 auto-background

`background_at_deadline`：

- 从 child 取走 spawn reply；
- 发送 `SubagentResult { backgrounded: true, ... }`；
- 把 child 标为 backgrounded/handle-only；
- child 继续运行。

Interim result 保持 `success=false` 默认值，避免 status consumer 把仍在运行的 child 记录为 completed。

---

## 63. Caller gone 对 Task 与 Workflow 不同

Coordinator 在处理 command 时会 reap abandoned callers。

foreground reply receiver 被丢弃时：

- Task-owned child：自动后台继续；
- Workflow-owned child：cancel。

普通 Task 的目标是不要因 parent turn future 消失丢掉已启动工作；Workflow 则需要由 workflow runtime 保持结构化所有权。

---

## 64. Definition background 与 call background 不相同

`StartedChild.definition_background` 来自 resolved AgentDefinition，影响 outstanding/accounting；
`request.run_in_background` 来自这次工具调用，决定是否立即返回 handle及是否建立 foreground deadline。

一个 agent 类型可以声明适合后台运行，但某次 Task call 仍选择 foreground await；两者不能合并成一个 bool。

---

## 65. Auto-background 后 TaskTool 返回什么

TaskTool 检测 `result.backgrounded` 后返回 Text，包含：

- child 仍在运行；
- subagent ID；
- type 与 description；
- 使用真实 get-output 工具与 timeout 等待的说明；
- 只有 system reminders 启用时才承诺完成通知。

不能无条件写“you will be notified”，因为某些 client/toolset 没有交付 reminder 的能力。

---

## 66. Child agent loop 与普通 Session 共用主干

Child 拥有真正的 SessionActor、ChatState、Sampler、ToolBridge 和 persistence，因此 prompt-to-answer 主链与普通 Session 相同。

区别主要来自：

- Prompt audience 是 Subagent；
- StartupHints 标记 parent/type/depth；
- toolset/capability/permission 已按 child policy 裁剪；
- UI presentation 通过 parent lifecycle event；
- turn result最终汇总成 `SubagentResult`。

Subagent 不是“一次特殊 LLM completion”，而是一个受限制的完整 agent session。

---

## 67. `SubagentResult` 聚合哪些终态信息

典型字段包括：

- success / cancelled / backgrounded；
- output / error；
- subagent ID / child session ID；
- tool call count / turn count / duration；
- worktree path / snapshot reference；
- token usage 与 incomplete 标记。

它是 Coordinator 的 completion value，也是 foreground Tool Output、query snapshot、notification 和持久化 metadata 的共同事实来源。

---

## 68. Child output 先持久化，再允许 Coordinator 丢大字符串

Shell runner 把最终 output 写入 parent `subagents/<id>` 目录，并把 persisted output directory 放进 `ShellCompletionData`。

Coordinator 调用 `runner.persisted_output_ref` 保存 reference；若存在，会把 completed registry 中的 `result.output` 清空，避免 1024 个 completed entries 长期 pin 大字符串。

查询时 runner 可从 reference 重新加载 output。

---

## 69. Usage 必须折回 Parent ledger

Child 有自己的 ChatState usage，但费用/预算通常属于触发它的 parent turn。

Runner 收集：

- by-model usage；
- total/output tokens；
- ledger incomplete；
- cancellation may hide usage；
- task output token budget usage。

然后发送 `RecordSubagentUsage` 给 parent Session，并等待 ack。

---

## 70. Usage fold 失败为什么要 sticky 标记

若 parent channel 已关闭或 ack 失败，代码记录 warning，并尝试：

1. 向 parent 发送 `MarkSubagentUsageNotApplied`；
2. 再退到 Coordinator event 保存 prompt scope 标记。

目标不是伪造精确 token，而是让最终账单/turn result 明确标记 incomplete，防止悄悄少算后台 child 成本。

---

## 71. 后台 usage 不阻塞原 Task Tool，但仍要最终归属

显式 background TaskTool 已经返回，child usage 只能在完成时异步 fold。

父 turn 结束前还有 usage drain/reconciliation；若 child 更晚完成，则后续 session-level bookkeeping 继续接收。

因此“原 turn 已完成”不必然表示所有由它启动的后台 token 已立即进入当时 snapshot，incomplete/sticky 信息用于暴露这种时序。

---

## 72. Child 结束前要 reparent 它留下的后台进程

Child 可能启动 monitor、dev server 或后台 Bash。它们共享 parent terminal backend，却暂时以 child session ID 作为 owner。

Child 完成时，如果同时有 parent backend 和 parent notification handle，runner 调用 `reparent_notifications`：

- owner 改回 root parent；
- future notifications 发给 parent；
- parent UI 重新建立 task row；
- monitor pipeline 继续运行。

随后才能 shutdown child Session。

---

## 73. 为什么 backend 与 notification handle 必须成对存在

只有 backend，没有新 notification handle：进程能继续但事件仍指向已死 child。

只有 handle，没有 backend：无法遍历并更新任务 owner/runtime。

代码在只存在一个时 warning 并跳过 reparent，不做半套状态迁移。

---

## 74. Child Session 的退出顺序

主收敛顺序大致是：

1. child turn 得到 terminal result；
2. 持久化 output 与 metadata；
3. 收集并 fold usage；
4. reparent surviving terminal tasks；
5. 发送 child graceful shutdown；
6. `workspace_ops.end_local_session`；
7. snapshot/remove worktree（按配置与结果）；
8. runner 返回 `ChildRunOutput`；
9. Coordinator 提交 Completed；
10. presentation 发 finished/auto-wake。

先保存事实和转移存活资源，再销毁 child owner。

---

## 75. Worktree 完成后可能转成 Git snapshot ref

若启用 dispose snapshot：

- 将 child worktree snapshot 到 `refs/grok/subagents/<id>` 一类 ref；
- metadata 持久化 snapshot ref；
- 持久化成功后删除物理 worktree；
- `SubagentResult`/CompletedChild 保留可恢复 reference。

这使隔离目录可回收，而成果仍能由 parent 之后应用或检查。

---

## 76. Coordinator 完成提交先移出 Pending/Active

`finish_child` 从 active 或 pending map 移出 record；找不到则直接返回，防止重复 runner completion 再次提交。

随后提取 request、identity、cwd、model、worktree、spawn reply、handle-only 与 explicit-kill 状态，构造 `CompletedChild`。

状态移动而非复制，使一个 ID 在任意时刻只属于 Pending、Active、Completed 之一。

---

## 77. Blocking query waiter 先于 foreground reply 交付

完成时 Coordinator：

1. 构造 completed snapshot；
2. 向 `waiters[id]` 全部发送 snapshot，记录是否至少一个真的收到；
3. 再处理原 spawn reply；
4. 计算 completion disposition。

`send(...).is_ok()` 很重要：receiver 已被取消的不算交付，不能因此抑制后续 completion surface。

---

## 78. Foreground result 只在真正发送成功时算 delivered

如果 spawn reply 存在且 child 不是 handle-only，发送成功才设置 `foreground_delivered=true`。

若 receiver 已丢，child 会被视为 background/handle-only，允许其他完成通道接手。

这避免“Coordinator 尝试发送过”被误当作“模型已经看到”。

---

## 79. `CompletionDisposition` 是去重决策摘要

字段包括：

| 字段 | 意义 |
| --- | --- |
| `foreground_delivered` | 原 foreground Task Tool 收到最终结果 |
| `backgrounded` | completion 需要按 handle-only/background 看待 |
| `waiter_delivered` | blocking get-output/query 已收到 snapshot |
| `explicitly_killed` | kill result 已消费控制终态 |
| `should_surface` | 是否还应向 parent 主动呈现 |

Shell presentation 不重新推断 Coordinator 内部 channel 状态，只消费这个已提交后的摘要。

---

## 80. `should_surface` 的真实条件

Coordinator 计算：

```text
request.surface_completion
&& handle_only
&& !result.cancelled
&& !waiter_delivered
&& !explicitly_killed
```

这意味着 foreground delivered、blocking waiter delivered、cancelled 或 explicit kill 的 child 不应再自动提醒。

Workflow/harness 内部 child 可把 `surface_completion=false`，彻底排除普通用户 completion surface。

---

## 81. Pending completion buffer 也有容量

Shell Coordinator 开启 `buffer_completions=true`。

符合条件的非-workflow completion summary 进入 buffer，供 between-turn drain；最大 256，超出丢最老。

Completed registry 本身最大 `MAX_COMPLETED_ENTRIES=1024`，按完成顺序淘汰。

两者解决不同问题：一个保存提醒候选，一个保存可 query/resume 的完成状态。

---

## 82. Completed 淘汰不等于 child 数据删除

内存 registry 淘汰后：

- query 可能无法再从 Coordinator 直接得到 entry；
- durable metadata/session/output 仍可能存在；
- resume runner 可以尝试 durable lookup；
- worktree 成果可能已有 snapshot ref。

所以 registry cap 是内存生命周期，不是持久化 retention policy。

---

## 83. `on_completed` 发生在 Coordinator 状态提交之后

Coordinator 先插入 completed map、更新 order/running count，再调用 runner `on_completed`。

Shell `present_child_completion` 因此可以让 UI/auto-wake 立即 query 同一 ID，而不会看到仍 active 或 not found 的中间状态。

这是典型的 commit-before-publish invariant。

---

## 84. Finished notification 的 `will_wake` 必须说真话

Shell presentation 根据：

- disposition.backgrounded / should_surface；
- result.cancelled；
- auto-wake config；
- waiter delivered / explicit kill；
- goal loop active；
- parent command channel 是否仍 open；

计算 `will_wake`。

字段只在 synthetic prompt 真有可能提交时为 true，避免 UI 告诉用户“会自动继续”，实际 parent channel 已关闭。

---

## 85. Cancelled child 不 auto-wake

用户 Stop、kill 或 teardown 导致的 cancelled completion 仍持久化并发 Finished event，但不会立即拉起模型。

最危险的竞态是：caller drop 先让 foreground child auto-background，紧接着 cancel token 到达。若只看 backgrounded，它会在用户 Stop 后自动唤醒。

显式 `!cancelled` gate 关闭这条路径。

---

## 86. Auto-wake 如何注入 Parent

满足条件时：

1. reserve subagent ID；
2. 格式化 completion summary；
3. 包成 system reminder；
4. 生成 `subagent-completed-<id>` prompt ID；
5. 向 parent 发送 synthetic `SessionCommand::Prompt`；
6. 可选创建 synthetic trace；
7. send 失败时释放 reservation。

Parent Session 的队列、取消抑制和 completion consumption 会继续做第二层去重。

---

## 87. Foreground 成功如何变成模型 Tool Output

TaskTool 收到 `result.success=true` 后返回 `SubagentCompletedOutput`，包含：

- output；
- subagent ID/type；
- tool calls、turns、duration；
- worktree path；
- resume hint。

这是普通 tool result，会进入 parent conversation 并触发下一轮 sampling。此时不需要另外 auto-wake。

---

## 88. Foreground failure 为什么是 ToolError

若 child terminal result `success=false` 且不是 interim background handle，TaskTool 把 error 转成 invalid/custom tool error。

Session 工具失败链会生成 matching tool result，父模型仍可恢复、换策略或向用户解释。

UI 同时通过 SubagentFinished lifecycle event 收敛 child row。

---

## 89. Query 与 Kill 复用第 13 篇的统一工具

`get_task_output` 先查 terminal task，再查 SubagentBackend；`kill_task` 也是 Bash-first、Subagent fallback。

Coordinator query 能返回 Pending、Active progress 或 Completed snapshot；positive timeout 注册 blocking waiter但不取消 child。

因此 Bash 和 Subagent 共用模型侧 task-management UX，内部 registry 仍彼此独立。

---

## 90. Cancel by ID 的 Pending 与 Active 行为不同

Active child：

- set explicitly_killed；
- cancel token；
- `ChildControl::cancel()` 通知真实 child Session。

Pending child：

- set explicitly_killed；
- cancel token；
- 尚无 runtime control 可调用；
- promotion ACK 会拒绝后来才建好的 half-initialized child。

这正是 Pending 必须进入 Coordinator registry 的原因。

---

## 91. Cancel by Parent Prompt 只针对本轮 children

`parent_prompt_id` 在 TaskTool 构造 request 时从 `CurrentPromptIdResource` 捕获。

取消某个 parent turn 时，Coordinator 遍历 Pending/Active，只取消相同 parent session + prompt ID 的 child。

更早轮次明确留下的后台 child 不应因用户取消当前轮而被误杀。

---

## 92. Parent Session Stop 与 Teardown 不完全相同

User Stop：取消该 parent 的非-workflow children，并设置 spawn admission block，防止 late Task spawn。

Session teardown：范围更彻底，最终不应留下由该 Session 拥有的 child；Coordinator channel关闭还会 cancel all remaining children。

下一轮真实用户输入可重新打开 spawn admission；teardown 则没有下一轮。

---

## 93. Workflow cancel 可以等待整组收敛

Cancel by workflow run ID 会取消该 lineage 下 Pending/Active children。

如果仍有 outstanding，Coordinator 保存 cancel waiter，直到最后一个 child finish 才回复 cancelled。

这使 workflow runtime 能把“已发送 cancel”与“整组 child 已实际收敛”区分开。

---

## 94. Coordinator mailbox 关闭后的行为

外部 commands channel 关闭后，Coordinator 不会立刻退出；它继续 drain 已在运行的 run/validation/description/progress futures。

当 commands closed 且这些 futures 都空，loop 结束并 `cancel_all_children()`。

这样不会因为最后一个 sender drop 而在状态提交中途直接抛弃 run completion。

---

## 95. 常见误解

### 误解 1：Subagent 是线程

它是独立 agent session；底层 async tasks 只是运行机制。

### 误解 2：TaskTool 直接创建 SessionActor

它只验证并发送 `SubagentRequest`；Shell runner 才构建 child。

### 误解 3：Pending 表示 Coordinator 还不知道它

Pending 已可 query/cancel，只是 runtime control 尚未 ready。

### 误解 4：background 是 fire-and-forget

Coordinator 继续管理、持久化、query、cancel 和 completion surface。

### 误解 5：foreground Task 一定等到最终完成

预算到期或 caller drop 可让它 auto-background。

### 误解 6：child 继承 parent 全部 conversation 和工具

普通 child 默认 New context，工具按 definition/capability/depth 重新解析。

### 误解 7：请求 worktree 就一定获得严格隔离

当前创建失败会 warning 后降级 shared workspace。

### 误解 8：resume 可以同时换模型或 type

source identity/model 被验证和 pin。

### 误解 9：child success 表示成果已自动合并到 parent branch

worktree child 可能只留下 path/snapshot ref，需要另一步 apply。

### 误解 10：取消 Task Tool 就一定取消后台 child

explicit background 不绑定原 tool cancellation；应使用正确 prompt/session/kill scope。

---

## 96. 调试：Task Tool 立即失败

依次检查：

1. Task Tool 配套 query/kill tools 是否 finalize；
2. depth/max depth；
3. cwd 与 worktree 是否冲突；
4. cwd 是否存在且为目录；
5. subagent type validation outcome；
6. allowed/toggle config；
7. Task.model validator/catalog/auth；
8. ChannelBackend 是否绑定正确 parent session；
9. Coordinator mailbox 是否 open。

---

## 97. 调试：显示 Spawned 后永远不 Active

重点看：

- child SessionActor spawn 是否返回；
- `reporter.started` internal event 是否到达；
- Pending 是否已被 cancel/remove；
- Started ACK 是 true 还是 false；
- Shell runner 在 ACK=false 后是否执行 half-initialized cleanup；
- LocalSet 是否仍运行；
- parent/session process scope 是否提前关闭。

---

## 98. 调试：Child 已完成但 Parent 没看到结果

核对四个可能 surface：

1. foreground spawn reply 是否真实 send 成功；
2. blocking query waiter 是否真实收到 snapshot；
3. completion buffer 是否被 between-turn drain；
4. auto-wake 是否通过 should_surface、goal、cancel、channel、reservation gates。

同时检查 `surface_completion`、owner workflow、explicitly_killed、cancelled 和 parent channel 状态。

---

## 99. 调试：Parent token usage 少算

检查：

- child `try_get_session_usage` 是否返回；
- cancellation 是否让 usage incomplete；
- `RecordSubagentUsage` 是否 send；
- parent ack 是否到达；
- `parent_prompt_id` 是否正确；
- fallback `MarkSubagentUsageNotApplied` 是否成功；
- Coordinator `usage_not_applied_prompts` 是否保存 sticky scope；
- turn-end snapshot 是否折叠 child usage。

不要用 child output token count代替完整 by-model ledger。

---

## 100. 调试：Child 结束后仍有孤儿进程

检查：

- child 是否复用 parent terminal backend；
- terminal task owner 是否是 child session ID；
- parent notification handle 是否同时存在；
- `reparent_notifications` 是否在 child shutdown 前 await；
- foreground processes 是否应被 teardown kill，而不是 reparent；
- ProcessScope 是否来自 root parent；
- nested child 是否已在 Coordinator reparent 到 root。

---

## 101. 修改 TaskTool 时必须守住的不变量

1. Background spawn 前必须完成 type/model eager validation。
2. Depth gate 与 child toolset depth policy 必须一致。
3. Resume sentinel、cwd 和 model override 的规范化不能只存在一层。
4. `cwd` 与 worktree 的冲突语义必须明确。
5. Foreground tool cancellation 只能单向转发，结束后必须 abort forwarder。
6. Background handle 必须使用本轮真实 get-output 工具名。
7. Interim background result 不能伪装成 success completion。
8. 配套 query/kill tools 缺失时不能暴露 Task Tool。

---

## 102. 修改 Coordinator 时必须守住的不变量

1. 每个 ID 只能位于 Pending、Active、Completed 之一。
2. Pending 插入必须先于 runner initialization。
3. Pending→Active 必须由 acknowledged internal event提交。
4. Cancellation 能作用于 Pending，不能依赖 active control 已存在。
5. Runner completion/panic 必须回到单写者 loop。
6. 只有成功发送给存活 receiver 才算 delivered。
7. 状态提交必须早于 finished event/auto-wake publish。
8. Caller drop 对 Task/Workflow 的不同所有权语义必须保留。
9. User Stop 后 late spawn admission 必须关闭。
10. Completed/pending-completion buffers 必须有界。
11. Coordinator shutdown 必须取消仍存活 children。

---

## 103. 修改 Shell runner 时必须守住的不变量

1. Parent evicted 时不能用默认 context 创建孤儿 child。
2. Type/identity/model/permission 在真正 spawn 边界再次验证。
3. Resume copy 失败必须 fail closed。
4. Capability 只能收紧，不能扩大 definition 权限。
5. Spawned event 后的失败必须产生 matching Finished。
6. ACK=false 的 half-initialized Session 必须 shutdown/cleanup。
7. Output/metadata/usage 必须在 owner teardown 前落稳。
8. 后台 terminal tasks 必须在 child shutdown 前 reparent。
9. Usage fold 失败必须留下 incomplete sticky state。
10. Worktree snapshot/ref 持久化成功后才能删除隔离目录。
11. `on_completed` 只能做 presentation，不能倒写 Coordinator lifecycle。

---

## 104. 推荐实验

### 实验一：最短 Foreground Child

用 test runner 返回固定输出，验证 Pending→Active→Completed、foreground reply 和 `SubagentCompletedOutput`。

### 实验二：显式 Background

确认 Task Tool 立即返回 ID，Coordinator 仍保留 Pending/Active，完成后可 query 并触发一次 surface。

### 实验三：Foreground Deadline

将 budget 缩短到 20ms，让 child 晚完成。验证先收到 `backgrounded=true` interim，再从 completion surface 获取最终结果。

### 实验四：Cancel-at-promote

暂停 runner 在 Session spawn 后、`reporter.started` 前，先 cancel ID。验证 ACK=false、child shutdown、pristine worktree cleanup。

### 实验五：Caller Drop

丢弃 foreground spawn receiver：Task child 应后台继续，Workflow child 应 cancel。

### 实验六：Resume

完成一个 child，再以 `resume_from` 启动新 ID。验证 transcript/tool state/model 继承，system prompt fresh render。

### 实验七：Resume Active 拒绝

对仍运行 source 发 resume，验证明确提示先等待完成。

### 实验八：Worktree Failure

注入 WorktreeBuilder failure，观察当前 shared-workspace fallback，并确认 telemetry/metadata 能暴露 isolation degradation。

### 实验九：Usage Ack Failure

关闭 parent command channel，验证 Coordinator 或 parent sticky incomplete 标记。

### 实验十：Child Background Process Reparent

Child 启动 monitor/server 后结束，验证 task owner 与 notification target 变成 root parent，进程保持存活。

### 实验十一：Late Spawn After Stop

阻塞 detached background spawn，在 parent Stop 后放行，验证 spawn_blocked_sessions 拒绝它；下一 turn reopen 后新 spawn 可成功。

### 实验十二：Runner Panic

让 runner panic，验证该 ID 进入失败 Completed，其他 child 和 Coordinator 仍工作。

---

## 105. 推荐定向测试

| 命令 | 主要覆盖 |
| --- | --- |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task::tests` | TaskTool validation、foreground/background、参数和错误 |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task::coordinator::tests` | Pending/Active/Completed、deadline、query、cancel、panic、buffer |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task::backend::tests` | ChannelBackend transport 与 scope |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task::types::tests` | wire type、cwd/resume 清洗、能力过滤与状态 helper |
| `cargo test -p xai-grok-tools --test test_subagent_soak` | 多轮 spawn/query/cancel soak |
| `cargo test -p xai-grok-shell --lib subagent_usage_fold_tests` | child usage 折回 parent |
| `cargo test -p xai-grok-shell --lib auto_wake_suppression_tests` | completion surface、kill/stop 去重 |

实际结果见下一节。Shell lib test 可能被仓库其他测试模块的编译错误阻塞。

---

## 106. 本文编写时的实际验证

| 命令 | 结果 |
| --- | --- |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task::tests` | 通过：67 passed |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task::coordinator::tests` | 通过：31 passed |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task::backend::tests` | 通过：24 passed |
| `cargo test -p xai-grok-tools --lib implementations::grok_build::task::types::tests` | 通过：29 passed |
| `cargo test -p xai-grok-tools --test test_subagent_soak` | 通过：17 passed，1 ignored |
| `cargo test -p xai-grok-shell --lib subagent_usage_fold_tests` | 阻塞：Shell lib test 编译失败，E0599 |
| `cargo test -p xai-grok-shell --lib auto_wake_suppression_tests` | 阻塞：同一 E0599 |

工具层与 soak 辅助测试共 168 项通过、0 failed。被 ignored 的一项是
`subagent_lifecycle_soak_bounds_threads_open_files_and_heap`；源码明确要求以 `--ignored` 单独运行，
并用 `SUBAGENT_SOAK_CYCLES` 控制测量窗口，因此本文不把它计作已验证通过。

两个 Shell 目标都在收集目标测试之前，被仓库现有文件
`crates/codegen/xai-grok-shell/src/session/acp_session_tests/tool_layer_images_bridge_tests.rs:15`
阻塞：该测试调用 `STANDARD.encode(buf)`，但没有把 `base64::Engine` trait 引入作用域。
这与本文文档改动无关；本文没有顺手修改无关代码，也不把 usage fold 或 auto-wake Shell 测试记作通过。

---

## 107. 自测题

1. TaskTool、Coordinator 和 ShellChildRunner 分别拥有什么职责？
2. 为什么 Background Task 仍要在 detached task 中 await backend.spawn？
3. PendingChild 为什么已经可以被 cancel？
4. `reporter.started` 为什么必须等待 ACK？
5. 普通 Task caller drop 与 Workflow caller drop 分别发生什么？
6. explicit background、auto-background 和 definition background 有何不同？
7. Resume 为什么忽略 model override？
8. Worktree 创建失败当前是 fail closed 还是降级？
9. Child 哪些基础设施与 parent 共享，哪些状态独立？
10. Coordinator 如何判断 foreground result 是否真的交付？
11. `CompletionDisposition` 为什么由 Coordinator 计算，而不是 UI 自己推断？
12. Usage fold ack 失败后如何避免静默少算？
13. Child 启动的后台 Bash 为什么要 reparent？
14. User Stop 如何阻止已经 detached 的 late spawn？
15. Completed registry 淘汰后 resume 是否必然失败？

---

## 108. 本篇术语表

| 名词 | 白话解释 | 本篇中的具体含义 |
| --- | --- | --- |
| Subagent | 由另一个 agent 启动的受控子 agent | 有独立 Session/Conversation/Sampler，但继承受限基础设施和 parent scope |
| parent session | 发起 child 的根会话 | 提供 cwd、资源、权限、usage 归属和 completion 接收面 |
| child session | 真正运行 Subagent 的会话 | 当前 ID 通常等于 subagent ID，拥有独立 SessionActor/ChatState |
| `TaskTool` | 模型可调用的创建工具 | 验证输入并把 plain `SubagentRequest` 发给 backend |
| `SubagentRequest` | 一次 child 创建意图的数据对象 | 没有 runtime handle，不直接拥有 SessionActor |
| backend | Tool 层与生命周期管理器之间的抽象 | `ChannelBackend` 把 spawn/query/cancel 变成 Coordinator events |
| Coordinator | Subagent 生命周期单写者 actor | 独占 Pending/Active/Completed、waiters、deadline、cancel 和 completion buffer |
| runner | 把抽象 child request 变成 host runtime 的适配器 | Shell 中是 `ShellChildRunner` / `run_shell_child` |
| Pending | 已登记、runtime 尚未完成初始化 | 已可 query/cancel，尚无 `ChildControl` |
| Active | child runtime 已建立并获 Coordinator ACK | 有 child session ID、control、cwd、model 和 progress |
| Completed | runner terminal output 已提交 | 可 query/resume，重 output 可从持久引用重载 |
| two-phase promotion | 创建 runtime 与登记 Active 分两步确认 | `reporter.started` + Coordinator bool ACK 关闭 cancel-at-promote 竞态 |
| `ChildControl` | Coordinator 对 live runtime 的最小控制接口 | 只有 progress 与 cancel |
| foreground | 原 Task Tool future 等最终 child result | 可能因 deadline/caller drop 转后台 |
| explicit background | 调用参数一开始就要求立即返回 handle | `run_in_background=true`，默认值也是 true |
| auto-background | foreground 等待预算到期后转 handle-only | child 不被杀，Tool Call 返回 ID |
| definition background | AgentDefinition 声明的 accounting 属性 | 与本次 Tool Call 是否 foreground 是不同维度 |
| foreground budget | Task Tool 最多阻塞 parent turn 的时间 | Shell 默认环境可配，fallback 600s，只后台化不取消 child |
| handle-only | 原 spawn reply 只用于交付 ID或已不再等 final | 最终 completion 应由 query/reminder/auto-wake surface |
| eager validation | 在 detached background 之前同步验证 | 类型/model 错误能直接返回原 Tool Call |
| validation unavailable | 无法联系/获得 validator 结果 | transport/infra 故障，不是参数 Unknown |
| depth | Subagent 调用树层级 | 到 max 时工具调用拒绝且 child toolset移除 Task Tool |
| owner | child 的上层生命周期类型 | Task 或 Workflow，决定 caller-drop/cancel/completion 语义 |
| root reparent | nested child 在 Coordinator 中归到根 parent | 避免中间 child teardown 产生孤儿并统一控制 scope |
| spawn admission | 某 parent 当前是否允许新 Task spawn | User Stop 后关闭，下一 turn 可 reopen |
| Initial Context | child 第一次对话从哪里来 | New、Forked 或 Resumed |
| fork context | 复制/规范化 parent conversation | Harness-only 为主，普通模型 Task 默认关闭 |
| resume | 从已完成 peer child 延续 transcript/tool state/model | 验证 identity，source active 时拒绝 |
| durable resume | 内存 Completed 淘汰后从磁盘恢复 source | 依赖 child session/metadata 完整 |
| capability intersection | 多层工具能力只取共同允许部分 | request 不能扩大 AgentDefinition 权限 |
| isolation | child 文件执行环境策略 | shared workspace 或创建 worktree；当前创建失败可降级 |
| worktree snapshot | 删除隔离目录前保存成果的 Git ref | 允许 parent 后续检查/应用 |
| lifecycle notification | Parent/UI 看到的 child 状态事件 | `SubagentSpawned` 与 `SubagentFinished` |
| progress snapshot | 某时刻 child 运行统计 | turns、tools、tokens、context、errors；pull-based |
| waiter | blocking query 的结果接收者 | 只有 send 成功才算 completion 已交付 |
| `CompletionDisposition` | Coordinator 对完成交付面的决策摘要 | foreground/waiter/kill/background/should_surface |
| completion surface | 最终输出进入 parent model 的方式 | foreground tool result、blocking query、auto-wake 或 between-turn reminder |
| auto-wake | background child 完成后创建 parent synthetic Prompt | 受 cancelled、goal、channel、reservation 和 disposition gates 限制 |
| completion reservation | 某通道预留一个 ID 的交付权 | 防止 auto-wake、query、reminder 重复呈现 |
| usage fold | 把 child token ledger并入 parent | 通过 parent SessionCommand + ack，失败留下 incomplete sticky state |
| sticky incomplete | 无法确认 usage 已应用时保留的持久/协调标记 | 防止最终账单假装完整 |
| reparent notifications | child 退出前转移其后台 terminal task | owner/通知目标改为 root parent |
| persisted output ref | Completed entry 对大 output 的磁盘引用 | 内存可清空，需要时重载 |
| commit-before-publish | 先提交 registry终态，再发外部完成事件 | finished/auto-wake 后立即 query 不会看见旧状态 |
| LocalSet | 运行 `!Send` futures 的 Tokio 本地执行域 | Shell Coordinator/runner 可安全访问 LocalRef 状态 |
| caller gone | spawn reply receiver 已被丢弃 | Task child 转后台，Workflow child 取消 |
| cancel scope | 一次取消作用的身份范围 | ID、parent prompt、parent session、workflow run 或全局 teardown |

更多通用名词见[全局术语表](../appendices/glossary.md)。

---

## 109. 源码证据索引

| 结论 | 符号/位置 |
| --- | --- |
| Task input/default background | `xai-tool-types/src/task.rs::TaskToolInput` |
| TaskTool Gate 与 request 构造 | `task/mod.rs::TaskTool::run` |
| 配套工具依赖 | `TaskTool::requires_expr` |
| Cwd/resume 清洗 | `sanitize_cwd_value`、`is_valid_resume_id` |
| Backend seam | `task/backend.rs::SubagentBackend`、`ChannelBackend` |
| Request/owner/overrides | `task/types.rs::SubagentRequest`、`SubagentOwner` |
| Coordinator registry | `task/coordinator.rs::SubagentCoordinator` |
| Spawn admission/nested reparent | `SubagentCoordinator::handle_command` Spawn arm |
| Pending/Active/Completed | `task/coordinator_state.rs` corresponding structs |
| 两阶段 promotion | `ChildReporter::started`、Coordinator `InternalEvent::Started` |
| Foreground deadline | `background_at_deadline` |
| Caller drop | `background_if_caller_gone` |
| Finish/disposition | `SubagentCoordinator::finish_child` |
| Shell runner adapter | `agent/mvp_agent/subagent_coordinator.rs::ShellChildRunner` |
| Host Coordinator config | `MvpAgent::start_subagent_coordinator` |
| Parent context snapshot | `try_build_subagent_spawn_context` + async SessionHandle snapshots |
| Child 构建主链 | `agent/subagent/handle_request.rs::run_shell_child` |
| Resume/fork bootstrap | `bootstrap_initial_context`、`resume_source` branch |
| Runtime/tool/capability resolution | `resolve_runtime_config`、`apply_child_tool_policy` |
| Child persistence/meta | `SubagentMeta`、`persist_subagent_output`、`persist_subagent_completion` |
| Spawn/Finished presentation | `emit_subagent_notification`、`present_child_completion` |
| Usage fold | `record_subagent_usage` and fallback marking |
| Terminal task reparent | `run_shell_child` completion cleanup branch |
| Auto-wake gate | `should_auto_wake_subagent` |
| Synthetic completion prompt | `inject_subagent_completed_prompt` |
| Half-initialized cleanup | `cancel_pending_shell_child` |

相关学习资料：

- [Subagent 从创建到结果回传](../02-runtime-flows/07-subagent-task.md)
- [Subagent 调度、父子状态继承与后台任务协调](../03-subsystems/08-subagent-coordinator-inheritance-and-background-tasks.md)
- [后台终端任务如何启动、等待、取消并自动唤醒模型](13-background-terminal-task-start-transition-wait-kill-auto-wake.md)
- [一条 Tool Call 如何经过解析、权限、Hooks、执行并返回模型](12-tool-call-parse-permission-hooks-dispatch-output-and-model-feedback.md)
- [并发模型、Actor、Channel 与取消传播](../03-subsystems/15-concurrency-actors-channels-cancellation-and-shutdown.md)
- [Worktree 成果如何应用、保留、恢复与安全清理](09-worktree-result-apply-conflict-snapshot-and-cleanup.md)

---

## 110. 一句话复盘

一次 Subagent Task 不是父模型旁边随手 spawn 的异步函数，而是一条分层事务：TaskTool 把不可信模型参数验证成带 parent scope 和 cancel token 的创建意图，ChannelBackend 把它交给单写者 Coordinator，Coordinator 先登记 Pending 再通过 ACK 将 Shell runner 建好的真实 child Session提升为 Active，child 用受限继承的文件、终端、权限、Hooks、MCP 与模型配置独立完成 agent loop，最后先持久化 output、折回 usage、转移后台进程并清理 Session/worktree，再由 Coordinator 原子提交 Completed 和交付决策，让 foreground result、blocking query、auto-wake 与 reminder 只由真正尚未消费结果的通道接手。
