# 37：性能剖析实战——从 p95 退化追到真正热点

第 36 章解决的是“怎样证明真的变慢了”。本章继续向下追问：已经知道 p95 延迟退化，下一步究竟应该打开 Trace、看火焰图、查内存，还是检查锁和队列？

很多性能调查失败，不是因为缺少工具，而是拿错工具回答了错误问题。例如 CPU 火焰图没有热点，并不能证明程序没有性能问题；它可能大部分时间都在等待网络、锁或 channel。

> 源码基线：`4ee41929eaf4`。本章引用 Codex 当前的 OpenTelemetry、Turn timing、App-server span、W3C trace context 和 rollout trace 实现。贯穿案例是教学场景，不表示当前代码存在该性能缺陷。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 从 latency、CPU、memory、I/O、lock、queue 六个方向分类性能问题；
2. 区分 metric、log、trace、CPU profile、heap profile 和 rollout trace；
3. 看懂 trace、span、parent/child、attribute、event 和 trace context；
4. 从 Turn profile 判断下一步应查 sampling、tool、compaction 还是本地 overhead；
5. 看懂 CPU 火焰图的横向宽度和纵向调用关系；
6. 理解 sampling profiler 能看到什么、看不到什么；
7. 识别锁争用、队列等待、背压和异步任务调度问题；
8. 用 allocation profile 区分高分配率、峰值内存和内存保留；
9. 添加低风险、低基数且不泄露敏感数据的 instrumentation；
10. 把定位证据转化为可验证的最小优化假设。

---

## 2. 先说人话：剖析不是“看哪个函数最长”

把一次 Turn 想象成去医院完成检查：

```text
挂号 -> 排队 -> 问诊 -> 等检查室 -> 检查 -> 等报告
```

总共花了 90 分钟，不代表医生问诊了 90 分钟。

- **Metric** 告诉你今天 p95 总耗时从 60 分钟涨到 90 分钟；
- **Trace** 把一次就诊画成时间线，显示 50 分钟在等检查室；
- **CPU profile** 告诉你工作人员实际忙碌时主要在哪个步骤；
- **Lock/queue 指标** 告诉你检查室为什么排队；
- **Heap profile** 告诉你是否一直堆积没有清理的报告；
- **Log** 提供某些事件发生时的文字证据。

“最长的函数”可能只是在 `await`；它自己没有消耗 CPU，却拥有很长 wall-clock duration。性能剖析首先要分清：

```text
慢在做工作，还是慢在等工作？
```

---

## 3. 性能问题的六大类

### 3.1 CPU-bound

程序主要时间用于计算，例如：

- JSON 编解码；
- 图片 resize/encode；
- 大量字符串复制；
- 压缩或 hash；
- 低效算法重复扫描。

典型证据：CPU 利用率高，sampling profile 中某些调用栈很宽。

### 3.2 I/O-bound

程序在等待磁盘、网络、子进程或远端服务。

典型证据：wall-clock latency 高，但 CPU profile 不宽；Trace 中远端或 I/O span 占大部分时间。

### 3.3 Lock contention

多个 task/thread 竞争同一 mutex/RwLock。

典型证据：并发升高后延迟陡增；单请求正常；锁内 critical section 或等待时间明显。

### 3.4 Queueing / backpressure

生产速度超过消费速度，工作在 channel、线程池、连接池或外部服务前排队。

典型证据：服务时间没有显著变慢，但 queue wait 增长；吞吐到达平台后，尾延迟快速上升。

### 3.5 Allocation / memory pressure

频繁 allocation、复制大对象、内存碎片、page fault 或 cache 保留过多数据。

典型证据：allocation rate、RSS 或 page fault 增长；CPU 时间可能消耗在 allocator 或 clone。

### 3.6 Retry / duplicated work

一次用户动作触发了更多 sampling、重试、重复解析或重复 I/O。

典型证据：单次工作速度没变，但 request count、retry count 或调用次数增加。

这六类可以同时存在。例如锁竞争导致 queue 变长，随后 timeout 又触发 retry，最终 CPU 和网络成本都上升。

---

## 4. 先用 Metric 找“哪一类请求变了”

Metric 适合回答群体问题：

```text
哪个版本开始变慢？
哪类请求变慢？
p50 还是 p95/p99 变慢？
失败率、重试数、请求数是否一起变化？
```

当前 `codex-rs/otel/src/metrics/names.rs` 定义了多类计数和耗时指标，例如：

```text
codex.turn.e2e_duration_ms
codex.turn.ttft.duration_ms
codex.turn.ttfm.duration_ms
codex.api_request.duration_ms
codex.tool.call.duration_ms
codex.websocket.request.duration_ms
codex.responses_api_inference_time.duration_ms
```

不要只看一个总延迟。假设：

```text
Turn p95：+800 ms
TTFT p95：基本不变
Tool duration：基本不变
Sampling request count：从 1.1 增到 2.0
```

这更像“做了更多轮工作”，而不是“每轮 sampling 变慢”。

---

## 5. Cardinality：Metric 标签不能随便加

Metric tag/label 常用于分组：

```text
status=success/error
transport=websocket/stdio
tool=shell/read_file
```

`cardinality` 是某个标签可能出现的不同值数量。

低基数：

```text
status ∈ {success, error, cancelled}
```

高基数：

```text
thread_id = 每条 thread 都不同
full_path = 每个用户路径都可能不同
error_text = 内容几乎无限
```

把 `thread_id` 或完整 URL 直接做 metric tag，可能产生海量 time series，增加内存、传输和查询成本。调查单次请求身份通常用 Trace attribute/log，而不是无界 metric tag。

