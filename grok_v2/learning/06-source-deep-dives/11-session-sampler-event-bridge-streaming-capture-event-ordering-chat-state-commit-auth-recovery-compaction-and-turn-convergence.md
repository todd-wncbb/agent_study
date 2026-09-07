# 源码精读 11：Session 如何消费 Sampler Event、提交最终响应并让 Turn 收敛

> 本篇把前两篇重新接回 Agent 主循环，精读 `xai-grok-shell::session` 与 `xai-grok-sampler` 之间的边界：为什么一次采样同时需要 Event Stream 和 Completion Result；流式 Text、Reasoning、Tool Call Delta、Backend Tool、Retry、Failure 与 Completed 分别由谁消费；通知顺序为什么还需要一个 Drain Barrier；未提交的流式内容怎样进入 `streaming_partial.json` 而不污染模型历史；最终 `ConversationResponse` 又在何处成为 Chat State 的权威记录；401、Context Overflow、Retry Exhaustion 和 Cancel 如何各自收敛成继续、重采样或终止。
>
> 源码基线：`ed6d543`。源码变化后优先按本文列出的类型、函数和测试名重新定位。

---

## 1. 先给结论：这里不是一条回调，而是两条协作通道

理解这一层最重要的结论是：

```text
同一次 Sampler Submit
        |
        +---- SamplingEvent channel ----> Session event drainer
        |                                  |
        |                                  +-- 实时 UI
        |                                  +-- Streaming Capture
        |                                  +-- Retry / Backend Tool 通知
        |                                  +-- Latency / Signal / Metadata
        |
        +---- completion oneshot --------> Turn loop
                                           |
                                           +-- 成功：提交 Chat State
                                           +-- 401：刷新认证后重建请求
                                           +-- Overflow：压缩后重建请求
                                           +-- 终态失败：结束 Turn
```

两条通道携带的事实不同：

- Event Channel 回答“采样过程中刚刚发生了什么”。
- Completion Channel 回答“这次 Submit 最终得到哪个权威结果”。

因此：

- `ChannelToken` 可以立即显示，但不能直接写入 Conversation。
- `Completed` 可以结束流式展示，但它的 Event Consumer 不负责提交响应。
- `Failed` 可以做日志与观测，但它的 Event Consumer 不负责决定是否压缩、刷新认证或终止。
- Chat State 的提交权只在 Turn Loop 中。

这是一条非常值得记住的 Agent Runtime 原则：

> 展示事实可以是增量的；Conversation 事实必须是最终、完整且只提交一次的。

---

## 2. 本篇主线文件

| 文件 | 责任 |
| --- | --- |
| `xai-grok-shell/src/session/acp_session_impl/spawn.rs` | 创建 Sampler Actor、Event Channel 与 Drainer |
| `xai-grok-shell/src/session/acp_session.rs` | Session 中的 Sampler、Capture 与 Drain Barrier 字段 |
| `xai-grok-shell/src/session/acp_session_impl/tool_calls.rs` | `handle_sampling_event`：事件到 Session Side Effect 的映射 |
| `xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs` | Turn Submit、失败恢复、认证恢复和 Completion 收敛 |
| `xai-grok-shell/src/session/acp_session_impl/turn.rs` | 外层 Agentic Loop、Response 提交和 Tool 执行入口 |
| `xai-grok-shell/src/session/streaming_capture.rs` | 未提交 Generation 的旁路捕获结构 |
| `xai-grok-shell/src/session/acp_session_impl/updates.rs` | ACP/xAI 通知、Event ID 与缓冲路径 |
| `xai-grok-shell/src/session/acp_session_impl/auth_retry.rs` | 认证恢复后的 Turn 级重试预算 |
| `xai-grok-shell/src/session/compaction.rs` | Context Overflow 恢复判定 |
| `xai-grok-shell/src/session/acp_session_impl/run_loop.rs` | `TakeStreamingCapture` 的 Session Command 处理 |
| `xai-grok-shell/src/upload/turn.rs` | Turn 结束时取走 Capture |
| `xai-grok-shell/src/upload/trace.rs` | 上传 `streaming_partial.json` |
| `xai-grok-shell/src/session/acp_session_tests/replay_buffer_send_update_tests.rs` | Event、Capture、Barrier 与通知顺序的不变量测试 |

---

## 3. 先画出完整时序

### 3.1 正常文本回答

```text
Turn loop             SamplerActor          event drainer          Chat State / Client
   |                       |                      |                         |
   | update_config         |                      |                         |
   | submit(request)       |                      |                         |
   |---------------------->|                      |                         |
   |                       |-- StreamStarted ---->| begin capture          |
   |                       |-- FirstToken ------->| telemetry               |
   |                       |-- Reasoning -------->| thought chunk --------->|
   |                       |-- Text ------------->| message chunk --------->|
   |                       |-- Completed -------->| clear committed slot    |
   |<----- response -------|                      | release drain barrier   |
   | wait drain barrier    |                      |                         |
   | push assistant response --------------------------------------------->|
   | ResponseCompleted -------------------------------------- notification>|
```

### 3.2 Tool Call 回答

```text
Sampler stream:
  ResponseStarted
  Reasoning chunks
  ToolCallDelta chunks
  Completed

Turn loop after barrier:
  consume canonical ConversationResponse
  persist Assistant(tool_calls)
  emit canonical ACP ToolCall
  execute tools
  persist ToolResult
  rebuild next ConversationRequest
```

`ToolCallDelta` 是“参数正在生成”的展示事件；真正可执行的 Tool Call 来自最终 Response。

### 3.3 401 恢复

```text
Submit
  -> Failed(Auth, 401)
  -> event drainer: log + signal only
  -> completion error reaches Turn loop
  -> AuthManager / provider auth recovery
  -> prepare_sampler_for_turn
  -> AuthRetrySchedule decides pacing/budget
  -> continue outer loop
  -> rebuild ConversationRequest
  -> resubmit
```

### 3.4 Context Overflow 恢复

```text
Submit
  -> completion error
  -> handle_sampling_failure
  -> should_compact_on_error
  -> compare estimated_total_tokens with metadata.context_window
  -> run_compact_only
  -> CompactAndResubmit
  -> continue outer loop
  -> build request from compacted Chat State
```

### 3.5 Cancel 或终态失败

```text
partial stream already reached client
  -> Failed event preserves capture
  -> turn ends without assistant commit
  -> TakeStreamingCapture(prompt_id)
  -> finalize_for_upload
  -> streaming_partial.json

Conversation history is unchanged by the partial.
```

---

## 4. Session 创建时，谁拥有 Event Receiver

`spawn_session_actor` 创建：

```rust
let (sampler_event_tx, mut sampler_event_rx) =
    tokio::sync::mpsc::unbounded_channel::<SamplingEvent>();
```

然后把 Sender 交给 `SamplerActor::spawn`，把 Receiver 留给 Session。

这形成明确的所有权：

| 端点 | 所有者 | 能做什么 |
| --- | --- | --- |
| `sampler_event_tx` | Sampler Actor / Request Task | 发布采样生命周期事件 |
| `sampler_event_rx` | Session Drainer | 串行消费并转成 Session Side Effect |

Session 构造完成后启动 Local Task：

```rust
while let Some(event) = sampler_event_rx.recv().await {
    drainer_session.handle_sampling_event(event).await;
}
```

这里有三个关键点。

### 4.1 单 Receiver 带来 FIFO 消费

同一 Channel 上发送成功的 Event 按 Channel 顺序被一个 Drainer 消费。

它避免了每个 Chunk 各自 Spawn Task 后重新乱序。

### 4.2 Drainer 与 Turn Loop 是不同 Task

即使 Event 自身 FIFO，Turn Loop 仍可能先拿到 Completion OneShot。

所以“Event FIFO”并不能单独保证：

```text
最后一个 Text Chunk 的客户端通知
一定早于
Turn Loop 发出的正式 ToolCall 通知
```

这正是 Drain Barrier 存在的原因。

### 4.3 Unbounded 不等于无代价

`unbounded_channel` 不会让 Sampling Producer 因队列容量等待，但消费者过慢时内存可增长。

这里选择它的隐含优先级是：

- 不用 UI Side Effect 反压模型网络流。
- 由单 Session 的 Event 处理速度承担积压风险。
- 高频通知之后仍可进入 Replay Buffer 做合并。

这是源码行为，不代表所有 Agent Runtime 都应该无界排队。

---

## 5. Sampler 配置在 Session 启动时怎样裁剪

Session 创建 Sampler 时，会根据工作流约束调整 Retry Policy。

