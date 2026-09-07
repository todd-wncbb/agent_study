# 源码精读 10：SamplingClient 如何构建请求、动态认证、解析 SSE 并保住错误语义

> 本篇继续向 Sampler 的最底层下钻，逐段精读 `xai-grok-sampler::SamplingClient`：`SamplerConfig` 中哪些值在 Client 构造时冻结，哪些值在每次请求时重新解析；Base URL 与 Query Parameter 如何组合；Bearer、`x-api-key`、Extra Header、环境变量 Header、Tracing Header 和 `x-grok-*` Header 如何进入请求；共享 HTTP/2 Client 为什么不会串凭据；401 如何按真实上线路径归因；三种 API 的非流式与流式请求怎样序列化、校验状态、清理错误 Body 并把 Byte Stream 解码成 Typed SSE Event；Responses API 又如何在 SDK 类型之外补齐 xAI 特有 Tool、Cost、Context Token 和 Doom Loop 信号。
>
> 源码基线：`ed6d543`。源码变化后优先按本文列出的类型、函数和测试名重新定位。

---

## 1. 先给结论：`SamplingClient` 是一条有状态的请求装配流水线

它不是简单的：

```rust
reqwest::Client::post(url).json(body).send().await
```

真实链路是：

```text
SamplerConfig
   |
   | Client construction
   v
Frozen defaults + default headers + endpoint template + shared HTTP client
   |
   | every post()
   v
Live credential resolution + per-request header injection
   |
   v
Backend-specific headers + body serialization/patching
   |
   v
HTTP request build + safe diagnostics
   |
   v
HTTP status/header classification
   |
   +--> non-stream JSON response
   |
   +--> byte stream -> BOM strip -> SSE frames -> stream error probe
                                      -> backend typed events
```

这条流水线必须同时保证：

1. 请求语义正确。
2. 动态 Token 不会被旧快照覆盖。
3. 共享连接池不会共享请求 Header。
4. 错误信息可诊断但不泄露 HTML、Secret 或完整 Token。
5. SDK 尚未建模的 Wire Field 不会静默丢失。
6. 流中一个 Transport Error 不会变成无限 Poll Busy Loop。

---

## 2. 本篇主线文件

| 文件 | 责任 |
| --- | --- |
| `xai-grok-sampler/src/config.rs` | Client 与 Request 的全部传输配置 |
| `xai-grok-sampler/src/client.rs` | Endpoint、Header、Body、HTTP、SSE 与 Response 解析主线 |
| `xai-grok-sampler/src/shared_http.rs` | Process-wide HTTP/2 Client 与 HTTP/1.1 Fallback |
| `xai-grok-sampler/src/attribution.rs` | 401 归因接口与 Consumer 标识 |
| `xai-grok-sampler/src/doom_loop.rs` | Responses SSE 的非标准信号旁路 |
| `xai-grok-sampler/src/sampling_log.rs` | Sampling Request Span 与 Auth 摘要 |
| `xai-grok-sampling-types/src/error.rs` | Error Body 清理、Stream Error 与 Retry 性质 |
| `xai-grok-sampling-types/src/conversation/*.rs` | 三种 Backend Body 的初始转换 |
| `xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs` | Session 怎样重建完整 `SamplerConfig` |
| `xai-grok-shell/src/agent/mvp_agent/mod.rs` | Proxy Header 在上层怎样注入 |
| `xai-grok-sampler/tests/request_query_and_headers.rs` | Query/Header 真实 Wire 测试 |
| `xai-grok-sampler/tests/shared_http_wire.rs` | 连接复用与 Header 隔离测试 |
| `xai-grok-sampler/tests/shared_http_kill_switch.rs` | 共享 Client Kill Switch 测试 |
| `xai-grok-sampler/tests/cf_edge_error_message.rs` | HTML Edge Error 清理测试 |

---

## 3. 先分清三类配置生命周期

`SamplerConfig` 中的字段不是在同一时刻生效。

### 3.1 Client 构造时冻结

- Base URL。
- Query Params。
- 默认 Model、Temperature、Top P、Max Tokens。
- API Backend 与 Auth Scheme。
- Static API Key 初始 Header。
- Extra Headers。
- Environment-backed Headers 的当前值。
- User-Agent、Client Identifier 等 Identity Header。
- 是否使用 HTTP/1.1。
- Stream Tool Calls 与 Doom Loop Policy。

### 3.2 每次 `post()` 动态解析

- `BearerResolver::current_bearer()`。
- `HeaderInjector::inject()`，例如当前 Traceparent。

### 3.3 不由 `SamplingClient` 执行

- Context Window Enforcement。
- Retry Loop。
- Session Auth Refresh。
- URL 是否属于 First-party/Proxy 的信任判断。
- Conversation 的 Prompt Pruning。

这三类生命周期是理解“为什么改了配置却没有影响当前 Client”的关键。

---

## 4. 为什么 `SamplerConfig` 不复用 Shell 的 `SamplingConfig`

源码明确让 Sampler crate 拥有自己的 `SamplerConfig`，避免依赖 Shell-specific 类型。

Shell 中的 Chat State 保存模型采样事实；真正发请求前，Session 再把它和：

- Credentials。
- Auth Manager。
- Deployment/User Identity。
- Proxy Headers。
- Compaction Headers。
- Attribution Callback。
- Live Bearer Resolver。
- Trace Header Injector。

组合成完整 Sampler Config。

因此 `SamplerConfig` 是传输边界 DTO，不是整个 Agent 配置树。

---

## 5. `SamplerConfig` 字段分组

### 模型请求默认值

- `model`
- `max_completion_tokens`
- `temperature`
- `top_p`
- `reasoning_effort`

### 协议与地址

- `base_url`
- `api_backend`
- `query_params`
- `stream_tool_calls`

### 认证与 Header

- `api_key`
- `auth_scheme`
- `extra_headers`
- `env_http_headers`
- `bearer_resolver`
- `header_injector`

### Client Identity

- `origin_client`
- `client_identifier`
- `deployment_id`
- `user_id`
- `client_version`

### 传输与恢复

- `force_http1`
- `max_retries`
- `idle_timeout_secs`
- `doom_loop_recovery`

### 上层能力信息

- `context_window`
- `supports_backend_search`
- `compactions_remaining`
- `compaction_at_tokens`

不是所有字段都直接在 `client.rs` 被读取；部分字段由 Session 构造 Extra Header 或由 Actor Retry Task 使用。

---

## 6. Serde Skip 字段为什么必须重接

以下字段持有 Trait Object，不能直接序列化：

- `attribution_callback`
- `bearer_resolver`
- `header_injector`

它们用 `#[serde(skip)]`。

因此：

```text
SamplerConfig -> JSON -> SamplerConfig
```

会丢失这三项运行期能力。

从磁盘反序列化 Config 后，如果调用者不重新附加：

- 401 不再产生 Attribution。
- Token 不再 Live Resolve。
- Traceparent 不再按请求注入。

这不是 Serde Bug，而是进程内能力不能持久化的必然边界。

---

## 7. `SamplingClient` 自己保存什么

```text
http                 reqwest::Client
default_headers      construction-time HeaderMap
base_url             diagnostics
defaults             model/backend/auth/body defaults
attribution_callback optional 401 hook
bearer_resolver       optional per-request auth source
header_injector       optional per-request header hook
endpoint              precomputed URL template
```

它可廉价 Clone，因为 `reqwest::Client` 内部使用共享状态；HeaderMap 和小型配置则按值 Clone。

---

## 8. `SamplingClient::new` 不做网络请求

构造函数只做：

1. 生成默认 Headers。
2. 校验可静态发现的 Header 错误。
3. 获取或构建共享 Reqwest Client。
4. 保存 Defaults。
5. 预计算 Endpoint Template。

DNS、TCP、TLS 与 API 调用都要等真正 `send/execute` 时才发生。

因此 `new()` 成功只证明本地配置能构造成 Client，不证明 Endpoint 可达或 Credential 有效。

---

## 9. 默认 Header 的初始顺序

`SamplingClient::new` 大致按以下顺序向同一个 `HeaderMap` 写入：

```text
Content-Type: application/json
static api_key according to AuthScheme
extra_headers
resolved env_http_headers
x-grok-client-version
x-grok-deployment-id
x-grok-user-id
x-grok-client-identifier
User-Agent
```

`HeaderMap::insert` 对相同规范化名称执行替换，因此后写层通常覆盖前写层。

这意味着 Extra/Header 配置不是完全等价的来源；顺序会决定相同 Header Name 的最终值。

---

## 10. Header Name 为什么天然大小写不敏感

HTTP Header Name 不区分大小写。

`HeaderName::try_from` 和 `HeaderMap` 使用规范化 Key，所以：

```text
Authorization
authorization
AUTHORIZATION
```

会落到同一个逻辑 Header。

但是上层 `IndexMap<String, String>` 在进入 HeaderMap 前仍是普通 String Map；全局与模型级 Header 合并时必须自己做 Case-insensitive Conflict 处理，不能等到最后才假设没有重复意图。

---

## 11. 两种 Auth Scheme

`AuthScheme` 有：

```text
Bearer
XApiKey
```

Static `api_key` 在构造时变成：

| Scheme | Wire Header |
| --- | --- |
| Bearer | `Authorization: Bearer <key>` |
| XApiKey | `x-api-key: <key>` |

Backend 与 Auth Scheme 是两个独立维度。

例如 Messages Backend 可以使用传统 Anthropic `x-api-key`，也可以通过兼容网关使用 Bearer。

---

## 12. 无效 Static API Key 怎样失败

若 Key 不能转换成合法 `HeaderValue`：

- 构造函数返回 `SamplingError::Auth`。
- 不会创建一个缺失或截断认证 Header 的 Client。

Header Value 会拒绝换行等危险字节，所以这一校验同时防止 Header Injection。

