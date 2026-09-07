# 08. 术语表

这份术语表把项目里反复出现的词翻译成适合新手理解的说法。

## 项目术语

| 术语 | 白话解释 |
| --- | --- |
| Grok Build | 这个项目实现的终端 AI 编程助手。 |
| `grok` | 用户实际运行的命令名。源码里的二进制 artifact 是 `xai-grok-pager`。 |
| pager | 本项目里通常指终端 TUI 层，不是传统意义上的 `less` 那种 pager。 |
| TUI | Terminal User Interface，终端里的图形化交互界面。 |
| headless | 没有交互式 UI 的运行模式，适合脚本、CI、自动化。 |
| stdio agent | 通过 stdin/stdout 收发 ACP JSON-RPC 的 agent 模式。IDE 或桌面端常用。 |
| leader | 一个共享的后端 agent 进程，多个 client 可以连接它。 |
| relay | agent/leader 和 grok.com 之间的 WebSocket 通道。 |
| workspace | 当前项目工作区，包括文件、git、权限、worktree 等状态。 |
| hub | Computer Hub / workspace server 相关远程连接层。 |
| session | 一段可恢复的对话。它有 id、cwd、历史消息、工具状态、文件状态等。 |
| turn | 一轮用户 prompt 到 assistant 完成回复的过程。一个 turn 里可能多次调用工具。 |
| prompt | 用户输入给模型的一段内容，也可能包含图片、上下文、skill 信息。 |
| prompt block | ACP 里 prompt 的结构化内容块，例如 text block、image block。 |
| synthetic prompt | 不是用户直接手打的 prompt，而是系统自动注入的 prompt，比如任务完成后的 auto-wake。 |
| scrollback | TUI 中显示历史消息和工具输出的滚动区域。 |
| dashboard | agent/session 总览视图。 |
| plan mode | 规划模式。通常限制编辑范围，让模型先制定计划再执行。 |
| yolo / always-approve | 尽量自动批准工具执行的模式，但仍可能被策略或 plan mode 拦住。 |
| auto mode | 自动判定工具是否安全的模式，可能结合规则和 classifier。 |
| ask mode | 风险操作需要问用户确认的模式。 |
| MCP | Model Context Protocol。外部 server 可以通过它提供工具。 |
| skill | 可发现的能力说明，通常来自 `SKILL.md`，会影响模型如何解决任务。 |
| plugin | 本地或远程能力包，可提供 skills、MCP server、hooks、apps 等。 |
| AGENTS.md | 项目级规则/说明文件，注入到 agent 上下文中。 |
| hunk | git diff 里的一块修改。项目会追踪 agent 改过哪些 hunk。 |
| worktree | git worktree，用来给 session/fork 创建隔离工作目录。 |
| rewind | 回滚到之前某个 prompt/turn 对应的文件状态。 |
| compaction | 当上下文太长时，把历史压缩成更短摘要，继续对话。 |
| telemetry | 埋点和诊断信息。 |
| trace | 一轮或一个 session 的调试/分析轨迹。 |

## ACP 相关

| 术语 | 白话解释 |
| --- | --- |
| ACP | Agent Client Protocol，client 和 agent 之间的 JSON-RPC 协议。 |
| `initialize` | client 连接 agent 后的初始化请求。 |
| `new_session` | 创建新 session 的 ACP 请求。 |
| `load_session` | 恢复已有 session 的 ACP 请求。 |
| `prompt` | 发送用户 prompt 的 ACP 请求。 |
| notification | agent 主动推给 client 的事件，例如文本增量、工具状态、turn 完成。 |
| gateway | 后端发 notification 给 client 的通道封装。 |
| ext method | ACP 扩展方法，项目里很多 `x.ai/...` 能力走扩展方法。 |
| meta / `_meta` | 请求或通知里的附加信息，例如 client id、screen mode、prompt id。 |

## 代码结构术语

| 术语 | 白话解释 |
| --- | --- |
| crate | Rust 的编译单元。这个项目由很多 crate 组成。 |
| workspace | 多个 crate 的集合，由根 `Cargo.toml` 管理。 |
| module | Rust 模块，通常由 `mod xxx;` 声明。 |
| `lib.rs` | crate 的库入口。 |
| `main.rs` | 二进制程序入口。 |
| `mod.rs` | 目录模块入口。 |
| feature | Cargo feature，用来打开/关闭某些编译能力。 |
| profile | Cargo 编译 profile，例如 dev、release、release-dist。 |

