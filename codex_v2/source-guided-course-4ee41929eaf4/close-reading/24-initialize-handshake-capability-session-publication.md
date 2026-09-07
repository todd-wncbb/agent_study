# 源码精读 24：Initialize 握手、capability 协商与连接状态发布

> 源码基线：`4ee41929eaf4`。本文只描述该固定提交中的实现。以后源码移动时，请搜索符号名，不要依赖行号。

上一篇已经知道：普通 client request 在进入 handler 前，必须通过 `session.initialized()` 检查。

但“完成 initialize”到底意味着什么？只是把一个 `bool` 改成 `true` 吗？

不是。固定提交中的 initialize 至少同时影响五类状态：

```text
initialize params
  ├─ connection session：client info 与 capabilities
  ├─ process identity：originator、User-Agent suffix、residency
  ├─ outbound projection：initialized、experimental、notification opt-out
  ├─ thread routing：live connection 与 attestation capability
  └─ future thread creation：client name/version 与 MCP extensions
```

本篇要解决的核心问题是：

> 一次 initialize 怎样从“不可信的客户端声明”，变成服务器后续可以依赖的 connection state？为什么 session 已经提交、initialize response 已经入队以后，还不能立刻把该连接视为完全 outbound-ready？

---

## 1. 先给最重要的结论

固定提交中的 initialize 可以拆成七个阶段：

1. raw request 已经被解码成 `ClientRequest::Initialize`；
2. 拒绝同一连接的重复初始化；
3. 为缺省 capabilities 填默认值，并筛选受支持的 MCP extensions；
4. 验证 `clientInfo.name`；
5. 用 `OnceLock` 一次性提交 connection session；
6. 更新若干进程级 identity/HTTP client metadata，并发送 initialize response；
7. transport owner 镜像发送侧 capability、发送初始通知、注册 thread capability，最后开放普通广播。

最值得记住的三句话：

> `OnceLock` 提交的是 connection-local 协议状态，不是整个初始化发布流程的最后一步。

> initialize response 是定向 response，不受 broadcast initialized gate 阻挡。

> “client capability negotiation”在当前协议里主要是客户端声明、服务器据此改变行为；response 不回显一份服务器接受后的 capability 清单。

---

## 2. 贯穿全文的案例

假设 WebSocket 客户端 A 发送：

```json
{
  "id": 1,
  "method": "initialize",
  "params": {
    "clientInfo": {
      "name": "study_client",
      "title": "我的学习客户端",
      "version": "1.2.0"
    },
    "capabilities": {
      "experimentalApi": true,
      "requestAttestation": true,
      "optOutNotificationMethods": ["item/agentMessage/delta"],
      "extensions": {
        "openai/form": {},
        "io.modelcontextprotocol/ui": {
          "mimeTypes": ["text/html;profile=mcp-app"]
        },
        "example/unknown": {"enabled": true}
      }
    }
  }
}
```

服务器不会把整个 params 原封不动存下来，而是：

- 保存 name 与 version；
- 当前 initialize processor 不使用 title；
- 保存 experimental 与 attestation 两个 boolean；
- 把 opt-out `Vec` 变成便于查找的 `HashSet`；
- MCP extensions 只保留 `openai/form` 和 `io.modelcontextprotocol/ui`；
- 丢弃不认识的 `example/unknown`；
- 把整理后的状态一次性写入 connection session；
- 回应 user agent、Codex home 和服务器平台；
- 再让 outbound router 看见 experimental/opt-out 投影；
- 发送该连接的 config warnings 与 remote-control status；
- 注册该连接可以回答 attestation request；
- 最后才允许普通 broadcast 把它选为目标。

---

## 3. 第一张源码地图

| 层级 | 固定提交路径 | 重点符号 |
|---|---|---|
| initialize wire types | `app-server-protocol/src/protocol/v1.rs` | `InitializeParams`、`ClientInfo`、`InitializeCapabilities`、`InitializeResponse` |
| method DSL | `app-server-protocol/src/protocol/common.rs` | `Initialize => "initialize"` |
| session owner | `app-server/src/message_processor.rs` | `ConnectionSessionState`、`InitializedConnectionSessionState` |
| initialize 主流程 | `app-server/src/request_processors/initialize_processor.rs` | `InitializeRequestProcessor::initialize` |
| process identity | `login/src/auth/default_client.rs` | originator、`USER_AGENT_SUFFIX`、residency、user agent |
| MCP capability 筛选 | `codex-mcp/src/client_capabilities.rs` | `client_mcp_extensions` |
| transport 后处理 | `app-server/src/lib.rs` | initialize transition branch |
| in-process 后处理 | `app-server/src/in_process.rs` | typed initialize publication |
| outbound projection | `app-server/src/transport.rs` | `OutboundConnectionState`、broadcast/filter |
| thread connection capability | `app-server/src/thread_state.rs` | `ConnectionCapabilities` |
| attestation consumer | `app-server/src/attestation.rs` | capable connection selection 与 reverse request |
| thread consumer | `app-server/src/request_processors/thread_processor.rs` | client info 与 MCP extensions 传递 |

---

## 4. initialize 是 v1 类型，但服务于整个 App-server 连接

`InitializeParams` 和 `InitializeResponse` 定义在 `protocol/v1.rs`，而大量后续 API 位于 v2。

这不表示“初始化后只能使用 v1”。它表示固定协议复用了既有 initialize handshake 类型，连接完成这次握手后才能调用普通 typed requests，包括 v2 methods。

不要把“类型文件位于 v1”误读成“它创建一个只支持 v1 的 session”。

---

## 5. wire 上的最小结构

结构定义是：

```rust
pub struct InitializeParams {
    pub client_info: ClientInfo,
    pub capabilities: Option<InitializeCapabilities>,
}
```

因此最小概念结构是：

```json
{
  "clientInfo": {
    "name": "study_client",
    "version": "1.0.0"
  }
}
```

`clientInfo` 是必需字段；`capabilities` 可以省略。`title` 是 `Option<String>`，也可以省略或为 null。

---

## 6. `Default` 不等于所有 wire 字段都可缺省

这些 structs 派生了 `Default`，只是 Rust 代码可以写：

```rust
InitializeParams::default()
```

Serde 是否允许 JSON 缺少某字段，还取决于字段类型和 annotation。

- `capabilities: Option<_>` 缺少时自然是 `None`；
- `title: Option<_>` 缺少时自然是 `None`；
- `clientInfo`、`name`、`version` 没有 `#[serde(default)]`，正常 typed decode 仍要求它们存在。

“类型实现 Default”与“协议字段 optional”是两件事。

---

## 7. `ClientInfo` 的三个字段

| 字段 | 类型 | initialize processor 怎样使用 |
|---|---|---|
| `name` | `String` | 验证、session 保存、originator/UA、tracing、thread client info |
| `title` | `Option<String>` | 当前 processor 解构为 `_title`，不写入 session |
| `version` | `String` | session 保存、UA suffix、tracing、thread compatibility behavior |

