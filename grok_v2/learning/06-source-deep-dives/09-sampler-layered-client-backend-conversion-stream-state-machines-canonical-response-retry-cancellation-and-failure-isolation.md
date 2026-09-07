# 源码精读 09：Sampler 如何统一三种 API、流式状态机、重试与失败隔离

> 本篇继续深入 Agentic Loop 的模型调用边界 `xai-grok-sampler`：同一份 `ConversationRequest` 如何转换成 Chat Completions、Responses 与 Anthropic Messages 三种协议；原始 SSE Chunk 如何被三个 Layer-2 状态机归一化为 `SamplingEvent`；Actor 如何并发承载请求、隔离每次 Attempt、实施重试、图片降级、Doom Loop 恢复与取消；最终为什么只有被接受的 `ConversationResponse` 才能进入 Chat State。
>
> 源码基线：`ed6d543`。源码变化后优先按本文列出的类型、函数和测试名重新定位。

---

## 1. 先给结论：Sampler 不是一个 HTTP Client

如果只把 Sampler 理解成“发 HTTP 请求并读 SSE”，会漏掉它最关键的责任。

Sampler 实际上是五层边界的组合：

```text
ConversationRequest
        |
        v
SamplerHandle / SamplerActor       请求并发、配置快照、取消、生命周期
        |
        v
Per-request Retry Loop             Attempt 隔离、重试、降级、终态裁决
        |
        v
SamplingClient                     认证、Header、Endpoint、三种 Wire Request
        |
        v
Raw Backend Stream                 Chat Chunk / Response Event / Message Event
        |
        v
Layer-2 Stream Transform           累加文本、Reasoning、Tool Call、Usage
        |
        v
SamplingEvent + ConversationResponse
```

它同时回答：

1. 这次请求使用哪个 Backend？
2. 怎样把 canonical Conversation 转成该 Backend 的 Wire Schema？
3. 怎样把不同 SSE Event 统一成一种下游事件协议？
4. 哪些失败应重试、等待、换 HTTP/1.1、去图或交给 Session？
5. 一个失败 Attempt 已经流出部分文本时，哪些数据可以展示，哪些不能提交？
6. 多个请求并发、取消或 Actor 退出时，任务由谁回收？

---

## 2. 本篇主线文件

| 文件 | 责任 |
| --- | --- |
| `xai-grok-sampler/src/lib.rs` | 三层公开 API 与模块边界 |
| `xai-grok-sampler/src/config.rs` | Sampler、Retry 与 Backend 配置 |
| `xai-grok-sampler/src/commands.rs` | Actor Command 协议 |
| `xai-grok-sampler/src/handle.rs` | 可 Clone Handle、提交、查询与 RAII 取消 |
| `xai-grok-sampler/src/actor/mod.rs` | Actor Run Loop、Active Request 与 JoinSet |
| `xai-grok-sampler/src/actor/request_task.rs` | 单请求 Attempt Loop、终态拦截与重试执行 |
| `xai-grok-sampler/src/client.rs` | HTTP Client、认证、Header、Endpoint 与 SSE 解码 |
| `xai-grok-sampler/src/retry.rs` | 纯错误分类与 Backoff 算法 |
| `xai-grok-sampler/src/events.rs` | 统一的 `SamplingEvent` 与 Error Info |
| `xai-grok-sampler/src/stream/chat_completions.rs` | Chat Completions Layer-2 状态机 |
| `xai-grok-sampler/src/stream/responses.rs` | Responses Layer-2 状态机 |
| `xai-grok-sampler/src/stream/messages.rs` | Messages Layer-2 状态机 |
| `xai-grok-sampler/src/stream/collect.rs` | 不需要流式 UI 时的终态收集器 |
| `xai-grok-sampler/src/metrics.rs` | TTFB、TTLB 与 ITL 指标 |
| `xai-grok-sampler/src/doom_loop.rs` | Responses SSE 的 Doom Loop 信号收集器 |
| `xai-grok-sampling-types/src/conversation/*.rs` | Canonical Conversation 与三种 Wire Schema 互转 |
| `xai-grok-sampling-types/src/error.rs` | Rich Error、错误性质与重试辅助判断 |

---

## 3. 先建立三层 API 心智模型

`xai-grok-sampler/src/lib.rs` 明确把 API 分为三层。

### 3.1 Layer 1：Raw Client

`SamplingClient` 负责：

- 构建 URL、Headers 和 Request Body。
- 应用认证和动态 Bearer Token。
- 发出 HTTP 请求。
- 把 SSE Frame 解码成 Backend 自己的类型。

它返回的仍是：

```text
ChatCompletionChunk
ResponseStreamEvent
MessageStreamEvent
```

这一层不负责 Agent Chat State，也不决定最终是否重试。

### 3.2 Layer 2：Stream Transform

`stream_chat_completions`、`stream_responses`、`stream_messages` 把 Backend Event 转为统一的 `SamplingEvent`。

它们还会累加最终响应：

- 拼接文本。
- 拼接 Reasoning。
- 合并 Tool Call 参数碎片。
- 收集 Stop Reason、Usage 和 Provider Metadata。
- 生成 canonical `ConversationResponse`。

### 3.3 Layer 3：Actor API

`SamplerHandle` 与 `SamplerActor` 负责：

- 多请求并发。
- 每请求 Cancellation Token。
- 默认配置与 per-request override。
- Attempt 重试。
- 对外共享 Event Channel。
- 可选 Completion Oneshot。

最重要的分工是：

> Layer 2 判断“一次流怎样结束”，Layer 3 判断“这次结束能否成为整个请求的正式结果”。

---

## 4. 为什么 canonical Conversation 必须位于 Wire Schema 之上

三种 API 对同一语义有完全不同的表达：

| Canonical 语义 | Chat Completions | Responses | Messages |
| --- | --- | --- | --- |
| System | `role=system` Message | System Input Item | 独立 `system` 参数 |
| User Text/Image | User Message Blocks | Easy Input Content | User Content Blocks |
| Assistant Text | Assistant Message | Assistant Input Message | Assistant Text Block |
| Reasoning | 下一条 Assistant 的 `reasoning_content` | 顶层 Reasoning Item | Thinking Block |
| Function Tool Call | Assistant `tool_calls` | FunctionCall Item | ToolUse Block |
| Tool Result | `role=tool` | FunctionCallOutput Item | User ToolResult Block |
| Backend Tool Call | 无原生等价物 | 原生顶层 Item | 无原生等价物 |

如果 Chat State 直接保存某一种 Provider Message：

- 切换 Backend 会非常困难。
- Provider 独有字段会污染 Agent 层。
- Tool Pair 修复和 Compaction 要写三套。
- 一次响应无法稳定重放到另一种协议。

因此 `ConversationRequest` 和 `ConversationResponse` 是内部事实模型，三种 Wire Schema 是边界投影。

---

## 5. 整条请求链路

```text
Session builds ConversationRequest
        |
        | submit(request_id, request)
        v
SamplerHandle
        |
        | SamplerCommand::Submit
        v
SamplerActor
        |
        | snapshot effective config
        | register CancellationToken
        | JoinSet::spawn
        v
run_request_task
        |
        | loop: run_one_attempt
        v
SamplingClient conversation_stream_*
        |
        | Raw Backend Stream
        v
stream_chat_completions / responses / messages
        |
        | nonterminal SamplingEvents are forwarded
        | terminal event is intercepted
        v
drive_l2 -> AttemptOutcome
        |
        +--> retry / degrade / cancel
        |
        +--> one accepted Completed
                    |
                    +--> shared SamplingEvent::Completed
                    +--> optional completion oneshot
```

注意图中有两个“Completed”：

1. Layer-2 的 Completed：只代表某个 Attempt 的流完整结束。
2. Request Task 的 Completed：代表整个 Retry Loop 接受了最终结果。

源码通过拦截前者、重新发出后者来隔离失败 Attempt。

---

## 6. `SamplerHandle` 为什么只是 Sender

`SamplerHandle` 的核心字段只有：

```rust
mpsc::UnboundedSender<SamplerCommand>
```

所以 Clone Handle：

- 不会 Clone HTTP Client。
- 不会 Clone Active Request Map。
- 不会创建新 Actor。
- 只是增加一个 Command Sender。

Session 的不同控制流可以共享 Handle，但所有配置更新、提交和取消最终都进入同一个 Actor Command Stream。

---

## 7. 三种提交方式

### 7.1 `submit`

使用 Actor 当前默认配置，结果只通过共享 Event Channel 返回。

适合正常 Agent Turn：UI 本来就需要逐个消费流式事件。

### 7.2 `submit_with_config`

为单个请求附带完整 `SamplerConfig` Override。

Actor 在接收 Command 时决定：

```text
Command.config exists
  -> use override
else
  -> clone actor current config
```

这意味着运行中的请求拥有自己的配置快照；后续 `UpdateConfig` 不会修改已经 Spawn 的任务。

### 7.3 `submit_and_collect`

它同时保留两条输出路径：

