# Walkthrough：Structured Output 如何选择原生 Schema 或合成工具、验证并返回结果

> 场景：客户端要求回答必须满足一份 JSON Schema。Shell 从 Prompt metadata 读取 `outputSchema`，把它随队列项保存，在 Turn 开始时编译 Validator。若目标 API Backend 原生支持 schema，就把 schema 直接放进模型请求；否则动态追加一个合成的 `StructuredOutput` 工具，要求模型在完成其他工作后单独调用。无论走哪条路径，最终结果都在本地再次解析和校验，并通过 PromptResponse `_meta` 确定性返回。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
Client Prompt
  _meta.outputSchema = {...}
  -> MvpAgent::prompt
     -> 要求顶层是 JSON object
     -> SessionCommand::Prompt(json_schema)
  -> queue_input
     -> InputItem.json_schema
  -> handle_prompt / run sampling loop
     -> jsonschema::validator_for(schema)（每 Turn 一次）
     -> api_backend.supports_native_schema()

     Native path:
       -> request.json_schema = schema
       -> model returns assistant JSON text
       -> validate_structured_output(text)

     Fallback path:
       -> inject reminder
       -> append synthetic StructuredOutput ToolSpec(schema)
       -> model finishes real tools
       -> calls StructuredOutput alone
       -> intercept call; never real-dispatch
       -> parse + validate arguments
          invalid, retries < 3 -> correction tool_result + re-sample
          valid               -> accepted + complete
          retries exhausted   -> complete with error

  -> TurnOutcome.structured_output = Some(Ok(value) | Err(message))
  -> PromptTurnOk
  -> build_prompt_response_meta
     -> structuredOutput OR structuredOutputError
  -> ACP PromptResponse resolves
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| Prompt 入口 | `agent/mvp_agent/acp_agent.rs` | `outputSchema` 解析 |
| Queue 传播 | `session/acp_session_impl/prompt_queue.rs` | `InputItem.json_schema` |
| 核心循环 | `session/acp_session_impl/turn.rs` | structured-output 分支 |
| 统一验证 | 同上 | `validate_structured_output` |
| 合成工具拦截 | 同上 | `handle_structured_output_tool_call` |
| Turn 结果 | `session/commands.rs`、`acp_session_impl/types.rs` | `structured_output` |
| RPC meta | `agent/mvp_agent/mod.rs` | `build_prompt_response_meta` |
| Meta 测试 | `agent/mvp_agent/prompt_response_meta_tests.rs` | camelCase 输出 |
| Validator 测试 | `turn.rs` 底部 | parse/schema cases |
| Subagent 消费 | `agent/subagent/handle_request.rs` | output-schema result mapping |
| Workflow 契约 | `session/workflow/schema_contract.rs` | contract output validation |

## 3. Structured Output 解决什么问题

普通文本回答只能由调用者自行解析。Structured Output 把“回答必须是符合指定结构的 JSON”变成可机器验证的契约。

客户端获得的是解析后的 `serde_json::Value`，而不是“看起来像 JSON”的 Markdown 文本。

## 4. outputSchema 从 Prompt metadata 进入

入口读取：

```text
arguments.meta["outputSchema"]
```

它与正文、PromptMode、tool overrides 分离，避免把控制契约混进用户自然语言。

## 5. 第一层只检查顶层 object

若 outputSchema 存在但不是 JSON object，ACP 入口立即返回 invalid params。

这是廉价形状检查；Schema 是否语义有效稍后由 `jsonschema::validator_for()` 决定。

## 6. 为什么入口不编译 Validator

Prompt 可能排队、取消或被 send-now 重排。把编译放在真正执行 Turn 时，可以避免为永不运行的输入做工作，并把验证生命周期绑定到 Turn。

## 7. Schema 随 InputItem 冻结

`queue_input()` 把 `json_schema` 保存进 InputItem。即使客户端随后改变默认设置，这条 Prompt 仍使用提交时的契约。

