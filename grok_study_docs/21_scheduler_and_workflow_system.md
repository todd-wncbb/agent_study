# Grok Build Scheduler 与 Workflow 系统源码详解

本文分析 Grok Build 中两套面向“长期、异步、多步骤工作”的机制：

1. Scheduler：按时间间隔反复触发一个 prompt；
2. Workflow：执行一段确定性的 Rhai 编排脚本，由脚本串行或并行调用多个子 Agent。

它们都会使用 Subagent，但目标不同：

```text
Scheduler
  = 时间驱动
  = 每隔一段时间执行相同工作
  = 维护跨迭代的对话链

Workflow
  = 脚本驱动
  = 按阶段、分支和 fan-out 编排工作
  = 用 journal 支持暂停与同进程恢复
```

核心源码：

```text
crates/codegen/xai-grok-tools/src/implementations/grok_build/scheduler/
  mod.rs
  create.rs
  list.rs
  delete.rs
  interval.rs
  types.rs
  actor.rs
  occurrence_journal.rs

crates/codegen/xai-grok-tools/src/implementations/grok_build/workflow/mod.rs

crates/codegen/xai-workflow/src/
  lib.rs
  meta.rs
  engine.rs
  host.rs
  journal.rs
  run.rs
  validate.rs

crates/codegen/xai-grok-shell/src/session/workflow/
  mod.rs
  registry.rs
  manager.rs
  host_service.rs
  tracker.rs
  store.rs
  notify.rs
  schema_contract.rs

crates/codegen/xai-grok-shell/src/session/acp_session_impl/workflow.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/spawn.rs
```

---

## 1. 三种后台工作机制的边界

在 Grok Build 中很容易混淆：

```text
background terminal task
Task/Subagent
Scheduler
Workflow
```

它们不是同一种抽象。

| 机制 | 触发方式 | 状态 handle | 是否重复 | 是否编排多个 Agent |
| --- | --- | --- | --- | --- |
| Background terminal | shell command | task ID | 命令自己决定 | 否 |
| Task/Subagent | 单次 tool call | subagent ID | 否 | 单 child |
| Scheduler | wall clock interval | scheduled task ID | 是 | 每轮通常一个 child |
| Workflow | Rhai script | workflow run ID/display name | 脚本决定 | 是 |

最重要的产品语义：

> Workflow run ID 不是 background task ID，不能传给 `get_task_output` 或 `wait_tasks`。

Workflow 的进度在 `/workflows` 和 `WorkflowUpdated` 通知中，完成后由 session 自动生成 completion turn。

---

## 2. Scheduler 与 Workflow 不是同一个调度层

Scheduler 有自己的 actor：

```text
SchedulerActor
  ├─ 时间计算
  ├─ create/update/list/delete
  ├─ expiry
  ├─ fire notification
  └─ loop subagent spawn
```

Workflow 有自己的运行时组合：

```text
WorkflowManager
  ├─ WorkflowTracker
  ├─ WorkflowRunStore
  ├─ Rhai executor
  ├─ WorkflowHostService
  └─ SubagentCoordinator adapter
```

两者最后都可能向 `SubagentCoordinator` 发送 `SubagentEvent::Spawn`，但 Coordinator 只负责 child lifecycle，不理解“定时任务”或“Rhai phase”。

---

# 第一部分：Scheduler

## 3. 模型可见的 Scheduler tools

三个工具：

```text
scheduler_create
scheduler_list
scheduler_delete
```

它们通过 `SchedulerHandle` 向同一个 `SchedulerActor` 发送命令。

### 3.1 `scheduler_create`

输入：

```rust
pub struct SchedulerCreateInput {
    pub task_id: Option<String>,
    pub interval: Option<String>,
    pub prompt: Option<String>,
    pub recurring: bool,
    pub durable: Option<bool>,
    pub foreground: Option<bool>,
    pub fire_immediately: bool,
}
```

它既负责创建，也负责原位更新。

创建时：

- `interval` 必填；
- `prompt` 必填；
- 当前只支持 recurring；
- `durable` 默认 false；
- `foreground` 默认 false；
- `fire_immediately` 默认 false。

更新时传 `task_id`：

- `interval` 和 `prompt` 至少提供一个；
- 未提供字段保持原值；
- ID 不变；
- schedule phase 尽量保持；
- `durable`、`foreground`、`fire_immediately` 是 create-only，更新时忽略。

输出：

```rust
pub struct SchedulerCreateOutput {
    pub id: String,
    pub human_schedule: String,
    pub updated: bool,
}
```

### 3.2 `scheduler_list`

输入为空对象，输出：

```rust
pub struct ScheduledTaskSummary {
    pub id: String,
    pub prompt: String,
    pub interval_human: String,
    pub next_fire_at: String,
    pub created_at: String,
    pub recurring: bool,
}
```

Prompt 超过 80 bytes 附近会按 UTF-8 边界截短展示。

虽然它是“查询”，tool capability 当前仍标成 write。这与 scheduler tools 共享 `ToolKind::Other`、整体 capability 策略有关，不能只从业务动作名称推导协议 scope。

### 3.3 `scheduler_delete`

输入：

```rust
pub struct SchedulerDeleteInput {
    pub id: String,
}
```

输出：

```rust
pub struct SchedulerDeleteOutput {
    pub success: bool,
    pub message: String,
}
```

不存在的 ID 不是 tool error，而是：

```text
success = false
message = No scheduled task ...
```

持久删除过程中的 persistence、notification、timeout 等基础设施错误才会转成 typed `ToolError`。

---

## 4. Interval parser

接受格式：

```text
60s
5m
2h
1d
```

解析步骤：

1. trim；
2. 最后一字符作为单位；
3. 前缀 parse `u64`；
4. 检查非零；
5. checked multiplication；
6. clamp 到最小 60 秒。

所以：

```text
1s  → 60s
30s → 60s
60s → 60s
```

不支持复合表达式：

```text
1h30m  ×
cron    ×
ISO8601 ×
```

这是固定间隔 scheduler，不是 cron scheduler。

---

## 5. `ScheduledTask`

核心状态：

