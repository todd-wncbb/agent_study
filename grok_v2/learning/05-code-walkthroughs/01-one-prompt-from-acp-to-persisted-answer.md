# Walkthrough：一条 Prompt 如何从 ACP 请求变成可重放的流式回答

本文不再按子系统分别介绍概念，而是固定一个具体场景，沿真实源码逐步走完：

> 一个已经创建好的普通本地 session 收到纯文本 prompt；当前没有其他 turn；模型直接返回文本，不调用工具、不触发 compaction、不发生重试；客户端持续收到流式文本，最后收到 turn terminal；对话和通知均可从磁盘恢复。

这个“最短成功路径”很重要。大型系统的完整分支太多，如果一开始把图片、slash command、tool call、401、自动压缩、Goal continuation 和取消全部混进来，就很难分清主干与恢复分支。本文先把主干钉牢，再在末尾说明各复杂分支从哪里岔出。

本文核对基线为根目录 `SOURCE_REV` 记录的提交。行号用于帮助第一次定位，长期维护时以类型和函数名为准。

---

## 1. 最终调用链

先看全链路，再逐段打开代码：

```mermaid
sequenceDiagram
    participant Client as "ACP Client / TUI"
    participant Agent as "MvpAgent::prompt"
    participant Cmd as "SessionCommand channel"
    participant Actor as "SessionActor run loop"
    participant Turn as "handle_prompt / process_conversation_turn"
    participant Sampler as "SamplerActor"
    participant Drain as "SamplingEvent drainer"
    participant Events as "ReplayBuffer / notification pipeline"
    participant Chat as "ChatStateActor"
    participant Disk as "Persistence worker"

    Client->>Agent: session/prompt(PromptRequest)
    Agent->>Agent: 校验 session、model、meta
    Agent->>Cmd: SessionCommand::Prompt + oneshot
    Cmd->>Actor: serialized receive
    Actor->>Actor: queue_input()
    Actor->>Turn: maybe_start_running_task()
    Turn->>Chat: increment prompt index + push User
    Turn->>Disk: user echo / content chunk / optional flush barrier
    Turn->>Sampler: submit_and_collect(request_id, request)
    Sampler-->>Drain: StreamStarted / ChannelToken / Completed
    Drain->>Events: AgentMessageChunk
    Events->>Disk: persisted ACP updates
    Events-->>Client: session/update chunks
    Sampler-->>Turn: collected ConversationResponse
    Turn->>Turn: await stream-drain barrier
    Turn->>Chat: record usage + Assistant item
    Turn->>Disk: flush conversation + end rewind point
    Turn-->>Actor: PromptTurnResult via completion channel
    Actor->>Events: flush ReplayBuffer
    Actor->>Disk: durable TurnCompleted
    Actor-->>Agent: resolve prompt oneshot
    Agent-->>Client: PromptResponse(stopReason, meta)
```

关键不是函数数量，而是四条不同的数据轨道：

| 轨道 | 载荷 | 同步方式 |
| --- | --- | --- |
| 请求轨 | `PromptRequest` → `SessionCommand::Prompt` | mpsc + oneshot |
| Turn 状态轨 | queue、running task、conversation、prompt index | SessionActor + ChatStateActor |
| 流式事件轨 | text/reasoning/tool deltas | sampler event channel + drainer |
| 持久化轨 | chat history、updates、summary、rewind point | persistence channel/worker |

只追一条轨道会产生误判。例如 `MvpAgent::prompt()` 尚未返回，不代表客户端没有看到文字；文字走流式事件轨，RPC response 要等 terminal 后才返回。

---

## 2. 阅读前准备

建议同时打开这些文件：

```text
crates/codegen/xai-grok-shell/src/
├── agent/mvp_agent/acp_agent.rs
└── session/
    ├── commands.rs
    ├── handle.rs
    ├── acp_session.rs
    └── acp_session_impl/
        ├── run_loop.rs
        ├── prompt_queue.rs
        ├── turn.rs
        ├── sampler_turn.rs
        ├── tool_calls.rs
        ├── updates.rs
        └── turn_end.rs
```

Sampler 的内部实现位于 `xai-grok-sampler`，Chat 状态位于 `xai-chat-state`。第一遍先把它们视为有明确输入输出的 actor，等主链走通后再下钻。

快速建立跳转点：

```sh
rg -n "async fn prompt\(" \
  crates/codegen/xai-grok-shell/src/agent/mvp_agent/acp_agent.rs

rg -n "SessionCommand::Prompt|queue_input|handle_prompt" \
  crates/codegen/xai-grok-shell/src/session

rg -n "run_turn_via_sampler|handle_sampling_event|handle_completion" \
  crates/codegen/xai-grok-shell/src/session
```

---

## 3. 第 1 步：ACP 请求进入 `MvpAgent::prompt`

入口位于：

```text
xai-grok-shell/src/agent/mvp_agent/acp_agent.rs
Agent trait impl → MvpAgent::prompt()
```

当前基线约在第 939 行。

输入是 `acp::PromptRequest`，包含：

- `session_id`；
- `prompt: Vec<ContentBlock>`；
- 可选 `_meta`，如 mode、promptId、sendNow、outputSchema、toolOverrides；
- 调用方 trace context。

### 3.1 这里先做控制面检查

`MvpAgent::prompt()` 首先处理：

1. 从 `_meta` 链接 traceparent；
2. 等待目标 session 完成 load；
3. 拒绝未知 session ID；
4. 检查 model allowlist；
5. 处理“恢复的旧 model 已不可用”状态；
6. 获取该 session 的 dispatch lock；
7. 解析 prompt mode 和 prompt ID；
8. 分配外层 turn number；
9. 准备 trace/upload metadata；
10. 验证 output schema 和 tool override。

这层不直接改 conversation，也不直接调用模型。它是 ACP 控制面适配器。

### 3.2 为什么需要 dispatch lock