当前 MCP metric 代码会对部分 tag value 做 sanitize，并对 error code 长度设上限。这体现的原则是：观测数据本身也必须有边界。

---

## 6. Trace、Span 和 Event 是什么

### 6.1 Trace

Trace 表示一次操作跨多个组件的完整因果链。例如：

```text
TUI 请求
  -> app-server thread/start
    -> core 创建 Turn
      -> Responses API
      -> tool call
        -> exec-server
```

### 6.2 Span

Span 是 Trace 中一个有开始、结束和属性的时间区间。

```text
Span {
  name: "app_server.request",
  start: 10:00:00.000,
  end:   10:00:00.042,
  attributes: {
    rpc.method: "thread/start",
    rpc.transport: "websocket"
  }
}
```

### 6.3 Parent/child

一个 span 可以是另一个 span 的 child：

```text
app_server.request                  42 ms
├── parse_request                    2 ms
├── thread_start                    35 ms
│   ├── load_config                  5 ms
│   └── create_session              20 ms
└── serialize_response               1 ms
```

父 span 的 duration 不等于所有 child duration 简单相加，因为 child 可能重叠，也可能存在没有 child 覆盖的 self time。

### 6.4 Event

Event 是 span 时间线中的一个瞬时记录，例如：

```text
span: mcp.call
  event at +0 ms: request_sent
  event at +820 ms: timeout_warning
  event at +1000 ms: request_failed
```

Event 没有自己的持续区间；Span 才有 duration。

---

## 7. Trace waterfall 怎样读

一个简化 waterfall：

```text
时间(ms)  0       200       400       600       800      1000
Turn      [================================================]
Sampling  [==========]
Tool                 [=============================]
Sampling                                            [======]
```

先看四件事：

1. 哪个 span 最宽？
2. 哪些 span 彼此重叠？
3. 父 span 中是否有大块没有 child 覆盖的空白？
4. 同类快请求和慢请求的结构有何差异？

大块空白可能表示：

- 缺少 instrumentation；
- task 在排队；
- 等锁；
- 等 channel message；
- 调度延迟；
- 父层做了未拆分的本地工作。

它不是自动等于“CPU self time”。需要用 profile 或新增细分 span 验证。

---

## 8. Codex 的 App-server request span

当前 `codex-rs/app-server/src/app_server_tracing.rs` 为请求创建名为 `app_server.request` 的 span，并记录：

```text
otel.kind = server
otel.name = RPC method
rpc.system = jsonrpc
rpc.method
rpc.transport
rpc.request_id
app_server.connection_id
app_server.api_version = v2
app_server.client_name
app_server.client_version
turn.id
```

它还让 stdio、Unix socket、WebSocket 和 in-process 请求使用可比较的 span shape。

这很重要：如果不同 transport 的字段和名称完全不同，就难以在同一查询中比较它们。

### 8.1 为什么有些字段先是 Empty

创建 span 时可能还不知道 `turn.id` 或 client info。`tracing` 可以先声明字段，再在后续获得值时 `span.record(...)`。

### 8.2 Attribute 不是 payload dump

Span attribute 应用于筛选和解释，不应默认塞入完整 prompt、tool output 或终端输出。完整 payload 既有隐私风险，也会增加 trace 体积和序列化成本。

---

## 9. Trace context 为什么需要传播

如果 app-server 调用 exec-server，但没有把父 Trace 身份传过去：

```text
Trace A：app-server.request
Trace B：exec-server.process
```

观测平台会看到两个无关操作，无法自动拼回一条路径。

W3C Trace Context 使用常见字段：

- `traceparent`：携带 trace ID、parent span ID 和 flags；
- `tracestate`：携带厂商或系统附加状态。

当前 `codex-rs/otel/src/trace_context.rs` 可以：

- 从当前 span 提取 W3C context；
- 注入 HTTP headers；
- 从传入 context 设置 parent；
- 从环境变量读取父 context；
- 验证和合并 `tracestate`。

`codex-rs/exec-server/src/trace_context.rs` 会把当前 context 放入出站 header。

传播的是关联身份，不意味着把所有 span 内容或业务 payload 一起发送。

---

## 10. Trace context 常见错误

### 10.1 每个组件都创建新 root

跨进程链路被切断。

### 10.2 复用错误 parent

两个无关用户请求被连接到同一 Trace，时间线和身份都错误。

### 10.3 异步 task 丢失 span

Spawn 后没有保留正确 context，child 工作成为 root 或挂到错误请求。

### 10.4 接受无效 header

Malformed `traceparent` 可能污染传播。当前代码验证 context，无效时忽略并 warning。

### 10.5 把 trace ID 当授权信息

Trace context 只用于关联和采样，不应被当作用户身份、权限或可信凭据。

---

## 11. `#[tracing::instrument]` 与手工 span

仓库 `AGENTS.md` 对 async tracing 有明确约定：优先在函数或方法定义上使用：

```rust
#[tracing::instrument(...)]
async fn do_work(...) { ... }
```

而不是在每个调用点给 Future 添加：

```rust
do_work(...).instrument(span).await
```

并且添加前先检查 callee 或它立即委托的方法是否已经 instrumented。

原因包括：

- Span 生命周期与函数实现绑定，更不容易漏掉调用点；
- 所有调用者得到一致的 span shape；
- 避免同一工作被重复包两层等价 span；
- async poll 跨 await 时由 instrument 正确关联。

但 app-server request 是入口级动态边界，需要根据 RPC envelope、transport 和 connection state 构造 span，因此手工 `request_span(...)` 再 instrument request future 是合理的现有模式。

