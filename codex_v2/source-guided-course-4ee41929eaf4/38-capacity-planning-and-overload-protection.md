# 38：容量规划与过载保护——系统忙不过来时怎样安全变慢

第 37 章学习从 p95 退化定位 CPU、锁、队列或内存根因。本章讨论更进一步的问题：即使每个请求都已经足够快，当请求同时涌入的速度超过系统处理能力时，系统应该怎样表现？

一个没有过载保护的系统，常常不是“多处理一些请求”，而是所有请求一起排队、超时、重试，最终让原本还能完成的工作也失败。好的容量设计不是保证永远不忙，而是在忙不过来时仍保持资源有界、错误明确、关键工作可继续、恢复路径可预测。

> 源码基线：`4ee41929eaf4`。本章引用当前 App-server transport、Code Mode host、Skill loader、Responses retry、MCP reconnect、HTTP client pool、durable queue 和 Guardian circuit breaker。教学负载数字不代表线上容量承诺。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分容量、吞吐量、并发数、利用率、队列深度和 in-flight；
2. 用到达率、服务时间和 burst 理解系统为何过载；
3. 区分 bounded queue、Semaphore、rate limit 和 connection pool；
4. 解释 block、queue、reject、drop 四种满载策略；
5. 设计总超时、连接超时、空闲超时和 deadline budget；
6. 区分 retry、backoff、jitter、fallback 和 circuit breaker；
7. 识别 retry storm、thundering herd 和 cascading failure；
8. 理解 load shedding、admission control 和 graceful degradation；
9. 从 App-server 与 Code Mode 的真实容量边界读懂过载语义；
10. 为容量改动设计稳定的并发、超时和恢复测试。

---

## 2. 先说人话：餐厅为什么不能无限接单

假设一家餐厅：

- 4 名厨师；
- 每份菜平均制作 10 分钟；
- 等候区只能放 20 张订单；
- 顾客平均每分钟下 1 单。

稳定情况下，4 名厨师每 10 分钟最多完成约 4 单，即每分钟 0.4 单。到达率 1 单/分钟长期高于处理率 0.4 单/分钟：

```text
每分钟新增积压 ≈ 1.0 - 0.4 = 0.6 单
```

如果订单队列无限：

- 等待越来越久；
- 顾客重复下单；
- 内存/纸张越来越多；
- 新鲜食材被大量预占；
- 关门时还有无法完成的订单。

如果队列有界且满时明确拒绝：

- 一部分顾客立即知道无法接单；
- 已接单的工作仍有机会按时完成；
- 系统不会因积压无限增长而崩溃；
- 恢复后可以重新接单。

过载保护的核心是：

```text
不能完成所有工作时，明确选择完成哪些，拒绝哪些，等待多久，并把资源上限写进系统。
```

---

## 3. 六个容易混淆的容量名词

### 3.1 Throughput

单位时间完成的工作量，例如每秒完成 100 个请求。

### 3.2 Concurrency

同一时刻正在执行或等待完成的工作数。

并发 100 不等于每秒完成 100。若每个请求耗时 10 秒，并发 100 的稳定吞吐上限近似 10 请求/秒。

### 3.3 Capacity

在可接受延迟、错误率和资源范围内，系统能持续承受的工作负载。

容量不是只看“还能运行”，而要包含 SLO：

```text
在并发 N 下，p95 < 2 s、错误率 < 1%、内存保持有界。
```

### 3.4 Utilization

资源已使用比例，例如 worker、CPU、连接或 semaphore permit 的占用率。

### 3.5 Queue depth

已经到达但尚未开始处理的工作数量。

### 3.6 In-flight

已经被系统接纳、尚未到达终态的工作。它可能包含正在执行和等待外部响应的请求。

不同代码对 in-flight 边界可能不同，阅读时要看 permit 从哪里获得、到哪里释放。

---

## 4. Little’s Law 在容量规划中的直觉

稳定系统中常用：

```text
L = λ × W
```

- `L`：系统内平均工作数；
- `λ`：单位时间完成/到达的工作率；
- `W`：每个工作平均停留时间。

例子：

```text
吞吐 λ = 20 请求/秒
平均 latency W = 0.5 秒
平均 in-flight L ≈ 10
```

如果 latency 增到 2 秒，而吞吐仍 20：

```text
L ≈ 40
```

系统需要容纳更多同时存在的状态、buffer、连接和 task。

注意：突发流量、重试、丢弃和未稳定增长期间，公式不能机械套用。它用于建立量级直觉和检查观测是否自洽。

---

## 5. 平均负载正常，为什么仍会过载

因为真实流量有 burst。

```text
平均：每秒 20 个请求
某 100 ms：突然到达 50 个请求
```

即使系统持续吞吐 30/s，也不可能在 100 ms 内立即完成 50 个。如果没有短队列吸收 burst，就会立即拒绝；如果队列太大，尾延迟可能超过用户 deadline。

容量设计至少要描述：

- steady-state rate；
- burst size；
- burst duration；
- 单请求 service-time 分布；
- 并发资源消耗；
- 可接受 queue wait；
- downstream 限制。

只看一天平均 QPS 会隐藏分钟、秒甚至毫秒级峰值。

---

## 6. Queue、Semaphore、Rate Limit、Pool 分别管什么