同一 session 可能从多个客户端/入口同时提交 prompt。Dispatch lock 保护“读取当前 mode、生成请求上下文、发送 command”这一段，使同一 session 的外部请求投递顺序可解释。

它不是整个 turn 的锁。发送 `SessionCommand::Prompt` 后就释放，后续排序由 SessionActor queue 负责。如果一直持有到模型返回，第二条 prompt 连“进入队列”都做不到。

### 3.3 Prompt ID 与 turn number 不是同一个概念

- `prompt_id` 是字符串 identity，可由客户端 `_meta.promptId` 提供，否则生成 UUID；
- turn number 是 agent 侧分配的观测/上传序号；
- `prompt_index` 是 chat state 中的零基对话位置。

正常单客户端场景里它们同步向前，看起来很像；取消、queued prompt、synthetic prompt、resume 后则不能互换。

---

## 4. 第 2 步：请求被转换成 `SessionCommand::Prompt`

命令定义位于 `session/commands.rs` 的 `SessionCommand::Prompt`。它携带的不是只有文字：

```text
prompt_id
prompt_blocks
prompt_mode
artifact_upload_ctx
client_identifier / screen_mode
verbatim
traceparent
json_schema
send_now
admission
tool_overrides_update
respond_to
persist_ack
parsed_prompt_tx
```

### 4.1 两个容易忽略的 oneshot

`respond_to` 是主完成通道。SessionActor 最终将 `PromptTurnResult` 发回，`MvpAgent::prompt()` 才能构造 ACP `PromptResponse`。

`persist_ack` 是更早的可选 barrier：调用者可要求“User message 已进入 chat history 且 persistence flush 完成”后收到确认，而不必等模型结束。普通 ACP prompt 此处传 `None`，但 subagent/trace 等路径可能依赖它。

`parsed_prompt_tx` 则把解析、Skill 展开和大 prompt 截断后的结果送回 metadata upload task，避免外层重新实现一遍 prompt parser。

### 4.2 为什么发送 command 后立即释放 dispatch guard

当前基线约在 `acp_agent.rs:1270` 发送命令，随后 `drop(dispatch_guard)`，再 await `rx`。

这形成清晰边界：

```text
dispatch lock 负责可靠投递顺序
SessionActor 负责执行顺序
oneshot 负责把单个执行结果送回原 RPC
```

---

## 5. 第 3 步：SessionActor 接收并准入

`SessionActor` 不通过 `&mut self` 被所有并发任务随意调用。外部持有 `SessionHandle`，通过 `cmd_tx` 发消息；actor 的 serialized run loop 消费消息。

对应分支位于：

```text
session/acp_session_impl/run_loop.rs
SessionCommand::Prompt match arm（当前基线约第 597 行）
```

### 5.1 Actor 先判断来源

`PromptOrigin::from_prompt_id()` 区分：

- 真实用户 prompt；
- task/subagent/workflow completion；
- notification drain；
- goal summary/nudge；
- scheduler wake；
- plan resume。

来源会影响 queue 可见性、user echo、历史记录、通知 suppression 和优先级。不能看到 `Prompt` variant 就假设一定是人类刚输入的文字。

### 5.2 Prefix 必须 ready

Session 初始化时 prefix 可以后台构建。第一条 Prompt 到达后 `ensure_prefix_ready()` 是门槛，防止模型请求在 system prompt、AGENTS.md、Skills 等静态前缀尚未准备好时开始。

### 5.3 真实用户输入解除若干抑制

非 synthetic prompt 会：

- 清除 task-wake suppression；
- 恢复 notification delivery；
- 增加 `user_input_generation`；
- 使正在等待的 laziness classifier 观察到 generation 已过期并退出。

这是“用户重新参与”的控制信号，不只是内容消息。

### 5.4 进入 queue，而不是直接执行

Run loop 调用 `queue_input(...)`，然后：

- 如 `send_now` 要抢占，取消当前 turn；
- 调用 `maybe_start_running_task()`；
- 只有 session 当前空闲时，队首 prompt 才被提升为 running task。

Actor 因而可以继续接收取消、编辑队列、模式切换等命令，不会被一次长模型请求完全堵死。

---

## 6. 第 4 步：`queue_input` 建立队列事实

实现位于 `acp_session_impl/prompt_queue.rs`。

### 6.1 Fast prompt history 在排队时就写

真实用户文字会立即追加到 per-CWD prompt history，而不是等 `handle_prompt()`：prompt 即使后来在队列中被取消，也仍属于用户输入历史，可用于历史选择器。

这和 conversation history 不同：conversation 只在 prompt 真正开始处理后加入 User item。

### 6.2 User prompt 会淘汰尚未运行的 synthetic wakes

真实用户输入优先于等待中的 task/subagent auto-wake。`sweep_pending_inputs()` 会保护真正的 running prompt ID，只删除其他符合条件的 synthetic items。

代码刻意不用“队首就是 running”作为唯一依据，因为 completion/cancel 交错时，队列 front 和其他快捷状态可能有短暂不同步窗口。

### 6.3 Queue metadata 与执行载荷分开

`InputItem` 同时带：

- 执行所需的 prompt blocks、oneshot、trace 等；
- 用户可见 queue metadata：ID、version、owner、kind、text；
- origin 和 send-now 状态。

Synthetic prompt 的 `queue_meta` 为 `None`，因此能执行却不一定显示在共享用户队列中。

### 6.4 当前场景的结果

因为我们假设 session 空闲：

```text
pending_inputs: [] -> [当前 InputItem]
running_task: None -> AgentTask(prompt_id)
current_prompt_id: None -> 当前 prompt_id
```

随后 LocalSet task 调用 `handle_prompt()`，结果通过 `completion_tx` 回到 actor run loop，而不是直接操作队列完成态。

---

## 7. 第 5 步：`handle_prompt` 打开 Turn

主函数位于 `acp_session_impl/turn.rs`，当前基线约第 241 行。

### 7.1 进入函数后的 guard

首先建立：

