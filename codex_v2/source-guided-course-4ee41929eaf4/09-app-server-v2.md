# 09：App-server v2 边界

App-server 是当前 TUI 与 core 之间的 typed API 边界，也服务其他客户端。学习它时要同时观察 wire contract、请求处理和 core event 投影。

## 先说人话：它像一个业务后台

桌面 App、IDE 插件等客户端不会直接操纵 Codex 内部对象。它们向 App-server 发送类似 `thread/start`、`turn/start` 的请求，再通过通知持续接收执行进度。

这很像浏览器调用后台服务：浏览器负责界面和用户交互，后台负责真正的业务状态与执行。尤其要注意：**收到一次请求响应，不一定表示耗时任务已经完成。**

## 1. 两阶段生命周期

```text
thread/start
  → 创建可寻址 thread，确定初始配置、环境和持久化模式
  → ThreadStartResponse + thread/started notification

turn/start { threadId, input, overrides... }
  → 找到已存在 thread，构造本 turn 设置
  → submit Op::UserInput
  → TurnStartResponse
  → turn/started、item/*、turn/completed notifications
```

Thread 和 turn 分开，使客户端能够先创建/恢复会话，再连续提交多个任务，并可在 turn 间修改 sticky 设置。

## 2. `turn/start` 到 `Op::UserInput`

`request_processors/turn_processor.rs` 的主路径会：

1. 解析并映射 `input` items；
2. 解析 cwd、runtime workspace roots 和 environment selection；
3. 构造 `ThreadSettings` overrides；
4. 处理 approval、sandbox/permissions、model、effort、summary、mode 等；
5. 创建 `Op::UserInput`，带上 output schema、metadata、additional context；
6. 提交给目标 thread，并将 submission ID 作为 turn 身份的一部分返回。

API 参数不是直接覆盖全局 config；它们经过验证和 thread/turn 设置合并。

## 3. 为什么 API 类型与 core protocol 分开

App-server v2 是外部 wire contract：

- 字段使用 camelCase；
- request optional 字段使用 nullable optional 的 TS 表达；
- API ID 在边界优先用 `String`；
- experimental 字段需要 gate；
- schema 和生成的 TypeScript 必须与 serde rename 一致。

Core 的 `Op`/`EventMsg` 面向内部运行时，可以更接近 Rust 业务类型。Request processor 与 event mapper 的职责正是隔离两种演进速度。

## 4. Response 与 notification 不同

JSON-RPC response 确认请求是否接受，并返回资源快照；notification 表达后续异步生命周期。`turn/start` 成功返回不等于模型已完成，甚至不等于客户端已收到所有 started/item 事件。

客户端因此需要：

- 用 request ID 匹配 response；
- 用 thread ID / turn ID 路由 notifications；
- 容忍 response 与某些 notification 在异步调度下靠得很近；
- 将 approval、elicitation 等 server request 当作需要回复的双向交互。

可以用外卖订单类比：

- `turn/start` 的 response：订单已经被系统接收；
- `turn/started`：商家开始处理；
- 各种 item notification：备餐和配送过程；
- `turn/completed`：订单完成；
- approval server request：商家遇到需要你确认的事项，必须等你回复。

## 5. Core event 的投影

`bespoke_event_handling.rs` 接收 core `EventMsg`，将其转为 v2 notification 或 server request。例如：

- `TurnStarted` → `TurnStartedNotification`；
- turn items/deltas → item 生命周期通知；
- exec/patch approval → server 向客户端发 request；
- user input/elicitation → 等待客户端 response；
- turn complete/error → 更新对外状态。

这层不是机械 serde 转换：它还处理兼容、缓存、请求关联和客户端能力。

## 6. 修改 v2 API 的验证清单

1. 只在 v2 添加新 surface；
2. `*Params` / `*Response` / `*Notification` 命名正确；
3. serde 与 TS rename 对齐；
4. request optional 字段标注 `#[ts(optional = nullable)]`；
5. 判断是否需要 `#[experimental(...)]`；
6. 更新 `app-server/README.md`；
7. 运行 `just write-app-server-schema`，必要时加 `--experimental`；
8. 运行 `just test -p codex-app-server-protocol`；
9. 行为变化通过 public JSON-RPC 集成测试验证，而不只测 struct 序列化。

## 读完后自测

1. 为什么 `turn/start` 返回成功后，客户端仍要继续监听通知？
2. Notification 和需要客户端回复的 server request 有什么差别？
3. 为什么字段重命名即使 Rust 内部能编译，也可能是破坏性改动？

## 本章词汇表

| 词语 | 直译 | 在 App-server 中的意思 |
|---|---|---|
| JSON-RPC | JSON 远程过程调用 | 用方法名、参数、ID、结果和错误通信的协议 |
| Params | 参数 | 客户端发给某个 API 方法的请求 payload |
| Response | 响应 | 对指定 request ID 的直接回答 |
| Notification | 通知 | 服务端主动发送、不要求客户端回复的消息 |
| Server request | 服务端请求 | 服务端主动询问客户端，客户端必须返回 response |
| Wire contract | 线上契约 | 客户端实际看到的 JSON 字段、类型和时序 |
| Experimental gate | 实验门控 | 只有显式启用实验 API 后才能使用的边界 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

- 对比 `protocol/v2/thread.rs::ThreadStartParams` 与 `turn.rs::TurnStartParams`：哪些字段是初始设置，哪些是 sticky override？
- 阅读 `request_processors/turn_processor.rs` 创建 `Op::UserInput` 的代码。
- 在 `app-server/tests/suite/v2/` 找一个 `turn/start` 测试，列出 response 与 notification 的断言。
