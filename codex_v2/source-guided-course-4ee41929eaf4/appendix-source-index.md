# 附录：源码证据索引

> 基线 `4ee41929eaf4`。路径相对仓库根目录；更新代码后优先搜索符号名。

## 怎样读这份索引

| 词语 | 含义 |
|---|---|
| 文件 | 源码所在路径；以 `/` 结尾时表示目录 |
| 符号 | 类型、函数、trait 或 enum 名，适合使用 `rg` 搜索 |
| 入口 | 建议开始追踪某条路径的位置，不代表它拥有全部状态 |
| 测试入口 | 最接近对外行为、可用来验证理解的测试目录或文件 |
| 同上 | 与表格上一行使用同一源码文件 |

通用代码名的解释见[术语总表](glossary.md)。

## 进程与 UI

| 主题 | 文件 | 符号 |
|---|---|---|
| 顶层 CLI | `codex-rs/cli/src/main.rs` | `main`, `cli_main`, `MultitoolCli` |
| TUI 启动 | `codex-rs/tui/src/lib.rs` | `run_main`, `run_ratatui_app`, `start_app_server` |
| TUI session 生命周期 | `codex-rs/tui/src/app/session_lifecycle.rs` | 搜索 `ThreadStart`, `TurnStart` |
| TUI notification 路由 | `codex-rs/tui/src/app/thread_routing.rs` | notification / thread routing |
| notification 目标 | `codex-rs/tui/src/app/app_server_event_targets.rs` | `ServerNotification` match |

## App-server

| 主题 | 文件 | 符号 |
|---|---|---|
| 进程入口 | `codex-rs/app-server/src/main.rs` | `main` |
| JSON-RPC 解析 | `codex-rs/app-server/src/message_processor.rs` | `deserialize_client_request` |
| 请求处理 | `codex-rs/app-server/src/request_processors/` | thread / turn processors |
| core event 转换 | `codex-rs/app-server/src/bespoke_event_handling.rs` | `EventMsg` match |
| v2 thread API | `codex-rs/app-server-protocol/src/protocol/v2/thread.rs` | `ThreadStartParams`, `ThreadStartedNotification` |
| v2 turn API | `codex-rs/app-server-protocol/src/protocol/v2/turn.rs` | `TurnStartParams`, `TurnStartedNotification` |
| API 说明 | `codex-rs/app-server/README.md` | v2 methods and examples |

## Core 生命周期

| 主题 | 文件 | 符号 |
|---|---|---|
| Thread 管理 | `codex-rs/core/src/thread_manager.rs` | `ThreadManager`, `StartThreadOptions` |
| Session | `codex-rs/core/src/session/mod.rs` | `Session`, `SessionIo::submit` |
| Submission 分派 | `codex-rs/core/src/session/handlers.rs` | `submission_loop` |
| Turn 主循环 | `codex-rs/core/src/session/turn.rs` | `run_turn` |
| Prompt 构造 | 同上 | `build_prompt`, `run_sampling_request` |
| Stream 消费 | 同上 | `try_run_sampling_request` |
| 跨层操作/事件 | `codex-rs/protocol/src/protocol.rs` | `Submission`, `Op`, `Event`, `EventMsg` |

## 模型与工具

| 主题 | 文件 | 符号 |
|---|---|---|
| Prompt 类型 | `codex-rs/core/src/client_common.rs` | `Prompt` |
| 模型 session | `codex-rs/core/src/client.rs` | `ModelClient`, `ModelClientSession::stream` |
| 增量 WS input | 同上 | `get_incremental_items` |
| Response items | `codex-rs/protocol/src/models.rs` | `ResponseItem` |
| Tool router | `codex-rs/core/src/tools/router.rs` | `ToolRouter`, `ToolCall` |
| Tool registry | `codex-rs/core/src/tools/registry.rs` | `ToolRegistry`, `CoreToolRuntime` |
| Tool plan / exposure | `codex-rs/core/src/tools/spec_plan.rs` | `build_tool_router`, `finalize_tool_router`, `ToolExposure` |
| Tool parallel / cancellation | `codex-rs/core/src/tools/parallel.rs` | `ToolCallRuntime`, parallel execution gate |
| Tool lifecycle | `codex-rs/core/src/tools/lifecycle.rs` | `notify_tool_start`, `notify_tool_finish`, `notify_tool_aborted` |
| Tool hooks dispatch | `codex-rs/core/src/tools/registry.rs` | `pre_tool_use_payload`, `post_tool_use_payload`, `dispatch_any_with_terminal_outcome` |
| Approval routing | `codex-rs/core/src/tools/approvals.rs` | `ApprovalAction`, `Session::request_approval` |
| Approval + Sandbox orchestration | `codex-rs/core/src/tools/orchestrator.rs` | `ToolOrchestrator::run` |
| Sandbox runtime contract | `codex-rs/core/src/tools/sandboxing.rs` | `ToolRuntime`, `SandboxAttempt`, `ExecApprovalRequirement` |
| Simple tool contrast | `codex-rs/core/src/tools/handlers/current_time.rs` | `CurrentTimeHandler` |
| Shell dispatch | `codex-rs/core/src/tools/handlers/shell.rs` | `ShellCommandHandler`, `run_exec_like` |
| Apply patch dispatch | `codex-rs/core/src/tools/handlers/apply_patch.rs` | `ApplyPatchHandler`, `ApplyPatchArgumentDiffConsumer` |
| Unified exec schema | `codex-rs/core/src/tools/handlers/shell_spec.rs` | `create_exec_command_tool_with_environment_id`, `create_write_stdin_tool` |
| Unified exec handlers | `codex-rs/core/src/tools/handlers/unified_exec/` | `ExecCommandHandler`, `WriteStdinHandler` |
| Unified exec runtime | `codex-rs/core/src/tools/runtimes/unified_exec.rs` | `UnifiedExecRuntime`, local/remote launch preparation |
| Process session manager | `codex-rs/core/src/unified_exec/process_manager.rs` | `exec_command`, `write_stdin`, `collect_output_until_deadline` |
| Local/remote process wrapper | `codex-rs/core/src/unified_exec/process.rs` | `UnifiedExecProcess`, `ProcessHandle`, output tasks |
| Streaming and exit watcher | `codex-rs/core/src/unified_exec/async_watcher.rs` | `start_streaming_output`, `spawn_exit_watcher` |
| Bounded command output | `codex-rs/core/src/unified_exec/head_tail_buffer.rs` | `HeadTailBuffer` |
| Background terminal API | `codex-rs/app-server/src/request_processors/thread_processor.rs` | background terminals list/terminate/clean |
| Tool output trait | `codex-rs/tools/src/tool_output.rs` | `ToolOutput` |
| Core tool outputs | `codex-rs/core/src/tools/context.rs` | `ToolOutput` implementations |
| Stream item 处理 | `codex-rs/core/src/stream_events_utils.rs` | `handle_output_item_done` |

## 上下文与恢复

| 主题 | 文件 | 符号 |
|---|---|---|
| Prompt history | `codex-rs/core/src/context_manager/history.rs` | `ContextManager::for_prompt` |
| Context 更新 | `codex-rs/core/src/session/mod.rs` | `record_context_updates_and_set_reference_context_item` |
| Compaction | `codex-rs/core/src/compact.rs` | compact 主路径 |
| Resume | `codex-rs/core/tests/suite/resume.rs` | 行为测试 |
| Fork | `codex-rs/core/src/thread_manager.rs` | `fork_history_from_snapshot` |
| Fork 测试 | `codex-rs/core/tests/suite/fork_thread.rs` | 行为测试 |

## 测试入口

| 主题 | 路径 |
|---|---|
| Agent 主行为 | `codex-rs/core/tests/suite/` |
| 测试 builder | `codex-rs/core/tests/common/test_codex.rs` |
| Responses mock helpers | `codex-rs/core/tests/common/responses.rs` 及 test support exports |
| History 规范化 | `codex-rs/core/src/context_manager/history_tests.rs` |
| App-server v2 | `codex-rs/app-server/tests/suite/v2/` |
| TUI 行为与 snapshots | `codex-rs/tui/src/app/tests/`、`codex-rs/tui/tests/` |

## Skills、plugins 与 MCP

| 主题 | 文件 | 符号 |
|---|---|---|
| 显式依赖解析 | `codex-rs/core/src/session/turn.rs` | `required_mcp_servers_for_input` |
| Turn 注入 | 同上 | `build_skills_and_plugins` |
| Step 快照 | `codex-rs/core/src/session/step_context.rs` | `StepContext` |
| MCP 不可变绑定 | `codex-rs/codex-mcp/src/binding.rs` | `McpBinding`, `PreparedMcpCall` |
| Skill extension | `codex-rs/ext/skills/src/extension.rs` | `SkillsExtension` |
| Tool 暴露计划 | `codex-rs/core/src/tools/spec_plan.rs` | `build_tool_router` |

## 建议保存的搜索命令

```bash
rg -n 'pub enum Op|pub enum EventMsg' codex-rs/protocol/src/protocol.rs
rg -n 'run_turn|run_sampling_request|try_run_sampling_request' codex-rs/core/src/session/turn.rs
rg -n 'ThreadStart|TurnStart' codex-rs/tui/src codex-rs/app-server/src
rg -n 'build_tool_call|dispatch_tool_call' codex-rs/core/src/tools
rg -n 'for_prompt' codex-rs/core/src/context_manager
```

## 第二阶段高级专题

| 主题 | 文件 | 符号 |
|---|---|---|
| Code Mode 服务 | `codex-rs/core/src/tools/code_mode/mod.rs` | `CodeModeService`, `call_nested_tool` |
| Code Mode 执行工具 | `codex-rs/core/src/tools/code_mode/execute_handler.rs` | `CodeModeExecuteHandler` |
| Code Mode session 契约 | `codex-rs/code-mode-protocol/src/session.rs` | `CodeModeSession`, `CodeModeSessionDelegate` |
| JS runtime service | `codex-rs/code-mode-runtime/src/service.rs` | `InProcessCodeModeSession` |
| 多 Agent 控制面 | `codex-rs/core/src/agent/control.rs` | `AgentControl` |
| 子 Agent 创建/恢复 | `codex-rs/core/src/agent/control/spawn.rs` | `spawn_agent_internal`, `ensure_v2_agent_loaded` |
| Agent 内存身份 | `codex-rs/core/src/agent/registry.rs` | `AgentRegistry`, `AgentMetadata` |
| 持久拓扑 | `codex-rs/agent-graph-store/src/store.rs` | `AgentGraphStore` |
| V2 协作工具 | `codex-rs/core/src/tools/handlers/multi_agents_v2.rs` | collaboration handlers |
| 环境管理 | `codex-rs/exec-server/src/environment.rs` | `EnvironmentManager`, `Environment` |
| Turn 环境快照 | `codex-rs/core/src/environment_selection.rs` | `TurnEnvironmentSnapshot` |
| Shell 执行编排 | `codex-rs/core/src/tools/handlers/shell.rs` | `run_exec_like` |
| 交互进程 | `codex-rs/core/src/unified_exec/` | `UnifiedExecProcessManager` |
| Sandbox 选择/转换 | `codex-rs/sandboxing/src/manager.rs` | `SandboxManager` |
| 模型传输 | `codex-rs/core/src/client.rs` | `ModelClient`, `ModelClientSession` |
| WebSocket 行为测试 | `codex-rs/core/tests/suite/client_websockets.rs` | incremental/fallback tests |

## 第三阶段工程化专题

| 主题 | 文件 | 符号 |
|---|---|---|
| 配置来源与合并 | `codex-rs/config/src/state.rs`、`loader/mod.rs`、`merge.rs` | `ConfigLayerStack` |
| 配置强制要求 | `codex-rs/config/src/config_requirements.rs` | requirements types |
| Feature registry | `codex-rs/features/src/lib.rs` | `Feature`, `FeatureSpec`, `FEATURES` |
| Hook 事件 | `codex-rs/protocol/src/protocol.rs` | `HookEventName` |
| Hook engine | `codex-rs/hooks/src/engine/` | discovery、dispatcher、output parser |
| Extension registry | `codex-rs/ext/extension-api/src/registry.rs` | `ExtensionRegistry`, `ExtensionRegistryBuilder` |
| Extension 状态 | `codex-rs/ext/extension-api/src/state.rs` | `ExtensionData` |
| Memory 总览 | `codex-rs/memories/README.md` | Phase 1 / Phase 2 |
| Memory 写入 | `codex-rs/memories/write/src/phase1.rs`、`phase2.rs` | extraction / consolidation |
| Memory 读取 | `codex-rs/ext/memories/src/extension.rs` | `MemoriesExtension` |
| 模型目录 | `codex-rs/models-manager/src/manager.rs` | `ModelsManager`, `RefreshStrategy` |
| Provider 配置 | `codex-rs/model-provider-info/src/lib.rs` | `ModelProviderInfo` |
| Context window | `codex-rs/core/src/session/context_window.rs` | `ContextWindowTokenStatus` |
| Token budget | `codex-rs/core/src/session/token_budget.rs` | defaults、reminder、fallback |
| Turn 性能 | `codex-rs/core/src/turn_timing.rs` | `TurnTimingState` |

## 第四阶段数据与调试专题

| 主题 | 文件 | 符号 |
|---|---|---|
| Turn API 输入 | `codex-rs/app-server-protocol/src/protocol/v2/turn.rs` | `TurnStartParams` |
| Core 用户操作 | `codex-rs/protocol/src/protocol.rs` | `Op::UserInput` |
| 模型请求中间表示 | `codex-rs/core/src/client_common.rs` | `Prompt` |
| 模型输入与工具项 | `codex-rs/protocol/src/models.rs` | `ResponseItem` |
| Prompt slots | `codex-rs/ext/extension-api/src/contributors/prompt.rs` | `PromptSlot` |
| Base instructions | `codex-rs/protocol/src/models.rs` | `BaseInstructions` |
| World-state 更新 | `codex-rs/core/src/session/world_state.rs` | full state / diff |
| 错误语义 | `codex-rs/protocol/src/error.rs` | `CodexErrorDetails`, `is_retryable` |
| Turn 取消与终止 | `codex-rs/core/src/tasks/mod.rs` | abort / complete lifecycle |
| Core 事件全集 | `codex-rs/protocol/src/protocol.rs` | `EventMsg` |
| Session telemetry | `codex-rs/otel/src/events/session_telemetry.rs` | `SessionTelemetry` |
| Trace context | `codex-rs/otel/src/trace_context.rs` | W3C trace helpers |
| Turn profile | `codex-rs/analytics/src/facts.rs`、`core/src/turn_timing.rs` | `TurnProfile`, `TurnTimingState` |

## 第四阶段修改实验与最小实现

| 主题 | 文件 | 符号 |
|---|---|---|
| 简单只读工具 | `codex-rs/core/src/tools/handlers/current_time.rs` | `CurrentTimeHandler` |
| 工具计划与注册 | `codex-rs/core/src/tools/spec_plan.rs` | `build_tool_router`、Registry add |
| 工具端到端测试 | `codex-rs/core/tests/suite/current_time_reminder.rs` | current-time tool call tests |
| v2 Turn Params | `codex-rs/app-server-protocol/src/protocol/v2/turn.rs` | `TurnStartParams` |
| v2 请求处理 | `codex-rs/app-server/src/request_processors/turn_processor.rs` | turn/start mapping |
| Feature metadata | `codex-rs/features/src/lib.rs` | `Feature`, `FeatureSpec`, `FEATURES` |
| Hook labels/scope | `codex-rs/hooks/src/lib.rs`、`engine/dispatcher.rs` | event labels、`scope_for_event` |
| Hook typed events | `codex-rs/hooks/src/events/`、`schema.rs` | request/outcome/wire schema |
| 最小 Agent 主循环映射 | `codex-rs/core/src/session/turn.rs` | `run_turn` |
| Step 一致性 | `codex-rs/core/src/session/step_context.rs` | `StepContext` |
| Tool 路由 | `codex-rs/core/src/tools/router.rs`、`registry.rs` | `ToolRouter`, registry/runtime traits |
| Turn task 与取消 | `codex-rs/core/src/tasks/mod.rs` | task lifecycle、abort、terminal event |

## 第五阶段 Rust、异步与客户端实现

| 主题 | 文件 | 符号 |
|---|---|---|
| Task 创建与所有权 | `codex-rs/core/src/tasks/mod.rs` | `CancellationToken`, `Notify`, `tokio::spawn`, `RunningTask` |
| Session 通信入口 | `codex-rs/core/src/session/mod.rs` | `SessionIo`, submission sender、event receiver、status receiver |
| Submission 消费 | `codex-rs/core/src/session/handlers.rs` | `submission_loop` |
| Tool executor trait | `codex-rs/tools/src/tool_executor.rs` | `ToolExecutor`, `ToolExecutorFuture` |
| Boxed extension Future | `codex-rs/ext/extension-api/src/contributors.rs` | `ExtensionFuture` |
| 弱 MCP 绑定 | `codex-rs/core/src/mcp_tool_exposure.rs` | `Weak<McpBinding>` |
| RAII 计时 guard | `codex-rs/core/src/turn_timing.rs` | `TurnProfileTimingGuard`, `Drop` |
| 事件转发与取消 | `codex-rs/core/src/codex_delegate.rs` | `forward_events`, `tokio::select!` |
| 执行取消 | `codex-rs/core/src/exec.rs` | `CancellationToken`, `cancelled()` |
| 一次性审批结果 | `codex-rs/core/src/state/turn.rs`、`session/mod.rs` | `pending_approvals`, `tx_approve`, `oneshot` |
| Thread 创建广播 | `codex-rs/core/src/thread_manager.rs` | `broadcast` |
| TUI app-server 启动 | `codex-rs/tui/src/lib.rs` | `AppServerTarget`, `start_app_server` |
| TUI 主事件循环 | `codex-rs/tui/src/app.rs` | `select!`, `THREAD_EVENT_CHANNEL_CAPACITY` |
| App-server 事件入口 | `codex-rs/tui/src/app/app_server_events.rs` | `handle_app_server_event` |
| Notification thread 定位 | `codex-rs/tui/src/app/app_server_event_targets.rs` | `server_notification_thread_target` |
| Thread 事件缓存 | `codex-rs/tui/src/app/thread_events.rs` | `ThreadEventStore`, `ThreadEventSnapshot` |
| Active/inactive thread 路由 | `codex-rs/tui/src/app/thread_routing.rs` | enqueue、live handling、replay |
| ChatWidget 协议入口 | `codex-rs/tui/src/chatwidget/protocol.rs` | `handle_server_notification` |
| 流式消息对账 | `codex-rs/tui/src/chatwidget/streaming.rs` | delta、finalize、consolidation |
| Turn 客户端生命周期 | `codex-rs/tui/src/chatwidget/turn_lifecycle.rs` | `TurnLifecycleState` |
| Transcript 状态 | `codex-rs/tui/src/chatwidget/transcript.rs` | `TranscriptState` |
| 状态栏状态 | `codex-rs/tui/src/chatwidget/status_state.rs` | `StatusState` |
| Core 到 v2 通知投影 | `codex-rs/app-server/src/bespoke_event_handling.rs` | `EventMsg` 到 `ServerNotification` |
| TUI app-server 行为测试 | `codex-rs/tui/src/chatwidget/tests/app_server.rs` | delta、completion、command output、replay |
| Rollout 协议类型 | `codex-rs/protocol/src/protocol.rs` | `ThreadHistoryMode`, `HistoryPosition`, `RolloutItem`, `RolloutLine` |
| Rollout crate 入口 | `codex-rs/rollout/src/lib.rs` | sessions/archived 目录、读取和记录 API |
| Rollout 持久化策略 | `codex-rs/rollout/src/policy.rs` | `is_persisted_rollout_item`, durable/transient events |
| Rollout 后台 writer | `codex-rs/rollout/src/recorder.rs` | `RolloutRecorder`, `RolloutCmd`, persist/flush/shutdown |
| Thread Store 抽象 | `codex-rs/thread-store/src/store.rs` | `ThreadStore` |
| 本地 Store 组合 | `codex-rs/thread-store/src/local/mod.rs` | `LocalThreadStore`, live recorders、SQLite handles |
| Live writer | `codex-rs/thread-store/src/local/live_writer.rs` | append、persist、flush、shutdown、projection |
| Thread 读取与回退 | `codex-rs/thread-store/src/local/read_thread.rs` | SQLite metadata、rollout fallback、ID validation |
| Thread 列表与修复 | `codex-rs/thread-store/src/local/list_threads.rs` | DB page、filesystem scan、read-repair |
| 分页历史物化 | `codex-rs/thread-store/src/local/thread_history_materialization.rs` | rollout 到 turn/item projection |
| Fork 引用与删除 | `codex-rs/thread-store/src/local/delete_thread.rs` | `RolloutReferenceIndex`, reference integrity |
| SQLite 文件管理 | `codex-rs/state/src/sqlite.rs` | `SqliteConfig`, runtime DB paths |
| State runtime | `codex-rs/state/src/runtime.rs` | `StateRuntime`、migrations、独立 DB pools |
| Thread metadata schema | `codex-rs/state/migrations/0001_threads.sql` | `threads` table |
| 分页历史 schema | `codex-rs/state/thread_history_migrations/0001_thread_history.sql` | `thread_turns`, `thread_items`, projection state |
| State DB backfill | `codex-rs/rollout/src/state_db.rs` | init、backfill gate、fallback |
| Resume/Fork runtime | `codex-rs/core/src/thread_manager.rs` | resume、fork、spawn、remove、shutdown |
| App-server resume | `codex-rs/app-server/src/request_processors/thread_processor.rs` | `thread_resume_inner`, running/cold resume |
| 持久化集成测试 | `codex-rs/core/tests/suite/sqlite_state.rs`、`rollout_list_find.rs`、`resume.rs` | index、fallback、resume |
| MCP 配置类型 | `codex-rs/config/src/mcp_types.rs` | `McpServerConfig`, transport、auth、filter、timeout |
| Thread MCP runtime | `codex-rs/core/src/session/mcp_runtime.rs`、`mcp.rs` | initial publish、dirty refresh、step binding、elicitation |
| MCP runtime 发布 | `codex-rs/codex-mcp/src/runtime.rs` | `McpRuntime`, `ArcSwap`, replace、binding、shutdown |
| MCP 连接集合 | `codex-rs/codex-mcp/src/connection_manager.rs` | `McpConnectionSet`, reuse、deferred startup、status events |
| MCP 启动与发现 | `codex-rs/codex-mcp/src/rmcp_client.rs` | transport、initialize、`tools/list`、`ManagedClient` |
| 底层 MCP client | `codex-rs/rmcp-client/src/rmcp_client.rs` | initialize、call、OAuth refresh、recovery、shutdown |
| stdio server 生命周期 | `codex-rs/rmcp-client/src/stdio_server_launcher.rs` | child process、stdin/stdout/stderr、termination |
| MCP 工具命名与过滤 | `codex-rs/codex-mcp/src/tools.rs` | `ToolInfo`, `ToolFilter`, normalization、collision hash |
| MCP 目录捕获 | `codex-rs/codex-mcp/src/connection_manager/tool_catalog.rs` | optional grace、cache、exact client、catalog revision |
| MCP Step 绑定 | `codex-rs/codex-mcp/src/binding.rs` | `McpBinding`, `PreparedMcpCall`, revision guard |
| MCP Tool Handler | `codex-rs/core/src/tools/handlers/mcp.rs` | ToolSpec、router execution、hooks、ready wait |
| MCP 调用安全链 | `codex-rs/core/src/mcp_tool_call.rs` | 参数、policy、approval、metadata、events、telemetry |
| MCP 结果回传 | `codex-rs/core/src/tools/context.rs` | `McpToolOutput`, truncation、`FunctionCallOutput` |
| MCP Resources | `codex-rs/core/src/tools/handlers/mcp_resource.rs` | list templates/resources、read、pagination |
| MCP 集成测试 | `codex-rs/core/tests/suite/rmcp_client.rs`、`mcp_tool_exposure.rs`、`mcp_auth_refresh.rs` | 调用、暴露、缓存、认证刷新 |
| 工具结果来源结构 | `codex-rs/core/src/tools/context.rs`、`context_manager/history.rs` | `McpToolOutput::to_response_item`, `FunctionCallOutput` |
| 文件读取与元数据保护 | `codex-rs/protocol/src/permissions.rs` | `deny_read_matchers`、`.git`/`.agents`/`.codex` carve-outs |
| Shell 动作与审批分类 | `codex-rs/core/src/tools/handlers/shell.rs`、`exec_policy.rs`、`tools/sandboxing.rs` | `run_exec_like`, `ExecApprovalRequirement` |
| 网络动作审批 | `codex-rs/core/src/tools/network_approval.rs` | trigger command/call、网络审批请求 |
| Guardian 审批请求 | `codex-rs/core/src/guardian/approval_request.rs` | `GuardianApprovalRequest` variants |
| Guardian transcript | `codex-rs/core/src/guardian/prompt.rs` | 来源标签、大小上限、截断与 review prompt |
| Guardian 授权策略 | `codex-rs/core/src/guardian/policy_template.md` | user authorization、untrusted evidence、risk level |
| Guardian 风险策略 | `codex-rs/core/src/guardian/policy.md` | exfiltration、credential probing、persistent weakening、destructive actions |
| Guardian 执行与失败关闭 | `codex-rs/core/src/guardian/review.rs`、`guardian/mod.rs` | routing、timeout、malformed output、circuit breaker |
| Managed MCP 身份 | `codex-rs/config/src/mcp_requirements.rs` | `McpServerRequirement` |
| Plugin 来源策略 | `codex-rs/core-plugins/src/marketplace_policy.rs` | `restrict_to_allowed_sources` |
| 工具前置安全 Hook | `codex-rs/core/src/hook_runtime.rs`、`codex-rs/hooks/src/events/pre_tool_use.rs` | block、input rewrite、additional context |
| 审批 Hook | `codex-rs/hooks/src/events/permission_request.rs` | Allow/Deny/undecided、deny wins |
| Core 事件与终态 | `codex-rs/protocol/src/protocol.rs` | `EventMsg`, `TurnStartedEvent`, `TurnCompleteEvent`, `CodexErrorInfo` |
| Turn 运行状态 | `codex-rs/core/src/state/turn.rs` | `ActiveTurn`, `RunningTask`, pending approvals/input |
| Task 收尾 | `codex-rs/core/src/tasks/mod.rs` | `TurnComplete`/`TurnAborted`、active turn 清理、rollout flush |
| 模型流重试 | `codex-rs/core/src/responses_retry.rs` | retry budget、backoff、`StreamError`、transport fallback |
| Turn 耗时分解 | `codex-rs/core/src/turn_timing.rs` | TTFT、TTFM、sampling、compaction、tool blocking |
| Tool 安全编排 | `codex-rs/core/src/tools/orchestrator.rs` | approval→sandbox→attempt→retry |
| App-server 当前 Turn 投影 | `codex-rs/app-server/src/thread_state.rs` | started/items/terminal event tracking |
| Thread 状态推导 | `codex-rs/app-server/src/thread_status.rs` | Active flags、Idle、SystemError、NotLoaded |
| Thread v2 状态协议 | `codex-rs/app-server-protocol/src/protocol/v2/thread.rs` | `ThreadStatus`, `ThreadActiveFlag` |
| Core 终态到 v2 通知 | `codex-rs/app-server/src/bespoke_event_handling.rs` | `TurnComplete`→`TurnCompletedNotification` |
| TUI Turn 收尾 | `codex-rs/tui/src/chatwidget/turn_lifecycle.rs`、`streaming.rs` | finalize、consolidation、state cleanup |
| 模型断线测试 | `codex-rs/core/tests/suite/client_websockets.rs` | reconnect、retry、fallback |
| Thread 状态测试 | `codex-rs/app-server/tests/suite/v2/thread_status.rs` | approval/input waiting flags、terminal transition |
| 仓库修改与测试规则 | `AGENTS.md` | change size、integration、snapshot、target tests、fmt/fix |
| Core 测试响应工具 | `codex-rs/core/tests/common/responses.rs` 及子模块 | SSE fixtures、`ResponseMock`、request assertions |
| Core 测试实例 | `codex-rs/core/tests/common/test_codex.rs` | `TestCodexBuilder`, `build_with_auto_env` |
| Agent integration 示例 | `codex-rs/core/tests/suite/current_time_reminder.rs` | mock model、tool loop、request inspection |
| MCP integration 示例 | `codex-rs/core/tests/suite/rmcp_client.rs` | model→tool→result→next sampling |
| App-server status 回归 | `codex-rs/app-server/tests/suite/v2/thread_status.rs` | Active→non-Active、`turn/completed` |
| TUI app-server 测试 | `codex-rs/tui/src/chatwidget/tests/app_server.rs` | notification lifecycle、用户可见状态 |
| TUI snapshots | `codex-rs/tui/src/snapshots/` | `insta` 预期输出与可见 diff |
| 仓库验证 recipes | `justfile` | `test`, `fix`, `fmt`, schema、Bazel lock、argument lint |
| Config schema 产物 | `codex-rs/core/config.schema.json` | `ConfigToml` wire/schema 同步 |
| Bazel 依赖锁 | `MODULE.bazel.lock` | Cargo dependency 变化的 Bazel lock 同步 |
| 外部贡献政策 | `docs/contributing.md` | invitation-only、topic branch、atomic commits、PR、review、CLA、security |
| PR 默认模板 | `.github/pull_request_template.md` | 外部贡献前提、issue link、高质量说明 |
| 路径评审所有权 | `.github/CODEOWNERS` | 核心 crate、签名/CI 敏感路径的 reviewer mapping |
| CI 策略总览 | `.github/workflows/README.md` | PR Bazel/fast Cargo 与 post-merge full Cargo 分工 |
| PR 阻塞入口 | `.github/workflows/blocking-ci.yml` | Bazel、policy、repo、Rust、SDK 与 required 汇总 |
| CI 结果汇总 | `.github/scripts/check_ci_results.py` | needs result 解释与最终 gate |
| Bazel 主验证 | `.github/workflows/bazel.yml` | platform matrix、Windows shards、tests、clippy、clean tree |
| 快速 Rust PR CI | `.github/workflows/rust-ci.yml` | changed paths、fmt、cargo shear、argument-comment-lint |
| 合并后完整 Rust CI | `.github/workflows/rust-ci-full.yml` | clippy、nextest、release、cross-platform、remote env |
| 仓库结构检查 | `.github/workflows/repo-checks.yml` | manifests、crate boundary、scripts、format |
| 干净工作树检查 | `.github/actions/check-clean-worktree/action.yml` | formatter/generator/test 产生的未提交漂移 |
| 大文件策略 | `.github/workflows/blob-size-policy.yml`、`.github/blob-size-allowlist.txt` | blob size gate 与受控例外 |
| 依赖策略 | `.github/workflows/cargo-deny.yml` | dependency license/advisory/source policy |
| Breaking-change review | `.codex/skills/code-review-breaking-changes/SKILL.md` | app-server、raw events、CLI、config、resume |
| Change-size review | `.codex/skills/code-review-change-size/SKILL.md` | 800/500 行指导与 staged landing |
| Test review | `.codex/skills/code-review-testing/SKILL.md` | Agent integration tests、test module/helper 规则 |
| Model-context review | `.codex/skills/code-review-context/SKILL.md` | append-only history、cache、hard cap、10K/1K token、fragment type |
| Context fragments | `codex-rs/core/src/context/` | `ContextualUserFragment` 与有界模型上下文项 |
| Context history | `codex-rs/core/src/context_manager/` | incremental history、normalize、model input |
| Tool output context | `codex-rs/core/src/tools/context.rs` | truncation、source identity、`FunctionCallOutput` |
| Config source type | `codex-rs/config/src/config_toml.rs` | serde keys、defaults、schema source |
| Review 的公开状态测试 | `codex-rs/app-server/tests/suite/v2/thread_status.rs` | turn identity、terminal status、JSON-RPC observation |

## 第六阶段性能工程

| 主题 | 文件 | 符号/入口 |
|---|---|---|
| Benchmark 仓库规则 | `AGENTS.md` | Divan、`just bench`、`just bench-smoke` |
| Benchmark recipes | `Justfile` | `bench`、`bench-smoke`、`bench-e2e`、`bench-e2e-smoke` |
| Workspace benchmark 依赖 | `codex-rs/Cargo.toml` | `divan` |
| 图片 benchmark target | `codex-rs/utils/image/Cargo.toml` | `[[bench]] prompt_images`、`harness = false` |
| 图片微基准 | `codex-rs/utils/image/benches/prompt_images.rs` | fresh/repeated、PNG/JPEG、`with_inputs`、cache-miss variants |
| 图片 prompt 处理 | `codex-rs/utils/image/src/` | `load_for_prompt_bytes`、resize、encode、cache |
| CLI 端到端基准 | `codex-rs/cli/e2e_benches/codex_help.rs` | `codex_help`、`Command::new`、success assertion |
| CLI benchmark 声明 | `codex-rs/cli/BUILD.bazel` | `codex_e2e_benchmark` |
| Bazel benchmark rule | `bazel/rules/e2e_benchmark.bzl` | `codex_e2e_benchmark`、runfiles env、wrapper |
| E2E benchmark suite | `codex-rs/BUILD.bazel` | `e2e-benchmarks` |
| Turn timing | `codex-rs/core/src/turn_timing.rs` | TTFT、TTFM、phase guards、sampling/tool/compaction profile |
| Turn profile facts | `codex-rs/analytics/src/facts.rs` | `TurnProfile`、`TurnProfileFact` |
| 性能 metric 名称 | `codex-rs/otel/src/metrics/names.rs` | turn TTFT/TTFM、API/tool duration metrics |
| Session telemetry | `codex-rs/otel/src/events/session_telemetry.rs` | `record_duration`、histogram、turn TTFT |
| Histogram 正确性测试 | `codex-rs/otel/tests/suite/timing.rs` | bounds、bucket counts、sum、count、unit、attributes |

## 第六阶段性能剖析

| 主题 | 文件 | 符号/入口 |
|---|---|---|
| Async tracing 约定 | `AGENTS.md` | `#[tracing::instrument]`、callee instrumentation 检查 |
| OpenTelemetry crate 指南 | `codex-rs/otel/README.md` | provider、logs/traces/metrics、context、shutdown |
| OTEL exporter 配置 | `codex-rs/otel/src/config.rs` | `OtelSettings`、`OtelExporter`、span attributes |
| OTEL 用户配置类型 | `codex-rs/config/src/types.rs` | `OtelConfigToml`、`OtelConfig` |
| OTEL 配置解析 | `codex-rs/core/src/config/otel.rs` | defaults、metadata validation、startup warnings |
| 性能 Metric 边界 | `codex-rs/otel/src/metrics/names.rs` | Turn/API/tool/WebSocket duration/count |
| RAII Metric timer | `codex-rs/otel/src/metrics/timer.rs` | `Timer`、`Drop`、`record` |
| Turn E2E timer | `codex-rs/core/src/tasks/mod.rs` | `TURN_E2E_DURATION_METRIC`、`RunningTask::_timer` |
| Turn 阶段画像 | `codex-rs/core/src/turn_timing.rs` | TTFT、TTFM、sampling/compaction/tool/overhead |
| Turn timing 测试 | `codex-rs/core/src/turn_timing_tests.rs` | synthetic timeline、完整 `TurnProfile` equality |
| App-server 请求 Span | `codex-rs/app-server/src/app_server_tracing.rs` | `request_span`、`typed_request_span`、attributes |
| 请求 Future 关联 | `codex-rs/app-server/src/message_processor.rs` | request context、`.instrument(...)` |
| 请求 Trace 测试 | `codex-rs/app-server/src/message_processor_tracing_tests.rs` | parent/child、request span fields |
| W3C Trace Context | `codex-rs/otel/src/trace_context.rs` | extract/inject、parent、validate、tracestate merge |
| Exec-server Context Header | `codex-rs/exec-server/src/trace_context.rs` | `current_trace_context_headers` |
| Context 传播测试 | `codex-rs/exec-server/src/trace_context_tests.rs` | traceparent/tracestate headers |
| MCP Metric Tags | `codex-rs/core/src/mcp_tool_call/telemetry.rs` | count/duration/error、sanitize、bounded code |
| Hook duration | `codex-rs/core/src/hook_runtime.rs` | `emit_hook_completed_metrics` |
| Rollout 大小观测 | `codex-rs/rollout/src/persistence_metrics.rs` | `CountingWriter`、pre/post filter、sampling |
| SQLite Metric adapter | `codex-rs/rollout/src/sqlite_metrics.rs` | `OtelDbTelemetry`、bounded originator |
| WebSocket 定向 Timing | `codex-rs/codex-api/src/endpoint/responses_websocket.rs` | `responses_websocket_timing` TRACE event |
| Rollout Trace 设计 | `codex-rs/rollout-trace/README.md` | local bundle、raw evidence、reducer、privacy |
| Tool Dispatch Rollout Trace | `codex-rs/core/src/tools/tool_dispatch_trace.rs`、`tools/registry.rs` | `ToolDispatchTrace`、各 early-return 的 completed/failed 记录 |