- `session.handle_prompt` tracing span；
- prompt length；
- active skill reset；
- turn/session active RAII guards；
- extension `on_turn_start` 回调；
- regeneration 或 edit-and-retry 信号。

RAII guard 很关键：无论正常 return、`?` 提前退出还是 future 被取消，Drop 都能清除 active 标志，避免 session 永久显示忙碌。

### 7.2 早期分支

在进入普通模型路径前，可能岔到：

- direct Bash command；
- builtin slash command；
- Skill slash invocation；
- Goal/workflow action。

本文场景是普通文字，所以继续主干。调试“为什么没调用模型”时，这些 early return 是第一批断点。

### 7.3 TurnStarted 不等于模型已发请求

函数调用 `events.begin_turn()`，产生内部 `TurnStarted`、observability event、before-turn hook 和 telemetry。此时还没解析完整 prompt，也没提交 sampler。

看到 TurnStarted 日志只能证明 turn 被提升，不能证明 provider 收到请求。

---

## 8. 第 6 步：分配 Prompt Index 并打开 Rewind Point

当前关键顺序在 `turn.rs` 约 518–543 行：

1. 读取当前 `prompt_index`；
2. 将其写入本轮 notification metadata；
3. `increment_prompt_index()`；
4. 缓存 prompt text；
5. 更新工具资源中的 prompt index；
6. `file_state_tracker.begin_prompt(current_prompt_index)`。

因此 checkpoint N 表示 prompt N 执行前的文件状态。先读取 N、再递增全局计数，保证该轮的 User/Assistant/tool items 都能标记同一个 N。

### 8.1 User echo 先进入更新轨

原始 `ContentBlock` 被包装为 `UserMessageChunk`：

- 普通用户 prompt：转发给客户端并持久化；
- 某些 synthetic origin：只持久化，隐藏 scrollback echo。

这一步保存的是客户端提交的块；稍后的 conversation User item 保存的是 parser 组装后的模型输入。二者不是完全相同的数据形状。

---

## 9. 第 7 步：解析和组装模型看到的 User Message

`parse_prompt_with_skills()` 返回：

- `context`；
- `query`；
- `skill_information`；
- images；
- harness compatibility 标记。

随后还会：

1. 恢复 orphan image placeholders；
2. 规范化图片；
3. 合并 context/query/skill information；
4. 必要时把超大 prompt 落到本地文件并截断模型内文本；
5. 通过 `parsed_prompt_tx` 报告真正使用的文本；
6. 注入 MCP、日期、plan、task completion、workflow 等 reminders。

### 9.1 “用户输入”有三种表示

| 表示 | 用途 |
| --- | --- |
| 原始 `ContentBlock` | ACP echo、附件和客户端语义 |
| Parsed/assembled prompt | metadata、截断判断、模型输入构造 |
| `ConversationItem::User` | ChatState 与采样 history |

调试 prompt 内容时必须先问“我看的哪一种”。

### 9.2 Prompt parser 的错误发生得很早

解析失败会直接返回 `acp::Error`。这时可能已经：

- increment prompt index；
- 打开 file rewind point；
- 发出 User echo；

但还没调用 provider。Turn-end/finalization 路径必须能处理这种半打开状态。

---

## 10. 第 8 步：User Item 进入 ChatState 和持久化

组装后的文字按 `PromptOrigin` 构造成不同 `ConversationItem` variant。普通输入使用 `ConversationItem::user(user_message)`，并设置 `prompt_index`；图片 URL 也在这里挂载。

### 10.1 普通路径

没有 `persist_ack` 时：

```text
chat_state_handle.push_user_message(user_chat)
```

消息进入 ChatStateActor，由其状态与持久化协议继续处理。

### 10.2 Barrier 路径

有 `persist_ack` 时：

1. `push_user_message_and_ack()` 确认 actor 已接收；
2. 向 persistence worker 发送 `FlushAndAck`；
3. 等磁盘 flush 回应；
4. 才 resolve 调用方的 ack oneshot。

所以 persist ack 的语义是“推理前可从持久化层看到该 User message”，不是简单的“send channel 成功”。

### 10.3 UserPromptSubmit hook 在消息之后

源码随后 dispatch `UserPromptSubmit` hook。Hook 看到的是处理后的 prompt text，并且此时 User item 已进入 conversation。若 hook 阻塞或失败，历史中仍可能已有本轮 User message。

---

## 11. 第 9 步：进入 Conversation Turn 循环

`handle_prompt()` 调用：

```text
process_conversation_turn_with_recovery()
  -> process_conversation_turn()
```

外层 recovery wrapper 负责特定失败的自动恢复；内层函数是 agentic loop。

### 11.1 即使“模型只回答文字”，它仍运行 agentic loop

`process_conversation_turn()` 每一轮都可能得到：

- 普通 assistant text，无 tool call：结束；
- assistant + tool calls：执行工具，把 results 加入 conversation，再次 sample；
- 需要 compact：压缩后重新提交；
- auth 恢复成功：刷新后重新提交；
- refusal、schema 不满足、stationarity、max turns：进入各自终态。

本文场景只经过一次 sample，但代码不能写成“一次 HTTP 请求函数”，因为同一主干必须承载多轮工具循环。

### 11.2 构建 Tool Specs 和请求

每次 sample 前会：

- 等待/快照 MCP 能力；
- 取 builtin tool definitions；
- 根据 plan mode 过滤工具；
- 应用 hosted-tool overrides；
- 加入 structured output tool/schema；
- 从 ChatState 读取 conversation；
- 估算 context 与触发 preflight compaction；
- 构造 `ConversationRequest`。

即使模型最终没有调用工具，tool definitions 仍可能随请求发送。

---

## 12. 第 10 步：Sampler Actor 接管网络采样

入口是 `sampler_turn.rs::run_turn_via_sampler()`，当前基线约第 1142 行。

### 12.1 提交前配置刷新

`prepare_sampler_for_turn()` 会处理：

