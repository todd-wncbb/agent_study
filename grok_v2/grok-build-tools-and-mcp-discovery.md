# Grok Build 工具系统与 MCP Tool 按需发现机制

本文说明 Grok Build 默认向模型提供的工具，以及 `search_tool` / `use_tool`
如何解决 MCP Server 和 MCP Tool 数量增长带来的上下文膨胀问题。

本文以当前仓库代码为准。核心入口包括：

- 默认 Grok Build toolset：
  [`crates/codegen/xai-grok-agent/src/config.rs`](../crates/codegen/xai-grok-agent/src/config.rs)
- 内置工具注册表：
  [`crates/codegen/xai-grok-tools/src/registry/types.rs`](../crates/codegen/xai-grok-tools/src/registry/types.rs)
- `search_tool`：
  [`crates/codegen/xai-grok-tools/src/implementations/search_tool/`](../crates/codegen/xai-grok-tools/src/implementations/search_tool/)
- BM25 Tool 索引：
  [`crates/codegen/xai-grok-shell/src/session/tool_index.rs`](../crates/codegen/xai-grok-shell/src/session/tool_index.rs)
- MCP Tool 元数据快照：
  [`crates/codegen/xai-grok-shell/src/session/acp_session_impl/mcp_snapshot.rs`](../crates/codegen/xai-grok-shell/src/session/acp_session_impl/mcp_snapshot.rs)
- `use_tool`：
  [`crates/codegen/xai-grok-tools/src/implementations/use_tool/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/use_tool/mod.rs)

---

## 第一部分：Grok Build 默认工具

### 1. “有多少个 Tool”需要区分两个层次

Grok Build 的工具系统存在两个不同概念：

1. **注册表中可用的 Tool**
   - `ToolRegistryBuilder::new()` 注册了 Grok Build、Codex、OpenCode、
     GrokBuildConcise、GrokBuildHashline 等多套实现。
   - 它们是当前二进制“能够使用”的工具全集，不代表每次请求都会全部提供给模型。

2. **默认 `grok-build` preset 向模型暴露的 Tool**
   - `default_grok_build_toolset()` 默认包含 **18 个 Tool**。
   - Agent Builder 还可能按配置动态加入 Web、Memory、LSP、图片、视频、写文件和
     Plan Mode 工具。

本文第一部分重点介绍默认的 18 个 Tool。

### 2. 内部 ID 与模型可见名称

工具在注册表中使用完全限定 ID，例如：

```text
GrokBuild:run_terminal_cmd
```

模型收到的则是 client-facing name。默认 preset 对部分名称进行了重命名：

| 内部 ID | 模型可见名称 |
|---|---|
| `GrokBuild:run_terminal_cmd` | `run_terminal_command` |
| `GrokBuild:task` | `spawn_subagent` |
| `GrokBuild:get_task_output` | `get_command_or_subagent_output` |
| `GrokBuild:wait_tasks` | `wait_commands_or_subagents` |
| `GrokBuild:kill_task` | `kill_command_or_subagent` |

`run_terminal_cmd.is_background` 和 `task.run_in_background` 在模型可见 Schema
中也分别重命名为 `background`。

重命名发生在
[`xai-grok-agent/src/config.rs`](../crates/codegen/xai-grok-agent/src/config.rs)
中的 `bash_tool_config()`、`task_tool_config()`、`task_output_tool_config()`、
`wait_tasks_tool_config()` 和 `kill_task_tool_config()`。

### 3. 默认 18 个 Tool 总览

> 下表中的 Input/Output 是模型使用层面的摘要。精确 JSON Schema 由 Rust
> 类型通过 `schemars` 生成，应以对应源码为最终依据。

| # | 模型可见名称 | 内部 Tool ID | Description 摘要 | Input 摘要 | Output 摘要 |
|---:|---|---|---|---|---|
| 1 | `run_terminal_command` | `GrokBuild:run_terminal_cmd` | 执行终端命令，支持前台和后台模式 | `command`、`timeout`、`description`、`background` | 前台命令结果，或后台任务句柄 |
| 2 | `read_file` | `GrokBuild:read_file` | 读取文本、PDF、PPTX、Notebook 和图片 | `target_file`、`offset`、`limit`、`pages`、`format` | 文件内容、图片/PDF 页面，或结构化错误 |
| 3 | `search_replace` | `GrokBuild:search_replace` | 在文件中精确匹配并替换字符串 | `file_path`、`old_string`、`new_string`、`replace_all` | 编辑详情、Patch，或匹配/路径错误 |
| 4 | `list_dir` | `GrokBuild:list_dir` | 列出目录，遵守 `.gitignore` | `target_directory` | 格式化目录内容或路径错误 |
| 5 | `grep` | `GrokBuild:grep` | 使用 ripgrep 正则搜索文件内容 | `pattern`、`path`、`glob`、上下文参数、`type` 等 | stdout/stderr、退出码、匹配数和结构化匹配 |
| 6 | `kill_command_or_subagent` | `GrokBuild:kill_task` | 终止后台命令、Monitor 或子 Agent | `task_id` | `killed`/`already_exited` 结果或未找到 |
| 7 | `todo_write` | `GrokBuild:todo_write` | 创建和更新结构化任务清单 | `merge`、`todos[]` | 最新 Todo 状态或参数错误 |
| 8 | `get_command_or_subagent_output` | `GrokBuild:get_task_output` | 查询或等待后台命令/子 Agent 输出 | `task_ids[]`、`timeout_ms` | 单任务、多任务结果或未找到 |
| 9 | `wait_commands_or_subagents` | `GrokBuild:wait_tasks` | 兼容性等待接口，等待任一或全部任务 | `task_ids[]`、`mode`、`timeout_ms` | 多任务状态与输出 |
| 10 | `spawn_subagent` | `GrokBuild:task` | 启动或恢复子 Agent | `prompt`、`description`、`subagent_type`、`background` 等 | 后台启动句柄或前台完成结果 |
| 11 | `scheduler_create` | `GrokBuild:scheduler_create` | 创建或原地更新周期任务 | `task_id`、`interval`、`prompt`、`durable`、`foreground`、`fire_immediately` | 任务 ID、人类可读计划和是否更新 |
| 12 | `scheduler_delete` | `GrokBuild:scheduler_delete` | 按 ID 取消周期任务 | `id` | `success`、`message` |
| 13 | `scheduler_list` | `GrokBuild:scheduler_list` | 列出活动周期任务 | 空对象 | 任务摘要数组 |
| 14 | `monitor` | `GrokBuild:monitor` | 启动后台脚本并把每行 stdout 作为事件推送 | `command`、`description`、`timeout_ms`、`persistent` | Monitor task ID 和超时信息 |
| 15 | `search_tool` | `GrokBuild:search_tool` | 搜索 MCP Tool 并返回输入 Schema | `query`、`limit` | 按 Server 分组的候选 Tool、分数和 Schema |
| 16 | `use_tool` | `GrokBuild:use_tool` | 调用一个通过搜索发现的 MCP Tool | `tool_name`、`tool_input` | 目标 MCP Tool 的实际结果 |
| 17 | `update_goal` | `GrokBuild:update_goal` | 汇报活跃 Goal 的进度、完成或阻塞状态 | `completed`、`message`、`blocked_reason` | `success`、`summary` |
| 18 | `workflow` | `GrokBuild:workflow` | 启动编排多个子 Agent 的 Rhai Workflow | Workflow 来源、`args`、`agent_budget` 等 | Workflow run ID、显示名称、脚本路径和消息 |

### 4. 各 Tool 的 Description 与 I/O

#### 4.1 `run_terminal_command`

内部 ID：`GrokBuild:run_terminal_cmd`

作用：

- 在当前工作区执行 Shell 命令。
- 前台模式等待命令完成并返回输出。
- 后台模式立即返回 task ID，命令继续执行。

模型可见输入：

```json
{
  "command": "cargo test",
  "timeout": 120000,
  "description": "Run the test suite to verify the change",
  "background": false
}
```

主要字段：

- `command: string`：要执行的命令。
- `timeout?: integer`：毫秒，最大 300000；Schema 默认 120000。
- `description: string`：为什么执行该命令。
- `background: boolean`：内部字段名为 `is_background`。

输出分为：

- `Foreground(BashOutput)`：
  - `output`
  - `output_for_prompt`
  - `exit_code`
  - `command`
  - `truncated`
  - `signal`
  - `timed_out`
  - `current_dir`
  - 完整输出文件路径等
- `Background(BackgroundTaskStarted)`：
  - `task_id`
  - `task_type`
  - `output_file`
  - `status`
  - `command`
  - `summary`

实现：
[`grok_build/bash/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/bash/mod.rs)

