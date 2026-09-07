# 测试体系、Fixtures、Snapshots 与兼容性验证：从纯函数到 PTY、协议和压力测试

> 本篇不把“测试”简化成 `cargo test`。Grok Build 同时包含协议库、异步 Actor、文件与进程后端、TUI、ACP/MCP、更新器和历史兼容层，不同风险需要不同证据。

## 1. 先给结论

1. 仓库没有一种测试能证明全部正确性，而是使用单元测试、crate 集成测试、wire round-trip、snapshot、mock server、built-binary E2E、PTY E2E、soak/stress 和平台专属测试组成证据梯度。
2. `src` 内 `#[cfg(test)]` 适合验证私有纯函数和状态机；`tests/` 目录从 crate 的公开边界验证组合行为。
3. Mock 能精确注入错误、时间和消息，但不能证明 OS、PTY、symlink、进程树或真实网络行为。
4. Snapshot 适合复杂视觉输出，精确 JSON/字符串断言适合协议和行为合同；二者都不是“发现真理”，更新基线前必须人工解释差异。
5. 大量 `legacy-0.4.10` 与 “exact historical message” 测试把历史模型可见行为当成兼容合同。
6. 异步测试最重要的不只是结果，还包括顺序、唯一终态、取消、channel 关闭、资源释放和“不发生某事”。
7. `#[ignore]` 往往表示需要预构建二进制、真实 PTY、Linux cgroup、剪贴板或长时间压力环境，不表示测试不重要。
8. 修改范围越靠近进程、协议或 TUI，越不能只跑一个函数过滤；但日常也不应默认先跑 81-crate 全 workspace。
9. 固定 Rust 1.94.0、rustfmt 和 clippy 是回归控制的一部分；工具链升级本身需要全 targets check/clippy。
10. 可靠验证报告必须说明“运行了什么、没运行什么、为什么没运行”，不能只写“测试通过”。

## 2. 测试金字塔不够，使用“证据梯度”

传统金字塔只按数量区分 unit/integration/E2E，但本仓库还需要区分环境真实性：

```text
纯函数与类型不变量
  → Actor/异步组件（fake channel、fake clock/backend）
  → crate 公开边界集成
  → 协议 wire/序列化往返
  → 子进程与 mock HTTP/MCP/ACP server
  → built binary
  → PTY/真实终端/平台能力
  → soak、stress、资源泄漏和竞态
```

越往下越接近真实系统，运行成本和不稳定因素也越多。正确策略是让每个 bug 在最低但足够真实的一层获得回归测试。

## 3. 源码地图

```text
每个 crate/src/**/*.rs
└── #[cfg(test)] mod tests           # 私有逻辑单元测试

每个 crate/tests/*.rs               # crate 集成测试
├── xai-tool-runtime/tests           # trait、动态分派、streaming
├── xai-grok-tools/tests             # 路径提示、soak、cgroup
├── xai-grok-shell/tests             # session、ACP、MCP、binary E2E
├── xai-grok-pager/tests             # PTY、配置、脚本场景
├── xai-grok-update/tests            # 网络、子进程、并发收敛
└── xai-grok-sandbox/tests           # 真正隔离边界

xai-grok-pager/**/snapshots/*.snap   # insta 视觉快照
xai-grok-shell/tests/fixtures/*.json # 合成 trace/session 输入
```

根 `Cargo.toml` 统一版本依赖，包括 `insta`、`serial_test`、`tempfile` 和 `wiremock`。`rust-toolchain.toml` 固定 1.94.0，并安装 rustfmt/clippy。

## 4. `src` 内单元测试

单元测试与实现同模块编译，能访问私有函数。适合：

- 路径清理边界；
- timeout 计算；
- Bash `&` 词法识别；
- reducer/state machine transition；
- 序列化 helper；
- 截断的 UTF-8 边界；
- 历史错误文案。

优势是快、定位精确；局限是容易只证明 helper，而漏掉注册、Resources 注入、权限或协议适配。

一个实用模式是“纯算法 + 薄 I/O 壳”：先把重试、匹配、状态转换写成可注入 closure/future 的 helper，再用确定性单测覆盖所有分支。本地文件写重试的 `write_file_with_retry_hooks` 就能在不制造真实 Windows 锁的情况下验证 delay 和次数。

