# 源码精读 07：Agentic Loop 如何流式采样、执行 Tool、回填结果并决定结束

> 本篇把前面三篇串成真正运行的 Agent：Prompt 与 Tool Definition 进入一次 `ConversationRequest` 后，Sampler 如何流式产生文本、Reasoning 与 Tool Call；Shell 如何把 Tool Call 解析、审批并并发执行；Tool Result 如何进入 Conversation；下一次采样为何能看到这些结果；一个用户 Turn 最终又由什么条件结束。
>
> 源码基线：`ed6d543`。源码变化后优先按本文列出的类型与函数名重新定位。

---

## 1. 先给结论：Agent 不是“模型调用一次”

一次普通聊天常被想象成：

```text
User -> Model -> Assistant
```

但带工具的 Agent 实际更接近：

```text
User
  |
  v
构建 ConversationRequest
  |
  v
模型流式输出
  |
  +--> 最终没有 Tool Call ---------> 完成当前 Turn
  |
  +--> 最终包含 Tool Call
          |
          v
       Pre-flight
       解析参数 / Plan Gate / Hook / Permission
          |
          v
       并发 Dispatch
          |
          v
       Tool Result 写回 Conversation
          |
          +--------------------------> 重新构建请求并再次采样
```

因此应先区分三个层级：

| 层级 | 白话含义 | 本文叫法 |
| --- | --- | --- |
| 用户发出一次 Prompt，到系统把控制权还给用户 | 用户可感知的一轮 | Turn |
| Turn 内部的一次模型请求 | 一次推理调用 | Sampling Round / Model Call |
| 一次模型响应请求执行的某个函数 | 一个动作 | Tool Call |

一个 Turn 可以包含多个 Sampling Round；一个 Sampling Round 又可以包含多个 Tool Call。

---

## 2. 本篇主线文件

| 文件 | 主要职责 |
| --- | --- |
| `xai-grok-shell/src/session/acp_session_impl/turn.rs` | Agentic Loop 主循环、请求构建、响应提交、继续与结束条件 |
| `xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs` | 将一次请求交给 Sampler，等待 canonical response，处理采样失败恢复 |
| `xai-grok-shell/src/session/acp_session_impl/tool_calls.rs` | Sampling Event 映射、Tool Pre-flight、权限、并发执行、Tool Result 回填 |
| `xai-grok-shell/src/session/acp_session_impl/tool_dispatch.rs` | Prepared Tool Call 到 Workspace/Tool Runtime 的实际 Dispatch |
| `xai-grok-shell/src/session/acp_session_impl/types.rs` | `SamplerTurnOutcome`、`TurnOutcome`、`ToolLoop` 等控制流枚举 |
| `xai-grok-sampler/src/events.rs` | Sampler 发出的流式事件协议 |
| `xai-grok-sampling-types/src/conversation.rs` | Conversation、Response、StopReason 的规范化内部表示 |
| `xai-grok-shell/src/session/acp_session_impl/spawn.rs` | 启动独立的 Sampling Event Drainer |

阅读时不要只盯 `turn.rs`。主循环、流式事件与工具执行由不同异步任务协作完成。

---

## 3. 最重要的入口：`process_conversation_turn`

核心函数是：

```rust
async fn process_conversation_turn(...) -> Result<TurnOutcome, acp::Error>
```

它不是只调用一次模型，而是在内部执行一个 `loop`。

循环外初始化 Turn 级状态：

- 准备 Tool Definition。
- 记录 Turn 起点与 Telemetry。
- 创建工具调用名称累计表。
- 初始化 Tool Round 计数。
- 初始化重复 Tool Call 检测器。
- 初始化 401 恢复预算。
- 初始化 Structured Output 校验器。

循环内完成一次 Sampling Round：

1. 注入本轮动态上下文。
2. 检查是否需要压缩。
3. 计算本轮有效 Tool Spec。
4. 从最新 Chat State 构建请求。
5. 交给 Sampler。
6. 提交 canonical response 到 Conversation。
7. 没有 Tool Call 时尝试结束。
8. 有 Tool Call 时执行，并再次循环。

---

## 4. 为什么 Tool Definition 在循环外准备

`prepare_tool_definitions_timed()` 在进入 `loop` 前执行一次。

这意味着同一 Turn 的基础工具集通常保持稳定：

```text
Turn 开始
  -> 准备基础 Tool Definitions
  -> Sampling Round 1
  -> Tool Result
  -> Sampling Round 2
  -> Tool Result
  -> Sampling Round 3
  -> Turn 结束
```

每次循环仍会重新计算 `effective_tools`，因为还可能叠加：

- Forked Tool Override。
- Structured Output 合成工具。
- Hosted Tool 配置。

因此“基础 Tool Definition 只准备一次”不等于“请求中的全部工具字段永远不变”。

---

## 5. 一个循环迭代开始时先做什么

每次循环首先发出：

```rust
Event::LoopStarted { loop_index }
```

然后依次处理：

- Action Stationarity 硬停止。
- Action Stationarity 提醒。
- Pending Interjection。
- Pending Skill Reminder。
- Monitor Event。
- 首轮 Memory Reminder。
- MCP Reminder。
- Two-pass Compaction Prefire。
- Token Refresh。
- Auto Compaction。

这揭示一个重要事实：

> 下一次模型请求不是简单地在旧请求尾部拼 Tool Result，而是从最新的权威 Conversation 和最新运行期状态重新构建。

---

## 6. Chat State 才是下一轮请求的权威来源

请求通过：

```rust
self.chat_state_handle.build_request(...).await
```

构建。

上一轮产生的 Assistant Item 与 Tool Result 已经写入 Chat State；本轮新增的 Reminder、Interjection、Follow-up 也已写入。因此 `build_request` 看到的是最新快照。

阅读模型：

```text
Conversation_0
  + Assistant(tool_call A)
  + ToolResult(A)
  + Runtime Reminder
  = Conversation_1

build_request(Conversation_1)
  -> Sampling Round 2
```

Agent 的“循环记忆”不是某个局部变量偷偷传给模型，而是 Conversation 被持续扩展后重新序列化。

---

## 7. 请求构建后的运行期补丁

`build_request` 返回后，Turn Driver 还会填写：

- `x_grok_session_id`
- `x_grok_turn_idx`
- `x_grok_agent_id`
- `x_grok_deployment_id`
- `json_schema`
- `hosted_tools`
- `max_output_tokens`

然后阶段切换为：

```text
WaitingForModel / Sampling
```

所以一次请求有两类来源：

1. Chat State 负责 Conversation 与基础 Sampling Config。
2. Session Turn Driver 负责当前运行实例的动态字段。

---

## 8. `run_turn_via_sampler` 的职责边界

`run_turn_via_sampler` 驱动的是一次 Sampling Round，而不是整个 Agent Turn。

其核心步骤是：

```text
prepare_sampler_for_turn
  -> 安装 stream-drain barrier
  -> 生成 sampler request id
  -> sampler_handle.submit_and_collect
  -> 成功：等待 stream drainer 处理完 Completed
  -> 返回 canonical ConversationResponse
```

返回类型：

```rust
enum SamplerTurnOutcome {
    Response(...),
    CompactAndResubmit,
    RefreshAuthAndResubmit { ... },
}
```

注意 `CompactAndResubmit` 和 `RefreshAuthAndResubmit` 都不是 Agent Turn 结束；外层 `loop` 会 `continue` 并重建请求。

---

## 9. Sampler 为什么既“流式发事件”又“返回完整响应”

Sampler 同时服务两个需要：

1. 用户需要尽快看到文本和 Reasoning。
2. Tool 执行与 Conversation 持久化需要完整、规范化、可验证的响应。

因此它有双通路：

```text
Provider Stream
  |
  +--> SamplingEvent::ChannelToken ------> UI / ACP Chunk
  |
  +--> SamplingEvent::ToolCallDelta -----> 扩展流式展示
  |
  +--> L2 Collector ---------------------> ConversationResponse
                                             |
                                             v
                                      Turn 主循环执行工具
```

