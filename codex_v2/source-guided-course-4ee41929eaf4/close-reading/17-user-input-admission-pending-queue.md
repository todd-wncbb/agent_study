# User input admission 与 pending queue：一条用户消息究竟何时算“收到了”

> 源码基线：`4ee41929eaf4`。本篇的行号只用于固定提交定位；源码更新后，请优先搜索符号名。

这一篇解决一个看似简单、实际上很容易说错的问题：

> 客户端把一条用户消息交给 Codex 后，什么时候可以认为它已经成功？

“成功”至少可能指四件不同的事：

1. 消息已经放进 Core 的 submission channel；
2. Core 已决定它要启动新 Turn，还是加入正在运行的 Turn；
3. 消息已经进入当前 Turn 的 pending input queue；
4. 消息已经通过 Hook，并写入 rollout。

这四件事不是同一时刻发生，也不是同一种承诺。如果把它们都简称成“提交成功”，并发、取消和磁盘失败一来，调用者就会误判状态。

---

## 1. 先说人话：餐厅取号、入座与记账

可以把 Codex 想成一家只有一张工作桌的餐厅。

- 把订单交给门口工作人员：相当于 submission channel 接收成功；
- 工作人员判断“开一桌”还是“加到现有桌”：相当于 admission；
- 加单先放到桌边托盘：相当于 pending input queue；
- 厨房审查订单并记入账本：相当于 Hook 检查和 rollout persistence；
- 厨师下一次查看托盘：相当于下一轮模型 sampling 前 drain pending input。

门口收下订单，不等于已经入座；入座不等于已经记账；加单放到托盘，也不等于厨师正在处理这道菜。

本篇最重要的心智模型就是：

```text
queued → admitted → pending/initial input → hook accepted → persisted → sampled
```

其中任何一步都可能失败或被取消。

---

## 2. 贯穿案例：两条消息几乎同时到达

假设 thread 当前空闲，客户端 A、B 几乎同时提交：

```text
A: “先检查登录失败的原因”
B: “另外也看看最近的错误日志”
```

理想结果不是启动两个互相竞争的 Turn，而是：

```text
A ──► Started { turn_id: T }
B ──► Steered { turn_id: T }
```

两条消息属于同一个活动 Turn。第一条成为初始输入，第二条进入该 Turn 的 pending queue，并在后续模型请求前写入 history。

这里的关键问题是：谁先拿到“当前没有活动 Turn”的判断权？源码通过持有 `active_turn` 相关锁并在同一临界区完成检查和队列修改，避免两条消息都把自己看成第一个。

---

## 3. 本篇阅读地图

主路径如下：

```text
App-server turn/start
    │
    ▼
CodexThread::submit_user_input_...
    │  只把 Submission 发入 channel
    ▼
submission_loop
    │
    ▼
handlers::user_input_or_turn
    │
    ├─ steer_input 成功 ──► Steered + pending queue
    │
    └─ NoActiveTurn ──────► spawn RegularTask + Started
                                  │
                                  ▼
                         run_hooks_and_record_inputs
                                  │
                         Hook ────┼──── rollout flush
                                  ▼
                             model sampling
```

显式 `turn/steer` 走一条更短的路径：

```text
App-server turn/steer
    ▼
Session::steer_input
    ▼
当前 Turn 的 pending input queue
```

---

## 4. 三个必须分开的承诺层级

| API 语义 | 等待到哪里 | 能保证什么 | 不能保证什么 |
|---|---|---|---|
| 普通 submit | submission channel 接收 | Core loop 将有机会处理 | 未保证 Started/Steered，更未保证落盘 |
| wait for admission | `Started` 或 `Steered` 已确定 | 消息归属某个 Turn | 未保证 rollout 已持久化 |
| wait for persisted admission | admission 与 persistence 都完成 | 消息已归属 Turn，且写入已 flush | 不保证模型已经读取或回答 |

这张表是本篇的核心。以后看到返回了一个 `turn_id`，先问：它来自哪一级 API？

---

## 5. `turn/start` 的名字比实际保证更强

App-server 的 `turn_start_inner` 会：

1. 找到 thread；
2. 检查是否允许直接输入；
3. 验证输入长度；
4. 把 v2 输入转换成 Core 输入；
5. 合并 Turn 设置；
6. 构造 `Op::UserInput`；
7. 调用 `submit_user_input_with_client_user_message_id`。

源码注释写着“Start the turn”，但该调用返回时，通常只说明 `Submission` 已成功发入 channel。

等价白话代码：

```text
submission_id = 生成新 ID
把 { submission_id, Op::UserInput, client_id } 发给 Core loop
立即把 submission_id 当作 turn_id 返回
```

因此，App-server 返回的 `TurnStatus::InProgress` 是客户端投影，不是“模型已经开始 sampling”的同步证明。

