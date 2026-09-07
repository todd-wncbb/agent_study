# 精读 10：MCP tool call 生命周期——外部工具怎样被发现、授权、调用并回到模型

> 源码基线：`4ee41929eaf4`  
> 运行时总入口：`codex-rs/codex-mcp/src/runtime.rs`  
> 连接与目录：`codex-rs/codex-mcp/src/connection_manager.rs`、`connection_manager/tool_catalog.rs`  
> Step 绑定：`codex-rs/codex-mcp/src/binding.rs`  
> 模型工具注册：`codex-rs/core/src/mcp_tool_exposure.rs`、`core/src/tools/spec_plan.rs`  
> 调用主函数：`codex-rs/core/src/mcp_tool_call.rs::handle_mcp_tool_call`  
> 前置阅读：[MCP 生命周期主题章](../30-mcp-lifecycle.md)、[精读 04：Turn 主循环](04-turn-main-loop.md)、[精读 05：工具分派](05-tool-dispatch.md)

[OpenAI 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp)说明了公开边界：MCP 把模型连接到第三方工具和上下文；Codex 支持本地 stdio 与 Streamable HTTP server，初始化会读取 server instructions；配置可以设置 required、启动和调用超时、工具 allow/deny list，以及 server/单工具审批模式。本篇只用官方文档界定公开行为，内部快照、revision、handler 和结果回填顺序以固定提交源码为准。

## 1. 先说人话：MCP server 连接成功，还不等于模型能安全调用工具

假设公司工单 MCP server 提供：

```text
server = tickets
tool   = get_ticket
```

用户看到“tickets server 已连接”以后，可能以为执行过程只有：

```text
模型想调用 get_ticket
-> Codex 把请求转发给 tickets
```

实际链路要长得多：

```text
配置和认证
-> 建立 transport
-> initialize 握手
-> tools/list 发现
-> allow/deny 与 visibility 过滤
-> 名称规范化
-> 为一个 Step 冻结工具目录和 exact client
-> 工具 schema 交给模型
-> 模型生成 tool call
-> ToolRouter 找到 McpHandler
-> 参数 JSON 解析
-> 再确认工具仍可执行
-> 计算 approval policy
-> 用户 / Guardian / hook 决策
-> catalog revision 再检查
-> tools/call
-> 结果净化、截断和 UI 事件
-> FunctionCallOutput 回到下一次模型采样
```

一句话心智模型：

> MCP 负责外部协议；Codex 还要负责“本 Step 看见什么、以哪条连接执行、谁授权、结果怎样安全进入上下文”。

## 2. 贯穿案例：读取工单后发表评论

假设 server 声明两个工具：

```json
{
  "name": "get_ticket",
  "description": "Read one ticket",
  "annotations": { "readOnlyHint": true }
}
```

```json
{
  "name": "add_comment",
  "description": "Add a public comment",
  "annotations": {
    "readOnlyHint": false,
    "openWorldHint": true
  }
}
```

用户说：

```text
读取 INC-42，然后留言“已定位问题”
```

我们将追踪两次调用：

1. `get_ticket` 通常可按只读策略自动执行；
2. `add_comment` 会根据 server/tool approval mode、annotations、当前 permission policy 和 reviewer 决定是否询问。

贯穿问题是：

```text
模型在采样时看到的那一个 add_comment，
怎样保证执行时仍然是同一个 server、同一个目录版本和同一套审批权威？
```

答案主要藏在：

```text
McpRuntime
McpBinding
PreparedMcpCall
catalog_revision
```

## 3. 先分清六个对象

| 对象 | 生命周期 | 它拥有或冻结什么 |
|---|---|---|
| `McpRuntime` | thread | 当前发布的 MCP 状态，可被刷新替换 |
| `McpConnectionSet` | 一次 runtime publication | 多个 server 的连接、过滤器、目录 revision |
| `ManagedClient` | 某个 server connection | 协议 client、已发现 tools、server info、timeout |
| `McpBinding` | 一个或多个兼容 Step | 冻结的目录、exact clients、config、prepared calls |
| `PreparedMcpCall` | 一个可执行工具身份 | exact client、原始 tool、审批元数据、revision |
| `McpHandler` | ToolRegistry 条目 | 模型可见 ToolSpec 与调用适配代码 |

方向是：

```text
McpRuntime
  └─ PublishedMcpRuntime
       └─ McpConnectionSet
            └─ capture -> McpBinding
                          ├─ tools: Vec<ToolInfo>
                          └─ calls: (server, tool) -> PreparedMcpCall
                                      │
                                      └─ McpHandler 执行时使用
```

`McpHandler` 不是网络连接；`ToolInfo` 也不是执行权限。真正的调用能力在 `PreparedMcpCall` 中。

## 4. 第一阶段：配置先投影成 `McpRuntimeInput`

Session 初始化或刷新时，不直接把原始 `config.toml` 交给连接层，而是构造：

```rust
McpRuntimeInput
```

它包含：

- MCP config；
- 计算后的 `EffectiveMcpServer` 集合；
- plugin 是否可用；
- 当前认证；
- environment manager 与 cwd；
- tool catalog cache；
- Codex Apps tools cache；
- event sender；
- startup cancellation token；
- elicitation reviewer/lifecycle；
- client MCP extensions。

为什么叫 input，而不是 config？因为同一份静态配置，在不同 thread 中还要结合：

```text
当前身份
当前执行环境
当前 workspace/cwd
当前 plugin 选择
当前权限配置
当前 capability roots
```

才能得到真正可运行的连接集合。

## 5. `McpRuntime` 为什么属于 thread

源码注释直接给出角色：

```rust
/// Owns all mutable MCP state for one Codex thread.
```

它的核心字段是：

```rust
current: ArcSwap<PublishedMcpRuntime>
```

