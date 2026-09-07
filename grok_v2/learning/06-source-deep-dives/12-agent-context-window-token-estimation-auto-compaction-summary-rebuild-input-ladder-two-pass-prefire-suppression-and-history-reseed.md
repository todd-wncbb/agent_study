# 源码精读 12：Agent 如何估算上下文、生成压缩摘要并重建 Conversation

> 本篇继续沿着 Agent Runtime 的基础链路下钻，精读 grok-build 的上下文管理与 Compaction：Provider Token Usage 和本地 Bytes/4 Estimate 怎样拼成当前上下文占用；自动阈值、Tool Result Preflight、Model Switch 和错误恢复分别在何时触发压缩；Compaction Model 实际看见什么 Prompt；Verbatim、Fitted、Lossy 三段输入阶梯怎样避免“会话太长，连压缩请求自身也发不出去”；Summary 如何清洗、校验并与 System Prompt、AGENTS.md、最后真实用户问题、Todo、后台任务、Subagent、MCP、Memory 和 Plan Mode 重新组装；最终 Chat State 又怎样原子替换历史并重置 Token 基线。
>
> 源码基线：`ed6d543`。源码变化后优先按本文列出的类型、函数和测试名重新定位。

---

## 1. 先给结论：Compaction 不是删除旧消息，而是一次受控状态迁移

最粗糙的上下文管理是：

```text
超过窗口 -> 删除最老的消息
```

grok-build 的主路径更接近：

```text
权威 Conversation
   |
   | Provider usage + post-response estimate
   v
触发判断
   |
   | snapshot + simplify / fit
   v
Compaction Model 生成 successor summary
   |
   | clean + reject degenerate + validate tool pairing
   v
重建新的 Conversation
   |
   | persist checkpoint + actor replace + token reseed
   v
下一次模型请求从新历史继续
```

所以 Compaction 同时处理四个问题：

1. **容量问题**：让下一次请求重新落入 Context Window。
2. **连续性问题**：保留用户目标、当前进度、关键代码与未完成任务。
3. **协议问题**：新历史仍满足 Tool Call/Tool Result、Reasoning 和 Provider Schema 不变量。
4. **运行态问题**：保留 Todo、后台命令、Subagent、MCP、项目指令和 Plan Mode 等 Conversation 外状态。

---

## 2. 本篇主线文件

| 文件 | 责任 |
| --- | --- |
| `xai-token-estimation/src/lib.rs` | Bytes/4、Image Estimate、Usage 百分比与阈值算法 |
| `xai-chat-state/src/actor/state.rs` | Token 权威值、本地增量与响应时 Estimate 基线 |
| `xai-chat-state/src/actor/mutations.rs` | 新 Item 增量估算、Token Usage 记录、Conversation Replace 与 Reseed |
| `xai-chat-state/src/compaction_utils.rs` | Summarization 输入简化、Budget Fit、Summary 清洗与新历史构造 |
| `xai-chat-state/src/compaction_mode.rs` | Summary、Transcript、Segments 三种恢复模式 |
| `xai-grok-shell/src/session/compaction_config.rs` | 阈值、抑制状态、Prefire Cache 与 Cancel Gate |
| `xai-grok-shell/src/session/compaction.rs` | 触发、采样、输入阶梯、上下文恢复、替换与通知主线 |
| `xai-grok-shell/src/session/helpers/session_compact.rs` | Compaction Prompt、Sampling Stream 与错误分类 |
| `xai-grok-shell/src/session/helpers/full_replace_compaction.rs` | Shell 到共享 Full-replace Engine 的 Adapter |
| `xai-grok-shell/src/session/helpers/compaction_context.rs` | 压缩时应保留的运行态快照与 Reminder |
| `xai-grok-shell/src/session/compaction_segments.rs` | Transcript/Segment 持久化模式分派 |
| `xai-grok-shell/src/session/acp_session_impl/turn.rs` | Turn 中的 Prefire、Pre-sampling 与 Tool 后 Preflight 触发点 |
| `xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs` | Sampling Error 后的 Overflow Recovery |
| `xai-grok-shell/src/session/acp_session_tests/inline_auto_compact_flow_tests.rs` | 跨 Session/Sampler/Chat State 的集成不变量 |

---

## 3. 先区分五个经常混在一起的概念

### 3.1 Context Window

模型一次请求能接收和生成的总 Token 容量上限。

### 3.2 Token Accounting

记录 Provider 最近一次响应报告的真实/近真实 Usage，并估算响应后新增内容。

### 3.3 Request Pruning

构建某次请求的 Clone 时，对旧 Tool Output 做临时裁剪；不一定修改权威 Conversation。

### 3.4 Retained-history Hard Clear

对非常旧的 Tool Result 做内存级硬清理，确实修改保留历史，但目标主要是释放字符串内存。

### 3.5 Compaction

调用模型生成摘要，构造并替换一份新的权威 Conversation。

它们不是同义词。

---

## 4. 为什么不能只相信 Provider 的 `total_tokens`

Provider Usage 只在模型响应完成后出现。

响应完成后，Agent 可能继续追加：

- Assistant Tool Call。
- Tool Results。
- 新 User/Synthetic Reminder。
- Working Directory Switch。
- Background Task Completion。

在下一次模型请求之前，这些内容还没有 Provider Usage。

如果只看上次 `total_tokens`：

```text
上次响应：80k
工具输出：+40k
当前真实请求约：120k
```

系统仍会误以为只有 80k。

所以 Chat State 保存：

```text
estimated_total_tokens =
    last_provider_total_tokens
  + estimated_tokens_since_model
```

---

## 5. Token State 的三个核心字段

### 5.1 `total_tokens`

最近一次 Provider 响应报告的累计上下文 Token 数；在 Conversation Replace 后会被重新播种。

### 5.2 `estimated_tokens_since_model`

上次 `record_token_usage` 之后，新增的非 Assistant Conversation Item 的本地估算。

### 5.3 `estimate_at_last_response`

上次记录 Provider Usage 时，同一份 Conversation 用本地估算器算出的 Token 数。

这个字段不是另一个 Total，而是校准基线：

```text
provider_total / local_estimate_at_response
```

近似表示本地估算和 Provider 计数之间的比例差异。

---

## 6. 为什么 `estimate_at_last_response` 必须冻结

响应之后，Conversation 可能被：

- 追加 Tool Output。
- Prune 老 Tool Result。
- Rewind。
- Restore Snapshot。

若在 Compaction 时拿“当前 Conversation Estimate”与旧 Provider Total 做比例，会把响应后的变化错误解释成 Provider Overhead。

冻结基线使校准关系仍指向同一次响应的同一份历史。

这是一个通用数据工程原则：

> 两个数要做 Ratio，必须来自同一时刻、同一数据快照。

---

## 7. Bytes/4 Estimator 到底在估什么

共享 `xai-token-estimation` 提供：

```rust
estimate_tokens(s) = s.len() / 4
```

其中 `len()` 是 UTF-8 Byte Length，不是 Unicode Character Count。

这意味着：

- ASCII 约四字节一个估算 Token。
- 中文通常一个字符三字节，估算关系不同。
- 它不是模型真实 Tokenizer。

它的定位是：

- 快。
- 确定性。
- 不需加载模型词表。
- 适合新内容增量和触发前保护。

---

## 8. 图片怎样进入估算

共享常量：

```text
IMAGE_TOKEN_ESTIMATE = 765
```

每张图按低分辨率 Patch 的近似成本计数。

原因是 Base64 URL 的字节数与模型视觉 Token 成本不是同一个量；直接按字符串 Bytes/4 会极度失真。

---

## 9. `push_message` 为什么不估算 Assistant

通用 `push_message` 只把非 Assistant Item 计入 `estimated_tokens_since_model`。

因为 Assistant Response 到来时，Provider 的 `total_tokens` 已经包含这次输出；紧接着的 `record_token_usage` 会清零增量并把 Provider Total 设为新基线。

若同时把 Assistant 再加到增量中，会双计。

而 Tool Result 通常发生在响应之后、下次响应之前，所以必须本地估算。

---

## 10. `record_token_usage` 是一次校准点

成功模型响应后：

1. `estimated_tokens_since_model = 0`。
2. 用当前 Conversation 计算 `estimate_at_last_response`。
3. `total_tokens = provider total_tokens`。
4. 发 `TokensUpdated`。

这一步把混合估算重新锚定到 Provider 报告。

---

## 11. 触发阈值的整数算法

共享函数：

```text
used * 100 >= context_window * threshold_percent
```

默认 Threshold 是 85%。

注意是大于等于，不是严格大于。

例如：

```text
context_window = 100_000
threshold = 85
used = 85_000
```

刚好触发。

算法使用 Saturating Integer Arithmetic，避免大数乘法溢出。