#### 4.2 `read_file`

内部 ID：`GrokBuild:read_file`

Description 要点：

- 文件路径可以是工作区相对路径或绝对路径。
- 默认从文件开头读取有限行数，并给文本结果添加 1-based 行号。
- 支持文本、PDF、PPTX、Jupyter Notebook 和图片。
- 图片通过多模态内容返回。

输入：

```json
{
  "target_file": "src/main.rs",
  "offset": 1,
  "limit": 200,
  "pages": null,
  "format": null
}
```

输出 `ReadFileOutput` 可能是：

- `FileContent`
- `ImageContent`
- `PdfPageImages`
- `FileNotFound`
- `IsADirectory`
- `PermissionDenied`
- `FileTooLarge`
- `FileReadError`
- `ImageSizeError`

实现：
[`grok_build/read_file/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/read_file/mod.rs)

#### 4.3 `search_replace`

内部 ID：`GrokBuild:search_replace`

Description 要点：

- 在单个文件中替换精确字符串。
- 建议先使用 `read_file` 读取文件。
- `old_string` 默认必须只匹配一个位置。
- 多处匹配时需要增加上下文，或者设置 `replace_all: true`。

输入：

```json
{
  "file_path": "src/main.rs",
  "old_string": "let old = true;",
  "new_string": "let enabled = true;",
  "replace_all": false
}
```

输出 `SearchReplaceOutput` 可能是：

- `EditsApplied`：包含绝对路径、编辑位置、上下文和可选 Patch。
- `MultipleMatchesFound`
- `NoMatchesFound`
- `InvalidInput`
- `FileNotFound`
- `FileAlreadyExists`
- `FilenameTooLong`

实现：
[`grok_build/search_replace/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/search_replace/mod.rs)

