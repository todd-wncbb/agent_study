# Grok Build 测试体系、Mock、Fixture 与可靠性验证：怎样测试一个异步 Agent

> 一个 Agent 同时依赖模型流、HTTP、SSE、MCP、文件系统、Git、子进程、PTY、时间、权限、持久化和多个 actor。本文不只罗列测试文件，而是解释这些不可控因素如何被变成可重复的测试输入，以及测试自身如何避免污染开发机、泄漏子进程和制造假阳性。

---

## 1. 先给结论

Grok Build 的测试体系可以分成八层：

1. 纯函数单元测试：解析、归约、策略和序列化。
2. Actor/状态机测试：通过 channel 驱动真实 actor，断言消息顺序和状态转换。
3. Crate 内部集成测试：真实组件组合，替换网络、终端或持久化边界。
4. 独立 integration test binary：从 crate 外部验证 public API、真实子进程和协议。
5. Mock Inference/HTTP/MCP 测试：本机随机端口上的真实协议栈。
6. Headless/ACP/Leader 子进程测试：启动真实 `grok` binary。
7. PTY/TUI E2E：真实终端模式、按键、resize、ANSI/OSC 和屏幕状态。
8. Snapshot、fuzz、benchmark、soak、平台特定与手工 ignored 验证。

它的核心测试基础设施分三层：

```text
xai-test-utils
    通用 Git、runfiles、tracing capture、图片工具

xai-grok-test-support
    MockInferenceServer、ACP client、TestSandbox、TestProcess、故障代理

xai-grok-pager-pty-harness
    PTY、虚拟屏幕、按键/resize、场景 DSL、性能与正确性检查
```

---

## 2. 为什么 Agent 测试比普通服务难

一个看似简单的“发送 prompt 后得到回答”至少跨越：

```text
输入队列
  ↓
Session actor
  ↓
ChatState actor
  ↓
HTTP + SSE 模型流
  ↓
Tool loop / Permission
  ↓
文件或子进程副作用
  ↓
Persistence actor
  ↓
ACP gateway
  ↓
TUI reducer/render
```

测试若只 mock 最上层函数，很容易漏掉 channel 顺序、流式边界、崩溃恢复和真实终端行为；若每个测试都启动完整 binary，又会太慢、难定位且容易 flaky。因此必须分层。

---

## 3. 测试金字塔不是固定比例

Grok Build 的合理形状更像：

```text
             少量平台/真实系统测试
          PTY、Leader、真实 Binary E2E
       协议集成、Mock Server、Actor 组合
    大量纯函数、状态机、schema、序列化测试
 fuzz / snapshot / soak 横向覆盖特殊风险
```

关键不是追求某个百分比，而是让每个风险在最低、最确定的层级被验证一次，再用少量高层测试证明接线正确。

---

## 4. Rust 单元测试的组织方式

小模块通常在同文件：

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_known_value() { ... }
}
```

大型模块把测试拆到相邻文件，再用 `#[path]` 接入。例如 `acp_session.rs` 声明大量：

```text
acp_session_tests/prompt_queue_actor_tests.rs
acp_session_tests/rewind_cross_compaction_tests.rs
acp_session_tests/turn/chat_history_integrity_tests.rs
acp_session_tests/goal/goal_planner_e2e_tests.rs
```

这样测试仍能访问 crate-private 甚至父模块私有实现，又不让生产文件膨胀到不可阅读。

---

## 5. `tests/` integration binary

Crate 根下 `tests/*.rs` 被 Cargo 编译为独立 crate：

- 只能通过 public API 访问产品代码；
- 静态全局状态与其他 test binary 隔离；
- 可以拥有独立 main/harness 配置；
- 适合启动真实 binary、网络 server 或 PTY。

例如：

```text
crates/codegen/xai-grok-shell/tests/test_mcp_integration.rs
crates/codegen/xai-grok-shell/tests/test_fork_session.rs
crates/codegen/xai-grok-shell/tests/test_subagent_orphan_reconcile.rs
crates/codegen/xai-grok-pager/tests/pty_e2e_queue.rs
```

---

## 6. 为什么有些 target `harness = false`

Benchmark、自定义 runner 或需要自行管理进程生命周期的目标不使用 libtest 默认 main。Cargo.toml 的 `harness = false` 允许它们提供自己的入口。

这不等于测试被跳过，而是执行协议由目标自己定义。阅读 Cargo.toml 时必须同时看 target name、path、required-features 和 harness。

---

## 7. Feature-gated test support

部分 integration target 要求：

```toml
required-features = ["test-support"]
```

原因通常是测试需要公开额外构造器、mock seam 或 introspection，但生产二进制不应携带这些 API 和依赖。

正确边界是：feature 暴露测试接缝，不改变被测业务语义。

---

## 8. 三个测试支持 crate 的职责

### `xai-test-utils`

面向所有 xAI crate 的低层工具：hermetic Git、runfiles、tracing capture、环境数值解析等。

### `xai-grok-test-support`

面向 Grok Agent 集成：模型服务器、ACP stdio client、真实子进程、Sandbox、资源采样、UDS 故障代理。

### `xai-grok-pager-pty-harness`

面向终端：portable PTY、按键、resize、screen parser、场景步骤、退出回收、性能测量。

依赖方向由通用到具体，避免 Shell test helper 反向依赖 Pager。

---

## 9. `xai-test-utils` 的 hermetic Git

测试不能假设系统装有同一版本 Git，也不能读取用户全局 `.gitconfig`、credential helper 或 pager。

通用 helper：

- 在 Bazel 环境优先解析 runfiles 中的静态 Git；
- 为 Cargo 环境提供正常 fallback；
- 初始化临时 repo；
- 设置测试用户；
- commit fixture；
- 抑制 pager 和交互认证。

