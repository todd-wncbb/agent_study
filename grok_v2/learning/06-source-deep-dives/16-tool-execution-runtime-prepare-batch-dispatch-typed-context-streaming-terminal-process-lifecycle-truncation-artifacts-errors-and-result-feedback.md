# 源码精读 16：Tool Execution Runtime 如何准备调用、并发执行、管理进程、截断输出并把结果回填给模型

> 上一篇停在 `Permission Allow`。本文继续追踪：一个已经获批的 Tool Call 怎样真正进入实现层，文件、终端、MCP 与 Web Tool 怎样共享统一 Runtime，执行结果又怎样同时服务 UI、模型上下文、Hook、Artifact、Telemetry 和下一轮推理。

## 0. 本文回答什么

本文重点回答：

1. 模型返回的 Tool Call 为什么不能直接调用 Rust 函数；
2. `prepare_tool_call` 在 Dispatch 前还做了哪些工作；
3. 一批 Tool Call 为什么“准备串行、执行并行”；
4. 哪些调用可以并发，哪些必须按文件串行；
5. `Tool`、`ToolDyn`、`ToolDispatch` 与 `FinalizedToolset` 各处在哪一层；
6. Typed Args/Output 怎样跨越 JSON 和动态注册边界；
7. `ToolCallContext` 怎样传 CWD、Behavior Version、Cancellation 与 Resources；
8. Meta-tool `use_tool` 怎样二次分发而不死锁、不重复 Reminder；
9. Streaming Tool 的 Progress/Terminal 不变量是什么；
10. 为什么 Session 的普通本地调用目前主要消费 Terminal，而 Bash Mode 有另一条实时流；
11. Terminal Actor 怎样 Spawn、轮询、超时、自动后台化和回收进程树；
12. 前台命令、后台任务和 Monitor 有什么区别；
13. 输出为什么同时存在内存首尾、完整日志文件、Prompt 文本与 UI Delta；
14. Tool Error 与“成功返回的逻辑失败”为什么是两层错误；
15. 图片、PDF、MCP Rich Content 和 Web Fetch Artifact 怎样避免塞爆上下文；
16. Tool Result 怎样按原始 Call ID 回填后触发下一次模型请求。

源码基线：

```text
ed6d543
```

---

## 1. 总体执行图

```text
模型 ToolCallResponse
  │ name + call_id + raw JSON arguments
  ▼
SessionActor::prepare_tool_call
  │ 注册 UI → JSON 恢复 → Typed Parse
  │ Plan Gate → PreToolUse Hook → Permission
  ▼
PreparedToolCall
  │
  ├── 同批其他调用也完成 Prepare
  ▼
FuturesUnordered 并发 Dispatch
  │
  ├── 同文件读写 Mutex
  ├── Auth Retry
  └── 可中断 Wait Tool
  ▼
WorkspaceOps::call_tool
  ▼
FinalizedToolset
  │ 参数反向映射 → Context → LocalRegistry
  ▼
ToolDyn → Tool::execute
  │ Progress* → Terminal(Result<Output, ToolError>)
  ▼
FinalizedToolset::finalize_output
  │ Output Converter → Reminder → Prompt Render → Persistence
  ▼
ToolRunResult
  │ structured output + prompt_text + effective_tool_name
  ▼
Session Post-flight
  │ ACP Update / Hook / Image / Signal / Telemetry
  ▼
ConversationItem::tool_result(call_id, ...)
  ▼
下一次模型采样
```

这条链最核心的设计是：

> 执行层同时保留“结构化真相”和“给模型看的投影”，避免 UI、协议和 Prompt 互相污染。

---

## 2. 核心源码地图

### 2.1 Session 编排

```text
crates/codegen/xai-grok-shell/src/session/
  acp_session.rs
  acp_session_impl/tool_calls.rs
  acp_session_impl/tool_dispatch.rs
  acp_session_impl/tool_layer_images.rs
```

关键符号：

- `PreparedToolCall`；
- `execute_tool_calls`；
- `execute_tool_calls_batch`；
- `prepare_tool_call`；
- `dispatch_tool`；
- `handle_bridge_tool_success`；
- `handle_tool_error`；
- `DrainedToolSuccess`。

### 2.2 通用 Tool Runtime

```text
crates/common/xai-tool-runtime/src/
  tool.rs
  dispatch.rs
  context.rs
  error.rs
  render.rs
  streaming.rs
```

关键类型：

- `Tool`；
- `ToolStream`；
- `ToolStreamItem`；
- `ToolProgress`；
- `TypedToolOutput`；
- `ToolDispatch`；
- `ToolCallContext`；
- `TypedExtensions`；
- `ToolError`。

### 2.3 Grok Tool Registry

```text
crates/codegen/xai-grok-tools/src/
  registry/types.rs
  bridge.rs
  types/tool_io.rs
  types/output.rs
  types/resources.rs
```

关键符号：

- `FinalizedToolset`；
- `prepare_dispatch`；
- `call_streaming_with_cancellation`；
- `finalize_output`；
- `call_raw`；
- `InnerDispatchForToolset`；
- `ToolRunResult`；
- `ToolOutput`。

### 2.4 Terminal Runtime

```text
crates/codegen/xai-grok-tools/src/computer/
  types.rs
  local/terminal.rs
  local/lifecycle.rs
  local/cgroup.rs
  task_log.rs
```

### 2.5 Workspace 路由

```text
crates/codegen/xai-grok-workspace/src/workspace_ops.rs
```

`WorkspaceOps` 统一 Local 与 Proxy 两种执行位置。

---

## 3. `PreparedToolCall` 是 Prepare 与 Execute 的所有权边界

`PreparedToolCall` 保存：

| 字段 | 用途 |
| --- | --- |
| `call_id` | 模型生成的 Tool Use ID，用于 Tool Result 配对 |
| `tool_call_id` | ACP 内部 Tool Call ID |
| `tool_name` | 模型请求的 Wire Name |
| `raw_arguments` | 原始参数字符串，供 Hook 与审计 |
| `parsed_args` | 可 Dispatch 的 JSON Value |
| `model_id` | 调用发生时的模型 |
| `concatenated_json_count` | 是否从粘连 JSON 中恢复 |
| `dispatch_target_name` | Meta-tool 的真实目标 |
| `is_read_only` | 是否可绕开同文件写锁 |

它不是 Tool Input 本身，而是一张“执行凭证”：

- 参数已经被 Toolset 验证；
- Hook 已经允许；
- Permission 已经允许；
- Plan Gate 已经允许；
- UI 已经注册；
- 真实目标已经解析。

只有 `PreparedToolCall` 会进入并发 Dispatch 阶段。

---

## 4. Prepare 的第一步：先注册 Pending Tool Call

`prepare_tool_call` 一开始就向客户端发送：

```text
ToolCall(status = Pending)
```

此时甚至还未完成参数解析。

为什么这么早？

- 用户能立即看见模型正在尝试什么；
- 后续 Parse Error、Hook Deny、Permission Deny 都能更新同一个 UI Entry；
- Timeline 不会凭空出现一个“结果”，却没有开始事件；
- Permission Prompt 可以引用已经存在的 Tool Call。

对于 Subagent Tool，还会尽早提取：

```text
subagentBackground
```

用于 UI 呈现。

---

## 5. Tool Identity 会经历两次精化

首次 Pending 注册时：

- 只有 Wire Name；
- 参数可能只是尽力解析；
- `stamp_tool_meta` 只能生成初步 Identity。

Typed Parse 成功后：

- 已知 `ToolInput`；
- 可计算 Canonical Input；
- 可知道真实 `ToolKind`；
- Meta-tool 可得到 Target；
- `send_tool_call_start` 更新 Title、Kind、Location、Raw Input。

这是一种：

> 先保证时间线完整，再逐步增强展示精度。

---

## 6. 参数解析不是一次 `serde_json::from_str`

### 6.1 空参数归一化

某些模型对无参数 Tool 可能返回：

- 空字符串；
- 空白；
- `null` 风格内容。