`title` 存在于 wire contract，不代表当前执行路径一定消费它。读源码时要区分“协议接收”与“业务使用”。

---

## 8. 五类 initialize capabilities

固定类型包含：

| wire 字段 | Rust 字段 | 默认 | 作用 |
|---|---|---:|---|
| `experimentalApi` | `experimental_api` | false | 允许实验 request/field，并接收实验 notification |
| `requestAttestation` | `request_attestation` | false | 允许服务器向该客户端发 `attestation/generate` reverse request |
| `mcpServerOpenaiFormElicitation` | 同名 snake_case | false | 旧版 `openai/form` opt-in |
| `optOutNotificationMethods` | 同名 snake_case | None | 按精确 method 名过滤通知 |
| `extensions` | `extensions` | None | 声明客户端支持的 MCP extensions 与配置 |

这些字段描述客户端能力或偏好，不是服务器功能总开关。

---

## 9. capabilities 整体缺省时发生什么

主流程写的是：

```rust
let capabilities = params.capabilities.unwrap_or_default();
```

因此省略整个 capabilities 等价于：

```text
experimental_api = false
request_attestation = false
legacy openai/form = false
opt_out methods = empty
extensions = empty
```

这是一种保守默认：未明确声明，就不假设客户端支持实验协议、attestation 或 MCP UI/form extensions。

---

## 10. boolean 字段缺省也会变成 false

`InitializeCapabilities` 中几个 boolean 带 `#[serde(default)]`。

所以即使 capabilities object 存在：

```json
{"capabilities": {}}
```

也不会因缺少三个 boolean 而反序列化失败，而是全部 false。

这有利于协议向后扩展：旧客户端不必知道后来新增的 opt-in boolean。

---

## 11. 这里的 negotiation 不是完整双向协商

日常说“能力协商”，容易想象：

```text
client offers A/B/C
server replies accepted A/C
```

但当前 `InitializeResponse` 只有：

- `userAgent`；
- `codexHome`；
- `platformFamily`；
- `platformOs`。

它不回显 accepted capabilities。

所以更精确的描述是：

> 客户端在 initialize 中声明 capability 与偏好，服务器筛选并保存自己认识的部分，后续按这些声明改变行为。

客户端不能仅从 response payload 得到一份“最终协商结果清单”。

---

## 12. initialize 在普通 initialized gate 之前分叉

`handle_client_request` 先匹配：

```rust
if let ClientRequest::Initialize { request_id, params } = codex_request {
    initialize_processor.initialize(...).await?;
    return Ok(());
}
```

其他 request 才进入 `dispatch_initialized_client_request` 并检查 `session.initialized()`。

这个顺序表达协议状态机：

```text
Uninitialized
  ├─ initialize 成功 → Initialized
  └─ 普通 request → Not initialized
```

---

## 13. 第一道 duplicate 检查

initialize processor 一开始检查：

```rust
if session.initialized() {
    return Err(invalid_request("Already initialized"));
}
```

这为常见重复请求提供快速、清楚的错误。

但它不是唯一的并发安全保障，因为“检查后、提交前”仍可能发生竞争。

---

## 14. 为什么还需要第二道 duplicate 检查

真正提交时：

```rust
session.initialize(InitializedConnectionSessionState { ... })
```

内部是：

```rust
self.initialized.set(session).map_err(|_| ())
```

只有一个并发调用能成功 `OnceLock::set`。失败者同样得到 `Already initialized`。

所以两道检查分工是：

- 第一层改善普通重复请求的快速路径；
- `OnceLock::set` 提供真正的原子 first-writer-wins 提交。

---

## 15. `OnceLock` 表达单向状态转换

`ConnectionSessionState` 创建时：

```text
initialized = empty
```

成功提交后：

```text
initialized = Some(InitializedConnectionSessionState)
```

没有 reset 或 update API。

若客户端想改变 experimental、opt-out、attestation 或 extensions，当前协议不是在同一连接上再 initialize，而是建立新的连接并重新握手。

---

## 16. 为什么不是五六个独立可变字段

如果分别写：

```text
experimental = true
opt_out = {...}
client_name = ...
request_attestation = true
```

其他 task 可能在中途看到“半初始化”状态。

把它们先构造成完整 `InitializedConnectionSessionState`，再一次放入 `OnceLock`，读者只会看到：

```text
完全未初始化
或
一份完整一致的 initialized session
```

这是原子发布复合状态的常见设计。

---

## 17. capability 先被归一化，再进入 session

主流程不是把 `InitializeCapabilities` 原样保存，而是抽取：

```text
experimental bool
request_attestation bool
opt-out Vec → HashSet
extensions → filtered ClientMcpExtensions
```

`InitializedConnectionSessionState` 是运行时需要的精简形状，不是 wire DTO 的副本。

这样后续代码不必每次重新处理 `Option`、legacy alias 或未知 extension。

---

## 18. opt-out 从 `Vec` 变成 `HashSet`

wire 用数组方便客户端表达：

```json
["thread/started", "item/agentMessage/delta"]
```

session 存为 `HashSet<String>`，因为 outbound router 的常见操作是：

```text
这个 notification method 是否在集合中？
```

重复 method 会自然去重；顺序没有协议意义。

---

## 19. opt-out 是精确 method 名匹配

outbound router 取 notification 的 wire method string，然后做：

```rust
opted_out_notification_methods.contains(method.as_str())
```

所以它不是 prefix、glob 或正则：

```text
"thread/started" 只匹配 thread/started
"thread/*" 不会自动匹配所有 thread 通知
```

写错一个字符就不会生效。

---

## 20. opt-out 同时影响 targeted 与 broadcast notification

`send_message_to_connection` 无论来源是 `ToConnection` 还是 Broadcast，都会调用 notification filter。

因此 opt-out 不只是“全局广播退订”。即使业务代码明确 targeting 该 connection，若消息是被退订的 notification，也会被跳过。

Response、Error 与 reverse Request 不受 notification opt-out 影响。

---

## 21. experimental capability 有 inbound 与 outbound 两面

### Inbound

普通 typed request 的 method 或字段若带 experimental reason，而 session 未启用，就返回 invalid request。

### Outbound

- 实验 notification 在未启用连接上被过滤；
- 某些 reverse request 的实验字段会按连接裁剪。

所以它不是“只让客户端多调用几个 API”，还改变服务器发给该连接的 wire shape。

---

## 22. experimental capability 当前是 per-connection

A、B 两个连接可以分别声明：

```text
A.experimental = true
B.experimental = false
```

相同实验 request 在 A 上通过、B 上失败；相同实验 notification 也只对 A 可见。