---

## 12. Instrumentation 不是越多越好

每个 span/event 都有成本：

- 创建和销毁对象；
- 读取时间；
- 格式化字段；
- clone 字符串；
- export/serialize；
- 网络和存储；
- 查询时的索引成本。

高频循环里每个 item 一个 span，可能让观测本身成为性能问题。

添加 instrumentation 前问：

1. 它要区分哪两个假设？
2. 边界是否稳定且有所有权？
3. 字段是否低基数、有限长、无敏感数据？
4. 是否已有等价 span/metric？
5. 是否应该采样而不是全量记录？
6. 关闭 exporter 时 hot path 是否足够便宜？

---

## 13. 贯穿案例：p95 Turn 变慢，但 p50 正常

观察到：

```text
版本 A：p50 2.1 s，p95 4.0 s
版本 B：p50 2.2 s，p95 7.8 s
```

这说明：

- 典型请求基本相同；
- 慢请求尾部显著恶化；
- 很可能是条件性路径、并发、重试或特定输入，而不是所有请求都多做固定计算。

进一步按 Turn profile 分组：

```text
sampling_ms：相近
tool_blocking_ms：相近
compaction_ms：相近
between_sampling_overhead_ms：慢请求显著增加
sampling_request_count：相同
```

调查范围因此收窄到“两次 sampling 之间、但不属于 tool blocking/compaction 的本地工作或等待”。

---

## 14. 先比较快 Trace 和慢 Trace

快请求：

```text
Turn 2.0 s
├── Sampling 1.2 s
├── local transition 40 ms
└── Sampling 0.7 s
```

慢请求：

```text
Turn 7.6 s
├── Sampling 1.2 s
├── local transition 5.6 s
└── Sampling 0.7 s
```

慢 Trace 的 local transition 没有 child spans。此时有三个主要假设：

1. CPU 做了 5.6 秒未 instrument 的工作；
2. 在等锁/channel/任务调度；
3. 在做没有 span 的 I/O。

下一步用 CPU profile 区分假设 1 与“主要在等待”。

---

## 15. CPU Sampling Profiler 怎样工作

Sampling profiler 每隔一小段时间中断或观察进程，记录当时线程调用栈。

例如采集 1,000 次：

```text
420 次在 image::resize
250 次在 png::encode
180 次在 serde_json::to_writer
150 次在其他调用栈
```

近似推断 CPU 时间占比：

```text
image::resize ≈ 42%
```

它不是给每个函数入口/出口精确打表，而是通过随机/周期样本估计 on-CPU 分布。

优点：

- 通常开销比全量函数 instrumentation 小；
- 不必提前知道热点；
- 适合发现意外调用栈。

局限：

- 极短、低频函数可能采不到；
- inlining 会改变栈显示；
- 符号缺失会让栈难读；
- 主要等待的任务不会形成宽 CPU 栈；
- 采样结果仍受工作负载是否代表真实问题影响。

---

## 16. 火焰图怎样读

火焰图通常由采样调用栈聚合而来。

```text
                 ┌──────── encode_png ────────┐
        ┌──────── prepare_image ──────────────┐
┌────────────── handle_attachment ───────────────┐
```

### 16.1 横向宽度

宽度表示该函数及其子调用栈被采样到的比例。越宽，表示占用的采样 CPU 时间越多。

宽度通常不是时间轴。左边不代表先发生，右边不代表后发生。

### 16.2 纵向高度

高度表示调用栈深度。下面是 caller，上面是 callee。

高不等于慢。一个很深但很窄的栈可能只占很少时间。

### 16.3 Self time 与 total/inclusive time

- Inclusive：函数本身加所有子调用；
- Self/exclusive：函数自身，不含子调用。

父框很宽但 self 很窄，说明主要成本在子函数。

### 16.4 颜色

颜色未必表示热度，取决于生成工具。不能只凭“红色”判断问题。

---

## 17. CPU 火焰图没有热点意味着什么

贯穿案例中，慢请求期间 CPU 使用率较低，profile 没有额外 5 秒的宽栈。

这能削弱假设 1：不是大量未观测 CPU 计算。

但不能直接证明是锁。剩余候选包括：

- mutex/RwLock 等待；
- channel recv/send 等待；
- semaphore/连接池等待；
- 文件或网络 I/O；
- 子进程等待；
- task 没有及时被 scheduler poll；
- sleep/backoff。

此时 Trace 与代码所有权结合，定位 local transition 涉及哪些 await 点。

---

## 18. Async Rust 中“函数很慢”的误解

```rust
async fn load_state(store: &Store) -> State {
    let guard = store.lock().await;
    read_state(&guard)
}
```

Span 显示 `load_state` 5 秒，并不代表 `read_state` 计算了 5 秒。可能是：

```text
等待 lock 4.99 s
真正读取 10 ms
```

要分开观测：

```text
lock_wait
critical_section
```

教学示意：

```rust
let wait_started = Instant::now();
let guard = store.lock().await;
metrics.record_duration("state.lock_wait", wait_started.elapsed(), &[]);

let hold_started = Instant::now();
let result = read_state(&guard);
metrics.record_duration("state.lock_hold", hold_started.elapsed(), &[]);
```

真实实现不一定应永久加入这两个 metric；先评估频率、基数和观测成本。

---

## 19. 锁争用的四个检查点

### 19.1 谁持有锁

搜索所有 acquire 点和锁保护的数据所有权。

### 19.2 锁内做了什么

危险例子：

```rust
let mut state = state.lock().await;
let response = client.send(request).await?;
state.apply(response);
```

