# 78：Rust Unit、Integration、Mock、Snapshot 与 Property Test

> 源码基线：`4ee41929eaf4`。本章解决“一个行为该在哪一层测试、怎样驱动真实边界、怎样让失败有诊断价值，以及如何避免测试只复制实现”。

## 1. 本章解决什么问题

测试不是“多写几个 assert”。真正困难的是选择观察点：测私有 helper 很快，却可能漏掉 wiring；完整端到端很真实，却可能慢且难定位。本章把不同测试层组织成一套决策方法。

## 2. 先说人话：Test 是可重复的行为证明

一个好测试会明确：给定什么前提，执行哪个公共动作，应该观察到什么结果。它还应在旧错误实现上可靠失败，在正确实现上稳定通过。

## 3. Arrange、Act、Assert

```rust
#[test]
fn rejects_zero_version() {
    // Arrange
    let raw = 0;

    // Act
    let version = ProtocolVersion::new(raw);

    // Assert
    assert_eq!(version, None);
}
```

三段不必总写注释，但应让读者能一眼分出准备、动作和观察。

## 4. Test Oracle

Oracle 是判断正确与否的依据。它可以是精确 value、事件序列、wire request、渲染 snapshot、不变量或旧数据恢复结果；“函数没有 panic”通常是很弱的 oracle。

## 5. Unit Test

Unit test 聚焦较小逻辑单元，通常与实现位于同一 crate，可访问 crate 内部/private module。它反馈快，适合 parser、状态转换、集合规则和纯函数边界。

## 6. Integration Test

Rust `tests/` 下的 integration test 作为单独 crate 编译，只能通过公开 API 使用被测 crate。它能发现 module wiring、公开契约和多组件组合问题。

## 7. End-to-end Test

E2E 从用户或协议入口驱动接近完整系统，验证跨进程、网络、存储或 UI 的旅程。覆盖最真实，但建立环境、定位失败和运行成本也最高。

## 8. 测试层不是文件名决定的

一个 `#[test]` 也可能启动很多组件；一个 `tests/` 文件也可能只测小公开函数。判断层级要看实际穿过哪些边界、替换了哪些依赖和观察了什么。

## 9. `#[cfg(test)]`

```rust
#[cfg(test)]
mod tests;
```

测试配置启用时才编译该 module。普通库构建不会带入测试 helpers 和 test-only imports。

## 10. 独立 Sibling Test File

Codex 新测试模块常写：

```rust
#[cfg(test)]
#[path = "execution_tests.rs"]
mod tests;
```

实现文件保留声明，测试内容放在相邻 `*_tests.rs`，既能访问父 module 私有项，又避免继续放大主实现文件。

## 11. Inline Test Module

很小的类型也可能直接写 `#[cfg(test)] mod tests { ... }`，如固定提交的 `ThreadId`。已有风格不必只为统一而机械搬动；新增大测试组则更适合独立文件。

## 12. `#[test]`

普通同步测试是无参数函数。正常返回表示通过，panic、assert 失败或未处理错误表示失败。

## 13. 返回 `Result` 的 Test

测试可返回 `anyhow::Result<()>`，从 setup 和 async 操作中直接使用 `?`。但关键预期失败仍应显式断言 error variant/message，而不是只让任何错误冒泡。

## 14. `assert!` 与 `assert_eq!`

`assert!(predicate)` 适合单一布尔性质；`assert_eq!(actual, expected)` 会展示两边差异。能比较完整结构时，优先完整 equality，而不是逐字段写很多 assert。

## 15. `pretty_assertions`

Codex 测试广泛导入 `pretty_assertions::assert_eq`，为长 Vec、Struct 和文本提供更清楚的 diff。宏名相同，但失败输出更适合阅读。

## 16. Deep Equality

```rust
assert_eq!(actual, expected);
```

完整对象比较会同时检查字段之间的组合关系，新增字段也更容易迫使测试作者决定期望；逐字段比较可能无意遗漏重要状态。

## 17. 不要测试静态定义本身

若常量就是 `const LIMIT: usize = 10`，再断言 LIMIT 等于 10 只复制源码。应测试 LIMIT 参与行为后是否真的限制了第 11 项。

## 18. 不要为已删除逻辑写负向 Test

功能移除后，测试“旧实现不存在”往往绑死内部结构。若仍存在重要公开合同，应测试当前行为；否则用静态搜索、编译边界或迁移证据更合适。

## 19. Test Name 是微型需求

`current_time_read_round_trip_adds_reminder_to_model_input` 比 `test_current_time` 更清楚：入口、动作和可观察结果都在名称里。