`normalize_empty_arguments` 把它们规范为可解析对象。

### 6.2 粘连 JSON 恢复

模型有时返回：

```text
{"path":"a"}{"path":"b"}
```

系统会：

1. 提取多个 JSON Object；
2. 逐个让当前 Tool 的 Typed Parser 尝试；
3. 选择第一个可匹配对象；
4. 只执行一个对象；
5. 记录对象总数；
6. 在 Tool Result 中追加 System Reminder，要求以后拆成独立 Tool Call。

### 6.3 为什么不能执行全部恢复对象

一个 Tool Call ID 只能对应一个 Tool Result。

自动执行全部对象会造成：

- Permission Scope 不明确；
- UI 只显示一次却发生多次副作用；
- Tool Result 无法一一配对；
- 重试与审计含糊。

因此恢复只用于挽救一次明确调用。

### 6.4 解析失败的回填

若最终 `try_parse` 失败：

- 发送 Failed Tool Update；
- 把 Parse Error 作为 Tool Result 回填；
- 返回 `ToolLoop::ToolParsingError`；
- 不进入 Hook、Permission 或执行。

模型能在下一轮修正参数。

---

## 7. `try_parse` 把 Client 参数映射回 Canonical 参数

Toolset 可能通过 Template：

- 改 Tool 名；
- 改参数名；
- 兼容不同 Harness。

`FinalizedToolset::try_parse` 先用 `reverse_params` 把客户端参数恢复为 Canonical Key，再调用每个 Tool 的 Parser。

所以：

```text
Model-facing Schema
      ↓ reverse mapping
Canonical Args
      ↓ deserialize
ToolInput
```

Tool 实现无需理解所有外部命名变体。

---

## 8. Plan Mode Gate 早于 Permission

`plan_mode_edit_gate` 在 Permission 之前执行。

原因是：

- YOLO 不理解 Plan Mode；
- Permission Allow 也不能改变当前 Agent Mode；
- Plan Mode 的“不修改实现文件”是流程不变量。

因此即使 Always Approve：

- 非 Plan File 的 Edit 仍被拒绝；
- `apply_patch` 因无法在解析前精确得知全部目标，保守拒绝；
- Read、Search、MCP、Web 等不受该 Edit Gate 影响。

Mode Gate 与 Permission 是正交层。

---

## 9. PreToolUse Hook 看真实目标

对于普通 Tool：

```text
hook_tool_name = wire tool name
```

对于 Meta-tool：

```text
use_tool → linear__save_issue
```

Hook 看见：

```text
linear__save_issue
```

而不是笼统的 `use_tool`。

`dispatch_target_name` 是所有 Dispatch-phase Hook 的单一真实目标来源。

这防止策略只允许 Dispatcher，却绕过对目标 Tool 的 Hook。

---

## 10. Permission Allow 后还可能有 Plan Approval

`exit_plan_mode` 是特殊交互：

- Permission 决定 Tool 是否可调用；
- Plan Approval 决定当前 Plan 是否被接受；
- 两者不是同一个问题。

系统读取 Plan File：

- 有内容 → 显示审批；
- 不存在/空 → 根据调用类型决定；
- 无法读取 → Fail Closed，仍拦截；
- Client 中途断开 → 保持 `awaiting_plan_approval`，Resume 后重新展示。

`AwaitingApprovalGuard` 用 Drop 清理状态，但在 Client Disconnect 路径会 `disarm`，故意保留 Pending。

---

## 11. 为什么一批调用先串行 Prepare

模型一次响应可能返回多个 Tool Call。

`execute_tool_calls_batch` 先依次执行 Prepare：

1. 注册开始事件；
2. Parse；
3. Hook；
4. Permission；
5. 收集 `PreparedToolCall`。

这么做的理由：

- 多个 Permission Prompt 不会同时弹出；
- Hook 与 Permission 的用户交互顺序确定；
- 任何用户 Reject/Cancel/Followup 能阻止后续调用；
- 在执行副作用前，批次授权状态已清楚。

### 11.1 早期终态如何影响后续调用

若某个调用返回：

- User Permission Reject；
- Cancelled；
- Followup Message；

后面的未准备调用：

- 不执行；
- 每个仍写入一个“因前序决定而取消”的 Tool Result；
- 保证模型历史里没有悬空 Tool Call。

PolicyDeny 的语义不同：

- 通常回填错误；
- `ToolLoop::Continue`；
- 模型仍可从结果中调整。

---

## 12. Exit Plan Tool 被放在批次尾部

如果一批同时包含：

- 普通 Tool；
- `exit_plan_mode`；

`split_exit_plan_tail` 会先执行普通部分，再执行退出 Plan。

否则退出调用可能：

- 先改变 Mode；
- 让同一模型响应中的编辑调用在错误 Mode 下运行；
- 或让审批 UI 与真实计划内容错序。

---

## 13. 获批调用使用 `FuturesUnordered`

所有 `PreparedToolCall` 转为 Future 后放入：

```rust
FuturesUnordered
```

它的行为是：

- 同时 Poll 多个执行；
- 谁先完成就先产出；
- 不等待较慢的前序 Future；
- UI 可尽快看到 Fast Tool 的完成结果。

### 13.1 完成顺序与模型调用顺序不同

模型发出：

1. 慢 Search；
2. 快 Read；

UI 可能先看到 Read 完成。

但每个结果都带原始 Call ID，模型历史仍能正确配对。

### 13.2 为什么不用 `join_all`

`join_all` 要等整批完成后才统一返回。

`FuturesUnordered` 允许：

- 增量 Post-flight；
- 立即发 ToolCallUpdate；
- 立即记录 Telemetry；
- 立即处理 Auth Retry。

---

## 14. 同文件锁：并发不能破坏读写因果

系统从参数中提取：

- `file_path`；
- `path`；
- `target_file`。

先找出本批所有非 Read-only 调用涉及的路径。

如果一个路径存在写调用，则所有命中该路径的调用共享：

```rust
Arc<tokio::sync::Mutex<()>>
```

### 14.1 为什么读也要进入该锁

同批：

1. Edit `a.rs`；
2. Read `a.rs`；

若 Read 不锁，可能读到修改前状态。

只有“该路径在本批完全没有 Write”时，Read 才可自由并发。

### 14.2 为什么锁 Key 是原始参数字符串

这是批内轻量保序机制，不是安全路径解析器。

Permission 与 Tool 实现仍负责：

- CWD；
- Symlink；
- Canonical Path；
- Sandbox。

它解决的是常见同名文件调用的并发冲突。

### 14.3 为什么不锁目录列表

`target_directory` 被刻意排除。

目录读取若全部锁定，会让大量独立查询失去并发收益。

---

## 15. Wait Tool 可以被用户插话打断

以下 Tool 可能长时间阻塞等待：

- Task Output，且 Timeout > 0；
- Wait Tasks；
- Await/AwaitShell。

执行时使用 `tokio::select!`：

```text
Tool 完成
    vs
Pending Interjection 出现
```

并使用 `biased` 优先顺序。

用户发来新消息后：

- Wait Future 被 Drop；
- 返回一个状态为 `cancelled` 的正常 `TaskOutputResult`；
- Prompt Text 是“Wait interrupted”；
- Agent 能先处理新用户输入。

### 15.1 为什么不是整个 Turn Cancel

用户插话不一定要杀掉后台任务。

它只中断“等待动作”：

- 后台工作可继续；
- Agent 先响应真人；
- 之后仍可重新查询 Task。

### 15.2 `BlockingWaitGuard`

进入 Wait 时计数加一，Future Drop 或完成时减一。

这让 Session 知道当前是否卡在可中断等待中。

---

## 16. `WorkspaceOps` 统一本地与代理执行

`WorkspaceOps::call_tool` 有两种实现：

### 16.1 Local

- 用 Session ID 查 Workspace Session；
- 取得该 Session 的 Finalized Toolset；
- In-process 调用 `toolset.call(...)`。

### 16.2 Proxy