在锁内等待网络会让其他 task 一起排队。

### 19.3 是否能缩小 critical section

```rust
let request = {
    let state = state.lock().await;
    state.build_request()
};

let response = client.send(request).await?;

{
    let mut state = state.lock().await;
    state.apply(response);
}
```

但这种变换可能引入状态在 I/O 期间变化的问题，必须检查版本、identity 和不变量。不能为了缩锁盲目拆开原子操作。

### 19.4 并发增长曲线

如果并发 1、2、4 正常，8、16 时 p95 激增，很像共享瓶颈或容量上限。单请求 profile 可能完全看不见。

---

## 20. Queue wait 与 service time

一次操作总延迟可以拆为：

```text
total latency = queue wait + service time
```

假设 worker 实际处理始终 100 ms：

```text
低负载：queue 5 ms + service 100 ms = 105 ms
高负载：queue 900 ms + service 100 ms = 1000 ms
```

只在 worker 函数内部计时，会看到稳定 100 ms，误以为系统没有退化。

正确边界需要：

- enqueue timestamp；
- dequeue/start timestamp；
- completion timestamp；
- queue depth 或 in-flight 数；
- drop/reject/backpressure 次数。

---

## 21. Little’s Law 的直觉

在相对稳定系统中：

```text
系统内平均工作数 L ≈ 到达率 λ × 平均停留时间 W
```

如果每秒进入 20 个任务，每个任务平均停留 0.5 秒，那么系统内平均约有 10 个任务。

它帮助检查观测是否自洽：如果吞吐基本不变，但 in-flight 和 latency 同时上升，系统可能正在积压。

这不是在每个短窗口都精确成立的魔法公式；突发流量、丢弃、重试和非稳定状态都会影响解释。

---

## 22. Backpressure 不是性能 bug 的同义词

Bounded channel 满时阻塞或拒绝，是为了防止无限内存增长。它可能增加 latency，却保护系统。

错误“优化”：

```text
bounded channel -> unbounded channel
```

短测吞吐可能变好，但持续负载下 queue 无限增长，最终内存耗尽。

更合理的方向：

- 减少 producer 重复工作；
- 提升 consumer service rate；
- 合理批处理；
- 明确 overload 时拒绝/降级；
- 调整容量并用真实 burst 分布验证；
- 给用户显示明确等待状态。

---

## 23. 贯穿案例的锁假设

代码检查发现慢路径需要获取一个 session 状态锁；另一个后台任务在持锁时执行 rollout 序列化和 flush。

事件顺序：

```text
1. Background task 获取 session lock
2. 锁内构造大 rollout payload
3. 锁内 await flush
4. Turn local transition 请求同一 lock
5. Turn 等待 5.6 s
6. Background task 释放 lock
7. Turn 在 8 ms 内完成本地更新
```

这解释了：

- p50 正常：多数请求没有撞上后台 flush；
- p95 变慢：条件性并发交错；
- CPU 火焰图不宽：大部分是 I/O 等待；
- between-sampling overhead 增加：等待发生在该阶段；
- sampling/tool duration 不变：它们不是根因。

此时应通过锁等待/持有边界的定向 instrumentation 或可控并发测试证伪，而不是立即重构。

---

## 24. 怎样写可证伪的性能假设

差：

```text
可能是锁太慢。
```

好：

```text
当后台 rollout flush 与 Turn transition 重叠时，后者等待同一 session lock；
如果这是 p95 回归根因，那么慢样本应同时满足：
lock_wait_ms 增长、lock_hold 覆盖 flush，且移出锁外的 I/O 在保持状态不变量后
会降低 tail latency，而 sampling/tool duration 不变。
```

证伪条件：

- 慢样本 lock wait 仍接近 0；
- flush 没有持有该锁；
- 控制并发交错无法复现；
- 优化锁边界后 p95 不变。

---

## 25. Allocation Profile 解决什么问题

CPU profile 告诉你 CPU 样本在哪里；allocation profile 告诉你内存在哪里被申请。

常见维度：

- allocation count：申请次数；
- allocated bytes：累计申请字节；
- live bytes：当前仍存活字节；
- retained bytes：由某条引用链继续保留的内存；
- peak RSS：进程驻留内存峰值。

它们不能互换。

例子：

```text
每秒分配并释放 1 GB：allocation rate 很高，live bytes 可能低
一次分配 500 MB 后长期保留：allocation rate 后续低，live/RSS 很高
```

第一种可能造成 allocator CPU 压力；第二种更像 retention/cache 容量问题。

---

## 26. Rust 中常见的分配热点

- 循环里反复 `String::new` / `Vec::new`；
- `clone()` 大 `String`、`Vec<u8>` 或嵌套对象；
- 多次 `format!` 和 JSON 中间值；
- 先完整序列化到 `String`，再只统计长度；
- `collect::<Vec<_>>()` 后立即只遍历一次；
- 缓冲区不复用或 capacity 不足反复扩容；
- cache entry 有界于数量，却没有字节上限。

当前 rollout persistence measurement 用 `CountingWriter` 配合 `serde_json::to_writer` 统计逻辑 JSON 字节，不需要先生成完整 JSON `String`。这是减少不必要中间 allocation 的一个具体例子，但仍要根据 profile 决定是否值得关注。

---

## 27. Clone 是 bug 吗

不是。Clone 可能用于：

- 跨 task 所有权；
- 让锁尽快释放；
- 保持 cache 内部拥有值；
- 简化小对象代码；
- 建立不可变快照。

只有证据表明某个 clone 位于热点、对象很大、调用频繁，且替代方案保持正确所有权时，才应优化。

