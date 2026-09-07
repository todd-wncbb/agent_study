# 20：端到端数据实例

## 这篇解决什么问题

前面的章节分别解释了 Thread、Turn、Prompt、Tool 和 Event，但初学者常会卡在：“同一句用户输入，到了每一层到底长什么样？”

这篇使用同一个任务贯穿全链路：

> 请在 `codex-core` 中找到失败测试 `tool_result_is_recorded`，修复后运行它。

下面的 JSON 和 Rust 都是**省略无关字段的结构示意**。它们保留关键字段关系，但不是可以原样发送的完整 API payload。

## 1. 客户端发出 `turn/start`

桌面 App 或其他客户端先知道目标 thread ID，再发送类似：

```json
{
  "method": "turn/start",
  "id": 41,
  "params": {
    "threadId": "thread-abc",
    "input": [
      {
        "type": "text",
        "text": "请找到失败测试 tool_result_is_recorded，修复后运行它"
      }
    ],
    "cwd": "/workspace/codex",
    "model": null
  }
}
```

这里有三类 ID：

- JSON-RPC request ID `41`：用来匹配这次 API response；
- `thread-abc`：指出任务属于哪条长期对话；
- 稍后产生的 turn/submission ID：标识这一次用户目标。

它们用途不同，不能看到“都是 ID”就混用。

## 2. App-server 转成 Core 操作

`turn_processor.rs` 验证参数、合并本轮设置，然后构造概念上类似的内部操作：

```rust
Op::UserInput {
    items: vec![UserInput::Text { /* 用户文本 */ }],
    final_output_json_schema: None,
    responsesapi_client_metadata: None,
    additional_context: Default::default(),
    thread_settings: ThreadSettingsOverrides {
        cwd: Some("/workspace/codex"),
        ..Default::default()
    },
}
```

App-server v2 类型是外部 wire contract；`Op` 是 Core 内部控制协议。二者相似，但不应直接共用同一个类型，因为它们的兼容周期和职责不同。

## 3. Submission 进入 Session

`SessionIo::submit()` 不会直接调用模型。它先将操作和 submission identity 放入队列：

```text
Submission
├── id / turn identity
└── op = Op::UserInput { ... }
```

`submission_loop()` 按顺序消费，应用 thread settings，并创建本轮 `TurnContext`。这一步保证同一 thread 的状态变更不会因为多个异步请求随意乱序。

## 4. 用户输入写入 History

进入模型上下文前，输入会变成内部 conversation item。概念上可以理解为：

```json
{
  "type": "message",
  "role": "user",
  "content": [
    {
      "type": "input_text",
      "text": "请找到失败测试 tool_result_is_recorded，修复后运行它"
    }
  ]
}
```

与此同时，cwd、权限、`AGENTS.md`、Skill instructions 等可能以不同角色的 context fragments 进入 History 或本次 Prompt。它们不会被粗暴拼进用户字符串。

## 5. StepContext 冻结本次视图

第一次模型请求前捕获 `StepContext`，其中包含：

- 当前 `TurnContext`；
- environment 和 cwd；
- ToolRouter 与模型可见 Tool specs；
- MCP binding；
- 当前观察到的 `AGENTS.md` 和能力状态。

如果模型看到 `exec_command` 工具，这次调用必须由同一个 Step 的 Router 解释，不能在模型生成调用后换用另一份全局工具表。

## 6. 构造第一份 Prompt

`Prompt` 在源码中至少包含：

```rust
Prompt {
    input: Vec<ResponseItem>,
    tools: Vec<ToolSpec>,
    parallel_tool_calls: bool,
    base_instructions: BaseInstructions,
    output_schema: Option<Value>,
    output_schema_strict: bool,
}
```

发送给模型的简化形状可能是：

```json
{
  "model": "selected-model",
  "instructions": "...基础和开发者指令...",
  "input": [
    {"role": "user", "content": "请找到失败测试 ..."}
  ],
  "tools": [
    {
      "name": "exec_command",
      "description": "运行命令",
      "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}
    }
  ]
}
```

History 主要进入 `input`，基础指令进入 `instructions`，工具定义进入 `tools`。三者用途不同。

## 7. 模型返回 Tool Call

模型没有真的运行命令，只返回结构化意图：

```json
{
  "type": "function_call",
  "name": "exec_command",
  "arguments": "{\"cmd\":\"rg -n 'tool_result_is_recorded' codex-rs\"}",
  "call_id": "call-search-1"
}
```

注意 `arguments` 在 Responses 语义中可能是包含 JSON 的字符串。Core 之后才解析、校验并构造内部 `ToolCall`。

## 8. Router 执行工具

关键路径是：

