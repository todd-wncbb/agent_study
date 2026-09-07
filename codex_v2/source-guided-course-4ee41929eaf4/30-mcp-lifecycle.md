# 30：MCP 生命周期——从配置、连接到一次工具调用

第 8 章已经区分了 Skill、Plugin、MCP、App 和 Tool。这一章只深挖 MCP：一个外部工具怎样从配置文件出发，经过连接、发现、注册、模型选择、审批和执行，最后把结果送回模型。

> 源码基线：`4ee41929eaf4`。MCP 仍在快速演进，阅读其他版本时应优先搜索本章给出的类型和函数名，不要死记行号。

---

## 1. 这一章要解决什么问题

读完后，你应该能够解释：

1. Codex、MCP client 和 MCP server 分别负责什么；
2. `stdio` 与 Streamable HTTP 两种连接方式有什么区别；
3. 为什么“工具出现在模型请求里”不等于“调用时一定成功”；
4. `McpRuntime`、`McpConnectionSet`、`McpBinding` 和 `PreparedMcpCall` 分别活多久；
5. 一次 `tools/call` 前后经过哪些安全检查；
6. 工具结果怎样变成下一次模型请求的输入；
7. 启动失败、认证失败、超时、目录刷新和断线分别应从哪里排查。

---

## 2. 先说人话：MCP 像统一的“外接设备接口”

假设 Codex 需要读取公司工单系统。

没有统一协议时，每接一个系统都可能需要重新约定：

- 怎样连接；
- 怎样声明有哪些操作；
- 参数是什么格式；
- 怎样返回文本、图片或结构化数据；
- 怎样报告错误；
- 需要用户补充信息时怎么办。

MCP 把这些交互抽象成共同协议。公司工单系统可以提供一个 MCP server，并声明：

```text
工具：get_ticket
说明：读取一个工单
参数：{ "ticket_id": "string" }
```

Codex 不需要知道工单系统内部使用 MySQL、GraphQL 还是旧式 Java 服务。Codex 只需要通过 MCP client 按协议调用 `get_ticket`。

这很像 USB：USB 统一了连接方式，但并不保证每个设备都安全、都已授权、都能正常工作。

---

## 3. 三个角色一定要分清

官方概念可以压缩成三个角色：

| 角色 | 本例 | 职责 |
|---|---|---|
| Host | Codex | 管理会话、模型、工具目录、安全策略和用户交互 |
| Client | Codex 内部的 MCP client | 按 MCP 协议连接某个 server、发送请求、接收结果 |
| Server | 工单 MCP server | 暴露工具和资源，把请求转成真实业务操作 |

最容易误解的是：**Codex 是 Host，Codex 内部还包含 MCP Client。**

模型既不是 MCP client，也不会直接连外部服务。模型只会生成类似下面的“调用建议”：

```json
{
  "name": "mcp__tickets__get_ticket",
  "arguments": {
    "ticket_id": "INC-42"
  }
}
```

真正决定是否执行、通过哪个连接执行的是 Codex runtime。

