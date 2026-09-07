# 15. 按执行顺序阅读源码：从启动到最终回答

## 1. 本篇解决什么问题

前面的章节按照架构主题拆分源码。本篇换一种方式：完全按照程序运行的时间顺序，回答两个问题：

1. 用户在终端运行 `codex` 后，程序怎样一步一步建立可工作的 Agent？
2. 用户提交一个问题后，这条输入怎样经过 Session、Prompt、模型和 Tool，最后变成回答？

本文以交互式 TUI 的主要路径为主。当前版本的 TUI 通常通过 App Server 协议创建和控制 Thread，因此会同时出现 TUI、App Server 和 Core 三层。`codex exec`、IDE 等入口的 UI 层不同，但进入 Core 后的 Session 和 Agent Loop 基本相同。

> 版本提醒：当前 Core 协议主要使用 `Op::UserInput`。TUI 内部仍有 `AppCommand::UserTurn` 这样的 UI 命令名。阅读不同版本或旧文档时，不要因为名字不同而误以为是两套 Agent Loop。

## 2. 总时序图

```mermaid
sequenceDiagram
    participant User as 用户
    participant CLI as codex CLI
    participant TUI as TUI App
    participant AS as App Server
    participant TM as ThreadManager
    participant S as Session
    participant Loop as run_turn
    participant Model as Responses API
    participant Tool as Tool Runtime

    User->>CLI: 运行 codex
    CLI->>TUI: run_interactive_tui / tui::run_main
    TUI->>TUI: 解析配置、环境、认证和启动目标
    TUI->>AS: thread/start
    AS->>TM: start_thread(StartThreadOptions)
    TM->>S: Session::spawn(...)
    S->>S: 建模型、History、服务和 channels
    S->>S: tokio::spawn(submission_loop)
    S-->>TM: Session + SessionIo
    TM-->>AS: NewThread
    AS-->>TUI: ThreadStartResponse

    User->>TUI: 输入问题并提交
    TUI->>AS: turn/start 或 turn/steer
    AS->>S: submit(Op::UserInput)
    S->>S: submission_loop dispatch
    S->>S: new TurnContext + RegularTask
    S->>Loop: run_turn(input)
    Loop->>Loop: StepContext + WorldState + Skills + Tools
    Loop->>Model: stream(Prompt)

    alt 模型返回 Tool Call
        Model-->>Loop: FunctionCall
        Loop->>Tool: dispatch
        Tool-->>Loop: Tool Result
        Loop->>S: append Result to History
        Loop->>Model: 下一次 Prompt
    else 模型返回最终消息
        Model-->>Loop: assistant message
        Loop->>S: 记录最终消息并结束 Turn
    end

    S-->>AS: Event stream
    AS-->>TUI: notifications
    TUI-->>User: 实时显示进度和答案
```

# 第一部分：程序启动

## 3. 第一步：进入 CLI `main()`

二进制入口位于 [`codex-rs/cli/src/main.rs`](../codex-rs/cli/src/main.rs)：

```rust
fn main() -> anyhow::Result<()> {
    let remote_control_disabled = codex_app_server::take_remote_control_disabled_env();
    arg0_dispatch_or_else(move |arg0_paths| async move {
        cli_main(arg0_paths, remote_control_disabled).await?;
        Ok(())
    })
}
```

这里先做 `arg0` 分发。Codex 安装包可能通过不同可执行文件名进入不同功能；如果没有被特殊入口接管，就执行 `cli_main()`。

`cli_main()` 使用 Clap 解析：

-全局配置覆盖；
-Feature enable/disable；
-远程模式；
-交互式参数；
-`exec`、`review`、`mcp-server`、`app-server` 等子命令。

没有子命令时进入交互式 TUI：

```text
main()
  → arg0_dispatch_or_else()
  → cli_main()
  → run_interactive_tui()
  → codex_tui::run_main()
```

此时还没有用户 Turn，也没有运行 Agent Loop。

## 4. 第二步：TUI 启动和 Bootstrap 配置

TUI 主入口位于 [`codex-rs/tui/src/lib.rs::run_main()`](../codex-rs/tui/src/lib.rs)。它首先处理启动参数：

