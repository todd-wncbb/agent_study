# 36：性能回归与基准测试实战——怎样证明“真的变慢了”

上一章学习怎样从 diff 中发现正确性和兼容性问题。本章处理另一类常见问题：程序功能完全正确，但用户觉得它越来越慢。

“感觉慢”是重要线索，却还不是可以修改代码的证据。性能工作真正困难的地方通常不是写优化，而是回答下面三个问题：

1. 慢的是哪一段？
2. 在什么输入和状态下慢？
3. 修改后怎样证明提升不是偶然噪声，也没有牺牲正确性？

> 源码基线：`4ee41929eaf4`。本章会引用仓库里的真实 benchmark 和 timing 代码；“图片附件性能回归”是教学案例，不表示当前源码存在这个缺陷。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分性能症状、性能指标和根因；
2. 区分 latency、throughput、CPU、memory 和用户感知速度；
3. 区分 microbenchmark、macrobenchmark 与生产 telemetry；
4. 为冷缓存、热缓存和不同输入规模分别设计案例；
5. 理解平均值、median、p95、p99 和 outlier；
6. 避免把 fixture 构造、I/O 或 clone 误算进被测逻辑；
7. 使用仓库当前的 Divan、`just bench` 和 smoke 入口；
8. 判断一次性能差异是否足够稳定、足够重要；
9. 把 benchmark、正确性测试和线上观测组合成完整证据链；
10. 写出别人能够复现和审查的性能报告。

---

## 2. 先说人话：性能测试究竟在测什么

假设你每天走路去地铁站。

- 总共用了多久，是**端到端延迟**；
- 等电梯用了多久，是其中一个**阶段耗时**；
- 一小时能运送多少乘客，是**吞吐量**；
- 工作日早高峰和周日早晨，是两种不同**工作负载**；
- 第一次开门要等电梯启动，之后连续运行更快，类似**冷启动与热路径**；
- 某天遇到红灯特别多，是一次**离群值**。

如果只记录“今天走了 18 分钟”，你不能直接断言电梯变慢。可能是：

- 出门晚，路上人多；
- 红灯更多；
- 电梯慢；
- 地铁安检排队；
- 计时开始和结束的位置变了。

程序性能也是一样。一个总耗时数字只有在**边界、输入、环境和统计方法明确**时才有解释力。

---

## 3. 正确性测试和 benchmark 不是一回事

### 3.1 正确性测试问什么

```text
给定输入 X，输出是否等于 Y？
```

例如：

```rust
assert_eq!(resize(image), expected_image);
```

它主要回答“结果对不对”。

### 3.2 Benchmark 问什么

```text
在明确环境和工作负载下，操作 X 的耗时或吞吐量分布怎样？
```

它主要回答“完成得多快、波动多大”。

### 3.3 两者不能互相替代

一个函数可以：

- 输出正确，但从 20 ms 退化到 800 ms；
- 输出错误，但运行速度极快；
- benchmark 很稳定，却一直测的是不真实输入；
- 单元测试通过，但性能优化改变了边缘输入语义。

因此性能修改至少需要两条证据线：

```text
正确性测试：行为仍然正确
benchmark：目标工作负载确实改善
```

必要时还要加第三条：

```text
生产 telemetry：真实用户路径也出现相同改善
```

---

## 4. 五类常见性能指标

### 4.1 Latency：一次操作多久完成

`latency` 常译为“延迟”。例如：

- 打开 Codex 到出现界面的时间；
- 提交 turn 到第一个 token 的时间；
- 处理一张图片附件的时间；
- 一次工具调用的完成时间。

单位通常是 ns、µs、ms 或 s。

### 4.2 Throughput：单位时间完成多少工作

`throughput` 常译为“吞吐量”。例如：

```text
每秒处理 1,200 个事件
每分钟索引 800 个 rollout
每秒编码 40 张图片
```

Latency 和 throughput 相关，但不是同一个指标。并发批处理可能提高总体吞吐量，却让单个请求等待更久。

### 4.3 CPU time：处理器实际做了多少工作

Wall-clock latency 包含等待磁盘、网络、锁和调度；CPU time 更关注处理器实际执行时间。

两个版本都耗时 200 ms：

- A 使用一个核心忙算 200 ms；
- B 只用 10 ms CPU，其余在等待网络。

它们的优化方向完全不同。

### 4.4 Memory：占用多少内存

性能回归不只表现为更慢，还可能表现为：

- peak RSS 增长；
- allocation 次数增加；
- 临时大对象复制；
- cache 无界增长；
- 长生命周期对象保留本应释放的数据。

### 4.5 User-perceived latency：用户感知到的等待

用户不一定等到整个 turn 完成才觉得系统“有响应”。常见边界包括：

- 点击后界面出现反馈；
- 第一个 token 出现；
- 第一条完整 assistant message 出现；
- 工具开始执行；
- 整个 turn 完成。

优化总耗时 5%，但让首 token 提前 50%，可能明显改善体验。反过来，总耗时更短但界面长时间没有任何反馈，用户仍可能觉得更慢。

---

## 5. Codex 中已经存在的 Turn 耗时边界

真实源码 `codex-rs/core/src/turn_timing.rs` 中，`TurnTimingState` 不只保存一个总时间，还跟踪：

```text
started_at
first_token_at
first_message_at
sampling
compaction
between_sampling_overhead
tool_blocking
sampling_request_count
sampling_retry_count
```

通俗地说，它在回答：