## 5. `tests/` 集成测试

Cargo 会把 `crate/tests/foo.rs` 编译成独立测试 crate。它通常只能使用公开 API，因此能发现：

- 类型没有公开；
- feature 没启用；
- 模块重导出遗漏；
- 真实构建边界和内部单测不同；
- 多个公开组件组合后契约破裂。

例如 `xai-tool-runtime/tests/tool_streaming.rs` 定义最小工具并通过公开 `Tool` API 验证：

```text
Progress* → 唯一 Terminal
```

成功与失败终态都被覆盖，空 progress 也必须产生 Terminal。

`xai-grok-tools/tests/path_suggestions_production.rs` 则用数据驱动的临时目录布局覆盖模型常见错误，而不是只测一个内部 matcher。

## 6. Fixtures：固定输入，不是固定答案

Fixture 是预先准备的输入或环境，例如：

- 合成 JSON trace；
- 历史 session snapshot；
- 临时 Git repo；
- mock server 响应；
- fake MCP/ACP 消息；
- 文件树与权限；
- 预构建 binary 路径。

Fixture 的价值是可重复。风险是它可能只反映构造者想象的世界。

好的 fixture 应说明：

- 它模拟哪个真实事件；
- 哪些字段故意省略以模拟旧版本；
- 哪些 ID、时间和路径需要归一化；
- 失败时哪个差异有业务意义；
- 是否包含敏感或版权数据。

测试中动态生成的 `TempDir` 也是 fixture，只是生命周期由测试拥有。

## 7. Snapshot 测试

Pager 使用 `insta` 保存 `.snap`，尤其用于 diff 卡片和状态块。Snapshot 适合：

- 多行布局；
- ANSI/样式投影后的稳定文本；
- diff hunk 合并；
- 很难用几十个局部 assert 表达的整体结构。

### 7.1 Snapshot 的正确审查方式

失败后不要直接接受新快照。先分类：

1. 预期产品变化；
2. 无关噪声，如路径、时间、随机 ID；
3. 真正回归；
4. 测试环境变化；
5. 快照范围过大导致的连带变化。

只有第一类应更新基线；第二类应先做 normalization。

### 7.2 内联 JSON snapshot

工具 registry 的 `non_pi_finalized_contract_snapshot_is_unchanged` 没使用 `.snap` 文件，而是在测试中保存完整 JSON Schema/description 期望值。这仍是 snapshot 思路：把模型看到的合同整体锁定。

这种测试对工具描述、required 字段和默认值非常敏感，正是目的所在。

## 8. 精确历史合同测试

源码中常见：

```text
legacy-0.4.10
exact historical message
exact historical invalid input
```

它们保护的不只是“文案好看”，而是：

- 老客户端或模型提示依赖的输出；
- replay fixture；
- benchmark/harness 对行为的比较；
- 旧 session 恢复后的确定性；
- provider 兼容模式。

修改这类断言前应先判断：

```text
这是当前行为改进？
还是无意中破坏 legacy behavior version？
```

常见正确做法是保留 legacy 分支，为 current 增加新结果，而不是统一删除历史字符串。

## 9. Wire 与 Serde 往返测试

协议类型测试通常验证：

```text
Rust value → JSON/JSON-RPC → Rust value
```

重点包括：

- 字段名与 rename；
- tagged enum shape；
- 缺省字段；
- 未知字段策略；
- ID 校验；
- Error envelope；
- 新字段能否被旧 snapshot 缺省恢复。

仅做 round-trip 有盲点：如果 encode 和 decode 同时犯同一个错误，往返仍成功。因此还需要固定 wire JSON 断言和跨实现 fixture。

`xai-grok-workspace-types/tests/wire_round_trip.rs`、`xai-tool-protocol/tests/serde_roundtrip.rs` 和 ACP setup wire 测试承担不同协议边界。

## 10. Mock、Fake、Stub 与真实后端

| 类型 | 白话用途 | 适合证明 | 不适合证明 |
| --- | --- | --- | --- |
| Stub | 返回固定值 | 某分支能继续 | 交互正确性 |
| Fake | 简化但可工作的实现 | 状态流、读写组合 | OS 细节 |
| Mock | 记录并校验调用 | 次数、顺序、参数 | 真实兼容性 |
| Mock server | 监听本地端口模拟协议 | HTTP/wire 行为 | 公网、TLS 全环境 |
| Real backend | 使用真正 OS/进程/PTY | 系统边界 | 所有平台普遍性 |

