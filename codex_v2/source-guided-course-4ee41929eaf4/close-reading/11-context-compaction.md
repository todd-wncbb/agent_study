# Context compaction：长历史怎样变成可继续运行的新上下文

> 源码基线：`4ee41929eaf4`  
> 本篇重点：自动触发、手动触发、本地摘要、远端 v1/v2、replacement history、工具调用配对、初始上下文重注入、持久化与恢复。  
> 建议先读：[Turn 主循环](04-turn-main-loop.md)和[Rollout resume](09-rollout-resume.md)。

## 1. 先说人话：compaction 不是把聊天记录压成 zip

一个 Codex 任务可能持续很久。期间会不断产生：

- 用户要求；
- Codex 的回复；
- reasoning item；
- shell、MCP、文件修改等工具调用；
- 工具返回的大段输出；
- cwd、权限、模型等运行上下文；
- world state 的完整快照和变化。

模型的 context window 有上限，所以这些内容不能无限累积。

compaction 的目标可以先理解为：

> 根据旧历史生成一份更短、但仍足够继续工作的“新模型上下文”，然后让它整体取代旧模型上下文。

它不是：

- 压缩磁盘上的 JSONL 文件；
- 删除 UI 里所有旧消息；
- 单纯截掉最早的若干字符；
- 把所有旧 `ResponseItem` 原样保留下来，只压缩其中的文本；
- 保证模型对每个细节都像压缩前一样精确。

源码甚至会在本地 compaction 成功后提示：很长的 thread 和多次 compaction 可能降低准确度，应尽量保持任务小而聚焦。

## 2. 官方公开边界与本篇源码边界