可以把 `ArcSwap` 理解为一个能够原子更换的共享指针：

```text
旧 PublishedMcpRuntime ── 被正在运行的 Step 持有

McpRuntime.current
        │ 原子替换
        ▼
新 PublishedMcpRuntime ── 被后续 Step 捕获
```

这允许两件事同时成立：

- 新配置能够发布；
- 已经开始的 Step 不会在执行一半时突然换 client。

## 6. `replace` 不是无条件全部重连

`McpRuntime::replace` 先读取当前 publication，然后把旧 `McpConnectionSet` 作为 `previous` 交给新集合：

```text
连接身份相同且仍健康
-> 可能复用旧 client

连接配置、环境或凭据变化
-> 建立新 client
```

连接复用比较的不只是 server name，还包括 `McpServerConnectionIdentity`。同名 server 若从本地环境切到远端环境，不能误复用原进程。

`replace_fresh` 则明确不提供 previous，并在发布后 hard refresh Codex Apps catalog。

## 7. Publication gate 为什么存在

构造 `McpConnectionSet` 时，server startup task 可能立刻开始产生状态事件。但新 connection set 尚未存入 `McpRuntime.current`。

如果先发 Ready，再发布 runtime，观察者可能收到：

```text
事件说 server 已 Ready
但 current 仍指向旧连接集合
```

因此 `McpPublicationGate` 让 startup task 等待：

```text
构造 connections
-> current.store(new publication)
-> publish.send(true)
-> startup 状态事件才允许对外出现
```

它维护的是“状态事件与可访问对象的发布顺序”。

## 8. 第二阶段：每个 server 怎样启动

`McpConnectionSet::new` 为每个有效 server 建立 `McpServerView`。其中分开保存：

```text
connection    协议 client 和启动状态
metadata      approval、environment、origin 等
tool_filter   enabled_tools / disabled_tools
tool_timeout  单次工具调用上限
catalog limit 目录最大项目数
```

这比把全部内容塞进 client 更清晰：连接负责通信，view 负责“当前 thread 怎样使用这条连接”。

### Eager 与 LazyWhenCached

根 Session 通常使用 `Eager`；subagent 使用 `LazyWhenCached`。

Lazy 条件不是只看“有缓存”，还要求：

- server 不是 required；
- 它不是本次显式选中的 plugin server；
- 缓存中至少有一个通过 filter 且 model-visible 的工具。

缓存只能让发现先进行；真正调用仍要唤醒 server 并取得 exact client。

## 9. stdio 与 Streamable HTTP 在哪里分叉

`make_rmcp_client` 根据 transport config 选择：

```text
Stdio
  -> 启动本地或远端 executor 中的子进程
  -> stdin/stdout 传协议
  -> stderr 作为日志

StreamableHttp
  -> 解析 URL、header 和认证来源
  -> 使用对应环境的 HTTP client
  -> 建立远程 transport
```

两条路径最终都包装为统一 `RmcpClient`，所以上层的 initialize、tools/list 和 tools/call 不需要重复写两套业务逻辑。

## 10. 第三阶段：initialize 先于 tools/list

startup 的主顺序是：

```text
make_rmcp_client
-> client.initialize(...)
-> 读取 ServerCapabilities / server_info / instructions
-> list_tools_for_client_uncached
-> 构造 ManagedClient
```

initialize 回答“双方能怎样说话”；tools/list 回答“这个 server 现在提供什么工具”。

### Server instructions

server 返回的 `instructions` 会随每个 `ToolInfo` 的构造进入 namespace/tool 指导。它适合描述跨工具的共同约束，例如：

```text
先 search 再 get
每分钟最多调用 20 次
写操作前必须读取最新版本
```

instructions 不是工具执行权限，也不能覆盖 Codex 的安全策略。

## 11. tools/list 不是只请求一页

`list_tools_for_client_uncached` 使用：

```text
collect_paginated_with_limit("tools/list", ...)
```

它要处理：

- pagination cursor；
- protocol mode 差异；
- startup/tool timeout；
- catalog item hard limit；
- Codex Apps 与普通 MCP 的 metadata 差异。

有界目录很重要。一个恶意或错误 server 不能通过无限分页把无界工具定义注入模型上下文。

## 12. 原始 tool 怎样变成 `ToolInfo`

server 原始声明大致包含：

```text
name
title
description
inputSchema
outputSchema
annotations
meta
```

Codex 包装成 `ToolInfo` 后还补充：

- `server_name`；
- callable namespace/name；
- connector id/name/description；
- server instructions；
- plugin provenance；
- supports parallel tool calls；
- server origin/environment metadata。

这里有两套名称：

```text
原始 MCP 名称
  server=tickets, tool=get_ticket
  真正发送 tools/call 时使用

模型可见名称
  mcp__tickets__get_ticket
  ToolRouter 与模型 API 使用
```

## 13. 名称规范化解决什么问题

不同 server 可能都声明 `search`，名称还可能包含空格、斜线或超长字符。

规范化会：

- 清理模型 API 不接受的字符；
- 加 server namespace；
- 默认加入 `mcp__` 前缀；
- 处理规范化后的碰撞；
- 限制长度；
- 保留原始身份用于调用。

因此不能从模型名称靠字符串猜原始 server/tool，然后直接调用。绑定里已经保存了准确映射。

## 14. 工具过滤发生不止一次

### 连接目录过滤

`ToolFilter` 先应用 `enabled_tools` allow-list，再应用 `disabled_tools` deny-list。

```text
enabled_tools = [get_ticket, add_comment]
disabled_tools = [add_comment]

最终只允许 get_ticket
```

### model visibility

如果 MCP UI metadata 明确声明 visibility，但其中不包含 `model`，则工具不能放进 model-facing declarations。

### Codex Apps policy

Apps 工具还要通过 connector id 与管理配置的 enabled policy。