1.解析 `--sandbox`、`--ask-for-approval` 等安全参数；
2.把旧的 `--search` 选项映射到统一配置；
3.解析 `-c key=value` 覆盖；
4.定位 Codex home；
5.处理配置 profile；
6.判断使用本地 App Server、已存在的 daemon，还是显式远程 App Server；
7.准备 Environment Manager；
8.确定本地或远程 workspace 的 cwd；
9.加载 bootstrap config；
10.构建 bootstrap HTTP client、认证和后续运行所需服务。

这里的“bootstrap config”用于让程序先有足够信息启动完整系统。配置来源可能包含：

-内置默认值；
-用户 `config.toml`；
-项目级配置；
-managed requirements；
-CLI 覆盖；
-profile。

配置加载顺序很重要。后层覆盖前层时仍需满足管理员要求，不能简单把所有 TOML 合并后不保留来源。

## 5. 第三步：选择 App Server 目标

当前 TUI 不一定在同一对象里直接调用 Core。它会创建 `AppServerSession`，目标可能是：

-当前进程管理的本地 App Server；
-可复用的本地 daemon；
-显式远程 App Server。

这个边界的意义是：

-TUI 与 Agent Runtime 通过公开协议解耦；
-本地和远程执行可以共享交互逻辑；
-Thread start、resume、fork、turn start 等行为都经过同一 API。

如果你只关心 Agent 实现，可以把 App Server 理解为“把 JSON-RPC 请求转换为 Core 调用、再把 Core Event 转成通知”的适配层。

## 6. 第四步：TUI 请求创建 Thread

TUI 调用 [`AppServerSession::start_thread_with_session_start_source()`](../codex-rs/tui/src/app_server_session.rs)，发送：

```text
ClientRequest::ThreadStart {
    params: thread_start_params_from_config(...)
}
```

参数包含当前模型、cwd、权限、sandbox、环境、personality 等线程初始设置。

App Server 的 handler 将它解析为 Core 的 `StartThreadOptions`，然后调用：

```text
ThreadManager::start_thread(options)
```

Thread Manager 是进程内多 Thread 的控制面。

## 7. 第五步：`ThreadManager::start_thread()`

入口位于 [`core/src/thread_manager.rs`](../codex-rs/core/src/thread_manager.rs)：

```text
start_thread()
  → start_thread_inner()
  → spawn_thread_with_source()
  → Session::spawn()
```

`start_thread_inner()` 先确定：

-默认或显式 Environment selections；
-当前 AgentControl；
-Session source 和 Thread source；
-初始 History；
-History mode；
-Dynamic Tools；
-是否允许 provider/model fallback。

新线程通常使用空的初始对话历史；resume 和 fork 则先构造 `InitialHistory`，最终仍汇入相同的 Session 创建路径。

## 8. 第六步：`Session::spawn_internal()` 初始化

Session 初始化位于 [`core/src/session/mod.rs`](../codex-rs/core/src/session/mod.rs)。这是启动阶段最值得细读的函数之一。

### 8.1 创建通信 Channels

```rust
let (tx_sub, rx_sub) = async_channel::bounded(SUBMISSION_CHANNEL_CAPACITY);
let (tx_event, rx_event) = async_channel::unbounded();
```

- submission channel：客户端向 Session 提交 `Op`；
- event channel：Session 向客户端发送 `Event`。

Submission channel 有界，防止调用方无限堆积操作；Event channel 允许 Runtime 持续输出流式事件。

### 8.2 加载用户指令与执行策略

Session 接收已经加载的 User Instructions，并收集 warning。随后构造 `ExecPolicyManager`：

-Guardian reviewer 使用受控默认策略；
-子 Agent 可以继承策略；
-普通线程从配置层加载用户和项目规则；
-必要时迁移旧的 prefix rule。

这一步建立真正执行 Shell 时会使用的规则，不只是 Prompt 文案。

### 8.3 发现并选择模型

Session 根据是否为 root Agent 选择模型目录刷新策略：

-root Agent 可以 `OnlineIfUncached`；
-非 root Agent 更偏向 Offline，避免每个子 Agent 重复刷新。

然后：

```text
models_manager.list_models(...)
  → get_default_model(...)
  → get_model_info(...)
```

