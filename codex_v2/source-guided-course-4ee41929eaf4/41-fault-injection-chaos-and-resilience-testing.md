# 41：故障注入、混沌工程与韧性测试——主动把系统弄坏，证明它真的能恢复

第 38 章设计过载保护，第 39 章设计灰度、回滚和事故响应，第 40 章用 SLO 与错误预算衡量可靠性。本章进一步问：这些保护是不是只有在设计文档里看起来正确？如果网络在流式响应中间断开、队列突然满、进程恰好在迁移的一半崩溃、数据库损坏、时间推进到重试窗口，系统实际会怎样？

可靠性不能只由正常路径测试证明。故障注入是在受控条件下主动制造失败，验证系统能否保持安全、资源有界、终态清晰，并在故障解除后恢复。混沌工程则把这种方法扩展到更接近真实运行环境的实验：先定义稳定状态和可证伪假设，再限制爆炸半径，观察系统整体行为。

> 源码基线：`4ee41929eaf4`。本章引用当前仓库中真实存在的 mock server、断线、重试、fallback、队列满载、timeout、畸形 frame、迁移 journal 和 SQLite recovery 测试。生产环境混沌实验的组织权限、流量比例和内部平台不在公开仓库中，本章对此只给出通用安全模型，不声称它们是 OpenAI 内部做法。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分 failure、fault 和 error；
2. 区分 fault injection、chaos engineering、load test 和 fuzz test；
3. 从 steady state 写出可证伪韧性假设；
4. 为实验定义 blast radius、abort condition 和 rollback；
5. 识别网络、时间、资源、进程、数据和协议故障模型；
6. 解释 omission、delay、duplication、reordering 和 corruption；
7. 用 mock server 精确控制一次或多次 Responses 结果；
8. 测试 WebSocket 失败后的 retry、reconnect 与 HTTP fallback；
9. 用容量为 1 的 channel 稳定复现 overload；
10. 用 paused time 测试 backoff，而不是让测试真实等待；
11. 验证 timeout 覆盖整条逻辑操作，而不是每层重新计时；
12. 测试进程在持久化中间崩溃后的 journal recovery；
13. 区分 recovery、failover、fallback、retry 和 repair；
14. 为故障路径断言 safety、liveness、boundedness 和 durability；
15. 识别“测试得到了错误”但并未证明系统韧性的弱测试；
16. 设计一个可停止、可观察、可复现、不会扩大伤害的实验。

---

## 2. 先说人话：消防演习不是把整栋楼点着

消防系统平时看起来正常：警报器亮绿灯、灭火器在墙上、安全出口有标识。但真正着火时可能出现：

- 警报器没有声音；
- 人群堵住出口；
- 某扇防火门打不开；
- 负责人不知道该联系谁；
- 消防车到了却找不到入口。

所以需要演习。但合理的演习不是把整栋楼点着，而是：

1. 先规定要验证什么；
2. 选择有限楼层和参与者；
3. 使用可控烟雾或模拟信号；
4. 准备随时终止；
5. 观察警报、疏散、通信和恢复；
6. 记录哪条假设被推翻；
7. 修复系统，再重复演习。

软件故障注入也一样：目标是得到有关系统的证据，不是制造最大破坏。

---

## 3. Fault、Error、Failure 的区别

不同资料用词略有差异，本章采用一种常见模型：

```text
Fault → 产生内部错误状态 Error → 对外表现为 Failure
```

例子：

- Fault：网络 socket 在流中途被关闭；
- Error：client 内部失去当前 Responses stream；
- Failure：Turn 没有完成，用户收到断线终态。

也可能 fault 被成功掩蔽：

- Fault：第一次 WebSocket 建连失败；
- Error：当前 transport attempt 失败；
- Recovery：切到 HTTP；
- 用户结果：Turn 最终成功，没有顶层 failure。

故障注入要同时观察内部错误和外部结果，否则你只知道“最后成功”，不知道冗余是否已被大量消耗。

---

## 4. 四种容易混淆的测试

| 方法 | 主要改变什么 | 主要问题 |
|---|---|---|
| Fault injection | 主动制造特定失败 | 某个故障出现时行为是否符合设计？ |
| Chaos engineering | 在系统层做有假设、有护栏的实验 | 真实组合条件下系统是否仍保持稳态？ |
| Load/stress test | 增加工作量和资源竞争 | 容量边界、过载和恢复怎样？ |
| Fuzz test | 大量生成意外输入 | parser/protocol 是否崩溃、越界或违反不变量？ |

它们可以组合。例如在高并发下随机断开 5% 连接，既包含负载也包含故障注入。但组合之前应先分别验证基本行为，否则失败后很难定位是哪一维导致。

---

## 5. 单元级故障注入与生产混沌实验不是一回事

### 5.1 单元/集成级注入

在测试进程中控制：

- mock server 返回状态；
- channel 容量；
- fake clock；
- 临时文件；
- dependency trait；
- socket 关闭；
- task cancellation。

优点是确定、快速、影响小、可在 CI 重复。

### 5.2 系统级混沌实验

在更真实环境中控制：

- 实例退出；
- 网络分区或延迟；
- 依赖错误；
- 资源饱和；
- 配置传播异常；
- 版本混合。

它验证测试替身没覆盖的系统交互，但风险更高。

正确顺序通常是：

```text
确定性小测试 → 组件测试 → 隔离环境实验 → 小爆炸半径真实实验
```

生产混沌不能替代便宜的回归测试；回归测试也不能证明真实部署、网络和操作流程全部正确。

---

## 6. 混沌工程的核心不是“随机”而是“假设”

一个实验应写成：

```text
给定：系统在稳态，Turn completion SLI 正常
当：单个 Responses WebSocket 在第一次请求时不可用
那么：有限重试后切换 HTTP；Turn 最终完成；没有重复 tool side effect；
      p95 仍在实验 guardrail 内；故障解除后后续 Turn 不反复冲击失败路径
```

这是一条可证伪假设。如果实验只写“随机杀一些进程看看”，即使什么也没发生，你也不知道是系统有韧性，还是根本没有打到目标路径。

---

## 7. Steady State 是什么

Steady state 不是“所有指标完全不变”，而是实验前系统处于可接受范围。

可描述为：

- Turn good-event 比例；
- p95/threshold latency；
- queue depth；
- in-flight；
- retry/fallback rate；
- terminal event pairing；
- DB error；
- crash/restart count；
- 数据恢复探针；
- 安全不变量。

