# Grok Build Subagent 与 Task 系统源码详解

本文分析 Grok Build 如何从一次模型发出的 `task` tool call，创建一条独立的子 Agent 会话，并管理它从初始化、运行、等待、查询、取消、完成、结果回流、持久化到进程重启后修复的完整生命周期。

这套机制涉及三个主要 crate：

```text
xai-tool-types
  └─ 模型可见的 task/get_task_output/wait_tasks/kill_task 输入输出协议

xai-grok-tools
  ├─ TaskTool
  ├─ ChannelBackend
  ├─ SubagentCoordinator
  └─ TaskOutputTool / WaitTasksTool / KillTaskTool

xai-grok-shell
  ├─ ShellChildRunner
  ├─ run_shell_child()
  ├─ 独立 child Session
  ├─ completion presentation / auto-wake
  └─ 持久化与 orphan reconciliation
```

核心源码：

```text
crates/common/xai-tool-types/src/task.rs

crates/codegen/xai-grok-tools/src/implementations/grok_build/task/mod.rs
crates/codegen/xai-grok-tools/src/implementations/grok_build/task/backend.rs
crates/codegen/xai-grok-tools/src/implementations/grok_build/task/coordinator.rs
crates/codegen/xai-grok-tools/src/implementations/grok_build/task/coordinator/query.rs
crates/codegen/xai-grok-tools/src/implementations/grok_build/task/coordinator_state.rs
crates/codegen/xai-grok-tools/src/implementations/grok_build/task/types.rs
crates/codegen/xai-grok-tools/src/implementations/grok_build/task_output/mod.rs
crates/codegen/xai-grok-tools/src/implementations/grok_build/task_output/wait_tasks.rs
crates/codegen/xai-grok-tools/src/implementations/grok_build/kill_task/mod.rs

crates/codegen/xai-grok-shell/src/agent/mvp_agent/subagent_coordinator.rs
crates/codegen/xai-grok-shell/src/agent/subagent/mod.rs
crates/codegen/xai-grok-shell/src/agent/subagent/handle_request.rs

crates/codegen/xai-grok-subagent-resolution/src/definition.rs
crates/codegen/xai-grok-subagent-resolution/src/overrides.rs
crates/codegen/xai-grok-subagent-resolution/src/context.rs
```

---

## 1. 先给出最重要的结论

Grok Build 的 subagent 不是一个轻量的“再请求一次 LLM”。

它实际上会：

1. 解析一个新的 `AgentDefinition`；
2. 根据角色、persona、父 Agent harness 和 capability mode 重建工具集；
3. 选择或继承模型与 reasoning effort；
4. 创建独立的 child session ID；
5. 创建独立的 session persistence 目录；
6. 可选创建隔离 worktree；
7. 可选复制父历史或恢复以前的子会话；
8. 启动一条完整的 `SessionActor + sampling + tool loop`；
9. 把任务文本作为 child session 的用户 prompt；
10. 通过 coordinator 统一管理查询、取消、等待和完成交付。

可以把它理解成：

```text
task tool
    ↓
受父会话约束的新 Agent 实例
    ↓
独立 Session
    ↓
独立模型循环与工具调用
    ↓
结果重新注入父 Agent
```

所以它比普通函数调用重得多，也更有隔离性、可观测性和恢复能力。

---

## 2. 两套“调度器”不要混淆

代码里同时存在：

```text
SubagentCoordinator
GrokBuild scheduler tool
```

它们不是同一个东西。

### 2.1 `SubagentCoordinator`

这是运行时内部 actor，负责：

- spawn；
- pending → active → completed 状态迁移；
- query；
- block waiter；
- cancel；
- foreground deadline；
- completion buffer；
- parent session ownership；
- resume source lookup；
- 完成记录淘汰。

模型不能直接调用它。

### 2.2 GrokBuild `scheduler`

这是 Agent 可见的业务工具，用来创建、列出或删除定时任务。

父会话可以把它的 handle 传给普通 child session，但 workflow-owned child 会被移除相关 scheduler tools，避免工作流递归创建新的调度任务。

因此本文出现“协调器”时，除非特别说明，指的是 `SubagentCoordinator`。

---

## 3. 模型看见的 `task` 协议

`TaskToolInput` 位于：

```text
crates/common/xai-tool-types/src/task.rs
```

结构可概括为：

```rust
pub struct TaskToolInput {
    pub prompt: String,
    pub description: String,
    pub subagent_type: String,
    pub run_in_background: bool,
    pub capability_mode: Option<SubagentCapabilityMode>,
    pub isolation: Option<SubagentIsolationMode>,
    pub resume_from: Option<String>,
    pub cwd: Option<String>,
    pub model: Option<String>,
    pub task_id: Option<String>,
}
```

字段语义如下。

| 字段 | 含义 | 默认/约束 |
| --- | --- | --- |
| `prompt` | 给 child 的完整任务 | 必填 |
| `description` | 3～5 个词的短描述 | 必填，用于 UI 和状态输出 |
| `subagent_type` | 要解析的 Agent 类型 | 默认 `general-purpose` |
| `run_in_background` | 是否立即返回 handle | 默认 `true` |
| `capability_mode` | child 工具能力上限 | 可选 |
| `isolation` | shared workspace 或 worktree | 默认由角色决定，最终通常为 `none` |
| `resume_from` | 从已完成 child 继续 | 必须属于同一父 session |
| `cwd` | child 的显式工作目录 | 与有效的 `worktree` 互斥 |
| `model` | 显式模型 slug | resume 时忽略 |
| `task_id` | 服务端注入 ID | 不出现在模型 schema 中 |

### 3.1 默认后台运行

值得注意的是：

```rust
#[serde(default = "default_true")]
pub run_in_background: bool
```

也就是说，模型省略该字段时不是阻塞等待，而是立即得到 `subagent_id`。

### 3.2 输入兼容处理

代码对模型常见的脏输入做了防御：

```text
""
"null"
"none"
"undefined"
只有空白
```

这些值在 `resume_from`、`cwd`、`model` 等可选字符串上会被视为未传。

`cwd` 还会：

- trim 空白；
- 去掉首尾多余的单引号、双引号或反引号；
- 展开开头的 `~`；
- 再检查是否存在且为目录。

这是一种很实用的 tool boundary normalization：schema 只能限制 JSON 类型，不能保证模型给出的字符串有业务意义。

---

## 4. Capability Mode 如何限制 child

模型可以请求：

```text
read-only
read-write
execute
all
```

对应 `SubagentCapabilityMode`。

它不是给 child 增权，而是对原本 `AgentDefinition.tool_config` 做过滤。

最终能力大致遵守：

```text
effective capability
  = role/default capability
    ∩ AgentDefinition capability
    ∩ spawn-time capability override
    ∩ parent/operator policy
```

代码调用：

```rust
effective_runtime.capability_mode =
    intersect_capability_modes(
        effective_runtime.capability_mode,
        definition.capability_mode,
    );

apply_child_tool_policy(
    &mut definition,
    effective_runtime.capability_mode,
    allow_nested_subagents,
);
```

关键点：

- capability mode 对 built-in tools 按 `ToolKind` 过滤；
- `all` 不做过滤；
- 没有 `kind` 的 MCP/custom tool 不能仅凭这里分类，因此过滤逻辑会保留它们；
- 更高层的 MCP inheritance、plugin policy、permission policy 还会继续约束；
- 如果 Task 被移除，也会清理已经失去生产者的 background lifecycle tools。

