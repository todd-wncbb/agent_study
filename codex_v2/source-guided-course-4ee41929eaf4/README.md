# Codex 源码伴读课程

> 源码基线：`4ee41929eaf4`（2026-08-07 检查）

这不是一套按文件逐项翻译的 API 文档，而是一条以“能够独立读懂、验证和修改 Codex”为目标的学习路线。文中的“当前实现”只指上面的提交；源码变化后，应优先搜索符号名，再修正文档。

## 你将建立的心智模型

学完第一阶段后，你应该能回答：

1. `codex` 进程如何选择 TUI、`exec`、app-server 等运行表面？
2. 当前 TUI 为什么先连接 app-server，而不是直接操作 `codex-core`？
3. `thread`、`Session`、`turn`、`step`、一次 sampling request 分别活多久？
4. 用户输入怎样变成模型请求，tool call 又怎样进入下一次模型请求？
5. 哪些状态属于模型上下文，哪些状态属于持久化记录，哪些只是 UI 投影？
6. 修改一条主链路时，应当在哪一层写测试、观察什么证据？

## 推荐阅读顺序

| 顺序 | 文档 | 阅读产出 |
|---|---|---|
| 随时查询 | [Codex 源码术语总表](glossary.md) | 查询英文代码词、直译和在 Codex 中的实际含义 |
| 专项精读 | [Codex 源码精读系列](close-reading/README.md) | 选择关键函数，逐块解释变量、顺序、失败边界和测试证据 |
| 0 | [先认识术语与贯穿案例](00-concepts-and-running-example.md) | 不看源码也能理解后续常用概念 |
| 0.5 | [学习和验证方法](00-study-method.md) | 学会区分事实、推断和简化模型 |
| 1 | [当前系统地图](01-current-system-map.md) | 建立 UI、app-server、core、模型、工具之间的边界 |
| 2 | [生命周期与状态所有权](02-thread-turn-step.md) | 分清 thread、turn、step 和 request |
| 3 | [一次用户请求的源码走读](03-one-turn-walkthrough.md) | 沿真实调用链完成第一次源码阅读 |
| 4 | [工具系统与安全边界](04-tools-and-security.md) | 理解“模型建议动作，runtime 决定执行” |
| 5 | [上下文、持久化与恢复](05-context-state-resume.md) | 分清 prompt history、rollout 和 thread store |
| 6 | [测试与调试手册](06-testing-debugging.md) | 用测试和事件验证理解 |
| 7 | [渐进式练习](07-exercises.md) | 从搜索符号进阶到设计小改动 |
| 8 | [Skills、Plugins、MCP 与 Apps](08-capability-stack.md) | 理解说明、能力包、连接和工具之间的分工 |
| 9 | [App-server v2 边界](09-app-server-v2.md) | 沿公开 API 追踪 thread 与 turn |
| 10 | [Code Mode](10-code-mode.md) | 理解持久 JS cell 如何组合普通工具 |
| 11 | [多 Agent](11-multi-agent.md) | 理解“一个 Agent 就是一条 thread”的协作模型 |
| 12 | [执行环境、审批与 Sandbox](12-execution-environments.md) | 分清动作决策、执行位置和 OS 强制边界 |
| 13 | [模型传输、增量请求与重试](13-model-transport.md) | 理解 HTTP/WebSocket、sticky state 和缓存条件 |
| 14 | [第二阶段综合实验](14-advanced-labs.md) | 用真实测试完成跨层验证 |
| 15 | [配置系统与 Feature Flag](15-configuration-and-features.md) | 理解配置来源、强制要求和能力开关怎样生效 |
| 16 | [Hooks 与 Extensions](16-hooks-and-extensions.md) | 分清外部生命周期检查与进程内扩展接口 |
| 17 | [长期 Memory](17-long-term-memory.md) | 理解跨 thread 记忆的提取、整合和按需读取 |
| 18 | [模型、Provider 与认证](18-models-providers-and-auth.md) | 分清模型目录、连接配置和凭据管理 |
| 19 | [Token 预算与性能](19-token-budget-and-performance.md) | 用窗口状态和分阶段耗时定位容量与延迟问题 |
| 20 | [端到端数据实例](20-end-to-end-data-example.md) | 看懂同一任务在 API、Core、模型、工具和 UI 中的数据形状 |
| 21 | [Prompt 与指令层级](21-prompt-and-instruction-priority.md) | 分清角色、来源、可信度和 Prompt Injection 防线 |
| 22 | [错误、取消与恢复](22-errors-cancellation-and-recovery.md) | 按错误层级选择返回模型、重试、压缩或终止 |
| 23 | [事件、Telemetry 与可观测性](23-events-telemetry-and-observability.md) | 用 Event、Trace、Metric 和 ID 定位任务卡点 |
| 24 | [引导式源码修改实验](24-guided-source-modification-labs.md) | 学会评估 Tool、API、Feature、Hook 和通知修改的影响面 |
| 25 | [从零理解一个最小 Agent](25-minimal-agent-from-scratch.md) | 从核心循环推导 Step、安全、恢复和扩展架构 |
| 26 | [Codex 源码中的常用 Rust 模式](26-rust-patterns-used-by-codex.md) | 看懂所有权、`Arc`、trait、Future、生命周期和 RAII 等高频写法 |
| 27 | [异步任务、Channel 与取消](27-async-tasks-channels-and-cancellation.md) | 理解 task 通信、背压，以及一次“停止生成”的完整收尾过程 |
| 28 | [TUI 与客户端状态管理](28-tui-and-client-state.md) | 理解事件路由、thread 切换、流式渲染和最终内容对账 |
| 29 | [Rollout、Thread Store 与状态数据库](29-rollout-thread-store-and-state-db.md) | 分清耐久历史、查询索引、运行对象，以及恢复和 Fork 的数据来源 |
| 30 | [MCP 生命周期](30-mcp-lifecycle.md) | 从配置、连接和工具发现追踪到审批、执行、结果回传与刷新 |
| 31 | [Prompt Injection 与分层安全防线](31-prompt-injection-and-defense-in-depth.md) | 沿恶意工单攻击链理解授权、审批、Guardian、Sandbox 与网络/MCP 防线 |
| 32 | [系统化调试](32-systematic-debugging.md) | 用配对事件、稳定 ID 和分层决策树定位“任务卡住”的真正边界 |
| 33 | [从故障定位到最小修复](33-from-diagnosis-to-minimal-fix.md) | 把证据变成假设、不变量、最小补丁和能防止复发的回归测试 |
| 34 | [从最小修复到可审查 PR](34-from-fix-to-reviewable-pr.md) | 用原子提交、PR 证据链、兼容性分析、CI 和 review 迭代推进安全合并 |
| 35 | [代码审查实战](35-code-review-practice.md) | 在教学 Diff 中识别上下文、并发、兼容性、测试和改动规模问题，并写出可执行 finding |
| 36 | [性能回归与基准测试实战](36-performance-regression-and-benchmarks.md) | 区分延迟、吞吐量、冷/热路径与噪声，用微基准、宏基准和 telemetry 证明真实性能变化 |
| 37 | [性能剖析实战](37-performance-profiling-practice.md) | 从 p95 与 Turn profile 进入 Trace、火焰图、锁等待、队列和内存剖析，定位真正性能根因 |
| 38 | [容量规划与过载保护](38-capacity-planning-and-overload-protection.md) | 用有界队列、Semaphore、超时、退避、熔断和降级设计资源有界且可恢复的满载行为 |
| 39 | [发布、灰度、回滚与事故响应](39-release-rollout-rollback-and-incident-response.md) | 从可信制品和分阶段放量走到安全止血、兼容回滚、数据修复与事故复盘 |
| 40 | [SLO、错误预算与可靠性决策](40-slo-error-budgets-and-reliability-decisions.md) | 从用户旅程定义 SLI/SLO，用错误预算、燃烧速率和政策决定何时发布或优先修可靠性 |
| 41 | [故障注入、混沌工程与韧性测试](41-fault-injection-chaos-and-resilience-testing.md) | 用断线、超时、满载、损坏和崩溃中间态证明安全隔离、有限恢复与持久一致性 |
| 42 | [依赖管理与软件供应链安全](42-dependency-management-and-software-supply-chain-security.md) | 从 manifest、lockfile 和依赖执行面追到来源策略、CI 最小权限、制品校验、签名与事故审计 |
| 43 | [跨平台工程与远程执行测试](43-cross-platform-engineering-and-remote-executor-testing.md) | 区分 host、target 与 remote placement，用 PathUri、目标 Shell 和 auto-env 测试覆盖 Linux、macOS 与 Windows |
| 44 | [协议演进、Schema 与向后兼容测试](44-protocol-evolution-schema-and-backward-compatibility.md) | 从 wire JSON、Schema、CLI、配置和 Rollout 五类契约判断 breaking change，并用旧版本 fixture 验证演进 |
| 45 | [大规模重构、模块边界与渐进式迁移](45-large-refactors-module-boundaries-and-incremental-migration.md) | 从变化原因和状态所有权建立 module/crate 边界，用 adapter、兼容字段和架构测试分阶段迁移热点 |
| 46 | [安全删除、废弃代码与技术债治理](46-safe-deletion-deprecation-and-technical-debt-governance.md) | 区分停用、停写、停读与物理删除，用静态、行为和运行证据安全退休 Feature、协议、配置与历史数据 |
| 47 | [幂等性、重复消息与分布式状态一致性](47-idempotency-duplicate-messages-and-distributed-state-consistency.md) | 区分关联 ID、业务幂等键和顺序号，用唯一终态、generation、CAS、ordinal 与对账处理重试、乱序和断线 |
| 48 | [资源生命周期、RAII 与泄漏防护](48-resource-lifecycle-raii-and-leak-prevention.md) | 区分取消、关闭、排空、终止与等待，用 guard、显式 shutdown、保留清理容量和进程树回收避免资源泄漏 |
| 49 | [缓存设计、失效策略与一致性](49-cache-design-invalidation-and-consistency.md) | 从 key、TTL、ETag、stale policy、singleflight 和 generation fencing 设计既快又不会跨身份读错的缓存 |
| 50 | [数据库事务、隔离级别与崩溃一致性](50-database-transactions-isolation-and-crash-consistency.md) | 从事务、WAL、锁、约束、lease 与 migration 理解并发写入、崩溃中间态和可重建投影 |
| 51 | [认证凭据、密钥与敏感数据生命周期](51-auth-credentials-secrets-and-sensitive-data-lifecycle.md) | 从 stdin、env、file/keyring 和内存追踪凭据的加载、传播、刷新、脱敏、撤销与删除 |
| 52 | [时间、时钟、超时与分布式时间语义](52-time-clocks-timeouts-and-distributed-time-semantics.md) | 区分 wall clock、monotonic clock、Duration 与 deadline，理解 timeout、TTL、lease、重试和跨机器 clock skew |
| 53 | [文件系统原子性、路径、临时文件与跨平台持久化](53-filesystem-atomicity-paths-temp-files-and-cross-platform-persistence.md) | 从 Path、symlink 和临时文件追到原子替换、锁、fsync、journal、TOCTOU 与跨平台差异 |
| 54 | [目录遍历、文件搜索、忽略规则与变化检测](54-directory-walking-file-search-ignore-rules-and-change-detection.md) | 理解 WalkBuilder、Git ignore、Nucleo 增量模糊匹配、流式 snapshot、watcher 与 fsmonitor 安全边界 |
| 55 | [文件读取、文本编码、二进制识别与有界内容加载](55-file-reading-text-encoding-binary-detection-and-bounded-loading.md) | 区分 bytes 与 text，掌握整读、分块、逐行、tail、编码探测、UTF-8 安全截断和多层容量上限 |
| 56 | [进程标准输入输出、Pipe、PTY、实时输出与背压](56-process-stdio-pipes-pty-streaming-output-and-backpressure.md) | 理解三条 stdio、Pipe/PTY 行为差异、有界 channel、seq/replay、stdin 幂等和退出后的输出排空 |
| 57 | [Shell、argv、引号转义、环境变量与工作目录](57-shell-argv-quoting-environment-and-working-directory.md) | 理解脚本文本怎样包装成 shell argv，以及 quoting、cwd、env policy、PATH、snapshot 和跨平台边界 |
| 58 | [退出码、信号、超时、取消与进程树回收](58-exit-codes-signals-timeouts-cancellation-and-process-tree-cleanup.md) | 区分 exit/EOF/interrupt/timeout/cancel/kill，掌握 Unix process group、Windows Job 与有界排空 |
| 59 | [子进程错误分类、错误传播、重试与用户可读诊断](59-process-error-classification-propagation-retry-and-user-diagnostics.md) | 区分程序非零结果与执行系统故障，理解结构化错误、sandbox 升级、远程恢复和有界诊断 |
| 60 | [网络请求完整链路](60-network-request-dns-proxy-tls-http-websocket-and-sse.md) | 区分产品流量与工具流量，沿 proxy、DNS、TCP、TLS、HTTP、SSE 与 WebSocket 定位连接问题 |
| 61 | [Rust Workspace、Cargo、Just 与 Bazel](61-rust-workspace-cargo-just-bazel-and-build-graph.md) | 区分 module、crate、package 与 target，理解 Cargo/Just/Bazel 怎样共享依赖事实并形成可复现的构建测试图 |
| 62 | [Rust 编译、链接、原生依赖与制品](62-rust-compilation-linking-native-dependencies-and-artifacts.md) | 从 rustc、MIR、codegen 和 linker 追到 C/C++ 依赖、跨平台 ABI、debug symbols、strip 与发布制品 |
| 63 | [Rust 运行时内存](63-rust-runtime-memory-stack-heap-layout-allocation-and-oom.md) | 从栈、堆、布局、String/Vec、Box/Arc/Weak 追到有界缓冲、瞬时副本、内存泄漏、RSS 与 OOM 诊断 |
| 64 | [Serde、JSON/TOML 与反序列化边界](64-serde-json-toml-data-model-deserialization-boundaries-and-schema.md) | 从 Rust value、Serde data model 和 wire shape 追到 JSONL framing、TOML 分层、节点预算、重复键防线与 Schema/TS 对齐 |
| 65 | [Rust 生命周期、Borrow 与异步边界](65-rust-lifetimes-borrowing-send-sync-and-async-boundaries.md) | 从 owner/reference/lifetime 追到 `'static`、Send/Sync、async move、BoxFuture、RPITIT、spawn 与 Future non-Send 排错 |
| 66 | [Rust Result、thiserror、anyhow 与 Panic 边界](66-rust-result-option-thiserror-anyhow-panic-and-error-boundaries.md) | 从 Result/Option/`?`/From 追到 typed source chain、anyhow context、nested task Result、JoinError 与安全协议投影 |
| 67 | [Rust 宏、Attributes 与条件编译](67-rust-macros-attributes-cfg-cargo-features-and-generated-code.md) | 从 macro_rules fragments/repetition 追到 derive/proc macro、cfg、Cargo feature、build.rs custom cfg 与生成物验证 |
| 68 | [Rust Trait、泛型与动态分发](68-rust-traits-generics-associated-types-and-dynamic-dispatch.md) | 从 trait contract、bound 和 associated type 追到 impl Trait、dyn Trait、blanket adapter、object safety 与类型擦除 |
| 69 | [Rust Enum、模式匹配与状态机](69-rust-enums-pattern-matching-and-state-machines.md) | 从 variant/payload 和 destructuring 追到穷尽 match、事件状态投影、终态分类与协议演进 |
| 70 | [Rust Iterator、Closure、惰性管线与 Stream](70-rust-iterators-closures-lazy-pipelines-and-streams.md) | 从 iter/iter_mut/into_iter 追到 combinator、collect 短路、ownership 快照与有界异步 Stream |
| 71 | [Rust 集合、Map、Set、Queue 与 Entry API](71-rust-collections-maps-sets-queues-and-entry-api.md) | 从 Vec/VecDeque 追到 Hash/BTree/Index Map、唯一性、稳定顺序、容量与锁外 drain |
| 72 | [Rust 内部可变性、锁、原子类型与一次初始化](72-rust-interior-mutability-locks-atomics-and-once-initialization.md) | 从 Cell/Mutex/RwLock 追到 Atomic ordering、OnceLock、异步锁作用域与取消安全 |
| 73 | [Rust Module、路径、可见性与 Re-export](73-rust-modules-paths-visibility-reexports-and-api-boundaries.md) | 从 crate root、mod/use 和分级可见性追到 facade、条件模块与稳定 API 路径 |
| 74 | [Rust Struct、Impl、Newtype、Builder 与领域建模](74-rust-struct-impl-newtype-builders-and-domain-modeling.md) | 从字段、receiver 和构造器追到私有表示、领域不变量、validated newtype 与分阶段 Builder |
| 75 | [Rust From、TryFrom、AsRef、Borrow、Cow 与类型转换](75-rust-from-tryfrom-asref-borrow-cow-and-conversion-design.md) | 区分可失败验证、owned 转换、借用视图、Cow 快路径、分配成本与边界类型转换 |
| 76 | [Rust 智能指针、Deref、Drop、Pin 与 Unpin](76-rust-smart-pointers-deref-drop-pin-and-unpin.md) | 从 Box/Arc/Weak 的 owner graph 追到 RAII Drop guard、boxed Future、栈/堆 Pin 与地址稳定性 |
| 77 | [Rust Future、Poll、Waker、Async 状态机与取消](77-rust-future-poll-waker-async-state-machines-and-cancellation.md) | 从惰性 Future 和 poll 时间线追到 Waker、Stream、select、取消安全与有界并发驱动 |
| 78 | [Rust Unit、Integration、Mock、Snapshot 与 Property Test](78-rust-testing-unit-integration-mocks-snapshots-and-property-tests.md) | 从最小行为证明追到 TestCodex/App-server harness、SSE mock、TUI snapshot、并发与性质测试 |
| 79 | [Rust Tracing、日志、Span 与 OTEL 上下文](79-rust-tracing-logs-spans-structured-diagnostics-and-otel-context.md) | 从结构化 event/span 追到 async context、W3C parent、字段脱敏、cardinality 与 phase metrics |
| 80 | [Rust Unsafe、FFI、原始指针与 OS Handle](80-rust-unsafe-ffi-raw-pointers-os-handles-and-safety-boundaries.md) | 从 safety contract 追到 C ABI、raw buffer、fd/HANDLE ownership、RAII 与 pre-exec 限制 |
| 81 | [Rust 宏系统、声明宏、过程宏与 Derive](81-rust-macros-declarative-procedural-derive-and-expansion.md) | 从 token/matcher 展开到协议 DSL、syn/quote、attribute、no-op derive 与 cargo expand 调试 |
| 82 | [Rust 条件编译、Cfg、Cargo Feature 与 Target](82-rust-conditional-compilation-cfg-features-targets-and-build-scripts.md) | 从代码是否存在追到 feature 合并、跨平台 facade、build.rs custom cfg 与 Cargo/Bazel 对齐 |
| 83 | [Rust 编译错误、所有权、生命周期与 Trait 诊断](83-rust-compiler-errors-diagnostics-ownership-lifetimes-traits-and-send.md) | 从 expected/found 与 bound origin 追到 move/borrow、Future Send、'static、dyn、`?` 和错误级联 |
| 84 | [App-server 代码生成、TypeScript、JSON Schema 与协议同步](84-app-server-code-generation-typescript-json-schema-and-contract-sync.md) | 从 Rust wire 类型追到 stable/experimental TS、JSON Schema、预计算归档、fixture drift 与兼容性审查 |
| 85 | [App-server 协议版本演进、前后兼容与迁移策略](85-app-server-protocol-versioning-forward-backward-compatibility-and-migrations.md) | 从 v1/v2 共存与 initialize capabilities 追到 alias/default、兼容投影、弃用生命周期和旧 rollout 恢复 |
| 86 | [App-server JSON-RPC、请求响应、通知、ID 与双向调用](86-app-server-json-rpc-requests-responses-notifications-ids-and-bidirectional-lifecycle.md) | 从四类 envelope 与 JSONL framing 追到 connection ID、server request callback、首答胜出、重放、取消和断线清理 |
| 87 | [App-server 请求并发、Serialization Scope、队列与竞态](87-app-server-request-concurrency-serialization-scopes-queues-fairness-and-races.md) | 从资源key与访问模式追到FIFO、SharedRead批次、writer fairness、ConnectionRpcGate、后台写入和关闭清理 |
| 88 | [App-server Thread、Turn、Item 状态机、快照与事件流](88-app-server-thread-turn-item-state-machines-snapshots-streams-and-recovery-consistency.md) | 从三层状态和ItemsView追到权威完成快照、delta合并、运行中resume原子订阅、历史投影与断线恢复一致性 |
| 89 | [App-server 审批状态机、Server Request、用户输入与多客户端协作](89-app-server-approvals-server-requests-user-input-and-multi-client-coordination.md) | 从业务ID与RPC ID分离追到pending oneshot、首答胜出、resolved/replay、Turn转换取消、权限交集与fail-closed响应 |
| 90 | [App-server Sandbox、权限配置、审批策略与命令执行安全链路](90-app-server-sandbox-permissions-approval-policies-and-command-execution-security.md) | 从审批与Sandbox两条独立轴追到legacy policy/命名profile互斥、sticky设置、Core决策、平台Sandbox转换、spawn与显式升级重试 |
| 91 | [App-server 命令执行、进程会话、流式输出、PTY 与清理](91-app-server-command-execution-process-sessions-streaming-pty-and-cleanup.md) | 对比Agent Item、sandboxed command/exec、unsandboxed process/spawn与thread/shellCommand，追踪PTY、stdin、base64 delta、timeout、drain、后台终端和断线清理 |
| 92 | [App-server Environment、本地与远程执行、路径、文件系统与能力边界](92-app-server-environments-local-remote-execution-paths-filesystems-and-capabilities.md) | 从Manager注册与Thread/Turn三态选择追到Ready/Starting、primary、跨平台PathUri、远程shell、exec/filesystem后端、能力协商、断线恢复与权限隔离 |
| 93 | [Exec-server 协议、传输、握手、Session 恢复、Noise 与版本偏差](93-exec-server-protocol-transports-handshake-sessions-recovery-noise-and-version-skew.md) | 从JSON-RPC/WS/stdio三层追到initialize、session attach/TTL、process序号补偿、writeId幂等、fs/http流、Noise认证分帧、背压与新旧版本双向兼容 |
| 94 | [App-server 配置 API、Layer Stack、来源、写入、Requirements 与运行时刷新](94-app-server-config-api-layer-stack-origins-writes-requirements-and-runtime-reload.md) | 从cwd相关的层叠读取追到effective值与origin、版本指纹、replace/upsert、批量原子写、管理员约束、okOverridden、缓存失效与Thread热刷新 |
| 95 | [App-server 模型目录、选择、Reasoning、Service Tier、Provider 能力与刷新](95-app-server-model-catalog-discovery-selection-reasoning-service-tiers-provider-capabilities-and-refresh.md) | 从model/list的picker投影追到远端/内置目录、认证过滤、默认选择、effort保序、tier门控、ETag/TTL刷新、Thread/Turn解析与运行时reroute |