Git 测试失败时应反映产品逻辑，而不是开发机配置差异。

---

## 10. Cargo 与 Bazel 路径兼容

`crate_root!` 一类 helper 在 Bazel 下通过 runfiles 定位，在 Cargo 下使用 `CARGO_MANIFEST_DIR`。

当前开源工作区主要呈现 Cargo manifest，但源码中的 runfiles、`GROK_BINARY` 和测试注释保留了 Bazel test 接线。写 fixture 时不能硬编码 `target/debug` 作为唯一世界。

---

## 11. `TestSandbox` 是测试隔离核心

代码位于：

```text
crates/codegen/xai-grok-test-support/src/sandbox.rs
```

一个 Sandbox 拥有：

```text
temp root/
├── home/
│   └── .grok/
├── workspace/
└── tmp/
```

同时拥有一份稳定排序的 child environment map。`TempDir` Drop 时删除整棵目录。

---

## 12. 为什么 Sandbox 不修改父进程环境

构造 Sandbox 从不 `set_var`。启动 child 时先：

```rust
cmd.env_clear().envs(sandbox.env())
```

然后才应用测试显式 override。

好处：

- 并行测试互不争抢 `HOME`、`GROK_HOME`；
- 用户真实 API key 不会泄漏给 child；
- 父进程代理、MCP、配置不会意外影响测试；
- 失败可复现。

---

## 13. Sandbox baseline 包含什么

只保留平台运行所需变量，并设置：

- 独立 `HOME` / `USERPROFILE`；
- 独立 `GROK_HOME`；
- 独立 `TMPDIR` / `TMP` / `TEMP`；
- 网络 kill switches；
- 必要的 PATH/系统运行变量；
- 显式 mock endpoint 和测试 API key。

后续 `set_env`、`remove_env`、`extend_env` 是唯一受支持的 per-test 变化入口。

---

## 14. Sandbox 的 mock URL 扇出

`set_mock_url` 不只替换一个 chat endpoint，而是把模型、settings、feedback、trace、conversation、web 等流量都指向 loopback mock。

集成测试最危险的错误不是 mock 返回错，而是漏改一个 endpoint，让测试偶尔访问生产网络。集中扇出比每个测试手动设置十几个变量可靠。

---

## 15. Sandbox 的 Git 工作区

`TestSandboxBuilder::git()` 会：

1. `git init`；
2. 配置测试 name/email；
3. 创建 `README.md`；
4. add/commit 初始状态。

产品测试因此可以直接验证 dirty state、worktree、rewind 和 hunk tracking，无需重复样板。

---

## 16. 失败诊断也必须脱敏

Sandbox 的 `diagnostic_summary()` 会输出路径和非敏感配置，但：

- secret key value 显示 `<redacted>`；
- endpoint 只做安全化摘要；
- child 输出中的 endpoint、credential、私有 sandbox path 可被替换；
- 不打印完整 environment map。

测试失败日志会上传 CI，同样是数据泄漏面。

---

## 17. 何时才使用 `EnvGuard`

有些库代码直接读取当前进程环境，无法只改 child env。`EnvGuard`：

- 保存变量旧值；
- 设置或删除；
- Drop 时恢复旧值；
- assertion panic 时同样恢复。

但调用者必须标记 `#[serial_test::serial]`，因为 Rust 2024 中进程环境修改与其他线程并发读取不安全。

优先 Sandbox；只有无法注入的遗留全局配置才用 EnvGuard。

---

## 18. Named serial lock

`#[serial_test::serial(GROK_HOME)]` 只串行化共享相同 lock name 的测试，而不是把全仓库测试变成单线程。

不同全局资源使用不同名字：

```text
GROK_HOME
GROK_AGENT_DASHBOARD
MEMTRACE_SINK
jemalloc_heap_profile
```

这样既保证安全又保留最大并行度。

---

## 19. Mock Inference Server

代码位于：

```text
crates/codegen/xai-grok-test-support/src/mock_server.rs
```

它是真实 Axum HTTP server，绑定 loopback 随机端口，覆盖：

```text
/v1/chat/completions
/v1/responses
/v1/messages
/v1/models
/v1/settings
/v1/user
/v1/storage
/v1/privacy/coding-data-retention
```

测试的是实际 HTTP、JSON、header 和 SSE parser，而不是直接调用 sampler 内部函数。

---

## 20. 为什么随机端口允许并行

每个 MockServer 让 OS 分配独立端口，不共享全局 listener。只要测试不改父进程环境，就可以并行运行。

固定端口会造成：

- 本机已有服务冲突；
- 并行 test binary 互抢；
- 上一失败进程占端口；
- CI 偶发 bind error。

---

## 21. Mock Server 的响应优先级

模型 endpoint 按顺序选择：

1. 命名 expectation matcher；
2. 对应 path 的 scripted FIFO；
3. 当前 fallback mode。

Fallback 可以 echo 最后一条 user message，也可以返回固定文本。简单测试只设置 fixed response；复杂重试/多 turn 测试使用 expectation/FIFO。

---

## 22. 为什么请求日志有限长

Mock server 记录 method、path、body、Authorization 和到达顺序中的 headers，但最多保留约 1024 个请求，超出后淘汰最旧。

Soak 测试可能运行成千上万 cycle。无限保存完整 conversation request 会让测试工具自身成为内存泄漏来源。

请求总 count 独立用原子计数，即使关闭 body retention 仍可验证调用次数。

---

## 23. ScriptedResponse 是数据而非 handler closure

```rust
struct ScriptedResponse {
    status: u16,
    headers: Vec<(String, String)>,
    body: ScriptedBody,
}
```

