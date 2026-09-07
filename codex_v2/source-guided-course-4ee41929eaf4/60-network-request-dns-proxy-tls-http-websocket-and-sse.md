# 60：网络请求完整链路——DNS、代理、TLS、HTTP、WebSocket 与 SSE 怎样连在一起

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方文档说明：Codex 云端 Agent 默认在 agent phase 阻止互联网访问；开启后可使用域名 allowlist，并可把 HTTP 方法限制为 `GET`、`HEAD`、`OPTIONS`。这描述的是产品安全策略，不规定本章涉及的本地 Rust HTTP client、PAC、企业 CA、SSE parser、WebSocket dialer 等内部实现。
>
> 官方对照资料：[Agent internet access](https://learn.chatgpt.com/docs/cloud/internet-access)。

## 1. 本章解决什么问题

看到下面的错误时，你能判断失败在哪一层吗？

```text
name or service not known
connection refused
proxy authentication required
certificate verify failed
HTTP 429
stream closed before response.completed
idle timeout waiting for SSE
websocket closed
network access blocked by policy
```

它们都可以被笼统称为“网络出问题了”，但根因和解决办法完全不同。本章回答：

- Codex 自己访问模型 API，与 shell 命令访问互联网是不是同一条链？
- URL 怎样变成 hostname、IP、TCP connection 和 HTTP request？
- 系统代理、环境变量代理与 direct route 怎样选择？
- `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 分别做什么？
- PAC、WPAD 是什么，为什么代理决策与完整 URL 有关？
- TLS 证书验证到底在验证什么？企业代理为什么需要 custom CA？
- HTTP 普通响应、SSE 与 WebSocket 有什么区别？
- connect timeout、request timeout、stream idle timeout 有什么区别？
- HTTP redirect 为什么可能改变代理路线并泄漏 Authorization？
- 哪些错误可以重试，建立流以后断开为何更棘手？
- 怎样系统排查 DNS、proxy、TLS、HTTP 和 stream 问题？

## 2. 先说人话：访问网址不是一个动作

执行：

```text
POST https://api.example.com/responses
```

背后至少有这些步骤：

```text
解析 URL
选择 direct 或 proxy route
把 hostname 解析成 IP
建立 TCP connection
进行 TLS handshake
发送 HTTP request line、headers 和 body
接收 HTTP status 与 headers
持续读取 response body
把 SSE bytes 解析成 events
把 JSON event 转成 Codex ResponseEvent
```

任一步都可能单独失败。

## 3. 用长途电话建立直觉

可以把网络请求想成打长途电话：

| 网络概念 | 电话类比 |
|---|---|
| URL | 完整联系说明 |
| DNS | 查电话号码 |
| IP address | 电话号码 |
| Port | 总机后的分机 |
| TCP | 接通一条可靠通话线路 |
| Proxy | 先打公司中转台 |
| TLS | 验证对方身份并加密谈话 |
| HTTP | 约定怎样提问和回答 |
| SSE | 对方在一通电话中不断单向播报 |
| WebSocket | 双方保持一通可双向讲话的电话 |
| Timeout | 某个阶段等太久便放弃 |

## 4. 第一条核心原则：先区分两种“Codex 联网”

Codex 中至少有两条不同网络路径：

```text
A. Codex 产品进程自己的网络
   登录、模型 API、Responses SSE/WebSocket、exec-server registry

B. Codex 启动的工具/子进程网络
   curl、git、npm、cargo、用户程序、测试程序
```

它们可以有不同的代理、凭据、sandbox、allowlist、审批和错误类型。

## 5. 模型 API 可用不证明 shell 可联网

即使 Codex 已经成功连接模型服务，下面的命令仍可能被 sandbox 或 managed network policy 阻止：

```bash
curl https://example.com
```

反过来，shell 能访问 GitHub，也不证明 Codex 的模型 WebSocket 能穿过企业代理或信任企业 CA。

## 6. 排错时先问“是谁发起请求”

至少确认：

```text
Codex core？
HTTP client crate？
Responses API client？
exec-server？
MCP server？
managed proxy？
shell child process？
远程 environment 内的进程？
```

发起者不同，配置和日志入口就不同。

## 7. URL 包含哪些部分

以此为例：

```text
https://api.example.com:8443/v1/responses?mode=stream
```

可拆为：

```text
scheme   https
host     api.example.com
port     8443
path     /v1/responses
query    mode=stream
```

默认 port 通常由 scheme 推导：HTTP/WS 为 80，HTTPS/WSS 为 443。

## 8. Scheme 决定的不只是默认端口

常见 scheme：

| Scheme | 主要语义 |
|---|---|
| `http` | TCP 上的明文 HTTP |
| `https` | TLS 上的 HTTP |
| `ws` | HTTP upgrade 后的明文 WebSocket |
| `wss` | TLS + HTTP upgrade + WebSocket |

Codex `Provider::websocket_url_for_path()` 会把 `http` 转成 `ws`、把 `https` 转成 `wss`。

## 9. Hostname、IP 与 origin 不一样

```text
Hostname  api.example.com
IP        203.0.113.10
Origin    scheme + hostname + effective port
```

安全判断通常不能只看 IP：TLS 证书和 HTTP Host 依赖 hostname；redirect 是否跨 origin 还取决于 scheme 与 port。

## 10. DNS 做什么

DNS 把 hostname 解析成一个或多个 IP address：

```text
api.example.com
  → 2001:db8::10
  → 203.0.113.10
```

DNS 成功不代表 TCP 可连接；DNS 失败也不代表远端服务器宕机，可能只是 resolver、VPN 或企业网络配置问题。

## 11. DNS 错误常见表现

```text
No such host
Name or service not known
Temporary failure in name resolution
could not resolve to any address
```

排查时要问：解析的是目标 host，还是 proxy host？通过代理时，本地未必直接解析最终目标。

## 12. WebSocket dialer 怎样显式解析 DNS

`codex-rs/websocket-client/src/dialer.rs` 的 direct/custom route 会调用：

```rust
tokio::net::lookup_host(address).await
```

得到 `Vec<SocketAddr>` 后，再用 Happy Eyeballs 建立 TCP。

## 13. HTTP DNS 为什么不一定在 Codex 源码里显式出现

普通 HTTP 走 reqwest。DNS、socket pool 和部分 TLS 细节由 reqwest 及其底层 transport 实现。

因此找不到 `lookup_host()` 不代表没有 DNS；只是该职责封装在依赖库边界内。

## 14. IPv4 与 IPv6 为什么需要 Happy Eyeballs

一个域名可能同时返回 IPv6 与 IPv4。如果客户端只顺序等待第一个不可用地址，连接会很慢。

Happy Eyeballs 的目标是：

```text
优先尝试一个地址
短暂等待后交错尝试另一地址族
哪个先成功就使用哪个
```

## 15. 当前 WebSocket 连接的 250 ms 交错

`HAPPY_EYEBALLS_DELAY` 当前为 250 ms。

Dialer 以 DNS 返回的第一个地址族为 preferred，并把另一地址族交错进后续尝试。一次失败也会立即取下一个地址，不必永远等固定间隔。

## 16. TCP connection 是什么

TCP 提供：

- 有序 byte stream；
- 丢包重传；
- 流量与拥塞控制；
- connection 生命周期。

它不提供应用身份验证，也不知道 HTTP message 边界。

## 17. `connection refused` 与 timeout 不一样

```text
connection refused
  目标地址可达，但端口没有 listener 或主动拒绝

connect timeout
  在预算内没有完成连接，可能是丢包、防火墙、黑洞路由
```

二者可能都映射为 connect/network error，但诊断含义不同。

## 18. Port 指向网络服务入口

同一个 IP 可以监听多个服务：

```text
443   HTTPS
3128  常见 HTTP proxy port
8081  当前 managed SOCKS 默认端口
```

端口开放只证明有 listener，不证明它说的是预期协议。

## 19. `TCP_NODELAY` 做什么

Nagle 算法会合并小包以提高吞吐效率，却可能增加低延迟交互的等待。

`WebSocketConnector::with_tcp_nodelay()` 可禁用它。源码把这个选择显式化，而不是假设所有 WebSocket 都应无条件修改 socket 行为。

## 20. Proxy 是什么

代理让 client 不直接连接目标，而是先连接中间节点：

```text
Client → Proxy → Target
```

代理可用于：

- 企业出口控制；
- 审计；
- 域名/method allowlist；
- 缓存；
- 隔离 sandbox 网络；
- TLS inspection。

## 21. Forward proxy 与 reverse proxy

本章主要讲 forward proxy：代表 client 访问外部目标。

Reverse proxy 位于 server 前方，代表后端接收请求。两者都叫 proxy，但配置位置与信任边界相反。

## 22. Direct、TransportDefault 与 Proxy route

`OutboundProxyRoute` 有三类：

```text
TransportDefault
  保留 reqwest/tungstenite 自己的代理行为

Direct
  明确禁用 transport 层代理发现

Proxy { url, no_proxy }
  使用已选定的具体代理
```

这不是简单的 `Option<proxy_url>`；`TransportDefault` 与 `Direct` 语义不同。

## 23. 为什么调用方必须显式选择 proxy policy

`OutboundProxyPolicy` 包含：

```text
ReqwestDefault
RespectSystemProxy
```

显式 policy 防止某个新 call site 因忘记读取 feature/config，就悄悄使用不同网络路径。

## 24. `HttpClientFactory` 是网络策略入口

产品流量通常通过 `HttpClientFactory` 构造 client。调用方提供：

- 目标 URL；
- 粗粒度 `ClientRouteClass`；
- 已解析的 outbound proxy policy。

单个 endpoint 不应各自重新猜测系统代理配置。

## 25. `ClientRouteClass` 是什么

当前分为：

```text
Auth
Api
WebSocket
Other
```

它不是目标 URL，也不是已选 proxy route，而是用于诊断“哪类产品流量正在构造 client”的粗分类。

## 26. 为什么 route class 不保存完整 endpoint

完整 URL 可能含：

- query token；
- signed upload credential；
- 私有 hostname；
- 用户路径信息。

粗分类让 telemetry 能区分 auth/API/WebSocket，同时降低泄漏风险。

## 27. 系统代理是什么

操作系统或企业策略可设置全局代理、自动配置脚本或按 URL 选择路线。macOS 和 Windows 有各自的系统 API。

当前 `RespectSystemProxy` 会优先尝试平台系统解析；不可用时才看环境变量，最后 direct。

## 28. PAC 是什么

PAC 是 Proxy Auto-Configuration。它可以根据完整 URL、host 等返回：

```text
DIRECT
PROXY proxy.example:8080
```

因此代理选择可能不是“整个进程只有一个 proxy”，而是每个 destination 都不同。

## 29. WPAD 是什么

WPAD 是自动发现代理配置的一类机制。它和 PAC 都可能依赖操作系统/企业环境，解析过程还可能同步阻塞。

课程重点不是协议细节，而是：route resolution 本身也是一个有成本、可失败、需缓存的步骤。

## 30. 环境变量代理的 fallback 顺序

当前显式 fallback 大致为：

```text
https → HTTPS_PROXY → ALL_PROXY
wss   → HTTPS_PROXY → HTTP_PROXY → ALL_PROXY
http/ws → HTTP_PROXY → ALL_PROXY
其他  → ALL_PROXY
```

`NO_PROXY` 随具体 proxy route 保留，用于匹配无需经过代理的目标。

## 31. 大小写环境变量

现实软件常同时认识：

```text
HTTP_PROXY / http_proxy
HTTPS_PROXY / https_proxy
ALL_PROXY / all_proxy
NO_PROXY / no_proxy
```

具体优先级由实现决定。排错不能只检查其中一种拼写。

## 32. `NO_PROXY` 做什么

它描述哪些 host/port 应绕过代理，例如：

```text
localhost,127.0.0.1,.example.internal
```

匹配语义可能含：

- 精确 hostname；
- 域名后缀；
- port；
- wildcard；
- `*`。

不同库的细节不完全相同，不能把配置想当然地跨工具复制。

## 33. WebSocket 为何要映射到 HTTP scheme 做代理解析

平台 PAC/system proxy API 通常理解 HTTP/HTTPS URL。因此当前实现先把：

```text
wss://... → https://...
ws://...  → http://...
```

用于 route resolution，再以原 WebSocket URL 建立连接。

## 34. 系统代理解析为什么放到 blocking pool

macOS/Windows 的平台 lookup 可能同步阻塞。异步函数使用 `spawn_blocking`，避免卡住 Tokio worker。

此外使用单个 Semaphore permit，使多个 cache miss 不会无限占用 blocking threads。

## 35. 取消 caller 后为什么 permit 仍跟着 blocking task

同步系统调用无法因 async future 被取消就立即停止。若 caller 一取消便释放 permit，第二个 lookup 会并发启动，最终仍可能堆积。

源码把 permit 移进 blocking task，直到真实同步工作结束才释放。

## 36. 系统代理决策缓存

当前参数：

```text
成功 Direct/Proxy TTL       60 秒
Unavailable TTL              5 秒
最多 cache entries          256
```

失败缓存更短，让临时不可用较快恢复；总条目数有上限，避免按 URL 无界增长。

## 37. Cache key 为什么对 URL 做 hash

PAC 可能按完整 URL 决策，所以 key 不能只用 host。

但缓存若直接保留 raw URL，可能长期保存 query credential。当前实现用带版本前缀的 SHA-256 hash 作为 key。

## 38. Proxy route cache 与 HTTP connection pool 不一样

```text
Proxy decision cache
  记住某 URL 应 direct 还是走哪个 proxy

HTTP client/connection pool
  复用已构造 client 与底层连接
```

一个减少系统代理解析，另一个减少 client/TCP/TLS 建立成本。

## 39. `RouteAwareClientPool` 为什么存在

若 request/redirect URL 会变化，仅为第一个 URL 构造固定 proxy client 不够。

`RouteAwareClientPool` 对每个 URL 解析 route，并按 resolved route 复用 client。

## 40. 当前 client route cache 上限

Pool 最多缓存 16 个 route 对应的 `HttpClient`。

到达上限时移除一个现有 route。这里追求有界复用，不承诺复杂 LRU 行为。

## 41. Redirect 为什么必须重新选 route

请求：

```text
https://a.example/start
→ 302 https://b.example/result
```

PAC 可能要求 a direct、b 走 proxy。若 reqwest 在固定 client 内部自动跟随，第二跳会错误复用第一跳路线。

## 42. `RespectSystemProxy` 怎样处理 redirect

当前 pool 会：

1. 禁用 reqwest 内部 redirect；
2. 读取 redirect response；
3. 构造下一跳 request；
4. 为新 URL 重新解析 route；
5. 继续使用共享总 timeout deadline。

## 43. Redirect 上限

当前 `MAX_REDIRECTS` 为 10。

上限避免：

- a → b → a 循环；
- 无限消耗连接与时间；
- 恶意 redirect chain；
- 代理 route cache 被持续扰动。

## 44. 301/302/303 与 307/308 的 body 语义

当前 helper 按常见行为处理：

- POST 遇 301/302 可变成 GET 并丢 body；
- 303 通常变成 GET，HEAD 保留；
- 307/308 保留 method/body，需要 request 可 clone/replay。

流式或不可克隆 body 不能总被安全重放。

## 45. 跨 origin redirect 要移除敏感 headers

若 scheme、host 或 effective port 变化，当前实现移除：

```text
Authorization
Cookie / cookie2
Proxy-Authorization
WWW-Authenticate
```

否则服务 A 的 credential 可能被 redirect 到服务 B。

## 46. Proxy route 变化也要移除 Proxy-Authorization

即使目标 redirect 逻辑已处理敏感 header，若 resolved proxy 本身变化，也不能把前一个代理的认证交给新代理。

当前 pool 在 `previous_route != current_route` 时移除 `PROXY_AUTHORIZATION`。

## 47. Referer 也需要安全处理

当前逻辑：

- HTTPS → HTTP 不发送 Referer；
- 移除 URL username、password 和 fragment；
- 跨 origin 时只保留 origin 根，不保留 path/query。

Referer 不是无害字符串，它可能暴露敏感 URL 信息。

## 48. TLS 解决什么问题

TLS 主要提供：

- server identity verification；
- 通信机密性；
- 传输完整性。

它不证明 server 的业务逻辑可信，也不阻止你主动把 secret 发给一个证书有效的恶意域名。

## 49. TLS handshake 大致做什么

简化流程：

```text
协商 TLS 版本与算法
server 提供 certificate chain
client 验证 hostname、有效期与信任链
双方派生 session keys
之后用对称加密传输 application data
```

失败可能发生在连接建立之后、HTTP request 发送之前。

## 50. CA 与 root store 是什么

Certificate Authority 为证书签名。Client root store 保存它信任的根证书。

Server certificate chain 必须最终连接到可信 root，并满足 hostname、有效期等检查。

## 51. 企业 TLS inspection 为什么会报证书错

企业 proxy 可能终止 client TLS，再与目标建立另一条 TLS：

```text
Client ⇄ Enterprise proxy ⇄ Target
```

Client 实际看到由企业 CA 签发的证书。若企业 CA 不在 root store，就会验证失败。

## 52. Custom CA 不是“关闭证书验证”

正确做法是把明确的企业 CA 加入 trust store，而不是接受所有证书。

Codex custom CA 支持读取：

```text
CODEX_CA_CERTIFICATE
SSL_CERT_FILE
```

前者优先；空值视为未设置。

## 53. Custom CA 文件需要什么格式

当前 helper 读取 PEM bundle，支持多个 certificate block，并处理现实中的 `TRUSTED CERTIFICATE` label 和混合 CRL 等情况。

如果文件不可读、没有可用证书或注册失败，会返回带路径来源和修复提示的结构化错误。

## 54. HTTP 与 WebSocket 必须共享 CA policy

如果 HTTPS request 支持企业 CA，而 WSS 使用默认 root store，用户会看到：

```text
普通 API 正常
WebSocket certificate verify failed
```

`custom_ca.rs` 同时提供 reqwest builder 和 rustls WebSocket config 的构造路径。

## 55. 为什么直接 `reqwest::Client::new()` 容易埋坑

它可能绕过 Codex 的：

- outbound proxy policy；
- custom CA；
- request diagnostics；
- cookie store；
- route classification。

因此源码注释要求产品流量优先走 `HttpClientFactory` 或 `RouteAwareClientPool`。

## 56. `build_direct()` 不是普通默认选项

它明确绕过 proxy discovery，只适合：

- hermetic local test；
- localhost callback；
- 另有独立 egress routing 的 sandbox；
- 确实要求 direct 的例外路径。

普通产品流量不应因“代理很麻烦”就改成 direct。

## 57. HTTP 是 request/response 协议

请求主要包含：

```text
method
URL/path
headers
body
```

响应主要包含：

```text
status
headers
body stream
```

`200`、`429`、`500` 是 HTTP 层事实，不是 DNS/TCP/TLS 错误。

## 58. `HttpTransport` 为什么抽象 execute 与 stream

Trait 分为：

```rust
execute(Request) -> Response
stream(Request) -> StreamResponse
```

普通响应需要完整 body；流式响应在 headers 成功后立即返回一个持续产生 bytes 的 stream。

## 59. `TransportError` 的当前分类

```text
Http { status, url, headers, body }
RetryLimit
Timeout
Network(String)
Build(String)
```

它区分 HTTP 非成功 status、超时、网络故障与请求构造失败。

## 60. Request build error 发生在联网前

例如：

- 无效 header value；
- JSON 编码失败；
- body compression preparation 失败；
- URL 构造失败。

此类错误不能靠等待网络恢复解决。

## 61. HTTP status error 已经连到 server

收到 401、429、500 表示至少已经完成了足够的网络和 HTTP 交互，server 或中间代理返回了 response。

所以 429 不应被诊断为 DNS failure。

## 62. Headers 为什么重要

Headers 可携带：

- Content-Type；
- Authorization；
- request ID；
- rate-limit 信息；
- Retry-After；
- server model；
- turn state；
- proxy authentication challenge。

它们既是协议数据，也可能含敏感信息。

## 63. Trace headers 怎样进入请求

共享 `HttpClient` 在发送时注入 OpenTelemetry propagation headers，使 server trace 能与 client span 关联。

这帮助回答“这次 turn 对应服务端哪次 request”，但不应把 auth token 当成普通 telemetry field。

## 64. Request logging 为什么可以关闭

`without_request_logging()` 抑制 URL 与 response-header diagnostics，适合 signed upload 等 URL/header 本身带 credential 的 endpoint。

源码还提供 `RouteAwareRequestError::without_url()`，在返回/记录前移除 reqwest error 中的 URL。

## 65. 日志开关不等于不发 trace propagation

关闭 request URL/header logging 是诊断脱敏策略；trace header 注入属于分布式追踪协议。

两者目的不同，不应因关日志便默认关掉所有 correlation。

## 66. Connect timeout 只限制连接建立

`HttpClientBuilder::connect_timeout()` 的注释明确：只限制 connection establishment，不限制整个 request。

它适合避免 DNS/TCP/TLS connect 阶段无限等，但 server 处理和 body streaming 仍需其他预算。

## 67. Request timeout 覆盖更大范围

`RouteAwareRequestBuilder::timeout()` 从 route resolution 之前开始，覆盖：

```text
选择/构造 route client
建立连接
发送 request
等待 response
```

Redirect hops 共享同一个 deadline，并把 remaining duration 写回下一跳 request。

## 68. Timeout duration 与 deadline 的区别再出现

若每次 redirect 都重新获得完整 10 秒：

```text
10 redirects × 10 秒 = 最多约 100 秒
```

共享 deadline 则保证整个逻辑 request 只有一个总预算。

## 69. Body timeout 与 stream idle timeout

还要区分：

```text
Request timeout
  整次 HTTP request 的总预算

Stream idle timeout
  已建立 stream 后，连续多久没有下一条数据
```

一个长达 20 分钟、每秒有事件的 stream 不应因“总时长长”而触发 idle timeout。

## 70. Responses HTTP 路径怎样构造请求

`ResponsesClient::stream_request()` 会：

1. 编码 request JSON；
2. 加 session/thread/subagent headers；
3. 设置 `Accept: text/event-stream`；
4. 可选择 request compression；
5. 调用 transport `stream()`；
6. 把 byte stream 交给 SSE processor。

## 71. SSE 是什么

Server-Sent Events 是 HTTP response body 上的事件格式。典型 bytes：

```text
event: response.output_text.delta
data: {"type":"response.output_text.delta","delta":"你"}

```

空行结束一条 event。它主要是 server → client 单向推送。

## 72. SSE 与“一次返回完整 JSON”区别

完整 JSON：

```text
请求 → 等待全部生成 → 一次收到完整 body
```

SSE：

```text
请求 → 收到 headers → 不断收到 delta/event → completed
```

SSE 改善首 token 延迟，也引入半途断线、事件顺序和终态确认问题。

## 73. Byte chunk 不等于 SSE event

网络可能把一条 event 切成多个 chunks，也可能一个 chunk 带多条 events。

`eventsource_stream` 负责跨 byte 边界拼接 SSE framing；上层不能对每个 `bytes_stream()` item 直接做一次 JSON parse。

## 74. SSE event 又不等于 Codex `ResponseEvent`

解析分两层：

```text
bytes → SSE event { event/data/... }
SSE data JSON → ResponsesStreamEvent
ResponsesStreamEvent.kind → ResponseEvent
```

每层都可能遇到格式错误或未知类型。

## 75. 未知事件为什么可以忽略

`process_responses_event()` 对未知 `kind` 记录 trace，然后返回 `Ok(None)`。

这允许 server 增加 client 暂时不关心的事件，而不让旧 client 立即崩溃。真正必需的终态仍要验证。

## 76. 单条 JSON parse 失败的当前处理

SSE processor 遇到无法解析的 event data，会 debug log 后继续读下一条。

这是容错选择，但也意味着协议关键事件若损坏，最终可能以“缺少 completed”表现，而不是在第一处 parse error 就终止。

## 77. `response.completed` 为什么是终态证据

TCP EOF 只表示 stream 关闭，不证明模型响应完整。

当前 processor 只有发出 `ResponseEvent::Completed` 后正常返回。若 byte stream 先结束，会报：

```text
stream closed before response.completed
```

## 78. `response.failed` 怎样转成语义错误

它会识别：

```text
context window exceeded
quota exceeded
usage not included
cyber policy
invalid request
server overloaded
其他 retryable response failure
```

机器 category 不依赖 UI 文案。

## 79. SSE idle timeout 怎样工作

循环每次执行：

```rust
timeout(idle_timeout, stream.next()).await
```

每收到一条 SSE event，下一轮重新开始等待预算。超时提示为：

```text
idle timeout waiting for SSE
```

## 80. SSE 输出 channel 为什么有界

`spawn_response_stream()` 使用容量 1600 的 mpsc channel。

若下游处理慢，`send().await` 会产生背压，防止已解析事件在内存中无限积累。

## 81. HTTP stream 的重试边界在哪里

`EndpointSession` 的 retry 包住 `transport.stream(request)`，因此可重试的是：

```text
在成功取得 StreamResponse 之前的 HTTP/transport attempt
```

一旦 headers 已成功返回、SSE processor 开始读取，中途断线发生在这个 retry wrapper 之外。

## 82. 为什么 mid-stream retry 更困难

此时 server 可能已经生成并发送部分 item。简单重发完整 request 可能：

- 重复 output item；
- 重复 tool call；
- 丢失前一条 stream 的顺序；
- 改变模型随机结果；
- 重复计费或副作用。

恢复需要 response ID、incremental state、去重与协议支持，而不是普通 HTTP retry。

## 83. Provider retry policy 包含什么

`RetryConfig` 转成：

```text
max_attempts
base_delay
retry_429
retry_5xx
retry_transport
```

`RetryOn::should_retry()` 只对配置允许的 429、5xx、timeout/network 返回 true。

## 84. 当前 backoff 公式

概念上：

```text
base × 2^(attempt-1) × random jitter
```

Jitter 当前在约 0.9 到 1.1 之间，使用 saturating arithmetic 避免整数溢出。

## 85. `max_attempts` 名字需要结合循环阅读

`run_with_retry()` 的循环是：

```rust
for attempt in 0..=policy.max_attempts
```

所以阅读配置时必须确认该字段表达“最大重试次数”还是“最大总尝试数”。不要只按变量英文猜语义。

## 86. Authentication 为什么每个 attempt 重新应用

Retry closure 每次都会调用 `auth.apply_auth(req)`。

这样等待期间若 token refresh 或 auth provider 状态改变，新 attempt 不必机械复用已经过期的 header。

## 87. WebSocket 是什么

WebSocket 先通过 HTTP handshake 升级连接，然后在同一 connection 上双向传输 message frames：

```text
client → response.create
server → response events
client ↔ ping/pong/close
```

它适合复用长连接并支持双向消息，不等于普通 HTTP streaming body。

## 88. WebSocket handshake 仍然经过 HTTP

连接需要发送 upgrade request，server 通常返回 HTTP 101 Switching Protocols。

所以 handshake 可能收到普通 HTTP status、headers 和 body，当前 `map_ws_error()` 会把它们映射为 `TransportError::Http`。

## 89. WebSocket connector 的路线

`WebSocketConnector`：

1. 用 `HttpClientFactory` 解析 proxy route；
2. 准备 native roots + custom CA；
3. direct 时 DNS + Happy Eyeballs TCP；
4. proxy 时连接 proxy 并建立 tunnel；
5. 对 secure target 做 TLS；
6. 完成 WebSocket handshake。

## 90. HTTPS proxy 可能有两层 TLS

使用安全 proxy 时可能是：

```text
TLS #1：client ↔ HTTPS proxy
CONNECT tunnel
TLS #2：client ↔ target（穿过 tunnel）
WebSocket handshake
```

任一层证书配置不正确都可能报 TLS error。

## 91. WebSocket message 类型

当前 pump 处理：

```text
Text
Binary
Ping
Pong
Close
Frame
```

收到 Ping 会回复 Pong；Pong 本身不交给业务 event parser；Close 会转发后结束 pump。

## 92. WebSocket pump 为什么同时 select send 与 receive

单个 connection 既要接收 server event，也要发送 request/Pong。

`tokio::select!` 在 command channel 和 network stream 之间推进，避免业务层同时可变借用同一个 socket。

## 93. WebSocket command channel 与 message channel

当前：

- command channel 容量 32；
- inbound message channel 是 unbounded；
- 每次 send command 用 oneshot 返回发送结果；
- `WsStream::drop` 会 abort pump task。

这要求上层用 connection 串行化和及时消费限制实际积压。

## 94. 为什么一个 response stream 独占 WebSocket guard

`ResponsesWebsocketConnection` 在一条 response stream 生命周期内持有 `Mutex<Option<WsStream>>` guard。

这样不同请求的 event 不会在同一 socket 上无协议支持地交错；connection reuse 是顺序复用，不是任意 multiplexing。

## 95. WebSocket idle timeout

与 SSE 类似，读取 loop 对每个 `ws_stream.next()` 使用 provider 的 idle timeout。

超时提示：

```text
idle timeout waiting for websocket
```

关闭且没有 `response.completed` 则报告 stream 提前结束。

## 96. Terminal stream error 为什么立即丢弃连接

源码注释指出：若在错误后等待 graceful close handshake，可能无限卡住并掩盖原错误。

因此 terminal error 时从 guard 中 `take()` failed stream、释放锁并把错误立即发给 caller。

## 97. Handshake probe 解决什么问题

Probe 使用与真实连接相同的：

- URL；
- auth headers；
- proxy route；
- TLS/custom CA；
- upgrade handshake。

但不发送模型 request，只短暂观察 server 是否立即 Close。它能区分“握手可用”和“101 后立刻被 policy 关闭”。

## 98. WebSocket connection limit 是协议错误

当前解析特定 error code：

```text
websocket_connection_limit_reached
```

并映射为 retryable error，提示建立新的 WebSocket connection。它不是 DNS/TLS 问题。

## 99. `previous_response_not_found` 为什么特殊

增量请求可能引用 previous response。若 server 找不到它，当前实现映射为 retryable，并提示重试完整 request。

这说明 transport 已连通，失败在更高层的 session/state 协议。

## 100. Managed network proxy 属于另一条链

前面的 `HttpClientFactory` 主要服务 Codex 产品 outbound traffic。

`codex-rs/network-proxy` 则可为工具/子进程提供受管理的 HTTP、SOCKS、domain、method、Unix socket 与 credential policy。

两者都叫 proxy，但 owner 和威胁模型不同。

## 101. 官方云端互联网策略的核心风险

官方文档列出的风险包括：

- 从不可信网页受到 prompt injection；
- 泄漏代码或 secret；
- 下载恶意或脆弱依赖；
- 引入许可证受限内容。

所以“连接能成功”并不是唯一目标，还要证明连接被授权且范围最小。

## 102. Domain allowlist 是什么

Allowlist 只允许匹配的目标域名。例如只开放 package registry，而不是整个互联网。

当前 managed proxy config 可包含 allow/deny domain entries，并把规则编译为 glob sets。

## 103. Domain rule 不能单独解决所有安全问题

一个可信域名可能托管用户内容；redirect 可能跳到新域名；DNS 可返回私有 IP；GET 也可能触发服务器副作用。

因此仍需要 method policy、redirect re-evaluation、IP/local restriction、凭据隔离和 output review。

## 104. `Limited` 与 `Full` network mode

当前 `NetworkMode`：

```text
Limited
  HTTP 只允许 GET、HEAD、OPTIONS

Full
  允许所有 HTTP methods
```

Limited 下 HTTPS CONNECT 还需 MITM 才能检查 tunnel 内 method；否则不能假装看见加密内容。

## 105. 为什么 HTTPS CONNECT 会隐藏 method

普通 forward proxy 收到：

```text
CONNECT example.com:443
```

之后看到的是加密 TLS bytes，不知道内部是 GET 还是 POST。

要执行 method policy，只能阻止 tunnel，或在明确配置与信任下做 TLS interception/MITM。

## 106. MITM 是高风险能力

MITM proxy 需要：

- 动态为目标签发证书；
- 让 child 信任 managed CA；
- 解密和检查内容；
- 严格保护 CA/private key；
- 清楚处理 credential 与审计数据。

它不是普通“打开 HTTPS”的同义词。

## 107. Managed proxy 默认 bind 到 loopback

默认 HTTP proxy URL 为 `127.0.0.1:3128`，SOCKS URL 为 `127.0.0.1:8081`。

若请求非 loopback bind 且没有危险 override，当前 config 会 clamp 回 loopback，避免无意把代理暴露给局域网。

## 108. DNS 解析本身也受策略约束

Managed proxy runtime 有 2 秒 `DNS_LOOKUP_TIMEOUT`，并会判断解析结果是否指向不允许的 local/private address。

只按 hostname allowlist 而不检查解析 IP，可能留下访问本机服务或内网的路径。

## 109. Blocked request 保留哪些审计字段

`BlockedRequest` 包括：

```text
host
reason
client
method
mode
protocol
decision/source
port
timestamp
execution_id（不序列化到普通形状）
```

这些字段让“网络失败”能被解释为具体 policy decision。

## 110. Blocked events 为什么也要有界

当前 state 最多保留 200 个 blocked events，并另有 `blocked_total` 计数。

这样既能查看近期样本，又不会让持续扫描被拒绝的恶意程序无限占用内存。

## 111. Network approval 与普通 proxy auth 不一样

```text
Network approval
  用户/Guardian 是否允许本次目标访问

Proxy authentication
  client 用 credential 向企业/上游 proxy 证明身份
```

两者可能同时出现。HTTP 407 通常是 proxy auth 问题，不等于 Codex approval 被拒。

## 112. 一条模型 SSE 请求的完整主链

```text
Turn 构造 Responses request
  ↓
Provider 拼 base URL/path/query
  ↓
EndpointSession 加 auth 并进入 retry wrapper
  ↓
HttpTransport 构造 request
  ↓
HttpClientFactory 选择 proxy route / custom CA
  ↓
DNS → TCP → TLS → HTTP POST
  ↓
收到 success status + headers + byte stream
  ↓
eventsource parser 解析 SSE framing
  ↓
JSON → ResponsesStreamEvent → ResponseEvent
  ↓
直到 response.completed
```

## 113. 一条 Responses WebSocket 请求的完整主链

```text
Provider 把 HTTPS URL 转为 WSS
  ↓
合并 provider/extra/default/auth headers
  ↓
WebSocketConnector 解析 proxy route 与 custom CA
  ↓
DNS/Happy Eyeballs → TCP → optional proxy tunnel → TLS
  ↓
HTTP 101 upgrade
  ↓
发送 response.create text frame
  ↓
读取 Text/Ping/Pong/Close frames
  ↓
JSON event → ResponseEvent
  ↓
response.completed 或 terminal stream error
```

## 114. 诊断决策树

```text
谁在联网？Codex 产品进程还是 child tool？
├─ child tool
│  ├─ sandbox/network approval 是否允许？
│  ├─ managed proxy 是否记录 blocked request？
│  └─ child env 中 proxy/CA 是否正确？
└─ Codex 产品进程
   ├─ URL 能否构造？
   ├─ proxy route 是 direct/system/env 哪一种？
   ├─ proxy host/target host 能否 DNS resolve？
   ├─ TCP 是 refused 还是 timeout？
   ├─ TLS 是 hostname、expiry 还是 unknown CA？
   ├─ HTTP status 是 401/407/429/5xx？
   ├─ redirect 是否跨 origin/改变 route？
   └─ stream 是否缺 completed、idle timeout 或 WS close？
```

## 115. 示例一：浏览器能开，Codex 不能连

可能原因：

- 浏览器自动读取 PAC，Codex 路径未启用 system proxy policy；
- 浏览器信任企业 CA，Codex custom CA 未配置；
- 浏览器有 proxy login session，CLI 没有；
- 浏览器与 terminal 位于不同 VPN/network namespace；
- shell 设置了代理，但 GUI app 没继承这些 env，或反过来。

“浏览器能开”只是一条对照证据，不是网络栈等价证明。

## 116. 示例二：HTTP 正常，WebSocket 失败

排查：

1. `wss` 是否被 proxy policy 映射到正确路线？
2. Proxy 是否允许 CONNECT/Upgrade？
3. WSS 是否加载相同 custom CA？
4. HTTP 101 后是否立即收到 Close frame？
5. 企业网关是否限制长连接或 WebSocket extension？
6. 是 handshake 失败，还是 response stream idle timeout？

## 117. 示例三：收到 407

`407 Proxy Authentication Required` 表示请求到达 proxy，proxy 要求身份验证。

下一步不是修改 DNS，而是检查：

- selected proxy 是否正确；
- proxy credential 来源；
- route change 是否移除了旧 `Proxy-Authorization`；
- 企业 SSO/session 是否适用于 CLI；
- 日志中是否安全地隐藏 credential。

## 118. 示例四：证书验证失败

依次问：

```text
失败的是 proxy TLS 还是 target TLS？
certificate hostname 是否匹配？
系统时间是否正确？
证书是否过期？
企业 CA 是否在 CODEX_CA_CERTIFICATE/SSL_CERT_FILE？
PEM 是否含可用 CERTIFICATE blocks？
HTTP 与 WebSocket 是否走同一 CA helper？
```

不要用“接受所有证书”掩盖问题。

## 119. 示例五：SSE 每隔一段时间断开

收集：

- 最后收到的 event kind；
- 是否见过 `response.created`；
- 是否见过 `response.completed`；
- 每次 event 间隔；
- provider idle timeout；
- proxy/load balancer 的 idle connection timeout；
- upstream request ID；
- 断开是 EOF、parse error 还是 timeout。

## 120. 示例六：429 后不断重试

检查：

- provider 是否启用 `retry_429`；
- `max_attempts` 的实际循环含义；
- base delay 与 jitter；
- server 是否给 retry delay；
- 多个 thread 是否共同造成 retry storm；
- 总 turn deadline 和取消是否仍生效。

Retry 是压力控制的一部分，不应把 rate limit 变成更猛烈的流量。

## 121. 示例七：GET 成功，POST 被拒

若工具经过 managed proxy 的 Limited mode：

```text
GET/HEAD/OPTIONS 可允许
POST/PUT/PATCH/DELETE 被阻止
```

这通常是 method policy 正常工作，不是 server bug。需要更改权限策略或获得审批，而不是换域名绕过。

## 122. 网络日志应该记录什么

适合结构化记录：

```text
route class
method
redacted origin/path class
status
attempt
connect/request/idle phase
elapsed
is_connect/is_timeout/is_body
proxy route kind（不含 credential）
request/trace ID
stream last event kind
policy decision/reason
```

## 123. 网络日志不应记录什么

谨慎处理：

- Authorization；
- Cookie；
- Proxy-Authorization；
- signed URL query；
- raw proxy URL；
- request body 中的源代码/secret；
- private host/path；
- certificate private key。

当前 `OutboundProxyRoute::Debug` 会把 proxy URL 与 `no_proxy` 显示为 `<redacted>`。

## 124. 网络测试为什么要分层

```text
纯函数测试
  URL/origin、NO_PROXY、redirect header、retry classification

Hermetic local integration
  本地 HTTP/HTTPS/WS server、proxy、custom CA、timeout

Platform test
  macOS/Windows system proxy/PAC API

Remote executor test
  host 与 target 不同网络、proxy/CA/env 传播
```

只测一个公网 URL 会慢、不稳定且难定位。

## 125. Custom CA 测试为何使用子进程

环境变量和平台 proxy discovery 会污染测试进程。当前 custom CA 说明将测试拆成：

- 模块 unit tests 验证 env selection；
- subprocess integration tests 清理继承 env；
- local HTTPS server 验证真实 TLS handshake；
- test-only client 禁用 proxy autodetection 保持 hermetic。

## 126. Redirect 测试应验证什么

至少包括：

- 每一跳重新 route resolution；
- 10 次上限；
- relative Location；
- POST → GET body removal；
- 307/308 replayability；
- 跨 origin Authorization/Cookie removal；
- HTTPS → HTTP 不带 Referer；
- route change 移除 Proxy-Authorization；
- 所有 hops 共用一个 deadline。

## 127. Stream 测试应验证什么

- chunk 任意切分仍正确组成 event；
- 多 event 同 chunk；
- 未知 event 可忽略；
- malformed JSON 的既定行为；
- response.failed 语义映射；
- EOF before completed；
- idle timeout；
- downstream receiver Drop 后 parser 退出；
- bounded channel 背压；
- completed 后不等待额外 EOF。

## 128. WebSocket 测试应验证什么

- direct/proxy/custom CA；
- IPv4/IPv6 address fallback；
- HTTP 101 与非成功 handshake；
- Ping → Pong；
- Close reason；
- send error 终止 pump；
- idle timeout；
- failed connection 不被继续复用；
- immediate-close probe；
- connection limit 与 previous-response error mapping。

## 129. 审查网络代码的清单

```text
这是产品流量还是 child tool 流量？
URL、scheme、host、port 与 origin 是否解析正确？
是否通过 HttpClientFactory/RouteAwareClientPool？
proxy policy 是 explicit 还是 accidental default？
system/PAC lookup 是否阻塞 async worker？
route/cache key 是否泄漏 raw URL？
cache 与 client pool 是否有界？
redirect 每一跳是否重新选 route？
跨 origin 是否移除 auth/cookie？
route change 是否移除 proxy auth？
HTTPS→HTTP 是否避免 Referer 泄漏？
HTTP 与 WSS 是否共享 custom CA policy？
是否有人绕过验证使用 accept-invalid-certs？
connect、request、idle timeout 是否分开？
redirect/retry 是否共享总 deadline？
retry 是否只包住安全的阶段？
mid-stream 断线是否需要状态恢复而非简单重放？
SSE/WS 是否要求明确 completed 终态？
channel 与 retained events 是否有界？
日志是否记录 category/ID 而非 credential？
managed network 是否检查 domain、method 与 resolved IP？
```

## 130. 理解检查

### 问题 1

为什么模型回复正常，不代表 `curl` 一定能访问互联网？

<details><summary>参考答案</summary>

模型 API 是 Codex 产品进程自己的网络路径；curl 是 child tool 路径，可能受另一套 sandbox、managed proxy、allowlist 和审批策略限制。

</details>

### 问题 2

为什么 redirect 后必须重新解析 proxy route？

<details><summary>参考答案</summary>

PAC/system proxy 决策可能依赖完整 URL；新 URL 可能需要另一代理或 direct route。

</details>

### 问题 3

为什么 custom CA 不等于关闭 TLS 验证？

<details><summary>参考答案</summary>

Custom CA 是把明确证书加入信任链，hostname、有效期和签名仍需验证；关闭验证则接受无法证明身份的 server。

</details>

### 问题 4

为什么 connect timeout 和 SSE idle timeout 不能共用同一概念？

<details><summary>参考答案</summary>

前者限制 DNS/TCP/TLS 建连阶段；后者用于已建立长流后等待下一事件。长流可以持续很久但始终不 idle。

</details>

### 问题 5

为什么 HTTP retry wrapper 不能自动修复所有 SSE 中途断线？

<details><summary>参考答案</summary>

取得 stream 后 server 可能已生成部分 item/tool call。重发需 response identity、状态恢复与去重，否则可能重复或丢失事件。

</details>

### 问题 6

为什么 Limited mode 下 HTTPS CONNECT 需要 MITM 才能检查 method？

<details><summary>参考答案</summary>

普通 CONNECT 后 proxy 只看见加密 TLS bytes，不知道内部是 GET 还是 POST；终止 TLS 后才能检查 HTTP method。

</details>

### 问题 7

为什么 route cache key 使用 URL hash，而不是只用 hostname？

<details><summary>参考答案</summary>

PAC 可能按完整 URL 决策；hash 既保留 URL 级区分，又不在 cache key 中长期保存 raw query/credential。

</details>

### 问题 8

为什么 EOF 不能作为 Responses 成功终态？

<details><summary>参考答案</summary>

EOF 只表示 transport 关闭；只有 `response.completed` 才证明协议层响应完整。

</details>

## 131. 本章词汇表与代码名称翻译

| 代码或术语 | 中文理解 | 在本章中的作用 |
|---|---|---|
| URL | 统一资源定位符 | scheme、host、port、path、query 的完整目标说明 |
| Scheme | 协议方案 | `http/https/ws/wss`，决定默认端口和 TLS/upgrade 语义 |
| Hostname | 主机名 | DNS、TLS hostname 与 policy 判断使用的名字 |
| IP address | IP 地址 | 网络路由实际连接的地址 |
| Port | 端口 | 同一 host 上区分网络服务的数字入口 |
| Origin | 源 | scheme、host、effective port 的安全边界 |
| DNS / resolver | 域名系统/解析器 | 把 hostname 解析成一个或多个 IP |
| IPv4 / IPv6 | 第四/第六版 IP | 同一域名可能同时拥有的两种地址族 |
| Happy Eyeballs | 双栈竞速连接 | 交错尝试 IPv6/IPv4，减少坏地址族造成的等待 |
| TCP | 传输控制协议 | 提供有序可靠 byte stream |
| `TCP_NODELAY` | 禁用 Nagle | 降低小消息交互等待的 socket 选项 |
| Proxy | 代理 | 代表 client 连接外部目标的中间节点 |
| Forward proxy | 正向代理 | 站在 client 一侧控制 outbound traffic |
| Direct route | 直连路线 | 明确绕过 transport proxy discovery |
| Transport default | 传输默认路线 | 保留 reqwest/tungstenite 自身代理行为 |
| System proxy | 系统代理 | 操作系统或企业配置的全局/按 URL route |
| PAC | 代理自动配置 | 用脚本按 URL 返回 DIRECT/PROXY 决策 |
| WPAD | Web 代理自动发现 | 自动寻找代理配置的机制 |
| `HTTP_PROXY` | HTTP 代理变量 | 常用于 http/ws 目标的环境 proxy |
| `HTTPS_PROXY` | HTTPS 代理变量 | 常用于 https/wss 目标的环境 proxy |
| `ALL_PROXY` | 通用代理变量 | 特定协议变量缺失时的 fallback |
| `NO_PROXY` | 代理绕过变量 | 指定应 direct 的 host/port pattern |
| Route | 路线 | Direct、TransportDefault 或具体 Proxy |
| Route class | 路线类别 | Auth/API/WebSocket/Other 产品诊断分类 |
| `HttpClientFactory` | HTTP client 工厂 | 集中应用 outbound proxy 与 cookie policy |
| `RouteAwareClientPool` | 路线感知 client 池 | 每 URL 解析 route，并按 route 复用 client |
| Redirect | 重定向 | HTTP response 指示 client 请求另一个 URL |
| `Location` | 重定向目标 header | 给出下一跳 URL |
| Referer | 来源页面 header | 可能暴露前一 URL，需按 origin 安全裁剪 |
| TLS | 传输层安全协议 | 验证 server identity 并加密/保护传输 |
| TLS handshake | TLS 握手 | 协商参数、验证证书并建立 session keys |
| Certificate | 证书 | 把 public key、hostname 和签发关系绑定 |
| CA / root store | 证书机构/根信任库 | client 验证 certificate chain 的信任根 |
| Custom CA | 自定义根证书 | 企业 TLS inspection 等环境增加的明确 trust anchor |
| PEM | 文本证书封装 | Custom CA bundle 使用的常见编码形式 |
| MITM | 中间人式 TLS 终止 | Proxy 解密 HTTPS 以执行 method/hook policy 的高风险能力 |
| HTTP method | HTTP 方法 | GET/POST/PUT 等请求动作语义 |
| Header | 请求/响应头 | auth、content type、request ID、rate limit 等元数据 |
| Body | 消息体 | JSON、bytes 或持续 stream 内容 |
| Status code | 状态码 | HTTP 101/200/401/407/429/5xx 等结果 |
| HTTP 101 | 协议切换 | WebSocket handshake 成功的常见状态 |
| HTTP 407 | 代理要求认证 | Proxy auth 问题，不是 DNS 或 Codex approval |
| HTTP 429 | 请求过多 | Rate limit，可按明确 policy 退避重试 |
| `HttpTransport` | HTTP 传输抽象 | 分离完整 execute 与 streaming response |
| `TransportError` | 传输错误 | HTTP、timeout、network、build、retry-limit 分类 |
| Connect timeout | 建连超时 | 限制 DNS/TCP/TLS connection establishment |
| Request timeout | 请求超时 | 覆盖 route、连接、发送和等待响应的总预算 |
| Idle timeout | 空闲超时 | Stream 连续没有下一 event/message 的最大等待 |
| SSE | Server-Sent Events | HTTP body 上的 server→client 事件流格式 |
| Event framing | 事件分帧 | 从任意 byte chunks 识别完整 SSE events |
| Delta | 增量 | 流式输出的一小段新增内容 |
| `response.completed` | 响应完成事件 | Responses stream 的明确成功终态 |
| WebSocket | Web 套接字 | HTTP upgrade 后的双向 message connection |
| Frame / Message | 帧/消息 | WebSocket wire 单元与业务可见消息 |
| Ping / Pong | 探测/回应 | WebSocket 连接活性控制消息 |
| Close frame | 关闭帧 | 带 code/reason 的协议关闭消息 |
| Connection reuse | 连接复用 | 多个顺序请求共享已经建立的 connection |
| Pump task | 泵任务 | 同时推进 WebSocket send command 与 receive stream |
| Tunnel / CONNECT | 隧道/建立隧道 | 通过 proxy 转发后续 target TCP/TLS bytes |
| Retry policy | 重试策略 | 规定 429/5xx/transport、次数与 base delay |
| Backoff / jitter | 退避/抖动 | 延长并打散重试间隔 |
| Mid-stream failure | 流中途失败 | Headers 成功后、completed 之前断线或解析失败 |
| Allowlist / denylist | 允许/拒绝名单 | 限定工具可访问的 domain |
| Limited network mode | 受限网络模式 | 只允许 GET/HEAD/OPTIONS 等低副作用方法 |
| Managed proxy | 受管理代理 | 为 child traffic 强制 domain/method/IP/credential policy |
| Blocked request | 被拦请求 | 带 host、method、reason、source 的审计记录 |
| Redaction | 脱敏 | 从 URL、proxy、header、error 中移除 credential |
| Hermetic test | 封闭测试 | 只依赖测试声明的本地 server、CA 与 env |

## 132. 源码检查点

1. `codex-rs/http-client/src/client_builder.rs`
   - 看 factory 优先原则、connect timeout、redirect、logging 和 direct 例外。
2. `codex-rs/http-client/src/outbound_proxy.rs`
   - 看 proxy policy、system/env fallback、TTL cache、URL hash key 和 redacted Debug。
3. `codex-rs/http-client/src/route_aware_client_pool.rs`
   - 看 per-URL route、16-client 上限、总 deadline 与手动 redirect。
4. `codex-rs/http-client/src/route_aware_redirect.rs`
   - 看 method/body 转换、origin、敏感 header 与 Referer 规则。
5. `codex-rs/http-client/src/custom_ca.rs`
   - 看 CA env 优先级、PEM 解析、reqwest/rustls 共享策略和错误提示。
6. `codex-rs/http-client/src/client.rs`
   - 看 trace header、request diagnostics、disabled logging 和 timeout builder。
7. `codex-rs/http-client/src/transport.rs`
   - 看 execute/stream、status/body/error 映射和 byte stream。
8. `codex-rs/http-client/src/error.rs`
   - 看 `TransportError` 与 `StreamError` 分类。
9. `codex-rs/codex-client/src/retry.rs`
   - 看 429/5xx/transport 判定、attempt loop、指数 backoff 和 jitter。
10. `codex-rs/codex-api/src/provider.rs`
    - 看 URL、query、retry config、idle timeout 和 http→ws scheme 转换。
11. `codex-rs/codex-api/src/endpoint/session.rs`
    - 看 auth、request recreation、HTTP/stream retry 边界。
12. `codex-rs/codex-api/src/endpoint/responses.rs`
    - 看 Responses request encoding、headers、compression 与 SSE 入口。
13. `codex-rs/codex-api/src/sse/responses.rs`
    - 看 1600 channel、eventsource parser、idle timeout、failed/completed 与 EOF。
14. `codex-rs/websocket-client/src/lib.rs`
    - 看 proxy-aware connector、custom CA 和 transport-independent stream/sink。
15. `codex-rs/websocket-client/src/dialer.rs`
    - 看 DNS、250 ms Happy Eyeballs、proxy/TLS tunnel 与 target handshake。
16. `codex-rs/codex-api/src/endpoint/responses_websocket.rs`
    - 看 pump、Ping/Pong、exclusive stream、probe、idle timeout 和 error mapping。
17. `codex-rs/network-proxy/src/config.rs`
    - 看 Full/Limited、domain、SOCKS、MITM 与 loopback clamp。
18. `codex-rs/network-proxy/src/runtime.rs`
    - 看 2 秒 DNS lookup、blocked event cap、IP/domain policy 与 audit shape。
19. `codex-rs/core/src/config/network_proxy_spec.rs`
    - 看 managed constraints、permission profile、approval flow 与 proxy lifecycle。
20. `codex-rs/http-client/src/route_aware_client_pool_tests.rs`
    - 看 route reuse、timeout、redirect 与敏感 header 测试。
21. `codex-rs/websocket-client/src/dialer_tests.rs`
    - 看 address fallback、proxy、custom TLS 和连接行为。
22. `codex-rs/codex-api/src/sse/responses.rs` 的测试模块
    - 看 chunk framing、错误事件、EOF、completed 和各种 Responses event。

## 133. 一句话总结

> 一次 Codex 网络请求不是“调用 HTTP”这么简单，而是先确定发起者与授权边界，再依次完成 URL/origin、proxy route、DNS、TCP、TLS、HTTP 与 SSE/WebSocket 协议；可靠实现必须让 redirect 每跳重选路线、HTTP/WSS 共享企业 CA、不同 timeout 各管自己的阶段、stream 以 `response.completed` 为终态，并在任何日志、缓存、重试和 managed policy 中同时守住容量、凭据与副作用边界。
