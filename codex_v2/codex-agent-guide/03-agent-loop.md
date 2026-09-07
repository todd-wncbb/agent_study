# 03. Agent Loop：模型与工具如何循环

## 1. 核心入口

主循环位于 [`core/src/session/turn.rs`](../codex-rs/core/src/session/turn.rs) 的 `run_turn()`。源码注释已经给出了最基本契约：

- 模型可能返回 function call；
- 也可能返回 assistant message；
- function call 会被执行，其输出放入下一次采样；
- 只有 assistant message 且没有后续工作时，Turn 才完成。

真实实现还加入了 compaction、pending input、Hook、流式事件、并行工具和错误恢复。

## 2. 高层伪代码

下面的伪代码保留了真实控制结构，省略了 UI 事件细节：

```rust
async fn run_turn(session, turn, user_input, cancel) {
    maybe_compact_before_turn().await?;
    let required_mcp = inspect_input_dependencies(&user_input).await?;
    let first_step = capture_step(required_mcp).await?;

    let mut world_state = record_initial_world_state(&first_step).await?;
    let injections = load_mentioned_skills_and_plugins(&user_input).await?;
    record_user_input_and_injections(injections).await?;

    let mut next_step = Some(first_step);
    loop {
        record_pending_user_input().await?;

        let step = match next_step.take() {
            Some(step) => step,
            None => capture_step_for_current_state().await?,
        };

        world_state = record_world_state_diff(world_state, &step).await?;
        let history = session.history.for_prompt();
        let result = run_sampling_request(&step, history, cancel.child_token()).await?;

        if result.needs_follow_up || session.has_pending_input() {
            maybe_compact_if_needed().await?;
            continue;
        }

        if stop_hook_requests_continuation() {
            continue;
        }
        break;
    }
}
```

## 3. 阶段一：采样前压缩

`run_pre_sampling_compact()` 在新用户输入正式进入当前采样前检查旧历史。这样做是为了避免：

- 旧历史已经接近窗口上限；
- 再加入完整 World State、Skill 正文和用户消息后直接超过限制；
- 第一次请求根本无法发出。

压缩失败不总是等价处理：用户中断会传播 `TurnAborted`；其他失败会发出错误生命周期事件，并让本 Turn 停止而不摧毁 Session。

## 4. 阶段二：从输入推导能力依赖

Runtime 在构建工具前会查看用户输入，识别：

- 明确提到的 Plugin；
- App/Connector mention；
- Skill 依赖的 MCP server；
- 本轮必须等待启动的 server。

这一步说明 Tool availability 不只是静态配置。用户本轮选择的 Skill 或 App 可能要求 Runtime 提前激活相关 MCP。

## 5. 阶段三：捕获第一 Step

`capture_step_context_with_required_mcp_servers()` 会同时准备：

- 环境和 AGENTS.md；
- MCP runtime；
- Tool recommendations；
- ToolRegistry 与模型可见 ToolSpec。

第一 Step 随后被用于两件相互关联的工作：

1. 生成模型可见 World State；
2. 构建首次请求使用的工具集合。

从同一快照生成两者，避免上下文说“你可以使用某环境”，而工具实际却属于另一个环境状态。

## 6. 阶段四：注入 Skill、Plugin 和用户输入

`build_skills_and_plugins()` 处理当前输入中明确选中的能力。其结果不是直接附加到某个字符串，而是转换成 `ResponseItem`，再通过 Session 的统一记录方法：

- 加入 conversation history；
- 持久化到 rollout；
- 向客户端发送 raw response item 事件。

用户输入也通过 `run_hooks_and_record_inputs()` 进入同一条历史。这让后续恢复时可以按事件顺序重建模型输入。

## 7. 阶段五：准备每次 Sampling Request

每次循环都会执行：

```text
检查 pending input
  → 记录时间/token reminder
  → 捕获或复用 StepContext
  → 计算 World State diff
  → clone History
  → ContextManager::for_prompt()
  → build_prompt()
  → ModelClientSession::stream()
```

`build_prompt()` 本身很薄：

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

复杂工作已经在前面完成：History 决定 input；ToolRouter 决定 tools；Session/ModelInfo 决定 instructions 和采样能力。

## 8. 阶段六：消费流式事件

`try_run_sampling_request()` 从 `ResponseStream` 连续读取事件。重要事件包括：

- `Created`：服务端创建响应；
- `OutputItemAdded`：一个 assistant、reasoning 或 tool item 开始；
- 文本与 reasoning delta：向 UI 流式展示；
- `OutputItemDone`：一个完整 item 可以进入业务处理；
- completed/token usage 等终止信息。