## 源码精读系列

主题章回答“一套机制涉及哪些模块”，源码精读则选一个关键函数慢下来，回答“每一块代码为什么存在、状态怎样变化、为什么要按这个顺序、测试究竟证明了什么”。

第一篇是[`ConfigManager::apply_edits`：一次配置写入怎样安全完成](close-reading/01-config-manager-apply-edits.md)。它从单项/批量入口开始，逐段追踪路径权限、版本指纹、requirements、Replace/Upsert、内存副本、完整校验、保格式持久化与 `OkOverridden` 响应。

第二篇是[`ModelsManager::list_models`：模型目录如何在缓存、远端和认证之间做决定](close-reading/02-models-manager-catalog-refresh.md)。它从 `model/list` 入口追到 bundled、文件缓存、`/models`、ETag、source-of-truth、认证过滤、默认项和后台刷新。

第三篇是[`Session::spawn` 与 `Session::new`：一条 thread 怎样变成可运行的 Agent](close-reading/03-session-initialization.md)。它从 `thread/start` 追到模型与指令解析、三类 channel、持久化初始化、`SessionState`/`SessionServices`、首个 `SessionConfigured` 事件、required MCP 失败边界和 submission loop。

第四篇是[`run_turn`：模型、工具与下一次采样怎样组成循环](close-reading/04-turn-main-loop.md)。它从 `turn/start` 追到 `TurnContext`/`StepContext`、四层循环、response stream、tool call、并行执行、有序结果回填、follow-up sampling、compaction、steer 与统一 Turn 收尾。

