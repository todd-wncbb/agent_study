# 源码精读 22：Agent Session Protocol Runtime——ACP 入口、Session Registry、Handle、Gateway、多客户端路由与驻留生命周期

> 源码基线：`ed6d543`
>
> 本文关注的不是“Agent 怎样思考”，而是更靠外的一层：一个客户端请求怎样进入 Agent，怎样找到正确的 SessionActor，怎样跨越重连、并发 load、多客户端 leader、通知回放和进程驻留边界，最后得到唯一、可路由的响应。

---

## 1. 先给结论：这里其实有五个互相咬合的状态机

初看 ACP 入口，很容易形成一个过度简单的认识：

```text
JSON-RPC request
    -> MvpAgent::prompt
    -> SessionActor
    -> JSON-RPC response
```

这条线没有错，但它遗漏了真正难的部分。

Grok Build 的 Session Protocol Runtime 同时维护五个状态机：

1. **ACP 请求状态机**：请求、通知、反向请求与响应怎样对应；
2. **Session presence 状态机**：Session 是 resident、attaching、dormant、closed 还是 dead；
3. **Actor 控制状态机**：`SessionHandle` 怎样把外部操作串行送给唯一 SessionActor；
4. **通知交付状态机**：持久化、实时转发、replay、delta drain 与 response boundary 怎样排序；
5. **多客户端路由状态机**：谁是 subscriber，谁是 driver，哪些消息广播、单播或只交给 driver。

可以先把全局结构记成：

```text
Client A ─┐
Client B ─┼─> Leader Router ─> ACP Agent connection ─> MvpAgent
Client C ─┘         │                                  │
                    │                                  ├─ SessionRegistry
                    │                                  │      └─ SessionPresence
                    │                                  │
                    │                                  └─ SessionHandle
                    │                                         │ command channel
                    │                                         v
                    │                                    SessionActor
                    │                                         │
                    └<──── Gateway <──── notifications / reverse requests
```

最重要的一条阅读原则是：

> `SessionId` 只是路由键；`SessionRegistry` 保存宿主事实；`SessionHandle` 是控制代理；`SessionActor` 才是会话运行时的单一写入者；leader 决定多个外部客户端怎样共享这一个写入者。

---

## 2. 本文源码地图

核心入口如下。

| 层次 | 文件 | 关键符号 | 作用 |
| --- | --- | --- | --- |
| ACP trait 入口 | `crates/codegen/xai-grok-shell/src/agent/mvp_agent/acp_agent.rs` | `impl acp::Agent for MvpAgent` | 接收标准 ACP 方法与 xAI 扩展方法 |
| Session 创建/加载 | `.../mvp_agent/session_setup.rs` | `new_session_inner`、`attach_session` | 构建或恢复 SessionActor |
| Registry | `.../mvp_agent/session_registry.rs` | `SessionRegistry`、`SessionPresence` | 维护 residency、attach、thread 与 retained state |
| 生命周期 | `.../mvp_agent/session_lifecycle.rs` | `close_active_session`、`sweep_dead_sessions` | close、reap、supervise、idle unload |
| Agent 辅助操作 | `.../mvp_agent/agent_ops.rs` | `spawn_and_register_session`、`session_handle_waiting_for_load` | Actor 注册、load 等待与资源接线 |
| Actor 代理 | `crates/codegen/xai-grok-shell/src/session/handle.rs` | `SessionHandle` | 从协议层向 actor 发送 typed command |
| 通知代理 | `.../session/notifications.rs` | `NotificationSender` | 组合 gateway、转发 gate 与 persistence |
| 未决交互 | `.../session/pending_interaction.rs` | `PendingInteractionGuard` | 记录阻塞式 permission/question/approval |
| Replay | `.../mvp_agent/replay.rs` | `forward_raw_replay_line` | 重新发送历史事件并标记 replay/target |
| Gateway | `crates/codegen/xai-acp-lib/src/gateway.rs` | `AcpGatewaySender`、`AcpGatewayReceiver` | 把 `!Send` ACP connection 包装成 channel 边界 |
| Leader 路由 | `crates/codegen/xai-grok-shell/src/leader/server.rs` | `session_subscribers`、`session_driver` | 多客户端订阅、驱动、单播、广播与缓冲 |

本文按“请求进入 → 找到 Session → 驱动 Actor → 发出事件 → 多客户端路由 → 退出/重连”阅读。

---

## 3. ACP 在这里扮演什么角色

ACP 可以先理解为 Agent 与 Client 之间的双向协议。

Client 发给 Agent 的标准方法包括：

- `initialize`；
- `authenticate`；
- `session/new`；
- `session/load`；
- `session/resume`；
- `session/prompt`；
- `session/cancel`；
- `session/set_mode`；
- `session/set_model`；
- `session/close`。

Agent 也会反向调用 Client：

- 发 `session/update`；
- 请求文件读写；
- 创建、查询和终止终端；
- 请求工具权限；
- 调用 `x.ai/ask_user_question` 等扩展方法。

所以 ACP connection 不是一个单向 HTTP handler。

它更像：

```text
ClientSide                       AgentSide
----------                       ---------
request session/prompt   ----->  handle prompt
receive session/update   <-----  send notification
answer permission        <-----> reverse request + response
```

这也是 Gateway 必须存在的根本原因：SessionActor、Tool Runtime 和后台任务都可能在未来某个时刻需要向 Client 发消息，它们不能直接持有并随意调用底层连接。

---

## 4. `impl acp::Agent for MvpAgent`：协议入口只做边界工作

`acp_agent.rs` 中的 trait 实现是整个协议面的总入口。

标准方法通常很薄：

```rust
async fn new_session(&self, arguments: acp::NewSessionRequest) -> Result<..., ...> {
    self.new_session_inner(arguments).await
}

async fn load_session(&self, arguments: acp::LoadSessionRequest) -> Result<..., ...> {
    self.load_session_inner(arguments).await
}
```

这层的职责不是承载全部业务，而是完成四类边界动作：

1. 解析 protocol request 与 `_meta`；
2. 建立 tracing / telemetry 上下文；
3. 找到对应 Session；
4. 把请求转换成 `SessionCommand` 或分派给 extension handler。

这样做有一个很实用的效果：

- ACP 类型留在边界；
- actor 内部使用更贴近运行时的 typed command；
- extension 可以独立演进；
- Session 生命周期不会塞进一个巨大的 trait 文件。

---

## 5. 标准方法与扩展方法是两套路由表

标准 ACP 方法由 trait 的函数表决定。

xAI 扩展则进入：

```rust
async fn ext_method(&self, args: acp::ExtRequest) -> Result<acp::ExtResponse, acp::Error>
```

它按 method name 路由到不同模块，例如：

- `x.ai/session/*` → session handler；
- `x.ai/skills/*` → skills extension；
- `x.ai/mcp/*` → MCP extension；
- `x.ai/task/*` → task extension；
- `x.ai/terminal/*` → terminal extension；
- `x.ai/git/*` → workspace/git extension；
- `x.ai/hooks/*` → hooks extension；
- `x.ai/rewind/*` → rewind extension。

一个容易混淆的点是：

> “ACP extension method”不是“模型 Tool”。

前者是 Client 与 Agent runtime 之间的控制协议；后者是模型在 Agentic Loop 中发出的函数调用。

例如：

```text
x.ai/skills/list
```

是客户端打开 Skills 面板时调用 Agent。

而：

```text
read_file(...)
```

是模型在一次 Turn 中调用 Tool Runtime。

两者最终可能访问同一份数据，但调用者、生命周期和错误语义都不同。

---

## 6. 为什么 `MvpAgent` 不能直接保存 `HashMap<SessionId, SessionHandle>`

如果 Session 永远是“创建后一直运行，关闭后立即消失”，一个 map 足够了。

实际场景包括：

- Client 正在 reconnect；
- `session/load` 已开始但 Actor 还没注册；
- 原 Actor 正在 flush 并退出；
- Session 已从内存卸载，但磁盘记录仍可恢复；
- Actor 已 panic，JoinHandle 已结束；
- close 与 load 正在竞争；
- 新 Actor 替换了旧 Actor；
- Session 没有 client owner，但仍有运行中的 Turn。

这些都不能只用“map 中存在/不存在”表达。

因此源码把状态提升为：

```rust
enum SessionPresence {
    Resident { ... },
    Attaching { ... },
    Evicted { ... },
    Closed { ... },
    Dead { ... },
    Dormant { ... },
}
```

这是本文最关键的类型。

---

## 7. `SessionPresence`：让状态携带证明

源码注释强调，每个 variant 都携带“使它为真的证据”。

例如 Resident：

```rust
Resident {
    handle: Option<SessionHandle>,
    thread: Option<SessionThread>,
    activity: Activity,
}
```

这里包含：

- 控制 actor 的 handle；
- 追踪 actor 生死的 thread/join handle；
- actor 当前是 Working 还是 Idle。