- 检查 Hub Client 连接；
- 把 Name 转成 `ToolId`；
- 构造 `ToolCallContext`；
- 调用远端 Harness；
- 消费 Stream 到 Terminal；
- 从 `TypedToolOutput.value` 反序列化 `ToolRunResult`。

### 16.3 为什么 Shell Session 当前总走 Local

`dispatch_tool` 的注释明确：

> Agent sessions always use local workspace ops.

但保留 Proxy 抽象使 Workspace API、Remote Harness 和测试可共享协议。

---

## 17. `Tool` Trait：Typed Tool 作者面对的接口

`Tool` 有两个关联类型：

```rust
type Args: Deserialize + JsonSchema
type Output: Serialize + ToolOutput
```

Tool 作者必须实现：

- `id()`；
- `description()`；

并在两种执行方法中选一个：

- `run`：普通一次性 Tool；
- `execute`：Streaming Tool。

### 17.1 Runtime 永远调用 `execute`

默认 `execute` 会：

1. 调用 `run`；
2. 把结果包成 `terminal_only` Stream。

因此非流式 Tool 不需要理解 Stream。

### 17.2 两者都没实现

默认 `run` 返回：

```text
ToolError::NotImplemented
```

不是 Panic，也不是空结果。

---

## 18. 为什么 `Tool` 不是 Object-safe

`Tool` 有关联类型 `Args` 和 `Output`，并使用泛型化返回。

动态 Registry 无法直接保存：

```rust
Arc<dyn Tool>
```

所以 Runtime 在注册时通过 `ToolDyn` 做类型擦除：

```text
JSON Value
  ↓ deserialize
Typed Args
  ↓ Tool::execute
Typed Output
  ↓ serialize
TypedToolOutput
```

Tool 实现仍拥有编译期类型安全，Registry 则拥有运行期动态路由。

---

## 19. `ToolDispatch` 是 Object-safe 的统一路由协议

`ToolDispatch::call` 接收：

- `ToolId`；
- JSON Args；
- `ToolCallContext`。

返回：

```rust
ToolStream<TypedToolOutput>
```

`call_terminal` 是默认便利方法：

- 丢弃 Progress；
- 返回第一个 Terminal；
- Stream 无 Terminal 时产生 `stream_no_terminal` Error。

这让：

- 本地 Registry；
- Hub；
- Meta-tool Inner Dispatch；
- 测试 Mock；

共享同一执行契约。

---

## 20. Tool Stream 的协议不变量

合法 Stream 形状：

```text
Progress*
Terminal(Result<Output, ToolError>)
```

即：

- Progress 可以为零；
- Terminal 必须恰好一个；
- Terminal 必须最后；
- Terminal 后消费者停止读取。

### 20.1 Progress 类型

`ToolProgress`：

- `Text`；
- `Content { blocks }`；
- `Custom { subkind, payload }`。

### 20.2 Content Block

支持：

- Text；
- Image；
- Resource URI。

Image 可带：

- MIME；
- Base64；
- Media ID；
- Filename；
- Path；
- Metadata。

### 20.3 无 Terminal 为什么是协议错误

Stream 静默结束会让消费者无法区分：

- Tool 成功但忘记结果；
- Tool 被 Drop；
- 网络断开；
- 实现 Bug。

所以强制生成：

```text
stream_no_terminal
```

---

## 21. 当前 Session 普通 Tool 与 Streaming 能力的关系

`FinalizedToolset` 完整支持 Streaming：

- `call_streaming`；
- `call_streaming_with_cancellation`；
- Progress 原样转发；
- Terminal 统一进入 `finalize_output`。

但当前 `SessionActor::dispatch_tool` 调用的是：

```text
WorkspaceOps::call_tool
```

Local 路径又调用：

```text
FinalizedToolset::call
```

它会消费并忽略 Progress，只保留 Terminal。

因此要区分：

- Runtime 已具备 Streaming 协议；
- 普通 Agent Tool Session 路径当前主要消费最终结果；
- Bash/Monitor 可通过 Notification Handle 走旁路实时更新；
- Direct Bash Mode 有独立的显式 Streaming UI 路径；
- Hub Harness 可直接传递 Runtime Progress。

---

## 22. `ToolCallContext`：不用加 Trait 参数的扩展总线

`ToolCallContext` 包含：

- `call_id`；
- `TypedExtensions`。

`TypedExtensions` 以 `TypeId` 为 Key，保存：

```rust
Arc<dyn Any + Send + Sync>
```

### 22.1 常见 Extension

- `Cwd`；
- `BehaviorVersion`；
- `TraceContext`；
- `SessionContext`；
- `Cancellation`；
- `Resources`；
- `TemplateRenderer`；
- `InvokingToolParamNames`；
- `InnerDispatch`；
- `WorkspaceViewerContext`。

### 22.2 为什么用 Type 而不是字符串

字符串 Map 容易：

- 拼写漂移；
- 类型不一致；
- 每个 Tool 重复反序列化；
- 运行期才发现错误。

Typed Extension 让 Tool 用：

```rust
ctx.get::<Cwd>()
```

得到明确类型。

### 22.3 Safe Default

例如 `WorkspaceViewerContext` 的所有 Flag 默认 Off。

缺少 Extension 不会意外启用高成本或更宽行为。

---

## 23. `prepare_dispatch` 做什么

`FinalizedToolset::prepare_dispatch`：

1. 按 Client-facing Name 查 Tool Entry；
2. Clone Registry ID、Output Converter、Reverse Params；
3. 释放 Tools Read Lock；
4. Reverse-remap 参数；
5. 解析 Meta-tool Effective Name；
6. 解析 Contract Version；
7. 构造 Runtime Call ID；
8. 注入 Resources 与 Renderer；
9. 注入 CWD Override；
10. 注入 Cancellation Token；
11. 注入 Behavior Version；
12. 注入 Inner Dispatch；
13. 注入 Viewer Context；
14. 从 LocalRegistry 查 Type-erased Handle。

### 23.1 为什么必须在 Await 前释放 Read Lock

Tool 执行可能：

- 很慢；
- 触发 Reload；
- 通过 Meta-tool 再次访问 Toolset；
- 等待远端服务。

若持有 Registry Read Guard 跨越 Await：

- Hot Reload 可能饥饿；
- Inner Dispatch 可能死锁；
- 并发能力下降。

所以 `DispatchParts` 只保存 Owned Clone。

---

## 24. Meta-tool `use_tool` 的二次 Dispatch

`use_tool` 是模型可见的统一入口，真实目标可能是：

```text
linear__save_issue
```

### 24.1 Outer Dispatch

`prepare_dispatch("use_tool")`：

- 从参数读 `tool_name`；
- 把它记为 `effective_tool_name`；
- Context 注入 `InnerDispatch`。

### 24.2 Inner Dispatch

`use_tool` 实现从 Context 取出 `InnerDispatch`，调用目标 Tool。

`InnerDispatchForToolset` 使用：

```text
FinalizedToolset::call_raw
```

### 24.3 为什么不能再次调用普通 `call`

普通 `call` 会执行：

- Reminder 收集；
- Prompt Render；
- Resource Persistence。

Outer `use_tool` 完成后还会再执行一遍。

因此 Inner 必须：

- 只执行目标；
- 返回原始 `ToolOutput`；
- 让 Outer 统一 Post-process。

### 24.4 为什么绕过 ToolBridge Mutex

Outer 调用已经可能持有 Bridge 相关资源。

若 Inner 再走同一个 Outer Mutex，会自锁。

`call_raw` 直接进入 Finalized Toolset。

### 24.5 为什么 Inner Context 不再注入 InnerDispatch

目标 MCP Tool 不应再次调用 `use_tool`。

剥掉该 Extension：

- 防止递归链；
- 限制栈深；
- 明确 Meta-tool 只能一层转发。

但 Call ID、CWD、Cancellation 等仍继承。

---

## 25. `TypedToolOutput` 同时携带三种输出

字段：

- `value`：完整 JSON；
- `model_output`：模型/MCP Rich Content；
- `chat_completion_output`：可选前端 Chat Frame。

### 25.1 为什么不能只有 JSON

