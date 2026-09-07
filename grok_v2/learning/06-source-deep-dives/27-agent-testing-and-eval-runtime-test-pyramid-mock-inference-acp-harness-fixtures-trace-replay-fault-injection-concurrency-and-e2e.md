# 源码精读 27：Agent Testing & Eval Runtime——测试金字塔、Mock Inference、ACP Harness、Fixtures、Trace Replay、故障注入、并发与 E2E

> 源码基线：`ed6d543`
>
> 上一篇研究 Goal Runtime 怎样判断一个长期目标是否真正完成；本篇换一个更高层的问题：我们怎样判断“负责判断完成的 Agent 系统本身”是正确的？答案不是再调用一次 LLM，而是把不确定系统拆成可重复、可观测、可注入故障的测试层。

---

## 1. 本篇解决什么问题

读完后应能回答：

- Agent 为什么不能只靠几个端到端测试？
- 什么逻辑适合纯函数测试，什么必须进入 Actor、ACP 或 PTY？
- 测试如何替代真实模型，同时保留真实 HTTP/SSE 协议？
- 怎样区分 foreground 模型请求与 title、classifier 等 auxiliary 请求？
- 怎样稳定复现 streaming、retry、cancel 和重复请求竞态？
- Fixture、snapshot、trace replay、scripted scenario 各是什么？
- Goal Planner、Evaluator、Skeptic Panel 分别怎样测试？
- 为什么“没有请求”“只请求一次”“历史只出现一次”也是核心断言？
- 故障注入为什么必须落在协议 frame、terminal event、文件 symlink 等精确边界？
- 如何给新的 Agent 功能设计一套成本合理、定位清晰的验证矩阵？

---

## 2. 先给出总图

```text
                    ┌──────────────────────────────┐
                    │ Real binary + PTY + screen   │
                    │ 用户真正看到和操作的终端行为 │
                    └──────────────▲───────────────┘
                                   │
                    ┌──────────────┴───────────────┐
                    │ Subprocess ACP / Headless E2E │
                    │ 真实进程、stdio、HTTP mock     │
                    └──────────────▲───────────────┘
                                   │
                    ┌──────────────┴───────────────┐
                    │ In-process ACP + SessionActor │
                    │ actor、持久化消息、tool bridge │
                    └──────────────▲───────────────┘
                                   │
                    ┌──────────────┴───────────────┐
                    │ Protocol / fixture / replay   │
                    │ JSON、SSE、JSONL、状态兼容性    │
                    └──────────────▲───────────────┘
                                   │
                    ┌──────────────┴───────────────┐
                    │ Pure functions/state machines │
                    │ parser、gate、tracker、quorum  │
                    └──────────────────────────────┘
```

越靠下：

- 运行快；
- 输入可穷举；
- 失败定位准；
- 但覆盖的真实集成边界少。

越靠上：

- 更接近用户；
- 能发现 wiring、进程、终端和时序错误；
- 但更慢、更脆弱，失败原因更宽。

正确策略不是选择某一层，而是让同一关键契约在相邻层形成证据链。

---

## 3. 这里的 Eval 不只是“评测模型质量”

Agent 领域常把 eval 理解成：给一组任务，计算成功率。

本仓库展示的是更广义的验证：

```text
模型输出质量
+ Runtime 决策正确性
+ 协议兼容性
+ 状态持久化与恢复
+ 并发线性化
+ 安全边界
+ 用户界面呈现
+ 资源与性能约束
```

一个模型答对了，但 Runtime 重复执行 Tool、丢失完成事件或恢复后重发请求，产品仍然是错的。

---

## 4. 第一原则：把概率性挡在测试边界之外

生产系统依赖 LLM，测试却不能依赖“这次模型大概会按要求回答”。

因此测试把模型替换为确定性来源：

- 固定文本；
- FIFO scripted responses；
- 按请求类型匹配的 named expectations；
- 自定义 SSE event 序列；
- subagent coordinator stub；
- JSON fixture 中声明的预期结果。

测试的目标不是模拟模型有多聪明，而是控制 Runtime 收到的每一种输出形状。

---

## 5. 核心源码地图

| 领域 | 主要源码 |
|---|---|
| 通用测试支持 | `crates/codegen/xai-grok-test-support/src/` |
| Mock inference | `mock_server.rs`、`inference_override.rs`、`scripted.rs`、`sse.rs` |
| ACP 子进程客户端 | `acp_client.rs` |
| Hermetic 环境 | `sandbox.rs`、`process.rs` |
| IPC 故障注入 | `uds_proxy.rs` |
| Session 测试 Actor | `xai-grok-shell/src/session/acp_session_tests/support.rs` |
| 内存 ACP Harness | `xai-grok-shell/src/session/testkit/e2e.rs` |
| 合成 Session | `session/testkit/synth/` |
| Todo trace replay | `xai-grok-shell/tests/trace_replay.rs` |
| Goal 状态机 | `goal_tracker.rs` |
| Goal Evaluator | `goal_evaluator.rs` |
| Goal Skeptic Panel | `goal_classifier.rs` |
| Goal Strategist | `goal_strategist.rs` |
| Goal Planner E2E | `acp_session_tests/goal/goal_planner_e2e_tests.rs` |
| PTY 场景 | `xai-grok-pager/tests/pty_e2e/`、`tests/scenarios/` |

---

## 6. 为什么测试支持是独立 crate

`xai-grok-test-support` 被多个 crate 的 integration tests 复用，提供：

- MockInferenceServer；
- GrokStdioClient / RawStdioClient；
- Headless runner；
- TestSandbox；
- TestProcess；
- Leader fixture；
- UDS fault proxy；
- RSS、thread、fd 资源采样。

独立 crate 能避免每个测试套件重新发明 mock server 和子进程清理逻辑。

---

## 7. 为什么 Session synth 反而留在 shell crate

`session::testkit` 注释明确说明：合成 Session 需要驱动真实 `JsonlStorageAdapter`。

若把它放进通用 test-support，再反向依赖 shell，就会形成循环依赖。

所以测试代码的位置也受生产依赖图约束：

```text
xai-grok-shell ──dev-dep──> xai-grok-test-support
      │
      └── 内部 testkit 可直接调用自身 storage
```

“测试工具都放一个 crate”不是绝对规则；不能破坏依赖方向。

---

## 8. 最底层：纯函数测试

适合纯函数测试的对象包括：

- JSON parser；
- enum wire mapping；
- quorum 聚合；
- blocker streak；
- gap fingerprint；
- todo gate；
- path formatter；
- prompt template renderer；
- config precedence；
- 状态迁移。

这类测试不需要网络、Actor 或真实时间，应该承担最大的边界组合覆盖。

---

## 9. GoalTracker 为什么有大量单元测试

`GoalTracker` 是相对纯的状态机，因此能直接验证：

- create → active；
- pause/resume/complete/budget-limit；
- terminal 状态不可错误恢复；
- elapsed time 刷新；
- history 上限；
- strategist streak；
- blocker key 连续性；
- serializer backward compatibility；
- restart 后 Active 降级为 UserPaused；
- scratch path 与 symlink 防护。

这些若只从完整 Session 入口测试，失败时很难知道是状态机、Actor 还是协议 wiring 出错。

---

## 10. 状态迁移测试的关键不是 Happy Path

真正高价值的是非法边：

```text
Complete --resume--> 不允许
BudgetLimited --resume--> 不允许
Complete --pause--> no-op
unknown future status --restore--> UserPaused
Active snapshot --restart--> UserPaused
```

它们证明 Runtime 不会在版本变化或重启后未经用户同意继续自治。

---

## 11. 兼容性测试为什么直接构造旧 JSON

GoalTracker 测试内嵌 legacy JSON，故意缺少新字段或带旧字段：

- 缺失字段应获得安全 default；
- 已删除的 `tokens_used` 应被忽略；
- PascalCase 旧状态仍可读取；
- 未知未来状态不得恢复成 Active。

只做“当前 struct 序列化再反序列化”只能证明同版本自洽，不能证明升级兼容。

---

## 12. Round-trip 测试能证明什么

```rust
let json = serde_json::to_string(&state)?;
let restored = serde_json::from_str(&json)?;
assert_eq!(...);
```

它能证明：

- 当前 writer 与 reader 对称；
- 需要持久化的字段没有意外丢失；
- wire naming 符合预期。

它不能证明：

- 老版本数据仍兼容；
- 新版本未知字段安全；
- 磁盘写入具备原子性；
- Actor 恢复时做了正确 reconciliation。