这比平行字段安全：

```text
handle: Option<Handle>
thread: Option<Thread>
live_state: Option<State>
```

因为平行字段能表达许多非法组合，例如：

```text
live_state = Working
handle = None
```

而 `SessionPresence::Resident` 把相关事实收拢在同一个分支中。

---

## 8. Presence 的六种状态

### 8.1 `Resident`

Actor 已注册并驻留内存。

它再细分：

- `Activity::Working`；
- `Activity::Idle`。

投影到外部 `SessionLiveState` 后分别是：

- `Working`；
- `IdleResident`。

### 8.2 `Attaching`

正在执行 load/resume，actor 可能尚未构建完成。

它持有：

- `waiter`：让竞争请求等待；
- `displaced`：attach 前的旧 presence；
- `handle`：attach 中途可能已经注册的新 handle；
- `thread`：attach 中途可能已经注册的新 thread；
- `settled_activity`：attach 中途发生的 Working/Idle 更新。

### 8.3 `Evicted`

客户端所有权已消失，registry 不再对外报告 live state，但旧 actor thread 可能仍在 flush。

### 8.4 `Dormant`

Session 仍在磁盘上、可以恢复，但没有驻留 actor。

### 8.5 `Closed`

显式 close 后的终态投影。

这里的“终态”是 hosting/registry 语义，不代表会话文件不可读取。

### 8.6 `Dead`

Actor 异常退出。

它和 Closed 的区别是：

- Closed 是显式生命周期操作；
- Dead 是 runtime failure。

---

## 9. `SessionLiveState` 不是持久化业务状态

`SessionLiveState` 定义在 `session/handle.rs`：

```rust
enum SessionLiveState {
    Working,
    IdleResident,
    Dormant,
    Completed,
    DeadFailed,
    Attaching,
}
```

它主要回答：

> 这个进程当前怎样托管该 Session？

它不回答：

- 对话是否有答案；
- Goal 是否完成；
- 是否还能从磁盘 load；
- 最后一轮模型为什么停止。

因此不要把 `Completed` 等同于 Agentic Loop 的 `EndTurn`，也不要把 `Dormant` 等同于业务暂停。

---

## 10. `SessionResources` 为什么拆成 retained 与 resident

Registry entry 中还有：

```rust
struct SessionResources {
    retained: Option<RetainedResources>,
    resident: Option<ResidentResources>,
    presence: Option<SessionPresence>,
    unavailable_model: Option<acp::ModelId>,
}
```

可用生命周期解释：

```text
retained resources
    跨 actor reload 保留

resident resources
    actor 驻留期间有效，idle unload 时释放

presence
    当前宿主状态 + handle + thread

unavailable_model
    load 后模型不可用时的会话级阻塞信息
```

`RetainedResources` 中的典型内容包括：

- per-session `dispatch_lock`；
- turn number；
- permission event receiver。

为什么 dispatch lock 必须 retained？

因为同一个 SessionId reload 后仍应延续同一条请求提交顺序，而不是因为 actor 换了就突然出现第二把锁。

---

## 11. `SessionHandle`：它不是 Session 本身

`SessionHandle` 的第一字段就是：

```rust
pub cmd_tx: mpsc::UnboundedSender<SessionCommand>
```

因此最准确的理解是：

> `SessionHandle` 是一个可克隆的 actor proxy，加上一组为快速路由、观测和继承而缓存的共享引用与快照。

它包含几类字段。

### 11.1 控制通道

- `cmd_tx`；
- `persistence_tx`。

### 11.2 同步可观测信号

- `current_prompt_id`；
- `pending_interactions`；
- `gateway_enabled`；
- `force_compact`。

### 11.3 Actor 子系统 handle

- `chat_state_handle`；
- `signals_handle`；
- `hunk_tracker_handle`；
- `permission_handle`；
- terminal backend；
- scheduler handle。

### 11.4 会话级能力快照

- model id / reasoning effort；
- MCP servers；
- client filesystem/terminal/code-nav 能力；
- YOLO/auto mode；
- origin client；
- Agent Definition 与 allowed subagent types；
- hook registry；
- workspace ops。

---

## 12. 为什么 Handle 里既有快照，也有 actor command

有些状态适合直接读：

- 当前 SessionId、cwd；
- origin client；
- 能否使用 code navigation；
- 当前是否有 prompt id；
- 是否有 pending interaction。

另一些状态只能询问 actor：

- 当前 prompt mode；
- 当前 model metadata；
- prompt queue 是否为空；
- background task 列表；
- 最新 MCP runtime 状态。

区分依据是：

```text
跨模块频繁读取、可由共享原子量表达
    -> Handle 上的 Arc / Atomic / snapshot

需要与 actor 内部状态严格排序
    -> SessionCommand + oneshot response
```

例如 `is_busy()` 不直接猜 actor 状态，而是发送：

```rust
SessionCommand::IsBusy { respond_to: tx }
```

如果 actor 不可达，它保守返回 `true`。

这体现了一条安全偏好：

> 不确定是否有工作时，宁愿暂时保持 resident，也不要错误卸载一个仍有活动的 Session。

---

## 13. `spawn_and_register_session`：Actor 真正落入 Registry 的时刻

创建或恢复 Session 最终都会进入：

```rust
MvpAgent::spawn_and_register_session
```

该函数负责组装：

- filesystem backend；
- client capabilities；
- Gateway 与 notification gate；
- PersistenceHandle；
- MCP runtime；
- ToolContext；
- Agent Definition；
- hooks；
- model config；
- chat history；
- terminal/scheduler/process scope。

Actor 启动后，注册顺序尤其值得注意：

```text
spawn actor + obtain SessionHandle/SessionThread
    -> registry.set_thread(...)
    -> set_live(IdleResident)
    -> ensure_session_supervisor()
    -> initialize / advertise commands
    -> register activity and permission receiver
    -> insert_resident(handle)
```

在 attach 期间，这些写入不会粗暴覆盖 `Attaching`；Registry 会把 thread、handle 与 activity 暂存在该 variant 中，最后由 guard drop 统一 settle。

---

## 14. `session/new` 的主链路

`new_session_inner` 大致执行：

```text
验证 initialize 已完成
    -> resolve cwd / workspace / MCP
    -> 解析 request meta
    -> 决定或验证 SessionId
    -> 解析 client identity 与 capability
    -> 解析 model / agent type / permission mode
    -> 创建 persistence
    -> spawn_and_register_session
    -> 应用初始 model
    -> 返回 session id + model state + response meta
```

这里的 SessionId 默认使用 UUID v7。

客户端也可通过 `_meta.sessionId` 提供 id，但必须通过 UUID 格式校验。

为什么允许客户端提供？

一个重要用途是外部协调层预先分配稳定 identity，使 Session 创建与更高层 workspace/leader 状态能够关联。

---

## 15. `initialize` 与 Session 能力的关系

`initialize` 建立的是连接/客户端级能力。

但 Grok Build 不会把所有 client capability 永久视为全局事实。

在多客户端 leader 模式下，不同客户端可能拥有不同能力：

- 有的支持 terminal；
- 有的支持 client-side filesystem；
- 有的支持 code navigation；
- 有的启用 yolo；
- 不同 product/version 可能行为不同。

因此 Session 创建时会把相关能力解析并固化到 `SessionHandle`。

这避免：

```text
Client B 后来 initialize
    -> 覆盖 MvpAgent 上的共享 client config
    -> Client A 已存在的 Session 突然改变能力
```

源码多处注释称这种错误为 cross-client contamination。

---

## 16. `session/load` 不是“读文件然后返回”

`load_session_inner` 进入统一的：

```rust
attach_session(arguments, AttachOperation::Load)
```

`resume_session_inner` 也会转换为 LoadSessionRequest，然后进入相同函数，只是 `AttachOperation::Resume` 改变 policy。

attach 的完整职责包括：

1. 注册 attach guard；
2. 等待旧 actor thread 尽量完成 flush；
3. 解析 workspace 与请求能力；
4. 轻量加载 persistence summary；
5. 关闭 resident actor 的实时输出 gate；
6. replay transcript；
7. 重新创建 actor，或更新现有 actor；
8. 恢复 model、signals、plan、workflow 等状态；
9. 重新打开输出 gate；
10. drain replay completion；
11. 返回 attach response。

所以 load 同时是一条：

```text
恢复管线 + reconnect 管线 + 通知切换管线
```

---

## 17. `SessionLoadGuard`：把所有退出路径统一成 settle

attach 一开始执行：

```rust
let _load_guard = self.begin_session_load(&arguments.session_id);
```

Guard 持有：

- agent 引用；
- session id；
- watch receiver；
- watch sender。

Drop 实现只有一件事：

```rust
self.agent
    .session_registry
    .settle_attach(&self.session_id, &self.rx);
```

这意味着以下路径都会 settle：