日志目前在 Debug Event 中记录 `%api_key`；从安全审查角度，这是一个值得持续关注的敏感日志点，不能因为请求 Header 日志会 Redact 就认为所有路径都不会记录 Key。

---

## 13. `extra_headers` 的职责边界

Sampler 不根据 URL 自行猜测：

- 是否是 cli-chat-proxy。
- 是否需要 `X-XAI-Token-Auth`。
- 是否是 Staging。
- 是否需要额外访问 Header。

这些判断在 Shell 上层完成，再把结果写入 `extra_headers`。

这样 Sampler 保持 URL-agnostic，避免底层 HTTP Client 偷偷拥有产品环境知识。

---

## 14. 为什么 URL-derived Header 在上层生成

`inject_url_derived_headers` / `inject_proxy_headers` 掌握：

- First-party Proxy URL 判定。
- Alpha/Staging Key。
- Client Version Fallback。
- Process Client Identifier。

这些规则与部署环境和信任策略相关，不是通用协议转换。

Sampler 只执行传入的 Header，不重新推断，避免出现上层认为是第三方 Endpoint、底层却擅自发送 First-party Secret 的风险。

---

## 15. `env_http_headers` 为什么保存“变量名”而不是 Secret

配置格式是：

```text
header name -> environment variable name
```

例如：

```text
x-tenant-token -> TENANT_TOKEN_VAR
```

Sampler 构造时才调用 `getenv` 取得值。

这样：

- Config 文件可以持久化映射。
- Secret Value 不进入 Config 序列化结果。
- 模型 Provider 配置可移植到不同环境。

---

## 16. Environment Header 的过滤规则

`apply_env_http_headers` 对每个映射：

1. 环境变量不存在 -> 跳过。
2. Trim 前后空白。
3. Trim 后为空 -> 跳过。
4. Header Name 或 Value 非法 -> Warning 并跳过。
5. 合法 -> `headers.insert`。

Env Header 的单项错误不会让整个 Client 构造失败，这与显式 `extra_headers` 的 Invalid Configuration 行为不同。

---

## 17. Env Header 为什么能覆盖 Extra Header

`extra_headers` 先写，Env Header 后写。

相同 Header Name 时，已解析的环境值覆盖静态值。

这允许 Config 中提供非敏感默认或占位，而运行环境注入真实 Secret。

测试 `apply_env_http_headers_resolves_trims_skips_and_overrides` 固定了这一顺序。

---

## 18. Identity Header 的后置覆盖

Env Header 后，Client 还会写：

- `x-grok-client-version`
- `x-grok-deployment-id`
- `x-grok-user-id`
- `x-grok-client-identifier`
- `User-Agent`

因此如果 Extra/Env Header 使用相同名字，后置的显式 Config Field 可能覆盖它。

这是合理的单一字段权威设计，但维护者新增 Header 来源时必须把顺序写进测试，否则覆盖关系很容易悄悄改变。

---

## 19. User-Agent 怎样构造

User-Agent 包含：

- Origin Product 与可选 Version。
- Grok Shell Agent Product 与 Version。
- OS。
- Architecture。

若 Origin 本身就是同版本 Grok Shell，不重复写两遍 Product。

例如心智模型：

```text
third-party-origin/1.2 grok-shell/X.Y (macos; aarch64)
```

如果没有 Origin，也始终生成 Grok Shell 自身 User-Agent。

回归测试明确防止 Sampling Request 再次丢失 User-Agent。

---

## 20. Platform 名称归一化

源码把：

- macOS 写成 `macos`。
- Windows 写成 `windows`。
- Rust `arm64` 归一为 `aarch64`。
- 其他 OS/Arch 原样使用编译期常量。

这使 User-Agent 的架构命名与常见服务端分析维度保持一致。

---

## 21. Endpoint 为什么预计算成 Template

每个 Client 会重复调用同一个 Base URL 的不同路径：

```text
chat/completions
responses
messages
```

`EndpointTemplate::new` 在构造时处理 Query Folding，每次请求只调用 `url_for_path(path)`。

这避免每个 Attempt 都重新 Parse、合并和 Percent Encode Query。

---

## 22. `EndpointTemplate::Plain`

条件：

- 没有配置 Query Params。
- Base URL 自身也不含 `?`。

行为：

```text
trim trailing slash from base
trim leading slash from path
format "{base}/{path}"
```

它是常见路径的 Fast Path。

---

## 23. 为什么 Base URL 自带 Query 时不能直接拼路径

错误拼法：

```text
https://gateway/v1?api-version=x/responses
```

这里 `/responses` 被放进 Query Value，而不是 URL Path。

正确结果应是：

```text
https://gateway/v1/responses?api-version=x
```

因此只要 Base 含 Query，即使没有额外 `query_params`，也必须走 Parse/Fold 路径。

---

## 24. `EndpointTemplate::WithQuery`

它保存：

```text
prefix = base without query and trailing slash
suffix = ? + folded percent-encoded query
```

最终：

```text
{prefix}/{path}{suffix}
```

Path 永远位于 Query 之前。

---

## 25. Query 的覆盖规则

构造时：

1. 读取 Base URL 原有 Query Pairs。
2. 找出 Configured Query Keys。
3. 丢掉 Base 中同名 Key。
4. 保留其他 Base Query。
5. 追加 Configured Query。

因此 Configured Key 获胜，而且不会出现同名重复。

Wire Test 固定了：

- `api-version=old` 被新值替换。
- 无关 `keep=1` 保留。
- `tenant=a b` 正确编码。

---

## 26. Base URL 无法解析时怎样降级

如果需要 Query Folding，但 `reqwest::Url::parse` 失败：

- 记录 Warning。
- 返回 `Plain(base)`。
- 不折叠 Configured Query。

之后真正构建 Request 时仍可能因 URL 非法失败。

这种 Best-effort 设计让错误最终由统一 Reqwest URL 构建路径表达，但也意味着 Query Params 在非法 Base 上不会被保留。

---

## 27. 共享 HTTP Client 为什么安全

Process-wide `reqwest::Client` 只保存连接池和传输配置。

它不保存模型 Config 的：

- Authorization。
- Extra Headers。
- Base URL。
- User-Agent。

这些都在 `SamplingClient::post()` 的 Request Builder 上逐请求应用。

所以两个 SamplingClient 可以复用同一 TCP/H2 Connection，而 Header 仍然隔离。

Wire Test 用 `token-a/header-a` 与 `token-b/header-b` 证明没有串线。

---

## 28. Shared HTTP/2 的默认参数

`build_http_client` 默认：

- 每 Host 最多 2 个 Idle Connection。
- Idle Pool Timeout 90 秒。
- Connect Timeout 10 秒。
- TCP NoDelay。
- HTTP/2 Keepalive Interval 15 秒。
- HTTP/2 Keepalive Timeout 5 秒。
- Idle 时也发送 Keepalive。
- 加载可选 Extra CA Root。

其中前几项可由环境变量调整，并在 Shared Client 第一次构造时锁定。

---

## 29. `OnceLock` 的成功缓存与失败不缓存

Shared Client Helper：

```text
cell already initialized
  -> clone cached client

not initialized
  -> build
     success -> get_or_init and clone
     failure -> return Err; cell stays empty
```

构建失败不会永久毒化进程；下一次调用可以再次尝试。

并发构造时，竞争失败者构建出的 Client 会被丢弃，最终共享 OnceLock 中的胜者。

---

## 30. Shared Client Kill Switch

环境变量：

```text
GROK_SAMPLER_SHARED_CLIENT=0
GROK_SAMPLER_SHARED_CLIENT=false
```

会恢复“每个 SamplingClient 新建 Reqwest Client”的旧行为。

Kill Switch 使用 OnceLock 在进程内只解析一次，避免运行中环境变化让部分 Client 共享、部分不共享。

独立 Integration Test Binary 保证测试修改环境变量时不会污染其他测试进程。

---

## 31. HTTP/1.1 Fallback Client 为什么禁用连接池

`build_http_client_http1`：

- `http1_only()`。
- `pool_max_idle_per_host(0)`。
- `pool_idle_timeout(0)`。

它用于第一次 Transport Retry 逃离可能损坏的 HTTP/2 Pool。

即使这个 Client 对象本身也被 OnceLock 共享，由于不保留 Idle Connection，每次请求仍会开新连接；对象共享不等于连接复用。

---

## 32. Extra CA 在哪里接入

两种 Client Builder 都经过：

```text
xai_grok_extra_ca::with_extra_root_certificates(...)
```

所以企业代理或私有 CA 支持不是某个 Backend 特例，而是所有 Sampling Transport 的共同 TLS 层能力。

---

## 33. `post()` 是每次请求的真正动态边界

`post()` 做：

1. Clone `default_headers`。
2. 若有 Bearer Resolver，重建 Auth Header。
3. 记录安全受限的 Auth Diagnostics。
4. 从最终 Auth Header 截取 Credential Tail。
5. 调用 Header Injector。
6. 构造 `self.http.post(url).headers(headers)`。
7. 返回 `SentRequest`。

所有六种实际 API 调用都必须经过它，才能保持认证与 Attribution 语义一致。

---

## 34. `BearerResolver` 为什么是同步 Trait

接口只有：

```rust
fn current_bearer(&self) -> Option<String>;
```

它必须是 Cheap Sync Read。

真正可能异步刷新的 Auth Manager 在上层执行刷新；Resolver 只读取当前 Wire-valid Credential Snapshot。

如果 `post()` 内部再做异步 Refresh：

- Request Builder 构造会变成 Await Point。
- 并发与锁顺序更复杂。
- Sampler 开始拥有 Session Auth Policy。

当前设计保持“上层刷新、底层读取”。

---

## 35. 配置 Resolver 后为什么必须删除两个 Auth Header

