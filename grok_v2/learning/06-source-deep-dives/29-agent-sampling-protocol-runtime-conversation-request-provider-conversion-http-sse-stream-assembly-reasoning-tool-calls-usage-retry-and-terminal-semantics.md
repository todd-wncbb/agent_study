# 源码精读 29：Agent Sampling Protocol Runtime——Provider 转换、HTTP/SSE、流式组装、Reasoning、Tool Call、Usage、Retry 与终态语义

> 源码基线：`ed6d543`
>
> 上一篇停在 `ConversationRequest`。本篇继续向下：同一份内部请求怎样变成 Chat Completions、OpenAI Responses 或 Anthropic Messages 三种 wire format；三套完全不同的 SSE 事件又怎样收敛成统一的 `ConversationResponse`，最终安全回填到 Agent Loop。

---

## 1. 本篇解决什么问题

读完后应能回答：

- `xai-grok-sampler` 为什么拆成三层？
- `ConversationRequest` 如何选择 Chat Completions、Responses 或 Messages？
- 三种协议怎样表示 System、图片、Tool Call、Tool Result 与 Reasoning？
- 为什么 Reasoning 在内部是 Assistant 的 sibling，而不是永远塞在 Assistant 字段里？
- HTTP Client 负责什么，Layer-2 stream transform 又负责什么？
- SSE delta 为什么不能直接写进 `chat_history.jsonl`？
- 三种协议分别怎样重建完整 Tool Call arguments？
- Text、Reasoning 与 Tool Call 的流式通知怎样保持相对顺序？
- `Completed` 为什么不一定代表本次 Attempt 可以接受？
- Empty、Length、ContentFilter 和 ToolCalls 的终态有何不同？
- Usage、cached tokens、reasoning tokens 与 live context total 怎样归一化？
- 413、429、5xx、401、serialization、idle timeout 分别在哪一层恢复？
- 为什么重试后只能提交最后一次成功 Attempt？
- Session 怎样等待 stream drainer，再开始执行 Tool Call？

---

## 2. 先给出分层总图

```text
SessionActor
    │ ConversationRequest
    ▼
SamplerHandle ──► SamplerActor ──► per-request task
                                      │
                           choose ApiBackend
                  ┌───────────────────┼───────────────────┐
                  ▼                   ▼                   ▼
          Chat Completions         Responses           Messages
          /chat/completions        /responses          /messages
                  │                   │                   │
          raw chunk stream      raw event stream    raw block stream
                  │                   │                   │
                  └──────────────┬────┴──────────────┬────┘
                                 ▼
                      Layer-2 stream transform
                                 │
       StreamStarted / ChannelToken / ToolCallDelta / Completed / Failed
                                 │
                                 ▼
                     retry + cancel + terminal gate
                                 │
                                 ▼
                      ConversationResponse
                                 │
                                 ▼
                  Session notification + ChatState
```

---

## 3. 最重要的心智模型

Sampling Runtime 不只是“发一个 HTTP 请求”。它包含四次语义转换：

```text
内部对话模型
  → Provider 请求模型
  → Provider 流式事件模型
  → 统一 SamplingEvent
  → 内部 ConversationResponse
```

任一转换发生信息丢失，错误往往会在下一轮请求才暴露，例如：

- Tool Call arguments 拼接错误；
- Reasoning signature 丢失；
- Tool Result role 转换错误；
- stop reason 被误判；
- Usage total 含义混淆。

---

## 4. 核心源码地图

| 领域 | 主要源码 |
| --- | --- |
| Sampler 公共分层 | `xai-grok-sampler/src/lib.rs` |
| HTTP Client | `xai-grok-sampler/src/client.rs` |
| Sampler Actor | `xai-grok-sampler/src/actor/mod.rs`、`actor/state.rs` |
| 每请求重试任务 | `xai-grok-sampler/src/actor/request_task.rs` |
| 统一事件 | `xai-grok-sampler/src/events.rs` |
| Retry 分类 | `xai-grok-sampler/src/retry.rs` |
| Chat Completions 流 | `xai-grok-sampler/src/stream/chat_completions.rs` |
| Responses 流 | `xai-grok-sampler/src/stream/responses.rs` |
| Messages 流 | `xai-grok-sampler/src/stream/messages.rs` |
| Buffered collector | `xai-grok-sampler/src/stream/collect.rs` |
| 内部请求/响应类型 | `xai-grok-sampling-types/src/` |
| Chat Completions 转换 | `xai-grok-sampling-types/src/conversation/chat_completions.rs` |
| Responses 转换 | `xai-grok-sampling-types/src/conversation/responses.rs` |
| Messages 转换 | `xai-grok-sampling-types/src/conversation/messages.rs` |
| Session 事件消费 | `xai-grok-shell/src/session/acp_session_impl/tool_calls.rs` |
| Session 提交与恢复 | `xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs` |
| Agentic Loop 接回点 | `xai-grok-shell/src/session/acp_session_impl/turn.rs` |

---

# 第一部分：Sampler 为什么分三层

## 5. Layer 1：SamplingClient

`SamplingClient` 负责 Provider 边界：

- 构造 URL；
- 设置认证和 headers；
- 应用 model、temperature、top_p、max tokens 默认值；
- 把内部请求转换成具体 wire request；
- 发起 HTTP 请求；
- 解析 HTTP 错误和响应 headers；
- 把 SSE data 解码成 Provider 原始 typed event stream。

它不负责把 token 发给 UI，也不决定一次失败是否应重试十五次。

---

## 6. Layer 2：stream transform

每种 backend 有独立 transform：

- `stream_chat_completions`；
- `stream_responses`；
- `stream_messages`。

它们把不同原始事件统一成 `SamplingEvent`，并构造最终 `ConversationResponse`。

这层是纯流转换：不依赖 Shell，不执行 Tool，不处理用户审批。

---

## 7. Layer 3：SamplerActor 与 request task

SamplerActor 管理并发 request 生命周期；每个 request task 独占：

- retry loop；
- `CancellationToken`；
- request 的可变恢复副本；
- HTTP client fallback；
- transport retry budget；
- doom-loop resample budget；
- completion oneshot。

它只在某次 Attempt 真正可接受时向外发最终 `Completed`。

---

## 8. 为什么 Actor 不直接解析 SSE

若 Actor 同时承担：

- request registry；
- HTTP I/O；
- 三协议解析；
- retry sleep；
- token 归并；

一个慢流或 backoff 就会阻塞其他请求控制命令。

Actor 把每个请求 spawn 成独立 task，自己保留生命周期所有权和取消入口。

---

## 9. 两种调用风格

Streaming Session 使用：

```text
SamplerHandle::submit_and_collect
+ shared SamplingEvent receiver
```

辅助模型调用可使用：

```text
conversation_collect
    → Layer-2 stream
    → collect_response
```

后者吞掉中间 delta，只返回第一个 `Completed` 或 `Failed`。

