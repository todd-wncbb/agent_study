# 08. MCP、Plugin 与 App

## 1. 三者不是同一个概念

| 概念 | 主要作用 |
|---|---|
| MCP | 连接外部 server，发现并调用工具、资源或 prompts |
| Plugin | 把 Skills、MCP 配置、Apps 和其他扩展元数据打成一个安装单元 |
| App/Connector | 面向具体外部服务的连接能力，通常依赖 MCP 工具和授权 |

它们会相互组合，但 Runtime 仍需保留来源身份和权限边界。

## 2. MCP 生命周期

Session 不应该在每次 Tool Call 时临时创建 MCP client。当前实现包含：

- session 级 MCP runtime/manager；
-启动预热；
-按本轮 required servers 等待 readiness；
-每 Step 捕获 `McpBinding`；
-配置或连接变化后的刷新。

相关入口：

- [`session/mcp.rs`](../codex-rs/core/src/session/mcp.rs)
- [`session/mcp_prewarm.rs`](../codex-rs/core/src/session/mcp_prewarm.rs)
- [`session/mcp_refresh.rs`](../codex-rs/core/src/session/mcp_refresh.rs)

## 3. MCP Tool 发现

MCP server 返回的工具信息需要转换成 Codex Runtime 可理解的对象：

```text
MCP tools/list
  → server/tool identity
  → JSON schema 与 annotations
  → MCP Tool Runtime
  → ToolRegistry
  → 直接或 Deferred exposure
```

[`mcp_tool_exposure`](../codex-rs/core/src/mcp_tool_exposure.rs) 和 [`tools/handlers/mcp.rs`](../codex-rs/core/src/tools/handlers/mcp.rs) 负责重要转换与执行路径。

## 4. 为什么要命名空间

多个 MCP server 都可能提供 `search`。如果都注册成平面名称：

-会冲突；
-模型无法判断来源；
-权限和 telemetry 难以归因；
-恢复时工具身份不稳定。

因此工具身份至少应包含 server/namespace 与 tool name。即使最终 wire schema因兼容性被扁平化，内部身份仍应保持结构化。

## 5. MCP Resource

Resource 与 Tool 不同：

- Tool 执行动作或查询；
- Resource 表示可读取内容；
- Resource template 表示带参数的资源地址模板。

Codex 提供 `list_mcp_resources`、`list_mcp_resource_templates` 和 `read_mcp_resource` 等 Handler，让模型在需要时读取资源，而不是把全部资源正文预注入 Prompt。

## 6. Plugin 注入

Plugin 可以影响两条链：

```text
上下文链：Plugin instructions / Skill catalog → Prompt
能力链：MCP / App / Tool contributor → ToolRegistry
```

用户明确 mention Plugin 时，`run_turn()` 会识别它，准备依赖并把 Plugin 注入项记录到 History。未安装但推荐的 Plugin 可能通过专门工具请求安装批准；Runtime 不能因为模型认为有用就静默改变安装状态。

## 7. App/Connector

Apps 通常需要：

-用户或工作区授权；
-可访问 connector 列表；
-enable/disable 配置；
-对应 MCP tools；
-面向模型的使用说明。

Step 构建工具时会把 Plugin connector snapshot 与当前可访问 MCP connector 合并，再考虑 App enablement。模型最终只看到当前用户实际可用的能力。

## 8. Lazy Loading 与 Tool Suggest

外部生态中的工具数量可能非常大。Codex 可以先构造 `DiscoverableTool` 候选，只展示搜索/推荐入口；模型发起 `tool_search` 后再暴露具体 schema。

候选可能来自：

-已加载 Plugin；
-可访问 App connectors；
-服务端推荐 endpoint；
-配置允许的其他发现源。

这种延迟加载同时降低 token 成本和无关能力干扰。

## 9. Step 一致性

MCP/App 动态变化仍必须遵守 Step 快照：

1. 捕获 Step 时取得 MCP binding；
2. 用该 binding 构建 ToolRouter；
3. Prompt 使用 Router 的 model-visible specs；
4.返回的 Tool Call 使用同一个 Router 执行。

新连接或刷新结果留到下一 Step 生效。

## 10. 故障处理

外部能力常见失败包括：

-server 启动超时；
-授权过期；
-tools/list schema 无效；
-调用中断或返回协议错误；
-工具在相邻 Step 间消失；
-Resource 分页读取不完整。

处理原则：

-启动错误形成有界 warning，不无限污染 Prompt；
-必须能力不可用时向用户说明；
-非必须能力失败允许 Agent 使用替代方案；
-错误附带 server/tool identity；
-不要把外部返回内容当成高优先级可信指令；
-下一 Step 可以刷新，但当前 Step 保持一致。

## 11. 设计建议

实现扩展生态时，应建立统一的 Capability Descriptor，至少包含：

-稳定来源 ID；
-名称和描述；
-输入/输出 schema；
-授权状态；
-exposure；
-读取或执行 authority；
-版本与刷新时间；
-风险或 side-effect annotations。

这样 Prompt、Registry、权限和 telemetry 才能共享同一身份系统。

