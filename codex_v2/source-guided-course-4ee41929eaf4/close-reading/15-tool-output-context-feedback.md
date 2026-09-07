# Tool output 与 context feedback：工具结果怎样回到模型并推动下一轮采样

> 源码基线：`4ee41929eaf4`  
> 本篇重点：`ToolOutput`、`AnyToolResult`、`ResponseInputItem`、`call_id`、`FuturesOrdered`、history recording、truncation、multimodal normalization、synthetic output、follow-up sampling、失败与取消。  
> 建议先读：[run_turn](04-turn-main-loop.md)、[Tool dispatch](05-tool-dispatch.md)、[Context compaction](11-context-compaction.md)。

## 1. 先说人话：工具结果为什么不能只是一个字符串

模型请求天气工具：

```text
call_id = call_17
tool = get_weather
arguments = {"city":"上海"}
```

工具返回：

```text
25°C，多云
```

如果 Codex 只把字符串塞进聊天记录，模型会遇到几个问题：

- 这是哪个工具的结果？
- 它对应哪一次调用？
- 同时调用三个工具时，结果怎样配对？
- 结果是文本、图片、音频还是 JSON？
- 工具失败、被拒绝或取消时怎样闭合调用？
- 输出有十万行时是否全部进入上下文？
- 模型不支持图片时怎么办？
- 什么时候应该继续采样，什么时候 Turn 应该结束？

所以工具结果不是孤立字符串，而是带协议身份、内容形态和生命周期语义的 history item。

## 2. 官方公开模型：call 与 output 用 `call_id` 连接