`post()` 一旦看到 Resolver：

```text
remove Authorization
remove x-api-key
```

然后只按 `AuthScheme` 写回 Resolver 当前值。

删除两个而不是只删当前 Scheme，是为了防止：

- Extra Header 留下另一种 Auth。
- 静态 API Key 与动态 Bearer 同时发送。
- 模型切换后遗留错误 Scheme。

最终请求只应有当前权威认证源选择的一种 Header。

---

## 36. Resolver 是唯一认证源

如果 Resolver 存在，它不会在返回 `None` 时回退到 `config.api_key`。

行为是：

```text
resolver returns Some(fresh)
  -> send fresh credential

resolver returns None
  -> send no auth header
```

这是一种 Fail-closed 设计。

Hard-expired Session Token 不应该因为 Resolver 暂时没有 Wire-valid Token，就悄悄发送构造时留下的 stale seed key。

---

## 37. 为什么不能用 `RequestBuilder::header` 追加新 Token

历史回归问题是：

1. Default Headers 已有 `Authorization: Bearer stale`。
2. Resolver 又用 Builder `.header(AUTHORIZATION, fresh)`。
3. Reqwest 追加第二个 Authorization。
4. Proxy 收到重复认证 Header 并返回 400。

当前实现先在独立 HeaderMap 中 Remove，再 Insert，然后一次性 `.headers(headers)`。

测试明确断言 Wire 上只有一个 Authorization。

---

## 38. 动态 Messages 认证

Resolver 的返回值没有预加 Scheme。

按 `AuthScheme`：

- Bearer -> `Authorization: Bearer <fresh>`。
- XApiKey -> `x-api-key: <fresh>`。

测试覆盖了 Messages + Bearer 和 Messages + Anthropic API Key 两种组合。

---

## 39. 动态 Credential 无法转成 Header 时怎样处理

`post()` 对 Resolver 返回值执行 `HeaderValue::from_str`。

若非法：

- 不插入 Auth Header。
- 方法仍构造并发送请求。
- `sent_bearer=None`。

这与 Static API Key 在 `new()` 时直接返回 Auth Error 不同。

动态值的非法情况当前表现为“无凭据请求，通常收到 401”，而不是本地同步失败。这是理解 Attribution `Missing` 的一个边界来源。

---

## 40. `SentRequest` 为什么把 Builder 和 Credential Capture 绑在一起

```text
SentRequest {
  builder,
  sent_bearer
}
```

它表达一个不变量：

> 之后如果这个 Builder 得到 401，只能使用它构造时捕获的 Credential 信息做归因。

如果 Builder 和 Capture 分开返回，很容易错误地在响应路径重新查询 Resolver。

---

## 41. 为什么只截取 Credential 尾片

`sent_fragment_from_headers`：

- Bearer 路径去掉 `Bearer ` 前缀。
- XApiKey 直接取 Header Value。
- 然后只保留固定长度 Tail。

完整 Credential 不跨过 SamplingClient 的 Attribution 边界。

尾片足以和 Auth Manager 当前 Token 做诊断关联，又显著降低日志或 Callback 泄露完整 Secret 的风险。

---

## 42. Build-time Capture 关闭什么竞态

时序：

```text
request built with token A
request sent
server returns 401
auth recovery rotates resolver to token B
attribution callback runs
```

如果 Callback 时再读 Resolver，会错误报告 B 被拒绝。

当前 `post()` 在 Request Build 时捕获 A 的 Tail，所以无论 Resolver 后来怎样变化，401 都归因给真正上线路径。

测试 `post_capture_is_immune_to_resolver_rotation_after_build` 固定了这一不变量。

---

## 43. `current_sent_bearer_suffix` 只能用于请求开始诊断

它读取“现在下一次请求可能携带什么”：

- 有 Resolver -> Live Read。
- 无 Resolver -> Default Header Snapshot。

它用于 `auth_info()` 和 Sampling Span。

它不能用于已完成请求的 401 归因，因为那会发生前述 Token Rotation Race。

---

## 44. `SentCredential` 的三种语义

401 转成 `SamplingError::Auth` 时：

| Capture | Variant | 含义 |
| --- | --- | --- |
| Some tail | `Sent` | 请求确实携带某个 Credential，并被拒绝 |
| None | `Missing` | 请求没有认证 Header |
| 无法从 Wire Path 确定 | `Unknown` | 合成或旧路径，保守处理 |

这让上层认证预算区分“凭据被拒绝”和“根本没发凭据”。

---

## 45. 401 Attribution Callback 的六个 Consumer

每种 Backend 都有 Streaming 与 Non-streaming Site：

- `chat_completions_stream`
- `chat_completions`
- `responses_stream`
- `responses`
- `messages_stream`
- `messages`

Callback 可以按 Endpoint Path 统计 401，而不把所有 Sampling Auth Failure 混成一个桶。

调用点位于看到真实 HTTP Status 的最低层；更高层不应重复 Emit。

---

## 46. Attribution Callback 为什么必须便宜且不阻塞

Trait 方法是同步的，并直接运行在用户可见的 401 Error Path 上。

它不应：

- 做网络请求。
- 等待长锁。
- 执行 Token Refresh。
- 写大文件。

合理实现是把小型结构化事件投递到另一个 Channel 或 Telemetry Buffer。

---

## 47. Header Injector 的真实用途

Session 构造一个 `TraceContextInjector`，每次 `post()` 调用时读取当前 Trace Context 并写：

```text
traceparent: ...
```

它不能在 Client 构造时冻结，因为同一个 SamplingClient 的不同请求可能属于不同 Span。

这和 Live Bearer 的共同点是：值在每次 Request Build 时才正确。

---

## 48. Header Injector 的调用顺序与信任假设

当前顺序是：

```text
resolve auth headers
capture sent credential suffix
call header_injector
build request
```

官方使用者只注入 Traceparent。

但 Trait 类型本身可以修改任意 Header；如果某个自定义 Injector 在 Capture 后改了 Authorization，Attribution 就不再准确。

因此这里存在隐含契约：

> HeaderInjector 应注入追踪类 Header，不应改认证 Header。

若未来允许第三方 Injector，应在类型或代码层限制这一能力。

---

## 49. Grok Request Headers 在哪里加入

`post()` 只生成公共 Headers。

每个 Backend 方法再构造 `GrokRequestHeaders`：

- `x-grok-conv-id`
- `x-grok-req-id`
- `x-grok-model-override`
- `x-grok-session-id`
- `x-grok-turn-idx`
- `x-grok-agent-id`
- `x-grok-deployment-id`
- `x-grok-user-id`

然后调用 `apply(builder)`。

这些值来自具体 Request Wrapper，比 Client Defaults 更接近本次逻辑请求。

---

## 50. Required-looking Header 为什么允许空字符串

Conv ID、Req ID、Model ID、Session ID、Agent ID 的 `GrokRequestHeaders` 字段是 `&str`，调用者缺失时传 `unwrap_or_default()`。

因此 Header 仍会存在，但 Value 可以为空。

Deployment、User 和 Turn Index 使用 `Option`，其中 Deployment/User 还过滤空字符串。

这不是统一的“None 就不发”策略；每个 Header 的 Wire Contract 不同。

---

## 51. Deployment/User Header 为什么写两次来源

它们可能已经在 Client Default Headers 中由 Config 写入，同时 Request Wrapper 也可能携带并通过 `GrokRequestHeaders` 添加。

正常 Session 构造会让两处值一致。

维护时应注意 Reqwest Builder 的 `.header` 具有追加语义风险；认证路径已经有重复 Header 回归测试，而这些身份 Header 目前依赖调用方一致性。若未来来源可能分叉，应补 Wire-level 单值断言。

---

## 52. `Accept: text/event-stream` 只在 Streaming 路径加入

非流式请求只需要 JSON Content Type。

流式请求在 Grok Headers 后添加：

```text
Accept: text/event-stream
```

Responses Doom Loop 开启时还添加：

```text
x-grok-doom-loop-check: true
```

Header 的 Presence 是 Opt-in，服务端忽略其具体值。

---

## 53. Trace Object 与 Trace Header 不是同一东西

`ConversationRequest` / Wrapper 中存在 process-local `trace` Trait Object。

在 Responses/Messages 真正序列化前：

```text
request.trace.take()
```

它不会作为 JSON 发给 Provider。

分布式追踪上下文则由 `HeaderInjector` 写入 `traceparent`。

因此：

- Trace Object 是进程内协作载体。
- Traceparent 是 Wire Header。

不要因为 Wrapper 有 `trace` 字段就认为它会进入 Request Body。

---

## 54. 三种 API 的默认值并不完全相同

### Chat Completions

缺失时填：

- Model。
- `max_tokens`。
- Temperature。
- Top P。

### Responses

除相同采样字段外还：

- `store=false`，保证 ZDR 默认。
- Include `ReasoningEncryptedContent`。

### Messages

- 空 Model String 才填 Model。
- `max_tokens == 0` 时填 Config，若 Config 也缺失则 128,000。
- 填 Temperature 与 Top P。

协议字段的 Option/Zero 表达不同，所以不能共用一个泛型 Defaults Function。

---

## 55. Responses 为什么强制 `store=false`

Responses API 的默认 Store 行为可能破坏 Zero Data Retention 预期。

只有 Request 未指定时，Sampler 才设置 `Some(false)`；显式值不会被覆盖。

这是一项安全默认，而不是模型采样参数默认。

---

## 56. Responses 为什么请求 Encrypted Reasoning

`apply_response_defaults` 确保 Include List 中存在：

```text
ReasoningEncryptedContent
```

后续 Turn 要重放模型 Reasoning 上下文时，需要服务端返回的 Encrypted Content。

若只保存可读 Summary，跨 Turn 的 Reasoning Continuity 可能下降，或 Provider 不接受缺失的加密状态。