---

# 第二部分：统一内部类型

## 10. ConversationRequest 是 Provider-neutral IR

它包含：

- `items: Vec<ConversationItem>`；
- client-executed `tools`；
- server-executed `hosted_tools`；
- model 与 sampling 参数；
- reasoning effort；
- JSON schema；
- tracing 与 x-grok IDs；
- prompt cache key 等扩展。

把它理解成编译器的中间表示比“某个 API 请求 DTO”更准确。

---

## 11. ConversationResponse 也是统一 IR

核心字段包括：

- `items`：Reasoning、Assistant、BackendToolCall 等；
- `stop_reason`；
- `usage`；
- `cost_usd_ticks`；
- `message_chunks_emitted`；
- doom-loop signals；
- provider message id；
- raw stop reason、stop sequence、refusal message。

Session 的 Agent Loop 只处理这份统一结果，不再直接 switch 三种 Provider response。

---

## 12. ApiBackend 是协议选择，不等于厂商名

当前枚举：

- `ChatCompletions`；
- `Responses`；
- `Messages`。

选择的是 endpoint/wire semantics。同一个模型服务可以兼容其中一种或多种协议，不能简单把 backend 枚举当作模型品牌。

---

## 13. Default 何时应用

`SamplingClient::apply_conversation_defaults` 在发请求前补齐：

- model；
- temperature；
- top_p；
- max output tokens。

Builder 可以逐请求覆盖；为空时使用 `SamplerConfig` 默认值。配置验证也在 Provider I/O 之前完成，确定性错误不进入 retry loop。

---

# 第三部分：三种请求转换

## 14. 转换对照表

| 内部项 | Chat Completions | Responses | Messages |
| --- | --- | --- | --- |
| System | system message | easy input message | top-level system blocks |
| User text/image | user message | easy input content | user content blocks |
| Assistant text | assistant message | assistant easy message | assistant text block |
| Tool Call | assistant.tool_calls | function_call item | tool_use block |
| Tool Result | tool role message | function_call_output | user tool_result block |
| Reasoning | 下一个 Assistant 的 `reasoning_content` | top-level reasoning item | assistant thinking block |
| Hosted Tool | 无统一表达 | native/raw tool entry | 当前请求路径不承担同等能力 |

---

## 15. Chat Completions 是 role-message 模型

`conversation_to_chat_messages` 把 items 展开为 `ChatRequestMessage`。

主要映射：

- System → `role=system`；
- User → `role=user`；
- Assistant → `role=assistant` + `tool_calls`；
- ToolResult → `role=tool` + `tool_call_id`。

图片转换为该协议支持的 content parts。

---

## 16. Chat Completions 的 Reasoning folding

内部 Reasoning 是独立 sibling；Chat Completions 却要求它位于后续 Assistant 的 `reasoning_content`。

转换器维护 `pending_reasoning`：

1. 遇到 Reasoning，暂存文本；
2. 遇到紧随其后的 Assistant，join 后写入 reasoning_content；
3. `BackendToolCall` 不打断 folding；
4. 其他 item 会清空 pending，避免把陈旧推理贴到错误回答；
5. 尾部孤立 Reasoning 被丢弃。

---

## 17. 为什么内部不直接采用 reasoning_content

Responses 把 Reasoning 作为顶层 output item，Messages 把它作为 content block。

若内部类型绑定 Chat Completions 字段，另外两种协议会反复做有损拆装。Sibling 形式更接近跨协议最小公分母。

---

## 18. Malformed Tool arguments 在出站边界降级为 `{}`

内部历史仍可保存原始 Tool Call arguments，但 Provider 会校验历史中的 `function.arguments`；一条旧的非法 JSON 足以让此后每轮请求都返回 400。

因此三个 wire converter 都做防御性处理：

- Chat Completions 和 Responses 调用 `sanitize_tool_arguments`，先用 `IgnoredAny` 无 DOM 校验；
- Messages 在构造 `ToolUse.input` 时解析 JSON，失败则回退空 object；
- 合法 JSON 保持字节内容不变；
- 非法 JSON 出站时替换为 `{}`，并记录 call ID、tool name 和截断后的 preview 告警；
- 匹配的 ToolResult 仍保留原始文本证据，让模型知道那次调用实际发生过什么并继续恢复。

这里的边界很精确：不是悄悄改写 ChatState 中的历史事实，而是避免一条已损坏的旧 Tool Call 永久毒化所有后续 Provider 请求。

---

## 19. Responses 是 typed item 序列

`conversation_item_to_input_items` 可能让一个内部 item 展开成多个 input items。

例如 Assistant 同时有正文和多个 Tool Call：

```text
Assistant text
FunctionCall 1
FunctionCall 2
```

都成为 Responses input 序列里的独立 item。

---

## 20. Responses Tool Result 是 FunctionCallOutput

内部 `ToolResult.tool_call_id` 变为 Responses 的 `call_id`，正文/图片变为 function call output。

这体现了 Responses 的关联模型：输出不是普通 User message，而是显式引用先前 FunctionCall。

---

## 21. Responses Reasoning 保持 sibling

`ConversationItem::Reasoning` 直接成为 `rs::Item::Reasoning`。

因此顺序非常重要：

```text
Reasoning item
Assistant message / FunctionCall items
```

任何排序变化都可能破坏 Provider prompt cache 前缀稳定性。

---

## 22. reasoning_text 需要 JSON patch

依赖库的 `ReasoningTextContent` 类型没有 wire 所需的 `type: "reasoning_text"` 字段。

实现序列化后用 `patch_reasoning_text_types` 补 discriminator。这是 typed SDK 与真实 wire schema 不完全一致时的兼容层。

---

## 23. Hosted Tool 有 native 与 raw 两条路

Responses SDK 能表示的 hosted tool 进入 `rs::Tool`。

xAI 特有的 `x_search` 等无法由 SDK enum 表示，先收集为 raw JSON `extra_tool_entries`，在 wrapper 序列化阶段注入。

接收端若回显这些工具，decoder 也会剥掉 SDK 不认识的 tool entry 后重试 typed deserialize。

---

## 24. Messages 把 System 独立出去

Anthropic Messages request 的 system 不属于普通 role message 序列。

`build_messages_request` 收集 System blocks，其他 items 再组织成 user/assistant messages。

若只有一个无 cache-control 的 system block，可使用紧凑 text form；否则使用 blocks form。

---

## 25. Messages 必须合并相邻 Assistant blocks

内部序列可能是：

```text
Reasoning
Assistant text + ToolCall
BackendToolCall summary
```

转换器用 `pending_assistant` 把 thinking、text、tool_use 等组织成合规的一个 assistant message，而不是产生连续相同 role 的碎片。

---

## 26. Messages Tool Result 属于 User role

Messages API 把 `tool_result` block 放在 User message 中。

因此转换器维护 `pending_tool_results`，把连续结果合并为一个 user blocks message；不能照搬 Chat Completions 的 `role=tool`。

