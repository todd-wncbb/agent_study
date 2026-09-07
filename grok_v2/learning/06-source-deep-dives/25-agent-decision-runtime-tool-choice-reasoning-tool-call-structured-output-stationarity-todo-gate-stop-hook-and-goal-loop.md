# 源码精读 25：Agent Decision Runtime——Tool Choice、Reasoning、Tool Call、Structured Output、Stationarity、TodoGate、Stop Hook 与 Goal Loop

> 源码基线：`ed6d543`
>
> 上一篇研究“模型在一次请求中看见什么”；本篇继续回答更关键的问题：模型输出一个行动建议后，究竟是谁决定执行、重试、继续采样、拒绝、强制工作或真正结束。

---

## 1. 先给结论：Agent 的下一步不是由模型单独决定

Grok Build 中不存在一个名叫 `decide_next_action()` 的总函数。

最终行动由多层决策叠加产生：

```text
请求侧约束
  Tool Catalog / Tool Choice / JSON Schema / Prompt
                         │
                         ▼
模型候选决定
  visible text / reasoning / tool_calls / stop_reason
                         │
                         ▼
Turn 内层 Runtime
  response 归一化 → StructuredOutput → Tool prepare/execute
  → Stationarity → TodoGate → Interjection → candidate completion
                         │
                         ▼
Turn 外层 Runtime
  Refusal → Goal evaluator → Stop Hook → final TurnOutcome
                         │
                         ▼
协议终态
  EndTurn / Refusal / Cancelled / MaxTurnsReached
```

模型拥有“提出下一步”的权力；Runtime 拥有“这个下一步是否合法、是否执行、是否已经足够、是否还能继续”的权力；用户、策略、Hook 和资源预算又能覆盖其中一部分决定。

因此最重要的一条阅读原则是：

> Assistant 输出“我完成了”，只是候选停止信号；它不等于整个 Agent Turn 已经结束。

---

## 2. 本篇要回答的核心问题

- `tools` 和 `tool_choice` 有什么区别？
- 普通 Agent Turn 是否强制模型调用 Tool？
- Reasoning 文本会不会直接被 Runtime 当作控制指令？
- `stop_reason=tool_calls` 与实际 `tool_calls` 谁更可信？
- 一个 Response 同时有正文和 Tool Call 时，为什么仍会执行 Tool？
- Tool 名不存在、参数错误、Hook deny、Permission reject 分别怎样处理？
- 多个 Tool Call 是否并发，写同一个文件时怎样避免冲突？
- Structured Output 为什么有 native schema 和 synthetic tool 两条路线？
- 为什么普通重复 Tool Call 第 8 次提醒、第 16 次终止，而 `true` 第 4 次就终止？
- TodoGate 为什么只拦截一部分未完成 Todo？
- Goal Loop 与 Stop Hook 谁先运行？
- Refusal、Stationarity、Max Turns 为什么不能被 Stop Hook 重新打开？
- 最终 ACP `StopReason` 怎样由内部 `TurnOutcome` 投影出来？

---

## 3. 核心源码地图

| 主题 | 主要源码与符号 |
|---|---|
| Turn 外层循环 | `xai-grok-shell/src/session/acp_session_impl/turn.rs`：`handle_prompt` |
| Turn 内层循环 | 同文件：`process_conversation_turn` |
| Recovery 包装 | 同文件：`process_conversation_turn_with_recovery` |
| Turn 结果类型 | `acp_session_impl/types.rs`：`TurnOutcome` |
| Tool Loop 结果 | 同文件：`ToolLoop` |
| 请求结构 | `xai-grok-sampling-types/src/conversation.rs`：`ConversationRequest` |
| Tool Choice | 同文件：`ConversationToolChoice` |
| 归一化响应 | 同文件：`ConversationResponse`、`StopReason` |
| Provider 请求转换 | `conversation/responses.rs`、`chat_completions.rs`、`messages.rs` |
| Tool 预处理和执行 | `acp_session_impl/tool_calls.rs` |
| Structured Output | `turn.rs`：`handle_structured_output_tool_call` |
| 重复行动保护 | `turn.rs`：`IdenticalToolCallRun` |
| TodoGate | `acp_session_impl/reminders.rs`：`evaluate_todo_gate` |
| Stop Hook Gate | `acp_session_impl/stop_gate.rs`：`run_stop_gate` |
| Goal Loop | `acp_session_impl/goal.rs`：`run_goal_round_end` |
| Sampler 与空响应恢复 | `acp_session_impl/sampler_turn.rs` |
| 最终协议投影 | `turn.rs`：`PromptTurnOk` 构造逻辑 |

---

## 4. 先统一三个容易混淆的“轮次”

### 4.1 User Turn

用户提交一次 Prompt，到 Shell 返回一个协议终态。

它是用户最直观理解的“一轮”。

### 4.2 Agentic Loop Round

`process_conversation_turn` 内每调用一次模型，就是一个采样 Round。

```text
模型 A → Tool → Result → 模型 B → Tool → Result → 模型 C
```

这仍可以属于同一个 User Turn。

### 4.3 Goal / Stop Continuation Round

当一次 `process_conversation_turn` 已返回 `Completed` 后：

- Goal evaluator 可以注入 continuation，再启动一个完整内层 Turn；
- Stop Hook 也可以回填 feedback，再启动一个完整内层 Turn。

所以一次 User Turn 还可能包含多个“已经候选完成的内层 Turn”。

### 4.4 为什么必须分清

`max_turns` 统计的是 Tool execution cycle，而 Stop Hook continuation 有自己独立的上限；Goal Loop 又受 Goal budget、状态和 evaluator 控制。把它们统称为“模型轮数”，会读错每个保险丝的边界。

---

## 5. 请求侧第一层：Tool Catalog 只提供候选行动空间

每次内层采样前，`process_conversation_turn` 都会得到 `effective_tools: Vec<ToolSpec>`。

一个 `ToolSpec` 主要包含：

```rust
name
description
parameters // JSON Schema
```

它告诉模型：

- 有哪些动作；
- 动作的语义；
- 参数应该长什么样。

它不等于：

- Runtime 保证 Tool 一定还存在；
- Tool 一定获得权限；
- 参数一定有效；
- Tool 一定能成功执行；
- 模型必须选一个 Tool。

Tool Catalog 是“候选动作说明书”，不是“执行授权表”。

---

## 6. `tools` 与 `tool_choice` 是两条独立控制轴

`ConversationRequest` 同时持有：

```rust
pub tools: Vec<ToolSpec>,
pub hosted_tools: Vec<HostedTool>,
pub tool_choice: Option<ConversationToolChoice>,
```

可以用下表理解：

| 字段 | 回答的问题 |
|---|---|
| `tools` | 模型可以调用什么？ |
| `hosted_tools` | Provider 可在服务端直接执行什么？ |
| `tool_choice` | 模型是否必须、禁止或指定调用某个 Tool？ |

`ConversationToolChoice` 有四种：

```rust
Auto
None
Required
Function(String)
```

- `Auto`：模型自己决定是否调用；
- `None`：禁止 Tool；
- `Required`：必须调用某个 Tool；
- `Function(name)`：必须调用指定 Tool。

---

## 7. 一个重要源码事实：普通交互 Turn 没有主动设置 `tool_choice`

普通 Agentic Loop 的 `build_request(...)` 会携带 Tool Specs，但在本基线中没有在 `turn.rs` 为普通请求补上 `Required` 或具体 Function。

换句话说，普通 Agent Turn 的典型状态是：

```text
tools = 非空候选集合
tool_choice = None（不显式覆盖 Provider 默认）
```

这表示 Grok Build 通常通过以下方式影响行动：

- System Prompt 和 Reminder；
- Tool 的名字、描述和 Schema；
- Tool Result 中的纠错反馈；
- Runtime Gate；

而不是每轮都在协议层强制“必须调用 Tool”。

### 7.1 不要把 Rust 的 `Option::None` 与协议的 `tool_choice: none` 混淆

这里尤其容易误读：

```text
request.tool_choice == None
```

表示“请求中不显式设置该字段”，不等于：

```text
ConversationToolChoice::None
```

后者才表达“禁止 Tool”。

---

## 8. Tool Choice 的 Backend 转换并非完全对称

统一类型最终要转成不同 Provider API：

### 8.1 Responses API

四种 Choice 都有直接映射：`auto`、`none`、`required`、指定 function。

### 8.2 Chat Completions

同样能映射为 `auto`、`none`、`required` 和指定 function；如果根本没有 Tool，代码会省略 `tool_choice`，避免兼容后端拒绝请求。

### 8.3 Messages API

源码映射中：

```rust
ConversationToolChoice::None => ToolChoiceParam::Auto
```

也就是说，这条 Backend 适配并没有把统一层的 `None` 映射成一个真正的“禁用 Tool”值。