| 机制 | 限制的对象 | 主要目的 |
|---|---|---|
| Bounded queue | 等待中的消息/任务数量 | 限制积压内存并提供背压 |
| Semaphore | 同时进入某区域的执行者数量 | 控制并发和稀缺资源占用 |
| Rate limiter | 时间窗口内准入的工作速率 | 平滑/约束请求到达率 |
| Connection pool | 可复用连接或 client 数量 | 限制建连成本并复用传输资源 |
| Timeout/deadline | 单个工作最多占用的时间 | 清理失去价值或卡住的工作 |
| Circuit breaker | 连续失败期间是否继续尝试 | 避免反复冲击已知失败依赖 |

它们不能互换。

例如 semaphore 限制 10 个并发，但每个任务 60 秒，稳定吞吐只有约 0.167/s；它不自动限制每秒到达 100 个任务进入等待队列。

---

## 7. Bounded 与 Unbounded Channel

### 7.1 Unbounded

发送方通常立即把消息放进内存队列，不因容量等待。

优点：简单、低负载下发送快。

风险：消费者跟不上时，队列和内存可无限增长。

### 7.2 Bounded

队列有固定容量。满时发送方必须：

- 等待容量；
- 快速失败；
- 丢弃；
- 覆盖旧消息；
- 断开连接。

选择哪种语义取决于消息类型，不能全局统一。

---

## 8. 满载时的四种基本策略

### 8.1 Block / wait

等待队列出现空间。

适合：消息不能丢、上游可以自然背压、deadline 足够长。

风险：上游 task 大量挂起，占用内存；还可能形成 head-of-line blocking。

### 8.2 Reject

立即返回明确 overload/busy 错误。

适合：请求有调用方，可以安全重试或告知用户。

### 8.3 Drop

静默或带统计地丢弃。

适合：允许损失的 telemetry、重复状态刷新等。

不适合：需要一一配对的 RPC request/response。

### 8.4 Disconnect / fail connection

当连接状态或顺序已经无法可靠维持时，断开比继续输出不完整结果更安全。

适合：有状态协议的关键出站队列溢出，继续运行会导致双方状态分叉。

---

## 9. App-server 的真实 bounded channel

当前 `codex-rs/app-server-transport/src/transport/mod.rs` 定义：

```rust
pub const CHANNEL_CAPACITY: usize = 128;
```

源码注释明确说，这是 throughput 与 memory usage 的平衡，128 条消息对交互式 CLI 应当充足。

这说明 128 不是数学真理，而是根据 workload 做出的工程选择。

### 9.1 Incoming request 满载

当 transport event channel 已满，普通 JSON-RPC request 不继续无限等待，而是尝试返回：

```json
{
  "error": {
    "code": -32001,
    "message": "Server overloaded; retry later."
  }
}
```

这样调用方知道请求没有被接纳。

### 9.2 Incoming response 满载

Response 与 request 语义不同。Response 已经是某个 server-initiated request 的配对结果，不能像新请求一样随意拒绝。当前代码在相应分支等待 enqueue，而不是直接丢弃。

### 9.3 Notification

不同 message variant 也可能选择不同策略。容量处理必须基于协议语义，而不是只按字节大小。

---

## 10. 为什么 overload response 也可能发不出去

如果入站队列满，同时出站 writer queue 也满：

```text
请求无法进入处理队列
        ↓
尝试发送 overload error
        ↓
出站队列也没有空间
```

当前代码会 warning 并丢弃 overload response，而不是在这里无限阻塞整个读取路径。

这提醒我们：

- 拒绝路径本身也消耗资源；
- 错误响应需要预留或快速路径；
- 极端过载下客户端可能只观察到断线/超时；
- 监控应统计无法交付的拒绝，而不仅是成功返回的 overload。

---

## 11. App-server 测试怎样固定过载语义

同一文件的测试使用容量为 1 的 channel，稳定构造满载：

```text
1. 先填入第一条 notification
2. 再提交 JSON-RPC request
3. 验证第一条仍在队列
4. 验证第二条收到 -32001 overload error
```

另一个测试验证 incoming response 会等待，而不是被错误丢弃。

这是好的容量测试：不依赖“机器刚好很慢”，而是用小容量和同步边界确定性制造饱和。

---

## 12. Semaphore：许可证就是并发预算

Tokio `Semaphore` 内含固定数量 permits。

```text
Semaphore(3)

Task A 获取 1 -> 剩 2
Task B 获取 1 -> 剩 1
Task C 获取 1 -> 剩 0
Task D 请求   -> 等待或失败
```

Permit 的生命周期就是容量占用范围。用 `OwnedSemaphorePermit` 存在运行对象中，可以通过 RAII 在对象 drop 时自动归还。

设计 Semaphore 时最重要的问题不是数字，而是：

```text
从哪一行获得 permit，到哪个终态释放？
```

---

## 13. Code Mode host 的三层并发边界

当前 `codex-rs/code-mode-host/src/lib.rs` 定义：

```text
MAX_IN_FLIGHT_REQUESTS = 256
MAX_ACTIVE_CELLS = 128
OUTGOING_CHANNEL_CAPACITY = 128
```

`peer.rs` 还限制 pending delegate calls，并给每个 cell message route 使用容量 128 的 channel。

### 13.1 In-flight request permit

`spawn_request` 使用 `try_acquire_owned()`。没有 permit 时快速返回：

```text
code-mode host has too many in-flight requests
```

它没有为更多请求创建无限等待 task。

### 13.2 Active cell permit

Execute 请求还需要 active-cell permit。达到上限时返回：

```text
code-mode host has too many active cells
```