“消灭所有 clone”常会换来：

- 更长锁持有；
- 复杂生命周期；
- 更广共享可变状态；
- 错误的数据身份。

---

## 28. 内存泄漏与内存增长的区别

内存增长可能来自：

- 合法 cache warming；
- allocator 保留已释放 arena；
- 一次高峰后 RSS 未立即归还 OS；
- 延迟释放的 batch；
- 真正不可达/永不删除的对象；
- queue backlog。

调查时画时间序列：

```text
输入停止后 live objects 是否下降？
cache 是否达到稳定上限？
queue drain 后内存是否回落？
重复相同 workload，每轮基线是否继续抬高？
```

只看一个时刻的 RSS 不能直接断言 leak。

---

## 29. Rollout Trace 不是 OpenTelemetry Trace

当前 `codex-rs/rollout-trace/README.md` 明确说明 rollout tracing：

- 是 opt-in 本地诊断路径；
- 只有设置 `CODEX_ROLLOUT_TRACE_ROOT` 才写 bundle；
- 不上传或报告这些 traces；
- bundle 可含 prompt、response、tool input/output、终端输出和路径，属于敏感数据；
- 先写有序 raw events/payload，再由离线 reducer 生成语义图。

它擅长回答：

```text
哪个模型请求产生了这个 tool call？
某段输出来自哪里？
Code Mode cell 怎样发起嵌套工具？
父子 Agent 如何传递任务和结果？
```

它不等同于 CPU profiler，也不自动提供每个函数的 CPU 样本。

### 29.1 名字相同，目的不同

| 名称 | 主要目的 |
|---|---|
| OpenTelemetry trace | 跨组件时间区间、因果与属性 |
| Rollout trace | Codex runtime 原始证据与离线语义图 |
| CPU profile/flame graph | on-CPU 调用栈占比 |

调查时可以组合使用，但不能互相替代。

---

## 30. Rollout Trace 的隐私边界

因为本地 bundle 可能包含：

- 用户 prompt；
- 模型 response；
- tool arguments/results；
- terminal output；
- filesystem paths；

所以不能：

- 未审查就上传到工单；
- 提交进 Git；
- 公开分享；
- 把它当普通低敏日志长期保留。

性能调查只需要 duration 或结构时，优先使用不含 payload 的 metric/span。确实需要 rollout evidence 时，最小化采集范围并脱敏。

---

## 31. Instrumentation Observer Effect

测量会影响被测对象，称为 observer effect。

例如：

- 每次 loop 打 log 改变 I/O 和锁竞争；
- 全量 span export 增加网络；
- heap profiler 改变 allocation 时序；
- debug build instrumentation 改变优化；
- 把 `Instant::now()` 放进极小函数，计时成本占比很高。

验证办法：

1. 测量 instrumentation 开/关时 benchmark 差异；
2. 只对目标阶段临时采样；
3. 使用低频聚合 metric；
4. 避免格式化未启用级别的昂贵 payload；
5. 结论在接近生产的构建模式下复核。

---

## 32. Sampling：观测系统也需要取样

全量 trace 在高流量系统中成本很高。Sampling 可以只保留部分 trace。

但随机采样可能错过稀少慢请求。常见思路包括：

- head sampling：请求开始时决定；
- tail sampling：看到 duration/status 后决定；
- error/slow trace 提高保留率；
- 其他正常流量低比例采样。

Tail sampling 更适合保留慢请求，但需要先暂存 trace 数据，系统更复杂。

本章不声称 Codex 当前采用某个特定采样策略；这是解释观测设计时必须理解的一般概念。

---

## 33. 日志级别和定向诊断

日志常见级别：

```text
ERROR > WARN > INFO > DEBUG > TRACE
```

提高全仓库到 TRACE 往往产生巨大噪声。更好的做法是只提高相关 target/module。

当前 Responses WebSocket timing 代码注释给出一个定向例子：

```text
RUST_LOG='codex_api::responses_websocket_timing=trace'
```

该事件包含完整 timing payload，并明确从 always-on sinks 排除，只允许显式 opt-in。它再次说明：高细节诊断数据需要单独的隐私和容量边界。

不要把这条环境变量机械用于所有问题；先确认目标 module 和日志内容。

---

## 34. 从 Profile 到优化的决策树

```text
p95 退化
├── sampling/tool/remote span 变宽
│   ├── 单次 remote latency 变宽 -> 网络/服务/连接
│   └── request count 增加 -> retry/重复工作
├── 本地 span 变宽 + CPU 高
│   ├── 火焰图窄而分散 -> 多处小成本/符号或 workload 问题
│   └── 明确宽栈 -> 算法/序列化/clone/压缩热点
├── 本地 span 变宽 + CPU 低
│   ├── lock wait -> 缩 critical section/拆所有权
│   ├── queue wait -> capacity/service rate/backpressure
│   ├── I/O -> 减少/并行/缓存/批处理
│   └── scheduler -> blocking work/runtime starvation
└── 内存/RSS 增长
    ├── allocation rate 高 -> 临时对象与复用
    ├── live bytes 高 -> retention/cache/queue
    └── 峰值高后稳定 -> burst/allocator/working set
```

每个箭头都需要证据，不是看到图形就直接重构。

---

## 35. Blocking work 对 Async Runtime 的影响

Tokio task 应在 await 点合作让出线程。如果 async task 中执行长时间同步 CPU 或 blocking I/O：

```rust
async fn handle() {
    expensive_sync_work(); // 长时间不 await
}
```

它可能占住 runtime worker，使其他 task 不能及时 poll，表现为：

