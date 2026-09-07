# 源码精读 30：Agent Tool-Call Protocol State Machine——从流式片段、解析与 Gate，到并行执行、ToolResult 配对、修复与再次采样

> 源码基线：`ed6d543`
>
> 上一篇追到 Sampler 产出权威 `ConversationResponse`。本篇只研究一件事：模型给出的 Tool Call 怎样跨过协议、解析、策略、并发和失败边界，最终形成与 call ID 严格配对的 Tool Result，并成为下一次 Sampling 的输入。

---

## 1. 本篇解决什么问题

读完后应能回答：

- 流式 `ToolCallDelta` 为什么不能直接触发工具？
- 最终 Tool Call 在什么时候成为权威事实？
- 一个 Tool Call 为什么同时有模型 ID 和 ACP ID？
- 空参数、非法 JSON、连续 JSON 分别怎样处理？
- wire tool name、typed `ToolInput`、dispatch target 和 effective tool name 有何区别？
- Plan Gate、Hook、Permission 为什么必须在 dispatch 前串行执行？
- Policy Deny、用户 Reject、Cancel 和 Follow-up 为什么产生不同的 loop 结果？
- 一批 Tool Call 为什么是“串行准备、并行执行、完成序回收”？
- 为什么同一路径的读写会串行，而不同路径仍可并行？
- 成功、工具返回错误、Runtime error、解析错误、拒绝和取消怎样统一闭合协议？
- Tool Result 为什么可以不按原调用顺序返回？
- 图片、Skill reminder 和用户插话为什么要延迟到整批 Tool Result 之后？
- 中途崩溃或取消留下 dangling Tool Call 后，历史怎样修复？
- Structured Output 为什么借用了 Tool Call 外壳，却不进入普通 Tool Runtime？

---

## 2. 先给出完整状态机

```text
Provider SSE
   │ ToolCallDelta（展示事实，不可执行）
   ▼
terminal ConversationResponse
   │ 完整 id + name + arguments
   ▼
Assistant ToolCall 写入 ChatState
   │
   ▼
prepare_tool_call（逐个、串行）
   ├─ 注册 Pending UI item
   ├─ MCP readiness
   ├─ JSON normalization / recovery
   ├─ ToolBridge typed parse
   ├─ Plan edit gate
   ├─ PreToolUse hooks
   ├─ Permission decision
   └─ ExitPlan interception
          │
          ├─ 未批准：立即写配对 ToolResult
          ▼
PreparedToolCall
   │ 一批 approved calls
   ▼
parallel dispatch（同路径条件串行）
   │ FuturesUnordered：完成序回收
   ├─ success → ACP update + model ToolResult
   └─ error   → ACP Failed + model ToolResult
          │
          ▼
整批闭合后追加 deferred User followups
          │
          ▼
下一次 ConversationRequest / Sampling
```

这个状态机最核心的不变量是：

```text
每一个已提交给对话历史的 Assistant Tool Call，
最终都必须有一个使用同一 call ID 的 Tool Result。
```

工具是否真的执行成功，是结果内容；Tool Call 是否有结果，是协议结构。

---

## 3. 核心源码地图

| 领域 | 主要源码 |
| --- | --- |
| Agentic Loop 接回点 | `xai-grok-shell/src/session/acp_session_impl/turn.rs` |
| Tool Call 总状态机 | `xai-grok-shell/src/session/acp_session_impl/tool_calls.rs` |
| Prepared/ToolLoop 类型 | `xai-grok-shell/src/session/acp_session_impl/types.rs` |
| 具体 dispatch 路由 | `xai-grok-shell/src/session/acp_session_impl/tool_dispatch.rs` |
| 参数恢复辅助函数 | `xai-grok-shell/src/session/helpers/tool_input_parsing.rs` |
| ToolBridge | `xai-grok-tools/src/bridge.rs` |
| Tool Registry typed parse | `xai-grok-tools/src/registry/types.rs` |
| ToolInput 与 meta-dispatch | `xai-grok-tools/src/types/tool_io.rs` |
| Tool taxonomy | `xai-grok-tools/src/tool_taxonomy.rs` |
| 内部 ConversationItem | `xai-grok-sampling-types/src/conversation.rs` |
| Messages Tool Result 分组 | `xai-grok-sampling-types/src/conversation/messages.rs` |
| Responses 转换 | `xai-grok-sampling-types/src/conversation/responses.rs` |
| Chat Completions 转换 | `xai-grok-sampling-types/src/conversation/chat_completions.rs` |

---

# 第一部分：Tool Call 有三种“真实性”

## 4. 流式 delta 是展示事实

Sampler 收到 Provider 的参数片段后发出：

```rust
SamplingEvent::ToolCallDelta {
    tool_index,
    id,
    name,
    arguments_delta,
    ..
}
```

`SessionActor::handle_sampling_event` 只把它变成 `ToolCallDeltaChunk` 发给客户端。

它不调用 `ToolBridge`，也不触发 Permission 或 dispatch。

原因很简单：此时 arguments 可能只有：

```json
{"target_f
```

## 5. terminal Tool Call 才是执行事实

三种 Provider stream transform 都会在终态组装完整 Tool Call。

只有被 Session 接受的最终 `ConversationResponse` 才能进入：

```text
response.tool_calls()
  → ToolCallResponse
  → execute_tool_calls(...)
```

因此重试 attempt 流出的 delta 即使已显示，也绝不能执行。

## 6. ChatState 中的 Tool Call 是历史事实

Session 在执行工具前，先把最终 Assistant response 写入 ChatState。

这很重要：Tool Result 必须指向已经存在于历史中的 Tool Call，而不是先执行后补调用。

## 7. 三种真实性不能混用

| 层次 | 是否完整 | 是否持久化 | 是否可执行 |
| --- | --- | --- | --- |
| `ToolCallDelta` | 不保证 | 否 | 否 |
| terminal response Tool Call | 是 | 即将写入 | 是 |
| ChatState Assistant Tool Call | 是 | 是 | 已成为协议父节点 |

## 8. stream-drain barrier 仍然适用

Session 开始 Tool UI 与执行前，先等待 Sampling event drainer 排空。

否则可能出现：

```text
Tool Pending event
Assistant 最后一个文字 chunk
```

这会颠倒用户看到的因果顺序。

## 9. Backend Tool Call 是另一条通道