所以还需要手写旧 fixture 和更高层 restore 测试。

---

## 13. Parser 必须拒绝“差不多正确”

Goal Evaluator 测试覆盖：

- 三种 decision 的合法 JSON；
- 未知 decision；
- 额外字段；
- 空 evidence / next_step；
- blocker_key 的条件约束。

验证模型输出时，宽松 parser 会把模型漂移藏起来。结构化输出的测试价值在于固定边界，而不是尽可能猜测模型意图。

---

## 14. Prompt 也是可测试的程序输出

Planner、Verifier、Strategist 的 template tests 会断言关键合同语句仍存在，例如：

- objective fidelity；
- acceptance criteria；
- shared verification plan；
- blocking 与 fixable 的分类；
- 不得篡改 plan；
- scratch 路径占位符必须被替换。

Prompt 不是普通文案，它会改变 Agent 的控制策略，应像 API schema 一样受到 regression guard。

---

## 15. Prompt 测试不要只做全文 golden

本仓库大量使用语义子串断言，而非所有 Prompt 都做完整 snapshot。

优点：

- 文案小改不会制造海量无意义 diff；
- 直接固定真正不可丢失的合同；
- 失败消息可以指出缺失的能力。

但关键默认模板仍有“完整 render 保留 canonical text”测试，用来防止模板引擎或占位符替换破坏整体。

---

## 16. Goal quorum 为什么适合表驱动测试

Skeptic 聚合是纯规则：

```text
输入：每个 skeptic 的 refuted/confidence/blocking
输出：Achieved / NotAchieved / Blocked
```

测试覆盖 N=1..5、cold panel 门槛、skeptic0 decisive refute、mixed blocking/fixable 等组合。

这比真的启动多个 subagent 更适合验证数学规则。

---

## 17. 单调性测试比几个样例更强

源码有测试检查 `required_cold_approvals` 随 panel 大小的变化。

这种测试不只问“3 个时是不是 2 票”，还问一个性质：

> 增加 verifier 数量时，批准门槛不能反常下降。

这是 property-like test。仓库没有依赖 property-testing 框架，也可以用循环验证不变量。

---

## 18. 字符串截断也必须测 UTF-8 边界

Verifier evidence、prior gaps、panel details 都有字节/字符上限。

测试不仅覆盖：

- 上限以下不变；
- 刚好等于上限；
- 超过一位添加省略；

还覆盖多字节字符不能被切在 UTF-8 中间。

Agent Runtime 经常拼接来自模型的任意 Unicode，字节 cap 若处理错误会直接造成 panic 或非法 JSON。

---

## 19. 控制标记注入也属于测试面

模型 evidence 可能包含 reminder tag 或类似控制帧 token。

测试验证它们在进入下一轮 Prompt 前被 neutralize，防止“验证报告中的普通文本”被重新解释成系统级提醒。

这是一类 Agent 特有安全测试：数据跨轮回流时必须保持 data，而不能升级成 instruction。

---

## 20. Path 测试不是琐碎实现细节

Goal verifier 会读写 scratch、details、changes patch。测试覆盖：

- 正常 scratch-rooted path；
- `..` traversal；
- NUL；
- 未解析 placeholder；
- `/etc`；
- `~`；
- root 外路径；
- symlink squat；
- destination symlink；
- file/dir 抢占。

LLM 能影响文件路径时，路径验证就是核心安全契约。

---

## 21. “Fail open”也必须被精确测试

历史类型名可能叫 `FailOpenAchieved`，但上层新路线会将 verifier infra failure 截获并暂停 Goal。

测试应分别验证：

- 低层函数的返回类型；
- 上层 Actor 如何解释；
- 是否回滚 attempt；
- 是否写 placeholder details；
- 是否错误完成 Goal。

只看一个函数名或一层断言，很容易对最终用户行为得出相反结论。

---

## 22. 第二层：Fake / Stub，而不是完整 Mock Server

Goal Planner E2E 没有启动真实 HTTP server，而是替换 subagent coordinator channel。

`SpawnBehaviour` 可选择：

- 写 plan 后返回 Done；
- 只返回 Done 但不写文件；
- runtime failure；
- cancelled failure。

它保留真实 `SessionActor → SubagentEvent::Spawn → result_tx` 协议，同时把 child Agent 变成确定性 stub。

---

## 23. Planner 测试为什么解析 Prompt 里的路径

Stub 从 planner Prompt 中寻找 `/plan.md`，提取真实目标路径并写文件。

这同时验证：

- Prompt 确实携带正确 session-scoped path；
- Planner 成功依赖文件而非口头 Done；
- Actor 最终把该路径写入 Goal snapshot。

若测试直接从 Actor 内部拿路径写入，会绕过 Prompt wiring，少证明一层。

---

## 24. Planner 成功判据的正反测试

正例：

```text
Spawn → 写非空 goal/plan.md → Done → Goal 保持 Active
```

反例：

```text
Spawn → Done → 没写 plan.md → FailClosed → Goal 暂停
```

这防止未来维护者把 terminal text 当成事实来源。

---

## 25. Harness flag 也需要断言

Planner 测试捕获 `SubagentRequest`：

- `fork_context == true`；
- `surface_completion == false`；
- model override 的实际值。

这些字段不是小细节：它们决定 child 是否继承上下文、是否把内部完成通知暴露给用户、是否破坏 prompt cache/model 一致性。

---

## 26. Actor 测试需要显式构造真实依赖图

`create_test_actor_ex` 会组装：

- MockFs；
- DummyTerminal；
- HunkTracker actor；
- ChatState actor；
- gateway channel；
- persistence channel；
- allow-all PermissionHandle；
- MCP state；
- ToolContext；
- cancellation primitives。

这比直接调用一个 helper 更重，但能检查 actor 字段之间的 wiring。

---

## 27. DummyTerminal 为什么默认返回错误

测试中的 DummyTerminal 不应假装执行成功，而是返回：

```text
TerminalError::Other("dummy terminal")
```

这样意外走到真实 terminal 路径时测试会显式失败，不会静默产生看似成功的假证据。

好的 fake 默认应该暴露未预期调用。

---

## 28. 测试 Agent 应只注册必要 Tool

support 提供：

- `test_agent_default()`；
- `test_agent_with_goal_tool()`；
- `test_grok_build_agent_with_todo()`；
- `test_agent_with_plan_tools()`；
- `test_agent_with_tools(...)`。

每个测试只装自己需要的真实 Tool，避免“默认全量 Toolset”掩盖 availability 与 catalog 错误。

---

## 29. Real Tool + Fake Environment 的组合

例如 Todo 测试注册真实 `TodoWriteTool`，但：

- cwd 是测试路径；
- notification handle 是 noop；
- backend、filesystem 可替换；
- state_path 在临时位置。

这种组合能验证 Tool schema、registry、dispatch 和 Resources state，又不会触碰用户环境。

---

## 30. persistence channel 是可观测边界

Planner 测试保留 `PersistenceMsg` receiver，检查 `GoalUpdated` 中 planning flag 的发射顺序。

这证明的不只是内存最终状态，还包括客户端和磁盘会看到的过渡：

```text
planning=true
→ planner finishes
→ planning=false
```

只断言最终 snapshot 会漏掉“UI 永远卡在 planning”的 bug。

---

## 31. Channel stub 的完成语义

subagent coordinator stub 收到 `Spawn(req)` 后通过 `req.result_tx.send(result)` 回传。

这保留 production 的 oneshot completion handshake。测试因此能发现：

- 调用方没有等待 result；
- sender 被提前 drop；
- cancel/runtime error 映射错误；
- spawn 次数不符合预期。

---

## 32. 为什么部分 Actor 测试使用 LocalSet

ACP 和某些 Session 对象包含 `!Send` future 或本地状态，测试用：

```rust
#[tokio::test(flavor = "current_thread")]
let local = tokio::task::LocalSet::new();
local.run_until(async { ... }).await;
```

这与生产执行模型一致，不能为了测试方便强行假设所有任务可跨线程移动。

---

## 33. `#[serial]` 说明了什么

仓库一些测试会触及：

- process-wide env；
-固定全局 test seam；
-全局日志 subscriber；
-共享静态配置。

它们用 `serial_test` 防止同一 test binary 内并发污染。

但 `#[serial]` 不是 hermeticity 的替代品；能改成 per-test dependency injection 的状态仍应隔离。

---

## 34. 第三层：MockInferenceServer

