# Grok Build 上下文压缩、Token 预算与长会话生命周期源码详解

本文分析 Grok Build 如何让一个 Agent 会话在持续几十轮、几百轮甚至跨进程恢复后仍能继续工作。

表面上，这个问题似乎只是：

> 对话太长时，让模型总结一下旧消息。

但源码里的真实问题远比这复杂。系统必须同时回答：

1. 当前 prompt 到底占了多少 token；
2. provider 报告值与本地估算值不一致时相信谁；
3. 工具结果在两次模型响应之间突然撑爆窗口怎么办；
4. 什么时候自动压缩，什么时候只裁剪旧工具输出；
5. 压缩模型自身也塞不下原始对话时怎么办；
6. 摘要失败、被截断、内容退化或用户取消时怎么办；
7. 压缩后哪些状态必须原样恢复，而不能交给摘要模型“记住”；
8. rewind、fork、model switch 和进程恢复如何跨越 compaction 边界；
9. context window、output limit、计费 token、Goal budget 为什么不是同一个概念；
10. 怎样让多次压缩不会逐轮丢失关键任务状态。

核心源码：

```text
crates/codegen/xai-grok-shell/src/session/
  compaction.rs
  compaction_config.rs
  compaction_segments.rs
  two_pass.rs
  helpers/
    session_compact.rs
    full_replace_compaction.rs
    compaction_context.rs
    memory_flush.rs
    replay.rs
  acp_session_impl/
    turn.rs
    sampler_turn.rs
    model_switch.rs
    rewind.rs

crates/codegen/xai-chat-state/src/
  actor/state.rs
  actor/mutations.rs
  actor/queries.rs
  actor/request_builder.rs
  compaction_utils.rs
  compaction_mode.rs
  compaction_transcript.rs
  usage.rs

crates/common/xai-grok-compaction/src/
  code_compaction/
  intra_compaction/
  inter_compaction/
  history/
  select.rs
  token.rs

crates/codegen/xai-grok-sampling-types/src/
  conversation.rs
  types.rs

crates/codegen/xai-grok-shell/src/tools/tool_context.rs
crates/codegen/xai-grok-subagent-resolution/src/context.rs
```

---

## 1. 先建立正确的心智模型

Grok Build 的长会话不是一个无限增长的 `Vec<Message>`。

更准确的模型是：

```text
原始事件日志 updates.jsonl
        │
        ├─ 可用于 replay / rewind / 审计
        │
        ▼
ChatStateActor 中的当前工作会话
        │
        ├─ 旧 tool result 逐步裁剪
        ├─ image 按请求体字节预算驱逐
        ├─ 达阈值后 full-replace compaction
        └─ 压缩成新的短 conversation
                │
                ├─ summary
                ├─ 最后真实用户请求
                ├─ AGENTS.md / Skills / Plan 状态
                ├─ 运行中 task / subagent / MCP 提醒
                └─ 可选 transcript/segment 指针
```

因此系统同时维护两种历史：

```text
durable historical truth ≠ current model working context
```

- durable historical truth 用于恢复与回放；
- current working context 用于下一次模型请求；
- compaction 会替换后者，但不会简单删除前者。

这也是后文理解 checkpoint、segment、rewind 的基础。

---

## 2. 五种容易混淆的 Token 数

阅读源码前，必须先把五类预算分开。

| 概念 | 代表字段 | 含义 | 是否累计 |
| --- | --- | --- | --- |
| 当前上下文占用 | `ChatState.total_tokens` | 最近一次模型响应报告的 live context 总量 | 否，近似当前窗口 |
| 响应间增量估算 | `estimated_tokens_since_model` | 上次响应后新加入的 user/tool 内容估算 | 临时累计 |
| 模型输出上限 | `max_output_tokens` / `max_completion_tokens` | 单次调用最多生成多少 token | 否 |
| 计费 Usage Ledger | `UsageLedger` | 所有模型调用实际 input/output 的累计账单 | 是 |
| 执行预算 | Goal `token_budget`、`TaskOutputTokenBudget` | 控制长期任务或 child 最多消耗/输出多少 | 是 |

最常见的误解是把 `total_tokens` 当成“这个 session 历史上一共花了多少 token”。

实际上源码明确说明：

```rust
// Responses wire `total` is live context length.
```

累计计费由 `UsageLedger` 负责。

可以写成：

```text
live_context_tokens(t)
  = provider_reported_total_of_latest_call

billed_tokens(session)
  = Σ each_call.input_tokens + Σ each_call.output_tokens
```

长会话可能已经累计花费数百万 token，但压缩后的 live context 只有几万 token。这两者完全不矛盾。

---

## 3. ChatStateActor 是上下文状态的单写者

`ChatStateActor` 独占维护：

```rust
pub(crate) struct ChatState {
    pub conversation: Vec<ConversationItem>,
    pub sampling_config: SamplingConfig,
    pub prompt_index: usize,
    pub total_tokens: u64,
    pub estimated_tokens_since_model: u64,
    pub estimate_at_last_response: u64,
    pub last_compaction_prompt_index: Option<usize>,
    // ...
}
```

这里的设计价值是：

- message append；
- token 更新；
- conversation replacement；
- rewind；
- prompt index；
- persistence；

都在一个 actor command loop 中串行处理。

因此不会出现：

```text
线程 A 正在 compact 替换历史
线程 B 同时 append tool result
最终 B 的结果被旧 snapshot 覆盖
```

即使 shell 的 `SessionActor` 负责更高层编排，真正的 conversation mutation 仍通过 `ChatStateHandle` 发回单写 actor。

---

## 4. 本地 Token 估算为什么是 bytes / 4

初始化 conversation 时，代码调用：

```rust
let initial_tokens = estimate_conversation_tokens(&conversation);
```

它本质上使用轻量估算，而不是每次都运行模型对应 tokenizer。

粗略模型是：

```text
estimated_tokens ≈ serialized/text bytes ÷ 4
```

这种方法的优势：

- O(n) 且实现简单；
- 不依赖不同 provider 的 tokenizer；
- append 一条消息时可快速增量计算；
- 足够用于“是否接近阈值”的保守判断。

它不是精确账单，也不是 provider 最终权威值。

因此系统采用混合策略：

```text
有 provider usage 时：使用 provider total
两次 response 之间：provider total + 本地增量估算
没有 provider usage 时：从 conversation 本地估算
```

---

## 5. `total_tokens + estimated_tokens_since_model`

模型响应到达后：

```rust
record_token_usage(u64::from(usage.total_tokens));
```

`record_token_usage` 做三件事：

```rust
self.state.estimated_tokens_since_model = 0;
self.state.estimate_at_last_response =
    estimate_conversation_tokens(&self.state.conversation);
self.state.total_tokens = total_tokens;
```

之后，如果 Agent 又调用工具，新的 `ToolResult` 会在下一次模型调用前进入 conversation。

此时 provider 还没有机会重新计数，所以：

```rust
GetEstimatedTotalTokens =>
    total_tokens + estimated_tokens_since_model
```

注意 `push_message` 的一个细节：

```rust
let count_in_delta = !matches!(item, ConversationItem::Assistant(_));
```

Assistant response 不再加入 delta，因为它已经包含在刚收到的 `usage.total_tokens` 中；再次增加会双计。