第五篇是[`ToolRouter` 与 `ToolRegistry`：工具名怎样找到 handler，安全策略在哪里介入](close-reading/05-tool-dispatch.md)。它从 `ResponseItem` 规范化为 `ToolCall` 开始，追踪 Step 工具快照、注册和暴露、并行门、Pre/PostToolUse hooks、普通工具与 shell/patch 的分叉，以及 approval、Sandbox、升级重试、取消和结果回填。

第六篇是[Unified exec：命令怎样变成可继续交互的进程会话](close-reading/06-unified-exec.md)。它从 `exec_command` 参数与环境解析开始，追踪 approval/Sandbox、本地与远端 spawn、Session 级 ProcessStore、initial yield、`session_id`、`write_stdin`、实时 delta、首尾有界缓冲、退出 watcher、终止竞态与 Session shutdown。

第七篇是[App-server JSON-RPC 分派：一行 JSON 怎样找到正确的 Rust handler](close-reading/07-app-server-json-rpc-dispatch.md)。它从 stdio 的 JSONL framing 开始，追踪四类 envelope、`ClientRequest` 强类型转换、initialize/experimental gate、穷举分派、connection-scoped request id、成功与错误回包，以及 App-server 反向请求客户端批准时的 callback 生命周期。

第八篇是[Serialization scope：并发请求怎样按资源排队，又不锁住整个服务器](close-reading/08-request-serialization-scope.md)。它从 config 读写与同 thread 请求的冲突开始，追踪 scope 宏、运行时 queue key、connection-scoped process/watch、FIFO、SharedRead 批次、writer fairness、每 key 唯一 drain、ConnectionRpcGate、断线跳过和 background config 写入。

第九篇是[Rollout resume：历史记录怎样重新变成可运行的 thread](close-reading/09-rollout-resume.md)。它先区分运行中 rejoin 与冷恢复，再追踪 StoredThread、`InitialHistory::Resumed`、Paginated 反向扫描、compaction/rollback 重放、TurnContext 与 world-state 基线、Session 身份、同一路径续写、单 writer 所有权，以及模型上下文、UI 投影和状态数据库之间的边界。

