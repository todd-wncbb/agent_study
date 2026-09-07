# 25：从零理解一个最小 Agent

## 本章目标

我们用一个教学版 Agent 重建最核心的循环：

```text
用户输入
  → 调用模型
  → 模型给最终文字，或请求工具
  → Runtime 执行工具
  → 工具结果写回 History
  → 再次调用模型
```

代码使用接近 Rust 的教学伪代码，重点是类型和控制流，不保证复制后直接编译。每增加一项现实需求，我们都会看到 Codex 为什么需要对应结构。

## 1. 最小数据模型

先定义 History 中可能出现的 item：

```rust
enum Item {
    UserMessage {
        text: String,
    },
    AssistantMessage {
        text: String,
    },
    ToolCall {
        call_id: String,
        name: String,
        arguments_json: String,
    },
    ToolResult {
        call_id: String,
        output: String,
        is_error: bool,
    },
}
```

这里最重要的不是字段名称，而是保留 `ToolCall` 与 `ToolResult` 的结构和 `call_id`。如果只保存拼接文字：

```text
模型要运行搜索。搜索结果是……
```

模型协议无法可靠知道哪个结果属于哪个调用，也难以支持并行 Tool Calls。

## 2. 模型返回两种主要决定

```rust
enum ModelDecision {
    FinalAnswer {
        text: String,
    },
    CallTool {
        call_id: String,
        name: String,
        arguments_json: String,
    },
}
```

真实 Responses API 还有 reasoning、多模态、多个输出 item、并行调用、compaction 等变体。教学版先只保留“回答”或“调用一个工具”。

## 3. 模型客户端接口

```rust
trait ModelClient {
    async fn sample(
        &self,
        instructions: &str,
        history: &[Item],
        tools: &[ToolSpec],
    ) -> Result<ModelDecision, ModelError>;
}
```

模型客户端只负责：

- 将结构化输入发送给模型；
- 消费流式或普通响应；
- 转换成内部 `ModelDecision`；
- 返回传输/协议错误。

它不应该直接执行工具，否则模型通信和外部副作用会混成一层。

## 4. Tool Spec 与 Tool Runtime 分开

```rust
struct ToolSpec {
    name: String,
    description: String,
    parameters_schema: JsonValue,
}

trait ToolHandler {
    fn spec(&self) -> ToolSpec;

    async fn execute(
        &self,
        arguments_json: &str,
        context: &ToolContext,
    ) -> Result<ToolOutput, ToolError>;
}
```

- `ToolSpec` 给模型看；
- `ToolHandler` 给 Runtime 用。

模型知道工具 schema，不代表它拥有 Handler 对象；Runtime 有 Handler，也不代表这项工具必须暴露给本次模型请求。

## 5. 最小 Registry

```rust
struct ToolRegistry {
    handlers: HashMap<String, Box<dyn ToolHandler>>,
}

impl ToolRegistry {
    fn specs(&self) -> Vec<ToolSpec> {
        self.handlers
            .values()
            .map(|handler| handler.spec())
            .collect()
    }

    async fn execute(
        &self,
        name: &str,
        arguments_json: &str,
        context: &ToolContext,
    ) -> Result<ToolOutput, ToolError> {
        let handler = self.handlers
            .get(name)
            .ok_or_else(|| ToolError::UnknownTool(name.to_string()))?;

        handler.execute(arguments_json, context).await
    }
}
```

这就是最小 Router/Registry：根据工具名找到实现并执行。

真实 Codex 还要解决：namespace、deferred exposure、MCP binding、动态工具、Code Mode、工具冲突和当前 Step 的不可变视图。

## 6. 第一个 Agent Loop

