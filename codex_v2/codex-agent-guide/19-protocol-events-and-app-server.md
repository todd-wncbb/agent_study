# 19. Protocol、Event 与 App Server

## 1. 为什么需要协议层

Codex 不只有一个终端 UI。TUI、IDE、桌面应用和其他客户端都需要启动 Thread、提交 Turn、接收流式结果和响应批准请求。如果每个客户端直接调用 Core 内部函数，API 会不稳定且难以支持远程运行。

因此系统分为：

```text
Client UI
  ↕ App Server JSON-RPC
App Server
  ↕ Core protocol Op/Event
Codex Core Session
```

## 2. 三层数据

### Client Request/Response

App Server 公共 JSON-RPC，例如：

- `thread/start`；
- `thread/resume`；
- `turn/start`；
- `turn/steer`；
- `turn/interrupt`。

类型主要在 [`app-server-protocol`](../codex-rs/app-server-protocol/src) 的 v2 协议中。

### Core Operation

App Server 转换后提交给 Session 的 `Op`，例如：

- `Op::UserInput`；
- `Op::Interrupt`；
- `Op::ExecApproval`；
- `Op::Compact`；
- `Op::Shutdown`。

### Core Event

Session 向外发出的 `Event { id, msg: EventMsg }`。App Server 再将其映射成公开 Notification。

## 3. Thread API

### `thread/start`

创建新 Thread，输入包括模型、cwd、权限、environment 和动态工具等设置。App Server 构造 `StartThreadOptions` 并调用 `ThreadManager::start_thread()`。

### `thread/resume`

从持久化 Thread ID 或 rollout 恢复。Runtime 重建 History、Session metadata 和 World State。

### `thread/fork`

从指定历史点创建新 Thread，保留 parent/fork metadata，但拥有独立未来。

### Read/List/Archive 等

这些操作主要访问 Thread Store，不一定启动 Agent Loop。

## 4. Turn API

### `turn/start`

在 idle Thread 上开始一次用户 Turn。最终进入 Core `Op::UserInput`。

### `turn/steer`

向 active turn 追加输入。它需要 expected turn ID，避免客户端把输入误发给刚刚切换的新 Turn。

### `turn/interrupt`

取消指定 active turn。Thread 仍然存在。

### Approval/Elicitation Response

客户端把用户决定发送给正在等待的 Runtime Future，不会创建新的普通用户 Turn。

## 5. 请求 ID、Thread ID、Turn ID

这些 ID 解决不同关联问题：

| ID | 关联对象 |
|---|---|
| JSON-RPC request ID | 一次 client request/response |
| Thread ID | 持久对话 |
| Submission ID | Core channel 中的一次 Op |
| Turn ID | 一次 Agent task |
| Response Item ID | 模型输出 item/UI item |
| Tool Call ID | Tool Call 与 Result |

不能用 JSON-RPC request ID 代替 Turn ID：`turn/start` 请求可能很快返回，而 Turn 继续通过 Notification 执行很久。

## 6. `SessionIo`

Core 使用 `SessionIo` 隔离 Session 内部状态：

```text
tx_sub                         Client → Session
rx_event                       Session → Client
agent_status                   watch current Agent status
session_loop_termination       await shutdown
```

`submit()` 为 Op 生成 submission ID，发送进有界 channel。模型和工具工作由后台 task 完成。

## 7. Submission Loop

Submission Loop 是控制面 dispatcher。它串行接收 Op，但每个长期 Turn 由独立 SessionTask 运行，所以 Loop 仍可处理：

-Interrupt；
-Approval；
-Steer；
-MCP refresh；
-User input answer；
-Shutdown。

如果 Submission Loop 自己 `await run_turn()` 到结束，运行中就无法处理 approval 或 interrupt。

## 8. EventMsg 类别

可以按用途分类：

### 生命周期

-Thread/Session configured；
-Turn started/completed；
-Turn item started/completed；
-shutdown。

### 流式内容

-assistant text delta；
-reasoning delta；
-plan delta；
-raw response item。

### 工具和变更

-command execution；
-file change；
-MCP call；
-Turn diff。

### 交互

-approval request；
-request user input；
-elicitation。

### 状态与诊断

