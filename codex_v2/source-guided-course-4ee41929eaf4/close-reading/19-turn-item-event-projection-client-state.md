# Turn/Item event projection 与客户端状态重建：实时通知为什么不是最终快照

> 源码基线：`4ee41929eaf4`。行号只用于固定提交定位；源码变化后优先搜索符号名。

本篇回答一个客户端开发中非常实际的问题：

> App-server 不断发送 `turn/started`、`item/started`、各种 delta、`item/completed` 和 `turn/completed`。客户端只要把这些消息依次画出来，就能得到正确状态吗？

不能这么简单理解。

实时通知适合低延迟展示，但客户端还需要处理：

- started item 被 completed item 替换；
- delta 只是一段增量，不一定等于最终内容；
- Turn completed 通知可能只带 summary，不是全量 items；
- 断线重连时必须从 rollout 重建；
- resume 时历史快照与新事件之间不能留缝；
- 错误、取消和正常完成必须归一成唯一 Turn 终态；
- 同一命令的 legacy 与 canonical 事件可能重复。

所以真正的模型是：

```text
实时事件流 = 低延迟变化
持久历史快照 = 可恢复基线
稳定 ID + upsert = 两者对齐方法
```

---

## 1. 先说人话：直播比分与赛后技术统计

看球时，你会同时接触两种数据：

- 直播事件：“第 23 分钟射门”“第 24 分钟进球”；
- 赛后统计：“最终 2:1、射门 12 次、控球率 54%”。

直播事件让画面及时，但可能重复、修正或暂时不完整；赛后统计较完整，却不能等比赛结束才显示。

Codex 客户端也一样：

- delta 让文字逐字出现；
- item started 让工具立刻显示“运行中”；
- item completed 给出最终 item snapshot；
- turn completed 给出 Turn 终态和摘要；
- thread read/resume/turns list 从持久历史重建完整视图。

正确客户端既消费直播，也接受最终快照校正。

---

## 2. 贯穿案例：模型运行一条命令再回答

用户说：

```text
请检查项目状态
```

客户端可能观察到：

```text
turn/started(T1)
item/started(U1: UserMessage)
item/completed(U1)
item/started(C1: CommandExecution, InProgress)
item/commandExecution/outputDelta(C1, "On branch main...")
item/completed(C1: CommandExecution, Completed)
item/started(A1: AgentMessage)
item/agentMessage/delta(A1, "项目")
item/agentMessage/delta(A1, "状态正常")
item/completed(A1: AgentMessage, "项目状态正常")
turn/completed(T1, itemsView=Summary)
```

客户端不能把 started 和 completed 当两条不同命令；它要按 `item_id=C1` upsert。也不能假设拼接 delta 永远等于 completed item；最终应以 A1 的 completed snapshot 校正。

---

## 3. 三层数据模型

| 层 | 主要类型 | 用途 |
|---|---|---|
| Core lifecycle | `EventMsg`、Core `TurnItem` | Runtime 内部事实与持久事件 |
| App-server projection | v2 `Turn`、`ThreadItem`、notifications | 面向客户端的稳定协议视图 |
| 客户端 view state | TUI event store / UI reducer | 当前屏幕、缓冲与重放状态 |

“projection”就是把上游数据变成下游需要的表示。它不是简单改字段名，有时还需要去重、状态汇总、摘要选择和历史合并。

---

## 4. Turn 与 Item 是嵌套生命周期

最小结构是：

```text
Thread
└── Turn T1
    ├── Item U1: UserMessage
    ├── Item C1: CommandExecution
    └── Item A1: AgentMessage
```

Turn 表示一次用户可感知的工作回合；Item 表示回合内可独立展示的消息、命令、文件变化、工具调用、推理、计划等。

Turn started/completed 包住整批工作；item started/completed 包住某个局部对象。

---

## 5. Core 在哪里发 `TurnStarted`

Regular task 在真正进入 `run_turn` 前内联发送：

```text
EventMsg::TurnStarted {
    turn_id,
    trace_id,
    started_at,
    model_context_window,
    collaboration_mode_kind
}
```