---

## 27. Tool Result 中的图片

若 ToolResult 带结构化 image，Messages 转换会把它组织为 ToolResult 内嵌 content blocks，而不是丢成普通文本 URL。

不同 Provider 对 Tool output multimodality 的容器不同，这正是内部统一类型存在的意义。

---

## 28. Messages Thinking 与 signature

内部 Reasoning 的：

- 可见/摘要文本；
- `encrypted_content`；

分别映射为 Messages thinking block 的 `thinking` 与 `signature`。

signature 是 Provider 用来延续推理状态的 opaque data，不能把它当日志文本处理。

---

## 29. Messages Prompt Cache breakpoint

转换器会在合适的 system/message block 上标记 ephemeral cache control。

选择点大体围绕最近的 assistant/user 边界，目标是在多轮请求中复用稳定前缀，同时让变化尾部不污染缓存边界。

Reasoning block 本身不承载同样的 cache-control 字段。

---

## 30. reasoning_effort 与 structured output 正交

Messages request 中：

- thinking 配置由 `reasoning_effort` 驱动；
- output config/schema 由 JSON schema 驱动。

不能因为启用了 structured output 就意外打开 thinking，也不能因 reasoning effort 覆盖 JSON schema。

---

# 第四部分：HTTP 与 SSE 边界

## 31. EndpointTemplate 预计算 URL

Client 构造时把 `base_url` 与 query params 合成为：

- `Plain(base)`；或
- `WithQuery { prefix, suffix }`。

每次请求只追加 endpoint path。配置 query key 覆盖 base URL 中同名 key，避免重复参数。

---

## 32. x-grok headers 提供跨层关联

请求可带：

- conversation ID；
- request ID；
- model override；
- session ID；
- turn index；
- agent ID；
- deployment ID；
- user ID。

这些不是模型正文，而是路由、观察和后端关联元数据。

---

## 33. Credential 是否真的发出必须记录

401 恢复需要区分：

- 请求带了凭据但被拒；
- 请求根本没带凭据；
- 旧 peer 无法判断。

`SentCredential` 进入 `SamplingErrorInfo`，让 Session 的 auth retry schedule 正确计费，避免无凭据重提耗尽已认证重试预算。

---

## 34. SSE decoder 的职责

Decoder 把每个 `data:` frame 解析为 typed Provider event，并识别：

- 正常数据；
- `[DONE]` 或流结束；
- error payload；
- check/doom-loop side-channel；
- headers 中 model metadata；
- Retry-After 与 `x-should-retry`。

它还必须容忍 Provider 方言与 SDK schema 的差异。

---

## 35. Responses terminal usage 有特殊修正

xAI Responses terminal event 可能额外提供 `context_details`。

Decoder 用：

```text
live total_tokens = context_details.input_tokens
                  + context_details.output_tokens
```

覆盖 typed SDK 的 total，以服务 `/context` 和 auto-compaction。

但 billing fields 仍保留 wire cumulative input/output/cached token 值。

---

## 36. Raw error 为什么要 tee

Layer-2 `Failed` 携带可序列化的 `SamplingErrorInfo`，但 rich `SamplingError` 可能含 reqwest/serde error 和精确分类信息。

`tee_errors`：

1. 原样把 raw stream 交给 Layer 2；
2. 同时把第一个 rich error clone 到共享 cell；
3. request task 收到 `Failed` 时优先取 rich error；
4. 只有合成失败才从 Info 重建。

---

# 第五部分：统一 SamplingEvent

## 37. 事件不是只有 Token

`SamplingEvent` 包括：

- `StreamStarted`；
- `FirstToken`；
- `ChannelToken(Text/Reasoning)`；
- `ToolCallDelta`；
- `ResponseStarted`；
- `ReasoningCompleted`；
- `ModelMetadata`；
- backend tool started/completed；
- `Retrying`；
- `Completed`；
- `Failed`。

这是一套跨 Provider 的流式控制协议。

---

## 38. request_id 隔离并发流

Sampler event channel 是共享的，所有事件带 `RequestId`。

Session 通常一个主请求在飞，但 sampler 本身支持并发请求；request ID 让日志、取消、completion 与流事件保持关联。

---

## 39. ChannelToken 分离 Text 与 Reasoning

统一事件不为每种内容建立独立 variant，而用 `SamplingChannel`：

- Text → ACP `AgentMessageChunk`；
- Reasoning → ACP `AgentThoughtChunk`。

未来增加 Planning 等 channel 时，不必复制整套事件字段。

---

## 40. chunk_index 是展示顺序，不是 Provider block index

三协议自己的 index 语义不同：choice index、output index、content block index。

Layer 2 维护统一递增 `chunk_index`，跨 Text 与 Reasoning 为通知提供相关顺序。Tool block 映射则另有专门表。

---

## 41. ToolCallDelta 的 arguments 不是完整 JSON

事件明确允许：

```text
{"pa
th":"
foo"}
```

这类碎片逐段到达。UI 可以增量显示，真正 Tool 执行必须等待 terminal response 中的完整 ToolCall。

---

## 42. Exactly-one terminal contract

每个 Layer-2 transform 承诺只产生一个终态：

- `Completed`；或
- `Failed`。

若 stream 静默结束而没有任何终态，collector/request task 将它升级为截断/transport failure。

---

# 第六部分：Chat Completions 流式归并

## 43. 核心 accumulator

`stream_chat_completions` 维护：

- `content_acc`；
- `reasoning_acc`；
- `BTreeMap<tool_index, (id, name, arguments)>`；
- model/fingerprint；
- usage/cost；
- finish reason；
- chunk timestamps 与 message chunk count。

---

## 44. Text delta

非空 `delta.content`：

1. 首次时发 `FirstToken`；
2. 更新 content-aware idle timer；
3. 追加到 `content_acc`；
4. 增加 chunk index 和 message chunk count；
5. 发 `ChannelToken::Text`。

---

## 45. Reasoning delta

非空 `delta.reasoning_content`：

- 同样可以触发 `FirstToken`；
- 追加到 `reasoning_acc`；
- 发 Reasoning channel；
- 不增加 text-only 的 `message_chunks_emitted`。

最终 reasoning 会成为 Assistant 前的独立 sibling。

---

## 46. Tool Call delta 如何合并

以 Provider `tc_delta.index` 为 key：

- 首片通常带 id 和 function name；
- 每片可能带 arguments fragment；
- fragment 按到达顺序 append；
- 每片同时发 `ToolCallDelta` 给 UI。

使用 `BTreeMap` 使最终 calls 按 index 稳定排序。

---

## 47. Tool Call 优先决定 stop reason

即使 Provider 漏发或错误发了 finish reason，只要最终存在 Tool Call，统一响应的 stop reason 会强制为 `ToolCalls`。

Agent Loop 必须执行真实 calls，不能因为 wire metadata 不完整而误当最终回答。

