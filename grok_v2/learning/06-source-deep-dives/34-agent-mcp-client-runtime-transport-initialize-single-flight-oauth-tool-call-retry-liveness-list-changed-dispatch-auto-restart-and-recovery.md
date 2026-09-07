# 源码精读 34：MCP Client Runtime——Transport、Initialize Single-flight、OAuth、Tool Call、Liveness、ListChanged 与自动恢复

> 源码基线：`ed6d543`
>
> 上一篇从握手结果向上追踪 Tool Index、SearchTool 与 UseTool。本篇转向连接以下：`McpClient` 怎样建立 transport、保证只进行一次并发握手、执行工具、处理认证和断线，并把变化送回 Session 控制面。

---

## 1. 本篇解决什么问题

读完后应能回答：

- stdio、HTTP/SSE、HTTP OAuth 和 ACP reverse transport 怎样统一成 `PendingTransport`？
- 为什么 stdio handshake 失败进入 `Empty`，HTTP/ACP 却能回到 `Pending`？
- `ensure_initialized()` 怎样避免两个并发调用发起两次 handshake？
- 为什么 `Notify` future 必须在读取 state 前创建？
- `InitGuard` 怎样让被取消的握手任务不永久卡住 Client？
- handshake timeout、in-flight wait timeout 和 tool timeout 有何区别？
- OAuth discovery 为什么在 interactive 与 non-interactive 模式下采用不同 fail policy？
- 已有 Authorization header 为什么跳过 OAuth discovery？
- 一次 MCP Tool Call 在哪些错误下会重连、重试或重新认证？
- 为什么 Tool timeout 后重置 transport，却不自动重放调用？
- MCP `isError=true` 与 Rust Runtime error 有什么不同？
- Text、Image、Resource 结果怎样进入 Agent ToolOutput？
- liveness watcher 为什么轮询 state，而不是调用 `ensure_initialized()`？
- 为什么 watcher 遇到 `Pending/Initializing/Empty` 必须静默退出？
- stale `TransportClosed` 怎样避免误删同名新 Client？
- stdio restart 与 HTTP recovery 为什么采用不同策略？
- `tools/list_changed` 当前打通了什么，又没有打通什么？

---

## 2. 总体运行图

```text
ACP MCP config
      │
      ▼
start_mcp_server
  ├─ Stdio: spawn child + resilient JSON-RPC transport
  ├─ Http/Sse: URL + headers + OAuth discovery
  └─ ACP SDK: server_id + reverse invoker
      │
      ▼
McpClient { state = Pending(transport) }
      │
      ▼
ensure_initialized()
  Pending ── take ownership ──► Initializing ── try_handshake ──► Ready(service)
      ▲                              │                                 │
      │                              └─ failure ─► Pending / Empty     │
      │                                                                │
      └──────── concurrent callers wait on init_done Notify ───────────┘
                                                                       │
                      ┌────────────────────────────────────────────────┤
                      │                                                │
                      ▼                                                ▼
                tools/list                                      tools/call
                      │                                                │
             dynamic registrations                      timeout / reconnect / auth
                                                                       │
                                                                       ▼
                                                            MCP ToolOutput

Ready service
  ├─ GrokClientHandler receives list_changed → McpClientEvent
  └─ liveness watcher polls closed state → TransportClosed event
                                              │
                                              ▼
                                    50ms StatusDispatcher window
                                      ├─ status push
                                      ├─ stale-client defense
                                      ├─ stdio respawn
                                      └─ HTTP in-place recovery
```

---

## 3. 核心源码地图

| 领域 | 主要源码与符号 |
| --- | --- |
| Transport 与 Client 状态机 | `xai-grok-mcp/src/servers.rs`：`PendingTransport`、`ClientState`、`McpClient` |
| Client 创建 | `servers.rs`：`start_mcp_server`、`new_with_transport` |
| 并发握手 | `servers.rs`：`ensure_initialized`、`InitGuard`、`try_handshake` |
| OAuth 预探测与重认证 | `servers.rs`：`discover_and_prepare_auth`、`try_reauth_from_disk`、`force_reauth` |
| 模型侧 Tool 执行 | `servers.rs`：`McpErasedTool::run`、`try_call_tool`、`recover_and_retry` |
| Server notification handler | `servers.rs`：`GrokClientHandler` |
| Transport liveness | `xai-grok-mcp/src/liveness.rs` |
| 状态事件合并 | `xai-grok-shell/src/session/mcp_dispatcher.rs` |
| stdio/HTTP 恢复 | `xai-grok-shell/src/session/mcp_restart.rs` |
| Session 注册与快照 | `xai-grok-shell/src/session/acp_session_impl/mcp.rs`、`mcp_snapshot.rs` |

---

# 第一部分：Transport 先被表示为“可用于下一次握手的资源”

## 4. `PendingTransport` 的四个变体

```text
Stdio(SafeTokioChildProcess)
Http(HttpConfig)
HttpAuth { config, auth_manager }
Acp { server_id, invoker }
```

它们都能交给 `try_handshake()`，但重建能力不同。

## 5. Pending 不是“连接失败”

`Pending` 表示 transport 已配置、尚未被某次 handshake 消耗。

新建 Client 的正常初始状态就是 Pending。

## 6. Stdio transport 拥有子进程

Stdio variant 保存 `SafeTokioChildProcess`，内部绑定 child stdin/stdout，并控制进程组回收。

它不是一份可复制配置，而是唯一的 live process handle。

## 7. HTTP transport 保存可重建配置

`HttpConfig` 只有 URL 与 header 列表。

每次 handshake 都可据此重新构造 reqwest client 和 Streamable HTTP transport。

## 8. HttpAuth 额外共享 AuthorizationManager

Transport 内的 AuthClient 与 Client 的 re-auth 路径持有同一个 `Arc<Mutex<AuthorizationManager>>`。

Token 更新因此可被后续 transport rebuild 看到。

## 9. ACP transport 是反向通道

SDK server 不由 Agent 直接拨号；它保存 server ID 和 `AcpReverseInvoker`，通过 `x.ai/mcp/sdk_call` 反向请求宿主。

从 `McpClient` 往上看，它仍遵守同一 MCP service 接口。

## 10. `restorable_transport()` 定义恢复能力

| Transport | 可恢复？ | 原因 |
| --- | --- | --- |
| Stdio | 否 | child 已被 `serve` 消耗，不能 clone |
| HTTP | 是 | config 可 clone |
| HTTP Auth | 是 | config 与 manager Arc 可 clone |
| ACP | 是 | server ID 与 invoker Arc 可 clone |

## 11. `PendingTransport` 故意不实现 Clone

若整个 enum 自动 Clone，Stdio 很容易被误认为可以安全重试。

显式 helper 让“哪些 transport 可以恢复”成为代码审查可见的策略。

## 12. Client 构造都汇入一个函数

`new_stdio`、`new_http`、`new_http_auth`、`new_acp` 最终调用 `new_with_transport()`。

所有字段和默认状态只在一个 struct literal 中初始化。

## 13. `reconnect` 是创建时快照

Client 在把 transport 移入 `ClientState::Pending` 前，调用 `restorable_transport()` 保存重连材料。

