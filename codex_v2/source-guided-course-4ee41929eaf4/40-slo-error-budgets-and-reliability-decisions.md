# 40：SLO、错误预算与可靠性决策——系统到底要可靠到什么程度

第 36～38 章分别讨论性能、剖析和过载保护，第 39 章讨论发布、回滚与事故响应。但还缺一个决定优先级的问题：系统究竟要可靠到什么程度？一次失败算不算违约？指标变差到哪里必须停止发布？团队应继续开发功能，还是先偿还可靠性债务？

SLO 和错误预算就是把这些争论从“我感觉不太稳定”变成可计算、可提前约定的决策规则。它们不会自动让系统可靠，却能帮助团队把用户体验、工程成本和变化速度放进同一张账表。

> 源码基线：`4ee41929eaf4`。本章引用 Codex 当前公开的 OpenTelemetry 指标、Core 终态、App-server thread 状态、错误分类和 Turn timing。示例中的 99%、99.9%、窗口和告警阈值均为教学数字，不是 OpenAI 对 Codex 作出的 SLA 或 SLO 承诺。公开配置文档说明了可导出的 telemetry，但没有为这些指标规定统一产品目标。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分 reliability、availability、durability 和 correctness；
2. 区分 SLI、SLO、SLA 和 error budget；
3. 从用户旅程而不是现成 dashboard 反推指标；
4. 为成功率 SLI 正确定义 numerator 和 denominator；
5. 区分 good event、valid event、bad event 和 excluded event；
6. 计算按事件和按时间定义的错误预算；
7. 理解 99%、99.9%、99.99% 的真实成本差异；
8. 用 percentile 或 threshold-based SLI 表达延迟目标；
9. 处理取消、重试、过载、审批等待和依赖故障；
10. 理解 burn rate 为什么比“当前错误率”更适合告警；
11. 设计多窗口、多 burn-rate 告警；
12. 用错误预算政策决定发布、暂停和可靠性工作；
13. 从 Codex 的 metrics、terminal events 和 error info 组合候选 SLI；
14. 识别平均值、幸存者偏差、低流量和高基数陷阱；
15. 为一个 Agent 系统同时定义可用性、延迟、正确性和安全不变量。

---

## 2. 先说人话：电梯“99% 正常”是什么意思

一栋楼说电梯 99% 正常，至少可能有四种含义：

```text
时间口径：一个月 99% 的时间至少有电梯可用
请求口径：99% 的呼梯请求最终成功到达
延迟口径：99% 的呼梯在 60 秒内有电梯到达
正确性口径：99% 的乘客被送到所选楼层
```

它们不是同一个目标。

如果电梯门能打开，但总把人送错楼层，“在线时间”很好，却不可靠。如果每次都送对，但平均等 30 分钟，也不能说用户体验达标。

所以第一步不是选择 `99.9%`，而是先说清楚：

```text
谁，在做什么时，怎样才算得到一次好的服务？
```

---

## 3. Reliability 不只是 Availability

### 3.1 Availability

服务在需要时能否接受并完成工作，常译为可用性。

### 3.2 Reliability

系统在一段时间内持续提供符合预期行为的能力。它通常比 availability 更宽，包括成功、延迟、正确性和恢复。

### 3.3 Durability

已确认保存的数据是否能长期保留。例如 thread 已显示保存成功，重启后却丢失，这是 durability 问题。

### 3.4 Correctness

输出或状态是否正确。请求返回 HTTP 200，不代表工具执行、文件内容或 thread 状态一定正确。

### 3.5 Safety

系统是否始终守住不可接受的边界，例如未经批准不得执行高风险动作。安全目标通常不是“允许消耗 0.1% 预算”的普通 SLO，而更接近必须保持的不变量。

可靠性设计必须先区分这些维度，不能只用 uptime 代表一切。

---

## 4. SLI、SLO、SLA：三个最容易混淆的词

### 4.1 SLI：实际测量

Service Level Indicator 是“服务水平指标”。

例子：

```text
过去 28 天，92,430 次有效 Turn 中有 92,101 次成功到达终态。
SLI = 92,101 / 92,430 = 99.644%
```

### 4.2 SLO：内部目标

Service Level Objective 是“服务水平目标”。

例子：

```text
任意滚动 28 天内，至少 99.5% 的有效 Turn 成功到达可恢复终态。
```

### 4.3 SLA：对外承诺

Service Level Agreement 是合同或正式协议，可能规定补偿、责任和排除条款。

关系是：

```text
SLI = 实际测到多少
SLO = 希望至少达到多少
SLA = 对外承诺及后果
```

仓库里存在一个 metric 常量，不代表团队已经制定 SLO；团队内部有 SLO，也不自动构成 SLA。

---

## 5. Error Budget 从哪里来

若成功率 SLO 是 99.9%，允许失败比例是：

```text
100% - 99.9% = 0.1%
```

这个 0.1% 就是错误预算比例。

如果 28 天内有 1,000,000 个有效请求：

```text
允许 bad events = 1,000,000 × 0.001 = 1,000
```

错误预算不是“计划让 1,000 个请求失败”，而是承认零故障成本可能无限高，并提前约定可容忍边界。

---

## 6. 按时间计算预算

若按服务时间定义，一个 30 天窗口包含：

```text
30 × 24 × 60 = 43,200 分钟
```

不同目标允许的坏时间近似为：