### 5.1 输出预算子任务

若存在 `task_output_token_budget`，输出一旦开始就可能已经消耗不可重放的预算。

### 5.2 `retry_only_before_output`

若工作流要求只在输出前重试，Sampler 不能在已向父任务泄露输出后重新生成另一份内容。

### 5.3 Doom Loop Recovery 被关闭

上述两类任务都会关闭 Doom Loop Recovery。

原因不是 Doom Loop Detection 不准确，而是重新采样会破坏调用方对单次输出预算或输出唯一性的假设。

因此 Retry Policy 是 Session 语义和 Sampler 能力的交集，不是 Sampler 单方面决定的。

---

## 6. Session 中与本链路相关的三个字段

### 6.1 `sampler_handle`

向常驻 Sampler Actor 发送配置更新、Submit、Cancel 等命令。

### 6.2 `streaming_turn_capture`

```rust
Mutex<StreamingTurnCapture>
```

记录当前 Turn 中已经流出、但尚未被权威 Assistant Response 提交的内容。

它明确不属于 Chat State。

### 6.3 `turn_stream_drained`

```rust
Mutex<Option<oneshot::Sender<()>>>
```

每次 Submit 前安装一个新的 Sender；`Completed` Event 被 Drainer 消费时取出并触发。

它不是网络完成信号，而是：

> Session 已经处理到本次成功流的终止事件，所有排在它前面的流式 Event 都已经走过 `handle_sampling_event`。

---

## 7. `prepare_sampler_for_turn`：每个模型请求前刷新配置

常驻 Sampler Actor 不代表配置永远冻结。

每次 Turn Loop 提交前，`prepare_sampler_for_turn` 会：

1. 主动刷新过期 Token。
2. 从当前 Session 状态重建完整 `SamplerConfig`。
3. 按 Workflow 约束关闭 Doom Loop Recovery。
4. 注入当前 Inference Idle Timeout。
5. 调用 `sampler_handle.update_config`。

随后才 Submit。

同一个 Handle Sender 上的命令顺序保证：

```text
UpdateConfig
Submit
```

会被 Sampler Actor 按发送顺序观察。

这里的重点不是“更新一个字段”，而是避免旧 Client Cache 携带旧 Token、旧 Endpoint 或旧模型约束进入下一次请求。

---

## 8. `run_turn_via_sampler` 是两条通道的汇合点

这个函数负责一次 Submit 的 Session 级收敛。

### 8.1 Submit 前安装 Barrier

```text
create oneshot(tx, rx)
store tx in turn_stream_drained
submit_and_collect(request)
```

必须先安装，再 Submit；否则极快的 `Completed` 可能在 Barrier 出现前被消费。

### 8.2 生成独立 Request ID

Request ID 用于关联：

- Event。
- Retry 日志。
- Failed 日志。
- Latency Span。

它不是 Prompt ID。

一个 Prompt/Turn 可能因 Tool Loop、Compaction 或 Auth Recovery 产生多个 Request ID。

### 8.3 成功时先拿 Response，再等 Drainer

`submit_and_collect` 成功返回：

- `ConversationResponse`。
- `InferenceLatencyStats`。

但函数不会立即把 Response 交给上层，而是等待 Drain Barrier。

### 8.4 Barrier 有五秒超时

若五秒内未收到 `Completed` Drainer 信号：

- 清掉尚存的 Sender。
- 记录 Warning。
- 继续返回权威 Response。

这说明 Barrier 是顺序完整性的保护，不是“成功结果永不交付”的硬门闩。

设计取舍是：

- 正常时严格保持通知边界。
- Drainer 异常时避免 Turn 永久挂死。

### 8.5 失败时不等待 Completed Barrier

错误分支会移除 Barrier Sender，并进入 `handle_sampling_failure`。

因为失败路径不会收到成功 `Completed`，等待 Barrier 没有意义。

未提交的流内容则继续留在 Streaming Capture，供 Turn 收尾取走。

---

## 9. `handle_sampling_event` 的“纯映射”应该怎样理解

源码注释把它称为 Pure Event Mapping。

它并非函数式意义上的纯函数，因为它会：

- 修改 Capture。
- 修改 Chat State 的时间指标。
- 发送通知。
- 写日志和 Signal。
- 更新模型配置。

这里的“纯”应理解为：

> 它只解释单个 Event 的即时含义，不执行依赖完整 Turn 状态的语义恢复。

特别是它不做：

- Auto Compaction。
- Auth Refresh 后重提。
- Friendly Terminal Error 决策。
- 最终 Assistant Response 提交。

这些属于 Completion Error 或 Completion Response 的处理路径。

---

## 10. `StreamStarted`：建立 Generation 边界

`StreamStarted` 到来时：

1. 读取当前 `prompt_id`。
2. 锁住 `streaming_turn_capture`。
3. 若 Capture 的 Prompt ID 不同，调用 `begin_turn`。
4. 调用 `start_stream(timestamp_ms)`。
5. 把 Stream Start 时间记入 Chat State 的通知元数据。

### 10.1 Prompt 变了才重置整个 Capture

相同 Prompt 的第二个 `StreamStarted` 不会 `begin_turn`。

这允许同一个逻辑 Turn 的多次 Generation 被保留，例如：

- Doom Loop Resample。
- Transport Retry 后重启流。
- Empty Response Retry。

### 10.2 `start_stream` 会先折叠旧 Slot

新的 Generation 开始前，当前 Slot 被推入 `segments`。

然后：

- 清空当前 Reasoning/Text。
- 更新时间戳。
- 重置 Phase。
- `attempt_count += 1`。

因此 Capture 是：

```text
segments[]          = 已结束但未提交的 generation
current flat fields = 正在进行的 generation
```

### 10.3 Stream Start 不是 Response Commit

它只说明网络/解析层开始了一次生成，不保证最后得到可提交响应。

---

## 11. `FirstToken`：观测事件，不携带正文

`FirstToken` 被映射为 Session Event Tracker 的 `FirstToken`。

它用于：

- TTFT 观测。
- UI/Telemetry 生命周期。

正文不从这个 Event 获取；正文由 `ChannelToken` 携带。

---

## 12. `ChannelToken::Reasoning`：三个动作

每个 Reasoning Chunk 会执行：

1. 追加到 Capture 的 `reasoning_text`。
2. 发出 `PhaseChanged::StreamingReasoning`。
3. 发送 ACP `AgentThoughtChunk`，带 `chunk_index`。

### 12.1 Lazy Begin Fallback

如果因某种兼容路径没有先观察到 `StreamStarted`，`ChannelToken` 会：

- 用当前 Prompt ID 初始化 Capture。
- 手动把 `attempt_count` 加一。

这是一条容错路径，不是正常协议顺序。

### 12.2 Reasoning 与 Text 分槽

Capture 不把二者拼成一个字符串，因为故障诊断必须回答：

- 模型仍在 Thinking 时被截断？
- 还是已经开始输出最终 Answer？

---

## 13. `ChannelToken::Text`：展示增量，不提交历史

Text Chunk 会：

1. 追加到 `response_text`。
2. 发出 `PhaseChanged::StreamingText`。
3. 发送 ACP `AgentMessageChunk`。

最容易误读的一点是：

```text
AgentMessageChunk 已经显示在客户端
```

不等于：

```text
Assistant Message 已经写入 Chat State
```

客户端 Scrollback 是展示投影；Chat State 是下一轮 Prompt 的权威历史。

如果流在半句时失败：

- 用户可能已经看见半句。
- Chat State 不应让模型在下一轮把半句当作正式历史。
- 半句只进入诊断 Capture。

---

## 14. Chunk Index 与 Event ID 是两个不同坐标

`chunk_index` 来自采样流，用于标识 LLM Chunk。

`eventId` 由 Session 的通知发送路径生成，用于所有通知的全局排序和去重。

| 坐标 | 作用域 | 主要用途 |
| --- | --- | --- |
| `chunk_index` | 一次 Sampling Stream | Chunk 跟踪、合并 |
| `eventId` | Session Notification Stream | 客户端顺序、重放、去重 |

不能用 Chunk Index 代替 Event ID，因为 Tool Call、Mode Update、Retry State 等事件不一定有 Chunk Index。

---

## 15. `ToolCallDelta`：只展示“正在长出来的参数”

事件包含：

- `tool_index`。
- Tool Call ID。
- Name。
- Arguments Delta。

Session 会：

1. 把 Capture Phase 标为 `ToolCall`。
2. 发送缓冲的 xAI `ToolCallDeltaChunk`。