```text
Shared Event Channel   -> 仍然可做实时显示
Completion Oneshot     -> 调用者 await 最终 Result
```

Compaction、Summary、`/btw` 等顺序调用者不用自己从共享 Channel 中按 Request ID 过滤终态。

---

## 8. `submit_and_collect` 的 RAII 取消

方法内部定义 `CancelOnDrop`：

```text
future 正常完成
future 被上游 select! 丢弃
future 所在任务 panic/unwind
        |
        v
CancelOnDrop::drop
        |
        v
SamplerCommand::Cancel(request_id)
```

只有 Submit 成功进入 Channel 后才 Arm Guard。

正常完成时 Guard 也会发送 Cancel，但请求通常已从 Active Map 移除，所以是安全 No-op。

这是一种很实用的异步资源所有权模式：

> Awaiting Future 的生命周期就是远端任务的租约；Future 消失，租约自动撤销。

---

## 9. Actor 如何同时“串行管理”与“并发采样”

`SamplerActor` 本身逐条处理 Command，但真正的模型请求被放进 `JoinSet<RequestId>`：

```text
single actor task
  ├── handle Submit A -> spawn task A
  ├── handle Submit B -> spawn task B
  ├── handle Cancel A
  └── join completed tasks

request tasks
  ├── A: HTTP/SSE/retry
  └── B: HTTP/SSE/retry
```

因此：

- Active Map 的修改仍是串行的。
- 网络等待不会阻塞 Actor 接收 Cancel。
- 多个请求可以同时 Streaming。
- 每个请求的 Retry State 独立。

---

## 10. `biased select!` 为什么先清理 JoinSet

Actor Run Loop 优先选择 `tasks.join_next()`。

这样做不是为了提高模型速度，而是为了缩短状态陈旧窗口：

```text
request task 已结束
但 actor 还没 remove active_requests[id]
```

如果 Command 长时间持续到达而 Join 分支没有优先级，`is_active` 和 `active_count` 可能更久地看到已经结束的请求。

---

## 11. 重复 Request ID 的语义

收到 `Submit` 时，Actor 用 Request ID 注册新的 `ActiveRequest`。

如果旧 ID 已存在：

1. 新 Token 进入 Active Map。
2. 旧 Token 被 Cancel。
3. 新任务继续执行。

这避免相同逻辑请求留下两个无人管理的流。

但它不是数据库式的“同 ID 自动去重并返回旧结果”；它的语义更接近：

> 新提交取代旧提交。

---

## 12. Actor Shutdown 的所有权闭环

当所有 Handle 被释放，Command Receiver 返回 `None`，Actor 退出主循环。

退出前：

1. Drain Active Request Map。
2. Cancel 每个 Request Token。
3. `tasks.shutdown().await`。

因此 Per-request Task 不会因为 Actor 的 Command 入口消失而继续成为孤儿任务。

---

## 13. `run_request_task` 拥有什么

每个请求任务独占：

- Request ID。
- 可变的 `ConversationRequest` Clone。
- Effective `SamplerConfig`。
- `RetryPolicy`。
- `SamplingClient`。
- Transport Retry Counter。
- Doom Retry Counter。
- Cancellation Token。
- 可选 Completion Sender。

可变 Request 很重要，因为图片降级会修改它；这项修改只影响当前请求及其后续 Attempt，不会回写 Chat State 中的 canonical Conversation。

---

## 14. 配置错误为什么在 Attempt Loop 外失败

`SamplingClient::new(config.clone())` 在进入 Loop 前执行。

若 Base URL、Header 或其他 Client 配置无效：

- 发出最终 `SamplingEvent::Failed`。
- 向 Completion Oneshot 发送 `Err`。
- 立即返回。

相同配置原样重试不会变好，所以它不是 Attempt 级瞬态失败。

---

## 15. Retry Budget 的来源

最大重试次数按以下来源解析：

```text
GROK_MAX_RETRIES environment override
        > model / Sampler config
        > DEFAULT_MAX_RETRIES = 15
```

但源码对显式 `0` 做了特别保护。

如果调用者明确配置 `0`：

- 不允许环境默认把它重新变成 15。
- 所有非 Session-owned 错误都会直接 Fatal。
- 甚至 413 去图重试也不会发生，因为 `max_retries == 0` 的判断早于图片分支。

---

## 16. 两套 Retry Counter

`run_request_task` 维护：

```text
retry_count       transport / 5xx / 429 / empty / image recovery
doom_retry_count  doom-loop resampling only
```

它们互不借用预算。

成功时：

```text
metrics.attempts = retry_count + doom_retry_count + 1
```

所以 `attempts=1` 表示一次成功，没有任何重试。

---

## 17. 一次 Attempt 的五种结果

`AttemptOutcome` 把复杂流归约成五类：

| Variant | 含义 |
| --- | --- |
| `Completed` | Layer 2 完成且响应被判为非空 |
| `Empty` | 流正常完成，但没有可见文本或 Tool Call |
| `Failed` | 流内失败，带可分类 Rich Error |
| `Cancelled` | Cancellation Token 抢先触发 |
| `InitFailed` | 还没拿到 Raw Stream 就失败 |

这个 Enum 是 Retry Loop 与三套 Stream Parser 之间的隔离层。

---

## 18. `run_one_attempt` 的 Backend 分派

```text
ApiBackend::ChatCompletions
  conversation_stream
  -> stream_chat_completions

ApiBackend::Responses
  conversation_stream_responses
  -> stream_responses_tracked

ApiBackend::Messages
  conversation_stream_messages
  -> stream_messages
```

三条分支的前半段类型不同，后半段都进入 `drive_l2`。

这就是 Backend 差异被压缩的位置。

---

## 19. `tee_errors` 为什么存在

Raw Stream 的错误类型是 Rich `SamplingError`，其中可能包含：

- `reqwest::Error`。
- Status、Retry-After 与 `x-should-retry`。
- Credential Provenance。
- Provider Metadata。

Layer 2 为了把错误发到统一 Event Channel，会把它转成可 Clone、可序列化的 `SamplingErrorInfo`。

Retry Classifier 却更适合接收原始 `SamplingError`。

`tee_errors` 因此：

1. 原样把 Stream Item 继续交给 Layer 2。
2. 将遇到的第一个 Raw Error Clone 到共享 Cell。
3. `drive_l2` 收到 `Failed` 时优先取回 Raw Error。

只捕获第一个错误，因为连接撕裂后的后续错误通常只是次生噪声。

---

## 20. 为什么需要 `synthesize_from_info`

不是每个 Layer-2 Failure 都来自 Raw Stream Error，例如：

- 本地 Idle Timeout。
- Responses 的 `ResponseFailed`。
- Messages 的 `Error` Event。
- 缺失 Terminal Event。
- 本地 Max Tokens 判定。

此时 Error Cell 为空，`drive_l2` 必须根据 `SamplingErrorInfo` 重建一个 Rich Error。

这里最关键的不变量是：

> 重建不能改变错误的重试性质。

例如 Serialization 必须重建为 `Serialization`，不能偷懒改成可重试的 `EventStreamError`，否则同一坏响应会烧完整个 Retry Budget。

---

## 21. Layer-2 Terminal Event 为什么不能直接转发

`drive_l2` 对事件分两类：

```text
Nonterminal
  -> forward to shared event channel

Completed / Failed
  -> intercept
  -> return AttemptOutcome
```

如果直接转发每个 Attempt 的 Completed：

- Session 会把失败 Attempt 当作正式 Assistant Response。
- Chat State 可能追加多条互相冲突的回答。
- Retry 后的 Tool Call 可能和旧 Tool Call 混在一起。
- Token Usage 会重复累计。

所以 Request Task 只有在整个 Loop 接受结果后，才重新发出唯一的 `SamplingEvent::Completed`。

---

## 22. 失败 Attempt 的部分 Token 会怎样

非终态事件已经实时转发，因此某次 Attempt 在中途断流前产生的：

- Text Chunk。
- Reasoning Chunk。
- Tool Call Delta。
- Backend Tool Progress。

可能已经被 UI 或诊断逻辑看到。

但是该 Attempt 的 canonical `ConversationResponse` 不会被提交。

要区分两种一致性：

| 层面 | 保证 |
| --- | --- |
| 展示流 | 低延迟，可能观察到后来失败的部分输出 |
| 正式对话历史 | 只接收 Retry Loop 最终接受的一份 Response |

这正是 Streaming UX 与 Conversation Integrity 之间的边界。

---

## 23. `retry_only_before_output` 的意义

任务共享一个 `AtomicBool output_observed`。

以下事件会把它设为 `true`：

- First Token。
- Text / Reasoning Token。
- Tool Call Delta。
- Backend Tool Started / Completed。
- Attempt Completed。

Responses Parser 还会根据原始 Response Event 提前标记可能已产生输出的事件。

当 Policy 开启 `retry_only_before_output` 且已经有输出：

```text
effective_max_retries = 0
```

于是之后的普通失败直接收敛，不会在用户已看到内容后悄悄换一份新采样。

---

## 24. Empty Response 不是 Parser Error