如果 stream 在收到正常完成事件前关闭，Runtime 会返回 Stream error，而不是把半截输出当成成功。

## 9. OutputItemDone：循环的分叉点

完整 item 交给 `handle_output_item_done()`。处理结果中最关键的两个字段是：

- `tool_future`：如果这是 Tool Call，包含异步执行 future；
- `needs_follow_up`：是否需要把新结果再发给模型。

普通 assistant message 会被记录并可能成为 `last_agent_message`。Tool Call 则会：

1. 转换成内部 `ToolCall`；
2. 由本 Step 的 ToolRouter 找到 Runtime；
3. 创建执行 future；
4. future 完成后生成 `ResponseInputItem`；
5. 写入 History；
6. 把 `needs_follow_up` 设为真。

## 10. 并行 Tool Call

Runtime 使用 `FuturesOrdered` 保存 in-flight Tool Future。这里有两个重要语义：

- 工具可以并发开始，以降低总延迟；
- 结果按照稳定顺序 drain 和写入 History，避免每次运行因完成时序不同而产生完全不同的上下文。

并不是所有工具都允许并行。Tool Runtime 可以声明自己的并行能力；具有全局副作用或交互式状态的工具通常需要更谨慎。

## 11. Follow-up 的判断

采样结束后，外层循环综合判断：

```text
needs_follow_up =
    model_needs_follow_up
    OR has_pending_user_input
```

典型需要 follow-up 的情况：

- 执行了 Tool Call，需要把结果给模型；
- Tool Search 新增了工具定义；
- 用户在运行中追加了输入；
- Stop Hook 阻止结束并添加了继续执行的提示；
- 多 Agent mailbox 到达新消息。

## 12. Turn 如何结束

当不需要 follow-up 时，并不立刻退出。Runtime 还会运行 Turn stop hooks：

- Hook 可以允许结束；
- 可以要求继续，并提供新的上下文片段；
- 可以要求停止；
- 老的 after-agent hook 也可能处理最终消息。

最终 `run_turn()` 返回最后一条 assistant message。Session 仍然存在，等待下一次用户 Turn。

## 13. 重试不是简单重跑整个 Turn

`run_sampling_request()` 对可重试的流错误进行有限重试。重试时会重新从当前 History 构建 Prompt，但不会重新执行已经成功并记录的工具。`executed_tool_calls` 机制还可把待附加的执行记录加入请求，避免丢失已发生动作的语义。

这条原则非常关键：

> 任何具有副作用的动作都不能因为网络重试而被无条件重复执行。

模型请求可重试，Tool 副作用必须具备单独的幂等或提交语义。

## 14. 中途压缩

采样后 Runtime 检查 token 状态。如果仍需 follow-up 且达到阈值，就在 Turn 中途 compact：

1. 用当前 History 生成压缩表示；
2. 安装新的 replacement history；
3. 重建 World State baseline；
4. 保留继续执行所需的最后用户意图与工具关系；
5. 下一轮采样继续，而不是结束用户任务。

因此 compaction 是 Agent Loop 的一部分，不是离线维护任务。

## 15. 一个具体例子

用户输入：

> 找到登录失败的原因，修复后运行相关测试。

可能产生以下循环：

```text
Sampling 1
  模型：调用 rg 搜索登录代码
  Runtime：执行 exec_command，记录输出

Sampling 2
  模型：读取相关文件
  Runtime：执行命令，记录文件片段

Sampling 3
  模型：调用 apply_patch
  Runtime：检查写权限，应用补丁，记录结果

Sampling 4
  模型：运行目标测试
  Runtime：执行测试，记录退出码和输出

Sampling 5
  模型：输出修复总结
  Runtime：没有 Tool Call、没有 pending input，Turn 完成
```

对用户而言这是一轮对话；对模型服务而言是五次请求；对 Tool Runtime 而言是四次受策略控制的动作。

## 16. 自己实现时必须有的保护

最小 Agent Loop 也应具备：

- 最大模型采样次数或其他资源预算；
- cancellation；
- Tool 参数校验；
- Tool Call 与 Result 的稳定关联 ID；
- 副作用工具的重复执行保护；
- stream 未正常结束的错误判断；
- History 写入的原子顺序；
- 上下文超限处理；
- 明确的完成条件。

后续两章将展开 Agent Loop 每次采样所使用的 Prompt 和 World State。