源码 TODO 还指出：共享 thread 场景可能出现奇怪的跨客户端行为，例如一个连接启用的动态能力影响共享工作，而另一个连接未 opt in。

这说明“per-connection”是当前事实，不代表所有跨连接语义已经最终定型。

---

## 23. MCP extensions 不是照单全收

客户端可以传任意 extension map：

```json
{
  "openai/form": {},
  "io.modelcontextprotocol/ui": {...},
  "example/other": {...}
}
```

`client_mcp_extensions` 只保留服务器认识且愿意向下游投影的 namespace：

- `openai/form`；
- `io.modelcontextprotocol/ui`。

未知 extension 被丢弃，而不是因为客户端声明就自动获得新能力。

---

## 24. 为什么必须过滤未知 extension

若服务器把任意客户端 map 原样转发给 MCP server，就等于让客户端伪造服务器未审查的 capability namespace。

当前筛选建立一个 trust boundary：

```text
client-declared map
  → server allowlist
  → trusted ClientMcpExtensions
  → thread/Core/MCP downstream
```

这与普通“保留未知字段以便前向兼容”不同；capability 会改变下游行为，因此需要明确选择。

---

## 25. legacy form boolean 怎样归一化

旧客户端可能只发送：

```json
{"mcpServerOpenaiFormElicitation": true}
```

helper 会把它转换成：

```json
{"openai/form": {}}
```

并放入同一个 `ClientMcpExtensions` map。

这样后续 thread/Core/MCP 代码只处理一种现代表示，不必处处判断 legacy boolean。

---

## 26. 新旧 form 声明同时出现时谁优先

代码使用：

```rust
selected.entry("openai/form").or_insert_with(empty_object)
```

因此：

- extensions 已显式提供 `openai/form` 时，保留它的 value；
- 只有不存在时，legacy true 才补一个空 object。

新式、信息更丰富的声明不会被 legacy fallback 覆盖。

---

## 27. request attestation capability 保存了什么承诺

`requestAttestation: true` 表示该客户端愿意处理服务器发出的：

```text
attestation/generate
```

reverse request。

initialize 本身不会立刻生成 attestation。它只是把能力登记到 live connection metadata；以后某条 thread 的上游请求需要 attestation header 时，provider 才选择合适连接并询问客户端。

---

## 28. attestation capability 还要与 thread subscription 相交

选择连接时不是看“所有已初始化连接”，而是：

1. 找到该 thread 的 subscribers；
2. 只保留 live connections；
3. 只保留 `request_attestation == true`；
4. 取最小 `ConnectionId`。

所以 capability 只是必要条件。未订阅该 thread 的客户端不会因为声明 attestation 就接管它的请求。

---

## 29. attestation 是反向请求，不是 notification

被选中的客户端要返回 token。服务器对它设置 100ms timeout，并处理：

- 成功 token；
- client error；
- callback canceled；
- timeout；
- malformed response。

这再次说明 capability 是“我能处理这种协议动作”的声明，而不是“服务器单方面打开一个 boolean 后就完成了功能”。

---

## 30. `clientInfo.name` 在提交前必须验证

代码使用 `HeaderValue::from_str(&name)`。

若 name 含回车换行等非法 header 字符，返回：

```text
code: -32600
message: Invalid clientInfo.name: ... Must be a valid HTTP header value.
```

测试 `initialize_rejects_invalid_client_name` 用 `bad\rname` 固定了这条边界。

---

## 31. 为什么 name 要符合 HTTP header 规则

name 后续可能成为：

- process default originator；
- User-Agent 的一部分；
- outbound HTTP request metadata。

如果不在 trust boundary 先验证，换行等字符可能破坏 header 结构。

这里不是为了让显示名称“好看”，而是因为它会进入协议级 metadata。

---

## 32. title 与 version 当前没有同样的显式预验证

initialize processor：

- 把 `title` 解构为 `_title`；
- 保存 `version`，并把 `name; version` 作为 UA suffix 候选。

`get_codex_user_agent` 后续会对最终 candidate 做 header sanitation/fallback，但 initialize 入口没有像 name 一样直接拒绝非法 version。

因此不要泛化成“所有 ClientInfo 字段都在 initialize 时按 header 规则验证”。固定代码只显式验证 name。

---

## 33. 为什么必须先验证，再 `OnceLock::set`

顺序是：

```text
解析 capability
→ 验证 name
→ 构造完整 session value
→ OnceLock::set
```

如果先 set 后验证失败，连接会陷入：

```text
服务器认为 initialized
但 initialize request 返回 error
客户端认为 handshake 失败
```

当前顺序保证 invalid name 失败时 session 仍为空，客户端可以修正后在同一连接重试。

---

## 34. `analytics_initialize_params` 为什么先 clone

代码随后会消费 `params.capabilities` 与 `params.client_info`。

为了在成功提交后记录完整原始 initialize 参数，它预先 clone：

```rust
let analytics_initialize_params = params.clone();
```

运行时 session 保存的是归一化结果；analytics 看到的是客户端原始声明。两者服务不同问题，不应强行共用一种数据形状。

---

## 35. session commit 是明确的不可逆点

`OnceLock::set` 成功前，错误可以让连接保持 Uninitialized。

成功后，后续代码对 originator 的失败只记 warning 或忽略特定错误，发送 response 的 helper 也不把 channel send failure返回给 initialize。

因此本函数的重要状态边界是：

```text
set 之前 → 可安全返回验证/竞争错误
set 之后 → connection 已被服务器认定为 initialized
```

即使客户端因断线没看到 response，也不能假设服务器自动回滚 session。

---

## 36. connection-local state 与 process-global state 不同

`InitializedConnectionSessionState` 是每连接一份。

但下面这些是进程级共享 metadata：

- default originator；
- `USER_AGENT_SUFFIX`；
- default client residency requirement。

所以一次连接握手既提交 local state，也可能影响同进程后续 HTTP clients 的全局默认信息。

---

## 37. originator 是什么

这里的 originator 可以理解为：

> 告诉上游 HTTP 服务“这次 Codex 调用主要由哪个宿主/客户端发起”的标识。

它不是 `ConnectionId`，也不是 UI 标题。固定默认值是 `codex_cli_rs`，真实宿主 initialize 后可能尝试把它设置为 client name。

---

## 38. 哪些 client name 不修改进程 identity

固定数组是：

```rust
["codex_app_server_daemon", "codex-backend"]
```

这两类更像探测/后端连接，不应把自己当作最终 originating client 覆盖进程 identity。

测试分别证明它们 initialize 后 response user agent 仍以 `codex_cli_rs/` 开头。

---

## 39. default originator 是 first successful set wins

`set_default_originator` 使用进程级 `RwLock<Option<Originator>>`：

- 未设置时写入；
- 已有值时返回 `AlreadyInitialized`；
- env override 存在时优先采用 env 值。