Provider 托管的搜索或代码执行也会产生 Backend Tool events。

这些事件主要驱动 ACP 展示和信号统计；本篇的本地 `execute_tool_calls` 状态机处理的是 client-side Function Tool Call。

---

# 第二部分：从 Turn Loop 进入 Tool Loop

## 10. Tool Call 从响应中提取

`turn.rs` 在收到可信响应后取得 `response.tool_calls()`，再记录 Assistant 和其他 response item。

这是“先提交模型输出、后运行副作用”的边界。

## 11. 内部类型被转成 ToolCallResponse

Turn Loop 将每个内部 `ToolCall` 映射成：

```rust
ToolCallResponse {
    id,
    kind: "function",
    function: ToolCallFunction { name, arguments },
}
```

这只是 Session Tool Runtime 的输入适配，不是又一次 Provider 转换。

## 12. ToolLoop 是控制流，不是工具输出

`ToolLoop` 包括：

- `Continue`
- `NonExistingTool`
- `ToolParsingError`
- `PermissionReject`
- `Cancelled`
- `FollowupMessage`
- `HookDenied`

它告诉外层 Turn Loop 接下来做什么；真正给模型看的内容是写入 ChatState 的 `ToolResult`。

## 13. 数据平面与控制平面分离

```text
ToolResult → 模型下一轮看到的事实
ToolLoop   → Session 自己怎样继续或结束
ACP update → 客户端怎样展示
Telemetry  → 运维怎样观察
```

同一个失败通常会同时触碰四条平面。

## 14. 为什么 NonExistingTool 仍可继续

工具不存在时，模型收到失败 Tool Result，外层通常继续 Sampling，让模型改用其他工具或修正名称。

它不是整个用户 Turn 的强制取消。

## 15. 为什么 HookDenied 也非终止

`ToolLoop::HookDenied` 的注释明确说它是 non-terminal。

Hook 的拒绝理由回填给模型后，模型仍有机会换一种行动。

---

# 第三部分：prepare_tool_call 是真正的协议门卫

## 16. 准备阶段为何逐个串行

`execute_tool_calls_batch` 先按模型原顺序调用 `prepare_tool_call`。

准备阶段可能：

- 等 MCP 初始化；
- 运行 Hook；
- 弹 Permission；
- 接收用户 Follow-up；
- 改变 Plan Mode。

这些都不能无序地并发争抢交互。

## 17. 第一步永远是注册 Pending UI item

`prepare_tool_call` 一开始就发 ACP `SessionUpdate::ToolCall`：

```text
title  = wire tool name
kind   = Other
status = Pending
raw_input = 能提前解析时才有
```

后续会用更丰富的 title、kind、location、diff 和 meta 更新它。

## 18. 为什么先注册再解析

即使参数解析失败，客户端也应该看到“哪个调用失败了”，而不是凭空出现错误文本。

Pending item 是 UI 生命周期的锚点。

## 19. 两类 call ID

`PreparedToolCall` 保存：

- `call_id: String`：模型协议 ID，用于 Tool Result matching；
- `tool_call_id: acp::ToolCallId`：ACP UI 更新的 typed ID。

它们通常文本相同，但服务于不同协议边界。

## 20. 空 ID 的局部兜底

并行完成回收时，如果模型 call ID 为空，代码会生成 `missing-call-id-{idx}` 作为事件 join key。

注意：这是观测/UI 侧的局部兜底；模型历史里的配对仍依赖原 call ID。正确的上游应提供稳定非空 ID。

## 21. 提前 raw_input 只是最佳努力

首次注册时使用普通 `serde_json::from_str(...).ok()`。

连续 JSON 恢复、空参数归一化和 typed parse 都发生在后面，所以首次 UI raw input 不一定等于最终 dispatch input。

## 22. Subagent background meta 的提前推断

对于 `task`、`Task`、`spawn_subagent`，注册阶段会从参数中的 `run_in_background` 或 `background` 推断 UI meta。

若字段缺失，默认按 background 处理。

---

# 第四部分：MCP readiness 先于普通解析

## 23. 为什么先识别 MCP-qualified name

MCP 工具名携带 server 信息。Session 先解析 server/tool，再决定是否刷新 managed MCP 或等待初始化。

## 24. Blocking 初始化策略

`McpInitStrategy::Blocking` 会等待 MCP 初始化完成，再继续准备。

适合工具集合必须在本轮稳定可用的场景。

## 25. Progressive 初始化策略

Progressive 模式不阻塞当前调用；若尚未初始化，则返回模型可行动的错误：

```text
Tool not available. Use search_tool to find available tools.
```

同时生成配对 Tool Result，并返回 `NonExistingTool`。

## 26. Managed MCP 的主动刷新

若 server 名带 managed 前缀，准备阶段调用 `refresh_managed_mcp_if_stale()`。

这发生在参数解析之前，因为 tool availability 本身就是解析语境的一部分。

---

# 第五部分：参数不是“一次 serde_json::from_str”

## 27. 第一层：空参数归一化

`normalize_empty_arguments` 将空串或全空白字符串变为：

```json
{}
```

零参数 MCP tool 常被模型输出成 `""`；语义上它更接近空 object，而不是 malformed JSON。

## 28. 非空参数原样保留

归一化函数不会 trim 或改写正常内容。

这避免“修复”逻辑悄悄改变字符串值或诊断证据。

## 29. 第二层：普通 JSON 解析

若 `serde_json::from_str::<Value>` 成功，所得 `Value` 进入 ToolBridge typed parse。

“是合法 JSON”并不等于“符合该 Tool schema”。

## 30. 第三层：连续 JSON 恢复

模型偶尔会输出：

```json
{"path":"a.rs"}{"path":"b.rs"}
```

`try_extract_concatenated_json_objects` 使用 `StreamDeserializer`，能正确处理嵌套花括号和字符串中的 `}{`。

## 31. 连续 JSON 的成立条件

辅助函数要求：

- trim 后以 `{` 开始；
- 整体不是合法单个 JSON；
- 能连续解析出至少两个 object；
- 遇到非 object 或错误即停止。

数组不是连续 JSON 恢复对象。

## 32. 代码意图：选择最匹配对象

准备代码会逐个调用 `bridge.try_parse(tool_name, obj)`，记录第一个能按目标 Tool 解析的 `selected_index`。

它还记录 `matched_named_tool`，便于日志诊断。