源码注释说明，这样首个 Turn 生命周期不会被 startup prewarm 阻塞。

因此 `turn/started` 表示 Runtime 已建立这次执行边界，不表示已经收到模型第一个 token。

---

## 6. Core 在哪里发 Turn 终态

任务收尾时二选一：

```text
有 abort reason → TurnAborted
否则            → TurnComplete
```

`TurnComplete` 可以携带 terminal error；所以事件名叫 Complete，不等于业务状态一定成功。

App-server 会把它投影成：

- 没有 terminal error：`TurnStatus::Completed`；
- 有 error：`TurnStatus::Failed`；
- `TurnAborted`：`TurnStatus::Interrupted`。

---

## 7. 为什么 App-server 没有单独的 `turn/interrupted` 通知

公开 v2 统一发送 `turn/completed`，但其中 `turn.status` 可以是：

```text
completed
failed
interrupted
```

这让客户端只需要一个终态入口，同时仍能区分原因。

请求方法 `turn/interrupt` 是动作；通知 `turn/completed(status=interrupted)` 是动作完成后的事实。两者不是同一消息。

---

## 8. Item 时间戳为何用毫秒，Turn 用秒

v2 `Turn` 的 `startedAt`、`completedAt` 是 Unix 秒；Item lifecycle 通知的 `startedAtMs`、`completedAtMs` 是 Unix 毫秒。

Item 往往持续很短，需要更细粒度排序和耗时展示。客户端不能把两个单位混用，否则时间会差一千倍。

---

## 9. Core 怎样保证 completed item 有开始时间

`emit_turn_item_started` 把 `item.id()` 和当前毫秒写入 Turn timing state，再发送 ItemStarted。

`emit_turn_item_completed` 取回同一 ID 的开始时间。若找不到，会 warning，并退化为：

```text
started_at_ms = completed_at_ms
```

因此缺失 start 不会让 completed 事件无法发出，但观测系统仍能发现生命周期不完整。

---

## 10. Core event 先持久化，再交付 listener

`send_event_raw_with_persistence` 的顺序是：

```text
若 persist：追加 RolloutItem::EventMsg
记录 protocol trace
发送到 tx_event
```

这条顺序帮助恢复路径看到已经对外发出的关键生命周期事实。

但“调用 persist”与“强制 flush 到稳定介质”仍不是所有事件都相同；需要 durability barrier 的路径会额外 flush。不要把普通 event send 理解为同步 fsync。

---

## 11. Thread listener 是 App-server 的串行观察者

每条运行中 thread 有 listener loop。它在三类来源间 `select!`：

- listener 取消；
- listener command；
- `conversation.next_event()`；
- 以及 unload 触发。

收到 Core event 后，关键顺序是：

```text
1. track_current_turn_event
2. 检查 raw event opt-in
3. 计算订阅连接
4. apply_bespoke_event_handling
5. 发送类型化通知
```

状态先更新、通知后发送，是本章最关键的不变量之一。

---

## 12. 为什么必须“先更新状态，再发通知”

假设先发 `item/completed`，再更新 active Turn snapshot。此时另一个连接刚好调用 resume，可能得到不含该 item 的旧快照，随后又因为订阅时机错过通知。

当前顺序让 listener 内的 snapshot 至少先包含已处理事件，再对外广播它。

客户端看到通知时，服务端内存投影已经不落后于该通知。

---

## 13. `ThreadState` 保存的不是完整永久数据库

它主要保存运行中协调状态：

- pending interrupts / rollback；
- 当前 `TurnSummary`；
- last terminal Turn ID；
- listener generation 与 cancel sender；
- last settings；
- `current_turn_history: ThreadHistoryBuilder`；
- listener command channel；
- watch registration。

持久完整历史仍来自 rollout/thread store。`ThreadState` 更像运行中的投影缓存与协调器。

---

## 14. `TurnSummary` 为什么只保留部分信息

它保留：

- `started_at`；
- 已发 started 的 command IDs；
- `last_error`；
- `last_agent_message`。

