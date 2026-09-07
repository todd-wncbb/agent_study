# 22：错误、取消与恢复

## 为什么不能把所有失败都叫“报错”

用户看到一句“失败”，背后可能是完全不同的事情：

- 命令正常运行，但测试返回退出码 1；
- 工具参数无法解析；
- 用户拒绝了审批；
- Sandbox 拒绝文件访问；
- 模型 stream 中途断开；
- Context window 已满；
- 用户主动中断 Turn；
- Runtime 自身出现内部错误。

这些情况由不同层处理，也有不同的重试和恢复策略。错误分类不是为了写更漂亮的 enum，而是为了防止重复副作用、错误重试和误导用户。

## 1. 先判断失败属于哪一层

| 层 | 例子 | 通常由谁处理 |
|---|---|---|
| 用户任务结果 | 测试断言失败、搜索无匹配 | 模型阅读 Tool Result 后决定下一步 |
| Tool 输入/路由 | 参数 JSON 错误、工具不存在 | Tool Router 返回结构化错误 |
| Policy/approval | 用户拒绝、策略禁止 | Runtime 记录明确决定，模型可调整方案 |
| Execution/sandbox | 超时、signal、denial | Tool Orchestrator 分类并返回有限输出 |
| Model transport | 断线、stream 未完成 | Model client 按 retry budget 处理 |
| Capacity/auth | 使用额度、认证或服务过载 | 停止、恢复认证或提示用户，依类别而定 |
| Agent lifecycle | interrupt、replace、shutdown | Session 取消任务并发终止事件 |
| Internal defect | task panic、状态不变量破坏 | 记录诊断并安全结束，不能伪装成模型判断 |

## 2. 非零退出码不一定是 Runtime 错误

运行 `rg` 没找到内容可能返回非零；测试失败也会返回非零。这通常是命令执行成功、业务结果不满足条件。

工具应把退出码、stdout/stderr 的有界表示返回模型，让模型决定：

- 换搜索方式；
- 阅读失败日志；
- 修改代码；
- 向用户说明未找到。

如果把所有非零退出码都升级成 Turn 级致命错误，Agent 就无法根据失败结果自我修正。

## 3. Tool 错误怎样回到模型

例如模型调用不存在的工具或参数类型错误：

```text
Tool Call
  → Router 查找/解析失败
  → 产生与 call ID 对应的结构化 Tool Result
  → 写入 History
  → 模型获得一次修正机会
```

这里仍要保持 call/output 配对。不能因为是错误，就只向 UI 打一行日志而让模型完全不知道发生了什么。

## 4. 用户拒绝与 Sandbox Denial 不同

- **Approval rejected**：动作尚未获准尝试；
- **Sandbox denial**：动作已经进入执行路径，但实际资源访问被执行端阻止。

用户批准运行测试后，测试写 `/etc` 被 Sandbox 拒绝，不能告诉用户“你拒绝了命令”。反过来，用户拒绝审批时也不应该伪造一个操作系统 permission denied。

两种情况都可能让模型调整方案，但审计语义不同。

## 5. 超时最危险的地方：副作用可能已经发生

假设调用外部 API 创建工单，客户端等待超时。你不能根据“没有收到成功响应”推断“工单没有创建”。

因此对有副作用的动作：

- 尽量使用 idempotency key；
- 在错误中说明是否可能已产生副作用；
- 不要盲目自动重试；
- 可以先查询外部状态再决定是否补偿或重试。

Shell timeout 同样应保留已经捕获的有界输出，帮助判断进程做到哪一步。

## 6. Model Stream 为什么需要 `response.completed`

模型流可能已经发送部分文字或 Tool Call，随后连接断开。如果没有终止完成事件，Runtime 不能把部分输出当作完整回答。

`CodexErrorDetails::Stream` 表示握手后、完成前断开。它属于可能暂时恢复的错误，通常按配置的 stream retry budget 重试。重试前还要考虑：

- 部分输出是否已经对外展示；
- 是否已经执行了 Tool Call；
- previous response/incremental 状态是否仍可安全使用；
- 是否应发送 `StreamError` 进度通知。

## 7. 哪些错误可以重试

`CodexErr::is_retryable()` 对语义类别进行显式分类。直觉上：

### 常见可重试类别

- 暂时断线或 stream failure；
- request timeout；
- 部分 HTTP/server 内部错误；
- 某些 I/O 或异步 task failure。

### 常见不可直接重试类别

- Invalid request；
- Context window exceeded；
- Usage/quota limit；
- Tool collision；
- Sandbox 配置/拒绝错误；
- Unsupported operation；
- Thread not found；
- 已达到 retry limit。

“不可直接重试”不等于永远无法恢复。例如 Context window exceeded 可以由上层走 compaction；认证失败可以走 auth recovery；它只表示不要原样立即再发一次相同请求。