```rust
pub struct ScheduledTask {
    pub id: String,
    pub interval_secs: u64,
    pub prompt: String,
    pub recurring: bool,
    pub durable: bool,
    pub foreground: bool,
    pub created_at: DateTime<Utc>,
    pub last_fired_at: Option<DateTime<Utc>>,
    pub expires_at: Option<DateTime<Utc>>,
    pub last_subagent_id: Option<String>,
    pub iterations_since_fresh: u32,
    pub chain_reset_pending: bool,
}
```

### 5.1 ID

来源是 UUID v7，去掉连字符后取前 12 个字符。

它比完整 UUID 更适合模型和 UI，但碰撞空间相应缩小。Actor 在单 session 中依赖最大 50 项和低创建频率降低实际风险；代码没有额外 ID collision retry。

### 5.2 Fire time

```rust
next_fire_at = (last_fired_at || created_at) + interval
```

`fire_immediately=true` 时，不是单独执行一次，而是把 `created_at` 回拨一个 interval，使第一次 actor tick 看到它已经 due。

### 5.3 Expiry

Recurring task 创建时：

```text
expires_at = now + 7 days
```

到期后删除，不再 fire。

更新 task 不会重新创建 ID，也没有看到自动延长 7 天 expiry 的逻辑。

---

## 6. Scheduler state 与 handle

持久状态：

```rust
pub struct SchedulerState {
    pub tasks: Vec<ScheduledTask>,
    occurrence_journal: OccurrenceJournal,
}
```

它通过 Resources state registry 注册，可序列化到 tool resources state 文件。

运行时 handle：

```rust
pub struct SchedulerHandle(
    mpsc::UnboundedSender<SchedulerCommand>
);
```

Handle 本身 ephemeral，不序列化。

如果 child 继承 parent scheduler handle，它操作的是父会话同一个 SchedulerActor；否则 finalized toolset 会新建 actor。

### 6.1 Commands

```rust
enum SchedulerCommand {
    Create { task, reply },
    Update { id, prompt, interval_secs, reply },
    Delete { id, reply },
    List { reply },
}
```

仍然是 `mpsc command + oneshot response` actor 模式。

---

## 7. SchedulerActor 的启动与关闭

Toolset finalize 时：

```text
有 parent_scheduler_handle
  → 复用，不启动新 actor

没有
  → 创建 channel
  → Resources 插入 SchedulerHandle
  → tokio::spawn(SchedulerActor::run())
```

`FinalizedToolset::drop()` 会触发 scheduler cancellation token。

Actor 启动先：

1. 等待即将到期的后台 loop 所需 subagent wiring；
2. 重发现有任务的 Created notification；
3. 进入 command/timer/cancel select loop。

关闭时会给仍存在的 task 发送 Removed notification，但不会为了 UI 清理而清空持久 state。

### 7.1 Startup wiring grace

如果两秒内有后台 recurring task due，actor 最多等 10 秒，让 Resources 出现：

```text
SubagentEventSender
SessionIdResource
```

每 250ms 检查一次。

这是为恢复 session 时的启动顺序竞态设计的：SchedulerActor 可能比 SubagentCoordinator 资源注入更早启动。

---

## 8. Actor 主循环

每轮先计算最近 fire delay：

```text
遍历非 blocked expiry tasks
  → 取最小 next_fire_at
  → 已到期返回 Duration::ZERO
  → 没任务返回 Duration::MAX
```

随后 biased select：

```text
cancel
command
timer
```

当存在 `pending_removal` 时，timer branch 暂停，先完成 durable removal barrier，防止 mutation/version 顺序被后续 fire 穿插。

---

## 9. Scheduler version clock

每个外部可观察 transition 都有：

```text
generation: UUID v7
revision: u64
```

事件包括：

- created/upsert；
- fired；
- removed。

版本用途是让 Pager/持久 notification consumer 正确处理重放、乱序和 tombstone。

### 9.1 Reservation

Actor 在 mutation 前：

```rust
clock.prepare_transition(count)
```

得到一段连续 revision reservation，然后按顺序 `commit_next()`。

One-shot fire 需要两个 transition：

```text
fire revision
removal revision = fire + 1
```

### 9.2 Revision 溢出

如果 `u64` revision 即将溢出：

- 生成新 UUID v7 generation；
- revision 从 1 重新开始；
- 记录 error-level rollover log；
- reservation 保证一批 transition 全在正确 generation 中。

这是极端边界，但体现了 versioned event stream 的完整性设计。

---

## 10. Create 与 Update

Actor create：

1. 若 removal pending，拒绝；
2. 检查最多 50 tasks；
3. reserve 1 transition；
4. push 到 `Vec`；
5. commit version；
6. 发 `ScheduledTaskCreated`；
7. 回复 created task。

Resources 在 tool dispatch 的统一 finalize tail 会异步保存，所以正常 tool mutation 能进入 resources persistence。

### 10.1 为什么状态用 Vec

不仅因为最多 50 项。

Actor 恢复时会按 `Vec` 顺序重发 Created；Pager 端重建任务面板时，重发顺序用于保持原插入顺序。

### 10.2 Update 保持 phase

更新 interval 不清空 `created_at/last_fired_at`。

如果缩短 interval 导致新的 `next_fire_at <= now`，代码设置：

```rust
last_fired_at = now
```

防止更新动作立刻触发一次意外 fire。

### 10.3 Prompt update 与 chain reset

Prompt 改变时：

```text
chain_reset_pending = true
iterations_since_fresh = 0
```

但保留 `last_subagent_id`，因为它还用于检测旧迭代是否正在执行。下一次真正安全 fire 时才放弃旧 conversation chain。

如果更新时直接清空 anchor，可能在旧 iteration 未完成时再启动一个新 child，造成重叠执行。

---

## 11. Fire 前先推进 cadence

`fire_next_task()` 找到第一个 due task 后，在释放 Resources lock 前：

```rust
task.last_fired_at = Some(now)
```

这是非常关键的顺序：

```text
先推进 cadence
  ↓
再释放 actor state
  ↓
再查询/创建 subagent
```

否则 child spawn await 期间其他 timer/command 路径可能再次选择同一个 due task。