因此 capability mode 不是完整安全边界，它是工具集裁剪的一层。真正的安全仍由 permission/workspace policy 兜底。

---

## 5. 嵌套深度限制

默认值：

```rust
pub const MAX_SUBAGENT_DEPTH: u32 = 1;
```

语义是：

```text
顶层父 Agent depth = 0
第一层 child depth = 1
默认不能继续创建孙 Agent
```

`TaskTool::run()` 在入口先检查：

```rust
if depth >= max_depth {
    return Err(... "Subagent depth limit exceeded" ...);
}
```

child 构造时还会执行第二层策略：

```rust
let child_depth = spawn_depth.unwrap_or(parent_depth + 1);
let allow_nested_subagents = child_depth < subagents_max_depth;
apply_child_tool_policy(..., allow_nested_subagents);
```

如果达到最大深度，会直接从 child tool config 中去掉 `task` 工具。

这形成两道防线：

```text
Tool 执行时拒绝
        +
Child tool list 中根本不提供 Task
```

注意：深度限制不等于并发限制。

当前 coordinator 没有看到“最多同时运行 N 个 child”的 semaphore。`pending` 只是“child runtime 仍在初始化”，不是因并发配额不足而排队。

---

## 6. `TaskTool::run()` 完整入口流程

`TaskTool` 位于：

```text
crates/codegen/xai-grok-tools/src/implementations/grok_build/task/mod.rs
```

主流程可以写成：

```text
解析 Resources
  ↓
检查 depth
  ↓
清洗 resume/model/cwd
  ↓
检查 cwd 与 worktree
  ↓
预验证 subagent_type
  ↓
预验证 model slug
  ↓
生成 UUID v7
  ↓
构造 CancellationToken
  ↓
构造 SubagentRequest
  ↓
background ? 立即返回 id : 等待 coordinator 结果
```

### 6.1 读取 session resources

主要资源包括：

```text
SubagentBackendResource
SubagentDepthCounter
MaxSubagentDepth
SessionIdResource
CurrentPromptIdResource
SubagentForegroundWait
TaskModelValidator
```

其中：

- `SessionIdResource` 用于把 child 绑定到父 session；
- `CurrentPromptIdResource` 用于把 child 绑定到发起它的父 turn；
- `TaskModelValidator` 使用 live model catalog 检查显式 slug；
- `SubagentForegroundWait` 让宿主知道当前工具正在等待 foreground child。

### 6.2 `cwd` 与 `worktree` 的特殊规则

如果两者同时设置：

```text
cwd 是真实存在的目录
  → 报错，因为两者都决定 effective cwd

cwd 不存在
  → 认为是模型生成的无效路径，清掉 cwd，让 worktree 获胜
```

这体现了一个有趣的设计：

- 对明确、有效但矛盾的用户意图 fail closed；
- 对明显无意义的模型脏参数做容错。

### 6.3 为什么 spawn 前先 validate type

后台模式会立即返回。如果不预验证：

```text
模型得到“已启动”
  ↓
后台才发现类型不存在
  ↓
模型暂时看不到错误
```

因此 `TaskTool` 先发 `ValidateType` 到 coordinator：

```rust
backend.validate_type(subagent_type, parent_session_id).await
```

可能结果：

```text
Ok
Unknown { available }
Disabled
NotAllowed { allowed }
ValidationUnavailable
```

前四种业务结果会形成清楚的参数错误；`ValidationUnavailable` 被建模为 transport/infrastructure error，而不是 unknown type，避免模型误以为换个名字就能解决。

### 6.4 显式模型验证

如果模型传 `model`：

```text
TaskModelValidator 存在
  → 对 live catalog 做 slug 校验

validator 不存在
  → validation_unavailable

slug 无效
  → invalid_arguments
```

当 `resume_from` 存在时，`model` 会在 tool 层被忽略，因为恢复必须固定到源 child 的模型。

### 6.5 ID

没有服务器预注入 `task_id` 时：

```rust
uuid::Uuid::now_v7().to_string()
```

该 ID 同时是：

- `subagent_id`；
- MVP 下的 `child_session_id`；
- query/cancel/resume handle；
- persistence 子目录名的一部分。

UUID v7 既唯一，又大致按时间排序。

---

## 7. `SubagentRequest`：内部真正的 spawn 协议

模型输入会被扩展为更丰富的内部请求：

```rust
pub struct SubagentRequest {
    pub id: String,
    pub prompt: String,
    pub description: String,
    pub subagent_type: String,
    pub parent_session_id: String,
    pub parent_prompt_id: Option<String>,
    pub resume_from: Option<String>,
    pub cwd: Option<String>,
    pub runtime_overrides: SubagentRuntimeOverrides,
    pub run_in_background: bool,
    pub surface_completion: bool,
    pub await_to_completion: bool,
    pub fork_context: bool,
    pub owner: SubagentOwner,
    pub cancel_token: CancellationToken,
}
```

### 7.1 模型不可见的控制字段

`surface_completion`：

- `true`：完成后可以进入 reminder/auto-wake；
- `false`：内部 planner/classifier 等 child 不应暴露给模型。

`await_to_completion`：

- 普通 Task 为 `false`，foreground 受预算约束；
- 某些内部流程可要求一定等待到完成。

`fork_context`：

- 普通模型 Task 为 `false`；
- harness 内部任务可以把父历史复制给 child。

`owner`：

```rust
enum SubagentOwner {
    Task,
    Workflow { run_id: String },
}
```

workflow child 在取消、结果暴露、scheduler 工具和 caller-drop 语义上与普通 Task 不同。

### 7.2 Runtime overrides

内部 override 还可以携带：

```text
model
model_override_provenance
reasoning_effort
persona
capability_mode
isolation
harness_agent_type
completion_output_cap
spawn_depth
output_token_budget
output_schema
loop_task_id
```

普通模型发出的 `task` 只允许设置其中一小部分。

例如 `harness_agent_type` 仅由 `/goal` 等内部角色 spawner 设置，模型不能通过 `task` 偷换 child 的 harness flavor。

---

## 8. Backend 是 transport abstraction

三个工具不直接操作 coordinator state：

```text
TaskTool
TaskOutputTool
KillTaskTool
       ↓
SubagentBackend trait
       ↓
ChannelBackend
       ↓
mpsc::UnboundedSender<SubagentEvent>
       ↓
SubagentCoordinator actor
```

trait 的模型可见核心能力是：

```rust
async fn spawn(...) -> Result<SubagentResult, ToolError>;
async fn query(...) -> Option<SubagentSnapshot>;
async fn cancel(...) -> SubagentCancelOutcome;
async fn validate_type(...) -> SubagentValidateTypeOutcome;
async fn describe_subagent_type(...) -> SubagentDescribeOutcome;
```

`ChannelBackend::for_session()` 会绑定父 session ID。

这很重要：查询或取消不是全局按 ID 裸查，而是还能带 parent ownership scope，避免一个 session 操作另一个 session 的 child。

### 8.1 Request/reply 模式

channel 负责命令传输，`oneshot` 负责单次响应：

