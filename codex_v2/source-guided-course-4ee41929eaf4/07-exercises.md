# 07：渐进式源码练习

不要直接查看“答案提示”。每题先用 `rg` 给出证据路径。

这些练习不是都要一次做完。建议先完成 Level 1 和练习 4；能用自己的话讲清 `thread → turn → step → tool follow-up` 后，再进入 Level 3。每题的产出最好只有一张小图或 5～10 句结论，不要抄大段源码。

遇到卡住时按这个顺序降级：先找符号定义，再找调用者，再找同名测试；仍看不懂，就回到对应讲解章节，而不是继续向下展开更多文件。

## Level 1：导航

### 练习 1：运行表面

找出 `codex` 无子命令、`codex exec`、`codex app-server` 分别进入哪个函数。解释为什么 CLI 入口不适合作为 agent loop 的入口。

### 练习 2：控制协议

在 `Op` 中找出：启动用户任务、打断任务、批准 exec、关闭 session 的 variant。再找出 `submission_loop()` 对应分支。

### 练习 3：事件投影

选择一个 `EventMsg::TurnStarted`，追踪它如何变成 app-server 的 `TurnStartedNotification`，再找 TUI 如何确定 notification 属于哪个 thread。

## Level 2：主链路

### 练习 4：两次模型请求

找一个先返回 function call、再返回 assistant message 的 core 集成测试。画出两个 outbound request 的 input 差异，并标出 `call_id`。

### 练习 5：Step 一致性

从 `run_turn()` 找出第一次和后续捕获 `StepContext` 的位置。解释 pending input 为空/非空时，为什么捕获路径不同。

### 练习 6：流提前关闭

找到“stream closed before response.completed”的错误。设计一个 mock SSE 测试：发送 created 和部分 item，但不发送 completed。预测用户会看到什么事件。

## Level 3：状态与恢复

### 练习 7：模型不支持图片

从 `for_prompt()` 的测试找到图片/音频 modality 处理。说明内部 history 与模型实际 input 为什么可能不同。

### 练习 8：增量请求失效

阅读 `get_incremental_items()`，列出至少三种必须回退完整请求的情况。思考修改 tool specs 是否属于其中一种，以及由哪个 property comparison 判断。

### 练习 9：Fork 中断边界

从 `thread_manager.rs` 搜索 `append_interrupted_boundary` 和 `fork_history_from_snapshot`。解释从一个未完成 turn 中 fork 时，为什么不能只截断 vector。

## Level 4：设计练习

### 练习 10：新增一个只读工具

不写代码，先列设计清单：

- ToolSpec 放在哪里；
- runtime 如何注册；
- payload/output 类型；
- 是否允许并行；
- 输出硬上限；
- approval/sandbox 需求；
- core 集成测试的两段 SSE；
- app-server 是否需要新增 API。

### 练习 11：新增 v2 optional 字段

假设给 `turn/start` 添加一个可选字段。列出 Rust/TypeScript serde 约束、schema 生成、README 示例和测试命令。判断它是否需要 experimental gate。

### 练习 12：诊断“工具执行了但模型说没执行”

按边界提出至少五个互斥假设。例如输出未写 history、call ID 不匹配、`for_prompt()` 丢弃、增量基线错误、第二次 sampling 未发生。为每个假设写一条最小证据。

## 本章词汇表

| 词语 | 直译 | 做练习时的意思 |
|---|---|---|
| Navigate | 导航 | 用符号、调用者和测试快速定位源码 |
| Trace | 追踪 | 沿数据或控制流逐层查找去向 |
| Entry point | 入口点 | 某条执行路径最先进入的函数或协议方法 |
| Variant | 变体 | Rust `enum` 中一个可能的具体情况 |
| Invariant | 不变量 | 执行过程中始终必须保持成立的条件 |
| Hypothesis | 假设 | 尚未证实、可被具体证据推翻的解释 |

完整解释见[术语总表](glossary.md)。

## 答案提示

答案不提供完整代码，只给验证入口：

- 练习 1：`cli/src/main.rs::cli_main()`。
- 练习 3：`app-server/src/bespoke_event_handling.rs` 与 `tui/src/app/app_server_event_targets.rs`。
- 练习 4：`core/tests/suite/tools.rs`、`client.rs`，以及 `core_test_support::responses` helpers。
- 练习 5：`session/turn.rs::run_turn()` 中 `first_step_context` 和 loop 内 `next_step_context`。
- 练习 7：`context_manager/history_tests.rs` 中 `for_prompt_*media*` 测试。
- 练习 9：`core/src/thread_manager.rs` 与 `core/tests/suite/fork_thread.rs`。
- 练习 11：仓库根 `AGENTS.md` 的 App-server API Development Best Practices。