## 第六阶段容量与过载保护

| 主题 | 文件 | 符号/入口 |
|---|---|---|
| App-server channel 容量 | `codex-rs/app-server-transport/src/transport/mod.rs` | `CHANNEL_CAPACITY`、`OVERLOADED_ERROR_CODE`、`enqueue_incoming_message` |
| App-server 满载测试 | `codex-rs/app-server-transport/src/transport/mod.rs` | request reject、response wait、writer-full cases |
| Code Mode Host 总容量 | `codex-rs/code-mode-host/src/lib.rs` | request/cell Semaphore、outgoing channel、timeouts |
| Code Mode Peer 容量 | `codex-rs/code-mode-host/src/peer.rs` | delegate permits、cell route queue、full disconnect |
| Code Mode 协议限制 | `codex-rs/code-mode-protocol/src/host/mod.rs` | `MAX_PENDING_DELEGATE_CALLS`、host module exports |
| Skill 扫描并发常量 | `codex-rs/core-skills/src/loader.rs` | `MAX_CONCURRENT_ROOT_SCANS` |
| Skill Root 并发调度 | `codex-rs/core-skills/src/root_loader.rs` | shared Semaphore、`buffer_unordered`、stable precedence |
| Durable queue 上限 | `codex-rs/state/src/lib.rs` | `MAX_QUEUE_ITEMS` |
| Durable queue 原子插入 | `codex-rs/state/src/runtime/queued_items.rs` | count-bounded SQL insert |
| Queue 上限错误映射 | `codex-rs/thread-store/src/queue_store.rs` | `LocalQueueStore::enqueue` |
| Responses 有限重试 | `codex-rs/core/src/responses_retry.rs` | max retries、delay、warning、transport fallback |
| Responses 重试测试 | `codex-rs/core/src/responses_retry_tests.rs` | retry count、fallback、terminal error |
| 通用 HTTP retry | `codex-rs/codex-client/src/retry.rs` | `RetryPolicy`、429/5xx/transport、backoff+jitter |
| MCP reconnect 冷却 | `codex-rs/codex-mcp/src/rmcp_client.rs` | `reconnect_in_flight`、`retry_not_before`、capped backoff |
| Guardian 熔断状态 | `codex-rs/core/src/guardian/mod.rs` | consecutive/recent denials、interrupt action |
| Guardian 熔断执行 | `codex-rs/core/src/guardian/review.rs` | warning、abort active Turn |
| Guardian 熔断测试 | `codex-rs/core/src/guardian/tests.rs` | thresholds、window、reset、single trip |
| Route-aware client pool | `codex-rs/http-client/src/route_aware_client_pool.rs` | `MAX_CACHED_ROUTES`、request/connect timeout、eviction |
| Client pool 测试 | `codex-rs/http-client/src/route_aware_client_pool_tests.rs` | route reuse、eviction、timeout、redirect |
| Code Mode remote channel | `codex-rs/code-mode/src/remote_session/connection.rs` | IPC capacity、handshake/wait timeout |
| TUI 帧率限制 | `codex-rs/tui/src/tui/frame_rate_limiter.rs` | render rate control |

## 第六阶段发布与事故响应

| 主题 | 文件 | 符号/入口 |
|---|---|---|
| Rust 发布总流程 | `.github/workflows/rust-release.yml` | Tag/version gate、platform matrix、sign、package、verify、release、publish |
| Windows 发布 | `.github/workflows/rust-release-windows.yml` | Windows x64/ARM64 build 与 package |
| R2 分阶段发布 | `.github/workflows/r2-release.yml` | `stage: assets`、`stage: finalize` |
| R2 发布实现 | `.github/scripts/publish_r2_release.py` | version validation、asset/metadata staging |
| Package archive | `.github/scripts/build-codex-package-archive.sh` | primary/app-server bundle、target resources、tar archives |
| Symbols 与 strip | `.github/scripts/archive-release-symbols-and-strip-binaries.sh` | archive debug symbols before stripping |
| macOS 代码签名 | `.github/scripts/macos-signing/sign_macos_code.sh` | codesign/rcodesign backend |
| macOS DMG 公证 | `.github/scripts/macos-signing/notarize_macos_dmg_with_akv.sh` | notarize、staple、verification log |
| Linux 制品签名 | `.github/actions/linux-code-sign/action.yml` | `.sigstore` artifact |
| CI 与 release 前验证分工 | `.github/workflows/README.md` | PR fast checks、post-merge full Cargo checks |
| Feature 生命周期与开关 | `codex-rs/features/src/lib.rs` | `Stage`、`FeatureSpec`、`Features` enable/disable |
| Feature 开关测试 | `codex-rs/features/src/tests.rs` | defaults、override、legacy alias、materialize |
| 旧版本迁移容忍 | `codex-rs/state/src/migrations.rs` | `runtime_migrator`、`ignore_missing` |
| Future migration 运行测试 | `codex-rs/state/src/runtime.rs` | `open_state_sqlite_tolerates_newer_applied_migrations` |
| Released migration 兼容测试 | `codex-rs/state/src/migrations_tests.rs` | old migrator + newer migrations |
| Rollout migration checkpoint | `codex-rs/state/migrations/0047_rollout_migration_state.sql` | cursor/checkpoint、skipped rollouts |
| Crash-safe migration publish | `codex-rs/thread-store/src/local/rollout_migration/publish.rs` | temporary paths、`.pending` journal、sync |
| Breaking-change 边界 | `AGENTS.md` | app-server、raw events、CLI、config、resume |

## 第六阶段 SLO 与可靠性决策

| 主题 | 文件 | 符号/入口 |
|---|---|---|
| Metric 名称目录 | `codex-rs/otel/src/metrics/names.rs` | API、SSE、WebSocket、Turn、tool、Guardian metrics |
| Session telemetry | `codex-rs/otel/src/events/session_telemetry.rs` | API/tool success、duration、status/error fields |
| Runtime metric 汇总 | `codex-rs/otel/src/metrics/runtime_metrics.rs` | `RuntimeMetricsSummary`、counter/histogram aggregation |
| Runtime 汇总测试 | `codex-rs/otel/tests/suite/runtime_summary.rs` | API/tool/stream/WebSocket/TTFT/TTFM deep equality |
| Histogram 测试 | `codex-rs/otel/tests/suite/timing.rs` | buckets、count、sum、units |
| Turn 时间边界 | `codex-rs/core/src/turn_timing.rs` | TTFT、TTFM、E2E、phase profile |
| Turn 终态 | `codex-rs/core/src/tasks/mod.rs` | complete/abort、terminal error、idle cause、flush |
| 错误分类 | `codex-rs/protocol/src/protocol.rs` | `CodexErrorInfo`、`affects_turn_status` |
| Thread 状态协议 | `codex-rs/app-server-protocol/src/protocol/v2/thread.rs` | `ThreadStatus`、`ThreadActiveFlag` |
| Thread 状态投影 | `codex-rs/app-server/src/thread_status.rs` | running、idle、system error、waiting state |
| Thread 状态测试 | `codex-rs/app-server/tests/suite/v2/thread_status.rs` | public status transitions |
| Responses 重试 | `codex-rs/core/src/responses_retry.rs` | attempts、exhaustion、fallback |
| Responses 重试测试 | `codex-rs/core/src/responses_retry_tests.rs` | retry/fallback/terminal behavior |
| DB metric 常量 | `codex-rs/state/src/lib.rs` | init、fallback、backfill、error metrics |
| DB telemetry 分类 | `codex-rs/state/src/telemetry.rs` | db/phase/status/error low-cardinality tags |
| SQLite metric adapter | `codex-rs/rollout/src/sqlite_metrics.rs` | state telemetry to MetricsClient |
| OTEL 使用说明 | `codex-rs/otel/README.md` | provider、logs/traces/metrics、privacy、shutdown |
| OTEL 配置解析 | `codex-rs/core/src/config/otel.rs` | exporter、metadata validation、warnings |
| OTEL 配置类型 | `codex-rs/config/src/types.rs` | `OtelConfigToml`、`OtelConfig` |

## 第六阶段故障注入与韧性测试

| 主题 | 文件 | 符号/入口 |
|---|---|---|
| Responses mock harness | `codex-rs/core/tests/common/responses.rs` | SSE builders、`ResponseMock`、request assertions |
| WebSocket fallback | `codex-rs/core/tests/suite/websocket_fallback.rs` | 426、retry exhaustion、sticky HTTP fallback |
| WebSocket reconnect | `codex-rs/core/tests/suite/client_websockets.rs` | connection limit、terminal error、request harness |
| Responses retry 实现 | `codex-rs/core/src/responses_retry.rs` | bounded retry、delay、fallback、terminal error |
| Responses retry 测试 | `codex-rs/core/src/responses_retry_tests.rs` | attempt/fallback state transitions |
| App-server 满载注入 | `codex-rs/app-server-transport/src/transport/mod.rs` | capacity-one request/response/writer-full tests |
| Unix socket 生命周期 | `codex-rs/app-server-transport/src/transport/unix_socket_tests.rs` | open、message、ping、close、lock contention |
| HTTP deadline 注入 | `codex-rs/http-client/src/route_aware_client_pool_tests.rs` | slow resolver、shared redirect deadline、closed listener |
| HTTP transport 错误 | `codex-rs/http-client/src/transport_tests.rs` | timeout/error mapping |
| Crash-safe publish | `codex-rs/thread-store/src/local/rollout_migration/publish.rs` | staged paths、journal、sync、cleanup |
| Migration recovery tests | `codex-rs/thread-store/src/local/rollout_migration/startup_tests.rs` | pending journal、cursor lookback、live writer |
| Migration rollback | `codex-rs/thread-store/src/local/rollout_migration/rollback.rs` | rollback/replay recovery |
| SQLite recovery | `codex-rs/state/src/runtime/recovery.rs` | corruption classification、backup、path extraction |
| SQLite recovery tests | `codex-rs/state/src/runtime/recovery_tests.rs` | corrupt vs locked、targeted backup |
| State runtime 恢复 | `codex-rs/state/src/runtime.rs` | init、integrity check、future migrations |
| Code Mode WebSocket faults | `codex-rs/code-mode-host/tests/websocket.rs` | malformed frame isolation、dual lane、slow callback |
| Code Mode peer cleanup | `codex-rs/code-mode-host/src/peer.rs` | disconnect token、queue full、delegate cancellation |
| Code Mode bounded registration | `codex-rs/code-mode-host/src/transport_tests.rs` | registration capacity/release |
| MCP fake-time recovery | `codex-rs/codex-mcp/src/connection_manager_tests.rs` | paused time、backoff、refresh、reconnect |
| MCP reconnect implementation | `codex-rs/codex-mcp/src/rmcp_client.rs` | singleflight、retry_not_before、capped backoff |
| Turn cancellation cleanup | `codex-rs/core/src/tasks/mod.rs` | terminal event、active state、rollout flush |
| Interrupt integration tests | `codex-rs/core/tests/suite/abort_tasks.rs` | long-running tool interruption、history marker、next request |
| Failure telemetry | `codex-rs/otel/src/events/session_telemetry.rs` | error/retry/fallback fields |
| Telemetry failure independence | `codex-rs/state/src/telemetry.rs` | DB behavior independent of delivery |
| 测试仓库规则 | `AGENTS.md` | integration harness、remote executors、timeouts、snapshots |

## 第六阶段依赖管理与软件供应链安全

| 主题 | 文件 | 符号/入口 |
|---|---|---|
| Rust workspace 依赖 | `codex-rs/Cargo.toml` | `[workspace.dependencies]`、Git `rev`、`[patch.crates-io]` |
| Rust 精确依赖图 | `codex-rs/Cargo.lock` | registry source/checksum、Git revision、传递关系 |
| Rust 依赖政策 | `codex-rs/deny.toml` | advisories、licenses、bans、sources |
| 依赖 policy CI | `.github/workflows/cargo-deny.yml` | 固定 SHA action、`cargo-deny` gate |
| 阻塞 CI 汇总 | `.github/workflows/blocking-ci.yml` | `cargo-deny` required result |
| Node 顶层策略 | `package.json` | pnpm 版本、resolutions、overrides、engine |
| pnpm 供应链策略 | `pnpm-workspace.yaml` | minimum release age、strict builds、allowBuilds |
| Node 精确依赖图 | `pnpm-lock.yaml` | importer、resolution、integrity、Git tarball revision |
| Frozen npm 安装 | `.github/workflows/repo-checks.yml` | `pnpm install --frozen-lockfile` |
| Frozen Python 安装 | `.github/workflows/sdk.yml` | `uv sync --frozen`、`--no-sync` |
| Bazel 锁文件 | `MODULE.bazel.lock` | Bazel module/repository resolution |
| Bazel lock recipes | `justfile` | `bazel-lock-update`、`bazel-lock-check` |
| 依赖变更仓库规则 | `AGENTS.md` | Rust dependency 更新必须同步 Bazel lock |
| 自动依赖升级 | `.github/dependabot.yaml` | Cargo、Actions、Docker、toolchain weekly + cooldown |
| CI Action 固定 | `.github/workflows/*.yml` | `uses: owner/action@<full commit SHA> # version` |
| 敏感路径 owner | `.github/CODEOWNERS` | release/signing workflow review routing |
| V8 下载校验 | `.github/actions/setup-rusty-v8/action.yml` | SHA-256 manifest、跨平台 verification |
| Release checksum | `.github/workflows/rust-release.yml` | `codex-package_SHA256SUMS` |
| Linux 签名 | `.github/actions/linux-code-sign/action.yml` | Cosign blob signing、Sigstore OIDC bundle |
| Windows 签名 | `.github/actions/windows-code-sign/action.yml` | Azure Trusted Signing、OIDC login |
| macOS 签名/公证 | `.github/workflows/rust-release.yml`、`.github/scripts/macos-signing/` | Key Vault signing、verification、notarization |
| npm 可信发布 | `.github/workflows/rust-release.yml` | `id-token: write`、OIDC、无 `NODE_AUTH_TOKEN` |
| Checkout 凭据边界 | `.github/workflows/*.yml` | `persist-credentials: false` |
| Installer 完整性测试 | `scripts/install/test_install_sh.py` | wrong/missing/corrupt checksum cases |

## 第六阶段跨平台工程与远程执行测试

| 主题 | 文件 | 符号/入口 |
|---|---|---|
| 测试环境模型 | `codex-rs/core/tests/common/test_environment.rs` | `TestEnvironment`、`TestTargetOs`、host/target/remote mapping |
| 测试环境解析 | `codex-rs/core/tests/common/test_environment_tests.rs` | local/docker/wine、invalid/stale configuration |
| Core auto-env | `codex-rs/core/tests/common/test_codex.rs` | `TestEnv`、`test_env`、`build_with_auto_env` |
| 跨平台 skip 宏 | `codex-rs/core/tests/common/lib.rs` | remote/no-remote/Wine/target-Windows/host-Windows |
| App-server auto-env | `codex-rs/app-server/tests/common/test_app_server.rs` | builder auto environment、`send_thread_start_request_with_auto_env` |
| Auto-env 回归测试 | `codex-rs/app-server/tests/suite/v2/auto_env.rs` | target cwd model context、explicit config conflict |
| Turn environment API | `codex-rs/app-server-protocol/src/protocol/v2/turn.rs` | `TurnEnvironmentParams` |
| Environment API | `codex-rs/app-server-protocol/src/protocol/v2/environment.rs` | add/info/status、Shell、PathUri cwd |
| Environment registry | `codex-rs/exec-server/src/environment.rs` | `EnvironmentManager`、local/remote/lazy connection |
| Exec-server wire protocol | `codex-rs/exec-server-protocol/src/protocol.rs` | `EnvironmentInfo`、`ShellInfo`、`ExecParams` |
| Host 绝对路径 | `codex-rs/utils/absolute-path/src/lib.rs` | `AbsolutePathBuf`、normalization、canonicalization |
| 跨平台路径 URI | `codex-rs/utils/path-uri/src/lib.rs` | `PathUri`、`PathConvention`、join/parent/equality |
| App path 兼容层 | `codex-rs/utils/path-uri/src/api_path_string.rs` | `LegacyAppPathString`、foreign conversion/rendering |
| Path URI 测试 | `codex-rs/utils/path-uri/src/tests.rs` | POSIX/drive/UNC/non-UTF-8/case semantics |
| Remote filesystem URI 测试 | `codex-rs/exec-server/src/local_file_system_path_uri_tests.rs` | target-native path conversion |
| Shell 检测 | `codex-rs/shell-command/src/shell_detect.rs` | zsh/bash/sh/PowerShell/cmd、platform fallback |
| PowerShell UTF-8 | `codex-rs/shell-command/src/powershell.rs` | output encoding prefix、PowerShell argv parse |
| Executor sandbox 具体化 | `codex-rs/exec-server/src/process_sandbox_tests.rs` | portable sandbox intent → target wrapper |
| 进程树终止 | `codex-rs/exec-server/src/connection.rs` | Unix process group、Windows `taskkill /T /F` |
| 跨平台 symlink | `codex-rs/exec-server/src/local_file_system.rs` | Unix symlink、Windows file/dir symlink |
| Docker remote fixture | `scripts/test-remote-env.sh` | container、exec-server WebSocket、cleanup |
| Wine remote runner | `codex-rs/exec-server/testing/wine_remote_test_runner.rs` | environment injection、isolated Windows exec-server |
| Wine 测试边界 | `codex-rs/core/tests/remote_env_windows/README.md` | Bazel target、x86-64、ConPTY limitation |
| Remote CI setup | `.github/workflows/rust-ci-full-nextest-platform.yml` | Linux Docker auto-env variables |
| 跨平台 CI 策略 | `.github/workflows/README.md` | PR/post-merge、native platform、remote-env coverage |

## 第六阶段：协议演进与向后兼容

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| App-server 生命周期与生成命令 | `codex-rs/app-server/README.md` | initialize、thread/turn、stable/experimental schema |
| JSON-RPC 方法总表 | `codex-rs/app-server-protocol/src/protocol/common.rs` | request/notification macros、method wire name、`inspect_params` |
| v2 optional 与分页 | `codex-rs/app-server-protocol/src/protocol/v2/model.rs` | `ModelListParams`、`ModelListResponse` |
| 实验字段与三态参数 | `codex-rs/app-server-protocol/src/protocol/v2/thread.rs` | `ThreadStartParams`、`ExperimentalApi`、`service_tier` |
| TS/JSON Schema 导出 | `codex-rs/app-server-protocol/src/export.rs` | stable filtering、experimental export、precomputed outputs |
| Schema 漂移测试 | `codex-rs/app-server-protocol/src/schema_fixtures_tests.rs` | generated tree 与 committed fixture 对比 |
| 配置旧键兼容 | `codex-rs/config/src/key_aliases.rs` | `normalize_key_aliases`、legacy/canonical 冲突优先级 |
| 配置 Schema | `codex-rs/core/config.schema.json` | 当前 `ConfigToml` 的机器可读结构 |
| Rollout wire 格式 | `codex-rs/protocol/src/protocol.rs` | `RolloutItem`、`SessionMetaLine`、`TurnContextItem` |
| 旧会话恢复行为 | `codex-rs/app-server/tests/suite/v2/thread_resume.rs` | 通过公共 JSON-RPC 验证 resume |

## 第六阶段：大规模重构与渐进式迁移

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 模块规模与 API 指导 | `AGENTS.md` | 500/800 LoC、Core 膨胀、private module、测试迁移 |
| 精确公开 API 与旧名称 | `codex-rs/core/src/lib.rs` | private `mod`、`pub use`、deprecated aliases |
| 旧调用适配到明确 enum | `codex-rs/core/src/thread_manager.rs` | `ForkSnapshot`、`From<usize>` |
| Handler 分批迁移 | `codex-rs/core/src/tools/context.rs` | `ToolInvocation.turn`、`step_context` compatibility field |
| 单一 turn source of truth | `codex-rs/core/src/tools/router.rs` | 从 `step_context.turn` clone 旧字段 |
| 集中策略与架构测试 | `codex-rs/tui/src/motion.rs` | `MotionMode`、reduced motion、禁止直接 primitive 调用 |
| TUI 中央热点的模块化 | `codex-rs/tui/src/chatwidget.rs`、`codex-rs/tui/src/chatwidget/` | orchestration 与职责子模块 |
| App 中央热点的模块化 | `codex-rs/tui/src/app.rs`、`codex-rs/tui/src/app/` | event routing、session lifecycle、thread state |
| 工具子系统分层 | `codex-rs/core/src/tools/` | router、registry、handlers、runtimes、tests |
| Crate 真实依赖方向 | `codex-rs/*/Cargo.toml` | Core、protocol、app-server、TUI 依赖 |

## 第六阶段：安全删除与技术债治理

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Removed/no-op feature | `codex-rs/features/src/lib.rs` | removed compatibility `Feature`、旧 key 应用 |
| Feature 旧别名 | `codex-rs/features/src/legacy.rs` | alias 到当前 Feature、legacy usage |
| Removed key 防复活测试 | `codex-rs/features/src/tests.rs` | `from_sources_ignores_removed_*` |
| 已停止发送的通知 | `codex-rs/app-server-protocol/src/protocol/v2/item.rs` | `FileChangeOutputDeltaNotification` |
| Deprecated 通知 registry | `codex-rs/app-server-protocol/src/protocol/common.rs` | `item/fileChange/outputDelta` |
| 安全的旧输入处理 | `codex-rs/app-server-protocol/src/protocol/v2/permissions.rs` | ignore fullAccess、reject restricted |
| 旧权限字段测试 | `codex-rs/app-server-protocol/src/protocol/v2/tests.rs` | 等价兼容与 fail-closed 拒绝 |
| Removed config Schema | `codex-rs/config/src/schema.rs` | `removed_apps_mcp_path_override_schema` |
| Config lock 规范语义 | `codex-rs/core/src/session/config_lock.rs` | removed input 不产生 lock drift |
| 生成物过期清理 | `codex-rs/app-server-protocol/src/schema_fixtures.rs` | `ensure_empty_dir` 后重新生成 |
| 条件 dead code | `codex-rs/tui/src/app_event.rs` | Windows/platform `cfg_attr` allowance |

## 第六阶段：幂等性与分布式状态一致性

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| JSON-RPC 请求关联 | `codex-rs/app-server-protocol/src/protocol/common.rs` | `RequestId` 进入 request/response |
| 连接内请求身份 | `codex-rs/app-server/src/message_processor.rs` | `ConnectionRequestId`、请求路由 |
| 正常完成与取消竞态 | `codex-rs/core/src/tools/parallel.rs` | `terminal_outcome_reached` |
| 唯一终态通知 | `codex-rs/core/src/tools/registry.rs` | `notify_tool_finish_if_unclaimed`、atomic swap |
| Refresh 合并与取消恢复 | `codex-rs/core/src/session/mcp_refresh.rs` | `pending`、Semaphore、invalidation guard |
| Listener 世代与串行命令 | `codex-rs/app-server/src/thread_state.rs` | `listener_generation`、`ThreadListenerCommand` |
| Resume/订阅原子边界 | `codex-rs/app-server/src/request_processors/thread_lifecycle.rs` | listener command、generation cleanup |
| 状态通知去重 | `codex-rs/app-server/src/thread_status.rs` | previous/current、`send_if_modified` |
| 配置乐观并发 | `codex-rs/app-server/src/config_manager_service.rs` | `expected_version`、version conflict |
| 幂等物化 | `codex-rs/rollout/src/recorder.rs` | `RolloutRecorder::persist` |
| 持久顺序 | `codex-rs/rollout/src/ordinal.rs` | next ordinal、gap、prefix、overflow |
| 数据库原子初始化 | `codex-rs/state/src/runtime.rs` | `ON CONFLICT(id) DO NOTHING` |
| UI Item 去重 | `codex-rs/tui/src/app/agent_status_feed.rs` | `seen_item_ids` |
| 投影对账和读取修复 | `codex-rs/rollout/src/state_db.rs` | backfill、reconcile、read repair |

## 第六阶段：资源生命周期、RAII 与泄漏防护

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 取消 future 的实际保证 | `codex-rs/async-utils/src/lib.rs` | `OrCancelExt`、`CancelErr` |
| Session teardown 顺序 | `codex-rs/core/src/session/handlers.rs` | `shutdown_session_runtime` |
| 请求关闭与等待终态 | `codex-rs/core/src/session/mod.rs` | `CodexThread::shutdown_and_wait` |
| Session 清理测试 | `codex-rs/core/src/session/tests.rs` | channel close、并发 waiter、TERM trap |
| 关闭准入并排空 task | `codex-rs/app-server/src/connection_rpc_gate.rs` | `ConnectionRpcGate`、`TaskTracker` |
| Refresh 关闭与失败回滚 | `codex-rs/core/src/session/mcp_refresh.rs` | Semaphore、`close`、invalidation guard |
| Permit 和 cleanup 容量 | `codex-rs/exec-server/src/rpc.rs` | `RpcInboundRequestGuard`、`call_for_cleanup` |
| 进程启动责任交接 | `codex-rs/exec-server/src/client.rs` | `PendingProcessStartSession`、接收确认 |
| Drop 中的远端注销 | `codex-rs/exec-server/src/remote_process.rs` | `RemoteExecProcess::drop` |
| 远端文件流关闭 | `codex-rs/exec-server/src/remote_file_stream.rs` | `FileReadRegistration` |
| HTTP 流各出口的 route 清理 | `codex-rs/exec-server/src/client/http_response_body_stream.rs` | registration、`disarm`、EOF cleanup |
| PTY 和进程树回收 | `codex-rs/utils/pty/src/process.rs` | `request_terminate`、`terminate`、Drop |
| Worker 不延长 owner 生命周期 | `codex-rs/app-server/src/models_refresh_worker.rs` | token、JoinHandle、`Weak<ModelsManager>` |
| 管理 cleanup task 本身 | `codex-rs/app-server/src/connection_cleanup.rs` | `ConnectionCleanupTasks`、`JoinSet` |
| 终端多步骤恢复 | `codex-rs/tui/src/tui.rs` | `restore_common`、`restore_after_exit` |
| TUI 子资源 owner | `codex-rs/tui/src/app.rs`、`codex-rs/tui/src/chatwidget.rs` | terminal title、rate-limit poller 的 Drop |

## 第六阶段：缓存设计、失效策略与一致性

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 模型磁盘缓存契约 | `codex-rs/models-manager/src/cache.rs` | `ModelsCache`、TTL、client version、ETag |
| 模型缓存读取与远端回退 | `codex-rs/models-manager/src/manager.rs` | `try_load_cache`、`fetch_and_update_models`、`refresh_if_new_etag` |
| 模型缓存行为测试 | `codex-rs/models-manager/src/manager_tests.rs` | fresh/stale、读写错误、TTL revalidation |
| 周期性模型刷新 | `codex-rs/app-server/src/models_refresh_worker.rs` | online refresh、interval、Weak owner、cancellation |
| Connector 身份隔离 | `codex-rs/core/src/connectors.rs` | `AccessibleConnectorsCacheKey`、`expires_at` |
| Connector key 测试 | `codex-rs/core/src/connectors_tests.rs` | 账号与配置维度、缓存命中 |
| Plugin metadata 世代栅栏 | `codex-rs/core-plugins/src/tool_suggest_metadata.rs` | generation、二次检查、1024 entry 上限 |
| Plugin 缓存清理 | `codex-rs/core-plugins/src/manager.rs` | loaded cache、recommended cache、OnceCell refreshes |
| Plugin clear 行为测试 | `codex-rs/core-plugins/src/discoverable_tests.rs` | manifest 改变前后与 `clear_cache` |
| 远端 Plugin 磁盘缓存 | `codex-rs/core-plugins/src/remote/catalog_cache.rs` | tenant/scope key、schema version、3 小时 TTL |
| Stale Plugin Catalog 刷新 | `codex-rs/core-plugins/src/remote.rs` | `PreferCache`、`cache_refresh_needed`、scope invalidation |
| Plugin Catalog 缓存测试 | `codex-rs/core-plugins/src/remote/catalog_cache_tests.rs` | stale、schema、身份 scope |
| Auth token 刷新合并 | `codex-rs/login/src/auth/external_bearer.rs` | mutex 内检查、provider command、refresh interval |
| Git status 在途去重 | `codex-rs/git-utils/src/status.rs` | `WeakShared`、per-repository key、只共享未完成 future |
| Windows setup singleflight | `codex-rs/windows-sandbox-rs/src/setup.rs` | `SETUP_FLIGHTS`、leader/waiter、`Arc::ptr_eq` |
| Prompt cache key | `codex-rs/core/src/client.rs` | session key、override、增量请求属性比较 |
| Guardian Prompt cache 作用域 | `codex-rs/core/src/guardian/review_session.rs` | parent thread 派生的 override |

## 第六阶段：数据库事务、隔离级别与崩溃一致性

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| SQLite 统一连接配置 | `codex-rs/state/src/sqlite.rs` | WAL、NORMAL、incremental vacuum、5 秒 busy timeout、pool 大小 |
| 多数据库初始化与关闭 | `codex-rs/state/src/runtime.rs` | 六个 runtime DB、分阶段 open/migrate、pool cleanup |
| SQLite 版本与公开入口 | `codex-rs/state/src/lib.rs` | WAL 修复版本断言、DB metric、queue 上限 |
| Thread History 原子投影 | `codex-rs/thread-store/src/local/thread_history.rs` | `BEGIN IMMEDIATE`、projection rows 与 offset/ordinal 同事务 |
| 投影失败恢复测试 | `codex-rs/thread-store/src/local/thread_history_materialization_tests.rs` | 模拟 projection failure 后从 Rollout 追平 |
| Queue 原子容量与重排 | `codex-rs/state/src/runtime/queued_items.rs` | 单 SQL enqueue、UNIQUE order、transaction rollback |
| Queue 并发测试 | `codex-rs/state/src/runtime/queued_items_tests.rs` | 两个 runtime 争最后容量、跨 thread 隔离 |
| Memory job 原子 claim | `codex-rs/state/src/runtime/memories.rs` | lease、ownership token、retry、`BEGIN IMMEDIATE`、affected rows |
| Backfill singleton claim | `codex-rs/state/src/runtime/backfill.rs` | 条件 UPDATE、lease cutoff、watermark |
| Goal snapshot 多表提交 | `codex-rs/state/src/runtime/goals.rs` | goal upsert 与 continuation deferral 同事务 |
| Section 并发排序 | `codex-rs/state/src/runtime/thread_section_order.rs` | writer slot、position 计算、事务更新 |
| Section 并发测试 | `codex-rs/state/src/runtime/thread_section_order_tests.rs` | 并发 move 后 position 仍唯一有序 |
| Migration runner | `codex-rs/state/src/migrations.rs` | 多库 migrator、ignore newer versions、known checksum 验证 |
| Migration SQL | `codex-rs/state/migrations/` | table rebuild、trigger、index、compatibility bridge |
| Migration 并发与修复测试 | `codex-rs/state/src/migrations_tests.rs` | legacy recency version repair、writer slot 场景 |
| WAL checkpoint | `codex-rs/state/src/runtime/logs.rs` | startup maintenance、`wal_checkpoint(PASSIVE)` |
| 损坏分类与单库备份 | `codex-rs/state/src/runtime/recovery.rs` | corruption/lock 分流、主库及 `-wal`/`-shm` sidecar |
| 恢复范围测试 | `codex-rs/state/src/runtime/recovery_tests.rs` | 只移动目标 DB、保留其他 runtime DB |
| App-server 自动恢复 | `codex-rs/app-server/src/lib.rs` | 识别失败 DB、备份、fresh start、用户 notice |
| CLI 恢复提示 | `codex-rs/cli/src/state_db_recovery.rs` | locked guidance、corruption backup、可恢复输出 |

## 第六阶段：认证凭据、密钥与敏感数据生命周期

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 持久认证数据形状 | `codex-rs/login/src/auth/storage.rs` | `AuthDotJson`、`AgentIdentityStorage`、敏感字段 |
| 文件存储权限 | `codex-rs/login/src/auth/storage.rs` | `FileAuthStorage`、`auth.json`、Unix `0o600` |
| Keyring 与 Secrets 存储 | `codex-rs/login/src/auth/storage.rs` | `DirectKeyringAuthStorage`、`SecretsKeyringAuthStorage`、store key |
| Auto 与 Ephemeral 策略 | `codex-rs/login/src/auth/storage.rs` | fallback、进程内 map、load/save/delete |
| Keyring 后端 feature 选择 | `codex-rs/core/src/config/auth_keyring.rs` | `SecretAuthStorage`、Direct/Secrets |
| 认证来源优先级 | `codex-rs/login/src/auth/manager.rs` | `load_auth`、env、ephemeral、persistent store |
| 环境变量入口 | `codex-rs/login/src/auth/manager.rs` | `OPENAI_API_KEY`、`CODEX_API_KEY`、`CODEX_ACCESS_TOKEN` |
| stdin 登录 | `codex-rs/cli/src/login.rs` | `read_api_key_from_stdin`、`read_access_token_from_stdin` |
| OAuth callback 与 token 交换 | `codex-rs/login/src/server.rs` | authorization code、PKCE、`exchange_code_for_tokens` |
| URL 和错误脱敏 | `codex-rs/login/src/server.rs` | `redact_sensitive_url_parts`、敏感 query keys |
| Auth 类型与 bearer 提取 | `codex-rs/login/src/auth/manager.rs` | `CodexAuth`、`get_token`、auth mode |
| 自定义 Header Debug | `codex-rs/login/src/auth/auth_headers.rs` | `AuthHeaders`、`<redacted>` |
| 主动刷新与 401 恢复 | `codex-rs/login/src/auth/manager.rs` | `should_refresh_proactively`、`UnauthorizedRecovery` |
| 刷新 singleflight 与身份栅栏 | `codex-rs/login/src/auth/manager.rs` | `refresh_lock`、guarded reload、account match |
| 新 token 持久化 | `codex-rs/login/src/auth/manager.rs` | `persist_tokens`、refresh-token replacement、reload |
| 外部 bearer provider | `codex-rs/login/src/auth/external_bearer.rs` | provider command、stdout、cache、refresh interval |
| Logout 与远端撤销 | `codex-rs/login/src/auth/revoke.rs` | refresh-token first、timeout、best-effort revoke |
| 认证环境 Telemetry | `codex-rs/login/src/auth_env_telemetry.rs` | presence-only metadata、provider name bucketing |
| Doctor 输出脱敏 | `codex-rs/cli/src/doctor/output.rs` | `redact_detail`、URL 收缩、secret absence tests |
| MCP 环境变量间接引用 | `codex-rs/config/src/mcp_types.rs` | `bearer_token_env_var`、`env_http_headers` |
| MCP Header 构造 | `codex-rs/rmcp-client/src/utils.rs` | `build_default_headers`、运行时 env 解析 |
| Executor 侧 secret 解析 | `codex-rs/codex-mcp/src/plugin_config.rs` | executor-owned HTTP MCP 限制 |
| 登录/刷新/撤销集成测试 | `codex-rs/cli/tests/login.rs` | stdin API key、刷新持久化、revoke request |
| 存储后端测试 | `codex-rs/login/src/auth/storage_tests.rs` | file/keyring/auto/ephemeral 行为与清理 |