这是 Agent E2E 的核心替身。它监听 `127.0.0.1:0`，支持：

- `/v1/chat/completions`；
- `/v1/responses`；
- `/v1/messages`；
- `/v1/models`；
- `/v1/settings`；
- `/v1/user`；
- `/v1/storage`；
- privacy endpoint。

绑定随机端口避免固定端口冲突；启动后用实际 TCP connect 探测 readiness，而不是固定 sleep。

---

## 35. Mock response 的优先级

Inference 请求按以下顺序选择响应：

```text
named matched expectation
→ endpoint compatibility FIFO
→ required-auth rejection
→ steady echo/fixed mode
```

优先级本身是测试合同。比如 scripted 401 可覆盖 steady auth 行为，named expectation 又能避免 auxiliary request 偷走 foreground script。

---

## 36. Echo mode 与 Fixed mode

默认 Echo mode 返回最后一条 user message 的回声，便于最小 smoke test。

`set_response(text)` 切换 Fixed mode，SSE deltas 拼接后必须逐字节还原文本，包括换行。

固定响应适合：

- Markdown fence；
- Mermaid；
- 空白敏感输出；
-终端 rendering。

---

## 37. FIFO script 的用途与危险

```rust
server.enqueue_response("/v1/responses", response1);
server.enqueue_response("/v1/responses", response2);
```

适合明确的顺序协议，例如：

```text
第一次 doomed response
→ Runtime resample
→ 第二次 clean response
```

危险是 title/classifier 等旁路请求也可能访问同一 endpoint，从而抢走队首脚本。

---

## 38. 为什么需要 Named Expectation

Named expectation 同时匹配：

- endpoint；
- request kind：Foreground 或 Auxiliary。

它让测试表达：

> 这段响应只能被用户主 Turn 的 Responses 请求消费。

而不是脆弱地表达：

> 下一个任何 Responses 请求都拿它。

---

## 39. Foreground / Auxiliary 怎样分类

优先规则：

```text
有非空 x-grok-turn-idx → Foreground
否则有非空 x-grok-req-id → Auxiliary
否则 tools 数量 >= 2 → 兼容性推断 Foreground
否则 → Auxiliary
```

最后一条是旧调用形状的兼容 heuristic，不应被当作新协议首选身份。

---

## 40. 为什么 Tool 数量只是 fallback heuristic

title、classifier、prompt suggestion 往往没有完整 Toolset；用户 Turn 通常带多个 Tool。

但这只是统计特征，不是可靠身份。生产加入显式 header 后，测试优先信任 header，以免 Tool catalog 变化破坏脚本匹配。

---

## 41. Expectation 是一个状态机

```text
Pending → Received → Blocked → Satisfied
```

测试 handle 提供：

- `wait_received()`；
- `wait_blocked()`；
- `release()`；
- `wait_satisfied()`；
- `assert_satisfied()`。

因此并发测试可以等待真实事件边界，而不是猜测“sleep 50ms 应该到了”。

---

## 42. Received 不等于 Satisfied

请求抵达 server 只证明它被 claim。

对于 streaming response，仍可能：

- 尚未发 terminal event；
- client 已取消；
- duplicate overlap 尚未退出；
- body 未被完整消费。

所以测试必须按自己关心的语义选择等待阶段。

---

## 43. Terminal barrier 怎样制造精确竞态

Blocked expectation 在最后一个 SSE event 前暂停：

```text
server 已收到请求
→ 已发送前面的 stream chunks
→ terminal event 暂停
→ 测试执行 cancel / interject / status check
→ release
```

这是复现“恰好在 Turn 完成前发生操作”的稳定方法。

---

## 44. Drop 自动 release 的原因

若测试 panic 或提前返回，blocked expectation handle 的 Drop 会打开 barrier。

否则 server task 可能永远挂起，随后测试 suite 也无法干净退出。

RAII 在测试基础设施里同样用于保证异常路径清理。

---

## 45. Duplicate request 为什么要 fingerprint

重试或共享请求可能产生重叠副本。Fingerprint 包含：

```text
endpoint
+ request kind
+ x-grok-req-id
+ serialized request body
```

只有活跃且 fingerprint 相同的重叠请求复用同一 expectation response。

---

## 46. 为什么不能只用 request ID

Tool result follow-up 可能复用同一 Turn ID，但 request body 已变化。

若只按 ID 去重，follow-up 会错误拿到上一阶段的响应。加入 body 后，它能 claim 下一条 expectation。

---

## 47. 为什么不推断“稍后到达的相同请求也是 retry”

生产协议没有明确的 HTTP attempt identity。

因此 mock 只对同时 in-flight 的相同 fingerprint 做 replay；前一次完成后再来的相同请求会 claim 下一条 expectation。

测试基础设施不应根据时间窗口发明生产协议里不存在的身份。

---

## 48. Primary cancel 的语义

测试覆盖：primary 被取消后：

- expectation 不能被标记 satisfied；
- active replay 状态需要清理；
- 后续请求不能继承幽灵 response；
- blocked waiter 不得泄漏。

取消测试若只断言 API 返回 cancelled，会漏掉资源与状态残留。

---

## 49. Request log 是另一条证据通道

Mock server 记录：

- method；
- path；
- JSON body；
- authorization；
- headers；
- 总请求计数。

最多保留 1024 条，超出时淘汰最旧项；总 count 仍独立累加。

这让测试能同时断言“结果正确”和“系统是通过正确请求得到结果”。

---

## 50. Negative assertion 为什么重要

常见高价值断言：

- resume replay 不应产生新 inference request；
- disabled policy 不应解析 doom-loop signal；
- cancelled queued prompt 不应发往模型；
- internal planner completion 不应 surface 给用户；
- retry 不应把 poisoned turn 写进 conversation。

Agent bug 很多不是缺少行为，而是额外做了一次危险行为。

---

## 51. Mock log 为什么有容量上限

完整 request body 可能包含整段 conversation。Soak test 若无限保存，会让测试工具自身成为内存泄漏源。

`count` 与 `entries` 分离后：

- 可以断言请求总量；
- 又能限制诊断内存。

测试仪器必须比被测对象更可控。

---

## 52. SSE Generator 不是随便拼字符串

`sse.rs` 为三类 API 生成真实期望的 event shape：

- Chat Completions chunks + usage + `[DONE]`；
- Responses created/delta/completed；
- Anthropic Messages start/delta/stop。

这样测试覆盖真实 parser 和 collector，而不是绕过 wire protocol 直接构造最终 response object。

---

## 53. Byte-exact 与 whitespace-collapsing 两套生成器

Echo 场景可以按 whitespace 切词；但 Markdown code fence、换行和连续空格需要 exact generator。

源码专门提供：

- `chat_completion_events_exact`；
- `responses_api_events_exact`。

选择错误生成器会让测试以为 renderer 有 bug，实际是 mock 已改变文本。

---

## 54. Usage event 不能省

Chat Completions scripted stream 在文本后发送 usage-only chunk，再 `[DONE]`。

这能验证：

- collector 接受 choices 为空的 usage event；
- token accounting 不依赖文本 chunk；
- terminal 顺序正确。

一个只发文本的简陋 mock 会漏掉计费和 Goal budget 路径。

---

## 55. Reasoning-only fixture 的价值

Responses generator 能产生：

```text
reasoning summary deltas
+ reasoning output item
+ 没有 message/output_text/tool call
```

collector 将其识别为 `ReasoningOnly` empty reason，从而触发 doom-loop resample。

这是用协议形状驱动 Runtime 分支，而不是在测试中直接设置一个布尔值。

---

## 56. Malformed SSE 需要 Raw body

`ScriptedBody` 支持：

- Json；
- Sse(Vec<SseEvent>)；
- Raw(String)。

Raw 用于：

- 非法 JSON；
- 截断 SSE；
- 错误 event/data 组合；
- parser resilience。

若 mock 只能生成合法对象，就永远无法验证错误处理。

---

## 57. 脚本应在注册时校验

`ScriptedResponse::validate` 提前检查 status code 与 header name/value。

坏测试数据应在 enqueue/registration 处 panic，而不是请求到来后在 server task 深处异步失败。

这叫让错误靠近作者。

---

## 58. Mock 也要覆盖非 inference API

模型之外，Agent startup 还会访问 models、settings、user subscription、storage、privacy。

若 mock 只实现 `/responses`，真实二进制 E2E 会：

- 意外联网；
- startup 挂起；
- 出现无关 404；
- 无法测试 auth refresh 与上传恢复。

