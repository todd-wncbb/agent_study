# Grok Build Conversation History、Compaction 与上下文预算详解

本文分析 Grok Build 如何保存对话历史、估算 token、在请求阶段裁剪旧 Tool Result，以及在上下文接近或超过模型窗口时生成摘要并原子替换历史。

这套实现并不是简单的“消息太多就截掉前半段”。它同时解决：

- provider token 统计和本地实时估算如何结合；
- Tool Result 刚写入、尚未获得下一次 usage 时如何预测溢出；
- 压缩输入本身过大时如何逐级降级；
- 摘要完成后如何恢复 AGENTS.md、Skill、MCP、Subagent、Plan Mode 等运行状态；
- Tool Call 与 Tool Result 配对如何保持合法；
- fork session 为什么不能随意丢弃继承前缀；
- compaction 后如何继续支持 rewind、resume 和审计。

核心源码：

```text
crates/codegen/xai-chat-state/src/actor/state.rs
crates/codegen/xai-chat-state/src/actor/mutations.rs
crates/codegen/xai-chat-state/src/actor/queries.rs
crates/codegen/xai-chat-state/src/actor/request_builder.rs

crates/codegen/xai-grok-shell/src/session/compaction.rs
crates/codegen/xai-grok-shell/src/session/compaction_config.rs
crates/codegen/xai-grok-shell/src/session/helpers/session_compact.rs
crates/codegen/xai-grok-shell/src/session/helpers/compaction_context.rs
crates/codegen/xai-grok-shell/src/session/helpers/replay.rs

crates/common/xai-grok-compaction/src/
```

## 1. Conversation History 不是普通的字符串数组

ChatState 中保存的是：

```rust
Vec<ConversationItem>
```

典型元素包括：

```text
System
User
Assistant
ToolResult
```

Assistant item 还可能携带：

- 普通文本；
- reasoning；
- 一个或多个 Tool Call；
- model id 和 fingerprint；
- provider usage 对应的响应边界信息。

因此历史必须满足结构约束，而不仅是角色交替：

1. Tool Result 必须能找到对应 Tool Call；
2. 同一 Tool Call 不能出现冲突的重复结果；
3. System head 必须保留；
4. compaction 后仍需保留继续执行任务所需的隐式状态；
5. replay 时不能把合成摘要误算成真实用户 prompt。

## 2. ChatState 是历史的单一写入边界

SessionActor 不直接随意修改 `Vec<ConversationItem>`，而是通过 `ChatStateHandle` 发命令给 ChatStateActor。

常见命令包括：

```text
push_user_message
push_message
record_token_usage
build_conversation_request
replace_conversation_for_compaction
record_compaction_at
get_conversation
get_estimated_total_tokens
```

这样可以把以下动作串行化：

- 内存历史变更；
- token 状态变更；
- persistence 写入；
- ConversationReset/TokensUpdated 等事件；
- turn capture offset 的 rebase；
- Tool Call/Result integrity repair。

如果多个任务直接共享并修改向量，上述状态很容易发生偏移或竞态。

## 3. 三个 token 量必须分清

ChatStateState 中最关键的三个字段是：

```rust
pub total_tokens: u64,
pub estimated_tokens_since_model: u64,
pub estimate_at_last_response: u64,
```

### 3.1 `total_tokens`

最近一次模型响应报告的累计 token 使用量。

当 sampler 返回 usage 后：

```rust
record_token_usage(total_tokens)
```

会执行：

```text
estimated_tokens_since_model = 0
estimate_at_last_response = estimate(current conversation)
total_tokens = provider total_tokens
```

provider 统计通常比纯字符估算更可信，因为它使用实际 tokenizer 和真实请求结构。

### 3.2 `estimated_tokens_since_model`

最近一次 provider usage 之后，本地新增内容的 token 估算。

例如：

```text
模型返回 usage.total_tokens = 100,000
Tool A 输出约 3,000 token
Tool B 输出约 5,000 token
```