这是阅读统一抽象时必须牢记的限制：

> 统一 enum 表达了上层意图，但具体 Provider 转换能力可能并不完全同构。

普通 Agent Turn 没有显式使用这个禁用值，因此它不直接改变日常 Agentic Loop；但如果未来新增强依赖 `None` 的调用点，就必须重新审视 Messages Backend 的行为。

---

## 9. 某些专用请求会真正使用强 Tool Choice

全仓搜索可以看到：

- Session title 生成使用 `Function("session_title")`；
- Compaction 根据配置使用 `Auto` 或 `None`；
- 普通 Goal evaluator 请求将 `tool_choice` 设为未指定；
- 普通 Turn 也通常未指定。

这说明 Tool Choice 不是 Agent 全局常量，而是“某种模型调用”的请求级策略。

---

## 10. Reasoning、正文、Tool Call 和 Stop Reason 是四类不同信号

归一化后的 `ConversationResponse` 同时可能包含：

- `Reasoning` sibling items；
- trailing `Assistant` 的 visible content；
- trailing `Assistant` 的 `tool_calls`；
- `stop_reason`；
- `stop_message`；
- usage 和模型指纹等元数据。

它们不能互相代替。

### 10.1 Reasoning 是模型生成内容，不是 Runtime 控制协议

Runtime 可以保存、展示或重放 Reasoning，但不会解析一句“接下来调用 bash”并据此直接执行 Bash。

真正进入 Tool Runtime 的是结构化 `tool_calls()`。

### 10.2 Visible Text 也不是 Tool 指令

Assistant 正文中写：

```text
我将读取 src/main.rs
```

不会执行任何工具。只有合法 Tool Call 才进入 `execute_tool_calls`。

### 10.3 Stop Reason 是生成终止原因

统一层 `StopReason` 有：

```rust
Stop
Length
ToolCalls
ContentFilter
```

它描述 Provider 为什么停止本次生成，不直接等于 Shell 为什么结束整个 User Turn。

---

## 11. 实际分支首先看结构化 Tool Call 数组

`process_conversation_turn` 在响应回来后执行：

```rust
let mut tool_calls = response.tool_calls().to_vec();
```

后续核心分支是：

```text
tool_calls.is_empty() ?
├── 是：候选完成路径
└── 否：StructuredOutput 或普通 Tool 执行路径
```

因此即使响应带有可见正文，只要还有 Tool Call，Runtime 仍会执行 Tool 并继续采样。

这是 Agent 常见行为的源码根源：模型可以先解释，再行动；正文出现不代表 Turn 已结束。

---

## 12. `stop_reason` 与 `tool_calls` 不一致时怎样理解

本层代码没有写成：

```rust
if stop_reason == ToolCalls { execute_tools() }
```

它直接读取 canonical Tool Call 数组。

这是一种更稳健的设计：

- `stop_reason` 负责描述 Provider terminal reason；
- Tool Call item 负责提供可执行载荷；
- Runtime 只对真实存在的结构化载荷执行动作。

如果 Provider 声称 `ToolCalls` 却没有可执行 Tool Call，本层会落到“无 Tool Call”的候选完成路径；更早的 Sampler 空响应与重试逻辑可能先处理异常形态。

---

## 13. Reasoning-only 为什么被视作空响应

`ConversationResponse::empty_reason()` 的规则是：

- Assistant 有 visible content：非空；
- Assistant 有 Tool Call：非空；
- 两者都没有，即使存在 Reasoning：仍是 `ReasoningOnly` 空响应；
- 什么都没有：`NoVisibleContent`。

设计含义是：

> 模型只“想了”，但既没有告诉用户结果，也没有发出结构化行动，不算完成了一步。

空响应恢复由 Sampler/Session 的恢复逻辑处理，而不是把私有 Reasoning 当最终答案。

---

## 14. Response 先提交历史，再决定下一步

响应被归一化后：

- Assistant item 通过 `record_assistant_response` 提交；
- 其他 sibling item 也进入 Chat State；
- 丢失流式正文 chunk 时，`fallback_text()` 补发 UI 更新；
- 然后 Runtime 才进入 Tool / Completion 分支。

这条顺序保证下一次采样看得到：

- 模型刚才说了什么；
- 它刚才请求了哪些 Tool；
- Tool Result 应该与哪个 Tool Call 配对。

否则会出现“工具执行成功，但下一轮历史里没有对应 Assistant Tool Call”的非法对话结构。

---

## 15. 内层 Agentic Loop 的完整骨架

可把 `process_conversation_turn` 压缩为下面的伪代码：

```rust
prepare_base_tool_definitions_once();
initialize_turn_counters();

loop {
    stationarity_hard_stop_if_needed();
    inject_stationarity_nudge_if_needed();
    drain_interjections_and_runtime_reminders();
    maybe_compact();

    effective_tools = resolve_tools_for_this_round();
    request = build_request(effective_tools);
    response = sample_with_recovery(request);
    persist_canonical_response(response);

    if response.tool_calls.is_empty() {
        maybe_todo_gate_and_continue();
        drain_interjections_twice();
        return TurnOutcome::Completed;
    }

    maybe_handle_structured_output();
    observe_identical_tool_step();
    result = execute_tool_calls();

    maybe_cancel_on_permission_result();
    maybe_add_followup_and_continue();
    enforce_max_turns();
    maybe_preflight_compact();
}
```

注意：Stationarity hard stop 在下一轮开头检查，因为当前重复 Tool Call 的结果需要先被提交。

---

## 16. 每轮重新算什么，整 Turn 只算一次什么

### 16.1 整个内层 Turn 开始时准备一次

```rust
let (tool_definitions, ...) = prepare_tool_definitions_timed().await;
```

这是基础 Tool Definition 快照。

### 16.2 每个模型 Round 重新计算

- pending Interjection；
- pending Skill reminder；
- monitor event；
- date、MCP、Memory reminder；
- auto-compaction 条件；
- forked Tool override 或 turn base specs；
- StructuredOutput synthetic tool；
- hosted tools；
- token budget clamp；
- 完整 `ConversationRequest`。

因此 Tool 的基础描述相对稳定，但本轮可见集合、动态提醒和历史会演进。

---

## 17. Tool Call 不是直接 Dispatch：先走 Prepare 阶段

`execute_tool_calls` 会把每个调用交给 `prepare_tool_call`。

Prepare 阶段大致包含：

1. 向客户端登记 Pending Tool Call；
2. 识别 MCP wire name；
3. 必要时等待或刷新 MCP；
4. 解析 JSON 参数；
5. 用 ToolBridge 转成 typed `ToolInput`；
6. 判断 Plan Mode edit gate；
7. 执行 `PreToolUse` file/client hooks；
8. 计算 `AccessKind`；
9. 请求或自动作出 Permission Decision；
10. 形成 `PreparedToolCall`。

只有通过这些步骤的调用才会进入真正 Dispatch。

---

## 18. 参数解析包含一层容错，但最终仍以 Typed Parse 为准

Tool arguments 先按 JSON 解析。

若失败，Runtime 还会尝试识别“模型把多个 JSON 对象粘在一起”的情况，并挑选第一个能被目标 Tool typed parser 接受的对象。

如果不能恢复：

- 原始参数会被包装或反馈；
- `handle_tool_parse_error` 生成 Tool Result；
- 返回 `ToolLoop::ToolParsingError`；
- 外层不会把它当终止 Turn 的理由；
- 下一次模型采样可以根据错误修正参数。

这体现了一个通用 Agent 设计：

> 模型输出错误优先转成可见反馈闭环，而不是立刻把整个 Turn 变成基础设施错误。

---

## 19. 不存在的 Tool 通常也是可恢复错误

例如 Progressive MCP 初始化期间调用不可用 MCP Tool：

- Runtime 写入错误 Tool Result；
- 提示使用 `search_tool` 找可用 Tool；
- 返回 `ToolLoop::NonExistingTool`；
- 内层循环之后继续让模型修正。

`NonExistingTool` 和 `ToolParsingError` 在 telemetry 中归为 `InvalidTool`，但它们通常不是 User Turn 的 terminal cancellation。

---

## 20. Plan Mode 拒绝编辑为什么返回 `Continue`

Plan Mode edit gate 拒绝修改时：

- Tool 不执行；
- Runtime 写入“未执行”的 Tool Result；
- 返回 `ToolLoop::Continue`；
- 模型获得反馈后应改成只读探索或计划输出。

这是“动作被拒绝”与“整个 Turn 被取消”的区别。

---

## 21. PreToolUse Hook deny 是非终止控制

`ToolLoop::HookDenied` 的注释明确说明：

- Hook deny reason 会回填；
- Tool 不执行；
- Turn 继续；
- 下一轮模型可以改用其他方案。