Stdio 的 `reconnect=None`；其他可恢复类型保留副本。

## 14. 每个 Client 有进程级唯一 ID

`NEXT_CLIENT_ID: AtomicU64` 为实例分配单调 ID。

同一个 server name 重建出的两个 Client 也有不同 ID。

## 15. 为什么 name 不足以识别断线对象

旧 Client 的 liveness event 可能在 50ms coalesce window 中排队；这期间新 Client 已注册到同名 key。

若只按 server name 删除，会误杀健康 replacement。

## 16. Timeout 配置在构造时收敛

Client 保存：

- `startup_timeout_sec`；
- server-level `tool_timeout_sec`；
- per-tool `tool_timeouts`。

## 17. Timeout 优先级

Per-tool：

```text
_meta toolTimeoutsMs
> 外部 config per-tool seconds
> _meta toolTimeoutMs
> 外部 config server-level seconds
> default
```

## 18. 毫秒转秒使用向上取整

`div_ceil(1000)` 避免 1～999ms 变成 0 秒。

代价是 sub-second 精度被有意丢弃。

## 19. 当前源码常量

```text
startup default = 30 seconds
tool-call default = 6000 seconds
OAuth discovery = 5 seconds
stdio graceful shutdown = 3 seconds
```

注意：`tool_timeout_for()` 的 doc-comment 仍写“Default (60s)”，但实现常量是 6000。阅读和运行行为应以常量为准，这是当前基线中的注释漂移。

## 20. 未匹配的 per-tool timeout 会被提示

完成 `tools/list` 后，代码把 timeout keys 与发现到的 bare names 比较。

拼错的 key 只记录 info，不会让 server 初始化失败。

---

# 第二部分：`start_mcp_server()` 选择并准备 Transport

## 21. Stdio 分支先规划 executable

代码考虑 PATH override、平台差异与 command args，得到真正 program 与 spawn args。

随后构造 Tokio Command。

## 22. `kill_on_drop(true)` 是生命周期底线

Client 被配置移除或 Session teardown 时，child 不应继续成为孤儿进程。

Safe wrapper 还负责 process group 级回收。

## 23. 子进程 stderr 被单独排空

stderr 写入 `~/.grok/logs/mcp/<sanitized-server>.stderr.log`，每次 spawn 截断。

不持续排空 stderr 可能让 child 因 pipe buffer 满而阻塞。

## 24. Server name 会被净化成日志文件名

只保留字母数字、点、下划线、短横线，并限制长度。

它防止 server name 变成任意路径。

## 25. Stdio stdout 不是普通日志

stdout 承载 newline-delimited JSON-RPC。

Server 若把调试文本写到 stdout，会污染协议，因此 stderr 才是日志出口。

## 26. `ResilientRwTransport` 容忍单行坏数据

它自行逐行读取：无法解码的行会记录事件并跳过；只有真正 EOF 才返回 `None`。

## 27. 为什么坏行不能等同 EOF

若一次非 JSON 输出被解释为 transport closed，所有 in-flight request 都会失败，连接也会被错误重启。

容忍单行污染使 off-spec server 不至于立即击穿整个会话。

## 28. 坏行不会收到 JSON-RPC error reply

代码选择 wire silence，避免某些错误 server 把 error reply 再 echo 回来形成协议噪声循环。

## 29. HTTP 与 SSE 进入同一分支

ACP config 中的 Http/Sse 最终都构造 `HttpConfig`，使用 Streamable HTTP client transport。

产品配置名保留差异，Client Runtime 复用同一实现。

## 30. Session placeholder 在 header 中展开

HTTP header 可包含 session ID placeholder；spawn 时根据当前 Session 展开。

因此 config 是模板，HttpConfig 是本 Session 的实际值。

## 31. 显式 Authorization header 优先

若 config 已带 Authorization，代码跳过 OAuth metadata discovery，直接创建普通 HTTP Client。

这支持用户自带 bearer/API credentials。

## 32. 无显式 Authorization 才预探测 OAuth

`discover_and_prepare_auth()` 创建 manager、连接 credential store，并尝试加载凭证或发现 metadata。

整个探测有 5 秒上限。

## 33. 多 server 启动受并发上限控制

`start_mcp_servers()` 使用 `buffer_unordered(8)`。

启动顺序不决定完成顺序；最多并行准备八个 server。

## 34. 单 server 失败不会取消其他 server

返回类型是 `Vec<Result<McpClient, McpError>>`。

上层分别处理成功、spawn failure、auth required 等结果。

---

# 第三部分：OAuth 预探测与交互策略

## 35. `OauthInteractivity` 是能力边界

```text
Interactive
NonInteractive
```

它不表示 server 是否支持 OAuth，而表示当前 Session 能否完成浏览器授权。

## 36. OAuth 预探测的三种结果

```text
NoOauthSupport
ManagerReady(auth_manager)
NeedsInteractiveLogin
```

上层据此构造 Http、HttpAuth 或返回 AuthRequired。

## 37. Credential store 以 server 与 URL 绑定

Adapter 用 server name 和 parsed URL 创建。

避免不同 connector 或 endpoint 的 token 混用。

## 38. 已存凭证路径

若 `initialize_from_store()` 成功：

- Interactive 直接 ManagerReady；
- NonInteractive 还调用 `get_access_token()` 验证其可用性。

## 39. NonInteractive 为什么更严格

它不能弹浏览器修复。

带着无效 token 启动 worker 只会在后台产生 AuthorizationRequired，同时让上层误以为连接正在工作。

## 40. 无存储 token 但 server 支持 OAuth

- Interactive：保留 manager，允许后续 auth flow；
- NonInteractive：`NeedsInteractiveLogin`，拒绝无认证启动。

## 41. Server 明确不支持 OAuth

`NoAuthorizationSupport` 映射为 `NoOauthSupport`，正常走 plain HTTP。

## 42. 探测失败采用不对称策略

- Interactive fail-open 到 plain HTTP；
- NonInteractive fail-closed 到 NeedsInteractiveLogin。

原因是 Interactive 尚可由用户后续修复，而 headless 场景不能。

## 43. 这是可用性与噪声的权衡

探测服务临时故障时，Interactive plain HTTP 可能其实仍可工作；完全拒绝会损失可用性。

NonInteractive 若盲启则更可能制造不可操作的失败状态。

## 44. 已撤销但无 expiry 的 token 是已知缺口

rmcp 可能把缺少 expiry metadata 的 stored token 原样返回。

因此它能通过 ManagerReady，直到实际 handshake/tool call 才暴露 401。

---

# 第四部分：ClientState 是握手所有权状态机

## 45. 四个状态

```text
Empty
Pending(PendingTransport)
Initializing
Ready(Arc<RunningService>)
```

## 46. `Empty`

表示没有可用于下一次 handshake 的 transport。

测试 stub 和失败后无法恢复的 Stdio Client 会进入该状态。

## 47. `Pending`

表示某个调用者可以取得 transport，成为 handshake holder。

此时尚不存在可调用的 MCP service。

## 48. `Initializing`

表示 transport 已被唯一 holder 拿走，握手正在锁外进行。

其他调用者不能并发启动第二次 handshake。

## 49. `Ready`