## 第六阶段：时间、时钟、超时与分布式时间语义

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Core 时间抽象 | `codex-rs/core/src/current_time.rs` | `TimeProvider`、System/External、current time 与 sleep |
| App-server 外部时钟 | `codex-rs/app-server/src/current_time.rs` | 唯一 subscriber、共享 10 秒 deadline、轮询 external sleep |
| 当前时间提醒状态 | `codex-rs/core/src/session/time_reminder.rs` | interval、window、user/tool boundary、回拨比较 |
| Current-time context fragment | `codex-rs/core/src/context/current_time_reminder.rs` | UTC 格式、developer fragment |
| Current-time 工具 | `codex-rs/core/src/tools/handlers/current_time.rs` | `clock.curr_time`、TimeProvider 调用 |
| 可中断 Sleep 工具 | `codex-rs/core/src/tools/handlers/sleep.rs` | 1ms–12h、input activity、elapsed output |
| Durable sleep 调度 | `codex-rs/core/src/tasks/mod.rs` | extension item、pending mailbox 唤醒 |
| 时间 Provider 集成测试 | `codex-rs/core/tests/suite/current_time_reminder.rs` | 固定/递增/回拨/失败时间源 |
| Turn 性能计时 | `codex-rs/core/src/turn_timing.rs` | Instant 耗时、Unix timestamp、TTFT/TTFM、phase profile |
| Turn 时间测试 | `codex-rs/core/src/turn_timing_tests.rs` | before/after wall-time 区间、首次事件语义 |
| Exec 到期原因 | `codex-rs/core/src/exec.rs` | `ExecExpiration`、timeout/cancellation、pipe drain timeout |
| Guardian 总 Deadline | `codex-rs/core/src/guardian/review.rs` | 整体 deadline、retry backoff、cancel race |
| Guardian Deadline 消费 | `codex-rs/core/src/guardian/review_session.rs` | `sleep_until`、interrupt 与 drain timeout |
| Responses 重试 | `codex-rs/core/src/responses_retry.rs` | retry count、server delay、backoff、transport fallback |
| 指数退避与 jitter | `codex-rs/core/src/util.rs` | `backoff`、0.9–1.1 jitter |
| SSE Retry-After 解析 | `codex-rs/codex-api/src/sse/responses.rs` | rate-limit message、seconds/milliseconds delay |
| WebSocket connect timeout | `codex-rs/core/src/client.rs` | 连接阶段 timeout 与 telemetry |
| WebSocket idle timeout | `codex-rs/codex-api/src/endpoint/responses_websocket.rs` | 等待下一 stream item 的空闲上限 |
| MCP timeout 配置解析 | `codex-rs/config/src/mcp_types.rs` | startup sec/ms 优先级、tool timeout |
| MCP runtime 默认值 | `codex-rs/codex-mcp/src/rmcp_client.rs` | startup/tool 默认 Duration、启动 timeout |
| Optional MCP grace | `codex-rs/codex-mcp/src/connection_manager/tool_catalog.rs` | 共享 startup deadline、缓存工具快速路径 |
| MCP retry deadline | `codex-rs/rmcp-client/src/streamable_http_retry.rs` | remaining、受 deadline 限制的 sleep |
| 进程内工具缓存 TTL | `codex-rs/codex-mcp/src/tool_catalog_cache.rs` | Tokio Instant、30 分钟 TTL、generation |
| Detached session TTL | `codex-rs/exec-server/src/server/session_registry.rs` | 30 秒 resume window、connection identity fencing |
| 模型磁盘缓存时间 | `codex-rs/models-manager/src/cache.rs` | UTC fetched_at、TTL refresh、future timestamp 行为 |
| Plugin 磁盘缓存时间 | `codex-rs/core-plugins/src/remote/catalog_cache.rs` | 3 小时 TTL、负 age 拒绝 |
| Cloud config expiry | `codex-rs/cloud-config/src/cache.rs` | signed cached_at/expires_at、1 小时 TTL |
| MCP OAuth expiry | `codex-rs/rmcp-client/src/oauth.rs` | expires_in→epoch millis、refresh skew、checked add |
| MCP OAuth 刷新事务 | `codex-rs/rmcp-client/src/oauth/refresh_transaction.rs` | 45 秒 provider timeout、取消后 owned task |
| HTTP Retry-After | `codex-rs/app-server-transport/src/transport/remote_control/server_api.rs` | delta seconds、HTTP date、绝对 retry_at |
| Retry-After 测试 | `codex-rs/app-server-transport/src/transport/remote_control/server_api_tests.rs` | body-read delay、invalid/past fallback |
| Memory job lease | `codex-rs/state/src/runtime/memories.rs` | Unix seconds、lease_until、retry_at、ownership token |
| Backfill lease | `codex-rs/state/src/runtime/backfill.rs` | updated_at cutoff、跨 runtime claim |
| Rate-limit reset | `codex-rs/codex-api/src/rate_limits.rs` | window minutes、resets_at Unix seconds |
| Rate-limit UI staleness | `codex-rs/tui/src/status/rate_limits.rs` | captured_at、Local 展示、stale threshold |
| Code Mode yield | `codex-rs/code-mode-runtime/src/service.rs` | yield grace、session clamp、yield 不终止 cell |
| 可控时间测试 | `codex-rs/code-mode-runtime/src/service_tests.rs` | `start_paused`、`advance`、边界前后断言 |

## 第六阶段：文件系统原子性、路径、临时文件与跨平台持久化

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 绝对路径类型 | `codex-rs/utils/absolute-path/src/lib.rs` | `AbsolutePathBuf`、绝对/词法规范化保证、非 UTF-8 路径 |
| `.`/`..` 词法处理 | `codex-rs/utils/absolute-path/src/absolutize.rs` | `Component`、base resolution、Windows prefix |
| 保留逻辑 symlink 路径 | `codex-rs/utils/absolute-path/src/lib.rs` | `canonicalize_preserving_symlinks`、existing variant |
| 路径比较规范化 | `codex-rs/utils/path-utils/src/lib.rs` | `normalize_for_path_comparison`、fallback equality |
| WSL/Windows workdir | `codex-rs/utils/path-utils/src/lib.rs` | `/mnt/<drive>` ASCII lowercase、`dunce::simplified` |
| Symlink-aware 写目标 | `codex-rs/utils/path-utils/src/lib.rs` | `resolve_symlink_write_paths`、relative target、visited cycle |
| 公共原子写 helper | `codex-rs/utils/path-utils/src/lib.rs` | 同目录 `NamedTempFile`、`write_all`、`persist` |
| Config read-edit-write | `codex-rs/core/src/config/edit.rs` | `ConfigDocument`、decor 保留、批量 edit、原子发布 |
| Plugin config 持久化 | `codex-rs/config/src/plugin_edit.rs` | symlink target、TOML edit、blocking task |
| Connector cache 原子替换 | `codex-rs/connectors/src/connector_runtime/persistence.rs` | bounded read、同目录 temp、schema、persist |
| Cache 竞态大小上限 | `codex-rs/connectors/src/connector_runtime/persistence.rs` | metadata length + `take(MAX+1)` 二次检查 |
| 消息历史 append | `codex-rs/message-history/src/lib.rs` | JSONL、`O_APPEND`、single-line preparation |
| 历史跨进程锁 | `codex-rs/message-history/src/lib.rs` | `try_lock`、`try_lock_shared`、有界 retry |
| 历史权限与平台差异 | `codex-rs/message-history/src/lib.rs` | Unix mode `0o600`、Windows cursor-to-end |
| 历史裁剪临界区 | `codex-rs/message-history/src/lib.rs` | append、flush、soft cap rewrite 均在 exclusive lock 内 |
| Rollout 路径 scope | `codex-rs/thread-store/src/local/helpers.rs` | canonical root、`starts_with`、thread ID filename suffix |
| Delete 路径防护 | `codex-rs/thread-store/src/local/delete_thread.rs` | active/archive scope、缺失幂等、文件名复核 |
| Archive 原子搬移 | `codex-rs/thread-store/src/local/archive_thread.rs` | lifecycle/writer lock、`std::fs::rename` |
| Unarchive 搬移 | `codex-rs/thread-store/src/local/unarchive_thread.rs` | 日期目标目录、rename、mtime 更新 |
| Rollout migration 顺序 | `codex-rs/thread-store/src/local/rollout_migration.rs` | journal、staging、projection、conflict check、publish |
| Durable migration helper | `codex-rs/thread-store/src/local/rollout_migration/publish.rs` | staged path、`sync_all`、parent-directory sync |
| `.pending` 恢复标记 | `codex-rs/thread-store/src/local/rollout_migration/publish.rs` | create/sync journal、完成后 remove/sync |
| 发布前 optimistic check | `codex-rs/thread-store/src/local/rollout_migration.rs` | source length + modified time 再验证 |
| PID 名字预留 | `codex-rs/app-server-daemon/src/backend/pid.rs` | `create_new(true)`、`AlreadyExists`、reservation lock |
| PID 临时发布 | `codex-rs/app-server-daemon/src/backend/pid.rs` | `.pid.tmp`、rename、失败时终止 child/清理 |
| Unix daemon flock | `codex-rs/app-server-daemon/src/backend/pid.rs` | `LOCK_EX | LOCK_NB`、deadline、非 Unix 不支持 |
| 进程内 cache mutex | `codex-rs/core-plugins/src/remote/share/local_paths.rs` | `Mutex<()>` 作用域、同目录 temp replace |
| Unix/Windows symlink | `codex-rs/git-utils/src/platform.rs` | Unix 通用 symlink、Windows file/dir 分支 |
| Patch path 与 cwd | `codex-rs/apply-patch/src/parser.rs`、`codex-rs/apply-patch/src/invocation.rs` | 相对路径解析、effective cwd、foreign path |
| Sandbox 文件边界 | `codex-rs/core/src/tools/handlers/apply_patch.rs` | workspace roots、write permission profile、审批边界 |

## 第六阶段：目录遍历、文件搜索、忽略规则与变化检测

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 搜索结果形状 | `codex-rs/file-search/src/lib.rs` | `FileMatch`、root/relative path、file/directory、indices |
| 搜索选项 | `codex-rs/file-search/src/lib.rs` | limit、exclude、threads、compute_indices、respect_gitignore |
| Session 建立 | `codex-rs/file-search/src/lib.rs` | `create_session`、Nucleo、Injector、两个 worker thread |
| 多 root 相对路径 | `codex-rs/file-search/src/lib.rs` | `get_file_path`、最深 root、UTF-8 边界 |
| Ignore override | `codex-rs/file-search/src/lib.rs` | `OverrideBuilder`、显式 exclude glob |
| 目录遍历策略 | `codex-rs/file-search/src/lib.rs` | `walker_worker`、hidden(false)、follow_links、threads |
| Git ignore 作用域 | `codex-rs/file-search/src/lib.rs` | `require_git(true)`、关闭全部 ignore processing |
| #3493 回归 | `codex-rs/file-search/src/lib.rs` | parent `.gitignore`、non-repo 与 local repository tests |
| 增量模糊匹配 | `codex-rs/file-search/src/lib.rs` | `matcher_worker`、reparse append、tick、Nucleo snapshot |
| 流式结果 | `codex-rs/file-search/src/lib.rs` | `scanned_file_count`、`walk_complete`、on_update/on_complete |
| Cancel 与 Drop | `codex-rs/file-search/src/lib.rs` | 1024-entry 检查、shutdown flag、shared cancel test |
| 搜索 CLI | `codex-rs/file-search/src/cli.rs` | 2 threads 经验值、64 limit、exclude、cwd |
| TUI `@` session | `codex-rs/tui/src/file_search.rs` | query reuse、CWD change、session_token stale fencing |
| App-server fuzzy search | `codex-rs/app-server/src/fuzzy_file_search.rs` | 50 limit、12 threads cap、indices、latest-query fencing |
| Search RPC owner | `codex-rs/app-server/src/request_processors/search.rs` | cancellation token、session start/update/stop、Arc::ptr_eq |
| Search protocol | `codex-rs/app-server-protocol/src/protocol/common.rs` | FuzzyFileSearch result、session notification types |
| Watch event channel | `codex-rs/file-watcher/src/lib.rs` | BTreeSet dedupe、sender count、receiver shutdown |
| Throttle/debounce | `codex-rs/file-watcher/src/lib.rs` | `ThrottledWatchReceiver`、`DebouncedWatchReceiver` |
| Multi-subscriber watch | `codex-rs/file-watcher/src/lib.rs` | subscriber state、path ref counts、effective recursive mode |
| RAII watch cleanup | `codex-rs/file-watcher/src/lib.rs` | `WatchRegistration`、subscriber Drop、unwatch reconfigure |
| OS event bridge | `codex-rs/file-watcher/src/lib.rs` | RecommendedWatcher、Tokio event loop、mutating-event filter |
| Missing target watch | `codex-rs/file-watcher/src/lib.rs` | nearest existing ancestor、fallback、actual watch move |
| Path namespace mapping | `codex-rs/file-watcher/src/lib.rs` | requested/matched/actual、canonical backend event |
| Watcher tests | `codex-rs/file-watcher/src/file_watcher_tests.rs` | dedupe、scope、missing target、shutdown flush、event filter |
| App-server fs/watch | `codex-rs/app-server/src/fs_watch.rs` | connection-owned ID、200ms debounce、unwatch completion barrier |
| Skills cache watcher | `codex-rs/app-server/src/skills_watcher.rs` | recursive roots、10s throttle、clear_cache、SkillsChanged |
| Git fsmonitor policy | `codex-rs/git-utils/src/fsmonitor.rs` | configured helper 禁用、boolean parsing、daemon capability |
| Git command override | `codex-rs/git-utils/src/info.rs` | disabled hooks path、safe fsmonitor config、bounded probe |

## 第六阶段：文件读取、文本编码、二进制识别与有界内容加载

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 整文件 byte 上限 | `codex-rs/exec-server/src/local_file_system.rs` | `MAX_READ_FILE_BYTES`、metadata、`take(MAX+1)`、post-read check |
| 文件 chunk contract | `codex-rs/file-system/src/lib.rs` | `FILE_READ_CHUNK_SIZE`、byte-oriented file API |
| 流式文件读取 | `codex-rs/exec-server/src/local_file_system.rs` | `read_file_stream`、`ReaderStream::with_capacity` |
| 随机 block 读取 | `codex-rs/exec-server/src/file_read.rs` | handle map、1 MiB block、offset、`read_at`/`seek_read` |
| 文件读取资源上限 | `codex-rs/exec-server/src/file_read.rs` | 128 handles、block length validation、error close |
| 远程文件流 | `codex-rs/exec-server/src/remote_file_stream.rs` | offset 推进、chunk 复核、EOF/Drop close |
| Byte wire format | `codex-rs/exec-server/src/server/file_system_handler.rs` | `fs/readFile`、base64、open/readBlock/close |
| Connector cache 有界读取 | `codex-rs/connectors/src/connector_runtime/persistence.rs` | 32 MiB、metadata 与读取期增长二次检查 |
| History JSONL | `codex-rs/message-history/src/lib.rs` | BufReader、lines、newline byte count、完整行裁剪 |
| Rollout 单行上限 | `codex-rs/thread-store/src/local/rollout_migration.rs` | 16 MiB、`read_until`、超大行 discard/resync |
| Stderr tail | `codex-rs/app-server-daemon/src/backend/pid.rs` | 4096 bytes、seek、残缺首行、UTF-8 lossy |
| Shell 输出编码 | `codex-rs/protocol/src/exec_output.rs` | UTF-8 fast path、chardetng、encoding_rs、fallback |
| 传统编码歧义 | `codex-rs/protocol/src/exec_output.rs` | Windows-1252 punctuation 与 IBM866 heuristic |
| Unified exec 总缓冲 | `codex-rs/core/src/unified_exec/head_tail_buffer.rs` | 1 MiB、head/tail、omitted bytes、marker |
| Unified exec delta | `codex-rs/core/src/unified_exec/async_watcher.rs` | 8192-byte event 上限、合法 UTF-8 prefix 优先、非法输入至少推进 1 byte |
| 文本中间截断 | `codex-rs/utils/string/src/truncate.rs` | `char_indices`、prefix/suffix、token 近似 |
| UTF-8 安全 prefix | `codex-rs/utils/string/src/lib.rs` | `take_bytes_at_char_boundary` |
| Skill 模型内容上限 | `codex-rs/core-skills/src/lib.rs`、`codex-rs/core-skills/src/injection.rs` | 8000 bytes、truncated flag |
| zstd 流式压缩 | `codex-rs/thread-store/src/local/rollout_migration/publish.rs` | decoder/encoder、`io::copy`、finish、sync |
| 压缩制品验证 | `codex-rs/thread-store/src/local/rollout_migration/publish.rs` | reopen、decode、copy to `io::sink` |

## 第六阶段：进程标准输入输出、Pipe、PTY、实时输出与背压

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 启动后端选择 | `codex-rs/sandboxing/src/spawn.rs` | `tty`、`stdin_open`、PTY/pipe/null stdin |
| Pipe stdio 接线 | `codex-rs/utils/pty/src/pipe.rs` | `Stdio::piped`、`Stdio::null`、split stdout/stderr |
| Pipe reader | `codex-rs/utils/pty/src/pipe.rs` | 8192-byte buffer、Interrupted retry、并发 drain |
| Pipe/PTY stdin queue | `codex-rs/utils/pty/src/pipe.rs`、`codex-rs/utils/pty/src/pty.rs` | 128-slot mpsc、single writer、write_all/flush |
| PTY master/slave | `codex-rs/utils/pty/src/pty.rs` | openpty、三条 stdio 接 slave、master read/write |
| PTY resize | `codex-rs/utils/pty/src/process.rs`、`codex-rs/utils/pty/src/pty.rs` | `TerminalSize`、portable resize、`TIOCSWINSZ` |
| Windows 输入归一化 | `codex-rs/utils/pty/src/windows_input.rs` | LF→CR、CRLF 跨 chunk、backspace→DEL |
| 公共进程 owner | `codex-rs/utils/pty/src/process.rs` | `ProcessHandle`、tasks、handles、Drop termination |
| 请求终止与强制清理 | `codex-rs/utils/pty/src/process.rs` | `request_terminate`、`terminate`、reader drain |
| 合并 split streams | `codex-rs/utils/pty/src/process.rs` | `combine_output_receivers`、broadcast 256、Lagged |
| PTY/pipe 行为测试 | `codex-rs/utils/pty/src/tests.rs` | REPL、stdin、stderr drain、late tail、resize、process group |
| Unified process wrapper | `codex-rs/core/src/unified_exec/process.rs` | local/exec-server handle、state、output task、reconcile |
| Unified 最终缓冲 | `codex-rs/core/src/unified_exec/head_tail_buffer.rs` | 1 MiB head/tail、omission marker |
| 实时 output watcher | `codex-rs/core/src/unified_exec/async_watcher.rs` | UTF-8 prefix、8192-byte delta、10,000-event cap |
| Exit 后实时排空 | `codex-rs/core/src/unified_exec/async_watcher.rs` | output closed 优先、100 ms grace fallback |
| stdin 与 poll | `codex-rs/core/src/unified_exec/process_manager.rs` | `write_stdin`、空 input poll、Ctrl-C、yield bounds |
| 同进程交互串行化 | `codex-rs/core/src/unified_exec/process_manager.rs` | `interaction_lock`、draining buffer、terminal event |
| Poll 的 exit 后排空 | `codex-rs/core/src/unified_exec/process_manager.rs` | 50 ms close wait、Notify/atomic、pause-aware deadline |
| 远程进程协议 | `codex-rs/exec-server-protocol/src/protocol.rs` | process ID、stream、seq、read/write/status/closed |
| 远程输出 retention | `codex-rs/exec-server/src/local_process.rs` | 1 MiB、50,000 chunks、VecDeque eviction |
| Exited/Closed barrier | `codex-rs/exec-server/src/local_process.rs` | `open_streams`、exit code、late output、30s retention |
| 远程 stdin 幂等 | `codex-rs/exec-server/src/local_process.rs` | `write_id`、4096 IDs、reserve/send/remember |
| 远程事件重排 | `codex-rs/exec-server/src/client.rs` | ordered pending buffer、sequence gap、failure |

## 第六阶段：Shell、argv、引号转义、环境变量与工作目录

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Shell argv 构造 | `codex-rs/core/src/shell.rs` | Bash/Zsh/sh `-c/-lc`、PowerShell `-NoProfile/-Command`、cmd `/c` |
| 默认 shell 探测 | `codex-rs/shell-command/src/shell_detect.rs` | passwd shell、which、平台 fallback、shell type |
| shell_command 参数 | `codex-rs/core/src/tools/handlers/shell/shell_command.rs` | command、workdir、login、env、ExecParams |
| Unified command 解析 | `codex-rs/core/src/tools/handlers/unified_exec.rs` | `ExecCommandArgs`、Direct/ZshFork、`derive_exec_args` |
| Environment 选择 | `codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs` | 远程 shell、workdir join、foreign path、display command |
| Workdir base | `codex-rs/core/src/tools/handlers/mod.rs` | `resolve_workdir_base_path`、environment cwd |
| 环境策略类型 | `codex-rs/protocol/src/config_types.rs` | All/Core/None、exclude/set/include_only、use_profile |
| 环境策略算法 | `codex-rs/protocol/src/shell_environment.rs` | 六步 map 构造、默认敏感名过滤、thread ID、PATHEXT |
| 运行时 env 注入 | `codex-rs/core/src/exec_env.rs` | `CODEX_THREAD_ID`、`CODEX_PERMISSION_PROFILE` |
| 环境 TOML 转换 | `codex-rs/config/src/shell_environment_policy.rs` | 默认值、filter representation、case-insensitive pattern |
| Pipe spawn 边界 | `codex-rs/utils/pty/src/pipe.rs` | program/args、current_dir、env_clear、显式 env |
| PTY spawn 边界 | `codex-rs/utils/pty/src/pty.rs` | 相同 argv/cwd/env 语义、Unix arg0 |
| Sandbox argv 转换 | `codex-rs/sandboxing/src/manager.rs` | program + args、wrapper argv、cwd/env 保留 |
| Runtime PATH | `codex-rs/core/src/tools/runtimes/mod.rs` | package/zsh prepend、去重、snapshot 后重放 |
| Snapshot wrapper | `codex-rs/core/src/tools/runtimes/mod.rs` | source、override capture/restore、single-quote escaping、exec |
| Snapshot 生命周期 | `codex-rs/core/src/shell_snapshot.rs` | 捕获、PWD/OLDPWD 排除、10s timeout、验证、清理 |
| Bash 静态解析 | `codex-rs/shell-command/src/bash.rs` | tree-sitter、word-only subset、literal extraction |
| PowerShell 解析 | `codex-rs/shell-command/src/powershell.rs` | wrapper 提取、AST、UTF-8 output prefix |
| 命令展示解析 | `codex-rs/shell-command/src/parse_command.rs` | `shlex_join`、ParsedCommand、Unknown fallback |
| Snapshot 测试 | `codex-rs/core/src/tools/runtimes/mod_tests.rs` | quote、override 不进 argv、PATH precedence |
| Shell argv 测试 | `codex-rs/core/src/shell_tests.rs` | 各 shell 的精确 argv 断言 |

## 第六阶段：退出码、信号、超时、取消与进程树回收

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 退出码规范化 | `codex-rs/utils/pty/src/process.rs` | normal code、Unix `128 + signal`、unknown `-1` |
| ProcessHandle 终止 | `codex-rs/utils/pty/src/process.rs` | interrupt、request_terminate、terminate、Drop |
| Unix containment | `codex-rs/utils/pty/src/process_group.rs` | setsid/setpgid、group signals、parent-death |
| macOS member fallback | `codex-rs/utils/pty/src/process_group.rs` | PermissionDenied、枚举 PGID 成员、身份复核 |
| Pipe terminator | `codex-rs/utils/pty/src/pipe.rs` | SIGINT/SIGKILL、Windows Job/process fallback |
| PTY terminator | `codex-rs/utils/pty/src/pty.rs` | process group、direct child killer、signal reset |
| Windows Job | `codex-rs/utils/pty/src/win/job.rs` | kill-on-close、breakaway、suspended assign/resume |
| Windows root exit | `codex-rs/utils/pty/src/win/mod.rs` | preserve descendants、terminate race lock |
| ConPTY containment | `codex-rs/utils/pty/src/win/psuedocon.rs` | CreateProcessW、PTY/Job startup attributes |
| 传统 exec expiration | `codex-rs/core/src/exec.rs` | 10s default、timeout/cancel outcome、combined token |
| Graceful cancellation | `codex-rs/core/src/exec.rs` | SIGTERM、50ms grace、SIGKILL escalation |
| Timeout result | `codex-rs/core/src/exec.rs` | timed_out、exit 124、附带已收集输出 |
| I/O drain deadline | `codex-rs/core/src/exec.rs` | 2s reader wait、descendant-held pipes、abort fallback |
| Unified process control | `codex-rs/core/src/unified_exec/process.rs` | local/remote interrupt、terminate、confirmed terminate |
| Background process store | `codex-rs/core/src/unified_exec/process_manager.rs` | yield 后保活、poll/write、process identity |
| 单个/全部终止 | `codex-rs/core/src/unified_exec/process_manager.rs` | terminate_process、terminate_all_processes |
| Remote signal protocol | `codex-rs/exec-server-protocol/src/protocol.rs` | Interrupt、SignalParams、TerminateParams/Response |
| Remote termination | `codex-rs/exec-server/src/local_process.rs` | Starting/Running、termination_requested、late start fencing |
| Turn interruption | `codex-rs/core/src/tasks/mod.rs` | CancellationToken、100ms grace、task abort、TurnAborted |
| Session shutdown | `codex-rs/core/src/session/handlers.rs` | tasks、unified processes、code mode、MCP 清理顺序 |
| Unix lifecycle tests | `codex-rs/utils/pty/src/tests.rs` | group child、detached reader、interrupt、late output |
| Windows lifecycle tests | `codex-rs/utils/pty/src/windows_tests.rs` | Job descendant kill、Pipe race、ConPTY Ctrl-C |

## 第六阶段：子进程错误分类、错误传播、重试与用户可读诊断

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Unified exec 错误边界 | `codex-rs/core/src/unified_exec/errors.rs` | `UnifiedExecError`、sandbox output、截断元数据 |
| Core 语义错误 | `codex-rs/protocol/src/error.rs` | `CodexErrorDetails`、`CodexErr`、source、retry delay |
| Sandbox 错误类型 | `codex-rs/protocol/src/error.rs` | denied/timeout/signal、输出 payload |
| 通用重试分类 | `codex-rs/protocol/src/error.rs` | `is_retryable()` 的穷尽 match |
| 客户端错误投影 | `codex-rs/protocol/src/error.rs` | `to_codex_protocol_error()`、`CodexErrorInfo` |
| 用户错误文案 | `codex-rs/protocol/src/error.rs` | `get_error_message_ui()`、2 KiB、output fallback |
| 错误格式测试 | `codex-rs/protocol/src/error_tests.rs` | sandbox stream 选择、空输出 fallback、协议映射 |
| Sandbox denial 推断 | `codex-rs/sandboxing/src/denial.rs` | keyword、quick reject、Linux SIGSYS、side-effect free |
| Tool 错误边界 | `codex-rs/core/src/tools/sandboxing.rs` | `ToolError::Rejected/Codex`、`SandboxAttempt` |
| 审批与 sandbox 编排 | `codex-rs/core/src/tools/orchestrator.rs` | approval、first attempt、denial-only escalation |
| 有界第二次执行 | `codex-rs/core/src/tools/orchestrator.rs` | policy checks、retry reason、second attempt、telemetry |
| Process manager 映射 | `codex-rs/core/src/unified_exec/process_manager.rs` | `SandboxErr` → `UnifiedExecError`、分类变粗边界 |
| 晚到网络拒绝 | `codex-rs/core/src/unified_exec/process_manager.rs` | grace、failure message、terminate |
| Sandbox 输出回模型 | `codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs` | raw output、exit code、omission metadata、无 process ID |
| Tool 控制错误 | `codex-rs/tools/src/function_call_error.rs` | `RespondToModel`、`Fatal` |
| Tool 错误进入 conversation | `codex-rs/core/src/stream_events_utils.rs` | FunctionCallOutput、needs_follow_up、fatal 转换 |
| 远程错误总表 | `codex-rs/exec-server/src/client.rs` | `ExecServerError`、source、registry、protocol |
| Lazy 连接行为 | `codex-rs/exec-server/src/client.rs` | startup sharing、reconnect singleflight、fail-fast status |
| RPC 错误分类 | `codex-rs/exec-server/src/rpc.rs` | closed、JSON、server、timeout、pending limit |
| RPC 错误映射 | `codex-rs/exec-server/src/client.rs` | method/timeout context、closed → disconnected |
| Session 恢复循环 | `codex-rs/exec-server/src/client_recovery.rs` | 25s deadline、retryable errors、最后错误组合 |
| Registry 退避 | `codex-rs/exec-server/src/client_recovery.rs` | 500ms initial、5s cap、deterministic jitter |
| 远程恢复测试 | `codex-rs/exec-server/src/client_recovery_tests.rs` | registry 退避、瞬时分类、process event 重排 |

## 第六阶段：网络请求、DNS、代理、TLS、HTTP、WebSocket 与 SSE

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| HTTP client 构造入口 | `codex-rs/http-client/src/client_builder.rs` | factory 优先、connect timeout、direct 例外、logging |
| Outbound proxy policy | `codex-rs/http-client/src/outbound_proxy.rs` | ReqwestDefault/RespectSystemProxy、route class |
| 系统与环境代理 | `codex-rs/http-client/src/outbound_proxy.rs` | PAC/platform、HTTP(S)/ALL/NO_PROXY、ws/wss 映射 |
| Proxy decision cache | `codex-rs/http-client/src/outbound_proxy.rs` | 60s/5s TTL、256 cap、URL SHA-256 key、singleflight |
| Route-aware client pool | `codex-rs/http-client/src/route_aware_client_pool.rs` | per-URL route、16-client cap、shared deadline |
| Redirect control | `codex-rs/http-client/src/route_aware_redirect.rs` | 10-hop cap、method/body、origin、header removal、Referer |
| Custom CA | `codex-rs/http-client/src/custom_ca.rs` | CODEX_CA_CERTIFICATE、SSL_CERT_FILE、PEM、HTTP/WSS |
| HTTP diagnostics | `codex-rs/http-client/src/client.rs` | trace propagation、URL/header logging、disabled path |
| Transport abstraction | `codex-rs/http-client/src/transport.rs` | execute/stream、status、headers、ByteStream |
| Transport errors | `codex-rs/http-client/src/error.rs` | HTTP、RetryLimit、Timeout、Network、Build |
| HTTP retry | `codex-rs/codex-client/src/retry.rs` | 429/5xx/transport、attempt loop、backoff、jitter |
| Provider 网络配置 | `codex-rs/codex-api/src/provider.rs` | base URL、query、retry、idle timeout、WS scheme |
| Endpoint session | `codex-rs/codex-api/src/endpoint/session.rs` | auth per attempt、HTTP/stream retry boundary |
| Responses HTTP | `codex-rs/codex-api/src/endpoint/responses.rs` | JSON、headers、compression、Accept event-stream |
| SSE processor | `codex-rs/codex-api/src/sse/responses.rs` | framing、1600 channel、idle timeout、failed/completed |
| WebSocket connector | `codex-rs/websocket-client/src/lib.rs` | proxy-aware connection、custom rustls config |
| WebSocket dialer | `codex-rs/websocket-client/src/dialer.rs` | DNS、250ms Happy Eyeballs、proxy tunnel、target TLS |
| Responses WebSocket | `codex-rs/codex-api/src/endpoint/responses_websocket.rs` | HTTP 101、pump、Ping/Pong、exclusive stream、probe |
| Managed network config | `codex-rs/network-proxy/src/config.rs` | Full/Limited、domain、SOCKS、MITM、loopback clamp |
| Managed network runtime | `codex-rs/network-proxy/src/runtime.rs` | 2s DNS、private/local IP、200 blocked events、audit |
| Core proxy spec | `codex-rs/core/src/config/network_proxy_spec.rs` | managed constraints、permission profile、approval flow |
| Proxy pool tests | `codex-rs/http-client/src/route_aware_client_pool_tests.rs` | route reuse、redirect、deadline、sensitive headers |
| WebSocket dialer tests | `codex-rs/websocket-client/src/dialer_tests.rs` | address fallback、proxy、TLS、connection errors |

## 第六阶段：Rust Workspace、Cargo、Just 与 Bazel 构建图

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Workspace 边界 | `codex-rs/Cargo.toml` | members、resolver 2、workspace package/dependencies |
| 统一 lint/profile | `codex-rs/Cargo.toml` | workspace lints、dev/release/profiling/ci-test profiles |
| 全局依赖替换 | `codex-rs/Cargo.toml` | `[patch.crates-io]`、git revision |
| Package 与 crate 名 | `codex-rs/core/Cargo.toml` | `codex-core`、`codex_core`、lib/bin targets |
| 多 target package | `codex-rs/tui/Cargo.toml` | library、codex-tui、md-events、autobins false |
| Cargo 解析结果 | `codex-rs/Cargo.lock` | version、source、checksum、transitive dependencies |
| Rust 工具链 | `codex-rs/rust-toolchain.toml` | 1.95.0、clippy、rustfmt、rust-src |
| 平台 Rust flags | `codex-rs/.cargo/config.toml` | Windows MSVC/GNU stack 和 target flags |
| 测试运行策略 | `codex-rs/.config/nextest.toml` | local/default profile、retry、slow timeout、test groups |
| 团队命令入口 | `justfile` | cwd、参数转发、fmt/test/fix、schema 与 Bazel recipes |
| Bazel module graph | `MODULE.bazel` | Bzlmod deps、rules_rs、toolchains、platform triples |
| Cargo graph 导入 | `MODULE.bazel` | `crate.from_cargo`、Cargo.toml/Cargo.lock |
| Bazel 锁定状态 | `MODULE.bazel.lock` | module extension 与 dependency resolution |
| 根平台 targets | `BUILD.bazel` | Linux、Windows MSVC/gnullvm platforms、rbe alias |
| Rust crate 宏 | `defs.bzl` | `codex_rust_crate`、library/binary/test/build script |
| Cargo-like 测试环境 | `defs.bzl` | workspace root wrapper、runfiles、`CARGO_BIN_EXE_*` |
| Core 构建数据 | `codex-rs/core/BUILD.bazel` | compile_data、extra binaries、schema/snapshots、shards |
| TUI 构建数据 | `codex-rs/tui/BUILD.bazel` | collaboration templates、snapshot data、test shards |
| Bazel lock 校验 | `scripts/check-module-bazel-lock.sh` | `--lockfile_mode=error` 与 drift 提示 |
| 仓库格式化 | `scripts/format.py` | Rust、Starlark、justfile、Python 的统一格式入口 |
| 格式 CI | `.github/workflows/repo-checks.yml` | `just fmt-check`、clean worktree |
| Bazel CI | `.github/workflows/bazel.yml` | lock check、跨平台 test、sharding、execution logs |
| Nextest CI | `.github/workflows/rust-ci-full-nextest-platform.yml` | archive、platform matrix、shards、JUnit |
| Bazel cache key | `.github/actions/prepare-bazel-ci/action.yml` | MODULE.bazel、Cargo.lock、Cargo.toml hash |

## 第六阶段：Rust 编译、链接、原生依赖与发布制品

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Rust 编译 profile | `codex-rs/Cargo.toml` | codegen units、ThinLTO、debug、split-debuginfo、strip |
| Rust toolchain | `codex-rs/rust-toolchain.toml` | rustc 1.95.0、clippy、rustfmt、rust-src |
| Cargo 平台 flags | `codex-rs/.cargo/config.toml` | Windows stack reserve、CRT static、Arm64 flag |
| CLI targets | `codex-rs/cli/Cargo.toml` | build.rs、default-run、library、codex/logs binaries |
| macOS link flag | `codex-rs/cli/build.rs` | `CARGO_CFG_TARGET_OS`、`-ObjC` |
| CLI Bazel linking | `codex-rs/cli/BUILD.bazel` | macOS native flags、bwrap、multiplatform binaries |
| Bwrap Cargo inputs | `codex-rs/bwrap/Cargo.toml` | Linux libc、cc、pkg-config build dependencies |
| Bwrap native build | `codex-rs/bwrap/build.rs` | OUT_DIR、vendored C、libcap、cc::Build、link metadata、cfg |
| Bwrap Bazel parity | `codex-rs/bwrap/BUILD.bazel` | disabled build.rs、cc_library、@libcap、explicit cfg |
| Windows manifest link | `codex-rs/windows-sandbox-rs/build.rs` | binary-scoped arg、MSVC/gnullvm branches |
| Windows artifact targets | `codex-rs/windows-sandbox-rs/Cargo.toml` | library、setup binary、command runner |
| Cross-platform link flags | `defs.bzl` | stack reserve、MSVC UCRT、macOS `-ObjC/-lc++` |
| Hermetic native tools | `MODULE.bazel` | LLVM、macOS SDK、rules_cc、platform toolchains |
| Release target matrix | `bazel/platforms/release_binaries.bzl` | Linux musl、macOS、Windows × amd64/arm64 |
| Musl sysroot/tool setup | `.github/scripts/install-musl-build-tools.sh` | CC/CXX/linker、Zig、libcap、pkg-config isolation |
| MSVC environment | `.github/actions/setup-msvc-env/action.yml` | Target-specific Visual Studio SDK environment |
| Unix/macOS release | `.github/workflows/rust-release.yml` | Matrix、musl、timings、symbols、strip、signing |
| Windows release | `.github/workflows/rust-release-windows.yml` | exe/PDB staging、symbol job、signing |
| Symbol extraction | `.github/scripts/archive-release-symbols-and-strip-binaries.sh` | `.debug`、dSYM、PDB 与 strip branches |
| Rusty V8 native artifact | `.github/actions/setup-rusty-v8/action.yml` | Target override、prebuilt archive、checksum |