| SLO | 错误预算 | 30 天允许坏时间 |
|---|---:|---:|
| 99% | 1% | 432 分钟，即 7 小时 12 分 |
| 99.9% | 0.1% | 43.2 分钟 |
| 99.99% | 0.01% | 4.32 分钟 |
| 99.999% | 0.001% | 25.92 秒 |

每增加一个 9，预算大约缩小十倍。99.999% 不是“比 99.9% 稍微好一点”，而是把允许坏时间从四十多分钟压到半分钟以内，架构、发布、检测和恢复成本会显著增加。

---

## 7. 时间口径还是事件口径

### 7.1 Time-based SLI

把时间切成小窗口，判断每个窗口好或坏。

适合：持续提供、能通过健康探测判断的服务。

风险：一分钟内只有一个用户失败和一万个用户失败，都可能只算一分钟坏时间。

### 7.2 Event-based SLI

按真实请求、Turn、tool call 等事件计算。

适合：请求量变化大、希望让每次用户操作等权或按权重计算。

风险：高流量租户可能淹没低流量但重要的用户群；重试还可能改变分母。

### 7.3 选择原则

问：“用户损失更接近持续时间，还是失败操作数量？”

很多系统会同时使用两者：事件成功率代表多数用户体验，时间型灾难 guardrail 防止一段完全不可用被流量低谷掩盖。

---

## 8. 从用户旅程开始，而不是从 Metric 名开始

错误顺序：

```text
仓库里有 codex.api_request → 就把 API 2xx 比例当整个产品 SLO
```

更好的顺序：

1. 用户真正想完成什么？
2. 哪个边界最接近用户观察？
3. 什么终态算成功？
4. 哪些等待是正常交互？
5. 哪些失败由系统负责？
6. 现有信号能否测量？
7. 缺少什么 instrumentation？

对于 Codex，一个候选旅程可能是：

```text
用户提交一条有效输入
→ Turn 被接受
→ 模型和工具循环运行
→ 收到唯一 terminal event
→ 最终状态可读取、可恢复
```

这个旅程明显比“某一次 HTTP 请求返回 2xx”更接近用户结果。

---

## 9. Numerator 和 Denominator

成功率通常写成：

```text
SLI = good events / valid events
```

- numerator：分子，即好事件数；
- denominator：分母，即有效事件总数。

困难通常不在除法，而在分类。

例如 Turn SLI 可能先定义：

```text
valid = 已被系统接受并开始执行的普通用户 Turn
good  = 在 deadline 内以 Completed 结束，且没有影响 Turn 状态的 terminal error
bad   = valid 但未满足 good
```

接着必须回答取消、审批等待、usage limit、bad request、断网和进程退出各算什么。

---

## 10. Good、Bad、Valid、Excluded

建议先画分类表：

| 事件 | 是否进分母 | 是否 good | 原因 |
|---|---:|---:|---|
| 正常完成 Turn | 是 | 是 | 满足用户旅程 |
| Server overloaded | 是 | 否 | 系统未完成已定义服务 |
| Internal server error | 是 | 否 | 系统故障 |
| 用户主动 interrupt | 视目标而定 | 通常不算 bad | 用户改变意图 |
| 等待审批超过用户设定时间 | 视目标而定 | 不应误算系统执行延迟 | 外部交互等待 |
| 明显无效参数 | 可排除或单独计 | 否/不适用 | 请求不符合契约 |
| Usage limit | 单独 SLI 或明确规则 | 取决于服务承诺 | 配额可用性问题 |
| Telemetry 丢失 | 不应直接当 good | unknown | 缺少观测不是成功 |

排除必须少而稳定。若事故后临时把错误类型移出分母，SLO 就失去约束力。

---

## 11. Codex 的错误类型可以帮助分类，但不能替你定政策

`codex-rs/protocol/src/protocol.rs` 的 `CodexErrorInfo` 区分：

- `ContextWindowExceeded`；
- `SessionBudgetExceeded`；
- `UsageLimitExceeded`；
- `ServerOverloaded`；
- `HttpConnectionFailed`；
- `ResponseStreamConnectionFailed`；
- `InternalServerError`；
- `Unauthorized`、`BadRequest`；
- `SandboxError`；
- `ResponseStreamDisconnected`；
- `ResponseTooManyFailedAttempts`；
- 以及其他错误。

`affects_turn_status()` 还明确哪些错误会把历史回放中的 Turn 标为失败。

这些是很好的 failure taxonomy，但 SLO policy 仍要另外决定：

- 未授权是系统故障、用户配置问题还是身份服务可用性？
- usage limit 属于套餐行为还是可用性失败？
- client 网络中断是否由本地产品 SLO 负责？
- sandbox error 是否按 OS 分组？

代码分类提供事实，服务所有者定义责任边界。

---

## 12. Terminal event 比“开始过”更接近完成语义

Core 收尾会发送：

- `TurnComplete`，其中仍可能带 `terminal_error`；
- 或 `TurnAborted`，带明确 abort reason。

因此不能写：

```text
收到 TurnComplete == 成功
```

更准确的候选分类是：

```text
TurnComplete && error == None → completed-good candidate
TurnComplete && error != None → completed-but-failed
TurnAborted(Interrupted) → 用户取消候选
TurnAborted(other reason) → 按原因分类
没有 terminal event → incomplete/unknown，超过截止时间后通常是 bad
```

第 32 章讲过配对事件。这里同样重要：只数 `TurnStarted` 会把卡死和丢失终态隐藏掉。

---

## 13. ThreadStatus 为什么不能直接当 Turn SLI

App-server v2 的 `ThreadStatus` 有：