User、ToolResult 等响应后新增内容才进入 delta。

一个例子：

```text
模型响应报告 total_tokens = 100,000
assistant 文本已经包含在 100,000 中
之后工具返回约 8,000 token

get_total_tokens()           = 100,000
get_estimated_total_tokens() = 108,000
```

自动压缩前检查必须使用后者，否则大工具输出可能在模型调用前把请求撑爆。

---

## 6. `estimate_at_last_response` 解决 provider overhead

为什么还要维护：

```rust
estimate_at_last_response: u64
```

因为 provider 报告的 token 总量可能包含本地 `bytes / 4` 看不到的开销：

- message framing；
- tool schema；
- hosted tools；
- provider 特有的序列化；
- reasoning/encrypted metadata；
- tokenizer 与 bytes/4 的系统误差。

在响应时：

```text
provider_total = 51,000
local_conversation_estimate = 40,000
```

两者比例约为：

```text
overhead_ratio = 51,000 / 40,000 = 1.275
```

如果 compact 后新 conversation 的本地估算是 8,000，不能直接把 live total 重置成 8,000，否则下一轮会系统性低估。

源码中的 reseed 公式是：

```rust
ratio = pre_replace_total / estimate_at_last_response
estimated_tokens = round(base_estimate * ratio)
estimated_tokens = min(estimated_tokens, pre_replace_total)
```

即：

```text
post_compact_total
  = min(
      local_estimate(compacted_history)
        × provider_total_before / local_estimate_at_last_response,
      provider_total_before
    )
```

最后的 cap 保证：

> compaction 绝不能在 UI 和阈值判断上表现为 token 使用量反而上升。

普通 rewind 的 `replace_conversation(..., false)` 不携带这层 overhead；只有 compaction replacement 使用比例校准。

---

## 7. UsageLedger 是另一套账

`xai-chat-state/src/usage.rs` 中：

```rust
pub struct UsageTotals {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cached_read_tokens: u64,
    pub cache_creation_tokens: u64,
    pub reasoning_tokens: u64,
    pub model_calls: u64,
    pub api_duration_ms: u64,
    pub cost_usd_ticks: Option<i64>,
    pub cost_missing_calls: u64,
}
```

`UsageLedger` 同时维护：

```text
totals
by_model
main_loop_model_calls
incomplete
```

它区分：

- main loop model call；
- subagent usage；
- compaction 等 side call。

注释特别强调：

> compaction side call 不应通过 `record_main_loop_call` 增加 `numTurns`。

因此：

```text
conversation compaction 会改变 live context
但不等于一个新的用户 turn
也不能污染 main-loop turn count
```

`incomplete` 是 fail-closed 标记。当 subagent usage 未能完整 drain，或者 response 缺少 usage 时，系统不会假装账单准确。

---

## 8. 自动压缩阈值如何解析

默认阈值：

```rust
DEFAULT_AUTO_COMPACT_THRESHOLD_PERCENT = 85
```

解析优先级从高到低：

```text
1. GROK_AUTO_COMPACT_THRESHOLD_PERCENT
2. 用户 TOML [model.<id>] per-model
3. 用户 TOML [session] global
4. remote settings per-model
5. remote settings global
6. 默认 85
```

环境变量必须落在 `0..=100`，否则忽略并继续 fallback。

阈值判断使用统一 helper：

```rust
xai_token_estimation::exceeds_threshold(total, context_window, percent)
```

概念上：

```text
trigger iff total_tokens > context_window × percent / 100
```

边界测试显示等于阈值时通常不触发，超过才触发。

阈值不是 hard context limit。85% 提前触发是为了给以下内容留 runway：

- compaction prompt；
- tool definitions；
- summary 输出；
- 下一轮 assistant completion；
- 估算误差。

---

## 9. 四条 Compaction 触发路径

Grok Build 不是只有一个 auto-compact 检查。

完整触发矩阵如下：

| 触发点 | 方法 | 用途 |
| --- | --- | --- |
| 用户命令 | `run_compact` | 手动 `/compact [context]` |
| 每次 sampling 前 | `check_auto_compact_needed` | 达到百分比阈值 |
| tool loop 后 | `check_preflight_overflow` | tool output 已超过 hard window |
| sampling error 后 | `should_compact_on_error` | provider 返回更小 context metadata |
| model switch | `maybe_compact_on_model_switch` | 切到窗口更小的模型 |

它们最终大多汇合到：

```text
run_compact_only(trigger_info)
  → run_compact_inner(..., Auto)
```

手动路径则是：

```text
run_compact(user_context)
  → run_compact_inner(user_context, ..., Manual)
```

手动 compaction 与自动 compaction 的一个重要差别是：

> 自动失败抑制不会阻止用户显式执行 `/compact`。

这样用户仍可在修复认证、配置或提供额外摘要提示后主动重试。

---

## 10. Sampling 前阈值检查

Agentic loop 每次准备请求时都会执行：

```rust
if task_output_token_budget.is_none()
    && let Some(trigger_info) = check_auto_compact_needed().await
{
    run_compact_only(trigger_info).await;
}
```

`check_auto_compact_needed`：

1. memory flush 进行中则跳过；
2. 读取当前 model context window；
3. 使用 `get_estimated_total_tokens()`；
4. 更新 UI/context usage signal；
5. 检查 suppression；
6. 支持 debug force flag；
7. 超过阈值则返回 tokens/window/percentage。

这里专门排除了 `task_output_token_budget` child。

预算型 workflow child 不走普通长会话 compact/recovery，它需要严格受输出 grant 控制；否则 compaction side call 和重试会破坏可归因预算。

---

## 11. Tool output 后的 Preflight Overflow

一次 agentic turn 可能是：

```text
model → tool call → 20 MB output → model → tool call → ...
```

即使第一次模型响应只有 70% context，工具结果也可能立刻把下一次请求推到 110%。

因此 tool execution 后、下一次 sampling 前还有：

```rust
check_preflight_overflow()
```

它不是按 85% 判断，而是检查：

```text
estimated_total > context_window
```

触发后压缩并 `continue` agent loop，重新从 compact 后的 conversation 构建请求。

这个 guard 的意义是：

> 不要明知请求超窗还发给 provider，再依赖一次昂贵且不稳定的 400 错误恢复。

---

## 12. Provider Error 驱动的修正

有时本地 model catalog 中的 context window 已经过时，provider 错误响应会携带新的 model metadata。

`handle_sampling_failure` 调用：

```rust
should_compact_on_error(&error)
```

只有满足以下条件才触发：

- 当前未 suppression；
- error 有 model metadata；
- metadata 有非零 context window；
- `estimated_total > error.context_window`。

随后如果没有 debug context override，系统会把 session 的 sampling config 更新到 provider 告知的新 window：

```rust
cfg.context_window = new_cw;
```

再执行 compaction，并返回：

```rust
SamplerFailureRecovery::CompactAndResubmit
```

因此这是一个闭环：

```text
错误暴露真实窗口
  → 更新本地元数据
  → compact
  → 重建请求
  → resubmit
```

---

## 13. Model Switch 为什么也可能触发压缩

每轮结束记录：