- 多个不相关 span 同时出现空白；
- timer/取消响应延迟；
- CPU 利用率可能只集中在少数 worker；
- queue wait 增长。

可能的修复包括算法优化、分块合作、专用 blocking pool 等，但必须结合工作大小、并发和取消语义设计，不能看到同步函数就一律 `spawn_blocking`。

---

## 36. 怎样验证锁优化没有破坏正确性

假设准备把 rollout flush 移出 session lock。

先写不变量：

```text
1. flush 内容对应确定的状态版本；
2. 两次更新顺序不能倒置；
3. Turn completion 前需要的 durability 语义保持不变；
4. 取消/错误不会丢失待写内容；
5. 并发 flush 不会覆盖新状态。
```

然后验证：

- 控制事件交错的并发测试；
- 完整对象/持久化结果断言；
- 失败和取消路径；
- benchmark 的 p95/lock wait；
- 必要时跨平台执行。

性能补丁如果没有正确性不变量，只是把一个已知慢问题换成未知 race。

---

## 37. 一次完整剖析实验

### 阶段 A：建立症状

固定 commit、workload、并发和构建模式，确认 p95 回归可重复。

### 阶段 B：用现有 Metric 缩小边界

比较 Turn total、TTFT、TTFM、sampling/tool/compaction、request/retry count。

### 阶段 C：选择慢样本 Trace

与同类快样本比较 span 结构，而不是随机挑一条。

### 阶段 D：决定 Profiler

- CPU 高：CPU sampling/flame graph；
- CPU 低：await/lock/queue/I/O 边界；
- RSS 高：allocation/live/retention profile。

### 阶段 E：添加最小定向 instrumentation

只添加能区分剩余假设的 wait/hold/queue 指标或 span。

### 阶段 F：写可证伪实验

人为构造后台 flush 与 Turn transition 的交错。

### 阶段 G：实现最小修复

缩小锁范围或移动 I/O，同时用版本/快照保持状态一致。

### 阶段 H：分层验证

```text
并发正确性测试
目标 crate tests
microbenchmark
macro/Turn p95
instrumentation overhead
```

### 阶段 I：诚实报告限制

说明机器、样本、输入、是否包含远端服务，以及结论没有覆盖的路径。

---

## 38. 性能 Finding 怎样写

```text
[P1] Do not await rollout flush while holding the session-state lock

When the background flush overlaps the transition between two sampling requests,
the turn waits on this lock for the full I/O duration. Slow traces show 5–6 s of
between-sampling overhead with unchanged sampling/tool time, and the controlled
interleaving reproduces the wait. This raises p95 turn latency while p50 remains
stable. Snapshot the flush payload under the lock, release it before I/O, and
revalidate ordering/version invariants when applying completion state.
```

一条性能 finding 仍需：

- changed line；
- 触发条件；
- 观测证据；
- 用户/系统影响；
- 最小修复方向；
- 不夸大优先级。

“这个 clone 看起来慢”不够。

---

## 39. 常见误判

### 误判 1：最宽 span 就是 CPU 热点

Span 是 wall-clock；可能一直在 await。

### 误判 2：火焰图最高的塔最慢

高度是栈深，宽度才近似 CPU 样本占比。

### 误判 3：CPU profile 没热点，所以没有问题

等待型延迟不会形成宽 on-CPU 栈。

### 误判 4：RSS 没下降就是 leak

Allocator、cache 和 working set 都可能保留内存。

### 误判 5：给每个函数加 span 就能定位

可能增加噪声、基数、隐私和性能成本。

### 误判 6：Trace ID 可以做 metric tag

这会产生近乎每请求一个 time series 的高基数。

### 误判 7：去掉 bounded queue 能消除等待

只是把等待转换成无界内存积压。

### 误判 8：Rollout trace 就是性能 trace

它主要保存 runtime 证据和语义关系，不是 CPU sampling profile。

---

## 40. 性能剖析检查清单

### 症状

- p50/p95/p99 哪个变化？
- 绝对增加多少时间/内存？
- 哪个 workload、并发、平台触发？
- 请求数、重试数、错误率是否变化？

### Trace

- 慢/快样本是否同类？
- 最宽 span 是计算还是 await？
- 是否有未覆盖空白？
- 跨进程 parent context 是否连续？
- Span 是否重叠，能否直接相加？

### CPU

- 构建是否有优化和可读符号？
- Workload 是否稳定复现慢路径？
- 看的是 width 还是错误地看 color/height？
- Inclusive 与 self time 是否区分？

### Async/并发

- lock wait 与 hold 是否分开？
- 锁内是否 await I/O？
- queue wait 与 service time 是否分开？
- bounded capacity 和 backpressure 是否保留？
- 是否有 blocking work 饿死 runtime worker？

### Memory

- 看 allocated、live、retained 还是 RSS？
- 增长是否到达稳定上限？
- 输入停止后是否回落？
- Cache 是否同时有条目和字节边界？

### Instrumentation

- 新观测能区分哪个假设？
- 字段是否低基数、有界、脱敏？
- 是否已有等价 span/metric？
- 开销是否被测量？
- 调试数据是否需要显式 opt-in？

---

## 41. 本章词汇表

完整总表见[课程术语表](glossary.md)。

