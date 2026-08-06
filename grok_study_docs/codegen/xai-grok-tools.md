# xai-grok-tools 代码导读

> `crates/codegen/xai-grok-tools` · 内置 agent 工具、注册表、`ToolBridge`
> 配套：[xai-grok-shell](./xai-grok-shell.md) · [05 工具权限](../05_tools_workspace_permissions.md)

---

## 1. 系统位置

```text
AgentBuilder (xai-grok-agent)
  → ToolRegistryBuilder::register::<T>()
  → ToolBridge::finalize_builder(config, SessionContext)
SessionActor (xai-grok-shell)
  → WorkspaceOps::call_tool → ToolBridge::call
  → ToolRunResult → chat_state.push_tool_result
```

| 常量 | 值 | 含义 |
| --- | ---: | --- |
| `DEFAULT_TOOL_OUTPUT_BYTES` | 40000 | 工具输出字节上限 |
| `DEFAULT_TOOL_OUTPUT_CHARS` | 20000 | bash 字符上限 |

---

## 2. 顶层模块

- **`bridge`** — ToolBridge 门面：call、definitions、skill/AGENTS 种子
- **`registry`** — ToolRegistryBuilder → FinalizedToolset
- **`implementations`** — grok_build / codex / opencode 等工具实现
- **`types`** — ToolKind、ToolInput/Output、Resources
- **`tool_taxonomy`** — x.ai/tool _meta 契约
- **`computer`** — TerminalBackend、AsyncFileSystem
- **`reminders`** — 执行后 system-reminder
- **`notification`** — 流式工具通知
- **`util`** — 截断、spawn、MCP truncate
- **`normalization`** — 参数 canonical 化
- **`versions`** — behavior_preset

---

## 3. 执行路径

### 构建（session spawn）
1. `ToolBridge::get_builder()`
2. `register::<ReadFileTool>()` …
3. `finalize_builder(ToolServerConfig, SessionContext)`

### 运行（tool call）
```text
dispatch_tool → call_tool → ToolBridge::call
  → FinalizedToolset::call → try_parse → Tool::execute
```

---

## 4. registry/types.rs 核心类型

| 类型 | 作用 |
| --- | --- |
| `ToolConfig` | per-tool id、override、params、kind |
| `ToolServerConfig` | 启用列表 + behavior_preset |
| `SessionContext` | terminal、fs、cwd、subagent、skills |
| `ToolRegistryBuilder` | register、finalize |
| `FinalizedToolset` | call、tool_definitions、MCP register |
| `register_tool_pack` | 进程级外部 tool pack 扩展 |

---

## 5. ToolBridge 方法

- `call / try_parse` — 执行 / 仅解析
- `tool_definitions / tool_definitions_builtins_only` — LLM tools 数组
- `tool_for_kind / tool_kind` — 按 ToolKind 查名
- `render_prompt` — minijinja `${{ tools.by_kind.* }}`
- `register_mcp_tools` — 运行时注册 MCP
- `seed_skill_discovery / seed_agents_md` — skill 与 AGENTS
- `kill_foreground_commands` — 取消时杀 bash

---

## 6. ToolKind / ToolNamespace

**Namespace：** GrokBuild · GrokBuildConcise · GrokBuildHashline · Codex · OpenCode · MCP

**Kind（节选）：** Read · Edit · Execute · Search · ListDir · Task · WebSearch · WebFetch · Skill · EnterPlan · ExitPlan · AskUser · Workflow · Memory*

---

## 7. implementations/grok_build/

| 子模块 | 说明 |
| --- | --- |
| `bash/` | Shell、background、block_until_ms |
| `read_file/` | 文件/图片/PDF/PPTX |
| `search_replace/` | 编辑 |
| `grep/` | ripgrep |
| `list_dir/` | 目录 |
| `task/` | 子 agent |
| `task_output / wait_tasks/` | 后台输出 |
| `kill_task/` | 杀任务 |
| `web_fetch / web_search/` | 网络 |
| `todo/` | TodoWrite |
| `enter_plan_mode / exit_plan_mode/` | Plan |
| `ask_user_question/` | 反向提问 |
| `scheduler/` | /loop |
| `monitor/` | monitor 事件 |
| `image_gen / video_gen/` | 媒体 |
| `workflow/` | xai-workflow |
| `lsp/` | LSP |