在下一次模型响应前：

```text
total_tokens                 = 100,000
estimated_tokens_since_model =   8,000
estimated_total              = 108,000
```

这解决了工具输出把上下文瞬间撑爆、但 provider 尚未有机会返回新 usage 的时间差。

### 3.3 `estimate_at_last_response`

记录最近一次 response 边界时，对当时 conversation 做本地估算的结果。

它的主要用途是校准：

```text
provider_total / local_estimate
```

这个比例可以在 compaction 替换历史后，修正新历史的本地估算。

## 4. 本地 token 估算的定位

`estimate_conversation_tokens()` 对消息内容做快速估算。它不是 tokenizer 的精确替代品，而是两个模型调用之间的安全预测器。

设计目标是：

- 快；
- 无需依赖远程 provider；
- 能覆盖刚加入的 User、Tool Result 和 reminder；
- 宁可较早触发安全检查，也不要等到请求被 backend 拒绝。

因此代码同时保留 provider truth 和 local delta，而没有二选一。

## 5. 消息追加时如何更新 token delta

当 User 或 Tool Result 等消息被 push 时，mutations 会估算新增内容并累加：

```text
estimated_tokens_since_model += estimated_tokens
```

Assistant push 需要特殊处理：provider 的 `usage.total_tokens` 通常已经包含该 Assistant response，因此不能在记录 usage 后再把 Assistant 内容加一次，否则会 double count。

对应测试明确覆盖了：

- push User 后 delta 增加；
- push Tool Result 后 delta 增加；
- record usage 后 delta 清零；
- Assistant push 不重复增加已经包含在 usage 中的 token。

## 6. `get_total_tokens()` 与 `get_estimated_total_tokens()`

二者语义不同：

```text
get_total_tokens()
    = 最近 provider 报告值

get_estimated_total_tokens()
    = total_tokens + estimated_tokens_since_model
```

在 UI 展示、telemetry 或需要精确响应边界时，可以使用前者。

在 pre-sampling 和 Tool Result 后 overflow 判断时，必须使用后者。

## 7. Request-time Pruning 不是 Compaction

`build_conversation_request()` 在构造请求时还有一层轻量治理：

```rust
let needs_prune = should_prune(
    self.state.total_tokens,
    self.state.sampling_config.context_window,
);
```

`should_prune()` 的门槛是：

```text
total_tokens > context_window / 2
```

它主要处理旧 Tool Result：

- 最近若干用户 turn 完整保留；
- 较旧且很大的 Tool Result 保留 head + tail；
- 非常旧的 Tool Result 替换为固定 placeholder。

软裁剪格式：

```text
<head>

[…trimmed…]

<tail>
```

硬清理格式：

```text
[Tool result omitted — too old]
```

这和 compaction 有本质差异：

| 机制 | 目标 | 是否调用模型 | 结果 |
| --- | --- | --- | --- |
| Tool Result pruning | 降低旧工具输出成本 | 否 | 保留对话骨架，缩短结果正文 |
| Image eviction | 控制序列化 body 大小 | 否 | 从 request working copy 移除旧 inline image |
| Compaction | 用摘要替换旧历史 | 是 | 原子替换 ChatState history |

## 8. Inline Image 是 byte budget，不只是 token budget

Request Builder 会估算 wire body 大小。接近约 50 MB 的请求上限时，才批量淘汰最旧的 inline images，降到 low-water mark。

选择 low-water mark 而不是“刚好低于上限”是为了：

- 一次回收足够空间；
- 避免下一轮马上再次淘汰；
- 减少反复改写 prefix 导致 KV cache miss。

因此系统同时管理：

```text
token context window
HTTP/JSON serialized body bytes
```

二者不是同一个预算。

## 9. Auto Compaction 的阈值公式

基础判断是：

```text
estimated_total > context_window * threshold_percent / 100
```

默认策略在相关 compaction policy 中通常使用 85% 作为 trigger threshold；实际值会从 Agent/config/model 重新解析，不应把 85 写死为所有运行场景的常量。