请求并发和长期活动 cell 是不同资源，因此分开限制。

### 13.3 Pending delegate permit

Host 向 client 发起的 delegate call 也有限制。没有 permit 时快速失败，防止 pending response map 无界增长。

### 13.4 Outgoing queue

发送使用 `try_send`。队列满时连接被标记断开并返回 unavailable。对于有序协议，积压到无法发送可能意味着继续运行不再安全。

---

## 14. 等待 Permit 还是立即拒绝

两种 API 表达不同准入策略：

```rust
semaphore.acquire().await        // 等待
semaphore.try_acquire()          // 立即成功或失败
```

等待适合：

- 工作已经在有界上游队列中；
- 等待不会超过 deadline；
- 公平排队有价值。

立即拒绝适合：

- 再创建等待 task 会扩大资源；
- 调用方能够收到 busy/error；
- 工作尚未被承诺接纳；
- 系统需要快速 load shedding。

不能只因为 `await` 写起来简单就选择等待。

---

## 15. Head-of-line Blocking

队首阻塞指最前面的慢工作挡住后面的快工作。

```text
Queue: [60 秒慢任务, 10 ms 快任务, 10 ms 快任务]
Worker: 1
```

后两个请求本可很快完成，却必须等 60 秒。

缓解方式可能包括：

- 不同 workload 分 lane/queue；
- 合理增加 worker；
- 长任务可取消/分块；
- priority，但要防 starvation；
- 将 bulk 与 control traffic 分离。

Code Mode transport 支持 control/bulk lane，正体现了不同流量类型可能需要不同通道；但不要仅凭名字推断所有调度保证，要继续读 lane 和 writer 逻辑。

---

## 16. Skill loader 的共享并发池

当前 `codex-rs/core-skills/src/loader.rs` 定义：

```rust
pub const MAX_CONCURRENT_ROOT_SCANS: usize = 8;
```

多个并发 load 共享 `root_scan_slots: Arc<Semaphore>`。每个 root scan 先 acquire，共享池限制跨加载任务的总扫描并发。

同时每次 load 使用：

```rust
.buffer_unordered(MAX_CONCURRENT_ROOT_SCANS)
```

目的包括：

- 保持扫描 queue 有界；
- 避免某个慢 root 造成完全的队首阻塞；
- 所有 slot 尽量保持工作；
- 完成后按原始 root index 排序，恢复确定的优先级合并语义。

并发优化不能改变最终 precedence，因此性能与确定性必须一起设计。

---

## 17. 并发上限怎样选择

不能只写“CPU 有 8 核，所以并发 8”。要看工作类型。

### CPU-bound

并发明显超过可用核心，通常增加 context switch 和 cache 竞争。

### I/O-bound

可以有更多并发，因为很多 task 在等待；但连接、内存、远端 rate limit 仍有上限。

### Memory-heavy

每个任务占 100 MB，并发 32 可能先耗尽内存，即使 CPU 很空。

### External dependency

本地能并发 100，远端只允许 10，继续放大会增加 429、排队和 retry。

可用估算：

```text
并发上限 <= min(
  CPU 可接受并发,
  内存预算 / 单任务峰值内存,
  连接预算,
  下游并发限制,
  可接受队列延迟对应的 in-flight
)
```

最终数字要用负载测试和生产分布校准。

---

## 18. Durable queue 也必须有上限

内存队列无界会耗尽 RAM；持久化队列无界会耗尽磁盘并让恢复、列表和迁移越来越慢。

当前 thread queue 定义：

```rust
MAX_QUEUE_ITEMS = 100
```

SQLite `enqueue` 在 SQL 中原子检查当前 thread 的 item 数量，小于上限才插入；超过上限时上层返回明确 invalid request：

```text
queue cannot contain more than 100 items
```

### 18.1 为什么在数据库里原子检查

错误模式：

```text
查询 count = 99
两个并发请求都认为可插入
两个都 insert
最终变成 101
```

将条件放进同一 SQL 语句避免 check-then-act race。

---

## 19. Queue 容量不应只按条目数

100 条每条 100 字节，与 100 条每条 10 MB 差别巨大。

完整容量设计可能同时需要：

- max items；
- max bytes；
- per-item size；
- per-thread limit；
- global limit；
- TTL/age；
- tenant/user quota。

当前 `MAX_QUEUE_ITEMS` 是真实条目边界，不应把它误解成已经限制所有磁盘字节风险。阅读某个 limit 时，要问它限制哪个维度，还缺哪些维度。

---

## 20. Timeout 的四种常见边界

### 20.1 Connect timeout

只限制建立连接阶段。

### 20.2 Request/operation timeout

限制整个请求，包括路由、建连、发送和等待响应。

当前 `RouteAwareRequestBuilder::timeout` 的注释明确说明预算从 outbound route resolution 前开始，覆盖 client 选择/构造、建连、发送与等待响应。

### 20.3 Idle timeout

流式连接在多长时间没有新数据后判定异常。

### 20.4 Shutdown timeout

给清理、flush、worker join 的最长时间，避免关闭永远挂住。

Code Mode host 当前对 shutdown 和 writer supervision 使用 5 秒边界，对 bulk pairing 使用 10 秒边界。

---

## 21. Deadline budget：下游不能各自用完整超时

假设用户总 deadline 是 10 秒，路径有三层：

```text
App-server -> Core -> MCP server
```

如果每层都独立使用 10 秒 timeout，最坏可能串行等待约 30 秒。

更合理的是传播剩余预算：