## 33. 基线中的实现偏差

源码基线里：

```rust
let best_match = objects[0].clone();
// loop 只更新 selected_index
...
best_match
```

也就是说，日志会报告匹配到的 index，但实际 `raw_input` 仍返回第一个 object。

这是源码事实，不应把注释中的“best matching”误写成已完全实现的行为；它也是适合补回归测试的候选点。

## 34. 恢复后只执行一个 object

即使检测到 20 个连续 object，也不会隐式执行 20 次工具。

一次模型 Tool Call 只能对应一个 Tool Result 和一次权限语义，静默拆成多次副作用会破坏协议与安全边界。

## 35. 成功结果中的纠错提醒

若使用连续 JSON 恢复，成功 Tool Result 会追加 `<system-reminder>`：

- 本次只执行一个对象；
- 其余被忽略；
- 模型应为剩余操作分别发独立 Tool Call。

## 36. 最后兜底：raw wrapper

如果既不是合法 JSON，也不能恢复为连续 object，输入被包成：

```json
{"raw":"原始参数"}
```

这让某些接收原始文本的工具仍有解析机会，同时保留诊断证据。

## 37. ToolBridge typed parse 才是 schema 边界

`ToolBridge::try_parse` 根据 wire name 找到 registry entry，再把 `Value` 解析成具体 `ToolInput` variant。

从这一刻开始，下游不再只是处理任意 JSON，而是在处理有语义的命令。

## 38. 解析失败必须成为 Tool Result

`handle_tool_parse_error` 会：

- 记录结构化错误；
- 发 ACP Failed update；
- 生成包含原始参数诊断的模型可见消息；
- 使用原 call ID 写入 Tool Result。

随后返回 `ToolParsingError`，让模型下一轮修正参数。

## 39. 为什么不能只返回 Rust Err

若函数只 `return Err(...)`，模型历史中会留下 Assistant Tool Call，却没有 Tool Result。

下一次 Provider 转换可能拒绝该历史，模型也不知道该如何修正。

---

# 第六部分：一个工具有四种名字

## 40. wire name

模型在 Function Call 中写出的名称，例如 `use_tool`。

它决定初始 Registry lookup 和模型协议中的可见身份。

## 41. typed ToolInput

`ToolBridge` 解析出的 Rust enum variant。

它承载 AccessKind、展示、dispatch 和 tool taxonomy 所需的语义。

## 42. dispatch target name

Meta-dispatch 工具会转发到另一个目标，例如：

```text
wire name: use_tool
target:    某个被搜索到的 MCP tool
```

`ToolInput::dispatch_target_name()` 为普通工具返回 `None`，为 meta tool 返回真实目标。

## 43. effective tool name

执行结果还能返回 `effective_tool_name` override。

最终优先级是：

```text
ToolRunResult.effective_tool_name
  ?? Prepared.dispatch_target_name
  ?? Prepared.tool_name
```

## 44. Hook 看哪个名字

`PreparedToolCall::hook_tool_name()` 选择 dispatch target，否则使用 wire name。

因此 PostToolUse Hook 能匹配真正执行的工具，而不只是 `use_tool` 这个壳。

## 45. 错误消息保留两层身份

若 effective name 与 requested name 不同，错误写成近似：

```text
Tool `<effective>` failed via `<requested>`: ...
```

这同时回答“谁失败了”和“模型通过什么入口调用”。

---

# 第七部分：Gate 的精确顺序

## 46. AccessKind 从 typed input 推导

只有 typed parse 成功后，Session 才能可靠区分：

- Read
- Edit
- Bash
- Grep
- MCPTool
- WebFetch
- WebSearch

Permission 不是按工具名字猜测副作用。

## 47. Plan edit gate 最先判断

在 Plan Mode 中，非 plan file 的编辑会在通用 Permission 前被拒绝。

理由是 Plan Mode 是更高层的交互状态约束；即使普通 permission 会允许写，也不能绕过它。

## 48. Plan Gate 拒绝如何闭合

`handle_tool_not_executed`：

- 将 ACP ToolCall 标为 Failed；
- 把原因作为 content；
- 写入同 ID Tool Result。

控制流返回 `Continue`，让模型改写计划或改用只读操作。

## 49. send_tool_call_start 丰富 UI

通过 Gate 后，Session 根据 typed `ToolInput` 生成：

- 人类可读 title；
- ACP ToolKind；
- file locations；
- diff/content preview；
- canonical raw input/meta。

这覆盖最初只有 wire name 的 Pending skeleton。

## 50. PreToolUse host hook

Host hook 收到：

- resolved tool name；
- tool use ID；
- 截断后的 input；
- 是否发生截断；
- subagent type。

Hook deny 会经 `deny_tool` 形成失败更新与配对 Tool Result。

## 51. Client hook 在 host hook 之后

Host registry 未拒绝时，还可能运行 client-side PreToolUse hook。

两者共享“拒绝也必须闭合 Tool Call”的要求。

## 52. Hook payload 为什么截断

Hook 是扩展边界。把任意巨大 Tool JSON 原样复制给每个 Hook 会造成内存、日志和外部请求放大。

截断位同时告诉 Hook：当前看到的并非完整输入。

## 53. Plan file edit 自动批准

如果 AccessKind 是对当前 plan file 的 Edit，Plan Mode 可自动批准。

这是“只允许写计划文件”的正向配套，不是绕过所有 Permission。

## 54. Permission 前刷新 classifier transcript

Auto permission mode 会从当前 Conversation 构造最近 turns，写入 classifier transcript。

权限分类因此基于最新上下文，而不是 Session 启动时的旧快照。

## 55. PendingInteractionGuard

Permission 等待期间注册 pending interaction，使多客户端、取消和 reconnect 能识别“当前正在等用户决定”。

Guard 离开作用域时清理该状态。

## 56. Decision::Allow 与 Ask

在这一层，两者都进入执行路径。

`Ask` 表示权限系统经过询问后批准，而不是“仍然没决定”。

## 57. PolicyDeny 与 Reject 的关键差异

两者都会：

- 标记 Tool Failed；
- 写配对 Tool Result；
- 触发 PermissionDenied Hook。

但控制流不同：

```text
PolicyDeny → ToolLoop::Continue
Reject     → ToolLoop::PermissionReject
```

## 58. 为什么 PolicyDeny 可让模型继续