它不会执行 Tool，也不会把 Delta 逐段写入 Chat State。

真正执行使用的是最终 `ConversationResponse` 中解析完整的 Tool Call。

这避免了：

- 半个 JSON 参数被执行。
- Retry 的旧 Delta 与新 Generation 混成一个调用。
- Stream Decoder 的临时状态污染权威 Conversation。

---

## 16. `ResponseStarted`：Messages 风格的响应边界

它投影为 xAI Extension `ResponseStarted`，携带：

- Message ID。
- Model。
- Input Tokens。
- Cache Read Input Tokens。
- Cache Creation Input Tokens。

它属于高频/有序缓冲路径。

它给客户端一个响应级边界，但仍然不是 Chat State Commit。

---

## 17. `ReasoningCompleted`：签名边界

这个事件把 Reasoning Signature 发送为 xAI Extension。

签名常用于：

- 保留 Provider 的加密/可验证 Reasoning Continuation 信息。
- 让客户端知道 Reasoning 段已经结束。

最终 Response 中也可能携带签名；`ResponseCompleted` 会从 Reasoning Item 中提取最终签名。

---

## 18. `Completed`：它结束流，但不提交 Response

这是全篇最值得逐字记住的分界。

`handle_sampling_event(Completed)` 做：

1. 释放 Stream Drain Barrier。
2. 处理 Doom Loop Accepted-after-budget 统计。
3. 清掉当前已提交 Generation 的 Capture Slot。
4. 记录 API Request Time。
5. 记录 Inference Metrics。

它不做：

- `push_assistant_response`。
- Tool Call 执行。
- Tool Result 回填。
- Turn 结束判断。

### 18.1 为什么 Event 中明明有 Response，仍不提交

`SamplingEvent::Completed` 确实携带 `response`。

但 Session 把它当成观测终止事件，而不是权威业务返回路径。

原因包括：

- Turn Loop 需要统一处理 Response 与 Error。
- Turn Loop 持有当前循环的 Prompt、Schema、Tool Gate、Auth Schedule 等局部状态。
- 若 Event Drainer 和 Turn Loop 都提交，会出现双写。
- Completion OneShot 天然提供“一次 Submit 只有一个返回值”的控制流。

---

## 19. 为什么 `Completed` 要清 Capture 当前 Slot

正常成功的 Reasoning 与 Answer 最终会随 canonical response 进入 Chat State / Trace History。

若再把同一内容上传为 `streaming_partial.json`：

- Trace 重复。
- 容易误判为失败 Generation。
- 占用额外存储。

所以 `clear_current_segment` 丢弃当前已提交 Generation 的正文。

但它不会清空整个 Capture。

### 19.1 之前的未提交 Generation 仍保留

例如：

```text
Generation 1: doom loop，已流出 reasoning，随后 resample
Generation 2: 正常完成并提交
```

`Completed` 只清 Generation 2 当前 Slot；Generation 1 已经在 `segments[]` 中，继续保留。

成功 Turn 若含 Doom Loop Segment，Turn Trace 仍可上传它，并标记 `doom_loop_recovered`。

---

## 20. Doom Loop Stamp 怎样跨 Generation 保存

`Retrying` 且 Kind 为 `DoomLoopDetected` 时：

1. Turn Tally 的 `attempts` 加一。
2. 合并 Trigger。
3. 给当前 Capture Slot 加 `DoomLoopSegmentStamp`。
4. Action 设为 `resampled`。
5. 记录 Recovery Attempt Signal。

Stamp 包含：

- `doom_loop_triggers`。
- 1-based `attempt`。
- `aborted_at_chunk`。
- `action`。

下一次 `StreamStarted` 折叠当前 Slot 时，Stamp 跟正文一起进入 Segment。

### 20.1 无正文的 Doom Attempt 也必须留 Segment

如果检测发生得极早，Slot 可能没有 Text/Reasoning。

普通空 Slot 可跳过；带 Doom Stamp 的空 Slot不能跳过，否则：

- 本次 Recovery 消失。
- Stamp 可能错误黏到下一次 Generation。

测试专门固定了这个边界。

### 20.2 Retry 预算耗尽后仍接受的响应

若之前已经发生 Doom Recovery Attempt，而最终 Response 仍带 confident triggers：

- Tally 标为 `accepted_after_budget`。
- 当前 Slot 加相应 Stamp。
- `Completed` 清正文时保留一个无正文 Stamp Segment。

于是 Trace 能区分：

- 观察到信号但从未重采样。
- 重采样成功恢复。
- 花完预算后仍接受。

---

## 21. `Retrying` 的非 Doom 分支

所有 Retry 都会：

- 写统一 `shell.turn.inference_retry` 日志。
- 携带 Request ID、Attempt、Max Retries、Kind 与截断 Reason。
- 发送 xAI `RetryState::Retrying`。

只有 Doom Retry 会额外 Stamp Capture。

Transport、Rate Limit、Empty Response 等 Retry 仍可能产生新的 `StreamStarted`，旧的未提交正文会因 Generation Fold 被保留，但没有 Doom 特定标签。

---

## 22. `Failed` Event 为什么故意“不恢复”

`Failed` Event Handler 做：

- 统一失败日志。
- Typed Error Signal。
- Empty Response Context 的诊断日志。

它不做：

- 401 刷新。
- Auto Compact。
- Retry Exhaustion 的用户消息。
- 清除 Streaming Capture。

因为同一个最终错误还会通过 Completion Channel 回到 `run_turn_via_sampler`。

若两边都恢复，可能出现：

- 两次 Auth Refresh。
- 两次 Compaction。
- 两份终态通知。
- 两次 Resubmit。

所以 Event Path 是 Observe，Completion Path 是 Decide。

---

## 23. `Failed` 为什么不能清 Capture

失败发生前可能已经流出：

- Reasoning。
- 半截最终 Text。
- Tool Call Delta。

这些内容不能提交到下一轮模型历史，但对诊断很有价值。

因此 Failed 保留 Capture，稍后由 Turn End Consumer 取走。

测试 `failed_event_preserves_streaming_capture_for_takeout` 固定了这个不变量。

---

## 24. `ModelMetadata`：响应 Header 反向修正 Session 配置

`ModelMetadata` Event 进入 `handle_model_metadata_update`。

它可能携带：

- Models ETag。
- Context Window。
- Max Completion Tokens。

### 24.1 ETag

新 ETag 触发 Models Manager 刷新检查。

### 24.2 Context Window 只允许安全升级

若没有显式 Compaction Override：

- Header 值大于当前值：接受升级。
- Header 值小于当前值：Warning 并忽略。

忽略降级是一个防御性策略，避免偶发/错误 Header 把 Session 的有效窗口突然缩小。

### 24.3 Max Completion Tokens 可以更新

Header 中的新值与当前不同，就写回 Sampling Config。

### 24.4 更新写入 Chat State Config

它不会只改 Session 的临时字段，而是通过 Chat State Handle 更新 Sampling Config，使后续 Request 重建能看到新值。

---

## 25. Backend Tool Event 与模型 Tool Call 不同

Sampler 还会发送：

- `BackendToolStarted`。
- `BackendToolCompleted`。

它们表示 Provider Backend 内部执行的 Hosted Tool，例如搜索类能力。

这与模型要求本地 Agent 执行的 Tool Call 是两条路径。

### 25.1 Started

Session 会：

- 记录 Tool Call Signal。
- 从 Backend Tool 类型生成展示标题、Tool Kind 与输入。
- 发送 ACP `ToolCall`，状态为 In Progress。
- 在 Metadata 中标记 Backend Tool。

### 25.2 Completed

Session 根据 Result 判断成功/失败：

- 更新 Signal。
- 发送 ACP `ToolCallUpdate`。
- 附加 Title、Status 与 Raw Output。

### 25.3 为什么仍走 Event Path

Hosted Tool 的执行发生在 Provider 采样内部，Shell 不负责 Dispatch，所以它天然表现为 Sampling 生命周期事件。

本地 Tool 则必须等待最终 Response、权限检查和 Tool Bridge Dispatch。

---

## 26. 通知为什么要经过 Replay Buffer

Text、Thought、ToolCallDelta 等高频更新不会全部直接广播。

路径大致是：

```text
send_update / send_buffered_xai_update
  -> SessionEvent::Notification
  -> actor FIFO
  -> ReplayBuffer
  -> merge / debounce / flush
  -> persistence + live gateway
```

这样做可以：

- 合并细碎 Chunk。
- 减少 IPC/持久化写入。
- 支持重连后的 Replay。
- 保持一个统一的通知顺序入口。