流式 Delta 用于即时呈现；最终 `ConversationResponse` 才是语义提交对象。

---

## 10. `SamplingEvent` 有哪些类别

`xai-grok-sampler/src/events.rs` 定义的主要事件包括：

| 事件 | 含义 |
| --- | --- |
| `StreamStarted` | HTTP 流已建立 |
| `FirstToken` | 收到第一个内容 Token |
| `ChannelToken` | Text 或 Reasoning 增量 |
| `ToolCallDelta` | Tool Call 名称、ID 或参数片段 |
| `ResponseStarted` | Provider 的真实消息 ID、Model、输入 Token 信息 |
| `ReasoningCompleted` | Reasoning Block 的签名已完整 |
| `BackendToolCallStarted` | 服务端工具开始执行 |
| `BackendToolCallCompleted` | 服务端工具执行完成 |
| `Retrying` | Sampler 正在内部重试 |
| `Completed` | 完整响应已组装 |
| `Failed` | 采样最终失败 |
| `ModelMetadata` | 收到模型元数据 |

`SamplingChannel` 当前区分：

```rust
Text
Reasoning
```

---

## 11. 谁消费 `SamplingEvent`

Session 创建时，`spawn_session_actor` 启动一个独立 Local Task：

```rust
while let Some(event) = sampler_event_rx.recv().await {
    drainer_session.handle_sampling_event(event).await;
}
```

这个任务可称为 Sampling Event Drainer。

它与等待 `submit_and_collect()` 的 Turn 主循环并行运行：

```text
Sampler Actor
  |
  +--> Event Channel --> Drainer --> ACP/UI Update
  |
  +--> Completion -----> submit_and_collect 返回给 Turn Loop
```

理解这个并发关系，才能理解后面的 stream-drain barrier。

---

## 12. Text Token 如何显示

`handle_sampling_event` 收到：

```rust
SamplingEvent::ChannelToken {
    channel: SamplingChannel::Text,
    text,
    chunk_index,
}
```

会：

1. 把文本追加到 Streaming Capture。
2. 将阶段更新为 `StreamingText`。
3. 发送 ACP `AgentMessageChunk`。

这里发送的是显示事件，不等同于把 Assistant Message 提交到 Conversation。

canonical Assistant Item 要等 `ConversationResponse` 完成后由 Turn 主循环提交。

---

## 13. Reasoning Token 如何显示

Reasoning 通道会：

1. 追加到 Streaming Capture 的 Reasoning 区。
2. 将阶段更新为 `StreamingReasoning`。
3. 发送 ACP `AgentThoughtChunk`。

因此 UI 层把可见回答与思考过程分开。

在规范 Conversation 中，Reasoning 也不是 Assistant 字符串里的临时字段；它可以作为独立的 `ConversationItem::Reasoning` sibling 保存，以维持 Responses API 的原始顺序和 KV Cache 前缀稳定性。

---

## 14. Tool Call Delta 不能直接执行

事件注释明确指出：

```text
arguments_delta 不保证单独是合法 JSON
```

例如模型可能分三段发出：

```text
{"path":
"src/main
.rs"}
```

任意一个片段都不能可靠解析。

因此 `ToolCallDelta` 只会：

- 把 Streaming Capture 阶段标为 ToolCall。
- 发送扩展更新 `ToolCallDeltaChunk`。

它不会进入 `prepare_tool_call`，也不会触发权限请求或 Dispatch。

真正执行的数据来自最终 `ConversationResponse.tool_calls()`。

---

## 15. Backend Tool 与 Client Tool 是两条执行路径

`BackendToolCallStarted/Completed` 表示 Provider 后端已经执行工具，例如托管搜索。

Shell 对它做的是：

- 发 ToolCall/ToolCallUpdate 给客户端显示。
- 记录成功、失败与 Telemetry。

Shell 不会再次本地 Dispatch。

相反，Assistant Item 中的 Function Tool Call 才会进入：

```text
prepare_tool_call
  -> permission
  -> dispatch_tool
  -> ToolResult
```

两者都可显示为“工具”，但执行所有权不同。

---

## 16. Stream-drain barrier 解决什么竞态

Turn 主循环和 Sampling Event Drainer 是两个异步任务。

如果 `submit_and_collect()` 已拿到完整响应，而 Drainer 还没把最后几个 Text Chunk 发完，主循环可能立即发送 ToolCall：

```text
Text Chunk 1
Text Chunk 2
ToolCall
Text Chunk 3   <- UI 时间线被切开
```

`send_update` 在调用时分配全局递增 `eventId`，所以这不是单纯显示延迟，而会形成错误的权威事件顺序。

解决办法：

1. `run_turn_via_sampler` 在提交前安装 oneshot sender。
2. Drainer 处理 `SamplingEvent::Completed` 时发送信号。
3. `submit_and_collect` 返回后，主循环等待 receiver。
4. 最长等待 5 秒，超时才继续。

正常顺序因此成为：

```text
所有 Text/Thought/Delta Event
  -> Drainer 处理 Completed
  -> barrier release
  -> Turn Loop 发 canonical ToolCall 通知
```

---

## 17. 为什么 `Completed` 自己不提交 Conversation

Drainer 收到 `Completed` 时主要做：

- 释放 stream-drain barrier。
- 更新 Doom-loop Capture。
- 清理当前 Streaming Segment。
- 记录请求耗时与推理指标。

它不调用 `push_assistant_response`。

原因是语义提交属于 Turn 主循环：主循环需要在同一处处理 Usage、StopReason、Structured Output、Tool Calls 和结束判断。

这形成清晰分工：

| 组件 | 负责什么 |
| --- | --- |
| Drainer | 实时事件与显示副作用 |
| Turn Loop | canonical response、Conversation 状态与控制流 |

---

## 18. Sampling 成功后先记录什么

主循环得到 `ConversationResponse` 后会记录：

- Prompt / Cached / Completion / Reasoning Token。
- TTFT 与 Tokens per Second。
- 本 Turn 聚合 Usage。
- Model Fingerprint。
- Stop Reason。
- Response Completed 扩展更新。

然后提取：

```rust
let mut tool_calls = response.tool_calls().to_vec();
```

再处理 `response.items`。

---

## 19. `ConversationResponse.items` 是平铺的有序列表

规范类型不是只有一个 Assistant Message：

```rust
enum ConversationItem {
    System,
    User,
    Assistant,
    ToolResult,
    BackendToolCall,
    Reasoning,
}
```

一次 Provider 响应可能形如：

```text
Reasoning
BackendToolCall
Reasoning
BackendToolCall
Assistant(text + client tool calls)
```

保留 sibling 顺序可支持：

- Responses API 无损 Round-trip。
- 后端工具上下文延续。
- Reasoning Item 的完整保存。
- Prefix KV Cache 稳定。

---

## 20. Response Item 如何写回 Chat State

Turn 主循环遍历 `response.items`：

```rust
match item {
    ConversationItem::Assistant(_) => record_assistant_response(item),
    _ => chat_state_handle.push_tool_result(item),
}
```

这里 `push_tool_result` 的名字容易误导：非 Assistant 的 response sibling，包括 Reasoning 和 BackendToolCall，也通过这条 actor command 追加。

要按参数类型理解，而不要仅凭方法名假设“这里只能放 ToolResult”。

---

## 21. 流式文本与 canonical Assistant 为什么不会写两遍

Text Chunk 是 ACP/UI Update；`record_assistant_response` 是 Conversation Mutation。

二者位于两个不同平面：

```text
显示平面：AgentMessageChunk + AgentThoughtChunk
状态平面：ConversationItem::Assistant / Reasoning / BackendToolCall
```

UI 可以边生成边显示；模型下一轮只读取完成并提交的 canonical item。

取消时的未完成流式内容存入 trace-only Streaming Capture，不会偷偷回填 Chat State，也不会进入下一轮模型上下文。

---

## 22. Fallback Text 解决什么问题

若完整响应里存在 Assistant Text，但 `message_chunks_emitted == 0`，主循环会发一个 fallback `AgentMessageChunk`。

