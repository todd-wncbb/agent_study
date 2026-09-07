# 52：时间、时钟、超时与分布式时间语义——“过了 10 秒”与“现在几点”不是同一个问题

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方配置参考公开了 MCP startup/tool timeout、WebSocket timeout 等用户可配置项，但不会解释 Codex 仓库中所有时钟选择、deadline 传播和持久 lease 实现。本章的实现事实以当前源码与测试为准。
>
> 官方对照资料：[Codex `config.toml` 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference#configtoml)。官方文档与本源码基线可能对应不同发布版本；遇到默认值不一致时，本课程会明确区分“公开文档当前值”和“基线源码常量”。

## 1. 本章解决什么问题

代码里的时间看起来只是几个相似表达式：

```rust
Utc::now()
Instant::now()
tokio::time::timeout(duration, future)
```

但实际必须回答完全不同的问题：

- “这次请求已经运行多久？”
- “这个缓存是哪一天创建的？”
- “从现在开始最多等 10 秒”还是“整条链路在 10 秒内完成”？
- 系统时间被 NTP 调快、调慢或人工回拨后会怎样？
- 一个时间值能否写入 SQLite，重启后继续比较？
- local host 和 remote executor 的时钟不一致时，以谁为准？
- timeout 返回后，底层任务和子进程是否真的停止？
- retry sleep 是否偷走了后续尝试的全部时间预算？
- `expires_at` 到底是秒、毫秒、UTC 字符串还是进程内 `Instant`？

本章的核心认识是：

> “时间”至少包含可展示的墙上时间、只用于测量间隔的单调时间、持续长度和截止点；选错一种，正常路径仍可能工作，但时钟跳变、重试、重启或跨机器时就会出错。

## 2. 先用“机场的两类钟”理解

机场同时需要两类时间：

1. 航班牌写“18:30 起飞”；
2. 安检人员说“这个检查最多花 3 分钟”。

第一类必须让不同人、不同设备、不同日期都能交流，所以需要日历时间和时区。

第二类只关心经过多久。如果墙上时钟因夏令时、人工校时或网络校时跳了一小时，检查并不应瞬间超时或多等一小时。

程序中大致对应：

```text
18:30 起飞        → DateTime<Utc> / SystemTime / Unix timestamp
最多检查 3 分钟   → Duration
检查从何时开始    → Instant
最晚何时结束等待  → monotonic deadline
```

先判断问题属于哪一类，再选 API。

## 3. 四个最基本的时间概念

| 概念 | Rust 常见类型 | 回答的问题 |
|---|---|---|
| wall-clock time | `SystemTime`、`DateTime<Utc>` | 现实世界现在是什么日期时间？ |
| monotonic instant | `std::time::Instant`、`tokio::time::Instant` | 从某个进程内时刻经过了多久？ |
| duration | `std::time::Duration` | 一段时间有多长？ |
| deadline | `Instant + Duration` | 最迟等到哪个单调时刻？ |

它们不能随意替换：

- `Duration::from_secs(10)` 不是 1970 年后的第 10 秒；
- `Instant` 通常不能序列化后交给另一个进程；
- UTC timestamp 能持久化，但系统时钟可能向前或向后跳；
- deadline 是一个绝对的进程内截止点，不只是“再等一次 10 秒”。

## 4. `Duration` 只表示长度

`Duration` 表示非负时间长度：

```rust
let timeout = Duration::from_secs(10);
let ttl = Duration::from_secs(30 * 60);
```

它不包含：

- 起点；
- 日期；
- 时区；
- 当前是否已经过期。

所以“TTL 是 30 分钟”必须再配一个起点：

```text
published_at + TTL
```

或直接保存一个截止时间：

```text
expires_at
```

只存 `Duration`，重启后无法知道那 30 分钟已经过去多少。

## 5. Wall Clock 用于现实时间和持久化

墙上时间能表示：

- `2026-08-09 14:30:00 UTC`；
- Unix epoch 后 1,786,xxx,xxx 秒；
- 文件修改时间；
- token 的服务端到期时间；
- 数据库中的 `created_at`、`retry_at`、`lease_until`。

它的优势是可序列化、可跨进程和跨重启交流。

风险是它不保证单调：

- 用户可修改系统时间；
- NTP 可校正误差；
- 虚拟机恢复快照后时间可能突变；
- 两台机器存在 clock skew；
- 从睡眠唤醒后墙钟与进程预期不同。

因此不应直接用两次 wall clock 相减测量短期函数耗时。

## 6. Monotonic Clock 用于“经过多久”

`Instant` 的目标是单调推进，适合：

- 请求耗时；
- timeout；
- UI 动画；
- 进程内 TTL；
- retry delay；
- cleanup grace period；
- detached session 的短期保留窗口。

常见写法：

```rust
let started = Instant::now();
do_work().await;
let elapsed = started.elapsed();
```

墙上时间即使被调回一小时，`elapsed` 也不应变成负数。

代价是 `Instant` 只在当前运行环境中有意义。不要把它写进 JSON、SQLite 或协议，期待另一个进程能解释。

## 7. `std::time::Instant` 与 `tokio::time::Instant`

两者都表达单调时刻，但 Tokio 版本与异步 runtime 的 timer 系统集成：

```rust
let deadline = tokio::time::Instant::now() + duration;
tokio::time::sleep_until(deadline).await;
```

测试可以暂停并推进 Tokio 时间：

```rust
#[tokio::test(start_paused = true)]
async fn expires_after_limit() {
    tokio::time::advance(Duration::from_secs(10)).await;
}
```

这样不必真的等待 10 秒。

如果逻辑使用 `std::time::Instant`，Tokio 的 paused time 不一定能控制它。测试设计必须与生产代码所选时钟匹配。

## 8. UTC、Local 和时区各自做什么

Codex 持久状态和协议通常偏向 UTC/Unix 时间，因为它们不依赖用户所在时区。

展示给用户时才可能转换为 Local：

```text
存储：2026-08-09T06:30:00Z
上海显示：2026-08-09 14:30
纽约显示：2026-08-09 02:30
```

安全规则是：

- 存储和比较使用明确 UTC；
- UI 展示时再转 local；
- 协议字段说明单位和时区；
- 不解析没有时区的字符串并偷偷当作本地时间；
- 不把“日期”误当作全球同一瞬间。

同一 UTC 时刻在不同时区可能属于不同日期。

## 9. 为什么模型也需要可靠的当前时间

模型不会自动知道运行环境此刻的真实时间。训练知识、会话创建时间和用户机器时间是不同概念。

当前源码可通过 `TimeProvider`：

- 为 environment context 生成本地日期；
- 将 UTC 当前时间作为 developer fragment 注入模型上下文；
- 暴露 `clock.curr_time` 工具；
- 可选暴露 `clock.sleep`。

因此“现在是几号”不是应由模型猜测的静态知识，而是一个运行时数据依赖。

在本源码基线中，`CurrentTimeReminder` feature 标记为 `UnderDevelopment` 且默认关闭；这里是在学习实现边界，不表示所有已发布客户端都默认暴露这些工具。

## 10. `TimeProvider` 是时间的所有权边界

`codex-rs/core/src/current_time.rs` 定义：

```rust
trait TimeProvider {
    fn current_time(&self, thread_id: ThreadId) -> TimeFuture<'_>;
    fn sleep(&self, thread_id: ThreadId, duration: Duration) -> SleepFuture<'_>;
}
```

这层抽象让 Core 不必永远绑定当前主机时钟。

它同时把两件相关但不同的能力交给同一来源：

- 读取这个 thread 所认可的当前时间；
- 按同一时间来源等待指定 duration。

若 current time 来自外部模拟时钟，而 sleep 却使用本机真实时间，测试或虚拟环境会产生矛盾。

## 11. System 与 External 两种时间源

`CurrentTimeSource` 当前支持：

- `System`：`Utc::now()` + `tokio::time::sleep`；
- `External`：由宿主集成提供 `TimeProvider`。

如果配置要求 external，却没有提供 external provider，`resolve_time_provider` 会报错，而不是悄悄退回系统时钟。

这是 fail closed：调用方明确要求一个时间域时，不应在缺失后换成另一个可能语义不同的时钟。

External 适合：

- 测试可控时钟；
- 宿主应用拥有权威时间；
- 模拟环境或回放；
- thread 与特定客户端时间绑定的场景。

## 12. App-server 怎样取得 External Time

`codex-rs/app-server/src/current_time.rs` 的 `AppServerTimeProvider` 会向订阅该 thread 的客户端发送 `CurrentTimeRead` 请求。

主流程是：

```text
Core 请求当前时间
  → 等待 thread 出现订阅客户端
  → 找到唯一 connection
  → 发送 server request
  → 等待 current_time_at 响应
  → 解析为 DateTime<Utc>
```

这里的时间不是 app-server 自己 `Utc::now()`，而是客户端报告的 Unix seconds。

因此真正的时间 authority 是外部客户端。

## 13. 为什么 External Clock 要求唯一客户端

`require_single_current_time_connection` 要求恰好一个订阅连接：

- 0 个：无人回答；
- 1 个：来源明确；
- 2 个或更多：拒绝选择。

原因是外部时钟不可随意互换。两个客户端可能：

- 处于不同时区；
- 使用不同模拟时间；
- 一个暂停，一个正常推进；
- 对“当前业务日期”有不同定义。

若代码随便取第一个连接，同一 thread 的时间可能随连接顺序随机变化。

## 14. 一个 Deadline 覆盖两段等待

External time 请求先等 subscriber，再等 response。源码只创建一次：

```rust
let deadline = Instant::now() + CURRENT_TIME_REQUEST_TIMEOUT;
```

两步都用同一个 `timeout_at(deadline, ...)`。

这意味着总预算是 10 秒，而不是：

```text
最多等 subscriber 10 秒
+ 最多等 response 10 秒
= 最坏 20 秒
```

这是 deadline 传播最直观的例子。一个用户操作经过多个阶段时，应传递剩余预算，而不是每层重新创建完整 timeout。

## 15. Current Time Reminder 怎样控制注入频率

`CurrentTimeReminderState` 保存：

- `last_delivery_time`；
- `last_window_id`；
- 是否刚经过 user/tool output boundary。

提醒到期条件包括：

- 新 context window；
- interval 为 0；
- 从上次投递起已经达到配置秒数。

`delivery_mode` 可允许任意 inference，或只在 user/tool output 后注入；新窗口仍会强制一次新提醒。

这样既避免模型使用过期时间，又避免每个采样步骤都增加重复上下文。

## 16. 时钟回拨对 Interval 判断的影响

提醒间隔使用：

```rust
current_time.signed_duration_since(last_delivery_time)
```

如果 external clock 回拨，差值为负，普通正 interval 不会到期。

源码测试专门覆盖 interval 为 0 时即使时间回拨仍投递，这证明作者认识到“时间不一定只向前”。

设计其他 wall-clock TTL 时也要明确策略：

- future timestamp 视为 stale；
- 暂时继续视为 fresh；
- clamp age 到 0；
- 记录 clock-skew warning；
- 请求权威源重新验证。

没有唯一答案，但不能无意中由一个比较符号决定。

## 17. `clock.sleep` 的语义不是普通 `sleep`

`clock.sleep`：

- 接受 1 ms 到 12 小时；
- 通过当前 `TimeProvider` 等待；
- 返回标签为 `Wall time` 的经过时长；实际由单调 `Instant::elapsed()` 计算；
- 有新输入时可提前结束；
- 用 turn item 记录开始与完成。

System provider 使用 Tokio sleep；External provider 则可按外部时钟等待。

这意味着“睡一小时”可能是：

- 本机真实经过一小时；
- 模拟时钟推进一小时；
- 客户端报告时间达到 wake time；
- 新用户输入后提前唤醒。

工具契约必须写清“等待哪个时钟”和“什么事件会提前结束”。

## 18. External Sleep 使用轮询

App-server external sleep 的流程是：

1. 读取外部 `started_at`；
2. 计算 `wake_at = started_at + duration`；
3. 每隔本机 1 秒请求一次外部当前时间；
4. 当 external time `>= wake_at` 时完成。

这里同时存在两个时钟：

- 本机 Tokio timer 控制轮询频率；
- 外部 UTC clock 决定业务 sleep 是否完成。

如果外部时钟暂停，sleep 会一直轮询；如果外部时间突然跳过 wake time，下次轮询就结束。每次 current-time request 自身还有 10 秒 deadline。

## 19. 新输入怎样中断 Sleep

`SleepHandler` 同时等待：

- `time_provider.sleep(...)`；
- input queue 的 activity change。

它使用 `tokio::select!`。如果已有 pending activity，就根本不开始长等待；若等待中收到新输入，则返回：

```text
Sleep interrupted by new input.
```

被丢弃的 sleep future 按 `TimeProvider` 契约应取消等待。

这属于协作式中断，不是把 thread 强制销毁。输入优先改变用户体验：用户不必等模型设定的 12 小时 sleep 自然结束。

## 20. Durable Sleep 与普通 Future 不一样

Sleep turn item 会进入 extension data。即使当前 turn 结束，只要 thread 仍有 outstanding durable sleep，新的 mailbox 输入也能让调度器启动 turn 并唤醒它。

“durable”在这里主要指业务意图能附着到 thread 状态，而不是声称一个 Tokio timer 能跨进程重启继续运行。

读 durable timer 设计时要区分：

- 是否只保存“正在 sleep”的 item；
- 是否保存绝对 wake timestamp；
- 重启后怎样计算剩余时间；
- 新输入如何取消或消费它；
- 重复恢复是否会启动多个 waiter。

## 21. Turn Timing 为什么同时用两种时钟

`TurnTimingState` 同时保存：

- `Instant`：计算 TTFT、TTFM、turn duration 和各 phase 耗时；
- Unix timestamp：记录可展示、可持久化的开始/完成时间。

这不是重复，而是职责分工：

```text
“turn 开始于几点” → wall clock
“turn 用了多久”   → monotonic clock
```

如果用完成 Unix time 减开始 Unix time计算性能，系统校时可能产生负耗时或异常尖峰。

## 22. `saturating_duration_since` 防什么

源码很多 deadline loop 使用：

```rust
let remaining = deadline.saturating_duration_since(Instant::now());
```

如果现在已经超过 deadline，它返回 `Duration::ZERO`，不会下溢或 panic。

`checked_duration_since` 则返回 `None`，适合把“已经到期”作为循环终止条件。

这两个 API 不会自动决定业务策略；它们只安全表达差值。调用者仍需决定 zero 时：

- 立即 timeout；
- 进行最后一次非阻塞检查；
- 进入 cleanup；
- 返回已有 partial output。

## 23. Timeout 与 Deadline 的本质区别

`timeout` 是相对预算：

```text
从现在起最多等 10 秒
```

`deadline` 是绝对截止点：

```text
最晚等到 monotonic T
```

单阶段操作两者近似等价；多阶段操作差异巨大：

```text
错误：DNS 10s + connect 10s + auth 10s + body 10s
正确：整个 request deadline 10s，各阶段读取 remaining
```

deadline 是传播总预算的载体，timeout 是创建或消费预算的一种接口。

## 24. 每次尝试 Timeout 会放大总耗时

假设单次请求 timeout 为 30 秒，最多重试 3 次，并有 backoff：

```text
attempt 1: 30s
sleep:      1s
attempt 2: 30s
sleep:      2s
attempt 3: 30s
```

总耗时可能超过 93 秒。

如果产品承诺“整项操作 30 秒内结束”，就应在入口生成一个 deadline，每次 attempt 和 sleep 都只使用 remaining。

Guardian review 就建立一个整体 deadline，并把重试 sleep 的目标限制为：

```rust
(Instant::now() + retry_delay).min(deadline)
```

## 25. `tokio::time::timeout` 超时后发生什么

简化理解：

```rust
match timeout(limit, future).await {
    Ok(value) => ...,
    Err(_) => ...,
}
```

超时后，被包裹的 future 会被 drop。只有当底层操作是 cancellation-safe，drop 才等价于停止。

可能继续存在的东西包括：

- 已 spawn 的独立 task；
- OS 子进程；
- 已发出的远端请求；
- 已被服务器接受的副作用；
- 后台线程；
- 持有独立 owner 的 I/O。

所以 timeout 是“调用方不再等”，不自动是“世界恢复到操作前”。

## 26. Timeout、Cancellation 和 Termination 要分开

`ExecExpiration` 明确区分：

- `Timeout(Duration)`；
- `DefaultTimeout`；
- `Cancellation(CancellationToken)`；
- `TimeoutOrCancellation`。

`wait_with_outcome` 返回 `TimedOut` 或 `Cancelled`，让上层能报告真实原因。

组合分支使用 biased `select!`，取消信号优先于同时就绪的 timeout。

但判断原因只是第一步。执行层仍需要终止进程树、关闭 pipe、回收 session，并决定保留多少 partial output。

## 27. Cleanup 也需要独立 Deadline

正常工作 timeout 后，不能无限等待清理：

```text
业务 deadline 到期
  → 发 cancellation / TERM
  → cleanup grace period
  → 必要时强制 kill / abort
  → 有界读取 stdout/stderr
```

`exec.rs` 对 stdout/stderr drain 使用单独 I/O timeout；超时后 abort reader task，避免仍打开的 pipe 让返回永久挂住。

Guardian review timeout 后会先 interrupt，再用 `GUARDIAN_INTERRUPT_DRAIN_TIMEOUT` 等待 `TurnAborted` 或 `TurnComplete`。

业务 timeout 与 cleanup timeout 应分开命名，否则很难知道预算保护的是用户体验还是资源回收。

## 28. MCP 的 Timeout 配置

官方 Codex 配置参考公开：

- `mcp_servers.<id>.startup_timeout_sec`；
- `startup_timeout_ms` 作为毫秒别名；
- `mcp_servers.<id>.tool_timeout_sec`。

基线源码解析规则是：

- sec 和 ms 同时存在时，sec 优先；
- 浮点秒通过 checked conversion 转为 `Duration`；
- 未配置时交给运行层默认值。

当前基线的 `DEFAULT_STARTUP_TIMEOUT` 为 30 秒、`DEFAULT_TOOL_TIMEOUT` 为 300 秒；本章核对时，官方配置页把 startup 默认描述为 10 秒。这可能是仓库与发布文档版本差异，因此不要把任一默认值脱离版本写成永久事实。

## 29. Startup Timeout 与 Tool Timeout 不一样

MCP startup timeout 覆盖：

- 启动 stdio 进程或建立 HTTP client；
- 协议 initialize handshake；
- 初始能力和工具发现所需阶段。

Tool timeout 覆盖一次具体工具调用。

二者需要不同默认值，因为：

- startup 应较快暴露不可用依赖；
- 某些工具本身可能执行几分钟；
- required server 启动失败可能阻止 turn；
- optional server 可被暂时省略，不应拖住全部工具目录。

用一个全局 `timeout` 同时控制二者，会让配置语义模糊。

## 30. Optional MCP Startup 使用共享 Grace Deadline

构建工具目录时，optional MCP server 如果还未启动完成，可只等待一段 grace period。

源码维护共享的 `optional_startup_deadline`，而不是对每个 server 依次等完整 grace：

```text
server A 等到共同截止点
server B 只拿剩余时间
server C 已超时则立即省略
```

有可复用 tool catalog cache 时，等待策略又会根据缓存状态调整。

这能避免 optional server 数量增加后，启动耗时线性累加。

## 31. Idle Timeout 与 Total Timeout

WebSocket 或流式响应常需要 **idle timeout**：只要持续收到新 frame，就可以运行很久；长时间没有任何数据才认为连接卡住。

它不同于 total timeout：

- total timeout：无论是否活跃，到点都结束；
- idle timeout：每次有效活动后重新开始空闲计时；
- connect timeout：只限制握手建立；
- first-byte timeout：只限制首次响应；
- drain timeout：只限制收尾读取。

`responses_websocket.rs` 对读取下一个 stream item 使用 idle timeout；`core/client.rs` 则单独限制 WebSocket connect。

一个“网络超时”字段很难准确覆盖这些阶段。

## 32. Code Mode 的 Yield Timeout 不是执行超时

Code Mode 的 `yield_time_ms` 控制：

> 本次调用最多同步等多久，然后先把 `Yielded` 返回给调用方。

测试明确证明，达到 yield limit 后 cell 仍继续运行；之后可通过 wait 取得结果，或显式 terminate。

因此：

```text
yield     ≠ cancel
yield     ≠ terminate
yield     ≠ cell execution deadline
```

源码还会对较长 yield 加 1 秒 grace，给已在执行的 nested tool 留出完成机会，同时再受 session `max_yield_time_ms` 上限约束。

## 33. Retry Backoff 为什么需要增长

立即重试会在服务故障时制造热循环：

```text
失败 → 立刻重试 → 再失败 → 立刻重试
```

Codex 的通用 `backoff(attempt)` 使用指数增长的 base delay。Responses stream 重连时：

- 服务端给了 retry delay，就优先使用；
- 否则使用本地 backoff；
- 记录 retry count 和 delay；
- 超过重试次数后可尝试 transport fallback。

backoff 的目标不是让一次请求更快，而是降低故障期间的同步冲击并给依赖恢复时间。

## 34. Jitter 为什么重要

如果一万客户端都在同一秒断线，并严格等待 1、2、4、8 秒，它们会在相同时间再次冲击服务。

`backoff` 会把 base delay 乘以约 `0.9..1.1` 的随机 jitter。

```text
无 jitter：4.000s, 4.000s, 4.000s, ...
有 jitter：3.72s, 4.08s, 3.91s, ...
```

jitter 打散 herd behavior。但它会让测试不稳定，因此测试通常应：

- 验证范围而不是精确毫秒；
- 注入 RNG；
- 或测试不含随机数的 deadline/分类逻辑。

## 35. `Retry-After` 有两种格式

HTTP `Retry-After` 可表达：

- delta seconds：`Retry-After: 120`；
- HTTP date：`Retry-After: Sun, ... GMT`。

Remote-control server API 会把二者统一解析成绝对 `retry_at`。

如果读取 response body 又花了 30 秒：

- delta seconds 从收到 headers 时算；
- 后续真正 sleep 只应等剩余 90 秒；
- 不能重新完整等 120 秒。

测试 `http_date_retry_after_preserves_absolute_deadline` 就固定了这一语义。

## 36. Retry Sleep 也必须受总 Deadline 限制

`sleep_with_retry_deadline` 会：

1. 计算 deadline 剩余时间；
2. 若已经为 0，拒绝继续；
3. 用 remaining timeout 包住 retry sleep；
4. sleep 完整结束才允许下一次尝试。

否则可能出现：

```text
总预算剩 1 秒
backoff 决定睡 8 秒
调用方却多等 8 秒才知道超时
```

正确的 retry loop 同时需要 attempt limit、backoff、jitter 和 total deadline，缺一项都可能失控。

## 37. 进程内 TTL 适合用 `Instant`

`McpToolCatalogCache` 的 snapshot 保存：

```rust
published_at: tokio::time::Instant
```

读取时判断：

```rust
snapshot.published_at.elapsed() <= TOOL_CATALOG_CACHE_TTL
```

这是纯进程内 30 分钟 TTL：

- 不需跨重启；
- 不需展示具体日期；
- 不受 wall-clock 回拨影响；
- Tokio paused-time 测试可以快速推进 30 分钟。

`exec-server` detached session 的 30 秒 TTL 也使用 Tokio `Instant`，因为 session registry 本身只在当前进程内存在。

## 38. 持久 TTL 必须保存 Wall Time

模型目录、remote plugin catalog 和 cloud config 会写入磁盘，重启后仍要判断 freshness，所以保存 `DateTime<Utc>`：

- model cache 保存 `fetched_at`；
- plugin catalog 保存 `fetched_at`；
- cloud config 保存 `cached_at` 和 `expires_at`；
- MCP OAuth 保存 epoch milliseconds 的 `expires_at`。

这里无法只用 `Instant`，因为新进程没有旧进程的 monotonic epoch。

代价是必须明确处理系统时钟跳变、未来 timestamp、单位和溢出。

## 39. Future Timestamp 是容易忽略的边界

比较两个源码实现会看到不同策略：

- remote plugin `is_fresh` 要求 `age >= 0 && age <= TTL`，未来 fetched time 被视为 stale；
- model cache `is_fresh` 只检查 `age <= TTL`，负 age 也满足该条件。

这不一定直接证明缺陷，因为产品策略可能不同；但它提示 reviewer 必须主动问：

> 如果缓存 timestamp 在未来，系统希望 fail open、fail closed，还是容忍有限 clock skew？

未来 timestamp 可能来自时钟回拨、坏数据、手工编辑或另一台快时钟机器。

## 40. Token Expiry 为什么需要 Refresh Skew

若 token 恰好在请求发出时仍有效，但传输和服务处理期间到期，请求仍可能失败。

MCP OAuth 的 `token_needs_refresh` 使用：

```text
now + REFRESH_SKEW >= expires_at
```

也就是在真正到期前预留安全窗口。

`compute_expires_at_millis` 把服务端 `expires_in` 加到当前 epoch time，并使用 checked add 与上限保护。

refresh skew 需要平衡：

- 太小：在途请求撞上到期；
- 太大：频繁刷新，缩短 token 使用时间；
- 大于 token lifetime：每次请求都认为需要刷新。

## 41. 跨进程 Lease 为什么使用 UTC Timestamp

Memory jobs 和 backfill 的 owner 可能跨 task、runtime 或进程竞争，并把状态写入 SQLite，所以 lease 使用 Unix seconds：

```text
now = Utc::now().timestamp()
lease_until = now + lease_seconds
```

另一个进程读取同一行，可以判断 lease 是否仍有效。

如果使用 `Instant`，值不能跨进程解释；如果只保存 duration，重启后不知道起点。

但 wall-clock lease 依赖各参与者时间足够接近。严重 clock skew 可能导致提前接管或长期不接管。

## 42. Lease 不能代替 Ownership Token

时钟正确也无法阻止旧 worker 在 lease 过期后迟到提交：

```text
worker A 获得 lease
A 卡住，lease 到期
worker B 接管并完成
A 恢复，尝试提交旧结果
```

因此 Memory job 同时使用 `ownership_token`。完成或失败更新必须匹配当前 token。

Lease 解决“何时允许接管”；ownership token 解决“谁仍有权提交”。

即使 clock skew 让 lease 判断不完美，token fencing 仍能降低旧 owner 覆盖新结果的风险。

## 43. 秒、毫秒和纳秒必须进入类型或名字

常见表示包括：

- Unix seconds：`i64`；
- Unix milliseconds：`i64` / `u64`；
- `Duration::as_millis()`：`u128`；
- RFC 3339 字符串；
- `DateTime<Utc>`。

最危险的代码长这样：

```rust
fn expired(at: i64) -> bool
```

`at` 是秒还是毫秒？

更清楚的名字或类型：

```text
expires_at_ms
created_at: DateTime<Utc>
timeout: Duration
deadline: Instant
```

从 `u128` 毫秒转 `u64` 或 `i64` 时还需处理溢出，不能直接 `as` 后假设永远安全。

## 44. Rate-limit Reset 是服务端时间契约

Codex 会从 event/header 解析 `resets_at` Unix seconds，并在 UI 中转换为本地时间。

这类时间不是客户端自己计算出的精确事实，而是服务端声明：

```text
这个限额窗口预计在某个 UTC timestamp 重置
```

UI 还要记录 snapshot `captured_at`，判断展示是否已经 stale。

要区分：

- rate-limit window 的服务端 reset time；
- 客户端收到 snapshot 的时间；
- UI 当前渲染时间；
- 本地 clock skew。

旧 snapshot 即使带未来 resets_at，也不一定仍反映服务端当前状态。

## 45. 分布式系统里不存在免费的“现在”

当 host、app-server client、remote executor、MCP server 和 OpenAI API 位于不同机器时，每台机器都有自己的 wall clock。

因此应明确 authority：

- API token 到期：通常信任签发方提供的 expiry，同时本地预留 skew；
- Retry-After：信任服务端 header，再按本地接收时刻计算剩余量；
- 数据库 lease：所有竞争者必须采用约定时间域，并接受一定 skew 风险；
- UI 日期：可能使用用户设备 local timezone；
- 性能耗时：在同一进程用 monotonic clock；
- remote command timeout：最好由真正执行命令的 executor 强制。

“调用控制端的 `now()`”不是所有问题的答案。

## 46. 时钟异常时应怎样失败

不同业务需要不同策略：

| 场景 | 较安全的候选策略 |
|---|---|
| 安全策略缓存已过期 | fail closed 或重新拉取 |
| 模型目录缓存时间异常 | 当作 miss，重新验证 |
| token 可能已到期 | 提前刷新或重新认证 |
| UI 相对时间异常 | 显示绝对时间并提示数据陈旧 |
| lease 时钟不可信 | 使用 ownership fencing，限制最长 lease |
| timeout clock API 失败 | 返回错误，不无限等待 |
| 外部 current-time 来源不唯一 | 拒绝随机选择 |
| Unix time 早于 epoch | 显式错误、默认 0 或 fail closed，按业务决定 |

源码中有多种选择：`unwrap_or_default()`、checked add、saturating arithmetic、返回 `None`、直接报 fatal。Reviewer 要判断该选择是否符合数据的重要性。

## 47. 时间测试怎样避免又慢又不稳定

推荐四种技术：

1. **Paused Tokio time**：测试 timer、backoff、TTL，无需真实等待；
2. **Injected TimeProvider**：给出固定、递增、回拨或失败的 UTC 时间；
3. **显式传入 `now`**：纯函数测试 freshness 和 Retry-After；
4. **before/after 区间**：不得不读真实 wall clock 时，断言值位于两次读取之间。

当前源码都有实例：

- Code Mode 和 MCP cache 使用 `start_paused` + `advance`；
- current-time reminder 使用 `TestTimeProvider`；
- `parse_retry_after(headers, now)` 直接注入接收时刻；
- Turn Timing 测试用 before/after 包围 Unix timestamp。

不要用真实 `sleep(30s)` 测 30 秒 timeout。

## 48. 常见错误与理解检查

### 常见错误

- 用 `Utc::now()` 的差值测函数性能；
- 把 `Instant` 序列化进数据库；
- 每个重试 attempt 都获得完整 timeout；
- 认为 `timeout` 返回就代表子进程和远端副作用已停止；
- 把 Code Mode yield 当作 terminate；
- 把 idle timeout、connect timeout 和 total timeout 混成一个字段；
- 重试 sleep 不受总 deadline 限制；
- 忘记 Retry-After 的 HTTP-date 形式；
- 把 epoch seconds 当 milliseconds；
- future cache timestamp 自动被视为 fresh，却没有明确策略；
- 用 lease 代替 ownership fencing；
- 时间测试依赖真实睡眠和恰好毫秒数。

### 理解检查

1. 为什么 TTFT 应使用 `Instant`，而 `created_at` 应使用 UTC？
2. timeout 和 deadline 在三阶段请求中会产生什么差异？
3. `tokio::time::timeout` 为什么不自动杀死 OS 子进程？
4. MCP startup 与 tool timeout 各保护什么？
5. 为什么 optional servers 应共享 grace deadline？
6. Code Mode 返回 `Yielded` 后，cell 是否仍可能运行？
7. 为什么磁盘 TTL 不能只保存 `Instant`？
8. wall-clock lease 为什么还需要 ownership token？
9. external clock 有两个订阅客户端时，为什么不能随便选一个？
10. 怎样在毫秒内测试“30 分钟后缓存过期”？

## 49. 本章术语表

| 英文或代码名 | 中文理解 | 在代码里先问什么 |
|---|---|---|
| wall clock | 墙上时间/现实时间 | 是否需要日期、时区、持久化或跨进程交流？ |
| monotonic clock | 单调时钟 | 是否只测量当前进程内经过多久？ |
| `SystemTime` | 系统墙上时间 | 时钟回拨和 epoch 前时间怎样处理？ |
| `DateTime<Utc>` | UTC 日期时间 | 何时转 local，协议格式是什么？ |
| `Instant` | 单调时刻 | 是否错误地跨进程或持久化？ |
| `tokio::time::Instant` | Tokio 单调时刻 | 测试能否 pause/advance runtime time？ |
| `Duration` | 时间长度 | 起点或 deadline 在哪里？ |
| timeout | 相对超时 | 是单阶段预算还是整项操作预算？ |
| deadline | 绝对截止点 | 下游是否只使用 remaining？ |
| elapsed | 已经过时间 | 使用 monotonic 还是 wall clock 计算？ |
| remaining | 剩余预算 | 到 0 后是 timeout、最后检查还是 cleanup？ |
| idle timeout | 空闲超时 | 什么活动会重置计时？ |
| connect timeout | 连接超时 | 是否只覆盖 DNS/TCP/TLS/WebSocket handshake？ |
| drain timeout | 排空超时 | 收尾读取或等待终态最多持续多久？ |
| grace period | 宽限期 | 到期后是 yield、cancel 还是强制 terminate？ |
| UTC | 协调世界时 | 存储和比较是否使用明确 UTC？ |
| local time | 本地时间 | 只用于展示，还是错误地参与权威比较？ |
| timezone | 时区 | 同一 UTC 时刻显示为何不同？ |
| Unix epoch | Unix 纪元 | 数值单位是秒、毫秒还是纳秒？ |
| clock skew | 时钟偏差 | 不同机器相差多少会改变 expiry/lease？ |
| clock rollback | 时钟回拨 | future timestamp 和负 age 怎样处理？ |
| NTP | 网络时间同步 | 校时会不会让 wall time 非单调？ |
| TTL | 存活时间 | 进程内用 Instant，持久化用什么 timestamp？ |
| `expires_at` | 到期时刻 | 谁的时钟、什么单位、是否含 refresh skew？ |
| refresh skew | 提前刷新窗口 | 为什么不能等到精确到期才刷新？ |
| lease | 有期限的所有权 | 跨进程用哪个时钟，过期后谁可接管？ |
| ownership token | 所有权令牌 | 旧 worker 迟到时如何被拒绝？ |
| Retry-After | 服务端建议重试时间 | 是 delta seconds 还是 HTTP date？ |
| exponential backoff | 指数退避 | delay 怎样随 attempt 增长？ |
| jitter | 随机抖动 | 怎样避免客户端同步重试？ |
| `timeout_at` | 等到 deadline | 是否复用整体截止点而非重置预算？ |
| `sleep_until` | 睡到单调时刻 | 到期、取消和 select 的竞态怎样处理？ |
| cancellation-safe | 可安全取消 | future 被 drop 后是否留下半完成状态？ |
| yield | 暂时交还结果 | 后台工作是否仍在继续？ |
| terminate | 终止执行 | 是否真正停止 task、cell 或进程树？ |
| TTFT | Time To First Token | 从 turn 开始到首个有效 token 的单调耗时 |
| TTFM | Time To First Message | 从 turn 开始到首个 agent message 的单调耗时 |
| `TimeProvider` | 时间提供者接口 | 当前 thread 的时间 authority 是 system 还是 external？ |
| `CurrentTimeReminder` | 当前时间提醒 | 多久以及在哪种 inference 边界注入？ |
| durable sleep | 附着于 thread 的等待意图 | 重启、输入和重复恢复时怎样继续或中断？ |

代码单词可以这样拆：

- `saturating_duration_since`：计算两个单调时刻的差，已过期时安全收敛为 0；
- `checked_duration_since`：无法形成非负差值时返回 `None`；
- `CURRENT_TIME_REQUEST_TIMEOUT`：一次 external current-time 操作的总预算；
- `require_single_current_time_connection`：拒绝在多个不可互换时钟来源中随机选择；
- `take_reminder_due`：判断并消费本次 current-time reminder 投递机会；
- `wait_with_outcome`：等待 timeout/cancellation 并保留完成原因；
- `optional_startup_deadline`：多个 optional MCP startup 共享的 grace 截止点；
- `sleep_with_retry_deadline`：只在整体 deadline 剩余时间内执行 retry sleep；
- `token_needs_refresh`：把 refresh skew 加到当前时间后判断是否临近到期；
- `try_claim_stage1_job`：用 wall-clock lease 和 ownership token 原子竞争工作；
- `complete_profile_and_duration_ms`：用 wall time 记录完成时刻、用 Instant 计算耗时；
- `resolve_yield_timeout`：把请求 yield、grace 和 session 上限合成为观察等待时间。

## 50. 本章小结

读时间代码时，依次检查七层语义：

1. **问题类型**：要现实时间、经过时长，还是绝对 deadline？
2. **时钟来源**：system、Tokio、外部客户端、executor 还是服务端？
3. **作用范围**：只在进程内，还是需要跨进程、重启和机器？
4. **预算传播**：每阶段是否共享一个 end-to-end deadline？
5. **到期动作**：yield、返回 timeout、cancel、interrupt、drain 还是 terminate？
6. **时钟异常**：回拨、快钟、慢钟、未来 timestamp 和单位溢出怎样处理？
7. **测试方法**：能否注入 now、暂停 Tokio time，并断言边界前后行为？

最值得记住的一句话是：

> 需要回答“现在几点”时用可交流的墙上时间；需要回答“过了多久”时用单调时间；需要限制整条链路时传递同一个 deadline；timeout 之后还要明确谁负责真正停止和清理工作。

看到 `now()`、`sleep()`、`timeout()`、`expires_at`、`retry_at` 或 `lease_until` 时，不要只看数字；继续追踪时钟域、单位、authority、持久范围、到期动作和回拨策略。