低频的一次性通知，例如 Retry State，使用 Direct xAI Notification 路径。

“缓冲”与“直接”的选择按频率和顺序语义划分，而不是按 ACP/xAI 名字简单划分。

---

## 27. `send_update` 在什么时候生成 Event ID

`send_update_full` 会：

1. 关闭 Rewind Window。
2. 更新 Edit Path 等附加状态。
3. 读取 Total Tokens、Stream Start、Turn Start。
4. 调用 `generate_event_id()`。
5. 构造 Notification Metadata。
6. 把 Notification 放入 Session Event FIFO。

Metadata 常含：

- `totalTokens`。
- `eventId`。
- `agentTimestampMs`。
- `promptId`。
- `streamStartMs`。
- `turnStartMs`。
- `updateType` / `updateParams`。
- `chunkId`。
- Replay 时的 `isReplay`。

Event ID 在 `send_update` 调用时分配，而不是等真正广播时分配。

因此不同 Task 调用 `send_update` 的相对时机非常重要。

---

## 28. Drain Barrier 解决的真实乱序问题

假设没有 Barrier：

```text
Drainer Task: Text chunk A -> send_update(eventId 100)
Turn Task:    canonical ToolCall -> send_update(eventId 101)
Drainer Task: Text chunk B -> send_update(eventId 102)
```

客户端看到：

```text
回答前半段
Tool Call
回答后半段
```

这不是 Sampler Event 自身乱序，而是两个消费者 Task 在 Session Notification 序列上交错。

### 28.1 Barrier 的保证

Drainer 只有处理到 `Completed` 才释放 Barrier。

由于 `Completed` 在同一个 Event Channel 中排在此前 Chunk 后面，释放时这些 Chunk 都已经调用了通知发送函数并取得 Event ID。

Turn Loop 等到 Barrier 后才处理 Response 中的 canonical Tool Call。

于是顺序成为：

```text
all streamed chunks' eventId
<
canonical post-response notifications' eventId
```

### 28.2 Barrier 不保证什么

它不保证：

- 客户端已经绘制所有 Chunk。
- Persistence 已经落盘所有 Chunk。
- Gateway 网络已经确认所有 Chunk。

它保证的是 Session 本地通知入队/Event ID 分配边界。

---

## 29. Completion Result 回到 Turn Loop 后发生什么

成功路径在 `turn.rs` 中继续。

它先记录：

- Model Elapsed。
- TTFT。
- ITL P50。
- Attempts。
- Prompt/Cached/Completion/Reasoning Tokens。
- 近似 Tokens Per Second。

然后：

- 更新 Token Usage。
- 清掉“压缩直到成功前禁止再次压缩”等抑制状态。
- 记录 Response Usage。
- 构造 `ResponseCompleted` Extension。

随后才消费 `response.items`。

---

## 30. `response.items` 怎样进入 Chat State

代码按 Item Variant 分流：

```rust
match item {
    ConversationItem::Assistant(_) => record_assistant_response(item),
    _ => chat_state_handle.push_tool_result(item),
}
```

这里的 `push_tool_result` 名字比实际用途窄。

非 Assistant Item 可能包括：

- Reasoning Item。
- Backend Tool Call/Result 表示。
- 其他协议 Conversation Item。

阅读时应把它理解成“追加非 Assistant Conversation Item 的通用入口”，而不是只允许本地 Tool Result。

---

## 31. `record_assistant_response` 才是 Assistant Commit 点

它做三件核心事：

1. 记录 Assistant Message Signal。
2. 对模型 ID 与首个 Tool Call 做日志。
3. 调用 `chat_state_handle.push_assistant_response`。

这就是权威 Assistant Response 进入 Conversation 的位置。

因此本文中的“提交”不是数据库事务术语，而是：

> 从暂态 Streaming Projection 转换为下一轮模型请求可见的 Conversation History。

---

## 32. Fallback Text 为什么只在没有流式正文时补发

最终 Response 可能含 Assistant Text，但 Sampling Backend 没有产生对应 Text Chunk。

Turn Loop 从 Response 计算 `fallback_text()`：

- 若需要，发送一个不带 Chunk Index 的 `AgentMessageChunk`。
- 若正文已经流式发过，则不重复。

这让“最终 Response 完整”与“客户端展示完整”在 Backend 不完全一致时仍能收敛。

---

## 33. Content Filter 空响应的展示补偿

若：

- Stop Reason 是 Content Filter。
- Response 本身为空。

Turn Loop 会合成一段 Provider Refusal Notice，并在有 Explanation 时追加说明。

这段 Notice 是客户端体验补偿，不等价于把 Provider 未生成的 Assistant Content 伪造进原始 Response。

---

## 34. `ResponseCompleted` 何时发出

Session 先构造 Boundary Update，再提交 Response Items、补 Fallback/Refusal，最后通过高频有序路径发送 `ResponseCompleted`。

它携带：

- Message ID。
- Raw Stop Reason。
- Usage 投影。
- Reasoning Signature。
- Stop Sequence。

Usage 中 Input Tokens 会减掉：

- Cache Read Tokens。
- Cache Creation Tokens。

以得到 Messages API 语义下的 uncached input。

`ResponseCompleted` 是客户端协议边界；Chat State Commit 则由前面的 Item Push 完成。两者不要混为一个动作。

---

## 35. 成功 Response 后为什么还可能继续同一个 Turn

Agentic Turn 不等于一次 Sampling Request。

成功 Response 后可能：

- 有本地 Tool Calls：执行后继续模型循环。
- Todo Gate 发出 Reminder：继续模型循环。
- Structured Output Gate 要求修复：继续模型循环。
- Two-pass/其他控制逻辑要求下一次采样。

所以：

```text
Sampling Completed
!=
Prompt Turn Completed
```

每次 Sampling Response 都可以提交到 Conversation，随后新的 Request 使用更新后的历史。

---

## 36. `handle_sampling_failure` 的判定顺序很重要

Completion Error 进入这个函数后，不是简单按 Status Code Switch。

其大致优先级是：

1. Workflow Output Budget / Retry-only 约束。
2. 是否可通过 Context Compaction 恢复。
3. 特殊的 Encrypted Content 400。
4. Rate Limit 终态。
5. Model/Base URL/Auth Provider 上下文解析。
6. Session Auth 或 Provider Auth 恢复。
7. Idle / Empty Response 等 Signal 与 Capture Stamp。
8. Legacy Login、404、401 等友好错误增强。
9. 发送 Terminal Retry State 并返回 ACP Error。

顺序决定同一个底层错误最终被解释成哪一种产品语义。

---

## 37. Output-budgeted 子任务为什么直接 Fail Closed

对于有输出预算的 Workflow Child：

- 采样失败时关闭 Usage Accounting。
- 不进入一般恢复分支。
- 返回 Terminal Internal Error。

对于 `sampler_retry_only_before_output`：

- 异步标记 Usage Incomplete。
- 同样终止。

这是在保护父子 Agent 之间的计费与输出契约。

---

## 38. Context Overflow 恢复不是字符串匹配

`should_compact_on_error` 的核心判断是：

1. Auto Compact 没被抑制。
2. Error Metadata 提供非零 `context_window`。
3. 当前估算 Total Tokens 大于 Context Window。

它不依赖错误 Message 是否恰好包含“context length exceeded”。

### 38.1 好处

- Provider 使用不同 Status Code 或文案时仍可能恢复。
- 逻辑基于结构化 Metadata 与本地 Token Estimate。

### 38.2 限制

- 没有 `model_metadata.context_window` 时不能走这条恢复。
- 本地 Estimate 未超过窗口时，即使 Message 像 Overflow，也不会自动压缩。

### 38.3 Context Window 可同步升级

若没有用户 Override，Error Metadata 中的 Context Window 可以更新当前 Sampling Config，再触发 Compaction。

---

## 39. `CompactAndResubmit` 为什么必须回到外层 Loop

Compaction 会改变 Chat State History。

因此不能复用失败前已经构造好的 `ConversationRequest`。

外层收到 `CompactAndResubmit` 后：

1. 重置 Auth Retry Schedule。
2. `continue`。
3. 从压缩后的 Chat State 重新 Build Conversation Request。
4. 重新注入 Session/Turn/Agent/Deployment 等请求字段。
5. 再 Submit。

这是一条“改变权威状态后必须重建快照”的通用原则。

---

## 40. 401 为什么也回到外层 Loop

成功 Auth Recovery 会改变：

