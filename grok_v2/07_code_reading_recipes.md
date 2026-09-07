# 07. 读代码实战路线

这一章给你一些“想追某个功能时怎么搜”的模板。

## 通用搜索命令

项目很大，优先用 `rg`：

```sh
rg -n "关键词" crates
rg -n "enum Command|struct PagerArgs" crates/codegen/xai-grok-pager/src/app
rg -n "async fn prompt|handle_prompt|execute_tool_calls" crates/codegen/xai-grok-shell/src
rg -n "ToolBridge|ToolDefinition|ToolInput|ToolOutput" crates/codegen/xai-grok-tools/src
```

列文件用：

```sh
rg --files crates/codegen/xai-grok-pager/src/app
find crates/codegen/xai-grok-shell/src/session/acp_session_impl -maxdepth 1 -type f
```

## 路线 1：追 `grok --version`

目标：理解简单 CLI subcommand 怎么走。

搜索：

```sh
rg -n "Command::Version|Version \\{" crates/codegen/xai-grok-pager-bin/src/main.rs crates/codegen/xai-grok-pager/src/app/cli.rs
```

阅读顺序：

1. `app/cli.rs` 看 `Command::Version` 的定义。
2. `pager-bin/src/main.rs` 看 `async_main()` 的 `match command`。
3. 找到 `Command::Version { json }` 分支。
4. 看普通输出和 JSON 输出分别怎么构造。

这是最简单的入口练习。

## 路线 2：追 TUI 创建 session

搜索：

```sh
rg -n "CreateSession|SessionCreated|NewSessionRequest" crates/codegen/xai-grok-pager/src/app
```

阅读顺序：

1. `actions.rs` 找 `Action::NewSession`、`Effect::CreateSession`、`TaskResult::SessionCreated`。
2. `dispatch/` 里找哪个 action 产生 `Effect::CreateSession`。
3. `effects/mod.rs` 里找 `Effect::CreateSession`，看它如何发送 `acp::NewSessionRequest`。
4. `acp_handler/` 或 `dispatch/task_result.rs` 看创建成功后 UI 怎么更新。
5. 后端继续跳到 `MvpAgent::new_session`。

## 路线 3：追一条 prompt

搜索：

```sh
rg -n "SendPrompt|PromptRequest|async fn prompt|SessionCommand::Prompt|handle_prompt" crates/codegen
```

阅读顺序：

1. UI：`Action::SendPrompt`。
2. UI：dispatch 产生发送 prompt 的 effect。
3. UI：effects 发送 `acp::PromptRequest`。
4. 后端：`MvpAgent::prompt` 接收请求。
5. 后端：`SessionCommand::Prompt` 进入 session actor。
6. 后端：`handle_prompt` 解析并开启 turn。
7. 后端：`process_conversation_turn` 调模型。
8. 后端：`execute_tool_calls` 执行工具。

你只要能把这条链路串起来，就已经理解项目最核心的一半了。

## 路线 4：追工具调用

搜索：

```sh
rg -n "execute_tool_calls|prepare_tool_call|ToolBridge::call|struct ToolBridge" crates/codegen
```

阅读顺序：

1. `xai-grok-shell/src/session/acp_session_impl/tool_calls.rs`
2. `xai-grok-tools/src/bridge.rs`
3. `xai-grok-tools/src/registry/types.rs`
4. `xai-grok-tools/src/implementations/mod.rs`
5. 具体工具模块，例如 `implementations/grok_build/read_file`。

注意看两个结果：

- 给 UI/系统用的结构化 `ToolOutput`。
- 给模型继续推理用的 `prompt_text`。

## 路线 5：追权限弹窗

搜索：

```sh
rg -n "PermissionHandle|PermissionCommand|PromptOutcome|request_permission|AccessKind" crates/codegen/xai-grok-workspace/src/permission crates/codegen/xai-grok-shell/src/session
```

阅读顺序：

1. `xai-grok-workspace/src/permission/types.rs` 看权限数据类型。
2. `permission/manager.rs` 看 permission actor。
3. `permission/prompter.rs` 看如何向用户请求确认。
4. `session/acp_session_impl/tool_calls.rs` 看工具执行前如何请求权限。
5. `xai-grok-pager/src/app/acp_handler/permissions.rs` 看 UI 如何显示 permission 事件。

## 路线 6：追 slash command

搜索：