完整 loopback API surface 是 hermetic subprocess test 的前提。

---

## 59. 第四层：Hermetic TestSandbox

每个 `TestSandbox` 拥有独立：

- root；
- HOME / USERPROFILE；
- GROK_HOME；
- workspace；
- TMPDIR/TMP/TEMP；
- child environment map。

构造时不修改父进程环境。

---

## 60. `env_clear()` 为什么关键

子进程从清空环境开始，只加入：

- 平台必需项；
- 测试路径；
- hermetic git；
- 网络 kill switches；
-显式 feature overrides。

否则开发者机器上的 API key、代理、配置、真实 HOME 或实验 feature flag 会让测试结果不可重复，甚至误访问真实服务。

---

## 61. 一个 mock URL 要覆盖所有外呼入口

`set_mock_url` 把 API、models、feedback、trace、managed config、code web、conversation 等 URL 都指向 loopback。

这不是重复配置，而是防止某条“旁路网络”逃出测试沙箱。

---

## 62. TestSandbox 还能初始化真实临时 Git 仓库

Builder 的 `.git()` 会在 isolated workspace 中创建并提交初始文件。

Goal baseline、diff、rewind、worktree 等测试因此能使用真实 Git 语义，又不依赖用户仓库。

---

## 63. Hermetic Git 的双运行器兼容

测试可能由 Cargo 或 Bazel 运行。

`GIT_BIN_PATH` 与 `GIT_EXEC_PATH` seam 让 Bazel 使用提供的静态 git；Cargo 环境则可使用正常路径。`xai-test-utils` 还提供 runfiles/CARGO_MANIFEST_DIR 双路径解析。

测试数据定位不应假设当前工作目录。

---

## 64. 诊断输出也要做 secret redaction

TestSandbox 的 diagnostic summary：

- secret value 显示 `<redacted>`；
- endpoint 做 sanitization；
- sandbox private path 可从 child output 中替换。

测试失败日志常会进入 CI artifact，测试工具不能因为“只是测试”而泄漏凭据。

---

## 65. 第五层：TestProcess

TestProcess 统一负责：

- spawn；
- stdin/stdout/stderr policy；
- process group/tree；
- graceful terminate；
- hard kill fallback；
- Drop cleanup；
- bounded output tail；
- timeout diagnostics。

子进程测试最常见的基础设施 bug就是僵尸进程、孙进程泄漏与 pipe 堵塞。

---

## 66. 为什么输出必须持续 drain

若 child 大量写 stderr，而测试不读，OS pipe buffer 填满后 child 会阻塞，看起来像业务 deadlock。

TestProcess 默认后台 drain，只保留最后 64 KiB 左右的 bounded tail。

这样兼顾：

- 防止 pipe backpressure；
- 失败时仍有最近诊断；
- 不无限占用内存。

---

## 67. Piped output 也保留 tail

当 ACP client 需要直接消费 child stdout 时，wrapper reader 会在每次 `poll_read` 后把字节复制到 diagnostic tail。

因此“调用方自己读”与“测试基础设施可诊断”不冲突。

---

## 68. 终止是一个有阶段的协议

```text
NaturalExit
或 GracefulTerminate
→ grace period
→ HardKillAfterGrace
→ bounded reap wait
```

Drop 也会做 best-effort cleanup。测试结束不能只 drop `tokio::process::Child`，否则 descendant 可能继续存活。

---

## 69. 超时不是修复竞态的方法

超时的用途是：

- 给等待设置上界；
- 失败时输出 stderr/process diagnostics；
- 防止 CI 永久挂住。

真正的同步应使用 expectation、Notify、watch、channel 或可观察屏幕状态。把 sleep 加长只会让 flaky 变慢。

---

## 70. `GROK_TEST_TIMEOUT_SCALE`

共享 CI runner 可能整体变慢，test-support 用正整数环境变量统一放大 timeout。

它适合调整环境速度差异，不应掩盖没有事件同步的测试设计缺陷。

---

## 71. 第六层：Typed ACP subprocess client

`GrokStdioClient` 启动真实：

```text
grok agent stdio
```

然后通过 typed ACP client 执行：

```text
initialize
→ authenticate
→ session/new 或 session/load
→ prompt
→ 收集 notification
```

这是验证二进制、CLI wiring、stdio JSON-RPC 和 Session Runtime 的完整链路。

---

## 72. 测试 ACP Client 怎样处理 Permission

`TestAcpClient::request_permission`：

- 优先选择 AllowOnce；
- 否则第一个 option；
- 无 option 则 Cancelled。

这让普通 E2E 不被交互 UI 阻塞，同时仍经过真实 permission request/response 协议。

需要验证拒绝行为时，应换专门 client，而不是修改通用 auto-approve 逻辑。

---

## 73. Notification capture 为什么只拼 Agent text

通用 client 统计所有 notification，但只把非空 `AgentMessageChunk(Text)` 拼进 captured text。

它适合 smoke assertion；Tool、Plan、Goal 等结构化通知则应使用更专门的 client 或读取原始消息，避免把展示文本当成完整协议状态。

---

## 74. RawStdioClient 的必要性

Typed ACP library 无法构造某些历史或异常 wire shape，例如：

- 非标准 method spelling；
- string UUID id；
- malformed JSON-RPC；
-协议版本边界。

Raw client 能逐行发送原始 JSON。协议兼容测试不能只覆盖当前 typed SDK 允许表达的输入。

---

## 75. In-process ACP Harness 与 subprocess Harness 的区别

`session/testkit/e2e.rs` 使用两对 `tokio::io::duplex`：

```text
ClientSideConnection ⇄ in-memory pipes ⇄ AgentSideConnection(MvpAgent)
```

它仍运行真实 ACP codec 和 MvpAgent，但不启动 OS 进程。

适合测 session/load 性能与协议 round-trip，成本低于 subprocess。

---

## 76. 为什么 duplex buffer 很大

Session load 会 burst replay 大量 notification。Harness 设置 16 MiB buffer，避免小 pipe 容量把性能测试变成纯 backpressure 测试。

但这也意味着它不能证明真实 OS stdio 在极端 backpressure 下完全相同；那需要 subprocess/PTY 层。

---

## 77. In-process Harness 仍有明确 timeout

- initialize：60 秒；
- load：180 秒。

它们是失败上界，不是同步机制。load elapsed 从发出请求前记录，用于性能与内存测试。

---

## 78. 第七层：Session synthesis

`SessionSpec` 控制：

- turns；
- 每 Turn 的 AvailableCommandsUpdate 数；
- catalog command 数量与描述长度；
- agent chunks 数与长度；
- rewind point 数；
- 每个 rewind 的文件数和内容长度。

默认能生成约 20 MB `updates.jsonl`，并可由环境变量 scale。

---

## 79. 为什么特意合成冗余 ACU

真实 Session 会在 skill/subagent 边界反复发布 available command catalog。

如果性能 fixture 只有聊天文本，就无法复现真实日志中 catalog 冗余带来的：

- 文件大小；
-解析成本；
- replay skip；
- fork copy 开销。

性能测试数据应模拟主导成本，而非只追求格式合法。

---

## 80. 两种 synthesis 路径

精确回放路径：

- summary 通过真实 adapter 初始化；
- `updates.jsonl` / `rewind_points.jsonl` 直接写；
- 精确控制每种 line 与重复量。

Bench 路径：

- 所有 update 通过真实 `JsonlStorageAdapter::append_update`；
- 直到达到目标字节数。

前者强调形状控制，后者强调 production writer fidelity。

---

## 81. Synth round-trip guard

非 ignored 测试生成小 Session，再通过 production replay reader 读回，分别统计：

- user chunks；
- ACU；
- agent chunks；
-总数。

这防止 testkit 自己悄悄生成了 production reader 无法解析的数据。

---

## 82. 性能测试 fixture 也需要自证

Bench generator 的测试断言：

- `updates.jsonl` 至少达到目标字节；
- production reader 能解析；
-结果非空。

否则 benchmark 可能非常快，只因为 fixture 没生成成功。

---

## 83. 第八层：Trace Replay

当前 `tests/trace_replay.rs` 的准确定位是：

> 对 TodoGate 纯函数做数据驱动的合成 turn snapshot 回放。

它读取 `tests/fixtures/synthetic_*.json`，在每个 assistant turn 上构造 `CollectedTodoGateInput` 并调用 `evaluate_todo_gate`。

---