从 JSON 再恢复具体 Output 类型：

- 需要知道真实 Rust Type；
- 容易丢图片 Metadata；
- Dynamic Tool 不一定有本地类型；
- Chat Completion Projection 可能无法重建。

因此类型擦除时就提取这些投影。

### 25.2 `model_output` 永不为空

默认实现会把 JSON 序列化为 Text Block。

即使 Tool 没有自定义 Rich Output，也满足 MCP Content 非空不变量。

---

## 26. `finalize_output` 是 Terminal Result 的单一收口

无论 Streaming 还是 Non-streaming，成功 Terminal 都进入：

```text
FinalizedToolset::finalize_output
```

步骤：

1. 用 Output Converter 把 JSON 转回聚合 `ToolOutput`；
2. 查询是否启用 System Reminder；
3. 逐个 Reminder Collector 收集补充；
4. `output.to_prompt_format()`；
5. 把 Reminder 包装进 Prompt Text；
6. 持久化 Resources；
7. 构造 `ToolRunResult`。

这样不会出现：

- Streaming 路径有 Reminder，普通路径没有；
- Hub 路径的 Prompt Format 不同；
- Resource 只在某种调用方式保存。

---

## 27. `ToolRunResult` 为什么分成三个字段

```rust
pub struct ToolRunResult {
    pub output: ToolOutput,
    pub prompt_text: String,
    pub effective_tool_name: Option<String>,
}
```

### 27.1 `output`

结构化、未被 Reminder 修改的结果。

用于：

- ACP Conversion；
- Hook Payload；
- Error Classification；
- Hunk/Task/Plan Tracking；
- JSON/Wire；
- Image Harvest。

### 27.2 `prompt_text`

给模型的文本：

- 已格式化；
- 可含 System Reminder；
- 可含 Truncation Hint；
- 可含恢复提示。

### 27.3 `effective_tool_name`

Meta-tool 的真实执行目标。

用于：

- Hook；
- Error Message；
- MCP Attribution；
- Git/PR Signal；
- Telemetry。

---

## 28. 两层失败：`Err(ToolError)` 与 `Ok(ToolOutput::Error)`

### 28.1 Hard Execution Error

`Err(ToolError)` 表示 Runtime 层失败：

- Tool 不存在；
- 参数无效；
- Permission/Unauthorized；
- Timeout；
- Cancelled；
- Network；
- Execution；
- Output Encoding/Decoding；
- Stream Protocol。

### 28.2 Logical Tool Failure

Tool 可能正常完成 Rust Future，却返回：

- ReadFile NotFound；
- SearchReplace NoMatches；
- Bash Exit Code 非零；
- MCP `is_error = true`；
- WebFetch Error Variant。

这仍是：

```rust
Ok(ToolRunResult)
```

但：

```rust
tool_result.output.is_error() == true
```

### 28.3 为什么要区分

逻辑失败仍可能有丰富结构：

- Exit Code；
- stderr；
-建议；
- Path；
- Retry 信息；
- Remote Server Metadata。

把它压成 Runtime Error 会丢信息。

---

## 29. `ToolError` 的 Typed Kind

`ToolErrorKind` 包括：

- NotImplemented；
- InvalidArguments；
- NotFound；
- PermissionDenied；
- Unauthorized；
- Timeout；
- Cancelled；
- RateLimited；
- UsagePoolExhausted；
- UsageLimitReached；
- GlobalRateLimit；
- ConcurrencyLimit；
- ServiceUnavailable；
- NetworkError；
- Execution；
- BehaviorVersionUnsupported；
- RenderLimited；
- TerminalError；
- Custom。

`detail` 是模型可读说明。

`source` 只给开发日志，不应直接暴露。

`details` 可携带：

- Tool ID；
- Custom Code；
- HTTP Status；
- Retry Hint。

Typed Kind 让上层能判断：

- 是否 Auth Retry；
- 是否网络断连；
- 是否限流；
- 是否应标记 Connection Dead。

---

## 30. Auth Retry 有两个层次

### 30.1 通用 Auth Recovery

并发 Dispatch 包裹：

```text
call_with_auth_retry
```

同批使用共享 `OnceCell<bool>`：

- 多个调用同时遇到 Auth 问题；
- 只触发一次共享恢复；
- 避免每个 Future 都刷新登录。

### 30.2 Managed MCP Reactive Reauth

若 Managed MCP：

- Runtime Error 显示 Auth Reject；
- 或成功返回的 MCP Output 是 Error 且文本是 Auth Reject；

Session 会：

1. 触发 Managed Reauth；
2. 若成功，重试一次 Dispatch；
3. 累加 Duration；
4. 单独记录 Retry Span。

这再次说明为什么需要同时检查 Hard Error 与 Logical Error。

---

## 31. Terminal Tool 的输入

`TerminalRunRequest` 包含：

- Command；
- Working Directory；
- Environment；
- Timeout；
- Output Byte Limit；
- Output File；
- Notification Handle；
- Tool Call ID；
- Display Command；
- Auto-background Flag；
- Foreground Block Budget；
- Task Kind；
- Owner Session ID；
- Description。

这不是普通的 `Command::new` 包装，而是一份完整进程生命周期协议。

---

## 32. Terminal Actor 为什么存在

`LocalTerminalActor` 单独拥有：

- Active Process Map；
- Completion Waiters；
- Completed Snapshot；
- Shell State；
- Cgroup；
- Memory Monitor；
- Process Scope；
- Output File；
- Poll Tick；
- Kill Commands。

如果 Bash Tool 自己 Spawn 并 Wait：

- 后台任务无法跨 Tool Call 查询；
- Kill Tool 找不到 Child；
- Session 结束容易遗留进程；
- 多个 Waiter 难协调；
- Persistent Shell 状态难维护。

Actor 把进程变成 Session Resource。

---

## 33. 前台执行生命周期

典型流程：

1. 为任务生成 Task ID；
2. 建立 Output File；
3. Spawn Shell；
4. 创建/附加 Process Group；
5. 注册到 Process Scope；
6. 把 ProcessState 放进 Actor；
7. Poll Child、stdout、stderr、Memory；
8. 定期发送 Output Chunk；
9. 达到 Timeout、OOM 或 Cancel 时杀进程树；
10. 等待 Child Exit；
11. 继续 Drain Pipe；
12. Flush/截断日志；
13. 通知 Waiter；
14. 返回 `TerminalRunResult`。

### 33.1 Exit 不等于 Output 完整

Child Process Exit 后，Pipe 里可能仍有缓存。

`Lifecycle` 区分：

- Running；
- Exiting；
- Output Collecting；
- Complete/Swept。

只有完成 Output Drain 才能给最终结果。

---

## 34. Process Group 与 Process Scope

只 Kill Immediate Child 不够。

Shell 可能启动：

- Compiler；
- Test Runner；
- Worker；
- Grandchild；
- Detached Process。

项目为每个命令建立 `ProcessGroup`，并注册到：

- Global Process Scope；
- 可选 Session Process Scope。

退出或 Cancel 时：

- 对 Group 执行 Kill；
- 同时对 Immediate Child `start_kill` 兜底。

### 34.1 为什么 Scope 保存 Weak

已完成任务仍可能为了查询保留数分钟。

若 Scope 对 ProcessGroup 保持强引用：

- PID 可能已被 OS 复用；
- 稍后 `kill_all` 可能误杀复用 PID 的新进程。

Reap 完成后释放强引用，使 Weak 无法 Upgrade。

这是非常关键的 PID Reuse 防护。

---

## 35. Cgroup 与 Memory Pressure

Linux 本地 Terminal 可把 Child 放进 Cgroup：

- 设置 Soft/Hard Memory Limit；
- 监控 Pressure/OOM Event；
- 超限时终止 Process Tree；
- 产生明确 Kill Reason。

它与 Timeout 独立：

- Timeout 限时间；
- Cgroup 限内存；
- Output File Cap 限磁盘写入。

---

## 36. Timeout 不只是一个数

### 36.1 Model Timeout