---

## 48. Usage 与 cost 是 last-write-wins

流中 usage 通常累计出现，最后一个有效值最完整。

cost 只在新 chunk 确实报告时覆盖；缺失值不会抹掉之前已知 cost。

---

# 第七部分：Responses 流式归并

## 49. Responses 的增量与终态来源不同

Text/Reasoning delta 用于实时展示；最终权威 items 从：

- `ResponseCompleted.response`；或
- `ResponseIncomplete.response`

转换得到。

因此 transform 不需要仅靠 delta 重建所有 output item。

---

## 50. 必须收到 terminal response object

流结束后若没有 `ResponseCompleted` 或 `ResponseIncomplete`，即使之前收到文本 delta，也会失败。

否则无法可靠获得：

- 完整 output items；
- usage；
- status；
- Tool Call 完整 arguments；
- reasoning encrypted content。

---

## 51. Responses Tool index 需要二次映射

Responses 的 `output_index` 包含文本、Reasoning、Tool 等所有 output items，不等于“第几个 Tool”。

transform 在 `ResponseOutputItemAdded(FunctionCall)` 时：

1. 分配连续 tool-only index；
2. 记录 `output_index → tool_index`；
3. 发 id/name；
4. 后续 arguments delta 查表再发。

---

## 52. 无前置 Added 的 arguments delta 会丢弃

若 delta 找不到 output-to-tool mapping，transform 不猜测它属于哪个 call。

猜错比少一次 UI 增量更危险；terminal response 仍是完整 Tool Call 的权威来源。

---

## 53. Streaming reasoning fallback

某些部署会流出 ReasoningTextDelta，但 terminal response 的 reasoning content/summary 为空。

transform 累积 `reasoning_acc`，在最终 typed conversion 后调用 `inject_streaming_reasoning_fallback`，仅在缺失时补入，不覆盖 terminal 已有权威内容。

---

## 54. Hosted Tool 事件独立于 client Tool Call

Responses 可流出：

- web search；
- x search/custom tool；
- code interpreter；
- MCP 等 server-side 生命周期事件。

它们变成 `BackendToolCallStarted/Completed`，供 UI 展示；客户端不再次执行这些 Tool。

---

## 55. OutputItemDone 携带结果

许多 `...Completed` 状态事件只表示阶段完成，没有结果 payload。

真正可展示的 query、sources、code、outputs 通常在 `ResponseOutputItemDone` 的完整 item 中，因此 completion 通知从这里提取结构化 result。

---

## 56. ResponseFailed/ResponseError 怎样处理

两者转成 `SamplingError::Api`，默认使用 500 语义交给 retry classifier。

这是 server 在 SSE 内报告失败，不是 HTTP handshake 失败；但对 retry loop 而言仍属于上游 API failure。

---

## 57. Responses status 转 StopReason

- 有 client Tool Call → `ToolCalls`；
- `Completed` → `Stop`；
- `Incomplete` → `Length`；
- 其他状态 → None。

随后 request task 会把 `Length` 升级为 `MaxTokensTruncation`，不会当普通成功提交。

---

## 58. Doom-loop signal 的两道检查

Responses decoder 可收集后端 doom-loop labels。

- 中途 confidence 已满足：立即产生 `DoomLoopDetected`，丢弃当前 SSE tail；
- terminal response 到达：request task 再检查一次，作为 belt-and-braces。

重采样预算耗尽后会 disarm abort，允许下一次完整结束并按原样接受。

---

# 第八部分：Messages 流式归并

## 59. Messages 是 block state machine

transform 以 `content_block.index` 建立 `BlockState`：

- Text；
- Thinking；
- ToolUse。

每个 block 独立累积正文、arguments、signature，直到 `ContentBlockStop` 才提交到最终 response accumulator。

---

## 60. MessageStart 很有价值

Messages 在正文前就给出：

- provider message ID；
- model；
- input tokens；
- cache-read tokens；
- cache-creation tokens。

Layer 2 立刻发 `ResponseStarted`，使 partial-mode 客户端不用伪造 message ID 或等待终态。

---

## 61. Text block

BlockStart 可能自带初始 text，后续 `TextDelta` 继续追加并实时发送 Text channel。

BlockStop 后 text 已经合并进全局 assistant text；最终仍以 terminal response accumulator 为准提交一次 AssistantItem。

---

## 62. Thinking block

Thinking block 维护：

- `thinking_acc`；
- `signature`。

Thinking delta 发 Reasoning channel。Signature delta 不作为可见 Reasoning 文本输出，而是保存在 block state。

---

## 63. ReasoningCompleted 为什么单独存在

到 `ContentBlockStop` 时 signature 才完整。

Layer 2 在 stop 前发 `ReasoningCompleted { signature }`，让 partial wire consumer 能按正确顺序输出 signature delta，再结束 thinking block。

最终 signature 同时保存为内部 Reasoning 的 encrypted content。

---

## 64. ToolUse block

BlockStart 提供 id/name，并分配 tool-only index；`InputJsonDelta` 逐片追加 arguments。

arguments accumulator 必须从空字符串开始，不能从 `{}` 开始再 append，否则会生成 `{}` + fragments 的非法 JSON。

---

## 65. Tool block stop 才形成完整 ToolCall

UI delta 可以提前显示；真正加入 `assistant_tool_calls` 的时机是 ToolUse block stop。

这保证 Agent Loop 只执行已经完整闭合的 Tool Call。

---

## 66. Messages stop reason 保留两份

Runtime 同时保存：

- 归一化 `StopReason`；
- `raw_stop_reason` 精确 wire string。

还单独保留 matched `stop_sequence` 与 refusal explanation，方便 UI 和诊断不丢 Provider 细节。

---

## 67. Messages stop mapping

| Wire reason | 内部语义 |
| --- | --- |
| `end_turn` | Stop |
| `max_tokens` | Length |
| `stop_sequence` | Stop |
| `tool_use` | ToolCalls |
| `refusal` | ContentFilter |
| `pause_turn` | 当前实现按 Stop，并告警 |
| `model_context_window_exceeded` | Length |
| unknown | Stop，并保留 raw 值、告警 |

---

## 68. Tool Call 胜过 Refusal

如果已闭合 ToolUse blocks，即使 terminal reason 是 Refusal，统一 stop reason 仍为 `ToolCalls`。

真实 Tool Call 是可执行模型输出，Agent Loop 必须先解决它们。

---

## 69. Messages Length 直接失败

transform 在最终构造 response 前若发现 Length，会发 `MaxTokensTruncation` Failed。

这避免把被截断的自然语言或半成品 Tool 意图写入持久 history。

---

## 70. Messages Usage 如何合并 cache buckets

Anthropic 风格分开报告：

- uncached input；
- cache read input；
- cache creation input；
- output。

统一值为：

```text
prompt_tokens = input + cache_read + cache_creation
cached_prompt_tokens = cache_read
cache_creation_prompt_tokens = cache_creation
total_tokens = prompt_tokens + output
```