Policy 是系统边界。模型可以在边界内选择另一种方案，所以把拒绝原因作为行动反馈继续推理。

## 59. 为什么用户 Reject 终止本 Turn

用户明确否定了当前行动。继续自动尝试相邻副作用，可能违背用户意图。

外层将它转成 `TurnOutcome::Cancelled`，附带 tool name 与 reason。

## 60. Cancelled 与 Reject 不是一回事

`Cancelled` 表示交互被中断，例如等待 Permission 时 Cmd+C。

它同样写 Tool Result，但 TurnOutcome 分类为 `PermissionCancelled`，便于 UI 和遥测区分。

## 61. FollowupMessage 是“改题”

用户可以不批准工具，而是提供下一步文字。

当前 Tool Call 先得到“未执行”结果；随后 Follow-up 作为新的 User turn 加入，Agent Loop 继续 Sampling。

## 62. ExitPlan 是准备期拦截器

ExitPlan 不是普通函数调用那么简单：它可能读取 plan file、等待用户选择并切换交互模式。

因此它在 dispatch 前被特殊处理，结果仍必须使用原 call ID 闭合。

---

# 第八部分：为何 ExitPlan 被移到 batch 尾部

## 63. split_exit_plan_tail

当一轮含多个 Tool Call 时，`execute_tool_calls` 按 ToolKind 将 ExitPlan 调用拆到 tail batch。

判断基于 taxonomy 的 `ToolKind::ExitPlan`，不硬编码 wire alias。

## 64. 一个典型危险批次

模型可能同时输出：

```text
1. write plan file
2. exit_plan_mode
```

若二者完全并行，ExitPlan 可能在计划写完前读取文件。

## 65. body 与 tail 都走同一状态机

ExitPlan 不是另写一套执行器。

它只改变 batch 边界：先完成 body，再准备/执行 tail，因此复用相同的 pairing、事件和失败规则。

---

# 第九部分：准备失败怎样影响同批兄弟调用

## 66. 非终止准备失败

以下错误通常只影响当前调用：

- NonExistingTool
- ToolParsingError
- HookDenied
- Plan/Policy denial 返回 Continue

它们已各自生成 Tool Result，其他兄弟仍可准备和执行。

## 67. 终止型准备结果

以下结果写入 `final_result`：

- PermissionReject
- Cancelled
- FollowupMessage

## 68. 后续兄弟为何不再准备

一旦 `final_result` 存在，后续调用不会再弹权限、运行 Hook 或 dispatch。

但它们仍然已经存在于 Assistant response 中，不能直接丢弃。

## 69. 取消兄弟也要补 Tool Result

代码为每个后续调用写入解释性结果，例如：

```text
Tool execution cancelled due to earlier permission rejection ...
```

这正是“业务未执行，但协议已完成”。

## 70. final_result 只保留第一个终止原因

第一个用户 Reject/Cancel/Follow-up 决定整个 batch 的外层结果。

后续调用只是被取消，不应覆盖根因。

---

# 第十部分：并行执行不是无约束并发

## 71. approved 是准备阶段的输出

只有完成所有解析、Gate、Hook 和 Permission 的调用，才变成 `PreparedToolCall` 放入 approved。

因此 dispatch future 不再需要重复做交互决策。

## 72. PreparedToolCall 是安全快照

它固定：

- 原 call ID 与 ACP ID；
- wire name 与 dispatch target；
- 原始参数与 parsed args；
- model ID；
- concatenated JSON count；
- read-only 分类。

## 73. 为什么 dispatch 才并行

执行阶段通常是文件 I/O、进程、网络或 MCP RPC，等待占比高，并行能显著降低一批独立工具的总延迟。

## 74. read-only 分类来自 taxonomy

Tool metadata/ToolKind 给出 `is_read_only`。

它不是只看 AccessKind 字符串，而是工具注册时声明的能力事实。

## 75. write_paths 怎样建立

代码只从非 read-only prepared calls 中提取 `lock_path_for_args(parsed_args)`，形成 `write_paths` set。

## 76. 为什么读也可能拿锁

创建锁映射时，只要某调用的 path 出现在 `write_paths`，该调用就拿同一个 path mutex。

因此：

```text
read A + write A → 串行
write A + write A → 串行
read A + read A → 通常并行
write A + write B → 并行
```

## 77. 这是批内路径锁，不是全局事务

锁 map 在 `execute_tool_calls_batch` 内创建。

它防止当前模型 batch 中显然冲突的文件操作，但不等于跨 Session、外部进程或全工作区的数据库事务。

## 78. 无法提取路径时的限制

若参数 shape 不含 `lock_path_for_args` 能识别的路径，即使工具实际会写同一资源，也不会获得这层序列化。

正确 metadata 和 typed args 仍然重要。

## 79. FuturesUnordered 的含义

每个 approved call 变成 future，加入 `FuturesUnordered`。

它按完成顺序 yield，而不是按原 vector 顺序等待。

## 80. idx 防止完成序丢失身份

每个 future 返回：

```text
(original approved index, result, duration_ms)
```

Actor 再用 `approved_slots[idx].take()` 找回对应 `PreparedToolCall`。

## 81. take() 固定 exactly-once 回收

若同一 idx 完成两次，第二次会触发断言。

这把“每个 future 只能 finalize 一次”编码成运行时不变量。

## 82. 为什么另起 drainer task

drainer 持续消费 `FuturesUnordered`，通过 unbounded channel 把完成项送回 Session 侧 finalize loop。

`AbortOnDrop` 确保外层退出时 drainer 不会成为孤儿任务。

## 83. Tool Result 按完成序写入

`handle_bridge_tool_success` 和 `handle_tool_error` 在收到 completion 时立即 push Tool Result。

所以快工具的结果可能先于模型原列表中的慢工具。

## 84. 为什么完成序是合法的

并行 Tool Result 的语义关联键是 `tool_call_id`，不是数组位置。

Provider converter 会按 ID 表达每个 result；它不应把“第一个结果”误当成“第一个调用”。

## 85. 原调用顺序仍被保留在哪里

Assistant response 中的 Tool Call 列表顺序保持模型输出顺序。

变化的只是后续 Tool Result 到达顺序。

## 86. 共享 auth recovery

并发工具共用 `OnceCell<bool>`。

多个调用同时遭遇 401 时，只执行一次 credential recovery 决策，其他调用复用结果，避免刷新风暴。