其他 toolset：`codex/` · `opencode/` · `grok_build_concise/` · `grok_build_hashline/` · `skills/` · `memory/`

---

## 8. 依赖与被依赖

依赖：xai-tool-runtime · xai-grok-sandbox · minijinja · async-lsp · reqwest

被依赖：xai-grok-shell · xai-grok-agent · xai-grok-workspace · xai-grok-hooks · xai-grok-mcp

---

## 9. 全文件索引

### `./`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `attribution.rs` | 103 | 401 attribution: callback hook + shared helpers for tool HTTP cli |
| `bridge.rs` | 910 | ToolBridge: adapter that wraps `xai-grok-tools`'s `ToolRegistry`  |
| `gitignore.rs` | 121 | Shared gitignore matching utility. |
| `lib.rs` | 38 | Grok tools library. |
| `normalization.rs` | 152 | First-party tool normalization — the `ToolInput`-coupled projecti |
| `persistence.rs` | 665 | Background persistence for tool state. |
| `retry.rs` | 154 | Generic retry utilities with exponential backoff. |
| `tool_taxonomy.rs` | 378 | Tool taxonomy — the harness-independent vocabulary, identity, and |
| `versions.rs` | 954 | Behavior version catalog for version-managed tools. |

### `computer/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 3 | Contains the computer implementation |
| `types.rs` | 425 | Create an IO error from a message string with no preserved error  |

### `computer/local/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `cgroup.rs` | 538 | Cgroup v2 memory-high monitor for graceful OOM handling. |
| `embedded_search_tools.rs` | 805 | Shadow `find`→`bfs` and `grep`→`ugrep` when those binaries resolv |
| `file_system.rs` | 344 | Creates a local FS access which allows writing and reading from t |
| `mock_fs.rs` | 122 | Mock file system implementation for testing. |
| `mod.rs` | 39 | Per-backend enable state for the bash-harness `find`→`bfs` / `gre |
| `shell_state.rs` | 1209 | Persistent shell state across command invocations. |
| `static_shell.rs` | 311 | Static (replay-only) login-shell capture for the non-persistent b |
| `terminal.rs` | 5405 | Actor-based terminal implementation with support for foreground a |

### `implementations/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `cursor_rules_on_read.rs` | 767 | Cursor project-rule reminders attached after successful file read |
| `mod.rs` | 29 | — |

### `implementations/codex/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 22 | Codex-specific tool implementations. |

### `implementations/codex/apply_patch/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `apply.rs` | 319 | Pure (I/O-free) patch application logic. |
| `errors.rs` | 58 | Error types for the codex apply-patch engine. |
| `mod.rs` | 25 | Codex `apply_patch` — core patch engine (pure library, no I/O). |
| `parser.rs` | 779 | Patch parser for the codex apply-patch format. |
| `seek_sequence.rs` | 186 | Fuzzy line-sequence matcher for the codex apply-patch engine. |
| `tool.rs` | 750 | `ApplyPatchTool` — Tool trait implementation for the codex apply- |

### `implementations/codex/grep_files/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 7 | Codex `grep_files` — file-path-only regex search. |
| `tool.rs` | 547 | `CodexGrepFilesTool` — file-path-only regex search via ripgrep. |

### `implementations/codex/list_dir/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 7 | Codex `list_dir` — paginated, depth-limited directory listing. |
| `tool.rs` | 716 | `CodexListDirTool` — paginated, depth-limited, BFS directory list |

### `implementations/codex/read_file/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `indentation.rs` | 708 | Indentation-mode reader — exact port of codex `indentation::*`. |
| `mod.rs` | 22 | Codex `read_file` — text file reader in codex `L{n}: {content}` f |
| `slice.rs` | 190 | Slice-mode reader — exact port of codex `slice::read()`. |
| `text_utils.rs` | 30 | Shared text utilities for the codex read_file tool. |
| `tool.rs` | 643 | `CodexReadFileTool` — Tool trait implementation for the codex rea |

### `implementations/editor_infra/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `file_operation_lock.rs` | 270 | File operation lock manager — serializes concurrent file operatio |
| `mod.rs` | 6 | Shared editor infrastructure used by the default grok_build tools |

