# 88：App-server Thread、Turn、Item 状态机、快照、事件流与恢复一致性

> 源码基线：4ee41929eaf4。本章解决“客户端先读取历史、再接收实时事件时，怎样把 Thread、Turn、Item 拼成一份可信 UI 状态，以及断线重连、事件重复、部分数据和最终快照怎样处理”。

## 1. 本章解决什么问题

App-server 不会每次变化都把整条对话重新发送一遍。它通常先给客户端一个快照，再持续发送增量事件。

这更高效，却带来一个难题：客户端怎样知道某个字段是完整数据、摘要、临时进度，还是最终结果？

## 2. 资料边界

当前[官方 OpenAI App Server 文档](https://learn.chatgpt.com/docs/app-server)说明了 Thread、Turn、Item 生命周期和事件合同。本章再用固定提交 4ee41929eaf4 的协议类型、ThreadHistoryBuilder、listener 与测试解释具体实现。

官方文档会继续演进；涉及本课程源码细节时，以固定提交为准。

## 3. 先说人话：快照加流水账

把 Thread 想成一个项目文件夹，Turn 是一次工作轮次，Item 是轮次里的消息、命令或文件修改。

打开项目时先拿到一张当前照片；打开以后发生的变化，再通过流水账逐条追加。

## 4. 三层包含关系

最常见的关系是：

```text
Thread
└── Turn 1
    ├── UserMessage Item
    ├── AgentMessage Item
    └── CommandExecution Item
└── Turn 2
    └── ...
```

Thread 是长期容器，Turn 是一次用户输入到本轮终止，Item 是可显示或可追踪的工作单元。

## 5. Thread 不是操作系统线程

这里的 Thread 是一条可持久化、恢复、派生和订阅的对话任务线。

它不是 `std::thread::Thread`，也不代表一条 CPU 执行线程。

## 6. Turn 是什么

Turn 通常从一次 `turn/start` 开始，到 completed、interrupted 或 failed 结束。

一个 Thread 可以顺序包含许多 Turn。

## 7. Item 是什么

Item 是 Turn 内部的具体工作单元，例如：

- 用户消息
- Agent 消息
- 推理摘要
- 命令执行
- 文件修改
- MCP 工具调用
- 子 Agent 活动

不同 Item variant 拥有不同字段和内部状态。

## 8. 为什么需要状态机

状态机不是一定要写成一个叫 StateMachine 的类型。只要对象只能经过有限状态，并由事件推动转换，它就在遵守状态机合同。

TurnStatus 与 Item 自身的 status 字段就是这种合同。

## 9. Thread、Turn、Item 不是同一套状态

不要把三层状态混在一起：

- ThreadStatus 描述整条任务线当前是否加载、活跃或异常。
- TurnStatus 描述某一轮是否进行中或已经如何结束。
- Item status 描述某个命令、工具或文件修改的执行结果。

一个 Thread 为 Active 时，其中可能已有许多 Completed Turn。

## 10. ThreadStatus 的四种形态

固定提交定义：

- `NotLoaded`
- `Idle`
- `SystemError`
- `Active { active_flags }`

它们描述运行时状态，不是完整的持久化历史分类。

## 11. NotLoaded

NotLoaded 表示 App-server 当前没有把这条 Thread 加载为活跃运行对象。

它不等于“Thread 不存在”，也不等于“历史为空”。

## 12. Idle

Idle 表示 Thread 已加载，但当前没有正在执行的 Turn。

客户端仍可能订阅它，也仍可以开始下一轮。

## 13. Active

Active 表示 Thread 当前有运行活动。

`active_flags` 还能提示是否正在等待审批或等待用户输入。

## 14. SystemError

SystemError 是 Thread 运行层面的异常状态。

它不要与某个 Turn 的 `Failed` 或某个 Item 的 `Failed` 简单等同。

## 15. TurnStatus

固定提交中的 TurnStatus 有：

- `InProgress`
- `Completed`
- `Interrupted`
- `Failed`

其中后三个是终态。

## 16. Turn 的正常转换

最简单路径是：

```text
不存在于当前轮次
        |
        v
   InProgress
        |
        v
    Completed
```

Turn 开始后不能因为 UI 暂时没收到 Item 就被判断为 Completed。

## 17. Turn 的中断转换

用户调用 `turn/interrupt` 后，成功结果不是立刻把本地状态改完。

最终应由 `turn/completed` 中的 `Interrupted` 状态收敛。

## 18. Turn 的失败转换

TurnComplete 携带 error 时，历史投影把状态设为 Failed，并构造 TurnError。

错误通知可以先出现，但终态仍由完成事件确认。

## 19. 没有 Running 这个名字

协议使用 `InProgress`，不是 `Running`。

学习代码时应区分业务概念的“正在运行”和 wire enum 的精确拼写。

## 20. ThreadItem 是 Tagged Enum

ThreadItem 通过 `type` 字段区分 variant。

这意味着客户端先看 `item.type`，再按对应 shape 读取字段，不能假设所有 Item 都有 `status` 或 `text`。

## 21. Item 生命周期的共同外壳

尽管 Item variant 不同，实时协议提供两个共同事件：

- `item/started`
- `item/completed`

二者都携带完整的 `item`、`threadId` 和 `turnId`。

## 22. ItemStartedNotification

固定提交的 started 通知包含：

- `item`
- `threadId`
- `turnId`
- `startedAtMs`

`item.id` 是后续 delta 对位的关键。

## 23. ItemCompletedNotification

completed 通知包含：

- 最终 `item`
- `threadId`
- `turnId`
- `completedAtMs`

完成通知没有单独的 startedAtMs；需要时应从 started 状态或持久化记录合并。

## 24. Started Item 是临时快照

以命令为例，started 时可能有命令、cwd 和 `InProgress`，但还没有 exit code、完整输出和 duration。

所以 started 适合立即渲染，不适合作为最终记录。

## 25. Completed Item 是权威最终状态

官方合同明确要求把 `item/completed` 中的最终 Item 当作权威状态。

客户端应按相同 item ID 替换临时 Item，而不是盲目追加第二条。

## 26. 为什么不能只拼 Delta

Agent message、plan、reasoning 和 command output 会产生 delta。

但 delta 是传输进度，不保证拼接结果与最终 Item 完全相同；例如最终内容可能经过规范化或汇总。

## 27. Delta 的正确职责

Delta 用来改善实时体验：字逐步出现、命令输出不断增长、计划逐步显示。

它是“临时渲染材料”，不是永久真相来源。

## 28. Delta 的对位键

Item delta 通常携带：

- threadId
- turnId
- itemId
- delta

客户端至少用三层 ID 找到正确 Item，不能只按“当前打开的 Turn”追加。

## 29. 为什么只按 itemId 也不够稳妥

协议里的 item ID 可能在特定 legacy shape 中有特殊生成方式。把 threadId、turnId 一起纳入 key，可以避免跨上下文误合并。

推荐逻辑 key：`(threadId, turnId, itemId)`。

## 30. 一个最小客户端状态

概念上可以维护：

```text
threads[threadId]
  turns[turnId]
    items[itemId]
    itemOrder[]
```

Map 负责按 ID 幂等更新，order 数组负责稳定显示顺序。

## 31. 为什么 Map 和顺序数组都要有

只用 Vec，替换和去重需要线性搜索；只用 Map，显示顺序可能不稳定。

两者组合是常见的 normalized state。

## 32. TurnStartedNotification

Turn started 通知包含 threadId 和一个 Turn。

固定提交会把该 Turn 的 `items` 清空，并把 `itemsView` 设为 `NotLoaded`。

## 33. 为什么 TurnStarted 不携带当前全部 Item

TurnStarted 的职责是宣布生命周期边界，而不是重复发送历史。

紧随其后的 Item 事件会逐步建立本轮内容。

## 34. TurnCompletedNotification

Turn completed 同样携带 threadId 和 Turn，但它的 items 也不一定完整。

固定提交只可能放最后一条 Agent 消息摘要，或完全不放 Item。

## 35. TurnItemsView

Turn 通过 `itemsView` 显式告诉客户端 items 的完整程度：

- `NotLoaded`
- `Summary`
- `Full`

这个字段决定了“空数组”应该如何解释。

## 36. NotLoaded 的空数组

`items=[]` 且 `itemsView=NotLoaded` 意味着“没有加载 Item”。

它不意味着“这个 Turn 确实没有 Item”。

## 37. Summary

Summary 表示 items 只有适合快速显示的摘要子集。

缺失的命令、推理或中间消息不能被解释为已删除。

## 38. Full

Full 表示该 payload 包含持久化历史中可提供的全部 ThreadItem。

客户端只有在 Full 时，才适合用整个列表替换对应 Turn 的完整 items 集合。

## 39. 三种 View 的覆盖规则

一个实用规则是：

- Full 可以重建整个 Turn Item 集合。
- Summary 只更新它明确携带的 Item，不删除其他已知 Item。
- NotLoaded 不对已有 Item 做删除性解释。

## 40. 空不等于无

这是本章最重要的一句话之一。

在部分加载协议里，空数组可能表示“没取”，而不是“确认没有”。

## 41. Thread.turns 也可能为空但不代表无历史

Thread 类型注释说明，turns 只在特定 resume、rollback、fork、read 场景中填充。

其他响应或通知返回 Thread 时，turns 会是空列表。

## 42. Snapshot 是什么

Snapshot 是某个时点的物化状态，例如 `thread/read(includeTurns=true)` 返回的 Thread 与 Turns。

它让客户端不必重放从系统诞生以来的全部事件。

## 43. Stream 是什么

Stream 是连接建立以后持续收到的通知序列。

它让客户端低延迟显示新变化，而无需频繁轮询整份历史。

## 44. Snapshot + Stream 模式

典型流程是：

```text
读取或恢复快照
      |
      v
建立本地状态
      |
      v
按顺序应用新事件
```

真正困难的是快照与事件之间不能出现空窗。

## 45. 空窗竞态

错误流程：先读取历史，稍后才订阅。

如果一个 Item 恰好在两步之间完成，快照里没有它，订阅流也错过它，客户端会永久缺一条变化。

## 46. thread/read 的语义

官方文档明确说 `thread/read` 读取存储快照，但不恢复 Thread，也不订阅事件。

它适合历史浏览，不单独提供实时一致性。

## 47. includeTurns

`thread/read` 的 includeTurns 为 true 时，响应包含 Turns；为 false 或省略时，只返回 Thread 摘要。

是否加载 Turn 与是否订阅是两个正交选择。

## 48. thread/resume 的语义

Resume 不只是“读文件”。它会加载或重新接入运行中的 Thread，并让连接接收后续事件。

因此它要解决 snapshot 与 subscription 的排序问题。

## 49. 冷恢复与运行中重连

冷恢复是 Thread 尚未加载，需要从历史构建运行对象。

运行中重连是 Thread 已在执行，新的 connection 要加入，并拿到包含 active Turn 的一致快照。

## 50. 为什么运行中重连更难

持久化文件可能只到上一个 append；内存中 active Turn 还可能有更新。

响应必须合并 durable history 与 live active snapshot。

## 51. ThreadListenerCommand

固定提交把运行中 resume 请求包装成 `SendThreadResumeResponse`，送进每 Thread listener 的命令通道。

这不是普通 helper；它是顺序控制机制。

## 52. Listener 同时处理两类输入

Thread listener 的 loop 同时接收：

- listener command
- conversation event

两者在同一个 async loop 中被串行处理。

## 53. 单 Listener 的意义

如果 resume response 在另一个独立 task 中组装，它可能与 ItemCompleted 并发交错。

放进同一个 listener，相当于为快照和实时事件建立同一条排序线。

## 54. Biased Select

固定提交使用 `tokio::select! { biased; ... }`，并把 listener command 分支放在 event 分支之前。

当多个分支同时 ready 时，前面的 command 获得优先选择。

## 55. Biased 不等于全局优先级

它只影响这个 select 调用在多个 ready 分支间的选择。

它不是操作系统优先级，也不能替代整个顺序设计。

## 56. active_turn_snapshot

ThreadState 内部维护 ThreadHistoryBuilder，并暴露 active_turn_snapshot。

运行中 resume 会读取这个内存快照，再与持久化 Turn 历史合并。

## 57. 为什么需要合并 active Turn

若只发磁盘历史，客户端可能看不到当前正在执行的命令或 Agent 输出。

若只发内存 active Turn，又会丢掉过去已完成的 Turns。

## 58. merge_turn_history_with_active_turn

该操作按 Turn ID 把 active Turn 合入历史列表。

核心思想是 ID 对位更新，不是简单 push，避免同一 Turn 出现两份。

## 59. 先订阅还是先发响应

固定提交在 listener command 内先把 connection 加入 Thread subscribers，再发送 resume response。

由于 command 本身在 listener 中串行执行，后续事件只能在 command 返回后处理。

## 60. 客户端会先看到什么

在正常的同一 connection 输出顺序里，resume response 先被发送；随后 listener 才继续翻译新事件。

订阅登记提前完成，避免事件落入“无人接收”的空窗。

## 61. 原子订阅的真实含义

这里的“原子”不是一条 CPU instruction。

它表示 response snapshot 与 subscriber 注册相对 Thread event listener 共享同一个不可穿插的顺序边界。

## 62. Response 后的附加快照

Resume response 后还可能对该 connection 发送 token usage、goal snapshot 和待处理 server request replay。

客户端不应假设一个 response 已经包含所有辅助 UI 状态。

## 63. Pending Server Request Replay

若 Thread 正等待审批或用户输入，新订阅连接需要看到尚未解决的 server request。

否则 UI 会显示“正在运行”，却没有可操作的审批框。

## 64. Replay 后的 resolved

重放的请求仍可能很快被其他连接解决。

`serverRequest/resolved` 让所有相关 UI 删除或关闭过期提示。

## 65. 多连接订阅

同一 Thread 可以有多个 subscribed connection。

ThreadScopedOutgoingMessageSender 会把通知路由给当时订阅该 Thread 的连接集合。

## 66. 订阅不是 Thread 所有权

Connection 加入 subscribers，只表示它希望接收事件。

它不因此独占 Thread，也不阻止其他连接订阅。

## 67. Unsubscribe

`thread/unsubscribe` 从 Thread 的 connection set 中移除调用连接。

它不会因为一个 UI 关闭页面就删除 Thread 历史。

## 68. Connection 断开

断线清理会从所有 Thread 的 subscriber set 移除该 connection。

Thread 是否立即卸载，还取决于是否有其他 subscribers 和是否仍 Active。

## 69. 无订阅者卸载

固定提交存在“无 subscribers 且 idle 后延迟卸载”的生命周期逻辑。

订阅集合因此也参与内存资源管理，而不仅是通知路由。

## 70. ThreadHistoryBuilder

ThreadHistoryBuilder 是把 EventMsg/rollout items 归约成 Turns 与 Items 的 reducer。

它同时服务历史重建和运行中 active Turn 跟踪，使两条路径尽量共享语义。

## 71. Reducer 是什么

Reducer 接收“旧状态 + 一个事件”，产生新状态。

概念公式：

```text
new_state = reduce(old_state, event)
```

## 72. Builder 的核心字段

固定提交保存：

- 已完成 turns
- current_turn
- item 顺序计数
- rollout 索引
- 可选 change set

这些字段让事件既能物化状态，也能报告本次改变了什么。

## 73. handle_event

handle_event 对不同 EventMsg 调用对应 handler。

TurnStarted 打开当前 Turn，ItemStarted/Completed 更新 Item，TurnComplete/Aborted 关闭 Turn。

## 74. 同一个 Reducer 用于两种场景

历史恢复按 rollout 顺序重放事件。

运行时 listener 在事件到达时调用同一 Builder 跟踪 current Turn。

## 75. 为什么共享 Reducer 很重要

如果恢复逻辑和实时逻辑分别实现，极容易出现“在线看起来一种结果，重启后又变成另一种结果”。

共享状态归约规则可减少这种漂移。

## 76. Rollout 是什么

Rollout 是 Thread 的持久化记录流，常见载体是按顺序追加的 JSONL。

它不必等于客户端看见的 wire notification；中间还存在协议投影。

## 77. Projection

Projection 把内部 canonical event/item 转成客户端 Thread、Turn、ThreadItem 视图。

投影可以省略内部字段、重命名状态或生成摘要。

## 78. Canonical ItemCompleted

新分页历史格式持久化 canonical `ItemCompleted(TurnItem)` 记录。

thread_history_projection 对它生成 changed item，并保留 started/completed timestamps。

## 79. 为什么持久化 Completed 而不是每个 Delta

Delta 数量多、临时性强，还可能经过重试或修正。

持久化最终 Item 更适合重建稳定历史，也能显著减少状态复杂度。

## 80. 实时体验与持久化真相分层

实时层可以有很多细粒度 delta。

持久层重点保存能够恢复最终语义的 canonical record。

## 81. TurnStarted 的历史投影

project_rollout_line 看见 TurnStarted 时产生：

- status=InProgress
- startedAt
- completedAt=None
- error=None

这建立 Turn 的初始物化行。

## 82. TurnComplete 的历史投影

TurnComplete 无 error 时变成 Completed，有 error 时变成 Failed。

同时写入完成时间、duration 和可选 TurnError。

## 83. TurnAborted 的历史投影

TurnAborted 有 turn ID 时投影成 Interrupted。

若历史事件缺少 turn ID，投影返回空 change set，避免猜错目标。

## 84. 不猜目标是一种正确性策略

面对无法可靠对位的旧数据，跳过更新通常比修改错误 Turn 更安全。

“尽量恢复”不等于“随便猜一个最近对象”。

## 85. Change Set

ThreadHistoryChangeSet 描述本次 append 改变了哪些 Turns 和 Items。

它适合增量写入存储，而无需每次重建整个 Thread。

## 86. Batch Coalescing

Builder 可以处理一批 rollout items，并把同一 Turn/Item 的多次变化合并成最新快照。

这样数据库只需接收批次末的有效状态。

## 87. Coalescing 与丢事件不同

Coalescing 会省略中间状态，但保留批次结束后的最终状态。

它适用于物化视图，不适合需要逐事件审计的日志用途。

## 88. Item 顺序

Item ID 用于身份，item order 用于展示次序。

不能把 HashMap 的遍历顺序当作对话顺序。

## 89. 重复事件

网络或恢复路径可能让客户端面对语义重复的数据。

按 `(threadId, turnId, itemId)` upsert 能让 started/completed 重放具备基本幂等性。

## 90. Upsert

Upsert 是“存在则更新，不存在则插入”。

对生命周期事件，它通常比无条件 append 更正确。

## 91. Completed 先于 Started 怎么办

理想流中 started 在前，但健壮客户端仍可直接插入 completed Item，并标记为终态。

后来若收到较旧 started，不应把终态降级回 InProgress。

## 92. 终态单调性

一旦同一 Item 或 Turn 已知进入终态，旧的非终态事件不能覆盖它。

这叫 monotonic terminal state，能抵抗延迟和重放。

## 93. Timestamp 能否决定一切

Timestamp 可辅助判断，但不要仅靠客户端接收时间排序。

服务端事件顺序、对象 ID、状态优先级和 payload view 共同决定合并规则。

## 94. 毫秒与秒

Turn 的 startedAt/completedAt 使用 Unix seconds；Item lifecycle 时间使用 milliseconds。

显示或比较前必须统一单位，避免把 1 秒误当 1 毫秒。

## 95. startedAtMs 为零或 completedAtMs 为零

历史兼容数据可能用 0 表示未知。固定投影会把 completedAtMs=0 转为 None。

未知时间不应显示为 1970 年的真实事件。

## 96. Error Notification 与 Turn Failed

`error` notification 描述错误事件，并有 `willRetry`。

willRetry=true 时只是中间流错误；客户端不能立即把 Turn 标成 Failed。

## 97. 最终失败的确认

只有终结 Turn 的 payload 表示 Failed，才应把该 Turn 收敛到失败终态。

临时错误可以显示警告，但应允许后续重试成功。

## 98. Interrupt Response 与完成事件

`turn/interrupt` 的空 response 只表示中断请求被接受处理。

UI 若要确认结果，应等待 TurnCompleted 的 Interrupted。

## 99. 请求确认与状态确认

这是异步系统的通用区别：

- RPC response：命令是否被接受。
- lifecycle event：对象最终变成什么状态。

两者不能互相替代。

## 100. ThreadStatusChanged

ThreadStatusChangedNotification 携带 threadId 与完整的新 ThreadStatus。

客户端应替换 status，而不是尝试从 flag delta 猜测完整集合。

## 101. Active Flag 是集合快照

Active 中的 activeFlags 是当前集合，不是“新增一个 flag”的命令。

新通知到来时应整体替换 flags。

## 102. 状态来源冲突

Resume snapshot、ThreadStatusChanged、TurnStarted 和 TurnCompleted 可能都影响 UI 对“是否忙”的判断。

最好把协议状态分别存储，再由 selector 计算 UI，而不是到处直接修改一个 `isBusy` boolean。

## 103. Derived State

Derived state 是由原始协议状态计算的 UI 状态，例如：

```text
showSpinner = thread.status is Active
showApproval = Active flags contains WaitingOnApproval
```

派生值不必重复持久化。

## 104. 为什么单个 isBusy 不够

它无法区分正在运行、等待审批、等待用户输入和系统异常。

过早压扁状态会让后续 UI 无法表达真实语义。

## 105. Snapshot 覆盖策略

收到 Full snapshot 时，可以重建对应范围；收到 Summary/NotLoaded 时，只做非删除性合并。

收到实时 completed event 时，按 ID 覆盖对应 Item 的临时版本。

## 106. 一个简化合并算法

```text
onItemStarted(event):
  if existing is terminal: ignore stale start
  else upsert(event.item)

onItemDelta(event):
  if existing is terminal: ignore stale delta
  else append to transient buffer

onItemCompleted(event):
  replace item with event.item
  clear transient buffer
  mark terminal
```

重点是 completed 替换，而不是把临时字段与最终字段随意拼接。

## 107. TurnStarted 的简化处理

```text
upsert turn(id, status=InProgress)
do not delete previously restored items merely because itemsView=NotLoaded
set activeTurnId=id
```

若这是一个全新 Turn，items 自然从空集合开始。

## 108. TurnCompleted 的简化处理

```text
upsert terminal metadata
merge summary item if present
do not replace full item list unless itemsView=Full
clear activeTurnId only when IDs match
```

最后一条规则防止旧 Turn 的迟到事件清掉新 Turn。

## 109. 为什么检查 Turn ID

异步 UI 可能已经进入 Turn B，随后收到 Turn A 的完成或回放数据。

若只写 `activeTurn=null`，会错误关闭 Turn B 的运行状态。

## 110. 断线后不要从本地最后事件盲续

固定提交的普通通知没有统一公开 sequence number 供客户端精确补洞。

断线重连时应重新 resume/read 快照，再应用新的连接事件，而不是假设本地缓存无缺口。

## 111. Sequence Number 的价值

若协议提供单调序号，客户端可以发现缺口、去重并请求 replay。

本章固定接口不能普遍依赖它，所以使用 authoritative snapshot 重新收敛。

## 112. Eventual Consistency

实时 delta 期间 UI 可能短暂不完整，但最终 completed item 和恢复快照会收敛到相同状态。

这是一种以最终收敛为目标的一致性设计。

## 113. 它不是随便延迟都可以

Eventual consistency 仍需要明确：

- 权威来源是谁
- 最终何时到达
- 如何去重
- 如何发现或修复缺失

没有这些合同，只是“不一致”。

## 114. Snapshot Isolation 的误区

这里说的 snapshot 不自动意味着数据库 snapshot isolation。

它只是某时点的协议物化视图；一致性来自 listener 排序与合并规则。

## 115. Persistence Lag

内存事件发生到 durable rollout append 之间可能存在时间差。

运行中 resume 合并 active Turn，正是在弥补“只读持久化历史”可能过旧的问题。

## 116. Crash Recovery 边界

若进程在事件产生但尚未持久化时崩溃，内存状态无法恢复。

课程源码能保证的是已持久化记录的重建逻辑，不是任意时刻零数据丢失。

## 117. Stale InProgress Turn

恢复历史时可能发现一个 InProgress Turn，但当前 Thread 实际已不再运行。

固定提交会结合 loaded status 与 live agent status 规范化陈旧状态，避免 UI 永久转圈。

## 118. 为什么历史本身不总能说明“仍在运行”

TurnStarted 可能已落盘，而进程在写 TurnComplete 前退出。

必须用当前 runtime facts 判断这个 InProgress 是否仍真实活跃。

## 119. Rollback 与状态重建

Rollback 会改变 Thread 可见历史边界。

客户端收到 rollback 后不应仅删除屏幕最后一行；更可靠的做法是按返回的 Thread/Turns 重建受影响范围。

## 120. Fork

Fork 创建新的 Thread ID，并复制选定历史。

即使 Items 内容相似，新 Thread 的后续事件也必须按新 threadId 隔离。

## 121. Ephemeral Thread

Ephemeral 表示不应物化到磁盘的 Thread。

它仍可以有实时 Turn/Item 生命周期，但崩溃或进程退出后的恢复能力不同。

## 122. Pagination 与 Snapshot

历史很长时，Turns/Items 可以分页加载。

单页 snapshot 只覆盖该页范围，不能拿第一页缺失的旧 Turn 当作已删除。

## 123. Backwards Cursor

固定提交的 resume 可以返回 backwards cursor，帮助客户端从已取页面继续抓取更早或更新范围。

Cursor 是服务端定义的 opaque token，不应自行解析。

## 124. 页面与 Active Turn

运行中 resume 会尽量把 active Turn 合入合适页面，必要时为 descending 页面预留 active slot。

这避免第一页全是旧历史，而当前正在运行的 Turn 不可见。

## 125. ItemsView 与分页是两回事

Pagination 回答“取哪一段”；ItemsView 回答“每个 Turn 的 Item 取到什么程度”。

客户端需要同时保存这两个维度。

## 126. UI 列表的稳定 Key

React、SwiftUI 等 UI 应使用协议对象 ID 作为稳定 key。

用数组下标会导致插入历史页或合并 active Turn 时组件状态错位。

## 127. 乐观 UI

客户端发送 turn/start 后可以先显示用户输入，但必须准备与服务端返回的 Turn/Item ID 对位。

若协议提供 client user message ID，应利用它消除本地临时消息与服务端消息重复。

## 128. 乐观状态必须可回滚

RPC 失败时，本地临时 Turn/Item 不能假装已经被服务端接受。

给 optimistic entity 单独标记 local/pending 比伪造服务端终态更安全。

## 129. 审批是 Item 生命周期的一部分

命令或文件修改先 item/started，再发 server request 等待审批，最后 item/completed。

等待审批不是一个新的 Turn；它是 Active Turn 中 Item 暂停推进。

## 130. 为什么审批 UI 要用多组 ID

审批 request 带 threadId、turnId、itemId 和 requestId。

前三个定位业务对象，requestId 对位这次双向 RPC，职责不同。

## 131. Duplicate Command Start 抑制

固定提交用 `command_execution_started` set 防止审批兼容路径和 canonical ItemStarted 重复发起同一个命令 start。

这是服务端在事件投影边界做去重的例子。

## 132. Set 何时清理

命令 ItemCompleted 时移除 item ID。

若不清理，集合会增长；若过早清理，重复 start 又可能穿透。

## 133. 服务端去重不替代客户端幂等

服务端会尽量保证合理事件流，但断线、重放和版本兼容仍要求客户端按 ID upsert。

稳健性应该分层实现。

## 134. 测试：Started 不带 Active Items

固定提交有测试确认 TurnStarted 即使内部 active snapshot 已有内容，通知仍将 items 清空并标记 NotLoaded。

这防止客户端把生命周期通知误认成完整 snapshot。

## 135. 测试：Completed Summary

测试确认 TurnCompleted 可只携带最后 Agent message，并把 itemsView 标成 Summary。

这直接支撑“不要用完成通知清空其他 Items”的客户端规则。

## 136. 测试：历史投影

thread_history_projection_tests 覆盖 TurnStarted、TurnComplete、TurnAborted 和 ItemCompleted 到 change set 的映射。

这些测试比只看 enum 定义更能说明实际状态转换。

## 137. 测试：运行中 Resume

Thread processor/lifecycle 测试关注 active Turn 合并、订阅与 response/event 顺序。

这是 snapshot + stream 边界最值得阅读的集成证据。

## 138. 观测指标

生产客户端至少可以记录：

- 收到未知 thread/turn/item ID 的 delta 数量
- completed-before-started 数量
- resume 后首个事件延迟
- snapshot merge 冲突数量
- 长期停留 InProgress 的对象数量

## 139. Unknown ID Delta

收到 delta 却找不到 Item 时，可暂存在有界 orphan buffer，等待 ItemStarted。

缓冲必须有大小和时间上限，避免恶意或异常流导致无界内存。

## 140. 为什么不能无限缓存

事件可能永远不会补齐，或来自客户端不再关心的 Thread。

有界缓存加日志，比无限等待更安全。

## 141. Client Reducer 的不可变测试

给 reducer 输入一串事件，比较整个最终 state，而不是逐字段断言。

这样更容易发现旧 Item 未清理、顺序重复或 itemsView 被错误降级。

## 142. 建议的测试序列一

```text
TurnStarted
ItemStarted(agent message)
Delta("你")
Delta("好")
ItemCompleted("你好")
TurnCompleted(Summary)
```

最终应只有一条 Agent message，内容以 Completed 为准。

## 143. 建议的测试序列二

```text
Resume Full snapshot
duplicate ItemCompleted
stale ItemStarted
```

最终状态不能重复 Item，也不能从 terminal 降级到 InProgress。

## 144. 建议的测试序列三

```text
Turn A started
Turn B started
late Turn A completed
```

若协议异常或重放产生该序列，late A 不应清空 B 的 activeTurnId。

## 145. 建议的测试序列四

```text
Full snapshot: 5 items
TurnCompleted: Summary with last agent message
```

最终仍应保留 5 个 Item，并更新摘要中对应的那一项。

## 146. 常见错误一：把通知当数据库行

通知 payload 可能为部分视图，不能每次都整对象 replace。

先看 itemsView 和事件合同，再决定覆盖范围。

## 147. 常见错误二：把 Delta 永久保存

若永久存储所有 delta 再自行拼接，恢复结果可能与 canonical completed Item 不同。

最终持久化应以 completed snapshot 为主。

## 148. 常见错误三：只维护一个 currentItem

一个 Turn 中可能有并行或交错的工具、命令、推理和消息 Item。

必须按 itemId 分开追踪。

## 149. 常见错误四：收到空列表就清空

NotLoaded 和 Summary 下的空或短列表不是删除命令。

这是最常造成“命令执行记录突然消失”的 UI bug。

## 150. 常见错误五：RPC 成功就宣布 Turn 完成

turn/start response 只创建 InProgress Turn；turn/interrupt response 只确认请求。

Turn 终态来自生命周期事件。

## 151. 常见错误六：重连后继续用旧 transient buffer

旧连接的 delta buffer 可能包含未完成片段。

新 resume snapshot 到来时，应按权威 Item 重建并清除无法对位的旧临时 buffer。

## 152. 一套来源优先级

对同一 Item，可采用：

```text
completed canonical item
    > full persisted snapshot
    > started item + live deltas
    > summary/notLoaded absence
```

这里的 `>` 表示更适合决定最终内容，不代表所有字段都能无条件覆盖。

## 153. 字段级合并仍要谨慎

Full snapshot 可能带持久化时间，live completed 带更新的运行信息。

实际客户端应定义每类 Item 的 merge 函数，不要用通用浅合并掩盖语义。

## 154. Version Skew

客户端和 App-server 版本不同，可能出现未知 Item variant 或新字段。

解析层应保留可诊断信息，UI 层应提供 fallback，而不是让整个 Stream 崩溃。

## 155. 状态机审查清单

新增事件时问：

1. 它创建、更新还是终结哪个对象？
2. 对位 ID 是什么？
3. payload 是 full、summary 还是 delta？
4. 能否重复？
5. 迟到时能否让状态倒退？
6. 是否持久化？
7. 恢复路径能否得到相同结果？

## 156. Snapshot + Stream 审查清单

1. 订阅和快照之间有没有空窗？
2. 快照之后的第一个事件是否保证不丢？
3. 断线后怎样重新收敛？
4. 部分页能否被误当完整集合？
5. pending approvals 是否会 replay？
6. 多连接解决请求后怎样关闭其他 UI？

## 157. 阅读源码的推荐顺序

1. v2/thread_data.rs：Thread、Turn、ItemsView
2. v2/turn.rs 与 v2/item.rs：通知 payload
3. thread_history.rs：共享 reducer
4. thread_history_projection.rs：durable change set
5. thread_state.rs：active snapshot 与 subscriber set
6. thread_lifecycle.rs：atomic resume/rejoin
7. bespoke_event_handling.rs：EventMsg 到 wire notification

## 158. 一个完整时间线

```text
client -> turn/start
server -> response: Turn(InProgress, items=[])
server -> turn/started(NotLoaded)
server -> item/started(Command InProgress)
server -> output deltas...
server -> item/completed(Command Completed)
server -> item/started(AgentMessage)
server -> message deltas...
server -> item/completed(AgentMessage final)
server -> turn/completed(Completed, Summary)
```

客户端用 started/delta 提供实时感，用 completed 收敛最终状态。

## 159. 断线重连时间线

```text
old connection breaks
server continues active turn
new connection -> thread/resume
listener serializes resume with events
server registers subscriber
server -> response(history + active turn)
server -> pending request replay / auxiliary snapshots
server -> subsequent live events
```

关键不是“重新连上”，而是快照与后续事件之间没有丢失窗口。

## 160. 本章核心结论

App-server 的一致性不是靠一份永远完整的对象，而是靠四个合同协作：

1. ID 对位身份。
2. ItemsView 表示完整程度。
3. Completed snapshot 终结临时状态。
4. Per-thread listener 排序 resume snapshot 与 live stream。

## 161. 理解检查

请尝试回答：

1. 为什么 `items=[]` 不能直接解释为没有 Item？
2. 为什么 Delta 拼接不能替代 ItemCompleted？
3. thread/read 与 thread/resume 的订阅语义有什么区别？
4. 运行中 resume 为什么要合并 durable history 与 active snapshot？
5. 为什么先登记 subscriber、再发 response 仍能让 response 排在新事件前？
6. 迟到的 started 为什么不能覆盖 completed？

## 162. 动手练习一：写 Reducer

用 TypeScript、Python 或伪代码实现：

- onTurnStarted
- onItemStarted
- onItemDelta
- onItemCompleted
- onTurnCompleted

要求区分 Full、Summary、NotLoaded，并保证终态不倒退。

## 163. 动手练习二：制造空窗

写一个小模拟器：先 read snapshot，等待 100ms，再 subscribe；在中间插入 ItemCompleted。

观察客户端为什么永远缺少该 Item，再改成服务端串行 snapshot+subscribe。

## 164. 动手练习三：恢复测试

对同一组事件分别：

- 实时逐条 reduce
- 生成 canonical rollout，再用 ThreadHistoryBuilder 重建

比较两个最终 Turn 列表是否相等。

## 165. 源码检查点

- `codex-rs/app-server-protocol/src/protocol/v2/thread_data.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/turn.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/item.rs`
- `codex-rs/app-server-protocol/src/protocol/thread_history.rs`
- `codex-rs/app-server-protocol/src/protocol/thread_history_projection.rs`
- `codex-rs/app-server/src/thread_state.rs`
- `codex-rs/app-server/src/request_processors/thread_lifecycle.rs`
- `codex-rs/app-server/src/request_processors/thread_processor.rs`
- `codex-rs/app-server/src/bespoke_event_handling.rs`

## 166. 本章词汇表

| 术语 | 字面含义 | 本章中的具体意思 |
|---|---|---|
| Thread | 线程/任务线 | 可持久化和恢复的对话容器，不是 OS thread |
| Turn | 一轮 | 一次用户输入触发的完整 Agent 工作轮次 |
| Item | 条目/工作单元 | Turn 中的消息、命令、文件修改或工具调用 |
| State machine | 状态机 | 对象可进入的有限状态及合法转换规则 |
| Terminal state | 终态 | Completed、Interrupted、Failed 等不会回到进行中的状态 |
| Snapshot | 快照 | 某一时点物化出的 Thread/Turn/Item 状态 |
| Stream | 流 | 连接上连续到达的实时通知序列 |
| Delta | 增量片段 | 为实时渲染追加的文字或输出，不是最终权威内容 |
| Authoritative | 权威的 | 冲突时应作为最终状态来源 |
| TurnItemsView | Turn条目视图 | 声明 items 是未加载、摘要还是完整集合 |
| NotLoaded | 未加载 | payload 没装入数据，不表示确认为空 |
| Summary | 摘要 | 只携带适合展示的部分 Item |
| Full | 完整 | 该范围内的持久化 Item 已全部物化 |
| Subscription | 订阅 | Connection 注册接收某 Thread 后续事件 |
| Resume/Rejoin | 恢复/重新加入 | 加载或接入 Thread，并取得快照与后续事件 |
| Reducer | 归约器 | 用旧状态和一个事件计算新状态的函数/对象 |
| Projection | 投影 | 把内部 canonical 数据转换成协议视图 |
| Canonical | 规范的 | 被系统选作恢复与最终语义基础的表示 |
| Rollout | 执行记录流 | 按顺序持久化的 Thread 历史记录 |
| Upsert | 插入或更新 | 按 ID 存在则替换，不存在则新增 |
| Idempotent | 幂等 | 同一事件重复应用不改变最终结果 |
| Coalescing | 合并压缩 | 同一对象一批变化只保留最新物化结果 |
| Active turn | 活跃轮次 | 当前仍在执行或等待外部输入的 Turn |
| Transient buffer | 临时缓冲 | 保存尚未被 completed snapshot 确认的 delta |
| Eventual consistency | 最终一致性 | 临时视图可不完整，但最终会向权威状态收敛 |
| Persistence lag | 持久化延迟 | 内存变化到 durable record 写入之间的间隔 |
| Version skew | 版本偏差 | Client 与 Server 版本不同造成的协议 shape 差异 |
| Opaque cursor | 不透明游标 | 只能原样交回服务端的分页位置 token |
| Optimistic UI | 乐观界面 | 服务端确认前先显示本地临时状态 |
| Derived state | 派生状态 | 从原始协议状态计算出的 isBusy 等 UI 值 |