`ModelInfo` 决定的不只有名称，还包括：

-context window；
-reasoning 参数；
-工具模式；
-并行 Tool Call；
-输入模态；
-基础指令模板；
-Responses Lite 等协议行为。

### 8.4 解析 Base Instructions

优先级是：

```text
config.base_instructions
  > 恢复 History 中的 session metadata
  > 当前 ModelInfo 的默认 instructions
```

解析后的结果进入 `SessionConfiguration.base_instructions`。

### 8.5 恢复或注册 Dynamic Tools

如果启动参数提供 Dynamic Tools，使用本次参数；否则尝试从恢复 History 中读取。这样 resume 后的线程可以继续理解以前声明的动态能力。

### 8.6 创建 `SessionConfiguration`

线程配置包含：

-provider 和 collaboration mode；
-reasoning、service tier、developer instructions；
-personality 和 base instructions；
-approval、permission profile、Windows sandbox level；
-environment selections；
-history mode 和 Session source；
-parent/fork metadata；
-dynamic tools。

### 8.7 创建真正的 Session

`Session::new()` 接收配置和共享服务，建立：

-Conversation History；
-Skill/Plugin/MCP 服务；
-模型客户端；
-input queue；
-Hook/Extension 数据；
-AgentControl；
-Thread store 与 rollout；
-telemetry；
-权限和环境状态。

### 8.8 启动 Submission Loop

Session 初始化结束后启动长期后台任务：

```rust
tokio::spawn(async move {
    submission_loop(session_for_loop, config, rx_sub).await;
});
```

它会一直等待操作，直到 `Op::Shutdown` 或 submission channel 关闭。

Session 返回 `SessionIo`：

```text
SessionIo
├── tx_sub：提交操作
├── rx_event：接收事件
├── agent_status：观察状态
└── session_loop_termination：等待 loop 退出
```

## 9. 第七步：Thread 注册并返回 UI

Thread Manager 将 Session 和 IO 包装成 `CodexThread`，注册进 live thread map，并把 `NewThread` 返回 App Server。

App Server 再把 Core Thread 信息转换为 `ThreadStartResponse`。TUI 收到后：

-创建或替换 ChatWidget；
-订阅该 Thread 的通知；
-渲染已有 History（如果是 resume）；
-允许用户开始输入。

到这里，Agent 已经“启动”，但尚未执行用户任务。它处于等待 Submission 的状态。

# 第二部分：用户提交问题

## 10. 第八步：TUI 生成 User Turn 命令

用户在 composer 输入内容并提交后，TUI 先生成内部：

```text
AppCommand::UserTurn {
    items,
    cwd,
    approval_policy,
    model,
    effort,
    collaboration_mode,
    personality,
    ...
}
```

这里的 `items` 已经是结构化用户输入，不一定只有字符串，也可以包含图片、Skill mention、App mention 等。

## 11. 第九步：决定 `turn/start` 还是 `turn/steer`

TUI 查询 Thread 是否有 active turn：

-没有 active turn：发送 `turn/start`；
-有可 steer 的 active turn：发送 `turn/steer`；
-active turn 正好结束造成竞态：刷新状态并退回 `turn/start`；
-active turn 不接受 steer：排队或给用户错误提示。

这段逻辑主要位于 [`tui/src/app/thread_routing.rs`](../codex-rs/tui/src/app/thread_routing.rs)。

`turn/start` 会携带本轮覆盖设置。App Server 将请求转换成 Core `Op::UserInput` 并通过 `CodexThread/SessionIo` 提交。

## 12. 第十步：`SessionIo::submit()`

`SessionIo::submit()` 为每次操作生成唯一 submission ID，构造：

```text
Submission {
    id,
    op: Op::UserInput { ... }
}
```

然后发送到有界 `tx_sub`。此时 App Server 请求处理与真正 Agent 执行解耦：长期工作由 Session task 完成，进度通过 Event channel 返回。

## 13. 第十一步：`submission_loop()` 分发

[`core/src/session/handlers.rs::submission_loop()`](../codex-rs/core/src/session/handlers.rs) 不断执行：