---

# 第九部分：超时、空响应与终态裁决

## 71. Idle timeout 有两层

每个 transform 同时检查：

1. `stream.next()` 长时间完全不返回；
2. stream 持续发 ping/空 delta，但长时间没有 meaningful content。

只做第一层会让无限 keepalive 永远不超时。

---

## 72. 什么算 meaningful content

不同协议分别定义：

- 非空 text/reasoning/tool arguments；
- finish/terminal 状态；
- backend tool progress；
- refusal/error；
- block 生命周期等。

纯 Ping、Queued、InProgress 等 liveness event 通常不重置 content progress timer。

---

## 73. Completed 只是 Layer-2 成功解析

`drive_l2` 收到 Completed 后还要裁决：

- 是否有 confident doom-loop；
- stop reason 是否 Length；
- 是否 content-filtered；
- response 是否 empty。

只有通过这些检查，才成为 `AttemptOutcome::Completed`。

---

## 74. Empty response 为什么可重试

空响应可能是：

- reasoning-only；
- 没有 choice；
- stream 被上游异常截断但仍正常结束；
- Assistant text/tool 都为空。

Runtime 构造 `EmptyResponseContext`，记录 model、finish reason、usage、是否有 reasoning，再按 transient error 进入 retry。

---

## 75. ContentFilter 空响应为什么不重试

Refusal 是确定性 Provider 决策。重新采样同一请求会造成 retry storm。

所以 `ContentFilter` 即使没有文本/Tool，也被当作合法完成；Session 再向用户显示 provider refusal notice 与 explanation。

---

## 76. Length 为什么不是部分成功

被 max tokens 截断时：

- JSON 可能未闭合；
- Tool arguments 可能不完整；
- 回答语义可能停在否定/条件句中间；
- Reasoning 与正文可能不同步。

因此 Runtime 把它提升为 `MaxTokensTruncation`，交给上层决定 compact、缩减或报错，而不是持久提交半答案。

---

## 77. stream silent drop

若 Layer 2 没发 Completed/Failed 就结束，request task 合成：

```text
EventStreamError("stream dropped without terminal event")
```

`collect_response` 也有同类防线。终态缺失本身就是错误，不能以“已经收到一些文本”假装成功。

---

# 第十部分：Retry Runtime

## 78. Retry 分类是纯函数

`classify_error` 只根据：

- error；
- 已重试次数；
- max retries；
- rate-limit cap；

返回 `RetryDecision`。Sleep、改 request、重建 client 和发事件由 request task 执行。

纯分类使边界组合容易单测。

---

## 79. 通用 RetryDecision

- `Retry`；
- `RetryWithBackoff`；
- `RetryWithImageStrip`；
- `RetryWithClientRebuild`；
- `EmitToSession`；
- `Fatal`。

不同决策明确表达“重发同一请求”还是“先改变请求/客户端”。

---

## 80. 默认重试预算

默认最多 15 次，退避：

```text
2s → 4s → 8s → 16s → ... capped at 30s
```

再加 ±20% jitter，避免大量客户端同时重试形成 thundering herd。

环境变量优先于模型配置，再回退默认值。

---

## 81. 429 使用较低上限

Rate limit 默认只重试较少次数，并优先尊重 `Retry-After`。

429 的等待可能很长，不能无条件消耗完整 15 次 transport budget。

---

## 82. 首次 transport retry 重建 HTTP/1.1 Client

连接 reset、HTTP/2 pool 污染等错误可能在复用连接池中持续复现。

首次通用 retry 会构造 `force_http1=true` 的 fresh client，尝试逃离坏掉的 HTTP/2 connection state。

---

## 83. 413 与图片处理错误会改变请求

这类错误不是原样 retry：

1. `ConversationRequest::strip_images()`；
2. 若确实删除了图片，发 Retrying 并重提；
3. 若已无图片可删，升级 Fatal。

疑似 nginx 在 body upload 阶段 reset 连接时，也会在 retry 前主动 strip images。

---

## 84. x-should-retry 是服务端否决权

`x-should-retry: false` 会终止 retry，即使 HTTP status 平常可重试。

`true` 不会强迫客户端重试原本不可重试的错误；该 header 只做 veto，避免服务端错误提示放大请求风暴。

---

## 85. Context/size overflow 不原样重试

确定性 overflow 若请求内容不变，重发只会再次失败。

Sampler 将其 Fatal/Emit 给 Session；Session 拥有 token state 与 Compaction 能力，只有上层能真正改变上下文后 resubmit。

---

## 86. 401 归 Session 恢复

Sampler 不掌握完整 auth policy，因此返回 `EmitToSession`。

Session 再区分：

- provider-minted token；
- session token；
- BYOK；
- first-party endpoint；
- 请求是否真的带 credential；
- recovery budget。

成功刷新后外层 turn loop 重建请求并 resubmit。

---

## 87. Serialization 不重试

同一 response frame 再取一次大概率仍无法解析，而且 retry 可能重复模型副作用。

因此 response schema mismatch 默认 fatal；应修兼容 decoder，而不是用概率掩盖协议错误。

---

## 88. Idle timeout 当前不自动 retry

源码将 IdleTimeout 视为模型卡住；原样 retry 可能再次占用很长时间。

这个策略与一般 connection failure 不同，体现“等待成本”也是 retry 分类的一部分。

---

## 89. Doom-loop 使用独立预算

Doom-loop 不是 transport failure，而是当前 sample 的内容退化。

它：

- 使用近乎立即、只有小 jitter 的 backoff；
- 不消耗 transport retry count；
- 有独立 `max_retries`；
- 预算耗尽后 disarm 检测，接受下一次完整响应。

---

## 90. retry_only_before_output

某些配置要求一旦客户端已观察到输出就不再自动 retry。

`output_observed` 会被 Text、Reasoning、Tool delta、backend tool event 等置位；之后 effective max retries 变为 0。

这避免用户看见一段输出后，Runtime 静默换成另一份重新生成的答案。

---

# 第十一部分：Attempt commit 与 Session 接回

## 91. L2 terminal 被 request task 截留

`run_one_attempt` 不把 Layer-2 的 Completed 原样转发给 Session。

它先返回 `AttemptOutcome` 给 retry loop。只有最终成功 Attempt 才由 request task 发唯一的外部 `SamplingEvent::Completed`。

失败 Attempt 的 partial token 可用于 UI/trace，但不会成为 ChatState 权威 Assistant。

---

## 92. Streaming capture 是 out-of-band

Session 收到 ChannelToken 时，同时：

- 发 ACP chunk；
- 累积 `StreamingTurnCapture`。

该 capture 专供失败、取消、doom-loop 的 trace inspection：

- 不进入 ChatState；
- 不进入下一次模型请求；
- 不通过 `push_assistant_response` 持久化为权威答案。

---

## 93. Session 事件翻译

