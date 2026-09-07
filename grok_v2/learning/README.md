# Grok Build 源码学习手册

这是一套从当前仓库源码重新建立的学习资料。它的目标不是把 Rust 源文件翻译成中文，而是回答三个更有用的问题：系统如何工作、状态由谁拥有、修改会影响哪里。

## 基线

- 源码版本：`SOURCE_REV` 中记录的 `d6937fe255dce4133c3d000a50f9cb94de12f06f`
- Rust 工具链：`rust-toolchain.toml` 固定的 `1.94.0`
- Workspace：根 `Cargo.toml` 中声明 81 个成员
- 事实来源：本仓库源码、每个 crate 的 `Cargo.toml`、测试和随源码维护的注释

版本号很重要：函数、类型和模块会变化。如果以后更新源码，应先核对 `SOURCE_REV`，再信任本文中的路径和调用关系。

## 从哪里开始

第一次阅读，按下面顺序：

1. [项目导读](00-guides/01-project-orientation.md)：先建立产品、进程和代码分层的心智模型。
2. [Workspace 全景图](01-architecture/01-workspace-map.md)：知道核心 crate 如何协作，哪些目录暂时可以跳过。
3. [分层阅读路线](00-guides/02-learning-routes.md)：根据可用时间和学习目标选择路径。
4. `02-runtime-flows/`：沿一次真实请求理解系统，而不是逐 crate 漫游。
5. `03-subsystems/`：按权限、会话、配置、协议等专题深入。
6. `04-crate-reference/`：需要查模块边界和公开 API 时使用。
7. `05-code-walkthroughs/`：用具体场景复核调用链。
8. `06-rust-patterns/`、`07-debugging/` 和 `08-exercises/`：把阅读转化为工程能力。

## 文档地图

| 目录 | 用途 | 推荐用法 |
| --- | --- | --- |
| `00-guides` | 导读、阅读路线、使用方法 | 第一次读 |
| `01-architecture` | 全局结构、依赖和边界 | 建立地图 |
| `02-runtime-flows` | 端到端运行链路 | 主线精读 |
| `03-subsystems` | 独立子系统的设计与实现 | 专题深入 |
| `04-crate-reference` | crate 和模块速查 | 定位代码 |
| `05-code-walkthroughs` | 真实功能的逐步源码追踪 | 验证理解 |
| `06-rust-patterns` | 项目中的 Rust 工程模式 | 学语言与设计 |
| `07-debugging` | 编译、测试和故障定位 | 开发维护 |
| `08-exercises` | 问题、实验和综合任务 | 主动学习 |
| `appendices` | 术语、源码索引、变更记录 | 随手查询 |

## 核心问题索引

文档完成后，可以从问题直接进入相应章节：