```rust
while let Ok(sub) = rx_sub.recv().await {
    match sub.op {
        Op::UserInput { .. } => user_input_or_turn(...).await,
        Op::Interrupt => interrupt(...).await,
        Op::ExecApproval { .. } => exec_approval(...).await,
        Op::Compact => compact(...).await,
        Op::Shutdown => shutdown(...).await,
        // ...
    }
}
```

同一个 channel 还承载：

-用户中断；
-Shell/Patch approval；
-request_user_input 回答；
-MCP refresh；
-rollback；
-realtime 操作；
-shutdown。

因此 Submission Loop 是 Session 的“控制命令总入口”，但它本身不执行模型循环。

## 14. 第十二步：`user_input_or_turn_inner()`

这个函数处理新用户输入的准入：

### 14.1 解构 `Op::UserInput`

读取：

-用户 items；
-最终输出 JSON schema；
-Responses client metadata；
-additional context；
-Thread settings overrides。

### 14.2 创建本轮 `TurnContext`

```text
sess.new_turn_with_sub_id(sub_id, updates)
```

它把线程当前配置和本轮覆盖解析成稳定 TurnContext。无效设置在这里产生错误，而不是运行到一半才失败。

### 14.3 先尝试 Steer

`sess.steer_input()` 检查是否已有可接收输入的 active turn：

-成功：输入进入该 Turn 的 queue，返回 `Steered`；
-没有 active turn：返回 `NoActiveTurn(items)`，开始新任务；
-其他错误：发出 Error event。

### 14.4 为新 Turn 构造 `TurnInput`

Additional context 先合并到 Session 的 context store，再转换成 `TurnInput::ResponseItem`。真正用户内容转换成：

```text
TurnInput::UserInput {
    content: items,
    client_id
}
```

### 14.5 创建 `RegularTask`

```rust
sess.spawn_task(
    Arc::clone(&current_context),
    task_input,
    RegularTask::new(),
).await;
```

至此，控制从 Submission handler 转到异步 Session Task。

## 15. 第十三步：`spawn_task()` 和 `start_task()`

代码位于 [`core/src/tasks/mod.rs`](../codex-rs/core/src/tasks/mod.rs)。主要顺序：

1.中止需要被替换的旧任务；
2.清理上一 Turn 的 connector selection；
3.记录 Turn 开始时间和初始 token usage；
4.创建 CancellationToken 和 completion Notify；
5.接收此前排队的 pending input；
6.发出 Turn start lifecycle；
7.在 `active_turn` 中安装 task 状态；
8.取得 multi-agent execution guard；
9.用 `tokio::spawn` 启动具体 `SessionTask::run()`；
10.任务结束时 flush rollout、发结束事件并清理 active state。

一个 Session 同时只维护受控的 active task。新任务不能不经协调地与旧任务争夺同一 History。

## 16. 第十四步：`RegularTask::run()`

[`core/src/tasks/regular.rs`](../codex-rs/core/src/tasks/regular.rs) 是普通用户 Turn 的任务包装器。

它首先：

-发送 `EventMsg::TurnStarted`；
-清理本轮 server reasoning 标记；
-消费启动阶段预热的 `ModelClientSession`。

预热结果有三种：

-Ready：把已经准备好的 ModelClientSession 交给 `run_turn()`；
-Unavailable：让 `run_turn()` 自己创建；
-Cancelled：记录输入和 Hook 后退出。

随后进入：

```text
RegularTask::run()
  → run_turn(...)
```

如果 `run_turn()` 返回时 input queue 又有新内容，RegularTask 会用空的 `next_input` 再运行一次，让队列内容通过统一路径被消费。

# 第三部分：Agent Loop 内部执行

## 17. 第十五步：`run_turn()` 初始化

主函数位于 [`core/src/session/turn.rs`](../codex-rs/core/src/session/turn.rs)。开始时：

1.复用预热的 ModelClientSession，或创建新的 Turn 级 session；
2.检查是否需要 pre-sampling compaction；
3.从用户输入提取纯 `UserInput`；
4.解析需要提前启动的 MCP servers 和 Plugin mentions；
5.捕获 `first_step_context`。

## 18. 第十六步：捕获 `StepContext`

`capture_step_context_with_required_mcp_servers()` 的时间顺序：

