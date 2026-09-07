# 附录：源码阅读与实现练习

这些练习按难度递增。建议每题先画出数据流，再定位代码；不要一开始就全文搜索某个 UI 文案。

## 第一组：建立导航能力

### 练习 1：追踪一次用户输入

从 TUI 的 `AppCommand::UserTurn` 开始，写出直到 `Prompt.input` 的函数链，并记录每一层数据类型发生了什么变化。

验收标准：至少解释 `Submission`、`Op::UserInput`、`TurnContext`、`StepContext`、`History` 和 `Prompt`。

参考：[第 15 章](../15-execution-walkthrough.md)。

### 练习 2：找出 ToolSpec 与 Executor 的分界

选择 `shell` 或 `apply_patch`，回答：

1. 模型看到的 JSON Schema 在哪里生成；
2. Tool Call 如何按名称路由；
3. Approval 在哪一层发生；
4. Result 如何变成 ResponseItem。

参考：[第 7 章](../07-tools.md) 与 [第 9 章](../09-tool-execution-and-sandbox.md)。

### 练习 3：解释配置来源

选择一个 Feature，列出它从 config file 到 `config.features.enabled(...)` 的路径。人为在 profile、CLI override 和 requirement 中设置冲突值，预测最终值与 origin。

参考：[第 16 章](../16-configuration-and-features.md)。

## 第二组：修改一个最小 Agent

### 练习 4：实现有界文件读取工具

在自己的最小 Agent 中实现 `read_file(path, line_offset, max_lines, max_tokens)`：

- 拒绝越出 workspace 的路径；
- 使用 1-based 行号；
- 返回 `truncated`；
- 结果与 call ID 配对；
- 错误作为结构化 Tool Result 返回模型。

不要让模型直接传任意 shell 命令来替代这个练习。

### 练习 5：加入 Approval

为写文件工具增加 `Allow / Deny / Ask` policy。要求等待 approval 时仍能处理 interrupt，并确保 denial 进入模型 History。

### 练习 6：实现增量 History

让 Agent 完成两次工具调用。打印每次模型请求的 input，验证旧消息没有被重新解释或改写，function call 与 output 一一配对。

参考：[最小 Agent](minimal-agent.md)。

## 第三组：上下文与性能

### 练习 7：工具输出截断

构造一个输出 100MB 的假工具，对比：

- 不截断；
- 字节截断；
- Token 近似截断；
- 完整输出落盘、只把摘要和路径交给模型。

记录请求体大小和模型能否继续完成任务。

### 练习 8：实现窗口状态

复刻 `ContextWindowTokenStatus` 的最小版本，支持 `Total` 和 `BodyAfterPrefix` 两种 scope。为 hard window、soft limit、fallback buffer 和 saturating subtraction 写测试。

### 练习 9：性能分段

给最小 Agent 增加以下计时：pre-sampling、sampling、tool blocking、between-sampling、TTFT、TTFM。用一个故意慢 2 秒的工具验证指标归因。

参考：[第 25 章](../25-token-budget-and-performance.md)。

## 第四组：扩展能力

### 练习 10：实现一个 Extension

做一个项目术语 Extension：

- Thread 启动时加载配置；
- 向 developer context 注入最多 500 Token 的索引；
- 提供 `search_terms` 和 `read_term` 工具；
- 配置更新后无需重启 Thread；
- 不修改 Core Agent Loop。

参考：[第 20 章](../20-hooks-and-extensions.md)。

### 练习 11：实现 Tool Search

假设系统有 1,000 个工具。只在初始 Prompt 暴露工具索引和搜索工具，模型选中工具后再加载完整 Schema。测量 Prompt Token 变化，并处理同名工具 namespace。

### 练习 12：模拟 MCP 动态变化

在两次 Step 之间增加或删除一个 MCP Tool，验证当前 Step 使用稳定快照，下一 Step 才看到新目录。

## 第五组：可靠性与恢复

### 练习 13：中断恢复

在 Tool 执行一半时发送 Interrupt。要求：

- 子进程被取消；
- Tool Call 有终止结果；
- Turn 发出 aborted/completed 边界事件；
- 下一 Turn 可继续；
- rollout 能解释发生了什么。

### 练习 14：幂等重试

模拟“写操作已完成，但响应在返回前断流”。设计 idempotency key 或读取后验证机制，避免重试产生两次副作用。

### 练习 15：Resume

持久化一个包含两次模型请求和一次工具调用的 Thread，重启进程后恢复。验证：

- History 顺序不变；
- 工作目录变化被作为新 world state 追加；
- 未完成 Tool Call 不会被误认为成功；
- 新 Turn 使用当前配置而不是盲目复制旧配置。

## 第六组：长期记忆

### 练习 16：两阶段记忆

实现简化版：

1. 从三个历史 Thread 并行提取严格 JSON；
2. 保存到 SQLite；
3. 用单一全局锁整合成 `MEMORY.md`；
4. Prompt 只注入短索引；
5. 模型通过 search/read 取细节。

额外测试一个 worker 崩溃后 lease 过期重领的场景。

参考：[第 23 章](../23-long-term-memory.md)。

## 第七组：综合设计题

### 练习 17：设计一个远程 Agent Runtime

要求 UI 和 Agent Core 在 macOS，Executor 在 Linux。请定义：

- 路径和 working directory 类型；
- 工具 capability handshake；
- 文件和命令在哪端执行；
- Approval 如何回传；
- 断线时哪些状态可恢复；
- Rollout 记录宿主路径还是执行器路径。

### 练习 18：写一份新增工具的设计评审

新工具允许部署到生产环境。评审必须覆盖：

- 输入 Schema；
- ToolSpec 暴露条件；
- 权限和 approval；
- secret 处理；
- 幂等性；
- timeout/cancel；
- 用户和模型可见错误；
- integration tests；
- telemetry；
- 是否应放入 Core、Extension、MCP 或 Plugin。

## 自我检查答案框架

完成每题后，用下面六句话复述：

1. 外部输入首先变成什么强类型？
2. 哪个对象拥有当前状态？
3. 哪个边界执行安全决策？
4. 哪个 ID 关联请求、事件和结果？
5. 失败或中断后留下什么持久事实？
6. 哪些数据有硬上限，在哪里实施？