-token usage；
-rate limits；
-warning/error；
-model reroute；
-Agent status。

## 9. Raw Response Item 与 Turn Item

这两个概念容易混淆：

-Raw Response Item 更接近模型/API 的原始结构，用于协议兼容、调试或高级客户端；
-Turn Item 是产品层可展示的语义对象，例如 AgentMessage、CommandExecution 或 FileChange。

一个模型 item 可能先产生流式 Turn Item lifecycle，完成后又发 raw item。客户端需要按 ID 去重和更新，而不是把所有通知都显示成新消息。

## 10. Delta 与 Completed

流式文本事件通常是：

```text
item started
  → delta 1
  → delta 2
  → ...
  → item completed
```

客户端应：

-按 item ID 更新同一个视图；
-保留顺序；
-处理中途断线；
-completed 后固定最终内容；
-不要把 delta 自己写成多条持久消息。

## 11. TUI 路由

TUI 的 `AppCommand::UserTurn` 经过 `thread_routing.rs`：

1.确定当前 Thread；
2.查询 active turn ID；
3.优先尝试 steer；
4.处理 missing/expected-turn-mismatch 竞态；
5.必要时调用 `turn/start`；
6.异步消费通知并更新 ChatWidget。

这使 UI 能应对“用户按 Enter 的瞬间旧 Turn 正好结束”这种真实竞态。

## 12. App Server Handler 到 Core

App Server v2 handler 的职责是：

-反序列化和验证 Params；
-解析 String ID；
-查找 Thread；
-转换 API DTO 与 Core 类型；
-提交 Op 或调用 ThreadManager；
-将 Core error 映射成 JSON-RPC error；
-把 Core Event 映射成 Notification。

它不应复制 Agent Loop 或 Tool policy。

## 13. 本地与远程

TUI 可以连接本地或远程 App Server。协议相同，但环境语义不同：

-远程 cwd 属于服务器 workspace；
-本地路径不能直接发送后假设远端可读；
-媒体或文件可能需要上传/资源 URI；
-approval UI 在客户端，实际 sandbox 在服务端；
-断线与重连需要重新订阅 Thread。

## 14. 订阅和断线

客户端可以订阅 Thread 事件。断线时需要区分：

-客户端断开，但 Agent/Thread 继续运行；
-本地拥有进程退出，Session 被关闭；
-重新连接后从 Store 读取已完成 Turns；
-从最新 cursor/状态继续接收通知。

不能假设 WebSocket/stdio 断开一定等于取消用户任务。

## 15. API 设计约定

App Server v2 的重要约定：

-方法名使用单数资源 `<resource>/<method>`；
-请求为 `*Params`，响应为 `*Response`，通知为 `*Notification`；
-wire 字段 camelCase，配置 RPC 例外；
-可选请求字段使用 nullable optional；
-API 边界 ID 使用 String；
-timestamp 使用 Unix seconds，命名 `*_at`；
-新 list API 默认 cursor pagination；
-实验字段和方法显式 gating；
-Rust serde rename 与 TS rename 保持一致。

## 16. Breaking Change 风险

以下变化即使 Rust 编译通过，也可能破坏外部客户端：

-修改 v2 Params/Response/Notification；
-改变 enum wire value；
-删除 rawResponseItem 事件；
-改变 ID 语义；
-改变 resume rollout 兼容；
-把可空字段改成省略或相反；
-改变通知顺序。

因此 API 变化要更新 schema fixtures、README 和集成测试。

## 17. 测试链

App Server 测试应使用公开 JSON-RPC：

```text
TestAppServer::builder().build()
  → send thread/start
  → send turn/start
  → mock Responses SSE
  → receive Notifications
  → submit approval/steer
  → assert public response and request body
```

不要只调用内部 handler helper，否则无法证明 wire naming、nullable 字段和通知映射正确。

## 18. 三种投影总结

```text
Model History
  目标：让下一次推理继续
  内容：ResponseItems、Tool Call/Result、context

UI Event Stream
  目标：实时展示和交互
  内容：delta、started/completed、approval、warning

Rollout
  目标：恢复、fork、审计
  内容：History 事实 + context/config/checkpoint metadata
```

同一个事实可以出现在三者中，但表示和生命周期不同。