| 名词 | 常见写法 | 通俗解释 |
|---|---|---|
| Profiling | profile | 采集运行时证据，定位时间、CPU 或内存集中位置 |
| Trace | distributed trace | 一次操作跨组件的完整因果时间线 |
| Span | span | Trace 中一个有开始、结束和属性的区间 |
| Event | event | Span 时间线中的瞬时记录 |
| Attribute | tag/field | 描述 span、metric 或 event 的结构化字段 |
| Trace context | context | 跨进程传播 trace/parent 身份的元数据 |
| Traceparent | traceparent | W3C 定义的 trace ID、parent span ID 等 header |
| Tracestate | tracestate | W3C Trace Context 的附加厂商/系统状态 |
| Waterfall | timeline | 横向展示 span 开始、结束、嵌套和重叠的时间图 |
| Sampling profiler | sampling profile | 周期性记录调用栈，估计 on-CPU 时间分布 |
| Flame graph | flamegraph | 聚合采样栈，以框宽表示样本占比的图 |
| Inclusive time | total time | 函数自身加所有子调用的成本 |
| Self time | exclusive time | 函数自身、不含子调用的成本 |
| CPU-bound | CPU 密集 | 主要时间用于处理器计算 |
| I/O-bound | I/O 密集 | 主要时间用于等待磁盘、网络或外部操作 |
| Lock contention | 锁争用 | 多个执行者等待同一锁 |
| Critical section | 临界区 | 持锁期间受保护的代码区域 |
| Queue wait | 排队时间 | 入队后到真正开始处理之间的等待 |
| Service time | 服务时间 | Worker 真正处理任务的耗时 |
| Backpressure | 背压 | 下游容量不足时限制上游继续生产 |
| In-flight | 处理中 | 已进入系统但尚未完成的工作数量 |
| Allocation profile | 分配剖析 | 统计内存申请次数、字节和调用栈 |
| Retained memory | 保留内存 | 因仍可达引用而无法释放的内存 |
| Observer effect | 观察者效应 | 测量和记录本身改变程序表现 |
| Cardinality | 基数 | 标签字段可能出现的不同值数量 |
| Head sampling | 头部采样 | 请求刚开始就决定是否保留 Trace |
| Tail sampling | 尾部采样 | 看到耗时/状态后决定是否保留 Trace |
| Rollout trace | rollout trace | Codex 本地原始运行证据与离线语义图，不是 CPU profile |

### 性能剖析单词短语拆解

- `profile`：剖析；统计资源消耗落在哪些运行路径。
- `profiler`：剖析器；采集和汇总 profile 的工具。
- `trace`：踪迹；一次请求跨边界的因果链。
- `span`：跨度；一段有起止时间的操作。
- `parent`：父级；发起或包含当前 span 的上层操作。
- `child`：子级；由当前 span 发起的更细操作。
- `root span`：根 span；一条 Trace 最上层的起点。
- `attribute`：属性；用于筛选或解释的结构化字段。
- `event`：事件；某一瞬间发生的记录。
- `context`：上下文；用于保持跨调用关联的信息。
- `propagation`：传播；把 context 带到下一个进程或服务。
- `carrier`：载体；承载 trace context 的 header/envelope。
- `waterfall`：瀑布图；按真实时间排列各 span 的图。
- `sampling`：采样；只观察部分时刻或请求。
- `stack sample`：栈样本；某一采样时刻的调用栈。
- `flame graph`：火焰图；合并调用栈并用宽度表示样本占比。
- `frame`：栈帧；调用栈中的一个函数位置。
- `caller`：调用者；下层发起调用的函数。
- `callee`：被调用者；上层实际被调用的函数。
- `inclusive`：包含式；含所有子调用成本。
- `exclusive/self`：自身式；不含子调用成本。
- `on-CPU`：正在 CPU 上执行；不包括休眠/多数等待时间。
- `off-CPU`：没有在 CPU 上执行；常因锁、I/O 或调度等待。
- `CPU-bound`：CPU 受限；提升受计算能力限制。
- `I/O-bound`：I/O 受限；提升受外部读写等待限制。
- `contention`：争用；多个执行者竞争共享资源。
- `lock wait`：锁等待；从请求锁到获得锁的时间。
- `lock hold`：持锁时间；获得锁到释放锁的时间。
- `critical section`：临界区；持锁保护的状态操作。
- `queue`：队列；等待处理的工作集合。
- `enqueue`：入队；把工作加入队列。
- `dequeue`：出队；消费者取出并开始处理。
- `service time`：服务时间；不含排队的实际处理耗时。
- `backlog`：积压；尚未处理完成的队列工作。
- `backpressure`：背压；容量不足时向上游施加限制。
- `in-flight`：飞行中；已开始但尚未终结。
- `semaphore`：信号量；限制并发许可证数量的同步原语。
- `allocation`：分配；申请新的内存区域。
- `live bytes`：存活字节；当前仍被引用的内存。
- `retained`：被保留的；因引用或 cache 未能释放。
- `RSS`：Resident Set Size，常驻物理内存近似值。
- `leak`：泄漏；本应可释放的内存长期无法回收。
- `cardinality`：基数；字段不同取值数量。
- `instrumentation`：埋点/检测代码；为观测添加 span、metric、event。
- `observer effect`：观察者效应；观测本身改变被测性能。
- `opt-in`：主动启用；默认关闭，明确选择后才采集。
- `redaction`：脱敏；移除凭据、隐私和无关内容。

---

## 42. 自测题