---

## 57. Chat Streaming Body 怎样避免二次序列化

`StreamingChatRequest<'a>`：

```rust
#[serde(flatten)]
inner: &'a ChatCompletionRequest,
stream: true,
stream_options: { include_usage: true }
```

Flatten 后，Inner 字段直接位于顶层。

旧方案可能先序列化成 `Value`、插入 Streaming 字段、再序列化成 Bytes；当前方案单次序列化，减少分配和意外 Shape Drift。

---

## 58. 为什么 Chat Stream 请求 `include_usage=true`

普通 Text Chunk 不一定带完整 Usage。

请求 `stream_options.include_usage` 让 Provider 在尾部发送 Token Usage Chunk，Layer-2 Parser 才能建立最终 `TokenUsage` 与 Cost。

因此 `[DONE]` 前的尾部 Metadata Chunk 也属于有价值的 Stream 数据。

---

## 59. Responses Body 为什么必须先转 `serde_json::Value`

Responses 路径有 SDK 类型无法表达的 Wire Field：

- `stream_tool_calls`。
- xAI-specific X Search Tool Entries。
- Reasoning Text 的缺失 Discriminator。

所以流程是：

```text
typed CreateResponse
  -> serde_json::Value
  -> insert/extend xAI fields
  -> patch reasoning content type
  -> request.json(value)
```

这是一层受控的 Raw JSON Escape Hatch。

---

## 60. `stream_tool_calls` 怎样注入

仅 Streaming Responses 路径在配置开启时写：

```json
"stream_tool_calls": true
```

它不是标准 SDK 字段，所以直接写入 JSON Object。

Chat 和 Messages 的 Tool Delta 行为由各自协议决定，不使用这个同名扩展。

---

## 61. X Search Tool 怎样注入

Canonical 转换阶段先收集 `extra_tool_entries`。

Streaming Client 序列化主体后：

- Body 已有 `tools` Array -> Extend。
- 没有 -> 新建 Array。

这样标准 Web Search/Function Tool 走 SDK Enum，X Search 走 Raw JSON，但最终 Wire 上仍是同一个 Tools List。

---

## 62. Non-streaming Responses 的一个能力差异

`conversation_stream_responses` 显式提取并传递 `extra_tool_entries`。

`conversation_responses` 当前只把 `ConversationRequest` 转成 Typed `CreateResponse`，没有附加 `extra_tool_entries`；`create_response` 也不注入 X Search Raw Entries。

因此 xAI-specific Hosted X Search 的完整注入路径目前集中在 Streaming Responses。

此外，Non-streaming `create_response` 直接把成功 Body 反序列化为 `rs::Response`，不会经过 Streaming Decoder 的 `apply_terminal_event_overrides`。因此 `context_details` 驱动的 Live Context Total 修正与 `cost_in_usd_ticks` 临时桥接也是 Streaming Responses 路径的附加能力。

阅读“统一 Conversation API”时不能假设 Streaming 与 Non-streaming 除返回类型外完全等价。

---

## 63. Reasoning Text Discriminator Patch

SDK 的 `ReasoningTextContent` 缺少 Wire 要求的：

```json
"type": "reasoning_text"
```

Patch：

1. 找 Body `/input` Array。
2. 只处理 `type=reasoning` Item。
3. 遍历 `content`。
4. 若 Content Object 没有 `type`，补 `reasoning_text`。

已有类型不覆盖。

这把兼容补丁限定在准确的 JSON 层级，避免全局扫描误改普通 Text。

---

## 64. 每个 Responses Attempt 为什么新建 Doom Collector

`create_response_stream` 每次调用都根据 Policy 创建新 Collector。

请求重试会再次调用该方法，所以：

- 失败 Attempt 的 Signal 不会进入下一 Attempt。
- Collector Policy 与该 Request Header 同步。
- Header 未开启时没有 Per-event Peek Cost，除防御 Rollout Skew 的轻量识别外。

这是 Attempt Isolation 在 Layer 1 的具体体现。

---

## 65. Doom Header 与 Decoder Gate 为什么共用一个条件

只有 `defaults.doom_loop_recovery` 存在时：

- 创建 Collector。
- 发送 Opt-in Header。
- 解码器记录 Signal。

这样不会出现：

- Header 开了但没有 Collector。
- Collector 等待信号但服务端没被 Opt-in。

配置、Wire 和 Decode Behavior 由同一 Option 驱动。

---

## 66. Messages Body 为什么可以直接 Typed JSON

Messages 的 Request Type 已能表达当前需要的：

- System。
- Content Blocks。
- Thinking。
- Tools。
- Tool Choice。
- Output Config。
- Streaming Flag。

所以 `create_message[_stream]` 直接 `.json(&request.inner)`，没有 Responses 那种 Post-serialize Raw Patch。

复杂性主要已经在 `build_messages_request` 中完成。

---

## 67. Non-streaming HTTP 主线

三种非流式方法都遵循：

```text
apply defaults
extract request identifiers
drop process-local trace when applicable
build endpoint + common/live/grok headers
serialize JSON
send
capture status and response headers
read full body bytes

if non-success
  -> classify 401 or Api error
else
  -> deserialize typed response
```

Body 被完整读入内存，适合单个 JSON Response，不适合无限或大型 Stream。

---

## 68. Streaming HTTP 主线

Streaming 方法比 Non-streaming 多：

```text
build Request explicitly
log safe request metadata/headers
self.http.execute(request)
record status/success on tracing span

if success
  -> response.bytes_stream()
  -> strip possible BOM on first byte chunk
  -> eventsource()
  -> scan state machine
  -> boxed typed stream
```

函数返回时只完成了 HTTP Response Header 阶段；内容仍在后续 Poll Stream 时到达。

---

## 69. `send()` 与 `build + execute()` 的差异

Non-streaming 使用 RequestBuilder `send()`。

Streaming 先 `build()` 再 `self.http.execute()`，因为它需要在发送前：

- 记录最终 URL 与 Method。
- 遍历最终 Headers 做 Redacted Debug Log。
- 明确把 Request Build Error 映射为 `SamplingError::Http`。

这也让“真正上线路径”的可观察对象更明确。

---

## 70. Request Header 日志怎样脱敏

Header Name 转小写后，只要包含：

- `authorization`
- `api-key`
- `apikey`
- `token`
- `secret`

Value 就显示 `[REDACTED]`。

其他非 UTF-8 Header 显示 `[non-utf8]`。

这是基于名字的启发式，不是完整数据流证明；自定义敏感 Header 若名字不含这些词，仍可能被 Debug Log 输出。

---

## 71. Auth Diagnostics 为什么只记录 Prefix/Tail

`client_post` Log 记录：

- 是否有 Resolver。
- 是否存在 Authorization / x-api-key。
- 截断的 Header Prefix。

Sampling Request Span 的 `auth_info` 则保存 Auth Type 与 Credential Tail。

这两种日志服务不同诊断维度，但都不应被当作可恢复 Secret 的存储。

安全审查时仍应确认截断片段长度与日志访问权限满足组织策略。

---

## 72. Transport Failure 为什么额外写 Span

如果 `execute()` 在收到任何 HTTP Response 前失败：

- 没有 Status Code 可记录。
- 若不手动写字段，Instrumentation Span 结束时 `success/error` 都为空。

`record_stream_request_failure` 因此记录：

```text
success=false
error=<reqwest error>
```

否则网络 outage 在 Error-rate Metrics 中可能看起来像“没有失败请求”。

---

## 73. Response Header 解析

Sampler 读取：

| Header | 结果 |
| --- | --- |
| `Retry-After` | Retry 等待秒数 |
| `x-should-retry` | 服务端 Retry Veto Hint |
| `x-grok-context-window` | Model Context Window |
| `x-grok-max-completion-tokens` | Model Output Limit |
| `x-models-etag` | Model Catalog Refresh Signal |

这些信息会进入 `SamplingError` 或 `ResponseModelMetadata`，再由上层 Retry、Session 与 Model Catalog 使用。

---

## 74. `Retry-After` 的精确规则

- 只接受整数 Delta Seconds。
- `0` 合法。
- HTTP-date 形式忽略。
- 最大截断到 120 秒。
- 缺失或非法返回 None，Retry Layer 使用 Exponential Backoff。

限制 120 秒防止错误或恶意上游让客户端 Sleep 极长时间。

---

## 75. `x-should-retry` 的精确解析

- `true` / `false` 大小写不敏感。
- 其他值如 `banana` -> None。
- 缺失 -> None。

它只保存解析后的 Option；真正“false 否决、true 不强制”的策略在 Retry Classifier。

---

## 76. Model Metadata 为什么全空时返回 None

只有至少一个 Header 成功解析时才构造 `ResponseModelMetadata`。

这样下游能区分：

- Provider 确实返回一个 Metadata 对象，但部分字段缺失。
- Provider 完全没有这些 Header。

非法数字与缺失等效，不会让整个 Sampling Request 失败。

---

## 77. HTTP 401 为什么单独成为 `Auth`

所有六个路径都在 Status 非成功后先检查 401：

1. 记录 Span Error。
2. 调用对应 Consumer 的 Attribution Callback。
3. 清理 Server Error Body。
4. 使用 Build-time Credential Capture 构造 `SamplingError::Auth`。

其他 4xx/5xx 进入 `SamplingError::Api`。

这个类型差异决定上层是否进入认证刷新流程。

---

## 78. 为什么 403 不走 Auth 分支

403 表示请求已被理解但操作不被允许，可能是权限、策略、Safety 或 ZDR 限制。

它保留为 `SamplingError::Api { status: 403 }`。

若把它变成 Auth：

- 会触发无意义 Token Refresh。
- 可能导致 Session Tear-down。
- 掩盖真实 Policy Denial。

---

## 79. API Error 为什么保留 Header Metadata