### exposure policy

工具可能是：

```text
Direct    直接出现在模型请求中
Deferred  先通过 tool search 发现
Hidden    不向当前表面暴露
```

所以：

> tools/list 返回工具，只证明 server 声明了它；不证明当前模型请求一定包含它。

## 15. Required server 与 Optional server

required server 初始化失败，会让 Session 初始化失败。`install_initial_mcp_runtime` 发布 runtime 后调用：

```rust
validate_required_servers()
```

optional server 的原则不同：

- 不让整个 thread 无限等待；
- 可给一段共享 startup grace；
- 未及时 ready 时本 Step 可以省略；
- 后续 Step 可在恢复后看到它。

因此同一 Turn 的不同 sampling Step，工具列表也可能不同。Step 是动态状态快照的边界。

## 16. 第四阶段：捕获 `McpBinding`

`McpRuntime::current_binding_with_required_servers` 从最新 publication 捕获绑定。

如果所有连接已经稳定，并且 `catalog_revision` 未变化，runtime 可以复用 cached binding。否则重新调用：

```rust
capture_binding_with_metadata(...)
```

`McpBinding` 的定义非常值得逐字读：

```rust
pub struct McpBinding {
    connections: Arc<McpConnectionSet>,
    clients: Arc<McpBindingClients>,
    config: Arc<McpConfig>,
    plugins_available: bool,
    tools: Vec<ToolInfo>,
    calls: HashMap<(String, String), PreparedMcpCall>,
}
```

它同时冻结“模型目录”和“执行映射”。

## 17. 为什么缓存 binding 要检查 revision

假设上次 binding 包含：

```text
revision=4
tools=[get_ticket, add_comment]
```

目录 hard refresh 后变成：

```text
revision=5
tools=[get_ticket]
```

如果只按 connection pointer 缓存，旧 binding 仍会把 `add_comment` 放进新 Step。`CachedMcpBinding` 因此保存：

```text
catalog_revision + binding
```

只有 revision 相同才可复用。

## 18. `capture_binding_with_metadata` 为什么持有 revision 读锁

捕获过程需要保持：

```text
列出的工具
创建的 PreparedMcpCall
记录的 revision
```

来自同一个目录版本。

因此它取得 `tool_catalog_revision.read()`，在完成目录和 call map 构造前不让 hard refresh 写锁插入。

否则可能出现：

```text
读取旧 tools
目录刷新
以新 revision 创建旧 tools 的 prepared calls
```

这是一次典型的原子快照边界。

## 19. 缓存工具为什么不一定能调用

pending optional server 可以先贡献 cached tool metadata，让发现表面知道“可能有这个工具”。但若没有 exact ready client：

```text
ToolInfo 可能被保留用于目录/发现
PreparedMcpCall 不会创建
```

`capture_binding_with_metadata` 对 exact ready client 做二次确认。真正 model-visible 且可执行的 direct 工具必须有 call 映射；找不到时会省略或在调用时 fail closed。

缓存不是权限，也不是活连接。

## 20. `PreparedMcpCall` 冻结哪些权威

它保存：

- 维持旧 connection set 生命周期的 `Arc`；
- exact `ManagedClient`；
- 当时的 `McpConfig`；
- 捕获时的 catalog revision；
- revision source；
- 原始 `ToolInfo`；
- server metadata；
- plugin id；
- 是否为本次显式选择的 plugin server。

可以把它理解为：

```text
不是“以后去找一个名叫 add_comment 的工具”
而是“允许按这份目录、这条连接、这套策略准备 add_comment”
```

## 21. 第五阶段：Step 同时冻结 binding 和 ToolRouter

`StepContext` 明确保存：

```rust
mcp: Arc<McpBinding>
tool_router: Arc<ToolRouter>
```

捕获顺序是：

```text
mcp_runtime_for_step
-> 得到 McpBinding
-> built_tools(..., &mcp)
-> build_tool_router
-> append_mcp_tools
-> 得到同一 Step 的 ToolRouter
```

这保证模型看到的工具 spec 与 runtime handler 都来自同一 binding。

## 22. `McpHandlerCache` 缓存的是什么

把 ToolInfo 转成 JSON Schema ToolSpec 有成本，所以 Session 缓存 `McpHandler`。

缓存键不是只有工具名。`CachedMcpHandlers` 还弱引用整个 binding：

```text
如果 binding pointer 不同
-> 清空 handler cache
```

这避免新目录沿用旧 ToolInfo 构造的 handler。

handler 自身包含：

```text
ToolInfo
ToolSpec
Code Mode tool definitions cache
```

它不保存 secret 或 OAuth token。

## 23. MCP 工具怎样进入 `ToolRegistry`

`append_mcp_tools` 先把普通 MCP tools 与 Apps tools 分开过滤，再为每个工具建立 `McpHandler`，最后注册 exposure：

```text
tool search 开启 -> Deferred
否则            -> Direct
超出 agent plugin spec budget -> Hidden
```

agent plugin MCP spec 还有单工具和总字节预算：

```text
MAX_AGENT_PLUGIN_MCP_SPEC_BYTES
MAX_AGENT_PLUGIN_MCP_TOTAL_BYTES
```

这再次防止外部目录无界占用模型上下文。

## 24. 模型获得什么，拿不到什么

模型请求中的 ToolSpec 包含：

- 模型可见工具名；
- description；
- input schema；
- namespace description；
- 可能的并行提示。

模型拿不到：

- HTTP bearer token；
- OAuth refresh token；
- stdio child handle；
- exact client；
- approval cache；
- catalog lock。

模型只有“提出调用”的能力，没有直接执行权。

## 25. 第六阶段：模型返回 tool call

教学示例：

```json
{
  "call_id": "call-7",
  "name": "mcp__tickets__get_ticket",
  "arguments": "{\"ticket_id\":\"INC-42\"}"
}
```