```text
刷新环境 readiness
  → 刷新 AGENTS.md
  → 解析 capability roots
  → 获取 Executor capability discovery
  → 准备 ExtensionData / sandbox contexts
  → 并行获取 MCP binding 与 Tool recommendations
  → built_tools()
  → 返回 StepContext
```

这一步固定了第一次模型请求会看到和能够执行的能力。

## 19. 第十七步：记录初始 World State

Session 使用第一 Step：

```text
build_world_state_for_step()
  → 与 History baseline 比较
  → 初次完整注入或生成 diff
  → 写入 Conversation History
  → 持久化 WorldState snapshot/patch
```

同时计算 Turn diff tracker 的 workspace display roots，用于后续向 UI 汇报本轮文件变化。

## 20. 第十八步：加载本轮 Skill 和 Plugin

`build_skills_and_plugins()`：

1.从输入收集显式 Skill mentions；
2.从当前 Skill snapshot 唯一解析 Skill；
3.读取 `SKILL.md` 正文；
4.处理 Plugin mention 和依赖；
5.可能提示安装 Skill 需要的 MCP dependency；
6.构造待注入 `ResponseItem`；
7.产生 warning 和 connector enablement。

然后 Session 按顺序记录：

-Session start Hook 结果；
-真实用户输入；
-Skill/Plugin injection items。

从此以后，它们都是结构化 History 的一部分。

## 21. 第十九步：进入外层循环

`run_turn()` 的 `loop` 每次代表一次可能的模型 sampling。循环顶部执行：

1.取 pending input；
2.运行输入 Hook 并记录；
3.记录 rollout/token reminder；
4.复用第一 Step 或重新捕获 Step；
5.记录当前时间提醒；
6.计算并追加 World State diff；
7.clone History 并调用 `for_prompt()`。

此时 `sampling_request_input` 就是模型本次要看到的结构化历史。

## 22. 第二十步：构造 `Prompt`

`run_sampling_request()` 读取：

-Session 的 Base Instructions；
-本 Step 的 ToolRouter；
-TurnContext 的模型和输出设置；
-准备好的 History input。

`build_prompt()` 最终创建：

```rust
Prompt {
    input,
    tools: router.model_visible_specs(),
    parallel_tool_calls: turn_context.model_info.supports_parallel_tool_calls,
    base_instructions,
    output_schema: turn_context.final_output_json_schema.clone(),
    output_schema_strict: ...,
}
```

这里再次体现：工具不是一段文字，而是单独的结构化 specs。

## 23. 第二十一步：转换成 Responses API 请求

`ModelClientSession::stream()` 调用 Core client：

```text
Prompt
  → build_responses_request()
  → ResponsesApiRequest
  → WebSocket 或 HTTP streaming
```

构造器会加入：

-model；
-instructions；
-input；
-tools；
-reasoning；
-parallel_tool_calls；
-service tier；
-prompt cache key；
-输出 schema；
-client metadata。

如果使用 Responses Lite，base instructions 和 tools 的 wire 位置会变化，但 Agent Loop 仍使用相同 Prompt 抽象。

## 24. 第二十二步：模型开始流式返回

`try_run_sampling_request()` 不断从 `ResponseStream` 读取 `ResponseEvent`：

```text
Created
OutputItemAdded
Text / Reasoning Delta
OutputItemDone
Completed / Usage
```

文本 delta 会立即变成 UI 事件，因此用户可以看到 commentary 或回答逐步出现。History 不会为每个 token 添加一条 Message；完整 item 在 `OutputItemDone` 后才进入正式处理。

## 25. 第二十三步 A：模型返回普通消息

如果 `OutputItemDone` 是 assistant message：

1.完成可能的文本 parser/plan parser；
2.生成或更新 UI TurnItem；
3.记录完整 ResponseItem；
4.更新 `last_agent_message`；
5.如果没有 Tool Call，不设置 tool follow-up。

当整个 stream 完成后，如果：

-没有 Tool Result 需要回传；
-没有 pending input；
-没有 Stop Hook 要求继续；

外层循环就会结束。

## 26. 第二十三步 B：模型返回 Tool Call

如果 item 是 Function/Custom/ToolSearch Call：