实验后要判断是否回到同一稳态范围，而不只是故障注入动作已经停止。

---

## 8. 实验的五道安全边界

### 8.1 Scope

明确组件、版本、平台、用户群和时间范围。

### 8.2 Blast radius

限定最多影响多少实例、请求、thread 或测试数据。

### 8.3 Abort condition

提前定义何时立即停止，例如数据完整性异常、错误率超过阈值、影响扩散到 control。

### 8.4 Recovery action

准备恢复依赖、撤销网络规则、重新启用实例、关闭 feature、清理测试数据。

### 8.5 Observation

确认 dashboard、日志、trace、稳定 ID 和值班人已就绪。

任何一项缺失，都不适合扩大实验。

---

## 9. 故障模型一：Omission

Omission 表示某个预期动作没有发生：

- 请求没有送达；
- response event 丢失；
- terminal event 没有产生；
- 写入没有完成；
- heartbeat 消失。

测试问题：

```text
等待会不会永远挂住？
有没有 deadline？
调用方能否区分“仍在运行”和“已经丢失”？
重试会不会重复副作用？
```

网络断线和进程 crash 常表现为 omission，但底层原因可以不同。

---

## 10. 故障模型二：Delay

Delay 表示动作最终可能完成，但晚于预期：

- DNS/route resolution 很慢；
- 首 token 很慢；
- tool callback 阻塞；
- approval 很久没有回答；
- queue 消费者停顿；
- migration 等待 live writer。

延迟比明确失败更危险，因为资源仍被占用，上游可能同时重试。

需要验证：

- timeout 覆盖哪一段；
- cancellation 是否传递到底层；
- permit/锁是否释放；
- late result 会不会覆盖新状态；
- 用户是否收到明确进度或终态。

---

## 11. 故障模型三：Duplication

消息、请求或副作用可能被重复执行：

- client 超时后重试，但第一次其实已成功；
- reconnect 后 replay 同一 tool call；
- journal recovery 再次处理已发布文件；
- queue consumer 崩溃前已执行但未 ack。

这要求：

- idempotency key；
- stable call ID；
- compare-and-set；
- durable completion marker；
- 去重或可重复执行语义。

“最终只有一个成功 response”不证明副作用只发生一次。测试必须检查真实 side-effect count 或最终整个对象。

---

## 12. 故障模型四：Reordering

异步系统中消息可能乱序：

```text
completed 比部分 delta 更早被某消费者观察
旧连接的迟到 response 在新连接后到达
取消通知和工具完成交叉
迁移 cursor 前进与 journal 清理顺序错误
```

测试应检查：

- sequence/revision；
- terminal 后是否拒绝迟到 mutation；
- 同一 ID 的状态转换是否合法；
- 是否以 authoritative final item 对账；
- 持久化顺序能否在 crash 后恢复。

---

## 13. 故障模型五：Corruption

Corruption 表示数据存在但内容无效：

- 畸形 JSON/WebSocket frame；
- 截断 rollout；
- SQLite `file is not a database`；
- checksum 不匹配；
- 无效 UTF-8 路径；
- schema version 与内容不一致。

理想行为通常是：

- 限制损坏影响范围；
- 返回明确分类错误；
- 不 panic 整个 listener/process；
- 保存原始证据；
- 只修复/隔离确切目标；
- 恢复后重新验证完整性。

---

## 14. 故障模型六：Resource Exhaustion

资源耗尽包括：

- channel full；
- semaphore permits 用完；
- file descriptor 用完；
- 磁盘满；
- 内存压力；
- connection limit；
- thread/task 数过多；
- telemetry queue 堆积。

第 38 章已经说明系统必须有界。本章关注怎样证明：

```text
达到上限 → 按协议等待/拒绝/丢弃/断开
故障期间 → 资源不继续无界增长
负载解除 → permit、连接和队列可恢复
```

只测“第 N+1 个请求被拒绝”不够，还应测已有 N 个能完成，并且完成后新请求又可进入。

---

## 15. 故障模型七：Process Crash

进程可能在任意 await 或系统调用之间退出：

```text
写临时文件之后
rename 正式文件之前
正式文件发布之后
数据库 metadata 提交之前
journal 删除之前
terminal event flush 之前
```

Crash test 的核心不是优雅 shutdown，因为 crash 没有机会执行析构和清理。必须依靠：

- 写入顺序；
- atomic rename；
- fsync/sync_all；
- journal/WAL；
- 幂等恢复；
- 可检测的中间状态。

协作式 cancellation 测试和真正 kill -9 测试验证的边界不同。

---

## 16. 故障模型八：Clock 与时间

重试、缓存、lease、deadline、cooldown 都依赖时间。

常见风险：

- 测试真的 sleep 30 分钟；
- wall clock 回拨导致负 duration；
- 每次 retry 重置总 deadline；
- 多客户端在同一时间醒来；
- expired cache 被错误继续使用；
- paused time 下 task 没有让出执行导致测试挂住。

Rust/Tokio 测试可用：

```rust
#[tokio::test(start_paused = true)]
async fn reconnect_after_backoff() {
    // trigger failure
    tokio::time::advance(Duration::from_secs(30)).await;
    // assert retry becomes eligible
}
```

Fake/paused time 让分钟级策略在毫秒级测试完成，并减少 CI 抖动。

---

## 17. 一个好故障测试要断言六件事

### 17.1 Safety

不发生未授权副作用、不损坏其他 thread、不接受畸形状态。

### 17.2 Boundedness

队列、重试、内存、任务和等待时间有上限。

### 17.3 Liveness

故障解除后，系统最终继续推进，而不是永久卡住。

### 17.4 Correct terminal state

成功、失败、取消只能有一个权威终态，错误分类正确。

### 17.5 Durability

重启或重读后，已确认状态仍一致；中间状态可恢复。

### 17.6 Observability

日志、metric、event 或 trace 能说明发生了什么和采用了哪条恢复路径。

只断言函数返回 `Err`，通常只覆盖第 4 项的一小部分。

---

## 18. Codex 的 Responses Mock 提供什么

Core integration tests 常用 `core_test_support::responses`：

- 启动本地 mock server；
- 用 `ev_response_created`、tool call、`ev_completed` 等构造 SSE；
- `mount_sse_once` 返回一次响应；
- `mount_sse_sequence` 按顺序返回多次响应；
- `ResponseMock` 保存实际 `/responses` 请求；
- 测试可检查 input、tool output、headers 和调用次数。