Bash Input 可提供 Timeout。

### 36.2 Foreground Max Timeout

模型值会被配置上限 Clamp：

- 默认最大 5 分钟；
- Production 可配置更高；
- 绝对上限 10 小时。

### 36.3 Foreground Block Budget

若启用 Auto-background：

- 命令不一定等到总 Timeout；
- 默认约 15 秒后移到后台；
- Process 继续运行；
- 当前 Tool Call 返回 Background Handle。

### 36.4 Background Max Runtime

后台任务还有独立安全上限：

```text
10 hours
```

超过后 Kill Reason 为 `max_runtime`。

### 36.5 Timeout 与 Background 的区别

- Timeout：终止进程；
- Auto-background Budget：停止阻塞 Turn，但不终止进程；
- Explicit Background：一开始就不等待完成。

---

## 37. 为什么禁止不可跟踪的 `&`

如果模型在命令里手写：

```bash
server &
```

Shell Child 可能脱离 Terminal Actor 的 Task Tracking。

实现会按 Shell 语义识别 Background Operator：

- 排除 `&&`；
- 排除重定向 `&>`、`>&`、`<&`；
- 排除 Quote 中的 `&`；
- 排除 Escaped `\&`；
- 理解 Heredoc；
- 区分 Bash、PowerShell 和 cmd.exe；
- `... & ... & wait` 视为阻塞式并行。

当 Background 功能禁用时，任何无法跟踪的 Fork 都拒绝。

推荐模型使用 Typed `is_background=true`。

---

## 38. 后台任务返回什么

`BackgroundTaskStarted` 包含：

- Task ID；
- Task Type；
- Output File；
- Status；
- Command；
- Summary；
- Retrieval Hint；
- 可选 Pre-formatted Prompt；
- 可选 PID。

模型拿到的不是“命令成功”，而是：

> 命令已启动，后续必须用 Task Output/Wait/Kill 管理。

### 38.1 Task Snapshot

查询时可得到：

- Display Command 与实际 Wrapped Command；
- CWD；
- Start/End；
- Output Preview；
- Output File；
- Total Bytes；
- Exit Code/Signal；
- Completed；
- Task Kind；
- Owner Session；
- Backgrounded；
- Block-waited；
- Explicitly-killed。

---

## 39. Owner Session 防止父子 Agent 互杀

Terminal Backend 可能由 Parent 与 Subagent 共享。

每个 Process 记录：

```text
owner_session_id
```

提供 Scoped 操作：

- `kill_foreground_commands_by_owner`；
- `kill_all_background_tasks_by_owner`；
- `reparent_notifications`。

Child Cancel 时：

- 只杀 Child 自己的任务；
- Parent 和 Sibling 不受影响。

Child 完成但某些进程需要保留时，可以把 Notification Owner Reparent 给 Parent。

---

## 40. Wait、Kill 与后台完成通知

### 40.1 Waiter 不阻塞 Actor

`CompletionWaiter` 保存：

- Reply Sender；
- Deadline。

Actor 每个 Tick 检查：

- Task 是否完成；
- Deadline 是否到达。

它不会在 Actor Loop 内 `await child.wait()`。

### 40.2 Kill Outcome

`KillOutcome`：

- Killed；
- AlreadyExited；
- NotFound。

避免用字符串猜状态。

### 40.3 Block-waited

若 Blocking Wait 已消费最终结果：

- Snapshot 标记 `block_waited`；
- Notification Bridge 不再额外注入 Auto-wake Prompt。

### 40.4 Explicitly-killed

模型已经通过 Kill Tool 得知结果时：

- 标记 `explicitly_killed`；
- 避免后台完成再次唤醒 Agent。

---

## 41. 输出为什么同时写内存和文件

Terminal Output 有两个消费者：

### 41.1 实时/Prompt 消费

需要：

- 小；
- 快；
- 能放进上下文；
- 保留开头与结尾。

### 41.2 完整诊断消费

需要：

- 尽可能完整；
- 可由 `read_file` 按需查看；
- 不占模型 Context。

所以每个请求带 `output_file`，stdout/stderr 到来时同时：

- 累加 Total Bytes；
- 写日志；
- 更新内存 Ring；
- 发 Delta Notification。

---

## 42. 内存截断为什么保留首尾

首次超限时：

1. 冻结前半部分到 `front_buffer`；
2. `output_buffer` 只保留最后半部分；
3. 后续输出继续替换 Tail；
4. 最终用 Marker 拼接。

结果：

```text
最早输出
... truncation marker ...
最新输出
```

开头常包含：

- 命令配置；
- Build Target；
- 测试环境。

结尾常包含：

- Error；
- Summary；
- Exit Reason。

---

## 43. UTF-8 与“Byte Limit”的命名陷阱

字段叫 `output_byte_limit`，但内存截断实际按字符边界操作：

- 通过 `String::from_utf8_lossy`；
- 用 `chars().count()`；
- 用 `char_indices` 找切点。

因此不会从中间切断中文或 Emoji。

Total Bytes 仍记录原始写入字节数。

---

## 44. Streaming Delta 为什么看 `total_bytes`

截断前：

```text
buffer.len() 单调增长
```

截断后 Tail 可能反复缩短。

若用 `buffer.len() > previous_len` 判断新输出：

- 截断后可能永远不再发 Chunk。

所以 ProcessState 保存：

```text
last_notified_total
```

只要单调 `total_bytes` 增长，就知道有新数据。

Progress 中还区分：

- `truncated`：累计输出已截断；
- `gap`：本 Tick 的 Delta 与上次之间存在缺口。

---

## 45. Output File 也有上限

无限日志会填满磁盘。

源码中：

- 运行时 Output File 默认上限：5 GiB；
- 完成后保留上限：64 MiB；
- 超过运行时上限会停止无界 Writer；
- 完成时 Flush 并 Truncate 到保留上限。

所以“完整输出文件”准确含义是：

> 相对模型内存 Preview 更完整，但仍受系统磁盘安全上限约束。

---

## 46. Bash Result 怎样格式化给模型

`BashOutput` 保留：

- Raw Bytes；
- ANSI-stripped、Soft-wrapped Prompt Text；
- Exit Code；
- Command；
- Truncated；
- Signal；
- Timed Out；
- Current Dir；
- Output File；
- Total Bytes；
- Delta；
- Bare Echo 标记。

Prompt Header：

```text
exit: 0
```

被 Runtime 杀死时：

```text
exit: killed (timeout)
exit: killed (max_runtime)
exit: killed (cancelled)
exit: killed (signal N)
```

若截断，还给出：

- 已显示大小；
- 总大小；
- 完整日志路径。

---

## 47. Persistent Shell State

Terminal 可启用 Persistent Shell：

- 当前目录；
- Environment；
- Function；
- Alias；

在命令间延续。

实现不是永远保持一个交互 Shell Process，而是：

- 包装命令；
- 通过额外 FD 导出 State Dump；
- Child 退出后解析；
- 更新权威 `ShellState`。

这让每次 Process 仍可被独立管理，同时保留 Shell Session 体验。

---

## 48. Direct Bash Mode 是旁路但不随意

用户直接输入 Bash Command 时，不经过模型 Tool Call。

`handle_direct_bash_command` 会：

1. 把用户命令写入 Scrollback 与 Persistence；
2. 合成 Tool Call ID；
3. 按 Execute Kind 注册 ToolCall；
4. 构造 `TerminalRunRequest`；
5. 开启 Streaming；
6. 最终只在摘要显示最后 10 行；
7. 标记 Background、Timeout、Signal 与 Exit。

它绕过 Agent 推理，但仍复用：

- Terminal Backend；
- Output Limit；
- Tool Update；
- Process Lifecycle。

---

## 49. Web Fetch 为什么需要自己的安全 Runtime

Web Fetch 不只是 `reqwest.get`。

它包含：

- URL Length Limit；
- Domain Normalization；
- Permission Domain；
- SSRF 检查；
- DNS/IP 分类；
- 手动 Redirect；
- 每次 Redirect 重新检查；
- Connect Timeout；
- Request Timeout；
- Content Type 验证；
- Body Overflow；
- HTML/Text 转换；
- Artifact。