这些正是生成轻量 `turn/completed` 通知所需的信息。它不重复保存所有 items，因为完整 item history 由 `ThreadHistoryBuilder` 和持久记录负责。

---

## 15. 最后一个 AgentMessage 怎样成为 Turn summary

`track_current_turn_event` 观察 `ItemCompleted`：

- item 必须是 AgentMessage；
- phase 是 FinalAnswer 或 None；
- 至少包含一段非空 text。

满足后，它更新 `turn_summary.last_agent_message`。

中间 commentary 或空消息不会误成为完成摘要。

---

## 16. `turn/started` 为什么 `itemsView=NotLoaded`

App-server 发 TurnStarted 时构造：

```text
status = InProgress
items = []
itemsView = NotLoaded
```

这里的空数组不是“Turn 确定没有 item”，而是“本通知没有加载 item 列表”。

`itemsView` 是解释 `items` 的必要元数据。只看数组长度会得出错误结论。

---

## 17. 三种 `TurnItemsView`

| 值 | `items` 的含义 |
|---|---|
| `NotLoaded` | 没加载，故意为空 |
| `Summary` | 只含展示摘要，通常是最后 AgentMessage |
| `Full` | 包含从持久 App-server history 可获得的全部 items |

它们不是完整度百分比，而是明确的数据合同。

---

## 18. `turn/completed` 为什么通常也不带全量 items

终态通知优先轻量：

- 有最后 AgentMessage：`items=[last message]`、`Summary`；
- 没有：`items=[]`、`NotLoaded`。

客户端在整个 Turn 期间已经收到 item 通知，没有必要在终态重复发送所有大命令输出和文件变化。

需要权威全量视图时，调用 thread/turn history 读取接口。

---

## 19. completed summary 不能覆盖本地完整 items

客户端若直接写：

```text
local_turn = completed_notification.turn
```

可能把此前积累的完整 items 替换成一条 summary。

正确 reducer 应根据 `itemsView` 合并：

```text
更新 status/error/time
Summary 只更新摘要信息
NotLoaded 不解释为清空
Full 才能作为完整 item snapshot
```

---

## 20. ItemStarted 与 ItemCompleted 是 upsert，不是 append-only UI

两条通知包含相同稳定 item ID。客户端应：

```text
若 ID 已存在 → 替换/更新
若 ID 不存在 → 插入
```

仓库历史 builder 的 `upsert_turn_item` 正是按 `item.id()` 查找并替换。

否则一个命令会显示成“运行中命令 + 已完成命令”两行重复记录。

---

## 21. Delta 是临时增量，不是权威 item

Agent message delta 只包含：

```text
thread_id
turn_id
item_id
delta
```

客户端可将 delta 拼到流式 cell，以便立即显示。但 completed AgentMessage 包含最终结构化 item，应作为校正点。

协议对 Plan delta 更明确警告：客户端不应假设拼接 delta 与 completed plan 内容完全一致。

---

## 22. 为什么 delta 与最终内容可能不同

可能原因包括：

- stream parser 修整；
- 引用或格式后处理；
- 中途取消；
- delta 丢失或重复；
- plan 最终结构化转换；
- 服务端合并/规范化。

所以 UI 可乐观展示 delta，但 item/completed 到达时应收敛到最终 snapshot。

---

## 23. 不同 Item 有不同 progress 通知

常见映射包括：

- AgentMessage → text delta；
- Plan → plan delta；
- Reasoning → summary/content delta 与 section break；
- CommandExecution → output delta、terminal interaction；
- FileChange → patch updated；
- MCP / Dynamic tool → progress 或 completed snapshot。

统一生命周期提供外壳，item-specific notification 提供过程细节。

---

## 24. `item_event_to_server_notification` 为什么只做无状态映射

函数注释强调它只覆盖一对一、stateless projection。

例如：

```text
Core AgentMessageContentDelta
→ App-server AgentMessageDeltaNotification
```

但命令去重、error summary、interrupt response 等需要周围状态，由 `bespoke_event_handling` 调用方负责。

