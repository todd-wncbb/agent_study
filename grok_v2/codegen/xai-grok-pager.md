# xai-grok-pager 代码导读

> `crates/codegen/xai-grok-pager` · Grok Build TUI（ratatui + ACP 客户端）
> 配套：[03 TUI 架构](../03_tui_architecture.md) · [xai-grok-shell](./xai-grok-shell.md)

---

## 1. 职责

终端 UI：**输入、scrollback 渲染、ACP 会话、权限弹窗、slash 命令**。
业务逻辑在 `xai-grok-shell`（SessionActor）；pager 是 **ACP Client + 视图层**。

```text
用户按键/鼠标
  → input → Action
  → dispatch (同步，纯函数)
  → Vec<Effect>
  → effects (spawn async: ACP RPC, 文件, 网络)
  → TaskResult → dispatch

ACP 通知 (shell → pager)
  → acp_handler → 更新 AgentSession / ScrollbackState
  → 下一帧 draw
```

---

## 2. Action / Effect / TaskResult 管道

定义在 `app/actions.rs`（~2900 行 `Action` 枚举）：

| 类型 | 生产者 | 消费者 | 约束 |
| --- | --- | --- | --- |
| `Action` | `input/` 键鼠 | `dispatch::dispatch` | 同步、无副作用 |
| `Effect` | dispatch | `effects/` + `event_loop` | 描述异步工作 |
| `TaskResult` | 完成的任务 | dispatch | 结果回灌 |

**dispatch 不变量**（`app/dispatch/mod.rs` 注释）：

- 不直接操作 terminal / 网络 / 文件
- 所有 mutation 同步、可单测
- 异步 = 返回 `Effect`，由 event loop 执行

---

## 3. app/ 子系统

| 模块 | 作用 |
| --- | --- |
| `actions` | Action / Effect / TaskResult |
| `dispatch` | router + session/turn/settings/permissions… |
| `effects` | Effect → tokio::spawn |
| `event_loop` | biased select: input, ACP, effects, tick |
| `acp_handler` | ACP 通知 → scrollback + 权限队列 |
| `agent` | AgentSession, TurnState, AgentId |
| `agent_view` | 单 agent 输入框 + pane |
| `app_view` | 根视图：welcome / agent / dashboard |
| `session_startup` | 会话创建、worktree 选择 |
| `turn_completion` | turn 结束协调 |
| `cli` | PagerArgs, 子命令 |

---

## 4. acp_handler 路由

`app/acp_handler/mod.rs` — 处理 `AcpClientMessage`：

| 子模块 | 职责 |
| --- | --- |
| `routing` | session_id → AgentId 匹配 |
| `session_notification` | `x.ai/session_notification`、replay |
| `permissions` | tool permission 队列 UI |
| `queue` | prompt 队列、running adoption |
| `mcp` | MCP 相关通知 |
| `background` | 后台任务状态 |
| `workflow_ingest` | workflow 更新 |
| `follow_ups` | 后续 prompt |

核心类型：`AcpUpdateTracker`（`acp/tracker.rs`）把 ACP update 转成 scrollback blocks。

---

## 5. scrollback 渲染管线

`scrollback/mod.rs`：

```text
ACP update → blocks/* (AgentMessage, ToolCall, Thinking, UserPrompt…)
           → entry::ScrollbackEntry
           → state::ScrollbackState (scroll, selection, turns)
           → wrappers/* (BlockRenderer, padding, accent)
           → render.rs → ratatui Buffer
```

| 目录 | 内容 |
| --- | --- |
| `blocks/` | 各类型 block 的 layout + 内容 |
| `blocks/tool/` | 工具调用 UI（bash, edit, search…）|
| `state/` | timeline, layout, selection |
| `wrappers/` | 组合渲染、CSI 过滤 |

---

## 6. views/ 与输入

| 区域 | 模块 |
| --- | --- |
| `prompt_widget` | 多行输入、图片粘贴 |
| `permission_view` | 工具权限审批 |
| `session_picker` | 欢迎页选会话 |
| `dashboard` | 用量/状态面板 |
| `status_bar / context_bar` | 底部状态 |
| `settings_modal` | 设置 UI |
| `tasks_pane / queue_pane` | 后台任务、prompt 队列 |

`input/` — 键盘（vim mode）、鼠标、Kitty 协议、`keyboard_normalizer`。

---

## 7. slash 命令

`slash/registry.rs` 注册；实现在 `slash/commands/*.rs`：

`/help` `/model` `/theme` `/rewind` `/tasks` `/mcp` `/plan` `/export` …

命令解析 → `Action` → dispatch 同路径。

---

## 8. 与 xai-grok-pager-render

`lib.rs` re-export `xai_grok_pager_render`：
`theme` `glyphs` `render` `syntax` `terminal` `clipboard` …

pager = 业务 + 布局；render crate = 纯绘制原语。

---

## 9. minimal 模式接缝

- `minimal_hook.rs` — full pager → minimal dispatch（fn 指针 IoC）
- `minimal_api.rs` — minimal → pager 只读 facade
- 实际 minimal UI 在 **`xai-grok-pager-minimal`** crate

---

## 10. 顶层 lib.rs 模块一览

| 模块 | 说明 |
| --- | --- |
| `acp` | ACP (Agent Communication Protocol) connection management. |
| `actions` | Action registry — single source of truth for all actions, key bindings, and |
| `app` | Application entry point and terminal management. |
| `client_identity` | `User-Agent` for pager-owned direct-to-`api.x.ai` clients (voice STT). |
| `completions_cmd` | `grok completions <shell>` — generate shell completion scripts. |
| `diagnostics` | Route-aware terminal diagnostics engine. |
| `diff` | Build diff hunks from full old/new text strings. |
| `docs` | In-app how-to documentation data (embedded markdown). |
| `doctor_cmd` | Print the diagnostic report as JSON. |
| `export_cmd` | Session ID to export |
| `git_info` | Git branch/worktree info — cached queries shared across views. |
| `headless` | Headless single-turn mode (`grok -p "prompt"`). |
| `hyperlink_route` | Per-environment hyperlink route policy. |
| `inline_media_ffmpeg` | FFmpeg PATH probes and the "install ffmpeg" banner for inline video posters |
| `input` | Input handling (keys, mouse). |
| `input_log` | Input flight recorder — rolling buffer of recent key events. |
| `mcp_cmd` | `grok mcp` — manage MCP server configurations from the command line. |
| `memory_cmd` | Clear memory files (workspace by default) |
| `memory_release` | Allocator memory-release seam. |
| `memory_trace` | Process memory tracing: durable JSONL evidence for memory investigations. |
| `minimal_api` | — |
| `minimal_hook` | — |
| `models` | `grok models` subcommand. |
| `notifications` | Ghostty resets the OSC 9;4 progress indicator after ~15 s of silence. |
| `obf` | Compile-time obfuscated string constants for binary hardening. |
| `plugin_cmd` | `grok plugin` CLI subcommand — manage plugins and marketplace sources. |
| `project_picker` | Project picker: select a project directory on first prompt from a non-proje |
| `pty_wrap` | Local PTY wrapper: the engine behind `grok wrap` (see [`crate::wrap_cmd`]). |
| `scrollback` | Scrollback — conversation display with blocks, scroll, selection, turns. |
| `search` | Reusable text-search primitives. |
| `sessions_cmd` | List recent sessions (same as search with no query) |
| `settings` | Settings registry and modal — the canonical place for user preferences. |
| `share_cmd` | Session ID to share |
| `slash` | Slash command system -- prompt-centric inline completion and execution. |
| `startup` | Generic startup warnings displayed on the welcome screen. |
| `tips` | Ephemeral tip primitive: a single-slot, TTL'd hint line rendered in the |
| `tutorial_docs` | Onboarding tutorial content (embedded markdown). |
| `wrap_clipboard_image` | Host clipboard image paste mediated by `grok wrap`. |
| `wrap_cmd` | `grok wrap` — run any command in a local PTY that forwards its clipboard. |
| `tool_usage` | Tool usage statistics aggregation for the pager. |
| `trace_cmd` | Session ID to export/upload |
| `tracing` | Tracing capture and display for the pager's tracing pane. |
| `unified_log` | Unified log forwarding for the pager. |
| `views` | Screen rendering — each screen type has its own rendering module. |
| `voice` | Voice input: STT pipeline integration and prompt-box dictation. |
| `worktree_cmd` | Local response types matching the ACP response shapes. |
| `test_util` | Shared test utilities for the pager crate. |