- load 成功；
- 读取 persistence 失败；
- workspace 校验失败；
- spawn actor 失败；
- model restore 中途返回错误；
- future 被取消并 drop。

RAII 在这里不是代码风格，而是并发正确性工具。

---

## 18. `begin_attach` 为什么保存 displaced presence

开始 attach 时，Registry 不只是写：

```text
state = Attaching
```

它会把原 presence 移入 `displaced`。

例如原来是：

```text
Resident(Working, old_handle, old_thread)
```

开始 reconnect load 后变为：

```text
Attaching {
    displaced = Resident(Working, ...),
    handle = clone(old_handle),
    ...
}
```

如果 attach 失败，`settle_attach` 可以恢复旧 presence。

否则，一个失败的 reconnect 就可能把仍在工作的 actor 错误降级为 Dormant 或直接丢失。

---

## 19. attach 中途为什么允许 handle 和 thread“落入”

创建新 Actor 不是原子操作。

可能出现：

```text
begin_attach
    -> actor spawned
    -> thread registered
    -> handle registered
    -> more restore work
    -> response assembled
    -> guard drops
```

因此 `Attaching` 必须能容纳：

- 新 thread；
- 新 handle；
- attach 中途产生的 activity update。

`put_resident` 和 `set_thread` 识别 `Attaching`，把资源放进当前 attach，而不是提前把 presence 改回 Resident。

这使 `wait_for_load_to_settle` 能准确判断：

> handle 已存在，并不表示整个 attach 已完成。

---

## 20. `settle_attach` 的三条路径

### 20.1 新 handle 已存在

根据：

- `current_prompt_id`；
- `settled_activity`；
- thread 优先级；

建立新的 Resident presence。

### 20.2 没有新 handle，但有 displaced

恢复旧 presence。

如果旧 Resident 的 channel 已关闭，会降为 Dormant，避免制造一个有 handle 外形但 actor 已死的 resident zombie。

### 20.3 新旧都没有

生成临时 Evicted，再调用 release 清理空 entry。

此外，`waiter.same_channel(...)` 用于确认 settle 的 guard 仍然是当前 attach owner。

这解决嵌套/重叠 attach：旧 guard 晚 drop 时不能把新 attach 的状态结算掉。

---

## 21. racing request 怎样等待 load

协议层不能在 prompt 到达时只做：

```rust
resident_handle(session_id)
```

否则 leader 重连时常见的序列会失败：

```text
session/load arrives
session/prompt arrives immediately after it
```

第二个请求可能在 actor 注册前查 map，得到 unknown session。

统一入口是：

```rust
session_handle_waiting_for_load(session_id).await
```

逻辑为：

```text
已有 resident handle?
    yes -> 立即返回
    no  -> 查看 attach waiter
             no waiter -> 确实不存在
             waiter    -> 最多等 60 秒
                          -> 再查 resident handle
```

源码把它称为 post-leader-crash error class 的 chokepoint。

---

## 22. 为什么 waiter 用 watch channel closure 唤醒

`begin_attach` 创建 watch channel，但不依赖发送某个业务值。

Guard drop 时 sender 一同 drop，等待方的 `rx.changed()` 因 channel closed 返回。

优点是：

- 不需要在每个 error return 前记得 `send(done)`；
- future cancellation 也能唤醒；
- 多个 waiter 都能观察关闭；
- waiter identity 可用 `same_channel` 比较。

等待方醒来后必须重新检查 Registry，而不能把“channel closed”直接理解为成功。

因为 guard drop 只表示 attach 已结束，可能成功，也可能失败。

---

## 23. load 前为什么要 drain old thread

idle unload 的步骤是：

```text
send Shutdown
    -> drop SessionHandle
    -> keep SessionThread tracked
    -> mark Dormant
```

Actor 可能仍在进行最后的 persistence flush。

如果立刻 load 并 replay 同一份 `updates.jsonl`，新旧 actor 可能同时读写。

因此 `drain_old_session_thread`：

- 每 10ms 异步检查一次；
- 默认最多等待 5 秒；
- 已完成则清除 thread；
- 超时则告警并继续。

这里选择 bounded wait，而不是无限等待。

它在两种风险之间折中：

```text
无限等旧 actor
    -> reconnect 永久卡死

完全不等
    -> replay 可能看不到最后写入
```

---

## 24. `prompt` 进入 actor 前做了什么

`MvpAgent::prompt` 的前半段主要完成：

1. 关联 trace meta；
2. 等待 Session attach；
3. 检查 model allowlist/unavailable latch；
4. 获取 per-session dispatch lock；
5. 解析 prompt mode；
6. 生成 prompt id；
7. 分配 turn number；
8. 准备 tracing/upload metadata；
9. 查询当前 model；
10. 解析 verbatim、sendNow、outputSchema、toolOverrides；
11. 构造 `SessionCommand::Prompt`。

发送后才释放 dispatch lock：

```rust
handle.cmd_tx.send(SessionCommand::Prompt { ... })?;
drop(dispatch_guard);
```

注意：锁只覆盖“提交到 actor mailbox”，不覆盖整轮模型执行。

否则第二个 prompt 或 cancel 会被一轮可能持续数分钟的模型执行阻塞。

---

## 25. `dispatch_lock` 保护的不是 Actor 内部状态

Actor mailbox 本身已经串行。

为什么还要一把外部 lock？

因为多个 ACP handler 是并发 future，它们在向 mailbox 发送前还做异步准备。

如果没有 lock：

```text
Prompt A handler starts
Prompt B handler starts
B finishes preamble first -> send B
A sends later             -> send A
```

于是协议接收顺序和 actor intake 顺序不同。

`dispatch_lock` 把“最后一段 preamble + mailbox enqueue”变成 per-session 提交临界区。

它还让 Cancel 不会越过其前面刚到达、但尚未 enqueue 的 Prompt。

---

## 26. 为什么 lock 是 per-session，不是 global

不同 Session 的 prompt 应该并发。

因此 Registry 按 SessionId 懒创建：

```rust
Rc<tokio::sync::Mutex<()>>
```

结果是：

```text
Session A: Prompt A1 -> Prompt A2 -> Cancel A
Session B: Prompt B1 -> Prompt B2
```

各自有序，但 A 与 B 互不阻塞。

这也是 dispatch lock 放进 retained resources，而不是放进临时 handler local 的原因。

---

## 27. `prompt` 响应为什么要等 actor 的 oneshot

发送命令时创建：

```rust
let (tx, rx) = oneshot::channel();
SessionCommand::Prompt { respond_to: tx, ... }
```

ACP handler 随后等待 `rx`。

Actor 直到 Turn 收敛后才返回：

- stop reason；
- total tokens；
- prompt usage；
- completion kind；
- structured output；
- tool override state；
- cancellation metadata。

所以 ACP `session/prompt` 是一个长请求：

```text
request lifetime ≈ one logical turn lifetime
```

流式内容并不走这个最终 response，而是走独立的 session notifications。

---

## 28. `cancel` 为什么也经过相同的 dispatch lock

Cancel 流程：

```text
wait for load
    -> parse cancelTrigger/cancelSubagents/rewindIfPristine
    -> acquire per-session dispatch lock
    -> enqueue SessionCommand::Cancel
    -> return Ok
```

它不等待整个 Turn 停止。

CancelNotification 的语义是“取消信号已提交”，而不是“所有子进程均已回收并完成持久化”。

dispatch lock 的关键保证是：

```text
Prompt 请求先进入协议处理
Cancel 紧跟到达

=> Prompt command 先入 actor mailbox
=> Cancel command 后入 actor mailbox
```

这避免 cancel 因 handler 调度更快而取消不到目标 prompt。

---

## 29. `set_session_mode` 与 `set_session_model`

这两个方法也属于 session-scoped control plane。

`set_session_mode`：

- 等待 load；
- 发送 `SessionCommand::SessionMode`；
- 等 actor oneshot。

`set_session_model`：

- 先解析并验证 model；
- 进入 model switch handler；
- 成功后清除 session 的 unavailable-model latch。

核心设计是：

> 多客户端场景下，model、reasoning effort、permission mode 等有效状态尽量以 Session 为边界，而不是依赖 MvpAgent 上最后一个客户端写入的全局值。

---

## 30. `session/close` 比 `cancel` 多了什么

Close 要完成的不只是取消当前 Turn。

`close_active_session` 按总预算执行：

```text
wait attach settle        最多 5s
    -> capture target actor channel
    -> acquire intake lock 最多 2s
    -> re-check actor identity
    -> send Cancel
    -> send Shutdown(CancelRunningTurn)
    -> remove session terminal state
    -> drain old thread
    -> finalize remote replica
```

全部阶段共用 8 秒总 deadline。

每个阶段用 `stage_budget` 取得“单阶段 cap 与剩余总预算的较小者”。

这避免三个独立 timeout 简单相加，导致 close 的实际最坏耗时无限膨胀。