- `NotLoaded`；
- `Idle`；
- `SystemError`；
- `Active { active_flags }`。

`Active` 还可标注等待审批或等待用户输入。

但 `Idle` 只表示当前没有运行中的 Turn，不保证上一 Turn 成功。源码中 `note_turn_completed` 最终会清理 active state；失败完成也可能回到 Idle。

所以：

```text
ThreadStatus 适合描述当前运行投影
Terminal Turn event 适合判断一次 Turn 的结局
Rollout/Thread store 适合验证结局是否耐久可恢复
```

把状态层级混用会产生虚假的高成功率。

---

## 14. API Request SLI 测到了哪一层

`SessionTelemetry::record_api_request` 当前把以下情况定义为该次 API 请求成功：

```rust
status 在 200..=299 之间，并且 error 为空
```

它记录：

- `codex.api_request` counter；
- `codex.api_request.duration_ms` histogram；
- `status` 和 `success` tag；
- attempt、endpoint、request ID 等事件字段。

这非常适合作为模型传输层 SLI 的原料。但一次 Turn 可能包含多次 sampling request 和 retry：

```text
API 第一次失败 + 第二次成功
→ API request SLI：1 good / 2 valid = 50%
→ 用户 Turn SLI：可能最终成功 = 100%
```

两者都正确，只是在测不同层。底层 SLI 可提前暴露依赖退化，顶层 SLI更接近用户结果。

---

## 15. Retry 怎样改变你看到的可靠性

Retry 可以把瞬时故障遮蔽在用户结果之下，但付出延迟和额外负载。

至少同时观察：

- per-attempt success；
- per-logical-operation success；
- retry count；
- 最终成功前耗时；
- retry exhaustion；
- fallback 使用率。

示例：

```text
Turn success 仍为 99.9%
sampling retry 从 1% 升到 35%
p95 从 4 秒升到 18 秒
```

若只看 Turn success，会错过正在消耗冗余和错误预算的早期信号。第 38 章还说明重试可能形成 retry storm，因此“最终成功”不能是唯一健康指标。

---

## 16. 延迟 SLO 的两种常见写法

### 16.1 Percentile objective

```text
过去 28 天，Turn latency 的 p95 小于 15 秒。
```

直观，但 percentile 不容易与错误预算直接相加，也容易在聚合方式上出错。

### 16.2 Threshold-based SLI

把每个有效事件分类：

```text
good = Turn 在 15 秒内完成
SLI = 15 秒内完成的 Turn / 有效 Turn
SLO = 99%
```

这样 1% 超过 15 秒就是明确预算。

还可用两个阈值：

```text
99% < 15 秒
99.9% < 60 秒
```

第一条保护日常体验，第二条限制极端长尾。

---

## 17. Turn latency 的起点和终点必须明确

候选边界：

```text
起点：系统接受 turn/start
终点：权威 terminal event 已发送
```

但用户体感还可能包含：

- client 到 app-server 的排队；
- 首 token 等待；
- 工具审批等待；
- 用户回答问题的时间；
- 工具执行；
- rollout flush；
- UI 最终渲染。

“Turn latency”若不定义边界，两个 dashboard 可能相差数分钟却都自称正确。

---

## 18. TTFT、TTFM、E2E 各回答什么

Codex 当前定义并记录：

- `codex.turn.ttft.duration_ms`：Time To First Token；
- `codex.turn.ttfm.duration_ms`：Time To First Model output item/message；
- `codex.turn.e2e_duration_ms`：整个 Turn 端到端时间。

`TurnTimingState` 还拆分 sampling、compaction、tool blocking 和 between-sampling overhead。

不同目标对应不同体验：

| 指标 | 用户问题 |
|---|---|
| TTFT | “我多久能看到系统开始响应？” |
| TTFM | “多久出现第一条真正模型输出？” |
| E2E | “整个任务多久结束？” |
| Tool blocking | “时间是否主要花在工具/外部动作？” |

TTFT 很快但最终永不完成，不能算可靠；E2E 很长但期间持续有有用进展，也可能比完全沉默更可接受。

---

## 19. 等待审批算不算延迟

没有唯一答案，但必须显式定义。

### 方案 A：端到端用户体验全部计入

优点：最接近用户从点击到结束的墙钟时间。

缺点：用户离开 30 分钟才批准，会污染系统执行延迟。

### 方案 B：只计系统可控时间

暂停等待审批/输入的计时。

优点：适合衡量系统处理效率。

缺点：不能反映审批交互设计给用户造成的真实等待。

实践中可同时定义：

```text
User-perceived E2E：不暂停
System-active latency：扣除明确 waiting-on-user/approval 区间
```

App-server 的 `ThreadActiveFlag::WaitingOnApproval` 和 `WaitingOnUserInput` 可以帮助识别状态，但配对和时间累计仍需可靠实现。

---

## 20. 可用性与正确性要分开

Agent 系统的请求可能“成功完成”但结果无用：

- 修改了错误文件；
- 测试没有真正覆盖目标；
- 最终回答声称成功但命令失败；
- tool result 被误读；
- 生成的代码无法编译。

传输/运行可靠性可以用在线 telemetry 直接衡量；语义正确性往往需要：

- 可自动验证的任务结果；
- eval dataset；
- human review；
- 用户纠正/重试信号；
- 构建和测试结果；
- 长期回归监控。

不要把 `TurnComplete(error=None)` 宣称为“任务正确率”。它只能说明运行链路没有报告 terminal failure。

---

## 21. Safety objective 为什么通常不使用普通错误预算