```text
一次 turn 总共慢
    ├── 是模型 sampling 慢？
    ├── 是 compaction 慢？
    ├── 是工具阻塞慢？
    ├── 是两次 sampling 之间的本地工作慢？
    └── 还是重试次数增加？
```

### 5.1 TTFT

`TTFT` 是 `Time To First Token`，即从 turn 开始到第一个模型 token 到达。

它能描述“用户多久第一次看到模型有输出”，但不能单独描述整个任务完成时间。

### 5.2 TTFM

`TTFM` 在当前代码中表示 `Time To First Message`：从 turn 开始到第一条 agent message item 出现。

首 token 和首条消息不是同一边界。模型可能先输出 reasoning、事件或其他 item。

### 5.3 Phase profile

`begin_sampling`、`begin_compaction`、`begin_tool_blocking` 返回 guard；guard 被 drop 时结束相应阶段计时。

这种 RAII 设计的好处是：

```text
阶段开始 -> 创建 guard
正常 return / ? / 提前退出 -> guard 仍会 drop
阶段耗时 -> 得到闭合
```

但 benchmark 不应直接把这些 production metrics 当成唯一证据。Telemetry 适合发现真实分布，受用户输入、网络、模型和机器差异影响；本地 benchmark 更适合隔离一个可控变化。

---

## 6. 三层性能证据

| 层次 | 主要问题 | 优点 | 局限 |
|---|---|---|---|
| Microbenchmark | 一个函数/小组件有多快 | 隔离、稳定、快速 | 可能不代表真实路径 |
| Macrobenchmark | 完整进程或跨组件路径有多快 | 更接近用户体验 | 噪声更多、定位较难 |
| Production telemetry | 真实用户实际经历什么 | 工作负载真实、样本多 | 变量多，因果关系难证明 |

最可靠的推理通常是：

```text
Telemetry 发现问题
        ↓
Profile / trace 定位阶段
        ↓
Microbenchmark 稳定复现热点
        ↓
实现最小优化并验证正确性
        ↓
Macrobenchmark 检查端到端收益
        ↓
Telemetry 观察真实发布效果
```

不是每个小改动都必须走完全部层次，但要知道每种证据能证明什么、不能证明什么。

---

## 7. 当前仓库怎样运行 benchmark

根目录 `AGENTS.md` 规定：

- 使用 Divan 编写新的 Rust benchmark；
- 用 `just bench` 运行 Cargo benchmark；
- 用 `just bench-smoke` 只跑一次，确认 benchmark 能启动。

根目录 `Justfile` 当前定义：

```make
bench *args:
    cargo bench --workspace --bench '*' {args}

bench-smoke:
    just bench -- --test
```

此外还有 Bazel 端到端入口：

```text
just bench-e2e
just bench-e2e-smoke
```

### 7.1 Smoke 不是性能结论

`smoke` 的目标只是验证：

- benchmark 能编译；
- fixture 能读取；
- 被测程序能启动；
- 基准函数不会立即崩溃。

一次运行没有足够样本估计分布，因此不能用它声称“提升了 18%”。

### 7.2 `bench` 与 `test` 的角色

```text
just test -p <crate>   -> 检查行为正确
just bench-smoke       -> 检查 benchmark 可运行
just bench ...         -> 收集性能样本
```

它们是互补步骤，不是三个名字不同的同类命令。

---

## 8. 真实微基准：图片怎样进入 prompt

当前仓库有：

```text
codex-rs/utils/image/benches/prompt_images.rs
```

对应 `Cargo.toml`：

```toml
[dev-dependencies]
divan = { workspace = true }

[[bench]]
name = "prompt_images"
harness = false
```

`harness = false` 表示不使用 Rust 默认 test harness，而由 benchmark 程序自己的 `main` 驱动：

```rust
fn main() {
    divan::main();
}
```

基准覆盖四个场景：

```text
small PNG screenshot + fresh attachment
large PNG screenshot + fresh attachment
large JPEG photo + fresh attachment
small PNG screenshot + repeated attachment
```

这里已经体现两条重要原则：

1. 输入规模和格式要分开；
2. cache miss 与 cache hit 要分开。

---

## 9. 教学案例：用户说“添加截图后发送越来越慢”

我们先把模糊症状拆开。

用户观察：

```text
以前添加截图后很快就能发送；最近大图要等一会儿。
```

这句话还不能证明：

- 是 PNG 解码慢；
- 是 resize 慢；
- 是 base64 编码慢；
- 是网络上传慢；
- 是模型 TTFT 慢；
- 是 UI 主线程被阻塞；
- 是第一次处理慢还是每次都慢。

### 9.1 第一步：定义可观察边界

我们可以把路径先简化为：

```text
读取图片字节
   ↓
识别格式
   ↓
解码
   ↓
按 PromptImageMode resize
   ↓
重新编码
   ↓
转换成 data URL
   ↓
加入模型请求
```

教学假设：trace 表明慢点位于 `load_for_prompt_bytes(...).into_data_url()`，而不是网络或 sampling。

只有得到这种边界证据后，微基准才有正确目标。

### 9.2 第二步：定义工作负载矩阵

| 维度 | 案例 |
|---|---|
| 格式 | PNG、JPEG |
| 内容 | UI 截图、纹理照片 |
| 尺寸 | 1536×864、2560×1440、3264×2448 |
| Cache | fresh/miss、repeated/hit |
| 模式 | `ResizeToFit` |

为什么不只测一张 10×10 的纯色图？

因为它可能走相同函数，却没有用户输入的解码、压缩和纹理成本，容易优化一个不存在的主要问题。

---