返回结构：

```rust
AutoCompactTriggerInfo {
    tokens_used,
    context_window,
    percentage,
}
```

它同时供：

- UI `AutoCompactStarted`；
- telemetry；
- `run_compact_only()`；
- failure diagnostics。

## 10. 四类 compaction 入口

### 10.1 手动 `/compact`

入口：

```rust
run_compact(user_context)
```

用户可以提供附加上下文，指导摘要重点。

手动 compact 不受 auto-compaction suppression 阻止，因为用户可能正是在主动修复自动路径失败的问题。

### 10.2 Pre-sampling Auto Compact

每次采样前调用：

```rust
check_auto_compact_needed()
```

它使用 `get_estimated_total_tokens()`，超过配置阈值时运行 `run_compact_only()`；完成后 turn loop 回到顶部，重新构造全部 request。

### 10.3 Context-error Recovery

如果 sampling backend 返回 model metadata 中的实际 `context_window`，并且：

```text
estimated_total > error.model_metadata.context_window
```

`should_compact_on_error()` 会请求压缩。

这是最后一道后端事实校正：配置中的窗口可能过时，但 provider 错误携带了更准确的窗口。

### 10.4 Tool Result 后的 Preflight Overflow

Tool 执行完、结果已写入 ChatState 后：

```rust
check_preflight_overflow()
```

它判断的是硬窗口：

```text
estimated_total > context_window
```

这里不是 85% threshold，因为其语义是“下一次请求已经预计放不下”，必须先 compact，不能再尝试一次注定失败的 sampling。

## 11. Model Switch Compaction

每个 turn 结束时，`record_turn_model()` 记录：

```text
model_slug
context_window
```

下一 turn 开始，`maybe_compact_on_model_switch()` 比较旧、新模型。

只有窗口缩小且当前历史超过新模型阈值时才主动 compact：

```text
old window <= new window  => 不需要
old window > new window   => 重新检查
```

模型切换还能清除部分 sticky suppression，因为之前的 size/schema 问题可能在新模型下消失；但 credit/auth suppression 不会因换模型自动解决。

## 12. `run_compact()` 与 `run_compact_only()`

两者最终都进入：

```rust
run_compact_inner(user_context, auto_continue, trigger)
```

差异主要是调用语义和通知：

- `run_compact()` 表示用户手动命令；
- `run_compact_only()` 表示自动压缩，外层 Agent loop 自己重建并继续；
- trigger 会进入 telemetry 和 hook payload；
- auto 路径会发送 Started/Completed/Failed/Cancelled 更新。

## 13. Compaction 前的 Memory Flush

压缩会不可逆地把大量细节变成摘要，因此代码在符合策略时启动 pre-compaction memory flush。

流程：

```text
compaction count + 1
    ↓
检查 memory_flush_enabled
    ↓
检查 token/window/周期 guard
    ↓
snapshot memory state
    ↓
spawn_local(run_memory_flush)
```

flush 在本地任务中运行，成功后记录 `last_flush_compaction`，避免同一周期重复 flush。

这里的思想是把“长期可检索记忆”和“短期上下文摘要”分开：摘要保证当前任务能继续，memory 则保存未来可能需要恢复的细节。

## 14. Compaction 采样前的输入准备

`run_compact_inner()` 首先并发读取：

```rust
tokio::join!(
    get_conversation_len(),
    get_system_message(),
    get_conversation(),
)
```

随后检查：

- conversation 非空；
- System message 存在；
- simplified conversation 非空；
- simplified conversation 中仍存在 System item。

如果这些前置条件失败，说明 ChatState 或历史结构已经异常，不能继续生成一个看似成功但不可用的摘要。

## 15. Verbatim 与 Lossy Summarization Input

配置 `verbatim_input` 决定第一次尝试是否尽量保留原始对话：

```text
Verbatim
VerbatimFitted
Lossy
```

### 15.1 Verbatim