## 8. Synthetic Prompt 也能携带 Schema

Subagent runtime override、Goal evaluator 或其他内部调用可直接给 SessionCommand::Prompt 提供 schema，不必模拟客户端 metadata。

## 9. Validator 每 Turn 只编译一次

Turn 初始化执行：

```rust
jsonschema::validator_for(schema)
```

结果保存在 `Result<Validator, String>`，采样重试时复用，避免每轮重新编译。

## 10. 为什么保存 Result 而不是提前失败

无效 Schema 被保存为 `Err("invalid output schema: ...")`。统一验证函数能把“Schema 本身坏了”和“模型输出不合规”都映射到 structured-output error 管道。

## 11. schema_ok 是路由条件

只有 `Some(Ok(validator))` 才启用原生或工具 fallback。没有 Schema 或 Schema 编译失败，都不会追加 StructuredOutput 工具。

## 12. Backend capability 决定实现方式

从 ChatState SamplingConfig 的 `api_backend.supports_native_schema()` 获取能力，不按模型名字硬编码。

API Backend 才知道请求协议是否有原生 JSON Schema 字段。

## 13. 没有 SamplingConfig 时保守 fallback

若请求了 Schema 却取不到 sampling config，记录 warning，并按不支持原生处理，使用合成工具路径。

## 14. 两个布尔分支互斥

```text
structured_output_native = schema_ok && native_backend
structured_output_tool   = schema_ok && !native_backend
```

同一个 Turn 不会同时把 Schema传给 Backend 又追加合成工具。

## 15. Native path 把 Schema 放进 request

模型请求构建完成后，若 native=true：

```text
request.json_schema = json_schema.clone()
```

Provider adapter 将其编码成对应 API 的原生结构化输出参数。

## 16. 原生约束也必须本地复验

Provider 声称支持 schema，不代表网络错误、兼容实现或模型行为绝无偏差。Shell 仍解析最终 assistant text 并调用同一个 Validator。

## 17. Native path 的候选值来自 assistant_text

只要请求过 json_schema，代码在响应 item 被移动前保存 `response.assistant_text()`，用于 Turn 结束时验证。

## 18. 为什么提前保存 final_answer_text

随后循环会消费 `response.items` 并写入 ChatState。提前提取避免所有权移动后无法再读取完整回答。

## 19. Fallback path 注入 System Reminder

提醒要求模型：完成其他工具后，恰好调用一次 `StructuredOutput`，把最终答案放在 arguments 中，不要用普通文本返回。

## 20. 合成 ToolSpec 动态追加

每次 sampling loop 构造 effective tools 时追加：

```text
name        = StructuredOutput
description = 最终以符合 schema 的 JSON 返回
parameters  = 用户的 outputSchema
```

Schema 直接成为工具参数 schema，模型工具调用自然携带目标 JSON。

## 21. 为什么不能注册成普通永久工具

它只在某一条 Prompt 请求 schema 时存在，而且 parameters 每次不同。永久注册无法表达 per-Prompt schema，也会让普通 Turn 误调用。

## 22. StructuredOutput 从不真实执行

它由 Turn loop 识别并截获，不进入 ToolBridge、PermissionManager 或工具 dispatch。

调用本身就是返回载体，不需要外部副作用。

## 23. Tool call 必须单独出现

若模型同一轮同时调用 StructuredOutput 与真实工具，Shell 给每个 StructuredOutput call 写 correction result，然后从本轮 calls 中移除它，让真实工具继续执行。

## 24. 为什么混合调用不能接受

真实工具可能改变事实，特别是读取、编辑或执行结果。若先接受同轮最终 JSON，它可能没有包含这些工具的返回值。

“单独且最后”形成明确完成屏障。

## 25. 多个 StructuredOutput 也属于违规

`tool_calls.len() > 1` 即走 correction 分支。模型不能在一轮提交多个候选答案让 Shell 猜选哪个。