initialize 对 `AlreadyInitialized` 当前视为 no-op，不向客户端报错。

所以多连接场景不能把 process originator 理解为“总是等于当前这个 connection 的 name”。

---

## 40. env override 的优先级

若 `CODEX_INTERNAL_ORIGINATOR_OVERRIDE` 存在，`get_originator_value` 优先用它，而不是 initialize 传入的 name。

集成测试证明：客户端声明 `codex_vscode`，response user agent 仍以 env override 的 originator 开头。

这是一种外部配置高于 connection 声明的 precedence。

---

## 41. `USER_AGENT_SUFFIX` 是另一份 process-global state

对 originating client，initialize 构造：

```text
"{name}; {version}"
```

然后写入 `USER_AGENT_SUFFIX`。

`get_codex_user_agent()` 最终大致形成：

```text
originator/version (OS info; terminal info) (name; client-version)
```

注意 suffix 与 originator 是两个不同全局槽：originator first-set-wins，而 suffix 在每次 originating initialize 时都会尝试覆盖。

---

## 42. 多连接下 process identity 不是纯粹 per-connection

假设 A 先 initialize 为 `client_a`，B 后 initialize 为 `client_b`：

- default originator 很可能保留 A；
- USER_AGENT_SUFFIX 可能更新为 B 的 `name; version`；
- 两个 connection session 各自仍保存自己的 name/version。

这是固定实现中全局 singleton 与多连接共存的真实边界。不要从某一条 initialize response 推断整个进程所有 metadata 都只属于该连接。

---

## 43. residency requirement 也在 initialize 后刷新

initialize 调用：

```rust
set_default_client_residency_requirement(
    self.config.enforce_residency.value()
)
```

它不是客户端 capability，而是服务器 config/requirements 派生的进程级 HTTP client metadata。

放在 initialize 流程中，意味着连接开始使用服务前刷新默认 client 的 residency header 要求。

---

## 44. InitializeResponse 返回什么

```rust
pub struct InitializeResponse {
    pub user_agent: String,
    pub codex_home: AbsolutePathBuf,
    pub platform_family: String,
    pub platform_os: String,
}
```

它让宿主知道：

- 服务器最终计算的 User-Agent；
- 服务器实际使用的 `$CODEX_HOME` 绝对路径；
- 执行 app-server 的平台 family 与 OS。

这些是服务器运行环境事实，不是对客户端 capabilities 的逐项确认。

---

## 45. platform family 与 platform OS 的区别

示例：

```text
platformFamily = "unix"
platformOs = "macos"
```

family 是更粗的系统家族；OS 是具体目标系统。

远程/分离式架构中，这些描述的是 app-server target 所在平台，不一定等于 UI 客户端所在设备。

---

## 46. response 在 session commit 之后发送

顺序是：

```text
OnceLock commit
→ process identity updates
→ build InitializeResponse
→ outgoing.send_response
```

所以客户端收到成功 response 时，服务器 connection session 已经提交。

反向并不成立：send helper 只是把 response 放入 outgoing pipeline；服务器内部提交成功不保证客户端最终读到 response。

---

## 47. initialize response 为什么不需要 outbound initialized=true

response 使用 `OutgoingEnvelope::ToConnection`。

outbound router 的 `initialized` gate 只用于 Broadcast 目标选择；定向 response 可以在 flag 为 false 时发往该 connection。

否则会出现死锁式协议错误：必须 initialized 才能收到 initialize response，但必须收到 response 才知道 initialize 成功。

---

## 48. 发送 response 不等于物理写出

`send_response(...).await` 在这里等待 global outgoing channel 接受 envelope，不等待：

- outbound router 已路由；
- per-connection writer 已接收；
- JSON 已序列化；
- socket/stdio 已写出；
- 客户端已读取。

因此 initialize processor 的“完成”与客户端观察到握手成功之间仍隔着第 22 篇讲过的发送链路。

---

## 49. 为什么 processor 还返回一个 bool

initialize 的返回类型是：

```rust
Result<bool, JSONRPCErrorError>
```

这个 bool 不是“initialize 成功与否”；成功/失败已经由 `Result` 表达。

它表示：调用者是否要求 initialize processor 自己立即把 outbound connection 标为 ready。

- `Ok(true)`：in-process 路径已在函数内 store ready；
- `Ok(false)`：通用 transport 主循环稍后完成 staged publication。

---

## 50. 通用 transport 为什么传 `None`

stdio、Unix socket、WebSocket、remote-control transport 都通过 `TransportEvent` 主循环处理 raw request。

这条路径调用 initialize 时传：

```rust
outbound_initialized = None
```

于是 initialize processor 只提交 session、回 response，不立刻开放 broadcast。主循环拥有后续 publication 顺序。

源码注释常用 WebSocket 举例，但实际这段 event-loop 后处理服务于通用 transport connection state。

---

## 51. 主循环怎样检测初始化跃迁

处理 request 前：

```rust
let was_initialized = session.initialized();
```

处理后：

```rust
let is_initialized = session.initialized();
```

只有：

```text
was_initialized == false
is_initialized == true
```

才执行一次 post-initialize publication。

普通后续请求不会反复发送初始通知或重复注册 connection capability。

---

## 52. session state 与 outbound projection 为什么分开

incoming request handler 需要 async-friendly、typed connection session。

outbound router 是独立 task，为了快速过滤每条消息，持有：

- `AtomicBool initialized`；
- `AtomicBool experimental_api_enabled`；
- `RwLock<HashSet<String>> opted_out_notification_methods`。

这是一份发送侧 projection，而不是第二个权威 session。

权威能力先在 `OnceLock` 中提交，再镜像到 router 需要的最小读取结构。

---

## 53. 主循环为何在每个 request 后都刷新 projection

代码不只在 initialize branch 内写 projection，而是在每次 Request 处理后都 snapshot session：

```text
session.opted_out_notification_methods()
session.experimental_api_enabled()
session.initialized()
```

由于 session 当前不可重新 initialize，这在成功握手后通常是重复写相同值。

好处是后处理集中在一个位置：无论 request 最终是否恰好完成了初始化，owner 都按 session 事实刷新 projection，而不依赖 initialize handler额外回传整份状态。

---

## 54. post-initialize publication 的精确顺序

通用 transport 主循环在跃迁后依次：

1. session opt-out → outbound RwLock；
2. session experimental → outbound atomic；
3. 定向发送 startup config warnings；
4. 定向发送当前 remote-control status；
5. 把 connection 与 request-attestation capability 注册到 thread processor；
6. `outbound_initialized.store(true, Release)`。

这才是“该连接对普通广播可见”的最后发布点。

---

## 55. 为什么先镜像 filter，再发初始通知

初始 config warning 也是 `ServerNotification`。

定向通知虽然不需要 initialized=true，但仍会经过：

- experimental notification filter；
- exact opt-out filter。