## 84. 它不是完整 Agent trace replay

它不会：

- 重新采样模型；
- 调度 Tool；
- 恢复 SessionActor；
-重放真实时间与并发；
-验证完整 ACP stream。

“trace”在这里是抽取后的决策输入序列。名称不能替代对 harness 边界的阅读。

---

## 85. Fixture schema 为什么使用 closed enum

`expected_gate_decision` 反序列化为：

```rust
enum ExpectedGateDecision { Nudge, Continue }
```

若 JSON 写成 `"maybe"`，加载直接失败。

用 String 再写宽松分支，可能让拼写错误的 fixture 没有真正执行任何断言。

---

## 86. Canonical fixture set guard

Harness 不只 glob 所有 `synthetic_*.json`，还把磁盘集合与 `CANONICAL_FIXTURES` 做 set equality。

这同时捕获：

- fixture 意外丢失；
- 新 fixture 忘记接受 review；
- 文件改名；
-未预期的额外 case。

开放发现 + 封闭清单形成双重约束。

---

## 87. 当前三个 TodoGate Fixture

| Fixture | 意义 |
|---|---|
| clean completion | todos 全完成，应 Continue |
| stranded narration | assistant 结束但 pending todo 无 backing task，应 Nudge |
| PR babysit partial backing | 三个 in-progress 只有一个后台任务，应只指出未 backing 的两个 |

Fixture 的描述字段同时记录了历史 failure shape 与设计意图。

---

## 88. Fixture 中未使用字段仍可能有价值

`tool_calls_emitted` 当前 gate 不读取，但保留在 fixture 中用于说明 Turn 形状。

源码用 `#[allow(dead_code)]` 明确这种“为人类可读性存在”的字段。

不过这类字段不能被误认为已受断言保护；若未来需要其语义，必须真正接入 input 或增加独立检查。

---

## 89. Reminder 不适合全文相等

Fixture 用 `expected_reminder_contains` 验证关键片段：

- Pending 标签；
-具体 Todo 文本；
-无 backing task 说明。

这样 copy edit 不会破坏 fixture，但决定语义仍被固定。

---

## 90. Snapshot 这个词的三种含义

本仓库至少有：

1. **State snapshot**：GoalOrchestration 等可序列化状态；
2. **File snapshot**：rewind point 中的文件 before/after 内容；
3. **Golden snapshot**：UI 渲染输出 `.snap` 文件。

三者不能混称“snapshot test”。前两种常是业务数据结构，第三种才是传统 golden comparison。

---

## 91. Golden snapshot 适合什么

Pager 的 diff block、status block 等视觉结构适合 `.snap`：

- 行布局复杂；
-完整输出比许多 substring 更容易 review；
-变更可通过 diff 审核。

但 Agent 控制流多用结构化断言，避免大段无关文本更新掩盖逻辑变化。

---

## 92. Scripted YAML Scenario 又是什么

Pager tests 的 YAML scenario 描述：

- 启动条件；
-按键/输入/resize；
-等待屏幕文本；
-截图；
-预期或 bug marker。

Runner 启动真实 Pager PTY，产出 report 和 artifacts。它是声明式用户旅程，不是 Rust state snapshot。

---

## 93. 为什么 YAML 场景多数 ignored

它们需要：

- 构建/启动真实二进制；
- PTY；
-流式 mock server；
-屏幕等待与截图。

成本和平台波动都高，因此默认测试只做 schema/load guard，完整场景由 opt-in/专门 CI lane 执行。

Ignored 不等于无价值，而是运行频率不同。

---

## 94. Scenario 自身必须有常规测试

源码特意让 YAML 解析/清单契约在普通 `cargo test` 中执行，防止 malformed scenario 长期躲在 ignored test 后面。

慢执行可分 lane，便宜的静态合法性不应一起跳过。

---

## 95. PTY E2E 能发现什么

只有真实 PTY 容易发现：

- ANSI/OSC 8 字节问题；
-resize 与 repaint；
-光标和 selection；
- bracketed paste；
- terminal mode 恢复；
- Ctrl-C/Esc 时序；
-滚动与 streaming 交互；
-真实 child exit code。

这些无法从 SessionActor 最终状态推导出来。

---

## 96. 屏幕断言之外还要检查 raw bytes

路径含空格的 hyperlink 场景既检查 rendered screen，也读取 `raw_output.bin` 验证完整 OSC 8 URL marker。

屏幕模拟器可能容错或归一化控制序列；字节级协议问题需要字节级证据。

---

## 97. “出现一次”为什么是重要 E2E 断言

Session resume/leader attach 测试常等待 marker，然后再次 pump/settle，再统计 marker 正好一次。

只等待“出现”无法发现 duplicate replay。分布式 UI 正确性常是：

```text
eventually appears
AND never appears twice
```

---

## 98. Replay 不得重驱动 inference

Leader reattach 测试记录 mock request count：新 client 应从 durable log 重放 transcript，而不是重新请求模型。

这是很强的跨层证据：

- UI 看到了历史；
-历史只出现一次；
-模型请求数没有增长。

三条合起来才能区分 replay 与 re-execution。

---

## 99. 第九层：协议级故障注入

`UdsProxy` 位于 leader client 与真实 Unix socket 之间，理解四字节大端长度前缀 frame。

它能对精确的第 N 帧：

- drop；
-只发送半个 length prefix 后断开；
-delay；
-duplicate。

---

## 100. 为什么不能用随机断网代替

随机 kill 只能说明“某处失败了”。Frame-aware proxy 能表达：

```text
第 3 个 client→leader frame 完整丢失
第 2 个 leader→client frame 重复
length prefix 只写 2 bytes 后断开
```

失败可重复，才能验证 retry、dedupe 和 parser 的精确契约。

---

## 101. FaultPlan 按连接和方向计数

- frame index 从 1 开始；
-每条连接重置；
- client→leader 与 leader→client 分开；
-未选方向透明转发。

明确计数语义很重要，否则 reconnect 后“第 N 帧”会变得不可预测。

---

## 102. `sever_now` 与计划故障的区别

计划故障绑定某个 frame；`sever_now` 则立即取消当前所有 proxied connections。

实现会换一个新的 CancellationToken，因此之后新建的连接不受先前 sever 影响。

这适合测试：当前连接死亡，但 reconnect 仍应成功。

---

## 103. Frame size 也要有防护

Proxy 最大缓存 64 MiB frame body，与 leader transport 上限对齐。

否则被截断/错位的 length prefix 可能解释成数 GiB，测试基础设施会 OOM，而不是给出可诊断的协议错误。

---

## 104. 文件系统故障注入

Goal tests 会主动预植：

- symlink root；
- destination symlink；
-目录占据预期文件；
-普通文件占据预期目录；
-plan 在 strategist 执行期间被替换。

这些不是 mock error code，而是构造真实文件系统攻击形状，验证 no-follow 与 restore guard。

---

## 105. Strategist 的 PlanGuard 怎样测试

测试 stub 可以在 strategist 运行时：

-覆盖 plan.md；
-创建原本不存在的 plan.md；
-把 plan 替换成 symlink；
-返回成功或错误。

随后断言：

-原文件字节被恢复；
-原本不存在则删除；
-symlink 不被跟随；
-失败路径同样恢复。

Drop/cancellation safety 必须覆盖成功与失败两边。

---

## 106. 原子写入测试验证什么

Verifier patch/details 写入测试检查 tempfile + rename 路径，而非只检查最终文件存在。

最终内容正确不代表崩溃中间态安全。原子写策略需要专门的实现级测试或故障模拟。

---

## 107. 并发测试的核心是可控制的 barrier

仓库常用：

- `Notify`；
- `watch`；
- oneshot；
- mpsc；
- expectation terminal barrier；
- AtomicBool/AtomicUsize；
- `tokio::join!`。

它们让测试安排 happens-before，而不是依靠调度器碰巧产生竞态。

---

## 108. Skeptic fan-out 怎样证明是并行

Fake spawner 为多个 skeptic 安装 hold Notify。测试等待它们都已开始，再统一 release。

若实现错误地串行：

-第一个会等待 release；
-后续永远无法到达“已开始”；
-测试在有界 timeout 内失败。

这比比较总耗时更稳定。

---

## 109. Concurrent completion gate 怎样测

Legacy Goal completion 用 AtomicBool 防止两个 classifier panel 同时运行。

测试应形成：

```text
请求 A 占有 in-flight gate
→请求 B 到达
→ B 不得启动第二 panel
→产生定义好的 synthetic outcome/Ack
→A 释放
```