## 10. Fresh 和 repeated 为什么要分开

### 10.1 Fresh attachment

同一内容第一次出现时，缓存中没有结果，需要执行完整处理。

它近似回答：

```text
用户第一次附加这张图片要等多久？
```

### 10.2 Repeated attachment

内容已经处理过，缓存可以命中。

它近似回答：

```text
相同图片再次进入 prompt 时，缓存路径有多快？
```

### 10.3 混在一起会发生什么

假设测量 100 次：

```text
第 1 次：100 ms，cache miss
后 99 次：1 ms，cache hit
```

平均值约为 1.99 ms。你可能错误地写出：

```text
图片处理只需约 2 ms。
```

但新图片用户实际经历的是 100 ms。Benchmark 极其稳定，却回答了错误问题。

---

## 11. 真实代码怎样强制 cache miss

当前 `prompt_images.rs` 创建 48 个输入变体：

```rust
const CACHE_MISS_VARIANT_COUNT: usize = 48;

fn cache_miss_variants(image: Vec<u8>) -> Vec<Vec<u8>> {
    (0..CACHE_MISS_VARIANT_COUNT)
        .map(|variant| {
            let mut image = image.clone();
            image.extend_from_slice(&variant.to_le_bytes());
            image
        })
        .collect()
}
```

源码注释说明 loader 按内容 digest 缓存；追加不同后缀会得到不同 digest，从而让 benchmark 保持在 miss 路径。

这里需要理解两层语义：

- benchmark 不是声称用户图片真的带这些后缀；
- 它是在构造多个内容身份不同、但解码主体近似相同的输入，避免第二轮误入 hit 路径。

### 11.1 为什么是多个变体

如果每轮都使用完全相同字节：

```text
第一轮 miss
后续轮次 hit
```

如果每轮临时生成全新复杂图片，则生成图片本身可能污染测量或让准备成本过高。预先生成一组变体，在可控内存和真实 cache 行为之间取得折中。

---

## 12. 不要把输入准备误算进被测时间

真实 benchmark 使用：

```rust
bencher
    .with_inputs(move || {
        let image = images[image_index].clone();
        image_index = (image_index + 1) % images.len();
        image
    })
    .bench_local_values(move |image| prepare_prompt_data_url(path, image));
```

源码注释明确说明：Divan 将 `with_inputs` 排除在被测时间之外。

### 12.1 为什么重要

我们想测：

```text
prepare_prompt_data_url 的处理成本
```

而不是：

```text
从 Vec 选输入 + clone 大字节数组 + 处理图片
```

如果优化后处理时间从 20 ms 降到 10 ms，但每轮 clone 都是 30 ms：

```text
旧总时间：50 ms
新总时间：40 ms
```

报告只看到 20% 改善；目标函数其实改善了 50%。更糟的是，不同输入大小的 clone 成本可能让结果排序完全改变。

### 12.2 但也不能永远排除 clone

如果真实产品路径确实每次都会 clone 大图片，那么 clone 是端到端成本的一部分。

正确做法是分层测量：

```text
microbenchmark A：只测转换逻辑
microbenchmark B：测 clone/allocation
macrobenchmark：测真实组合路径
```

不要为了数字“好看”随意排除成本；应根据问题边界决定。

---

## 13. Fixture 是什么，怎样避免测假数据

`fixture` 是为测试或 benchmark 准备的固定输入样例。

当前图片 benchmark 没有依赖用户私有图片，而是生成：

- 带工具栏、侧栏、边框和文字纹理的 UI screenshot；
- 带渐变和纹理的 photo。

这样做的价值：

- 可重复；
- 不依赖外部文件或网络；
- 没有版权和隐私问题；
- 图像复杂度比纯色块更接近真实编码工作。

但 synthetic fixture 仍然是近似。它不自动证明覆盖了：

- 极端宽高比；
- 透明通道边缘；
- EXIF orientation；
- 动图；
- 损坏文件；
- 真实截图压缩分布。

正确性边缘输入应由测试覆盖；benchmark 只选择对目标性能问题有代表性的集合。

---

## 14. 平均值为什么经常不够

假设有 10 次耗时：

```text
10, 10, 11, 10, 9, 10, 11, 10, 10, 100 ms
```

### 14.1 Mean

平均值：

```text
(10 + 10 + 11 + 10 + 9 + 10 + 11 + 10 + 10 + 100) / 10
= 19.1 ms
```

它被最后一次 100 ms 明显拉高。

### 14.2 Median / p50

排序后中间位置约为 10 ms。它更像“典型一次”的体验。

### 14.3 p95 / p99

Percentile 回答：某个比例的样本不超过这个值。

- p50：50% 样本不超过它；
- p95：95% 样本不超过它；
- p99：99% 样本不超过它。

尾延迟对交互系统很重要。平均值保持不变，不代表最慢的 5% 用户没有严重退化。

### 14.4 小样本的 percentile 要谨慎

只有 10 个样本时声称精确 p99 没有多少意义。高 percentile 需要足够样本，且应说明统计工具和采样方式。

---

## 15. Outlier 是错误数据吗

不一定。

Outlier 可能来自：

- OS 调度；
- 后台进程；
- CPU 降频或温度；
- page fault；
- 磁盘或网络抖动；
- allocator 行为；
- 真正存在的慢路径。

不能看到离群值就删除。先问：

```text
它是测量环境偶发干扰，还是产品路径真实尾延迟？
```

如果每隔固定次数就慢一次，可能不是噪声，而是 cache eviction、批量 flush、GC 类行为或周期性资源竞争。