---

## 12. 展示百分比与触发判断为什么分开

`usage_percentage_u8` 使用浮点并四舍五入。

`usage_percentage_truncated_u8` 使用整数并截断。

真正 Trigger 使用交叉乘法，不依赖展示百分比。

这避免：

```text
UI 显示 85%
```

却因 Round/Truncate 差异让内部阈值在临界点漂移。

---

## 13. Threshold 配置的优先级

自动压缩百分比按层解析：

1. 环境变量 `GROK_AUTO_COMPACT_THRESHOLD_PERCENT`。
2. 用户 TOML 的 Per-model 设置。
3. 用户 TOML `[session]` 设置。
4. Remote Per-model 设置。
5. Remote Global 设置。
6. 默认 85。

越靠前优先级越高。

Model Switch 时会重新解析并写入 `Cell<u8>`，所以 Threshold 可以随模型变化。

---

## 14. 自动压缩有四个触发入口

### 14.1 Pre-sampling Threshold

每次模型请求前，Estimate 达到配置阈值就压缩。

### 14.2 Tool-output Preflight Overflow

Tool 执行完成后，若 Estimate 已经超过完整 Context Window，在下一轮请求前立即压缩。

### 14.3 Sampling Error Recovery

请求已经失败，Error Metadata 的 Context Window 小于当前 Estimate，压缩后重提。

### 14.4 Model-switch Shrink

模型切换后窗口变小，并且当前占用达到新模型阈值，主动压缩。

此外还有用户主动 `/compact`，它属于 Manual Trigger。

---

## 15. Pre-sampling Check 在 Turn 中的位置

Turn Loop 先处理：

- Pending Interjection。
- Skill Reminder。
- Monitor Event。
- First-turn Memory Reminder。
- MCP Reminder。
- Two-pass Prefire。
- Token Refresh。

然后执行 `check_auto_compact_needed`。

这意味着触发估算包含部分即将进入下一请求的运行态注入，而不是只看上一轮静态历史。

---

## 16. Memory Flush 期间为什么不触发 Pre-sampling Compact

`check_auto_compact_needed` 首先检查 `memory.is_flushing`。

Flush 期间返回 None。

原因是 Memory Flush 与 Compaction 都可能读取和总结当前 Conversation；同时运行可能：

- 重复消费同一状态。
- 让压缩先替换历史，Flush 后读到不一致快照。
- 增加同 Session 并发采样负担。

---

## 17. Debug Force Trigger 是一次性的

`force_compact` 是 `AtomicBool`。

检查使用 `compare_exchange(true, false)`：

- 成功者消费该标志。
- 只强制触发一次。
- 后续恢复普通阈值逻辑。

这适合测试与诊断，而不会把 Session 永久锁进每轮压缩。

---

## 18. Tool-output Preflight 为什么单独存在

一次 Tool Call 可能返回几十万字符。

流程是：

```text
模型请求 Tool
  -> Tool Result 进入 Chat State
  -> estimated_tokens_since_model 大增
  -> 下一次模型请求尚未发出
```

`check_preflight_overflow` 在 Tool Loop 末尾检查：

```text
estimated_total > context_window
```

若成立，先压缩并 `continue`，不把已知超窗的请求交给 Provider。

这比等待一个 400/413/Context Error 更快、更便宜。

---

## 19. Error-recovery Trigger 为什么不用错误字符串

`should_compact_on_error` 要求：

1. 没有 Auto Compaction Suppression。
2. Error 有 `model_metadata`。
3. Metadata 有非零 `context_window`。
4. `estimated_total > context_window`。

它不依赖 Message 中是否含 `context length exceeded`。

结构化 Metadata 能跨不同 Provider 文案保持一致。

---

## 20. Model Switch Trigger 的边界

Session 在 Turn 结束保存：

- 上一个 Model Slug。
- 上一个 Context Window。

下一 Turn 若模型变化：

- Credit/Auth Suppression 不会被 Model Switch 清除。
- 其他 Suppression 可清除。
- 新窗口不小于旧窗口：无需主动压缩。
- 新窗口更小且当前已达阈值：执行压缩。

Model Switch 能解决容量问题，但不能解决账号余额或认证问题。

---

## 21. Manual `/compact` 与 Auto Compact 的差异

Manual Path：

- 可接受用户附加 Context。
- 忽略 Auto Suppression Gate。
- Trigger Telemetry 标为 Manual。
- 完成后发送 Completed Notification。

Auto Path：

- 由 Threshold/Overflow/Recovery 触发。
- 受 Suppression State 控制。
- 发送 Started、Completed、Failed 或 Cancelled 通知。
- 外层 Turn Loop负责压缩后继续请求。

---

## 22. `run_compact_only` 为什么叫 Only

它只做压缩，不在函数内部重新运行用户 Turn。

成功后：

- Chat State 已替换。
- UI 收到 AutoCompactCompleted。
- 调用者继续自己的控制流。

例如：

- Pre-sampling Path 压缩后继续构建当前请求。
- Error Recovery 返回 `CompactAndResubmit`，外层重建请求。
- Tool Preflight 压缩后 `continue` Agentic Loop。

这避免 Compaction Helper 偷偷拥有 Turn 控制权。

---

## 23. Auto Compact 的通知生命周期

开始时：

```text
AutoCompactStarted {
  tokens_used,
  context_window,
  percentage,
  reason
}
```

成功时：

```text
AutoCompactCompleted {
  tokens_before,
  tokens_after,
  elapsed_ms
}
```

失败时：

- 确定性失败可能由 Suppression Transition 发送带解释的 `AutoCompactFailed`。
- 其他非 Cancel Error 若尚未 Suppress，会发送空错误占位的 Failed。
- Cancel 发送 `AutoCompactCancelled::UserCancelled`。

---

## 24. Compaction 前为什么可能先 Flush Memory

`maybe_pre_compaction_flush`：

1. 先递增 Compaction Count。
2. 检查 Agent Policy 是否启用 Memory Flush。
3. 检查 Token、Threshold、Flush Config 和上次 Flush Cycle。
4. Snapshot 当前 Memory Flush State。
5. `spawn_local` 异步执行。

目标是把值得跨会话长期保存的事实写入 Memory，再把短期 Conversation 压缩。

Memory 与 Compaction 的时间尺度不同：

- Memory：长期、可检索、跨上下文。
- Compaction Summary：当前 Session 的连续工作状态。

---

## 25. Compaction 自己也是一次模型请求

压缩并不是无损算法，而是调用模型生成摘要。

它需要：

- Sampling Config。
- Sampling Client。
- Tool Definitions。
- Hosted Tools。
- Tool Choice。
- Idle Timeout。
- Wall-clock Budget。
- Cancellation Token。

因此 Compaction 自身也会失败、超时、认证失败、超窗或返回退化内容。

上下文管理必须管理“管理上下文的模型调用”。

---

## 26. Compaction Model 为什么仍拿到 Tool Definitions

生成器接收当前有效 Tool Definitions 与 Hosted Tools。

但 Prompt 明确要求不调用 Tool，只输出 Summary。

保留 Tool Schema 可能有几个现实原因：

- 复用统一 Sampling Client/Backend Request Path。
- 某些模型行为依赖当前工具语境。
- Tool Choice Policy 可在 Adapter 层控制。

无论原因如何，Tool Schema 的 Token 成本必须从 Compaction Input Budget 中扣除。

---

## 27. Summarization 输入有两种初始模式

### 27.1 Verbatim Input

尽量保留原始 Conversation：

- Tool I/O 保留。
- 图片保留。
- 可按 Backend 需求 Strip Reasoning。
- 尾部未完成 Tool Call 被截掉。

优点是信息完整、Cache Alignment 更好。

### 27.2 Lossy Input

`prepare_conversation_for_summarization`：

- 删除 Tool Results。
- 把 Assistant Tool Calls 变成 `[Called tools: ...]` 文本。
- 删除 Reasoning Items。
- 图片替换为 `[image]`。

优点是输入小、协议简单、不易让 Summarizer 本身超窗。

---

## 28. 为什么修改 Assistant Tool Text 前必须 Strip Reasoning

某些 Provider 的 Thinking/Reasoning Block 带签名，并与周围内容绑定。

Lossy Prep 会修改 Assistant Text 并清掉 Tool Calls。

若保留原 Signed Reasoning：

- Reasoning Signature 对不上修改后的消息结构。
- Strict Provider 可能返回 400。

因此顺序是：

```text
flatten tool calls
strip reasoning
strip images
```

不是单纯为了省 Token，也是在维护 Provider 协议合法性。

---

## 29. 为什么图片在 Lossy 模式中变成 `[image]`

直接删除会让 Summary 丢失“用户曾提供图片”这一语义。

保留 Base64 又可能带来数 MB 输入。

