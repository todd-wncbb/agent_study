# 附录 D：Codex Agent 术语表（Glossary）

这份术语表用于快速查询 Codex Agent 源码中的名词。它解释的是这些词在**当前 Codex 实现里的具体含义**，不一定等同于其他 Agent 框架中的同名概念。

## 1. Agent 与生命周期

### Agent

能够反复调用模型、执行工具、观察结果并继续工作的运行实体。Codex 中没有一个包办全部逻辑的 `Agent` 类；Agent 行为由 Session、Turn、Step、模型客户端、History 和 Tool Runtime 共同实现。

### Agent Loop

模型和工具之间的循环：构造 Prompt、调用模型、解析 Tool Call、执行工具、记录 Tool Result，再次调用模型，直到得到最终回答。主要入口是 `core/src/session/turn.rs::run_turn()`。

### Thread

一条可持续、可恢复、可 fork 的对话线程。面向调用方的句柄是 `CodexThread`，它接受 `Op` 并产生 `Event`。在产品界面中也可能称为 task 或 conversation。

### Session

Thread 在当前进程中的运行时实例和状态所有者。它保存 History、配置、服务、input queue、active turn 和持久化句柄。一个 Session 可以运行多个 Turn。

### Turn

一次用户任务的生命周期，从用户提交输入开始，到最终回答、错误或中断结束。一个 Turn 内可以调用模型很多次。

### `TurnContext`

一次 Turn 的稳定配置快照，包括模型、provider、reasoning、权限、sandbox、环境选择和动态工具。本 Turn 开始后，这些设置通常不会被中途静默替换。

### Step

一次模型采样以及处理该响应时使用的一致能力边界。一个 Turn 通常包含多个 Step。

### `StepContext`

一次 Step 的动态快照，包含环境 readiness、MCP binding、ToolRouter 和已加载的 AGENTS.md。它保证模型看到的工具与 Runtime 执行的工具来自同一视图。

### Sampling / Sampling Request

一次向模型服务发起生成请求的过程。在一个 Turn 中，第一次请求、每次 Tool Result 后的 follow-up 和某些 Hook continuation 都是独立 sampling。

### Follow-up

当前模型响应后仍需继续采样的状态。常见原因包括执行了工具、加载了新工具、收到 pending input 或 Stop Hook 要求继续。

### Pending Input / Steer

Agent 运行期间用户追加的输入。它先进入队列，在下一个安全采样边界写入 History，而不是直接改写正在进行的模型请求。

### Cancellation Token

Tokio 异步任务之间传播取消的机制。Turn、模型 stream、MCP 等待和 Tool Future 都应观察相应 token。

## 2. Prompt 与模型输入

### Prompt

一次模型请求的结构化中间表示，不是单个字符串。Codex 的 `Prompt` 包含 `input`、`tools`、`base_instructions`、并行工具标记和输出 schema。

### Base Instructions

线程级、相对稳定的基础模型指令，最终通常映射到 Responses API 顶层 `instructions`。它定义 Agent 身份、总体行为和模型特定规则。

### System Instructions

高优先级平台指令的通用称呼。在 Codex 内部和 Responses wire 表示中，不应简单假设所有指令都存为一条 `role=system` 消息；部分基础指令使用顶层 `instructions`。

### Developer Instructions

由应用或运行模式提供、优先于普通用户内容的行为规则。例如协作模式、权限说明、Skill 使用规则和 Plugin 规则。

### User Message

用户明确提交的任务或补充输入。它和 Runtime 自动生成的 contextual user message 都可能使用 user role，但二者具有不同标记和语义。

### Contextual User Message

Runtime 以 user role 注入的结构化上下文，例如 AGENTS.md、环境、Skill 正文和某些 Hook 内容。它不是用户手写消息，会使用稳定包装和 marker 供 History 管理。

### `ResponseItem`

Responses 协议中的结构化历史项目。可以表示消息、reasoning、Tool Call、Tool Result、Tool Search、图片生成和 compaction 等。

### `ContentItem`

Message 内部的内容单元，例如输入文本、输入图片或音频。一个 Message 可以包含多个 ContentItem。