---

## 16. 常见 benchmark 噪声来源

### 16.1 Debug 与 release 构建混用

优化级别不同，结果不能直接比较。端到端 benchmark 当前用 Bazel `--compilation_mode=opt`，目的是接近 production-style optimized binaries。

### 16.2 同时运行编译或大型测试

CPU、磁盘和内存竞争会污染结果。

### 16.3 机器状态不同

包括电源模式、温度、架构、内存压力和操作系统版本。

### 16.4 网络和远端服务

真实 API 延迟受网络和服务端影响。若目标是本地序列化逻辑，应 mock 或隔离网络；若目标正是用户端到端延迟，则要接受噪声并增加样本、记录环境。

### 16.5 Cache 状态不一致

一组跑前清 cache，另一组不清，比较没有意义。

### 16.6 输入不一致

旧版本测小图，新版本测大图；即使结果数值接近，也不能推断版本影响。

### 16.7 首轮一次性成本

动态库加载、lazy initialization、JIT、文件系统 cache 或连接建立都可能使首轮更慢。应决定这是不是目标工作负载，而不是自动丢弃。

---

## 17. Warm-up 的真实含义

`warm-up` 是正式采样前先运行若干次，让一次性初始化完成并使系统进入稳定状态。

Warm-up 适合回答：

```text
已经运行中的稳定路径有多快？
```

但它会隐藏：

```text
首次启动或首次操作有多慢？
```

因此不要把 warm-up 当成固定仪式。先定义问题：

- 测 CLI 启动：首轮成本往往就是用户体验；
- 测长期事件解析：稳定状态更重要；
- 测首次图片 attachment：不能先把相同内容预热成 cache hit；
- 测 repeated attachment：主动预热正是案例定义的一部分。

---

## 18. 真实宏基准：`codex --help`

当前源码：

```text
codex-rs/cli/e2e_benches/codex_help.rs
```

核心逻辑：

```rust
#[divan::bench(sample_count = 20, sample_size = 1)]
fn codex_help(bencher: Bencher) {
    let codex = codex_utils_cargo_bin::cargo_bin("codex")
        .expect("codex binary should be available through Bazel runfiles");

    bencher.bench_local(move || {
        let output = Command::new(&codex)
            .arg("--help")
            .output()
            .expect("codex --help should run");
        assert!(output.status.success(), "codex --help should succeed");
    });
}
```

它测量的边界包括：

- 创建子进程；
- 操作系统加载 executable；
- Codex CLI 初始化；
- 参数解析；
- 帮助输出；
- 进程退出和父进程等待。

这不是一个函数级微基准，而是便宜、确定性的 end-to-end process benchmark。

### 18.1 为什么仍然检查成功状态

如果优化意外让程序立即崩溃，耗时可能大幅“改善”。

```rust
assert!(output.status.success());
```

是最低限度的正确性 guard，防止把失败快当成性能快。

但它没有验证 help 文本完整，因此更全面的正确性仍应由普通测试或 snapshot 承担。

---

## 19. Microbenchmark 与 Macrobenchmark 如何互相验证

假设修改图片缓存后：

```text
micro fresh：100 ms -> 55 ms
micro repeated：1.2 ms -> 1.1 ms
```

这表明目标函数的 cold path 显著改善，warm path 基本不变。

但真实 UI 端到端：

```text
点击发送到请求发出：420 ms -> 370 ms
```

为什么不是也改善 45%？因为目标函数只是总路径的一部分：

```text
旧总时间 = 图片处理 100 + 其他 320 = 420
新总时间 = 图片处理 55 + 其他 315 = 370
```

这就是 Amdahl’s Law 的直觉：只优化总流程的一部分，总体提升受该部分原占比限制。

不要把函数级 45% 改善写成“Codex 发送图片快了 45%”，除非端到端证据确实如此。

---

## 20. 性能回归调查的八个步骤

### 步骤 1：把症状写成可比较句子

差：

```text
图片很慢。
```

好：

```text
在同一台机器上，第一次附加 2560×1440 PNG 到请求准备完成的中位耗时，
从基线提交的约 52 ms 增加到候选提交的约 96 ms。
```

### 步骤 2：固定基线和候选版本

记录 commit，而不是写“昨天”和“现在”。

### 步骤 3：固定环境

至少记录：

- OS 与架构；
- build mode；
- 相关 feature/config；
- 机器电源和负载条件；
- benchmark 命令。

### 步骤 4：找阶段边界

用 trace、profile、现有 metrics 或临时局部计时，避免直接猜代码热点。

### 步骤 5：建立能稳定复现的最小 benchmark

保留问题特征，排除网络、模型和无关 UI 工作。

### 步骤 6：一次只改变一个变量

例如先只比较代码版本，不同时更换编译器、fixture 和机器。

### 步骤 7：检查分布和绝对值

同时问：

- 相对变化多少百分比？
- 绝对多了多少毫秒？
- 波动范围多大？
- 用户会不会感知？

### 步骤 8：用正确性与更高层测试收尾

Benchmark 通过不意味着语义正确；micro 改善也不保证端到端改善。

---

## 21. 怎样比较两个版本

### 21.1 最简单的相对变化

```text
regression % = (new - old) / old × 100%
improvement % = (old - new) / old × 100%
```

例如：

```text
old = 50 ms
new = 60 ms
regression = 20%
```

### 21.2 百分比必须和绝对值一起看

```text
100 ns -> 150 ns：退化 50%，绝对增加 50 ns
100 ms -> 150 ms：退化 50%，绝对增加 50 ms
```