- Session Token Store。
- Provider Credential Store。
- Sampler Config / Live Credential Resolver 可见值。

返回 `RefreshAuthAndResubmit` 后，外层 Loop 重新执行请求准备，避免旧请求对象或旧 Client 状态继续上线路径。

---

## 41. Auth Recovery 有两类 Store

恢复结果会标明：

- `SessionToken`。
- `AuthProvider`。

Session Auth 与 Provider Auth 是分开的恢复域，并且恢复资格由 Auth Gate、失败 Base URL 和当前模型 Provider 等上下文决定。

不是每个 401 都无条件弹登录或刷新所有 Token。

---

## 42. `SentCredential` 为什么决定重试是否收费

错误记录携带 Credential Attribution：

- `Missing`：请求确认没有带 Credential。
- `Sent`：确认带了 Credential。
- `Unknown`：无法证明，按 Fail-closed 处理。

认证恢复后的 401 分成：

### 42.1 Missing：Uncharged Resubmit

没有真正把新 Credential 送上 Wire，不消耗三次 Credentialed Retry 预算。

但有单独 Runaway Guard。

### 42.2 Sent / Unknown：Charged Backoff

消耗一个 Retry Slot，并按 1s、2s、4s Backoff。

Unknown 之所以收费，是为了避免 Attribution 缺失导致无限重试。

---

## 43. `AuthRetrySchedule` 的三个保险丝

### 43.1 Credentialed Retry Budget

最多三次带凭据/未知归因的 Post-recovery 401 重试。

Delay 是：

```text
1s -> 2s -> 4s
```

源码特别警告 `tokio_retry::ExponentialBackoff` 的参数语义：错误地把 1000 当 base 会得到指数爆炸的分钟/天级等待。

### 43.2 Uncharged Runaway Guard

Credential Missing 的 Reject 不占三次预算，但最多允许 50 次 Success-free Resubmit。

否则“每次恢复成功但请求始终没带 Token”会无限循环。

### 43.3 Suspend Reset Cap

若 Wall Clock 相比 Monotonic Clock 多出至少 30 秒，认为机器发生 Suspend。

一个跨休眠的 Auth Incident 可以重置预算，但最多八次；长期故障不能靠反复睡眠永远续命。

---

## 44. Missing Credential Resubmit 怎样限速

若恢复写入 Session Token Store，但 `current_wire_valid()` 还没有值：

- 最多等待 15 秒让 Refresh 落地。

其他情况：

- 至少 Sleep 1 秒。

所以即使 Uncharged，也不会形成紧密请求风暴。

---

## 45. Auth 成功响应会重置什么

任意成功 Model Response 都调用 `reset_on_success()`：

- Charged Attempts 清零。
- Incident Counts 清零。
- Uncharged Rejections 清零。
- Suspend Reset Count 清零。

“成功”结束一段连续失败叙事。

Compaction-and-resubmit 也重置 Auth Schedule，因为它开启的是不同原因驱动的新请求阶段。

---

## 46. Auth Budget Exhausted 为什么有专门终态函数

Budget Exhaustion 发生在 `handle_sampling_failure` 之外：

- 单次 401 先成功恢复。
- 外层 Schedule 决定已无继续预算。

因此它需要 `fail_turn_auth_budget_exhausted` 自己：

- 发送 `RetryState::Failed`。
- 构造 ACP Error。

这避免了“只有单次错误处理器会发终态通知”的错误假设。

---

## 47. Empty Response 怎样增强 Capture

Terminal Empty Response 若带 `EmptyResponseContext`，失败处理会把以下信息 Stamp 到 Capture：

- `reasoning_tokens`。
- `completion_tokens`。
- `finish_reason`。
- `empty_reason`。

即使一个 Reasoning Chunk 都没到 Shell，只要 Token Metadata 表明模型消耗了大量 Reasoning，Capture 也不应因“正文为空”被丢弃。

`is_empty` 因此不能只检查字符串长度。

---

## 48. `StreamingTurnCapture` 的数据模型

核心字段可以分成五组。

### 48.1 身份

- `prompt_id`。
- `turn_number`。
- `model_id`。

### 48.2 当前 Generation

- `started_at_ms`。
- `reasoning_text`。
- `response_text`。
- `reasoning_chunks`。
- `text_chunks`。
- `phase`。
- 当前 Doom Stamp。

### 48.3 已折叠 Generation

- `segments: Vec<StreamSegment>`。

### 48.4 Turn 级元数据

- `attempt_count`。
- `reason`。
- `truncated`。

### 48.5 Terminal Empty 元数据

- Reasoning/Completion Tokens。
- Finish Reason。
- Empty Reason。

---

## 49. Capture Phase 是“被打断时在哪里”

Phase 包括：

- `Pending`。
- `Reasoning`。
- `ResponseText`。
- `ToolCall`。

没有 `ToolExecution`，因为本地 Tool 执行发生在成功 Response Commit 之后；那时当前 Sampling Generation 已经 Completed，不属于“未提交流捕获”。

Phase 的诊断意义是：

```text
模型尚未开始输出？
卡在 reasoning？
回答写到一半？
工具参数生成到一半？
```

---

## 50. Capture 的 8 MB 上限怎样计算

上限跨整个 Turn 的所有 Segment 计算，而不是每个 Generation 各有 8 MB。

```text
total_bytes =
  sum(segments.reasoning + segments.response)
  + current.reasoning
  + current.response
```

超过后：

- 只保留还能放下的 UTF-8 完整前缀。
- `truncated = true`。
- 后续 Append 快速返回，避免反复 O(segments) 统计。

### 50.1 UTF-8 边界

截断点会向前退到 Char Boundary，避免序列化出非法 UTF-8。

### 50.2 Phase 先更新再检查容量

即使正文因 Cap 被裁掉，Phase 仍反映模型最新到达的阶段。

诊断中“在哪里失败”比“是否保存了最后几个字节”更重要。

---

## 51. 为什么清掉已提交 Slot 后要重算 `truncated`

假设一个成功 Generation 曾把 Capture 撑到 8 MB：

- `truncated` 变成 true。
- `Completed` 后这份已提交正文应被丢弃。

若 `truncated` 仍粘住，后面同 Turn 的未提交 Generation 将无法 Append。

所以 `clear_current_segment` 会根据剩余 Segments 重新计算 Cap 状态。

这是一处很典型的“缓存标志必须随权威数据删除而失效”。

---

## 52. `finalize_for_upload` 做什么

取走 Capture 后，它会：

1. 把当前 Slot 折叠进 Segments。
2. 基于保留 Segment 重算 `truncated`。
3. 按顺序重建扁平 `reasoning_text`。
4. 按顺序重建扁平 `response_text`。
5. 汇总 Chunk Count。
6. 取首 Segment 的 Start Time。
7. 取末 Segment 的 Phase。

扁平字段用于兼容已有 Trace Viewer；`segments[]` 才保留完整的多 Generation 结构。

不同 Attempt 的非空正文间加入：

```text
--- attempt N ---
```

方便旧 Viewer 直接阅读。

---

## 53. `TakeStreamingCapture` 为什么是 Session Command

Capture 虽使用 Mutex，但取走动作仍通过 Session Actor Command：

```text
TakeStreamingCapture { prompt_id, respond_to }
```

这样 Turn End Consumer 不需要直接持有 Session 内部字段，也能与其他 Session Command 保持明确边界。

处理逻辑：

1. 锁住 Live Capture。
2. 比较 Prompt ID。
3. 匹配则 `mem::take`，原位恢复 Default。
4. 释放锁。
5. 在锁外 `finalize_for_upload`。
6. Finalize 后为空则返回 None。

### 53.1 为什么在锁外 Finalize

Finalize 可能拼接最多约 8 MB 的字符串。

若持锁执行，会阻塞同 Session 中正在到达的 Sampling Event。

先 `mem::take` 把所有权移出，是 Rust 中很实用的“大对象锁外处理”模式。

---

## 54. Prompt ID Tail Race

存在一个已知尾部 Race：

```text
旧 Prompt 被取消
新 Prompt 已排队并开始 StreamStarted
新 StreamStarted 把 Live Capture 重置成新 Prompt
旧 Prompt 才发送 TakeStreamingCapture
```

处理策略是：

- 若 Prompt ID 不匹配，不把新 Prompt 的内容误归给旧 Prompt。
- Live Slot 非空时记录 Warning Tripwire。
- 返回 None。
- 当前没有 Per-prompt Stash，所以旧 Partial 会丢失。

这是明确的“宁可少一份诊断数据，也不错误归因”。

---

## 55. Turn End Consumer 怎样区分 committed