```text
ResponseItem::FunctionCall
  → ToolRouter 根据 name 找 Handler
  → 解析 arguments
  → approval / policy / sandbox
  → Handler 在选定 environment 中执行
  → 产生有界 ToolOutput
```

假设搜索成功，工具结果会关联同一个 `call_id`：

```json
{
  "type": "function_call_output",
  "call_id": "call-search-1",
  "output": "codex-rs/core/tests/suite/tools.rs:418: ..."
}
```

如果 `call_id` 写错，模型会看到一个无法和原请求配对的回执。

## 9. 第二次模型请求

第二次 input 不是只有工具结果，而是保留完整关系：

```text
[用户问题]
[模型的 function_call: call-search-1]
[Runtime 的 function_call_output: call-search-1]
```

模型据此决定读取源码、应用 patch、运行测试。每次普通 Tool Call 都重复同样的 call → output → follow-up 结构。

如果 WebSocket 增量条件满足，传输层可以只发送新增 items；逻辑上的模型上下文仍然等价于完整序列。

## 10. Patch 和测试继续形成闭环

后续可能经历：

```text
模型：调用 apply_patch
Runtime：审批并修改文件
History：记录 patch call/output

模型：调用 exec_command 运行目标测试
Runtime：执行并返回退出码与有界日志
History：记录 test call/output

模型：确认完成并生成 final answer
```

修改文件和运行测试是 Runtime 副作用；assistant message 只是模型对这些已发生事实的解释。

## 11. 客户端同时收到事件

在执行过程中，Core 可能产生：

```text
TurnStarted
ItemStarted / ExecCommandBegin
ExecCommandOutputDelta
ExecCommandEnd / ItemCompleted
PatchApplyBegin / PatchApplyEnd
AgentMessageContentDelta
TurnComplete
```

App-server 将这些 Core events 投影为 v2 notifications。UI 可以实时显示进度，但 UI 展示不是模型 History 的权威来源。

## 12. Rollout 保存什么

Rollout 会持久记录足以恢复和审计的事件/response items，例如用户输入、模型 tool call、tool output、上下文更新和 Turn 边界。

它不是“第一份 Prompt JSON 的完整网络抓包”，也不是“UI 最终显示文字的简单日志”。恢复时仍需从持久内容重建 History，再由 `for_prompt()` 规范化。

## 13. 中途失败时数据链怎样变化

| 失败 | 应记录/返回什么 | 是否自动继续 |
|---|---|---|
| 搜索命令退出 1 | 正常工具结果、退出状态 | 模型可以判断是否换搜索方法 |
| Patch hunk 不匹配 | 结构化工具失败 | 通常交给模型修正 patch |
| Stream 提前关闭 | Stream error | 仅在 retry policy 和预算允许时重试 |
| 用户拒绝审批 | 明确拒绝结果 | 模型可解释或提出范围更小的动作 |
| Context window 达上限 | Token/context 错误或 compaction 路径 | 由上层策略决定压缩或停止 |

## 本章词汇表

| 词语 | 直译 | 在本章中的意思 |
|---|---|---|
| Payload | 载荷 | 请求、事件或工具调用中实际携带的数据 |
| Wire shape | 线上形状 | 跨进程发送时 JSON 字段的结构 |
| Internal operation | 内部操作 | App-server 转交给 Core 的 `Op` |
| Identity | 身份标识 | request、thread、turn 或 tool call 的不同 ID |
| Serialize | 序列化 | 把内存类型转换成 JSON 等可传输表示 |
| Deserialize | 反序列化 | 把 wire 数据解析成程序类型并验证 |
| Closed loop | 闭环 | Tool Call 执行结果重新进入模型输入的完整过程 |

完整解释见[术语总表](glossary.md)。

## 读完后自测

1. JSON-RPC request ID、thread ID 和 call ID 分别关联什么？
2. 为什么工具结果既要显示给 UI，也必须写回 History？
3. App-server v2 参数为什么不直接等同于 `Op::UserInput`？
4. 增量传输只发送新增 items，是否意味着模型逻辑上看不到旧输入？

## 源码检查点

- `codex-rs/app-server-protocol/src/protocol/v2/turn.rs::TurnStartParams`；
- `codex-rs/app-server/src/request_processors/turn_processor.rs`；
- `codex-rs/protocol/src/protocol.rs::Op::UserInput`；
- `codex-rs/core/src/client_common.rs::Prompt`；
- `codex-rs/protocol/src/models.rs::ResponseItem`；
- `codex-rs/core/src/session/turn.rs::run_turn`；
- `codex-rs/core/src/tools/router.rs::ToolRouter`；
- `codex-rs/protocol/src/protocol.rs::EventMsg`。