### 49.1 为什么禁用自动 Redirect

初始 URL 可能是 Public：

```text
https://example.com
```

但它可以 Redirect 到：

```text
http://127.0.0.1/admin
```

客户端自动跟随会绕过 SSRF 检查。

所以每一跳都由 Tool Runtime 验证。

---

## 50. Web Fetch Artifact

完整转换后的页面可能很大。

系统将完整内容写到 Session Artifact：

- 文件名递增分配；
- 使用独立目录；
- 扫描现存 Artifact；
- 总预算 1 GiB；
- 返回 Path、Size、Line Count；
- Prompt 只放 Preview 与读取提示。

### 50.1 分配为什么要避免覆盖

Artifact Number 通过扫描与分配文件协调。

即使并发 Fetch：

- 不应写到同一编号；
- 不应 Truncate 其他调用结果。

### 50.2 Artifact 不是模型附件

它首先是本地文件：

- 模型按需用 Read Tool 查看；
- UI 可展示 Location；
- Context 只保留索引和 Preview。

---

## 51. MCP Tool 的执行结果

`MCPOutput` 包含：

- Tool Name；
- Server Name；
- Okay/Error Payload；
- Reconnect Attempted；
- Auth Retry Attempted；
- Is Timeout；
- Is Error；
- Extracted Images。

### 51.1 为什么 MCP Error 常是 `Ok`

MCP Protocol Call 本身可能成功返回：

```text
isError = true
```

Transport 没坏，所以保留为结构化 Output。

### 51.2 图片必须在截断前收割

MCP 文本转换可能把图片替换成 Placeholder。

`extracted_images` 保存原始 Base64，Session 在 PostToolUse 序列化前 `take` 出来。

---

## 52. `DrainedToolSuccess` 防止图片收割被漏掉

构造：

```rust
DrainedToolSuccess::new(tool_result)
```

会立即从：

- ReadFile FileContent；
- MCP Output；

取走 Pre-truncation Images。

之后：

- Hook 看见的是不含巨大 Base64 的结构化 Output；
- ACP Wire 不重复携带；
- Session 单独规范化并注入 Vision Message。

`#[must_use]` 提醒调用方不能随便构造后丢弃。

---

## 53. Image/PDF 回填

### 53.1 Read Image

Session 检查：

- 数据是否太小；
- 是否可读；
- 是否适合 Inline Attach。

成功时：

- Tool Result Text 只说读取了哪个文件；
- 图片作为 `ContentPart::Image` 随同 Tool Result。

### 53.2 Read PDF

PDF Tool 可渲染多页 JPEG：

- 每页变成 Image Content Part；
- Text 记录已渲染页数和总页数。

### 53.3 Tool Text 中嵌入的 Base64

Session 会提取：

- 从 Prompt Text 删除大 Base64；
- 图片归一化；
- 过大/损坏时生成 Drop Notice；
- 作为 Deferred User Image Message 加入下一轮。

Text-only Harness 则保留兼容行为，不注入 Vision。

---

## 54. Post-flight：成功结果不只是 Push Tool Result

`handle_bridge_tool_success` 还会：

- 消费已经完成的 Task Reminder；
- 记录新 Background Task ID；
- 检测 Branch 变化；
- 记录 Bare Echo；
- 识别 PR 创建；
- 转成 ACP ToolCallUpdate；
- 转成 ACP Plan Update；
- 重写远端路径；
- Harvest 图片；
- 追加粘连 JSON Reminder；
- 更新 Search Tool Prompt Index；
- 应用 Pending Skill Update；
- 触发 PostToolUse Hook；
- 记录 Tool Success/Failure Signal。

所以 Post-flight 是“把执行事实投影到整个 Session”。

---

## 55. UI Status 与 Runtime Result

`acp_tool_update` 根据结构化 `ToolOutput` 生成：

- Completed；
- Failed；
- Title；
- Content；
- Location；
- Raw Output。

注意：

```text
Result<ToolRunResult, ToolError> == Ok
```

不保证 UI Status 是 Completed。

如果 `output.is_error()`：

- UI 可标记 Failed；
- Span Success=false；
- 但模型仍得到结构化 Tool Result。

---

## 56. Hard Error 的统一回填

`handle_tool_error`：

1. 记录 Requested/Effective Tool Name；
2. 记录 Model 与 Session；
3. 记录 Failure Signal；
4. 重写远端 Path；
5. 生成：

```text
Tool <name> failed: <detail>
```

Meta-tool 则显示：

```text
Tool <target> failed via <dispatcher>: <detail>
```

6. 发送 Failed ACP Update；
7. Raw Output 写 Typed Error Envelope；
8. 同样 Push Tool Result 给模型。

失败不是只给用户看的 Toast；模型必须知道失败才能修正。

---

## 57. PostToolUse 与 PostToolUseFailure 不会混发

成功 Dispatch：

- 可触发 `PostToolUse`；
- Payload 包含结构化 Tool Result。

Hard Error：

- 只触发 `PostToolUseFailure`；
- Payload 包含 Error Text。

Hook Payload 会先经过：

```text
truncate_payload
```

同时标记：

- Input 是否截断；
- Result 是否截断。

Hook 不能因为一个巨大 Tool Output 占满内存或 IPC。

---

## 58. Tool Result 必须用原始 Call ID

模型响应中的：

```text
tool_use.id
```

最终必须对应：

```text
tool_result.tool_call_id
```

即使：

- Tool 被重命名；
- `use_tool` 执行了另一个 Target；
- ACP ID 被重新包装；
- 并发完成顺序不同；

Conversation 回填仍使用原始 `call_id`。

否则 Provider 会认为：

- Tool Use 没有 Result；
- 或 Result 对应不存在的 Call；
- 下一次 Sampling 请求非法。

---

## 59. Deferred Followup 为什么在整批之后加入

图片、Skill Reminder、用户 Followup 等可能生成额外 Conversation Item。

`execute_tool_calls` 先完成批次 Tool Result，再统一 Push Deferred Followup。

这保持：

```text
Assistant: tool_use A, tool_use B
Tool: result A
Tool: result B
User/System followup
```

而不是把额外消息插入尚未闭合的 Tool Use/Result 对之间。

---

## 60. Interjection 与 Skill Reminder 的尾部 Drain

整批完成后：

1. Push Deferred Followup；
2. Drain Pending Interjections；
3. Flush Pending Skill Reminders；
4. 决定 `ToolLoop`。

这样真人新消息不会丢失，也不会插入单个 Tool 的内部 Post-flight。

---

## 61. Observability

每个调用创建 `tool.execution` Span：

- Session ID；
- Tool Name；
- Tool Call ID；
- Retry；
- Input Size；
- Success；
- Outcome；
- Result Size。

结果完成时一次性 `record` Outcome。

### 61.1 为什么 Span 字段预声明 Empty

Tracing 对未声明字段调用 `record` 会静默丢弃。

所以创建 Span 时先声明：

```text
success = Empty
outcome = Empty
tool_result_size_bytes = Empty
```

### 61.2 Duration

每次 Dispatch 记录 Wall Time。

Managed MCP Retry 时：

- 再建 Retry Span；
- Duration 累加；
- Event 能展示完整用户等待。

---

## 62. 常见误解

### 62.1 “Permission Allow 后就直接调用 Tool”

错。还要构造 Prepared Call、批处理、Context、Registry Dispatch、Output Conversion。

### 62.2 “同批 Tool 一定按模型顺序完成”

错。Prepare 有序，Dispatch 并发，结果按完成顺序处理。

### 62.3 “所有 Read 都可并发”

错。同批若该路径有 Write，Read 也进入路径锁。

### 62.4 “Streaming Tool 的 Progress 一定到 Agent UI”

错。Runtime 支持；当前普通 Session Terminal-only 路径会丢弃 Progress。Bash Notification 和 Direct Bash 有独立流。

### 62.5 “Tool Future 返回 Ok 就是成功”