在 `turn.rs` 中：

```rust
Ok(ToolLoop::HookDenied { .. }) => {}
```

随后仍走 `max_turns`、compaction 和下一轮采样。

所以 Hook deny 是“禁止这个动作”，不是“终止整个任务”。

---

## 22. Permission Reject 是终止级决定

用户在 Permission Prompt 选择拒绝时，`execute_tool_calls` 返回：

```rust
ToolLoop::PermissionReject { tool_name, reason }
```

`process_conversation_turn` 立即投影为：

```rust
TurnOutcome::Cancelled {
    category: PermissionRejected,
    context: { tool_name, reason }
}
```

这不会让模型继续游说用户或偷偷换一种等价写法执行。

用户取消 Permission Prompt 则变成 `PermissionCancelled`。

---

## 23. Permission 对话中的 Follow-up Message 会改变当前方向

用户不只是 Allow/Reject，还可能在 Permission 交互中发送新消息。

此时返回：

```rust
ToolLoop::FollowupMessage(String)
```

Turn 会：

1. 把 follow-up 作为新的 User item 加入历史；
2. 不结束；
3. 重新采样；
4. 让模型基于新意图重新决定。

这与 Permission Reject 的终止语义不同。

---

## 24. Batch 中出现终止性 Prepare 结果时，后续调用不会悄悄执行

Prepare Tool batch 时，一旦出现：

- Permission Reject；
- user cancellation；
- Follow-up Message；

`final_result` 会被锁定。

同 batch 后续未 Prepare 的调用会收到对应的取消 Tool Result，例如“由于之前的 permission rejection，本调用被取消”。

这避免模型一次生成多个动作时，用户拒绝第一个危险动作后，后面的动作仍继续执行。

---

## 25. 多 Tool Call 的执行模型：先全量 Prepare，再并发 Dispatch

通过 Prepare 的 Tool Call 被收集到 `approved`。

随后 Runtime 为它们构造 futures，放入 `FuturesUnordered` 并发执行；结果按照完成顺序 drain。

这意味着：

- 模型输出顺序不等于完成顺序；
- 每个结果依靠 Tool Call ID 与原调用关联；
- UI 和 telemetry 不能靠数组位置猜配对关系；
- 所有结果回填后才进入下一次模型采样。

---

## 26. ExitPlanMode Tail 为什么被拆出来

多个 Tool Call 会先通过 `split_exit_plan_tail` 分成 body 和 tail，分批执行。

这是在表达顺序语义：退出计划模式一类控制动作不能与前面的普通动作完全无序并发，否则会让同一个 batch 中的权限和模式边界含糊。

代码并不是“只要多个 Tool 就全部盲目并发”，而是先识别需要序列化的控制尾部。

---

## 27. 同一文件的并发访问怎样约束

Batch 会收集所有非只读调用涉及的路径，建立 path → async mutex。

只要某个路径在 batch 中存在写操作，相关调用执行前就获取同一把 path lock。

因此：

- 不同路径可以并发；
- 同路径且涉及写入的调用被串行化；
- 不必把整个 Tool batch 全局串行。

这是并发度与文件一致性之间的局部折中。

---

## 28. Tool 执行错误通常也回到模型，而不是直接终止

Dispatch 返回错误时：

- `handle_tool_error` 构造错误反馈；
- 可触发 `PostToolUseFailure` Hook；
- Tool outcome 记录为 Error；
- `ToolLoop` 通常仍为 `Continue`；
- 下一轮模型决定修复、改用其他 Tool 或向用户解释。

只有被明确分类为 Turn 终止的交互决定，才会转成 `TurnOutcome::Cancelled`。

---

## 29. Tool Success 后也不只是写一个字符串

成功路径会：

- 计算 effective tool name；
- 回填 canonical Tool Result；
- 收集 deferred follow-up items；
- 更新 search prompt index 等运行态；
- 应用 pending Skill discovery update；
- 触发 `PostToolUse`；
- 记录 signals、events 和 telemetry；
- 最后 drain Interjection 与 Skill reminder。

Tool Result 既是模型下轮输入，也是 UI、审计、Skill 动态发现和运行态推进的交汇点。

---

## 30. 可中断等待 Tool 与普通 Tool 的区别

某些等待类 Tool 会与 `wait_for_pending_interjection` 做 `tokio::select!`。

如果用户中途发送 Interjection：

- 等待动作可以返回一个“被新输入中断”的结果；
- Runtime 更快进入安全点；
- 新用户意图被 drain 进历史；
- 模型重新决定。

普通不可中断 Tool 不会因为 Interjection 自动撤销副作用。

---

## 31. 每个 Tool cycle 后的 `max_turns`

`tool_turn_count` 从 1 开始。

每次完成一轮 Tool execution 后计算：

```rust
let next_turn = tool_turn_count + 1;
if next_turn > limit {
    return TurnOutcome::MaxTurnsReached { limit };
}
```

所以它限制的是“Tool 驱动的内层继续轮数”，不是 Stop Hook 或 Goal evaluator 的统一总模型调用数。

`MaxTurnsReached` 最终对 ACP 投影为 `Cancelled`，并通过 completion kind 保留更精确原因。

---

## 32. Structured Output 有两条完全不同的执行路线

当用户提供 JSON Schema 时，Turn 先编译 validator，并检测当前 Backend 是否支持 native schema。

```text
schema 合法 + backend 原生支持
    → request.json_schema

schema 合法 + backend 不原生支持
    → 注入 StructuredOutput synthetic tool

schema 非法
    → 不启用上述强约束路线，最终保留 validation error 语义
```

---

## 33. Native Structured Output 路线

支持 native schema 时：

```rust
request.json_schema = json_schema.clone();
```

模型返回无 Tool Call 的最终文本后，Runtime 使用同一个 validator：

1. 把 Assistant text 解析为 JSON；
2. 校验 JSON Schema；
3. 将结果放进 `TurnOutcome::Completed.structured_output`。

这里的结构化载荷来自正文，不需要本地执行一个真实 Tool。

---

## 34. Synthetic `StructuredOutput` Tool 路线

不支持 native schema 时，Runtime 会：

1. 注入 system reminder，要求所有 Tool 完成后调用它；
2. 每轮把名为 `StructuredOutput` 的 ToolSpec 附加到 effective tools；
3. 直接把用户 Schema 作为它的参数 Schema；
4. 截获它，不交给普通 ToolBridge dispatch；
5. 对 arguments 做 JSON 解析和 Schema 校验。

它本质上是“用 Function Calling 模拟 Structured Output 协议”。

---

## 35. 为什么 `StructuredOutput` 必须单独调用

如果同一 Response 同时包含：

```text
StructuredOutput + 一个或多个普通 Tool
```

Runtime 不接受这次 Structured Output：

- 给每个 StructuredOutput call 回填纠错 Tool Result；
- 从数组中删除它们；
- 继续执行普通 Tool；
- 下一轮要求最终结果重新单独提交。

原因很直接：如果仍有真实动作要执行，所谓“最终结构化结果”还不是最终状态。

---

## 36. Structured Output 的三态决策

`StructuredOutputStep` 有三个分支：

```rust
Complete(Result<Value, String>)
Retry
Proceed
```

- `Proceed`：本响应没有它，继续普通 Tool 流程；
- `Retry`：它是唯一 Tool，但参数无效且还有重试预算；
- `Complete`：校验成功，或校验失败但已耗尽重试预算。

这个 enum 把“是否截获 Tool Call”和“是否结束 Turn”合成一个清晰协议。

---

## 37. Structured Output 最多纠错三次

无效 arguments 且：

```rust
retries < STRUCTURED_OUTPUT_MAX_RETRIES // 3
```

Runtime 会写入：

```text
具体错误
Fix the arguments and call StructuredOutput again.
```

然后重新采样。

三次重试耗尽后，不再无限循环，而是以：

```rust
structured_output: Some(Err(error))
```

完成 Turn。

“协议完成”不等于“业务数据校验成功”；调用方必须检查内部 `Result`。

---

## 38. Structured Output 为什么绕过 TodoGate

无 Tool Call 候选完成路径中，TodoGate 的条件包含：

```rust
!schema_ok
```

即有效 Structured Output schema 启用时，不执行普通 TodoGate。

设计上可以理解为：结构化输出请求有自己严格的终态协议，不能被一个面向普通自主任务的 Todo reminder 擅自改变输出契约。

---

## 39. Action Stationarity：Runtime 不相信无限重复就是进展

每次普通 Tool Call batch 执行前，Turn 会生成 step signature：

```text
tool_name US arguments RS tool_name US arguments ...
```

然后由 `IdenticalToolCallRun` 记录：

- 上一次 signature hash；
- 当前连续次数；
- Tool 名；
- 是否 `true` no-op；
- 本次 run 是否已经提醒过。