---

## 11. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `client_identity.rs` | 39 | `User-Agent` for pager-owned direct-to-`api.x.ai` clien | fn`client_user_agent` |
| `completions_cmd.rs` | 126 | `grok completions <shell>` — generate shell completion  | fn`run` |
| `config_toml_edit.rs` | 181 | Load `config.toml` as a [`toml_edit::DocumentMut`] for  | fn`read_config_document_for_edit`, fn`set_hint` |
| `diff.rs` | 1371 | Build diff hunks from full old/new text strings. | fn`build_diff_hunks`, fn`diff_hunks_from_strings`, fn`stitch_overlappi |
| `docs.rs` | 357 | In-app how-to documentation data (embedded markdown). | fn`find_doc`, fn`all_titles`, fn`get_howto_doc`, fn`list_howto_titles` |
| `export_cmd.rs` | 86 | Session ID to export | fn`run`, struct`ExportArgs` |
| `git_info.rs` | 536 | Git branch/worktree info — cached queries shared across | fn`update_from_notification`, fn`populate_from_cwd_async`, fn`compute_ |
| `headless.rs` | 2168 | Headless single-turn mode (`grok -p "prompt"`). | fn`parse_json_schema`, fn`parse_permission_rules_strict`, fn`parse_per |
| `hyperlink_route.rs` | 245 | Per-environment hyperlink route policy. | fn`hyperlink_route`, fn`resolve_hyperlink_route`, struct`HyperlinkRout |
| `inline_media_ffmpeg.rs` | 206 | FFmpeg PATH probes and the "install ffmpeg" banner for  | fn`ffmpeg_available`, fn`set_ffmpeg_available_for_test`, fn`ffmpeg_ins |
| `input_log.rs` | 277 | Input flight recorder — rolling buffer of recent key ev | fn`sanitize_key_code`, fn`format_key_code_raw`, struct`LastInputDelta` |
| `lib.rs` | 78 | xai-grok-pager — Grok Build TUI. | — |
| `mcp_cmd.rs` | 1117 | `grok mcp` — manage MCP server configurations from the  | fn`run`, struct`McpArgs`, struct`AddArgs`, enum`McpTransport`, enum`Mc |
| `memory_cmd.rs` | 132 | Clear memory files (workspace by default) | fn`run`, struct`MemoryArgs`, enum`MemoryCommand` |
| `memory_release.rs` | 222 | Allocator memory-release seam. | fn`install_release_hook`, fn`release_retained_memory`, fn`release_reta |
| `memory_trace.rs` | 746 | Process memory tracing: durable JSONL evidence for memo | fn`install_allocator_stats_provider`, fn`install_allocator_dump_provid |
| `models.rs` | 40 | `grok models` subcommand. | fn`list_available_models` |
| `obf.rs` | 19 | Compile-time obfuscated string constants for binary har | — |
| `plugin_cmd.rs` | 1326 | `grok plugin` CLI subcommand — manage plugins and marke | fn`run`, struct`PluginArgs`, struct`MarketplaceArgs`, enum`PluginComma |
| `pty_wrap.rs` | 426 | Local PTY wrapper: the engine behind `grok wrap` (see [ | fn`run_wrapped_command` |
| `sessions_cmd.rs` | 257 | List recent sessions (same as search with no query) | fn`run`, struct`SessionsArgs` |
| `share_cmd.rs` | 49 | Session ID to share | fn`run`, struct`ShareArgs` |
| `startup.rs` | 144 | Generic startup warnings displayed on the welcome scree | fn`banner_warning`, struct`ActionableStartupWarning`, struct`StartupWa |
| `test_util.rs` | 163 | Shared test utilities for the pager crate. | fn`make_agent_view`, struct`EnvVarGuard`, struct`GrokHomeFixture` |
| `tool_usage.rs` | 603 | Tool usage statistics aggregation for the pager. | struct`LineageEntry`, struct`CategoryStats`, struct`ToolUsageStats`, e |
| `trace_cmd.rs` | 663 | Session ID to export/upload | fn`run`, fn`build_session_tar`, fn`find_session_dir`, fn`trace_exports |
| `tracing.rs` | 915 | Tracing capture and display for the pager's tracing pan | fn`dropped_log_lines`, fn`init_tracing`, struct`TracingEntry`, struct` |
| `tutorial_docs.rs` | 149 | Onboarding tutorial content (embedded markdown). | struct`TutorialTopic` |
| `unified_log.rs` | 141 | Unified log forwarding for the pager. | fn`init`, fn`flush`, fn`flush_blocking`, fn`info`, fn`warn` |
| `wrap_clipboard_image.rs` | 333 | Host clipboard image paste mediated by `grok wrap`. | fn`request_osc_bytes`, fn`maybe_request_wrap_host_image`, fn`try_decod |
| `wrap_cmd.rs` | 234 | `grok wrap` — run any command in a local PTY that forwa | fn`run` |
| `wrap_filter.rs` | 832 | Streaming output filter for `grok wrap`: OSC 52 clipboa | fn`host_clipboard_image_frame`, struct`Osc52Filter` |
| `wrap_restore.rs` | 511 | Terminal-mode tracking and restore emission for `grok w | fn`restore_bytes`, struct`ModeTracker`, struct`ModeSnapshot` |

### `acp/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `leader_bridge.rs` | 479 | Bridge a leader IPC connection into an `AcpClientChanne | fn`bridge_leader_connection`, fn`bridge_channels`, struct`LeaderBridge |
| `meta.rs` | 233 | Strongly-typed notification metadata. | struct`NotificationMeta`, struct`ReplayMetaStamp` |
| `mod.rs` | 1081 | ACP (Agent Communication Protocol) connection managemen | fn`wait_for_exit_not_supported`, fn`connect`, fn`connect_via_leader`,  |
| `model_state.rs` | 646 | Model state — tracks available models and current selec | struct`ModelState`, enum`EffortTokenError` |
| `spawn.rs` | 169 | Agent spawning — creates the agent process and ACP chan | fn`spawn_grok_shell`, struct`SpawnedAgent` |
| `tracker.rs` | 6687 | AcpUpdateTracker — converts ACP SessionUpdate events in | fn`clamp_activity_subject`, fn`format_waiting_for_subject`, struct`Pen |

### `actions/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `defaults.rs` | 1250 | Default action definitions for the MVP. | fn`ctrl_dot_unreliable` |
| `mod.rs` | 915 | Action registry — single source of truth for all action | fn`default_actions`, struct`ActionDef`, struct`ActionRegistry`, enum`A |

### `app/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `actions.rs` | 2927 | Application actions, effects, and task results. | fn`reduce_clipboard_paste_completion`, struct`ClipboardPasteContext`,  |
| `agent.rs` | 1760 | Agent business types. | struct`AgentId`, struct`QueuedPrompt`, struct`BgTaskState`, struct`Sch |
| `app_view.rs` | 11637 | Root view component. | fn`esc_double_press_ttl`, fn`is_api_key_label`, struct`NewWorktreeDial |
| `bundle.rs` | 174 | Bundle status state and response types. | struct`BundleState`, struct`BundleStatusResult`, struct`PersonaDetail` |
| `cli.rs` | 1410 | CLI argument parsing for the pager. | struct`WrapArgs`, struct`LeaderTargetArgs`, struct`LeaderMgmtArgs`, st |
| `csi_filter.rs` | 741 | CSI fragment filter for the input event channel. Siblin | — |
| `display_refresh_startup.rs` | 209 | Display-refresh probe + motion cadence at TUI startup. | fn`start`, struct`MotionClocks` |
| `edit_highlight_worker.rs` | 660 | Off-draw-thread full-file syntax highlight for edit dif | fn`spawn_worker`, fn`resolve_edit_abs_path`, struct`EditHlJob`, struct |
| `event_loop.rs` | 5083 | Main event loop. | fn`run`, fn`load_initial_ui_config`, struct`TerminalState`, struct`Run |
| `external_editor.rs` | 589 | External-editor request and prompt-draft lifecycle. | fn`prepare`, fn`finish_prepare_error`, fn`finish`, fn`apply_prompt_tex |
| `foreign_sessions.rs` | 887 | Opaque per-application coordinator for foreign session  | fn`is_foreign_picker_source`, fn`badge_for_picker_source`, fn`foreign_ |
| `inline_edit.rs` | 503 | Inline edit-and-resubmit of a previous user prompt. | struct`InlineEditState` |
| `mermaid_worker.rs` | 2341 | Off-draw-thread Mermaid render worker, per-session disk | fn`spawn_worker`, fn`render_via_subprocess`, fn`maybe_run_render_subpr |
| `mod.rs` | 2030 | Application entry point and terminal management. | fn`push_gboom_keyboard_flags`, fn`pop_gboom_keyboard_flags`, fn`minima |
| `modals.rs` | 3126 | Modal dialog handling for [`AgentView`]: the `handle_mo | — |
| `mouse.rs` | 1684 | Mouse input handling for [`AgentView`]: the `handle_mou | — |
| `queue_edit.rs` | 1756 | Queued-prompt editing (`PromptMode::EditingQueued`) sta | enum`PromptMode` |
| `roster.rs` | 216 | Mirror types for the leader "session roster" wire forma | fn`parse_roster_list_response`, struct`RosterOrigin`, struct`RosterEnt |
| `screen_mode_relaunch.rs` | 914 | Rebuild process argv and re-exec the pager into a diffe | fn`build_screen_mode_relaunch_args`, fn`screen_mode_env_value`, fn`scr |
| `session_startup.rs` | 1333 | Canonical session-selection CLI intent. | fn`fork_session_params`, fn`parent_session_is_worktree`, fn`fork_respo |
| `session_title_resolve.rs` | 169 | Resume-by-title selection shared by startup paths. | fn`is_uuid_shaped`, fn`title_miss_hint`, fn`select_by_title`, fn`presa |
| `signal_handler.rs` | 289 | TUI-side signal handlers that restore the terminal befo | fn`set_current_session_id`, fn`set_quit_notify`, fn`install`, fn`mark_ |
| `status_blocks.rs` | 410 | Read-only system-block text for `/queue`, `/tasks`, and | fn`queue_block_text`, fn`tasks_block_text`, fn`session_usage_block_tex |
| `subagent.rs` | 1159 | Subagent business types. | fn`set_replay_grok_home_for_tests`, fn`enrich_from_meta`, fn`replay_in |
| `subscription.rs` | 493 | Free→paid subscription detection and gate imposition/li | — |
| `turn_completion.rs` | 329 | Finalizing a turn from a terminal turn signal. | — |
| `xt_filter.rs` | 606 | XTVERSION DCS reply filter for the input event channel  | — |