因此必须先把 session 中的 experimental/opt-out 投影给 outbound router，初始通知才能按该连接刚声明的偏好处理。

---

## 56. 初始通知为什么使用 targeted send

若用 Broadcast，connection 仍是 `initialized=false`，router 会把它排除。

代码改用：

```text
send_server_notification_to_connections(&[connection_id], ...)
```

Targeted envelope 绕过 Broadcast 的 ready 目标筛选，但仍保留 connection-specific capability 与 opt-out filtering。

---

## 57. startup config warnings 是握手后的状态补发

App-server 启动时已经发现的配置问题，不能在客户端尚未初始化时普通广播：

- 客户端可能还没声明通知能力/偏好；
- 新连接也可能错过进程早先产生的广播。

initialize processor 保存一份 `config_warnings` snapshot，并在每个新连接成功初始化后定向发送。

这是一种 connection bootstrap snapshot，不是普通 ephemeral broadcast。

---

## 58. 为什么 thread/start 不重复同一初始 warning

thread start 可能重新检测 exec-policy warning，但会与 `initial_config_warnings` 比较。

若 warning 已在 initialize bootstrap 发送，就不再重复发送。

集成测试 `thread_start_does_not_repeat_initialize_exec_policy_warning` 固定了这条去重边界。

---

## 59. remote-control status 也是初始快照

主循环维护最新 `remote_control_status`。

连接初始化后，会收到一次定向 `remoteControl/status/changed`，即使状态没有恰好在握手时发生变化。

之后真正变化才走正常通知路径。

这让新客户端不必等待下一次 change 才知道当前状态。

---

## 60. thread capability 注册为什么在 ready 前

`connection_initialized` 把 connection 写入 `ThreadStateManager.live_connections`，携带 `request_attestation`。

在 outbound ready 前完成，意味着一旦普通业务/广播开始把该连接当作已初始化目标，thread routing 也已经知道它的连接级能力。

这是跨 owner 的 publication ordering：先让内部消费者建立一致视图，再对外开放普通流量。

---

## 61. `Release` / `Acquire` 在这里表达什么

主循环最后：

```rust
initialized.store(true, Ordering::Release)
```

outbound router 读取：

```rust
initialized.load(Ordering::Acquire)
```

直观理解：

> router 一旦通过 Acquire 看到 ready=true，就应当同时看到发布前完成的相关状态更新。

原子内存序不能替代业务顺序，但它帮助跨线程建立“先准备状态、后公布 ready”的可见性关系。

---

## 62. ready store 保证到哪一层

它保证代码按顺序先完成 capability mirror、初始 notification 入队和 thread capability 注册，再发布 ready。

它不等于：

- initialize response 已物理写出；
- 初始 notifications 已被客户端读取；
- 所有并发 producer 的消息形成绝对 UI 顺序。

特别是 notification send 只等 global queue 接受，router 与 writer 仍是并发任务。

---

## 63. response 与初始通知的队列顺序

同一 initialize 调用中：

1. response envelope 先被送入 global outgoing queue；
2. 返回主循环后，初始 notification envelopes 才依次送入；
3. 每连接 writer 也是 FIFO。

在没有其他 producer 插入的简单路径中，客户端先看到 response，再看到初始通知。

但文档不应把它夸大为跨所有并发 producer 的全局事务：这些 `send` 之间没有一个覆盖整个 bootstrap 的大锁或 write-complete barrier。

---

## 64. broadcast ready gate 的真实作用

Broadcast 路由时只选择：

```text
initialized == true
且 notification 没被 capability/opt-out filter 排除
```

因此最后 store true 表示：

> 从这一刻起，未来被 router 检查的普通广播可以把该连接选为目标。

它不是“整个 initialize 生命周期已经被客户端确认”的 ACK。

---

## 65. in-process 路径为什么不同

in-process caller 直接传 typed `ClientRequest`，没有外部 WebSocket/stdio event loop替它完成 publication。

因此它把 `Some(&outbound_initialized)` 传入 initialize processor；后者发完 response 后立即 store true，并返回 `Ok(true)`。

`handle_client_request` 看到 true 后，立即注册 thread connection capability。

---

## 66. in-process 后处理还做什么

typed processor loop 在 request 返回后：

1. snapshot opt-out；
2. snapshot experimental；
3. 镜像到 outbound projection；
4. 若发生初始化跃迁，发送 initialize config warnings。

因为 in-process 当前只有固定的单 connection ID，初始 warnings 可以走 Broadcast。

这条路径没有通用 transport 主循环里的 remote-control status bootstrap。

---

## 67. in-process 的 ready 时序不要套用 WebSocket 模型

in-process initialize processor 会在 capability projection mirror 之前先把 outbound ready 设为 true。

通用 transport 则先 mirror、初始通知、thread registration，最后 ready。

所以讨论 publication 时必须先问 transport path。两条路径共享 session commit 逻辑，但 staged publication owner 与顺序并不完全相同。

---

## 68. client name/version 怎样进入后续 request tracing

initialize request 自身还没有 session client info，所以 tracing helper直接从 initialize params 提取 name/version。

后续 request 则从 `ConnectionSessionState` 读取并记录：

```text
app_server.client_name
app_server.client_version
```

这样 initialize 前后都能给 request span 添加客户端身份，而不要求提前提交 session。

---

## 69. client name/version 怎样进入 thread

普通 request dispatch 会从 session snapshot：

```rust
let app_server_client_name = ...;
let client_version = ...;
```

`thread/start`、`thread/resume`、`thread/fork` 等流程把它们传给 thread processor，再设置到 Core thread session configuration。

后续用途包括 Hook payload 的 `client` 字段，以及少量 client/version compatibility behavior。

---

## 70. 为什么 thread 要保存“哪个客户端创建/恢复了我”

Connection 生命周期可能短于 thread：

```text
connection A initialize
→ A start thread T
→ A disconnect
→ T 继续存在或以后 resume
```

某些 thread 行为仍需要知道宿主 client identity。于是 initialize 提供 connection-local identity，thread start/resume 再把相关快照复制到更长寿的 thread state。

这是状态作用域升级，不是让 thread 永远引用已断开的 connection session。

---

## 71. MCP extensions 怎样进入新 thread

dispatch 从 session clone `ClientMcpExtensions`，传给：

- thread start；
- cold resume；
- fork；
- 相关 MCP 调用路径。

Core 的 `StartThreadOptions` 保存它，Session services 再向 MCP downstream 提供。

集成测试证明 MCP tool call 能看到：

```json
"clientCapabilities": {
  "extensions": {
    "openai/form": {},
    "io.modelcontextprotocol/ui": {...}
  }
}
```

---

## 72. capability 是 connection snapshot，不是动态 live binding

一个 thread 创建时取得当时 connection session 的 MCP extensions clone。

