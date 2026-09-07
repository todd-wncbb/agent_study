# xai-grok-workspace 代码导读

> `crates/codegen/xai-grok-workspace` · 工作区 FS/VCS/权限、会话子系统、`WorkspaceOps`
> 配套：[05 工具权限](../05_tools_workspace_permissions.md) · [xai-grok-tools](./xai-grok-tools.md)

---

## 1. 职责

把 **工具执行、git/worktree、权限、MCP hub、会话状态** 封装成 `WorkspaceOps` trait，
供 shell 的 `SessionActor` 通过 channel/RPC 调用（本地 in-process 或远程 workspace daemon）。

```text
grok pager / shell
  → WorkspaceHandle / connect_local_workspace
  → WorkspaceOps::call_tool / permission / git / ...
  → session 内 ToolBridge
```

---

## 2. 核心模块

| 模块 | 作用 |
| --- | --- |
| `workspace_ops` | `WorkspaceOp` 枚举 + `WorkspaceOps` trait（call_tool 等） |
| `session` | `WorkspaceSession`、会话级 tool bridge 与状态 |
| `permission` | 工具/路径权限判定、folder trust |
| `file_system` | 抽象 FS、overlay、ignore |
| `worktree` | git worktree / fast-worktree 集成 |
| `hub / hub_server` | 多会话 hub、认证、channel |
| `mcp` | workspace 侧 MCP 配置 |
| `handle` | `WorkspaceHandle` 连接与生命周期 |
| `config` | `WorkspaceConfig`、`AgentSessionConfig` |
| `recovery` | 崩溃恢复 |

---

## 3. call_tool 路径

```text
WorkspaceOps::call_tool(tool_name, args, call_id, session_id)
  → session 查找对应 ToolBridge
  → ToolBridge::call(...).await
  → ToolRunResult 返回 shell
```

实现见 `workspace_ops.rs` + `session/`；shell 侧入口 `tool_dispatch::dispatch_tool`。

---

## 4. 权限与信任

- `permission/` — 工具调用前检查（读/写/执行/network）
- `folder_trust` / `trust` — 用户确认的目录信任
- 与 shell `SessionCommand::ApproveTool` 等命令联动

---

## 5. 源码文件索引