## 87. Managed MCP 还有一次 reactive retry

若 managed MCP 返回认证拒绝，Session 可执行 reactive reauth 后重试该调用一次。

重试耗时累加到原 duration，事件也记录新的 started/update 事实。

## 88. 可中断 wait tool

被判定为 interruptible wait 的工具使用 biased `tokio::select!`：

- 一边等待工具完成；
- 一边等待 pending interjection。

若用户新消息先到，返回一个合成的 interrupted-wait ToolRunResult，而不是留下无结果调用。

---

# 第十一部分：成功结果怎样进入两个世界

## 89. ToolRunResult 不只是字符串

成功 dispatch 返回的结果含：

- typed output；
- model-facing `prompt_text`；
- optional effective tool name；
- tool-layer images 等附加信息。

## 90. ACP output 与 model output 可以不同

`acp_tool_update` 把 typed output 变成适合 UI 的状态、content、location 或 diff。

`prompt_text` 则变成模型下一轮看到的 Tool Result。

不要假设 UI 文本就是模型上下文原文。

## 91. 工具“成功返回错误输出”

Dispatch 的 Rust `Result` 可以是 `Ok(ToolRunResult)`，但 typed output 自身 `is_error()`。

这类结果仍走 bridge success finalize，因为调用协议成功返回了一个结构化工具结果；ACP status 和 telemetry 会按 output error 标失败。

## 92. Hard execution error

只有 dispatch/validation 直接返回 `Err(ToolError)` 才进入 `handle_tool_error`。

它构造：

- ACP Failed；
- `raw_output.error = tool_execution_failed`；
- 模型可见 Tool Result；
- failure signals。

## 93. 错误会做路径重写

`handle_tool_error` 使用 path rewriter 改写错误中的路径，避免把内部真实路径错误地暴露给使用 display workspace 的客户端或模型。

## 94. 成功结果也做路径重写

`handle_bridge_tool_success` 对 prompt text 使用 `maybe_rewrite`。

路径表示必须在成功和失败两条路径一致。

## 95. 普通文本 Tool Result

无 inline image 时：

```rust
ConversationItem::tool_result(call_id, prompt_text)
```

它是最常见的闭合形式。

## 96. 带图片的 Tool Result

读取图片或 PDF page 时，会构造 `tool_result_with_images`。

模型协议转换层再分别映射到 Responses/Messages/Chat Completions 可接受的 shape。

## 97. 图片验证发生在回填前

图片可能被判断为太小、不可读或可附加。

不可附加时，Tool Result 仍存在，只是文本解释为何没有视觉内容；协议闭合不依赖图片成功。

## 98. Base64 图片抽取产生 deferred followup

某些工具把 base64 图片混在文本中。Runtime 从 prompt text 抽取并标准化后，将它们转成带图片的 User messages。

这些消息不立即 push。

## 99. Skill update 也产生 deferred followup

工具完成后 `apply_pending_skill_update()` 可能生成 skill reminder。

它同样先进入 `deferred_followups`，并可能更新 available commands。

## 100. 为什么 followup 必须延迟

假设模型一次发出两个 Tool Call：

```text
Assistant: call_A, call_B
ToolResult A
User: [Image extracted ...]  ← 若立即插入
ToolResult B
```

User item 会切断并行 Tool Result run，某些 Provider wire format 要求相邻分组，历史修复和裁剪也更困难。

因此真实顺序是：

```text
Assistant: call_A, call_B
ToolResult A/B（完成序）
ToolResult B/A
User followups
```

## 101. PostToolUse 在 Tool Result 处理后运行

成功结果被 drain、UI 更新并回填后，Session 运行 PostToolUse Hook。

输入与结果都经过 payload truncation，并携带 truncation flags。

## 102. PostToolUseFailure 只针对 hard error

Dispatch `Err` 路径运行 PostToolUseFailure Hook，携带 resolved hook name、原 input 与格式化错误。

结构化 error output 仍属于成功返回路径，其状态由 typed output 表达。

---

# 第十二部分：Tool Result pairing 是全路径不变量

## 103. 正常成功

```text
Tool Call(id=X) → Tool Result(id=X, output)
```

## 104. Typed output error

```text
Tool Call(id=X) → Tool Result(id=X, error-shaped prompt_text)
```

## 105. Hard dispatch error

```text
Tool Call(id=X) → Tool Result(id=X, "Tool ... failed")
```

## 106. Parse error

```text
Tool Call(id=X) → Tool Result(id=X, schema/argument diagnosis)
```

## 107. Plan/Policy/Hook denial

```text
Tool Call(id=X) → Tool Result(id=X, not executed + reason)
```

## 108. 用户 Reject/Cancel/Follow-up

当前调用和被连带取消的兄弟调用都各自获得 Tool Result。

## 109. “结果”不等于“执行产物”

对协议来说，以下句子也是合法结果：

```text
The tool was not executed because permission was denied.
```

它回答了模型此前悬而未决的调用。

## 110. 为什么每条错误路径都要审计 pairing

新增 `return`、`?` 或 timeout 分支时，最容易漏掉的不是 UI update，而是 ChatState `push_tool_result`。

Review Tool Runtime 改动时应逐分支检查：

```text
这个 call 已写入 Assistant history 吗？
如果是，此分支在哪里写同 ID Tool Result？
```

---

# 第十三部分：从 Tool Result 再次 Sampling

## 111. execute_tool_calls 返回后

若结果不是强终止，Turn Loop 增加 `tool_turn_count`，检查 max turns 与 preflight context overflow，然后回到 Sampling loop。

## 112. 下一次请求包含什么

概念上：

```text
...旧历史
Assistant Tool Calls
Tool Results
deferred User followups（若有）
```

ChatState 再将内部 Conversation 转成目标 Provider request。

## 113. Tool Result 顺序由 converter 适配

例如 Messages converter 会暂存连续 Tool Result blocks，再把它们作为 User content blocks flush。

内部 role/item 模型与 wire message 模型不必一一对应。

## 114. Responses API 的配对

Responses converter 将内部 Tool Result 转为 function-call output item，并保留 call ID。

关联仍靠 ID，不靠完成顺序。

## 115. Chat Completions 的配对

Chat Completions 使用 tool-role message 与 `tool_call_id` 指回 Assistant call。

同样不能丢失或重写 ID。

## 116. 模型如何从失败恢复