通用 `ToolRouter` 用模型名称找到 `McpHandler`。Handler 保留的 `ToolInfo` 告诉下游真实身份：

```text
server=tickets
tool=get_ticket
```

然后调用：

```rust
handle_mcp_tool_call(...)
```

## 26. `handle_mcp_tool_call` 第一关：解析 arguments

模型返回的 arguments 是字符串。函数处理：

```text
空白字符串 -> None
合法 JSON  -> serde_json::Value
非法 JSON  -> 立即返回错误 CallToolResult
```

非法 JSON 不会发给 server。

为什么有 schema 仍要解析？因为 schema 是生成约束，不是可以完全信任的安全证明；流式模型输出、旧模型或异常响应仍可能产生无效文本。

## 27. 第二关：再次准备 exact call

函数调用：

```text
Session::prepare_mcp_call(server, tool)
-> refresh_mcp_if_dirty()
-> wait_for_server_startup(server)
-> current_binding_for_call(server)
-> binding.prepare_call(server, tool)
```

找不到时产生用户可见失败：

```text
MCP tool `tickets/get_ticket` is not available to the model
```

这里不是从全局目录“尽量找一个同名工具”。它按精确 `(server, tool)` 查找。

## 28. 为什么调用时会捕获一个新 binding

Step 已经有 `step_context.mcp`，为什么 `Session::prepare_mcp_call` 还会看 runtime 最新状态？

固定提交的设计把执行 admission 放在最新 runtime 上：

- dirty config 先 refresh；
- lazy server 先等待 startup；
- 根据当前 publication 捕获可执行 call；
- catalog revision 仍防止准备后目录再变化。

同时，`PreparedMcpCall` 一旦取得，就绑定 exact client；后续 refresh 不会把它偷偷 reroute 到另一条连接。

测试 `prepared_call_keeps_captured_connection_and_authority_after_refresh` 验证这一点。

## 29. 第三关：Apps policy 与普通 MCP policy 分叉

普通 MCP server 的 approval mode 来自捕获的 server metadata：

```text
server default
-> per-tool override
```

Codex Apps 工具还要重新计算 `AppToolPolicy`，输入包括：

- connector id；
- tool name/title；
- destructive hint；
- open-world hint；
- config layer stack。

如果 Apps policy 已禁用工具，函数在发 Started 之前 fail closed，并报告：

```text
MCP tool call blocked by app configuration
```

## 30. annotations 是提示，不是安全证明

常见 annotations：

```text
readOnlyHint
destructiveHint
openWorldHint
```

例如：

```text
get_ticket:
  readOnlyHint=true

add_comment:
  readOnlyHint=false
  openWorldHint=true
```

`writes` 审批模式通常对非只读工具询问。但 server 可以错误标注，所以 Codex 还结合 server policy、permission mode、Guardian 和 hooks。

annotations 是风险信号，不是能力隔离边界。

## 31. Started 事件为什么在审批前发

通过基本 availability/app policy 检查后，Core：

1. 注册 approval metadata；
2. 发 `McpToolCallItem(InProgress)`；
3. 进入审批。

这样 UI 能显示：

```text
准备调用 add_comment
等待审批……
```

如果用户拒绝，同一个 call id 会收到 Failed/skip 终态，而不是工具从 UI 中凭空消失。

## 32. Approval decision 有哪些结果

内部决策包括：

```text
Accept
AcceptForSession
AcceptAndRemember
Decline { message }
Cancel
```

它们回答两件不同的事：

```text
这一次是否执行？
决定是否可在 Session 或持久配置中复用？
```

是否允许 remember 还受 server 类型、审批模式和策略限制。不是每个提示框都能把许可永久保存。

## 33. Hook、Guardian 和用户审批的关系

`maybe_request_mcp_tool_approval` 可以综合：

- 已记住的 approval；
- permission request hooks；
- Guardian review；
- 用户 elicitation / request user input；
- full-access 或特定 auto-approve 条件。

不能简单画成固定的“永远先用户、再 hook”。具体模式会选择不同路径，但最终都投影成 `McpToolApprovalDecision`。

拒绝不是 Rust panic，也不是 transport failure；它会转成模型可见的工具失败结果，让 Agent 决定怎样继续。

## 34. 最关键的竞态保护：`call_with_preparation`

`PreparedMcpCall::call_with_preparation` 做：

```text
取得 catalog revision 读锁
-> 比较 current revision == captured revision
-> 执行不可逆 preparation
-> exact client.tools/call
-> 释放读锁
```

若 revision 已变化：

```text
tool call rejected because the catalog changed after
`tickets/add_comment` was prepared
```

### 为什么 preparation 也要放在锁里

preparation 可能：

- 写入 session/persistent approval；
- 标记 thread memory polluted；
- 重写 OpenAI file 参数；
- 构造 trace 和 sandbox metadata。

若先做这些副作用，再发现目录过期，就太晚了。

测试 `stale_prepared_call_does_not_run_preparation` 明确验证：过期 call 连 preparation closure 都不运行。

### 为什么锁要覆盖真实调用

若只比较后立即释放：

```text
revision 检查通过
-> refresh 删除工具
-> 旧 call 仍发出
```

读锁覆盖 `tools/call`，hard refresh 需要写锁，因此二者形成明确顺序。

## 35. 调用前参数与 metadata 会怎样处理

在 catalog lease 内，Core 可能：

1. 应用并记住 approval decision；
2. 标记 memory mode polluted；
3. 将 OpenAI file 参数改写成 server 需要的表示；
4. 加入 turn、call、thread metadata；
5. 加入与 server environment 对应的 sandbox state；
6. 建立 rollout thread trace metadata。

这解释了为什么最终 MCP wire 请求不只是模型生成的 arguments。