## 20. One Reason to Fail

一个测试可以有多条相关断言，但最好只证明一个行为故事。setup 已成功后，失败应能快速指向同一个 contract，而不是十个无关功能。

## 21. Table-driven Test

多个输入遵循同一规则时，可用 case table 循环：输入、期望、case name 分开。失败信息必须带 case identity，避免只看到“第 7 次循环失败”。

## 22. `#[tokio::test]`

Async 测试需要 runtime 驱动 Future。`#[tokio::test]` 创建 Tokio runtime 并 await test body；可按需要选择 current-thread 或 multi-thread flavor。

## 23. Runtime Flavor 是测试条件

单线程 runtime 更可控，但可能掩盖 Send/并行问题；multi-thread 更接近生产，却增加调度变化。测试应按要证明的行为选择，而不是一律增加 worker 数。

## 24. 不要用 Sleep 猜完成

`sleep(100ms)` 既可能在慢机器不够，又让快机器白等。优先等待确定事件、channel、JoinHandle、状态通知或测试 harness 的 `wait_for_event`。

## 25. Timeout 是 Watchdog，不是同步机制

Timeout 可防止失败测试永久挂住，但不能证明内部动作已发生。正确模式是“等待语义事件 + 外层有界 timeout”，不是“sleep 一段时间后读状态”。

## 26. 可控时间

涉及 timer、TTL、retry 时，优先 fake clock、Tokio paused time 或可注入 TimeProvider。真实 wall clock 会带来时区、调度和 CI 负载噪声。

## 27. TempDir Fixture

`tempfile::TempDir` 为每个测试提供隔离目录，owner Drop 时清理。不要复用开发者真实配置目录，也不要让并行测试写同一个固定路径。

## 28. 避免修改进程环境

环境变量是进程全局可变状态，并行测试会互相干扰。优先从 builder 注入环境派生值或 dependency；确实必须修改时需串行化并可靠恢复。

## 29. Global State

OnceLock、全局 runtime、端口、terminal state 和 current directory 都可能泄漏到其他测试。Fixture 的职责不仅是创建，还要明确 teardown 与恢复。

## 30. Core Integration Test Aggregator

固定提交的 `codex-rs/core/tests/all.rs` 是单一 integration test binary，`mod suite;` 再聚合 `tests/suite/*.rs`。这样共享 harness 和 test-binary dispatch setup，而非每个文件生成独立 binary。

## 31. 为什么聚合 Test Binary

它可减少链接/启动开销，并集中安装 first-party binary aliases；代价是进程级全局状态由更多测试共享，因此隔离要求更高。

## 32. `TestCodex`

Core harness 构造一个可提交 `Op`、读取 Event、配置依赖并连接 mock model server 的测试实例。测试从 agent 公共行为推进，而不是直接调用每个私有 helper。

## 33. `build_with_auto_env`

该 builder 路径为本地/远程、不同 app/exec OS 测试准备自动环境。涉及 agent 或 executor 行为时，复用它比手工拼一个只适用于当前机器的环境更稳健。

## 34. Mock Responses Server

`start_mock_server()` 启动本地 Wiremock server，并安装默认 models response。测试控制模型侧事件，不需要访问真实 OpenAI 服务，也不会受真实模型非确定性影响。

## 35. 构造 SSE Fixture

`sse(vec![ev_response_created(...), ev_function_call(...), ev_completed(...)])` 用 typed helpers 构造模型事件流。Fixture 只包含当前行为所需事件，便于看清协议因果链。

## 36. `mount_sse_once`

它把响应配置为最多匹配一次，并返回 ResponseMock。若代码意外多发请求、请求未到达或顺序变化，测试能从 mock 交互中暴露问题。

## 37. `ResponseMock::single_request`

当场景只允许一次 POST 时，single_request 同时取出请求并断言数量。比“拿第一个请求忽略其余请求”更强，因为重复调用本身可能是 bug。

## 38. Structured Request Assertions

Response request wrapper 提供 message texts、function call output、header、path 等 helper。优先断言结构化字段，而不是反复手写深层 JSON index。

## 39. Additional Context 集成案例

测试提交真实 `Op::UserInput`，等待 `ItemCompleted` 和 `TurnComplete`，再检查用户事件与 outbound model request。它同时证明 additional context 对模型可见，却没有污染 user-message item。

## 40. Wait for Event

`wait_for_event_match` 以业务事件作为同步点并提取 payload。它比读“下一条恰好是目标事件”更能容忍无关 telemetry/中间事件，同时仍需明确最终 timeout 策略。

## 41. App-server Integration Test