### Input Modality

模型可接受的输入类型，例如文本、图片或音频。`ContextManager::for_prompt()` 会根据当前模型能力移除或调整不支持的媒体。

### Output Schema

用户或调用方要求模型最终输出满足的 JSON Schema。它和 Tool 参数 schema 是两个不同概念。

### Responses API

Codex 当前用于模型推理的结构化 API。它支持 instructions、input items、工具、流式事件、reasoning 和输出 schema。

### Responses Lite

Responses 的一种模型/协议能力分支。该路径可能把基础指令和工具定义作为 developer `ResponseItem` 前缀加入 `input`，而不是使用普通顶层字段。

### Prompt Cache

模型服务对稳定请求前缀的缓存。Codex 通过稳定 Base Instructions、append-only History、确定工具顺序和 World State diff 尽量提高命中率。

### Prompt Cache Key

发送给模型服务、用于关联缓存范围的键。它不是 History 本身，也不能替代客户端的一致性判断。

## 3. Context 与指令来源

### Context

模型在当前请求中可见的信息总和，包括聊天历史、运行环境、仓库指令、Skill、工具结果和动态状态。

### Context Window

模型一次请求能够处理的 token 上限。History、instructions、工具 schema 和输出预算都会占用窗口。

### `ContextualUserFragment`

自动生成上下文片段的统一抽象。具体片段负责渲染文本、声明 role 和稳定 marker，再转换成模型输入 item。

### World State

当前动态运行事实的规范化集合，例如模型、环境、权限、AGENTS.md、Apps 和模式。它支持完整渲染和相对 baseline 的差异渲染。

### World State Snapshot

某一时刻 World State 的不可变表示，用于比较变化、生成 patch 和持久化。

### World State Baseline

模型当前 History 已经知道的 World State 起点。新状态与 baseline 比较后只注入 diff；Compaction 后通常需要重建完整 baseline。

### World State Diff / Patch

新旧 World State 之间的变化。Diff 面向模型渲染为上下文消息；Patch 面向持久化用于恢复状态。

### AGENTS.md

仓库或目录范围内给 Agent 的工作规则。上层文件作用于目录树，更深层文件可以提供更具体的约束。

### Personality

模型的表达和协作风格配置。它可能被烘焙进 Base Instructions，也可能作为动态上下文片段注入。

### Collaboration Mode

例如 Default、Plan 等工作模式。它既可以改变 developer instructions，也可能改变可用工具。

### Realtime Mode

允许实时交互或特殊流式行为的运行模式，其开始和结束可能分别注入上下文。

### Extension / Context Contributor

向 Session、Thread、Turn 或 World State 提供额外 Prompt Fragment、Tool 或状态的扩展接口。

## 4. Skill

### Skill

给模型阅读的任务工作流包，入口通常是 `SKILL.md`。Skill 说明怎样完成某类任务，但通常不直接执行动作。

### `SKILL.md`

Skill 的主说明文件，通常由 YAML front matter 和 Markdown 正文组成。Front matter 提供名称、描述等 metadata；正文提供完整工作流。

### Skill Metadata

无需加载完整正文即可展示和选择 Skill 的摘要信息，包括 name、description、path、scope、policy 和 dependencies。

### Skill Catalog

当前可用 Skill metadata 的集合。模型可见版本受 token/字符预算限制，可能只是 Runtime 完整 Catalog 的子集。

### Skill Root

扫描 Skill 的根位置。不同 root 可以来自用户、仓库、系统、Plugin 或执行环境。

### Skill Scope

Skill 的配置来源和作用范围，例如 Admin、System、User 或 Repo。

### Skill Authority

Skill 资源的所有者和读取边界，例如 Host、Executor 或 Orchestrator。资源必须通过发现它的 authority 继续读取。

### Host Skill

由 Codex 主进程文件系统发现和读取的 Skill。

### Executor Skill

属于某个执行环境文件系统的 Skill。它可能无法通过 Host 的普通本地路径读取。

### Orchestrator Skill

由编排服务或 MCP resource 提供的 Skill，常使用 `skill://...` 资源标识。