```text
开始：10 s
路由/排队后剩 8.5 s
Core 本地工作后剩 8.0 s
MCP 调用最多使用剩余预算的一部分
预留错误返回和清理时间
```

Timeout 是本地计时器；deadline 是绝对或剩余完成预算。跨层系统更需要 deadline 思维。

---

## 22. Timeout 不等于取消成功

外层：

```rust
tokio::time::timeout(duration, operation).await
```

返回 timeout 只说明调用者不再等待。底层工作是否停止，取决于 Future drop、CancellationToken、子进程、远端请求和资源所有权。

可能出现：

- 调用方已返回超时；
- 子进程仍运行；
- 远端仍执行并产生副作用；
- permit 没有及时释放；
- retry 又发起第二次同类操作。

因此必须定义：

```text
谁取消？谁清理？操作是否幂等？迟到结果怎样处理？
```

---

## 23. Retry 解决瞬时失败，不解决持续过载

适合重试的典型错误：

- 短暂网络断开；
- 某些 5xx；
- 明确可重试 429；
- 临时 transport failure。

通常不应重试：

- 请求参数无效；
- 权限拒绝；
- 确定性业务错误；
- 非幂等动作已可能成功但响应丢失；
- 达到用户 deadline。

Retry 会增加总工作量。系统已过载时，无脑重试会让问题更严重。

---

## 24. 当前 Responses retry 的边界

`codex-rs/core/src/responses_retry.rs` 接收：

- 当前 retry count；
- `max_retries`；
- 错误提供的 retry delay 或本地 backoff；
- 当前 transport；
- request 类型。

流程简化为：

```text
错误可重试且未达上限
  -> retry count + 1
  -> 等待 Retry-After 或 backoff
  -> 告知 UI 正在 reconnect

达到 retry 上限且可切 transport
  -> WebSocket fallback 到 HTTPS
  -> retry count 重置

否则
  -> 返回最终错误
```

这里的上限、用户提示和 fallback 都很重要。只写 `loop { retry }` 会无界占用 Turn。

---

## 25. Exponential Backoff 与 Jitter

指数退避常见形式：

```text
delay = base × 2^(attempt-1)
```

例如 base 500 ms：

```text
0.5s, 1s, 2s, 4s, 8s ...
```

`codex-rs/codex-client/src/retry.rs` 在指数结果上加入约 0.9–1.1 的随机 jitter。

### 为什么要 Jitter

假设 10,000 个客户端同时失败，都严格等待 1 秒：

```text
t=1s：10,000 个请求再次同时到达
```

这叫 thundering herd。Jitter 把重试时间打散，降低同步冲击。

退避还应有最大值和最大尝试次数，否则 delay 或总时长可能失控。

---

## 26. Retry-After 与本地 Backoff

如果服务端明确返回 `Retry-After`，它通常比客户端猜测更了解恢复窗口。

但仍需检查：

- 值是否可解析、合理且有上限；
- 是否超过用户 deadline；
- 所有客户端会不会在同一时刻再次聚集；
- 操作是否幂等；
- 等待期间是否继续占有 permit/lock。

当前 Responses retry 优先使用错误携带的 `retry_delay()`，否则使用本地 backoff。

---

## 27. Retry Storm 与 Cascading Failure

假设下游容量 100 QPS，正常上游发 90 QPS。下游出现 20% 失败，上游每次失败立即重试两次：

```text
原始：90 QPS
额外 retry：最多 36 QPS
总流量：126 QPS
```

下游因此更过载，失败率继续升高，触发更多 retry。

这会向上游扩散：

- 线程/permit 被占满；
- queue 增长；
- timeout 增多；
- 用户重复操作；
- 其他健康依赖也被拖慢。

防护组合：有限 retry、backoff+jitter、deadline、concurrency cap、load shedding、circuit breaker。

---

## 28. Fallback 不是免费重试

从 WebSocket fallback 到 HTTPS 可能提高可用性，但也可能：

- 建立新连接；
- 重发请求；
- 改变缓存/增量状态；
- 增加服务端工作；
- 在非幂等路径产生重复副作用。

Fallback 必须明确状态可否重放、何时重置 retry count，以及旧 transport 的迟到响应怎样处理。

当前 Responses retry 只在重试预算耗尽且 client session 能切换 fallback transport 时执行该路径，并向用户发 warning。

---

## 29. Circuit Breaker 的三态模型

经典熔断器常解释为：

```text
Closed：正常放行
  失败超过阈值
Open：快速拒绝，不再打下游
  冷却时间到
Half-open：允许少量探测
  成功 -> Closed
  失败 -> Open
```

Circuit breaker 与 retry 的区别：

- Retry：当前请求是否再试一次；
- Circuit breaker：一段时间内后续请求是否还应尝试。

本章的经典三态是通用模型，不表示 Codex 每个 breaker 都按这三个 enum 实现。

---

## 30. Guardian 的真实 circuit breaker

当前 Guardian circuit breaker 不是远端服务健康 breaker，而是防止一个 Turn 反复发起被自动审查拒绝的动作。

它按 turn 记录：

- consecutive denials；
- 最近固定窗口内 denial 数；
- 是否已经触发 interrupt。

标准策略在连续拒绝达到阈值或最近窗口拒绝过多时中断 Turn；非拒绝会重置连续计数，但仍进入 recent window。

这说明 circuit breaker 的本质是：

```text
反复出现同一类失败时，不要无限继续消耗资源和制造噪声。
```