App-server 测试通过公开 JSON-RPC API 发 thread/start、turn/start，读取 request/notification，再发送 response。协议实现应从消费者能看到的边界验证，而不是直接调用内部 processor。

## 42. Current-time Round Trip 案例

测试启动 TestAppServer，提交 turn，循环处理 `CurrentTimeRead` server request，直到 `turn/completed`；随后检查模型请求中出现时间 reminder 和 environment date。这是一条完整双向协议旅程。

## 43. Mock、Stub、Fake、Spy

- Stub：返回预设答案；
- Fake：有简化但可工作的实现，如内存数据库；
- Mock：带交互预期的替身；
- Spy/Recorder：记录调用供之后断言。

团队术语可能混用，阅读时重点看替换了什么、是否保存状态、是否验证调用。

## 44. Mock 的边界

Mock 最适合不稳定、昂贵或不可控的外部边界。不要把被测类的每个内部 collaborator 都 mock 掉，否则测试只证明“代码按当前实现顺序调用了几个方法”。

## 45. Faithful Mock

替身应保留被依赖的关键协议语义：请求次数、流式顺序、错误 shape、关闭行为和 backpressure。过于宽松的 mock 会接受生产服务拒绝的行为。

## 46. Hermetic Test

Hermetic 表示测试依赖被固定和隔离：不读取用户机器状态，不访问真实外网，不依赖执行顺序，并在相同输入下产生相同结果。

## 47. `skip_if_no_network`

固定提交的一些 loopback mock tests 会在 sandbox 不允许网络 socket 时跳过。Skip 是环境能力声明，不等于测试通过；CI 仍需有能真正运行这些测试的平台。

## 48. Fixture

Fixture 是多个测试共享的准备对象、数据或 helper。好 fixture 有安全默认值，并允许当前测试只覆盖相关字段；坏 fixture 隐藏大量行为，让读者不知道场景究竟启动了什么。

## 49. Builder Fixture

`test_codex().with_config(...).build(...)` 通过 builder 描述差异。Builder 应避免全局 mutation，并把最终构造失败以 Result 暴露给 test。

## 50. Snapshot Test

Snapshot 把较大、可读输出保存为 golden expectation。下一次结果不同会产生 diff，由作者判断这是回归还是有意变化。

## 51. TUI Snapshot 案例

`history_ui_tests.rs` 构造 info/error history cell，以固定 width 渲染成文本，再用 `insta::assert_snapshot!` 检查完整可见输出。它测的是用户看到的布局，而非私有 span 列表。

## 52. Snapshot 适合什么

- 多行 TUI/CLI 渲染；
- 稳定序列化 shape；
- 较大的 prompt/context 布局；
- 人工 review 比逐字段 assert 更高效的结果。

## 53. Snapshot 不适合什么

随机 ID、当前时间、临时路径和无稳定顺序的 map 会制造噪声；单个布尔值也没必要 snapshot。先稳定或遮蔽非本质字段，再保存 golden。

## 54. Inline 与 External Snapshot

Insta 可把期望写在宏旁，也可保存 `.snap` 文件。Inline 适合短输出，external 适合长渲染和独立 review；选择应让 diff 最清楚。

## 55. Accept 不是修复

测试输出变化后不能直接全量 accept。先阅读 `.snap.new`，确认每一处变化都由需求解释，再接受预期 snapshot；意外变化应修代码或 fixture。

## 56. Snapshot 与精确 Assert 可并用

Additional-context test 既 snapshot 整体 context，又精确断言 developer/user message 分类。Snapshot 提供全貌，结构化 assert 锁定关键 contract。

## 57. Generated Fixture

JSON Schema、TypeScript bindings 和 lockfile 属于生成/解析事实的提交产物。测试应验证生成器与 committed fixture 同步，而不是手工复制 schema 内容到多个 assertion。

## 58. Property-based Test

Property test 不是列几个例子，而是生成大量输入验证普遍性质，例如 parse(format(x)) round trip、排序结果有序、normalize 幂等、容量永不越界。

## 59. Generator

Generator 描述输入空间。高价值 generator 会覆盖空值、边界长度、Unicode、重复项、极端数字和结构组合，而不是只生成常见 happy path。

## 60. Shrinking

失败后框架尝试把复杂随机输入缩小为仍能复现的最小 case。好的 shrink result 能把“500 个元素失败”变成“两个重复 key 就失败”。

## 61. Seed 与复现

随机测试必须报告 seed/最小反例并可本地复现。不能用“再跑一次通过了”处理失败；那只会把确定性 bug 变成 flaky signal。

## 62. Property Test 的位置