Layer 2 可以正常结束并产生一个结构合法、但对 Agent 无用的 Response。

`drive_l2` 调用 `response.empty_reason()` 区分：

- `ReasoningOnly`：有 Reasoning，没有可见文本或 Tool Call。
- `NoVisibleContent`：有响应结构，但 Assistant 为空且无 Tool Call。

随后构造 `EmptyResponseContext`，记录：

- 是否有 Reasoning。
- Content 长度。
- Tool Call 数量。
- Finish Reason。
- Token Usage。
- Model。
- 是否见到 Choice。

Empty Response 被视为可重试的采样失败，而不是 JSON 解析失败。

---

## 25. Content Filter 为什么允许空响应

如果 Stop Reason 是 `ContentFilter`，空内容是 Provider 的确定性拒绝结果。

源码不会把它归为 Empty Response。

否则相同 Prompt 可能不断触发：

```text
refusal -> empty retry -> refusal -> empty retry ...
```

因此 Content Filter 的空响应可以成为正式 Completed。

---

## 26. Length 为什么变成失败

如果最终 `stop_reason == Length`，`drive_l2` 返回：

```text
SamplingError::MaxTokensTruncation
```

它不会提交被截断的 Assistant Response，也不会在 Sampler 内自动重试。

理由是重新发送相同请求和相同输出上限通常仍然截断；是否调整模型、Max Tokens 或做上层恢复属于更高层策略。

Messages Parser 还会在构造 Completed 前直接把 Length 映射为 Failed，`drive_l2` 的检查则提供统一后盾。

---

## 27. Chat Completions 请求转换

`conversation_to_chat_messages` 是 canonical 转换入口。

### 27.1 Reasoning 折叠

Canonical Conversation 中 Reasoning 是 Assistant 前的 sibling：

```text
Reasoning A
Reasoning B
Assistant C
```

Chat Completions Wire Format 没有顶层 Reasoning Item，所以转换为：

```text
Assistant {
  reasoning_content: "A\nB",
  content: C
}
```

如果中间出现 Backend Tool Call，Pending Reasoning 不会清空；其他类型 Item 会清空。

没有后续 Assistant 的尾部 Reasoning 会丢弃，因为 Wire Format 没有合法落点。

### 27.2 Backend Tool 降级

Chat Completions 没有 Responses 风格的 Backend Tool Item。

源码把它变成一个 synthetic Assistant Message，其中内容是 `text_summary()`。

它保留“后台做过什么”的语义，但不是无损往返。

### 27.3 Tool Arguments 清理

Assistant Tool Call 在转 Wire Request 前会经过 `sanitize_tool_arguments`。

这是为了避免历史中已损坏的参数 JSON 原样污染下一次请求。

### 27.4 Structured Output

存在 `json_schema` 时，构造 Strict JSON Schema Response Format。

只有存在 Tools 时才设置 `tool_choice`，避免向某些 OpenAI-compatible Server 发送“有 Tool Choice、却没有 Tools”的非法组合。

---

## 28. Responses 请求转换

Responses 是三条路径中最接近 canonical Conversation 的一种。

### 28.1 保持顶层顺序

`build_responses_input` 对每个 `ConversationItem` 执行 Flat Map。

Reasoning 保持顶层 Item，不折叠进 Assistant，目的是按服务端原始输出顺序重放，维持 Prefix Cache 命中。

### 28.2 Assistant 可能展开成多个 Item

一个 canonical Assistant：

```text
Assistant text + N function tool calls
```

会展开为：

```text
optional assistant message
function call 1
function call 2
...
```

空文本不会生成无意义 Message，但 Tool Calls 仍会保留。

### 28.3 Tool Result

无图片时使用 Text FunctionCallOutput。

有图片时使用 Content List，第一块为文本，后续为 Input Image。

### 28.4 Backend Tool 无损回放

Web Search、X Search、Code Interpreter 会回到对应的 Input Item。

因此 Responses 路径能比另外两条路径更完整地重放后端执行上下文。

### 28.5 Reasoning Patch

当前依赖类型在 Reasoning Text Content 上缺少 API 要求的 `type: reasoning_text`。

`patch_reasoning_text_types` 在 JSON 序列化后补 discriminator。

这展示了一类现实边界：

> 强类型 SDK 不一定完整表达最新 Wire Contract，客户端有时必须在 JSON 边界做最小补丁。

### 28.6 Hosted Tool 冲突

Function Tool 与 Hosted Tool 同名时，Hosted Tool 获胜，Function Tool 被丢弃并记录 Warning。

否则请求中两个同名工具会被服务端拒绝。

### 28.7 xAI 特有 Tool 注入

SDK 的 `rs::Tool` 没有 X Search Variant。

因此转换先收集 `extra_tool_entries`，Client 序列化后再把 Raw JSON 追加进 `tools` Array。

---

## 29. Messages 请求转换

Messages API 的结构不是“Message Item 一对一转换”，而是一个小型重组器。

### 29.1 System 独立

Canonical System Item 被收集到 `system_blocks`，不进入普通 `messages`。

### 29.2 Pending Assistant Blocks

Reasoning、Assistant Text、ToolUse 和 Backend Tool Summary 可以连续积累在 `pending_assistant` 中，最终 Flush 成一个 Assistant Message。

### 29.3 Tool Result 属于 User Role

Messages API 用 User Content Block 表示 Tool Result。

多个连续 Tool Result 会积累到 `pending_tool_results`，一起 Flush 为 User Message。

### 29.4 Reasoning 变 Thinking

Canonical Reasoning 的可读文本进入 `thinking`，`encrypted_content` 进入 `signature`。

即使其中一个为空，只要另一个存在，就构建 Thinking Block。

### 29.5 Backend Tool 降级

Messages 同样没有通用 Backend Tool Call Item，所以把 `text_summary()` 作为 Assistant Text Block。

### 29.6 Tool Call ID 清理

Tool Use ID 和 Tool Result ID 只保留字母数字、下划线与连字符；其他字符变成 `_`。

### 29.7 图片格式

- Data URI 被拆成 Base64 Media Type 与 Data。
- HTTP/HTTPS URL 变 URL Image Source。
- 无法识别的格式降级成文本提示。

### 29.8 Cache Breakpoint

源码最多主动占用三个断点方向：

- System 尾部。
- Conversation Tip。
- 上一轮边界附近。

Thinking Block 不允许携带 Breakpoint，所以算法向前扫描可承载 `cache_control` 的 Block。

普通 Text Message 必要时被提升成 Block Form。

---

## 30. 三种转换的损失对比

| 能力 | Chat Completions | Responses | Messages |
| --- | --- | --- | --- |
| Reasoning 文本 | 折叠，可保留 | 顶层保留 | Thinking 保留 |
| Reasoning 加密信息 | 通常不可完整表达 | 原生保留 | Signature 保留 |
| Backend Tool Call | Summary 降级 | 原生保留 | Summary 降级 |
| Tool Call | 原生 | 原生顶层 Item | ToolUse Block |
| Tool Result 图片 | Blocks | Content List | ToolResult Blocks |
| Prompt Cache 精细控制 | 无本地特殊重组 | Prompt Cache Key / 顺序重放 | Cache Breakpoints |
| Provider Message ID | 不进入最终统一字段 | 当前未设置 | 保留 |

“支持切换 Backend”不等于“三种格式完全无损等价”。

正确理解是：canonical 层尽量保真，每个 Adapter 明确处理无法表达的部分。

---

## 31. `apply_conversation_defaults`

在三种 `conversation_stream_*` 入口中，Client 都先填默认值：

- Model。
- Temperature。
- Top P。
- Max Output Tokens。

规则是只填 `None`，不覆盖 Request 已指定的值。

这样 Chat State 构建 Request 时可以只写本轮差异，Sampler 再应用 Client Defaults。

---

## 32. Trace 与 x-grok 标识为何单独搬运

Responses 和 Messages 转换时会先从 canonical Request 保存：

- Conversation ID。
- Request ID。
- Session ID。
- Turn Index。
- Agent ID。
- Trace。

然后把协议主体转换成 SDK Request，再写进 Wrapper。

这些字段属于 xAI 的传输、归因或诊断外壳，不一定是标准 Provider Request 类型的一部分。

---

## 33. `SamplingEvent` 是下游统一协议

主要事件分组：

### 生命周期

- `StreamStarted`
- `FirstToken`
- `Completed`
- `Retrying`
- `Failed`

### 内容

- `ChannelToken { Text | Reasoning }`
- `ToolCallDelta`
- `ReasoningCompleted`

### Provider Metadata

- `ModelMetadata`
- `ResponseStarted`

### Hosted Tool Progress

- `BackendToolCallStarted`
- `BackendToolCallCompleted`

Session 无需知道当前 Raw Stream 是哪个 Provider 类型。

---

## 34. 每个 Layer-2 Parser 的共同骨架

三套 Parser 都遵守：

```text
emit StreamStarted
emit optional ModelMetadata

loop:
  timeout(stream.next())
  decode event/chunk
  update accumulators
  emit nonterminal events

build ConversationResponse
emit exactly one Completed
```