只比较连续相同的整批调用；只要 signature 变化，计数归 1。

---

## 40. 普通重复调用的两个阈值

源码常量：

```rust
NUDGE_AFTER_IDENTICAL_TOOL_CALLS = 8
MAX_CONSECUTIVE_IDENTICAL_TOOL_CALLS = 16
```

含义是：

- 连续第 8 次相同调用完成后，下一轮开头注入一次 nudge；
- 如果仍持续，到 16 次后，下一轮开头直接结束；
- 同一个连续 run 只 nudge 一次；
- signature 一变化，nudge latch 和计数都重置。

---

## 41. 为什么检查发生在下一轮开头

执行本轮 Tool 前，Runtime 才能 `observe` 本轮 signature；Tool Result 还需要完整写入历史。

因此流程是：

```text
第 N 次相同 Tool Call
→ 执行并提交 Tool Result
→ 进入下一轮
→ 检查 N 是否到阈值
→ nudge 或 hard stop
```

源码注释也明确要求 nudge 在 results committed 之后触发。

---

## 42. `true` no-op 有更严格的阈值

单一 Bash Tool，typed parse 后命令满足：

```rust
cmd.trim().eq_ignore_ascii_case("true")
```

就被视为 `true` no-op step。

它的 hard stop 是：

```rust
MAX_CONSECUTIVE_TRUE_NOOPS = 4
```

所有这类 step 使用特殊 signature `\0true_noop`，因此即使外层 Tool wire name 或其他无关表示有所差异，也按同一种无进展动作处理。

`true` 在 shell 中成功但不产生进展，允许它重复 16 次没有意义。

---

## 43. Stationarity 终态为什么不是普通 Completed

hard stop 返回：

```rust
TurnOutcome::StationarityEnded { snapshot }
```

而不是 `Completed`。

类型注释说明其目的：

> Silent EndTurn after stationarity/true-noop thrash；必须与 Completed 区分，防止 recovery、Goal 或 Stop Hook 再次打开采样循环。

协议层最终仍向用户投影 `EndTurn`，但内部 completion kind 保留 `StationarityEnded`，便于观察真正原因。

---

## 44. 无 Tool Call 只是进入候选完成路径

模型响应没有 Tool Call 后，Runtime 并不立即 return。

它依次考虑：

1. TodoGate；
2. pending Interjection；
3. turn bookkeeping；
4. late Interjection；
5. Structured Output native validation；
6. 返回 `TurnOutcome::Completed`。

随后外层还会考虑 Refusal、Goal 和 Stop Hook。

---

## 45. TodoGate 的输入不是一句“还有 Todo”

`collect_todo_gate_input` 收集：

- Todo insertion order 中的 Pending；
- InProgress；
- 当前 Prompt 的 outstanding subagents 数量；
- 未完成 Bash/Monitor background tasks 数量。

后两者相加得到：

```rust
backing_task_count
```

然后按 insertion order，把前 N 个 InProgress 视为有后台工作支撑，剩余部分视为 unbacked。

---

## 46. TodoGate 真正拦截什么

纯函数 `evaluate_todo_gate` 只有在以下两者都为空时才放行：

```text
pending
in_progress_unbacked
```

所以：

- Completed / Cancelled Todo 不影响；
- 有 Pending 会触发；
- 没有后台任务支撑的 InProgress 会触发；
- 有足够 background task / subagent 支撑的 InProgress 不触发。

这避免 Agent 启动长任务后，因为 Todo 仍是 InProgress 而被迫无意义轮询。

---

## 47. “后台任务支撑”是计数匹配，不是 ID 级关联

当前实现将 InProgress 按插入顺序分区：

```rust
backed_count = min(in_progress.len(), backing_task_count)
```

它没有证明“task-7 确实对应 todo-3”。

这是一个务实启发式：足以避免明显的错误结束和紧密轮询，但不是完整的 Todo ↔ Task 关系模型。

阅读日志时不要把 `in_progress_backed` 误解成已建立强外键。

---

## 48. TodoGate 的启用范围受 Policy 控制

`todo_gate_policy()` 会结合：

- reminder policy 是否 enabled；
- Prompt audience；
- Agent definition；
- Goal harness 是否启用；
- 当前 Goal status；

决定 Gate 是否 active。

此外 Turn 层还排除：

- Structured Output schema 已成功编译；
- Content Filter refusal。

所以不能根据“列表里有 Pending”就断言一定会继续。

---

## 49. TodoGate 有每 Prompt 的 fire cap

触发且未到上限时：

1. `todo_gate_fires += 1`；
2. 记录 event 和 telemetry；
3. 渲染 reminder；
4. 追加 System Reminder；
5. `continue` 重新采样。

到达 `max_fires_per_prompt` 后：

- 发出 exhausted event；
- 追加 fallthrough reminder；
- 允许本 Turn 向用户结束。

保险丝的意义是：即使模型反复无视 Todo discipline，也不能无限消耗额度。

---

## 50. Interjection 为什么在候选结束处 drain 两次

第一次 drain 在 bookkeeping 前：

```text
无 Tool Call
→ drain pending interjections
→ 如果有，continue
```

但 drain 与 return 之间还有异步 bookkeeping，用户可能恰好在这段时间发送消息。

因此 finalize 后再 drain 一次：

```text
bookkeeping
→ drain late interjections
→ 如果有，continue
→ 否则 Completed
```

这是关闭 Turn-end race window 的 double-check。

---

## 51. 内层 `Completed` 仍然不是最终结束

`handle_prompt` 外层拿到：

```rust
Ok(TurnOutcome::Completed { ... })
```

后继续执行：

```text
Refusal check
→ Goal round end
→ Stop gate
→ 才可能 break
```

只有不是 `Completed` 的结果，才直接跳出外层 continuation loop。

---

## 52. Refusal 的识别与传播

Turn 将统一 stop reason：

```rust
StopReason::ContentFilter
```

识别为 refusal，并保存 `stop_message` 作为 explanation。

如果响应完全为空，Runtime 还会向 UI 补发明确 notice，避免用户只看到静默结束。

`TurnOutcome::Completed` 中通过：

```rust
refusal: Option<String>
```

携带这一事实。

---

## 53. Refusal 为什么优先于 Goal 和 Stop Hook

外层检测到 `refusal: Some(_)` 后：

- 如果 Goal active，则自动以 Infra 原因暂停 Goal；
- 提示用户用 `/goal resume` 重试；
- 直接 break；
- 不运行 Goal evaluator；
- 不运行 Stop Hook continuation。

否则 Hook 或 Goal 可能把 Provider 明确拒绝重新打开成无限重试。

最终 ACP stop reason 投影为 `Refusal`。

---

## 54. Goal Loop 运行在 Stop Hook 之前

对非 refusal 的 `Completed`：

```text
Goal active?
├── 是 → run_goal_round_end
│       ├── Continue(directive) → 注入 goal summary，直接下一内层 Turn
│       └── EndTurn → 继续 Stop Gate
└── 否 → Stop Gate
```

所以只要 Goal evaluator 决定继续，本轮不会调用 Stop Hook。

Stop Hook 只在 Goal 不再要求继续后，获得阻止最终停止的机会。

---

## 55. Workflow Goal evaluator 的三种业务决定

`run_goal_round_end` 调用 evaluator，得到：

```text
Continue
CandidateComplete
Blocked
```

### 55.1 Continue

记录 evidence，清除 blocker streak，准备下一轮 directive。

### 55.2 CandidateComplete

它不是立即完成；Runtime 先记录进展，再调用 `verify_goal_candidate()`。

### 55.3 Blocked

记录 blocker key 的连续 streak。连续达到 3 次时，自动暂停 Goal，并告诉用户下一步需要做什么。

这说明 Goal 完成权没有完全交给主模型的一句“done”。

---

## 56. Goal evaluator 自身失败时 fail closed

如果 evaluator 在有界重试后仍失败：

- 记录本轮 progress/error；
- 以 Infra 原因暂停 Goal；
- 明确说明不能把验证失败当完成；
- 返回 `GoalRoundDecision::EndTurn`。

这里的 fail closed 指：不在缺少验证证据时宣告 Goal achieved。

但它也不会无限 retry evaluator，而是暂停并交还用户。

---

## 57. Goal Token Budget 在决定前后都参与控制

`run_goal_round_end` 在 evaluator 返回后检查当前 token budget；后续准备 continuation 时还会再读取 token 状态。

只有 Goal 仍为 Active 且能生成 continuation plan，才返回：

```rust
GoalRoundDecision::Continue(directive)
```

Goal 状态已经 achieved、paused、blocked 或预算触发，就不会继续。

---

## 58. Goal continuation 以 Synthetic User 形式注入

`inject_goal_continuation_message` 会：