```text
caller
  ├─ 创建 oneshot(tx, rx)
  ├─ mpsc.send(Event { respond_to: tx })
  └─ await rx

coordinator
  ├─ 收到 Event
  ├─ 改 actor-owned state
  └─ respond_to.send(result)
```

这种模式避免把 coordinator 的 `HashMap` 放进共享 `Mutex`。

### 8.2 Workflow caller drop

`ChannelBackend::spawn()` 对 workflow-owned child 设置 drop guard。

如果等待结果的 receiver 被丢弃，guard 会 cancel token。普通 Task 则可以转成后台继续运行。

这是 owner 语义差异的第一层实现。

---

## 9. 一个共享的 Single-Writer Coordinator

Shell 启动：

```rust
start_subagent_coordinator()
```

它只消费一次 `subagent_event_rx`，随后：

```rust
tokio::task::spawn_local(
    SubagentCoordinator::new(rx, runner, config).run()
);
```

配置包括：

```text
foreground_budget:
  GROK_SUBAGENT_AWAIT_BUDGET_MS
  默认 600 秒（shell host）

buffer_completions: true
buffered_completion_output_cap: None
```

注意 `CoordinatorConfig::default()` 的 foreground budget 是 45 秒，但 shell 启动时显式覆盖成 600 秒。因此分析实际产品行为时不能只看 struct default。

### 9.1 为什么是 single writer

actor 独占：

```text
pending
active
completed
completed_order
waiters
workflow_cancel_waiters
spawn_blocked_sessions
usage_not_applied_prompts
pending_completions
```

所有状态迁移都在 actor turn 内同步完成。

优点：

- 不需要多个锁；
- ID 冲突检查和插入是原子的；
- cancel 与 pending→active 的竞态容易封装；
- completion 只会被归档一次；
- waiter delivery disposition 可集中决定。

### 9.2 运行中的 future 集合

Coordinator 同时 poll：

```text
runs          child runtime future
validations   type validation future
descriptions  type describe future
progress      progress snapshot future
commands      外部命令 mailbox
internal_rx   child reporter 内部事件
deadline      foreground/waiter timer
```

实现使用 `tokio::select!`，且 internal event 和 child completion 在 command 前被优先检查。

child future 外面还有：

```rust
AssertUnwindSafe(...).catch_unwind()
```

因此一个 child runner panic 会被转换为失败结果，而不是直接让整个 coordinator actor 崩溃。

---

## 10. Coordinator 状态机

核心状态：

```text
           Spawn
             │
             ▼
         PendingChild
        初始化 child runtime
             │
       reporter.started()
             │
             ▼
          ActiveChild
       child session 正在运行
             │
       run future 返回/panic
             │
             ▼
        CompletedChild
```

### 10.1 `PendingChild`

保存：

```text
request
started_at
cancellation
spawn_reply
foreground_deadline
handle_only
explicitly_killed
```

Pending 意味着 worktree、模型、历史、persistence、child session 等仍在准备。

### 10.2 `ActiveChild`

在 Pending 基础上增加：

```text
child_session_id
persona
resumed_from
child_cwd
worktree_path
effective_model_id
definition_background
control
```

`control` 是 host-specific runtime handle。在 shell 中是 `ShellChildRuntime`，可以：

- 获取 progress；
- 发送 cancel；
- 持有 child thread 生命周期。

### 10.3 `CompletedChild`

保存最终结果和恢复所需元数据：

```text
request
child_session_id
persona
resumed_from
child_cwd
worktree_path
snapshot_ref
persisted_output_ref
effective_model_id
SubagentResult
```

Coordinator 最多保留：

```rust
MAX_COMPLETED_ENTRIES = 1024
```

超过后按 `completed_order` 淘汰最老记录。

这意味着 resume/query 不能永远依赖内存；shell 还实现了 durable metadata/output fallback。

---

## 11. Spawn 如何进入状态机

收到 `SubagentEvent::Spawn` 后，coordinator 会依次：

1. 处理 nested child reparenting；
2. 检查父 child 是否正在 teardown；
3. 检查 parent session 是否被 Stop gate 阻止新 spawn；
4. 检查 ID 是否已存在；
5. 计算 foreground deadline；
6. 插入 `PendingChild`；
7. 增加 running gauge；
8. 创建 `ChildReporter`；
9. 把 `runner.run(...)` 放入 `FuturesUnordered`。

### 11.1 Nested child reparenting

如果请求的 `parent_session_id` 实际是一个 active child session ID，coordinator 会找到它，并把孙 child 重新归属到 root parent session。

同时：

```text
surface_completion = false
继承 workflow lineage
继承 loop_task_id
```

原因是：

- root session 才是 UI/ownership 的稳定边界；
- 孙 child 的完成不应单独向 root 模型重复冒泡；
- workflow 内部嵌套不能因为 reparenting 丢失 workflow owner。

### 11.2 Stop 后的 late-spawn gate

用户 Stop 时可能存在竞态：某个 detached background `TaskTool` 刚准备把 Spawn 发进 channel。

Coordinator 用：

```text
spawn_blocked_sessions: HashSet<String>
```

解决：

```text
ParentSession cancel
  → session ID 加入 blocked set
  → 取消已有非 workflow children
  → 拒绝迟到的新 Task spawn

下一次用户 turn 开始
  → OpenSpawnAdmission
  → 从 blocked set 删除
```

这样 Stop 不会永久禁用该 session 的 Task，也不会被迟到的异步 spawn 绕过。

### 11.3 Pending→Active 的竞态

child runtime 完成初始化后调用：

```rust
reporter.started(StartedChild { ... }).await
```

Coordinator 只有在 Pending 仍存在且 cancellation 未触发时，才把它移动到 Active，并回复 `true`。

如果 cancel 先赢：

```text
started() 返回 false
  ↓
Shell runner 关闭刚创建的 child session
  ↓
必要时删除新建且尚未使用的 worktree
  ↓
返回 cancelled result
```

这个 acknowledgement 是专门为关闭“初始化完成”和“取消”之间的 race window 设计的。

---

## 12. Foreground 与 Background 不是两套 child runtime

两者创建的 child session 相同，差别主要在“谁等待结果、结果如何交付”。

### 12.1 Background

`TaskTool`：

```rust
tokio::spawn(async move {
    backend.spawn(request).await
});
```

随后立即返回：

```text
Subagent started in background.
subagent_id: ...
type: ...
description: ...

Use get_task_output ...
```

Coordinator 中：

```text
handle_only = true
foreground_deadline = None
```

### 12.2 Foreground

`run_in_background=false` 时，Task tool await `backend.spawn()`。

Coordinator 中：

```text
handle_only = false
foreground_deadline = now + foreground_budget
```

child 正常完成时，`spawn_reply` 直接收到最终 `SubagentResult`。

### 12.3 Foreground 自动转后台

如果超过预算且 `await_to_completion=false`：

```text
Coordinator 给 spawn_reply 发送 interim result：
  backgrounded = true
  subagent_id = ...

然后：
  handle_only = true
  foreground_deadline = None
  child 继续运行
```

Task tool 看见 `result.backgrounded` 后返回可轮询 handle。

这不是 completion，所以 interim result 的 `success` 保持 false，避免状态消费者把仍在运行的 child 记成 failed/completed。

### 12.4 Caller 消失

如果 foreground spawn 的 oneshot receiver 被父 turn 中止而关闭：

- 普通 Task-owned child：自动转后台继续；
- Workflow-owned child：取消。