最终状态相同不代表并发成本和 token 使用正确，因此要断言 spawn/request 次数。

---

## 110. Virtual time 的边界

Tokio test-util 支持 `start_paused = true`，仓库用于 interval/auth refresh 等时间驱动逻辑。

虚拟时间适合：

- backoff；
- interval tick；
-debounce；
-超时状态机。

不适合依赖真实 OS process、TCP、PTY 的测试；那些组件不会随 Tokio clock 自动前进。

---

## 111. 为什么有些测试仍需 `#[serial]`

虚拟时间只隔离 Tokio clock，不能隔离：

-环境变量；
-静态 global；
-固定文件路径；
-全局 tracing subscriber。

测试设计要识别共享资源种类，不能用一种同步手段解决所有污染。

---

## 112. Doom-loop integration 是优秀的分层例子

测试通过 MockInferenceServer 发送真实 Responses SSE：

- malformed check frame；
-unknown trigger；
-cumulative duplicate trigger；
-reasoning-only completion；
-confident signal 后 clean resample。

然后断言 parser、dedupe、policy gate、请求次数和 conversation 内容。

---

## 113. “Poisoned turn 不进入历史”怎样证明

脚本第一响应触发 doom-loop，第二响应干净。测试验证：

-总共两个请求；
-返回第二响应；
-第二次 request body 与第一次一致；
-第一响应的输出没进入 conversation prefix。

如果只断言最终文本正确，会漏掉错误历史污染。

---

## 114. Malformed 输入的期望不是统一 fail closed

对于辅助 doom-loop signal，malformed frame 被吞掉，主回答仍完成；因为该字段是可选优化，不应摧毁正常 response。

对于 Goal completion verdict，malformed verifier 输出则保守视为 refute/infra failure。

错误策略取决于功能风险，测试必须固定每条边界自己的 policy。

---

## 115. Goal Evaluator 的测试边界

Evaluator 单元测试重点验证：

- transcript 排除 System/Reasoning；
-保留最近相关项；
-总长度 cap；
-request 不带 Tool；
-JSON schema 被设置；
-decision 严格解析。

至于 sampler 超时/重试和 Actor 状态变化，应在 `acp_session_impl/goal.rs` 相邻层测试。

---

## 116. Goal Skeptic 测试为什么既有纯聚合又有 FakeSpawner

纯聚合证明投票数学；FakeSpawner stage tests 证明：

-实际发了 N 个请求；
-skeptic0 是否 resume；
-high-confidence refute 是否短路；
-剩余 cold skeptics 是否并发；
-per-index model/tool names 是否注入；
-malformed/transport/cancel 如何转成结果。

规则与编排是两种不同的正确性。

---

## 117. 为什么每种失败都要单独枚举

Skeptic stage 区分：

- transport error；
- runtime error；
-cancelled；
-malformed token；
-terminal-only fallback；
-JSON details 为空但磁盘报告存在；
-scratch root unsafe。

若都压成 `Err(String)` 一个 case，未来 mapping 改错时测试无法指出具体语义。

---

## 118. Telemetry 也可以成为断言对象

Goal tests 收集 Event，验证：

- fired；
-completed；
-resolved cap/cadence；
-latency/attempt 等字段；
-fail-open reason。

Observability 是生产运维合同。状态正确但事件缺失，会让真实故障无法诊断。

---

## 119. 但不要只测 Telemetry

Event emitted 不代表行为完成。应同时断言：

-返回 outcome；
-状态 snapshot；
-文件 artifact；
-请求/spawn 次数；
-持久化通知。

Telemetry 是旁证，不是权威事实。

---

## 120. Failure message 是测试 API 的一部分

优秀断言会包含：

- fixture name / turn index；
-完整 reminder；
-stderr tail；
-process diagnostics；
-sandbox summary；
-request log summary；
-artifact path。

CI 中无法附加调试器，失败消息决定一次失败需要几轮才能定位。

---

## 121. `expect` 文案应描述不变量

例如：

```text
"session/load timed out (>180s)"
"planner must request chat-prefix fork"
"no update is dropped or duplicated by the typed replay reader"
```

比 `unwrap()` 更能说明失败意味着哪个合同被破坏。

---

## 122. 测试不能意外写真实统一日志

shell test-support 用 pre-main constructor 把 unified log 重定向到临时位置。

Integration binary 则依靠 TestSandbox 的独立 HOME/GROK_HOME。

测试隔离不仅是输入，也包括日志、缓存、状态和 telemetry 输出。

---

## 123. ignored 测试应写清运行方式

PTY、真实 clipboard、网络或视觉 dump 测试通常在 `#[ignore = "..."]` 中注明：

-为什么默认跳过；
-应该运行哪个 test target；
-是否需要 `--test-threads=1`；
-是否需要 `--nocapture`。

否则 ignored test 很快变成从不运行的死代码。

---

## 124. 性能、内存与正确性测试要分开

Session testkit 支持：

- load perf；
-fork copy bench；
-load/fork memory；
-leader soak；
-小型非 ignored round-trip correctness。

同一个 fixture generator 可复用，但 acceptance metric 不同：延迟、RSS、文件大小或逻辑计数不能混成一个模糊测试。

---

## 125. ResourceSnapshot 为什么测 RSS/threads/fds

长期 Agent 可能逻辑正确却泄漏：

-subprocess；
-socket/fd；
-Tokio task 对应的资源；
-请求日志；
-Session cache。

Soak test 观察增长趋势，补足普通功能测试“进程结束后资源都被 OS 回收”的盲点。

---

## 126. 测试金字塔不是按文件名自动形成的

一个标为 `e2e_tests.rs` 的测试可能只使用 channel stub；一个普通 unit test 可能启动 TCP server。

判断层级应看真实边界：

```text
是否启动生产 Actor？
是否经过 serializer/codec？
是否经过真实 filesystem？
是否启动 OS process？
是否通过 PTY？
是否依赖外部网络？
```

名字只是线索。

---

## 127. 每一层能证明与不能证明什么

| 层 | 能证明 | 不能证明 |
|---|---|---|
| 纯函数 | 规则、边界、组合 | wiring、IO、时序 |
| Actor + stub | 状态与消息协议 | HTTP/SSE、二进制启动 |
| Mock HTTP | wire parser、retry、request shape |真实服务行为、真实网络 |
| In-process ACP | codec + Agent wiring | OS stdio/process cleanup |
| Subprocess ACP | 二进制与完整 stdio 生命周期 | 终端渲染/键盘 |
| PTY | 用户可见终端行为 | 所有平台/真实服务 |
| Soak/perf | 增长和规模表现 | 业务语义穷举 |

---

## 128. 为新 Agent 功能选择测试层

一个实用判断顺序：

1. 能否把核心决策提成纯函数？先测它。
2. 是否有状态迁移？测 tracker/actor。
3. 是否依赖模型 wire shape？加 scripted SSE。
4. 是否依赖 request ordering/cancel？加 expectation barrier。
5. 是否依赖持久化/restart？加 JSONL 与 load/replay。
6. 是否依赖 CLI/stdio？加 subprocess ACP。
7. 是否依赖终端行为？最后加 PTY。

不要从最贵的 E2E 开始表达所有规则。

---

## 129. 一个 Completion Gate 的推荐验证矩阵

```text
Pure:
  no todo / pending / in-progress backed / partially backed

Fixture replay:
  历史 failure shapes

Actor:
  turn-end 收集输入、nudge 注入、计数

Mock inference:
  nudge 后模型继续，不能结束死循环

Subprocess ACP:
  client 收到正确更新

PTY:
  用户看到一次且不会重复
```

每一层只承担自己新增的风险。

---

## 130. 一个新 Tool 的推荐验证矩阵

- schema parse：缺字段、未知字段、边界值；
- access kind：Read/Edit/Execute 分类；
- permission：allow/deny/cancel；
- fake backend：成功、typed error、cancel；
- notification：started/progress/completed 顺序；
- persistence：需要时 round-trip；
- Agent Turn：真实 Tool call/result 回流；
- E2E：只为 CLI/终端独有行为增加。

Tool 测试不应只调用 implementation function，绕过 registry 和 bridge。

---

## 131. 一个新模型旁路调用的推荐矩阵

例如新增 classifier：

- Prompt contract unit test；
- request tool-free/schema test；
- parser strictness；
- transcript cap/过滤；
- auxiliary matcher expectation；
- timeout；
-malformed response；
-usage missing；
-primary model fallback；
-不会偷走 foreground FIFO；
-失败不会错误改变用户任务状态。