设想目标：

```text
99.9% 的高风险命令需要批准
```

这等于允许千分之一绕过批准，通常不可接受。

安全、隐私和数据完整性边界更常表达为：

- hard invariant；
- zero-tolerance guardrail；
- 每次事件都调查；
- fail closed；
- 独立审计和测试。

错误预算适合平衡可接受的服务不完美与变化速度，不应把不可接受的违规合法化。

---

## 22. 多个 SLO 不要合成一个神奇总分

一个 Agent 服务可能同时有：

```text
Availability：99.9% 的有效 Turn 到达可恢复终态
Latency：99% 的有效 Turn 在 30 秒内首次输出
Durability：99.99% 的确认保存 thread 可恢复
Correctness：核心 eval 集合通过率不低于基线
Safety：未经授权的高风险执行为零
```

把它们加权成 `综合健康 97.3` 会隐藏某个维度完全失守。

更好的方法是：每个用户承诺有独立目标；发布 gate 要求所有关键 guardrail 满足，而不是用延迟改善抵消数据损坏。

---

## 23. Burn Rate：预算正在多快燃烧

Burn rate 表示当前坏事件速度相对于“刚好在窗口内耗尽预算”的倍数。

若 SLO 为 99.9%，允许坏比例 0.1%。当前坏比例为 1%：

```text
burn rate = 1% / 0.1% = 10
```

含义：如果一直保持当前速度，预算会以允许速度的 10 倍消耗。

若 burn rate：

- `1`：恰好按预算速度消耗；
- `< 1`：长期保持可能达标；
- `> 1`：长期保持会违约；
- `100`：灾难性快速燃烧。

---

## 24. 预算多久会烧完

近似计算：

```text
耗尽时间 ≈ SLO 窗口 / burn rate
```

28 天窗口：

| Burn rate | 若持续不变，约多久耗尽预算 |
|---:|---:|
| 1 | 28 天 |
| 2 | 14 天 |
| 14 | 2 天 |
| 28 | 1 天 |
| 336 | 2 小时 |

这提供了比“错误率看着挺高”更直接的紧迫度语言。

实际计算还需处理窗口边界、流量和采样，不要把近似表当监控实现。

---

## 25. 为什么只用一个短窗口会告警抖动

如果只看最近 5 分钟：

- 一个短暂尖峰就 page；
- 低流量时一个失败产生巨大比例；
- 告警恢复后又马上触发；
- 长期小幅超标可能没有足够瞬时幅度。

如果只看 24 小时：

- 严重故障被历史好数据稀释；
- 等告警时已消耗大量预算。

因此常用 multi-window、multi-burn-rate：短窗口捕捉灾难，长窗口确认持续性。

---

## 26. 多窗口告警的教学模型

示意而非 Codex 线上阈值：

```text
Page：1 小时窗口 burn > 14 且 5 分钟窗口 burn > 14
Ticket：6 小时窗口 burn > 3 且 30 分钟窗口 burn > 3
Review：3 天窗口 burn > 1
```

长窗口保证问题持续，短窗口保证现在仍在发生。

告警还应包含：

- SLO 名称和当前 burn；
- 预算剩余；
- 主要错误类别；
- 受影响版本/平台；
- dashboard 和 runbook；
- 最近 release/flag change；
- 建议的第一步止血动作。

---

## 27. Low Traffic：一个失败到底算多严重

假设 5 分钟只有 2 个请求，其中 1 个失败：

```text
错误率 = 50%
```

比例巨大，但样本极少。

可组合：

- 最小事件数；
- 更长窗口；
- synthetic probe；
- Bayesian/confidence interval 方法；
- “任何失败都调查但不一定 page”的低流量策略；
- 对关键旅程按时间健康探测。

不能简单忽略低流量，因为低流量路径可能是数据恢复、权限或企业配置等高风险操作。

---

## 28. Missing Telemetry 不是 Good

如果 exporter 故障导致一半失败事件没上报，计算出的 SLI 会虚高。

至少监控：

- telemetry pipeline 自身健康；
- expected event pairing；
- started 与 terminal 数量差；
- exporter drop/queue；
- 各版本事件覆盖；
- unknown 分类比例。

可采用保守规则：

```text
unknown 超过阈值 → SLI 标记为无数据/不可信，阻止自动晋升
```

不要把无数据默认为 100% 成功。

---

## 29. 官方 Codex Telemetry 能确认什么

当前公开配置文档说明 Codex 可选择导出 OpenTelemetry logs/metrics，代表性信号包括：

- API request 的 status、success、duration；
- SSE/WebSocket event 的 kind、success、duration；
- tool call 的 tool、success、duration；
- Turn E2E、TTFT、TTFM；
- feature state；
- DB backfill 和 DB error；
- approval、MCP、Hooks 等事件。

文档还说明 OTel 导出默认需要配置，prompt 内容默认不导出，除非显式 opt in。

这些是构建 SLI 的原料。公开文档没有说：

```text
codex.api_request.success 必须达到 99.9%
```

因此本章的目标值只能作为你为自己的环境设计目标的方法，不能转述为 Codex 官方承诺。

官方入口：[Observability and telemetry](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry)。

---

## 30. Telemetry 的隐私边界也属于可靠性设计

为了排障收集更多数据，可能增加隐私和安全风险。

当前公开配置强调：

- user prompt 内容默认 redacted；
- 只有显式开启 `otel.log_user_prompt` 才包含内容；
- metric 中 tool 字段表示工具名，不是实际 shell command 或 patch。