对应：

```rust
background_if_caller_gone(...)
```

普通 Task 选择保留可能有价值的工作；workflow 强调调用链的结构化生命周期，caller 消失后不应留下脱离 workflow 的孤儿工作。

---

## 13. `ShellChildRunner` 是 host adapter

Coordinator 本身不知道如何创建 Grok Shell session。

它只依赖：

```rust
trait ChildRunner {
    type Control;
    type CompletionData;
    type RunFuture;
    ...
}
```

Shell 提供 `ShellChildRunner`。

`run()` 做两件关键事情：

1. 从当前 `MvpAgent` 和父 `SessionHandle` 构造 `SubagentSpawnContext`；
2. 调用 `run_shell_child()`。

如果父 session 已被 evict 或 teardown：

```text
不 panic
不创建无主 child
返回 Parent session not found 的 failure result
```

### 13.1 为什么 spawn 前再次 snapshot

`ShellChildRunner` 在真正运行前从 parent handle snapshot：

```text
MCP pool
client hooks
tool definitions
```

因为这些状态可能在 session 创建后动态改变。使用 spawn 时刻的快照，比只复制 MvpAgent 初始配置更准确。

### 13.2 Spawn context 继承哪些宿主资源

典型包括：

```text
parent model/chat state/cmd channel
cwd
yolo/permission state
subagent depth/max depth
filesystem/workspace ops
terminal backend
hunk tracker
environment
LSP
process scope
hooks
plugin registry
MCP state/proxy
remote settings/policies
model catalog/auth manager
scheduler handle
skills config/cache
tool overrides
max turns
notification gateway
```

但“继承资源”不等于“共享整个父 session 状态”。child 仍有自己的：

- chat history；
- prompt queue；
- SessionActor；
- sampling loop；
- persistence；
- model context accounting；
- tool call history。

---

## 14. AgentDefinition 如何解析

`run_shell_child()` 首先：

```rust
resolve_agent_definition(subagent_type, ctx)
```

解析上下文包括：

```text
parent cwd
plugin registry
CLI agents
subagent toggles
allowed_subagent_types
```

找到 definition 后，还会应用 session CLI tool/permission overrides。

然后再做一次 gate：

```text
toggle disabled?
parent allow-list allowed?
```

即便 Task tool 已预验证，这里仍重复检查，属于 defense in depth：从内部 harness、workflow 或其他 backend 调用进入的 request 也必须受同样约束。

### 14.1 Toolset 取决于 role 和 harness

`resolve_subagent_toolset()` 同时看：

```text
subagent_type
harness_agent_type override
parent_agent_name
parent_model_agent_type
file_tool_overrides
```

两件事正交：

```text
subagent_type
  → 角色，例如 implementer / explorer

harness flavor
  → system prompt 与工具协议风格
```

普通 Task 的 harness 跟随父 Agent；内部 `/goal` spawner 才能显式覆盖。

### 14.2 DescribeType

`describe_subagent_type()` 不创建 child，只跑：

```text
resolve definition
  → gate
  → resolve harness toolset
  → summarize ToolKind→client name
```

返回：

```text
tool_names
can_read
can_search
can_execute
```

这样 `/goal` 等内部规划器可以在真正 spawn 前确认某个角色是否拥有需要的能力，并使用最终重命名后的 tool name 渲染 prompt。

---

## 15. Role、Persona 与 runtime override 合并

代码调用：

```rust
resolve_runtime_config(
    subagent_type,
    runtime_overrides,
    subagent_roles,
    subagent_personas,
    cwd,
    definition,
)
```

它会形成 effective runtime：

```text
effective model
effective reasoning_effort
effective persona
effective capability mode
effective isolation
role prompt
persona instructions
```

如果 persona 无法解析，spawn 失败；如果 role prompt file 读取失败，代码会记录 warning，并降级为没有 role prompt 继续。

Persona instructions 不是简单拼到 task 文本末尾。

对非 resume、非 verbatim mirror fork 的 child，它会被插入 inherited prefix 附近，成为：

```xml
<system-reminder>
persona instructions
</system-reminder>
```

这样 task prompt 仍然保持独立的 user message 语义。

---

## 16. 模型选择与继承

普通 child 默认继承父 session 的实际 live sampling config，而不是只读某个静态全局默认。

主要优先级可以概括为：

```text
spawn/persona/role effective_runtime.model
  ↓
[subagents.models].agent_name pin
  ↓
AgentDefinition.model override
  ↓
parent live sampling config
```

如果显式 pin 找不到已知 model，会 warning 并向下 fallback。

如果最终 resolved model 不在 live catalog，也会退回 parent model。

### 16.1 Resume 必须固定源模型

恢复时：

1. tool 层忽略 `Task.model`；
2. runtime 再清掉 effective model override；
3. 从 resume source 取 `model_id`；
4. 在当前 catalog 中解析；
5. 如果源模型已不可用，resume 失败。

这是为了防止把一条旧 transcript 直接接到语义、上下文窗口或 tool protocol 不兼容的新模型上。

### 16.2 Reasoning effort

只有目标模型声明支持 reasoning effort 时，字符串才会 parse 并覆盖 sampler config；解析失败只 warning，不让整个 spawn 失败。

---

## 17. 三种初始上下文

child 的 `InitialContextSource` 有三类：

```text
New
Forked
Resumed
```

优先级：

```text
有效 resume
  > fork_context
  > new
```

普通模型发出的 Task：

```text
fork_context = false
```

因此默认是全新对话，child 只收到任务文本和自己的 system prompt，不自动知道父对话全部内容。

这也是为何给 subagent 的 `prompt` 必须自包含。

---

## 18. Resume 的详细语义

`resume_from` 只允许恢复：

- 已完成的 child；
- 同一 root parent session 所有；
- 相同 subagent identity/type/persona；
- 当前仍可解析其源模型；
- transcript 不超过 child context window 的安全比例。

### 18.1 Resume source lookup

先问 coordinator：

```text
Active
Completed(SubagentResumeSource)
Missing
```

Active 会明确失败，要求先等它完成。

Missing 时 shell 会尝试从 durable session metadata 查找，以支持：

- completed cache 已淘汰；
- 进程重启；
- 内存 tracker 不再保留该 child。

### 18.2 复制哪些状态

Resume 使用 session copy，关键选项：

```text
copy_tool_state = true
copy_plan_state = false
copy_plan_mode_state = false
copy_signals = false
fork_filter = false
```

也就是说：

- 原始 transcript 保留；
- tool state 保留；
- plan UI 状态和旧 signals 不继承；
- 不对 transcript 做 fork filtering；
- 当前 definition 的 system prompt/tool config 会重新渲染。

新任务会作为下一条 user prompt 追加。

### 18.3 Context window guard

估算 source transcript tokens，要求不超过：

```text
child context window × 80%
```

超过则 fail closed，不做隐式压缩式恢复。

这是一个重要语义选择：resume 追求连续、忠实，而不是悄悄改写旧对话。

### 18.4 Resume worktree

源 child 有 worktree 时：

```text
目录仍存在，无 snapshot
  → 直接 reuse

目录不存在，有 snapshot_ref
  → rehydrate

目录不存在，无 snapshot_ref
  → fallback 到 shared workspace
```

恢复时调用者传新的 isolation override 不会凭空改变源会话的工作区身份。