第十篇是[MCP tool call 生命周期：外部工具怎样被发现、授权、调用并回到模型](close-reading/10-mcp-tool-call-lifecycle.md)。它从 thread-owned `McpRuntime`、连接初始化与 `tools/list` 开始，追踪 Step 级 `McpBinding`、`PreparedMcpCall`、catalog revision、`ToolRegistry`、approval/Guardian/hooks、exact client `tools/call`、结果净化与 `FunctionCallOutput`；同时特别标明固定版本中 `tools/list_changed` 只记录日志的实现边界。

第十一篇是[Context compaction：长历史怎样变成可继续运行的新上下文](close-reading/11-context-compaction.md)。它从 Manual/Auto、PreTurn/MidTurn、Total/BodyAfterPrefix 开始，比较 TokenBudget、本地摘要、远端 v1 与远端 v2 四条路线，追踪工具调用配对、opaque compaction item、initial context 重注入、`reference_context_item`、world-state baseline、window lineage、`CompactedItem.replacement_history`，以及 resume/fork 怎样重放 checkpoint。

第十二篇是[Sandbox approval lifecycle：一条命令为什么直接运行、询问、失败或升级](close-reading/12-sandbox-approval-lifecycle.md)。它先拆开“动作是否获准”和“获准后能接触什么”两条轴，再沿 `ShellHandler → ExecPolicyManager → Session::request_approval → ToolOrchestrator → SandboxManager` 追踪 `Skip / NeedsApproval / Forbidden`、Hook/Guardian/用户 reviewer、Session 审批缓存、平台 Sandbox、denied-read 不变量、拒绝识别与一次有界升级重试。

第十三篇是[Managed network approval：网络请求怎样被识别、暂停、批准与记住](close-reading/13-managed-network-approval.md)。它从 managed proxy 的真实出口检查开始，追踪 baseline allow/deny、私网与 HTTP method 硬边界、execution attribution、并发 pending 去重、Hook/Guardian/用户审批、once/session/persistent 三种授权、规则持久化，以及 shell 的 Immediate 与 unified exec 的 Deferred 收尾。

第十四篇是[Apply patch lifecycle：一段补丁怎样从模型文本变成真实文件变化](close-reading/14-apply-patch-lifecycle.md)。它区分流式 patch 提案、验证后的 `ApplyPatchAction` 和实际提交的 `AppliedPatchDelta`，追踪 freeform grammar、shell 拦截、上下文验证、路径权限、每文件审批缓存、Sandbox 与失败升级、Add/Update/Delete/Move 的真实写入顺序、部分成功边界、UI 事件和模型结果回填。

第十五篇是[Tool output 与 context feedback：工具结果怎样回到模型并推动下一轮采样](close-reading/15-tool-output-context-feedback.md)。它从模型 call item 先行落入 history 开始，追踪 `ToolOutput` 的多消费者投影、Function/Custom/ToolSearch output 分叉、Pre/PostToolUse、失败与取消闭合、并行执行和 `FuturesOrdered` 有序回填、三层输出截断、call/output synthetic 修复、多模态降级，以及外层 `run_turn` 怎样基于完整工具反馈发起 follow-up sampling。

第十六篇是[Cancellation 与 interruption lifecycle：用户按下停止以后，Codex 怎样真正停下来](close-reading/16-cancellation-interruption-lifecycle.md)。它先区分 cancel 请求与完成确认、interrupt 与 steer、Turn 与 Session、前台工具与后台 terminal，再沿 `Op::Interrupt → abort_all_tasks → CancellationToken → graceful wait → forced abort → TurnAborted` 追踪唯一终态；同时解释 pending 审批的清理顺序、interrupted history marker 的 durability barrier、App-server 延迟响应 interrupt 的 acknowledgement barrier，以及 shutdown 为什么还要拆除进程、MCP、Code Mode、Hooks 和持久化。

第十七篇是[User input admission 与 pending queue：一条用户消息究竟何时算“收到了”](close-reading/17-user-input-admission-pending-queue.md)。它把 channel 接收、Started/Steered 准入、Turn 内 pending queue、Hook 接受与 rollout flush 拆成不同承诺层级，并用两条并发消息解释 waiter 先注册、四态 durable admission、client message ID 关联、expected Turn ID 世代围栏、follow-up sampling 和任务提前结束时的失败收口。

第十八篇是[UserPromptSubmit Hook 与 additional context：用户消息进入模型前，谁还能检查和补充它](close-reading/18-user-prompt-submit-hook-additional-context.md)。它沿用户消息进入 history 前的最后一道扩展边界，追踪 Hook discovery/trust、stdin schema、并发 command 执行、稳定顺序汇总、block/stop/failure 解析、HookStarted/Completed 事件、blocked message 与保留 context 的分离，以及 Hook context 和 App-server client context 两条同名但信任语义不同的管线。

第十九篇是[Turn/Item event projection 与客户端状态重建：实时通知为什么不是最终快照](close-reading/19-turn-item-event-projection-client-state.md)。它从 Core Turn/Item 生命周期事件出发，追踪 App-server listener 的先记状态后发通知、canonical item 与 legacy event 去重、delta 与 completed snapshot 的收敛、`TurnItemsView` 完整度合同、Error 与 Turn 终态分离、`ThreadHistoryBuilder` 的实时/rollout 双输入，以及运行中 resume 怎样原子合并持久历史、active Turn 与新订阅。

第二十篇是[Thread watch、订阅与多客户端路由：谁能收到事件，thread 又何时真正卸载](close-reading/20-thread-watch-subscription-multi-client-routing.md)。它先区分 connection subscription、thread status watch 与文件 watch，沿双向订阅索引、live connection 检查、每 thread 唯一 listener、fan-out 和 generation fencing 解释两个客户端怎样安全共享同一 Core thread；再用 runtime facts、RAII active guard、双路 watch、30 分钟完整宽限期、Core 状态复核和 pending unload reservation，说明 unsubscribe、interrupt 与 unload 为什么是三种不同动作。

第二十一篇是[App-server 反向请求与待决响应收口：多个客户端同时看到审批时，为什么只有一个答案生效](close-reading/21-server-request-pending-response-lifecycle.md)。它从 Core 审批事件追到 typed `ServerRequestPayload`、全局 Atomic request ID、register-before-send、pending callback map 和 oneshot waiter，解释广播请求怎样靠原子 remove 实现首答胜出；随后沿 typed decode、fail-closed fallback、权限交集、listener-ordered `serverRequest/resolved`、`turnTransition` 取消、原 ID resume replay、unload 与 shutdown drain，建立跨多客户端和生命周期边界的一问一答终结模型。

第二十二篇是[Outgoing router、传输写入与交付边界：`send().await` 成功为什么不等于客户端已经收到](close-reading/22-outgoing-router-transport-write-delivery-boundaries.md)。它把 typed `OutgoingMessage` 继续追过 `ToConnection`/`Broadcast` envelope、容量 128 的全局队列、发送侧 connection projection 和 per-connection writer queue，解释 initialize gate、notification opt-out、实验字段按连接裁剪、WebSocket 慢连接断开与 stdio 背压；随后进入 JSONL/WebSocket/in-process writer、response serialization fallback 和 write-complete oneshot，逐层区分构造、入队、路由、写出、对端收到与对端应用六种承诺。

第二十三篇是[Incoming transport、envelope 分类与 handler 准入：合法 JSON 为什么仍可能到不了业务函数](close-reading/23-incoming-transport-envelope-handler-admission.md)。它从 stdio newline 与 WebSocket text frame 开始，追踪四类 untagged envelope、bounded incoming queue 的差异化过载策略、connection 存活检查、raw request 到 typed `ClientRequest` 的二次解码、initialize/experimental gates、resource serialization queue 与 per-connection RPC gate；最后解释 exhaustive handler match、`Some/None/Err` 回包所有权，以及 malformed、unknown connection 和断线 queued request 为什么会形成不同的“无响应”。

第二十四篇是[Initialize 握手、capability 协商与连接状态发布：初始化为什么不是设置一个布尔值](close-reading/24-initialize-handshake-capability-session-publication.md)。它从 `ClientInfo` 与五类 capabilities 开始，追踪保守默认、name header 验证、MCP extension allowlist、legacy form 归一化和 `OnceLock` 一次性 session commit；随后区分 connection-local state 与 process-global originator/UA/residency，解释 initialize response、outbound capability projection、初始 config warning/remote status、attestation registry 和最终 Broadcast-ready Release store 的发布顺序，并对比通用 transport 与 in-process 路径。

第二十五篇是[Request tracing、context ownership 与延迟回包：handler 返回以后，原来的 span 为什么还活着](close-reading/25-request-tracing-context-ownership-delayed-response.md)。它先拆开 log、event、span、trace 与 W3C carrier，追踪远端 `traceparent` 怎样成为统一 `app_server.request` server span 的 parent；随后沿 register-before-run、`RequestContext` registry、Core `Submission.trace`、Turn ID 晚绑定和 response/error 的 terminal take，解释立即回包、后台 `thread/start` 与终态确认式 `turn/interrupt` 怎样共享一套 tracing ownership，并明确区分 Responses API metadata、模型上下文、出站入队与客户端实际交付。