典型含义是：

- canonical response 成功收集。
- 但某次重试或事件路径丢失了文本 Chunk。
- 下游 UI 否则看不到最终回答。

Fallback 只补显示，不再写一份 Assistant Conversation Item。

---

## 23. StopReason 不是唯一结束依据

统一后的 `StopReason` 有：

```rust
Stop
Length
ToolCalls
ContentFilter
```

但 Turn 主循环真正先看的是：

```rust
tool_calls.is_empty()
```

原因是不同 Provider 的 Finish Reason 和实际内容可能存在兼容差异；规范化后的 Tool Call 列表更直接地决定是否需要客户端动作。

所以不要把 Agentic Loop 简化为：

```text
finish_reason == tool_calls 才执行工具
```

源码实际以 canonical Tool Call 数据为主。

---

## 24. Content Filter 如何处理

若：

```rust
stop_reason == ContentFilter
```

则当前 Turn 标为 refused。

如果响应还是空的，Shell 会向用户发送一个 Provider Refusal Notice；若 Provider 提供 explanation，也会附上。

当没有 Tool Call 时，最终仍以 `TurnOutcome::Completed` 返回，但 `refusal` 字段为 `Some(...)`。

“已完成控制流”不代表“模型成功回答了业务问题”；它只表示本 Turn 没有更多内部动作。

---

## 25. 没有 Tool Call 时为何不一定马上结束

`tool_calls.is_empty()` 分支依次检查：

1. TodoGate 是否要求模型继续推进未完成 Todo。
2. 是否有刚到达的 Interjection。
3. 执行 Turn-End Bookkeeping。
4. 再次检查是否有迟到 Interjection。
5. 校验 Native Structured Output。
6. 返回 `TurnOutcome::Completed`。

因此“模型没调用 Tool”只是结束候选，不是不可逆的结束点。

---

## 26. 为什么结束前要 Drain 两次 Interjection

第一次 Drain 在 Bookkeeping 前。

但 Bookkeeping 本身是异步的：期间用户可能又追加消息。因此完成 Bookkeeping 后再次 Drain。

时序：

```text
模型返回无 Tool Call
  -> drain #1
  -> async finalize bookkeeping
       用户消息可能在这里到达
  -> drain #2
  -> 真正 return Completed
```

任何一次 Drain 成功都会 `continue`，让新消息进入下一次 Sampling Round。

这是一种典型的“检查—异步间隙—复查”竞态兜底。

---

## 27. TodoGate 如何把“自然结束”改成“继续工作”

当模型无 Tool Call，但 Todo 中仍有 Pending 或 In Progress 项时，策略可能返回：

```rust
TodoGateDecision::Nudge { reminder, reason }
```

在每个 Prompt 的最大触发次数内：

- 将 Reminder 写入 Chat State。
- `continue` 到下一次 Sampling Round。

超过触发上限后不会无限循环，而是加入解释性 Reminder 并允许控制权回到用户。

这是 Agentic Loop 中“业务状态驱动续跑”的一个例子。

---

## 28. Structured Output 有两种路径

若 Backend 支持 Native Schema：

```text
request.json_schema = schema
```

模型无 Tool Call 时，Shell 对最终 Assistant Text 做 JSON Parse 与 Schema Validation。

若 Backend 不支持 Native Schema：

- 动态加入 `StructuredOutput` Tool Spec。
- 注入 System Reminder。
- 要求模型最终单独调用它一次。

这个 Tool 不走普通 Tool Dispatch，而由 Turn Driver 拦截并本地验证。

---

## 29. StructuredOutput 为什么必须单独调用

`handle_structured_output_tool_call` 检查：

- 是否存在 `StructuredOutput`。
- 当前 response 是否还包含其他 Tool Call。

如果它和其他工具混在同一批：

- 为 StructuredOutput 写入纠正性 Tool Result。
- 从本批工具列表移除 StructuredOutput。
- 继续执行其他工具。

如果它单独出现但参数不合 Schema：

- 写入错误 Tool Result。
- 在有限次数内 `continue`，让模型修正参数。

校验成功或重试耗尽后返回 `Complete(validated)`。

---

## 30. 从 canonical Tool Call 到 `ToolCallResponse`

模型响应中的 Tool Call 会转换成 Shell 兼容结构：

```rust
ToolCallResponse {
    id,
    kind: "function",
    function: {
        name,
        arguments,
    },
}
```

转换前会：

- 把 Tool Name 加入本 Turn `tools_called`。
- 记录 MCP Server/Tool Span 字段。
- 计算重复动作 Signature。
- 判断是否是 Bash `true` 真空转。

之后阶段切换为 `ToolExecution`。

---

## 31. `execute_tool_calls` 的总体三阶段

代码注释将其概括为：

```text
Prepare -> Dispatch -> Post-flight
```

更精确地说：

```text
模型 Tool Calls
  |
  v
按顺序 Pre-flight
  - 注册 UI ToolCall
  - MCP 可用性
  - JSON 恢复与类型解析
  - Plan Mode Gate
  - PreToolUse Hook
  - Permission
  - Exit Plan Approval
  |
  v
得到 approved: Vec<PreparedToolCall>
  |
  v
并发 Dispatch
  |
  v
按完成顺序 Drain
  - UI ToolCallUpdate
  - Tool Result 写 Conversation
  - Post Hook
  - Telemetry
```

---

## 32. 多 Tool Call 为什么先处理 `exit_plan_mode` 尾部

当一批超过一个 Tool Call 时，`split_exit_plan_tail` 将普通调用与 Exit Plan 类调用拆开：

```text
body batch
  -> tail batch
```

这是控制面工具的顺序约束：Plan Mode 的退出与审批可能改变后续执行语义，不应与普通动作无差别地并发。

文档阅读时可把它理解为：

> 一批 Tool Call 默认可并发，但状态切换型 Tool 可能被单独放到尾批。

---

## 33. Pre-flight 为什么按模型顺序串行

`execute_tool_calls_batch` 先循环调用：

```rust
prepare_tool_call(call, deferred_followups).await
```

只有通过 Pre-flight 的调用才进入 `approved`。

权限请求和 Hook 可能与用户交互，必须形成确定顺序。若早先调用已产生终止性 `ToolLoop`，后续未准备的调用会收到“因先前拒绝/取消而未执行”的 Tool Result。

因此：

```text
Pre-flight：串行、有序
Dispatch：并行、按完成返回
```

---

## 34. Tool Call 先以 Pending 状态注册

`prepare_tool_call` 一开始就发送 ACP：

```text
ToolCallStatus::Pending
```

并附上：

- Tool Call ID。
- Tool Name。
- 能提前解析出的 Raw Input。
- 规范化 Tool Meta。
- Subagent 是否后台运行等元数据。

这样即使后续 Parse、Hook 或 Permission 失败，UI 也有稳定的 Tool Call 身份可更新。

---

## 35. MCP Tool 的 Pre-flight 特例

若名字能解析为 MCP Tool：

- Managed MCP 可能先刷新过期连接。
- Blocking 策略会等待 MCP 初始化。
- Progressive 策略若尚未初始化，则写入“使用 `search_tool` 查找工具”的错误结果。

因此 MCP Tool “在模型响应中被点名”仍不代表一定能执行；运行时连接状态会在 Pre-flight 再验证。

---

## 36. Tool Arguments 的容错解析

先把空参数规范化，再尝试：

```rust
serde_json::from_str::<serde_json::Value>()
```

若失败，代码还会尝试从拼接的多个 JSON Object 中提取对象。

对每个候选对象调用 ToolBridge `try_parse`，寻找能匹配当前 Tool 的对象。

如果完全无法恢复，则包装成：

```json
{"raw": "原始参数"}
```

随后仍由 ToolBridge 做类型级解析。类型解析失败会形成模型可见的 Tool Parsing Error，而不是直接 Panic。

---

## 37. 为什么容错后还要 ToolBridge `try_parse`

合法 JSON 只说明语法正确，不说明参数符合工具契约。

例如：

```json
{"path": 42}
```