即使本轮最后因为上一 iteration 仍在运行而 `Skipped`，cadence 也已经推进；代码会重新发 Created/upsert，让 UI 知道新的 next fire。

---

## 12. Foreground 与 Background fire

以下情况不走后台 child：

```text
task.foreground = true
legacy one-shot
SchedulerBackgroundLoops = false
subagent wiring 不可用
```

Actor 发 `ScheduledTaskFired { subagent_id: None }`，外层 notification bridge 将 prompt 作为主会话 turn 处理。

后台模式则直接构造 `SubagentRequest`，通过 `SubagentEventSender` 发给共享 Coordinator。

### 12.1 Background loop child request

关键字段：

```text
subagent_type = general-purpose
run_in_background = true
surface_completion = true
await_to_completion = false
fork_context = false
owner = Task

completion_output_cap = 4000
spawn_depth = 0
loop_task_id = scheduler task id
```

`spawn_depth=0` 是内部 loop 策略，使这一轮 child 的 nesting policy 从 loop root 计算，而不是简单继承当前工具调用深度。

---

## 13. 防止 Scheduler 迭代重叠

每个 task 保存 `last_subagent_id`。

Fire 时先 non-blocking query 上一 child：

```text
Initializing/Running
  → Skipped

Completed/Failed/Cancelled/NotFound
  → 继续判断
```

查询发送或响应失败时 fail closed：跳过，而不是假设上一轮不存在。

查询有 10 秒超时，超时也跳过。

### 13.1 后代 child guard

上一 loop child 可能已经完成，但它创建的 nested child 仍在跑。

Actor 再发：

```text
SubagentEvent::LoopUnitActive { task_id }
```

Coordinator 会检查 pending/active child 的 `runtime_overrides.loop_task_id`。

如果任何后代仍活跃，本轮继续 Skipped。

因此 loop unit 的“不重叠”边界不是只有直接 child，而是整条 lineage。

---

## 14. Loop conversation chain

Scheduler 不总是每轮创建完全空白 child。

如果上一 child 成功完成且 anchor 可用：

```text
resume_from = last_subagent_id
```

下一 iteration 在同一子对话链上继续，能知道先前结果。

### 14.1 每 10 次强制 fresh

```rust
LOOP_FRESH_CHAIN_EVERY = 10
```

达到阈值后：

- 不 resume；
- 截取上一输出前 600 字符左右作为 prior summary；
- 新开 child chain；
- iteration counter 归 1。

原因：无限 resume 会让 transcript 膨胀、上下文污染并增加成本。

### 14.2 Prompt 修改

`chain_reset_pending=true` 时下一次：

- 不 resume 旧 child；
- 不把旧输出当作同一 job 的 continuity；
- 开始新的 chain。

### 14.3 Spawn 失败回滚 anchor

Actor 在发 Spawn 前先把新 child ID写入 task，防止并发 fire。

如果 channel send 失败，会恢复：

```text
last_subagent_id
iterations_since_fresh
chain_reset_pending
```

如果 spawn 后收到 error result，异步 guard 会清空新 anchor，避免下一轮 resume 一个失败的初始化。

---

## 15. Scheduler completion output cap

Loop child completion summary cap：

```rust
LOOP_COMPLETION_OUTPUT_CAP = 4000
```

它限制进入 completion reminder/auto-wake 的输出，而不是限制 child 的实际推理或完整持久输出。

定时循环可能长期运行，若每次把巨大输出自动注入父会话，会迅速污染 context，因此这里比普通 Task 更积极地限制自动回流文本。

---

## 16. Expiry 与 durable removal barrier

非 durable expiry：

```text
从 state 删除
commit removal version
发 Removed
```

Durable expiry 更严格：

```text
从内存 state 暂时删除
  ↓
Resources snapshot save_and_flush
  ↓
等待 durable notification consumer ACK tombstone
  ↓
commit version
```

顺序是“持久化 absence → 发布并确认 tombstone → commit transition”。

如果持久化明确失败，会把 task 插回原 index，并加入 `blocked_expiries`，防止 tight retry loop。

如果结果未知，如 timeout/cancel，则不武断回滚，以免磁盘其实已经提交后重新插入导致复活。

### 16.1 Explicit delete

显式 delete 也需要 durable notification target，即使当前 task 标记为 non-durable。

原因是 notification replay 中可能存在它以前的 Created；没有可靠 tombstone consumer 就无法保证删除后不会在下游重现。

Delete 使用 `pending_removal` 保存同一个 version reservation。失败后对相同 ID 重试会继续完成同一 tombstone，不生成新版本。

Barrier 有 30 秒超时，并响应 actor cancellation。

---

## 17. Occurrence Journal

源码中还有 `OccurrenceJournal`，用于 durable one-shot 的 fire/removal receipt 与恢复冲突检测。

当前模型工具明确拒绝创建 one-shot，Actor 仍保留 legacy one-shot 执行兼容；journal 的若干生产方法带有“wired by durable one-shot actor layer”的 dead-code 标记，说明这部分是更完整 durable one-shot 迁移的基础，而不是当前普通 recurring 路径的主干。

设计要点：

- occurrence ID 必须 RFC UUID v7；
- fire/removal 必须同 generation、非零、连续 revision；
- 最多 50 pending occurrences；
- malformed entry 尽量 quarantine task ID；
- 无法归属的破损会 `block_all_one_shots`；
- duplicate occurrence/task/version 触发 recovery required；
- 恢复计划只报告需要删除和阻塞的 ID，mutation 仍由 actor 执行。

这是典型的“宁可停发，也不重复执行一次性任务”策略。

---

# 第二部分：Workflow

## 18. Workflow 是什么

Workflow 是一段 Rhai script，声明 metadata，并调用宿主注册的函数：

```rhai
let meta = #{
    name: "review-changes",
    description: "Review changes from several perspectives",
    phases: [
        #{ title: "Inspect" },
        #{ title: "Synthesize" },
    ],
};

phase("Inspect");
let answers = parallel([
    #{ prompt: "Review correctness", label: "correctness" },
    #{ prompt: "Review security", label: "security" },
]);

phase("Synthesize");
let final = agent("Synthesize these findings: " + answers);
complete(final);
```