它同时是安全和容量保护，而不是为了提升峰值吞吐。

---

## 31. MCP reconnect 的单飞与冷却

当前 Codex Apps MCP startup reconnect 状态包含：

```text
current_client
reconnect_in_flight
consecutive_failures
retry_not_before
```

逻辑会：

- 已有 client 时不重复重连；
- 已有 reconnect in flight 时不再 spawn 第二个；
- 未到 `retry_not_before` 时快速返回；
- 失败后指数增加冷却，最大约 30 秒；
- 成功后清零失败计数和冷却。

### Singleflight

`reconnect_in_flight` 实现了单飞：许多调用者同时发现 client 不可用，也只让一个后台 reconnect 真正执行。

这可以避免连接风暴和重复认证工作。

---

## 32. Connection Pool 的两个不同上限

连接相关的“pool”可能限制：

1. 同一 endpoint 可同时使用多少连接；
2. 进程缓存多少种 route/client 对象。

当前 `RouteAwareClientPool` 的 `MAX_CACHED_ROUTES = 16` 限制第二种：按代理 route 复用 client，route 数达到上限时驱逐一个 cached route。

这不等同于“最多只能发 16 个 HTTP 请求”或“每 route 只有一个 TCP connection”。阅读 pool limit 时必须确认限制对象。

它还区分：

- whole-request timeout；
- connect timeout；
- redirect 每一跳重新做 route decision；
- 敏感 header 在跨 origin redirect 时移除。

容量优化不能绕过安全路由和 header 保护。

---

## 33. Admission Control 与 Load Shedding

### Admission control

在接纳工作前检查资源预算：

```text
有 permit？
队列没满？
请求大小合法？
deadline 仍有价值？
tenant quota 未超？
```

### Load shedding

系统饱和时主动拒绝部分工作，让已接纳和高价值工作继续。

负载丢弃策略可按：

- 新请求优先拒绝；
- 最旧且已无 deadline 价值的请求；
- 低优先级后台工作；
- 可重建 cache refresh；
- 采样 telemetry。

不能按不受信任客户端自报的“最高优先级”直接决定。

---

## 34. Graceful Degradation

优雅降级不是“静默返回错误结果”，而是在明确契约内减少非关键能力。

例子：

- 使用已有 cached tool catalog，同时后台单飞重连；
- 暂停非关键 prewarm；
- 减少 UI 刷新频率；
- 不加载可选 enrichment；
- 对新请求返回明确 busy，而不是拖垮所有 active Turn。

不可降级的内容：

- 权限检查；
- sandbox；
- 数据隔离；
- 持久化承诺；
- wire contract 正确性；
- 用户授权。

---

## 35. Fairness、Priority 与 Starvation

### Fairness

多个请求能以可解释方式分享资源。

### Priority

让控制、取消或用户交互优先于 bulk 后台工作，可能改善可恢复性。

### Starvation

高优先级工作持续到达，低优先级工作永远得不到执行。

常见缓解：

- aging：等待越久优先级逐渐上升；
- 为各类流量保留最低配额；
- weighted fair queue；
- bulk 分批并主动 yield；
- control lane 独立但仍有容量边界。

优先级策略是产品行为，必须有测试，而不只是调度细节。

---

## 36. 贯穿案例：Code Mode 请求风暴

教学场景：一个客户端 bug 在 1 秒内发出 10,000 个 Execute 请求，每个 cell 可能运行数十秒。

如果没有限制：

```text
10,000 task
10,000 pending responses
大量 active V8 cells
巨大 outgoing backlog
shutdown 无法完成
其他 session 饥饿
```

当前多层边界的作用：

```text
request permits：限制同时被处理的 host requests
active-cell permits：限制长期执行 cell 数
delegate permits：限制等待 client response 的反向调用
bounded outgoing channel：限制尚未写出的 frame
shutdown timeout：限制关闭等待
CancellationToken：传播断开/取消
```

没有单一 limit 能覆盖所有生命周期。

---

## 37. 为什么 256/128 不是通用答案

这些是当前 Code Mode host 的实现常量，不应复制到任意服务。

需要测量：

- 单 request 内存；
- 单 active cell heap/CPU；
- 平均和 p99 cell duration；
- delegate call fan-out；
- frame 大小；
- writer throughput；
- shutdown drain 时间；
- 目标机器和 executor 资源。

容量常量应附带 workload 假设和测试。如果实现从本地进程移到小型远端 executor，安全上限可能完全不同。

---

## 38. 过载下的用户体验

坏体验：

```text
点击后无反馈 60 秒 -> generic error
```

好一些：

```text
请求未被接纳 -> 立即显示“系统忙，请稍后重试”
请求已接纳但等待 -> 显示 queued/waiting 状态
正在重连 -> 显示 retry 进度
取消 -> 明确终态并释放资源
```

状态必须真实：如果请求已经进入不可取消外部副作用，不能简单显示“已取消”就假装不存在。

---

## 39. 容量 Metric 应记录什么

至少考虑：

- accepted count；
- rejected/overloaded count；
- dropped count；
- queue depth；
- queue wait；
- active/in-flight；
- permit utilization；
- service time；
- timeout/cancel count；
- retry attempts；
- backoff duration；
- circuit state/trips；
- fallback count；
- shutdown drain duration。

仍要遵守低 cardinality：不要给每个 request ID 建 time series。

只看成功请求 latency 会产生 survivor bias：过载时大量被拒绝/超时的请求没有进入成功分布，图表甚至可能“变快”。