---

## 132. 一个恢复功能的推荐矩阵

- 当前 snapshot round-trip；
-手写 legacy snapshot；
-未知未来字段/状态；
-in-flight 状态重启降级；
-artifact 缺失；
-artifact symlink/权限错误；
-durable log replay exactly once；
-replay 不产生新 inference；
-新 live event 与 replay overlap 去重；
-subprocess crash/reattach。

恢复测试必须覆盖“旧进程到底做到哪一步未知”。

---

## 133. 测试反模式：固定 sleep

```rust
sleep(Duration::from_millis(200)).await;
assert!(done);
```

问题：

-慢机器可能没完成；
-快机器浪费时间；
-无法证明等待的是目标事件；
-会掩盖 lost wakeup。

应改为事件 barrier + 外层 timeout。

---

## 134. 测试反模式：只断言最终文本

最终回答一样，内部可能：

-请求了两次模型；
-执行了两次 Tool；
-先泄漏错误通知再覆盖；
-把 poisoned turn 写进历史；
-错误消费 token budget。

至少再检查 request log、Tool count、persistent state 或 notification sequence。

---

## 135. 测试反模式：Mock 得比生产接口更高

如果要测试 SSE parser，却直接 fake `ConversationResponse`，测试永远看不到 malformed event、usage-only chunk 和 terminal ordering。

Mock 应放在你想验证边界的正下方：

-测 parser → mock HTTP bytes；
-测 Actor → mock sampler/subagent；
-测纯决策 →直接构造 typed input。

---

## 136. 测试反模式：一个万能 Fixture

把所有 feature、几十个 Turn、多个故障塞进一个 E2E：

-失败定位差；
-更新困难；
-任一旁路变化都可能破坏；
-很难证明具体 invariant。

更好的结构是：小型 canonical cases + 少数跨层 user journey。

---

## 137. 测试反模式：把 Mock 当作生产规范

Mock server 的行为是测试工具实现，不自动代表真实服务。

关键 wire shape 应：

-依据生产 client parser；
-与正式协议类型一致；
-至少有 shared HTTP wire tests；
-对真实服务新增字段保持合理兼容。

否则产品可能只兼容自己的 mock。

---

## 138. 测试反模式：为了通过而放宽 parser

模型输出失败时，常见诱惑是让 parser 接受更多自然语言。

但 Goal verdict 属于安全敏感决策。更好的路径是：

-维持 strict schema；
-明确 correction retry；
-失败进入 infra/fixable 路径；
-增加 malformed fixture。

测试应保护边界，不应训练实现去猜。

---

## 139. 如何阅读一个失败测试

建议按下面顺序：

1. 测试处于哪一层？
2. 哪些依赖是真实的，哪些是 fake？
3. 第一个权威状态在哪里？
4. 它等待了哪个事件边界？
5. positive 与 negative assertion 各是什么？
6. request/spawn/notification 次数是否异常？
7. fixture 是否改变了输入语义？
8. 是产品 bug、mock drift、timeout，还是环境泄漏？

先确定证明边界，再读 panic 最后一行。

---

## 140. 如何把线上 Agent 故障变成回归测试

推荐流程：

```text
线上现象
→ 提取最小决策输入/协议序列
→ scrub 用户数据
→ 决定最低可复现层
→ 写 closed typed fixture 或 scripted response
→ 增加 negative assertion
→ 必要时再补一条高层 wiring E2E
```

不要一开始就复制完整生产 Session；它通常噪声大、隐私风险高且难以稳定。

---

## 141. Fixture scrub 后要保留什么

可删除：

-真实用户文本；
-仓库名；
-绝对路径；
-凭据；
-无关长输出。

必须保留：

-Turn 边界；
-状态顺序；
-触发 parser/gate 的结构；
-重复/缺失关系；
-预期 decision 和 reason；
-必要的 path 类别，而非真实路径。

Fixture 的价值来自结构保真，不来自内容逼真。

---

## 142. 何时应升级到真实服务 Eval

本地 hermetic tests 无法证明：

-当前线上模型遵循 Prompt 的概率；
-真实服务 feature rollout；
-模型版本质量变化；
-真实网络与限流分布。

这些应由离线任务集、canary、shadow traffic 或线上 metrics 补充。但它们不能替代 deterministic runtime regression tests。

---

## 143. Runtime Test 与 Model Eval 的分工

```text
Runtime Test 问：
给定输出 X，系统是否必然执行 Y？

Model Eval 问：
给定任务 T，模型多大概率产生合格输出 X？
```

两者相乘才接近真实成功率。Runtime 不确定会污染 Model Eval；模型概率性又不适合成为每次提交的硬回归基线。

---

## 144. 一个成熟 Agent 验证体系需要四种 Oracle

1. **结构 Oracle**：schema、enum、typed parser；
2. **状态 Oracle**：tracker snapshot、持久化记录；
3. **行为 Oracle**：request/tool/spawn/notification 次数与顺序；
4. **用户 Oracle**：屏幕、文本、交互和 artifact。

只靠最终回答属于第四种的一个很窄子集。

---

## 145. 为什么 Evidence 应交叉验证

例如 resume 正确性：

```text
屏幕出现旧回答一次
+ durable log 中有 terminal
+ mock request count 不增长
+ session id 保持相同
```

任何单一信号都有替代解释；多条独立证据能排除“看起来对”的错误路径。

---

## 146. 本仓库测试体系最值得借鉴的设计

- 用严格 typed input 固定 LLM 输出边界；
-用 named expectation 隔离 foreground/auxiliary 模型调用；
-用 terminal barrier 代替 sleep 制造竞态；
-用 request count 证明没有重复副作用；
-用 hermetic child env 防止真实配置和网络渗入；
-用 frame-aware proxy 注入精确 IPC 故障；
-用 pure state-machine tests 穷举恢复安全；
-用少量 PTY E2E 覆盖真正只能由终端发现的问题。

---

## 147. 当前体系仍可继续增强的方向

从本基线可以看到若干演进空间：

- Trace Replay 目前只覆盖 TodoGate，可扩展为脱敏的 typed decision traces；
-更多并发状态机可加入系统化 schedule exploration；
-Prompt contract 可配合 versioned eval dataset；
-ignored PTY 场景可按平台拆专门 CI lane；
-comment/implementation drift 可加入静态检查或 review checklist；
-fixture schema 可显式 version；
-Mock wire shape 可与正式协议 corpus 做双向兼容测试。

这些是建议，不代表当前源码已经实现。

---

## 148. 一套可直接使用的测试 Review Checklist

### 输入

- 是否覆盖空值、上限、非法值、未知未来字段？
- 是否包含 malformed wire shape？
- Fixture 是否 typed 且有 schema/version？

### 状态

- 是否验证中间状态，而不只最终状态？
- restart 是否 fail safe？
- terminal transition 是否清理 latch、scratch 和 pending work？

### 副作用

- request、Tool、spawn 是否恰好一次？
- cancel 后是否仍有晚到写入？
-失败是否会触碰真实 HOME、网络或日志？

### 并发

- 是否使用 event barrier？
- timeout 是否只作为上界？
-是否覆盖 duplicate、delay、drop、cancel？

### 诊断

- panic 是否包含 case name、状态、日志尾和 artifact 路径？
-输出是否脱敏？

### 层级

- 核心规则是否在最低成本层覆盖？
-高层测试是否只验证新增边界？

---

## 149. 从源码到测试的追踪方法

面对一个 Agent feature，可以按下面的 grep 路径：

```text
production type/function
→ 同文件 #[cfg(test)]
→ acp_session_tests 中的 actor wiring
→ tests/ integration target
→ xai-grok-test-support 使用点
→ pager/headless/PTY 用户路径
```

然后画出每层替换的 dependency，避免重复测试同一件事或遗漏真正的边界。

---

## 150. 本篇最终心智模型

```text
Agent correctness
  = deterministic core rules
  × safe state transitions
  × faithful protocol parsing
  × exactly-once side effects
  × durable recovery
  × bounded concurrency/resources
  × user-visible behavior
  × probabilistic model quality
```

前七项主要由本篇的 Runtime 测试体系负责；最后一项需要独立 Model Eval。不能因为系统含有 LLM，就把所有错误都归因于模型概率。

---

## 151. Glossary：本文名词白话解释