是合法 JSON，但若 Tool 要求字符串路径，仍应失败。

因此有两层：

```text
JSON Syntax Parse
  -> Tool Name Resolution + Typed Args Parse
```

第二层还能处理别名、Meta Tool Dispatch Target 和 Harness 差异。

---

## 38. Plan Mode Gate 在 Permission 前

Tool Input 解析后先计算 `AccessKind`，再执行：

```rust
plan_mode_edit_gate(...)
```

不允许的写操作会：

- 记录 Plan Mode Deny。
- 发送未执行 Tool Result。
- 返回 `ToolLoop::Continue`。

这不是普通权限拒绝，因为即使 Permission Manager 处于 Always Approve，Plan Mode 仍应保持只读约束。

安全层次是：

```text
Mode Capability Gate
  -> Hook Policy
  -> Permission Policy / User Decision
  -> Dispatch
```

---

## 39. `PreToolUse` Hook 在 Permission 前

通过 Plan Gate 后，代码执行 Server Hook 与 Client Hook。

Hook Payload 包含：

- 实际解析后的 Tool Name。
- Tool Use ID。
- Tool Input。
- 输入是否被截断。
- Subagent Type。

若 Hook 返回 Deny：

- 写入拒绝结果。
- 返回 `ToolLoop::HookDenied`。
- 当前 Turn 不必终止，模型可以读到拒绝原因后选择别的动作。

把 Hook 放在 Permission 前可避免为一个必然被策略拦截的动作再询问用户。

---

## 40. Permission 的输入不是简单 Tool Name

Permission Manager 收到的核心是 `AccessKind`：

```text
Read(path)
Edit(path)
Bash(command)
Grep(path, glob)
MCPTool(name, ...)
WebFetch(url)
WebSearch(query)
```

还携带真实 CWD、显示 CWD、Session ID 与 ToolCallUpdate。

所以权限判断可以理解“读哪个文件”“执行什么命令”，而不只是笼统地判断 `bash` 是否允许。

---

## 41. Ask、Auto、Always Approve 仍共享一条控制流

Permission Mode 会映射成 Telemetry：

```text
Ask
Auto
AlwaysApprove / YOLO
```

Auto Mode 在请求前刷新近期 Conversation Transcript，供 LLM Classifier 使用。

但从 `prepare_tool_call` 看，它们最终都归一成 `Decision`：

```rust
Allow
Ask
Reject
PolicyDeny
Cancelled
FollowupMessage
```

这里 `Decision::Ask` 在返回到该层时与 Allow 一样表示可继续；真正的人机交互已由 Permission Manager 内部完成。

---

## 42. Permission Reject 与 Policy Deny 不同

两者都会写模型可见的“未执行”结果，但外层行为不同：

| 决定 | `ToolLoop` | Turn 行为 |
| --- | --- | --- |
| 用户 Reject | `PermissionReject` | 取消当前 Turn |
| Policy Deny | `Continue` | 把拒绝结果交给模型，允许换方案 |
| Cancelled | `Cancelled` | 取消当前 Turn |
| FollowupMessage | `FollowupMessage` | 注入用户补充消息并继续采样 |

这是很重要的产品语义：自动策略禁止某动作时，Agent 可以自主调整；用户明确拒绝则通常把控制权还给用户。

---

## 43. Follow-up Message 如何成为下一轮输入

用户在 Permission UI 中不批准，而是给出新指令时：

1. 当前 Tool 得到“未执行”结果。
2. `execute_tool_calls` 返回 `ToolLoop::FollowupMessage(text)`。
3. Turn Driver 调用 `add_followup_message_as_user_turn`。
4. `continue` 进入下一次 Sampling Round。

这不是新建一个完全独立的外部 Turn，而是在当前 Agentic Loop 中将用户修正写入 Conversation 后继续。

---

## 44. `PreparedToolCall` 是执行前的冻结快照

通过 Pre-flight 后形成：

```rust
PreparedToolCall {
    call_id,
    tool_call_id,
    tool_name,
    raw_arguments,
    parsed_args,
    model_id,
    concatenated_json_count,
    dispatch_target_name,
    is_read_only,
}
```

它冻结了执行所需的信息，使后续并发 Future 不必再次读取易变化的解析状态。

其中 Requested Tool Name 与 Dispatch Target Name 可能不同，例如 `use_tool` 最终分派到具体 MCP Tool。

---

## 45. 多工具如何并发执行

所有 `approved` 调用先转成 Future，再放入：

```rust
FuturesUnordered
```

随后由 Drainer Task 将完成项送入 Channel：

```text
Tool A Future --\
Tool B Future --- FuturesUnordered -> dispatch_tx -> dispatch_rx -> Post-flight
Tool C Future --/
```

因此执行不是按模型列出的顺序等待：

```text
模型顺序：A, B, C
完成顺序：B, C, A
```

Post-flight 通常也按完成顺序处理。

---

## 46. 为什么并发工具还需要文件锁

并发执行对多个独立 Read/Search 很有价值，但两个调用可能操作同一路径。

代码先收集非只读调用的 `write_paths`，再为相关路径创建：

```rust
Arc<tokio::sync::Mutex<()>>
```

任何命中同一路径且与写集合相关的调用，在 Dispatch 前获取同一把锁。

这样保留：

- 不同路径之间的并发。
- 同一路径读写或写写的串行化。

它不是全局 Workspace 大锁，粒度更细。

---

## 47. `is_read_only` 是并发提示，不是最终安全证明

`PreparedToolCall.is_read_only` 根据 ToolKind 分类，例如 Read、Search、LSP、WebSearch 等。

它在这里主要用于决定文件路径锁。

真正的安全限制此前已由：

- Plan Mode Gate。
- Hook。
- Permission Manager。

完成。

不要把 `is_read_only` 当作独立的访问控制系统。

---

## 48. 阻塞等待工具如何响应 Mid-turn Interjection

部分等待型 Tool 可能长时间阻塞。代码判断其是否 `interruptible`，并执行：

```rust
tokio::select! {
    result = tool_execution => result,
    _ = wait_for_pending_interjection(...) => interrupted_result,
}
```

使用 `biased`，工具已完成时优先取真实结果。

如果用户插话先到，则生成模型可见的“等待被中断”结果，让下一轮尽快处理用户新消息。

---

## 49. 同一批 MCP 认证恢复为什么用 `OnceCell`

批处理中共享：

```rust
Arc<tokio::sync::OnceCell<bool>>
```

它使多个并发工具遇到同类认证问题时可以共享一次恢复结果，避免每个 Future 同时发起重复认证恢复。

Managed MCP 还有结果级的 Reactive Re-auth：若输出表明认证被拒绝，恢复成功后可重试该具体工具。

这是“批次共享恢复”与“单工具结果重试”两层机制。

---

## 50. Dispatch 的真实边界

Future 最终调用：

```rust
dispatch_tool(&workspace_ops, &prepared, &session_id)
```

该层把已经解析、获批的 Prepared Tool Call 交给 Workspace Ops / ToolBridge Runtime。

因此职责顺序是：

```text
ToolBridge.try_parse     决定“这是什么调用”
Permission / Hook        决定“能不能调用”
dispatch_tool            决定“如何实际运行”
handle_*_result          决定“如何反馈模型与用户”
```

---

## 51. Tool 执行完成后为何保留原始索引

每个 Future 返回：

```text
(idx, result, duration_ms)
```

`idx` 用来从 `approved_slots` 精确取回对应的 `PreparedToolCall`。

这是因为 `FuturesUnordered` 按完成顺序产出；不能通过当前循环次数猜测结果属于哪个调用。

Tool Call ID 仍是跨 UI、Conversation 与 Telemetry 的主要 Join Key；`idx` 是本批次内部的安全定位方式。

---

## 52. Tool 成功有两个输出面

`ToolRunResult` 至少服务两个消费者：

1. 客户端显示：结构化 Tool Output 转成 ACP `ToolCallUpdate`。
2. 模型上下文：`prompt_text` 转成 `ConversationItem::ToolResult`。

