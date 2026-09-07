# 25. Token 预算、压缩与性能

## 1. Token 管理不是只看一个数字

Codex 同时维护几类边界：

```text
模型完整上下文窗口       硬上限
自动压缩作用域与阈值     何时主动开新窗口
工具输出截断上限         防止单次结果占满上下文
Token budget 提醒阈值    提醒模型收束工作
fallback buffer          给最后一次收尾请求预留空间
```

把它们混成一个 `max_tokens` 会丢失语义，也很难解释为何压缩或截断发生。

## 2. 有效上下文窗口

`TurnContext::model_context_window` 不是简单返回 `ModelInfo.context_window`，还应用：

```rust
resolved_context_window * effective_context_window_percent / 100
```

例如元数据窗口为 272k、有效比例 95%，Agent 实际规划会保守地少用一部分，为响应封装、估算误差和服务端差异留余量。

用户覆盖窗口时，`with_config_overrides` 还会用 `max_context_window` 限制，防止配置声称模型具备不存在的容量。

## 3. ContextWindowTokenStatus

`codex-rs/core/src/session/context_window.rs` 集中计算：

- `active_context_tokens`：当前完整活动上下文；
- `auto_compact_scope_tokens`：计入自动压缩阈值的部分；
- `auto_compact_scope_limit`；
- `full_context_window_limit`；
- `base_window_tokens_remaining`；
- 是否触及完整窗口或压缩阈值。

统一计算状态很重要：Prompt 提醒、自动压缩、UI 状态和错误处理应共享同一个事实来源。

## 4. 两种自动压缩作用域

`AutoCompactTokenLimitScope` 支持：

- `Total`：整个活动上下文都计入阈值；
- `BodyAfterPrefix`：只计算初始前缀之后新增的 Token。

`BodyAfterPrefix` 会从 Session 的 auto-compact window snapshot 读取 `prefill_input_tokens` 作为 baseline：

```text
scope_tokens = active_context_tokens - prefill_input_tokens
```

这对具有很大稳定前缀的 Agent 很有价值。固定 system/developer 内容本来就可能被服务端缓存，不应过早挤压每轮可用的“正文预算”。不过完整模型窗口仍是硬上限，不能因作用域设置而绕过。

## 5. 剩余空间取最小值

`base_window_tokens_remaining` 同时计算：

- 自动压缩作用域剩余；
- 完整上下文窗口剩余。

最终取两者最小值。也就是说，软策略可以比硬窗口更早触发，但绝不能比硬窗口更晚。

## 6. Token Budget Feature

`codex-rs/core/src/session/token_budget.rs` 管理一组模型可拥有默认值、用户也可显式覆盖的策略：

- `reminder_threshold_tokens`；
- `reminder_message_template`；
- `guidance_message`；
- `auto_compact_fallback_prompt`；
- `auto_compact_fallback_buffer_tokens`。

`apply_model_defaults` 只在 Feature 已启用且用户没有显式设置时应用模型默认值。这条优先级很合理：

```text
用户明确策略 > 模型建议策略 > 无策略
```

模型提供的默认值还会先 `validate()`；无效远端元数据只记录警告，不覆盖有效本地配置。

## 7. 提醒只发送一次

`maybe_record` 在剩余 Token 低于阈值时调用 Session state 的 `claim_token_budget_reminder()`。只有成功 claim 的调用才把 `TokenBudgetReminder` 作为 `ContextualUserFragment` 写入 History。

这防止循环中的每个 Step 重复注入同一提醒。提醒进入 History 而不只是日志，是为了让模型真正看到并调整行为，例如减少探索、优先验证和总结。

## 8. fallback buffer 的意义

如果配置了 `auto_compact_fallback_prompt`，上下文状态会把 buffer 加到自动压缩触发点：

```text
buffered_limit = auto_compact_scope_limit + fallback_buffer_tokens
```

到 base window 剩余为 0 时，系统可注入一次 fallback prompt，请模型在预留空间内完成收尾。没有 fallback prompt 时不预留 buffer，避免无意义浪费。

## 9. Token-budget compaction 与摘要 compaction

`codex-rs/core/src/compact_token_budget.rs` 的注释明确指出：Token-budget compaction 不调用模型或服务端生成摘要，而是直接建立新的 Context Window。

但它仍走标准压缩生命周期：

1. `run_pre_compact_hooks`；
2. 发出 `ContextCompaction` TurnItem started；
3. `start_new_context_window`；
4. 发出 completed；
5. `run_post_compact_hooks`。

这样 Hook、UI、rollout 和 telemetry 无需理解多套“换窗口”协议。

手动压缩和 inline 自动压缩分别由 `run_manual_compact_task`、`run_inline_auto_compact_task` 进入同一个内部函数。

## 10. 新窗口不是空窗口

