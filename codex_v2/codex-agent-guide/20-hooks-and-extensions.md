# 20. Hooks 与 Extensions

## 1. 两种扩展机制

Codex 中有两组相互关联但不同的扩展能力：

### Hooks

在明确生命周期节点运行配置的外部命令或规则，例如 user prompt、Tool 前后、compact 前后和 Session 结束。

### Extensions

进程内 Rust 扩展接口，可以贡献 Context、World State、Tools、MCP 配置、Turn Items 和生命周期逻辑。

简单理解：Hook 更像可配置的生命周期脚本；Extension 更像类型安全的内部插件 API。

## 2. Hook 事件

[`codex-rs/hooks/src/events`](../codex-rs/hooks/src/events) 定义的主要事件包括：

-Session Start / End；
-Subagent Start / Stop；
-User Prompt Submit；
-Pre Tool Use；
-Post Tool Use；
-Permission Request；
-Pre Compact；
-Post Compact；
-Stop。

每类事件有独立输入/输出 schema，而不是共享一个没有类型约束的 JSON 对象。

## 3. Hook 从哪里发现

Hook 配置属于 Config Layer。Discovery 会读取 layer stack，从系统、企业、用户、项目和 Session flags 等来源构造有效 Hook Registry。

保留来源非常重要：

-管理员 Hook 与项目 Hook 信任级别不同；
-用户可以知道某个 Hook 为什么运行；
-规则可以限制低信任 layer 注册某类 Hook；
-同一事件的执行顺序可以保持确定性。

发现和配置规则位于 [`hooks/src/engine/discovery.rs`](../codex-rs/hooks/src/engine/discovery.rs) 与 [`hooks/src/config_rules.rs`](../codex-rs/hooks/src/config_rules.rs)。

## 4. Hook Engine

Hook Engine 的主要步骤：

```text
Lifecycle event
  → Registry 找匹配 Hook
  → 构造 typed input
  → Command Runner 启动命令
  → 捕获有界 stdout/stderr/exit code
  → Output Parser 解析 typed output
  → 聚合多个 Hook outcome
  → Core 根据 outcome 继续、阻止或注入 context
```

核心实现位于 [`hooks/src/engine`](../codex-rs/hooks/src/engine)。

## 5. 为什么 Hook 输出有 Schema

Hook 可能要求：

-允许或拒绝工具；
-附加上下文；
-要求 Agent 继续；
-给用户 warning；
-修改 permission decision。

如果只根据 stdout 中某个任意字符串判断，会产生歧义和注入风险。Typed schema 让缺失字段、未知字段和错误值可以被明确诊断。

生成的 schema fixtures 位于 [`hooks/schema/generated`](../codex-rs/hooks/schema/generated)。

## 6. User Prompt Submit Hook

用户输入进入正式 History 前运行。它可以：

-记录或审计用户请求；
-提供额外上下文；
-阻止当前 Turn；
-对企业工作流做检查。

它不应把用户输入静默替换成另一个目标。附加内容应以有来源的 Prompt Fragment 进入上下文。

## 7. Pre Tool Use Hook

在执行 Tool 前，Hook 获得结构化信息：

-tool name；
-call ID；
-输入参数；
-cwd/environment；
-Thread/Turn identity。

它可以拒绝或影响执行策略，但最终仍要经过 Core approval 和 sandbox。Hook 允许不等于操作系统授权。

## 8. Post Tool Use Hook

工具结束后，Hook 可以查看：

-成功/失败；
-结构化响应；
-输出摘要；
-执行 metadata。

可用于审计、质量检查或给模型补充说明。Post Hook 自己失败时，Runtime 需要区分“工具已经成功产生副作用”和“Hook 报告失败”，不能把工具重新执行。

## 9. Stop Hook

模型准备结束 Turn 时运行。Stop Hook 可以：

-允许结束；
-要求继续，并给出 continuation fragment；
-要求停止；
-产生 warning。

`run_turn()` 在 `needs_follow_up=false` 后检查 Stop Hook。如果 Hook 要求继续，其 fragment 进入 History，然后重新 sampling。

需要防止 Stop Hook 无限要求继续，因此 Runtime 会维护 stop-hook active 状态和循环控制。

## 10. Compact Hooks

Pre Compact 可以检查或阻止压缩；Post Compact 可以记录结果或停止后续行为。它们必须知道 compaction trigger、reason 和 phase，因为 manual、pre-turn、mid-turn 的语义不同。

## 11. Output Spill

Hook stdout/stderr 也可能很大。`hooks/src/output_spill.rs` 负责有界处理和必要的外部存储引用。Hook 输出不能无上限进入 Prompt、Event 或 telemetry。