### Explicit Skill Invocation

用户通过结构化 Skill input、Skill 路径或 `$skill-name` 明确选择 Skill。

### Implicit Skill Invocation

模型或选择器根据任务与 Skill 描述自动采用 Skill。Skill policy 可以禁止隐式调用。

### Skill Injection

读取被选中 Skill 的完整主正文，并将其作为 `SkillInstructions` 加入当前 Turn 上下文。

### `skills.list` / `skills.read`

用于列出和读取非普通 Host 文件资源的专用工具，保留 authority、package 和 resource identity。

## 5. Tool

### Tool

Runtime 可以执行的结构化动作，例如运行命令、应用补丁、查询 MCP 或创建子 Agent。

### Tool Spec

模型可见的工具声明，包含名称、描述、参数 schema 等。Spec 描述“如何调用”，不包含真正执行代码。

### Tool Schema

通常为 JSON Schema，约束模型生成的参数结构。Runtime 仍需重新校验参数。

### Function Tool

使用 JSON 对象作为参数的普通函数型工具。

### Freeform Tool

输入不是标准 JSON 参数对象，而是自定义文本语法的工具，例如 patch 文本。

### Namespace Tool

在同一 namespace 下组织多个方法的工具表示，例如 `skills.read` 或 `collaboration.spawn_agent`。

### Hosted Tool

由模型服务端直接执行的工具，例如某些 Web Search 能力。客户端提供 spec，但不一定执行对应本地 Handler。

### Dynamic Tool

在启动线程或 Turn 时由调用方动态传入的工具，而不是 Codex 编译时内置工具。

### Deferred Tool

已经在 Runtime 注册、但暂不把完整 schema 暴露给模型的工具。模型通过 Tool Search 找到后再按需加载。

### Tool Search

让模型根据自然语言需求查找 Deferred Tool 的机制。它降低大量外部工具 schema 对上下文窗口的占用。

### Discoverable Tool

用于搜索和推荐的轻量工具 metadata，不一定已经直接对模型可调用。

### Tool Name

工具的稳定身份，由可选 namespace 和 name 组成。内部不应只依赖展示字符串。

### Tool Call

模型请求执行某个工具的结构化输出，包含工具名、call ID 和 payload。

### Tool Call ID

一次工具调用的关联 ID。对应 Tool Result 必须使用相同 ID，不能只靠位置或工具名匹配。

### Tool Payload

内部统一的工具输入，可以是 Function JSON、Custom text 或 Tool Search 参数。

### Tool Result / Tool Output

工具执行结果。它可能同时拥有模型可见、UI、Hook、telemetry 和 Code Mode 等多种视图。

### Tool Registry

本 Step 中 Runtime 已注册、可路由的工具全集。Registry 中的工具不一定全部对模型可见。

### Tool Router

同时持有 Tool Registry 和模型可见 ToolSpec，并负责把模型 ResponseItem 转换成内部 ToolCall 后分发给正确 Runtime。

### Tool Runtime / Handler / Executor

工具的实际执行实现。仓库中因抽象层不同会使用 `CoreToolRuntime`、Handler 或 `ToolExecutor` 等名称。

### Tool Exposure

决定工具如何暴露的策略，例如 Direct、Deferred、Hidden、Code Mode 可用或 Direct-model-only。

### Model-visible Tool Specs

本次 sampling 请求真正发送给模型的工具列表，是 Registry 根据 exposure、模型能力和运行模式过滤后的结果。

### Parallel Tool Calls

模型在一次响应中提出多个工具调用的能力。是否真正并发还要由各 Tool Runtime 的并行安全声明决定。

### Code Mode

允许模型通过受控代码运行时组合多个嵌套工具的模式。某些工具仅在 Code Mode 中可见，某些则只允许直接模型调用。

## 6. MCP、Plugin 与 App

### MCP

Model Context Protocol。Codex 使用它连接外部 server，发现和调用工具、资源及其他能力。

### MCP Server

实现 MCP 协议的外部进程或服务。它可以通过 `tools/list` 提供工具，通过 resources API 提供内容。