保存 `Arc<RunningService<RoleClient, GrokClientHandler>>`。

多个 tools/list、tools/call 和状态消费者可以 clone service 引用。

## 50. `ClientStateKind` 是无 payload 投影

诊断、恢复 gate 和 watcher 只需知道 variant，不应借出内部 transport/service。

因此源码提供 Copy enum 快照。

## 51. 状态转换总览

```text
Pending ── owner takes transport ──► Initializing
Initializing ── success ───────────► Ready
Initializing ── HTTP/ACP failure ──► Pending(restored)
Initializing ── Stdio failure ─────► Empty
Ready ── reset/re-auth ────────────► Pending
Pending/Ready ── external replace ─► another explicit state
```

## 52. 网络 I/O 不在 state lock 内执行

holder 先把 state 改为 Initializing、释放 Mutex，再调用 `try_handshake()`。

否则其他调用者无法观察 Initializing 并进入可控等待，只会堵在 Mutex 上。

---

# 第五部分：`ensure_initialized()` 的 Single-flight 算法

## 53. 方法首先计算等待上限

```text
inflight_wait = startup_timeout_sec + 1 second
```

`try_handshake` 自己已有 startup timeout；多出的 1 秒用于发布结果和调度余量。

## 54. 先创建 notified future，再读 state

顺序是：

```text
let notified = init_done.notified()
lock state
inspect state
```

## 55. 为什么顺序不能反

如果先看到 Initializing、释放锁、再订阅 Notify，holder 可能恰好在两步之间 `notify_waiters()`。

通知不会为未来 waiter 保留，从而造成 lost wakeup。

## 56. State 用 `mem::replace` 取得所有权

代码先把当前 state 替换成 Initializing，再匹配被移出的 owned value。

这样 Pending arm 可以直接拿走不可 Clone 的 transport。

## 57. Ready 分支

恢复 `Ready(service.clone())` 并立即返回 cloned Arc。

已初始化 Client 的 fast path 不做网络请求。

## 58. Empty 分支

恢复 Empty，返回“no transport configured”。

它不会凭空重启 stdio process。

## 59. Initializing 分支

恢复 unit variant、释放锁，然后带 timeout 等待 Notify。

收到通知后 `continue`，重新检查最新 state。

## 60. 为什么 waiter 必须重查而非直接接收结果

Notify 只表达“状态发生了完成性变化”，不携带 service/error payload。

真实结果存储在 state 中，是唯一事实源。

## 61. Pending 分支选出 holder

只有这一分支保留刚写入的 Initializing，并 break 出 owned transport。

因此同一时刻最多一个 task 进入 `try_handshake()`。

## 62. Holder 失败后 waiter 可能成为下一任 holder

若 HTTP/ACP handshake 失败并恢复为 Pending，所有 waiter 醒来竞争锁；其中一个取得 transport 继续尝试。

这可能产生顺序重试，但不会产生并发握手。

## 63. In-flight wait timeout 是卡死保险

若 holder 被取消且 guard 恢复也因罕见锁竞争失败，waiter 最终得到清晰 error，而不是永久挂起。

## 64. Single-flight 解决的真实竞态

Background `get_tool_registrations()` 与模型第一下 Tool Call 可能同时调用 `ensure_initialized()`。

旧行为返回“already initializing”；新行为让模型调用等待并复用同一握手结果。

---

# 第六部分：`InitGuard` 提供取消安全

## 65. 为什么普通 finally 不够

Rust async task 可在任意 `.await` 被 abort；若 holder 消失前没写回 state，Client 会永久停在 Initializing。

RAII Drop 必须承担兜底恢复。

## 66. Guard 保存什么

```text
&state Mutex
&init_done Notify
Option<restorable PendingTransport>
```

Stdio 的 restore 为 None，因此 guard 对它不能复活已消耗 child。

## 67. 正常路径先 `disarm()`

握手得到结果后、正式写入 Ready/Pending/Empty 前，holder 清空 guard.restore。

随后 Drop 成为 no-op，避免和正式发布竞争。

## 68. 异常 Drop 使用 `try_lock`

Drop 是同步函数，不能 `.await` Tokio Mutex。

所以它 best-effort `try_lock()`，只在当前仍是 Initializing 时恢复 Pending。

## 69. Lock 竞争时为什么仍 Notify

无论 restore 是否成功，都唤醒 waiters。

若状态仍卡在 Initializing，waiter 会再次等待并最终触发 bounded timeout。

## 70. Guard 保证是 best-effort，不是绝对事务

同步 Drop 与 async Mutex 的限制意味着恢复可能跳过。

系统用 Notify + timeout 组合保证“优先恢复，否则明确失败”，而不是假装绝不出错。

---

# 第七部分：`try_handshake()` 怎样适配各 Transport

## 71. 所有分支都有 startup timeout

`handler.serve(transport)` 被 `tokio::time::timeout(startup_timeout)` 包裹。

超时映射成带 server name 与 duration 的 `McpError::Timeout`。

## 72. Stdio 分支

直接把唯一的 `SafeTokioChildProcess` 交给 `handler.serve()`。

调用后无可重复使用的 process handle。

## 73. Plain HTTP 分支

根据 HttpConfig 每次重建 reqwest client、headers、证书配置和 Streamable HTTP transport。

然后交给相同 handler。

## 74. OAuth HTTP 分支

构造非 Authorization headers，创建 AuthClient，并把 placeholder manager 替换为共享 manager Arc。

Authorization 由 AuthClient 动态产生，不复制 config 中的旧 bearer。

## 75. ACP 分支

建立 reverse bridge transport。

单次 reverse invocation timeout 取 startup/tool 默认值中的较大者，外层 handshake/tool timeout 仍是最终边界。

## 76. Handler 统一 ClientInfo

它声明实现名称、版本、扩展能力，并显式固定 MCP protocol version `2025-06-18`。

升级 rmcp crate 不会悄悄改变线上声明版本。

## 77. MCP App UI capability 也在 ClientInfo 中声明

extension `io.modelcontextprotocol/ui` 宣告支持对应 HTML profile。

这与 Tool `_meta.ui` 的消费路径相配合。

## 78. Handshake error 被包装

`handler.serve()` 的错误转为带 server 和 source 的 `HandshakeFailed`。

上层状态事件可保留完整 reason 供排障。

---

# 第八部分：握手失败后的 Token Refresh 与结果发布

## 79. Auth Client 首次握手失败会尝试一次 refresh

只要存在 auth manager + HttpConfig，代码对任意 handshake failure 都尝试 refresh token。

这是因为不同 server 的错误字符串并不可靠。

## 80. Refresh 成功后重建 HttpAuth transport

同一 manager 中的新 token 会被新 AuthClient 使用，再执行一次 `try_handshake()`。

## 81. Refresh 失败则保留原 handshake error

没有浏览器流程发生在这个内部 retry 中。

Interactive 完整认证由显式 auth/re-auth 路径处理。

## 82. 发布顺序

```text
disarm InitGuard
snapshot event sender
lock state
store Ready / Pending / Empty
unlock
notify waiters
emit Ready / HandshakeFailed event
```

## 83. 为什么先解锁再 Notify

