# Request 与 Response：Codex 如何调用 LLM

本文基于 `codex-rs` 源码说明：**Agent 向模型发请求时，request / response 的类型定义在哪里、长什么样**。

## 外部参考

| Provider | 官方 API 文档 / Schema |
|----------|------------------------|
| OpenAI | [openai/openai-openapi](https://github.com/openai/openai-openapi)（看 **Responses API**，不是 Chat Completions） |
| Claude | [Messages API](https://platform.claude.com/docs/en/api/messages/create) |

---

## 重要前提：Codex 不用 Chat Completions

Codex **不走** `POST /v1/chat/completions`（没有 `messages` / `choices` 字段）。

主路径是 **Responses API**：

```
POST /v1/responses
```

流式返回 SSE 事件（`response.output_item.done`、`response.completed` 等），而不是一次性返回 `choices[]`。

下面先保留 Chat Completions 形态作对照，再说明 Codex 实际用的结构。

---

## Chat Completions（对照用，非 Codex 主路径）

### Request

```json
{
  "messages": [
    {
      "name": "",
      "role": "developer | system | user | assistant | tool",
      "content": ""
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "description": "description",
        "name": "name",
        "parameters": {},
        "strict": false
      }
    }
  ]
}
```

### Response

```json
{
  "choices": [
    {
      "message": {
        "content": {},
        "tool_calls": [
          {
            "id": "",
            "type": "function",
            "function": {
              "name": "",
              "arguments": ""
            }
          }
        ]
      }
    }
  ]
}
```

---

## Codex：Responses API

### 与 Chat Completions 的字段对照

| Chat Completions | Codex / Responses API |
|------------------|------------------------|
| `messages[]` | `input: ResponseItem[]` |
| system / developer 消息 | `instructions` 字段，或 `input` 里 `type: "message", role: "developer"` |
| `choices[].message.content` | `ResponseItem::Message`（经 SSE `response.output_item.done` 返回） |
| `choices[].message.tool_calls` | `ResponseItem::FunctionCall` |
| tool 执行结果 | `ResponseItem::FunctionCallOutput` |

### Request 长什么样（简化）

```json
{
  "model": "gpt-5.4",
  "instructions": "<system / base instructions>",
  "input": [
    {
      "type": "message",
      "role": "developer",
      "content": [{ "type": "input_text", "text": "<skills_instructions>...</skills_instructions>" }]
    },
    {
      "type": "message",
      "role": "user",
      "content": [{ "type": "input_text", "text": "用户问题" }]
    },
    {
      "type": "function_call_output",
      "call_id": "call_xxx",
      "output": "tool 返回内容"
    }
  ],
  "tools": [/* ToolSpec 序列化后的 JSON */],
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "stream": true,
  "reasoning": { "effort": "medium", "summary": "auto" },
  "include": ["reasoning.encrypted_content"]
}
```

说明：

- **`instructions`**：模型基础指令（`base_instructions`），类似 system prompt。
- **`input`**：对话历史 + 注入的上下文（skills catalog、world state、tool output 等），全部以 `ResponseItem` 表达。
- **`tools`**：当前 turn 可用工具列表。

`responses_lite` 模式下，`instructions` 和 `tools` 也可能被折叠进 `input` 开头的 developer message，但语义不变。

### Response 长什么样（SSE 流）

Codex 不反序列化一个完整的 response body，而是逐条处理 SSE 事件。核心事件类型：

| SSE `type` | 含义 | Codex 内部类型 |
|------------|------|----------------|
| `response.created` | 响应开始 | `ResponseEvent::Created` |
| `response.output_item.added` | 输出 item 开始 | `ResponseEvent::OutputItemAdded(ResponseItem)` |
| `response.output_item.done` | 输出 item 完成 | `ResponseEvent::OutputItemDone(ResponseItem)` |
| `response.output_text.delta` | 文本流式增量 | `ResponseEvent::OutputTextDelta(String)` |
| `response.completed` | 整次响应结束 | `ResponseEvent::Completed { token_usage, end_turn, ... }` |

`output_item.done` 里的 `item` 就是 `ResponseItem`，例如 assistant 消息：

```json
{
  "type": "message",
  "role": "assistant",
  "content": [{ "type": "output_text", "text": "最终回答" }]
}
```

或 tool call：

```json
{
  "type": "function_call",
  "name": "shell",
  "arguments": "{\"command\":\"ls\"}",
  "call_id": "call_abc"
}
```

---

## 源码里的定义在哪里

### 分层数据流

```
core: Prompt (client_common.rs)
  → core: build_responses_request() (client.rs)
    → codex-api: ResponsesApiRequest (common.rs)
      → POST /v1/responses (endpoint/responses.rs)
        → SSE: ResponsesStreamEvent (sse/responses.rs)
          → ResponseEvent (common.rs)
            → ResponseItem (protocol/models.rs)
```

### Request 相关

| 层级 | 文件 | 类型 | 作用 |
|------|------|------|------|
| 业务组装 | `codex-rs/core/src/client_common.rs` | `Prompt` | turn 级 payload：`input` + `tools` + `base_instructions` |
| HTTP body | `codex-rs/core/src/client.rs` | `build_responses_request()` | 把 `Prompt` 转成 `ResponsesApiRequest` |
| 序列化结构 | `codex-rs/codex-api/src/common.rs` | `ResponsesApiRequest` | 最终 POST body |
| 发送 | `codex-rs/codex-api/src/endpoint/responses.rs` | `ResponsesClient::stream_request()` | HTTP POST `/v1/responses` |
| WebSocket 变体 | `codex-rs/codex-api/src/common.rs` | `ResponseCreateWsRequest` | WS 路径的 request shape |

`ResponsesApiRequest` 核心字段：

```rust
// codex-rs/codex-api/src/common.rs
pub struct ResponsesApiRequest {
    pub model: String,
    pub instructions: String,
    pub input: Vec<ResponseItem>,
    pub tools: Option<ResponsesApiTools>,
    pub tool_choice: String,
    pub parallel_tool_calls: bool,
    pub reasoning: Option<Reasoning>,
    pub stream: bool,
    // ...
}
```

`Prompt` 核心字段：

```rust
// codex-rs/core/src/client_common.rs
pub struct Prompt {
    pub input: Vec<ResponseItem>,
    pub(crate) tools: Vec<ToolSpec>,
    pub base_instructions: BaseInstructions,
    // ...
}
```

### Response 相关

| 层级 | 文件 | 类型 | 作用 |
|------|------|------|------|
| SSE 原始事件 | `codex-rs/codex-api/src/sse/responses.rs` | `ResponsesStreamEvent` | 解析 SSE payload |
| 内部事件 | `codex-rs/codex-api/src/common.rs` | `ResponseEvent` | 统一的事件枚举 |
| 输入/输出 item | `codex-rs/protocol/src/models.rs` | `ResponseItem` | message / function_call / reasoning 等 |

`ResponseEvent` 主要变体：

```rust
// codex-rs/codex-api/src/common.rs
pub enum ResponseEvent {
    Created,
    OutputItemDone(ResponseItem),
    OutputItemAdded(ResponseItem),
    OutputTextDelta(String),
    ToolCallInputDelta { item_id, call_id, delta },
    Completed { response_id, token_usage, end_turn },
    // ...
}
```

`ResponseItem` 主要变体（request 的 `input` 和 response 的 output 共用）：

```rust
// codex-rs/protocol/src/models.rs
pub enum ResponseItem {
    Message { role, content, ... },
    Reasoning { summary, content, encrypted_content, ... },
    FunctionCall { name, arguments, call_id, ... },
    FunctionCallOutput { call_id, output, ... },
    ToolSearchCall { ... },
    // ...
}
```

---

## 和 Turn / Step 的关系

结合 [01-turn-vs-step.md](./01-turn-vs-step.md)：

- 每个 **Step** 对应一次 `run_sampling_request()` → 构造一个 `Prompt` → 发一个 `ResponsesApiRequest`。
- 一个 **Turn** 可能包含多个 Step（模型多次 tool call → 多次 sampling）。
- Step 之间的 `input` 会增长：assistant 的 `FunctionCall`、tool 的 `FunctionCallOutput` 都会 `record_items` 进 history，下一次 Step 的 `input` 带上完整历史。

典型两次 Step 的 `input` 变化：

```
Step 1 input:  [developer context, user message]
Step 1 output: FunctionCall(shell, ...)

Step 2 input:  [developer context, user message, FunctionCall, FunctionCallOutput]
Step 2 output: Message(assistant, "最终回答")
```

---

## 补充：上下文注入不在 instructions 里

skills catalog、world state 等注入内容走 **`input`（message history）**，不走 `instructions`：

| 内容 | 在 request 中的位置 | role |
|------|---------------------|------|
| 模型基础指令 | `instructions` | — |
| skills 目录（`<skills_instructions>`） | `input` 里的 developer message | `developer` |
| 显式引用的 skill 正文（`<skill>`） | `input` 里的 user message | `user` |
| 用户问题 | `input` | `user` |
| tool 输出 | `input` | `function_call_output` |

组装入口在 `codex-rs/core/src/session/turn.rs` 的 `run_turn()`：先 `record_context_updates` 写入 history，再 `clone_history().for_prompt()` 作为本次 Step 的 `input`。