任何错误路径：

```text
emit exactly one Failed
return
```

因此 Layer-2 Contract 是“恰好一个 Terminal Event”。

`collect_response` 和 `drive_l2` 都依赖这个不变量。

---

## 35. 两种 Idle Timeout

三套 Parser 都有外层 Per-next Timeout：

```text
timeout(idle_timeout, stream.next())
```

它检测连接完全不再给 Event。

另外还维护 `last_content_chunk_at`：

- 有意义的内容到达时重置。
- Ping、Keepalive 或空 Delta 不算进展。
- 虽然连接持续活着，但长期没有内容时仍然失败。

这防止 Provider 用心跳无限掩盖“模型已经卡死”。

Messages 的 Ping 明确是 Liveness-only；Responses 有自己的 meaningful-content 分类；Chat Completions 根据 Choice、Finish Reason 和 Delta 判定。

---

## 36. Chat Completions Parser 的累加器

`stream_chat_completions` 保存：

- Model 与 Fingerprint。
- Usage 与 Cost。
- Finish Reason。
- `content_acc`。
- `reasoning_acc`。
- 按 Tool Index 排序的 `tool_call_acc`。
- Chunk Index 与 Text Chunk Count。
- Content Timestamp。

`BTreeMap<u32, ...>` 让最终 Tool Call 按 Provider Index 稳定输出。

---

## 37. Chat Text 与 Reasoning 的流式事件

第一次非空 Text 或 Reasoning 到达时，只发一次 `FirstToken`。

随后：

```text
Text delta
  -> append content_acc
  -> chunk timestamp
  -> message_chunk_count += 1
  -> ChannelToken(Text)

Reasoning delta
  -> append reasoning_acc
  -> ChannelToken(Reasoning)
```

`message_chunks_emitted` 只统计 Text Chunk，而不是 Reasoning Chunk。

这个字段供下游检测“最终 Response 有文本，但流式消息可能丢失”的情况。

---

## 38. Chat Tool Call Delta 如何重组

每个 Tool Index 保存三段状态：

```text
(id, name, arguments_buffer)
```

首个 Delta 通常带 ID 与 Function Name，后续 Delta 只带 Argument Fragment。

Parser 一边更新 Accumulator，一边发出 `ToolCallDelta`，所以：

- UI 能渐进显示。
- 最终 Response 得到完整 Tool Call。

如果存在 Tool Calls，即使 Provider 忘记给 `finish_reason=tool_calls`，Parser 也会把 Stop Reason 覆盖为 `ToolCalls`。

---

## 39. Chat 最终 Response

最终 Items 顺序：

```text
optional synthesized Reasoning
trailing Assistant
```

如果一个 Choice 都没见到，仍会构造空 Assistant，使 Response 结构统一；上层 `drive_l2` 再把它判为 Empty。

这体现了职责分离：

- Parser 负责结构化结束。
- Retry Task 负责业务可接受性。

---

## 40. Responses Parser 为什么依赖 Final Response Event

Responses SSE 同时提供：

- 细粒度 Delta Event。
- 最终完整 `ResponseCompleted` 或 `ResponseIncomplete`。

Parser 用 Delta 做实时通知，但用 Final Response 构建 canonical Items。

若流结束却没有 Completed/Incomplete：

```text
Failed(Api 500: No ResponseCompleted...)
```

因为仅凭部分 Delta 无法可靠恢复所有顶层 Output Item、Usage 和 Status。

---

## 41. Responses Text 与两类 Reasoning Delta

Responses Parser 处理：

- `ResponseOutputTextDelta` -> Text Channel。
- `ResponseReasoningSummaryTextDelta` -> Reasoning Channel。
- `ResponseReasoningTextDelta` -> Reasoning Channel，并写入 fallback accumulator。

最后如果 Final Response 的 Reasoning Item 没有 Summary/Content，`inject_streaming_reasoning_fallback` 会把累积文本补进去。

这处理了“流中有 Reasoning，终态对象却没完整回显”的 Provider 差异。

---

## 42. Responses Tool Index 映射

Responses 的 Function Call Delta 引用 `output_index`，而统一事件要求 Tool-only `tool_index`。

Parser 维护：

```text
output_index -> tool_index
```

在 `ResponseOutputItemAdded(FunctionCall)` 时：

1. 分配连续 Tool Index。
2. 发送 ID 与 Name。
3. 保存映射。

后续 Argument Delta 查映射并发送 Fragment。

这样 Text、Reasoning 等 Output Item 穿插时，不会造成 Tool Index 空洞或错位。

---

## 43. Responses Backend Tool 生命周期

Hosted Tool 由服务端执行，客户端不进入本地 Tool Dispatcher。

Parser 只发进度事件：

```text
WebSearchCallInProgress
  -> BackendToolCallStarted(web_search)

CustomToolCallInputDone
  -> BackendToolCallStarted(x_search)

CodeInterpreterCallInProgress
  -> BackendToolCallStarted(code_interpreter)

OutputItemDone(full item)
  -> BackendToolCallCompleted(name, serialized result)
```

最终完整 Backend Tool Item 还会通过 `response_to_conversation_items` 进入 canonical Response，以便后续 Turn 重放。

---

## 44. Responses 的 Server-side Failure

`ResponseFailed` 与 `ResponseError` 被转换成 `SamplingError::Api { status: 500 }`。

这是有意让 Retry Loop 把它们当成可能的 Server-side Transient Failure。

这里的 500 是客户端的统一分类载体，不一定是另一个真实 HTTP Response Status；错误本身已经出现在成功建立的 SSE Stream 内。

---

## 45. Responses Usage 的双重语义

源码注释区分：

- Prompt、Completion、Cached、Reasoning Token：累计 Billing 值。
- `total_tokens`：当前活跃 Context Length，用于 `/context` 与 Auto Compact。

SSE 解码器在后端提供 `context_details` 时，会重写 Total Token，使它表达实时上下文长度；旧部署则保留 Wire Value。

因此“总计费 Token”和“当前上下文占用”不能简单视为同一概念。

---

## 46. Responses Cost 与 Metadata

Cost 通过 Metadata 中的内部 Key 携带。

Parser 构造 Response 时：

1. 从 Metadata 删除该 Key。
2. Parse 为 `i64 cost_usd_ticks`。
3. 写入 canonical `ConversationResponse`。

这样内部计费信息不会继续伪装成普通 Provider Metadata。

---

## 47. Responses Stop Reason

优先级：

```text
has function tool calls -> ToolCalls
else status Completed   -> Stop
else status Incomplete  -> Length
else                    -> None
```

因此实际 Tool Call 比宽泛的 Response Status 更能决定 Agent Loop 下一步。

---

## 48. Messages Parser 为什么按 Block Index 保存状态

Messages Stream 是：

```text
ContentBlockStart(index, type)
ContentBlockDelta(index, delta)
ContentBlockStop(index)
```

所以 Parser 使用：

```text
BTreeMap<u32, BlockState>
```

`BlockState` 按类型保存：

- Text Buffer。
- Thinking Buffer 与 Signature。
- Tool Name、ID 与 Argument Buffer。

Block Stop 才把局部状态折入最终 Assistant 累加器。

---

## 49. `ResponseStarted` 为什么只有 Messages 发

Messages 的 `MessageStart` 在任何内容前就给出：

- 真实 Message ID。
- Model。
- Input Token。
- Cache Read Token。
- Cache Creation Token。

Parser 立即发 `SamplingEvent::ResponseStarted`，让 Partial-mode Consumer 能按 Wire 顺序输出真实 `message_start`。

Chat Completions 与 Responses 在同一阶段没有完全对应的数据，所以不伪造该事件。

---

## 50. Messages Thinking Block

Thinking Start 会创建 `BlockState::Thinking`。

Thinking Delta：

- 追加可读 Reasoning。
- 发 `ChannelToken(Reasoning)`。

Signature Delta：

- 更新加密 Signature。
- 不作为普通文本显示。

Thinking Block Stop：

1. 若 Signature 非空，发 `ReasoningCompleted`。
2. 构造 canonical Reasoning Item。
3. 将 Summary 与 Encrypted Content 分别保留。

`ReasoningCompleted` 的时序保证 Partial Consumer 能在 Block Stop 前输出 Signature Delta。

---

## 51. Redacted Thinking 的当前边界

Parser 可以反序列化 `RedactedThinking`，避免整条流因未知 Block 失败。

但它不把 opaque data 发成 `SamplingEvent`，也不放入最终 canonical Response。

这是明确的“Parse-only Support”：

- 保证兼容读取。
- 不宣称下游已经支持显示或重放。

文档阅读时不要把“Enum 中存在 Variant”误解成“端到端功能已完成”。

---

## 52. Messages Text Block 的 Finalize

Text Delta 实时发送并累加在对应 Block。

Block Stop 时：

- 非空 Block Text 追加到 `assistant_text`。
- 多个 Text Block 之间插入换行。