被唤醒 caller 应立即读到最终 state，而不是醒来后又等待 holder 释放锁。

## 84. 为什么事件也在锁外发送

Dispatcher 当前不反查该锁，但锁外发送减少未来改动造成 lock inversion/deadlock 的风险。

## 85. 成功发布 Ready event

StatusDispatcher 映射为：

```text
status = ready
reason = initialized
```

## 86. 失败发布完整 reason

HandshakeFailed 的 error string 不截断进入 detail，便于 UI/incident debugging。

## 87. Event sender 是共享 slot 而非握手时快照

`notify_tx` 是 `Arc<parking_lot::Mutex<Option<Sender>>>`，Client 与 live GrokClientHandler 共享。

握手后再调用 `set_event_tx()`，handler 下一次 notification 仍能看到。

---

# 第九部分：重新认证不是简单“再开浏览器”

## 88. `try_reauth_from_disk()` 不做浏览器流程

它用于 overlay refresh 等后台恢复：

1. 重新从 credential store 加载；
2. 尝试 refresh token；
3. 都失败则 false。

## 89. 为什么比较 token before/after

`initialize_from_store()` 可能对同一份过期 disk credential 返回成功。

只有 access token 实际变化，才能声称另一个 Session/进程写入了 fresh token。

## 90. 比较期间持有同一 manager lock

before → reload → after 作为一个原子观察区间。

否则并发 token mutation 会让“是否来自 disk reload”判断失真。

## 91. `force_reauth()` 的三层阶梯

```text
fresh token from disk
→ refresh_token grant
→ browser OAuth flow
```

任一成功都会把 state 替换为 Pending(HttpAuth)。

## 92. 网络型 refresh failure 默认不升级浏览器

当 `force=false` 且失败被分类为 transient network error，方法直接返回 false。

Wi-Fi 短暂中断时打开浏览器既无帮助，也可能破坏仍有效的 credential。

## 93. Terminal refresh failure 才进入 browser fallback

例如 refresh token 被拒绝时，重新授权可能真正修复问题。

显式用户触发 `force=true` 也允许越过 transient gate。

## 94. Re-auth 成功不等于已 Ready

它只更新 credential，并把 Client 重置为 Pending transport。

下一个 `ensure_initialized()` 才完成新握手。

## 95. `replace_state()` 同时唤醒 waiter

外部 re-auth/reset 可能发生在其他调用者等待 Initializing 时。

写入新 state 后通知，避免 waiter 对旧世界等待到 timeout。

---

# 第十部分：模型侧 MCP Tool Call 主路径

## 96. `McpErasedTool` 是 JSON→JSON runtime wrapper

MCP Tool 的参数不是编译期 Rust Args struct，因此直接使用 `serde_json::Value`。

Tool kind 为 Other、namespace 为 MCP。

## 97. Runtime ID 使用 qualified name

`server__tool` 防止两个 server 的同名 bare Tool 在 LocalRegistry 冲突。

发给 MCP server 的 `CallToolRequestParams.name` 仍是 bare name。

## 98. `run()` 先从 McpState 找 live Client

它在 state lock 内 clone Client Arc 和 EventWriter，然后立即释放。

网络调用不会跨持有全局 McpState lock。

## 99. Tool timeout 按 bare name 解析

Per-tool config keys 对应 server 原始 Tool name，不是 qualified registry name。

## 100. 调用开始事件

`McpToolCallStarted` 包含 server、bare tool、qualified call ID 和 timeout。

这让 trace 能把模型 identity 与协议 identity 联系起来。

## 101. 第一层 `try_call_tool()`

它先 `ensure_initialized()`，构造 params，再为 `mcp_service.call_tool()` 加 per-tool timeout。

## 102. Arguments 只接受 object

`params.arguments = raw.as_object().cloned()`。

非 object Value 会变成无 arguments，而不是作为 arbitrary JSON 发送。

## 103. 正常协议结果

`Ok(Ok(CallToolResult))` 原样返回，由上层解释 `is_error` 与 content。

## 104. Transport error 可触发恢复

明确可恢复：

- `TransportClosed`；
- `TransportSend`。

## 105. HTTP 某些 MCP error 也可恢复

首次调用时，除 deterministic client errors 和 auth errors 外，HTTP MCP error 可尝试 rebuild/retry。

## 106. Deterministic client error 集合

```text
-32700 parse error
-32600 invalid request
-32601 method not found
-32602 invalid params
```

这些说明请求本身有问题，重连不会改变结果。

## 107. Auth error 不走普通 reconnect

重建 transport 仍会使用旧 credential，因此没有意义。

它交给外层 re-auth ladder。

## 108. Reconnect 最多一次

`reconnect_attempted` 阻止同一次 dispatch 无限 rebuild。

恢复成功后，原 params 只重试一遍。

## 109. Recovery 路径

```text
client.recover()
  → Ready 时 reset_transport()
  → Pending
  → ensure_initialized()
  → re-arm liveness watcher
```

## 110. 并发 Recovery 也被 single-flight 合并

只有观察到 Ready 的 caller 执行 reset；看到 non-Ready 的 caller 直接 join `ensure_initialized()`。

## 111. Recovery 失败返回原错误

这保留最初 transport/auth 信号，避免 secondary handshake error 掩盖真正触发原因。

## 112. Recovery 成功但 retry 失败返回 retry error

这时新 transport 已建立，第二个错误才是当前最相关状态。

---

# 第十一部分：为什么 Timeout 绝不自动重放

## 113. Tool timeout 设置 `is_timeout=true`

错误文本明确指出 Tool 与秒数，并进入 completed event。

## 114. HTTP timeout 后会为下一次调用 reset

若尚未 reconnect，Client 转回 Pending，避免继续复用可能卡住的 service。

## 115. 但当前调用不会 retry

Server 可能已经完成副作用，只是响应迟到。

自动重放 `create_issue`、`send_message`、`charge_card` 可能造成重复操作。

## 116. Transport closed 与 timeout 的语义不同

明确 send/connection failure 更有理由认为调用未可靠完成；timeout 只说明客户端没及时看到结果。

代码因此采用不同重试策略。

## 117. 这是 at-most-once 倾向，不是严格保证

Transport error 后的一次 retry 仍可能在极端网络场景重复副作用。

真正 exactly-once 需要 server 提供 idempotency key，Client Runtime 无法单方面保证。

---

# 第十二部分：失败后还可能进行一次 Auth Retry

## 118. 外层只对有 auth manager 的 Client 启用

第一次 dispatch error 后调用 `force_reauth(false)`。

这发生在普通 reconnect logic 外层。

## 119. Re-auth 成功后再次调用 `try_call_tool()`

新 Pending HttpAuth 在 `ensure_initialized()` 中重新握手，再执行 Tool。

## 120. Auth retry 也有次数边界

`auth_retry_attempted` 是一次布尔标记，没有循环。

第二次错误直接返回。

## 121. 为什么对 first error 较宽松

不同 OAuth MCP server 对失效凭证的错误形态不一致。

有 auth manager 本身是低成本 gate，`force_reauth` 内部再按 disk/refresh/browser 策略判断。

## 122. 两类恢复标记进入输出和事件

最终 MCPOutput 保存：