```rust
PreviousModelInfo {
    model_slug,
    context_window,
}
```

下一轮开始调用 `maybe_compact_on_model_switch`。

规则是：

```text
同一模型                    → 不处理
切到更大或相同窗口          → 不 compact
切到更小窗口且超过新阈值    → 先 compact
```

model switch 会清理 size/schema/turn 级 suppression，因为新模型或新窗口可能改变结果。

但不会清理：

- credit suppression；
- auth suppression。

因为换模型并不能可靠修复账户状态。

---

## 14. Full-Replace Compaction 的总体流程

`run_compact_inner` 是主干。

可以压缩为：

```text
snapshot conversation / config
  ↓
prepare summarizer input
  ↓
try cached two-pass NOTE₁
  ├─ hit → pass2 final summary
  └─ miss → single-pass + bounded retry
  ↓
if context overflow: verbatim → fitted → lossy
  ↓
validate / clean summary
  ↓
rebuild short conversation
  ↓
sanitize tool-call integrity
  ↓
persist segment + checkpoint
  ↓
replace ChatState conversation
  ↓
reset/reinject runtime state
  ↓
notify + telemetry
```

“Full replace” 的含义是：

> 成功后不是在旧消息前加一条 summary，而是构造一份新的 conversation vector 并原子替换。

这比逐条删除更容易维护结构不变量。

---

## 15. Compaction 前的结构检查

`run_compact_inner` 首先并行获取：

```rust
tokio::join!(
    get_conversation_len(),
    get_system_message(),
    get_conversation(),
)
```

然后 fail fast：

- conversation 为空；
- 找不到 system message；
- prepare 后 conversation 为空；
- prepare 后没有 system item。

这些不是“让 summarizer 自己凑合”的情况。

缺失 system message 通常意味着 chat-state 死亡、损坏或非法恢复；继续 sampling 可能生成不可验证的 replacement，所以直接返回内部错误。

---

## 16. Verbatim 与 Lossy Summarizer Input

系统有两种基础输入形态。

### 16.1 Lossy

`prepare_conversation_for_summarization` 会：

```text
drop ToolResult
assistant tool_calls → "[Called tools: ...]"
drop Reasoning siblings
image → "[image]"
```

优点：非常省 token，兼容 strict provider。

代价：摘要模型看不到完整工具结果和参数。

### 16.2 Verbatim

`prepare_conversation_for_verbatim_summarization` 尽量保留：

- tool call；
- tool result；
- images；
- 原始最近工作链。

对于 `ApiBackend::Messages` 可按需 strip reasoning，避免修改文本后 signed thinking block 失效。

同时删除末尾没有 ToolResult 的 dangling assistant tool call，防止 strict backend 拒绝请求。

配置：

```text
compaction_verbatim_input = true/false
```

即便启用 verbatim，发生输入超窗时仍会降级到 fitted/lossy。

---

## 17. 输入降级梯：Verbatim → Fitted → Lossy

压缩最危险的死锁是：

```text
主 conversation 太大，需要 compact
但把主 conversation 发给 compact model 本身也超窗
```

源码用三阶段 input ladder 解决：

```rust
enum InputStage {
    Verbatim,
    VerbatimFitted,
    Lossy,
}
```

只有 provider 明确返回 context overflow 才前进到下一阶段。

### Stage 1: Verbatim

完整度最高，优先利用 prompt/KV cache。

### Stage 2: VerbatimFitted

预算：

```text
context_window
  - 32,768 summary reserve
  - tool definition tokens
```

然后调用 `fit_conversation_to_budget`。

### Stage 3: Lossy

预算：

```text
context_window × 70%
  - tool definition tokens
```

先删除 tool result/reasoning/image bulk，再 fit。

如果 Lossy 仍然超窗，才将失败归类为 deterministic size failure，并对 auto-compaction 设置 sticky suppression。

这条 ladder 是长会话系统最值得借鉴的设计之一：

> 不是一开始就牺牲细节，也不是对同一个必然失败的 payload 盲目重试。

---

## 18. `fit_conversation_to_budget` 如何保持结构

它不是简单从字符串尾部截取。

算法大致是：

1. 始终保留 leading System；
2. 从 conversation 尾部反向累计最近 item；
3. 找到能装入预算的连续后缀；
4. 如果后缀从 ToolResult 开始，向后跳过 orphan result；
5. 如果一个完整 item 都装不下，仍恢复并截断最新工作单元；
6. ToolResult 集合与 owning assistant tool call 尽量一起保留；
7. UTF-8 char boundary 安全截断；
8. 添加明确的 dropped bytes marker。

最后兜底逻辑尤其重要：

```text
宁可截断最近 turn 的内容
也不能把 summarizer 输入变成只有 system、完全没有当前工作
```

否则 summary 可能保留了很早的背景，却丢掉正在修改的文件和最新错误。

---

## 19. Compaction Prompt 的职责

`build_compaction_prompt` 要求模型输出单个 `<summary>...</summary>` block，并按九个部分组织：

1. Primary Request and Intent；
2. Key Technical Concepts；
3. Files and Code Sections；
4. Errors and Fixes；
5. Problem Solving；
6. All User Messages；
7. Pending Tasks；
8. Current Work；
9. Optional Next Step。

Prompt 明确要求：

- 继承 prior compaction summary；
- 不要把 analysis 单独输出；
- 不要读取 `/tmp/compaction` 一类面向未来 Agent 的旁路文件；
- 优先简洁但必须足够让 successor 无缝继续；
- 不能把系统生成的 compaction instruction 当真实用户消息。

用户手动执行：

```text
/compact 请特别保留数据库迁移状态
```

时，`user_context` 会注入 prompt，要求摘要突出这段额外上下文。

---

## 20. 为什么压缩调用仍携带 Tools

`run_compact_inner` 会重新准备 tool definitions，并把 tools 传给 compaction sampling。

如果 backend search 已激活，会过滤重复的 `web_search` 定义。

工具 token 也进入输入预算：

```rust
compaction_tool_tokens = estimate_tool_definitions_tokens(...)
```

`CompactionToolChoice` 支持：

```text
auto
none
```

解析优先级：

```text
GROK_COMPACTION_TOOL_CHOICE
  > config
  > remote
  > Auto
```

这说明 compaction sampling 并非完全独立的纯文本 endpoint；它复用正常 sampling transport 和一部分工具能力，但工具 schema 自身必须计入窗口。

---

## 21. Compaction Sampling 的时间边界

两类超时共同约束 compaction：

### 21.1 Idle timeout

每个输出 chunk 之间不能无限停滞。

### 21.2 Wall-clock budget

默认：

```rust
DEFAULT_COMPACTION_WALL_CLOCK_BUDGET_SECS = 300
```

优先级：

```text
GROK_COMPACTION_WALL_CLOCK_SECS
  > remote global
  > 300 秒
```

`0` 表示禁用总时长限制。

小于 120 秒只 warning，不强制 clamp，因为真实超大输入的成功尾延迟可能超过两分钟。

这两个时间边界解决不同问题：

```text
idle timeout    → stream 卡住
wall-clock      → 一直有微小输出但 reasoning runaway
```

---

## 22. Retry：什么能重试，什么不能

Full replace 默认：