占位符保留事件事实，丢弃不可承受的 Payload。

---

## 30. Verbatim 模式为什么截掉尾部不完整 Tool Call

若最后一个 Assistant Item 有 Tool Calls，但尚无对应 Tool Result：

- Strict Backend 可能拒绝 dangling `tool_use`。
- Summarizer 也无法知道工具结果。

`truncate_trailing_incomplete_tool_call` 会连续移除这类尾部 Assistant Item。

它不是把中间任意 Tool Call 删除，只处理尾部未闭合状态。

---

## 31. Compaction Input Ladder

若启用 Verbatim，输入阶梯为：

```text
Verbatim
  -> VerbatimFitted
  -> Lossy
```

若一开始关闭 Verbatim，则直接从 Lossy 开始。

只有 Error 被标为 `context_overflow` 时才向下一 Stage 降级。

Transient 网络错误不会让输入从完整模式无故退化。

---

## 32. Stage 1：Verbatim

尽可能把完整 Conversation 交给 Summarizer。

它适合：

- Context Window 足够大。
- Tool Output 中有关键细节。
- Prefix Cache 能复用。
- 需要高保真续接。

失败若不是 Context Overflow，不自动降级成 Lossy。

---

## 33. Stage 2：VerbatimFitted

预算：

```text
context_window
- 32_768 summary reserve
- tool_definition_tokens
```

然后 `fit_conversation_to_budget` 从最新内容向前保留 Whole Items/Turns。

目标是给 Summary Output 和 Tool Schema 留出明确空间，同时尽量保住原始 Tool I/O。

---

## 34. `fit_conversation_to_budget` 怎样避免切断 Tool Pair

算法大致是：

1. System Item 单独保留。
2. 从 Conversation 尾部向前累计 Item Cost。
3. 超过 Budget 时确定 Start。
4. 若 Start 落在 Tool Result 中，向后移动到非 Tool Result。
5. 若一个完整尾部单元都放不下，进入 Tail Recovery。

Tail Recovery 会尽量保留：

- Tool Result 对应的 Assistant Owner。
- 最近 Tool Results 的截断版本。

而不是只留下孤立 Tool Result。

---

## 35. Stage 3：Lossy

预算：

```text
context_window * 70%
- tool_definition_tokens
```

先做 Lossy Prep，再 Fit。

70% 给 Summary Output、协议开销与 Estimate Error 留出更大安全空间。

若 Lossy 仍 Context Overflow：

- 认为当前输入无法自动压缩。
- Auto Path 进入 Size Suppression。
- 返回确定性失败。

---

## 36. 为什么输入阶梯只向前走

代码中的 `InputStage` 只有：

```text
Verbatim -> VerbatimFitted -> Lossy
```

没有回退到更完整 Stage。

一次 Compaction Attempt 中，Context Overflow 已经证明当前 Stage 太大；回退会重复确定性失败。

---

## 37. Full-replace Engine 负责什么

共享 `xai_grok_compaction::sample_full_replace_summary` 负责：

- 调用 Sampler Adapter。
- Attempt 循环。
- 退化 Summary 判定。
- Deterministic/Transient Error 分类传播。
- Retry Delay。

Shell 层仍负责：

- 输入阶梯。
- Auto Suppression。
- Session Context 重建。
- Persistence 与 Notification。
- Chat State Replace。

这是 L4/L5 分层：共享采样策略与产品 Session 语义分开。

---

## 38. Compaction 默认最多三次采样

`FullReplaceConfig` 使用：

- `max_attempts = 3`。
- `retry_delay_secs = 3`。
- `sampling_timeout_secs = 0`，由 Shell 自己的 Timeout/Budget 体系约束。

并非所有失败都会重试三次：

- Deterministic Failure 立即停止。
- Context Overflow 可能先切 Input Stage。
- Cancel 立即终止。
- Transient/Empty/Degenerate 由共享 Engine 的策略处理。

---

## 39. Deterministic 与 Transient 的边界

确定性示例：

- Auth。
- Invalid Configuration。
- Serialization。
- Idle Timeout。
- Max Tokens Truncation。
- 多数 4xx，排除 408 与 429。
- `invalid_request_error`。
- Context Length Error。

暂态示例：

- HTTP Transport。
- SSE Stream Error。
- 5xx。
- 408。
- 429。
- Empty Response。
- Doom Loop Detected。

“确定性”表示同一 Payload 立即重发不会解决，不表示故障永远无法恢复。

---

## 40. Compaction Prompt 在要求什么

详细 Prompt 把任务定义为：

> 为下一位只看到原始问题与 Summary 的 Assistant 生成可无缝续接的摘要。

它要求保留：

- 用户请求与约束。
- 技术概念。
- 文件与代码位置。
- 错误与修复。
- 已解决和正在解决的问题。
- 所有真实用户消息。
- 未完成任务。
- 当前工作现场。
- 与最近工作直接相关的下一步。

并要求：

- 先私下思考。
- 只输出一个 `<summary>...</summary>`。
- 不调用工具。
- 不把 Compaction Instruction 本身当成用户请求。

---

## 41. 用户手动 `/compact <context>` 如何进入 Prompt

User Context 被包进：

```text
<user_provided_context>
...
</user_provided_context>
```

并明确要求 Summary 突出吸收该 Context。

这允许用户告诉压缩器：

- 哪些信息绝不能丢。
- 接下来重点是什么。
- 哪些旧分支不再重要。

它影响 Summary 生成，但不是直接无校验写进 Conversation。

---

## 42. Successive Compaction 怎样防止早期信息逐代消失

Prompt 明确要求：

- 若发现以前的 Compaction Summary。
- 把它视为早期历史的权威表示。
- 把仍相关的信息继续带入新 Summary。

这是一种递归摘要：

```text
History 1 -> Summary 1
Summary 1 + New History -> Summary 2
Summary 2 + New History -> Summary 3
```

每一代仍可能有信息损失，所以 Transcript/Segments 模式提供 Out-of-band 细节恢复。

---

## 43. 为什么限制 Summary 目标长度

Prompt 要求“最多几千词”，强调紧凑而不是详尽抄写。

若 Summary 过长：

- Compaction 后仍接近阈值。
- 下一 Turn 很快再次压缩。
- 可能进入 Compaction Loop。
- Summary 自己被 Output Limit 截断。

压缩的成功标准不是“无损复述”，而是“保留继续工作所需的最小充分状态”。

---

## 44. Wall-clock Budget 防什么

Compaction Stream 在线记录：

- TTFT。
- Stream Duration。
- Delta Count。
- 最大 Inter-token Gap。
- Elapsed Wall Time。

若超过 Agent Policy 的 Wall-clock Budget：

- 判定 Runaway Generation。
- 中止压缩。

它防止 Summarizer 在长 Reasoning 或异常慢流中无限消耗时间。

---

## 45. Cancellation Gate 为什么用 Holder Count

Prefire Pass 1 与正式 Compaction 可能重叠。

`CompactCancelGate` 不是一个简单 Bool，而是：

- 一个共享 Cancellation Token。
- 一个 Holder Count。
- 每个 `enter()` 返回 Scope Guard。

第一个 Holder 创建 Token；嵌套 Holder 复用；最后一个 Scope Drop 后下一次进入才创建新 Token。

这样一次 Stop 可以同时取消重叠的 Prefire 与 Compact，而不会让后来的独立 Compaction 永久继承旧 Cancel 状态。

---

## 46. 什么是 Two-pass Compaction

单阶段：

```text
Full Conversation -> Final Summary
```

双阶段：

```text
旧 Prefix -> NOTE1
NOTE1 + Recent Tail -> Final Summary
```

目的：

- 大幅缩短最终同步请求输入。
- 让最近 Tail 保留更完整。
- 把旧 Prefix 的总结提前在后台完成。

---

## 47. Prefire 在什么时候启动

默认在 Auto Threshold 前 10 个百分点开始。

若 Threshold 是 85%，Prefire Start 约为 75%。

可用 `GROK_PREFIRE_LEAD_PERCENT` 覆盖 Lead。

触发还要求：

- 当前任务没有 Output Token Budget。
- Agent Policy 开启 Two-pass。
- 没有现成 Cache。
- 没有另一个 Prefire In Flight。

---

## 48. Prefire Pass 1 做什么

1. Snapshot 当前 Conversation。
2. 至少四个 Item 才继续。
3. 按默认约 95% Prefix / 5% Tail 切分。
4. 对 Prefix 做 Verbatim Summarization Prep。
5. 构造 Pass-1 History。
6. 调 Compaction Model。
7. 从输出提取可供 Pass 2 使用的 NOTE1。
8. 保存 Cache。

它只读取 Snapshot，不修改 Chat State。

---

## 49. Prefire Cache 保存哪些身份信息