## 26. 混合轮的真实工具仍执行

代码只 retain 非 StructuredOutput calls，然后返回 Proceed。这样 correction 不会丢弃模型同轮合法提出的工作。

## 27. 下一轮模型能看到 correction

每个被移除的合成 call 都有 tool_result：要求完成其他工具后单独调用一次。Conversation 协议仍保持 call/result 配对。

## 28. validate_structured_output 有两步

1. `serde_json::from_str(raw.trim())`；
2. `validator.validate(&value)`。

先证明是 JSON，再证明满足 Schema。

## 29. JSON 解析错误与 Schema 错误分开

错误前缀分别是：

- `model output was not valid JSON`；
- `output does not match the required schema`。

客户端和模型能据此采取不同修复。

## 30. Schema 自身无效也走统一错误

Validator 是 Err 时，`validate_structured_output` 直接 clone 并返回该错误，不尝试解析输出。

## 31. arguments 是完整 JSON 值

合成工具的 `arguments` 原始字符串直接作为候选结构，不再要求包一层 `{ "result": ... }`，除非用户 Schema 本身这样规定。

## 32. 失败后最多纠正三次

`STRUCTURED_OUTPUT_MAX_RETRIES = 3`。每次不合规且尚未到上限：

- retry counter +1；
- push 带具体错误的 tool_result；
- 要求修复 arguments 并再次调用；
- sampling loop continue。

## 33. “三次重试”意味着什么

初次失败后可以再发起三轮纠正；当 counter 已到 3，下一次失败不再继续采样，而以最后错误完成。

因此它是 correction retry 上限，不是总调用次数上限的简单同义词。

## 34. 为什么重试计数属于 Turn

`structured_output_retries` 在整个 sampling loop 外初始化。工具调用、普通采样与 correction 之间不会重置，防止无限循环消耗 token。

## 35. 成功时写 accepted tool result

即使即将结束 Turn，仍 push `Structured output accepted.`，保持 Conversation 中 Tool Call 有对应 Tool Result，方便持久化和未来恢复。

## 36. 重试耗尽也写 terminal result

最后错误文本作为 tool result 保存，然后返回 `Complete(Err)`，不让 Conversation 留下悬空调用。

## 37. StructuredOutputStep 三态

| Variant | 含义 |
| --- | --- |
| `Complete(Result)` | 成功或错误终止 Turn |
| `Retry` | 已给 correction，立即重新采样 |
| `Proceed` | 没有 sole synthetic call，继续普通工具流程 |

## 38. Synthetic tool 也计入 tools_called

Complete 时把 `StructuredOutput` 加入 `turn_tools_called`，便于 trace 表明该 Turn 使用了 fallback 交付机制。

## 39. 两条路径共享 Turn 完成 bookkeeping

Native text completion 与 synthetic-tool completion 都调用 `finalize_turn_bookkeeping()`，统一执行 Plan cleanup、Signals、usage、snapshot、BigQuery delta 和 feedback 逻辑。

## 40. 为什么不能为工具路径提前 return

若跳过共享 finalize，Structured Output Turn 会漏记 turn count、token、prompt modes 或持久化状态。

## 41. Native path 在无 Tool Call 时完成

当 response.tool_calls 为空，完成普通 Turn bookkeeping，再用 `final_answer_text` 验证并构造 `structured_output`。

## 42. Invalid Schema 也会产生结果错误

Schema 编译失败时 schema_ok=false，不启用约束。最终 assistant text 仍通过保存的 Err validator，得到 `Some(Err(invalid output schema...))`。

## 43. Provider refusal 是特殊情况

Content filter refusal会显示 provider notice。若响应为空，structured output 可能无法产生；refusal 信息与 structured result 分别在 TurnOutcome 中传递。

## 44. TodoGate 不应干扰有效 Schema 结果

代码只在 `!schema_ok` 时运行无工具的 TodoGate nudge。有效结构化请求优先完成自己的契约，不被 pending Todo 强行续跑。

