# TUI 与客户端状态管理：事件怎样变成你看到的界面

> 本章解决的问题：模型已经输出了一段内容，为什么终端里会一个字一个字地出现？工具执行、状态栏和“停止”按钮为什么能同时更新？切换到另一条 Agent thread 后，界面又怎样恢复成对应的内容？

阅读本章前，建议先看：

- [09：App-server v2 边界](09-app-server-v2.md)
- [20：端到端数据实例](20-end-to-end-data-example.md)
- [23：事件、Telemetry 与可观测性](23-events-telemetry-and-observability.md)
- [27：异步任务、Channel 与取消](27-async-tasks-channels-and-cancellation.md)

---

## 1. 先说结论：TUI 不是 Agent 本身

TUI 是 terminal user interface，也就是运行在终端里的用户界面。

它主要负责：

- 接收键盘输入；
- 把用户意图提交给 app-server；
- 接收 app-server 的通知和请求；
- 把通知转换成聊天记录、状态栏、审批窗口等界面状态；
- 在合适时机重新绘制终端画面。

它不负责：

- 自己调用模型；
- 自己决定使用哪个工具；
- 自己执行 Agent 主循环；
- 把界面上的文字直接当成 Core 的真实状态。

最值得先记住的一句话是：

> TUI 是 Core 运行状态的一种客户端投影，不是运行状态的最终所有者。

“投影”可以想象成仪表盘。汽车发动机拥有真实转速，仪表盘只是接收信号后显示转速。仪表盘可能暂时还没刷新，甚至断线，但这不等于发动机状态由仪表盘决定。

---

## 2. 本章贯穿案例

用户输入：

> 请搜索配置加载代码，并告诉我配置优先级。

一次简化事件序列可能是：

```text
TurnStarted
ItemStarted(UserMessage)
ItemCompleted(UserMessage)
AgentMessageDelta("我先搜索")
ItemStarted(CommandExecution)
CommandExecutionOutputDelta("codex-rs/...")
ItemCompleted(CommandExecution)
AgentMessageDelta("配置按")
AgentMessageDelta("以下顺序")
ItemCompleted(AgentMessage，携带完整最终文本)
TurnCompleted
```

TUI 需要把这些不同语义的事件变成不同界面变化：

- `TurnStarted`：进入运行状态，显示 Working；
- `AgentMessageDelta`：把新到的一小段文字加入流式预览；
- `ItemStarted(CommandExecution)`：新增一个正在执行的命令单元格；
- 命令输出 delta：更新该单元格中的实时输出；
- `ItemCompleted`：把临时单元格收束成最终记录；
- `TurnCompleted`：清理运行态、停止动画，并回到可继续输入的状态。

这不是“收到一个字符串然后打印”。它更像一个小型状态机。

---

## 3. 当前 TUI 为什么先连接 app-server

在当前源码基线中，TUI 通过 app-server client 工作。`codex-rs/tui/src/lib.rs` 中的 `AppServerTarget` 支持几种目标：

- `Embedded`：app-server 嵌在当前进程的运行路径中；
- `LocalDaemon`：连接本机 daemon；
- `Remote`：连接远端 app-server。

从 TUI 角度看，它们都提供相近的客户端边界：

```text
TUI
 │  request / notification / server request
 ▼
App-server client
 │
 ▼
App-server
 │  Core Op / EventMsg
 ▼
codex-core Session
```

这样做的价值是：

1. TUI 不需要分别理解本地和远端 Core 的内部对象；
2. 桌面客户端、IDE 或其他客户端可以共享 app-server v2 协议；
3. UI 依赖的是相对稳定的公开数据形状，而不是 Core 内部每个实现细节；
4. thread、turn、item 的身份可以跨进程保持一致。

“Embedded”并不表示 TUI 绕过 app-server 直接调用 Core。它表示 app-server 的部署位置更靠近 TUI，但协议边界仍然有意义。

---

## 4. Core Event 与 ServerNotification 不是同一个类型

Core 内部会产生 `EventMsg`。app-server 在 `codex-rs/app-server/src/bespoke_event_handling.rs` 中把它转换成客户端可理解的 `ServerNotification`。

例如：

```text
Core EventMsg::TurnStarted
        │
        ▼
app-server 读取 thread state，构造 v2 Turn
        │
        ▼
ServerNotification::TurnStarted
        │
        ▼
TUI
```

为什么不把 Core 类型原样全部暴露？

- Core 事件服务于内部执行；
- app-server API 服务于跨进程客户端；
- 客户端需要稳定、可序列化并带 thread/turn/item 身份的数据；
- app-server 有时还要去重、补充时间戳或把内部状态转换成客户端视图。