本章固定源码案例主要是 example、integration 和 snapshot tests；property-based testing 是补充方法。不要为了技术齐全把所有测试都改成随机生成。

## 63. Example Test 仍然重要

具体历史回归、wire fixture 和用户可读渲染通常需要命名示例。Property 擅长广度，example 擅长解释“这个真实 case 为什么重要”。

## 64. Concurrency Test

并发测试应控制关键交错：barrier、channel、permit、AtomicWaker 或 hook 明确表示“写已阻塞”“取消已到达”，再释放下一步。靠概率循环寻找 race 会慢且不稳定。

## 65. Cancellation Test

至少验证：取消前动作已进入目标阶段；取消信号确实到达；task/child/process 最终停止；permit、counter 和 queue state 被恢复；不会产生两个终态。

## 66. Resource Cleanup Test

可用 Weak upgrade、计数、临时文件、JoinHandle 或 mock 调用检查 owner 是否释放。只等待固定时间后断言“应该 drop 了”容易形成竞态。

## 67. Platform-specific Test

`#[cfg(unix)]`、Windows fixture 和 platform helper 只能证明对应平台。共享逻辑应尽量在所有 host 运行，平台语义再由 native/remote CI 分层覆盖。

## 68. Remote Executor Test

Host 与 executor 可能不同 OS。`build_with_auto_env` 和 app-server auto-env harness 让同一行为测试保留 foreign path、shell 和 sandbox 语义，而不是假设测试进程 OS 等于执行 OS。

## 69. Cargo 与 Bazel Resource

测试启动 first-party binary 或读取 fixture 时，应使用仓库提供的 cargo-bin/resource helper，避免只在 Cargo 的 `CARGO_MANIFEST_DIR` 下成功、到了 Bazel runfiles 就找不到。

## 70. 运行目标 Test

本仓库用 `just test -p codex-<crate>` 遵循统一 runner，而不是直接 `cargo test`。先运行修改 crate 的最小相关集合；common/core/protocol 大改再按规则扩大范围。

## 71. Test Filter 的风险

名称过滤适合快速迭代，但可能漏掉同模块其他 contract、snapshot 或 doc tests。提交前至少跑 crate 级目标测试，而不是只报告单个 case 通过。

## 72. Regression Test 先 Red 后 Green

修 bug 时，新增测试应在旧实现上因正确原因失败，再让最小修复使其通过。若测试一开始就绿，它可能没有触发 bug 或 oracle 太弱。

## 73. 测公开行为而非 Patch

测试应描述用户/调用者能观察的合同，不应断言“某 helper 被调用一次”，除非调用本身就是外部协议要求。这样重构实现不会无意义破坏测试。

## 74. Test Pyramid 不是硬配额

纯逻辑需要大量快 unit tests，agent orchestration 需要关键 integration tests，少量 E2E 保护完整旅程。比例由风险和边界决定，不是固定 70/20/10。

## 75. Flaky Test 分类

常见根因：真实时间、固定端口、全局环境、无序集合、并发竞态、未等待 teardown、外部服务、资源不足和断言过度依赖事件顺序。先找非确定性来源，不要只增加 retry。

## 76. Retry 何时有害

自动重跑可能掩盖真实 race 和泄漏。只有已知外部不稳定且保留首轮失败证据时才有限重试；产品逻辑测试应优先变得 deterministic。

## 77. 失败诊断顺序

1. 读第一个根因，而非后续级联 panic；
2. 区分 setup、act、timeout、assert、teardown；
3. 检查 mock 是否收到预期请求；
4. 检查等待事件是否真的可达；
5. 单独运行与 crate 全量都试一次；
6. 若只在全量失败，优先找共享状态和泄漏。

## 78. 新行为的 Test 选择清单

- 这是纯函数、状态投影、协议 wiring 还是用户旅程？
- 最低成本且能触发真实 bug 的入口在哪里？
- 哪些依赖必须真实，哪些可由受控 fake/mock 替代？
- Oracle 是 value、event、request、snapshot 还是资源终态？
- 失败、取消、重复与平台分支是否属于主 contract？
- 旧实现会因正确原因失败吗？

## 79. Review Test 的清单

- 名称是否说明行为？
- Setup 是否隐藏无关魔法？
- 是否比较完整对象或稳定结构？
- 是否靠 sleep、真实时间或外网？
- Mock 是否过宽或过度验证内部调用？
- Snapshot 是否已人工审阅？
- Async task、文件和全局状态是否清理？
- Test 是否在正确 crate/平台 runner 中执行？

## 80. 源码检查点