把纯转换和有状态协调分开，单元测试更容易，也避免协议 crate 依赖 server runtime。

---

## 25. canonical Item 与 legacy event 为什么并存

Core 仍可能 fan out `ExecCommandBegin/End`、MCP begin/end、Patch begin/end 等旧事件，供 raw event 和 rollout compatibility 消费者使用。

App-server v2 优先使用 canonical `TurnItem` lifecycle。

因此某些 legacy 分支明确“不再转发给 v2”。这防止客户端同时收到：

```text
legacy command begin
canonical CommandExecution ItemStarted
```

而显示两次。

---

## 26. 命令 started 还有额外去重集合

审批或 Guardian 流程可能比 Core canonical item 更早发命令 start。`turn_summary.command_execution_started` 保存已经向客户端发过 started 的 command ID。

再次收到 canonical ItemStarted 时：

```text
insert(id) 返回 false → 抑制重复 started
```

ItemCompleted 后从集合移除。

这是一种运行时去重，而不是要求所有上游瞬间完成统一迁移。

---

## 27. Error notification 与 Turn failed 是两个阶段

非重试 error 到达时，App-server：

1. 保存 `turn_summary.last_error`；
2. 立即发送 `error` notification，`willRetry=false`；
3. 等 Core TurnComplete；
4. 再发 `turn/completed(status=failed, error=...)`。

前者用于及时提示，后者用于确定终态。客户端不能收到 error 就擅自认为 Turn 生命周期已经闭合。

---

## 28. StreamError 为什么不把 Turn 标为 failed

StreamError 是中间错误，App-server 发送：

```text
error notification, willRetry=true
```

但不更新 `turn_summary.last_error`，因为 Runtime 可能重试并成功完成。

这是“发生过错误”与“最终失败”之间的重要区别。

---

## 29. TurnComplete 的 error 有两条来源

实时 App-server summary 会记住普通 Error event；Core `TurnComplete` 本身也有 terminal error 字段。

固定提交的 App-server实时完成处理主要依据 `turn_summary.last_error`；持久 history builder 则读取 `TurnComplete.error`，也会结合此前影响状态的 Error event。

不同重建路径必须最终投影成相同 `TurnStatus::Failed`。

---

## 30. `ThreadHistoryBuilder` 是事件折叠器

它把一串 rollout/event 输入折叠为：

```text
Vec<Turn>，每个 Turn 包含 Vec<ThreadItem>
```

它处理：

- Turn boundaries；
- item lifecycle；
- legacy events；
- raw ResponseItem 中的特殊消息；
- compaction；
- rollback；
- error/abort/complete；
- 新旧 rollout 兼容。

它不是单纯反序列化，而是状态机式 projection。

---

## 31. 为什么同时支持 live event 与 rollout item

builder 提供：

```text
handle_event
handle_rollout_item
handle_rollout_items_with_changes
```

实时 listener 输入 `EventMsg`；冷恢复输入 `RolloutItem`。两条路线共享同一投影规则，减少“直播 UI 一种结构、恢复后另一种结构”的漂移。

---

## 32. explicit Turn boundary 与旧历史兼容

新 rollout 有明确 TurnStarted/TurnComplete。旧流可能只有 user message。

builder 为兼容旧数据，可以创建 implicit Turn；但看到显式 TurnStarted 时会结束当前 Turn，再用真实 ID 建立 `opened_explicitly` Turn。

显式打开的 Turn 即使没有可渲染 item也会被保留，因为生命周期本身就是事实。

---

## 33. TurnStarted 遇到未完成旧 Turn 怎么办

`handle_turn_started` 先 `finish_current_turn()`，再创建新 InProgress Turn。

这保证 builder 同时只有一个 current Turn，不会因为缺失旧终态而把新 items 挂到旧 Turn 上。

恢复损坏或中断日志时，“新开始边界关闭旧 segment”是实用的自愈规则。

---

## 34. TurnComplete 为什么优先精确匹配 ID

完成事件可能迟到。builder 依次：