因此流式 Chunk 的边界不等于最终文本的语义边界；最终 Response 按 Content Block 重建。

---

## 53. Messages ToolUse Block

Start Event：

- 分配 Tool-only Index。
- 保存 Block Index -> Tool Index。
- 发 ID 与 Name。

`InputJsonDelta`：

- 直接从空 String 开始累加。
- 每个 Fragment 发 `ToolCallDelta`。

不能从 `"{}"` 开始再追加 Fragment，否则最终 JSON 会变成非法拼接。

Block Stop：

- 构造完整 canonical `ToolCall`。

---

## 54. Messages Stop Reason 映射

| Wire Stop Reason | Canonical Stop Reason | 说明 |
| --- | --- | --- |
| `end_turn` | `Stop` | 正常结束 |
| `max_tokens` | `Length` | 输出截断 |
| `stop_sequence` | `Stop` | 同时保留匹配序列 |
| `tool_use` | `ToolCalls` | 进入工具循环 |
| `refusal` | `ContentFilter` | 确定性拒绝 |
| `pause_turn` | `Stop` | 当前实现不自动续发 |
| `model_context_window_exceeded` | `Length` | 输出侧超限 |
| unknown | `Stop` | Warning 后保守结束 |

源码还保留 Raw Stop Reason、Stop Message 和 Stop Sequence，避免统一 Enum 丢掉全部 Provider 细节。

---

## 55. Messages Usage 为什么要相加

Messages 把 Prompt Token 拆成：

```text
uncached input
cache read input
cache creation input
```

canonical `prompt_tokens` 需要表示完整 Prompt Size，因此：

```text
total_prompt_tokens = input + cache_read + cache_creation
```

同时：

- `cached_prompt_tokens` 单独保留 Cache Read。
- `cache_creation_prompt_tokens` 单独保留 Cache Write。
- Total Token = Total Prompt + Output。

---

## 56. Tool Calls 为什么覆盖 Refusal

Messages 最终 Stop Reason 的裁决：

```text
if completed tool calls exist
  -> ToolCalls
else
  -> provider stop reason
```

即使 Provider 同时给出 Refusal，只要完整 ToolUse Block 已产生，Agent Loop 就必须解决真实 Tool Calls，不能把它们丢掉。

---

## 57. `collect_response` 是什么，不是什么

`collect_response` 消费 Layer-2 Event Stream：

- 第一个 Completed -> 返回 Response 与 Metrics。
- 第一个 Failed -> 返回 Error Info。
- 其他事件全部丢弃。
- Stream 无终态结束 -> 合成 Truncation Error。

它适合不需要逐 Token UI 的一次性调用。

它不包含：

- Actor。
- Retry Loop。
- Cancellation Registry。
- Attempt 隔离。

`SamplingClient::conversation_collect` 只是 Backend-aware 的 Layer 1 + Layer 2 Convenience API。

---

## 58. Retry Classifier 为什么是纯函数

`classify_error` 只接收：

- Error。
- 已重试次数。
- Max Retry。
- Rate Limit Threshold。

返回纯数据 `RetryDecision`，不 Sleep、不发 Event、不改 Request。

好处：

- 分类规则可以大量单测。
- I/O 与 Policy 分离。
- 上层能决定怎样展示 Retrying。
- 不会在错误辅助函数中隐藏 Cancellation Point。

---

## 59. Retry Decision 全表

| Decision | 执行动作 |
| --- | --- |
| `Retry` | 增加 Counter，发 Retrying，指数退避 |
| `RetryWithBackoff` | 使用 Retry-After 或计算 Backoff |
| `RetryWithImageStrip` | 删除 Request 中图片并立即重试 |
| `RetryWithClientRebuild` | 退避后以 HTTP/1.1 重建 Client |
| `EmitToSession` | 发最终 Failed，由 Session 负责恢复 |
| `Fatal` | 发最终 Failed，结束 Completion |

分类与执行分处 `retry.rs` 和 `request_task.rs`。

---

## 60. Auth 为什么交给 Session

Auth Error 返回 `EmitToSession`，而不是 Sampler 内盲目刷新。

因为 Session 掌握：

- 当前认证模式。
- Refresh 策略。
- UI 的 Auth Required 映射。
- 凭据失效预算。
- 是否应重建会话。

Sampler 只保留 `SentCredential`：

- `Sent`：确实发了 Credential 并被拒绝。
- `Missing`：请求根本没带 Credential。
- `Unknown`：旧路径或合成错误无法确定。

401 的含义因此可以更精确，不会把“没发 Token”误判为“Token 已失效”。

---

## 61. 403 为什么不是 Auth Refresh 信号

`SamplingError::is_auth_error` 只把 401 当 Credential Rejection。

403 可能表示：

- 已认证但没有权限。
- Content Safety Policy。
- ZDR 限制。
- 操作被策略禁止。

如果 403 触发 Refresh，既无助于恢复，还可能让客户端错误地拆毁 Session 或清理有效认证。

---

## 62. Encrypted Content Error 为什么交给 Session

不同模型家族可能无法解密历史中的 `encrypted_content`。

这不是网络瞬态问题；相同 Request 重试不会成功。

Sampler 选择 `EmitToSession`，让更高层给出“换 Session / 清理不兼容历史”的语义化恢复。

---

## 63. `x-should-retry` 只做否决，不做强制

规则：

```text
x-should-retry: false
  -> veto retry

x-should-retry: true
  -> continue normal local classification
```

为什么不让 `true` 强制重试所有状态？

因为服务端 Hint 不应把本地明确不可重试的 Serialization、Invalid Configuration 等错误变成 Retry Storm。

---

## 64. Context Length Error 为什么不重试

错误文本匹配 Prompt Too Long、Maximum Context Length、`context_length_exceeded` 等模式时，`is_retry_vetoed` 返回 true。

相同或更大的 Payload 重发不会变好。

真正的恢复通常是：

- 上层 Compaction。
- 删除附件。
- 切换更大 Context Model。

Sampler 不拥有这些完整语义，所以直接结束并把 Metadata 交给 Session。

---

## 65. 413 与图片处理错误

两类错误进入 `RetryWithImageStrip`：

- HTTP 413 Payload Too Large。
- 400/500 且消息包含 `Could not process image`。

执行时：

1. `request.strip_images()`。
2. 若删除数量为 0，升级为 Fatal。
3. 若删除成功，增加 Retry Counter 并重试。

要特别注意源码注释与实现的细微差异：注释称图片特殊处理“不计预算”，但当前执行路径确实 `retry_count += 1`。学习和维护时应以实际代码为准。

---

## 66. Connection Reset 为什么也可能先去图

某些 Nginx 在上传过大 Body 时不会返回标准 413，而是直接 Reset Connection 或 Broken Pipe。

`is_likely_body_rejected` 识别：

- Request/Body 写入错误。
- 排除 Timeout。
- 排除 Connect Failure。

若命中，任何 Retry Decision 执行前都会先尝试 Strip Images，避免重复上传同一个大 Body。

---

## 67. 第一次 Generic Retry 为什么切 HTTP/1.1

Generic Retryable Error 的第一次 Retry 返回 `RetryWithClientRebuild`。

执行顺序：

1. Backoff，可被 Cancel。
2. Clone Config。
3. 设置 `force_http1=true`。
4. 尝试构建新 Client。
5. 成功则替换，失败则继续使用旧 Client。

目的在于逃离可能被污染的 HTTP/2 Connection Pool 或协议层故障。

它是降级策略，不保证问题一定来自 HTTP/2。

---

## 68. Generic Backoff

`retry_backoff_with_jitter`：

```text
retry 1: base 2s
retry 2: base 4s
retry 3: base 8s
retry 4: base 16s
retry 5+: base capped at 30s
```

每次在 Base 周围加入约 ±20% Jitter。

Jitter 防止大量客户端在同一时间失败后，又在完全相同的秒数同时冲击服务端。

---

## 69. 429 的独立上限

Rate Limit 的默认阈值是 2，并与通用 Max Retry 取较小值。

若有 `Retry-After`，优先使用服务端指定等待秒数；否则使用通用指数 Backoff。

Rate Limit 等待可能很长，所以它不会默认烧完 15 次通用预算。

阅读源码时要注意 Counter 判定使用 `next_attempt >= effective_cap`；测试固定了真实边界，修改展示文案前应同时核对它。

---

## 70. Idle Timeout 为什么 Fatal

`SamplingError::IdleTimeout` 在当前实现中不可重试。

设计判断是：模型或网络路径已经长时间无进展，自动重放相同请求很可能再次占用数分钟。

这与短暂的连接断开不同；后者可能是 Retryable Transport Error。

---

## 71. Serialization 为什么 Fatal

Serialization 表示服务端数据不符合客户端预期 Schema。

同一个 Provider、同一个响应形态重试往往仍会 Parse Failure。

因此：

- `SamplingError::is_retryable` 返回 false。
- `clone_error` 必须保留 Variant。
- `synthesize_from_info` 也必须恢复 Variant。

这是错误类型不仅用于展示、还直接控制系统行为的典型例子。