## 第六阶段：Rust 运行时内存、布局、分配与 OOM

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 传统 exec 内存上限 | `codex-rs/core/src/exec.rs` | 8192-byte read、8 KiB initial capacity、OOM hard cap |
| Unified exec 总预算 | `codex-rs/core/src/unified_exec/mod.rs` | 1 MiB transcript、token budget、64 processes |
| Head-tail buffer | `codex-rs/core/src/unified_exec/head_tail_buffer.rs` | Vec/VecDeque、saturating accounting、snapshot clone、mem::take |
| Buffer 边界测试 | `codex-rs/core/src/unified_exec/head_tail_buffer_tests.rs` | 零容量、超大 chunk、omission、drain |
| Streaming transient state | `codex-rs/core/src/unified_exec/async_watcher.rs` | 8192 delta、pending deque、Arc<Mutex<buffer>> |
| Remote output retention | `codex-rs/exec-server/src/local_process.rs` | Box<RunningProcess>、1 MiB、50,000 chunks、FIFO eviction |
| Stdin ID memory | `codex-rs/exec-server/src/local_process.rs` | HashSet + VecDeque、String clone、4096 cap |
| RPC future/capacity | `codex-rs/exec-server/src/rpc.rs` | BoxFuture、Arc maps、1024 regular + 1 cleanup slots |
| Weak owner graph | `codex-rs/core/src/session/mcp.rs` | Weak<Session>、downgrade、upgrade-none |
| Shared request bytes | `codex-rs/http-client/src/request.rs` | EncodedJsonBody、Bytes clone、trace bytes、prepare once |
| Streaming bytes | `codex-rs/http-client/src/transport.rs` | BoxStream<Bytes>、error Vec/String conversion |
| TUI trait objects | `codex-rs/tui/src/chatwidget.rs` | Box<dyn HistoryCell>、Arc<ModelCatalog>、AtomicBool |
| String allocation | `codex-rs/utils/string/src/truncate.rs` | with_capacity、UTF-8 boundary、transient output copy |
| Process owner graph | `codex-rs/utils/pty/src/process.rs` | Arc owner、task/channel handles、Drop |
| Pipe chunk allocation | `codex-rs/utils/pty/src/pipe.rs` | Fixed read buffer、bounded stdin、Vec chunks |
| Bytes 到文本 | `codex-rs/protocol/src/exec_output.rs` | UTF-8/legacy decode、lossy String allocations |
| File search memory | `codex-rs/file-search/src/lib.rs` | Bounded channel、candidate stream、cancel |
| Search shared state | `codex-rs/tui/src/file_search.rs` | Arc<Mutex<SearchState>>、session generation |
| Test thread stack | `justfile` | `RUST_MIN_STACK=8388608` |
| Memory-related crates/profile | `codex-rs/Cargo.toml` | bytes、collections、runtime、debug/release profiles |

## 第六阶段：Serde、JSON/TOML、反序列化边界与 Schema

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| JSON-RPC bounded decode | `codex-rs/exec-server-protocol/src/rpc.rs` | `JSONRPCMessage::deserialize`、256K value nodes |
| Stateful node budget | `codex-rs/exec-server-protocol/src/rpc.rs` | `BoundedValueSeed`、`checked_sub`、共享 remaining |
| Streaming visitor | `codex-rs/exec-server-protocol/src/rpc.rs` | `visit_seq`、`visit_map`、owned `Value` tree |
| Duplicate-key rejection | `codex-rs/exec-server-protocol/src/rpc.rs` | `contains_key` 后显式 error |
| Arbitrary-precision JSON | `codex-rs/exec-server-protocol/src/rpc.rs` | private Number/RawValue tokens、child budget |
| Bounded decoder tests | `codex-rs/exec-server-protocol/src/rpc_tests.rs` | compact array、large scalar、raw bypass、duplicate keys |
| JSON-RPC message classification | `codex-rs/exec-server-protocol/src/rpc.rs` | method/id/result 决定 request/notification/response/error |
| String/integer request ID | `codex-rs/exec-server-protocol/src/rpc.rs` | untagged `RequestId` |
| Typed client request | `codex-rs/app-server-protocol/src/protocol/common.rs` | `client_request_definitions!`、method tag、params |
| Typed JSON bridge | `codex-rs/app-server-protocol/src/protocol/common.rs` | `serde_json::to_value/from_value` |
| Notification framing | `codex-rs/app-server-protocol/src/protocol/common.rs` | method tag + params content |
| Tagged capability enum | `codex-rs/protocol/src/capabilities.rs` | `CapabilityRootLocation`、type discriminator |
| Custom PathUri decode | `codex-rs/protocol/src/capabilities.rs` | legacy string、parse/fallback、Serde/TS/Schema alignment |
| Typed MCP core | `codex-rs/protocol/src/mcp.rs` | Tool/Resource fields 与 flexible `Value` extensions |
| MCP aliases | `codex-rs/protocol/src/mcp.rs` | inputSchema/input_schema、outputSchema/output_schema |
| Lossy numeric adapter | `codex-rs/protocol/src/mcp.rs` | `deserialize_lossy_opt_i64` |
| Rollout JSONL writer | `codex-rs/rollout/src/recorder.rs` | `JsonlWriter::write_line`、to_string/newline/write/flush |
| Flattened rollout line | `codex-rs/rollout/src/recorder.rs` | `RolloutLineRef`、timestamp、ordinal、flatten item |
| Resume newline repair | `codex-rs/rollout/src/recorder.rs` | `ensure_rollout_is_newline_terminated` |
| Typed config input | `codex-rs/config/src/config_toml.rs` | `ConfigToml`、Option fields、custom compatibility decode |
| Dynamic config layers | `codex-rs/config/src/loader/mod.rs` | per-source `TomlValue`、merge、project trust、typed conversion |
| Unknown config fields | `codex-rs/config/src/strict_config.rs` | `serde_ignored` paths、unknown feature keys |
| Nested config errors | `codex-rs/config/src/diagnostics.rs` | `serde_path_to_error`、TOML span、source range |
| App-server schema generation | `codex-rs/app-server-protocol/src/export.rs` | `schema_for!`、per-type JSON Schema writer |
| Stable/experimental fixtures | `codex-rs/app-server-protocol/src/schema_fixtures.rs` | precomputed exports、fixture write workflow |
| Config schema generation | `codex-rs/config/src/schema.rs` | `write_config_schema` |
| Schema command entrypoint | `codex-rs/core/src/bin/config_schema.rs` | config schema output path |
| Generated protocol schemas | `codex-rs/app-server-protocol/schema/json/` | exact committed wire descriptions |
| Schema workflows | `justfile` | `write-app-server-schema`、`write-config-schema` |

## 第六阶段：Rust 生命周期、Borrow、Send/Sync 与异步边界

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Background task trait | `codex-rs/core/src/tasks/mod.rs` | `SessionTask: Send + Sync + 'static` |
| Owned async receiver | `codex-rs/core/src/tasks/mod.rs` | `self: Arc<Self>`、owned Session/TurnContext/input/token |
| Native async trait contract | `codex-rs/core/src/tasks/mod.rs` | RPITIT `impl Future<Output=...> + Send` |
| Erased task adapter | `codex-rs/core/src/tasks/mod.rs` | `AnySessionTask`、blanket impl、`Box::pin` |
| Static versus borrowed task future | `codex-rs/core/src/tasks/mod.rs` | `run` BoxFuture static、`abort<'a>` BoxFuture borrowed |
| Borrowed provider future | `codex-rs/core/src/current_time.rs` | `TimeFuture<'a>`、`SleepFuture<'a>`、`TimeProvider` |
| Spawn boundary | `codex-rs/app-server/src/connection_cleanup.rs` | `Future + Send + 'static`、JoinSet |
| Task lifecycle owner | `codex-rs/app-server/src/connection_cleanup.rs` | reap、drain、abort、JoinError |
| Queued future type | `codex-rs/app-server/src/request_serialization.rs` | `BoxFutureUnit`、static boxed Future |
| Queue ownership boundary | `codex-rs/app-server/src/request_serialization.rs` | `QueuedInitializedRequest`、VecDeque、drain spawn |
| Shared queue owner | `codex-rs/app-server/src/request_serialization.rs` | Arc<Mutex<HashMap<...>>>、clone before spawn |
| Tool borrowed future | `codex-rs/core/src/tools/registry.rs` | `wait_until_ready<'a>` 的 self/session/future 关系 |
| MCP borrowed implementation | `codex-rs/core/src/tools/handlers/mcp.rs` | async block 使用 self 与 session references |
| Owner-then-borrow pattern | `codex-rs/core/src/tools/parallel.rs` | owned captures 进入 spawn、内部 readiness borrow |
| Arc task captures | `codex-rs/core/src/tools/parallel.rs` | Session/Turn/router/tracker ownership clones |
| Static state section | `codex-rs/core/src/context/world_state/mod.rs` | `WorldStateSection: Send + Sync + 'static` |
| Static ID and owned snapshot | `codex-rs/core/src/context/world_state/mod.rs` | `ID: &'static str`、`Snapshot: DeserializeOwned` |
| Borrow-or-own path | `codex-rs/core/src/config/permissions.rs` | `Cow<'_, Path>` |
| HRTB example | `codex-rs/core/src/client_tests.rs` | `for<'a> LookupSpan<'a>` |
| Weak Session ownership | `codex-rs/core/src/session/mcp.rs` | `Weak<Session>`、upgrade、non-owning callback |
| Static shared futures | `codex-rs/core/src/environment_selection.rs` | `Shared<BoxFuture<'static, ...>>` |
| Borrowed boxed future alias | `codex-rs/core/src/stream_events_utils.rs` | Future lifetime tied to caller scope |

## 第六阶段：Rust Result、thiserror、anyhow、Panic 与错误边界

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Typed config error | `codex-rs/app-server/src/config_manager_service.rs` | `ConfigManagerError` 的 Write/Io/Json/Toml/Anyhow variants |
| Error Display/source | `codex-rs/app-server/src/config_manager_service.rs` | `#[error]`、`#[source]`、static context |
| Domain error code | `codex-rs/app-server/src/config_manager_service.rs` | `write_error_code()` 的 typed match |
| Nested blocking result | `codex-rs/app-server/src/config_manager_service.rs` | `write_empty_user_config`、JoinError 与 io::Error 两层 map_err |
| Option to anyhow | `codex-rs/app-server/src/current_time.rs` | Weak upgrade 后 `.context(...)` |
| Anyhow context | `codex-rs/app-server/src/current_time.rs` | duration/range/deserialize context |
| Layered async result | `codex-rs/app-server/src/current_time.rs` | timeout、oneshot、remote JSON-RPC 三层 match |
| Task runtime failure | `codex-rs/app-server/src/connection_cleanup.rs` | `Result<(), JoinError>`、`is_cancelled()` |
| Typed file errors | `codex-rs/core/src/exec_policy.rs` | path + source 的 ReadDir/ReadFile/ParsePolicy |
| Blocking JoinError variant | `codex-rs/core/src/exec_policy.rs` | `JoinBlockingTask` |
| Automatic From | `codex-rs/core/src/exec_policy.rs` | `#[from] ExecPolicyRuleError` |
| Transparent wrapper | `codex-rs/core/src/image_preparation.rs` | `#[error(transparent)] Processing(#[from] ...)` |
| Anyhow chain inspection | `codex-rs/core/src/session_rollout_init_error.rs` | `chain()`、typed `downcast_ref` |
| Portable I/O mapping | `codex-rs/core/src/session_rollout_init_error.rs` | ErrorKind 到可行动 session-storage 提示 |
| Semantic exec error | `codex-rs/core/src/unified_exec/errors.rs` | typed variants、sandbox output、truncation metadata |
| Anyhow protocol boundary | `codex-rs/app-server/src/request_processors/mcp_processor.rs` | `{error:#}`、internal_error projection |
| Result pipeline | `codex-rs/core/src/config_lock.rs` | map_err、`?`、representation validation |
| Product error details | `codex-rs/protocol/src/error.rs` | Codex error classification 与内部 source 的边界 |
| Wire-level error info | `codex-rs/app-server-protocol/src/protocol/v2/shared.rs` | `CodexErrorInfo` stable client projection |

## 第六阶段：Rust 宏、Attributes、cfg、Cargo Features 与生成代码

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Experimental matcher arms | `codex-rs/app-server-protocol/src/protocol/common.rs` | `experimental_reason_expr!` 与 optional metadata |
| Serialization DSL | `codex-rs/app-server-protocol/src/protocol/common.rs` | `serialization_scope_expr!` 的多 token shapes |
| Client request generator | `codex-rs/app-server-protocol/src/protocol/common.rs` | `client_request_definitions!` matcher/repetitions |
| Generated request API | `codex-rs/app-server-protocol/src/protocol/common.rs` | enum、id/method/scope、JSON conversion |
| Generated response API | `codex-rs/app-server-protocol/src/protocol/common.rs` | response enum、payload、From implementations |
| Generated test exports | `codex-rs/app-server-protocol/src/protocol/common.rs` | experimental tables、TS visitors、Schema exporters |
| Manual macro escape | `codex-rs/app-server-protocol/src/protocol/common.rs` | `client_response_payload_from_impl!` empty manual arm |
| Server request generator | `codex-rs/app-server-protocol/src/protocol/common.rs` | params/response pairing、request constructor |
| Notification generator | `codex-rs/app-server-protocol/src/protocol/common.rs` | propagated meta、method/content tagged enums |
| Request DSL invocations | `codex-rs/app-server-protocol/src/protocol/common.rs` | Stable/experimental entries 与 wire literals |
| Clap derive | `codex-rs/app-server/src/main.rs` | `#[derive(Parser)]`、arg/command metadata |
| Debug-only symbols | `codex-rs/app-server/src/main.rs` | `#[cfg(debug_assertions)]` test hooks |
| Dependency proc-macro features | `codex-rs/app-server/Cargo.toml` | clap derive、serde derive、Tokio macros |
| Minimal dependency features | `codex-rs/core/Cargo.toml` | rmcp default-features false 与显式 features |
| Target-specific dependency | `codex-rs/app-server/Cargo.toml` | Windows-only sandbox dependency |
| Build-script cfg declaration | `codex-rs/bwrap/build.rs` | `rustc-check-cfg=cfg(bwrap_available)` |
| Build-script cfg enabling | `codex-rs/bwrap/build.rs` | Native build 成功后 `rustc-cfg` |
| Custom cfg consumer | `codex-rs/bwrap/src/main.rs` | available/unavailable compiled branches |
| Runtime product features | `codex-rs/features/src/lib.rs` | `Feature`、`Stage`、配置与 telemetry registry |
| Platform item branches | `codex-rs/app-server/src/request_processors/feedback_processor.rs` | Windows/non-Windows implementations |
| Compile-time boolean branch | `codex-rs/app-server/src/request_processors/thread_processor.rs` | `cfg!(windows)` 用于两边均合法的逻辑 |
| Integration-test visibility | `codex-rs/core/src/agents_md.rs` | 不能只用 cfg(test) 的注释/接口 |
| Schema fixture pipeline | `codex-rs/app-server-protocol/src/schema_fixtures.rs` | Derive/export 到 committed JSON fixtures |
| Bazel compile inputs | `codex-rs/core/BUILD.bazel` | compile_data 与 Cargo/Bazel parity |
| Generation recipes | `justfile` | App-server/config schema 和其他 codegen commands |

## 第六阶段：Rust Trait、泛型、关联类型与动态分发

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Runtime provider contract | `codex-rs/core/src/current_time.rs` | `TimeProvider: Send + Sync`、boxed borrowed futures |
| Concrete provider | `codex-rs/core/src/current_time.rs` | `SystemTimeProvider` 的 impl |
| Runtime provider selection | `codex-rs/core/src/current_time.rs` | `resolve_time_provider`、`Arc<dyn TimeProvider>` |
| Storage-neutral Trait | `codex-rs/thread-store/src/store.rs` | `ThreadStore: Any + Send + Sync` |
| Boxed generic future alias | `codex-rs/thread-store/src/store.rs` | `ThreadStoreFuture<'a, T>` |
| Default capability behavior | `codex-rs/thread-store/src/store.rs` | false/Unsupported defaults、`as_any` escape hatch |
| Typed async task contract | `codex-rs/core/src/tasks/mod.rs` | `SessionTask`、RPITIT、`self: Arc<Self>` |
| Erased async task contract | `codex-rs/core/src/tasks/mod.rs` | `AnySessionTask`、BoxFuture |
| Blanket task adapter | `codex-rs/core/src/tasks/mod.rs` | `impl<T> AnySessionTask for T where T: SessionTask` |
| Typed state section | `codex-rs/core/src/context/world_state/mod.rs` | `WorldStateSection::Snapshot`、associated `ID` |
| Erased state section | `codex-rs/core/src/context/world_state/mod.rs` | `ErasedWorldStateSection`、JSON Value adapter |
| Heterogeneous section map | `codex-rs/core/src/context/world_state/mod.rs` | `Box<dyn ErasedWorldStateSection>` |
| Unsized Trait reference | `codex-rs/core/src/context/world_state/mod.rs` | `impl ContextualUserFragment + ?Sized` |
| Tool supertrait | `codex-rs/core/src/tools/registry.rs` | `CoreToolRuntime: ToolExecutor<ToolInvocation>` |
| Generic-to-dyn registration | `codex-rs/core/src/tools/registry.rs` | `add<T>` 到 `Arc<dyn CoreToolRuntime>` |
| Trait object collection | `codex-rs/core/src/tools/registry.rs` | RegisteredTool 与 runtime registry |
| Sized-only convenience methods | `codex-rs/context-fragments/src/fragment.rs` | `Self: Sized`、associated `type_markers` |
| Boxed object receiver | `codex-rs/context-fragments/src/fragment.rs` | `self: Box<Self>` response conversion |
| Generic iterator pipeline | `codex-rs/core/src/compact_remote_history.rs` | `I: IntoIterator`、`I::Item: Borrow<ResponseItem>` |
| Opaque iterator return | `codex-rs/core/src/compact_remote_history.rs` | `impl Iterator<Item = ...>` |
| Generic closure adapter | `codex-rs/app-server/src/request_processors/thread_enrichment.rs` | `T`、`impl FnMut(&mut T) -> &mut Thread` |

## 第六阶段：Rust Enum、模式匹配与状态机

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Non-exhaustive command enum | `codex-rs/protocol/src/protocol.rs` | `Op`、`#[non_exhaustive]`、struct/tuple/unit variants |
| Tagged event enum | `codex-rs/protocol/src/protocol.rs` | `EventMsg`、type discriminator、variant payloads |
| Agent lifecycle enum | `codex-rs/protocol/src/protocol.rs` | `AgentStatus`、Completed Option、Errored String |
| Event-to-status projection | `codex-rs/core/src/agent/status.rs` | `agent_status_from_event`、nested abort reason match |
| Terminal classification | `codex-rs/core/src/agent/status.rs` | `is_final`、matches!、or-pattern |
| Exhaustive status formatting | `codex-rs/core/src/session_prefix.rs` | Completed Some/None、terminal/non-terminal arms |
| Tool payload enum | `codex-rs/tools/src/tool_payload.rs` | struct variants、borrowed/owned Cow output |
| Client thread status | `codex-rs/app-server-protocol/src/protocol/v2/thread.rs` | tagged enum、Active struct variant、active flags |
| Runtime status facts | `codex-rs/app-server/src/thread_status.rs` | RuntimeFacts、counters、running/system-error facts |
| Status projection priority | `codex-rs/app-server/src/thread_status.rs` | `loaded_thread_status`、early return ordering |
| Observation-window correction | `codex-rs/app-server/src/thread_status.rs` | `resolve_thread_status`、Idle/NotLoaded→Active |
| Nested exhaustive filter | `codex-rs/core/src/agent/control/spawn.rs` | `keep_forked_rollout_item`、nested/or/literal patterns |
| Let-else guard clauses | `codex-rs/core/src/agent/control/spawn.rs` | Message/content shape filtering |
| Whole-value `@` binding | `codex-rs/core/src/agent/control/spawn.rs` | SubAgentSource 完整值与 parent field 同时绑定 |
| Generic enum payload | `codex-rs/core/src/context/world_state/mod.rs` | `PreviousSectionState<'a, T>` 的 Absent/Unknown/Known |

## 第六阶段：Rust Iterator、Closure、惰性管线与 Stream

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Generic iterator state machine | `codex-rs/core/src/compact_remote_history.rs` | IntoIterator、peekable、from_fn、next_if |
| Optional item chaining | `codex-rs/core/src/compact_remote_history.rs` | once(source).chain(Option) |
| Filter-and-flatten pipeline | `codex-rs/app-server/src/external_agent_migration/processor.rs` | iter、filter_map、flat_map、cloned、collect |
| Short-circuit existence checks | `codex-rs/app-server/src/external_agent_migration/processor.rs` | any、nested predicate |
| Fallible collection | `codex-rs/app-server/src/request_processors/thread_processor.rs` | map deserialize、collect Result Vec、`?` |
| Incremental prefix validation | `codex-rs/core/src/client.rs` | chain、zip、all、length precheck |
| Reverse fold | `codex-rs/app-server/src/config_manager_service.rs` | path.iter().rev().fold sparse TOML overlay |
| Option zip | `codex-rs/app-server/src/config_manager_service.rs` | 两个 optional representations 的成对比较 |
| Sliding window search | `codex-rs/core/src/tools/runtimes/shell/unix_escalation.rs` | windows(3)、find_map、match guard |
| Iterator-to-Stream lift | `codex-rs/app-server/src/external_agent_migration/session_importer.rs` | stream::iter、async map |
| Bounded unordered concurrency | `codex-rs/app-server/src/external_agent_migration/session_importer.rs` | buffer_unordered、next().await |
| Lock-bounded snapshot | `codex-rs/core/src/thread_manager.rs` | read lock 内 Arc clone/collect、锁外 async shutdown |
| Dynamic future set | `codex-rs/core/src/thread_manager.rs` | collect FuturesUnordered、完成顺序分类 |

## 第六阶段：Rust 集合、Map、Set、Queue 与 Entry API

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Ordered tool registry | `codex-rs/core/src/tools/registry.rs` | IndexMap、注册顺序、prepend/shift insert |
| Duplicate-key policy | `codex-rs/core/src/tools/registry.rs` | Entry Vacant/Occupied、trusted/external 分支 |
| Per-resource FIFO queues | `codex-rs/app-server/src/request_serialization.rs` | HashMap<Key, VecDeque<Request>> |
| Lock-bounded queue drain | `codex-rs/app-server/src/request_serialization.rs` | pop_front batch、释放锁、join_all |
| Shared-read batching | `codex-rs/app-server/src/request_serialization.rs` | 只合并队首连续 SharedRead |
| Runtime fact map | `codex-rs/app-server/src/thread_status.rs` | HashMap entry/or_default、watcher cleanup |
| Fixed recent window | `codex-rs/core/src/guardian/mod.rs` | recent_denials VecDeque、头部 eviction |
| Residency ordering | `codex-rs/core/src/agent/control/residency.rs` | touch、pop_front、push_back、candidate scan |
| Deterministic config keys | `codex-rs/app-server/src/config_manager.rs` | BTreeMap/BTreeSet feature collections |
| Stable first-seen dedup | `codex-rs/app-server/src/request_processors/apps_processor/read.rs` | HashSet seen + ordered output |
| Deterministic search sort | `codex-rs/app-server/src/fuzzy_file_search.rs` | score descending、path ascending tie-break |

## 第六阶段：Rust 内部可变性、锁、原子类型与一次初始化

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Async lock scope | `codex-rs/app-server/src/request_serialization.rs` | Tokio Mutex、锁内 pop batch、锁外 join_all |
| Cooperative atomic cancel | `codex-rs/app-server/src/fuzzy_file_search.rs` | Arc<AtomicBool>、Relaxed load/store |
| Single terminal winner | `codex-rs/core/src/tools/parallel.rs` | AtomicBool swap、AcqRel |
| Execution counter | `codex-rs/core/src/agent/control/execution.rs` | AtomicUsize、fetch add/sub、RAII guard |
| Connection initialization | `codex-rs/app-server/src/message_processor.rs` | OnceLock initialized session state |
| Lazy built-in config | `codex-rs/core/src/agent/role.rs` | static LazyLock<BTreeMap<...>> |
| Async session initialization | `codex-rs/core/src/tools/code_mode/mod.rs` | Tokio OnceCell<Arc<dyn CodeModeSession>> |
| Sync read-heavy state | `codex-rs/app-server/src/config_manager.rs` | std RwLock runtime feature projection |

## 第六阶段：Rust Module、路径、可见性、Re-export 与 API 边界

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Core module graph | `codex-rs/core/src/lib.rs` | `mod`、`pub mod`、`pub(crate) mod` 与 crate root |
| Core public facade | `codex-rs/core/src/lib.rs` | `ThreadManager`、`CodexThread` 等 root `pub use` |
| Crate-internal facade | `codex-rs/core/src/lib.rs` | inline `mentions` 与 `pub(crate) use` |
| Compatibility aliases | `codex-rs/core/src/lib.rs` | deprecated `Conversation` 系列旧名称 |
| Protocol ID facade | `codex-rs/protocol/src/lib.rs` | 私有 ID modules 与根路径 `ThreadId` 等 re-export |
| App-server facade | `codex-rs/app-server/src/lib.rs` | 私有实现 modules 与少量选择性 re-export |
| Sibling test module | `codex-rs/core/src/agent/control.rs` | `#[cfg(test)]`、`#[path = "control_tests.rs"]` |
| Platform implementation | `codex-rs/app-server/src/request_processors/feedback_processor.rs` | Windows/non-Windows `cfg` items |

## 第六阶段：Rust Struct、Impl、Newtype、Builder 与领域建模

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Business ID struct | `codex-rs/protocol/src/thread_id.rs` | `ThreadId`、crate-visible UUID、new/from-string/default |
| Distinct numeric IDs | `codex-rs/code-mode-protocol/src/host/types.rs` | `RequestId`、`DelegateRequestId`、const constructor |
| Non-zero domain value | `codex-rs/code-mode-protocol/src/host/types.rs` | `ProtocolVersion(NonZeroU32)`、Option constructor |
| Layered validated newtypes | `codex-rs/code-mode-protocol/src/host/types.rs` | `NonEmptyString`、`Capability`、`SessionId` |
| Aggregate invariant | `codex-rs/code-mode-protocol/src/host/types.rs` | `CapabilitySet`、`SupportedProtocolVersions`、duplicate/empty errors |
| Absolute path newtype | `codex-rs/utils/absolute-path/src/lib.rs` | private PathBuf、smart constructors、as/to/into methods |
| Configuration builder | `codex-rs/core/src/config/mod.rs` | `ConfigBuilder`、fluent setters、async consuming build |
| Edit-command builder | `codex-rs/core/src/config/edit.rs` | `ConfigEditsBuilder`、Vec<ConfigEdit>、atomic apply |
| Validated profile name | `codex-rs/protocol/src/config_types.rs` | `ProfileV2Name`、private String、FromStr validation |
| Public protocol DTO | `codex-rs/protocol/src/approvals.rs` | `NetworkApprovalContext`、public host/protocol fields |

## 第六阶段：Rust From、TryFrom、AsRef、Borrow、Cow 与类型转换

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Validated ID conversion | `codex-rs/protocol/src/thread_id.rs` | TryFrom<&str/String>、From<ThreadId> for String |
| Borrowed path view | `codex-rs/utils/absolute-path/src/lib.rs` | AsRef<Path>、as_path、Deref coercion |
| Consuming path conversion | `codex-rs/utils/absolute-path/src/lib.rs` | into_path_buf、From<AbsolutePathBuf> for PathBuf |
| Fallible path upgrade | `codex-rs/utils/absolute-path/src/lib.rs` | TryFrom<&Path/PathBuf/&str/String> |
| Borrow-or-own normalization | `codex-rs/utils/absolute-path/src/lib.rs` | normalize_path_for_platform、Cow<Path> |
| URI validation | `codex-rs/utils/path-uri/src/lib.rs` | TryFrom<Url/String>、FromStr、Deserialize parse |
| Infallible URI fallback | `codex-rs/utils/path-uri/src/lib.rs` | From<AbsolutePathBuf>、opaque fallback encoding |
| Payload log view | `codex-rs/tools/src/tool_payload.rs` | log_payload、Cow<str> Borrowed/Owned branches |
| Conditional name allocation | `codex-rs/core/src/tools/mod.rs` | flat_tool_name、namespace concatenation |
| Generic borrowed item | `codex-rs/core/src/compact_remote_history.rs` | Borrow<ResponseItem>、owned iteration preserved |
| Platform path Cow | `codex-rs/core/src/config/permissions.rs` | unchanged borrowed Path vs normalized owned PathBuf |

## 第六阶段：Rust 智能指针、Deref、Drop、Pin 与 Unpin

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Read-only pointer facade | `codex-rs/utils/absolute-path/src/lib.rs` | Deref<Target = Path>、as_path、private PathBuf |
| Weak service dependency | `codex-rs/app-server/src/current_time.rs` | Weak<OutgoingMessageSender>、upgrade failure、boxed Future |
| Background weak observer | `codex-rs/app-server/src/models_refresh_worker.rs` | Arc::downgrade、per-iteration upgrade、explicit strong drop |
| Worker drop fallback | `codex-rs/app-server/src/models_refresh_worker.rs` | CancellationToken、Drop sends cancel without async join |
| Capacity RAII guard | `codex-rs/core/src/agent/control/execution.rs` | Arc limiter、atomic increment、Drop decrement |
| Search cancellation Drop | `codex-rs/app-server/src/fuzzy_file_search.rs` | FuzzyFileSearchSession、AtomicBool cancellation |
| Borrowing boxed futures | `codex-rs/core/src/current_time.rs` | TimeFuture/SleepFuture、Pin<Box<dyn Future + Send + 'a>> |
| Queued erased futures | `codex-rs/app-server/src/request_serialization.rs` | BoxFutureUnit、'static、heterogeneous request queue |
| Stack-pinned operation state | `codex-rs/app-server/src/command_exec.rs` | tokio::pin! expiration and exit receiver |
| Explicitly Unpin Stream | `codex-rs/tui/src/tui/event_stream.rs` | Unpin bound、Pin<&mut Self>、manual poll_next |
| Large immediately-awaited Future | `codex-rs/core/src/config/mod.rs` | Box::pin(build_inner)、runtime thread stack rationale |

## 第六阶段：Rust Future、Poll、Waker、Async 状态机与取消

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Hand-written process Future | `codex-rs/utils/pty/src/win/mod.rs` | WinChild::poll、OS wait thread、Waker clone/wake |
| Controlled Waker test | `codex-rs/exec-server/src/connection.rs` | AtomicWaker、poll_fn、poll_ready Pending/Ready |
| Channel-backed Stream | `codex-rs/core/src/client_common.rs` | ResponseStream、poll_recv、Drop cancellation token |
| Downstream-drop propagation | `codex-rs/core/src/client.rs` | map_response_events、select consumer_dropped vs api_stream |
| Completion/cancel race | `codex-rs/core/src/tools/parallel.rs` | dispatch JoinHandle、CancellationToken、terminal flag |
| Dynamic concurrent shutdown | `codex-rs/core/src/thread_manager.rs` | FuturesUnordered、completion-order collection、final sorting |
| Bounded unordered import | `codex-rs/app-server/src/external_agent_migration/session_importer.rs` | buffer_unordered、SESSION_IMPORT_CONCURRENCY、next await |
| Boxed in-flight tool state | `codex-rs/core/src/stream_events_utils.rs` | InFlightFuture、async tool-call state、'static ownership |

## 第六阶段：Rust Unit、Integration、Mock、Snapshot 与 Property Test

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Small inline unit test | `codex-rs/protocol/src/thread_id.rs` | cfg(test)、default ID behavioral assertion |
| Sibling private tests | `codex-rs/core/src/agent/control/execution.rs` | path execution_tests.rs、private limiter access |
| Aggregated integration binary | `codex-rs/core/tests/all.rs` | single binary、suite module、test binary dispatch setup |
| Integration suite routing | `codex-rs/core/tests/suite/mod.rs` | feature/platform cfg modules、behavior-area organization |
| Agent test harness | `codex-rs/core/tests/common/test_codex.rs` | TestCodexBuilder、build_with_auto_env、submit/read events |
| Model protocol mock | `codex-rs/core/tests/common/responses.rs` | Wiremock、typed SSE events、mount once、ResponseMock |
| Multi-oracle agent case | `codex-rs/core/tests/suite/additional_context.rs` | wait_for_event、single_request、snapshot plus exact fields |
| App-server public harness | `codex-rs/app-server/tests/common/test_app_server.rs` | JSON-RPC request/response/notification driver |
| Bidirectional protocol journey | `codex-rs/app-server/tests/suite/v2/current_time.rs` | Thread/turn start、server request response、turn completion |
| User-visible TUI snapshot | `codex-rs/tui/src/app/history_ui_tests.rs` | fixed-width render、Insta snapshot |

## 第六阶段：Rust Tracing、日志、Span、结构化诊断与 OTEL 上下文

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Uniform request span | `codex-rs/app-server/src/app_server_tracing.rs` | app_server.request、RPC/transport/connection fields |
| Late-bound request fields | `codex-rs/app-server/src/app_server_tracing.rs` | field::Empty、client info record、turn.id slot |
| Inbound parent extraction | `codex-rs/app-server/src/app_server_tracing.rs` | request trace carrier、invalid-parent warning、env fallback |
| Request Future context | `codex-rs/app-server/src/message_processor.rs` | RequestContext registry、request_fut.instrument |
| W3C context utilities | `codex-rs/otel/src/trace_context.rs` | traceparent/tracestate extract/inject、current trace ID |
| Export routing policy | `codex-rs/otel/src/targets.rs` | log-only and trace-safe target classification |
| Mutable tool outcome span | `codex-rs/core/src/tools/parallel.rs` | tool/call fields、aborted=false then record(true) |
| Function-level error span | `codex-rs/core/src/tools/router.rs` | instrument skip_all err、tool-call parsing/dispatch |
| Bounded tool-call event | `codex-rs/core/src/stream_events_utils.rs` | thread identity、tool name、payload preview |
| Phase timing metrics | `codex-rs/core/src/turn_timing.rs` | timing guard、sampling/compaction/tool durations、TTFT/TTFM |
| Tracing contract tests | `codex-rs/app-server/src/message_processor_tracing_tests.rs` | request parent/context field assertions |

## 第六阶段：Rust Unsafe、FFI、原始指针与 OS Handle

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| C ABI program entry | `codex-rs/bwrap/src/main.rs` | unsafe extern C、CString、argv pointer vector、null sentinel |
| Borrowed Unix descriptor | `codex-rs/linux-sandbox/src/exec_util.rs` | as_raw_fd、fcntl、FD_CLOEXEC、last_os_error |
| Windows allocated pointer | `codex-rs/config/src/loader/mod.rs` | SHGetKnownFolderPath、UTF-16 slice、CoTaskMemFree |
| Checked external buffer | `codex-rs/network-proxy/src/windows_tcp_attribution.rs` | aligned buffer、checked offsets、read_unaligned、from_raw_parts |
| Windows resource owner | `codex-rs/network-proxy/src/windows_tcp_attribution.rs` | OpenProcess、as_raw_handle、OwnedHandle::from_raw_handle |
| Fork-to-exec boundary | `codex-rs/core/src/spawn.rs` | CommandExt::pre_exec、detach TTY、parent-death signal |
| Process lifecycle syscalls | `codex-rs/utils/pty/src/process_group.rs` | prctl/getppid recheck、setsid、setpgid、killpg |
| Comprehensive Unix boundary | `codex-rs/linux-sandbox/src/linux_run_main.rs` | fork、pipe2、signal、from_raw_fd、explicit close |

