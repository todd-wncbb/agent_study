# 14：第二阶段综合实验

这些实验强调跨层证据。建议每题保存一张调用图、一份测试定位和一段自己的结论。

这是第二阶段内容，不适合作为第一次阅读入口。判断自己是否准备好：不看文档时，已经能解释一次普通 turn、一次工具 follow-up，以及 approval 和 sandbox 的区别。否则先完成 [渐进式源码练习](07-exercises.md) 的 Level 1～2。

## 实验 1：Code Mode 嵌套工具闭环

目标：证明 JavaScript 没有绕过普通工具安全边界。

1. 在 `core/tests/suite/code_mode.rs` 找一个脚本调用 nested tool 的测试。
2. 标出模型可见 `exec` call、runtime delegate call、普通 tool output、最终 exec output。
3. 找到 `ToolCallSource::CodeMode` 的构造点。
4. 回答：approval、sandbox、tool telemetry 分别在哪一层仍然生效？

完成标准：能解释为什么直接在 V8 中实现 filesystem API 会破坏当前安全模型。

## 实验 2：Yielded Cell 生命周期

构造三种时序：

- 脚本自然完成；
- `yield_control()` 后由 wait 得到完成结果；
- yield 后被 terminate。

为每种时序列出 `RuntimeResponse`、cell 是否仍存在、model-visible 状态文本以及 callback cleanup 时机。

## 实验 3：父 Agent 给运行中子 Agent 发消息

追踪：

```text
send_message
  → InterAgentCommunication(trigger_turn=false)
  → Op::InterAgentCommunication
  → target input queue/mailbox
  → current turn 的 pending input drain
```

再与 `followup_task(trigger_turn=true)` 比较。回答目标空闲与运行中时各自会发生什么。

## 实验 4：Agent Fork 的历史卫生

从 `spawn_forked_thread()` 追踪 full history 与 last-N turns：

- 为什么先 materialize/flush rollout？
- 哪些 usage hint 或 parent-only instruction 被过滤？
- reference context item 何时保留？
- capability roots 怎样继承？

完成标准：能列出“直接 clone parent history vector”的至少四个错误。

## 实验 5：同一命令在本地和远端执行

选择一个简单 shell call，分别画出：

```text
Tool handler → ToolOrchestrator → LocalProcess → codex-sandboxing
Tool handler → ToolOrchestrator → RemoteProcess → exec-server sandbox
```

比较 cwd、permission profile、environment ID、sandbox type 和输出 sequence 分别在哪里产生。

## 实验 6：审批通过但仍然失败

设计两个案例：

1. 用户批准命令，但 sandbox 拒绝访问目标文件；
2. filesystem 允许，但 network proxy 拒绝连接。

说明 UI 应看到哪些 approval/exec/denial 事件，以及为什么不能把二者归类为“用户拒绝”。

## 实验 7：WebSocket 增量续接

从 `client_websockets.rs` 选择三个测试：

- prefix → 带 `previous_response_id`；
- non-prefix → 完整 create；
- request property changed → 完整 create。

将每个 request body 精简成 model、tools、input、previous_response_id 四列对照表。

## 实验 8：跨 Turn 连接复用但状态隔离

证明以下两句话能同时成立：

- WebSocket 物理连接可跨 turn 复用；
- `x-codex-turn-state` 不能跨 turn 复用。

查找 `ModelClientSession` 的创建、Drop/cache 行为和相关 tests。完成后画出 session client、turn client session、connection、turn state 四者的所有权图。

## 本章词汇表

| 词语 | 直译 | 做综合实验时的意思 |
|---|---|---|
| Closed loop | 闭环 | 从模型请求、工具执行到结果回到模型的完整链条 |
| Lifecycle | 生命周期 | 对象创建、挂起、恢复、完成和清理的全过程 |
| Timing | 时序 | 多个异步事件实际发生的先后关系 |
| State diagram | 状态图 | 展示对象可处状态及状态转换的图 |
| Ownership graph | 所有权图 | 展示哪些对象持有或共享其他对象的关系图 |
| Review criterion | 完成标准 | 用来判断实验是否真正证明结论的条件 |

完整解释见[术语总表](glossary.md)。

## 实验报告模板

```markdown
# 实验标题

## 问题
希望证明或推翻什么？

## 源码证据
文件、符号、关键分支。

## 测试证据
测试名、mock 输入、核心断言。

## 时序或状态图
只画与结论有关的节点。

## 结论
事实、推断、尚未验证项分别列出。

## 如果修改实现
需要同步修改的 crate、协议、测试和文档。
```