---

## 40. 容量测试的五类场景

### 40.1 Steady state

持续低于容量，验证 latency 和资源稳定。

### 40.2 Ramp

逐步提高到达率，寻找吞吐平台、队列增长点和 knee。

### 40.3 Burst

短时间突发，验证 bounded queue 和恢复速度。

### 40.4 Sustained overload

长期超过容量，验证内存有界、明确拒绝、健康检查和关键路径仍工作。

### 40.5 Recovery

移除负载后，验证 queue drain、circuit 恢复、permit 归还、连接重建和 latency 回落。

只测“压到崩”却不测恢复，不足以证明保护机制正确。

---

## 41. 确定性测试，不依赖机器速度

好测试使用：

- 容量 1 的 channel/semaphore；
- oneshot barrier 控制任务开始/释放；
- paused time 或可控 clock；
- fake transport 返回明确序列；
- 完整事件/错误对象断言；
- timeout 只作为测试失败保险，不作为主要同步方式。

测试流程示例：

```text
1. Task A 获得唯一 permit，并在 barrier 等待
2. Task B 尝试准入
3. 断言 B 立即得到 overload（或保持 pending，按契约）
4. 释放 A
5. 断言 permit 被归还
6. Task C 可以成功
```

这比“spawn 1000 个 task 然后 sleep 50ms”稳定得多。

---

## 42. 容量修改的正确性不变量

1. 每个接纳请求最终有唯一终态；
2. 被拒绝请求不得偷偷执行副作用；
3. Permit 在成功、失败、取消、panic/abort 清理路径都释放；
4. Queue 永远不超过声明上限；
5. Response 不因普通 request overload 被误丢弃；
6. Retry 不超过次数和 deadline；
7. 非幂等操作不会未经协议支持自动重放；
8. Circuit open 时返回明确状态；
9. 恢复后不会永远保持错误 open/closing 状态；
10. 关闭时关键持久化和协议收尾符合既有承诺。

---

## 43. 常见错误方案

### 错误 1：把 queue 调大就算扩容

吞吐不变，只是把拒绝换成长等待和更多内存。

### 错误 2：把 bounded 改成 unbounded

短测少失败，持续负载下资源无界。

### 错误 3：所有错误都重试

确定性错误和持续过载被放大。

### 错误 4：固定 backoff 无 jitter

大量客户端同步重试形成 herd。

### 错误 5：timeout 后立刻重发非幂等操作

旧操作可能已成功，产生重复副作用。

### 错误 6：只限制 task 数，不限制 task 内资源

单 task 可以打开大量 cell、连接或 buffer。

### 错误 7：只看成功 latency

忽略 rejected、timeout 和 dropped 工作。

### 错误 8：熔断后没有恢复策略

一次故障永久关闭能力。

### 错误 9：提高优先级解决所有控制请求

低优先级长期 starvation。

### 错误 10：降级时跳过安全检查

可用性不能以授权和隔离为代价。

---

## 44. 设计一个容量预算表

| 资源 | 单任务成本 | 总预算 | 理论上限 | 选择的安全上限 | 满载行为 |
|---|---:|---:|---:|---:|---|
| Active cell heap | 20 MB | 4 GB | 200 | 128 | reject Execute |
| Pending request state | 16 KB | 8 MB | 512 | 256 | busy error |
| Outgoing frames | 最大 1 MB | 128 MB | 128 | 128 | disconnect/fail |
| Root scans | I/O heavy | 共享 FS | 未知 | 8 | await shared permit |

上表数字除“选择的当前常量”外是教学假设，不能当作 Codex 实测数据。

真正设计时还要留 headroom：系统线程、runtime、cache、模型上下文、日志和突发都需要资源。

---

## 45. 容量 PR 的 Review 清单

### 工作负载

- 到达率、burst、service time 和并发是否明确？
- 限制是 per connection、per thread、per user 还是 global？
- CPU、内存、连接、下游哪个先成为瓶颈？

### 边界

- Queue/permit/pool 限制的具体对象是什么？
- 获取与释放生命周期是否清楚？
- Items 与 bytes 是否都有限制？
- 是否存在嵌套资源导致死锁或顺序反转？

### 满载语义

- 等待、拒绝、丢弃还是断开？
- Request、response、notification 是否按协议区分？
- 错误是否明确且调用方可处理？
- 拒绝路径本身是否有资源？

### Timeout/retry

- 使用 connect、operation、idle 还是 shutdown timeout？
- 跨层是否传播剩余 deadline？
- Retry 是否只针对瞬时且可安全重放的错误？
- 是否有最大次数、backoff、jitter 和总预算？
- Fallback 会不会重复副作用？

### 恢复

- Circuit/冷却怎样恢复？
- Permit 和队列在取消/错误后是否清理？
- Sustained overload 结束后 p95 和内存是否回落？
- Shutdown 是否有明确 drain/abort 策略？

---

## 46. 本章词汇表

完整总表见[课程术语表](glossary.md)。