例如内存 MockFs 能证明 `search_replace` 在 NotFound 后不写入，却不能证明 Windows sharing violation 的处理；mock Terminal 能证明 timeout 参数传递，却不能证明 SIGTERM 是否杀死真实孙进程。

## 11. 时间与异步测试

异步系统最常见的脆弱写法是：

```rust
sleep(Duration::from_millis(100)).await;
assert!(done);
```

它把机器负载变成正确性条件。更稳的模式包括：

- oneshot/channel 明确通知状态到达；
- `tokio::time::timeout` 给等待设上限；
- fake clock 或暂停 Tokio 时间；
- barrier 控制两个任务的交错点；
- 查询 actor snapshot，而不是猜调度时间；
- 断言 channel close 和 task join。

必须测试的不变量通常是：

- 消息顺序；
- Terminal 唯一且最后；
- 取消后不再产生副作用；
- sender drop 后 waiter 退出；
- shutdown 等待拥有的任务；
- timeout 不吞掉已产生的输出。

## 12. `serial_test` 为什么存在

Rust 默认并行运行测试。以下状态会相互污染：

- 环境变量；
- process-wide singleton；
- 固定端口；
- 用户目录或模拟 `$HOME`；
- 安装器目录；
- 全局 crypto/provider 初始化；
- 剪贴板和真实终端。

`xai-grok-update` 的 downgrade/subprocess 测试大量使用 `#[serial]`，代码注释明确指出它们会修改环境变量。MCP permission persistence 等测试也会串行保护共享目录。

串行不是修复所有竞态的万能药。优先把状态隔离进 TempDir/依赖对象；只有确实属于进程全局的状态才串行。

## 13. HTTP、MCP 与 ACP 测试

`wiremock` 或专用 mock server 可以控制：

- 状态码；
- headers；
- chunked/streaming 响应；
- 延迟与断连；
- 重试次数；
- 认证过期；
- 请求 body 与 query。

本地 server 比 mock client 更接近真实 HTTP stack，能发现 header 编码、URL 拼接和流读取问题；但通常仍不覆盖公网 DNS、企业代理、真实证书链和 CDN。

ACP harness 则需要验证请求/响应之外的 session ID 路由、客户端能力、异步通知和重连。

## 14. Built-binary E2E

一部分测试需要真正编译出的可执行文件，因为 `cargo test` 进程无法证明：

- CLI 参数解析；
- 环境变量启动行为；
- signal/exit code；
- 子进程树；
- panic profile；
- 动态资源查找；
- 终端恢复。

这些测试常标 `#[ignore = "requires pre-built binary"]`，并由环境变量传入 binary 路径。直接运行 ignored 测试前，应按该文件顶部说明先构建正确 profile。

## 15. PTY E2E

Pager 的交互行为依赖 pseudo-terminal，而不是普通 stdin/stdout pipe。PTY 测试能覆盖：

- raw mode；
- resize；
- ANSI escape；
- bracketed paste；
- mouse reporting；
- Ctrl-C/Ctrl-L；
- terminal restore；
- 流式渲染与 scrollback；
- 多客户端 leader attach。

这些测试慢且平台敏感，因此许多默认 ignored。失败时要保留 terminal transcript、尺寸、TERM、平台和 binary 版本，否则截图式“失败了”很难诊断。

Snapshot 证明 renderer 给定输入的输出；PTY E2E 证明真实事件、终端模式和 renderer 组合后仍工作。二者互补。

## 16. 平台专属测试

仓库显式区分：

- Linux cgroup v2；
- Windows clipboard 与 sharing violation；
- macOS 路径/终端差异；
- WSL 慢文件系统；
- Unix signal/process group；
- Windows Job Object。

`#[cfg]` 只在对应平台编译代码，不能在 macOS 上证明 Windows 分支。跨平台修改至少需要：

1. 当前平台单测；
2. `cargo check --target` 检查可编译性；
3. 对应平台 runner 上的真实测试；
4. 必要时平台 fake 的纯逻辑测试。