1. 清理旧的 Goal continuation directives；
2. 以 `ConversationItem::goal_summary(directive)` 追加新指令；
3. 再启动 `process_conversation_turn_with_recovery`。

它不是修改模型刚才的响应，也不是偷偷递归调用某个 Tool；它是对话历史中一个有类型的自动续接 User message。

---

## 59. Legacy Goal Loop 与 Workflow Goal Loop 的区别

Legacy `run_goal_round_end_legacy` 主要依据 `prepare_goal_continuation`：

- 若不能继续，EndTurn；
- 若能继续，提交 strategist note / premature-stop signal；
- 返回 directive。

Workflow engine 路线多了一层 evaluator verdict、candidate verification、blocker streak 和明确的失败暂停语义。

外层 Turn 只关心统一的：

```rust
GoalRoundDecision::{Continue, EndTurn}
```

---

## 60. Stop Hook 是 Turn 最后的外部 Gate

只有以下条件同时成立，Stop Gate 才会被访问：

- 内层返回普通 Completed；
- 不是 refusal；
- Goal 没有要求继续。

主 Agent 使用 `Stop` event，Subagent 使用 `SubagentStop` event。

没有 file hook 和 client hook 时，直接 `AllowStop`。

---

## 61. Stop Hook 能看到什么

Gate payload 包含：

- `stop_hook_active`：本 Turn 是否已经被续接过；
- last assistant message；
- 主 Agent 的 active background tasks；
- active subagents；
- session cron；
- Subagent 自己的 id/type。

所以 Hook 可以基于“后台还有活”“回答缺少某项”“外部质量检查未通过”等事实要求继续。

---

## 62. Stop Hook 的三种实际效果

### 62.1 没有 block/context

`AllowStop`。

### 62.2 普通 block 或 additional context

Runtime 合并 file hooks 与 client hooks 的结果，格式化 feedback：

```text
Stop hook feedback:
- reason A
- reason B

additional context ...
```

返回 `KeepWorking { feedback }`。

外层把它作为 `ConversationItem::stop_hook_feedback` 追加到历史，再开一个内层 Turn。

### 62.3 `prevent_continuation`

名字容易误解：它表示强制停止 continuation，而不是强制继续。

Runtime 发出警告 annotation，然后 `AllowStop`。

---

## 63. Stop Hook failure 为什么 fail open

源码注释明确说明：Hook failures fail open，Agent 正常停止。

Stop Hook 属于扩展控制面。如果脚本崩溃、HTTP Hook 失败就永远不允许 Agent 返回，用户会被第三方扩展锁死。

因此只有成功解析出的 continuation signal 才能继续工作。

---

## 64. Stop Hook continuation 上限是 8

常量：

```rust
MAX_STOP_HOOK_CONTINUATIONS_PER_TURN = 8
```

到达上限时：

- 不再咨询或通知 Gate Hook；
- 发出 limit reached annotation；
- 强制 `AllowStop`。

这与 Stationarity、TodoGate、Structured Output 各自独立的保险丝共同防止无限自主循环。

---

## 65. Session-end Stop 与 Turn-end Stop 不同

`dispatch_session_end_stop` 是 observe-only：

- Session 已经要结束，没有 Turn 可继续；
- 即使 Hook 返回 block，也会被 demote 成 Success；
- 有 5 秒 shutdown budget；
- 用于通知和审计，不拥有 continuation 权力。

不要把它与 `run_stop_gate` 的 Turn-end 决策混为一谈。

---

## 66. 决策优先级总表

下面按实际控制流排列，而不是按概念重要性排列：

| 阶段 | 条件 | 结果 | 是否再采样 |
|---|---|---|---|
| Sampler recovery | auth/compact/retryable failure | resubmit | 是 |
| Response | 有 StructuredOutput 且合法/耗尽 | Completed | 否，进入外层 |
| Response | 有普通 Tool Call | prepare/execute/result | 是 |
| Tool prepare | Permission Reject/Cancel | Cancelled | 否 |
| Tool prepare | Follow-up | 新 User item | 是 |
| Tool prepare | invalid/Hook deny/plan deny | Error Tool Result | 是 |
| Loop start | Stationarity hard stop | StationarityEnded | 否 |
| No Tool | TodoGate fire | System Reminder | 是 |
| No Tool | Interjection arrived | User item | 是 |
| No Tool | 无其他门 | Completed | 进入外层 |
| Outer | Refusal | pause Goal + Refusal | 否 |
| Outer | Goal Continue | Goal directive | 是，完整内层 Turn |
| Outer | Stop Hook KeepWorking | feedback | 是，完整内层 Turn |
| Outer | Stop Hook AllowStop | 最终完成 | 否 |

---

## 67. 哪些终态能被外层重新打开

外层代码首先判断：

```rust
if !matches!(round, Ok(TurnOutcome::Completed { .. })) {
    break round;
}
```

因此：

| 内部结果 | Goal 可重开 | Stop Hook 可重开 |
|---|---:|---:|
| `Completed`，非 refusal | 是 | Goal 不继续后可以 |
| `Completed`，refusal | 否 | 否 |
| `Cancelled` | 否 | 否 |
| `MaxTurnsReached` | 否 | 否 |
| `StationarityEnded` | 否 | 否 |

“用不同 enum variant 表达终态原因”本身就是控制流安全机制。

---

## 68. 最终 ACP StopReason 是一次投影，不是原始 Provider StopReason

最终转换规则：

```text
TurnOutcome::Completed + refusal None
    → ACP EndTurn

TurnOutcome::Completed + refusal Some
    → ACP Refusal

TurnOutcome::StationarityEnded
    → ACP EndTurn + completion kind StationarityEnded

TurnOutcome::Cancelled
    → ACP Cancelled + category/context

TurnOutcome::MaxTurnsReached
    → ACP Cancelled + completion kind MaxTurnsReached
```

所以系统中至少有两种 Stop Reason：

- Provider/Sampling StopReason：解释单次生成为什么停；
- ACP StopReason：解释整个 User Turn 对客户端怎样结束。

---

## 69. `TurnOutcome::Completed` 还携带哪些结果

除 snapshot 外还有：

```rust
tools_called: Vec<String>
structured_output: Option<Result<Value, String>>
refusal: Option<String>
```

这些分别服务于：

- completion requirement / telemetry；
- JSON Schema 调用方；
- refusal 协议投影和 Goal pause。

`Completed` 不是一个无信息的布尔值。

---

## 70. 一次完整示例：读文件、修改、测试、正常结束

```text
Round 1
  Model: read_file(...)
  Runtime: parse → permission → dispatch → ToolResult

Round 2
  Model: apply_patch(...)
  Runtime: PreToolUse → permission → file lock → dispatch → ToolResult

Round 3
  Model: bash(test)
  Runtime: dispatch → ToolResult(pass)

Round 4
  Model: visible final answer, no Tool Call
  Runtime: TodoGate satisfied → no interjection → Completed

Outer
  no Goal → no Stop Hook block → ACP EndTurn
```

模型选择了候选动作；Runtime 决定每个动作是否被允许，并决定最后一句话是否足以结束。

---

## 71. 示例：参数错误怎样自愈

```text
Round 1
  Model: read_file({"wrong": 1})
  Runtime: typed parse fails
  ToolResult: expected file_path ...

Round 2
  Model: read_file({"file_path": "..."})
  Runtime: success

Round 3
  Model: final text
```

参数错误是模型可修正的环境反馈，不需要把整个 User Turn 判为 Error。

---

## 72. 示例：PreToolUse deny 与 Permission reject 的差异

```text
PreToolUse deny
  → ToolResult(reason)
  → 模型可以选择安全替代动作
  → Turn 继续

Permission reject
  → TurnOutcome::Cancelled(PermissionRejected)
  → Goal/Stop Hook 都不能重开
  → 用户重新发 Prompt 才继续
```

前者是策略对动作的约束，后者是用户对当前授权请求的终局决定。

---

## 73. 示例：Todo 已启动后台任务

```text
Todo:
  [in_progress] 等待大型构建结束

Runtime:
  outstanding monitor count = 1
  in_progress count = 1

partition:
  in_progress_backed = 1
  in_progress_unbacked = 0
```

模型无 Tool Call 结束时 TodoGate 放行，不强迫它 tight polling。

如果没有 monitor/background task，则该 InProgress 是 unbacked，Gate 会提醒继续采取实际动作。

---

## 74. 示例：Stop Hook 要求补测试

```text
Inner Turn Completed
  Assistant: 修改已完成

Goal inactive

Stop Hook
  block: Tests were not run

Outer Runtime
  push stop_hook_feedback(User synthetic)
  start another inner Turn

Next Model Round
  sees prior answer + hook feedback
  calls bash(test)
```