### `app/acp_handler/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `background.rs` | 720 | Route a `ToolCallUpdate` stdout chunk to the central bg | — |
| `follow_ups.rs` | 109 | Max follow-up chips kept from a single (server-controll | — |
| `interactions.rs` | 256 | Handle `x.ai/ask_user_question` ext-method. | fn`handle_ask_user_question` |
| `mcp.rs` | 331 | Cached `mcp.push_server_status` flag resolution. | — |
| `mod.rs` | 782 | ACP message handling. | fn`handle`, fn`refresh_workflow_run_capabilities` |
| `permissions.rs` | 433 | Route a permission request to the agent that owns its ` | — |
| `prompt_origin.rs` | 95 | Returns true if the prompt_id was generated by the shel | fn`is_server_initiated_prompt`, fn`is_scheduler_fired_prompt`, fn`is_w |
| `queue.rs` | 429 | A server-authoritative running prompt that drained into | struct`PendingRunningAdoption` |
| `routing.rs` | 189 | Result of looking up which view a notification's `sessi | — |
| `session_notification.rs` | 1368 | Stash a live stop/stop_failure batch under `stash_pid`  | fn`apply_session_event_for_test` |
| `settings.rs` | 686 | Handle `x.ai/models/update` — model list changed (etag- | — |
| `subagent_activity.rs` | 103 | Update the activity label on a subagent's collapsed scr | fn`finalize_killed_subagent` |
| `workflow_ingest.rs` | 196 | — | — |

### `app/agent_view/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `cta.rs` | 1048 | Plugin CTA banner and follow-up chips: connect-plugin s | — |
| `input.rs` | 2410 | Top-level input routing for [`AgentView`]: `handle_inpu | enum`ExternalPromptEditorAccess` |
| `interactions.rs` | 1849 | Blocking interaction surfaces: permission prompts, the  | — |
| `jump.rs` | 180 | `/jump` picker: transcript preview syncing and key/mous | — |
| `links.rs` | 2701 | Link affordances in scrollback: highlight cycling, hove | — |
| `media.rs` | 800 | Inline media: image/video viewer keys, playback state,  | — |
| `mod.rs` | 3637 | Per-agent view component. | fn`translate_local_submit_for_test`, fn`render_dropdown_chrome`, fn`dr |
| `modals.rs` | 3333 | Modal input handlers: agents/persona modals and the ext | — |
| `notices.rs` | 404 | Transient user feedback: toasts, ephemeral tips, mode-s | — |
| `panes.rs` | 877 | Secondary pane input: scrollback keys and search, todo/ | — |
| `paste.rs` | 2638 | Paste routing: bracketed paste, clipboard attachment pr | — |
| `plan.rs` | 874 | Plan surfaces: plan chip/preview, plan approval + feedb | — |
| `prompt.rs` | 1828 | Prompt-pane key handling: `handle_prompt_key`, the Esc  | — |
| `queue.rs` | 1882 | Prompt-queue pane: visibility toggles, key handling, ro | — |
| `render.rs` | 4510 | Frame rendering for [`AgentView`]: the `draw` entry poi | struct`AppRenderParams` |
| `rewind.rs` | 331 | Rewind picker: anchor syncing, dim ranges, and key/mous | — |
| `selection.rs` | 2963 | Scrollback text/block selection: click counting, word/l | — |
| `session.rs` | 1614 | Session lifecycle: bind/reload/replay bookkeeping, turn | — |
| `shell_completion.rs` | 923 | Bash-mode shell completion: the always-on Tab surface ( | — |
| `viewer.rs` | 983 | Line and block viewer popups plus the /btw panel: open/ | — |
| `workflows_overlay.rs` | 694 | — | — |

### `app/dispatch/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `auth.rs` | 550 | Login, logout, account switching, and auth-code submiss | — |
| `billing.rs` | 553 | Subscription tier checks, credit-limit upsells, and aut | fn`is_credit_limit_error` |
| `cta.rs` | 530 | Plugin install call-to-action phase tracking helpers an | — |
| `ctx.rs` | 286 | Active-agent lookup and view-context helpers shared acr | fn`switch_to_agent`, enum`SwitchCause` |
| `dashboard.rs` | 2376 | Dashboard dispatchers: attach, overlays, rows, renames, | — |
| `dashboard_telemetry.rs` | 34 | — | — |
| `external_editor.rs` | 43 | Pure dispatch preparation for minimal-mode external pro | — |
| `import_claude.rs` | 124 | Claude session import dispatchers. | — |
| `interject.rs` | 368 | Mid-turn interjection dispatch: optimistic local echo,  | — |
| `jump.rs` | 83 | `/jump` picker dispatchers: pure client-side turn navig | — |
| `mod.rs` | 66 | Synchronous state dispatch: [`Action`](crate::app::acti | — |
| `modes.rs` | 969 | Plan, yolo, auto, and permission mode transitions and t | fn`effective_auto`, fn`downgrade_displayed_auto_if_gated` |
| `notes.rs` | 494 | Feedback, remember-note, btw, and recap dispatchers. | fn`recap_unavailable_toast`, fn`scrollback_has_user_messages` |
| `permissions.rs` | 276 | Permission request selection, follow-up, cancellation,  | fn`resolve_permission_queue_transition` |
| `prompt.rs` | 1684 | Prompt and bash-command submission dispatchers and relo | fn`dispatch_initial_prompt` |
| `queue.rs` | 3091 | Prompt-queue dispatch: the server-authoritative immedia | fn`shim_renders_own_user_block`, fn`arm_send_now_and_paint`, fn`apply_ |
| `rewind.rs` | 867 | Conversation rewind dispatchers and prompt-entry lookup | — |
| `router.rs` | 1510 | Top-level action router: maps actions and action result | fn`dispatch` |
| `status.rs` | 628 | Session status, sharing, privacy, usage, and info dispa | fn`commit_minimal_update_notice` |
| `task_result.rs` | 1223 | Async task-result application: routes task results into | fn`current_doctor_target`, fn`deliver_doctor_message` |
| `transcript.rs` | 824 | Transcript export, block copying, viewer/modal, and inp | fn`dispatch_open_transcript_pager` |
| `turn.rs` | 597 | Turn cancellation, task and subagent kills, and overdue | fn`reconcile_overdue_turn_ends` |
| `voice.rs` | 169 | Voice mode enable, toggle, and stop dispatchers. | — |

### `app/dispatch/session/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `foreign.rs` | 434 | — | — |
| `fork.rs` | 627 | Fork and project-selection dispatchers and fork placeho | — |
| `lifecycle.rs` | 1209 | New, exit, cloud, and worktree session dispatchers plus | fn`take_deferred_model_switch`, fn`apply_deferred_model_switch`, fn`ap |
| `load.rs` | 1276 | Session loading, session pickers, and deep-search dispa | — |
| `mod.rs` | 7 | Session lifecycle, loading, picking, modal, and fork di | — |
| `modal.rs` | 95 | Session rename / close helpers (shared with the dashboa | — |

### `app/dispatch/settings/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 4 | Settings setters and settings UI dispatchers. | — |
| `setters.rs` | 2094 | Individual setting setters with persistence effects and | — |
| `ui.rs` | 1194 | Settings UI: command palette, settings modal, toggles,  | fn`refresh_open_settings_modals`, fn`build_pager_snapshot` |

### `app/effects/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `helpers.rs` | 1490 | Typed progress message for session restore. | fn`parse_session_load_running_prompt_id`, fn`sanitize_user_error`, fn` |
| `mod.rs` | 4631 | Async effect execution. | fn`execute` |
| `tests.rs` | 2354 | The invalid-params server detail survives `attach_promp | — |

### `app/leader_cluster/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 645 | In-process multi-client leader cluster: a REAL leader I | — |
| `scenarios.rs` | 390 | Scenario tests driving [`PagerLeaderCluster`] (the harn | — |

### `app/turn_completion/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `tests.rs` | 783 | Unit tests for the turn-finalize rails in [`super`] (`t | — |

### `bin/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mermaid_playground.rs` | 109 | Interactive playground for mermaid diagrams in the real | — |
| `mouse_events_playground.rs` | 173 | — | — |
| `question_view_playground.rs` | 409 | Hardcoded example question sets for UI playground scena | — |
| `scrollback_search_playground.rs` | 268 | Interactive playground for the scrollback search render | — |
| `scrollback_selection_playground.rs` | 457 | Maximum time (ms) between consecutive clicks to count a | — |
| `todo_pane_playground.rs` | 231 | Interactive playground for the Ctrl+T todo pane (hide-d | — |

### `diagnostics/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `doctor_format.rs` | 208 | In-TUI `/doctor` report formatting. | fn`format_doctor` |
| `fix.rs` | 1526 | Exact planning and application for diagnostic fixes. | fn`resolve_fix_id`, fn`human_fix_command`, fn`automatic_fix_choices`,  |
| `mod.rs` | 3034 | Route-aware terminal diagnostics engine. | fn`apply_voice_probe`, fn`summarize_warnings`, fn`collect_startup_warn |
| `model.rs` | 224 | Shared terminal diagnostic report types. | fn`probe_requires_live_tui`, struct`DiagnosticId`, struct`DiagnosticRe |
| `view.rs` | 655 | Interpretation of terminal probe snapshots. | fn`view`, fn`finding_from_warning`, struct`DiagnosticSnapshot` |

### `diagnostics/probes/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 514 | Shared terminal observations for diagnostics consumers. | fn`osc52_sink_active`, fn`collect_startup_tui`, fn`collect_doctor_tui` |
| `tmux.rs` | 27 | — | struct`LiveTmuxProbe`, trait`TmuxOptionQuery` |

### `doctor_cmd/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `human.rs` | 257 | — | — |
| `json.rs` | 466 | — | — |
| `mod.rs` | 208 | Print the diagnostic report as JSON. | fn`run`, fn`run_with_writer`, fn`collect_report`, struct`DoctorArgs`,  |
| `tests.rs` | 1045 | — | — |

### `input/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `key.rs` | 556 | Key shortcut types and the `key!()` macro. | fn`is_paste_key`, fn`is_inline_paste_key`, fn`is_undo_key`, fn`is_altg |
| `keyboard_normalizer.rs` | 288 | Reconciles delivered key events with physical input sta | struct`ModifierState`, struct`OsModifierProbe`, struct`KeyboardNormali |
| `line_editor.rs` | 304 | The key was recognized and consumed, but text and curso | fn`sanitize_single_line`, struct`LineEditor`, enum`LineEditOutcome` |
| `macos_modifiers.rs` | 50 | Native macOS modifier key detection via CoreGraphics. | fn`snapshot` |
| `mod.rs` | 13 | Input handling (keys, mouse). | — |
| `mouse.rs` | 1448 | Scroll normalization for mouse wheel/trackpad input. | fn`speed_to_multiplier`, struct`ScrollConfig`, struct`ScrollConfigOver |
| `scroll_log.rs` | 287 | Scroll flight recorder — `GROK_SCROLL_LOG` JSONL log of | fn`default_log_path`, struct`ScrollLogConfigEcho`, struct`ScrollLogEve |
| `terminal_support.rs` | 98 | OS-level rescue for the modified-Enter chord. | fn`is_apple_terminal_newline_modifier_held`, fn`is_mod_enter` |

### `input/mouse/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `tests.rs` | 1878 | Unit tests for scroll normalization in [`super`] (`mous | — |

### `minimal/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `api.rs` | 881 | Read/render surface consumed by the `xai-grok-pager-min | fn`minimal_btw_size_is_paintable`, fn`minimal_btw_geometry_is_paintabl |
| `hook.rs` | 48 | Inversion-of-control seam for the optional minimal (scr | fn`install`, fn`hooks`, struct`MinimalHooks` |

### `notifications/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `config.rs` | 404 | Show an automatic "where was I" session recap when you  | struct`NotificationConfig`, struct`TitleConfig`, struct`NotificationHo |
| `focus.rs` | 285 | Minimum gap between automatic recap *attempts* while st | struct`FocusTracker` |
| `hooks.rs` | 288 | — | fn`run_hook` |
| `mod.rs` | 758 | Ghostty resets the OSC 9;4 progress indicator after ~15 | fn`load_notification_config`, struct`NotificationEvent`, struct`Notifi |
| `progress.rs` | 208 | Build the progress bar escape sequence as an owned `Str | fn`supports_progress_bar`, fn`emit_progress`, fn`build_progress_escape |
| `protocol.rs` | 383 | iTerm2/WezTerm/Warp: `\x1b]9;{message}\x07` | fn`select_protocol`, fn`emit_notification`, enum`NotificationProtocol` |
| `sleep.rs` | 275 | Platform-specific sleep prevention. | struct`SleepInhibitor` |
| `title.rs` | 887 | Hold each spinner frame for this many ticks before adva | struct`TitleState`, struct`TitleManager` |
| `tmux.rs` | 100 | Wrap an escape sequence in tmux DCS passthrough. | fn`tmux_passthrough`, fn`passthrough_available` |

### `project_picker/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 131 | Project picker: select a project directory on first pro | fn`build_project_question`, struct`ProjectQuestion` |
| `sources.rs` | 49 | Data sources for the project picker: recent directories | fn`collect_recent_dirs`, fn`display_path` |

### `scrollback/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `block.rs` | 1700 | BlockContent trait, RenderBlock enum, and shared bullet | fn`prepend_bullet`, fn`join_searchable`, struct`AnchoredMedia`, struct |
| `entry.rs` | 833 | ScrollbackEntry - wraps a block with display state. | struct`EntryId`, struct`ScrollbackEntry`, struct`EffectiveOutput`, enu |
| `export.rs` | 112 | Pure functions for exporting a conversation transcript  | fn`render_blocks_to_markdown` |
| `layout.rs` | 238 | Horizontal layout for scrollback entries. | struct`HorizontalLayout` |
| `link_map.rs` | 617 | VisibleLinkMap — per-frame map of clickable link region | struct`VisibleLink`, struct`VisibleLinkMap` |
| `mod.rs` | 45 | Scrollback — conversation display with blocks, scroll,  | — |
| `render.rs` | 4512 | Scroll-aware rendering for scrollback entries. | fn`media_open_button_label`, fn`media_open_button_col`, fn`render_scro |
| `scrollback_pane.rs` | 1276 | ScrollbackPane widget - the main conversation display. | struct`ScrollbackPane`, struct`RenderOutputWithSelectionBoundaries` |
| `search.rs` | 1185 | Full-text search over scrollback text, scanned on a bac | struct`ScrollbackMatch`, struct`ScrollbackSearchIndex`, struct`Scrollb |
| `selection.rs` | 438 | Selection box rendering for v3 pager. | struct`SelectionBox`, struct`RenderOutput`, struct`ScrollInfo` |
| `sticky.rs` | 1429 | Sticky header computation for AllTurns view. | fn`compute_sticky_layout`, struct`PromptDescriptor`, struct`RenderedPr |
| `table_geometry.rs` | 619 | Box-drawing table grid detection so selection inside re | struct`CellRef`, struct`TableGeometry` |
| `text_selection.rs` | 3106 | Direction for drag auto-scroll. | fn`compute_autoscroll`, fn`drag_threshold_exceeded`, fn`reconstruct_se |
| `types.rs` | 743 | Core types for pager v3. | fn`line_plain_text`, fn`line_plain_text_into`, fn`derive_selection_tex |

### `scrollback/blocks/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `agent.rs` | 491 | AgentMessageBlock - displays agent responses with markd | struct`AgentMessageBlock` |
| `bg_task.rs` | 577 | BgTaskBlock — scrollback entries for background task li | struct`BgTaskBlock`, enum`BgTaskKind` |
| `btw.rs` | 102 | BtwBlock — scrollback entry for /btw side-question resp | struct`BtwBlock` |
| `context_info.rs` | 1384 | ContextInfoBlock — typed `/context` display rendered in | struct`ContextInfoBlock` |
| `credit_limit.rs` | 253 | CreditLimitBlock — scrollback card shown when a max-tie | struct`CreditLimitBlock`, enum`CreditLimitCardAction` |
| `markdown_content.rs` | 670 | Shared markdown content with cached word-wrapping. | struct`MarkdownContent`, struct`WrappedLines` |
| `mermaid_content.rs` | 930 | Mermaid diagram detection and the on-screen affordance  | fn`hash_source`, fn`mermaid_blocks`, fn`mermaid_block_ranges`, fn`them |
| `mod.rs` | 41 | Block implementations for v3 pager. | — |
| `quote_bar.rs` | 471 | Rendered blockquote-bar detection for selection/copy me | struct`QuoteBarStrip` |
| `session_event.rs` | 1387 | SessionEventBlock — typed session-level events displaye | struct`SessionEventBlock`, enum`SessionEvent` |
| `subagent.rs` | 322 | SubagentBlock — scrollback entries for subagent lifecyc | struct`SubagentBlock`, enum`SubagentBlockKind` |
| `system.rs` | 94 | SystemMessageBlock - displays system messages. | struct`SystemMessageBlock` |
| `thinking.rs` | 556 | ThinkingBlock - displays agent thinking/reasoning conte | struct`ThinkingBlock` |
| `user.rs` | 1165 | UserPromptBlock - displays user input. | struct`UserPromptBlock` |
| `workflow.rs` | 309 | — | struct`WorkflowBlockPhase`, struct`WorkflowBlock`, enum`WorkflowBlockS |

### `scrollback/blocks/tool/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `edit.rs` | 2926 | EditToolCallBlock - displays file edit diffs with synta | fn`render_diff_hunk_highlighted`, fn`render_diff_hunks_highlighted`, f |
| `execute.rs` | 1067 | ExecuteToolCallBlock - runs shell commands with streami | struct`ExecuteToolCallBlock` |
| `hook.rs` | 345 | Hook data types and rendering helpers for tool call blo | fn`render_hooks_inline_suffix`, fn`render_stop_hooks_summary`, fn`rend |
| `lifecycle.rs` | 63 | LifecycleEventBlock — standalone block for lifecycle ho | struct`LifecycleEventBlock` |
| `list_dir.rs` | 286 | ListDirToolCallBlock - lists directory contents. | struct`ListDirToolCallBlock` |
| `memory_search.rs` | 462 | MemorySearchToolCallBlock — structured memory search re | fn`parse_memory_results`, struct`MemoryResult`, struct`MemorySearchToo |
| `mod.rs` | 741 | Tool call blocks - sum type for different tool types. | struct`LineRange`, enum`VerbGroupKind`, enum`ToolCallBlock` |
| `other.rs` | 543 | OtherToolCallBlock - unknown/generic tool types. | struct`OtherToolCallBlock` |
| `read.rs` | 765 | ReadToolCallBlock - reads a file with syntax highlighti | struct`ReadToolCallBlock`, enum`ReadMediaKind` |
| `search.rs` | 562 | SearchToolCallBlock - search/grep for pattern. | struct`SearchLineMatch`, struct`SearchFileMatch`, struct`SearchInputMe |
| `search_tool.rs` | 357 | SearchToolCallBlock — integration tool discovery result | fn`discovered_tool_action`, struct`DiscoveredTool`, struct`SearchToolC |
| `use_tool.rs` | 345 | UseToolCallBlock — MCP integration tool dispatch. | struct`UseToolCallBlock` |
| `web_fetch.rs` | 406 | WebFetchToolCallBlock — URL fetch with content preview. | struct`WebFetchToolCallBlock` |
| `web_search.rs` | 438 | WebSearchToolCallBlock — web search with citations prev | struct`WebSearchToolCallBlock` |

### `scrollback/state/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `groups.rs` | 608 | Derived group model for the scrollback's view-time fold | fn`span_containing`, struct`GroupSpan`, enum`GroupKind` |
| `layout.rs` | 3543 | Layout cache and lazy viewport measurement for [`Scroll | fn`compute_paint_window`, struct`ScrollAnchor` |
| `mod.rs` | 3460 | ScrollbackState - unified state for v3 scrollback pane. | struct`ScrollbackState` |
| `nav.rs` | 2426 | Find the response anchor for a turn: the first AgentMes | — |
| `selection.rs` | 2886 | Selection and folding for [`ScrollbackState`]: selected | — |
| `timeline.rs` | 437 | Conversation timeline: one entry per turn, for jump nav | struct`TimelineEntry` |
| `types.rs` | 146 | Status of a turn in the conversation. | struct`Turn`, struct`ViewportSnapshot`, struct`EntryLayoutInfo`, enum` |
| `verb_group.rs` | 769 | Verb-group aggregation: the "Read 10 files, Ran 2 subag | fn`run_step`, fn`verb_group_kind_changed`, fn`scan_run_forward`, fn`ve |

### `scrollback/wrappers/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `accented.rs` | 142 | Accented wrapper - adds an accent line on the left. | struct`Accented` |
| `block_renderer.rs` | 233 | BlockRenderer - bridges BlockContent to Renderable. | struct`BlockRenderer` |
| `entry_renderer.rs` | 1921 | EntryRenderer - renders a ScrollbackEntry using compose | fn`group_header_chrome_prefix`, fn`group_header_chrome_prefix_width`,  |
| `mod.rs` | 80 | Wrapper types for composable rendering. | — |
| `padded.rs` | 172 | Padded wrapper - adds horizontal padding around content | struct`Padded` |

### `search/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `matcher.rs` | 125 | [`TextMatcher`] — a compiled substring/regex query with | struct`TextMatcher`, enum`QueryKind` |
| `mod.rs` | 59 | Reusable text-search primitives. | fn`next_index_after`, fn`prev_index_before` |

### `settings/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `defs.rs` | 1597 | Default settings catalog — declares every user-tunable  | fn`default_settings` |
| `mod.rs` | 35 | Settings registry and modal — the canonical place for u | — |
| `registry.rs` | 1629 | Settings registry — pure-metadata data model. | fn`dynamic_enum_choices`, fn`canonical_voice_capture_mode`, fn`canonic |

### `slash/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `acp_command.rs` | 544 | Wrapper that turns an ACP `AvailableCommand` into a `Sl | struct`AcpSlashCommand` |
| `command.rs` | 331 | Slash command trait and execution types. | struct`ScheduledTaskPreview`, struct`ArgItem`, struct`AppCtx`, struct` |
| `matcher.rs` | 187 | Nucleo-based fuzzy matcher for slash command and argume | struct`FuzzyMatcher` |
| `mod.rs` | 2958 | Slash command system -- prompt-centric inline completio | fn`command_offered`, fn`parse_invocation`, fn`is_command_complete`, fn |
| `mru.rs` | 395 | Slash command MRU / recency (`$GROK_HOME/slash-mru.json | fn`persist_async`, struct`SlashMru`, struct`MruSnapshot` |
| `registry.rs` | 1263 | Command registry -- maps command names/aliases to `Slas | struct`CommandTrigger`, struct`CommandRegistry`, enum`CommandSource` |

### `slash/commands/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `always_approve.rs` | 93 | `/always-approve` -- toggle auto-approve (YOLO / `permi | struct`AlwaysApproveCommand` |
| `announcements.rs` | 167 | `/announcements` -- show or hide the announcement banne | struct`AnnouncementsCommand` |
| `auto.rs` | 115 | `/auto` -- toggle auto permission mode (LLM classifier) | struct`AutoCommand` |
| `btw.rs` | 54 | `/btw` -- ask a side question without interrupting the  | struct`BtwCommand` |
| `cd.rs` | 123 | `/cd [path]` — change the working directory new dashboa | struct`CdCommand` |
| `compact.rs` | 51 | `/compact` -- compact conversation history. | struct`CompactCommand` |
| `compact_mode.rs` | 31 | `/compact-mode` -- toggle compact display mode. | struct`CompactModeCommand` |
| `config_agents.rs` | 29 | `/config-agents` -- open the agents modal. | struct`ConfigAgentsCommand` |
| `context.rs` | 33 | `/context` -- show detailed context usage (instant, not | struct`ContextCommand` |
| `copy.rs` | 200 | `/copy` -- copy the last (or Nth) assistant message to  | struct`CopyCommand` |
| `dashboard.rs` | 150 | `/dashboard` — open the Agent Dashboard view. | struct`DashboardCommand` |
| `debug.rs` | 199 | `/debug` — debug-overlay toggles (scroll HUD, FPS HUD,  | struct`DebugCommand` |
| `docs.rs` | 224 | `/docs` -- open How-to Guides (in-TUI) or online Build  | struct`DocsCommand` |
| `doctor.rs` | 223 | `/doctor` — diagnose terminal, color/theme, clipboard,  | struct`DoctorCommand` |
| `edit_prompt.rs` | 129 | `/edit-prompt` -- edit the minimal-mode composer in an  | struct`EditPromptCommand` |
| `effort.rs` | 373 | `/effort` — set reasoning effort on the current model w | struct`EffortCommand` |
| `effort_levels.rs` | 78 | Shared reasoning-effort dropdown levels for `/model` an | fn`effort_description`, fn`legacy_effort_options`, fn`build_effort_arg |
| `exit.rs` | 29 | `/quit` (alias `/exit`) -- quit the application. | struct`ExitCommand` |
| `expand.rs` | 114 | `/expand` -- re-print the last collapsed block, fully e | struct`ExpandCommand` |
| `export.rs` | 245 | `/export [filename]` -- export the current conversation | struct`ExportCommand` |
| `feedback.rs` | 38 | `/feedback` -- send session feedback. | struct`FeedbackCommand` |
| `find.rs` | 133 | `/find` -- open an incremental search over the conversa | struct`FindCommand` |
| `fork.rs` | 338 | `/fork` -- branch the current session into a peer top-l | fn`parse_fork_args`, struct`ForkArgs`, struct`ForkCommand` |
| `gboom.rs` | 46 | `/gboom` -- hidden easter egg. Launches a tiny raycaste | struct`GboomCommand` |
| `help.rs` | 65 | `/help` -- open the command palette (the command + shor | struct`HelpCommand` |
| `history.rs` | 79 | `/history` -- open the prompt-history search overlay. | struct`HistoryCommand` |
| `home.rs` | 29 | `/home` -- exit the current session and return to the w | struct`HomeCommand` |
| `imagine.rs` | 108 | — | struct`ImagineCommand` |
| `imagine_video.rs` | 116 | — | struct`ImagineVideoCommand` |
| `import_claude.rs` | 36 | `/import-claude` -- open the interactive Claude setting | struct`ImportClaudeCommand` |
| `jump.rs` | 75 | Minimal mode has no interactive scrollback pane to scro | struct`JumpCommand` |
| `login.rs` | 24 | `/login` -- log in or re-authenticate with your account | struct`LoginCommand` |
| `logout.rs` | 24 | `/logout` -- remove auth credentials and return to the  | struct`LogoutCommand` |
| `loop_cmd.rs` | 374 | Pre-built slice for `LoopCommand::required_tools()`. Li | struct`LoopCommand` |
| `mcps.rs` | 25 | — | struct`McpsCommand` |
| `mod.rs` | 801 | Concrete slash command implementations. | fn`builtin_commands` |
| `model.rs` | 493 | `/model` (alias `/m`) — switch model + (optionally) rea | struct`ModelCommand` |
| `multiline.rs` | 134 | `/multiline` -- toggle multiline input mode. | struct`MultilineCommand` |
| `new.rs` | 29 | `/new` (alias `/clear`) -- create a new session. | struct`NewCommand` |
| `personas.rs` | 30 | `/personas` -- open the agents modal on the Personas ta | struct`PersonasCommand` |
| `plan.rs` | 195 | `/plan` -- enter plan mode. | struct`PlanCommand` |
| `plugin.rs` | 106 | `/hooks` and `/plugins` -- open the hooks/plugins modal | struct`HooksCommand`, struct`PluginsCommand`, struct`MarketplaceComman |
| `privacy.rs` | 213 | `/privacy` -- show or toggle privacy and data retention | fn`parse_privacy_arg`, struct`PrivacyCommand` |
| `queue.rs` | 91 | `/queue` -- list the queued prompts as a committed syst | struct`QueueCommand` |
| `recap.rs` | 37 | `/recap` (alias `/summarize`) -- summarize the session  | struct`RecapCommand` |
| `release_notes.rs` | 60 | `/release-notes` -- view release notes for the current  | struct`ReleaseNotesCommand` |
| `remember.rs` | 38 | `/remember` -- save a memory note. | struct`RememberCommand` |
| `rename.rs` | 54 | `/rename` (alias `/title`) -- rename the current sessio | struct`RenameCommand` |
| `resume.rs` | 24 | `/resume` -- open session picker overlay to resume a pr | struct`ResumeCommand` |
| `rewind.rs` | 26 | — | struct`RewindCommand` |
| `screen_mode_switch.rs` | 229 | `/minimal` and `/fullscreen` — session-scoped re-exec o | struct`ScreenModeSwitchCommand` |
| `scroll_debug.rs` | 41 | `/scroll-debug` — toggle the scroll-diagnostics HUD | struct`ScrollDebugCommand` |
| `session_info.rs` | 34 | `/session-info` -- show current session info (instant,  | struct`SessionInfoCommand` |
| `settings_cmd.rs` | 99 | `/settings` -- open the settings modal. | struct`SettingsCommand` |
| `share.rs` | 34 | `/share` -- share current session via URL. | struct`ShareCommand` |
| `tasks.rs` | 92 | `/tasks` -- list background tasks, subagents, and sched | struct`TasksCommand` |
| `theme.rs` | 567 | `/theme` (alias `/t`) -- switch the color theme. | struct`ThemeCommand` |
| `timeline.rs` | 43 | `/timeline` -- toggle the timeline sidebar (per-turn ti | struct`TimelineCommand` |
| `timestamps.rs` | 32 | `/timestamps` -- toggle timestamp display on messages. | struct`TimestampsCommand` |
| `toggle_mouse_reporting.rs` | 116 | `/toggle-mouse-reporting` — flip terminal mouse capture | struct`ToggleMouseReportingCommand` |
| `transcript.rs` | 101 | `/transcript` -- view the full conversation transcript  | struct`TranscriptCommand` |
| `tutorial.rs` | 81 | `/tutorial` -- open the onboarding tutorial overlay. | struct`TutorialCommand` |
| `usage.rs` | 70 | `/usage` — session token/cost; consumer accounts can al | struct`UsageCommand` |
| `view_plan.rs` | 33 | `/view-plan` -- open the current saved plan preview. | struct`ViewPlanCommand` |
| `vim_mode.rs` | 31 | `/vim-mode` -- toggle vim-style scrollback keybindings. | struct`VimModeCommand` |
| `voice.rs` | 60 | `/voice` — toggle dictation: start recording now, stop  | struct`VoiceCommand` |
| `workflows.rs` | 78 | — | struct`WorkflowsCommand` |

### `tips/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `clear_detector.rs` | 186 | Undo-tip trigger: detects a user-initiated wipe of a su | fn`undo_tip`, struct`ClearDetector` |
| `clipboard_focus.rs` | 480 | Clipboard-image tip trigger: while the terminal is focu | fn`clipboard_image_tip`, fn`run_clipboard_check`, struct`CheckOutcome` |
| `ephemeral.rs` | 412 | The ephemeral tip primitive: a single-slot, TTL'd, dedu | fn`tip_row_renderable`, struct`EphemeralTip`, struct`EphemeralTipState |
| `mod.rs` | 20 | Ephemeral tip primitive: a single-slot, TTL'd hint line | — |
| `plan_nudge.rs` | 157 | Plan-nudge trigger: detects planning keywords typed int | fn`plan_nudge_tip`, fn`prompt_mentions_planning` |
| `render.rs` | 173 | Tip renderer. | fn`tip_height`, fn`render_tip`, fn`render_ephemeral_tip` |
| `send_now.rs` | 66 | Tip after queuing a follow-up while a turn is running:  | fn`send_now_tip` |
| `small_screen.rs` | 106 | Small-screen tip: on smallish terminals, advertise that | fn`small_screen_band_contains`, fn`small_screen_tip` |
| `ssh_wrap.rs` | 94 | SSH tip shown over an unwrapped remote session. | fn`ssh_wrap_tip` |
| `word_select.rs` | 108 | Tip after double-clicking scrollback while text-selecti | fn`word_select_tip` |

### `views/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `agent.rs` | 2260 | Agent view layout and rendering helpers. | fn`effective_compact`, fn`fill_background`, fn`render_follow_ups`, fn` |
| `agent_status.rs` | 909 | Agent status bar — composable right-aligned status item | fn`format_tokens_compact`, fn`active_phase_label`, fn`classifier_attem |
| `agents_modal.rs` | 3318 | Agents modal popup — lists all agent definitions (built | fn`build_agent_list`, fn`merge_persona_lists`, fn`load_agent_toggle`,  |
| `announcements.rs` | 1632 | Session announcement banner: one slot, critical always  | fn`upgrade_cta_reserve`, fn`render_cta_button`, fn`is_dismissible`, fn |
| `block_viewer.rs` | 1803 | Block viewer — fullscreen content viewer for scrollback | struct`ContentLine`, struct`DiffLineMeta`, struct`BlockViewerPane`, st |
| `btw_overlay.rs` | 904 | `/btw` side question inline panel. | fn`btw_panel_height`, fn`render_btw_panel`, enum`BtwOverlayState` |
| `completion_dropdown.rs` | 414 | Dropdown renderer for shell command completion suggesti | fn`dropdown_height`, fn`scroll_offset`, fn`render_dropdown` |
| `context_bar.rs` | 432 | Context usage bar — shows token usage in the status bar | fn`fmt_pct5`, fn`fmt_tokens`, fn`default_breakpoints`, fn`blend_color` |
| `credit_bar.rs` | 817 | Credit balance indicator for the agent status bar. | fn`format_usage_summary`, fn`usage_warning`, fn`usage_warning_for_sess |
| `debug_style.rs` | 93 | Theme-agnostic chrome for debug overlays (scroll HUD, F | fn`overlay_body`, fn`overlay_title`, fn`render_panel` |
| `extensions_modal.rs` | 7120 | Extensions modal popup (Hooks, Plugins, Marketplace, Sk | fn`fuzzy_matches_hook`, fn`source_has_matching_plugin`, fn`filtered_ma |
| `fps_hud.rs` | 265 | Release-safe FPS readout — `/debug fps`, `GROK_FPS` on  | struct`FpsHud`, struct`FpsOverlay` |
| `goal_detail.rs` | 2482 | Expanded goal detail overlay — full-screen popup showin | fn`format_elapsed`, fn`truncate_to_width`, fn`strip_control_chars`, fn |
| `history_search.rs` | 719 | Prompt history search with background-thread nucleo mat | struct`HistoryEntry`, struct`HistoryMatchResult`, struct`HistorySearch |
| `import_claude_modal.rs` | 1444 | Interactive modal for selectively importing Claude sett | fn`render_import_claude_modal`, struct`ImportClaudeModalState`, enum`I |
| `jump.rs` | 254 | `/jump` picker: an overlay listing every turn in the co | fn`handle_jump_key`, fn`move_cursor`, fn`set_jump_cursor`, fn`jump_act |
| `mcps_modal.rs` | 741 | MCP server data types, status enum, response conversion | fn`section_key`, fn`section_label`, fn`managed_connectors_url`, fn`man |
| `memory_modal.rs` | 1682 | `/memory` browser modal -- view-state, rendering, and i | fn`build_entries`, fn`render_memory_modal`, fn`handle_memory_key`, fn` |
| `mod.rs` | 55 | Screen rendering — each screen type has its own renderi | — |
| `modal.rs` | 1472 | Modal dialogs and the [`ActiveModal`] enum. | fn`howto_list_modal`, fn`default_palette_entries`, fn`filter_palette_e |
| `modal_window.rs` | 1968 | Shared modal window chrome component. | fn`set_embedded`, fn`embedded`, fn`embedded_row_style`, fn`push_vim_na |
| `new_worktree_dialog.rs` | 271 | Popup dialog for creating a new worktree with an option | fn`render_new_worktree_dialog` |
| `overlay.rs` | 171 | Shared overlay pane state machine. | fn`handle_overlay_key`, fn`handle_overlay_nav_key`, struct`OverlayStat |
| `overlay_list.rs` | 221 | Shared prompt-area list overlay: accent bar, bold title | struct`ListOverlay`, struct`RowCtx` |
| `permission_view.rs` | 3071 | Permission view state and helpers. | fn`permission_chrome_height_pub`, fn`permission_view_height`, fn`inlin |
| `persona_detail.rs` | 931 | Persona detail/edit modal — structured view of a person | fn`render_persona_detail`, fn`handle_persona_detail_key`, fn`handle_pe |
| `picker.rs` | 4036 | Shared picker rendering helpers. | fn`render_picker_frame`, fn`compute_scroll_offset`, fn`search_bar_layo |
| `plan_approval_view.rs` | 490 | Placeholder body for the plan-approval preview when `ex | fn`plan_approval_status_label`, fn`send_exit_plan_response`, fn`inline |
| `privacy_banner.rs` | 169 | Coding-data sharing upsell banner (Figma "Data Sharing  | fn`render`, struct`PrivacyBannerRects` |
| `progress_bar.rs` | 170 | Unicode block progress bar at 1/8th-cell resolution via | fn`progress_bar_spans`, fn`render_progress_bar` |
| `prompt_suggestion.rs` | 362 | Next-prompt suggestion controller (tab autocomplete gho | fn`resolve_enabled`, fn`suggestion_size`, fn`resolve_model`, struct`Pr |
| `question_view.rs` | 3338 | Question view state and helpers. | fn`option_heights`, fn`total_options_height`, fn`item_index_at_visual_ |
| `queue_pane.rs` | 1992 | Queue pane — renders queued prompts in a `ListPane`. | fn`visible_held_server_row`, fn`kind_from_wire`, struct`QueueRowRef`,  |
| `rewind.rs` | 1543 | Whether the "File changes only" row exists at all. `fal | fn`handle_rewind_key`, fn`move_cursor`, fn`confirm_cursor`, fn`rewind_ |
| `scroll_debug_hud.rs` | 244 | Scroll-diagnostics HUD — the in-pager "scroll playgroun | struct`ScrollDebugHud`, struct`ViewportDebug`, struct`ScrollDebugPanel |
| `session_picker.rs` | 1650 | Shared session picker helpers. | fn`repo_name_from_cwd`, fn`loading_spinner_active`, fn`capture_picker_ |
| `session_title.rs` | 328 | Session display-title helpers shared by the dashboard a | fn`entry_title`, fn`last_user_prompt_line`, fn`last_agent_message_line |
| `shortcuts_bar.rs` | 428 | Shortcuts bar — renders keyboard hints. | fn`compute_effective_hints`, struct`HintItem`, struct`ShortcutsBar`, s |
| `shortcuts_help.rs` | 3707 | All-shortcuts cheatsheet modal (Ctrl+. / Ctrl+X). | fn`default_collapsed`, fn`build_entries`, fn`build_initial_picker_stat |
| `slash_dropdown.rs` | 811 | Dropdown list renderer for slash command completion. | fn`desired_item_rows`, fn`render_dropdown`, struct`RenderedDropdown` |
| `status_bar.rs` | 93 | StatusBar widget - displays context info at the top. | struct`StatusBar` |
| `subagent_catalog_pane.rs` | 518 | Subagent catalog pane — browseable list of bundled pers | struct`SubagentCatalogPane` |
| `tasks_pane.rs` | 3535 | Combined tasks pane — unified overlay panel showing bot | fn`highlight_bash_command`, struct`TasksPane`, enum`TaskEntryId`, enum |
| `timeline.rs` | 550 | Timeline sidebar: a tick rail (one tick per turn) that  | fn`rail_width`, fn`compute_rail`, fn`chevron_target`, fn`render_rail`, |
| `todo_pane.rs` | 565 | Todo pane — renders `TodoItem`s from `xai-grok-tools` i | struct`TodoStatusStyle`, struct`TodoPaneStyle`, struct`TodoListEntry`, |
| `turn_status.rs` | 1635 | Turn status line — single-row widget showing current tu | fn`pending_diamond_color`, fn`format_still_running`, fn`is_sendable_wa |
| `tutorial.rs` | 693 | Onboarding tutorial overlay (`/tutorial`). | fn`handle_tutorial_input`, fn`render_tutorial`, struct`TutorialState`, |
| `workflows.rs` | 1536 | — | fn`footer_shortcuts`, fn`modal_config`, fn`phase_rail`, fn`render_work |

### `views/dashboard/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `layout.rs` | 1075 | Pure layout computation for the dashboard view. | fn`peek_live_tail_desired_content`, fn`peek_max_box_rows`, fn`chrome_o |
| `mod.rs` | 153 | Agent Dashboard — top-level overview of every session i | fn`overlay_cycle_order`, fn`dashboard_enabled`, fn`session_switch_hint |
| `peek.rs` | 2348 | Peek-panel state + helpers. | fn`compute_peek_fields`, fn`peek_model_and_mode`, fn`render_peek_panel |
| `peek_tail.rs` | 464 | Dense live-tail paint for the dashboard peek middle. | fn`densified_body_line_count`, fn`scrollback_has_last_user`, fn`paint_ |
| `render.rs` | 8950 | Dashboard rendering. | fn`render_dashboard`, fn`focusables`, fn`section_of_row`, fn`cached_ho |
| `row.rs` | 2035 | Dashboard rows: classification, build, filter, sort. | fn`build_rows`, fn`build_rows_with_roster`, fn`classify_top_level`, fn |
| `state.rs` | 10797 | Dashboard state types — `DashboardState`, `DashboardRow | fn`scrollback_mut_for_row`, fn`scrollback_available_for_row`, fn`parse |

### `views/file_search/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `context.rs` | 367 | @-context detection: parses `@query` tokens from prompt | fn`detect`, fn`detect_with_drill`, fn`normalize_display_path`, struct` |
| `dropdown.rs` | 237 | Dropdown list renderer for @-completion results. | fn`render_dropdown`, fn`dropdown_height` |
| `line_viewer.rs` | 1833 | Line viewer popup for selecting line ranges from a file | fn`render_line_viewer`, struct`SourceLine`, struct`CommentLine`, struc |
| `mod.rs` | 49 | @-provider: fuzzy file completion for `@foo/bar` refere | fn`styled_file_ref` |
| `state.rs` | 392 | File search state: owns the fuzzy matcher daemon, resul | struct`FileSearchReplacement`, struct`FileSearchState` |

### `views/list_pane/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `layout.rs` | 298 | Layout cache for the list pane. | enum`WrapMode`, enum`ListLayoutCache` |
| `mod.rs` | 316 | Generic scrollable list pane widget. | fn`line_display_width`, struct`ListPaneStyle`, trait`ListItem` |
| `render.rs` | 1753 | `ListPane<'a, T>` — the rendering widget for a scrollab | struct`ListPane` |

### `views/list_pane/state/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `methods.rs` | 2307 | Create a new state with the given wrap mode and follow- | — |
| `mod.rs` | 2778 | `ListPaneState` — non-generic view state for a scrollab | struct`ListMatcher`, struct`ListFilter`, struct`ListPaneState`, struct |

### `views/persona_detail/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `tests.rs` | 189 | — | — |

### `views/prompt_widget/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 3668 | Reusable prompt input widget. | fn`file_ref_display`, struct`ViewerRequest`, struct`PromptStyle`, stru |
| `tests.rs` | 4575 | Cmd+A is gated to Ghostty in production, but every othe | — |

### `views/settings_modal/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `input.rs` | 1241 | Settings modal keyboard and mouse input handling. | fn`handle_settings_key`, fn`handle_settings_paste`, fn`handle_settings |
| `mod.rs` | 37 | Settings modal — opens via F2, `/settings`, command pal | — |
| `render.rs` | 2903 | Settings modal rendering. | fn`render_settings_modal`, struct`ResetConfirmOverlay` |
| `state.rs` | 1092 | Settings modal state, types, and filter cache. | struct`SettingsModalState`, enum`SettingsKeyOutcome`, enum`RowEntry`,  |
| `tests.rs` | 7472 | The contextual-hints group renders as a single top-leve | — |

### `views/suggestion_controller/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 787 | Shell command suggestion controller. | struct`GhostTextState`, struct`GhostSuggestionParsed`, struct`Completi |
| `tests.rs` | 1588 | Slash suppression is a full draft invalidation, not jus | — |

### `views/welcome/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `hero_box.rs` | 985 | Hero box component — side-by-side logo + menu inside a  | — |
| `logo.rs` | 318 | Logo component — renders the braille art logo. | fn`shimmer_frame`, fn`logo_line_count`, fn`logo_visual_width`, fn`rend |
| `menu.rs` | 140 | Menu component — renders shortcut key menus. | fn`render_menu` |
| `mod.rs` | 4096 | Welcome screen — the first thing users see. | fn`render_welcome`, fn`render_session_picker`, struct`WelcomeRenderRes |
| `prompt.rs` | 121 | Prompt component — renders the welcome screen prompt us | fn`prompt_inset`, fn`render_prompt` |
| `top_bar.rs` | 187 | Top bar component — renders cwd and git info. | fn`render_top_bar`, fn`location_line`, fn`location_line_at` |

### `voice/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `auth.rs` | 45 | Bridge the shell's `AuthManager` onto the voice crate's | fn`build_voice_auth` |
| `handle.rs` | 100 | Map pipeline [`VoiceEvent`]s onto prompt-box dictation  | fn`handle_voice_event` |
| `mod.rs` | 31 | Voice input: STT pipeline integration and prompt-box di | — |

### `worktree_cmd/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `display.rs` | 325 | Extract the label from a worktree record's metadata JSO | fn`print_table`, fn`print_json`, fn`print_show`, fn`print_stats`, fn`p |
| `mod.rs` | 549 | Local response types matching the ACP response shapes. | fn`run`, struct`GcReport`, struct`DbStats`, struct`RebuildReport`, str |

---

## 12. 依赖与被依赖

依赖：`xai-tty-utils` · `xai-grok-version` · `xai-prompt-queue` · `xai-grok-pager-render` · `xai-grok-announcements` · `xai-grok-markdown` · `xai-grok-mermaid` · `xai-ratatui-textarea` · `xai-ratatui-inline` · `xai-grok-update` · `xai-acp-lib` · `xai-file-utils` · `xai-grok-config` · `xai-grok-voice` · `xai-grok-shell` · `xai-grok-workspace` · `xai-token-estimation` · `xai-grok-telemetry` · `xai-fast-worktree` · `xai-grok-tools` · `xai-grok-agent` · `xai-grok-plugin-marketplace` · `xai-hooks-plugins-types` · `xai-grok-sandbox` · `xai-crash-handler`

被依赖：`xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-pager-minimal` · `xai-grok-pager-pty-harness` · `xai-grok-pager-render` · `xai-prompt-queue` · `xai-sqlite-journal`

---

## 13. 阅读顺序

1. `app/mod.rs` 模块图
2. `app/actions.rs` — Action 全貌（可搜索关键字）
3. `app/dispatch/router.rs` — 入口 dispatch
4. `app/event_loop.rs` — 主循环
5. `app/acp_handler/routing.rs` + `session_notification.rs`
6. `scrollback/blocks/tool/` — 工具 UI
7. [03 TUI 架构](../03_tui_architecture.md)