模型不能自行伪造 Host 管理的 thread id、sandbox state 或审批记录。

## 36. 第七阶段：真正发送 `tools/call`

最终调用：

```rust
client.call_tool(tool_name, arguments, meta, tool_timeout)
```

注意四点：

- 使用原始 MCP tool name；
- 使用 captured exact client；
- 使用当前 server view 捕获的 tool timeout；
- error 会添加 `server/tool` context。

协议示意：

```json
{
  "method": "tools/call",
  "params": {
    "name": "get_ticket",
    "arguments": { "ticket_id": "INC-42" },
    "_meta": { "...": "Host-managed metadata" }
  }
}
```

这只是教学形状，不是完整 wire fixture。

## 37. 两种失败必须分开

### transport / protocol `Err`

例如：

- server connection closed；
- timeout；
- OAuth 失败；
- JSON-RPC 错误；
- catalog changed；
- preparation 失败。

这时没有正常 `CallToolResult`。

### `CallToolResult.is_error = true`

例如 server 正常处理请求，但业务上工单不存在：

```json
{
  "content": [{ "type": "text", "text": "ticket not found" }],
  "isError": true
}
```

通信成功，业务失败。UI 会把 item 标为 Failed，但仍可保留 server 返回内容。

## 38. `CallToolResult` 怎样转换

RMCP result 被转换成 Codex protocol：

```text
content             -> JSON content blocks
structured_content  -> 结构化 JSON
is_error            -> 业务错误标记
meta                -> 可序列化 metadata
```

content block 可能是：

- text；
- image；
- audio；
- embedded resource；
- resource link。

## 39. 模型不支持某种模态时怎么办

`sanitize_mcp_tool_result_for_model` 检查模型 input modalities。

若模型不支持 image：

```text
image block
-> <image content omitted because you do not support image input>
```

audio 同理。

这不是把整个工具调用判为失败；它只把模型不能消费的 block 替换为说明文字。UI/原始结果与模型上下文仍可能使用不同投影。

## 40. UI 事件为什么单独截断

调用完成后，Core 发送 `McpToolCallItem`。App-server 会从 rollout 中重建这些 Items，因此不能把数 MB 的 result 原样写进事件。

`truncate_mcp_tool_result_for_event`：

```text
小结果 -> 保留结构
大结果 -> 序列化整体，截成文本 preview
          structured_content=None
          meta=None
```

这是 UI/event storage 预算，不等于模型结果预算。

## 41. `McpToolOutput` 怎样回到模型

Handler 把结果包装为：

```rust
McpToolOutput {
    result,
    tool_input,
    wall_time,
    original_image_detail_supported,
    truncation_policy,
}
```

其 `to_response_item` 生成：

```rust
ResponseInputItem::FunctionCallOutput {
    call_id,
    output,
}
```

`response_payload` 还会：

- 加入 wall time header；
- 调整 image detail；
- 按 function output policy 截断模型上下文形式。

下一次 sampling request 通过相同 `call_id` 把结果与原 tool call 配对。

## 42. 一次完整的双采样时间线

```text
Sampling 1
  模型看到 get_ticket / add_comment specs
  模型返回 get_ticket(call-1)

Runtime
  ToolRouter -> McpHandler
  JSON parse -> exact PreparedMcpCall
  approval: read-only, 不需询问
  tools/call get_ticket
  FunctionCallOutput(call-1)

Sampling 2
  模型读到 INC-42 内容
  模型返回 add_comment(call-2)

Runtime
  ToolRouter -> McpHandler
  approval: write/open-world，需要询问
  用户 AcceptForSession
  catalog lease 内应用决定
  tools/call add_comment
  FunctionCallOutput(call-2)

Sampling 3
  模型读到 comment created
  模型生成最终自然语言回答
```

MCP server 只返回工具结果；最终回答仍由模型在后续采样中生成。

## 43. 并行调用由谁决定

`McpHandler::supports_parallel_tool_calls` 返回 true 的条件包括：

- server metadata 明确支持 parallel；或
- 工具声明 `readOnlyHint=true`。

Tool runtime 再用通用并行门安排调用。

这不表示所有只读工具绝对线程安全，而是协议/实现对正确 server 的期望。错误 server 仍可能失败，所以并行能力也是合同，不是数学证明。

## 44. Elicitation 与 approval 不相同

MCP server 在工具执行中可以反向请求 Host 补充信息，这叫 elicitation：

```text
Codex -> deploy
Server -> 请选择 region
Host/UI -> 用户选择
Codex -> ElicitationResponse
```

approval 回答“允许执行这个动作吗”；elicitation 回答“server 还需要什么输入”。

二者可能连续发生，但拥有不同 request id、路由器和生命周期。

## 45. 第八阶段：配置 refresh

Session 用 `McpRefresh` 保存：

```text
pending: AtomicBool
gate: Semaphore(1)
```

dirty 后，`refresh_mcp_if_dirty`：

```text
取得唯一 gate
-> claim pending
-> 重新计算 desired state
-> 发布新 runtime
-> 若期间再次 dirty，继续下一轮
```

`McpRefreshInvalidationGuard` 保证：刷新 task 若在 publication 前被取消，claimed dirty 会恢复，不会悄悄丢失更新。

## 46. Runtime refresh 与 catalog hard refresh 不相同

### Runtime refresh

重新投影配置、认证、环境与 server 集合；可能复用 identity 相同的连接。

### Codex Apps hard refresh

显式重新请求 Apps tools/list，并：

```text
写入 tools override
-> catalog_revision += 1
-> 后续 binding 使用新目录
-> 旧 PreparedMcpCall 被 revision 检查拒绝
```

两个都叫 refresh，但作用对象不同。

## 47. 固定提交中的 `tools/list_changed` 边界

MCP protocol 支持 server 通知：

```text
notifications/tools/list_changed
```