| 问题 | 文档 |
| --- | --- |
| 程序有哪些入口和运行模式？ | [启动链路](02-runtime-flows/01-startup.md) |
| 一条 prompt 如何变成最终回答？ | [Prompt 到最终回答](02-runtime-flows/02-prompt-to-answer.md) |
| 模型请求在哪里真正发出？ | [Sampling 与 Agentic Loop](02-runtime-flows/03-sampling-loop.md) |
| 工具如何注册、审批和执行？ | [工具从注册到执行](02-runtime-flows/04-tool-execution.md) |
| 文件和命令为什么会要求授权？ | [权限、目录信任与 Sandbox](02-runtime-flows/05-permission-and-sandbox.md) |
| 长会话如何压缩和恢复？ | [会话上下文、裁剪与压缩](02-runtime-flows/06-session-context.md) |
| Subagent 如何建立和回传结果？ | [Subagent 从创建到结果回传](02-runtime-flows/07-subagent-task.md) |
| TUI 如何消费流式事件？ | [TUI 事件循环：从键盘输入到终端重绘](02-runtime-flows/08-tui-event-loop.md) |
| ACP 与 MCP 分别位于哪里？ | [ACP 与 MCP：Agent 控制面和外部工具面的边界](02-runtime-flows/09-acp-and-mcp.md) |
| 配置覆盖优先级是什么？ | [配置系统：从多层输入到运行时生效值](02-runtime-flows/10-configuration.md) |
| 会话状态如何落盘和重放？ | [会话持久化：落盘、恢复、重放与故障边界](02-runtime-flows/11-persistence.md) |
| 多个 Actor 分别拥有哪部分状态？ | [状态所有权：MvpAgent、SessionActor、ChatStateActor 与 TUI](03-subsystems/01-state-ownership.md) |
| 通知如何缓冲、落盘、重放并避免重复？ | [通知与事件路由：缓冲、持久化、重放与客户端去重](03-subsystems/02-notification-routing.md) |
| 多个 Prompt 如何排队、打断并依次完成？ | [Prompt 队列与 Turn 调度：排队、提升、打断、完成与自动唤醒](03-subsystems/03-prompt-queue-and-turn-scheduling.md) |
| 工具如何注册并获得 Session 依赖？ | [ToolBridge、工具注册表与 Resources：工具依赖、注册与执行](03-subsystems/04-tool-bridge-registry-and-resources.md) |
| 一次工具调用如何经过权限、审批与 Sandbox？ | [权限判定与审批状态机：Policy、用户交互、目录信任与 Sandbox](03-subsystems/05-permission-approval-state-machine.md) |
| AgentDefinition 如何变成运行中的 Agent，又何时需要重建？ | [Agent 构建、配置覆盖与运行时重建：从 Definition 到 Session-bound Agent](03-subsystems/06-agent-build-configuration-and-rebuild.md) |
| MCP Server 如何启动、刷新能力并把动态工具接入 Agent？ | [MCP Server 生命周期、能力刷新与动态工具：初始化、注册、调用、恢复与重载](03-subsystems/07-mcp-server-lifecycle-and-dynamic-tools.md) |
| Subagent 如何调度、继承父状态并在后台完成？ | [Subagent 调度、父子状态继承与后台任务协调：Coordinator、Child Session、取消与结果回传](03-subsystems/08-subagent-coordinator-inheritance-and-background-tasks.md) |
| 模型实际看到的 Prompt 如何构建，动态上下文又如何注入？ | [Prompt 构建、上下文注入与 System Reminder：静态身份、首轮前缀、动态状态与压缩重建](03-subsystems/09-prompt-construction-context-injection-and-reminders.md) |
| 跨会话 Memory 如何存储、检索、注入并整理？ | [Memory 子系统：Markdown 存储、混合检索、上下文注入、Flush 与 Dream](03-subsystems/10-memory-storage-retrieval-flush-and-dream.md) |
| Hooks 如何匹配生命周期事件、执行外部逻辑并改变工具或停止流程？ | [Hooks 子系统：事件、匹配、执行、Gate 与信任边界](03-subsystems/11-hooks-events-dispatch-gates-and-trust.md) |
| 模型如何选择 Provider、转换请求、合并流式响应并分层恢复错误？ | [模型请求与 Provider 子系统：模型目录、协议转换、流式响应、重试与恢复](03-subsystems/12-model-provider-request-streaming-retry-and-recovery.md) |
| Token 如何估算和计费，Context Window 又如何驱动自动压缩？ | [Token 计量、Context Window 与自动压缩决策：估算、真实 Usage、阈值、预算与压缩恢复](03-subsystems/13-token-accounting-context-window-and-compaction-policy.md) |
| 日志、Telemetry、Tracing、Metrics 和 Trace Artifact 分别解决什么问题？ | [日志、Telemetry、Tracing 与可观测性：本地诊断、事件管线、分布式 Trace、Metrics 与隐私边界](03-subsystems/14-logging-telemetry-tracing-and-observability.md) |
| 并发任务由谁拥有，Actor、Channel、取消和 Shutdown 如何协作？ | [并发模型、Actor、Channel 与取消传播：线程隔离、任务所有权、背压和有序关闭](03-subsystems/15-concurrency-actors-channels-cancellation-and-shutdown.md) |
| 错误如何分类，什么时候重试、降级、熔断或直接失败？ | [错误分类、重试、降级与恢复状态机：Typed Error、Backoff、认证恢复、熔断和终态映射](03-subsystems/16-error-taxonomy-retry-degradation-and-recovery-state-machines.md) |
| OAuth、API Key、BYOK 和 Session Token 如何选择、刷新并避免发错 Endpoint？ | [认证、凭据与 Endpoint 信任边界：OAuth、API Key、BYOK、刷新、隔离与失效恢复](03-subsystems/17-authentication-credentials-endpoint-trust-and-refresh.md) |
| Bash、读取、搜索和编辑等内置工具内部如何执行并返回结果？ | [内置工具实现与执行语义：Bash、读取、搜索、编辑、后台任务和输出契约](03-subsystems/18-builtin-tools-execution-semantics-and-output-contracts.md) |
| 文件工具如何跨本地与 ACP 后端访问真实工作区，路径和安全规则又如何分层？ | [文件系统抽象、工作区路径与安全边界：本地/ACP 后端、路径映射、Gitignore 与策略分层](03-subsystems/19-filesystem-path-workspace-and-safety-boundaries.md) |
| 应该怎样分层验证这个仓库，Fixtures、Snapshots、PTY 和兼容测试分别证明什么？ | [测试体系、Fixtures、Snapshots 与兼容性验证：从纯函数到 PTY、协议和压力测试](03-subsystems/20-testing-fixtures-snapshots-and-compatibility-validation.md) |
| Bash、PowerShell、进程组和 PTY 如何跨平台协作，异常退出后终端又怎样恢复？ | [跨平台 Shell、Terminal、PTY 与进程生命周期：命令语法、TTY 隔离、终止和终端恢复](03-subsystems/21-cross-platform-shell-terminal-pty-and-process-lifecycle.md) |
| 源码如何变成跨平台发行包，新版本又如何安全下载、激活、回滚和重启？ | [构建、打包、发布通道与原子更新：版本注入、跨平台产物、安装器和回滚边界](03-subsystems/22-build-packaging-release-channels-and-atomic-update.md) |
| Plugin、Skill 和 Marketplace 如何发现、命名、信任、安装并进入具体 Session？ | [插件、Skills、Marketplace 与扩展生命周期：发现、信任、命名、快照、热刷新和执行边界](03-subsystems/23-plugins-skills-marketplace-and-extension-lifecycle.md) |
| Rewind 如何同时恢复对话、文件和 Git，Fork、Worktree 与 Session 恢复又如何协作？ | [Git、Checkpoint、Rewind 与 Worktree：多域快照、冲突检测、会话分叉和代码恢复](03-subsystems/24-git-checkpoints-rewind-worktrees-and-session-recovery.md) |
| 一条真实 Prompt 如何跨过 ACP、SessionActor、Sampler、流式通知和持久化后完成？ | [Walkthrough：一条 Prompt 如何从 ACP 请求变成可重放的流式回答](05-code-walkthroughs/01-one-prompt-from-acp-to-persisted-answer.md) |
| 模型发起 Bash Tool Call 后，如何经过解析、Hook、权限、Terminal 执行并把结果送回模型？ | [Walkthrough：Bash Tool Call 如何经过权限、执行并回到下一轮采样](05-code-walkthroughs/02-bash-tool-call-permission-sandbox-and-result-loop.md) |
| 一次文件编辑如何从读取依据变成精确替换、Diff、LSP 反馈和可回退快照？ | [Walkthrough：一次 SearchReplace 如何从读取依据变成可回退的文件修改](05-code-walkthroughs/03-search-replace-from-read-evidence-to-rewindable-edit.md) |
| 工具解析失败、Hook/权限拒绝或运行中取消后，UI、Turn 和对话如何收敛？ | [Walkthrough：工具失败、拒绝与取消如何收敛并修复对话](05-code-walkthroughs/04-tool-failure-rejection-cancellation-and-conversation-repair.md) |
| 上下文接近上限后，系统如何估算 Token、触发压缩、替换历史并从失败或取消中恢复？ | [Walkthrough：自动压缩如何重建可继续的对话上下文](05-code-walkthroughs/05-auto-compaction-token-budget-summary-and-recovery.md) |
| 重新打开 Session 时，模型历史、UI 时间线、运行态和崩溃残留如何分别恢复？ | [Walkthrough：一次 Session Load 如何恢复模型状态、重放 UI 并修复崩溃残留](05-code-walkthroughs/06-session-load-resume-replay-and-crash-recovery.md) |
| 用户 Rewind 到某个 Prompt 前时，对话、文件、Git、Hunk 和压缩历史如何协调恢复？ | [Walkthrough：一次 Rewind 如何协调对话、文件、Git 与压缩边界](05-code-walkthroughs/07-rewind-conversation-files-git-and-compaction-boundaries.md) |
| Fork Session 时，历史、事件、路径、压缩档案和 Worktree 如何复制并保持父分支不变？ | [Walkthrough：一次 Session Fork 如何复制历史并隔离 Worktree](05-code-walkthroughs/08-session-fork-history-copy-path-rewrite-and-worktree-isolation.md) |
| Worktree 子任务完成后，代码成果如何应用、处理冲突、保存快照并安全清理？ | [Walkthrough：Worktree 成果如何应用、保留、恢复与安全清理](05-code-walkthroughs/09-worktree-result-apply-conflict-snapshot-and-cleanup.md) |
| 模型请求遇到限流、认证失效、流中断或上下文超限后，如何重试、恢复并收敛？ | [Walkthrough：模型请求如何重试、刷新认证、压缩恢复并收敛失败](05-code-walkthroughs/10-model-request-retry-auth-refresh-context-recovery-and-terminal-failure.md) |
| 一个 MCP Server 如何连接、发现工具、接受调用、响应能力变化并在断线后恢复？ | [Walkthrough：一个 MCP Server 如何连接、发现工具、接受调用、恢复并退出](05-code-walkthroughs/11-mcp-server-connect-discover-call-refresh-recover-and-shutdown.md) |
| 模型提出 Tool Call 后，如何经过参数解析、权限、Hooks 和并发调度，再把结果返回模型？ | [Walkthrough：一条 Tool Call 如何经过解析、权限、Hooks、执行并返回模型](05-code-walkthroughs/12-tool-call-parse-permission-hooks-dispatch-output-and-model-feedback.md) |
| 一个终端命令进入后台后，如何保存输出、等待或取消，并在完成时避免重复唤醒模型？ | [Walkthrough：后台终端任务如何启动、转入后台、等待、取消并自动唤醒模型](05-code-walkthroughs/13-background-terminal-task-start-transition-wait-kill-auto-wake.md) |
| 父模型创建一个 Subagent 后，系统如何验证请求、建立 Child Session、协调完成与取消并回传结果？ | [Walkthrough：一次 Subagent Task 如何验证、创建 Child Session、完成并回传](05-code-walkthroughs/14-subagent-task-validate-coordinate-child-session-complete-cancel.md) |
| Workflow 如何启动、编排顺序与并行 Agent、通过 Journal 暂停恢复，并在安全收拢后回传结果？ | [Walkthrough：一次 Workflow 如何启动、编排并行 Agent、Journal 恢复并回传完成](05-code-walkthroughs/15-workflow-launch-rhai-parallel-journal-resume-completion.md) |
| Goal 如何创建、规划、跨多个模型 Round 自动续跑，并通过验证、预算与阻塞规则收敛？ | [Walkthrough：一次 Goal 如何创建、规划、跨 Turn 续跑并完成或阻塞](05-code-walkthroughs/16-goal-create-plan-autonomous-turns-verification-budget-completion.md) |
| Plan Mode 如何进入、限制代码写入、维护计划文件、等待审批，并在批准后安全切回执行语义？ | [Walkthrough：一次 Plan Mode 如何进入、限制写入、审批计划并切回执行](05-code-walkthroughs/17-plan-mode-entry-write-gate-plan-approval-and-execution-transition.md) |
| Ask Prompt Mode 如何从客户端传播到 Turn，它为什么不等于权限 Ask，真正的只读 Capability 又在哪里生效？ | [Walkthrough：Ask Prompt Mode 如何传播，以及它为什么不等于 Permission Ask](05-code-walkthroughs/18-ask-prompt-mode-permission-ask-and-read-only-capability-boundaries.md) |
| Session 切换模型时，如何校验目录和 Harness 兼容性、重建零 Turn Agent，并同步采样、压缩、认证与客户端状态？ | [Walkthrough：一次 Session Model Switch 如何校验、重建 Harness 并更新采样与认证](05-code-walkthroughs/19-session-model-switch-catalog-harness-rebuild-sampling-auth-and-isolation.md) |
| Structured Output 如何选择原生 JSON Schema 或合成工具路径，完成本地验证、有限重试并确定性返回结果？ | [Walkthrough：Structured Output 如何选择原生 Schema 或合成工具、验证并返回结果](05-code-walkthroughs/20-structured-output-json-schema-native-tool-fallback-validation-and-delivery.md) |
| Tool Overrides 如何随 Prompt 排队，在晋升时限制 Hosted Search，回显实际生效值并把安全边界传给 Subagent？ | [Walkthrough：Tool Overrides 如何在 Prompt 晋升时约束搜索、回显并传给 Subagent](05-code-walkthroughs/21-tool-overrides-search-cutoff-promotion-hosted-tools-echo-and-subagent-inheritance.md) |
| 用户在 Turn 运行中追加消息时，Interjection 如何安全注入当前上下文，尾部竞态如何兜底，它又为何不等于 Send Now？ | [Walkthrough：Mid-turn Interjection 如何安全注入，以及它与 Cancel、Send Now 的边界](05-code-walkthroughs/22-mid-turn-interjection-buffer-safe-drain-fallback-images-skills-and-send-now-boundary.md) |
| 一张粘贴图片如何保持 chip 与字节配对，经过路径安全、格式校验、压缩或视觉转写，最终进入模型、历史与请求预算？ | [Walkthrough：图片如何从粘贴进入 Prompt、模型上下文与持久化历史](05-code-walkthroughs/23-image-input-paste-chips-placeholder-recovery-normalization-transcription-persistence-and-budget.md) |
| `/btw` 与 Session Recap 如何借用主会话上下文做一次性辅助采样，同时处理缓存、并发与显示，又不污染正式 Conversation？ | [Walkthrough：`/btw` 与 Session Recap 如何读取快照、复用缓存并保持显示态隔离](05-code-walkthroughs/24-side-question-btw-and-session-recap-snapshot-one-shot-cache-isolation-gating-and-display-only.md) |
| Agent Turn 结束后，系统如何低成本预测用户下一条 Prompt，过滤不可靠输出，处理迟到响应并把候选安全显示为 Ghost Text？ | [Walkthrough：Next-Prompt Suggestion 如何从 Turn End 生成、过滤并进入编辑器](05-code-walkthroughs/25-next-prompt-suggestion-turn-end-transcript-model-routing-sanitization-generation-ghost-acceptance.md) |
| Bash 模式输入变化或按下 Tab 后，History、PATH、File 和 AI 候选如何聚合，并在引号、旧响应和局部替换风险下安全写入 Draft？ | [Walkthrough：Bash Shell Completion 如何聚合候选、执行 LCP 并安全替换 Token](05-code-walkthroughs/26-bash-shell-completion-debounce-history-path-file-ai-ranking-token-splice-tab-lcp-and-staleness.md) |
| 用户启动 Voice Dictation 后，麦克风、鉴权、STT WebSocket、Interim/Final、目标绑定和 Submit 如何协作，且不自动发送或污染错误 Prompt？ | [Walkthrough：Voice Dictation 如何流式转写并安全合并进 Prompt](05-code-walkthroughs/27-voice-dictation-gates-target-binding-lazy-pipeline-audio-capture-websocket-stt-interim-final-submit-and-recovery.md) |
| 多行文本粘贴如何避免被 Enter 提前发送，又如何经过终端碎片合并、路径分类、原子 Chip、预览展开和异步附件竞态后完整进入 Prompt？ | [Walkthrough：文本粘贴如何跨过终端碎片、路由和原子 Chip](05-code-walkthroughs/28-text-paste-terminal-coalescing-routing-drop-classification-atomic-chips-preview-expansion-and-deferred-send.md) |
| Minimal Mode 如何把普通 Prompt Draft 安全交给 `$VISUAL/$EDITOR`，并在 TTY 争用、富文本附件、异步状态变化、非法结果和终端恢复中保证不丢 Draft？ | [Walkthrough：外部编辑器如何接管 Prompt 并安全回写](05-code-walkthroughs/29-external-prompt-editor-entry-gates-tty-handoff-temp-file-stale-write-protection-restore-and-failure-recovery.md) |
| Prompt History 如何按工作区与 Session 持久化、即时合并和去重，又如何让 Up 浏览与 `/history` 模糊搜索共享面板而不丢 Draft 或混淆 Bash Mode？ | [Walkthrough：Prompt History 如何持久化、搜索并安全恢复 Draft](05-code-walkthroughs/30-prompt-history-jsonl-session-scope-merge-dedup-async-fuzzy-search-browse-draft-restore-and-mode-safety.md) |
| Slash Command 如何把 Builtin 与 ACP 动态命令注册到同一 Catalog，经过冲突处理、可见性与模式限制、模糊补全和参数契约，再安全映射成 Action、队列、Skill 注入或透传，同时保住 Palette 打开前的 Draft？ | [Walkthrough：Slash Command 如何从注册、补全走到执行与透传](05-code-walkthroughs/31-slash-command-builtins-acp-registry-collisions-visibility-fuzzy-completion-arguments-dispatch-and-pass-through.md) |
| `@` 文件引用如何从 Cursor Token 触发异步模糊搜索，完成隐藏文件、目录下钻、原子 Chip 和行号选择，再由 Shell 读取文件、限制预算并注入模型上下文；Pager 与 Shell 的语法边界又有哪些当前错位？ | [Walkthrough：`@` 文件引用如何从模糊补全变成模型上下文](05-code-walkthroughs/32-at-file-reference-context-detection-fuzzy-walk-hidden-mode-directory-drill-atomic-chip-line-viewer-shell-attachment-and-context-budget.md) |
| Prompt Queue 如何在 Pager 本地队列和 Shell 权威共享队列之间选择路线、维持全局 FIFO，又如何处理乐观 Echo、合并、编辑、换序、Send now、Turn Adoption 与附件恢复？ | [Walkthrough：Prompt Queue 如何在本地与服务端之间排队、编辑、提升并对账](05-code-walkthroughs/33-prompt-queue-local-server-authority-fifo-immediate-send-combine-edit-reorder-send-now-adoption-and-attachment-recovery.md) |
| Scrollback 如何把 Prompt、Answer、Thinking 与 Tool Block 组织成稳定时间线，又如何通过高度估算、增量布局、窗口化绘制、分组、Sticky Prompt 与 Follow Mode 支撑长会话？ | [Walkthrough：Scrollback 如何把流式事件变成稳定、可滚动的对话时间线](05-code-walkthroughs/34-scrollback-block-entry-state-layout-cache-streaming-windowed-render-sticky-group-and-follow-mode.md) |
| 鼠标从屏幕字符开始拖动后，系统如何命中稳定文本坐标、跨视口自动滚动、恢复软换行与 Unicode 文本，并把 Box-drawing Table 转换为 Cell/Grid 选择和 TSV？ | [Walkthrough：鼠标选择如何从屏幕坐标恢复成准确的文本与表格](05-code-walkthroughs/35-mouse-text-selection-hit-testing-drag-autoscroll-word-url-table-cell-grid-tsv-and-clipboard-reconstruction.md) |
| Markdown、裸 URL、Tool Path、Session Media 与 Citation 如何汇入同一链接系统，并在 OSC 8、应用点击、终端原生打开和 VS Code Remote 委托之间安全分配所有权？ | [Walkthrough：链接如何从文本语义变成安全、可点击的终端目标](05-code-walkthroughs/36-hyperlinks-semantic-target-overlay-osc8-visible-map-hover-click-file-path-remote-delegation-and-safety.md) |
| 一段已经重建好的复制文本，如何通过 Native、tmux Buffer 与 OSC 52 多路投递，在 SSH、容器、Wayland 和嵌套终端中判断可信交付，并通过 `grok wrap` 与 Owner-only 文件保证可恢复？ | [Walkthrough：复制内容如何跨过本地、tmux、SSH、容器与 OSC 52 到达用户](05-code-walkthroughs/37-clipboard-multifire-native-tmux-osc52-trust-delivery-backup-file-wrap-bridge-and-diagnostics.md) |
| 长会话中的 Prompt、回答、Thinking 与工具输出如何变成可搜索 Corpus，在后台合并查询、拒绝迟到结果、展开隐藏命中并映射回 Unicode 与软换行后的屏幕高亮？ | [Walkthrough：Scrollback 全文搜索如何异步索引、导航并映射回屏幕高亮](05-code-walkthroughs/38-scrollback-full-text-search-index-snapshot-background-daemon-generation-regex-navigation-reveal-and-render-highlight.md) |
| 当前 Active Agent 或磁盘 Session 如何被整理成可读 Markdown，并在跳过 Thinking、摘要 Tool Call 后，通过路径补全、文件、剪贴板或 stdout 安全交付？ | [Walkthrough：会话如何从 Live Scrollback 或 Session Replay 导出为 Markdown](05-code-walkthroughs/39-conversation-export-live-scrollback-session-replay-markdown-projection-tool-summary-path-completion-file-clipboard-and-feedback.md) |