例如命令执行开始事件可能来自不止一条内部路径。app-server 会使用 thread summary 中的集合避免向客户端重复发送同一个 canonical start notification。

这里的 canonical 可以理解为“被协议认可的权威版本”。

---

## 5. 一条通知进入 TUI 后的总路线

当前主链路可以概括为：

```text
app-server 发出 ServerNotification
              │
              ▼
AppServerSession::next_event()
              │
              ▼
App::handle_app_server_event
              │
              ▼
判断通知属于哪个 thread
              │
       ┌──────┴──────┐
       ▼             ▼
当前可见 thread    非当前 thread
进入活跃 channel    写入该 thread 的 buffer
       │             │
       ▼             └── 等切换过去时 replay
ChatWidget::handle_server_notification
              │
              ▼
修改 transcript / status / running command 等 UI state
              │
              ▼
请求 redraw
              │
              ▼
Ratatui 根据最新状态渲染下一帧
```

要读懂这条路径，需要分清三层状态：

| 层 | 例子 | 主要所有者 |
|---|---|---|
| Runtime 真实状态 | turn 是否运行、工具是否完成 | Core / app-server |
| 客户端事件缓存 | 某 thread 收到过哪些通知、当前 turn ID | `ThreadEventStore` |
| 当前画面状态 | 流式文字、历史 cell、状态栏、弹窗 | `ChatWidget` |

它们有关联，但不是同一个对象。

---

## 6. 主事件循环：界面为什么不会只顾着模型输出

`codex-rs/tui/src/app.rs` 的主循环使用 `select!` 同时等待多类输入：

- 内部 `AppEvent`；
- 当前 thread channel 的事件；
- 终端键盘和窗口事件；
- app-server 事件流。

教学版伪代码如下：

```rust
loop {
    tokio::select! {
        event = app_event_rx.recv() => handle_app_event(event).await,
        event = active_thread_rx.recv() => handle_thread_event(event).await,
        event = terminal.next() => handle_terminal_event(event).await,
        event = app_server.next_event() => handle_app_server_event(event).await,
    }
}
```

这解释了为什么模型正在流式输出时，你仍可以：

- 滚动；
- 输入文字；
- 点击或按键中断；
- 收到审批窗口；
- 看到命令输出继续更新。

它们不是在一个长函数中严格排队执行，而是不同事件源都能唤醒主循环。

但这不代表处理顺序可以随便变化。每个事件携带的 thread、turn 和 item ID，仍用于判断它属于哪条生命周期。

---

## 7. `AppEvent`：TUI 内部的意图和后台结果

app-server 事件是“后端告诉 TUI 发生了什么”。`AppEvent` 则是 TUI 自己内部使用的事件总线。

在 `codex-rs/tui/src/app_event.rs` 中，可以看到例如：

- 打开 Agent picker；
- 切换 active thread；
- 提交 thread operation；
- 新建、恢复或 fork session；
- 导出 transcript；
- 合并已经完成的流式消息；
- 请求退出应用。

为什么 widget 不直接到处调用 `App` 的方法？

因为 UI 中的子组件很多。让它们发送一个 `AppEvent`，可以避免把整个应用对象或大量 channel 一层层传下去。

可以这样区分：

```text
ServerNotification：后端事实进入客户端
AppEvent：客户端内部意图或异步工作结果
Terminal event：用户键盘、鼠标和窗口变化
```

三者最终都由应用主循环协调。

---

## 8. 为什么每条 thread 都需要自己的事件缓存

多 Agent 或 side conversation 存在时，后台 thread 仍可能继续运行。屏幕一次却只能主要展示一条 thread。

如果只保留“当前屏幕看到的事件”，会发生：

1. 你正在看主 thread；
2. 子 Agent 在后台完成一条命令并回答；
3. 因为它不在屏幕上，事件被丢弃；
4. 你切过去后，看不到刚才发生的过程。

因此 `App` 为 thread 维护 `ThreadEventChannel`，其中共享一个 `ThreadEventStore`。

`ThreadEventStore` 保存的主要内容包括：

- 当前 session 的客户端视图；
- 已知 turns；
- 有界的 buffered events；
- 正在等待的交互请求状态；
- active turn ID；
- pending interrupt turn ID；
- 该 thread 的 composer/input state；
- 这条 thread 当前是否 active。

当前常量 `THREAD_EVENT_CHANNEL_CAPACITY` 为 32768。这里记住“有明确上限”比死记数字更重要；数字可能随版本调整。