尽量保留原消息内容，只做必要的 backend 兼容处理，例如某些 Messages backend 的 reasoning stripping。

### 15.2 VerbatimFitted

如果 verbatim 输入本身超出 compaction model 的窗口，则按预算拟合，但仍尽量维持原始表达。

### 15.3 Lossy

使用 `prepare_conversation_for_summarization()` 生成更紧凑的摘要输入，允许牺牲非关键细节以确保摘要请求本身可提交。

这是一个输入降级梯，而不是无限重试同一份超大 payload。

## 16. 为什么压缩请求也包含 Tool Definitions

compaction sampling 会准备有效 Tool Definitions，并估算：

```rust
compaction_tool_tokens = estimate_tool_definitions_tokens(...)
```

然后从摘要输入预算中扣除工具成本。

这点容易忽略：context window 消耗不仅来自 conversation，还包括工具 schema、hosted tools、system prompt 和预留输出空间。

代码还会在 backend search 已激活时过滤重复的 `web_search` 工具，避免同一能力以两个入口进入 compaction request。

## 17. Summary Output Reserve

源码为摘要输出预留：

```rust
const SUMMARY_BUDGET_RESERVE_TOKENS: u64 = 32_768;
```

输入不能占满整个 context window。否则模型即便读得下，也没有空间输出能承载状态的摘要。

预算可以抽象为：

```text
available_input
  = context_window
  - summary_output_reserve
  - tool_definition_tokens
  - 其他固定 prompt 成本
```

## 18. Full-replace Summary Sampling

核心由公共 crate `xai-grok-compaction` 提供 full-replace 算法，shell 侧通过：

```text
ShellCompactionSampler
ShellFullReplaceObserver
FullReplaceConfig
sample_full_replace_summary()
```

连接具体 sampler、通知和 telemetry。

可能结果包括：

- 成功摘要；
- NothingToCompact；
- EmptyResponse；
- Sampler error；
- cancel；
- context overflow；
- deterministic failure；
- transient failure。

## 19. Retry 不是单一计数器

源码外层设置常规 retry 参数：

```text
max_retries = 3
retry_delay = 3 seconds
```

但不同失败并非同样处理：

- transient failure 可以等待后重试；
- empty/degenerate summary 会记录 rejection 并重新采样；
- input overflow 会切换到下一输入 stage；
- deterministic size/schema failure 可能进入 suppression；
- auth failure 需要转入 re-auth，而不能继续采样 oversized request；
- cancellation 立即结束并发出 Cancelled 通知。

因此“尝试次数”和“输入阶段”是两个正交状态。

## 20. Degenerate Summary 防护

`is_degenerate_summary()` 用来拒绝明显不可用的输出。

典型问题包括：

- 空响应；
- 极短、没有实际状态的信息；
- 模型拒绝总结而只返回元话语；
- 不能支持任务继续执行的退化内容。

被拒绝的摘要不会直接替换 history，而会进入 retry telemetry：

```text
attempts
degenerate_rejections
input_overflow_rejections
deterministic_rejections
transient_rejections
last_rejected_summary
```

## 21. Two-pass Compaction

策略启用后，长历史会拆成 prefix 和 tail：

```text
Pass 1: prefix → NOTE₁
Pass 2: NOTE₁ + prepared tail + compact prompt → final summary
```

目的不是简单生成两次摘要，而是给较早历史一个独立的信息提取机会，避免长尾近期内容压制早期关键约束。

## 22. Prefire：提前生成 Pass 1

默认 prefire lead 是阈值前 10 个百分点：

```text
auto compact threshold = 85%
prefire lead           = 10%
prefire eligible       ≈ 75%
```

到达 prefire 区域时，后台 `spawn_local` 运行 pass 1，把结果缓存为：

```text
note1
prefix_len
fingerprint
model_slug
pass1_latency_ms
```

正式 compaction 时校验：

- prefix 长度仍匹配；
- prefix fingerprint 未变化；
- model slug 相同。