## Rust 语法和异步术语

| 术语 | 白话解释 |
| --- | --- |
| trait | 一组能力接口。类似“只要你实现这些方法，就能被当成这种东西用”。 |
| impl | 给类型实现方法或实现 trait。 |
| enum | 枚举。这个项目大量用 enum 表达状态和消息类型。 |
| struct | 结构体，用来组合字段。 |
| `Result<T, E>` | 成功是 `Ok(T)`，失败是 `Err(E)`。 |
| `Option<T>` | 有值是 `Some(T)`，没有值是 `None`。 |
| `async fn` | 异步函数，返回 future，需要 `.await`。 |
| `.await` | 等一个异步操作完成。 |
| Tokio | Rust 常用异步 runtime，本项目大量使用。 |
| `mpsc` | 多生产者单消费者 channel，适合 actor 收消息。 |
| `oneshot` | 只返回一次结果的 channel，适合请求-响应。 |
| `Arc<T>` | 多线程共享所有权的智能指针。 |
| `Rc<T>` | 单线程共享所有权的智能指针。 |
| `Mutex<T>` | 给共享可变状态加锁。 |
| `RefCell<T>` | 单线程运行时借用检查，常和 `Rc` 一起用。 |
| `LocalSet` | Tokio 的单线程任务集合，可以跑不是 `Send` 的 future。 |
| `Send` | 类型可以跨线程移动。 |
| `Sync` | 类型可以被多个线程安全共享引用。 |
| `serde` | Rust 序列化/反序列化库，用来处理 JSON/YAML/TOML。 |
| `clap` | Rust 命令行参数解析库。 |

## 本项目最重要的几个类型

| 类型 | 文件 | 作用 |
| --- | --- | --- |
| `PagerArgs` / `Command` | `xai-grok-pager/src/app/cli.rs` | CLI 参数和 subcommand。 |
| `Action` / `Effect` / `TaskResult` | `xai-grok-pager/src/app/actions.rs` | TUI 状态流的核心 enum。 |
| `AppView` | `xai-grok-pager/src/app/app_view.rs` | TUI 根状态。 |
| `MvpAgent` | `xai-grok-shell/src/agent/mvp_agent/mod.rs` | ACP Agent 实现和 session 管理器。 |
| `SessionHandle` | `xai-grok-shell/src/session/handle.rs` | 外部操作 session actor 的句柄。 |
| `SessionCommand` | `xai-grok-shell/src/session/commands.rs` | 发给 session actor 的消息协议。 |
| `SessionActor` | `xai-grok-shell/src/session/acp_session.rs` | 一段 session 的真实运行状态。 |
| `AgentDefinition` | `xai-grok-agent/src/config.rs` | agent 配置定义。 |
| `AgentBuilder` | `xai-grok-agent/src/builder.rs` | 把 definition 构造成可运行 `Agent`。 |
| `Agent` | `xai-grok-agent/src/agent.rs` | 已构建 agent，包含 system prompt 和 ToolBridge。 |
| `ToolBridge` | `xai-grok-tools/src/bridge.rs` | 工具系统门面。 |
| `AsyncFileSystem` | `xai-grok-workspace/src/file_system/fs.rs` | 文件系统抽象。 |
| `WorkspaceOps` | `xai-grok-workspace/src/workspace_ops.rs` | workspace local/proxy 操作入口。 |
| `PermissionHandle` | `xai-grok-workspace/src/permission/manager.rs` | 权限系统句柄。 |

## 一句话区分几个容易混的词

- `AgentDefinition` 是配置，`AgentBuilder` 是建造过程，`Agent` 是建造结果。
- `MvpAgent` 管很多 session，`SessionActor` 只管一个 session。
- `SessionHandle` 是遥控器，`SessionCommand` 是遥控器发出的指令。
- `ToolBridge` 管工具集合，具体工具实现在 `xai-grok-tools/src/implementations/`。
- `pager` 是 UI，`shell` 是 agent runtime，`workspace` 是文件/git/权限底座。