### MCP Binding

一个 Step 捕获的 MCP 状态和工具视图。Prompt 工具构建与该 Step 的 MCP 调用共享这份 binding。

### MCP Resource

MCP server 暴露的可读取内容。它与执行动作的 MCP Tool 不同。

### Resource Template

带参数的 MCP resource URI 模板，用于构造具体资源地址。

### Plugin

可安装的能力包，可以组合 Skills、MCP server、Apps、工具和说明。Plugin 本身不是一个可直接调用的 Tool。

### App / Connector

连接 Gmail、Calendar、Drive 等具体外部服务的产品能力，通常包含授权状态和由 MCP 提供的工具。

### Tool Suggest

根据当前用户、Plugin 和 Connector 状态生成外部工具候选或推荐的机制，可与 Tool Search 配合延迟暴露工具。

## 7. 权限与执行安全

### Permission Profile

当前线程或 Turn 的总体权限配置，例如文件系统可读写范围和网络策略。

### Approval Policy

决定什么动作必须请求用户批准、什么动作可以直接执行的策略。

### Escalation

当前 sandbox 权限不足时，请求在更高权限范围内执行具体动作。它必须有明确理由和范围。

### Sandbox

由 Runtime 或操作系统强制执行的资源隔离。Prompt 中的安全说明不是 sandbox；真正安全边界是执行时限制。

### Sandbox Policy

描述文件、网络、进程等实际允许范围的结构化策略。

### Hook

在 Session、Turn、Tool 或 Compaction 生命周期边界执行的扩展逻辑。Hook 可以记录、补充上下文、阻止动作或要求继续。

### Pre-tool-use / Post-tool-use Hook

分别在工具执行前和执行后运行的 Hook。它们消费结构化调用/结果，不应依赖解析 UI 文本。

### Side Effect

会改变外部状态的动作，例如写文件、发送消息、创建事件。Side-effect Tool 的重试和恢复必须考虑幂等性。

### Idempotency

同一操作重复提交不会产生额外副作用的性质。外部 API 可使用 idempotency key；本地工具可以记录 call lifecycle。

## 8. History、Compaction 与持久化

### Conversation History

模型下一次请求可见的结构化 `ResponseItem` 序列，由 `ContextManager` 管理。

### UI Transcript

用户界面展示的消息和事件。它可能包含增量、状态和诊断，不等同于模型 History。

### `ContextManager`

管理模型工作记忆的类型，负责记录、截断、规范化、rollback 和为 Prompt 生成合法历史。

### History Normalization

发送请求前修复 History 结构，例如补充缺失 Tool Result、移除孤立 output，以及按模型能力处理媒体。

### History Version

History 被 compaction、rollback 等操作重写时推进的版本。它帮助增量客户端判断旧状态是否还能复用。

### Truncation

按硬上限缩短单个 Tool Result 或上下文片段。Truncation 通常保留局部内容并明确标记，而不是生成语义摘要。

### Compaction

把较早 History 转换成较短摘要或 replacement history，以开启新的上下文窗口。它是有损但有语义的压缩。

### Local Compaction

客户端构造 compact prompt 并调用模型生成摘要的路径。

### Remote Compaction

由 provider 提供专用 compact endpoint 返回 replacement history 的路径。

### Pre-turn Compaction

在新用户输入进入当前采样前压缩旧历史。

### Mid-turn Compaction

Agent 仍有 Tool follow-up 时进行压缩。它必须保留未完成任务和正确的 item 顺序。

### Context Window ID

标识一次 compaction 前后上下文窗口的 ID，用于 checkpoint、telemetry 和恢复。

### Rollout

Thread 的结构化持久化事件日志，用于恢复、fork、审计和调试。它比模型 History 包含更多运行事实。

### Rollout Item

Rollout 中的一条记录，例如 ResponseItem、TurnContext、WorldState patch 或 Compaction checkpoint。

### Checkpoint

允许恢复过程跳过较早事件重放的完整状态边界，例如 compacted replacement history 加新的 World State baseline。

### Resume

从持久化状态重新创建 live Session，并重建 History、配置和 World State。

