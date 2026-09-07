# 17. 端到端数据实例：一次搜索、修改和测试

## 1. 场景

用户输入：

> 找到 `calculate_total` 的实现，修复空列表时的 panic，然后运行相关测试。

本篇给出一条教学用的完整数据轨迹。字段名称对应当前源码的主要结构，但为可读性省略长 instructions、metadata 和大量工具 schema。因此它是**真实类型的可读投影**，不是逐字节网络抓包。

## 2. TUI 内部命令

用户提交后，TUI 先形成类似：

```rust
AppCommand::UserTurn {
    items: vec![UserInput::Text {
        text: "找到 calculate_total 的实现，修复空列表时的 panic，然后运行相关测试。".into(),
        text_elements: Vec::new(),
    }],
    cwd: Some("/workspace/project".into()),
    model: None,
    effort: None,
    final_output_json_schema: None,
    collaboration_mode: None,
    // ...
}
```

TUI 检查没有 active turn，于是通过 App Server 发送 `turn/start`。

## 3. Core Submission

App Server 将请求映射为：

```rust
Submission {
    id: "turn-123",
    op: Op::UserInput {
        items: vec![UserInput::Text { /* 上述文本 */ }],
        final_output_json_schema: None,
        responsesapi_client_metadata: None,
        additional_context: None,
        thread_settings: ThreadSettingsOverrides::default(),
    },
}
```

该对象通过 `SessionIo.tx_sub` 进入 `submission_loop()`。

## 4. TurnContext 投影

`new_turn_with_sub_id()` 从 Session 设置解析本轮快照：

```text
TurnContext
├── sub_id: "turn-123"
├── model_info.slug: "当前选中模型"
├── provider: OpenAI/Configured Provider
├── cwd: /workspace/project
├── mode: Default
├── reasoning_effort: 当前有效值
├── approval_policy: 当前有效策略
├── permission profile: 当前有效权限
├── environments: 本轮环境选择
├── dynamic_tools: []
└── final_output_json_schema: None
```

这不是发给模型的 JSON，而是 Runtime 的稳定执行配置。

## 5. TurnInput

没有 active turn，因此输入被包装为：

```rust
TurnInput::UserInput {
    content: vec![UserInput::Text { /* ... */ }],
    client_id: None,
}
```

Session 创建 `RegularTask`，随后进入 `run_turn()`。

## 6. 第一个 StepContext

首次捕获可简化为：

```text
StepContext
├── turn: Arc<TurnContext>
├── environments
│   └── default environment: ready, cwd=/workspace/project
├── selected_capability_roots
├── executor_capability_discovery
├── mcp: 当前 MCP binding
├── loaded_agents_md
└── tool_router
    ├── registry: 所有可执行工具
    └── model_visible_specs: 本次公开工具
```

假设本轮模型直接看到：

```text
exec_command
write_stdin
apply_patch
update_plan
```

Registry 还可能包含 Deferred 或 Code Mode 工具，但它们不一定进入 `model_visible_specs`。

## 7. 初始 History

第一次请求前，History 的可读投影可能是：

```json
[
  {
    "type": "message",
    "role": "developer",
    "content": [
      { "type": "input_text", "text": "<skills_instructions>...目录...</skills_instructions>" },
      { "type": "input_text", "text": "<permissions instructions>...</permissions instructions>" }
    ]
  },
  {
    "type": "message",
    "role": "user",
    "content": [
      { "type": "input_text", "text": "# AGENTS.md instructions ..." },
      { "type": "input_text", "text": "<environment_context>...</environment_context>" }
    ]
  },
  {
    "type": "message",
    "role": "user",
    "content": [
      { "type": "input_text", "text": "找到 calculate_total 的实现，修复空列表时的 panic，然后运行相关测试。" }
    ]
  }
]
```

具体片段取决于 Feature、Skill 和环境配置。

## 8. 第一份 Prompt

Core 中间对象：

```rust
Prompt {
    input: history_items,
    tools: step_context.tool_router.model_visible_specs(),
    parallel_tool_calls: model_info.supports_parallel_tool_calls,
    base_instructions: session_base_instructions,
    output_schema: None,
    output_schema_strict: true,
}
```

## 9. 第一次 Responses 请求

可读投影：

```json
{
  "model": "current-model",
  "instructions": "[完整 Base Instructions]",
  "input": ["[上面的三个结构化 message]"],
  "tools": [
    {
      "type": "function",
      "name": "exec_command",
      "description": "Runs a command...",
      "parameters": {
        "type": "object",
        "properties": {
          "cmd": { "type": "string" },
          "workdir": { "type": "string" },
          "yield_time_ms": { "type": "number" }
        },
        "required": ["cmd"]
      }
    },
    "[其他工具 schema]"
  ],
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "reasoning": { "effort": "..." },
  "stream": true,
  "prompt_cache_key": "[thread/window key]"
}
```

## 10. 模型返回搜索调用

流中先出现 added/delta，完成时形成：

```json
{
  "type": "function_call",
  "id": "item-1",
  "call_id": "call-search-1",
  "name": "exec_command",
  "arguments": "{\"cmd\":\"rg -n \\\"fn calculate_total|calculate_total\\\" .\",\"workdir\":\"/workspace/project\"}"
}
```

Function Call 的 `arguments` 在 wire item 中可能是 JSON 字符串；Router 将它转换成统一 payload。

## 11. 内部 ToolCall