`AsyncCompactionCache` 包含：

- `note1`。
- `prefix_len`。
- Prefix Fingerprint。
- Model Slug。
- Pass-1 Latency。

不能只缓存 Summary 文本；否则无法证明它仍对应当前 Conversation Prefix 和当前模型。

---

## 50. Prefix Fingerprint 怎样计算

Fingerprint Hash 输入包括：

- Item Count。
- 每个 Item 的 Variant Tag。
- `text_content()`。

它是 Cheap Validity Fingerprint，不是密码学哈希。

它能发现：

- Edit。
- Rewind。
- Branch 导致的 Prefix 内容变化。
- Prefix 长度变化。

若不一致，NOTE1 被视为 Stale。

---

## 51. Pass 2 怎样消费 Cache

正式 Compaction 时：

1. 若 Prefire Handle 仍存在，先 Await。
2. 取走 Cache。
3. 获取 Live Conversation。
4. 校验 Prefix Length、Model Slug 和 Fingerprint。
5. 准备当前 Tail。
6. 构造 `NOTE1 + Tail + Prompt`。
7. 生成 Final Summary。

任何校验失败都回退 Single-pass，不使用过期 NOTE1。

---

## 52. Prefire 尚未完成时为什么等待它

若已经为 Pass 1 花了大部分时间，直接丢弃并重新做 Full Single-pass 会浪费成本。

所以 Pass 2 Await In-flight Handle。

Telemetry 对等待时间的处理很细：

- 已提前完成的 Pass 1 Latency 不计入用户同步等待。
- 正式压缩时等待尚未完成 Pass 1 的时间，会加到 Final TTFT。
- Stream Duration、Delta Count、ITL 仍只统计 Pass 2。

---

## 53. Degenerate Summary 是什么

Summary 可能技术上非空，但只有几句泛化文字，无法续接真实任务。

清洗后少于 500 Characters 被判为 Degenerate。

这个阈值来自观测：

- 75–264 字符常见于退化输出。
- 健康生产摘要最小值远高于此。

Degenerate 不进入 Chat State，而是按可重试失败处理。

---

## 54. Summary 清洗为什么不是简单 Strip Tag

模型可能输出：

- 顶层 `<analysis>`。
- `<summary>` 内又嵌套 Analysis。
- Markdown `**Analysis**` Scratchpad。
- 在正文中引用 `<summary>` 控制词。
- 多余空行。

`format_compact_summary` 会：

1. 删除 Leading Analysis Scratchpad。
2. 提取外层 Summary Body。
3. 保留合法编号章节。
4. 中和正文中的控制 Tag。
5. 压缩三重空行。
6. Trim。

---

## 55. 为什么要中和 Control Token

正文可能讨论：

```text
<summary>
<analysis>
<summary_request>
```

若原样写进下一轮，模型可能把引用误读成新的结构指令。

清洗器在 `<` 后插入 Zero-width Space，使它视觉可读但不再是活 Tag。

这是 Prompt Injection Surface Reduction，而不只是格式美化。

---

## 56. Continuation Preamble 的作用

清洗后的 Summary 被包装成：

```text
This session is being continued from a previous conversation...

Summary:
...
```

它告诉下一轮模型：

- 这不是用户的新问题。
- 它是被丢弃历史的代理表示。
- 应继续工作，而不是回答“谢谢你的摘要”。

该 Item 使用 Synthetic/User Metadata 语义进入 Conversation。

---

## 57. Compaction 前还会 Snapshot 哪些运行态

`CompactionStateContext` 保存：

- Working Directory Generation。
- Destination Project Instructions。
- Last Real User Query。
- Recent Messages 视图。
- Agent Edited Paths。
- Running Background Tasks。
- Running Subagents。
- Connected MCP Servers。
- Todos。

这些事实未必完整存在于可总结 Conversation 中，却直接决定下一步怎么继续。

---

## 58. 为什么强调 Last Real User Query

Conversation 中的 User Variant 不一定来自真人。

Synthetic User Item 可能是：

- System Reminder。
- Auto-continue Prompt。
- Metadata Bootstrap。
- Tool/Gate 注入。

若简单取最后一个 User：

- Summary 后可能把 Reminder 当成用户目标。
- Assistant/Tool Result Tail 可能被错误切断。

所以 Helpers 使用 `is_real_user_turn` 识别真实边界。

---

## 59. Recent Messages 为什么避免孤立 Tool Result

从最后真实用户问题之后提取 Tail 时：

- 保留 Assistant。
- Tool Result 内容替换成省空间占位。
- 忽略 Synthetic User 注入。

若 Synthetic User 被当作边界，可能只保留 Tool Result 而丢掉它前面的 Assistant Tool Call，导致 Provider 400。

真实用户边界不仅是产品语义，也是协议完整性边界。

---

## 60. grok-build 的 Compaction View 为什么丢掉 Recent Messages

`CompactionStateContext::for_compaction` 会把 `recent_messages` 设为空，但保留其他运行态。

源码注释解释：对于只有一个真实 User Turn 的 Subagent，Recent Messages 可能就是整个工作 Transcript；再把它原样塞回会几乎不释放空间，并让模型重复读同一批文件。

因此当前 grok-build 重建路径更依赖：

- Summary。
- Last Real User Query。
- State Reminder。

而不是保留完整 Recent Tail。

---

## 61. AGENTS.md 怎样跨 Compaction 保留

新历史会重新注入 Project Instructions：

- CWD Generation 为零时使用原 AGENTS Reminder。
- Working Directory 已切换时使用 Destination Project Instructions。

这避免 Summary 用自己的话改写强约束。

项目指令应尽量 Verbatim Reinject，而不是依赖模型摘要准确复述。

---

## 62. Skill State 怎样跨 Compaction 恢复

Compaction 时会：

- 重新解析可用 Slash Skills。
- 构造 System Reminder。
- 成功替换后调用 Tool Bridge 的 Skill Discovery Compaction Hook。
- 持久化 Announcement State。

摘要可以记住“用了哪个 Skill”，但 Skill Catalog/Discovery 本身要由运行态重新建立。

---

## 63. MCP State 怎样进入 Reminder

系统收集已连接 MCP Server：

- Name。
- Tool Count。
- 清洗、截断后的 Description。

若存在 MCP Server，还会动态解析当前 Agent 中：

- Search Tool 的模型可见名字。
- Use Tool 的模型可见名字。

解析失败则省略该提醒，不把 `${{ tools.by_kind... }}` 未渲染模板泄露给模型。

---

## 64. Running Subagent 怎样保留

通过 Subagent Event Channel 请求当前 Active List，转换成：

- Subagent ID。
- Type。
- Description。
- Elapsed Time。

同时解析 Poll 与 Cancel Tool 的模型可见名字。

压缩后模型知道：

- 哪些委派仍在运行。
- 应用什么工具继续观察或取消。

而不是把它们误判为已丢失任务。

---

## 65. Background Task 怎样保留

Tool Bridge 返回尚未完成的后台任务。

Context Snapshot 保存：

- Task ID。
- Command。
- Status。
- 对应 Execute/Monitor Tool Name。

Compaction 不能只保留命令文本；没有 Task ID 与 Monitor Tool，下一位模型无法接管生命周期。

---

## 66. Todo 怎样跨 Compaction

Todo Resource 被转换为轻量 Summary：

- ID。
- Content。
- Pending/InProgress/Completed/Cancelled。

新 System Reminder 可恢复当前行动面。

同时，持久化层的 PlanState 会重置默认值，Tool/Reminder 机制负责重新收敛，而不是让旧 Plan State 与新 Conversation 错位。

---

## 67. Plan Mode 怎样跨 Compaction

若 Plan Mode Active：

1. 读取 Plan File Path。
2. 检查 Plan File 是否有内容。
3. 渲染完整 Plan Reminder Template。
4. 插入现有 `<system-reminder>` Closing Tag 之前；若没有现有 Reminder，则创建 Wrapper。

成功后：

- `reset_after_compaction()`。
- 持久化 Plan Mode State。

计划文件本身是外部权威载体，Summary 只是上下文入口。

---

## 68. Memory 怎样帮助恢复被摘要丢失的事实

构造 System Reminder 时可提供 Memory Backend，Search Source 标为 `compaction_recovery`。

若 Reminder 构造期间发生 Memory Search：

- 增加 `compaction_recovery_count`。
- 写 Memory Telemetry。

压缩后 `memory.context_injected = false`，下一 Turn 会重新检查是否需要注入，而不是假设旧注入仍存在于新历史。

---

## 69. 三种 Compaction Mode

### 69.1 Summary

只保留 Summary，不提供旧历史指针。默认模式。

### 69.2 Transcript

Summary 中附上原始 `updates.jsonl` 路径。

模型需要精确错误、代码或 Tool Output 时可按需读取。