Body 可以是 JSON、SSE events 或 raw bytes。状态码和 header 在 enqueue 时立即验证，让 fixture 错误在测试准备阶段失败，而不是请求到达后远处 panic。

数据化脚本更容易 clone、排队、打印和跨 harness 使用。

---

## 24. SSE 流如何被精确控制

Mock server 可以：

- 每 chunk 设置 delay；
- 在 terminal event 前等待 barrier；
- 发送 event name + data；
- 发送 raw malformed SSE；
- 模拟先文本、再 tool call、再 stop；
- 让流永久 hang。

这能稳定制造“用户在最后一个 chunk 前取消”“工具参数只收到一半”“terminal event 延迟”等竞态。

---

## 25. Terminal barrier 比 sleep 更可靠

若测试写：

```text
sleep 100ms，希望此时流到一半
```

在慢 CI 上可能尚未开始，在快机器上可能已结束。

更可靠做法：server 在 terminal 前 await oneshot/Notify；测试等观察到中间状态后主动 release。同步基于业务事件，不基于猜测时间。

---

## 26. Expectation matcher 验证请求

Expectation 可以匹配：

- endpoint 类型；
- request body 形状；
- model；
- 特定 message/tool；
-调用顺序或次数。

它不仅返回假响应，还验证 Agent 实际发出的上下文。例如 compaction 测试应断言第二次请求包含 compacted prefix，而不是只断言 UI 最终出现回答。

---

## 27. Mock `/v1/models` 与 agent invariant

`MockModelEntry` 可控制：

- model ID；
- agent type；
- API backend；
- backend search 支持；
- reasoning effort 能力和值。

这使 model switch、agent type mismatch、reasoning 设置等测试通过真实 catalog parsing，而不是直接构造最终内存对象。

---

## 28. Mock Storage 的 401 gate

Storage state 可动态切换 unauthorized，并记录已接受上传：

- request count；
- path；
- size；
-小于上限的 body；
- Authorization。

测试可验证上传在 401 时 park、认证恢复后 drain，而不会真的向云存储写数据。

大 body 只保留 size，不无限捕获内容。

---

## 29. Wiremock 与专用 Mock Server

仓库也使用 `wiremock`，适合：

- 一个或几个 HTTP endpoint；
- method/path/header matcher；
- 固定 response template；
- update/install 下载矩阵。

Grok 专用 MockInferenceServer 适合跨多个产品 endpoint、SSE 和动态 Session 场景。测试工具应匹配问题复杂度，不必所有网络测试都共用巨型 server。

---

## 30. ACP Stdio Client

`GrokStdioClient` 启动：

```text
grok agent stdio
```

通过真实 stdin/stdout 发送 typed ACP 请求，接收 notification 和 response。它验证：

- JSON framing；
- initialize/new/load/prompt；
- reverse request；
-消息顺序；
-子进程启动和退出。

这比在进程内直接调用 `Agent::prompt` 多覆盖了协议适配层。

---

## 31. Raw ACP Client 的必要性

Typed client 只能构造当前 Rust 类型允许的消息。兼容测试需要发送：

- legacy 方法名；
- 特殊转义；
- string UUID ID；
- malformed 或未来字段；
- typed API 会自动规范化掉的原始 bytes。

`RawStdioClient` 保留 wire 控制，防止“客户端库和服务端同时犯同一个错，测试仍绿色”。

---

## 32. Leader Fixture

Unix 下 `LeaderFixture` 驱动：

```text
grok agent --leader stdio
```

用于验证多客户端共享 Session、attach/reconnect、leader death、version skew 和 durable replay。

Leader test 必须同时控制 child tree、socket/UDS、client attach 顺序和模型 server，属于最高复杂度集成层。

---

## 33. UDS Fault Injection Proxy

普通 mock server 无法制造 IPC frame 只传一半、连接在特定边界断开等故障。`UdsProxy` 在 leader IPC socket 之间转发帧，并可注入：

- drop；
- delay；
-断连；
-指定帧边界故障。

它用于测试 Ack lost、重连和 durable log 等“成功与失败之间”的灰色状态。

---

## 34. `TestProcess` 是子进程所有者

代码位于：

```text
crates/codegen/xai-grok-test-support/src/process.rs
```

它统一负责：

- cleared environment；
- stdin pipe/null；
- stdout/stderr capture 或 caller-piped；
- graceful terminate；
-超时后 hard kill；
-进程树清理；
- bounded diagnostics；
- Drop 回收。

Fixture 必须拥有资源，而不是只返回一个裸 `Child` 让每个测试自行记得 kill。

---

## 35. 输出为什么只保存尾部

Child 可能输出无限日志。若测试等到结束才 `wait_with_output`：

- pipe buffer 可能塞满导致 child 死锁；
-内存无限增长；
-失败消息巨大。

TestProcess 后台持续 drain，只保存默认约 64 KB tail，并记录：

- total bytes seen；
-是否 truncated；
- read error。

失败时尾部通常最相关，同时不会阻塞 child。

---

## 36. 退出策略是分阶段的

典型策略：

```text
request graceful shutdown
        ↓ wait grace period
still alive?
        ↓ hard kill process tree
        ↓ bounded wait/reap
```

Termination enum 区分 NaturalExit、GracefulTerminate、HardKill、HardKillAfterGrace、DropCleanup，便于测试断言与诊断。

---

## 37. 为什么要清理进程树

杀主进程不一定杀掉：

- shell 创建的 command；
- MCP server；
-后台 task subprocess；
- PTY session descendants。

遗留进程会占端口、写旧 Session 文件、影响后续测试，甚至让测试 runner不退出。Process tree guard 是 E2E 稳定性的基础，不是附加清理。

---