下一次 Sampling 中，模型能看到：

- 哪个工具失败；
- 输入为何不合法；
- 哪条 policy/plan/hook 阻止了执行；
- 是否只执行了连续 JSON 中的一个对象；
- 用户是否提供了新的 follow-up。

这使错误从 Runtime exception 变成 Agent 可推理的 observation。

---

# 第十四部分：Structured Output 是特殊 Tool 协议

## 117. 为什么用 Tool Call 表达结构化输出

Schema 可以被渲染成一个合成 `StructuredOutput` function。

模型通过函数参数提交 JSON，复用 Provider 已有的 typed arguments 通道。

## 118. 它为何不进入普通 dispatch

`turn.rs::handle_structured_output_tool_call` 在 `execute_tool_calls` 前拦截它。

它不对应文件、Shell、MCP 或网络副作用，只需要 schema validation。

## 119. 必须单独调用

如果 `StructuredOutput` 与其他工具混在一批：

- 每个 StructuredOutput call 得到纠错 Tool Result；
- 从本批移除 StructuredOutput；
- 其余普通工具继续执行。

纠错内容要求它在其他工具完成后单独调用且只调用一次。

## 120. Validation retry

参数不符合 schema 且未耗尽 retry budget 时：

- 写配对 Tool Result，包含 validation error；
- 要求修复参数后再次调用；
- 返回 `StructuredOutputStep::Retry`，直接重新 Sampling。

## 121. 最终接受也写 Tool Result

验证成功时写：

```text
Structured output accepted.
```

然后返回 `TurnOutcome::Completed { structured_output: Some(...) }`。

即使不会再向模型采样，历史仍保持协议完整。

## 122. retry 耗尽时的语义

达到上限后，最后一次 validation error 仍写 Tool Result，`Complete(Err(...))` 将错误作为结构化输出终态上交，而不是无限循环。

---

# 第十五部分：崩溃、取消与历史修复

## 123. 正常路径尽量立即闭合

本篇前面的每个分支都在当前状态机内主动写 Tool Result。

但进程崩溃、任务被 abort 或旧版本 bug 仍可能在“Assistant call 已提交、Result 未写”之间截断。

## 124. dangling Tool Call

若某 Assistant Tool Call ID 在后续找不到对应 Tool Result，它就是 dangling。

并行 batch 中可能只有一部分 dangling：已完成兄弟仍保留真实结果。

## 125. repair_dangling_tool_calls

`xai-grok-sampling-types::conversation::repair_dangling_tool_calls` 扫描历史，为缺失 ID 插入合成 Tool Result。

原因由 `DanglingToolCallReason` 表达，例如用户取消或 harness halted。

## 126. 修复不覆盖真实结果

修复只补缺失 ID。

如果 call_A 已成功而 call_B 因取消缺失，结果是：

```text
call_A → 原真实结果
call_B → 合成取消结果
```

## 127. 修复为何不在任意 read 时执行

工具仍在 flight 时，暂时没有结果是正常状态。

若每次读取 Conversation 都修复，会把慢工具提前判成取消，并可能随后产生 duplicate result。

因此修复只应发生在明确的安全写边界。

## 128. duplicate Tool Result 清理

`dedup_duplicate_tool_results` 在同一调用区间内按 call ID 删除重复项。

这处理修复结果与迟到真实结果、旧 bug 或重复提交造成的冲突。

## 129. 去重不是跨全历史随便删

Call ID 的作用域与 Assistant call run 有关。

实现按调用区间工作，避免两个独立历史段恰巧复用字符串 ID 时误删。

## 130. Provider 转换还有最后防线

历史中的 malformed Tool arguments 在 outbound conversion 时会被安全规范化，例如必要时用 `{}`，以免 Provider 因非法 JSON 拒绝整个恢复请求。

这不是鼓励保存坏参数，而是保证旧历史仍可继续。

---

# 第十六部分：顺序不变量总结

## 131. 必须保持的顺序

```text
terminal response accepted
  < Assistant Tool Call committed
  < paired Tool Result committed
  < deferred User followup
  < next Sampling request
```

## 132. 不要求保持的顺序

同一 parallel batch 内，不要求 Tool Result 按 Tool Call 原列表顺序到达。

要求的是：每个 Result 的 call ID 正确且整批在后续 User followup 前闭合。

## 133. UI 顺序与模型历史顺序不同

流式 delta、Pending、Started、Progress、Completed 是客户端体验时间线。

Assistant ToolCall 与 ToolResult 是模型协议时间线。

二者相关但不可当作同一个日志流。

## 134. Permission 顺序与 execution 顺序不同

Permission/Hook preparation 按模型顺序串行；获得批准后的 execution 才并行。

这既避免多个交互同时弹出，又保留独立 I/O 的吞吐。

---

# 第十七部分：三个逐步 Walkthrough

## 135. Walkthrough A：两个独立读取

模型输出：

```text
call_A read a.rs
call_B read b.rs
```

链路：

1. terminal response 写入两个 Assistant Tool Calls；
2. A、B 依次 typed parse；
3. 两者均通过 Hook/Permission；
4. 因都 read-only 且无 write path，两个 future 并行；
5. B 先完成，先写 `ToolResult(call_B)`；
6. A 后完成，再写 `ToolResult(call_A)`；
7. 下一次 Sampling 按 ID 看懂两个结果。

## 136. Walkthrough B：同文件读写

模型输出：

```text
call_A write src/lib.rs
call_B read  src/lib.rs
```

链路：

1. write 调用使 `src/lib.rs` 进入 `write_paths`；
2. A、B 都从 file lock map 获得同一个 mutex；
3. future 仍同时创建，但进入 dispatch 前竞争同一锁；
4. 同文件观察被串行化；
5. 两者各写自己的 Tool Result。

注意：谁先获得 mutex 由调度决定，代码不是显式 FIFO 事务队列。

## 137. Walkthrough C：第一个调用被用户拒绝

模型输出三次工具调用。

1. call_A 注册 Pending、解析、弹 Permission；
2. 用户 Reject；
3. A 写 Failed update 和 `ToolResult(A, rejected)`；
4. `final_result = PermissionReject`；
5. B、C 不再 prepare/dispatch；
6. B、C 各写 `ToolResult(..., cancelled due to earlier rejection)`；
7. 外层 TurnOutcome 为 Cancelled；
8. 历史没有悬空 Tool Call。