### 69.3 Segments

把历史整理成 `compaction/segment_*.md` 与 Index。

Summary 附上 Segment Store 指针，并告诉模型只读恢复细节。

---

## 70. 为什么 Transcript/Segments 是 Out-of-band Memory

把完整旧历史继续放在 Prompt 中就失去压缩意义。

Out-of-band 模式变成：

```text
默认只带 Summary
需要精确细节时再 Read/Grep 外部 Artifact
```

这类似操作系统：

- Context Window 是内存。
- Summary 是 Working Set。
- Transcript/Segments 是可按需换入的磁盘页。

---

## 71. `build_compacted_history` 的真实顺序

默认 grok-build 路径组装：

```text
1. 原 System Message
2. User Metadata Prefix（用户信息/项目布局）
3. Project Instructions（若有）
4. 最后真实 User Query（若有）
5. Recent Messages（当前视图通常为空）
6. Continuation Summary Item
7. System State Reminder（若有）
```

这个顺序很重要：

- System 仍在最前。
- 原用户目标在 Summary 前重新建立。
- Summary 解释此前工作。
- 最后的 Reminder 提醒当前运行态和下一步工具。

---

## 72. 为什么 Summary 不是 System Message

Summary 被构造成 Synthetic User Metadata Item。

这样：

- 不与原 System Prompt 抢最高指令层级。
- 明确它是 Conversation Continuation Context。
- Provider 的 Role Schema 更容易保持兼容。

Summary 中的旧指令也不会天然获得 System 权限。

---

## 73. Sanitization 解决什么 Tool Pair 问题

新历史必须满足：

```text
每个 ToolResult
必须有一个此前出现的 Assistant.tool_calls[].id 与之匹配
```

`sanitize_compacted_history` 左到右扫描：

- 遇到 Assistant，记录 Tool Call IDs。
- 遇到 Tool Result，若 ID 尚未出现则删除。

它能删除：

- 完全孤立的 Result。
- 出现在 Owner 之前的 Result。

---

## 74. Sanitizer 的明确非目标

它不会删除“有 Tool Call 但暂时没有 Tool Result”的 Assistant。

这种状态可能是：

- 正在执行。
- Cancel 后等待修复。
- 合法的 In-flight Trace。

Compaction Sanitizer只修复会立即导致 Strict Provider 失败的 Orphan Result 方向。

---

## 75. 为什么 Sanitization 后还要 Validate

代码先清洗，再用独立 Read-only Validator 检查。

若仍有 Violation：

- 记录 Error。
- 回退到 Minimal Compacted History。
- 不带可能造成 Tool Pair 问题的 Recent Messages。

这是一种 Defense in Depth：转换函数和不变量检查函数分开。

---

## 76. Summary Commit 前的持久化顺序

在替换 Chat State 前：

1. 再次检查 Cancellation。
2. 持久化 Compaction Segment。
3. 记录 Compaction Prompt Index。
4. 持久化 Compaction Checkpoint。
5. 处理 Forked Prefix。
6. 调用 `replace_conversation_for_compaction`。

Checkpoint 在 Replace 前准备，使 Restore/Rewind 能理解这次结构性历史变化。

---

## 77. Forked Session 为什么可能保留 Inherited Prefix

Fork 启动时可能固定一段从 Parent 继承的 Prefix。

压缩 Child Suffix 后，系统尝试：

- 保留 Inherited Prefix。
- 丢掉新 Compacted History 的重复 System。
- 若 Prefix 已含 AGENTS.md，再丢掉重复注入的 AGENTS Item。

这样 Child 的继承语义仍可见。

---

## 78. 为什么又可能释放 Inherited Prefix

若重新 Pin Prefix 后，Projected Token Reseed 仍达到 Auto Threshold：

- 释放 Prefix。
- 使用对完整历史生成的 Self-contained Summary。
- 设置 `prefix_released` Sticky Flag。

否则每次 Compaction 都重新附上巨大 Parent Transcript，会无限重复触发。

---

## 79. Prefix Reseed Projection 为什么只是下界

Projection 使用：

```text
preserved_estimate
* tokens_before
/ current_full_conversation_estimate
```

真实 Replace Reseed 使用冻结的 `estimate_at_last_response`。

当前 Conversation 只会增长或变化，所以 Projection 可能偏低、倾向保留。

源码用 Replace 后真实 `exceeds_threshold` 再检查兜底；若仍超阈值，设置 Sticky Size Suppression，避免 Re-loop。

---

## 80. Chat State Replace 为什么必须经过 Actor

`replace_conversation_for_compaction` 最终进入 Chat State Actor 的 `replace_conversation(items, true)`。

Actor 内串行完成：

- Turn Capture Snapshot/Rebase。
- Persistence Replace History。
- Token Reseed。
- Conversation Swap。
- Delta Reset。
- Event Notification。

如果 Session 直接改一个共享 Vec，会与 Tool Result Push、User Message、Rewind 和 Persistence 产生 Lost Update。

---

## 81. Compaction Reseed 为什么不能只用新历史 Bytes/4

Provider Token Count 与本地 Estimate 之间可能有巨大差异，来源包括：

- Tool Schema。
- Hidden Protocol Overhead。
- Tokenizer 差异。
- Image/Reasoning Cost。
- Backend 包装。

若压缩后直接：

```text
new_total = estimate(new_history)
```

上下文占用会突然虚假下降，之后太晚才再次触发。

---

## 82. Reseed 的比例公式

当有有效 Provider 基线时：

```text
ratio = pre_replace_total / estimate_at_last_response
new_total = round(base_estimate_of_compacted_history * ratio)
new_total = min(new_total, pre_replace_total)
```

这个 Ratio 把 Provider 与本地 Estimator 的差异按新历史大小缩放。

### 82.1 为什么不是加法 Carry

错误模型：

```text
new_total = new_estimate
          + (provider_total - old_local_estimate)
```

若旧历史很大、新 Summary 很小，固定加上旧 Overhead 会让压缩后仍看起来接近满窗。

比例模型让 Overhead 随保留内容规模缩小。

---

## 83. Reseed 为什么 Cap 在旧 Total

Compaction 的目的就是减少上下文。

Estimator Ratio 或 Round Error 不应让压缩后的 Usage 比压缩前更高。

因此：

```text
new_total <= pre_replace_total
```

是明确不变量。

---

## 84. 没有 Provider Count 时怎样 Reseed

Fresh Session 或旧 Snapshot 可能没有有效基线。

此时回退：

```text
new_total = base_estimate(new_history)
```

Legacy Snapshot 中若 `estimate_at_last_response == 0`，Restore 会重算合理基线，避免除零和极端 Ratio。

---

## 85. Replace 后还重置哪些状态

成功 Compaction 后：

- `estimated_tokens_since_model = 0`。
- `estimate_at_last_response` 设为新 Conversation 的本地 Estimate。
- 发送 `ConversationReset`。
- 发送 `TokensUpdated`。
- 更新 Idle Flush Conversation Length。
- Memory Injection 标志清零。
- Plan State 重置。
- AGENTS/Skill Discovery 收到 Compaction 回调。
- Announcement State 持久化。
- Plan Mode 重置/持久化。
- 触发 PostCompact Hook。

它是整个 Session Runtime 的状态迁移，不只是 Chat Vec Swap。

---

## 86. PreCompact 与 PostCompact Hook 的边界

PreCompact 在实际生成摘要前 Dispatch，Payload 带 Source：Manual 或 Auto。

PostCompact 只在新历史成功替换后 Dispatch。

因此 Hook Consumer 可以区分：

- 压缩开始了。
- 压缩真正提交了。

失败/Cancel 不应伪装成 PostCompact Success。

---

## 87. Auto Suppression 为什么必需

假设 Conversation 已经太大，Lossy Compaction 仍超窗。

若下一 Turn 又无条件检查：

```text
达到阈值 -> 压缩失败 -> 继续
下一 Turn -> 达到阈值 -> 同样失败
```

系统会反复花费请求、延迟和通知。

Suppression State 记录“当前失败在什么条件变化前不值得重试”。

---

## 88. 五种 Suppression State

| 状态 | 原因 | 清除条件 |
| --- | --- | --- |
| `SUPPRESS_NONE` | 无抑制 | 正常允许 Auto Compact |
| `SUPPRESS_TURN` | Other，可乐观恢复 | 下一 Turn 开始 |
| `SUPPRESS_STICKY` | Size/Schema | Context Budget 变化、Rewind、成功压缩或可修复 Model Switch |
| `SUPPRESS_UNTIL_SUCCESS` | Credit Block | 任意模型请求成功 200 |
| `SUPPRESS_AUTH` | Auth | Login/Token Refresh |

Manual `/compact` 不受这些 Auto Gate 限制。