---

## 31. Close 为什么要捕获并复核 actor identity

Close 在等待 lock 时，Session 可能被另一次 load 替换。

因此先保存：

```text
target = current.cmd_tx
```

拿到 lock 后再次查询：

- 没有 actor → `NotResident`；
- channel 不是同一个 → `Superseded`；
- 仍是同一个 → 执行 hard stop。

这是一种轻量 generation check。

它不需要给 actor 显式编号，而是利用 channel identity 判断“我准备关闭的对象是否仍是当前对象”。

---

## 32. Close、Delete、Evict 不是同义词

### Close

- Cancel running turn；
- Shutdown actor；
- 清理 runtime；
- finalize replica；
- 对客户端返回 close outcome。

### Delete

- 先 hard stop；
- 等待 flush；
- 然后由 delete 路径删除持久化数据；
- 不应把 close 的 remote-finalize 语义机械复用。

### Evict / Disconnect detach

- 客户端离开；
- 有 live work 的 Session 保持 resident；
- 完全 idle 的 Session 才卸载到磁盘；
- 不 finalize，因为它仍可 reconnect/load。

理解这三个操作，是理解 resident supervisor 的前提。

---

## 33. “客户端断开”为什么不能直接杀 Session

Session 可能正在：

- 等模型输出；
- 运行 shell command；
- 等 permission；
- 等 ask-user response；
- 执行 background subagent；
- 持久化最后几个事件。

如果 IPC client 断开就 drop handle：

- `KillOnDrop` 资源可能被误杀；
- pending oneshot 消失；
- Turn 无法在重连后继续；
- transcript 可能缺尾部事件。

因此 `handle_evict_sessions` 采用：

```text
attaching?
    -> keep resident

actor changed during check?
    -> keep resident

session_has_live_work?
    -> mark Working, keep resident

otherwise
    -> graceful Shutdown
    -> drop handle
    -> clear resident resources
    -> mark Dormant
```

这是 no-evict 语义：断开是 detach，不是 session destruction。

---

## 34. 怎样判断 Session 仍有 live work

`session_has_live_work` 分三层检查。

### 第一层：running turn

查看共享的 `current_prompt_id`。

锁 poisoned 时保守认为 busy。

### 第二层：parked plan approval

检查 `pending_interactions` 中是否有 `PlanApproval`。

它需要保持 resident，因为计划审批还带有持久化 gate，卸载会错误清掉等待关系。

### 第三层：actor queue probe

调用 `handle.is_busy()`，询问 actor：

```text
running_task.is_some() || !pending_inputs.is_empty()
```

查询 timeout 或 actor 不可达时仍保守返回 busy。

---

## 35. Resident supervisor 解决什么问题

Actor 可以在没有显式 close 的情况下结束：

- panic；
- channel 链路意外关闭；
- task 提前返回；
- shutdown 完成但 registry 还保留 thread。

`ensure_session_supervisor` 只启动一次后台任务，周期调用：

```rust
sweep_dead_sessions()
```

它检查已 finished 的 thread。

规则是：

```text
finished + still resident
    -> unexpected death -> DeadFailed -> reap

finished + non-resident
    -> expected clean exit -> only clear thread
```

此外 sweep 被 `catch_unwind` 包围。

一个 sweep panic 不应让之后所有 Session 都失去监督。

---

## 36. 为什么 Gateway 是一个 channel 边界

`xai-acp-lib/src/gateway.rs` 提供：

```rust
AcpGatewaySender<S>
AcpGatewayReceiver<S, C>
```

创建时：

```rust
let (tx, rx) = mpsc::unbounded_channel();
```

Sender 可克隆并进入各个 runtime 组件。

Receiver 独占真实 ACP connection，并在自己的 loop 中把消息转发出去。

这解决三个问题：

1. connection 可能是 `!Send`；
2. 多个 producer 需要共享 outbound transport；
3. 请求需要 oneshot response correlation。

---

## 37. 为什么 Gateway receiver 默认 `spawn_local`

Gateway 的 receiver 收到 message 后，不直接在 receive loop 内 await handler。

它通过可配置的 spawn function 分派，默认：

```rust
tokio::task::spawn_local(fut)
```

原因是 ACP trait 使用 `#[async_trait(?Send)]`，底层 connection 与部分状态基于 `Rc`。

这与整个 MvpAgent/Session 控制面运行在 Tokio `LocalSet` 上的设计一致。

这里要区分：

- `spawn_local` 允许 future 持有 `!Send` 数据；
- 并不意味着所有业务都阻塞在一个 future 中；
- receive loop 仍可不断接收并分派多个请求。

---

## 38. Gateway message 怎样保存 response correlation

Sender enqueue 一个请求时创建 oneshot：

```text
AcpArgs {
    request,
    response_tx,
}
```

Receiver 调用真实 connection 方法，得到 response 后写回 `response_tx`。

因此 Gateway channel 里的 message 不是裸 JSON，而是：

```text
typed request + typed response continuation
```

这使 Rust 类型系统能确保：

- `RequestPermissionRequest` 对应 `RequestPermissionResponse`；
- `ReadTextFileRequest` 对应 `ReadTextFileResponse`；
- Notification 对应 `()`。

---

## 39. Gateway 的三种发送方式

### 39.1 `send`

发送并等待真实 response：

```rust
gateway.send(request).await
```

适用于反向请求，例如 permission、文件读取和终端创建。

### 39.2 `forward_with_completion`

同步 enqueue，并返回 completion receiver：

```rust
let completion = gateway.forward_with_completion(notification);
```

调用者可以先快速 enqueue 一批，再逐个 await completion。

Replay 使用它建立 response boundary。

### 39.3 `forward_fire_and_forget`

只返回 channel 是否接受：

```rust
let accepted = gateway.forward_fire_and_forget(notification);
```

适用于 live delta、roster update、状态提示等 best-effort 通知。

`accepted = true` 只代表进入 Gateway queue，不代表客户端已经渲染。

---

## 40. 为什么 replay 不能全用 fire-and-forget

`session/load` 返回前，客户端必须先看到历史事件。

需要保证：

```text
replay event 1
replay event 2
...
replay event N
load response
```

如果只是 enqueue 后立即返回 load response，Gateway receiver 的并发 dispatch 可能让 response 边界跑到 replay handler 完成之前。

所以 replay 收集每条通知的 completion receiver，然后 drain：

```text
enqueue replay batch synchronously
    -> await every completion
    -> return load response
```

Gateway 自带测试 `completion_drain_preserves_notification_ordering` 固定了这一点。

---

## 41. `NotificationSender` 的三件套

Session 的通知层只有三个核心字段：

```rust
NotificationSender {
    gateway,
    gateway_enabled,
    persistence_tx,
}
```

它把两个目的地放在一起：

```text
Session update
    ├─> Persistence Actor
    └─> ACP Gateway
```

但 gateway 分支受 `gateway_enabled` 控制。

因此 gate 关闭时：

- 事件仍持久化；
- 不向当前 Client 实时转发。

这对 reconnect replay 至关重要。

---

## 42. reconnect 时为什么暂时关闭 live gateway

已有 resident actor 时，load 会：

```rust
handle.gateway_enabled.store(false, ...)
```

期间 actor 仍可能产生新事件。

这些事件写入 persistence，但不直接进入客户端实时流。

随后：

1. flush persistence；
2. replay 历史直到某个 offset；
3. 再 flush；
4. 从 replay end offset 读取 delta；
5. 打开 gateway；
6. await delta completion；
7. 返回 load response。

这是一种 two-phase cutover。

---

## 43. 两阶段切换解决的竞态

假设 load 正在 replay：

```text
历史: E1 E2 E3
actor 同时产生: E4 E5
```

如果 live gate 一直打开，客户端可能看到：

```text
E1 E4 E2 E5 E3
```

如果 replay 结束后才重新读取 persistence，但不做 flush，又可能漏掉 actor 缓冲中的 E5。

当前协议是：

```text
gate off
replay stable prefix
flush actor/persistence
read post-prefix delta
gate on
drain delta completions
response
```

Gateway 的 `two_phase_cutover_no_missing_updates` 测试针对的就是 replay 与 live producer 交错。

---

## 44. Replay 事件为什么带 `_meta.isReplay`

历史事件与当前实时事件可能有不同的客户端处理策略。

例如客户端可能：

- 不为 replay 重复播放动画；
- 不重复触发提示音；
- 用更快路径构建 scrollback；
- 避免把历史状态误判为新到达状态。

因此 historical replay 会注入：

```json
{
  "_meta": {
    "isReplay": true
  }
}
```

cursor 后的 delta 虽然也由 replay reader 发出，但语义上是 live delta，因此不一定标记 `isReplay`。

---

## 45. Replay 为什么压平 ToolCall streaming update

持久化记录中，一个 Tool Call 可能包括：

