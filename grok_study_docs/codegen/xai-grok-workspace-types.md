# xai-grok-workspace-types

> 路径：`crates/codegen/xai-grok-workspace-types`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Wire types for the xAI workspace API (request/chunk/event enums shared by client and server)

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `chunks` | Streaming response chunk types. |
| `error` | Workspace-wide error type. |
| `events` | Pub/sub event types and topic-filter sets. |
| `identity` | Identifier types for sessions, tool calls, and hunks. |
| `metadata` | Per-call metadata header map. |
| `request` | The wire-side request envelope. |
| `requests` | Wire-format request enums. |
| `rpc` | Canonical wire types for hub-proxied `workspace.*` RPC methods, |
| `types` | Supporting structs/enums referenced from requests, chunks, and events. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `error.rs` | 593 | Workspace-wide error type. | enum`WorkspaceError`, enum`IoKind` |
| `identity.rs` | 163 | Identifier types for sessions, tool calls, and hunks. | struct`SessionId`, struct`ToolCallId`, struct`HunkId` |
| `lib.rs` | 116 | Wire types for the `xai-grok-workspace` API. | — |
| `metadata.rs` | 243 | Per-call metadata header map. | struct`Metadata` |
| `request.rs` | 140 | The wire-side request envelope. | struct`RequestMessage` |

### `chunks/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 357 | Streaming response chunk types. | enum`ChunkKind` |
| `ops.rs` | 149 | Workspace-ops chunks (`OpsChunk`). | enum`OpsChunk` |
| `session.rs` | 76 | Session-lifecycle chunks (`SessionChunk`). | enum`SessionChunk` |
| `tool.rs` | 262 | Tool-stream chunks ([`ToolChunk`]) and the paired sampl | enum`ToolChunk`, enum`ToolResponse` |

### `events/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lag.rs` | 51 | Backpressure signal emitted by the event bus when a con | enum`EventLag` |
| `mod.rs` | 18 | Pub/sub event types and topic-filter sets. | — |
| `workspace.rs` | 343 | Workspace-scoped events (FS changes, server lifecycle,  | struct`WorkspaceTopicSet`, enum`WorkspaceEvent`, enum`WorkspaceTopic` |

### `requests/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 54 | Wire-format request enums. | enum`WorkspaceRequest` |
| `ops.rs` | 137 | Workspace-ops RPC requests. | enum`WorkspaceOpsRequest` |
| `session.rs` | 86 | Session-lifecycle RPC requests. | enum`SessionLifecycleRequest` |
| `tool.rs` | 59 | Tool RPC requests. | struct`ToolCallArgs`, enum`ToolRequest` |

### `rpc/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `agents_md.rs` | 59 | Discovery method (`workspace.discover_agents_md`). | struct`DiscoverAgentsMdReq`, struct`AgentConfigFile` |
| `code_nav.rs` | 125 | Codebase index / code navigation methods (`workspace.co | struct`CodeGotoDefinitionReq`, struct`CodeGotoReferencesReq`, struct`C |
| `deploy.rs` | 118 | App deployment workspace RPC methods. | enum`DeployError` |
| `envelope.rs` | 112 | Response envelope for `workspace.*` methods. Wire shape | struct`RpcError`, enum`RpcEnvelope` |
| `fs.rs` | 754 | File I/O methods: service-level `workspace.put_files` / | struct`PutFileEntry`, struct`PutFilesReq`, struct`PutFileResult`, stru |
| `git.rs` | 1077 | Git methods (`workspace.git_*`, `workspace.detect_vcs_k | struct`GitStatusReq`, struct`GitStatusExtReq`, struct`GitFilesReq`, st |
| `hooks.rs` | 230 | Hook registry method (`workspace.hook_registry`). | struct`HookRegistryReq`, struct`HookRegistryWire`, struct`HookSpecWire |
| `hunks.rs` | 413 | Hunk tracker methods (`workspace.hunk_*`). | struct`HunkActionReq`, struct`HunkSingleActionReq`, struct`HunkFileAct |
| `mod.rs` | 49 | Canonical wire types for hub-proxied `workspace.*` RPC  | trait`WorkspaceRpc` |
| `search.rs` | 226 | Search methods (`workspace.ripgrep`, `workspace.fuzzy_* | struct`ContentSearchRequest`, struct`ContentMatch`, struct`ContentMatc |
| `session.rs` | 89 | Session file-state / rewind methods (`workspace.begin_p | struct`BeginPromptReq`, struct`EndPromptReq`, struct`RewindToReq`, str |
| `skills.rs` | 275 | Discovery methods (`workspace.discover_skills`). | struct`DiscoverSkillsReq`, struct`DiscoverPluginsReq`, struct`SkillInf |
| `workspace.rs` | 324 | Environment/info/config/session-admin methods (`workspa | struct`WorkspaceInfoReq`, struct`LoadProjectConfigReq`, struct`LoadPer |
| `worktree.rs` | 406 | Worktree lifecycle methods (`workspace.create_worktree` | struct`DirtyStateSummary`, struct`CopiedChangesSummary`, struct`Create |

### `types/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `config.rs` | 133 | Configuration shapes referenced from session lifecycle  | struct`ToolServerConfig`, struct`AgentSessionConfig`, struct`ProjectCo |
| `files.rs` | 35 | `@file` provider shapes referenced from `OpsChunk::Reso | struct`FileReference`, struct`ResolvedFile` |
| `git.rs` | 123 | Minimal serializable git/VCS shapes referenced from `Wo | struct`GitStatusOpts`, struct`GitStatus`, struct`GitDiffArgs`, struct` |
| `hunk.rs` | 61 | Minimal serializable hunk shapes. | struct`Hunk`, enum`HunkAction` |
| `interaction.rs` | 145 | User-interaction shapes referenced from | struct`UserQuestion`, struct`UserQuestionOption`, enum`UserAnswer` |
| `memory.rs` | 26 | Memory subsystem shapes referenced from `OpsChunk::Memo | struct`MemoryChunk` |
| `mod.rs` | 48 | Supporting structs/enums referenced from requests, chun | — |
| `permission.rs` | 55 | Permission flow shapes used by | struct`PermissionRequest`, enum`PermissionDecision` |
| `plan_mode.rs` | 74 | Plan-mode transition shapes used by | enum`PlanModeTransition`, enum`PlanModeDecision` |
| `plugins.rs` | 54 | Discovery shapes for plugins and hooks surfaced by `Ops | struct`PluginInfo`, struct`HookInfo` |
| `search.rs` | 89 | Minimal serializable search-related shapes (ripgrep + f | struct`RipgrepArgs`, struct`ContentMatch`, struct`MatchSpan`, struct`R |
| `session.rs` | 120 | Session-related shapes referenced from `SessionChunk` a | struct`AgentSessionInfo`, struct`RewindResult`, struct`RewindPoint`, e |
| `skills.rs` | 27 | Discovery shapes for skills surfaced by `OpsChunk::Skil | struct`SkillInfo` |
| `tools.rs` | 178 | Tool-related shapes referenced from `ToolChunk`. | struct`ToolOutputChunk`, struct`ToolCallResult`, struct`ToolDef`, enum |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-grok-mcp` · `xai-grok-tools` · `xai-grok-workspace` · `xai-grok-workspace-client` · `xai-grok-workspace-types`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-workspace-types
cargo test -p xai-grok-workspace-types
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