---

## 89. 为什么 Credit 等成功 200 才清除

客户端无法可靠观察“用户刚充值成功”。

Token Refresh 也不能证明余额恢复。

只有模型请求成功，才能证明 Credit Block Incident 已结束。

---

## 90. 为什么 Auth 不能等成功 200 才清除

如果 Conversation 已经超窗：

- Auth Suppression 阻止 Compaction。
- 超窗又阻止普通模型请求成功。
- 若必须等 200 才清 Auth Suppression，就形成死锁。

所以 Auth Suppression 在 Login/Token Refresh 时清除，让 Compaction 先重新尝试。

---

## 91. 为什么 Size/Schema 是 Sticky

同一输入、同一窗口、同一 Schema 立即重试不会改变结果。

它们只在以下变化后值得再试：

- Context Window 变大。
- Rewind 让 Conversation 变短。
- Model Switch 改变协议/窗口。
- 用户手动调整配置或执行成功压缩。

---

## 92. Error Text 在哪里仍被使用

Overflow Recovery Trigger 使用结构化 Metadata。

但 Compaction 自身的确定性失败分类仍会把 Error Text 映射成 Suppress Reason：

- Spending/Credit 关键词。
- Context Length 关键词。
- 401/Unauthorized。
- `invalid_request_error`。
- 其他。

这里文本分类用于产品恢复范围，不用于判定原模型请求是否超窗。

---

## 93. Auth Compaction Failure 为什么直接中止 Turn

Pre-sampling 时若需要 Compaction，但压缩请求本身 401：

- 不能继续发送原本已接近/超过窗口的普通请求。
- 也不能默默忽略 Compaction 失败。

`surface_compact_auth_failure` 会：

- 发送 `RetryState::Failed`，Error Type 为 Auth。
- 返回 ACP `auth_required`。
- 提示 `/login`。

认证恢复后才重新进入压缩路径。

---

## 94. Cancel 为什么不设置 Auto Suppression

用户 Cancel 不是 Compaction 输入或系统状态的确定性故障。

下次用户主动继续时，压缩仍可能成功。

所以 Cancel：

- 发 Cancelled Notification。
- 返回 Cancel Error。
- 不把 Auto Compact 锁死。

---

## 95. Compaction Request Artifact 保存什么

每次 Single-pass/Full-replace 请求会 Best-effort 持久化：

- Schema Version。
- Request ID。
- Start Time。
- Trigger 类型。
- Prompt Variant。
- Model。
- User Context。
- 精确 Chat History。
- Tools。
- Summary 或 Final Error。
- Attempts。
- 每次 Attempt Detail。

路径位于 Session 的 `compaction_requests/`，随后跟随现有 Session Archive Upload。

---

## 96. 为什么记录被拒绝的 Summary

Degenerate Attempt 可能输出：

- Thinking Trace。
- 泛化一句话。
- 幻觉 Tool Invocation。
- 被截断的开头。

若只记录“重试成功”，无法改进 Prompt 或分类器。

Artifact 会保存有界的 Reject Output：最多 8192 Characters，超出时保留 Head 与 Tail，中间写 Elision Marker。

---

## 97. Telemetry 观测哪些 Compaction 质量

包括：

- Tokens Before/After。
- Trigger Percentage/Threshold。
- Summary Characters。
- Attempt Count。
- Degenerate Rejections。
- Input Overflow Rejections。
- Deterministic/Transient Rejections。
- Stop Reason。
- Outcome：Success/Truncated/Deterministic/Transient/Degenerate/Failed。
- TTFT、Stream Time、Delta Count、ITL Max。
- Two-pass/Prefire Hit、Wait、Stale、Pass Latency。
- Fork Prefix Release。

这让“压缩成功”不再是唯一指标，还能衡量质量、等待与浪费。

---

## 98. 一条普通 Pre-sampling Auto Compact 路径

1. 上次 Provider 报告 70k Tokens。
2. Tool/User/Reminder 新增估算 16k。
3. `estimated_total = 86k`。
4. Context Window 100k，Threshold 85%。
5. Pre-sampling Check 触发。
6. 发 AutoCompactStarted。
7. Snapshot Conversation 与 Runtime Context。
8. Compaction Model 生成 Summary。
9. 清洗并校验 Summary History。
10. 持久化 Checkpoint。
11. Chat State Actor Replace。
12. 比例 Reseed 得到例如 18k。
13. 发 ConversationReset、TokensUpdated 和 AutoCompactCompleted。
14. Turn Loop 从新历史构造模型请求。

---

## 99. 一条 Tool Result Preflight 路径

1. 模型在 80k 上下文请求 Tool。
2. Tool 返回约 30k Tokens。
3. Chat State 估算 110k。
4. Tool Loop 结束前 `check_preflight_overflow` 发现超过 100k。
5. 不发送必然超窗的下一 Request。
6. `run_compact_only`。
7. 成功后 `continue` Agentic Loop。
8. 使用压缩历史重新 Build Request。

---

## 100. 一条 Verbatim Input Overflow 路径

1. Verbatim Compaction Request 本身超过 Context Window。
2. Typed Error 标记 `context_overflow`。
3. Input Stage 从 Verbatim 降为 VerbatimFitted。
4. 为 Summary Reserve 32,768 Tokens，并扣 Tool Schema。
5. 从最新 Whole Turns 向前 Fit。
6. 再采样。
7. 若仍超窗，降为 Lossy。
8. Strip Tool Result/Reasoning/Image，按 70% Budget Fit。
9. 再采样。
10. 若仍失败，标记 Size Suppression，不继续死循环。

---

## 101. 一条 Two-pass Prefire Hit 路径

1. Session 到 75%，尚未达到 85% Auto Threshold。
2. 后台 Snapshot 95% Prefix。
3. Pass 1 生成 NOTE1 并 Cache。
4. Session 继续正常工作。
5. 到 85%，正式 Compaction 开始。
6. 校验 Live Prefix Fingerprint 未变，Model 未变。
7. Pass 2 只看 NOTE1 与 Recent Tail。
8. 生成 Final Summary。
9. 用户同步等待只承担 Pass 2；Pass 1 已在后台隐藏。

---

## 102. 一条 Prefire Stale 路径

1. Pass 1 Snapshot 旧 Prefix。
2. 用户 Rewind、Edit 或 Branch 改变 Prefix。
3. 正式 Compaction 取 Cache。
4. Fingerprint 不一致。
5. 标记 `compaction_prefire_stale`。
6. 丢弃 NOTE1。
7. 回退 Single-pass。

系统宁可多花一次压缩请求，也不把错误分支的摘要写入当前历史。

---

## 103. 一条 Sampling Overflow Recovery 路径

1. Precheck 因 Estimate 偏低没有触发。
2. Provider 返回 Error，并在 Metadata 给出真实 Context Window。
3. Session 比较当前 Estimate 与该 Window。
4. 若 Estimate 已超过，更新无 Override 的 Sampling Config Window。
5. 运行 Compact Only。
6. 返回 `CompactAndResubmit`。
7. 外层 Turn Loop 从压缩后 Chat State 重建 Request。

这条路径是 Precheck 的安全网。

---

## 104. 一条确定性 Credit Failure 路径

1. Auto Compact 发起 Summary Request。
2. Provider 返回 Out-of-credits。
3. Error 分类为 Deterministic/CreditBlock。
4. `auto_compact_suppressed` 从 None 变成 Until-success。
5. 只在首次 Transition 时发 Telemetry 与 AutoCompactFailed。
6. 后续 Turn 不再重复压缩。
7. 某次普通模型请求成功 200 后清除 Suppression。

---

## 105. 一条 Fork Prefix Release 路径

1. Child Session 继承巨大 Parent Prefix。
2. Child Suffix 被压缩成 Summary。
3. 尝试重新拼回 Parent Prefix。
4. Projected Reseed 仍达到阈值。
5. 放弃 Pin Prefix，改用 Self-contained Summary。
6. `prefix_released = true`。
7. 后续 Compaction 不再重复尝试 Pin。

---

## 106. 测试固定的 Token 不变量

### `compaction_reseed_carries_provider_overhead`

新 Total 必须高于纯 Bytes/4 Estimate，但不能超过压缩前 Provider Total。

### `compaction_reseed_scales_overhead_down_with_deleted_content`

大量删除旧历史后，Overhead 按 Ratio 缩小，不能固定相加。

### `compaction_reseed_excludes_post_response_deltas_from_overhead`

响应后巨大 Tool Result 不得污染 Provider/Estimate Ratio。

### `compaction_overhead_unaffected_by_pruning_after_last_response`

响应后 Pruning 不得改写冻结基线。

### Restore Tests

Snapshot 应保留 `estimate_at_last_response`；旧 Snapshot 无该字段时安全回退重算。

---