尚未落地的链接以代码样式显示，不制造空链接；对应文章完成后再改成可点击链接。

### 源码精读系列

源码精读以一个核心文件或紧密模块为单位，按字段、函数、所有权、并发边界、Rust 语法和测试逐段阅读；具体行号绑定文档记录的 Git 基线，源码漂移后优先按符号名重定位。

| 想精读的问题 | 文档 |
| --- | --- |
| `scrollback/search.rs` 如何用 `Arc`、Channel、Mutex、Generation 和 Poll 组成一个可拒绝迟到结果的后台搜索状态机，每组测试又在固定什么不变量？ | [源码精读 01：`scrollback/search.rs` 的索引、后台线程、状态机与测试](06-source-deep-dives/01-scrollback-search-rs-line-by-line-index-daemon-state-machine-and-tests.md) |
| `scrollback/state/mod.rs` 如何同时维护稳定 Entry 身份、顺序索引、流式内容、两类 Generation、Minimal Commit、Continuation 与三段式布局收敛？ | [源码精读 02：`scrollback/state/mod.rs` 如何维护时间线的权威状态](06-source-deep-dives/02-scrollback-state-mod-rs-authoritative-entry-store-generations-mutation-layout-cache-and-continuation.md) |
| `scrollback/state/layout.rs` 如何用全历史估算、视口精测、Virtual Y、Scroll Anchor 和 Paint Window，在长会话中同时保持性能与几何正确？ | [源码精读 03：`scrollback/state/layout.rs` 的惰性测量、虚拟坐标与绘制窗口](06-source-deep-dives/03-scrollback-state-layout-rs-lazy-measurement-virtual-y-scroll-anchor-hit-test-and-paint-window.md) |
| Agent 的 System Prompt、AGENTS.md、Skill Catalog、环境前缀、真人输入、历史和 Tool Schema，究竟按什么顺序进入最终模型请求？ | [源码精读 04：Agent Prompt 如何从模板、项目指令、Skills、用户输入和历史组装成模型请求](06-source-deep-dives/04-agent-prompt-assembly-system-template-project-instructions-skills-user-message-history-and-request.md) |
| Skill 如何从磁盘、配置、插件和运行期路径被发现，经过 Frontmatter、Scope 与冲突去重成为 Catalog，又如何由 Slash、模型 Read 或 Agent 预加载取得完整正文？ | [源码精读 05：Skill 如何发现、解析、去重、公告并加载完整 SKILL.md](06-source-deep-dives/05-agent-skills-discovery-frontmatter-scope-precedence-dedup-catalog-activation-and-full-loading.md) |
| Tool 如何从 Rust 类型进入候选 Registry，经 Agent 配置、Finalize、Schema 与描述渲染成为模型 Function；MCP Tool 又为什么隐藏在 BM25 `search_tool` 与 `use_tool` 后面？ | [源码精读 06：Tool 如何注册、Finalize、生成 Schema、搜索 MCP Tool 并进入模型请求](06-source-deep-dives/06-agent-tools-static-registration-finalization-schema-mcp-bm25-search-meta-dispatch-and-request.md) |
| 一个 User Turn 为什么能包含多个模型请求；流式文本、Reasoning 与 Tool Call 如何排序；Tool 又如何经过解析、Hook、Permission、并发 Dispatch 和 Result 回填后驱动下一轮，最终由什么条件结束？ | [源码精读 07：Agentic Loop 如何流式采样、执行 Tool、回填结果并决定结束](06-source-deep-dives/07-agentic-loop-streaming-response-tool-call-batch-permission-execution-result-resampling-and-termination.md) |
| Chat State 为什么采用 Actor；Conversation 如何串行追加和修复；Build Request 如何隔离裁剪、图片与 Memory；Token、Compaction、Rewind、Turn Capture 和 Persistence 又如何保持一致？ | [源码精读 08：Chat State Actor 如何维护 Conversation、构建请求并安全替换历史](06-source-deep-dives/08-chat-state-actor-command-serialization-conversation-integrity-request-snapshot-token-accounting-compaction-rewind-and-persistence.md) |
| Sampler 如何把同一 Conversation 转成三种 API，归一化流式事件，并通过 Attempt 隔离、Typed Retry、图片降级、Doom Recovery 与取消只提交唯一终态？ | [源码精读 09：Sampler 如何统一三种 API、流式状态机、重试与失败隔离](06-source-deep-dives/09-sampler-layered-client-backend-conversion-stream-state-machines-canonical-response-retry-cancellation-and-failure-isolation.md) |
| SamplingClient 如何组合 Endpoint、Query 与多层 Header，动态读取认证、隔离共享连接、归因 401，并把 HTTP/SSE、非标准 Wire 字段和错误安全转换成 Typed Stream？ | [源码精读 10：SamplingClient 如何构建请求、动态认证、解析 SSE 并保住错误语义](06-source-deep-dives/10-sampling-client-config-endpoint-query-header-live-auth-credential-attribution-http-pooling-sse-decoding-error-sanitization-and-terminal-overrides.md) |
| Sampler Event 回到 Session 后，流式 Text、Reasoning、Tool Delta、Backend Tool、Retry 与 Failure 如何分流；为什么只有最终 Response 能提交 Chat State；Barrier、Capture、401 与 Compaction 又如何让 Turn 有序且只收敛一次？ | [源码精读 11：Session 如何消费 Sampler Event、提交最终响应并让 Turn 收敛](06-source-deep-dives/11-session-sampler-event-bridge-streaming-capture-event-ordering-chat-state-commit-auth-recovery-compaction-and-turn-convergence.md) |
| Agent 如何用 Provider Token 与本地增量判断上下文占用；自动压缩如何触发、生成续接摘要、恢复项目与运行态、校验 Tool 历史并原子替换 Conversation；输入超窗和压缩失败又如何降级且避免死循环？ | [源码精读 12：Agent 如何估算上下文、生成压缩摘要并重建 Conversation](06-source-deep-dives/12-agent-context-window-token-estimation-auto-compaction-summary-rebuild-input-ladder-two-pass-prefire-suppression-and-history-reseed.md) |
| Agent Memory 与 Chat History、Compaction、`AGENTS.md` 有何区别；Markdown 如何按 Scope 落盘、切块和增量索引；FTS/Vector 怎样混合召回；首轮注入、Search/Get Tool、Flush、Session-end、Dream 与 Subagent 又怎样组成跨会话知识循环？ | [源码精读 13：Agent Memory 如何存储、检索、注入、沉淀与隔离](06-source-deep-dives/13-agent-memory-storage-scope-markdown-chunking-index-hybrid-search-initial-injection-tools-flush-dedup-dream-and-subagent-isolation.md) |
| Agent Definition 如何发现、Shadow 与公告；`task` 如何验证 Type、Depth、Model、Capability 和 Isolation；Coordinator 怎样维护 Child 状态；Fresh、Fork、Resume、Background、Cancel、Usage 与 Worktree 又如何形成完整嵌套 Session？ | [源码精读 14：Subagent 如何发现、启动、继承、隔离、回传与收敛](06-source-deep-dives/14-agent-definitions-discovery-task-tool-subagent-coordinator-child-session-context-inheritance-capability-isolation-background-resume-cancellation-and-usage-folding.md) |
| 模型看见 Tool 为什么不等于能执行；AccessKind、Rule、Bash/Shell Gate、Auto Classifier、用户 Grant 与 Managed Ceiling 怎样决定本次授权；Permission Allow 后 OS Sandbox 又如何限制真正的文件和网络能力？ | [源码精读 15：Agent Permission 如何分类访问、合并策略、审批并由 Sandbox 执行兜底](06-source-deep-dives/15-agent-permission-access-classification-policy-rules-shell-bypass-defense-auto-mode-user-approval-managed-ceilings-sandbox-execution-and-audit.md) |
| Permission Allow 后 Tool Call 怎样完成 Parse、Hook、Batch 与 Dispatch；Typed Runtime 如何路由文件、终端、MCP 和 Web；进程树、后台任务、Timeout、Streaming、截断、Artifact、图片与 Tool Result 又怎样收敛？ | [源码精读 16：Tool Execution Runtime 如何执行、流式更新、管理进程并回填结果](06-source-deep-dives/16-tool-execution-runtime-prepare-batch-dispatch-typed-context-streaming-terminal-process-lifecycle-truncation-artifacts-errors-and-result-feedback.md) |
| Hook 如何从配置、文件、插件、Agent 和客户端进入 Registry；Event、Matcher、Envelope、Command/HTTP Runner、Tool Gate 与 Stop Gate 又怎样通过 Fail-open、进程回收、SSRF 防护和反馈回填接入 Agent Loop？ | [源码精读 17：Hook Runtime 如何发现、匹配、执行并回接 Agent Loop](06-source-deep-dives/17-hook-runtime-events-discovery-provenance-trust-matcher-envelope-command-http-client-gates-stop-continuation-fail-open-and-agent-loop-feedback.md) |
| 配置如何从 System/Managed/User/Requirements/MDM、Version Override、Campaign、Env、CLI 与 Remote Settings 收敛成 Typed Runtime；又怎样传播给 Prompt、Tool、Skill、MCP、Hook，并通过 Watcher、Typed Update、Agent Rebuild 与 Session Snapshot 控制生效边界？ | [源码精读 18：Agent Configuration Runtime 如何分层加载、约束并传播](06-source-deep-dives/18-agent-configuration-runtime-files-layers-authority-version-overrides-campaigns-requirements-env-remote-typed-resolution-hot-reload-and-session-snapshots.md) |
| 一次 Agent Turn 怎样同时进入 tracing Span、Typed Event、Session Metrics、Unified Log、events.jsonl、Internal OTLP、External OTEL 与 Trace Artifact；这些管道又如何关联、脱敏、限流、分类错误并在退出时收敛？ | [源码精读 19：Agent Observability Runtime 如何记录、关联、脱敏并导出一次 Turn](06-source-deep-dives/19-agent-observability-runtime-tracing-spans-events-metrics-telemetry-redaction-debug-logs-trace-upload-and-turn-correlation.md) |
| 一个 Session 为什么拆成 Summary、Chat History、Updates、Rewind Points 与多个状态快照；Persistence Actor 如何协调追加、原子替换和耐久屏障；load、resume、replay、rewind、remote restore 又怎样把磁盘事实恢复成可继续运行的 Agent？ | [源码精读 20：Agent Persistence Runtime 如何写盘、恢复、回放与回退一个 Session](06-source-deep-dives/20-agent-persistence-runtime-session-directory-jsonl-actor-snapshots-rewind-load-resume-and-replay.md) |
| SessionActor 为什么保持单线程控制面却把 Turn、Tool、Process、Subagent 与 Workflow 分层并发；Interjection、Send Now、Ctrl+C、Close/Delete 又如何按范围取消、回收资源并只提交一个可信终态？ | [源码精读 21：Agent Concurrency & Cancellation Runtime 如何启动、打断并收敛一次 Turn](06-source-deep-dives/21-agent-concurrency-cancellation-runtime-localset-turn-task-tool-batch-interjection-background-process-shutdown-and-convergence.md) |
| 一个 ACP 请求怎样找到正确的 SessionActor；Registry 为什么是带证据的 Presence 状态机；load、prompt、cancel、close 怎样串行；Gateway、Replay Gate、Subscriber、Driver 与 Pending Interaction 又怎样让多客户端共享同一 Session 而不重复执行？ | [源码精读 22：Agent Session Protocol Runtime 如何接入、路由、驻留、重连并服务多个客户端](06-source-deep-dives/22-agent-session-protocol-runtime-acp-entry-registry-handle-gateway-multi-client-routing-residency-leader-and-bridge.md) |
| Transport、HTTP、SSE、Sampler、Tool、Auth、Persistence 与 Actor failure 怎样分层；Retry、401 refresh、Doom resample、Compaction、Session Repair 和 degradation 分别在哪一层发生；最终错误又怎样收敛为唯一、可观察、可回放的 Turn 终态？ | [源码精读 23：Agent Failure & Recovery Runtime 如何分类、重试、降级并修复失败](06-source-deep-dives/23-agent-failure-recovery-runtime-error-taxonomy-retry-auth-doom-turn-convergence-persistence-actor-repair-and-degradation.md) |
| System Prompt、项目规则、Skill Catalog 与正文、Tool Schema、Tool Result、Interjection、Memory 和动态 Reminder 分别何时进入模型上下文；Compaction、Resume、模型切换、CWD Relocation 与 Subagent fork 后又如何去重、继承和重建？ | [源码精读 24：Agent Context & Instruction Runtime 如何加载、演进与重建上下文](06-source-deep-dives/24-agent-context-instruction-runtime-system-prompt-project-rules-skills-tool-results-interjections-compaction-resume-and-subagent-scope.md) |
| 模型输出正文、Reasoning、Tool Call 或停止信号后，Tool Choice、Parse、Hook、Permission、Structured Output、Stationarity、TodoGate、Goal 和 Stop Hook 如何分层仲裁“执行、纠错、继续还是结束”？ | [源码精读 25：Agent Decision Runtime 如何仲裁行动与终止](06-source-deep-dives/25-agent-decision-runtime-tool-choice-reasoning-tool-call-structured-output-stationarity-todo-gate-stop-hook-and-goal-loop.md) |
| Prompt Mode、Plan Mode、Todo、长期 Goal 与 Named Workflow 有何区别；Planner、Evaluator、Skeptic Panel、Strategist、Token Budget、Pause/Resume 又怎样把一个目标组织成可验证的多轮自治闭环？ | [源码精读 26：Agent Goal & Planning Runtime 如何规划、验证、续跑与恢复](06-source-deep-dives/26-agent-goal-planning-runtime-plan-mode-todo-goal-tracker-evaluator-verifier-strategist-budget-pause-resume-and-workflows.md) |
| 如何验证一个包含 LLM、Tool、Actor、持久化、并发和终端 UI 的 Agent 系统；Mock Inference、ACP Harness、Fixtures、Trace Replay、故障注入和 E2E 各能证明什么？ | [源码精读 27：Agent Testing & Eval Runtime 如何建立可复现的正确性证据](06-source-deep-dives/27-agent-testing-and-eval-runtime-test-pyramid-mock-inference-acp-harness-fixtures-trace-replay-fault-injection-concurrency-and-e2e.md) |
| 一条用户消息怎样进入 ChatState 和模型请求；模型历史、持久化日志与客户端回放为何是三份状态；Context 又怎样跨过 Token 裁剪、Compaction、Rewind、Resume 与两种 Fork？ | [源码精读 28：Agent Context Lifecycle 如何生长、压缩、回退、恢复与分叉](06-source-deep-dives/28-agent-context-lifecycle-user-message-chat-state-conversation-request-token-window-compaction-rewind-resume-replay-and-fork.md) |
| 同一份 ConversationRequest 如何转换为 Chat Completions、Responses 与 Messages；三套 SSE 又怎样归并 Text、Reasoning、Tool Call、Usage、Retry 和唯一可信终态？ | [源码精读 29：Agent Sampling Protocol Runtime 如何统一三种模型协议与流式终态](06-source-deep-dives/29-agent-sampling-protocol-runtime-conversation-request-provider-conversion-http-sse-stream-assembly-reasoning-tool-calls-usage-retry-and-terminal-semantics.md) |
| 模型流出的 Tool Call 怎样从 delta 变成权威调用；JSON、别名、Hook、Permission 和 Plan Gate 怎样准备调用；并行执行的成功、失败、拒绝与取消又怎样都生成配对 Tool Result 并安全进入下一次 Sampling？ | [源码精读 30：Agent Tool-Call Protocol State Machine 如何保持调用与结果闭合](06-source-deep-dives/30-agent-tool-call-protocol-state-machine-stream-finalization-parse-normalization-alias-gates-permission-parallel-dispatch-tool-result-pairing-repair-and-resampling.md) |
| Rust Tool 参数怎样生成模型可见 JSON Schema；配置、版本、工具名与参数名映射怎样在 Finalize 时改写合同；三种 Provider 和 Runtime Parser 又怎样避免“模型会调用但代码解析不了”？ | [源码精读 31：Agent Tool Schema Contract 如何保持广告与执行一致](06-source-deep-dives/31-agent-tool-schema-contract-rust-types-schemars-finalization-name-parameter-remapping-provider-wire-validation-versioning-and-drift.md) |
| Tool 已在代码和 Registry 中存在，为什么当前 Agent 或本轮模型仍可能看不到；Tool Pack、Preset、环境能力、allow/deny、Capability、Requirements、MCP search/use 和 Turn Snapshot 怎样逐层决定最终工具集合？ | [源码精读 32：Agent Tool Registry Selection 如何从候选全集收敛到本轮公告](06-source-deep-dives/32-agent-tool-registry-selection-packs-presets-agent-config-capability-requirements-mcp-progressive-discovery-turn-snapshot-and-cache-stability.md) |
| MCP Server 的工具怎样从 handshake 进入 Metadata Snapshot 和 BM25 索引；`search_tool` 如何返回少量相关 Schema；`use_tool` 又怎样校验目标并分派到动态 Registry，同时保持连接更新、禁用和 Bridge 重建的一致性？ | [源码精读 33：MCP Tool Discovery Index 如何搜索并执行动态工具](06-source-deep-dives/33-agent-mcp-tool-discovery-index-server-handshake-metadata-snapshot-bm25-ranking-search-schema-use-tool-dispatch-live-updates-and-rebuild-consistency.md) |
| MCP Client 怎样统一 stdio、HTTP/OAuth 和 ACP transport；`ensure_initialized` 如何保证并发单飞与取消安全；Tool Call、ListChanged、Liveness、Stdio Restart 和 HTTP Recovery 又怎样分层收敛？ | [源码精读 34：MCP Client Runtime 如何连接、调用、监测并恢复](06-source-deep-dives/34-agent-mcp-client-runtime-transport-initialize-single-flight-oauth-tool-call-retry-liveness-list-changed-dispatch-auto-restart-and-recovery.md) |
| 动态 MCP Tool 为什么不能只按 `use_tool` 审批；真实 target 与参数怎样进入 AccessKind、Policy、Auto Classifier、Hook 和用户 Grant；Folder Trust、Server Admission、不可信 description/result、Prompt Injection 与 app-direct 调用又有哪些明确边界？ | [源码精读 35：Dynamic Tool Safety 如何约束发现后的外部动作](06-source-deep-dives/35-agent-dynamic-tool-safety-mcp-permission-access-kind-policy-rules-auto-classifier-user-grants-hooks-folder-trust-untrusted-output-prompt-injection-audit-and-boundaries.md) |