- provider/session token freshness；
- 当前完整 `SamplingConfig`；
- auth gate；
- retry/doom-loop 配置；
- sampler actor config update。

SessionActor 不自己执行 HTTP streaming，而是调用：

```text
sampler_handle.submit_and_collect(request_id, request)
```

### 12.2 为什么既“collect”又有独立流事件

同一次采样产生两个消费结果：

- turn future 等待完整 `ConversationResponse`，用于 tool calls、usage、assistant history 和终态；
- sampler event channel 实时发送 token/delta，供客户端尽快显示。

如果只有 collect，客户端必须等完整响应；如果只有 token events，turn loop 又要自己重组 canonical response。两条路径各司其职。

### 12.3 三类返回

`run_turn_via_sampler()` 返回：

- `Response(response, metrics)`；
- `CompactAndResubmit`；
- `RefreshAuthAndResubmit`；
- 或 terminal `acp::Error`。

后两种是“本次 sample 没完成，但整个 turn 仍可继续”，不能立即当作 Prompt RPC 失败。

---

## 13. 第 11 步：独立 Drainer 转发流式 Token

Session spawn 时创建 sampler event channel，并启动 drainer：

```text
while let Some(event) = sampler_event_rx.recv().await {
    drainer_session.handle_sampling_event(event).await;
}
```

映射函数在 `tool_calls.rs::handle_sampling_event()`，当前基线约第 2616 行。

### 13.1 文本 token

`SamplingEvent::ChannelToken { channel: Text, ... }`：

1. 追加到 `streaming_turn_capture`；
2. 发出 `PhaseChanged::StreamingText`；
3. 转成 `acp::SessionUpdate::AgentMessageChunk`；
4. 调用 `send_update(..., chunk_index)`。

Reasoning channel 类似，但转成 `AgentThoughtChunk`。

### 13.2 `send_update` 不直接写 socket

`updates.rs::send_update()`：

1. 关闭 rewind window；
2. 补 totalTokens、eventId、timestamps、promptId、chunkId；
3. 包成 `SessionNotification`；
4. 发到 actor 的 `event_tx`。

Actor run loop 中的 ReplayBuffer 合并/节流高频 chunk，再调用 `emit_buffered()`。

### 13.3 ACP chunk 会持久化，tool delta chunk 不一定

`emit_buffered()` 按协议类型分流：

- ACP text/thought chunk → `emit_notification_direct()` → persistence + gateway；
- 高频 xAI `ToolCallDeltaChunk` → gateway only，不逐块持久化。

Tool delta 的 canonical 持久化来源是组装完成后的 ACP ToolCall，避免把大量不完整 JSON argument fragments 当恢复事实。

### 13.4 第一条出站 update 关闭 rewind window

`send_update_full()` 首先调用 `close_rewind_window()`。含义是：turn 一旦对外产生 prompt-scoped 结果，就不能再把它当作“尚未真正开始、可无痕撤回”的状态。

---

## 14. 第 12 步：Stream-drain Barrier 保证事件顺序

这里是整条链路最值得学习的并发细节。

Sampler actor 可能按如下时序工作：

```text
event channel: token A -> token B -> Completed
collect future:                         returns response
```

`submit_and_collect()` 返回完整 response 时，独立 drainer 未必已经处理完 token B。若 turn loop立刻发 ToolCall/ResponseCompleted/terminal，客户端可能看到：

```text
token A -> terminal -> token B
```

客户端按 event ID 去重时，晚到的 B 甚至会被当成旧事件丢掉。

### 14.1 Barrier 实现

`run_turn_via_sampler()` 提交前创建 oneshot，将 sender 放进 `turn_stream_drained`。Drainer 收到 `SamplingEvent::Completed` 时取出 sender 并 send。Collect future 返回后，turn loop等待 receiver，最多 5 秒。

因此正常顺序变成：

```text
drainer 已处理先前所有 FIFO events
  -> 处理 Completed
  -> resolve barrier
  -> run_turn_via_sampler 返回 canonical response
  -> turn loop 发后续 canonical events
```

### 14.2 超时是降级，不是永久卡死

5 秒未收到 barrier 会 warning 并继续。系统选择“可能本轮 event order 不完美”，而不是因 drainer bug/关闭让整个 session 永久挂死。

这是典型的有界协调：正常路径强保证，异常路径保活并留下诊断信号。

---

## 15. 第 13 步：Canonical Response 回到 Agentic Loop

本文场景返回没有 tool calls 的 assistant response。

Turn loop 会：

1. `record_response_token_usage()`；
2. 把模型 usage 写入 session、prompt、signals 等 ledger；
3. 提取 assistant `ConversationItem`；
4. `record_assistant_response()` 推入 ChatState；
5. 发 ResponseCompleted 等扩展事件；
6. 检查 schema/todo/stop gate；
7. 因无 tool call，结束 agentic loop。

### 15.1 Streaming text 与 Assistant history 是两份职责

流式 chunk 用于实时 UI 和事件回放；最终 Assistant item 是 conversation 的 canonical 模型消息。不能用“已经看到完整流式文字”推断 ChatState 一定已经写入 Assistant，也不能在恢复 conversation 时简单拼所有 UI chunks 代替 canonical history。

### 15.2 Usage 在完整 response 后结算

Token chunk 本身通常不知道最终完整 usage。`record_response_token_usage()` 使用 provider response 的 usage 更新：

- task output budget；
- total token state；
- last-turn usage；
- per-model usage/cost；
- signals。

若强预算场景下 response 缺 usage，会 fail closed 标记 incomplete，而不是把未知成本当 0。

---

## 16. 第 14 步：`handle_prompt` 关闭 Turn

Agentic loop 返回后，`handle_prompt()` 按 outcome 分发：

- Completed；
- StationarityEnded；
- Cancelled；
- MaxTurnsReached；
- Error。

并发送对应：

- internal TurnEnded event；
- observability bridge event；
- after-turn hook；
- telemetry；
- StopFailure hook（适用时）。