## 38. Drop 清理也必须有界

测试 panic 时显式 teardown 不会执行，但 Rust Drop 会执行。Drop 不能无限 await，因此使用短回收预算，必要时硬杀，并把降级记录到 diagnostics。

理想测试正常路径显式 shutdown；Drop 是失败保险网。

---

## 39. 超时应按 harness 缩放

`xai_grok_test_support::scaled(base)` 读取正整数：

```text
GROK_TEST_TIMEOUT_SCALE
```

共享 CI runner 负载高时可以整体放大等待预算，而不修改每个测试。它不改变业务 timeout，只改变 harness 的“等测试条件出现”的预算。

---

## 40. 不能用放大 timeout 掩盖死锁

Timeout scale 只适合已知的机器调度差异。可靠测试仍应：

-等事件/状态而非固定 sleep；
-失败时打印 child tail 和 screen；
-给每个阶段独立 deadline；
-区分“条件未出现”和“进程已退出”；
-避免无上限 `recv().await`。

把 5 秒改成 120 秒不会修复竞态，只会让失败更慢。

---

## 41. Actor 测试如何构造真实状态机

`acp_session_tests/support.rs` 提供 `create_test_actor_ex` 等 builder，真实创建：

- SessionActor；
- ChatStateActor；
- MockFs；
- DummyTerminal；
- HunkTrackerActor；
- gateway/persistence/event channels；
- token/context/compaction 配置。

测试通过消息驱动 actor，观察 outbound channel，而不是直接改内部字段得到不可能的状态。

---

## 42. Null 与 Noop 依赖

当测试不关心某领域，可使用：

```text
NullChatPersistence
ToolNotificationHandle::noop()
ObservabilityBridge::new(None, ...)
PermissionHandle::allow_all()
```

Null object 比 `Option` 分支或 panic mock 更适合基础 builder：接口仍被真实调用，只是不产生副作用。

但权限专项测试必须换成真实/可控 permission manager，不能沿用 allow_all。

---

## 43. Fake 与 Mock 的区别

- DummyTerminal：固定返回错误，验证“不应调用”或错误路径。
- MockFs：内存中实现文件语义，可检查读写。
- MockInferenceServer：可编程并记录交互。
- NullPersistence：吞掉调用。
- Spy/capture channel：记录调用供断言。

选错 test double 会降低测试价值。例如需要验证顺序时，返回固定值的 stub 不够，必须有 spy 或真实 actor。

---

## 44. Channel 是天然测试接缝

Actor 架构允许测试：

1. 建立 mpsc/oneshot；
2. 启动 actor；
3. 发送 command；
4. 等特定 event；
5. 断言顺序和 Ack；
6. drop sender 让 actor drain/退出。

这比给 actor 加大量 `#[cfg(test)]` getter 更接近生产交互。

---

## 45. 测试异步顺序不能只断言最终集合

以下事件集合相同，但语义不同：

```text
正确：ToolCall -> ToolResult -> TurnCompleted
错误：TurnCompleted -> ToolCall -> ToolResult
```

测试需要按 receive 顺序逐条断言，尤其是：

- replay vs live；
- mode update FIFO；
- permission resolution；
- persistence Ack；
- subagent finish；
- queued prompt promotion。

---

## 46. Tokio 虚拟时间

支持 `#[tokio::test(start_paused = true)]` 的状态机可用：

```rust
tokio::time::advance(duration).await;
```

适合：

- heartbeat/liveness；
- backoff；
- idle timeout；
- admission window；
- MCP stable-stream threshold。

一个 60 秒 timeout 测试可以瞬间完成，并精确验证 `deadline - 1ms` 尚未触发、再 advance 1ms 正好触发。

---

## 47. 为什么网络测试不能总用 paused time

真实 reqwest/socket IO 仍依赖 runtime poll 和 wall-clock 进展。某些 wiremock 网络测试明确不用 `tokio::time::pause()`，而是减少 retry 次数、使用短 wall timeout 并允许随机端口并行。

虚拟时间只控制 Tokio timer，不会魔法般让操作系统 TCP 完成。

---

## 48. 注入 Clock trait

Circuit breaker 使用可注入测试 clock，测试直接 `clock.advance()`，不依赖 Tokio runtime。

对纯状态机，显式 Clock abstraction 往往比全局 paused runtime 更清晰：

- production 用 system/monotonic clock；
- test 用手动 clock；
-状态 transition 同步可断言。

---

## 49. 防止 lost wakeup

异步测试常见错误：先触发事件，再开始 `notified().await`，通知可能已经过去。

可靠方式：

- 先创建 receiver/future，再触发；
-使用 channel 缓存消息；
-用 Barrier/oneshot 表达一次性阶段；
-条件变量循环检查状态；
- server terminal barrier 由测试显式释放。

测试代码本身也需要并发设计。

---

## 50. Snapshot 测试

Pager 使用 `insta::assert_snapshot!` 固定复杂文本输出，例如：

- session usage status block；
- edit diff；
-多 hunk；
- dual view；
- reflow 和三位数行号。

Snapshot 适合“输出大且整体变化有意义”，比几十个字符级 assert 更可读。

---

## 51. Snapshot 不适合什么

不要 snapshot：

-随机 UUID；
-绝对临时路径；
-当前时间；
-HashMap 非稳定顺序；
-巨大完整协议 payload；
-只关心一个 bool 的结果。

先 normalize 不稳定字段；对于关键 invariant 仍使用明确 assert，避免 reviewer 机械接受大 snapshot 更新。

---

## 52. TUI 单元渲染测试

在进入 PTY 前，很多 UI 逻辑可用 Ratatui TestBackend 或纯 render function 验证：