1. 匹配当前 Turn ID；
2. 匹配已完成 turns 中的 ID；
3. 找不到时才 fallback 到当前 Turn。

这样迟到的 T1 completion 不会轻易关闭已经运行的 T2。

ID fencing 在事件重建中与上一章 steer 的 expected Turn ID 同样重要。

---

## 35. TurnAborted 也优先精确 ID

有 `payload.turn_id` 时，先找当前或历史中的同 ID Turn；没有 ID 或未知 ID 才 fallback 到 active Turn。

这兼容旧事件缺 ID，同时让新事件尽量避免误伤错误 Turn。

---

## 36. `handle_event_with_changes` 解决什么问题

有些消费者不想每来一个 rollout item 就重新复制全部 history。builder 可返回变化集：

- changed items；
- changed turns；
- removed Turn IDs。

批量处理还会合并同一 ID 的多次变化，只保留最终 snapshot，同时保持第一次出现顺序。

这是一种增量 projection API。

---

## 37. rollback 为什么需要 removed Turn IDs

回滚不只是“某个 item 更新”，而是整个后缀 Turn 可能消失。

change accumulator 看到 removed Turn 后，会丢弃该 Turn 已累计的 changed item/turn，避免同一批次先说“更新 T3”，又说“删除 T3”。

最终变化集表达批次结束时的真实状态。

---

## 38. active snapshot 怎样用于运行中 resume

`ThreadState.active_turn_snapshot()` 从当前 history builder 克隆正在运行的 Turn。

处理 thread resume 时：

1. 从持久 history 构造 turns；
2. 读取 active snapshot；
3. 删除历史中相同 ID Turn；
4. 把 active Turn 追加进去。

这避免 rollout 读取点较旧时，页面把运行中的 Turn 显示成缺 item 或错误状态。

---

## 39. 为什么 resume 必须在 listener command 中执行

注释明确说明：运行中 thread 的 resume response 与订阅新更新必须原子地排序。

如果普通 request task 自己做：

```text
读取历史
（此时新事件发生）
注册订阅
```

中间事件可能既不在历史快照，也没被订阅收到。

把 `SendThreadResumeResponse` 放进同一个 listener command queue，就能与 event consumption 串行排序，关闭这条 gap。

---

## 40. listener command 与 event 谁优先

listener 使用 biased `select!`，顺序是：

```text
cancel
listener command
next Core event
unload
```

已就绪的 resume command 会优先于后续 event 分支处理。关键不是“所有网络时序绝对固定”，而是 resume snapshot 与订阅切换在同一个 owner 中完成，不与独立 task 并发修改。

---

## 41. listener generation 防止旧 listener 继续投影

设置新 listener 时：

- 取消 previous listener；
- generation 加一；
- 创建新 command channel；
- 保存当前 conversation weak reference。

generation 与 pointer identity 帮助管理替换关系，避免旧 listener 在 resume/reload 后继续向客户端发送陈旧事件。

---

## 42. 订阅是 per connection 的

发送通知前，App-server 查询该 thread 的 subscribed connection IDs，再创建 thread-scoped outgoing sender。

因此：

- 多个客户端可以观察同一 thread；
- 未订阅连接不应收到该 thread 的普通通知；
- server request/response 还需要额外的 connection request identity。

Thread state 是共享投影，投递集合是连接级路由。

---

## 43. raw response events 为什么需要 opt-in

`RawResponseItem` 和 `RawResponseCompleted` 只有在 `experimental_raw_events` 开启时才继续投影。

raw item 更接近模型协议，体积和兼容风险都更大；普通 v2 客户端应消费 canonical ThreadItem 通知。

同时使用 raw 与 canonical 流时必须知道它们可能描述同一底层事实，不能盲目双重计数。

---

## 44. TUI 为什么还要自己的 per-thread event store

TUI 可能在主 conversation、subagent 和 side conversation 之间切换。非当前 thread 的通知不能直接丢掉。

`ThreadEventStore` 保存：

- session snapshot；
- turns；
- bounded event buffer；
- active Turn ID；
- pending interrupt；
- pending interactive replay；
- composer/input state。