### Fork

从现有 Thread 的某个历史点创建拥有独立未来的新 Thread。

### Rollback

结构化地撤回部分 History。它需要同时维护 Tool pair、context fragments、baseline 和 history version。

## 9. 模型、传输与遥测

### Model Provider

模型后端配置和能力抽象，负责 endpoint、认证、provider 能力和 API error 映射。

### `ModelInfo`

描述具体模型能力的权威 metadata，例如 context window、reasoning、输入模态、工具模式、并行调用和基础指令。

### `ModelClient`

线程级模型客户端，持有 provider、认证和共享配置。

### `ModelClientSession`

Turn 级模型连接状态，复用 WebSocket、sticky routing 和 fallback 状态。

### SSE

Server-Sent Events。HTTP 流式 Responses 使用的事件传输形式之一。

### WebSocket

全双工长连接传输。Codex 可以优先使用 Responses WebSocket，并在失败后 fallback 到 HTTP。

### Idle Timeout

等待下一个流事件允许的最长空闲时间，不等于整个请求总时长。

### Retry Policy

定义哪些错误可重试、最大尝试次数和基础退避时间的策略。

### Backoff

重试前的延迟。Codex 使用指数增长并加入随机 jitter，减少大量客户端同步重试。

### Jitter

在退避时间上加入的小范围随机扰动，用于避免重试惊群。

### TTFT

Time To First Token/first event，从发出模型请求到收到首个可用输出的延迟指标。

### Telemetry

请求、采样、工具、Skill、token、重试和错误等运行指标。Telemetry 不应泄露 secrets 或无限外部内容。

## 10. Multi-Agent

### Multi-Agent

由一个父 Agent 创建和协调多个独立子 Agent Thread 的能力。

### Parent Agent / Child Agent

委派任务的一方和接受任务的子线程。两者可以共享工作区，但拥有独立 History。

### Agent ID

Agent 的稳定内部身份，用于状态、消息和控制路由。

### Canonical Task Name

表达 Agent tree 层级的可读名称，例如 `/root/api_review`。它不应替代稳定 Agent ID。

### Spawn Agent

创建子 Agent Thread 并传递任务、上下文继承策略和可选模型配置。

### Fork Turns

控制创建子 Agent 时继承多少父 Thread 历史，例如 none、最近 N turns 或全部。

### Mailbox

Agent 之间异步传递消息的队列。消息在安全采样边界进入接收方上下文。

### Wait Agent

等待子 Agent 状态变化或 mailbox 活动的工具。它应基于通知和 timeout，而不是高频轮询。

### Interrupt Agent

请求取消目标 Agent 当前 Turn，同时保留其 Thread 状态以便后续继续。

### Agent Graph

父子 Agent、状态和路径组成的任务树，可由 `agent-graph-store` 持久化。

## 11. 常见易混淆术语

| 容易混淆的词 | 区别 |
|---|---|
| Thread vs Session | Thread 是持久对话身份；Session 是当前进程中的运行实例 |
| Turn vs Step | Turn 对应一次用户任务；Step 对应一次模型采样能力快照 |
| Prompt vs Instructions | Prompt 是完整结构；Instructions 只是其中一部分 |
| History vs Rollout | History 给模型；Rollout 用于完整恢复与审计 |
| Truncation vs Compaction | Truncation 按长度裁剪；Compaction 做语义摘要和窗口替换 |
| Skill vs Tool | Skill 教模型怎样做；Tool 让 Runtime 真正执行动作 |
| Registry vs Model-visible specs | Registry 是可执行全集；visible specs 是本次公开子集 |
| Tool Search vs Skill Search | 前者加载工具 schema；后者选择工作流说明 |
| Prompt policy vs Sandbox | 前者影响模型选择；后者强制限制实际执行 |
| MCP Tool vs MCP Resource | Tool 执行动作；Resource 提供可读取内容 |
| Plugin vs App | Plugin 是能力安装包；App 是具体服务连接能力 |
| Retry vs Follow-up | Retry 重做失败的模型请求；Follow-up 用新 History 继续下一次推理 |