---

## 9. 活跃 thread 与后台 thread 的不同处理

### 9.1 当前可见 thread

通知会先更新 store，再尝试发送到 active receiver，由主循环交给当前 `ChatWidget` 即时处理。

### 9.2 后台 thread

通知只进入对应 store 的 buffer，不会修改当前屏幕的 `ChatWidget`。

### 9.3 为什么不能让所有通知都直接修改 ChatWidget

假设屏幕正在显示 thread A，thread B 收到 `TurnCompleted`。如果不先按 ID 路由：

- A 的 Working 状态可能被错误清掉；
- B 的回答可能出现在 A 的 transcript；
- A 的审批窗口可能被 B 的完成事件关闭。

`codex-rs/tui/src/app/app_server_event_targets.rs` 会从不同 `ServerNotification` 中提取 `thread_id`，并把通知归类为：

- 特定 thread；
- thread ID 非法；
- app-scoped；
- global。

这一步是在保护 UI 状态隔离。

---

## 10. 切换 thread 时发生的不是“换一个标题”

切换前，TUI 需要保存当前 thread 的输入状态，例如尚未提交的草稿。切换后，则要为目标 thread 重建可见状态。

简化流程是：

```text
保存 thread A 的 composer state
把 A 标记为 inactive
取出 thread B 的 snapshot
重建或重新配置 ChatWidget
回放 B 的 buffered events
恢复 B 的 composer state
把 B 标记为 active
继续消费 B 的 live events
```

这说明两个容易混淆的概念：

- live handling：事件刚到达时立即处理；
- replay：切换或恢复时，把已保存事件重新应用到新的 UI 状态。

`ChatWidget::handle_server_notification` 会收到一个可选的 `ReplayKind`，从而区分实时通知、thread snapshot 回放和初始历史恢复。某些动画、错误提示或副作用在 replay 时不应再次发生。

例如，恢复旧 thread 时看到过去的 `TurnStarted`，不能让 TUI误以为现在刚启动了一轮新的工作。

---

## 11. `ChatWidget` 是聊天界面的状态机

`codex-rs/tui/src/chatwidget.rs` 对 `ChatWidget` 的注释已经明确了职责：它维护由协议事件派生的每 session UI 状态，并把按键转换成用户意图；它不运行 Agent 本身。

它拥有的状态很多，可以先按用途分组，不要逐字段硬背。

### 11.1 Transcript 状态

- 当前正在变化的 active cell；
- 最近完成的 Agent Markdown；
- 流式 plan buffer；
- 本轮是否发生过工具工作；
- 最近 plan 进度。

### 11.2 Turn 生命周期状态

- Core 是否认为 Agent turn 正在运行；
- 最近 turn ID；
- 哪些 turn 因预算停止；
- turn 的开始时间；
- 是否应阻止系统休眠。

### 11.3 流式状态

- 普通回答的 `StreamController`；
- Plan 的 `PlanStreamController`；
- 等待合并的 stream 数量；
- 自适应分块策略。

### 11.4 工具和交互状态

- 正在运行的命令；
- MCP startup 状态；
- 审批或 request-user-input 弹窗；
- 活跃 hook；
- 等待中断处理的 UI 事件。

### 11.5 展示状态

- 状态栏标题和详情；
- terminal title；
- token usage；
- rate limit；
- thread 名称和当前 Agent 标签；
- composer 草稿和输入队列。

理解这些分组后，你会发现它不是一个“消息数组”，而是多个相互配合的小状态机。

---

## 12. `handle_server_notification`：通知到 UI 行为的总路由

`codex-rs/tui/src/chatwidget/protocol.rs` 中的 `handle_server_notification` 是非常适合学习的入口。

它会根据 notification 变体调用不同处理方法：

| Notification | 典型 UI 行为 |
|---|---|
| `TurnStarted` | 记录 turn ID，进入 running 状态 |
| `TurnCompleted` | 根据终态完成或中断本轮 UI |
| `ItemStarted` | 创建命令、工具、文件修改等活动单元格 |
| `ItemCompleted` | 把对应 item 收束成最终状态 |
| `AgentMessageDelta` | 追加回答增量 |
| `PlanDelta` | 追加 Plan 增量 |
| `CommandExecutionOutputDelta` | 更新命令实时输出 |
| `TurnPlanUpdated` | 更新结构化 plan 清单 |
| `Error` | 显示可重试流错误或终止错误 |
| `Warning` | 插入警告信息 |
| `McpServerStatusUpdated` | 更新 MCP 启动状态 |
| `ThreadTokenUsageUpdated` | 更新 token 使用显示 |