## 12. Extension Registry

进程内扩展通过 [`ext/extension-api/src/registry.rs`](../codex-rs/ext/extension-api/src/registry.rs) 注册 contributor。主要类别包括：

-Context Contributor；
-World State Contributor；
-Tool Contributor；
-MCP Contributor；
-Turn Input Contributor；
-Turn/Thread Lifecycle Contributor；
-Turn Item Contributor；
-Tool Lifecycle Contributor；
-Skill Invocation Contributor。

Skills、Memories 等功能可以作为 Extension 实现，而无需继续扩张 `codex-core`。

## 13. Context Contributor

[`ContextContributor`](../codex-rs/ext/extension-api/src/contributors.rs) 可以在不同边界贡献 Prompt Fragment：

-Thread context：线程启动时相对稳定；
-Turn context：每个 Turn；
-World State：可 diff 的动态状态。

Fragment 明确声明：

-DeveloperPolicy；
-DeveloperCapabilities；
-SeparateDeveloper；
-ContextualUser。

Session 统一收集和排序，Extension 不直接修改 History 内部数组。

## 14. Tool Contributor

Tool Contributor 在每个 Step 产生工具执行器。这样它可以读取：

-Session extension data；
-Thread extension data；
-Step/Turn extension data；
-当前配置和能力快照。

`build_tool_router()` 把 Extension Tool 与 Core/MCP/Dynamic Tool 一起加入 Registry，再统一计算 exposure。

## 15. Turn Item Contributor

它允许 Extension 在模型 item 进入 UI 前生成或调整产品层 Turn Item。例如外部能力可以提供自己的展示对象，而不改变原始模型 History。

这再次体现三种投影分离：Extension 可以影响 UI 表示，不必重写模型协议 item。

## 16. `ExtensionData`

[`ext/extension-api/src/state.rs`](../codex-rs/ext/extension-api/src/state.rs) 提供类型化数据 store。作用域包括：

```text
Session store     跨整个 Session
Thread store      当前 Thread 生命周期
Turn store        当前 Turn
Step data         捕获本次能力快照时使用
```

Extension 通过类型而不是全局字符串 key 共享状态，减少冲突和错误 downcast。

## 17. 一个 Skill Extension 例子

Skills Extension 的执行轨迹：

```text
Thread 初始化
  → store SkillsThreadState
  → ContextContributor 渲染 catalog

每个 Step
  → WorldStateContributor 获取 Executor catalog
  → Turn store 保存 snapshot

用户输入
  → TurnInputContributor 解析 explicit mentions
  → Provider 读取 main prompt
  → 返回 SkillInstructions fragment

构建 Tools
  → ToolContributor 提供 skills.list/read
```

这说明一个 Extension 可以同时贡献上下文、状态和工具，但每条能力仍走统一 Core 边界。

## 18. Hook 与 Extension 的选择

| 需求 | 更适合 |
|---|---|
| 用户在配置中运行审计脚本 | Hook |
| 不重新编译即可增加规则 | Hook |
| 贡献类型安全 Tool Runtime | Extension |
| 跨 Turn 保存内部状态 | ExtensionData |
| 修改 Stop/Tool 生命周期决定 | 两者都可，取决于信任与部署 |
| 核心产品能力 | Extension 优先 |

## 19. 失败处理

-Hook 找不到命令：产生有界 warning 或按策略阻止；
-Hook 超时：取消进程，不无限卡住 Turn；
-Hook 输出无效：诊断具体 schema 错误；
-Extension contributor panic：属于 Runtime 缺陷，不能伪装成模型错误；
-Extension 工具失败：转换为 Tool Result；
-Post Hook 失败：不能自动重跑已经成功的副作用工具。

## 20. 测试建议

-每种 Hook typed input/output schema；
-多个 Config Layer 的发现顺序；
-timeout、非零退出和超大输出；
-Pre Tool 拒绝不会执行 Handler；
-Post Tool 失败不重复副作用；
-Stop Hook continuation 真正进入下一次请求；
-Extension Context role 和顺序；
-Tool Contributor 的 Runtime/spec 一致；
-ExtensionData 作用域隔离；
-恢复或新 Turn 不泄漏旧 Turn store。

## 21. 设计原则

```text
生命周期节点必须明确。
Hook 输入输出必须结构化和有界。
Extension 通过 contributor 贡献，不直接篡改 Core 内部状态。
外部 Hook 与进程内 Extension 使用不同信任模型。
所有新工具仍经过统一 Registry、Policy 和 Sandbox。
```