相对值相同，用户影响完全不同。

### 21.3 交替运行优于全部先后运行

如果先连续跑完旧版本，再连续跑完新版本，机器温度或后台负载可能随时间漂移。

更稳妥的实验可交替：

```text
old A -> new A -> old A -> new A
```

或使用专门比较工具随机化顺序。无论用什么工具，都要报告方法。

### 21.4 不要只挑最好的一次

“best of 10” 会偏向偶然幸运结果。应使用 benchmark 框架收集的完整统计，或明确说明为何使用最小值。

---

## 22. 一个看似合理但错误的 benchmark

```rust
#[test]
fn image_is_fast() {
    let start = Instant::now();
    let output = prepare_prompt_data_url("image.png", tiny_png());
    assert!(start.elapsed() < Duration::from_millis(50));
    assert!(!output.is_empty());
}
```

问题包括：

1. 混在普通 test 中，debug/CI 机器差异会造成不稳定；
2. 只运行一次；
3. `tiny_png()` 构造时间是否计入不清楚；
4. 没有区分 cache hit/miss；
5. 50 ms 阈值缺乏基线依据；
6. CI 并发负载可能造成 flaky failure；
7. tiny fixture 未必代表用户大图；
8. 只检查输出非空，正确性过弱。

### 22.1 时间断言何时有用

超时类测试可以验证“不会无限挂住”或取消必须在宽松上界内生效，但不要把共享 CI 上的窄 wall-clock 阈值当精密性能回归检测。

---

## 23. Benchmark 也可能被编译器优化掉

如果被测结果完全未使用，编译器可能证明计算没有外部影响并删除它。

Benchmark 框架通常提供机制阻止这种 dead-code elimination。使用框架的 `bench_local`、返回值消费或 `black_box` 等正确接口，不要自己随意写空循环。

错误示意：

```rust
let start = Instant::now();
for _ in 0..1_000_000 {
    2 + 2;
}
println!("{:?}", start.elapsed());
```

优化构建可能把循环全部删除，最后测到的只是计时器和打印附近成本。

---

## 24. 异步 benchmark 的额外陷阱

### 24.1 Runtime 创建成本

如果每轮都创建 Tokio runtime，你测到的可能主要是 runtime startup，而不是 async 函数。

### 24.2 只创建 Future 却没有 await

Rust async 函数调用先返回 Future；没有 poll/await，工作可能根本未执行。

### 24.3 Mock 太快

零延迟 mock 有助于隔离本地开销，但可能隐藏 backpressure、并发竞争和真实 buffer 行为。

### 24.4 Sleep 不是可靠工作负载

`sleep(10 ms)` 测量调度和 timer，不能代表 CPU 工作；实际唤醒时间通常不早于目标，但可能更晚。

### 24.5 并发数必须明确

比较 throughput 时，要固定：

- task 数量；
- channel 容量；
- 输入数量；
- producer/consumer 比例；
- 是否包含 setup 和 teardown。

---

## 25. 锁和 Channel 性能怎样测

只测单线程无竞争 mutex，不能推断高并发 contention。

至少区分：

```text
uncontended：没有其他 task 竞争
contended：多个 task 同时请求同一锁
short critical section：锁内工作很少
long critical section：锁内做 I/O 或大量计算
```

Channel 同样要区分：

```text
bounded / unbounded
single producer / multiple producers
single consumer / multiple consumers
empty queue / saturated queue
message size
```

如果优化通过把 bounded channel 改成 unbounded 获得短期吞吐量提升，却导致内存无界增长，这不是安全优化，而是把 backpressure 问题藏起来。

---

## 26. 性能优化不能破坏不变量

常见危险“优化”包括：

- 跳过权限检查以减少延迟；
- 取消 output truncation 以少做一次遍历；
- 复用本应按 turn 隔离的状态；
- 缓存包含凭据或用户敏感数据；
- 使用无界 cache；
- 省略 fsync/flush，改变持久化承诺；
- 把正确等待改成 race；
- 为减少 clone 而引入悬空生命周期或错误共享。

性能目标必须排在行为和安全契约之后：

```text
先保持正确性、安全、兼容性
再在这些约束内降低成本
```

---

## 27. Cache 优化必须回答的七个问题

1. Cache key 是什么？
2. 相同 key 是否真的代表相同语义？
3. 何时失效？
4. 最大条目数或字节数是多少？
5. 跨 thread/user 共享是否安全？
6. 敏感内容是否会被长期保留？
7. 命中率不足时，维护 cache 的开销是否反而更大？

图片 benchmark 用内容 digest 区分缓存身份，是具体实现事实。但任何新 cache 都不能仅凭 benchmark 变快就合并，还要审查所有权、生命周期、隔离和容量。

---

## 28. Telemetry、Trace、Profile、Benchmark 的区别

| 工具/证据 | 回答的问题 |
|---|---|
| Log | 某个时刻发生了什么 |
| Trace | 一次请求跨阶段怎样流动，各 span 多久 |
| Metric/Telemetry | 大量运行中的分布、计数和趋势怎样 |
| CPU profile | CPU 时间集中在哪些调用栈 |
| Heap profile | allocation 和内存保留在哪里 |
| Benchmark | 固定工作负载在受控环境下表现怎样 |

如果 p95 turn duration 上升：

- metric 先告诉你趋势存在；
- trace 告诉你主要增加在哪个阶段；
- profile 找到阶段内热点；
- benchmark 固定热点并比较补丁；
- metric 再验证真实发布。