## 17. Soak、Stress 与有界 Fuzz

长会话和高频取消的 bug 往往需要重复才能出现。仓库包含：

- Subagent soak；
- leader soak；
- 内存/进程资源测试；
- pager scroll/resize stress scenario；
- updater cancellation bounded fuzz 与 ignored 100k stress。

好的 stress 测试应提供：

- 固定 seed 以复现；
- 默认有界小规模版本；
- ignored 长规模版本；
- 循环次数环境变量；
- 超时；
- 失败时打印 seed、iteration 和状态；
- 资源基线与允许增长范围。

只“循环一百万次”但不保存 seed 和故障上下文，价值很低。

## 18. 测试隔离与清理

### 18.1 TempDir

`tempfile::TempDir` 在 guard drop 时清理目录，适合 Git repo、session 和文件工具测试。不要返回只借用其内部路径而提前 drop guard。

### 18.2 环境变量

环境变量是 process global。测试必须保存旧值并恢复，或使用串行 guard。panic 时也要通过 RAII 恢复。

### 18.3 子进程

测试失败/timeout 仍要 kill 并 wait，避免 zombie、端口占用和下个测试污染。

### 18.4 Channel 与 Actor

drop sender、调用 shutdown、await join handle。仅让 runtime 结束并不证明生产 shutdown 正确。

## 19. 失败路径矩阵

每个核心功能至少考虑：

| 维度 | 示例 |
| --- | --- |
| 正常 | 请求成功、顺序正确 |
| 输入 | 缺字段、未知 variant、超限 |
| 权限 | policy deny、用户拒绝、sandbox deny |
| I/O | NotFound、PermissionDenied、disk full |
| 时间 | timeout 前/后边界、假唤醒 |
| 取消 | 等待中取消、写入中取消 |
| 通道 | sender/receiver 提前关闭 |
| 恢复 | restart、snapshot 缺旧字段 |
| 兼容 | current 与 legacy 分支 |
| 并发 | 同资源竞争、多个 session 隔离 |
| 输出 | 截断、UTF-8、多模态、空输出 |

单个 happy-path test 只能覆盖表中一格。

## 20. 如何为一次修改选测试

### 修改纯 parser

- 表驱动 unit tests；
- malformed input；
- Unicode/边界；
- round-trip 或固定 wire；
- fuzz（若输入空间大）。

### 修改 Tool

- helper unit tests；
- `Tool::run` typed test；
- registry/ToolDyn dispatch；
- schema/description contract snapshot；
- permission decision；
- notification 与 streaming 顺序；
- Local/ACP 能力差异。

### 修改 Actor

- 状态机 unit；
- channel integration；
- cancel/shutdown；
- owner isolation；
- soak/leak test。

### 修改 TUI

- reducer unit；
- render snapshot；
- headless dispatch；
- 目标 PTY E2E；
- resize/terminal restore 回归。

### 修改发布/更新器

- 版本矩阵；
- mock artifact server；
- 并发收敛；
- 取消点 fuzz；
- built installer subprocess；
- 平台安装目录权限。

## 21. 推荐日常验证阶梯

```text
1. cargo fmt --check
2. cargo check -p <直接 crate>
3. cargo test -p <直接 crate> <目标过滤>
4. cargo test -p <直接 crate> --lib / --test <integration>
5. cargo clippy -p <直接 crate> --all-targets
6. 相邻协议/调用方 crate 测试
7. 需要时 built-binary / PTY / ignored / 平台测试
8. 高风险合并前 workspace all-targets
```

根 `rust-toolchain.toml` 对工具链升级要求 `cargo check --all-targets --workspace` 与 `cargo clippy --all-targets --workspace`。这是升级验证，不代表每次小改都要以最慢步骤开场。

## 22. 如何阅读失败

测试失败先分类，而不是立即改断言：

- 编译失败：API/feature/cfg 边界；
- assertion：行为差异；
- timeout：死锁、负载、真实慢操作或错误预算；
- snapshot diff：产品变化或未归一化噪声；
- only-in-suite：共享全局状态；
- only-on-CI：平台、资源、并行度、环境；
- flaky：未控制的时间/竞态/外部依赖；
- ignored 未运行：验证覆盖缺口，不是 pass。