主要映射：

| SamplingEvent | Session 行为 |
| --- | --- |
| StreamStarted | 记录 stream start 与 capture segment |
| FirstToken | 发内部 FirstToken event |
| Text token | ACP AgentMessageChunk |
| Reasoning token | ACP AgentThoughtChunk |
| ToolCallDelta | xAI buffered tool-call delta |
| ResponseStarted | partial response framing |
| ReasoningCompleted | signature completion update |
| Retrying | RetryState notification、trace stamp |
| ModelMetadata | 更新模型真实 metadata |
| Completed | 结束 capture segment、记录 latency |
| Failed | typed error signals/logging |

---

## 94. 为什么有 stream-drain barrier

Sampler completion oneshot 与 event drainer 是两个任务。

若 turn loop 收到 response 后立即发 ToolCall notification，仍在队列中的最后几个文本 chunk 可能取得更晚的 eventId，客户端就会看到：

```text
文本前半 → ToolCall → 文本后半
```

Session 安装 oneshot barrier，等待 drainer 处理 Completed 后才开始 Tool Call 阶段。

---

## 95. Barrier 有超时兜底

等待上限为数秒。超时后告警并继续，避免 event drainer 故障永久挂死 Turn。

这是“尽力保证展示顺序”与“不能永远不收敛”的权衡。

---

## 96. Response 回填顺序

成功后 Session 大致执行：

1. 记录 latency、telemetry；
2. 记录 Provider Usage；
3. 构造 response-completed update；
4. 复制 Tool Calls 供下一阶段执行；
5. 遍历 response items；
6. Assistant 用 `record_assistant_response`；
7. Reasoning/BackendToolCall 等按非 Assistant item 回填 ChatState；
8. 必要时补发 fallback text；
9. 发 response-completed；
10. 根据 Tool Calls 继续 agentic loop 或进入终止 gates。

---

## 97. 为什么需要 fallback text

有时 terminal response 有 Assistant text，但流中没有任何 Text chunk，例如 Provider/代理只给最终对象。

`fallback_text()` 让 Session 补发一个 AgentMessageChunk，避免 ChatState 有答案而 UI 空白。

`message_chunks_emitted` 帮助判断是否已流式发过文本。

---

## 98. Usage 回填是单一入口

`record_response_token_usage` 同时更新：

- task output budget；
- ChatState total token anchor；
- last-turn usage；
- prompt/session usage ledger；
- model/cost/API duration；
- signals 中 completion/reasoning tokens。

若受严格 budget 的调用缺失 Usage，则 fail closed 或标记 ledger incomplete。

---

## 99. 两类 Token Total 必须分清

```text
billing cumulative fields
  prompt/completion/cached/reasoning

live context total
  当前模型上下文实际长度，用于 /context 与 compaction
```

Responses 特别可能同时给出两套口径。不能用累计计费总和直接代替当前 context length。

---

## 100. Reasoning item 的持久顺序

统一响应通常是：

```text
ConversationItem::Reasoning
ConversationItem::Assistant
```

回填时二者都进入 ChatState，下一轮再由目标 Provider converter 按其协议重新组合。

这使 Session 在中途切换 backend 时仍有机会保留语义。

---

# 第十二部分：修改与测试指南

## 101. 新增 Provider backend 的最小工作集

至少需要：

- ApiBackend variant；
- request wire types；
- ConversationRequest converter；
- HTTP endpoint/auth/headers；
- raw stream decoder；
- Layer-2 transform；
- Tool Call delta accumulator；
- Reasoning mapping；
- stop reason mapping；
- Usage normalization；
- rich error classification；
- request task dispatch；
- conversion/stream/integration tests。

---

## 102. 新增流事件时的检查清单

回答：

- 它是否表示 meaningful content？
- 是否置 `output_observed`？
- 是否重置 idle timer？
- 是否需要新的统一 SamplingEvent？
- 是否必须在 terminal 前按序发出？
- retry 后旧 Attempt 的该事件是否会误导 UI？
- 是否含敏感 Reasoning/credential 数据？

---

## 103. Tool Call 测试矩阵

至少覆盖：

- id/name/arguments 在同一片；
- arguments 跨多片；
- 并行 calls 交错；
- output/block index 不连续；
- delta 早于 start；
- 空 arguments；
- Unicode/转义边界；
- terminal Tool Call 与 delta accumulator 一致；
- Tool Call 存在但 Provider stop reason 错误。

---

## 104. Reasoning 测试矩阵

至少覆盖：

- Text 与 Reasoning 交错；
- reasoning-only completion；
- Responses terminal 缺内容但 stream 有 delta；
- Messages signature-only block；
- Chat Completions pending reasoning 被 User 打断；
- BackendToolCall 不打断合法 folding；
- trailing orphan reasoning；
- Fork/Compaction strip reasoning 后不残留 wire block。

---

## 105. Terminal 测试矩阵

至少覆盖：

- 正常 Stop；
- ToolCalls override；
- Length；
- ContentFilter with/without explanation；
- Empty plain；
- Empty reasoning-only；
- SSE Error frame；
- silent stream drop；
- transport idle；
- keepalive-only idle；
- unknown stop reason；
- pause_turn；
- context-window-exceeded output stop。

---

## 106. Retry 测试矩阵

至少覆盖：

- 500/502/503/504；
- connection reset 的 HTTP/1 fallback；
- 429 + Retry-After + cap；
- 413 有图/无图；
- image-processing 400/500；
- `x-should-retry=false`；
- serialization fatal；
- auth EmitToSession；
- cancel during request；
- cancel during backoff；
- retry_only_before_output；
- doom budget 与 transport budget 独立；
- 最终只发一次 Completed/Failed。

---

# 第十三部分：调试手册

## 107. UI 有乱码或重复文本

检查：

- Provider 是否同时发送 start 初始文本与 delta，是否重复 append；
- retry Attempt 的旧 chunks 是否被客户端当新答案拼接；
- chunk index/eventId 是否乱序；
- fallback text 是否在已有 streamed text 后再次发送；
- Message block stop 是否重复 commit。

---

## 108. Tool arguments 执行时 JSON 不完整

检查：

- accumulator 是否以空字符串初始化；
- 是否按 index 隔离并行 calls；
- Tool 执行是否错误使用 `ToolCallDelta`，而非 terminal ToolCall；
- Responses output_index 映射是否建立；
- stream 是否 Length/Failed 却被当成功。

---

## 109. Reasoning 在切换模型后消失

检查：

- ChatState 中是否有独立 Reasoning sibling；
- Chat Completions folding 是否被非 Assistant item 打断；
- Messages signature 是否放入 encrypted content；
- Responses reasoning_text discriminator 是否 patch；
- Fork/Compaction 是否按设计 strip reasoning；
- Provider 是否根本不支持回放该 reasoning 形态。

---

## 110. Context bar 与账单 Token 不一致

先判断比较的是：