`SamplingError::Api` 包含：

- Status。
- Sanitized Message。
- Model Metadata。
- Retry After。
- Should Retry。

一个 Error 不只是给用户看的 String；它同时是 Retry 和 Session Recovery 的输入。

例如 Context Overflow 的 Error Message 与 Context Window Header 可以共同帮助上层决定 Compaction。

---

## 80. Error Body 支持两种 JSON Envelope

### OpenAI-style

```json
{
  "error": {
    "message": "...",
    "type": "..."
  }
}
```

### Flat Proxy-style

```json
{
  "code": "...",
  "error": "..."
}
```

统一解析结果是：

```text
(error_type, message)
```

同一个 Parser 同时用于 HTTP Error Body 和 SSE 内嵌 Stream Error。

---

## 81. 用户为什么看不到 Cloudflare HTML

`user_facing_api_error_message` 只向用户暴露已识别的 Structured JSON Error。

若 Body 是：

- HTML Edge Page。
- Plain Text Dump。
- 空 Body。
- 非法 UTF-8。

就完全不使用原 Body，而按 HTTP Status 生成固定文案。

这防止：

- 大段 HTML 污染 TUI。
- 上游页面泄露内部标识。
- 错误 Body 被当作可信用户消息。

---

## 82. Status-based Fallback 文案

- 502–504：Grok 暂时不可用。
- 520–524：连接超时或中断。
- 其他 5xx：服务端错误。
- 4xx：请求失败。
- 其他：通用请求失败。

文案包含 HTTP Code，但不包含原 HTML。

Integration Test 使用真实 524 HTML 验证 `<!DOCTYPE>` 和 `<html>` 不会进入 Error String。

---

## 83. Structured Error 为什么限制 280 字符

即使 JSON Envelope 合法，Message 也可能非常大。

`truncate_user_error` 按 Unicode Character 数截到 280，再加省略号。

按 Character 而不是 Byte 截断，避免切坏 UTF-8。

原始大 Body 可以在受控诊断日志中有限预览，但不应直接进入用户界面。

---

## 84. `body_preview` 与 User-facing Error 的区别

`body_preview`：

- 用于 Error Log。
- Lossy UTF-8。
- 最多 500 Characters。
- 可能包含原始非结构 Body 的一部分。

`user_facing_api_error_message`：

- 用于返回给调用者/用户。
- 非 JSON Body 完全不透传。
- Structured Message 最多 280 Characters。

日志访问控制因此仍然重要；“TUI 不显示 HTML”不等于日志没有 Preview。

---

## 85. 成功 HTTP + 坏 JSON 为什么是 Serialization

若 Status 成功，但 Body 无法反序列化为目标 Response：

- 记录 Parse Error 和 Raw Body。
- 返回 `SamplingError::Serialization`。

它不伪装成 API 500，因为服务端已经返回成功 Status；问题是 Wire Contract 与客户端类型不一致。

Serialization 在 Retry Policy 中不可重试，防止确定性 Schema Mismatch 烧预算。

---

## 86. Streaming 返回值代表什么

例如：

```text
Result<(BoxStream<Result<TypedEvent>>, Option<ModelMetadata>)>
```

外层 `Result` 表示：

- Request 构建失败。
- 连接失败。
- HTTP Status 非成功。

内层 Stream Item `Result` 表示：

- SSE Transport 中途失败。
- SSE 内嵌 Server Error。
- Event JSON 反序列化失败。

必须同时处理“拿不到 Stream”和“Stream 中途 Error”两种失败平面。

---

## 87. UTF-8 BOM 为什么手动剥离

三条 Streaming 路径都检查首个 Byte Chunk 是否以：

```text
EF BB BF
```

开头。

若有则切掉完整 3-byte BOM。

源码说明依赖的 `eventsource-stream 0.2.3` 对 BOM Slice 有已知错误；在 Byte Stream 进入 SSE Parser 前修复可以保护所有 Backend。

只检查第一个 Byte Chunk，因为 BOM 只应出现在 Stream 开头。

---

## 88. Byte Stream 怎样变 SSE Event

```text
response.bytes_stream()
  -> map(strip first BOM)
  -> eventsource()
  -> Stream<Result<Event, EventStreamError>>
```

SSE Event 至少提供：

- `event.event`：Event Name。
- `event.data`：Payload String。

大多数 Typed Decode 使用 Data；Responses Doom Loop 还需要 Event Name。

---

## 89. `[DONE]` 怎样结束流

Chat 与 Messages，以及 Responses 兼容路径都检查：

```text
event.data == "[DONE]"
```

命中后 `scan` 返回外层 None，Typed Stream 正常结束。

`[DONE]` 不是一个传给 Layer 2 的业务 Event。

Layer 2 是否已经拿到足够终态数据由各协议状态机判断；Responses 仍要求看到 Completed/Incomplete Typed Event。

---

## 90. 为什么每个 SSE Data 先探测 Stream Error

Provider 可能在 HTTP 200 的 SSE 中发送 Error JSON，而不是 Typed Chunk/Event。

流程：

```text
try_parse_stream_error(data)
  Some(error) -> yield Err(StreamError)
  None        -> deserialize normal backend type
```

如果反过来先做 Backend Typed Decode，Server Error Envelope 会被误报成 Serialization，丢失 `error_type` 和正确 Retry 性质。

---

## 91. Stream Error 与 Transport Error 的区别

### Stream Error

HTTP/SSE 连接仍正常，Data 是 Provider Error Envelope：

```text
SamplingError::StreamError { error_type, message }
```

### Transport Error

Eventsource/HTTP Body 读取失败：

```text
SamplingError::EventStreamError(rendered error)
```

二者当前都可重试，但诊断、Overload 分类和错误来源不同。

---

## 92. `scan(had_transport_error)` 防止什么

某些 HTTP/2 连接断开后，底层 Stream 可能在每次 Poll 都继续返回 Error。

如果简单 `.map`：

```text
Err -> yield Err
next poll -> Err
next poll -> Err
...
```

可能形成高 CPU Busy Loop 和重复 Failed Noise。

当前 State Machine：

```text
first transport error
  -> had_transport_error=true
  -> yield one Err

next poll
  -> return None
```

Layer 2 只看到一次错误，然后 Stream 终止。

---

## 93. Serialization Error 为什么不设置 `had_transport_error`

`had_transport_error` 只在 Eventsource Transport Err 时置 true。

Typed JSON Parse Error 作为一个 Stream Item Err 发出；Layer 2 收到第一个 Err 后立即产生 Failed 并 Return，不会继续 Poll。

因此即使 Scan 状态未锁死，实际消费者仍按 Terminal Contract 停止。

如果未来出现“跳过坏 Event 继续流”的 Consumer，就必须重新评估这一行为。

---

## 94. Responses 为什么有 `filter_map`

Responses Decode 的 `scan` Item 是双层 Option：

```text
outer None       -> terminate stream
Some(None)       -> swallow this SSE event, keep stream alive
Some(Some(item)) -> forward typed Result
```

随后 `filter_map` 删除内层 None。

这个结构专门支持 Doom Loop Check Event：它需要被 Collector 消费，但不能送进 SDK Typed Parser，也不能终止正常 Stream。

---

## 95. Doom Check 关闭时为什么仍识别并吞掉 Event

即使 Client 未发送 Opt-in Header，服务端在 Rollout Skew 中仍可能发非标准 Check Event。

没有 Collector 时，Decoder 使用 `is_check_event(event_name, data)`：

- 识别到 -> Swallow。
- 不记录 Signals。
- 不让 Async OpenAI Typed Enum 因未知 Event 失败。

这是 Forward/Deployment Compatibility 防线。

---

## 96. Doom Collector 同时观察独立 Event 与终态字段

`absorb` 能识别：

- 专门的 `response.doom_loop_check` Event。
- Terminal Response Object 中的 Doom Field。

独立 Check Event：记录后 Swallow。

Terminal Event：记录但不 Swallow，因为 Layer 2 仍需要完整 Completed Response。

Signals 按 Raw Label 去重，因为服务端可能随着检测进度重复发送累计集合。

---

## 97. Responses Typed Decode 的未知 Tool Fallback

服务端会在 Response Created/Completed 中 Echo Request Tools。

如果其中包含 SDK 不认识的 `x_search`：

1. 第一次 `serde_json::from_str<ResponseStreamEvent>` 失败。
2. 再把 Data Parse 成 `serde_json::Value`。
3. 找 `/response/tools` Array。
4. 对每个 Entry 尝试反序列化为 `rs::Tool`。
5. 删除失败的 Tool Entry。
6. 重试整个 Typed Event Decode。

不是硬编码“删 x_search”，而是保留 SDK 当前能理解的所有 Tool。

---

## 98. 为什么只删 Echoed Tool Definition

未知 Tool Definition 位于 `/response/tools`，主要是 Request 配置回显。

真正的 Backend Tool Output Item 仍需通过 `OutputItem::CustomToolCall` 等类型进入响应。

补丁的目标不是删除工具执行结果，而是移除阻碍整个 Terminal Event 反序列化的、SDK 未建模的配置回显。

---

## 99. Typed Decode 仍失败时怎样处理

Fallback 后若仍无法反序列化：

- Error Log 记录 First Serde Error 与 Raw Data。
- 返回 `SamplingError::Serialization(first_err)`。

它保留首次完整 Typed Decode 的错误，而不是 Value Sanitization 后的次生错误。

从诊断角度，首次错误通常更接近原始 Wire Contract Mismatch。

---

## 100. Responses Terminal Override 为什么重读 Raw JSON

Async OpenAI Typed Response 尚未建模：

- `usage.context_details`。
- `usage.cost_in_usd_ticks`。

Typed Decode 成功后，`apply_terminal_event_overrides` 再 Parse 一次 Raw JSON，读取这些字段并修改 Typed Response。