- widget layout；
- selection index；
- modal state；
- diff text；
- unicode width；
-输入 reducer。

这类测试快、失败定位清楚。只有 terminal driver、raw mode、scrollback、mouse/clipboard 等才需要 PTY。

---

## 53. 为什么 TUI 最终仍需真实 PTY

TestBackend 无法完整模拟：

- crossterm event bytes；
- bracketed paste；
- terminal resize/SIGWINCH；
- ANSI alternate screen；
- native scrollback；
- OSC 8 hyperlink；
- mouse reporting；
- Ctrl+C 对进程组的影响；
- child shell 与 controlling TTY。

因此 Pager 有独立 PTY harness。

---

## 54. PTY Harness 的分层

```text
Layer 1: PtyController
  spawn、raw key、resize、output chunks、exit/reap

Layer 2: screen/content
  VTE 解析、可见文本、ANSI/OSC、屏幕状态

Layer 3: flows/scenarios
  welcome、submit、wait sentinel、permission、plan 等业务步骤

Layer 4: tests/benchmarks
  correctness、latency、stress、resource assertions
```

低层只理解 bytes 和 process，高层才理解“回答出现了”。

---

## 55. PTY 启动环境

内容驱动的 Pager 必须使用 `spawn_in_sandbox`：清空环境后应用 TestSandbox baseline 和 typed `EnvOp::Set/Remove`。

只有专门测试 host terminal brand/probe/继承行为的场景才使用 `spawn_inherited_env`。

这种 API 命名让不安全继承成为显式选择。

---

## 56. 原始按键注入

Harness 写真实 byte sequence：

```text
Enter    \r
Ctrl+C   0x03
Esc      0x1b
Arrow    ESC [ A/B/C/D
PageDown ESC [ 6 ~
F2       ESC O Q
```

因此能验证 terminal parser，而不仅是调用内部 `on_key(KeyCode::Enter)`。

Paste、IME、不同 terminal chord 还会使用更复杂字节序列。

---

## 57. Screen sentinel 等待

测试不应每 10ms snapshot 整个 ANSI raw output 并做精确相等。常见做法：

- VTE parser 增量消费 chunk；
-重建可见 screen；
-等待稳定 sentinel text；
- deadline 到时输出 raw tail、screen、process-tree diagnostics。

Sentinel 必须短、唯一且不易换行，例如 `MOCKRESPONSE_T1`。

---

## 58. PTY 尺寸是 fixture 的一部分

默认约 50 行 × 120 列，足以显示 welcome，又让扫描便宜。

测试滚动和换行时尺寸直接影响行为。若不固定 rows/cols，同一 Markdown 在 CI terminal 与本机可能产生不同布局。

Resize 测试显式调用 PTY resize，让 child 收到真实 SIGWINCH。

---

## 59. PTY 测试按调度族拆分

Cargo.toml 将大量 case 拆成：

```text
pty_e2e_smoke
pty_e2e_queue
pty_e2e_scroll_selection
pty_e2e_minimal
pty_e2e_config_ui
pty_e2e_shell_tools
pty_e2e_persistence
pty_e2e_clipboard
leader_pty_e2e
```

每个 root 用 `#[path]` 收纳相关 case。这样 CI 可以按风险/资源调度，不必一个巨大 binary 串行或全部重编译。

---

## 60. 为什么多数 PTY case 是 `#[ignore]`

它们：

-需要先构建 Pager binary；
-跨真实进程和 PTY；
-耗时高；
-部分依赖 OS/terminal 能力；
-不适合每次普通 `cargo test`。

Ignore message 应写明所属 target 和运行命令，例如带 `--ignored --test-threads=1`。

默认测试仍可验证 scenario YAML/fixture 可解析，避免 ignored 测试长期腐烂到连编译/加载都失败。

---

## 61. PTY 退出与 portable-pty 的特殊问题

Portable PTY 在 Unix kill/try_wait 路径可能已经 reap child。Harness 缓存 exit status，避免重复 query 一个已回收 child。

同时附加 `TestProcessTree`：

-正常 quit 发送 `q`；
-超时清 descendants；
-再 kill portable child；
- bounded wait；
-Drop 再兜底。

退出测试和业务测试同样重要，否则整个 suite 会被孤儿进程污染。

---

## 62. Queue/Interjection E2E 验证什么

典型 case：

- mid-turn queue 后 Ctrl+C；
- queued prompt 被 promote；
- interjection 进入同一 turn；
-发送一次、UI 只渲染一次；
- remove 后绝不送模型；
- Bash queue 保留 Bash 类型；
- Esc 取消时 draft 保留。

这些是 channel 和 UI reducer 交叉行为，仅单测 PromptQueue 不足以证明端到端 exactly-once。

---

## 63. Persistence PTY 验证什么

包括：

- `--continue` 恢复历史；
- title rename 进入边框；
-等待/park marker；
- quit 后后台 task 回收；
- storage 401 恢复后 drain；
- spinner 在 wait resume 后重现。

它们验证“磁盘事实 + ACP replay + UI”完整链路。

---

## 64. Leader PTY 为什么常要求单线程

Leader case 可能竞争：

-全局 leader discovery；
- socket path；
-共享 Session；
-多个真实客户端；
-进程组信号；
-较大时间预算。

因此运行建议明确 `--test-threads=1`。这不是为掩盖普通数据竞争，而是测试场景本身模拟单个系统级 leader 资源。

---

## 65. Scripted Scenario

场景 runner 把操作数据化：

```text
spawn
wait text
inject keys
resize
set mock response
release barrier
assert screen
quit
```

数据驱动场景便于复现 bug、手工运行和基准复用。场景文件/DSL 的解析验证应进入默认 suite，即使完整执行 ignored。

---