- `auth_retry_attempted`；
- `reconnect_attempted`；
- `is_timeout`。

排障时能判断结果是否经历了隐式恢复。

---

# 第十三部分：协议错误结果与 Runtime Error 不相同

## 123. MCP server 可返回 `CallToolResult.is_error=true`

这仍是一次成功完成的 JSON-RPC response。

Rust 调用层得到 `Ok(CallToolResult)`，不会当作 transport/runtime failure。

## 124. `is_error=true` 转换成 MCPOutput error

代码收集其中 Text blocks，组成业务错误文本。

它作为 ToolOutput 回到 Agent conversation。

## 125. 为什么不抛 ToolError

ToolError 表示调度、transport、timeout 等执行基础设施失败。

MCP `isError` 是目标工具已执行并报告的语义结果，应保留正常 Tool Result 配对。

## 126. 成功 content 支持多种 block

- Text：直接文本；
- Image：data URI；
- image Blob Resource：同样转 data URI；
- 其他 Resource：序列化 JSON；
- 不认识的 block：跳过。

## 127. 多个 block 用换行拼接

顺序保持 server response 的 content 顺序。

## 128. 图片默认只产生 data URI

Session 层可以提取并变成 vision tokens。

## 129. `expose_image_base64` 的附加行为

启用时还附加 `<mcp_image_base64>` wrapper，使 raw bytes 在 data URI 提取后仍对 Agent 可见，支持路径化转发等场景。

## 130. Tool 完成事件区分协议 error

`success = !is_error`。

即使 Rust Future 返回 Ok，MCP semantic error 在 telemetry 中仍记录失败。

---

# 第十四部分：`GrokClientHandler` 接收 Server Push

## 131. 为什么不用 rmcp 默认 ClientInfo handler

默认 handler 能完成协议，却不把 `tools/list_changed`、`resources/list_changed` 路由到 Session。

自定义 handler 保留 ClientInfo，同时增加事件桥。

## 132. Handler 持有共享 sender slot

它不是在 handshake 时 clone 一个 `Option<Sender>` 值，而是 clone 整个 Arc slot。

后接入 Session dispatcher 也不会丢失未来通知。

## 133. Tool list notification

```text
on_tool_list_changed
→ McpClientEvent::ToolsChanged { server }
```

## 134. Resource list notification

同样映射为 `ResourcesChanged`。

## 135. Send failure 静默处理

Session teardown 或无 owner 时 receiver 可能不存在。

notification handler 不能把 send error 返回 rmcp，否则会反向击穿 service loop。

## 136. Handler 方法不使用 `async_trait`

rmcp trait 使用 return-position `impl Future`；宏生成的 boxed future 签名不兼容。

源码显式记录了这一点，防止未来“统一风格”式误改。

---

# 第十五部分：ListChanged 当前真正做到哪里

## 137. Event 先进入 50ms tumbling window

相同 `(server, kind)` 在窗口内 last-write-wins。

100 个 burst `ToolsChanged` 可以合并为一个 entry。

## 138. 不同 kind 不互相覆盖

同 server 的 `ToolsChanged` 与 `TransportClosed` 使用不同 key，会各自保留。

## 139. Status payload 映射

ToolsChanged/ResourcesChanged 当前映射为：

```text
status = ready
reason = config_changed
```

## 140. Dispatcher 不重新执行 `tools/list`

它不会在该事件 arm 中：

- 请求新工具目录；
- diff registrations；
- unregister 旧 Tool；
- register 新 Tool；
- refresh ToolMetadataSnapshot。

## 141. `RefreshMcpSearchIndex` 也不是此事件的直接消费者

当前命令主要由 Agent-level Managed Gateway catalog 更新向 Session 广播。

它只重建 snapshot 投影，也不会替代 local server 的重新 list/register。

## 142. 因此这是一个明确的实现边界

Server push 已经能通知 UI“配置变化”，但 local dynamic Tool 的自动目录同步链尚未在这里闭合。

显式 re-init、auth recovery、toggle、Bridge rebuild 等路径仍会重新注册并刷新。

## 143. 正确补全需要比“收到事件就刷新快照”更多

仅从旧 Bridge 刷新只会得到旧 definitions。

完整实现需要：

```text
re-list server
→ validate/diff registrations
→ remove disappeared tools
→ add/update current tools
→ refresh metadata snapshot
→ UI notification/reminder
```

## 144. 还要处理并发与旧 Client

list_changed 可能来自即将被替换的 Client；重同步必须绑定 client identity/generation，避免旧事件覆盖新连接目录。

---

# 第十六部分：Liveness Watcher 只判断 Transport Closure

## 145. 为什么需要轮询

rmcp `RunningService` 没有暴露可直接 await 的 transport-shutdown future。

最接近的信号是 `is_transport_closed()`。

## 146. 默认 500ms poll interval

每 tick 成本是 state Mutex + closed flag 检查；平均发现延迟低于一秒。

## 147. `arm_liveness_watcher()` 的 gates

- ACP transport：不装；
- 未接 event sender：不装；
- state 非 Ready：不装；
- 已有 handle：不重复装。

## 148. 为什么 ACP 不装 watcher

ACP reverse transport 的生命周期由宿主通道管理，不沿用本地/HTTP Client 的轮询恢复模型。

## 149. Watcher 每 tick 只做 `liveness_check()`

它绝不能调用 `ensure_initialized()`。

状态查询若触发网络 handshake，会让 UI status modal 等只读操作阻塞数十秒。

## 150. 三值分类

```text
Healthy
TransportClosed
Transient
```

比简单 bool 更重要的是区分“真的 closed”与“状态被外部 reset”。

## 151. Healthy

Ready 且 service transport open：继续 poll，不发事件。

## 152. TransportClosed

Ready 且 closed：清 handle slot，发一次带 client ID 的 event，然后退出。

## 153. Transient

Pending、Initializing 或 Empty：清 slot，静默退出。

这表示其他恢复/重认证逻辑已经接管，不应再报告断线。

## 154. 为什么不能用 `!is_healthy()` 直接发断线

reset transport 会合法地把 Ready 改成 Pending。

若非健康都视为 closed，每次 re-handshake 都会制造虚假 TransportClosed。

## 155. Watcher 是 one-shot

无论 closed 或 transient，退出前清 slot。

新 handshake 成功后由 owner/recover 路径重新 arm。

## 156. Handle Drop 自动取消 task

`TransportLivenessHandle` 持有 CancellationToken 的 DropGuard。

Client teardown 或替换 handle 时，无需显式 abort API。

## 157. Interval 第一次立即 tick

若 transport 在 Ready 发布到 watcher 安装之间已经关闭，可以立即发现。

## 158. Missed tick 选择 Skip

Runtime stall 后不补做一串无意义历史 poll，只检查当前状态。

---

# 第十七部分：StatusDispatcher 如何安全消费断线事件

## 159. 事件按 `(server, kind)` 合并

TransportClosed 还额外收集窗口内所有 client IDs。

因为同 key last-write-wins 不能丢掉可能匹配当前实例的旧/新 ID 集合。

## 160. 先判断 transport 类型

可恢复的非 managed、enabled HTTP/SSE Client 保留在 McpState，走 in-place recovery。