Hook 不直接替模型挑 Bash 命令；它只否决停止并提供继续工作的理由。

---

## 75. 示例：Goal candidate completion

```text
Main model: “目标已完成”
  ↓
Inner Turn Completed
  ↓
Goal evaluator: CandidateComplete
  ↓
verify_goal_candidate()
  ├── 验证通过 → Goal 不再 Active → Stop Gate
  └── 有 gap → Goal 仍 Active → continuation directive → 新内层 Turn
```

主模型是执行者，不是 Goal 完成状态的唯一裁判。

---

## 76. 示例：重复轮询怎样被打断

```text
同一 tool + 同一 arguments 连续执行
  1 ... 8
  ↓
下一轮注入 stationarity nudge
  ↓
模型若改变动作 → signature reset
模型若继续相同动作到 16
  ↓
StationarityEnded
  ↓
Goal 与 Stop Hook 都不能重开
```

若动作只是 Bash `true`，第 4 次后就 hard stop，不等第 8 次 nudge。

---

## 77. Runtime 强制与 Prompt 劝导的边界

| 机制 | 主要形式 | 模型能否无视 |
|---|---|---:|
| Tool description | Prompt/Schema | 能，可能选错或不选 |
| “请继续完成 Todo” | Reminder | 能，所以有 fire cap |
| StructuredOutput reminder | Prompt | 能，所以 Runtime 截获和校验 |
| JSON Schema validator | Runtime | 不能伪造校验成功 |
| Permission reject | Runtime terminal | 不能继续当前 Turn |
| Plan edit gate | Runtime | 不能执行被禁编辑 |
| PreToolUse deny | Runtime | 不能执行该调用 |
| File lock | Runtime | 不能并发写穿同一路径 |
| Stationarity hard stop | Runtime terminal | 不能重开 |
| Stop Hook block | Outer Runtime | 模型不能直接结束，但可在后续轮仍不解决 |
| Goal status/budget | Outer Runtime | 决定是否继续，不由一句正文覆盖 |

可靠 Agent 系统不会只靠 Prompt 表达所有约束。

---

## 78. 为什么没有一个单一“终止条件”

不同 Gate 保护不同不变量：

- 无 Tool Call：模型认为当前可以停；
- TodoGate：显式工作清单不能无故遗留；
- Interjection Gate：不能漏掉并发用户输入；
- Goal evaluator：长期目标需要独立完成证据；
- Stop Hook：外部策略可以要求补工作；
- Stationarity：不能把重复动作伪装成进展；
- max turns / retry caps：任何机制都不能无限消耗资源；
- Permission terminal：用户拒绝必须立刻生效。

一个总布尔值会丢掉原因、优先级和可恢复性。

---

## 79. 四类“继续”也不是同一种继续

| 继续来源 | 注入内容 | 循环层级 |
|---|---|---|
| Tool Result | `ToolResult` | 内层 Agentic Loop |
| TodoGate | `SystemReminder` synthetic User | 内层 Agentic Loop |
| Interjection | Interjection/User item | 内层 Agentic Loop |
| Goal | `goal_summary` synthetic User | 外层重新启动内层 Turn |
| Stop Hook | `stop_hook_feedback` synthetic User | 外层重新启动内层 Turn |
| Sampler recovery | 同一 request 或重建 request | 采样恢复层 |

它们对计数器、trace、budget 和可见历史的影响不同。

---

## 80. 四类“拒绝”也不是同一种拒绝

| 拒绝来源 | 拒绝对象 | 结果 |
|---|---|---|
| Provider ContentFilter | 本次生成 | Refusal，Goal 可自动暂停 |
| PreToolUse Hook | 某个 Tool Call | 写反馈，继续模型循环 |
| Plan Mode gate | 编辑动作 | 写反馈，继续模型循环 |
| Permission Reject | 当前授权与 Turn | Cancelled，立即终止 |
| Stop Hook block | 模型停止请求 | 回填反馈，继续工作 |
| Stop Hook prevent continuation | Hook 的继续请求 | 强制允许停止 |

看到日志里的 “deny/block/refusal” 时必须先问：它拒绝的是动作、停止、生成，还是整个 Turn？

---

## 81. 保险丝并不是一个共享预算

本篇出现的主要上限：

| 机制 | 上限 |
|---|---:|
| StructuredOutput invalid retries | 3 |
| identical Tool nudge | 连续 8 次后一次 |
| identical Tool hard stop | 连续 16 次 |
| Bash `true` no-op hard stop | 连续 4 次 |
| Stop Hook continuations | 每 User Turn 8 次 |
| TodoGate | policy 的 `max_fires_per_prompt` |
| Goal blocked evaluator | 同一 blocker streak 达 3 后暂停 |
| Tool rounds | 可配置 `max_turns` |

这些计数器解决不同故障模式，不能简单相加成“Agent 最多循环 N 次”。

---

## 82. 哪些计数器在什么范围内初始化

进入一次 `process_conversation_turn` 时初始化：

- `tool_turn_count`；
- `loop_index`；
- `identical_tool_calls`；
- `todo_gate_fires`；
- `structured_output_retries`；
- auth retry schedule；
- turn span totals。

进入 `handle_prompt` 外层 continuation loop 前初始化：

- `stop_continuations_this_turn`。

所以 Goal 或 Stop Hook 启动新的内层 Turn 时，Stationarity/Todo/Structured Output 计数重置，但 Stop Hook continuation 总数不重置。

---

## 83. Tool Result 顺序与模型可见一致性

并发 Tool 按完成顺序 drain，但每条结果都带 call id。

对模型而言，关键不是“第一个结果对应第一个数组项”，而是：

```text
Assistant ToolCall(call_id=X)
↔ ToolResult(call_id=X)
```

Compaction 和历史修复也必须维护这种配对关系。否则 Provider 可能拒绝请求，或模型无法判断哪个动作得到了哪个结果。

---

## 84. Hosted Tool 与本地 Tool 的决定边界

`hosted_tools` 随 Request 发送，由 Provider 的 agentic sampler 在服务端执行；本地 Function Tool 以 Assistant Tool Call 返回后，由 Shell 的 `execute_tool_calls` 执行。

因此本篇详细分析的：

- local parse；
- PreToolUse；
- local permission；
- file path lock；
- local ToolLoop；

主要针对客户端 Tool Runtime。Hosted Tool 的中间行动会以 backend tool items/stream events 进入 canonical response，不完全走同一条本地 dispatch 路线。

---

## 85. 模型的自由度究竟在哪里

在普通 Turn 中，模型通常可以决定：

- 输出答案还是调用 Tool；
- 选哪个已公告 Tool；
- 一次发一个还是多个 Tool Call；
- 参数内容；
- Tool Result 后采取什么新行动；
- 何时尝试停止。

Runtime 决定：

- Tool 是否真实存在；
- 参数能否解析；
- 当前模式是否允许；
- Hook 是否允许；
- Permission 是否允许；
- Dispatch 是否成功；
- 结果如何进入历史；
- 重复是否过度；
- 停止是否满足 Todo/Goal/Hook；
- 是否超出预算。

这是“policy-guided model proposal + deterministic runtime arbitration”。

---

## 86. 调试一次“为什么 Agent 没调用 Tool”

建议按顺序查：

1. 本轮 `effective_tools` 是否包含目标 Tool；
2. Tool name/description/Schema 是否正确；
3. 是否 forked override 隐藏了 Tool；
4. MCP 是否采用 progressive search，需要先 `search_tool`；
5. Request 的 `tool_choice` 是否未设置、Auto、None 或 Function；
6. Backend 转换后 wire payload 是什么；
7. 模型 Response 是否真的没有 Tool Call，还是解析丢失；
8. 是否返回 Reasoning-only 并被空响应恢复；
9. Prompt/Reminder 是否给出足够清晰的行动纪律；
10. TodoGate 是否 active，能否在模型过早结束后再提醒一次。

不要一上来就假设 Tool Runtime 坏了；模型可能根本没提出结构化调用。

---

## 87. 调试一次“Tool 明明调用了，为什么没执行”

依次查看：

1. canonical response 的 `tool_calls`；
2. StructuredOutput 是否截获；
3. MCP 初始化/发现状态；
4. JSON 和 typed parse；
5. Plan Mode gate；
6. PreToolUse file hook；
7. PreToolUse client hook；
8. Permission decision；
9. batch 中是否已有 terminal `final_result`；
10. 是否被 Interjection 中断的是等待类 Tool；
11. dispatch error 与 Tool Result；
12. ToolCompleted event 的 outcome。

“模型发出了调用”只证明候选动作存在，不证明通过了 Prepare。

---

## 88. 调试一次“Agent 为什么停不下来”

检查：