```rust
async fn run_turn(
    model: &dyn ModelClient,
    tools: &ToolRegistry,
    history: &mut Vec<Item>,
    user_text: String,
) -> Result<String, AgentError> {
    history.push(Item::UserMessage { text: user_text });

    loop {
        let decision = model
            .sample(
                BASE_INSTRUCTIONS,
                history,
                &tools.specs(),
            )
            .await?;

        match decision {
            ModelDecision::FinalAnswer { text } => {
                history.push(Item::AssistantMessage {
                    text: text.clone(),
                });
                return Ok(text);
            }

            ModelDecision::CallTool {
                call_id,
                name,
                arguments_json,
            } => {
                history.push(Item::ToolCall {
                    call_id: call_id.clone(),
                    name: name.clone(),
                    arguments_json: arguments_json.clone(),
                });

                let result = tools
                    .execute(&name, &arguments_json, &ToolContext::current())
                    .await;

                history.push(match result {
                    Ok(output) => Item::ToolResult {
                        call_id,
                        output: output.text,
                        is_error: false,
                    },
                    Err(error) => Item::ToolResult {
                        call_id,
                        output: error.for_model(),
                        is_error: true,
                    },
                });
            }
        }
    }
}
```

这段循环已经具备 Agent 的核心特征：模型不是一次性回答，而是可以观察工具结果后继续决定。

## 7. 用一个具体执行走一遍

用户输入：

```text
找到项目中定义 ToolRouter 的文件。
```

### 第一次 Sampling

模型返回：

```rust
CallTool {
    call_id: "call-1",
    name: "search_text",
    arguments_json: r#"{"query":"struct ToolRouter"}"#,
}
```

History 变为：

```text
UserMessage
ToolCall(call-1)
```

### Runtime 执行

Tool Result：

```text
codex-rs/core/src/tools/router.rs:...
```

History 变为：

```text
UserMessage
ToolCall(call-1)
ToolResult(call-1)
```

### 第二次 Sampling

模型看到结果后返回：

```rust
FinalAnswer {
    text: "ToolRouter 定义在 codex-rs/core/src/tools/router.rs。",
}
```

Turn 结束。

## 8. 这个最小实现的第一个问题：可能无限循环

模型可能反复调用同一个工具。加入基本预算：

```rust
struct TurnBudget {
    max_sampling_requests: u32,
    max_tool_calls: u32,
}
```

每轮检查计数，超过上限就返回明确错误。

但真实 Codex 不能只靠固定次数：还要考虑 token/window、session rollout budget、工具并发和不同模型重试预算。

## 9. 第二个问题：工具表可能在调用前后变化

教学版每轮执行 `tools.specs()`，然后稍后从同一个可变 Registry 查 Handler。如果期间 Plugin/MCP refresh 改了 Registry，模型看到的工具和实际执行的实现可能不一致。

解决思路是捕获 Step：

```rust
struct StepContext {
    tool_router: Arc<ToolRouter>,
    environment: EnvironmentSnapshot,
    permissions: PermissionProfile,
    world_state: WorldState,
}
```

```text
捕获 StepContext
  → 用 step.tool_router.specs() 构造 Prompt
  → 用同一个 step.tool_router 执行模型产生的 Tool Call
```

这就是 Codex `StepContext` 的核心价值。

## 10. 第三个问题：工具不能直接执行

教学版 Registry 直接调用 Handler，缺少安全编排。更合理的路径：

```rust
async fn execute_tool(
    call: ToolCall,
    step: &StepContext,
) -> ToolResult {
    validate_arguments(&call)?;
    let approval = evaluate_approval(&call, step.permissions)?;
    request_user_approval_if_needed(approval).await?;
    let sandbox = build_sandbox_policy(&call, step)?;
    run_handler_in_environment(call, sandbox, step.environment).await
}
```

模型只提出动作；Runtime 决定是否允许、在哪里执行、能访问什么。

## 11. 第四个问题：取消需要传遍异步工作

给 Turn 加 `CancellationToken`：

```rust
async fn run_turn(..., cancel: CancellationToken) {
    loop {
        tokio::select! {
            _ = cancel.cancelled() => {
                return Err(AgentError::Interrupted);
            }
            decision = model.sample(...) => {
                // 处理模型结果
            }
        }
    }
}
```

Tool Handler、approval wait、remote process 和 Hook 也需要观察同一取消链。只取消模型请求，却让 shell 永远后台运行，不算完整中断。