- 当前 live context length；还是
- 本请求累计 billable usage。

再检查 Responses `context_details` override、Messages cache buckets、ChatState `record_token_usage` 是否在成功响应后调用。

---

## 111. 失败后为什么没有自动重试

检查：

- error kind；
- `x-should-retry=false`；
- max retries 是否为 0；
- 是否已输出且 `retry_only_before_output=true`；
- 是否 IdleTimeout/Serialization/Auth/Length；
- 是否 deterministic context overflow；
- rate-limit cap 是否已到。

---

## 112. 模型回答完成但 Tool UI 插到正文中间

检查：

- `turn_stream_drained` sender 是否安装；
- drainer 是否在处理 Completed 时发送 barrier；
- wait 是否超时；
- Tool Call notification 是否绕过 turn loop 提前发送；
- eventId 是否在 `send_update` 调用时分配。

---

# 第十四部分：学习实验

## 113. 实验一：同一 ConversationRequest 三协议序列化

构造：

```text
System
User(text + image)
Reasoning
Assistant(text + 2 tool calls)
2 ToolResults
```

分别调用三个 converter，逐项标注 role/item/block 的变化。

---

## 114. 实验二：手写三套 Tool delta

分别构造：

- Chat Completions tool call chunks；
- Responses OutputItemAdded + arguments deltas；
- Messages ToolUse block + InputJsonDelta。

验证三者最后得到完全相同的内部 `ToolCall`。

---

## 115. 实验三：Keepalive 不能掩盖空转

每隔短时间发一个 Ping/Queued/空 delta，但不发 meaningful content。

断言 content-aware idle timer 最终产生 IdleTimeout，而 transport-level timeout不会被误认为足够。

---

## 116. 实验四：Retry Attempt 隔离

Attempt 1 流出半句后 500，Attempt 2 成功。

验证：

- partial capture 记录 Attempt 1；
- ChatState 只写 Attempt 2 terminal items；
- 最终只出现一个 sampler Completed；
- retry-only-before-output 模式下行为相反：观察输出后不重试。

---

## 117. 实验五：Cache Usage 归一化

为 Messages 构造：

```text
input=100
cache_read=800
cache_creation=50
output=40
```

验证统一 usage：prompt=950、cached=800、creation=50、total=990。

---

# 第十五部分：最终心智模型

## 118. 一次 Sampling 的完整状态机

```text
Build internal request
      │
      ▼
Apply defaults + auth + headers
      │
      ▼
Convert to selected wire protocol
      │
      ▼
HTTP handshake ──fail──► InitFailed ──classify──► retry/session/fatal
      │
      ▼
Decode SSE raw events
      │
      ▼
Layer-2 accumulator
      ├── intermediate SamplingEvents ──► UI / trace
      └── Completed or Failed
                 │
                 ▼
       Attempt semantic validation
       doom / length / empty / refusal
                 │
        retry ◄──┴──► accepted
                       │
                       ▼
              one canonical Completed
                       │
                       ▼
            stream-drain ordering barrier
                       │
                       ▼
       Usage + ConversationItems → ChatState
                       │
                       ▼
           Tool execution or turn finish
```

---

## 119. 最值得记住的设计判断

1. 内部 `ConversationRequest`/`Response` 是跨 Provider IR，不应绑定某一 wire format。
2. HTTP Client、stream transform、retry task、Session semantic recovery 是四个不同责任层。
3. 流式 delta 面向实时体验，terminal object 才是可持久提交的权威结果。
4. Tool Call arguments fragment 永远不能单独执行。
5. Reasoning sibling 让三协议之间的转换更少丢失，但必须严格保持邻接顺序。
6. `Completed` 仍要经过 doom、length、empty、refusal 语义裁决。
7. ContentFilter 是合法终态，Length 是不可信半成品，二者不能同样处理。
8. Retry 必须明确哪些动作改变 request/client；原样重发确定性错误没有意义。
9. 旧 Attempt 的 partial output 可以进 UI/trace，但不能进 ChatState 权威 history。
10. stream-drain barrier 证明并发正确性不仅是数据正确，还包括客户端观察顺序。

---

## 120. 下一篇建议

下一篇适合继续做：

**源码精读 30：Agent Tool-Call Protocol State Machine——模型 Tool Call 从流式 arguments、完整性校验、别名解析、并行 Batch、Hook、Permission、执行、ToolResult 配对，到下一次 Sampling 的精确状态变化。**

虽然此前已经研究过 Tool Runtime，本篇之后可专门聚焦“模型协议状态”而不是工具实现，把 Tool Call/Result 如何维持跨 Provider history 合法性完整串起来。

---

## 121. Glossary（术语表）