第二十六篇是[Connection teardown、resource ownership 与有界排空：断开连接为什么不是删除一个 map entry](close-reading/26-connection-teardown-resource-ownership-bounded-drain.md)。它从 stdio/WebSocket 物理终态进入 `ConnectionClosed` control event，沿 live-map logical removal、RPC gate close/drain、outbound writer 撤销和独立 cleanup JoinSet，逐项追踪 request context、reverse callback、filesystem watch、command/process 与 thread subscription 的不同 owner；随后对比单连接 close、stdio EOF、第一次 graceful signal、第二次 forced signal 和 in-process explicit shutdown，解释 30/10/5 秒 deadline、CancellationToken、control message、channel closure 与 task abort 各自真正保证什么。

第二十七篇是[Analytics、telemetry projection 与业务终态：一条 event 为什么需要多层事实才能生成](close-reading/27-analytics-telemetry-projection-business-terminal.md)。它先区分 log、trace、metric 与 analytics event，沿选择性 track、容量 256 的 try-send queue、单一 reducer 和 auth/batch/HTTP 发送，解释 best-effort observability 为什么不阻塞业务；随后用 thread/start、turn/start、TurnSteer 和 reverse approval 案例追踪 request/response/notification/Core facts 怎样按 connection/request/thread/turn/item 关联，建立 thread originator、Turn readiness、tool/review terminal 和 flush 边界，并说明 event 存在或缺失各自不能证明什么。

第二十八篇是[Error taxonomy、structured payload 与恢复策略：看到 error 后为什么有时要重试、有时却只能继续等](close-reading/28-error-taxonomy-structured-payload-recovery.md)。它先区分 JSON-RPC request error、Turn 中间态 `error` notification 和 `turn/completed` 业务终态，再沿 `ApiError → CodexErr → CodexErrorInfo → ErrorEvent/StreamErrorEvent → TurnError` 追踪有损映射；随后用输入过长、SSE 临时断开、retry budget 耗尽和用户 interrupt 四个案例，解释 `willRetry`、`affects_turn_status`、指数退避、transport fallback、旁路错误、history replay 和客户端幂等恢复策略。

| 附录 | [源码证据索引](appendix-source-index.md) | 快速回到关键定义和测试 |

## 阅读方式

第一次阅读时，先读术语页，再读每章的“先说人话”和例子；此时不必马上打开源码。第二遍再同时打开仓库，完成章末的“理解检查”和“源码检查点”。搜索时优先使用符号名：

```bash
rg -n 'pub\(crate\) async fn run_turn' codex-rs/core/src
rg -n 'pub enum Op|pub enum EventMsg' codex-rs/protocol/src
```

行号只是基线版本的定位辅助；符号名和调用关系才是长期有效的导航方式。

### 怎样使用词汇表

每章末尾都有“本章词汇表”，只解释阅读该章最容易卡住的词。遇到表中没有的名词，再查[术语总表](glossary.md)。词汇表提供三层信息：英文写法、字面翻译、它在 Codex 当前源码中的具体职责。阅读代码时应以第三层为准。

例如 `Thread` 字面是“线程”，但本课程中通常指一条可持续和恢复的对话任务线，不是操作系统执行线程；`Handler` 字面是“处理器”，在代码中通常是负责接收某类请求并实现行为的对象。

## 目录词汇

| 词语 | 在本课程中的意思 |
|---|---|
| 源码基线 | 文档核对所依据的 Git commit |
| 阅读产出 | 读完后应该能够独立解释或完成的事情 |
| 第一/二/三阶段 | 学习难度和主题分组，不是 Codex Runtime 的执行阶段 |
| 源码检查点 | 建议亲自打开验证的文件、符号或测试 |
| 心智模型 | 为理解复杂实现而建立的简化解释，不等于所有内部类型一一对应 |

## 课程边界

第一阶段专注最稳定、最值得先掌握的骨架：

- 进程入口与运行表面；
- TUI ↔ app-server ↔ core；
- thread / turn / step；
- 模型采样与 tool loop；
- 上下文、事件、持久化；
- 集成测试方法。

第二阶段继续覆盖 Skills、plugins、MCP、apps、Code Mode、多 Agent、remote executor 和 sandbox 如何接入主链路。各平台 sandbox 的具体 profile/系统调用细节不在本课程逐行展开；学习重点是稳定的跨平台职责边界。

第三阶段讨论产品化和长期运行：配置如何从多来源合并并受管理要求约束，Hooks 与 Extensions 如何安全扩展生命周期，Memory 如何跨 thread 提炼知识，以及模型目录、认证、Token 和性能如何共同决定实际体验。

第四阶段把概念落到真实数据和调试工作：沿一条任务查看每层 payload，分析不同来源的指令怎样进入 Prompt，按语义区分错误和取消，并使用 Event、Trace、Metric 与稳定 ID 判断系统实际停在哪一步。

第四阶段下半部分开始动手：先用五个受控实验练习确定修改影响面和测试层，再从一个教学版最小 Agent 出发，逐项推导 Codex 为什么需要 StepContext、Tool Router、安全编排、Compaction、Rollout 和 App-server。

第五阶段转向“读实现所需的底层能力”。先把 Codex 中反复出现的 Rust 写法翻译成可直接套用的阅读方法，再沿一次用户中断理解 Tokio task、不同 channel、背压、`select!` 和协作式取消；接着进入 TUI，理解 app-server notification 怎样按 thread 路由，并由 `ChatWidget` 合成为实时但可最终校正的客户端投影；然后沿持久化链路分清 rollout 账本、SQLite 查询投影、`ThreadStore` 和运行中的 Session；再进入 MCP，理解配置、连接、目录、step binding、审批和结果回传为何必须组成一条一致性链；随后用一条恶意工单攻击链，把不可信内容、授权、Guardian、Sandbox、网络和 MCP/App 外部副作用放进同一套分层防御模型；最后把所有层重新组织成一棵调试决策树，用最后事件、配对事件和稳定 ID 定位“任务卡住”的真正边界。

第六阶段从“找到问题”进入“安全地改代码”。先区分产品缺陷、项目指导缺口与预期行为，再把调试证据写成可证伪假设和公开不变量；沿所有权边界选择最小修改位置，用现有 test harness 写出旧实现会正确失败的回归测试，并按仓库约定完成目标测试、schema/snapshot/lock 生成、lint、format 与最终 diff review；随后把本地补丁组织成可审查 PR，用原子提交、What/Why/How、breaking-change 清单、分层 CI 和有证据的 review 回复推进到可安全合并的状态；接着交换到 reviewer 视角，在一份跨 Core、App-server、Config 和 UI 的教学 diff 中练习证明触发路径、校准 P0–P3、按根因去重并给出可执行 finding；然后进入性能工程，沿图片处理与 CLI 启动案例学习怎样定义延迟边界、构造冷/热工作负载、控制 benchmark 噪声，并用微基准、宏基准和线上耗时画像共同证明优化收益；当基准已经确认 p95 退化后，再组合 Turn profile、OpenTelemetry Trace、CPU/内存 profile、锁与队列边界，把“慢”追踪到可证伪的计算、等待、积压或内存根因；接着把单请求性能扩展到系统容量，理解 burst、准入、积压、超时、重试风暴和级联故障，并用多层有界资源与恢复测试保证过载时安全退化；随后沿真实 release workflow 把合并后的源码变成可验证制品，用分阶段灰度控制影响面，在异常时依据 feature、协议和迁移兼容性选择暂停、回滚、向前修复或数据修复，并通过事故响应和复盘把恢复经验沉淀成自动 gate、测试与 runbook；再从关键用户旅程定义 SLI/SLO，区分可用性、延迟、正确性、持久性和安全边界，用错误预算、burn rate 和多窗口告警衡量可靠性风险，把预算余量写成发布与工程优先级政策；接着主动注入断线、延迟、乱序、资源耗尽、畸形数据和崩溃中间态，以安全、有界、活性、终态、持久性和可观测性断言证明保护机制并非只在正常路径成立；随后把可靠性视线扩展到第一方源码之外，从 manifest、resolver 和多生态 lockfile 追踪完整依赖图，审查 build script 与 CI Action 等执行面，并用来源白名单、固定 revision、frozen install、最小权限、checksum、签名和 OIDC 发布构成软件供应链的分层证据链；接着把同一份集成测试扩展到不同执行位置和操作系统，区分 host、target 与 local/remote placement，以 PathUri 保留 foreign path 语义，让 Shell、Sandbox 和进程终止在 executor 侧按目标平台具体化，并通过 Docker Linux、Wine Windows 与 native CI 分层验证；再把“代码是否能编译”扩展成完整的协议兼容评审，沿 wire JSON、TypeScript/JSON Schema、运行行为、CLI、config 和 Rollout 找到无法与本次提交同步升级的消费者，以 capability、alias、兼容 reader、reader-first 灰度和固定旧数据恢复测试支撑安全演进与回滚；随后进入大型热点的长期治理，从 change reason、状态 owner 和依赖方向选择 module 或 crate，通过 deprecated alias、adapter、compatibility field、branch by abstraction 与 architecture test 把重构拆成可验证的小阶段，并在调用和运行证据归零后完成 contract；接着学习怎样真正删除旧路径，区分停用、停写、停读与物理移除，用静态、行为、运行三类证据覆盖 App-server、CLI、config、Rollout、平台代码和生成物，按语义选择 alias、no-op、迁移或 fail-closed 拒绝，并以 owner、利息、触发条件、退出标准和 ratchet 管理长期技术债；随后把重试、断线和并发竞态放进同一个一致性模型，区分 request correlation、entity identity、operation key 与 ordinal，用原子终态、generation fencing、串行 owner、optimistic version、数据库约束和 authoritative-log/projection 对账，使重复投递和乱序更新不会变成重复副作用或丢失状态；接着沿一次完整 teardown 追踪 task、channel、listener、Semaphore permit、远端 stream、PTY/子进程和终端全局模式的 owner，用 RAII guard 覆盖错误与取消路径，用显式 async shutdown 规定关闭顺序、排空、等待和 deadline，并为过载下的 cleanup 保留容量；最后把缓存视为权威状态的有界副本，从身份完整的 key、TTL/ETag/version、fresh/stale 策略、singleflight、generation fencing、错误降级和容量淘汰共同证明命中结果既可复用又不会跨账号、跨世代读错。