---

## 19. Fork context 的详细语义

`fork_context` 主要供 harness 内部 child 使用。

它优先尝试 live parent chat state，其次用磁盘 session copy。

### 19.1 Verbatim mirror fork

满足两个条件时保留父历史逐 item 原样复制：

```text
估算 tokens ≤ child window 的 80%
父历史结束在完整 assistant 文本边界
```

这条路径不执行 `fork_filter_chat`，因为删除任何 synthetic reminder 都会破坏 prefix cache 的字节级一致性。

同时 child 会继承父 tool definitions snapshot，使完整 request prefix 更接近父请求。

### 19.2 Summarized fork fallback

如果历史过大或 tail 不完整：

```text
fork_filter_chat
  ↓
去 synthetic / 修剪不完整 turn
  ↓
normalize_forked_context
  ↓
生成 background context
```

如果最终没有可继承内容，则 fail open 为 `New`，不会创建一个只有空壳 system items 的 fork。

### 19.3 Resume 与 fork 同时存在

成功解析的 resume 获胜，而且 copy 失败会终止 spawn，不会再悄悄退回 fork。

这保证显式的“继续某个 child”不会变成语义完全不同的“复制父上下文再开一个 child”。

---

## 20. Worktree isolation

新 child 的 effective isolation 不是只有 tool 参数决定，还会合并 role/persona/definition 默认。

如果最终 isolation 非 `none`：

1. 找 parent source cwd；
2. 计算 worktree base dir；
3. 创建 `subagent-{id}`；
4. preserve working tree；
5. 标记 WorktreeKind::Subagent；
6. 记录 session ID；
7. child cwd 指向 worktree。

创建失败时当前实现会 warning 并 fallback shared workspace，而不是让任务整体失败。

这是可用性优先的策略，但安全审查时要注意：调用方不能把 `isolation=worktree` 等同于“创建失败也绝不共享目录”的强制隔离保证。

### 20.1 child cwd 决策

大致优先级：

```text
worktree path
  > resume inherited cwd / explicit cwd
  > parent cwd
```

Resume 时优先保持源 child 的 cwd/worktree 连续性。

### 20.2 Hunk tracking

如果 child cwd 在 parent cwd 外部，且父会话启用了 hunk tracking，会为 child 开启对应 fs watch 能力，确保隔离 worktree 中的修改仍可被跟踪。

---

## 21. Child 工具集与父资源的关系

child 的工具不是简单 clone 父最终 tool list。

主路径是：

```text
解析 child AgentDefinition
  ↓
根据 parent harness 重选 role toolset
  ↓
应用 session CLI overrides
  ↓
应用 capability intersection
  ↓
应用 max-depth policy
  ↓
应用 workflow 特殊裁剪
  ↓
spawn_session_on_thread 最终 finalize
```

只有 verbatim mirror fork 为保持 request prefix/cache，才可能传入父 tool definitions snapshot。

### 21.1 MCP inheritance

definition 决定 MCP inheritance policy。

Shell 先取得 parent MCP pool snapshot，再按 inheritance 过滤。

此外 definition 自己声明的 agent-owned MCP servers 也可以接入，但 plugin agent 是否允许拥有这些 server 还受额外 policy 控制。

所以 child MCP 集合大致是：

```text
filtered inherited parent MCP pool
  + allowed agent-owned MCP servers
  + managed MCP policy
```

### 21.2 Skill inheritance

`definition.inherit_skills` 决定是否继承父技能。

如果需要继承但 parent skills cache 尚未生成，spawn path 会按 parent cwd、skills config、plugin registry、compat mode 现场调用 skill listing。

随后把：

```text
parent_skills_config
parent_skills
```

传给 child session。

如果不继承，child 得到默认空的 `SkillsConfig` 和 `None` skills snapshot。

这说明 Skill 继承是 definition-level capability，不是所有 subagent 天然拥有父 Agent 的 Skills。

### 21.3 Permission

child permission mode 来自 definition 与宿主 policy 解析。

Plugin agent、YOLO policy、parent yolo state 会影响最终结果。即使 child 获得写/执行工具，具体 workspace 操作仍会经过共享 permission handle/workspace ops。

---

## 22. 真正创建 Child Session

准备完成后调用：

```rust
session::spawn_session_on_thread(...)
```

这是和普通主 session 同级别的创建路径。

传入的关键参数包括：

```text
child SessionInfo
GatewaySender
effective SamplerConfig
credentials/auth manager
ToolContext
agent-owned MCP servers
inherited parent MCP pool
persistence
initial conversation
initial token estimate
StartupHints { is_subagent: true, ... }
permission/client type
compaction config
AgentDefinition
skill config/snapshot
memory config
managed MCP state
effective model ID
subagent max turns
parent terminal backend
optional parent scheduler handle
optional parent tool definitions
```

### 22.1 共享 terminal backend

child 使用父 terminal backend，因此 child 启动的 background command/monitor 对宿主仍可见，并可以进入统一的 task lifecycle 工具。

### 22.2 Scheduler handle

普通 Task child 可以继承 parent scheduler handle；workflow owner 不继承。

再次强调，这里是 GrokBuild scheduler 业务工具的 handle，不是 `SubagentCoordinator`。

### 22.3 Max turns

优先级：

```text
AgentDefinition.maxTurns
  > parent max turns
```

child 达到 max turns 会形成 cancelled-like terminal result，错误中包含 limit，并尽量保留它已经输出的 final text。

---

## 23. Child prompt 如何发送

session spawn 成功并通过 Pending→Active promotion 后，runner 发送：

```rust
SessionCommand::Prompt {
    prompt_id: UUID v7,
    prompt_blocks: [Text(task_prompt_text)],
    prompt_mode: Agent,
    verbatim: true,
    json_schema: runtime_overrides.output_schema,
    send_now: false,
    ...
}
```

这里的 task prompt 是独立 user turn。

如果 initial context 是：

- New：它是 child 的第一条用户任务；
- Forked：它追加在继承/规范化后的父背景之后；
- Resumed：它追加在源 child transcript 后。

Runner 随后 race：

```text
prompt result
    vs
cancel_token.cancelled()
```

---

## 24. Progress 如何获取

Active child 的 progress 不是 coordinator 自己统计，而是通过 `ChildControl::progress()` 从 child `SessionSignals` 读取。

快照包含：

```text
turn_count
tool_call_count
tokens_used
context_window_tokens
context_usage_pct
tools_used
error_count
```

Coordinator query 时创建 progress future；完成后把 metadata seed 与 live progress 合并成 `SubagentSnapshotStatus::Running`。

这种设计避免 coordinator 跟踪 child 内部每个 event，也避免把 signals 的 mutable state共享给工具层。

Shell 另外启动 progress publisher，向 gateway/UI 周期性发 child progress，并可给 goal loop 发送 tick。

---

## 25. `SubagentResult`

最终结果结构：

```rust
pub struct SubagentResult {
    pub success: bool,
    pub output: Arc<str>,
    pub error: Option<String>,
    pub cancelled: bool,
    pub subagent_id: String,
    pub child_session_id: String,
    pub tool_calls: u32,
    pub turns: u32,
    pub duration_ms: u64,
    pub tokens_used: u64,
    pub output_tokens_used: u64,
    pub total_tokens_used: u64,
    pub output_usage_incomplete: bool,
    pub worktree_path: Option<String>,
    pub backgrounded: bool,
}
```