## 45. Interjection 仍可能延迟完成

在 Turn final bookkeeping 前后，若 drain 到用户插话，循环继续。Structured output 必须对应最终稳定 Turn，而不是抢在新用户信息前结束。

## 46. TurnOutcome 保存 Result 而非两个字段

内部类型是：

```text
Option<Result<Value, String>>
```

三种状态明确区分：未请求/未产生、成功值、验证错误。

## 47. PromptResponse meta 才是客户端交付通道

`build_prompt_response_meta()` 把内部 Result 投影为：

- 成功：`structuredOutput`；
- 失败：`structuredOutputError`；
- None：两个字段都省略。

## 48. 为什么不依赖流式文本

流式 chunks 可能被 UI 渲染、截断或和工具消息交错。Prompt RPC resolve 的 `_meta` 是确定性单次结果，机器调用方可在请求完成时读取。

## 49. Wire 字段使用 camelCase

Rust 字段 `structured_output` / `structured_output_error` 经序列化成为 `structuredOutput` / `structuredOutputError`，测试固定该协议。

## 50. 成功与错误字段互斥

`Some(Ok)` 只填 value；`Some(Err)` 只填 error。客户端无需处理“既有值又有错误”的矛盾状态。

## 51. 普通 Agent 文本仍可能被流式展示

Structured Output 控制机器结果，不一定抑制所有 provider fallback chunks。客户端应把 `_meta.structuredOutput` 当成结构化真相源。

## 52. Subagent 可以声明 output_schema

Subagent runtime overrides 把 schema 传给 child Prompt。Child 完成后父协调器检查 `PromptTurnOk.structured_output`。

## 53. Subagent 成功时返回 JSON 字符串

若 requested schema 且得到 `Some(Ok(value))`，Task 结果 success=true，output 使用 `value.to_string()`，而不是 child 的自由文本 final answer。

## 54. Subagent 验证失败会降为任务失败

`Some(Err)` 映射为 success=false 和 `structured output validation failed`；output 仍可保留 final text 供诊断。

## 55. 请求了 Schema 却无结果也是失败

父协调器单独处理 `(wanted=true, structured_output=None)`，报 `requested but none produced`，避免把缺失误当成功。

## 56. Goal Evaluator 复用同一能力

隐藏 Goal evaluator 构造自己的 JSON Schema，要求模型返回 typed decision。它不需要单独开发一套 JSON 解析重试框架。

## 57. Workflow contract 还有额外限制

Workflow structured output 除 Schema 外可能限制最终消息字节数。通用 Structured Output 负责 JSON 契约，Workflow 层再施加领域约束。

## 58. Schema 与 Tool Schema 形式相同但角色不同

普通 Tool parameters 描述“调用工具需要什么输入”；outputSchema 描述“最终回答必须是什么结构”。Fallback 巧妙地把后者临时映射成前者。

## 59. Native 与 fallback 的共同不变量

1. 用户 Schema 必须是 object。
2. 本地 Validator 是最终判定者。
3. 成功值是解析后的 JSON Value。
4. 错误必须可返回客户端。
5. Turn bookkeeping 不因实现路径而不同。

## 60. 常见误读：Provider 原生支持就无需验证

错误。本地复验防止 Provider 兼容偏差，并让两条路径拥有同一错误格式。

## 61. 常见误读：StructuredOutput 是真实工具

错误。它只是一种协议适配器，由 Turn loop 截获，不进入权限或 dispatch。

## 62. 常见误读：可以和真实工具同时提交最终答案

错误。Shell 会移除合成调用并要求所有真实工具完成后再单独提交。

## 63. 常见误读：输出是 JSON 就算成功

错误。JSON 语法有效只是第一层，还必须满足 required、type、enum、additionalProperties 等 Schema 约束。

## 64. 常见误读：验证失败必然让 ACP 请求报错