公开概念边界可参考 OpenAI 的 [Codex MCP 说明](https://developers.openai.com/codex/concepts/customization#mcp)。

---

## 4. MCP 能暴露什么

MCP 协议可以让 server 暴露：

- **Tools**：可以调用的动作；
- **Resources**：可以读取的数据；
- **Prompts**：server 提供的可复用提示模板。

当前 Codex 主链最重要的是 Tools 和 Resources：

- 普通 MCP tool 会转换成模型可见的工具定义；
- Resources 通过 `list_mcp_resources`、`list_mcp_resource_templates`、`read_mcp_resource` 等 Codex 工具访问；
- 本章核对的当前源码没有把 MCP prompts 作为同等主链展开，因此不要因为协议支持 prompts，就假设当前 Codex 的所有表面都会自动使用它们。

协议能力与某个 Host 的产品实现不是同一件事。

---

## 5. 一次完整生命周期总览

先看全图，后面逐段拆开：

```text
config.toml / Plugin / App / Executor
                │
                ▼
      计算有效 MCP server 配置
                │
                ▼
    为 thread 创建或刷新 McpRuntime
                │
                ▼
  建立 stdio / Streamable HTTP transport
                │
                ▼
       initialize 握手、协商能力
                │
                ▼
       tools/list 发现工具目录
                │
                ▼
   过滤、命名规范化、构造 ToolInfo
                │
                ▼
       Step 捕获不可变 McpBinding
                │
                ▼
   ToolRouter 把工具定义交给模型
                │
                ▼
       模型返回 tool call 建议
                │
                ▼
  PreparedMcpCall + policy + approval
                │
                ▼
            tools/call
                │
                ▼
     CallToolResult + UI 生命周期事件
                │
                ▼
 function_call_output 进入下一次采样
```

注意：这不是“一次 HTTP 请求”。它跨越了 thread 初始化、step 快照和多次模型采样。

---

## 6. 第一阶段：配置从哪里来

最直观的来源是 `config.toml`。下面是教学示例。

### 6.1 本地 stdio server

```toml
[mcp_servers.tickets]
command = "ticket-mcp-server"
args = ["--format", "mcp"]
enabled = true
startup_timeout_sec = 10
tool_timeout_sec = 30
enabled_tools = ["get_ticket", "search_tickets"]
```

含义是：Codex 启动一个本地子进程，通过该进程的标准输入和标准输出交换 MCP 消息。

### 6.2 Streamable HTTP server

```toml
[mcp_servers.tickets]
url = "https://tickets.example.com/mcp"
bearer_token_env_var = "TICKETS_MCP_TOKEN"
enabled = true
required = false
disabled_tools = ["delete_ticket"]
```

含义是：Codex 连接远程 HTTP endpoint，token 的真实值从环境变量读取，而不是直接写进配置。

当前配置结构的主要字段位于 `codex-rs/config/src/mcp_types.rs::McpServerConfig` 和 `McpServerTransportConfig`。

---

## 7. 配置不是只有“地址”

`McpServerConfig` 同时描述连接、暴露和安全策略：

| 配置 | 回答的问题 |
|---|---|
| `enabled` | 要不要初始化这个 server |
| `required` | 初始化失败是否应使 thread 初始化失败 |
| `startup_timeout_sec` | 初始化和首次列工具最多等多久 |
| `tool_timeout_sec` | 单次工具调用最多等多久 |
| `enabled_tools` | 只允许暴露哪些工具 |
| `disabled_tools` | 明确移除哪些工具 |
| `default_tools_approval_mode` | 默认怎样审批工具 |
| `tools.<name>` | 某个工具的单独策略 |
| `supports_parallel_tool_calls` | 是否允许把该 server 的工具视为可并行 |
| `environment_id` | server 应在哪个执行环境中启动或访问 |

所以“已经配置一个 server”不代表：

- 它已启用；
- 它能连接成功；
- 所有工具都对模型可见；
- 所有调用都不需要审批。

---

## 8. 配置会先变成“有效 Server 集合”

实际运行时的 server 不只来自用户手写配置，还可能来自：

- 已安装 Plugin；
- 用户显式选择的 Plugin；
- Codex Apps / connectors；
- Executor 或 extension 提供的 MCP 能力；
- 管理策略允许后的配置投影。

Core 会为当前 thread、当前环境和当前认证状态计算 `McpRuntimeProjection`，再得到 `EffectiveMcpServer` 集合。

这里的关键词是 **effective**：它表示“所有来源合并并应用策略之后真正生效的结果”，不是某一份原始 TOML。

---

## 9. `McpRuntime` 为什么属于 Thread

`codex-rs/codex-mcp/src/runtime.rs` 对 `McpRuntime` 的定义很明确：它拥有一条 Codex thread 的可变 MCP 状态。

它主要保存：

- 当前发布的 `McpConnectionSet`；
- 当前有效配置与认证身份；
- 可用 capability roots；
- 缓存的 `McpBinding`；
- elicitation 路由；
- 是否需要在下次刷新时重连。

这样设计有两个好处：

1. 不同 thread 可以拥有不同的环境、插件选择和认证视图；
2. 配置刷新时可以原子发布新状态，而已经开始的 step 仍持有自己的旧快照。

它不是全进程只有一个“万能 MCP client”。

---

## 10. `ArcSwap`：怎样发布新 Runtime 快照

`McpRuntime.current` 使用 `ArcSwap<PublishedMcpRuntime>`。

先不用研究库的全部实现，只要理解：

```text
后台构造新连接集合
        ↓
一次性替换 current 指针
        ↓
新 step 看到新版本
旧 step 仍可持有旧 Arc
```

这比在共享 HashMap 上边调用边修改更容易维护一致性。

`McpRuntime::replace()` 会尽量复用身份一致且仍可用的旧连接；`replace_fresh()` 则明确发布全新的连接集合。

---

## 11. 第二阶段：选择 Transport

### 11.1 stdio

本地 stdio 路径会：

1. 解析 command、args、env 和 cwd；
2. 启动一个子进程；
3. 把子进程 stdin/stdout 作为协议通道；
4. 单独读取 stderr，避免把日志误当协议消息；
5. shutdown 时终止 Codex 拥有的 stdio server 进程。

关键源码：

- `codex-rs/codex-mcp/src/rmcp_client.rs::make_rmcp_client`；
- `codex-rs/rmcp-client/src/stdio_server_launcher.rs::LocalStdioServerLauncher`；
- `codex-rs/rmcp-client/src/rmcp_client.rs::shutdown`。

### 11.2 Streamable HTTP

HTTP 路径会：

1. 解析 URL 和 headers；
2. 解析 bearer token 或 OAuth / ChatGPT 认证来源；
3. 使用当前执行环境提供的 HTTP client；
4. 建立 MCP Streamable HTTP transport；
5. 对协议允许恢复的部分错误执行受控重试。

远程 HTTP server 不是 Codex 启动的子进程，因此 shutdown 的重点是关闭 client transport，而不是杀死远程服务。

---

## 12. 执行环境也是连接身份的一部分

同一个 server 配置，如果运行环境不同，不能随意复用同一个连接。

例如：

- 本地 thread 的 stdio server 在本机启动；
- remote executor 的 stdio server 应在 executor 一侧启动；
- HTTP 请求也可能需要由对应环境的 route-aware client 发出；
- OAuth 凭据名称需要隔离 executor-owned server，避免误用本机身份。

`McpRuntimeContext` 保存环境管理器和本地 stdio fallback cwd。连接复用还会比较 `McpServerConnectionIdentity`，而不只是比较 server 名字。

---

## 13. 第三阶段：Initialize 握手

transport 建立后还不能直接 `tools/call`。Client 和 Server 先进行 initialize 握手。

概念上，Client 会告诉 Server：

- 自己是谁，例如 `codex-mcp-client`；
- 支持哪些 client capabilities；
- 是否支持 elicitation；
- 支持哪些扩展。

Server 返回：

- server 名称和版本信息；
- server capabilities；
- 可选 instructions；
- 支持的实验扩展。

当前调用链：

```text
make_rmcp_client
→ RmcpClient::initialize
→ start_server_task
→ 读取 initialize_result
```

握手成功只表示双方能按协议对话，还没有完成工具目录发现。

---

## 14. 第四阶段：`tools/list` 发现目录

`start_server_task()` 在 initialize 后调用 `list_tools_for_client_uncached()`。

一个简化的工具定义可能是：

```json
{
  "name": "get_ticket",
  "description": "Read one ticket by ID",
  "inputSchema": {
    "type": "object",
    "properties": {
      "ticket_id": { "type": "string" }
    },
    "required": ["ticket_id"]
  },
  "annotations": {
    "readOnlyHint": true
  }
}
```

Codex 将原始声明包装为 `ToolInfo`。它同时保留两类身份：

- `server_name` 和原始 `tool.name`：真正发回 MCP server 时使用；
- `callable_namespace` 和 `callable_name`：给模型和 ToolRouter 使用。

这两者不能混为一谈。

---

## 15. 为什么工具名字需要规范化

不同 server 可能都提供 `search`，名称中也可能含有模型 API 不接受的字符，或者组合后超过长度限制。

`codex-rs/codex-mcp/src/tools.rs` 会：

- 清理模型不可接受的字符；
- 按需要加 `mcp__` 前缀；
- 组合 namespace 和 tool name；
- 发现冲突时增加稳定 hash 后缀；
- 保证模型可见名字唯一且长度受限；
- 仍保留原始 server/tool 身份用于协议调用。

所以 UI 或模型看到的 `mcp__tickets__get_ticket` 可能不是 server 原始声明的完整名字。

---

## 16. 工具目录要经过多层过滤

一个 server 返回了工具，不代表模型一定能看到。

主要过滤包括：

1. server 是否 enabled；
2. `enabled_tools` allow-list；
3. `disabled_tools` deny-list；
4. MCP UI visibility 是否允许 model；
5. plugin / app / managed policy 是否允许；
6. 当前 exposure 计划是 direct、deferred 还是 code mode；
7. 是否存在可执行的 exact client。

`ToolFilter::allows()` 的顺序是：先要求满足 allow-list，再应用 deny-list。因此同一个工具同时出现在两边时，deny 生效。

---

## 17. Required 与 Optional Server

### Required

`required = true` 的 server 会在 session 初始化阶段被等待。`validate_required_servers()` 会汇总失败并返回错误。

适合：没有这个能力，任务就没有意义的自动化环境。

### Optional

普通 server 可以在启动较慢或失败时暂时不进入本 step 的工具目录，其他工具仍可工作。

当前 `capture_binding_with_metadata()` 对 pending optional server 只给一个有限 grace window；若仍未就绪，可以先省略它，而不是让整个 turn 无限等待。

因此可能出现：

```text
第一步：模型看不到某个慢启动工具
稍后：连接成功
下一步或刷新后：新 binding 才看到它
```

---

## 18. Eager 与 LazyWhenCached

`McpStartupPolicy` 有两个主要模式：

- `Eager`：发布 runtime 后启动 server；
- `LazyWhenCached`：已有缓存工具目录时，某些 optional server 可以先休眠，首次真正需要时再启动。

当前 core 对普通主 session 使用 Eager；对 SubAgent 使用 `LazyWhenCached`。

Lazy 并不等于“凭空调用缓存中的工具”。缓存可以先帮助发现和展示工具，但执行前仍需要 exact ready client。为了安全，缓存目录里的 `read_only_hint` 在没有精确连接时也不能被完全信任。

---

## 19. 第五阶段：Step 捕获 `McpBinding`

这是本章最重要的部分。

`McpRuntime` 是 thread 范围、可以刷新的；模型的一次 step 却需要稳定视图。因此 step 会捕获一份不可变 `McpBinding`。

`McpBinding` 包含：

- 冻结的工具目录；
- exact clients；
- 当前 MCP config；
- 每个可执行工具对应的 `PreparedMcpCall`；
- plugin 是否可用等归属信息。

可以把它理解为一张带时间戳的登机牌：

```text
不是“系统里可能有这趟航班”
而是“你在这个时刻、用这个身份、被绑定到这趟航班”
```

---

## 20. 为什么不能只保存一个裸 Client

如果只保存 `client`，调用时会产生竞态：

1. 模型看到旧目录中的 `delete_ticket`；
2. 管理员刷新配置并禁用这个工具；
3. runtime 换成新目录；
4. 旧 step 却拿“最新 client”执行旧工具。

`PreparedMcpCall` 因此把下面的信息冻结在一起：

- exact client；
- 原始 tool metadata；
- server metadata；
- tool timeout；
- approval authority；
- plugin attribution；
- catalog revision。

这就是“模型看到什么”和“runtime 按什么身份执行”保持一致的关键。

---

## 21. Catalog Revision 防止过期调用

`PreparedMcpCall::call_with_preparation()` 在执行前比较：

```text
prepared 时的 catalog_revision
            vs
当前 catalog_revision
```

若已经变化，调用会被拒绝：

```text
tool call rejected because the catalog changed after ... was prepared
```

而且 revision 的读锁会覆盖不可逆准备和真正执行，避免“检查刚通过，目录立刻被替换”的空窗。

这是一种典型的 TOCTOU 防护：检查时间与使用时间不能脱节。

---

## 22. 第六阶段：工具进入模型请求

`McpHandler` 把 `ToolInfo` 转换成 `ToolSpec`，然后注册到当前 step 的 `ToolRouter`。

模型看到的主要内容是：

- 工具名称；
- 描述；
- JSON Schema 参数；
- namespace；
- 是否允许并行等执行提示。

模型不会获得：

- bearer token；
- OAuth refresh token；
- stdio 子进程句柄；
- HTTP client；
- 用户尚未授予的额外权限。

模型只拥有“描述和建议调用的能力”，不是连接本身。

---

## 23. 第七阶段：模型提出 Tool Call

假设模型返回：

```json
{
  "call_id": "call_7",
  "name": "mcp__tickets__get_ticket",
  "arguments": "{\"ticket_id\":\"INC-42\"}"
}
```

ToolRouter 根据模型可见名称找到对应 `McpHandler`。Handler 再使用 `ToolInfo` 中保留的原始身份调用：

```text
server = tickets
tool   = get_ticket
```

这说明模型可见名字负责路由入口，原始名字负责 MCP 协议请求。

---

## 24. 调用前的第一道检查：参数必须是合法 JSON

`handle_mcp_tool_call()` 先解析 arguments：

- 空字符串视为没有参数；
- 合法 JSON 转成 `serde_json::Value`；
- 非法 JSON 直接形成错误结果，不发给 server。

JSON Schema 帮模型生成正确参数，但 schema 不是绝对保证。Runtime 仍必须把模型输出当作需要验证的输入。

---

## 25. 第二道检查：当前 Step 是否真的有 Prepared Call

Core 调用：

```text
Session::prepare_mcp_call(server, tool)
→ refresh_mcp_if_dirty()
→ current_binding_for_call(server)
→ McpBinding::prepare_call(server, tool)
```

找不到时不会绕过 binding 直接访问“最新 client”，而是返回：

```text
MCP tool `server/tool` is not available to the model
```

这能阻止模型幻想一个未暴露的工具名，也防止旧请求绕开当前 step 的目录。

---

## 26. 第三道检查：App Policy 与 Tool Approval

普通 MCP server 使用捕获的 server/tool approval 配置；Codex Apps 还会根据 connector、工具名和 annotations 计算 `AppToolPolicy`。

可能影响决策的 hint 包括：

- `readOnlyHint`：是否只读；
- `destructiveHint`：是否可能造成破坏；
- `openWorldHint`：是否影响公开世界；
- server 或 per-tool approval mode；
- plugin 是否由用户在本 turn 显式选择；
- workspace/admin policy；
- hooks 和自动 reviewer。

重要原则：**hint 是 server 提供的元数据，不是不可伪造的安全证明。** Host 仍要结合配置和策略处理。

---

## 27. Approval、Sandbox 和外部服务权限不是一回事

这三层常被混淆：

| 层 | 解决什么问题 |
|---|---|
| Tool approval | 用户或策略是否允许这次外部动作 |
| Codex sandbox / permission profile | 本地命令、文件和网络能访问什么 |
| 外部服务 auth / RBAC | 这个账号在工单系统里本来能做什么 |

批准一次 `delete_ticket` 不会自动让 OAuth 账号获得管理员权限；反过来，账号具有权限也不代表 Codex 可以跳过 approval policy。

MCP server 如果运行在本地子进程中，它自身的进程权限和启动环境也很重要，不能把“通过 MCP 调用”理解为天然沙箱化。

---

## 28. Hooks 在哪里介入

MCP tool call 也会经过通用 Tool Runtime 的 lifecycle：

- PreToolUse 可以检查或修改输入；
- PermissionRequest 可以参与审批；
- PostToolUse 可以观察结果；
- telemetry 记录 server、tool、call ID、耗时与 outcome。

Hook 名称会确保带上 MCP 前缀，便于规则明确匹配外部工具。

这再次说明：MCP 是外部执行协议，但进入 Codex 后仍受统一工具编排层管理。

---

## 29. 第八阶段：真正发送 `tools/call`

审批完成后，`PreparedMcpCall::call_with_preparation()` 执行不可逆准备：

1. 确认 catalog revision 未变化；
2. 应用本次或持久化 approval decision；
3. 必要时重写 OpenAI file 参数；
4. 附加 thread、trace 和 sandbox state 等 request meta；
5. 调用 exact client 的 `call_tool()`；
6. 使用捕获的 tool timeout 等待结果。

协议层的核心请求可以简化为：

```json
{
  "method": "tools/call",
  "params": {
    "name": "get_ticket",
    "arguments": {
      "ticket_id": "INC-42"
    }
  }
}
```

真实请求还可能包含 `_meta`，不要把上面的教学 JSON 当作完整 wire dump。

---

## 30. `CallToolResult` 可以包含什么

Codex 协议层的 `CallToolResult` 主要保存：

- `content`：文本、图片、音频、资源链接等内容块；
- `structured_content`：结构化 JSON；
- `is_error`：server 是否把这次调用标记为业务错误；
- `meta`：额外元数据。

例如：

```json
{
  "content": [
    {
      "type": "text",
      "text": "INC-42: Login fails after password reset"
    }
  ],
  "structuredContent": {
    "id": "INC-42",
    "priority": "P1"
  },
  "isError": false
}
```

---

## 31. 两种“失败”要分开

### 31.1 协议或执行失败

例如：

- transport 已断开；
- tool timeout；
- server 不认识方法；
- OAuth refresh 失败；
- catalog revision 已变化。

这种情况通常表现为 Rust `Err(...)`，没有正常 `CallToolResult`。

### 31.2 Server 正常返回业务错误

例如工单不存在，server 仍成功完成 MCP 请求，但返回：

```json
{
  "content": [{ "type": "text", "text": "ticket not found" }],
  "isError": true
}
```

这时 transport 成功，业务结果失败。Telemetry 和 UI 需要区分两者。

---

## 32. 为什么结果不能原样无限塞给模型

外部 server 可能返回：

- 超大文本；
- 模型不支持的图片 detail；
- 敏感 telemetry metadata；
- 不适合进入上下文的内容形状。

Core 会进行：

- 内容格式转换；
- 必要的敏感字段处理；
- 模态兼容处理；
- function output 截断；
- UI event 的独立大小限制。

`McpToolOutput::response_payload()` 还会加入 wall time，并按模型的 truncation policy 生成上下文注入形式。

Code Mode 消费者可以拿到更原始的结构，但这不等于普通模型上下文不受限制。

---

## 33. 结果怎样回到模型

`McpToolOutput::to_response_item()` 生成：

```text
ResponseInputItem::FunctionCallOutput {
    call_id,
    output,
}
```

它随后进入对话历史和下一次 sampling request。`call_id` 把结果与模型先前提出的 tool call 对应起来。

所以 Agent 工具循环是：

```text
采样 1：模型说“调用 get_ticket”
        ↓
Codex 执行 MCP 工具
        ↓
采样 2：模型读到 call_7 的结果
        ↓
模型继续推理或给最终回答
```

MCP server 不会直接生成 Codex 的最终自然语言回答。

---

## 34. UI 为什么有 Started 和 Completed 两个事件

工具可能执行数秒甚至更久。Core 会构造 `McpToolCallItem`，并先后发出：

- `InProgress`；
- `Completed`；或
- `Failed`。

Item 还可以携带：

- server 和 tool；
- arguments；
- connector / app / plugin 归属；
- read-only hint；
- result 或 error；
- duration。

TUI 或 app-server 可以先显示“正在读取工单”，完成后再更新同一个 call ID，而不必等到整个 turn 结束。

这属于客户端投影，不是 MCP wire protocol 本身。

---

## 35. Resources 走的是另一条工具入口

MCP Resource 是可读取数据，不是任意 action。Codex 提供统一入口：

```text
list_mcp_resources
list_mcp_resource_templates
read_mcp_resource
```

它们内部仍通过当前 `McpBinding` 或连接集合发送：

- `resources/list`；
- `resources/templates/list`；
- `resources/read`。

指定单个 server 时可以使用分页 cursor；不指定 server 时，Codex 可以汇总多个 server 的资源，并为每项补上 server 名，避免 URI 来源不清。

工具和资源的区别可以简化为：

- Tool：请求 server **做一件事**；
- Resource：请求 server **给一份可读内容**。

---

## 36. Elicitation：Server 反过来向用户要信息

普通 tool call 是 Client 请求 Server；elicitation 则允许 Server 在执行过程中请求 Host 补充信息或确认。

例如：

```text
Codex → create_deployment
Server → 请选择部署区域
Codex UI → 向用户显示选项
用户 → 选择 ap-southeast-1
Codex → 把响应交回 Server
```

当前实现由 `ElicitationRequestManager`、`ElicitationRequestRouter` 和 Session 侧 reviewer/lifecycle 共同处理。

Elicitation 仍受 approval policy 控制；在不能交互或不允许弹出时，应拒绝或自动处理，而不能让 server 无限等待。

不要把 elicitation 与 Codex 模型主动调用 `request_user_input` 混为一谈：前者由 MCP server 发起，后者是 Codex 自己的工具行为。

---

## 37. Authentication 的优先级

HTTP MCP 可能使用：

- 配置的 bearer token 环境变量；
- 静态或环境来源的 HTTP authorization header；
- 已保存的 MCP OAuth credentials；
- 受信 first-party origin 上的 ChatGPT session；
- 无认证连接。

源码注释强调：显式配置的 bearer token 和 authorization header 优先。之后才根据 `auth = "oauth"` 或 `auth = "chatgpt"` 选择 fallback。

两个安全习惯：

1. 配置里写环境变量名，不写 secret 值；
2. 不把“登录 Codex”误认为自动登录所有第三方 MCP server。

OAuth 登录、凭据保存、凭据刷新和一次工具审批也是四件不同的事。

---

## 38. Refresh：配置或认证改变后怎么办

Session 使用 `McpRefresh` 协调刷新。

当出现这些变化时，runtime 可能被标记 dirty：

- auth identity 或 token 改变；
- plugin 可用状态改变；
- capability roots 改变；
- MCP 配置被更新；
- 之前认证失败的 server 获得了新 OAuth credentials。

`refresh_mcp_if_dirty()` 会串行化刷新：

```text
acquire refresh gate
→ claim dirty generation
→ 重新计算 desired state
→ 构造 runtime projection
→ publish 新 McpRuntime
→ 若刷新期间又变 dirty，则继续一轮
```

这样可以避免多个调用同时重建连接并互相覆盖。

---

## 39. 什么情况下复用连接，什么情况下重连

刷新不等于全部断开重连。

若旧连接的 identity、协议模式、环境、认证和目录限制仍兼容，`McpConnectionSet::new()` 可以复用 ready client。

以下情况通常需要新连接：

- transport 或 server 配置改变；
- auth identity 改变；
- 执行环境改变；
- 协议模式不匹配；
- 调用方明确要求 fresh reconnect；
- Codex Apps 在认证后需要重新发现 exact tools。

`replace_fresh()` 和 `reconnect_on_next_refresh()` 表达了两种明确重连意图。

---

## 40. Tool Catalog Cache 能做什么、不能做什么

缓存可以：

- 减少每个 thread 首次发现工具的等待；
- 在 SubAgent 中支持 lazy startup；
- 让非执行型目录页面先展示已知 metadata；
- 给 optional server 一个较平滑的启动体验。

缓存不能：

- 证明 server 当前在线；
- 代替 exact client；
- 保证旧 annotations 仍可信；
- 绕过 catalog revision；
- 绕过认证和审批。

一句话：**缓存解决发现速度，不授予执行权。**

---

## 41. 断线、超时和 Shutdown

### 启动超时

覆盖 initialize 和初次工具发现。错误提示会建议调整 `startup_timeout_sec`。

### 工具超时

只覆盖某次调用，使用 server 捕获的 `tool_timeout_sec`。

### HTTP 短暂故障

Streamable HTTP 对明确可重试的初始化或 session 失效场景有受控恢复逻辑，不代表所有业务错误都会重试。

### stdio 子进程退出

transport 会关闭，后续调用失败；刷新可能建立新 client。退出原因常要结合 server stderr 和 Codex 日志判断。

### Thread 关闭

`McpRuntime::shutdown()` 让连接集合关闭 clients；本地 stdio client 还会终止它拥有的 server process。

---

## 42. 状态分别活多久

| 状态 | 大致作用域 | 例子 |
|---|---|---|
| 原始配置 | 配置层 | `mcp_servers.tickets` |
| Effective server 集合 | 一次 runtime projection | 合并 Plugin/App/策略后的结果 |
| `McpRuntime` | Thread | 当前已发布连接状态 |
| `McpConnectionSet` | 一个 runtime generation | 一批精确连接和目录 revision |
| `McpBinding` | 一个或多个兼容 sampling step | 冻结目录和 clients |
| `PreparedMcpCall` | 某 binding 中某工具的执行授权 | exact client + metadata + revision |
| `McpToolCallItem` | 一次 tool call | started/completed/failed UI item |
| `FunctionCallOutput` | 对话历史 / 下一次 sampling | 返回给模型的工具结果 |

这张表是理解 MCP 实现最有效的速查表。

---

## 43. 与 Skill、Plugin、App 和 Code Mode 的关系

### Skill

告诉模型怎样完成某类任务，可以声明所需 MCP server 或 tool dependency。Skill 本身不是连接。

### Plugin

可以携带 MCP server 配置、Skill 和归属 metadata。安装 Plugin 不等于自动批准其中所有工具。

### App / Connector

面向用户授权的具体服务。当前实现中 Codex Apps 可以通过保留的 MCP server 汇入工具目录，并叠加 app policy。

### Code Mode

可以用代码组合 MCP 工具，减少模型逐次编排的负担；底层工具仍来自当前 runtime，仍受暴露、安全和调用规则控制。

可以记成：

```text
Skill：教你怎样做
Plugin：把能力打包交付
App：连接到某个已授权服务
MCP：规定怎样连接和调用
Tool：一次具体动作
Code Mode：用代码组合多个动作
```

---

## 44. 完整例子：读取工单并总结

用户说：

> 查看 INC-42，并告诉我最可能的根因。

### 44.1 Thread 初始化

1. 配置层解析 `mcp_servers.tickets`；
2. Core 计算 effective server；
3. `McpRuntime` 发布 `McpConnectionSet`；
4. stdio launcher 启动 `ticket-mcp-server`；
5. Client 和 Server initialize；
6. Client 调用 `tools/list`；
7. `get_ticket` 经过 filter 和名称规范化。

### 44.2 Step 创建

1. Step 捕获 `McpBinding`；
2. Binding 保存 exact client、tool metadata 和 revision；
3. `McpHandler` 把工具 spec 注册进 ToolRouter；
4. 模型看到 `mcp__tickets__get_ticket`。

### 44.3 模型提出调用

```json
{
  "ticket_id": "INC-42"
}
```

### 44.4 Runtime 执行

1. 参数 JSON 解析成功；
2. 当前 binding 中找到 prepared call；
3. policy 判断它是只读，可按当前配置执行；
4. 发出 `McpToolCallItem::InProgress`；
5. revision 校验通过；
6. exact client 发送 `tools/call`；
7. server 查询真实工单系统。

### 44.5 返回结果

1. Server 返回文本和结构化字段；
2. Codex 清理并截断到允许大小；
3. 发出 `Completed` item；
4. 构造 `FunctionCallOutput(call_7)`；
5. 下一次模型采样读取工单；
6. 模型结合日志和描述生成根因分析。

这个例子中，只有第 6 步的“根因分析”由模型完成；真实工单读取由 MCP server 完成。

---

## 45. 常见误解

### 误解 1：“MCP server 就是一个模型”

不是。它通常是工具或数据服务，可能完全不包含 LLM。

### 误解 2：“模型自己访问外网调用 MCP”

不是。模型生成 tool call，Codex client/runtime 才持有 transport 和 credentials。

### 误解 3：“`tools/list` 返回了，就一定能执行”

不一定。还要经过过滤、binding、exact client、revision、审批、认证和 timeout。

### 误解 4：“readOnlyHint 为 true，所以绝对安全”

不对。它是 server 声明的 hint，应与 Host policy、服务信誉和实际行为一起判断。

### 误解 5：“Sandbox 会自动限制远程系统中的副作用”

不对。本地 sandbox 主要限制本地执行边界；远程服务副作用还靠 approval、MCP policy 和外部账号权限。

### 误解 6：“MCP 工具结果就是最终回答”

通常不是。结果作为 `function_call_output` 回到模型，模型再继续推理。

### 误解 7：“刷新目录只影响下一次调用，与正在执行的 step 无关”

当前实现会用 catalog revision 拒绝已经过期的 prepared call，避免旧目录动作在新策略下偷偷执行。

### 误解 8：“缓存目录等于缓存了连接”

不是。Tool metadata cache 和 exact live client 是两种不同资产。

---

## 46. 故障排查表

| 现象 | 优先检查 | 可能原因 |
|---|---|---|
| Server 完全不出现 | effective config、`enabled`、project trust | 配置未加载或被策略禁用 |
| 启动失败 | command/URL、cwd、env、stderr | 程序不存在、路径错误、HTTP 不通 |
| 要求登录 | auth status、bearer env、OAuth store | token 缺失或过期 |
| Server ready 但工具少 | allow/deny list、visibility、catalog limit | 工具被过滤或隐藏 |
| 模型看见工具但调用被拒 | binding revision、app policy、approval | 目录已刷新或权限不允许 |
| 调用一直等 | `tool_timeout_sec`、server logs | server 卡住或下游服务慢 |
| 返回内容被截断 | model truncation policy、结果大小 | 外部输出过大 |
| 第一次 SubAgent 看见缓存工具但稍后才可调用 | LazyWhenCached、exact client | 目录先于连接就绪 |
| 修改配置后仍像旧配置 | dirty refresh、连接 identity、重启边界 | runtime 尚未刷新或表面不支持热刷新 |
| Thread 关闭后 stdio 进程仍异常存在 | shutdown 日志、进程树 | server 忽略终止或 launcher 清理失败 |

排查时按层次走，不要一看到“工具失败”就只查 Prompt：

```text
配置 → 环境 → transport → initialize → tools/list
→ filter → binding → approval → tools/call → result conversion
```

---

## 47. 怎样阅读这一部分源码

### 第一遍：只认四个对象

1. `McpRuntime`：thread 的可刷新 MCP 状态；
2. `McpConnectionSet`：一代连接集合；
3. `McpBinding`：step 捕获的不可变目录；
4. `PreparedMcpCall`：某工具的 exact execution authority。

### 第二遍：追启动链

```text
Session::install_initial_mcp_runtime
→ publish_mcp_runtime
→ McpRuntime::replace
→ McpConnectionSet::new
→ AsyncManagedClient
→ make_rmcp_client
→ RmcpClient::initialize
→ list_tools_for_client_uncached
```

### 第三遍：追调用链

```text
McpHandler::handle_call
→ handle_mcp_tool_call
→ Session::prepare_mcp_call
→ McpBinding::prepare_call
→ approval / hooks
→ PreparedMcpCall::call_with_preparation
→ RmcpClient::call_tool
→ McpToolOutput::to_response_item
```

### 第四遍：专门读失败测试

搜索：

- `required MCP servers failed`；
- `startup timed out`；
- `authentication required`；
- `catalog changed`；
- `cached tools`；
- `elicitation`；
- `mcp_tool_exposure`。

失败测试最能说明作者真正要保护的边界。

---

## 48. 本章名词表

完整总表见[课程术语表](glossary.md)。

| 名词 | 代码中的常见写法 | 通俗解释 |
|---|---|---|
| Model Context Protocol | MCP | Host 与外部工具/上下文服务通信的标准协议 |
| Host | Codex | 拥有模型、会话、安全策略和用户界面的应用 |
| MCP Client | `RmcpClient` | 连接一个 MCP server 并发送协议请求的客户端 |
| MCP Server | `McpServerConfig` / server | 暴露工具、资源等能力的外部服务 |
| Transport | stdio / Streamable HTTP | MCP 消息实际经过的传输通道 |
| Handshake | `initialize` | Client 与 Server 建立协议会话并交换能力 |
| Capability | capabilities | 握手时声明支持哪些协议功能 |
| Tool discovery | `tools/list` | 从 Server 获取工具目录 |
| Resource | MCP resource | Server 暴露的可读取上下文数据 |
| Elicitation | elicitation | Server 反向请求 Host/用户补充信息或确认 |
| Effective server | `EffectiveMcpServer` | 合并配置、Plugin、环境和策略后的实际 server |
| Runtime projection | `McpRuntimeProjection` | 针对当前 thread/step 计算出的 MCP 配置视图 |
| MCP Runtime | `McpRuntime` | 一条 thread 当前发布的 MCP 连接状态 |
| Connection set | `McpConnectionSet` | 同一代 runtime 中的一组 server 连接 |
| Tool info | `ToolInfo` | 同时保存原始 MCP 身份和模型可见身份的工具元数据 |
| Tool filter | `ToolFilter` | 应用 enabled/disabled tool 规则的过滤器 |
| Catalog | tool catalog | 当前发现并允许展示的工具目录 |
| Catalog revision | `catalog_revision` | 标识工具目录版本的递增编号 |
| Binding | `McpBinding` | Step 冻结的工具目录、client 和 prepared calls |
| Prepared call | `PreparedMcpCall` | 绑定 exact client、tool、policy 与 revision 的可执行调用 |
| Exact client | exact ready client | 与当前目录和身份完全对应的已就绪连接 |
| Approval mode | `AppToolApproval` | 某 server/tool 应自动、写入时或每次怎样审批 |
| Annotation | `ToolAnnotations` | Server 为工具提供的只读、破坏性等提示元数据 |
| OAuth | OAuth credentials | 远程服务的授权与 token 刷新机制 |
| Tool timeout | `tool_timeout_sec` | 单次 MCP tool call 的等待上限 |
| Startup timeout | `startup_timeout_sec` | initialize 与首次发现工具的等待上限 |
| Tool result | `CallToolResult` | MCP Server 返回的内容、结构化数据和错误标记 |
| Function output | `FunctionCallOutput` | 把工具结果与 call ID 对应后交回模型的输入项 |
| Dirty refresh | `refresh_mcp_if_dirty` | 状态变化后重新计算并发布 MCP runtime |
| Lazy startup | `LazyWhenCached` | 有缓存目录时把 optional server 延迟到需要时启动 |
| Tool catalog cache | `McpToolCatalogCache` | 缓存工具 metadata 以改善发现速度，不代表执行权限 |

### 代码单词和短语拆解

- `context`：上下文；模型完成任务时可使用的信息。
- `protocol`：协议；双方共同遵守的消息和状态规则。
- `host`：宿主；承载模型和 MCP client 的应用。
- `client` / `server`：发起协议请求的一侧 / 提供能力并响应的一侧。
- `transport`：传输层；消息实际怎样移动。
- `stdio`：standard input/output，标准输入和标准输出。
- `streamable`：可流式传输的。
- `initialize`：初始化并协商协议能力。
- `capability`：能力声明，不等于已授权的具体动作。
- `discover` / `list`：发现 / 列出目录。
- `effective`：合并所有来源和限制后实际生效的。
- `projection`：从更完整状态计算出的当前视图。
- `runtime`：运行期对象和状态。
- `connection set`：同一代发布状态中的连接集合。
- `binding`：把多个必须一致的对象固定关联起来。
- `prepared`：已解析目标并冻结执行依据的。
- `catalog`：可发现项目的目录。
- `revision`：某份数据的版本编号。
- `normalize`：把不同来源的名字转换成统一合法形式。
- `sanitize`：清理不合法或不应继续传播的内容。
- `filter`：根据规则保留或移除项目。
- `allow-list` / `deny-list`：允许清单 / 拒绝清单。
- `annotation`：附加说明性元数据。
- `approval`：执行动作前的允许决定。
- `elicitation`：向用户引出额外信息的请求。
- `credential`：证明访问身份的凭据。
- `bearer token`：持有即可用于请求认证的 token。
- `refresh`：根据新配置或身份重新发布状态。
- `reconnect`：放弃或替换旧连接后重新连接。
- `reuse`：确认身份兼容后继续使用已有连接。
- `dirty`：状态已经变化，需要刷新。
- `lazy` / `eager`：需要时再启动 / 尽早启动。
- `timeout`：等待超过上限后停止。
- `shutdown`：有序关闭连接和受管理进程。
- `call_id`：把一次调用与它的结果对应起来的标识符。
- `structured content`：便于程序继续处理的结构化结果。
- `is_error`：Server 对一次正常协议响应给出的业务失败标记。
- `TOCTOU`：time-of-check to time-of-use，检查与使用之间状态发生变化的问题。

---

## 49. 自测题

1. 为什么 Codex 同时是 MCP Host，又包含 MCP Client？
2. stdio server 和 Streamable HTTP server 分别由谁启动和关闭？
3. initialize 成功后为什么还要调用 `tools/list`？
4. `ToolInfo` 为什么同时保存原始名字和模型可见名字？
5. `enabled_tools` 与 `disabled_tools` 同时包含一个工具时，哪个生效？
6. Required server 与 Optional server 启动失败的影响有什么不同？
7. LazyWhenCached 为什么不能只靠缓存直接执行工具？
8. `McpRuntime` 与 `McpBinding` 的作用域有什么区别？
9. `PreparedMcpCall` 为什么不能只保存一个 client？
10. Catalog revision 防止了哪类竞态？
11. 模型能否看到 bearer token 和 OAuth refresh token？
12. `readOnlyHint` 为什么只是 hint，不是安全证明？
13. Tool approval、sandbox 和外部账号 RBAC 分别控制什么？
14. Rust `Err` 与 `CallToolResult.is_error = true` 有什么区别？
15. MCP 工具结果怎样进入下一次模型采样？
16. Elicitation 与模型调用 `request_user_input` 有什么区别？
17. MCP tool catalog cache 能保证什么，不能保证什么？
18. 修改认证后，`refresh_mcp_if_dirty()` 为什么要串行化发布？

---

## 50. 源码检查点

建议按以下顺序对照：

1. `codex-rs/config/src/mcp_types.rs`
   - 找 `McpServerConfig`、`McpServerAuth`、`McpServerTransportConfig`；
   - 把连接字段、过滤字段和审批字段分成三组。
2. `codex-rs/core/src/session/mcp_runtime.rs`
   - 找 `McpDesiredState`、`install_initial_mcp_runtime`、`build_mcp_runtime_input`；
   - 观察 SubAgent 为什么选择 `LazyWhenCached`。
3. `codex-rs/codex-mcp/src/runtime.rs`
   - 找 `McpRuntime`、`PublishedMcpRuntime`、`replace`、`current_binding_for_call` 和 `shutdown`；
   - 解释 `ArcSwap` 发布模型。
4. `codex-rs/codex-mcp/src/connection_manager.rs`
   - 阅读 `McpConnectionSet::new`；
   - 找连接复用、deferred startup、startup status 和 summary event。
5. `codex-rs/codex-mcp/src/rmcp_client.rs`
   - 追 `make_rmcp_client`、`start_server_task`、initialize 与初次 list tools。
6. `codex-rs/rmcp-client/src/stdio_server_launcher.rs`
   - 看本地进程的 command、cwd、env、stdin/stdout/stderr 和终止逻辑。
7. `codex-rs/rmcp-client/src/rmcp_client.rs`
   - 找 `initialize`、`list_tools`、`call_tool`、OAuth refresh、session recovery 和 `shutdown`。
8. `codex-rs/codex-mcp/src/tools.rs`
   - 找 `ToolInfo`、`ToolFilter` 和 `normalize_tools_for_model_with_prefix`。
9. `codex-rs/codex-mcp/src/connection_manager/tool_catalog.rs`
   - 追 `capture_binding_with_metadata`；
   - 看 optional grace、cached tools、exact client 与 revision。
10. `codex-rs/codex-mcp/src/binding.rs`
    - 对比 `McpBinding` 和 `PreparedMcpCall`；
    - 找 catalog-changed rejection。
11. `codex-rs/core/src/tools/handlers/mcp.rs`
    - 看 ToolInfo 怎样变成 ToolSpec 和 McpHandler；
    - 找 `wait_until_ready` 与 tool lifecycle hooks。
12. `codex-rs/core/src/mcp_tool_call.rs`
    - 追参数解析、prepared call、App policy、approval、execution、telemetry 和 item events。
13. `codex-rs/core/src/tools/context.rs`
    - 看 `McpToolOutput::to_response_item` 与 `response_payload`；
    - 解释为什么普通上下文要截断，而 Code Mode 可保留更原始结果。
14. `codex-rs/core/src/tools/handlers/mcp_resource.rs`
    - 对比 list/read resource 与普通 tool call。
15. `codex-rs/core/src/session/mcp.rs`
    - 找 `refresh_mcp_if_dirty`、`mcp_runtime_for_step`、elicitation 和 hard refresh。
16. `codex-rs/codex-mcp/src/connection_manager/required.rs`
    - 看 required server 失败怎样被汇总。
17. `codex-rs/core/tests/suite/rmcp_client.rs`、`mcp_tool_exposure.rs`、`mcp_tool_cache.rs`
    - 用集成测试验证调用、暴露和缓存行为。
18. `codex-rs/core/tests/suite/mcp_auth_refresh.rs`、`mcp_auth_elicitation.rs`、`hooks_mcp.rs`
    - 验证认证刷新、elicitation 和 hooks 边界。

---

## 51. 一句话总结

Codex 先把多来源配置投影成 thread-owned `McpRuntime`，通过 stdio 或 Streamable HTTP 完成 initialize 与工具发现，再让每个 step 捕获不可变 `McpBinding`；模型只能建议调用，真正执行必须使用带 exact client、policy 和 catalog revision 的 `PreparedMcpCall`，结果经过安全转换后以 `FunctionCallOutput` 回到下一次模型采样。