缓存有效则只同步等待 pass 2，从而降低用户可见 compact latency。

## 23. Prefire 的并发安全

`PrefireState` 同时持有：

- cache；
- in-flight 状态；
- task handle。

正式 compact 遇到仍在运行的 pass 1 时，会等待其 handle，再读取 cache；不会因为“此刻缓存尚未写入”就误判 miss 并重复生成 pass 1。

如果 prefix 在等待期间发生变化，fingerprint 校验仍会将其标为 stale。

## 24. 摘要不是最终历史

模型输出 `generate_session_compact` 后，代码不会直接执行：

```text
conversation = [System, User(summary)]
```

而是先重建运行状态。

收集内容包括：

- user message prefix；
- 已发现并提醒过的 AGENTS.md；
- 当前可解析 Skills；
- agent edited paths；
- pending background tasks；
- running subagents；
- connected MCP servers；
- todo/goal/workflow 等 state context；
- memory recovery；
- Plan Mode 文件和提醒；
- transcript hint。

这一步是 Agent compaction 与普通聊天摘要之间最大的区别。

## 25. CompactionStateContext

`CompactionStateContext::build()` 从完整 conversation 和运行态输入中提取可恢复状态，再通过：

```rust
state_context.for_compaction()
```

生成适合写入 compacted history 的视图。

其作用是让下一次模型采样知道：

- 当前任务进行到了哪里；
- 哪些文件已被 agent 修改；
- 哪些 Tool/MCP 能力曾被使用；
- 是否有后台任务仍未结束；
- 是否处于 Plan Mode；
- 哪些外部规则仍然有效。

## 26. Skill、MCP 与 Subagent 提醒如何恢复

系统会从 Tool Bridge 动态渲染实际工具名，而不是硬编码：

```text
${{ tools.by_kind.search_tool }}
${{ tools.by_kind.use_tool }}
${{ tools.by_kind.execute }}
${{ tools.by_kind.monitor }}
```

这样 AgentDefinition 改名或替换工具后，compaction reminder 仍引用当前真正可调用的名称。

只有确实存在相关状态时才生成提醒：

- 无 connected MCP server，不插 MCP reminder；
- 无 pending task，不插 task monitor reminder；
- 无 running subagent，不插 subagent continuation 信息。

## 27. Memory Recovery 在 Compaction 中的作用

构造 system reminder 时可以提供 MemoryBackend。

如果状态构建发现摘要中缺失、但继续任务可能需要的信息，可执行 memory search。搜索来源标记为：

```text
compaction_recovery
```

并累计 `compaction_recovery_count`，便于衡量压缩后需要多少额外记忆恢复。

## 28. `build_compacted_history()`

最终重建大致包含：

```text
System message
User prefix / environment identity
AGENTS.md reminder
Compaction summary
State recovery reminder
Transcript hint
必要的 recent/state messages
```

具体顺序受 `summary_before_recent` 和 compaction mode 影响。

摘要不是取代 system policy，而是在保留 system head 的基础上取代大段旧交互。

## 29. Tool Pair Integrity 修复

构造结果后首先运行：

```rust
sanitize_compacted_history(raw_compacted)
```

它会移除找不到对应 Tool Call 的孤立 Tool Results，并记录被剥离的 call ids。

随后执行：

```rust
validate_compacted_history(&compacted_history)
```

若仍有 violation，不冒险写入，而是回退到不包含 recent messages 的 minimal compacted history。

这形成两层保护：

```text
repair/sanitize
    ↓ still invalid
minimal rebuild
```

## 30. Cancellation 的 Commit Boundary

摘要生成和状态重建可能耗时。代码在真正持久化并替换历史前再次检查：

```rust
if cancel.is_cancelled() { ... }
```

取消发生在 commit boundary 之前：

- 原 conversation 保持不变；
- 不写 compaction checkpoint；
- auto path 发出 AutoCompactCancelled。

这比在生成中途逐字段改写 history 更容易维持一致性。