### 16.1 先 flush，再 finalize file snapshot

在函数尾部，当前基线约第 1159 行：

```text
flush_to_disk().await
file_state_tracker.end_prompt(fs, current_prompt_index).await
```

随后处理 chat state flush、trace artifact、usage freeze 等收尾。

若本轮没有工具修改文件，rewind point 仍可存在，只是 snapshot 数为 0。Conversation rewind 仍需要这个 prompt index。

### 16.2 返回的是内部 TurnOutcome 映射结果

`handle_prompt()` 最终产生 `PromptTurnResult`，但它不会直接 resolve ACP RPC。Running task 将结果发到 `completion_tx`，统一由 actor run loop 完成队列出队和 terminal 排序。

---

## 17. 第 15 步：Completion 回到 Actor 主循环

`run_loop.rs` 的 `completion_rx.recv()` arm 当前基线约第 405 行。

处理顺序：

1. flush ReplayBuffer 中剩余 chunk；
2. 计算 Goal degradation/continuation 信息；
3. `handle_completion(prompt_id, result)`；
4. drain 迟到的 monitor events；
5. 处理 goal continuation；
6. 把 stranded interjection 转回队列；
7. 启动下一个 pending prompt。

第一步是另一道 ordering barrier：即使高频 buffer 还留有最后一小段文本，也要在 durable terminal 前强制排空。

---

## 18. 第 16 步：`handle_completion` 结束队列所有权

实现位于 `turn_end.rs`，当前基线约第 180 行。

### 18.1 只允许 owner completion 修改队列

函数检查队首 `prompt_id` 是否匹配：

- 匹配：pop front、通过 `input.respond_to` 回传结果；
- 不匹配：视为 stale completion，记录 warning，不发 terminal；
- 只有匹配 running/owned 的 completion 才清空 `running_task`。

取消路径可能已经先终结同一 prompt，所以迟到 completion 不能再次弹队列或覆盖新 turn 状态。

### 18.2 两种“完成通知”

系统暂时同时存在：

- fire-and-forget `prompt_complete`；
- persisted + replayable `XaiSessionUpdate::TurnCompleted`。

后者是可靠 rail：重连 viewer 可以从 `updates.jsonl` replay terminal，不会永远停在 Waiting 状态。

### 18.3 Terminal 必须在最后一个 delta 之后

`handle_completion` 前已经 flush ReplayBuffer；`emit_turn_completed()` 再通过持久化通知管线写 terminal。因此 `updates.jsonl` 的有效顺序是：

```text
...最后一个 AgentMessageChunk
ResponseCompleted 等 canonical update
TurnCompleted
```

这也是 session/load 能恢复完整 UI 状态的关键。

---

## 19. 第 17 步：ACP `PromptResponse` 返回客户端

`handle_completion()` resolve 的正是最初 `SessionCommand::Prompt.respond_to` oneshot。`MvpAgent::prompt()` 的 `rx.await` 醒来后：

- 读取 last-turn usage；
- 附加 applied tool overrides；
- 映射 stop reason；
- 构造 response metadata；
- 更新 roster activity；
- 返回 `acp::PromptResponse`。

所以客户端观察到的顺序通常是：

```text
多个 session/update notifications
持久化 TurnCompleted notification
session/prompt RPC response
```

RPC response 是请求完成确认，不是承载回答全文的消息。

---

## 20. 状态变化总表

| 时点 | Queue state | ChatState | File tracker | 客户端 | 磁盘 |
| --- | --- | --- | --- | --- | --- |
| ACP 刚进入 | 未变 | 未变 | 未变 | 等 RPC | 未变 |
| `queue_input` 后 | pending/running 已占位 | 未加 User | 未打开 | queue update | prompt fast history |
| `handle_prompt` 开始 | running | prompt index 将递增 | begin point | TurnStarted/User echo | updates 开始 |
| User push 后 | running | 有 User | point open | 已看到 echo | chat history 可异步写入 |
| streaming 中 | running | 尚未必有最终 Assistant | 捕获工具文件变化 | 连续 chunks | ACP chunks 持久化 |
| canonical response | running | usage + Assistant | point 仍打开 | ResponseCompleted | canonical history/update |
| handle_prompt 尾部 | running | flushed | end point | TurnEnded events | flush/rewind point |
| handle_completion | dequeued/idle | last usage 可读 | finalized | TurnCompleted | durable terminal |
| ACP 返回 | idle 或下一条 running | 稳定 | 稳定 | PromptResponse | 稳定 |

---

## 21. 复杂分支从哪里岔出

### 21.1 Slash command

`handle_prompt()` 早期 `slash_commands::resolve()`。某些 builtin 直接产生 host turn 输出并 return，完全不进入 sampler。

### 21.2 Skill invocation

同一 resolver 展开 Skill，设置 active skill、记录 telemetry，把 skill information 合并进 parser 输入，然后回到普通模型主干。

### 21.3 Direct Bash

`extract_bash_command()` 命中后走 `handle_direct_bash_command()`，跳过普通 agentic sampling。

### 21.4 Tool call

`process_conversation_turn()` 从 canonical Assistant item 读 tool calls，进入 `execute_tool_calls()`；results 推入 ChatState，然后 loop 回到下一次 sampler request。

### 21.5 Context overflow

Sampler failure 映射为 `CompactAndResubmit`，或 preflight 直接触发 compaction；完成后外层 loop 重新构造 conversation request。

### 21.6 401

严格识别 401 和 endpoint trust；刷新成功返回 `RefreshAuthAndResubmit`，更新 credentials 后只重试受控次数。403 不被误判为 token refresh。

### 21.7 Cancel / send-now

新 prompt 可插入 next position并请求取消 running turn。Cancel path 自己拥有 terminal；旧 task 的迟到 completion 会被 owner check 丢弃。

### 21.8 Goal continuation / Stop gate

一次 `process_conversation_turn` Completed 后，Goal 或 Stop hook 可能注入新的 synthetic User item并继续 loop，所以一个 ACP prompt 可以包含多个模型 round。