官方公开语义也把 `turn/start` 描述为以用户输入启动 Turn；而内部源码进一步告诉我们：RPC 响应与 Core 真正 admission 之间存在异步边界。参见 [OpenAI Codex App Server 官方文档](https://learn.chatgpt.com/docs/app-server)。

---

## 6. 为什么 submission ID 可以成为新 Turn ID

普通输入若最终走 `Started` 分支，源码返回：

```rust
UserMessageAdmission::Started { turn_id: sub_id }
```

也就是说，创建 submission 时生成的 ID 被沿用为新 Turn ID。

这样做的好处是客户端可以立即拿到一个稳定相关 ID，不必等待另一次异步分配。但代价是：

> “拿到 Turn ID”不自动证明 Turn 已被 Core 建立。

ID 是相关性标识，成功阶段必须由事件、admission API 或 durable admission API另行表达。

---

## 7. `client_user_message_id` 与 `submission_id` 不是一回事

| ID | 谁产生 | 标识什么 |
|---|---|---|
| `submission_id` | Core 客户端接口 | 一次提交操作；启动新 Turn 时也成为 Turn ID |
| `client_user_message_id` | 上层客户端 | 一条用户消息的客户端稳定身份 |
| `turn_id` | Core admission 结果 | 最终接纳该消息的 Turn |

为什么 durable admission 必须要求 `client_user_message_id`？

因为消息被 steer 到已有 Turn 后：

```text
submission_id = S2
active turn_id = T1
```

持久化代码处理的是队列中的用户消息，需要用客户端消息 ID 把“这个 rollout 写入”与等待中的提交 S2 对回去。

---

## 8. `SessionIo::submit_with_id` 只负责过 channel

`SessionIo` 把数据包装成：

```text
Submission {
    id,
    op,
    client_user_message_id,
    trace,
    parent_turn_id,
}
```

然后发送到 `tx_sub`。

发送成功只证明接收端还活着、channel 接受了值。此时 Core 还可能在后面发现：

- 输入为空；
- 设置无效；
- Session loop 已终止；
- Hook 拒绝输入；
- rollout flush 失败。

因此，channel send 是 transport acknowledgement，不是业务完成证明。

---

## 9. 为什么 waiter 必须先注册、再发送

等待 admission 的 API 顺序是：

```text
1. 生成 submission_id
2. 在 PendingUserMessageAdmissions 注册 oneshot waiter
3. 使用同一个 submission_id 发送 Submission
4. 等待 waiter 或 Session loop 终止
```

不能先发送再注册。否则 Core loop 足够快时可能发生：

```text
Core 完成 admission
    ↓
查不到 waiter，于是结果无人接收
    ↓
调用者才注册 waiter
    ↓
永远等待
```

这是一种典型的“通知早于订阅”竞态。

---

## 10. Guard 为什么不是多余包装

`register` 返回一个 `PendingUserMessageAdmissionGuard`。它的 `Drop` 会从 map 删除 submission。

这覆盖了调用者 future 被取消、超时或提前返回的情况：

```text
调用者不再等待
    ↓
guard drop
    ↓
pending map 删除该 waiter
```

没有 Guard，取消一个请求可能永久遗留 sender、client ID 和状态，形成内存泄漏，甚至让以后相同 client ID 误配旧请求。

---

## 11. `user_input_or_turn` 是统一 admission 入口

Core 收到 `Op::UserInput` 后，调用：

```rust
user_input_or_turn_inner(...).await
pending_user_message_admissions.complete(&sub_id, admission)
```

这两个动作要分开理解：

- inner 决定 Started、Steered 或错误；
- outer 把决定送给可能存在的 waiter。

普通 fire-and-forget submit 没注册 waiter，`complete` 查不到条目便直接返回。这不是错误，而是同一处理路径同时服务“只排队”和“等待结果”两类调用者。

---

## 12. 为什么先创建 `current_context`，再尝试 steer

`user_input_or_turn_inner` 会先调用 `new_turn_with_sub_id`，应用本次提交携带的设置并建立当前上下文，然后尝试 `steer_input`。

初学者可能会问：“既然可能只是 steer，为什么先建一个 context？”

这里要区分：

- 为本次提交解析和验证设置的 `current_context`；
- 最终真正执行任务的 active Turn context。

若 steer 成功，消息被加入现有 active Turn；若没有 active Turn，这个 `current_context` 就用于启动新任务。源码还会用它记录 telemetry 和发设置相关事件。

不要把局部变量名 `current_context` 直接理解成“已经安装的活动 Turn”。

---

## 13. 核心决策不是 `if idle`，而是“先尝试 steer”

等价白话代码是：

```text
尝试把消息加入活动 Turn

如果成功：
    返回 Steered { 活动 turn_id }

如果明确是 NoActiveTurn：
    组装初始输入
    spawn RegularTask
    返回 Started { submission_id }

如果是其他 steer 错误：
    发 Error 事件
    admission 失败
```

这比“先读一次 idle，再决定调用哪个函数”更安全。后者会在检查与执行之间留下竞态窗口。

---

## 14. `NoActiveTurn(items)` 为什么把输入还回来

`SteerInputError::NoActiveTurn` 携带原始 `items`。

原因是 steer 尝试失败不应该吞掉消息。调用者拿回输入后，可以把它变成新 Turn 的初始输入：

```text
尝试 steer(items)
    └─ NoActiveTurn(items) → spawn_task(items)
```

这是一种很实用的所有权设计：失败类型不仅说明原因，还返还下一条分支继续需要的数据。

---

## 15. `Started` 分支怎样组装初始输入

如果没有活动 Turn，源码会：

1. 写入可选 `parent_turn_id`；
2. 写入 Responses API client metadata；
3. 合并 `additional_context`；
4. 把额外上下文转换成 `TurnInput::ResponseItem`；
5. 把非空用户内容包装成 `TurnInput::UserInput`；
6. `spawn_task(..., RegularTask::new())`。

所以初始输入并不必然只有一条用户文本。它可能是：

```text
[额外上下文 ResponseItem, 用户 UserInput]
```

顺序很重要：额外上下文先进入，用户消息随后进入。

---

## 16. `Steered` 分支不会启动第二个 `RegularTask`

steer 成功时，Core 只返回活动 Turn 的 ID。输入被放入现有 Turn 的 `TurnState.pending_input`。

这意味着：

- 不创建新的并行模型主循环；
- 不把新消息插入正在进行的 model request；
- 当前 sampling/tool work 可以继续；
- Turn 主循环在安全点 drain 新输入，再决定 follow-up。

“steer 当前 Turn”更接近“给当前任务追加指示”，不是中断旧任务再开新任务。

---

## 17. 显式 `turn/steer` 多了一道 generation fence

App-server `turn/steer` 要求 `expectedTurnId` 非空，并传入 Core：

```text
steer_input(..., expected_turn_id = Some(expectedTurnId), ...)
```

Core 会比较：

```text
expected == actual active turn ID ?
```

不相等则返回 `ExpectedTurnMismatch`。

这道检查防止迟到请求误加到后来启动的新 Turn。例如：

```text
客户端看到 T1 正在运行
客户端发送 steer(T1)，网络延迟
T1 已结束，T2 已启动
迟到的 steer 到达
```

没有 expected ID，旧指令可能污染 T2；有 generation fence，它会被明确拒绝。

---

## 18. 为什么普通 `Op::UserInput` 不传 expected Turn ID

普通入口表达的是：

> 有活动 Turn 就追加；没有就启动。

显式 `turn/steer` 表达的是：

> 只能追加到我看到的这个特定 Turn，否则失败。

两者产品语义不同，所以前者调用 `steer_input` 时传 `None`，后者传 `Some(expected_turn_id)`。

---

## 19. 哪些活动 Turn 不允许 steer

`steer_input` 只接受 Regular task。Review 与 Compact 属于不可 steer 的 Turn kind。

这不是说它们没有 Turn ID，而是说其执行合同不允许任意用户输入插入。App-server 会把错误映射成更具体的：

- cannot steer a review turn；
- cannot steer a compact turn。

因此，“存在 active Turn”只是必要条件，不是充分条件。

---

## 20. 空输入为什么在合并 additional context 前拒绝

显式 steer 会先检查用户输入是否为空，再合并 additional context。

固定提交的测试明确验证：只有 context、没有用户输入的 steer 会失败，而且 context 不会偷偷留在 Session 中。

这保证了失败请求没有半完成副作用：

```text
EmptyInput → 不合并 context → 返回错误
```

---

## 21. `TurnInput` 不只表示用户文字

队列元素有三种：

```rust
TurnInput::UserInput { content, client_id }
TurnInput::ResponseItem(...)
TurnInput::InterAgentCommunication(...)
```

分别表示：

- 用户可见输入；
- 已经是模型协议形态的上下文项；
- Agent 间通信。

因此 `InputQueue` 是“Turn 后续可消费输入”的统一队列，不只是聊天文本框缓冲区。

---

## 22. 两层队列：Turn-local 与 Session-scoped

源码中有两种 pending storage：

| 存储 | 作用域 | 典型内容 |
|---|---|---|
| `TurnState.pending_input` | 当前 Turn | steer 用户消息 |
| `InputQueue.mailbox_pending_mails` | Session | Agent 间 mailbox 消息 |

`get_pending_input` 在允许当前 Turn 接收 mailbox 时，会先取 Turn-local pending，再取 Session mailbox，合并后交给主循环。

不能把二者简单合成一个全局 `Vec`，因为 mailbox 消息可能被明确推迟到下一 Turn，而 steer 必须唤醒当前 Turn 的 follow-up。

---

## 23. `extend_pending_input_and_accept_mailbox_delivery...` 做三件事

这个长函数名其实很诚实：

1. 把新输入追加到当前 Turn 的 pending items；
2. 重新允许 mailbox 在当前 Turn 交付；
3. 通过 watch channel 发出 `InputQueueActivity::Steer`。

等价白话代码：

```text
锁住 TurnState
pending_input.extend(new_items)
mailbox_delivery_phase = CurrentTurn
解锁
activity = Steer
```

通知只是告诉等待者“状态变了”；真实数据仍以队列为准。

---

## 24. watch channel 为什么只传 `Mailbox` 或 `Steer`

watch channel 保存的是最近活动类型，不负责保存全部消息。即使多次通知被合并，输入也不会丢，因为数据已经进入：

- `pending_input.items`；或
- `mailbox_pending_mails`。

这是常见设计：

```text
队列保存事实
watch 负责唤醒
```

不要把 watch value 当成工作项本身。

---

## 25. `split_off(0)` 表示原子式 drain

读取 pending input 时使用 `split_off(0)`：返回全部旧元素，同时让原容器变空。

概念上相当于：

```text
drained = 所有当前 pending items
pending items = []
```

这一动作发生在锁内，所以同一批输入不会被两个消费者同时取走。新 steer 要么进入本批之前，要么进入下一批，不会处于“取了一半”的模糊状态。

---

## 26. steer 不会硬插进正在飞行的模型请求

模型 API 请求一旦发出，输入 payload 已经确定。新消息不能神奇地改写远端正在处理的 HTTP body。

源码采取的策略是：

```text
当前 sample 完成
    ↓
主循环发现 has_pending_input
    ↓
drain + Hook + record
    ↓
构造下一次 prompt
    ↓
再次 sample
```

所以 steer 的效果是推动 follow-up sampling，而非修改已经发出的 sampling。

---

## 27. 新 Turn 的第一条输入为什么不会立刻 drain steer

`run_turn` 初始阶段会先处理启动该 Turn 的 initial input。对刚启动的 Turn，后来的 pending steer 通常要等首轮 sample 之后再 drain。

这样可以维持一个清晰边界：

1. 首条消息确实启动并形成第一次模型请求；
2. 并发到达的第二条消息成为后续指示；
3. 第二次模型请求看到完整更新后的历史。

固定提交的并发 admission 测试正是这样断言：第一次请求看到 started message，第二次请求看到两条消息。

---

## 28. admission 状态机的四个内部状态

`PendingUserMessageAdmissionState` 有四种：

| 状态 | 含义 |
|---|---|
| `Immediate` | admission 一确定即可回复 |
| `WaitingForAdmission` | durable 模式，尚未拿到 admission |
| `Admitted(result)` | admission 已到，仍等 persistence |
| `Persisted` | persistence 已到，仍等 admission |

注意 `Admitted` 和 `Persisted` 都可能是中间态，因为两个异步完成信号没有固定先后顺序。

---

## 29. 为什么必须允许 persistence 先到

直觉上似乎一定是“先 admission，再 persistence”。但 steer 路径在把消息加入 active Turn 队列时，可以通过 `client_id` 关联 admission；而主循环、队列和 waiter 完成是多个异步边界，代码不能依赖脆弱的调度顺序。

健壮状态机应接受两种时间线：

```text
时间线 A：Admitted → Persisted → 回复
时间线 B：Persisted → Admitted → 回复
```

只有两块拼图都到齐，durable waiter 才收到成功。

---

## 30. `complete` 怎样处理 admission

简化规则：

```text
admission 失败：立即移除并回复错误

admission 成功：
    Immediate  → 立即回复
    Waiting    → 保存为 Admitted
    Persisted  → 两条件齐全，立即回复
    Admitted   → 忽略重复完成
```

这使同一个状态容器同时支持快速 admission 与 durable admission。

---

## 31. `complete_persistence` 怎样处理落盘

它先通过 `client_id` 找到非 Immediate waiter，然后：

```text
持久化失败 → 移除并回复具体错误

持久化成功：
    旧状态是 Admitted → 两条件齐全，回复成功
    其他状态          → 记为 Persisted，继续等 admission
```

`Immediate` 不参与 durable persistence 匹配，所以普通 admission waiter 不会无意中强迫每条消息 flush。

---

## 32. 为什么 map 用 `std::sync::Mutex`

这个锁保护的是很小的内存状态变更：查 map、改枚举、移除 sender；临界区内没有 `.await`。

因此同步 Mutex 可以让 `complete`、Drop guard 和持久化回调保持简单。这里若机械地认为“Tokio 项目所有锁都必须是 async Mutex”，反而会误解代码。

选择锁的关键问题是：持锁期间会不会等待异步操作？这里不会。

---

## 33. durable admission 为什么会触发显式 `flush_rollout`

一般的 `record_conversation_items` 会更新 history，并请求持久化 rollout item；但 durable admission 需要更强的完成边界。

`record_pending_input` 检查该 `client_id` 是否有 durable waiter。如果有，就：

1. 记录用户 prompt 并发 Turn item 事件；
2. 记录 Hook 增加的 context；
3. 调用 `flush_rollout()`；
4. flush 成功才 `complete_persistence(Ok)`。

所以 durable 不是“已经排队等待写”，而是显式等待 writer 刷过这条消息。

---

## 34. 用户消息记录同时服务 history、rollout 与 UI

`record_user_prompt_and_emit_turn_item` 会：

- 把用户输入转换为 `ResponseItem`，写入 conversation history；
- 把它持久化到 rollout；
- 从原始 `UserInput` 构造 `UserMessageItem`；
- 发 item started/completed 生命周期事件；
- 保留 UI 使用的 `text_elements` 和 `client_id`。

为什么 UI 事件不直接从 `ResponseItem::Message` 反推？因为后者不携带所有 UI-only span 信息。

同一用户消息因此有多个投影，但身份和顺序必须保持一致。

---

## 35. Hook 在“接纳”与“持久化”之间

`run_hooks_and_record_inputs` 对每个输入先调用 `inspect_pending_input`。

用户输入会触发 UserPromptSubmit Hook；ResponseItem 和 Agent 通信默认不走这类用户 prompt 审查。

如果 Hook 决定停止：

```text
reject_pending_input
记录 Hook 额外上下文
不记录被拒绝的用户消息
```

因此 `Steered` 只说明消息进入了 Turn 的处理范围，不代表它已经通过 Hook。

---

## 36. `RejectedByHook` 为什么是 durable admission 错误

Hook 拒绝后，源码用 `client_id` 找到等待者并完成：

```text
Err(UserMessageAdmissionError::RejectedByHook)
```

这比模糊的“任务失败”更准确：

- Core admission 可能已经判定 Started/Steered；
- 但消息没有成为 durable user message；
- 模型也不应消费它。

durable API 的调用者能据此决定是否向用户显示策略拒绝，而不是盲目重试。

---

## 37. persistence failure 为什么停止 sampling

若 rollout flush 失败，代码仍继续记录同批后续已 drain 的输入，使它们不会凭空消失；但 `run_hooks_and_record_inputs` 最终返回 stop，阻止本轮模型 sampling。

原因是：如果模型已经根据未可靠持久化的消息产生外部动作，恢复后可能无法解释或安全重放这段行为。

等价原则：

> 可以尽量保住已经取出的输入，但在 durable barrier 失败后不要继续产生新的模型副作用。

---

## 38. 四类 admission 失败要分别理解

| 错误 | 失败边界 | 例子 |
|---|---|---|
| `Admission(CodexErr)` | 尚未成功归属 Turn | 无效设置、错误 Op、容量拒绝 |
| `RejectedByHook` | 已进入输入处理，但被策略挡下 | UserPromptSubmit Hook stop |
| `TaskEndedBeforePersistence` | Turn 先结束，durability 未确认 | 中断、Session loop 终止 |
| `PersistenceFailed(CodexErr)` | 写入/flush 失败 | rollout writer I/O error |

调用者不应把它们统一显示成“网络错误”。它们代表完全不同的重试与用户提示策略。

---

## 39. Turn 结束怎样清理尚未完成的 waiter

`complete_task_end(turn_id)` 会找出属于该 Turn 的未完成 durable admissions：

- submission 自己启动了这个 Turn；或
- admission 已表明它 steer 到这个 Turn。

然后返回 `TaskEndedBeforePersistence`。

这避免调用者在 Turn 已不存在时永久等待一个永远不会到来的 flush 信号。

---

## 40. Session loop 终止是第二条逃生路径

等待 API 使用 `tokio::select!`：

```text
等待 admission oneshot
或者
等待 session_loop_termination
```

即使某个内部清理路径没有机会发送 oneshot，Session loop 终止也会让调用者得到 `TaskEndedBeforePersistence`，而不是挂死。

`biased` 让已经就绪的 admission 结果优先被观察，减少“结果刚成功、终止信号也到了”时丢失更具体结果的机会。

---

## 41. 并发双提交的完整时间线

下面把贯穿案例展开：

```text
客户端 A                 Core                     客户端 B
   │                      │                          │
   ├─ 注册 waiter S1 ────►│                          │
   ├─ submit S1 ─────────►│                          │
   │                      │◄── 注册 waiter S2 ──────┤
   │                      │◄── submit S2 ───────────┤
   │                      │                          │
   │                      ├─ 处理 S1                 │
   │                      ├─ 无 active Turn          │
   │                      ├─ spawn T=S1              │
   │◄─ Started(T) ────────┤                          │
   │                      │                          │
   │                      ├─ 处理 S2                 │
   │                      ├─ 找到 active T           │
   │                      ├─ queue B into T          │
   │                      ├─ client B ↔ S2           │
   │                      ├──────── Steered(T) ─────►│
```

若使用 durable admission，两边还要各自等待对应用户消息通过 Hook 并 flush。

---

## 42. 为什么测试要求“两者同一个 Turn ID”

并发测试不只数 `Started` 和 `Steered` 各一个，还断言它们的 `turn_id` 相同。

否则可能出现一种表面正确、实际错误的实现：

```text
A = Started(T1)
B = Steered(T2)
```

类型数量对了，但消息归属不同，系统仍发生了竞态。深比较 ID 才证明二者汇合到同一个活动 Turn。

---

## 43. 为什么测试还要检查 rollout 中两个 client ID

只检查 admission 返回成功，无法证明 durable contract。

测试继续读取 rollout，确认两条用户消息的 client ID 都存在。这证明：

- Started 消息被持久化；
- Steered 消息也被持久化；
- 两个 waiter 没有因 client ID 关联错误而互相串线。

这是一种很好的测试写法：返回值证明控制流，持久记录证明外部可恢复事实。

---

## 44. 为什么第一轮模型只看到第一条消息

这不是消息 B 丢了，而是 queue 的时序合同：

```text
initial A → first sample
pending B → record → second sample
```

如果强行要求第一次请求同时看到 A、B，系统就必须在人为窗口内等待“也许还有输入”，既增加延迟，也无法定义窗口何时结束。

当前实现选择确定边界：首条启动，后来消息 steer follow-up。

---

## 45. 明确的 `turn/steer` 为什么通常不等 durable admission

App-server 的显式 steer 直接调用 `thread.steer_input`，成功即返回 active Turn ID。它证明输入已放进 pending queue，但没有等待 Hook 与 rollout flush。

这符合交互式 steer 的低延迟目标；客户端随后通过 item/Turn 事件观察处理结果。

如果内部扩展需要“返回前确保消息已落盘”，应使用 persisted admission API，而不是把普通 steer 的返回语义想得更强。

---

## 46. `try_start_turn_if_idle` 是相邻但不同的入口

该 API 主要供 extension 在 thread idle 时自动启动工作，不是 App-server 用户消息主入口。

它额外检查：

- 是否已有用户/client trigger 的 mailbox 工作；
- 是否处于 Plan mode 且输入没有用户消息；
- 是否已有 active task；
- idle reservation 是否仍属于自己。

它先插入一个 reservation，再异步准备 Turn，避免两个 extension 同时看到 idle。

若携带用户消息，它也会等待初始输入持久化；若失败，错误会携带原始输入，允许调用者决定重试还是放弃。

---

## 47. reservation 为什么还要用 `Arc::ptr_eq` 复查

仅仅看到 `active_turn.is_some()` 不足以证明里面仍是自己放入的 reservation。异步等待期间，状态可能被清理和替换。

`Arc::ptr_eq` 检查对象身份：

```text
当前 TurnState 就是我之前保留的那个？
```

这是一种比比较字段更可靠的 generation/ownership 检查，防止迟到的自动任务覆盖后来合法建立的活动 Turn。

---

## 48. 常见误解一：`turn/start` 一定新开 Turn

错误。

在固定提交中，`turn/start` 最终提交 `Op::UserInput`，Core 的统一入口会先尝试 steer。如果已有可 steer 的 Regular Turn，它可以变成 `Steered`。

公开 API 名描述客户端意图；内部 admission 负责在并发状态下给出实际归属。

---

## 49. 常见误解二：steer 成功表示模型已看见新消息

错误。

steer 成功通常表示消息已进入当前 Turn 的 pending queue。正在飞行的模型请求不会被改写；消息要等安全点 drain、Hook、record，然后进入后续 prompt。

---

## 50. 常见误解三：写入 history 就等于 durable

不够准确。

history 是内存中的模型上下文权威状态之一；rollout 是可恢复账本。普通记录会安排持久化，但 durable admission 还显式等待 `flush_rollout`，才向调用者承诺这条消息已跨过持久化屏障。

---

## 51. 常见误解四：有锁就没有竞态

锁只保护其临界区内的不变量。本链路还有：

- waiter 注册与通知先后；
- admission 与 persistence 先后；
- Turn 完成与 flush 先后；
- 客户端 expected Turn 与当前 Turn 世代；
- idle reservation 与异步初始化。

所以实现还需要状态机、oneshot、generation fence、Drop guard 和终止信号。

---

## 52. 常见误解五：pending queue 是失败重试队列

不是。

它保存“已经被当前 Turn 接受、但尚未在安全点并入下一模型步骤”的输入。失败重试通常有自己的次数、退避和幂等语义；这里的核心是 Turn 内后续输入调度。

---

## 53. 一次消息的状态表

| 阶段 | 内存位置或证据 | 能否安全告诉用户“已保存” |
|---|---|---|
| RPC 已解析 | request processor 局部变量 | 不能 |
| channel 已接收 | `tx_sub.send` 成功 | 不能 |
| Started/Steered | admission result | 只能说已接纳 |
| pending queue | `TurnState.pending_input` | 不能说已落盘 |
| Hook accepted | record path 继续 | 仍要看 flush contract |
| rollout flushed | `complete_persistence(Ok)` | 可以说已持久接纳 |
| model sampled | Responses request/stream | 可以说模型已开始处理该上下文 |
| Turn completed | terminal event | 可以说本 Turn 已结束 |

---

## 54. 调试“我发了消息但没反应”的顺序

建议按边界检查：

1. RPC 是否成功返回 submission/turn ID？
2. 是否有 admission 错误事件？
3. 实际结果是 Started 还是 Steered？
4. active Turn 是否为 Review/Compact？
5. expected Turn ID 是否过期？
6. pending input 是否入队并触发 `Steer` activity？
7. UserPromptSubmit Hook 是否拒绝？
8. rollout flush 是否失败？
9. 当前模型请求是否仍在飞行？
10. 后续 sampling 是否包含该 client message ID 对应内容？

不要一上来只查模型 API；问题可能停在模型调用之前很远。

---

## 55. 设计新调用方时怎样选 API

| 调用方需求 | 合适语义 |
|---|---|
| UI 只需快速提交，靠事件观察 | 普通 submit |
| 内部逻辑必须知道启动还是 steer | wait for admission |
| extension 返回前必须保证可恢复 | wait for persisted admission |
| 只允许追加到用户看到的特定 Turn | steer + expected Turn ID |
| thread idle 时才允许自动工作 | `try_start_turn_if_idle` |

选择依据不是哪个函数名字最像，而是调用方需要哪一层完成保证。

---

## 56. 本篇真正展示的并发设计原则

### 原则一：先注册观察者，再发布工作

避免完成通知跑在 waiter 前面。

### 原则二：把多个独立完成条件写成显式状态机

不要假设 admission 与 persistence 的调度顺序。

### 原则三：通知与事实分离

watch channel 负责唤醒，queue 保存真实输入。

### 原则四：迟到请求必须带世代条件

`expectedTurnId` 防止旧 steer 污染新 Turn。

### 原则五：失败要返还足够语义

Started、Steered、Hook reject、task ended、persistence failed 必须可区分。

### 原则六：取消等待也要清理注册状态

Drop guard 让 caller cancellation 不变成泄漏。

---

## 57. 测试证据地图

### Core admission 集成测试

`codex-rs/core/tests/suite/user_message_admission.rs` 重点验证：

- 两条并发 durable submission 恰好一个 Started、一个 Steered；
- 两者归属同一 Turn；
- 两个 client ID 都出现在 rollout；
- 第一次与第二次模型请求看到预期消息集合；
- 无效设置能快速拒绝，Session 后续仍可使用；
- 非 UserInput Op 被立即拒绝；
- Session shutdown 后等待不会挂死。

### App-server steer 测试

`codex-rs/app-server/tests/suite/v2/turn_steer.rs` 验证：

- 没有 active Turn 时拒绝；
- expected ID 与实际 Turn 的约束；
- 输入长度限制；
- 返回 active Turn ID；
- client message ID 出现在 item 事件；
- context-only steer 被拒绝且不残留 context。

### 持久化失败测试

`codex-rs/core/src/session/tests.rs` 中的 persistence failure 测试验证：

- durable waiter 得到 typed failure；
- Turn 进入错误终态；
- 已 drain 的后续输入仍尽量保留；
- 失败后停止 sampling，避免重复副作用。

---

## 58. 建议亲自做的源码跟读

按此顺序搜索：

```bash
rg -n 'turn_start_inner|turn_steer_inner' codex-rs/app-server/src
rg -n 'submit_user_input_and_wait_for' codex-rs/core/src
rg -n 'user_input_or_turn_inner' codex-rs/core/src
rg -n 'pub async fn steer_input' codex-rs/core/src
rg -n 'PendingUserMessageAdmissionState' codex-rs/core/src
rg -n 'run_hooks_and_record_inputs|record_pending_input' codex-rs/core/src
rg -n 'get_pending_input|has_pending_input' codex-rs/core/src
```

每找到一个函数，写下三个答案：

1. 它返回时承诺到哪个阶段？
2. 用户消息此时存在哪里？
3. 下一次失败还能通过什么通道反馈？

---

## 59. 理解检查

### 问题 1

`submit_user_input_with_client_user_message_id` 返回 Turn ID，是否证明消息已经写入 rollout？

答案：否。它主要证明 submission channel 接收成功；ID 在 Started 路径会被用作 Turn ID。

### 问题 2

两条并发消息为什么不会都启动新 Turn？

答案：Core 统一尝试 steer，并在受保护的 active Turn 状态上完成检查与修改；第一条安装任务后，第二条会看到 active Regular Turn 并入队。

### 问题 3

为什么 durable admission 要保存 `Admitted` 和 `Persisted` 两种半完成状态？

答案：两个完成信号可能以任意顺序到达，必须等两者齐全。

### 问题 4

显式 steer 成功后，新消息是否能影响已经发出的模型 HTTP 请求？

答案：不能。它会在安全点 drain，影响后续 sampling。

### 问题 5

为什么 `expectedTurnId` 很重要？

答案：它是世代围栏，阻止迟到的旧请求追加到后来出现的新 Turn。

### 问题 6

Hook 拒绝与 admission 拒绝有何区别？

答案：admission 拒绝发生在消息尚未成功归属 Turn；Hook 拒绝发生在消息已进入输入处理后，但未成为 durable model-visible 用户消息。

---

## 60. 本章词汇表

| 英文或代码词 | 字面意思 | 本篇中的具体含义 |
|---|---|---|
| admission | 准入、接纳 | Core 决定用户消息启动新 Turn 或加入现有 Turn |
| `UserMessageAdmission` | 用户消息准入结果 | `Started` 或 `Steered` |
| `Started` | 已启动 | 该提交创建并归属一个新 Turn |
| `Steered` | 已引导 | 该提交加入已有活动 Turn |
| submission | 提交 | 送入 Core submission loop 的操作信封 |
| submission ID | 提交编号 | 关联一次提交；Started 时也用作 Turn ID |
| client user message ID | 客户端用户消息编号 | 跨队列与持久化关联同一消息的稳定身份 |
| pending | 待处理 | 已接受但尚未在安全点并入模型上下文 |
| queue | 队列 | 保存待消费 `TurnInput` 的有序容器 |
| drain | 排空、取走 | 原子取出当前全部 pending items，并清空原队列 |
| durable | 耐久的 | 已跨过 rollout flush，进程重启后可恢复 |
| persistence | 持久化 | 把内存事实写入 rollout 存储 |
| flush | 刷写 | 等待此前排队的持久化操作完成 |
| waiter | 等待者 | 等待 admission 或 persistence 结果的调用 future |
| oneshot | 单次通道 | 只发送一个最终结果的 Tokio channel |
| guard | 守卫对象 | Drop 时自动撤销 pending waiter 注册 |
| `Immediate` | 立即 | admission 一确定就回复，不等落盘 |
| `WaitingForAdmission` | 等待准入 | durable waiter 尚未拿到 Started/Steered |
| `Admitted` | 已准入 | 已知 Turn 归属，仍等待 persistence |
| `Persisted` | 已持久化 | 已知写入完成，仍等待 admission 结果 |
| generation fence | 世代围栏 | 用 expected Turn ID 拒绝针对旧 Turn 的迟到请求 |
| `expectedTurnId` | 预期 Turn 编号 | 显式 steer 只能作用于该活动 Turn |
| active Turn | 活动回合 | 当前正在执行任务的 Turn |
| steer | 引导、追加指示 | 把用户输入加入当前 Regular Turn 的 pending queue |
| initial input | 初始输入 | 新 Turn 第一次进入主循环的输入集合 |
| follow-up | 后续轮次 | pending/tool output 促使模型再次 sampling |
| sampling | 采样 | 向模型发送当前 prompt 并接收输出 |
| Hook | 生命周期钩子 | 用户输入落入模型历史前的可扩展检查与上下文注入点 |
| rollout | 运行账本 | 可恢复的 thread/turn 事件与模型项持久记录 |
| `TurnInput` | Turn 输入 | UserInput、ResponseItem 或 Agent 通信的统一队列元素 |
| mailbox | 信箱 | Session 范围的 Agent 间待交付消息队列 |
| watch channel | 状态观察通道 | 通知等待者发生 Mailbox/Steer 活动，不保存完整工作项 |
| race condition | 竞态条件 | 结果依赖不可控异步先后顺序的错误 |
| critical section | 临界区 | 由锁保护、必须原子观察和修改的状态区域 |
| reservation | 预留 | idle 自动任务先占活动槽，防止多个启动者同时成功 |
| `Arc::ptr_eq` | 指针身份比较 | 确认当前 reservation 仍是自己创建的同一对象 |
| fire-and-forget | 发出后不等待 | 只排队提交，不同步等待业务 admission |
| acknowledgement | 确认 | 某一特定边界完成的回执，必须说明是哪一层 |

---

## 61. 本篇结论

一条用户消息进入 Codex，不是一次函数调用就瞬间完成，而是跨越多个边界：

```text
Submission 被 channel 接收
    ↓
Core 决定 Started 或 Steered
    ↓
初始输入或 pending queue
    ↓
Hook 检查
    ↓
history / rollout 记录
    ↓
durable flush
    ↓
后续模型 sampling
```

整个设计最值得学习的，不是某个 Rust 枚举，而是它拒绝用一个含糊的“成功”覆盖所有阶段：

- ID 负责相关；
- admission 负责归属；
- queue 负责安全调度；
- Hook 负责策略边界；
- persistence barrier 负责耐久承诺；
- expected Turn ID 负责抵御迟到请求；
- Guard 与终止信号负责让等待不会泄漏或挂死。

下一篇计划精读 **UserPromptSubmit Hook 与 additional context lifecycle**：一条输入在进入模型历史前怎样被检查、阻止、追加开发者上下文，并保证事件、持久化和失败语义一致。

