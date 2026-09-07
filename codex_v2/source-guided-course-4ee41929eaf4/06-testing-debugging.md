# 06：测试与调试手册

调试这类系统的关键不是“多打印日志”，而是先找出：**最后一个已经确认正常的边界在哪里，按设计下一个应该出现的事件是什么。**

例如，用户说“工具明明执行了，模型却像没看见”。不要马上怀疑模型。先依次确认工具 output 是否生成、是否带正确 `call_id`、是否写入 history、第二次模型请求是否发出。每确认一层，就排除一整类原因。

## 1. 先选正确测试层

| 修改类型 | 首选测试 |
|---|---|
| 纯解析、规范化、无外部行为的小函数 | 独立 `*_tests.rs` 单元测试 |
| agent loop、prompt、tool follow-up | `core/tests/suite/` 集成测试 |
| app-server API | app-server public JSON-RPC 集成测试 |
| TUI 可见内容 | TUI 行为测试 + `insta` snapshot |
| 跨本地/远端执行环境 | 使用 remote-aware builder/helpers |

Agent 行为只写单元测试通常不够，因为真正容易出错的是“记录 history → 发请求 → 收 stream → 执行工具 → 再发请求”之间的组合。

## 2. Core 集成测试的标准读法

典型测试包含：

1. 用 `TestCodexBuilder::build_with_auto_env()` 建立实例；
2. 用 `responses::mount_sse_once()` 挂载模拟 Responses stream；
3. 提交 `Op::UserInput`；
4. 等待某个 protocol event；
5. 保留 `ResponseMock` 并检查 outbound `/responses` request；
6. 使用 `function_call_output(call_id)` 等结构化 helper，而不是手工深挖 JSON。

阅读测试时，把每个 SSE constructor 映射到 `try_run_sampling_request()` 的一个 match 分支。

### 一个最小的两次请求测试

假设第一次模拟响应返回 `function_call(call-7)`，Codex 执行工具后，第二次模拟响应返回普通文本。测试至少要证明：

1. 工具确实被调用；
2. 第二个 outbound request 中包含 `call-7` 对应的工具结果；
3. 用户最终收到完成事件。

如果只断言最终文本正确，工具结果没有正确进入 history 的 bug 也可能被漏掉。

## 3. 四类高价值断言

- **请求断言**：模型实际收到了哪些 input、instructions 和 tools？
- **事件断言**：客户端按何顺序看到 started/delta/completed/error？
- **状态断言**：history、token usage、thread metadata 是否正确？
- **副作用断言**：文件、命令、审批或 MCP 调用是否发生且受限？

优先比较完整对象，避免逐字段断言遗漏新增语义。

## 4. 从症状定位层级

### 用户输入后没有开始 turn

依次检查：

1. TUI 是否发出 `turn/start`；
2. app-server 是否找到 thread 并返回 response；
3. 是否调用 `SessionIo::submit(Op::UserInput)`；
4. `submission_loop()` 是否收到 submission；
5. active turn / steer 规则是否拒绝新任务。

### 模型请求中缺少上下文

检查：记录函数 → `ContextManager::for_prompt()` → `build_prompt()` → outbound mock body。不要只打印 session history。

### 工具调用后不继续

检查：

- `ToolRouter::build_tool_call()` 是否返回 call；
- registry 是否找到 runtime；
- tool future 是否完成；
- output 是否写回 history；
- `needs_follow_up` 是否为 true；
- cancellation/pending input/compaction 是否改变控制流。

### UI 没显示或重复显示消息

区分 core item 生命周期、app-server notification 转换和 TUI routing。重点检查 started/delta/completed 是否被两层都重复合成。

## 5. 本仓库验证命令

只改学习文档不需要编译 Rust。真正修改源码时遵循仓库规则：

```bash
cd codex-rs
just fmt
just test -p codex-core
```

如果修改 TUI，运行 `just test -p codex-tui` 并检查 pending snapshots。修改 common/core/protocol 后，完整测试套件需要先取得用户同意。较大 Rust 改动在结束前运行 scoped `just fix -p <project>`，而且 fix/fmt 之后不要重复跑测试。

## 6. 调试笔记模板

```markdown
### 症状
用户可见现象与最小复现。

### 最后一个已确认边界
例如：app-server 已成功 submit Op::UserInput。

### 缺失的下一个事件
例如：没有 TurnStarted，或没有第二次 /responses 请求。

### 证据
测试 mock、EventMsg、trace span、持久状态。

### 假设与反证
列出每个假设，以及哪条观测可以推翻它。
```

## 本章词汇表

| 词语 | 直译 | 在测试与调试中的意思 |
|---|---|---|
| Fixture | 固定装置 | 测试运行前准备好的输入、文件或环境 |
| Mock | 模拟对象 | 代替真实服务、并允许检查交互的测试实现 |
| Assertion | 断言 | 测试中必须成立的预期条件 |
| Integration test | 集成测试 | 验证多个真实组件组合行为的测试 |
| Snapshot test | 快照测试 | 保存完整输出，未来变更时比较整体差异 |
| Regression | 回归 | 原本正常的行为在修改后再次出错 |
| Reproduction | 复现 | 能稳定触发问题的最小步骤或测试 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

在 `core/tests/suite/` 中分别找一个：普通回答、tool call、resume、compaction 测试。不要先读测试名以外的说明；根据 SSE 和断言预测它验证的控制流，再阅读实现。