---

## 22. 调试断点与日志

### 22.1 请求没有进入 session

断点/日志：

```text
MvpAgent::prompt
session_handle_waiting_for_load
SessionCommand::Prompt send
run_loop Prompt arm
```

检查 unknown session、model unavailable、dispatch channel closed。

### 22.2 Prompt 一直 queued

检查：

```text
queue_input
State::running_prompt_id
maybe_start_running_task
current_prompt_id
running_task
```

重点找 stale running slot、未完成 cancel、队首 ID 不一致。

### 22.3 有 User echo，但没有模型请求

从 `handle_prompt` 往后查：

- parser error；
- image normalize/transcribe；
- blocking hook；
- prefix/MCP wait；
- preflight compaction；
- auth refresh；
- early slash/direct Bash return。

### 22.4 Provider 已返回，但 UI 缺最后几个字

检查：

- `handle_sampling_event(ChannelToken)`；
- `chunk_index`；
- event ID 单调性；
- `turn_stream_drained` barrier；
- ReplayBuffer completion flush；
- 客户端 dedup 水位。

### 22.5 UI 完成了，但恢复后卡在 Waiting

检查 `handle_completion` 是否拥有 completion、是否调用 `emit_turn_completed()`，以及 terminal 是否实际写入 `updates.jsonl`。

### 22.6 Conversation 有 User 没 Assistant

这可以是合法错误/取消中间态。检查：

- sampler terminal error；
- `record_assistant_response()` 是否执行；
- cancel 时 streaming capture 如何补齐；
- history repair 是否需要 synthetic tool result；
- turn terminal 的 error/stop reason。

### 22.7 推荐日志过滤

```sh
RUST_LOG='sampling_log=debug,acp_event=info,xai_grok_shell::session=debug' \
  cargo run -p xai-grok-pager-bin
```

实际 binary/package 名按当前开发入口调整。重点关联字段：

```text
session_id
prompt_id
turn_number
prompt_index
request_id
eventId
chunkId
model_id
```

---

## 23. 错误与安全边界

### 23.1 不要记录原始 prompt

Telemetry 的 `PromptSubmitted` 明确把 `prompt_text` 设为 `None`。调试时也应优先记录长度、block count、ID 和分类，不要把用户源码、凭据或图片 base64 打进通用日志。

### 23.2 `outputSchema` 在进入 actor 前校验

非 object schema 直接返回 invalid params，避免把无效结构送进 sampler或 structured-output tool。Tool overrides 同样先 typed parse。

### 23.3 Session ID 只用于查 registry

ACP 传来的 session ID 不能直接拼文件路径。`MvpAgent` 先取得已注册 `SessionHandle`，持久化路径由 session info/helper 解析。

### 23.4 Channel send 成功不等于磁盘成功

- `cmd_tx.send`：actor 接受了命令；
- `push_user_message`：chat actor 接受了状态变化；
- `PersistenceMsg::Update`：worker 接受了落盘请求；
- `FlushAndAck`：此前写入完成的 barrier。

需要强持久化语义的路径必须显式使用 ack，不能把前三级 send 当 fsync。

### 23.5 Stale completion 必须 fail closed

未知 prompt completion 不应修改 queue、running task 或发第二个 terminal。否则旧取消 task 能破坏已经提升的新 turn。

### 23.6 流式内容不是唯一事实源

客户端显示可消费 chunks，conversation 恢复应依赖 canonical chat history；tool arguments 恢复依赖组装后的 ToolCall，而不是零碎 delta。不同数据用途需要不同持久化粒度。

---

## 24. 修改这条主链时的检查清单

### ACP 入口

- dispatch guard 是否在 await 完整 turn 前释放？
- 新 `_meta` 字段是否 typed validate？
- traceparent 是否跨 command hop 继续关联？
- unknown session/model gate 是否返回明确错误？

### Queue

- running prompt 是否始终保留队列所有权？
- synthetic prompt 是否避免污染用户可见 queue？
- send-now 是否保持多条抢占请求 FIFO？
- 移除 queued prompt 时是否 resolve 原 oneshot？
- stale completion 是否无法 pop 新 prompt？

### Turn start

- prompt index 是否只增一次？
- User echo 和 canonical User item 是否各自语义正确？
- rewind point 是否在工具写文件前打开？
- parser early return 是否仍触发必要收尾？
- RAII active guard 是否覆盖所有 return？

### Sampling

- config/auth 是否在每轮提交前更新？
- streaming events 与 collected response 是否使用同一个 request ID？
- drain barrier 是否在 canonical tool/terminal events 前？
- barrier 是否有界，异常时有日志？
- retry/compact 是否不会重复记录 User prompt？

### Updates

- event ID 是否按交付顺序生成？
- high-frequency update 是否走 ReplayBuffer？
- direct event 是否可能越过 queued chunks？
- 哪些事件需要持久化，哪些只是瞬时 delta？
- terminal 是否严格位于最后一个 delta 后？

### Completion

- completion ownership 是否基于 prompt ID？
- queue pop、running clear、response oneshot 是否一致？
- cancel 与 normal completion 是否只能有一个 terminal owner？
- PromptResponse usage 是否来自冻结后的本轮 ledger？
- 下一条 prompt 是否只启动一次？

---

## 25. 验证实验

### 实验一：最短文本路径

使用可控 sampler stub 返回三个 text chunks：`A`、`B`、`C`，然后 Completed。记录：

```text
eventId(chunk A) < eventId(chunk B) < eventId(chunk C)
                 < eventId(TurnCompleted)
```

再确认 ACP PromptResponse 在 terminal 后 resolve。

### 实验二：人为放慢 drainer

在测试 drainer 中让最后一块 sleep，而 collect future 先返回。验证 `run_turn_via_sampler()` 被 barrier 阻塞，直到 drainer 处理 Completed。

### 实验三：Barrier 超时