这使测试能够精确表达：

```text
第一次 sampling：断线
第二次 sampling：返回 tool call
工具结果回传后：完成
```

同时断言不是只看 UI 文本，而是检查模型真正收到的下一次请求。

---

## 19. 为什么要保存 Mock handle

仓库 `AGENTS.md` 明确建议保存 `mount_sse*` 返回的 `ResponseMock`，然后使用：

- `single_request()`；
- `requests()`；
- `function_call_output(call_id)`；
- `input()`；
- `header()` 等结构化 helper。

故障测试尤其需要它，因为你要回答：

- retry 实际发了几次；
- fallback 是否 replay 了一次；
- tool result 是否重复；
- 新请求是否携带正确 previous response state；
- 失败 attempt 是否污染后续 context。

只等到 `TurnComplete` 会错过这些内部不变量。

---

## 20. 真实案例一：WebSocket 失败后切换 HTTP

`codex-rs/core/tests/suite/websocket_fallback.rs` 包含多种场景。

### 20.1 Upgrade Required

Mock 对 WebSocket handshake 返回 HTTP 426。测试期望：

- startup prewarm 只尝试一次 WebSocket；
- 会话立即切到 HTTP；
- 第一个 Turn 直接通过 HTTP 成功；
- 没有额外无意义的 WebSocket retry。

### 20.2 Retry exhausted

当 WebSocket 一般性失败：

- prewarm 有一次尝试；
- Turn 进行 initial + 2 retries；
- 重试耗尽后只 replay 一次 HTTP；
- mock SSE 只收到一个有效 HTTP request。

### 20.3 Sticky fallback

第二个 Turn 继续使用 HTTP，不再次冲击已知失败的 WebSocket 路径。

这组测试同时验证 failure detection、有限重试、fallback、去重和跨 Turn 恢复状态。

---

## 21. 为什么 Sticky Fallback 也是韧性的一部分

如果每个 Turn 都重新做三次失败 WebSocket 尝试：

- 用户每次都承担额外延迟；
- 失败依赖持续被冲击；
- retry traffic 放大；
- 日志和告警充满重复噪声。

Sticky fallback 表示在合适作用域记住恢复选择。

但 sticky 不能永久锁死：真实系统还要定义何时探测恢复、重启会话或清除状态。测试应覆盖：

```text
故障时不重复冲击
恢复探测时不会惊群
恢复成功后可以回到主路径
```

当前测试能确认跨 Turn 保持 HTTP；更长周期的探测策略需要从具体实现另行验证。

---

## 22. 真实案例二：连接寿命错误后重连

`client_websockets.rs` 的 `responses_websocket_connection_limit_error_reconnects_and_completes` 构造服务端错误：

```text
websocket_connection_limit_reached
```

Mock server 第一条连接返回该错误，第二条连接返回正常 completed event。

测试断言：

- Turn 最终完成；
- 一共发生两次 WebSocket request；
- 两次 handshake 都有预期 User-Agent。

这不是把所有 400 都重试，而是识别一个具有恢复语义的特定错误：旧连接达到限制，应新建连接继续。

错误分类必须足够精确，否则：

- 把 BadRequest 重试会浪费预算；
- 不重试连接寿命错误会无谓失败；
- 无界重连会形成循环。

---

## 23. 真实案例三：App-server 队列满载

`app-server-transport/src/transport/mod.rs` 的测试直接创建容量为 1 的 channel。

### 23.1 Request queue full

先放入一条 notification 占满 transport queue，再提交 JSON-RPC request。

期望：

- request 不进入已满队列；
- writer 收到相同 request ID 的 overload error；
- error code 是 `OVERLOADED_ERROR_CODE`；
- message 明确要求稍后重试。

### 23.2 Response queue full

Response 不能像新 request 一样随便拒绝，否则原始 RPC 永远等不到配对结果。测试证明 enqueue 会等待空间，随后 response 被转发。

### 23.3 Writer queue 也满

连 overload error 都发不出去时，请求路径仍不应永久阻塞。测试用 100ms timeout 证明函数返回，原有 writer message 保持不变。

这是很好的分层故障注入：不仅让第一条队列满，还让“报告过载”的第二条队列也满。

---

## 24. 容量测试为什么使用 1 而不是生产常量

如果真实容量是 128，测试要可靠填满 128 并控制消费者暂停，代码更复杂，也更易受调度影响。

容量 1 可以构造最小状态：

```text
0 → 还有空间
1 → 刚好满
再入 1 → 必须触发满载策略
```

它验证的是状态转换和协议语义，不是 benchmark 真实吞吐。

另一个独立测试可以检查生产常量或容量配置，但不要把“填满 128”当作过载语义的唯一证明。

---

## 25. 真实案例四：共享 Deadline

`route_aware_client_pool_tests.rs` 有两个关键测试。

### 25.1 Route selection 也受 timeout 约束

Resolver 故意 sleep 100ms，请求总 timeout 为 10ms。期望在 route selection 阶段就返回 `Timeout`。

如果 timeout 只包真正 HTTP send，DNS/proxy route 卡住仍可无限等待。

### 25.2 Redirect hops 共享同一 timeout

总预算 2 秒：

- 第一次 route resolution 消耗 500ms；
- redirect 后第二次 resolution 消耗 1750ms；
- 总计超过 2 秒，应 timeout。

错误实现会为每个 hop 重新给 2 秒，使整体达到 2.25 秒甚至更多。

故障测试必须围绕用户的总 deadline，而不只是每个局部 future 都有 timeout。

---

## 26. Timeout 测试最常见的假阳性

这种测试很弱：

```rust
let result = timeout(Duration::from_secs(1), operation()).await;
assert!(result.is_err());
```

它只说明测试外层等了一秒，不能证明：

- operation 内部停止了；
- socket/task 已取消；
- permit 已释放；
- late result 不会写状态；
- 后续请求能成功；
- telemetry 记录正确原因。

更完整的测试应在 timeout 后继续：

1. 检查底层 cancellation/连接；
2. 检查资源计数；
3. 解除故障；
4. 再发一个请求；
5. 断言恢复并且没有迟到副作用。

---

## 27. 真实案例五：迁移中断后的 Journal Recovery

`thread-store/src/local/rollout_migration/publish.rs` 说明：迁移可能在发布 JSONL 和完成 SQLite metadata 之间崩溃，`.pending` journal 是耐久交接标记。