## 66. Fuzz Testing

Markdown crate 使用 `cargo-fuzz` + libFuzzer：

```text
crates/codegen/xai-grok-markdown/fuzz/fuzz_targets/render_all.rs
```

每个输入覆盖八种组合：

```text
pretty / non-pretty
syntect / no-syntect
full / char-by-char streaming
```

种子包括 table、code、math、list、emoji、Unicode 等。

---

## 67. 为什么 Markdown 特别适合 fuzz

输入空间巨大且攻击性强：

-未闭合 fence；
-嵌套 list/table；
-任意 Unicode combining/width；
-逐字符 streaming 边界；
-极长 token；
-混合 LaTeX/code/emoji。

手写 example 难覆盖组合爆炸。Fuzzer 更擅长寻找 panic、越界、死循环和 full/streaming 不一致。

---

## 68. Fuzz crash 如何产品化

找到 crash 后：

1. artifact 保存最小化输入；
2. 用 `cargo +nightly fuzz run ... crash-hash` 重现；
3. 修复；
4. 将输入加入 checked-in seed 或常规 regression test；
5. 保留 invariant，避免只针对 hash 特判。

Fuzz 发现 bug，稳定 regression test 防止它回来。

---

## 69. Soak Testing

Subagent soak 反复执行 foreground/background/concurrent 生命周期，测量：

- RSS；
- thread 数；
- open file 数；
-可选 DHAT heap bytes；
-每 cycle 增长斜率；
- quiesce 是否完成。

它不是看一次操作是否成功，而是验证长期资源有界。

---

## 70. Soak 的 warmup、baseline 与 measured window

正确资源测试需要：

```text
warmup
  ↓ 初始化 lazy cache/thread pool
concurrent phase
  ↓ 覆盖峰值路径
quiesce
  ↓ 等后台任务排空
baseline snapshot
  ↓
N measured cycles
  ↓
quiesce + final snapshot
```

若 baseline 前未 warmup，会把正常一次性初始化误判为泄漏；若 final 前未 quiesce，会把仍在工作的资源误判为 retained。

---

## 71. Soak 不能只比较前后差值

测试计算每-cycle 增长并对多维资源设 bound。某些平台无法采某指标时应标记 unavailable，不能把缺数据当零。

输出机器可读 `SUBAGENT_SOAK_SUMMARY` JSON，方便 CI 聚合趋势。若 quiesce budget 超时，结果标为不可靠而不是硬凑结论。

---

## 72. Benchmark 与正确性测试不同

Criterion/PTY benchmark 测：

- render throughput；
- search；
- edit highlight；
- resize；
- paste latency；
- streaming render/scroll stress。

正确性测试问“结果是否满足 invariant”；benchmark 问“分布和回归是否可接受”。不要用脆弱的 wall-clock `assert!(elapsed < 10ms)` 替代基准框架。

---

## 73. 平台特定测试

一些功能只能在：

- Linux cgroup v2 delegation；
- macOS real pasteboard；
- Windows console/job；
- Unix signal/process group；
-真实 filesystem watcher；
-实际 clipboard。

代码使用 `#[cfg]` 隔离无法编译的平台，用 `#[ignore = "requires ..."]` 标记需要环境的执行条件。

---

## 74. Ignore 不是墓地

合理 ignored：

-硬件/OS 权限要求；
-真实 binary/PTY 长流程；
-soak/benchmark；
-破坏性或手工验收；
-已知 CI 环境不具备能力。

危险 ignored：

-“flaky”但无 issue、原因和替代覆盖；
- `unimplemented!()` stub 长期存在；
-已知 broken snapshot 没有 owner；
-默认 suite 不再编译其 fixture。

每个 ignore 都应能回答何时运行、谁关注、默认层有什么替代保护。

---

## 75. Flaky 测试通常暴露什么

仓库中的注释展示了常见根因：

- output buffer 未在 kill 前 flush；
- `pgrep` 看到了其他 sandbox tenant；
- filesystem notification 在 CI 丢失；
-真实 clipboard 不可用；
- PTY 调度下固定 timeout 太小；
-对子进程状态重复 reap。

修复方向通常是增强隔离和可观察同步，而不是简单 retry 整个测试。

---

## 76. 测试重试为什么危险

自动 retry 能让 CI 变绿，但会隐藏：

-数据竞争；
-lost wakeup；
-未清理全局状态；
-顺序依赖；
-真实产品间歇性 bug。

如果必须临时 retry，应记录每次失败、限制次数、建立追踪，并继续修复同步条件。测试成功率本身也是信号。

---

## 77. 持久化故障测试

JSONL/storage 测试应覆盖：

-完整 append；
-末尾无换行；
-半个 UTF-8；
-中间坏 JSON；
-并发 writer lock；
- sync/rename 失败；
- committed vs not committed；
- corrupt backup；
- legacy/current 混合；
-copy 前 flush barrier。

需要断言磁盘 bytes 和返回提交状态，不能只看 reload 最终“差不多成功”。

---

## 78. Replay/Rewind 测试

高价值矩阵：

- cursor 命中/丢失；
- eventId-less tail；
- replay 中 live append；
- AvailableCommandsUpdate cursor；
- ToolCall 状态合并；
- target 在 checkpoint 前/后；
-多次 RewindMarker；
- checkpoint 缺失/损坏/版本过新；
-文件 after snapshot 冲突。

这类测试最好使用合成事件流，精确控制每行，而不是启动整套 UI。

---

## 79. 安全测试

测试必须验证拒绝路径：

- symlink escape；
- `..` traversal；
- deny read glob；
- untrusted folder 不执行本地配置；
- Bash scope 不因文本前缀绕过；
- managed policy 不能被用户配置放宽；
- sandbox leader confinement；
- hook write deny；
- secret 不出现在 diagnostics/telemetry。