第六阶段的数据库专题进一步把“一致性”落到持久写入：从 ACID 和 `BEGIN IMMEDIATE` 理解读改写的隔离边界，用 WAL、busy timeout、constraint、UPSERT、affected rows、lease 与 ownership token 处理单 writer 竞争和迟到提交；再沿 JSONL → Thread History projection 的同事务 offset 推进、SQLx migration version/checksum、单库 corruption 备份与重建，区分事务原子性、跨库最终修复和不同崩溃模型下的持久性保证。

第六阶段的认证专题继续追踪另一类高价值状态：从浏览器 OAuth、stdin、环境变量和外部 provider command 识别 secret 的入口，比较权限受限文件、系统 keyring、secrets 后端和 ephemeral 内存的真实保证；再沿 AuthManager 缓存、HTTP header、MCP 与 executor 传播边界，理解 singleflight 刷新、refresh-token rotation、日志/Doctor/Telemetry 脱敏，以及 logout 时远端撤销与本地删除为何缺一不可。

第六阶段的时间专题把代码中看似相同的“几秒钟”拆成不同语义：用 UTC/SystemTime 表达可持久和跨进程交流的现实时间，用 Instant 测量进程内耗时与 deadline，用 Duration 表达长度；再沿 TimeProvider、current-time reminder、input-interruptible sleep、MCP startup/tool timeout、WebSocket idle timeout、Code Mode yield、Retry-After、TTL、OAuth expiry 和 SQLite lease，理解总预算传播、时钟回拨、clock skew、到期动作与可控时间测试。

第六阶段的文件系统专题继续把“保存文件”拆成不同保证：用 Path/PathBuf 和 AbsolutePathBuf 区分平台路径、词法绝对化与真实 canonicalization，沿 symlink-aware config edit 理解逻辑/物理目标；再比较 cache 的同目录临时文件替换、history 的 append + advisory lock、rollout migration 的 staged file + fsync + parent-directory sync + durable journal，理解 atomicity、durability、lost update、TOCTOU 和 Unix/Windows/WSL 差异。

第六阶段的文件发现专题接着研究“怎样找到正确文件”：用 ignore::WalkBuilder 把 root、hidden、Git ignore、override 与 symlink 组织成候选流，用 Nucleo index 将一次目录遍历复用于每次 `@` query，并以流式 snapshot、stable top-N、cancel 和 session generation 保证交互响应；再把搜索与 codex-file-watcher 分开，理解多 subscriber ref count、missing-path ancestor fallback、throttle/debounce、粗粒度 cache invalidation，以及 Git fsmonitor 加速为何必须禁用仓库选择的 executable helper。

第六阶段的文件读取专题继续回答“找到以后怎样安全打开”：先把任意文件保留为 bytes，再按用途选择整读、1 MiB 分块、JSONL 逐记录或 4 KiB tail；沿 metadata 预检、`take(MAX+1)` 和实际长度复核理解读取期增长竞态，再区分严格 UTF-8、旧 code page 探测与 lossy 诊断，最后用字符/记录边界、head-tail omission marker、skill prompt byte budget 和流式 zstd 说明每一层都需要独立容量边界。

第六阶段的进程 I/O 专题把静态文件读取扩展为持续数据流：从 stdin/stdout/stderr 与 fd 0/1/2 入手，对比 Pipe 的分流和 PTY 的 terminal 语义；再沿 8192-byte reader、mpsc 背压、broadcast lag、1 MiB/50,000-chunk 远程 retention、seq/replay 对账与 write ID 幂等，理解实时 delta、最终 transcript、Exited、Closed 和 late-output drain 为什么必须分层设计。

第六阶段的构建系统专题再把视线从运行时拉回“源码怎样变成可验证制品”：先区分 Rust module、crate、Cargo package/target 与 Bazel package/target，沿 workspace manifest、feature、target triple、profile 和 Cargo.lock 建立本地依赖图；再展开 Just recipe、Nextest 调度和 schema/snapshot 生成入口，并追踪 Bazel 怎样用 `crate.from_cargo` 复用 Cargo 解析事实、用 hermetic toolchain、platform constraint、runfiles 与显式 compile/test data 建立可缓存的 action graph，从而解释本地 Cargo 通过而 Bazel/CI 失败的常见边界。

第六阶段的编译链接专题继续进入单个构建节点内部：沿 parsing、macro expansion、类型与借用检查、MIR、LLVM codegen、monomorphization 和 linker 理解 `.rmeta`、`.rlib`、object 与 executable；再用 Linux bwrap 的 C/libcap 构建、macOS Objective-C category flags 和 Windows manifest/CRT 案例说明 header、sysroot、pkg-config、ABI 与跨平台工具链，最后追踪 DWARF/dSYM/PDB、strip、sign 和 package，学会把 compile、link、loader 与 runtime failure 分层诊断。

第六阶段的运行时内存专题再追问“制品启动后，数据究竟怎样活着”：用 stack frame、heap allocation、alignment 和 object graph 理解 String/Vec 的 pointer-length-capacity、Box/trait object 的间接布局、Arc/Weak/Mutex 的共享 owner 与 async Future state；再沿远程进程 1 MiB/50,000 chunk 双上限、HeadTailBuffer snapshot、4096 write-ID cache 和 HTTP `Bytes` shared clone，区分 payload、entry overhead 与瞬时峰值，最后用 live bytes、allocation rate、RSS、fragmentation 和 task/queue count 判断 high-water retention、真实 leak、stack overflow 与 OOM。

第六阶段的序列化专题随后研究“内存中的值怎样安全跨过边界”：先区分 Rust domain model、Serde data model、JSON/TOML syntax 与 JSONL/transport framing，再逐项拆解 rename、alias、tag、untagged、flatten、missing/null/default；沿 App-server typed dispatch、Rollout 逐行追加和 Config `TomlValue` 分层合并理解三类真实用途，并以 exec-server 的 `DeserializeSeed`/`Visitor`、重复键拒绝和节点预算说明反序列化为何同时是类型边界、兼容边界与资源安全边界，最后用 exact wire fixture、旧 reader 样本、Schema/TS 对齐和边界矩阵验证 contract。

第六阶段的生命周期专题接着追问“typed value 怎样安全进入函数、Future、队列与后台任务”：从 owner、shared/mutable borrow、NLL 和 reborrow 建立引用有效范围模型，区分 `&'static T` 与 `T: 'static`、Send 与 Sync、借用 Future 与 owned Future；再沿 `TimeProvider`、`SessionTask`/`AnySessionTask`、App-server cleanup/serialization queue 和工具并行执行路径，理解 RPITIT、`BoxFuture<'a>`/`BoxFuture<'static>`、`self: Arc<Self>` 与 spawn 的关系，并把 use-after-move、borrowed-data-escapes 和 Future-not-Send 错误翻译成可操作的 owner/scope 决策。

第六阶段的 Rust 错误专题继续研究“失败怎样穿过这些边界而不丢语义”：把 `Result`/`Option`/`?` 展开成显式 enum 控制流，区分 map、map_err、and_then、transpose 与 From 转换；再沿 ConfigManager typed error、current-time 的 anyhow context 与多层 timeout、session rollout init 的 chain/downcast、cleanup task 的 JoinError，理解 `thiserror`、anyhow、panic/cancel 和 nested Result 的职责，最后以 stable code、安全有界 message、内部 source chain 和 trace correlation 构成协议错误投影。

