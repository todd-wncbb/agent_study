# Codex 端到端 JSON 示例：Prompt、工具调用与下一轮请求

本文给出一个尽量贴近当前仓库实现的 Responses API 交互示例。它不是凭空设计的协议：首轮 `input` 由当前源码中的 `debug prompt-input` 路径实际构造，顶层请求字段、消息类型、工具定义和工具结果类型分别对应仓库里的 Rust 数据结构。

## 1. 真实性边界

示例分成两种表示：

1. **无删节 `input` 的生成方式**：直接运行当前源码，可以得到发送给模型的完整消息数组。
2. **适合阅读的端到端 JSON 投影**：保留真实字段、role、消息顺序、工具参数和调用关联关系；仅把三个超长字符串和未展示的其他工具写成明确的省略标记。

因此，下面的可读投影不能直接冒充一次逐字节抓包。真正无删节的 `input` 可由以下命令生成：

```bash
CODEX_HOME=/private/tmp/codex-prompt-example-home \
  cargo run --bin codex -- \
  -C /Users/haining.zhang/github.com/openai/codex \
  --model gpt-5.4 \
  debug prompt-input \
  '请检查 codex-rs/core/src/session/turn.rs，并先运行相关测试。'
```

本次实际运行该命令后，得到的 `input` 是 3 个 `message`：

1. `developer`：两个 `input_text`，依次为 `<skills_instructions>` 和 `<permissions instructions>`。
2. `user`：两个 `input_text`，依次为 `# AGENTS.md instructions ...` 和 `<environment_context>`。
3. `user`：用户本轮任务。

默认 base instructions 来自：

```text
codex-rs/protocol/src/prompts/base_instructions/default.md
```

当前文件长度为 20,903 字节。根目录 `AGENTS.md` 当前长度为 22,519 字节。把二者、skills 目录和完整工具 schema 全部内联后，请求体自然会达到数万字节。

## 2. 第一次请求：Codex -> Responses API

以下是可读投影。`[OMITTED ...]` 是本文为了可读性加入的字符串，不是运行时真的发送给模型的文字。