---

## 29. Histogram 为什么比单个总和更有用

OpenTelemetry histogram 会把观测分配到多个 bucket，并保留 count、sum 等聚合。

例如 bucket 边界：

```text
5 ms, 10 ms, 25 ms, 50 ms, 100 ms, 250 ms ...
```

可以回答：

- 有多少请求不超过 25 ms；
- 长请求是否越来越多；
- count 是否变化；
- sum 增长是流量增加还是单次变慢。

当前 `codex-rs/otel/tests/suite/timing.rs` 会验证 duration metric：

- 有 bucket bounds；
- bucket count 总和正确；
- sum 和 count 正确；
- 单位为 `ms`；
- description 正确；
- tags/attributes 被保留。

这属于 telemetry 正确性测试，不是目标函数的性能 benchmark。

---

## 30. 怎样设计一个新的 Codex benchmark

假设你怀疑 rollout preview 提取在超长历史上变慢。

### 30.1 先写工作负载说明

```text
目标：测量从 1,000 个合法 rollout items 提取 preview 的 CPU latency。
包含：解析已在内存中的 item、选择 preview。
不包含：磁盘读取、SQLite、fixture 生成。
案例：短消息、长消息、含 tool output、没有用户消息。
```

### 30.2 再选层次

- 如果只怀疑纯函数：microbenchmark；
- 如果怀疑 rollout 文件读取与解析组合：macrobenchmark；
- 如果用户列表页变慢：再补端到端或生产 timing。

### 30.3 把 setup 移出 measurement

预先构建 1,000 个 items，迭代时传给被测函数。若 clone 是真实成本，则另建包含 clone 的案例，不要含糊。

### 30.4 保留结果

确保返回的 preview 被 benchmark harness 消费，避免编译器删除工作。

### 30.5 先跑 smoke

确认 target、fixture 和依赖正确。

### 30.6 再收集基线与候选数据

固定命令、机器、模式和 commit，保存完整输出。

### 30.7 最后检查更高层收益

函数快 30% 但列表页无可测变化时，应诚实描述局部结果，不夸大产品影响。

---

## 31. 性能报告应该怎样写

推荐模板：

```markdown
### Problem

首次处理大 PNG 的耗时在提交 A 到 B 之间上升。

### Workload

- 2560×1440 synthetic UI screenshot
- PNG
- ResizeToFit
- forced cache-miss path

### Environment

- macOS / arm64
- optimized benchmark build
- same machine and power mode

### Command

<精确命令>

### Results

| version | median | p95 | notes |
|---|---:|---:|---|
| baseline A | ... | ... | ... |
| candidate B | ... | ... | ... |

### Interpretation

局部 cold path 改善 X ms；repeated path 无显著变化。

### Correctness

列出相关 target tests 和输出语义检查。

### Limitations

synthetic fixture；不包含 UI、网络和模型 sampling。
```

一份好报告让 reviewer 知道结论边界，而不是只看到绿色百分比。

---

## 32. 怎样判断变化“显著”

这里的“显著”有两层，不能混为一谈。

### 32.1 统计上可区分

变化大于测量噪声，重复运行时方向稳定，置信区间或框架分析支持两个分布不同。

### 32.2 工程上有意义

即使统计上稳定，绝对改善只有 20 ns，且函数每个 turn 只调用一次，可能不值得增加复杂度。

反过来，p99 减少 200 ms 即使只影响少数路径，也可能显著改善交互体验。

决策还要考虑：

- 调用频率；
- 用户可见性；
- CPU/内存成本；
- 代码复杂度；
- 维护和安全风险；
- 跨平台一致性。

---

## 33. 常见错误结论

### 错误 1：“一次快了，所以优化有效”

一次样本无法区分代码变化和调度噪声。

### 错误 2：“平均值没变，所以没有回归”

p95/p99 可能严重退化。

### 错误 3：“micro 快 40%，产品就快 40%”

目标函数可能只占总路径很小比例。

### 错误 4：“benchmark 通过，所以行为正确”

Benchmark 的断言通常很弱，必须保留正确性测试。

### 错误 5：“smoke 通过，所以性能没问题”

Smoke 只证明能运行。

### 错误 6：“release 比 debug 快，所以补丁有效”

比较变量不只代码版本，实验无效。

### 错误 7：“缓存后非常快，所以方案可合并”

还需检查命中语义、失效、容量、隔离和敏感数据。

### 错误 8：“把 setup 排除后数字更漂亮，所以更准确”

是否排除取决于要回答的问题，而不是结果好坏。

---

## 34. 性能 PR 的 review 清单

### 问题与边界

- 是否有可复现的性能症状？
- 测量开始/结束边界是否明确？
- 优化目标是否对应用户或系统成本？

### 工作负载

- 输入是否具有代表性？
- 是否覆盖不同规模、格式和状态？
- 冷/热 cache 是否明确分开？
- setup 是否按问题边界正确处理？

### 实验

- 基线和候选 commit 是否固定？
- build mode 与机器环境是否一致？
- 样本是否足够？
- 是否报告分布和绝对值？
- 是否避免只挑最好结果？

### 实现风险

- 是否改变正确性、安全或公开契约？
- cache 是否有界且正确失效？
- 是否引入无界并发或内存？
- 是否在所有支持平台成立？
- 复杂度是否配得上收益？

### 验证

- 普通测试是否继续通过？
- Benchmark smoke 是否可运行？
- 完整 benchmark 是否重复得到相同方向？
- 必要时 macrobenchmark/telemetry 是否支持产品收益？