错误。Turn 可以正常完成，但 PromptResponse `_meta` 携带 `structuredOutputError`。调用方需要显式检查该字段。

## 65. 常见误读：Schema 编译错误会无限重试模型

错误。无效 Schema 不启用合成工具；最终通过保存的 validator Err 返回契约错误。

## 66. 调试：没有 structuredOutput 字段

检查：

1. metadata key 是否为 `outputSchema`；
2. Schema 顶层是否 object；
3. InputItem 是否携带 schema；
4. Turn 是否取消/refuse；
5. 是否得到 `structuredOutputError`；
6. 内部消费者是否请求 schema 却无结果。

## 67. 调试：模型一直调用 StructuredOutput

检查 retry counter、具体 Validator error、Schema 是否过度严格，以及 correction tool_result 是否正确进入下一轮 Conversation。

## 68. 调试：真实工具结果没进入最终 JSON

检查模型是否把 StructuredOutput 与真实工具同轮提交；Shell 会要求单独重试。还要确认真实 calls 执行后的 tool results 已被下轮采样读取。

## 69. 调试：原生 Backend 返回普通文本

检查 `supports_native_schema()` 是否准确、Provider adapter 是否传输了 request.json_schema，以及 assistant_text 是否包含 Markdown code fence。Fence 不是合法的裸 JSON。

## 70. 调试：Schema 本身被判无效

直接使用相同 `jsonschema` crate 编译 Schema，检查 draft keywords、错误引用与非标准扩展。顶层 object 只通过入口形状检查，不代表是合法 Schema。

## 71. 推荐测试矩阵

| 条件 | 期望 |
| --- | --- |
| outputSchema 非 object | ACP invalid params |
| 合法 Schema + native | request 带 json_schema，本地复验成功 |
| 合法 Schema + fallback | ToolSpec 动态追加 |
| 非 JSON arguments | correction + retry |
| JSON 缺 required | schema error + retry |
| mixed real + synthetic calls | synthetic 移除，真实工具执行 |
| 第三次后仍失败 | Turn 完成，meta error |
| valid fallback call | accepted，meta value |
| invalid Schema | structuredOutputError |
| Subagent requested but none | Task failure |

## 72. 设计建议：Schema 大小限制

当前主链重点验证形状与语义。生产系统还应限制 Schema 字节数、嵌套深度和编译成本，避免用户输入造成高 CPU/内存占用。

## 73. 设计建议：明确 JSON 文本策略

Native response 是否允许前后空白、Markdown fence 或解释文字应固定。当前只 trim 外部空白，不剥离 code fence，契约清晰且严格。

## 74. 设计建议：记录重试 telemetry

可记录 backend path、validation failure kind、retry count 与最终 success，帮助判断某 Provider 的 native schema 可靠性和 Schema 难度。

## 75. 设计建议：避免敏感值进入错误日志

Validator error 应描述路径和规则，不应把完整包含秘密的 JSON 输出写入 telemetry。当前 helper 返回 human-readable error，调用方仍需审计日志字段。

## 76. Glossary：JSON 与 Schema

### JSON

由 object、array、string、number、boolean 和 null 组成的结构化数据格式。

### JSON Schema

描述 JSON 值允许结构、类型、必填字段和约束的声明式规范。

### Validator

由 Schema 编译得到、用于检查某个 JSON Value 是否满足契约的对象。

### Parse

把原始字符串转换成结构化 JSON Value；语法错误在此阶段发现。

### Validation

在解析成功后检查 Value 是否符合 Schema。

### Required

Schema 中声明 object 必须包含哪些属性的关键字。

### Additional Properties

控制 object 是否允许 Schema 未声明字段的规则。

### Contract

生产者与消费者约定的数据结构和失败语义。

## 77. Glossary：模型与工具路径

### Native Schema

模型 API 原生接受最终输出 Schema，并在生成阶段约束结果的能力。

### API Backend