设计 SLI 时优先使用低敏感、低基数标签：

- success；
- status/error class；
- version；
- platform；
- operation kind；
- feature cohort。

不要为了按用户排障，直接把 prompt、完整路径、token 或用户 ID 作为 metric label。敏感细节应进入受控日志/trace，并遵循保留和访问策略。

---

## 31. Cardinality 为什么会毁掉指标系统

Metric tag 每个不同值都会形成新的 time series。

低基数：

```text
success = true/false
platform = linux/macos/windows
status_class = 2xx/4xx/5xx
```

高基数：

```text
thread_id
request_id
full_error_message
absolute_file_path
user_prompt
```

高基数会增加内存、存储、查询成本，甚至让监控本身过载。稳定 ID 很适合日志/trace 关联，但通常不适合作为 metric tag。

---

## 32. Error Budget Policy：数字怎样变成决定

只计算预算，不规定动作，预算就只是 dashboard 装饰。

教学策略：

| 预算状态 | 发布策略 | 工程优先级 |
|---|---|---|
| 剩余 > 50% | 正常灰度 | 功能与可靠性按计划 |
| 25%～50% | 收紧高风险发布 | 修复主要预算消费者 |
| 0～25% | 仅低风险/可靠性发布 | 优先恢复预算 |
| 已耗尽 | 默认停止非必要变更 | 事故修复、容量和防复发 |

还需定义例外：

- 紧急安全修复；
- 法规/合规要求；
- 修复正在消耗预算的变更；
- 由谁批准；
- 例外多久过期；
- 如何记录风险。

Policy 的目的不是惩罚团队，而是在系统已经证明脆弱时减少变化风险。

---

## 33. 错误预算不是团队之间的武器

反模式：

- 产品用预算逼工程承诺不现实的 99.99%；
- 工程用“预算还有”合理化可避免的故障；
- 平台团队把下游故障全部排除；
- 每次违约都修改 SLO 让图表变绿；
- 以个人绩效绑定单次事故，导致隐藏问题。

正确用途是建立共同决策语言：

```text
我们选择了怎样的用户承诺？
当前变化速度是否超出系统承受能力？
哪类故障消耗最多预算？
下一项可靠性工作能买回多少风险空间？
```

---

## 34. Dependency SLO 与自己的 SLO

假设你的服务依赖：

- 模型 API；
- MCP server；
- 身份服务；
- 本地文件系统；
- 数据库；
- 网络代理。

不能简单写：

```text
我们依赖方 99.9%，所以我们也承诺 99.9%。
```

串行依赖的可用性可能相乘：

```text
0.999 × 0.999 ≈ 0.998001，即约 99.8001%
```

Retry、cache、fallback 和 graceful degradation 可以减少依赖故障对顶层旅程的影响，但也可能增加延迟或功能缺失。

顶层 SLO 应从用户结果定义，再分配 dependency budget，而不是复制依赖 SLA。

---

## 35. Budget Allocation：把顶层目标拆到各阶段

假设顶层允许 0.5% Turn 失败，可以做教学分配：

```text
模型传输：0.15%
工具执行框架：0.10%
本地状态/恢复：0.05%
MCP/外部能力：0.10%
未知和其他：0.10%
```

这不是简单相加就能得到真实概率，因为事件可能相关或重叠。但 allocation 能帮助：

- 找到预算 owner；
- 确定 instrumentation；
- 判断哪层最值得投资；
- 避免所有团队都假设“剩余预算由别人承担”。

最终报表要防止重复计数：一次 Turn 可能同时有 API retry 和 tool failure，但顶层只应算一个 bad Turn。

---

## 36. 按版本、平台和功能切片，但不要改变总账

全局 SLI 下还应查看：

- `app.version`；
- OS/CPU；
- auth mode；
- model/provider；
- transport；
- feature state；
- thread 新建/恢复；
- built-in/MCP tool；
- foreground/background work。

切片用于发现局部伤害。总账仍应覆盖所有有效用户事件，不能因为某个平台表现差就把它从 denominator 删除。

若某个群组确实有不同承诺，应预先定义独立 SLO，而不是事故发生后临时拆分。

---

## 37. Release Gate 怎样使用 Error Budget

结合第 39 章：

发布前：

- 当前预算是否足够；
- 该变更触及哪些 SLI；
- 能否按版本/feature cohort 区分；
- rollback/kill switch 是否可用；
- abort threshold 是否按 burn rate 表达。

发布中：

- 实验组相对 control 的 good-event 比例；
- 短/长窗口 burn；
- latency threshold budget；
- unknown telemetry；
- 安全和数据完整性 zero-tolerance signals。

发布后：

- 预算消耗是否回到基线；
- retry/fallback 是否仍高；
- 长尾和低频旅程是否覆盖；
- 是否关闭临时 override。

错误预算不是只在季度复盘时看，而应进入 go/hold/rollback 决策。

---

## 38. 一个教学案例：成功率正常，但预算正在失控

新版本开启 WebSocket transport：

```text
Turn success：99.92% → 99.91%，看起来变化很小
API attempt failure：0.3% → 4%
fallback_to_http：0.1% → 18%
Turn p95：8 s → 19 s
15 s 内完成比例：99.2% → 96.5%
```

若只有 availability SLO 99.9%，它可能仍勉强达标。但 latency SLO 已快速燃烧，而且 transport retry/fallback 是领先信号。

合理决策可能是：

