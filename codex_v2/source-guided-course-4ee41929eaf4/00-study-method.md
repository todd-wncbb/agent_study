# 00：怎样学习一个快速演进的 Agent 代码库

这篇不是要求你记住一套术语，而是提供一种“遇到看不懂的源码时怎么拆”的方法。初学时每次只追一个问题，例如：“工具执行结果怎样回到模型？”不要试图一次理解整个仓库。

## 1. 先追踪所有权，再追踪调用

阅读异步系统时，最容易陷入“函数 A 调函数 B”的细节，却不知道状态由谁拥有。对每个概念先问四个问题：

1. 谁创建它？
2. 谁长期持有它？
3. 谁能修改它？
4. 取消、失败或恢复时，谁负责收尾？

例如，`run_turn()` 很重要，但它不是 thread 的所有者。它接收 `Arc<Session>`、`Arc<TurnContext>` 和取消令牌，在一次 turn 内循环。更长寿命的 thread 管理由 `ThreadManager` 和 session 层承担。

### 把四个问题用于一个具体对象

以一次工具调用结果为例：

1. Tool runtime 创建结果；
2. turn 循环接收并记录它；
3. context/history 层长期保存需要进入后续请求的表示；
4. 如果 turn 被取消，turn/session 层负责停止后续循环并收尾。

这样读源码时，你是在追踪一件东西的生命周期，而不是在函数名之间迷路。

## 2. 使用三类证据

### 定义证据

类型和函数签名回答“系统允许什么”：

- `protocol/src/protocol.rs` 中的 `Op` 和 `EventMsg`；
- `core/src/session/turn.rs` 中的 `run_turn()`；
- `core/src/tools/router.rs` 中的 `ToolRouter`。

### 调用证据

调用点回答“主要路径实际怎样走”。不要看到一个 public 方法就假设生产路径一定使用它。用 `rg` 找所有调用者，再从入口向下收窄。

### 测试证据

测试回答“维护者承诺哪些行为”。本仓库的 agent 行为常由 `codex-rs/core/tests/suite/` 集成测试锁定；TUI 的用户可见行为还会由 snapshot 覆盖。

一个实用顺序是：先读类型定义建立边界，再读一条主要调用路径，最后用测试确认你的理解。若三者冲突，通常是你把“允许存在的路径”误当成了“当前生产主路径”。

## 3. 区分五种容易混淆的东西

| 名称 | 它是什么 | 不是什么 |
|---|---|---|
| 协议类型 | 层与层之间允许传递的数据 | 某个 UI 的内部状态 |
| runtime 状态 | 当前进程为执行任务持有的对象 | 必然写入磁盘的记录 |
| conversation history | 发给模型前会规范化的上下文来源 | rollout 文件的逐字副本 |
| rollout / thread store | 恢复、索引或审计所需的持久状态 | 模型每次请求看到的完整内容 |
| UI projection | 将通知和 item 渲染成界面 | agent 的业务真相来源 |

## 4. 每篇笔记应回答的固定问题

- 入口是什么？
- 输入、输出和错误分别是什么？
- 状态由谁拥有？
- 哪些数据跨 turn，哪些只在 step 内有效？
- 哪些 feature/config 会改变路径？
- 最接近用户行为的测试在哪里？
- 如果修改它，最可能漏掉哪个相邻层？

## 5. 防止文档过时

文档顶部记录提交号，但正文尽量引用“文件 + 符号”，少依赖固定行号。更新仓库后，可以运行：

```bash
rg -n 'run_turn|submission_loop|build_prompt|try_run_sampling_request' \
  codex-rs/core/src/session
rg -n 'ThreadStart|TurnStart' codex-rs/app-server codex-rs/tui
```

如果符号消失，不要直接把链接改到相似名称；先重新追踪所有权和行为测试，确认概念是否被重构或删除。

## 本章词汇表

| 词语 | 直译 | 阅读源码时的意思 |
|---|---|---|
| Ownership | 所有权 | 哪个对象负责持有状态并完成清理；不仅指 Rust 编译器所有权 |
| Call site | 调用位置 | 实际调用某个函数的代码位置 |
| Signature | 签名 | 函数名、参数和返回类型共同描述的接口 |
| Evidence | 证据 | 能由类型、调用路径或测试直接支持的结论 |
| Projection | 投影 | 把内部状态转换成 UI 或协议所需视图 |
| Refactor | 重构 | 不以改变外部行为为目标的代码结构调整 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

1. 打开 `codex-rs/core/src/session/turn.rs`，只读 `run_turn()` 的函数注释和参数。
2. 写下每个参数的生命周期猜测。
3. 再阅读 `ThreadManager`、`SessionIo::submit()` 和 `submission_loop()`，修正猜测。

如果这三步还比较吃力，可以先跳到 [术语与贯穿案例](00-concepts-and-running-example.md)，再读 [一次完整 Turn](03-one-turn-walkthrough.md)，之后回来做检查点。