```text
ToolCall registration
ToolCallUpdate metadata
ToolCallUpdate in_progress
ToolCallUpdate completed
```

实时渲染需要这些增量。

历史 replay 若原样推送，会让客户端为同一个 Tool 重复进行多次昂贵的 UI 更新。

因此 `forward_raw_replay_line` 会：

- 缓存未完成 ToolCall；
- 合并 metadata；
- 丢弃中间 streaming state；
- 遇到 Completed/Failed 后发送一个预完成 ToolCall。

这是“持久化事实不变，回放投影可优化”的典型例子。

---

## 46. 单客户端模式下的路由很简单

没有 leader 时：

```text
MvpAgent Gateway
    -> 当前 ACP Client connection
```

所有 Session 共享一个 Gateway sender，但每条 session update 都带 SessionId，客户端可自行映射到对应 UI。

这并不意味着 SessionActor 共享状态。

SessionActor、ChatState、ToolContext、model、permission mode 等仍按 Session 隔离。

---

## 47. Leader 模式为什么需要更复杂的路由

Leader 后面可以连接多个客户端：

```text
Desktop window A
Desktop window B
CLI C
```

这些客户端可能：

- 查看同一个 Session；
- 只查看不同 Session；
- 在 load 中途加入；
- 断开后重连；
- 都能回答一个 permission modal；
- 但不能都驱动同一个 scheduled prompt。

所以 leader 不能使用“最后活跃客户端接收全部消息”的简单策略。

---

## 48. `session_subscribers`：谁应该看见会话事件

Leader 维护：

```rust
HashMap<String, HashSet<ClientId>>
```

当客户端从 `session/new/load/resume` response 中获得某个 SessionId，它就加入该 Session 的 subscriber set。

普通带 SessionId 的 notification 会广播给所有 subscriber。

用途包括：

- Text/Reasoning delta；
- ToolCall update；
- prompt complete；
- roster-related session update；
- pending/resolved interaction 状态。

Subscriber 是“观察资格”，不是“写入所有权”。

---

## 49. `session_driver`：谁负责产生副作用

Leader 还维护：

```rust
HashMap<String, ClientId>
```

Driver 用于只应由一个客户端处理的消息。

例如 scheduled task 的 inject prompt 如果广播：

```text
Client A enqueue + drive
Client B enqueue + drive
Client C enqueue + drive
```

同一个定时任务会被执行三次。

因此这类消息只路由到 driver。

其他客户端仍通过 SessionActor 之后广播出的 `session/update` 看见执行结果。

---

## 50. “看见”与“驱动”必须分离

这是多客户端 Agent 系统的一条通用原则：

```text
rendering fan-out
    可以一对多

side-effect ownership
    通常必须一对一
```

如果混在一起，常见故障是：

- 重复 prompt；
- 重复 tool execution；
- 多个 terminal owner；
- 多份 permission response；
- 互相覆盖的 queue mutation。

Grok Build 用 subscriber/driver 两张表显式表达这一区别。

---

## 51. 请求 ID 为什么要 namespace

不同 Client 的 JSON-RPC request id 可能相同：

```text
Client A request id = 7
Client B request id = 7
```

Leader 转发给同一个 Agent connection 前，会把 id namespace 化，逻辑格式类似：

```text
client_id | original_id_json
```

Agent response 回来后，Leader 解析 namespace：

- 找到原 ClientId；
- 恢复原 request id；
- 单播 response 给正确客户端。

这条路径针对 request/response correlation；它与 Session notification 的 subscriber 路由是两个不同机制。

---

## 52. load replay 为什么不能广播给已有 subscriber

Client B 正在 load 一个 Client A 已经打开的 Session。

如果 replay 按普通 notification 广播：

```text
Client A 再收到整段历史
Client B 收到需要的历史
```

A 的 UI 会重复插入旧事件。

因此 Leader 在 load request 中注入：

```json
{
  "_meta": {
    "x.ai/leaderClientId": 42
  }
}
```

Agent replay 时把该值回写到每条 replay notification。

Leader 检测后只发给 Client 42。

---

## 53. `x.ai/leaderClientId` 是路由标签，不是权限身份

这个字段只表示：

> 当前 replay burst 的目标客户端是谁。

它不是：

- 用户身份；
- authorization principal；
- Session owner 的永久记录；
- 防伪安全边界。

它由 leader 注入，并在同一受控进程链路中用于 unicast routing。

真正的用户/团队/auth identity 来自独立的认证系统。

---

## 54. load 中途的 live event 怎样处理

即使 Agent 通过 gateway gate 尽量维持 replay 顺序，leader 仍要处理多客户端局部视角。

对正在 load 的特定 `(ClientId, SessionId)`，leader 建立：

```text
load_live_buffer
```

普通 live notification 暂存，直到 load response 路由完成后再 flush。

同时它记录 replay 中最大的 event sequence。

Flush buffer 时，若某条 live event sequence 已包含在 replay cutoff 中，就去重。

因此目标客户端得到：

```text
targeted replay
    -> load response
    -> buffered live events not covered by replay
```

---

## 55. eventId 在路由层的额外价值

持久化章节中，eventId 用于稳定事件身份。

在 leader load cutover 中，它还用于：

```text
replay/live overlap detection
```

Leader 从形如：

```text
{sessionId}-{counter}
```

的 eventId 解析 monotonic counter。

于是同一事件如果既出现在 replay 尾部又进入 live buffer，可按 sequence 丢弃重复项。

这说明一个好的事件 identity 往往同时服务：

- persistence；
- idempotency；
- reconnect；
- multi-client routing。

---

## 56. 普通反向请求为什么只交给 driver

Agent 发给 Client 的反向请求常常需要由客户端执行副作用，例如：

- client filesystem 操作；
- terminal 操作；
- 某些 extension method。

如果每个 subscriber 都执行，结果会重复。

因此 Leader 检测带 `id` 和 `method` 的 reverse request，默认只发给 session driver。

随后 driver 的 response 通过 request id namespace 返回 Agent。

---

## 57. Permission、Question、Plan Approval 为什么例外广播

三类 interaction reverse request：

- `session/request_permission`；
- `x.ai/ask_user_question`；
- `x.ai/exit_plan_mode`。

它们的目标不是让 Client 执行系统副作用，而是让某个可见客户端提供人类决策。

如果只发给 driver，而 driver 对应窗口已经不可见或失联，Agent 会永久等待。

因此 leader 将它们广播给所有 subscriber：

```text
任何正在看该 Session 的客户端都可回答
```

响应采用 first-answer-wins。

---

## 58. `PendingInteractionGuard`：把“正在等人”变成共享事实

Session runtime 用：

```rust
PendingInteractions = Arc<Mutex<HashMap<String, PendingKind>>>
```

key 是稳定的 `tool_call_id`。

`PendingInteractionGuard::new`：

1. 插入 map；
2. 广播 `pending_interaction`。

Guard drop：

1. 从 map 删除；
2. 只有确实删除成功时广播 `interaction_resolved`。

无论 await 是：

- 正常响应；
- error；
- cancel；
- future drop；

最终都会走 Drop 清理。

---

## 59. first-answer-wins 怎样成立

多个客户端都收到 modal。

第一个回答完成后，对应 parked future 收敛，guard 删除 `tool_call_id` 并广播 resolved。

其他客户端收到 resolved 后关闭 modal。

如果第二条路径再次 drop 或尝试清理：

```rust
map.remove(&tool_call_id).is_some()
```

返回 false，不再广播第二次 resolved。

这提供了幂等清理语义。

真正 response correlation 仍由底层 JSON-RPC request id 完成；`tool_call_id` 用于跨客户端 UI 与 pending registry 的稳定业务身份。

---

## 60. Leader 为什么缓存 interaction request

Client B 在 permission modal 已经发出后才 load Session。

历史 transcript 不会包含该 pending request，因为 reverse requests 刻意不持久化。

如果 leader 不缓存，B 只知道 Session 是 NeedsInput，却看不到要回答什么。

因此 leader：

- 按 SessionId 和 tool_call_id 缓存未决 interaction request；
- 新客户端 load response 完成后 replay 这些 request；
- 收到 `interaction_resolved` 后删除缓存。

这是一个进程内的 transient replay cache，不是磁盘历史。

---

## 61. 为什么 pending interaction 不写入 transcript

Permission/question 是一个尚未完成的协议 request。

持久化它会产生困难：

- 进程重启后原 JSON-RPC response channel 已不存在；
- 旧 request id 无法继续使用；
- 重放可能制造重复审批；
- “用户曾经看到请求”不等于“这个请求现在仍可回答”。

因此持久化的是与业务恢复有关的 gate 或最终结果，而不是 transport-level pending request 本身。

Leader 的缓存只解决同一进程生命周期内新 subscriber attach 的可见性。

---

## 62. 为什么 Plan Approval 比普通 Permission 更影响 residency