```text
max attempts = 3
retry delay  = 3 seconds
```

会重试：

- empty response；
- degenerate summary；
- transient network/sampling failure；
- 408 / 429 等瞬态状态。

不会对同一 payload 重试：

- deterministic 4xx；
- schema/invalid request；
- context overflow。

context overflow 不走普通 retry，而是回到 host 的 input ladder，构造更小的新 payload。

因此系统区分：

```text
retry same request
  vs
degrade input and issue a different request
```

这是错误恢复语义上的重要差别。

---

## 23. Degenerate Summary 防护

模型可能成功返回 HTTP 200，却只输出：

```text
<summary>Continue the task.</summary>
```

把它提交为 replacement 会造成不可逆信息损失。

所以代码清洗 summary 后检查：

```rust
MIN_SUMMARY_SEED_CHARS = 500
```

低于 500 字符视为 degenerate，像 transient failure 一样重试。

这不是语言质量评分，而是最低信息量保险丝。

源码注释提到健康生产摘要通常远高于这个下限；500 是识别明显坏输出，不是要求摘要越长越好。

---

## 24. Summary 清洗与控制标签去活化

原始模型输出不能直接塞回 conversation。

`format_compact_summary` 会：

1. 删除 leading `<analysis>...</analysis>`；
2. 提取 outer `<summary>...</summary>`；
3. 转为 `Summary:\n...`；
4. 清除 markdown analysis scratchpad；
5. 将正文中引用的 compaction control tag 插入零宽字符去活化；
6. 合并过多空行；
7. trim。

为什么要去活化：

```text
摘要正文可能引用“请只输出 <summary>”
下一轮模型把这段当活指令
于是正常回答再次输出 summary block
```

代码会将：

```text
<summary>
```

改成视觉相似但不再可匹配的：

```text
<​summary>
```

最终 carrier 是：

```text
This session is being continued from a previous conversation that ran out of context.
The summary below covers the earlier portion of the conversation.

Summary:
...
```

---

## 25. Two-Pass Prefire：把延迟搬到后台

单次 compact 的主要延迟来自让模型读取超长 prefix。

Two-pass 策略：

```text
达到阈值前 10 个百分点
  → 后台 summarize 约 95% prefix，得到 NOTE₁

真正达到 compact 阈值
  → 只让 pass2 读取 NOTE₁ + 最近约 5% tail
  → 得到 successor-visible NOTE₂
```

默认阈值 85% 时，prefire 大约从 75% 开始：

```rust
start_pct = threshold.saturating_sub(prefire_lead_percent())
```

环境变量：

```text
GROK_PREFIRE_LEAD_PERCENT
```

可改变 lead，默认 10。

prefire 使用 `spawn_local`，读取 conversation snapshot，不修改主 conversation。

---

## 26. Two-Pass 如何切分 95% / 5%

`split_conversation_for_two_pass` 不是按 item 数量，而是按估算 token weight。

```text
每个 item 估算 token
  → 累计到总量的 95%
  → 得到 split_idx
```

之后调整边界，保证：

- assistant tool call 不与后续 ToolResult 分开；
- tail 尽量非空；
- 不以孤立 ToolResult 开头。

因此实际 split 可能略偏离 95%，结构合法性优先于数学精确比例。

Pass 1：

```text
prepared prefix + two-pass compaction prompt
  → NOTE₁
```

Pass 2：

```text
system from prefix
+ NOTE₁ carrier
+ recent tail
+ special final-compaction instruction
  → NOTE₂
```

最终写入 conversation 的只有 NOTE₂。

---

## 27. NOTE₁ 的提取与上限

Pass 1 输出中，如果存在完整且足够长的 `<summary>` block，优先提取其内部。

阈值：

```rust
TWO_PASS_MIN_SUMMARY_BLOCK_CHARS = 1000
```

否则使用 pass1 的完整 response。

NOTE₁ 嵌入 pass2 的上限：

```rust
TWO_PASS_MAX_NOTE1_CHARS = 12_000
```

超过则截断并添加 marker。

这是二层预算：

```text
NOTE₁ 必须足够完整
但不能大到再次吞掉 pass2 的低延迟优势
```

---

## 28. Prefire Cache 如何防止使用过期摘要

`AsyncCompactionCache` 保存：

```rust
note1
prefix_len
fingerprint
model_slug
pass1_latency_ms
```

fingerprint 计算 conversation prefix 的：

- item 数；
- item variant tag；
- text content。

Pass 2 使用 cache 前检查：

```text
prefix_len 合法
model slug 未改变
live prefix fingerprint 相同
```

以下操作都会使 cache 失效：

- edit；
- rewind；
- branch/fork state change；
- model switch；
- prefix 内容变化。

如果 stale，直接 fallback single-pass，绝不拿旧 NOTE₁ 覆盖新历史。

---

## 29. Prefire 并发与取消

`PrefireState` 包含：

```text
in_flight: AtomicBool
cache: RefCell<Option<AsyncCompactionCache>>
handle: RefCell<Option<JoinHandle<()>>>
```

`try_begin` 用 compare-exchange 保证同一 session 只启动一个后台 pass1。

如果真正 compaction 到来时 pass1 还在运行：

1. pass2 取走 handle；
2. await pass1；
3. 再读取 cache；
4. 等待时间计入最终 TTFT；
5. pass1 stream latency 不混入 pass2 stream 指标。

`CompactCancelGate` 不是 bool，而是 holder count：

```text
prefire scope + compact scope 可重叠
第一个 enter 创建 token
嵌套 enter 共享 token
stop 取消共享 token
最后一个 scope drop 后下一代获得 fresh token
```

这避免 stop 只取消其中一个 sampling，而另一个还在后台继续烧 token。

---

## 30. CompactionStateContext：摘要不能承担所有状态

LLM summary 是概率输出，不适合保存结构化运行状态。

所以 compact 成功后，系统从 live runtime 重新收集：

- running terminal tasks；
- running subagents；
- agent edited paths；
- connected MCP servers；
- todo items；
- AGENTS.md paths；
- discovered skills；
- plan mode；
- memory recovery context。

并渲染为新的 `<system-reminder>`。

这是一条非常重要的分层原则：

```text
semantic history → LLM summary
runtime truth     → deterministic re-query and reinjection
```

例如运行中的 task ID 如果只依赖 summarizer，有可能被漏掉或抄错。直接向 task/subagent coordinator 查询才是权威来源。

---

## 31. Tool 名称为什么在 Compaction 时动态解析

提醒中会告诉 successor：

- 用哪个 tool 查询后台 task；
- 用哪个 tool 取消 child；
- 用哪个 tool 搜索 MCP；
- 用哪个 tool 调 MCP。

但不同 preset/tool registry 的真实名称可能不同。

因此代码通过 ToolBridge template：

```text
${{ tools.by_kind.background_task_action }}
${{ tools.by_kind.kill_task_action }}
${{ tools.by_kind.search_tool }}
${{ tools.by_kind.use_tool }}
```

动态解析当前 session 的实际 tool name。

解析失败时宁可省略对应 reminder，也不向模型注入一个不存在的名字。

---

## 32. 压缩后 Conversation 的精确结构

`build_compacted_history` 构造的典型顺序是：