但在固定提交 `4ee41929eaf4` 中，`LoggingClientHandler::on_tool_list_changed` 只执行：

```rust
info!("MCP server tool list changed");
```

它没有直接：

- 调用 tools/list；
- 增加 catalog revision；
- invalidate Session MCP runtime；
- 重建当前 Step ToolRouter。

因此本版本不能写成“server 发通知后 Codex 自动热更新普通 MCP 目录”。可确认的刷新来源是 Codex 自己的 runtime refresh，以及专门的 Codex Apps hard refresh。

这是“协议支持某通知”与“Host 已把通知接入产品状态机”的典型区别。

## 48. Refresh 期间旧连接为何不能马上关闭

旧 Step 可能仍持有：

```text
McpBinding
-> PreparedMcpCall
-> Arc<ManagedClient>
```

新 runtime 发布后若立即杀死所有旧 client，已批准并正在调用的工具会中断。

通过 Arc 所有权，旧 connection set 可以活到最后一个使用者释放。测试 `refresh_keeps_superseded_mcp_server_alive_for_in_flight_calls` 验证 refresh 不会破坏 in-flight call。

但旧 prepared call 也不会在连接关闭后 reroute 到新连接；测试 `prepared_call_does_not_reroute_after_captured_connection_closes` 验证 fail closed。

## 49. Startup 状态怎样对外展示

每个 server 会产生：

```text
Starting
Ready
Failed { error, reason }
Cancelled
```

所有非 dormant startup task 汇合后，还发：

```text
McpStartupComplete {
  ready,
  failed,
  cancelled
}
```

required validation 决定是否阻断 Session；状态事件则让 UI 能解释哪个 server 失败、是否需要登录或是否超时。

## 50. Shutdown 与取消

Session 结束时，MCP runtime：

- cancel pending startup；
- 关闭当前 connections；
- stdio server 由 client shutdown 负责结束进程；
- HTTP transport 关闭 client；
- unresolved elicitation 不能无限悬挂。

`McpServerConnection::Drop` 也会 cancel token，作为异步启动任务的兜底，但正常路径仍应显式 shutdown。

## 51. 失败排查表

| 现象 | 首查层 | 常见原因 |
|---|---|---|
| 配置有 server，但无 Starting | effective projection | disabled、plugin 未选择、环境不适用 |
| Starting 后失败 | transport/init | command、URL、env、OAuth、startup timeout |
| Ready 但工具缺失 | catalog/filter | allow/deny、visibility、limit、Apps policy |
| 第一 Step 没工具，后续出现 | optional startup | grace 内未 ready，后续 binding 捕获成功 |
| 模型看到工具但 not available | binding/call admission | 无 exact client、runtime 已变、工具已移除 |
| catalog changed | revision | 准备后 hard refresh，旧 call 被拒绝 |
| 等待用户 | approval | prompt/writes/Guardian/hook 决策 |
| tool call timeout | execution | `tool_timeout_sec` 或 server 卡住 |
| `isError=true` | server business result | transport 成功，但业务动作失败 |
| UI 结果被截短 | event projection | rollout item 有独立大小预算 |
| 模型看不到图片 | modality sanitization | 当前模型不支持 image input |
| server 发 list_changed 后无变化 | fixed-version behavior | 本版本 handler 只记录日志 |

## 52. 测试证据：每组测试能证明什么

### `codex-mcp/src/binding_tests.rs`

| 测试 | 证明的行为 |
|---|---|
| `prepared_call_keeps_captured_connection_and_authority_after_refresh` | refresh 后旧 call 不改用新连接/新权威 |
| `prepared_call_does_not_reroute_after_captured_connection_closes` | exact client 关闭后 fail closed |
| `prepared_call_is_rejected_after_catalog_refresh` | revision 变化拒绝旧 call |
| `stale_prepared_call_does_not_run_preparation` | 过期调用不会先执行副作用 preparation |
| `preparation_holds_catalog_authority_until_it_finishes` | revision 写入不能越过正在执行的 catalog lease |

### `codex-mcp/src/connection_manager_tests.rs`

重点覆盖：

- allow/deny 顺序；
- 名称清理与碰撞；
- cached tools 与 exact client 的差异；
- optional startup grace；
- required/optional 失败稳定性；
- hard refresh cache race；
- connection reconciliation；
- startup timeout 与认证诊断。

### `core/src/mcp_tool_call_tests.rs`

重点覆盖：

- annotations 怎样影响 approval；
- writes/prompt/approve modes；
- Guardian 与 permission hook；
- session/persistent remember；
- unsupported image/audio sanitization；
- 大事件结果截断；
- thread/turn/sandbox metadata；
- Codex Apps auth elicitation。

### Core integration tests

- `mcp_tool_cache.rs`：不同 thread 的 call 不串 client；root/subagent eager/lazy 差异；
- `mcp_refresh_cleanup.rs`：refresh 保留 in-flight old server；
- `mcp_tool_exposure.rs`：direct/deferred/恢复与目录变化；
- `hooks_mcp.rs`：MCP 进入通用 hooks 生命周期；
- `rmcp_client.rs`：真实 test server 的 end-to-end 协议行为。

## 53. 源码阅读顺序

### 第一遍：只看四个核心类型

```text
runtime.rs              McpRuntime
connection_manager.rs   McpConnectionSet
binding.rs              McpBinding
binding.rs              PreparedMcpCall
```

回答：thread 状态怎样变成 Step 快照。

### 第二遍：只看发现和注册

```text
rmcp_client.rs                         start_server_task / tools/list
connection_manager/tool_catalog.rs     capture_binding_with_metadata
core/mcp_tool_exposure.rs              append_mcp_tools
core/tools/spec_plan.rs                build_tool_router
```

回答：server 声明怎样变成模型 ToolSpec。