---

## 35. 本章贯穿案例的完整结论

对于“图片附件变慢”，合理结论应类似：

```text
生产/trace 证据把增加的耗时定位到 prompt 图片准备阶段。

Divan microbenchmark 使用固定 synthetic screenshot/photo，分别测量 PNG/JPEG、
小/大尺寸和 forced miss/repeated hit。输入 clone 位于 with_inputs，
不计入目标转换函数时间。

候选补丁改善了 large PNG fresh path；repeated path 没有显著变化。
普通图片正确性测试继续验证输出，macro measurement 显示请求准备总耗时也下降，
但总体百分比小于函数级改善，因为路径还包含其他工作。

结论不覆盖网络、模型 TTFT 或所有真实图像分布。
```

注意它没有说：

```text
Codex 整体快了 X%。
```

因为证据没有支持这么宽的结论。

---

## 36. 本章词汇表

完整总表见[课程术语表](glossary.md)。

| 名词 | 常见写法 | 通俗解释 |
|---|---|---|
| Performance regression | regression | 相同工作负载在新版本中更慢或更耗资源 |
| Benchmark | bench | 用固定工作负载重复测量性能的程序 |
| Microbenchmark | microbench | 隔离一个函数或小组件的基准测试 |
| Macrobenchmark | macrobench/e2e bench | 测完整进程或跨组件路径的基准测试 |
| Latency | latency | 一次操作从开始到某边界完成的时间 |
| Throughput | throughput | 单位时间内完成的工作量 |
| Wall-clock time | elapsed time | 现实钟表经过的总时间，含等待 |
| CPU time | CPU time | CPU 实际执行工作的时间 |
| Workload | workload | 被测输入、状态、规模、并发等条件集合 |
| Fixture | fixture | 可重复的固定或合成输入样例 |
| Warm-up | warmup | 正式采样前运行，让稳定路径准备好 |
| Cold path | cold/fresh | 首次初始化或缓存未命中的路径 |
| Hot path | warm/repeated | 已初始化、经常执行或缓存命中的路径 |
| Cache hit | hit | 缓存中已有对应结果 |
| Cache miss | miss | 缓存没有结果，需要完整计算 |
| Sample | sample | 一次或一组独立测量值 |
| Mean | average | 所有样本相加后除以数量 |
| Median | p50 | 排序后处在中间位置的样本 |
| Percentile | p95/p99 | 指定比例样本不超过的数值 |
| Tail latency | tail | 分布较慢末端的延迟 |
| Outlier | outlier | 明显偏离大多数样本的值 |
| Noise | variance/jitter | 与目标代码无关或不可控的测量波动 |
| Histogram | histogram | 按多个数值区间累计样本的分布结构 |
| Benchmark harness | harness | 负责运行、采样、计时和统计的框架 |
| Smoke run | smoke | 只确认 benchmark 可以启动和完成 |
| Synthetic fixture | synthetic input | 程序生成、近似真实数据的可重复样例 |
| Dead-code elimination | DCE | 编译器删除结果未使用的计算 |
| Contention | contention | 多个线程或 task 竞争同一资源 |
| Amdahl’s Law | Amdahl 定律 | 总体提升受被优化部分在总耗时中占比限制 |

### 性能代码单词短语拆解

- `bench`：benchmark 的缩写；基准测试。
- `regression`：回归、退化；新版本比基线更差。
- `baseline`：基线；用于比较的旧版本或原始实现。
- `candidate`：候选；准备验证的新版本或补丁。
- `elapsed`：已经过去的；常指 wall-clock 耗时。
- `duration`：持续时间；开始与结束之间的长度。
- `latency`：延迟；一次请求完成到指定边界要多久。
- `throughput`：吞吐量；固定时间完成多少工作。
- `workload`：工作负载；具体输入与运行条件。
- `sample`：样本；一次测量观察。
- `sample count`：样本数量；收集多少组观察。
- `sample size`：样本大小；一组样本中包含多少次迭代，具体语义以框架为准。
- `iteration`：迭代；重复执行被测操作的一轮。
- `fixture`：样例夹具；预先准备的稳定输入。
- `synthetic`：合成的；由程序构造而非真实用户数据。
- `representative`：有代表性的；保留真实工作负载的重要特征。
- `fresh`：新的；当前 cache 中没有相同内容。
- `repeated`：重复的；同一内容已经处理过。
- `cold`：冷的；尚未预热或缓存未命中。
- `warm`：热的；初始化完成或缓存可命中。
- `warm-up`：预热；正式计时前先运行。
- `cache hit`：缓存命中；直接复用已有结果。
- `cache miss`：缓存未命中；必须重新计算。
- `digest`：摘要；由内容计算的稳定短标识。
- `variant`：变体；主体相近但身份不同的输入版本。
- `input setup`：输入准备；构造或复制 benchmark 参数。
- `measurement`：测量区间；真正计入结果的代码范围。
- `harness`：运行框架；负责重复、计时与统计。
- `mean`：均值；所有样本的算术平均。
- `median`：中位数；排序后位于中间的值。
- `percentile`：百分位数；一定比例样本不超过的值。
- `p50`：第 50 百分位，通常等同中位数。
- `p95`：第 95 百分位，观察较慢的尾部。
- `p99`：第 99 百分位，观察更极端的尾部。
- `tail`：尾部；延迟分布中最慢的一段。
- `outlier`：离群值；偏离多数样本的观察。
- `noise`：噪声；不来自目标变化的波动。
- `jitter`：抖动；相邻测量的不稳定变化。
- `variance`：方差/变异程度；描述样本分散程度。
- `histogram`：直方图；把样本归入多个 bucket。
- `bucket`：桶；一个数值范围及其样本计数。
- `profile`：性能剖析；定位 CPU、内存或阶段耗时。
- `hotspot`：热点；消耗资源最多的代码位置。
- `hot path`：热路径；频繁执行或主要耗时路径。
- `critical path`：关键路径；决定端到端完成时间的依赖链。
- `contention`：争用；多个执行者竞争锁或资源。
- `allocation`：内存分配；申请新的内存块。
- `peak RSS`：峰值常驻内存；进程实际驻留物理内存的高点近似。
- `black box`：黑盒；阻止编译器假定值无关并删除计算的机制。
- `dead-code elimination`：死代码消除；编译器移除无可观察结果的工作。
- `smoke`：冒烟检查；只验证最基本路径能运行。
- `optimized build`：优化构建；启用编译器优化的产物。
- `end-to-end`：端到端；覆盖用户入口到结果边界的完整路径。