1. `codex-rs/protocol/src/thread_id.rs`：小型 inline unit test。
2. `codex-rs/core/src/agent/control/execution.rs` 与 `execution_tests.rs`：sibling private unit test module。
3. `codex-rs/core/tests/all.rs`、`tests/suite/mod.rs`：聚合 integration test binary。
4. `codex-rs/core/tests/common/test_codex.rs`：TestCodex builder 与 auto environment。
5. `codex-rs/core/tests/common/responses.rs`：Wiremock、typed SSE、ResponseMock。
6. `codex-rs/core/tests/suite/additional_context.rs`：事件、outbound request 和 snapshot 组合断言。
7. `codex-rs/app-server/tests/common/test_app_server.rs`：公开 JSON-RPC harness。
8. `codex-rs/app-server/tests/suite/v2/current_time.rs`：双向 request/response round trip。
9. `codex-rs/tui/src/app/history_ui_tests.rs`：固定 width 的用户可见 snapshot。

## 81. 搜索命令

```bash
rg -n '#\[(tokio::)?test\]|#\[path = ".*_tests.rs"\]' codex-rs/core/src codex-rs/app-server/src
rg -n 'mount_sse_once|single_request\(\)|wait_for_event' codex-rs/core/tests
rg -n 'TestAppServer|JSONRPCMessage::Request|turn/completed' codex-rs/app-server/tests
rg -n 'assert_snapshot!|assert_eq!' codex-rs/tui/src codex-rs/core/tests
```

## 82. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Test case / oracle | 测试案例/判定依据 | 一组前提动作与预期，以及判断正确性的事实来源 |
| Arrange/Act/Assert | 准备/执行/断言 | 测试故事的三个逻辑阶段 |
| Unit/integration/E2E | 单元/集成/端到端 | 小逻辑、公开组合边界和完整用户旅程测试 |
| Regression test | 回归测试 | 固定历史 bug、阻止相同行为再次出现的案例 |
| Fixture / builder | 夹具/构建器 | 可复用测试环境数据与具名配置入口 |
| Mock/stub/fake/spy | 模拟/桩/假实现/记录器 | 不同能力和交互强度的 test double |
| Hermetic / deterministic | 封闭/确定性 | 与外部机器隔离并在相同输入下稳定复现 |
| Test harness | 测试工具架 | 驱动系统、注入依赖并收集观察结果的基础设施 |
| Test oracle / deep equality | 判定依据/深度相等 | 预期来源，以及比较完整对象结构 |
| Snapshot / golden file | 快照/金文件 | 保存大段稳定预期并以 diff review 变化 |
| Property/generator/shrinking | 性质/生成器/缩减 | 广泛生成输入验证不变量并最小化失败反例 |
| Seed / counterexample | 随机种子/反例 | 可复现生成过程的值与证明性质不成立的输入 |
| Flaky test | 不稳定测试 | 代码未变却因时间、顺序或环境偶发变化的测试 |
| Watchdog timeout | 看门狗超时 | 防止挂死的外层界限，而非完成同步机制 |
| Loopback / Wiremock | 本机回环/网络模拟器 | 不访问真实外网的本地协议测试 server |
| SSE fixture / ResponseMock | SSE 夹具/响应记录器 | 预制模型事件流与捕获 outbound request 的对象 |
| Red/green | 红/绿 | 修复前正确失败和修复后通过两个阶段 |
| Test isolation / teardown | 测试隔离/拆除 | 防止跨 case 状态污染，以及回收资源 |

## 83. 常见误解

- “测试越接近私有函数越精确”：它也可能漏掉真实 wiring 和公开 contract。
- “集成测试必须启动所有服务”：可在公开边界使用受控 mock 外部依赖。
- “Sleep 足够长就稳定”：机器负载总能打破时间猜测。
- “Mock 越多越隔离”：过度 mock 会复制实现顺序而非验证行为。
- “Snapshot 变化直接 accept”：每一处 diff 都必须先解释。
- “Property test 能替代具体回归”：历史行为仍需要命名 example。
- “Timeout 就代表取消完成”：外部 task/process 可能仍在运行。
- “单个过滤测试通过就完成验证”：还需 crate 级相关测试与生成物检查。
- “Flaky 失败重跑过了就没事”：它通常提示 race、泄漏或共享状态。

## 84. 一句话收束

先从最小但真实的行为边界触发问题，再用稳定 oracle 观察完整结果；unit test 保护局部规则，integration harness 保护 wiring 与协议，snapshot 保护复杂可见输出，property test 扩展输入空间，而确定性同步、隔离资源和人工审阅让这些证据长期可信。