### `implementations/grok_build/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `deploy_app_stub.rs` | 16 | Stub surface when the deploy feature is off. |
| `mod.rs` | 69 | New-architecture tool implementations (NewTool trait). |
| `storage.rs` | 250 | Session-scoped file storage with crash-safe atomic writes and bud |

### `implementations/grok_build/ask_user_question/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `format.rs` | 711 | Formatting functions for AskUserQuestion tool results. |
| `mod.rs` | 1120 | `AskUserQuestion` tool — new architecture (`Tool` trait). |
| `types.rs` | 684 | Shared protocol and channel types for the AskUserQuestion blockin |

### `implementations/grok_build/bash/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 5045 | `run_terminal_cmd` (Bash) tool — new architecture (`Tool` trait). |

### `implementations/grok_build/enter_plan_mode/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 707 | `EnterPlanMode` tool — new architecture (`Tool` trait). |

### `implementations/grok_build/exit_plan_mode/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 439 | `ExitPlanMode` tool — new architecture (`Tool` trait). |
| `types.rs` | 122 | Wire types for the `x.ai/exit_plan_mode` ACP ext_method. |

### `implementations/grok_build/grep/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 2596 | `grep` tool — new architecture (`Tool` trait). |
| `ripgrep.rs` | 81 | Get the path to the ripgrep executable. |

### `implementations/grok_build/image_edit/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 704 | `image_edit` tool — edits or transforms images via the xAI Imagin |

### `implementations/grok_build/image_gen/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 627 | `image_gen` tool — generates images via the xAI Imagine API and s |