安全测试要断言副作用没有发生，而不只是返回了一个 error 字符串。

---

## 80. Protocol compatibility 测试

对 ACP、SSE、Chat/Responses/Messages backend，要覆盖：

- unknown fields ignored；
- old envelope accepted；
- camelCase/snake_case wire contract；
- typed/raw client roundtrip；
- malformed frame 安全失败；
- vendor-specific stop reason；
- tool delta 拼接；
- response size 上限；
- version skew。

序列化 snapshot 和 raw-wire fixture 都有价值。

---

## 81. 观测系统的测试

可观测性不能只“看有没有 log”：

- EventTracker TurnEnded 防重；
- active tool cancel 补 completed；
- tracing capture 统计特定 message prefix 次数；
- external schema name/unit/key 固定；
-默认 content gate 隐藏 prompt/tool detail；
- secret/path scrub；
- traceparent 端到端传播；
- debug session router；
- test binary 重定向 Unified Log，不污染开发机。

Telemetry schema 是对 dashboard 的 API，应有 contract test。

---

## 82. Test-only tracing capture

`xai-test-utils::tracing_capture` 和模块内 CaptureLayer 可以：

-安装 thread-local subscriber；
-收集 level 与 fields；
-统计 message prefix；
-断言 warning 只出现一次；
-验证 fallback path 被调用。

比读取全局日志文件更确定，也避免测试互相混入事件。

全局 subscriber 只能安装一次，优先使用 scoped/default guard。

---

## 83. Assertion 设计

好的错误消息应包含：

-预期业务阶段；
-实际事件序列；
-请求日志 tail；
-child stdout/stderr bounded tail；
-screen contents/raw ANSI snippet；
-Sandbox 安全摘要；
-资源 snapshot；
- timeout 所在步骤。

`assert!(condition)` 没有上下文时，CI 上往往无法复现。

`pretty_assertions` 适合复杂结构 diff，snapshot 适合稳定大文本。

---

## 84. 避免过度 Mock

若把 Session、ChatState、ToolBridge、Persistence、ACP 全部 mock，测试验证的只是 mock 之间按预设对话。

推荐原则：

-纯逻辑用直接输入；
-actor 保持真实，替换外部 IO；
-协议 server 保持真实 HTTP/SSE，替换远端服务；
-TUI 保持真实 binary/PTY，替换模型内容；
-系统权限测试才依赖真实 OS feature。

每层只 fake 它无法稳定拥有的边界。

---

## 85. 测试数据 Builder

复杂 struct 若每个测试写完整 literal，会导致新增字段时数百测试机械修改。Support 中提供：

- `test_agent_default`；
- `test_agent_with_tools`；
- `test_agent_with_plan_tools`；
- `create_test_actor_ex`；
- `MockModelEntry` builder；
- `TestSandboxBuilder`；
- `TestProcessConfig`。

Builder 应提供安全、最小、明确的默认值；专项字段由测试显式覆盖。

---

## 86. Builder 也可能隐藏重要默认

如果 builder 默认 `allow_all`，权限测试忘记覆盖就会产生假阳性；如果默认 `/tmp` 共享路径，并行测试可能互相污染。

因此：

-危险默认在名字中体现，例如 `test_actor_allow_all`；
-文件路径使用每测试 TempDir；
-关键策略在专项测试显式传入；
-builder 自身有 contract test；
-不要为了少写几行把所有真实配置藏起来。

---

## 87. 本地运行策略

从最小范围开始：

```sh
cargo test -p <crate> <test_name>
cargo test -p <crate> --test <integration_target> <case>
cargo test -p xai-grok-shell --features test-support <case>
```

需要日志：

```sh
RUST_LOG=xai_grok_shell=debug cargo test ... -- --nocapture
```

Ignored PTY/soak 按源码 message 给出的目标运行，必要时：

```sh
... -- --ignored --test-threads=1 --nocapture
```

不要一开始跑整个 workspace 来定位一个 Session reducer bug。

---

## 88. 变更类型与测试选择

| 变更 | 最低验证 |
| --- | --- |
| parser/schema | 单元 + malformed/legacy |
| actor transition | channel 顺序测试 |
| persistence | tempdir + crash/torn-tail |
| HTTP/SSE | MockServer integration |
| ACP wire | stdio typed + raw case |
| TUI reducer | in-process render/state |
| terminal behavior | PTY target |
| concurrency bug | barrier/fault injection + repeated stress |
| resource leak | soak |
| untrusted input renderer | fuzz corpus |
| performance hot path | Criterion/PTY benchmark |

高层 E2E 不能替代低层边界矩阵；低层测试也不能证明最终 wiring。

---

## 89. CI 分层建议

### 每次提交

- format/clippy/check；
-默认快速 unit/integration；
-schema/snapshot；
-少量 smoke。

### Merge lane

- test-support features；
-协议/真实 binary；
-分片 PTY family；
-平台矩阵。

### Nightly/定期

- fuzz time budget；
-soak；
-ignored platform tests；
-benchmark comparison；
-高并发 leader/registry churn。

分层让开发反馈快，同时不放弃慢风险。

---

## 90. 测试可靠性的不变量

### 隔离

测试不得读写用户真实 HOME/GROK_HOME，不得访问生产网络。

### 有界

所有 wait、输出 capture、请求日志、child shutdown 都有上限。

### 可拥有

端口、TempDir、child、worker、subscriber guard 由 fixture RAII owner 持有。

### 可诊断

失败打印最近状态而不泄漏 secret。

### 可重复

使用随机端口、固定输入、稳定排序和事件 barrier。

### 语义真实