| 名词 | 常见写法 | 通俗解释 |
|---|---|---|
| Capacity planning | capacity | 根据负载和资源确定安全承载边界 |
| Overload | saturation | 到达工作持续超过可接受处理能力 |
| Admission control | admission | 在工作进入系统前决定接纳或拒绝 |
| Load shedding | shedding | 过载时主动拒绝部分工作保护整体 |
| Bounded queue | bounded channel | 有最大待处理消息数的队列 |
| Semaphore | permit pool | 用有限许可证限制同时进入的执行者 |
| Rate limit | rate limiter | 限制时间窗口内允许的请求速率 |
| Connection pool | pool | 复用并限制连接或 client 对象 |
| Burst | traffic spike | 短时间集中到达的大量工作 |
| Saturation | 饱和 | 关键资源接近或达到全部占用 |
| Headroom | 余量 | 不分配给稳定负载、用于波动和恢复的资源 |
| Backpressure | backpressure | 下游忙时限制上游继续生产 |
| Overload rejection | busy error | 未接纳工作并立即返回明确过载错误 |
| Head-of-line blocking | HOL blocking | 队首慢工作阻塞后续快工作 |
| Starvation | 饥饿 | 某类工作长期得不到资源 |
| Deadline | deadline/budget | 整条操作必须完成的最终时间预算 |
| Timeout | timeout | 某个局部等待或操作允许的最长时间 |
| Retry | retry | 失败后再次尝试同一逻辑操作 |
| Exponential backoff | backoff | 每次失败后按指数增加等待 |
| Jitter | jitter | 给退避加入随机偏移，打散同步重试 |
| Retry storm | 重试风暴 | 大量失败重试进一步压垮依赖 |
| Thundering herd | 惊群 | 大量客户端同一时刻恢复或重试 |
| Fallback | fallback | 主路径失败后切到备用实现或传输 |
| Circuit breaker | breaker | 重复失败后暂时停止继续尝试 |
| Singleflight | single flight | 相同恢复/加载同时只运行一份 |
| Graceful degradation | 优雅降级 | 过载时减少非关键能力但保持正确安全 |
| Cascading failure | 级联故障 | 一个依赖过载向其他组件扩散 |
| Durable queue | 持久队列 | 写入数据库/磁盘、重启后仍存在的等待工作 |
| Survivor bias | 幸存者偏差 | 只观察成功请求而忽略被拒绝/超时的工作 |

### 容量代码单词短语拆解

- `capacity`：容量；可在目标质量下持续承受的负载。
- `overload`：过载；输入工作超过处理能力。
- `saturation`：饱和；某个资源已接近完全占用。
- `utilization`：利用率；资源已使用比例。
- `concurrency`：并发；同一时刻存在的多个执行。
- `in-flight`：处理中；已接纳但尚未结束。
- `queue depth`：队列深度；当前等待工作数。
- `backlog`：积压；未能及时完成的工作。
- `burst`：突发；短时间集中到来的流量。
- `steady state`：稳态；输入和输出长期近似平衡。
- `ramp`：爬坡；逐步提高负载。
- `knee`：拐点；负载增加后 latency 开始陡升的位置。
- `headroom`：余量；为突发、故障和恢复预留的资源。
- `bounded`：有界；有明确最大数量或字节数。
- `unbounded`：无界；理论上可以持续增长。
- `permit`：许可证；Semaphore 中一个并发名额。
- `acquire`：获取；申请 permit 或锁。
- `release`：释放；归还资源名额。
- `admission`：准入；决定新工作是否进入系统。
- `reject`：拒绝；明确不接纳工作。
- `drop`：丢弃；不继续保存或处理消息。
- `load shedding`：卸载负载；主动拒绝低价值工作。
- `rate limit`：速率限制；单位时间准入上限。
- `quota`：配额；某用户/租户可使用的总量。
- `pool`：池；一组受管理、可复用的资源。
- `connection pool`：连接池；复用网络连接/client。
- `head-of-line`：队首；最前面的工作阻挡后续工作。
- `fairness`：公平性；不同调用方合理分享资源。
- `priority`：优先级；决定谁先获得资源。
- `starvation`：饥饿；长期无法获得执行机会。
- `deadline`：截止时间；整个工作剩余完成预算。
- `timeout`：超时；局部操作允许等待的上限。
- `idle timeout`：空闲超时；多久无进展后终止。
- `shutdown timeout`：关闭超时；清理最多等待多久。
- `retry`：重试；失败后重新执行。
- `attempt`：尝试次数；包含第一次或后续重试，需看 API 定义。
- `backoff`：退避；重试前等待。
- `exponential`：指数式；延迟按倍数增长。
- `jitter`：随机抖动；打散多个客户端时间点。
- `retry storm`：重试风暴；重试流量加重故障。
- `thundering herd`：惊群；大量工作同时唤醒冲击资源。
- `fallback`：回退；切到备用路径。
- `circuit breaker`：熔断器；连续失败后停止反复尝试。
- `closed/open/half-open`：经典熔断器的放行、阻断和探测状态。
- `cooldown`：冷却期；失败后暂不重试的时间。
- `singleflight`：单飞；同类并发请求共享一次实际工作。
- `graceful degradation`：优雅降级；有意减少非关键功能。
- `cascading failure`：级联故障；故障跨依赖传播。
- `drain`：排空；停止接新工作并处理已有队列。

---

## 47. 自测题