[OpenAI 官方 Compaction 文档](https://developers.openai.com/api/docs/guides/compaction)给出的公开概念是：compaction 用更少的 token 携带后续 Turn 所需的关键状态；standalone compact endpoint 返回的是下一次请求应当使用的 canonical compacted window，而且结果通常不只包含一个 compaction item。

公开 API 当前还支持 server-side compaction。但本篇不是教你调用最新 API，而是精读固定提交 `4ee41929eaf4` 中 Codex 如何：

1. 判断何时需要压缩；
2. 在本地摘要、远端 v1、远端 v2 之间选路；
3. 构造并安装 replacement history；
4. 把 checkpoint 写入 rollout；
5. 在 resume/fork 时重建相同的模型历史。

因此：公开行为以官方文档为参照，内部类型和顺序以固定提交源码为准。

## 3. 贯穿案例：一场越来越长的数据库迁移

假设用户让 Codex 完成数据库迁移：

```text
Turn 1：阅读 schema，制定迁移计划
Turn 2：搜索 40 个调用点
Turn 3：运行测试，得到 20,000 行日志
Turn 4：修改代码，再运行测试
Turn 5：模型调用工具后还想继续修复
```

压缩前的模型历史可以抽象为：

```text
[初始开发者指令]
[用户：做数据库迁移]
[assistant reasoning]
[function call: 搜索调用点]
[function output: 大量搜索结果]
[assistant：迁移计划]
[用户：继续实现]
[shell call]
[shell output: 20,000 行测试日志]
[assistant：发现两个失败]
[用户：继续修复]
```

压缩后可能更像：

```text
[近期真实用户要求]
[摘要：目标、已完成修改、未解决失败、重要文件和约束]
```

或者在远端 v2 路线中：

```text
[被允许保留的一部分近期消息]
[opaque compaction item]
```

旧工具日志不必逐字进入新窗口，但摘要必须尽可能保留“测试仍有两个失败”这种后续决策所需事实。

## 4. 先分清五种“历史”

### 4.1 UI 历史

用户看到的 Turn、消息和工具卡片。compaction 不等于把 UI 记录清空。

### 4.2 Rollout 历史

磁盘上的 append-only 事实流。旧记录仍然存在；compaction 会追加一个 checkpoint。

### 4.3 `ContextManager` 中的 live history

当前 Session 真正维护的模型历史。compaction 会整体替换它。

### 4.4 Prompt history

调用 `ContextManager::for_prompt` 后得到的、已经规范化并适配模型模态的请求输入。

### 4.5 Replacement history

compaction 产出的新 live history。它会写进 `CompactedItem.replacement_history`，供未来 resume/fork 重建。

最重要的关系是：

```text
旧 rollout 仍在
     │
     ├─ UI 可以继续展示旧事实
     │
     └─ 最新 CompactedItem.replacement_history
                         │
                         ▼
                 当前模型上下文起点
```

## 5. 两种触发者：Manual 与 Auto

源码用 `CompactionTrigger` 区分：

- `Manual`：用户或客户端显式请求 compact；
- `Auto`：Codex 因上下文预算、模型切换等原因自动执行。

手动入口最终由 `CompactTask` 执行。它是一个独立 Session task，因此会产生自己的 `TurnStarted` 和 compaction 生命周期。

自动入口嵌在普通 Turn 主循环中，不一定创建一个完全独立的用户工作流。

## 6. 自动压缩有三个主要原因

固定提交中的 `CompactionReason` 包括：

| reason | 白话解释 |
|---|---|
| `ContextLimit` | 当前上下文达到自动压缩阈值或模型硬上限 |
| `ModelDownshift` | 切换到 context window 更小的模型，旧历史放不下 |
| `CompHashChanged` | 前后模型都声明了 compaction compatibility hash，且 hash 不同 |
| `UserRequested` | 用户手动要求压缩 |

`comp_hash` 可以理解成“这两个模型是否能安全沿用同一压缩语义”的兼容标识。只有前后两边都提供 hash 且不同，才触发该原因；缺少 hash 不能被当成“不兼容”。

## 7. Token 状态不是只看一个数字

`context_window_token_status` 同时计算：

- `active_context_tokens`：当前完整活跃上下文用量；
- `auto_compact_scope_tokens`：用于自动压缩判断的作用域用量；
- `auto_compact_scope_limit`：配置或模型给出的自动压缩阈值；
- `full_context_window_limit`：模型的硬 context window；
- `base_window_tokens_remaining`：两个上限中更紧的剩余额度；
- `token_limit_reached`：是否应该压缩。

所以“模型窗口是 200k”并不表示一定等到 200k 才压缩。系统可以设置更早的自动压缩阈值，为后续推理和输出留空间。

## 8. `Total` 与 `BodyAfterPrefix` 两种计数作用域

`AutoCompactTokenLimitScope` 决定阈值怎样计算。

### `Total`

```text
用于判断的 token = 整个 active context
```

初始指令、工具定义、历史正文都算在内。

### `BodyAfterPrefix`

```text
用于判断的 token = active context - 当前窗口的 prefill baseline
```

它更关心“建立新窗口以后又增长了多少”，不会让每次都必须携带的固定前缀过早吃完增长预算。

但模型完整 context window 仍是硬上限。即使 body 增长没达到软阈值，只要完整上下文到顶，也必须压缩。

## 9. `AutoCompactWindow` 记录的不是聊天窗口

它记录一次 compaction window 的身份和计数基线：

```rust
window_number
first_window_id
previous_window_id
window_id
prefill_input_tokens
```

其中：

- `window_number` 单调递增；
- `first_window_id` 标识整条窗口链的起点；
- `previous_window_id` 指向上一窗口；
- `window_id` 是当前窗口的 UUIDv7；
- `prefill_input_tokens` 是 `BodyAfterPrefix` 模式的绝对基线。

这些 ID 不是模型 `response_id`，也不是 thread id。它们描述的是同一 thread 内上下文窗口的血缘。

## 10. Prefill 为什么既可以 Estimated，也可以 ServerObserved

恢复 Session 或本地重算时，Codex 可能先估计前缀 token 数，因此记录 `Estimated`。

真正拿到服务器 usage 后，可以用 `ServerObserved` 替换估计值。已有 server-observed 值不会再被估计值覆盖。

白话理解：

> 没有秤时先目测；真正称过以后，以秤的结果为准。

## 11. 自动压缩可能发生在 Turn 开始前

`run_pre_sampling_compact` 在普通 sampling step 创建之前检查：

1. 上一个模型是否需要先完成兼容性压缩；
2. 当前 token limit 是否已经达到。

如果需要，就捕获一个 `StepContext`，执行 pre-turn compaction，然后才进入正常采样。

这种路线使用：

```text
InitialContextInjection::DoNotInject
```

原因稍后解释。

## 12. 自动压缩也可能发生在 Turn 中途

一次模型响应可能只是请求工具调用。工具执行并回填后，模型还需要 follow-up sampling。

主循环在一次 sampling 结束后计算：

```text
needs_follow_up = 模型需要继续 || 用户又 steer 了输入
```

只有还要继续时，达到阈值才会在同一 Turn 中途 roll over：

```text
sampling
  → tool call
  → tool output
  → token limit reached
  → mid-turn compaction
  → follow-up sampling
```

这解释了为什么 compaction 不能只当成“两个 Turn 之间的维护任务”。

## 13. 为什么没有 follow-up 时不急着立刻压缩

如果当前 Turn 已经结束，就没有马上再发一次模型请求。此刻即使历史很长，也可以等下一次用户输入到来后，在 pre-turn 阶段处理。

这样可以避免在用户可能不再继续该 thread 时，额外执行一次昂贵 compaction。

## 14. 模型切换为何优先让旧模型压缩

切换模型时，旧历史是由旧模型产生的。源码会构造 previous-model `TurnContext`，优先让旧模型完成压缩。

如果这是 OpenAI Codex backend、provider 合适、当前模型不同，并且错误属于可能与模型相关的类别，系统可以再用当前模型重试。

可触发 fallback 的错误包括：

- invalid request；
- unexpected status；
- context window exceeded；
- usage limit；
- server overloaded；
- internal server error；
- retry limit。

但取消或明确不可重试的错误不会随意换模型重跑。

## 15. `run_auto_compact` 是路线选择器

它先检查 TokenBudget feature；否则读取 provider capability：

```text
TokenBudget enabled
  → 新建 context window，不调用摘要模型

RemoteCompactionSupport::V2 + feature enabled
  → remote compaction v2

RemoteCompactionSupport::V1 或未启用 v2
  → remote compact v1

RemoteCompactionSupport::Unsupported
  → local summarization
```

所以“Codex 的 compaction 算法”并不是一个函数，而是一层共同生命周期加多种实现。

## 16. 四种实现的核心差异

| 实现 | 谁生成压缩结果 | 新历史大致是什么 |
|---|---|---|
| TokenBudget | 不调用摘要模型 | 新的 canonical initial context |
| Local | 普通 Responses 推理生成文字摘要 | 近期真实用户消息 + summary user message |
| Remote v1 | 专门 compact endpoint | endpoint 返回并经本地过滤的 compacted transcript |
| Remote v2 | Responses stream 返回 compaction item | 本地保留的消息 + opaque compaction item |

共同点是最后都会走向 `Session::replace_compacted_history`。

## 17. PreCompact 与 PostCompact Hook

三条主要路线都会运行：

```text
pre-compact hooks
      │
      ▼
执行 compaction
      │ 成功
      ▼
post-compact hooks
```

Hook 可以让扩展观察或中止生命周期。`TurnAborted` 会作为明确的中断向上传递。

注意：某些 hook action 在该事件类型上可能不支持，不能仅凭配置里写了 `block` 就断言一定阻止压缩；应看 hook runtime 最终投影成 `Continue` 还是 `Stopped`。

## 18. UI 生命周期与历史替换是两件事

开始压缩时，系统创建：

```text
TurnItem::ContextCompaction(ContextCompactionItem)
```

然后发出 started 事件；成功安装新历史后再发 completed 事件。

这个 UI item 用于告诉客户端“正在压缩/压缩完成”，不等于模型历史里的 `ResponseItem::Compaction`，也不等于 rollout checkpoint `CompactedItem`。

三个同名概念要分开：

| 对象 | 用途 |
|---|---|
| `ContextCompactionItem` | UI/Turn 生命周期 |
| `ResponseItem::Compaction` | 模型/API 上下文中的 opaque item |
| `CompactedItem` | rollout 中的 durable replacement checkpoint |

## 19. Local 路线先把摘要要求当成一条合成用户输入

本地自动压缩使用默认 `SUMMARIZATION_PROMPT`，也允许配置 `compact_prompt`。

它构造 `UserInput::Text`，但这不是用户真的输入了一条聊天消息，而是系统为了让模型总结历史合成的 prompt。

手动 local compact 同样会构造该输入，并作为一个独立 compact Turn 执行。

## 20. Local 路线为什么克隆历史

它先：

```rust
let mut history = sess.clone_history().await;
history.record_items(&[summary_prompt], policy);
```

这个临时 `history` 用来向摘要模型发请求。此时 live Session history 还没有被替换。

好处是：摘要调用失败时，不会把“半成品新历史”提前安装进 Session。

## 21. `for_prompt` 在发送摘要请求前做什么

`ContextManager::for_prompt` 会规范化克隆出来的历史：

1. 给缺少结果的调用补 synthetic output；
2. 删除找不到对应调用的 orphan output；
3. 模型不支持 image 时移除或替换 image 内容；
4. 模型不支持 audio 时移除或替换 audio 内容。

因此 raw history 和真正发给模型的 prompt history 可能不同。

## 22. 为什么工具调用和结果必须配对

模型协议中的工具交互通常是：

```text
FunctionCall(call_id = abc)
FunctionCallOutput(call_id = abc)
```

如果只剩 Call：模型会看到一个永远没有结束的动作。

如果只剩 Output：模型不知道是谁产生了这个结果。

这不只是“读起来奇怪”，还可能让上游 API 拒绝输入或让模型错误理解当前执行状态。

## 23. 缺少 output 时为什么补 `aborted`

`ensure_call_outputs_present` 会扫描 function、tool search、custom tool 和 local shell call。

普通 function call 缺结果时，会紧跟在 call 后插入 synthetic output：

```text
output = "aborted"
```

这不是伪造“工具成功”，而是把不完整调用封口，明确告诉模型它没有正常结果。

synthetic output ID 基于源 item ID 和固定 UUID namespace 派生，所以多次 prompt normalization 可以得到稳定 ID，有利于 prompt cache 复用。

## 24. Orphan output 为什么直接删除

`remove_orphan_outputs` 收集所有可匹配的 `call_id`，随后移除没有来源的 client-side output。

server-executed tool search output 是一个特殊情况，可以没有本地 call pair，因此不会一概删除。

这里体现一个原则：

> 无法证明 output 属于哪次调用时，宁可不把它发给模型。

## 25. Local compaction 请求超过窗口时怎样自救

即使“正在压缩”，输入本身仍可能已经大到放不进模型。

本地路线收到 `ContextWindowExceeded` 后，如果临时输入还有多个 item，就删除最老 item，清零重试计数，再试一次。

源码注释说从开头删是为了：

- 保留最近消息；
- 尽量维护 prefix-based cache。

如果只剩一个 item 仍超限，则标记 token full、发错误并失败。

## 26. 删除最老 item 时如何避免拆散工具对

`ContextManager::remove_first_item` 不只做：

```text
remove(items[0])
```

它还调用 `normalize::remove_corresponding_for`：

- 删 FunctionCall 时，也删匹配的 FunctionCallOutput；
- 删 Output 时，也删匹配的 FunctionCall 或 LocalShellCall；
- ToolSearch 和 CustomTool 同理。

所以溢出裁剪不会因为只删了一边而制造半个工具交互。

## 27. Local 摘要流为什么只关心 `OutputItemDone` 与 `Completed`

`drain_to_completed` 消费 Responses stream：

- `OutputItemDone`：记录模型生成的 item；
- `ServerReasoningIncluded`：更新能力状态；
- `RateLimits`：更新限流快照；
- `Completed`：记录 response id、usage，并结束；
- 其他增量事件：继续等待。

如果 stream 在 `response.completed` 前关闭，就不能把摘要当成完整结果。

## 28. Local replacement history 从哪里来

摘要调用完成后，源码从 live Session history 取得：

1. 当前 compact Turn 最后的 assistant message，作为 `summary_suffix`；
2. 历史中的真实用户消息；
3. `SUMMARY_PREFIX`。

随后构造：

```text
[最多约 20,000 tokens 的近期真实用户消息]
[role=user, text=SUMMARY_PREFIX + summary_suffix]
```

这里的 summary 被编码成 user message，是固定实现约定，不代表摘要真是用户亲自说的。

## 29. 为什么还保留一部分真实用户消息

摘要可能遗漏措辞、约束或最近的明确指令。保留最近真实用户消息可以提供更高保真的任务边界。

选择顺序是从新到旧，在总预算内尽量保留；最后一个放不下的消息会按剩余 token 截断，然后再恢复原有时间顺序。

旧的 summary message 不会再次作为“真实用户消息”收集，否则多次压缩会不断把摘要套进用户历史。

## 30. Local replacement history 不保留旧工具明细

Local builder 只追加：

- 选中的用户消息；
- 新摘要。

旧 reasoning、tool call、tool output、assistant 普通回复不会原样进入 replacement history。

它们的重要事实只能通过摘要继续存在。这正是多次压缩可能逐渐损失细节的根本原因。

## 31. Remote v1 与 Local 最大的不同

Remote v1 不要求普通模型生成一段文本后由客户端重新拼历史，而是调用专门的 compact conversation endpoint：

```rust
model_client.compact_conversation_history(...)
```

请求仍包含：

- 规范化历史；
- 当前可见工具规格；
- base instructions；
- reasoning effort/summary；
- compaction metadata。

endpoint 直接返回一组新的 `ResponseItem`，Codex 再进行本地过滤和上下文重注入。

## 32. Remote 请求前为何先压缩超大的尾部工具输出

历史可能已经超过 compact endpoint 自己能接受的窗口，而最大头来自刚执行完的工具输出。

`trim_function_call_history_to_fit_context_window` 从尾部反向查看。如果末尾是可改写的 output，就把正文替换为固定提示：

```text
Output exceeded the available model context and was truncated
```

对于 tool search output，则保留状态和执行方式，但把 `tools` 数组清空。

它保留 `call_id` 和 success 等结构字段，所以协议配对仍成立。

## 33. 为什么只改写连续的尾部 output

函数从最新 item 向前走，遇到第一个不能安全改写的 source 就停止。

这是一种保守策略：只缩小最可能造成突发溢出的近期工具结果，不任意重写整个历史中的旧语义。

此外 `HistoryItemGroup` 会把 image resize notice 与其 source 一起移动或计算，避免通知脱离原消息。

## 34. Remote v1 返回结果为何不能原样信任

`process_compacted_history` 会过滤 endpoint 返回的 transcript：

- 丢弃 developer message，避免旧指令或重复指令继续存在；
- 只保留能解析成真实用户消息或 HookPrompt 的 user-role message；
- 保留 assistant/AgentMessage；
- 保留 Compaction/ContextCompaction item；
- 丢弃 reasoning、tool calls、tool outputs、search/image call 等旧执行细节；
- 丢弃 `CompactionTrigger`。

之后，当前 Session 会按需要重新生成 canonical initial context。

## 35. 为什么 developer message 必须重建

旧 developer message 可能包含：

- 旧 cwd；
- 旧权限模式；
- 旧模型专属说明；
- 已变化的 AGENTS.md 或开发者指令；
- 上一窗口的 context diff。

让远端 compact 输出中的旧 developer message直接成为权威，会造成 stale context。因此源码选择丢弃它，再从当前 Session 状态重建。

## 36. Remote v2 请求是普通 stream 加一个 Trigger

v2 路线取得规范化 prompt history 后追加：

```rust
ResponseItem::CompactionTrigger {}
```

然后走 Responses stream。

这个 trigger 只是请求信号，发送前不会被 `ContextManager` 当成长期 API history 保存，返回后也会从 `prompt_input` 里 pop 掉。

## 37. Remote v2 对返回流的要求非常严格

`collect_compaction_output` 要求：

1. 必须看到 `response.completed`；
2. 输出中必须恰好有一个 `ResponseItem::Compaction`；
3. 可以有其他 output item，但 compaction item 不能是零个或多个。

零个意味着服务端没给压缩结果；多个意味着 canonical checkpoint 不明确。两种情况都失败，不会猜一个来安装。

## 38. Opaque compaction item 是什么

`ResponseItem::Compaction` 包含模型/服务端生成的加密内容。源码的 token estimator 会按 encrypted reasoning 的近似明文成本估算它。

它不是给人阅读的 Markdown 摘要。应用不应解读、修改或拆分其内部内容。

白话理解：

> 它像一张由服务端签发的“续聊状态胶囊”；客户端负责完整携带，不负责读懂胶囊内部。

## 39. Remote v2 为什么还要本地保留消息

v2 并不是只把 compaction item 单独作为全部新历史。

`build_v2_compacted_history` 会从压缩前 prompt 中选出允许保留的消息，再追加 opaque item：

```text
[retained messages]
[exactly one Compaction item]
```

这与官方文档所说“compacted window 通常不只包含 compaction item”一致。

## 40. v2 哪些消息可以被保留

候选包括：

- user/developer/system `Message`；
- 非 FINAL_ANSWER 的 `AgentMessage`；
- 且单个 AgentMessage 估算不超过 10,000 tokens。

候选之后还要经过通用 `should_keep_compacted_history_item` 过滤，因此 stale developer/system wrapper 最终不会直接进入安装后的历史。

近期保留消息共享约 64,000 token 的预算；选择也偏向较新的内容。

## 41. v2 保留消息怎样截断

它从后往前选择 message group：

- 整组放得下：完整保留；
- 只剩部分预算：截断文本 content；
- image/audio content 不按文本方式截断；
- 最后恢复原时间顺序。

被保留的 input image 数量会进入 analytics。

## 42. TokenBudget 路线为什么没有摘要

当 TokenBudget feature 启用时，compaction 被实现为“启动新的 context window”：

1. 运行 pre hook；
2. 发出 ContextCompaction started；
3. `start_new_context_window`；
4. 安装当前 canonical initial context；
5. 发 completed；
6. 运行 post hook。

它没有让模型总结旧任务事实，因此适合的语义与普通摘要 compaction 不完全相同。不要看到共同 lifecycle 就假设它们保留的信息一样。

## 43. 最难的区别：Pre-turn 与 Mid-turn 的初始上下文

源码用 `InitialContextInjection` 明确区分。

### Pre-turn / Manual：`DoNotInject`

replacement history 暂时不注入初始上下文，并把 `reference_context_item` 清成 `None`。

下一次正常 Turn 看到没有 reference baseline，就会完整注入当前初始上下文。

### Mid-turn：`BeforeLastUserMessage`

同一个 Turn 马上还要继续 sampling，不能等下一 Turn。因此必须现在就把 canonical initial context 插入 replacement history。

## 44. Mid-turn 为什么要插在最后真实用户消息之前

模型被训练成在 mid-turn compaction 后看到 compaction summary/item 位于历史最后。与此同时，当前 Turn 的真实用户输入仍要保持正确边界。

插入规则依次寻找：

1. 最后真实 user 或非 FINAL_ANSWER AgentMessage；
2. 否则最后 summary-like user message；
3. 否则最后 compaction item；
4. 都没有则追加到末尾。

目标是让 summary/compaction item 尽量仍是最后的“压缩结论”，同时把当前运行上下文放在模型预期位置。

## 45. 为什么 initial context 和 world-state baseline 必须一起产生

`build_compaction_initial_context` 返回：

```text
(rendered initial context items, world_state baseline)
```

两者来自同一个 world state。

如果历史里写的是状态 A，内存 baseline 却记录状态 B，那么下一次只生成 diff 时，就可能漏掉 A→B 之间的变化。

因此注入内容和 baseline 必须一致地安装。

## 46. `reference_context_item` 解决什么问题

它是下一次生成 context updates 时的比较基线。

- `None`：下一正常 Turn 必须完整重注入；
- `Some(current turn context)`：可以只注入相对变化。

Mid-turn compaction 已经在 replacement 中注入完整上下文，所以设置 Some。

Pre-turn/manual 没有立即注入，所以设置 None。

## 47. 真正的语义提交点：`replace_compacted_history`

无论哪条摘要路线，最终都调用：

```rust
Session::replace_compacted_history(
    items,
    reference_context_item,
    world_state_baseline,
    metadata,
)
```

它先给缺少 ID 的 `ResponseItem` 分配 ID，然后用同一份 items：

- 替换 live `ContextManager`；
- 写入 `CompactedItem.replacement_history`。

这保证 live history 与 persisted checkpoint 使用相同 item ID 和内容。

## 48. `ContextManager::replace` 会改变什么

replacement 不是 append：

```text
self.items = new_items
history_version += 1
world_state_baseline = None
```

调用方随后根据 compaction 类型重新安装正确 world-state baseline。

`history_version` 告诉其他逻辑“历史发生了整体重写”，与普通追加 item 不同。

## 49. `CompactedItem` 持久化哪些信息

```rust
pub struct CompactedItem {
    message: String,
    replacement_history: Option<Vec<ResponseItem>>,
    window_number: Option<u64>,
    first_window_id: Option<String>,
    previous_window_id: Option<String>,
    window_id: Option<String>,
}
```

Local 路线把可读 summary 放在 `message` 中。

Remote 路线主要依赖 `replacement_history` 中的 compacted items，因此 `message` 可以为空。

`replacement_history` 是未来恢复时的权威新窗口，不应从 `message` 反向猜测完整历史。

## 50. 持久化顺序为什么是 checkpoint、world state、turn context

源码顺序是：

1. 写 `RolloutItem::Compacted`；
2. 如有，写完整 `WorldStateItem`；
3. 如有，写 `TurnContextItem`。

注释强调：baseline 要写在建立它的 replacement history 之后。

resume 的反向扫描可以先找到最新 replacement checkpoint，再向后取得属于这个新窗口的完整 baseline。

## 51. 为什么 compaction 后会排队 SessionStart hook

安装完成后，Session 把：

```text
SessionStartSource::Compact
```

加入 pending source。

主循环在合适边界运行 pending session-start hooks，使扩展知道“新的上下文窗口开始了”，并有机会重新注入其必要信息。

## 52. Compaction 成功后为什么要重算 token usage

旧 usage 描述的是旧历史。history 已整体替换后，如果继续沿用旧数字，下一步可能立刻再次压缩或错误显示已满。

因此各路线安装 replacement 后都会 `recompute_token_usage`。

这通常是估算/重建新窗口的用量基线；后续真实服务器 usage 到达后还会进一步校准。

## 53. Resume 怎样理解 replacement history

Rollout 是 append-only，因此压缩前的旧 items 仍在文件中。

恢复逻辑不能把它们和 replacement history 简单拼接：

```text
错误：old history + replacement history + later items
正确：replacement history 取代此前模型历史，再追加 checkpoint 之后的新 items
```

这就是第 9 篇中 compaction checkpoint 正向重放的含义。

## 54. Fork 为什么也必须尊重 checkpoint

fork 的新 thread 可以拥有新身份，但起始模型上下文必须与来源 thread 在该位置看到的上下文一致。

集成测试会比较：

- 原 thread compaction 后的后续请求；
- resume 后的请求；
- fork 后的请求。

它们应保留相同的模型 history view，而不是把已被替换的旧历史重新复活。

## 55. Compaction 失败时哪些状态不能提前改变

在成功得到压缩结果之前，不应：

- advance window id；
- 替换 live history；
- 写成功 checkpoint；
- 发 completed item。

源码总体顺序是：先请求并校验结果，再 `advance_auto_compact_window`、处理结果和安装。

Pre hook 中止也发生在真正安装之前。

## 56. Remote v2 为什么限制 stream retry 次数

普通 Responses stream 可能允许较多重试，但 compact 请求本身可能运行很久。

v2 将每种 transport 的 retry budget 限制为最多 2 次，以免一次压缩在服务异常时长时间反复占用 Turn。

重试会复用同一个 `ModelClientSession`，以保留 sticky routing、WebSocket incremental state 等 Turn 级状态。

## 57. 手动 CompactTask 为什么常常返回 `Ok(None)`

手动 compact 是维护任务，不产生普通 assistant 最终回复。

各实现会通过事件报告进度和错误。`CompactTask` 对 `TurnAborted` 明确向上传递；其他 compaction 错误通常已在内部记录并发出 error event，task 最后不再伪造一条聊天回复。

## 58. 一张完整时序图

```text
用户显式 compact / token 达阈值 / 模型切换
                    │
                    ▼
             选择 reason + phase
                    │
                    ▼
              pre-compact hooks
                    │
                    ▼
       emit ContextCompaction started
                    │
       ┌────────────┼───────────────┐
       ▼            ▼               ▼
    local        remote v1       remote v2
普通模型摘要     compact endpoint  CompactionTrigger
       │            │               │
用户消息+summary  返回新 transcript  retained + opaque item
       └────────────┼───────────────┘
                    ▼
        过滤 stale context / 必要时重注入
                    │
                    ▼
         advance AutoCompactWindow
                    │
                    ▼
       replace_compacted_history
       ├─ 替换 live ContextManager
       ├─ persist CompactedItem
       ├─ persist WorldState baseline
       └─ persist TurnContext baseline
                    │
                    ▼
            recompute token usage
                    │
                    ▼
       emit completed + post hooks
                    │
                    ▼
          下一次或同 Turn 继续 sampling
```

## 59. 四个“不等于”

```text
compaction 不等于删除 rollout
summary message 不等于完整 replacement history
UI ContextCompactionItem 不等于模型 Compaction item
token estimate 不等于服务器精确 usage
```

再加一个很重要的：

```text
保留 call_id 结构不等于保留工具输出全文
```

## 60. 常见误解

### 误解一：压缩就是保留最后 N 条消息

不同路线有不同保留策略，还要加入摘要、opaque item 和当前 canonical context。

### 误解二：压缩后旧记录从磁盘消失

Rollout 仍是 append-only；checkpoint 改变的是后续模型历史解释。

### 误解三：摘要越短越好

过短会丢失目标、约束、失败状态和下一步；压缩是在质量、成本与延迟之间平衡。

### 误解四：只要工具输出很大，直接删 output 即可

删 output 会破坏 call/output 配对。源码选择成对删除或保留结构并改写正文。

### 误解五：remote 返回什么就安装什么

Codex 会过滤 stale developer/context wrapper，并重建当前 canonical context。

### 误解六：compaction 只会在用户发送新消息时发生

它也可能在同一 Turn 的工具调用和 follow-up sampling 之间发生。

### 误解七：多次压缩完全无损

Local summary 尤其会让旧细节只通过摘要继续存在；多次重述可能累积信息损失。

## 61. 失败排查表

| 现象 | 优先检查 |
|---|---|
| 刚压缩完又立刻压缩 | token usage 是否重算、prefill baseline 和 scope 是否正确 |
| 切模型后请求超限 | 是否执行 previous-model compaction、是否真的是 downshift |
| 摘要后 cwd/权限像旧值 | remote output 的 stale developer message 是否被过滤，初始上下文是否重注入 |
| 工具历史被 API 拒绝 | `for_prompt` normalization、call/output `call_id` 配对 |
| 大工具输出导致 compact 自身超限 | trailing output rewrite 或 local oldest-item removal 是否生效 |
| resume 后旧历史“复活” | reconstruction 是否把 replacement 当整体替换而非 append |
| mid-turn 压缩后模型忘记当前输入 | initial context 与最后真实用户消息的插入位置 |
| UI 一直显示压缩中 | 是否看到 completed、失败/取消事件，stream 是否缺 `response.completed` |
| remote v2 报 malformed output | Compaction item 是否恰好一个 |

## 62. 测试证据：每组测试证明什么

### Local 构造测试

`compact_tests.rs` 覆盖：

- 只收集真实用户文本；
- 过滤 session prefix 和旧 summary；
- 超长用户消息按 token 预算截断；
- summary 总在 replacement 尾部；
- mid-turn initial context 插入在正确边界；
- stale developer message 被替换。

### History invariant 测试

`context_manager/history_tests.rs` 覆盖：

- 删除最早 Call 会连带删除 Output；
- 删除 Output 会连带删除 Call；
- LocalShell/CustomTool 配对；
- 缺 output 时插入稳定 synthetic output；
- orphan output 被删除；
- 不支持的 image/audio 被净化。

### Local 集成测试

`core/tests/suite/compact.rs` 覆盖：

- 手动与自动 compact；
- token limit 触发；
- pre-turn 和 mid-turn 请求形状；
- model downshift 与 comp hash change；
- BodyAfterPrefix 和硬 context window；
- hooks、usage、UI lifecycle；
- creation-time instructions 在新窗口中继续存在。

### Remote 集成测试

`compact_remote.rs` 覆盖：

- remote replacement 成为 follow-up history；
- v2 trigger 与 stream retry；
- 大 function/tool-search output 改写；
- replacement checkpoint 写进 rollout；
- stale developer instructions 刷新；
- mid-turn context 重注入；
- realtime turn state 复用。

### Resume/Fork 测试

`compact_resume_fork.rs` 覆盖：

- compact、resume、fork 的模型 history view 一致；
- 第二次 compaction 后仍能正确恢复；
- rollback 跨过 compaction 时按 append-only rollout 正确重放。

### App-server 测试

`app-server/tests/suite/v2/compaction.rs` 覆盖：

- `thread/compact/start`；
- local/remote 自动压缩的 started/completed item；
- 无效或未知 thread id 的拒绝。

## 63. 推荐源码阅读顺序

第一遍只看主干：

1. `core/src/session/context_window.rs`
2. `core/src/session/turn.rs` 的 `run_pre_sampling_compact` 与 `run_auto_compact`
3. `core/src/tasks/compact.rs`
4. `core/src/compact.rs`
5. `core/src/session/mod.rs` 的 `replace_compacted_history`

第二遍看远端分叉：

6. `core/src/compact_remote_request.rs`
7. `core/src/compact_remote.rs`
8. `core/src/compact_remote_v2_attempt.rs`
9. `core/src/compact_remote_v2.rs`

第三遍看不变量与恢复：

10. `core/src/context_manager/history.rs`
11. `core/src/context_manager/normalize.rs`
12. `core/src/session/rollout_reconstruction.rs`
13. `protocol/src/protocol.rs` 中的 `CompactedItem`

最后再读集成测试，不要一开始掉进数千行 fixture。

## 64. 理解检查

### 问题 1

为什么 pre-turn compaction 可以不立即注入 initial context，而 mid-turn 不行？

<details>
<summary>参考答案</summary>

pre-turn 后，正常 Turn 的 context update 流程马上有机会发现 reference baseline 为空并完整注入。mid-turn 则要在同一个 Turn 中立刻继续 sampling，没有新的普通 Turn 边界可等待。

</details>

### 问题 2

为什么 `CompactedItem.message` 不能被当成完整的新历史？

<details>
<summary>参考答案</summary>

真正权威的是 `replacement_history`。Remote 路线的 `message` 可以为空，replacement 可能包含 retained messages 和 opaque compaction item。

</details>

### 问题 3

删除一个最老 FunctionCall 时，为什么必须顺便删除 matching output？

<details>
<summary>参考答案</summary>

否则会留下 orphan output，破坏协议结构并使模型无法知道结果来自哪次调用。

</details>

### 问题 4

为什么 remote compact 返回的 developer message 会被丢弃？

<details>
<summary>参考答案</summary>

它可能是旧 cwd、旧权限、旧模型说明或重复 context wrapper。当前 Session 应重新生成 canonical initial context。

</details>

### 问题 5

`BodyAfterPrefix` 是否可以突破模型完整 context window？

<details>
<summary>参考答案</summary>

不可以。它只改变自动压缩软阈值的计数作用域，完整模型窗口仍是独立硬上限。

</details>

## 65. 动手练习

### 练习一：画出三份历史

任选一个包含一次 shell call 的 Turn，分别写出：

1. raw `ContextManager` history；
2. `for_prompt` 后的 history；
3. local compaction replacement history。

特别标出 synthetic `aborted` output 可能出现在哪一层。

### 练习二：模拟 call/output 成对删除

给定：

```text
User
FunctionCall(call_id=A)
FunctionCallOutput(call_id=A)
Assistant
```

分别模拟删除数组第 0、1、2 个 item 后，`remove_corresponding_for` 还会删除什么。

### 练习三：比较三个 phase

为 Manual、PreTurn、MidTurn 各画一条时间线，标出：

- started/completed event；
- initial context 注入时机；
- reference context 是 None 还是 Some；
- 下一次 sampling 在哪里。

### 练习四：验证 fixed commit

```bash
git grep -n 'async fn run_auto_compact' 4ee41929eaf4 -- codex-rs/core/src/session/turn.rs
git grep -n 'replace_compacted_history' 4ee41929eaf4 -- codex-rs/core/src/session/mod.rs
git grep -n 'ensure_call_outputs_present' 4ee41929eaf4 -- codex-rs/core/src/context_manager
git grep -n 'build_v2_compacted_history' 4ee41929eaf4 -- codex-rs/core/src/compact_remote_v2.rs
```

## 66. 本篇局部术语表

| 名词 / 代码词 | 中文理解 | 本篇中的具体含义 |
|---|---|---|
| context window | 上下文窗口 | 一次模型请求能够处理的输入、推理和输出 token 空间 |
| compaction | 上下文压缩 | 用更短的新模型历史整体替换旧模型历史 |
| compact | 压缩（动词） | 执行 compaction 动作 |
| replacement history | 替代历史 | checkpoint 之后模型应使用的 canonical 新上下文 |
| canonical | 权威的 / 规范的 | 后续逻辑应直接采用，而非从别处重新猜测 |
| checkpoint | 检查点 | rollout 中声明“此前模型历史被这份 replacement 取代”的记录 |
| `ContextManager` | 上下文管理器 | Session 内维护 live model history、token 和 context baseline 的对象 |
| raw history | 原始历史 | 尚未为某个模型请求执行 normalization 的内存 items |
| prompt history | 请求历史 | `for_prompt` 后真正准备发给模型的 items |
| compaction trigger | 压缩触发者 | Manual 或 Auto |
| compaction reason | 压缩原因 | UserRequested、ContextLimit、ModelDownshift、CompHashChanged |
| compaction phase | 压缩阶段 | StandaloneTurn、PreTurn 或 MidTurn |
| auto compact limit | 自动压缩阈值 | 达到后触发压缩的软预算 |
| hard context limit | 上下文硬上限 | 模型能够接受的最大窗口，不受 scope 绕过 |
| scope | 计数作用域 | Total 或 BodyAfterPrefix |
| prefill | 预填充前缀 | 新窗口建立时已经占用的固定/初始输入部分 |
| baseline | 比较基线 | 后续计算增长或 diff 时作为起点的状态 |
| `AutoCompactWindow` | 自动压缩窗口状态 | 窗口编号、血缘 ID、prefill 与一次性提醒状态 |
| window lineage | 窗口血缘 | first/previous/current window ID 构成的链 |
| local compaction | 本地编排压缩 | 用普通 Responses 推理生成文字摘要，再由客户端拼 replacement |
| remote v1 | 远端压缩 v1 | 调用专门 compact conversation endpoint 返回新 transcript |
| remote v2 | 远端压缩 v2 | 在 Responses stream 中请求并接收 opaque Compaction item |
| opaque | 不透明的 | 客户端携带但不解析内部语义 |
| retained message | 保留消息 | v2 在 opaque item 之外继续携带的近期合规消息 |
| summarization prompt | 摘要提示 | 系统合成、要求模型总结旧历史的输入 |
| `SUMMARY_PREFIX` | 摘要前缀 | 标记一条 user-role message 实际是 compaction summary |
| synthetic output | 合成结果 | 为未完成 call 补上的稳定 `aborted` output |
| orphan output | 孤儿结果 | 找不到 matching call 的工具结果 |
| call/output pair | 调用结果对 | 共享 `call_id` 的 tool call 与 tool output |
| normalization | 规范化 | 补缺失 output、删 orphan、适配 image/audio 的发送前处理 |
| truncation | 截断 | 在预算内缩短文本或工具输出，不等于语义总结 |
| `CompactionTrigger` | 压缩触发项 | v2 请求尾部的控制 item，完成后不进入长期 prompt history |
| `ResponseItem::Compaction` | 模型压缩项 | 服务端返回的 opaque 模型上下文载体 |
| `ContextCompactionItem` | UI 压缩项 | started/completed 生命周期展示对象 |
| `CompactedItem` | 持久化压缩记录 | rollout 中保存 replacement 和窗口血缘的 checkpoint |
| initial context | 初始上下文 | 当前开发者指令、环境、权限、skills、world state 等 canonical 输入 |
| context reinjection | 上下文重注入 | 丢弃 stale wrapper 后从当前 Session 重新生成初始上下文 |
| `reference_context_item` | 上下文比较基线 | 决定下一 Turn 发送完整 context 还是仅发送 diff |
| world-state baseline | 世界状态基线 | 与 replacement 中所渲染 world state 对齐的比较起点 |
| model downshift | 模型降档 | 切到 context window 更小的模型 |
| comp hash | 压缩兼容哈希 | 模型声明的 compaction 语义兼容标识 |
| fallback model | 后备模型 | 旧模型压缩因模型相关错误失败后尝试的当前模型 |
| recompute | 重新计算 | replacement 安装后重建 token usage 等派生状态 |

## 67. 最后压缩成一句话

Context compaction 的真正主线不是“让模型写一段摘要”，而是：

> 在安全触发点取得一份结构合法的旧上下文，选择合适的压缩实现，过滤过期运行信息，按 Turn 阶段重建当前 canonical context，将结果作为 replacement history 同时安装到内存和 rollout checkpoint，再以新的 token、world-state 和 context baseline 继续运行。

下一篇计划精读 Sandbox approval lifecycle：一条命令为什么有时直接运行、有时询问、有时先在 sandbox 失败再请求升级。

返回[源码精读系列目录](README.md)或[课程总目录](../README.md)。