由于同一 connection 不能 reinitialize，connection capability 本身也不会动态变化。子 thread 还可能从 parent thread 继承 extensions。

所以不要想象每次 MCP call 都回头读取“当前 UI 连接最新 capabilities”。它们会在明确的生命周期边界被复制和继承。

---

## 73. stdio 还有一条 client-name 提示旁路

stdio reader 在正式 `forward_incoming_message` 前，会尝试从某一行解析 initialize params 并提取 `clientInfo.name`，通过 oneshot 发送一次。

这个 hint 用于 remote-control startup/persistence 相关逻辑。

但它不是 initialize commit：

- 只提取 name；
- 不写 `ConnectionSessionState`；
- 不完成 capability negotiation；
- 正式消息仍会继续进入正常 processor。

---

## 74. 为什么旁路提示不能当作认证证据

stdio helper 只要能把该行解码成 initialize request 与 `InitializeParams`，就可能先送出 name；真正 processor 后面还要检查 duplicate、header validity 并提交 session。

所以它是 startup hint，不是“连接已经可信且初始化成功”的证明。

阅读旁路解析代码时，永远要继续找权威 commit point。

---

## 75. 初始化失败分层表

| 失败点 | session 是否已提交 | 客户端结果 |
|---|---:|---|
| JSON/envelope/typed decode 失败 | 否 | 第 23 篇的 invalid/malformed 行为 |
| 已经 initialized 的快速检查 | 已有旧值 | `-32600 Already initialized` |
| `clientInfo.name` 非法 | 否 | `-32600 Invalid clientInfo.name...` |
| 并发 `OnceLock::set` 失败 | 另一个调用已提交 | `-32600 Already initialized` |
| `set_default_originator` 已设置 | 是 | 当前忽略该错误，initialize 继续成功 |
| UA suffix lock 失败 | 是 | 跳过 suffix 更新，initialize 继续 |
| outgoing response channel 失败 | 是 | helper 记 warning；函数没有回滚 session |
| response 后 transport 断开 | 是 | 客户端可能没观察到成功；connection cleanup 随后执行 |

---

## 76. 为什么 commit 后不轻易回滚

commit 后已经可能发生：

- analytics 记录；
- process originator 设置；
- residency metadata 更新；
- UA suffix 更新；
- response 入队。

回滚一个 `OnceLock` 不仅技术上没有 API，也无法可靠撤销这些跨 owner 副作用。

因此设计把所有可预见的输入验证放在 commit 前，commit 后走尽力完成 publication/发送的路线。

---

## 77. 客户端 timeout 后能否在同连接安全重试 initialize

不能简单假设安全。

客户端 timeout 只说明它没观察到 response，不说明服务器没提交。若服务器已经 set session，再发 initialize 会得到 `Already initialized`。

更稳妥的恢复模型通常是：把该连接状态视为不确定，关闭并建立新连接，再从 Uninitialized 状态重新握手。

这是根据固定的 at-most-once session commit 与无 request idempotency key 推出的客户端设计建议。

---

## 78. 多连接不会共享 initialized boolean

每个 `ConnectionState` 都创建新的 `ConnectionSessionState` 与 outbound initialized atomic。

所以：

```text
A initialize 成功
≠
B 自动 initialized
```

WebSocket 集成测试建立两个连接：A 完成 initialize 后，B 的 config/read 仍得到 `Not initialized`；B 必须独立握手。

---

## 79. initialize response 不能泄漏到其他连接

response key 使用 `ConnectionRequestId`，outgoing 形成 `ToConnection` envelope。

双 WebSocket 测试还断言：A 的 initialize response 不会出现在 B 上。

即使 A、B 使用相同 wire request ID，它们也由 connection identity 隔离。

---

## 80. request ID 重用与 duplicate initialize 是不同问题

- 相同 request ID 出现在不同连接：允许，由 connection 隔离；
- 同一连接第二次 initialize，即使换了 request ID：拒绝，因为 session 已提交；
- 同一连接重复普通 request ID：是否安全取决于更上层协议，initialize 本身不提供通用去重缓存。

不要把“ID 相同”与“状态机动作重复”混成一个概念。

---

## 81. 测试证据怎样组合

| 测试/位置 | 证明的性质 |
|---|---|
| `serialize_initialize_capabilities` | camelCase wire shape 与所有 capability 字段 |
| `deserialize_initialize_capabilities` | wire 能恢复对应 typed request |
| `initialize_uses_client_info_name_as_originator` | real client name 影响 user agent/originator |
| probe/backend 两个 initialize tests | 特殊 name 不覆盖 originator |
| `initialize_respects_originator_override_env_var` | env override 优先 |
| `initialize_rejects_invalid_client_name` | header-invalid name 在 commit 前被拒绝 |
| `initialize_opt_out_notification_methods_filters_notifications` | exact notification opt-out 生效 |
| `experimental_api.rs` | 未 opt in 的实验请求被拒绝 |
| `client_capabilities_tests.rs` | extension allowlist 与 legacy normalization |
| `attestation_generate_round_trip...` | requestAttestation 声明驱动反向请求与上游 header |
| `mcp_server_tool_call_uses_session_client_extensions` | session extensions 进入 thread/MCP |
| WebSocket per-connection handshake test | 每连接独立初始化与 response routing |
| thread-start warning test | initialize bootstrap warning 不重复 |

---

## 82. 常见误读一：initialize 只是设置 bool

错。真正的 session payload包含 identity、实验能力、通知偏好、attestation 与 MCP extensions；随后还要更新 process metadata、outbound projection 和 thread live connection registry。

bool 只是给 outbound Broadcast 使用的发布标记之一。

---

## 83. 常见误读二：服务器完整接受客户端声明的所有 extension

错。unknown namespaces 被过滤，只保留明确支持的两个 MCP extension IDs；legacy form boolean也被归一到同一 trusted map。

客户端声明是输入，不是授权服务器行为的最终真相。

---

## 84. 常见误读三：收到 initialize response 才提交 session

错。服务器先 `OnceLock::set`，再更新 metadata 并把 response 放入 outgoing queue。

这保证成功 response 对应已提交状态，但也意味着客户端没收到 response 时，服务器可能已经完成提交。

---

## 85. 常见误读四：session initialized 等于 broadcast ready

错。通用 transport 中间还有 capability projection、初始通知与 thread capability 注册。最后的 outbound atomic store 才开放 Broadcast target selection。

这是两阶段 publication：权威 session commit 在前，发送侧 ready 发布在后。

---

## 86. 常见误读五：capabilities 都是 per-connection

大部分协议信息存于 connection session，但 initialize 也修改 process-global originator、UA suffix 和 residency metadata。

必须逐字段确认 owner，不能因输入来自某连接就推断所有副作用只属于该连接。

---

## 87. 常见误读六：title 与 name 一样参与运行时身份