Rhai 本身负责控制流；真正的 Agent、文件、模板和 git 操作由 host service 实现。

---

## 19. `workflow` tool contract

输入：

```rust
pub struct WorkflowToolInput {
    pub agent_budget: Option<u64>,
    pub name: Option<String>,
    pub script: Option<String>,
    pub script_path: Option<String>,
    pub args: Option<serde_json::Value>,
    pub resume_from_run_id: Option<String>,
    pub validate_only: bool,
}
```

新运行必须恰好提供一个 source：

```text
name
script
script_path
```

Resume 只提供：

```text
resume_from_run_id
可选更高 agent_budget
```

不能把 resume 和新 source/args 混在一起，因为 resume 必须使用原始不可变 script 与 args。

### 19.1 Top-level only

Workflow tool 检查 `SubagentDepthCounter`：

```text
depth > 0 → workflow_depth_exceeded
```

无论配置允许多深的 Subagent，Workflow 都只能从顶层 session 启动。

Workflow-spawned child 的 tool policy 也不会允许递归启动 Workflow。

### 19.2 Agent budget

范围：

```text
1..=1024
默认 128
```

预算单位是逻辑 child-agent call：

- 每次 live `agent()` 消耗 1；
- `parallel()` 每个 live item 消耗 1；
- journal replay 不重复收费；
- schema corrective retry 不增加逻辑预算，但受独立 agent-run quota 限制。

### 19.3 Output

```rust
pub struct WorkflowToolOutput {
    pub run_id: String,
    pub task_id: String,
    pub name: String,
    pub script_path: Option<String>,
    pub message: String,
}
```

`task_id` 只是 `run_id` alias，明确标注不可传给 task output tools。

`name` 是 session-unique display handle，例如：

```text
deep-research
deep-research-2
```

用户管理时应使用 name，内部追踪才使用完整 `wf_<uuid>`。

---

## 20. Workflow source discovery

来源优先级/范围：

```text
built-in
project .grok/workflows/*.rhai
user $GROK_HOME/workflows/*.rhai
inline
explicit trusted path
session run projection
```

当前 built-in：

```text
deep-research
```

### 20.1 Registry scan

Project workflows 只有 folder trust 允许时才扫描。

每个 scope 内：

- 只看 `.rhai`；
- 文件名排序，保证 deterministic listing；
- 同 scope 同名定义全部标记 ambiguous；
- 高优先 scope 已有名字时，低优先同名不覆盖。

### 20.2 名称规则

1～64 bytes，必须由：

```text
lowercase ASCII letters
digits
single hyphens
```

组成，不能：

- 以 `-` 开头或结尾；
- 连续 `--`；
- uppercase；
- underscore。

### 20.3 Path trust

Explicit path 必须：

- regular file；
- 非 symlink；
- canonical path 位于 project、user workflow dir 或 session workflow runs；
- project path 需要 folder trust；
- 普通保存文件的 stem 必须等于 `meta.name`；
- source 最大 1 MiB。

Session run 下的 editable projection 可使用通用文件名，因此不强制 filename 与 meta name 对齐。

---

## 21. Metadata 必须是第一条语句

Script 开头允许注释，之后第一 statement 必须是：

```rhai
let meta = #{ ... };
```

或 `const meta`。

Metadata：

```rust
pub struct WorkflowMeta {
    pub name: String,
    pub description: String,
    pub when_to_use: Option<String>,
    pub phases: Vec<PhaseMeta>,
}
```

约束：

| 字段 | 上限 |
| --- | --- |
| name | 64 bytes |
| description | 1024 bytes |
| when_to_use | 2048 bytes |
| phases | 64 |
| phase title | 128 bytes |
| phase detail | 1024 bytes |

`deny_unknown_fields` 会拒绝 metadata 拼写错误，不会静默忽略。

Phase title 不能为空且不能重复。

### 21.1 安全提取 meta

Meta extractor：

- max operations 100,000；
- 限制 expression depth；
- DummyModuleResolver；
- 禁用 `eval`；
- 给 `args` 注入 Unit；
- compile 整个 script；
- eval 后只读取 `meta`。

它不是纯文本正则，但通过 operation/module/eval 限制降低 meta probe 执行任意复杂逻辑的风险。

---

## 22. Launch channel

Tool 不直接持有 `WorkflowManager`。

```text
WorkflowTool
  ↓ WorkflowLaunchHandle
mpsc::UnboundedSender<WorkflowLaunchEnvelope>
  ↓
Session-owned launch task
  ↓
resolve/validate
  ↓
WorkflowManager::launch
```

Ack：

```text
Started
Validated
Rejected { code, detail }
```

这种边界让 tool crate 不依赖 shell 的 registry、session persistence 和 Manager 实现。

### 22.1 Session-side checks

Launch consumer 再检查：

- background workflow feature flag；
- input validity；
- resume source exclusivity；
- source resolve/trust；
- validate-only；
- args/objective；
- manager admission。

Tool boundary 与 session host 再次形成双层校验。

---

## 23. `validate_only`

Smoke check 在 blocking thread 执行：

```text
extract metadata
compile full script
注入 canned host
按给定 args 执行一条实际路径
应用 agent budget
```

它能发现：

- syntax error；
- metadata error；
- 当前 args 路径上的 runtime misuse；
- 部分 budget/control flow 问题。

但不能证明：

- 所有分支都走通；
- live Agent 输出符合预期；
- 工具和网络可用；
- 权限足够；
- 并发运行没有环境冲突。

所以输出明确称为 path-specific smoke check。

---

## 24. WorkflowManager admission

每个 session 最多：

```rust
WORKFLOW_MAX_ACTIVE_RUNS_PER_SESSION = 4
```

Admission 计算：

```text
active.len + retiring.len
```

即已经 terminal 但 cleanup watcher 尚未 drain 完的 run 仍占名额，避免新 run 与旧 child 清理重叠导致资源风暴。

这与前一篇提到的普通 Task 不同：Workflow 明确有 per-session active run cap。

---

## 25. 新 run 的创建

新 run ID：

```text
wf_<UUIDv7 simple>
```

步骤：