先用同一 seed、同一过滤重复；再用 `--nocapture`、tracing 或保留 TempDir artifact 获取证据。不要用盲目增加 sleep 掩盖竞态。

## 23. 测试报告应该怎么写

一个可审查的报告至少包含：

```text
运行：cargo test -p xai-grok-tools --lib read_file
结果：196 passed, 0 failed, 2709 filtered out
未覆盖：ACP 真实客户端、Windows、ignored PTY
原因：本次修改仅涉及本地 read_file 文本路径
```

`filtered out` 很重要：它说明通过的是目标子集，不是整个 crate。`#[ignore]` 也必须单独说明。

## 24. 常见误解

### “单测全过，所以功能完成”

若修改涉及注册、协议、进程或 UI，单元测试只证明一层。

### “Snapshot 变化就更新 snapshot”

Snapshot 是审查界面，不是自动批准机制。

### “Mock server 等于网络 E2E”

它验证本地协议栈，不覆盖真实代理、DNS、TLS/CDN。

### “Ignored 测试等于废弃测试”

通常只是依赖昂贵或特殊环境，需要显式调度。

### “多加 sleep 可以修 flaky”

它通常扩大概率窗口；应改为事件同步和有界等待。

### “Round-trip 成功就兼容”

同一实现的 encode/decode 可能共同偏离外部 wire；还需要固定 JSON 或跨实现测试。

## 25. 本篇术语表

| 名词 | 白话解释 | 在本篇中的具体含义 |
| --- | --- | --- |
| unit test | 验证小块逻辑的测试 | 常在 `src` 的 `#[cfg(test)]` 中 |
| integration test | 从公开边界组合多个组件 | Cargo `tests/` 下独立测试 crate |
| E2E | 从真实入口走到外部可见结果 | built binary、PTY、sandbox 等 |
| evidence gradient | 从便宜抽象到真实环境的证据层级 | 本篇替代单纯测试金字塔的模型 |
| fixture | 预先准备的输入或环境 | JSON、文件树、session、server 响应 |
| snapshot test | 把复杂输出与已保存基线比较 | Pager `.snap` 或内联合同 JSON |
| golden | 被接受的标准输出 | 需人工审查，不天然正确 |
| normalization | 删除随机/环境噪声 | 稳定 path、time、ID 后再比较 |
| stub | 只返回固定结果的替身 | 推动被测分支 |
| fake | 简化但能工作的替代实现 | MockFs、内存 backend |
| mock | 记录并断言交互的替身 | 校验调用参数、次数和顺序 |
| mock server | 本地模拟网络服务 | wire、header、错误和延迟测试 |
| test double | 所有测试替身的总称 | stub/fake/mock |
| round-trip | 编码后再解码 | 验证 serde 基本对称性 |
| wire shape | 实际传输 JSON 的字段结构 | 不能只由 Rust 类型名推断 |
| regression | 已经正确的行为再次损坏 | 用最小足够真实层建立回归测试 |
| flaky test | 相同代码偶发通过或失败 | 多由时间、竞态或外部环境导致 |
| deterministic | 同输入总得到同结果 | 固定 seed、事件同步、受控时钟 |
| serial test | 不与同组测试并行 | 保护进程全局环境或固定资源 |
| TempDir | 自动清理的临时目录 guard | 测试文件与 Git/session fixture |
| RAII cleanup | 对象销毁时恢复资源 | 环境变量、临时目录、锁 |
| barrier | 让多个任务在指定点会合 | 精确构造竞态交错 |
| fake clock | 可由测试控制的时间源 | 避免真实 sleep |
| PTY | pseudo-terminal，伪终端 | 验证真实 TUI/terminal 行为 |
| raw mode | 终端按键不经普通行缓冲 | Pager 必须正确启用与恢复 |
| built-binary test | 启动真实编译产物 | 验证 CLI、signal、资源定位 |
| `#[ignore]` | 默认 test run 跳过 | 需要显式 `--ignored` 和环境 |
| soak test | 长时间重复运行观察泄漏 | Subagent/leader 生命周期 |
| stress test | 用高负载放大竞态 | scroll、resize、取消等 |
| fuzz | 自动生成大量输入探索异常 | updater 使用固定 seed 有界随机循环 |
| seed | 随机序列的起点 | 保存后可复现失败 iteration |
| filtered out | 因 test name filter 未运行 | 不能计入通过覆盖 |
| code coverage | 哪些代码被测试执行 | 高覆盖率不等于断言质量 |
| compatibility fixture | 保存旧版本输入/输出 | legacy session、历史错误合同 |
| behavior version | 选择历史行为的版本 | current 与 legacy 测试分开 |
| panic profile | panic 时 abort 或 unwind | dev/release 与 x-prod 设置不同 |