这里的 `match` 不只是数据解析，它把协议语义翻译成客户端状态转移。

阅读某个 UI bug 时，可以先找对应 notification，再从这个 match 进入，而不是从渲染函数向后盲猜。

---

## 13. 流式文本不是每来一个 token 就直接 `print`

模型传来的 `AgentMessageDelta` 会进入 `on_agent_message_delta`，再交给 streaming controller。

为什么不直接把每个 delta 写进终端？

- delta 可能很小，逐个重绘会抖动且浪费资源；
- Markdown 结构可能尚未完整，例如代码围栏还没闭合；
- 终端宽度改变时，需要重新换行；
- 当前行可能只是临时 tail，还不适合写入稳定 scrollback；
- plan、reasoning 和 final answer 有不同展示规则。

因此可以把流程理解成：

```text
收到 delta
   │
   ▼
追加到 StreamController 队列
   │
   ▼
commit tick 按节奏提交可展示内容
   │
   ├── 稳定行进入 transcript/history cell
   └── 未完成尾部作为 live tail 展示
   │
   ▼
请求下一帧 redraw
```

“流式”描述的是数据逐步到达；“动画”描述的是客户端用怎样的节奏显示。两者相关，但不是同一个机制。

---

## 14. Delta 是预览，completed item 是权威结果

这是本章最重要的实现原则之一。

假设完整回答是：

```text
配置优先级是：命令行、项目配置、用户配置。
```

客户端可能收到：

```text
delta 1: "配置优先级是："
delta 2: "命令行、项目"
delta 3: 因拥塞丢失
delta 4: "用户配置。"
```

如果最终只拼 delta，屏幕会少一段。为此，`codex-rs/tui/src/chatwidget/streaming.rs` 在 assistant message 完成时使用 completed item 中的完整文本进行最终合并。

源码注释明确表达：item completion 是权威来源，因此即使饱和传输丢掉 delta，也不能让最终 transcript 被截断。

```text
增量事件：低延迟预览
最终 item：完整性对账
```

这是一种常见的实时系统设计：快速路径允许暂时不完整，最终路径负责校正。

### 14.1 为什么还要比较 streamed 与 completed 文本

如果两者相同，只需把临时流式 cells 合并成一个稳定 Markdown cell。

如果不同，TUI 需要以 completed message 为准，并触发必要的 scrollback reflow。否则窗口调整宽度或复制最后回答时，可能仍使用残缺文本。

---

## 15. Live tail、history cell 与 scrollback

这三个词可以用正在输入的一段 Markdown 理解。

### Live tail

仍在变化的最后一部分内容。例如模型刚输出：

````text
  ```rust
fn main(
````

语法块还没完成，它适合作为临时尾部显示。

### History cell

已经稳定、可进入 transcript 的显示单元，例如一条用户消息、完成的命令、最终 Agent Markdown 或警告。

### Scrollback

终端中已经提交、用户可以向上查看的历史区域。内容进入 scrollback 后，再修改和重新排版的成本更高。

所以 TUI 会区分：

```text
正在变化的 tail → 完成后 finalize → 稳定 history cell → 必要时重新排版 scrollback
```

如果把所有东西一到达就永久提交，Markdown 未完成、窗口 resize 和最终文本校正都会变得很难处理。

---

## 16. TurnStarted 和 TurnCompleted 如何控制状态栏

`TurnLifecycleState` 中的 `agent_turn_running` 记录客户端所理解的运行状态。

收到 `TurnStarted` 时，TUI 通常会：

- 保存最后 turn ID；
- 把 turn 标记为 running；
- 显示或恢复状态指示器；
- 更新 terminal title；
- 根据配置阻止系统在任务执行时休眠。

收到 `TurnCompleted` 时，TUI 需要：

- 根据 completed、failed、interrupted 等状态选择展示；
- finalize 活跃命令或 stream；
- 清理 running 状态；
- 停止动画；
- 恢复 composer 可交互状态；
- 避免同一错误显示两次。

状态栏不是简单的 boolean 映射。工具执行、等待审批、Reasoning、重试和后台 terminal 等阶段可能使用不同标题。`StatusState` 保存当前状态标题、详情、terminal title 类别和等待恢复的状态。

---

## 17. 为什么终态事件到达时，流式动画可能还没播完

后端已经完成，不代表客户端的展示队列已经清空。

例如：

1. 模型很快产生完整回答；
2. delta 已经进入客户端队列；
3. TUI 为了平滑显示，commit tick 还在逐段提交；
4. `TurnCompleted` 已经到达。

如果此时立刻清掉所有流式状态，回答尾部可能消失。因此 `ChatWidget` 有 `task_complete_pending` 一类协调状态：先记住后端已完成，等 stream 收束后再完成相应 UI 收尾。

这说明：

> 后端生命周期完成，与客户端动画完成，是两个不同时间点。

最终 UI 必须尊重后端终态，但也要正确排空已经接收的展示数据。

---

## 18. 命令执行为什么也是一个状态机

命令显示通常经历：

```text
ItemStarted(CommandExecution)
        │
        ▼
