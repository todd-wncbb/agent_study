# Grok Build Tool 全量代码实现文档

本文档集以当前仓库源码为准，覆盖 `ToolRegistryBuilder::new()` 静态注册的全部
Tool、运行时 Tool Pack，以及 MCP 动态工具。阅读时首先要区分三个数字：

- **50 个静态注册实现**：二进制内置、注册表可以选择的实现总数。
- **18 个默认 Grok Build Tool**：默认 Agent 通常发给 LLM 的 Tool。
- **N 个动态 Tool**：Tool Pack 和 MCP 在运行时继续加入，数量不固定。

“注册”不等于“启用”，“启用”也不等于“名称原样发给模型”。最终 Tool Definition
还会经过 preset 选择、requirements 过滤、名称/参数重写、description 模板渲染和
JSON Schema 生成。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [01-runtime-registry.md](01-runtime-registry.md) | Tool trait、注册、finalize、Schema、调用、Resources、Reminder、MCP 动态注册 |
| [../grok-build-tools-and-mcp-discovery.md](../grok-build-tools-and-mcp-discovery.md) | 默认 18 个 Tool 的逐项 I/O，以及 `search_tool` / `use_tool` 的 BM25 和分发细节 |
| [02-grok-build-optional-tools.md](02-grok-build-optional-tools.md) | GrokBuild 可选 Tool：Web、LSP、媒体、Plan、Ask、Memory 等 |
| [03-compatibility-toolsets.md](03-compatibility-toolsets.md) | Codex、OpenCode、Concise、Hashline 的全部兼容实现 |
| [04-skill-implementation.md](04-skill-implementation.md) | Skill 发现、Prompt 目录、正文加载、slash 预展开、OpenCode `skill` Tool |
| [05-cross-cutting-behavior.md](05-cross-cutting-behavior.md) | 权限、输出截断、文件锁、动态提醒、持久化和安全边界 |
| [../13_prompt_and_tool_list_assembly.md](../13_prompt_and_tool_list_assembly.md) | Tool Definition 如何与 system/user/history 独立组装成每次采样请求 |

## 静态注册表：50 个 Tool

注册入口：
[`registry/types.rs`](../../crates/codegen/xai-grok-tools/src/registry/types.rs)。

### GrokBuild：28 个

| # | Registry ID | Rust 实现 | 默认 | 主要用途 |
| ---: | --- | --- | :---: | --- |
| 1 | `GrokBuild:run_terminal_cmd` | `BashTool` | 是 | 前台/后台 Shell、超时、任务输出持久化 |
| 2 | `GrokBuild:read_file` | `ReadFileTool` | 是 | 文本、图片、PDF、PPTX、Notebook 读取 |
| 3 | `GrokBuild:search_replace` | `SearchReplaceTool` | 是 | 精确字符串替换和 Patch 结果 |
| 4 | `GrokBuild:list_dir` | `ListDirTool` | 是 | gitignore-aware 目录浏览 |
| 5 | `GrokBuild:grep` | `GrepTool` | 是 | ripgrep 内容搜索 |
| 6 | `GrokBuild:kill_task` | `KillTaskTool` | 是 | 统一终止命令、子 Agent、Monitor |
| 7 | `GrokBuild:kill_terminal_command` | `KillTerminalCommandTool` | 否 | 仅终止后台终端命令的兼容入口 |
| 8 | `GrokBuild:todo_write` | `TodoWriteTool` | 是 | 会话 Todo 状态 |
| 9 | `GrokBuild:update_goal` | `UpdateGoalTool` | 是 | Goal 完成/阻塞状态上报 |
| 10 | `GrokBuild:workflow` | `WorkflowTool` | 是 | 启动 Rhai 多 Agent Workflow |
| 11 | `GrokBuild:get_task_output` | `TaskOutputTool` | 是 | 命令和子 Agent 的统一输出查询 |
| 12 | `GrokBuild:get_terminal_command_output` | `GetTerminalCommandOutputTool` | 否 | 仅后台终端输出查询的兼容入口 |
| 13 | `GrokBuild:wait_tasks` | `WaitTasksTool` | 是 | 等待任一/全部后台任务 |
| 14 | `GrokBuild:task` | `TaskTool` | 是 | 启动或恢复子 Agent |
| 15 | `GrokBuild:web_search` | `WebSearchTool` | 否 | xAI Web Search 客户端包装 |
| 16 | `GrokBuild:web_fetch` | `WebFetchTool` | 否 | URL 获取、缓存、SSRF 防护、溢出落盘 |
| 17 | `GrokBuild:lsp` | `LspTool` | 否 | LSP hover/definition/references 等统一分发 |
| 18 | `GrokBuild:image_gen` | `ImageGenTool` | 否 | 文生图请求、附件与多模态结果 |
| 19 | `GrokBuild:image_edit` | `ImageEditTool` | 否 | 基于输入图像的编辑 |
| 20 | `GrokBuild:image_to_video` | `ImageToVideoTool` | 否 | 单图生成视频 |
| 21 | `GrokBuild:reference_to_video` | `ReferenceToVideoTool` | 否 | 多参考生成视频 |
| 22 | `GrokBuild:enter_plan_mode` | `EnterPlanModeTool` | 否 | 请求进入 Plan Mode |
| 23 | `GrokBuild:exit_plan_mode` | `ExitPlanModeTool` | 否 | 提交计划并退出 Plan Mode |
| 24 | `GrokBuild:ask_user_question` | `AskUserQuestionTool` | 否 | 阻塞等待结构化用户选择 |
| 25 | `GrokBuild:monitor` | `MonitorTool` | 是 | 后台运行脚本并逐行推送事件 |
| 26 | `GrokBuild:scheduler_create` | `SchedulerCreateTool` | 是 | 创建/更新周期任务 |
| 27 | `GrokBuild:scheduler_delete` | `SchedulerDeleteTool` | 是 | 删除周期任务 |
| 28 | `GrokBuild:scheduler_list` | `SchedulerListTool` | 是 | 列出周期任务 |