这两个内容不必完全相同。

例如 UI 可以显示结构化 Edit、Plan 或 Bash 状态；模型得到的是适合继续推理的文本和可能的图片 Content Part。

---

## 53. Tool Result 如何真正进入下一轮

成功路径最终调用：

```rust
self.chat_state_handle.push_tool_result(tool_chat);
```

`tool_chat` 绑定原 Tool Call ID：

```text
Assistant.tool_calls[n].id
          ==
ToolResult.tool_call_id
```

下一次 `build_request` 会从 Chat State 读到完整配对：

```text
Assistant: call tool A, id=call_123
ToolResult: id=call_123, content=...
```

模型因此知道哪个结果对应哪个请求。

---

## 54. Tool Result 可以携带图片

`handle_bridge_tool_success` 会处理：

- Tool Layer 提取出的图片。
- Prompt Text 中的 Base64 图片。
- Read File 返回的图片。
- PDF Page Images。

对于模型可见的 Tool Result：

```rust
ConversationItem::tool_result_with_images(...)
```

额外提取图片还可能生成 Deferred User Follow-up Item，在整批结束后追加。

所以 Tool Result 不一定只是纯文本字符串。

---

## 55. 为什么图片 Follow-up 要 Deferred

并发 Tool 完成时如果立刻插入 User Item，可能把一组 Assistant Tool Calls 与对应 Tool Results 切开，破坏消息顺序约束。

因此：

1. 先让每个 Tool Result 完整回填。
2. 收集 `deferred_followups`。
3. 整批工具完成后统一 `push_user_message`。

这保护了 Function Calling 历史的结构完整性。

---

## 56. Tool Error 为什么通常不终止 Turn

硬执行错误会转换成：

```text
Tool `name` failed: error
```

然后同时：

- 发 Failed `ToolCallUpdate` 给客户端。
- 写 `ConversationItem::ToolResult` 给模型。
- 记录 Tool Failure。
- 执行 `PostToolUseFailure` Hook。
- 返回 `ToolLoop::Continue`。

下一轮模型可据此：

- 修正参数。
- 换工具。
- 解释阻塞。
- 请求用户帮助。

工具失败是 Agent 的观察结果，不天然等于控制流异常。

---

## 57. Requested Tool Name 与 Effective Tool Name

Meta Tool 场景中：

```text
requested: use_tool
effective: github__create_issue
```

成功与错误处理都保留这一区分。

错误消息可写成：

```text
Tool `effective` failed via `requested`: ...
```

这对理解 Tool Search/Use、Telemetry 与 Hook 过滤非常关键。

---

## 58. Skill 也可能在 Tool 执行后变化

每个 Tool 结果完成后，代码调用：

```rust
bridge.apply_pending_skill_update().await
```

若 Tool 动作激活或改变了 Skill：

- 生成 Skill Reminder。
- 加入 Deferred Follow-up。
- 必要时更新可用命令列表。

因此 Skill 不只在 Session 初始化时影响 Prompt，也可能在 Agentic Loop 中由 Tool 副作用推动下一轮上下文变化。

---

## 59. Post Hook 的执行位置

成功路径在已处理 Tool Result 后执行 `PostToolUse`；失败路径执行 `PostToolUseFailure`。

Post Hook Payload 可包含：

- Tool Name。
- Tool Use ID。
- Tool Input。
- Tool Result 或 Error。
- 是否截断。
- Subagent Type。

Post Hook 主要是观察与后处理，不是将成功执行倒回去的事务机制。

---

## 60. 并发完成顺序会影响 Conversation Item 顺序吗

源码按 `dispatch_rx.recv()` 的完成顺序调用成功/错误处理，而这些处理立即 `push_tool_result`。

所以同一批并发 Tool Result 的写入顺序通常是完成顺序，不保证与模型 Tool Call 列表顺序一致。

正确关联依靠 Tool Call ID，而不是相邻位置。

学习时应建立这个不变量：

> Function Call / Result 的逻辑配对靠 ID；并发系统不能依赖返回顺序。

---

## 61. 一批执行完后还会做什么

`execute_tool_calls` 在所有 Batch 完成后：

1. 追加 Deferred Follow-up User Items。
2. Drain Pending Interjection。
3. Flush Pending Skill Reminder。
4. 返回最终 `ToolLoop`，没有特殊情况则为 `Continue`。

这保证下一轮请求已经看见：

- 全部 Tool Result。
- 工具衍生图片。
- Skill 更新提醒。
- 用户插话。

---

## 62. `ToolLoop` 是工具阶段到 Turn 阶段的控制协议

```rust
enum ToolLoop {
    Continue,
    NonExistingTool,
    ToolParsingError,
    PermissionReject { ... },
    Cancelled,
    FollowupMessage(String),
    HookDenied { ... },
}
```

它不是 Tool 本身的业务结果，而是“工具阶段后 Turn Driver 应怎么走”。

映射关系：

| ToolLoop | Turn Driver 行为 |
| --- | --- |
| `Continue` | 进入后续检查并再次循环 |
| `NonExistingTool` | 错误已写回，通常继续 |
| `ToolParsingError` | 错误已写回，通常继续 |
| `HookDenied` | 拒绝已写回，继续让模型调整 |
| `PermissionReject` | 返回 Cancelled |
| `Cancelled` | 返回 Cancelled |
| `FollowupMessage` | 写 User Follow-up，立即继续 |

---

## 63. 为什么普通 Tool 执行成功后没有显式 `continue`

匹配 `ToolLoop` 后，代码还要：

- 更新 `max_turns` 计数。
- 检查 Preflight Overflow。
- 必要时先压缩。

随后自然到达 `loop` 尾部，再进入下一次迭代。

所以控制流等价于继续，但不是所有路径都在同一个位置写 `continue`。

---

## 64. `max_turns` 实际限制什么

`tool_turn_count` 初始为 1。

每完成一个“模型响应包含客户端 Tool Call，并执行完该工具批次”的周期后，计算：

```rust
next_turn = tool_turn_count + 1
```

若超过 `max_turns`，返回：

```rust
TurnOutcome::MaxTurnsReached { limit }
```

所以这里更接近限制 Agent Tool Round，而不是所有 HTTP Sampling Attempt。

Compaction Resubmit 或 401 Resubmit 不按同样方式增加该计数。

---

## 65. Tool Result 太大时为何在下一轮前压缩

工具执行完成后调用 `check_preflight_overflow()`。

如果新增 Tool Result 使下一次请求可能溢出上下文：

- 先执行 `run_compact_only`。
- 然后 `continue`。
- 下一次循环从压缩后的 Chat State 重建请求。

这比等 Provider 返回 Context Length Error 更主动。

---

## 66. Action Stationarity 检测什么

每次模型响应的整批 Tool Calls 被序列化成 Step Signature：

```text
tool_name + arguments
tool_name + arguments
...
```

若连续轮次 Signature 相同，`IdenticalToolCallRun.run_len` 增加。

特殊情况：单个 Bash 命令 `true` 被归类为 True No-op，所有此类动作使用统一 Signature。

阈值：

| 情况 | Nudge | Hard Stop |
| --- | --- | --- |
| 普通相同 Tool Step | 8 次 | 16 次 |
| Bash `true` 真空转 | 不等到普通 Nudge | 4 次 |

---

## 67. Nudge 与 Hard Stop 为什么在下一轮开头检查

`observe()` 在 Tool Call 被识别后更新状态；Tool Result 随后先完整提交。

下一次循环开头才：

- 检查 Hard Stop。
- 或注入 Nudge Reminder。

这样模型若收到 Nudge，它同时也能看到刚才工具执行的真实结果。

Hard Stop 返回：

```rust
TurnOutcome::StationarityEnded
```

它故意不同于 `Completed`，避免 Completion Recovery、Goal Continuation 或 Stop Hook 再把循环重新打开。

---

## 68. Sampler 内部重试与 Agent 重采样不是一回事

至少有三类“再试一次”：