创建 running command / active cell
        │
        ├── OutputDelta：追加 stdout/stderr
        ├── TerminalInteraction：记录 stdin 交互
        └── 状态栏显示执行中
        │
        ▼
ItemCompleted(CommandExecution)
        │
        ▼
写入退出码、耗时和最终状态，转成稳定 history cell
```

开始和完成依靠相同 item ID 关联。没有 ID，只凭“最近一个命令”猜测，在并行工具执行时就会出错。

这也解释了为什么 UI 测试不能只断言出现一段命令文字，还应该检查：

- running cell 是否创建；
- delta 是否进入正确 item；
- completion 是否结束同一个 item；
- 中断时是否标记为失败或中止；
- 之后的回答是否按正确顺序出现。

---

## 19. Replay 为什么不能机械重放所有副作用

假设历史中有一个旧的审批请求，用户当时已经点击批准。恢复 thread 时如果机械回放 request，审批窗口会再次弹出。

因此 `ThreadEventStore` 还维护 pending interactive replay 状态，用来判断：

- 哪些审批仍待回答；
- 哪些 request 已经 resolved；
- 哪些 input request 已经完成；
- buffer 淘汰事件后是否需要更新 pending 状态。

Snapshot 只应重放仍然有意义的交互请求。

同样，一些大体积或只适合实时处理的通知不会复制到每条 thread 的 replay buffer，例如部分 raw response、realtime audio 和过程型 delta。源码会明确筛掉 TUI 回放无法使用或成本过高的事件。

Replay 的目标不是复刻每一帧动画，而是重建正确、可继续交互的客户端状态。

---

## 20. 缓冲区满了意味着什么

活跃 thread 的 channel 是有界的。发送时会先尝试 `try_send`：

- 有空间：立即进入队列；
- 已满：启动异步发送，等待队列出现空间；
- 已关闭：记录警告。

Thread store 自己的 buffer 超过容量时，会从前面淘汰最旧事件。

这体现两个不同目标：

- active channel 尽量保持 live 处理顺序，并通过等待形成背压；
- snapshot buffer 必须有硬上限，不能为了无限回放而无限占用内存。

因此 UI 系统需要最终权威事件来修复可能不完整的实时预览。第 14 节的 completed-item 对账并不是孤立技巧，而是整个有界事件系统的一部分。

---

## 21. Redraw：修改状态不等于立即写终端

事件处理器通常先改变内存中的 UI state，再请求 redraw。下一帧渲染时，Ratatui 根据最新状态生成画面。

```text
event
  │
  ▼
mutate UI state
  │
  ▼
FrameRequester / request_redraw
  │
  ▼
render latest state
```

这样做有几个好处：

- 多个很近的状态变化可以在一帧中体现；
- 渲染逻辑从业务事件处理逻辑中分离；
- resize 后可以用同一份状态重新布局；
- 测试可以先构造状态，再检查完整终端快照。

因此排查“状态已经更新但界面没变化”时，要分别检查：

1. notification 是否到达；
2. handler 是否改变状态；
3. 是否请求了 redraw；
4. render 是否读取了正确字段；
5. 新画面是否被其他 overlay 或 active cell 覆盖。

---

## 22. 界面状态为什么不能反向当作后端真相

下面几种情况下，UI 都可能暂时与真实状态不同：

- app-server 通知正在队列中；
- 当前查看的是另一条 thread；
- delta 到达但尚未进入 commit tick；
- turn 已完成但流式展示还在收尾；
- app-server 断线；
- replay 只重建稳定状态，不重播所有过程动画；
- 某个非权威增量被有界传输丢弃。

所以代码不能因为“status row 当前写着 Working”就推断 Core 一定还有 active turn。真正需要执行中断时，应使用由 app-server/Core 提供或按 thread store 跟踪的 turn ID。

同理，UI 隐藏了一个审批窗口，不一定意味着审批已经得到答复；需要查看 server request resolved 状态。

---

## 23. 一次完整回答的 UI 时间线

把前面的知识合在一起：

```text
1. 用户在 composer 提交消息
   └── TUI 形成 AppCommand，交给 app-server