`upload::turn::take_streaming_partial` 接受 `committed: bool`。

无论 committed 与否，它都会先取走 Live Slot，防止下一 Turn 继承。

### 55.1 `committed == false`

返回所有非空 Capture，并由调用方设置具体 Reason，例如 Cancel 或 Sampling Failure。

### 55.2 `committed == true`

正常已提交 Generation 已由 `Completed` 清掉。

只有 Capture 含 Doom Loop Segments 才保留并上传，Reason 默认为：

```text
doom_loop_recovered
```

其他 Capture 被丢弃。

---

## 56. 为什么 `streaming_partial.json` 永远不写 Chat State

它是 Trace Artifact，不是 Conversation Item。

上传路径：

```text
TakeStreamingCapture
  -> finalize_for_upload
  -> complete_prompt_trace
  -> upload_streaming_partial
  -> {session_id}/turn_N/streaming_partial.json
```

它与：

- `chat.jsonl`。
- `turn_messages.json`。
- `afterStateHistory`。

分离。

这样下一轮模型不会看到：

- 被 Cancel 的半句。
- 失败 Attempt 的 Reasoning。
- Doom Loop 的旧 Generation。
- 截断的 Tool Arguments。

这是防止 Conversation History Pollution 的关键隔离。

---

## 57. Cancel、Fail、Success 的 Capture 真值表

| Turn 结果 | 当前成功 Slot | 旧失败 Segments | 是否可能上传 |
| --- | --- | --- | --- |
| 正常成功，无 Retry | Completed 清掉 | 无 | 否 |
| Doom Recovery 后成功 | Completed 清掉 | 保留并带 Stamp | 是，`doom_loop_recovered` |
| 流中 Cancel | 保留 | 可能保留 | 是 |
| Terminal Sampling Failure | 保留 | 可能保留 | 是 |
| Reasoning-only Empty | 保留/空正文但有 Token Stamp | 可能保留 | 是 |
| Prompt ID Tail Race | 新 Prompt 占 Slot | 旧数据无法取回 | 否，记录 Warning |

---

## 58. “只收敛一次”由哪些边界共同实现

没有单个全局 Boolean 叫 `committed_once`；唯一收敛来自多个结构组合。

### 58.1 Sampler Request Task

每个 Submit 通过 Completion OneShot 返回一个 Result。

### 58.2 Event Drainer

Event 只投影生命周期，不提交 Conversation。

### 58.3 Turn Loop

只在 `SamplerTurnOutcome::Response` 分支消费 `response.items`。

### 58.4 Retry/Recovery Outcome

失败恢复只返回枚举：

- `CompactAndResubmit`。
- `RefreshAuthAndResubmit`。
- Terminal Error。

外层 Loop 是唯一 Resubmit 控制点。

### 58.5 Chat State Actor

Conversation Mutation 经 Actor 串行化。

组合起来得到：

```text
一个 Attempt 只有一个 Completion Result；
一个 Result 只有 Turn Loop 能转成 Conversation Mutation；
一个 Mutation Stream 由 Chat State Actor 串行处理。
```

---

## 59. 不要混淆四种“完成”

| 完成概念 | 含义 |
| --- | --- |
| HTTP/SSE 完成 | 网络响应结束 |
| Sampling `Completed` | Sampler 构造出权威 `ConversationResponse` |
| Response Commit | Turn Loop 把 Response Items 追加进 Chat State |
| Prompt Turn 完成 | 不再有 Tool Loop/Gate/Resubmit，向调用方返回终态 |

它们可能相隔多个异步步骤。

---

## 60. 不要混淆三种 Tool

| Tool 形态 | 谁执行 | 从哪里展示 | 是否需本地权限/Dispatch |
| --- | --- | --- | --- |
| ToolCall Delta | 尚未执行 | Sampling Event | 否，只有增量展示 |
| Backend/Hosted Tool | Provider | BackendTool Event | 通常不走本地 Dispatch |
| Agent Local/MCP Tool | Shell Tool Bridge | 最终 Response + Turn Loop | 是 |

---

## 61. 不要混淆两种 Retry

### 61.1 Sampler 内部 Retry

处理：

- Transport。
- Retryable Status。
- Empty Response。
- Doom Loop Resample。
- 图片降级等 Sampler Policy。

对 Session 表现为 `Retrying` Event，最终仍只返回一个 Completion Result。

### 61.2 Session Turn 级 Resubmit

处理：

- Context Compaction 后重建请求。
- Auth Recovery 后重建请求。

它发生在一次 Sampler Submit 已经失败之后，由外层 Turn Loop 再 Submit。

前者重用 Sampler Attempt 机制；后者重建 Session Request Snapshot。

---

## 62. 测试如何固定这些不变量

### 62.1 `channel_tokens_accumulate_into_streaming_capture`

验证：

- Reasoning/Text 分别按到达顺序拼接。
- Prompt/Turn/Start Time 正确。
- Chunk Count 正确。
- 最后 Channel 决定 Phase。

### 62.2 `same_prompt_restart_accumulates_segments_via_handler`

验证相同 Prompt 的第二个 `StreamStarted` 折叠旧 Generation，而不是清空整个 Turn。

### 62.3 `completed_event_clears_slot_keeps_prior_uncommitted_segments`

验证 Completed：

- 清当前提交 Slot。
- 不删除更早的未提交 Segment。

### 62.4 `completed_event_releases_stream_drain_barrier`

验证：

- 中途 Chunk 不释放 Barrier。
- Completed 取走 Sender。
- Receiver 收到信号。

### 62.5 `failed_event_preserves_streaming_capture_for_takeout`

验证 Failed 不清理 Partial Reasoning，且 Phase 保留在 Reasoning。

### 62.6 `reasoning_only_doomloop_turn_captures_every_generation_as_segments`

通过真实 Session Command 和 Failure Handler 验证：

- 所有未提交 Generation 按序保留。
- Empty Reason Stamp 保留。

### 62.7 Streaming Capture Struct Tests

还固定：

- 空 Doom Stamp 不泄漏到下一 Generation。
- Stamp JSON Round Trip。
- 只保留未提交 Generation。
- Token-only Metadata 也算非空。
- 8 MB Cap 跨 Segment。
- Commit 后重置 Cap。
- 新 Prompt 重置整个 Capture。

---

## 63. 一条正常成功路径的逐步追踪

假设模型输出 Reasoning，再输出文本，无 Tool。

1. Turn Loop 从 Chat State Build Request。
2. `prepare_sampler_for_turn` 刷新 Token 与配置。
3. 安装 Drain Barrier。
4. Submit。
5. `StreamStarted` 初始化 Capture Generation。
6. Reasoning Chunks 进入 Capture 与 Thought Notifications。
7. Text Chunks 进入 Capture 与 Message Notifications。
8. Sampler 构造完整 Response。
9. Completion OneShot 返回 Response。
10. Completed Event 在 Channel 中排到所有 Chunk 后。
11. Drainer 处理 Completed，清当前 Capture Slot并释放 Barrier。
12. `run_turn_via_sampler` 通过 Barrier。
13. Turn Loop 记录 Usage/Latency。
14. Assistant Item 被 `record_assistant_response` 提交。
15. `ResponseCompleted` 入通知 FIFO。
16. 没有 Tool/Gate，Prompt Turn 收敛。
17. Turn End 取 Capture；Finalize 后为空，不上传 Partial。

---

## 64. 一条 Doom Recovery 成功路径

1. Generation 1 开始。
2. 流出重复 Reasoning。
3. Sampler 识别 Doom Loop。
4. 发 `Retrying(DoomLoopDetected)`。
5. Drainer 给 Generation 1 Stamp `resampled`。
6. Generation 2 的 `StreamStarted` 折叠 Generation 1。
7. Generation 2 正常输出并 Completed。
8. 当前成功正文被清掉。
9. Generation 1 Segment 仍在 Capture。
10. Turn Loop 提交 Generation 2 的 canonical response。
11. Turn End 因 Capture 含 Doom Segment 而保留。
12. 上传 `streaming_partial.json`，Reason 为 `doom_loop_recovered`。

这个 Artifact 不是“最终回答的副本”，而是“被恢复掉的失败生成证据”。

---

## 65. 一条 Max Tokens Failure 路径