`output` 用 `Arc<str>`，因为同一份大文本可能同时进入：

- completed state；
- completion summary；
- reminder buffer；
- auto-wake；
- snapshot；
- persistence。

使用 refcount clone 可避免多次复制任意大的 child 输出。

`status()` 的优先级：

```text
cancelled → "cancelled"
success   → "completed"
otherwise → "failed"
```

`backgrounded` 是非 terminal 的 interim 标记，不应传给 `status()` 当作最终状态。

---

## 26. Child turn 结果如何转成 SubagentResult

Runner 会读取：

```text
PromptTurnOk completion kind
last assistant text
turn snapshot
SessionSignals fallback
chat_state total tokens
usage ledger
```

主要分支：

### 26.1 正常完成

如果没有 structured output 要求：

- 有 final assistant text：作为 output；
- 没有：生成“completed successfully + tool calls/turns”的 fallback 文本。

### 26.2 Structured output

如果 request 带 `output_schema`：

- valid structured value：序列化为 output，success；
- validation error：failed，并保留 final text；
- 没有 structured output：failed。

### 26.3 Permission/hook/mid-turn cancellation

会把取消原因细分为：

```text
user rejected permission
user cancelled permission prompt
hook denied
mid-turn abort
generic cancellation
```

并尽可能带 tool/hook/reason 上下文。

### 26.4 Max turns

标记：

```text
success = false
cancelled = true
error = max turns reached
```

### 26.5 Usage

child token usage会回写 parent session ledger，并带 `parent_prompt_id` 归属。

如果 cancellation 可能使 usage 不完整，或回写 parent 失败，会设置 incomplete 标志，供 turn freeze/reporting 判断父账单是否完整。

这避免 parent 只报告自己的 sampling 花费，而漏掉子 Agent 成本。

---

## 27. 完成时 Coordinator 做什么

`finish_child()` 是中心汇合点。

它会：

1. 从 active 或 pending 移除记录；
2. 创建 `CompletedChild`；
3. 唤醒 query waiters；
4. 尝试交付 foreground spawn reply；
5. 根据配置写 completion buffer；
6. 如果 output 已持久化，释放内存中的文本；
7. 计算 `CompletionDisposition`；
8. 插入 completed map/order；
9. 更新 running gauge；
10. 调用 host 的 `on_completed()`；
11. 解决 workflow cancel waiters。

### 27.1 Completion disposition

它记录：

```text
foreground_delivered
backgrounded
waiter_delivered
explicitly_killed
should_surface
```

这是防止同一结果通过多个渠道重复唤醒模型的关键。

例如：

- foreground tool 已直接拿到结果，就不需要 auto-wake；
- block waiter 已消费结果，就不需要再次唤醒；
- 显式 kill 后的 completion 不应唤醒；
- cancelled child 不应在用户 Stop 后马上把模型重新叫醒。

### 27.2 Completion buffer

如果：

```text
buffer_completions
surface_completion
非 workflow owner
```

则生成 `SubagentCompletionSummary` 放进 `pending_completions`。

上限：

```rust
MAX_PENDING_COMPLETIONS = 256
```

超出时删除最老项，避免关闭但未 teardown 的 session 无界占内存。

---

## 28. 三种结果回流渠道

可以把结果交付理解成三个 surface。

### 28.1 Surface 1：Foreground tool result

`run_in_background=false` 且预算内完成：

```text
child completion
  → spawn_reply
  → TaskTool
  → ToolOutput::SubagentCompleted
  → 当前父 Agent tool result
```

输出包含：

```xml
child answer

<subagent_meta>...</subagent_meta>

<subagent_result>
subagent_id: ...
To continue ... resume_from="..."
</subagent_result>
```

### 28.2 Surface 2：显式查询

后台 child 可由父模型调用：

```text
get_task_output(task_ids=[id], timeout_ms=...)
```

结果由 coordinator snapshot 转成统一 `TaskOutputResult`。

### 28.3 Surface 3：Completion reminder / auto-wake

后台 child 完成时：

- coordinator buffer completion summary；
- shell 发 `SubagentFinished` UI update；
- 条件满足时向父 session 注入 synthetic prompt。

synthetic prompt 的 ID：

```text
subagent-completed-{subagent_id}
```

消息被包装为 system reminder 风格，再作为新的父 Agent prompt 运行。

这样父 Agent 即使当前 idle，也能自动继续处理 child 结果。

---

## 29. Auto-wake 的精确条件

`should_auto_wake_subagent()` 要求同时满足：

```text
completion 被视为 backgrounded
result 不是 cancelled
auto_wake_enabled
没有 block waiter 消费
不是 explicitly killed
goal loop 当前不 active
parent command channel 仍开放
disposition.should_surface
```

任何一项不满足都不注入 synthetic prompt。

但“不 auto-wake”不一定等于“结果丢失”：completion buffer 和显式 query 仍可提供其他交付面。

### 29.1 Reservation

Auto-wake 前会 reserve subagent ID，避免同一 completion 又被普通 between-turn drain 重复注入。

如果向 parent command channel 发送 Prompt 失败，会 release reservation。

---

## 30. `get_task_output`

模型输入：

```rust
pub struct TaskOutputToolInput {
    pub task_ids: Vec<String>,
    pub timeout_ms: Option<u64>,
}
```

兼容行为：

- 接受 alias `task_id`；
- 宽松接受 bare string/number；
- trim；
- 去空；
- 去重但保持首次出现顺序；
- 最多 20 个 ID。

等待语义：

```text
timeout_ms omitted / 0
  → non-blocking snapshot

timeout_ms > 0
  → wait
```

服务器等待上限：

```text
GROK_MAX_WAIT_BLOCK_MS
默认 600000 ms
```

即使模型传非常大的值，也会 clamp，防止一个 tool call 把 turn 卡住数小时。

### 30.1 Bash task 与 subagent 共用一个查询工具

查询顺序：

```text
先 terminal backend 查 background bash task
  ↓ not found
再 SubagentBackend query
  ↓ not found
返回 TaskNotFound
```

因此 task ID namespace 在用户体验上统一，但底层是两个 registry。

### 30.2 Snapshot 状态

Subagent 映射为：

```text
Initializing
Running { progress... }
Completed { output... }
Failed { error }
Cancelled { reason }
```

Completed 输出还会加：

- subagent meta；
- worktree path；
- resume footer。

### 30.3 Multi-ID wait

多个 ID + 正 timeout 使用 wait-all。

它先 resolve 一次，找出：

```text
pending_bash_ids
pending_subagent_ids
```

然后为两类任务注册 event-driven wait，直到：

- 全部完成；或
- deadline。

最后再 resolve 一次生成一致快照。

没有 200ms polling loop。

### 30.4 Wait helper 的清理

每个等待被放进 spawned helper task，以便 bash 和 subagent 并行等待。

`AbortWaitsOnDrop` 保证：

- wait-all 返回；
- wait-any 返回；
- deadline；
- 父 tool future 被取消；

任何路径都会 abort 剩余 helper wait。

否则残留 waiter 可能稍后偷走 completion，错误地把任务标为已经 block-waited，从而压掉本应发生的 auto-wake。

---

## 31. `wait_tasks`

`wait_tasks` 是旧 prompt 的兼容 alias。