```text
1. 原 System prompt
2. user-info / project-layout prefix
3. AGENTS.md reminder（可选）
4. 最后真实 user query（可选）
5. recent messages（某些模式/路径）
6. continuation summary
7. runtime system reminder（可选）
```

伪代码：

```rust
vec![
    system_message,
    user_meta(user_message_prefix),
    project_instructions(agents_md),
    user(wrap_user_query(last_query)),
    // recent tail
    user_meta(format_compact_summary_content(summary)),
    system_reminder(runtime_state),
]
```

AGENTS.md 不仅由 summary 描述，而是原样重新注入。

这防止构建规则、安全约束和测试命令在摘要中被压缩成模糊表述。

---

## 33. “最后真实用户请求”并不等于最后一个 User item

Conversation 中有大量 synthetic User：

- AutoContinue；
- SystemReminder；
- AutoRecovery；
- GoalSummary；
- notification drain；
- permission follow-up；
- bootstrap metadata。

`is_real_user_turn` 的规则包括：

- 必须是 `ConversationItem::User`；
- `synthetic_reason` 必须为空；
- 不能只是 metadata tag；
- 不能是 `__auto_continue__`；
- 不能是 `AUTO_CONTINUE_PROMPT`；
- image-only prompt 仍然是真实用户 turn。

如果把最后 synthetic User 当作用户意图，compact 后 successor 可能只看到“继续执行”，却不知道究竟要执行什么。

---

## 34. Tool Call / Tool Result 完整性修复

Provider 通常要求：

```text
assistant tool_call(id=X)
  后面必须有 tool_result(call_id=X)
```

Compaction replacement 中可能出现 orphan ToolResult。

所以提交前执行：

```text
sanitize_compacted_history
  → strip orphaned ToolResults

validate_compacted_history
  → 再次检查剩余违规
```

如果 sanitize 后仍然不合法，fallback 到最小 compacted history，去掉 recent messages。

这是典型的两级安全策略：

```text
先做局部修复
修复后仍不满足协议 → 降级到更简单、可证明合法的结构
```

---

## 35. Forked Session 的继承前缀难题

mirror/fork child 可能继承父会话完整 prefix。

正常 compact 后，希望继续保留 inherited prefix，再接 child 自己的 summary。

但如果 inherited prefix 本身已经接近窗口，重新 pin 回去会导致：

```text
compact 成功
  → re-pin 巨大 prefix
  → 仍超过阈值
  → 下一轮再次 compact
  → 无限循环
```

`resolve_forked_compacted_history` 会估算 re-pin 后 token：

```text
projected_preserved_reseed_tokens
```

如果仍达到阈值：

- 释放 inherited prefix；
- 使用已覆盖完整 conversation 的 self-contained summary；
- 设置 `prefix_released` sticky；
- 后续不再重新 pin。

如果 replacement 后仍超过 threshold，则设置 `SUPPRESS_STICKY`，阻止 auto compact re-loop。

---

## 36. Compaction 成功前先持久化什么

真正替换 conversation 前，代码会：

```text
persist_compaction_segment(...)
record_compaction_at(prompt_index)
persist_compaction_checkpoint(...)
replace_conversation_for_compaction(...)
```

Checkpoint 内容包括：

```text
checkpoint_id
prompt_index_at_compaction
compacted_history
schema_version
created_at
original_user_info
reread_file_paths
auto_continue metadata
```

同时在 `updates.jsonl` 中记录 `CompactionCheckpoint` marker。

因此恢复时能知道：

- 哪个 prompt index 发生了 compaction；
- compact 后的 authoritative history 在哪里；
- rewind 目标在 compaction 前还是后；
- 是否需要恢复 auto-continue 语义。

---

## 37. 三种 CompactionMode

`CompactionMode`：

```rust
enum CompactionMode {
    Summary,
    Transcript,
    Segments(CompactionDetail),
}
```

### Summary

只保留摘要，不给模型历史指针。默认模式。

### Transcript

摘要后附 `updates.jsonl` 完整原始 transcript 路径。

### Segments

每次 compaction 写一个清洁的：

```text
compaction/segment_NNN.md
compaction/INDEX.md
```

摘要告诉 successor 可用 `read_file` 或 `grep` 精确恢复旧代码片段、错误信息和工具输出。

这三种模式对应三种信息恢复策略：

```text
Summary    → 完全相信摘要
Transcript → 可查原始事件流
Segments   → 可查按压缩周期整理的阅读友好档案
```

---

## 38. Segment Detail Levels

`CompactionDetail`：

```text
none
minimal
balanced
verbose（默认）
```

| Detail | 保留内容 |
| --- | --- |
| none | stats + summary |
| minimal | 每轮一行 tool signature |
| balanced | tool call + 截断 response + 完整文本 |
| verbose | 完整 verbatim turns |

单个 segment verbatim 部分最大：

```rust
SEGMENT_MAX_BYTES = 512 * 1024
```

截断按完整 turn boundary，而不是任意字节切开。

`INDEX.md` 保存：

- segment number；
- filename；
- turn count；
- approximate bytes；
- keywords。

这使旁路历史既可搜索，又不会无限制造一个巨大单文件。

---

## 39. Raw Transcript 与 Working History 的生命周期差异

有三个相关文件/层：

```text
updates.jsonl
chat_history.jsonl
compaction_checkpoints/*.json
```

概念上：

- `updates.jsonl` 是更完整的事件/回放依据；
- `chat_history.jsonl` 镜像当前工作 conversation；
- checkpoint 保存 compact 后可直接装载的结构化历史。

旧 ToolResult 被 retained-memory prune 清除时：

```text
chat_history / in-memory 会失去旧 bulk
updates.jsonl 仍保留原始数据，支持 replay
```

这就是“工作集压缩”和“历史删除”的区别。

---

## 40. Rewind 如何跨越 Compaction

`prompt_index` 是 rewind 的逻辑坐标。

每个真实 prompt turn 的 User item会记录：

```rust
UserItem.prompt_index: Option<usize>
```

`conversation_truncate_for_prompt` 兼容三种历史：

1. 旧数据没有 marker：用 legacy user counting；
2. 新数据有连续 marker：progressive counting；
3. compaction rebuild 后 marker 从高绝对值开始：markers-only。

为什么第三种重要：

```text
compact 后前面重新注入了多个 User-shaped metadata item
它们不是新的真实 turn
若按 User 数量截断，会把 rewind 边界算错
```

Rewind 到 compaction 之前时使用 raw updates replay；目标在 compaction 之后时可从 checkpoint 开始。

同时必须调整 `last_compaction_prompt_index`，否则 `x-compactions-remaining` 等 header 会误以为 session 仍处于已压缩 epoch。

---

## 41. Backend Compaction Headers

某些模型支持请求 header：

```text
x-compaction-at
x-compactions-remaining
```

### `CompactionAtTokens`

配置可为：

```text
false  → 不发送
true   → context_window × threshold_percent / 100
N      → 固定 N
```

### `CompactionsRemaining`

配置可为：

```text
false  → 不发送
true   → 未压缩时 1，已有 summary 时 0
N      → 固定 N
```

动态 `x-compaction-at` 在已经完成一次 compaction 后会被移除。