Stdio dead Client 则是 eviction + respawn。

## 161. Stale close 防御

Dispatcher 比较：

```text
closed IDs contains current owned_client.client_id ?
```

不匹配说明事件属于已替换实例。

## 162. Stale event 会被彻底剥离

它不会：

- 删除 current Client；
- 推 unavailable status；
- 写 disconnect span；
- 安排 restart。

## 163. 为什么 ConfigRemoved 不在这里删 Client

Config diff producer 已同步更新 McpState、移除旧 Client，再发送事件。

若 flush 时仍有同名 Client，它可能是 remove+re-add 后的新实例。

## 164. `shutting_down` 只由 ConfigRemoved 标记

TransportClosed 代表故障，不代表用户意图关闭。

若把它也标记 shutting_down，auto-restart 的第一道 gate 会把所有真实崩溃都跳过。

## 165. Ready 清除 shutting_down 标记

Server 成功重新握手后，应恢复处理未来故障的资格。

---

# 第十八部分：Stdio Auto Restart

## 166. Stdio 不能在原 Client 内 recover

child process 已退出/被消费，必须重新执行 `start_mcp_server()`，创建全新 Client。

这就是 auto-restart 位于 Shell Session 层而不是 McpClient 内部的原因。

## 167. 哪些事件可以调度 restart

- TransportClosed；
- HandshakeFailed。

其他状态变化不触发。

## 168. 调度前 guard rails

- 不在 intentional shutting_down；
- 当前仍配置且启用为 stdio；
- 同 server 没有 restart task in flight。

## 169. In-flight claim 防止双进程

若两个事件窗口分别触发 restart，没有 dedup 会 spawn 两个 child，并竞争写入 owned_clients。

`begin_restart` 原子占位，RAII guard 在所有退出路径释放。

## 170. Backoff 阶梯

```text
attempt 1: wait 1s
attempt 2: wait 4s
attempt 3: wait 16s
cumulative: 1s, 5s, 21s
```

## 171. 每次 sleep 后重新检查配置

用户可能在 backoff 期间禁用 server。

Task 必须停止，不能把用户刚关掉的进程复活。

## 172. Session shutdown 可取消 backoff

Dispatcher channel 关闭时取消 token。

Restart task 不会拖住退出 21 秒，也不会向正在 teardown 的 Gateway 推 status。

## 173. Restart 成功由 restart task 独占发布

`respawn_stdio` 特意在 `ensure_initialized` 后才接 event sender，避免同时发 Initialized 和 RestartSucceeded 两个成功状态。

## 174. 三次失败后 park

发布 exhausted 状态，并 unregister server tools。

模型不应继续看到指向不存在 Client 的旧执行 entries。

## 175. Explicit Refresh 是 park 后恢复入口

自动重试有界，避免永久后台循环和资源消耗。

---

# 第十九部分：HTTP In-place Recovery

## 176. HTTP 不删除 Client 对象

它保留相同 Arc、Tool registrations 和 identity，只替换内部 transport state。

## 177. 为什么 HTTP 与 stdio 策略不同

HTTP 连接地址可重建；远端服务可能只是 rolling deploy。

不必重建整个 Client 和 Toolset entry。

## 178. Recovery 首次立即尝试

随后 backoff：

```text
1s, 4s, 16s, 30s, 30s, 30s, 30s
```

共八次尝试，覆盖约 2.5 分钟。

## 179. Managed HTTP 被排除

Managed connector 有自己的 rotating credential/reactive re-auth 路径。

普通 HTTP recovery 只处理 non-managed、enabled HTTP/SSE。

## 180. HTTP recovery 不自行推 success/failure status

`ensure_initialized()` 的 Ready/HandshakeFailed event 是状态唯一发布者。

避免恢复 loop 与 Client 双重公告。

## 181. Exhaust 后仍保留 lazy recovery

Client 保持 Pending；未来 Tool Call 调用 `ensure_initialized()` 时仍可再次尝试。

“park”不是永久封死。

## 182. Lazy 与 proactive 共用 `recover()`

Dispatcher 的后台 HTTP recovery 和 Tool Call 遇错后的同步 retry 都走 reset → handshake → re-arm。

这降低两条恢复路径的行为漂移。

---

# 第二十部分：两条完整时序

## 183. 首次连接：HTTP OAuth server

```text
SessionActor
  → start_mcp_server(Http)
  → no Authorization header
  → discover OAuth metadata
  → load stored credential
  → McpClient Pending(HttpAuth)
  → set_event_tx
  → get_tool_registrations
      → ensure_initialized
          → Pending → Initializing
          → build AuthClient
          → handler.serve / initialize
          → Ready(service)
          → notify waiters
          → Ready event
      → paginated tools/list
  → register model-visible tools
  → refresh search snapshot
  → arm liveness watcher
```

## 184. 同时到来的第一下 Tool Call

```text
background: ensure_initialized owns handshake
model call: ensure_initialized sees Initializing
  → waits init_done
background stores Ready + notify
model call loops, sees Ready
  → reuses same service
  → tools/call
```

它不会收到“already initializing”。

## 185. HTTP 断线恢复

```text
liveness: Ready + closed
  → TransportClosed(client_id)
dispatcher:
  → keep HTTP Client in McpState
  → push unavailable
  → schedule HTTP recovery
recovery:
  → Ready → Pending(reconnect config)
  → ensure_initialized single-flight
  → Ready event
  → re-arm watcher
```

## 186. Stdio 崩溃恢复

```text
liveness → TransportClosed(old client_id)
dispatcher:
  → identity match
  → remove old owned Client
  → push unavailable
  → schedule stdio restart
restart after 1/4/16s:
  → spawn fresh child
  → fresh McpClient + fresh client_id
  → initialize + list/register
  → replace owned client
  → RestartSucceeded
```

## 187. Timeout 的时序

```text
tools/call sent
server may or may not execute
client timeout expires
  → mark timeout
  → HTTP state reset for next call
  → DO NOT replay current call
  → return ToolError
```

---

# 第二十一部分：正确性边界与审查重点

## 188. Single-flight 只覆盖一个 Client 实例

若上层错误地为同一 server 同时创建两个 McpClient，它们各有自己的 state/Notify，仍能并行握手。

Session 的 generation、owned_clients 和 restart dedup 负责更高层唯一性。

## 189. Ready 不等于远端永远健康

它表示 initialize 完成、service 当前可用。

后续 disconnect、401、rolling deploy 都需 liveness/tool-call path 发现。

## 190. HTTP idle 健康检查有限

Streamable HTTP 的 service loop 可能直到下一次 send failure 才知道远端已不可达。

`is_transport_closed()` 不是主动 HTTP health probe。

## 191. Auth 分类依赖部分错误文本

代码对 401 wording 做了保守 classifier，并排除 `401ms`、`4012` 等误判。

但跨 server 错误格式不统一，所以某些路径选择“有 manager 就尝试 refresh”。

## 192. Tool retry 不是 exactly-once

Transport failure 时一次 retry 能提高可用性，却无法证明 server 没执行第一次请求。

高副作用 MCP Tool 最好自行支持 idempotency key。

