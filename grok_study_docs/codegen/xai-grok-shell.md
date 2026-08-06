# xai-grok-shell 完整代码导读

> `crates/codegen/xai-grok-shell` · **5000 行级**代码参考 · 覆盖模块、协议、磁盘布局、逐文件索引
> 配套：[09](../09_end_to_end_request_flow.md) · [10](../10_acp_protocol.md) · [12](../12_reading_order.md)

---

## 1. 系统架构

`xai-grok-shell` 是 Grok CLI **Agent 运行时**：ACP 服务端、多会话、LLM 循环、工具、MCP、认证、leader、持久化。

```text
pager-bin → pager → [ACP|leader] → MvpAgent → SessionActor → sampler / ToolBridge
```

### 1.1 线程模型

| 组件 | 模型 |
| --- | --- |
| MvpAgent | LocalSet + Rc<RefCell> |
| SessionActor | 专用线程或 LocalSet |
| SessionHandle | Clone + Send，仅 channel |
| ChatStateHandle | xai-chat-state Actor |
| LeaderServer | tokio 多客户端 |

### 1.2 不变量

1. `AgentRebuildSpec` 唯一调用 `AgentBuilder::new`
2. `initialize()` 单写 `auth_method_id`
3. Leader 下 per-session `model_id` / `yolo_mode`
4. `tool_use` 先于工具执行入历史
5. MCP 名 `server__tool`
6. Plan mode 门禁在 `tool_calls.rs`

---

## 2. lib.rs 顶层模块