| 层级 | 例子 | 是否新增 Conversation Item |
| --- | --- | --- |
| Sampler 内部 Retry | 网络、空响应、Doom-loop sampling retry | 通常不新增 canonical item |
| Turn Loop Resubmit | 401 恢复、Compaction | 可能改变凭据或 Conversation 快照 |
| Agent Tool Loop | Tool Result 后再次调用模型 | 会新增 Assistant/ToolResult |

日志里都可能出现 retry/resubmit/continue，但语义不同。

---

## 69. Sampling Failure 如何回到外层循环

`run_turn_via_sampler` 的错误恢复可能返回：

```text
CompactAndResubmit
RefreshAuthAndResubmit
```

Turn Loop 对前者直接 `continue`；对后者还经过 `AuthRetrySchedule`：

- 判断被拒绝请求是否真的携带凭据。
- 区分不计预算的 Resubmit 与带 Backoff 的 Retry。
- 防止持续恢复却仍 401 的 Runaway。
- 超过预算后返回终止错误。

关键原则是：恢复后重新构建请求，而不是复用过期快照盲发。

---

## 70. Streaming Capture 为什么不进入 Conversation

Streaming Capture 保存本 Turn 尚未提交的流式生成，用于取消、终端错误或 Doom-loop 时上传诊断 Trace。

源码注释强调它是 out-of-band：

- 不由 `BuildConversationRequest` 返回。
- 不通过 `push_assistant_response` 写入。
- 不在后续 Turn 发给模型。

否则一次被取消的半句话可能污染 Agent 后续判断。

---

## 71. `TurnOutcome` 才是 Turn 的终态协议

```rust
enum TurnOutcome {
    Completed { ... },
    Cancelled { ... },
    MaxTurnsReached { limit },
    StationarityEnded { ... },
}
```

含义：

| Outcome | 含义 |
| --- | --- |
| `Completed` | 当前内部循环正常收敛，没有更多客户端 Tool Call |
| `Cancelled` | 权限拒绝、取消等使本 Turn 中止 |
| `MaxTurnsReached` | Tool Round 达到配置上限 |
| `StationarityEnded` | 重复动作/True No-op 被静默熔断 |

基础设施错误则走外层 `Err(acp::Error)`，不包装为上述业务终态。

---

## 72. `Completed` 携带的不只是答案

`TurnOutcome::Completed` 包含：

- Turn-End Signal Snapshot。
- 本 Turn 调用过的 Tool Names。
- Structured Output 校验结果。
- Provider Refusal 信息。

这些信息供外层做：

- Telemetry。
- Completion Requirement 判断。
- Goal/Recovery 逻辑。
- Structured Result Delivery。

Agentic Loop 的返回值是控制面汇总，不只是 Assistant Text。

---

## 73. Completion Requirement 会在 Turn 外再开一次 Turn

`process_conversation_turn_with_recovery` 包装基础循环。

某些 Agent Definition 要求完成前必须调用特定 Tool。若基础 Turn `Completed`，但 `tools_called` 不含该工具：

1. 按指数 Backoff 等待。
2. 注入 Auto Recovery User Message。
3. 再调用一次 `process_conversation_turn`。
4. 直到满足要求或重试耗尽。

这比普通 Tool Loop 高一层：

```text
Completion Recovery
  -> Conversation Turn
       -> Sampling Round
            -> Tool Calls
```

Stationarity 与 MaxTurns 会直接返回，不被 Recovery 重新打开。

---

## 74. Turn 结束 Bookkeeping 做什么

`finalize_turn_bookkeeping` 被正常完成与 Stationarity 路径复用，主要包括：

- Plan Cleanup。
- 记录 Turn Complete Signal。
- 获取一致的 Turn-End Snapshot。
- 把 Prompt Mode 与 Token 汇总写入 Snapshot。
- 持久化与 Telemetry。
- Feedback 触发。

它发生在控制流返回前，因此 Turn Outcome 已带着可用于外层处理的一致快照。

---

## 75. 一次两工具调用的完整时序

假设模型同时请求读取 `a.rs` 和 `b.rs`：

```text
User Prompt
  |
  v
build_request
  |
  v
Sampler Stream
  |-- Reasoning chunks --> AgentThoughtChunk
  |-- Text chunks ------> AgentMessageChunk
  |-- Tool deltas ------> ToolCallDeltaChunk
  `-- Completed
         |
         v
stream-drain barrier release
         |
         v
commit Assistant(tool A, tool B)
         |
         v
prepare A: parse -> hook -> permission
prepare B: parse -> hook -> permission
         |
         v
dispatch A -----\
                FuturesUnordered
dispatch B -----/
  |                 |
  | B first         | A later
  v                 v
ToolResult B      ToolResult A
  \                 /
   `---- Chat State
             |
             v
drain interjection / skill reminders
             |
             v
build_request again
             |
             v
Model reads Assistant calls + both results
             |
             v
No Tool Call -> Completed
```

---

## 76. Conversation 在各阶段长什么样

初始：

```text
[System, ProjectInstructions, User]
```

第一轮采样完成：

```text
[System, ProjectInstructions, User,
 Reasoning?, BackendToolCall?, Assistant(tool_calls=[A, B])]
```

工具完成：

```text
[...,
 Assistant(tool_calls=[A, B]),
 ToolResult(B),
 ToolResult(A)]
```

第二轮采样完成：

```text
[...,
 ToolResult(B), ToolResult(A),
 Reasoning?, Assistant(final text)]
```

注意 Tool Result 顺序可以不同，但 ID 配对必须正确。

---

## 77. Agentic Loop 的五类“状态”

阅读源码时可以把状态分成五层：

| 状态 | 例子 | 权威持有者 |
| --- | --- | --- |
| Conversation State | User、Assistant、ToolResult | Chat State Actor |
| Turn Control State | loop_index、max_turns、stationarity | `process_conversation_turn` 局部状态 |
| Stream Display State | chunk、phase、partial capture | Sampling Drainer / Session |
| Tool Execution State | Prepared Calls、Locks、Futures | `execute_tool_calls_batch` |
| Session Runtime State | Permissions、MCP、Skills、Interjections | SessionActor 相关组件 |

很多 Bug 来自把其中一层误当成另一层的权威来源。

---

## 78. 三个最容易误读的方法名

### `run_turn_via_sampler`

只运行一次 Sampling Round，不是完整 User Turn。

### `push_tool_result`

在 Response Item 提交处还用于追加非 Assistant sibling，不要只按名字推断参数一定是 `ToolResult` Variant。

### `ToolLoop::Continue`

表示工具阶段没有要求中止；实际进入下一次迭代前仍会检查 MaxTurns 与 Preflight Overflow。

---

## 79. 四种常见错误的传播方式

| 错误 | 如何反馈 | 是否通常继续 Agent Loop |
| --- | --- | --- |
| Tool 参数解析错误 | Tool Result | 是 |
| Tool 执行错误 | Failed UI Update + Tool Result | 是 |
| Policy/Hook 禁止 | Tool Result | 是 |
| 用户 Permission Reject/Cancel | Tool Result + Cancelled Outcome | 否 |
| Sampling 基础设施终止错误 | ACP Error | 否 |

核心设计思想：

> 能被模型理解并换方案的失败，优先变成 Observation；无法安全继续或代表用户收回授权的失败，结束 Turn。

---

## 80. 为什么 Tool Result 必须在再次采样前提交

如果先开始下一次采样再异步写结果，会出现：

```text
Assistant calls A
下一次 Model Request   <- 看不到 A 的结果
ToolResult A arrives
```

当前实现等待整批 Dispatch 与 Post-flight 完成，才回到循环顶部重建请求。

因此每次普通 Agent Tool Round 都有明确 Barrier：

```text
canonical Assistant committed
  -> all approved tools settled
  -> all results committed
  -> next request built
```

---

## 81. 哪些事件只是 UI，哪些会影响模型