它是客户端本地 projection，不是服务端 ThreadState 的复制品。

---

## 45. 客户端 buffer 为什么必须有容量上限

TUI 的 buffer 是 `VecDeque`，超过 capacity 会从头淘汰。

无限缓存所有 delta、音频和进程输出会让长期 thread 内存无限增长。源码甚至明确跳过一些大或不可重放通知，例如 raw response item、realtime audio 和 process output delta。

客户端必须区分“需要重放的语义事件”和“只适合实时显示的高容量流”。

---

## 46. session refresh 后为什么要 rebase buffer

拿到新的 session/turn snapshot 后，旧 notification 大多已经被新基线覆盖。若全部重放，会重复 item 或把状态倒退。

TUI 只保留少数需要跨 refresh 生存的事件，例如 HookStarted/Completed、MCP status，以及交互请求类事件。

这就是 snapshot rebase：用新权威基线替换旧推导状态，再选择性保留未被基线覆盖的事件。

---

## 47. active Turn ID 怎样从 snapshot 恢复

`set_turns` 从后向前找第一个 `TurnStatus::InProgress`，设为 `active_turn_id`。

随后实时：

- TurnStarted 设置 active ID；
- 同 ID TurnCompleted 清除；
- ThreadClosed 清除 active 与 pending interrupt。

它不会因其他 Turn 的迟到 completed 通知误清当前 active Turn。

---

## 48. 一个稳健客户端 reducer 的伪代码

```text
on snapshot(turns):
    state = authoritative turns
    replay only events not covered by snapshot

on turn/started(turn):
    upsert turn metadata by turn.id
    mark active
    do not treat NotLoaded items as empty authority

on item/started(item):
    upsert item by (turn_id, item.id)

on delta(item_id, text):
    append to transient streaming buffer

on item/completed(item):
    replace item by ID with completed snapshot
    clear transient buffer for that item

on error(willRetry=true):
    show transient retry status

on error(willRetry=false):
    show error, but wait for Turn terminal event

on turn/completed(turn):
    merge status/error/timing
    merge items according to itemsView
    clear active only if IDs match
```

---

## 49. 常见误解一：空 `items` 表示 Turn 没有内容

错误。先看 `itemsView`。NotLoaded 明确表示没有加载；Summary 也不是全量。

---

## 50. 常见误解二：收到 error 就可以关闭 Turn

错误。`willRetry=true` 可能恢复；即使 false，也应等待统一 Turn terminal notification 完成生命周期。

---

## 51. 常见误解三：把所有 delta 拼起来就是最终答案

错误。delta 是实时体验，completed item 是最终校正。Plan 协议甚至明确否定这一假设。

---

## 52. 常见误解四：resume 只需读 rollout

错误。运行中的 active Turn 可能领先于持久读取快照；还必须合并 live snapshot，并与新订阅原子排序。

---

## 53. 常见误解五：started 与 completed 是两条 item

错误。相同 item ID 表示同一实体的不同状态，应 upsert。

---

## 54. 常见误解六：通知流就是数据库

错误。通知可能只针对订阅连接、可能跳过大 payload、可能在断线时丢失；rollout/thread store 才提供恢复基线。

---

## 55. 调试“UI 状态不对”的顺序

1. Core 是否发出预期 `EventMsg`？
2. event 是否先被 `track_current_turn_event` 消费？
3. raw event 是否因未 opt-in 被过滤？
4. bespoke handling 是否选择 canonical 还是 legacy 分支？
5. notification 的 thread/turn/item ID 是否正确？
6. 客户端是否按 ID upsert，而非 append？
7. delta buffer 是否在 completed 后清除？
8. completed Turn 的 `itemsView` 是什么？
9. error 的 `willRetry` 是什么？
10. active Turn 是否只按相同 ID 清除？
11. resume 是否合并 active snapshot？
12. session refresh 后是否错误重放旧 buffer？
13. listener 是否被新 generation 替换？
14. rollout 冷重建与 live projection 是否得到相同状态？

---