```sh
rg -n "slash_commands|BuiltinAction|SlashCommandOutcome|execute_builtin_slash_command" crates/codegen/xai-grok-shell/src/session
```

阅读顺序：

1. `session/slash_commands.rs`
2. `session/acp_session_impl/turn.rs` 中 `slash_commands::resolve(...)`
3. `session/acp_session_impl/slash_exec.rs`
4. 如果 slash 是 UI 侧命令，也看 `xai-grok-pager/src/slash` 和 `app/dispatch`。

## 路线 7：追 leader 模式

搜索：

```sh
rg -n "run_leader|connect_or_spawn|LeaderClient|run_leader_server|ControlCommand" crates/codegen/xai-grok-shell/src crates/codegen/xai-grok-pager-bin/src/main.rs
```

阅读顺序：

1. `pager-bin/src/main.rs` 看什么时候选择 leader。
2. `xai-grok-pager/src/acp` 看 UI 如何连接 leader。
3. `xai-grok-shell/src/agent/app.rs` 看 `run_leader`。
4. `xai-grok-shell/src/leader/` 看 IPC protocol、client、server、lock。

leader 代码并发多，建议先看注释和 phase 编号。

## 路线 8：追 workspace RPC

搜索：

```sh
rg -n "trait WorkspaceOp|WorkspaceRpc|GitStatusExtReq|execute\\(" crates/codegen/xai-grok-workspace/src crates/codegen/xai-grok-workspace-types/src
```

阅读顺序：

1. `xai-grok-workspace/src/workspace_ops.rs`
2. `xai-grok-workspace-types/src/rpc/`
3. 对应 local `WorkspaceOp` 实现。
4. 如果是 proxy，继续看 workspace client/server。

## 路线 9：追文件读取

搜索：

```sh
rg -n "AsyncFileSystem|read_file|ReadFileTool|FsReadFileReq" crates/codegen/xai-grok-workspace/src crates/codegen/xai-grok-tools/src crates/codegen/xai-grok-shell/src
```

阅读顺序：

1. `xai-grok-workspace/src/file_system/fs.rs`
2. `xai-grok-workspace/src/file_system/local_fs.rs`
3. `xai-grok-workspace/src/file_system/acp_fs.rs`
4. `xai-grok-tools/src/implementations/grok_build/read_file`
5. `tool_calls.rs` 看调用前后处理。

## 路线 10：追测试

搜索某个功能对应测试：

```sh
rg -n "SendPrompt|SessionCommand::Prompt|ToolBridge|permission|worktree" crates -g '*test*.rs'
rg -n "#\\[test\\]|#\\[tokio::test\\]" crates/codegen/xai-grok-pager/tests crates/codegen/xai-grok-shell/src
```

测试分布很广：

- `crates/codegen/xai-grok-pager/tests/`：TUI PTY e2e、scenario 测试。
- `crates/codegen/xai-grok-shell/src/**/tests.rs`：session/runtime 单元测试。
- `crates/codegen/xai-grok-tools/tests/`：工具测试。
- `crates/codegen/xai-grok-workspace/src/**/tests.rs`：workspace 测试。

## 读大文件的技巧

这个项目有不少大文件，不要从第 1 行读到最后。建议：

1. 先看文件顶部注释。
2. 用 `rg -n "struct Name|enum Name|impl Name|async fn foo"` 找结构。
3. 找调用点，不先读实现。
4. 找测试，测试通常比实现更能解释意图。
5. 看注释里的 invariant、phase、safety contract，这些往往是关键设计。

例如读 `MvpAgent`：

```sh
rg -n "pub struct MvpAgent|impl acp::Agent|async fn initialize|async fn new_session|async fn load_session|async fn prompt" crates/codegen/xai-grok-shell/src/agent/mvp_agent
```

例如读 `SessionActor`：

```sh
rg -n "pub\\(crate\\) struct SessionActor|run_session|handle_prompt|process_conversation_turn|execute_tool_calls" crates/codegen/xai-grok-shell/src/session
```

## 最后的小建议

读这种大型项目时，不要追求一次看懂所有抽象。每次只追一条链路：

- 一个 CLI 命令。
- 一个 UI action。
- 一个 ACP request。
- 一个 session command。
- 一个工具调用。
- 一个 workspace RPC。

每追完一条，项目地图就会亮一块。几条链路之后，你会发现很多模块名字已经变得熟悉了。