```text
ResponseItem
  → ToolRouter::build_tool_call()
  → ToolCall { tool_name, call_id, payload }
  → 当前 Step 的 Registry 查 Runtime
  → ToolCallRuntime 创建 Future
```

工具 Future 随后执行：

1.验证 payload；
2.运行 pre-tool Hook；
3.评估 approval；
4.构造 sandbox context；
5.执行 Handler；
6.限制和规范化输出；
7.运行 post-tool Hook；
8.转换成 `ResponseInputItem`。

## 27. 第二十四步：Tool Result 写回 History

主循环使用有序 future 集合接收结果。每个完成结果通过 Session：

```text
Tool Result
  → FunctionCallOutput / CustomToolCallOutput
  → ContextManager.record_items()
  → rollout append
  → raw response event
```

`needs_follow_up` 被设为真，因为模型还没有看见这个结果。

## 28. 第二十五步：再次调用模型

外层 `run_turn()` 检查：

```text
needs_follow_up = model_needs_follow_up OR has_pending_input
```

如果为真：

1.检查 token 状态；
2.必要时 mid-turn compact；
3.重新捕获 StepContext；
4.重新 clone 已包含 Tool Result 的 History；
5.构造下一次 Prompt；
6.再次调用模型。

模型这次会看到：

```text
之前的用户问题
+ 自己发出的 Tool Call
+ Runtime 返回的 Tool Result
```

于是可以根据真实结果决定下一步。

## 29. 第二十六步：请求重试

如果模型 stream 发生可重试错误：

1.`run_sampling_request()` 判断 `err.is_retryable()`；
2.按 provider 最大次数退避；
3.保留同一个 ModelClientSession；
4.从当前 History 重新构造 Prompt；
5.不无条件重复已成功记录的 Tool 副作用。

Context-window exceeded 和 usage-limit 等错误有专门分支，不会当作普通网络重试。

## 30. 第二十七步：Turn 收尾

当模型不再需要 follow-up：

1.运行 Turn stop hooks；
2.Hook 可允许结束、要求继续或停止；
3.运行兼容的 after-agent Hook；
4.`run_turn()` 返回最后 assistant message；
5.`RegularTask` 检查队列是否又有输入；
6.Session task flush rollout；
7.发出 Turn completed lifecycle；
8.清理 active task 和取消句柄；
9.Session 回到等待下一 Submission 的状态。

Thread 不会因为一次 Turn 结束而销毁。

# 第四部分：事件如何返回用户

## 31. Runtime Event 流

Agent 执行过程中会产生：

-`TurnStarted`；
-assistant message delta；
-reasoning delta；
-Tool/TurnItem started 和 completed；
-approval request；
-token usage；
-warning/error；
-Turn completed。

这些 Event 经过：

```text
Session.tx_event
  → CodexThread / App Server
  → ServerNotification
  → TUI App event
  → ChatWidget 状态
  → ratatui render
```

所以模型流、工具执行和 UI 绘制是解耦的。Runtime 只发送语义事件，不直接操作终端组件。

## 32. Approval 的暂停和恢复

如果 Tool 需要批准：

1.Handler 创建 approval request；
2.Session 向 UI 发事件；
3.Tool Future 等待对应 request ID；
4.用户选择后，TUI/App Server 提交 `Op::ExecApproval` 或 `Op::PatchApproval`；
5.Submission Loop 调用 `notify_approval()`；
6.等待中的 Tool Future 被唤醒；
7.批准则在限定策略下继续，拒绝则生成模型可见结果。

这说明 Submission Loop 在 Turn 执行期间仍保持活跃，可以处理 approval、interrupt 和 steer。

## 33. 用户中断

用户按下中断键后：

```text
TUI → turn/interrupt → Op::Interrupt
  → submission_loop::interrupt()
  → active task CancellationToken.cancel()
```

模型 stream 和 Tool Future 通过 child token 感知取消。Task 收尾逻辑仍会运行，避免把 Session 留在“永远 active”的状态。

# 第五部分：三种典型执行轨迹

## 34. 轨迹一：无需工具的普通回答

```text
UserInput
  → TurnContext
  → RegularTask
  → run_turn
  → StepContext
  → Prompt
  → Responses API
  → assistant message
  → History + UI Event
  → no follow-up
  → Turn complete
```

通常只有一次 sampling。