`startup_tests.rs` 的 `recovers_pending_migrations_behind_the_checked_thread`：

1. 创建 legacy rollout；
2. 完成一次迁移并推进 startup cursor；
3. 删除 projection，模拟 metadata 缺失；
4. 手工写 `.pending` journal，模拟 crash 中间态；
5. 再次运行 startup migration；
6. 断言 journal 被清理；
7. projection 被恢复；
8. rollout metadata 仍是 Paginated。

它验证一个容易漏掉的情况：pending migration 位于 cursor 之后/之前的常规扫描边界外，也必须优先恢复。

---

## 28. 迁移测试为什么要检查整个恢复结果

弱断言：

```text
migrate_rollouts_on_startup() 返回 Ok
```

强断言：

- `.pending` 不再存在；
- SQLite projection 存在；
- rollout metadata 是目标 history mode；
- 源数据没有丢失；
- cursor 合理；
- 再执行一次仍得到同样状态。

返回 Ok 可能只是错误地跳过了工作。恢复测试必须检查耐久状态，而不是函数情绪。

---

## 29. 真实案例六：迁移与 Live Writer 竞争

同一测试文件的 `waits_for_a_live_writer_before_migrating`：

1. 持有某个 thread 的 live writer lock；
2. 启动 migration task；
3. 用短 timeout 证明 migration 尚未完成；
4. 释放 writer guard；
5. 等待 migration 完成；
6. 验证 history mode 已变为 Paginated。

它注入的不是网络故障，而是并发占用。

不变量是：

```text
live writer 存在时不迁移同一 rollout
lock 释放后 migration 最终继续
```

同时证明 safety 和 liveness。只证明“会等”可能得到永久死锁；只证明“最终迁移”可能在 writer 活跃时破坏文件。

---

## 30. 真实案例七：畸形 WebSocket 不拖垮 Listener

`code-mode-host/tests/websocket.rs` 的 `malformed_websocket_frame_does_not_stop_the_listener`：

1. 启动真实 host child process；
2. 连接 WebSocket；
3. 发送畸形 binary frame；
4. 观察该连接被关闭；
5. 从 stderr 等到明确诊断；
6. 再建立正常连接；
7. 完成 negotiation。

这验证 fault containment：坏连接应被隔离，listener 仍能服务其他连接。

如果测试只断言畸形连接关闭，host 进程可能已经一起退出。最后的正常连接是恢复/隔离证明的关键部分。

---

## 31. 真实案例八：SQLite Corruption 分类与备份

`state/src/runtime/recovery_tests.rs` 验证：

- `file is not a database`、`database disk image is malformed` 被识别为 corruption；
- `database is locked` 不被误判为 corruption；
- 能从 error chain 找到实际失败数据库路径；
- 路径名字里包含 `corrupt` 不应触发误判；
- backup 只移动指定 runtime DB 文件。

为什么分类重要？

```text
locked → 等待/重试/查持锁者
corrupt → 隔离、备份、重建/恢复
```

若把暂时锁竞争当成损坏，自动恢复可能不必要地移动健康数据库；若把真实损坏当暂时错误，会无限重试。

---

## 32. Paused Time：把 30 分钟 Backoff 压缩成瞬间

`codex-mcp/src/connection_manager_tests.rs` 有 `#[tokio::test(start_paused = true)]` 和 `tokio::time::advance(...)`。

适合验证：

- TTL 到期；
- reconnect initial backoff；
- retry_not_before；
- cache refresh；
- cooldown；
- deadline transition。

测试模式：

```text
T0：触发失败
T0：立即再次调用，断言没有过早重连
advance(backoff - ε)：仍不重连
advance(ε)：允许一次探测
再次失败：backoff 增加或保持上限
恢复依赖：下一探测成功
```

要断言边界前后，而不是只推进到很久以后看“终于重试了”。

---

## 33. Determinism：可靠性测试本身也要可靠

Flaky fault test 会让团队忽略真正回归。

提高确定性：

- 使用 mock server，而非公共网络；
- 使用 temporary directory；
- channel 容量设为 1；
- 用 barrier/semaphore 明确 task 到达某点；
- paused clock 代替 sleep；
- 保存 mock handle 并检查调用次数；
- 使用 stable ID；
- timeout 仅作为测试防挂护栏，不作为主要同步方式；
- 断言完整对象或最终状态；
- cleanup 与测试数据隔离。

`sleep(100ms)` 猜调度时序通常是 race 的来源。更好的是等明确“已开始”信号，再释放故障。

---

## 34. Barrier、Semaphore 和 Channel 怎样控制测试时序

假设要测试慢 tool callback 不阻塞其他 session：

```text
slow callback 启动 → 发送 started 信号
slow callback 等待 release permit
测试收到 started → 启动 fast session
断言 fast session 在 deadline 内完成
测试释放 permit → slow callback 完成
```

这比“让慢任务 sleep 2 秒”更稳定，也更快。

Code Mode WebSocket 测试就使用 semaphore 协调大工具回调，使另一个 session 的进展成为可控断言。

---

## 35. Failpoint 是什么

Failpoint 是代码中专门允许测试在某位置失败、暂停或崩溃的控制点。

例子：

```text
after_temp_file_sync
before_atomic_rename
after_publish_before_metadata_commit
before_journal_cleanup
```

优点：可以稳定覆盖极窄 crash window。

风险：

- 生产构建意外启用；
- failpoint 本身改变时序；
- 为测试侵入大量业务代码；
- 只测试人工点，没有覆盖未知中断点。

引入时应最小化公开 API，默认关闭，并用 feature/test-only wiring 清楚隔离。仓库当前 journal 测试主要通过构造中间状态验证恢复，不代表所有路径都使用通用 failpoint 框架。

---

## 36. Network Fault 注入的层级

### 36.1 Protocol response

Mock 返回 426、429、500、畸形 event。最确定，适合业务分类。

### 36.2 Stream behavior

连接成功后中途关闭、只发半个 frame、长期不发数据。适合 idle timeout 和 partial progress。

### 36.3 Socket behavior

拒绝连接、reset、broken pipe。适合 transport error。

### 36.4 Route/DNS/proxy

resolution 延迟、代理不可用、redirect loop。适合请求总预算和路由恢复。

### 36.5 Network partition

更真实环境阻断方向/端口。适合系统级实验，但风险和不确定性更高。

先选择能触发目标分支的最窄层级，不必每个测试都操作 OS 网络。

