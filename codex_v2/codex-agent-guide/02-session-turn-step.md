# 02. Session、Turn 与 Step

## 1. 三种生命周期

Codex 将运行时状态分为三个主要层次：

| 层次 | 生命周期 | 典型内容 |
|---|---|---|
| Session | 整条对话线程 | History、服务、当前配置、持久化句柄 |
| Turn | 一次用户任务 | 模型、权限、reasoning、动态工具、用户设置快照 |
| Step | 一次模型采样及其工具执行视图 | 环境 readiness、MCP binding、ToolRouter、AGENTS.md |

这种划分解决的是“一致性和刷新频率”问题，而不只是代码组织。

## 2. Session：线程级状态所有者

Session 会跨多个用户消息存在。它负责：

- 接收协议操作；
- 管理当前 active turn；
- 保存 conversation history；
- 保存线程配置和上一 Turn 设置；
- 管理 pending input；
- 向客户端发送事件；
- 向 rollout/store 持久化；
- 持有模型、MCP、Skill、Plugin、Hook 等共享服务。

重要原则是：**长期存在的可变状态应该有明确所有者。**如果每个 Tool Handler 都能任意修改历史、模型和环境配置，恢复和并发都会变得不可控。Codex 通过 Session 方法统一记录 conversation item、发送事件和更新状态。

## 3. Turn：一次用户意图的稳定解释

Turn 从用户提交任务开始，到最终回答、错误或中断结束。一个 Turn 内可能包含很多模型采样和工具调用。

[`TurnContext`](../codex-rs/core/src/session/turn_context.rs) 固定的内容包括：

- 本轮使用的模型与 `ModelInfo`；
- provider；
- reasoning effort、summary、verbosity、service tier；
- developer instructions、personality、collaboration mode；
- permission profile、approval policy、sandbox policy；
- 当前工作目录和执行环境选择；
-本轮 dynamic tools；
- output schema；
- turn id、source、telemetry 与 timing state。

为什么不在每次模型调用时重新读 Session 配置？因为用户可能在本轮运行中改变下一轮设置。若进行到一半突然切模型或改变权限，本轮前后行为就无法解释。TurnContext 把“这次任务采用什么配置”固定下来。

## 4. Step：一次请求的一致能力快照

一个 Turn 内，以下状态可能变化：

- MCP server 从 starting 变为 ready；
- Executor 环境准备完成；
- AGENTS.md 被工具修改；
- Plugin 或 App 的可用工具发生刷新；
- Tool search 加载了新的 Deferred Tool。

因此每次准备模型请求时，Session 会调用 `capture_step_context()`。关键流程可概括为：

```text
刷新环境 readiness
  → 刷新 AGENTS.md
  → 解析选中的 capability roots
  → 获取 Executor capability discovery
  → 获取当前 MCP binding
  → 准备 Tool recommendations
  → 构建 ToolRouter
  → 生成只读 StepContext
```

[`StepContext`](../codex-rs/core/src/session/step_context.rs) 持有：

- `turn: Arc<TurnContext>`；
- 环境快照；
- capability roots；
- Executor capability discovery；
- MCP binding；
- 本 Step 的 ToolRouter；
- 已加载的 AGENTS.md。

## 5. 为什么 ToolRouter 必须属于 Step

假设模型请求开始前工具列表是：

```text
read_file, exec_command, jira.search
```

模型收到这些 schema 后返回 `jira.search`。如果 Runtime 在等待响应期间重新连接 MCP 并用新 Registry 替换旧 Registry，而新列表里没有该工具，那么这次合法的模型调用将无法执行。

StepContext 的解决方案是：

```text
同一 Step 的 Prompt.tools ─┐
                           ├─ 来自同一个 ToolRouter
同一 Step 的 Tool execution ┘
```

模型看到什么，Runtime 就使用同一份 Router 执行什么。

## 6. 第一 Step 与后续 Step

`run_turn()` 首次进入时会提前捕获 `first_step_context`，因为首次请求还要完成：

- Skill 与 Plugin 依赖解析；
- 初始 World State 记录；
- 用户输入和注入项进入 History；
- Session start hook。

之后每次需要 follow-up 时，主循环通常重新捕获 Step。如果用户在运行中追加 pending input，还会先分析这批输入要求哪些 MCP server，再捕获带 required server 的 Step。

## 7. Pending Input 与 Steer

用户可以在 Agent 正在运行时追加输入。Codex 不会任意时刻直接修改当前模型请求，而是把输入放入队列，在安全边界处理：

1. 当前 sampling 或 Tool 阶段继续到可中断点；
2. 主循环检查 input queue；
3. 通过 Hook 处理并记录新输入；
4. 必要时为新输入启动相关 MCP；
5. 下一次采样时，新输入已经进入 History。

这说明实时 steer 本质上仍然是**在下一次结构化采样边界追加上下文**，不是直接篡改正在生成的 token 流。

## 8. 生命周期与取消

`run_turn()` 接受 `CancellationToken`。向下调用时通常使用 child token：

- 模型 stream 可以被中断；
- Tool Runtime 可以观察取消；
- MCP 等待可以通过 `or_cancel` 提前返回；
- 某些工具即使取消也需要等待清理，Runtime 会声明相应行为。

取消后的关键要求是：

- 不把半个 Tool Result 当成成功结果写入 History；
- 已产生且应保留的 assistant/tool item仍按协议记录；
- 向客户端发出明确的 TurnAborted 或结束事件；
- Session 本身继续可用，用户可以开始下一 Turn。

## 9. 配置变化如何传播

配置变更通常遵守如下传播方式：

```text
Session configuration 更新
       ↓
下一 Turn 创建新的 TurnContext
       ↓
每个 Step 从新 TurnContext 和动态服务捕获快照
```

少数线程级变化需要在历史中留下模型可见增量，例如模型、personality、权限或工作目录变化。它们由 World State/Turn Context 更新系统处理，而不是静默改变 Runtime 行为。

## 10. 可复用设计原则

如果自己实现 Agent，可以使用更简单的名字，但建议保留三层语义：

```text
ConversationState   长期、可恢复
TaskConfig          一次用户任务稳定
RequestSnapshot     一次模型调用一致
```

判断某个字段放在哪层的方法：

- 跨用户消息需要保留吗？放 Session。
- 本轮开始后不应改变吗？放 Turn。
- 可能变化但一次请求内必须一致吗？放 Step。

下一章沿着 `run_turn()` 分析真正的 Agent Loop。