## 8. Retry 必须有预算和 Backoff

无限重试会：

- 消耗额度；
- 重复外部副作用；
- 长时间占用 Turn；
- 给服务增加压力；
- 让用户误以为系统卡死。

因此重试需要次数上限、可能的服务端建议延迟，以及逐步 backoff。超过预算后转换成明确的 `RetryLimit` 类错误，而不是继续循环。

## 9. Context Window 超限为什么不能原样重试

请求内容已经超过模型可接受窗口，再发送完全相同 payload 不会变好。需要改变输入：

- 自动/手动 compaction；
- 截断过大工具结果；
- 创建新的 context window；
- 必要时提示用户新建 thread 或缩小任务。

这属于“修改状态后再恢复”，不是网络层 Retry。

## 10. Interrupt 是受控取消

`Op::Interrupt` 请求终止当前任务。Session 通过 `CancellationToken` 通知 sampling、工具等待和其他异步工作停止。

取消通常是协作式的：任务在安全检查点观察 token 并退出。如果在短暂 grace period 内没有结束，Runtime 才会更强制地 abort task handle。

这样做比立即杀掉所有 future 更有机会完成：

- 释放 lease/并发槽；
- 清理 callback；
- 记录 interrupted boundary；
- flush rollout；
- 发出 `TurnAborted`。

## 11. Interrupt 不等于销毁 Thread

Turn 被中断后，Thread 通常仍然存在，可以继续提交新输入。为了让未来模型知道上一轮并非正常结束，Runtime 会按配置写入中断边界标记，并在终止事件前尽量持久化。

用户以后 resume 时，看到的是“上一 Turn 被中断”的历史事实，而不是一段假装正常完成的回答。

## 12. `TurnComplete`、`TurnAborted` 与 `Error`

- `TurnComplete`：Turn 生命周期正常走到终点；其中仍可能携带终端错误信息；
- `TurnAborted`：Turn 因 interrupt/replacement 等原因被取消；
- `Error` event：向客户端报告执行 submission 时发生的错误；
- `Warning`：Turn 仍可继续，但用户应知道某个异常或降级。

客户端不应只等待“任何一个 error 字符串”，而应按生命周期事件更新状态。

## 13. 一张恢复决策表

| 现象 | 首先做什么 | 不要做什么 |
|---|---|---|
| 测试失败 | 把日志交给模型分析 | 自动当作 Runtime 崩溃 |
| Stream 断开 | 检查 retryable + budget | 无限立即重试 |
| Context 满 | Compact/减少输入 | 原样重发 |
| 用户拒绝 | 记录决定并缩小方案 | 伪装成 Sandbox denial |
| 写操作超时 | 查询副作用是否已发生 | 无条件重复写操作 |
| Interrupt | 传播取消、清理、持久化边界 | 删除整个 Thread |
| Auth 失败 | 走受控凭据恢复或提示用户 | 在日志打印 token |

## 本章词汇表

| 词语 | 直译 | 在本章中的意思 |
|---|---|---|
| Error category | 错误类别 | 决定处理、展示和重试方式的语义分类 |
| Retryable | 可重试 | 原操作在条件不变或短暂恢复后可安全再次尝试 |
| Terminal | 终止性的 | 会结束当前生命周期的最终状态或错误 |
| Cancellation | 取消 | 通知异步工作尽快停止的受控流程 |
| Grace period | 宽限期 | 强制 abort 前等待任务自行清理的短时间 |
| Idempotent | 幂等 | 同一操作重复执行不会产生额外不同副作用 |
| Backoff | 退避 | 连续失败后延迟再次尝试 |
| Recovery | 恢复 | 改变认证、上下文或状态后重新进入可工作状态 |

完整解释见[术语总表](glossary.md)。

## 读完后自测

1. 为什么命令退出码 1 通常不应直接终止 Turn？
2. 写操作超时后，为什么不能假设它没有执行？
3. Context window exceeded 和 stream disconnect 的恢复方式有何不同？
4. Interrupt 为什么要先传播 CancellationToken，再强制 abort？

## 源码检查点

- `codex-rs/protocol/src/error.rs::CodexErrorDetails` 与 `is_retryable`；
- `codex-rs/core/src/client.rs::run_sampling_request`；
- `codex-rs/core/src/tasks/mod.rs`：abort、terminal events 与 rollout flush；
- `codex-rs/protocol/src/protocol.rs::TurnAbortedEvent`；
- `codex-rs/core/src/tools/orchestrator.rs`：工具重试/审批编排；
- `codex-rs/core/tests/suite/stream_no_completed.rs`；
- `codex-rs/core/tests/suite/abort_tasks.rs`；
- `codex-rs/core/tests/suite/approvals.rs`。