| 数据 | 给 UI | 写 Conversation | 下一轮模型可见 |
| --- | --- | --- | --- |
| Text Chunk | 是 | 否 | 否，直到 canonical Assistant 提交 |
| Thought Chunk | 是 | 否 | 同上 |
| ToolCallDelta | 是 | 否 | 否 |
| ConversationResponse Assistant | 间接 | 是 | 是 |
| ToolCallUpdate | 是 | 否 | 否 |
| ConversationItem::ToolResult | 间接 | 是 | 是 |
| Streaming Partial Trace | 否/诊断 | 否 | 否 |

这个表是理解本篇最重要的检查清单之一。

---

## 82. 哪些 `continue` 会重建请求

以下分支都会回到循环顶部：

- Compaction Resubmit。
- 401 Recovery Resubmit。
- TodoGate Nudge。
- Interjection Drain。
- Structured Output Retry。
- Permission Follow-up Message。
- 普通 Tool Batch 完成。
- Preflight Overflow Compaction。

但它们加入 Chat State 的内容不同，所以虽然都叫 `continue`，下一次 Request 不同。

---

## 83. 哪些路径不会执行普通 Tool Dispatcher

- Provider Backend Tool：服务端已执行。
- StructuredOutput 合成 Tool：Turn Driver 本地拦截验证。
- Plan Mode Gate 拒绝：Pre-flight 即返回。
- Hook Deny：Pre-flight 即返回。
- Permission Reject/Cancel：Pre-flight 即返回。
- Tool Parse Error：无法形成 `PreparedToolCall`。

因此 UI 看到 Tool Call，不等于一定进入 `dispatch_tool`。

---

## 84. 哪些边界保证历史可重放

1. Provider Response 被规范化为平铺有序 `ConversationItem`。
2. Assistant Tool Call 与 Tool Result 用 ID 关联。
3. 未完成 Streaming Partial 不进入 Conversation。
4. Deferred Follow-up 避免切断 Tool Call/Result 结构。
5. 下一轮始终从 Chat State 重建 Request。
6. Backend Tool Call 与 Reasoning sibling 被保留。

这些约束让 Resume、Fork、Compaction 与跨 Backend 转换有可能保持语义一致。

---

## 85. 推荐的源码阅读断点

第一次调试可在下列符号设置断点或日志：

```text
process_conversation_turn
ChatStateHandle::build_request
run_turn_via_sampler
handle_sampling_event
record_assistant_response
execute_tool_calls
prepare_tool_call
dispatch_tool
handle_bridge_tool_success
handle_tool_error
finalize_turn_bookkeeping
```

观察字段：

```text
loop_index
tool_turn_count
response.stop_reason
response.items
tool_calls
call.id
PreparedToolCall.dispatch_target_name
ToolLoop
TurnOutcome
```

---

## 86. 一个最小日志实验

选择一个必然需要两步的任务，例如：

```text
读取 Cargo.toml，然后告诉我 package name
```

理想观察顺序：

```text
LoopStarted(0)
WaitingForModel
StreamingReasoning/Text
ToolCallDelta
Sampling Completed
ToolExecution
ToolStarted(read_file)
ToolCompleted(read_file)
LoopStarted(1)
WaitingForModel
StreamingText
Turn Completed
```

然后检查历史应包含：

```text
User
Assistant(tool_call)
ToolResult
Assistant(final answer)
```

---

## 87. 并发工具实验

让模型同时读取两个独立文件，记录：

- Pre-flight 顺序。
- Dispatch 开始时间。
- Completion 顺序。
- Conversation 中 Tool Result 顺序。
- Call ID 配对。

预期：

- Pre-flight 与 Permission 按模型顺序。
- Dispatch 可重叠。
- 完成顺序可以不同。
- 每个 Result ID 始终匹配原 Call ID。

---

## 88. 权限实验

分别测试：

1. Policy Deny。
2. 用户 Reject。
3. 用户 Cancel。
4. Follow-up Message。
5. PreToolUse Hook Deny。

不要只看 UI 文案，要记录最终：

```text
ToolLoop
TurnOutcome
Conversation 尾部 Items
是否再次 Sampling
```

这能验证相似的“未执行”展示背后有不同控制流语义。

---

## 89. Stream Ordering 实验

选择一个会先输出少量文本、再发 Tool Call 的模型响应，记录所有 ACP Event ID。

应满足：

```text
最后一个 AgentMessageChunk / AgentThoughtChunk eventId
    <
canonical ToolCall eventId
```

若发生 stream-drain 5 秒超时，日志会明确警告本 Turn 的 Event ID Ordering 可能不完美。

---

## 90. Stationarity 实验

单元测试可直接围绕 `IdenticalToolCallRun`：

- 相同 Signature 累加。
- 不同 Signature 重置。
- Nudge 每个连续 Run 只触发一次。
- 普通调用阈值 16。
- `true` No-op 阈值 4。

集成实验不要真的让模型浪费 16 轮；可通过测试构造状态并验证 Outcome。

---

## 91. 建议优先阅读的测试主题

使用 `rg` 找这些关键词：

```sh
rg "chat_history_integrity|stream.*drain|ToolLoop|permission.*reject|pending_interjection|identical_tool_call|structured_output|max_turns" \
  crates/codegen/xai-grok-shell/src/session
```

重点验证：

- Tool Call 与 Result 历史完整性。
- Cancel 后 Partial 不进入 Chat State。
- Interjection 中断 Wait Tool。
- Permission Decision 的分支差异。
- Structured Output 的有限重试。
- Stationarity 阈值。

---

## 92. 代码修改时的回归清单

若修改 Agentic Loop，应逐项确认：

- [ ] 流式 Chunk 仍在 ToolCall 通知前完成排序。
- [ ] canonical Assistant 只提交一次。
- [ ] ToolCallDelta 不被当作完整参数执行。
- [ ] 每个 Tool Call 都获得 Tool Result，包括失败、拒绝和跳过。
- [ ] 多 Tool Call 的 Result 依靠 ID 关联。
- [ ] Deferred User Item 不切断 Assistant/ToolResult 结构。
- [ ] Tool Error 仍能反馈模型继续修正。
- [ ] User Reject 不会被自动绕过。
- [ ] Plan Mode Gate 仍早于 Always Approve 快路径。
- [ ] MaxTurns、Stationarity、Structured Output 重试都有上限。
- [ ] Compaction 与 Auth Resubmit 会重建 Request。
- [ ] Cancelled Streaming Partial 不进入后续上下文。

---

## 93. 设计推断：为什么采用 Actor + Loop + Event Drainer

以下是基于源码结构的设计推断：

- Chat State Actor 提供 Conversation 写入顺序与请求快照的一致性。
- Turn Loop 集中表达 Agent 的语义控制流。
- Sampler Actor 隔离 Provider、Retry 与 Stream 解析。
- Event Drainer 让 UI 更新不必等待完整响应。
- Stream Barrier 修补并行任务在权威事件序号上的排序需求。
- Tool Batch 把交互式审批与高吞吐执行分成串行、并行两阶段。

它们不是重复的抽象，而是在一致性、实时性和并发吞吐之间分工。

---

## 94. 学习者应能回答的十个问题

读完后应能不看文档回答：

1. 为什么一个 User Turn 可以有多个模型请求？
2. Text Chunk 与 Assistant Conversation Item 有什么不同？
3. 为什么 ToolCallDelta 不能直接执行？
4. stream-drain barrier 在保护什么顺序？
5. Tool Pre-flight 为什么串行，Dispatch 为什么并行？
6. Tool Result 怎样与 Tool Call 配对？
7. 工具执行失败为何通常不会终止 Turn？
8. Policy Deny 与 User Reject 的控制流有何不同？
9. 哪些情况会在没有普通 Tool Call 时继续采样？
10. `Completed`、`Cancelled`、`MaxTurnsReached`、`StationarityEnded` 有何区别？

---

## 95. 一句话记忆模型

```text
Agentic Loop =
  从权威 Conversation 重建请求
  + 将 Provider Stream 映射成即时显示
  + 将完整响应提交为 canonical history
  + 把 Tool Call 先串行治理、再安全并发执行
  + 把成功与失败都变成模型可观察的 Tool Result
  + 在下一次采样前完成状态屏障
  + 用明确上限与终态收敛
```