## 35. 轨迹二：搜索、修改、测试

```text
Sampling 1：模型调用 exec_command 搜索代码
Tool 1：Runtime 执行 rg，输出写入 History

Sampling 2：模型调用 exec_command 读取文件
Tool 2：Runtime 返回文件片段

Sampling 3：模型调用 apply_patch
Tool 3：Runtime 校验路径并修改文件

Sampling 4：模型调用 exec_command 运行测试
Tool 4：Runtime 返回退出码和日志

Sampling 5：模型生成最终总结
Turn complete
```

这五次 sampling 对用户仍然是一个 Turn。

## 36. 轨迹三：运行中追加问题

```text
Turn 正在执行 Tool
  → 用户发送新输入
  → TUI 选择 turn/steer
  → Session.steer_input 写入 queue
  → 当前安全边界结束
  → run_turn drain pending input
  → 必要时启动新 MCP
  → 捕获新 Step
  → 新输入进入下一次 Prompt
```

它不会直接把文字插进正在接收的 SSE 字节流。

# 第六部分：建议的源码阅读断点

## 37. 第一轮：只跟主干

按以下顺序读，不进入每个 helper：

1. `cli/src/main.rs::main`
2. `cli/src/main.rs::cli_main`
3. `tui/src/lib.rs::run_main`
4. `tui/src/app_server_session.rs::start_thread_with_session_start_source`
5. `core/src/thread_manager.rs::start_thread`
6. `core/src/session/mod.rs::Session::spawn_internal`
7. `core/src/session/handlers.rs::submission_loop`
8. `core/src/session/handlers.rs::user_input_or_turn_inner`
9. `core/src/tasks/mod.rs::Session::spawn_task`
10. `core/src/tasks/regular.rs::RegularTask::run`
11. `core/src/session/turn.rs::run_turn`
12. `core/src/session/turn.rs::run_sampling_request`
13. `core/src/session/turn.rs::try_run_sampling_request`

读完应能回答：“一个用户输入怎样触发多次模型调用？”

## 38. 第二轮：跟 Prompt 和 Context

1. `core/src/client_common.rs::Prompt`
2. `core/src/context_manager/history.rs::for_prompt`
3. `core/src/session/mod.rs::build_initial_context_with_world_state`
4. `core/src/session/mod.rs::record_step_world_state_if_changed`
5. `core/src/client.rs::build_responses_request`

读完应能回答：“最终发送给模型的 instructions、input 和 tools 分别来自哪里？”

## 39. 第三轮：跟 Tool

1. `core/src/session/turn.rs::built_tools`
2. `core/src/tools/spec_plan.rs::build_tool_router`
3. `core/src/tools/registry.rs::ToolRegistry`
4. `core/src/tools/router.rs::build_tool_call`
5. `core/src/tools/router.rs::dispatch_*`
6.一个具体 Handler，例如 shell 或 apply_patch
7. `core/src/tools/parallel.rs`

读完应能回答：“模型看见工具、生成调用、Runtime 执行和回传结果之间怎样保持一致？”

## 40. 第四轮：跟 Skill 和 MCP

1. `core-skills/src/loader.rs`
2. `ext/skills/src/extension.rs`
3. `core-skills/src/injection.rs`
4. `core/src/session/turn.rs::build_skills_and_plugins`
5. `core/src/session/mcp.rs`
6. `core/src/mcp_tool_exposure.rs`

读完应能回答：“能力在启动时发现多少，在用户需要时又加载多少？”

## 41. 最后用一句话串起来

Codex 的执行主线可以压缩为：

> CLI/TUI 先通过 App Server 创建 Thread；ThreadManager 创建持久 Session 和 Submission Loop。用户输入被转换成 `Op::UserInput`，Session 为它创建稳定的 TurnContext 和异步 RegularTask。`run_turn()` 在每次采样前捕获 StepContext，把 History、World State、Skill 与模型可见 ToolSpec 组合成 Prompt，流式调用 Responses API；若模型返回 Tool Call，则当前 Step 的 ToolRouter 在权限和沙箱中执行，结果写回 History并触发下一次采样；直到模型给出最终消息且没有待处理输入，Turn 才结束，而 Session 继续等待下一次操作。