---

## 37. Error Response 与 Connection Drop 不可互换

服务端返回 503：

- 请求已到达服务；
- client 能读取 status/header；
- 可能有 Retry-After；
- body 可携带结构化错误。

连接中途 drop：

- 不知道服务端处理到哪一步；
- 可能已经发生副作用；
- 没有完整错误 body；
- retry 的幂等性风险更高。

因此要分别测试。用 `Err(anyhow!("network"))` 代替所有网络情况会丢失关键语义。

---

## 38. Cancellation 注入

取消是一种预期控制流，也能暴露清理 bug。

可在这些时点取消：

- sampling 前；
- streaming 中；
- tool call 发出后、结果前；
- approval 等待中；
- rollout 写入中；
- migration 等锁时；
- reconnect backoff 中。

断言：

- 唯一 TurnAborted；
- running task 清理；
- pending approval/input 清理；
- permit/lock 释放；
- child process 是否按策略终止；
- rollout 有 interrupted marker；
- resume 后 history 自洽；
- late tool result 不改变已终止 Turn。

第 27、32 章的取消和终态知识在这里转化为实验矩阵。

---

## 39. Fallback、Failover、Retry、Recovery、Repair

| 词 | 含义 | 示例 |
|---|---|---|
| Retry | 再试同一路径 | WebSocket stream 再尝试一次 |
| Fallback | 切到能力可能不同的备用路径 | WebSocket → HTTP |
| Failover | 切到冗余实例/节点 | 主实例 → 备用实例 |
| Recovery | 系统回到可接受稳态 | listener 隔离坏连接后继续服务 |
| Repair | 修正已损坏/不一致状态 | 依据 journal 重建 projection |
| Restart | 重新启动进程/组件 | 可能是恢复动作的一部分 |

测试名称和文档要用准确词。重启成功不代表数据已 repair；一次 retry 成功也不代表系统完成了 failover。

---

## 40. Recovery Time 与 Recovery Point

### 40.1 RTO

Recovery Time Objective：故障后多久恢复服务。

### 40.2 RPO

Recovery Point Objective：最多允许丢失多新的数据。

例子：

```text
RTO = 5 分钟：五分钟内恢复 thread 服务
RPO = 0：所有已确认持久化的 terminal event 都不能丢
```

对本地 agent，RPO 边界取决于什么时候向 client 声称“已完成/已保存”。如果先通知、后异步 flush，crash window 可能使用户看过的成功结果消失。

RTO/RPO 示例不是 Codex 官方承诺；它们是设计恢复实验时必须明确的目标。

---

## 41. Graceful Degradation 的实验

若非关键依赖失败，系统可能降级：

- WebSocket → HTTP；
- remote catalog → 已验证 cache；
- optional MCP 不可用 → 核心工具仍可工作；
- telemetry exporter 失败 → 业务行为不依赖导出成功；
- UI enrichment 失败 → 核心终态仍显示。

实验应验证：

1. 降级被明确记录；
2. 用户知道缺少什么能力；
3. 安全策略不被放宽；
4. 数据语义仍正确；
5. 降级资源有界；
6. 主路径恢复后有清晰返回策略。

“catch 所有错误并继续”不是优雅降级，可能是静默损坏。

---

## 42. Telemetry 故障也要注入

官方 Codex 配置文档说明 OTel exporter 异步批量发送并在 shutdown flush；源码的 DB telemetry trait 注释还要求 delivery failure 不影响数据库行为。

可测试：

- exporter endpoint 不可达；
- exporter queue 满；
- shutdown flush 超时；
- metric backend 慢；
- invalid metadata；
- prompt redaction 保持；
- telemetry disabled。

不变量：

```text
观测失败不能让核心 Turn/DB 失败
观测队列不能无界增长
敏感内容不能因 fallback 意外泄露
系统应能观测“观测系统坏了”这一事实
```

官方入口：[Observability and telemetry](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry)。

---

## 43. 安全故障注入

安全相关实验不能只验证 availability：

- approval service timeout → 应 fail closed 还是返回用户？
- Guardian malformed output → 不应默认为允许；
- sandbox unavailable → 是否阻止高风险命令；
- MCP identity 缺失 → 是否拒绝调用；
- hook 失败 → 是 block、warn 还是忽略；
- network approval 丢失 → 不能自动扩权。

核心原则：

```text
可用性恢复不能以绕过安全边界为代价。
```

故障注入时也不能使用真实凭据、真实破坏命令或不受控外部副作用。用 fake reviewer、temporary workspace 和 mock server 验证策略。

---

## 44. Side Effect 测试必须使用安全替身

不要为了验证重复支付保护真的发两次真实支付；不要为了测试删除保护真的删生产目录。

替代方式：

- in-memory fake 记录调用；
- temporary directory；
- local mock HTTP server；
- test database；
- disposable sandbox；
- dry-run adapter；
- fake clock/queue；
- synthetic tenant。

然后断言：

```text
logical call count = 1
side effect record = exactly one
retry attempts = expected N
final object = expected complete state
```

测试故障处理不应本身成为事故。

---

## 45. 一份完整实验模板

```text
实验名称：Responses WebSocket 首次连接失败

目的：验证主传输不可用时 Turn 仍能通过 HTTP 完成

Steady state：
- 测试 Turn 成功
- retry/fallback counter 为 0
- 无 pending task

假设：
- 第一次 WebSocket handshake 返回 426 后立即 fallback
- HTTP 只收到一次逻辑请求
- Turn 产生唯一成功终态
- 后续 Turn 保持 HTTP，不重复冲击 WebSocket

Fault：
- mock server 对 GET /responses 返回 426

Blast radius：
- 单个测试 session、temporary server、无外部副作用

Observation：
- handshake count
- HTTP request count/body
- StreamError/TurnComplete events
- fallback telemetry

Abort：
- 测试总 deadline 10 秒

Recovery：
- drop mock server/session；无持久生产状态

Success criteria：
- 所有假设断言通过
```

这份模板同样适用于更真实环境，只是 blast radius、审批和 recovery 要更严格。

---

## 46. 从一张 Failure Matrix 开始

为一条 Agent Turn 画矩阵：