## 阅读约定

文档使用以下标记区分信息性质：

- **源码事实**：可以由给出的文件、类型、函数或测试直接验证。
- **阅读模型**：为了理解而做的分层或简化，不代表源码中存在同名抽象。
- **设计推断**：根据依赖和行为推断出的动机，会明确标注。
- **学习建议**：阅读顺序和优先级，不是产品约束。

路径默认相对于仓库根目录。首次出现关键符号时，同时给出 crate、文件路径和符号名。核心文档不依赖易漂移的行号，而依赖更稳定的类型和函数名；精确 walkthrough 才使用行号，并记录校验版本。

## 本篇术语表

| 名词 | 白话解释 | 在本套文档中的含义 |
| --- | --- | --- |
| workspace | 一个同时管理多个 Rust 包的工程 | 根 `Cargo.toml` 统一管理 Grok Build 的成员、依赖版本和构建设置 |
| crate | Rust 的包/编译单元 | 通常对应一个含 `Cargo.toml` 的目录，例如 `xai-grok-agent` |
| runtime | 让异步任务真正运行的执行环境 | 多数情况下指 Tokio runtime；有时也泛指 agent 的运行时逻辑，正文会注明 |
| 端到端链路 | 从外部输入一直追到最终结果的完整路径 | 例如从用户 prompt 追到模型回答和持久化，而不是只看某个函数 |
| 源码证据 | 能直接支持文档结论的代码、配置或测试 | 文件路径之外，通常还会给出类型、函数或测试名 |
| walkthrough | 选一个具体场景逐步跟踪代码 | 比一般架构说明更精确，可能记录行号和中间数据 |
| 基线 | 文档核对时采用的源码版本 | 本套文档用 `SOURCE_REV` 记录的提交作为基线 |

更多通用名词见 [全局术语表](appendices/glossary.md)。每篇文档还会包含只针对本篇的术语表，解释名词在当前上下文里的具体意思。

## 如何验证文档

每篇核心文章至少提供一种验证方式：

```sh
# 快速查符号
rg "symbol_name" crates/codegen

# 只检查目标 crate；不要把全 workspace 作为日常默认动作
cargo check -p <crate-name>

# 运行目标 crate 测试
cargo test -p <crate-name>
```

阅读时，先尝试凭文档画出链路，再打开源码核对；最后用测试、日志或最小运行实验验证。只读完文章不算完成学习。

## 维护规则

1. 修改核心调用链时，同步检查 `02-runtime-flows`。
2. 移动公开类型或模块时，同步检查 `01-architecture` 和 `04-crate-reference`。
3. 修改事件、协议或持久化格式时，同步检查所有相关时序图和 walkthrough。
4. 文档结论与源码冲突时，以源码和测试为准，并在修订记录中写明原因。
5. 新文章必须遵循 [写作与证据规范](appendices/authoring-standard.md)，并包含“本篇术语表”。