### `./`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `activity.rs` | 1944 | Per-session and connection-level activity tracking for tool  |
| `capability.rs` | 332 | Capability-mode filtering for session toolsets. |
| `channel.rs` | 19 | Shared transport types used by workspace communication layer |
| `config.rs` | 1113 | Workspace and session configuration types. |
| `daemonize.rs` | 1073 | Self-daemonization and single-instance locking for the works |
| `diag_server.rs` | 722 | In-guest diagnostics HTTP server (`/ready`, `/statusz`, `/lo |
| `discovery.rs` | 582 | Skill, plugin, project-config, and permissions discovery. |
| `envrc.rs` | 285 | Parse .envrc files and extract environment variables. |
| `error.rs` | 135 | Workspace error types. |
| `folder_trust.rs` | 1172 | Folder-trust DECISION side ("do you trust this folder?"). |
| `fs_notify.rs` | 365 | FsNotify adapter functions bridging [`xai_fsnotify`] events  |
| `handle.rs` | 9671 | [`WorkspaceHandle`] -- public handle to a workspace instance |
| `hub.rs` | 1414 | Server integration for the workspace. |
| `hub_auth.rs` | 490 | Hub [`AuthProvider`] from `~/.grok/auth.json` for the standa |
| `hub_channel.rs` | 127 | Server-proxied workspace utilities. |
| `hub_ids.rs` | 10 | Hub tool ID constants, canonical in `xai_grok_workspace_type |
| `hub_server.rs` | 3076 | Workspace-side RPC handler for server-proxied workspace meth |
| `lib.rs` | 210 | Core workspace library: FS, VCS, permissions, tool config, a |
| `mcp.rs` | 276 | MCP integration for the workspace server. |
| `preview_supervisor.rs` | 1338 | One-child supervisor for the in-sandbox preview-proxy. |
| `project_config.rs` | 115 | Project config-file discovery: locating repo-local `.mcp.jso |
| `recovery.rs` | 886 | Startup restart-recovery scan for the workspace upload queue |
| `rpc_envelope.rs` | 229 | `WorkspaceError` <-> wire-code mapping for the workspace RPC |
| `status_config.rs` | 628 | Runtime-tunable timing/threshold config for the workspace to |
| `telemetry.rs` | 32 | Stable `tracing` target for workspace telemetry events. |
| `trust.rs` | 1563 | Folder-trust store ("do you trust this folder?"). |
| `workspace_ops.rs` | 2191 | [`WorkspaceOps`] — dual-mode workspace operations handle. |

### `bin/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `workspace_server.rs` | 869 | Standalone workspace ToolServer for remote sandboxes. |
| `workspace_server_probe.rs` | 214 | Probe that verifies a running workspace-server actually serv |

### `file_system/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `acp_fs.rs` | 179 | When set, any path under `display_cwd` is rewritten to `root |
| `adapter.rs` | 89 | AcpFsAdapter: implements `xai-grok-tools::AsyncFileSystem` u |
| `attach_file.rs` | 546 | Contains utility functions to attach file content and render |
| `client_fs.rs` | 775 | Read-only filesystem helpers backing the client-facing |
| `codebase_index.rs` | 306 | Codebase Index Manager |
| `content.rs` | 273 | Batch of results sent during streaming search. |
| `ext_fs.rs` | 560 | Filesystem extension ops (`workspace.fs_*`) — the server-pro |
| `file_tree.rs` | 526 | Generates the file-tree based on how we are using it during  |
| `fs.rs` | 143 | Get the root directory for this filesystem. |
| `fuzzy.rs` | 479 | Matcher score, higher is better. |
| `git_status.rs` | 288 | Generates a compact git status for the system prompt. |
| `index.rs` | 1566 | Compact file index for efficient storage, transfer, and fuzz |
| `jj_status.rs` | 85 | Compact jj status for the system prompt. |
| `local_fs.rs` | 50 | — |
| `mock_fs.rs` | 58 | — |
| `mod.rs` | 360 | Result of one fuzzy-search poll tick (see [`WorkspaceHandle: |
| `walk.rs` | 344 | Shared filesystem core for the fs list/read ops. |

### `foreign_sessions/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `claude.rs` | 497 | — |
| `mod.rs` | 763 | Bounded, metadata-only listing of foreign coding-agent sessi |

### `foreign_sessions/capability/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `mod.rs` | 308 | — |
| `tests.rs` | 223 | — |
| `unix.rs` | 240 | — |
| `windows.rs` | 166 | — |

### `foreign_sessions/claude/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `projects.rs` | 55 | — |
| `tests.rs` | 411 | — |

### `foreign_sessions/codex/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `db.rs` | 291 | — |
| `files.rs` | 359 | — |
| `mod.rs` | 194 | — |

### `permission/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `auto_mode.rs` | 3169 | Auto permission mode: LLM transcript classifier with safe fa |
| `bash_command_splitting.rs` | 1889 | Internal representation of a parsed "plain" command: |
| `claude_settings.rs` | 564 | Reads and parses `.claude/settings.json` (vendor settings in |
| `exec_risk.rs` | 826 | Bash request-level execution risk: argv flags that spawn pro |
| `gate_preflight.rs` | 197 | Managed-policy preflight for one permission request. |
| `hub_permission.rs` | 497 | Tool-permission emit: when the rules engine returns "ask" fo |
| `manager.rs` | 8759 | Canonical `decision_reason` values for the uploaded artifact |
| `mod.rs` | 48 | Zero-init this module's metric families. See [`crate::init_m |
| `policy.rs` | 1488 | A security-gate escalation with `Ask` provenance. The bash-c |
| `prompter.rs` | 1681 | Stable option id for the edit prompt's "Yes, allow all edits |
| `resolution.rs` | 4392 | Permission resolution engine: merges native `.grok/config.to |
| `rules.rs` | 302 | Native permission rule-string DSL and permission-mode vocabu |
| `shell_access.rs` | 2917 | Detect file reads/writes inside a shell command so a managed |
| `state.rs` | 805 | Domains the user has approved for `web_fetch` |
| `types.rs` | 684 | A permission event capturing the decision made for a tool ca |

### `session/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `checkpoint.rs` | 1052 | Turn-boundary fan-out for the workspace. |
| `checkpoint_store.rs` | 725 | Disk-backed, co-located checkpoint store. |
| `file_state.rs` | 1813 | File state tracking for session rewind functionality. |
| `git.rs` | 4210 | Git operations: CLI for simple actions (stage, commit, push) |
| `jj.rs` | 227 | Jujutsu (jj) operations for colocated repos. |
| `mod.rs` | 1032 | Minimal result types for git error reporting (duplicated fro |
| `swap_policy.rs` | 779 | Toolset-swap guard policy: every trigger evaluates the one d |
| `tool_config.rs` | 1226 | Tool config resolution pipeline. |

### `upload/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `environment.rs` | 442 | Workspace environment capture — `workspace_environment.json` |
| `mod.rs` | 661 | `…_pending_bytes` is the series the mandatory queue-memory a |

### `util/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `mod.rs` | 32 | True if `e` reports that an advisory `flock` is held by anot |
| `ripgrep.rs` | 51 | — |

### `worktree/`

| 文件 | 行数 | 文档 |
| --- | ---: | --- |
| `mod.rs` | 3173 | Git worktree operations: create, list, remove, apply. |

---

## 6. 依赖与被依赖

依赖：`xai-grok-version` · `xai-grok-agent` · `xai-grok-tools` · `xai-grok-tools-api` · `xai-grok-workspace-client` · `xai-grok-workspace-types` · `xai-grok-config` · `xai-grok-config-types` · `xai-acp-lib` · `xai-codebase-graph` · `xai-grok-paths` · `xai-grok-env` · `xai-grok-sandbox` · `xai-grok-hooks` · `xai-hunk-tracker` · `xai-computer-hub-sdk` · `xai-computer-hub-mcp-adapter` · `xai-grok-mcp` · `xai-file-utils` · `xai-grok-auth` · `xai-grok-telemetry` · `xai-tty-utils` · `xai-sqlite-journal` · `xai-tool-protocol` · `xai-tool-runtime` · `xai-tool-types` · `xai-fast-worktree` · `xai-fsnotify` · `xai-tracing`

被依赖：`xai-grok-http` · `xai-grok-mcp` · `xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-pager-render` · `xai-grok-shell` · `xai-grok-shell-session-support` · `xai-grok-tools` · `xai-grok-workspace` · `xai-grok-workspace-client` · `xai-grok-workspace-types`

---

## 7. 阅读顺序

1. `lib.rs` 导出表
2. `workspace_ops.rs` — 所有 RPC 操作定义
3. `session/mod.rs` — 会话如何持有 ToolBridge
4. `permission/` + `handle.rs`
5. shell 中 `workspace_ops` 字段的初始化（`session_setup.rs`）

