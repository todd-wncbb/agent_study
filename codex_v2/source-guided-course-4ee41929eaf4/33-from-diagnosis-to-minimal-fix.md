# 33：从故障定位到最小修复——怎样修改最少的代码，并让问题不再复发

第 32 章教你用最后事件和配对事件找到故障边界。本章继续回答两个问题：

1. 找到边界以后，应该改哪里？
2. 怎样证明补丁修复的是根因，而不是碰巧让症状消失？

> 源码基线：`4ee41929eaf4`。本章中的“小型补丁”代码是教学伪代码；测试组织、命令和仓库约束来自当前源码与根目录 `AGENTS.md`。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 把调试证据写成可证伪的故障假设；
2. 区分产品缺陷、项目配置问题和正常但令人困惑的行为；
3. 从不变量反推出代码所有者和测试层；
4. 选择 unit、integration、snapshot 或 protocol/schema test；
5. 写一条修复前失败、修复后通过的回归测试；
6. 控制补丁的依赖、API 和文件影响面；
7. 按 Codex 仓库约定运行目标测试、生成物、lint 和 format；
8. 用 diff review 检查意外行为变化。

---

## 2. 先说人话：修复不是“改到不报错”

假设水管漏水，地板上有一摊水。

你可以：

- 把地板擦干；
- 在漏点下面放桶；
- 调低水压；
- 修复破裂的接头。

前三项可能让症状暂时不见，第四项才可能修复根因。

软件也一样：

```text
症状：UI 一直显示 Active
临时遮盖：30 秒后强制把 UI 改成 Idle
根因修复：终态通知到达时，无条件完成对应 turn 的客户端状态机
```

一条好补丁必须回答：

> 哪个原本应该成立的不变量被破坏了？修改后由什么测试长期守住它？

---

## 3. 第一步不是改代码，而是给问题分类

上一章的运行案例最终发现：测试进入了 watch mode。

```text
Tests passed. Watching for file changes...
```

这时 `ExecCommandBegin` 没有对应 `ExecCommandEnd`，不是因为 Codex 丢了 End，而是因为进程按设计没有退出。

### 3.1 三种常见分类

| 分类 | 含义 | 典型修复 |
|---|---|---|
| Product defect | 实现违反已有契约 | 修改代码并加回归测试 |
| Configuration/guidance gap | 实现正常，但项目没有告诉 Agent 正确用法 | 改命令、配置或 `AGENTS.md` |
| Expected behavior | 行为符合设计，只是用户预期不对 | 改进解释、文档或 UI 提示 |

### 3.2 Watch mode 案例应怎样修

如果仓库已有一次性测试命令，只是 Agent 总选错，可以在最接近适用目录的 `AGENTS.md` 写：

```md
Run `npm test -- --run` for one-shot validation.
Do not use watch mode in automated verification.
```