### 第三遍：只看一次调用

```text
core/tools/handlers/mcp.rs     McpHandler::handle_call
core/mcp_tool_call.rs          handle_mcp_tool_call
codex-mcp/binding.rs           call_with_preparation
core/tools/context.rs          McpToolOutput
```

回答：审批和 catalog lease 分别在哪里。

### 第四遍：读测试而不是继续猜

先读五个 binding tests，再读 approval 与 result sanitization tests。

## 54. 八个常见误解

### 误解一：server Ready 就表示所有工具都会给模型

错。还要经过 filter、visibility、policy 和 exposure。

### 误解二：ToolInfo 就是可执行连接

错。ToolInfo 是 metadata；PreparedMcpCall 才绑定 exact client。

### 误解三：模型能看到 token

错。模型只看 schema/description，认证保留在 runtime。

### 误解四：只读 hint 可以完全信任

错。它影响策略和并行判断，但不是不可伪造的强制边界。

### 误解五：refresh 后旧调用自动转到新 client

错。PreparedMcpCall 不 reroute；目录 revision 还可能直接拒绝它。

### 误解六：业务 `isError` 就等于 transport 断开

错。它是一次成功 MCP response 中的业务失败标记。

### 误解七：UI 展示结果与模型收到结果字节完全一样

错。事件、模型上下文和 Code Mode 有不同转换/预算。

### 误解八：tools/list_changed 自动刷新普通工具

固定提交中不成立；这里只记录日志。

## 55. 理解检查

1. 为什么 `McpRuntime` 是 thread-owned，而 `McpBinding` 是 Step 快照？
2. tool cache 为什么不能单独授权调用？
3. `McpBinding.calls` 为什么以 `(server, tool)` 为 key？
4. 为什么 revision 读锁要覆盖 preparation 和 tools/call？
5. Apps policy 为什么在调用时还会再检查？
6. `readOnlyHint` 影响哪两类行为？
7. `isError=true` 与 Rust `Err` 有什么不同？
8. 为什么 UI event result 与模型 FunctionCallOutput 分开截断？
9. refresh 后旧连接为什么可能继续存活？
10. 本固定版本收到 `tools/list_changed` 后实际做什么？

### 参考答案

1. Thread 配置会刷新；一次采样需要稳定的目录和执行权威。
2. 缓存只有 metadata，没有 exact ready client 和当前审批权威。
3. 不同 server 可以有同名工具，原始协议身份必须精确。
4. 防止检查后目录更换，也防止过期 call 先做不可逆副作用。
5. 配置层可能变化，外部工具必须 fail closed。
6. 审批风险判断和并行调用提示。
7. 前者是正常 response 的业务失败；后者是执行/协议失败。
8. rollout/UI 存储预算和模型上下文预算不同。
9. in-flight Step 仍通过 Arc 持有 exact connection。
10. 写一条 info 日志，不直接重拉目录。

## 56. 动手练习

### 练习一：画出两套名称

给 server `ticket-system/v2` 的 tool `add-comment` 画出：

```text
原始 server/tool
规范化 namespace/name
ToolRouter key
MCP tools/call name
```

不要假设具体 hash 后缀，去 `tools.rs` 找规范化测试确认。

### 练习二：分析一次目录竞态

时间线：

```text
t1 模型看到 delete_ticket
t2 用户批准
t3 hard refresh 删除 delete_ticket
t4 runtime 准备执行
```

指出 revision check、preparation closure 和真实 call 分别能否发生。

### 练习三：区分三种结果

为下面情况写出 UI 状态和模型输出：

1. 用户拒绝；
2. server timeout；
3. server 返回 `isError=true` 和“ticket not found”。

## 57. 本篇局部术语表