不发送 Completed event，只让 collect 返回。使用 paused Tokio time 或缩短测试 timeout，验证：

- turn 最终继续；
- `turn_stream_drained` sender 被清理；
- 有 ordering warning；
- session 不永久挂起。

### 实验四：Stale completion

1. Prompt A running；
2. cancel A 并提升 B；
3. 让 A 的 task 迟到返回；
4. 验证 B 不被 pop，A 不产生第二个 TurnCompleted。

### 实验五：Persist ack

发送带 `persist_ack` 的内部 Prompt command，在 ack 收到后立即读取 `chat_history.jsonl`，确认 User item 已存在；此时 sampler 可以仍未完成。

### 实验六：Replay terminal

Prompt 完成后断开客户端，再 load/replay session：

- 文本 chunks 顺序一致；
- TurnCompleted 存在；
- UI 不停留 Waiting；
- canonical Assistant history 只有一份。

### 推荐定向测试入口

```sh
# Queue、stream event 与 terminal ordering 主要位于 shell lib tests
cargo test -p xai-grok-shell --lib prompt_queue
cargo test -p xai-grok-shell --lib replay_buffer_send_update_tests
cargo test -p xai-grok-shell --lib turn_completion_emit_tests

# Sampler actor 的 submit/stream/retry 行为
cargo test -p xai-grok-sampler --lib

# Chat state 的 message、usage 与 persistence 行为
cargo test -p xai-chat-state --lib
```

若 shell 的整库 test target 被无关 `#[cfg(test)]` 模块编译错误阻塞，应记录准确文件和错误，并单独运行可编译的 integration test或下层 crate 测试，不能把“没跑到”写成“通过”。

### 本次基线的实际验证记录

本文写作时实际执行结果：

| 命令 | 结果 |
| --- | --- |
| `cargo test -p xai-grok-sampler --lib` | 169 passed |
| `cargo test -p xai-chat-state --lib` | 350 passed |
| `cargo test -p xai-grok-shell --lib turn_completion_emit_tests` | 未运行到测试体；lib-test 编译被既有错误阻塞 |

Shell blocker 位于：

```text
crates/codegen/xai-grok-shell/src/session/acp_session_tests/
tool_layer_images_bridge_tests.rs:15
```

测试代码调用 `base64::engine::general_purpose::STANDARD.encode(buf)`，但没有把提供 `encode()` 的 `base64::Engine` trait 引入作用域，触发 `E0599`。它与本文 terminal ordering 逻辑无关，但 Cargo 必须先编译整个 lib-test target，因此过滤测试也无法绕过。修复该独立编译问题后，应补跑上面的三组 shell filters。

---

## 26. 自测题

1. 为什么 `MvpAgent::prompt()` 不直接调用 sampler？
2. Dispatch lock 为什么不能持有到整个 turn 结束？
3. Prompt ID、turn number、prompt index 有什么不同？
4. `respond_to`、`persist_ack`、`parsed_prompt_tx` 三个 oneshot 分别解决什么问题？
5. 为什么真实 prompt 在 queued 时就写 fast history，而 conversation User item 要等 turn 开始？
6. Synthetic prompt 为什么能执行但不一定出现在共享 queue？
7. `TurnStarted` 能否证明 provider 已收到请求？
8. Rewind point 为什么在 prompt parser 后续逻辑之前打开？
9. 原始 ContentBlock、assembled prompt、ConversationItem::User 有何区别？
10. Persist ack 为什么还要经过 `FlushAndAck`？
11. 没有 tool call 时，为什么仍然运行 agentic loop？
12. Sampler 为什么同时返回 collected response 和 streaming events？
13. Drainer 与 turn future 并发会产生什么排序风险？
14. `turn_stream_drained` barrier 为什么等到 Completed event，而不是 FirstToken？
15. 为什么 barrier 必须有 timeout？
16. Streaming chunks 和 canonical Assistant item 各自是谁的事实源？
17. ToolCallDelta 为什么不必逐块持久化？
18. 为什么 completion arm 还要再 flush ReplayBuffer？
19. Stale completion 为什么不能发 TurnCompleted？
20. 客户端何时看到回答文字，何时收到 PromptResponse？
21. User 已在 history、Assistant 不在 history 是否一定是 bug？
22. 调试缺字问题时，哪几个 ID/序号必须放在同一时间线上比较？

---

## 27. 本篇术语表