开始新窗口时，系统仍需要恢复继续工作所需的最小状态，例如：

- 当前 world state；
- 有界的全局/项目指令；
- 当前任务目标；
- 必要的上下文注入。

因此“压缩”本质上是重新选择上下文，不是简单清空消息。相关背景可结合 [第 11 章](11-history-compaction-and-cache.md) 阅读。

## 11. 工具输出必须先截断

模型元数据带 `truncation_policy`，配置可通过 `tool_output_token_limit` 覆盖。模型若采用字节截断，Token 上限会通过 `approx_bytes_for_tokens` 转成近似字节值。

所有工具，尤其是 shell、日志、搜索和文件读取，都可能产生远超上下文容量的输出。健壮 Agent 应：

- 在进入 History 前截断；
- 保留开头/结尾或按工具语义选择重要部分；
- 告诉模型结果已截断；
- 必要时把完整输出落盘并返回路径；
- 为分页和后续读取提供接口。

截断应靠强制策略，而不是提示模型“请少输出”。

## 12. Cache 友好的上下文构造

性能不仅取决于 Token 数量，也取决于稳定前缀是否可缓存。Codex 的上下文原则包括：

- History 增量追加，不频繁重写旧内容；
- 稳定的 base instructions 放在前面；
- 动态 world state 和用户输入放在后面；
- 每个注入片段有硬上限；
- 不因小变化重新排列整个工具集合或 Prompt。

对自己的 Agent，最好把 Prompt 分成“稳定前缀”和“动态后缀”，并让序列化顺序保持确定。

## 13. 性能分解：不要只看总耗时

`codex-rs/core/src/turn_timing.rs::TurnTimingState` 跟踪：

- TTFT：time to first token；
- TTFM：time to first agent message；
- sampling 总时间与请求次数；
- sampling retry 次数；
- compaction 时间；
- tool blocking 时间；
- 多次 sampling 之间的 orchestration overhead；
- 首次 sampling 前的准备时间。

内部用 `TurnProfileTimingGuard` 的 RAII Drop 结束阶段计时，因此即使函数提前返回，也不容易漏记结束点。

## 14. 如何读性能数据

```text
TTFT 高
  -> 网络、认证、模型排队、请求体过大、连接建立

首次 sampling 前耗时高
  -> MCP 初始化、Skill 扫描、world state、Prompt 构造

sampling 时间高
  -> 模型生成或重试

tool_blocking 高
  -> shell/搜索/MCP/approval/远程 executor

between_sampling_overhead 高
  -> 工具结果转换、History 更新、下一 Step 构造

compaction 高
  -> 摘要请求、上下文重建或 Hook
```

只有这种阶段化数据才能回答“Agent 慢在哪里”。总 Turn duration 只能说明慢，不能指导优化。

## 15. 一次接近窗口上限的执行

假设当前活动上下文为 90k，自动压缩基础阈值为 92k，fallback buffer 为 4k，完整有效窗口为 100k。

1. 剩余基础预算为 `min(2k, 10k) = 2k`；
2. 如果提醒阈值为 5k，注入一次 TokenBudgetReminder；
3. Agent 继续执行工具，活动上下文增至 92k；
4. 基础剩余为 0，系统可注入一次 fallback prompt；
5. buffer 允许流程继续到 96k 左右；
6. 随后触发新 Context Window；
7. 即使 soft limit 配置错误，达到 100k 完整窗口时仍强制触发。

实际计算会使用 saturating arithmetic，避免负数或整数越界导致剩余预算反向增长。

## 16. 优化优先级

建议按以下顺序优化 Agent：

1. 给所有外部输入和工具输出设置硬上限；
2. 避免重复注入相同长文本；
3. 让稳定 Prompt 前缀保持顺序和内容稳定；
4. 按阶段测量 TTFT、sampling、tool blocking；
5. 对昂贵发现过程使用有版本和 TTL 的缓存；
6. 并行执行彼此独立的只读工作；
7. 为接近窗口上限设计提醒和收尾路径；
8. 压缩后恢复最少但充分的任务状态。

不要一开始就压缩所有 Prompt 文案。相比删掉几百 Token，消除一次重复工具调用、无界日志或不必要的模型 round trip 通常收益更大。

## 17. 相关源码

- `codex-rs/core/src/session/context_window.rs`：窗口状态计算；
- `codex-rs/core/src/session/token_budget.rs`：模型默认、提醒和 fallback；
- `codex-rs/core/src/compact_token_budget.rs`：新窗口生命周期；
- `codex-rs/core/src/turn_timing.rs`：性能阶段与 TTFT/TTFM；
- `codex-rs/models-manager/src/model_info.rs`：上下文与工具截断覆盖；
- `codex-rs/core/src/context/`：有界上下文片段类型。