| 阶段 | 注入故障 | 期望终态 | 关键不变量 |
|---|---|---|---|
| Accept | inbound queue full | overload error | 不阻塞、不丢配对 response |
| Sampling connect | 426/拒绝连接 | fallback 或明确失败 | retry 有限 |
| Streaming | 中途断开 | reconnect/final error | context 不重复 |
| Tool dispatch | handler error | tool output/error | 安全审批仍有效 |
| Tool running | cancellation | TurnAborted | child/permit 清理 |
| Approval | timeout/deny | fail closed | 不执行副作用 |
| Persistence | crash after publish | startup repair | 已确认数据不丢 |
| Resume | corrupt DB | isolate/backup/error | 不误伤其他 DB |
| Output | client disconnect | durable terminal state | 重连后可对账 |

矩阵迫使你覆盖生命周期，而不是只在网络层反复测试 500。

---

## 47. Property-Based 与 Fuzz 怎样补充场景测试

场景测试验证已知危险序列。Property/fuzz 更适合发现未知组合。

可定义属性：

- 任意 frame bytes 不应让 listener process panic；
- 任意事件序列最多产生一个 terminal state；
- 任意 cancel 点后 active task 最终清零；
- 任意合法 migration 重跑两次结果相同；
- 任意 redirect sequence 不超过 hop/deadline 上限；
- 任意 queue 操作不超过容量；
- 任意 error string 不泄露 URL secret。

发现最小反例后，应把它保存成普通回归测试 fixture，避免以后只能依赖随机种子重现。

---

## 48. Model-Based Testing

复杂状态机可先写简化模型：

```text
Disconnected → Connecting → Ready
Ready → Failed → Backoff
Backoff → Connecting
Ready → Closed
```

生成动作：connect、fail、advance time、call、shutdown。

每步比较：

- 实现状态；
- 模型允许状态；
- 可见事件；
- 资源计数。

适合 MCP reconnect、WebSocket session、Turn lifecycle 和 migration state。模型无需复制全部代码，只保留关键不变量；若模型和实现一样复杂，就失去价值。

---

## 49. Metamorphic Testing

当结果难以预先精确写出，可以验证输入变化间关系：

- 同一 migration 执行两次，第二次不改变最终状态；
- 在不改变语义的事件间插入 telemetry failure，业务结果不变；
- 把一次网络延迟缩短，结果不应从成功变失败；
- 将非关键 MCP 移除，核心本地 tool 仍可完成；
- response 分块方式变化，拼接后的最终 item 相同。

这对流式协议和 Agent 输出特别有用，因为具体 delta 数量可能变化，但最终投影必须一致。

---

## 50. Soak Test 与恢复后的慢性问题

一次 30 秒实验可能看不到：

- 每次 reconnect 泄漏一个 task；
- fallback 后旧 socket 没关闭；
- journal 文件不断积累；
- cache 只增不减；
- error log 导致磁盘增长；
- retry counter 溢出；
- semaphore permit 在少数错误分支泄漏。

Soak test 在较长时间重复故障—恢复循环，观察：

- RSS/heap trend；
- task/connection/file descriptor count；
- queue depth；
- journal/temp files；
- latency baseline；
- recovery time 是否逐轮变差。

时长应与风险相称；CI 可做短 smoke，定时环境做长 soak。

---

## 51. Game Day

Game day 是多人参与的计划性演练，重点不只在代码：

- 告警是否到达；
- 值班能否识别影响；
- runbook 是否可执行；
- 权限是否足够；
- 沟通是否一致；
- rollback/flag 是否有效；
- 数据 repair 是否有人负责；
- 恢复验证是否覆盖 backlog。

它连接第 39 章的 incident response 和本章的 fault injection。

Game day 应使用明确脚本、批准、观察者和停止条件。没有通知的“惊喜演习”会破坏信任，也可能让真实值班误判。

---

## 52. 生产实验前的最低清单

- 已有小范围自动测试证明基本机制；
- 实验假设可证伪；
- 目标 SLI/SLO 和 dashboard 可用；
- control group 存在；
- blast radius 有技术限制，不只口头约定；
- abort 可立即执行；
- recovery 已演练；
- 无不可逆真实副作用；
- 数据/隐私边界明确；
- 当班负责人知情；
- 变更冻结和其他事故已检查；
- 实验时区、开始/结束时间和记录人明确。

如果系统已经在事故中，通常不应再叠加探索性故障，除非该动作本身是经批准的诊断或恢复步骤。

---

## 53. 常见反模式

### 53.1 Random kill without hypothesis

随机杀进程但没有 steady state 和预期结果，实验无法解释。

### 53.2 只断言出现错误

没有验证资源、终态、数据和恢复。

### 53.3 用 sleep 猜时序

CI 快慢变化就 flaky。

### 53.4 外层 timeout 代替 cancellation 验证

测试返回了，但底层工作仍泄漏。

### 53.5 只测第一次失败

没有解除 fault 后的 recovery phase。

### 53.6 所有网络故障统一成 500

遗漏连接拒绝、中途断开、partial response 和 ambiguous side effect。

### 53.7 生产实验没有技术 blast-radius 限制

“我们会小心”不是控制措施。

### 53.8 自动修复比故障更危险

误把 lock 当 corruption，移动健康数据库。

### 53.9 Fallback 静默降低安全

为了成功率绕过审批、sandbox 或身份验证。

### 53.10 只看系统没崩

可能所有请求都失败、数据丢失或用户无限等待。

### 53.11 实验结束即宣布恢复

队列、重试、journal 或错误预算还未回稳。

### 53.12 不把发现变成回归测试

下一次仍需人工实验才能发现同一问题。

---

## 54. 从发现到永久防线

混沌实验发现问题后，按成本向下沉淀：

```text
生产/系统实验发现失败
        ↓
提炼最小故障序列
        ↓
写确定性 integration test
        ↓
能更小则写 component/unit/property test
        ↓
增加 metric/alert/runbook
        ↓
修复后重复原实验
```

最高价值不是“本次顶住了”，而是让同类故障以后在更早、更便宜的层级被发现。

---

## 55. 本章词汇表