源码只把 parked `PlanApproval` 计入 `session_has_live_work` 的同步 pending 检查。

原因是 resume re-park 的 plan approval 还对应持久化的 `awaiting_plan_approval` gate。

如果 idle unload 把 parked future drop：

- guard 会清理 pending；
- 磁盘 gate 与内存等待可能失配。

普通 permission/question 没有同样的持久化 gate，因此不在该特殊检查中；它们仍可能被 actor 的 `is_busy` 覆盖。

---

## 63. machine-wide notification 为什么没有 SessionId

有些通知属于整个进程/产品状态：

- `x.ai/sessions/changed`；
- `x.ai/models/update`；
- `x.ai/mcp/servers_updated`；
- `x.ai/announcements/update`。

它们没有 SessionId，不能按 subscriber map 路由。

Leader 显式识别并广播给所有 client。

否则使用“last active client” fallback 会导致：

- 其他窗口 model picker 不更新；
- MCP modal 看不到新 connector；
- dashboard roster 过期；
- banner 状态不一致。

---

## 64. ext notification 在 wire 上为什么可能多包一层

Leader 源码同时处理两种形态。

直接 method：

```json
{"method":"session/update","params":{...}}
```

Gateway 扩展包装：

```json
{
  "method":"_x.ai/foo",
  "params":{
    "method":"x.ai/foo",
    "params":{...}
  }
}
```

因此 leader 提供 `method_of` 与 `interaction_inner_params` 统一解包。

若路由逻辑只比较 top-level method，就会漏掉生产环境中的 wrapped ext notification。

这是协议适配层常见的陷阱：

> 业务 method name 与 transport envelope method name 不一定是同一个字段。

---

## 65. 子 Session 怎样继承路由

Subagent 会生成 child Session。

当 leader 从 `SubagentSpawned` 事件识别 child SessionId 时，会：

- 复制 parent subscribers；
- 继承 parent driver；
- 记录 parent → children 关系。

这样打开父会话的客户端能直接看见子 Agent 的事件。

Child finish 时，leader 递归清理 route。

还需要 backfill：如果 parent route 晚于 child spawn 才建立，已有 descendants 也必须补上 subscriber/driver。

---

## 66. Gateway、Gateway Bridge、Tool Bridge 不要混为一谈

源码中至少有三种“bridge/gateway”语境。

### ACP Gateway

本文重点。

它把 runtime 的 typed outbound ACP message 经 channel 转发到真实 Client connection。

### Gateway Bridge / Local Workspace Supervisor

它连接额外 workspace server 或计算环境，使 Session 获得 remote/local-workspace 资源与 computer sessions。

它是 workspace/backend 接线，不是多客户端 ACP fan-out 本身。

### Tool Bridge / Notification Bridge

它在 Tool Runtime 内部路由 terminal、background task、scheduler 与工具通知。

它连接 SessionActor 与工具子系统，不等于 ACP transport。

读代码时见到 `bridge`，先问：

```text
它桥接的是 Client connection、Workspace backend，还是 Tool runtime？
```

---

## 67. `BridgeAttach` 表达什么

`BridgeAttach` 有三种结果：

```rust
NotAttached
AlreadyAttached
Spawned
```

它主要表达 Session 与 workspace gateway bridge 的接线结果：

- 没有必要或没有可用 bridge；
- 已存在，当前请求的初始选项未重新生效；
- 本次调用创建，新选项生效。

它与 `SessionPresence::Attaching` 完全不同：

- `BridgeAttach` 是某个外围 bridge 的 attach outcome；
- `SessionPresence::Attaching` 是整个 Session actor 的 load/resume 生命周期状态。

同名词根不代表同一状态机。

---

## 68. Local mode 与 bridge mode 怎样保持 Session 控制权

无论 filesystem/terminal 背后是：

- 本地实现；
- client capability；
- workspace server；
- gateway bridge；

协议层仍通过同一个 `SessionHandle.cmd_tx` 控制 SessionActor。

差异被封装到：

- `WorkspaceOps`；
- `AsyncFileSystem`；
- terminal backend；
- ToolContext；
- gateway bridge handle。

因此核心关系仍是：

```text
ACP request -> one SessionActor
SessionActor -> selected backend implementation
```

而不是每种 backend 各自复制一套 Agent loop。

---

## 69. 为什么每个 Session 要保存 origin client

`SessionHandle.origin_client` 保存创建/attach 时解析的产品来源。

用途包括：

- per-session User-Agent；
- telemetry attribution；
- leader 下只更新匹配来源的 yolo/auto 行为；
- subagent 继承父 Session 的客户端语境。

如果只读取 `MvpAgent.initialize_request`，leader 中后 initialize 的 Client 会覆盖前一个 Client 的语境。

所以连接级输入要在 Session 边界固化。

---

## 70. 资源清理为什么由 Registry、Handle、Thread 三方配合

### Registry

保存“这个资源仍应被追踪”的事实。

### Handle

持有 command sender、process scope 与各子系统引用。

drop/take handle 会释放或触发部分运行时资源。

### SessionThread

即使 handle 已移除，thread 仍可能在 flush。

必须留在 Registry，直到 supervisor 或 drain 确认结束。

如果 remove session 时同时无条件丢掉 thread handle，运行中的 task 会 detach，之后无法：

- 判断它是否结束；
- 等待 flush；
- 将异常退出标记为 DeadFailed；
- 防止同 SessionId 的新 actor 与旧 actor 重叠写盘。

---

## 71. `release` 为什么可能留下一个 Evicted entry

`SessionRegistry::release` 先从 map 移除整个 `SessionResources`。

若发现 thread 尚未 finished，则重新插入一个极简 entry：

```text
presence = Evicted { thread: running }
retained = None
resident = None
unavailable_model = None
```

它不再报告 live state，也不保留业务资源，只保留最后一个可回收句柄。

等 thread 完成，sweep 再删掉。

这是一种“逻辑删除已经完成，物理回收仍待收尾”的状态。

---

## 72. `take_resident` 与 `release` 为什么分开

`take_resident` 只拿走 Handle。

它刻意不立即改写 live state 或 thread。

因为调用者可能要执行不同的下一步：

- idle unload → `Dormant`；
- close → `Completed`；
- crash reap → `DeadFailed`；
- respawn → 新 Resident。

如果 `take_resident` 自己决定状态，它就会把多个生命周期动作耦合在一起。

`release` 则是更彻底的 per-session 资源释放。

---

## 73. Registry 为什么使用 `Rc<RefCell<...>>`

MvpAgent 控制面运行在单线程 LocalSet。

因此 Registry 使用：

```rust
Rc<RefCell<HashMap<...>>>
```

而不是跨线程 `Arc<Mutex<...>>`。

优点：

- 无跨线程锁开销；
- borrow scope 明确；
- 与 `?Send` ACP trait 一致。

代价：

- 不能把 Registry 引用送到任意 worker thread；
- 不能跨 `await` 持有 RefCell borrow；
- 长函数必须先 clone `SessionHandle` 再 await。

源码在 `resident_handle` 上直接写明：调用者不要跨 await 持有 registry borrow。

---

## 74. 为什么 `SessionHandle` 可以 Clone + Send

Registry 本身属于 local control plane，但 Handle 中的多数成员是：

- Tokio channel sender；
- `Arc<Mutex/...>`；
- thread-safe subsystem handle；
- immutable/copyable configuration。

因此外部任务可 clone Handle，并通过 channel 与 actor 交互，而不需要拿着 Registry borrow。

这形成清晰边界：

```text
local registry mutation
    只在 MvpAgent LocalSet

session control
    通过 cloneable SessionHandle
```

---

## 75. 错误处理的三种基本语义

### 75.1 Unknown Session

只有在：

- 没有 resident handle；
- 没有 in-flight attach；
- 或 attach 已失败/超时；

才返回 unknown/not found。

### 75.2 Transport receiver gone

Gateway enqueue 失败表示 receiver 已 drop。

fire-and-forget 通常记录并丢弃；需要交付结果的 reverse request 会得到 error。

### 75.3 Actor channel gone

发送 `SessionCommand` 失败表示 actor 不再接收。

请求路径通常返回 internal error；lifecycle/supervisor 随后负责 reap。

这三种错误不能合并成一个“session failed”：

- Session identity 不存在；
- Client transport 不存在；
- Session actor 不存在；

对应的是三层不同故障。

---

## 76. 一次正常 Prompt 的端到端时序

```text
Client
  | session/prompt(sessionId)
  v
Leader
  | namespace request id
  | identify subscriber/driver context
  v
MvpAgent::prompt
  | wait for attach if needed
  | acquire per-session dispatch lock
  | build prompt metadata
  | enqueue SessionCommand::Prompt
  | release dispatch lock
  v
SessionActor
  | run turn / tools / model
  | persist updates
  | emit live notifications
  v
ACP Gateway
  v
Leader
  | broadcast session notifications to subscribers
  v
Clients render

SessionActor
  | complete respond_to oneshot
  v
MvpAgent::prompt
  | build PromptResponse
  v
Leader
  | restore request id, unicast response
  v
Requesting Client
```