---

## 72. Doom Loop 信号来自哪里

Responses SSE Decoder 可以从服务端 Event 中收集 Doom Loop Signals。

`DoomLoopSignalCollector` 与 Raw Stream 一起返回，Layer 2 会：

- 在流中检查是否出现足够可信的 Abort Trigger。
- 在最终 Response 上附带收集到的 Signal。

只有 Responses Backend 走这条路径；Chat Completions 与 Messages 向 `drive_l2` 传 `None`。

---

## 73. Mid-stream Doom Abort

Responses Parser 在处理当前 Event 前检查 Collector：

```text
confident abort trigger exists
  -> Failed(DoomLoopDetected)
  -> drop current attempt stream
```

错误记录当前 `chunk_index`，用于 Telemetry。

在处理 Event 前检查，能避免携带终态 Signal 的 Frame 被先接受为 Completed。

---

## 74. Terminal Doom Check

即使 Mid-stream Abort 没触发，`drive_l2` 在收到 Completed 时还会：

1. 用 Policy 检查 `response.doom_loop_signals`。
2. 若有 Confident Trigger，转换成 `DoomLoopDetected`。
3. 不提交该 Response。

这是 Belt-and-braces：流中早停与终态复检形成两道防线。

---

## 75. Doom Retry 为什么几乎不等待

`doom_loop_backoff` 只返回 0 到 250ms Jitter。

Doom Loop 被视为随机采样陷入坏轨迹；新采样本身就是恢复手段，长时间等待不会提高成功概率，只需要轻微错开并发重采样。

---

## 76. Doom Budget 用完后为什么“解除武装并接受”

当 `doom_retry_count >= doom_max_retries`：

- 下一次 Attempt 的 `doom_check=None`。
- Collector 的 Mid-stream Abort 被 Disarm。
- Attempt 可以完整结束。
- 最终响应即使仍有 Signal，也会记录 Warning 后 Accepted As-is。

否则 Doom Recovery 自己可能成为无限循环。

这是一种有界恢复策略：先尝试改善，预算耗尽后保证系统收敛。

---

## 77. Doom 与普通 Retry 的优先级

在 `drive_l2` 收到 Completed 后：

```text
Doom Check
  > Length Check
  > Empty Check
  > Accept
```

原因是一个有信号的循环响应无论看起来是截断、空还是有文本，都已经被判为 Poisoned Attempt。

Doom Failure 在外层也不会进入普通 `classify_error`，确保不扣 Transport Budget。

---

## 78. Retrying Event 表达什么

`SamplingEvent::Retrying` 包含：

- 当前 Request ID。
- Attempt/Retry Counter。
- Max Retries。
- Typed Error Kind。
- Human-readable Reason。
- 可选 Doom Trigger 与 Abort Chunk。

下游不需要解析错误字符串来判断 Rate Limit、Doom 或 Empty Response。

字符串用于展示，Typed Kind 用于控制与 Telemetry。

---

## 79. Cancellation 的三个检查点

### Loop 顶部

避免已取消请求再开始新 Attempt。

### `drive_l2` 的 biased select

取消优先于下一条 Stream Event，尽快停止正在流式读取的请求。

### Backoff Sleep

`sleep_or_cancel` 让请求在长 Backoff 期间也能立即响应 Cancel。

如果只在 Attempt 之间检查，30 秒 Backoff 会让 Cancel 看起来失效。

---

## 80. Cancellation 的终态映射

共享 Event Channel 收到：

```text
Failed {
  kind: Api,
  message: "request cancelled",
  is_retryable: false
}
```

Completion Oneshot 则收到一个 Rich Error，目前用 `auth_unknown("request cancelled")` 承载。

这不表示取消真的是认证失败；只是当前 Rich Error Enum 没有专门的 Cancelled Variant。

这是值得未来重构的类型表达缺口，但文档必须按当前行为理解。

---

## 81. Event Channel 关闭为什么不终止请求

绝大多数 `event_tx.send(...)` 都忽略错误。

因此如果 UI/Event Consumer 消失：

- Request Task 仍可完成。
- Completion Oneshot 仍可能收到结果。
- Sampler 不会因为展示面关闭而自动取消请求。

真正的请求生命周期由 Cancellation Token、Actor 和 Completion Future 的 RAII Guard 控制。

---

## 82. Completion Sender 为什么放在 `Option`

`send_completion` 执行：

```text
completion_tx.take()
  -> send once
```

这在类型层防止多个终态分支重复发送同一个 Oneshot Sender。

没有 `submit_and_collect` 的请求则从一开始就是 `None`。

---

## 83. Metrics 怎样计算

三个 Parser 都记录：

- Stream Start。
- 每个 Text Content Chunk 的 Timestamp。
- Stream End。

`InferenceLatencyStats::from_timestamps` 计算：

- TTFB：Start 到第一个 Text Chunk。
- TTLB：Start 到 Stream Exhaustion。
- Chunk Count。
- ITL Intervals。
- ITL p50、p99、max、mean。

Reasoning Chunk 当前通常不进入 Content Timestamp，所以这些指标更接近“可见回答文本”的生成延迟。

---

## 84. TTLB 为什么晚于最后一个 Token

TTLB 在整个 Stream 结束时取值，而不是最后一个 Text Delta 时。

因此它包含：

- 尾部 Usage Event。
- Completed Frame。
- Metadata。
- `[DONE]` 或 Stream Close 延迟。

TTFB/ITL 衡量内容生成；TTLB 衡量请求真正释放前的完整尾延迟。

---

## 85. Cost、Usage、Context 不是一个数字

Sampler 最终响应可能同时包含：

```text
Usage
  prompt_tokens
  completion_tokens
  reasoning_tokens
  cache tokens
  total_tokens

Cost
  cost_usd_ticks

Latency
  TTFB / TTLB / ITL
```

它们分别回答：

- 模型处理了多少 Token？
- 当前上下文占了多少？
- 计费是多少？
- 用户等了多久？

不要用 Completion Token 推导 Cost，也不要把 Billing Total 当 Auto Compact 的唯一依据。

---

## 86. 最终成功的精确时序

```text
Raw stream ends
  -> Layer 2 builds ConversationResponse
  -> Layer 2 emits Completed
  -> drive_l2 intercepts
  -> check Doom
  -> check Length
  -> check Empty / Content Filter
  -> AttemptOutcome::Completed
  -> set metrics.attempts
  -> emit one shared Completed
  -> send completion oneshot
  -> task returns RequestId
  -> Actor JoinSet removes Active Request
```

Chat State 只能在共享的最终 Completed 之后看到可提交 Response。

---

## 87. 最终失败的精确时序

```text
Raw/L2/Init failure
  -> AttemptOutcome::Failed or InitFailed
  -> classify_error

retry decision
  -> optional mutation/backoff/client rebuild
  -> Retrying
  -> next attempt

terminal decision
  -> shared Failed
  -> completion Err
  -> task returns RequestId
  -> Actor cleanup
```

失败 Attempt 自己的 Layer-2 Failed 被拦截，不会先作为“整个请求已失败”通知 Session，再继续重试。

---

## 88. 与 Agentic Loop 的边界

Sampler 负责：

- 得到一份可接受的模型 Response。
- 统一流式内容。
- 对网络和采样类问题做有限恢复。

Agentic Loop 负责：

- 把最终 Assistant Response 写入 Chat State。
- 解析 Tool Calls。
- 执行本地工具。
- 写回 Tool Results。
- 决定是否下一轮 Sampling。

因此 Sampler 内的“Retry Loop”与 Agent 的“Tool Loop”是两层完全不同的循环。

---

## 89. 两层循环不要混淆

```text
Agent Tool Loop
  request model
    Sampler Retry Loop
      attempt 1 failed
      attempt 2 succeeded
  execute tool
  append result
  request model again
    Sampler Retry Loop
      attempt 1 succeeded
  finish turn
```

Sampler Retry 不应向 Chat State 追加中间 Attempt。

Agent Tool Loop 的每个成功模型 Response 则必须成为正式 Conversation 的一部分。

---

## 90. 为什么 Tool Delta 不能直接当 Tool Call 执行

单个 `arguments_delta` 不保证是合法 JSON。

例如：

```text
delta 1: {"path":
delta 2: "/tmp/a",
delta 3: "line":3}
```

Streaming Event 供 UI 和 Progressive State 使用；Agent 真正执行 Tool 时应读取最终 canonical Assistant 中已经合并的 Tool Call。

否则可能在参数尚未完成时执行破碎调用。

---

## 91. Backend Tool 与 Local Tool 的所有权差异

| 类型 | 执行者 | 流式事件 | 是否进入本地 Tool Dispatcher |
| --- | --- | --- | --- |
| Function Tool Call | Grok Build Client | `ToolCallDelta` | 是 |
| Hosted Web/X Search | Backend Agentic Sampler | `BackendToolCallStarted/Completed` | 否 |
| Code Interpreter Backend Call | Backend | Backend Tool Events | 否 |