## 第六阶段：Rust 宏系统、声明宏、过程宏与 Derive

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Public declarative macro | `codex-rs/core/src/util.rs` | feedback_tags、ident=expr repetition、optional comma、absolute paths |
| Protocol definition DSL | `codex-rs/app-server-protocol/src/protocol/common.rs` | client_request_definitions、nested helpers、single source of truth |
| Mirrored enum generator | `codex-rs/app-server-protocol/src/protocol/v2/shared.rs` | v2_enum_from_core、metadata forwarding、bidirectional conversions |
| Crate-relative expansion | `codex-rs/otel/src/events/shared.rs` | $crate paths、tt forwarding、log/trace field separation |
| Compile-time resources | `codex-rs/tui/src/frames.rs` | frames_for、include_str、concat、embedded arrays |
| Callsite compile env | `codex-rs/utils/cargo-bin/src/lib.rs` | find_resource、env/option_env、Cargo/Bazel resolution |
| Procedural derive | `codex-rs/codex-experimental-api-macros/src/lib.rs` | TokenStream、syn DeriveInput、quote、spanned compile error |
| No-op procedural derives | `codex-rs/app-server-protocol-noop-macros/src/lib.rs` | empty expansion、registered helper attributes |
| Conditional macro provider | `codex-rs/app-server-protocol/src/lib.rs` | cfg(test)、real schemars/ts-rs vs production no-op exports |

## 第六阶段：Rust 条件编译、Cfg、Cargo Feature 与 Target

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Custom cfg producer | `codex-rs/bwrap/build.rs` | CARGO_CFG_TARGET_OS、check-cfg、rustc-cfg、native link、rerun inputs |
| Exhaustive cfg branches | `codex-rs/bwrap/src/main.rs` | Linux+available、Linux+unavailable、non-Linux main |
| Bazel cfg equivalent | `codex-rs/bwrap/BUILD.bazel` | disabled build script、OS select、C target、--cfg flag |
| Feature forwarding | `codex-rs/v8-poc/Cargo.toml` | sandbox to v8/v8_enable_sandbox |
| Feature/native agreement | `codex-rs/v8-poc/src/lib.rs` | cfg!(feature)、V8 linked sandbox test |
| Platform dependencies | `codex-rs/config/Cargo.toml` | Unix libc、macOS Core Foundation、Windows APIs |
| Family vs OS implementations | `codex-rs/utils/pty/src/process_group.rs` | Linux prctl、Unix setsid/setpgid、non-Unix fallbacks |
| Platform facade | `codex-rs/app-server/src/request_processors/feedback_processor.rs` | conditional import、Windows attachment、non-Windows None |
| Test/production code graph | `codex-rs/app-server-protocol/src/lib.rs` | cfg(test) modules and real/no-op derives |
| Conditional derive | `codex-rs/app-server-protocol/src/precomputed_exports.rs` | cfg_attr(test, derive(...)) |

## 第六阶段：Rust 编译错误、所有权、生命周期与 Trait 诊断

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Domain conversion errors | `codex-rs/protocol/src/thread_id.rs` | TryFrom、From、UUID parse、Serde custom error |
| Borrowed vs owned path | `codex-rs/utils/absolute-path/src/lib.rs` | as_path、to_path_buf、into_path_buf、Cow lifetimes |
| Borrowing Future contract | `codex-rs/core/src/current_time.rs` | TimeFuture<'a>、Send、TimeProvider trait object |
| Nested async errors | `codex-rs/app-server/src/current_time.rs` | timeout、oneshot cancel、remote error、JSON conversion |
| Queued static Future | `codex-rs/app-server/src/request_serialization.rs` | Send+'static、BoxFutureUnit、lock scope、spawn |
| Opaque-to-boxed adapter | `codex-rs/core/src/tasks/mod.rs` | SessionTask、AnySessionTask、RPITIT、BoxFuture、Arc receiver |
| Trait object registry | `codex-rs/core/src/tools/registry.rs` | CoreToolRuntime bound、Arc<dyn Trait>、coercion |
| Generated exhaustive code | `codex-rs/app-server-protocol/src/protocol/common.rs` | Macro-generated enum/matches and diagnostic origin |

## 第七阶段：App-server 代码生成、TypeScript、JSON Schema 与协议同步

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Test/production derive split | `codex-rs/app-server-protocol/src/lib.rs` | cfg(test)、真实 schemars/ts-rs、production no-op macros |
| Real TypeScript generator | `codex-rs/app-server-protocol/src/export.rs` | GenerateTsOptions、request/response/notification、index/header/Prettier |
| Real JSON Schema generator | `codex-rs/app-server-protocol/src/export.rs` | individual schemas、v1 allowlist、v2 bundle、experimental filtering |
| Fixture writing pipeline | `codex-rs/app-server-protocol/src/schema_fixtures.rs` | ensure_empty_dir、TypeVisitor、stable/experimental temp trees |
| Precomputed archive creation | `codex-rs/app-server-protocol/src/schema_fixtures.rs` | PrecomputedExports、BTreeMap、JSON serialization、zstd level 19 |
| Production schema exporter | `codex-rs/app-server-protocol/src/precomputed_exports.rs` | include_bytes、decompress、generate_ts/json/types、path validation |
| Visible fixture drift | `codex-rs/app-server-protocol/src/schema_fixtures_tests.rs` | path-set comparison、TS normalization、JSON canonicalization、unified diff |
| Archive drift | `codex-rs/app-server-protocol/src/schema_fixtures_tests.rs` | stable fixtures and experimental fresh-generation comparisons |
| Production round trip | `codex-rs/app-server-protocol/src/precomputed_exports_tests.rs` | archive-to-disk parity、header/index generation options |
| Optional/nullable output | `codex-rs/app-server-protocol/schema/typescript/v2/ThreadStartParams.ts` | generated banner、`field?: T | null` |
| Required/property schema | `codex-rs/app-server-protocol/schema/json/v2/ThreadStartParams.json` | properties、required、definitions/$ref |
| Compile/test data inputs | `codex-rs/app-server-protocol/BUILD.bazel` | schema/precomputed compile_data、schema tree test data |
| Fixed-baseline writer driver | `codex-rs/app-server-protocol/scripts/write_schema_fixtures.py` | environment options、ignored exact Rust test、Prettier toggle |
| Documented regeneration workflow | `codex-rs/app-server/README.md` | stable/experimental generation and protocol crate tests |
| Fixed-baseline recipe mismatch | `justfile` | write-app-server-schema points to a bin absent from commit 4ee41929eaf4 |

## 第七阶段：App-server 协议版本演进、前后兼容与迁移策略

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Client identity vs capability | `codex-rs/app-server-protocol/src/protocol/v1.rs` | ClientInfo version、InitializeCapabilities、experimental_api default |
| v1/v2 method coexistence | `codex-rs/app-server-protocol/src/protocol/common.rs` | initialize v1 payload、NEW APIs、DEPRECATED APIs、exact wire methods |
| Experimental metadata | `codex-rs/app-server-protocol/src/protocol/common.rs` | ExperimentalApi、experimental_reason_expr、inspect_params |
| Once-only handshake | `codex-rs/app-server/src/request_processors/initialize_processor.rs` | OnceLock state、Already initialized、client identity/capabilities |
| Request gate | `codex-rs/app-server/src/message_processor.rs` | Not initialized、experimental reason、invalid request path |
| Outgoing compatibility gate | `codex-rs/app-server/src/transport.rs` | per-connection notification skip、experimental field stripping |
| Read alias/write canonical | `codex-rs/app-server-protocol/src/protocol/v2/shared.rs` | guardian_subagent alias、auto_review serialization/custom schema |
| Legacy default regressions | `codex-rs/app-server-protocol/src/protocol/v2/tests.rs` | missing itemsView/isBlocking、legacy payload whole-object assertions |
| Ignored deprecated inputs | `codex-rs/app-server-protocol/src/protocol/v2/thread.rs` | multiAgentMode accepted/ignored、fixed compatibility response |
| Permission compatibility projection | `codex-rs/app-server-protocol/src/protocol/v2/thread.rs` | legacy sandbox input/output、new permissions/profile provenance |
| Runtime deprecation notice | `codex-rs/app-server/src/request_processors/thread_processor.rs` | thread/rollback targeted connection notice |
| Deprecated wire entries | `codex-rs/app-server-protocol/src/protocol/common.rs` | fileChange outputDelta、thread compacted、deprecationNotice |
| Core/wire compatibility split | `codex-rs/app-server/src/bespoke_event_handling.rs` | raw/rollout legacy fan-out vs canonical v2 TurnItem events |
| Historical data adapter | `codex-rs/app-server-protocol/src/protocol/thread_history.rs` | legacy event rebuilding、attachments/client id/compaction preservation |
| Error compatibility surface | `codex-rs/app-server/src/error_code.rs` | JSON-RPC standard codes and project-specific errors |
| Resume behavior contract | `codex-rs/app-server/README.md` | thread/resume、legacy/paginated history compatibility、deprecated fields |

## 第七阶段：App-server JSON-RPC、请求响应、通知、ID 与双向调用生命周期

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Wire envelope | codex-rs/app-server-protocol/src/rpc.rs | JSONRPCMessage、RequestId、无jsonrpc字段、trace/result/error |
| Typed request/response DSL | codex-rs/app-server-protocol/src/protocol/common.rs | ClientRequest、ServerRequest、ServerResponse、notification macros |
| Transport outgoing variants | codex-rs/app-server-transport/src/outgoing_message.rs | ConnectionId、OutgoingMessage、QueuedOutgoingMessage |
| Ingress and overload | codex-rs/app-server-transport/src/transport/mod.rs | TransportEvent、untagged parse、-32001、serialization fallback |
| Stdio framing | codex-rs/app-server-transport/src/transport/stdio.rs | BufRead lines、stdout newline、write_complete |
| Four-way routing | codex-rs/app-server/src/lib.rs | Request/Response/Notification/Error dispatch、connection lifecycle |
| Typed request lifecycle | codex-rs/app-server/src/message_processor.rs | ConnectionRequestId、RequestContext、decode/gate/final response |
| Pending callback owner | codex-rs/app-server/src/outgoing_message.rs | AtomicI64 IDs、HashMap callbacks、oneshot、notify/cancel/replay |
| Approval round trip | codex-rs/app-server/src/bespoke_event_handling.rs | core event→server request→typed decision、safe fallback |
| Listener resolution | codex-rs/app-server/src/thread_state.rs | ResolveServerRequest command and completion |
| Resume replay and UI convergence | codex-rs/app-server/src/request_processors/thread_lifecycle.rs | pending request replay、serverRequest/resolved |
| In-process symmetry | codex-rs/app-server/src/in_process.rs | duplicate ID、typed event queues、shutdown cancellation |
| JSON-RPC error codes | codex-rs/app-server/src/error_code.rs | -32600/-32601/-32602/-32603/-32001 |
| Structured cancellation reason | codex-rs/app-server/src/server_request_error.rs | data.reason=turnTransition |

## 第七阶段：App-server 请求并发、Serialization Scope、队列、公平性与竞态

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Scope declaration DSL | codex-rs/app-server-protocol/src/protocol/common.rs | ClientRequestSerializationScope、serialization_scope_expr |
| Method-to-scope contract | codex-rs/app-server-protocol/src/protocol/common.rs | Global/SharedRead/thread/process/None declarations and tests |
| Internal queue identity | codex-rs/app-server/src/request_serialization.rs | QueueKey、connection-local process/watch keys、Access |
| FIFO queue owner | codex-rs/app-server/src/request_serialization.rs | Mutex<HashMap<Key, VecDeque>>、first-item drainer spawn |
| Shared read batching | codex-rs/app-server/src/request_serialization.rs | consecutive SharedRead pop、join_all、write boundary |
| Queue cleanup race | codex-rs/app-server/src/request_serialization.rs | empty-key removal under the same mutex |
| FIFO and cross-key tests | codex-rs/app-server/src/request_serialization.rs | same-key order、different-key concurrency |
| Read/write fairness tests | codex-rs/app-server/src/request_serialization.rs | concurrent reads、write waits、later read cannot jump |
| Per-connection execution gate | codex-rs/app-server/src/connection_rpc_gate.rs | accepting mutex、TaskTracker token、close/shutdown |
| Scope dispatch | codex-rs/app-server/src/message_processor.rs | keyed enqueue vs None direct spawn |
| App-owned serialized mutation | codex-rs/app-server/src/effective_plugin_change.rs | background Global(config) Exclusive trust update |
| Disconnect drain | codex-rs/app-server/src/lib.rs | close gate、cleanup task、single-client stdio shutdown |

## 第七阶段：App-server Thread、Turn、Item 状态机、快照、事件流与恢复一致性

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Thread/Turn数据形状 | codex-rs/app-server-protocol/src/protocol/v2/thread_data.rs | Thread、Turn、TurnItemsView、时间与部分加载合同 |
| Thread运行状态 | codex-rs/app-server-protocol/src/protocol/v2/thread.rs | ThreadStatus、ThreadActiveFlag、ThreadStatusChangedNotification |
| Turn生命周期通知 | codex-rs/app-server-protocol/src/protocol/v2/turn.rs | TurnStatus、TurnStartedNotification、TurnCompletedNotification |
| Item union与生命周期 | codex-rs/app-server-protocol/src/protocol/v2/item.rs | ThreadItem、ItemStarted/CompletedNotification、delta payloads |
| 共享历史reducer | codex-rs/app-server-protocol/src/protocol/thread_history.rs | ThreadHistoryBuilder、current_turn、handle_event、active snapshot |
| Canonical分页投影 | codex-rs/app-server-protocol/src/protocol/thread_history_projection.rs | TurnStarted/Complete/Aborted、ItemCompleted到change set |
| 投影回归测试 | codex-rs/app-server-protocol/src/protocol/thread_history_projection_tests.rs | 状态、时间与Item change映射 |
| Per-thread live state | codex-rs/app-server/src/thread_state.rs | ThreadState、TurnSummary、subscriber maps、listener command |
| Snapshot+subscribe排序 | codex-rs/app-server/src/request_processors/thread_lifecycle.rs | biased listener、SendThreadResumeResponse、先订阅后响应 |
| Running resume入口 | codex-rs/app-server/src/request_processors/thread_processor.rs | history读取、active合并材料、command enqueue |
| EventMsg到wire事件 | codex-rs/app-server/src/bespoke_event_handling.rs | Turn/Item started/completed、summary view、错误与去重 |
| Thread runtime状态归约 | codex-rs/app-server/src/thread_status.rs | NotLoaded/Idle/Active/SystemError、active flags、watch通知 |
| Resume与Item视图测试 | codex-rs/app-server/src/bespoke_event_handling.rs | turn_started_omits_active_snapshot_items、completed summary测试 |

## 第七阶段：App-server 审批状态机、Server Request、用户输入与多客户端协作

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Server Request方法合同 | codex-rs/app-server-protocol/src/protocol/common.rs | server_request_definitions、v2/legacy methods、typed response |
| Command/File/UserInput payload | codex-rs/app-server-protocol/src/protocol/v2/item.rs | 四类decision、approvalId、availableDecisions、questions/answers |
| Permission请求与响应 | codex-rs/app-server-protocol/src/protocol/v2/permissions.rs | requested/granted profile、Turn/Session scope、strictAutoReview |
| MCP elicitation shape | codex-rs/app-server-protocol/src/protocol/v2/mcp.rs | request body、Accept/Decline/Cancel与content |
| Resolved notification | codex-rs/app-server-protocol/src/protocol/v2/notification.rs | threadId + requestId lifecycle payload |
| Pending callback owner | codex-rs/app-server/src/outgoing_message.rs | AtomicI64、HashMap<RequestId, PendingCallbackEntry>、oneshot |
| 首答胜出 | codex-rs/app-server/src/outgoing_message.rs | take_request_callback、notify_client_response/error |
| Pending replay | codex-rs/app-server/src/outgoing_message.rs | thread过滤、request ID排序、原request定向重发 |
| Thread/Global取消 | codex-rs/app-server/src/outgoing_message.rs | cancel_request、cancel_requests_for_thread、cancel_all_requests |
| 审批事件翻译 | codex-rs/app-server/src/bespoke_event_handling.rs | command/file/user-input/permission/MCP request创建与waiter task |
| Response fallback | codex-rs/app-server/src/bespoke_event_handling.rs | malformed、client error、RecvError、turnTransition分支 |
| 权限安全收窄 | codex-rs/app-server/src/bespoke_event_handling.rs | intersect_permission_profiles、path localization、fail closed |
| Dynamic tool例外 | codex-rs/app-server/src/dynamic_tools.rs | callback decode/fallback、固定提交不发送resolved |
| Resolved事件排序 | codex-rs/app-server/src/thread_state.rs | ResolveServerRequest listener command与completion oneshot |
| Resolved通知发送 | codex-rs/app-server/src/request_processors/thread_lifecycle.rs | resolve_pending_server_request、Thread subscribers |
| 等待状态计数 | codex-rs/app-server/src/thread_status.rs | permission/user-input counter、RAII guard、active flags |
| Turn转换取消原因 | codex-rs/app-server/src/server_request_error.rs | reason=turnTransition detection/tests |
| Peer response入口 | codex-rs/app-server/src/message_processor.rs | process_response、process_error到callback map |

## 第七阶段：App-server Sandbox、权限配置、审批策略与命令执行安全链路

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 审批与Sandbox枚举 | codex-rs/app-server-protocol/src/protocol/v2/shared.rs | AskForApproval、ApprovalsReviewer、SandboxMode及wire rename/alias |
| 详细Sandbox wire策略 | codex-rs/app-server-protocol/src/protocol/v2/permissions.rs | SandboxPolicy四variants、legacy读取兼容、to_core |
| 文件权限wire模型 | codex-rs/app-server-protocol/src/protocol/v2/permissions.rs | FileSystemPath、SpecialPath、AccessMode、SandboxEntry |
| Profile发现与来源 | codex-rs/app-server-protocol/src/protocol/v2/permissions.rs | PermissionProfileList、Summary、ActivePermissionProfile |
| Thread权限入口 | codex-rs/app-server-protocol/src/protocol/v2/thread.rs | sandbox、permissions互斥合同、runtimeWorkspaceRoots |
| Turn权限入口 | codex-rs/app-server-protocol/src/protocol/v2/turn.rs | sticky approval/reviewer/sandboxPolicy/permissions字段 |
| Thread启动转换 | codex-rs/app-server/src/request_processors/thread_processor.rs | SandboxMode到core policy、配置override与有效snapshot |
| Sticky设置构建 | codex-rs/app-server/src/request_processors/turn_processor.rs | build_thread_settings_overrides、互斥、profile加载、preview |
| 兼容权限投影 | codex-rs/app-server/src/request_processors/thread_summary.rs | active profile与compatibility SandboxPolicy |
| Legacy Core Sandbox模型 | codex-rs/protocol/src/protocol.rs | SandboxPolicy、WritableRoot、protected metadata |
| 执行策略计算 | codex-rs/core/src/exec_policy.rs | approval policy、ExecApprovalRequirement、prompt拒绝条件 |
| Shell输入约束 | codex-rs/core/src/tools/handlers/shell.rs | require_escalated、additional permissions与approval policy |
| 附加权限校验 | codex-rs/core/src/tools/handlers/mod.rs | feature gate、OnRequest要求、参数组合拒绝 |
| 审批与第一次尝试 | codex-rs/core/src/tools/orchestrator.rs | requirement、approval、select_initial、SandboxAttempt |
| Sandbox denial升级 | codex-rs/core/src/tools/orchestrator.rs | network context、unsandboxed allowed、fresh approval、retry |
| Managed network审批 | codex-rs/core/src/tools/network_approval.rs | policy decision、目标上下文与approval flow |
| 可移植执行转换 | codex-rs/core/src/exec.rs | build_exec_request、SandboxTransformRequest、execute_exec_request |
| 平台Sandbox选择 | codex-rs/sandboxing/src/manager.rs | should_sandbox、select_initial、transform、effective profile |
| macOS策略构造 | codex-rs/sandboxing/src/seatbelt.rs | Seatbelt policy与sandbox-exec argv |
| Linux能力判断 | codex-rs/sandboxing/src/bwrap.rs | 平台Sandbox需求与Linux helper分支 |
| 真正子进程启动 | codex-rs/core/src/spawn.rs | env_clear、cwd、stdio、pre_exec、kill_on_drop |
| Command exec协议测试 | codex-rs/app-server/tests/suite/v2/command_exec.rs | policy/profile互斥、平台执行与错误行为 |