| 英文 | 中文直觉 | 本章中的具体含义 |
|---|---|---|
| Fault | 故障原因 | 被主动注入或自然出现的底层异常条件 |
| Error | 错误状态 | Fault 在系统内部造成的不正确状态 |
| Failure | 服务失败 | 系统对外未提供预期服务 |
| Fault injection | 故障注入 | 在受控条件下主动制造具体失败 |
| Chaos engineering | 混沌工程 | 基于稳态假设、护栏和真实系统实验验证韧性 |
| Resilience | 韧性 | 遭遇故障时限制影响并恢复服务的能力 |
| Steady state | 稳态 | 实验前后系统应保持的可接受行为范围 |
| Hypothesis | 假设 | 实验准备证伪的明确预期 |
| Blast radius | 影响半径 | 实验最多能影响的实例、请求、用户或数据范围 |
| Abort condition | 中止条件 | 达到后立即停止实验的预定义边界 |
| Fault containment | 故障隔离 | 阻止局部故障扩散到其他连接/组件 |
| Omission | 遗漏 | 预期消息、响应、写入或终态没有发生 |
| Delay | 延迟 | 操作完成时间远晚于预期 |
| Duplication | 重复 | 同一消息、请求或副作用执行多次 |
| Reordering | 乱序 | 事件到达顺序不同于逻辑顺序 |
| Corruption | 损坏 | 数据存在但格式、内容或校验无效 |
| Resource exhaustion | 资源耗尽 | 队列、连接、内存、磁盘或 permit 达到上限 |
| Network partition | 网络分区 | 两组组件之间无法通信但各自仍在运行 |
| Failpoint | 故障点 | 为测试在特定代码位置失败/暂停的控制点 |
| Mock server | 模拟服务器 | 可编程返回固定响应和记录请求的测试服务 |
| Fake clock | 假时钟 | 可由测试推进、不依赖真实等待的时间源 |
| Paused time | 暂停时间 | Tokio 中冻结并手动 advance 的测试时间 |
| Determinism | 确定性 | 相同条件下测试稳定得到相同结果 |
| Flaky test | 不稳定测试 | 在代码未变时也随机通过或失败的测试 |
| Safety | 安全性 | 故障期间不发生不可接受动作或损坏 |
| Liveness | 活性 | 条件恢复后系统最终继续推进 |
| Boundedness | 有界性 | 资源、重试和等待不无限增长 |
| Idempotency | 幂等性 | 同一操作重复执行仍得到等价最终状态 |
| Failover | 故障转移 | 从失败主实例切到冗余实例 |
| Fallback | 备用回退 | 切到能力可能不同的备用实现/传输 |
| Recovery | 恢复 | 系统回到可接受稳态 |
| Repair | 修复数据 | 改正已经损坏或不一致的持久状态 |
| RTO | 恢复时间目标 | 故障后恢复服务允许的最长时间 |
| RPO | 恢复点目标 | 故障时最多允许丢失多新的已确认数据 |
| Crash consistency | 崩溃一致性 | 任意中断点后数据仍可识别和恢复 |
| Journal | 日志/事务标记 | 记录未完成持久化步骤的耐久恢复线索 |
| Soak test | 浸泡测试 | 长时间重复运行以发现慢性泄漏和退化 |
| Game day | 故障演练日 | 多角色验证告警、响应、权限和恢复的计划演练 |
| Property-based test | 属性测试 | 生成大量输入验证通用不变量 |
| Model-based test | 模型测试 | 将实现状态转换与简化状态机比较 |
| Metamorphic test | 变形测试 | 验证输入变化前后输出关系而非固定输出 |

### 故障测试代码单词短语拆解

- `fault`：缺陷/故障点；导致问题的条件。
- `failure`：失败；对外没有完成承诺。
- `inject`：注入；主动把条件放进系统。
- `chaos`：混沌；真实组合条件下的不确定故障。
- `resilient`：有韧性；受冲击后仍能维持或恢复。
- `steady`：稳定；处于可接受常态。
- `hypothesis`：假设；可以被实验推翻的预期。
- `blast radius`：爆炸半径；可能受影响的最大范围。
- `abort`：中止；立即停止实验或操作。
- `contain`：遏制；不让故障向外扩散。
- `omit`：遗漏；该发生的动作没有发生。
- `delay`：延迟；动作很晚才发生。
- `duplicate`：重复；同一动作出现多次。
- `reorder`：重新排序；事件先后次序变化。
- `corrupt`：损坏；内容不可合法解释。
- `exhaust`：耗尽；资源用到没有剩余。
- `partition`：分区；组件间通信被切断。
- `drop`：丢弃/断开；根据上下文可指消息或连接。
- `reset`：重置；连接被强制终止或状态归初始。
- `malformed`：畸形；不符合协议格式。
- `partial`：部分；只收到完整内容的一部分。
- `ambiguous`：不明确；不知道副作用是否已经发生。
- `mock`：模拟对象；按测试脚本表现的替身。
- `fake`：伪实现；可由测试完全控制的简化依赖。
- `stub`：桩；返回固定结果的简单替身。
- `fixture`：固定样本；测试使用的准备数据。
- `harness`：测试支架；搭建系统、驱动输入和收集输出的工具。
- `barrier`：屏障；多个 task 到齐后再继续。
- `permit`：许可；Semaphore 中有限并发名额。
- `pause`：暂停；冻结时间或执行进展。
- `advance`：推进；手动让假时间向前。
- `deterministic`：确定；重复运行结果稳定。
- `flaky`：飘忽；测试随机失败。
- `liveness`：活性；最终能继续前进。
- `bounded`：有界；不会无限增长或等待。
- `idempotent`：幂等；重复执行最终效果等价。
- `fallback`：备用回退；主路径失败后换另一实现。
- `failover`：故障转移；换到冗余服务实例。
- `recover`：恢复；回到可接受工作状态。
- `repair`：修复；纠正已产生的坏状态。
- `journal`：事务日志；记录未完成操作供重启恢复。
- `soak`：浸泡；长时间反复运行。
- `game day`：演练日；团队共同进行故障响应演习。

---

## 56. 自测题