| 名词 | 白话解释 |
|---|---|
| Eval | 评估系统质量的方法；既可指模型任务成功率，也可泛指 Agent 验证 |
| Runtime Test | 给定确定输入，检查运行时是否产生确定状态和副作用的测试 |
| Model Eval | 对一组任务重复采样，衡量模型输出质量和成功率 |
| Oracle | 测试中判断“对或错”的依据 |
| Test Pyramid | 大量便宜底层测试、较少昂贵高层测试的分层结构 |
| Unit Test | 聚焦单个函数、类型或小模块的测试 |
| Integration Test | 验证多个真实组件连接后的行为 |
| E2E | End-to-End，从外部入口走到最终输出的端到端测试 |
| Harness | 驱动被测系统、提供依赖并收集结果的测试架子 |
| Testkit | 一组面向某领域的测试构造器和工具 |
| Fake | 有简化实现、可实际运行的测试替身 |
| Stub | 按预设输入返回结果的轻量替身 |
| Mock | 可配置行为并记录调用、供断言使用的替身 |
| Spy | 重点记录调用次数和参数的测试对象 |
| Mock Inference Server | 在 loopback 上模拟模型 HTTP API 的服务器 |
| Loopback | 本机网络地址，如 `127.0.0.1`，不离开机器 |
| Scripted Response | 测试预先写好的 HTTP/SSE 响应 |
| FIFO | First In First Out，先入先出队列 |
| Named Expectation | 有名字、有匹配条件并可等待生命周期的预期请求 |
| Foreground Request | 用户主 Turn 发出的模型请求 |
| Auxiliary Request | title、classifier、suggestion 等旁路模型请求 |
| Request Matcher | 判断某请求应消费哪条测试响应的规则 |
| Fingerprint | 用多项字段计算的请求身份特征 |
| Overlapping Duplicate | 前一个相同请求仍在执行时到来的重复请求 |
| Barrier | 让并发流程停在精确位置，等待测试释放的同步点 |
| Terminal Event | 表示一个 stream 正式结束的最后事件 |
| Received | Server 已收到并认领请求，不代表响应已完成 |
| Satisfied | 被测响应已越过关键完成边界且相关副本已收束 |
| SSE | Server-Sent Events，模型流式响应常用的文本协议 |
| Delta | 流式响应中的一小段增量内容 |
| Usage-only Chunk | 只携带 token usage、不携带文本的 stream 事件 |
| Byte-exact | 拼接后与原始字节完全一致，包括换行和空格 |
| Malformed Input | 不符合协议或 schema 的输入 |
| Fail Closed | 不确定时拒绝、暂停或判未完成 |
| Fail Open | 辅助功能失败时允许主流程继续；是否安全取决于边界 |
| Hermetic | 测试不依赖开发者机器的隐式环境、配置或网络 |
| TestSandbox | 拥有独立 HOME、workspace、temp 和 child env 的测试沙箱 |
| `env_clear()` | 清空子进程继承环境，再只加入允许项 |
| Kill Switch | 强制禁用真实外部网络或功能的环境开关 |
| Redaction | 在日志中移除或替换秘密信息 |
| TestProcess | 统一管理测试子进程、输出和清理的封装 |
| Process Tree | 一个进程及其创建的子孙进程 |
| Reap | 回收已退出子进程，避免 zombie |
| Backpressure | 消费太慢导致 buffer 填满并阻塞生产者 |
| Bounded Tail | 只保留输出末尾固定字节用于诊断 |
| ACP | Agent Client Protocol，客户端与 Agent 的协议 |
| Typed Client | 通过 Rust 类型构造合法协议消息的客户端 |
| Raw Client | 直接发送原始 JSON/字节，可构造异常或历史 wire shape |
| Duplex | 内存中的双向异步字节管道 |
| LocalSet | Tokio 用于运行 `!Send` future 的单线程本地任务集合 |
| Actor | 通过消息串行处理状态的并发组件 |
| MPSC | 多生产者、单消费者 channel |
| Oneshot | 只发送一次结果的 channel |
| Watch Channel | 保存最新值并通知观察者变化的 channel |
| Notify | Tokio 的轻量异步唤醒原语 |
| RAII | 依靠对象生命周期/Drop 自动释放资源或恢复状态 |
| `#[serial]` | 要求相关测试不要在同一进程内并行运行 |
| Virtual Time | 测试中暂停并人工推进的 Tokio 时钟 |
| Fixture | 固定的测试输入数据文件或对象 |
| Canonical Fixture Set | 经过显式认可、数量和文件名都受保护的 fixture 集合 |
| Closed Enum | 只有列出的合法 variant，未知值解析失败的枚举 |
| Trace | 一段执行过程记录；具体包含什么必须看 schema |
| Trace Replay | 把记录的输入序列重新交给被测逻辑，比较决策 |
| State Snapshot | 可持久化的运行时状态副本 |
| File Snapshot | 用于 rewind 的文件内容副本 |
| Golden Snapshot | 保存完整渲染输出并做 diff 的基准文件 |
| Rewind | 恢复到较早的 conversation/file 状态 |
| JSONL | 每行一个 JSON 对象的日志格式 |
| ACU | AvailableCommandsUpdate，可用命令目录更新 |
| Session Synthesis | 人工生成 production-shaped Session 数据 |
| Round-trip | 写出后再读回并验证信息不丢失 |
| Backward Compatibility | 新代码仍能读取旧数据/旧协议 |
| Forward Compatibility | 旧或当前代码遇到未来未知内容时安全处理 |
| Golden Test | 将当前输出与审阅过的完整基准输出比较 |
| Scripted Scenario | 用 YAML 等声明交互步骤和预期的用户旅程测试 |
| PTY | Pseudo Terminal，模拟真实终端输入输出和 mode |
| ANSI | 终端颜色、光标等控制序列 |
| OSC 8 | 终端超链接控制协议 |
| Fault Injection | 主动制造错误来验证恢复与防护 |
| Frame | 传输协议中有明确边界的一条消息 |
| Length Prefix | 在消息前写长度，让接收方知道读取多少字节 |
| UDS | Unix Domain Socket，本机进程间 socket |
| Symlink Squat | 攻击者预先放置符号链接诱导程序写到别处 |
| Path Traversal | 通过 `..` 等路径逃出允许目录 |
| Atomic Write | 写临时文件后 rename，避免留下半个文件 |
| Happens-before | 并发事件之间被同步原语保证的先后关系 |
| Race Condition | 结果依赖不可控并发调度的竞态 |
| Linearization | 并发操作表现得像在某个瞬间原子发生 |
| Exactly-once | 关键副作用既不丢失也不重复 |
| At-least-once | 允许重复，但保证至少发生一次 |
| Idempotent | 重复执行仍等价于执行一次 |
| Negative Assertion | 断言某件不该发生的事确实没有发生 |
| Regression Guard | 防止已修复错误再次出现的测试 |
| Property-like Test | 验证一类输入都满足某个不变量，而非只测单点样例 |
| Quorum | 多个 verifier 达成结论所需的票数门槛 |
| Skeptic Panel | 多个对抗性 verifier 组成的验证小组 |
| Poisoned Turn | 因 doom loop 等原因应被丢弃、不能进入历史的模型轮次 |
| Doom Loop | 模型重复、空转或只推理不产生有效行动的循环 |
| Resample | 放弃某次模型结果并重新请求 |
| Telemetry | 为诊断和运营发出的结构化事件/指标 |
| Soak Test | 长时间或高次数运行以发现资源增长和偶发竞态 |
| RSS | 进程实际驻留在内存中的页规模 |
| FD | File Descriptor，文件、socket、pipe 等系统资源句柄 |
| Benchmark | 衡量时间、吞吐或资源表现的基准测试 |
| Flaky Test | 在代码不变时也会随机通过或失败的测试 |
| CI Lane | 持续集成中针对某类测试配置的独立运行通道 |
| Runfiles | Bazel 为测试提供源码和数据文件的运行时目录 |

---

## 152. 下一篇适合继续精读什么

下一篇建议回到用户最关注的 Agent 基础主链：

> **源码精读 28：Agent Context Lifecycle——一条用户消息怎样经过 Prompt Parser、Chat History、Context Window、Compaction、Rewind、Resume 与 Fork，最终变成下一次模型请求。**

前面的文章已经分别讲过 Prompt 拼接、Tool、Skill、Goal 和验证；下一篇可以把“上下文怎样跨 Turn 生长、压缩、回退和分叉”串成完整生命周期。