错。固定 initialize processor 明确保留 name/version，title 当前被解构为 `_title`。title 是协议接受的显示 metadata，但本条主流程没有把它写进 session。

---

## 88. 一张完整状态时间线

```text
Client                 Processor                 Session          Process globals       Outbound/Thread
  | initialize              |                       |                    |                    |
  |------------------------>| typed params          |                    |                    |
  |                         | duplicate check       |                    |                    |
  |                         | normalize caps        |                    |                    |
  |                         | validate name         |                    |                    |
  |                         |---------------------->| OnceLock set       |                    |
  |                         |                       | initialized        |                    |
  |                         |------------------------------------------->| originator/UA     |
  |                         | enqueue response      |                    |                    |
  |                         |-------------------------------------------------------------> router
  |                         | return to owner       |                    |                    |
  |                         | mirror exp/opt-out ------------------------------------------>|
  |                         | enqueue warnings/status ------------------------------------->|
  |                         | register attestation ---------------------------------------->|
  |                         | ready=true --------------------------------------------------->|
  |<------------------------| response/notifications eventually written                    |
```

图中“enqueue response”与“客户端收到”之间仍有完整 outgoing pipeline。

---

## 89. 贯穿案例复盘

开头的 A 声明了三条 extensions：

```text
openai/form                    → 保留
io.modelcontextprotocol/ui     → 保留
example/unknown                → 丢弃
```

其余状态：

```text
session.experimental = true
session.request_attestation = true
session.opt_out = {item/agentMessage/delta}
session.client_name = study_client
session.client_version = 1.2.0
```

后续效果：

- 实验 request 可准入；
- 实验 notification 可见；
- agent message delta 被过滤；
- 若 A 订阅 thread T，可成为 T 的 attestation responder；
- A 创建的新 thread 获得筛选后的 form/UI MCP extensions；
- tracing 和 Hook/client-specific behavior 可看到 study_client/1.2.0；
- 普通广播只在 bootstrap publication 结束后选择 A。

---

## 90. 调试 initialize 时的检查顺序

当客户端说“initialize 成功了，但后续行为不对”，按下面顺序查：

1. wire params 是否成功解码为 `InitializeParams`？
2. capabilities 是否整个省略，因而全部采用保守默认？
3. camelCase 字段名是否正确？
4. name 是否通过 HeaderValue 验证？
5. `OnceLock::set` 是否真正成功？
6. session snapshot 中各字段是什么？
7. extension 是否因不在 allowlist 被过滤？
8. outbound experimental/opt-out projection 是否已镜像？
9. thread live connection 是否登记 attestation？
10. thread 创建时是否拿到正确 client info/extensions clone？
11. response/notification 是否只是入队、尚未物理写出？
12. 问题发生在通用 transport 还是 in-process 特殊路径？

---

## 91. 局部术语表

| 代码词/短语 | 字面意思 | 本篇中的实际含义 |
|---|---|---|
| initialize | 初始化 | 每个 connection 必须完成的一次性协议握手 |
| handshake | 握手 | 双方建立后续通信前的初始请求/响应与状态提交 |
| capability | 能力 | 客户端声明自己支持或选择启用的协议行为 |
| negotiation | 协商 | 当前更接近 client declaration + server consumption，不回显 accepted list |
| client info | 客户端信息 | name、可选 title、version |
| originator | 发起方标识 | 上游 HTTP metadata 中标识主要宿主客户端的进程级值 |
| user agent | 用户代理字符串 | 组合 originator、Codex build、OS/terminal 与可选 client suffix |
| suffix | 后缀 | 追加到 User-Agent 的 `name; version` process-global metadata |
| residency requirement | 驻留要求 | 服务器 config/requirements 派生的默认 HTTP header 约束 |
| opt in | 主动加入 | 客户端明确启用实验或扩展能力 |
| opt out | 主动退出 | 客户端明确要求过滤某些 notification method |
| exact match | 精确匹配 | method string 必须完全相等，不支持通配符 |
| extension namespace | 扩展命名空间 | MCP extension ID，例如 `openai/form` |
| allowlist | 允许列表 | 服务器只保留明确认识的 extension IDs |
| normalization | 归一化 | 把 Option、legacy boolean 和 extension map 转成统一运行时表示 |
| legacy capability | 旧能力字段 | 兼容旧客户端的 `mcpServerOpenaiFormElicitation` |
| attestation | 证明/认证材料 | 客户端按服务器反向请求生成、用于上游 header 的 token |
| connection-local | 连接局部 | 每个 connection 独立保存的 session/capability 状态 |
| process-global | 进程全局 | 同一个 app-server 进程内多个连接共享的 identity/HTTP metadata |
| commit point | 提交点 | `OnceLock::set` 成功、session 从此不可重新初始化的时刻 |
| first writer wins | 首个写入者胜出 | 并发 initialize 中只有一个 `OnceLock::set` 成功 |
| projection | 投影 | 从权威 session 复制 outbound router 所需的最小字段 |
| bootstrap | 启动补齐 | 新连接初始化后接收当前 config warnings/status 快照 |
| outbound-ready | 出站就绪 | connection 可被普通 Broadcast 选择为目标 |
| targeted notification | 定向通知 | 发给明确 connection，不依赖 Broadcast ready gate |
| publication | 状态发布 | 先准备跨 owner 状态，最后以 ready atomic 对 router 可见 |
| Release/Acquire | 发布/获取内存序 | 跨线程建立先准备状态、后观察 ready 的可见性关系 |
| snapshot | 快照 | 在生命周期边界复制当时的 capability/client state |
| trust boundary | 信任边界 | 客户端声明必须经过验证/筛选，才能成为服务器信任的运行时状态 |
| startup hint | 启动提示 | stdio 提前提取 client name 的旁路信息，不是正式 initialize commit |

---

## 92. 理解检查

### 练习一

客户端完全省略 capabilities。后续实验请求和实验通知会怎样？

<details>
<summary>参考答案</summary>

`unwrap_or_default` 使 experimental=false；实验 request 被 capability gate 拒绝，实验 notification 在 outbound router 被过滤。

</details>

### 练习二

客户端同时用 extensions 提供带配置的 `openai/form`，又把 legacy form boolean 设为 true。最终 value 是什么？

<details>
<summary>参考答案</summary>

保留 extensions 中的显式 value。legacy true 只在 key 不存在时用空 object 补齐，`or_insert_with` 不覆盖已有值。

</details>

### 练习三

为什么 session commit 后不能马上开放 Broadcast？

<details>
<summary>参考答案</summary>

通用 transport 还要把 experimental/opt-out 镜像给 outbound router、入队初始 config warnings 和 remote status、向 thread state 注册 attestation capability，最后才以 ready atomic 发布完整视图。

</details>

### 练习四

客户端 initialize timeout 后，在同一连接重发 initialize 是否一定安全？