错。`ToolOutput::is_error` 可能仍为 true。

### 62.6 “Timeout 后只杀 Shell PID”

错。Runtime 尝试终止整个 Process Group，并对 Immediate Child 兜底。

### 62.7 “日志文件永远完整”

错。它比 Prompt Preview 更完整，但仍受 5 GiB 运行上限和 64 MiB 保留上限。

### 62.8 “后台命令加个 & 就行”

不可靠。Typed Background 才能被 Task Manager 查询、等待和回收。

### 62.9 “use_tool 返回的是 use_tool 的名字”

Requested Name 仍保留，但 Effective Name 是真实 MCP Tool，用于 Hook、错误和归因。

### 62.10 “图片就是一大段 Tool Result 文本”

错。Session 在截断前 Harvest，独立规范化成 Vision Content。

---

## 63. 完整案例：同批 Edit + Read

模型输出：

```text
1. search_replace(file_path="src/a.rs")
2. read_file(target_file="src/a.rs")
3. read_file(target_file="README.md")
```

执行：

1. 三个调用依次 Prepare；
2. Edit 与两个 Read 都完成 Permission；
3. `write_paths = {"src/a.rs"}`；
4. Edit 与第一个 Read 取得同一个 Mutex；
5. README Read 不加锁；
6. README Read 可与 Edit 并行；
7. `a.rs` Read 等 Edit 释放锁；
8. 三个结果按实际完成顺序更新 UI；
9. 每个按 Call ID 回填；
10. 下一轮模型看到一致的 Tool Result 集。

---

## 64. 完整案例：长命令自动后台化

模型调用：

```text
run_terminal_cmd(command="long-test", timeout=600)
```

假设启用 Auto-background：

1. Bash Tool Clamp Timeout；
2. 构造 Output File；
3. Terminal Actor Spawn Process Group；
4. 前 15 秒阻塞当前 Tool；
5. 持续写 File、Ring 与 Chunk；
6. 15 秒到，Process 仍运行；
7. 标记 Backgrounded，不 Kill；
8. 返回 `BackgroundTaskStarted`；
9. Agent 可做别的工作；
10. 后续调用 Task Output；
11. Task 完成后 Snapshot 保存 Exit Code 与完整路径；
12. 若 10 小时仍未完成，Actor Kill，Signal=`max_runtime`。

---

## 65. 完整案例：MCP 图片与 Auth Retry

模型调用：

```text
use_tool(tool_name="browser__screenshot", ...)
```

1. Prepare Parse Meta-tool；
2. Hook/Permission 使用 `browser__screenshot`；
3. Outer Dispatch 执行 `use_tool`；
4. Context 提供 Inner Dispatch；
5. Inner `call_raw` 调用 MCP Tool；
6. 若 Auth Reject，Managed Reauth 后重试；
7. MCP 返回 Text Placeholder + Extracted Image；
8. Outer `finalize_output` 只运行一次；
9. `DrainedToolSuccess` 在 Hook 序列化前取走 Base64；
10. ACP 收到简洁结构化 Output；
11. Session 规范化图片；
12. Conversation 得到 Tool Text 与 Vision Followup；
13. 下一轮模型真正“看见”截图。

---

## 66. 完整案例：Tool Stream 忘记 Terminal

某自定义 Tool 的 `execute`：

```text
Progress("half")
<stream ends>
```

Runtime：

1. Progress 可以传给 Streaming Consumer；
2. Stream End；
3. `FinalizedToolset` 发现没有 Terminal；
4. 生成 `stream_no_terminal`；
5. Session 走 Hard Error；
6. UI Status Failed；
7. 模型收到 Tool Failed Result；
8. 不会把“最后一个 Progress”误当成功。

---

## 67. 测试不变量

### 67.1 Runtime

- Blocking `run` 自动包成一个 Terminal；
- Progress 顺序不变；
- Terminal 只出现一次；
- 无 Terminal 返回 Typed Error；
- Typed Output 保留 Model Content 与 Chat Frame；
- Context Extension 可 Clone/Downcast。

### 67.2 Registry

- 参数 Rename 可 Reverse；
- Context 注入 CWD/Version/Viewer；
- Streaming 与 Non-streaming Terminal Prompt 一致；
- Inner Dispatch 不重复 Reminder；
- Effective Tool Name 保留。

### 67.3 Session

- Fast Tool 可早于 Slow Sibling 完成；
- Permission Reject 取消后续未执行 Call；
- 同文件写入有序；
- Wait 被 Interjection 中断；
- Hook Success/Failure 互斥；
- Call ID 始终配对。

### 67.4 Terminal

- Timeout Kill；
- Background Start；
- Background Operator 识别；
- UTF-8 截断；
- 截断后 Delta 继续；
- Output File Cap；
- Process Scope Kill；
- PID Reuse 防护；
- Owner-scoped Kill；
- Background Max Runtime。

### 67.5 Artifact 与 Rich Output

- Web Fetch SSRF 每跳校验；
- Artifact 不覆盖；
- 总 Budget；
- MCP/Read Images 在序列化前 Drain；
- Text-only 与 Vision Harness 分流。

---

## 68. 推荐阅读顺序

第一轮，理解主链：

1. `PreparedToolCall`；
2. `execute_tool_calls_batch`；
3. `dispatch_tool`；
4. `WorkspaceOps::call_tool`；
5. `FinalizedToolset::call`；
6. `finalize_output`；
7. `handle_bridge_tool_success`。

第二轮，理解 Runtime：

1. `xai-tool-runtime/tool.rs`；
2. `dispatch.rs`；
3. `context.rs`；
4. `error.rs`；
5. `registry/types.rs::prepare_dispatch`；
6. `call_raw`。

第三轮，理解进程：

1. `computer/types.rs`；
2. `terminal.rs::ProcessState`；
3. `terminal.rs::LocalTerminalActor`；
4. `lifecycle.rs`；
5. Process Group/Scope；
6. Bash Tool 的 Timeout/Background Formatting。

第四轮，理解大结果：

1. `types/output.rs`；
2. `tool_layer_images.rs`；
3. Web Fetch Overflow/Artifact；
4. Base64 Extraction；
5. Image Normalize；
6. ACP Conversion。

---

## 69. 十条核心结论

1. `PreparedToolCall` 才是已通过全部前置 Gate 的执行对象。
2. 多 Tool 批次采用“串行 Prepare、并行 Dispatch”。
3. 同文件存在写入时，读写共享 Mutex 保住批内因果。
4. `Tool` 保持 Typed API，`ToolDyn/ToolDispatch` 解决动态路由。
5. Tool Stream 必须以唯一 Terminal 结束。
6. `ToolRunResult` 同时保留结构化 Output 与模型 Prompt 投影。
7. Runtime Error 和 Logical Output Error 必须分开。
8. Terminal Actor 管理的是进程树、任务、日志和生命周期，不只是一次 Command。
9. 大输出通过首尾 Preview + Artifact/File + Hint 控制上下文。
10. 任何路径最终都要用原始 Call ID 写回 Tool Result，Agentic Loop 才能继续。

---

## 70. Glossary

### ACP ToolCall

客户端时间线中的 Tool Call 表示，拥有 Pending/InProgress/Completed/Failed 状态。

### Artifact

Tool 把过大完整结果保存到磁盘后的可寻址文件，Prompt 只返回 Preview 与路径。

### Auth Retry

检测 Unauthorized 后刷新认证并重试 Tool Dispatch 的机制。

### Auto-background

前台等待超过较短预算后，不终止进程，而把它转成后台任务。

### Background Handle

后台任务启动后返回的 Task ID、Output File 和可选 PID。

### Background Operator

Shell 中的 `&` Job 操作符。若不受 Task Runtime 跟踪，可能产生孤儿进程。

### Behavior Version

Tool Contract 版本，通过 Typed Context 传给实现，用于兼容旧参数和行为。

### Blocking Tool

只实现 `run`、一次性返回 Output 的 Tool。Runtime 自动包装成 Terminal-only Stream。

