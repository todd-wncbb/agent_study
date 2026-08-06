# xai-grok-agent 代码导读

> `crates/codegen/xai-grok-agent` · Agent 定义、构建器、系统 prompt 组装
> 配套：[xai-grok-tools](./xai-grok-tools.md) · [09 端到端](../09_end_to_end_request_flow.md)

---

## 1. 职责

从 `xai-grok-shell` 抽出的 **可移植 Agent 对象**：工具集、system prompt、
compaction/reminder 策略、模型配置。Shell 的 `SessionActor` 持有一个 `Agent`。

```text
AgentDefinition (.md / preset)
  → AgentBuilder::from_definition / with_*
  → ToolBridge::finalize_builder
  → Agent { tool_bridge, prompt, policies }
```

---

## 2. 顶层模块

| 模块 | 文件 | 作用 |
| --- | --- | --- |
| `agent` | `agent.rs` | `Agent` 结构体：tool_bridge、definition、render_system_prompt |
| `builder` | `builder.rs` | `AgentBuilder` 流式 API，`build().await` |
| `config` | `config/` | `AgentDefinition` 解析、preset、toolset_for_preset |
| `prompt` | `prompt/` | PromptContext、模板、AGENTS.md 注入 |
| `compaction` | `compaction.rs` | `CompactionPolicy` |
| `system_reminder` | `system_reminder.rs` | `ReminderPolicy` |
| `discovery` | `discovery.rs` | 子 agent / subagent 发现 |
| `plugins` | `plugins.rs` | 插件 agent 加载 |
| `repo` | `repo.rs` | agent 定义仓库扫描 |
| `timing` | `timing.rs` | 构建耗时指标 |

---

## 3. AgentBuilder 构建流程

```text
AgentBuilder::new(cwd, prompt_cwd, notification_handle)
  .terminal_backend(arc) / .fs_backend(arc)
  .from_definition(AgentDefinition)   // 或 .with_name / .with_tools
  .build().await
    ├─ 解析 tools 列表（preset + allow/deny）
    ├─ register_*_tools on ToolRegistryBuilder
    ├─ SessionContext { terminal, fs, cwd, skills, ... }
    ├─ ToolBridge::finalize_builder
    ├─ 渲染 system prompt（minijinja + PromptContext）
    └─ Agent { ... }
```

**`prompt_working_directory`**：fork/worktree 时向模型隐藏真实 overlay 路径，
工具执行仍用真实 `working_directory`。

---

## 4. AgentDefinition

`config/` 解析 front matter + markdown body：

- `name`、`description`、`tools`、`skills`、`permission_mode`
- `prompt_mode`：Full / Minimal 等
- builtin preset：`BuiltinAgentName`、 `toolset_for_preset`

文件发现：`discovery` + `repo` 扫描 `.grok/agents`、`AGENTS.md` 链接。

---

## 5. 与 xai-grok-tools 的接口

| Agent 侧 | Tools 侧 |
| --- | --- |
| `AgentBuilder::build` | `ToolRegistryBuilder::register` |
| `agent.tool_bridge()` | `ToolBridge` |
| `SessionContext` 字段 | `resources.rs` 类型 |
| `tool_id_eq` / `short_tool_name` | 工具 wire 名解析 |

---

## 6. System Prompt 组装

`prompt/context.rs` — `PromptContext` 提供 `${{ os_name }}`、`${{ working_directory }}` 等。
`ToolBridge::render_prompt` 解析 `${{ tools.by_kind.read }}` 等工具名占位符。

模板文件：`templates/prompt.md`（crate 内默认 system prompt 骨架）。

---

## 7. 源码文件索引

### `./`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `agent.rs` | 296 | Agent — a fully built agent: definition + session context. |
| `builder.rs` | 2444 | AgentBuilder — fluent construction API for building Agents. |
| `compaction.rs` | 46 | Compaction policy — threshold, model, and memory flush confi |
| `config.rs` | 2592 | Agent definition types — parsed from `.grok/agents/*.md` fil |
| `discovery.rs` | 1501 | Agent definition file discovery. |
| `error.rs` | 36 | Error types for agent construction. |
| `lib.rs` | 29 | Agent builder, definition parsing, and system prompt assembl |
| `repo.rs` | 222 | Shared git-repo dir-chain primitive. |
| `system_reminder.rs` | 127 | Reminder policy — wraps xai-grok-tools reminder config. |
| `timing.rs` | 31 | — |

### `plugins/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `discovery.rs` | 1865 | Filesystem scanning for plugin directories. |
| `git_install.rs` | 1596 | Git-based plugin installation. |
| `hooks_adapter.rs` | 674 | Plugin hooks adapter — pre-filter and source-entry builder. |
| `install_registry.rs` | 631 | Install registry for managing plugins installed from git rep |
| `local_refresh.rs` | 667 | Refresh of copied local plugin installs from their live sour |
| `manifest.rs` | 869 | Plugin manifest parsing and validation. |
| `marketplace.rs` | 497 | Marketplace plugin discovery. |
| `mod.rs` | 32 | Plugin system — discover, load, and manage plugins (includin |
| `registry.rs` | 1245 | In-memory registry of active plugins. |
| `trust.rs` | 365 | Project plugin trust management. |

### `prompt/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `agents_md.rs` | 1114 | AGENTS.md / Claude.md / rules directory discovery and loadin |
| `context.rs` | 1432 | First-class, inspectable system prompt context. |
| `ignore.rs` | 37 | Gitignore integration for AGENTS.md and skills discovery. |
| `mod.rs` | 9 | System prompt assembly — template rendering, AGENTS.md, and  |
| `prompt_encrypted.rs` | 14 | — |
| `skills.rs` | 2688 | Skill and command discovery for system prompt injection. |
| `subagent_prompts.rs` | 26 | System prompts for built-in subagent profiles. |
| `template.rs` | 880 | System prompt template source and constants. |
| `user_message.rs` | 410 | Per-agent first-user-message rendering. |
| `workspace_user.rs` | 162 | Optional multi-user workspace helpers for loading per-user a |

---

## 8. 依赖与被依赖

依赖：`xai-grok-hooks` · `xai-grok-sampling-types` · `xai-grok-tools` · `xai-tty-utils` · `xai-token-estimation` · `xai-grok-config`

被依赖：`xai-grok-agent` · `xai-grok-pager` · `xai-grok-plugin-marketplace` · `xai-grok-shell` · `xai-grok-subagent-resolution` · `xai-grok-workspace`

---

## 9. 阅读顺序

1. `lib.rs` → `agent.rs` → `builder.rs`（`build` 函数体）
2. `config/agent_definition.rs` — 定义文件格式
3. `prompt/context.rs` + `templates/prompt.md`
4. 对照 [xai-grok-tools](./xai-grok-tools.md) 的 finalize 流程
5. shell：`session/agent.rs` 或 `SessionActor` 如何 `borrow` Agent