这是有意的二次解析：只有 Terminal Event 需要，且它保存了关键 Context/Cost 语义。

---

## 101. Terminal Override 只处理哪两类 Event

- `ResponseCompleted`
- `ResponseIncomplete`

其他 Event 立即 Return。

Reasoning/Text Delta 中即使意外出现同名字段，也不会修改 Usage。

测试固定了 Non-terminal Event 不受 Context Details 影响。

---

## 102. Context Total 为什么覆盖 Typed `total_tokens`

Hosted Search 等 Server-side Multi-turn Loop 可能让 Wire Cumulative Total 随每一轮内部调用累加。

但 CLI `/context`、Auto Compact 与 Persisted `meta.totalTokens` 需要的是：

```text
final live context = context_details.input_tokens + output_tokens
```

所以 Terminal Override 只替换 `usage.total_tokens`。

Billing 字段仍保留 Typed Response 中的累计值。

---

## 103. Context Details 为什么要求两项都存在

`extract_context_total` 只有在：

- Input Tokens 存在且能转 u32。
- Output Tokens 存在且能转 u32。

时才返回 Saturating Sum。

若缺一半，不猜测另一半为 0。

旧部署完全没有 Context Details 时，也保持原 Typed `total_tokens`。

---

## 104. Cost 为什么暂存在 Metadata

Typed `ResponseUsage` 没有 Cost 字段。

Decoder 读取有效 `cost_in_usd_ticks` 后写入：

```text
response.metadata["xai.cost_usd_ticks"] = string value
```

Layer-2 Responses Parser 随后：

1. 从 Metadata Remove 该 Key。
2. Parse 为 `i64`。
3. 写入 canonical `ConversationResponse.cost_usd_ticks`。

Metadata 只是跨越 Typed SDK 层的临时 Transport，不是最终公开存储位置。

---

## 105. 为什么 Cost 的 0 可能视为未报告

两条路径都经过 `reported_cost_ticks`。

它区分“有意义的 Cost Tick”与 Provider 用 0 表示未报告的情况。

这样后续不会把缺失计费误当成真实零成本。

---

## 106. 三条 Streaming Decode 的差异表

| 阶段 | Chat Completions | Responses | Messages |
| --- | --- | --- | --- |
| `[DONE]` | 终止 | 终止 | 终止 |
| Stream Error Probe | 有 | 有 | 有 |
| Typed Target | `ChatCompletionChunk` | `ResponseStreamEvent` | `MessageStreamEvent` |
| Unknown Tool Fallback | 无 | 有 | 无 |
| Doom Event Swallow | 无 | 有 | 无 |
| Terminal Raw Override | 无 | Context + Cost | 无 |
| Return Extra Collector | 无 | 有 | 无 |
| Transport Error Latch | 有 | 有 | 有 |

共同骨架相同，但 Responses 在 Layer 1 已经拥有最多兼容逻辑。

---

## 107. Sampling Log 与 HTTP Span 的分层

### Sampling Request Span

在 Per-request Retry Task 外层建立，包含：

- Request ID。
- Model。
- Backend。
- Base URL。
- Auth Type/Tail。
- Reasoning Effort。
- 最终 Output/Reasoning Token。

### HTTP Method Span

每个 Streaming Attempt 的具体 Client 方法建立，包含：

- Endpoint。
- Model ID。
- Status Code。
- Success。
- Error。

因此一次逻辑 Sampling Request 可包含多个 HTTP Attempt Span。

---

## 108. 为什么 Raw SSE Chunk 被写入 Sampling Log

每个 Backend Decoder 都记录：

```text
event="sse_chunk"
backend=...
data=<raw payload>
```

它对协议诊断极有价值：

- 确认 Provider 发了什么 Event。
- 定位 Serialization Drift。
- 重建 Stop/Usage 顺序。

但 Raw Payload 可能包含用户内容、模型输出和 Tool 参数；只有显式启用 Sampling Log，并且必须按敏感数据策略保护日志文件。

---

## 109. 常见错误理解一：`api_key` 永远是最终 Wire Credential

错误。

有 Resolver 时，`api_key` 只可能成为构造期 Header Seed；`post()` 会先删除它，再以 Resolver 当前值为唯一权威。

Resolver 返回 None 时也不会回退。

---

## 110. 常见错误理解二：共享 Reqwest Client 会串 Authorization

错误。

连接池共享的是传输连接，不是 SamplingClient 的 Default Header Map。

每次 `post()` Clone 当前 SamplingClient 自己的 Header，再放入 Request。

Wire-level Integration Test 同时证明连接被复用、Header 没串线。

---

## 111. 常见错误理解三：Config 中的 Env Header 每次请求都会重新读

错误。

`env_http_headers` 在 `SamplingClient::new` 时解析一次并冻结到 Default Headers。

运行中修改环境变量不会影响既有 Client；需要重建 Client。

只有 Bearer Resolver 与 Header Injector 是 Per-request Dynamic。

---

## 112. 常见错误理解四：HTTP 200 就不会返回错误

错误。

Streaming Provider 可以在 SSE Data 中发送 Error Envelope。

Decoder 必须先 `try_parse_stream_error`，再尝试普通 Typed Event。

---

## 113. 常见错误理解五：`[DONE]` 就等于已有完整 Response

错误。

`[DONE]` 只终止 Byte/SSE Stream。

Responses Layer 2 仍要求此前收到 `ResponseCompleted` 或 `ResponseIncomplete`；否则产生 Missing Terminal Failure。

Chat/Messages 则根据各自已经累加的状态构造终态。

---

## 114. 常见错误理解六：SDK Typed Model 是完整 Wire Contract

错误。

当前至少有：

- X Search Tool Definition。
- Reasoning Text Discriminator。
- Context Details。
- Cost Ticks。
- Doom Loop Check Event。

需要 Raw JSON Patch/Peek/Swallow。

Typed SDK 是主要结构，不是不可突破的唯一事实来源。

---

## 115. 常见错误理解七：所有错误 Body 都应该原样展示

错误。

只有已识别的 Structured JSON Error Message 进入用户文案。

HTML、Plain Text 与未知 Body 只按 Status 映射，既改善 UX，也减少信息泄露。

---

## 116. 常见错误理解八：Header Injector 可以随意改任何 Header

类型上可以，设计契约上不应该。

Credential Tail 在 Injector 之前捕获；Injector 若改 Auth 会破坏 Wire Attribution。

当前实现只注入 Traceparent，这个假设必须被文档、Code Review 或未来更窄接口保护。

---

## 117. 一次 Chat Streaming Request 的精确时序

```text
ConversationRequest
  -> apply conversation defaults
  -> canonical -> ChatCompletionRequest
  -> apply chat defaults
  -> StreamingChatRequest flatten + stream options
  -> endpoint(chat/completions)
  -> post(): live auth + capture + trace header
  -> apply x-grok request headers
  -> Accept SSE + JSON body
  -> build request + redacted header log
  -> execute
  -> status/header classification
  -> bytes stream
  -> strip BOM
  -> eventsource
  -> [DONE] / stream-error probe / ChatChunk decode
  -> Layer-2 Chat Parser
```

---

## 118. 一次 Responses Streaming Request 的精确时序

```text
ConversationRequest
  -> apply conversation defaults
  -> canonical -> typed CreateResponse + extra x_search entries
  -> apply response defaults: store=false + encrypted reasoning include
  -> stream=true
  -> serialize typed body to Value
  -> insert stream_tool_calls
  -> extend x_search tools
  -> patch reasoning_text discriminator
  -> create fresh doom collector
  -> post(): live auth + capture + trace header
  -> x-grok headers + Accept SSE + optional doom opt-in
  -> execute + classify status
  -> bytes/SSE
  -> swallow/record doom event
  -> stream-error probe
  -> typed Response Event decode
     -> optional unknown-tool sanitize retry
     -> terminal context/cost override
  -> Layer-2 Responses Parser
```

---

## 119. 一次 Messages Streaming Request 的精确时序

```text
ConversationRequest
  -> apply conversation defaults
  -> build Messages Request blocks
  -> apply message defaults
  -> stream=true
  -> drop process-local trace object
  -> endpoint(messages)
  -> post(): Bearer or x-api-key + capture + trace header
  -> x-grok headers + Accept SSE + typed JSON body
  -> execute + classify status
  -> bytes/SSE
  -> [DONE] / stream-error probe / MessageEvent decode
  -> Layer-2 Messages Block State Machine
```

---

## 120. 推荐源码阅读顺序

第一遍：请求装配。

1. `SamplerConfig`。
2. `SamplingClient` 字段。
3. `SamplingClient::new`。
4. `EndpointTemplate`。
5. `post` 与 `SentRequest`。

第二遍：每种 API。

1. Chat Streaming。
2. Responses Streaming。
3. Messages Streaming。
4. 三个 Non-streaming 对照。

第三遍：兼容与安全。

1. `shared_http.rs`。
2. `attribution.rs`。
3. `error.rs` Body Sanitization。
4. `doom_loop.rs`。
5. Integration Tests。

---

## 121. 建议动手实验一：画 Header 来源矩阵

设置同名 `x-demo`：

- Extra Header = A。
- Env Header = B。
- Header Injector = C。

再设置：

- Static API Key。
- Live Resolver Key。

Build 最终 Request 并打印非敏感 Header，验证实际顺序。

对 Authorization 只验证数量和预期测试值，不要输出真实 Secret。

---

## 122. 建议动手实验二：Token Rotation

实现一个 Mutex-backed Resolver：

1. 初值 Token A。
2. 调用 `post()` Build Request。
3. 改成 Token B。
4. 检查 Request Header 与 Capture。
5. 再 Build 第二个 Request。

预期：

- 第一请求携带/归因 A。
- 第二请求携带/归因 B。
- 第一请求不会因后续 Rotation 被改写。

