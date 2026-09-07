# 06. 给 Rust 新手的学习路线

这个仓库不适合从第一天就逐行读。它是一个大型 Rust workspace，里面有 TUI、异步 actor、RPC、工具注册、权限、文件系统、模型调用。最好的方式是分阶段建立理解。

## 第 0 阶段：先知道 Rust 项目怎么看

你需要先掌握这些基本概念：

| 概念 | 在本项目里怎么出现 |
| --- | --- |
| crate | `crates/codegen/xai-grok-pager` 这种就是一个 crate。 |
| workspace | 根 `Cargo.toml` 把很多 crate 组合在一起。 |
| module | `lib.rs`、`mod.rs`、`pub mod xxx`。 |
| trait | 文件系统、工具、workspace op 都用 trait 定义能力。 |
| enum | CLI command、UI action、session command 都大量用 enum。 |
| async/await | agent、TUI event loop、工具执行都基于 Tokio。 |
| channel | `mpsc` 和 `oneshot` 用来让 actor 和任务通信。 |
| Arc | 多处共享同一个对象。 |
| Mutex | 异步或多线程共享可变状态。 |
| Rc/RefCell | `LocalSet` 单线程里共享可变状态。 |
| serde | JSON/YAML/TOML 解析。 |
| clap | CLI 参数解析。 |

不需要先成为 Rust 专家，但要能看懂函数签名和 enum。

## 第 1 阶段：先跑起来

先确认你能让项目过基本检查：

```sh
cargo check -p xai-grok-pager-bin
cargo run -p xai-grok-pager-bin -- --version
cargo run -p xai-grok-pager-bin -- --help
```

如果编译很慢，不要一上来跑 full workspace：

```sh
cargo check --workspace
```

README 也提醒了：开发时尽量针对具体 crate。

## 第 2 阶段：读入口，不深入

读：

```text
crates/codegen/xai-grok-pager-bin/src/main.rs
crates/codegen/xai-grok-pager/src/app/cli.rs
```

目标：

- 知道 `main()` 和 `async_main()` 分别做什么。
- 知道 `Command` enum 有哪些 subcommand。
- 知道默认 TUI 最后会调用 `xai_grok_pager::app::run(...)`。
- 知道 agent 子命令最后会进入 `xai_grok_shell::agent::app`。

不要在这个阶段深挖 allocator、crash handler、telemetry。先把启动分流看懂。

## 第 3 阶段：读 TUI 架构

读：

```text
crates/codegen/xai-grok-pager/src/app/actions.rs
crates/codegen/xai-grok-pager/src/app/mod.rs
crates/codegen/xai-grok-pager/src/app/event_loop.rs
crates/codegen/xai-grok-pager/src/app/dispatch/mod.rs
crates/codegen/xai-grok-pager/src/app/effects/mod.rs
```

目标：

- 理解 `Action -> dispatch -> Effect -> TaskResult -> dispatch`。
- 理解 `event_loop` 只是编排，不做太多业务。
- 理解 `AppView` 是根状态，`AgentView` 是单会话状态。

练习：

```sh
rg -n "SendPrompt|CreateSession|LoadSession" crates/codegen/xai-grok-pager/src/app
```

试着追一个 action 的完整路径。

## 第 4 阶段：读 agent runtime

读：

```text
crates/codegen/xai-grok-shell/src/agent/app.rs
crates/codegen/xai-grok-shell/src/agent/mvp_agent/acp_agent.rs
crates/codegen/xai-grok-shell/src/agent/mvp_agent/mod.rs
```

目标：

- 知道 `run_stdio_agent`、`run_headless`、`run_leader` 的共同点。
- 理解 `MvpAgent` 是 ACP 请求入口。
- 理解 `initialize`、`new_session`、`load_session`、`prompt`。

练习：

```sh
rg -n "async fn initialize|async fn new_session|async fn load_session|async fn prompt" crates/codegen/xai-grok-shell/src/agent/mvp_agent
```

## 第 5 阶段：读 session actor

读：

```text
crates/codegen/xai-grok-shell/src/session/handle.rs
crates/codegen/xai-grok-shell/src/session/commands.rs
crates/codegen/xai-grok-shell/src/session/acp_session.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/run_loop.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs
```

目标：

- 理解 `SessionHandle` 是遥控器。
- 理解 `SessionCommand` 是 actor 消息协议。
- 理解一轮 prompt 在 `handle_prompt` 中如何开始。

练习：

```sh
rg -n "SessionCommand::Prompt|handle_prompt|PromptTurnResult" crates/codegen/xai-grok-shell/src/session
```

## 第 6 阶段：读工具和权限

读：

```text
crates/codegen/xai-grok-agent/src/builder.rs
crates/codegen/xai-grok-tools/src/bridge.rs
crates/codegen/xai-grok-tools/src/implementations/mod.rs
crates/codegen/xai-grok-shell/src/session/acp_session_impl/tool_calls.rs
crates/codegen/xai-grok-workspace/src/permission/manager.rs
```

目标：

- 理解 `AgentBuilder` 如何组装工具。
- 理解 `ToolBridge` 是工具门面。
- 理解模型 tool call 执行前要经过权限、plan mode、hooks、MCP 检查。

练习：

```sh
rg -n "execute_tool_calls|prepare_tool_call|plan_mode_edit_gate|ToolBridge::call" crates/codegen
```

## 第 7 阶段：读 workspace

读：

```text
crates/codegen/xai-grok-workspace/src/lib.rs
crates/codegen/xai-grok-workspace/src/file_system/fs.rs
crates/codegen/xai-grok-workspace/src/workspace_ops.rs
crates/codegen/xai-grok-workspace/src/session/
crates/codegen/xai-grok-workspace/src/worktree/
```

目标：

- 理解为什么文件系统要抽象成 `AsyncFileSystem`。
- 理解 `WorkspaceOps` 的 local/proxy 两种模式。
- 理解 git/hunk/worktree/rewind 都属于 workspace 关注点。

## 第 8 阶段：开始做小改动

适合新手的小任务：

1. 给某个 CLI subcommand 补一条更清楚的 help 文案。
2. 给某个小函数补单元测试。
3. 在 `dispatch` 里追一个 action，然后写注释说明。
4. 给 docs 增加“如何使用某个命令”的说明。
5. 找一个工具实现，给错误信息增加上下文。

不要一开始就改：

- `run_leader` 的并发流程。
- 权限判定核心逻辑。
- tool call 执行循环。
- session persistence 格式。
- workspace RPC wire 类型。

这些地方影响面大，适合熟悉之后再碰。

## 看不懂时怎么办

优先问这 5 个问题：

1. 这个文件属于哪个 crate？
2. 它是入口、状态、trait、实现，还是测试？
3. 这个函数是同步改状态，还是异步做 I/O？
4. 数据是通过返回值传递，还是通过 channel 传递？
5. 这段代码是在 UI 进程、agent runtime、session actor，还是 tool/workspace 层？

能回答这 5 个问题，通常就不会迷路。