两者都可能进入 canonical Conversation，但 Tool Execution Ownership 不同。

---

## 92. 常见错误理解一：看到 Completed 就一定写历史

错误。

Layer-2 Completed 可能随后被判为：

- Doom Loop。
- Max Tokens Truncation。
- Empty Response。

只有 Request Task 重新发出的 Completed 才是整个请求正式成功。

---

## 93. 常见错误理解二：重试完全不可见

错误。

Retrying Event 明确可见；失败 Attempt 的非终态 Token 也可能已经流出。

真正被隔离的是 canonical 终态提交，不是所有实时展示痕迹。

如果业务要求“用户绝不能看到失败 Attempt 的任何 Token”，必须在更上层 Buffer 整个 Attempt，会牺牲实时性。

---

## 94. 常见错误理解三：三种 Backend 只差 URL

错误。

它们至少在以下方面不同：

- Message 与 Item 结构。
- Reasoning 表达。
- Tool Call 编排。
- Tool Result Role。
- Hosted Tool 能力。
- Cache 控制。
- Usage 字段。
- Stop Reason。
- Streaming Event State Machine。

Sampler 的大部分复杂度正来自语义适配，而不是 URL 拼接。

---

## 95. 常见错误理解四：`is_retryable()` 是全部策略

错误。

最终分类还受：

- Max Retry 是否为 0。
- Auth / Encrypted Content 的 Session Ownership。
- 413 / Image Special Case。
- `x-should-retry:false`。
- Context Length Veto。
- 429 独立 Threshold。
- First Retry HTTP/1.1 Rebuild。
- Doom 独立 Loop。
- `retry_only_before_output`。

`is_retryable()` 只是基础性质，不是完整状态机。

---

## 96. 常见错误理解五：取消就是网络错误

错误。

取消来自本地 Cancellation Token，不表示 Provider 或网络失败。

当前共享 Event 把它粗略映射为 Api Kind，Completion Rich Error 又复用了 Auth Variant；这只是现有类型系统的表达限制。

诊断时应优先看 Message `request cancelled`，不要把 Kind 名称机械当根因。

---

## 97. 推荐源码阅读顺序

第一遍只读控制面：

1. `lib.rs` 的三层说明。
2. `events.rs`。
3. `handle.rs`。
4. `actor/mod.rs`。
5. `actor/request_task.rs`。
6. `retry.rs`。

第二遍读数据面：

1. `conversation.rs` 的 canonical types。
2. 三个 `conversation/*.rs` 转换器。
3. `client.rs` 的三个 `conversation_stream_*`。
4. 三个 `stream/*.rs`。

第三遍再读边界细节：

1. Auth/Header/Endpoint。
2. SSE Decoder。
3. Doom Loop Collector。
4. Metrics。
5. Tests。

---

## 98. 建议动手实验一：画 Attempt Event 表

构造：

```text
attempt 1: text "hel" -> EventStreamError
attempt 2: text "hello" -> Completed
```

记录：

- Shared Channel 收到哪些 Nonterminal Event？
- 收到几个 Retrying？
- 收到几个 Completed？
- Completion Oneshot 返回什么？
- 哪份 Response 能进入 Chat State？

正确答案的关键是：只有一个共享 Completed，但展示层可能看到两个 Attempt 的 Text。

---

## 99. 建议动手实验二：比较同一 Conversation 的三份 JSON

Conversation 至少包含：

- System。
- User Image。
- Reasoning。
- Assistant Text + Tool Call。
- Tool Result Image。
- Backend Web Search Call。

分别转换后检查：

- 哪些字段被折叠？
- 哪些 Item 被展开？
- 哪些能力降级为 Summary？
- Tool Result 的 Role 怎样变化？
- Reasoning 的加密内容是否仍存在？

这个实验比只看类型定义更容易真正理解 Provider Adapter。

---

## 100. 建议动手实验三：验证 Idle 的两种形式

### 完全停流

Raw Stream 不再 Yield，验证外层 `timeout(stream.next())`。

### 只发心跳

持续 Yield Ping/空 Event，但不发内容，验证 `last_content_chunk_at`。

两者都应收敛为单个 `Failed(IdleTimeout)`，但触发路径不同。

---

## 101. 建议动手实验四：图片降级

构造有两张 Inline Image 的 Request，再模拟：

- 413。
- Image Processing 500。
- Broken Pipe Body Upload。
- 普通 Connect Timeout。

观察：

- 哪些会 Strip Images？
- Retry Counter 怎样变化？
- 没有图片可删时怎样结束？
- `max_retries=0` 时是否仍去图？

最后一问特别适合发现“注释意图”和“当前代码顺序”的差异。

---

## 102. 建议动手实验五：Drop Future 取消

启动 `submit_and_collect`，在 Provider 还没完成时 Drop Future。

验证：

1. `CancelOnDrop` 是否发送 Command。
2. Active Count 是否最终回到 0。
3. Backoff 中是否能迅速退出。
4. 是否只发一个最终 Failed。

---

## 103. 关键测试地图

### `retry.rs`

重点测试：

- Env Override 优先级。
- 429 Threshold。
- Retry-After。
- `x-should-retry` Veto。
- Context Length Veto。
- 413/Image Recovery。
- Serialization Clone 仍不可重试。
- Exponential Backoff 范围。

### `stream/chat_completions.rs`

重点测试：

- Empty Stream。
- Text + First Token。
- Reasoning Sibling。
- Tool Delta Assembly。
- Idle Timeout。
- Usage/Cost/Fingerprint。

### `stream/responses.rs`

重点测试：

- Completed/Failed/Incomplete。
- Missing Terminal Event。
- Tool Index Mapping。
- Backend Tool Progress。
- Reasoning Fallback。
- Doom Abort 与 Disarm。

### `stream/messages_tests.rs`

重点测试：

- Message Start Metadata。
- Text/Thinking/Signature。
- ToolUse JSON Delta。
- Cache Usage。
- Stop Reason 与 Stop Sequence。
- Refusal、Length 与 Unknown Stop。

### `actor/request_task.rs`

重点测试：

- Terminal Event Suppression。
- Error Reconstruction。
- Cancellation。
- Empty Response Context。
- `retry_only_before_output`。
- Doom 独立预算。

---

## 104. 可执行验证命令

```sh
cargo test -p xai-grok-sampler --lib
cargo test -p xai-grok-sampling-types --lib
```

只定位主线符号：

```sh
rg "run_request_task|run_one_attempt|drive_l2|classify_error" \
  crates/codegen/xai-grok-sampler/src

rg "stream_chat_completions|stream_responses|stream_messages" \
  crates/codegen/xai-grok-sampler/src/stream

rg "conversation_to_chat_messages|build_responses_input|build_messages_request" \
  crates/codegen/xai-grok-sampling-types/src/conversation
```

---

## 105. 修改 Sampler 时的审查清单

### 新增 Sampling Event

- 三个 Parser 是否都需要发？
- `drive_l2` 是否应标记 `output_observed`？
- Session Event Drainer 是否处理？
- Headless/Partial/TUI Consumer 是否都处理？
- Retag、Serialize 或测试是否更新？

### 新增 Error Variant

- `SamplingErrorInfo::from` 是否映射？
- `synthesize_from_info` 是否恢复？
- `clone_error` 是否保持性质？
- `is_retryable` 与 `classify_error` 是否一致？
- Completion 与共享 Event 是否都能表达？

### 新增 Backend

- Canonical -> Wire Request。
- Raw Stream Type。
- Layer-2 Parser。
- Usage、Cost、Stop Reason。
- Tool Call 与 Reasoning。
- Image 与 Hosted Tool 能力。
- Error/Header/Metadata。
- `run_one_attempt` 分派。
- `conversation_collect` 分派。

### 修改 Retry

- Counter 边界是否 Off-by-one？
- 429 是否仍有独立上限？
- Cancel 能否打断 Sleep？
- 失败 Attempt Terminal 是否仍被拦截？
- `retry_only_before_output` 是否覆盖新 Event？
- Doom 是否仍不扣 Transport Budget？

---

## 106. 可以继续追问的设计问题

1. Cancellation 是否应增加专门的 `SamplingError::Cancelled`？
2. 图片 Recovery 是否真的应该不计预算，还是应修正文档注释？
3. 普通重试允许失败 Attempt Token 流出时，UI 应怎样标记 Attempt Boundary？
4. `retry_only_before_output` 是否应成为特定 Surface 的默认策略？
5. Messages 的 Redacted Thinking 是否需要端到端 canonical 表达？
6. Chat/Messages 的 Backend Tool Summary 降级是否足够支持跨 Backend Resume？
7. Responses 的 Server-side Failure 是否应保留更精确的原始 Error Code，而非统一 500？
8. Idle Timeout 是否应该根据“完全停流”和“仅心跳”区分错误类型？

这些不是本文声称存在的 Bug，而是读懂当前边界后自然出现的演进问题。

---

## 107. 本篇核心不变量

最后把整篇压缩成十二条：