| 模块 | 职责 |
| --- | --- |
| agent | MvpAgent、ACP server、models |
| session | SessionActor、持久化、workflow |
| auth | AuthManager、OAuth/OIDC |
| extensions | x.ai/* 扩展 |
| leader | Unix socket IPC |
| tools | ToolContext |
| util | config、grok_home |
| config | 热重载 watcher |
| sampling | 采样封装 |
| remote | 后端 HTTP |
| terminal | PTY/bash |
| upload | GCS trace |
| relay | WebSocket |
| plugin | 插件市场 |
| inspect | grok inspect |
| active_sessions | TUI 崩溃恢复 |
| cli_models | CLI 模型 |
| mcp_doctor | MCP 诊断 |
| trace_classifier | trace 分类 |

---

## 3. agent/

| 文件 | 说明 |
| --- | --- |
| `activity.rs` | Send-safe view of the agent's in-flight work, shared with the leader's |
| `app.rs` | Configuration for periodic auto-update checking in leader mode. |
| `auth_method.rs` | Shared, live handle to the agent's current ACP auth method id. |
| `chat_modes.rs` | grok.com chat-product model catalog: caches `/rest/modes` and maps modes to |
| `config.rs` | The mode in which the agent is running. |
| `config_model_override_parse.rs` | Resilient parsing for `[model.<id>]` TOML overrides. |
| `ext_parsers.rs` | Wire-shape parsers for ext-notification params handled by `MvpAgent`. |
| `feedback_client.rs` | REST client for feedback collection via cli-chat-proxy. |
| `folder_trust.rs` | Folder-trust gate ("do you trust this folder?"). |
| `handlers/mod.rs` | — |
| `handlers/model_switch.rs` | Applies a model switch to a session — the ungated path. `set_session_model` |
| `handlers/session.rs` | Session meta-information handlers. |
| `handlers/workspaces.rs` | — |
| `init.rs` | Agent bootstrap and lifecycle hooks. |
| `mod.rs` | — |
| `model_providers.rs` | Query parameters folded into every request URL; inherited by models. |
| `models.rs` | Model fetching, resolution, and management. |
| `mvp_agent/acp_agent.rs` | [`acp::Agent`] trait implementation for [`MvpAgent`]. |
| `mvp_agent/agent_ops.rs` | Inherent [`MvpAgent`] helpers (MCP/clients/gateway, settings/models, session ops, spawn). |
| `mvp_agent/code_nav.rs` | Code-navigation eligibility gating and codebase-index management for [`MvpAgent`]. |
| `mvp_agent/folder_trust_prompt.rs` | Interactive folder-trust prompt: a dormant agent→GUI-client ACP round-trip |
| `mvp_agent/heap_profile.rs` | Heap-profile monitor wiring for [`MvpAgent`]. |
| `mvp_agent/mod.rs` | A `'static` reference to a value on a single-threaded `LocalSet`. |
| `mvp_agent/session_lifecycle.rs` | Session lifecycle, roster deltas, and the idle-session supervisor for [`MvpAgent`]. |
| `mvp_agent/subagent_coordinator.rs` | Shell runner adapter and spawn-context construction for [`MvpAgent`]. |
| `mvp_agent/tests.rs` | Build an unsigned JWT with a `tier` claim (header.payload.sig base64url). |
| `proxy.rs` | HTTP CONNECT proxy support for WebSocket connections. |
| `relay.rs` | WebSocket relay connection management. |
| `restore_code.rs` | Thin wire-format adapter that wraps the shared |
| `roster.rs` | Roster types for the multi-client FleetView dashboard. |
| `server.rs` | WebSocket server for remote agent connections. |
| `session_config.rs` | The built-in session-picker modes used when the model has no server list. |
| `session_metrics.rs` | Session lifecycle event structs. |
| `session_registry_client.rs` | REST client for the session replicas registry (cli-chat-proxy). |
| `subagent/handle_request.rs` | Runtime adapter for one shell child. Shared lifecycle state is owned by the |
| `subagent/mod.rs` | Shell child runtime adapter and presentation. |
| `subscription_check.rs` | Subscription check for paywall gate lift. |
| `update_chunk_merge.rs` | Controls how sampling/output chunks are buffered before being delivered to the client. |

---

## 4. MvpAgent

- **结构**：`mvp_agent/mod.rs` — sessions、auth、models、gateway
- **ACP**：`acp_agent.rs` — initialize / new_session / load_session / prompt / ext_method

### 4.1 new_session 流程

1. 解析 session `_meta`（model、mcp、yolo、worktree）
2. folder_trust
3. 打开 PersistenceHandle，加载 chat_history
4. `spawn_session_actor`
5. 注册 SessionHandle，推送 session/update

### 4.2 prompt 流程

1. 解析 PromptRequest meta
2. prompt 队列 / send_now
3. `SessionCommand::Prompt` + oneshot
4. 等待 `PromptTurnResult` → PromptResponse

---

## 5. session/

### PromptOrigin

User · TaskCompleted · SubagentCompleted · NotificationDrain · SchedulerFired · PlanResume · GoalSummary · WorkflowCompleted

### 顶层文件

| 文件 | 说明 |
| --- | --- |
| `acp_conversion.rs` | ACP conversion functions for `xai-grok-tools`'s `ToolOutput`. |
| `acp_mcp.rs` | In-process SDK MCP servers over the ACP reverse channel (`x.ai/mcp/sdk_call`). |
| `acp_session/hooks.rs` | Client-registered hooks for [`SessionActor`]. |
| `acp_session.rs` | Session actor implementation for the MVP ACP agent. |
| `acp_types.rs` | Public wire types (DTOs) for the ACP session actor. |
| `agent_rebuild.rs` | `AgentRebuildSpec` — the canonical recipe for constructing an |
| `announcement_state.rs` | Persisted announcement tracking state for session resumption. |
| `chat_persistence.rs` | Production `ChatPersistence` implementation backed by the existing persistence channel. |
| `commands.rs` | Session actor command enum and associated public types. |
| `compaction.rs` | Compaction methods for `SessionActor`. |
| `compaction_config.rs` | Compaction configuration and runtime state for the session actor. |
| `compaction_segments.rs` | Shell-side dispatch on [`CompactionMode`]. Split into two methods so the |
| `events.rs` | Re-exports of the crate-internal event types that live in |
| `export.rs` | Session export for sharing via the remote session-sharing backend. |
| `feedback.rs` | Feedback request heuristics for Grok Code sessions. |
| `feedback_manager.rs` | Feedback manager for session-level feedback collection. |
| `file_system.rs` | Pagination offset applied after the dirs-first sort (default 0). |
| `fork.rs` | Session forking functionality |
| `fs_watch.rs` | Session-level fs-watch policy over [`xai_fsnotify`]. |
| `goal_classifier/evidence.rs` | Evidence-packet construction for the goal-verification stage. |
| `goal_classifier.rs` | Goal-verification stage (harness-owned). |
| `goal_evaluator.rs` | — |
| `goal_next_step.rs` | Helper that mines the "next concrete step" inlined into the goal |
| `goal_orchestrator.rs` | Goal mode support — notification helpers and state formatters. |
| `goal_planner.rs` | Goal planner subagent runner. Mirrors [`crate::session::goal_classifier`] |
| `goal_role_tools.rs` | Shared per-role prompt tool-name rendering for the `/goal` harness. |
| `goal_stop_detector.rs` | Heuristic stop-detector for premature "give up" turn endings. |
| `goal_strategist.rs` | Stall-triggered goal strategist subagent runner. |
| `goal_summarizer.rs` | Achievement-triggered goal summarizer subagent runner. |
| `goal_tracker.rs` | Goal mode state machine. |
| `handle.rs` | `SessionHandle` — the `Clone + Send` proxy for interacting with a session actor. |
| `helpers/chat.rs` | Returns the largest valid UTF-8 character boundary index at or before `index`. |
| `helpers/compaction_context.rs` | Rendering helpers for [`CompactionStateContext`] that depend on |
| `helpers/full_replace_compaction.rs` | grok-build's L5 wiring onto the shared full-replace engine |
| `helpers/memory_context.rs` | Format memory search results as `<system-reminder>` content. |
| `helpers/memory_flush.rs` | Pre-compaction memory flush logic. |
| `helpers/mod.rs` | — |
| `helpers/prompt_suggest.rs` | Next-prompt prediction helpers (tab autocomplete ghost text). |
| `helpers/replay.rs` | Replay pipeline for cross-compaction rewind. |
| `helpers/session_compact.rs` | Compacts the current conversation and generates a summary of the conversation which |
| `helpers/session_recap.rs` | Session recap generation helpers. |
| `helpers/session_summary.rs` | Session title generation via LLM tool call. |
| `helpers/tool_input_parsing.rs` | Normalize empty tool call arguments to `"{}"`. |
| `image_describe.rs` | Image processing helpers for sessions with image inputs. |
| `image_normalize.rs` | Re-encode decoded attachments that exceed [`MAX_IMAGE_BYTES`], |
| `inference_metrics.rs` | Per-response inference latency metrics. |
| `managed_mcp.rs` | Shell-side managed MCP: merges MCP server sources, then injects managed |
| `mcp_descriptors.rs` | MCP descriptor mirror. |
| `mcp_dispatcher.rs` | Session-actor side `StatusDispatcher` for MCP client events. |
| `mcp_restart.rs` | Bounded stdio MCP auto-restart. |
| `mcp_servers.rs` | MCP server re-exports + shell-side wrappers for timeout override resolution. |
| `memory/hooks.rs` | Session lifecycle hooks for the memory system. |
| `memory/mod.rs` | Memory system shim. |
| `memory_state.rs` | `SessionMemory` — memory subsystem state for the session actor. |
| `merge.rs` | Merged session listing — combines local and remote session data. |
| `mod.rs` | `false` twin: this template is not compiled into this build, so no |
| `normalize_cache.rs` | Process-wide cache for normalized images: `moka::future::Cache` |
| `notifications.rs` | `NotificationSender` — transport layer for session notifications. |
| `pending_interaction.rs` | Per-session pending-interaction registry. |
| `persistence.rs` | Current chat history format version. |
| `plan_mode.rs` | Plan mode state machine and prompt text generation. |
| `prompt_history.rs` | Per-working-directory prompt history for fast reverse search. |
| `prompt_parser.rs` | Parsed prompt with context and query kept separate. |
| `prompt_queue.rs` | Server-authoritative prompt queue wire types. |
| `prompt_timing.rs` | Per-turn prompt latency measurement. |
| `replay_events.rs` | Notification destined for the high-frequency event ReplayBuffer). |
| `repo_changes/mod.rs` | Serialize local repository changes (local commits + uncommitted worktree/index changes). |
| `restore_stub.rs` | — |
| `result.rs` | Extension method result: `{ result: T | null, error?: string | ExtMethodError }` |
| `signals.rs` | Session signals tracking for feedback heuristics. |
| `slash_commands.rs` | ACP slash command advertising and resolution. |
| `storage/jsonl/mod.rs` | JSONL storage under `{root}/sessions/{url_encoded_cwd}/{session_id}/`. |
| `storage/jsonl/tests.rs` | Resume from updates.jsonl alone: when chat_history.jsonl is missing, load |
| `storage/mod.rs` | On-disk file names, relative to a session directory. Single source of truth for |
| `storage/relocation/fs.rs` | Durable filesystem operations used by session relocation. |
| `storage/relocation/journal.rs` | Relocation journal types, validation, lease, and commit-aware persistence. |
| `storage/relocation/mod.rs` | Durable, source-retaining relocation of a dormant session directory. |
| `storage/relocation/tests.rs` | — |
| `storage/relocation/view.rs` | Recovery-aware point-in-time view of local session storage. |
| `storage/search.rs` | Session search orchestration: querying and background indexing. |
| `storage/search_fts.rs` | SQLite-backed FTS5 index for session search. |
| `storage/search_remote_sync.rs` | GCS-based remote index sync for session search. |
| `storage/summary_write.rs` | Concurrency-safe, field-correct writes to a session's `summary.json`. |
| `streaming_capture.rs` | Out-of-band per-turn capture of the model's streamed reasoning + text. |
| `summary.rs` | Session summary (title) generation lifecycle. |
| `telemetry.rs` | Permission-mode label for the `session.permission_mode_changed` span. |
| `tool_index.rs` | Concrete `ToolSearchIndex` implementation using BM25. |
| `turn_completion.rs` | Pure construction of the durable, replayable turn-completion terminal. |
| `two_pass.rs` | Pure builders for prefire two-pass compaction (shell-only). |
| `unified_list/cursor.rs` | — |
| `unified_list/envelope.rs` | — |
| `unified_list/facets.rs` | — |
| `unified_list/mod.rs` | Hard-off in release builds so they can't enable the |
| `unified_list/row.rs` | — |
| `user_message.rs` | Wraps the user query properly |
| `wire_tags.rs` | Single source of truth for the `sessionUpdate` discriminant strings the |
| `workflow/host_service.rs` | — |
| `workflow/manager.rs` | — |
| `workflow/mod.rs` | — |
| `workflow/notify.rs` | — |
| `workflow/registry.rs` | — |
| `workflow/schema_contract.rs` | — |
| `workflow/store.rs` | — |
| `workflow/tracker.rs` | — |
| `worktree.rs` | Git worktree operations: create, list, remove, apply. |
| `worktree_pool.rs` | Bounded worktree pool for fast fork setup. |

---

## 6. SessionCommand 全集（90 个变体）

`session/commands.rs` → `run_loop.rs` 分发。

| 变体 | 备注 |
| --- | --- |
| `Cancelled` | |
| `MaxTurnsReached` | |
| `MonitorEvent` | |
| `MonitorCompleted` | |
| `BashTaskCompleted` | |
| `Initialize` | |
| `ReplaceSystemPrompt` | |
| `GetToolOverrides` | |
| `SetToolOverrides` | |
| `Prompt` | |
| `SessionMode` | |
| `SetSessionModel` | |
| `RebuildAgentForDefinition` | |
| `OverrideModelName` | |
| `GetCurrentModel` | |
| `GetCurrentPromptMode` | |
| `GetModelMetadata` | |
| `GetSessionInfo` | |
| `CompactSession` | |
| `ReloadPlugins` | |
| `FlushMemory` | |
| `SetYoloMode` | |
| `SetAutoMode` | |
| `Rewind` | |
| `RepairHistory` | |
| `GetRewindPoints` | |
| `GetRewindFileCounts` | |
| `ReconcileRewindTracker` | |
| `XaiSessionNotification` | |
| `RecordSubagentUsage` | |
| `MarkSubagentUsageNotApplied` | |
| `ErrorPathUsageFallback` | |
| `SetNextTraceTurn` | |
| `CopyFile` | |
| `FlushComplete` | |
| `UpdateMcpServers` | |
| `ToggleMcpServer` | |
| `ToggleMcpTool` | |
| `GetMcpStatus` | |
| `GetManagedGatewayDisabledTools` | |
| `SnapshotMcpPool` | |
| `SnapshotClientHooks` | |
| `SnapshotToolDefinitions` | |
| `SetClientHooks` | |
| `CallMcpTool` | |
| `ReadMcpResource` | |
| `McpAuthStatus` | |
| `McpAuthTrigger` | |
| `RetryAuthRequiredServers` | |
| `BackgroundForegroundCommand` | |
| `KillBackgroundTask` | |
| `DeleteScheduledTask` | |
| `ListTasks` | |
| `IsBusy` | |
| `GetHooksList` | |
| `HooksAction` | |
| `NotifyPluginUpdates` | |
| `PluginsAction` | |
| `PluginsList` | |
| `InjectNotification` | |
| `DropMonitorNotifications` | |
| `DispatchNotificationHook` | |
| `RecordGoalTurnTaskIds` | |
| `RemoveQueuedPrompt` | |
| `ReorderQueue` | |
| `ClearQueue` | |
| `EditQueuedPrompt` | |
| `HoldCombineEdit` | |
| `ReleaseCombineEdit` | |
| `InterjectQueuedPrompt` | |
| `Cancel` | |
| `TriggerTestFeedback` | |
| `PersistFeedback` | |
| `GetWorkflowCatalogState` | |
| `ListAvailableCommands` | |
| `DispatchSessionStartHook` | |
| `GetFeedbackContext` | |
| `GetActiveAgent` | |
| `SideQuestion` | |
| `Recap` | |
| `AISuggest` | |
| `SuggestPrompt` | |
| `RewriteMemoryNote` | |
| `Interject` | |
| `GoalSummaryTurn` | |
| `WorkflowCompletionTurn` | |
| `TakeTurnMessages` | |
| `TakeHarnessTraceTurns` | |
| `TakeStreamingCapture` | |
| `PersistGitHead` | |

---

## 7. Turn 执行链

### handle_prompt (`turn.rs`)

PromptOrigin → on_turn_start → bash 短路 → slash_commands → ensure_prefix_ready → parse_prompt → push history → process_conversation_turn

### process_conversation_turn loop

stationarity → drain interject/skill/monitor → memory → auto-compact → prepare_tool_definitions → build_request → sampler → execute_tool_calls → stop_reason

### execute_tool_calls

prepare_tool_call → plan gate → pre_hook → permission → dispatch_tool → auth retry → push_tool_result → post_hook → UI notify

---

## 8. acp_session_impl/

| 文件 | 说明 |
| --- | --- |
| `extensions.rs` | Composition root for the session's extensions: each lives in its own submodule and installs itself here. |
| `goal.rs` | Goal-orchestration concern for `SessionActor`. |
| `goal_support.rs` | Goal-harness support for `SessionActor`: reminder/directive templates, |
| `hook_dispatch.rs` | Hook dispatch concern for `SessionActor`: run contexts, hook execution |
| `hooks_plugins.rs` | Trust the current project via the unified folder-trust store. Now an |
| `interjection.rs` | Mid-turn interjection concern for `SessionActor` (buffer type, formatting, |
| `laziness.rs` | Laziness / stop-detector concern for `SessionActor`. |
| `laziness_classifier.rs` | Layer-3 LazinessDetector pure helpers: classifier prompt/config consts, |
| `mcp.rs` | Wait for MCP tools to be initialized. |
| `mcp_snapshot.rs` | MCP snapshot concern for `SessionActor`: server-snapshot refresh and |
| `memory_dream.rs` | Memory concern for `SessionActor`: memory flush, the dream pipeline, |
| `model_switch.rs` | Handle [`SessionCommand::RebuildAgentForDefinition`]. |
| `notification_drain.rs` | Idle-gated pending-notification buffering and drain for `SessionActor`, |
| `prompt_build.rs` | User-message construction concern for `SessionActor`: templated prefix |
| `prompt_queue.rs` | Running-turn display fields for `x.ai/queue/changed` (clients paint turn-start UI). |
| `recap.rs` | Auxiliary model-call concern for `SessionActor`: side questions, recap |
| `reminders.rs` | System-reminder injection concern for `SessionActor`: reminder policy, |
| `rewind.rs` | Rewind concern for `SessionActor`: rewind points, cross-compaction |
| `run_loop.rs` | The session actor's main loop (`run_session`): command dispatch, idle |
| `sampler_turn.rs` | Sampler-turn pipeline for `SessionActor`: tool definitions, model auth |
| `session_mode.rs` | Session/plan-mode concern for `SessionActor` (`handle_session_mode`, |
| `session_setup.rs` | Session initialization concern for `SessionActor`: `initialize`, prefix |
| `slash_exec.rs` | Execute a built-in slash command (e.g. `/compact`, `/yolo`). |
| `spawn.rs` | Session bring-up concern for `acp_session`: `spawn_session_actor`, the |
| `stop_gate.rs` | The turn-end `Stop`/`SubagentStop` gate for `SessionActor`. |
| `tasks_cancel.rs` | Prompt-task plumbing for `SessionActor` (`AgentTask`, `TaskSlot`, |
| `tool_calls.rs` | Tool-call execution concern for `SessionActor`: the model-output → |
| `tool_dispatch.rs` | Tool dispatch helpers for `SessionActor`: `dispatch_tool` and its lock / |
| `turn.rs` | Turn-execution concern for `SessionActor` (`handle_prompt`, turn-end, |
| `turn_end.rs` | Turn-completion concern for `SessionActor`: completion handling |
| `types.rs` | Top-level enum definitions for `acp_session`; their `impl` blocks and |
| `updates.rs` | Outbound update emission concern for `SessionActor`: `send_update` and |
| `workflow.rs` | — |

---

## 9. extensions & x.ai 路由

`acp_agent.rs::ext_method`

| 路由 | 模块 |
| --- | --- |
| `x.ai/getApiKey|setApiKey` | `auth` |
| `x.ai/session/*` | `handlers/session` |
| `x.ai/sessions/list` | `roster` |
| `x.ai/mcp/*` | `mcp` |
| `x.ai/interject` | `interject` |
| `x.ai/memory/*` | `memory` |
| `x.ai/git/*` | `git/jj` |
| `x.ai/git/worktree/*` | `worktree` |
| `x.ai/terminal/*` | `terminal` |
| `x.ai/task/*|scheduler/*|subagent/*` | `task` |
| `x.ai/skills/*` | `skills` |
| `x.ai/workflows/list` | `skills` |
| `x.ai/debug/*` | `debug` |
| `x.ai/rewind*` | `rewind` |
| `x.ai/hooks/*` | `hooks` |
| `x.ai/plugins/*` | `plugins` |
| `x.ai/marketplace/*` | `marketplace` |
| `x.ai/search/*` | `search` |
| `x.ai/code/*` | `code_nav` |
| `x.ai/internal/reload_*` | `session_admin` |
| `x.ai/cloud/*` | `sandbox inline` |
| `x.ai/billing|auto-topup` | `billing` |
| `x.ai/share_session` | `share` |
| `x.ai/recap` | `recap` |
| `x.ai/feedback|btw|review*` | `feedback` |
| `x.ai/suggest*` | `suggest` |
| `x.ai/auth/*` | `auth` |

### extensions 文件

- `auth.rs` — `x.ai/auth/*` and legacy `x.ai/{get,set}ApiKey` extension handlers.
- `auth_gate.rs` — Require xAI auth from a sync context, accepting tokens in the client-side buffer window.
- `billing.rs` — `x.ai/billing` extension handler.
- `bundle.rs` — ACP extension handlers for bundled subagent cache sync and status.
- `chat_conversation_history.rs` — `x.ai/session/load_history`: fetch one older page of a gateway-backed
- `code_nav.rs` — Code Navigation Extension Methods
- `debug.rs` — `x.ai/debug/*` extension handlers for local client testing.
- `feedback.rs` — `x.ai/feedback`, `x.ai/feedback/dismiss`, `x.ai/btw`, and `x.ai/review/*`
- `fs.rs` — Filesystem extension API layer.
- `git.rs` — Git extension API layer.
- `hooks.rs` — `x.ai/hooks/*` extension handlers.
- `hunk_tracker.rs` — Hunk Tracker extension API layer.
- `interject.rs` — `x.ai/interject` extension handler.
- `jj.rs` — Jujutsu extension handlers — delegates to [`xai_grok_workspace::session::jj`].
- `marketplace.rs` — `x.ai/marketplace/*` extension handlers.
- `mcp.rs` — MCP extension methods and business logic.
- `memory.rs` — `x.ai/memory/flush`, `x.ai/memory/rewrite`, and `x.ai/compact_conversation`
- `mod.rs` — Deserialize ACP params from their raw JSON string, mapping a parse failure
- `notification.rs` — Retained for wire backwards compatibility; always empty in the
- `plugins.rs` — `x.ai/plugins/*` extension handlers.
- `pr.rs` — `gh pr view --json` does not expose `isInMergeQueue`; query GraphQL via `gh api`.
- `privacy.rs` — `x.ai/privacy/setCodingDataRetention` extension handler.
- `prompt_history.rs` — `x.ai/prompt_history` extension handler.
- `prompt_meta.rs` — Typed metadata for a prompt `TextContent._meta` field.
- `recap.rs` — `x.ai/recap` extension handler.
- `repair.rs` — `x.ai/session/repair` — out-of-band recovery for sessions bricked by
- `rewind.rs` — `x.ai/rewind/*` extension handlers.
- `rollout.rs` — `x.ai/rollout/survey` extension handler.
- `routing.rs` — Metadata from the request, used for routing notifications back to the
- `search.rs` — Search extension API layer (fuzzy file search, content search).
- `session_admin.rs` — Session-administration extension handlers.
- `session_search.rs` — ACP extension handler for session search (`x.ai/session/search`).
- `session_state.rs` — `x.ai/session/state` reads a session's metadata columns; `x.ai/session/import`
- `session_updates.rs` — ACP extension handler for bulk session updates (`x.ai/session/updates`).
- `share.rs` — `x.ai/share_session` extension handler.
- `skills.rs` — Generic params for methods that only need an optional `cwd`.
- `suggest/ai_provider.rs` — Request AI-powered shell command suggestions via the session actor.
- `suggest/file_provider.rs` — Filesystem completion for the shell token under the cursor: any
- `suggest/history_provider.rs` — Rank history matches from three tiers of history sources.
- `suggest/mod.rs` — Deterministic Tab mode: run only the token providers (path/file).
- `suggest/path_provider.rs` — The command token being typed, via the canonical tokenizer: quotes hide
- `suggest/shell_token.rs` — Minimal shell-token syntax for completion: find the token under the
- `task.rs` — Wire DTO for the `x.ai/task/kill` ext request.
- `terminal.rs` — Response for any terminal creation — piped or PTY. Both return just a `terminalId`.
- `usage.rs` — `x.ai/session/usage` — cumulative session token/cost as [`PromptUsage`].
- `worktree.rs` — Handler for x.ai/git/worktree/* extension methods.

---

## 10. auth/

### AuthManager

`auth()` · `configure_refresher` · `try_recover_unauthorized` · `refresh_notifier`

#### `attribution.rs`

Shell-side 401-attribution helpers.

API: fn`test_emit_count`, fn`reset_test_emit_count`, fn`record_consumer_401`, fn`record_auth_401`, struct`ShellAttribution`, enum`ConsumerKind`

#### `auth_provider.rs`

Model auth providers (`[auth_provider.<name>]`).

API: fn`test_backdate_provider_mint`, fn`test_counting_provider`, struct`AuthProviderConfig`, struct`AuthProviderRef`, enum`ProviderRefreshOutcome`

#### `config.rs`

Default scopes for the xAI OAuth2 provider. Includes `grok-cli:access`

API: fn`allowed_accounts_app_origins`, fn`accounts_app_cors_layer`, fn`use_local_auth`, fn`xai_oauth2_issuer`, fn`is_xai_oauth2_issuer`, struct`GrokComConfig`

#### `credential_provider.rs`

`api_key.id` for the active credential: hash the stable API key, never the

API: fn`embedding_session_credentials`, fn`build_storage_client_for_proxy`, fn`wire_otel_auth_manager`, fn`sync_external_otel_identity`, fn`wire_otel_deployment_key`, fn`build_default_otel_layer_config`

#### `devbox_login_stub.rs`

Stub for builds without the devbox auth feature.

API: fn`is_devbox_environment`, fn`mint_devbox_auth`, fn`run_devbox_login`

#### `device_code.rs`

RFC 8628 Device Authorization Grant -- CLI side.

API: fn`request_device_code`, fn`complete_device_code_login`, fn`run_device_code_login_channels`, struct`DeviceCode`, enum`DeviceCodeError`, enum`ClientSurface`

#### `error.rs`

Token expired and no refresh authority available.

API: struct`RefreshTransientError`, struct`RefreshTokenFailedError`, enum`AuthError`, enum`RefreshTokenError`, enum`RefreshTokenFailedReason`

#### `external_auth.rs`

Parse stdout into a session-credential `GrokAuth`.

API: fn`parse_output`, fn`run_external_refresh`, fn`refresh_with_command`

#### `flow.rs`

Reject a cached credential for reuse if it lacks `oidc_issuer`, has a

API: fn`run_auth_flow_with_stderr_bridge`, fn`run_auth_flow`, fn`run_auth_flow_interactive`, fn`try_ensure_fresh_auth`, fn`try_ensure_session_noninteractive`, fn`ensure_authenticated`

#### `jwt.rs`

JWT expiration detection. Returns `None`/`false` for non-JWT tokens.

API: fn`parse_jwt_expiration`, fn`is_jwt_expired_or_near`

#### `manager/enrichment.rs`

Background `/user` enrichment spawned by `AuthManager::update()`.



#### `manager/lock.rs`

Advisory `auth.json.lock` helpers (free functions, no `AuthManager`

API: fn`try_lock_auth_file_nonblocking`, fn`try_lock_auth_file_async`

#### `manager/sleep_gate.rs`

System-sleep refresh-straddle mitigation for [`AuthManager`].



#### `manager.rs`

`AuthManager` -- single source of truth for `auth.json` + the

API: fn`compute_proactive_sleep`, fn`shared_api_key_provider`, struct`AuthManager`, struct`SharedAuthKeyProvider`, enum`RefreshReason`, enum`DiskAuthState`

#### `meta.rs`

Access gate from `grok_build_access_gate`.

API: struct`GateInfo`, struct`AuthMeta`

#### `mod.rs`

—



#### `model.rs`

Legacy auth.json scope key. Fallback for old devbox auth files.

API: fn`default_coding_data_retention_opt_out`, fn`token_suffix`, fn`lookup_auth`, fn`is_expired`, fn`is_expired_with_buffer`, struct`GrokAuth`

#### `oidc/login.rs`

Interactive login orchestration: callback HTTP server, browser

API: fn`callback_page`, fn`run_login_flow`, fn`run_login_flow_with_config`

#### `oidc/mod.rs`

OIDC authentication: protocol, login, and refresh submodules.



#### `oidc/protocol.rs`

Pure OIDC protocol mechanics: PKCE, discovery, token exchange,

API: fn`with_alpha_test_key`, fn`is_configured`, fn`peek_access_token_principal`, fn`peek_access_token_principal_id`, fn`resolve_login_principal_policy`, fn`login_principal_policy`

#### `oidc/refresh.rs`

Pure-data OIDC refresh. Talks to the IdP and returns

API: fn`oidc_token_exchange`, enum`OidcRefreshResult`

#### `oidc/test_helpers.rs`

Shared test helpers for `oidc::protocol::tests` and `oidc::login::tests`.



#### `recovery.rs`

Unauthorized (401) recovery state machine.

API: fn`manual_auth_reason`, fn`relay_should_cancel`, struct`RejectedAuth`, struct`ManualAuthTracker`, struct`UnauthorizedRecovery`, enum`RecoverySource`

#### `refresh/external_refresher.rs`

Refreshes by re-running the operator's external auth binary via the async

API: struct`ExternalBinaryRefresher`

#### `refresh/mod.rs`

Callback for diagnostic log upload on auth refresh failure.

API: fn`resolve_refresh_credential`, fn`build_refresher`, enum`RefreshOutcome`

#### `refresh/oidc_refresher.rs`

Escalate to `PermanentFailure` after this many consecutive transient

API: struct`OidcRefresher`

#### `single_flight.rs`

Single-flight guard for interactive login.

API: struct`AttemptChannels`, struct`AuthSingleFlight`, struct`AuthAttemptGuard`, enum`SubmitCodeError`

#### `storage.rs`

RAII guard for an exclusive advisory lock on `auth.json.lock`.

API: fn`read_auth_json`, fn`read_auth_json_or_empty`, fn`backup_corrupt_auth_file`, fn`read_auth_json_or_empty_recovering_corrupt`, fn`read_token_by_scope`, fn`read_api_key`

#### `token_output.rs`

Shared parser for an auth command's stdout.

API: fn`expiry_after_seconds`, fn`parse_token_output`, struct`ExternalAuthOutput`, struct`ParsedTokenOutput`

#### `token_type.rs`

What kind of bearer is loaded right now. Dispatch key for

API: enum`TokenType`

---

## 11. leader/

`connect_or_spawn` → `~/.grok/leader.sock` → 单 MvpAgent · `LeaderLock` 防双 leader · `ShutdownReason::AutoUpdate`

| 文件 | 说明 |
| --- | --- |
| mod.rs | connect_or_spawn |
| client.rs | LeaderClient |
| server.rs | run_leader_server |
| lock.rs | flock 路径 |
| protocol.rs | 控制协议 |
| transport.rs | Unix listener |

---

## 12. 持久化 ~/.grok

**全局**：auth.json · config.toml · models_cache.json · leader.sock · sessions/ · logs/unified.jsonl · worktrees.db · skills/

**单会话** `sessions/{encoded_cwd}/{id}/`：summary.json · chat_history.jsonl · updates.jsonl · signals.json · plan.json · plan_mode.json · goal/state.json · rewind_points.jsonl · feedback.jsonl · subagents/

**项目** `.grok/config.toml` · hooks · skills · plan.md

---

## 13. 配置与环境变量

GROK_HOME · GROK_AUTH · GROK_AUTH_PATH · XAI_API_KEY · GROK_CLI_CHAT_PROXY_BASE_URL · GROK_OIDC_* · GROK_MEMORY · GROK_LEADER_LOG · GROK_DEBUG_LOG

config.toml: [cli] [mcp_servers] [memory] [permission] [plugins] [session_search] [model.<id>]

---

## 14. 其他模块

### terminal/ (10 files)
- `acp_terminal.rs` (118L)
- `adapter.rs` (692L)
- `background_task.rs` (772L)
- `exit_watcher.rs` (255L)
- `local_terminal.rs` (189L)
- `mod.rs` (233L)
- `output_recorder.rs` (236L)
- `pty_session.rs` (754L)
- `runner.rs` (44L)
- `streaming_local_terminal.rs` (1550L)

### remote/ (9 files)
- `agent.rs` (327L)
- `chat_models_client.rs` (206L)
- `client.rs` (2115L)
- `conversations_client.rs` (309L)
- `mod.rs` (37L)
- `pull.rs` (458L)
- `pull_smoke_test.rs` (127L)
- `sync.rs` (182L)
- `workspaces_client.rs` (180L)

### upload/ (5 files)
- `gcs.rs` (160L)
- `manifest.rs` (433L)
- `mod.rs` (4L)
- `trace.rs` (2514L)
- `turn.rs` (509L)

### tools/ (7 files)
- `bridge.rs` (6L)
- `config.rs` (747L)
- `mod.rs` (22L)
- `notification_bridge.rs` (2327L)
- `retry.rs` (26L)
- `todo.rs` (81L)
- `tool_context.rs` (420L)

### util/ (28 files)
- `config/announcements.rs` (93L)
- `config/campaigns.rs` (745L)
- `config/hints.rs` (392L)
- `config/load.rs` (296L)
- `config/mcp.rs` (2048L)
- `config/mod.rs` (34L)
- `config/permissions.rs` (748L)
- `config/persist.rs` (1392L)
- `config/resolve/auto_mode.rs` (571L)
- `config/resolve/compaction.rs` (219L)
- `config/resolve/crash_handler.rs` (230L)
- `config/resolve/display_refresh.rs` (654L)
- `config/resolve/features.rs` (262L)
- `config/resolve/mcp.rs` (466L)
- `config/resolve/mod.rs` (29L)
- `config/resolve/system_prompt.rs` (166L)
- `config/resolve/tool_approvals.rs` (192L)
- `config/resolve/toolset.rs` (765L)
- `config/resolve/ui.rs` (442L)
- `config/resolve/version.rs` (512L)
- `config/settings_writes.rs` (310L)
- `config/tips.rs` (398L)
- `config/worktree.rs` (802L)
- `grok_auth_credentials.rs` (189L)
- `hooks.rs` (116L)
- …

### sampling/ (4 files)
- `conversation.rs` (40L)
- `error.rs` (774L)
- `mod.rs` (32L)
- `types.rs` (64L)

### relay/ (3 files)
- `mod.rs` (17L)
- `sync.rs` (1141L)
- `types.rs` (51L)

### inspect/ (2 files)
- `compat.rs` (305L)
- `mod.rs` (2012L)

### config/ (4 files)
- `mod.rs` (1878L)
- `reloader.rs` (1055L)
- `tests.rs` (3617L)
- `watcher.rs` (1302L)

### trace_classifier/ (1 files)
- `mod.rs` (3063L)

### heap_profile/ (2 files)
- `mod.rs` (211L)
- `monitor.rs` (1125L)

---

## 15. 依赖

xai-acp-lib · xai-grok-agent · xai-chat-state · xai-grok-sampler · xai-grok-tools · xai-grok-workspace · xai-grok-mcp · xai-grok-hooks · xai-grok-compaction · xai-grok-memory · agent-client-protocol · rusqlite · git2 · axum · tokio

## 16. 测试

```sh
cargo check -p xai-grok-shell
cargo test -p xai-grok-shell --lib
cargo test -p xai-grok-shell session::acp_session_tests
```

## 17. FAQ

| 问题 | 入口 |
| --- | --- |
| 新扩展 | extensions + ext_method |
| 新 SessionCommand | commands + run_loop |
| 改 prompt | session_setup + xai-grok-agent |
| 首 prompt 慢 | MCP Blocking |
| 401 | AuthManager + SessionTokenAuthGate |
| Leader 串扰 | SessionHandle per-session 字段 |

---

## 18. 全文件紧凑索引

### `active_sessions.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `active_sessions.rs` | 282 | Tracks open TUI sessions in `~/.grok/active_sessions.json` for crash |

### `agent/` (38 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `agent/activity.rs` | 411 | Send-safe view of the agent's in-flight work, shared with the leader's |
| `agent/app.rs` | 2343 | Configuration for periodic auto-update checking in leader mode. |
| `agent/auth_method.rs` | 1105 | Shared, live handle to the agent's current ACP auth method id. |
| `agent/chat_modes.rs` | 323 | grok.com chat-product model catalog: caches `/rest/modes` and maps modes to |
| `agent/config.rs` | 12525 | The mode in which the agent is running. |
| `agent/config_model_override_parse.rs` | 870 | Resilient parsing for `[model.<id>]` TOML overrides. |
| `agent/ext_parsers.rs` | 267 | Wire-shape parsers for ext-notification params handled by `MvpAgent`. |
| `agent/feedback_client.rs` | 1355 | REST client for feedback collection via cli-chat-proxy. |
| `agent/folder_trust.rs` | 1674 | Folder-trust gate ("do you trust this folder?"). |
| `agent/handlers/mod.rs` | 3 | — |
| `agent/handlers/model_switch.rs` | 281 | Applies a model switch to a session — the ungated path. `set_session_model` |
| `agent/handlers/session.rs` | 291 | Session meta-information handlers. |
| `agent/handlers/workspaces.rs` | 174 | — |
| `agent/init.rs` | 200 | Agent bootstrap and lifecycle hooks. |
| `agent/mod.rs` | 32 | — |
| `agent/model_providers.rs` | 1010 | Query parameters folded into every request URL; inherited by models. |
| `agent/models.rs` | 3614 | Model fetching, resolution, and management. |
| `agent/mvp_agent/acp_agent.rs` | 4113 | [`acp::Agent`] trait implementation for [`MvpAgent`]. |
| `agent/mvp_agent/agent_ops.rs` | 3827 | Inherent [`MvpAgent`] helpers (MCP/clients/gateway, settings/models, session ops |
| `agent/mvp_agent/code_nav.rs` | 261 | Code-navigation eligibility gating and codebase-index management for [`MvpAgent` |
| `agent/mvp_agent/folder_trust_prompt.rs` | 453 | Interactive folder-trust prompt: a dormant agent→GUI-client ACP round-trip |
| `agent/mvp_agent/heap_profile.rs` | 352 | Heap-profile monitor wiring for [`MvpAgent`]. |
| `agent/mvp_agent/mod.rs` | 2668 | A `'static` reference to a value on a single-threaded `LocalSet`. |
| `agent/mvp_agent/session_lifecycle.rs` | 457 | Session lifecycle, roster deltas, and the idle-session supervisor for [`MvpAgent |
| `agent/mvp_agent/subagent_coordinator.rs` | 560 | Shell runner adapter and spawn-context construction for [`MvpAgent`]. |
| `agent/mvp_agent/tests.rs` | 4845 | Build an unsigned JWT with a `tier` claim (header.payload.sig base64url). |
| `agent/proxy.rs` | 619 | HTTP CONNECT proxy support for WebSocket connections. |
| `agent/relay.rs` | 1182 | WebSocket relay connection management. |
| `agent/restore_code.rs` | 54 | Thin wire-format adapter that wraps the shared |
| `agent/roster.rs` | 311 | Roster types for the multi-client FleetView dashboard. |
| `agent/server.rs` | 487 | WebSocket server for remote agent connections. |
| `agent/session_config.rs` | 219 | The built-in session-picker modes used when the model has no server list. |
| `agent/session_metrics.rs` | 10 | Session lifecycle event structs. |
| `agent/session_registry_client.rs` | 642 | REST client for the session replicas registry (cli-chat-proxy). |
| `agent/subagent/handle_request.rs` | 1880 | Runtime adapter for one shell child. Shared lifecycle state is owned by the |
| `agent/subagent/mod.rs` | 2616 | Shell child runtime adapter and presentation. |
| `agent/subscription_check.rs` | 195 | Subscription check for paywall gate lift. |
| `agent/update_chunk_merge.rs` | 1070 | Controls how sampling/output chunks are buffered before being delivered to the c |

### `auth/` (30 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `auth/attribution.rs` | 954 | Shell-side 401-attribution helpers. |
| `auth/auth_provider.rs` | 660 | Model auth providers (`[auth_provider.<name>]`). |
| `auth/config.rs` | 430 | Default scopes for the xAI OAuth2 provider. Includes `grok-cli:access` |
| `auth/credential_provider.rs` | 884 | `api_key.id` for the active credential: hash the stable API key, never the |
| `auth/devbox_login_stub.rs` | 37 | Stub for builds without the devbox auth feature. |
| `auth/device_code.rs` | 890 | RFC 8628 Device Authorization Grant -- CLI side. |
| `auth/error.rs` | 144 | Token expired and no refresh authority available. |
| `auth/external_auth.rs` | 222 | Parse stdout into a session-credential `GrokAuth`. |
| `auth/flow.rs` | 1971 | Reject a cached credential for reuse if it lacks `oidc_issuer`, has a |
| `auth/jwt.rs` | 45 | JWT expiration detection. Returns `None`/`false` for non-JWT tokens. |
| `auth/manager.rs` | 2364 | `AuthManager` -- single source of truth for `auth.json` + the |
| `auth/manager/enrichment.rs` | 256 | Background `/user` enrichment spawned by `AuthManager::update()`. |
| `auth/manager/lock.rs` | 1187 | Advisory `auth.json.lock` helpers (free functions, no `AuthManager` |
| `auth/manager/sleep_gate.rs` | 433 | System-sleep refresh-straddle mitigation for [`AuthManager`]. |
| `auth/meta.rs` | 58 | Access gate from `grok_build_access_gate`. |
| `auth/mod.rs` | 54 | — |
| `auth/model.rs` | 520 | Legacy auth.json scope key. Fallback for old devbox auth files. |
| `auth/oidc/login.rs` | 701 | Interactive login orchestration: callback HTTP server, browser |
| `auth/oidc/mod.rs` | 14 | OIDC authentication: protocol, login, and refresh submodules. |
| `auth/oidc/protocol.rs` | 1295 | Pure OIDC protocol mechanics: PKCE, discovery, token exchange, |
| `auth/oidc/refresh.rs` | 249 | Pure-data OIDC refresh. Talks to the IdP and returns |
| `auth/oidc/test_helpers.rs` | 130 | Shared test helpers for `oidc::protocol::tests` and `oidc::login::tests`. |
| `auth/recovery.rs` | 1140 | Unauthorized (401) recovery state machine. |
| `auth/refresh/external_refresher.rs` | 113 | Refreshes by re-running the operator's external auth binary via the async |
| `auth/refresh/mod.rs` | 217 | Callback for diagnostic log upload on auth refresh failure. |
| `auth/refresh/oidc_refresher.rs` | 339 | Escalate to `PermanentFailure` after this many consecutive transient |
| `auth/single_flight.rs` | 359 | Single-flight guard for interactive login. |
| `auth/storage.rs` | 578 | RAII guard for an exclusive advisory lock on `auth.json.lock`. |
| `auth/token_output.rs` | 155 | Shared parser for an auth command's stdout. |
| `auth/token_type.rs` | 67 | What kind of bearer is loaded right now. Dispatch key for |

### `bin/` (3 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `bin/chat-history-downgrade.rs` | 503 | normalize chat_history.jsonl, convert any v1 (ConversationItem) to v0 (ChatReque |
| `bin/test-sampling-server.rs` | 58 | Usage: cargo run -p xai-grok-shell --bin test-sampling-server |
| `bin/trace_classify.rs` | 224 | Replay an offline session trace against the Layer-2 TodoGate and |

### `builtin.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `builtin.rs` | 104 | Built-in files extracted to `~/.grok/` on startup. |

### `bundle.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `bundle.rs` | 1508 | — |

### `claude_import.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `claude_import.rs` | 2186 | Scope for an import operation. |

### `claude_import_state.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `claude_import_state.rs` | 337 | Persistent import state, loaded from / saved to `~/.grok/claude_import_state.jso |

### `cli_models.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `cli_models.rs` | 337 | Data APIs for `grok models`. Clients own display. |

### `config/` (4 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `config/mod.rs` | 1878 | Full configuration for the memory system. |
| `config/reloader.rs` | 1055 | Typed, `Send`-safe messages for the agent to apply inside its `LocalSet`. |
| `config/tests.rs` | 3617 | Mutex to serialize tests that touch the GROK_MEMORY env var. |
| `config/watcher.rs` | 1302 | A [`notify::Watcher`] that drops `EventKind::Access` before it reaches the |

### `extensions/` (46 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `extensions/auth.rs` | 252 | `x.ai/auth/*` and legacy `x.ai/{get,set}ApiKey` extension handlers. |
| `extensions/auth_gate.rs` | 18 | Require xAI auth from a sync context, accepting tokens in the client-side buffer |
| `extensions/billing.rs` | 610 | `x.ai/billing` extension handler. |
| `extensions/bundle.rs` | 1172 | ACP extension handlers for bundled subagent cache sync and status. |
| `extensions/chat_conversation_history.rs` | 12 | `x.ai/session/load_history`: fetch one older page of a gateway-backed |
| `extensions/code_nav.rs` | 439 | Code Navigation Extension Methods |
| `extensions/debug.rs` | 135 | `x.ai/debug/*` extension handlers for local client testing. |
| `extensions/feedback.rs` | 480 | `x.ai/feedback`, `x.ai/feedback/dismiss`, `x.ai/btw`, and `x.ai/review/*` |
| `extensions/fs.rs` | 252 | Filesystem extension API layer. |
| `extensions/git.rs` | 702 | Git extension API layer. |
| `extensions/hooks.rs` | 563 | `x.ai/hooks/*` extension handlers. |
| `extensions/hunk_tracker.rs` | 1030 | Hunk Tracker extension API layer. |
| `extensions/interject.rs` | 122 | `x.ai/interject` extension handler. |
| `extensions/jj.rs` | 64 | Jujutsu extension handlers — delegates to [`xai_grok_workspace::session::jj`]. |
| `extensions/marketplace.rs` | 1973 | `x.ai/marketplace/*` extension handlers. |
| `extensions/mcp.rs` | 2524 | MCP extension methods and business logic. |
| `extensions/memory.rs` | 101 | `x.ai/memory/flush`, `x.ai/memory/rewrite`, and `x.ai/compact_conversation` |
| `extensions/mod.rs` | 90 | Deserialize ACP params from their raw JSON string, mapping a parse failure |
| `extensions/notification.rs` | 2389 | Retained for wire backwards compatibility; always empty in the |
| `extensions/plugins.rs` | 307 | `x.ai/plugins/*` extension handlers. |
| `extensions/pr.rs` | 253 | `gh pr view --json` does not expose `isInMergeQueue`; query GraphQL via `gh api` |
| `extensions/privacy.rs` | 91 | `x.ai/privacy/setCodingDataRetention` extension handler. |
| `extensions/prompt_history.rs` | 162 | `x.ai/prompt_history` extension handler. |
| `extensions/prompt_meta.rs` | 71 | Typed metadata for a prompt `TextContent._meta` field. |
| `extensions/recap.rs` | 58 | `x.ai/recap` extension handler. |
| `extensions/repair.rs` | 295 | `x.ai/session/repair` — out-of-band recovery for sessions bricked by |
| `extensions/rewind.rs` | 111 | `x.ai/rewind/*` extension handlers. |
| `extensions/rollout.rs` | 46 | `x.ai/rollout/survey` extension handler. |
| `extensions/routing.rs` | 143 | Metadata from the request, used for routing notifications back to the |
| `extensions/search.rs` | 354 | Search extension API layer (fuzzy file search, content search). |
| `extensions/session_admin.rs` | 747 | Session-administration extension handlers. |
| `extensions/session_search.rs` | 117 | ACP extension handler for session search (`x.ai/session/search`). |
| `extensions/session_state.rs` | 309 | `x.ai/session/state` reads a session's metadata columns; `x.ai/session/import` |
| `extensions/session_updates.rs` | 1035 | ACP extension handler for bulk session updates (`x.ai/session/updates`). |
| `extensions/share.rs` | 290 | `x.ai/share_session` extension handler. |
| `extensions/skills.rs` | 676 | Generic params for methods that only need an optional `cwd`. |
| `extensions/suggest/ai_provider.rs` | 215 | Request AI-powered shell command suggestions via the session actor. |
| `extensions/suggest/file_provider.rs` | 1147 | Filesystem completion for the shell token under the cursor: any |
| `extensions/suggest/history_provider.rs` | 727 | Rank history matches from three tiers of history sources. |
| `extensions/suggest/mod.rs` | 680 | Deterministic Tab mode: run only the token providers (path/file). |
| `extensions/suggest/path_provider.rs` | 407 | The command token being typed, via the canonical tokenizer: quotes hide |
| `extensions/suggest/shell_token.rs` | 640 | Minimal shell-token syntax for completion: find the token under the |
| `extensions/task.rs` | 886 | Wire DTO for the `x.ai/task/kill` ext request. |
| `extensions/terminal.rs` | 366 | Response for any terminal creation — piped or PTY. Both return just a `terminalI |
| `extensions/usage.rs` | 104 | `x.ai/session/usage` — cumulative session token/cost as [`PromptUsage`]. |
| `extensions/worktree.rs` | 619 | Handler for x.ai/git/worktree/* extension methods. |

### `heap_profile/` (2 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `heap_profile/mod.rs` | 211 | Heap-profile IoC seam + threshold monitor. |
| `heap_profile/monitor.rs` | 1125 | Threshold-triggered jemalloc heap dump + upload. |

### `inspect/` (2 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `inspect/compat.rs` | 305 | Vendor-compat resolution for `grok inspect`. |
| `inspect/mod.rs` | 2012 | `grok inspect` — configuration introspection. |

### `instrumentation.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `instrumentation.rs` | 67 | Shim — see `xai_grok_telemetry::instrumentation` for the implementation. |

### `leader/` (7 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `leader/client.rs` | 1308 | Interval for sending keepalive pings to detect dead connections |
| `leader/lock.rs` | 601 | Compute a short hash suffix from a WS URL for differentiating leader instances. |
| `leader/mod.rs` | 2225 | Leader-follower IPC architecture for grok-shell. |
| `leader/protocol.rs` | 734 | Unique identifier for a connected client. |
| `leader/server.rs` | 6783 | The binary version of the currently running leader process. |
| `leader/test_support.rs` | 178 | In-crate fake leaders for exercising client-side handling of misbehaving |
| `leader/transport.rs` | 304 | Cross-platform IPC transport for leader<->client communication. |

### `lib.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `lib.rs` | 46 | — |

### `managed_config/` (2 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `managed_config/response.rs` | 257 | The deployment-config fetch/response contract: the credential source and its |
| `managed_config/tests.rs` | 446 | Fail closed only for a managed principal AND compromised policy; every other com |

### `managed_config.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `managed_config.rs` | 1123 | Sync `managed_config.toml` + `requirements.toml` from the deployment-config endp |

### `mcp_doctor.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `mcp_doctor.rs` | 737 | `grok mcp doctor` -- runtime health check for MCP servers. |

### `plugin.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `plugin.rs` | 2234 | Shared plugin lifecycle operations (output-agnostic). |

### `relay/` (3 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `relay/mod.rs` | 17 | Relay session sharing module. |
| `relay/sync.rs` | 1141 | WebSocket relay sync for real-time session sharing. |
| `relay/types.rs` | 51 | Shared types for relay session sharing. |

### `remote/` (9 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `remote/agent.rs` | 327 | Remote sandbox client for cli-chat-proxy. |
| `remote/chat_models_client.rs` | 206 | grok.com chat-product model catalog (`POST /rest/modes`) — the models |
| `remote/client.rs` | 2115 | HTTP client for backend CRUD operations. |
| `remote/conversations_client.rs` | 309 | Body for `PUT /rest/app-chat/conversations/{id}` (grok-web `chatUpdateConversati |
| `remote/mod.rs` | 37 | Remote storage client for the backend. |
| `remote/pull.rs` | 458 | Pull-on-miss: fetch a session from the backend and hydrate local JSONL storage. |
| `remote/pull_smoke_test.rs` | 127 | Push → pull round-trip smoke test against the live backend. |
| `remote/sync.rs` | 182 | Writeback push: async queue that flushes session updates to the backend. |
| `remote/workspaces_client.rs` | 180 | — |

### `sampling/` (4 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `sampling/conversation.rs` | 40 | API-agnostic conversation representation. |
| `sampling/error.rs` | 774 | Sampling error types. |
| `sampling/mod.rs` | 32 | — |
| `sampling/types.rs` | 64 | Render an `ImageContent` produced by the read-file tool as a URL |

### `session/` (141 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `session/acp_conversion.rs` | 1328 | ACP conversion functions for `xai-grok-tools`'s `ToolOutput`. |
| `session/acp_mcp.rs` | 144 | In-process SDK MCP servers over the ACP reverse channel (`x.ai/mcp/sdk_call`). |
| `session/acp_session.rs` | 2070 | Session actor implementation for the MVP ACP agent. |
| `session/acp_session/hooks.rs` | 566 | Client-registered hooks for [`SessionActor`]. |
| `session/acp_session_impl/extensions.rs` | 57 | Composition root for the session's extensions: each lives in its own submodule a |
| `session/acp_session_impl/extensions/idle_prompt.rs` | 130 | Debounced `idle_prompt` notification extension. |
| `session/acp_session_impl/goal.rs` | 2323 | Goal-orchestration concern for `SessionActor`. |
| `session/acp_session_impl/goal_support.rs` | 1586 | Goal-harness support for `SessionActor`: reminder/directive templates, |
| `session/acp_session_impl/hook_dispatch.rs` | 385 | Hook dispatch concern for `SessionActor`: run contexts, hook execution |
| `session/acp_session_impl/hooks_plugins.rs` | 1050 | Trust the current project via the unified folder-trust store. Now an |
| `session/acp_session_impl/interjection.rs` | 338 | Mid-turn interjection concern for `SessionActor` (buffer type, formatting, |
| `session/acp_session_impl/laziness.rs` | 884 | Laziness / stop-detector concern for `SessionActor`. |
| `session/acp_session_impl/laziness_classifier.rs` | 869 | Layer-3 LazinessDetector pure helpers: classifier prompt/config consts, |
| `session/acp_session_impl/mcp.rs` | 1881 | Wait for MCP tools to be initialized. |
| `session/acp_session_impl/mcp_snapshot.rs` | 441 | MCP snapshot concern for `SessionActor`: server-snapshot refresh and |
| `session/acp_session_impl/memory_dream.rs` | 768 | Memory concern for `SessionActor`: memory flush, the dream pipeline, |
| `session/acp_session_impl/model_switch.rs` | 342 | Handle [`SessionCommand::RebuildAgentForDefinition`]. |
| `session/acp_session_impl/notification_drain.rs` | 626 | Idle-gated pending-notification buffering and drain for `SessionActor`, |
| `session/acp_session_impl/prompt_build.rs` | 906 | User-message construction concern for `SessionActor`: templated prefix |
| `session/acp_session_impl/prompt_queue.rs` | 955 | Running-turn display fields for `x.ai/queue/changed` (clients paint turn-start U |
| `session/acp_session_impl/recap.rs` | 691 | Auxiliary model-call concern for `SessionActor`: side questions, recap |
| `session/acp_session_impl/reminders.rs` | 861 | System-reminder injection concern for `SessionActor`: reminder policy, |
| `session/acp_session_impl/rewind.rs` | 583 | Rewind concern for `SessionActor`: rewind points, cross-compaction |
| `session/acp_session_impl/run_loop.rs` | 2239 | The session actor's main loop (`run_session`): command dispatch, idle |
| `session/acp_session_impl/sampler_turn.rs` | 1490 | Sampler-turn pipeline for `SessionActor`: tool definitions, model auth |
| `session/acp_session_impl/session_mode.rs` | 364 | Session/plan-mode concern for `SessionActor` (`handle_session_mode`, |
| `session/acp_session_impl/session_setup.rs` | 639 | Session initialization concern for `SessionActor`: `initialize`, prefix |
| `session/acp_session_impl/slash_exec.rs` | 1020 | Execute a built-in slash command (e.g. `/compact`, `/yolo`). |
| `session/acp_session_impl/spawn.rs` | 2539 | Session bring-up concern for `acp_session`: `spawn_session_actor`, the |
| `session/acp_session_impl/stop_gate.rs` | 498 | The turn-end `Stop`/`SubagentStop` gate for `SessionActor`. |
| `session/acp_session_impl/tasks_cancel.rs` | 744 | Prompt-task plumbing for `SessionActor` (`AgentTask`, `TaskSlot`, |
| `session/acp_session_impl/tool_calls.rs` | 3160 | Tool-call execution concern for `SessionActor`: the model-output → |
| `session/acp_session_impl/tool_dispatch.rs` | 411 | Tool dispatch helpers for `SessionActor`: `dispatch_tool` and its lock / |
| `session/acp_session_impl/turn.rs` | 2770 | Turn-execution concern for `SessionActor` (`handle_prompt`, turn-end, |
| `session/acp_session_impl/turn_end.rs` | 475 | Turn-completion concern for `SessionActor`: completion handling |
| `session/acp_session_impl/types.rs` | 261 | Top-level enum definitions for `acp_session`; their `impl` blocks and |
| `session/acp_session_impl/updates.rs` | 1009 | Outbound update emission concern for `SessionActor`: `send_update` and |
| `session/acp_session_impl/workflow.rs` | 371 | — |
| `session/acp_session_tests/support.rs` | 615 | Wrap `id` in a shared auth-method handle for `SessionActor` test literals |
| `session/acp_types.rs` | 917 | Public wire types (DTOs) for the ACP session actor. |
| `session/agent_rebuild.rs` | 539 | `AgentRebuildSpec` — the canonical recipe for constructing an |
| `session/announcement_state.rs` | 119 | Persisted announcement tracking state for session resumption. |
| `session/chat_persistence.rs` | 127 | Production `ChatPersistence` implementation backed by the existing persistence c |
| `session/commands.rs` | 780 | Session actor command enum and associated public types. |
| `session/compaction.rs` | 3834 | Compaction methods for `SessionActor`. |
| `session/compaction_config.rs` | 211 | Compaction configuration and runtime state for the session actor. |
| `session/compaction_segments.rs` | 59 | Shell-side dispatch on [`CompactionMode`]. Split into two methods so the |
| `session/events.rs` | 1008 | Re-exports of the crate-internal event types that live in |
| `session/export.rs` | 171 | Session export for sharing via the remote session-sharing backend. |
| `session/feedback.rs` | 1170 | Feedback request heuristics for Grok Code sessions. |
| `session/feedback_manager.rs` | 1659 | Feedback manager for session-level feedback collection. |
| `session/file_system.rs` | 359 | Pagination offset applied after the dirs-first sort (default 0). |
| `session/fork.rs` | 326 | Session forking functionality |
| `session/fs_watch.rs` | 1650 | Session-level fs-watch policy over [`xai_fsnotify`]. |
| `session/goal_classifier.rs` | 6596 | Goal-verification stage (harness-owned). |
| `session/goal_classifier/evidence.rs` | 2178 | Evidence-packet construction for the goal-verification stage. |
| `session/goal_evaluator.rs` | 246 | — |
| `session/goal_next_step.rs` | 369 | Helper that mines the "next concrete step" inlined into the goal |
| `session/goal_orchestrator.rs` | 492 | Goal mode support — notification helpers and state formatters. |
| `session/goal_planner.rs` | 1689 | Goal planner subagent runner. Mirrors [`crate::session::goal_classifier`] |
| `session/goal_role_tools.rs` | 654 | Shared per-role prompt tool-name rendering for the `/goal` harness. |
| `session/goal_stop_detector.rs` | 637 | Heuristic stop-detector for premature "give up" turn endings. |
| `session/goal_strategist.rs` | 1296 | Stall-triggered goal strategist subagent runner. |
| `session/goal_summarizer.rs` | 680 | Achievement-triggered goal summarizer subagent runner. |
| `session/goal_tracker.rs` | 3758 | Goal mode state machine. |
| `session/handle.rs` | 592 | `SessionHandle` — the `Clone + Send` proxy for interacting with a session actor. |
| `session/helpers/chat.rs` | 142 | Returns the largest valid UTF-8 character boundary index at or before `index`. |
| `session/helpers/compaction_context.rs` | 458 | Rendering helpers for [`CompactionStateContext`] that depend on |
| `session/helpers/full_replace_compaction.rs` | 423 | grok-build's L5 wiring onto the shared full-replace engine |
| `session/helpers/memory_context.rs` | 357 | Format memory search results as `<system-reminder>` content. |
| `session/helpers/memory_flush.rs` | 739 | Pre-compaction memory flush logic. |
| `session/helpers/mod.rs` | 13 | — |
| `session/helpers/prompt_suggest.rs` | 585 | Next-prompt prediction helpers (tab autocomplete ghost text). |
| `session/helpers/replay.rs` | 1256 | Replay pipeline for cross-compaction rewind. |
| `session/helpers/session_compact.rs` | 2255 | Compacts the current conversation and generates a summary of the conversation wh |
| `session/helpers/session_recap.rs` | 848 | Session recap generation helpers. |
| `session/helpers/session_summary.rs` | 253 | Session title generation via LLM tool call. |
| `session/helpers/tool_input_parsing.rs` | 174 | Normalize empty tool call arguments to `"{}"`. |
| `session/image_describe.rs` | 805 | Image processing helpers for sessions with image inputs. |
| `session/image_normalize.rs` | 1557 | Re-encode decoded attachments that exceed [`MAX_IMAGE_BYTES`], |
| `session/inference_metrics.rs` | 9 | Per-response inference latency metrics. |
| `session/managed_mcp.rs` | 1216 | Shell-side managed MCP: merges MCP server sources, then injects managed |
| `session/mcp_descriptors.rs` | 417 | MCP descriptor mirror. |
| `session/mcp_dispatcher.rs` | 1753 | Session-actor side `StatusDispatcher` for MCP client events. |
| `session/mcp_restart.rs` | 1518 | Bounded stdio MCP auto-restart. |
| `session/mcp_servers.rs` | 158 | MCP server re-exports + shell-side wrappers for timeout override resolution. |
| `session/memory/hooks.rs` | 648 | Session lifecycle hooks for the memory system. |
| `session/memory/mod.rs` | 18 | Memory system shim. |
| `session/memory_state.rs` | 238 | `SessionMemory` — memory subsystem state for the session actor. |
| `session/merge.rs` | 1209 | Merged session listing — combines local and remote session data. |
| `session/mod.rs` | 365 | `false` twin: this template is not compiled into this build, so no |
| `session/normalize_cache.rs` | 536 | Process-wide cache for normalized images: `moka::future::Cache` |
| `session/notifications.rs` | 26 | `NotificationSender` — transport layer for session notifications. |
| `session/pending_interaction.rs` | 230 | Per-session pending-interaction registry. |
| `session/persistence.rs` | 4529 | Current chat history format version. |
| `session/plan_mode.rs` | 1226 | Plan mode state machine and prompt text generation. |
| `session/prompt_history.rs` | 402 | Per-working-directory prompt history for fast reverse search. |
| `session/prompt_parser.rs` | 547 | Parsed prompt with context and query kept separate. |
| `session/prompt_queue.rs` | 83 | Server-authoritative prompt queue wire types. |
| `session/prompt_timing.rs` | 7 | Per-turn prompt latency measurement. |
| `session/replay_events.rs` | 178 | Notification destined for the high-frequency event ReplayBuffer). |
| `session/repo_changes/mod.rs` | 10 | Serialize local repository changes (local commits + uncommitted worktree/index c |
| `session/restore_stub.rs` | 214 | — |
| `session/result.rs` | 140 | Extension method result: `{ result: T | null, error?: string | ExtMethodError }` |
| `session/signals.rs` | 3133 | Session signals tracking for feedback heuristics. |
| `session/slash_commands.rs` | 2992 | ACP slash command advertising and resolution. |
| `session/storage/jsonl/mod.rs` | 2093 | JSONL storage under `{root}/sessions/{url_encoded_cwd}/{session_id}/`. |
| `session/storage/jsonl/tests.rs` | 3642 | Resume from updates.jsonl alone: when chat_history.jsonl is missing, load |
| `session/storage/mod.rs` | 3713 | On-disk file names, relative to a session directory. Single source of truth for |
| `session/storage/relocation/fs.rs` | 373 | Durable filesystem operations used by session relocation. |
| `session/storage/relocation/journal.rs` | 225 | Relocation journal types, validation, lease, and commit-aware persistence. |
| `session/storage/relocation/mod.rs` | 803 | Durable, source-retaining relocation of a dormant session directory. |
| `session/storage/relocation/tests.rs` | 581 | — |
| `session/storage/relocation/view.rs` | 209 | Recovery-aware point-in-time view of local session storage. |
| `session/storage/search.rs` | 2087 | Session search orchestration: querying and background indexing. |
| `session/storage/search_fts.rs` | 1157 | SQLite-backed FTS5 index for session search. |
| `session/storage/search_remote_sync.rs` | 580 | GCS-based remote index sync for session search. |
| `session/storage/summary_write.rs` | 466 | Concurrency-safe, field-correct writes to a session's `summary.json`. |
| `session/streaming_capture.rs` | 622 | Out-of-band per-turn capture of the model's streamed reasoning + text. |
| `session/summary.rs` | 124 | Session summary (title) generation lifecycle. |
| `session/telemetry.rs` | 224 | Permission-mode label for the `session.permission_mode_changed` span. |
| `session/tool_index.rs` | 2502 | Concrete `ToolSearchIndex` implementation using BM25. |
| `session/turn_completion.rs` | 125 | Pure construction of the durable, replayable turn-completion terminal. |
| `session/two_pass.rs` | 385 | Pure builders for prefire two-pass compaction (shell-only). |
| `session/unified_list/cursor.rs` | 571 | — |
| `session/unified_list/envelope.rs` | 49 | — |
| `session/unified_list/facets.rs` | 718 | — |
| `session/unified_list/mod.rs` | 1014 | Hard-off in release builds so they can't enable the |
| `session/unified_list/row.rs` | 197 | — |
| `session/user_message.rs` | 221 | Wraps the user query properly |
| `session/wire_tags.rs` | 128 | Single source of truth for the `sessionUpdate` discriminant strings the |
| `session/workflow/host_service.rs` | 978 | — |
| `session/workflow/manager.rs` | 1435 | — |
| `session/workflow/mod.rs` | 45 | — |
| `session/workflow/notify.rs` | 272 | — |
| `session/workflow/registry.rs` | 873 | — |
| `session/workflow/schema_contract.rs` | 177 | — |
| `session/workflow/store.rs` | 492 | — |
| `session/workflow/tracker.rs` | 1202 | — |
| `session/worktree.rs` | 1186 | Git worktree operations: create, list, remove, apply. |
| `session/worktree_pool.rs` | 2439 | Bounded worktree pool for fast fork setup. |

### `terminal/` (10 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `terminal/acp_terminal.rs` | 118 | — |
| `terminal/adapter.rs` | 692 | `AcpTerminalAdapter`: implements `xai-grok-tools::TerminalBackend` over ACP |
| `terminal/background_task.rs` | 772 | Background task registry for tracking long-running commands. |
| `terminal/exit_watcher.rs` | 255 | Exit detection and completion for ACP background terminals: awaits |
| `terminal/local_terminal.rs` | 189 | Truncate buffer to keep only the last `limit` bytes (drops oldest bytes). |
| `terminal/mod.rs` | 233 | Resolved absolute path to bash. On Unix uses the `xai_grok_config` shell |
| `terminal/output_recorder.rs` | 236 | Reconstructs a client-side terminal's log file from its `terminal/output` |
| `terminal/pty_session.rs` | 754 | Agent-scoped interactive PTY manager. PTYs are keyed by `terminalId`, |
| `terminal/runner.rs` | 44 | Whether to stream output updates and register in the terminal registry. |
| `terminal/streaming_local_terminal.rs` | 1550 | Upper bound on how long terminal teardown waits for a SIGKILL'd child to be |

### `test_support/` (2 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `test_support/lsp_runtime.rs` | 174 | Like `test_gateway` but returns the receiver; keep it alive for the test. |
| `test_support/mod.rs` | 38 | Prepend the hermetic git binary (via `GIT_BIN_PATH`) to `PATH` so that |

### `tier.rs/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `tier.rs` | 58 | Subscription-tier classification shared across the shell and the pager. |

### `tools/` (7 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `tools/bridge.rs` | 6 | ToolBridge: re-exported from `xai-grok-tools`. |
| `tools/config.rs` | 747 | Production grok-build foreground command-timeout ceiling (seconds). The |
| `tools/mod.rs` | 22 | Tool infrastructure for xai-grok-shell. |
| `tools/notification_bridge.rs` | 2327 | Notification bridge: translates `xai-grok-tools` `ToolNotification` events |
| `tools/retry.rs` | 26 | Retry utilities — re-exported from `xai-grok-tools`. |
| `tools/todo.rs` | 81 | Todo types — re-exported from `xai-grok-tools` with ACP conversion helpers. |
| `tools/tool_context.rs` | 420 | Session context — legacy name "ToolContext". |

### `trace_classifier/` (1 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `trace_classifier/mod.rs` | 3063 | Trace-replay classifier. |

### `upload/` (5 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `upload/gcs.rs` | 160 | Shell-side adapter that threads the live `AuthManager` through to the |
| `upload/manifest.rs` | 433 | Upload manifest: authoritative "turn upload is done" signal. |
| `upload/mod.rs` | 4 | — |
| `upload/trace.rs` | 2514 | Per-turn trace artifact uploads to cloud storage. |
| `upload/turn.rs` | 509 | Turn lifecycle orchestration for trace uploads. |

### `util/` (28 files)

| 文件 | 行数 | 首行文档 |
| --- | ---: | --- |
| `util/config/announcements.rs` | 93 | Announcement entry received from cli-chat-proxy `/v1/settings`. |
| `util/config/campaigns.rs` | 745 | Campaign dismiss state, remote cache, and effective-config overlay. |
| `util/config/hints.rs` | 392 | Persisted worktree preference for `/new` and `/fork` (`[hints]` in config.toml). |
| `util/config/load.rs` | 296 | Resolve a bool from an optional env var > config.toml `[section] key` > false. |
| `util/config/mcp.rs` | 2048 | TUI/CLI settings. Composed from typed section configs defined in `agent::config` |
| `util/config/mod.rs` | 34 | — |
| `util/config/permissions.rs` | 748 | How the agent handles tool execution permissions. Defined in |
| `util/config/persist.rs` | 1392 | Process-wide write lock for `~/.grok/config.toml`. |
| `util/config/resolve/auto_mode.rs` | 571 | Env override for the **auto** permission-mode feature gate. |
| `util/config/resolve/compaction.rs` | 219 | Default auto-compact threshold (% of context window) when no source sets it. |
| `util/config/resolve/crash_handler.rs` | 230 | Env override for the full crash-handler install gate. |
| `util/config/resolve/display_refresh.rs` | 654 | Display-refresh probe + auto-cadence policy resolve and pure cadence derivation. |
| `util/config/resolve/features.rs` | 262 | Resolve whether ZDR users are allowed to use the product. |
| `util/config/resolve/mcp.rs` | 466 | Resolve `mcp.liveness_watchers` for a session. |
| `util/config/resolve/mod.rs` | 29 | — |
| `util/config/resolve/system_prompt.rs` | 166 | Resolve system-prompt identity label. |
| `util/config/resolve/tool_approvals.rs` | 192 | Env override for the **remember tool approvals** permission-panel gate. |
| `util/config/resolve/toolset.rs` | 765 | Resolve whether the bash-harness `find`→`bfs` / `grep`→`ugrep` shadows are |
| `util/config/resolve/ui.rs` | 442 | Env override for showing agent thinking blocks in the TUI. |
| `util/config/resolve/version.rs` | 512 | Machine-readable channel name derived from the GCS stable pointer cache. |
| `util/config/settings_writes.rs` | 310 | Persist `[ui].compact_mode` via `update_config`. |
| `util/config/tips.rs` | 398 | Read `[cli] show_tips` from config.toml. Returns `None` if not set. |
| `util/config/worktree.rs` | 802 | Worktree creation type configuration. |
| `util/grok_auth_credentials.rs` | 189 | Credentials for authenticating with grok backend services. |
| `util/hooks.rs` | 116 | Shared hook source path discovery. |
| `util/mod.rs` | 141 | Aborts the wrapped tokio task when dropped. |
| `util/subprocess.rs` | 367 | Shared subprocess helpers: a TTY-detached async runner with a wall-clock |
| `util/user_identity.rs` | 904 | Resolves and caches the author identity for feedback submissions |

---

## 19. 核心文件精读

### 19.1 `agent/mvp_agent/acp_agent.rs` (4113 行)

> [`acp::Agent`] trait implementation for [`MvpAgent`].
> Co-located child of `mvp_agent` (`use super::*`).
> Which `x_search` sub-tools enforce the date cutoff, sent in `initialize`. `x_user_search` and
> `x_thread_fetch` are `false`: they don't honor it yet.

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.2 `agent/mvp_agent/mod.rs` (2668 行)

> A `'static` reference to a value on a single-threaded `LocalSet`.
> 
> Encapsulates the raw-pointer pattern used when `spawn_local` tasks need
> `&T` but the borrow checker requires `'static`. The pointer is valid as
> long as:
> 
> 1. `T` is heap-allocated and never moved (e.g., behind `Rc` or owned by
> the ACP connection for the process lifetime).
> 2. All access happens on the **same** `LocalSet` thread (no `Send`).
> 3. The `LocalRef` does not outlive the `LocalSet`.
> 
> These invariants are upheld by construction: `LocalRef` is `!Send`
> (via `*const T`) and only used inside `spawn_local` closures on the
> agent's `LocalSet`.
> Create a `LocalRef` from a shared reference.
> 
> # Safety contract (enforced by the caller, not by the type system)
> 
> The referenced `T` must live for the entire duration of the `LocalSet`
> and must not be moved or deallocated while any `LocalRef` clone exists.

**符号：** fn`reject_direct_hub_cloud_meta`, fn`jwt_tier_claim`, fn`resolve_subscription_tier_for_telemetry`, fn`jwt_claim_matches_user_subscription_tier`, fn`parse_session_plugin_dirs`, fn`chat_session_spawn_options`, fn`resolve_session_auto_mode`, fn`build_prompt_response_meta`, fn`warm_async_http_client`, fn`resolve_required_agent_type`, fn`inherited_harness_template`, fn`agent_name_after_model_switch`, fn`harnesses_are_compatible`, fn`warn_on_missing_parent_session_for_validate_type`, fn`parse_json_object_env`, fn`settings_allow_access`, struct`LocalRef`, struct`SessionSpawnOptions`, struct`PromptResponseMeta`, struct`PromptResponseMetaArgs`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.3 `agent/app.rs` (2343 行)


**符号：** fn`run_auto_update_checker`, fn`run_stdio_agent`, fn`run_headless`, fn`run_headless_no_browser`, fn`run_leader`, struct`LeaderAutoUpdateConfig`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.4 `agent/init.rs` (200 行)

> Agent bootstrap and lifecycle hooks.
> 
> [`bootstrap`] runs the full init sequence (config resolution, process
> singletons, model catalog) and returns a resolved config + `ModelsManager`.
> [`update_telemetry_config`] re-initializes telemetry after auth changes.
> Resolve config, init process singletons, build the model catalog.
> 
> The `ModelsManager` is `Clone + Send`, so callers that need a handle
> for the config watcher can clone it before passing it to
> `MvpAgent::with_models`.

**符号：** fn`bootstrap`, fn`exit_on_config_error`, fn`update_telemetry_config`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.5 `session/acp_session.rs` (2070 行)

> Session actor implementation for the MVP ACP agent.
> 
> Each session runs as an actor with its own chat history and tool context.
> The agent owns the client connection and routes commands and events via
> channels:
> - Agent → Session: `SessionCommand` (prompt, cancel, shutdown)
> - Session → Client: `session_notification` via a shared gateway handle
> 

**符号：** fn`is_session_idle_for_injection`, fn`state_is_busy`, fn`load_system_prompt`, fn`load_prompt_context`, struct`InputItem`, struct`State`, struct`PreparedToolCall`, struct`ModelAuthMemo`, struct`SessionActor`, struct`TraceConfigTemplate`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.6 `session/commands.rs` (780 行)

> Session actor command enum and associated public types.
> 
> `SessionCommand` defines the message protocol used to drive a session
> actor. It was extracted from `acp_session.rs` to keep the actor
> implementation focused on behaviour.
> Structured context for a cancelled turn, replacing stringly-typed JSON.
> What triggered the cancel (`"send_now"`, `"esc"`, `"ctrl_c"`); surfaced
> as `cancelTrigger` on the `PromptResponse`/`TurnCompleted` `_meta`.
> `None` for graceful in-turn cancels and older clients.
> Prompt completion kind returned to the ACP layer.

**符号：** fn`ok_end_turn`, struct`CancellationContext`, struct`PromptTurnOk`, struct`ParsedPromptInfo`, struct`TaskWakeFallback`, struct`TaskWakeAdmission`, enum`PromptCompletionKind`, enum`NotificationPriority`, enum`NotificationSource`, enum`SessionCommand`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.7 `session/handle.rs` (592 行)

> `SessionHandle` — the `Clone + Send` proxy for interacting with a session actor.
> 
> Callers hold a `SessionHandle` and send `SessionCommand` messages via the
> internal channel. Extracted from `acp_session.rs` to keep the actor
> implementation focused on behaviour.
> Coarse lifecycle state of a session as known to the leader/agent.
> 
> A grok session has no
> terminal status field on its own — it is a resumable log on disk — so
> "liveness" is *residency + turn-state*, not a pid. The agent's join-handle
> supervisor tracks this per session so a panicked actor can be reaped
> (demoted to `Dormant`) instead of lingering as a roster zombie. This is the
> data source the roster/dashboard reads.
> Resident actor, a turn is currently running.
> Resident actor, no turn in flight.
> On disk, not resident (idle-unloaded or never loaded this run).
> Finished and resumable (terminal marker on disk).

**符号：** struct`SessionHandle`, enum`SessionLiveState`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.8 `session/agent_rebuild.rs` (539 行)

> `AgentRebuildSpec` — the canonical recipe for constructing an
> [`xai_grok_agent::Agent`] for a given session.
> 
> INVARIANT: This is the **only** place in the shell crate that calls
> [`xai_grok_agent::AgentBuilder::new`]. Both initial session spawn
> ([`crate::session::acp_session::spawn_session_actor`]) and zero-turn
> harness rebuild
> ([`crate::session::acp_session::SessionActor::handle_rebuild_agent_for_definition`])
> go through [`AgentRebuildSpec::build_agent`].
> 
> ## Why this exists
> 
> [`xai_grok_agent::Agent`] owns an [`xai_grok_tools::bridge::ToolBridge`]
> that carries session-scoped channels (notification handle, terminal/fs
> backends, subagent senders, scheduler set, plugin registry, attribution
> callback). The Agent is therefore session-bound — it cannot be shared
> across sessions and cannot be re-rendered from outside its session
> context. To rebuild it (e.g. when the user picks a model with a
> different `agent_type` before sending any user message), we need to
> retain every input that the original `AgentBuilder` chain consumed.
> `AgentRebuildSpec` is exactly that retained bag of inputs.
> 
> ## WHEN ADDING A NEW [`xai_grok_agent::AgentBuilder`]`::with_*` KNOB
> 
> 1. Add the corresponding field to [`AgentRebuildSpec`].
> 2. Pass it through in [`AgentRebuildSpec::build_agent`]. The destructure
> pattern at the top of `build_agent` forces every field to be used —
> drift is a compile error (`#[deny(unused_variables)]`).
> 3. Populate the field at the call site in `spawn_session_actor`.
> 

**符号：** fn`test_rebuild_spec_default`, struct`ResolvedToolParamsJson`, struct`AgentRebuildSpec`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.9 `session/acp_session_impl/turn.rs` (2770 行)

> Turn-execution concern for `SessionActor` (`handle_prompt`, turn-end,
> sampling loop).
> Synthetic tool the model calls to return its schema-constrained final answer
> on backends that can't constrain output natively (Messages API). Intercepted
> in the loop, never executed as a real tool.
> Max times the model may re-call `StructuredOutput` with non-conforming args
> before the turn ends with the last validation error.
> What a `StructuredOutput` tool call means for the turn (see
> `handle_structured_output_tool_call`).
> Accepted, or retries exhausted: the carried result is the final output.
> Non-conforming args; a corrective tool_result was pushed — re-sample.
> No sole StructuredOutput call (absent, or co-emitted with real tools that
> should run this round).
> Parse `raw` as JSON and validate it against a `validator` compiled once per
> turn. Returns the value on success, or a human-readable error (surfaced to
> the model on retry and to the client as `structuredOutputError`). A `validator`
> of `Err` means the user's schema itself was invalid.

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.10 `session/acp_session_impl/run_loop.rs` (2239 行)

> The session actor's main loop (`run_session`): command dispatch, idle
> arms, and the free helpers only the loop consumes.
> The `YoloToggled` event to emit after `set_yolo_mode(requested)`, given the
> previous state and the post-call ACTUAL state (read back via
> `is_yolo_mode()`). Returns `Some(actual)` only on a real change.
> 
> Callers MUST pass the read-back `actual`, never the request: under the
> always-approve pin the manager clamps a requested ON to OFF, so reporting the
> request would announce (event + telemetry + log) a turn-on that never
> happened.
> A pin-clamped enable (requested ON but actual stays OFF) reports no
> change, so no spurious "turned on" event/telemetry is emitted. Real
> flips report the actual new state.
> Best-effort removal of this session's per-session scratch staging on

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.11 `session/acp_session_impl/spawn.rs` (2539 行)

> Session bring-up concern for `acp_session`: `spawn_session_actor`, the
> per-session OS thread (`SessionThread` / `spawn_session_on_thread`), and
> the MCP auto-restart wiring (`SessionRestartActions`).
> Partition CLI `--allow` rules under the pin: blanket catch-all allows
> (`Allow(Any)` `*` / `**`, plus bare/match-all Bash/MCP/WebFetch grants — see
> `resolution::is_catchall_allow`) substitute for the blocked `--yolo`, so drop them when
> `policy_block` is set; keep everything else (and everything without a pin).
> Pure (no I/O) so the wiring is unit-testable; the caller surfaces `dropped`.

**符号：** fn`spawn_session_actor`, fn`spawn_session_on_thread`, struct`SessionThread`, struct`SessionRestartActions`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.12 `session/acp_session_impl/tool_calls.rs` (3160 行)

> Tool-call execution concern for `SessionActor`: the model-output →
> tool-execution pipeline (`execute_tool_calls`, `prepare_tool_call`,
> tool-call start/success/error notifications, and sampling-event handling).
> 
> `#[path]` child of `acp_session` (see the module comments there) so this
> `impl SessionActor` block retains access to the actor's private fields and
> the parent module's private helpers.
> Whether a tool name is an MCP `create_pull_request` (qualified
> `server__create_pull_request` or bare).
> Blocking wait tools that should abort when a mid-turn interjection is pending.

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.13 `session/acp_session_impl/session_setup.rs` (639 行)

> Session initialization concern for `SessionActor`: `initialize`, prefix
> readiness, skills reload and reminders, session info, and model-metadata
> refresh.
> `true` for session-based ACP auth methods.

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.14 `session/acp_session_impl/sampler_turn.rs` (1490 行)

> Sampler-turn pipeline for `SessionActor`: tool definitions, model auth
> facts/gates and retry, sampler config reconstruction, sampling-failure
> recovery, and per-response usage recording.
> Auth-failure detector for tool errors. Matches strictly on HTTP 401
> when the error carries a structured status code, mirroring
> `SamplingError::is_auth_error` in xai-grok-sampling-types: 403 is
> deliberately excluded because it means "authenticated but forbidden"
> (content-safety blocks, ZDR-gated requests, remote settings gates), where
> a token refresh would be a no-op and would surface to the client as
> a spurious auth_required teardown.
> 
> String fallbacks remain for tools that surface auth failures without
> going through the structured `HttpFailure` path (e.g. JSON-only
> `invalid_token` payloads, BYOK key-validation messages).
> Gate inputs bundled with the composed decision so the 401-recovery log can
> report the components.

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.15 `session/persistence.rs` (4529 行)

> Current chat history format version.
> - Version 0: Legacy ChatRequestMessage format (default for old sessions)
> - Version 1: ConversationItem format (used for new sessions)

**符号：** fn`is_false`, fn`session_exists_for_cwd`, fn`find_local_child_for_remote`, fn`resolve_local_session`, fn`resolve_local_session_for_repo`, fn`resolve_local_session_for_repo_in_root`, fn`resolve_local_session_any_cwd`, fn`resolve_local_session_any_cwd_result`, fn`find_session_dir_by_id`, fn`find_persisted_session_dir_by_id_result`, fn`find_persisted_session_dir_by_id_in_root_result`, fn`find_any_session_dir_by_id_result`, fn`find_summary_by_session_id`, fn`find_summary_by_session_id_in_root`, fn`local_summaries_for_cwd_sync`, fn`resumed_session_sandbox_profile`, fn`get_prompt_file_path`, fn`grok_home_string`, fn`default_model_id`, fn`io_error_to_acp`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.16 `session/mcp_servers.rs` (158 行)

> MCP server re-exports + shell-side wrappers for timeout override resolution.

**符号：** fn`build_config_resolved_event`, fn`start_mcp_server`, fn`build_pending_clients`, fn`start_mcp_servers`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.17 `extensions/mcp.rs` (2524 行)

> MCP extension methods and business logic.
> 
> - `x.ai/mcp/list` — list available MCP servers (agent-scoped or session-annotated)
> - `x.ai/mcp/call` — invoke an MCP tool directly, outside the LLM loop
> - `x.ai/mcp/servers_updated` — notification pushed when managed configs resolve
> - `x.ai/mcp/server_status` — per-server delta pushed by the
> `StatusDispatcher` (transport-closed pollers, handshake failures,
> config diffs, server-pushed list-changed notifications). See
> [`crate::session::mcp_dispatcher`] for the coalescing /
> payload-shaping logic. Re-exported below so other crates have a
> single import point.
> Agent-only `x.ai/mcp/*` ACP method/notification names.
> 
> Unlike [`wire::MCP_CALL`] (the cross-SDK contract, which stays in
> `xai_grok_mcp::wire`), these methods are private to the agent↔client channel and

**符号：** fn`notify_servers_updated`, fn`handle`, fn`build_mcp_catalog`, fn`build_mcp_catalog_with_gateway_tools`, fn`build_mcp_status`, fn`init_agent_mcp_pool`, fn`call_mcp_tool`, fn`read_mcp_resource`, struct`McpListRequest`, struct`McpListResponse`, struct`McpServerEntry`, struct`McpEnvVar`, struct`McpServerSessionState`, struct`McpToolEntry`, struct`McpCallRequest`, struct`McpCallResponse`, struct`McpContentBlock`, struct`McpStatusSnapshot`, struct`McpClientStatus`, struct`McpServersUpdated`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.18 `auth/manager.rs` (2364 行)

> `AuthManager` -- single source of truth for `auth.json` + the
> in-memory bearer cache. Mutations go through `refresh_chain` or `update`; lock
> and enrichment helpers live in submodules.

**符号：** fn`compute_proactive_sleep`, fn`shared_api_key_provider`, struct`AuthManager`, struct`SharedAuthKeyProvider`, enum`RefreshReason`, enum`DiskAuthState`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.19 `leader/mod.rs` (2225 行)

> Leader-follower IPC architecture for grok-shell.
> 
> This module implements a single-leader-per-machine architecture where one leader
> process manages the agent state while multiple clients (TUI, IDE extensions, headless)
> communicate via Unix domain sockets.
> 
> # Architecture
> 
> ```text
> ┌─────────────────────────────────────────────────────────────┐
> │                        Leader Process                        │
> │  ┌─────────────────────────────────────────────────────────┐│
> │  │                      Agent (MvpAgent)                    ││
> │  │   - Shared state across all clients                      ││
> │  │   - Persists to ~/.grok/                                 ││
> │  └─────────────────────────────────────────────────────────┘│
> │                           ▲                                  │
> │                           │ ACP                              │
> │  ┌────────────────────────┴────────────────────────────────┐│
> │  │                   IPC Server (Unix Socket)               ││
> │  │   - Routes messages between clients and agent            ││
> │  │   - Namespaces request IDs to avoid collisions           ││
> │  │   - Tracks session ownership for routing                 ││
> │  └────────────────────────┬────────────────────────────────┘│
> └───────────────────────────┼──────────────────────────────────┘
> │ IPC (Unix socket at ~/.grok/leader.sock)
> ┌───────────────────┼───────────────────┐
> ▼                   ▼                   ▼
> ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
> │   TUI Client  │   │  IDE Extension │   │ Headless CLI  │

**符号：** fn`leader_is_older_than`, fn`discover_leaders`, fn`kill_stale_reachable_leaders`, fn`resolve_leader_target`, fn`connect_or_spawn`, fn`wait_for_socket_connectable`, struct`LeaderEnvUrls`, struct`LeaderTargetError`, struct`LiveLeaderInfo`, struct`LeaderDescriptor`, struct`LeaderTargetSelection`, struct`LeaderConnection`, struct`LeaderReconnector`, enum`LeaderDiscoveryState`, enum`LeaderTargetErrorCode`, enum`LeaderTarget`, enum`ConnectionError`, enum`ConnectionStatus`, enum`ReconnectPolicy`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

### 19.20 `leader/server.rs` (6783 行)

> The binary version of the currently running leader process.
> 
> Compared against each registering client's `ClientCapabilities::client_version`
> to detect mismatches early and surface a structured ACP notification.
> In development builds where `VERSION_WITH_COMMIT` is not set, this is
> `"unknown"` and version-mismatch detection is disabled (no notification sent).

**符号：** fn`run_leader_server`, fn`spawn_leader_server`, struct`LeaderServerMetadata`, struct`LeaderServerControlState`, struct`WorkspaceControl`, struct`ServerHandle`, enum`ServerError`

**阅读建议：** 从 `rg 'pub.*fn' {rel}` 找入口；对照单元测试。

## 20. 子系统深读

### 20.1 Compaction

路径：`session/compaction.rs`
- auto-compact
- /compact
- two_pass prefire
- checkpoints/

### 20.2 Rewind

路径：`session/acp_session_impl/rewind.rs`
- rewind_points
- file_state_tracker
- RepairHistory

### 20.3 Workflow

路径：`session/workflow/`
- WorkflowCompletionTurn
- cancel_all_and_drain

### 20.4 Goal

路径：`session/goal_*.rs`
- GoalSummaryTurn
- goal/state.json

### 20.5 MCP

路径：`session/mcp.rs + extensions/mcp.rs`
- Blocking
- OAuth
- CallMcpTool

### 20.6 Subagent

路径：`agent/subagent/`
- coordinator
- SnapshotMcpPool

### 20.7 Hooks

路径：`hook_dispatch.rs`
- pre/post
- x.ai/hooks/run

### 20.8 Upload

路径：`upload/`
- GCS
- manifest
- turn trace

## 21. 调用链 & 术语

**Prompt：** `MvpAgent::prompt` → `SessionCommand::Prompt` → `handle_prompt` → `process_conversation_turn` → `build_request` → `sampler`
**Tool：** `execute_tool_calls` → `prepare_tool_call` → `dispatch_tool` → `WorkspaceOps::call_tool`
**Auth：** `AuthManager::auth` → `try_recover_unauthorized` → `call_with_auth_retry`

- **MvpAgent** — ACP 门面
- **SessionActor** — 单会话状态机
- **SessionHandle** — Send 代理
- **AgentRebuildSpec** — Agent 构建快照
- **GatewaySender** — 推 UI 更新
- **PromptOrigin** — 合成 prompt
- **YOLO** — 自动批准
- **Dormant** — 仅磁盘

## 22. 附录：逐文件补充说明

### 22.1 `active_sessions.rs`

- 规模：282 行 · 目录 `.`
- 摘要：Tracks open TUI sessions in `~/.grok/active_sessions.json` for crash
- 符号：fn`register`, fn`unregister`, fn`try_unregister`, fn`collect_crashed`, fn`register_in`, fn`unregister_in`, fn`try_unregister_in`, fn`collect_crashed_in`
- 调试：`rg -n 'active_sessions' crates/codegen/xai-grok-shell/src`

### 22.2 `agent/activity.rs`

- 规模：411 行 · 目录 `agent`
- 摘要：Send-safe view of the agent's in-flight work, shared with the leader's
- 符号：struct`AgentActivity`
- 调试：`rg -n 'activity' crates/codegen/xai-grok-shell/src`

### 22.3 `agent/app.rs`

- 规模：2343 行 · 目录 `agent`
- 摘要：Configuration for periodic auto-update checking in leader mode.
- 符号：fn`run_auto_update_checker`, fn`run_stdio_agent`, fn`run_headless`, fn`run_headless_no_browser`, fn`run_leader`, struct`LeaderAutoUpdateConfig`
- 调试：`rg -n 'app' crates/codegen/xai-grok-shell/src`

### 22.4 `agent/auth_method.rs`

- 规模：1105 行 · 目录 `agent`
- 摘要：Shared, live handle to the agent's current ACP auth method id.
- 符号：fn`new_shared_auth_method_id`, fn`read_xai_api_key_env`, fn`has_xai_api_key_env`, fn`should_advertise_xai_api_key`, fn`build_auth_methods`, fn`is_session_based_method`, fn`session_token_auth_gate`, fn`method_id_after_cached_token_unavailable`
- 调试：`rg -n 'auth_method' crates/codegen/xai-grok-shell/src`

### 22.5 `agent/chat_modes.rs`

- 规模：323 行 · 目录 `agent`
- 摘要：grok.com chat-product model catalog: caches `/rest/modes` and maps modes to
- 符号：fn`process_chat_mode_enabled`, fn`modes_to_model_state`, struct`ChatModesManager`
- 调试：`rg -n 'chat_modes' crates/codegen/xai-grok-shell/src`

### 22.6 `agent/config.rs`

- 规模：12525 行 · 目录 `agent`
- 摘要：The mode in which the agent is running.
- 符号：fn`default_agent_type`, fn`default_asset_server_url`, fn`env_string`, fn`resolve_compat_cell_with_env`, fn`compat_config_cell`, fn`resolve_compat_sessions_from_raw`, fn`resolve_string_flag`, fn`resolve_enabled`
- 调试：`rg -n 'config' crates/codegen/xai-grok-shell/src`

### 22.7 `agent/config_model_override_parse.rs`

- 规模：870 行 · 目录 `agent`
- 摘要：Resilient parsing for `[model.<id>]` TOML overrides.
- 符号：fn`parse_model_overrides`, fn`log_config_warnings`, struct`ConfigWarning`, struct`ParsedModelOverrides`, enum`ConfigWarningKind`, enum`WarningTarget`
- 调试：`rg -n 'config_model_override_parse' crates/codegen/xai-grok-shell/src`

### 22.8 `agent/ext_parsers.rs`

- 规模：267 行 · 目录 `agent`
- 摘要：Wire-shape parsers for ext-notification params handled by `MvpAgent`.
- 调试：`rg -n 'ext_parsers' crates/codegen/xai-grok-shell/src`

### 22.9 `agent/feedback_client.rs`

- 规模：1355 行 · 目录 `agent`
- 摘要：REST client for feedback collection via cli-chat-proxy.
- 符号：fn`signals_to_update`, fn`snapshot_to_turn_delta`, struct`SessionTurnDelta`, struct`SessionTurnDeltaResponse`, struct`FeedbackApiError`, struct`FeedbackClient`
- 调试：`rg -n 'feedback_client' crates/codegen/xai-grok-shell/src`

### 22.10 `agent/folder_trust.rs`

- 规模：1674 行 · 目录 `agent`
- 摘要：Folder-trust gate ("do you trust this folder?").
- 符号：fn`revoke_folder_trust`, fn`project_scope_allowed`, fn`prompt_warranted`, fn`detected_config_kinds`, fn`agent_inline_hooks_allowed`, fn`record_for_test`, fn`resolve_and_record`, fn`resolve_launch_dir_trust`
- 调试：`rg -n 'folder_trust' crates/codegen/xai-grok-shell/src`

### 22.11 `agent/handlers/mod.rs`

- 规模：3 行 · 目录 `agent/handlers`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.12 `agent/handlers/model_switch.rs`

- 规模：281 行 · 目录 `agent/handlers`
- 摘要：Applies a model switch to a session — the ungated path. `set_session_model`
- 符号：fn`apply`
- 调试：`rg -n 'model_switch' crates/codegen/xai-grok-shell/src`

### 22.13 `agent/handlers/session.rs`

- 规模：291 行 · 目录 `agent/handlers`
- 摘要：Session meta-information handlers.
- 符号：fn`handle`
- 调试：`rg -n 'session' crates/codegen/xai-grok-shell/src`

### 22.14 `agent/handlers/workspaces.rs`

- 规模：174 行 · 目录 `agent/handlers`
- 摘要：—
- 符号：fn`handle`
- 调试：`rg -n 'workspaces' crates/codegen/xai-grok-shell/src`

### 22.15 `agent/init.rs`

- 规模：200 行 · 目录 `agent`
- 摘要：Agent bootstrap and lifecycle hooks.
- 符号：fn`bootstrap`, fn`exit_on_config_error`, fn`update_telemetry_config`
- 调试：`rg -n 'init' crates/codegen/xai-grok-shell/src`

### 22.16 `agent/mod.rs`

- 规模：32 行 · 目录 `agent`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.17 `agent/model_providers.rs`

- 规模：1010 行 · 目录 `agent`
- 摘要：Query parameters folded into every request URL; inherited by models.
- 符号：fn`model_provider_auth_name`, fn`auth_config_issues`, fn`parse_model_providers`, struct`ModelProviderConfig`
- 调试：`rg -n 'model_providers' crates/codegen/xai-grok-shell/src`

### 22.18 `agent/models.rs`

- 规模：3614 行 · 目录 `agent`
- 摘要：Model fetching, resolution, and management.
- 符号：fn`task_model_error_for_catalog`, fn`prefetch_models_blocking`, fn`prefetch_models_and_settings_blocking`, fn`start_early_prefetch_with_auth`, fn`start_early_prefetch`, fn`resolve_catalog_key`, fn`selectable_catalog_key_for_persisted`, fn`resolve_default_model`
- 调试：`rg -n 'models' crates/codegen/xai-grok-shell/src`

### 22.19 `agent/mvp_agent/acp_agent.rs`

- 规模：4113 行 · 目录 `agent/mvp_agent`
- 摘要：[`acp::Agent`] trait implementation for [`MvpAgent`].
- 调试：`rg -n 'acp_agent' crates/codegen/xai-grok-shell/src`

### 22.20 `agent/mvp_agent/agent_ops.rs`

- 规模：3827 行 · 目录 `agent/mvp_agent`
- 摘要：Inherent [`MvpAgent`] helpers (MCP/clients/gateway, settings/models, session ops, spawn).
- 调试：`rg -n 'agent_ops' crates/codegen/xai-grok-shell/src`

### 22.21 `agent/mvp_agent/code_nav.rs`

- 规模：261 行 · 目录 `agent/mvp_agent`
- 摘要：Code-navigation eligibility gating and codebase-index management for [`MvpAgent`].
- 调试：`rg -n 'code_nav' crates/codegen/xai-grok-shell/src`

### 22.22 `agent/mvp_agent/folder_trust_prompt.rs`

- 规模：453 行 · 目录 `agent/mvp_agent`
- 摘要：Interactive folder-trust prompt: a dormant agent→GUI-client ACP round-trip
- 符号：struct`FolderTrustRequest`, struct`FolderTrustResponse`, enum`FolderTrustOutcome`
- 调试：`rg -n 'folder_trust_prompt' crates/codegen/xai-grok-shell/src`

### 22.23 `agent/mvp_agent/heap_profile.rs`

- 规模：352 行 · 目录 `agent/mvp_agent`
- 摘要：Heap-profile monitor wiring for [`MvpAgent`].
- 调试：`rg -n 'heap_profile' crates/codegen/xai-grok-shell/src`

### 22.24 `agent/mvp_agent/mod.rs`

- 规模：2668 行 · 目录 `agent/mvp_agent`
- 摘要：A `'static` reference to a value on a single-threaded `LocalSet`.
- 符号：fn`reject_direct_hub_cloud_meta`, fn`jwt_tier_claim`, fn`resolve_subscription_tier_for_telemetry`, fn`jwt_claim_matches_user_subscription_tier`, fn`parse_session_plugin_dirs`, fn`chat_session_spawn_options`, fn`resolve_session_auto_mode`, fn`build_prompt_response_meta`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.25 `agent/mvp_agent/session_lifecycle.rs`

- 规模：457 行 · 目录 `agent/mvp_agent`
- 摘要：Session lifecycle, roster deltas, and the idle-session supervisor for [`MvpAgent`].
- 符号：struct`RegistrySnapshot`
- 调试：`rg -n 'session_lifecycle' crates/codegen/xai-grok-shell/src`

### 22.26 `agent/mvp_agent/subagent_coordinator.rs`

- 规模：560 行 · 目录 `agent/mvp_agent`
- 摘要：Shell runner adapter and spawn-context construction for [`MvpAgent`].
- 调试：`rg -n 'subagent_coordinator' crates/codegen/xai-grok-shell/src`

### 22.27 `agent/mvp_agent/tests.rs`

- 规模：4845 行 · 目录 `agent/mvp_agent`
- 摘要：Build an unsigned JWT with a `tier` claim (header.payload.sig base64url).
- 调试：`rg -n 'tests' crates/codegen/xai-grok-shell/src`

### 22.28 `agent/proxy.rs`

- 规模：619 行 · 目录 `agent`
- 摘要：HTTP CONNECT proxy support for WebSocket connections.
- 符号：fn`resolve_proxy_for_host`, fn`connect_via_proxy`
- 调试：`rg -n 'proxy' crates/codegen/xai-grok-shell/src`

### 22.29 `agent/relay.rs`

- 规模：1182 行 · 目录 `agent`
- 摘要：WebSocket relay connection management.
- 符号：fn`spawn_relay_connection`, fn`spawn_relay_connection_with_callback`, fn`run_websocket_session`, fn`run_websocket_session_with_liveness`, struct`RelayConfig`, struct`RelayHandle`, enum`SessionEndReason`
- 调试：`rg -n 'relay' crates/codegen/xai-grok-shell/src`

### 22.30 `agent/restore_code.rs`

- 规模：54 行 · 目录 `agent`
- 摘要：Thin wire-format adapter that wraps the shared
- 符号：fn`build_code_restore_meta`
- 调试：`rg -n 'restore_code' crates/codegen/xai-grok-shell/src`

### 22.31 `agent/roster.rs`

- 规模：311 行 · 目录 `agent`
- 摘要：Roster types for the multi-client FleetView dashboard.
- 符号：fn`merge_roster`, struct`RosterEntry`, struct`RosterListResponse`, struct`RosterChanged`, enum`RosterActivity`, enum`RosterOrigin`
- 调试：`rg -n 'roster' crates/codegen/xai-grok-shell/src`

### 22.32 `agent/server.rs`

- 规模：487 行 · 目录 `agent`
- 摘要：WebSocket server for remote agent connections.
- 符号：fn`run_agent_server`, struct`ServerConfig`, struct`WsQueryParams`
- 调试：`rg -n 'server' crates/codegen/xai-grok-shell/src`

### 22.33 `agent/session_config.rs`

- 规模：219 行 · 目录 `agent`
- 摘要：The built-in session-picker modes used when the model has no server list.
- 符号：fn`legacy_session_effort_options`, fn`build_session_config_options`, struct`SessionConfigOption`, struct`GrokSessionDetail`
- 调试：`rg -n 'session_config' crates/codegen/xai-grok-shell/src`

### 22.34 `agent/session_metrics.rs`

- 规模：10 行 · 目录 `agent`
- 摘要：Session lifecycle event structs.
- 调试：`rg -n 'session_metrics' crates/codegen/xai-grok-shell/src`

### 22.35 `agent/session_registry_client.rs`

- 规模：642 行 · 目录 `agent`
- 摘要：REST client for the session replicas registry (cli-chat-proxy).
- 符号：struct`RegisterRequest`, struct`UpdateRequest`, struct`SessionRecord`, struct`SearchResponse`, struct`DownloadResponse`, struct`SessionRegistryClient`
- 调试：`rg -n 'session_registry_client' crates/codegen/xai-grok-shell/src`

### 22.36 `agent/subagent/handle_request.rs`

- 规模：1880 行 · 目录 `agent/subagent`
- 摘要：Runtime adapter for one shell child. Shared lifecycle state is owned by the
- 符号：fn`run_shell_child`
- 调试：`rg -n 'handle_request' crates/codegen/xai-grok-shell/src`

### 22.37 `agent/subagent/mod.rs`

- 规模：2616 行 · 目录 `agent/subagent`
- 摘要：Shell child runtime adapter and presentation.
- 符号：fn`present_child_completion`, fn`resume_inherited_prefix_len`, fn`validate_subagent_type`, fn`subagent_harness_flavor_is_representable`, fn`describe_subagent_type`, fn`read_subagent_output`, fn`reconcile_orphaned_subagents_with_backend`, struct`AutoCompactThresholdTiers`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.38 `agent/subscription_check.rs`

- 规模：195 行 · 目录 `agent`
- 摘要：Subscription check for paywall gate lift.
- 符号：fn`single_check`, struct`UnblockResult`
- 调试：`rg -n 'subscription_check' crates/codegen/xai-grok-shell/src`

### 22.39 `agent/update_chunk_merge.rs`

- 规模：1070 行 · 目录 `agent`
- 摘要：Controls how sampling/output chunks are buffered before being delivered to the client.
- 符号：struct`BufferingSettings`, struct`ReplayBuffer`
- 调试：`rg -n 'update_chunk_merge' crates/codegen/xai-grok-shell/src`

### 22.40 `auth/attribution.rs`

- 规模：954 行 · 目录 `auth`
- 摘要：Shell-side 401-attribution helpers.
- 符号：fn`test_emit_count`, fn`reset_test_emit_count`, fn`record_consumer_401`, fn`record_auth_401`, struct`ShellAttribution`, enum`ConsumerKind`
- 调试：`rg -n 'attribution' crates/codegen/xai-grok-shell/src`

### 22.41 `auth/auth_provider.rs`

- 规模：660 行 · 目录 `auth`
- 摘要：Model auth providers (`[auth_provider.<name>]`).
- 符号：fn`test_backdate_provider_mint`, fn`test_counting_provider`, struct`AuthProviderConfig`, struct`AuthProviderRef`, enum`ProviderRefreshOutcome`
- 调试：`rg -n 'auth_provider' crates/codegen/xai-grok-shell/src`

### 22.42 `auth/config.rs`

- 规模：430 行 · 目录 `auth`
- 摘要：Default scopes for the xAI OAuth2 provider. Includes `grok-cli:access`
- 符号：fn`allowed_accounts_app_origins`, fn`accounts_app_cors_layer`, fn`use_local_auth`, fn`xai_oauth2_issuer`, fn`is_xai_oauth2_issuer`, struct`GrokComConfig`, struct`OidcAuthConfig`, struct`OAuth2ProviderConfig`
- 调试：`rg -n 'config' crates/codegen/xai-grok-shell/src`

### 22.43 `auth/credential_provider.rs`

- 规模：884 行 · 目录 `auth`
- 摘要：`api_key.id` for the active credential: hash the stable API key, never the
- 符号：fn`embedding_session_credentials`, fn`build_storage_client_for_proxy`, fn`wire_otel_auth_manager`, fn`sync_external_otel_identity`, fn`wire_otel_deployment_key`, fn`build_default_otel_layer_config`, struct`ShellAuthCredentialProvider`, struct`StorageClientAttributionBridge`
- 调试：`rg -n 'credential_provider' crates/codegen/xai-grok-shell/src`

### 22.44 `auth/devbox_login_stub.rs`

- 规模：37 行 · 目录 `auth`
- 摘要：Stub for builds without the devbox auth feature.
- 符号：fn`is_devbox_environment`, fn`mint_devbox_auth`, fn`run_devbox_login`
- 调试：`rg -n 'devbox_login_stub' crates/codegen/xai-grok-shell/src`

### 22.45 `auth/device_code.rs`

- 规模：890 行 · 目录 `auth`
- 摘要：RFC 8628 Device Authorization Grant -- CLI side.
- 符号：fn`request_device_code`, fn`complete_device_code_login`, fn`run_device_code_login_channels`, struct`DeviceCode`, enum`DeviceCodeError`, enum`ClientSurface`
- 调试：`rg -n 'device_code' crates/codegen/xai-grok-shell/src`

### 22.46 `auth/error.rs`

- 规模：144 行 · 目录 `auth`
- 摘要：Token expired and no refresh authority available.
- 符号：struct`RefreshTransientError`, struct`RefreshTokenFailedError`, enum`AuthError`, enum`RefreshTokenError`, enum`RefreshTokenFailedReason`
- 调试：`rg -n 'error' crates/codegen/xai-grok-shell/src`

### 22.47 `auth/external_auth.rs`

- 规模：222 行 · 目录 `auth`
- 摘要：Parse stdout into a session-credential `GrokAuth`.
- 符号：fn`parse_output`, fn`run_external_refresh`, fn`refresh_with_command`
- 调试：`rg -n 'external_auth' crates/codegen/xai-grok-shell/src`

### 22.48 `auth/flow.rs`

- 规模：1971 行 · 目录 `auth`
- 摘要：Reject a cached credential for reuse if it lacks `oidc_issuer`, has a
- 符号：fn`run_auth_flow_with_stderr_bridge`, fn`run_auth_flow`, fn`run_auth_flow_interactive`, fn`try_ensure_fresh_auth`, fn`try_ensure_session_noninteractive`, fn`ensure_authenticated`, fn`ensure_authenticated_with_override`, fn`ensure_authenticated_or_noninteractive`
- 调试：`rg -n 'flow' crates/codegen/xai-grok-shell/src`

### 22.49 `auth/jwt.rs`

- 规模：45 行 · 目录 `auth`
- 摘要：JWT expiration detection. Returns `None`/`false` for non-JWT tokens.
- 符号：fn`parse_jwt_expiration`, fn`is_jwt_expired_or_near`
- 调试：`rg -n 'jwt' crates/codegen/xai-grok-shell/src`

### 22.50 `auth/manager/enrichment.rs`

- 规模：256 行 · 目录 `auth/manager`
- 摘要：Background `/user` enrichment spawned by `AuthManager::update()`.
- 调试：`rg -n 'enrichment' crates/codegen/xai-grok-shell/src`

### 22.51 `auth/manager/lock.rs`

- 规模：1187 行 · 目录 `auth/manager`
- 摘要：Advisory `auth.json.lock` helpers (free functions, no `AuthManager`
- 符号：fn`try_lock_auth_file_nonblocking`, fn`try_lock_auth_file_async`
- 调试：`rg -n 'lock' crates/codegen/xai-grok-shell/src`

### 22.52 `auth/manager/sleep_gate.rs`

- 规模：433 行 · 目录 `auth/manager`
- 摘要：System-sleep refresh-straddle mitigation for [`AuthManager`].
- 调试：`rg -n 'sleep_gate' crates/codegen/xai-grok-shell/src`

### 22.53 `auth/manager.rs`

- 规模：2364 行 · 目录 `auth`
- 摘要：`AuthManager` -- single source of truth for `auth.json` + the
- 符号：fn`compute_proactive_sleep`, fn`shared_api_key_provider`, struct`AuthManager`, struct`SharedAuthKeyProvider`, enum`RefreshReason`, enum`DiskAuthState`
- 调试：`rg -n 'manager' crates/codegen/xai-grok-shell/src`

### 22.54 `auth/meta.rs`

- 规模：58 行 · 目录 `auth`
- 摘要：Access gate from `grok_build_access_gate`.
- 符号：struct`GateInfo`, struct`AuthMeta`
- 调试：`rg -n 'meta' crates/codegen/xai-grok-shell/src`

### 22.55 `auth/mod.rs`

- 规模：54 行 · 目录 `auth`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.56 `auth/model.rs`

- 规模：520 行 · 目录 `auth`
- 摘要：Legacy auth.json scope key. Fallback for old devbox auth files.
- 符号：fn`default_coding_data_retention_opt_out`, fn`token_suffix`, fn`lookup_auth`, fn`is_expired`, fn`is_expired_with_buffer`, struct`GrokAuth`, struct`UserInfo`, enum`AuthMode`
- 调试：`rg -n 'model' crates/codegen/xai-grok-shell/src`

### 22.57 `auth/oidc/login.rs`

- 规模：701 行 · 目录 `auth/oidc`
- 摘要：Interactive login orchestration: callback HTTP server, browser
- 符号：fn`callback_page`, fn`run_login_flow`, fn`run_login_flow_with_config`
- 调试：`rg -n 'login' crates/codegen/xai-grok-shell/src`

### 22.58 `auth/oidc/mod.rs`

- 规模：14 行 · 目录 `auth/oidc`
- 摘要：OIDC authentication: protocol, login, and refresh submodules.
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.59 `auth/oidc/protocol.rs`

- 规模：1295 行 · 目录 `auth/oidc`
- 摘要：Pure OIDC protocol mechanics: PKCE, discovery, token exchange,
- 符号：fn`with_alpha_test_key`, fn`is_configured`, fn`peek_access_token_principal`, fn`peek_access_token_principal_id`, fn`resolve_login_principal_policy`, fn`login_principal_policy`, fn`enforce_login_principal`
- 调试：`rg -n 'protocol' crates/codegen/xai-grok-shell/src`

### 22.60 `auth/oidc/refresh.rs`

- 规模：249 行 · 目录 `auth/oidc`
- 摘要：Pure-data OIDC refresh. Talks to the IdP and returns
- 符号：fn`oidc_token_exchange`, enum`OidcRefreshResult`
- 调试：`rg -n 'refresh' crates/codegen/xai-grok-shell/src`

### 22.61 `auth/oidc/test_helpers.rs`

- 规模：130 行 · 目录 `auth/oidc`
- 摘要：Shared test helpers for `oidc::protocol::tests` and `oidc::login::tests`.
- 调试：`rg -n 'test_helpers' crates/codegen/xai-grok-shell/src`

### 22.62 `auth/recovery.rs`

- 规模：1140 行 · 目录 `auth`
- 摘要：Unauthorized (401) recovery state machine.
- 符号：fn`manual_auth_reason`, fn`relay_should_cancel`, struct`RejectedAuth`, struct`ManualAuthTracker`, struct`UnauthorizedRecovery`, enum`RecoverySource`
- 调试：`rg -n 'recovery' crates/codegen/xai-grok-shell/src`

### 22.63 `auth/refresh/external_refresher.rs`

- 规模：113 行 · 目录 `auth/refresh`
- 摘要：Refreshes by re-running the operator's external auth binary via the async
- 符号：struct`ExternalBinaryRefresher`
- 调试：`rg -n 'external_refresher' crates/codegen/xai-grok-shell/src`

### 22.64 `auth/refresh/mod.rs`

- 规模：217 行 · 目录 `auth/refresh`
- 摘要：Callback for diagnostic log upload on auth refresh failure.
- 符号：fn`resolve_refresh_credential`, fn`build_refresher`, enum`RefreshOutcome`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.65 `auth/refresh/oidc_refresher.rs`

- 规模：339 行 · 目录 `auth/refresh`
- 摘要：Escalate to `PermanentFailure` after this many consecutive transient
- 符号：struct`OidcRefresher`
- 调试：`rg -n 'oidc_refresher' crates/codegen/xai-grok-shell/src`

### 22.66 `auth/single_flight.rs`

- 规模：359 行 · 目录 `auth`
- 摘要：Single-flight guard for interactive login.
- 符号：struct`AttemptChannels`, struct`AuthSingleFlight`, struct`AuthAttemptGuard`, enum`SubmitCodeError`
- 调试：`rg -n 'single_flight' crates/codegen/xai-grok-shell/src`

### 22.67 `auth/storage.rs`

- 规模：578 行 · 目录 `auth`
- 摘要：RAII guard for an exclusive advisory lock on `auth.json.lock`.
- 符号：fn`read_auth_json`, fn`read_auth_json_or_empty`, fn`backup_corrupt_auth_file`, fn`read_auth_json_or_empty_recovering_corrupt`, fn`read_token_by_scope`, fn`read_api_key`, fn`store_api_key`, fn`clear_api_key`
- 调试：`rg -n 'storage' crates/codegen/xai-grok-shell/src`

### 22.68 `auth/token_output.rs`

- 规模：155 行 · 目录 `auth`
- 摘要：Shared parser for an auth command's stdout.
- 符号：fn`expiry_after_seconds`, fn`parse_token_output`, struct`ExternalAuthOutput`, struct`ParsedTokenOutput`
- 调试：`rg -n 'token_output' crates/codegen/xai-grok-shell/src`

### 22.69 `auth/token_type.rs`

- 规模：67 行 · 目录 `auth`
- 摘要：What kind of bearer is loaded right now. Dispatch key for
- 符号：enum`TokenType`
- 调试：`rg -n 'token_type' crates/codegen/xai-grok-shell/src`

### 22.70 `bin/chat-history-downgrade.rs`

- 规模：503 行 · 目录 `bin`
- 摘要：normalize chat_history.jsonl, convert any v1 (ConversationItem) to v0 (ChatRequestMessage) format.
- 调试：`rg -n 'chat-history-downgrade' crates/codegen/xai-grok-shell/src`

### 22.71 `bin/test-sampling-server.rs`

- 规模：58 行 · 目录 `bin`
- 摘要：Usage: cargo run -p xai-grok-shell --bin test-sampling-server
- 符号：struct`Cli`
- 调试：`rg -n 'test-sampling-server' crates/codegen/xai-grok-shell/src`

### 22.72 `bin/trace_classify.rs`

- 规模：224 行 · 目录 `bin`
- 摘要：Replay an offline session trace against the Layer-2 TodoGate and
- 调试：`rg -n 'trace_classify' crates/codegen/xai-grok-shell/src`

### 22.73 `builtin.rs`

- 规模：104 行 · 目录 `.`
- 摘要：Built-in files extracted to `~/.grok/` on startup.
- 符号：fn`extract_builtin_files`
- 调试：`rg -n 'builtin' crates/codegen/xai-grok-shell/src`

### 22.74 `bundle.rs`

- 规模：1508 行 · 目录 `.`
- 摘要：—
- 符号：fn`bundled_root`, fn`read_cached_manifest`, fn`write_bundle_to_cache`, fn`extract_bundle_archive`, fn`checksum_bytes`, fn`checksum_file`, fn`prune_removed_files`, fn`count_entries_by_prefix`
- 调试：`rg -n 'bundle' crates/codegen/xai-grok-shell/src`

### 22.75 `claude_import.rs`

- 规模：2186 行 · 目录 `.`
- 摘要：Scope for an import operation.
- 符号：fn`scan_importable_settings`, fn`find_project_root`, fn`is_claude_import_marked`, fn`refresh_marker_cache`, fn`reset_marker_cache_for_test`, fn`is_claude_import_marked_with_log`, fn`is_claude_import_marked_at`, fn`mark_claude_imported`
- 调试：`rg -n 'claude_import' crates/codegen/xai-grok-shell/src`

### 22.76 `claude_import_state.rs`

- 规模：337 行 · 目录 `.`
- 摘要：Persistent import state, loaded from / saved to `~/.grok/claude_import_state.json`.
- 符号：fn`load_import_state`, fn`save_import_state`, fn`has_new_changes`, fn`mark_imported`, fn`mark_dismissed`, struct`ImportState`, struct`ScopeState`
- 调试：`rg -n 'claude_import_state' crates/codegen/xai-grok-shell/src`

### 22.77 `cli_models.rs`

- 规模：337 行 · 目录 `.`
- 摘要：Data APIs for `grok models`. Clients own display.
- 符号：fn`list_models`, enum`AuthStatus`
- 调试：`rg -n 'cli_models' crates/codegen/xai-grok-shell/src`

### 22.78 `config/mod.rs`

- 规模：1878 行 · 目录 `config`
- 摘要：Full configuration for the memory system.
- 符号：fn`config_origins`, fn`apply_managed_settings_features`, fn`apply_requirements`, fn`apply_sandbox`, fn`load_project_config`, fn`resolve_effective_plugins_config`, fn`add_plugin_path`, fn`remove_plugin_path`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.79 `config/reloader.rs`

- 规模：1055 行 · 目录 `config`
- 摘要：Typed, `Send`-safe messages for the agent to apply inside its `LocalSet`.
- 符号：fn`hash_auth_key`, fn`parse_skills_config`, struct`ConfigReloader`, enum`ConfigUpdate`
- 调试：`rg -n 'reloader' crates/codegen/xai-grok-shell/src`

### 22.80 `config/tests.rs`

- 规模：3617 行 · 目录 `config`
- 摘要：Mutex to serialize tests that touch the GROK_MEMORY env var.
- 调试：`rg -n 'tests' crates/codegen/xai-grok-shell/src`

### 22.81 `config/watcher.rs`

- 规模：1302 行 · 目录 `config`
- 摘要：A [`notify::Watcher`] that drops `EventKind::Access` before it reaches the
- 符号：struct`AccessFilteredWatcher`, struct`ConfigFileWatcher`, struct`SkillsFileWatcher`, struct`ProjectDiscoveryWatcher`, enum`ConfigChangeEvent`, enum`DiscoveryChange`
- 调试：`rg -n 'watcher' crates/codegen/xai-grok-shell/src`

### 22.82 `extensions/auth.rs`

- 规模：252 行 · 目录 `extensions`
- 摘要：`x.ai/auth/*` and legacy `x.ai/{get,set}ApiKey` extension handlers.
- 符号：fn`handle`
- 调试：`rg -n 'auth' crates/codegen/xai-grok-shell/src`

### 22.83 `extensions/auth_gate.rs`

- 规模：18 行 · 目录 `extensions`
- 摘要：Require xAI auth from a sync context, accepting tokens in the client-side buffer window.
- 符号：fn`require_xai_auth`
- 调试：`rg -n 'auth_gate' crates/codegen/xai-grok-shell/src`

### 22.84 `extensions/billing.rs`

- 规模：610 行 · 目录 `extensions`
- 摘要：`x.ai/billing` extension handler.
- 符号：fn`handle`, struct`BillingCycle`, struct`Cent`, struct`UsagePeriod`, struct`BillingPeriodUsage`, struct`BillingConfig`, struct`BillingConfigResponse`, struct`AutoTopupRule`
- 调试：`rg -n 'billing' crates/codegen/xai-grok-shell/src`

### 22.85 `extensions/bundle.rs`

- 规模：1172 行 · 目录 `extensions`
- 摘要：ACP extension handlers for bundled subagent cache sync and status.
- 符号：fn`has_bundle_credentials`, fn`handle`, fn`bundle_cache_is_fresh`, fn`maybe_sync_bundle_to_root`, fn`sync_bundle_to_root`, struct`BundleSyncResult`, struct`BundleStatusResult`, struct`PersonaDetail`
- 调试：`rg -n 'bundle' crates/codegen/xai-grok-shell/src`

### 22.86 `extensions/chat_conversation_history.rs`

- 规模：12 行 · 目录 `extensions`
- 摘要：`x.ai/session/load_history`: fetch one older page of a gateway-backed
- 符号：fn`handle`
- 调试：`rg -n 'chat_conversation_history' crates/codegen/xai-grok-shell/src`

### 22.87 `extensions/code_nav.rs`

- 规模：439 行 · 目录 `extensions`
- 摘要：Code Navigation Extension Methods
- 符号：fn`handle`, struct`GotoRequest`, struct`FindSymbolRequest`, struct`StatusRequest`, struct`CodeNavResponse`, struct`SymbolLocation`, struct`StatusResponse`, enum`IndexStatusReason`
- 调试：`rg -n 'code_nav' crates/codegen/xai-grok-shell/src`

### 22.88 `extensions/debug.rs`

- 规模：135 行 · 目录 `extensions`
- 摘要：`x.ai/debug/*` extension handlers for local client testing.
- 符号：fn`handle`
- 调试：`rg -n 'debug' crates/codegen/xai-grok-shell/src`

### 22.89 `extensions/feedback.rs`

- 规模：480 行 · 目录 `extensions`
- 摘要：`x.ai/feedback`, `x.ai/feedback/dismiss`, `x.ai/btw`, and `x.ai/review/*`
- 符号：fn`handle`
- 调试：`rg -n 'feedback' crates/codegen/xai-grok-shell/src`

### 22.90 `extensions/fs.rs`

- 规模：252 行 · 目录 `extensions`
- 摘要：Filesystem extension API layer.
- 符号：fn`is_fs_method`, fn`handle`, struct`FsListRequest`, struct`FsExistsRequest`, struct`FsReadFileRequest`, struct`FsWriteFileRequest`, struct`FsDeleteFileRequest`
- 调试：`rg -n 'fs' crates/codegen/xai-grok-shell/src`

### 22.91 `extensions/git.rs`

- 规模：702 行 · 目录 `extensions`
- 摘要：Git extension API layer.
- 符号：fn`handle`, struct`GitStatusRequest`, struct`GitFilesRequest`, struct`GitDiffsRequest`, struct`GitStageRequest`, struct`GitStageContentRequest`, struct`GitUnstageRequest`, struct`GitDiscardRequest`
- 调试：`rg -n 'git' crates/codegen/xai-grok-shell/src`

### 22.92 `extensions/hooks.rs`

- 规模：563 行 · 目录 `extensions`
- 摘要：`x.ai/hooks/*` extension handlers.
- 符号：fn`hook_spec_to_info`, fn`parse_client_hooks`, fn`reconnect_client_hooks`, fn`handle`, struct`ClientHookGroup`, struct`ClientHookDispatch`, struct`ClientHookResponse`, enum`ClientHookDecision`
- 调试：`rg -n 'hooks' crates/codegen/xai-grok-shell/src`

### 22.93 `extensions/hunk_tracker.rs`

- 规模：1030 行 · 目录 `extensions`
- 摘要：Hunk Tracker extension API layer.
- 符号：fn`handle`, struct`GetHunksRequest`, struct`GetFilesRequest`, struct`HunkActionRequest`, struct`FileActionRequest`, struct`TurnActionRequest`, struct`AllActionRequest`, struct`GetSummaryRequest`
- 调试：`rg -n 'hunk_tracker' crates/codegen/xai-grok-shell/src`

### 22.94 `extensions/interject.rs`

- 规模：122 行 · 目录 `extensions`
- 摘要：`x.ai/interject` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'interject' crates/codegen/xai-grok-shell/src`

### 22.95 `extensions/jj.rs`

- 规模：64 行 · 目录 `extensions`
- 摘要：Jujutsu extension handlers — delegates to [`xai_grok_workspace::session::jj`].
- 符号：fn`try_handle`
- 调试：`rg -n 'jj' crates/codegen/xai-grok-shell/src`

### 22.96 `extensions/marketplace.rs`

- 规模：1973 行 · 目录 `extensions`
- 摘要：`x.ai/marketplace/*` extension handlers.
- 符号：fn`handle`, fn`purge_default_skills_installs`, fn`ensure_official_marketplace_source`
- 调试：`rg -n 'marketplace' crates/codegen/xai-grok-shell/src`

### 22.97 `extensions/mcp.rs`

- 规模：2524 行 · 目录 `extensions`
- 摘要：MCP extension methods and business logic.
- 符号：fn`notify_servers_updated`, fn`handle`, fn`build_mcp_catalog`, fn`build_mcp_catalog_with_gateway_tools`, fn`build_mcp_status`, fn`init_agent_mcp_pool`, fn`call_mcp_tool`, fn`read_mcp_resource`
- 调试：`rg -n 'mcp' crates/codegen/xai-grok-shell/src`

### 22.98 `extensions/memory.rs`

- 规模：101 行 · 目录 `extensions`
- 摘要：`x.ai/memory/flush`, `x.ai/memory/rewrite`, and `x.ai/compact_conversation`
- 符号：fn`handle`
- 调试：`rg -n 'memory' crates/codegen/xai-grok-shell/src`

### 22.99 `extensions/mod.rs`

- 规模：90 行 · 目录 `extensions`
- 摘要：Deserialize ACP params from their raw JSON string, mapping a parse failure
- 符号：fn`parse_params`, fn`parse_params_str`, fn`parse_session_id`, fn`to_ext_response`, fn`to_raw_response`, fn`to_ext_response_partial`, struct`Empty`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.100 `extensions/notification.rs`

- 规模：2389 行 · 目录 `extensions`
- 摘要：Retained for wire backwards compatibility; always empty in the
- 符号：fn`ticks_to_usd`, fn`uncached_input_tokens`, fn`project_result_usage`, fn`attach_result_usage_fail_closed`, fn`is_reauthable_failure`, struct`GoalDeliverableInfo`, struct`WorkflowPhaseInfo`, struct`WorkflowAgentInfo`
- 调试：`rg -n 'notification' crates/codegen/xai-grok-shell/src`

### 22.101 `extensions/plugins.rs`

- 规模：307 行 · 目录 `extensions`
- 摘要：`x.ai/plugins/*` extension handlers.
- 符号：fn`loaded_plugin_to_info`, fn`handle`
- 调试：`rg -n 'plugins' crates/codegen/xai-grok-shell/src`

### 22.102 `extensions/pr.rs`

- 规模：253 行 · 目录 `extensions`
- 摘要：`gh pr view --json` does not expose `isInMergeQueue`; query GraphQL via `gh api`.
- 符号：fn`handle`, struct`PrStatusRequest`, struct`PrStatusResponse`, struct`PrData`
- 调试：`rg -n 'pr' crates/codegen/xai-grok-shell/src`

### 22.103 `extensions/privacy.rs`

- 规模：91 行 · 目录 `extensions`
- 摘要：`x.ai/privacy/setCodingDataRetention` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'privacy' crates/codegen/xai-grok-shell/src`

### 22.104 `extensions/prompt_history.rs`

- 规模：162 行 · 目录 `extensions`
- 摘要：`x.ai/prompt_history` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'prompt_history' crates/codegen/xai-grok-shell/src`

### 22.105 `extensions/prompt_meta.rs`

- 规模：71 行 · 目录 `extensions`
- 摘要：Typed metadata for a prompt `TextContent._meta` field.
- 符号：struct`PromptBlockMeta`
- 调试：`rg -n 'prompt_meta' crates/codegen/xai-grok-shell/src`

### 22.106 `extensions/recap.rs`

- 规模：58 行 · 目录 `extensions`
- 摘要：`x.ai/recap` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'recap' crates/codegen/xai-grok-shell/src`

### 22.107 `extensions/repair.rs`

- 规模：295 行 · 目录 `extensions`
- 摘要：`x.ai/session/repair` — out-of-band recovery for sessions bricked by
- 符号：fn`handle`, struct`RepairSessionResponse`
- 调试：`rg -n 'repair' crates/codegen/xai-grok-shell/src`

### 22.108 `extensions/rewind.rs`

- 规模：111 行 · 目录 `extensions`
- 摘要：`x.ai/rewind/*` extension handlers.
- 符号：fn`handle`
- 调试：`rg -n 'rewind' crates/codegen/xai-grok-shell/src`

### 22.109 `extensions/rollout.rs`

- 规模：46 行 · 目录 `extensions`
- 摘要：`x.ai/rollout/survey` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'rollout' crates/codegen/xai-grok-shell/src`

### 22.110 `extensions/routing.rs`

- 规模：143 行 · 目录 `extensions`
- 摘要：Metadata from the request, used for routing notifications back to the
- 符号：fn`inject_routing_meta`, fn`send_routed_notification`, struct`RequestMeta`, struct`NotificationMeta`
- 调试：`rg -n 'routing' crates/codegen/xai-grok-shell/src`

### 22.111 `extensions/search.rs`

- 规模：354 行 · 目录 `extensions`
- 摘要：Search extension API layer (fuzzy file search, content search).
- 符号：fn`handle`, struct`FuzzyOpenResponse`, struct`FuzzyChangeResponse`, struct`FuzzyCloseResponse`, struct`FuzzyOpenRequest`, struct`FuzzyChangeRequest`, struct`FuzzyCloseRequest`, struct`ContentSearchRequest`
- 调试：`rg -n 'search' crates/codegen/xai-grok-shell/src`

### 22.112 `extensions/session_admin.rs`

- 规模：747 行 · 目录 `extensions`
- 摘要：Session-administration extension handlers.
- 符号：fn`handle`
- 调试：`rg -n 'session_admin' crates/codegen/xai-grok-shell/src`

### 22.113 `extensions/session_search.rs`

- 规模：117 行 · 目录 `extensions`
- 摘要：ACP extension handler for session search (`x.ai/session/search`).
- 符号：fn`handle`, struct`SearchSessionsRequest`, struct`SearchSessionsResponse`, struct`SearchSessionHit`
- 调试：`rg -n 'session_search' crates/codegen/xai-grok-shell/src`

### 22.114 `extensions/session_state.rs`

- 规模：309 行 · 目录 `extensions`
- 摘要：`x.ai/session/state` reads a session's metadata columns; `x.ai/session/import`
- 符号：fn`handle_state`, fn`handle_import`
- 调试：`rg -n 'session_state' crates/codegen/xai-grok-shell/src`

### 22.115 `extensions/session_updates.rs`

- 规模：1035 行 · 目录 `extensions`
- 摘要：ACP extension handler for bulk session updates (`x.ai/session/updates`).
- 符号：fn`handle`
- 调试：`rg -n 'session_updates' crates/codegen/xai-grok-shell/src`

### 22.116 `extensions/share.rs`

- 规模：290 行 · 目录 `extensions`
- 摘要：`x.ai/share_session` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'share' crates/codegen/xai-grok-shell/src`

### 22.117 `extensions/skills.rs`

- 规模：676 行 · 目录 `extensions`
- 摘要：Generic params for methods that only need an optional `cwd`.
- 符号：fn`handle`, struct`SkillsAddRequest`, struct`SkillsAddResponse`, struct`SkillsRemoveRequest`, struct`SkillsRemoveResponse`, struct`SkillsResetResponse`, struct`SkillsToggleRequest`, struct`SkillsListRequest`
- 调试：`rg -n 'skills' crates/codegen/xai-grok-shell/src`

### 22.118 `extensions/suggest/ai_provider.rs`

- 规模：215 行 · 目录 `extensions/suggest`
- 摘要：Request AI-powered shell command suggestions via the session actor.
- 符号：fn`suggest`
- 调试：`rg -n 'ai_provider' crates/codegen/xai-grok-shell/src`

### 22.119 `extensions/suggest/file_provider.rs`

- 规模：1147 行 · 目录 `extensions/suggest`
- 摘要：Filesystem completion for the shell token under the cursor: any
- 符号：struct`FilePathProvider`
- 调试：`rg -n 'file_provider' crates/codegen/xai-grok-shell/src`

### 22.120 `extensions/suggest/history_provider.rs`

- 规模：727 行 · 目录 `extensions/suggest`
- 摘要：Rank history matches from three tiers of history sources.
- 符号：struct`HistoryProvider`
- 调试：`rg -n 'history_provider' crates/codegen/xai-grok-shell/src`

### 22.121 `extensions/suggest/mod.rs`

- 规模：680 行 · 目录 `extensions/suggest`
- 摘要：Deterministic Tab mode: run only the token providers (path/file).
- 符号：fn`handle`, fn`should_skip_ai`, struct`SuggestContext`, struct`RankedSuggestion`, enum`SuggestionSource`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.122 `extensions/suggest/path_provider.rs`

- 规模：407 行 · 目录 `extensions/suggest`
- 摘要：The command token being typed, via the canonical tokenizer: quotes hide
- 符号：struct`PathProvider`
- 调试：`rg -n 'path_provider' crates/codegen/xai-grok-shell/src`

### 22.123 `extensions/suggest/shell_token.rs`

- 规模：640 行 · 目录 `extensions/suggest`
- 摘要：Minimal shell-token syntax for completion: find the token under the
- 调试：`rg -n 'shell_token' crates/codegen/xai-grok-shell/src`

### 22.124 `extensions/task.rs`

- 规模：886 行 · 目录 `extensions`
- 摘要：Wire DTO for the `x.ai/task/kill` ext request.
- 符号：fn`handle`, fn`handle_scheduler`, fn`handle_subagent`, struct`KillTaskRequest`, struct`KillTaskResponse`, struct`CancelSubagentRequest`, struct`CancelSubagentResponse`, enum`SubagentCancelOutcomeDto`
- 调试：`rg -n 'task' crates/codegen/xai-grok-shell/src`

### 22.125 `extensions/terminal.rs`

- 规模：366 行 · 目录 `extensions`
- 摘要：Response for any terminal creation — piped or PTY. Both return just a `terminalId`.
- 符号：fn`handle`, fn`handle_pty_input`, struct`EnvVar`, struct`CreateTerminalRequest`, struct`TerminalIdRequest`, struct`CreateTerminalResponse`, struct`PtyCreateRequest`, struct`PtyLoadRequest`
- 调试：`rg -n 'terminal' crates/codegen/xai-grok-shell/src`

### 22.126 `extensions/usage.rs`

- 规模：104 行 · 目录 `extensions`
- 摘要：`x.ai/session/usage` — cumulative session token/cost as [`PromptUsage`].
- 符号：fn`handle`, struct`SessionUsageResponse`
- 调试：`rg -n 'usage' crates/codegen/xai-grok-shell/src`

### 22.127 `extensions/worktree.rs`

- 规模：619 行 · 目录 `extensions`
- 摘要：Handler for x.ai/git/worktree/* extension methods.
- 符号：fn`handle`, struct`ListWorktreeRequest`, struct`ShowWorktreeRequest`, struct`GcWorktreeRequest`, struct`WorktreeDbPathResponse`, struct`ResolveLocalForWorktreeResumeRequest`, struct`ResolveLocalForWorktreeResumeResponse`
- 调试：`rg -n 'worktree' crates/codegen/xai-grok-shell/src`

### 22.128 `heap_profile/mod.rs`

- 规模：211 行 · 目录 `heap_profile`
- 摘要：Heap-profile IoC seam + threshold monitor.
- 符号：fn`install`, fn`stats`, fn`set_prof_active`, fn`dump_to_path`, fn`prof_available`, struct`HeapProfileHooks`, struct`JemallocStats`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.129 `heap_profile/monitor.rs`

- 规模：1125 行 · 目录 `heap_profile`
- 摘要：Threshold-triggered jemalloc heap dump + upload.
- 符号：fn`should_latch`, fn`is_valid_session_id`, fn`sanitize_version`, fn`object_paths`, fn`normalize_thresholds`, fn`clamp_poll_interval_secs`, fn`resolve_jemalloc_heap_profile`, fn`build_upload_handles`
- 调试：`rg -n 'monitor' crates/codegen/xai-grok-shell/src`

### 22.130 `inspect/compat.rs`

- 规模：305 行 · 目录 `inspect`
- 摘要：Vendor-compat resolution for `grok inspect`.
- 符号：struct`ExternalCompatEntry`, struct`ExternalCompatReport`, enum`CompatEntryStatus`, enum`CompatSource`
- 调试：`rg -n 'compat' crates/codegen/xai-grok-shell/src`

### 22.131 `inspect/mod.rs`

- 规模：2012 行 · 目录 `inspect`
- 摘要：`grok inspect` — configuration introspection.
- 符号：fn`inspect`, struct`InspectReport`, struct`InstructionFile`, struct`PermissionsReport`, struct`EnforcedPolicy`, struct`SkippedRule`, struct`LoginPolicyReport`, struct`HookEntry`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.132 `instrumentation.rs`

- 规模：67 行 · 目录 `.`
- 摘要：Shim — see `xai_grok_telemetry::instrumentation` for the implementation.
- 符号：fn`finalize_and_exit`
- 调试：`rg -n 'instrumentation' crates/codegen/xai-grok-shell/src`

### 22.133 `leader/client.rs`

- 规模：1308 行 · 目录 `leader`
- 摘要：Interval for sending keepalive pings to detect dead connections
- 符号：struct`LeaderRegistration`, struct`LeaderClient`, enum`DisconnectReason`, enum`ClientError`
- 调试：`rg -n 'client' crates/codegen/xai-grok-shell/src`

### 22.134 `leader/lock.rs`

- 规模：601 行 · 目录 `leader`
- 摘要：Compute a short hash suffix from a WS URL for differentiating leader instances.
- 符号：fn`compute_ws_url_suffix`, fn`lock_path_for_ws_url_in`, fn`lock_path_for_ws_url`, fn`socket_path_for_ws_url_in`, fn`socket_path_for_ws_url`, fn`ws_url_suffix_from_paths`, struct`LeaderLock`, enum`LockError`
- 调试：`rg -n 'lock' crates/codegen/xai-grok-shell/src`

### 22.135 `leader/mod.rs`

- 规模：2225 行 · 目录 `leader`
- 摘要：Leader-follower IPC architecture for grok-shell.
- 符号：fn`leader_is_older_than`, fn`discover_leaders`, fn`kill_stale_reachable_leaders`, fn`resolve_leader_target`, fn`connect_or_spawn`, fn`wait_for_socket_connectable`, struct`LeaderEnvUrls`, struct`LeaderTargetError`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.136 `leader/protocol.rs`

- 规模：734 行 · 目录 `leader`
- 摘要：Unique identifier for a connected client.
- 符号：fn`read_frame`, fn`write_frame`, fn`read_message`, fn`write_message`, struct`ClientId`, struct`ClientCapabilities`, struct`LeaderCapabilities`, enum`ProtocolError`
- 调试：`rg -n 'protocol' crates/codegen/xai-grok-shell/src`

### 22.137 `leader/server.rs`

- 规模：6783 行 · 目录 `leader`
- 摘要：The binary version of the currently running leader process.
- 符号：fn`run_leader_server`, fn`spawn_leader_server`, struct`LeaderServerMetadata`, struct`LeaderServerControlState`, struct`WorkspaceControl`, struct`ServerHandle`, enum`ServerError`
- 调试：`rg -n 'server' crates/codegen/xai-grok-shell/src`

### 22.138 `leader/test_support.rs`

- 规模：178 行 · 目录 `leader`
- 摘要：In-crate fake leaders for exercising client-side handling of misbehaving
- 符号：fn`fake_caps`, fn`spawn_fake_leader`, struct`FakeVersions`, struct`FakeLeaderHandle`, enum`FakeLeaderBehavior`
- 调试：`rg -n 'test_support' crates/codegen/xai-grok-shell/src`

### 22.139 `leader/transport.rs`

- 规模：304 行 · 目录 `leader`
- 摘要：Cross-platform IPC transport for leader<->client communication.
- 符号：fn`listener_is_ready`
- 调试：`rg -n 'transport' crates/codegen/xai-grok-shell/src`

### 22.140 `lib.rs`

- 规模：46 行 · 目录 `.`
- 摘要：—
- 调试：`rg -n 'lib' crates/codegen/xai-grok-shell/src`

### 22.141 `managed_config/response.rs`

- 规模：257 行 · 目录 `managed_config`
- 摘要：The deployment-config fetch/response contract: the credential source and its
- 符号：enum`ManagedConfigError`
- 调试：`rg -n 'response' crates/codegen/xai-grok-shell/src`

### 22.142 `managed_config/tests.rs`

- 规模：446 行 · 目录 `managed_config`
- 摘要：Fail closed only for a managed principal AND compromised policy; every other combination proceeds.
- 调试：`rg -n 'tests' crates/codegen/xai-grok-shell/src`

### 22.143 `managed_config.rs`

- 规模：1123 行 · 目录 `.`
- 摘要：Sync `managed_config.toml` + `requirements.toml` from the deployment-config endpoint per principal.
- 符号：fn`has_active_team_auth`, fn`clear_orphan`, fn`spawn_sync`, fn`resolve_deployment_id`, fn`resolve_deployment_key`, fn`is_fetch_enabled`, fn`sync`, fn`post_login_sync`
- 调试：`rg -n 'managed_config' crates/codegen/xai-grok-shell/src`

### 22.144 `mcp_doctor.rs`

- 规模：737 行 · 目录 `.`
- 摘要：`grok mcp doctor` -- runtime health check for MCP servers.
- 符号：fn`run_doctor`, fn`print_report`, struct`ConfigSourceStatus`, struct`McpServerStatus`, struct`Check`, struct`DoctorReport`, enum`ConfigSourceState`
- 调试：`rg -n 'mcp_doctor' crates/codegen/xai-grok-shell/src`

### 22.145 `plugin.rs`

- 规模：2234 行 · 目录 `.`
- 摘要：Shared plugin lifecycle operations (output-agnostic).
- 符号：fn`install_source_is_local`, fn`install_plugin`, fn`uninstall_plugin`, fn`repo_update_requires_reload`, fn`update_plugins`, fn`update_plugins_by_selector`, fn`normalize_git_url`, fn`name_from_url`
- 调试：`rg -n 'plugin' crates/codegen/xai-grok-shell/src`

### 22.146 `relay/mod.rs`

- 规模：17 行 · 目录 `relay`
- 摘要：Relay session sharing module.
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.147 `relay/sync.rs`

- 规模：1141 行 · 目录 `relay`
- 摘要：WebSocket relay sync for real-time session sharing.
- 符号：fn`build_share_url`, struct`RelaySyncState`, struct`SyncStatus`, struct`RelaySync`, enum`ConnectionState`
- 调试：`rg -n 'sync' crates/codegen/xai-grok-shell/src`

### 22.148 `relay/types.rs`

- 规模：51 行 · 目录 `relay`
- 摘要：Shared types for relay session sharing.
- 符号：enum`AgentType`
- 调试：`rg -n 'types' crates/codegen/xai-grok-shell/src`

### 22.149 `remote/agent.rs`

- 规模：327 行 · 目录 `remote`
- 摘要：Remote sandbox client for cli-chat-proxy.
- 符号：struct`SandboxClient`
- 调试：`rg -n 'agent' crates/codegen/xai-grok-shell/src`

### 22.150 `remote/chat_models_client.rs`

- 规模：206 行 · 目录 `remote`
- 摘要：grok.com chat-product model catalog (`POST /rest/modes`) — the models
- 符号：struct`Mode`, struct`ModeAvailability`, struct`ListModesResponse`, struct`ChatModelsClient`, enum`ChatModelsError`
- 调试：`rg -n 'chat_models_client' crates/codegen/xai-grok-shell/src`

### 22.151 `remote/client.rs`

- 规模：2115 行 · 目录 `remote`
- 摘要：HTTP client for backend CRUD operations.
- 符号：fn`share_url`, fn`fetch_subagent_bundle`, fn`fetch_bundle`, fn`fetch_settings_blocking`, fn`fetch_login_device_flow`, fn`models_list_url`, fn`fetch_models_blocking`, fn`parse_remote_model_value`
- 调试：`rg -n 'client' crates/codegen/xai-grok-shell/src`

### 22.152 `remote/conversations_client.rs`

- 规模：309 行 · 目录 `remote`
- 摘要：Body for `PUT /rest/app-chat/conversations/{id}` (grok-web `chatUpdateConversation`).
- 符号：struct`Conversation`, struct`Workspace`, struct`ConvQuery`, struct`ListConversationsPage`, struct`UpdateConversationBody`, struct`ConversationsClient`, enum`ConvError`
- 调试：`rg -n 'conversations_client' crates/codegen/xai-grok-shell/src`

### 22.153 `remote/mod.rs`

- 规模：37 行 · 目录 `remote`
- 摘要：Remote storage client for the backend.
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.154 `remote/pull.rs`

- 规模：458 行 · 目录 `remote`
- 摘要：Pull-on-miss: fetch a session from the backend and hydrate local JSONL storage.
- 符号：fn`pull_session_to_local`, enum`PullResult`
- 调试：`rg -n 'pull' crates/codegen/xai-grok-shell/src`

### 22.155 `remote/pull_smoke_test.rs`

- 规模：127 行 · 目录 `remote`
- 摘要：Push → pull round-trip smoke test against the live backend.
- 调试：`rg -n 'pull_smoke_test' crates/codegen/xai-grok-shell/src`

### 22.156 `remote/sync.rs`

- 规模：182 行 · 目录 `remote`
- 摘要：Writeback push: async queue that flushes session updates to the backend.
- 符号：struct`RemoteSync`
- 调试：`rg -n 'sync' crates/codegen/xai-grok-shell/src`

### 22.157 `remote/workspaces_client.rs`

- 规模：180 行 · 目录 `remote`
- 摘要：—
- 符号：struct`Workspace`, struct`WsQuery`, struct`ListWorkspacesPage`, struct`WorkspacesClient`, enum`WsError`
- 调试：`rg -n 'workspaces_client' crates/codegen/xai-grok-shell/src`

### 22.158 `sampling/conversation.rs`

- 规模：40 行 · 目录 `sampling`
- 摘要：API-agnostic conversation representation.
- 符号：struct`ConversationRequestTrace`
- 调试：`rg -n 'conversation' crates/codegen/xai-grok-shell/src`

### 22.159 `sampling/error.rs`

- 规模：774 行 · 目录 `sampling`
- 摘要：Sampling error types.
- 符号：fn`is_free_usage_exhausted_error`, fn`format_rate_limited_user_message`, fn`map_sampling_err_to_acp`, fn`error_data_with_status`, fn`terminal_error_data`, fn`stop_reason_for_turn_error`, fn`error_detail_from_data`, fn`http_status_from_error`
- 调试：`rg -n 'error' crates/codegen/xai-grok-shell/src`

### 22.160 `sampling/mod.rs`

- 规模：32 行 · 目录 `sampling`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.161 `sampling/types.rs`

- 规模：64 行 · 目录 `sampling`
- 摘要：Render an `ImageContent` produced by the read-file tool as a URL
- 符号：fn`get_image_content_url`
- 调试：`rg -n 'types' crates/codegen/xai-grok-shell/src`

### 22.162 `session/acp_conversion.rs`

- 规模：1328 行 · 目录 `session`
- 摘要：ACP conversion functions for `xai-grok-tools`'s `ToolOutput`.
- 符号：fn`maybe_rewrite`, fn`raw_output_json`, fn`acp_tool_update`, fn`acp_plan_update`, struct`PathRewriter`
- 调试：`rg -n 'acp_conversion' crates/codegen/xai-grok-shell/src`

### 22.163 `session/acp_mcp.rs`

- 规模：144 行 · 目录 `session`
- 摘要：In-process SDK MCP servers over the ACP reverse channel (`x.ai/mcp/sdk_call`).
- 符号：fn`parse_acp_mcp_servers`, struct`GatewayAcpInvoker`
- 调试：`rg -n 'acp_mcp' crates/codegen/xai-grok-shell/src`

### 22.164 `session/acp_session/hooks.rs`

- 规模：566 行 · 目录 `session/acp_session`
- 摘要：Client-registered hooks for [`SessionActor`].
- 调试：`rg -n 'hooks' crates/codegen/xai-grok-shell/src`

### 22.165 `session/acp_session.rs`

- 规模：2070 行 · 目录 `session`
- 摘要：Session actor implementation for the MVP ACP agent.
- 符号：fn`is_session_idle_for_injection`, fn`state_is_busy`, fn`load_system_prompt`, fn`load_prompt_context`, struct`InputItem`, struct`State`, struct`PreparedToolCall`, struct`ModelAuthMemo`
- 调试：`rg -n 'acp_session' crates/codegen/xai-grok-shell/src`

### 22.166 `session/acp_session_impl/extensions/idle_prompt.rs`

- 规模：130 行 · 目录 `session/acp_session_impl/extensions`
- 摘要：Debounced `idle_prompt` notification extension.
- 调试：`rg -n 'idle_prompt' crates/codegen/xai-grok-shell/src`

### 22.167 `session/acp_session_impl/extensions.rs`

- 规模：57 行 · 目录 `session/acp_session_impl`
- 摘要：Composition root for the session's extensions: each lives in its own submodule and installs itself here.
- 调试：`rg -n 'extensions' crates/codegen/xai-grok-shell/src`

### 22.168 `session/acp_session_impl/goal.rs`

- 规模：2323 行 · 目录 `session/acp_session_impl`
- 摘要：Goal-orchestration concern for `SessionActor`.
- 符号：struct`PanelResolveCache`, enum`RoleCapability`, enum`GapsUpdate`
- 调试：`rg -n 'goal' crates/codegen/xai-grok-shell/src`

### 22.169 `session/acp_session_impl/goal_support.rs`

- 规模：1586 行 · 目录 `session/acp_session_impl`
- 摘要：Goal-harness support for `SessionActor`: reminder/directive templates,
- 符号：fn`planner_failure_pause_message`, fn`goal_slash_and_harness_available`, fn`laziness_injection_active`, struct`GoalClassifierPolicy`, struct`SubagentTokenRecord`, struct`GoalRoleModelConfig`
- 调试：`rg -n 'goal_support' crates/codegen/xai-grok-shell/src`

### 22.170 `session/acp_session_impl/hook_dispatch.rs`

- 规模：385 行 · 目录 `session/acp_session_impl`
- 摘要：Hook dispatch concern for `SessionActor`: run contexts, hook execution
- 调试：`rg -n 'hook_dispatch' crates/codegen/xai-grok-shell/src`

### 22.171 `session/acp_session_impl/hooks_plugins.rs`

- 规模：1050 行 · 目录 `session/acp_session_impl`
- 摘要：Trust the current project via the unified folder-trust store. Now an
- 调试：`rg -n 'hooks_plugins' crates/codegen/xai-grok-shell/src`

### 22.172 `session/acp_session_impl/interjection.rs`

- 规模：338 行 · 目录 `session/acp_session_impl`
- 摘要：Mid-turn interjection concern for `SessionActor` (buffer type, formatting,
- 调试：`rg -n 'interjection' crates/codegen/xai-grok-shell/src`

### 22.173 `session/acp_session_impl/laziness.rs`

- 规模：884 行 · 目录 `session/acp_session_impl`
- 摘要：Laziness / stop-detector concern for `SessionActor`.
- 符号：fn`build_laziness_debug_line`, fn`classify_debug_decision`, fn`append_laziness_debug_log_line`, struct`LazinessFireMeta`, struct`DebugClassifierOutput`, struct`DebugTodoSnapshot`, struct`LazinessDebugLogLine`, enum`LazinessFireOutcome`
- 调试：`rg -n 'laziness' crates/codegen/xai-grok-shell/src`

### 22.174 `session/acp_session_impl/laziness_classifier.rs`

- 规模：869 行 · 目录 `session/acp_session_impl`
- 摘要：Layer-3 LazinessDetector pure helpers: classifier prompt/config consts,
- 符号：fn`turn_elapsed_seconds_from_start_ms`, fn`format_runtime_state_line`, fn`flatten_transcript_for_classifier`, fn`neutralize_transcript_user_text`, fn`build_classifier_turns`, fn`agents_md_classifier_body`, fn`should_set_classifier_project_instructions`, fn`laziness_window_start`
- 调试：`rg -n 'laziness_classifier' crates/codegen/xai-grok-shell/src`

### 22.175 `session/acp_session_impl/mcp.rs`

- 规模：1881 行 · 目录 `session/acp_session_impl`
- 摘要：Wait for MCP tools to be initialized.
- 调试：`rg -n 'mcp' crates/codegen/xai-grok-shell/src`

### 22.176 `session/acp_session_impl/mcp_snapshot.rs`

- 规模：441 行 · 目录 `session/acp_session_impl`
- 摘要：MCP snapshot concern for `SessionActor`: server-snapshot refresh and
- 符号：fn`refresh_mcp_snapshot_for_test`, fn`refresh_mcp_snapshot_for_test_with_disabled`
- 调试：`rg -n 'mcp_snapshot' crates/codegen/xai-grok-shell/src`

### 22.177 `session/acp_session_impl/memory_dream.rs`

- 规模：768 行 · 目录 `session/acp_session_impl`
- 摘要：Memory concern for `SessionActor`: memory flush, the dream pipeline,
- 调试：`rg -n 'memory_dream' crates/codegen/xai-grok-shell/src`

### 22.178 `session/acp_session_impl/model_switch.rs`

- 规模：342 行 · 目录 `session/acp_session_impl`
- 摘要：Handle [`SessionCommand::RebuildAgentForDefinition`].
- 调试：`rg -n 'model_switch' crates/codegen/xai-grok-shell/src`

### 22.179 `session/acp_session_impl/notification_drain.rs`

- 规模：626 行 · 目录 `session/acp_session_impl`
- 摘要：Idle-gated pending-notification buffering and drain for `SessionActor`,
- 符号：struct`PendingNotification`
- 调试：`rg -n 'notification_drain' crates/codegen/xai-grok-shell/src`

### 22.180 `session/acp_session_impl/prompt_build.rs`

- 规模：906 行 · 目录 `session/acp_session_impl`
- 摘要：User-message construction concern for `SessionActor`: templated prefix
- 调试：`rg -n 'prompt_build' crates/codegen/xai-grok-shell/src`

### 22.181 `session/acp_session_impl/prompt_queue.rs`

- 规模：955 行 · 目录 `session/acp_session_impl`
- 摘要：Running-turn display fields for `x.ai/queue/changed` (clients paint turn-start UI).
- 调试：`rg -n 'prompt_queue' crates/codegen/xai-grok-shell/src`

### 22.182 `session/acp_session_impl/recap.rs`

- 规模：691 行 · 目录 `session/acp_session_impl`
- 摘要：Auxiliary model-call concern for `SessionActor`: side questions, recap
- 调试：`rg -n 'recap' crates/codegen/xai-grok-shell/src`

### 22.183 `session/acp_session_impl/reminders.rs`

- 规模：861 行 · 目录 `session/acp_session_impl`
- 摘要：System-reminder injection concern for `SessionActor`: reminder policy,
- 符号：fn`evaluate_todo_gate`, fn`resolve_reminder_policy`, fn`date_rollover_reminder`, struct`CollectedTodoGateInput`, struct`TodoGateInput`
- 调试：`rg -n 'reminders' crates/codegen/xai-grok-shell/src`

### 22.184 `session/acp_session_impl/rewind.rs`

- 规模：583 行 · 目录 `session/acp_session_impl`
- 摘要：Rewind concern for `SessionActor`: rewind points, cross-compaction
- 调试：`rg -n 'rewind' crates/codegen/xai-grok-shell/src`

### 22.185 `session/acp_session_impl/run_loop.rs`

- 规模：2239 行 · 目录 `session/acp_session_impl`
- 摘要：The session actor's main loop (`run_session`): command dispatch, idle
- 调试：`rg -n 'run_loop' crates/codegen/xai-grok-shell/src`

### 22.186 `session/acp_session_impl/sampler_turn.rs`

- 规模：1490 行 · 目录 `session/acp_session_impl`
- 摘要：Sampler-turn pipeline for `SessionActor`: tool definitions, model auth
- 调试：`rg -n 'sampler_turn' crates/codegen/xai-grok-shell/src`

### 22.187 `session/acp_session_impl/session_mode.rs`

- 规模：364 行 · 目录 `session/acp_session_impl`
- 摘要：Session/plan-mode concern for `SessionActor` (`handle_session_mode`,
- 调试：`rg -n 'session_mode' crates/codegen/xai-grok-shell/src`

### 22.188 `session/acp_session_impl/session_setup.rs`

- 规模：639 行 · 目录 `session/acp_session_impl`
- 摘要：Session initialization concern for `SessionActor`: `initialize`, prefix
- 调试：`rg -n 'session_setup' crates/codegen/xai-grok-shell/src`

### 22.189 `session/acp_session_impl/slash_exec.rs`

- 规模：1020 行 · 目录 `session/acp_session_impl`
- 摘要：Execute a built-in slash command (e.g. `/compact`, `/yolo`).
- 调试：`rg -n 'slash_exec' crates/codegen/xai-grok-shell/src`

### 22.190 `session/acp_session_impl/spawn.rs`

- 规模：2539 行 · 目录 `session/acp_session_impl`
- 摘要：Session bring-up concern for `acp_session`: `spawn_session_actor`, the
- 符号：fn`spawn_session_actor`, fn`spawn_session_on_thread`, struct`SessionThread`, struct`SessionRestartActions`
- 调试：`rg -n 'spawn' crates/codegen/xai-grok-shell/src`

### 22.191 `session/acp_session_impl/stop_gate.rs`

- 规模：498 行 · 目录 `session/acp_session_impl`
- 摘要：The turn-end `Stop`/`SubagentStop` gate for `SessionActor`.
- 调试：`rg -n 'stop_gate' crates/codegen/xai-grok-shell/src`

### 22.192 `session/acp_session_impl/tasks_cancel.rs`

- 规模：744 行 · 目录 `session/acp_session_impl`
- 摘要：Prompt-task plumbing for `SessionActor` (`AgentTask`, `TaskSlot`,
- 符号：struct`AgentTask`, struct`TaskSlot`
- 调试：`rg -n 'tasks_cancel' crates/codegen/xai-grok-shell/src`

### 22.193 `session/acp_session_impl/tool_calls.rs`

- 规模：3160 行 · 目录 `session/acp_session_impl`
- 摘要：Tool-call execution concern for `SessionActor`: the model-output →
- 调试：`rg -n 'tool_calls' crates/codegen/xai-grok-shell/src`

### 22.194 `session/acp_session_impl/tool_dispatch.rs`

- 规模：411 行 · 目录 `session/acp_session_impl`
- 摘要：Tool dispatch helpers for `SessionActor`: `dispatch_tool` and its lock /
- 调试：`rg -n 'tool_dispatch' crates/codegen/xai-grok-shell/src`

### 22.195 `session/acp_session_impl/turn.rs`

- 规模：2770 行 · 目录 `session/acp_session_impl`
- 摘要：Turn-execution concern for `SessionActor` (`handle_prompt`, turn-end,
- 调试：`rg -n 'turn' crates/codegen/xai-grok-shell/src`

### 22.196 `session/acp_session_impl/turn_end.rs`

- 规模：475 行 · 目录 `session/acp_session_impl`
- 摘要：Turn-completion concern for `SessionActor`: completion handling
- 调试：`rg -n 'turn_end' crates/codegen/xai-grok-shell/src`

### 22.197 `session/acp_session_impl/types.rs`

- 规模：261 行 · 目录 `session/acp_session_impl`
- 摘要：Top-level enum definitions for `acp_session`; their `impl` blocks and
- 符号：enum`McpReminderMode`, enum`SamplerFailureRecovery`, enum`SamplerTurnOutcome`, enum`TurnOutcome`, enum`ToolLoop`, enum`TodoGateReason`, enum`TodoGateDecision`, enum`LazinessAbortReason`
- 调试：`rg -n 'types' crates/codegen/xai-grok-shell/src`

### 22.198 `session/acp_session_impl/updates.rs`

- 规模：1009 行 · 目录 `session/acp_session_impl`
- 摘要：Outbound update emission concern for `SessionActor`: `send_update` and
- 调试：`rg -n 'updates' crates/codegen/xai-grok-shell/src`

### 22.199 `session/acp_session_impl/workflow.rs`

- 规模：371 行 · 目录 `session/acp_session_impl`
- 摘要：—
- 符号：fn`parse_named_workflow_args`
- 调试：`rg -n 'workflow' crates/codegen/xai-grok-shell/src`

### 22.200 `session/acp_session_tests/support.rs`

- 规模：615 行 · 目录 `session/acp_session_tests`
- 摘要：Wrap `id` in a shared auth-method handle for `SessionActor` test literals
- 符号：fn`test_auth_method_id`, fn`noop_observability_bridge`, fn`test_agent_default`, fn`test_agent_backend_search`, fn`test_agent_with_goal_tool`, fn`test_grok_build_agent_with_todo`, fn`test_agent_with_plan_tools`, fn`test_agent_with_tools`
- 调试：`rg -n 'support' crates/codegen/xai-grok-shell/src`

### 22.201 `session/acp_types.rs`

- 规模：917 行 · 目录 `session`
- 摘要：Public wire types (DTOs) for the ACP session actor.
- 符号：fn`default_rewind_mode`, fn`count_detail`, fn`is_coding_model_slug`, fn`should_show_model_fingerprint`, fn`model_display_name`, struct`SessionListRequest`, struct`AllSessionOverviewRequest`, struct`SessionListResponse`
- 调试：`rg -n 'acp_types' crates/codegen/xai-grok-shell/src`

### 22.202 `session/agent_rebuild.rs`

- 规模：539 行 · 目录 `session`
- 摘要：`AgentRebuildSpec` — the canonical recipe for constructing an
- 符号：fn`test_rebuild_spec_default`, struct`ResolvedToolParamsJson`, struct`AgentRebuildSpec`
- 调试：`rg -n 'agent_rebuild' crates/codegen/xai-grok-shell/src`

### 22.203 `session/announcement_state.rs`

- 规模：119 行 · 目录 `session`
- 摘要：Persisted announcement tracking state for session resumption.
- 符号：fn`to_persisted_fingerprints`, fn`from_persisted_fingerprints`, struct`AnnouncementState`, struct`McpServerFingerprint`
- 调试：`rg -n 'announcement_state' crates/codegen/xai-grok-shell/src`

### 22.204 `session/chat_persistence.rs`

- 规模：127 行 · 目录 `session`
- 摘要：Production `ChatPersistence` implementation backed by the existing persistence channel.
- 符号：struct`ChannelChatPersistence`
- 调试：`rg -n 'chat_persistence' crates/codegen/xai-grok-shell/src`

### 22.205 `session/commands.rs`

- 规模：780 行 · 目录 `session`
- 摘要：Session actor command enum and associated public types.
- 符号：fn`ok_end_turn`, struct`CancellationContext`, struct`PromptTurnOk`, struct`ParsedPromptInfo`, struct`TaskWakeFallback`, struct`TaskWakeAdmission`, enum`PromptCompletionKind`, enum`NotificationPriority`
- 调试：`rg -n 'commands' crates/codegen/xai-grok-shell/src`

### 22.206 `session/compaction.rs`

- 规模：3834 行 · 目录 `session`
- 摘要：Compaction methods for `SessionActor`.
- 符号：struct`AutoCompactTriggerInfo`, enum`SuppressReason`
- 调试：`rg -n 'compaction' crates/codegen/xai-grok-shell/src`

### 22.207 `session/compaction_config.rs`

- 规模：211 行 · 目录 `session`
- 摘要：Compaction configuration and runtime state for the session actor.
- 符号：struct`PreviousModelInfo`, struct`AsyncCompactionCache`, struct`PrefireState`, struct`CompactionConfig`
- 调试：`rg -n 'compaction_config' crates/codegen/xai-grok-shell/src`

### 22.208 `session/compaction_segments.rs`

- 规模：59 行 · 目录 `session`
- 摘要：Shell-side dispatch on [`CompactionMode`]. Split into two methods so the
- 调试：`rg -n 'compaction_segments' crates/codegen/xai-grok-shell/src`

### 22.209 `session/events.rs`

- 规模：1008 行 · 目录 `session`
- 摘要：Re-exports of the crate-internal event types that live in
- 符号：fn`prior_turn_interrupt_from_cancellation`, enum`LazinessCategory`, enum`GoalClassifierFailOpenReason`, enum`GoalClassifierFailClosedReason`, enum`GoalPlannerFailClosedReason`, enum`GoalStrategistFailReason`, enum`GoalStrategistRestoreFailReason`, enum`GoalSummarizerFailReason`
- 调试：`rg -n 'events' crates/codegen/xai-grok-shell/src`

### 22.210 `session/export.rs`

- 规模：171 行 · 目录 `session`
- 摘要：Session export for sharing via the remote session-sharing backend.
- 符号：struct`ExportedMessage`, struct`ExportedMetadata`, struct`ExportedSession`
- 调试：`rg -n 'export' crates/codegen/xai-grok-shell/src`

### 22.211 `session/feedback.rs`

- 规模：1170 行 · 目录 `session`
- 摘要：Feedback request heuristics for Grok Code sessions.
- 符号：struct`TriggerCondition`, struct`TriggerSignalSnapshot`, struct`FeedbackEvaluation`, struct`FeedbackHeuristics`, struct`FeedbackRequest`, enum`FeedbackTier`
- 调试：`rg -n 'feedback' crates/codegen/xai-grok-shell/src`

### 22.212 `session/feedback_manager.rs`

- 规模：1659 行 · 目录 `session`
- 摘要：Feedback manager for session-level feedback collection.
- 符号：fn`new_submission`, fn`submit_feedback_workflow`, struct`SubmitFeedbackOptions`, struct`SessionFeedbackData`, struct`FeedbackFlags`, struct`FeedbackManagerConfig`, struct`FeedbackManager`, enum`SubmitOutcome`
- 调试：`rg -n 'feedback_manager' crates/codegen/xai-grok-shell/src`

### 22.213 `session/file_system.rs`

- 规模：359 行 · 目录 `session`
- 摘要：Pagination offset applied after the dirs-first sort (default 0).
- 符号：fn`list`, fn`exists`, fn`read_file`, fn`read_file_ranged`, fn`check_file_size_limits`, fn`write_file`, fn`build_file_entry`, fn`delete_file`
- 调试：`rg -n 'file_system' crates/codegen/xai-grok-shell/src`

### 22.214 `session/fork.rs`

- 规模：326 行 · 目录 `session`
- 摘要：Session forking functionality
- 符号：fn`fork_session`, struct`ForkSessionRequest`, struct`ForkSessionResponse`
- 调试：`rg -n 'fork' crates/codegen/xai-grok-shell/src`

### 22.215 `session/fs_watch.rs`

- 规模：1650 行 · 目录 `session`
- 摘要：Session-level fs-watch policy over [`xai_fsnotify`].
- 符号：fn`is_under_hidden_dir`, fn`forward_to_hunk_tracker`, fn`git_head_dedup_key`, fn`spawn`, struct`FsWatchCapabilities`, struct`CapabilityInputs`, struct`FsWatchDeps`, struct`FsWatchPlan`
- 调试：`rg -n 'fs_watch' crates/codegen/xai-grok-shell/src`

### 22.216 `session/goal_classifier/evidence.rs`

- 规模：2178 行 · 目录 `session/goal_classifier`
- 摘要：Evidence-packet construction for the goal-verification stage.
- 符号：fn`extract_changed_files`, fn`build_classifier_evidence_packet`, fn`capture_changes_diff`, fn`capture_plan_changes`, fn`parse_created_at_to_unix`, fn`now_unix_seconds`, fn`extract_final_response`, fn`compose_verifier_final_response`
- 调试：`rg -n 'evidence' crates/codegen/xai-grok-shell/src`

### 22.217 `session/goal_classifier.rs`

- 规模：6596 行 · 目录 `session`
- 摘要：Goal-verification stage (harness-owned).
- 符号：fn`expand_skeptic_assignment`, fn`format_details_path`, fn`format_changes_path`, fn`validate_details_path`, fn`validate_details_path_in_root`, fn`parse_skeptic_terminal_response`, fn`capture_git_baseline`, fn`build_subagent_trace_items`
- 调试：`rg -n 'goal_classifier' crates/codegen/xai-grok-shell/src`

### 22.218 `session/goal_evaluator.rs`

- 规模：246 行 · 目录 `session`
- 摘要：—
- 符号：fn`parse_goal_evaluator_verdict`, fn`goal_evaluator_json_schema`, fn`bounded_goal_transcript`, fn`build_goal_evaluator_request`, struct`GoalEvaluatorVerdict`, enum`GoalEvaluatorDecision`, enum`GoalEvaluatorParseError`
- 调试：`rg -n 'goal_evaluator' crates/codegen/xai-grok-shell/src`

### 22.219 `session/goal_next_step.rs`

- 规模：369 行 · 目录 `session`
- 摘要：Helper that mines the "next concrete step" inlined into the goal
- 符号：fn`first_unchecked_plan_item`
- 调试：`rg -n 'goal_next_step' crates/codegen/xai-grok-shell/src`

### 22.220 `session/goal_orchestrator.rs`

- 规模：492 行 · 目录 `session`
- 摘要：Goal mode support — notification helpers and state formatters.
- 符号：fn`build_goal_updated`, fn`build_goal_cleared`, fn`format_elapsed`, struct`GoalNotifySender`
- 调试：`rg -n 'goal_orchestrator' crates/codegen/xai-grok-shell/src`

### 22.221 `session/goal_planner.rs`

- 规模：1689 行 · 目录 `session`
- 摘要：Goal planner subagent runner. Mirrors [`crate::session::goal_classifier`]
- 符号：fn`effective_role_model_id`, fn`spawn_with_fail_open_retry`, fn`parse_terminal_response`, fn`run_goal_planner`, struct`RoleSpawnOverride`, struct`RoleRenderedPrompt`, struct`ChannelSpawner`, struct`GoalPlannerInputs`
- 调试：`rg -n 'goal_planner' crates/codegen/xai-grok-shell/src`

### 22.222 `session/goal_role_tools.rs`

- 规模：654 行 · 目录 `session`
- 摘要：Shared per-role prompt tool-name rendering for the `/goal` harness.
- 符号：struct`RoleToolNames`
- 调试：`rg -n 'goal_role_tools' crates/codegen/xai-grok-shell/src`

### 22.223 `session/goal_stop_detector.rs`

- 规模：637 行 · 目录 `session`
- 摘要：Heuristic stop-detector for premature "give up" turn endings.
- 符号：fn`matched_stop_pattern`
- 调试：`rg -n 'goal_stop_detector' crates/codegen/xai-grok-shell/src`

### 22.224 `session/goal_strategist.rs`

- 规模：1296 行 · 目录 `session`
- 摘要：Stall-triggered goal strategist subagent runner.
- 符号：fn`strategist_should_fire`, fn`run_goal_strategist`, struct`ChannelSpawner`, struct`GoalStrategistInputs`, enum`GoalStrategistOutcome`
- 调试：`rg -n 'goal_strategist' crates/codegen/xai-grok-shell/src`

### 22.225 `session/goal_summarizer.rs`

- 规模：680 行 · 目录 `session`
- 摘要：Achievement-triggered goal summarizer subagent runner.
- 符号：fn`run_goal_summarizer`, struct`ChannelSpawner`, struct`GoalSummarizerInputs`, enum`GoalSummarizerOutcome`
- 调试：`rg -n 'goal_summarizer' crates/codegen/xai-grok-shell/src`

### 22.226 `session/goal_tracker.rs`

- 规模：3758 行 · 目录 `session`
- 摘要：Goal mode state machine.
- 符号：fn`generate_verifier_id`, fn`goal_scratch_root`, fn`ensure_goal_scratch_root`, fn`implementer_scratch_dir`, fn`skeptic_scratch_dir`, fn`make_base_orchestration`, struct`GoalHistoryEntry`, struct`GoalOrchestration`
- 调试：`rg -n 'goal_tracker' crates/codegen/xai-grok-shell/src`

### 22.227 `session/handle.rs`

- 规模：592 行 · 目录 `session`
- 摘要：`SessionHandle` — the `Clone + Send` proxy for interacting with a session actor.
- 符号：struct`SessionHandle`, enum`SessionLiveState`
- 调试：`rg -n 'handle' crates/codegen/xai-grok-shell/src`

### 22.228 `session/helpers/chat.rs`

- 规模：142 行 · 目录 `session/helpers`
- 摘要：Returns the largest valid UTF-8 character boundary index at or before `index`.
- 符号：fn`build_transcript`, fn`truncate_middle_words`, fn`text_completion`, fn`build_prompt_from_template`, fn`template_completion`
- 调试：`rg -n 'chat' crates/codegen/xai-grok-shell/src`

### 22.229 `session/helpers/compaction_context.rs`

- 规模：458 行 · 目录 `session/helpers`
- 摘要：Rendering helpers for [`CompactionStateContext`] that depend on
- 符号：fn`to_system_reminder_sync`, fn`to_system_reminder`, struct`McpToolNames`, struct`SubagentToolNames`
- 调试：`rg -n 'compaction_context' crates/codegen/xai-grok-shell/src`

### 22.230 `session/helpers/full_replace_compaction.rs`

- 规模：423 行 · 目录 `session/helpers`
- 摘要：grok-build's L5 wiring onto the shared full-replace engine
- 符号：struct`ShellCompactionSampler`, struct`FullReplaceTelemetry`, struct`ShellFullReplaceObserver`
- 调试：`rg -n 'full_replace_compaction' crates/codegen/xai-grok-shell/src`

### 22.231 `session/helpers/memory_context.rs`

- 规模：357 行 · 目录 `session/helpers`
- 摘要：Format memory search results as `<system-reminder>` content.
- 符号：fn`conversation_has_memory_context`, fn`format_memory_reminder`, fn`is_greeting`
- 调试：`rg -n 'memory_context' crates/codegen/xai-grok-shell/src`

### 22.232 `session/helpers/memory_flush.rs`

- 规模：739 行 · 目录 `session/helpers`
- 摘要：Pre-compaction memory flush logic.
- 符号：fn`should_flush`, fn`process_flush_response`, fn`is_duplicate`, fn`is_semantically_duplicate`, fn`select_flush_window`, enum`FlushResult`
- 调试：`rg -n 'memory_flush' crates/codegen/xai-grok-shell/src`

### 22.233 `session/helpers/mod.rs`

- 规模：13 行 · 目录 `session/helpers`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.234 `session/helpers/prompt_suggest.rs`

- 规模：585 行 · 目录 `session/helpers`
- 摘要：Next-prompt prediction helpers (tab autocomplete ghost text).
- 符号：fn`effective_suggest_model`, fn`build_transcript`, fn`suggest_prompt_user_message`, fn`is_repeat_of_user_message`, fn`sanitize_suggestion`
- 调试：`rg -n 'prompt_suggest' crates/codegen/xai-grok-shell/src`

### 22.235 `session/helpers/replay.rs`

- 规模：1256 行 · 目录 `session/helpers`
- 摘要：Replay pipeline for cross-compaction rewind.
- 符号：fn`find_latest_compaction_checkpoint`, fn`replay_to_prompt`, struct`ReplayResult`
- 调试：`rg -n 'replay' crates/codegen/xai-grok-shell/src`

### 22.236 `session/helpers/session_compact.rs`

- 规模：2255 行 · 目录 `session/helpers`
- 摘要：Compacts the current conversation and generates a summary of the conversation which
- 符号：fn`build_compaction_chat_history`, fn`build_compaction_prompt`, fn`build_two_pass_compaction_prompt`, fn`generate_session_compact`, struct`CompactOutput`, enum`CompactFailure`, enum`CompactionOutcome`
- 调试：`rg -n 'session_compact' crates/codegen/xai-grok-shell/src`

### 22.237 `session/helpers/session_recap.rs`

- 规模：848 行 · 目录 `session/helpers`
- 摘要：Session recap generation helpers.
- 符号：fn`recap_instruction`, fn`build_recap_items`, fn`budget_recap_items`, fn`main_turn_count`, fn`recap_gate`, fn`should_suppress_auto_recap_display`, fn`clean_recap_text`
- 调试：`rg -n 'session_recap' crates/codegen/xai-grok-shell/src`

### 22.238 `session/helpers/session_summary.rs`

- 规模：253 行 · 目录 `session/helpers`
- 摘要：Session title generation via LLM tool call.
- 符号：fn`title_fallback_from_user_text`, fn`generate_session_summary`
- 调试：`rg -n 'session_summary' crates/codegen/xai-grok-shell/src`

### 22.239 `session/helpers/tool_input_parsing.rs`

- 规模：174 行 · 目录 `session/helpers`
- 摘要：Normalize empty tool call arguments to `"{}"`.
- 符号：fn`try_extract_concatenated_json_objects`, fn`normalize_empty_arguments`
- 调试：`rg -n 'tool_input_parsing' crates/codegen/xai-grok-shell/src`

### 22.240 `session/image_describe.rs`

- 规模：805 行 · 目录 `session`
- 摘要：Image processing helpers for sessions with image inputs.
- 符号：fn`strip_template_context_tags`, fn`build_conversation_outline`, fn`build_describe_prompt`, fn`scrub_for_envelope`, fn`scrub_envelope_body`, fn`render_image_description_block`, fn`describe_prompt_fingerprint`, fn`content_fingerprint_bytes`
- 调试：`rg -n 'image_describe' crates/codegen/xai-grok-shell/src`

### 22.241 `session/image_normalize.rs`

- 规模：1557 行 · 目录 `session`
- 摘要：Re-encode decoded attachments that exceed [`MAX_IMAGE_BYTES`],
- 符号：fn`normalize_images`, fn`normalize_images_in`, fn`render_image_dropped_notice`, fn`dropped_to_envelope`, fn`render_re_encode_fallback_notice`, fn`render_compression_notice`, fn`persisted_image_reject_reason`, fn`inline_attach_verdict`
- 调试：`rg -n 'image_normalize' crates/codegen/xai-grok-shell/src`

### 22.242 `session/inference_metrics.rs`

- 规模：9 行 · 目录 `session`
- 摘要：Per-response inference latency metrics.
- 调试：`rg -n 'inference_metrics' crates/codegen/xai-grok-shell/src`

### 22.243 `session/managed_mcp.rs`

- 规模：1216 行 · 目录 `session`
- 摘要：Shell-side managed MCP: merges MCP server sources, then injects managed
- 符号：fn`fetch_managed_mcp_configs`, fn`mcp_server_name`, fn`merge_managed_mcp_servers`, fn`merge_managed_mcp_servers_with_policy`, fn`merge_managed_mcp_servers_sourced`, fn`auto_inject_managed_servers_with_disabled`, fn`collect_plugin_oauth_configs`, fn`merge_plugin_oauth_into`
- 调试：`rg -n 'managed_mcp' crates/codegen/xai-grok-shell/src`

### 22.244 `session/mcp_descriptors.rs`

- 规模：417 行 · 目录 `session`
- 摘要：MCP descriptor mirror.
- 符号：fn`server_descriptor_dir`, fn`materialize_descriptors_for_clients`, fn`materialize_descriptors_for_gateway_tools`, struct`GatewayToolDescriptor`
- 调试：`rg -n 'mcp_descriptors' crates/codegen/xai-grok-shell/src`

### 22.245 `session/mcp_dispatcher.rs`

- 规模：1753 行 · 目录 `session`
- 摘要：Session-actor side `StatusDispatcher` for MCP client events.
- 符号：fn`classify_source`, fn`new_shutdown_state`, fn`collect_window`, fn`build_payload`, fn`flush_window`, fn`collect_close_candidates`, fn`recoverable_http_servers`, fn`collect_http_transport_closed`
- 调试：`rg -n 'mcp_dispatcher' crates/codegen/xai-grok-shell/src`

### 22.246 `session/mcp_restart.rs`

- 规模：1518 行 · 目录 `session`
- 摘要：Bounded stdio MCP auto-restart.
- 符号：fn`maybe_schedule_restart`, fn`auto_restart_stdio`, fn`maybe_schedule_http_recovery`, fn`forward_status`, enum`SkipReason`
- 调试：`rg -n 'mcp_restart' crates/codegen/xai-grok-shell/src`

### 22.247 `session/mcp_servers.rs`

- 规模：158 行 · 目录 `session`
- 摘要：MCP server re-exports + shell-side wrappers for timeout override resolution.
- 符号：fn`build_config_resolved_event`, fn`start_mcp_server`, fn`build_pending_clients`, fn`start_mcp_servers`
- 调试：`rg -n 'mcp_servers' crates/codegen/xai-grok-shell/src`

### 22.248 `session/memory/hooks.rs`

- 规模：648 行 · 目录 `session/memory`
- 摘要：Session lifecycle hooks for the memory system.
- 符号：fn`on_session_end`, fn`generate_metadata_summary`, enum`SessionEndResult`
- 调试：`rg -n 'hooks' crates/codegen/xai-grok-shell/src`

### 22.249 `session/memory/mod.rs`

- 规模：18 行 · 目录 `session/memory`
- 摘要：Memory system shim.
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.250 `session/memory_state.rs`

- 规模：238 行 · 目录 `session`
- 摘要：`SessionMemory` — memory subsystem state for the session actor.
- 符号：struct`SessionMemory`, struct`MemoryTelemetry`
- 调试：`rg -n 'memory_state' crates/codegen/xai-grok-shell/src`

### 22.251 `session/merge.rs`

- 规模：1209 行 · 目录 `session`
- 摘要：Merged session listing — combines local and remote session data.
- 符号：fn`over_fetch`, fn`fetch_merged`, fn`filter_summaries_by_repo`, fn`fetch_lanes`, fn`merge`, struct`MergedSession`, struct`SessionLanes`
- 调试：`rg -n 'merge' crates/codegen/xai-grok-shell/src`

### 22.252 `session/mod.rs`

- 规模：365 行 · 目录 `session`
- 摘要：`false` twin: this template is not compiled into this build, so no
- 符号：fn`is_cursor_user_template`, fn`is_cursor_system_template`, fn`image_blocks`, struct`ClientFsConfig`, struct`RegistryConfig`, enum`PromptOrigin`, enum`ClientFsMode`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.253 `session/normalize_cache.rs`

- 规模：536 行 · 目录 `session`
- 摘要：Process-wide cache for normalized images: `moka::future::Cache`
- 符号：fn`run_blocking`, struct`NormalizeError`, struct`NormalizeCache`, enum`NormalizedEntry`, enum`HarnessVariant`
- 调试：`rg -n 'normalize_cache' crates/codegen/xai-grok-shell/src`

### 22.254 `session/notifications.rs`

- 规模：26 行 · 目录 `session`
- 摘要：`NotificationSender` — transport layer for session notifications.
- 符号：struct`NotificationSender`
- 调试：`rg -n 'notifications' crates/codegen/xai-grok-shell/src`

### 22.255 `session/pending_interaction.rs`

- 规模：230 行 · 目录 `session`
- 摘要：Per-session pending-interaction registry.
- 符号：fn`has_parked_plan_approval`, struct`PendingInteractionGuard`, enum`PendingKind`
- 调试：`rg -n 'pending_interaction' crates/codegen/xai-grok-shell/src`

### 22.256 `session/persistence.rs`

- 规模：4529 行 · 目录 `session`
- 摘要：Current chat history format version.
- 符号：fn`is_false`, fn`session_exists_for_cwd`, fn`find_local_child_for_remote`, fn`resolve_local_session`, fn`resolve_local_session_for_repo`, fn`resolve_local_session_for_repo_in_root`, fn`resolve_local_session_any_cwd`, fn`resolve_local_session_any_cwd_result`
- 调试：`rg -n 'persistence' crates/codegen/xai-grok-shell/src`

### 22.257 `session/plan_mode.rs`

- 规模：1226 行 · 目录 `session`
- 摘要：Plan mode state machine and prompt text generation.
- 符号：fn`plan_mode_reminder_full_template`, fn`plan_mode_reminder_sparse_template`, fn`plan_mode_reentry_reminder_template`, fn`plan_mode_edit_rejected_template`, fn`plan_mode_exit_reminder_template`, fn`is_plan_file_write`, fn`is_markdown_file_path`, fn`plan_file_has_content`
- 调试：`rg -n 'plan_mode' crates/codegen/xai-grok-shell/src`

### 22.258 `session/prompt_history.rs`

- 规模：402 行 · 目录 `session`
- 摘要：Per-working-directory prompt history for fast reverse search.
- 符号：fn`prompt_history_path`, fn`append_prompt`, fn`load_prompts`, fn`load_prompts_for_session`, fn`truncate_if_needed`, fn`append_prompt_async`, fn`load_bash_prompts`, fn`load_prompts_async`
- 调试：`rg -n 'prompt_history' crates/codegen/xai-grok-shell/src`

### 22.259 `session/prompt_parser.rs`

- 规模：547 行 · 目录 `session`
- 摘要：Parsed prompt with context and query kept separate.
- 符号：fn`parse_prompt`, fn`parse_prompt_with_skills`, struct`ParsedPrompt`
- 调试：`rg -n 'prompt_parser' crates/codegen/xai-grok-shell/src`

### 22.260 `session/prompt_queue.rs`

- 规模：83 行 · 目录 `session`
- 摘要：Server-authoritative prompt queue wire types.
- 调试：`rg -n 'prompt_queue' crates/codegen/xai-grok-shell/src`

### 22.261 `session/prompt_timing.rs`

- 规模：7 行 · 目录 `session`
- 摘要：Per-turn prompt latency measurement.
- 调试：`rg -n 'prompt_timing' crates/codegen/xai-grok-shell/src`

### 22.262 `session/replay_events.rs`

- 规模：178 行 · 目录 `session`
- 摘要：Notification destined for the high-frequency event ReplayBuffer).
- 符号：fn`flush_replay_actor`, enum`SessionNotification`, enum`SessionEvent`, enum`FlushReplayError`
- 调试：`rg -n 'replay_events' crates/codegen/xai-grok-shell/src`

### 22.263 `session/repo_changes/mod.rs`

- 规模：10 行 · 目录 `session/repo_changes`
- 摘要：Serialize local repository changes (local commits + uncommitted worktree/index changes).
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.264 `session/restore_stub.rs`

- 规模：214 行 · 目录 `session`
- 摘要：—
- 符号：fn`restore_session`, fn`restore_session_with_progress`, fn`restore_session_with_storage`, fn`resolve_restore_turn`, fn`download_to_tempfile`, fn`apply_memory_download`, fn`apply_session_state_download`, fn`format_session_line`
- 调试：`rg -n 'restore_stub' crates/codegen/xai-grok-shell/src`

### 22.265 `session/result.rs`

- 规模：140 行 · 目录 `session`
- 摘要：Extension method result: `{ result: T | null, error?: string | ExtMethodError }`
- 符号：struct`ExtMethodError`, struct`ExtMethodResult`, struct`Empty`
- 调试：`rg -n 'result' crates/codegen/xai-grok-shell/src`

### 22.266 `session/signals.rs`

- 规模：3133 行 · 目录 `session`
- 摘要：Session signals tracking for feedback heuristics.
- 符号：fn`sample_rss_bytes`, fn`merge_tightest_trigger`, fn`spawn_signals_actor`, fn`spawn_signals_actor_with_interval`, struct`ToolOutcome`, struct`ToolDuration`, struct`PrCreatedSignal`, struct`TurnDeltaSnapshot`
- 调试：`rg -n 'signals' crates/codegen/xai-grok-shell/src`

### 22.267 `session/slash_commands.rs`

- 规模：2992 行 · 目录 `session`
- 摘要：ACP slash command advertising and resolution.
- 符号：fn`build_tools_meta`, fn`builtin_commands`, fn`list_commands`, fn`parse_skill_references`, struct`BuiltinCommand`, struct`CommandAvailability`, struct`ListCommandsRequest`, struct`ListCommandsResponse`
- 调试：`rg -n 'slash_commands' crates/codegen/xai-grok-shell/src`

### 22.268 `session/storage/jsonl/mod.rs`

- 规模：2093 行 · 目录 `session/storage/jsonl`
- 摘要：JSONL storage under `{root}/sessions/{url_encoded_cwd}/{session_id}/`.
- 符号：fn`fork_filter_chat`, fn`strip_invalid_images`, struct`JsonlStorageAdapter`, enum`AppendDurability`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.269 `session/storage/jsonl/tests.rs`

- 规模：3642 行 · 目录 `session/storage/jsonl`
- 摘要：Resume from updates.jsonl alone: when chat_history.jsonl is missing, load
- 调试：`rg -n 'tests' crates/codegen/xai-grok-shell/src`

### 22.270 `session/storage/mod.rs`

- 规模：3713 行 · 目录 `session/storage`
- 摘要：On-disk file names, relative to a session directory. Single source of truth for
- 符号：fn`write_bytes_atomic`, fn`sync_file_durable`, fn`fullfsync_raw`, fn`sync_file_durable`, fn`sync_file_durable`, fn`write_bytes_atomic_async`, fn`write_jsonl_atomic`, fn`write_jsonl_atomic_async`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.271 `session/storage/relocation/fs.rs`

- 规模：373 行 · 目录 `session/storage/relocation`
- 摘要：Durable filesystem operations used by session relocation.
- 调试：`rg -n 'fs' crates/codegen/xai-grok-shell/src`

### 22.272 `session/storage/relocation/journal.rs`

- 规模：225 行 · 目录 `session/storage/relocation`
- 摘要：Relocation journal types, validation, lease, and commit-aware persistence.
- 符号：struct`RelocationJournal`, struct`RelocationLease`, enum`RelocationPhase`
- 调试：`rg -n 'journal' crates/codegen/xai-grok-shell/src`

### 22.273 `session/storage/relocation/mod.rs`

- 规模：803 行 · 目录 `session/storage/relocation`
- 摘要：Durable, source-retaining relocation of a dormant session directory.
- 符号：fn`recovery_action`, struct`RelocationRequest`, struct`RelocationAuthority`, struct`StagedRelocation`, struct`TerminalRelocation`, struct`RelocationStorage`, enum`RelocationError`, enum`RecoveryAction`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.274 `session/storage/relocation/tests.rs`

- 规模：581 行 · 目录 `session/storage/relocation`
- 摘要：—
- 调试：`rg -n 'tests' crates/codegen/xai-grok-shell/src`

### 22.275 `session/storage/relocation/view.rs`

- 规模：209 行 · 目录 `session/storage/relocation`
- 摘要：Recovery-aware point-in-time view of local session storage.
- 符号：struct`RelocationView`
- 调试：`rg -n 'view' crates/codegen/xai-grok-shell/src`

### 22.276 `session/storage/search.rs`

- 规模：2087 行 · 目录 `session/storage`
- 摘要：Session search orchestration: querying and background indexing.
- 符号：fn`notify_session_updated`, fn`execute_search`, struct`SessionSearchRequest`, struct`SessionSearchResponse`, struct`SearchIndexManager`, struct`BootstrapProgress`, struct`SearchIndexStatus`
- 调试：`rg -n 'search' crates/codegen/xai-grok-shell/src`

### 22.277 `session/storage/search_fts.rs`

- 规模：1157 行 · 目录 `session/storage`
- 摘要：SQLite-backed FTS5 index for session search.
- 符号：struct`SessionDoc`, struct`SessionIndexState`, struct`SessionSearchRow`, struct`QueryResult`, struct`SessionSearchIndex`
- 调试：`rg -n 'search_fts' crates/codegen/xai-grok-shell/src`

### 22.278 `session/storage/search_remote_sync.rs`

- 规模：580 行 · 目录 `session/storage`
- 摘要：GCS-based remote index sync for session search.
- 符号：fn`read_last_bootstrap_at`, fn`try_read_last_bootstrap_at`, fn`write_last_bootstrap_at`, fn`is_local_stale`, fn`maybe_upload_index`, fn`maybe_download_index`, struct`RemoteSyncConfig`
- 调试：`rg -n 'search_remote_sync' crates/codegen/xai-grok-shell/src`

### 22.279 `session/storage/summary_write.rs`

- 规模：466 行 · 目录 `session/storage`
- 摘要：Concurrency-safe, field-correct writes to a session's `summary.json`.
- 符号：fn`apply_patch_locked`, struct`ModelPatch`, struct`GitHeadPatch`, struct`TraceTurnPatch`, struct`SummaryPatch`, enum`CounterOp`
- 调试：`rg -n 'summary_write' crates/codegen/xai-grok-shell/src`

### 22.280 `session/streaming_capture.rs`

- 规模：622 行 · 目录 `session`
- 摘要：Out-of-band per-turn capture of the model's streamed reasoning + text.
- 符号：struct`DoomLoopSegmentStamp`, struct`StreamSegment`, struct`StreamingTurnCapture`
- 调试：`rg -n 'streaming_capture' crates/codegen/xai-grok-shell/src`

### 22.281 `session/summary.rs`

- 规模：124 行 · 目录 `session`
- 摘要：Session summary (title) generation lifecycle.
- 符号：fn`notify_client`, struct`SummaryConfig`, struct`SummaryGenerator`
- 调试：`rg -n 'summary' crates/codegen/xai-grok-shell/src`

### 22.282 `session/telemetry.rs`

- 规模：224 行 · 目录 `session`
- 摘要：Permission-mode label for the `session.permission_mode_changed` span.
- 符号：fn`permission_mode_label`, fn`permission_decision_source`, fn`emit_mcp_connection_span`, fn`skill_source_label`, fn`format_hook_name`, struct`HookRegInfo`, struct`SessionHarnessMetrics`
- 调试：`rg -n 'telemetry' crates/codegen/xai-grok-shell/src`

### 22.283 `session/tool_index.rs`

- 规模：2502 行 · 目录 `session`
- 摘要：Concrete `ToolSearchIndex` implementation using BM25.
- 符号：fn`extract_parameter_names`, fn`split_qualified_name`, struct`ToolMetadata`, struct`ServerMetadata`, struct`ToolMetadataSnapshot`, struct`Bm25ToolSearchIndex`
- 调试：`rg -n 'tool_index' crates/codegen/xai-grok-shell/src`

### 22.284 `session/turn_completion.rs`

- 规模：125 行 · 目录 `session`
- 摘要：Pure construction of the durable, replayable turn-completion terminal.
- 符号：fn`build_turn_completed`
- 调试：`rg -n 'turn_completion' crates/codegen/xai-grok-shell/src`

### 22.285 `session/two_pass.rs`

- 规模：385 行 · 目录 `session`
- 摘要：Pure builders for prefire two-pass compaction (shell-only).
- 符号：fn`split_conversation_for_two_pass`, fn`note_for_two_pass_pass2`, fn`build_two_pass_pass1_history`, fn`build_two_pass_pass2_history`, struct`TwoPassSplit`
- 调试：`rg -n 'two_pass' crates/codegen/xai-grok-shell/src`

### 22.286 `session/unified_list/cursor.rs`

- 规模：571 行 · 目录 `session/unified_list`
- 摘要：—
- 调试：`rg -n 'cursor' crates/codegen/xai-grok-shell/src`

### 22.287 `session/unified_list/envelope.rs`

- 规模：49 行 · 目录 `session/unified_list`
- 摘要：—
- 符号：struct`SessionMetaEnvelope`, enum`SessionKind`, enum`FacetValue`
- 调试：`rg -n 'envelope' crates/codegen/xai-grok-shell/src`

### 22.288 `session/unified_list/facets.rs`

- 规模：718 行 · 目录 `session/unified_list`
- 摘要：—
- 符号：fn`build_facet_registry`, struct`NormalizedItem`, struct`SourceQuery`, struct`KindFacet`, struct`CwdFacet`, struct`WorkspaceFacet`, struct`StarredFacet`, struct`RepoFacet`
- 调试：`rg -n 'facets' crates/codegen/xai-grok-shell/src`

### 22.289 `session/unified_list/mod.rs`

- 规模：1014 行 · 目录 `session/unified_list`
- 摘要：Hard-off in release builds so they can't enable the
- 符号：fn`facet_registry`, fn`conversations_lane_enabled`, fn`conversations_lane_active`, fn`parse_list_req`, fn`force_kind_chat`, fn`build_unified_list`, fn`ext_list_response`, struct`ListReq`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.290 `session/unified_list/row.rs`

- 规模：197 行 · 目录 `session/unified_list`
- 摘要：—
- 符号：fn`merged_session_to_row`, fn`conversation_to_row`, struct`UnifiedRow`, struct`RowMeta`, struct`ExtSupersetRow`, struct`SessionInfo`
- 调试：`rg -n 'row' crates/codegen/xai-grok-shell/src`

### 22.291 `session/user_message.rs`

- 规模：221 行 · 目录 `session`
- 摘要：Wraps the user query properly
- 符号：fn`user_query`, fn`construct_user_message_minimal`, fn`format_vcs_status_block`, fn`compute_vcs_status_block`, fn`construct_user_message`, struct`UserInfoOverride`
- 调试：`rg -n 'user_message' crates/codegen/xai-grok-shell/src`

### 22.292 `session/wire_tags.rs`

- 规模：128 行 · 目录 `session`
- 摘要：Single source of truth for the `sessionUpdate` discriminant strings the
- 调试：`rg -n 'wire_tags' crates/codegen/xai-grok-shell/src`

### 22.293 `session/workflow/host_service.rs`

- 规模：978 行 · 目录 `session/workflow`
- 摘要：—
- 符号：fn`spawn_workflow_host_service`, struct`WorkflowHostParams`, enum`HostDrainOutcome`
- 调试：`rg -n 'host_service' crates/codegen/xai-grok-shell/src`

### 22.294 `session/workflow/manager.rs`

- 规模：1435 行 · 目录 `session/workflow`
- 摘要：—
- 符号：struct`LaunchSpec`, struct`WorkflowManager`, enum`LaunchError`
- 调试：`rg -n 'manager' crates/codegen/xai-grok-shell/src`

### 22.295 `session/workflow/mod.rs`

- 规模：45 行 · 目录 `session/workflow`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.296 `session/workflow/notify.rs`

- 规模：272 行 · 目录 `session/workflow`
- 摘要：—
- 符号：fn`build_workflow_updated`, struct`WorkflowNotifySender`
- 调试：`rg -n 'notify' crates/codegen/xai-grok-shell/src`

### 22.297 `session/workflow/registry.rs`

- 规模：873 行 · 目录 `session/workflow`
- 摘要：—
- 符号：fn`project_root`, fn`user_workflow_dir`, fn`warm_builtin_cache`, fn`resolve_by_name`, fn`resolve_by_path`, fn`resolve_inline`, fn`validate_workflow_name`, fn`save_project_workflow`
- 调试：`rg -n 'registry' crates/codegen/xai-grok-shell/src`

### 22.298 `session/workflow/schema_contract.rs`

- 规模：177 行 · 目录 `session/workflow`
- 摘要：—
- 符号：fn`contract_prompt`, fn`compile_contract_schema`, fn`validate_contract_output`
- 调试：`rg -n 'schema_contract' crates/codegen/xai-grok-shell/src`

### 22.299 `session/workflow/store.rs`

- 规模：492 行 · 目录 `session/workflow`
- 摘要：—
- 符号：fn`validate_run_id`, fn`script_revision_path`, fn`read_bounded_nofollow`, struct`WorkflowRunManifest`, struct`RestoredWorkflowRun`, struct`WorkflowRunStore`
- 调试：`rg -n 'store' crates/codegen/xai-grok-shell/src`

### 22.300 `session/workflow/tracker.rs`

- 规模：1202 行 · 目录 `session/workflow`
- 摘要：—
- 符号：struct`WorkflowHistoryEntry`, struct`WorkflowTokenLease`, struct`WorkflowAgentRow`, struct`WorkflowRunState`, struct`WorkflowTracker`, enum`WorkflowRunStatus`
- 调试：`rg -n 'tracker' crates/codegen/xai-grok-shell/src`

### 22.301 `session/worktree.rs`

- 规模：1186 行 · 目录 `session`
- 摘要：Git worktree operations: create, list, remove, apply.
- 符号：fn`checkout_persisted_head_in_worktree`, fn`build_worktree_restore_outcome`, fn`resolve_session_repo_wide`, fn`resume_session_in_worktree`, fn`rehydrate_session_in_worktree`, struct`WorktreeRestoreDecision`
- 调试：`rg -n 'worktree' crates/codegen/xai-grok-shell/src`

### 22.302 `session/worktree_pool.rs`

- 规模：2439 行 · 目录 `session`
- 摘要：Bounded worktree pool for fast fork setup.
- 符号：fn`should_enable_pool`, fn`take_adoptable_worktrees`, fn`cleanup_stale_pool_worktrees`, struct`ClaimedWorktree`, struct`AcquiredWorktree`, struct`WorktreePool`, struct`AdoptableWorktree`
- 调试：`rg -n 'worktree_pool' crates/codegen/xai-grok-shell/src`

### 22.303 `terminal/acp_terminal.rs`

- 规模：118 行 · 目录 `terminal`
- 摘要：—
- 符号：struct`AcpTerminalRunner`
- 调试：`rg -n 'acp_terminal' crates/codegen/xai-grok-shell/src`

### 22.304 `terminal/adapter.rs`

- 规模：692 行 · 目录 `terminal`
- 摘要：`AcpTerminalAdapter`: implements `xai-grok-tools::TerminalBackend` over ACP
- 符号：struct`AcpTerminalAdapter`
- 调试：`rg -n 'adapter' crates/codegen/xai-grok-shell/src`

### 22.305 `terminal/background_task.rs`

- 规模：772 行 · 目录 `terminal`
- 摘要：Background task registry for tracking long-running commands.
- 符号：fn`get_task_output_path`, fn`persist_manifest`, fn`load_and_clear_manifest`, fn`format_resumed_tasks_reminder`, struct`TaskSnapshot`, struct`BackgroundTaskRegistry`, struct`BackgroundTaskManifestEntry`
- 调试：`rg -n 'background_task' crates/codegen/xai-grok-shell/src`

### 22.306 `terminal/exit_watcher.rs`

- 规模：255 行 · 目录 `terminal`
- 摘要：Exit detection and completion for ACP background terminals: awaits
- 调试：`rg -n 'exit_watcher' crates/codegen/xai-grok-shell/src`

### 22.307 `terminal/local_terminal.rs`

- 规模：189 行 · 目录 `terminal`
- 摘要：Truncate buffer to keep only the last `limit` bytes (drops oldest bytes).
- 符号：struct`LocalTerminalRunner`
- 调试：`rg -n 'local_terminal' crates/codegen/xai-grok-shell/src`

### 22.308 `terminal/mod.rs`

- 规模：233 行 · 目录 `terminal`
- 摘要：Resolved absolute path to bash. On Unix uses the `xai_grok_config` shell
- 符号：fn`default_shell_path`, fn`list_terminals`, fn`color_env`, fn`no_color_env`, struct`TerminalInfo`, struct`TerminalRunner`, enum`TerminalStatus`, enum`TerminalExtError`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.309 `terminal/output_recorder.rs`

- 规模：236 行 · 目录 `terminal`
- 摘要：Reconstructs a client-side terminal's log file from its `terminal/output`
- 符号：struct`OutputRecorder`
- 调试：`rg -n 'output_recorder' crates/codegen/xai-grok-shell/src`

### 22.310 `terminal/pty_session.rs`

- 规模：754 行 · 目录 `terminal`
- 摘要：Agent-scoped interactive PTY manager. PTYs are keyed by `terminalId`,
- 符号：fn`get_pty`, fn`require_pty`, fn`create_pty`, fn`write_pty_input`, fn`resize_pty`, fn`is_exited`, fn`close_pty`, fn`close_all`
- 调试：`rg -n 'pty_session' crates/codegen/xai-grok-shell/src`

### 22.311 `terminal/runner.rs`

- 规模：44 行 · 目录 `terminal`
- 摘要：Whether to stream output updates and register in the terminal registry.
- 符号：struct`TerminalRunRequest`, struct`TerminalRunResult`, enum`TerminalError`
- 调试：`rg -n 'runner' crates/codegen/xai-grok-shell/src`

### 22.312 `terminal/streaming_local_terminal.rs`

- 规模：1550 行 · 目录 `terminal`
- 摘要：Upper bound on how long terminal teardown waits for a SIGKILL'd child to be
- 符号：fn`find_terminal_session_id`, fn`kill_and_release_all_for_session`, fn`create_terminal`, fn`get_terminal_output`, fn`wait_for_terminal_exit`, fn`kill_terminal`, fn`release_terminal`, fn`background_terminal`
- 调试：`rg -n 'streaming_local_terminal' crates/codegen/xai-grok-shell/src`

### 22.313 `test_support/lsp_runtime.rs`

- 规模：174 行 · 目录 `test_support`
- 摘要：Like `test_gateway` but returns the receiver; keep it alive for the test.
- 符号：fn`test_gateway`, fn`test_gateway_with_receiver`, fn`ctx_with_toggle`, struct`DummyLspDispatch`
- 调试：`rg -n 'lsp_runtime' crates/codegen/xai-grok-shell/src`

### 22.314 `test_support/mod.rs`

- 规模：38 行 · 目录 `test_support`
- 摘要：Prepend the hermetic git binary (via `GIT_BIN_PATH`) to `PATH` so that
- 符号：fn`ensure_hermetic_git_on_path`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.315 `tier.rs`

- 规模：58 行 · 目录 `.`
- 摘要：Subscription-tier classification shared across the shell and the pager.
- 符号：fn`is_restricted_tier_name`
- 调试：`rg -n 'tier' crates/codegen/xai-grok-shell/src`

### 22.316 `tools/bridge.rs`

- 规模：6 行 · 目录 `tools`
- 摘要：ToolBridge: re-exported from `xai-grok-tools`.
- 调试：`rg -n 'bridge' crates/codegen/xai-grok-shell/src`

### 22.317 `tools/config.rs`

- 规模：747 行 · 目录 `tools`
- 摘要：Production grok-build foreground command-timeout ceiling (seconds). The
- 符号：fn`web_search_sampling_config`, struct`BashToolConfig`, struct`AskUserQuestionToolConfig`, struct`WebFetchToolConfig`, struct`ShellToolsetConfig`, struct`HashlineSchemeConfig`, enum`FileToolset`
- 调试：`rg -n 'config' crates/codegen/xai-grok-shell/src`

### 22.318 `tools/mod.rs`

- 规模：22 行 · 目录 `tools`
- 摘要：Tool infrastructure for xai-grok-shell.
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.319 `tools/notification_bridge.rs`

- 规模：2327 行 · 目录 `tools`
- 摘要：Notification bridge: translates `xai-grok-tools` `ToolNotification` events
- 符号：fn`resolved_tool_name`, fn`spawn_notification_bridge`, struct`NotificationBridgeConfig`
- 调试：`rg -n 'notification_bridge' crates/codegen/xai-grok-shell/src`

### 22.320 `tools/retry.rs`

- 规模：26 行 · 目录 `tools`
- 摘要：Retry utilities — re-exported from `xai-grok-tools`.
- 符号：fn`execute_with_retry`
- 调试：`rg -n 'retry' crates/codegen/xai-grok-shell/src`

### 22.321 `tools/todo.rs`

- 规模：81 行 · 目录 `tools`
- 摘要：Todo types — re-exported from `xai-grok-tools` with ACP conversion helpers.
- 符号：fn`todo_item_from_plan_entry`, fn`plan_entry_from_todo_item`
- 调试：`rg -n 'todo' crates/codegen/xai-grok-shell/src`

### 22.322 `tools/tool_context.rs`

- 规模：420 行 · 目录 `tools`
- 摘要：Session context — legacy name "ToolContext".
- 符号：fn`subagent_foreground_wait`, struct`TaskOutputTokenBudget`, struct`BlockingWaitState`, struct`BlockingWaitGuard`, struct`ToolContext`
- 调试：`rg -n 'tool_context' crates/codegen/xai-grok-shell/src`

### 22.323 `trace_classifier/mod.rs`

- 规模：3063 行 · 目录 `trace_classifier`
- 摘要：Trace-replay classifier.
- 符号：fn`reconstruct_todo_state`, fn`count_outstanding_dispatches`, fn`build_classifier_request`, fn`process_turn`, fn`compute_turn_elapsed_seconds`, fn`validate_min_confidence`, fn`parse_trace_file`, fn`resolve_api_key`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.324 `upload/gcs.rs`

- 规模：160 行 · 目录 `upload`
- 摘要：Shell-side adapter that threads the live `AuthManager` through to the
- 符号：fn`upload_to_auth_diagnostics`, struct`TraceExportConfigWithAuth`
- 调试：`rg -n 'gcs' crates/codegen/xai-grok-shell/src`

### 22.325 `upload/manifest.rs`

- 规模：433 行 · 目录 `upload`
- 摘要：Upload manifest: authoritative "turn upload is done" signal.
- 符号：fn`new_artifact_tracker`, fn`record_artifact`, fn`skip_artifact`, fn`build_manifest`, fn`resolve_upload_method`, fn`write_error_manifest`, fn`write_upload_manifest`, struct`FailureDetail`
- 调试：`rg -n 'manifest' crates/codegen/xai-grok-shell/src`

### 22.326 `upload/mod.rs`

- 规模：4 行 · 目录 `upload`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.327 `upload/trace.rs`

- 规模：2514 行 · 目录 `upload`
- 摘要：Per-turn trace artifact uploads to cloud storage.
- 符号：fn`upload_tool_definitions`, fn`upload_session_state`, fn`local_sandbox_telemetry`, fn`strip_url_credentials`, fn`resolve_git_repo_info`, fn`enrich_git_metadata`, fn`upload_metadata`, fn`upload_subagent_metadata`
- 调试：`rg -n 'trace' crates/codegen/xai-grok-shell/src`

### 22.328 `upload/turn.rs`

- 规模：509 行 · 目录 `upload`
- 摘要：Turn lifecycle orchestration for trace uploads.
- 符号：fn`spawn_upload_task`, fn`join_required_restore_artifacts`, fn`take_streaming_partial`, fn`complete_prompt_trace`, fn`parse_agent_profile_from_meta`, fn`parse_ask_user_question_from_meta`, fn`lookup_session_model`, fn`apply_yolo_mode_to_matching_sessions`
- 调试：`rg -n 'turn' crates/codegen/xai-grok-shell/src`

### 22.329 `util/config/announcements.rs`

- 规模：93 行 · 目录 `util/config`
- 摘要：Announcement entry received from cli-chat-proxy `/v1/settings`.
- 符号：fn`announcements_from_toml`, fn`merge_announcements`, fn`announcements_override`, fn`resolve_announcements`, fn`resolve_announcements_from_disk`
- 调试：`rg -n 'announcements' crates/codegen/xai-grok-shell/src`

### 22.330 `util/config/campaigns.rs`

- 规模：745 行 · 目录 `util/config`
- 摘要：Campaign dismiss state, remote cache, and effective-config overlay.
- 符号：fn`set_remote_campaigns_from_settings`, fn`load_dismissed_ids`, fn`dismiss_campaign_ids`, fn`campaigns_override`, fn`remote_campaigns_from_settings`, fn`resolve_active_campaigns_from_layers`, fn`load_effective_config`, fn`load_effective_config_disk_only`
- 调试：`rg -n 'campaigns' crates/codegen/xai-grok-shell/src`

### 22.331 `util/config/hints.rs`

- 规模：392 行 · 目录 `util/config`
- 摘要：Persisted worktree preference for `/new` and `/fork` (`[hints]` in config.toml).
- 符号：fn`resolve_contextual_hints`, fn`resolve_hints`, fn`resolve_hints_from_disk`, struct`ResolvedHints`, struct`ResolvedContextualHints`, enum`WorktreeHintMode`
- 调试：`rg -n 'hints' crates/codegen/xai-grok-shell/src`

### 22.332 `util/config/load.rs`

- 规模：296 行 · 目录 `util/config`
- 摘要：Resolve a bool from an optional env var > config.toml `[section] key` > false.
- 符号：fn`load_relay_sync_enabled_sync`, fn`load_blocking_upload_config_sync`, fn`load_config`, fn`load_config_from_toml`, fn`resolve_permission_config`
- 调试：`rg -n 'load' crates/codegen/xai-grok-shell/src`

### 22.333 `util/config/mcp.rs`

- 规模：2048 行 · 目录 `util/config`
- 摘要：TUI/CLI settings. Composed from typed section configs defined in `agent::config`.
- 符号：fn`get_mcp_server_config`, fn`get_mcp_server_config_with_project`, fn`mcp_server_scope`, fn`load_mcp_servers_with_oauth`, fn`worktree_pool_from_toml`, fn`load_mcp_servers`, fn`load_mcp_servers_toml_only`, fn`reload_mcp_servers_merged`
- 调试：`rg -n 'mcp' crates/codegen/xai-grok-shell/src`

### 22.334 `util/config/mod.rs`

- 规模：34 行 · 目录 `util/config`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.335 `util/config/permissions.rs`

- 规模：748 行 · 目录 `util/config`
- 摘要：How the agent handles tool execution permissions. Defined in
- 符号：fn`parse_permission_mode_canonical`, fn`permission_mode_canonical_str`, fn`permission_mode_from_ui_if_set`, fn`resolve_permission_mode`, fn`clamped_display_permission_mode`, fn`resolved_display_permission_mode`, fn`load_permission_mode`, fn`effective_yolo_for_launch`
- 调试：`rg -n 'permissions' crates/codegen/xai-grok-shell/src`

### 22.336 `util/config/persist.rs`

- 规模：1392 行 · 目录 `util/config`
- 摘要：Process-wide write lock for `~/.grok/config.toml`.
- 符号：fn`save_config`, fn`lock_config_writes`, fn`read_to_string_or_empty`, fn`atomic_write_string`, fn`update_config`
- 调试：`rg -n 'persist' crates/codegen/xai-grok-shell/src`

### 22.337 `util/config/resolve/auto_mode.rs`

- 规模：571 行 · 目录 `util/config/resolve`
- 摘要：Env override for the **auto** permission-mode feature gate.
- 符号：fn`remote_auto_mode_enabled`, fn`resolve_auto_permission_mode_enabled`, fn`cache_remote_auto_mode`, fn`cache_remote_auto_permission_mode_enabled`, fn`auto_permission_mode_enabled_from_disk`, fn`resolve_auto_mode_config_from_disk`, fn`auto_mode_classify_timeout`, fn`auto_mode_classifier_defaults`
- 调试：`rg -n 'auto_mode' crates/codegen/xai-grok-shell/src`

### 22.338 `util/config/resolve/compaction.rs`

- 规模：219 行 · 目录 `util/config/resolve`
- 摘要：Default auto-compact threshold (% of context window) when no source sets it.
- 符号：fn`resolve_compaction_tool_choice_from`, fn`resolve_auto_compact_threshold_percent`, fn`resolve_auto_compact_threshold_percent_from_tiers`, fn`resolve_compaction_wall_clock_budget_secs`, enum`CompactionToolChoice`
- 调试：`rg -n 'compaction' crates/codegen/xai-grok-shell/src`

### 22.339 `util/config/resolve/crash_handler.rs`

- 规模：230 行 · 目录 `util/config/resolve`
- 摘要：Env override for the full crash-handler install gate.
- 符号：fn`resolve_crash_handler_enabled`, fn`cache_remote_crash_handler_enabled`, fn`load_crash_handler_enabled_sync`
- 调试：`rg -n 'crash_handler' crates/codegen/xai-grok-shell/src`

### 22.340 `util/config/resolve/display_refresh.rs`

- 规模：654 行 · 目录 `util/config/resolve`
- 摘要：Display-refresh probe + auto-cadence policy resolve and pure cadence derivation.
- 符号：fn`resolve_display_refresh`, fn`decide_auto_cadence`, fn`merge_motion_cadence`, fn`resolve_motion_cadence`, struct`DisplayRefreshPolicy`, struct`AutoCadenceDecision`, struct`MotionCadence`
- 调试：`rg -n 'display_refresh' crates/codegen/xai-grok-shell/src`

### 22.341 `util/config/resolve/features.rs`

- 规模：262 行 · 目录 `util/config/resolve`
- 摘要：Resolve whether ZDR users are allowed to use the product.
- 符号：fn`resolve_zdr_access_enabled`, fn`resolve_remote_fetch_enabled`
- 调试：`rg -n 'features' crates/codegen/xai-grok-shell/src`

### 22.342 `util/config/resolve/mcp.rs`

- 规模：466 行 · 目录 `util/config/resolve`
- 摘要：Resolve `mcp.liveness_watchers` for a session.
- 符号：fn`resolve_mcp_liveness_watchers`, fn`resolve_mcp_auto_restart`, fn`resolve_mcp_push_server_status`, fn`resolve_mcp_recursive_config_watch`, fn`cache_remote_mcp_startup_timeout_secs`, fn`resolved_mcp_startup_timeout_secs`, fn`resolve_mcp_startup_timeout_secs`, fn`cache_remote_max_mcp_output_bytes`
- 调试：`rg -n 'mcp' crates/codegen/xai-grok-shell/src`

### 22.343 `util/config/resolve/mod.rs`

- 规模：29 行 · 目录 `util/config/resolve`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.344 `util/config/resolve/system_prompt.rs`

- 规模：166 行 · 目录 `util/config/resolve`
- 摘要：Resolve system-prompt identity label.
- 符号：fn`resolve_system_prompt_label`, fn`resolve_system_prompt_label_from_tiers`
- 调试：`rg -n 'system_prompt' crates/codegen/xai-grok-shell/src`

### 22.345 `util/config/resolve/tool_approvals.rs`

- 规模：192 行 · 目录 `util/config/resolve`
- 摘要：Env override for the **remember tool approvals** permission-panel gate.
- 符号：fn`resolve_remember_tool_approvals`, fn`cache_remote_remember_tool_approvals`, fn`remember_tool_approvals_from_disk`
- 调试：`rg -n 'tool_approvals' crates/codegen/xai-grok-shell/src`

### 22.346 `util/config/resolve/toolset.rs`

- 规模：765 行 · 目录 `util/config/resolve`
- 摘要：Resolve whether the bash-harness `find`→`bfs` / `grep`→`ugrep` shadows are
- 符号：fn`resolve_search_tools_enabled`, fn`resolve_shell_env_policy`, fn`resolve_login_shell_capture`, fn`resolve_scheduler_background_loops`, fn`resolve_ask_user_question_params_from_disk`
- 调试：`rg -n 'toolset' crates/codegen/xai-grok-shell/src`

### 22.347 `util/config/resolve/ui.rs`

- 规模：442 行 · 目录 `util/config/resolve`
- 摘要：Env override for showing agent thinking blocks in the TUI.
- 符号：fn`resolve_show_thinking_blocks`, fn`resolve_group_tool_verbs`, fn`resolve_collapsed_edit_blocks`, fn`resolve_mouse_reporting_toggle`
- 调试：`rg -n 'ui' crates/codegen/xai-grok-shell/src`

### 22.348 `util/config/resolve/version.rs`

- 规模：512 行 · 目录 `util/config/resolve`
- 摘要：Machine-readable channel name derived from the GCS stable pointer cache.
- 符号：fn`channel_name_from_cache`, struct`VersionPolicy`, enum`VersionKnob`
- 调试：`rg -n 'version' crates/codegen/xai-grok-shell/src`

### 22.349 `util/config/settings_writes.rs`

- 规模：310 行 · 目录 `util/config`
- 摘要：Persist `[ui].compact_mode` via `update_config`.
- 符号：fn`set_compact_mode`, fn`set_show_timestamps`, fn`set_show_timeline`, fn`set_page_flip_on_send`, fn`set_combine_queued_prompts`, fn`set_simple_mode`, fn`set_contextual_hint_undo`, fn`set_contextual_hint_plan_mode`
- 调试：`rg -n 'settings_writes' crates/codegen/xai-grok-shell/src`

### 22.350 `util/config/tips.rs`

- 规模：398 行 · 目录 `util/config`
- 摘要：Read `[cli] show_tips` from config.toml. Returns `None` if not set.
- 符号：fn`show_tips_from_toml_opt`, fn`tips_from_toml`, fn`merge_tips`, fn`resolve_tips`, fn`resolve_tips_from_disk`, fn`resolve_slash_command_tags`, fn`channel_from_toml_opt`, struct`TipsOverride`
- 调试：`rg -n 'tips' crates/codegen/xai-grok-shell/src`

### 22.351 `util/config/worktree.rs`

- 规模：802 行 · 目录 `util/config`
- 摘要：Worktree creation type configuration.
- 符号：fn`worktree_type_from_toml_opt`, fn`worktree_type_from_toml`, fn`resolve_worktree_type`, fn`worktree_type`, fn`restore_code_from_toml`, fn`resolve_restore_code`, fn`use_leader_sync`, fn`worktree_auto_gc_from_toml`
- 调试：`rg -n 'worktree' crates/codegen/xai-grok-shell/src`

### 22.352 `util/grok_auth_credentials.rs`

- 规模：189 行 · 目录 `util`
- 摘要：Credentials for authenticating with grok backend services.
- 符号：struct`GrokAuthCredentials`
- 调试：`rg -n 'grok_auth_credentials' crates/codegen/xai-grok-shell/src`

### 22.353 `util/hooks.rs`

- 规模：116 行 · 目录 `util`
- 摘要：Shared hook source path discovery.
- 符号：fn`discover_hook_source_paths`, fn`discover_hooks`, struct`HookSourcePaths`
- 调试：`rg -n 'hooks' crates/codegen/xai-grok-shell/src`

### 22.354 `util/mod.rs`

- 规模：141 行 · 目录 `util`
- 摘要：Aborts the wrapped tokio task when dropped.
- 符号：fn`is_user_instruction_path`, fn`expand_home`, struct`AbortOnDrop`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.355 `util/subprocess.rs`

- 规模：367 行 · 目录 `util`
- 摘要：Shared subprocess helpers: a TTY-detached async runner with a wall-clock
- 符号：fn`git_bin`, fn`sh_c`, fn`run_detached_with_timeout`, struct`RunOptions`, enum`CommandLog`, enum`RunError`
- 调试：`rg -n 'subprocess' crates/codegen/xai-grok-shell/src`

### 22.356 `util/user_identity.rs`

- 规模：904 行 · 目录 `util`
- 摘要：Resolves and caches the author identity for feedback submissions
- 符号：fn`cached_identity`, struct`ResolvedUserIdentity`, struct`IdentityCache`
- 调试：`rg -n 'user_identity' crates/codegen/xai-grok-shell/src`

### 22.357 `active_sessions.rs`

- 规模：282 行 · 目录 `.`
- 摘要：Tracks open TUI sessions in `~/.grok/active_sessions.json` for crash
- 符号：fn`register`, fn`unregister`, fn`try_unregister`, fn`collect_crashed`, fn`register_in`, fn`unregister_in`, fn`try_unregister_in`, fn`collect_crashed_in`
- 调试：`rg -n 'active_sessions' crates/codegen/xai-grok-shell/src`

### 22.358 `agent/activity.rs`

- 规模：411 行 · 目录 `agent`
- 摘要：Send-safe view of the agent's in-flight work, shared with the leader's
- 符号：struct`AgentActivity`
- 调试：`rg -n 'activity' crates/codegen/xai-grok-shell/src`

### 22.359 `agent/app.rs`

- 规模：2343 行 · 目录 `agent`
- 摘要：Configuration for periodic auto-update checking in leader mode.
- 符号：fn`run_auto_update_checker`, fn`run_stdio_agent`, fn`run_headless`, fn`run_headless_no_browser`, fn`run_leader`, struct`LeaderAutoUpdateConfig`
- 调试：`rg -n 'app' crates/codegen/xai-grok-shell/src`

### 22.360 `agent/auth_method.rs`

- 规模：1105 行 · 目录 `agent`
- 摘要：Shared, live handle to the agent's current ACP auth method id.
- 符号：fn`new_shared_auth_method_id`, fn`read_xai_api_key_env`, fn`has_xai_api_key_env`, fn`should_advertise_xai_api_key`, fn`build_auth_methods`, fn`is_session_based_method`, fn`session_token_auth_gate`, fn`method_id_after_cached_token_unavailable`
- 调试：`rg -n 'auth_method' crates/codegen/xai-grok-shell/src`

### 22.361 `agent/chat_modes.rs`

- 规模：323 行 · 目录 `agent`
- 摘要：grok.com chat-product model catalog: caches `/rest/modes` and maps modes to
- 符号：fn`process_chat_mode_enabled`, fn`modes_to_model_state`, struct`ChatModesManager`
- 调试：`rg -n 'chat_modes' crates/codegen/xai-grok-shell/src`

### 22.362 `agent/config.rs`

- 规模：12525 行 · 目录 `agent`
- 摘要：The mode in which the agent is running.
- 符号：fn`default_agent_type`, fn`default_asset_server_url`, fn`env_string`, fn`resolve_compat_cell_with_env`, fn`compat_config_cell`, fn`resolve_compat_sessions_from_raw`, fn`resolve_string_flag`, fn`resolve_enabled`
- 调试：`rg -n 'config' crates/codegen/xai-grok-shell/src`

### 22.363 `agent/config_model_override_parse.rs`

- 规模：870 行 · 目录 `agent`
- 摘要：Resilient parsing for `[model.<id>]` TOML overrides.
- 符号：fn`parse_model_overrides`, fn`log_config_warnings`, struct`ConfigWarning`, struct`ParsedModelOverrides`, enum`ConfigWarningKind`, enum`WarningTarget`
- 调试：`rg -n 'config_model_override_parse' crates/codegen/xai-grok-shell/src`

### 22.364 `agent/ext_parsers.rs`

- 规模：267 行 · 目录 `agent`
- 摘要：Wire-shape parsers for ext-notification params handled by `MvpAgent`.
- 调试：`rg -n 'ext_parsers' crates/codegen/xai-grok-shell/src`

### 22.365 `agent/feedback_client.rs`

- 规模：1355 行 · 目录 `agent`
- 摘要：REST client for feedback collection via cli-chat-proxy.
- 符号：fn`signals_to_update`, fn`snapshot_to_turn_delta`, struct`SessionTurnDelta`, struct`SessionTurnDeltaResponse`, struct`FeedbackApiError`, struct`FeedbackClient`
- 调试：`rg -n 'feedback_client' crates/codegen/xai-grok-shell/src`

### 22.366 `agent/folder_trust.rs`

- 规模：1674 行 · 目录 `agent`
- 摘要：Folder-trust gate ("do you trust this folder?").
- 符号：fn`revoke_folder_trust`, fn`project_scope_allowed`, fn`prompt_warranted`, fn`detected_config_kinds`, fn`agent_inline_hooks_allowed`, fn`record_for_test`, fn`resolve_and_record`, fn`resolve_launch_dir_trust`
- 调试：`rg -n 'folder_trust' crates/codegen/xai-grok-shell/src`

### 22.367 `agent/handlers/mod.rs`

- 规模：3 行 · 目录 `agent/handlers`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.368 `agent/handlers/model_switch.rs`

- 规模：281 行 · 目录 `agent/handlers`
- 摘要：Applies a model switch to a session — the ungated path. `set_session_model`
- 符号：fn`apply`
- 调试：`rg -n 'model_switch' crates/codegen/xai-grok-shell/src`

### 22.369 `agent/handlers/session.rs`

- 规模：291 行 · 目录 `agent/handlers`
- 摘要：Session meta-information handlers.
- 符号：fn`handle`
- 调试：`rg -n 'session' crates/codegen/xai-grok-shell/src`

### 22.370 `agent/handlers/workspaces.rs`

- 规模：174 行 · 目录 `agent/handlers`
- 摘要：—
- 符号：fn`handle`
- 调试：`rg -n 'workspaces' crates/codegen/xai-grok-shell/src`

### 22.371 `agent/init.rs`

- 规模：200 行 · 目录 `agent`
- 摘要：Agent bootstrap and lifecycle hooks.
- 符号：fn`bootstrap`, fn`exit_on_config_error`, fn`update_telemetry_config`
- 调试：`rg -n 'init' crates/codegen/xai-grok-shell/src`

### 22.372 `agent/mod.rs`

- 规模：32 行 · 目录 `agent`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.373 `agent/model_providers.rs`

- 规模：1010 行 · 目录 `agent`
- 摘要：Query parameters folded into every request URL; inherited by models.
- 符号：fn`model_provider_auth_name`, fn`auth_config_issues`, fn`parse_model_providers`, struct`ModelProviderConfig`
- 调试：`rg -n 'model_providers' crates/codegen/xai-grok-shell/src`

### 22.374 `agent/models.rs`

- 规模：3614 行 · 目录 `agent`
- 摘要：Model fetching, resolution, and management.
- 符号：fn`task_model_error_for_catalog`, fn`prefetch_models_blocking`, fn`prefetch_models_and_settings_blocking`, fn`start_early_prefetch_with_auth`, fn`start_early_prefetch`, fn`resolve_catalog_key`, fn`selectable_catalog_key_for_persisted`, fn`resolve_default_model`
- 调试：`rg -n 'models' crates/codegen/xai-grok-shell/src`

### 22.375 `agent/mvp_agent/acp_agent.rs`

- 规模：4113 行 · 目录 `agent/mvp_agent`
- 摘要：[`acp::Agent`] trait implementation for [`MvpAgent`].
- 调试：`rg -n 'acp_agent' crates/codegen/xai-grok-shell/src`

### 22.376 `agent/mvp_agent/agent_ops.rs`

- 规模：3827 行 · 目录 `agent/mvp_agent`
- 摘要：Inherent [`MvpAgent`] helpers (MCP/clients/gateway, settings/models, session ops, spawn).
- 调试：`rg -n 'agent_ops' crates/codegen/xai-grok-shell/src`

### 22.377 `agent/mvp_agent/code_nav.rs`

- 规模：261 行 · 目录 `agent/mvp_agent`
- 摘要：Code-navigation eligibility gating and codebase-index management for [`MvpAgent`].
- 调试：`rg -n 'code_nav' crates/codegen/xai-grok-shell/src`

### 22.378 `agent/mvp_agent/folder_trust_prompt.rs`

- 规模：453 行 · 目录 `agent/mvp_agent`
- 摘要：Interactive folder-trust prompt: a dormant agent→GUI-client ACP round-trip
- 符号：struct`FolderTrustRequest`, struct`FolderTrustResponse`, enum`FolderTrustOutcome`
- 调试：`rg -n 'folder_trust_prompt' crates/codegen/xai-grok-shell/src`

### 22.379 `agent/mvp_agent/heap_profile.rs`

- 规模：352 行 · 目录 `agent/mvp_agent`
- 摘要：Heap-profile monitor wiring for [`MvpAgent`].
- 调试：`rg -n 'heap_profile' crates/codegen/xai-grok-shell/src`

### 22.380 `agent/mvp_agent/mod.rs`

- 规模：2668 行 · 目录 `agent/mvp_agent`
- 摘要：A `'static` reference to a value on a single-threaded `LocalSet`.
- 符号：fn`reject_direct_hub_cloud_meta`, fn`jwt_tier_claim`, fn`resolve_subscription_tier_for_telemetry`, fn`jwt_claim_matches_user_subscription_tier`, fn`parse_session_plugin_dirs`, fn`chat_session_spawn_options`, fn`resolve_session_auto_mode`, fn`build_prompt_response_meta`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.381 `agent/mvp_agent/session_lifecycle.rs`

- 规模：457 行 · 目录 `agent/mvp_agent`
- 摘要：Session lifecycle, roster deltas, and the idle-session supervisor for [`MvpAgent`].
- 符号：struct`RegistrySnapshot`
- 调试：`rg -n 'session_lifecycle' crates/codegen/xai-grok-shell/src`

### 22.382 `agent/mvp_agent/subagent_coordinator.rs`

- 规模：560 行 · 目录 `agent/mvp_agent`
- 摘要：Shell runner adapter and spawn-context construction for [`MvpAgent`].
- 调试：`rg -n 'subagent_coordinator' crates/codegen/xai-grok-shell/src`

### 22.383 `agent/mvp_agent/tests.rs`

- 规模：4845 行 · 目录 `agent/mvp_agent`
- 摘要：Build an unsigned JWT with a `tier` claim (header.payload.sig base64url).
- 调试：`rg -n 'tests' crates/codegen/xai-grok-shell/src`

### 22.384 `agent/proxy.rs`

- 规模：619 行 · 目录 `agent`
- 摘要：HTTP CONNECT proxy support for WebSocket connections.
- 符号：fn`resolve_proxy_for_host`, fn`connect_via_proxy`
- 调试：`rg -n 'proxy' crates/codegen/xai-grok-shell/src`

### 22.385 `agent/relay.rs`

- 规模：1182 行 · 目录 `agent`
- 摘要：WebSocket relay connection management.
- 符号：fn`spawn_relay_connection`, fn`spawn_relay_connection_with_callback`, fn`run_websocket_session`, fn`run_websocket_session_with_liveness`, struct`RelayConfig`, struct`RelayHandle`, enum`SessionEndReason`
- 调试：`rg -n 'relay' crates/codegen/xai-grok-shell/src`

### 22.386 `agent/restore_code.rs`

- 规模：54 行 · 目录 `agent`
- 摘要：Thin wire-format adapter that wraps the shared
- 符号：fn`build_code_restore_meta`
- 调试：`rg -n 'restore_code' crates/codegen/xai-grok-shell/src`

### 22.387 `agent/roster.rs`

- 规模：311 行 · 目录 `agent`
- 摘要：Roster types for the multi-client FleetView dashboard.
- 符号：fn`merge_roster`, struct`RosterEntry`, struct`RosterListResponse`, struct`RosterChanged`, enum`RosterActivity`, enum`RosterOrigin`
- 调试：`rg -n 'roster' crates/codegen/xai-grok-shell/src`

### 22.388 `agent/server.rs`

- 规模：487 行 · 目录 `agent`
- 摘要：WebSocket server for remote agent connections.
- 符号：fn`run_agent_server`, struct`ServerConfig`, struct`WsQueryParams`
- 调试：`rg -n 'server' crates/codegen/xai-grok-shell/src`

### 22.389 `agent/session_config.rs`

- 规模：219 行 · 目录 `agent`
- 摘要：The built-in session-picker modes used when the model has no server list.
- 符号：fn`legacy_session_effort_options`, fn`build_session_config_options`, struct`SessionConfigOption`, struct`GrokSessionDetail`
- 调试：`rg -n 'session_config' crates/codegen/xai-grok-shell/src`

### 22.390 `agent/session_metrics.rs`

- 规模：10 行 · 目录 `agent`
- 摘要：Session lifecycle event structs.
- 调试：`rg -n 'session_metrics' crates/codegen/xai-grok-shell/src`

### 22.391 `agent/session_registry_client.rs`

- 规模：642 行 · 目录 `agent`
- 摘要：REST client for the session replicas registry (cli-chat-proxy).
- 符号：struct`RegisterRequest`, struct`UpdateRequest`, struct`SessionRecord`, struct`SearchResponse`, struct`DownloadResponse`, struct`SessionRegistryClient`
- 调试：`rg -n 'session_registry_client' crates/codegen/xai-grok-shell/src`

### 22.392 `agent/subagent/handle_request.rs`

- 规模：1880 行 · 目录 `agent/subagent`
- 摘要：Runtime adapter for one shell child. Shared lifecycle state is owned by the
- 符号：fn`run_shell_child`
- 调试：`rg -n 'handle_request' crates/codegen/xai-grok-shell/src`

### 22.393 `agent/subagent/mod.rs`

- 规模：2616 行 · 目录 `agent/subagent`
- 摘要：Shell child runtime adapter and presentation.
- 符号：fn`present_child_completion`, fn`resume_inherited_prefix_len`, fn`validate_subagent_type`, fn`subagent_harness_flavor_is_representable`, fn`describe_subagent_type`, fn`read_subagent_output`, fn`reconcile_orphaned_subagents_with_backend`, struct`AutoCompactThresholdTiers`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.394 `agent/subscription_check.rs`

- 规模：195 行 · 目录 `agent`
- 摘要：Subscription check for paywall gate lift.
- 符号：fn`single_check`, struct`UnblockResult`
- 调试：`rg -n 'subscription_check' crates/codegen/xai-grok-shell/src`

### 22.395 `agent/update_chunk_merge.rs`

- 规模：1070 行 · 目录 `agent`
- 摘要：Controls how sampling/output chunks are buffered before being delivered to the client.
- 符号：struct`BufferingSettings`, struct`ReplayBuffer`
- 调试：`rg -n 'update_chunk_merge' crates/codegen/xai-grok-shell/src`

### 22.396 `auth/attribution.rs`

- 规模：954 行 · 目录 `auth`
- 摘要：Shell-side 401-attribution helpers.
- 符号：fn`test_emit_count`, fn`reset_test_emit_count`, fn`record_consumer_401`, fn`record_auth_401`, struct`ShellAttribution`, enum`ConsumerKind`
- 调试：`rg -n 'attribution' crates/codegen/xai-grok-shell/src`

### 22.397 `auth/auth_provider.rs`

- 规模：660 行 · 目录 `auth`
- 摘要：Model auth providers (`[auth_provider.<name>]`).
- 符号：fn`test_backdate_provider_mint`, fn`test_counting_provider`, struct`AuthProviderConfig`, struct`AuthProviderRef`, enum`ProviderRefreshOutcome`
- 调试：`rg -n 'auth_provider' crates/codegen/xai-grok-shell/src`

### 22.398 `auth/config.rs`

- 规模：430 行 · 目录 `auth`
- 摘要：Default scopes for the xAI OAuth2 provider. Includes `grok-cli:access`
- 符号：fn`allowed_accounts_app_origins`, fn`accounts_app_cors_layer`, fn`use_local_auth`, fn`xai_oauth2_issuer`, fn`is_xai_oauth2_issuer`, struct`GrokComConfig`, struct`OidcAuthConfig`, struct`OAuth2ProviderConfig`
- 调试：`rg -n 'config' crates/codegen/xai-grok-shell/src`

### 22.399 `auth/credential_provider.rs`

- 规模：884 行 · 目录 `auth`
- 摘要：`api_key.id` for the active credential: hash the stable API key, never the
- 符号：fn`embedding_session_credentials`, fn`build_storage_client_for_proxy`, fn`wire_otel_auth_manager`, fn`sync_external_otel_identity`, fn`wire_otel_deployment_key`, fn`build_default_otel_layer_config`, struct`ShellAuthCredentialProvider`, struct`StorageClientAttributionBridge`
- 调试：`rg -n 'credential_provider' crates/codegen/xai-grok-shell/src`

### 22.400 `auth/devbox_login_stub.rs`

- 规模：37 行 · 目录 `auth`
- 摘要：Stub for builds without the devbox auth feature.
- 符号：fn`is_devbox_environment`, fn`mint_devbox_auth`, fn`run_devbox_login`
- 调试：`rg -n 'devbox_login_stub' crates/codegen/xai-grok-shell/src`

### 22.401 `auth/device_code.rs`

- 规模：890 行 · 目录 `auth`
- 摘要：RFC 8628 Device Authorization Grant -- CLI side.
- 符号：fn`request_device_code`, fn`complete_device_code_login`, fn`run_device_code_login_channels`, struct`DeviceCode`, enum`DeviceCodeError`, enum`ClientSurface`
- 调试：`rg -n 'device_code' crates/codegen/xai-grok-shell/src`

### 22.402 `auth/error.rs`

- 规模：144 行 · 目录 `auth`
- 摘要：Token expired and no refresh authority available.
- 符号：struct`RefreshTransientError`, struct`RefreshTokenFailedError`, enum`AuthError`, enum`RefreshTokenError`, enum`RefreshTokenFailedReason`
- 调试：`rg -n 'error' crates/codegen/xai-grok-shell/src`

### 22.403 `auth/external_auth.rs`

- 规模：222 行 · 目录 `auth`
- 摘要：Parse stdout into a session-credential `GrokAuth`.
- 符号：fn`parse_output`, fn`run_external_refresh`, fn`refresh_with_command`
- 调试：`rg -n 'external_auth' crates/codegen/xai-grok-shell/src`

### 22.404 `auth/flow.rs`

- 规模：1971 行 · 目录 `auth`
- 摘要：Reject a cached credential for reuse if it lacks `oidc_issuer`, has a
- 符号：fn`run_auth_flow_with_stderr_bridge`, fn`run_auth_flow`, fn`run_auth_flow_interactive`, fn`try_ensure_fresh_auth`, fn`try_ensure_session_noninteractive`, fn`ensure_authenticated`, fn`ensure_authenticated_with_override`, fn`ensure_authenticated_or_noninteractive`
- 调试：`rg -n 'flow' crates/codegen/xai-grok-shell/src`

### 22.405 `auth/jwt.rs`

- 规模：45 行 · 目录 `auth`
- 摘要：JWT expiration detection. Returns `None`/`false` for non-JWT tokens.
- 符号：fn`parse_jwt_expiration`, fn`is_jwt_expired_or_near`
- 调试：`rg -n 'jwt' crates/codegen/xai-grok-shell/src`

### 22.406 `auth/manager/enrichment.rs`

- 规模：256 行 · 目录 `auth/manager`
- 摘要：Background `/user` enrichment spawned by `AuthManager::update()`.
- 调试：`rg -n 'enrichment' crates/codegen/xai-grok-shell/src`

### 22.407 `auth/manager/lock.rs`

- 规模：1187 行 · 目录 `auth/manager`
- 摘要：Advisory `auth.json.lock` helpers (free functions, no `AuthManager`
- 符号：fn`try_lock_auth_file_nonblocking`, fn`try_lock_auth_file_async`
- 调试：`rg -n 'lock' crates/codegen/xai-grok-shell/src`

### 22.408 `auth/manager/sleep_gate.rs`

- 规模：433 行 · 目录 `auth/manager`
- 摘要：System-sleep refresh-straddle mitigation for [`AuthManager`].
- 调试：`rg -n 'sleep_gate' crates/codegen/xai-grok-shell/src`

### 22.409 `auth/manager.rs`

- 规模：2364 行 · 目录 `auth`
- 摘要：`AuthManager` -- single source of truth for `auth.json` + the
- 符号：fn`compute_proactive_sleep`, fn`shared_api_key_provider`, struct`AuthManager`, struct`SharedAuthKeyProvider`, enum`RefreshReason`, enum`DiskAuthState`
- 调试：`rg -n 'manager' crates/codegen/xai-grok-shell/src`

### 22.410 `auth/meta.rs`

- 规模：58 行 · 目录 `auth`
- 摘要：Access gate from `grok_build_access_gate`.
- 符号：struct`GateInfo`, struct`AuthMeta`
- 调试：`rg -n 'meta' crates/codegen/xai-grok-shell/src`

### 22.411 `auth/mod.rs`

- 规模：54 行 · 目录 `auth`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.412 `auth/model.rs`

- 规模：520 行 · 目录 `auth`
- 摘要：Legacy auth.json scope key. Fallback for old devbox auth files.
- 符号：fn`default_coding_data_retention_opt_out`, fn`token_suffix`, fn`lookup_auth`, fn`is_expired`, fn`is_expired_with_buffer`, struct`GrokAuth`, struct`UserInfo`, enum`AuthMode`
- 调试：`rg -n 'model' crates/codegen/xai-grok-shell/src`

### 22.413 `auth/oidc/login.rs`

- 规模：701 行 · 目录 `auth/oidc`
- 摘要：Interactive login orchestration: callback HTTP server, browser
- 符号：fn`callback_page`, fn`run_login_flow`, fn`run_login_flow_with_config`
- 调试：`rg -n 'login' crates/codegen/xai-grok-shell/src`

### 22.414 `auth/oidc/mod.rs`

- 规模：14 行 · 目录 `auth/oidc`
- 摘要：OIDC authentication: protocol, login, and refresh submodules.
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.415 `auth/oidc/protocol.rs`

- 规模：1295 行 · 目录 `auth/oidc`
- 摘要：Pure OIDC protocol mechanics: PKCE, discovery, token exchange,
- 符号：fn`with_alpha_test_key`, fn`is_configured`, fn`peek_access_token_principal`, fn`peek_access_token_principal_id`, fn`resolve_login_principal_policy`, fn`login_principal_policy`, fn`enforce_login_principal`
- 调试：`rg -n 'protocol' crates/codegen/xai-grok-shell/src`

### 22.416 `auth/oidc/refresh.rs`

- 规模：249 行 · 目录 `auth/oidc`
- 摘要：Pure-data OIDC refresh. Talks to the IdP and returns
- 符号：fn`oidc_token_exchange`, enum`OidcRefreshResult`
- 调试：`rg -n 'refresh' crates/codegen/xai-grok-shell/src`

### 22.417 `auth/oidc/test_helpers.rs`

- 规模：130 行 · 目录 `auth/oidc`
- 摘要：Shared test helpers for `oidc::protocol::tests` and `oidc::login::tests`.
- 调试：`rg -n 'test_helpers' crates/codegen/xai-grok-shell/src`

### 22.418 `auth/recovery.rs`

- 规模：1140 行 · 目录 `auth`
- 摘要：Unauthorized (401) recovery state machine.
- 符号：fn`manual_auth_reason`, fn`relay_should_cancel`, struct`RejectedAuth`, struct`ManualAuthTracker`, struct`UnauthorizedRecovery`, enum`RecoverySource`
- 调试：`rg -n 'recovery' crates/codegen/xai-grok-shell/src`

### 22.419 `auth/refresh/external_refresher.rs`

- 规模：113 行 · 目录 `auth/refresh`
- 摘要：Refreshes by re-running the operator's external auth binary via the async
- 符号：struct`ExternalBinaryRefresher`
- 调试：`rg -n 'external_refresher' crates/codegen/xai-grok-shell/src`

### 22.420 `auth/refresh/mod.rs`

- 规模：217 行 · 目录 `auth/refresh`
- 摘要：Callback for diagnostic log upload on auth refresh failure.
- 符号：fn`resolve_refresh_credential`, fn`build_refresher`, enum`RefreshOutcome`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.421 `auth/refresh/oidc_refresher.rs`

- 规模：339 行 · 目录 `auth/refresh`
- 摘要：Escalate to `PermanentFailure` after this many consecutive transient
- 符号：struct`OidcRefresher`
- 调试：`rg -n 'oidc_refresher' crates/codegen/xai-grok-shell/src`

### 22.422 `auth/single_flight.rs`

- 规模：359 行 · 目录 `auth`
- 摘要：Single-flight guard for interactive login.
- 符号：struct`AttemptChannels`, struct`AuthSingleFlight`, struct`AuthAttemptGuard`, enum`SubmitCodeError`
- 调试：`rg -n 'single_flight' crates/codegen/xai-grok-shell/src`

### 22.423 `auth/storage.rs`

- 规模：578 行 · 目录 `auth`
- 摘要：RAII guard for an exclusive advisory lock on `auth.json.lock`.
- 符号：fn`read_auth_json`, fn`read_auth_json_or_empty`, fn`backup_corrupt_auth_file`, fn`read_auth_json_or_empty_recovering_corrupt`, fn`read_token_by_scope`, fn`read_api_key`, fn`store_api_key`, fn`clear_api_key`
- 调试：`rg -n 'storage' crates/codegen/xai-grok-shell/src`

### 22.424 `auth/token_output.rs`

- 规模：155 行 · 目录 `auth`
- 摘要：Shared parser for an auth command's stdout.
- 符号：fn`expiry_after_seconds`, fn`parse_token_output`, struct`ExternalAuthOutput`, struct`ParsedTokenOutput`
- 调试：`rg -n 'token_output' crates/codegen/xai-grok-shell/src`

### 22.425 `auth/token_type.rs`

- 规模：67 行 · 目录 `auth`
- 摘要：What kind of bearer is loaded right now. Dispatch key for
- 符号：enum`TokenType`
- 调试：`rg -n 'token_type' crates/codegen/xai-grok-shell/src`

### 22.426 `bin/chat-history-downgrade.rs`

- 规模：503 行 · 目录 `bin`
- 摘要：normalize chat_history.jsonl, convert any v1 (ConversationItem) to v0 (ChatRequestMessage) format.
- 调试：`rg -n 'chat-history-downgrade' crates/codegen/xai-grok-shell/src`

### 22.427 `bin/test-sampling-server.rs`

- 规模：58 行 · 目录 `bin`
- 摘要：Usage: cargo run -p xai-grok-shell --bin test-sampling-server
- 符号：struct`Cli`
- 调试：`rg -n 'test-sampling-server' crates/codegen/xai-grok-shell/src`

### 22.428 `bin/trace_classify.rs`

- 规模：224 行 · 目录 `bin`
- 摘要：Replay an offline session trace against the Layer-2 TodoGate and
- 调试：`rg -n 'trace_classify' crates/codegen/xai-grok-shell/src`

### 22.429 `builtin.rs`

- 规模：104 行 · 目录 `.`
- 摘要：Built-in files extracted to `~/.grok/` on startup.
- 符号：fn`extract_builtin_files`
- 调试：`rg -n 'builtin' crates/codegen/xai-grok-shell/src`

### 22.430 `bundle.rs`

- 规模：1508 行 · 目录 `.`
- 摘要：—
- 符号：fn`bundled_root`, fn`read_cached_manifest`, fn`write_bundle_to_cache`, fn`extract_bundle_archive`, fn`checksum_bytes`, fn`checksum_file`, fn`prune_removed_files`, fn`count_entries_by_prefix`
- 调试：`rg -n 'bundle' crates/codegen/xai-grok-shell/src`

### 22.431 `claude_import.rs`

- 规模：2186 行 · 目录 `.`
- 摘要：Scope for an import operation.
- 符号：fn`scan_importable_settings`, fn`find_project_root`, fn`is_claude_import_marked`, fn`refresh_marker_cache`, fn`reset_marker_cache_for_test`, fn`is_claude_import_marked_with_log`, fn`is_claude_import_marked_at`, fn`mark_claude_imported`
- 调试：`rg -n 'claude_import' crates/codegen/xai-grok-shell/src`

### 22.432 `claude_import_state.rs`

- 规模：337 行 · 目录 `.`
- 摘要：Persistent import state, loaded from / saved to `~/.grok/claude_import_state.json`.
- 符号：fn`load_import_state`, fn`save_import_state`, fn`has_new_changes`, fn`mark_imported`, fn`mark_dismissed`, struct`ImportState`, struct`ScopeState`
- 调试：`rg -n 'claude_import_state' crates/codegen/xai-grok-shell/src`

### 22.433 `cli_models.rs`

- 规模：337 行 · 目录 `.`
- 摘要：Data APIs for `grok models`. Clients own display.
- 符号：fn`list_models`, enum`AuthStatus`
- 调试：`rg -n 'cli_models' crates/codegen/xai-grok-shell/src`

### 22.434 `config/mod.rs`

- 规模：1878 行 · 目录 `config`
- 摘要：Full configuration for the memory system.
- 符号：fn`config_origins`, fn`apply_managed_settings_features`, fn`apply_requirements`, fn`apply_sandbox`, fn`load_project_config`, fn`resolve_effective_plugins_config`, fn`add_plugin_path`, fn`remove_plugin_path`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.435 `config/reloader.rs`

- 规模：1055 行 · 目录 `config`
- 摘要：Typed, `Send`-safe messages for the agent to apply inside its `LocalSet`.
- 符号：fn`hash_auth_key`, fn`parse_skills_config`, struct`ConfigReloader`, enum`ConfigUpdate`
- 调试：`rg -n 'reloader' crates/codegen/xai-grok-shell/src`

### 22.436 `config/tests.rs`

- 规模：3617 行 · 目录 `config`
- 摘要：Mutex to serialize tests that touch the GROK_MEMORY env var.
- 调试：`rg -n 'tests' crates/codegen/xai-grok-shell/src`

### 22.437 `config/watcher.rs`

- 规模：1302 行 · 目录 `config`
- 摘要：A [`notify::Watcher`] that drops `EventKind::Access` before it reaches the
- 符号：struct`AccessFilteredWatcher`, struct`ConfigFileWatcher`, struct`SkillsFileWatcher`, struct`ProjectDiscoveryWatcher`, enum`ConfigChangeEvent`, enum`DiscoveryChange`
- 调试：`rg -n 'watcher' crates/codegen/xai-grok-shell/src`

### 22.438 `extensions/auth.rs`

- 规模：252 行 · 目录 `extensions`
- 摘要：`x.ai/auth/*` and legacy `x.ai/{get,set}ApiKey` extension handlers.
- 符号：fn`handle`
- 调试：`rg -n 'auth' crates/codegen/xai-grok-shell/src`

### 22.439 `extensions/auth_gate.rs`

- 规模：18 行 · 目录 `extensions`
- 摘要：Require xAI auth from a sync context, accepting tokens in the client-side buffer window.
- 符号：fn`require_xai_auth`
- 调试：`rg -n 'auth_gate' crates/codegen/xai-grok-shell/src`

### 22.440 `extensions/billing.rs`

- 规模：610 行 · 目录 `extensions`
- 摘要：`x.ai/billing` extension handler.
- 符号：fn`handle`, struct`BillingCycle`, struct`Cent`, struct`UsagePeriod`, struct`BillingPeriodUsage`, struct`BillingConfig`, struct`BillingConfigResponse`, struct`AutoTopupRule`
- 调试：`rg -n 'billing' crates/codegen/xai-grok-shell/src`

### 22.441 `extensions/bundle.rs`

- 规模：1172 行 · 目录 `extensions`
- 摘要：ACP extension handlers for bundled subagent cache sync and status.
- 符号：fn`has_bundle_credentials`, fn`handle`, fn`bundle_cache_is_fresh`, fn`maybe_sync_bundle_to_root`, fn`sync_bundle_to_root`, struct`BundleSyncResult`, struct`BundleStatusResult`, struct`PersonaDetail`
- 调试：`rg -n 'bundle' crates/codegen/xai-grok-shell/src`

### 22.442 `extensions/chat_conversation_history.rs`

- 规模：12 行 · 目录 `extensions`
- 摘要：`x.ai/session/load_history`: fetch one older page of a gateway-backed
- 符号：fn`handle`
- 调试：`rg -n 'chat_conversation_history' crates/codegen/xai-grok-shell/src`

### 22.443 `extensions/code_nav.rs`

- 规模：439 行 · 目录 `extensions`
- 摘要：Code Navigation Extension Methods
- 符号：fn`handle`, struct`GotoRequest`, struct`FindSymbolRequest`, struct`StatusRequest`, struct`CodeNavResponse`, struct`SymbolLocation`, struct`StatusResponse`, enum`IndexStatusReason`
- 调试：`rg -n 'code_nav' crates/codegen/xai-grok-shell/src`

### 22.444 `extensions/debug.rs`

- 规模：135 行 · 目录 `extensions`
- 摘要：`x.ai/debug/*` extension handlers for local client testing.
- 符号：fn`handle`
- 调试：`rg -n 'debug' crates/codegen/xai-grok-shell/src`

### 22.445 `extensions/feedback.rs`

- 规模：480 行 · 目录 `extensions`
- 摘要：`x.ai/feedback`, `x.ai/feedback/dismiss`, `x.ai/btw`, and `x.ai/review/*`
- 符号：fn`handle`
- 调试：`rg -n 'feedback' crates/codegen/xai-grok-shell/src`

### 22.446 `extensions/fs.rs`

- 规模：252 行 · 目录 `extensions`
- 摘要：Filesystem extension API layer.
- 符号：fn`is_fs_method`, fn`handle`, struct`FsListRequest`, struct`FsExistsRequest`, struct`FsReadFileRequest`, struct`FsWriteFileRequest`, struct`FsDeleteFileRequest`
- 调试：`rg -n 'fs' crates/codegen/xai-grok-shell/src`

### 22.447 `extensions/git.rs`

- 规模：702 行 · 目录 `extensions`
- 摘要：Git extension API layer.
- 符号：fn`handle`, struct`GitStatusRequest`, struct`GitFilesRequest`, struct`GitDiffsRequest`, struct`GitStageRequest`, struct`GitStageContentRequest`, struct`GitUnstageRequest`, struct`GitDiscardRequest`
- 调试：`rg -n 'git' crates/codegen/xai-grok-shell/src`

### 22.448 `extensions/hooks.rs`

- 规模：563 行 · 目录 `extensions`
- 摘要：`x.ai/hooks/*` extension handlers.
- 符号：fn`hook_spec_to_info`, fn`parse_client_hooks`, fn`reconnect_client_hooks`, fn`handle`, struct`ClientHookGroup`, struct`ClientHookDispatch`, struct`ClientHookResponse`, enum`ClientHookDecision`
- 调试：`rg -n 'hooks' crates/codegen/xai-grok-shell/src`

### 22.449 `extensions/hunk_tracker.rs`

- 规模：1030 行 · 目录 `extensions`
- 摘要：Hunk Tracker extension API layer.
- 符号：fn`handle`, struct`GetHunksRequest`, struct`GetFilesRequest`, struct`HunkActionRequest`, struct`FileActionRequest`, struct`TurnActionRequest`, struct`AllActionRequest`, struct`GetSummaryRequest`
- 调试：`rg -n 'hunk_tracker' crates/codegen/xai-grok-shell/src`

### 22.450 `extensions/interject.rs`

- 规模：122 行 · 目录 `extensions`
- 摘要：`x.ai/interject` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'interject' crates/codegen/xai-grok-shell/src`

### 22.451 `extensions/jj.rs`

- 规模：64 行 · 目录 `extensions`
- 摘要：Jujutsu extension handlers — delegates to [`xai_grok_workspace::session::jj`].
- 符号：fn`try_handle`
- 调试：`rg -n 'jj' crates/codegen/xai-grok-shell/src`

### 22.452 `extensions/marketplace.rs`

- 规模：1973 行 · 目录 `extensions`
- 摘要：`x.ai/marketplace/*` extension handlers.
- 符号：fn`handle`, fn`purge_default_skills_installs`, fn`ensure_official_marketplace_source`
- 调试：`rg -n 'marketplace' crates/codegen/xai-grok-shell/src`

### 22.453 `extensions/mcp.rs`

- 规模：2524 行 · 目录 `extensions`
- 摘要：MCP extension methods and business logic.
- 符号：fn`notify_servers_updated`, fn`handle`, fn`build_mcp_catalog`, fn`build_mcp_catalog_with_gateway_tools`, fn`build_mcp_status`, fn`init_agent_mcp_pool`, fn`call_mcp_tool`, fn`read_mcp_resource`
- 调试：`rg -n 'mcp' crates/codegen/xai-grok-shell/src`

### 22.454 `extensions/memory.rs`

- 规模：101 行 · 目录 `extensions`
- 摘要：`x.ai/memory/flush`, `x.ai/memory/rewrite`, and `x.ai/compact_conversation`
- 符号：fn`handle`
- 调试：`rg -n 'memory' crates/codegen/xai-grok-shell/src`

### 22.455 `extensions/mod.rs`

- 规模：90 行 · 目录 `extensions`
- 摘要：Deserialize ACP params from their raw JSON string, mapping a parse failure
- 符号：fn`parse_params`, fn`parse_params_str`, fn`parse_session_id`, fn`to_ext_response`, fn`to_raw_response`, fn`to_ext_response_partial`, struct`Empty`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.456 `extensions/notification.rs`

- 规模：2389 行 · 目录 `extensions`
- 摘要：Retained for wire backwards compatibility; always empty in the
- 符号：fn`ticks_to_usd`, fn`uncached_input_tokens`, fn`project_result_usage`, fn`attach_result_usage_fail_closed`, fn`is_reauthable_failure`, struct`GoalDeliverableInfo`, struct`WorkflowPhaseInfo`, struct`WorkflowAgentInfo`
- 调试：`rg -n 'notification' crates/codegen/xai-grok-shell/src`

### 22.457 `extensions/plugins.rs`

- 规模：307 行 · 目录 `extensions`
- 摘要：`x.ai/plugins/*` extension handlers.
- 符号：fn`loaded_plugin_to_info`, fn`handle`
- 调试：`rg -n 'plugins' crates/codegen/xai-grok-shell/src`

### 22.458 `extensions/pr.rs`

- 规模：253 行 · 目录 `extensions`
- 摘要：`gh pr view --json` does not expose `isInMergeQueue`; query GraphQL via `gh api`.
- 符号：fn`handle`, struct`PrStatusRequest`, struct`PrStatusResponse`, struct`PrData`
- 调试：`rg -n 'pr' crates/codegen/xai-grok-shell/src`

### 22.459 `extensions/privacy.rs`

- 规模：91 行 · 目录 `extensions`
- 摘要：`x.ai/privacy/setCodingDataRetention` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'privacy' crates/codegen/xai-grok-shell/src`

### 22.460 `extensions/prompt_history.rs`

- 规模：162 行 · 目录 `extensions`
- 摘要：`x.ai/prompt_history` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'prompt_history' crates/codegen/xai-grok-shell/src`

### 22.461 `extensions/prompt_meta.rs`

- 规模：71 行 · 目录 `extensions`
- 摘要：Typed metadata for a prompt `TextContent._meta` field.
- 符号：struct`PromptBlockMeta`
- 调试：`rg -n 'prompt_meta' crates/codegen/xai-grok-shell/src`

### 22.462 `extensions/recap.rs`

- 规模：58 行 · 目录 `extensions`
- 摘要：`x.ai/recap` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'recap' crates/codegen/xai-grok-shell/src`

### 22.463 `extensions/repair.rs`

- 规模：295 行 · 目录 `extensions`
- 摘要：`x.ai/session/repair` — out-of-band recovery for sessions bricked by
- 符号：fn`handle`, struct`RepairSessionResponse`
- 调试：`rg -n 'repair' crates/codegen/xai-grok-shell/src`

### 22.464 `extensions/rewind.rs`

- 规模：111 行 · 目录 `extensions`
- 摘要：`x.ai/rewind/*` extension handlers.
- 符号：fn`handle`
- 调试：`rg -n 'rewind' crates/codegen/xai-grok-shell/src`

### 22.465 `extensions/rollout.rs`

- 规模：46 行 · 目录 `extensions`
- 摘要：`x.ai/rollout/survey` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'rollout' crates/codegen/xai-grok-shell/src`

### 22.466 `extensions/routing.rs`

- 规模：143 行 · 目录 `extensions`
- 摘要：Metadata from the request, used for routing notifications back to the
- 符号：fn`inject_routing_meta`, fn`send_routed_notification`, struct`RequestMeta`, struct`NotificationMeta`
- 调试：`rg -n 'routing' crates/codegen/xai-grok-shell/src`

### 22.467 `extensions/search.rs`

- 规模：354 行 · 目录 `extensions`
- 摘要：Search extension API layer (fuzzy file search, content search).
- 符号：fn`handle`, struct`FuzzyOpenResponse`, struct`FuzzyChangeResponse`, struct`FuzzyCloseResponse`, struct`FuzzyOpenRequest`, struct`FuzzyChangeRequest`, struct`FuzzyCloseRequest`, struct`ContentSearchRequest`
- 调试：`rg -n 'search' crates/codegen/xai-grok-shell/src`

### 22.468 `extensions/session_admin.rs`

- 规模：747 行 · 目录 `extensions`
- 摘要：Session-administration extension handlers.
- 符号：fn`handle`
- 调试：`rg -n 'session_admin' crates/codegen/xai-grok-shell/src`

### 22.469 `extensions/session_search.rs`

- 规模：117 行 · 目录 `extensions`
- 摘要：ACP extension handler for session search (`x.ai/session/search`).
- 符号：fn`handle`, struct`SearchSessionsRequest`, struct`SearchSessionsResponse`, struct`SearchSessionHit`
- 调试：`rg -n 'session_search' crates/codegen/xai-grok-shell/src`

### 22.470 `extensions/session_state.rs`

- 规模：309 行 · 目录 `extensions`
- 摘要：`x.ai/session/state` reads a session's metadata columns; `x.ai/session/import`
- 符号：fn`handle_state`, fn`handle_import`
- 调试：`rg -n 'session_state' crates/codegen/xai-grok-shell/src`

### 22.471 `extensions/session_updates.rs`

- 规模：1035 行 · 目录 `extensions`
- 摘要：ACP extension handler for bulk session updates (`x.ai/session/updates`).
- 符号：fn`handle`
- 调试：`rg -n 'session_updates' crates/codegen/xai-grok-shell/src`

### 22.472 `extensions/share.rs`

- 规模：290 行 · 目录 `extensions`
- 摘要：`x.ai/share_session` extension handler.
- 符号：fn`handle`
- 调试：`rg -n 'share' crates/codegen/xai-grok-shell/src`

### 22.473 `extensions/skills.rs`

- 规模：676 行 · 目录 `extensions`
- 摘要：Generic params for methods that only need an optional `cwd`.
- 符号：fn`handle`, struct`SkillsAddRequest`, struct`SkillsAddResponse`, struct`SkillsRemoveRequest`, struct`SkillsRemoveResponse`, struct`SkillsResetResponse`, struct`SkillsToggleRequest`, struct`SkillsListRequest`
- 调试：`rg -n 'skills' crates/codegen/xai-grok-shell/src`

### 22.474 `extensions/suggest/ai_provider.rs`

- 规模：215 行 · 目录 `extensions/suggest`
- 摘要：Request AI-powered shell command suggestions via the session actor.
- 符号：fn`suggest`
- 调试：`rg -n 'ai_provider' crates/codegen/xai-grok-shell/src`

### 22.475 `extensions/suggest/file_provider.rs`

- 规模：1147 行 · 目录 `extensions/suggest`
- 摘要：Filesystem completion for the shell token under the cursor: any
- 符号：struct`FilePathProvider`
- 调试：`rg -n 'file_provider' crates/codegen/xai-grok-shell/src`

### 22.476 `extensions/suggest/history_provider.rs`

- 规模：727 行 · 目录 `extensions/suggest`
- 摘要：Rank history matches from three tiers of history sources.
- 符号：struct`HistoryProvider`
- 调试：`rg -n 'history_provider' crates/codegen/xai-grok-shell/src`

### 22.477 `extensions/suggest/mod.rs`

- 规模：680 行 · 目录 `extensions/suggest`
- 摘要：Deterministic Tab mode: run only the token providers (path/file).
- 符号：fn`handle`, fn`should_skip_ai`, struct`SuggestContext`, struct`RankedSuggestion`, enum`SuggestionSource`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.478 `extensions/suggest/path_provider.rs`

- 规模：407 行 · 目录 `extensions/suggest`
- 摘要：The command token being typed, via the canonical tokenizer: quotes hide
- 符号：struct`PathProvider`
- 调试：`rg -n 'path_provider' crates/codegen/xai-grok-shell/src`

### 22.479 `extensions/suggest/shell_token.rs`

- 规模：640 行 · 目录 `extensions/suggest`
- 摘要：Minimal shell-token syntax for completion: find the token under the
- 调试：`rg -n 'shell_token' crates/codegen/xai-grok-shell/src`

### 22.480 `extensions/task.rs`

- 规模：886 行 · 目录 `extensions`
- 摘要：Wire DTO for the `x.ai/task/kill` ext request.
- 符号：fn`handle`, fn`handle_scheduler`, fn`handle_subagent`, struct`KillTaskRequest`, struct`KillTaskResponse`, struct`CancelSubagentRequest`, struct`CancelSubagentResponse`, enum`SubagentCancelOutcomeDto`
- 调试：`rg -n 'task' crates/codegen/xai-grok-shell/src`

### 22.481 `extensions/terminal.rs`

- 规模：366 行 · 目录 `extensions`
- 摘要：Response for any terminal creation — piped or PTY. Both return just a `terminalId`.
- 符号：fn`handle`, fn`handle_pty_input`, struct`EnvVar`, struct`CreateTerminalRequest`, struct`TerminalIdRequest`, struct`CreateTerminalResponse`, struct`PtyCreateRequest`, struct`PtyLoadRequest`
- 调试：`rg -n 'terminal' crates/codegen/xai-grok-shell/src`

### 22.482 `extensions/usage.rs`

- 规模：104 行 · 目录 `extensions`
- 摘要：`x.ai/session/usage` — cumulative session token/cost as [`PromptUsage`].
- 符号：fn`handle`, struct`SessionUsageResponse`
- 调试：`rg -n 'usage' crates/codegen/xai-grok-shell/src`

### 22.483 `extensions/worktree.rs`

- 规模：619 行 · 目录 `extensions`
- 摘要：Handler for x.ai/git/worktree/* extension methods.
- 符号：fn`handle`, struct`ListWorktreeRequest`, struct`ShowWorktreeRequest`, struct`GcWorktreeRequest`, struct`WorktreeDbPathResponse`, struct`ResolveLocalForWorktreeResumeRequest`, struct`ResolveLocalForWorktreeResumeResponse`
- 调试：`rg -n 'worktree' crates/codegen/xai-grok-shell/src`

### 22.484 `heap_profile/mod.rs`

- 规模：211 行 · 目录 `heap_profile`
- 摘要：Heap-profile IoC seam + threshold monitor.
- 符号：fn`install`, fn`stats`, fn`set_prof_active`, fn`dump_to_path`, fn`prof_available`, struct`HeapProfileHooks`, struct`JemallocStats`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.485 `heap_profile/monitor.rs`

- 规模：1125 行 · 目录 `heap_profile`
- 摘要：Threshold-triggered jemalloc heap dump + upload.
- 符号：fn`should_latch`, fn`is_valid_session_id`, fn`sanitize_version`, fn`object_paths`, fn`normalize_thresholds`, fn`clamp_poll_interval_secs`, fn`resolve_jemalloc_heap_profile`, fn`build_upload_handles`
- 调试：`rg -n 'monitor' crates/codegen/xai-grok-shell/src`

### 22.486 `inspect/compat.rs`

- 规模：305 行 · 目录 `inspect`
- 摘要：Vendor-compat resolution for `grok inspect`.
- 符号：struct`ExternalCompatEntry`, struct`ExternalCompatReport`, enum`CompatEntryStatus`, enum`CompatSource`
- 调试：`rg -n 'compat' crates/codegen/xai-grok-shell/src`

### 22.487 `inspect/mod.rs`

- 规模：2012 行 · 目录 `inspect`
- 摘要：`grok inspect` — configuration introspection.
- 符号：fn`inspect`, struct`InspectReport`, struct`InstructionFile`, struct`PermissionsReport`, struct`EnforcedPolicy`, struct`SkippedRule`, struct`LoginPolicyReport`, struct`HookEntry`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.488 `instrumentation.rs`

- 规模：67 行 · 目录 `.`
- 摘要：Shim — see `xai_grok_telemetry::instrumentation` for the implementation.
- 符号：fn`finalize_and_exit`
- 调试：`rg -n 'instrumentation' crates/codegen/xai-grok-shell/src`

### 22.489 `leader/client.rs`

- 规模：1308 行 · 目录 `leader`
- 摘要：Interval for sending keepalive pings to detect dead connections
- 符号：struct`LeaderRegistration`, struct`LeaderClient`, enum`DisconnectReason`, enum`ClientError`
- 调试：`rg -n 'client' crates/codegen/xai-grok-shell/src`

### 22.490 `leader/lock.rs`

- 规模：601 行 · 目录 `leader`
- 摘要：Compute a short hash suffix from a WS URL for differentiating leader instances.
- 符号：fn`compute_ws_url_suffix`, fn`lock_path_for_ws_url_in`, fn`lock_path_for_ws_url`, fn`socket_path_for_ws_url_in`, fn`socket_path_for_ws_url`, fn`ws_url_suffix_from_paths`, struct`LeaderLock`, enum`LockError`
- 调试：`rg -n 'lock' crates/codegen/xai-grok-shell/src`

### 22.491 `leader/mod.rs`

- 规模：2225 行 · 目录 `leader`
- 摘要：Leader-follower IPC architecture for grok-shell.
- 符号：fn`leader_is_older_than`, fn`discover_leaders`, fn`kill_stale_reachable_leaders`, fn`resolve_leader_target`, fn`connect_or_spawn`, fn`wait_for_socket_connectable`, struct`LeaderEnvUrls`, struct`LeaderTargetError`
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.492 `leader/protocol.rs`

- 规模：734 行 · 目录 `leader`
- 摘要：Unique identifier for a connected client.
- 符号：fn`read_frame`, fn`write_frame`, fn`read_message`, fn`write_message`, struct`ClientId`, struct`ClientCapabilities`, struct`LeaderCapabilities`, enum`ProtocolError`
- 调试：`rg -n 'protocol' crates/codegen/xai-grok-shell/src`

### 22.493 `leader/server.rs`

- 规模：6783 行 · 目录 `leader`
- 摘要：The binary version of the currently running leader process.
- 符号：fn`run_leader_server`, fn`spawn_leader_server`, struct`LeaderServerMetadata`, struct`LeaderServerControlState`, struct`WorkspaceControl`, struct`ServerHandle`, enum`ServerError`
- 调试：`rg -n 'server' crates/codegen/xai-grok-shell/src`

### 22.494 `leader/test_support.rs`

- 规模：178 行 · 目录 `leader`
- 摘要：In-crate fake leaders for exercising client-side handling of misbehaving
- 符号：fn`fake_caps`, fn`spawn_fake_leader`, struct`FakeVersions`, struct`FakeLeaderHandle`, enum`FakeLeaderBehavior`
- 调试：`rg -n 'test_support' crates/codegen/xai-grok-shell/src`

### 22.495 `leader/transport.rs`

- 规模：304 行 · 目录 `leader`
- 摘要：Cross-platform IPC transport for leader<->client communication.
- 符号：fn`listener_is_ready`
- 调试：`rg -n 'transport' crates/codegen/xai-grok-shell/src`

### 22.496 `lib.rs`

- 规模：46 行 · 目录 `.`
- 摘要：—
- 调试：`rg -n 'lib' crates/codegen/xai-grok-shell/src`

### 22.497 `managed_config/response.rs`

- 规模：257 行 · 目录 `managed_config`
- 摘要：The deployment-config fetch/response contract: the credential source and its
- 符号：enum`ManagedConfigError`
- 调试：`rg -n 'response' crates/codegen/xai-grok-shell/src`

### 22.498 `managed_config/tests.rs`

- 规模：446 行 · 目录 `managed_config`
- 摘要：Fail closed only for a managed principal AND compromised policy; every other combination proceeds.
- 调试：`rg -n 'tests' crates/codegen/xai-grok-shell/src`

### 22.499 `managed_config.rs`

- 规模：1123 行 · 目录 `.`
- 摘要：Sync `managed_config.toml` + `requirements.toml` from the deployment-config endpoint per principal.
- 符号：fn`has_active_team_auth`, fn`clear_orphan`, fn`spawn_sync`, fn`resolve_deployment_id`, fn`resolve_deployment_key`, fn`is_fetch_enabled`, fn`sync`, fn`post_login_sync`
- 调试：`rg -n 'managed_config' crates/codegen/xai-grok-shell/src`

### 22.500 `mcp_doctor.rs`

- 规模：737 行 · 目录 `.`
- 摘要：`grok mcp doctor` -- runtime health check for MCP servers.
- 符号：fn`run_doctor`, fn`print_report`, struct`ConfigSourceStatus`, struct`McpServerStatus`, struct`Check`, struct`DoctorReport`, enum`ConfigSourceState`
- 调试：`rg -n 'mcp_doctor' crates/codegen/xai-grok-shell/src`

### 22.501 `plugin.rs`

- 规模：2234 行 · 目录 `.`
- 摘要：Shared plugin lifecycle operations (output-agnostic).
- 符号：fn`install_source_is_local`, fn`install_plugin`, fn`uninstall_plugin`, fn`repo_update_requires_reload`, fn`update_plugins`, fn`update_plugins_by_selector`, fn`normalize_git_url`, fn`name_from_url`
- 调试：`rg -n 'plugin' crates/codegen/xai-grok-shell/src`

### 22.502 `relay/mod.rs`

- 规模：17 行 · 目录 `relay`
- 摘要：Relay session sharing module.
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.503 `relay/sync.rs`

- 规模：1141 行 · 目录 `relay`
- 摘要：WebSocket relay sync for real-time session sharing.
- 符号：fn`build_share_url`, struct`RelaySyncState`, struct`SyncStatus`, struct`RelaySync`, enum`ConnectionState`
- 调试：`rg -n 'sync' crates/codegen/xai-grok-shell/src`

### 22.504 `relay/types.rs`

- 规模：51 行 · 目录 `relay`
- 摘要：Shared types for relay session sharing.
- 符号：enum`AgentType`
- 调试：`rg -n 'types' crates/codegen/xai-grok-shell/src`

### 22.505 `remote/agent.rs`

- 规模：327 行 · 目录 `remote`
- 摘要：Remote sandbox client for cli-chat-proxy.
- 符号：struct`SandboxClient`
- 调试：`rg -n 'agent' crates/codegen/xai-grok-shell/src`

### 22.506 `remote/chat_models_client.rs`

- 规模：206 行 · 目录 `remote`
- 摘要：grok.com chat-product model catalog (`POST /rest/modes`) — the models
- 符号：struct`Mode`, struct`ModeAvailability`, struct`ListModesResponse`, struct`ChatModelsClient`, enum`ChatModelsError`
- 调试：`rg -n 'chat_models_client' crates/codegen/xai-grok-shell/src`

### 22.507 `remote/client.rs`

- 规模：2115 行 · 目录 `remote`
- 摘要：HTTP client for backend CRUD operations.
- 符号：fn`share_url`, fn`fetch_subagent_bundle`, fn`fetch_bundle`, fn`fetch_settings_blocking`, fn`fetch_login_device_flow`, fn`models_list_url`, fn`fetch_models_blocking`, fn`parse_remote_model_value`
- 调试：`rg -n 'client' crates/codegen/xai-grok-shell/src`

### 22.508 `remote/conversations_client.rs`

- 规模：309 行 · 目录 `remote`
- 摘要：Body for `PUT /rest/app-chat/conversations/{id}` (grok-web `chatUpdateConversation`).
- 符号：struct`Conversation`, struct`Workspace`, struct`ConvQuery`, struct`ListConversationsPage`, struct`UpdateConversationBody`, struct`ConversationsClient`, enum`ConvError`
- 调试：`rg -n 'conversations_client' crates/codegen/xai-grok-shell/src`

### 22.509 `remote/mod.rs`

- 规模：37 行 · 目录 `remote`
- 摘要：Remote storage client for the backend.
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.510 `remote/pull.rs`

- 规模：458 行 · 目录 `remote`
- 摘要：Pull-on-miss: fetch a session from the backend and hydrate local JSONL storage.
- 符号：fn`pull_session_to_local`, enum`PullResult`
- 调试：`rg -n 'pull' crates/codegen/xai-grok-shell/src`

### 22.511 `remote/pull_smoke_test.rs`

- 规模：127 行 · 目录 `remote`
- 摘要：Push → pull round-trip smoke test against the live backend.
- 调试：`rg -n 'pull_smoke_test' crates/codegen/xai-grok-shell/src`

### 22.512 `remote/sync.rs`

- 规模：182 行 · 目录 `remote`
- 摘要：Writeback push: async queue that flushes session updates to the backend.
- 符号：struct`RemoteSync`
- 调试：`rg -n 'sync' crates/codegen/xai-grok-shell/src`

### 22.513 `remote/workspaces_client.rs`

- 规模：180 行 · 目录 `remote`
- 摘要：—
- 符号：struct`Workspace`, struct`WsQuery`, struct`ListWorkspacesPage`, struct`WorkspacesClient`, enum`WsError`
- 调试：`rg -n 'workspaces_client' crates/codegen/xai-grok-shell/src`

### 22.514 `sampling/conversation.rs`

- 规模：40 行 · 目录 `sampling`
- 摘要：API-agnostic conversation representation.
- 符号：struct`ConversationRequestTrace`
- 调试：`rg -n 'conversation' crates/codegen/xai-grok-shell/src`

### 22.515 `sampling/error.rs`

- 规模：774 行 · 目录 `sampling`
- 摘要：Sampling error types.
- 符号：fn`is_free_usage_exhausted_error`, fn`format_rate_limited_user_message`, fn`map_sampling_err_to_acp`, fn`error_data_with_status`, fn`terminal_error_data`, fn`stop_reason_for_turn_error`, fn`error_detail_from_data`, fn`http_status_from_error`
- 调试：`rg -n 'error' crates/codegen/xai-grok-shell/src`

### 22.516 `sampling/mod.rs`

- 规模：32 行 · 目录 `sampling`
- 摘要：—
- 调试：`rg -n 'mod' crates/codegen/xai-grok-shell/src`

### 22.517 `sampling/types.rs`

- 规模：64 行 · 目录 `sampling`
- 摘要：Render an `ImageContent` produced by the read-file tool as a URL
- 符号：fn`get_image_content_url`
- 调试：`rg -n 'types' crates/codegen/xai-grok-shell/src`

### 22.518 `session/acp_conversion.rs`

- 规模：1328 行 · 目录 `session`
- 摘要：ACP conversion functions for `xai-grok-tools`'s `ToolOutput`.
- 符号：fn`maybe_rewrite`, fn`raw_output_json`, fn`acp_tool_update`, fn`acp_plan_update`, struct`PathRewriter`
- 调试：`rg -n 'acp_conversion' crates/codegen/xai-grok-shell/src`

### 22.519 `session/acp_mcp.rs`

- 规模：144 行 · 目录 `session`
- 摘要：In-process SDK MCP servers over the ACP reverse channel (`x.ai/mcp/sdk_call`).
- 符号：fn`parse_acp_mcp_servers`, struct`GatewayAcpInvoker`
- 调试：`rg -n 'acp_mcp' crates/codegen/xai-grok-shell/src`

### 22.520 `session/acp_session/hooks.rs`

- 规模：566 行 · 目录 `session/acp_session`
- 摘要：Client-registered hooks for [`SessionActor`].
- 调试：`rg -n 'hooks' crates/codegen/xai-grok-shell/src`

### 22.521 `session/acp_session.rs`

- 规模：2070 行 · 目录 `session`
- 摘要：Session actor implementation for the MVP ACP agent.
- 符号：fn`is_session_idle_for_injection`, fn`state_is_busy`, fn`load_system_prompt`, fn`load_prompt_context`, struct`InputItem`, struct`State`, struct`PreparedToolCall`, struct`ModelAuthMemo`
- 调试：`rg -n 'acp_session' crates/codegen/xai-grok-shell/src`

### 22.522 `session/acp_session_impl/extensions/idle_prompt.rs`

- 规模：130 行 · 目录 `session/acp_session_impl/extensions`
- 摘要：Debounced `idle_prompt` notification extension.
- 调试：`rg -n 'idle_prompt' crates/codegen/xai-grok-shell/src`

### 22.523 `session/acp_session_impl/extensions.rs`

- 规模：57 行 · 目录 `session/acp_session_impl`
- 摘要：Composition root for the session's extensions: each lives in its own submodule and installs itself here.
- 调试：`rg -n 'extensions' crates/codegen/xai-grok-shell/src`

### 22.524 `session/acp_session_impl/goal.rs`

- 规模：2323 行 · 目录 `session/acp_session_impl`
- 摘要：Goal-orchestration concern for `SessionActor`.
- 符号：struct`PanelResolveCache`, enum`RoleCapability`, enum`GapsUpdate`
- 调试：`rg -n 'goal' crates/codegen/xai-grok-shell/src`

### 22.525 `session/acp_session_impl/goal_support.rs`

- 规模：1586 行 · 目录 `session/acp_session_impl`
- 摘要：Goal-harness support for `SessionActor`: reminder/directive templates,
- 符号：fn`planner_failure_pause_message`, fn`goal_slash_and_harness_available`, fn`laziness_injection_active`, struct`GoalClassifierPolicy`, struct`SubagentTokenRecord`, struct`GoalRoleModelConfig`
- 调试：`rg -n 'goal_support' crates/codegen/xai-grok-shell/src`

### 22.526 `session/acp_session_impl/hook_dispatch.rs`

- 规模：385 行 · 目录 `session/acp_session_impl`
- 摘要：Hook dispatch concern for `SessionActor`: run contexts, hook execution
- 调试：`rg -n 'hook_dispatch' crates/codegen/xai-grok-shell/src`

### 22.527 `session/acp_session_impl/hooks_plugins.rs`

- 规模：1050 行 · 目录 `session/acp_session_impl`
- 摘要：Trust the current project via the unified folder-trust store. Now an
- 调试：`rg -n 'hooks_plugins' crates/codegen/xai-grok-shell/src`

### 22.528 `session/acp_session_impl/interjection.rs`

- 规模：338 行 · 目录 `session/acp_session_impl`
- 摘要：Mid-turn interjection concern for `SessionActor` (buffer type, formatting,
- 调试：`rg -n 'interjection' crates/codegen/xai-grok-shell/src`

### 22.529 `session/acp_session_impl/laziness.rs`

- 规模：884 行 · 目录 `session/acp_session_impl`
- 摘要：Laziness / stop-detector concern for `SessionActor`.
- 符号：fn`build_laziness_debug_line`, fn`classify_debug_decision`, fn`append_laziness_debug_log_line`, struct`LazinessFireMeta`, struct`DebugClassifierOutput`, struct`DebugTodoSnapshot`, struct`LazinessDebugLogLine`, enum`LazinessFireOutcome`
- 调试：`rg -n 'laziness' crates/codegen/xai-grok-shell/src`

### 22.530 `session/acp_session_impl/laziness_classifier.rs`

- 规模：869 行 · 目录 `session/acp_session_impl`
- 摘要：Layer-3 LazinessDetector pure helpers: classifier prompt/config consts,
- 符号：fn`turn_elapsed_seconds_from_start_ms`, fn`format_runtime_state_line`, fn`flatten_transcript_for_classifier`, fn`neutralize_transcript_user_text`, fn`build_classifier_turns`, fn`agents_md_classifier_body`, fn`should_set_classifier_project_instructions`, fn`laziness_window_start`
- 调试：`rg -n 'laziness_classifier' crates/codegen/xai-grok-shell/src`

### 22.531 `session/acp_session_impl/mcp.rs`

- 规模：1881 行 · 目录 `session/acp_session_impl`
- 摘要：Wait for MCP tools to be initialized.
- 调试：`rg -n 'mcp' crates/codegen/xai-grok-shell/src`

### 22.532 `session/acp_session_impl/mcp_snapshot.rs`

- 规模：441 行 · 目录 `session/acp_session_impl`
- 摘要：MCP snapshot concern for `SessionActor`: server-snapshot refresh and
- 符号：fn`refresh_mcp_snapshot_for_test`, fn`refresh_mcp_snapshot_for_test_with_disabled`
- 调试：`rg -n 'mcp_snapshot' crates/codegen/xai-grok-shell/src`

### 22.533 `session/acp_session_impl/memory_dream.rs`

- 规模：768 行 · 目录 `session/acp_session_impl`
- 摘要：Memory concern for `SessionActor`: memory flush, the dream pipeline,
- 调试：`rg -n 'memory_dream' crates/codegen/xai-grok-shell/src`

### 22.534 `session/acp_session_impl/model_switch.rs`

- 规模：342 行 · 目录 `session/acp_session_impl`
- 摘要：Handle [`SessionCommand::RebuildAgentForDefinition`].
- 调试：`rg -n 'model_switch' crates/codegen/xai-grok-shell/src`

### 22.535 `session/acp_session_impl/notification_drain.rs`

- 规模：626 行 · 目录 `session/acp_session_impl`
- 摘要：Idle-gated pending-notification buffering and drain for `SessionActor`,
- 符号：struct`PendingNotification`
- 调试：`rg -n 'notification_drain' crates/codegen/xai-grok-shell/src`

### 22.536 `session/acp_session_impl/prompt_build.rs`

- 规模：906 行 · 目录 `session/acp_session_impl`
- 摘要：User-message construction concern for `SessionActor`: templated prefix
- 调试：`rg -n 'prompt_build' crates/codegen/xai-grok-shell/src`

### 22.537 `session/acp_session_impl/prompt_queue.rs`

- 规模：955 行 · 目录 `session/acp_session_impl`
- 摘要：Running-turn display fields for `x.ai/queue/changed` (clients paint turn-start UI).
- 调试：`rg -n 'prompt_queue' crates/codegen/xai-grok-shell/src`

### 22.538 `session/acp_session_impl/recap.rs`

- 规模：691 行 · 目录 `session/acp_session_impl`
- 摘要：Auxiliary model-call concern for `SessionActor`: side questions, recap
- 调试：`rg -n 'recap' crates/codegen/xai-grok-shell/src`

### 22.539 `session/acp_session_impl/reminders.rs`

- 规模：861 行 · 目录 `session/acp_session_impl`
- 摘要：System-reminder injection concern for `SessionActor`: reminder policy,
- 符号：fn`evaluate_todo_gate`, fn`resolve_reminder_policy`, fn`date_rollover_reminder`, struct`CollectedTodoGateInput`, struct`TodoGateInput`
- 调试：`rg -n 'reminders' crates/codegen/xai-grok-shell/src`

### 22.540 `session/acp_session_impl/rewind.rs`

- 规模：583 行 · 目录 `session/acp_session_impl`
- 摘要：Rewind concern for `SessionActor`: rewind points, cross-compaction
- 调试：`rg -n 'rewind' crates/codegen/xai-grok-shell/src`

### 22.541 `session/acp_session_impl/run_loop.rs`

- 规模：2239 行 · 目录 `session/acp_session_impl`
- 摘要：The session actor's main loop (`run_session`): command dispatch, idle
- 调试：`rg -n 'run_loop' crates/codegen/xai-grok-shell/src`

### 22.542 `session/acp_session_impl/sampler_turn.rs`

- 规模：1490 行 · 目录 `session/acp_session_impl`
- 摘要：Sampler-turn pipeline for `SessionActor`: tool definitions, model auth
- 调试：`rg -n 'sampler_turn' crates/codegen/xai-grok-shell/src`

### 22.543 `session/acp_session_impl/session_mode.rs`

- 规模：364 行 · 目录 `session/acp_session_impl`
- 摘要：Session/plan-mode concern for `SessionActor` (`handle_session_mode`,
- 调试：`rg -n 'session_mode' crates/codegen/xai-grok-shell/src`

### 22.544 `session/acp_session_impl/session_setup.rs`

- 规模：639 行 · 目录 `session/acp_session_impl`
- 摘要：Session initialization concern for `SessionActor`: `initialize`, prefix
- 调试：`rg -n 'session_setup' crates/codegen/xai-grok-shell/src`

### 22.545 `session/acp_session_impl/slash_exec.rs`

- 规模：1020 行 · 目录 `session/acp_session_impl`
- 摘要：Execute a built-in slash command (e.g. `/compact`, `/yolo`).
- 调试：`rg -n 'slash_exec' crates/codegen/xai-grok-shell/src`

### 22.546 `session/acp_session_impl/spawn.rs`

- 规模：2539 行 · 目录 `session/acp_session_impl`
- 摘要：Session bring-up concern for `acp_session`: `spawn_session_actor`, the
- 符号：fn`spawn_session_actor`, fn`spawn_session_on_thread`, struct`SessionThread`, struct`SessionRestartActions`
- 调试：`rg -n 'spawn' crates/codegen/xai-grok-shell/src`

### 22.547 `session/acp_session_impl/stop_gate.rs`

- 规模：498 行 · 目录 `session/acp_session_impl`
- 摘要：The turn-end `Stop`/`SubagentStop` gate for `SessionActor`.
- 调试：`rg -n 'stop_gate' crates/codegen/xai-grok-shell/src`

### 22.548 `session/acp_session_impl/tasks_cancel.rs`

- 规模：744 行 · 目录 `session/acp_session_impl`
- 摘要：Prompt-task plumbing for `SessionActor` (`AgentTask`, `TaskSlot`,
- 符号：struct`AgentTask`, struct`TaskSlot`
- 调试：`rg -n 'tasks_cancel' crates/codegen/xai-grok-shell/src`

### 22.549 `session/acp_session_impl/tool_calls.rs`

- 规模：3160 行 · 目录 `session/acp_session_impl`
- 摘要：Tool-call execution concern for `SessionActor`: the model-output →
- 调试：`rg -n 'tool_calls' crates/codegen/xai-grok-shell/src`

### 22.550 `session/acp_session_impl/tool_dispatch.rs`

- 规模：411 行 · 目录 `session/acp_session_impl`
- 摘要：Tool dispatch helpers for `SessionActor`: `dispatch_tool` and its lock /
- 调试：`rg -n 'tool_dispatch' crates/codegen/xai-grok-shell/src`

### 22.551 `session/acp_session_impl/turn.rs`

- 规模：2770 行 · 目录 `session/acp_session_impl`
- 摘要：Turn-execution concern for `SessionActor` (`handle_prompt`, turn-end,
- 调试：`rg -n 'turn' crates/codegen/xai-grok-shell/src`

### 22.552 `session/acp_session_impl/turn_end.rs`

- 规模：475 行 · 目录 `session/acp_session_impl`
- 摘要：Turn-completion concern for `SessionActor`: completion handling
- 调试：`rg -n 'turn_end' crates/codegen/xai-grok-shell/src`

### 22.553 `session/acp_session_impl/types.rs`

- 规模：261 行 · 目录 `session/acp_session_impl`
- 摘要：Top-level enum definitions for `acp_session`; their `impl` blocks and
- 符号：enum`McpReminderMode`, enum`SamplerFailureRecovery`, enum`SamplerTurnOutcome`, enum`TurnOutcome`, enum`ToolLoop`, enum`TodoGateReason`, enum`TodoGateDecision`, enum`LazinessAbortReason`
- 调试：`rg -n 'types' crates/codegen/xai-grok-shell/src`

### 22.554 `session/acp_session_impl/updates.rs`

- 规模：1009 行 · 目录 `session/acp_session_impl`
- 摘要：Outbound update emission concern for `SessionActor`: `send_update` and
- 调试：`rg -n 'updates' crates/codegen/xai-grok-shell/src`

### 22.555 `session/acp_session_impl/workflow.rs`

- 规模：371 行 · 目录 `session/acp_session_impl`
- 摘要：—
- 符号：fn`parse_named_workflow_args`
- 调试：`rg -n 'workflow' crates/codegen/xai-grok-shell/src`

### 22.556 `session/acp_session_tests/support.rs`

- 规模：615 行 · 目录 `session/acp_session_tests`
- 摘要：Wrap `id` in a shared auth-method handle for `SessionActor` test literals
- 符号：fn`test_auth_method_id`, fn`noop_observability_bridge`, fn`test_agent_default`, fn`test_agent_backend_search`, fn`test_agent_with_goal_tool`, fn`test_grok_build_agent_with_todo`, fn`test_agent_with_plan_tools`, fn`test_agent_with_tools`
- 调试：`rg -n 'support' crates/codegen/xai-grok-shell/src`

### 22.557 `session/acp_types.rs`

- 规模：917 行 · 目录 `session`
- 摘要：Public wire types (DTOs) for the ACP session actor.
- 符号：fn`default_rewind_mode`, fn`count_detail`, fn`is_coding_model_slug`, fn`should_show_model_fingerprint`, fn`model_display_name`, struct`SessionListRequest`, struct`AllSessionOverviewRequest`, struct`SessionListResponse`
- 调试：`rg -n 'acp_types' crates/codegen/xai-grok-shell/src`

### 22.558 `session/agent_rebuild.rs`

- 规模：539 行 · 目录 `session`
- 摘要：`AgentRebuildSpec` — the canonical recipe for constructing an
- 符号：fn`test_rebuild_spec_default`, struct`ResolvedToolParamsJson`, struct`AgentRebuildSpec`
- 调试：`rg -n 'agent_rebuild' crates/codegen/xai-grok-shell/src`

### 22.559 `session/announcement_state.rs`

- 规模：119 行 · 目录 `session`
- 摘要：Persisted announcement tracking state for session resumption.
- 符号：fn`to_persisted_fingerprints`, fn`from_persisted_fingerprints`, struct`AnnouncementState`, struct`McpServerFingerprint`
- 调试：`rg -n 'announcement_state' crates/codegen/xai-grok-shell/src`

### 22.560 `session/chat_persistence.rs`

- 规模：127 行 · 目录 `session`
- 摘要：Production `ChatPersistence` implementation backed by the existing persistence channel.
- 符号：struct`ChannelChatPersistence`
- 调试：`rg -n 'chat_persistence' crates/codegen/xai-grok-shell/src`

### 22.561 `session/commands.rs`

- 规模：780 行 · 目录 `session`
- 摘要：Session actor command enum and associated public types.
- 符号：fn`ok_end_turn`, struct`CancellationContext`, struct`PromptTurnOk`, struct`ParsedPromptInfo`, struct`TaskWakeFallback`, struct`TaskWakeAdmission`, enum`PromptCompletionKind`, enum`NotificationPriority`
- 调试：`rg -n 'commands' crates/codegen/xai-grok-shell/src`

### 22.562 `session/compaction.rs`

- 规模：3834 行 · 目录 `session`
- 摘要：Compaction methods for `SessionActor`.
- 符号：struct`AutoCompactTriggerInfo`, enum`SuppressReason`
- 调试：`rg -n 'compaction' crates/codegen/xai-grok-shell/src`

### 22.563 `session/compaction_config.rs`

- 规模：211 行 · 目录 `session`
- 摘要：Compaction configuration and runtime state for the session actor.
- 符号：struct`PreviousModelInfo`, struct`AsyncCompactionCache`, struct`PrefireState`, struct`CompactionConfig`
- 调试：`rg -n 'compaction_config' crates/codegen/xai-grok-shell/src`

### 22.564 `session/compaction_segments.rs`

- 规模：59 行 · 目录 `session`
- 摘要：Shell-side dispatch on [`CompactionMode`]. Split into two methods so the
- 调试：`rg -n 'compaction_segments' crates/codegen/xai-grok-shell/src`

### 22.565 `session/events.rs`

- 规模：1008 行 · 目录 `session`
- 摘要：Re-exports of the crate-internal event types that live in
- 符号：fn`prior_turn_interrupt_from_cancellation`, enum`LazinessCategory`, enum`GoalClassifierFailOpenReason`, enum`GoalClassifierFailClosedReason`, enum`GoalPlannerFailClosedReason`, enum`GoalStrategistFailReason`, enum`GoalStrategistRestoreFailReason`, enum`GoalSummarizerFailReason`
- 调试：`rg -n 'events' crates/codegen/xai-grok-shell/src`

### 22.566 `session/export.rs`

- 规模：171 行 · 目录 `session`
- 摘要：Session export for sharing via the remote session-sharing backend.
- 符号：struct`ExportedMessage`, struct`ExportedMetadata`, struct`ExportedSession`
- 调试：`rg -n 'export' crates/codegen/xai-grok-shell/src`

### 22.567 `session/feedback.rs`

- 规模：1170 行 · 目录 `session`
- 摘要：Feedback request heuristics for Grok Code sessions.
- 符号：struct`TriggerCondition`, struct`TriggerSignalSnapshot`, struct`FeedbackEvaluation`, struct`FeedbackHeuristics`, struct`FeedbackRequest`, enum`FeedbackTier`
- 调试：`rg -n 'feedback' crates/codegen/xai-grok-shell/src`

### 22.568 `session/feedback_manager.rs`

- 规模：1659 行 · 目录 `session`
- 摘要：Feedback manager for session-level feedback collection.
- 符号：fn`new_submission`, fn`submit_feedback_workflow`, struct`SubmitFeedbackOptions`, struct`SessionFeedbackData`, struct`FeedbackFlags`, struct`FeedbackManagerConfig`, struct`FeedbackManager`, enum`SubmitOutcome`
- 调试：`rg -n 'feedback_manager' crates/codegen/xai-grok-shell/src`

### 22.569 `session/file_system.rs`

- 规模：359 行 · 目录 `session`
- 摘要：Pagination offset applied after the dirs-first sort (default 0).
- 符号：fn`list`, fn`exists`, fn`read_file`, fn`read_file_ranged`, fn`check_file_size_limits`, fn`write_file`, fn`build_file_entry`, fn`delete_file`
- 调试：`rg -n 'file_system' crates/codegen/xai-grok-shell/src`

### 22.570 `session/fork.rs`

- 规模：326 行 · 目录 `session`
- 摘要：Session forking functionality
- 符号：fn`fork_session`, struct`ForkSessionRequest`, struct`ForkSessionResponse`
- 调试：`rg -n 'fork' crates/codegen/xai-grok-shell/src`

### 22.571 `session/fs_watch.rs`

- 规模：1650 行 · 目录 `session`
- 摘要：Session-level fs-watch policy over [`xai_fsnotify`].
- 符号：fn`is_under_hidden_dir`, fn`forward_to_hunk_tracker`, fn`git_head_dedup_key`, fn`spawn`, struct`FsWatchCapabilities`, struct`CapabilityInputs`, struct`FsWatchDeps`, struct`FsWatchPlan`
- 调试：`rg -n 'fs_watch' crates/codegen/xai-grok-shell/src`

### 22.572 `session/goal_classifier/evidence.rs`

- 规模：2178 行 · 目录 `session/goal_classifier`
- 摘要：Evidence-packet construction for the goal-verification stage.
- 符号：fn`extract_changed_files`, fn`build_classifier_evidence_packet`, fn`capture_changes_diff`, fn`capture_plan_changes`, fn`parse_created_at_to_unix`, fn`now_unix_seconds`, fn`extract_final_response`, fn`compose_verifier_final_response`
- 调试：`rg -n 'evidence' crates/codegen/xai-grok-shell/src`

### 22.573 `session/goal_classifier.rs`

- 规模：6596 行 · 目录 `session`
- 摘要：Goal-verification stage (harness-owned).
- 符号：fn`expand_skeptic_assignment`, fn`format_details_path`, fn`format_changes_path`, fn`validate_details_path`, fn`validate_details_path_in_root`, fn`parse_skeptic_terminal_response`, fn`capture_git_baseline`, fn`build_subagent_trace_items`
- 调试：`rg -n 'goal_classifier' crates/codegen/xai-grok-shell/src`

### 22.574 `session/goal_evaluator.rs`

- 规模：246 行 · 目录 `session`
- 摘要：—
- 符号：fn`parse_goal_evaluator_verdict`, fn`goal_evaluator_json_schema`, fn`bounded_goal_transcript`, fn`build_goal_evaluator_request`, struct`GoalEvaluatorVerdict`, enum`GoalEvaluatorDecision`, enum`GoalEvaluatorParseError`
- 调试：`rg -n 'goal_evaluator' crates/codegen/xai-grok-shell/src`

### 22.575 `session/goal_next_step.rs`

- 规模：369 行 · 目录 `session`
- 摘要：Helper that mines the "next concrete step" inlined into the goal
- 符号：fn`first_unchecked_plan_item`
- 调试：`rg -n 'goal_next_step' crates/codegen/xai-grok-shell/src`

### 22.576 `session/goal_orchestrator.rs`

- 规模：492 行 · 目录 `session`
- 摘要：Goal mode support — notification helpers and state formatters.
- 符号：fn`build_goal_updated`, fn`build_goal_cleared`, fn`format_elapsed`, struct`GoalNotifySender`
- 调试：`rg -n 'goal_orchestrator' crates/codegen/xai-grok-shell/src`

### 22.577 `session/goal_planner.rs`

- 规模：1689 行 · 目录 `session`
- 摘要：Goal planner subagent runner. Mirrors [`crate::session::goal_classifier`]
- 符号：fn`effective_role_model_id`, fn`spawn_with_fail_open_retry`, fn`parse_terminal_response`, fn`run_goal_planner`, struct`RoleSpawnOverride`, struct`RoleRenderedPrompt`, struct`ChannelSpawner`, struct`GoalPlannerInputs`
- 调试：`rg -n 'goal_planner' crates/codegen/xai-grok-shell/src`

### 22.578 `session/goal_role_tools.rs`

- 规模：654 行 · 目录 `session`
- 摘要：Shared per-role prompt tool-name rendering for the `/goal` harness.
- 符号：struct`RoleToolNames`
- 调试：`rg -n 'goal_role_tools' crates/codegen/xai-grok-shell/src`

### 22.579 `session/goal_stop_detector.rs`

- 规模：637 行 · 目录 `session`
- 摘要：Heuristic stop-detector for premature "give up" turn endings.
- 符号：fn`matched_stop_pattern`
- 调试：`rg -n 'goal_stop_detector' crates/codegen/xai-grok-shell/src`

### 22.580 `session/goal_strategist.rs`

- 规模：1296 行 · 目录 `session`
- 摘要：Stall-triggered goal strategist subagent runner.
- 符号：fn`strategist_should_fire`, fn`run_goal_strategist`, struct`ChannelSpawner`, struct`GoalStrategistInputs`, enum`GoalStrategistOutcome`
- 调试：`rg -n 'goal_strategist' crates/codegen/xai-grok-shell/src`

### 22.581 `session/goal_summarizer.rs`

- 规模：680 行 · 目录 `session`
- 摘要：Achievement-triggered goal summarizer subagent runner.
- 符号：fn`run_goal_summarizer`, struct`ChannelSpawner`, struct`GoalSummarizerInputs`, enum`GoalSummarizerOutcome`
- 调试：`rg -n 'goal_summarizer' crates/codegen/xai-grok-shell/src`

### 22.582 `session/goal_tracker.rs`

- 规模：3758 行 · 目录 `session`
- 摘要：Goal mode state machine.
- 符号：fn`generate_verifier_id`, fn`goal_scratch_root`, fn`ensure_goal_scratch_root`, fn`implementer_scratch_dir`, fn`skeptic_scratch_dir`, fn`make_base_orchestration`, struct`GoalHistoryEntry`, struct`GoalOrchestration`
- 调试：`rg -n 'goal_tracker' crates/codegen/xai-grok-shell/src`

### 22.583 `session/handle.rs`

- 规模：592 行 · 目录 `session`
- 摘要：`SessionHandle` — the `Clone + Send` proxy for interacting with a session actor.
- 符号：struct`SessionHandle`, enum`SessionLiveState`
- 调试：`rg -n 'handle' crates/codegen/xai-grok-shell/src`

### 22.584 `session/helpers/chat.rs`

- 规模：142 行 · 目录 `session/helpers`
- 摘要：Returns the largest valid UTF-8 character boundary index at or before `index`.
- 符号：fn`build_transcript`, fn`truncate_middle_words`, fn`text_completion`, fn`build_prompt_from_template`, fn`template_completion`
- 调试：`rg -n 'chat' crates/codegen/xai-grok-shell/src`

### 22.585 `session/helpers/compaction_context.rs`

- 规模：458 行 · 目录 `session/helpers`
- 摘要：Rendering helpers for [`CompactionStateContext`] that depend on
- 符号：fn`to_system_reminder_sync`, fn`to_system_reminder`, struct`McpToolNames`, struct`SubagentToolNames`
- 调试：`rg -n 'compaction_context' crates/codegen/xai-grok-shell/src`

### 22.586 `session/helpers/full_replace_compaction.rs`

- 规模：423 行 · 目录 `session/helpers`
- 摘要：grok-build's L5 wiring onto the shared full-replace engine
- 符号：struct`ShellCompactionSampler`, struct`FullReplaceTelemetry`, struct`ShellFullReplaceObserver`
- 调试：`rg -n 'full_replace_compaction' crates/codegen/xai-grok-shell/src`

### 22.587 `session/helpers/memory_context.rs`

- 规模：357 行 · 目录 `session/helpers`
- 摘要：Format memory search results as `<system-reminder>` content.
- 符号：fn`conversation_has_memory_context`, fn`format_memory_reminder`, fn`is_greeting`
- 调试：`rg -n 'memory_context' crates/codegen/xai-grok-shell/src`