---

## 123. 建议动手实验三：Query Folding

Base URL：

```text
https://gateway.example/v1?api-version=old&keep=1
```

Config Params：

```text
api-version=2026-07-22
tenant=a b
```

验证三个 Endpoint 都得到：

- Path 位于 Query 前。
- API Version 只有一个。
- Keep 保留。
- Space 被编码。

---

## 124. 建议动手实验四：SSE Transport Error Latch

构造一个 Eventsource Stream：

```text
valid chunk
transport error
transport error
transport error
```

验证 Typed Stream 只输出：

```text
Ok(valid)
Err(first transport error)
EOF
```

这能直观看到 `scan(false, ...)` 的价值。

---

## 125. 建议动手实验五：HTTP 200 内嵌 Error

让 Mock Server 返回 SSE：

```text
data: {"error":{"type":"overloaded_error","message":"Overloaded"}}
```

验证结果是：

```text
SamplingError::StreamError
```

而不是 `Serialization`。

再检查 Retry Classifier 是否按 Overload/Stream Error 处理。

---

## 126. 建议动手实验六：未知 Responses Tool Echo

在 Terminal Response 的 `response.tools` 中加入 SDK 不认识的 Tool Type，同时保留合法 Output Item。

验证：

- 首次 Typed Decode 失败。
- Tool Echo 被过滤。
- Event 整体成功解析。
- 真正 Output Item 仍保留。

---

## 127. 建议动手实验七：Context 与 Billing Token

Terminal Event 中设置：

```text
usage.input_tokens = cumulative 1000
usage.output_tokens = cumulative 500
usage.total_tokens = 1500
context_details.input_tokens = 300
context_details.output_tokens = 50
```

预期：

- Billing Input/Output 仍是 1000/500。
- Canonical Total Context 变为 350。

这能避免以后把两个“Total”再次混为一谈。

---

## 128. 关键单元测试地图

### Endpoint 与 Header

- `endpoint_appends_path_before_a_base_url_query_without_configured_params`
- `apply_env_http_headers_resolves_trims_skips_and_overrides`
- `new_applies_extra_headers`
- `sampling_client_always_has_user_agent`
- `header_injector_is_called_in_post`

### Auth

- `messages_plus_anthropic_api_key_uses_x_api_key_and_not_authorization`
- `messages_plus_bearer_uses_authorization_and_not_x_api_key`
- `post_emits_single_authorization_with_api_key_and_bearer_resolver`
- `bearer_resolver_none_strips_default_authorization`
- `post_capture_is_immune_to_resolver_rotation_after_build`
- `record_401_attribution_invokes_callback_with_captured_bearer`

### Response Headers

- `extract_retry_after_*`
- `extract_should_retry_*`

### Responses Override

- `deserialize_response_event_overrides_total_tokens_from_context_details`
- `deserialize_response_event_stashes_cost_in_metadata`
- `deserialize_response_event_total_tokens_unchanged_when_context_details_absent`
- `deserialize_response_event_total_tokens_unchanged_when_context_details_partial`
- `deserialize_response_event_ignores_context_details_on_non_terminal_events`

---

## 129. 关键 Integration Test 地图

### `request_query_and_headers.rs`

真实起本地 Axum Server，验证 Query 与 Env Header 到达 Wire。

### `shared_http_wire.rs`

验证：

- 两个 SamplingClient 复用一个 Connection。
- 不同 Config 的 Auth/Extra Header 隔离。
- HTTP/1.1 Fallback 不复用连接。

### `shared_http_kill_switch.rs`

验证 Kill Switch 让两个 SamplingClient 建立两条连接。

### `cf_edge_error_message.rs`

验证：

- 524/503 HTML 不进入用户 Error。
- Structured JSON Message 保留。
- Status Fallback Matrix。

这些测试比只检查内部 HeaderMap 更强，因为它们观察真正到达 Server 的 Wire。

---

## 130. 可执行验证命令

```sh
cargo test -p xai-grok-sampler --lib
cargo test -p xai-grok-sampling-types --lib
cargo test -p xai-grok-sampler --test request_query_and_headers
cargo test -p xai-grok-sampler --test shared_http_wire
cargo test -p xai-grok-sampler --test shared_http_kill_switch
cargo test -p xai-grok-sampler --test cf_edge_error_message
```

快速定位：

```sh
rg "fn post|SentRequest|BearerResolver|record_401_attribution" \
  crates/codegen/xai-grok-sampler/src

rg "EndpointTemplate|query_params|env_http_headers" \
  crates/codegen/xai-grok-sampler/src/client.rs

rg "eventsource|had_transport_error|deserialize_response_event" \
  crates/codegen/xai-grok-sampler/src/client.rs
```

---

## 131. 修改认证代码时的审查清单

1. Resolver 存在时，是否仍先删除两种旧 Auth Header？
2. Resolver None 是否仍不回退 stale seed？
3. Wire 上是否只有一个 Authorization/x-api-key？
4. Credential Capture 是否来自该 Request Build，而非响应时 Live Read？
5. 完整 Token 是否跨出了 Client 边界？
6. 401 是否只在最低层 Emit 一次 Attribution？
7. 401 与 403 是否仍区分？
8. Streaming/Non-streaming 六个路径是否都同步更新？
9. Header Injector 是否可能在 Capture 后改 Auth？
10. 新日志是否输出 Secret 或过长片段？

---

## 132. 修改 Header/Endpoint 时的审查清单

1. 新 Header 属于上层 Trust Policy 还是底层 Protocol？
2. 与 Extra/Env/Identity/Header Injector 的覆盖顺序是什么？
3. Header Name 是否 Case-insensitive Merge？
4. 非法值是 Fatal 还是 Skip，是否一致？
5. Base URL 已有 Query 时 Path 是否仍在 Query 前？
6. Configured Query 是否替换 Base 同名 Key？
7. Value 是否由 URL Encoder 处理？
8. Streaming 和 Non-streaming 是否都应用？
9. Shared Client 是否仍不保存 Config Header？
10. 是否有 Wire-level Test，而不只是内部结构测试？

---

## 133. 修改 SSE Decoder 时的审查清单

1. `[DONE]` 是否正常终止？
2. UTF-8 BOM 是否仍在 Parser 前剥离？
3. Stream Error Envelope 是否早于普通 Typed Decode？
4. Transport Error 是否只发一次后 EOF？
5. Serialization Error 是否保持 Typed Fatality？
6. 新的非标准 Event 应 Swallow、Forward 还是 Fail？
7. Swallow Event 是否误终止 Stream？
8. Responses Unknown Tool Fallback 是否只删配置回显？
9. Terminal Raw Override 是否只作用于终态？
10. Raw SSE Logging 是否符合数据安全要求？

---

## 134. 可以继续追问的设计问题

1. Dynamic Resolver 产生非法 Header 时，是否应本地返回 Auth Error，而不是无凭据发送？
2. `HeaderInjector` 是否应该改为只返回允许名单中的 Trace Headers？
3. Deployment/User Header 的双来源是否应该收敛为单一位置？
4. Static API Key 的 Invalid Header Debug Log 是否应彻底去掉 Key Value？
5. Raw SSE Sampling Log 是否需要字段级 Redaction 或按数据分类开关？
6. Non-streaming Responses 是否应补齐 X Search Extra Tool Injection？
7. Responses Raw JSON Compatibility Patch 是否应该抽成可版本测试的 Wire Adapter？
8. Env Headers 是否有场景需要 Per-request Refresh，而不是 Client 构造时冻结？
9. Retry-After 是否需要支持 HTTP-date？
10. Error Body Preview 是否应针对自定义 Secret 字段进一步清理？

这些问题不等于已确认 Bug；它们是从当前所有权和时序中推导出的维护风险点。

---

## 135. 本篇核心不变量

1. `SamplingClient::new` 只构造本地状态，不做网络 I/O。
2. Base URL Query 必须在追加 Endpoint Path 后出现。
3. Configured Query Key 覆盖 Base URL 同名 Key。
4. Shared Reqwest Client 共享连接，不共享 Sampling Config Headers。
5. 一旦存在 Bearer Resolver，它就是唯一 Auth Source。
6. Resolver None 必须删除 stale static credentials，并无凭据发送。
7. 每个 Request 的 401 必须归因于 Build 时真实捕获的 Credential Tail。
8. 完整 Credential 不进入 Attribution Callback。
9. Header Injector 当前只应修改追踪类 Header。
10. HTTP 非成功先按 401/Auth 与普通 Api Error 分流。
11. 非结构 Error Body 不得原样进入用户界面。
12. HTTP 200 SSE 仍可能包含 Provider Stream Error。
13. 首个 Transport Stream Error 发出后必须终止流。
14. SDK 未建模字段通过局部 Raw JSON Escape Hatch 补齐，而不是放弃 Typed Model。
15. Responses Billing Usage 与 Live Context Total 必须保持不同语义。

---

## 136. 本篇术语表