## 56. 测试证据地图

### App-server bespoke event tests

`codex-rs/app-server/src/bespoke_event_handling.rs` 的测试验证：

- TurnStarted 发 InProgress + NotLoaded；
- ItemStarted/Completed 正确转换；
- completed Turn 有 last agent summary 时使用 Summary；
- interrupted 投影为 Interrupted；
- terminal error 投影为 Failed；
- 多 Turn 错误不会串线；
- command started 去重。

### History projection tests

`codex-rs/app-server-protocol/src/protocol/thread_history_projection_tests.rs` 验证：

- Started/Complete/Aborted 状态迁移；
- item lifecycle 进入正确 Turn；
- terminal error 与时间字段；
- changed turns/items 增量输出。

### Rollout reconstruction tests

Core 与 app-server protocol 的 reconstruction tests 覆盖：

- 显式和隐式 Turn 边界；
- 不完整 Turn 后出现新 Turn；
- compaction 与 rollback；
- 迟到终态与 ID 匹配。

### TUI replay tests

TUI 测试覆盖：

- history replay；
- item started/completed 替换；
- agent delta；
- Summary/Full/NotLoaded；
- session refresh 与 bounded buffer；
- active Turn 和 pending interaction 恢复。

---

## 57. 建议亲自跟读的符号

```bash
rg -n 'emit_turn_item_started|emit_turn_item_completed' codex-rs/core/src
rg -n 'TurnStartedEvent|TurnCompleteEvent|TurnAbortedEvent' codex-rs/core/src
rg -n 'track_current_turn_event' codex-rs/app-server/src
rg -n 'apply_bespoke_event_handling' codex-rs/app-server/src
rg -n 'item_event_to_server_notification' codex-rs/app-server-protocol/src
rg -n 'ThreadHistoryBuilder|handle_turn_started' codex-rs/app-server-protocol/src
rg -n 'active_turn_snapshot|merge_turn_history_with_active_turn' codex-rs/app-server/src
rg -n 'ThreadEventStore|rebase_buffer_after_session_refresh' codex-rs/tui/src
```

每次定位后问：

1. 这是事件、增量还是完整 snapshot？
2. 它是否持久化？
3. 作用于哪个稳定 ID？
4. 客户端应该 append、upsert 还是 replace？
5. 断线恢复后怎样得到相同状态？

---

## 58. 理解检查

### 问题 1

`turn/completed` 的 `items=[]` 是否表示这个 Turn 从未产生 item？

不是。若 `itemsView=NotLoaded`，只是该通知没有加载 item。

### 问题 2

为什么 completed item 应覆盖本地 delta 拼接结果？

因为 delta 只用于实时进度，可能经历修整、遗漏或协议不等价；completed 是最终结构化 snapshot。

### 问题 3

为什么 Error 与 TurnCompleted 都要发送？

Error 提供及时反馈；TurnCompleted 关闭生命周期并给出唯一终态。

### 问题 4

运行中 resume 为什么还要 active snapshot？

持久 history 可能落后于 listener 已处理的 live events，active snapshot 补齐正在执行的 Turn。

### 问题 5

为什么 resume command 必须进入 thread listener？

让历史响应、active snapshot 和订阅切换与 Core events 串行排序，防止读历史和订阅之间丢事件。

### 问题 6

同 ID ItemStarted 与 ItemCompleted 应怎样处理？

按 ID upsert；completed 替换 started 的 InProgress snapshot。

### 问题 7

`willRetry=true` 的 error 会把最终 Turn 标成 Failed 吗？

不会自动标记。它是中间 stream error，Turn 仍可能重试成功。

---

## 59. 本章词汇表