## 107. 测试固定的 Prefire 不变量

- 相同 Prefix Fingerprint 稳定。
- 内容变化会改变 Fingerprint。
- Length 变化会改变 Fingerprint。
- 默认 Lead 为 10 个百分点。
- In-flight Pass 1 可被 Pass 2 Await，完成后 Cache 可见。
- Handle/Cache 都是 Take-once。
- 没有 Prefire 时自然回退 Single-pass。

---

## 108. 测试固定的 History 不变量

- Tool Result 必须有此前的 Owner Tool Call。
- Sanitizer 删除 Orphan/Displaced Result。
- Summary 清洗删除 Scratchpad。
- 控制 Tag 被中和。
- Degenerate Summary 被拒绝。
- Budget Fit 不从 Tool Result 中间开始。
- 最新 Tail 即使过大也尽量截断保留，而不是全部丢弃。
- Project Instructions 和最后真实 User Query 按固定位置重建。

---

## 109. 代码阅读时最容易犯的错误

### 错误一：`total_tokens` 永远是当前精确值

它是最近 Provider 锚点；响应后新增内容通过 Estimate Delta 补齐。

### 错误二：达到 85% 是因为 UI 百分比四舍五入

Trigger 用整数交叉乘法，UI 百分比只是展示。

### 错误三：Compaction 就是保留最后 N 条消息

主路径是模型 Summary + Runtime State Rebuild + Actor Replace。

### 错误四：Summary 是新 System Prompt

它是 Synthetic User Metadata；原 System Prompt 仍保留最高层级。

### 错误五：Compaction 成功后 Token 直接等于 Bytes/4

会按冻结 Provider Ratio Reseed。

### 错误六：所有压缩失败都下次继续重试

确定性失败进入不同生命周期的 Suppression。

### 错误七：Prefire Cache 有值就能用

还必须校验 Prefix Length、Fingerprint 和 Model Slug。

---

## 110. 如果修改 Token Accounting，应检查什么

- 新 Conversation Item 是否需要计入 Delta？
- Provider Usage 是否已经包含该 Item？
- `record_token_usage` 是否在 Commit 后调用？
- Estimate Baseline 是否与 Provider Total 对应同一快照？
- Rewind/Restore/Prune 是否保持冻结 Baseline？
- Compaction Reseed 是否永不超过旧 Total？
- 图片与 Encrypted Reasoning 是否被合理估算？

---

## 111. 如果修改 Compaction 输入，应检查什么

- 是否破坏 Signed Reasoning 与修改后 Text 的一致性？
- Tool Call/Result 是否成对？
- 图片是否会把请求撑爆？
- Tool Schema Token 是否从 Budget 扣除？
- Summary Output 是否有 Reserve？
- 最新 Tail 是否仍可恢复？
- Context Overflow 是否只向更小 Stage 降级？
- Artifact 是否记录最终实际发送的 History？

---

## 112. 如果修改新历史结构，应检查什么

- System Message 是否仍第一项？
- 用户信息前缀是否保留？
- AGENTS.md 是否 Verbatim Reinject？
- 最后真实用户问题是否误选 Synthetic Item？
- Summary Role 是否保持 Metadata 语义？
- Runtime Reminder 是否包含 Todo/Task/Subagent/MCP？
- Tool Result Validator 是否通过？
- Checkpoint 是否先于 Replace？
- Actor Persistence 与 In-memory State 是否原子收敛？

---

## 113. 如果修改 Suppression，应检查什么

- Failure 是否真的 Deterministic？
- 清除条件是否可被客户端观察？
- 是否可能形成等待 200 的超窗死锁？
- Model Switch 是否错误清掉 Credit/Auth？
- Manual `/compact` 是否仍可绕过 Auto Gate？
- 同一 Transition 是否只发一次通知？
- 成功、Rewind、Login 与 Turn Start 各清哪一种状态？

---

## 114. 推荐调试顺序

遇到“明明没满却压缩”“压缩后立刻又压缩”“摘要后忘了任务”时：

1. 读取 `context_window` 与 `threshold_percent`。
2. 对比 `total_tokens`、`estimated_tokens_since_model`、`estimate_at_last_response`。
3. 确认 Trigger Source：Pre-sampling、Preflight、Error、Model Switch、Manual。
4. 检查 Suppression State。
5. 检查实际 Input Stage。
6. 查看 Compaction Request Artifact 的真实 History、Tools 和 Attempts。
7. 检查 Summary 是否 Degenerate/Truncated。
8. 查看 Sanitizer 是否删除 Orphan Tool Result。
9. 检查 `build_compacted_history` 输出顺序。
10. 对比 Replace 前后 Token Reseed Ratio。
11. 检查 Prefire Cache Fingerprint/Model 是否 Stale。
12. 检查 Runtime Reminder 是否恢复 Todo、Task、Subagent、MCP 和 Plan。

---

## 115. 推荐断点

### Token Plane

- `push_message`。
- `push_user_message`。
- `record_token_usage`。
- `GetEstimatedTotalTokens` Command Arm。
- `replace_conversation(is_compaction = true)`。

### Trigger Plane

- `check_auto_compact_needed`。
- `check_preflight_overflow`。
- `should_compact_on_error`。
- `maybe_compact_on_model_switch`。

### Sampling Plane

- `run_compact_only`。
- `run_compact_inner`。
- `sample_full_replace_summary` Adapter。
- Input Stage Transition。
- `generate_session_compact` Stream Loop。

### Rebuild Plane

- `CompactionStateContext::build`。
- `build_compacted_history`。
- `sanitize_compacted_history`。
- `validate_compacted_history`。
- `persist_compaction_checkpoint`。
- `replace_conversation_for_compaction`。

---

## 116. 本篇建立的分层模型

可以把上下文管理拆成六层：

```text
1. Measurement
   Provider count + local delta estimate

2. Trigger
   threshold / preflight / error / model switch / manual

3. Reduction
   verbatim / fitted / lossy / two-pass

4. Synthesis
   compaction prompt -> successor summary

5. Reconstruction
   system + user + project instructions + summary + runtime state

6. Commit
   validate -> checkpoint -> actor replace -> token reseed
```

Suppression 与 Cancellation 横跨这六层，防止重复失败和失控生成。

---

## 117. 与 Agent Prompt、Skill、Tool 的连接

Compaction 不是独立于 Prompt Assembly 的后台清理。

它直接决定下一次 Prompt 中还能看到什么：

- 原 System Prompt 原样保留。
- AGENTS.md 重新注入。
- Skill Discovery 在成功后刷新。
- Tool Names 动态解析后写入运行态 Reminder。
- MCP Server 状态保留，但完整 Tool Catalog 仍由下一次 Tool Loading 决定。
- Memory 可补回 Summary 丢掉的长期事实。
- Transcript/Segments 允许模型按需读取精确旧记录。

因此 Compaction 是 Prompt Assembly 的“历史重写前置层”。

---

## 118. 本篇术语表（Glossary）