### Codex：4 个

| # | Registry ID | Rust 实现 | 用途 |
| ---: | --- | --- | --- |
| 29 | `Codex:apply_patch` | `ApplyPatchTool` | 解析 Codex Patch DSL，支持多文件增删改移动 |
| 30 | `Codex:list_dir` | `CodexListDirTool` | BFS、深度限制、分页目录浏览 |
| 31 | `Codex:grep_files` | `CodexGrepFilesTool` | 仅返回匹配文件路径的 regex 搜索 |
| 32 | `Codex:read_file` | `CodexReadFileTool` | slice / indentation 两种文本读取模式 |

### OpenCode：8 个

| # | Registry ID | Rust 实现 | 用途 |
| ---: | --- | --- | --- |
| 33 | `OpenCode:bash` | `OpenCodeBashTool` | OpenCode 参数和输出约定的 Bash |
| 34 | `OpenCode:read` | `OpenCodeReadTool` | 文件或目录读取 |
| 35 | `OpenCode:edit` | `OpenCodeEditTool` | `oldString/newString` 精确替换 |
| 36 | `OpenCode:write` | `OpenCodeWriteTool` | 创建或整体覆盖文件 |
| 37 | `OpenCode:grep` | `OpenCodeGrepTool` | OpenCode 风格 ripgrep |
| 38 | `OpenCode:glob` | `OpenCodeGlobTool` | Glob 文件发现和时间排序 |
| 39 | `OpenCode:todowrite` | `OpenCodeTodoWriteTool` | OpenCode Todo 协议 |
| 40 | `OpenCode:skill` | `OpenCodeSkillTool` | 精确名称查找并加载 `SKILL.md` 正文 |

### Memory、MCP 发现：4 个

| # | Registry ID | Rust 实现 | 用途 |
| ---: | --- | --- | --- |
| 41 | `GrokBuild:memory_search` | `MemorySearchImpl` | 搜索持久化记忆片段 |
| 42 | `GrokBuild:memory_get` | `MemoryGetImpl` | 按路径/行段读取记忆内容 |
| 43 | `GrokBuild:search_tool` | `SearchTool` | BM25 搜索 MCP Tool 元数据和 Schema |
| 44 | `GrokBuild:use_tool` | `UseTool` | 调用已经发现的 MCP Tool |

### Concise：3 个

| # | Registry ID | Rust 实现 | 与标准版的差异 |
| ---: | --- | --- | --- |
| 45 | `GrokBuildConcise:run_terminal_cmd` | `BashConciseTool` | 复用 Bash 执行核心，缩短 description/结果表达 |
| 46 | `GrokBuildConcise:read_file` | `ReadFileConciseTool` | 复用读取核心，使用精简 Tool 契约 |
| 47 | `GrokBuildConcise:search_replace` | `SearchReplaceConciseTool` | 复用编辑核心，使用精简 Tool 契约 |

### Hashline：3 个

| # | Registry ID | Rust 实现 | 用途 |
| ---: | --- | --- | --- |
| 48 | `GrokBuildHashline:hashline_read` | `HashlineReadTool` | 为每行生成稳定 hash anchor |
| 49 | `GrokBuildHashline:hashline_edit` | `HashlineEditTool` | 以 anchor/range 为前置条件执行编辑 |
| 50 | `GrokBuildHashline:hashline_grep` | `HashlineGrepTool` | 搜索结果携带可用于编辑的 anchor |

## 默认 18 个 Tool 的模型可见名称

默认 preset 会重命名一部分 Tool：

| Registry ID | 默认模型可见名称 |
| --- | --- |
| `GrokBuild:run_terminal_cmd` | `run_terminal_command` |
| `GrokBuild:task` | `spawn_subagent` |
| `GrokBuild:get_task_output` | `get_command_or_subagent_output` |
| `GrokBuild:wait_tasks` | `wait_commands_or_subagents` |
| `GrokBuild:kill_task` | `kill_command_or_subagent` |

其余默认 Tool 通常保持 ID 中的短名称。精确清单和逐项 Input/Output 见
[默认工具文档](../grok-build-tools-and-mcp-discovery.md)。

## 运行时扩展：数量不固定

静态 50 个之外还有两条扩展路径：

1. `register_tool_pack()`：进程启动期间由外部 crate 向 builder 注册新的本地 Tool。
2. `register_mcp_tools()`：Session 初始化或 MCP Server 更新时，把远端 Tool 动态注册到
   `FinalizedToolset`；默认大规模场景不会全部发给 LLM，而是进入 `ToolIndex` 后由
   `search_tool` / `use_tool` 间接访问。

因此“仓库最终一共有多少 Tool”没有固定上限；**50 是当前核心注册表的静态实现数**。

## 每个 Tool 页应回答的问题

本文档集对每个 Tool 使用相同阅读框架：

1. Registry ID、namespace、默认是否启用、模型可见重命名。
2. `Args` / `Output` Rust 类型和生成的 JSON Schema。
3. `ToolMetadata`：kind、requirements、description 模板。
4. `run()` 的主要分支、后端资源和异步边界。
5. 输出如何转换为模型可见文本/多模态块。
6. Reminder、权限、持久化、取消和并发行为。
7. 与其他 namespace 同类 Tool 的差异。
8. 已知限制和最值得继续追踪的代码位置。