## 第七阶段：App-server 命令执行、进程会话、流式输出、PTY 与清理

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| 四类RPC方法 | codex-rs/app-server-protocol/src/protocol/common.rs | command/exec、process/*、thread/shellCommand、backgroundTerminals |
| Sandboxed独立命令协议 | codex-rs/app-server-protocol/src/protocol/v2/command_exec.rs | params、deferred response、control、base64 delta、capReached |
| Unsandboxed进程协议 | codex-rs/app-server-protocol/src/protocol/v2/process.rs | spawn即时响应、handle、outputDelta、exited、三态limits |
| Command Item形状 | codex-rs/app-server-protocol/src/protocol/v2/item.rs | CommandExecution字段、Status、Source |
| Thread用户命令与后台列表 | codex-rs/app-server-protocol/src/protocol/v2/thread.rs | shell string、full access、list/terminate payload |
| Core事件到wire delta | codex-rs/app-server-protocol/src/protocol/event_mapping.rs | Begin→started、bytes→delta、End→completed |
| command/exec入口校验 | codex-rs/app-server/src/request_processors/command_exec_processor.rs | empty/互斥/limits/cwd/env/profile/network/build request |
| command/exec会话表 | codex-rs/app-server/src/command_exec.rs | ConnectionProcessId、Generated/Client ID、duplicate与cleanup |
| command/exec运行loop | codex-rs/app-server/src/command_exec.rs | control/expiration/exit select、124、drain、final response |
| command/exec输出收集 | codex-rs/app-server/src/command_exec.rs | 64KiB合并、per-stream cap、base64、stream/buffer二选一 |
| process/spawn实现 | codex-rs/app-server/src/request_processors/process_exec_processor.rs | immediate response、connection handle、control、exited |
| Thread shell入口 | codex-rs/app-server/src/request_processors/thread_processor.rs | trim/local host、Op::RunUserShellCommand、immediate response |
| 后台终端API | codex-rs/app-server/src/request_processors/thread_processor.rs | list分页、ID转换、terminate bool、clean Core Op |
| Item事件翻译 | codex-rs/app-server/src/bespoke_event_handling.rs | ExecCommand begin/delta/end live fan-out |
| Agent exec handler | codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs | args、environment、process ID、yield与tool output |
| Unified manager | codex-rs/core/src/unified_exec/process_manager.rs | store-before-wait、list、terminate、prune、terminate_all |
| Unified process | codex-rs/core/src/unified_exec/process.rs | local/remote handle、broadcast、state watch、interaction lock、Drop |
| Clean Core handler | codex-rs/core/src/session/handlers.rs | Op::CleanBackgroundTerminals到close_unified_exec_processes |
| 公共后台信息 | codex-rs/core/src/codex_thread.rs | BackgroundTerminalInfo、list/terminate delegation |
| PTY工具层 | codex-rs/utils/pty/src/lib.rs | spawn PTY/pipe、TerminalSize、ProcessHandle |
| App-server执行测试 | codex-rs/app-server/tests/suite/v2/command_exec.rs | buffered/streaming/stdio/timeout/disconnect/platform行为 |

## 第七阶段：App-server Environment、本地与远程执行、路径、文件系统与能力边界

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| App-server环境协议 | codex-rs/app-server-protocol/src/protocol/v2/environment.rs | add/info/status、shell、cwd PathUri与四种status |
| Thread环境入口 | codex-rs/app-server-protocol/src/protocol/v2/thread.rs | omitted/default、empty/disabled、non-empty/current合同 |
| Turn环境入口 | codex-rs/app-server-protocol/src/protocol/v2/turn.rs | TurnEnvironmentParams、sticky三态、每环境cwd/roots |
| 参数到selection | codex-rs/app-server/src/request_processors.rs | LegacyAppPathString推断、绝对路径拒绝、roots保序去重 |
| Turn兼容fallback | codex-rs/app-server/src/request_processors/turn_processor.rs | build_environment_override、legacy_fallback_cwd、顶层roots |
| 环境RPC处理 | codex-rs/app-server/src/request_processors/environment_processor.rs | unknown/info error映射、status无恢复观察 |
| Manager注册表 | codex-rs/exec-server/src/environment.rs | default IDs、local保留、from snapshot、upsert与provisioning |
| Environment能力束 | codex-rs/exec-server/src/environment.rs | Local/Remote Process、FileSystem、HTTP、info/status |
| 配置来源 | codex-rs/exec-server/src/environment_provider.rs | provider snapshot、default与include_local |
| TOML配置 | codex-rs/exec-server/src/environment_toml.rs | environments.toml解析、ID/default校验 |
| Exec-server wire | codex-rs/exec-server-protocol/src/protocol.rs | initialize、environment info/capabilities、exec/fs PathUri |
| Turn环境状态机 | codex-rs/core/src/environment_selection.rs | update、resolution、Ready/Starting、primary/local/snapshot |
| 单轮环境上下文 | codex-rs/core/src/session/turn_context.rs | TurnEnvironment、cwd、roots、shell、permission profile |
| 通用工具选择 | codex-rs/core/src/tools/handlers/mod.rs | environment_id显式查找与省略使用primary |
| Unified exec路由 | codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs | foreign cwd、remote shell、backend、Sandbox限制 |
| Apply patch路由 | codex-rs/core/src/tools/handlers/apply_patch.rs | 多环境ID、filesystem、per-environment permissions |
| 图片读取路由 | codex-rs/core/src/tools/handlers/view_image.rs | 选择环境并通过其filesystem读取远程图片 |
| 等待异步环境 | codex-rs/core/src/tools/handlers/wait_for_environment.rs | Starting查找、wait、失败后继续合同 |
| App直调文件API | codex-rs/app-server/src/request_processors/fs_processor.rs | local-only accessor与App fs/*边界 |
| 远程进程后端 | codex-rs/exec-server/src/remote_process.rs | exec-server process RPC与控制handle |
| 远程文件后端 | codex-rs/exec-server/src/remote_file_system.rs | PathUri、fs/* RPC、base64与错误映射 |
| 连接与恢复 | codex-rs/exec-server/src/client.rs | lazy client、initialized connection与请求路由 |
| 恢复状态机 | codex-rs/exec-server/src/client_recovery.rs | startup failure、断线与正常使用恢复 |
| 跨平台路径核心 | codex-rs/utils/path-uri/src/lib.rs | file URI、PathConvention、equality/hash、native转换 |
| Legacy路径桥 | codex-rs/utils/path-uri/src/api_path_string.rs | 原样保存、语法推断、target-native渲染 |
| 跨环境集成测试 | codex-rs/core/tests/suite/remote_env.rs | local/remote工具、Windows/POSIX与断线行为 |

## 第七阶段：Exec-server 协议、传输、握手、Session 恢复、Noise 与版本偏差

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Exec-server整体合同 | codex-rs/exec-server/README.md | transport、握手、process/fs方法、relay format与生命周期 |
| 协议crate入口 | codex-rs/exec-server-protocol/src/lib.rs | exports与MINIMUM_SUPPORTED_CODEX_VERSION |
| JSON-RPC envelope | codex-rs/exec-server-protocol/src/rpc.rs | 四类消息、RequestId、重复key拒绝、256K node cap |
| Typed方法payload | codex-rs/exec-server-protocol/src/protocol.rs | initialize、process/fs/http/environment、defaults与capabilities |
| Client总状态 | codex-rs/exec-server/src/client.rs | Lazy startup、connection state、session routes、seq reorder、HTTP streams |
| Transport选择 | codex-rs/exec-server/src/client_transport.rs | WebSocket/stdio/Noise打开、fresh bundle与reconnect strategy |
| Client恢复 | codex-rs/exec-server/src/client_recovery.rs | resume handshake、process/read replay与失败cleanup |
| WS/stdio适配 | codex-rs/exec-server/src/connection.rs | channel、framing、64MiB、Ping、stdio process tree owner |
| RpcClient | codex-rs/exec-server/src/rpc.rs | pending map、regular/cleanup slots、有序disconnect drain |
| Server反向RPC | codex-rs/exec-server/src/rpc_server_requests.rs | 256 in-flight、oneshot callback、timeout与close |
| Connection processor | codex-rs/exec-server/src/server/processor.rs | reader/dispatcher/outbound tasks与shutdown顺序 |
| 请求dispatcher | codex-rs/exec-server/src/server/request_dispatcher.rs | Inline/Concurrent、ordinary/control lanes与handshake gate |
| Typed router | codex-rs/exec-server/src/server/registry.rs | method到handler的完整注册表 |
| Connection handler | codex-rs/exec-server/src/server/handler.rs | initialize flags、session、fs/http与initialized前置条件 |
| Session registry | codex-rs/exec-server/src/server/session_registry.rs | UUID、single attach、detach、30秒TTL与shutdown |
| Process adapter | codex-rs/exec-server/src/server/process_handler.rs | process RPC到executor-local LocalProcess |
| Filesystem adapter | codex-rs/exec-server/src/server/file_system_handler.rs | file handles、50K directory cap、Sandbox与IO error mapping |
| 远程文件stream | codex-rs/exec-server/src/remote_file_stream.rs | UUID handle、block offset、EOF、Drop close |
| Noise密码通道 | codex-rs/exec-server/src/noise_channel.rs | identity、hybrid IK、prologue、encrypt/decrypt |
| Harness relay | codex-rs/exec-server/src/noise_relay/harness.rs | pinned key握手、record循环、背压、Ping/Pong |
| Executor虚拟流 | codex-rs/exec-server/src/noise_relay/executor_stream.rs | per-stream ConnectionProcessor、try_send隔离、instance ID |
| Noise消息framing | codex-rs/exec-server/src/noise_relay/message_framing.rs | 4-byte length、60KiB records、64MiB reassembly cap |
| Ciphertext排序 | codex-rs/exec-server/src/noise_relay/ordered_ciphertext.rs | reorder distance 64、1MiB cap、decrypt前去重 |
| Relay protobuf | codex-rs/exec-server/src/proto/codex.exec_server.relay.v1.proto | data/ack/resume/reset/heartbeat/handshake字段 |
| Pong watchdog | codex-rs/exec-server/src/websocket_pong_watchdog.rs | 60秒deadline、write deadline与received_pong |
| 跨版本脚本 | codex-rs/exec-server/testing/run_version_skew.sh | 下载release、current/minimum/latest组合 |
| 跨版本集成测试 | codex-rs/exec-server/tests/relay/version_skew.rs | current↔released、mock registry、真实Noise与密文断言 |

## 第七阶段：App-server 配置 API、Layer Stack、来源、写入、Requirements 与运行时刷新

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| Config v2 wire类型 | codex-rs/app-server-protocol/src/protocol/v2/config.rs | ConfigLayerSource、ConfigReadResponse、MergeStrategy、WriteStatus、requirements与write params |
| RPC方法与并发域 | codex-rs/app-server-protocol/src/protocol/common.rs | config/read共享读、value/batch全局写、requirements read |
| 请求总分派 | codex-rs/app-server/src/message_processor.rs | 四类config request到ConfigRequestProcessor |
| Processor读写协调 | codex-rs/app-server/src/request_processors/config_processor.rs | runtime feature投影、cache失效、plugin telemetry、session-default分类 |
| Thread热刷新 | codex-rs/app-server/src/request_processors/config_processor.rs | reload_user_config、逐Thread重建、refresh_runtime_config |
| ConfigManager构造 | codex-rs/app-server/src/config_manager.rs | loader overrides、CLI/runtime feature、cwd与thread配置加载 |
| App-server配置服务 | codex-rs/app-server/src/config_manager_service.rs | read、apply_edits、校验、持久化、okOverridden与错误分类 |
| Layer到wire转换 | codex-rs/app-server/src/config_layer.rs | domain source/metadata/layer到API类型显式映射 |
| Layer领域类型 | codex-rs/config/src/config_layer_source.rs | 八类来源、precedence与可读格式 |
| Layer Stack状态 | codex-rs/config/src/state.rs | active user、effective_config、origins、disabled层与迭代顺序 |
| Canonical loader | codex-rs/config/src/loader/mod.rs | system/user/profile/project/session/managed层构造与项目denylist |
| Loader设计说明 | codex-rs/config/src/loader/README.md | 层顺序、effective/origins/version与内部模块导航 |
| Layer文件读取 | codex-rs/config/src/loader/layer_io.rs | user/system/managed/requirements输入与路径处理 |
| CLI覆盖层 | codex-rs/config/src/overrides.rs | dotted path覆盖到SessionFlags TOML层 |
| 递归合并 | codex-rs/config/src/merge.rs | table overlay、alias、network host、shell filter与multi_agent_v2特例 |
| 来源与版本指纹 | codex-rs/config/src/fingerprint.rs | 叶子origin遍历、array index、canonical JSON与SHA-256 |
| Requirements模型 | codex-rs/config/src/config_requirements.rs | RequirementSource、Constrained、allow-list/exact与各管理类别 |
| Requirements组合 | codex-rs/config/src/requirements_layers/stack.rs | 多管理来源合成与优先/交集规则 |
| Config TOML类型 | codex-rs/config/src/config_toml.rs | 持久配置schema、强类型反序列化与flatten字段 |
| Profile-v2类型 | codex-rs/config/src/profile_toml.rs | 命名profile文件及用户层选择 |
| 保格式文本编辑 | codex-rs/core/src/config/edit.rs | ConfigEditsBuilder、SetPath/ClearPath与toml_edit持久化 |
| Feature requirement校验 | codex-rs/core/src/config/mod.rs | deserialize_config_toml_with_base与validate_feature_requirements |
| Config manager单元测试 | codex-rs/app-server/src/config_manager_service_tests.rs | 注释顺序、clear、batch原子性、origin、覆盖、冲突与profile拒绝 |
| Config RPC集成测试 | codex-rs/app-server/tests/suite/v2/config_rpc.rs | JSON-RPC读写、错误payload、requirements与运行时行为 |

## 第七阶段：App-server 模型目录、选择、Reasoning、Service Tier、Provider 能力与刷新

| 想追踪什么 | 从哪里开始 | 重点符号/内容 |
|---|---|---|
| App-server模型wire | codex-rs/app-server-protocol/src/protocol/v2/model.rs | ModelListParams/Response、Model、effort/tier、reroute与safety通知 |
| RPC注册 | codex-rs/app-server-protocol/src/protocol/common.rs | model/list、modelProvider/capabilities/read与模型通知method |
| Model目录处理器 | codex-rs/app-server/src/request_processors/catalog_processor.rs | OnlineIfUncached、hidden过滤、offset cursor与pagination错误 |
| Provider能力处理 | codex-rs/app-server/src/request_processors/config_processor.rs | 最新config、create_model_provider与capabilities投影 |
| Picker模型投影 | codex-rs/app-server/src/models.rs | supported_models、ModelPreset到wire Model、effort/tier映射 |
| 后台目录刷新 | codex-rs/app-server/src/models_refresh_worker.rs | 立即/3分钟Online刷新、Weak owner、CancellationToken与Drop |
| App模型事件翻译 | codex-rs/app-server/src/bespoke_event_handling.rs | Core reroute/verification/moderation/buffering到server notification |
| ModelsManager核心 | codex-rs/models-manager/src/manager.rs | RefreshStrategy、auth过滤、默认选择、remote merge、ETag与fallback |
| 模型缓存抽象 | codex-rs/models-manager/src/cache.rs | ModelsCache、entry、TTL/client-version资格与文件读写 |
| ModelInfo配置修正 | codex-rs/models-manager/src/model_info.rs | context clamp、truncation、instructions/personality与未知slug fallback |
| Picker历史兼容 | codex-rs/models-manager/src/model_presets.rs | 移除硬编码preset后保留的migration config keys |
| ModelManager配置 | codex-rs/models-manager/src/config.rs | context、compact、tool-output与instructions override输入 |
| 核心模型数据 | codex-rs/protocol/src/openai_models.rs | ReasoningEffort Custom、modality defaults、ModelInfo/Preset与auth/default规则 |
| Provider目录端点 | codex-rs/model-provider/src/models_endpoint.rs | auth解析、5秒timeout、route-aware client与请求telemetry |
| `/models` HTTP客户端 | codex-rs/codex-api/src/endpoint/models.rs | client_version query、ETag header与ModelsResponse解析 |
| Provider构造 | codex-rs/model-provider/src/provider.rs | Static/OpenAI manager、cache策略与auth manager注入 |
| Thread启动模型解析 | codex-rs/core/src/session/mod.rs | refresh strategy、get_default_model、ModelInfo、service tier与SessionConfiguration |
| Turn模型上下文 | codex-rs/core/src/session/turn_context.rs | effective effort、with_model兼容fallback与ModelInfo defaults |
| Thread运行设置 | codex-rs/core/src/session/handlers.rs | ThreadSettingsOverrides到CollaborationMode model/effort更新 |
| Session设置状态 | codex-rs/core/src/session/session.rs | collaboration mode、service tier规范化与config snapshot |
| 采样目录更新事件 | codex-rs/core/src/session/turn.rs | ServerModel、verification、buffering、ModelsEtag与refresh_if_new_etag |
| Thread start协议 | codex-rs/app-server-protocol/src/protocol/v2/thread.rs | model/provider/fallback/serviceTier输入与实际解析值响应 |
| Turn start协议 | codex-rs/app-server-protocol/src/protocol/v2/turn.rs | sticky model/effort/summary/tier与collaborationMode优先级 |
| Model list集成测试 | codex-rs/app-server/tests/suite/v2/model_list.rs | 可见/隐藏、远端权威、custom effort顺序、分页与cursor错误 |
| Provider能力测试 | codex-rs/app-server/tests/suite/v2/model_provider_capabilities_read.rs | 默认Provider与Bedrock capability差异 |
| Manager行为测试 | codex-rs/models-manager/src/manager_tests.rs | cache、ETag、auth、default、static fallback与metadata匹配 |
| Cache行为测试 | codex-rs/models-manager/src/cache.rs | fresh/stale/version与TTL renewal单元行为 |

## 源码精读系列 01：`ConfigManager::apply_edits`

| 精读问题 | 固定提交中的路径 | 重点符号/行 |
|---|---|---|
| 单项与批量怎样归一 | codex-rs/app-server/src/config_manager_service.rs | `write_value` 185—192、`batch_write` 231—243 |
| 主协调函数 | codex-rs/app-server/src/config_manager_service.rs | `apply_edits` 245—448 |
| 可写路径保护 | codex-rs/app-server/src/config_manager_service.rs | `user_config_path`、`paths_match` 251—265 |
| 用户层缺失处理 | codex-rs/app-server/src/config_manager_service.rs | `create_empty_user_layer`、`write_empty_user_config` 458—501 |
| 版本冲突 | codex-rs/app-server/src/config_manager_service.rs | `expected_version.as_deref` 276—283 |
| key path 解析 | codex-rs/app-server/src/config_manager_service.rs | `parse_key_path` 513—558 |
| JSON 到 TOML 与清除 | codex-rs/app-server/src/config_manager_service.rs | `parse_value`、`clear_path` |
| Replace/Upsert | codex-rs/app-server/src/config_manager_service.rs | `apply_merge`、`sparse_overlay` |
| 表示切换 | codex-rs/app-server/src/config_manager_service.rs | `shell_environment_policy_representation_switch` |
| 三阶段整体校验 | codex-rs/app-server/src/config_manager_service.rs | `validate_config`、feature requirements、`effective_config` 378—417 |
| 唯一目标编辑提交点 | codex-rs/app-server/src/config_manager_service.rs | `ConfigEditsBuilder::apply` 419—425 |
| 保格式路径编辑 | codex-rs/core/src/config/edit.rs | `ConfigEdit`、`ConfigEditsBuilder` |
| 用户层替换与有效配置 | codex-rs/config/src/state.rs | `with_user_config`、`effective_config` |
| 内容版本 | codex-rs/config/src/fingerprint.rs | `version_for_toml`、`canonical_json` |
| 覆盖解释 | codex-rs/app-server/src/config_manager_service.rs | `compute_override_metadata`、`first_overridden_edit` |
| 直接测试证据 | codex-rs/app-server/src/config_manager_service_tests.rs | comments/no-op/batch/version/requirements/upsert/override |

## 源码精读系列 02：`ModelsManager::list_models`

| 精读问题 | 固定提交中的路径 | 重点符号/行 |
|---|---|---|
| JSON-RPC 入口与分页 | codex-rs/app-server/src/request_processors/catalog_processor.rs | `model_list` 159—170、`list_models` 255—306 |
| picker 到 wire | codex-rs/app-server/src/models.rs | `supported_models`、`model_from_preset` |
| manager 总合同 | codex-rs/models-manager/src/manager.rs | `ModelsManager` 83—209 |
| 活动状态与构造 | codex-rs/models-manager/src/manager.rs | `OpenAiModelsManager` 216—284 |
| raw catalog 降级 | codex-rs/models-manager/src/manager.rs | `raw_model_catalog` 339—354 |
| ETag 重新验证 | codex-rs/models-manager/src/manager.rs | `refresh_if_new_etag` 356—372 |
| 三种刷新策略 | codex-rs/models-manager/src/manager.rs | `refresh_available_models` 374—410 |
| endpoint 与缓存写回 | codex-rs/models-manager/src/manager.rs | `fetch_and_update_models` 412—435 |
| remote-only 与 merge | codex-rs/models-manager/src/manager.rs | `apply_remote_models` 445—475 |
| cache 应用 | codex-rs/models-manager/src/manager.rs | `try_load_cache` 477—516 |
| 静态 provider 差异 | codex-rs/models-manager/src/manager.rs | `StaticModelsManager` impl 519—591 |
| 默认模型 | codex-rs/models-manager/src/manager.rs | `default_model_from_available`、两个 `get_default_model` |
| metadata 匹配 | codex-rs/models-manager/src/manager.rs | longest-prefix、namespace suffix、fallback |
| cache trait | codex-rs/models-manager/src/cache.rs | `ModelsCache`、`ModelsCacheEntry` |
| 文件 freshness | codex-rs/models-manager/src/cache.rs | `load_fresh_file`、`is_fresh`、`refresh_ttl` |
| 三分钟后台刷新 | codex-rs/app-server/src/models_refresh_worker.rs | `spawn_with_interval`、Weak、CancellationToken |
| ETag 事件触发 | codex-rs/core/src/session/turn.rs | `refresh_if_new_etag` 调用 |
| 行为测试 | codex-rs/models-manager/src/manager_tests.rs | cache/auth/source-of-truth/default/metadata tests |
| API 集成测试 | codex-rs/app-server/tests/suite/v2/model_list.rs | hidden、pagination、remote authority |

## 源码精读系列 03：`Session::spawn` 与 `Session::new`

| 精读问题 | 固定提交中的路径 | 重点符号/行 |
|---|---|---|
| app-server 启动入口 | codex-rs/app-server/src/request_processors/thread_processor.rs | `thread_start`、`thread_start_inner` |
| Core 统一启动入口 | codex-rs/core/src/thread_manager.rs | `start_thread`、`spawn_thread` 1651—1791 |
| 已运行 resume 去重 | codex-rs/core/src/thread_manager.rs | active resumed thread 分支 1685—1705 |
| Session 参数交接 | codex-rs/core/src/session/mod.rs | `SessionSpawnArgs` |
| Session IO 合同 | codex-rs/core/src/session/mod.rs | `SessionIo`、submission/event/status/termination |
| 外层 spawn 与 tracing | codex-rs/core/src/session/mod.rs | `Session::spawn` 493—515 |
| channel、policy 与模型解析 | codex-rs/core/src/session/mod.rs | `spawn_internal` |
| base instructions 与 dynamic tools | codex-rs/core/src/session/mod.rs | config/history/model 优先级、rollout 恢复 |
| Session 初始运行合同 | codex-rs/core/src/session/session.rs | `SessionConfiguration` |
| 重型初始化协调 | codex-rs/core/src/session/session.rs | `Session::new` 511 起 |
| thread/session id 血缘 | codex-rs/core/src/session/session.rs | root、subagent、resumed lineage 分支 |
| 并行初始化与持久化守卫 | codex-rs/core/src/session/session.rs | `tokio::join!`、`LiveThreadInitGuard` |
| 可变 Session 状态 | codex-rs/core/src/state/session.rs | `SessionState` |
| 生命周期服务集合 | codex-rs/core/src/state/service.rs | `SessionServices` |
| 上层 thread 句柄 | codex-rs/core/src/codex_thread.rs | `CodexThread` |
| 首事件与发布注册 | codex-rs/core/src/thread_manager.rs | `finalize_thread_spawn` 1794—1837 |
| 新建与 ephemeral 合同测试 | codex-rs/app-server/tests/suite/v2/thread_start.rs | create、pathless |
| required/optional MCP 测试 | codex-rs/app-server/tests/suite/v2/thread_start.rs | required failure、optional status |
| Session id 与初始化测试 | codex-rs/core/src/session/tests.rs | ids、config、shell、history |
| starting 阶段并发测试 | codex-rs/core/src/thread_manager_tests.rs | MCP invalidation、shared services |

## 源码精读系列 04：`run_turn`

| 精读问题 | 固定提交中的路径 | 重点符号/行 |
|---|---|---|
| app-server turn/start | codex-rs/app-server/src/request_processors/turn_processor.rs | `turn_start_inner` 约 472—594 |
| Op 构造与公开 turn id | codex-rs/app-server/src/request_processors/turn_processor.rs | `Op::UserInput`、submission id |
| 输入接纳与 steer 分流 | codex-rs/core/src/session/handlers.rs | `user_input_or_turn_inner` |
| Turn 级配置快照 | codex-rs/core/src/session/turn_context.rs | `TurnContext`、`new_turn_with_sub_id` |
| Step 级动态快照 | codex-rs/core/src/session/step_context.rs | `StepContext` |
| 捕获工具和环境视图 | codex-rs/core/src/session/mod.rs | `capture_step_context*` 3064 起 |
| 活动 Turn 状态 | codex-rs/core/src/state/turn.rs | `ActiveTurn`、`RunningTask`、`TurnState` |
| task 创建与统一收尾 | codex-rs/core/src/tasks/mod.rs | `spawn_task`、`start_task`、`on_task_finished` |
| RegularTask 外层循环 | codex-rs/core/src/tasks/regular.rs | `RegularTask::run` |
| Agent 主循环 | codex-rs/core/src/session/turn.rs | `run_turn` 151—558 |
| prompt 组装 | codex-rs/core/src/session/turn.rs | `build_prompt` 1285 起 |
| sampling 与网络重试 | codex-rs/core/src/session/turn.rs | `run_sampling_request` 1313 起 |
| 单次 response stream | codex-rs/core/src/session/turn.rs | `try_run_sampling_request` 2142 起 |
| tool futures 回填 | codex-rs/core/src/session/turn.rs | `drain_in_flight` 2093 起 |
| response item/tool 分流 | codex-rs/core/src/stream_events_utils.rs | `handle_output_item_done` |
| 工具调用规范化 | codex-rs/core/src/tools/router.rs | `ToolRouter::build_tool_call` |
| handler 路由 | codex-rs/core/src/tools/router.rs | `dispatch_tool_call_with_terminal_outcome` |
| 并行/串行执行门 | codex-rs/core/src/tools/parallel.rs | `ToolCallRuntime`、RwLock read/write |
| extension Turn 生命周期 | codex-rs/core/src/tasks/lifecycle.rs | start/error/stop/abort/idle |
| 公开 Turn 行为测试 | codex-rs/app-server/tests/suite/v2/turn_start.rs | raw completion、profile、overrides |
| 工具并行和顺序测试 | codex-rs/core/tests/suite/tool_parallelism.rs | duration、grouped outputs |
| pending/steer 行为测试 | codex-rs/core/tests/suite/pending_input.rs | pending input、compact、follow-up |
| 工具取消测试 | codex-rs/core/tests/suite/abort_tasks.rs | aborted output、terminal event |

## 源码精读系列 05：`ToolRouter` 与 `ToolRegistry`

| 精读问题 | 固定提交中的路径 | 重点符号/行 |
|---|---|---|
| 模型输出规范化 | codex-rs/core/src/tools/router.rs | `ToolRouter::build_tool_call` |
| 单次工具总分派 | codex-rs/core/src/tools/router.rs | `dispatch_tool_call_with_terminal_outcome` |
| handler 查找 | codex-rs/core/src/tools/registry.rs | `ToolRegistry`、handler map |
| 工具公开快照 | codex-rs/core/src/tools/spec.rs、spec_plan.rs | spec 构造与 Feature/环境选择 |
| 并行与串行门 | codex-rs/core/src/tools/parallel.rs | `ToolCallRuntime`、读写锁 |
| Hook 包装 | codex-rs/core/src/tools/router.rs | PreToolUse、PostToolUse |
| 普通 Function handler | codex-rs/core/src/tools/handlers | `ToolHandler` implementations |
| shell/patch 安全编排 | codex-rs/core/src/tools/orchestrator.rs | approval、Sandbox、升级重试 |
| 工具输出回填 | codex-rs/core/src/session/turn.rs | in-flight tool result 回到下一次 sampling |
| 工具并行测试 | codex-rs/core/tests/suite/tool_parallelism.rs | 并行时长、串行顺序和结果次序 |

## 源码精读系列 06：Unified exec

| 精读问题 | 固定提交中的路径 | 重点符号/行 |
|---|---|---|
| 工具参数 schema | codex-rs/core/src/tools/handlers/shell_spec.rs | exec_command、write_stdin |
| exec handler | codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs | `handle_call` |
| stdin/poll handler | codex-rs/core/src/tools/handlers/unified_exec/write_stdin.rs | `handle_call` |
| 进程会话 manager | codex-rs/core/src/unified_exec/process_manager.rs | `exec_command`、`write_stdin`、store |
| approval/Sandbox | 同上 | `open_session_with_sandbox` |
| 本地/远端 runtime | codex-rs/core/src/tools/runtimes/unified_exec.rs | `UnifiedExecRuntime` |
| process transport | codex-rs/core/src/unified_exec/process.rs | `ProcessHandle`、output tasks |
| 输出与退出 watcher | codex-rs/core/src/unified_exec/async_watcher.rs | streaming、终态发布 |
| 有界首尾缓冲 | codex-rs/core/src/unified_exec/head_tail_buffer.rs | `HeadTailBuffer` |
| Session shutdown | codex-rs/core/src/session/handlers.rs | unified exec teardown |
| 行为测试 | codex-rs/core/src/unified_exec/*tests.rs | yield、poll、remote、terminate、prune |

## 源码精读系列 07：App-server JSON-RPC 分派

| 精读问题 | 固定提交中的路径 | 重点符号/行 |
|---|---|---|
| 四类 envelope | codex-rs/app-server-protocol/src/rpc.rs | `JSONRPCMessage` 与四种结构 |
| typed request 生成 | codex-rs/app-server-protocol/src/protocol/common.rs | `client_request_definitions!` |
| 通用到 typed 转换 | 同上 | `TryFrom<JSONRPCRequest> for ClientRequest` 261—279 |
| server request 类型 | 同上 | `server_request_definitions!`、`ServerRequestPayload` |
| stdio JSONL framing | codex-rs/app-server-transport/src/transport/stdio.rs | reader 43—80、writer 82—98 |
| transport 消息解析 | codex-rs/app-server-transport/src/transport/mod.rs | `forward_incoming_message` 202—217 |
| 入站过载保护 | 同上 | `enqueue_incoming_message` 219—257 |
| 四类消息主分叉 | codex-rs/app-server/src/lib.rs | IncomingMessage match 1021—1109 |
| request 上下文与 typed 入口 | codex-rs/app-server/src/message_processor.rs | `process_request` 538—590 |
| initialize 特殊分支 | 同上 | `handle_client_request` 774—817 |
| 初始化和实验门 | 同上 | `dispatch_initialized_client_request` 819—882 |
| typed handler 总路由 | 同上 | `handle_initialized_client_request` 884—1501 |
| peer response/error | 同上 | `process_response`、`process_error` 761—772 |
| 连接关闭排空 | 同上 | `connection_closed` 727—754 |
| 双重请求标识 | codex-rs/app-server/src/outgoing_message.rs | `ConnectionRequestId`、`RequestContext` |
| server request callback | 同上 | `send_request_to_connections` 287—351 |
| client response 唤醒 | 同上 | `notify_client_response/error` 374—411 |
| success/error 定向回包 | 同上 | `send_response_as_inner`、`send_error_inner` |
| notification 定向/广播 | 同上 | `send_server_notification_to_connections` |
| outbound 连接路由 | codex-rs/app-server/src/transport.rs | `route_outgoing_envelope` 200—239 |
| transport 测试 | codex-rs/app-server-transport/src/transport/*tests.rs | 连接与消息转发 |
| 双向请求集成测试 | codex-rs/app-server/tests/suite/v2/dynamic_tools.rs | server request、client response、Core continuation |

## 源码精读系列 08：Serialization scope

| 精读问题 | 固定提交中的路径 | 重点符号/行 |
|---|---|---|
| scope 类型 | codex-rs/app-server-protocol/src/protocol/common.rs | `ClientRequestSerializationScope` 119—130 |
| scope 表达式 | 同上 | `serialization_scope_expr!` 132—197 |
| method 并发声明 | 同上 | `client_request_definitions!` 调用中的 `serialization:` |
| scope getter | 同上 | 宏生成的 `serialization_scope` 247—258 |
| keyed/unkeyed 分类测试 | 同上 | `client_request_serialization_scope_*` 2010 起 |
| queue key/access | codex-rs/app-server/src/request_serialization.rs | `RequestSerializationQueueKey`、`RequestSerializationAccess` |
| connection id 补入 | 同上 | `from_scope` 53—104 |
| handler future wrapper | 同上 | `QueuedInitializedRequest` 106—136 |
| queue map | 同上 | `RequestSerializationQueues` 143—146 |
| background work | 同上 | `enqueue_background` 148—162 |
| enqueue 与唯一 drain | 同上 | `enqueue` 164—192 |
| FIFO/SharedRead 批次 | 同上 | `drain` 194—226 |
| 同 key FIFO 测试 | 同上 | `same_key_requests_run_fifo` |
| 不同 key 并发测试 | 同上 | `different_keys_run_concurrently` |
| gate 跳过测试 | 同上 | `closed_gate_request...`、`shutdown_of_live_gate...` |
| SharedRead 批次测试 | 同上 | `same_key_shared_reads_run_concurrently` |
| write 等 read 测试 | 同上 | `exclusive_write_waits_for_running_shared_reads` |
| writer fairness 测试 | 同上 | `later_shared_reads_do_not_jump_ahead_of_queued_write` |
| 总接入点 | codex-rs/app-server/src/message_processor.rs | `dispatch_initialized_client_request` 819—882 |
| connection gate | codex-rs/app-server/src/connection_rpc_gate.rs | `run`、`close`、`shutdown` |
| 断线关闭与排空 | codex-rs/app-server/src/lib.rs、message_processor.rs | ConnectionClosed、30 秒 drain timeout |
| 后台 config 写入 | codex-rs/app-server/src/effective_plugin_change.rs | `enqueue_background(Global("config"), Exclusive, ...)` |

## 源码精读系列 09：Rollout resume

| 精读问题 | 固定提交中的路径 | 重点符号/行 |
|---|---|---|
| 公开 resume 入口 | codex-rs/app-server/src/request_processors/thread_processor.rs | `thread_resume` 519—536、`thread_resume_inner` 3093 起 |
| closing 与参数保护 | 同上 | `pending_thread_unloads`、sandbox/permissions 互斥 3101—3127 |
| 热恢复总分支 | 同上 | `resume_running_thread` 3545 起 |
| 活动 history/path 冲突 | 同上 | running history rejection、active path validation 3552—3610 |
| 活动 override 处理 | 同上 | mismatch、idle cache shutdown、rejoin 3611—3654 |
| listener 协调响应 | 同上 | `SendThreadResumeResponse` 3774—3797 |
| 冷恢复历史来源 | 同上 | request history / StoredThread / id/path 3177—3200 |
| Paginated 模型上下文 | 同上 | `load_resume_initial_history_from_stored_thread` 3819—3853 |
| id/path 查找与 archived 拒绝 | 同上 | `read_stored_thread_for_resume` 3855—3893 |
| 客户端 history 的语义 | 同上 | `resume_thread_from_history` 3803—3817，构造 Forked |
| config 与 persisted metadata | 同上 | history cwd、overrides、`load_and_apply_persisted_resume_metadata` |
| cold Session 启动 | codex-rs/core/src/thread_manager.rs | `resume_thread_with_history`、`spawn_thread` |
| 已运行 Core 去重 | 同上 | `InitialHistory::Resumed` fast path 1685—1705 |
| 恢复历史类型 | codex-rs/protocol/src/protocol.rs | `ResumedHistory`、`InitialHistory` 2567—2685 |
| rollout item 联合类型 | 同上 | `RolloutItem` 3211—3226、`RolloutLine` 3406—3413 |
| compaction checkpoint | 同上 | `CompactedItem` 3246—3276 |
| Turn 设置快照 | 同上 | `TurnContextItem` 3285—3351 |
| Session/thread 身份 | codex-rs/core/src/session/session.rs | resumed thread/session id 570—596 |
| 恢复持久化句柄 | 同上 | `ResumeThreadParams`、`LiveThread::resume` 700—721 |
| 初始历史安装 | codex-rs/core/src/session/mod.rs | `record_initial_history` 1295 起 |
| Interrupted/model/token 恢复 | 同上 | resumed 分支 1322 起 |
| reconstruction 安装 | 同上 | `apply_rollout_reconstruction` 1429—1489 |
| 反向分段扫描 | codex-rs/core/src/session/rollout_reconstruction.rs | `reconstruct_history_from_rollout` 112 起 |
| compaction/rollback 正向重放 | 同上 | history materialization 317—373 |
| world-state 重放 | 同上 | full/patch baseline 387—422 |
| Paginated 反向模型扫描 | codex-rs/thread-store/src/local/model_context.rs | `load_latest_model_context`、`scan_model_context_from_lineage_blocking` |
| LiveThread 恢复 | codex-rs/thread-store/src/live_thread.rs | `LiveThread::resume` 146—195 |
| 同路径 recorder 续写 | codex-rs/thread-store/src/local/live_writer.rs | `resume_thread` 40—111 |
| JSONL 与 SQLite 权威关系 | codex-rs/app-server/src/request_processors/thread_processor.rs | paginated persist 注释与调用 3318—3330 |
| API response 与 listener | 同上 | response 构造、attach、token usage 3342—3503 |
| App-server 行为测试 | codex-rs/app-server/tests/suite/v2/thread_resume.rs | running rejoin、path、override、updatedAt、writer ownership、excludeTurns |
| Core 重建测试 | codex-rs/core/src/session/rollout_reconstruction_tests.rs | rollback、compaction、incomplete turn、world-state |
| 模型上下文扫描测试 | codex-rs/thread-store/src/local/model_context_tests.rs | bounded checkpoint 与 full-history fallback |
| recorder 恢复测试 | codex-rs/rollout/src/recorder_tests.rs | ordinal gap、unsafe tail、subagent prefix |

## 源码精读系列 10：MCP tool call 生命周期

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| thread-owned 运行时入口 | codex-rs/codex-mcp/src/runtime.rs | `McpRuntimeInput`、`McpRuntime`、`PublishedMcpRuntime` |
| 原子 publication | 同上 | `replace`、`replace_fresh`、`publish` |
| 当前 binding 获取 | 同上 | `current_binding_with_required_servers`、cached binding |
| 期望状态与 server 投影 | codex-rs/core/src/session/mcp_runtime.rs | `McpDesiredState`、effective servers、Eager/LazyWhenCached |
| runtime 刷新协调 | codex-rs/core/src/session/mcp_refresh.rs | dirty flag、semaphore gate、invalidation guard |
| 连接启动、复用与状态 | codex-rs/codex-mcp/src/connection_manager.rs | `McpConnectionSet`、reuse identity、startup status |
| required/optional 发布边界 | 同上 | publication gate、optional grace、failed server views |
| initialize 与 server instructions | codex-rs/codex-mcp/src/rmcp_client.rs | startup timeout、initialize、instructions |
| 有界分页 `tools/list` | 同上 | pagination、tool/count limits、conversion、cache |
| 工具目录与执行句柄 | codex-rs/codex-mcp/src/connection_manager/tool_catalog.rs | list/capture binding、exact clients |
| hard refresh 与 revision | 同上 | catalog revision write lock、cache replacement |
| Step 绑定快照 | codex-rs/codex-mcp/src/binding.rs | `McpBinding`、catalog、execution handles |
| 已准备调用 | 同上 | `PreparedMcpCall`、exact client、captured authority |
| 防止过期执行 | 同上 | `call_with_preparation`、revision read lease、stale rejection |
| Core 调用准备入口 | codex-rs/core/src/session/mcp_runtime.rs | `prepare_mcp_call` |
| Step 持有的 MCP 状态 | codex-rs/core/src/session/step_context.rs | `StepContext.mcp`、`ToolRouter` |
| 普通 MCP 与 Apps 暴露 | codex-rs/core/src/mcp_tool_exposure.rs | Direct/Deferred/Hidden、预算、handler cache |
| 工具规格进入 registry | codex-rs/core/src/tools/spec_plan.rs | MCP `ToolSpec` 追加与 handler 注册 |
| MCP handler 适配 | codex-rs/core/src/tools/handlers/mcp.rs | `McpHandler`、`ToolExecutor`、parallel hint |
| 调用主流程 | codex-rs/core/src/mcp_tool_call.rs | `handle_mcp_tool_call`、arguments parse、started/completed event |
| Apps policy 与审批 | 同上 | approval mode、Guardian、permission hook、decision reuse |
| 调用前 preparation | 同上 | metadata、sandbox state、file path rewrite、catalog lease |
| 真实远端调用 | codex-rs/codex-mcp/src/binding.rs | exact client `call_tool` |
| 结果净化与事件截断 | codex-rs/core/src/mcp_tool_call.rs | modality sanitization、UI event budget |
| 模型结果回填 | codex-rs/core/src/tools/context.rs | `McpToolOutput` → `ResponseInputItem::FunctionCallOutput` |
| `tools/list_changed` 固定行为 | codex-rs/rmcp-client/src/logging_client_handler.rs | `on_tool_list_changed` 仅记录日志 |
| binding 一致性测试 | codex-rs/codex-mcp/src/binding_tests.rs | exact connection、no reroute、stale rejection、lease coverage |
| 连接目录测试 | codex-rs/codex-mcp/src/connection_manager_tests.rs | filters、normalization、cache、optional grace、refresh races |
| Core 调用测试 | codex-rs/core/src/mcp_tool_call_tests.rs | approvals、hooks、Guardian、sanitization、event truncation、metadata |
| 集成测试 | codex-rs/core/tests/suite/mcp_tool_cache.rs | catalog cache 与 refresh 行为 |
| 刷新清理测试 | codex-rs/core/tests/suite/mcp_refresh_cleanup.rs | refresh 后旧资源清理 |
| 暴露策略测试 | codex-rs/core/tests/suite/mcp_tool_exposure.rs | Direct/Deferred/Hidden 与预算 |
| Hook 集成测试 | codex-rs/core/tests/suite/hooks_mcp.rs | MCP permission hooks |
| RMCP 集成测试 | codex-rs/core/tests/suite/rmcp_client.rs | server/client 协议交互 |

## 源码精读系列 11：Context compaction

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| 自动压缩 token 判断 | codex-rs/core/src/session/context_window.rs | `context_window_token_status`、软阈值与硬窗口 |
| PreTurn 触发 | codex-rs/core/src/session/turn.rs | `run_pre_sampling_compact` |
| 模型切换触发 | 同上 | `maybe_run_previous_model_inline_compact`、comp hash、downshift |
| MidTurn 触发 | 同上 | post-sampling `should_roll_over`、follow-up gate |
| 实现路线选择 | 同上 | `run_auto_compact`、TokenBudget、remote v1/v2、local |
| 手动任务分派 | codex-rs/core/src/tasks/compact.rs | `CompactTask::run` |
| Local 生命周期 | codex-rs/core/src/compact.rs | `run_compact_task_inner`、pre/post hooks、UI item |
| Local 摘要请求 | 同上 | `run_compact_task_inner_impl`、`drain_to_completed` |
| Local 用户消息收集 | 同上 | `collect_user_messages`、`is_summary_message` |
| Local replacement 构造 | 同上 | `build_compacted_history`、20k user-message budget |
| MidTurn context 插入 | 同上 | `InitialContextInjection`、`insert_initial_context_before_last_real_user_or_summary` |
| Remote v1 请求 | codex-rs/core/src/compact_remote_request.rs | `run_remote_compact_attempt`、`compact_conversation_history` |
| Remote v1 生命周期 | codex-rs/core/src/compact_remote.rs | `run_remote_compact_task_inner_impl` |
| Remote 输出过滤 | 同上 | `process_compacted_history`、`should_keep_compacted_history_item` |
| 尾部工具输出改写 | 同上 | `trim_function_call_history_to_fit_context_window` |
| Remote history group | codex-rs/core/src/compact_remote_history.rs | `HistoryItemGroup`、image resize notice |
| Remote v2 请求准备 | codex-rs/core/src/compact_remote_v2_attempt.rs | `CompactionTrigger`、prompt、client session |
| Remote v2 流校验 | codex-rs/core/src/compact_remote_v2.rs | `collect_compaction_output`、恰好一个 Compaction item |
| v2 replacement 构造 | 同上 | `build_v2_compacted_history`、retained-message budget |
| v2 消息截断 | 同上 | `truncate_retained_messages_for_remote_compaction` |
| 旧模型失败后 fallback | codex-rs/core/src/compact_model_fallback.rs | `should_retry_with_current_model`、telemetry |
| TokenBudget 新窗口 | codex-rs/core/src/compact_token_budget.rs | `start_new_context_window`，无摘要请求 |
| Live history 容器 | codex-rs/core/src/context_manager/history.rs | `ContextManager`、raw/for_prompt/replace |
| Call/output 配对 | codex-rs/core/src/context_manager/normalize.rs | missing output、orphan output、成对删除 |
| 窗口身份与 prefill | codex-rs/core/src/state/auto_compact_window.rs | `AutoCompactWindowIds`、Estimated/ServerObserved |
| 安装与持久化 | codex-rs/core/src/session/mod.rs | `replace_compacted_history`、`start_new_context_window` |
| Durable checkpoint 类型 | codex-rs/protocol/src/protocol.rs | `CompactedItem`、replacement 与 window lineage |
| 旧 rollout 兼容 | codex-rs/protocol/src/compacted_item.rs | legacy numeric `window_id` migration |
| Local 单元测试 | codex-rs/core/src/compact_tests.rs | 用户消息预算、context 插入、stale wrapper 过滤 |
| History 单元测试 | codex-rs/core/src/context_manager/history_tests.rs | pair invariants、synthetic output、modality normalization |
| Local 集成测试 | codex-rs/core/tests/suite/compact.rs | Manual/Auto、Pre/MidTurn、scope、model switch、hooks |
| Remote 集成测试 | codex-rs/core/tests/suite/compact_remote.rs | v1/v2、output trim、checkpoint、context refresh、turn state |
| v1/v2 parity 测试 | codex-rs/core/tests/suite/compact_remote_parity.rs | request/follow-up/replacement parity |
| Resume/Fork 测试 | codex-rs/core/tests/suite/compact_resume_fork.rs | checkpoint 重放、二次压缩、rollback |
| App-server API 测试 | codex-rs/app-server/tests/suite/v2/compaction.rs | `thread/compact/start`、started/completed、错误 ID |

## 源码精读系列 12：Sandbox approval lifecycle

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| shell 参数入口 | codex-rs/core/src/tools/handlers/shell.rs | `ShellCommandHandler::handle_call`、`to_exec_params` |
| shell 安全链入口 | 同上 | `run_exec_like`、显式 escalation guard、apply_patch interception |
| 命令审批请求 | codex-rs/core/src/exec_policy.rs | `ExecApprovalRequest`、`create_exec_approval_requirement_for_command` |
| 命令片段解析 | 同上 | `commands_for_exec_policy`、plain commands、complex fallback |
| 未匹配命令默认决策 | 同上 | `render_decision_for_unmatched_command` |
| Prompt 与 policy 冲突 | 同上 | `prompt_is_rejected_by_policy`、granular rules/sandbox 分离 |
| 危险/安全启发式 | codex-rs/shell-command/src | `is_dangerous_command`、`is_safe_command` |
| amendment 推导 | codex-rs/core/src/exec_policy.rs | prompt/allow rule amendment helpers |
| amendment 持久化 | 同上 | `append_amendment_and_update` |
| 审批回调接入 | codex-rs/core/src/session/handlers.rs | `exec_approval` |
| Session 规则更新 | codex-rs/core/src/session/mod.rs | `persist_execpolicy_amendment` |
| Orchestrator 总流程 | codex-rs/core/src/tools/orchestrator.rs | `ToolOrchestrator::run` |
| 单次执行与网络包装 | 同上 | `run_attempt`、immediate/deferred network approval |
| 首次审批三态 | 同上 | Skip、NeedsApproval、Forbidden 分支 |
| 首次 Sandbox 选择 | 同上 | `sandbox_override_for_first_attempt`、`select_initial` |
| 拒绝与升级重试 | 同上 | `SandboxErr::Denied` match、retry approval、second attempt |
| Sandbox telemetry | 同上 | `sandbox_outcome_from_tool_error`、initial/escalated duration |
| 审批 action | codex-rs/core/src/tools/approvals.rs | `ApprovalAction`、Shell/ExecCommand/ApplyPatch |
| Hook/Reviewer 优先级 | 同上 | `Session::request_approval`、`request_reviewer_approval` |
| Guardian 路由 | 同上 | `ApprovalReviewer::for_turn`、`request_guardian_approval` |
| 用户审批 | 同上 | `request_user_approval`、command/patch approval event |
| 审批结果归一化 | 同上 | `ApprovalResolution::into_tool_result` |
| 审批缓存 | codex-rs/core/src/tools/sandboxing.rs | `ApprovalStore`、`with_cached_approval` |
| 默认审批要求 | 同上 | `default_exec_approval_requirement` |
| 首次绕过决策 | 同上 | `SandboxOverride`、`sandbox_override_for_first_attempt` |
| denied-read 不变量 | 同上 | `unsandboxed_execution_allowed`、`sandbox_permissions_preserving_denied_reads` |
| Tool runtime traits | 同上 | `Approvable`、`Sandboxable`、`ToolRuntime` |
| 无 Sandbox 审批资格 | 同上 | `wants_no_sandbox_approval`、`should_bypass_approval` |
| attempt 数据 | 同上 | `SandboxAttempt`、`env_for`、exec-server context |
| shell runtime | codex-rs/core/src/tools/runtimes/shell.rs | `ShellRequest`、`ApprovalKey`、`ShellRuntime` |
| 平台 Sandbox 选择 | codex-rs/sandboxing/src/manager.rs | `SandboxType`、`SandboxManager::should_sandbox/select_initial` |
| 启动请求转换 | 同上 | `SandboxManager::transform`、`SandboxTransformRequest` |
| macOS 实现 | codex-rs/sandboxing/src/seatbelt.rs | Seatbelt profile 与 sandbox-exec 参数 |
| Linux 实现 | codex-rs/sandboxing/src/landlock.rs、bwrap.rs | Landlock/seccomp/bubblewrap 参数 |
| Windows 实现 | codex-rs/sandboxing/src/windows.rs | restricted token Sandbox |
| 拒绝启发式 | codex-rs/sandboxing/src/denial.rs | `is_likely_sandbox_denied` |
| 拒绝错误包装 | codex-rs/core/src/exec.rs | 输出、timeout、signal、`SandboxErr::Denied` |
| ReviewDecision 协议 | codex-rs/protocol/src/protocol.rs | `ReviewDecision`、`AskForApproval`、`GranularApprovalConfig` |
| Exec amendment 协议 | codex-rs/protocol/src/approvals.rs | `ExecPolicyAmendment`、network approval types |
| Sandbox helper 测试 | codex-rs/core/src/tools/sandboxing_tests.rs | default requirement、explicit escalation、denied-read |
| 审批矩阵集成测试 | codex-rs/core/tests/suite/approvals.rs | policy matrix、cache、amendment、fork、denied-read |
| Exec policy 集成测试 | codex-rs/core/tests/suite/exec_policy.rs | forced deny/prompt、Windows backend、empty shell |
| Guardian 测试 | codex-rs/core/tests/suite/guardian_review.rs | allow/deny、reuse、cancel、interrupt |
| 网络审批测试 | codex-rs/core/tests/suite/network_approval.rs | once/session/deny、host/environment scope、Guardian |
| 拒绝分类测试 | codex-rs/core/src/exec_tests.rs | keywords、exit codes、signals、SandboxType::None |

## 源码精读系列 13：Managed network approval

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| 网络审批核心状态机 | codex-rs/core/src/tools/network_approval.rs | `NetworkApprovalService`、active calls、host caches |
| Approval 生命周期类型 | 同上 | `NetworkApprovalMode`、`ActiveNetworkApproval`、`DeferredNetworkApproval` |
| Execution 注册 | 同上 | `begin_network_approval`、registration/attribution/cancellation |
| 请求归属 | 同上 | `resolve_request_attribution`、exact/single/ambiguous |
| Session host key | 同上 | `HostApprovalKey`、environment/host/protocol/port |
| Pending 去重 | 同上 | `PendingHostApprovalKey`、owner/waiter/generation |
| Inline 审批主流程 | 同上 | `handle_inline_policy_request` |
| Hook 与 reviewer | 同上 | permission hook、Guardian/User routing |
| Decision 投影 | 同上 | AllowOnce、AllowForSession、Deny 与 call outcomes |
| Proxy callbacks | 同上 | `build_network_policy_decider`、`build_blocked_request_observer` |
| Immediate/Deferred finish | 同上 | `finish_immediate_network_approval`、`finish_deferred_network_approval` |
| Orchestrator 接入 | codex-rs/core/src/tools/orchestrator.rs | `run_attempt`、network proxy/cancel token 注入 |
| Shell spec | codex-rs/core/src/tools/runtimes/shell.rs | Immediate `NetworkApprovalSpec` |
| Unified exec spec | codex-rs/core/src/tools/runtimes/unified_exec.rs | Deferred `NetworkApprovalSpec` |
| 后台进程收尾 | codex-rs/core/src/unified_exec/process_manager.rs | process entry、late denial、finish OnceCell |
| Proxy config 合并 | codex-rs/core/src/config/network_proxy_spec.rs | config、requirements、exec policy、hard deny |
| Proxy 启动/刷新 | codex-rs/core/src/session/mod.rs | start/refresh managed proxy、decider/observer wiring |
| Network amendment 持久化 | 同上 | `persist_network_policy_amendment`、host validation |
| 模型上下文状态 | codex-rs/core/src/context/network_rule_saved.rs | `NetworkRuleSaved` developer fragment |
| Approval wire types | codex-rs/protocol/src/approvals.rs | context、protocol、amendment、available decisions |
| `ReviewDecision` | codex-rs/protocol/src/protocol.rs | Approved/Session/NetworkAmendment/Denied/Abort |
| Proxy request/decision | codex-rs/network-proxy/src/network_policy.rs | `NetworkPolicyRequest`、`evaluate_host_policy` |
| Host 基线判断 | codex-rs/network-proxy/src/runtime.rs | `host_blocked`、deny/allow/local/DNS |
| Host 和 pattern 规范化 | codex-rs/network-proxy/src/policy.rs | exact、`*.`、`**.`、IP normalization |
| Execution-scoped proxy | codex-rs/network-proxy/src/proxy/execution_scope.rs | token 注册与 Drop 注销 |
| Proxy attribution bridge | codex-rs/network-proxy/src/attribution.rs | attribution token 读取与 state 切换 |
| HTTP/CONNECT enforcement | codex-rs/network-proxy/src/http_proxy.rs | method gate、policy request、blocked response |
| SOCKS enforcement | codex-rs/network-proxy/src/socks5.rs | TCP/UDP request 与 policy evaluation |
| Network mode | codex-rs/network-proxy/src/config.rs | Limited/Full、HTTP method 规则 |
| Managed constraint validation | codex-rs/network-proxy/src/state.rs | allow/deny expansion 与约束校验 |
| 单元测试 | codex-rs/core/src/tools/network_approval_tests.rs | 去重、作用域、Drop、outcome、ambiguous |
| Core 集成测试 | codex-rs/core/tests/suite/network_approval.rs | user/Guardian/persistence/concurrency/remote |
| Proxy policy 测试 | codex-rs/network-proxy/src/network_policy.rs | baseline/decider/audit |
| Proxy runtime 测试 | codex-rs/network-proxy/src/runtime.rs | wildcard、private IP、DNS、constraints |

## 源码精读系列 14：Apply patch lifecycle

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| Freeform 工具规格 | codex-rs/core/src/tools/handlers/apply_patch_spec.rs | `create_apply_patch_freeform_tool`、grammar format |
| Patch grammar | codex-rs/core/src/tools/handlers/apply_patch.lark | begin/end、Add/Delete/Update/Move、change line |
| 直接工具入口 | codex-rs/core/src/tools/handlers/apply_patch.rs | `ApplyPatchHandler::handle_call` |
| 流式参数预览 | 同上 | `ApplyPatchArgumentDiffConsumer`、500ms buffer |
| Preview 转 protocol | 同上 | `convert_apply_patch_hunks_to_protocol` |
| Shell 拦截入口 | 同上 | `intercept_apply_patch` |
| Shell 调用点 | codex-rs/core/src/tools/handlers/shell.rs | `run_exec_like` 中普通命令执行前拦截 |
| Unified exec 调用点 | codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs | `exec_command` 中拦截与 process id 释放 |
| Shell 形态识别 | codex-rs/apply-patch/src/invocation.rs | `maybe_parse_apply_patch`、shell classification |
| Heredoc AST 匹配 | 同上 | `extract_apply_patch_from_bash`、Tree-sitter query |
| 隐式调用拒绝 | 同上 | `maybe_parse_apply_patch_verified`、`ImplicitInvocation` |
| Verification 主流程 | 同上 | `verify_apply_patch_args`、`try_verify_apply_patch_args` |
| Parser 与 hunks | codex-rs/apply-patch/src/parser.rs | `parse_patch`、`Hunk`、`UpdateFileChunk` |
| Streaming parser | codex-rs/apply-patch/src/streaming_parser.rs | 增量 hunk 解析与 finish |
| Patch action 类型 | codex-rs/apply-patch/src/lib.rs | `ApplyPatchAction`、`ApplyPatchFileChange` |
| Update 内容推导 | 同上 | `unified_diff_from_chunks`、`derive_new_contents_from_chunks` |
| 上下文序列定位 | codex-rs/apply-patch/src/seek_sequence.rs | 多轮 sequence match、Unicode punctuation normalization |
| 安全三态 | codex-rs/core/src/safety.rs | `assess_patch_safety`、AutoApprove/AskUser/Reject |
| Writable path 判断 | 同上 | `is_write_patch_constrained_to_writable_paths` |
| Runtime invocation 准备 | codex-rs/core/src/apply_patch.rs | `prepare_apply_patch`、protocol conversion |
| 受影响路径 | codex-rs/core/src/tools/handlers/apply_patch.rs | `file_paths_for_action`，move 两端 |
| 额外写权限 | 同上 | `write_permissions_for_paths`、`effective_patch_permissions` |
| 执行编排入口 | 同上 | `execute_verified_patch`、begin/orchestrator/finish |
| Approval action | codex-rs/core/src/tools/runtimes/apply_patch.rs | `build_approval_action` |
| 每路径审批键 | 同上 | `ApplyPatchApprovalKey` |
| 用户审批与 cache | codex-rs/core/src/tools/approvals.rs | ApplyPatch 分支、`with_cached_approval` |
| Sandbox 行为 | codex-rs/core/src/tools/runtimes/apply_patch.rs | `Sandboxable`、`wants_no_sandbox_approval` |
| Runtime 执行 | 同上 | `ToolRuntime::run`、denial 分类、committed delta 累计 |
| 底层 patch 入口 | codex-rs/apply-patch/src/lib.rs | `apply_patch`、`apply_hunks` |
| 逐 hunk 写入 | 同上 | `apply_hunks_to_files`、Add/Delete/Update/Move |
| 实际变化记录 | 同上 | `AppliedPatchDelta`、`AppliedPatchChange`、`exact` |
| Move 部分失败 | 同上 | 先写 destination、再删 source、失败 delta |
| UI 生命周期 | codex-rs/core/src/tools/events.rs | `ToolEmitter::ApplyPatch`、begin/success/failure/rejected |
| 协议事件 | codex-rs/protocol/src/protocol.rs | `PatchApplyUpdatedEvent`、legacy Begin/End/Status |
| 模型结果回填 | codex-rs/core/src/tools/context.rs | `ApplyPatchToolOutput`、`CustomToolCallOutput` |
| 可移植场景测试 | codex-rs/apply-patch/tests/fixtures/scenarios | Add、多操作、Move、错误与 partial success fixtures |
| 场景运行器 | codex-rs/apply-patch/tests/suite/scenarios.rs | input/patch/expected 最终状态比较 |
| Parser/应用单测 | codex-rs/apply-patch/src/lib.rs、parser.rs | 多 chunk、Unicode、move、write failure、delta exactness |
| Handler 测试 | codex-rs/core/src/tools/handlers/apply_patch_tests.rs | 直接调用、环境、权限与事件 |
| Runtime 测试 | codex-rs/core/src/tools/runtimes/apply_patch_tests.rs | approval key、Sandbox context、granular flag |
| Approval 集成测试 | codex-rs/core/tests/suite/approvals.rs | patch approval 与 Session cache |

## 源码精读系列 15：Tool output 与 context feedback

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| 公开输出接口 | codex-rs/tools/src/tool_output.rs | `ToolOutput`、`JsonToolOutput`、telemetry preview |
| 工具调用协议类型 | codex-rs/protocol/src/models.rs | `ResponseInputItem`、`ResponseItem`、Function/Custom/ToolSearch variants |
| 输出 body 类型 | 同上 | `FunctionCallOutputPayload`、`FunctionCallOutputBody`、content items |
| Wire serialization | 同上 | `FunctionCallOutputPayload::serialize`，body 直出、success 内部化 |
| Input 到 history item | 同上 | `From<ResponseInputItem> for ResponseItem` |
| Tool call 构造 | codex-rs/core/src/tools/router.rs | `ToolRouter::build_tool_call` |
| Tool call 立即记录 | codex-rs/core/src/stream_events_utils.rs | `handle_output_item_done`、`record_completed_response_item` |
| Future 调度 | 同上 | `OutputItemResult.tool_future`、`needs_follow_up` |
| 并行执行门 | codex-rs/core/src/tools/parallel.rs | `ToolCallRuntime`、RwLock read/write gate |
| 取消与唯一终态 | 同上 | cancellation select、`terminal_outcome_reached`、`AbortedToolOutput` |
| 普通失败回填 | 同上 | `failure_response`、payload-specific output variant |
| 统一结果容器 | codex-rs/core/src/tools/registry.rs | `AnyToolResult`、`into_response` |
| Handler 执行与外部上下文 | 同上 | `handle_any_tool`、`contains_external_context` |
| Pre/PostToolUse | 同上 | block、updated input、post feedback |
| Hook 反馈投影 | 同上 | `PostToolUseFeedbackOutput` |
| 具体 Core outputs | codex-rs/core/src/tools/context.rs | Function、MCP、ToolSearch、ApplyPatch、Aborted、ExecCommand |
| Function/custom 分叉 | 同上 | `function_tool_response` |
| MCP direct projection | 同上 | `McpToolOutput::response_payload`、wall time、image detail、truncate |
| Unified exec projection | 同上 | `response_text`、`truncated_output`、session id、omission marker |
| 有序结果收集 | codex-rs/core/src/session/turn.rs | `FuturesOrdered`、`drain_in_flight` |
| Sampling 主循环 | 同上 | `try_run_sampling_request`、`needs_follow_up`、outer `run_turn` continue |
| Stream 错误后 drain | 同上 | outcome 后统一 `drain_in_flight` |
| Pending input 合并 | 同上 | `model_needs_follow_up || has_pending_input` |
| MidTurn compaction gate | 同上 | token status、`should_roll_over`、continue |
| Session 记录入口 | codex-rs/core/src/session/mod.rs | `record_conversation_items` |
| History 记录截断 | codex-rs/core/src/context_manager/history.rs | `record_items`、`process_item` |
| Prompt projection | 同上 | `for_prompt`、`normalize_history` |
| Call 缺 output 修复 | codex-rs/core/src/context_manager/normalize.rs | `ensure_call_outputs_present` |
| Stable synthetic ID | 同上 | UUID v5 namespace、`synthetic_output_id` |
| Orphan output 删除 | 同上 | `remove_orphan_outputs` |
| Pair-aware 删除 | 同上 | `remove_corresponding_for` |
| Image/audio 降级 | 同上 | `strip_images_when_unsupported`、`strip_audio_when_unsupported` |
| Output truncation 库 | codex-rs/utils/output-truncation/src/lib.rs | text middle truncation、content-item budgets |
| Output truncation 测试 | codex-rs/utils/output-truncation/src/truncate_tests.rs | bytes/tokens、media、omission behavior |
| Output 类型测试 | codex-rs/core/src/tools/context_tests.rs | function/custom/MCP/exec projections |
| History 不变量测试 | codex-rs/core/src/context_manager/history_tests.rs | missing、orphan、multimodal、truncation |
| Router/Registry 测试 | codex-rs/core/src/tools/router_tests.rs、registry_tests.rs | payload mapping、AnyToolResult conversion |
| Stream item 测试 | codex-rs/core/src/stream_events_utils_tests.rs | tool future 与非工具 item 处理 |
| Turn 集成行为 | codex-rs/core/src/session/turn_tests.rs | follow-up、stream、tool result 生命周期 |

## 源码精读系列 16：Cancellation 与 interruption lifecycle

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| 中断协议入口 | codex-rs/protocol/src/protocol.rs | `Op::Interrupt`、background terminal 边界 |
| Session shutdown 操作 | 同上 | `Op::Shutdown`、`ShutdownComplete` |
| Turn 取消终态 | 同上 | `TurnAbortedEvent`、`TurnAbortReason` |
| Submission 分派 | codex-rs/core/src/session/handlers.rs | `submission_loop` 中 Interrupt/Shutdown 分支 |
| Interrupt handler | 同上 | `interrupt`、`shutdown_session_runtime` |
| Session teardown | 同上 | `shutdown`、意外 channel close fallback cleanup |
| 活动 Turn 状态 | codex-rs/core/src/state/turn.rs | `ActiveTurn`、`RunningTask`、`TurnState` |
| Pending waiter 清理 | 同上 | `clear_pending_waiters`、各种 oneshot sender map |
| Task 创建与 token 树 | codex-rs/core/src/tasks/mod.rs | `start_task`、CancellationToken child chain |
| 新任务替换旧任务 | 同上 | `spawn_task`、`TurnAbortReason::Replaced` |
| Turn 中止总入口 | 同上 | `abort_all_tasks`、`abort_turn_if_active` |
| 两阶段任务取消 | 同上 | `handle_task_abort`、100ms graceful wait、handle abort |
| 中断历史标记 | 同上 | `InterruptedTurnHistoryMarker`、`interrupted_turn_history_marker` |
| Marker durability | 同上 | marker 前置记录与 `flush_rollout` |
| 唯一 Turn 终态 | 同上 | `on_task_finished`、cancelled task 跳过重复 finish |
| Core interrupt 入口 | codex-rs/core/src/session/mod.rs | `interrupt_task`、无 active Turn 时 cancel MCP startup |
| Steer 准入 | 同上 | `SteerInputError`、`steer_input`、TaskKind gate |
| Shutdown barrier | 同上 | `shutdown_and_wait`、session loop termination |
| Pending input 队列 | codex-rs/core/src/session/input_queue.rs | `InputQueueActivity::Steer`、`clear_pending` |
| Steer 后续采样 | codex-rs/core/src/session/turn.rs | pending drain、`model_needs_follow_up || has_pending_input` |
| Model stream 取消 | 同上 | stream create/next 的 `or_cancel`、`CodexErr::TurnAborted` |
| In-flight tool drain | 同上 | cancellation 后 `drain_in_flight` 与 token count |
| Tool 取消竞态 | codex-rs/core/src/tools/parallel.rs | dispatch select、`terminal_outcome_reached` |
| Aborted tool output | 同上 | `aborted_response`、`notify_tool_aborted` |
| Command timeout/取消 | codex-rs/core/src/exec.rs | `ExecExpiration`、`ExecExpirationOutcome` |
| 组合取消源 | 同上 | `cancel_when_either` |
| 输入接纳状态 | codex-rs/core/src/user_message_admission.rs | Started/Steered、Admitted/Persisted |
| 中断中的 admission 失败 | 同上 | `complete_task_end`、TaskEndedBeforePersistence |
| App-server 中断入口 | codex-rs/app-server/src/request_processors/turn_processor.rs | `turn_interrupt_inner`、turn ID fencing |
| Pending interrupt 队列 | codex-rs/app-server/src/thread_state.rs | `pending_interrupts`、last terminal turn ID |
| 中断响应 barrier | codex-rs/app-server/src/bespoke_event_handling.rs | `respond_to_pending_interrupts` |
| App-server 终态投影 | 同上 | `handle_turn_interrupted`、TurnStatus::Interrupted |
| Server request 清理 | 同上 | TurnAborted 分支的 `abort_pending_server_requests` |
| 批量 Thread shutdown | codex-rs/core/src/thread_manager.rs | `shutdown_all_threads_bounded`、outcome report |
| App-server shutdown 调用 | codex-rs/app-server/src/request_processors/thread_processor.rs | `shutdown_threads`、10 秒边界 |
| TUI interrupt 默认键 | codex-rs/tui/src/keymap.rs | `chat.interrupt_turn`、默认 Esc |
| TUI 输入路由 | codex-rs/tui/src/chatwidget/interaction.rs | 局部 view、interrupt 与 quit 决策 |
| TUI 中断后输入恢复 | codex-rs/tui/src/chatwidget/input_restore.rs | `on_interrupted_turn`、pending steers |
| App-server 公开示例 | codex-rs/app-server/README.md | Interrupt、Steer、background terminal clean |
| App-server interrupt 测试 | codex-rs/app-server/tests/suite/v2/turn_interrupt.rs | RPC、TurnAborted、completion 与竞态 |
| Core interrupt 测试 | codex-rs/core/tests/suite | TurnAborted、工具/审批/进程取消行为 |

## 源码精读系列 17：User input admission 与 pending queue

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| App-server 新 Turn 入口 | codex-rs/app-server/src/request_processors/turn_processor.rs | `turn_start_inner`、输入映射、`Op::UserInput` |
| start 的异步返回边界 | 同上 | `submit_user_input_with_client_user_message_id`、submission ID 作为 Turn ID |
| App-server 显式 steer | 同上 | `turn_steer_inner`、`expectedTurnId`、错误投影 |
| Submission 信封与 channel | codex-rs/core/src/session/mod.rs | `SessionIo::submit_with_trace`、`submit_with_id` |
| Thread 普通提交接口 | codex-rs/core/src/codex_thread.rs | `submit_user_input_with_client_user_message_id` |
| 等待 admission 接口 | 同上 | `submit_user_input_and_wait_for_admission` |
| 等待 durable admission | 同上 | `submit_user_input_and_wait_for_persisted_admission` |
| waiter 注册顺序与终止竞态 | 同上 | `submit_user_input_and_wait_for_admission_inner`、biased `select!` |
| idle 自动启动入口 | 同上 | `try_start_turn_if_idle`、stable rejection reason |
| UserInput 总分派 | codex-rs/core/src/session/handlers.rs | `user_input_or_turn`、`user_input_or_turn_inner` |
| Started/Steered 决策 | 同上 | 先 `steer_input`，再处理 `NoActiveTurn(items)` |
| 新 Turn 输入组装 | 同上 | additional context、UserInput、`spawn_task(RegularTask)` |
| Admission 结果与错误 | codex-rs/core/src/user_message_admission.rs | `UserMessageAdmission`、`UserMessageAdmissionError` |
| Durable admission 四态 | 同上 | Immediate、WaitingForAdmission、Admitted、Persisted |
| Admission 完成 | 同上 | `complete` |
| Persistence 完成 | 同上 | `complete_persistence`、client ID 关联 |
| Steer 关联 waiter | 同上 | `associate_steered_by_client_id` |
| Turn 结束清理 waiter | 同上 | `complete_task_end` |
| 调用取消清理 | 同上 | `PendingUserMessageAdmissionGuard::drop` |
| Core steer 临界区 | codex-rs/core/src/session/mod.rs | `Session::steer_input`、active Turn lock |
| Steer 世代围栏 | 同上 | expected/actual Turn ID comparison |
| 不可 steer Turn | 同上 | Review/Compact `ActiveTurnNotSteerable` |
| 用户消息历史与 UI 投影 | 同上 | `record_user_prompt_and_emit_turn_item` |
| Conversation 记录入口 | 同上 | `record_conversation_items`、rollout response items |
| Turn 输入类型 | codex-rs/core/src/session/input_queue.rs | `TurnInput` 三种 variant |
| Turn-local pending queue | 同上 | `TurnInputQueue`、`extend_pending_input_and_accept_mailbox_delivery_for_turn_state` |
| Session mailbox | 同上 | `mailbox_pending_mails`、delivery phase |
| Queue 原子 drain | 同上 | `get_pending_input`、`split_off(0)` |
| Activity 唤醒 | 同上 | `InputQueueActivity`、watch sender/receiver |
| 初始输入与 pending 时序 | codex-rs/core/src/session/turn.rs | `run_turn`、`can_drain_pending_input` |
| Hook 与记录总入口 | 同上 | `run_hooks_and_record_inputs` |
| Follow-up 决策 | 同上 | `model_needs_follow_up || has_pending_input` |
| UserPromptSubmit 检查 | codex-rs/core/src/hook_runtime.rs | `inspect_pending_input` |
| Durable record/flush | 同上 | `record_pending_input`、`flush_rollout` |
| Hook 拒绝清理 | 同上 | `reject_pending_input` |
| 自动 idle reservation | codex-rs/core/src/session/inject.rs | `try_start_turn_if_idle`、`Arc::ptr_eq` |
| Admission 并发集成测试 | codex-rs/core/tests/suite/user_message_admission.rs | 一个 Started、一个 Steered、相同 Turn ID、rollout client IDs |
| Pending input 集成测试 | codex-rs/core/tests/suite/pending_input.rs | initial input、mailbox 合并、steer follow-up |
| Persistence failure 测试 | codex-rs/core/src/session/tests.rs | `failed_user_message_persistence_stops_turn_processing` |
| App-server steer 测试 | codex-rs/app-server/tests/suite/v2/turn_steer.rs | active Turn、expected ID、context-only reject、client ID |
| App-server start 测试 | codex-rs/app-server/tests/suite/v2/turn_start.rs | client user message ID 与 item event |

## 源码精读系列 18：UserPromptSubmit Hook 与 additional context

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| Hook 配置数据模型 | codex-rs/config/src/hook_config.rs | `HookEventsToml.user_prompt_submit`、`MatcherGroup`、command config |
| Hook engine 总入口 | codex-rs/hooks/src/engine/mod.rs | `ClaudeHooksEngine`、preview/run user prompt submit |
| 可执行 handler | 同上 | `ConfiguredHandler`、run ID、context limit |
| Handler discovery | codex-rs/hooks/src/engine/discovery.rs | config/plugin/managed sources、display order |
| Enabled 与 trust gate | 同上 | HookListEntry 与 ConfiguredHandler 的分离 |
| 不支持的 handler 形态 | 同上 | mcp_tool、prompt、agent 与 async warnings |
| UserPromptSubmit matcher 规则 | codex-rs/hooks/src/engine/dispatcher.rs | `select_handlers_for_matcher_inputs` 无条件选择事件 handlers |
| Hook 并发执行 | 同上 | `execute_handlers`、`FuturesUnordered` |
| 稳定结果顺序 | 同上 | completion order 后按 configured order 排序 |
| Running/Completed summary | 同上 | `running_summary`、`completed_summary`、Turn scope |
| Command 进程生命周期 | codex-rs/hooks/src/engine/command_runner.rs | shell、cwd、stdin、timeout、kill_on_drop |
| Hook request/outcome | codex-rs/hooks/src/events/user_prompt_submit.rs | `UserPromptSubmitRequest`、`UserPromptSubmitOutcome` |
| stdin JSON 构造 | 同上 | `UserPromptSubmitCommandInput` serialization |
| 多 Handler 聚合 | 同上 | any-stop、first reason、ordered contexts |
| Exit/stdout/stderr 解析 | 同上 | `parse_completed` |
| Block/stop/context 组合 | 同上 | decision:block、continue:false、exit 2 |
| Hook output wire schema | codex-rs/hooks/src/schema.rs | `UserPromptSubmitCommandOutputWire`、camelCase output |
| Hook input schema | 同上 | `UserPromptSubmitCommandInput`、snake_case input |
| 通用输出解析 | codex-rs/hooks/src/engine/output_parser.rs | `UniversalOutput`、`parse_user_prompt_submit` |
| 合法 block 检查 | 同上 | `invalid_block_reason`、非空 reason |
| Additional context 收集 | codex-rs/hooks/src/events/common.rs | `append_additional_context`、`flatten_additional_contexts` |
| Hook output spill | codex-rs/hooks/src/output_spill.rs | 2500-token default、per-handler limit、temp file preview |
| Spill 测试 | codex-rs/hooks/src/output_spill_tests.rs | inline、large spill、individual limits、limit 0 |
| Core Hook runtime bridge | codex-rs/core/src/hook_runtime.rs | `ContextInjectingHookOutcome`、`HookRuntimeOutcome` |
| User prompt 检查入口 | 同上 | `inspect_pending_input` |
| Started/Completed 事件 | 同上 | `run_context_injecting_hook`、emit functions |
| 允许输入记录 | 同上 | `record_pending_input` |
| Blocked 输入收口 | 同上 | `reject_pending_input`、durable admission error |
| Hook context 记录 | 同上 | `record_additional_contexts` |
| Hook context role | codex-rs/core/src/context/hook_additional_context.rs | `HookAdditionalContext`、developer role、无 markers |
| Turn 输入批处理 | codex-rs/core/src/session/turn.rs | `run_hooks_and_record_inputs`、blocked/accepted/persistence flags |
| SessionStart 顺序 | 同上 | `run_pending_session_start_hooks` 先于初始 UserPromptSubmit |
| Pending steer 检查 | 同上 | drain pending 后再次 `run_hooks_and_record_inputs` |
| Client context wire types | codex-rs/app-server-protocol/src/protocol/v2/turn.rs | AdditionalContextKind/Entry |
| Client context 映射 | codex-rs/app-server/src/request_processors/turn_processor.rs | `map_additional_context` |
| Client context store | codex-rs/core/src/state/additional_context.rs | `AdditionalContextStore::merge`、snapshot diff |
| Client context fragments | codex-rs/context-fragments/src/additional_context.rs | Untrusted/User、Application/Developer、1000-token cap |
| Hook 协议事件 | codex-rs/protocol/src/protocol.rs | HookRunStatus、HookOutputEntryKind、HookStarted/Completed |
| Hook order 集成测试 | codex-rs/core/tests/suite/hooks.rs | SessionStart before UserPromptSubmit |
| Blocked context 集成测试 | 同上 | blocked prompt 不进模型、context 留给下一 Turn |
| Queued batch 集成测试 | 同上 | accepted queued prompt 不被 blocked prompt 连带丢失 |
| Client context 集成测试 | codex-rs/core/tests/suite/additional_context.rs | role、marker、UI item 隔离、长度限制 |

## 源码精读系列 19：Turn/Item event projection 与客户端状态重建

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| Regular Turn 开始 | codex-rs/core/src/tasks/regular.rs | inline `TurnStarted`、startup prewarm 边界 |
| Turn 唯一终态 | codex-rs/core/src/tasks/mod.rs | `TurnComplete` / `TurnAborted` 二选一、timing/error |
| Core event persistence/delivery | codex-rs/core/src/session/mod.rs | `send_event_raw_with_persistence`、`deliver_event_raw` |
| Item started 发射 | 同上 | `emit_turn_item_started`、timing state |
| Item completed 发射 | 同上 | `emit_turn_item_completed`、缺失 start fallback |
| Core lifecycle 协议 | codex-rs/protocol/src/protocol.rs | TurnStarted/Complete/Aborted、ItemStarted/Completed |
| Thread listener loop | codex-rs/app-server/src/request_processors/thread_lifecycle.rs | cancel/command/next_event/unload biased select |
| 状态先于通知 | 同上 | `track_current_turn_event` 后 `apply_bespoke_event_handling` |
| Resume listener command | 同上 | `SendThreadResumeResponse`、原子 snapshot/subscribe 排序 |
| 运行中 resume 合并 | 同上 | `handle_pending_thread_resume_request`、active Turn snapshot |
| 历史与 active Turn 去重 | 同上 | `merge_turn_history_with_active_turn` |
| App-server ThreadState | codex-rs/app-server/src/thread_state.rs | `ThreadState`、listener generation、current history |
| Turn 轻量摘要 | 同上 | `TurnSummary`、last error/agent message、command start set |
| Live history 跟踪 | 同上 | `track_current_turn_event` |
| Active Turn snapshot | 同上 | `active_turn_snapshot` |
| Bespoke 总分派 | codex-rs/app-server/src/bespoke_event_handling.rs | `apply_bespoke_event_handling` |
| TurnStarted notification | 同上 | InProgress、NotLoaded、empty items |
| Turn completion summary | 同上 | `emit_turn_completed_with_status`、Summary/NotLoaded |
| Completed/Failed 选择 | 同上 | `handle_turn_complete`、last_error |
| Interrupted 选择 | 同上 | `handle_turn_interrupted` |
| Error 两阶段投影 | 同上 | `handle_error_notification`、willRetry false、terminal summary |
| StreamError 投影 | 同上 | willRetry true、不写 last_error |
| Command started 去重 | 同上 | `command_execution_started` set |
| Canonical/legacy 分流 | 同上 | deprecated begin/end suppression for v2 |
| Stateless item 映射 | codex-rs/app-server-protocol/src/protocol/event_mapping.rs | `item_event_to_server_notification` |
| Agent/Plan/Reasoning delta | 同上 | item-specific delta notifications |
| Exec/File progress | 同上 | output delta、terminal interaction、patch updated |
| v2 Turn 数据合同 | codex-rs/app-server-protocol/src/protocol/v2/thread_data.rs | `Turn`、`TurnItemsView` |
| v2 Turn notifications | codex-rs/app-server-protocol/src/protocol/v2/turn.rs | TurnStarted/TurnCompleted |
| v2 Item notifications | codex-rs/app-server-protocol/src/protocol/v2/item.rs | ItemStarted/Completed 与 timestamps |
| 历史总构建器 | codex-rs/app-server-protocol/src/protocol/thread_history.rs | `ThreadHistoryBuilder` |
| Live/rollout 双入口 | 同上 | `handle_event`、`handle_rollout_item` |
| Item upsert | 同上 | `handle_materialized_item_lifecycle`、`upsert_turn_item` |
| Turn ID 精确终态匹配 | 同上 | `handle_turn_complete`、`handle_turn_aborted` |
| 显式/隐式 Turn | 同上 | `opened_explicitly`、legacy user message fallback |
| 增量 changes | 同上 | `ThreadHistoryChangeSet`、batch accumulator |
| Rollback removal | 同上 | removed Turn IDs 与同批变化消除 |
| Projection 测试 | codex-rs/app-server-protocol/src/protocol/thread_history_projection_tests.rs | status、items、terminal timing、changes |
| Bespoke handler 测试 | codex-rs/app-server/src/bespoke_event_handling.rs | started/completed/interrupted/failed/summary/去重 |
| TUI per-thread store | codex-rs/tui/src/app/thread_events.rs | `ThreadEventStore`、active Turn、bounded buffer |
| TUI snapshot rebase | 同上 | `set_turns`、`rebase_buffer_after_session_refresh` |
| TUI replay 协议处理 | codex-rs/tui/src/chatwidget/protocol.rs、replay.rs | notification routing 与 history replay |
| TUI event tests | codex-rs/tui/src/chatwidget/tests、app/tests.rs | delta、itemsView、active Turn、replay |

## 源码精读系列 20：Thread watch、订阅与多客户端路由

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| 订阅双向索引 | codex-rs/app-server/src/thread_state.rs | `ThreadStateManagerInner`、`ThreadEntry` |
| 当前订阅者集合 | 同上 | `subscribed_connection_ids` |
| 自动附着与 live 检查 | 同上 | `try_ensure_connection_subscribed` |
| 取消单连接订阅 | 同上 | `unsubscribe_connection_from_thread` |
| 连接断开批量清理 | 同上 | `remove_connection`、反向索引 |
| 有无订阅者 watch | 同上 | `has_connections_watcher`、`update_has_connections` |
| 等待首个订阅者 | 同上 | `wait_for_thread_subscriber` |
| listener 实例匹配 | 同上 | `listener_matches`、Weak upgrade、`Arc::ptr_eq` |
| listener 替换 | 同上 | `set_listener`、cancel、`listener_generation` |
| listener 清理 | 同上 | `clear_listener`、command sender、history、file watch |
| attach 与 unload 围栏 | codex-rs/app-server/src/request_processors/thread_lifecycle.rs | `ensure_conversation_listener`、`pending_thread_unloads` |
| listener 唯一启动 | 同上 | `ensure_listener_task_running` |
| listener 主循环 | 同上 | cancel/command/next_event/unload biased select |
| 事件多连接 fan-out | 同上 | `subscribed_connection_ids`、thread-scoped sender |
| 旧 listener 清理防护 | 同上 | generation equality check |
| 双路卸载观察 | 同上 | `UnloadingState`、subscriber/status receivers |
| 完整宽限期计算 | 同上 | `unloading_target`、两个 timestamp 的 max |
| 到期后的状态复核 | 同上 | `should_unload_now`、`agent_status` |
| 活动滞后修补 | 同上 | `note_thread_activity_observed` |
| 无订阅者卸载 | 同上 | `unload_thread_without_subscribers` |
| 状态 owner | codex-rs/app-server/src/thread_status.rs | `ThreadWatchManager`、`ThreadWatchState` |
| 原始运行事实 | 同上 | `RuntimeFacts` |
| 状态投影顺序 | 同上 | `loaded_thread_status` |
| 待决请求 guard | 同上 | `ThreadWatchActiveGuard`、Drop、saturating counters |
| 状态修改与通知 | 同上 | `mutate_and_publish` |
| active snapshot 修正 | 同上 | `resolve_thread_status` |
| unsubscribe handler | codex-rs/app-server/src/request_processors/thread_processor.rs | `thread_unsubscribe_response_inner` |
| thread processor 断线入口 | 同上 | `connection_closed` |
| connection 总清理顺序 | codex-rs/app-server/src/message_processor.rs | `connection_closed` |
| unsubscribe wire types | codex-rs/app-server-protocol/src/protocol/v2/thread.rs | Params、Response、Status |
| status wire types | 同上 | `ThreadStatus`、`ThreadActiveFlag` |
| 多客户端订阅测试 | codex-rs/app-server/src/request_processors/thread_processor_tests.rs | listener 保留、watch transition、closed attach 拒绝 |
| status 与 guard 测试 | codex-rs/app-server/src/thread_status.rs | nested guards、flags、running count、shutdown |

## 源码精读系列 21：App-server 反向请求与待决响应收口

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| Server request DSL | codex-rs/app-server-protocol/src/protocol/common.rs | `server_request_definitions!` |
| 完整请求与无 ID payload | 同上 | `ServerRequest`、`ServerRequestPayload` |
| typed response 转换 | 同上 | `response_from_result`、`ServerResponse` |
| Command approval wire | codex-rs/app-server-protocol/src/protocol/v2/item.rs | Params、Response、Decision |
| File approval wire | 同上 | `FileChangeRequestApproval*` |
| Request user input wire | 同上 | `ToolRequestUserInput*` |
| Dynamic tool wire | 同上 | `DynamicToolCall*` |
| Permission wire | codex-rs/app-server-protocol/src/protocol/v2/permissions.rs | `PermissionsRequestApproval*` |
| Resolved notification wire | codex-rs/app-server-protocol/src/protocol/v2/notification.rs | `ServerRequestResolvedNotification` |
| Incoming connection request ID | codex-rs/app-server/src/outgoing_message.rs | `ConnectionRequestId` |
| Outgoing callback owner | 同上 | `OutgoingMessageSender` |
| Pending entry 数据 | 同上 | `PendingCallbackEntry` |
| Server request ID 分配 | 同上 | `next_server_request_id`、`next_request_id` |
| Register-before-send | 同上 | `send_request_to_connections` |
| Thread-scoped 路由包装 | 同上 | `ThreadScopedOutgoingMessageSender` |
| Success response callback | 同上 | `notify_client_response` |
| Error response callback | 同上 | `notify_client_error` |
| 首答原子取走 | 同上 | `take_request_callback`、`remove_entry` |
| Pending snapshot 与排序 | 同上 | `pending_requests_for_thread` |
| Resume replay | 同上 | `replay_requests_to_connection_for_thread` |
| Thread 批量取消 | 同上 | `cancel_requests_for_thread` |
| 全局 callback drain | 同上 | `cancel_all_requests` |
| Core event 到反向请求 | codex-rs/app-server/src/bespoke_event_handling.rs | `apply_bespoke_event_handling` |
| Command response 回填 | 同上 | `on_command_execution_request_approval_response` |
| File response 回填 | 同上 | `on_file_change_request_approval_response` |
| User input 回填 | 同上 | `on_request_user_input_response` |
| Permission response 回填 | 同上 | `on_request_permissions_response` |
| MCP elicitation 回填 | 同上 | `on_mcp_server_elicitation_response` |
| Turn 边界批量取消 | 同上 | TurnStarted/TurnComplete 的 `abort_pending_server_requests` |
| Active guard 释放 | 同上、thread_status.rs | response task 与 `ThreadWatchActiveGuard` |
| Dynamic tool 特殊边界 | codex-rs/app-server/src/dynamic_tools.rs | `on_call_response` |
| Resolved listener command | codex-rs/app-server/src/thread_state.rs | `ResolveServerRequest` |
| Resolved helper 与回执 | 同上 | `resolve_server_request_on_thread_listener` |
| Resume response 后 replay | codex-rs/app-server/src/request_processors/thread_lifecycle.rs | `handle_pending_thread_resume_request` |
| Resolved notification 广播 | 同上 | `resolve_pending_server_request` |
| Thread unload callback 取消 | 同上 | `unload_thread_without_subscribers` |
| JSON-RPC response/error 入口 | codex-rs/app-server/src/message_processor.rs | `process_response`、`process_error` |
| Turn transition reason | codex-rs/app-server/src/server_request_error.rs | constant 与 predicate |
| In-process shutdown drain | codex-rs/app-server/src/in_process.rs | `cancel_all_requests` |
| Callback 单元测试 | codex-rs/app-server/src/outgoing_message.rs | error、sort、cancel tests |
| Turn transition 单元测试 | codex-rs/app-server/src/request_processors/thread_processor_tests.rs | abort pending state |
| Resume replay 集成测试 | codex-rs/app-server/tests/suite/v2/thread_resume.rs | command/file approval |
| Interrupt 集成测试 | codex-rs/app-server/tests/suite/v2/turn_interrupt.rs | approval resolved + Turn interrupted |
| Resolved 集成测试 | codex-rs/app-server/tests/suite/v2 | user input、permissions、MCP elicitation |

## 源码精读系列 22：Outgoing router、传输写入与交付边界

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| 业务发送 owner | codex-rs/app-server/src/outgoing_message.rs | `OutgoingMessageSender` |
| 定向与广播 envelope | 同上 | `OutgoingEnvelope` |
| Response/error 定向发送 | 同上 | `send_outgoing_message_to_connection` |
| Notification fan-out | 同上 | `send_server_notification_to_connections` |
| Writer completion helper | 同上 | `send_server_notification_to_connection_and_wait` |
| Notification timestamp | 同上 | `timestamped_server_notification` |
| 同 timestamp fan-out 测试 | 同上 | targeted copies timestamp test |
| 发送侧 connection projection | codex-rs/app-server/src/transport.rs | `OutboundConnectionState` |
| 初始化与 opt-out filter | 同上 | `should_skip_notification_for_connection` |
| 实验字段按连接裁剪 | 同上 | `filter_outgoing_message_for_connection` |
| 单连接 writer 入队 | 同上 | `send_message_to_connection` |
| 慢连接断开 | 同上 | `try_send` Full/Closed branches |
| Envelope 总路由 | 同上 | `route_outgoing_envelope` |
| Router filter 测试 | codex-rs/app-server/src/transport_tests.rs | opt-out、experimental notification/request |
| 快慢连接隔离测试 | 同上 | `broadcast_does_not_block_on_slow_connection` |
| Stdio 背压测试 | 同上 | `to_connection_stdio_waits...` |
| Router control plane | codex-rs/app-server/src/lib.rs | `OutboundControlEvent` |
| Global outgoing channel | 同上 | capacity 128 sender/receiver |
| Outbound router task | 同上 | biased control/envelope loop |
| Connection open/close 投影 | 同上 | `TransportEvent` branches |
| WebSocket initialize 顺序 | 同上 | state mirror、定向快照、Release store |
| 跨 crate outgoing enum | codex-rs/app-server-transport/src/outgoing_message.rs | `OutgoingMessage` |
| Per-writer queue item | 同上 | `QueuedOutgoingMessage` |
| Connection identity | 同上 | `ConnectionId` |
| Transport 类型和容量 | codex-rs/app-server-transport/src/transport/mod.rs | `AppServerTransport`、`CHANNEL_CAPACITY` |
| Connection lifecycle events | 同上 | `TransportEvent`、`ConnectionOrigin` |
| Incoming overload | 同上 | `enqueue_incoming_message` |
| Outgoing JSON serialization | 同上 | `serialize_outgoing_message` |
| Response serialization fallback | 同上 | `response_serialization_error` |
| Wire/overload tests | 同上 | JSON shape、invalid UTF-8、Full queue tests |
| Stdio connection | codex-rs/app-server-transport/src/transport/stdio.rs | reader/writer tasks |
| JSONL framing | 同上 | line reader、newline writer |
| WebSocket writer capacity | codex-rs/app-server-transport/src/transport/websocket.rs | 32×1024 constant |
| WebSocket connection owner | 同上 | `run_websocket_connection` |
| WebSocket outbound loop | 同上 | writer/control queues、serialization、completion |
| WebSocket inbound loop | 同上 | text/ping/close、disconnect token |
| In-process router | codex-rs/app-server/src/in_process.rs | `run_outbound_router` |
| In-process delivery classes | 同上 | `server_notification_requires_delivery` |
| In-process writer policies | 同上 | response/request/notification match branches |
| In-process shutdown drain | 同上 | callbacks、pending client responses、task shutdown |
| Completion barrier callers | codex-rs/app-server/src/command_exec.rs | streamed command output |
| FS watch barrier caller | codex-rs/app-server/src/fs_watch.rs | `FsChanged` |
| Process barrier callers | codex-rs/app-server/src/request_processors/process_exec_processor.rs | output delta、ProcessExited |

## 源码精读系列 23：Incoming transport、envelope 分类与 handler 准入

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| 四类 wire envelope | codex-rs/app-server-protocol/src/rpc.rs | `JSONRPCMessage` |
| Request ID 类型 | 同上 | `RequestId` 的 String/Integer variants |
| 外层 message shapes | 同上 | `JSONRPCRequest`、Notification、Response、Error |
| 非完整 JSON-RPC 2.0 边界 | 同上 | module comment、无 `jsonrpc` 必需字段 |
| transport event | codex-rs/app-server-transport/src/transport/mod.rs | `TransportEvent` |
| JSON/envelope 解析入口 | 同上 | `forward_incoming_message` |
| incoming queue 准入 | 同上 | `enqueue_incoming_message` |
| Request overload | 同上 | `-32001`、writer `try_send` |
| Response 等待而非丢弃 | 同上 | Full generic event `send().await` |
| overload/backpressure tests | 同上 | 三个 `enqueue_incoming_*` tests |
| stdio JSONL framing | codex-rs/app-server-transport/src/transport/stdio.rs | `BufReader::lines`、reader loop |
| stdio initialize name hint | 同上 | `stdio_initialize_client_name` |
| WebSocket connection lifecycle | codex-rs/app-server-transport/src/transport/websocket.rs | `run_websocket_connection` |
| WebSocket text/control frames | 同上 | `run_websocket_inbound_loop` |
| Connection state owner | codex-rs/app-server/src/lib.rs | main processor loop、connections map |
| ConnectionOpened state | 同上 | session 与 outbound projection 初始化 |
| ConnectionClosed state | 同上 | remove、gate close、cleanup spawn |
| Incoming envelope routing | 同上 | Request/Response/Notification/Error match |
| Unknown connection fencing | 同上 | four drop branches |
| Initialize 后状态发布 | 同上 | mirror capabilities、initial notifications、Release store |
| Connection session | codex-rs/app-server/src/message_processor.rs | `ConnectionSessionState`、`OnceLock` |
| Initialized session payload | 同上 | `InitializedConnectionSessionState` |
| Raw request 入口 | 同上 | `process_request` |
| Connection-scoped ID | 同上、outgoing_message.rs | `ConnectionRequestId` |
| Request tracing context | message_processor.rs | `run_request_with_context` |
| Raw→typed wrapper | 同上 | `deserialize_client_request` |
| Initialize 特殊分支 | 同上 | `handle_client_request` |
| Initialized/experimental gates | 同上 | `dispatch_initialized_client_request` |
| Scope enqueue / unscoped spawn | 同上 | `QueuedInitializedRequest` 构造与分叉 |
| Typed handler exhaustive match | 同上 | `handle_initialized_client_request` |
| Response ownership | 同上 | `Ok(Some)` / `Ok(None)` / `Err` |
| Response/error callback 入口 | 同上 | `process_response`、`process_error` |
| Client notification 边界 | 同上 | `process_notification` 只记录日志 |
| Initialize 验证与提交 | codex-rs/app-server/src/request_processors/initialize_processor.rs | `InitializeRequestProcessor::initialize` |
| Duplicate initialize | 同上 | pre-check 与 `OnceLock::set` 竞争处理 |
| Error code helpers | codex-rs/app-server/src/error_code.rs | `-32600`、`-32001` 等 |
| Client request DSL | codex-rs/app-server-protocol/src/protocol/common.rs | `client_request_definitions!` |
| Typed request enum | 同上 | generated `ClientRequest` |
| Raw→typed conversion | 同上 | `TryFrom<JSONRPCRequest>` |
| Protocol serialization scope | 同上 | `ClientRequestSerializationScope` |
| Runtime queue key/access | codex-rs/app-server/src/request_serialization.rs | `from_scope` |
| Queued request gate wrapper | 同上 | `QueuedInitializedRequest::run` |
| Per-key FIFO/shared read | 同上 | `enqueue`、`drain` |
| Serialization tests | 同上 | FIFO、SharedRead、writer fairness |
| Per-connection RPC gate | codex-rs/app-server/src/connection_rpc_gate.rs | `run`、`close`、`shutdown` |
| Gate unit tests | 同上 | drop-unpolled、close returns、shutdown waits |
| Pre-init integration test | codex-rs/app-server/tests/suite/v2/connection_handling_websocket.rs | `Not initialized` assertion |
| Experimental integration tests | codex-rs/app-server/tests/suite/v2/experimental_api.rs | capability rejection |

## 源码精读系列 24：Initialize 握手、capability 协商与连接状态发布

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| Initialize 参数 | codex-rs/app-server-protocol/src/protocol/v1.rs | `InitializeParams` |
| Client identity wire | 同上 | `ClientInfo` |
| Capability wire/defaults | 同上 | `InitializeCapabilities` |
| Initialize response | 同上 | `InitializeResponse` |
| Initialize method DSL | codex-rs/app-server-protocol/src/protocol/common.rs | `Initialize => "initialize"` |
| Capability wire tests | 同上 | serialize/deserialize initialize capabilities |
| Connection session owner | codex-rs/app-server/src/message_processor.rs | `ConnectionSessionState` |
| Committed session payload | 同上 | `InitializedConnectionSessionState` |
| OnceLock commit | 同上 | `initialize`、getters |
| Initialize special dispatch | 同上 | `handle_client_request` |
| Transport-ready bool contract | 同上 | `outbound_initialized: Option<&AtomicBool>` |
| Session capability consumers | 同上 | initialized、experimental、client info、MCP getters |
| Initialize processor | codex-rs/app-server/src/request_processors/initialize_processor.rs | `InitializeRequestProcessor::initialize` |
| Duplicate initialize | 同上 | pre-check 与 atomic set error |
| Capability defaults | 同上 | `unwrap_or_default` |
| Opt-out normalization | 同上 | Vec → HashSet |
| Client-name validation | 同上 | `HeaderValue::from_str` |
| Session commit point | 同上 | `session.initialize(...)` |
| Originator/UA/residency effects | 同上 | commit 后 process-global updates |
| Initialize response construction | 同上 | UA、Codex home、platform family/OS |
| Bootstrap config warnings | 同上 | connection-targeted/global helpers |
| MCP extension selector | codex-rs/codex-mcp/src/client_capabilities.rs | `client_mcp_extensions` |
| Supported namespaces | 同上 | form 与 MCP App UI IDs |
| Legacy form normalization | 同上 | `entry(...).or_insert_with` |
| Extension unit tests | codex-rs/codex-mcp/src/client_capabilities_tests.rs | supported-only、legacy fallback |
| Default originator | codex-rs/login/src/auth/default_client.rs | `ORIGINATOR`、`DEFAULT_ORIGINATOR` |
| Originator precedence | 同上 | env override、first successful set |
| UA suffix | 同上 | `USER_AGENT_SUFFIX` |
| User-Agent builder | 同上 | `get_codex_user_agent`、sanitization |
| Residency owner | 同上 | `set_default_client_residency_requirement` |
| Incoming/outbound connection state | codex-rs/app-server/src/transport.rs | `ConnectionState`、`OutboundConnectionState` |
| Experimental/opt-out filter | 同上 | `should_skip_notification_for_connection` |
| Experimental request projection | 同上 | `filter_outgoing_message_for_connection` |
| Broadcast initialized gate | 同上 | `route_outgoing_envelope` |
| Common transport post-init | codex-rs/app-server/src/lib.rs | false→true session transition |
| Capability projection mirror | 同上 | outbound atomics/RwLock |
| Bootstrap warning/status order | 同上 | targeted notification sends |
| Thread capability registration | 同上 | `connection_initialized` |
| Final ready publication | 同上 | Release store |
| In-process initialize path | codex-rs/app-server/src/in_process.rs | typed request loop、early ready |
| Response targeting | codex-rs/app-server/src/outgoing_message.rs | `send_response`、`ToConnection` |
| Thread live connection registry | codex-rs/app-server/src/thread_state.rs | `ConnectionCapabilities` |
| Attestation candidate selection | 同上 | subscribed + capable + lowest ID |
| Attestation reverse request | codex-rs/app-server/src/attestation.rs | generate request、100ms timeout |
| Thread client identity copy | codex-rs/app-server/src/request_processors/thread_processor.rs | `set_app_server_client_info` |
| Client-version compatibility | 同上 | `xcode_26_4_mcp_elicitations_auto_deny` |
| MCP extensions to thread | 同上 | start/resume/fork argument flow |
| Core thread capability state | codex-rs/core/src/thread_manager.rs | `StartThreadOptions`、child inheritance |
| Request span client identity | codex-rs/app-server/src/app_server_tracing.rs | initialize params/session fallback |
| Hook client identity consumer | codex-rs/core/src/hook_runtime.rs | Hook payload `client` |
| stdio client-name side channel | codex-rs/app-server-transport/src/transport/stdio.rs | `stdio_initialize_client_name` |
| Name hint wiring | codex-rs/app-server/src/lib.rs | oneshot passed to remote-control startup |
| Originator/name validation tests | codex-rs/app-server/tests/suite/v2/initialize.rs | initialize integration tests |
| Notification opt-out test | 同上 | `initialize_opt_out_notification_methods_filters_notifications` |
| Per-connection initialize | codex-rs/app-server/tests/suite/v2/connection_handling_websocket.rs | two-client handshake |
| Experimental gate tests | codex-rs/app-server/tests/suite/v2/experimental_api.rs | capability required errors |
| Attestation integration | codex-rs/app-server/tests/suite/v2/attestation.rs | reverse request and header |
| MCP extensions integration | codex-rs/app-server/tests/suite/v2/mcp_tool.rs | downstream clientCapabilities |
| Bootstrap warning dedupe | codex-rs/app-server/tests/suite/v2/thread_start.rs | no repeated initialize warning |

## 源码精读系列 25：Request tracing、context ownership 与延迟回包

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| W3C carrier 数据结构 | codex-rs/protocol/src/protocol.rs | `W3cTraceContext` |
| Core submission trace 字段 | 同上 | `Submission.trace` |
| Span 导出 W3C carrier | codex-rs/otel/src/trace_context.rs | `span_w3c_trace_context` |
| Current span 导出 | 同上 | `current_span_w3c_trace_context` |
| Carrier 解析/合法性 | 同上 | `context_from_w3c_trace_context`、`context_from_trace_headers` |
| Parent 注入 | 同上 | `set_parent_from_w3c_trace_context`、`set_parent_from_context` |
| 环境 parent fallback | 同上 | `traceparent_context_from_env`、`load_traceparent_context` |
| Tracestate 合并 | 同上 | `merge_tracestate_entries` |
| App-server server span 模板 | codex-rs/app-server/src/app_server_tracing.rs | `app_server_request_span_template` |
| Wire span 创建 | 同上 | `request_span` |
| In-process span 创建 | 同上 | `typed_request_span` |
| Explicit/environment parent 顺序 | 同上 | `attach_parent_context` |
| Initialize client info enrichment | 同上 | `initialize_client_info`、`record_client_info` |
| Compound incoming request key | codex-rs/app-server/src/outgoing_message.rs | `ConnectionRequestId` |
| Request tracing context | 同上 | `RequestContext` |
| Span-first carrier fallback | 同上 | `RequestContext::request_trace` |
| Context registry owner | 同上 | `request_contexts` |
| Duplicate key replacement | 同上 | `register_request_context` warning |
| Request trace lookup | 同上 | `request_trace_context` |
| Turn ID late enrichment | 同上 | `record_request_turn_id` |
| Response terminal take | 同上 | `send_response_as_inner` |
| Error terminal take | 同上 | `send_error` |
| Send future instrumentation | 同上 | `send_outgoing_message_to_connection` |
| Disconnect registry purge | 同上 | `connection_closed` |
| Reverse callback map 对照 | 同上 | `request_id_to_callback`、`PendingCallbackEntry` |
| Raw request entry | codex-rs/app-server/src/message_processor.rs | `process_request` |
| Typed in-process entry | 同上 | `process_client_request` |
| Register-before-run | 同上 | `run_request_with_context` |
| Connection cleanup ordering | 同上 | `connection_closed`、RPC gate shutdown timeout |
| Turn Core trace helper | codex-rs/app-server/src/request_processors/turn_processor.rs | `request_trace_context`、`submit_core_op` |
| Turn/start submission carrier | 同上 | `submit_user_input_with_client_user_message_id` |
| Turn ID span record | 同上 | `record_request_turn_id` |
| Interrupt delayed ownership | 同上 | `pending_interrupts.push` |
| Interrupt terminal response | codex-rs/app-server/src/bespoke_event_handling.rs | `respond_to_pending_interrupts` |
| Thread Core trace helper | codex-rs/app-server/src/request_processors/thread_processor.rs | `request_trace_context`、`submit_core_op` |
| Thread/start background handoff | 同上 | `thread_start_inner`、`thread_start_task` |
| Thread creation parent trace | 同上 | `StartThreadOptions.parent_trace` |
| Delayed response/notification spans | 同上 | `app_server.thread_start.send_response`、`notify_started` |
| Core submission API | codex-rs/core/src/codex_thread.rs、session/mod.rs | `submit_with_trace`、submission enqueue |
| Core submission dispatch parent | codex-rs/core/src/session/handlers.rs | `submission_dispatch_span` |
| Test client carrier injection | codex-rs/app-server-test-client/src/lib.rs | `write_request` |
| Trace exporter test harness | codex-rs/app-server/src/message_processor_tracing_tests.rs | `RemoteTrace`、force flush、span topology helpers |
| Thread/start trace test | 同上 | `thread_start_jsonrpc_span_exports_server_span_and_parents_children` |
| Turn/start/Core trace test | 同上 | `turn_start_jsonrpc_span_parents_core_turn_spans` |
| Response context cleanup test | codex-rs/app-server/src/outgoing_message.rs tests | `send_response_clears_registered_request_context` |
| Disconnect context cleanup test | 同上 | `connection_closed_clears_registered_request_contexts` |

## 源码精读系列 26：Connection teardown、resource ownership 与有界排空

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| Transport close event | codex-rs/app-server-transport/src/transport/mod.rs | `TransportEvent::ConnectionClosed` |
| Connection origin | 同上 | `ConnectionOrigin` |
| Stdio EOF/read failure | codex-rs/app-server-transport/src/transport/stdio.rs | stdin reader loop |
| Stdio close emission | 同上 | `ConnectionClosed` send |
| Stdio writer channel terminal | 同上 | stdout writer loop |
| WebSocket acceptor stop | codex-rs/app-server-transport/src/transport/websocket.rs | shutdown token、graceful shutdown |
| WebSocket connection owner | 同上 | `run_websocket_connection` |
| Duplex task race | 同上 | inbound/outbound select、loser abort |
| WebSocket disconnect observation | 同上 | inbound/outbound loop token branch |
| Processor live connection map | codex-rs/app-server/src/lib.rs | `connections` |
| Close event orchestration | 同上 | remove、gate close、Outbound Closed、cleanup spawn |
| Stdio single-client exit | 同上 | `stdio_connection_closed` exit reason |
| Outbound control messages | 同上 | `OutboundControlEvent` |
| Router biased select | 同上 | control branch before envelope branch |
| Disconnect-all behavior | 同上 | request_disconnect + clear map |
| Signal state | 同上 | `ShutdownState` |
| Forceable/graceful-only signal | 同上 | `ShutdownSignal` |
| Running-turn graceful gate | 同上 | `ShutdownState::update` |
| Graceful processor drains | 同上 | gate join_all、cleanup/background/thread drain |
| Forced cleanup abort | 同上 | `connection_cleanup_tasks.abort` |
| Final handle joins | 同上 | processor、outbound、OTEL、accept handles |
| Outbound writer state | codex-rs/app-server/src/transport.rs | `OutboundConnectionState` |
| Disconnect request | 同上 | `request_disconnect` |
| Disconnected message drop | 同上 | `send_message_to_connection` |
| Per-connection RPC gate | codex-rs/app-server/src/connection_rpc_gate.rs | `ConnectionRpcGate` |
| Admission/token atomicity | 同上 | accepting mutex + `tasks.token()` |
| Close semantics | 同上 | `close` |
| Drain semantics | 同上 | `shutdown` |
| Future never-polled test | 同上 | `run_drops_future_without_polling_after_close` |
| Active handler survives close | 同上 | `close_returns_while_started_run_remains_active` |
| Shutdown waits active test | 同上 | `shutdown_waits_for_started_run_to_finish` |
| Late admission fence test | 同上 | `shutdown_drops_late_runs_while_waiting_for_inflight_work` |
| Cleanup JoinSet owner | codex-rs/app-server/src/connection_cleanup.rs | `ConnectionCleanupTasks` |
| Empty-set select behavior | 同上 | `reap_next` + `pending()` |
| Graceful drain/forced abort methods | 同上 | `drain`、`abort` |
| Connection drain deadline | codex-rs/app-server/src/message_processor.rs | `CONNECTION_RPC_DRAIN_TIMEOUT` |
| Per-owner cleanup order | 同上 | `connection_closed` |
| Global worker reference release | 同上 | `clear_runtime_references` |
| Request context cleanup | codex-rs/app-server/src/outgoing_message.rs | `connection_closed` |
| Reverse callback cancellation | 同上 | `cancel_all_requests` |
| Context isolation test | 同上 | `connection_closed_clears_registered_request_contexts` |
| FS watch connection key | codex-rs/app-server/src/fs_watch.rs | `WatchKey` |
| Watch RAII owners | 同上 | `WatchEntry` |
| Explicit unwatch acknowledgement | 同上 | `unwatch` |
| Connection watch cleanup | 同上 | `connection_closed` |
| Watch isolation test | 同上 | `connection_closed_removes_only_that_connections_watches` |
| Command session key | codex-rs/app-server/src/command_exec.rs | `ConnectionProcessId` |
| Command connection cleanup | 同上 | remove controls + `CommandControl::Terminate` |
| Process API session key | codex-rs/app-server/src/request_processors/process_exec_processor.rs | `ConnectionProcessHandle` |
| Process connection cleanup | 同上 | remove controls + `ProcessControl::Kill` |
| Thread connection indexes | codex-rs/app-server/src/thread_state.rs | `ThreadStateManagerInner` |
| Thread connection removal | 同上 | `remove_connection` |
| Has-subscriber projection | 同上 | `update_has_connections` |
| Listener cancellation fields | 同上 | `ThreadState::clear_listener` |
| Other subscriber preservation test | codex-rs/app-server/src/request_processors/thread_processor_tests.rs | `removing_auto_attached_connection_preserves_listener_for_other_connections` |
| Closed connection reattach fence test | 同上 | `closed_connection_cannot_be_reintroduced_by_auto_subscribe` |
| ThreadProcessor connection cleanup | codex-rs/app-server/src/request_processors/thread_processor.rs | `connection_closed` |
| App-server background drain | 同上 | `drain_background_tasks`、10s timeout |
| Core thread shutdown wrapper | 同上 | `shutdown_threads` |
| Shutdown report type | codex-rs/core/src/thread_manager.rs | `ThreadShutdownReport` |
| Concurrent bounded shutdown | 同上 | `shutdown_all_threads_bounded` |
| Shutdown-all test | codex-rs/core/src/thread_manager_tests.rs | `shutdown_all_threads_bounded_submits_shutdown_to_every_thread` |
| Per-thread shutdown call | codex-rs/core/src/codex_thread.rs | `shutdown_and_wait` |
| Session shutdown submission | codex-rs/core/src/session/mod.rs | `Op::Shutdown` + termination future |
| Graceful/forced integration tests | codex-rs/app-server/tests/suite/v2/connection_handling_websocket_unix.rs | Ctrl-C、SIGTERM、SIGHUP |
| In-process public shutdown | codex-rs/app-server/src/in_process.rs | `InProcessClientHandle::shutdown` |
| In-process staged processor cleanup | 同上 | processor task tail |
| Runtime reverse-request drain | 同上 | `cancel_all_requests` |
| Pending client response drain | 同上 | `pending_request_responses` |
| Explicit outbound router signal | 同上 | `outbound_shutdown_tx` |
| Processor/router deadline + abort | 同上 | `SHUTDOWN_TIMEOUT` |
| Analytics terminal flush | 同上 | `analytics_events_flush_client.flush` |

## 源码精读系列 27：Analytics、telemetry projection 与业务终态

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| Analytics client facade | codex-rs/analytics/src/client.rs | `AnalyticsEventsClient` |
| 有界异步队列 | 同上 | `AnalyticsEventsQueue`、`ANALYTICS_EVENTS_QUEUE_SIZE` |
| 非阻塞降级 | 同上 | `try_send`、queue full warning |
| Queue worker 与 flush barrier | 同上 | fact/flush message loop、`ANALYTICS_EVENTS_FLUSH_TIMEOUT` |
| HTTP timeout 与目标地址 | 同上 | `ANALYTICS_EVENTS_TIMEOUT`、analytics-events endpoint |
| Debug capture destination | 同上 | capture file initialization/append |
| Auth event filter | 同上 | `can_send_with_api_key_auth` 调用处 |
| Batch 与 fingerprint 隔离 | 同上 | `send_track_events` batching |
| App/plugin dedupe | 同上 | per-turn dedupe sets、`ANALYTICS_EVENT_DEDUPE_MAX_KEYS` |
| Internal fact enum | codex-rs/analytics/src/facts.rs | `AnalyticsFact` |
| Typed JSON-RPC analytics error | 同上 | `AnalyticsJsonRpcError` |
| Turn completion status | 同上 | `TurnStatus` |
| External event enum | codex-rs/analytics/src/events.rs | `TrackEventRequest` |
| App-server client metadata | 同上 | product/client/version/transport/experimental fields |
| Stateful projection owner | codex-rs/analytics/src/reducer.rs | `AnalyticsReducer` |
| Pending request correlation | 同上 | `RequestState`、compound request key |
| Thread/turn projection maps | 同上 | connection/thread/turn state maps |
| Tool item correlation | 同上 | started timestamps、pending tool responses |
| Tool response buffer bound | 同上 | `MAX_TOOL_RESPONSE_ENTRIES` |
| Review correlation | 同上 | pending reviews、review summaries |
| Thread initialized projection | 同上 | ThreadStart/Resume/Fork response branches |
| Thread originator override | 同上 | `ThreadAnalyticsState.originator` |
| Turn event readiness | 同上 | `maybe_emit_turn_event` |
| Missing-context degradation | 同上 | `thread_context_or_warn` and related warnings |
| TurnSteer accepted/rejected | 同上 | request/error response projection |
| Explicit interrupt enrichment | 同上 | earliest requested timestamp merge |
| Notification projection | 同上 | Turn/Item/Guardian notification branches |
| Flushable pending tool events | 同上 | reducer `flush` |
| Config-to-client construction | codex-rs/app-server/src/analytics_utils.rs | `analytics_events_client_from_config` |
| Shared client wiring | codex-rs/app-server/src/message_processor.rs | MessageProcessor、ThreadManager/Core dependencies |
| Initialized request track boundary | 同上 | `dispatch_initialized_client_request` |
| Initialize accepted fact | codex-rs/app-server/src/request_processors/initialize_processor.rs | `track_initialize` |
| Request tracking wrapper | 同上 | `track_initialized_request` |
| Response track-before-send | codex-rs/app-server/src/outgoing_message.rs | `send_response_as_inner` |
| Response originator projection | 同上 | `send_response_with_thread_originator` |
| Notification track-before-routing | 同上 | ThreadScoped/global `send_server_notification` |
| Reverse request tracking | 同上 | `send_request_to_connections` |
| Reverse response and abort | 同上 | notify/cancel methods |
| Replay without double-track | 同上 | `replay_requests_to_connection_for_thread` |
| Selected typed Turn errors | codex-rs/app-server/src/request_processors/turn_processor.rs | local `track_error_response` calls |
| TurnSteer rejection reasons | 同上 | `turn_steer_inner` |
| Interrupt terminal ordering | codex-rs/app-server/src/bespoke_event_handling.rs | pending interrupt response before terminal notification |
| Effective permissions result | 同上 | `track_effective_permissions_approval_response` |
| Core resolved config fact | codex-rs/core/src/session/turn.rs | `track_turn_resolved_config_analytics` |
| Core token/profile facts | codex-rs/core/src/tasks/mod.rs | terminal analytics calls |
| Core Codex error fact | codex-rs/core/src/session/mod.rs and task/compact modules | `track_turn_codex_error` |
| In-process terminal flush | codex-rs/app-server/src/in_process.rs | analytics flush before shutdown ack |
| Client filtering tests | codex-rs/analytics/src/client_tests.rs | request/response/notification selection tests |
| Queue flush tests | 同上 | `flush_waits_for_preceding_fact_delivery` |
| Projection payload tests | codex-rs/analytics/src/analytics_client_tests.rs | thread/turn/review/tool cases |
| App-server analytics harness | codex-rs/app-server/tests/suite/v2/analytics.rs | capture/wait/assert helpers |
| Turn end-to-end event | codex-rs/app-server/tests/suite/v2/turn_start.rs | `turn_start_tracks_thread_originator_in_analytics` |
| TurnSteer end-to-end events | codex-rs/app-server/tests/suite/v2/turn_steer.rs | accepted/rejected analytics |
| ThreadResume initialized event | codex-rs/app-server/tests/suite/v2/thread_resume.rs | resumed mode analytics |

## 源码精读系列 28：Error taxonomy、structured payload 与恢复策略

| 精读问题 | 固定提交中的路径 | 重点符号/区域 |
|---|---|---|
| Provider/API error 归一化 | codex-rs/codex-api/src/api_bridge.rs | `map_api_error` |
| Overload 与 cyber policy 特判 | 同上 | `server_is_overloaded`、`CYBER_POLICY_ERROR_CODE` |
| HTTP 429 分类 | 同上 | usage limit 与 `RetryLimitReachedError` branches |
| Core error wrapper | codex-rs/protocol/src/error.rs | `CodexErr` |
| 完整内部 taxonomy | 同上 | `CodexErrorDetails` |
| Payload-free analytics kind | 同上 | `CodexErrKind` |
| Sampling retry classifier | 同上 | `is_retryable` |
| Retry delay metadata | 同上 | `retry_delay`、`with_retry_delay` |
| Client-safe category mapping | 同上 | `to_codex_protocol_error` |
| Core ErrorEvent conversion | 同上 | `to_error_event` |
| HTTP status extraction | 同上 | `http_status_code_value` |
| Core public error enum | codex-rs/protocol/src/protocol.rs | `CodexErrorInfo` |
| Fatal/non-fatal predicate | 同上 | `affects_turn_status` |
| Terminal error event | 同上 | `ErrorEvent` |
| Intermediate stream event | 同上 | `StreamErrorEvent` |
| Shared retry executor | codex-rs/core/src/responses_retry.rs | `handle_retryable_response_stream_error` |
| WS→HTTPS fallback | 同上 | `try_switch_fallback_transport` call |
| Exponential backoff+jitter | codex-rs/core/src/util.rs | `backoff` |
| Retry defaults and hard caps | codex-rs/model-provider-info/src/lib.rs | `stream_max_retries` |
| Sampling error loop | codex-rs/core/src/session/turn.rs | retry/non-retryable branches |
| Invalid image user guidance | 同上 | `InvalidImageRequest` branch |
| Core terminal error capture | codex-rs/core/src/session/mod.rs | `send_event` |
| Retry notification construction | 同上 | `notify_stream_error` |
| Turn task terminal reduction | codex-rs/core/src/tasks/mod.rs | `on_task_finished` |
| JSON-RPC envelope types | codex-rs/app-server-protocol/src/rpc.rs | `JSONRPCError`、`JSONRPCErrorError` |
| App-server error code helpers | codex-rs/app-server/src/error_code.rs | `invalid_request`、`invalid_params`、`internal_error` |
| V2 error category translation | codex-rs/app-server-protocol/src/protocol/v2/shared.rs | `CodexErrorInfo` From impl |
| V2 Turn error object | codex-rs/app-server-protocol/src/protocol/v2/thread_data.rs | `TurnError` |
| V2 error notification | codex-rs/app-server-protocol/src/protocol/v2/notification.rs | `ErrorNotification`、`will_retry` |
| V2 final status | codex-rs/app-server-protocol/src/protocol/v2/turn.rs | `TurnStatus` |
| Raw request deserialize failure | codex-rs/app-server/src/message_processor.rs | `deserialize_client_request` |
| Request error terminal send | codex-rs/app-server/src/outgoing_message.rs | `send_error`、request context take |
| Structured input-too-large data | codex-rs/app-server/src/request_processors/turn_processor.rs | `input_too_large_error` |
| Structured steer rejection | 同上 | `ActiveTurnNotSteerable` branch |
| Core error to JSON-RPC mapping | codex-rs/app-server/src/request_processors/thread_processor.rs | `core_thread_write_error` |
| Core Error/StreamError projection | codex-rs/app-server/src/bespoke_event_handling.rs | matching `EventMsg` branches |
| Fatal summary recording | 同上 | `handle_error_notification` |
| Final completed/failed projection | 同上 | `handle_turn_complete` |
| Interrupted projection | 同上 | `handle_turn_interrupted` |
| Rollback error rerouting | 同上 | `handle_thread_rollback_failed` |
| Transport ingress overload | codex-rs/app-server-transport/src/transport/mod.rs | `OVERLOADED_ERROR_CODE`、writer `try_send` |
| Client overload retry contract | codex-rs/app-server/README.md | Backpressure behavior |
| In-process overload | codex-rs/app-server/src/in_process.rs | queue-full `-32001` errors |
| Stateful history reconstruction | codex-rs/app-server-protocol/src/protocol/thread_history.rs | `handle_error`、`handle_turn_complete` |
| Stateless terminal projection | codex-rs/app-server-protocol/src/protocol/thread_history_projection.rs | `project_rollout_line` |
| Retry classification test | codex-rs/protocol/src/error_tests.rs | `retryability_preserves_error_details_distinctions` |
| Protocol mapping test | 同上 | `to_error_event_handles_response_stream_failed` |
| Non-fatal predicate tests | codex-rs/protocol/src/protocol.rs tests | `does_not_affect_turn_status` |
| CamelCase serialization tests | codex-rs/app-server-protocol/src/protocol/v2/tests.rs | `codex_error_info_serializes_*` |
| Input rejection integration test | codex-rs/app-server/tests/suite/v2/turn_start.rs | `turn_start_rejects_combined_oversized_text_input` |
| Completion projection tests | codex-rs/app-server/src/bespoke_event_handling.rs tests | `test_handle_turn_complete_*`、interrupt test |