## 31. Compaction 的提交顺序

关键顺序是：

```text
1. persist optional segment
2. record compaction prompt index
3. persist compaction checkpoint
4. resolve fork inherited prefix
5. replace_conversation_for_compaction
6. recompute/calibrate tokens
7. emit reset/completed events
```

checkpoint 必须保存替换后的历史和 compaction 边界，否则崩溃恢复或 rewind 无法判断摘要覆盖了哪些真实 prompts。

## 32. Compaction Checkpoint 保存什么

checkpoint 至少关联：

```text
prompt_index_at_compaction
compacted_history
auto_continue metadata
original_user_info
checkpoint id/path
```

`original_user_info` 特别重要，因为 compacted history 中的合成 User summary 不能替代最初用于恢复身份/环境的 User info。

## 33. 为什么记录 `prompt_index_at_compaction`

prompt index 是 replay 的逻辑边界。

如果 rewind target：

```text
target < compaction boundary
```

就不能以 compacted history 为基础，因为摘要已经把 target 之后的信息混入了过去；必须从 raw updates 重放。

如果：

```text
target >= compaction boundary
```

则可以加载 checkpoint 中的 compacted history，再重放边界之后的更新。

## 34. Cross-compaction Rewind

`helpers/replay.rs` 会先在 `updates.jsonl` 中寻找最新可用 checkpoint marker。

两条路径：

```text
rewind 到压缩前
    忽略 compacted blob
    从原始 updates 重建

rewind 到压缩后
    载入 compacted blob
    从 checkpoint prompt index 继续累计
```

它还必须避免把 summary 内部的 User item 计入真实 prompt counter。

## 35. History Replace 后的 token 校准

`replace_conversation_for_compaction()` 先计算新历史的本地估算：

```text
base_estimate = estimate(new compacted history)
```

如果旧状态存在可靠数据，则计算：

```text
ratio = pre_replace_provider_total / estimate_at_last_response
new_total ≈ base_estimate * ratio
```

这样保留当前 provider/tokenizer 相对于 bytes/4 估算的偏差。

随后：

```text
estimated_tokens_since_model = 0
total_tokens = calibrated estimate
estimate_at_last_response = base estimate of new conversation
```

这也是为什么 `estimate_at_last_response` 不能只在临时变量中存在。

## 36. Forked Session 的 inherited prefix

fork session 可能要求继承父 session 的不可变前缀，以保持缓存、审计或语义连续性。

普通 compacted history 若直接替换整个历史，可能把 inherited prefix 一并压掉。因此：

```rust
preserve_inherited_prefix(...)
```

尝试把继承前缀重新 pin 到压缩后 suffix 前。

## 37. Fork Prefix Pressure Release

永久保留继承前缀也可能导致“怎么压缩仍超过阈值”。代码会投影 re-pin 后的 token 数：

```text
projected preserved history >= threshold
```

则释放继承 prefix，并将：

```text
prefix_released = true
```

设为 sticky，后续 compaction 不再反复尝试重新 pin 同一前缀。

这是可继续运行优先于 cache/prefix preservation 的降级策略。

## 38. Auto-compaction Suppression

自动 compact 如果发生确定性失败，不能每个 sampling loop 都重复触发同一失败。

`auto_compact_suppressed` 使用状态值区分原因：

| 原因 | 典型解除方式 |
| --- | --- |
| Size | sticky；模型切换等显式条件后重评 |
| Schema | sticky；环境变化后重评 |
| CreditBlock | 账户恢复后 |
| Auth | credential 恢复或重新登录后 |
| Other | 下一 turn 可重试 |

manual `/compact` 不受 suppression 限制。

## 39. Auth Failure 为什么必须中止 Turn

如果 pre-sampling compaction 因 401 失败，继续向同一 backend 提交原 oversized request 没有意义。

代码会：

- 标记 auth suppression；
- 发 RetryState/auth_required；
- 返回可识别的 auth error；
- 由上层 re-auth/retry 逻辑接管。