这些 header 是向 backend 暴露的模型能力提示，不替代 client 自己的 compaction state machine。

---

## 42. Tool Result Pruning 不是 Compaction

在 context 使用超过 50% 时，构建请求 clone 会运行：

```rust
prune_conversation(&mut items, &PruningConfig)
```

默认配置：

```rust
keep_last_n_turns       = 3
soft_trim_threshold     = 4000 chars
soft_trim_head          = 1500 chars
soft_trim_tail          = 1500 chars
hard_clear_age_turns    = 10
```

策略：

```text
最近 3 turns          → 永不裁剪
中等年龄且 >4000 chars → 保留 head + tail
至少 10 turns 之前     → 替换成 omitted placeholder
```

请求 clone 的 soft trim 不修改权威 conversation。

它的目的只是降低下一次 prompt，而不是生成一个 successor summary。

---

## 43. Retained-Memory Pruning

每次 push 新 user message 后，还会运行：

```rust
prune_retained_conversation()
```

它直接修改 actor 中保存的 conversation，但只做 hard clear，不做 soft trim。

目的不同：

```text
request-copy prune     → 控制模型上下文
retained-memory prune  → 控制进程内字符串内存
```

它会考虑 synthetic User 数量：

```text
synthetic_count = total_user_items - prompt_index
effective_threshold = hard_clear_age_turns + synthetic_count
```

否则大量 mid-turn synthetic User 会让旧 ToolResult 看起来比真实年龄更老，导致提前清除。

清除后更新 `chat_history`，但不改 `updates.jsonl`。

---

## 44. Image Budget 是字节预算，不是 Token 预算

内联 base64 图片可能还没耗尽 token window，就先超过 inference proxy 的 HTTP body 限制。

源码中的硬限制：

```text
50 MiB request body
```

`build_conversation_request` 每轮估算精确 serialized body bytes。

接近上限时：

- 从最旧图片开始驱逐；
- 一次回收到 low-water mark；
- 用明确 placeholder 替换；
- 告诉模型图片已经不可见，不能凭记忆描述；
- 要求必要时让用户重新上传。

为什么批量回收而不是每轮只删一张：

> 每次改写旧 prefix 都会破坏 KV-cache；一次释放足够 headroom 可以减少反复 cache bust。

这条机制与 conversation summary compaction 相互独立。

---

## 45. MCP / Shell Tool 输出的源头限流

最好的 context 管理不是等 85% 再 compact，而是在大输出进入 conversation 前就截断。

`TruncationConfig` 支持：

```rust
default_max_output_bytes
per_tool_max_output_bytes
mcp_max_output_bytes
```

优先级通常是：

```text
per-tool override
  > MCP-specific override（MCP 路径）
  > default override
  > built-in fallback
```

大输出常采用 head + tail，中间添加 marker，并给出完整日志文件路径。

这比只保留 head 更适合 shell：

- head 有命令启动信息；
- tail 有最终错误和退出状态。

因此整个系统是分层防御：

```text
tool source truncation
  → old tool-result pruning
  → preflight overflow guard
  → full conversation compaction
```

---

## 46. 单次模型输出上限

普通请求从 `SamplingConfig.max_completion_tokens` 写入：

```rust
ConversationRequest.max_output_tokens
```

它限制的是：

```text
这一次 model call 最多生成多少 completion token
```

不限制：

- prompt input 大小；
- session 累计花费；
- Goal 总预算；
- subagent 数量；
- compaction 次数。

Model metadata 在 resume 或 response header 变化时可更新：

```text
context_window
max_completion_tokens
```

所以 session 不应永久缓存启动时的值；源码在 resume/model response 路径重新同步。

---

## 47. `TaskOutputTokenBudget`

预算型 child 使用共享状态：

```rust
struct TaskOutputTokenBudgetState {
    total: Option<u64>,
    spent: u64,
    incomplete: bool,
}
```

每次模型请求前：

```text
remaining = total - spent
request.max_output_tokens = min(configured_limit, remaining)
```

如果 remaining 为 0，直接返回：

```text
workflow child output-token budget exhausted
```

每次 response：

```rust
record_reported_output(completion_tokens)
```

如果 response 缺 usage 或 sampling 失败导致实际消耗不明：

```text
mark incomplete
exhaust remaining grant
```

这是一种 fail-closed 预算：

> 不知道花了多少时，不能乐观假设花了 0。

同时关闭普通 auto-compact 和 doom-loop recovery，避免预算型 child 通过隐式 side calls 超出授予范围。

---

## 48. Goal Token Budget

Goal 的 `token_budget` 又是另一层。

它限制一个长期目标从起点到当前累计使用的 token，而不是某一次输出。

每次 goal continuation 前：

```rust
let (tokens_used, finished_marginal) = goal_tokens(current_tokens);
if tokens_used >= budget {
    goal_tracker.budget_limit();
}
```

达到预算后：

- Goal 转为 BudgetLimited；
- 清理 pending classifier completions；
- 发送 GoalUpdated；
- 停止自动 continuation；
- 告知用户如何清理并创建新 Goal。

对比：

| 预算 | 控制对象 | 达限动作 |
| --- | --- | --- |
| `max_output_tokens` | 单次 model call | provider 停止生成 |
| `TaskOutputTokenBudget` | child 的累计 completion | child fail closed |
| Goal `token_budget` | 长期目标累计消耗 | Goal BudgetLimited |
| context window | 单次请求 live context | compact / provider error |

---

## 49. Compaction Failure Suppression 状态机

自动 compact 如果永久失败，而每个 agent loop 都重试，会形成昂贵死循环。

状态：

```text
SUPPRESS_NONE          = 0
SUPPRESS_TURN          = 1
SUPPRESS_STICKY        = 2
SUPPRESS_UNTIL_SUCCESS = 3
SUPPRESS_AUTH          = 4
```

原因映射：

| 原因 | 状态 | 清理条件 |
| --- | --- | --- |
| other | TURN | 下一 turn 开始 |
| size | STICKY | compact/rewind/model budget change |
| schema | STICKY | context/config change |
| credit block | UNTIL_SUCCESS | 下一次 model 200 |
| auth | AUTH | login/token refresh |

为什么 auth 不等待下一次 200：

```text
session 已经超窗
auto compact 因 401 失败
若必须等 model 200 才清 suppression
但普通 model request 又因超窗无法成功
形成死锁
```

所以 auth 在 credential recovery 时清理。

---

## 50. Cancel 语义

用户 stop 当前 turn 时，compaction cancel gate 会取消正在进行的 compact/prefire sampling。

自动路径会发送：

```text
AutoCompactCancelled {
    reason: UserCancelled
}
```

关键提交点前再次检查 token：

```rust
if cancel.is_cancelled() {
    return emit_compact_cancelled(...);
}
```

这样不会出现：

```text
用户已经停止
summary 在网络上迟到
系统仍替换 conversation
```

Cancel gate 在 idle 时收到 cancel 是 no-op；下一次 compaction 会得到全新 token，不继承旧取消状态。

---

## 51. 压缩后的 Runtime Reset

Conversation replacement 后还要重置多个 session-level cache：