[OpenAI 官方 Function calling 文档](https://developers.openai.com/api/docs/guides/function-calling)说明：模型先产生 tool call；应用程序执行工具；tool call output 可以是文本或结构化内容，并通过 `call_id` 指向具体调用；随后把原始提示、tool call 和对应 output 交回模型，模型才继续生成答案。

固定提交中的 Codex 实现增加了更多工程层：

- 流式接收多个 call；
- 并行或串行执行；
- 保持稳定回填顺序；
- Hook 改写或阻止输出；
- 大输出截断；
- 多模态适配；
- history 配对修复；
- rollout、UI、telemetry 和 memory 状态同步。

## 3. 贯穿案例

模型在一次 response 中依次产生：

```text
call-A: read_file("config.toml")
call-B: run_tests("config")
call-C: fetch_issue(42)
```

实际完成顺序可能是：

```text
call-C 先完成
call-A 第二
call-B 最后
```

Codex 最终写入模型 history 的逻辑顺序仍是：

```text
call-A
call-B
call-C
output-A
output-B
output-C
```

下一轮模型看到完整 call/output 对，才能结合三份结果继续推理。

## 4. 一句话全景

```text
模型输出 ResponseItem::*Call
→ ToolRouter::build_tool_call
→ 先记录原 call item
→ ToolCallRuntime 创建执行 future
→ ToolRegistry / handler 产生 Box<dyn ToolOutput>
→ AnyToolResult 保存 call_id + payload + output
→ ToolOutput::to_response_item
→ ResponseInputItem::*Output
→ FuturesOrdered 按 call 顺序 drain
→ 转成 ResponseItem 并记录 history
→ prompt 前 truncate/normalize
→ needs_follow_up = true
→ 外层 run_turn 发起下一次 sampling
```

## 5. 先区分五种对象

### 5.1 `ResponseItem::*Call`

模型输出的协议项，例如 `FunctionCall` 或 `CustomToolCall`。

### 5.2 `ToolCall`

Codex 内部统一的待执行调用：

```text
tool_name
call_id
payload
encrypted_function_args
```

### 5.3 `ToolOutput`

具体 handler 返回的运行时结果对象。

### 5.4 `ResponseInputItem::*Output`

准备回给模型的输入形式。

### 5.5 `ResponseItem::*Output`

写入 Codex conversation history 与 rollout 的持久形态。

它们名字很像，但处在不同边界。

## 6. 为什么有 `ResponseInputItem` 和 `ResponseItem`

可以这样理解：

```text
ResponseItem
  模型输出或 conversation 中保存的完整 item

ResponseInputItem
  Codex 主动构造、准备送回模型的输入 item
```

工具执行产生 `ResponseInputItem`，随后转换为 `ResponseItem` 进入 history。转换时 output 的 `id` 暂为空，Session 会在记录过程中补齐需要的内部身份。

## 7. `ToolRouter::build_tool_call`

它识别三类客户端执行调用：

```text
ResponseItem::FunctionCall
ResponseItem::CustomToolCall
ResponseItem::ToolSearchCall { execution == "client" }
```

映射结果：

```text
FunctionCall       → ToolPayload::Function { arguments }
CustomToolCall     → ToolPayload::Custom { input }
client tool search → ToolPayload::ToolSearch { arguments }
```

服务端执行的 tool search 不在这里再次本地运行。

## 8. `arguments` 为什么仍是字符串

Responses API 的 function call arguments 是“包含 JSON 的字符串”，不是已经解析好的 Rust object。

所以路由层先保留：

```rust
ToolPayload::Function { arguments: String }
```

由具体 handler 或 registry adapter 按自身 schema 解析。这样错误能归因到具体工具，也支持不同参数协议。

## 9. call item 为什么先记 history

`handle_output_item_done` 一识别到 tool call，就先调用：

```text
record_completed_response_item(..., &item)
```

然后才构造 tool future。

原因有三个：

1. 即使 Turn 随后取消，模型已经发出的 call 仍应留在 rollout；
2. output 必须有可配对的先行 call；
3. retry/resume 时不能只看见工具结果而丢失调用意图。

源码注释明确说，要立即记录，保持 history 与 rollout 同步。

## 10. tool future 做什么

每个 call 被包装为：

```text
Future<Output = Result<ResponseInputItem>>
```

future 内部经过：

```text
并行准入门
→ Router
→ Registry
→ PreToolUse
→ Handler
→ PostToolUse
→ ToolOutput::to_response_item
```

此时 future 的最终产物已经是模型可消费的 output item，而不再是 handler 私有类型。

## 11. `needs_follow_up = true` 在何时设置

只要模型输出了可执行 tool call，`handle_output_item_done` 就设置：

```rust
output.needs_follow_up = true;
```

这不是说工具已经成功，而是说当前 response 还不能作为 Turn 的终点：必须等工具给出 output，再让模型看结果。

## 12. `ToolOutput` trait 是统一出口

固定源码的核心接口：

```rust
pub trait ToolOutput: Send {
    fn log_preview(&self) -> String;
    fn success_for_logging(&self) -> bool;
    fn contains_external_context(&self) -> bool;
    fn to_response_item(
        &self,
        call_id: &str,
        payload: &ToolPayload,
    ) -> ResponseInputItem;
    ...
}
```

它没有要求每个工具返回同样的数据结构，而是要求每个结果都能回答几个共同问题。

## 13. `log_preview` 不是模型输出

`log_preview` 给 telemetry/logging 使用，只保留：

- 最多约 2 KiB；
- 最多 64 行；
- 超出时附加 telemetry truncation notice。

它不是发送给模型的权威内容，也不能拿来重建真实工具结果。

把 logging preview 和 model-facing output 分开，可以降低日志泄漏和体积，同时保留模型需要的内容策略。

## 14. `success_for_logging` 也不是 wire 字段

它用于 telemetry、Hook 是否运行以及 lifecycle outcome。

`FunctionCallOutputPayload.success` 在固定源码中是内部 metadata；自定义 `Serialize` 只把 `body` 编成 wire 上的字符串或 content-item 数组，`success` 不作为公开 output wrapper 字段发送。

因此不要把它理解成 Responses API 必然收到的 JSON：

```json
{"success": true, "body": "..."}
```

实际 wire `output` 是文本或内容数组。

## 15. `contains_external_context`

某些工具结果来自外部世界，例如动态工具、搜索或外部系统。

若配置：

```text
memories.disable_on_external_context = true
```

工具输出可以标记 `contains_external_context`，Session 就把 thread 的 memory mode 标为 polluted，避免把外部不可信或临时内容当成可生成长期记忆的纯对话材料。

## 16. `to_response_item` 才是模型边界

handler 可以返回任意实现 `ToolOutput` 的 Rust 类型；真正进入模型协议前统一调用：

```rust
result.to_response_item(&call_id, &payload)
```

这里同时使用 `call_id` 和原始 `payload`，因为 output 类型需要知道：

- 要与哪次调用配对；
- 原调用是 function 还是 custom；
- 应返回哪种 `ResponseInputItem` variant。

## 17. `AnyToolResult` 为什么保留三样东西

```rust
AnyToolResult {
    call_id,
    payload,
    result: Box<dyn ToolOutput>,
    post_tool_use_payload,
}
```

即使 handler 已执行完，转换阶段仍需要原 call 的身份与 payload 类型。

`Box<dyn ToolOutput>` 让不同 handler 的具体结果类型在统一 pipeline 中流动，而不用定义一个包含所有工具结果的大 enum。

## 18. `AnyToolResult::into_response`

实现非常短：

```rust
result.to_response_item(&call_id, &payload)
```

短并不代表不重要。它是运行时对象到模型协议 item 的单一汇合点。

## 19. 普通 function output

`FunctionToolOutput` 保存：

```text
body: Vec<FunctionCallOutputContentItem>
success: Option<bool>
post_tool_use_response: Option<JsonValue>
```

只有一个 text item 时，转换会压成：

```text
FunctionCallOutputBody::Text
```

多个文本/图片/音频 item 时，保留：

```text
FunctionCallOutputBody::ContentItems
```

## 20. function 与 custom output 怎样分叉

辅助函数 `function_tool_response` 看原始 payload：

```text
ToolPayload::Custom
→ ResponseInputItem::CustomToolCallOutput

其他 payload
→ ResponseInputItem::FunctionCallOutput
```

所以同一个 `FunctionToolOutput` 类型既能服务 JSON function tool，也能服务 freeform custom tool。

## 21. 为什么 variant 必须与 call 类型匹配

模型历史中的配对不只靠相同 `call_id`，还要使用兼容的 item 类型：

```text
FunctionCall      ↔ FunctionCallOutput
CustomToolCall    ↔ CustomToolCallOutput
ToolSearchCall    ↔ ToolSearchOutput
LocalShellCall    ↔ FunctionCallOutput
```

若 Custom call 错回 Function output，history normalization 会把它视作不同协议族，不能正确闭合。

## 22. `FunctionCallOutputContentItem`

工具输出支持：

```text
InputText
InputImage
InputAudio
EncryptedContent
```

这说明“工具结果”不等于“字符串”。例如截图工具可以直接把图片内容交给支持视觉输入的模型。

## 23. 文本化是有损的

`function_call_output_content_items_to_text`：

- 只保留非空 `InputText`；
- 图片、音频和 encrypted content 被忽略；
- 多段文本用换行连接。

它适合 legacy string surface 或 telemetry preview，但不是多模态 output 的权威表示。

源码注释明确称这种转换 intentionally lossy。

## 24. MCP output 为什么先转协议 payload

`McpToolOutput` 内部保留完整 `CallToolResult`，包含：

- content；
- structured content；
- `is_error`；
- private metadata。

直接模型路径调用 `as_function_call_output_payload`，将 MCP 内容转成 Responses 支持的 text/image/audio/encrypted items，并在前面加入 wall time。

## 25. MCP 的 direct 与 code mode 看到的内容不同

直接模型上下文：

- 加 wall-time header；
- 清理不支持的 original image detail；
- 按模型 truncation policy 截断。

Code Mode：

- 使用原始 `CallToolResult` 的 JSON 结果；
- `_meta` 不交给 Code Mode；
- 不复用 direct context 的文本化包装。

这是同一工具结果针对不同消费者的两个 projection。

## 26. Tool search output

客户端 tool search 返回：

```text
ToolSearchOutput {
    call_id,
    status: "completed",
    execution: "client",
    tools: [...]
}
```

失败或取消时也需要闭合 call，因此可能返回空 tools，而不是完全没有 output item。

## 27. Apply Patch output

上一章的 `ApplyPatchToolOutput`：

- 模型可见内容是成功摘要文本；
- success logging 为 true；
- direct custom payload 转为 `CustomToolCallOutput`；
- PostToolUse 可得到字符串 response；
- Code Mode result 特意返回空 object。

这说明输出 projection 由工具语义决定，不要求所有消费者看到相同形态。

## 28. Unified exec output 是结构化文本包装

`ExecCommandToolOutput` 持有：

```text
chunk_id
wall_time
raw_output bytes
process/session id
exit_code
original_token_count
output_omitted_bytes
max_output_tokens
```

direct model response 被渲染为：

```text
Chunk ID: ...
Wall time: ...
Process exited with code ...
Process running with session ID ...
Original token count: ...
Output:
...
```

模型因此不仅看到 stdout，还能决定是否继续 `write_stdin`。

## 29. “工具成功”与“业务命令成功”不是一回事

Unified exec 的 `success_for_logging()` 返回 true，表示工具协议成功返回了一份可解释结果；命令是否退出 0 则在 `exit_code` 和文本中表达。

普通 shell 路径中，非零退出会经 emitter 转为 `RespondToModel`，最终得到 `success = false` 的 failure response。

所以 success 的语义取决于工具 contract，不能统一解释为“用户任务完成”。

## 30. PreToolUse block 怎样变成模型结果

Registry 在 handler 前运行 PreToolUse hook。

若 hook block：

1. handler 不执行；
2. 返回 `FunctionCallError::RespondToModel`；
3. `ToolCallRuntime::failure_response` 根据 payload 生成对应 output variant；
4. output body 是阻止原因；
5. function/custom output 内部 success 标为 false。

即使工具没运行，也必须给 call 一个 output，模型才能继续。

## 31. PostToolUse block 的含义不同

PostToolUse 发生在工具已经执行完成之后。

源码注释强调：

```text
PostToolUse block rejects the result,
not the already-completed tool execution.
```

也就是说，hook 可以阻止原结果进入模型，但不能假装撤销已经发生的文件、网络或进程副作用。

## 32. PostToolUse feedback 怎样替换模型可见内容

若 hook 不 block，但返回 feedback message，Registry 使用：

```text
PostToolUseFeedbackOutput {
    original,
    model_visible,
}
```

它的行为：

- logging success 仍取 original；
- Code Mode result 仍取 original；
- direct `to_response_item` 改用 feedback text。

因此 feedback 是“给模型的解释层”，不是篡改工具真实执行记录。

## 33. Handler 普通错误怎样闭合 call

`ToolCallRuntime::handle_tool_call` 区分：

```text
Fatal
  → 整个 sampling/turn 进入致命错误

其他 FunctionCallError
  → failure_response
  → 仍返回一个 output item
```

普通参数错误、审批拒绝、执行失败应尽量回给模型，让模型可以调整；内部不变量破坏才升级为 Fatal。

## 34. failure response 保留原 `call_id`

无论成功或可恢复失败，output 都使用原 call id。

例如：

```text
FunctionCall(call_17)
FunctionCallOutput(call_17, "permission denied")
```

失败不是配对关系的例外。恰恰相反，失败更需要明确告诉模型“哪次调用失败”。

## 35. 取消也要生成 output

Tool runtime 监听 cancellation token。

若取消发生在 handler terminal outcome 前：

- 普通 runtime task 被 abort；
- 需要清理的 runtime 可继续等待 teardown；
- 生成 `AbortedToolOutput`；
- 文本类似 `aborted by user after 1.2s`；
- lifecycle 只记录一次 Aborted。

这避免 history 留下一条永久悬空的 call。

## 36. `terminal_outcome_reached` 防什么

handler 完成和 cancellation 可能同时发生。`AtomicBool` 决定谁拥有唯一 terminal outcome。

没有它，竞态可能同时发：

```text
Completed
Aborted
```

或者产生两个不同 output。

固定测试覆盖“handler 已完成、随后取消”与“取消等待 runtime cleanup”两种边界。

## 37. 并行执行门

`ToolCallRuntime` 有：

```text
parallel_execution: Arc<RwLock<()>>
```

- 支持并行的工具拿 read lock；
- 不支持并行的工具拿 write lock。

多个 read lock 可同时运行；write lock 会与其他执行互斥。

这决定 execution concurrency，不直接决定 history output 顺序。

## 38. 为什么使用 `FuturesOrdered`

模型按 A、B、C 输出 call 时，三个 future 按该顺序 push 进 `FuturesOrdered`。

即使完成顺序是 C、A、B，stream 取结果时仍按 A、B、C yield。

这带来：

- 稳定 transcript；
- call/output 排列可预测；
- retry 和 snapshot 更容易比较；
- 减少因完成时序变化造成的 prompt cache churn。

## 39. 并行和有序并不矛盾

`FuturesOrdered` 不会强迫 B 等 A 才开始执行。future 都可以被并发 poll；它只延迟较晚位置结果的“交付”。

可以类比餐厅：三道菜同时做，但服务员按菜单顺序摆到桌上。

## 40. 为什么不在每个工具完成时立刻 sampling

如果 C 先完成就立刻请求模型，模型可能在看不见 A/B 的情况下作出中间决定；随后又为 A/B 重复采样。

固定主链等待当前 response 中所有 `in_flight` 工具 drain 完，再返回外层 loop。

这适合“模型一次明确发出的一批工具调用”：先收齐批次，再继续推理。

## 41. `drain_in_flight`

核心循环：

```text
while let Some(result) = in_flight.next().await {
    ResponseInputItem
    → ResponseItem
    → record_conversation_items
}
```

同时它会根据 output 是否可能包含 external context 更新 memory safety 状态。

## 42. 为什么 drain 在 response stream 完成之后

工具在模型 stream 仍继续时已经可以启动，因此实现了 model streaming 与 tool execution overlap。

但在本轮 sampling request 返回前，会统一 drain：

- token count event 等待可能暂停用户的工具结束；
- TurnDiff 在 patch 等工具完成后再计算；
- cancellation 在 pending 工具处理后统一检查。

## 43. stream 错误时工具结果怎么办

`try_run_sampling_request` 先得到一个 `outcome`，无论成功或 stream error，后面仍执行 `drain_in_flight`。

因此模型已经发出的 tool call 及其执行结果可以进入 history；随后 sampling error 再向上返回。若错误可重试，新 request 从更新后的 history 构造 prompt。

这避免因传输尾部错误重复执行已经完成的工具。

## 44. 结果怎样写入 Session

`record_conversation_items` 主要做：

1. 准备图片等 history 内容；
2. 锁住 Session state；
3. `state.record_items` 写入内存 history；
4. 持久化 rollout response items；
5. 发送 raw response item 事件。

所以模型反馈、恢复历史和客户端 raw stream 共享同一批 canonical items，但各消费面可能有不同投影。

## 45. 内存 history 在记录时就截断 output

`ContextManager::record_items` 对每个 API item 调用 `process_item`。

只有：

```text
FunctionCallOutput
CustomToolCallOutput
```

会在这里按 truncation policy 处理 body；其他 item 原样 clone。

这给模型上下文建立早期硬边界，避免巨大工具输出无限堆积。

## 46. 为什么乘以 `1.2`

源码使用：

```text
policy_with_serialization_budget = policy * 1.2
```

注释说明文本之后还要进入 JSON serialization，转义和 wrapper 会带来开销。这里给 payload 内容留一个小缓冲，使最终请求更接近目标预算。

不要把 1.2 理解成 tokenizer 精确公式；它是工程余量。

## 47. 文本截断保留头尾

`truncate_text` 使用中间截断 helper：

- byte policy：按字符边界保留前后；
- token policy：按近似 token budget 保留前后。

`formatted_truncate_text` 还加：

```text
Warning: truncated output
original token count
total output lines
```

保留头尾通常比只保留开头更适合日志：错误总结常出现在末尾。

## 48. token count 是近似值

输出截断使用 `approx_token_count` 等启发式，不是为每段输出调用模型 tokenizer。

优点是快、跨模型；代价是预算不是逐 token 精确。

真正上下文限制仍由模型请求和更高层 compaction/token accounting 共同兜底。

## 49. collection cap 与 model truncation 是两层

Unified exec 先可能因输出收集上限丢弃中间 bytes，记录：

```text
output_omitted_bytes
```

之后 model-facing output 又按 `max_output_tokens` 与模型 truncation policy 截断。

因此：

```text
原始进程输出
→ collection cap
→ raw_output buffer + omitted marker
→ model token truncation
```

模型会看到 omission notice，不会误以为所见是完整输出。

## 50. 多模态 item 怎样消费预算

`truncate_function_output_items_with_policy`：

- text 消耗 byte/token budget，超出时截片段；
- image 保留，不在这里扣文本预算；
- audio 按估算 token cost 扣预算，超出则省略；
- encrypted content 保留；
- 被省略的 text/audio 数量以 placeholder 告知。

这不是所有模态统一按字符串长度处理。

## 51. 为什么图片 detail 要净化

MCP 结果可能请求 `original` image detail，但当前模型未必支持。

`sanitize_original_image_detail` 会按模型能力调整，防止把不支持的 detail 直接发送上游。

能力适配发生在 model-facing projection，不要求 MCP server 了解当前模型。

## 52. prompt 前还要 normalize

`ContextManager::for_prompt` 在 history snapshot 上执行：

```text
ensure_call_outputs_present
remove_orphan_outputs
strip_images_when_unsupported
strip_audio_when_unsupported
```

注意它消费的是 clone/snapshot；归一化主要服务即将发送的 prompt，不一定把 synthetic 修复永久写回原始 rollout。

## 53. 为什么每个 call 必须有 output

工具协议的基本结构是：

```text
Call(call_id=X)
Output(call_id=X)
```

若只有 call，没有 output，模型可能把它理解为仍在等待的未完成动作；API 也可能拒绝不完整工具历史。

取消、恢复、旧 rollout 或异常中断都可能留下缺口，所以 prompt 前要修复。

## 54. missing output 怎样补

`ensure_call_outputs_present` 先收集已有 output IDs，再扫描 calls。

缺失时立即在对应 call 后插入：

```text
FunctionCallOutput("aborted")
CustomToolCallOutput("aborted")
ToolSearchOutput(tools=[])
```

LocalShellCall 使用 FunctionCallOutput 闭合。

## 55. synthetic output 为什么插在 call 后面

若统一追加到 history 尾部，中间可能隔着许多无关消息和其他 calls。

插在 call 后：

- 配对局部清晰；
- 历史语义更稳定；
- 不会改变后续正常 output 的相对结构。

批量插入时按倒序 index 操作，避免前一次 insertion 推动后面的 index。

## 56. synthetic output ID 为什么要稳定

prompt normalization 可能在 retry、resume 或多次采样前重复运行，但 synthetic item 不一定持久化。

实现根据 source call item ID，用固定 UUID v5 namespace 派生 output ID。

同一 call 每次得到同一 synthetic ID，可避免仅因随机 ID 改变 prompt prefix，提升 prompt-cache reuse。

## 57. 为什么 Custom/LocalShell 缺 output 会 `error_or_panic`

源码对一些理论上不该缺失的协议形态记录更强的不变量错误，但仍构造 synthetic aborted output，让 release 行为尽量可恢复。

开发构建可更早暴露 bug；生产 prompt 仍尽量保持配对完整。

这叫“报告内部异常，同时修复外部协议形状”。

## 58. orphan output 是什么

只有 output，没有对应 call：

```text
FunctionCallOutput(call_id=ghost)
```

这份数据无法知道是谁请求、参数是什么，也可能错误污染模型判断。

`remove_orphan_outputs` 会删除客户端执行的孤儿 output。

## 59. server tool search output 的例外

`ToolSearchOutput { execution == "server" }` 可以保留，因为其 call/output 生命周期可能由上游服务管理，不要求本地 history 中存在客户端 call 形态。

客户端 tool search output 则必须匹配本地 `ToolSearchCall`。

## 60. 删除 history item 为什么要成对

`remove_corresponding_for` 支持：

- 删 call 时删第一个对应 output；
- 删 output 时删对应 call；
- function/custom/tool-search/local-shell 分别匹配。

Compaction、rollback 或 history trimming 不能只删除一半，否则下次 normalization 又要合成 aborted，甚至改变原语义。

## 61. 模型不支持图片时怎么办

prompt normalization 把 image item 替换成文本：

```text
image content omitted because you do not support image input
```

图片不是静默消失。模型至少知道那里原本有图像，只是当前模型无法接收。

ImageGenerationCall 的 result 也会清空，避免不支持的图像数据进入请求。

## 62. 模型不支持音频时怎么办

音频 content 替换成：

```text
audio content omitted because you do not support audio input
```

这一转换同时覆盖普通 message 和 function/custom tool output。

所以切换到不支持某模态的模型时，不需要重写持久历史；prompt projection 会适配。

## 63. raw history、model history、rollout 不是同一个视图

可以这样分：

```text
handler result
  最接近工具原生对象

ResponseInputItem
  本次准备回模型的协议输出

in-memory ContextManager item
  记录时已按输出策略截断

for_prompt snapshot
  又完成配对和模态 normalization

rollout/raw event
  用于持久化和客户端观察的响应 item
```

不要从某一层的表现推断其他层一定字节相同。

## 64. 输出何时真正进入下一次 prompt

`drain_in_flight` 只负责记录。它不会直接调用模型。

返回外层 `run_turn` 后：

1. `model_needs_follow_up` 为 true；
2. 检查 pending user/mailbox input；
3. 必要时先 compaction；
4. `continue` 外层 loop；
5. `clone_history().for_prompt(...)`；
6. 构造新 `Prompt`；
7. 发下一次 sampling request。

feedback 通过 conversation state，而不是函数回调直连模型。

## 65. 下一轮模型看见什么

简化 history：

```text
User: 请检查配置
Assistant FunctionCall(call-A, read_file, ...)
Assistant FunctionCall(call-B, run_tests, ...)
FunctionCallOutput(call-A, "...")
FunctionCallOutput(call-B, "...")
```

再加相同工具规格、系统/开发者指令和必要 world state。

模型由此决定：继续调用工具、修复错误，还是给最终回答。

## 66. 为什么 call/output 后还要保留工具规格

模型不仅要理解旧 output，还可能继续调用同一工具。

每个 Step 构建 prompt 时带当前 `ToolRouter` 的 model-visible specs。Call history 告诉它过去发生了什么；tool specs 告诉它现在还能做什么。

两者职责不同。

## 67. 模型何时停止继续采样

当前 sampling 的 `needs_follow_up` 来源包括：

- 出现工具 call；
- server `end_turn == false`；
- mailbox/pending input；
- Stop hook 要求继续。

若没有这些条件，外层 loop 才接受最后 assistant message，并走 Turn 收尾。

## 68. 工具 output 本身不决定“继续几轮”

一个 output 只提供事实或错误。下一轮模型可能：

- 直接回答；
- 再读一个文件；
- 调用测试；
- 修复 patch；
- 请求审批。

每轮是否继续由新 response 中是否再次出现 call 等信号决定，不是在 tool handler 里写死循环次数。

## 69. pending 用户输入怎样加入

工具执行期间用户可能 steer，或者 mailbox 出现新消息。

采样结束后外层把：

```text
model_needs_follow_up || has_pending_input
```

合成总 `needs_follow_up`。

下一轮 prompt 同时包含工具结果和新输入，使模型能基于最新指令调整，而不是完成过时计划后才看见用户消息。

## 70. 为什么 token limit 检查在继续前发生

工具输出可能很大，即使单项被截断，多次调用仍会推高上下文。

外层在下一次 sampling 前检查 context-window token status；需要时运行 MidTurn compaction，再继续。

因此链路是：

```text
record outputs
→ 计算新上下文压力
→ compact 或 start new window
→ 再 sampling
```

## 71. Retry 为什么不会轻易重复工具

模型 call item立即记录，tool output 在 stream 结束后 drain。若随后出现可重试模型流错误，新的 request 使用 `clone_history()`。

已执行 call/output 已在 history 中，重试主要继续对话，而不是简单把旧 request 原样再跑一遍。

这减少副作用工具重复执行的风险。

## 72. Unsupported tool 怎样反馈

Registry 找不到工具时返回 `RespondToModel`，内容是 unsupported tool message。

`ToolCallRuntime` 再按原 payload 生成对应 failure output。

下一轮模型会知道：

- 哪个 call id 失败；
- 该工具不可用；
- 可以选择已暴露的其他工具或直接解释限制。

## 73. Payload kind 不兼容为何是 Fatal

如果 registry 找到 tool name，但 handler 声明的 payload kind 与实际调用不匹配，源码将其视为内部协议错误，返回 Fatal。

因为这通常说明：

- tool spec 与 runtime 注册不一致；
- router/handler contract 被破坏；
- 继续把错误伪装成普通工具失败可能隐藏实现 bug。

## 74. tool task join error 为何 Fatal

Tokio task panic、无法 join 等不是业务工具返回的正常错误。

`tool_task_join_error` 转成：

```text
FunctionCallError::Fatal("tool task failed to receive...")
```

模型不能靠换参数修复 runtime task 崩溃，因此不应伪造成普通 output。

## 75. `success = None` 是什么

有些输出没有明确内部成功标记，`success_for_logging` 通常把 `None` 当 true。

例如 Aborted output 转换时可能使用 `success = None`，但它自身的 `success_for_logging()` 返回 false，lifecycle 已记录 Aborted。

所以内部 success metadata、lifecycle outcome 和文本语义是相关但不完全相同的三个维度。

## 76. 外部 context 的第二条检测线

除了 `ToolOutput::contains_external_context`，记录完成 response item 时，ToolSearch/WebSearch 等协议项也会被视为可能包含 external context。

这覆盖：

- 本地 tool runtime 主动标记的外部结果；
- 上游/服务端直接产生的 search items。

安全状态不只依赖某一个 handler 记得打标。

## 77. `executed_tool_calls` metadata

可选 feature 会记录工具调用及 source/mode，并在下一次 prompt 构造时把 pending metadata 绑定到相关 output。

这属于 warehouse/internal passthrough metadata，不应与模型主要看到的 call/output body 混为一谈。

核心语义仍由 `call_id` 和正式 output item 承担。

## 78. 三种“截断”不要混淆

### 78.1 Telemetry preview 截断

只保护日志，约 2 KiB/64 行。

### 78.2 Tool-specific model output 截断

例如 unified exec 的 `max_output_tokens`、MCP response projection。

### 78.3 Context history 截断

`ContextManager::process_item` 对 function/custom outputs 的统一边界。

同一 output 可能依次经过多层；每层服务不同消费者。

## 79. 三种“顺序”不要混淆

### 79.1 模型 call 顺序

Response stream 中 A、B、C 的出现顺序。

### 79.2 工具完成顺序

受网络、进程、审批影响，可能 C、A、B。

### 79.3 history output 顺序

`FuturesOrdered` 恢复为 A、B、C。

UI 的实时完成事件可能反映实际执行时序，而模型 history 追求稳定逻辑顺序。

## 80. 三种“失败”不要混淆

### 80.1 工具领域失败

例如测试退出 1，可能仍是一个有效 exec result。

### 80.2 可回给模型的调用失败

例如参数错误、审批拒绝；产生 failure output，继续 sampling。

### 80.3 Runtime Fatal

例如 payload contract 破坏、tool task panic；终止当前正常反馈链。

## 81. 完整状态表

| 阶段 | 数据形态 | 是否已执行工具 | 是否已进 history | 是否触发后续 sampling |
|---|---|---:|---:|---:|
| 模型输出 call delta | 流式参数 | 否 | 否 | 尚未 |
| OutputItemDone call | `ResponseItem::*Call` | 否 | 是 | 标记需要 |
| queued future | `ToolCall` | 可能开始 | call 已记录 | 是 |
| handler result | `Box<dyn ToolOutput>` | 是 | 否 | 是 |
| converted output | `ResponseInputItem::*Output` | 是 | 否 | 是 |
| drained output | `ResponseItem::*Output` | 是 | 是 | 是 |
| prompt projection | normalized history | 是 | snapshot | 下一次请求 |
| final assistant answer | Message | 是 | 是 | 无新 call 时结束 |

## 82. 一个并发例子

模型产生：

```text
A = read small file
B = run slow tests
C = query remote issue
```

时间线：

```text
t0  A/B/C call items 按顺序记录
t1  三个 future 通过并行门
t2  C 完成，但暂存在 ordered stream 后方
t3  A 完成，drain 得到 A
t4  B 完成，drain 得到 B，再得到已完成的 C
t5  A/B/C outputs 按顺序进入 history
t6  外层构造下一次 prompt
```

若 B 不允许并行，它拿 write lock，会与 A/C 的 read locks 形成互斥边界。

## 83. 一个取消例子

```text
call-X 启动长命令
用户点击停止
```

可能路径：

```text
cancellation token 触发
→ runtime cleanup 或 task abort
→ AbortedToolOutput(call-X)
→ history 插入与 call-X 配对的 output
→ drain 完成
→ cancellation check 返回 TurnAborted
```

即使 Turn 被中止，恢复历史时也不留下模糊的“工具还在运行”。

## 84. 一个 Hook feedback 例子

工具原始结果：

```json
{"secret":"...", "count":42}
```

PostToolUse hook 返回 feedback：

```text
结果已执行，但原始响应不允许暴露；只告诉模型 count=42。
```

Registry 可以让 direct 模型只看到 feedback，同时 telemetry success 和 Code Mode projection 仍基于 original contract。

若 hook 选择 block，则模型得到 block error；外部副作用仍不能自动撤销。

## 85. 建议的源码阅读顺序

第一遍读主链：

1. `stream_events_utils.rs::handle_output_item_done`；
2. `tools/parallel.rs::handle_tool_call`；
3. `tools/registry.rs::AnyToolResult`；
4. `session/turn.rs::drain_in_flight`；
5. 外层 `run_turn` 的 `needs_follow_up` 分支。

第二遍读输出形态：

6. `tools/src/tool_output.rs::ToolOutput`；
7. `core/src/tools/context.rs`；
8. protocol `ResponseInputItem`；
9. `FunctionCallOutputPayload` serialization；
10. `ResponseInputItem → ResponseItem`。

第三遍读上下文防线：

11. `ContextManager::record_items/process_item`；
12. `normalize::ensure_call_outputs_present`；
13. `normalize::remove_orphan_outputs`；
14. image/audio stripping；
15. output-truncation crate 与测试。

## 86. 固定源码搜索命令

```bash
git grep -n 'pub trait ToolOutput' 4ee41929eaf4 -- codex-rs/tools/src
git grep -n 'struct AnyToolResult' 4ee41929eaf4 -- codex-rs/core/src/tools
git grep -n 'handle_output_item_done' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'drain_in_flight' 4ee41929eaf4 -- codex-rs/core/src/session
git grep -n 'ensure_call_outputs_present' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'truncate_function_output_payload' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'FunctionCallOutputPayload' 4ee41929eaf4 -- codex-rs/protocol/src
```

## 87. 理解检查

### 问题 1：为什么 output 必须带原 call 的 `call_id`？

<details><summary>参考答案</summary>

同一 response 可以有多个并发调用；`call_id` 让模型、history normalization 和恢复逻辑知道每份结果对应哪次请求。

</details>

### 问题 2：为什么 call 要在工具执行前写 history？

<details><summary>参考答案</summary>

即使执行取消或 stream 随后失败，也必须保留模型已经发出的调用；output 才有先行 call 可配对，retry 也不会轻易重复副作用。

</details>

### 问题 3：并行执行为何仍能按 call 顺序回填？

<details><summary>参考答案</summary>

并行门控制 future 的执行并发；`FuturesOrdered` 独立控制结果 yield 顺序，按 push 顺序交付。

</details>

### 问题 4：PostToolUse block 会撤销工具副作用吗？

<details><summary>参考答案</summary>

不会。它发生在 handler 完成后，只阻止或替换模型可见结果；已写文件、已发请求等需要工具自己提供事务或补偿。

</details>

### 问题 5：为什么 synthetic output 使用稳定 ID？

<details><summary>参考答案</summary>

Prompt normalization 可重复运行；稳定派生 ID 避免同一修复每次改变 prompt prefix，从而保护 prompt-cache reuse。

</details>

### 问题 6：工具结果为什么不会直接调用下一次模型？

<details><summary>参考答案</summary>

结果先记录为 conversation item；当前批次 drain 后，外层 turn loop 重新读取规范化 history、检查输入和 token 状态，再统一发下一次 sampling。

</details>

### 问题 7：为什么 unsupported image 要替换成文字而非静默删除？

<details><summary>参考答案</summary>

模型仍需知道结果中原本有无法消费的模态，避免把“未看见图片”误解成“工具没有返回图片”。

</details>

## 88. 动手练习

### 练习一：画配对

给出三个 FunctionCall 和两个 CustomToolCall，设计 call IDs，并写出正确的五个 output variants。

### 练习二：模拟乱序完成

假设 calls 为 A、B、C，完成顺序 C、B、A。分别写出：

```text
实际完成事件顺序
FuturesOrdered yield 顺序
history output 顺序
```

### 练习三：给失败分类

把下面情况归类为领域结果、RespondToModel 或 Fatal：

```text
测试退出 1
用户拒绝审批
function 参数 JSON 错误
handler task panic
registry payload kind 不匹配
```

### 练习四：计算截断层

一条 unified exec 产生 50 MB 输出，collection cap 丢掉 45 MB，model limit 又只允许 10k tokens。画出每层数据和 omission notice。

### 练习五：修复破损 history

输入：

```text
FunctionCall(A)
CustomToolCall(B)
FunctionCallOutput(ghost)
```

写出 normalization 后的 call/output 结构。

## 89. 本篇局部术语表

| 名词 / 代码词 | 中文理解 | 本篇中的具体含义 |
|---|---|---|
| tool output | 工具输出 | handler 执行后产生、准备回给模型的数据 |
| context feedback | 上下文反馈 | 把工具结果记录进 conversation，再由下一次 prompt 交给模型 |
| `call_id` | 调用配对 ID | 连接一次模型 tool call 与对应 output |
| `ResponseItem` | 响应/历史项 | 模型输出及 conversation 中保存的规范 item |
| `ResponseInputItem` | 响应输入项 | Codex 主动构造、准备作为下一次模型输入的 item |
| `ToolCall` | 内部工具调用 | 统一保存 tool name、call ID、payload 与加密参数 |
| `ToolPayload` | 工具载荷 | Function、Custom、ToolSearch 等输入类别 |
| `ToolOutput` | 工具输出 trait | 不同结果到 logging、Hook、Code Mode 和模型协议的统一接口 |
| model-facing | 面向模型 | 真正进入下一次 Responses prompt 的投影 |
| projection | 投影 | 同一原始结果针对不同消费者形成的表示 |
| `AnyToolResult` | 擦除具体类型的工具结果 | 保存 call ID、原 payload、动态 output 与 hook payload |
| dynamic dispatch | 动态分派 | 通过 `Box<dyn ToolOutput>` 在运行时调用具体实现 |
| `FunctionToolOutput` | 通用函数工具结果 | 保存 text/image/audio items 与内部 success |
| `FunctionCallOutputPayload` | 函数调用输出载荷 | wire 上编码为纯文本或 content-item 数组 |
| `FunctionCallOutputBody` | 输出正文 | Text 或 ContentItems 两种形态 |
| content item | 内容项 | 文本、图片、音频或加密内容的单元 |
| multimodal | 多模态 | 一个输出可包含不止文本的数据 |
| lossy | 有损 | 转换后丢失图片、音频等部分信息 |
| `CustomToolCallOutput` | 自定义工具输出 | 与 freeform CustomToolCall 配对的 output variant |
| `ToolSearchOutput` | 工具搜索输出 | 返回发现的工具列表并闭合 ToolSearchCall |
| `McpToolOutput` | MCP 工具结果适配 | 把 CallToolResult 投影成 direct context 或 Code Mode JSON |
| `ExecCommandToolOutput` | Unified exec 结果 | 保存 session、exit、raw output、截断与时间信息 |
| `log_preview` | 日志预览 | 有界 telemetry 文本，不是权威模型输出 |
| `success_for_logging` | 日志成功标记 | telemetry/lifecycle 用内部语义，不等于用户任务完成 |
| external context | 外部上下文 | 搜索、MCP 或外部工具带回的环境外信息 |
| memory pollution | 记忆污染标记 | 外部上下文存在时禁止从该 thread 生成长期 memory |
| PreToolUse | 工具执行前 Hook | 可阻止或改写 input |
| PostToolUse | 工具执行后 Hook | 可阻止原 result 或替换模型可见 feedback |
| `PostToolUseFeedbackOutput` | Hook 反馈包装 | original 服务 logging/Code Mode，feedback 服务 direct model |
| `RespondToModel` | 回给模型的错误 | 可恢复失败转为与 call 配对的 output |
| Fatal | 致命错误 | 内部 contract 或 runtime 失败，终止正常 feedback 流 |
| `AbortedToolOutput` | 已取消工具输出 | 用户取消后用于闭合 call 的结果 |
| terminal outcome | 终态 | Completed、Failed、Blocked 或 Aborted 等唯一生命周期结论 |
| cancellation token | 取消令牌 | 协作式通知 tool runtime 停止和清理 |
| parallel gate | 并行门 | 用 RwLock 协调 parallel-safe 与串行工具 |
| `FuturesOrdered` | 有序 future 流 | future 可并发执行，但结果按插入顺序 yield |
| in-flight | 执行中 | 已调度、尚未统一 drain 的工具 future |
| drain | 排空 | 等待并依次取出所有当前工具结果 |
| history | 会话历史 | 下一次 prompt 的主要 conversation state |
| rollout | 持久运行记录 | 用于恢复、审计和重放的 durable items |
| truncation policy | 截断策略 | 按 bytes 或近似 tokens 限制输出体积 |
| collection cap | 收集上限 | 进程输出进入 model truncation 前的缓冲限制 |
| omission marker | 省略标记 | 告诉模型中间有多少内容未保留 |
| telemetry truncation | 遥测截断 | 只限制日志 preview 的独立边界 |
| serialization budget | 序列化余量 | 为 JSON escaping 和 wrapper 开销预留的缓冲 |
| normalization | 归一化 | prompt 前修复配对并适配模型模态 |
| synthetic output | 合成输出 | 缺失真实 output 时插入的 aborted/empty 占位 item |
| UUID v5 | 名称派生 UUID | 用稳定 source ID 生成可重复 synthetic output ID |
| orphan output | 孤儿输出 | 找不到对应 call 的 output item |
| modality | 模态 | Text、Image、Audio 等输入形式 |
| placeholder | 占位文字 | 模型不支持某模态时说明内容被省略 |
| follow-up sampling | 后续采样 | 工具结果进 history 后再次请求模型继续推理 |
| `needs_follow_up` | 需要继续标记 | 当前 response 不是 Turn 终点 |
| prompt cache churn | Prompt 缓存扰动 | 等价历史因随机顺序/ID变化导致缓存 miss |
| idempotency | 幂等性 | retry 时避免重复副作用工具的重要性质 |

## 90. 最后压缩成一句话

Tool output 与 context feedback 的真正主线不是“工具返回字符串后模型立即继续”，而是：

> 模型的 Function/Custom/ToolSearch call 先以原 `call_id` 写入 history，再由 `ToolCallRuntime` 通过并行准入门、Registry、Hooks 和 handler 获得实现 `ToolOutput` 的具体结果；`AnyToolResult` 用原 payload 把它投影为匹配协议族的 `ResponseInputItem::*Output`，多个 future 虽可并行完成，却由 `FuturesOrdered` 按 call 顺序 drain 并写成 conversation `ResponseItem`；ContextManager 在记录与 prompt 投影阶段分别执行输出截断、call/output 补全、孤儿删除和模态降级；全部结果进入 history 后，外层 `run_turn` 才结合 pending input 与 token 状态决定 compaction，并发起 follow-up sampling，让模型基于完整、配对、有界的工具反馈继续工作。

下一篇计划精读 cancellation 与 interruption lifecycle：用户停止、steer、新 Turn、Session shutdown 和工具清理怎样争夺并统一终态。

返回[源码精读系列目录](README.md)或[课程总目录](../README.md)。