1. Response 是否仍有 Tool Call；
2. Tool Result 是否不断诱发新调用；
3. TodoGate 是否持续触发；
4. Pending/Unbacked InProgress Todo 是什么；
5. 是否有 Interjection 不断进入；
6. Goal status 是否仍 Active；
7. evaluator verdict 和 next_step；
8. Stop Hook blocks/additional context；
9. stop continuation count；
10. signature 是否真的“完全相同”，足以触发 Stationarity；
11. Tool 参数中时间戳等噪声是否让 signature 每轮变化；
12. 各机制的 cap 是否在当前循环层级重置。

Stationarity 只识别完全相同 signature；每次参数加随机值的逻辑死循环不会被它直接捕获。

---

## 89. 调试一次“Agent 为什么过早停止”

检查：

1. Tool Catalog 是否缺失关键动作；
2. Response 是否无 Tool Call；
3. Todo 是否创建和维护；
4. TodoGate policy 是否 active；
5. InProgress 是否被后台任务计数误判为 backed；
6. Goal harness/status 是否 active；
7. Goal 是否因 token budget、Infra、blocker streak 自动暂停；
8. Stop Hook 是否存在且成功返回 continuation；
9. Stop Hook 是否 fail open；
10. 是否到达 continuation cap；
11. 是否 StationarityEnded / MaxTurnsReached / Cancelled；
12. 是否 Provider refusal。

只有普通 Completed 才会走 Goal 和 Stop Gate。

---

## 90. 适合下断点的最小路径

想用调试器跟一遍，可按以下符号下断点：

```text
SessionActor::handle_prompt
SessionActor::process_conversation_turn_with_recovery
SessionActor::process_conversation_turn
ChatStateHandle::build_request
SessionActor::run_turn_via_sampler
ConversationResponse::tool_calls
SessionActor::handle_structured_output_tool_call
SessionActor::execute_tool_calls
SessionActor::prepare_tool_call
evaluate_todo_gate
SessionActor::run_goal_round_end
SessionActor::run_stop_gate
```

观察这些变量：

```text
loop_index
tool_turn_count
effective_tools
request.tool_choice
response.stop_reason
tool_calls
identical_tool_calls.run_len
todo_gate_fires
stop_continuations_this_turn
goal_tracker.status()
```

---

## 91. 建议阅读测试时抓住哪些不变量

### Structured Output

- 合法 JSON + schema 通过；
- 非 JSON 失败；
- 缺 required field 失败；
- 无效 schema 失败；
- mixed real tools + StructuredOutput 不会提前完成。

### Stationarity

- 第 8 次只 nudge 一次；
- 第 16 次 hard stop；
- signature 改变重置；
- `true` 使用 4 次阈值。

### TodoGate

- 空列表放行；
- Pending 触发；
- unbacked InProgress 触发；
- backed InProgress 放行；
- replay fixture 固定 producer/consumer 语义。

### Stop Gate

- file/client Hook 结果合并；
- failure fail open；
- force stop 跳过 continuation；
- cap 到达后不再调用 Hook；
- session-end block 被 demote。

### Tool Batch

- Permission terminal 取消后续调用；
- call id 配对；
- 同路径写锁；
- ExitPlan tail 顺序；
- Follow-up 进入新 User turn。

---

## 92. 不要从 UI 文案反推内部状态

UI 都可能显示“停止”或“取消”，但内部可能是：

- Provider refusal；
- User permission reject；
- Cmd+C cancellation；
- max turns；
- Stationarity；
- ordinary EndTurn；
- Stop Hook force-stop；
- Goal auto-pause 后 EndTurn。

正确诊断应查看：

- `TurnOutcome`；
- completion kind；
- cancellation category/context；
- typed events；
- Hook execution results；
- Goal history。

---

## 93. 设计优点一：候选动作与授权解耦

模型可以在统一 Tool Catalog 上学习如何行动；安全策略、用户权限和 Hook 则在确定性 Runtime 中执行。

这样即使 Prompt Injection 让模型“认为自己有权限”，真正的 dispatch 仍必须通过 AccessKind、Policy、Hook、Permission 和 Sandbox。

---

## 94. 设计优点二：错误尽量转成下一轮可见信息

Tool parse、Tool missing、dispatch failure、Hook deny 等很多错误不会直接炸掉 Turn，而会成为 Tool Result。

模型因此可以：

- 修参数；
- 换 Tool；
- 搜索 Tool；
- 改为只读方案；
- 向用户说明 blocker。

这让 Agentic Loop 具备局部自愈能力。

---

## 95. 设计优点三：终止原因用类型隔离

`Completed`、`Cancelled`、`MaxTurnsReached`、`StationarityEnded` 不共用一个 bool。

因此外层可以用 exhaustive match 保证：

- 只有普通 Completed 能被 Goal/Stop Hook 继续；
- refusal 有独立协议语义；
- cancellation 保留 category；
- Stationarity 对用户看似 EndTurn，却不会误入 continuation。

---

## 96. 设计代价一：控制权分散

“为什么继续”可能来自：

- Sampler recovery；
- Tool Loop；
- Structured Output；
- TodoGate；
- Interjection；
- Goal；
- Stop Hook。

没有一处代码能单独解释全部行为。

因此维护时最好画出层级状态机，并给每个 continue 标注它跳回哪一层。

---

## 97. 设计代价二：多个独立 cap 的总成本不直观

每个保险丝单独看都合理，但一次 User Turn 的最坏采样成本需要综合：

- 内层 Tool rounds；
- StructuredOutput retries；
- TodoGate retries；
- Goal continuation rounds；
- Stop Hook continuation rounds；
- Sampler retry / auth retry；
- Goal evaluator 自己的额外模型调用。

成本治理不能只盯一个 `max_turns`。

---

## 98. 设计代价三：启发式进展判断存在盲区

当前 Stationarity 检查“连续完全相同 signature”。

它擅长捕获：

- 同一轮询；
- 重复 `true`；
- 完全相同失败调用。

它不直接捕获：

- 参数中加入递增时间戳；
- 在 A/B 两个动作之间循环；
- 不同 Tool 做语义相同的无效动作；
- 每轮输出不同文本但没有实质进展。

Goal evaluator、Todo discipline、Stop Hook 和预算共同弥补，但没有任何一层能完美判断“真实进展”。

---

## 99. 一个适合记忆的五层心智模型

```text
1. Affordance
   模型看见哪些 Tool、Schema 与说明

2. Proposal
   模型输出正文、Reasoning、Tool Call 与 stop reason

3. Admission
   Parse、Mode、Hook、Permission 决定动作能否进入执行

4. Feedback
   Tool Result、错误、Interjection、Reminder 驱动下一次 Proposal

5. Termination Arbitration
   Stationarity、Todo、Goal、Stop Hook、Budget 决定是否真正结束
```

Agent 不是“LLM + tools”这么简单，而是一个带多层仲裁和反馈闭环的状态机。

---

## 100. 本篇最值得记住的十条源码结论

1. 普通 Agent Turn 通常公告 Tool，但不显式强制 `tool_choice=Required`。
2. `Option::None` 表示不设置 Tool Choice，不等于 `ConversationToolChoice::None`。
3. Runtime 执行动作只认结构化 Tool Call，不解析 Reasoning/正文当命令。
4. Reasoning-only 响应被视为 empty，需要恢复，而不是最终答案。
5. Tool Call 必须先经过 typed parse、mode、Hook 和 Permission，再 Dispatch。
6. Hook deny通常回填后继续；Permission reject 直接取消整个 Turn。
7. Structured Output 在不同 Backend 上走 native schema 或 synthetic Tool 两条路线。
8. 模型无 Tool Call只代表候选结束；Todo、Interjection、Goal 和 Stop Hook都能续接。
9. Goal 在 Stop Hook 前运行；refusal 和非 Completed 终态跳过二者。
10. Stationarity 单独使用 `StationarityEnded`，就是为了阻止外层重新打开死循环。

---

## 101. Glossary：本文名词白话解释