---

# 第十八部分：阅读源码时最容易产生的误解

## 138. 误解：看见 ToolCallDelta 就可以执行

错误。Delta 只是流式展示，只有 terminal response 是权威执行输入。

## 139. 误解：JSON 合法就代表 Tool Call 合法

错误。还必须经过 Registry lookup 和 typed `ToolInput` parse。

## 140. 误解：Permission 拒绝就不需要 Tool Result

错误。未执行是业务结果，仍要闭合模型协议。

## 141. 误解：并发执行必须按原顺序写结果

错误。关联靠 call ID；强制 head-of-line waiting 只会增加延迟。

## 142. 误解：read-only 调用永远不加锁

错误。若同批存在对相同 path 的 writer，reader 也会使用该 path lock。

## 143. 误解：Ok(ToolRunResult) 就一定是成功

错误。typed output 可以 `is_error()`，ACP 和 telemetry 会标失败。

## 144. 误解：Hook name 永远是模型看到的 name

错误。Meta-dispatch 工具的 Hook 应看到 resolved target。

## 145. 误解：deferred followup 只是性能优化

错误。它主要保护 Tool Call/Tool Result adjacency 和下一次 Provider 请求的合法结构。

## 146. 误解：历史修复可随时运行

错误。in-flight 缺结果是正常的；修复必须选择安全边界。

---

# 第十九部分：如何验证这些结论

## 147. 先跑参数恢复单测

```sh
cargo test -p xai-grok-shell \
  session::helpers::tool_input_parsing::tests
```

重点观察：

- empty/whitespace → `{}`；
- 嵌套 object 连续解析；
- 数组不被误判；
- 截断 JSON 不被当成多对象。

## 148. 再跑 Tool Result 转换测试

```sh
cargo test -p xai-grok-sampling-types conversation
```

重点搜索：

- multiple tool results；
- parallel batch 后 followups；
- tool result with images；
- dangling repair；
- duplicate result dedup。

## 149. 检查 ExitPlan tail 单测

```sh
cargo test -p xai-grok-shell exit_plan_tail_predicate_tests
```

它验证判断基于 ToolKind，并保持 body/tail 相对顺序。

## 150. 建议补的回归测试

最值得补的是连续 JSON “第二个 object 才匹配 schema”的测试：

```text
object[0] 不匹配目标 tool
object[1] 匹配目标 tool
期望实际 dispatch object[1]
```

在当前基线下，这个测试应暴露第 33 节记录的实现偏差。

## 151. 并发测试应该断言什么

不要断言两个独立工具固定谁先完成。

应断言：

- 两个 call ID 都恰有一个 result；
- followup 位于所有 results 之后；
- 同路径 writer/reader 不重叠关键区；
- 不同路径允许重叠；
- 取消后未执行兄弟仍有 synthetic result。

---

# 第二十部分：可复用的 Agent Runtime 设计原则

## 152. 把 Tool Call 当协议状态机，不是函数调用

函数调用只有 input/output；Agent Tool Call 还包含：

- 流式展示；
- 权威终态；
- 解析恢复；
- 策略 Gate；
- 用户交互；
- 并行调度；
- 历史配对；
- 崩溃修复。

## 153. 先提交意图，再提交观察

Assistant Tool Call 是模型提出的行动意图；Tool Result 是环境观察。

这两类事实都应进入历史，失败不能抹掉意图。

## 154. 安全决策必须发生在 typed boundary 之后

只有解析成 `ToolInput`，系统才知道真正的 path、command、URL 或 MCP target。

基于 raw name 的权限判断太脆弱。

## 155. 拒绝应可推理

Policy、Hook、Plan 和 Permission 不应只返回机器错误码。

给模型一个精确、可行动的 Tool Result，Agent 才能在安全边界内继续完成目标。

## 156. 并发正确性依赖身份，不依赖位置

用稳定 call ID join request/result，才能允许完成序回收、partial completion 和针对性 repair。

## 157. 延迟非 ToolResult 消息

任何可能产生 User/System followup 的工具后处理，都应避免切入尚未闭合的并行 Tool Result run。

## 158. Repair 是保险，不是正常控制流

正常状态机应在每条退出路径立即配对；repair 只负责 crash、abort、旧历史和极端竞态。

## 159. 观测 ID 兜底不能替代协议 ID

为日志生成 synthetic join key 可以改善诊断，但不能修复 Provider history 中缺失或重复的 call ID。

## 160. 注释、日志与实际返回值要一起读

连续 JSON 恢复展示了一个典型教训：日志说“selected index”，不代表该对象真的被赋给返回变量。

源码精读要沿数据变量追到底，而不是停在注释或 span 字段。

---

## 161. 一页纸总结

```text
1. ToolCallDelta 只展示，terminal Tool Call 才执行。
2. Assistant Tool Call 先写历史，Tool Result 后写历史。
3. prepare 串行：MCP → normalize → typed parse → Plan → Hook → Permission。
4. 任何 prepare 失败都必须写同 ID Tool Result。
5. 用户 Reject/Cancel/Follow-up 会取消后续兄弟，但兄弟也要补结果。
6. approved calls 并行执行；同写路径用 batch-local mutex 协调。
7. FuturesUnordered 按完成序 finalize，结果靠 call ID 关联。
8. success、typed error、hard error 都落为模型可见 Tool Result。
9. 图片、Skill reminder、interjection 等 followup 在整批结果后追加。
10. 下一轮 Sampling 把 Tool Results 当环境 observation。
11. StructuredOutput 复用 Tool Call 外壳，但在普通 dispatch 前验证。
12. crash 留下 dangling call 时，在安全边界补 synthetic result 并去重。
```

---

## 162. 本篇术语表