## 193. ListChanged 目前不是目录同步事务

它只产生 status signal。

读代码时不能因为 handler 名叫 `on_tool_list_changed` 就推断 SearchTool 目录已经更新。

## 194. Timeout 注释存在漂移

文档和 code comment 若写 60 秒，而实际常量为 6000 秒，测试运行将遵循 6000。

这是维护时应修正的非行为 bug。

## 195. 锁的职责必须分清

| 锁 | 保护对象 | 是否跨 await |
| --- | --- | --- |
| `state: tokio::Mutex` | ClientState/transport owner | 只在状态读写；handshake 锁外 |
| auth manager mutex | credential mutation | 会跨 auth async operation |
| notify_tx parking_lot mutex | sender Option | 否 |
| liveness slot parking_lot mutex | watcher handle | 否 |
| McpState tokio mutex | server/client maps | clone Arc 后释放再网络调用 |

## 196. 事件不是状态事实源

Event 用于通知、UI、telemetry 和恢复调度。

真正能否调用仍由 ClientState、McpState 中 current Client 和 ToolBridge entries 决定。

---

# 第二十二部分：如何调试

## 197. 初始化失败

依次检查：

- config transport variant；
- stdio executable/PATH/env；
- stderr log；
- OAuth discovery result；
- startup timeout；
- ClientState 是 Pending 还是 Empty；
- HandshakeFailed detail；
- generation 是否过期。

## 198. Tool Call 偶发失败

检查 completed event 中：

- `is_timeout`；
- `reconnect_attempted`；
- `auth_retry_attempted`；
- server/tool/qualified name；
- duration；
- semantic `isError` 还是 Runtime ToolError。

## 199. Server 显示 Ready 但新 Tool 搜不到

若 server 只推送了 `tools/list_changed`，当前代码可能只更新 status。

检查是否发生显式 re-list/register；仅刷新旧 snapshot 不足以取得新 Tool。

## 200. Server 崩溃后没有自动恢复

检查：

- watcher 是否成功 arm；
- event sender 是否已 wiring；
- client ID 是否被判 stale；
- auto restart 配置是否启用；
- server 是否在 disabled/shutting_down；
- 是否已有 in-flight claim；
- stdio/HTTP 是否走了正确恢复分支。

## 201. 快速源码检索

```sh
rg "enum PendingTransport|enum ClientState|ensure_initialized" crates/codegen/xai-grok-mcp/src/servers.rs
rg "try_call_tool|recover_and_retry|force_reauth" crates/codegen/xai-grok-mcp/src/servers.rs
rg "spawn_transport_liveness|LivenessCheck" crates/codegen/xai-grok-mcp/src
rg "collect_close_candidates|drop_dead_clients|run_dispatcher" crates/codegen/xai-grok-shell/src/session
rg "auto_restart_stdio|http_recovery_loop" crates/codegen/xai-grok-shell/src/session
```

## 202. 目标测试建议

```sh
cargo test -p xai-grok-mcp ensure_initialized --lib
cargo test -p xai-grok-mcp liveness --lib
cargo test -p xai-grok-mcp auth_rejection --lib
cargo test -p xai-grok-shell mcp_restart --lib
cargo test -p xai-grok-shell mcp_dispatcher --lib
```

Shell lib target 若被其他测试模块的编译错误阻塞，应记录 blocker，不为本文修改无关源码。

---

# 第二十三部分：结论

## 203. 一句话模型

```text
McpClient 用 PendingTransport 表达可握手资源，
用 Single-flight State + Notify + InitGuard 管理并发和取消，
再把 Tool Call 的 transport、auth、timeout 恢复与 Session 层的 liveness、restart 分层处理。
```

## 204. 关键不变量

1. 一个 McpClient 同时最多有一个 handshake holder。
2. Waiter 在读 state 前订阅 Notify，避免 lost wakeup。
3. Handshake 不跨持有 state lock。
4. 正常和失败发布都先写 state、再唤醒 waiter。
5. HTTP/ACP transport 可恢复；已消耗的 Stdio child 不可恢复。
6. Holder 取消时 InitGuard 尽力恢复，并始终唤醒 waiter。
7. Tool timeout 不重放副作用型调用。
8. Transport reconnect 与 OAuth re-auth 是不同恢复层。
9. Semantic `isError` 保持正常 Tool Result；基础设施错误才成为 ToolError。
10. Watcher 只对 Ready+closed 发事件，非 Ready 状态静默退出。
11. TransportClosed 绑定 client ID，旧事件不能删除 replacement。
12. Stdio 重建 Client，HTTP 原地重建 transport。
13. ListChanged status signal 不等于目录已经重新同步。

---

## 205. 本篇术语表