1. Throughput、concurrency 和 capacity 有什么区别？
2. 为什么平均 QPS 正常仍可能被 burst 压垮？
3. Little’s Law 提供了什么容量直觉？
4. Bounded queue、Semaphore 和 rate limiter 分别限制什么？
5. Queue 满时 block、reject、drop、disconnect 各适合什么语义？
6. App-server 为什么对 request 满载返回 overload，而 response 选择等待？
7. 拒绝路径为什么也可能在极端过载下失败？
8. 容量为 1 的测试怎样稳定复现队列饱和？
9. Semaphore permit 的生命周期为什么比常量数字更重要？
10. Code Mode host 为什么分别限制 request、active cell 和 delegate call？
11. `try_acquire` 与 `acquire().await` 表达什么不同准入策略？
12. Head-of-line blocking 是什么？
13. Skill root scan 为什么并发完成后还要恢复原始顺序？
14. CPU-bound 与 I/O-bound 工作怎样影响并发上限？
15. Durable queue 为什么也必须有上限？
16. 数据库内原子检查如何避免 99 条同时插入变 101 条？
17. 只限制 queue item 数还缺少哪些容量维度？
18. Connect、operation、idle、shutdown timeout 有什么区别？
19. 为什么跨三层各用 10 秒 timeout 可能变成 30 秒？
20. Timeout 返回后底层工作为什么可能仍在运行？
21. 哪些错误适合 retry，哪些不适合？
22. Responses retry 怎样限制重试并切换 fallback transport？
23. Exponential backoff 和 jitter 分别解决什么问题？
24. Retry storm 怎样形成 cascading failure？
25. Fallback 为什么不是免费的重试？
26. Circuit breaker 与 retry 的作用域有什么区别？
27. Guardian breaker 保护的是什么？
28. MCP reconnect 中 `reconnect_in_flight` 怎样实现 singleflight？
29. `MAX_CACHED_ROUTES = 16` 为什么不代表最多 16 个 HTTP 请求？
30. Admission control 与 load shedding 有什么区别？
31. 哪些能力可以 graceful degrade，哪些绝不能？
32. Priority 为什么可能造成 starvation？
33. 只看成功请求 latency 为什么会有 survivor bias？
34. 容量测试为什么必须包含 recovery？
35. Permit 在取消和错误路径释放应怎样测试？

---

## 48. 源码检查点

1. `codex-rs/app-server-transport/src/transport/mod.rs`
   - 查看 `CHANNEL_CAPACITY`、`OVERLOADED_ERROR_CODE`、`enqueue_incoming_message` 及容量为 1 的测试。
2. `codex-rs/code-mode-host/src/lib.rs`
   - 查看 request/cell permits、outgoing capacity、shutdown/pairing timeout 和 `spawn_request`。
3. `codex-rs/code-mode-host/src/peer.rs`
   - 查看 delegate permits、cell message queue、`try_send` 满载时 disconnect。
4. `codex-rs/code-mode-protocol/src/host/mod.rs`
   - 查 `MAX_PENDING_DELEGATE_CALLS` 和 frame/protocol 资源边界。
5. `codex-rs/core-skills/src/loader.rs`
   - 查看 `MAX_CONCURRENT_ROOT_SCANS` 和共享 Semaphore。
6. `codex-rs/core-skills/src/root_loader.rs`
   - 查看 acquire、`buffer_unordered` 和恢复 root precedence。
7. `codex-rs/state/src/lib.rs`
   - 查看 `MAX_QUEUE_ITEMS`。
8. `codex-rs/state/src/runtime/queued_items.rs`
   - 查看 SQL 中的原子 count-limit insert。
9. `codex-rs/thread-store/src/queue_store.rs`
   - 查看超过 queue 上限的公开错误映射。
10. `codex-rs/core/src/responses_retry.rs`
    - 查看 max retries、delay、用户 reconnect 提示和 transport fallback。
11. `codex-rs/core/src/responses_retry_tests.rs`
    - 查看 retry/fallback 状态转换测试。
12. `codex-rs/codex-client/src/retry.rs`
    - 查看 429/5xx/transport 分类、指数 backoff 和 jitter。
13. `codex-rs/codex-mcp/src/rmcp_client.rs`
    - 查看 reconnect singleflight、failure count、`retry_not_before` 和 capped backoff。
14. `codex-rs/core/src/guardian/mod.rs`
    - 查看 consecutive/recent denial window 与 interrupt action。
15. `codex-rs/core/src/guardian/review.rs`
    - 查看 breaker trip 后怎样 warning 并 abort active Turn。
16. `codex-rs/core/src/guardian/tests.rs`
    - 查看连续拒绝、窗口计数、non-denial reset 和单次触发测试。
17. `codex-rs/http-client/src/route_aware_client_pool.rs`
    - 查看 route client cache 上限、whole-request timeout、connect timeout 和 redirect route decision。
18. `codex-rs/http-client/src/route_aware_client_pool_tests.rs`
    - 查看 cache eviction、timeout 和路由复用测试。
19. `codex-rs/code-mode/src/remote_session/connection.rs`
    - 查看 IPC channel capacity、handshake/wait timeout 和请求配对。
20. `codex-rs/tui/src/tui/frame_rate_limiter.rs`
    - 看 UI 高频事件怎样限制绘制频率，这是另一种按时间控制工作速率的例子。

---

## 49. 一句话总结

容量规划不是把并发常量调大，而是用到达率、服务时间、burst、单任务资源和下游限制确定可持续边界；再分别用 bounded queue 限制积压、Semaphore 限制同时执行、rate limit 控制准入速率、pool 复用稀缺资源、deadline 清理失去价值的工作、有限 retry 与 jitter 应对瞬时失败、singleflight 和 circuit breaker 避免重复冲击；满载时按协议选择等待、拒绝、丢弃或断开，并通过稳态、突发、持续过载和恢复测试证明内存有界、终态唯一、错误明确、安全不变量不被降级。