```json
{
  "model": "gpt-5.4",
  "instructions": "You are a coding agent running in the Codex CLI, a terminal-based coding assistant. Codex CLI is an open source project led by OpenAI. You are expected to be precise, safe, and helpful.\n\nYour capabilities:\n\n- Receive user prompts and other context provided by the harness, such as files in the workspace.\n- Communicate with the user by streaming thinking & responses, and by making & updating plans.\n- Emit function calls to run terminal commands and apply patches. [OMITTED: default.md 的其余内容；实际请求逐字使用完整文件]",
  "input": [
    {
      "type": "message",
      "role": "developer",
      "content": [
        {
          "type": "input_text",
          "text": "<skills_instructions>\n## Skills\nA skill is a set of instructions provided through a `SKILL.md` source.\n### Available skills\n- babysit-pr: Babysit a GitHub pull request after creation ... (file: /Users/haining.zhang/github.com/openai/codex/.codex/skills/babysit-pr/SKILL.md)\n- code-review: Run a final code review on a pull request ...\n[OMITTED: 实际扫描出的其余 skill 条目和 How to use skills]\n</skills_instructions>"
        },
        {
          "type": "input_text",
          "text": "<permissions instructions>\nFilesystem sandboxing defines which files can be read or written. `sandbox_mode` is `read-only`: The sandbox only permits reading files. Network access is restricted.\n# Escalation Requests\n\nCommands are run outside the sandbox if they are approved by the user, or match an existing rule that allows it to run unrestricted.\n[OMITTED: 完整 escalation 与 prefix_rule 规则]\n</permissions instructions>"
        }
      ],
      "internal_chat_message_metadata_passthrough": {
        "turn_id": "auto-compact-0"
      }
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "# AGENTS.md instructions for /Users/haining.zhang/github.com/openai/codex\n\n<INSTRUCTIONS>\n# Rust/codex-rs\n\nIn the codex-rs folder where the rust code lives:\n\n- Crate names are prefixed with `codex-`. For example, the `core` folder's crate is named `codex-core`\n- When using format! and you can inline variables into {}, always do that.\n- Install any commands the repo relies on (for example `just`, `rg`, or `cargo-insta`) if they aren't already available before running instructions here.\n[OMITTED: 根 AGENTS.md 的其余原文]\n</INSTRUCTIONS>"
        },
        {
          "type": "input_text",
          "text": "<environment_context>\n  <cwd>/Users/haining.zhang/github.com/openai/codex</cwd>\n  <shell>zsh</shell>\n  <current_date>2026-07-31</current_date>\n  <timezone>Asia/Shanghai</timezone>\n  <filesystem><workspace_roots><root>/Users/haining.zhang/github.com/openai/codex</root></workspace_roots><permission_profile type=\"managed\"><file_system type=\"restricted\"><entry access=\"read\"><special>:root</special></entry></file_system></permission_profile></filesystem>\n</environment_context>"
        }
      ],
      "internal_chat_message_metadata_passthrough": {
        "turn_id": "auto-compact-0"
      }
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "请检查 codex-rs/core/src/session/turn.rs，并先运行相关测试。"
        }
      ],
      "internal_chat_message_metadata_passthrough": {
        "turn_id": "auto-compact-0"
      }
    }
  ],
  "tools": [
    {
      "type": "function",
      "name": "exec_command",
      "description": "Runs a command in a PTY, returning output or a session ID for ongoing interaction.",
      "strict": false,
      "parameters": {
        "type": "object",
        "properties": {
          "cmd": {
            "type": "string",
            "description": "Shell command to execute."
          },
          "workdir": {
            "type": "string",
            "description": "Working directory for the command. Defaults to the turn cwd."
          },
          "tty": {
            "type": "boolean",
            "description": "True allocates a PTY for the command; false or omitted uses plain pipes."
          },
          "yield_time_ms": {
            "type": "number",
            "description": "Wait before yielding output. Defaults to 10000 ms; effective range is 250-30000 ms."
          },
          "max_output_tokens": {
            "type": "number",
            "description": "Output token budget. Defaults to 10000 tokens; larger requests may be capped by policy."
          },
          "shell": {
            "type": "string",
            "description": "Shell binary to launch. Defaults to the user's default shell."
          },
          "login": {
            "type": "boolean",
            "description": "True runs the shell with -l/-i semantics; false disables them. Defaults to true."
          },
          "sandbox_permissions": {
            "type": "string",
            "enum": [
              "use_default",
              "require_escalated"
            ]
          },
          "justification": {
            "type": "string",
            "description": "User-facing approval question for `require_escalated`; omit otherwise."
          },
          "prefix_rule": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "cmd"
        ],
        "additionalProperties": false
      }
    },
    {
      "type": "custom",
      "name": "apply_patch",
      "description": "The `apply_patch` tool can be used to edit files. This is a FREEFORM tool, so do not wrap the patch in JSON.",
      "format": {
        "type": "grammar",
        "syntax": "lark",
        "definition": "start: begin_patch hunk+ end_patch\n[OMITTED: codex-rs/core/src/tools/handlers/apply_patch.lark 的其余语法]"
      }
    }
  ],
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "reasoning": {
    "effort": "medium",
    "summary": "auto"
  },
  "store": false,
  "stream": true,
  "include": [
    "reasoning.encrypted_content"
  ],
  "prompt_cache_key": "019b1f25-example-session",
  "text": {
    "verbosity": "medium"
  },
  "client_metadata": {
    "x-codex-installation-id": "install_01JEXAMPLE",
    "session_id": "019b1f25-example-session",
    "thread_id": "019b1f25-example-thread",
    "x-codex-window-id": "window_01JEXAMPLE"
  }
}
```

几个关键点：

- `instructions` 只放 thread 的 base instructions，不等于所有 prompt。
- AGENTS 在协议上是 `role: "user"` 的 contextual fragment，不是 system message。
- skills 和 permissions 在本次真实输出里合并为同一个 `developer` message 的两个 content item。
- environment 与 AGENTS 在本次真实输出里合并为同一个 `user` message 的两个 content item。
- `internal_chat_message_metadata_passthrough` 是 Codex 内部保留的元数据；是否发给非 OpenAI provider 由 client 层处理。
- 当前 session 实际可用的工具通常远多于示例中的两个；这里仅展示完成该任务最相关的两个 schema。

## 3. 模型返回工具调用：Responses API -> Codex

Responses API 使用 SSE 流式返回。模型决定先运行测试时，关键事件可以是：

```json
{
  "type": "response.output_item.done",
  "item": {
    "id": "fc_01JEXAMPLE",
    "type": "function_call",
    "name": "exec_command",
    "arguments": "{\"cmd\":\"just test -p codex-core session::turn\",\"workdir\":\"/Users/haining.zhang/github.com/openai/codex/codex-rs\",\"yield_time_ms\":30000}",
    "call_id": "call_01JEXAMPLE"
  }
}
```