| 英文 / 代码词 | 字面中文 | 在本篇中的实际含义 |
|---|---|---|
| MCP | 模型上下文协议 | Host 与外部 server 发现、调用工具和读取资源的协议 |
| Host | 主机 | Codex；管理模型、Session、策略、连接和用户交互 |
| MCP client | MCP 客户端 | Codex 内部与一个 server 按协议通信的组件 |
| MCP server | MCP 服务器 | 声明 tools/resources，并执行真实外部操作的进程或服务 |
| transport | 传输 | stdio 或 Streamable HTTP 等协议承载方式 |
| stdio | 标准输入输出 | Codex 启动进程并用 stdin/stdout 交换 MCP 消息 |
| Streamable HTTP | 可流式 HTTP | 通过 URL 访问的远程 MCP transport |
| initialize | 初始化握手 | 交换双方信息、capabilities、instructions 的第一阶段 |
| capability | 能力声明 | client/server 声明支持的协议功能，不等于业务权限 |
| server instructions | 服务器指导 | initialize 返回的跨工具使用说明 |
| discovery | 发现 | 用 tools/list、resources/list 等读取 server 目录 |
| catalog | 目录 | 当前发现并允许进入某个使用表面的工具集合 |
| catalog item limit | 目录项目上限 | 防止无限分页或过多外部定义进入内存/上下文的硬界限 |
| `McpRuntimeInput` | MCP 运行输入 | 配置、身份、环境、cache、事件和 extensions 的完整物化输入 |
| `McpRuntime` | MCP 运行时 | 一条 thread 拥有的可刷新 MCP publication owner |
| publication | 发布 | 原子替换 current runtime snapshot，使新 Step 可见 |
| publication gate | 发布门 | 防止 Ready 事件早于新 connection set 可访问 |
| `McpConnectionSet` | MCP 连接集合 | 一次 publication 内所有 server view 与目录 revision |
| `McpServerView` | server 使用视图 | connection、metadata、filter、timeout、catalog limit 的组合 |
| `ManagedClient` | 受管 client | ready RMCP client、已发现 tools、server info 和 timeout |
| connection identity | 连接身份 | 判断旧 client 能否在 refresh 中安全复用的配置/环境/凭据事实 |
| eager startup | 立即启动 | runtime 发布后直接启动 server |
| lazy startup | 延迟启动 | 有缓存的 optional server 在首次需要时才真正启动 |
| required server | 必需 server | 初始化失败会阻断 thread 的 MCP server |
| optional server | 可选 server | 失败或较慢时可暂时从当前 Step 省略 |
| startup grace | 启动宽限 | 捕获 binding 时等待 optional server 的有限时间 |
| `ToolInfo` | 工具信息 | 原始 tool schema 加 server、namespace、connector、plugin 元数据 |
| tool filter | 工具过滤器 | enabled allow-list 后再应用 disabled deny-list |
| model visibility | 模型可见性 | MCP UI metadata 是否允许工具进入模型声明 |
| callable name | 可调用名称 | 规范化后给模型与 ToolRouter 使用的名称 |
| canonical tool name | 规范工具名 | namespace/name 组成的稳定内部 ToolName |
| tool exposure | 工具暴露方式 | Direct、Deferred 或 Hidden |
| direct tool | 直接工具 | schema 直接随模型请求发送 |
| deferred tool | 延迟工具 | 先经 tool search 发现，再按需暴露 |
| `McpBinding` | MCP 绑定 | 某个 Step 冻结的目录、clients、config 和 prepared calls |
| exact client | 精确 client | 与工具被发现时同一 connection/authority 的协议 client |
| cached binding | 缓存绑定 | 仅在 publication 与稳定 catalog revision 未变时复用的快照 |
| `PreparedMcpCall` | 已准备 MCP 调用 | 绑定 exact client、tool metadata、approval authority 和 revision 的调用权 |
| catalog revision | 目录版本 | hard refresh 时递增，用于拒绝过期 prepared call |
| catalog lease | 目录租约 | revision 读锁覆盖 preparation 与真实调用的权威区间 |
| TOCTOU | 检查-使用竞态 | 检查允许后、真正执行前状态被更换的风险 |
| `McpHandler` | MCP 工具处理器 | 将 ToolInvocation 适配到 MCP 调用主函数的 ToolExecutor |
| `ToolSpec` | 模型工具规格 | 名称、描述、JSON Schema 和 namespace 等模型可见定义 |
| `ToolRouter` | 工具路由器 | 用模型可见名字找到当前 Step handler 的不可变计划 |
| arguments | 参数 | 模型生成的 JSON 字符串，执行前必须解析验证 |
| annotation | 注解 / 提示 | read-only、destructive、open-world 等 server 风险元数据 |
| approval mode | 审批模式 | auto、prompt、writes、approve 等 server/tool 策略 |
| approval authority | 审批权威 | 捕获时的 config、reviewer、plugin attribution 与复用规则 |
| Guardian | 自动审查者 | 可对 MCP 动作给出批准、拒绝或超时决定的 reviewer |
| permission hook | 权限 Hook | 在调用前参与 MCP 动作决策的扩展检查 |
| elicitation | 信息征询 | server 在执行期间反向向 Host 请求信息或选择 |
| `_meta` | 附加元数据 | Host 加入 thread/turn/trace/sandbox 等非普通 arguments 信息 |
| `CallToolResult` | 工具调用结果 | content、structured content、is_error 和 meta 的协议投影 |
| business error | 业务错误 | transport 成功，但 `isError=true` |
| transport error | 传输错误 | 连接、超时、认证或 JSON-RPC 失败产生 Rust Err |
| modality sanitization | 模态净化 | 模型不支持 image/audio 时替换对应 content block |
| event projection | 事件投影 | 给 UI/rollout 的 McpToolCallItem，有独立截断预算 |
| `McpToolOutput` | MCP 模型输出适配 | 把 CallToolResult 转成 FunctionCallOutput 的 ToolOutput |
| `FunctionCallOutput` | 函数调用结果项 | 用 call_id 回填到下一次模型采样的 Responses input item |
| in-flight call | 执行中调用 | 已开始、尚未得到终态的工具调用 |
| fail closed | 安全拒绝 | 无法证明工具/连接/权威仍有效时不执行 |
| dirty refresh | 脏状态刷新 | 配置/认证/环境变化后标记 runtime 需要重新投影 |
| hard refresh | 强制目录刷新 | 重新 tools/list 并发布新 catalog revision |
| `tools/list_changed` | 工具目录变化通知 | 本固定提交中仅记日志，没有自动重拉普通工具目录 |

## 58. 最后压缩成一张图

```text
config/auth/environment
        │
        ▼
McpRuntimeInput
        │ publish
        ▼
McpRuntime -> McpConnectionSet
                  │ initialize + tools/list
                  ▼
              ToolInfo catalog
                  │ capture under revision lock
                  ▼
StepContext: McpBinding + ToolRouter
                  │
       model emits tool call
                  │
                  ▼
McpHandler -> handle_mcp_tool_call
                  │ JSON / availability / policy / approval
                  ▼
PreparedMcpCall.call_with_preparation
                  │ revision lease + exact client
                  ▼
              MCP tools/call
                  │
                  ▼
CallToolResult
  ├─ UI McpToolCallItem（事件预算）
  ├─ telemetry/span（白名单字段）
  └─ McpToolOutput -> FunctionCallOutput（模型预算）
                         │
                         ▼
                    下一次 sampling
```

记住四个“不等于”：

```text
server Ready 不等于工具对模型可见
ToolInfo 不等于可执行权限
cached catalog 不等于 exact live client
模型提出 tool call 不等于动作已经获准
```

下一篇计划精读 Context compaction：长历史怎样变成 replacement history，以及压缩前后哪些工具调用配对和上下文基线必须保留。

返回[源码精读系列目录](README.md)或[课程总目录](../README.md)。