1. 暂停扩大；
2. 保留 control；
3. 按版本、网络和平台切片；
4. 关闭 WebSocket feature 或使用 HTTP fallback；
5. 验证 latency burn 回落；
6. 修复连接复用/断线原因；
7. 从小范围重新开始。

“最终还能成功”不是忽略 11 秒额外等待的理由。

---

## 39. 另一个案例：指标很好，但数据不可靠

假设：

```text
Turn success = 99.99%
p95 = 6 秒
DB error = 很低
```

但恢复测试发现 0.2% 的已完成 thread 重启后缺少最后一个 terminal event。

这说明运行可用性很好，durability 目标却失败。原因可能是：

- terminal event 发送早于 durable flush；
- flush 失败只写 warning；
- read path fallback 隐藏部分不一致；
- 成功确认边界定义错误。

Core 当前在中断收尾中特别 flush rollout，并注释说明客户端收到 abort 后可能立即重读。这类代码就是 durability 边界的证据。

正确 SLO 不能只数在线请求，还要验证确认过的状态在恢复后仍存在。

---

## 40. 怎样为 Codex-like Agent 设计第一版 SLO

### Step 1：选一个核心旅程

```text
普通用户 Turn 从接受到可恢复终态
```

### Step 2：写明确事件契约

```text
valid：已发出 TurnStarted 的普通 Turn
good：deadline 内出现唯一 terminal event，error=None，且状态可重读
```

### Step 3：列出分类争议

interrupt、approval wait、usage limit、unauthorized、bad request、client disconnect。

### Step 4：定义两个目标

```text
Availability：99.5% good
Latency：99% 在扣除 waiting-on-user 后 60 秒内到达 terminal
```

数字必须用历史分布、用户需求和成本校准，不能照抄。

### Step 5：建立观测质量目标

```text
started-terminal 配对 unknown < 0.1%
```

### Step 6：先 shadow 计算

运行几周但不触发政策，验证分类、分母和 dashboard。

### Step 7：再启用 budget policy

先用于 release review，成熟后才 page 和自动 gate。

---

## 41. SLO 文档模板

```text
名称：Turn completion availability
用户旅程：用户提交普通 Turn 并获得可恢复终态
SLI：good_turns / valid_turns
Good：terminal=complete、error=None、可在 5 分钟内重读
Valid：已接受且发出 TurnStarted；排除预先列出的 synthetic/admin 流量
窗口：滚动 28 天
目标：99.5%（教学示例）
数据源：Core terminal events + app-server notifications + recovery probe
切片：version、platform、transport、resume/new
Unknown：无 terminal 或 telemetry gap，超过 deadline 按 bad；unknown 单独告警
Owner：...
Dashboard：...
Runbook：...
Budget policy：剩余 <25% 时暂停高风险 feature rollout
Review date：...
```

如果一份 SLO 文档没有 good/valid 定义和数据源，它还只是口号。

---

## 42. SLO Review：目标也需要迭代

定期检查：

- 用户旅程是否仍重要；
- 指标是否真的代表用户体验；
- 排除是否越来越多；
- 目标是否长期轻松到没有决策价值；
- 目标是否不现实到永远冻结发布；
- 新平台/feature 是否未覆盖；
- telemetry schema 是否变化；
- 预算政策是否实际执行；
- SLA 与内部 SLO 是否仍有安全余量。

修改目标应记录原因和生效日期，保留历史可比性。不能为了掩盖一次违约而回改过去定义。

---

## 43. 常见反模式

### 43.1 先选 99.9，再找 Metric

目标没有用户和旅程含义。

### 43.2 把 HTTP 2xx 当任务正确

只证明传输请求成功，不证明 Agent 完成正确工作。

### 43.3 分母只放成功上报的数据

丢失、卡死和 telemetry gap 被消失。

### 43.4 所有用户取消都算系统失败

会把意图改变混入可靠性；但也不能无条件排除系统太慢导致的取消。

### 43.5 所有用户取消都排除

系统延迟引发的 rage cancel 会被隐藏。

### 43.6 只看平均延迟

少量极慢体验被平均值稀释。

### 43.7 每个错误都重复计入顶层预算

一次 Turn 的三个 retry 会被算成三次用户失败。

### 43.8 用总分抵消安全问题

性能改善不能抵消未经批准的执行。

### 43.9 有预算就故意制造故障

预算是风险容忍边界，不是必须花完的配额。

### 43.10 预算耗尽仍正常发布

数字没有进入决策，就不是真正的 policy。

---

## 44. 验证与测试

### 44.1 Classification tests

对每种 terminal/error/abort 输入断言 good、bad、excluded 或 unknown。

### 44.2 Pairing tests

证明 Started、Completed、Aborted 唯一配对；缺失终态超过 deadline 后进入 bad。

### 44.3 Retry tests

证明多个 attempt 只产生一个 logical-operation outcome，同时保留 attempt SLI。

### 44.4 Window tests

用固定时间验证滚动窗口边界、迟到事件和重复事件。

### 44.5 Burn-rate tests

给定 SLO、good/bad 数量，断言 burn 和预算剩余。

### 44.6 Low-traffic tests

验证最小样本和长窗口不会让告警在 0/1 个请求间抖动。

### 44.7 Telemetry-gap tests

模拟 exporter 丢失、terminal missing，确保不会自动算 good。

### 44.8 Recovery probes

在成功确认后重启/重读，验证 durability，而不仅是内存状态。

---

## 45. 本章词汇表