## 12. 第五个问题：网络错误与任务失败不同

模型请求需要 retry policy：

```rust
async fn sample_with_retry(...) -> Result<ModelDecision, ModelError> {
    for attempt in 0..=max_retries {
        match model.sample(...).await {
            Ok(value) => return Ok(value),
            Err(error) if error.is_retryable() && attempt < max_retries => {
                backoff(attempt, error.retry_delay()).await;
            }
            Err(error) => return Err(error),
        }
    }
    unreachable!()
}
```

但 Tool 写操作不能套用同一套盲重试，因为超时后副作用可能已经发生。

## 13. 第六个问题：History 会无限增长

加入有界输出与 Compaction：

```text
Tool Output 先按硬上限截断
History 接近 Context Window
  → 生成/构造压缩表示
  → 建立新的 Context Window baseline
  → 继续 Agent Loop
```

不能直接删除最旧一半，因为可能破坏：

- Tool Call/Result 配对；
- 当前目标和未完成计划；
- world-state diff baseline；
- 中断或恢复边界。

## 14. 第七个问题：进程退出后怎样继续

每次重要 item 和生命周期边界写入 Rollout：

```rust
trait RolloutWriter {
    async fn append(&self, item: RolloutItem) -> Result<(), PersistError>;
    async fn flush(&self) -> Result<(), PersistError>;
}
```

Resume 时：

```text
读取 Rollout
→ 重建合法 History
→ 创建新的 Session、ModelClient、MCP connections
→ 重新注入当前 World State
```

不能序列化旧进程的 socket、PID 或 `CancellationToken`，然后期望它们复活。

## 15. 第八个问题：UI 需要实时进度

Agent Loop 不应让 UI 读取内部锁。它发送 typed events：

```rust
enum Event {
    TurnStarted,
    ToolStarted { call_id: String, name: String },
    ToolOutputDelta { call_id: String, chunk: String },
    ToolCompleted { call_id: String, success: bool },
    MessageDelta { text: String },
    TurnCompleted,
    TurnAborted,
}
```

UI 根据 Event 构建展示；Rollout 根据持久 item 重建历史；Prompt 根据 History 构建模型输入。三种视图不要混成一个对象。

## 16. 第九个问题：多个客户端和远端执行

当 TUI、桌面 App 或 IDE 都要使用 Agent 时，需要 App-server typed API；当仓库位于远端，需要抽象：

```text
ExecBackend
ExecutorFileSystem
Environment selection
Remote sandbox enforcement
```

Agent Loop 不应直接调用本机 `std::fs`，否则远端 executor 会读错机器。

## 17. 第十个问题：能力越来越多

把所有能力继续塞进 `run_turn()` 会形成巨型函数。于是需要：

- Extension contributor 提供 Context、Tools 和生命周期逻辑；
- Hook 在受控节点运行外部检查；
- Skill 按需提供工作方法；
- MCP 连接外部工具；
- Code Mode 编排重复工具调用；
- Multi-Agent 将独立任务拆成独立 Thread。

这些不是为了“架构看起来高级”，而是每项现实需求都需要明确所有权和边界。

## 18. 从最小 Agent 到 Codex 的映射

| 教学版概念 | Codex 中的主要对应 |
|---|---|
| `Vec<Item>` | `ContextManager` / `ResponseItem` History |
| `run_turn()` loop | `core/src/session/turn.rs::run_turn` |
| `ModelClient::sample` | `ModelClientSession` + Responses stream |
| `ToolRegistry` | `ToolRouter` / Registry / Spec Plan |
| `ToolHandler` | `ToolExecutor` / `CoreToolRuntime` |
| `ToolContext` | `StepContext`、environment、permissions |
| `sample_with_retry` | `run_sampling_request` |
| `RolloutWriter` | Rollout recorder/writer |
| `Event` | `protocol::EventMsg` |
| 外部 API | App-server v2 |

映射不是一一同名，而是职责对应。

## 19. 一个更合理的最终骨架