2. Core 开始 turn
   └── app-server 投影为 TurnStarted

3. TUI 路由到正确 thread
   └── ChatWidget 进入 running，显示状态行

4. 模型产生 AgentMessageDelta
   └── StreamController 缓冲并按 commit tick 展示

5. 模型请求命令工具
   └── ItemStarted 创建 running command cell

6. 命令输出逐步到达
   └── 依据 item ID 更新对应 cell

7. 命令完成
   └── ItemCompleted 将 cell finalize

8. 最终 Agent message 完成
   └── completed item 与 delta 对账，生成稳定 Markdown cell

9. TurnCompleted 到达
   └── 清理运行态、动画、状态栏和临时资源

10. Ratatui 绘制空闲界面
    └── 用户可以开始下一轮
```

如果用户中途切换到另一条 thread，第 3–9 步仍可写入原 thread 的 store；切回时再通过 snapshot 和 replay 重建显示。

---

## 24. 三类常见 UI Bug 怎样定位

### 24.1 “后台已经完成，界面还一直 Working”

按顺序检查：

1. Core 是否发出 `TurnComplete`；
2. app-server 是否转换成 `TurnCompletedNotification`；
3. notification 的 thread ID 是否正确；
4. 是否进入对应 `ThreadEventStore`；
5. active receiver 是否消费；
6. `ChatWidget` 是否执行 turn completion handler；
7. stream 是否仍有 pending completion；
8. 状态栏是否被其他活动状态重新覆盖。

### 24.2 “流式回答缺了一段”

检查：

1. delta 是否在传输或 channel 拥塞时丢失；
2. `ItemCompleted(AgentMessage)` 是否携带完整文本；
3. `finalize_completed_assistant_message` 是否执行；
4. completed 文本是否被用于 consolidation；
5. scrollback reflow 后最终 cell 是否正确。

### 24.3 “切换 Agent 后看到另一个 Agent 的内容”

检查：

1. notification 的 thread ID 提取；
2. primary、side、subagent 的路由分支；
3. active thread ID 与 `ChatWidget.thread_id`；
4. 切换时旧 receiver 是否归还；
5. snapshot 是否来自目标 store；
6. replay notification 是否误改了父 thread 状态。

这种分层检查比直接在渲染代码里寻找字符串更有效。

---

## 25. TUI 测试为什么大量使用 Snapshot

终端 UI 的正确性通常不是某一个字段，而是完整组合：

- 文本顺序；
- 缩进和换行；
- 命令 cell 的状态；
- 状态栏是否出现；
- spinner、提示和 footer 的位置；
- 中断或完成后的最终画面。

因此 TUI 使用 `insta` snapshot test 保存完整渲染结果。修改用户可见 UI 时，仓库规则要求相应 snapshot 覆盖。

不过 snapshot 只能告诉你“画面变了”，不能单独证明事件路由正确。好的测试通常还会：

1. 构造明确的 `ServerNotification` 序列；
2. 送入 `ChatWidget` 或 `App`；
3. 断言关键状态；
4. 再对最终画面做 snapshot。

特别值得阅读的案例是 `codex-rs/tui/src/chatwidget/tests/app_server.rs` 中关于：

- deltas 与 completed message；
- 丢失 delta 后由 completion 修复；
- turn started/completed；
- command output delta；
- replay 与中断。

---

## 26. 阅读 TUI 源码的推荐顺序

TUI 文件很多，不建议从巨大的 `chatwidget.rs` 第一行读到最后一行。

### 第一遍：只看跨层路线

1. `codex-rs/tui/src/lib.rs`：TUI 如何取得 app-server client；
2. `codex-rs/tui/src/app.rs`：主事件循环同时等待哪些输入；
3. `codex-rs/tui/src/app/app_server_events.rs`：app-server 事件总入口；
4. `codex-rs/tui/src/app/app_server_event_targets.rs`：通知怎样找到 thread。

### 第二遍：看 thread 隔离与回放

1. `codex-rs/tui/src/app/thread_events.rs`：store、buffer、snapshot；
2. `codex-rs/tui/src/app/thread_routing.rs`：active/inactive 路由与 replay。

### 第三遍：看聊天状态机

1. `codex-rs/tui/src/chatwidget/protocol.rs`：notification 总 match；
2. `codex-rs/tui/src/chatwidget/turn_lifecycle.rs`：运行状态；
3. `codex-rs/tui/src/chatwidget/streaming.rs`：增量与最终对账；
4. `codex-rs/tui/src/chatwidget/transcript.rs`：稳定记录与临时状态；
5. `codex-rs/tui/src/chatwidget/status_state.rs`：状态栏和 terminal title。

### 第四遍：用测试反向验证

1. 找一个具体 snapshot；
2. 找生成它的测试；
3. 看测试发送了哪些 notification；
4. 再回到对应 handler。

这样每次只追一条事件，不会淹没在所有 UI 功能中。

---

## 27. 常见误解

### 误解一：TUI 直接订阅 Core 的所有内部事件

当前主路径通过 app-server client 和 v2 notification 边界工作。app-server 会转换、补充或过滤内部事件。

### 误解二：收到 delta 就永久追加到聊天历史

Delta 先进入流式控制器；完成时还要与权威 item 对账，并合并为稳定 cell。

### 误解三：切换 thread 只是把 `thread_id` 改一下

还涉及保存输入、切换 receiver、取得 snapshot、重建 widget、回放事件和恢复交互状态。

### 误解四：Replay 应重新播放原来的每个动画

Replay 的目标是恢复正确状态，不是复刻历史每一帧。已经解决的审批和过程型大 payload 不应盲目重放。

### 误解五：`TurnCompleted` 到达就能立刻清空所有 stream

客户端展示队列可能尚未收束，需要协调后端终态与动画完成。

### 误解六：UI 显示 Working，所以后端一定正在运行

UI 是派生投影，可能延迟、断线或处于 replay。需要用 thread/turn 的权威状态确认。

### 误解七：Snapshot test 通过就证明状态机完全正确

快照只覆盖测试构造的最终画面；还应验证事件身份、顺序、终态和内部关键状态。

---

## 28. 本章名词表

完整总表见 [课程术语表](glossary.md)。

| 名词 | 代码中的常见写法 | 通俗解释 |
|---|---|---|
| TUI | terminal user interface | 在终端中运行的文字用户界面 |
| Projection | projection | 把后端真实状态转换成某个客户端需要的视图 |
| App-server client | `AppServerSession` | TUI 用来请求 app-server 并接收事件的客户端边界 |
| Notification | `ServerNotification` | 后端主动告诉客户端“某件事已发生”的单向消息 |
| Server request | `ServerRequest` | app-server 向客户端发起、需要客户端回答的请求，例如审批 |
| App event | `AppEvent` | TUI 内部表达用户意图或后台结果的事件 |
| Routing | routing | 根据 thread ID 和事件类型送到正确状态对象 |
| Active thread | `active_thread_id` | 当前主要显示并实时消费事件的 thread |
| Event store | `ThreadEventStore` | 保存某条 thread 的客户端事件、turn 和交互状态 |
| Buffer | `VecDeque` / channel | 暂存尚未显示或等待处理的事件 |
| Replay | `ReplayKind` | 用保存的事件重建 UI 状态，不代表重新执行 Agent |
| Snapshot | `ThreadEventSnapshot` | 某条 thread 在一个时刻可用于恢复 UI 的状态集合 |
| ChatWidget | `ChatWidget` | 把协议通知转换成聊天界面状态的主要状态机 |
| Transcript | `TranscriptState` | 用户、Agent、工具和提示组成的可见聊天记录状态 |
| Cell | `HistoryCell` | transcript 中一个可独立渲染的显示单元 |
| Delta | `AgentMessageDelta` | 完整内容逐步到达的一小段增量 |
| Stream controller | `StreamController` | 缓冲并按显示节奏提交流式内容的控制器 |
| Commit tick | commit tick | 定期把已准备好的流式内容提交到可见界面的节拍 |
| Live tail | active stream tail | 仍可能变化、尚未成为稳定历史的末尾内容 |
| Finalize | `finalize()` | 把临时运行或流式状态收束为最终稳定状态 |
| Consolidation | consolidate | 把多段临时流式 cell 合并成一个稳定 Markdown cell |
| Canonical | canonical item/event | 协议认可、用于最终对账的权威表示 |
| Scrollback | terminal scrollback | 已提交到终端、可以向上滚动查看的历史区域 |
| Reflow | reflow | 终端宽度或内容变化后重新计算换行和布局 |
| Redraw | `request_redraw()` | 请求用最新 UI state 绘制下一帧 |
| Frame | frame | 一次完整的终端画面渲染结果 |
| Overlay | overlay | 覆盖在主聊天区域上的弹窗、选择器或临时层 |
| Composer | composer | 用户输入和编辑下一条消息的区域 |
| Terminal title | terminal title | 终端窗口或标签页标题中的简短运行状态 |

### 代码单词和短语拆解

- `terminal`：终端。
- `interface`：界面或接口，要结合上下文判断。
- `widget`：界面组件。
- `transcript`：谈话文字记录。
- `render`：把状态转换成可显示画面。
- `frame`：一帧完整画面。
- `route`：路由、送往正确目标。
- `active` / `inactive`：当前活跃 / 当前不活跃。
- `buffer`：缓冲、暂存。
- `replay`：回放保存的事件以恢复状态。
- `delta`：相对已有内容新增加的一小部分。
- `tail`：尾部，通常指仍在增长的最后一段。
- `commit`：提交为更稳定的状态。
- `finalize`：完成收尾并固定结果。
- `consolidate`：把多块内容合并整理成一个整体。
- `authoritative`：权威的，发生冲突时以它为准。
- `canonical`：规范化后的标准版本。
- `scrollback`：终端向上滚动可看的历史。
- `reflow`：因宽度变化而重新排版。
- `composer`：消息输入编辑区。
- `overlay`：覆盖在主界面上的临时层。

---

## 29. 自测题

1. 为什么说 TUI 是 Core 状态的投影，而不是状态最终所有者？
2. Embedded app-server 是否意味着 TUI 绕过 app-server 协议直接操作 Core？
3. Core `EventMsg` 为什么要转换成 `ServerNotification`？
4. 主事件循环同时等待哪几类输入？
5. `AppEvent` 与 `ServerNotification` 的来源和用途有什么不同？
6. 为什么后台 thread 的事件不能直接修改当前 `ChatWidget`？
7. 切换 thread 时为什么需要 snapshot 和 replay？
8. Replay 为什么不能重新弹出已经回答过的审批？
9. 为什么 delta 只适合实时预览，而 completed item 要作为最终权威来源？
10. live tail 与稳定 history cell 有什么区别？
11. 为什么 `TurnCompleted` 到达时，UI 可能还不能立刻结束所有动画？
12. 遇到“一直 Working”时，应当沿哪几层逐步检查？

---

## 30. 源码检查点

建议按下面顺序亲自验证：

1. `codex-rs/tui/src/lib.rs`
   - 找 `AppServerTarget` 和 `start_app_server`；
   - 确认 Embedded、LocalDaemon、Remote 三种连接位置。
2. `codex-rs/tui/src/app.rs`
   - 找主 `select!`；
   - 列出它同时等待的四类主要事件源；
   - 找 `THREAD_EVENT_CHANNEL_CAPACITY`。
3. `codex-rs/tui/src/app/app_server_events.rs`
   - 找 `handle_app_server_event`；
   - 看 notification、server request、lagged 和 disconnected 怎样分流。
4. `codex-rs/tui/src/app/app_server_event_targets.rs`
   - 找 `server_notification_thread_target`；
   - 观察不同 notification 的 thread ID 位于哪里。
5. `codex-rs/tui/src/app/thread_events.rs`
   - 找 `ThreadEventStore`、`push_notification_inner` 和 `snapshot`；
   - 注意哪些大事件不会进入 replay buffer。
6. `codex-rs/tui/src/app/thread_routing.rs`
   - 找 `enqueue_thread_notification`、`handle_thread_event_now` 和 `handle_thread_event_replay`；
   - 比较 active 与 inactive thread 的处理差别。
7. `codex-rs/tui/src/chatwidget/protocol.rs`
   - 从 `handle_server_notification` 的 match 选择一个事件向下追踪。
8. `codex-rs/tui/src/chatwidget/streaming.rs`
   - 找 `on_agent_message_delta` 和 `finalize_completed_assistant_message`；
   - 找“item completion is authoritative”的源码注释。
9. `codex-rs/tui/src/chatwidget/turn_lifecycle.rs`
   - 看 running 状态怎样开始、结束和恢复。
10. `codex-rs/app-server/src/bespoke_event_handling.rs`
    - 比较 Core `TurnStarted`、`ItemCompleted`、`TurnComplete` 到 v2 notification 的转换。
11. `codex-rs/tui/src/chatwidget/tests/app_server.rs`
    - 搜索 dropped message deltas、turn completion 和 command output delta 测试。

---

## 31. 一句话总结

TUI 的核心工作不是“打印模型输出”，而是把 app-server 的 thread-scoped 事件安全路由、缓存和回放，再由 `ChatWidget` 将增量预览、权威完成事件、工具生命周期和交互状态合成为可重绘的客户端投影。