#### 4.4 `list_dir`

内部 ID：`GrokBuild:list_dir`

Description 要点：

- 列出指定目录中的文件和子目录。
- 不显示 dot-file 和 dot-directory。
- 遵守 `.gitignore`。
- 大目录会使用文件数量和扩展名分布进行汇总。

输入：

```json
{
  "target_directory": "crates/codegen"
}
```

输出 `ListDirOutput`：

- `Content`
- `NotFound`
- `IsAFile`
- `NotADirectory`
- `PermissionDenied`
- `Error`

实现：
[`grok_build/list_dir/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/list_dir/mod.rs)

#### 4.5 `grep`

内部 ID：`GrokBuild:grep`

Description 要点：

- 使用 ripgrep 正则表达式搜索文件内容。
- 默认遵守 `.gitignore`。
- 支持 glob、文件类型、大小写、多行模式和上下文行。
- 大结果会被截断。

输入主要字段：

```json
{
  "pattern": "ToolMetadata",
  "path": "crates/codegen",
  "glob": "*.rs",
  "-B": 2,
  "-A": 2,
  "-C": null,
  "-i": false,
  "type": "rust",
  "head_limit": 200,
  "multiline": false
}
```

输出 `GrepSearchOutput`：

- `stdout`
- `stderr`
- `exit_code`
- `match_count`
- `file_matches[]`

实现：
[`grok_build/grep/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/grep/mod.rs)

#### 4.6 `kill_command_or_subagent`

内部 ID：`GrokBuild:kill_task`

作用：根据 task ID 终止后台命令、Monitor 或子 Agent。

输入：

```json
{
  "task_id": "task-123"
}
```

输出：

```json
{
  "task_id": "task-123",
  "outcome": "killed",
  "message": "..."
}
```

`outcome` 为 `killed` 或 `already_exited`；任务不存在时返回
`TaskNotFound`。

实现：
[`grok_build/kill_task/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/kill_task/mod.rs)

#### 4.7 `todo_write`

内部 ID：`GrokBuild:todo_write`

Description：

> Create and manage a structured task list. The user sees this list live —
> it is your primary way to show progress. Use for any task with 3+ steps.

输入：

```json
{
  "merge": true,
  "todos": [
    {
      "id": "inspect",
      "content": "Inspect the tool registry",
      "status": "in_progress"
    }
  ]
}
```

状态包括：

- `pending`
- `in_progress`
- `completed`
- `cancelled`

输出：

- `TodosUpdated`：包含 prompt 摘要和最新 Todo 数组。
- `DuplicateId`
- `InvalidArgument`

实现：
[`grok_build/todo/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/todo/mod.rs)

#### 4.8 `get_command_or_subagent_output`

内部 ID：`GrokBuild:get_task_output`

作用：

- 获取一个或多个后台命令/子 Agent 的当前状态和输出。
- `timeout_ms > 0` 时等待任务完成。
- 省略 `timeout_ms` 或传入 `0` 时只获取非阻塞快照。
- 最多接受 20 个 task ID。

输入：

```json
{
  "task_ids": ["task-1", "task-2"],
  "timeout_ms": 30000
}
```

输出：

- `Result(TaskOutputResult)`
- `MultiResult(MultiTaskOutputResult)`
- `TaskNotFound`

`TaskOutputResult` 包含：

- `task_id`
- `command`
- `status`
- `exit_code`
- `started`
- `ended`
- `duration_secs`
- `output`
- `output_file`
- `truncated`
- `raw_output_bytes`

实现：
[`grok_build/task_output/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/task_output/mod.rs)

#### 4.9 `wait_commands_or_subagents`

内部 ID：`GrokBuild:wait_tasks`

这是兼容性等待接口。新调用优先使用
`get_command_or_subagent_output` 加正数 `timeout_ms`。

输入：

```json
{
  "task_ids": ["task-1", "task-2"],
  "mode": "wait_all",
  "timeout_ms": 30000
}
```

`mode`：

- `wait_any`
- `wait_all`

输出复用 `TaskOutputOutput`。

实现：
[`grok_build/task_output/wait_tasks.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/task_output/wait_tasks.rs)

#### 4.10 `spawn_subagent`

内部 ID：`GrokBuild:task`

Description 是动态生成的，除了通用说明，还会列出当前实际可用的
subagent type 及其工具权限。默认内建类型包括：

- `general-purpose`
- `explore`
- `plan`

输入主要字段：

```json
{
  "prompt": "Inspect how MCP tools are indexed and report the code path.",
  "description": "Inspect MCP indexing",
  "subagent_type": "explore",
  "background": true,
  "capability_mode": "read-only",
  "isolation": "none",
  "resume_from": null,
  "cwd": null,
  "model": null
}
```

模型可见的 `background` 在内部为 `run_in_background`。

输出：

- 后台运行：立即返回 `subagent_id` 和查询提示。
- 前台完成：返回 `SubagentCompletedOutput`，包含：
  - `output`
  - `subagent_id`
  - `subagent_type`
  - `tool_calls`
  - `turns`
  - `duration_ms`
  - `worktree_path`
  - `resume_from_hint`