| 名词 | 白话解释 | 在本篇中的精确含义 |
| --- | --- | --- |
| transport | 传输通道 | 承载 MCP JSON-RPC 的 stdio、HTTP 或 ACP reverse channel |
| PendingTransport | 待握手传输 | 已配置、可被下一次 handshake holder 消耗的 transport resource |
| Stdio | 标准输入输出传输 | Agent 启动 child，并通过 stdin/stdout 交换逐行 JSON-RPC |
| Streamable HTTP | 可流式 HTTP 传输 | rmcp 使用的远程 MCP HTTP transport |
| ACP reverse transport | ACP 反向传输 | Agent 通过宿主提供的 reverse invoker 调用 SDK 内 MCP server |
| SafeTokioChildProcess | 安全子进程包装 | 管理 stdio transport、kill-on-drop、process group 和 graceful shutdown |
| ResilientRwTransport | 容错读写传输 | 跳过单行坏 JSON、只把真正 EOF 当 transport close 的 stdio 实现 |
| Pending | 待初始化状态 | Client 有可用 transport，但尚未建立 MCP service |
| Initializing | 初始化中状态 | 唯一 holder 已取得 transport，正在锁外 handshake |
| Ready | 就绪状态 | Client 持有可 clone 的 RunningService |
| Empty | 空状态 | 没有可用于下一次 handshake 的 transport |
| ClientStateKind | 状态类别投影 | 不暴露 payload 的 Copy 状态枚举 |
| handshake | 协议握手 | `handler.serve()` 建 transport service 并完成 MCP initialize |
| single-flight | 单飞 | 同一 Client 对同一初始化工作最多只有一个并发执行者 |
| handshake holder | 握手持有者 | 从 Pending 拿走 transport、负责发布结果的 task |
| waiter | 等待者 | 观察 Initializing 后等待 init_done 的并发 caller |
| Notify | 异步通知原语 | 握手完成或外部 state replace 后唤醒 waiter 的 Tokio 类型 |
| lost wakeup | 丢失唤醒 | 检查状态与订阅通知之间发生 notify，导致 waiter 永久错过 |
| inflight wait | 在途等待 | waiter 等待当前 handshake holder 的有界时长 |
| InitGuard | 初始化 RAII guard | holder 取消/panic 时尽力恢复 Pending 并通知 waiters |
| cancellation safety | 取消安全 | Future 中途被 drop 后共享状态仍可恢复或明确失败的性质 |
| RAII | 资源获取即初始化 | 借助对象 Drop 自动执行清理/释放的 Rust 模式 |
| restorable transport | 可恢复传输 | 能从 config/Arc 重建并再次 handshake 的 HTTP/Auth/ACP transport |
| reconnect snapshot | 重连快照 | Client 构造时保存的可恢复 PendingTransport 副本 |
| startup timeout | 启动超时 | initialize handshake 的时间上限 |
| tool timeout | 工具超时 | 单次 `tools/call` 等待 response 的时间上限 |
| per-tool timeout | 单工具超时 | 按 bare MCP Tool name 覆盖 server 默认值 |
| timeout replay | 超时重放 | 超时后再次发送同一调用；本实现为防重复副作用而不做 |
| side effect | 副作用 | 创建 issue、发消息等改变外部世界的操作 |
| at-most-once tendency | 至多一次倾向 | timeout 不自动 retry 以降低重复执行概率，但不构成严格保证 |
| exactly-once | 恰好一次 | 调用无论故障都只产生一次业务效果；需端到端幂等支持 |
| idempotency key | 幂等键 | Server 用来识别重复请求并复用结果的唯一 token |
| OAuth discovery | OAuth 发现 | 根据 RFC metadata 判断授权端点和 server 是否需要 OAuth |
| AuthorizationManager | 授权管理器 | 管理 metadata、credential store、access/refresh token 的共享对象 |
| credential store | 凭证存储 | 跨 Session/进程保存 OAuth token 的持久层 |
| interactive | 可交互模式 | 当前 Session 能打开浏览器让用户完成 OAuth |
| non-interactive | 无交互模式 | Headless/SDK Session 不能启动浏览器授权 |
| fail-open | 失败放行 | OAuth 探测不确定时继续 plain HTTP；Interactive 当前采用 |
| fail-closed | 失败关闭 | 无法确认凭证时拒绝启动；NonInteractive 当前采用 |
| token refresh | Token 刷新 | 用 refresh token 获取新 access token，不需浏览器 |
| re-auth | 重新认证 | disk reload、refresh grant、browser flow 组成的恢复阶梯 |
| transient auth failure | 暂时认证失败 | 实际为网络到 IdP 不通，不应立即丢弃凭证或开浏览器 |
| McpService | MCP 服务句柄 | `Arc<RunningService<RoleClient, GrokClientHandler>>` |
| ClientInfo | 客户端声明 | 协议版本、实现版本和 extension capabilities |
| protocol pin | 协议固定 | 显式声明 2025-06-18，避免依赖升级隐式改变线上协议 |
| McpErasedTool | MCP 动态工具包装 | 接收 JSON Value、查 Client、执行 tools/call 并转换结果的 Runtime Tool |
| qualified name | 全限定名 | LocalRegistry 使用的 `server__tool` identity |
| bare name | 裸名 | MCP server 在 tools/list/tools/call 中使用的原始 tool name |
| deterministic client error | 确定性客户端错误 | Parse/InvalidRequest/MethodNotFound/InvalidParams，重连无意义 |
| transport error | 传输错误 | TransportClosed/TransportSend 等连接级失败 |
| reconnect | 重连 | 从 reconnect snapshot 重建 transport 并重新 handshake |
| recover | 恢复 | reset transport、ensure initialized、re-arm watcher 的统一流程 |
| auth retry | 认证重试 | Tool Call 失败后执行 force_reauth，再尝试一次调用 |
| semantic error | 语义错误 | MCP response 正常返回，但 `CallToolResult.is_error=true` |
| Runtime ToolError | 运行时错误 | lookup、handshake、transport、timeout 等基础设施失败 |
| ContentBlock | 内容块 | MCP Tool result 中的 Text、Image、Resource 等条目 |
| data URI | 数据 URI | `data:image/...;base64,...` 形式的内联图片表示 |
| expose_image_base64 | 暴露原始图片开关 | 除 data URI 外保留 base64 wrapper 供 Agent 后续转发 |
| GrokClientHandler | 自定义 MCP handler | 提供 ClientInfo 并把 server push 转成 McpClientEvent |
| server push | Server 主动通知 | `notifications/tools/list_changed` 等无请求通知 |
| list_changed | 列表变化通知 | 表示 server 声称工具/资源目录改变，不自带完整新目录 |
| RPIT | 返回位置 impl Trait | rmcp notification trait 使用的 Future 返回签名形式 |
| event sender slot | 事件发送槽 | Client 与 handler 共享的 Arc<Mutex<Option<Sender>>> |
| liveness | 存活性 | Ready transport 是否已经关闭的运行时判断 |
| liveness watcher | 存活观察器 | 每 500ms 检查一次、一次性发 TransportClosed 的 task |
| Healthy | 健康分类 | Ready 且 transport open |
| TransportClosed | 断线分类/事件 | Ready service 的 transport sender 已关闭 |
| Transient | 过渡分类 | Pending/Initializing/Empty，watcher 静默退出 |
| one-shot watcher | 一次性观察器 | 首次 closed/transition 后退出，恢复后需重新安装 |
| DropGuard | Drop 取消守卫 | watcher handle 被丢弃时触发 CancellationToken |
| client ID | Client 实例 ID | 防止旧断线事件误删同名 replacement 的单调编号 |
| stale event | 陈旧事件 | 事件 client ID 不匹配当前 registered Client 的通知 |
| StatusDispatcher | 状态分派器 | 合并 McpClientEvent、推 ACP status 并触发恢复的 Session task |
| tumbling window | 滚动时间窗 | 固定 50ms 收集事件，到点整体 flush 的合并窗口 |
| last-write-wins | 最后写入生效 | 相同 `(server, kind)` 在窗口内只保留最后 payload |
| shutting_down | 主动关闭集合 | ConfigRemoved 标记的用户意图，用于阻止自动复活 |
| auto restart | 自动重启 | Stdio child 崩溃后有界 backoff respawn 新 Client |
| in-flight claim | 在途占位 | 保证同 server 只有一个 restart/recovery task 的去重标志 |
| exponential backoff | 指数退避 | 1、4、16 秒逐步增加的 stdio restart 等待 |
| park | 停驻 | 有界自动恢复耗尽后停止后台尝试，等待显式或 lazy 触发 |
| in-place recovery | 原地恢复 | 保留 HTTP Client Arc，只替换内部 transport/service |
| rolling deploy | 滚动发布 | 远端服务短时下线再恢复，HTTP recovery ladder 的典型场景 |
| directory resync | 目录重同步 | 重新 tools/list、diff/register/unregister 并刷新 Search snapshot |
| status signal | 状态信号 | 告诉 UI 有变化，但不等价于目录内容已经同步 |
| eventual consistency | 最终一致 | Client、Bridge、search snapshot 与 UI 经多步骤最终收敛的模型 |

---

## 206. 下一篇建议

下一篇可以继续精读 Agent 的 Tool Permission 与 MCP 交界：

> **源码精读 35：Dynamic Tool Safety——MCP Tool 的权限分类、读写语义、确认边界、输出不可信性、Prompt Injection 面与审计链**

重点回答动态 Tool 没有编译期 ToolKind 时怎样进入权限决策，外部 Tool description/result 为什么必须当作不可信数据，以及 Gateway、Local MCP、UI-only Tool 的授权边界是否一致。
