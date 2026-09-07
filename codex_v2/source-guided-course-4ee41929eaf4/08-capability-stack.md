# 08：Skills、Plugins、MCP 与 Apps

这几个词经常一起出现，但它们解决的是不同问题。先分清层次，再追踪一次显式提及如何改变 turn。

## 先用一次旅行来类比

假设你要去陌生城市旅行：

- **Skill** 像一份办事攻略：告诉你先订票、再订酒店、最后检查签证；
- **Tool** 像“查询车票”“预订酒店”这些具体动作；
- **MCP** 像一套统一插座，让 Codex 能用相似方式连接不同外部服务；
- **App / connector** 像已经登录并授权的具体服务，例如日历、网盘；
- **Plugin** 像一个安装包，可以把攻略、工具连接和其他配置一起交付。

所以，Skill 更偏“怎样完成任务”，Tool 更偏“现在能执行什么动作”。

## 1. 概念分工

| 概念 | 主要回答 | 典型模型可见内容 | Runtime 侧内容 |
|---|---|---|---|
| Skill | 某类任务应该怎样做 | 工作流说明、约束、资源入口 | catalog、provider、读取与注入状态 |
| Plugin | 一组能力如何安装和归属 | plugin 说明或能力摘要 | manifest、skills、MCP servers、apps 等组合 |
| MCP | 如何连接外部工具与资源 | tool specs、resource tools | client、catalog、timeout、approval 元数据 |
| App/connector | 用户授权的外部服务能力 | 可发现/可调用工具 | connector ID、授权与 policy 状态 |
| Tool | 模型此刻可采取什么动作 | 名称、描述、参数 schema | handler/runtime 与输出转换 |

Skill 是工作方法，不等于可执行函数；plugin 是分发和归属单元，不等于单个 MCP server；app 是产品层的授权能力，最终可能通过 MCP 工具进入当前 step。

## 2. 显式提及怎样影响 turn

`run_turn()` 先调用 `required_mcp_servers_for_input()`。它从用户输入中收集：

- 明确提及的 plugin 及其 MCP server；
- `mcp://server` 路径；
- 显式 skill 的 MCP tool dependencies；
- skill 所属 plugin 的 MCP servers；
- app/connector 的可访问状态。

这些 server 名称用于捕获第一份 step，使所需连接有机会在构造 tool catalog 前准备好。

随后 `build_skills_and_plugins()`：

1. 从 turn 的 skills snapshot 识别明确提及的 skills；
2. 必要时提示/安装 MCP 依赖；
3. 将选中 skill 渲染为有界 `ContextualUserFragment`；
4. 根据 plugin 和可访问 connectors 构造注入项；
5. 合并 extension 提供的 turn input fragments；
6. 记录明确启用的 connector selection。

### 一个具体例子

假设用户调用 `$release-check`：

1. Codex 找到名为 `release-check` 的 Skill；
2. 这个 Skill 声明它依赖名为 `release-system` 的 MCP 服务；
3. Step 创建时准备好对应的工具绑定；
4. Skill 文档告诉模型：先查构建状态，再查阻塞问题，最后生成摘要；
5. 模型按这个顺序调用工具；
6. MCP 服务把调用转发到真实的发布系统。

一句话概括：**Skill 决定“怎样做”，MCP 和 Tool 提供“用什么做”。**

## 3. 为什么依赖 StepContext

`StepContext` 同时冻结：

- environment snapshot；
- selected capability roots；
- executor capability discovery；
- `McpBinding`；
- 已最终确定的 `ToolRouter`；
- 当前观察到的 `AGENTS.md`。

这保证模型看到的工具、实际执行的工具和环境权限属于同一 request-scoped 视图。

`McpBinding` 不是简单的工具名称列表。它包含冻结 catalog 和 prepared calls；`PreparedMcpCall` 绑定具体 client、tool metadata、timeout/config、catalog revision 和 plugin attribution。即使全局 MCP runtime 后来刷新，当前调用仍能按当前 step 的身份和元数据解释。

## 4. Catalog 与正文按需展开

将所有 skill 正文一次性塞进 prompt 会带来无界上下文和 cache churn。Skills extension 因此区分：

- 可用 skill catalog 的有界展示；
- 显式选择后的 skill instructions；
- host、executor、orchestrator 等不同 authority 的读取路径；
- session/thread/turn/step 不同作用域的状态与缓存。

学习时应重点观察“何时只给目录，何时注入全文”，而不是只看 `SKILL.md` 的解析函数。

Catalog 很像一本书的目录：先告诉模型有哪些章节；真正需要某一章时，再读取详细内容。这样既节省上下文，也避免一次给模型太多无关信息。

## 5. Tool 暴露计划

`build_tool_router()` 汇总 core tools、MCP tools、extension tools、动态工具以及 feature/config 决策。工具还可能具有不同 exposure：直接暴露、deferred search 或 code mode。注册成功不意味着一定直接出现在本次模型请求中。

## 6. 信任边界

- 用户显式提及可以选择能力，但不能绕过 workspace/admin policy。
- Skill instructions 会影响模型行为，但有副作用的动作仍需 tool runtime。
- MCP catalog 来自外部连接，必须与具体 binding/revision 绑定。
- Connector 可见性同时受授权、app enabled 状态和 config layer policy 影响。
- Guardian review 输入被视为不可信证据，不从嵌入文本中再次触发 skill/plugin 注入。

## 7. 常见误解

- **“Skill 就是一段自动执行的程序。”** 不准确。Skill 首先是给模型看的任务说明，可能引用脚本或工具，但它本身不等于工具执行。
- **“装了 Plugin 就拥有所有权限。”** 不对。Plugin 提供能力，实际访问仍受登录授权、工具策略、审批和沙箱限制。
- **“MCP 是某一个具体外部服务。”** 不完全对。MCP 是连接协议；具体 MCP server 才承载某组服务和工具。

## 读完后自测

1. 为什么“教模型怎样查发布状态”和“真的读取发布状态”不是同一层能力？
2. Plugin 被安装后，为什么工具调用仍可能要求审批或失败？
3. Catalog 为什么只先展示目录，而不是一次注入所有工具定义？

## 本章词汇表

| 词语 | 直译 | 在能力系统中的意思 |
|---|---|---|
| Capability | 能力 | Codex 当前可以说明、发现或执行的某项功能 |
| Catalog | 目录 | 有界展示可用 Skill/Tool 的清单 |
| Binding | 绑定 | 把工具元数据固定关联到具体 MCP client 和 revision |
| Prepared call | 已准备调用 | 已解析目标、超时和元数据，可按当前 Step 身份执行的调用 |
| Exposure | 暴露方式 | 工具直接可见、延迟发现或仅由 Code Mode 使用 |
| Authority | 权威来源 | Skill/资源来自 host、executor 或 orchestrator 等哪一侧 |
| Attribution | 归属标记 | 记录能力来自哪个 Plugin 或服务 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

1. 阅读 `required_mcp_servers_for_input()`，列出四种 server 来源。
2. 阅读 `StepContext` 的全部字段，并为每个字段写一句“为什么必须同 step”。
3. 阅读 `McpBinding::prepare_call()`，解释为何返回 prepared call 而不是裸 client。
4. 在 `core/tests/suite/skills_extension.rs` 和 `mcp_tool_exposure.rs` 各找一个显式选择测试。