---

## 96. 本篇术语表

| 名词 | 白话解释 | 在本篇中的精确含义 |
| --- | --- | --- |
| Agentic Loop | 模型反复观察、行动、再观察的循环 | `process_conversation_turn` 内部的请求—响应—工具—再请求循环 |
| Turn | 用户可感知的一轮交互 | 从一个 Prompt 被处理到 `TurnOutcome` 返回，可含多个 Sampling Round |
| Sampling Round | 一次完整模型请求 | 一次 `ConversationRequest` 经 Sampler 得到 `ConversationResponse` |
| Model Call | 一次推理 API 调用 | 本文通常与 Sampling Round 同义；Sampler 内部 Retry 可能包含多个底层 Attempt |
| Attempt | 对 Provider 的一次尝试 | Retry 机制中的单次网络/推理尝试，不一定形成 Conversation Item |
| Conversation | 模型上下文的规范历史 | `ConversationItem` 的有序序列，由 Chat State Actor 管理 |
| canonical | 被系统认作权威、可持久化的版本 | 完整 `ConversationResponse` 中的 Item，而非临时 Stream Delta |
| Chat State | Conversation 的权威状态服务 | 负责追加消息、Token 状态与构建下一次请求的 Actor |
| Actor | 通过消息串行管理内部状态的异步组件 | Chat State、Sampler 等用 Handle/Channel 与 Actor Task 通信 |
| Sampler | 调用模型并统一不同 Backend 的层 | 负责 Provider Request、Stream Parse、Retry 与规范 Response |
| Streaming | 响应尚未完成时逐块到达 | Text、Reasoning、Tool Call Arguments 等以 Delta/Event 发送 |
| Chunk | 一小段流式内容 | 发给 UI 的 Text 或 Thought 增量 |
| Delta | 相对于当前累计状态的新片段 | Tool Name、ID 或 Arguments 的部分更新，单独可能不完整 |
| Drainer | 持续从 Channel 取事件的任务 | 消费 `SamplingEvent` 并映射为 ACP/UI Side Effect |
| Barrier | 在并发任务之间强制等待某个阶段完成 | stream-drain oneshot 保证 Chunk Event 先于 ToolCall Event |
| ACP | Agent Client Protocol | Shell 与客户端交换 Message Chunk、ToolCall、Update 等事件的协议 |
| Event ID | 客户端事件的全局递增序号 | 决定客户端按什么顺序重放与显示事件 |
| Reasoning | 模型的思考通道或结构化思考 Item | UI 用 `AgentThoughtChunk`，历史可保存为 sibling Item |
| sibling item | 与 Assistant 并列的响应项 | Reasoning、BackendToolCall 等按 Provider 输出顺序平铺保存 |
| Backend Tool | Provider 服务器执行的工具 | Shell 只显示和持久化，不做本地 Dispatch |
| Client Tool | 模型请求客户端执行的 Function | 进入 Shell 的 Pre-flight、Permission 与 Dispatch |
| Tool Call | 模型提出的函数调用请求 | 含稳定 Call ID、Tool Name 与完整 Arguments |
| Tool Result | 对 Tool Call 的观察结果 | 绑定 Call ID 写回 Conversation，供下一次模型请求读取 |
| Pre-flight | 工具真正执行前的治理阶段 | MCP 检查、参数解析、Plan Gate、Hook、Permission、Plan Approval |
| Dispatch | 把获批调用交给实际 Runtime | `dispatch_tool` 通过 Workspace Ops / ToolBridge 执行 |
| Post-flight | 工具完成后的处理阶段 | UI Update、Tool Result、Hook、Skill Update 与 Telemetry |
| Prepared Tool Call | 通过执行前检查的冻结调用 | 包含解析参数、目标、ID、只读分类等并发执行所需信息 |
| ToolBridge | 模型工具名与实际 Runtime 间的桥 | 负责解析、别名、Meta Tool 目标与 Toolset Dispatch |
| AccessKind | 权限系统理解的动作类型 | Read、Edit、Bash、MCPTool、WebFetch 等 |
| Permission | 执行敏感动作前的授权决策 | 可来自用户、规则、Auto Classifier 或 Always Approve 模式 |
| Policy Deny | 配置或安全策略禁止动作 | 结果反馈模型，通常允许 Agent 换方案 |
| User Reject | 用户明确拒绝本次动作 | 通常返回 Cancelled，结束当前 Turn |
| Hook | 在生命周期节点运行的扩展逻辑 | `PreToolUse` 可拒绝，`PostToolUse`/Failure 观察结果 |
| Plan Gate | Plan Mode 的能力边界检查 | 在权限前阻止非计划文件写入，即使 Always Approve 也不能绕过 |
| `FuturesUnordered` | 谁先完成就先产出谁的并发集合 | 用于同时执行一批获批 Tool Calls |
| File Lock | 同一路径操作的异步互斥锁 | 保留跨路径并发，同时串行化相关读写/写写动作 |
| Join Key | 跨事件关联同一逻辑对象的键 | Tool Call ID 用于关联 ToolCall、ToolUpdate 与 ToolResult |
| Deferred Follow-up | 暂缓追加的合成 User Item | 工具批次结束后再写入，避免切断 Call/Result 历史结构 |
| Interjection | 用户在 Turn 运行中追加的输入 | Drain 后写入上下文并触发下一次 Sampling Round |
| TodoGate | 根据 Todo 状态阻止过早结束的门 | 注入 Reminder 并有限次继续采样 |
| Structured Output | 必须符合 JSON Schema 的最终结果 | 走原生 Schema 或合成 `StructuredOutput` Tool 路径 |
| Compaction | 压缩长 Conversation 以释放上下文 | 完成后重新从 Chat State 构建 Request |
| Resubmit | 不结束 Turn，修改状态后重发模型请求 | 典型是 Compaction 或 401 Credential Recovery |
| Stationarity | Agent 连续重复相同行动、没有进展 | 先 Nudge，达到阈值后返回 `StationarityEnded` |
| True No-op | 明确什么也不做的动作 | 当前特别识别单个 Bash `true`，使用更低 Hard Stop 阈值 |
| Doom Loop | 采样内容层面的异常重复 | 主要由 Sampler 检测与重试，不等同于 Tool Action Stationarity |
| StopReason | Provider 生成停止原因的统一表示 | Stop、Length、ToolCalls、ContentFilter |
| `ToolLoop` | 工具阶段给 Turn Driver 的控制结果 | Continue、Reject、Cancelled、Followup、HookDenied 等 |
| `TurnOutcome` | 一个 Agent Turn 的最终控制结果 | Completed、Cancelled、MaxTurnsReached、StationarityEnded |
| Bookkeeping | Turn 完成前的统一收尾 | Plan Cleanup、Signals Snapshot、持久化、Telemetry 与 Feedback |
| Completion Requirement | 某 Agent 完成前必须满足的工具条件 | 若缺失，可在基础 Turn 外注入 Recovery Prompt 再运行 |
| Streaming Capture | 未提交流式生成的诊断副本 | Trace-only，不写 Conversation，不进入后续模型上下文 |
| KV Cache | Provider 对相同 Prompt 前缀的缓存 | 保持 Conversation Item 顺序稳定有助于缓存命中 |
| Telemetry | 用于观测运行行为的数据 | Token、延迟、Tool Outcome、Retry、StopReason 等日志与事件 |

更多通用名词见 [全局术语表](../appendices/glossary.md)。

---

## 97. 下一篇建议

到这里已经串起：

```text
Prompt -> Skill -> Tool -> Agentic Loop
```

下一篇最值得继续精读 Chat State Actor：

```text
Conversation Item 如何追加
  -> BuildRequest 如何取快照
  -> History Repair 如何维护 Function Call / Result 完整性
  -> Token Usage 与 Prompt Index 如何更新
  -> Compaction 如何替换历史而不破坏下一轮请求
```

它会回答本篇反复依赖但尚未完全展开的问题：为什么 Chat State 能成为 Agent Loop 的权威状态源。