新推荐方式：

```text
get_task_output(task_ids=[...], timeout_ms=positive)
```

它仍支持：

```text
wait_all
wait_any
```

其中：

- `wait_all` 复用统一 multi get path；
- `wait_any` 保留 legacy event-driven 实现；
- 省略或传 0 timeout 时，legacy wait 仍使用默认 30 秒，而不是 snapshot。

这个差异是兼容历史协议的结果。

---

## 32. `kill_task`

`kill_task` 同样统一 Bash task 和 Subagent。

顺序：

```text
terminal.kill_task(id)
  ├─ Killed
  ├─ AlreadyExited
  └─ NotFound
        ↓
      backend.cancel(id)
        ├─ Cancelled
        ├─ AlreadyFinished { status }
        └─ NotFound
```

因此如果 Bash 和 Subagent 恰好 ID 冲突，terminal registry 优先。但 subagent 默认 UUID v7，实际冲突概率极低。

### 32.1 Cancel target 类型

内部不仅能按 ID cancel：

```rust
enum SubagentCancelTarget {
    SubagentId(String),
    ParentPromptId(String),
    ParentSession,
    WorkflowRunId(String),
}
```

用途：

- `SubagentId`：模型 `kill_task`；
- `ParentPromptId`：只取消当前 turn 发起的 child；
- `ParentSession`：用户 Stop/Esc，取消该 session 的所有非 workflow child；
- `WorkflowRunId`：取消一个完整 workflow lineage。

### 32.2 Active 与 Pending 取消

Active：

```text
cancellation token.cancel()
control.cancel()
```

Pending：

```text
只 cancel token
```

因为 Pending 可能还没有可调用的 live runtime control。

完成后再 cancel 会返回：

```text
AlreadyFinished { status }
```

---

## 33. Parent turn、Parent session 与 teardown

三种结束语义必须分开。

### 33.1 当前 parent turn 中止

通过 `ParentPromptId` 找出该 turn 发起的 child 并 cancel。

不会误杀先前 turn 启动、仍在后台运行的 child。

### 33.2 用户 Stop 整个当前工作

`ParentSession`：

- 取消 session 下全部非 workflow active/pending child；
- 阻塞迟到 spawn；
- 不取消 workflow-owned child。

下一 turn 用 `OpenSpawnAdmission` 重新开放。

### 33.3 Session teardown

父 session 真正关闭时：

- 清掉它的 buffered completions；
- 清掉 spawn blocked marker；
- 将其所有 child 的 `surface_completion=false`；
- cancel active/pending child。

将 `surface_completion` 清掉是为了防止 parent 已消失后，child completion 又被缓存到未来同名 session。

---

## 34. Persistence

父 session 下有：

```text
<parent session dir>/subagents/<subagent_id>/
```

典型文件：

```text
meta.json
output
```

child 自己还有完整独立 session directory，保存 chat history、summary、tool state 等。

`SubagentMeta` 记录：

```text
subagent/parent/child IDs
type
description
prompt
status
started/completed time
duration
tool calls/turns
error
context source/normalized
fork error
persona
resumed_from
cwd/worktree
snapshot_ref
effective_model_id
```

写文件使用临时文件 + rename 的 atomic write 思路，降低中途崩溃产生半个 JSON 的概率。

### 34.1 大 output 的内存释放

Shell `persisted_output_ref()` 返回 output directory reference。

Coordinator 完成归档时，如果确认有持久化引用：

```rust
completed.result.output = Arc::from("");
```

后续 query 再通过 runner `load_persisted_output()` 读取。

因此最多 1024 个 completed entry 不意味着它们一定在内存中各自保留完整大输出。

### 34.2 Snapshot ref

隔离 worktree 可以生成 snapshot ref，供未来 worktree 已不存在时 rehydrate。

这把“恢复对话”和“恢复代码工作区”连接起来。

---

## 35. 进程重启后的 orphan reconciliation

如果进程崩溃，UI history 里可能有：

```text
SubagentSpawned
但没有 SubagentFinished
```

磁盘 `meta.json` 也可能仍是：

```text
status = running
```

重放 session 后：

```rust
reconcile_orphaned_subagents_with_backend(...)
```

它会合并两类候选：

- replay 发现的 unfinished spawn；
- 磁盘上仍为 running 的 meta。

然后逐 ID 查 coordinator：

```text
仍 active/pending
  → 不处理

coordinator 有 terminal inspection
  → 重发真实 SubagentFinished

没有 live/terminal 记录，但 meta 仍 running
  → 改为 cancelled
     error = "interrupted by process restart"
     发一次 finish
```

候选用 ID-keyed map 合并，避免同一 orphan 从两个来源被修复两次。

这是 UI/event sourcing 层的自愈，而不是恢复崩溃前仍在内存运行的 Tokio future。

---

## 36. 并发、容量和资源边界

源码中明确看到的边界：

| 边界 | 默认/上限 |
| --- | --- |
| Subagent nesting depth | 1 |
| Shell foreground await budget | 600 秒，可由 env 改 |
| 单次 get/wait 最大阻塞 | 600 秒，可由 env 改 |
| Multi wait IDs | 20 |
| Completed registry | 1024 |
| Buffered completions | 256 |
| Resume/fork安全上下文比例 | 80% |

当前 coordinator 没有显式的：

```text
max concurrent subagents semaphore
bounded spawn queue
per-parent child count quota
global token budget scheduler
```

`mpsc::unbounded_channel` 也意味着 mailbox 本身没有 backpressure。

实际并发仍会受到：

- 模型 API 并发/限流；
- 系统线程与 Tokio runtime；
- workspace/terminal backend；
- permission interaction；
- parent tool policy；
- max depth；
- 上层 goal/workflow 逻辑；
- 用户和模型实际 spawn 行为。

但从这个模块本身看，不能声称它实现了严格的全局 child concurrency cap。

这是值得进一步设计和压测的点。

---

## 37. Failure semantics

不同失败阶段有不同可见性。

### 37.1 Tool boundary 失败

例如：

```text
depth exceeded
unknown/disabled/not-allowed type
invalid model
invalid cwd
cwd/worktree conflict
```

这些在 background handle 返回前失败，模型立即看见 tool error。

### 37.2 Background spawn 后期失败

例如：

```text
parent session 已被 evict
persona 解析失败
resume copy 失败
sampling client 创建失败
persistence 创建失败
child session spawn 失败
```

Task tool 已经返回 ID，最终失败会进入 completed snapshot、notification/reminder/query path；background wrapper 也会记录 error log。

### 37.3 Worktree 创建失败

不是 terminal failure，而是 warning + shared workspace fallback。

### 37.4 Child panic

Coordinator catch unwind，并生成：

```text
success = false
error = "Subagent runtime panicked"
```

### 37.5 Coordinator channel 关闭

Backend 返回 typed custom errors，例如：

```text
channel_closed
validation_unavailable
```

使模型能区分参数错误和基础设施故障。

---

## 38. 完整时序图