| 英文 | 中文直觉 | 本章中的具体含义 |
|---|---|---|
| Reliability | 可靠性 | 持续提供符合用户预期行为的能力 |
| Availability | 可用性 | 需要时能否接受并完成服务 |
| Durability | 持久性 | 已确认保存的数据能否长期保留和恢复 |
| Correctness | 正确性 | 输出和状态是否真正符合任务要求 |
| Safety invariant | 安全不变量 | 不允许用普通错误预算交换的硬边界 |
| SLI | 服务水平指标 | 实际测得的 good/valid 比例或其他服务信号 |
| SLO | 服务水平目标 | 内部约定的指标目标和时间窗口 |
| SLA | 服务水平协议 | 对外正式承诺、责任和补偿条款 |
| Error budget | 错误预算 | 目标允许的不良事件或坏时间额度 |
| Numerator | 分子 | good events 数量 |
| Denominator | 分母 | valid events 总数 |
| Good event | 好事件 | 满足用户旅程目标的有效事件 |
| Bad event | 坏事件 | 有效但未满足目标的事件 |
| Exclusion | 排除项 | 按预先规则不进入该 SLI 的事件 |
| Unknown | 未知 | 因缺失终态或观测而无法可靠分类 |
| Objective window | 目标窗口 | 计算 SLO 的滚动或日历时间范围 |
| Rolling window | 滚动窗口 | 随当前时间连续移动的统计区间 |
| Calendar window | 日历窗口 | 按自然周/月等固定边界统计的区间 |
| Percentile | 百分位 | 某比例样本不超过的数值位置 |
| Threshold-based SLI | 阈值型指标 | 把每个事件按是否低于阈值分成 good/bad |
| Burn rate | 燃烧速率 | 当前预算消耗速度相对允许速度的倍数 |
| Budget remaining | 剩余预算 | 当前窗口尚可容忍的坏事件/时间 |
| Multi-window alert | 多窗口告警 | 同时用长短窗口判断持续性与当前性 |
| User journey | 用户旅程 | 用户真正希望完成的一段端到端行为 |
| Logical operation | 逻辑操作 | 对用户有意义、可能包含多次 retry 的一次工作 |
| Attempt | 尝试 | 逻辑操作内部的一次底层请求 |
| Failure taxonomy | 故障分类体系 | 按语义稳定划分错误类型的规则 |
| Dependency budget | 依赖预算 | 为某个下游/阶段分配的可靠性风险空间 |
| Budget policy | 预算政策 | 根据预算状态决定发布和工作优先级的规则 |
| Error budget freeze | 预算冻结 | 预算耗尽后暂停非必要高风险变更 |
| Synthetic probe | 合成探测 | 自动执行固定旅程以检查低流量能力 |
| Instrumentation | 埋点/观测代码 | 产生 metric、log、trace 和事件的实现 |
| Cardinality | 基数 | 一个标签可能出现的不同值数量 |
| Telemetry gap | 遥测缺口 | 应有的观测事件缺失或无法关联 |
| Rage cancel | 愤怒取消 | 用户因等待过久主动停止，可能反映系统退化 |
| Error budget policy | 错误预算政策 | 将预算余量映射为具体工程动作的制度 |

### SLO 代码单词短语拆解

- `service level`：服务水平；用户实际得到的质量。
- `indicator`：指标；用来指示状态的测量值。
- `objective`：目标；希望达到的内部标准。
- `agreement`：协议；相关方正式同意的承诺。
- `reliable`：可靠；在需要时反复得到预期结果。
- `available`：可用；当前能够提供服务。
- `durable`：耐久；重启和时间过去后仍保留。
- `correct`：正确；结果符合定义和事实。
- `valid`：有效；符合该指标统计范围。
- `good`：好；满足目标阈值。
- `bad`：坏；进入分母但不满足目标。
- `exclude`：排除；按事先规则不参加计算。
- `unknown`：未知；证据不足，不能当成功。
- `numerator`：分子；除号上面的 good 数。
- `denominator`：分母；除号下面的 valid 总数。
- `ratio`：比率；两个数量相除。
- `budget`：预算；可消费但有上限的风险额度。
- `burn`：燃烧；预算随坏事件被消耗。
- `remaining`：剩余；尚未消耗的预算。
- `window`：窗口；统计使用的时间区间。
- `rolling`：滚动；窗口随当前时刻移动。
- `calendar`：日历；按周/月固定边界。
- `percentile`：百分位；按排序定位尾部体验。
- `threshold`：阈值；good/bad 的数值分界。
- `journey`：旅程；用户跨多个组件完成的目标。
- `attempt`：尝试；一次底层执行。
- `logical operation`：逻辑操作；对用户算作一次的整体工作。
- `taxonomy`：分类体系；稳定且互相可解释的类别。
- `slice`：切片；按版本、平台等维度查看子群。
- `cohort`：群组；按共同条件选择的对象集合。
- `probe`：探针；主动检查系统的自动请求。
- `synthetic`：合成；由系统生成而非真实用户产生。
- `instrument`：埋点；在代码中加入可观测信号。
- `cardinality`：基数；不同标签值的数量。
- `aggregate`：聚合；把许多事件汇总成统计值。
- `alert`：告警；条件满足时通知或触发响应。
- `page`：呼叫值班；需要立即人工处理的高紧迫告警。
- `ticket`：工单；需要处理但不一定立即叫醒人的工作。
- `freeze`：冻结；暂时停止非必要变化。

---

## 46. 自测题