注意 `arguments` 是一个“内容为 JSON 的字符串”，而不是嵌套 JSON object。仓库中的 `ResponseItem::FunctionCall.arguments` 刻意保留这个 wire shape，之后由 Codex 再解析。

## 4. Codex 执行工具并形成结果

假设本机没有安装 `just`，工具运行结果会被格式化为与该 `call_id` 关联的 `function_call_output`：

```json
{
  "type": "function_call_output",
  "call_id": "call_01JEXAMPLE",
  "output": "Wall time: 0.0214 seconds\nProcess exited with code 127\nOutput:\nzsh: command not found: just\n"
}
```

这里的 `output` 在普通文本工具结果下是字符串。图片、音频等工具可以使用结构化 content items。

## 5. 第二次请求：携带调用与结果继续采样

Codex 不会只把工具输出单独发给模型，而是从增量维护的 history 重新构造下一次请求。下面只展开本轮末尾新追加的两个 item；前面的 base instructions、context messages、用户任务和 tools 与第一次请求相同。

```json
{
  "model": "gpt-5.4",
  "instructions": "[与第一次请求相同的完整 base instructions]",
  "input": [
    {
      "type": "message",
      "role": "developer",
      "content": [
        {
          "type": "input_text",
          "text": "[与第一次请求相同的 skills + permissions]"
        }
      ]
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "[与第一次请求相同的 AGENTS + environment]"
        }
      ]
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "请检查 codex-rs/core/src/session/turn.rs，并先运行相关测试。"
        }
      ]
    },
    {
      "type": "function_call",
      "name": "exec_command",
      "arguments": "{\"cmd\":\"just test -p codex-core session::turn\",\"workdir\":\"/Users/haining.zhang/github.com/openai/codex/codex-rs\",\"yield_time_ms\":30000}",
      "call_id": "call_01JEXAMPLE"
    },
    {
      "type": "function_call_output",
      "call_id": "call_01JEXAMPLE",
      "output": "Wall time: 0.0214 seconds\nProcess exited with code 127\nOutput:\nzsh: command not found: just\n"
    }
  ],
  "tools": "[与第一次请求相同的完整 tools 数组]",
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "reasoning": {
    "effort": "medium",
    "summary": "auto"
  },
  "store": false,
  "stream": true,
  "include": [
    "reasoning.encrypted_content"
  ],
  "prompt_cache_key": "019b1f25-example-session",
  "text": {
    "verbosity": "medium"
  },
  "client_metadata": {
    "x-codex-installation-id": "install_01JEXAMPLE",
    "session_id": "019b1f25-example-session",
    "thread_id": "019b1f25-example-thread",
    "x-codex-window-id": "window_01JEXAMPLE"
  }
}
```

模型看到失败后，可能改用 `cargo test`、先定位已有 test target，或者向用户说明仓库要求使用 `just` 但当前环境缺失。若再次调用工具，就继续重复：

```text
采样 -> function_call -> 本地执行 -> function_call_output -> 再采样
```

直到模型返回最终 assistant message，turn 才结束。

## 6. JSON 字段对应到哪些代码

| JSON 部分 | 主要实现位置 |
|---|---|
| 调试构造真实 `input` | `codex-rs/core/src/prompt_debug.rs` |
| `Prompt { input, tools, ... }` | `codex-rs/core/src/session/turn.rs` 的 `build_prompt` |
| 顶层 Responses request | `codex-rs/core/src/client.rs` 的 `build_responses_request` |
| `ResponsesApiRequest` 序列化结构 | `codex-rs/codex-api/src/common.rs` |
| 默认 `instructions` 内容 | `codex-rs/protocol/src/prompts/base_instructions/default.md` |
| AGENTS contextual fragment | `codex-rs/core/src/context/user_instructions.rs` |
| skills contextual fragment | `codex-rs/core/src/context/available_skills_instructions.rs` |
| `exec_command` schema | `codex-rs/core/src/tools/handlers/shell_spec.rs` |
| `apply_patch` schema | `codex-rs/core/src/tools/handlers/apply_patch_spec.rs` |
| function call/output wire types | `codex-rs/protocol/src/models.rs` 的 `ResponseItem` |

## 7. 最准确的心智模型

“Prompt”不是单个字符串，而是下面这组共同影响模型行为的输入：

```text
base instructions
+ contextual input messages
+ conversation history
+ tool schemas
+ reasoning/text controls
+ 本轮用户输入
+ 后续工具调用与工具结果
```

其中只有第一项进入 Responses API 的 `instructions` 字段；其余大部分分别进入 `input`、`tools` 或顶层控制字段。