auth 恢复后 `clear_auth_compact_suppression()` 只清 auth 原因，不会误清 credit suppression。

## 40. 两套“上下文不足”检测为何都需要

### 主动预测

```text
estimated_total > configured threshold/window
```

优点是用户通常不会看到 backend error。

### 被动事实校正

```text
backend error.model_metadata.context_window
```

优点是能处理：

- 模型元数据过期；
- 路由到不同 deployment；
- provider 临时使用更小窗口；
- 本地配置与服务端实际限制不一致。

两者组合才构成闭环。

## 41. Compaction Mode 与 Segments

配置中的 `compaction_mode` 决定是否额外写 segment，以及 segment detail level。

segment 并不是发给下一次模型的主历史，而是用于保留被压缩内容的可追踪表示、调试或后续恢复。

telemetry 会记录：

```text
mode
detail
```

便于按 compaction variant 比较结果。

## 42. Telemetry

`session.compact_inner` 记录大量字段：

```text
compaction_tokens_before
compaction_tokens_after
compaction_summary_chars
compaction_attempts
compaction_degenerate_rejections
compaction_input_overflow_rejections
compaction_deterministic_rejections
compaction_transient_rejections
compaction_trigger
compaction_trigger_pct
compaction_threshold_pct
compaction_outcome
compaction_stop_reason
compaction_ttft_ms
compaction_stream_ms
compaction_delta_count
compaction_two_pass_used
compaction_prefire_hit
compaction_pass2_latency_ms
compaction_prefire_waited_ms
compaction_prefire_stale
compaction_prefix_released
```

这些字段足以回答：压缩为何触发、耗时在哪、摘要是否退化、输入是否太大、prefire 是否有效、fork prefix 是否成为压力源。

## 43. 完整自动压缩时间线

```mermaid
sequenceDiagram
    participant Loop as Agent Turn Loop
    participant Chat as ChatStateActor
    participant Compact as Compaction
    participant Model as Sampler
    participant Disk as Persistence

    Loop->>Chat: get_estimated_total_tokens
    Chat-->>Loop: provider total + local delta
    Loop->>Compact: run_compact_only(trigger)
    Compact->>Chat: snapshot history/system/config
    Compact->>Model: sample full-replace summary
    Model-->>Compact: summary + timings
    Compact->>Compact: rebuild state reminders
    Compact->>Compact: sanitize + validate tool pairs
    Compact->>Disk: persist checkpoint/segment
    Compact->>Chat: replace conversation atomically
    Chat->>Chat: recalibrate token count
    Compact-->>Loop: completed
    Loop->>Chat: rebuild next sampling request
```

## 44. 失败场景示例

### 场景 A：Tool 输出突然超过窗口

```text
provider total = 110k
context window = 128k
Tool result estimate = 30k
estimated total = 140k
```

preflight overflow 在下一 sampling 前触发 compact。

### 场景 B：配置写 256k，provider 实际 128k

主动阈值未触发，请求被 backend 拒绝；error metadata 返回 128k；`should_compact_on_error()` 触发 compact 并 resubmit。

### 场景 C：压缩输入本身过大

Verbatim 失败后进入 VerbatimFitted，再不行进入 Lossy，而不是无限提交同一个 request。

### 场景 D：摘要留下孤立 Tool Result

sanitize 移除 orphan；若 validate 仍失败，则 minimal rebuild，不提交非法 history。

### 场景 E：Fork prefix 使压缩后仍达阈值

释放 inherited prefix，设置 sticky `prefix_released`，后续不再重复 re-pin。

## 45. 关键 Invariants