更多通用名词见[全局术语表](../appendices/glossary.md)。

## 26. 自测题

1. 为什么本篇使用“证据梯度”而不只用测试金字塔？
2. `src` unit 与 `tests/` integration 的 Rust 可见性差异是什么？
3. Fixture 与 snapshot 有什么区别？
4. 为什么更新 snapshot 前必须先归一化噪声？
5. Round-trip 为什么可能产生假安全感？
6. exact historical message 测试保护的是什么？
7. MockFs 能证明和不能证明什么？
8. 异步测试为什么应优先 channel/barrier 而非 sleep？
9. 哪些状态需要 `serial_test`？
10. 为什么 ignored 测试不能算通过？
11. PTY 测试比 renderer snapshot 多验证什么？
12. built-binary 测试能发现哪些 `cargo test` 内测试发现不了的问题？
13. 平台 cfg 编译成功为什么仍不等于平台行为正确？
14. stress 测试为何需要固定 seed？
15. filtered out 数量为什么应出现在验证报告里？
16. 一个 Tool 改动通常需要覆盖哪几层？
17. 怎么区分 flaky 与稳定的真实回归？
18. 为什么随意增加 timeout/sleep 可能掩盖问题？

## 27. 源码证据索引

| 主题 | 源码入口 |
| --- | --- |
| Tool streaming 公开合同 | `crates/common/xai-tool-runtime/tests/tool_streaming.rs` |
| 工具 contract 内联 snapshot | `crates/codegen/xai-grok-tools/src/registry/types.rs` 中 `non_pi_finalized_contract_snapshot_is_unchanged` |
| 数据驱动路径 fixture | `crates/codegen/xai-grok-tools/tests/path_suggestions_production.rs` |
| Pager insta snapshots | `crates/codegen/xai-grok-pager/src/**/snapshots/*.snap` |
| Shell 合成 JSON fixture | `crates/codegen/xai-grok-shell/tests/fixtures/` |
| ACP/MCP/session 集成 | `crates/codegen/xai-grok-shell/tests/` |
| PTY E2E | `crates/codegen/xai-grok-pager/tests/pty_e2e/` |
| PTY harness stress | `crates/codegen/xai-grok-pager-pty-harness/src/scenarios/` |
| Updater network/subprocess/convergence | `crates/codegen/xai-grok-update/tests/` |
| 有界与长取消 fuzz | `xai-grok-update/tests/test_blitz_cancel.rs` |
| Subagent soak | `xai-grok-tools/tests/test_subagent_soak.rs` |
| Linux cgroup ignored 测试 | `xai-grok-tools/tests/cgroup_memory_test.rs` |
| Sandbox E2E | `crates/codegen/xai-grok-sandbox/tests/` |
| 固定工具链与升级验证说明 | `rust-toolchain.toml` |
| 测试依赖版本 | 根 `Cargo.toml` workspace dependencies |

## 28. 最小验证命令

```sh
# 列出一个 crate 的测试，先确认真实过滤名
cargo test -p xai-tool-runtime -- --list

# 运行公开 streaming 合同集成测试
cargo test -p xai-tool-runtime --test tool_streaming

# 运行工具 contract snapshot
cargo test -p xai-grok-tools --lib non_pi_finalized_contract_snapshot_is_unchanged

# 运行数据驱动路径测试
cargo test -p xai-grok-tools --test path_suggestions_production

# 查看 ignored 覆盖，不执行
cargo test -p xai-grok-tools -- --list --ignored

# 格式和 lint；大改后再扩大到 workspace
cargo fmt --check
cargo clippy -p xai-grok-tools --all-targets
```

运行 built-binary、PTY、clipboard、cgroup 或 stress 测试前，先读对应测试文件顶部的环境和构建说明。