```text
Parent model
  │
  │ task(prompt, type, background, ...)
  ▼
TaskTool
  │ normalize + validate depth/cwd/type/model
  │ build SubagentRequest + CancellationToken
  ▼
ChannelBackend
  │ SubagentEvent::Spawn + oneshot reply
  ▼
SubagentCoordinator
  │ ownership / stop gate / duplicate ID
  │ insert PendingChild
  │ runner.run()
  ▼
ShellChildRunner
  │ snapshot parent MCP/hooks/tool definitions
  │ build SubagentSpawnContext
  ▼
run_shell_child
  │ resolve AgentDefinition
  │ gate type
  │ resolve role/persona/toolset/model/capability
  │ resolve resume/fork/new context
  │ create/reuse/rehydrate worktree
  │ create persistence + ToolContext
  │ spawn independent child Session
  ▼
ChildReporter.started
  │ Pending → Active
  ▼
Child SessionActor
  │ receive SessionCommand::Prompt
  │ sampling/tool loop/compaction/permissions
  │ final assistant text or failure
  ▼
run_shell_child
  │ collect stats + usage
  │ persist meta/output/snapshot
  │ return ChildRunOutput
  ▼
SubagentCoordinator.finish_child
  │ Active → Completed
  │ wake query waiters
  │ foreground reply or completion buffer
  │ compute disposition
  ▼
Shell presentation
  │ SubagentFinished UI event
  │ optional synthetic auto-wake prompt
  ▼
Parent SessionActor / Parent model
```

---

## 39. 为什么这套设计值得学习

### 39.1 Tool schema 与内部 request 分层

模型只能控制有限字段；owner、fork、surface、harness、budget 等由可信宿主补充。

这是 Agent 系统非常重要的 trust boundary。

### 39.2 Actor-owned lifecycle

所有状态迁移集中在一个 coordinator，避免分散在 Task tool、UI 和 child runtime 中各自维护真假不一的状态。

### 39.3 Host-independent coordinator

Coordinator 只依赖 `ChildRunner` 和 `ChildControl`，Shell 是一个 adapter。理论上其他 host 可以复用 lifecycle actor，而替换具体 child runtime。

### 39.4 Completion disposition

不是简单“完成就发消息”，而是记录结果是否已被 foreground、waiter、kill 或 auto-wake 消费。这是多交付面异步系统避免重复通知的典型方法。

### 39.5 Resume 与 fork 语义严格区分

```text
resume = 继续 child 自己的原始对话与工具状态
fork   = 从父上下文派生一个新 child
new    = 只接收自包含任务
```

这三个概念如果混在一起，会导致不可预测的上下文和权限继承。

### 39.6 运行时可观测性

Subagent 不只是一个 Future；它有：

- UI spawn/finish/progress event；
- query snapshot；
- token usage；
- tool/turn counts；
- worktree path；
- metadata；
- persisted output；
- crash reconciliation。

这使长任务能真正作为产品功能，而不只是 demo。

---

## 40. 值得继续研究和改进的方向

### 40.1 显式并发配额

可考虑：

```text
global max active children
per-parent quota
per-owner quota
priority queue
pending admission timeout
```

并区分当前“初始化 Pending”和未来“等待配额 Queued”。

### 40.2 Bounded mailbox/backpressure

当前 unbounded event channel 在异常 spawn storm 下缺少自然背压。可以研究 bounded channel、admission rejection 和 overload telemetry。

### 40.3 强隔离语义

给 isolation 增加：

```text
best-effort-worktree
required-worktree
```

避免安全敏感调用方误把 fallback shared workspace 当成成功隔离。

### 40.4 MCP/custom tool capability classification

Capability filter 对 `kind=None` 工具无法分类。可以要求动态工具声明 read/write/execute/network scope，再纳入 child capability intersection。

### 40.5 Durable coordinator registry

当前进程重启主要修复 UI/meta 状态，不恢复 in-flight future。可进一步研究可恢复 job backend、remote runner 和 durable lease。

### 40.6 Result delivery exactly-once 模型

现在通过 disposition、reservation、waiter delivery、buffer suppression 实现工程上的去重。可以把每个 completion surface 的状态持久化，形成更严格的 delivery ledger。

### 40.7 预算调度

可在 spawn admission 前综合：

```text
parent remaining token budget
expected role cost
current child usage
workflow budget
model rate limit
```

从“允许并发”升级成“成本感知调度”。

### 40.8 Security provenance

可以让 query/UI 展示 child 最终权限、工具、MCP、skill 和 policy provenance，便于审计“它为什么拥有这个能力”。

---

## 41. 推荐源码阅读顺序

第一遍只理解协议和状态机：

```text
xai-tool-types/src/task.rs
  ↓
task/types.rs
  ↓
task/backend.rs
  ↓
task/coordinator_state.rs
  ↓
task/coordinator.rs
```

第二遍理解 model-facing tools：

```text
task/mod.rs
  ↓
task_output/mod.rs
  ↓
task_output/wait_tasks.rs
  ↓
kill_task/mod.rs
```

第三遍理解真正的 child session：

```text
mvp_agent/subagent_coordinator.rs
  ↓
agent/subagent/mod.rs
  ↓
agent/subagent/handle_request.rs
```

最后再读：

```text
xai-grok-subagent-resolution
session spawn
session persistence
worktree
task completion reminders
usage ledger
```

---

## 42. 调试清单

如果 `task` 立即报错：

```text
检查 depth/max depth
检查 subagent_type discovery/toggle/allow-list
检查 cwd
检查 cwd + worktree 冲突
检查 model catalog validator
检查 coordinator channel
```

如果返回 ID 但一直 initializing：

```text
检查 worktree 创建
检查 resume copy
检查 sampling client/persistence
检查 child session spawn
检查 reporter.started ack
```

如果 running 但没有进展：

```text
get_task_output 看 turn/tool/token/error count
检查 permission prompt
检查 child max turns
检查 model stream/rate limit
检查 tool deadlock/terminal task
```

如果完成但父模型没醒：

```text
result 是否 cancelled
是否 foreground/waiter 已消费
是否 explicitly killed
auto_wake_enabled
goal_loop_active
parent cmd channel 是否关闭
surface_completion
reservation 是否已存在
```

如果 resume 失败：

```text
source 是否仍 active
是否属于同一 parent session
type/persona identity 是否一致
source model 是否仍可用
transcript 是否超过 80%
session copy 是否成功
worktree/snapshot 是否存在
```

如果进程重启后 UI 一直显示 running：

```text
检查 subagents/<id>/meta.json
检查 replay 是否发现 unfinished spawn
检查 orphan reconciliation 是否执行
检查 coordinator inspect 返回
检查 meta atomic write 错误
```

---

## 43. 总结

Grok Build 的 Task/Subagent 系统可以压缩成六层：

```text
1. Tool contract
   模型只提交受限的 spawn 参数

2. Validation and policy
   depth/type/model/cwd/capability/permission

3. Coordinator actor
   pending/active/completed/query/wait/cancel/delivery

4. Shell child adapter
   从父 session 获取宿主资源和动态快照

5. Independent child Session
   独立 AgentDefinition、history、sampling、tools、persistence

6. Result surfaces
   foreground result / explicit query / reminder + auto-wake
```

最关键的理解是：

> `task` 不是把工作交给一个匿名异步函数，而是创建一条受父会话约束、但拥有独立 Agent 生命周期的子会话。

它通过 single-writer coordinator 把 child 的运行时复杂度收敛为稳定的 spawn/query/cancel 协议，再通过 persistence、completion disposition 和 orphan reconciliation，把长时间异步工作变成可查询、可恢复、可审计的产品能力。