实现：
[`grok_build/task/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/task/mod.rs)

共享 I/O 类型：
[`crates/common/xai-tool-types/src/task.rs`](../crates/common/xai-tool-types/src/task.rs)

#### 4.11 `scheduler_create`

内部 ID：`GrokBuild:scheduler_create`

作用：

- 创建周期执行 Prompt 的 Scheduled Task。
- 传入现有 `task_id` 时原地更新。
- 最短周期 60 秒。
- 最多 50 个 Scheduled Task。
- 任务 7 天后自动过期。

输入：

```json
{
  "task_id": null,
  "interval": "30m",
  "prompt": "Check deployment status",
  "durable": false,
  "foreground": false,
  "fire_immediately": true
}
```

输出：

```json
{
  "id": "scheduled-task-id",
  "humanSchedule": "every 30 minutes",
  "updated": false
}
```

实现：
[`grok_build/scheduler/create.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/scheduler/create.rs)

#### 4.12 `scheduler_delete`

内部 ID：`GrokBuild:scheduler_delete`

输入：

```json
{
  "id": "scheduled-task-id"
}
```

输出：

```json
{
  "success": true,
  "message": "..."
}
```

实现：
[`grok_build/scheduler/delete.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/scheduler/delete.rs)

#### 4.13 `scheduler_list`

内部 ID：`GrokBuild:scheduler_list`

输入为空对象：

```json
{}
```

输出：

```json
{
  "tasks": [
    {
      "id": "...",
      "prompt": "...",
      "intervalHuman": "every 30 minutes",
      "nextFireAt": "...",
      "createdAt": "...",
      "recurring": true
    }
  ]
}
```

实现：
[`grok_build/scheduler/list.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/scheduler/list.rs)

#### 4.14 `monitor`

内部 ID：`GrokBuild:monitor`

Description 要点：

- 启动长时间运行的后台脚本。
- 每一行 stdout 都会成为一个事件并进入对话。
- 非 persistent 模式默认最长运行 10 小时。
- persistent 模式运行到显式终止或 Session 结束。

输入：

```json
{
  "command": "tail -f app.log | grep --line-buffered ERROR",
  "description": "Watch application errors",
  "timeout_ms": 36000000,
  "persistent": false
}
```

输出：

```json
{
  "taskId": "monitor-task-id",
  "timeoutMs": 36000000,
  "persistent": false
}
```

实现：
[`grok_build/monitor/`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/monitor/)

#### 4.15 `search_tool`

内部 ID：`GrokBuild:search_tool`

作用：根据关键词搜索当前已连接的 MCP Tool，并返回候选 Tool 的完整
输入 JSON Schema。第二部分会详细说明。

输入：

```json
{
  "query": "slack read thread history",
  "limit": 5
}
```

输出：

- 按 Server 分组的候选 Tool。
- Tool description。
- BM25 score。
- 完整 `input_schema`。
- 当前隐藏的 MCP Tool 总数。
- MCP 初始化状态。

#### 4.16 `use_tool`

内部 ID：`GrokBuild:use_tool`

作用：使用完整限定名和符合 Schema 的参数调用 MCP Tool。

输入：

```json
{
  "tool_name": "slack__read_thread",
  "tool_input": {
    "channel": "C123",
    "thread_ts": "1234567890.123"
  }
}
```

输出为目标 MCP Tool 的实际返回结果。第二部分会详细说明分发过程。

#### 4.17 `update_goal`

内部 ID：`GrokBuild:update_goal`

Description：

> Report progress on the active goal. Use the parameters to log a status
> message, mark the goal completed, or flag that you're blocked.

输入：

```json
{
  "completed": false,
  "message": "Finished the implementation; running verification.",
  "blocked_reason": null
}
```

语义：

- 只有完全完成时才设置 `completed: true`。
- `blocked_reason` 只用于同一问题连续失败多次且真正无法继续的情况。
- 仅提供 `message` 可以记录进度。

输出：

```json
{
  "success": true,
  "summary": "..."
}
```