<details>
<summary>参考答案</summary>

不一定。服务器可能已在 response 丢失前完成 `OnceLock::set`；重发会得到 Already initialized。更稳妥的恢复是重建连接后重新握手。

</details>

### 练习五

`requestAttestation=true` 是否意味着这个客户端会回答所有 thread 的 attestation？

<details>
<summary>参考答案</summary>

不是。选择还要求连接 live、订阅目标 thread，并在候选中按 connection ID 选择一个 responder。

</details>

### 练习六

为什么 initialize response 可以在 outbound initialized=false 时发送？

<details>
<summary>参考答案</summary>

它是 `ToConnection` response；initialized gate 只筛选 Broadcast 目标。否则客户端将无法收到使它知道握手成功的 response。

</details>

---

## 93. 源码导航

| 阅读目标 | 固定提交路径 | 重点符号 |
|---|---|---|
| Initialize wire params | `codex-rs/app-server-protocol/src/protocol/v1.rs` | `InitializeParams` |
| Client identity fields | 同上 | `ClientInfo` |
| Capability fields/defaults | 同上 | `InitializeCapabilities` |
| Initialize response | 同上 | `InitializeResponse` |
| Initialize method DSL | `codex-rs/app-server-protocol/src/protocol/common.rs` | `Initialize => "initialize"` |
| Wire round-trip tests | 同上 | serialize/deserialize initialize capabilities |
| Connection session owner | `codex-rs/app-server/src/message_processor.rs` | `ConnectionSessionState` |
| Committed session payload | 同上 | `InitializedConnectionSessionState` |
| OnceLock commit | 同上 | `ConnectionSessionState::initialize` |
| Initialize special dispatch | 同上 | `handle_client_request` |
| Session consumers | 同上 | initialized/experimental/client info/MCP getters |
| Initialize processor | `codex-rs/app-server/src/request_processors/initialize_processor.rs` | `InitializeRequestProcessor` |
| Duplicate checks | 同上 | pre-check、session.initialize error |
| Capability normalization | 同上 | defaults、HashSet、MCP helper |
| Name validation | 同上 | `HeaderValue::from_str` |
| Process identity updates | 同上 | originator、UA suffix、residency |
| Response construction | 同上 | `InitializeResponse` 与 `send_response` |
| Bootstrap warnings | 同上 | `send_initialize_notifications*` |
| MCP extension allowlist | `codex-rs/codex-mcp/src/client_capabilities.rs` | `client_mcp_extensions` |
| Extension unit tests | `codex-rs/codex-mcp/src/client_capabilities_tests.rs` | supported-only、legacy normalization |
| Originator owner | `codex-rs/login/src/auth/default_client.rs` | `ORIGINATOR`、`set_default_originator` |
| Originator precedence | 同上 | env override、first set wins |
| User-Agent construction | 同上 | `USER_AGENT_SUFFIX`、`get_codex_user_agent` |
| Residency metadata | 同上 | `set_default_client_residency_requirement` |
| Main connection state | `codex-rs/app-server/src/transport.rs` | `ConnectionState`、`OutboundConnectionState` |
| Notification filters | 同上 | `should_skip_notification_for_connection` |
| Broadcast ready gate | 同上 | `route_outgoing_envelope` |
| Common transport publication | `codex-rs/app-server/src/lib.rs` | was/is initialized transition |
| Projection mirror | 同上 | opt-out RwLock、experimental atomic |
| Initial notifications/status | 同上 | targeted send sequence |
| Final ready publication | 同上 | `outbound_initialized.store(Release)` |
| In-process publication | `codex-rs/app-server/src/in_process.rs` | `process_client_request` loop |
| Outgoing response routing | `codex-rs/app-server/src/outgoing_message.rs` | `send_response`、`ToConnection` |
| Thread connection registry | `codex-rs/app-server/src/thread_state.rs` | `ConnectionCapabilities`、`connection_initialized` |
| Attestation selection | 同上 | `first_attestation_capable_connection_for_thread` |
| Attestation reverse request | `codex-rs/app-server/src/attestation.rs` | request + 100ms timeout |
| Thread client info propagation | `codex-rs/app-server/src/request_processors/thread_processor.rs` | `set_app_server_client_info` |
| Thread MCP propagation | 同上 | start/resume/fork params 与 `StartThreadOptions` |
| Core thread MCP state | `codex-rs/core/src/thread_manager.rs` | `client_mcp_extensions` options/inheritance |
| Request tracing identity | `codex-rs/app-server/src/app_server_tracing.rs` | initialize params/session fallback |
| stdio name hint | `codex-rs/app-server-transport/src/transport/stdio.rs` | `stdio_initialize_client_name` |
| hint consumer | `codex-rs/app-server/src/lib.rs`、remote-control transport | name oneshot |
| Initialize integration tests | `codex-rs/app-server/tests/suite/v2/initialize.rs` | originator、invalid name、opt-out |
| Per-connection handshake | `codex-rs/app-server/tests/suite/v2/connection_handling_websocket.rs` | two connections |
| Experimental tests | `codex-rs/app-server/tests/suite/v2/experimental_api.rs` | opt-in gate |
| Attestation integration | `codex-rs/app-server/tests/suite/v2/attestation.rs` | reverse request/header |
| MCP extension integration | `codex-rs/app-server/tests/suite/v2/mcp_tool.rs` | session capabilities downstream |
| Bootstrap warning dedupe | `codex-rs/app-server/tests/suite/v2/thread_start.rs` | initialize warning not repeated |

---

## 94. 本篇收束

一次 initialize 建立的不是一个孤零零的 true，而是一组有顺序、有 owner、有作用域的合同：

- wire params 提供 client info 和可选 capabilities；
- 缺省能力采取保守 false/empty；
- name 在进入 HTTP metadata 前按 header value 验证；
- legacy form 与 extensions 被归一化，未知 namespace 被 allowlist 丢弃；
- 完整 connection session 通过 `OnceLock` 一次性提交；
- experimental、opt-out、attestation、client identity 和 MCP extensions 分别流向不同消费者；
- originator、UA suffix 与 residency 暴露出 connection-local 输入影响 process-global metadata 的边界；
- response 在 commit 后定向入队，不依赖 Broadcast ready gate；
- 通用 transport 再完成 capability projection、初始 warning/status、thread registration；
- 最后的 Release store 才让普通 Broadcast 把连接视为 outbound-ready；
- in-process 复用同一 session commit，但 publication owner 与顺序略有不同；
- client timeout 不证明服务器未提交，连接重建比盲目重试 initialize 更稳妥。

下一篇适合继续精读 App-server 的 request tracing 与 request context：一条 request 的 W3C parent、connection/client metadata、延迟 response span 和 cleanup 怎样从入口一直保持到最终回包。

返回[源码精读目录](README.md)，或查看[课程术语总表](../glossary.md)。