官方 AGENTS 指南也建议把重复出现的构建/测试命令、评审要求和目录约定写入 `AGENTS.md`，并把规则放在最接近适用代码的位置；可机械执行的规则再交给 linter、hook 或 type checker。[AGENTS Guidance](https://developers.openai.com/codex/concepts/customization#agents-guidance)

这里通常不需要给 executor 增加“检测所有 watch 命令”的复杂启发式。

---

## 4. 为什么“什么都不改”有时是正确结论

定位完成以后，可能发现：

- 用户主动启动了常驻服务器；
- 测试确实需要十分钟；
- 审批正在等用户；
- MCP server 按配置等待 30 秒；
- UI 正确显示后台 thread 仍在运行。

如果没有违反契约，代码改动可能引入新 bug。

正确产出可以是：

- 一条更准确的运行命令；
- 一项项目配置修正；
- 一条 `AGENTS.md` 规则；
- 一个更清楚的 UI 提示；
- 一份“不需要产品补丁”的证据报告。

不要把“提交了很多代码”当作问题解决质量的指标。

---

## 5. 把证据写成故障假设

故障假设应该能够被实验推翻。

### 5.1 太模糊的假设

```text
状态管理有问题。
```

它没有说明哪个状态、哪个事件、什么条件。

### 5.2 可证伪的假设

```text
当 app-server 收到一个没有 final assistant message 的 TurnComplete 时，
客户端投影没有清除 active turn，因此 thread/status/changed 一直保持 Active。
```

这条假设包含：

- 输入条件：无 final message 的 `TurnComplete`；
- 责任边界：app-server/client projection；
- 错误结果：active turn 未清除；
- 可观察行为：status 没回到 Idle/SystemError。

### 5.3 假设模板

```text
当【输入/状态】发生时，
【组件/函数】没有执行【应有转换】，
导致【公开可观察结果】；
如果假设正确，那么【最小实验】会稳定复现。
```

---

## 6. 从假设提炼不变量

不变量是无论实现细节怎样变化，都应该成立的规则。

对于上面的教学假设：

> 对同一个 turn，一旦收到 `TurnComplete` 或 `TurnAborted`，客户端投影最终不能继续把它视为 in progress。

这个表述比“把某个布尔值设成 false”更稳定，因为未来实现可能不再使用布尔值。

### 6.1 好不变量的特点

- 描述行为，不绑定某个私有字段；
- 能由外部事件或公开 API 观察；
- 对正常和失败终态都清晰；
- 没有把偶然实现细节写成契约；
- 可以直接转成测试名称。

例如：

```text
thread_status_returns_to_non_active_after_terminal_turn_event
```

---

## 7. 从不变量找到代码所有者

修复应该放在最接近不变量的层，而不是最容易编辑的文件。

### 7.1 本例的所有权链

```text
Core 生成 TurnComplete
  → app-server 接收 EventMsg
  → ThreadState 更新 current turn history
  → ThreadWatchManager 计算 status
  → v2 thread/status/changed
  → 客户端展示
```

如果 Core 已经正确生成 terminal event，而 app-server 状态仍 Active：

- 不应修改模型 Prompt；
- 不应修改 shell executor；
- 不应在 TUI 用定时器遮盖；
- 应先检查 app-server terminal-event 投影与状态推导。

### 7.2 “最靠近症状”不等于“拥有根因”

UI 是用户最先看见的地方，却可能只是最后一个投影层。修复所有者取决于最早违反不变量的位置。

---

## 8. 修改前先画影响面

在编辑之前列出可能受影响的契约：

```text
实现：app-server thread status transition
公开协议：ThreadStatus / thread/status/changed
客户端：TUI、App、IDE extension
持久化：resume 后的状态重建
测试：app-server v2 integration tests
生成物：只有 wire shape 改变时才需要 schema
平台：Linux / macOS / Windows
```

### 8.1 四类影响面

| 类型 | 要问的问题 |
|---|---|
| Call sites | 谁调用或消费这个函数/类型？ |
| Wire/API | JSON、CLI、配置或 rollout 是否变化？ |
| State/persistence | 旧 thread 或旧数据能否恢复？ |
| Build/test | Cargo、Bazel、snapshot、schema 是否需要更新？ |

### 8.2 Codex 特别敏感的外部边界

根目录 `AGENTS.md` 要求重点检查：

- app-server API；
- experimental raw response item events；
- CLI 参数；
- 配置加载；
- 从旧 rollout 恢复 session。

一次只有几行的类型修改，也可能是 breaking change。

---

## 9. 先寻找现有测试，而不是立刻新建框架

用症状中的稳定名词搜索：

```bash
rg -n 'thread_status_changed|WaitingOnApproval|TurnComplete' \
  codex-rs/app-server/tests codex-rs/app-server/src
```

当前仓库已有：

```text
codex-rs/app-server/tests/suite/v2/thread_status.rs
```

其中 `thread_status_changed_emits_runtime_updates` 已经验证：

```text
Active
→ turn 完成
→ Idle（或其他非 Active 终态）
→ 收到 turn/completed
```

这说明新的回归测试应优先复用 `TestAppServer` 和公开 JSON-RPC 通知，而不是调用一个私有 `set_active(false)` helper。

### 9.1 为什么复用现有 harness

- 与真实客户端走相同边界；
- 已处理初始化、mock model 和异步读取；
- 测试风格一致；
- Cargo/Bazel 兼容问题更少；
- 评审者更容易比较。

---

## 10. 选择正确的测试层

| 测试层 | 适合验证 | 不适合验证 |
|---|---|---|
| Unit test | 纯函数、局部状态转换、边界输入 | 跨组件事件是否真正连通 |
| Core integration | Agent loop、模型请求、工具回传、终态 | 只属于 TUI 像素布局的行为 |
| App-server integration | JSON-RPC、notification、公开状态 | 私有 helper 的每个分支 |
| TUI behavior/snapshot | 用户可见布局与交互状态 | 后端 API 是否正确持久化 |
| Schema/fixture | wire shape 与生成类型 | 运行时状态机行为 |
| Remote executor integration | app/exec 跨 OS 边界 | 可以由纯函数证明的小逻辑 |

### 10.1 Codex Agent 逻辑优先 integration test

仓库约定明确要求：改变 Agent 逻辑的功能必须有 integration test，优先使用 `core/suite` 和 `test_codex`，而不是只给私有函数写 unit test。

原因是 Agent 行为通常跨越：

```text
model response
→ tool routing
→ tool output
→ next model request
→ terminal event
```

只测中间 helper 容易漏掉真正断开的连接。

---

## 11. 一条回归测试应该证明什么

Regression test 不是“运行过代码”，而是把 bug 的必要条件和不变量固定下来。

本例的测试结构可以是：

```text
Given：模型结束 turn，但没有 final assistant message
When：app-server 处理 terminal event
Then：
  1. 曾观察到 Active
  2. 随后观察到非 Active
  3. 收到 turn/completed
  4. 不需要依赖任意 sleep 才成立
```

### 11.1 测试名应包含行为

不推荐：

```rust
#[tokio::test]
async fn bug_12345() { ... }
```

推荐：

```rust
#[tokio::test]
async fn terminal_turn_without_final_message_clears_active_status() { ... }
```

Issue 编号可以写在注释中，测试名应长期解释行为。

---

## 12. Red：先证明测试在旧实现上真的失败

理想顺序是：

```text
写测试
→ 在未修复实现上运行
→ 确认因预期断言失败
→ 再修改实现
```

### 12.1 为什么要确认“正确地失败”

测试可能因为完全无关的原因失败：

- fixture 没启动；
- mock response 格式错误；
- timeout 太短；
- 环境没有网络；
- assertion 观察错了 thread；
- 测试本来就 flaky。

只有失败信息直接对应不变量，才能证明测试真的覆盖 bug。

### 12.2 如果无法先运行旧版本

有时工作区已有修复或复现成本很高。至少应通过：

- 临时反转条件；
- 局部恢复旧逻辑；
- 构造直接触发旧分支的 fixture；

确认测试能抓住缺陷。临时验证改动不能进入最终 diff。

---

## 13. Green：做能恢复不变量的最小实现

假设教学版旧逻辑是：

```rust
if turn_has_final_message {
    state.finish_turn(turn_id);
}
```

它把“是否有最终文本”错误地当成“turn 是否结束”。最小修复可能是：

```rust
state.finish_turn(turn_id);
```

因为 terminal event 已经是生命周期的权威事实。

### 13.1 最小不是单纯按行数计算

真正的最小修复应同时满足：

- 修复根因；
- 保持其他行为不变；
- 不扩大公开 API；
- 不增加不需要的依赖；
- 不制造新的双重状态来源；
- 有测试保护。

有时新增一个小模块比把逻辑继续塞进 1,000 行中央文件更“小”，因为它缩小未来耦合。

---

## 14. 不要顺手重构无关代码

修 bug 时最常见的范围膨胀：

```text
修一个状态分支
+ 重命名附近十个类型
+ 更换错误库
+ 重排整个模块
+ 更新所有日志文案
```

这样会：

- 增加 review 成本；
- 隐藏真正行为变化；
- 增加 merge conflict；
- 让回滚更困难；
- 使失败测试难以归因。

如果重构确实必要，先问：能否拆成“行为不变的准备 PR”和“最小功能 PR”？

Codex 仓库建议非机械改动控制在 800 行以内，复杂逻辑最好低于 500 行；更大时应寻找可独立落地的阶段。

---

## 15. 为什么不要总往 `codex-core` 加代码

`codex-core` 已经很大。仓库约定明确要求引入新概念前先考虑：

- 现有其他 crate 是否拥有该职责；
- 是否应该创建一个更小的新 crate；
- 能否把已有通用能力抽出来，而不是继续扩大 core。

对于本例，如果错误发生在 app-server 状态投影，就应在 app-server 修复；把一个 UI 状态修正器塞进 core 会破坏所有权边界。

---

## 16. 测试完整对象，不要只断言方便的字段

仓库约定推荐使用 `pretty_assertions::assert_eq`，并尽量比较完整对象。

容易漏问题的写法：

```rust
assert_eq!(status.type_name(), "idle");
assert_eq!(notification.thread_id, expected_id);
```

如果类型支持，优先：

```rust
assert_eq!(
    notification,
    ThreadStatusChangedNotification {
        thread_id: expected_id,
        status: ThreadStatus::Idle,
    }
);
```

完整比较能在未来新增或误改字段时给出更清楚的 diff。

---

## 17. 不要用 sleep 猜异步系统完成了

脆弱测试：

```rust
tokio::time::sleep(Duration::from_millis(100)).await;
assert!(done);
```

它在快机器上可能通过，CI 上可能失败。

更好的方法是等待语义事件：

```rust
wait_for_event(&codex, |event| {
    matches!(event, EventMsg::TurnComplete(_))
})
.await;
```

或者从 app-server stream 等待指定 notification。

Timeout 仍应存在，但它是测试的外部护栏，不是业务同步机制。

---

## 18. Core integration test 的常用构造

当前仓库建议使用 `core_test_support::responses` 和 `test_codex`。

教学骨架：

```rust
let server = responses::start_mock_server().await;
let mock = responses::mount_sse_once(
    &server,
    responses::sse(vec![
        responses::ev_response_created("resp-1"),
        responses::ev_function_call(call_id, "shell", &args),
        responses::ev_completed("resp-1"),
    ]),
)
.await;

let test = test_codex().build_with_auto_env(&server).await?;
test.codex.submit(Op::UserInput { /* ... */ }).await?;

let request = mock.single_request();
assert_eq!(request.function_call_output(call_id), expected_output);
```

这里的关键不是背 API，而是：

- 模型响应由 fixture 明确控制；
- 保留 `ResponseMock`，可以检查真实 outbound request；
- 使用结构化 helper 读 request，不手工钻 JSON；
- 使用 `build_with_auto_env()` 兼容 app 与 executor 不同 OS 的情况。

---

## 19. App-server test 应通过公开 JSON-RPC 边界

App-server 的行为应尽量通过公开 API 测试：

```text
initialize
→ thread/start
→ turn/start
→ 读取 notification
→ 断言 thread/status/changed 和 turn/completed
```

使用 `TestAppServer::builder().build()` 与 `send_thread_start_request_with_auto_env()` 一类现有 helper，可以验证：

- wire serialization；
- request routing；
- Core event projection；
- notification ordering；
- 客户端真正能观察到的状态。

如果只调用 `resolve_thread_status()`，即使 unit test 通过，也可能漏掉事件没有到达它的情况。

---

## 20. UI 变化必须有 snapshot coverage

只要修改用户可见的 TUI 输出，就应新增或更新 `insta` snapshot。

典型流程：

```bash
just test -p codex-tui
cargo insta pending-snapshots -p codex-tui
cargo insta show -p codex-tui path/to/file.snap.new
cargo insta accept -p codex-tui
```

最后一步只应在确认要接受该 crate 的所有 pending snapshots 时运行。

### 20.1 Snapshot 不是“按一下接受”

必须亲自检查：

- 文案是否正确；
- 颜色/样式是否符合约定；
- 长文本换行是否正确；
- 错误状态有没有吞掉重要内容；
- 是否意外改变无关场景。

Snapshot 是 UI diff 的可审查契约。

---

## 21. 哪些测试不应该写

仓库约定提醒避免：

### 21.1 测试静态定义值

如果常量就是：

```rust
const DEFAULT_LIMIT: usize = 100;
```

再写 `assert_eq!(DEFAULT_LIMIT, 100)` 没有验证行为，只复制实现。

### 21.2 为已经删除的逻辑写负向测试

删除一个旧分支后，不必为每个旧细节永久增加“它不存在”测试。应保护新的公开不变量。

### 21.3 为测试向生产 API 暴露 helper

不要为了测试方便扩大 crate public surface。优先通过公开行为，或在专用 test module 中组织局部构造。

### 21.4 只断言“没有 panic”

如果行为有明确结果，应断言事件、对象或状态，而不是仅证明进程活着。

---

## 22. 新 unit test module 放在哪里

当新增测试模块时，仓库约定使用独立 sibling file：

```rust
#[cfg(test)]
#[path = "parser_tests.rs"]
mod tests;
```

实现放在：

```text
parser.rs
```

测试放在：

```text
parser_tests.rs
```

但不要为了符合新约定，把已有 inline test module 无意义地搬家。避免无关 churn。

---

## 23. 平台、Cargo 与 Bazel 的双重约束

本地 Cargo 通过，不保证 Bazel/其他平台通过。

### 23.1 启动 workspace binary

测试中优先：

```rust
codex_utils_cargo_bin::cargo_bin("codex")
```

而不是依赖只在 Cargo 目录布局下成立的路径。

### 23.2 定位 fixture/resource

优先：

```rust
codex_utils_cargo_bin::find_resource!(...)
```

避免用 `env!("CARGO_MANIFEST_DIR")` 假设 Bazel runfiles 布局。

### 23.3 编译时读取源码树文件

新增 `include_str!`、`include_bytes!`、`sqlx::migrate!` 等时，还要更新 crate 的 `BUILD.bazel` 中 `compile_data`、`build_script_data` 或 test data。

### 23.4 平台差异

除非功能明确是 OS-specific，测试应支持 Linux、macOS 和 Windows。命令行 quoting、路径分隔符、PTY 和 Sandbox 行为都可能不同。

---

## 24. 改了什么，就触发什么额外验证

| 改动 | 必做/常见后续 |
|---|---|
| `ConfigToml` 或嵌套配置类型 | `just write-config-schema` |
| app-server v2 API shape | 更新 README/examples，`just write-app-server-schema`，必要时 `--experimental` |
| `Cargo.toml` / `Cargo.lock` | `just bazel-lock-update`，提交 `MODULE.bazel.lock` |
| TUI 可见输出 | `just test -p codex-tui`，review/accept snapshots |
| common/core/protocol | 先目标测试；完整 `just test` 需按仓库流程征得用户同意 |
| 大型 Rust 改动 | `just fix -p <project>`，最后 `just fmt` |
| positional bool/None/number | 检查 `argument_comment_lint` 约定 |

生成文件不是噪声；它们是公开契约的一部分。

---

## 25. Codex 仓库中的验证顺序

对一个 app-server 小修复，可以采用：

```text
1. 运行最小单测/单个回归测试
2. 运行目标 crate：just test -p codex-app-server
3. 若涉及 core/protocol，评估并请求运行完整 just test
4. 需要时生成 schema / snapshots / Bazel lock
5. 大改动运行 just fix -p <project>
6. 最后在 codex-rs 运行 just fmt
7. 按仓库约定，不在 fix/fmt 后重新运行测试
8. 阅读最终 diff 和 git status
```

重要：当前仓库明确要求不要直接运行 `cargo test`，应使用 `just test` 走仓库默认测试配置。

Rust 构建可能在等锁或编译很久，不要因为暂时无输出就随意杀 PID。

---

## 26. 怎样判断应该跑多大范围的测试

测试范围与影响面匹配：

```text
单一纯函数
→ 单个 test / 目标 crate

App-server 状态投影
→ 回归 test + codex-app-server crate

Core Agent loop / protocol 公共类型
→ 目标 crate + 相关 integration + 完整 suite（按流程请求）

TUI 输出
→ 目标 TUI tests + snapshots

依赖或构建图
→ Cargo tests + Bazel lock/build considerations
```

“只跑一个测试”可能漏回归；“每次都跑全世界”又浪费时间。依据依赖方向选择范围。

---

## 27. Refactor：只有在 Green 之后才整理

测试通过后，可以做有限整理：

- 消除新引入的重复；
- 改善新代码命名；
- 缩小 visibility；
- 让 match exhaustive；
- 移除临时调试输出；
- 保持函数和模块尺寸合理。

不要把 Refactor 阶段变成新的功能设计。

如果整理改变了行为，说明它不是纯 Refactor，应该重新评估测试和拆分。

---

## 28. Review 最终 diff 时问什么

### 28.1 行为

- 测试是否精确覆盖原始复现条件？
- 正常路径有没有变化？
- error/cancel/timeout 是否仍闭合？
- 是否产生重复 terminal event？

### 28.2 API 与兼容性

- wire 字段或 enum variant 是否变化？
- 旧客户端/旧 rollout 是否仍能读取？
- optional/nullable 和 serde/TS rename 是否一致？

### 28.3 并发

- 同一个 turn 的迟到事件会怎样？
- 两个并行 call 会不会串 ID？
- lock 持有期间是否 await？
- cancellation 是否清理 pending sender？

### 28.4 安全

- 是否扩大文件、网络或工具权限？
- 是否绕过 approval/Sandbox？
- 日志是否泄露参数或凭据？

### 28.5 范围

- 是否混入无关格式化或重命名？
- 是否新增只用一次的 helper？
- 是否不必要扩大 `pub` API？
- diff 是否仍可由一个清晰标题解释？

---

## 29. 一个完整的教学修复过程

现在把过程串起来。

### 29.1 症状

```text
turn/completed 已到达，但 thread/status/changed 一直是 Active。
```

### 29.2 已确认事实

```text
Core TurnComplete 已生成
app-server 已收到 terminal event
客户端订阅正常
status transition 未发生
```

### 29.3 假设

```text
无 final assistant message 时，terminal branch 提前返回，未清 active state。
```

### 29.4 不变量

```text
terminal event 后，同一 turn 最终必须不再 Active。
```

### 29.5 所有者

```text
app-server terminal event projection / thread status
```

### 29.6 回归测试

```text
mock response 不产生 final assistant message
→ 启动 turn
→ 观察 Active
→ 等待 turn/completed
→ 观察 Idle/SystemError，而非 Active
```

### 29.7 Red

旧实现 timeout，错误信息表明一直没有非 Active status。

### 29.8 Green

移除“只有 final message 才清状态”的错误条件，让 terminal event 统一完成状态机。

### 29.9 验证

```text
单个回归测试
→ just test -p codex-app-server
→ 必要 schema/其他测试
→ just fix -p codex-app-server（若属大型改动）
→ just fmt
→ final diff review
```

### 29.10 结果说明

```text
根因：terminal lifecycle 被错误绑定到 final message 是否存在。
修复：terminal event 统一清理 active turn。
测试：覆盖无 final message 的完成路径。
未改变：wire schema、模型行为、executor 和 Sandbox。
```

这份说明让评审者能快速验证补丁边界。

---

## 30. 常见错误修复方式

### 错误 1：在 UI 加定时器强制 Idle

它隐藏了后端状态错误，也可能把真实长任务错误标成结束。

### 错误 2：测试私有字段，不测公开行为

字段重构后测试失去意义，而且无法证明 notification 链路可用。

### 错误 3：修复后才写永远通过的测试

如果没有证明测试能在旧逻辑失败，就不知道它是否覆盖原 bug。

### 错误 4：把 timeout 调得特别长

它可能减少 CI 偶发失败，却没有修复缺失状态转换。

### 错误 5：为了测试加 public helper

这扩大 crate API，并让测试绕开真实路径。

### 错误 6：接受所有 snapshot 而不查看

会把无关 UI 回归一起固化。

### 错误 7：直接 `cargo test`

会绕开仓库通过 `just test` 统一的默认行为。

### 错误 8：只汇报“测试通过”

还应说明改了什么、覆盖什么边界、哪些测试没跑以及原因。

---

## 31. 可复用的修复计划模板

```text
问题：
  用户可见症状是什么？

证据：
  最后正常边界：
  缺失/错误事件：
  相关 ID：

分类：
  产品缺陷 / 配置指导 / 预期行为

假设：
  当……时，……没有……，因此……

不变量：
  无论实现细节如何，什么必须成立？

所有者：
  哪个 crate/module 最早违反不变量？

影响面：
  call sites：
  wire/API：
  persistence：
  platform/build：

回归测试：
  Given：
  When：
  Then：
  旧实现怎样失败：

最小补丁：
  修改哪些文件：
  明确不修改什么：

验证：
  单个测试：
  目标 crate：
  schema/snapshot/lock：
  broader suite：
  lint/format：

最终检查：
  diff、git status、安全、兼容性、未运行项
```

---

## 32. 本章词汇表

完整总表见[课程术语表](glossary.md)。

| 名词 | 常见写法 | 通俗解释 |
|---|---|---|
| Defect classification | classification | 判断是产品 bug、配置问题还是预期行为 |
| Hypothesis | hypothesis | 可被实验推翻的根因猜想 |
| Invariant | invariant | 状态机或协议始终应满足的规则 |
| Code ownership | owner layer | 最早负责维持该不变量的组件 |
| Impact surface | impact surface | 修改可能影响的调用方、协议、存储、构建和平台 |
| Regression test | regression test | 防止已修复缺陷再次出现的测试 |
| Red / Green / Refactor | TDD cycle | 先失败、再最小通过、最后整理 |
| Test harness | harness | 创建环境、fixture、mock 和观察接口的测试脚手架 |
| Fixture | fixture | 为测试准备的固定输入或外部响应 |
| Mock server | mock server | 用可控响应替代真实外部服务的测试 server |
| Assertion | assertion | 测试对结果必须满足条件的声明 |
| Deep equality | whole-object equality | 比较完整对象，而不是逐个挑字段 |
| Integration test | integration test | 验证多个真实组件连接后的行为 |
| Snapshot test | `insta` snapshot | 保存完整 UI 输出并在变化时展示 diff |
| Flaky test | flaky | 相同代码和输入下偶发通过或失败的测试 |
| Public boundary | public API / wire | 真实调用者观察和依赖的接口 |
| Churn | unrelated churn | 与本次行为无关的重排、重命名和格式变化 |
| Breaking change | breaking change | 让已有客户端、配置或持久数据不再兼容的变化 |
| Generated artifact | generated file | 由 schema 或构建命令生成、需要随源类型同步提交的文件 |
| Targeted test | scoped test | 只运行受影响 test/crate 的快速验证 |
| Diff review | final diff | 提交前逐行检查实际改动及意外影响 |

### 代码单词和短语拆解

- `diagnosis`：诊断；用证据确定问题所在层。
- `fix`：修复；恢复被破坏的行为或不变量。
- `minimal`：最小；刚好修复根因且不扩大无关范围。
- `classify`：分类；判断问题属于哪种性质。
- `hypothesis`：假设；等待实验验证或推翻的具体解释。
- `falsifiable`：可证伪；存在一种结果能证明假设错误。
- `invariant`：不变量；状态变化过程中始终必须成立的性质。
- `owner`：所有者；对某项行为或状态负主要责任的模块。
- `impact surface`：影响面；变化可能传播到的范围。
- `call site`：调用点；使用某函数、类型或 API 的位置。
- `regression`：回归；过去修好的问题再次出现。
- `test harness`：测试脚手架；组织测试环境和观察能力的基础设施。
- `fixture`：夹具/固定样例；让测试输入可重复的数据。
- `mock`：模拟对象；替代真实依赖并提供可控行为。
- `stub`：桩；提供最小固定返回的替代实现。
- `assert`：断言；声明测试必须观察到的结果。
- `red`：红；测试因目标缺陷存在而失败。
- `green`：绿；最小实现让测试通过。
- `refactor`：重构；在不改变行为的前提下改善结构。
- `scope`：范围；这次修改明确包含和排除的内容。
- `churn`：扰动；无关但增加 diff 的代码变化。
- `flaky`：不稳定；在条件相同时偶发失败。
- `deterministic`：确定性；同样输入产生稳定可预测结果。
- `deep equality`：深度相等；递归比较整个结构。
- `snapshot`：快照；某次完整输出的可审查基准。
- `golden file`：黄金文件；作为预期输出保存的参考文件。
- `schema`：模式；数据结构和 wire shape 的正式描述。
- `artifact`：产物；构建或生成流程产生的文件。
- `compatibility`：兼容性；新版本继续理解旧调用方或旧数据。
- `breaking`：破坏兼容的。
- `targeted`：定向的；只覆盖当前责任范围。
- `broader suite`：更广测试集；检查跨 crate 回归。
- `lint`：静态规则检查；发现风格和常见代码问题。
- `format`：格式化；按统一规则排版源码。
- `review`：审查；检查行为、风险和 diff 是否符合意图。

---

## 33. 自测题

1. Watch mode 案例为什么不一定需要修改 Codex 源码？
2. Product defect、guidance gap 和 expected behavior 有什么区别？
3. 什么叫“可证伪的故障假设”？
4. 为什么不变量应描述行为而不是私有字段？
5. 怎样从最后正常边界找到代码所有者？
6. 为什么 UI 出现症状不代表 UI 拥有根因？
7. 修改前要检查哪四类影响面？
8. 为什么 Agent 逻辑优先 integration test？
9. 一条 regression test 为什么要在旧实现上确认失败？
10. “最小修复”为什么不只是行数最少？
11. 为什么不要顺手重构无关代码？
12. 什么情况下应避免继续向 `codex-core` 加代码？
13. 为什么优先比较完整对象？
14. 为什么 `sleep(100ms)` 容易产生 flaky test？
15. App-server 行为为什么应通过 JSON-RPC 边界测试？
16. Snapshot 更新后为什么必须查看 `.snap.new`？
17. 新 unit test module 应怎样组织？
18. Cargo 通过为什么仍可能在 Bazel 失败？
19. 哪些改动需要重新生成 config/app-server schema 或 Bazel lock？
20. 当前仓库为什么要求使用 `just test` 而不是 `cargo test`？
21. Final diff review 应检查哪些并发与兼容问题？

---

## 34. 源码检查点

1. 根目录 `AGENTS.md`
   - 找 test authoring、change size、integration、snapshot 和验证命令约定。
2. `codex-rs/app-server/tests/suite/v2/thread_status.rs`
   - 阅读 `thread_status_changed_emits_runtime_updates`；
   - 看测试怎样观察 Active→Idle 与 `turn/completed`。
3. `codex-rs/app-server/src/thread_status.rs`
   - 看 runtime facts 怎样投影成 `ThreadStatus`。
4. `codex-rs/app-server/src/bespoke_event_handling.rs`
   - 追 `TurnComplete` 的状态清理和 notification。
5. `codex-rs/core/tests/suite/current_time_reminder.rs`
   - 看 `test_codex`、mock responses、完整 request 断言。
6. `codex-rs/core/tests/suite/rmcp_client.rs`
   - 看模型→MCP→结果→下一次模型请求的 integration test。
7. `codex-rs/core/tests/suite/client_websockets.rs`
   - 看 transport fixture、headers 和断线行为测试。
8. `codex-rs/core/tests/common/responses.rs` 及其子模块
   - 找 `mount_sse_once`、`ResponseMock`、event constructors。
9. `codex-rs/core/tests/common/test_codex.rs`
   - 看 `TestCodexBuilder` 与 `build_with_auto_env()`。
10. `codex-rs/tui/src/chatwidget/tests/app_server.rs`
    - 看用户可见通知与 lifecycle 的测试。
11. `codex-rs/tui/src/snapshots/`
    - 选择一个 `.snap`，理解 snapshot header 与正文。
12. 根目录 `justfile`
    - 对照 `fmt`、`fix`、`test`、schema、Bazel lock 和 argument lint recipe。
13. `codex-rs/app-server-protocol/src/protocol/v2/`
    - 看 serde/TS wire types 与生成 schema 的对应关系。
14. `codex-rs/core/config.schema.json`
    - 理解配置类型变化为什么必须更新生成物。
15. `MODULE.bazel.lock`
    - 理解 Rust 依赖变化为什么还影响 Bazel lock。

---

## 35. 一句话总结

从诊断到修复的可靠路径是：先给问题分类，把证据写成可证伪假设，再提炼公开不变量并找到最早违反它的所有者；随后用现有 harness 写一条旧实现会因正确原因失败的回归测试，以最小补丁恢复不变量，运行与影响面匹配的测试、生成物、lint 和 format，最后用 diff review 确认没有把局部修复变成新的兼容性、并发或安全问题。