实现：
[`grok_build/update_goal/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/update_goal/mod.rs)

#### 4.18 `workflow`

内部 ID：`GrokBuild:workflow`

作用：启动一个用 Rhai 编写的多 Agent Workflow。Workflow 作为后台 Run
执行，并自行发送进度和完成通知。

输入主要字段：

- `agent_budget?: integer`：累计子 Agent 调用上限，默认 128，最大 1024。
- `name?: string`：已注册 Workflow 名称。
- `script?: string`：内联 Rhai 脚本。
- `script_path?: string`：Rhai 文件路径。
- `args?: JSON`：绑定到 Workflow 的 `args`。
- `resume_from_run_id?: string`：恢复同一进程中暂停的 Run。
- `validate_only: boolean`：只做路径相关的 Smoke Check。

`name`、`script` 和 `script_path` 必须且只能提供一个；恢复模式例外。

输出：

```json
{
  "run_id": "...",
  "task_id": "...",
  "name": "review-changes",
  "script_path": ".grok/workflows/review-changes.rhai",
  "message": "..."
}
```

这里的 `task_id` 是 `run_id` 的兼容别名，不应传给后台 Task 输出工具。

实现：
[`grok_build/workflow/mod.rs`](../crates/codegen/xai-grok-tools/src/implementations/grok_build/workflow/mod.rs)

### 5. 动态加入的可选 Tool

默认 18 个 Tool 之外，Agent Builder 可能根据功能配置加入：

| 条件 | 动态 Tool |
|---|---|
| Memory Backend 可用 | `memory_search`、`memory_get` |
| Web Search 启用 | `web_search` |
| Web Fetch 启用 | `web_fetch` |
| LSP 启用 | `lsp` |
| Image Generation 启用 | `image_gen` |
| Image Editing 启用 | `image_edit` |
| Video Generation 启用 | `image_to_video`、`reference_to_video` |
| Write File 启用且尚无写入工具 | OpenCode `write` |
| Plan Mode 配置需要 | `enter_plan_mode`、`exit_plan_mode` |
| 用户问答启用 | `ask_user_question` |

因此，“默认 preset 有 18 个 Tool”和“某个实际 Session 最终有多少个 Tool”
可能得到不同答案。

---

## 第二部分：`search_tool` 与 `use_tool`

### 6. 为什么需要 `search_tool` / `use_tool`

如果把所有 MCP Server 的所有 Tool Definition 都直接放进模型请求：

```text
LLM Context
├── Native Tool 1
├── Native Tool 2
├── MCP Server A / Tool 1 / 完整 Schema
├── MCP Server A / Tool 2 / 完整 Schema
├── MCP Server B / Tool 1 / 完整 Schema
└── ...数百或数千个 Tool
```

会产生：

- 上下文和 Token 成本随 Tool 数量增长。
- 模型需要在大量相似 Tool 中选择。
- Tool Schema 变化会导致顶层 Tool 列表频繁变化。
- 更容易选错 Tool 或猜错参数。
- MCP Server 越多，问题越明显。

Grok Build 将其改为两阶段流程：

```text
LLM 顶层 Tool 列表
├── search_tool
└── use_tool

需要外部能力时：

自然语言需求
    ↓
search_tool(query)
    ↓
少量候选 Tool + 精确 input_schema
    ↓
use_tool(tool_name, tool_input)
    ↓
实际 MCP Tool
```

这相当于在 MCP Tool 之上增加：

1. 一个轻量搜索索引。
2. 一个稳定的元调用入口。
3. 一层按需暴露 Schema 的机制。

### 7. `search_tool` 的公开契约

#### 7.1 Description

模型看到的 Description 是：

```text
Search for MCP tools by keyword and retrieve their input schemas.

If status is "partial", some servers may still be connecting.
```

它被标记为只读：

```rust
ToolCapabilities {
    is_read_only: true,
    tool_scope: Some(ToolScope::Read),
    ..
}
```

#### 7.2 Input

```rust
pub struct SearchToolInput {
    pub query: String,
    pub limit: Option<u8>,
}
```

对应 JSON：

```json
{
  "query": "linear create issue",
  "limit": 5
}
```

- `query`：
  - 与 Server 名称、Tool 名称、Description 和参数名进行匹配。
  - 最好包含“Server 名称 + 动作”，例如
    `slack read thread history`。
- `limit`：
  - 默认 5。
  - 限制返回的 Tool 总数，而不是 Server 分组数量。

定义：
[`search_tool/types.rs`](../crates/codegen/xai-grok-tools/src/implementations/search_tool/types.rs)

#### 7.3 Output

Rust 外层类型：

```rust
pub struct SearchToolOutput {
    pub result_count: usize,
    pub content: String,
}
```

其中 `content` 是序列化后的 JSON：

```json
{
  "results": [
    {
      "server": "linear",
      "tools": [
        {
          "tool_name": "linear__save_issue",
          "description": "Create or update a Linear issue",
          "score": 4.27,
          "input_schema": {
            "type": "object",
            "properties": {
              "title": {
                "type": "string"
              },
              "team": {
                "type": "string"
              }
            },
            "required": ["title", "team"]
          }
        }
      ]
    }
  ],
  "total_hidden_tools": 42,
  "status": "ready",
  "note": null
}
```

字段：

| 字段 | 含义 |
|---|---|
| `results` | 按 Server 分组的搜索结果 |
| `server` | MCP Server 名称或 Managed Gateway connector ID |
| `tools[].tool_name` | 交给 `use_tool` 的完整限定名称 |
| `tools[].description` | Tool Description，过长时截断 |
| `tools[].score` | 精确匹配分数或 BM25 分数 |
| `tools[].input_schema` | 完整输入 JSON Schema |
| `total_hidden_tools` | 当前索引中的 MCP Tool 总数 |
| `status` | `ready` 或 `partial` |
| `note` | 初始化状态或错误提示 |

Description 最大保留约 2048 个字符，超出部分以
`… [truncated]` 结尾。

### 8. MCP Tool 元数据如何收集

`search_tool` 自己不直接连接 MCP Server。Session 层负责收集数据并维护
`ToolMetadataSnapshot`。

#### 8.1 单个 Tool 的元数据

```rust
pub struct ToolMetadata {
    pub qualified_name: String,
    pub server_name: String,
    pub tool_name: String,
    pub description: String,
    pub parameters: Vec<String>,
    pub input_schema: serde_json::Value,
}
```

例如：

```text
qualified_name = linear__save_issue
server_name    = linear
tool_name      = save_issue
description    = Create or update a Linear issue
parameters     = [title, team, priority]
input_schema   = 完整 JSON Schema
```

#### 8.2 本地 MCP Tool

收集过程：

1. 调用 `tool_bridge.tool_definitions()` 获取当前注册的 Tool Definition。
2. 只保留名称包含 `__` 的 Tool。
3. 使用 `HashSet` 按完整名称去重。
4. 将 `server__tool` 拆分成 Server 名称和裸 Tool 名称。
5. 从 Tool Definition 读取 Description。
6. 从输入 Schema 顶层 `properties` 提取参数名称。
7. 保存完整输入 Schema。

关键代码：

```rust
let all_defs = tool_bridge.tool_definitions().await;

let mcp_tools = all_defs
    .iter()
    .filter(|d| d.function.name.contains("__"))
    .filter(|d| seen_tools.insert(d.function.name.clone()))
    .map(|d| {
        let (server, tool) = split_qualified_name(&d.function.name);
        ToolMetadata {
            qualified_name: d.function.name.clone(),
            server_name: server.to_string(),
            tool_name: tool.to_string(),
            description: d.function.description.clone().unwrap_or_default(),
            parameters: extract_parameter_names(&d.function.parameters),
            input_schema: d.function.parameters.clone(),
        }
    });
```

#### 8.3 Managed MCP Gateway Tool

当 Managed Gateway Tool Catalog 处于 Ready 状态时，也会将 Gateway Tool
加入同一快照。

其索引字段使用稳定 ID：

```text
qualified_name = connector_id__tool_id
server_name    = connector_id
tool_name      = tool_id
```

而不是 UI 展示标签：

```text
connector_name
tool_name/display name
```

原因是稳定 ID 才适合：

- 模型调用契约。
- 权限判断。
- 去重。
- 配置中禁用某个 Tool。
- Tool 展示名称变化后的兼容。

被禁用的 Connector 或 Tool 不会进入快照。

#### 8.4 Server 元数据

系统还维护：

```rust
pub struct ServerMetadata {
    pub name: String,
    pub description: Option<String>,
}
```

本地 MCP Server 的 Description 来自 MCP initialize 握手的
`instructions` 字段。

随后可以生成：

```rust
pub struct ServerSummary {
    pub name: String,
    pub description: Option<String>,
    pub tool_count: usize,
    pub tool_names: Vec<String>,
}
```

Server Summary 用于向模型提示当前连接了哪些集成。普通提醒只展示 Server
名称、Tool 数量和 Server Description；具体 Tool 名称和 Schema 仍通过
`search_tool` 按需查询。

### 9. Tool Metadata Snapshot

全部数据存放在：

```rust
pub struct ToolMetadataSnapshot {
    pub tools: Vec<ToolMetadata>,
    pub servers: Vec<ServerMetadata>,
    pub mcp_initialized: bool,
}
```

快照使用：

```rust
Arc<std::sync::Mutex<ToolMetadataSnapshot>>
```

进行共享。它会在以下情况刷新：

- MCP Server 初始化。
- MCP Server 重新连接。
- Tool 重新注册。
- Managed Gateway Catalog 更新。
- Connector 或 Tool 启用状态改变。

`mcp_initialized` 用于区分：

- `ready`：当前快照包含全部已知 Tool。
- `partial`：仍有 MCP Server 正在连接，结果可能不完整。

BM25 实现通过 `ToolIndex` Resource 注入 Tool Runtime：

```rust
pub struct ToolIndex(pub Arc<dyn ToolSearchIndex>);
```

`search_tool` 只依赖这个抽象接口，因此 `xai-grok-tools` 不需要依赖具体的
MCP Session 实现。

### 10. BM25 索引如何构建

#### 10.1 不是长期持久化索引

当前实现不会长期保存一个磁盘倒排索引。

每次调用 `search_snapshot()` 时：

1. 在 Mutex 内快速克隆一致的 Metadata Snapshot。
2. 为每个 Tool 构造一段搜索文档。
3. 在内存中构建 BM25 Search Engine。
4. 执行查询并返回 top N。

源码注释指出，对于几十到几百个 Tool，这个重建过程通常低于 1ms。

#### 10.2 每个 Tool 的搜索文档

基础文档：

```text
{server_name} {tool_name} {description} {parameter_names}
```

示例：

```text
linear save_issue Create or update a Linear issue
title team description assignee priority labels project
```

系统还会追加标识符拆分结果：

| 原标识符 | 拆分结果 |
|---|---|
| `linear__save_issue` | `linear save issue` |
| `save_issue` | `save issue` |
| `SearchDashboards` | `Search Dashboards` |
| `notion-search` | `notion search` |
| `query_prometheus_range` | `query prometheus range` |

拆分规则覆盖：

- `__`
- `_`
- `-`
- camelCase / PascalCase 的小写到大写边界

参数名也会参与拆分和索引。

代码：

```rust
fn to_document(&self) -> String {
    let params = self.parameters.join(" ");
    let doc = format!(
        "{} {} {} {}",
        self.server_name,
        self.tool_name,
        self.description,
        params
    );

    let extra = /* 拆分 server/tool/parameter identifiers */;
    format!("{doc} {extra}")
}
```

### 11. 搜索算法

搜索包含两个阶段。

#### 11.1 精确名称 Fast Path

首先对 query 执行：

```text
trim + lowercase
```

然后不区分大小写地匹配：

```text
qualified_name == query
或
tool_name == query
```

例如：

```text
linear__save_issue
save_issue
LINEAR__SAVE_ISSUE
```

都可以直接命中。

精确命中时：

- 跳过 BM25。
- 返回一个 Tool。
- `score = 1.0`。

#### 11.2 BM25 搜索

没有精确命中时：

```rust
let documents = snapshot
    .tools
    .iter()
    .map(ToolMetadata::to_document)
    .collect();

let search_engine =
    SearchEngineBuilder::<u32>::with_corpus(
        Language::English,
        documents,
    )
    .build();

let normalized = normalize_query(query);
let results = search_engine.search(&normalized, limit);
```

如果查询本身包含复合标识符，也会进行同样的拆分：

```text
原始：
grafana-ai__SearchDashboards

规范化：
grafana-ai__SearchDashboards grafana ai Search Dashboards
```

普通自然语言查询保持不变，例如：

```text
create a linear issue
search public slack messages
read thread replies slack
```

当前实现的核心是 BM25 关键词相关性，不包含：

- Embedding。
- 向量数据库。
- 语义向量召回。
- LLM reranker。

因此，Tool 名称、Server 名称、Description 和参数名称的质量会直接影响搜索
效果。

### 12. `search_tool` 如何组织返回值

BM25 原始结果是按分数降序排列的 Tool 列表。

`search_tool` 随后：

1. 按 `server_name` 分组。
2. 保持每个组内 Tool 的 BM25 分数顺序。
3. 使用每组最高分对 Server 分组排序。
4. 为每个 Tool 附上完整 `input_schema`。

因此模型可以同时看到：

- 哪个 Server 提供该 Tool。
- Tool 的业务语义。
- 为什么它被检索到。
- 调用时必须传什么参数。

### 13. 无 MCP 或 MCP 尚未初始化时

如果 Runtime 中不存在 `ToolIndex`：

```json
{
  "results": [],
  "total_hidden_tools": 0,
  "note": "No integration tools are configured. MCP servers are not connected."
}
```

如果索引存在，但 MCP 初始化尚未完成：

```json
{
  "results": [],
  "total_hidden_tools": 0,
  "status": "partial",
  "note": "Some MCP servers are still connecting. Results may be incomplete."
}
```

已有部分 Tool 时，`partial` 状态仍会返回当前可用结果。

### 14. `use_tool` 的公开契约

#### 14.1 Description

模型看到的 Description 是：

```text
Call an MCP integration tool.

The `tool_name` must be the qualified `server__tool` name
(e.g., `linear__save_issue`).
The `tool_input` must conform exactly to the input schema
returned by `search_tool`.
```

#### 14.2 Input

```rust
pub struct UseToolInput {
    pub tool_name: String,
    pub tool_input: serde_json::Value,
}
```

`tool_input` 的 Tool Schema 被定义为允许任意 JSON Object 字段：

```json
{
  "type": "object",
  "additionalProperties": true
}
```

原因是它无法在顶层静态 Schema 中提前知道目标 MCP Tool 的参数。准确参数
约束来自前一步 `search_tool` 返回的 `input_schema`。

调用示例：

```json
{
  "tool_name": "linear__save_issue",
  "tool_input": {
    "title": "Fix login redirect",
    "team": "Platform",
    "priority": 2
  }
}
```

#### 14.3 Output

`use_tool` 返回目标 MCP Tool 的实际输出，再经过统一 MCP 输出截断处理。

具体结构取决于目标 Tool，例如：

- 查询结果列表。
- 创建后的 Issue 对象。
- Slack 消息。
- 外部系统错误。
- 认证或权限错误。

### 15. `use_tool` 的内部调用路径

```text
use_tool.run()
    │
    ├── 读取 ManagedGatewayToolCatalog
    ├── 检查 tool_name 是否误用了 Native Tool
    ├── 检查名称是否符合 server__tool 形式
    │
    └── dispatch_mcp_tool()
           │
           ├── Managed Gateway Tool
           │      └── gateway client.call_tool(...)
           │
           └── Local MCP Tool
                  └── InnerDispatch
                       └── FinalizedToolset::call_raw(...)
```

#### 15.1 Native Tool 纠错

如果模型错误地把原生 Tool 传给 `use_tool`，例如：

```json
{
  "tool_name": "read_file",
  "tool_input": {}
}
```

`use_tool` 会检查 `EnabledNativeToolNames`，并提示模型：

```text
read_file is a native tool, not an MCP integration tool.
Call read_file directly instead of routing it through use_tool.
```

#### 15.2 非限定名称纠错

如果名称既不是 Gateway Catalog 中的条目，又不包含 `__`：

```text
save_issue
```

会被拒绝，并提示使用：

```text
server__tool
```

格式，同时让模型重新调用 `search_tool`。

#### 15.3 本地 MCP 分发

本地 MCP 调用通过 `InnerDispatch` 进入：

```text
FinalizedToolset::call_raw()
```

而不是重新从外层 `ToolBridge` 加锁。

这样可以：

- 避免嵌套调用导致 ToolBridge Mutex 死锁。
- 避免 Reminder 和 Persistence 重复执行。
- 让 `use_tool` 外层只做一次统一后处理。

#### 15.4 Managed Gateway 分发

如果 `tool_name` 存在于 `ManagedGatewayToolCatalog`：

1. 找到对应的 `connector_id`、`tool_id` 和 `call_id`。
2. 如果同名的本地 `server__tool` 也存在，先尝试本地分发；名称冲突时本地
   MCP Tool 优先。
3. 本地 Tool 不存在时，通过 Managed Gateway Client 调用。
4. 将 Gateway 返回值转换为统一 MCP Tool Output。
5. Gateway 响应中的待重新认证 Connector 数量会写入调试日志；当前
   `gateway_response_to_output()` 只把实际 Tool result 转成模型可见输出。

### 16. 为什么顶层只保留 `search_tool` 和 `use_tool`

`use_tool` 源码明确指出，它作为稳定的 Meta Tool 出现在模型 Tool 列表中，
可以避免新 MCP Tool 被发现后，顶层 Tool Set 每轮发生变化。

主要收益：

1. **减少上下文占用**
   - 不需要每轮提供所有 MCP Tool Description 和 Schema。

2. **降低 Tool 选择难度**
   - 模型先看到少量高相关候选，再选择具体 Tool。

3. **支持动态变化**
   - MCP Server 和 Tool 可以连接、断开、更新或禁用。
   - 只需刷新 Metadata Snapshot。

4. **保持顶层 Tool Set 稳定**
   - 顶层通常只需要稳定保留 `search_tool` 和 `use_tool`。
   - 对 KV Cache 和请求一致性更友好。

5. **按需提供 Schema**
   - 只有搜索命中的 Tool 才会把完整输入 Schema 返回给模型。

6. **隔离 Native Tool 与 MCP Tool**
   - Native Tool 直接调用。
   - MCP Tool 使用 `server__tool` 名称，经 `use_tool` 路由。

### 17. 这种设计的边界

它显著缓解 Tool 数量过多的问题，但不是无损的：

- BM25 依赖关键词，Description 写得差会降低召回率。
- 用户语言与 Tool Description 差异很大时可能搜不到。
- 多个 Tool 功能高度相似时，模型仍可能选择错误。
- 搜索加调用通常比直接调用多一个 Tool Round Trip。
- 当前没有向量语义召回或 LLM 重排。
- `search_tool` 返回完整 Schema，因此单次命中太多 Tool 时仍会增加上下文。

可进一步演进的方向包括：

- BM25 + Embedding 混合召回。
- 同义词或 Query Expansion。
- 基于 Server、权限和历史使用的加权。
- 对候选 Tool 进行 rerank。
- 根据调用成功率和用户反馈调整排序。

### 18. 完整示例

用户请求：

```text
在 Linear 的 Platform 团队里创建一个修复登录跳转的 Issue。
```

第一步，模型搜索 Tool：

```json
{
  "query": "linear create issue",
  "limit": 5
}
```

`search_tool` 返回：

```json
{
  "results": [
    {
      "server": "linear",
      "tools": [
        {
          "tool_name": "linear__save_issue",
          "description": "Create or update a Linear issue",
          "score": 4.27,
          "input_schema": {
            "type": "object",
            "properties": {
              "title": {
                "type": "string"
              },
              "team": {
                "type": "string"
              },
              "description": {
                "type": "string"
              }
            },
            "required": ["title", "team"]
          }
        }
      ]
    }
  ],
  "total_hidden_tools": 42,
  "status": "ready",
  "note": null
}
```

第二步，模型严格按照 Schema 调用：

```json
{
  "tool_name": "linear__save_issue",
  "tool_input": {
    "title": "Fix login redirect",
    "team": "Platform",
    "description": "Correct the redirect behavior after successful login."
  }
}
```

第三步，`use_tool`：

1. 确认它不是 Native Tool。
2. 识别 `linear__save_issue` 的 MCP Source。
3. 通过 Local MCP 或 Managed Gateway 分发。
4. 截断超大返回值。
5. 将目标 Tool 的实际结果返回给模型。

最终，模型不需要在初始上下文中看到 Linear Server 的全部 Tool Schema。

---

## 结论

Grok Build 默认 `grok-build` preset 包含 18 个基础 Tool，覆盖：

- Shell 执行。
- 文件读取、搜索和编辑。
- 后台任务与子 Agent。
- Todo、Goal 和 Workflow。
- Scheduler 与 Monitor。
- MCP Tool 发现和调用。

其中 `search_tool` / `use_tool` 是 MCP Tool 规模化接入的关键：

```text
收集 MCP Tool Metadata
    ↓
维护共享 Snapshot
    ↓
精确名称匹配或 BM25 搜索
    ↓
按需返回候选 Tool 的完整 Schema
    ↓
use_tool 路由到 Local MCP 或 Managed Gateway
```

它把“让模型同时理解所有 MCP Tool”的问题转换成了：

```text
先搜索少量候选，再基于精确 Schema 调用
```

从而在 MCP Server 和 Tool 数量持续增长时，控制上下文体积、Tool 选择复杂度
和顶层 Tool Set 的变化频率。