| 名词 | 白话解释 | 本文中的具体含义 |
| --- | --- | --- |
| Sampling | 采样/推理调用 | 把上下文发送给模型并接收一次生成结果 |
| Sampling Runtime | 采样运行时 | 请求转换、HTTP、SSE、流归并、重试、取消和终态处理的整体 |
| Provider | 模型服务提供方 | 接受某种 API wire protocol 的推理服务 |
| backend | 后端协议形态 | Chat Completions、Responses 或 Messages，不必等于厂商品牌 |
| wire format | 线上格式 | 实际经 HTTP JSON/SSE 发送和接收的数据结构 |
| IR | 中间表示 | Provider-neutral 的 ConversationRequest/ConversationResponse |
| DTO | 数据传输对象 | 为某个 API 请求或响应定义的结构体 |
| Chat Completions | 对话补全协议 | 使用 role messages、choices 与 delta 的 API 形态 |
| Responses API | Responses 协议 | 使用 typed input/output items 和细粒度 response events 的 API |
| Messages API | Messages 协议 | 使用 system + user/assistant content blocks 的 Anthropic 风格 API |
| SamplingClient | 采样 HTTP 客户端 | 负责 URL、认证、headers、wire conversion 和原始 stream |
| SamplerActor | 采样 Actor | 管理并发 request task、取消与生命周期 |
| request task | 每请求任务 | 独占某个请求的 attempt、retry 和 completion 状态 |
| Layer 1 | 第一层 | 原始 HTTP/Provider stream 层 |
| Layer 2 | 第二层 | 把 Provider stream 转成统一 SamplingEvent 的纯转换层 |
| Layer 3 | 第三层 | Actor、并发请求、重试和取消协调层 |
| SSE | Server-Sent Events | 服务端以连续 `data:` frame 推送生成事件的 HTTP 流协议 |
| chunk | 数据片 | Chat Completions 流的一段 delta 或泛指流式小片段 |
| delta | 增量 | 相对先前状态新增的一小段文本、参数或元数据 |
| accumulator | 累加器 | 把多个 delta 合并成最终文本/Tool Call/Reasoning 的状态 |
| content block | 内容块 | Messages 中 Text、Thinking、ToolUse、ToolResult 等有 index 的单元 |
| output item | 输出项 | Responses 中 Message、FunctionCall、Reasoning、HostedToolCall 等单元 |
| choice | 候选输出 | Chat Completions 响应中的生成候选；本 Runtime 主要处理其 delta |
| role message | 角色消息 | system/user/assistant/tool 形式的 Chat Completions 消息 |
| reasoning sibling | 推理兄弟项 | 内部 history 中位于 Assistant 旁边的独立 Reasoning item |
| folding | 折叠 | 把独立 Reasoning 合并到 Chat Completions assistant.reasoning_content |
| thinking block | 思考块 | Messages 协议中携带 thinking 与 signature 的 Assistant block |
| signature | 推理签名 | Provider 返回的 opaque encrypted reasoning continuation data |
| discriminator | 类型判别字段 | JSON 中如 `type: reasoning_text`，用于选择 union variant |
| Tool Call | 工具调用 | 模型输出的工具 ID、名称和 JSON arguments |
| ToolCallDelta | 工具调用增量 | 尚未完整的 id/name/arguments 片段，仅供展示和累积 |
| tool index | 工具序号 | 统一事件中只计算 Tool Call 的连续 index |
| output index | 输出序号 | Responses 中所有 output item 共用的 index |
| block index | 块序号 | Messages 中 content block 的位置标识 |
| Tool Result | 工具结果 | 对某个 call ID 的执行返回；三协议的容器不同 |
| hosted tool | 后端托管工具 | 在 Provider 服务端执行的 Web/X Search、Code Interpreter 等工具 |
| client tool | 客户端工具 | 由 Grok Build 本地 Tool Runtime 执行的工具 |
| raw JSON injection | 原始 JSON 注入 | SDK enum 无法表达扩展工具时，序列化后插入额外字段 |
| stream transform | 流转换器 | 将某协议 raw events 归一为 SamplingEvent/ConversationResponse |
| SamplingEvent | 统一采样事件 | Text、Reasoning、Tool delta、Retry、Completed 等跨协议事件 |
| channel | 内容通道 | Text 或 Reasoning 的统一分类 |
| FirstToken | 首 Token 事件 | 第一次出现文本或 Reasoning 内容时发出的延迟测量信号 |
| TTFB | 首字节时间 | HTTP stream 建立/收到首数据的延迟概念 |
| TTFT | 首 Token 时间 | 从请求开始到第一个有效生成 token 的时间 |
| ITL | Token 间延迟 | 连续生成 chunk/token 之间的延迟统计 |
| meaningful content | 有效进展 | 会推进模型输出而非纯 keepalive/status 的事件 |
| keepalive | 保活事件 | 证明连接活着但不代表生成有进展的 Ping/空事件 |
| idle timeout | 空闲超时 | 长时间没有 transport 数据或有效内容时终止 stream |
| terminal event | 终态事件 | 一次流唯一的 Completed 或 Failed |
| terminal object | 终态对象 | Provider 在流末返回的完整 response/message 数据 |
| Attempt | 一次尝试 | 同一逻辑请求在 retry loop 中的一次 HTTP+stream 执行 |
| canonical response | 权威响应 | 最终被接受并写入 ChatState 的成功 Attempt 结果 |
| partial output | 部分输出 | 失败/取消 Attempt 已经流出的内容，只供 UI/trace |
| empty response | 空响应 | 没有 Assistant 正文也没有可执行 Tool Call 的完成结果 |
| reasoning-only | 只有推理 | 有 Reasoning 但没有最终正文/Tool Call 的响应 |
| stop reason | 停止原因 | Stop、ToolCalls、Length、ContentFilter 等统一终止语义 |
| raw stop reason | 原始停止原因 | Provider wire 上未经归一化的精确字符串 |
| stop sequence | 停止序列 | 命中的用户/配置停止字符串 |
| refusal | 拒答 | Provider 因安全或政策不生成正常回答 |
| ContentFilter | 内容过滤终态 | 对 refusal 的统一语义，视为确定性合法完成 |
| MaxTokensTruncation | 最大 Token 截断 | 输出达到上限，结果可能不完整，不作为普通成功 |
| Usage | Token 使用量 | prompt、completion、cached、reasoning、total 等计量字段 |
| cached tokens | 缓存命中 Token | Prompt 前缀从 Provider cache 复用的输入量 |
| cache creation tokens | 建缓存 Token | Messages 报告的本轮创建 cache 的输入量 |
| live context total | 当前上下文长度 | 驱动 context bar 和 compaction 的实时总量 |
| billing cumulative | 累计计费口径 | 本次请求/服务端 agent loop 累计消耗的 Token 字段 |
| Retry | 重试 | 因可恢复错误重新执行 Attempt |
| retry budget | 重试预算 | 某一错误类别最多允许多少次新 Attempt |
| backoff | 退避 | 重试前等待时间随次数增长 |
| jitter | 抖动 | 在退避时间上加入随机偏移，避免并发请求同步重试 |
| thundering herd | 惊群 | 大量客户端同时重试压垮服务的现象 |
| Retry-After | 服务端等待提示 | 429 等响应告诉客户端应等待多少秒 |
| retry veto | 重试否决 | `x-should-retry=false` 或确定性 overflow 阻止原样重发 |
| HTTP/1 fallback | HTTP/1 降级 | 首次 transport retry 重建 Client，避开坏 HTTP/2 pool |
| image strip | 图片剥离 | 413/图片处理失败后改变请求、删除 inline images 再试 |
| EmitToSession | 交给 Session | Sampler 无权处理 Auth/Context 等语义，让上层恢复 |
| doom loop | 退化循环 | 模型重复/异常生成模式，由信号检测后重新采样 |
| resample | 重新采样 | 请求语义不变但丢弃退化 generation，生成新的输出 |
| output_observed | 已观察输出 | 标记客户端是否已见内容，用于禁止输出后的静默 retry |
| tee | 分流复制 | 流继续交给 Layer 2，同时旁路保存第一个 rich error |
| rich error | 富错误 | 保留 reqwest/serde/status/metadata 等不可直接序列化信息的错误 |
| SamplingErrorInfo | 可传输错误 | 从 rich SamplingError 提取的可序列化稳定字段 |
| cancellation token | 取消令牌 | 协作式通知 request/退避立即停止的信号 |
| completion oneshot | 完成单次通道 | request task 向 submit-and-collect 调用者返回唯一结果 |
| stream drainer | 流事件排空任务 | 把 SamplingEvent 翻译成 ACP/UI 更新的独立任务 |
| stream-drain barrier | 流排空屏障 | Tool notification 前等待所有文本/思考 chunk 已获得正确 eventId |
| fallback text | 兜底正文 | terminal 有文本但没流过 chunk 时，Session 补发的 UI 消息 |
| prompt cache | Prompt 缓存 | Provider 对稳定输入前缀的复用机制 |
| cache breakpoint | 缓存断点 | Messages 请求中标记可缓存稳定前缀末端的位置 |