注意两条回程路径：

- 流式 update → subscriber broadcast；
- 最终 response → 原 request client 单播。

---

## 77. 一次 reconnect load 的端到端时序

```text
Loading Client
  | session/load
  v
Leader
  | inject leaderClientId
  | create per-client live buffer
  v
MvpAgent::attach_session
  | begin SessionLoadGuard / Attaching
  | gate resident live forwarding off
  | flush persistence
  | load summary
  | replay stable prefix, target = leaderClientId
  | spawn actor or refresh resident actor
  | flush + replay delta
  | gate live forwarding on
  | drain replay completions
  | guard drops -> settle_attach
  v
Leader
  | targeted replay only to loading client
  | route load response
  | flush buffered non-overlap live events
  | replay cached pending interactions
  v
Loading Client is caught up
```

---

## 78. 一次 shared permission 的端到端时序

```text
SessionActor / Tool Runtime
  | create PendingInteractionGuard(tool_call_id)
  | send request_permission reverse request
  v
Gateway
  v
Leader
  | classify as interaction request
  | cache by sessionId + tool_call_id
  | broadcast to every subscriber
  v
Client A modal    Client B modal
  | allow first
  v
Leader -> Gateway -> parked future resolves
  | guard drops
  | remove pending registry entry
  | broadcast interaction_resolved
  | leader evicts cached request
  v
All clients close modal
```

---

## 79. 最容易出现的误读

### 误读一：Registry 是对象仓库

更准确：它是 session hosting state machine。

### 误读二：Handle 就是 actor state

更准确：Handle 是 command proxy + 快照 + 共享信号。

### 误读三：load response 返回就代表 replay 已 enqueue

更准确：代码会等 replay handler completion，建立可观察 response boundary。

### 误读四：所有 Session notification 都广播

更准确：targeted replay 单播；普通 session update 广播；driver-only reverse request 单播；machine-wide update 全局广播。

### 误读五：Client 断开等于 Session close

更准确：断开先 detach，有 live work 就继续 resident，idle 才 unload。

### 误读六：所有 bridge 都是 ACP transport

更准确：ACP Gateway、workspace gateway bridge、tool bridge 是不同边界。

---

## 80. 可复用的设计模式

### 80.1 State carries evidence

用 enum variant 携带 handle/thread/waiter，而不是维护容易失配的平行 map。

### 80.2 RAII settles async lifecycle

用 guard Drop 覆盖 success、error 与 cancellation。

### 80.3 Identity recheck after await

在等待 lock/timeout 后重新比较 channel identity，防止操作 superseded actor。

### 80.4 Persist first, project later

通知在 gateway gate 关闭时仍持久化，之后从日志恢复客户端视图。

### 80.5 Separate observer fan-out from side-effect ownership

Subscriber 与 driver 分离。

### 80.6 Conservative liveness

无法确认 idle 时视为 busy，保护运行中的工作。

### 80.7 Bounded shutdown

每阶段等待共享总 deadline，避免 cleanup 永久卡住。

---

## 81. 建议的断点与日志观察顺序

如果要实际调试一次 Session，建议按以下符号下断点或增加 tracing。

### 新建

1. `MvpAgent::new_session_inner`；
2. `MvpAgent::spawn_and_register_session`；
3. `SessionRegistry::set_thread`；
4. `SessionRegistry::put_resident`。

### Prompt

1. `MvpAgent::prompt`；
2. `MvpAgent::dispatch_lock`；
3. `SessionCommand::Prompt` 的 actor match arm；
4. NotificationSender 的更新发送路径；
5. Gateway receiver `run`。

### Reconnect

1. `MvpAgent::begin_session_load`；
2. `SessionRegistry::begin_attach`；
3. `MvpAgent::replay_transcript_gate`；
4. `MvpAgent::forward_raw_replay_line`；
5. `SessionRegistry::settle_attach`；
6. leader `extract_target_client_id` 与 load buffer flush。

### Close/Crash

1. `close_active_session`；
2. `hard_stop_resident`；
3. `remove_session`；
4. `sweep_dead_sessions`；
5. `SessionRegistry::release`。

---

## 82. 推荐先写的验证测试

若要修改这一层，优先固定这些不变量。

### Attach

- attach 失败恢复 displaced Working；
- nested attach 的旧 guard drop 不影响新 attach；
- handle 已注册但 guard 未 drop 时，close 仍等待 settle；
- closed actor channel 不恢复成 Resident；
- racing prompt 等待 load 后命中新 handle。

### Dispatch

- prompt A、prompt B 以接收顺序 enqueue；
- cancel 不越过前一 prompt；
- Session A 的 lock 不阻塞 Session B。

### Gateway

- replay completion 全部早于 load response；
- gate cutover 不漏 live delta；
- receiver drop 时 fire-and-forget 返回 false；
- reverse request response type 正确关联。

### Leader

- replay 只到 loading client；
- live update 到所有 subscribers；
- driver-only request 只执行一次；
- interaction 广播且 first-answer-wins；
- load buffer 按 event sequence 去重；
- child route 继承并在 finish 后清理。

---

## 83. 修改代码时的风险清单

### 修改 SessionPresence

检查：

- `live_state` 投影；
- `take_thread` 对 displaced 的递归；
- `hosted_handle`；
- `settle_attach`；
- `is_resource_empty`；
- telemetry counts。

### 修改 load/replay

检查：

- gateway gate 开关是否在所有 error path 恢复；
- replay target 是否保留；
- delta completion 是否 drain；
- leader live buffer 是否仍能去重；
- pending interaction 是否 attach 后重放。

### 修改 leader routing

检查：

- direct 与 wrapped ext method 两种 envelope；
- request response 与 notification 分流；
- SessionId 缺失的 machine-wide 消息；
- subagent child route；
- disconnected target；
- bounded buffer overflow 行为。

### 修改 Handle 字段

检查它属于：

- spawn snapshot；
- reconnect refresh；
- actor authoritative state；
- subagent inherited context；
- unload 时应释放的 resident resource。

---

## 84. 读完后应该建立的最终模型

可以把整个系统压缩成七句话：

1. ACP trait 是协议边界，不是 Session 真正的状态容器。
2. `SessionRegistry` 用 `SessionPresence` 表达宿主生命周期，并保存 handle/thread/waiter 这些状态证据。
3. `SessionHandle` 把并发协议 handler 的动作转换成唯一 SessionActor 的 typed mailbox command。
4. per-session `dispatch_lock` 保证 prompt/cancel 的提交顺序，但不锁住整轮执行。
5. Gateway 把 `!Send` connection 与可克隆 runtime sender 隔开，并用 oneshot 建立请求完成边界。
6. Persistence + gateway gate + replay completion 让 reconnect 客户端先恢复历史，再无缝接上 live stream。
7. Leader 用 subscriber、driver、target client 与 pending interaction cache，把一个 SessionActor 安全投影给多个客户端。

如果这七点清楚，之后再读 SessionActor、Tool Runtime、Persistence 或 Pager 多客户端逻辑，就不会把 transport、ownership、residency 和 business state 混在一起。

---

## 85. Glossary：本文名词白话解释

