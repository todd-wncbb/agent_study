# Grok Build 项目学习文档

这套文档是给第一次读这个项目的人准备的。目标不是把每一行代码都翻译成中文，而是帮你先建立地图：这个项目是什么、从哪里启动、用户输入怎么进入 agent、工具怎么被调用、文件和权限又在哪里处理。

## 先记住一句话

Grok Build 是一个 Rust 写的终端 AI 编程助手。它的可执行程序叫 `xai-grok-pager`，正式安装后通常以 `grok` 命令出现。代码大致分成四层：

1. `xai-grok-pager-bin`：真正的二进制入口，解析 CLI，决定启动哪种模式。
2. `xai-grok-pager`：终端 TUI，处理键盘、鼠标、滚动区、弹窗和渲染。
3. `xai-grok-shell` + `xai-grok-agent`：agent 运行时，管理认证、模型、会话、prompt、工具循环。
4. `xai-grok-tools` + `xai-grok-workspace`：具体工具、文件系统、权限、git/worktree、workspace RPC。

## 推荐阅读顺序

**主路线图：** [**12_reading_order.md**](12_reading_order.md) — 分阶段阅读顺序、代码文件清单、验收清单、按目标选捷径、3～4 小时最小路径。

| 顺序 | 文档 | 适合解决的问题 |
| --- | --- | --- |
| 1 | [01_project_map.md](01_project_map.md) | 这个仓库有哪些目录和 crate？我该先看哪里？ |
| 2 | [02_startup_modes.md](02_startup_modes.md) | `grok` 命令启动后经历了什么？有哪些运行模式？ |
| 3 | [03_tui_architecture.md](03_tui_architecture.md) | TUI 如何处理输入、状态、异步任务和渲染？ |
| 4 | [04_agent_session_runtime.md](04_agent_session_runtime.md) | agent、session、prompt、模型调用的主链路是什么？ |
| 5 | [05_tools_workspace_permissions.md](05_tools_workspace_permissions.md) | 工具如何注册、执行，文件和权限在哪里管？ |
| — | [**09_end_to_end_request_flow.md**](09_end_to_end_request_flow.md) | **从用户输入到多轮推理的完整代码级流程（prompt/skills/tools/历史）** |
| — | [**13_prompt_and_tool_list_assembly.md**](13_prompt_and_tool_list_assembly.md) | **System/User Prompt、Skill/附件/提醒与 Tool List 如何组装成最终模型请求** |
| — | [**14_complete_execution_timeline.md**](14_complete_execution_timeline.md) | **从 `main()`、TUI/ACP、Session 专用线程到 Sampling、Tool Loop、持久化和退出的完整时间线** |
| — | [**15_actor_channels_and_concurrency.md**](15_actor_channels_and_concurrency.md) | **Session 专用线程、LocalSet、Actor、Channel、Prompt Queue、Tool 并发、取消与输出顺序** |
| — | [**16_sampling_and_agent_loop.md**](16_sampling_and_agent_loop.md) | **Sampling iteration、stream event、Tool loop、auth/context retry、Structured Output、TodoGate 和 stationarity** |
| — | [**17_conversation_history_compaction_and_context_budget.md**](17_conversation_history_compaction_and_context_budget.md) | **Conversation History、token 双轨统计、Tool Result pruning、auto/error/preflight/model-switch compaction、two-pass prefire、状态恢复与跨压缩 rewind** |
| — | [**18_permissions_security_and_command_policy.md**](18_permissions_security_and_command_policy.md) | **AccessKind、规则 provenance、Permission Manager、Bash AST/路径/exec-risk、Auto classifier、持久授权、HITL、Sandbox 与 Agent loop 拒绝语义** |
| — | [**19_configuration_and_agent_definition.md**](19_configuration_and_agent_definition.md) | **TOML/requirements/MDM/Remote/Campaign 配置层级、Agent Markdown discovery、AgentDefinition 字段与 AgentBuilder 的 Prompt/Tool/Skill/MCP 最终装配** |
| — | [**20_subagent_and_task_system.md**](20_subagent_and_task_system.md) | **Task 协议、SubagentCoordinator 状态机、独立 Child Session、前后台等待、恢复/Fork/Worktree、查询取消、结果回流、持久化与孤儿修复** |
| — | [**21_scheduler_and_workflow_system.md**](21_scheduler_and_workflow_system.md) | **Scheduler 时间 Actor、版本与 durable tombstone、循环 Subagent 链，以及 Rhai Workflow、确定性 Journal、预算、并行 Agent、恢复、持久化和结果聚合** |
| — | [**22_context_compaction_token_budget_and_long_session_lifecycle.md**](22_context_compaction_token_budget_and_long_session_lifecycle.md) | **上下文 Token 估算与 provider 校准、自动压缩触发、Full-Replace 与输入降级梯、Two-Pass Prefire、状态重注入、Checkpoint、Rewind、Tool/Image 裁剪及多类执行预算** |
| — | [**23_permissions_approvals_trust_and_security_model.md**](23_permissions_approvals_trust_and_security_model.md) | **Folder Trust 与 Tool Permission 双门模型、TrustStore/审批记忆、Ask/Auto/Always-approve、企业 Pin、ACP/Hub 审批、MCP/Bash scope 校验、Subagent 继承及 Sandbox 纵深防御** |
| — | [**24_persistence_restore_replay_and_event_sourcing.md**](24_persistence_restore_replay_and_event_sourcing.md) | **Session 多轨持久化、updates/chat/summary 事实边界、JSONL 崩溃恢复、durable Ack、加载与游标续传、Checkpoint/Rewind、Workflow Journal、Subagent 恢复及混合事件溯源模型** |
| — | [**25_observability_logs_traces_metrics_and_diagnostics.md**](25_observability_logs_traces_metrics_and_diagnostics.md) | **Tracing/Debug Firehose/Unified Log/Session Events/Sampling Log 多通道路由、Instrumentation 与 Chrome Trace、产品 Telemetry、内部及外部 OTEL、分布式 Trace Context、隐私门控、指标基数与故障诊断方法** |
| — | [**26_testing_mocks_fixtures_and_reliability_validation.md**](26_testing_mocks_fixtures_and_reliability_validation.md) | **单元/Actor/协议/真实进程/PTY 分层测试，TestSandbox 与 TestProcess 资源所有权、Mock Inference/SSE、虚拟时间、Snapshot、Fuzz、Soak、平台测试、Flaky 治理及可靠性不变量** |
| — | [10_acp_protocol.md](10_acp_protocol.md) | ACP 协议与代码实现（TUI↔Agent） |
| — | [11_mcp_protocol.md](11_mcp_protocol.md) | MCP 协议与代码实现（Agent↔外部工具） |
| 6 | [06_learning_path.md](06_learning_path.md) | Rust 新手怎么分阶段读这个项目？ |
| 7 | [07_code_reading_recipes.md](07_code_reading_recipes.md) | 想追某个功能时，用哪些搜索命令和入口？ |
| 8 | [08_glossary.md](08_glossary.md) | 常见项目术语和 Rust 术语是什么意思？ |
| — | [**12_reading_order.md**](12_reading_order.md) | **完整阅读路线图（阶段 A～I、核心 crate、时间线）** |
| — | [codegen/README.md](codegen/README.md) | `crates/codegen/` 下全部 63 个 crate 的逐目录详解 |
| — | [tools/README.md](tools/README.md) | **全部 50 个静态 Tool、动态 MCP/Tool Pack、逐类实现与调用链** |