1. StreamStarted。
2. Reasoning 或 Text 已经流给客户端。
3. Sampler 判断 Max Tokens Truncation 为 Terminal。
4. 发 Failed Event，并完成 Completion Error。
5. Event Drainer 记录日志/Signal，不清 Capture。
6. Turn Loop 的 Failure Handler 构造终态错误。
7. Prompt 结束但没有 Assistant Commit。
8. Turn End 发送 `TakeStreamingCapture(prompt_id)`。
9. Capture Finalize，把当前 Slot折叠成 Segment。
10. 上传 `streaming_partial.json`。
11. 下一轮 Chat State 不含这段截断输出。

---

## 66. 一条 Context Overflow 路径

1. Submit 失败。
2. Failed Event 只做观测。
3. Completion Error 到 `handle_sampling_failure`。
4. Metadata 提供 Context Window。
5. Estimated Total Tokens 超过窗口。
6. `run_compact_only` 更新 Chat State。
7. 返回 `CompactAndResubmit`。
8. 外层 `continue`，不把失败 Attempt 写入 Conversation。
9. 从压缩后历史重建新 Request。
10. 新 Submit 成功后按正常路径 Commit。

---

## 67. 一条 401 Credential Missing 路径

1. Request 收到 401，Attribution 为 Missing。
2. Session Auth Recovery 成功刷新 Store。
3. `prepare_sampler_for_turn` 更新配置。
4. 返回 `RefreshAuthAndResubmit(Missing)`。
5. Schedule 不消耗三次 Credentialed Slot。
6. 发送 Retrying 通知。
7. 等待 Wire-valid Token 最多 15 秒，或至少 Floor Pace 1 秒。
8. 外层重建并 Submit。
9. 若连续 50 次仍无成功，Runaway Guard 终止。

---

## 68. 一条 401 Credential Sent 路径

1. Request 确认带 Credential，仍收到 401。
2. Auth Recovery 成功。
3. Schedule 消耗一次 Slot。
4. Backoff 1 秒后重建并 Submit。
5. 再失败则 2 秒、4 秒。
6. 预算耗尽后走专门 Terminal Failure。
7. 成功 Response 会清空整个 Incident Schedule。

---

## 69. Rust 所有权视角：为什么 Response 可以走两条路

`Completed` Event 中的 Response 通常以 `Box<ConversationResponse>` 携带，Completion Result 则拥有另一个最终返回值路径所需的 Response。

在 Sampler 内部，产生终态的一方负责安排 Event 与 Completion 的数据所有权，而 Session 两个 Consumer 不共享可变 Response 引用。

更重要的是业务所有权：

- Event Consumer 拥有观测权。
- Turn Consumer 拥有提交权。

Rust 类型只能防止内存级双重所有权；“谁有权写 Chat State”仍由架构约定和调用位置保证。

---

## 70. Mutex 使用边界

`streaming_turn_capture` 使用同步 `parking_lot::Mutex`，锁内操作刻意较短：

- 比较 ID。
- Append 小 Chunk。
- 改 Phase/Stamp。
- Clear Slot。
- `mem::take`。

可能做大字符串拼接的 `finalize_for_upload` 在锁外。

`turn_stream_drained` 锁只用于取放 `Option<Sender>`，不在锁内 Await。

这是异步 Rust 中应坚持的边界：

> 同步 Mutex 保护短小内存状态；任何可能 Await 或大计算的工作移出锁。

---

## 71. 代码阅读时最容易得出的五个错误结论

### 错误一：`Completed` Handler 会保存回答

不会。它清 Capture、记指标、放 Barrier；保存回答在 Turn Loop。

### 错误二：客户端看到 Chunk 就说明历史里已有 Chunk

不会。Chunk 是 Projection，不是 Conversation Commit。

### 错误三：`Failed` Handler 会决定是否 Retry

不会。Sampler 内部 Retry 在此前已决定；Session 级恢复由 Completion Error Handler 决定。

### 错误四：所有 Retry 都在同一层

不会。Sampler Attempt Retry 与 Session Resubmit 是两层。

### 错误五：成功 Turn 的 Capture 总为空

普通成功为空；Doom Recovery 成功仍可能保留旧失败 Segment。

---

## 72. 如果修改这段代码，应维护的 Checklist

### 新增 Sampling Event

- 它只是展示/观测，还是需要 Turn 语义决策？
- 是否必须进入 Replay Buffer？
- 是否影响 Capture Phase？
- 是否必须排在 Completed Barrier 前？
- 是否要携带 Request ID/Chunk Index？

### 修改 Completed

- Barrier 是否仍只释放一次？
- 之前所有 Chunk 是否已经取得 Event ID？
- 当前提交 Slot 是否清掉？
- 旧失败 Segment 是否仍保留？
- 是否错误地在 Event Path 写了 Chat State？

### 修改 Failed

- Capture 是否仍保留？
- Terminal Notification 是否会与 Completion Path 重复？
- Auth/Compaction 是否仍只执行一次？

### 修改 Retry

- 这是 Sampler Attempt 还是 Session Resubmit？
- 已输出内容是否允许重采样？
- Workflow Output Budget 是否被破坏？
- Credential Missing 是否错误消耗预算？
- 是否有 Runaway Guard 和 Pace？

### 修改 Capture

- 新字段是否参与 `is_empty`？
- Segment Fold 与 Clear Commit 是否语义正确？
- 8 MB Cap 是否跨 Segment？
- UTF-8 截断是否安全？
- JSON 是否向后兼容？
- 是否仍绝不进入 Chat State？

---

## 73. 推荐调试顺序

遇到“回答重复、工具乱序、Retry 后历史异常”时，按以下顺序查：

1. 用 Request ID 列出 Sampler Event 顺序。
2. 确认 Completion Result 是 Success 还是 Error。
3. 检查 `Completed` 是否被 Drainer 消费。
4. 检查 Barrier 是否正常触发或五秒超时。
5. 对比 Chunk `eventId` 与 canonical ToolCall `eventId`。
6. 检查 `record_assistant_response` 是否只调用一次。
7. 检查 Chat State Conversation Items 的实际顺序。
8. 若失败，检查 Capture 是否被 Take，以及 Prompt ID 是否匹配。
9. 若 401，检查 `SentCredential` 归因和 Auth Schedule Decision。
10. 若 Overflow，检查 Error Metadata Context Window 与 Estimated Total Tokens。

---

## 74. 推荐断点

### Event Path

- `spawn_session_actor` 中的 Drainer Loop。
- `handle_sampling_event` 每个 Match Arm。
- `send_update_full` 的 `generate_event_id`。
- Replay Buffer Flush。

### Completion Path

- `run_turn_via_sampler` Submit 返回处。
- Drain Barrier Await。
- `handle_sampling_failure`。
- Turn Loop 的 `SamplerTurnOutcome` Match。
- `record_assistant_response`。

### Capture Path

- `begin_turn`。
- `start_stream`。
- `push_current_segment`。
- `clear_current_segment`。
- `finalize_for_upload`。
- `TakeStreamingCapture` Command Arm。

---

## 75. 本篇建立的心智模型

可以把这一层记成四个平面。

### 75.1 Data Plane

Sampler Event：Chunk、Delta、Backend Tool、Metadata。

### 75.2 Control Plane

Completion Result：Success、Compact、Refresh Auth、Terminal Failure。

### 75.3 State Plane

Chat State：只接收 canonical Conversation Items。

### 75.4 Diagnostic Plane

Streaming Capture：只保存未提交 Generation，并上传 Trace。

Drain Barrier 则连接 Data Plane 与 Control Plane，保证“展示事件的本地排序边界”先于 Response 后续动作。

---

## 76. 与前几篇的连接

现在完整链路可以串起来：

```text
Prompt Assembly（源码精读 04）
  -> Skill Catalog / Loading（05）
  -> Tool Registry / Search（06）
  -> Agentic Loop（07）
  -> Chat State Actor（08）
  -> Sampler Actor / Attempt Retry（09）
  -> SamplingClient / HTTP / SSE（10）
  -> Session Event Bridge / Commit / Recovery（本篇）
```

前两篇回答“请求如何下去、流如何回来”；本篇回答“回来以后，什么只展示，什么能成为历史，以及错误怎样重新进入 Agent 循环”。

---

## 77. 本篇术语表（Glossary）