```text
last_idle_flush_conversation_len = new_len
memory.context_injected = false
TodoState = default
tool_bridge.on_agents_md_compaction()
tool_bridge.on_skill_discovery_compaction()
persist announcement state
plan_mode.reset_after_compaction()
PostCompact hook
```

为什么不简单保留：

- Todo 已经通过新 state reminder/plan 重建；
- memory context 需要在下一 turn 重新判断注入；
- AGENTS.md 与 Skill discovery 的“已提醒”状态要匹配新的 conversation；
- plan mode reminder 必须重新进入 compact 后的 prompt；
- announcement 去重状态需要持久化。

Compaction 是一次 conversation epoch 切换，而不只是文本缩短。

---

## 52. Pre-Compaction Memory Flush

在丢弃旧 working context 前，系统可启动 memory flush。

流程：

```text
increment compaction_count
  → should_flush(total, window, threshold, config, last_flush, count)
  → snapshot memory state
  → spawn_local run_memory_flush
  → success 后记录 last_flush_compaction
```

Memory flush 与 compaction 可并行，避免完全阻塞用户。

设计意图：

```text
短期上下文摘要保存当前任务连续性
长期 memory 保存未来会话仍有价值的偏好/事实
```

两者生命周期不同，不能用 compaction summary 直接代替 memory。

---

## 53. Observability

`session.compact_inner` span 记录：

```text
tokens_before / tokens_after
summary_chars
attempts
degenerate_rejections
input_overflow_rejections
deterministic_rejections
transient_rejections
trigger / trigger_pct / threshold_pct
outcome / stop_reason
ttft_ms / stream_ms / delta_count / itl_max_ms
two_pass_used
prefire_hit / prefire_stale / prefire_waited_ms
pass2_latency_ms
prefix_released
```

Compaction outcome：

```text
success
truncated
deterministic
transient
degenerate
failed
```

这使问题可以被拆成：

- 触发太晚；
- summarizer 太慢；
- 输入超窗；
- summary 退化；
- two-pass cache miss；
- fork prefix 过大；
- provider 输出被截断。

而不是笼统地记录一个 `compact failed`。

---

## 54. Compaction Request Artifact

每次 single-pass compaction 最终会 best-effort 写：

```text
compaction_requests/<request_id>.json
```

内容包括：

- exact chat history sent；
- tools；
- model；
- trigger；
- prompt variant；
- user context；
- summary 或 rejected summary；
- error；
- attempt count/details；
- created_at。

Artifact 用于离线 prompt 调优和故障分析。

它的写入失败不会让用户 compaction 失败，因为它不是 correctness path。

这种区分很值得学习：

```text
checkpoint persistence → correctness relevant
request artifact       → observability best effort
```

---

## 55. Auto-Continue 与 Turn Capture

旧实现和 replay 数据中存在 auto-continue metadata：compact 后可自动注入：

```text
Continue the conversation from where it left off...
```

当前 `run_compact_only` 的注释说明：

```text
Compact without auto-continue. The outer turn loop rebuilds and retries.
```

也就是 auto compaction 发生在 agentic loop 内时，原 loop `continue` 并重建 request，不一定需要额外模拟一个真实用户 prompt。

Turn capture 仍会标记：

```rust
compaction_occurred = true
```

如果 conversation 在当前 turn 中被替换，capture 会先保存 replacement 前属于本 turn 的 tail，再把 offset rebase 到新 vector，保证 turn artifact 不丢消息。

---

## 56. Subagent Fork Context 的独立压缩

父 Agent fork context 给 child 时，不会无界复制全部历史。

`xai-grok-subagent-resolution/src/context.rs` 的策略是：

```text
最近 3 个 turns verbatim
更早 turns 只生成结构化摘要
```

早期摘要主要记录：

- user/assistant message counts；
- files mentioned；
- tools used；
- tool call arguments preview；
- tool result preview。

典型上限：

```text
tool arguments preview ≈ 100 chars
tool result preview    ≈ 200 chars
```

并且 Unicode char-safe。

这与 session compaction 不同：

| 机制 | 是否调用 LLM | 目的 |
| --- | --- | --- |
| session compaction | 是 | 替换长期主会话工作上下文 |
| fork context normalization | 否，确定性摘要 | 给新 child 一份有界父背景 |

因此创建 subagent 不会把父 session 的完整 token 历史复制一遍。

---

## 57. Shared Compaction Crate 的分层

`xai-grok-compaction` 逐步把通用算法从 shell 中抽离。

### L1：Item traits

`CompactionItem` / `CompactionItemFactory` 抽象不同 harness 的消息类型。

### Full Replace

```text
build prompt
  → sample with retries
  → validate/clean
  → assemble replacement
```

### Intra Compaction

支持：

```text
FullReplace
StepsOnly
HistoryOnly
HistoryThenSteps
```

以及 target selection、input fitting 和 reduction 检查。

### Inter Compaction

把长历史分 chunk，总结每一块并组合，支持 recompaction user-query preservation。

Grok Build shell 当前主路径仍保留许多 L5 host concern：

- trigger；
- persistence；
- suppression；
- state reminder；
- input ladder；
- transport；
- two-pass prefire；
- fork prefix。

共享 crate 不直接访问 shell actor，这是合理的依赖方向。

---

## 58. 为什么不能把整个 Compaction 都下沉到公共库

公共算法可以决定：

- 哪些 turns 适合 compact；
- 怎样构造 prompt；
- 怎样重试；
- 怎样组装 replacement。

但它不知道：

- 当前有哪些 background terminal tasks；
- 哪些 subagent 仍在运行；
- MCP server 的真实名称；
- AGENTS.md/Skill discovery 状态；
- plan mode 文件；
- session checkpoint 目录；
- auth token 如何刷新；
- ACP notification 如何发送。

所以正确边界是：

```text
shared crate = transport-agnostic compaction algorithm
shell host    = session lifecycle and side-effect orchestration
```

`ShellCompactionSampler` 和 `ShellFullReplaceObserver` 正是两层之间的 adapter。

---

## 59. 完整时序图

```text
User / Tool loop
    │
    ▼
ChatStateActor append item
    │
    ├─ provider total remains T
    └─ local delta becomes Δ
    │
    ▼
estimated_total = T + Δ
    │
    ├─ below prefire line ───────────────────────────────┐
    │                                                    │
    ├─ above prefire line                               │
    │     └─ background pass1(prefix 95%) → NOTE₁ cache │
    │                                                    │
    ├─ above auto threshold                             │
    │     └─ run_compact_only                           │
    │            │                                       │
    │            ├─ valid NOTE₁ → pass2(tail 5%)        │
    │            └─ no NOTE₁ → full single pass         │
    │                         │                          │
    │                         ├─ transient → retry ×3    │
    │                         └─ overflow                │
    │                              verbatim              │
    │                                ↓                   │
    │                              fitted                │
    │                                ↓                   │
    │                              lossy                 │
    │                                                    │
    ▼                                                    │
clean summary + collect runtime truth                    │
    │                                                    │
    ▼                                                    │
build/sanitize compacted history                         │
    │                                                    │
    ▼                                                    │
persist segment/checkpoint                               │
    │                                                    │
    ▼                                                    │
replace_conversation_for_compaction                      │
    │                                                    │
    ├─ reseed provider overhead ratio                    │
    ├─ reset skill/AGENTS/memory/plan state              │
    └─ outer loop rebuilds next request ─────────────────┘
```