### Blocking Wait Guard

记录 Session 当前有多少可被用户插话打断的等待 Tool。

### Call ID

模型生成的 Tool Use 标识，Tool Result 必须用它配对。

### Canonical Args

Tool 实现内部使用的标准参数名，外部重命名参数要先 Reverse-remap。

### Cancellation Token

Tool 可主动观察的协作式取消信号；Dispatcher 也可通过 Drop Future 强制取消等待。

### Cgroup

Linux 控制组，用于限制 Child Process 的内存等资源。

### Chat Completion Output

Tool Output 可选的前端友好响应帧，与模型 Prompt Text 和 JSON Value 分离。

### Completion Waiter

等待后台任务完成的 One-shot Sender 与 Deadline，保存在 Terminal Actor 中。

### Concatenated JSON

模型把多个 JSON Object 粘在一个 Tool Arguments 字符串中的错误形态。

### Content Block

Tool Progress/Output 的富内容单元：Text、Image 或 Resource。

### Context Extension

按 Rust Type 注入 `ToolCallContext` 的附加依赖，如 CWD、Cancellation、Resources。

### Cooperative Cancellation

Tool 自己读取 Cancellation Token 并优雅结束的取消方式。

### Deferred Followup

Tool Result 之外、在整个批次闭合后追加的 Conversation Item，如图片或 Skill Reminder。

### Dispatch

从 Tool Name、JSON Args 和 Context 路由到具体 Tool 实现并执行。

### Dispatch Parts

`prepare_dispatch` 生成的 Owned 执行材料：Handle、Context、Canonical Params、Converter 与 Effective Name。

### Drained Tool Success

已从 Tool Output 取走 Pre-truncation Image 的成功结果包装，防止大 Base64 进入 Hook/Wire。

### Dynamic Tool

运行时注册、编译期不一定有聚合 Enum Variant 的 Tool，例如 MCP。

### Effective Tool Name

真正执行的目标名；Meta-tool 调用时与模型请求名不同。

### Exit Status

进程终止结果，包括 Exit Code 与 Signal/Kill Reason。

### Finalized Toolset

完成 Tool 注册、重命名、Schema 和 Resource 注入后的会话级可执行工具集合。

### Foreground Block Budget

开启 Auto-background 时，一条命令最多阻塞当前 Turn 多久。

### FuturesUnordered

并发 Poll 一组 Future、按实际完成顺序产出结果的 Stream。

### Hard Error

`Err(ToolError)`，表示 Dispatch、协议、认证、网络或执行基础设施失败。

### Hook Payload Truncation

限制发送给 Hook 的 Input/Result 大小，并显式标记是否截断。

### Inner Dispatch

Meta-tool 在当前 Toolset 内调用真实目标 Tool 的受控二次路由。

### Interjection

Tool 执行期间用户发来的新消息，可中断 Wait Tool。

### Logical Error

Future 正常返回 Output，但 Output 表示 NotFound、非零 Exit 或 MCP isError。

### Local Registry

按稳定 Tool ID 保存类型擦除执行 Handle 的进程内注册表。

### Meta-tool

自身不完成最终业务，而是选择并调用另一个 Tool 的工具，例如 `use_tool`。

### Model Output

Tool 类型擦除时提取的模型富内容投影，不等同于 `prompt_text`。

### Notification Handle

Tool/Terminal 向 UI 或 Session 发送增量事件的通道句柄。

### Output Converter

把 Type-erased JSON Value 转回 Grok 聚合 `ToolOutput` 的函数。

### Output Delta

自上次通知后的新增 Terminal 输出，而非完整 Buffer。

### Output File

Terminal 持续写入的磁盘日志，用于查看被 Prompt 截断的更多输出。

### Path Lock

同批调用针对同一文件存在写入时共享的异步 Mutex。

### Pending Tool Call

已在 UI 注册、但尚未完成 Parse/Permission/Execution 的调用。

### Post-flight

Dispatch 后把 Output 投影到 UI、Hook、Conversation、Telemetry 和 Signal 的阶段。

### Pre-flight / Prepare

执行前的注册、解析、Mode、Hook、Permission 与 Metadata 阶段。

### Process Group

用于把 Shell Child 及其后代作为一个整体终止的 OS 进程分组。

### Process Scope

登记多个 Process Group 的回收范围，可在应用或 Session 退出时统一 Kill。

### Progress

Tool Stream 的中间事件，可以是 Text、Rich Content 或 Custom Payload。

### Prompt Text

专门回填给模型的 Tool 结果文本，已经过格式化、截断提示和 Reminder 增强。

### Proxy Dispatch

通过 Workspace/Computer Hub 在另一个进程或主机执行 Tool。

### Raw Arguments

模型原始 Tool Arguments 字符串，保留给 Hook、诊断与恢复提示。

### Raw Output

结构化 Tool Update 中供客户端调试或专用渲染的底层结果。

### Reminder

根据 Tool Output 动态追加给模型的 System Hint，例如读取下一段或修正调用方式。

### Resource Persistence

Tool 完成后保存 Session Resources 的步骤。

### Reverse Params

从模型可见参数名映射回 Canonical 参数名的表。

### Ring Output

内存中保留的首部加最新尾部输出，用固定预算表示大日志。

### SSRF

Server-Side Request Forgery。Web Fetch 访问私网、Loopback 或内部地址的风险。

### Stream No Terminal

Tool Stream 结束但未提供最终 Terminal 的协议错误。

### Streaming Tool

自行实现 `execute`，可产生多个 Progress 后再产生 Terminal 的 Tool。

### Task Kind

后台 Process 的类型，例如 Bash 或 Monitor。

### Task Snapshot

后台任务在某一时刻的命令、状态、输出、Exit 与所有权快照。

### Terminal

Tool Stream 唯一的最终事件，携带 `Result<Output, ToolError>`。

### Terminal Actor

拥有 Process Map、Waiter、日志、Shell State 和 Kill 生命周期的 Actor。

### Tool

拥有 Typed Args、Typed Output、ID、Description 和执行方法的核心 Trait。

### Tool Call Context

一次调用的 Call ID 与 Typed Extension 集合。

### Tool Dispatch

Object-safe 的 JSON 动态调用接口。

### ToolDyn

把拥有关联类型的 `Tool` 擦除成动态 Registry Handle 的适配层。

### Tool Error

跨 Tool 生态统一的 Typed Hard Error。

### Tool Input

从 JSON 解析得到的 Grok 聚合输入枚举，用于 Permission 与展示。

### Tool Loop

Session 在处理完 Tool 后对 Agentic Loop 的控制结果，如 Continue、Cancelled、Followup。

### Tool Output

结构化执行结果，可表示成功或逻辑失败。

### Tool Result

写回模型 Conversation、与 Tool Use ID 配对的消息。

### Tool Run Result

同时携带结构化 Output、Prompt Text 和 Effective Tool Name 的最终包装。

### Tool Stream

零到多个 Progress 加唯一 Terminal 的异步 Stream。

### Tool Update

发送给客户端的 Tool 状态或内容增量。

### Truncation

按预算裁剪大输出，同时保留 Marker、总大小和恢复路径。

### Typed Extensions

按 `TypeId` 保存类型安全依赖的开放式 Context Store。

### Typed Tool Output

类型擦除边界后的 JSON Value、Model Content 和 Chat Completion Output 包装。

### Wire Name

模型请求中看到的 Tool 名，可能经过 Preset/Template 重命名。

### Workspace Ops

统一 Local Session Toolset 与 Proxy Harness 的 Workspace 调用接口。

---

## 71. 下一篇建议

下一篇可继续沿 Agent 基础主链深入：

> 源码精读 17：Hook Runtime 如何发现 Hook、匹配 Event、构造安全 Envelope、并发/串行执行 Command 与 Prompt Hook，并把 Deny、修改、超时、取消和审计接回 Agentic Loop。

它会专门展开本文只作为 Gate 使用的：

```text
PreToolUse
PostToolUse
PostToolUseFailure
PermissionDenied
Notification
Session/Turn Hooks
```