1. Fault、error 和 failure 有什么区别？
2. Fault injection、chaos engineering、load test 和 fuzz test 各解决什么问题？
3. 为什么生产混沌实验不能替代确定性 integration test？
4. 一个可证伪韧性假设应包含什么？
5. Steady state 为什么不等于所有指标不变？
6. 实验前必须定义哪五类安全边界？
7. Omission、delay、duplication、reordering 和 corruption 各是什么？
8. 为什么连接 drop 比明确 503 更难安全 retry？
9. 资源耗尽测试为什么还要覆盖恢复后重新准入？
10. 协作式 cancellation 与进程 crash 测试有什么区别？
11. Paused time 为什么比真实 sleep 更适合 backoff 测试？
12. 好故障测试需要断言哪六个方面？
13. `ResponseMock` 为什么必须保存并检查请求？
14. WebSocket 426 和一般连接失败为什么走不同 fallback 节奏？
15. Sticky fallback 解决什么问题，又需要怎样的恢复策略？
16. 连接寿命错误为什么适合重新建连，而普通 BadRequest 不适合？
17. App-server 为什么 request queue full 时拒绝，response queue full 时等待？
18. 当 writer queue 也满时，为什么 overload 路径不能阻塞？
19. 容量 1 的 channel 怎样稳定复现边界？
20. 为什么 redirect 每跳重新给完整 timeout 是错误的？
21. 外层 timeout 返回后还要检查什么？
22. `.pending` journal 怎样模拟 crash 中间态？
23. 为什么迁移返回 Ok 不能证明恢复正确？
24. Live writer 竞争测试怎样同时证明 safety 和 liveness？
25. 畸形连接测试为什么必须再建立一个正常连接？
26. 为什么 `database is locked` 不能当 corruption 处理？
27. Fake time 测试为什么要检查 backoff 边界前后？
28. Barrier 比 sleep 更适合控制并发时序的原因是什么？
29. Failpoint 有哪些收益和风险？
30. Network fault 可以在哪五个层级注入？
31. 取消应覆盖 Turn 生命周期中的哪些位置？
32. Retry、fallback、failover、recovery 和 repair 有什么区别？
33. RTO 和 RPO 分别是什么？
34. Graceful degradation 为什么不能放宽安全边界？
35. Telemetry exporter 故障应保持哪些不变量？
36. Side-effect 测试为什么必须使用安全替身？
37. Failure matrix 怎样避免只反复测试 HTTP 500？
38. Property、model-based 和 metamorphic test 各适合什么？
39. Soak test 能发现哪些短测试看不到的问题？
40. Game day 除了代码还验证什么？
41. 为什么系统已经在事故中时通常不再叠加探索性故障？
42. 怎样把一次系统实验发现沉淀成永久防线？

---

## 57. 源码检查点

1. `codex-rs/core/tests/common/responses.rs`
   - 查看 SSE/mock server helper、`ResponseMock` 和结构化请求断言入口。
2. `codex-rs/core/tests/suite/websocket_fallback.rs`
   - 查看 426、retry exhaustion、隐藏首个 retry 提示和 sticky HTTP fallback。
3. `codex-rs/core/tests/suite/client_websockets.rs`
   - 查看 connection-limit reconnect、terminal error、telemetry 和 WebSocket request harness。
4. `codex-rs/core/src/responses_retry.rs`
   - 查看有限 retry、delay、fallback 与 terminal error。
5. `codex-rs/core/src/responses_retry_tests.rs`
   - 查看不同 attempt/fallback 序列的状态断言。
6. `codex-rs/app-server-transport/src/transport/mod.rs`
   - 查看容量 1 的 request overload、response wait 和 writer-full 测试。
7. `codex-rs/app-server-transport/src/transport/unix_socket_tests.rs`
   - 查看连接 open/message/ping/close 与锁竞争测试。
8. `codex-rs/http-client/src/route_aware_client_pool_tests.rs`
   - 查看 route-selection timeout、跨 redirect 共享 deadline、连接失败和 cache eviction。
9. `codex-rs/http-client/src/transport_tests.rs`
   - 查看底层 transport 错误和 timeout 映射。
10. `codex-rs/thread-store/src/local/rollout_migration/publish.rs`
    - 查看 staged paths、journal、sync、publish 和 cleanup 顺序。
11. `codex-rs/thread-store/src/local/rollout_migration/startup_tests.rs`
    - 查看 pending migration recovery、cursor lookback 与 live writer 等待。
12. `codex-rs/thread-store/src/local/rollout_migration/rollback.rs`
    - 查看 migration rollback/replay 的恢复边界。
13. `codex-rs/state/src/runtime/recovery.rs`
    - 查看 SQLite corruption 分类、路径提取、backup 与恢复逻辑。
14. `codex-rs/state/src/runtime/recovery_tests.rs`
    - 查看 corruption/lock 区分和只备份目标数据库。
15. `codex-rs/state/src/runtime.rs`
    - 查看 runtime DB 初始化、integrity check 和 future migration 容忍测试。
16. `codex-rs/code-mode-host/tests/websocket.rs`
    - 查看畸形 frame 隔离、连接/session 隔离、双 lane 和慢 callback 并发。
17. `codex-rs/code-mode-host/src/peer.rs`
    - 查看 disconnect token、queue full、delegate cancellation 和 writer failure。
18. `codex-rs/code-mode-host/src/transport_tests.rs`
    - 查看 bulk registration 的 bounded/release 行为。
19. `codex-rs/codex-mcp/src/connection_manager_tests.rs`
    - 查看 paused time、reconnect backoff、cache refresh 和连接恢复。
20. `codex-rs/codex-mcp/src/rmcp_client.rs`
    - 查看 reconnect singleflight、failure count、retry_not_before 和 capped backoff。
21. `codex-rs/core/src/tasks/mod.rs`
    - 查看取消、terminal event、active turn cleanup 和 rollout flush。
22. `codex-rs/core/tests/suite/abort_tasks.rs`
   - 查看不同阶段中断后的事件、历史和恢复。
23. `codex-rs/otel/src/events/session_telemetry.rs`
    - 查看失败/retry/fallback 相关日志和 metric 字段。
24. `codex-rs/state/src/telemetry.rs`
    - 查看 telemetry delivery 不应影响数据库行为的契约。
25. `AGENTS.md`
    - 查看 integration test、remote executor、snapshot、timeout 和测试 helper 约定。

---

## 58. 一句话总结

韧性不是“系统遇到错误不会 panic”，而是在明确故障模型下仍保持安全边界、资源有界、终态唯一和数据可恢复，并在故障解除后及时回到稳态；有效的故障注入先从用户旅程和稳态写出可证伪假设，再用最窄的 mock、容量、fake clock、socket 或中间持久状态制造 omission、delay、duplication、乱序、损坏、资源耗尽和 crash，断言 safety、boundedness、liveness、terminal state、durability 与 observability；Codex 仓库里的 WebSocket→HTTP fallback、连接重建、队列满载、共享 deadline、迁移 journal、SQLite recovery 和畸形连接隔离测试展示了怎样把真实故障变成确定性证据，而更大范围混沌实验必须额外具备技术 blast-radius 限制、abort、recovery、监控和审批，并把每次发现继续下沉为更便宜的永久回归测试。