1. 为什么一个 wall-clock 很宽的 span 不一定是 CPU 热点？
2. CPU-bound 与 I/O-bound 的典型证据分别是什么？
3. p50 正常、p95 退化通常提示什么类型的问题？
4. Metric、Trace 和 CPU profile 各回答什么问题？
5. 为什么 `thread_id` 通常不适合做 metric tag？
6. Span 与 event 的核心区别是什么？
7. Parent span duration 为什么不能总是等于 child duration 之和？
8. Trace waterfall 中没有 child 覆盖的空白可能表示什么？
9. App-server request span 为什么统一不同 transport 的 shape？
10. `traceparent` 与 `tracestate` 用于什么？
11. Trace context 为什么不能当作授权凭据？
12. 仓库为什么优先在 async 函数定义上使用 `#[tracing::instrument]`？
13. 添加过多 span 会产生哪些成本？
14. Sampling profiler 怎样估计 CPU 时间分布？
15. 火焰图的宽度、高度和颜色分别该怎样理解？
16. CPU profile 没有热点时下一步应检查什么？
17. Async 函数 duration 5 秒为何可能只做了 10 ms 计算？
18. Lock wait 与 lock hold 为什么必须区分？
19. 在锁内 await 网络有什么风险？
20. Queue wait 与 service time 怎样组成总延迟？
21. 为什么把 bounded channel 改为 unbounded 不是安全性能修复？
22. 贯穿案例中的哪些证据共同支持锁等待假设？
23. 好的性能假设应怎样被证伪？
24. Allocation rate、live bytes 与 RSS 有什么区别？
25. 为什么 clone 不能一概视为性能 bug？
26. RSS 不下降为什么不一定是 leak？
27. OpenTelemetry trace、rollout trace 和 CPU profile 有什么区别？
28. Rollout trace bundle 为什么必须视为敏感数据？
29. Observer effect 怎样让性能结论失真？
30. Head sampling 与 tail sampling 有什么差别？
31. 定向 `RUST_LOG` 为什么优于全仓库 TRACE？
32. Blocking work 如何饿死 async runtime worker？
33. 缩小锁范围前必须保护哪些状态不变量？
34. 一条可执行性能 finding 需要哪些内容？

---

## 43. 源码检查点

1. 根目录 `AGENTS.md`
   - 搜索 async tracing 规则：优先 instrument 函数定义，并检查是否已有 span。
2. `codex-rs/otel/README.md`
   - 查看 provider、logs/traces/metrics、trace context 和 shutdown 的职责。
3. `codex-rs/otel/src/config.rs`
   - 查看 exporter、span attributes、tracestate 和 debug-build 默认行为。
4. `codex-rs/config/src/types.rs`
   - 查看 `OtelConfigToml` 与有效 `OtelConfig`。
5. `codex-rs/core/src/config/otel.rs`
   - 看配置默认值、metadata 验证和 startup warning。
6. `codex-rs/otel/src/metrics/names.rs`
   - 查看 Turn、API、WebSocket、tool 等 metric 边界。
7. `codex-rs/otel/src/metrics/timer.rs`
   - 看 RAII `Timer` 怎样在 Drop 时记录 duration。
8. `codex-rs/core/src/tasks/mod.rs`
   - 找 `TURN_E2E_DURATION_METRIC` timer 怎样与 `RunningTask` 同寿命。
9. `codex-rs/core/src/turn_timing.rs`
   - 看 sampling、compaction、tool blocking 和 between-sampling overhead。
10. `codex-rs/core/src/turn_timing_tests.rs`
    - 看人工 `Instant` 时间线怎样深度比较完整 `TurnProfile`。
11. `codex-rs/app-server/src/app_server_tracing.rs`
    - 看 request span 字段、transport 和 parent context。
12. `codex-rs/app-server/src/message_processor.rs`
    - 查 request future 怎样进入对应 span。
13. `codex-rs/app-server/src/message_processor_tracing_tests.rs`
    - 看请求 trace/span 关联测试。
14. `codex-rs/otel/src/trace_context.rs`
    - 看 W3C context 提取、注入、parent 设置、验证和 merge。
15. `codex-rs/exec-server/src/trace_context.rs`
    - 看当前 span context 怎样进入 HTTP headers。
16. `codex-rs/exec-server/src/trace_context_tests.rs`
    - 看 trace header 传播测试。
17. `codex-rs/core/src/mcp_tool_call/telemetry.rs`
    - 看 MCP count/duration/error tags、sanitize 和长度边界。
18. `codex-rs/core/src/hook_runtime.rs`
    - 看 Hook completed duration 怎样转为 metric。
19. `codex-rs/rollout/src/persistence_metrics.rs`
    - 看 `CountingWriter`、逻辑 JSON 大小和采样 metric。
20. `codex-rs/rollout/src/sqlite_metrics.rs`
    - 看 DB telemetry adapter 怎样附加 bounded originator。
21. `codex-rs/codex-api/src/endpoint/responses_websocket.rs`
    - 搜索 `responses_websocket_timing` 和定向 TRACE opt-in。
22. `codex-rs/rollout-trace/README.md`
    - 对比本地 raw evidence/reducer graph 与 OpenTelemetry performance trace。
23. `codex-rs/core/src/tools/tool_dispatch_trace.rs` 与 `codex-rs/core/src/tools/registry.rs`
    - 看 adapter 怎样集中 start/completed/failed 映射，以及 registry 各 early-return 分支怎样显式记录失败终态。

---

## 44. 一句话总结

性能剖析的核心不是找到图上最显眼的函数，而是先用 Metric 确认哪类请求和分位数发生变化，再用 Turn profile 与跨组件 Trace 把 wall-clock 延迟定位到具体阶段；CPU 高时用 sampling profile 和火焰图找 on-CPU 热点，CPU 低时拆分锁等待、队列等待、I/O 与调度，内存增长时区分 allocation、live、retained 和 RSS；最后只添加能够证伪假设、低基数、有界且脱敏的 instrumentation，在保持并发、持久化和安全不变量的前提下验证最小优化。