| 名词 | 白话解释 | 在本文源码中的具体含义 |
| --- | --- | --- |
| ACP | Agent 与客户端沟通的一套协议 | 定义 initialize、session/new、prompt、cancel、session/update 和反向请求等 typed message |
| JSON-RPC | 用 JSON 表达方法调用、请求 id、结果和错误的协议风格 | ACP 在线路上的 request/response correlation 基础 |
| Agent side | 协议中实现 Agent 方法的一方 | `MvpAgent` 接收 Client 发来的 prompt/load/cancel |
| Client side | 协议中实现客户端能力的一方 | 接收 session update，并替 Agent 读文件、开终端或请求人类授权 |
| reverse request | Agent 反过来向 Client 发出的请求 | permission、client filesystem、terminal、ask-user 等 |
| notification | 不要求业务返回值的单向消息 | session update、roster changed、prompt complete 等 |
| extension method | 标准 ACP 之外的厂商扩展 RPC | `x.ai/skills/*`、`x.ai/task/*`、`x.ai/session/*` 等 |
| `_meta` | 协议对象上的扩展元数据字典 | 传 cursor、client identity、leader target、trace context、permission mode 等 |
| MvpAgent | Shell 中 ACP Agent trait 的主要实现 | 协议入口、Session Registry 所有者和 extension dispatcher |
| Session | 一段有稳定 id、持久化历史和运行态的 Agent 对话 | 可在 actor 驻留和只存在磁盘两种形态间切换 |
| SessionActor | 单个 Session 的权威运行时 actor | 串行消费 `SessionCommand`，驱动 Turn、Tool、状态与持久化 |
| actor | 拥有私有状态并通过消息串行处理操作的并发单元 | SessionActor 与 ChatStateActor 都采用这一模式 |
| mailbox | Actor 接收命令的消息队列 | `cmd_tx` 对应的 Tokio mpsc channel |
| SessionCommand | 协议层发给 SessionActor 的 typed command | Prompt、Cancel、Shutdown、SetModel、IsBusy 等 |
| SessionHandle | 可克隆的 SessionActor 代理 | 含 `cmd_tx`、共享信号、子系统 handle 和会话能力快照 |
| SessionThread | 跟踪 actor 执行任务是否完成的句柄 | 即使 Handle 被移除也可保留，用于 drain 和 sweep |
| Registry | 按 SessionId 管理进程内 Session 资源的注册表 | `SessionRegistry`，同时也是 hosting state machine |
| presence | Session 在当前进程中的托管形态 | Resident、Attaching、Evicted、Dormant、Closed、Dead |
| resident | Actor 当前驻留内存并可接收 command | Registry 中存在 hosted SessionHandle |
| residency | Session 是否驻留运行时的属性 | 与 transcript 是否存在、业务是否完成不是同一回事 |
| dormant | 只在磁盘上、没有 resident actor，但可恢复 | idle unload 后的典型状态 |
| attaching | load/resume 正在构建或重新接线 Session | 带 waiter、displaced presence、可能已落入的 handle/thread |
| displaced | attach 开始前被暂存的旧 presence | attach 失败时可恢复，避免丢掉旧 Working actor |
| settle | 把临时 Attaching 状态结算为 Resident、恢复旧状态或清理 | `SessionRegistry::settle_attach` |
| RAII guard | 对象创建时登记资源、Drop 时保证清理的模式 | `SessionLoadGuard`、`PendingInteractionGuard` |
| watch channel | 多个接收者可观察最新状态或 channel 关闭的 Tokio 通道 | attach waiter 用 sender drop 唤醒所有竞争请求 |
| `same_channel` | 判断两个 sender/receiver 是否属于同一条 channel | 用于确认 attach owner 或 actor identity 未被替换 |
| retained resource | Actor reload 后仍应保留的 Session 资源 | dispatch lock、turn number、permission event receiver 等 |
| resident resource | 只在 actor 驻留期间需要的资源 | idle unload 时可以释放的 index/gateway 要求等 |
| dispatch lock | 每个 Session 的异步提交锁 | 保证 Prompt 与 Cancel 进入 actor mailbox 的顺序 |
| critical section | 一次只能有一个执行者进入的代码段 | 本文只覆盖 command enqueue 前的短提交区 |
| oneshot | 只传一个值的一次性异步通道 | SessionCommand response、Gateway request response correlation |
| LocalSet | Tokio 运行 `!Send` future 的单线程异步集合 | MvpAgent 与 ACP connection 的主要控制面环境 |
| `!Send` | 不能安全移动到另一线程的 Rust 类型/future | 常由 `Rc`、`RefCell` 或 `async_trait(?Send)` 带来 |
| `Rc` | 单线程引用计数指针 | Registry 与 local control plane 共享所有权 |
| `RefCell` | 在运行时检查借用规则的单线程内部可变容器 | 包裹 Session map，使 `&self` 方法可修改 registry |
| `Arc` | 可跨线程的原子引用计数指针 | Handle 中共享 current prompt、pending interactions、gate 等 |
| Gateway | 通过 channel 代理真实 ACP connection 的组件 | `AcpGatewaySender/Receiver` |
| enqueue | 把消息放入队列，但不代表已处理 | fire-and-forget 成功只保证 Gateway channel 接受 |
| completion receiver | 可等待某条 gateway 消息处理完成的 oneshot receiver | replay 用它保证 load response boundary |
| fire-and-forget | 发出后不等待业务 response | 适用于 live notification 与 best-effort 状态广播 |
| response boundary | 客户端看到最终 response 前必须已完成的一组前置发送 | load 前 replay completion drain |
| gateway gate | 控制 live notification 是否向 Client 转发的原子开关 | reconnect 时关闭，但 persistence 继续写入 |
| cutover | 从一种数据交付来源切换到另一种来源 | 从历史 replay 切换到 live stream |
| replay | 从持久化更新日志重新发送历史 Session 事件 | load 时恢复客户端 scrollback 与状态 |
| delta replay | stable replay 起点之后新写入事件的补发 | gate 切换前 flush 后从 offset 再读取 |
| cursor | 客户端声明自己已消费到的位置 | 允许只补发 cursor 后事件，而非完整历史 |
| eventId | Session 事件的稳定唯一/递增标识 | 用于 persistence、去重和 replay/live overlap 判定 |
| leader | 多客户端与单个 Agent connection 之间的路由进程/服务 | namespace request id，维护 subscriber 和 driver |
| ClientId | leader 为每个连接分配的进程内身份 | 用于响应单播、replay target 与路由表 |
| subscriber | 应接收某 Session 可渲染事件的客户端 | 同一 Session 可有多个 subscriber |
| driver | 负责某 Session 单一副作用型反向动作的客户端 | 每个 Session 通常只有一个 driver |
| broadcast | 同一消息发送给多个目标 | 普通 session update 发给全部 subscribers |
| unicast | 消息只发送给一个目标 | request response、targeted replay、driver-only request |
| machine-wide | 属于整个进程/应用而非某个 Session | models、MCP catalog、announcements、roster 更新 |
| request id namespace | 给不同 Client 的相同 JSON-RPC id 加上 ClientId 前缀 | Agent response 返回后可恢复并路由给原客户端 |
| `x.ai/leaderClientId` | leader 注入的 replay 目标标签 | Agent 回写到 replay notification，leader 据此单播 |
| live buffer | 某客户端 load 中途暂存的实时事件列表 | response 后 flush，并用 event sequence 去重 |
| reverse-request cache | leader 临时保存的未决人机交互请求 | 新 attach 客户端可恢复 permission/question modal |
| pending interaction | Agent 正在等待人类回答的阻塞式请求 | Permission、Question、PlanApproval |
| tool_call_id | 一次 Tool Call 的稳定业务 id | pending interaction registry 与 first-answer-wins 的 key |
| first-answer-wins | 多个客户端都可回答，但只接受第一个有效答案 | resolved 后其他 modal 被广播关闭 |
| idle unload | Session 无 live work 时停止 actor、释放内存但保留磁盘 | Client disconnect 后的内存控制策略 |
| detach | Client 与 Session 的路由所有权分离 | 不等于 close，也不一定停止 actor |
| evict | leader 通知 Agent 某些 Session 已失去 IPC owner | 当前实现按 busy 状态选择 keep resident 或 idle unload |
| drain | 等待已提交的消息/thread 尽量处理完成 | replay completion drain、old actor thread drain |
| sweep | 周期扫描并回收已经 finished 的 actor thread | `sweep_dead_sessions` |
| supervisor | 长期后台监督任务 | 定期 sweep，且一次 panic 不终止后续监督 |
| zombie | 外表仍登记为活跃，但实际 actor 已死的错误状态 | closed channel 的 handle 不能在 settle 时恢复为 Resident |
| superseded | 等待期间目标 actor 已被新 actor 替换 | close 返回 `Superseded`，不能误杀新实例 |
| bounded wait | 有明确 deadline 的等待 | load 60s、close 各阶段与总预算、thread drain 5s |
| conservative liveness | 无法确认 idle 时按 busy 处理 | 防止错误卸载仍在工作的 Session |
| Gateway Bridge | 连接 workspace server/计算环境的外围桥 | 不等于 ACP Gateway |
| Tool Bridge | Tool runtime 的 terminal/task/notification 接线层 | 不等于 leader 多客户端路由 |
| cross-client contamination | 一个客户端的配置误影响另一个客户端已有 Session | 用 per-session model/capability/origin snapshot 避免 |
| origin client | 创建或连接 Session 的产品来源信息 | 用于 User-Agent、telemetry、permission/yolo 范围与继承 |
| hosting state | 当前进程怎样托管 Session | 与会话内容、Goal 完成或模型 stop reason 不同 |

---

## 86. 下一篇适合继续精读什么

沿着本文继续，最值得写的是：

> **源码精读 23：Agent Failure & Recovery Runtime——从 Transport Error、Model Retry、401、Actor Death、Persistence Failure 到 Session Repair，错误怎样分类、传播、降级并恢复。**

它会把前面分散出现的错误路径连起来：

- 哪些错误只结束一次 sampling attempt；
- 哪些错误取消整个 Turn；
- 哪些错误让 Session 进入 DeadFailed；
- 哪些错误可通过 load/replay 恢复；
- 哪些错误要阻塞 prompt 等用户处理；
- fail-open、fail-closed 与 best-effort 分别在什么边界使用。