---

## 37. 自测题

1. 为什么“用户感觉慢”还不是性能根因？
2. Latency 和 throughput 有什么区别？
3. Wall-clock time 与 CPU time 为什么可能不同？
4. TTFT 和 TTFM 分别观察什么边界？
5. Microbenchmark、macrobenchmark 和 production telemetry 各能证明什么？
6. 为什么 `just bench-smoke` 不能证明没有性能回归？
7. 图片 benchmark 为什么分 fresh 和 repeated？
8. 48 个 cache-miss variant 解决了什么测量问题？
9. `with_inputs` 为什么排除 clone 时间？
10. 什么时候 clone 又应该计入端到端成本？
11. Synthetic fixture 有哪些优点和局限？
12. Mean 为什么可能掩盖典型值或尾延迟？
13. p95 的通俗含义是什么？
14. 为什么小样本不适合声称精确 p99？
15. Outlier 为什么不能自动删除？
16. Warm-up 会隐藏哪类用户体验？
17. `codex --help` benchmark 包含哪些成本？
18. 为什么 benchmark 内仍要断言进程成功？
19. Micro 改善 45% 为什么端到端可能只改善 12%？
20. 比较两个版本时至少要固定哪些变量？
21. 为什么不能只挑十次中最快的一次？
22. 普通测试中写 50 ms 硬阈值为什么容易 flaky？
23. 编译器怎样让一个错误 benchmark 看起来极快？
24. Async benchmark 如果没有 await 会发生什么？
25. 测锁性能时为什么要区分 contended/uncontended？
26. 把 bounded channel 改成 unbounded 为什么不一定是有效优化？
27. 新增 cache 前必须回答哪些生命周期和容量问题？
28. Histogram 的 count、sum、bucket 分别提供什么信息？
29. 统计上可区分与工程上有意义有什么区别？
30. 一份性能报告为什么必须写 limitations？

---

## 38. 源码检查点

1. 根目录 `AGENTS.md`
   - 搜索 `### Benchmarks`，确认 Divan、`just bench` 与 smoke 规则。
2. 根目录 `Justfile`
   - 查看 `bench`、`bench-smoke`、`bench-e2e`、`bench-e2e-smoke`。
3. `codex-rs/Cargo.toml`
   - 搜索 workspace `divan` 依赖版本。
4. `codex-rs/utils/image/Cargo.toml`
   - 查看 `[[bench]]`、`harness = false` 和 dev dependency。
5. `codex-rs/utils/image/benches/prompt_images.rs`
   - 查看四个 workload、`with_inputs`、cache-miss variants 和 synthetic fixture。
6. `codex-rs/utils/image/src/`
   - 追踪 `load_for_prompt_bytes` 与实际 cache/resize/encode 路径。
7. `codex-rs/cli/e2e_benches/codex_help.rs`
   - 看进程级 benchmark 如何查找 binary、执行和验证 status。
8. `codex-rs/cli/BUILD.bazel`
   - 查看 `codex_e2e_benchmark(name = "codex-help", ...)`。
9. `bazel/rules/e2e_benchmark.bzl`
   - 查看 Bazel-only Divan benchmark 怎样生成 binary、runfiles env 和 test wrapper。
10. `codex-rs/BUILD.bazel`
    - 查看 `e2e-benchmarks` suite 怎样聚合目标。
11. `codex-rs/core/src/turn_timing.rs`
    - 看 TTFT、TTFM、sampling、compaction、tool blocking 和 RAII guard。
12. `codex-rs/analytics/src/facts.rs`
    - 查 `TurnProfile` 的持久/分析数据形状。
13. `codex-rs/otel/src/metrics/names.rs`
    - 查 turn TTFT/TTFM 等 metric 名称。
14. `codex-rs/otel/src/events/session_telemetry.rs`
    - 看 duration/histogram 怎样记录及失败时怎样处理。
15. `codex-rs/otel/tests/suite/timing.rs`
    - 看 bucket、count、sum、unit、description 和 attributes 的正确性测试。

---

## 39. 一句话总结

性能优化不是看到一个耗时数字就开始改代码，而是先定义用户可见边界和代表性工作负载，用 telemetry、trace 或 profile 把问题定位到具体阶段，再用区分冷/热状态、排除无关 setup、具有足够样本的 benchmark 稳定复现；修改后同时验证正确性、局部分布、端到端绝对收益和实现风险，并把环境、命令、限制写清楚，使“更快”成为别人能够复现的工程事实，而不是一次幸运运行的感觉。