1. 确定 agent budget；
2. Store 注册 immutable script/args；
3. 建立 `workflows/<run_id>/journal.jsonl`；
4. Tracker `start_run()`；
5. session 内生成唯一 display name；
6. 在真正执行前同步持久化 state；
7. 发初始 WorkflowUpdated；
8. 创建 host channel；
9. 创建 cancellation token；
10. 启动 HostService；
11. `spawn_blocking(run_workflow)`；
12. 启动 async watcher 处理 outcome 和 drain。

执行前持久化失败会回滚新 run 的 tracker/store，而不是启动一个没有恢复记录的 workflow。

---

## 26. 为什么 Rhai executor 在 `spawn_blocking`

Rhai engine 是同步执行模型，host function 内使用 `blocking_recv()` 等待 async host service 的 oneshot 结果。

因此架构是：

```text
blocking thread
  Rhai engine
  host function
  blocking_recv()

        ↕ mpsc/oneshot

Tokio runtime
  WorkflowHostService
  SubagentCoordinator
  filesystem/git operations
```

如果直接在 async worker 上同步执行 Rhai，会阻塞 Tokio executor。

---

## 27. Rhai sandbox/limits

Workflow engine 设置：

```text
max operations       100,000,000
max call levels      64
max expression depth 128/64
max string size      16 MiB
max array size       65,536
max map size         65,536
max result host calls 10,000
```

还禁用：

```text
module resolver
eval
timestamp()
sleep()
exit()
```

`timestamp()` 被禁用是为了确定性：resume 时相同脚本必须发出相同 host calls。

`sleep()` 被禁用是因为 host call 本身已经阻塞等待结果，workflow 不应该拿 blocking thread 做定时器。

`exit()` 被替换为显式：

```text
complete(value)
pause(kind, message)
```

Cancellation 通过 `engine.on_progress` 检查 token，即使脚本在纯计算 loop 中也能停止。

---

## 28. Host functions

脚本可调用的主要能力：

```text
agent(prompt [, opts])
parallel([opts...])
phase(title)
log(message) / print / debug
telemetry_event(name, fields)
complete([value])
pause(kind, message)
await_user(kind, message)
budget()
render_template(name, vars)
write_scratch_file(name, content)
read_scratch_file(name)
git_diff_since(commit)
```

这些函数不会直接访问 shell 内部对象，而是构造 `WorkflowHostRequest` 发给 HostService。

Result-bearing call 会进入 journal；纯 UI/telemetry emit 会带 `replayed` 标记，恢复重放时避免重复持久日志或 telemetry。

---

## 29. `AgentOpts`

```rust
pub struct AgentOpts {
    pub prompt: String,
    pub label: Option<String>,
    pub model: Option<String>,
    pub max_output_tokens: Option<u64>,
    pub agent_type: Option<String>,
    pub capability_mode: Option<String>,
    pub isolation_worktree: bool,
    pub fork_context: bool,
    pub resume_from: Option<String>,
    pub output_schema: Option<Value>,
    pub phase: Option<String>,
}
```

`max_output_tokens` 已 deprecated 并忽略；当前 Workflow 预算逻辑 child call 数量，而不是对每个 child 强制输出 token cap。

Prompt 最大 1 MiB；label 和 phase 各最多 256 bytes。

`fork_context` 只允许 built-in workflow。Project/user/inline script 即使可信可读，也不能复制父完整对话。

如果同时 `resume_from`，resume 获胜并关闭 fork。

---

## 30. Workflow child request

HostService 把 `agent()` 转成：

```text
run_in_background = false
surface_completion = false
await_to_completion = true
owner = Workflow { run_id }
parent_prompt_id = None
```

关键含义：

- Rhai call 必须拿到结果才能继续；
- 不受普通 foreground await budget 自动转后台；
- child completion 不单独唤醒父模型；
- UI 由 Workflow tracker/notification 汇总；
- ParentSession Stop 不误杀 workflow lineage；
- workflow pause/cancel 按 WorkflowRunId 精确取消。

Workflow-owned child 还会被移除 scheduler management tools，避免脚本 child 递归创建定时任务。

---

## 31. `agent()` 预算记账

在 live host call 前：

```text
ReserveAgentCalls(1)
  ↓
Tracker 检查 agents_used + 1 <= limit
  ↓
更新 agents_used/revision
  ↓
同步 persist_now
  ↓
才允许 spawn
```

先持久化 reservation 的原因：如果进程在 child 启动后崩溃，恢复不能忘记已经花掉的预算。

如果 reservation persistence 失败，会释放 in-memory count 并拒绝 spawn。

### 31.1 Replay 不重复收费

Engine 在 reserve 前查 journal：

```text
该 seq/kind/hash 已有记录
  → replay result
  → 不 reserve

没有记录
  → live call
  → reserve
```

### 31.2 Panel 预留

`parallel()` 先扫描所有 item，计算其中有多少是 live，而不是逐个启动到一半才发现预算不足。

然后一次 reserve `live_count`。

如果 panel 超过剩余预算，所有 child 都不启动，避免部分 fan-out 成功、部分被预算拒绝的不稳定状态。

---

## 32. `parallel()`

单次最多：

```rust
MAX_PARALLEL = 1024
```

执行过程：

1. 每项 parse 为 `AgentOpts`；
2. 计算 request hash；
3. 预扫描 journal；
4. 原子 reserve live count；
5. 为每项分配连续 seq；
6. replay 已完成项；
7. 同时发送所有 live SpawnAgent；
8. blocking 收集 replies；
9. 处理 terminal sentinel；
10. 按 seq 写 journal；
11. 保持原数组顺序返回结果。

Rhai blocking thread 在等待 replies，但 HostService 对每个 SpawnAgent `tokio::spawn`，所以 child 真正并行。

---

## 33. Journal：确定性恢复的核心

每条 result-bearing host call 记录：

```rust
pub struct JournalEntry {
    pub seq: u64,
    pub kind: String,
    pub req_hash: String,
    pub result: Value,
    pub at_ms: u64,
}
```

恢复不是从程序计数器继续，而是：

```text
从头重新执行 script
  ↓
每次 host call 得到相同 seq
  ↓
计算 kind + request hash
  ↓
Journal 中匹配
  → 返回旧 result，不再做真实副作用

到达 journal 尾部
  → 从这里开始执行 live calls
```