尽量保留被测层的真实协议和状态机，只替换不可控外部边界。

---

## 91. 现有体系还可优化什么

### 91.1 统一 Test Runtime

集中创建 paused/current-thread/multi-thread runtime profile，避免测试随意选择导致行为差异。

### 91.2 Fixture Leak Audit

每个 integration binary 结束时检查 child、thread、fd、临时 socket 是否回到 baseline。

### 91.3 Ignore Registry

生成所有 `#[ignore]` 的原因、命令、平台、owner、最后成功时间，防止沉积。

### 91.4 Deterministic Scheduler Hooks

在关键 actor channel 边界提供测试 barrier，使并发回归不靠大循环碰运气。

### 91.5 Protocol Fixture Corpus

集中保存 ACP/SSE/JSON legacy、malformed、version-skew 语料，供 parser、raw client 和 fuzz 共用。

### 91.6 Coverage by Invariant

按“事件不重复、权限不放宽、child 必回收、replay 不丢 gap”等 invariant 追踪覆盖，而不仅是行覆盖率。

---

## 92. 推荐源码阅读顺序

第一组，通用工具：

```text
crates/common/xai-test-utils/src/lib.rs
crates/common/xai-test-utils/src/git.rs
crates/common/xai-test-utils/src/runfiles_util.rs
crates/common/xai-test-utils/src/tracing_capture.rs
```

第二组，Grok 集成 fixture：

```text
crates/codegen/xai-grok-test-support/src/lib.rs
crates/codegen/xai-grok-test-support/src/sandbox.rs
crates/codegen/xai-grok-test-support/src/process.rs
crates/codegen/xai-grok-test-support/src/mock_server.rs
crates/codegen/xai-grok-test-support/src/scripted.rs
crates/codegen/xai-grok-test-support/src/acp_client.rs
crates/codegen/xai-grok-test-support/src/leader.rs
crates/codegen/xai-grok-test-support/src/uds_proxy.rs
```

第三组，Session actor 测试：

```text
crates/codegen/xai-grok-shell/src/session/acp_session.rs
crates/codegen/xai-grok-shell/src/session/acp_session_tests/support.rs
crates/codegen/xai-grok-shell/src/session/acp_session_tests/prompt_queue_actor_tests.rs
crates/codegen/xai-grok-shell/src/session/acp_session_tests/rewind_cross_compaction_tests.rs
crates/codegen/xai-grok-shell/src/session/storage/jsonl/durable_tests.rs
```

第四组，PTY：

```text
crates/codegen/xai-grok-pager-pty-harness/src/pty.rs
crates/codegen/xai-grok-pager-pty-harness/src/screen.rs
crates/codegen/xai-grok-pager-pty-harness/src/flows.rs
crates/codegen/xai-grok-pager/tests/pty_e2e/common.rs
crates/codegen/xai-grok-pager/tests/pty_e2e_queue.rs
crates/codegen/xai-grok-pager/tests/leader_pty_e2e/mod.rs
```

第五组，专项可靠性：

```text
crates/codegen/xai-grok-tools/tests/test_subagent_soak.rs
crates/codegen/xai-grok-markdown/fuzz/fuzz_targets/render_all.rs
crates/codegen/xai-grok-pager/src/scrollback/blocks/tool/edit.rs
crates/common/xai-computer-hub-sdk/src/connection.rs
```

---

## 93. 常见误区

### 误区一：Mock 越多，测试越隔离

错。过度 Mock 会让测试脱离真实状态机和协议。

### 误区二：TempDir 就代表 hermetic

错。子进程仍可能继承 HOME、代理、API key 和系统 Git 配置。

### 误区三：有 timeout 就不会挂

错。后台 child、reader worker 和 process descendants 仍需 owner 清理。

### 误区四：`#[serial]` 可以修所有 flaky

错。它只解决明确的全局资源竞争，会掩盖本应并发安全的代码。

### 误区五：Snapshot 通过就等于 UI 正确

错。Snapshot 看静态输出，PTY 才覆盖真实 terminal interaction。

### 误区六：Ignored 测试不重要

错。它们覆盖平台、soak 和真实终端风险，但必须被定期调度。

### 误区七：扩大 sleep 可以稳定并发测试

错。应使用事件 barrier 和可控 scheduler seam。

---

## 94. 最终心智模型

测试一个 Agent，不是制造一个“看起来像模型”的 stub，然后断言最后字符串。可靠方法是按边界逐层拥有不确定性：

```text
纯逻辑
  用固定输入验证不变量
        ↓
Actor
  用 channel 和 barrier 验证状态顺序
        ↓
外部服务
  用 loopback HTTP/SSE mock 保留真实协议
        ↓
进程协议
  用隔离 Sandbox + ACP stdio 驱动真实 binary
        ↓
终端
  用 PTY、raw keys 和 screen parser 验证最终体验
        ↓
长期与未知输入
  用 soak、fuzz、benchmark 和平台测试补足
```

同时，每个测试 fixture 都必须回答五个问题：

1. 它修改了哪些全局状态？
2. 它拥有的文件、端口、进程和线程何时释放？
3. 它等待的条件是业务事件还是猜测性的 sleep？
4. 失败时留下哪些有界且脱敏的诊断？
5. 它保留了多少真实生产语义，又替换了哪个外部边界？

Grok Build 测试体系最值得学习的地方，是把“可测试性”落实在架构接缝上：Actor 用 channel、网络用本机协议服务器、进程用 RAII owner、环境用 cleared child map、终端用真实 PTY、时间用虚拟 clock、持久化用临时目录和故障状态。测试不是生产代码旁边的一堆断言，而是另一套同样需要资源所有权、并发正确性、安全边界和可观测性的系统。