决定模型请求协议与能力编码方式的 Provider adapter 类型。

### Fallback

原生能力不可用时采用的兼容实现；这里是合成工具。

### Synthetic Tool

临时注入模型工具表、但不执行真实业务逻辑的协议工具。

### ToolSpec

发送给模型的工具名称、描述与 parameters schema。

### Intercept

在普通 Tool dispatch 前识别并消费特殊调用。

### Tool Result Pairing

每个 Tool Call 在 Conversation 中都应有对应 Tool Result，保持协议历史完整。

### Completion Barrier

要求最终输出在所有真实工具完成后单独提交的边界。

## 78. Glossary：Turn 与交付

### Sampling Loop

模型响应、工具执行、结果回填和再次采样组成的 Turn 内循环。

### Retry Budget

允许自动纠正失败的有限次数，防止无限循环。

### TurnOutcome

SessionActor 完成 Turn 后返回的内部结果，包含 snapshot、tools 和 structured output。

### PromptResponse

ACP Prompt RPC 最终 resolve 给客户端的响应。

### `_meta`

不属于主要文本内容的协议扩展字段，用于确定性交付结构化结果和统计信息。

### CamelCase

JSON 字段命名形式；Rust `structured_output` 在线上变为 `structuredOutput`。

### Truth Source

冲突时应信任的数据；机器消费者应信任验证后的 meta value，而非 UI 流式文本。

### Refusal

Provider 因 content filter 等原因拒绝生成的终态，和 Schema 验证失败不同。

## 79. 一页复习版

```text
outputSchema
  -> 顶层 object 检查
  -> 随 Prompt 入队
  -> 每 Turn 编译 Validator 一次

native backend:
  request.json_schema -> assistant JSON text -> local validate

non-native backend:
  reminder + dynamic StructuredOutput ToolSpec
  -> 必须在真实工具后单独调用
  -> intercept arguments -> local validate
  -> 最多 3 次 correction retry

统一结果：
  Ok(Value) -> PromptResponse._meta.structuredOutput
  Err(text) -> PromptResponse._meta.structuredOutputError

合成工具不 dispatch，不经过 permission；
两条路径共享 Turn finalize 与本地 Validator。
```

## 80. 源码证据索引

| 结论 | 直接证据 |
| --- | --- |
| metadata 入口与 object 检查 | `MvpAgent::prompt` |
| Schema 随队列传播 | `InputItem.json_schema` |
| Validator 编译一次 | Turn loop 初始化 |
| native 能力判断 | `api_backend.supports_native_schema` |
| dynamic ToolSpec | effective tools append |
| synthetic call 不 dispatch | `handle_structured_output_tool_call` |
| sole-call 屏障 | `tool_calls.len() > 1` 分支 |
| 解析 + Schema 双验证 | `validate_structured_output` |
| 三次纠正预算 | `STRUCTURED_OUTPUT_MAX_RETRIES` |
| 共享完成 bookkeeping | `finalize_turn_bookkeeping` |
| RPC meta 交付 | `build_prompt_response_meta` |
| Subagent 强制消费 | `handle_request.rs` result mapping |

## 81. 阅读完成后应该能回答的问题

1. outputSchema 为什么随 InputItem 保存？
2. Schema 顶层 object 检查与 Validator 编译有何区别？
3. 如何决定 native 与 fallback path？
4. 为什么 native 输出仍需本地复验？
5. StructuredOutput 为什么不是普通工具？
6. 为什么它必须在真实工具后单独调用？
7. Retry counter 为什么位于整个 sampling loop 外？
8. `Option<Result<Value,String>>` 表达哪三种状态？
9. 为什么结果通过 PromptResponse meta 而非流式文本交付？
10. Subagent 请求 Schema 却没有结果时为何必须失败？

能回答这些问题，就掌握了 Structured Output 的核心：Provider 约束只是第一层，Shell 的本地验证、有限纠错与确定性交付才构成完整契约。