这是一种 deterministic replay。

### 33.1 Dense sequence

Seq 必须：

```text
0, 1, 2, 3, ...
```

load 和 append 都校验，缺号或乱序直接拒绝。

### 33.2 Request hash

同一 seq 要求：

```text
kind 相同
request hash 相同
```

否则 `JournalError::Divergence`：

```text
脚本非确定性
或 resume 时脚本被修改
```

这就是为何原始 script 和 args 必须 immutable。

### 33.3 Journal 限制

```text
最大 64 MiB
最多 10,000 entries
regular file
拒绝 symlink
O_NOFOLLOW on Unix
bounded read
```

Append 前检查新行是否会超过恢复上限，避免产生一个能继续写、但下次无法 load 的 stranded run。

### 33.4 Torn tail recovery

最后一行如果是崩溃写到一半：

- 可 parse：补 newline；
- 不可 parse：truncate 到上一完整 offset；
- 中间行损坏：失败，不猜测修复。

只有 tail 能安全判断为未提交写入，中间损坏可能改变已执行副作用的语义。

---

## 34. Host error 与恢复

普通 host failure 会以 sentinel 记进 journal：

```json
{"__xai_workflow_host_error": "..."}
```

Replay 时重新抛出相同 runtime error。

Failed run 被显式 resume 时，Manager 会检查 failure detail，并只 prune 最后一条匹配的 host-error entry，使那个失败调用重新 live 执行。

这实现：

```text
以前成功的步骤 replay
最后失败的外部调用重试
```

而不是整个 workflow 从零重复副作用。

Budget/cancel 等可恢复 terminal 也有专用 sentinel/release 规则，避免把未完成 child 计成永久 journal result。

---

## 35. Structured output contract

Workflow `agent()` 的 `output_schema` 不直接放进 SubagentRequest 的 sampler structured-output 字段。

Host side 做：

1. compile JSON Schema；
2. 拒绝 external `$ref`；
3. 限制 schema 256 KiB；
4. 使用受限 regex engine；
5. 把 `<output-contract>` 加到 prompt；
6. child 正常完成；
7. 从最后 `json` fence、完整文本或内嵌 object/array 中抽取 JSON；
8. host validator 校验。

输出最大 2 MiB。

### 35.1 一次 corrective retry

```rust
SCHEMA_CONTRACT_RETRIES = 1
```

第一次格式不合格：

- 用源 child ID `resume_from`；
- 新 child session ID；
- prompt 只要求修正 JSON；
- 不 fork；
- roster row rebind 到新 child ID。

逻辑 agent budget 不重复收费，但物理 agent run 有上限：

```text
MAX_AGENT_BUDGET × (retries + 1)
```

当前即 2048 physical runs。

---

## 36. Workflow HostService 的其他安全边界

主要上限：

| 项目 | 上限 |
| --- | --- |
| Agent prompt | 1 MiB |
| Template output | 1 MiB |
| Phase | 256 bytes |
| Log | 4 KiB |
| Script telemetry events | 64 |
| Scratch files | 64 |
| 单 scratch file | 10 MiB |
| Scratch total | 64 MiB |
| Scratch filename | 255 bytes |

Scratch I/O 有单独 mutex，避免并发写/配额检查竞态；路径限制在 run scratch root 下。

Git diff、template render 和 scratch result 也通过 host request/journal，因此 resume 不会在旧步骤上产生新的不确定输出。

---

## 37. Tracker state machine

状态：

```text
Active

UserPaused
BackOffPaused
NoProgressPaused
InfraPaused
Blocked
BudgetLimited

Interrupted
Complete
Failed
Cancelled
```

Terminal：

```text
Interrupted
Complete
Failed
Cancelled
```

Completion-reportable 还包括 `BudgetLimited`。

Resumable：

- 所有 paused；
- BudgetLimited；
- Failed。

不可恢复：

- Complete；
- Cancelled；
- Interrupted。

### 37.1 Pause kinds

Rhai `pause()` 映射：

```text
user         → UserPaused
back_off     → BackOffPaused
no_progress  → NoProgressPaused
verification → Blocked
infra        → InfraPaused
```

### 37.2 Revision/history

WorkflowRunState 每次有意义变化会 `advance_revision()`。

History 最多 64 项，记录：

```text
workflow_started
phase_entered
log
workflow_paused/resumed
workflow_budget_limited
workflow_completed/failed/cancelled/interrupted
... 
```

UI update 带 revision，用于忽略陈旧事件。

### 37.3 Agent roster

最多 256 rows，每项记录：

```text
agent_id
label
phase
model
state
tokens_used
duration_ms
```

超限时优先删除一个非 running 旧 row，不会为了显示新项丢掉当前 active child。

---

## 38. Pause、Cancel 与 child drain

Manager pause：

1. 从 active 移出；
2. `pause_intent=true`；
3. cancel workflow token；
4. 按 WorkflowRunId cancel child lineage；
5. 放入 retiring；
6. Tracker 立即标 UserPaused；
7. 发 update。

Cancel 类似，但目标状态是 Cancelled。

HostService channel 关闭或 cancel 后，会：

- 回复队列中 request 为 Cancelled；
- 向 SubagentCoordinator 发 `WorkflowRunId` cancel；
- 最多等待 20 秒，直到所有 workflow child drain。

Manager watcher 最多等 host drain 25 秒。

如果 cleanup 无法确认：

```text
run → Interrupted
cannot resume
```

系统宁可禁止恢复，也不在未知 child 副作用状态下 replay。

---

## 39. Outcome watcher

Rhai executor 返回后，watcher：

1. 若 host 尚未结束，cancel host；
2. 等 child drain；
3. 处理 cleanup failure；
4. 检查 execution epoch，防止旧 watcher覆盖新 resume；
5. 根据 pause intent 或 WorkflowOutcome 更新 Tracker；
6. acknowledged persist terminal manifest；
7. persist 失败则改为 Interrupted 并再次持久化；
8. 广播 WorkflowUpdated；
9. completion-reportable 时发 `WorkflowCompletionTurn`；
10. 完成 done/outcome oneshots。