| 名词 | 白话解释 | 本文中的具体含义 |
|---|---|---|
| Decision Runtime | 决定 Agent 下一步和是否结束的运行层 | Turn、Tool、Goal、Hook 等控制逻辑总称 |
| Arbitration | 多个意见冲突时作最终裁决 | Runtime 对模型候选行动和停止请求的仲裁 |
| Affordance | 系统向模型展示“可以做什么” | Tool Catalog、Schema 和描述 |
| Proposal | 模型提出的候选下一步 | 正文或结构化 Tool Call |
| Admission | 判断候选动作能否执行 | Parse、Mode、Hook、Permission |
| User Turn | 一次用户输入到最终返回 | 可包含很多模型采样和 Tool round |
| Round | 一次模型请求与响应 | 同一 Turn 中可重复多次 |
| Agentic Loop | 模型—工具—结果—再模型的循环 | `process_conversation_turn` 内层 loop |
| Outer Loop | 候选完成后仍可续接的循环 | `handle_prompt` 中的 Goal/Stop continuation loop |
| Tool Catalog | 给模型看的 Tool 候选目录 | 本轮 `effective_tools` |
| ToolSpec | 一个 Tool 的 wire-level 描述 | name、description、parameters |
| Hosted Tool | Provider 服务端执行的 Tool | 不经过本地 `execute_tool_calls` |
| Local Tool | Shell 本地执行的 Function Tool | 经过 ToolBridge、Hook、Permission |
| Tool Choice | 请求级的 Tool 选择约束 | Auto、None、Required、Function |
| `Option::None` | Rust 中字段未设置 | 不等于禁止 Tool |
| `ConversationToolChoice::None` | 明确要求禁止 Tool | Backend 适配能力可能不完全一致 |
| Auto | 模型自行决定是否用 Tool | 一种 Tool Choice |
| Required | 模型必须调用某个 Tool | 一种 Tool Choice |
| Function Choice | 强制模型调用指定名称的 Tool | 如 session title tool |
| Backend | 实际模型 API 形态 | Responses、Chat Completions、Messages |
| Canonical Response | 不同 Provider 归一化后的响应 | `ConversationResponse` |
| Reasoning | 模型的思考内容或摘要 | 保存为 sibling item，不是执行协议 |
| Visible Text | 给用户看的 Assistant 正文 | 有正文不代表无 Tool Call |
| Tool Call | 模型发出的结构化动作请求 | name、arguments、call id |
| Stop Reason | 单次模型生成为什么停 | Stop、Length、ToolCalls、ContentFilter |
| ACP StopReason | 整个 User Turn 怎样结束 | EndTurn、Refusal、Cancelled 等 |
| Content Filter | Provider 拒绝生成的终止原因 | 投影成 Turn refusal |
| Refusal | Provider 明确拒绝本轮 | Goal 自动暂停且不走 Stop continuation |
| Empty Response | 没有正文也没有 Tool Call | Reasoning-only 也属于 empty |
| Fallback Text | 流式 chunk 丢失时补发的正文 | 从 canonical Assistant 恢复 UI |
| Prepare | Tool 真正执行前的检查阶段 | Parse、Hook、Permission 等 |
| Typed Parse | 将 JSON 参数转成具体 Rust ToolInput | 最终参数有效性的权威检查 |
| Concatenated JSON Recovery | 从粘连的多个 JSON 对象中尝试恢复 | 模型参数格式错误的容错 |
| AccessKind | Tool 对资源的访问分类 | Read、Edit、Bash、MCP、Web 等 |
| Plan Mode Gate | 计划模式下阻止不允许的编辑 | 拒绝动作但让模型继续 |
| PreToolUse Hook | Tool 执行前运行的扩展检查 | 可 deny 当前调用 |
| Permission | 用户/策略对访问动作的授权 | Reject 会取消整个 Turn |
| Permission Reject | 用户明确拒绝执行 | terminal cancellation |
| Permission Cancel | 用户取消授权交互 | terminal cancellation |
| Follow-up Message | 授权期间用户输入的新方向 | 追加 User item 后重新采样 |
| Dispatch | 调用真正的 Tool implementation | Prepare 通过后才发生 |
| Batch | 同一个模型响应中的多个 Tool Call | 可并发执行 |
| FuturesUnordered | 按完成顺序收集异步任务的结构 | Tool batch 的并发执行机制 |
| File Lock | 同一路径上的异步互斥锁 | 涉及写操作时局部串行化 |
| Call ID | Tool Call 与 Tool Result 的关联键 | 并发结果不能靠数组位置配对 |
| Tool Result | Tool 成功、错误或拒绝的模型反馈 | 进入历史，驱动下一轮 |
| ToolLoop | Tool 执行层返回给 Turn 的控制结果 | Continue、Reject、Cancelled 等 |
| Deferred Follow-up | Tool 完成后延迟追加的 User item | batch 结束时统一进入历史 |
| Structured Output | 要求输出符合 JSON Schema | native 或 synthetic Tool 路线 |
| Native Schema | Provider 原生支持的结构化输出约束 | 设置 `request.json_schema` |
| Synthetic Tool | 为模拟协议而临时添加的 Tool | `StructuredOutput` 不走普通 dispatch |
| Validator | 编译后的 JSON Schema 校验器 | 解析并验证最终 JSON |
| Retry Budget | 一类失败允许重新尝试的次数 | StructuredOutput 最多纠错三次 |
| Stationarity | 行动长时间不变、疑似卡死 | 连续相同 Tool step 检测 |
| Step Signature | 一整个 Tool batch 的稳定表示 | Tool name + arguments 的组合 |
| Nudge | 提醒模型改变重复策略 | 相同调用连续 8 次后触发一次 |
| Hard Stop | Runtime 不再允许继续循环 | 普通 16 次，`true` 4 次 |
| No-op | 成功但不产生实际进展的动作 | 本文特指 Bash `true` |
| Latch | 一旦触发便保持的布尔状态 | 同一重复 run 只发一次 nudge |
| Candidate Completion | 模型没有 Tool Call，认为可以结束 | 仍需通过多层 Gate |
| TodoGate | 候选结束时检查 Todo 的门 | Pending/unbacked InProgress 会触发 |
| Pending Todo | 尚未开始的工作项 | 会阻止普通候选结束 |
| InProgress Todo | 正在进行的工作项 | 无后台支撑时会阻止结束 |
| Backing Task | 支撑 InProgress 的后台工作 | active subagent、Bash 或 Monitor |
| Unbacked | 没有可见后台任务支撑 | TodoGate 认为需要继续行动 |
| Heuristic | 不保证完全正确的实用判断 | Todo 与 task 目前按计数/顺序匹配 |
| Fire Cap | Gate 每 Prompt 最多触发次数 | 防止 reminder 无限循环 |
| Interjection | Turn 中途进入的新用户消息 | 在安全点 drain 并继续采样 |
| Race Window | 检查后、提交前状态可能变化的时间窗 | Turn end 用两次 drain 关闭 |
| Bookkeeping | Turn 完成时的状态收尾 | signals、snapshot、feedback、telemetry |
| TurnOutcome | Shell 内部的 Turn 终态类型 | Completed、Cancelled 等 |
| Completed | 正常候选完成的内部结果 | 非 refusal 时仍可被外层续接 |
| Cancelled | 用户或策略导致的终止 | Goal/Stop Hook 不重开 |
| MaxTurnsReached | Tool cycle 达到配置上限 | 协议投影为 Cancelled |
| StationarityEnded | 重复动作保险丝终止 | 协议为 EndTurn，外层不能重开 |
| Goal Harness | 让长期目标自动多轮推进的控制层 | 管理 Goal status、budget、verification |
| Goal Evaluator | 独立判断目标进展的模型/工作流 | Continue、CandidateComplete、Blocked |
| CandidateComplete | 看起来完成、仍需验证 | 不是直接 Achieved |
| Blocker Streak | 同一验证阻塞连续出现次数 | 达 3 次自动暂停 Goal |
| Goal Continuation | 要求下一轮继续目标的合成消息 | `goal_summary` item |
| Stop Hook | Agent 候选停止时运行的扩展 Gate | 可给 feedback 要求继续 |
| Stop Gate | 合并 Stop file/client hooks 的决策点 | AllowStop 或 KeepWorking |
| KeepWorking | Hook 否决停止 | feedback 进入历史后重开内层 Turn |
| AllowStop | Hook 允许真正结束 | 外层 loop break |
| `prevent_continuation` | Hook 强制不再继续 | 名字指“阻止续接”，即强制停止 |
| Fail Open | 扩展失败时允许流程继续/结束 | Stop Hook 失败不锁死 Agent |
| Observe-only | 只通知，不拥有控制权 | Session-end Stop hook |
| Continuation Cap | 自动续接的最大次数 | Stop Hook 每 User Turn 8 次 |
| Insurance Fuse | 防止无限循环或无限重试的上限 | 各层 cap、budget、stationarity |
| Projection | 把内部丰富状态映射到外部协议 | TurnOutcome → ACP StopReason |
| Completion Kind | 比 ACP StopReason 更细的内部完成分类 | Stationarity、MaxTurnsReached 等 |

---

## 102. 下一篇适合继续精读什么

下一篇可以研究：

> **源码精读 26：Agent Goal & Planning Runtime——Plan Mode、Todo、Goal Tracker、Evaluator、Verifier、Strategist、Budget、Pause/Resume 与 Workflow Engine 如何把“下一步行动”组织成可持续的长期任务。**

本篇只解释 Goal 在总体决策优先级中的位置；下一篇可以把 Goal 自身的创建、状态迁移、完成证明、阻塞判定、预算与恢复机制完整展开。