| 名词 | 白话解释 | 在本篇中的具体含义 |
| --- | --- | --- |
| ACP | Agent Client Protocol | Client 调用 session/prompt、接收 session/update 的协议面 |
| PromptRequest | 一次 ACP prompt 请求 | 包含 session ID、ContentBlocks 和 `_meta` |
| PromptResponse | Prompt RPC 的最终确认 | 含 stop reason/meta，不承载流式回答全文 |
| ContentBlock | 多模态输入块 | Text、Image 等 ACP 输入单元 |
| `_meta` | 协议扩展字段 | promptId、mode、sendNow、schema 等非核心字段 |
| control plane | 控制面 | 校验、路由、模式和会话选择，不直接做模型推理 |
| dispatch lock | 投递锁 | 保证同一 session 外部 Prompt command 的有序提交 |
| SessionHandle | Actor 的可克隆代理 | 持有 command sender 和只读/共享 handles |
| SessionActor | 会话行为所有者 | 串行处理 command、queue、completion 和事件交付 |
| actor run loop | Actor 的消息循环 | `select!` command/event/completion 等来源 |
| SessionCommand | 发给 SessionActor 的内部命令 | `Prompt` 是本文主入口消息 |
| mpsc | 多生产者单消费者 channel | 多处发送 command，单 actor 顺序接收 |
| oneshot | 只返回一次值的 channel | 把某个 prompt 的结果或 ack 送回调用者 |
| queue admission | 队列准入 | 决定输入是否进入 pending/running 状态 |
| synthetic prompt | 系统生成的 prompt | task wake、goal summary、notification drain 等 |
| PromptOrigin | prompt 来源分类 | 控制 echo、队列可见性、优先级和 conversation variant |
| running task | 当前执行的 turn task | 由 queue front 提升，完成后统一回到 completion channel |
| LocalSet | 运行 `!Send` future 的 Tokio 本地任务集合 | SessionActor 和 Rc/RefCell 相关任务的执行环境 |
| RAII guard | 离开作用域自动清理的守卫 | 即使取消/错误也清除 turn-active 状态 |
| prompt index | ChatState 的零基 prompt 坐标 | Rewind、conversation item 和 file tracker 的共同索引 |
| user echo | 向客户端显示原始输入 | `UserMessageChunk`，区别于模型看到的 User item |
| prompt parser | 输入解析器 | 拆 context/query/images/skills 并组装模型文本 |
| canonical | 作为恢复/语义事实的完整表示 | 最终 Assistant、ToolCall、chat history 等 |
| agentic loop | 模型与工具可反复交互的循环 | sample → tool → result → 再 sample，直到终态 |
| sampler | 模型采样执行器 | 负责 provider 请求、stream、retry、完整 response 收集 |
| SamplingEvent | sampler 的流式事件 | token、tool delta、retry、Completed 等 |
| drainer | 持续消费 event channel 的任务 | 将 SamplingEvent 映射为 ACP/xAI updates |
| collected response | 完整模型响应 | Turn loop 用于 usage、tool calls 和 canonical history |
| streaming chunk | 增量文本/推理片段 | Client 可即时渲染的 AgentMessageChunk/ThoughtChunk |
| chunk index | 模型流片段序号 | 写入 notification 的 chunkId，辅助排序诊断 |
| event ID | 出站事件唯一单调 ID | Client 去重和重放水位的重要依据 |
| ReplayBuffer | 高频事件缓冲器 | 合并/节流 chunks，并在 terminal 前强制 flush |
| drain barrier | 等 drainer 追上完整响应的同步点 | 防 canonical 后续事件越过尾部 token |
| bounded wait | 有超时的等待 | 正常保证顺序，异常时避免永久阻塞 |
| ChatStateActor | Conversation/usage 等状态所有者 | 接收 User、Assistant、ToolResult 和 flush 命令 |
| persistence worker | 磁盘写入任务 | 消费 PersistenceMsg，维护 history/updates 等文件 |
| flush barrier | 确认此前持久化写入完成 | `FlushAndAck`，语义强于 channel send 成功 |
| terminal | 一轮结束事件 | Durable `TurnCompleted` 或 RPC stop reason |
| stale completion | 已取消/失去所有权任务的迟到结果 | 必须忽略，不能修改新 turn 状态 |
| owner completion | 与当前队首/running ID 匹配的完成 | 唯一可以 pop queue 并发 terminal 的结果 |
| tool delta | 流式工具参数碎片 | 只用于即时 UI，完整 ToolCall 才是恢复事实 |
| fast prompt history | 独立的用户输入历史 | Prompt queued 时写，供历史选择而非模型 conversation |
| traceparent | W3C trace 上下文字段 | 跨 ACP → command → session span 关联请求 |
| fail closed | 信息不足时按不完整/不安全处理 | 如强预算缺 usage 时标记 incomplete |

---

## 28. 源码证据索引

| 阶段 | 文件 | 证据符号 |
| --- | --- | --- |
| ACP 入口 | `agent/mvp_agent/acp_agent.rs` | `MvpAgent::prompt()` |
| Session proxy | `session/handle.rs` | `SessionHandle` |
| Prompt command | `session/commands.rs` | `SessionCommand::Prompt` |
| Actor 准入 | `session/acp_session_impl/run_loop.rs` | Prompt match arm |
| Queue | `session/acp_session_impl/prompt_queue.rs` | `queue_input()`、`maybe_start_running_task()` |
| Queue state | `session/acp_session.rs` | `InputItem`、`State`、`sweep_pending_inputs()` |
| Turn 入口 | `session/acp_session_impl/turn.rs` | `handle_prompt()` |
| Prompt 解析 | 同上及 `prompt_parser` | `parse_prompt_with_skills()` |
| User 持久化 barrier | `turn.rs` | `push_user_message_and_ack()`、`FlushAndAck` |
| Agentic loop | `turn.rs` | `process_conversation_turn_with_recovery()`、`process_conversation_turn()` |
| Sampler 提交 | `sampler_turn.rs` | `run_turn_via_sampler()` |
| Token 映射 | `tool_calls.rs` | `handle_sampling_event()` |
| Update metadata | `updates.rs` | `send_update()`、`send_update_full()` |
| 高频缓冲 | `updates.rs`、`run_loop.rs` | `ReplayBuffer`、`emit_buffered()` |
| Stream barrier | `sampler_turn.rs`、`tool_calls.rs` | `turn_stream_drained` |
| Usage | `sampler_turn.rs` | `record_response_token_usage()` |
| Assistant history | `sampler_turn.rs` | `record_assistant_response()` |
| Turn 收尾 | `turn.rs` | `flush_to_disk()`、`end_prompt()` |
| Completion arm | `run_loop.rs` | `completion_rx.recv()` |
| Queue 完成 | `turn_end.rs` | `handle_completion()` |
| Durable terminal | `turn_end.rs` | `emit_turn_completed()` |

---

## 29. 一句话复盘

一条 Prompt 的真实主链是：`MvpAgent` 在 ACP 控制面校验并通过 command channel 投递，`SessionActor` 负责准入、排队和 turn 所有权，`handle_prompt` 建立 prompt index、rewind point 与 canonical User history，agentic loop 将请求交给 SamplerActor；独立 drainer 实时把 token 送入带 event ID 的缓冲/持久化通知轨，而 collected response 负责 usage 和 canonical Assistant，最后两道 flush/drain barrier 与 completion ownership 共同保证最后一个文本 chunk、durable TurnCompleted 和 ACP PromptResponse 以可重放的正确顺序结束。