```rust
ToolCall {
    tool_name: ToolName::plain("exec_command"),
    call_id: "call-search-1".into(),
    payload: ToolPayload::Function {
        arguments: "{...}".into(),
    },
    encrypted_function_args: None,
}
```

ToolRouter 使用当前 Step 的 Registry 解析 `exec_command` Runtime。

## 12. Tool Invocation

Handler 收到的不只是命令：

```text
ToolInvocation
├── call ID
├── payload
├── source: Direct
├── Session
├── StepContext
├── cwd/environment
├── approval + sandbox policy
├── cancellation token
└── turn diff tracker
```

参数反序列化后，Runtime 检查 workdir 和权限，再在 sandbox 中启动进程。

## 13. 搜索 Tool Result

假设命令返回：

```text
src/order.rs:42:pub fn calculate_total(items: &[Item]) -> Money {
tests/order_tests.rs:18:fn calculate_total_handles_empty_items() {
```

模型可见 output 投影：

```json
{
  "type": "function_call_output",
  "call_id": "call-search-1",
  "output": "Chunk ID: ...\nProcess exited with code 0\nFinal output:\nsrc/order.rs:42:..."
}
```

Call 与 Output 通过 `call_id` 配对，随后都存在于 History。

## 14. 第二次请求

第二次 `input` 是第一次 input 加上模型 Tool Call 和 Runtime Result：

```json
[
  "[初始上下文与用户消息]",
  {
    "type": "function_call",
    "call_id": "call-search-1",
    "name": "exec_command",
    "arguments": "{...}"
  },
  {
    "type": "function_call_output",
    "call_id": "call-search-1",
    "output": "src/order.rs:42:..."
  }
]
```

模型据此调用第二个命令读取源文件和测试。过程与第一次相同，只是拥有新的 call ID。

## 15. Patch 调用

模型确认 bug 后产生 Custom/Freeform Tool Call：

```text
*** Begin Patch
*** Update File: src/order.rs
@@
-    items.first().unwrap().currency()
+    let Some(first) = items.first() else {
+        return Money::zero();
+    };
+    first.currency()
*** End Patch
```

Apply Patch Runtime：

1.解析 patch grammar；
2.解析目标文件绝对路径；
3.确认目标位于可写 workspace；
4.运行 approval/Hook；
5.应用 hunk；
6.更新 Turn diff tracker；
7.返回成功或精确 hunk 错误。

对应结果仍以 `CustomToolCallOutput` 和 call ID 写回 History。

## 16. 测试调用

下一次模型请求看到 patch 成功，生成：

```json
{
  "name": "exec_command",
  "call_id": "call-test-1",
  "arguments": "{\"cmd\":\"just test -p order-crate calculate_total_handles_empty_items\",\"workdir\":\"/workspace/project\",\"yield_time_ms\":30000}"
}
```

如果测试尚未结束，工具可能返回 session ID；模型再调用 `write_stdin` 或 poll。若立即结束，则 output 包含 exit code 和有界日志。

## 17. 最后一次请求

History 现在包含：

```text
用户目标
搜索 Call/Result
读取 Call/Result
Patch Call/Result
测试 Call/Result
```

模型不再需要工具，返回完整 assistant message：

```json
{
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "output_text",
      "text": "已修复空列表 panic，并运行了相关测试。修改位于 src/order.rs。"
    }
  ],
  "phase": "final_answer"
}
```

`needs_follow_up=false` 且没有 pending input，Turn 结束。

## 18. Rollout 的可读投影

持久化日志不会只保存最终回答，大致包含：

```text
Session metadata
TurnContext(turn-123)
WorldState(full/patch)
User Message
FunctionCall(call-search-1)
FunctionCallOutput(call-search-1)
FunctionCall(call-read-1)
FunctionCallOutput(call-read-1)
CustomToolCall(call-patch-1)
CustomToolCallOutput(call-patch-1)
FunctionCall(call-test-1)
FunctionCallOutput(call-test-1)
Assistant Final Message
Turn completion metadata/events
```

恢复线程时，Runtime 可以用这些结构重建合法 History。

## 19. UI 同时看到什么

模型/工具链执行时，UI 通过 Event stream 看到：

```text
TurnStarted
Reasoning/Commentary deltas
CommandExecution started
Command output/completed
FileChange started/completed
Test command started/completed
AgentMessage deltas
TurnCompleted
```

这些 UI 事件不全部进入模型 History。UI lifecycle、模型 context 和 rollout 是三个相关但不同的投影。

## 20. 若中途失败

### 搜索命令退出 1

作为 Tool Result 交给模型，模型可以换搜索方式。

### Patch hunk 不匹配

返回精确错误，模型通常重新读取文件后生成新 patch。

### 模型 stream 中断

Sampling request 可能重试；已经成功记录的 patch 不应因为请求重试再次执行。

### 测试超时

工具返回 timeout 或保留 session；模型决定继续等待、缩小测试范围或向用户报告。

### Context 超限

主循环 compact History 后继续当前 Turn。

## 21. 从这个案例应学到什么

1. 一个用户 Turn 对应多次模型请求。
2. 每个 Tool Call 和 Result 都是 History 中的结构化 item。
3. Tool Result 是下一次推理的输入，不是旁路日志。
4. Runtime 权限判断发生在实际执行时。
5. UI Event、模型 History 和 Rollout 不是同一个数组。
6. StepContext 保证工具 schema 和执行器一致。
7. 最终回答只是整个事件链的最后一个 item。