| 名词 | 白话解释 | 在本篇中的精确含义 |
| --- | --- | --- |
| Tool Call | 工具调用 | Assistant 输出的 call ID、工具名和 JSON arguments |
| Tool Result | 工具结果 | 使用相同 call ID 回答某个 Tool Call 的环境观察 |
| pairing | 配对 | Tool Call 与 Tool Result 通过 call ID 建立一一对应 |
| call ID | 调用标识 | 跨 Assistant call、Tool Result 和 Provider wire 的关联键 |
| ACP ToolCallId | 客户端协议调用 ID | 用于 Pending/Progress/Completed 等 UI 更新的 typed ID |
| ToolCallDelta | 工具调用增量 | Provider stream 中尚未完整的 id/name/arguments 片段 |
| terminal Tool Call | 终态工具调用 | 最终可信 ConversationResponse 中的完整调用 |
| authoritative | 权威的 | 可提交历史、驱动副作用且不会被后续 retry attempt 替代 |
| ToolLoop | 工具循环控制结果 | 告诉 Turn Loop 继续、取消或加入 Follow-up 的 enum |
| data plane | 数据平面 | Tool Result 等真正进入模型历史的内容 |
| control plane | 控制平面 | ToolLoop、Actor command、取消和生命周期协调 |
| UI plane | 展示平面 | ACP Pending/Started/Progress/Failed/Completed 更新 |
| observability plane | 可观测平面 | tracing、events、signals 和 telemetry |
| prepare | 执行前准备 | 解析、Gate、Hook、Permission 与特殊拦截阶段 |
| dispatch | 分派执行 | 将 PreparedToolCall 交给具体 Tool Runtime |
| finalize | 完成处理 | 更新 UI、写 Tool Result、Hook、signals 与 telemetry |
| wire name | 线上工具名 | 模型 Function Call 中请求的名字 |
| ToolInput | 类型化工具输入 | JSON 经 Registry 解析后的 Rust enum variant |
| dispatch target | 分派目标 | `use_tool` 等 meta tool 最终转发到的真实工具 |
| effective tool name | 有效工具名 | 结果 override、dispatch target、wire name 按优先级得到的最终身份 |
| meta-dispatch tool | 元分派工具 | 自己是模型入口，但内部再选择真实目标的工具 |
| normalization | 归一化 | 将空参数等语义等价输入变成统一 JSON shape |
| concatenated JSON | 连续 JSON | 多个 object 被错误拼在同一个 arguments 字符串中 |
| StreamDeserializer | 流式 JSON 反序列器 | 可依次读取多个 JSON value，避免用字符串硬拆花括号 |
| raw wrapper | 原始值包装 | 将无法解析的参数包成 `{"raw": ...}` 再交给 typed parse |
| schema boundary | Schema 边界 | JSON 从任意 Value 变成具体 ToolInput 的校验点 |
| Gate | 门控 | 决定调用是否允许进入下一阶段的检查 |
| Plan Gate | 计划模式门控 | Plan Mode 中只允许特定编辑行为的约束 |
| PreToolUse | 工具前 Hook | dispatch 前可观察或拒绝调用的扩展事件 |
| PostToolUse | 工具后 Hook | 成功返回结构化结果后触发的扩展事件 |
| PostToolUseFailure | 工具失败后 Hook | hard dispatch error 后触发的扩展事件 |
| Permission | 权限决策 | 根据 AccessKind、policy 和用户决定是否允许副作用 |
| AccessKind | 访问类型 | Read、Edit、Bash、MCP、Web 等安全语义分类 |
| PolicyDeny | 策略拒绝 | 系统策略禁止，但允许模型收到反馈后换方案 |
| Reject | 用户拒绝 | 用户明确否定当前动作，终止当前 Turn 的工具循环 |
| Cancelled | 已取消 | 权限交互被中断，而非用户给出否定理由 |
| FollowupMessage | 跟进消息 | 用户用新文字代替批准，作为后续 User turn 注入 |
| PendingInteractionGuard | 等待交互守卫 | 登记 Permission 等待状态并在离开作用域时清理 |
| PreparedToolCall | 已准备调用 | 固定了解析、权限、目标和元数据的 dispatch 安全快照 |
| approved batch | 已批准批次 | 所有通过 prepare 的调用集合 |
| FuturesUnordered | 无序 Future 集合 | 谁先完成就先 yield 谁的并发容器 |
| completion order | 完成顺序 | 并发调用实际完成并写结果的先后 |
| original order | 原始顺序 | Assistant response 中 Tool Call 列表的先后 |
| head-of-line blocking | 队头阻塞 | 为等前面的慢任务而延迟后面已完成任务 |
| batch-local lock | 批内锁 | 只协调当前 Tool batch 的同路径冲突 mutex |
| write path | 写路径 | 非只读调用参数中可提取的目标文件路径 |
| read-only | 只读 | Tool metadata/taxonomy 声明不会修改目标状态 |
| OnceCell | 单次初始化单元 | 并发调用共享一次 auth recovery 结果的同步原语 |
| reactive reauth | 反应式重认证 | 收到 managed MCP 认证拒绝后刷新并单次重试 |
| interruptible wait | 可打断等待 | 新用户插话到来时能提前结束的 wait tool |
| typed output error | 类型化错误输出 | Rust Result 成功，但 ToolOutput 自身表示失败 |
| hard error | 硬执行错误 | dispatch 直接返回 `Err(ToolError)` |
| deferred followup | 延迟跟进项 | 等整批 Tool Results 闭合后才写入的 User message |
| adjacency | 相邻性 | Assistant Tool Calls 后保持 Tool Results 连续，避免被 User item 切断 |
| StructuredOutput | 结构化输出工具 | 用 function arguments 承载 schema JSON 的合成工具 |
| dangling Tool Call | 悬空工具调用 | 历史中没有同 ID Tool Result 的 Assistant Tool Call |
| synthetic Tool Result | 合成工具结果 | 为取消、崩溃或未执行调用补写的协议结果 |
| repair | 修复 | 在安全边界为 dangling calls 补结果 |
| dedup | 去重 | 删除同一调用区间内重复 call ID 的 Tool Results |
| safe boundary | 安全边界 | 能确认没有工具仍在 flight、可以修复历史的写入时点 |
| resampling | 再次采样 | 将 Tool Results 加入 Conversation 后再次请求模型 |
| observation | 环境观察 | 模型根据 Tool Result 获得的执行成功、失败或拒绝事实 |

---

## 163. 下一篇建议

下一篇可以继续沿 Agent 基础链路深入：

> **源码精读 31：Tool Schema Contract——Rust Tool 类型怎样生成模型可见 JSON Schema，Provider Strict Mode、Alias、默认值、Validation 与版本演进怎样避免“模型会调用但 Runtime 解析不了”**

它会把第 37 节的 typed parse 往前追到 schema producer：模型究竟看到了什么参数合同，以及 schema 与 `ToolInput` 反序列化为什么可能发生漂移。