第六阶段的系统边界专题随后进入 Safe Rust 无法独立证明的区域：从 unsafe block/function、raw pointer、alignment、初始化和 aliasing 建立 safety contract；再沿 bwrap C argv、Linux borrowed fd、Windows raw buffer/OwnedHandle 和 known-folder allocator pairing，区分借用、ownership transfer、close/free 责任与错误约定；最后进入 Unix fork 后的 `pre_exec` 限制，用最小 unsafe scope、贴近调用的 SAFETY 证明、RAII wrapper、Miri/sanitizer/fuzz 和 native CI 审计这些人工保证。

第六阶段的宏专题接着解释“源码中没写出的代码从哪里来”：从 token、fragment、matcher、repetition 和 hygiene 手工展开 `macro_rules!`，沿 App-server request DSL 理解一份 entry 怎样同步生成 enum、match、conversion 和 schema traversal；再进入 TokenStream、syn、quote、derive/attribute macro，追踪 `ExperimentalApi` 如何读取 fields/attributes 生成 impl，以及 test/production 如何通过 cfg 在真实 schema derive 与 no-op derive 间切换，最后用最小展开、cargo expand、compile-fail 和生成物测试调试隐藏代码。

第六阶段的条件编译专题继续回答“展开以后，哪些代码真正进入本次制品”：区分 `#[cfg]` 的源码删除、`cfg!` 的常量布尔和 `cfg_attr` 的条件属性，再从 host/target triple、Unix family、具体 OS、architecture/environment 选择平台实现和依赖；沿 V8 feature forwarding 理解 Cargo additive feature 与产品 runtime flag 的区别，最后用 Bwrap 的 build.rs capability probe、check-cfg、native link 与 Bazel select/`--cfg` 对照，建立覆盖 test/profile/feature/target/build-system 的验证矩阵。

第六阶段的编译诊断专题再把这些语言约束变成一套可重复的排错方法：不背错误代码，而是沿 primary/secondary span 和 notes 找 expected、found 与 bound origin；用 ThreadId/AbsolutePath newtype 区分类型与转换错误，用 TimeFuture、queued `'static` Future、SessionTask adapter 和 Tool Registry 解释 borrow、Send、dyn 与 type erasure，再沿 current-time 的 timeout/channel/protocol nested Result 拆解 `?` 和 error conversion，最终把每条长诊断翻译成 owner、scope、await 和 API contract 的时间线。

第七阶段从单一语言内部进入跨语言协议供应链：先区分 Serde 的运行时 wire behavior 与 TypeScript/JSON Schema 的静态投影，再沿 App-server 的 test-only 真实 derive、stable/experimental 过滤、可见 fixture、Zstandard 预计算归档和 production exporter，理解为何 Rust 能编译仍不足以证明客户端合同同步；最后用 path-set/content/archive drift 测试、可重复生成、四象限兼容矩阵和固定提交中的 generator 入口差异，建立协议生成物的修改与审查方法。

第七阶段的协议演进专题进一步拆开“版本”一词：`clientInfo.version` 是客户端软件标识，并不在固定提交中选择 v1/v2；旧动词式方法与 v2 资源式方法共存于同一 request surface，而 initialize capabilities 负责每连接的实验能力协商。随后用 `guardian_subagent` alias、legacy missing-field default、ignored `multiAgentMode`、sandbox compatibility projection、rollback deprecation notice 和不再 live emit 的旧 notification，理解宽读窄写、停止写/停止读与弃用窗口；最后把旧 rollout/history projection 纳入第五个兼容参与者，用四象限、持久化时间轴和分层 regression test 审查迁移。

第六阶段的宏与条件编译专题进一步回答“眼前源码为何不等于 compiler 最终检查的程序”：从 token、fragment、matcher arm、nested repetition 和 hygiene 手工展开 `macro_rules!`，沿 App-server request DSL 观察一条 entry 同时生成 enums、match、From、实验元数据和 schema exports；再区分 compiler/derive-helper/attribute-proc-macro 属性、`#[cfg]` 删除与 `cfg!` 常量分支、target/test/profile 条件、dependency Cargo feature 与 Codex runtime Feature，并把 build.rs custom cfg、Bazel parity、compile-time file inputs 和 committed generated fixtures 接成完整验证链。

第六阶段的 Trait 专题接着回答“不同具体类型怎样在保持类型安全的同时共享同一套调用方式”：先从 required/default method、receiver、supertrait、generic bound、`where` 和 associated type 建立行为契约，再区分泛型/`impl Trait` 的静态分发与 `dyn Trait` 的动态分发；沿 `TimeProvider`、`ThreadStore`、Tool Registry 观察运行时注入和异构集合，最后用 `SessionTask`→`AnySessionTask`、`WorldStateSection`→`ErasedWorldStateSection` 两条真实适配链解释 RPITIT、BoxFuture、blanket impl、dyn compatibility 和“强类型核心、擦除边缘”的设计方法。

第六阶段的 Enum 专题再研究“有限状态怎样成为编译器能检查的数据形状”：从 unit/tuple/struct variant、嵌套 payload、destructuring 和 ownership 开始，逐一比较 match、`matches!`、`if let`、`let else`、`while let`、or-pattern、guard 与 `@` binding；再沿 EventMsg→AgentStatus、RuntimeFacts→ThreadStatus 两条投影链理解终态、组合 flags、观测窗口与状态 owner，最后把穷尽匹配、`non_exhaustive`、wire discriminator 和旧客户端兼容放进同一份 enum 演进清单。

第六阶段的 Iterator 专题随后把单个 value 扩展成有序数据管线：先用 Item 类型和 ownership 区分 `iter`、`iter_mut`、`into_iter`，再逐项拆解 map/filter/filter_map/flat_map、chain/zip、peekable、fold、短路 consumer 与 `collect<Result>`；沿 migration session 展开、增量请求对位校验和锁内 snapshot 理解 clone、物化与错误语义，最后把同步 Iterator 提升到 Stream、FuturesUnordered 和 `buffer_unordered`，明确 async map 只创建 Future、有界并发如何驱动以及 unordered 为何会改变结果顺序。

第六阶段的集合专题继续追问“管线物化以后怎样保存与查找”：用访问模式区分 Vec、VecDeque、HashMap/Set、BTreeMap/Set 与 IndexMap，沿 Tool Registry 的 Entry 冲突策略、request serialization 的 `HashMap<Key, VecDeque<_>>`、Thread watch 的 `or_default` 和 Guardian 滑动窗口理解唯一性、插入顺序、key order 与 FIFO；再把 key 身份、稳定排序、容量/字节双上限、eviction、锁内 snapshot、锁外 async drain 和取消清理接成完整 mutation 不变量。

第六阶段的同步专题接着拆开 `Arc<Mutex<T>>` 的 owner、互斥和数据职责，从 Cell/RefCell 的单线程内部可变性走到 std/Tokio Mutex、RwLock、Atomic 与 OnceLock/LazyLock/OnceCell；沿请求队列的锁内 batch/锁外 await、搜索取消 flag、并行工具 terminal winner、Agent 执行计数和 connection 一次初始化，理解临界区、poisoning、CAS、memory ordering、RAII decrement、异步初始化取消与“无 data race 仍可能业务不一致”。

第六阶段的模块系统专题随后回答“文件里的实现怎样成为调用者可见的路径”：从 crate root、`mod`、`use`、`crate/self/super` 与 `pub`/`pub(crate)`/`pub(super)` 建立 module tree，再沿 `codex-core`、`codex-protocol` 和 `codex-app-server` 的私有实现模块与根级 `pub use` 区分源码文件树和公开 API 树；最后把 `cfg`/`path` test module、Trait import、`doc(hidden)`、deprecated alias、sealed Trait 以及 module/crate dependency 边界整理成源码导航和可见性变更清单。

第六阶段的领域建模专题进一步回答“公开路径中的类型为什么不直接用 String、整数和 PathBuf”：从 named/tuple/unit Struct、field、`impl`、receiver 和 smart constructor 建立数据与行为模型，再沿 `ThreadId`、`ProtocolVersion`、`NonEmptyString`、`CapabilitySet` 和 `AbsolutePathBuf` 理解 newtype 怎样保存身份及不变量；最后用 `ConfigBuilder` 和 `ConfigEditsBuilder` 区分准备态与有效产品，并把 private field、fallible construction、Deserialize 验证、DTO/domain model、derive 与 API 演进整理成类型设计清单。

第六阶段的类型转换专题接着研究“精确类型怎样安全穿过接口”：从 From/Into、TryFrom/TryInto 与 FromStr 区分不可失败转换和输入验证，从 AsRef、Borrow、Deref coercion、ToOwned 与 Cow 区分借用视图、等价 key、owned 副本和按需分配；再沿 `ThreadId`、`AbsolutePathBuf`、`PathUri`、`ToolPayload` 和 history grouping 追踪 move、clone、allocation、fallback、lossy 与 lifetime，最终形成参数、返回值和系统边界的转换选择清单。