| 名词 | 白话解释 | 在本篇中的精确定义 |
| --- | --- | --- |
| Actor Replace | 由状态 Actor 串行替换整份数据 | Chat State 原子替换 Conversation、持久化并重置 Token 基线 |
| AGENTS.md | 仓库中的项目级 Agent 指令 | Compaction 后尽量原样重新注入，而非依赖摘要复述 |
| Auto Compact | 达到条件后系统自动压缩 | 受 Threshold、Suppression、Memory Flush 和 Context Window 控制 |
| Baseline | 用来校准后续估算的参考点 | 最近 Provider 响应时的 Token Total 与本地 Conversation Estimate |
| Budget Fit | 把输入缩到指定 Token 预算 | 从最新内容向前保留，并保护 System 与 Tool Pair |
| Bytes/4 | 每四个 UTF-8 字节近似一个 Token | 新增 Conversation Item 的快速本地估算启发式 |
| Cache Alignment | 输入前缀与先前请求一致以复用缓存 | Verbatim Compaction 尽量不改 Prefix 结构 |
| Calibration | 用精确信号修正估算 | `record_token_usage` 用 Provider Total 重置本地增量 |
| Cancellation Token | 可被多个异步任务观察的取消信号 | Prefire 与正式 Compact 共享同一 Compaction Cancel Gate |
| Checkpoint | 结构性状态变化前后的可恢复记录 | Conversation Replace 前持久化的 Compaction 状态 |
| Compaction | 用摘要替换长历史的状态迁移 | 包括触发、采样、清洗、重建、校验、持久化和 Reseed |
| Compaction Mode | 旧细节的保存/恢复方式 | Summary、Transcript 或 Segments |
| Context Window | 模型单次请求可容纳的 Token 容量 | Sampling Config/Model Metadata 中的非零窗口 |
| Control Token | 会被模型解释成结构边界的文本标签 | `<summary>`、`<analysis>` 等，正文引用时会被中和 |
| CWD Generation | Working Directory 切换的单调版本 | 决定使用原项目指令还是目标目录项目指令 |
| Degenerate Summary | 非空但不足以续接工作的摘要 | 清洗后少于 500 Characters 的输出 |
| Delta Estimate | 最近 Provider 响应后的新增估算 | `estimated_tokens_since_model` |
| Deterministic Failure | 相同请求立即重发仍会失败 | Auth、Schema、多数 4xx、Context Overflow 等 |
| Defense in Depth | 多层独立保护同一不变量 | Sanitization 后再次 Validate，失败再回退 Minimal History |
| Full Replace | 用新历史整体替换旧历史 | Compaction 的 Commit 模式，不是原地删除若干 Item |
| Hard Clear | 永久清空很旧 Tool Result 内容 | Retained-memory 管理，与请求 Clone Pruning 不同 |
| Headroom | 给输出和估算误差预留的空间 | Fitted Reserve 32,768；Lossy 使用窗口 70% 等策略 |
| Inherited Prefix | Forked Session 从 Parent 固定继承的历史前缀 | 压缩后尝试重新 Pin，过大时可永久释放 |
| Input Ladder | 输入超窗后逐级减小的策略 | Verbatim → VerbatimFitted → Lossy |
| Input Stage | 当前压缩输入保真级别 | `InputStage` 枚举的三个状态 |
| ITL | Inter-token Latency | Compaction Stream 相邻 Delta 间延迟 |
| Last Real User Query | 最近一条真人输入的问题 | 跳过 System Reminder、Auto-continue 等 Synthetic User Item |
| Local Estimate | 不调用 Provider Tokenizer 的近似计数 | Conversation Item Estimator 与 Bytes/4 |
| Lossy | 主动舍弃部分细节以显著减小输入 | 删除 Tool Results/Reasoning、图片占位、Tool Calls 文本化 |
| Manual Compact | 用户主动发起的压缩 | 可带 Context，且不受 Auto Suppression Gate 阻止 |
| Memory Flush | 压缩前把长期事实写入可检索 Memory | 与当前 Session Summary 不同的长期保存机制 |
| Metadata Item | 用 User Role 承载但不是新真人问题的上下文 | Continuation Summary 与 User Prefix 的 Conversation 表示 |
| Model Switch Compact | 切到更小窗口模型前的主动压缩 | 仅在新窗口更小且当前达到阈值时发生 |
| NOTE1 | Two-pass 第一阶段对旧 Prefix 的摘要 | Cache 后与 Recent Tail 一起生成 Final Summary |
| Orphan Tool Result | 没有此前匹配 Tool Call 的 Tool Result | Sanitizer 会删除，避免 Strict Provider 400 |
| Out-of-band Memory | 不常驻 Prompt、需要时再读取的旧信息 | Transcript 或 Segment Store |
| Overhead Ratio | Provider Count 与本地 Estimate 的比例 | Compaction 后 Token Reseed 的校准系数 |
| PostCompact Hook | 新 Conversation 成功提交后的扩展事件 | 失败或 Cancel 不触发成功 Hook |
| PreCompact Hook | 开始生成摘要前的扩展事件 | Payload 标明 Manual/Auto Source |
| Prefire | 正式达到阈值前提前运行 Pass 1 | 默认比 Auto Threshold 早 10 个百分点 |
| Prefix Fingerprint | 判断 Cache 对应前缀是否仍未变化的轻量 Hash | 包含 Item Length、Variant 与 Text Content |
| Preflight Overflow | 发请求前已知 Estimate 超过窗口 | 常由巨大 Tool Result 触发 |
| Project Instructions | 当前代码目录对 Agent 的规则 | AGENTS.md 或目标 CWD 的指令块 |
| Prompt Injection Surface | 文本被模型误当作活指令的机会 | Summary 内 Control Tags 会被 Zero-width Space 中和 |
| Provider Total | 模型服务响应报告的 Token Usage | `record_token_usage` 写入 `total_tokens` 的锚点 |
| Pruning | 对旧内容做裁剪 | 可能仅作用于 Request Clone，也可能 Hard-clear 保留历史，需看具体路径 |
| Real User Turn | 真正由用户发起的 Turn 边界 | 不包括 Synthetic User Reminder |
| Reconstruction | 用摘要和运行态构建新历史 | `build_compacted_history` 的纯函数阶段 |
| Reseed | Conversation Replace 后重新设置 Token Total | 用 Provider/Estimate Ratio 缩放新历史成本 |
| Reserve | 不允许输入占用的预留 Token | 给 Summary Output、Tool Schema 与误差留空间 |
| Running State | Conversation 文本之外仍需继续维护的状态 | Todo、Task、Subagent、MCP、Plan、Edited Paths 等 |
| Sanitization | 删除会违反协议的历史条目 | 当前主要删除 Orphan/Displaced Tool Results |
| Segment Store | 按段保存的旧历史 Markdown | `CompactionMode::Segments` 的细节恢复载体 |
| Signed Reasoning | 与消息结构绑定的 Provider Reasoning Block | 修改 Assistant Text 后必须去除以防签名失效 |
| Snapshot | 某一时刻的不可变状态副本 | Prefire、Memory Flush 和 Compaction Context 的读取输入 |
| Sticky Suppression | 跨 Turn 保留的自动压缩抑制 | Size/Schema 直到 Context Budget 变化才清除 |
| Successor Assistant | 压缩后继续任务的下一轮模型 | Compaction Prompt 的目标读者 |
| Summary Carrier | 把清洗后的摘要放进新 Conversation 的 Item | 当前默认是 Synthetic User Metadata Item |
| Suppression | 暂停不值得重复的自动压缩 | 按 Turn、Sticky、Until-success 或 Auth 生命周期清除 |
| Synthetic User | 使用 User 形态但不是人类输入的消息 | Reminder、Auto-continue、Metadata Summary 等 |
| Tail | Conversation 最近一段内容 | Two-pass Pass 2 和 Budget Fit 优先保留区域 |
| Threshold | 自动压缩开始的窗口占用比例 | 默认 85%，使用整数 `>=` 判断 |
| Tokenizer | 把文本切分成模型 Token 的算法 | 本地触发没有加载真实模型 Tokenizer，依赖估算与 Provider 校准 |
| Transcript | 完整原始 Session 更新记录 | CompactionMode::Transcript 的按需恢复来源 |
| Transient Failure | 重试可能自行恢复的失败 | Transport、5xx、408、429、Stream Blip 等 |
| TTFT | Time To First Token | Compaction Summary 首个 Delta 的等待时间 |
| Two-pass | 先总结旧 Prefix，再结合 Tail 生成最终摘要 | Prefire Cache 可把 Pass 1 移出用户同步关键路径 |
| Verbatim | 尽量不改写原 Conversation 的输入 | 保留 Tool I/O/图片，按 Backend 需要 Strip Reasoning |
| VerbatimFitted | 保真但按预算裁掉旧 Turn 的输入 | Input Ladder 第二阶段 |
| Working Set | 当前上下文中高频继续工作所需的信息 | Summary + Runtime Reminder，而完整旧历史放 Out-of-band |
| Zero-width Space | 肉眼不明显但会打断标签语法的字符 | 用来中和 Summary 内嵌的控制 Token |

---

## 119. 最终总结

grok-build 的上下文管理不是一个孤立的“摘要按钮”，而是一套闭环：

```text
精确 Provider 锚点
  + 响应后本地增量
  -> 提前触发
  -> 多级缩减输入
  -> 生成 successor summary
  -> 恢复 Conversation 外运行态
  -> 校验协议不变量
  -> 持久化 checkpoint
  -> Actor 原子替换
  -> 按 Provider 比例重新播种 Token
```

这套设计最重要的五个不变量是：

1. **触发不能只看旧 Provider Usage**：巨大 Tool Output 必须在下一次请求前被估算。
2. **压缩请求自身必须可收敛**：Verbatim 超窗后按 Fitted、Lossy 逐级降级。
3. **Summary 不能独自承担全部状态恢复**：项目指令、Todo、Task、Subagent、MCP、Memory 与 Plan 要从运行态重新注入。
4. **新历史必须仍是合法协议历史**：Tool Pair、Reasoning Signature、Role 顺序和 System 层级都要校验。
5. **失败不能形成压缩死循环**：不同确定性原因拥有不同生命周期的 Suppression 和明确清除条件。

从 Agent 基础知识角度，可以把 Compaction 理解为：

> 当 Prompt 的历史部分太大时，Runtime 用另一次模型调用把“过去发生了什么”编译成一个更小的可执行状态，再把不可压缩的规则和实时状态重新链接进去，最终生成下一轮 Prompt 的新起点。