### `implementations/grok_build/kill_task/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 788 | `kill_task` tool — new architecture (`Tool` trait). |
| `terminal_command.rs` | 246 | Workspace-only, subagent-free variant of `kill_task` (delegates t |

### `implementations/grok_build/list_dir/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 1347 | `list_dir` tool — new architecture (`Tool` trait). |

### `implementations/grok_build/list_dir/versions/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `legacy_0_4_10.rs` | 452 | Legacy (0.4.10) depth-threshold directory rendering. |
| `mod.rs` | 7 | Version-specific behavior modules for `list_dir`. |

### `implementations/grok_build/lsp/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 115 | `lsp` tool - code intelligence via language servers. |

### `implementations/grok_build/monitor/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `event.rs` | 224 | Processes raw stdout chunks into complete lines. |
| `mod.rs` | 4 | — |
| `rate_limiter.rs` | 275 | Token bucket rate limiter. |
| `tool.rs` | 646 | Background pipeline: polls the task output and feeds lines throug |
| `types.rs` | 175 | Max characters per individual stdout line before truncation. |

### `implementations/grok_build/read_file/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 2517 | ReadFile — new-architecture implementation. |

### `implementations/grok_build/read_file/versions/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `legacy_0_4_10.rs` | 26 | Legacy (0.4.10) behavior for `read_file`. |
| `mod.rs` | 8 | Version-specific behavior modules for `read_file`. |

### `implementations/grok_build/scheduler/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `actor.rs` | 2821 | — |
| `create.rs` | 490 | Whether the task persists across sessions. Default false (session |
| `delete.rs` | 143 | Canonical tool name advertised by `SchedulerDeleteTool::id()`. |
| `interval.rs` | 165 | Parse an interval string like "5m", "2h", "30s", "1d" into second |
| `list.rs` | 146 | — |
| `mod.rs` | 7 | — |
| `occurrence_journal.rs` | 566 | Persisted one-shot removal receipts and restart reconciliation. |
| `types.rs` | 460 | Set when the prompt is patched: the next fire starts a fresh |

### `implementations/grok_build/search_replace/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `helpers.rs` | 532 | SearchReplace tool implementation. |
| `mod.rs` | 2490 | SearchReplace (Edit) tool — new architecture (`Tool` trait). |

### `implementations/grok_build/search_replace/versions/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `legacy_0_4_10.rs` | 92 | Legacy (0.4.10) error downgrade for `search_replace`. |
| `mod.rs` | 8 | Version-specific behavior modules for `search_replace`. |

### `implementations/grok_build/task/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `backend.rs` | 453 | Backend trait abstracting how subagent operations are dispatched. |
| `coordinator.rs` | 840 | Single-writer subagent coordinator actor. |
| `coordinator_state.rs` | 731 | Runtime-specific live progress for one active child. |
| `mod.rs` | 2557 | `task` tool — launches a subagent to handle a task autonomously. |
| `types.rs` | 1590 | Data and channel types for subagent coordination. |

### `implementations/grok_build/task/coordinator/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `query.rs` | 256 | Session-scoped query, inspection, and progress delivery. |

### `implementations/grok_build/task_output/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 2024 | `get_task_output` tool — output/status for one or many background |
| `terminal_command.rs` | 160 | Workspace-only, subagent-free variant of `get_task_output` (deleg |
| `wait_tasks.rs` | 308 | `wait_tasks` tool — blocks until multiple background tasks comple |

### `implementations/grok_build/todo/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 1007 | TodoWrite — new-architecture implementation. |

### `implementations/grok_build/update_goal/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 512 | `update_goal` — model-driven goal progress reporting. |

### `implementations/grok_build/video_gen/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 1534 | Video generation module. Hosts the shared [`VideoGenClient`] and  |

### `implementations/grok_build/web_fetch/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `artifact.rs` | 187 | — |
| `cache.rs` | 94 | In-memory cache for self-contained text fetches with TTL expiry a |
| `client.rs` | 1533 | `WebFetchClient` - shared HTTP client with cache, HTML-to-markdow |
| `config.rs` | 199 | Runtime-configurable parameters for the `web_fetch` tool. |
| `domain.rs` | 376 | Domain allowlist matching with precomputed host → path-prefix loo |
| `error.rs` | 157 | Structured errors for the `web_fetch` tool. |
| `http.rs` | 140 | Cached HTTP client with atomic invalidation for `web_fetch`. |
| `mod.rs` | 234 | `web_fetch` tool — client-side URL fetching with improved HTML-to |
| `overflow.rs` | 845 | Bounded inline previews with recoverable session artifacts and to |
| `ssrf.rs` | 416 | SSRF (Server-Side Request Forgery) protection for `web_fetch`. |

### `implementations/grok_build/web_search/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 151 | `web_search` tool — new architecture (`Tool` trait). |

### `implementations/grok_build/workflow/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 414 | — |

### `implementations/grok_build_concise/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `bash.rs` | 287 | Concise variant of the `run_terminal_cmd` (bash) tool. |
| `mod.rs` | 13 | `GrokBuildConcise` namespace — concise variants of core GrokBuild |
| `read_file.rs` | 190 | Concise variant of the `read_file` tool. |
| `search_replace.rs` | 202 | Concise variant of the `search_replace` tool. |

### `implementations/grok_build_hashline/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `anchor.rs` | 157 | Anchor convenience helpers and re-exports. |
| `benchmark.rs` | 887 | Offline benchmark harness for comparing anchor schemes. |
| `config.rs` | 390 | Hashline scheme configuration — shared across all hashline tools. |
| `grep.rs` | 759 | `hashline_grep` — anchor-annotated search results. |
| `mod.rs` | 24 | `GrokBuildHashline` namespace — hashline-anchored read/edit/searc |
| `mutate.rs` | 393 | Synthetic mutation generation for the hashline benchmark harness. |
| `read_file.rs` | 866 | `hashline_read` — anchor-annotated file reading. |
| `scheme.rs` | 1230 | Anchor scheme abstraction and candidate implementations. |

### `implementations/grok_build_hashline/edit/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `apply.rs` | 2237 | Core edit-application logic for `hashline_edit`. |
| `mod.rs` | 1147 | `hashline_edit` — anchor-based file editing. |
| `range_policy.rs` | 88 | Stateless range-size policy for hashline edit safety. |
| `types.rs` | 226 | Input/output types for the `hashline_edit` tool. |

### `implementations/lsp/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `client.rs` | 693 | Single LSP server connection — spawn, handshake, protocol methods |
| `config.rs` | 337 | LSP server configuration from `.grok/lsp.json`. |
| `dispatch.rs` | 565 | Bridges xai-grok-tools LspBackend trait to LspManager. |
| `format.rs` | 72 | Formatting helpers for LSP results. |
| `manager.rs` | 479 | Manages multiple LSP servers, routes by file extension, collects  |
| `mod.rs` | 66 | — |
| `restart.rs` | 397 | Monitors LSP servers for crashes and auto-restarts them. |
| `tests.rs` | 1313 | Creates a single-server LspManager with the mock TS server, alrea |
| `types.rs` | 120 | LSP configuration passed from shell. Same pattern as `WebSearchCo |

### `implementations/memory/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `get_tool.rs` | 194 | `memory_get` tool — new architecture (`Tool` trait). |
| `mod.rs` | 43 | Memory tools for cross-session knowledge retrieval. |
| `search_tool.rs` | 109 | `memory_search` tool — new architecture (`Tool` trait). |
| `types.rs` | 53 | Input/output types for memory tools. |

### `implementations/opencode/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 31 | OpenCode-specific tool implementations. |

### `implementations/opencode/bash/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 1201 | `bash` tool — OpenCode namespace. |

### `implementations/opencode/edit/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 1131 | `edit` tool — OpenCode namespace. |

### `implementations/opencode/glob/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 767 | `glob` tool — OpenCode architecture (`Tool` trait). |

### `implementations/opencode/grep/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 1032 | `grep` tool — OpenCode namespace. |

### `implementations/opencode/read/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 1359 | OpenCode `read` tool — reads files, directories, images, and PDFs |

### `implementations/opencode/skill/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 935 | `skill` tool — OpenCode variant of the skill tool. |

### `implementations/opencode/todowrite/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 812 | OpenCode `todowrite` tool — full-replace task list management. |

### `implementations/opencode/write/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 467 | OpenCode `write` tool — writes entire file contents to disk. |

### `implementations/read_file/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `image.rs` | 517 | Image compression for conversation embedding. |
| `metadata.rs` | 92 | Magic-byte metadata inspection shared by read tools. |
| `mod.rs` | 18 | Shared image/PDF/metadata helpers for read tools (grok_build, etc |
| `pdf.rs` | 549 | PDF text extraction and page rendering shared by read tools. |
| `pptx.rs` | 232 | PPTX text extraction shared by read tools. |

### `implementations/search_tool/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 706 | `search_tool` — discover MCP tools via BM25 keyword search. |
| `types.rs` | 20 | Types for the `search_tool`. |

### `implementations/skills/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `discovery.rs` | 1607 | SKILL.md filesystem discovery and parsing. |
| `mod.rs` | 3 | — |
| `skill.rs` | 1279 | Skill tool implementation - allows the agent to invoke user-defin |
| `types.rs` | 232 | Scope/priority of a skill based on where it was discovered. |

### `implementations/task_output/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 3 | Task output tool module. |
| `tool.rs` | 271 | Task output tool — old `impl Tool` deleted. |

### `implementations/use_tool/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 1652 | `use_tool` — dispatch to a discovered MCP tool. |

### `implementations/web_search/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `client.rs` | 752 | A minimal, purpose-built HTTP client for calling the Responses AP |
| `mod.rs` | 5 | — |
| `tool.rs` | 3 | Web search tool — old `impl Tool` deleted. |
| `types.rs` | 129 | Configuration for the web search tool. |

### `notification/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `handle.rs` | 383 | Envelope for consumers that can acknowledge durable notification  |
| `mod.rs` | 33 | — |
| `types.rs` | 656 | Contains the various kind of notifications which can be sent by t |

### `registry/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 4 | Contains the registry for all the tools |
| `proto_convert.rs` | 229 | Conversion from the gRPC wire config types (`xai-grok-tools-api`) |
| `types.rs` | 4658 | Process-global registry of external "tool packs" — functions that |

### `reminders/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `lsp_diagnostics.rs` | 48 | Cross-cutting reminder: notifies LSP of file changes and drains d |
| `mod.rs` | 189 | Cross-cutting reminders for tool outputs. |
| `skill_discovery.rs` | 254 | Skill discovery reminder — discovers new skills near accessed pat |
| `task_completion.rs` | 2014 | Background task and subagent completion reminder. |

### `types/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `agents_md_tracker.rs` | 959 | Tracks undiscovered AGENTS.md files during a session. |
| `api_key_provider.rs` | 26 | Resolves the current API key for tool HTTP requests. |
| `claude_alias.rs` | 143 | Canonical external-settings tool name ↔ Grok tool correspondence: |
| `compat.rs` | 632 | Vendor compatibility configuration for third-party agent surfaces |
| `config_source.rs` | 98 | Where a piece of configuration was loaded from. |
| `context.rs` | 146 | Client-configurable truncation settings. |
| `definition.rs` | 45 | Tool definition types for the model API. |
| `description.rs` | 245 | MiniJinja-based description template rendering for tool descripti |
| `error.rs` | 44 | Error type for SearchReplace tool operations. |
| `memory_backend.rs` | 282 | Backend-agnostic trait for memory search and retrieval. |
| `mod.rs` | 37 | — |
| `output.rs` | 2480 | `(added, removed)` line counts for the `edit.lines` telemetry cou |
| `params_validation.rs` | 138 | — |
| `process_manager.rs` | 14 | Process manager utilities. |
| `requirements.rs` | 261 | Requirement expressions for tool dependency validation. |
| `resources.rs` | 1652 | Type-safe heterogeneous resource container for the new tool archi |
| `schema.rs` | 371 | Helper schema types for JSON Schema generation. |
| `session_mode.rs` | 60 | Canonical session-mode enum shared between the agent and pager. |
| `template_renderer.rs` | 753 | Pre-built template renderer for tool/param name resolution and |
| `tool.rs` | 139 | Tool types and post-execution reminders. |
| `tool_index.rs` | 80 | Backend-agnostic trait for tool search/discovery. |
| `tool_io.rs` | 206 | New tool I/O types for the spec architecture. |
| `tool_metadata.rs` | 198 | `ToolMetadata` — grok-tools-specific metadata for tools. |

### `types/skill_discovery_tracker/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `conditional.rs` | 115 | `paths:`-gated conditional skills and their per-session activatio |
| `listing.rs` | 1861 | Budget-capped skill listing formatter. |
| `mod.rs` | 1576 | Session-scoped skill lifecycle manager. |

### `util/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `base64_images.rs` | 845 | Extract base64-encoded images from tool result text so they can b |
| `binary.rs` | 163 | Known binary file extensions — skip content reading for these. |
| `command_display.rs` | 378 | Display-only helpers for shell command chrome (activity titles, e |
| `env.rs` | 81 | Environment variable helpers and process isolation for terminal e |
| `fs.rs` | 288 | Filesystem helpers shared across tool implementations. |
| `git_detect.rs` | 249 | Detection of `git commit` / `gh pr create` / `gh pr merge` in ter |
| `grok_home.rs` | 4 | — |
| `hash.rs` | 203 | Shared hashing utilities for hashline anchor generation. |
| `image_compress.rs` | 286 | Shared image re-encoding with PNG+JPEG format selection. |
| `image_validate.rs` | 1041 | Shared image-bytes validation: sniff format, MIME allow-list, opt |
| `mcp_truncate.rs` | 469 | Shared size-bounding for MCP/text tool output. |
| `mod.rs` | 41 | — |
| `path_suggestions.rs` | 391 | Path-not-found enrichment hints for tool error messages. |
| `query_tools.rs` | 137 | `$PATH`-aware helper for steering messages that suggest shell too |
| `remap.rs` | 180 | Utilities for remapping tool/parameter names in JSON values and s |
| `serde_base64.rs` | 173 | Encodes a byte payload as a base64 string instead of a JSON integ |
| `shell_env_policy.rs` | 237 | Controls which environment variables agent subprocesses (bash too |
| `spawn.rs` | 10 | Cross-platform child-process lifecycle helpers for `tokio::proces |
| `truncate.rs` | 722 | Default wrap width for soft-wrapping (used by bash, task_output). |
| `unicode_confusables.rs` | 612 | Unicode confusable-character detection and normalization. |

---

## 10. 改动指南

| 目标 | 入口 |
| --- | --- |
| 新工具 | implementations + ToolRegistryBuilder::register |
| MCP | register_mcp_tools |
| 输出上限 | DEFAULT_TOOL_OUTPUT_* |
| 行为版本 | versions/ + behavior_preset |

---

## 11. ToolRegistryBuilder 注册机制

`register_with_params<T, P>()`（`registry/types.rs` ~567 行）为每个工具建立 `ToolEntry`：

```text
T::default()
  → name = "{namespace}:{id}"   // 如 grok_build:read_file
  → input_schema = generate_schema::<T::Args>()
  → metadata / output_converter / parse_input 闭包
  → register_in_local: LocalRegistry.register(T)
```

类型约束（编译期）：

- `T: xai_tool_runtime::Tool + ToolMetadata + Default`
- `T::Args: DeserializeOwned + JsonSchema + Into<ToolInput>`
- `T::Output: Serialize + DeserializeOwned + Into<ToolOutput>`
- `P: ResourceType` — 工具级配置（如 `BashParams`）存入 `Resources`

`finalize(config, ctx)` 遍历 `ToolServerConfig` 中启用的工具名，校验 `requires_expr`，
把 `SessionContext` 注入 `Resources`，构建 `TemplateRenderer`、`FinalizedToolset`。

---

## 12. FinalizedToolset::call 分发链

入口：`call` → `call_with_cancellation` → `call_streaming_with_cancellation`（~1496–1599 行）

```text
prepare_dispatch(tool_name, tool_args, tool_call_id, cwd_override, cancellation)
  ├─ 解析 client name → ToolEntry（支持 name override）
  ├─ parse_input → ToolInput
  ├─ 组装 ToolContext（Resources + Cwd override）
  └─ LocalRegistry handle → execute(ctx, canonical_params)
         ↓ stream
finalize_output(typed_value, output_converter, effective_tool_name)
  ├─ reminders（LSP / skill / task completion）
  ├─ truncate（DEFAULT_TOOL_OUTPUT_BYTES/CHARS）
  └─ ToolRunResult { output, prompt_text }
```

**`cwd_override`**：单次调用的工作目录覆盖，栈局部、不共享，避免并发 tool call 竞态。

**`call_streaming`**：Progress 事件原样转发；Terminal 经 `finalize_output` 后产出 `ToolRunResult`。

---

## 13. ToolBridge 与 shell 边界

`bridge.rs` 持有 `Arc<FinalizedToolset>` + 可选 `Arc<dyn TerminalBackend>`。

**为何 terminal 单独存放？** 取消时 `kill_foreground_commands()` 需在 `call()` 持锁期间
仍能访问 PTY，避免死锁（见 `bridge.rs` 注释 Cancellation Safety）。

```rust
// bridge.rs — finalize 后抽出 terminal
terminal = finalized_toolset.resources.lock().await.get::<Terminal>().map(|t| t.0.clone());
```

**`ToolBridgeResult`**：`output`（JSON/ACP）+ `prompt_text`（含 system-reminder，喂给模型）。

Shell 调用链（`tool_dispatch.rs`）：

```text
SessionActor::execute_tool_calls
  → dispatch_tool(workspace_ops, prepared, session_id)
  → WorkspaceOps::call_tool(name, args, call_id, Some(session_id))
  → workspace 内 ToolBridge::call
```

同文件还有：`lock_path_for_args`（同文件编辑串行化）、`build_tool_parse_error_message`（参数 JSON 修复提示）。

---

## 14. SessionContext 与 Resources

`SessionContext`（finalize 时传入）典型字段：

| 资源 | 用途 |
| --- | --- |
| `Terminal` / `TerminalBackend` | bash、前台命令 |
| `AsyncFileSystem` | read/write/grep 路径访问 |
| `Cwd` | 默认工作目录 |
| `OwnerSessionId` | 子 agent / task 归属 |
| `State` | 会话可变状态（todo、plan mode） |
| `TemplateRenderer` | prompt 占位符、kind→name |
| `AgentsMdTracker` / skill trackers | AGENTS.md、skill 发现 |

`types/resources.rs`（~1652 行）实现类型安全的异构容器：`insert/get/register_params`。
工具通过 `ToolContext` 在 `execute` 时读取，而非全局静态。

---

## 15. AgentBuilder 如何注册工具

`xai-grok-agent/src/builder.rs` 在 `build()` 中：

1. 根据 `AgentDefinition.tools` / preset 解析工具名列表
2. `ToolBridge::get_builder()` + 按 toolset 调用 `register_grok_build_tools` 等
3. `ToolServerConfig { enabled_tools, behavior_preset, ... }`
4. `ToolBridge::finalize_builder(builder, config, session_ctx).await`
5. 产物挂到 `Agent { tool_bridge, ... }`

vendor 兼容：`claude_tool_kind(name)` → `xai_grok_tools::types::kind_for(name)`，
让 Claude Code 风格 `tools:` 白名单映射到 `ToolKind`。

---

## 16. 代表性工具实现

### 16.1 Bash (`implementations/grok_build/bash/`)

- 参数：`command`、`description`、`is_background`、`block_until_ms` / `timeout`
- 经 `TerminalBackend::run(TerminalRunRequest)` 执行
- 输出：`BashOutput` → truncate → `prompt_text` 可含 working_dir、exit_code
- Shell **bash mode** 绕过模型，直接 `terminal.run`（见 shell `tool_dispatch.rs` `handle_direct_bash_command`）

### 16.2 Read (`read_file/`)

- 支持文本、图片 base64、PDF/PPTX 提取
- `util/binary.rs` 跳过已知二进制扩展名
- `util/image_validate.rs` 校验 MIME/尺寸

### 16.3 SearchReplace (`search_replace/`)

- 结构化编辑；输出 `edit.lines` 遥测
- shell 用 `lock_path_for_args` 对同 `file_path` 串行

### 16.4 Task (`task/`)

- 启动子 agent（`SubagentEntry`）
- 与 `reminders/task_completion.rs`（~2014 行）配合：后台完成时注入 reminder

### 16.5 Grep / ListDir

- grep 封装 ripgrep；list_dir 受 workspace 权限与 ignore 规则约束

---

## 17. Reminder 管道

`reminders/mod.rs` 在 `finalize_output` 之后追加 **system-reminder** 文本到 `prompt_text`：

| 模块 | 触发 |
| --- | --- |
| `lsp_diagnostics.rs` | 文件变更后 LSP 诊断 |
| `skill_discovery.rs` | 访问路径附近发现新 skill |
| `task_completion.rs` | 子 agent / 后台 bash 完成 |

模型看到的是 **tool_result 正文 + reminder**；`output` 字段保持「干净」供 ACP/持久化。

---

## 18. MCP 动态工具

- `ToolBridge::register_mcp_tools` — session 运行时把 MCP server 工具注册进 registry
- `util/mcp_truncate.rs` — MCP 返回体大小限制
- shell：`acp_session_impl/mcp.rs` 继承共享 MCP 客户端并注册到当前 session bridge

工具名通常带 server 前缀；`tool_definitions_builtins_only` 过滤 MCP，仅给需要内置 schema 的场景。

---

## 19. tool_taxonomy 与 x.ai/tool _meta

`tool_taxonomy.rs` 定义 LLM wire 与内部 `ToolKind` 的 `_meta` 契约（`x.ai/tool`）。
Shell `stamp_tool_meta` 把 wire name + `ToolInput` 写入 ACP `ToolCall.meta`，pager 据此选渲染器。

相关：`types/claude_alias.rs`（Claude 工具名 ↔ Grok 名）、`types/compat.rs`（第三方 agent 表面兼容）。

---

## 20. 输出截断与图像处理

```text
工具原始输出
  → types/output.rs 格式化
  → util/truncate.rs（软换行、字节/字符上限）
  → util/base64_images.rs（大图从 prompt 剥离）
  → util/mcp_truncate.rs（MCP 专用）
```

常量：`DEFAULT_TOOL_OUTPUT_BYTES=40000`、`DEFAULT_TOOL_OUTPUT_CHARS=20000`（`lib.rs` 导出）。

---

## 21. 外部 Tool Pack

`register_tool_pack(fn)` 在进程级注册扩展：out-of-tree crate 可在 finalize 前向
`ToolRegistryBuilder` 贡献 `register::<T>()`。用于内部实验或插件化工具集。

---

## 22. 本 crate 推荐阅读顺序

1. `lib.rs` — 模块树与常量
2. `bridge.rs` — 对外 API
3. `registry/types.rs` — `ToolRegistryBuilder` / `FinalizedToolset::call`
4. `types/tool.rs` + `tool_taxonomy.rs` — Kind/Namespace
5. `types/resources.rs` + `types/output.rs`
6. `implementations/grok_build/mod.rs` — 默认工具集注册列表
7. 选一个工具目录（如 `bash/`）对照 `xai_tool_runtime::Tool` trait
8. shell：`tool_dispatch.rs` + `xai-grok-workspace` 的 `call_tool`