1. Reliability、availability、durability 和 correctness 有何不同？
2. 为什么安全边界通常不适合 99.9% 式普通错误预算？
3. SLI、SLO 和 SLA 分别是什么？
4. 99.9% SLO 的错误预算比例是多少？
5. 30 天 99.99% 时间型 SLO 允许多少坏时间？
6. 事件型和时间型 SLI 各有什么偏差？
7. 为什么应从 user journey 而不是 metric 名开始？
8. Numerator 和 denominator 的定义为何比除法更难？
9. Missing telemetry 为什么不能默认为 good？
10. `CodexErrorInfo` 能帮助什么，又不能替团队决定什么？
11. 为什么 `TurnComplete` 仍需检查 terminal error？
12. 为什么 `ThreadStatus::Idle` 不能直接表示上一 Turn 成功？
13. API request SLI 和 Turn SLI 怎样同时正确却数值不同？
14. Retry 为什么会隐藏底层退化？
15. Percentile latency SLO 与 threshold-based SLI 有什么区别？
16. Turn latency 起止边界有哪些选择？
17. TTFT、TTFM 和 E2E 分别代表什么体验？
18. 等待审批时间应怎样处理？
19. `error=None` 为什么不证明任务语义正确？
20. 为什么多个 SLO 不应合成一个可互相抵消的总分？
21. SLO 99.9%、当前错误率 1% 时 burn rate 是多少？
22. 28 天窗口、burn rate 14 大约多久烧完预算？
23. 多窗口告警怎样同时减少噪声和加快灾难检测？
24. 低流量 SLI 有哪些处理方法？
25. 为什么 thread ID 适合 trace，却不适合 metric tag？
26. Error budget policy 应规定哪些动作？
27. 串行依赖两个 99.9% 为什么不自动得到顶层 99.9%？
28. Dependency budget allocation 有什么作用和重复计数风险？
29. 为什么切片发现局部故障后不能从总分母删除该平台？
30. 错误预算怎样进入第 39 章的 rollout gate？
31. Turn success 正常但 fallback 和 latency 变坏时应该怎样判断？
32. 怎样用 recovery probe 衡量 durability？
33. 第一版 SLO 为什么适合先 shadow 计算？
34. 修改 SLO 时为什么要保留历史定义？

---

## 47. 源码检查点

1. `codex-rs/otel/src/metrics/names.rs`
   - 查看 API、SSE、WebSocket、Turn、tool 和 Guardian metric 名称。
2. `codex-rs/otel/src/events/session_telemetry.rs`
   - 查看 API success 分类、status tag、tool result success/duration 和事件字段。
3. `codex-rs/otel/src/metrics/runtime_metrics.rs`
   - 查看 runtime summary 如何聚合 counter 和 histogram。
4. `codex-rs/otel/tests/suite/runtime_summary.rs`
   - 查看工具、API、流事件、WebSocket、TTFT/TTFM 的完整对象断言。
5. `codex-rs/otel/tests/suite/timing.rs`
   - 查看 duration histogram 的 bucket、count、sum 和单位测试。
6. `codex-rs/core/src/turn_timing.rs`
   - 查看 TTFT、TTFM、E2E 及 sampling/compaction/tool-blocking phase。
7. `codex-rs/core/src/tasks/mod.rs`
   - 查看 TurnComplete/TurnAborted、terminal error、idle cause、duration 和 rollout flush。
8. `codex-rs/protocol/src/protocol.rs`
   - 查看 `CodexErrorInfo` 与 `affects_turn_status()`。
9. `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
   - 查看 `ThreadStatus` 和等待审批/用户输入 active flags。
10. `codex-rs/app-server/src/thread_status.rs`
    - 查看运行、完成、中断、system error 如何改变 thread 状态投影。
11. `codex-rs/app-server/tests/suite/v2/thread_status.rs`
    - 查看 active/idle/system-error 及等待状态的公开协议测试。
12. `codex-rs/core/src/responses_retry.rs`
    - 查看 retry exhaustion、warning 和 transport fallback。
13. `codex-rs/core/src/responses_retry_tests.rs`
    - 查看 attempt、fallback 和最终错误状态测试。
14. `codex-rs/state/src/lib.rs`
    - 查看 DB init、fallback、backfill 和 error metric 常量。
15. `codex-rs/state/src/telemetry.rs`
    - 查看低基数 DB kind/phase/status/error 分类及 telemetry fail-independence。
16. `codex-rs/rollout/src/sqlite_metrics.rs`
    - 查看状态数据库 telemetry 怎样适配 MetricsClient。
17. `codex-rs/otel/README.md`
    - 查看 provider、logs/traces/metrics、privacy 和 shutdown 说明。
18. `codex-rs/core/src/config/otel.rs`
    - 查看 exporter 配置解析、metadata 校验与 warnings。
19. `codex-rs/config/src/types.rs`
    - 查看 `OtelConfigToml` 和 runtime `OtelConfig`。

---

## 48. 一句话总结

可靠性工程不是先挑一个漂亮的 99.9%，而是从关键用户旅程定义 valid、good、bad、excluded 和 unknown，用最接近用户结果的 terminal event、延迟阈值与恢复证据构造 SLI，再为明确窗口制定 SLO；错误预算把允许的不完美变成有限风险空间，burn rate 把消耗速度变成告警紧迫度，budget policy 则把剩余预算映射为发布、暂停和可靠性投资；Codex 当前的 API、流事件、工具、Turn timing、错误分类、thread 状态和 DB telemetry 提供了丰富原料，但每个指标只代表特定层级，不能把 metric 的存在误当作官方目标，也不能用传输成功替代任务正确、持久性或安全不变量。