```rust
async fn run_turn(
    session: Arc<Session>,
    turn: Arc<TurnContext>,
    cancel: CancellationToken,
) -> Result<Option<String>, AgentError> {
    session.record_user_input(&turn).await?;

    loop {
        cancel.check_cancelled()?;

        let step = session.capture_step_context(
            Arc::clone(&turn),
            &cancel,
        ).await?;

        session.record_world_state_diff_if_needed(&step).await?;

        let prompt = build_prompt(
            session.history_for_prompt(&turn).await?,
            step.tool_router.model_visible_specs(),
            session.base_instructions().await,
        );

        let outcome = run_sampling_request(
            &turn.model_client_session,
            prompt,
            &cancel,
        ).await?;

        match outcome {
            SamplingOutcome::FinalMessage(text) => {
                return Ok(Some(text));
            }
            SamplingOutcome::ToolCalls(calls) => {
                for call in calls {
                    let result = dispatch_with_policy(
                        call,
                        Arc::clone(&step),
                        &cancel,
                    ).await;
                    session.record_tool_result(&turn, result).await?;
                }
            }
            SamplingOutcome::NeedsCompaction => {
                session.compact(&turn, &cancel).await?;
            }
        }
    }
}
```

这仍然是教学骨架，但已经能看出 Codex 各层存在的原因。

## 20. 自己动手的三个练习

### 练习 A：并行 Tool Calls

让 `ModelDecision` 一次返回多个调用。回答：

- 哪些工具可以并行？
- 怎样保持 call ID 对应？
- 多个结果按完成顺序还是原始顺序写回？
- 任一调用失败是否取消其他调用？

### 练习 B：审批

给 Tool Handler 添加 `ApprovalRequirement`，实现：无需审批、询问用户、禁止三种结果。注意批准后仍要经过 Sandbox。

### 练习 C：恢复

设计最小 Rollout JSONL，并从中恢复 History。人为制造一个只有 Tool Call、没有 Tool Result 的中断边界，决定如何规范化。

## 本章词汇表

| 词语 | 直译 | 在最小 Agent 中的意思 |
|---|---|---|
| Agent loop | Agent 循环 | 模型决定、工具执行、结果回传不断重复的主循环 |
| Intermediate representation | 中间表示 | 位于外部 API 与 Runtime 之间、便于内部处理的数据结构 |
| Dispatch | 分派 | 把结构化调用送到正确 Runtime/Handler |
| Budget | 预算 | 对 sampling、工具、token 或重试设置的硬限制 |
| Invariant | 不变量 | 循环过程中始终必须保持的条件，如 call/result 配对 |
| Abstraction | 抽象 | 隐藏本地/远端等实现差异的统一接口 |
| Persistence | 持久化 | 把运行事实写入进程退出后仍存在的存储 |
| Pseudocode | 伪代码 | 用于解释设计、不保证可直接编译的代码 |

完整解释见[术语总表](glossary.md)。

## 读完后自测

1. 为什么 ToolSpec 与 ToolHandler 必须分开？
2. 最小 Registry 直接执行 Handler 缺少哪些安全层？
3. StepContext 解决了“看到的工具”和“执行的工具”之间什么问题？
4. 为什么 Resume 只能重建 Runtime 对象，而不能复活旧 socket/PID？
5. Event、Rollout 和 Prompt History 为什么不应使用同一个数据对象？

## 源码检查点

- `codex-rs/core/src/session/turn.rs::run_turn`；
- `codex-rs/core/src/client_common.rs::Prompt`；
- `codex-rs/core/src/client.rs::ModelClientSession`；
- `codex-rs/core/src/session/step_context.rs::StepContext`；
- `codex-rs/core/src/tools/router.rs::ToolRouter`；
- `codex-rs/core/src/tools/registry.rs`；
- `codex-rs/core/src/tasks/mod.rs`：Turn task 与取消；
- `codex-rs/core/src/context_manager/history.rs`；
- `codex-rs/protocol/src/protocol.rs::EventMsg`。