第六阶段的智能指针专题随后把“转换 value”推进到“谁拥有 value、它何时释放、地址能否改变”：从 pointer/pointee、Box、Deref、Rc/Arc strong count 与 Weak upgrade 建立 owner graph，沿 current-time provider 和 models refresh worker 理解后台观察者为何不应反向保活系统；再用 execution/search/worker Drop guard 区分同步 RAII fallback 与显式 async shutdown，最后从 Future::poll 的 `Pin<&mut Self>`、Box::pin、`tokio::pin!`、boxed dyn Future、Unpin bound 和 projection 理解异步状态机的地址稳定合同。

第六阶段的 Future 专题继续打开异步状态机内部：从 async fn 的惰性创建、Future::poll、Ready/Pending、Context 与 Waker 还原一次暂停和再次调度的时间线，用 Windows child 和 AtomicWaker 案例理解阻塞事件桥接及 lost wakeup；再沿 ResponseStream、consumer-drop token 和工具 dispatch/cancel 竞争解释 Stream、背压、select loser、terminal winner 与 cancellation safety，最后比较顺序 await、join、FuturesUnordered、buffer_unordered、spawn 和 timeout 的启动、完成顺序、上限与副作用语义。

第六阶段的 Rust 测试专题再把这些实现知识转换成长期证据：从 Arrange/Act/Assert、oracle、unit/integration/E2E 和 sibling test module 选择最小真实边界，用 deterministic event、timeout watchdog、TempDir 和注入依赖消除 sleep、全局环境与外网噪声；再沿 Core 的聚合 suite、TestCodex、typed SSE/ResponseMock 与 App-server JSON-RPC round trip 验证公开行为，并用 TUI Insta snapshot、结构化深度相等、property/shrinking、并发交错、平台/remote executor 分层和 red→green regression workflow 构成可维护的测试组合。

第六阶段的 Tracing 专题随后研究“测试之外怎样知道生产运行实际发生了什么”：从 structured event、field/message、level/target、span parent 与 subscriber/filter 建立诊断数据模型，用 `#[instrument(skip_all, err)]`、`.instrument(span)` 和显式 spawn context 追踪异步 poll 生命周期；再沿 App-server request span、RequestContext registry、W3C traceparent/tracestate 与 tool dispatch aborted late-record 连接跨 transport/process 因果链，并用低基数 metric label、有界 payload preview、secret/PII 审查、单点 error 记录和 Turn phase timing 控制容量、隐私与语义噪声。

第七阶段的 JSON-RPC 专题再把 wire shape 放进真实运行时间线：先区分 request、success response、error response 与 notification，明确固定提交省略 jsonrpc 字段、支持 string/integer RequestId，并用 ConnectionRequestId 隔离多连接的相同 ID；再沿 transport framing、typed decode、RequestContext 和 ToConnection/Broadcast 追踪 client→server 响应路径，反向沿 server request ID、pending oneshot callback、首答胜出和 serverRequest/resolved 追踪审批/用户输入；最后用 pending replay、turnTransition 取消、bounded queue overload、write-complete 层级、disconnect owner cleanup 和 in-process 对称实现建立双向调用的终结不变量。

第七阶段的请求并发专题继续追问“同一条双向总线上哪些handler可以同时运行”：从logical race与data race的区别出发，把协议DSL中的Global、Thread、ThreadPath、Process、FsWatch和MCP OAuth scope转换成带connection namespace的QueueKey；再沿HashMap<VecDeque>、每key单drainer、Exclusive单项和连续SharedRead join_all批次，理解FIFO、跨key并行、writer fairness与队首阻塞；最后把ConnectionRpcGate的close/shutdown、closed request跳过、无gate background config write、取消安全和跨进程一致性边界接成完整ordering contract。

第七阶段的状态一致性专题把排序合同推进到客户端可见状态：先拆开Thread、Turn、Item三层状态机与NotLoaded/Summary/Full三种ItemsView，再区分started临时快照、delta实时材料和completed权威终态；随后沿ThreadHistoryBuilder、canonical rollout projection和change set理解实时归约与重启恢复为何共享规则；最后进入运行中thread/resume的per-thread listener，解释durable history与active turn合并、subscriber先登记、response先输出、pending request replay，以及断线后用snapshot重新收敛而不是盲续旧delta。

第七阶段的审批专题再进入Agent暂停等待外部决定的双向状态机：先拆开threadId/turnId/itemId、approvalId与JSON-RPC requestId，沿PendingCallbackEntry、全局AtomicI64、oneshot和发送前登记理解请求对位；再用callback map的原子remove说明多客户端首答胜出，用按ID排序的pending replay与per-thread serverRequest/resolved关闭所有窗口的重复UI；最后比较Accept、AcceptForSession、Decline、Cancel、policy amendment、request_user_input、MCP elicitation和dynamic tool fallback，并沿turnTransition批量取消、RAII等待计数、权限requested/granted交集和fail-closed解析建立安全边界。

第七阶段的Sandbox专题把“允许”继续追到“真实进程能做什么”：先把approval policy、reviewer、permission/sandbox policy和平台enforcement拆成四种职责，比较read-only/workspace-write/danger-full-access与untrusted/on-request/never的独立组合；再从thread/start的sandbox、turn/start的sandboxPolicy和实验性命名permissions互斥输入，追踪sticky设置、profile来源、writable roots、deny与managed network；最后沿ExecApprovalRequirement、单命令additional permissions、SandboxManager平台选择与transform、spawn和Sandbox denial后的显式升级重试，建立wire、decision、enforcement三层验证法。

第七阶段的命令执行专题再把平台spawn推进到长进程生命周期：先对比Agent commandExecution Item、受Sandbox的command/exec、无Sandbox的实验性process/spawn和进入Thread事件流的user shell command，明确argv/shell string、安全边界、身份和终结信号；再沿PTY/pipe选择、connection-scoped process identity、有界control channel、base64 stdin/output、每流cap、timeout与退出竞争、IO drain和最终response排序理解交互会话；最后进入Core unified exec的yield、store-before-wait、HeadTailBuffer、background terminal list/terminate/clean、connection close与Drop保险，建立资源有界和唯一终结不变量。

第七阶段的Environment专题把“进程在哪运行”扩展为完整资源身份：先区分configured、selected、primary与Ready，拆开Thread/Turn中省略、空数组和非空数组的三态sticky选择；再沿EnvironmentManager、Starting/Ready snapshot、远程info与shell解析、连接事件和wait工具理解逻辑ID、物理连接与每轮资源handle；随后用LegacyAppPathString、PathConvention和PathUri解决POSIX宿主到Windows执行机的foreign path问题，并把exec、filesystem、HTTP、capability discovery、per-environment权限和local-only App-server API接成一张能力边界图。

第七阶段的Exec-server专题继续打开远程Environment内部：先把App-server业务API、exec-server JSON-RPC与relay/Noise三层分开，比较WebSocket frame、stdio line和Noise record的framing及资源上限；再沿initialize/initialized、SessionRegistry、30秒detached TTL、共享reconnect attempt、process/read(afterSeq)补偿、事件重排和writeId幂等建立断线恢复算法；最后进入双向network policy RPC、control/cleanup容量、connection-local文件流、HTTP body delta、Noise pinned key/prologue/virtual stream/nonce顺序/Pong watchdog，以及current↔released真实binary双向version-skew测试。

第七阶段的配置API专题再回到所有运行链路共同依赖的控制面：把system、enterprise、user/profile、project、session和legacy managed来源组织成ConfigLayerStack，用递归overlay、叶子origin与canonical SHA-256 version同时回答“最终值、赢家和是否过期”；随后把普通配置与requirements约束分开，比较okOverridden与requirement readonly，并沿keyPath解析、replace/upsert/null clear、批量预校验、保留注释的文本编辑和全局serialization scope理解安全写入；最后把文件保存、plugin/skill缓存失效、session-default例外与逐Thread保留session层的best-effort热刷新拆成三个独立阶段。

第七阶段的模型控制面专题紧接配置默认值，先把Core的完整ModelInfo、picker中间层ModelPreset、App-server wire Model和连接层Provider能力拆开；再沿bundled/remote/static目录、ChatGPT与API认证过滤、priority和picker visibility解释唯一默认项，沿有序且可扩展的reasoning effort、input modality、personality、service tier、upgrade和availability NUX理解选择器合同；随后进入Online/Offline/OnlineIfUncached、5分钟文件TTL、ETag重验证、3分钟弱引用worker和远端source-of-truth条件，最后把显式模型保留、static provider fallback、未知slug metadata、Thread/Turn粘性覆盖、effort兼容选择、FastMode tier门控与运行时reroute/verification/safety通知接到真正采样。

## 文档可信度约定

- **源码事实**：能指向当前提交中的类型、函数、match 分支或测试。
- **解释模型**：为了学习而压缩的概念，不声称与每个内部类型一一对应。
- **待验证问题**：刻意留下的练习，不应直接当成实现事实。
- Mermaid 图只表示主要控制流，省略遥测、hook、错误恢复和 feature 分支时会明确说明。