---

## 60. 一次数字化示例

假设：

```text
context window = 128,000
auto threshold = 85%
prefire lead   = 10%
```

阈值约为：

```text
prefire ≈ 96,000 tokens  (75%)
compact ≈ 108,800 tokens (85%)
hard window = 128,000
```

过程：

```text
1. provider 最新报告 total = 95,000
2. tool result 本地估算 +2,000
3. estimated_total = 97,000
4. 后台 pass1 开始总结约 95% prefix
5. Agent 再执行工具，本地 delta 增至 14,000
6. estimated_total = 109,000，触发 compact
7. pass1 已完成，pass2 只读取 NOTE₁ + tail
8. compacted history 本地估算 = 12,000
9. 压缩前 provider/local ratio = 109,000 / 100,000 = 1.09
10. reseed live total ≈ 13,080
11. 下一轮 provider 返回精确 total，重新校准
```

如果 pass1 prefix 在第 4、6 步之间被 edit，fingerprint 不一致，则第 7 步放弃 cache，走 single-pass。

---

## 61. 常见故障与源码诊断路径

### 61.1 每轮都触发 compact

检查：

```text
post_replace_tokens
prefix_released
auto_compact_suppressed
inherited_prefix_len
```

可能是 fork prefix 重新 pin 后仍超阈值。

### 61.2 Compact 200 但信息全部丢失

检查：

```text
degenerate_rejections
summary_chars
compaction request artifact
stop_reason
truncated
```

### 61.3 工具后 provider 400 context length

检查：

```text
record_token_usage 是否执行
estimated_tokens_since_model 是否更新
check_preflight_overflow 是否被 task budget 路径跳过
tool output truncation config
```

### 61.4 Two-pass 没有降低延迟

检查：

```text
prefire_hit
prefire_stale
prefire_waited_ms
pass1_latency_ms
pass2_latency_ms
GROK_PREFIRE_LEAD_PERCENT
```

### 61.5 压缩后 AGENTS.md/Skills 重复

检查：

```text
ProjectInstructions synthetic reason
agents_md_reminded_paths
on_agents_md_compaction
on_skill_discovery_compaction
fork inherited prefix 去重
```

### 61.6 Rewind 跨 compact 后位置错误

检查：

```text
UserItem.prompt_index
last_compaction_prompt_index
checkpoint marker
legacy/progressive/markers-only truncation mode
```

---

## 62. 设计上最值得学习的十点

### 62.1 精确值与估算值并存

Provider usage 是权威 checkpoint，本地估算填补响应间空档。

### 62.2 先在源头限制大输出

Tool truncation 比事后总结更便宜、更稳定。

### 62.3 错误恢复必须区分同请求重试和输入降级

Context overflow 对相同 payload 重试没有意义。

### 62.4 摘要只保存语义，运行状态重新查询

避免把 task ID、MCP、Todo 等权威状态托付给概率模型。

### 62.5 Replacement 必须保持协议结构

Tool call/result 完整性比摘要内容更基础。

### 62.6 压缩后仍保留可检索的原始历史

Summary 不可能完美，transcript/segments 是纠错通道。

### 62.7 Prefire 用 fingerprint 绑定 snapshot

异步优化不能牺牲 correctness。

### 62.8 Fail-closed budget

Usage 不明时耗尽 grant，而不是假设免费。

### 62.9 Suppression 必须按失败原因设生命周期

认证、额度、schema、size 的自愈条件完全不同。

### 62.10 Compaction 是 epoch transition

消息、token seed、memory、skill、plan、rewind marker 都必须一起切换。

---

## 63. 当前局限与可继续研究的方向

### 63.1 bytes/4 估算仍然粗糙

可以研究按 model family 使用轻量 tokenizer，比较 CPU 成本与 preflight accuracy。

### 63.2 Summary 质量只有最低长度门槛

可加入结构 section completeness、pending-task preservation 或事实一致性 evaluator。

### 63.3 多次 recompaction 的信息衰减

Prompt 要求继承 prior summary，但仍可能逐代压缩丢失。可研究结构化 state capsule 与自然语言 summary 双轨保存。

### 63.4 Two-pass 是固定 95/5

可根据 prompt cache、tail tool density、模型 prefill latency 动态选 split。

### 63.5 Segment store 需要主动检索

当前 successor 需自己决定 read/grep。可以研究基于 query 的自动 segment retrieval，但要避免重新注入过量历史。

### 63.6 Fork prefix 运行时释放

源码注释已经指出，fork admission 时就限制 inherited prefix 是更结构化的方案。

### 63.7 Context 与费用优化目标不一致

压缩降低 live context，却增加一个额外模型调用。可研究按 cache pricing、summary cost、未来剩余 turns 做经济性决策。

### 63.8 Tool schema 本身可能很大

Search-tool/lazy MCP 已减少动态 tools，但基础 tools 仍占 prompt。可以把 compaction sampling 的 tool set 进一步最小化。

---

## 64. 推荐源码阅读顺序

第一次阅读：

```text
1. xai-chat-state/src/actor/state.rs
2. xai-chat-state/src/actor/mutations.rs
3. xai-grok-shell/src/session/acp_session_impl/turn.rs
4. xai-grok-shell/src/session/compaction.rs
```

理解摘要输入与输出：

```text
5. xai-chat-state/src/compaction_utils.rs
6. xai-grok-shell/src/session/helpers/session_compact.rs
7. xai-grok-compaction/src/code_compaction/sample.rs
8. xai-grok-compaction/src/code_compaction/summary.rs
```

理解优化和恢复：

```text
9.  xai-grok-shell/src/session/two_pass.rs
10. xai-grok-shell/src/session/compaction_segments.rs
11. xai-grok-shell/src/session/helpers/replay.rs
12. xai-grok-shell/src/session/acp_session_impl/rewind.rs
```

理解外围预算：

```text
13. xai-chat-state/src/usage.rs
14. xai-grok-shell/src/tools/tool_context.rs
15. xai-chat-state/src/actor/request_builder.rs
16. xai-grok-subagent-resolution/src/context.rs
```

---

## 65. 总结

Grok Build 的长会话能力可以归纳为：

```text
provider token checkpoint
  + local between-response estimate
  + source-level tool/image truncation
  + threshold/preflight/error/model-switch triggers
  + verbatim→fitted→lossy input ladder
  + bounded-retry LLM summary
  + two-pass speculative prefire
  + deterministic runtime-state reinjection
  + atomic conversation replacement
  + calibrated token reseed
  + checkpoints/transcripts/segments
  + rewind/fork-aware lifecycle
  = 可持续的长会话 Agent
```

最关键的结论有三个。

第一：

> Token accounting 不是一个计数器，而是 current context、response delta、billing ledger、output grant 和 long-running goal budget 的组合协议。

第二：

> Compaction 不是“删除旧消息并加摘要”，而是一次带持久化、结构校验、状态重注入和 token 重新定标的 conversation epoch transition。

第三：

> 一个可靠的长会话系统不能把所有记忆都寄托在摘要模型上；语义可以总结，权威运行状态必须重新查询，原始历史必须保留可恢复通道。