| 名词 | 白话解释 | 本篇中的具体含义 |
| --- | --- | --- |
| HTTP client | 发出 HTTP 请求并维护连接的对象 | `reqwest::Client`，可在多个 SamplingClient 间共享 |
| SamplingClient | 模型采样的传输适配器 | 保存默认值、Header、Endpoint Template，并提供三种 API 方法 |
| configuration snapshot | 某一时刻冻结的配置副本 | Client 构造时保存的 Defaults 与 Default Headers |
| dynamic resolution | 真正请求时再读取当前值 | Bearer Resolver 与 Header Injector |
| DTO | 只为跨层传数据设计的结构 | `SamplerConfig` 是 Session 到 Sampler 的传输配置对象 |
| serde | Rust 的序列化/反序列化框架 | 把 Request/Response 类型和 JSON 互转 |
| `#[serde(skip)]` | 序列化时忽略字段 | Callback/Resolver/Injector 不会进入 JSON，也无法自动恢复 |
| trait object | 运行时通过统一接口调用不同实现的对象 | `Arc<dyn BearerResolver>` 等 |
| header | HTTP 请求或响应的元数据键值 | Authorization、Retry-After、Content-Type 等 |
| HeaderMap | 大小写不敏感的 HTTP Header 容器 | Client 构造与 `post()` 中合并 Header 的结构 |
| Header injection | 在请求构建时加入额外 Header | Extra、Env、Identity、Trace 等有不同来源和时序 |
| header precedence | 同名 Header 冲突时谁最终生效 | 由写入顺序、Insert 或 Builder Append 行为决定 |
| header injection attack | 用换行等字符伪造额外 Header | `HeaderValue` 校验会拒绝危险值 |
| Bearer | `Authorization` 的一种认证方案 | `Authorization: Bearer <token>` |
| x-api-key | Anthropic-style API Key Header | Messages 可按 AuthScheme 使用它 |
| seed key | Client 构造时提供的初始 API Key | 有 Live Resolver 时不会作为失败回退 |
| stale credential | 已过期或不再权威的凭据 | Resolver 存在时必须从 Default Headers 删除 |
| live bearer | 请求构建时 Auth Manager 当前认可的 Token | 通过 `BearerResolver::current_bearer` 读取 |
| fail closed | 不确定时选择不扩大权限的行为 | Resolver 无值时不发送 stale key，而不是回退 |
| credential provenance | 错误对应的请求是否真的携带凭据 | `SentCredential::Sent/Missing/Unknown` |
| attribution | 把 401 关联到真正上线路径和凭据片段 | Callback 收 Consumer 与 Build-time Token Tail |
| token rotation | 认证系统把旧 Token 换成新 Token | Build-time Capture 防止旋转后误归因 |
| credential suffix | Token 固定长度尾片 | 用于诊断关联，不暴露完整 Secret |
| callback | 发生事件时同步调用的接口 | 401 Attribution Callback 必须便宜且非阻塞 |
| resolver | 提供当前值的抽象接口 | Bearer Resolver 只做同步快照读取，不负责刷新 |
| header injector | 每请求修改 HeaderMap 的 Hook | 当前 Session 实现用于 `traceparent` |
| traceparent | W3C Trace Context Header | 将当前分布式追踪关系带到 Sampling 请求 |
| process-local trace | 只在当前进程中使用的 Trace 对象 | Wrapper 序列化前被 Take，不进入 JSON Body |
| endpoint | API 的最终 URL | Base URL 加 `chat/completions`、`responses` 或 `messages` |
| query parameter | URL `?` 后面的键值 | Provider Version、Tenant 等可由 Config 注入 |
| query folding | 合并 Base URL 与 Config 两处 Query | Config 同名 Key 获胜，Path 保证位于 Query 前 |
| percent encoding | 把 URL 中特殊字符编码 | 空格等通过 URL Query Builder 安全转换 |
| template | 预计算、每次只填少量变量的结构 | `EndpointTemplate` 保存 Prefix 与 Query Suffix |
| fast path | 针对常见简单情况跳过昂贵步骤 | 无 Query 时直接字符串拼 Endpoint |
| connection pool | 保存并复用网络连接的池 | Shared HTTP/2 Client 减少握手成本 |
| process-wide | 整个进程共享一份 | `OnceLock<reqwest::Client>` |
| OnceLock | 只初始化一次的并发容器 | 缓存成功构建的 Shared Client 或 Kill Switch 值 |
| HTTP/2 | 支持多路复用的 HTTP 协议版本 | 默认 Sampling Transport，带 Keepalive 与 Pool |
| HTTP/1.1 fallback | 只使用 HTTP/1.1 的降级 Client | 禁用 Pool，用于逃离损坏的 HTTP/2 连接状态 |
| keepalive | 空闲时探测连接是否仍健康 | HTTP/2 每 15 秒 Ping，5 秒超时 |
| idle pool timeout | 空闲连接在池中保留多久 | 默认 90 秒 |
| connect timeout | 建立 TCP/TLS 连接最多等待多久 | 默认 10 秒 |
| extra CA | 操作系统默认 CA 之外的根证书 | 企业代理/私有 PKI 可通过共同 Builder 接入 |
| kill switch | 紧急恢复旧行为的开关 | `GROK_SAMPLER_SHARED_CLIENT=0/false` 禁用共享 Client |
| request builder | 尚未发送、可继续加 Header/Body 的请求对象 | Reqwest `RequestBuilder` |
| wire | 真正通过网络发送或收到的数据 | Wire Header、Wire JSON、Wire SSE Frame |
| body | HTTP 请求或响应主体 | 非流式 JSON 或流式 SSE Bytes |
| typed model | 用 Rust Struct/Enum 表达的协议 | SDK 的 Request、Response 与 Event 类型 |
| raw JSON escape hatch | Typed Model 不够时局部操作 JSON Value | 注入 x_search、补 Reasoning Type、读取 Context/Cost |
| discriminator | 表示 JSON Variant 类型的字段 | Reasoning Content 需要 `type: reasoning_text` |
| wrapper | 在标准 SDK 类型外携带附加本地字段的结构 | CreateResponseWrapper、MessagesRequestWrapper |
| flatten | 把嵌套 Struct 字段序列化到同一层 | Streaming Chat Request 用它单次生成顶层 Body |
| ZDR | Zero Data Retention | Responses 默认 `store=false`，避免服务端留存 |
| response metadata | HTTP Header 或 Response Object 的附加信息 | Context Window、Max Completion、Model ETag 等 |
| status code | HTTP 响应的三位数字 | 401 单独为 Auth，其他失败进入 Api Error |
| Retry-After | 服务端建议的等待时间 | 只解析整数秒并封顶 120 秒 |
| retry hint | 服务端对错误是否值得重试的提示 | `x-should-retry` 被保存，策略在 Retry Layer 执行 |
| error envelope | 服务端包装错误的 JSON 结构 | 支持 OpenAI Nested 与 Proxy Flat 两种格式 |
| sanitization | 清理不应直接展示的数据 | HTML/Plain Text Body 被状态文案替换，结构消息限长 |
| body preview | 日志中的短原始 Body 片段 | 最多 500 字符，不等于 User-facing Message |
| serialization error | JSON 与 Typed Model 不匹配 | 成功 HTTP Body 或 SSE Event 解码失败，默认 Fatal |
| outer Result | 获取 Stream 之前的成败 | Request Build、Connect、HTTP Status Error |
| stream item Result | Stream 已建立后的单项成败 | Transport、Server Stream Error 或 Event Parse Error |
| byte stream | 按网络到达顺序产生 Bytes 的异步流 | `response.bytes_stream()` |
| UTF-8 BOM | 文本开头可选的三个标记字节 | 在 SSE Parser 前从首个 Byte Chunk 剥离 |
| SSE | Server-Sent Events | 把连续 Event Frame 放在一个 HTTP Response 中 |
| event name | SSE 的 `event:` 字段 | Responses Doom Check 可按名字识别 |
| event data | SSE 的 `data:` 字段 | 通常包含一段 JSON 或 `[DONE]` |
| `[DONE]` | 兼容 API 使用的流结束哨兵 | Decoder 消费它并结束，不转成 Typed Event |
| scan | 带内部状态转换 Stream Item 的组合器 | 记住是否已发生 Transport Error |
| busy loop | 不停 Poll/报错却不前进的高 CPU 循环 | Transport Error Latch 在一次错误后结束 Stream |
| stream error | HTTP 200 SSE 中的 Provider Error JSON | 先于普通 Event Decode，保留 Error Type |
| transport error | 网络 Body/Eventsource 读取失败 | 变成 EventStreamError，只发一次 |
| swallow | 消费某 Event 但不向下游转发 | Doom Check Event 被记录后跳过 |
| filter_map | 同时过滤和转换 Stream Item | Responses 用它删除被 Swallow 的内层 None |
| rollout skew | 客户端和服务端版本部署不同步 | 未 Opt-in 也可能收到新 Event，Decoder 防御性吞掉 |
| unknown tool echo | Response 回显了 SDK 不认识的 Tool Definition | Decoder 过滤无法解析的 `/response/tools` Entry 后重试 |
| terminal override | Typed Decode 后用 Raw JSON 修正终态字段 | Responses 的 Context Total 与 Cost |
| billing usage | 计费所依据的累计 Token | 不被 Context Total Override 改写 |
| live context | 最终模型当前真正占用的上下文长度 | `context_details.input + output` |
| cost ticks | Provider 返回的整数成本单位 | 经 Metadata 临时桥接到 canonical Response |
| tracing span | 记录一次操作字段和耗时的结构化范围 | Sampling Request Span 与每 Attempt HTTP Span 分层 |
| sampling log | 可选的低层采样诊断日志 | 可能含 Raw SSE Data，必须按敏感数据保护 |
| unit test | 在模块内验证纯逻辑的测试 | Header Parse、Endpoint、Auth Capture 等 |
| integration test | 从 crate 外通过真实本地网络观察行为的测试 | 验证 Wire Query/Header、Connection Reuse 与 Error Sanitization |

---

## 137. 一句话复盘

`SamplingClient` 的核心价值不是封装一次 POST，而是把“构造时配置”和“请求时动态状态”严格分开：它预计算安全稳定的 Endpoint、Defaults 与公共 Header，又在每个 Request Build 时以 Live Resolver 重建唯一认证、捕获真实上线路径并注入当前 Trace；随后通过状态码、受控错误 Body、一次错误后终止的 SSE Decoder 和局部 Raw JSON 补丁，把现实世界里不完整、会演进、可能带敏感数据的 Provider Wire Protocol，转换成上层可以可靠分类和恢复的 Typed Stream。