1. System head 不因 compaction 消失；
2. Tool Result 必须有匹配 Tool Call；
3. compaction commit 前 cancellation 不修改原历史；
4. Tool Result 后的 overflow 判断使用 estimated total；
5. provider usage 到达后清空 local delta；
6. Assistant 内容不能被 double count；
7. summary 不能替代运行状态恢复 reminder；
8. checkpoint 必须记录 compaction prompt boundary；
9. rewind 到边界前不能使用包含未来信息的 summary；
10. auto suppression 不能阻止用户手动 compact；
11. auth/credit suppression 不能因普通 model switch 被误清；
12. request body byte budget 与 token budget 独立治理。

## 46. 调试清单

### 误触发过早 compaction

检查：

- resolved `threshold_percent`；
- 当前 model 的真实 `context_window`；
- `total_tokens` 与 `estimated_tokens_since_model`；
- context window override；
- model switch 是否刚发生；
- Tool Result 是否被重复计入。

### 明明超窗却未 compact

检查：

- `auto_compact_suppressed` 当前 reason；
- memory flush 是否暂时令 pre-sampling check 返回 None；
- sampling config 是否缺失；
- provider error 是否带 model metadata；
- Tool Result 是否确实已 push 到 ChatState。

### compact 完仍然很大

检查：

- summary_chars；
- system reminder/Skill/AGENTS.md 体积；
- tool definition tokens；
- fork inherited prefix；
- prefix_released telemetry；
- provider/local calibration ratio。

### rewind 后历史错乱

检查：

- updates.jsonl 中 compaction checkpoint marker；
- checkpoint 文件是否存在；
- prompt_index_at_compaction；
- target 位于边界前还是后；
- synthetic summary User 是否被误计为真实 prompt。

## 47. 推荐测试矩阵

1. provider usage + User delta；
2. provider usage + Tool Result delta；
3. usage 后 delta 清零；
4. Assistant 不 double count；
5. 低于 auto threshold；
6. 恰好等于 threshold；
7. 高于 threshold；
8. Tool Result 后硬 overflow；
9. backend metadata window 更小；
10. model switch 到更大窗口；
11. model switch 到更小窗口；
12. manual compact bypass suppression；
13. auth suppression 与恢复；
14. credit suppression 不被 auth clear 误清；
15. verbatim overflow 降级；
16. degenerate summary retry；
17. two-pass prefire hit；
18. prefire stale fingerprint；
19. in-flight prefire wait；
20. orphan Tool Result sanitize；
21. validate 失败 minimal fallback；
22. cancel before commit；
23. fork prefix preserve；
24. fork prefix pressure release；
25. rewind 到 compaction 前；
26. rewind 到 compaction 后；
27. 多次 compaction 之间 rewind；
28. inline image body budget；
29. old Tool Result soft trim；
30. very old Tool Result hard clear。

## 48. 核心源码索引

| 主题 | 文件/函数 |
| --- | --- |
| token state | `xai-chat-state/src/actor/state.rs` |
| push/usage/replace | `xai-chat-state/src/actor/mutations.rs` |
| estimated total query | `xai-chat-state/src/actor/mod.rs`、`handle.rs` |
| auto threshold | `xai-chat-state/src/actor/queries.rs` |
| request pruning | `xai-chat-state/src/actor/request_builder.rs` |
| compaction config | `xai-grok-shell/src/session/compaction_config.rs` |
| 所有 shell 入口 | `xai-grok-shell/src/session/compaction.rs` |
| summary input/output helpers | `session/helpers/session_compact.rs` |
| state reminder | `session/helpers/compaction_context.rs` |
| full-replace algorithm | `common/xai-grok-compaction/src` |
| cross-boundary replay | `session/helpers/replay.rs` |

## 49. 相关文档

- [14_complete_execution_timeline.md](14_complete_execution_timeline.md)：完整执行时间线
- [15_actor_channels_and_concurrency.md](15_actor_channels_and_concurrency.md)：ChatStateActor 串行化边界
- [16_sampling_and_agent_loop.md](16_sampling_and_agent_loop.md)：四类 compact 入口如何接入 turn loop
- [13_prompt_and_tool_list_assembly.md](13_prompt_and_tool_list_assembly.md)：压缩后历史如何进入下一次 request