`execution_epoch` 是防 stale completion 的关键：同一 run pause/resume 后，旧 executor 的迟到 outcome 不应覆盖新执行状态。

---

## 40. Workflow completion 如何回到父 Agent

Workflow child 都 `surface_completion=false`，不会逐个 auto-wake。

统一回流是：

```text
Tracker terminal/budget-limited
  ↓
SessionCommand::WorkflowCompletionTurn { run_id, revision }
  ↓
Session 检查该 revision 仍未报告
  ↓
构造 workflow completion/status prompt
  ↓
父 Agent 新 turn
```

UI 同时通过 ACP extension：

```text
x.ai/session_notification
  WorkflowUpdated
```

看到 phases、agent roster、预算、耗时、pause message 和 result summary。

这避免 100 个 panel child 各自唤醒父 Agent 100 次。

---

## 41. Store 目录与 immutable source

每个 run：

```text
<session>/workflows/<run_id>/
  args.json
  script.rhai
  scripts/0000.rhai
  state.json
  journal.jsonl
  scratch/
```

`args.json` 和 `scripts/0000.rhai` 用 create-new atomic write，建立不可变 resume source。

`script.rhai` 是 editable projection，方便用户修改并作为新 run 启动。

重要区别：

```text
编辑 script.rhai
  ≠ 修改当前 run

resume current run
  → 仍用 store 中 immutable script/args

从 edited script_path launch
  → 新 run
```

### 41.1 Manifest

```rust
pub struct WorkflowRunManifest {
    pub version: u8,
    pub state: WorkflowRunState,
    pub script_revision: u32,
}
```

当前 version 4。

限制：

```text
最多恢复 128 runs
manifest 最大 512 KiB
args 最大 1 MiB
```

旧版本或没有 agent budget accounting 的 manifest 会标 Interrupted，不允许 resume。

### 41.2 Atomic persistence

Store 使用：

```text
create temp with create_new
write all
sync_all
rename
```

读取使用 regular-file/no-symlink/bounded/O_NOFOLLOW 检查。

Terminal state 使用 `persist_ack()`，必须等待 session persistence actor 确认后才报告最终 completion。

---

## 42. 进程重启后的语义

Restore 时：

- token leases 未解决：全部计入 used，标 usage incomplete；
- Active run：改为 Interrupted；
- running agent roster row：改为 cancelled；
- 记录恢复中断 history；
- completion-reportable 但尚未报告的状态可重新触发 status/completion surface。

源码明确告诉用户：

```text
same-process paused run 可 resume
process-restart interruption 是 terminal
```

为什么 journal 已持久化却仍不允许进程重启恢复？

因为当前系统没有稳定的 operation identity 来证明崩溃时 in-flight child 和 host side effect 的最终状态。Journal 只能证明已记录完成的 calls，不能证明最后一个未记录 call 是否“执行了副作用但来不及记账”。

因此采取安全策略：标 Interrupted，要求新 run。

---

## 43. `/workflow` 管理命令

支持：

```text
/workflow <name> [args]
/workflow pause <display-name>
/workflow resume <display-name>
/workflow stop <display-name>
/workflow save <display-name>
```

选择 run 时：

- exact display name/run ID 优先于 prefix；
- 多个 prefix match 时按操作可适用状态缩小；
- 仍多项则要求用户指定。

BudgetLimited resume 需要更高的 absolute agent budget，不是“再追加 N 个”。达到 1024 后不能 resume。

`save`：

- 保存到 project `.grok/workflows/<name>.rhai`；
- 需要 folder trust；
- atomic no-clobber；
- duplicate-run display handle 不能直接保存；
- built-in 不允许覆盖，需改新 meta.name。

---

## 44. Scheduler 与 Workflow 的取消差异

Scheduler delete：

- 删除未来 timer；
- 不等价于自动 kill 已经 fire 的最后 child；
- loop overlap guard 会追踪 lineage，但 task 被删除后 actor 不再安排新 iteration。

Workflow stop：

- cancel executor；
- cancel HostService；
- 按 run ID cancel所有 pending/active child；
- 等待 drain；
- 更新 terminal state。

因此 Workflow 的生命周期 ownership 更强。

---

## 45. 两套完整时序图

### 45.1 Scheduler

```text
Parent model
  │ scheduler_create
  ▼
SchedulerCreateTool
  │ SchedulerCommand::Create
  ▼
SchedulerActor
  │ mutate SchedulerState
  │ version + Created notification
  │ tool finalize persists Resources
  │
  │ sleep until next_fire_at
  ▼
fire_next_task
  │ advance cadence first
  │ expiry/overlap/lineage checks
  │
  ├─ foreground → ScheduledTaskFired(prompt)
  │                 → main Session turn
  │
  └─ background → SubagentEvent::Spawn
                    → loop child
                    → completion reminder/auto-wake
                    → next fire may resume child
```

### 45.2 Workflow

```text
Parent model
  │ workflow(source,args,budget)
  ▼
WorkflowTool
  │ WorkflowLaunchEnvelope
  ▼
Session launch consumer
  │ resolve/trust/validate
  ▼
WorkflowManager
  │ register immutable source
  │ persist initial state
  ├─ spawn_blocking Rhai executor
  └─ spawn async HostService

Rhai script
  │ phase()/agent()/parallel()
  ▼
WorkflowHostRequest
  │ reserve budget durably
  ▼
HostService
  │ SubagentRequest owner=Workflow(run_id)
  ▼
SubagentCoordinator
  │ await child completion
  ▼
HostService
  │ AgentResult
  ▼
Rhai engine
  │ journal result
  │ continue/replay/pause/complete
  ▼
Manager watcher
  │ drain children
  │ persist terminal manifest with ACK
  │ WorkflowUpdated
  └─ WorkflowCompletionTurn
        → Parent model
```

---

## 46. 设计上值得学习的地方

### 46.1 Scheduler 先推进 cadence

异步 timer 系统中，先更新选择条件再释放锁，比 spawn 完再更新更能防止重复选择。

### 46.2 Fail-closed overlap check

无法确认上一 iteration 是否结束时选择 skip，而不是假设结束。对可能产生外部副作用的循环任务，这是合理默认。