## 建议你先跑的命令

这些命令来自项目 README 和 workspace 配置，适合边读边验证：

```sh
cargo check -p xai-grok-pager-bin
cargo run -p xai-grok-pager-bin -- --version
cargo run -p xai-grok-pager-bin -- --help
```

如果你只想读代码，不急着编译，先从这些文件开始：

```text
Cargo.toml
README.md
crates/codegen/xai-grok-pager-bin/src/main.rs
crates/codegen/xai-grok-pager/src/app/mod.rs
crates/codegen/xai-grok-shell/src/agent/app.rs
crates/codegen/xai-grok-shell/src/agent/mvp_agent/acp_agent.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs
crates/codegen/xai-grok-tools/src/bridge.rs
crates/codegen/xai-grok-workspace/src/workspace_ops.rs
```

## 阅读心法

这个项目很大，不要试图从 `main.rs` 一路跳到每个函数。更好的方法是：先看 `lib.rs` / `mod.rs` 了解模块边界，再找一条具体链路，例如“发送 prompt”或“执行工具”，沿着枚举、trait、channel、handler 一步步追。

你会经常看到这些模式：

- `mod.rs`：目录模块的入口。
- `trait`：定义一种能力，比如文件系统、工具、workspace RPC。
- `Arc` / `Mutex` / `RefCell` / `Rc`：共享状态和单线程 actor 状态。
- `tokio::mpsc` / `oneshot`：异步任务之间传消息。
- `serde`：JSON / YAML / TOML 的序列化和反序列化。
- `clap`：命令行参数解析。

后面的文档会把这些概念放回真实代码里解释。