| 英文或代码词 | 字面意思 | 本篇中的具体含义 |
|---|---|---|
| projection | 投影 | 把 Core events 转换成客户端 Turn/ThreadItem 状态 |
| lifecycle | 生命周期 | Started、进度、Completed/Failed/Interrupted 的状态过程 |
| snapshot | 快照 | 某一时刻物化的完整或部分状态 |
| delta | 增量 | 相对已有 item 新增加的一小段内容 |
| reducer | 归约器 | 按顺序把事件折叠进客户端状态的函数 |
| upsert | 存在则更新，否则插入 | 用稳定 ID 合并 started/completed snapshot |
| canonical item | 规范 item | v2 首选的统一 `TurnItem` 生命周期表示 |
| legacy event | 旧事件 | 为兼容 raw/rollout 消费者保留的旧 begin/end 事件 |
| `TurnStatus` | Turn 状态 | InProgress、Completed、Failed 或 Interrupted |
| terminal state | 终态 | Turn 不再运行的 Completed/Failed/Interrupted |
| `TurnItemsView` | Turn item 视图完整度 | NotLoaded、Summary 或 Full |
| `NotLoaded` | 未加载 | items 空不代表没有数据 |
| `Summary` | 摘要 | 只包含展示所需的部分 items |
| `Full` | 全量 | 包含持久 history 能提供的全部 ThreadItems |
| `ThreadHistoryBuilder` | Thread 历史构建器 | 把 live events 或 rollout items 折叠为 Turns |
| `PendingTurn` | 构建中的 Turn | builder 内尚未最终放入 turns 列表的状态 |
| explicit boundary | 显式边界 | TurnStarted/TurnComplete 明确打开和关闭 Turn |
| implicit Turn | 隐式 Turn | 为兼容没有显式边界的旧历史而推导的 Turn |
| change set | 变化集 | changed items、changed turns、removed Turn IDs |
| coalesce | 合并归约 | 同批同 ID 多次变化只保留最终 snapshot |
| active Turn snapshot | 活动 Turn 快照 | listener 内存中正在运行 Turn 的物化视图 |
| resume gap | 恢复缺口 | 读历史与订阅新事件之间可能漏掉的时间窗口 |
| listener command | listener 命令 | 与 Core event 在同一 owner 中串行处理的控制请求 |
| listener generation | listener 世代 | 区分被替换的旧 listener 与当前 listener |
| subscribed connection | 已订阅连接 | 有资格收到某 thread 通知的客户端连接 |
| raw event opt-in | 原始事件选择加入 | 显式允许接收低层模型 response events |
| `TurnSummary` | Turn 轻量摘要 | 保存 started time、last error、last agent message、command 去重集合 |
| `willRetry` | 将重试 | error notification 是否代表可恢复中间错误 |
| transient state | 临时状态 | delta 拼接、spinner 等可被最终 snapshot 校正的数据 |
| authoritative baseline | 权威基线 | resume/read 得到的持久历史 snapshot |
| rebase | 重设基线 | 用新 snapshot 替换旧推导状态，再选择性重放事件 |
| bounded buffer | 有界缓冲区 | 容量受限的客户端事件重放队列 |
| event routing | 事件路由 | 按 thread 与 connection 把通知送到正确客户端 |
| item identity | item 身份 | 跨 started/delta/completed 保持不变的 item ID |
| timestamp unit | 时间戳单位 | Turn 用秒，item lifecycle 用毫秒 |

---

## 60. 本篇结论

Codex 客户端状态不是从一条“最终 JSON”直接得到，也不是只靠不断 append 通知得到。它由两条互补路径共同建立：

```text
Core lifecycle events
→ App-server live projection
→ started / delta / completed 实时体验

Rollout items
→ ThreadHistoryBuilder
→ read/resume 的可恢复 snapshot
```

两条路径依靠以下不变量汇合：

- thread、turn、item 使用稳定 ID；
- started/completed 按 ID upsert；
- delta 是临时视图，completed item 是校正点；
- `itemsView` 明确说明 payload 完整度；
- error notification 与 Turn terminal state 分离；
- listener 先更新状态，再发送通知；
- running resume 在 listener 中原子排序历史、active snapshot 与订阅；
- bounded client buffer 只保留真正可重放的语义事件。

下一篇计划精读 **Thread watch、订阅与多客户端路由**：多个连接怎样订阅同一 thread，活跃状态、权限等待和 thread unload 又怎样互相协调。