| 名词 | 白话解释 | 在本篇中的精确定义 |
| --- | --- | --- |
| ACP | Agent 与客户端之间的会话协议 | Shell 向客户端发送 Message、Thought、Tool Call 等 Session Update 的协议层 |
| Agentic Loop | 模型与工具反复交替的循环 | Build Request → Sample → Commit → Execute Tools → Append Results → 再 Sample |
| Attempt | 一次底层生成尝试 | Sampler 内部可因网络、空响应或 Doom Loop 产生多个 Attempt |
| Auth Gate | 判断某个 401 是否允许走某类认证恢复的门 | 结合 URL、认证模式与 Provider 上下文决定 Recovery Eligibility |
| Backoff | 重试前逐渐增长的等待 | Auth Credentialed Retry 使用 1s、2s、4s |
| Backend Tool | Provider 在推理服务内部执行的工具 | 通过 `BackendToolStarted/Completed` Event 展示，不由本地 Tool Bridge Dispatch |
| Barrier | 两条并发路径之间的顺序闸门 | Turn Loop 等 Event Drainer 消费 Completed 后再发 Response 后续通知 |
| Canonical | 被系统当作权威、可供后续计算使用的版本 | 最终 `ConversationResponse` 及其提交到 Chat State 的 Items |
| Capture | 对未提交流内容的旁路记录 | `StreamingTurnCapture`，只用于诊断 Trace |
| Channel Token | 某个语义通道中的增量文本 | Reasoning 或 Text Chunk |
| Chat State | 模型 Conversation 的权威状态机 | 下一次 Build Request 的历史来源 |
| Chunk Index | 一次模型流内部的块序号 | 用于 Chunk 跟踪与缓冲，不等于 Session Event ID |
| Commit | 把最终结果写入权威 Conversation | `record_assistant_response` / 其他 Item Push，不是客户端显示 Chunk |
| Compaction | 把过长历史压缩成更短表示 | Overflow 后修改 Chat State，再重建请求 |
| Completion Channel | 一次 Submit 返回最终 Result 的控制通道 | `submit_and_collect` / OneShot 所在路径 |
| Context Overflow | 输入历史超过模型上下文窗口 | 本篇依据结构化 Context Window 与 Token Estimate 判断 |
| Conversation Item | 模型协议历史中的一个条目 | Assistant、Reasoning、Tool Result 等 |
| Credential Attribution | 判断失败请求是否真的带了认证信息 | `SentCredential::{Missing, Sent, Unknown}` |
| Data Plane | 搬运实时数据的路径 | Sampling Event 到 UI、Capture 和指标 |
| Debounce | 等短时间以合并高频事件 | Replay Buffer 减少碎片通知 |
| Doom Loop | 模型输出高置信重复模式 | Sampler 可中止当前 Generation 并 Resample |
| Drain | 把 Channel 中已有事件按序消费完 | Drainer 处理到 Completed 表示此前 Chunk 已进入 Session 通知路径 |
| Drainer | 持续读取 Event Receiver 的 Task | 调用 `handle_sampling_event` 的单消费者 Local Task |
| ETag | 服务端资源版本标签 | Model Metadata 变化时提示 Models Manager 刷新 |
| Event ID | Session 通知全局顺序编号 | 在 `send_update` 时生成，供客户端去重与排序 |
| Fail Closed | 不确定时选择终止或计入预算 | 例如 Credential Attribution Unknown 按 Charged Retry 处理 |
| Fallback Text | 最终响应有文本但流中没发正文时补发的内容 | Turn Loop 从 canonical response 计算并通知客户端 |
| Generation | 从一个 StreamStarted 到终止的一次模型生成 | 同一 Prompt 可因 Retry 有多个 Generation |
| Hosted Tool | 由模型服务托管执行的工具 | 本篇与 Backend Tool 同一类概念 |
| Inference | 模型推理 | 从提交请求到生成响应的过程 |
| ITL | Inter-token Latency | 相邻输出 Token/Chunk 间延迟统计 |
| Lazy Begin | 缺少正常开始事件时按首 Chunk 补初始化 | ChannelToken 发现 Capture 无 Prompt ID 时的容错 |
| LocalSet | Tokio 中运行 `!Send` Future 的本地任务集合 | Session Drainer 运行环境的一部分 |
| Metadata | 不属于正文但影响状态/展示的数据 | Token、Model、Context Window、Timestamp、Signature 等 |
| OneShot | 只能发送一次结果的异步通道 | Barrier 和 Submit Completion 都使用这一模式 |
| Out-of-band | 不进入主业务状态的旁路 | Streaming Capture 不进入 Chat State |
| Phase | 模型被打断前最后所处阶段 | Pending、Reasoning、ResponseText、ToolCall |
| Projection | 权威状态面向某个消费者的展示 | 流式 Chunk 是客户端 Projection，不是 Conversation 本体 |
| Prompt ID | 一次用户 Prompt 的身份 | 可覆盖多个模型请求和 Tool Loop |
| Replay Buffer | 暂存、合并并可重放通知的缓冲器 | 高频 ACP/xAI Update 的 Session Actor 中间层 |
| Request ID | 一次 Sampler Submit 的身份 | 用于关联 Sampling Event 与日志；不同于 Prompt ID |
| Resample | 丢弃当前失败 Generation 后重新生成 | 常见于 Doom Loop Recovery |
| Resubmit | Session 外层重新提交一份新请求 | 常见于 Compaction 或 Auth Recovery 后 |
| Retry State | 给客户端显示的重试生命周期 | Retrying、Failed、Exhausted 等 xAI Extension |
| Runaway Guard | 防止理论上“不收费”的重试无限循环的上限 | Missing Credential 连续拒绝最多 50 次 |
| Sampling Event | Sampler 发布的增量生命周期事件 | StreamStarted、Token、Retrying、Failed、Completed 等 |
| Segment | Capture 中一个已折叠的未提交 Generation | 保存正文、Chunk Count、Phase 与 Doom Stamp |
| Session Actor | 串行处理 Session 命令和通知的运行体 | 连接 Agent、Chat State、Sampler、Persistence 与 Client |
| Signal | 面向观测/质量系统记录的离散事实 | First Token、Error Type、Tool Success、Doom Recovery 等 |
| SSE | Server-Sent Events | SamplingClient 接收流式响应的 Wire Framing |
| Stamp | 附着在 Capture 上的诊断标记 | Doom Loop Action 或 Empty Response Token Metadata |
| Stream Drain Barrier | 成功响应后的事件消费顺序屏障 | Completed Event 触发，Turn Loop 最多等待五秒 |
| Streaming Partial | 流出但未进入 canonical history 的内容 | 上传为 `streaming_partial.json` |
| Suspend | 机器休眠导致 Wall Clock 大幅超过 Monotonic Clock | Auth Incident 可有限次数重置 Retry Budget |
| Terminal Error | 当前 Turn 不再自动恢复的错误 | 发送失败状态并向上返回 ACP Error |
| Tool Bridge | Shell 本地 Tool 注册、权限与 Dispatch 层 | 只执行最终 Response 中完整的本地 Tool Call |
| Tool Call Delta | Tool 参数在流中的增量片段 | 只供展示，不可直接执行 |
| Trace Artifact | 为调试、回放或分析上传的文件 | `streaming_partial.json` 是其中一种 |
| TTFT | Time To First Token | 从请求开始到首 Token 的延迟 |
| Turn | 一次用户 Prompt 驱动的完整 Agent 工作单元 | 可包含多次 Sampling、Tool Calls 与 Recovery |
| Typed Error | 带结构化 Kind/Metadata 的错误 | 允许 Session 不依赖文案做恢复决策 |
| Unbounded Channel | 没有固定容量上限的异步队列 | Sampler Event Producer 不因 Session Drainer 容量等待 |
| Uncharged Resubmit | 请求未带 Credential 时不占三次认证重试预算的重提 | 仍受 Pace 与 50 次 Runaway Guard 限制 |
| Wire-valid | 当前凭据已经可以实际放到网络请求上 | Auth Refresh 后可能短暂尚未满足 |

---

## 78. 最终总结

这一层的核心不是“把 Event 转发给 UI”，而是建立四条严格边界：

1. **实时展示与权威历史分离**：Chunk 可以立即展示，只有最终 Response 能写 Chat State。
2. **事件观察与语义恢复分离**：Failed Event 只记录；Completion Error 才决定 Compact、Refresh Auth 或终止。
3. **并发任务与通知顺序协调**：Completed Drain Barrier 让所有流 Chunk 的 Event ID 先于 canonical Tool Call。
4. **失败诊断与模型上下文隔离**：未提交 Generation 进入 `streaming_partial.json`，永不污染下一轮 Prompt。

当这四条边界同时成立时，一次模型调用即使经历流式输出、Doom Resample、401、Context Overflow、Cancel 或 Tool Loop，仍能满足三个最终不变量：

```text
客户端尽量看到及时且有序的过程；
Chat State 只看到完整且唯一的事实；
失败现场仍可被诊断，但不会成为模型历史。
```
