# 附录：Agent Runtime 设计模式

## 1. Orchestrator 与 Capability 分离

Agent Loop 负责“下一步做什么”，工具负责“具体如何做”。Core 不应包含每个业务工具的实现分支。

Codex 对应：`run_turn` / ToolRouter / ToolRegistry / Extension / MCP。

适用：工具不断增加、需要独立权限和测试时。

## 2. 控制面与数据面分离

```text
控制面：配置、Feature、ToolSpec、权限、路由、状态
数据面：模型 Stream、工具 stdout、文件内容、MCP Result
```

控制面应小、稳定、强类型；数据面可能很大，必须截断、分页或落盘。

## 3. Snapshot Context

每个 Step 捕获 `StepContext`，而不是在模型流和工具执行中随时读取可变全局配置。

优点：同一步骤内部一致；配置和动态工具可在下一 Step 生效；并发测试更可预测。

## 4. Incremental Event Log

History 和 Rollout 采用追加式事实，而不是不断重写“当前真相”。

优点：模型缓存友好、可审计、可恢复。若环境变化，追加新的 world state；不要修改旧消息假装它从未发生。

## 5. Typed Boundary

所有关键边界尽早转为类型：

- API JSON -> `*Params`；
- 用户提交 -> `Op`；
- 模型响应 -> `ResponseItem`；
- Tool Call -> tool-specific args；
- 配置 TOML -> `ConfigToml` -> resolved `Config`。

类型不是为了代码漂亮，而是为了在副作用之前拒绝无效状态。

## 6. Policy / Mechanism 分离

Policy 决定“允许吗、需要确认吗、在哪执行”；Mechanism 执行命令或文件操作。

Codex 对应：Approval policy、Sandbox policy、Executor、Tool implementation。

反模式：工具函数内部自行判断一组全局 bool，然后直接用宿主权限执行。

## 7. Least-Privilege Internal Agent

内部 Agent 不因“系统自己创建”就自动可信。记忆整合 Agent 只获得记忆目录写权限、无网络、无协作。

适用：代码审查、索引构建、迁移、数据清理等后台 Agent。

## 8. Progressive Disclosure

先提供有界索引，细节通过 search/read 按需加载。

Codex 对应：Skills metadata、Tool Search、`memory_summary.md`、MCP 工具发现。

该模式同时降低 Token、延迟和 Prompt Injection 暴露面。

## 9. Bounded Everything

所有进入模型上下文的来源都必须有硬上限：

- 文件行数/Token；
- Tool Result；
- AGENTS/Skill 内容；
- MCP 目录；
- Memory summary；
- 重试次数；
- 并发数；
- 队列和 rollout scan 数。

“通常不会很大”不是上限。

## 10. Explicit Lifecycle

Thread、Turn、Item、Tool、Compact 都有 started/completed/failed/aborted 边界。

显式生命周期让 UI、持久化和 telemetry 观察同一事实，也让 Hook 有稳定插入点。

## 11. Correlation IDs

不同粒度使用不同 ID：thread、turn/submission、response、item、tool call、Code Mode cell。不要用数组位置或日志顺序隐式关联异步结果。

跨进程协议中，ID 应原样透传，并进入日志 span。

## 12. Structured Failure as Data

工具退出码、Approval denial、Sandbox denial 通常应成为模型可见 Result，而不是让 Agent Runtime panic。

只有无法维护协议一致性或继续运行安全性的错误，才应升级为 Turn/Thread failure。

## 13. Lease-Based Background Work

后台任务通过 claim + lease + heartbeat + backoff 协调，而不是用进程内 mutex 假设只有一个实例。

Codex 对应：记忆 Phase 1 jobs 和全局 Phase 2 consolidation。

适用：可跨启动恢复、可能多进程运行的提取和索引任务。

## 14. Stable Baseline + Diff

当增量整合涉及新增、修改和删除时，用稳定 baseline 生成 diff，比只看最新水位更可靠。

Codex 对应：记忆 workspace 的 Git baseline 和 `phase2_workspace_diff.md`。

## 15. Model Metadata as Capability Negotiation

不要按模型名称硬编码大量 `if model == ...`。把并行工具、上下文窗口、Wire API、工具类型等声明成 `ModelInfo`，在 Turn 构造时解析。

未知模型使用保守 fallback，并记录观测信号。

## 16. Provider Adapter

Agent Loop 只依赖统一模型客户端，Provider adapter 负责 base URL、认证、Header、重试和 Wire API。

这使 OpenAI-compatible、自定义命令凭据、AWS 等实现不侵入 Agent Loop。

## 17. Cache with Provenance

缓存项不仅保存 value，还保存版本、时间、ETag 和来源身份。读取时验证全部 provenance。

适用：模型目录、工具发现、Skill 扫描、远程环境能力。

## 18. RAII Phase Timing

进入 sampling/tool/compaction 时创建 guard，Drop 时自动结束计时。它比手写 begin/end 更能覆盖提前返回和 `?` 错误路径。

## 19. Dual Representation

同一事实针对不同消费者保留不同表示：

```text
RawResponseItem  面向传输/兼容/调试
TurnItem         面向产品语义和 UI
RolloutItem      面向持久恢复
```

不要强迫一个巨型枚举同时承担所有职责。

## 20. Hook 与 Extension 的选择

- Hook：观察或拦截生命周期，适合 policy、审计、通知；
- Extension：贡献 Prompt、Tool、配置和 thread-local data；
- MCP：进程外、动态工具和资源；
- Skill：模型按需读取的工作流知识；
- Plugin：把这些能力打包分发。

选错层会导致 Core 特判、权限模糊或部署困难。

## 21. Command Pattern for Submissions

外部客户端不直接调用 Session 内部方法，而是发送 `Op` 到 submission channel。`submission_loop` 串行处理控制命令，并把长期任务派发出去。

适用：需要 interrupt、approval response、steer 与普通输入共用一个控制入口时。

## 22. Actor-Like Ownership

Session 通过 Channel 和受控锁拥有状态，外部通过 handle 操作。它不是严格 Actor 框架，但遵循“状态归属明确、消息驱动”的核心思想。

## 23. Idempotent Side-Effect Boundary

所有可能重试的外部写操作应有 idempotency key、前置状态检查或完成后验证。模型/Stream 重试绝不能默认等于副作用重试。

## 24. Cross-Platform Path Semantics

路径是操作系统语义，不应在协议内部过早降级为 UTF-8 字符串。执行端负责解析自身路径，边界使用明确的 absolute/relative/path-bearing types。

## 25. 设计评审速查表

增加一个 Agent 功能前，逐项回答：

1. 它属于 Core、Hook、Extension、MCP、Skill 还是 Plugin？
2. 输入在哪个边界转成强类型？
3. 谁拥有生命周期和取消信号？
4. 模型能看到哪些信息，最大尺寸是多少？
5. 它需要什么最小权限？
6. Approval 在哪里发生？
7. 重试是否可能重复副作用？
8. 事件如何关联、持久化和恢复？
9. 性能分到哪个 timing phase？
10. Integration test 如何从公共入口验证行为？