1. Chat State 保存 canonical Conversation，不保存某个 Provider 的 Wire Message。
2. `SamplingClient` 负责传输，Layer 2 负责归一化，Actor 负责并发与恢复。
3. 每个 Layer-2 Stream 恰好产生一个 Terminal Event。
4. 每个 Attempt 的 Terminal Event 都被 `drive_l2` 拦截。
5. 整个 Request 只向 Session 发一个被接受的 Completed，或一个最终 Failed。
6. 失败 Attempt 的实时 Chunk 可能可见，但 canonical Response 不会提交。
7. Tool Argument Delta 只用于流式更新，执行应等待最终合并后的 Tool Call。
8. Error Variant 会控制 Retry；Clone 和序列化往返不能改变错误性质。
9. Doom Recovery 与 Transport Retry 使用独立预算。
10. Cancellation 必须能打断 Attempt 和 Backoff。
11. 三种 Backend 并非无损等价，Adapter 必须明确降级行为。
12. Sampler Retry Loop 与 Agent Tool Loop 是两层不同状态机。

---

## 108. 本篇术语表

| 名词 | 白话解释 | 本篇中的具体含义 |
| --- | --- | --- |
| Sampler | 向模型采样回答的组件 | 包含 Client、Stream Parser、Actor、Retry 与统一 Event，而不只是 HTTP 调用 |
| Sampling | 从模型概率分布生成一次输出 | 同一 Prompt 重试或 Doom Recovery 可能得到不同回答 |
| canonical | 内部统一、作为事实来源的格式 | `ConversationItem`、`ConversationRequest`、`ConversationResponse` |
| wire format | 真正发到网络上的 Provider 协议格式 | Chat Completion Message、Responses Item、Messages Content Block |
| backend | 一套具体模型 API 协议 | `ChatCompletions`、`Responses` 或 `Messages` |
| adapter | 在两套表示之间转换的代码 | canonical Conversation 与三种 Wire Schema 之间的转换器 |
| Layer 1 / L1 | 最靠近 HTTP 的原始客户端层 | 返回 Backend-specific Raw Stream |
| Layer 2 / L2 | 把 Raw Stream 归一化的一层 | 产生 `SamplingEvent` 并累加最终 Response |
| Layer 3 / L3 | Actor 与请求生命周期层 | 管并发、重试、取消和 Completion |
| SSE | Server-Sent Events | 服务端在一个 HTTP 响应中持续发送流式 Frame 的协议形式 |
| Chunk | 一小段流式数据 | 可能是文本、Reasoning、Tool Argument 或 Metadata |
| Delta | 相对于已收到内容的增量 | 例如 Tool Arguments 的一段字符串，不保证自身是完整 JSON |
| accumulator | 把多个 Delta 累加成完整值的状态 | `content_acc`、`reasoning_acc`、`BlockState` 等 |
| terminal event | 宣告流已经成功或失败结束的事件 | `SamplingEvent::Completed` 或 `Failed` |
| nonterminal event | 流尚未结束时的进度事件 | Token、Tool Delta、Metadata、Backend Tool Progress |
| Attempt | 整个逻辑请求中的一次网络采样尝试 | 失败后可由 Retry Loop 创建下一次 Attempt |
| Retry Loop | 对一次逻辑请求做多次 Attempt 的循环 | 位于 `run_request_task` |
| Agentic Loop | 模型调用、工具执行、结果回填、再采样的循环 | 位于更高层 Agent Turn，不等同于 Retry Loop |
| failure isolation | 防止失败尝试污染正式状态 | 拦截 Attempt Terminal，只提交被接受的 Response |
| Event Channel | 把流式事件发送给 Session Consumer 的 Channel | Actor Spawn 时传入的 Unbounded MPSC Sender；它不是多订阅者 Broadcast Channel |
| Completion Oneshot | 只返回一次最终结果的 Channel | `submit_and_collect` 等待的 per-request Result |
| actor | 独占状态并通过 Command 收消息的异步任务 | `SamplerActor` 管配置和 Active Request Registry |
| handle | 调用 Actor 的轻量引用 | `SamplerHandle` 内部只是 Command Sender |
| JoinSet | Tokio 管理一组异步任务的容器 | Actor 用它 Spawn、Join 和 Shutdown Request Tasks |
| CancellationToken | 可 Clone 的协作式取消信号 | Actor、Request Task、Backoff Sleep 共享取消状态 |
| RAII | 用对象生命周期自动管理资源 | `CancelOnDrop` 在 Future 被 Drop 时发 Cancel |
| backoff | 重试前等待一段时间 | 通用错误使用指数 Backoff，Doom 使用近即时 Jitter |
| exponential backoff | 等待时间逐次翻倍的退避 | 2、4、8、16 秒，随后封顶约 30 秒 |
| jitter | 在等待时间上加入随机扰动 | 避免大量客户端同步重试形成惊群 |
| retry budget | 一次请求最多允许多少次重试 | Transport 与 Doom 各有自己的 Counter |
| rate limit | 服务端限制请求频率 | 通常是 HTTP 429，并有更低 Retry Cap |
| `Retry-After` | 服务端建议等待时长的 Header | 429 时优先于本地 Backoff |
| `x-should-retry` | 服务端对是否值得重试的 Hint | `false` 可以否决，本地不会让 `true` 强制覆盖 Fatal 类型 |
| HTTP/1.1 fallback | 重建 Client 并禁用 HTTP/2 | 第一次通用 Retry 尝试逃离坏连接池 |
| payload | HTTP Request Body | Conversation、Tool Schema 和 Inline Images 序列化后的内容 |
| image strip | 从请求副本移除图片 | 413、图片处理失败或疑似 Body Rejection 时的降级 |
| idle timeout | 长时间没有真实进展时失败 | 同时覆盖完全不来 Event 和只来心跳两种情况 |
| keepalive / heartbeat | 只证明连接活着的空事件 | 不一定重置 Content-aware Idle Timer |
| reasoning | 模型的思考摘要或加密思考载体 | canonical Sibling，在不同 Backend 中表示不同 |
| thinking block | Messages API 的 Reasoning 表达 | 带可读 Thinking 与可选 Signature |
| signature | Provider 为 Thinking 提供的加密/验证信息 | Messages Parser 在 Block Stop 时通过 `ReasoningCompleted` 暴露 |
| redacted thinking | Provider 不公开内容的加密思考块 | 当前只保证能解析，不进入统一事件和最终 Response |
| tool call | 模型请求客户端执行某函数 | 最终存于 Assistant 的 `tool_calls` |
| hosted/backend tool | 由模型服务端直接执行的工具 | Web Search、X Search、Code Interpreter 等 |
| tool index | 统一事件中连续的工具序号 | 与 Responses Output Index 或 Messages Block Index 不一定相同 |
| stop reason | 模型为何停止输出 | Stop、Length、ToolCalls、ContentFilter 等统一枚举 |
| content filter | Provider 因策略拒绝生成 | 即使空内容也被视为确定性成功终态，不做 Empty Retry |
| empty response | 流正常结束但没有可用回答 | Reasoning-only 或 No-visible-content，可进入普通 Retry |
| truncation | 输出被上限截断 | `Length` 被转成不可自动重试的 MaxTokens Error |
| doom loop | 模型陷入高置信度重复推理轨迹 | Responses 信号可触发独立、有限的快速重采样 |
| disarm | 关闭 Doom 的中途 Abort | Budget 用尽后让最后一次 Attempt 完成并被接受 |
| rich error | 带状态、Header、Metadata 等完整信息的错误 | `SamplingError` |
| error info | 可 Clone、可序列化的错误镜像 | `SamplingErrorInfo`，用于 Event Channel |
| credential provenance | 请求究竟有没有带凭据 | `SentCredential::Sent/Missing/Unknown` |
| context length | 当前请求占用的模型上下文规模 | 超限时相同 Payload 重试无意义，交给上层压缩 |
| prefix cache | Provider 对相同输入前缀的缓存 | Responses 按原顺序重放、Messages 标断点以提高命中 |
| prompt cache breakpoint | 告诉 Messages API 在哪里建立缓存边界 | 通过 `cache_control: ephemeral` 标在可承载 Block 上 |
| TTFB | Time To First Token | Stream Start 到第一个可见 Text Chunk 的时间 |
| TTLB | Time To Last Byte | Stream Start 到整个 Stream 完全结束的时间 |
| ITL | Inter-token Latency | 相邻 Text Chunk 的时间间隔 |
| output observed | 是否已经观察到模型产生输出 | 决定 `retry_only_before_output` 是否关闭后续 Retry |
| state machine | 状态与事件共同决定下一步的程序结构 | 三个 Parser、Retry Loop 和 Agent Tool Loop 都是不同状态机 |

---

## 109. 一句话复盘

Sampler 的核心不是“把 Prompt 发给模型”，而是把一个 canonical 请求安全投影到三种不同协议，把三套不兼容的流式状态机归一化，再通过 Attempt 终态拦截、Typed Error 分类、有界恢复和协作式取消，保证实时输出可以快，而正式 Conversation 只接收唯一、完整、被接受的结果。