### 46.3 Event version + tombstone ACK

删除不是“从 Vec remove 就完成”，还考虑持久 consumer 是否已经确认 absence。这是跨进程 UI/state replication 中常被忽略的一层。

### 46.4 Deterministic replay journal

Workflow 不是序列化 Rhai VM 栈，而是从头执行、重放 host results。实现简单得多，但要求 script/args 确定且 immutable。

### 46.5 预算在副作用前持久 reservation

先记账再 spawn，恢复时不会因为 crash 忘记已经消耗的工作。

### 46.6 Panel 原子 admission

`parallel()` 先预留整批逻辑预算，避免半个 panel 启动。

### 46.7 Child completion 聚合

Workflow 不让每个 child 单独 auto-wake，而只在 run level 汇总回流，控制父 context 噪声。

### 46.8 Unknown cleanup → Interrupted

当无法确认 child 全部 drain 或 terminal manifest 持久化失败时，不允许 resume。这种保守状态机适合有副作用的 orchestration。

---

## 47. 当前局限与可研究方向

### 47.1 Scheduler 不是 cron

缺少：

- calendar expressions；
- timezone-aware wall-clock schedule；
- misfire policy；
- jitter；
- catch-up/backfill；
- per-task retry policy。

### 47.2 Scheduler task ID 碰撞

12 hex-like chars 足够日常使用，但 actor 可增加 collision detect/retry，使正确性不依赖概率。

### 47.3 Scheduler mailbox/backpressure

仍使用 unbounded channel，极端 command storm 下没有自然背压。

### 47.4 Strong isolation

Scheduler/Workflow child 的 worktree 创建失败仍可能 fallback shared workspace。脚本作者不能把 isolation flag 当作强制安全承诺。

### 47.5 Workflow crash-resume

若要支持进程重启恢复，需要：

- stable operation IDs；
- child job durable backend；
- exactly-once/at-least-once side-effect contract；
- lease/fencing token；
- 最后 in-flight host call reconciliation。

### 47.6 Journal schema evolution

Request hash 对脚本和 host protocol 变化很敏感。可引入 host call version、migration 和兼容 replay policy。

### 47.7 Workflow permission manifest

Launch 前可静态声明/展示：

```text
预计 agent types
capability modes
MCP/skills
filesystem/network needs
max parallelism
```

让用户在大 fan-out 前理解能力与成本。

### 47.8 Cost-aware budget

当前 agent budget 只数逻辑 calls，一个轻量 explore 与大型 implementer 都算 1。可结合 model price、token budget、parallel peak 和 wall time。

### 47.9 Scheduler + Workflow composition

当前 scheduler 主要触发 prompt，Workflow 顶层启动受到 session/tool context限制。若支持“定时启动已注册 Workflow”，需要明确 run admission、重复触发、上一 run overlap 与 durable result ownership。

---

## 48. 推荐阅读顺序

Scheduler：

```text
scheduler/create.rs
  ↓
scheduler/types.rs
  ↓
scheduler/actor.rs
  ↓
scheduler/interval.rs
  ↓
scheduler/occurrence_journal.rs
  ↓
tool registry finalize/persistence
```

Workflow 协议：

```text
grok_build/workflow/mod.rs
  ↓
xai-workflow/meta.rs
  ↓
xai-workflow/host.rs
  ↓
xai-workflow/engine.rs
  ↓
xai-workflow/journal.rs
```

Shell 集成：

```text
workflow/registry.rs
  ↓
acp_session_impl/spawn.rs launch consumer
  ↓
workflow/manager.rs
  ↓
workflow/host_service.rs
  ↓
workflow/tracker.rs
  ↓
workflow/store.rs
  ↓
workflow/notify.rs
```

---

## 49. 调试清单

Scheduler 不创建：

```text
interval 格式/最小值
prompt 是否存在
是否误传 recurring=false
是否达到 50 项
SchedulerHandle/actor 是否存在
pending durable removal
```

Scheduler 到点不 fire：

```text
next_fire_at
7-day expiry
blocked_expiries
pending_removal
SchedulerBackgroundLoops
subagent wiring
上一 child 是否 running
loop descendant 是否 active
query 是否 timeout/channel closed
```

Scheduler 重复或重叠：

```text
last_fired_at 是否先推进
last_subagent_id 是否写入
loop_task_id lineage
spawn failure rollback
update prompt 是否保留 in-flight anchor
```

Workflow 无法 launch：

```text
feature flag
top-level depth
source 是否恰好一个
path trust/symlink/size
meta first statement
name/filename match
active + retiring 是否达到 4
initial store persistence
```

Workflow 卡住：

```text
Rhai blocking executor
host request channel
active agent roster
SubagentCoordinator
permission prompt
agent budget
max ops/host calls
child drain
```

Workflow resume divergence：

```text
是否使用 immutable script/args
journal seq 是否 dense
kind/request hash 是否一致
是否编辑了当前 run source
是否使用 wall clock/non-deterministic branching
最后 host error 是否可 prune
```

Workflow 完成但父模型没收到：

```text
terminal manifest persist_ack
status 是否 completion-reportable
execution_epoch
WorkflowCompletionTurn 是否发出
run revision 是否仍 current/unreported
session command channel
```

---

## 50. 总结

Scheduler 的核心公式：

```text
persisted task state
  + single-writer timer actor
  + versioned notifications
  + subagent chain/overlap guard
  = 可持续的周期性 prompt loop
```

Workflow 的核心公式：

```text
immutable Rhai script + args
  + deterministic host-call journal
  + durable logical-agent budget
  + async HostService
  + workflow-owned subagents
  + tracker/store/notification
  = 可暂停、可观测、同进程可恢复的多 Agent 编排
```

最关键的区别是：

> Scheduler 解决“什么时候再次做”，Workflow 解决“这次复杂工作按什么结构做”。

两者都复用了 SubagentCoordinator，但在它之上建立了不同的 ownership、持久化、去重和结果回流协议。真正值得学习的不是“能启动多个 Agent”，而是它们如何在异步、失败、暂停、重放、预算和 UI 多消费者之间维持一致状态。
