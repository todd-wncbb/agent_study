# 源码精读 23：Agent Failure & Recovery Runtime——错误分类、Retry、401、Doom Recovery、Turn 收敛、Persistence 与 Session Repair

> 源码基线：`ed6d543`
>
> 本文研究一个 Agent 系统最容易被低估的问题：失败发生后，谁应该处理它？是 HTTP Client、Sampler、SessionActor、Tool Runtime、Persistence Actor、Leader，还是 Client UI？如果恢复失败，又怎样保证一次 Turn 仍然只产生一个可信终态，而不是卡死、重复执行或悄悄丢失错误？

---

## 1. 先给结论：错误不是一个值，而是一条责任链

Grok Build 中的错误并不是简单地从最底层一路 `?` 到客户端。

不同层有不同职责：

```text
Wire / HTTP
    识别 status、header、SSE 与 transport failure
        ↓
SamplingError
    保存可分类的结构化失败
        ↓
Sampler Retry Loop
    处理无须 Session 语境的 transient failure
        ↓
SessionActor Recovery
    处理 auth、compaction、model/history 等会话级恢复
        ↓
Turn Completion
    生成唯一 error terminal、usage 与 hook classification
        ↓
ACP Error + RetryState + TurnCompleted
    分别服务请求方、实时 UI 与持久化 replay
```

如果是 Tool 或 Persistence，则走另一条支路：

```text
ToolError
    -> 通常转换为 ToolResult 反馈给模型
    -> 少数 auth failure 先刷新并重试一次

Persistence io::Error
    -> best-effort 写入通常告警后继续
    -> correctness-critical 写入使用 acknowledged / commit-aware API
    -> load/init 错误转换成稳定 ACP filesystem code
```

最核心的设计原则是：

> 只有拥有足够恢复语境的层，才应该决定如何恢复；但所有最终失败必须在 Turn 终点重新收敛成一致、可观察、可回放的结果。

---

## 2. 本文源码地图

| 层次 | 文件 | 关键符号 |
| --- | --- | --- |
| 结构化采样错误 | `crates/codegen/xai-grok-sampling-types/src/error.rs` | `SamplingError` |
| Retry 分类 | `crates/codegen/xai-grok-sampler/src/retry.rs` | `RetryDecision`、`classify_error` |
| Sampler 请求任务 | `crates/codegen/xai-grok-sampler/src/actor/request_task.rs` | `AttemptOutcome`、`run_request_task`、`run_one_attempt` |
| 401 采样归因 | `crates/codegen/xai-grok-sampler/src/attribution.rs` | `Auth401AttributionCallback` |
| Shell 401 归因 | `crates/codegen/xai-grok-shell/src/auth/attribution.rs` | `ShellAttribution`、`record_auth_401` |
| Auth 恢复状态机 | `crates/codegen/xai-grok-shell/src/auth/recovery.rs` | `UnauthorizedRecovery` |
| Session 采样恢复 | `.../session/acp_session_impl/sampler_turn.rs` | `handle_sampling_failure` |
| 401 Turn 预算 | `.../session/acp_session_impl/auth_retry.rs` | `AuthRetrySchedule` |
| Turn 主循环 | `.../session/acp_session_impl/turn.rs` | `process_conversation_turn` 相关路径 |
| Turn 终态 | `.../session/acp_session_impl/turn_end.rs` | `handle_completion`、`emit_turn_completed` |
| ACP 错误映射 | `.../sampling/error.rs` | `map_sampling_err_to_acp`、`prompt_complete_fields` |
| Tool 失败 | `.../session/acp_session_impl/tool_calls.rs` | `handle_tool_parse_error` |
| Tool auth retry | `.../session/acp_session_impl/sampler_turn.rs` | `call_with_auth_retry` |
| Persistence Actor | `.../session/persistence.rs` | `PersistenceMsg`、`SessionPersistence::run` |
| Commit-aware storage | `.../session/storage/mod.rs` | `AppendUpdateError`、`AppendCwdSwitchError` |
| Session repair | `.../extensions/repair.rs` | `handle_session_repair`、`repair_on_disk` |
| Chat history repair | `crates/codegen/xai-chat-state/src/compaction_utils.rs` | `repair_history` |
| Actor death recovery | `.../agent/mvp_agent/session_lifecycle.rs` | `sweep_dead_sessions` |

---

## 3. 先区分六种“失败”

读源码前，应把失败至少分成六类。

### 3.1 Attempt failure

一次模型请求尝试失败。

它可能被 Sampler 重试，对 User Turn 不可见。

### 3.2 Sampling request failure

Sampler 的全部尝试都未得到可接受 Response。

它会交给 Session 决定是否还能 compaction、refresh auth 或 resubmit。

### 3.3 Tool-call failure

某个 Tool 无法解析或执行。

通常它只是 Conversation 中的一个失败 ToolResult，模型仍可修正并继续。

### 3.4 Turn failure

一次 Prompt 最终无法完成。

它必须结束 ACP `session/prompt` 请求，并产生 UI 与持久化终态。

### 3.5 Session runtime failure

SessionActor 自身 panic、退出或 channel 断开。

它可能让当前 Turn 失败，但历史 Session 仍可从磁盘重新 load。

### 3.6 Persistence failure

会话更新或状态无法写盘。

它可能只是辅助状态丢失，也可能意味着关键顺序点无法安全确认。

这些层级不能混用。

例如一次 `read_file` 失败不应自动杀死 Session；一次 SessionActor panic 也不应删除已经持久化的 Conversation。

---

## 4. `SamplingError` 为什么必须是 enum

`SamplingError` 包含：

```rust
Auth { message, credential }
InvalidConfiguration(...)
Http(reqwest::Error)
Serialization(serde_json::Error)
Api { status, message, model_metadata, retry_after_secs, should_retry }
EventStreamError(...)
StreamError { error_type, message }
IdleTimeout { elapsed_secs }
EmptyResponse { context }
MaxTokensTruncation
DoomLoopDetected { triggers, aborted_at_chunk }
```

如果底层只返回字符串：

```text
"request failed"
```

上层将无法安全判断：

- 能否重试；
- 是否应该刷新 token；
- 是否应该删除图片；
- 是否应该压缩上下文；
- 是否应该显示 rate-limit UI；
- 是否已经有部分输出；
- 是否是 response parse 的确定性故障。

结构化错误是恢复策略的输入数据。

---

## 5. `Auth` 与 HTTP 401 有什么区别

认证失败可以有两种来源：

### `SamplingError::Auth`

客户端在发请求前或构建认证时已经知道认证有问题。

它还记录：

```rust
credential: SentCredential
```

### `SamplingError::Api { status: 401 }`

请求已经到达服务端，服务端拒绝了凭据。

`is_auth_error()` 将这两类都视为 auth failure。

但它明确不把 403 算作 auth failure。

---

## 6. 为什么 403 不能触发 token refresh

401 通常表示：

```text
凭据缺失、过期或被拒绝
```

403 通常表示：

```text
认证已经成功，但没有执行该动作的权限
```

可能原因包括：

- content safety；
- ZDR 限制；
- remote setting gate；
- 账号权限不足。

如果把 403 当成 401：

1. 发起无意义 token refresh；
2. 返回 ACP `auth_required`；
3. Client 可能启动重新登录甚至拆 Session；
4. 真正的 policy error 被掩盖。

所以 `is_auth_error()` 只接受 `Auth` 与 401。

---

## 7. `SentCredential` 解决什么问题

401 不只要知道“被拒绝”，还要知道：

```text
请求到底有没有带凭据？
```

`SentCredential` 表达：

- `Sent`；
- `Missing`；
- `Unknown`。

这会影响 auth retry 预算。

如果根本没有凭据上 wire，服务端返回 401 不应立刻消耗“真实 token 已被拒绝”的预算。

但 `Unknown` 不能乐观视为 Missing，否则丢失 provenance 会导致无限 retry；源码倾向 fail-closed，把未知归入会消耗预算的路径。

---

## 8. API error 里为什么保存 header

`SamplingError::Api` 除 status/message 外还保存：

- `retry_after_secs`；
- `should_retry`。

`Retry-After` 告诉客户端服务端希望等待多久。

`x-should-retry` 是服务端对失败性质的额外判断。

特别重要的是：

```text
x-should-retry: false
```

可以把一个表面上是 500 的错误标记为 request-content-caused。

这样客户端不会把同一个坏请求重发 15 次。

---

## 9. Retry classifier 为什么写成纯函数

`classify_error` 只读取：

- `SamplingError`；
- 已重试次数；
- 最大重试预算；
- 429 单独预算。

返回：

```rust
enum RetryDecision {
    Retry { backoff },
    RetryWithBackoff { backoff, is_rate_limited },
    RetryWithImageStrip,
    RetryWithClientRebuild { backoff },
    EmitToSession(SamplingError),
    Fatal(SamplingError),
}
```

它不负责：

- sleep；
- logging；
- 发 UI notification；
- 修改 request；
- 刷新 auth。

纯分类函数的好处是测试可穷举，不会因 Tokio 时间、网络或 Client 状态变得不稳定。

---

## 10. `EmitToSession` 和 `Fatal` 的差别

两者都会结束 Sampler 内部 retry loop。

但语义不同。

### `Fatal`

Sampler 已经知道没有更合适的恢复办法，应作为最终采样结果返回。

### `EmitToSession`

Sampler 自己不该继续，但 Session 拥有更丰富语境，可能恢复。

当前典型情况：

- auth error；
- encrypted-content history mismatch。

因此：

> “Sampler 不重试”不等于“Turn 必须失败”。

Session 仍可能 refresh、compact 或给用户更可行动的提示。

---

## 11. 默认哪些错误会重试

默认最大 retry 数是 15。

典型可重试错误：

- HTTP connection failure；
- 500/502/503/504/520/529；
- EventStreamError；
- StreamError；
- EmptyResponse。

退避大致为：

```text
2s -> 4s -> 8s -> 16s -> capped around 30s
```

每次加入 ±20% jitter。

为什么 jitter 很重要？

同一服务故障时，成千上万客户端若在完全相同时间重试，会形成 thundering herd，让恢复中的服务再次过载。

---

## 12. 为什么 429 预算更小

429 的默认 threshold 是 2。

理由是 rate-limit 的 `Retry-After` 可能很长。

把 429 也按 15 次重试，会让用户等待很久才得到明确结果，而且通常不会提高成功率。

因此：

```text
generic transient failure
    -> 较长总预算

rate limit
    -> 较小独立 cap
```

429 还会优先使用服务端 `Retry-After`，没有时才使用客户端 exponential backoff。

---

## 13. 哪些错误不应原样重试

立即 Fatal 的典型错误：

- 400、401、403、404、408、422；
- InvalidConfiguration；
- Serialization；
- IdleTimeout；
- MaxTokensTruncation；
- context-length overflow；
- `x-should-retry: false`。

共同点是：

```text
重发同一个请求通常不会改变结果
```

但其中 401 与 context-length 可能在 Session 层通过改变输入/凭据后重新提交。

---

## 14. 为什么 Serialization 必须保持自己的类型

`serde_json::Error` 不是 Clone。

Retry loop 有时需要从借用错误构造 owned error。

源码不能粗暴转换为：

```rust
EventStreamError(error.to_string())
```

因为 EventStreamError 是 retryable，而 Serialization 是 deterministic non-retryable。

错误类型一旦丢失，系统会反复生成同一份无法解析的响应，烧完整个预算。

因此源码提供：

```rust
SamplingError::serialization_from_rendered(...)
```

保证 round-trip 后仍是 Serialization。

---

## 15. `is_retry_vetoed` 是共享的否决器

有些调用点不经过完整 SamplerActor retry loop，例如辅助请求。

为了避免分类漂移，`SamplingError` 自身定义：

```rust
is_retry_vetoed()
```

当前包括：

- server 明确 `should_retry = false`；
- context-length overflow。

这使主 Sampler、`/btw` 或其他 one-shot caller 可以共享同一条“绝不能原样重试”的规则。

---

## 16. 图片错误为什么是特殊恢复

413 或 “Could not process image” 不直接进入普通 backoff。

系统选择：

```text
strip inline images
    -> retry modified request
```

因为这里真正改变了 payload。

即使服务端返回 `should_retry: false`，图片 strip 检查也优先于 veto。

这不是违反 server hint，而是：

```text
server 说原请求不值得重试
客户端生成了一个不同的新请求
```

如果没有图片可删，则升级为 Fatal。

---

## 17. Connection reset 为什么也可能触发图片 strip

有些 nginx/proxy 在 body 太大时不返回标准 413，而是在上传阶段直接 reset connection。

`is_likely_body_rejected` 检测：

- request/body write error；
- 但排除 timeout；
- 排除 connect failure。

若 request 中有图片，普通 retry 前会主动 strip。

这是基于 transport symptom 的保守恢复，但不会对任意网络错误删图片。

---

## 18. 第一次 transport retry 为什么重建 HTTP/1.1 Client

generic retryable failure 的第一次 retry 返回：

```rust
RetryWithClientRebuild
```

执行层会：

1. backoff；
2. clone config；
3. 设置 `force_http1 = true`；
4. 创建 fresh SamplingClient；
5. 后续尝试使用新 Client。

目的不是每次 retry 都重建，而是逃离可能已 poisoned 的 HTTP/2 connection pool。

如果重建失败，源码告警并继续使用原 Client，而不是让“恢复动作本身失败”覆盖原始错误。

---

## 19. `AttemptOutcome` 隔离一次尝试

单次尝试有五种输出：

```rust
Completed { response, metrics }
Empty { context }
Failed { error }
Cancelled
InitFailed { error }
```

这里把“建立 stream 失败”与“stream 中途失败”分开，但两者最后都可进入同一个 classifier。

`Empty` 单独存在，是因为一次完整 HTTP/SSE 请求可能技术上成功，却没有模型可用内容。

---

## 20. 为什么 terminal event 要由 retry loop 延迟发送

Layer-2 stream 可能产生 Completed 或 Failed。

`run_one_attempt` 不直接把 terminal event 转发给 Session。

它先把 terminal 转成 `AttemptOutcome`。

只有整个 retry loop 决定接受结果后，才发送唯一：

```rust
SamplingEvent::Completed
```

最终放弃时才发送：

```rust
SamplingEvent::Failed
```

否则 Session 会看见：

```text
attempt 1 failed
attempt 2 failed
attempt 3 completed
```

并可能把前两个失败误当 Turn 终态。

---

## 21. 非 terminal streaming event 为什么仍实时转发

Text、Reasoning、ToolCallDelta 等非终态事件会立即发给 Session。

这样 UI 仍可流式显示。

代价是 retry 发生前可能已经展示了 attempt 的部分输出。

为此 Sampler 维护：

```rust
output_observed: AtomicBool
```

某些 retry policy 配置为：

```text
retry_only_before_output
```

一旦已有用户可见输出，就把 effective retry budget 降为 0，避免第二次 attempt 在旧内容后又生成一套冲突输出。

---

## 22. `tee_errors` 为什么保存第一份 raw error

Layer-2 stream 会把 `SamplingError` 转换成可发送的 `SamplingErrorInfo`。

这个转换可能损失：

- reqwest 细节；
- 原始 status/error variant；
- retry header；
- model metadata。

`tee_errors` 在 raw stream 上旁路捕获第一份 rich error，同时不改变原 stream。

当 L2 报 Failed 时：

- 有 raw error → 使用 raw；
- 没有 → 从 `SamplingErrorInfo` 重建。

为什么只保留第一个？

stream 断裂后的后续错误通常只是同一故障的二次效应。

---

## 23. stream 无 terminal event 怎样处理

正常 L2 stream 必须以 Completed 或 Failed 结束。

如果直接返回 `None`，源码合成：

```rust
EventStreamError("stream dropped without terminal event")
```

它是 retryable transport-like failure。

这防止 producer 意外 drop 后，consumer 永久等待或把不完整 Response 当成功。

---

## 24. Empty Response 为什么不等于空字符串

`ConversationResponse::empty_reason()` 会综合判断：

- 是否有 assistant text；
- 是否有 Tool Call；
- 是否只有 reasoning；
- 是否真的看到 choice；
- finish reason；
- token usage。

`EmptyResponseContext` 把这些信息带到日志和最终错误。

ContentFilter 是例外：

它可能合法地没有内容，但属于 deterministic refusal，不应 retry-storm。

---

## 25. MaxTokensTruncation 为什么不自动 retry

当 stop reason 是 Length，Sampler 返回：

```rust
SamplingError::MaxTokensTruncation
```

它不被普通 retry。

原因是重新发送相同上下文和 output cap，不保证得到更短答案，还可能重复消耗。

上层通过稳定 `error_kind` 标记把它映射为 Turn 的 `MaxTokens` 终态，供 UI、hook 和持久化区分一般 Error。

---

## 26. Doom Loop 是什么

Doom Loop 指模型输出表现出高度可信的循环模式。

服务端 Responses stream 可提供 `DoomLoopSignal`。

Sampler 的 policy 判断哪些 trigger 足够 confident。

一旦确认，本次 attempt 被视为 poisoned：

```text
即使它有文本、甚至已经 Completed，也不能直接接受
```

错误携带：

- trigger labels；
- mid-stream abort 的 chunk index。

它不携带完整 generation 内容，避免 telemetry 泄漏。

---

## 27. Doom retry 为什么有独立预算

`doom_retry_count` 与普通 `retry_count` 分开。

原因是：

- transport retry 解决网络/服务故障；
- doom resample 解决随机采样质量问题。

二者不应互相消耗预算。

Doom backoff 只有 0–250ms 小 jitter。

因为新的随机 sample 本身就是恢复手段，长时间等待不会改善结果。

---

## 28. Doom 预算耗尽后为什么接受下一次输出

预算用尽后，Sampler 会 disarm doom abort，让下一次 attempt 完成并按原样接受。

否则系统可能永远拒绝某类模型输出。

此时会记录：

```text
accepted_after_budget
```

这是一个明确的 bounded recovery：

```text
先尝试改善质量
    -> 但不能无限阻塞用户
```

对于 `retry_only_before_output` 的 workflow child，一旦已有输出则不会进行 doom resample，避免输出拼接污染。

---

## 29. Retry 事件怎样到达 UI

每次重试，Sampler 发：

```rust
SamplingEvent::Retrying {
    attempt,
    max_retries,
    kind,
    reason,
    ...
}
```

Session 的 event handler：

- 记录 inference retry telemetry；
- Doom 时更新 capture 与 signals；
- 转换为 `XaiSessionUpdate::RetryState::Retrying`；
- 向 Client 发通知。

所以 Retry 不是静默等待。

用户可以看到“正在重试”，观测系统也能区分失败种类和次数。

---

## 30. Sampler 失败为什么还要交给 `handle_sampling_failure`

Sampler 只知道 request、HTTP 与 retry policy。

Session 还知道：

- 当前 Conversation 与 token 估算；
- AuthManager；
- model/provider/BYOK 类型；
- available model catalog；
- compaction state；
- 当前是否是 budgeted workflow child；
- Client 应收到什么 UI notification。

因此最终 rich error 转成 `SamplingErrorInfo` 后进入：

```rust
SessionActor::handle_sampling_failure
```

---

## 31. Session failure handler 的决策顺序

顺序很重要，大致为：

```text
budgeted workflow child?
    -> fail closed on usage

retry-only-before-output child?
    -> mark usage incomplete + fail

context overflow can compact?
    -> compact + resubmit

encrypted_content mismatch?
    -> friendly terminal

rate limit exhausted?
    -> rate-limit terminal

401 eligible for session/provider recovery?
    -> refresh + resubmit

special empty/idle telemetry
legacy auth / model 404 decoration
    -> RetryState::Failed + ACP Error
```

越靠前的分支越具体，避免被后面的通用映射吞掉。

---

## 32. Context overflow 为什么由 Session 处理

Context-length error 是 deterministic，Sampler 不应原样 retry。

Session 可以改变请求历史：

1. 从 error metadata 读取真实 context window；
2. 更新本地 sampling config；
3. 计算当前 token percentage；
4. 运行 compact-only；
5. 返回 `CompactAndResubmit`；
6. Turn 外循环重新 build request。

这里 retry 的不是同一个 payload，而是压缩后的新 Conversation。

---

## 33. Compaction failure 怎样升级

如果 recovery compaction 本身失败：

- auth-shaped compaction error → 走专门 auth surface；
- 其他错误 → 返回该 ACP error，Turn 失败。

不能在 compaction 失败后继续提交原 oversized request。

否则：

- 已知会再次 context overflow；
- 可能形成 compaction/request 无限循环；
- 错误原因被后续 400 覆盖。

---

## 34. Encrypted-content mismatch 为什么不能修复

模型历史可能包含另一个 model family 生成的 `encrypted_content`。

当前模型无法解密时，API 返回 400。

这不是 transient failure，也不是普通 compaction 可修复的问题。

Session 将其映射为：

```text
encrypted_content_mismatch
```

并提示开始新 Session。

这里采用 fail-closed：不能静默删除 encrypted reasoning 后继续，因为那会改变 Conversation 语义。

---

## 35. 401 为什么有两条恢复路线

Session 根据 model auth facts 选择：

### Session-token route

使用 `AuthManager` 管理的 OIDC/external session credential。

### Auth-provider route

模型来自 `[auth_provider.*]`，token 由 provider command/mint helper 生成并写入 chat-state credentials。

两者互斥。

源码有 `debug_assert` 防止同一个 model 同时进入两种恢复。

这是为了避免把第三方 BYOK endpoint 的 token 错误替换成 xAI session bearer。

---

## 36. Session token auth gate 怎样防止 credential 泄漏

Gate 综合：

- auth method 是否 session-based；
- model 是否 BYOK；
- endpoint 是否 first-party。

明确 BYOK 的模型不能使用 session token。

当 BYOK 状态 Unknown 时，只允许 first-party endpoint 继续 session-token refresh。

这样既能修复模型 catalog 暂时 Unknown 导致的 stale 401，又不会把 session bearer 发给未知第三方 URL。

---

## 37. Pre-turn auth refresh 与 401 recovery 的关系

Turn 前会调用：

```rust
refresh_token_if_expired()
```

这是 proactive recovery。

如果 soft refresh 失败但旧 token 仍在 grace window，可 optimistic send。

服务端真的返回 401 后，再调用 unauthorized recovery。

所以是两层：

```text
near-expiry proactive refresh
    -> 尽量避免失败

server-rejected reactive recovery
    -> 失败后的安全网
```

---

## 38. `UnauthorizedRecovery` 的四步状态机

401 后依次尝试：

```text
ReloadFromDisk
    -> RefreshFromAuthority
    -> DevboxRecovery
    -> Done
```

### ReloadFromDisk

另一个进程可能已刷新 `auth.json`；如果磁盘 token 与 rejected token 不同，直接采用。

### RefreshFromAuthority

按 token type 调用 OIDC refresh、external provider 等权威来源。

### DevboxRecovery

特定 devbox 环境可清理旧认证并重新 mint。

### Done

所有策略耗尽，返回 terminal AuthError。

---

## 39. Fresh-mint guard 防什么

Token 刚刚刷新后，旧请求可能仍在飞行，并稍后返回 401。

如果每个迟到 401 都再次 refresh：

```text
mint token B
old request A returns 401
mint token C
request B returns delayed 401
mint token D
...
```

会形成 refresh storm。

Fresh-mint guard 在一个时间窗口内拒绝对刚 mint 的 token 再 mint。

它允许系统识别“这是旧请求的迟到失败”，而不是“新 token 一定坏了”。

---

## 40. Team-pin gate 为什么在 recovery 末尾

即使磁盘 reload 或 authority refresh 成功，新 credential 也可能属于错误团队。

Recovery 在接受前检查 pinned team policy。

不匹配时：

- reject；
- clear credential；
- 返回明确错误。

否则一次 401 recovery 可能把已经锁定团队的 Session 悄悄切换到另一个组织。

---

## 41. Auth provider 的恢复为什么最多重 mint 一次

Provider-backed model 的 401 路径：

1. 读取 rejected chat-state key；
2. 有 key → `recover_rejected_token`；
3. 无 key → cold `ensure_fresh_token`；
4. 得到新 key → 写回 chat state；
5. rebuild SamplerConfig；
6. resubmit。

Provider helper 自身有 fresh-mint guard。

如果 helper 拒绝或失败，401 直接进入 terminal surface，而不是无限调用外部 provider command。

---

## 42. 为什么成功 refresh 后还需要 `AuthRetrySchedule`

“Refresh API 返回成功”不保证下一次模型请求一定成功。

可能发生：

- token 尚未传播；
- wire 上没真正带 token；
- 服务端仍拒绝；
- provider 连续返回同一个坏 token；
- machine sleep 后旧 incident 恢复。

所以 Turn 外循环维护独立 post-recovery 401 budget。

这和 Sampler transport retry budget 是第三套不同预算。

---

## 43. Credentialed 401 的 1s/2s/4s 预算

`AuthRetrySchedule::MAX_RETRIES = 3`。

延迟必须是：

```text
1s -> 2s -> 4s
```

源码特别记录了 `tokio_retry::ExponentialBackoff` 的参数陷阱。

错误使用 `from_millis(1000)` 会产生指数的底数误解，第二步可能变成 1000 秒，第三步变成数天。

当前实现用：

```text
from_millis(2).factor(500)
```

得到想要的 1/2/4 秒。

---

## 44. Missing credential 为什么不立即收费

Recovery 成功后，下一请求若没有 credential 上 wire，不能说“新 token 被拒绝”。

它可能只是 refresh 尚未落入 resolver。

因此返回：

```rust
UnchargedResubmit
```

Session 会：

- 最多等 15 秒让 wire-valid token 落地；
- 或至少 sleep 1 秒；
- 再提交。

但不收费不等于无限。

---

## 45. Runaway guard 为什么设为 50

连续 credential-less 401 可能跨越 laptop suspend/resume。

源码允许最多 50 次 uncharged resubmit，大约覆盖长时间合盖场景。

超过后返回：

```rust
RunawayGuard { rejections }
```

否则“没有 token 所以不收费”的规则会变成永久循环漏洞。

---

## 46. Suspend reset 为什么有 cap

系统用 wall clock 与 monotonic clock 的漂移识别 suspend。

如果一个 auth incident 跨过睡眠，允许重置 charged retry budget，因为 wake 后可能已经是新的认证环境。

但 reset 也限制为 8 次。

否则每次合盖都能把同一个永久故障重新变成“全新 incident”，永远无法 terminal。

---

## 47. 401 Attribution 解决“刷新为何没用”的诊断问题

只记录 401 数量不足以判断：

- 请求发的是旧 token snapshot；
- 请求发的是 AuthManager 当前 token，但服务端仍拒绝；
- 请求根本没带 token；
- token 已 hard-expired；
- 哪个 API consumer 在失败。

因此 sampler 在六个 401 endpoint 调用：

```rust
Auth401AttributionCallback::record_401
```

只传 bearer 尾部 fragment，不跨 crate 暴露完整 secret。

---

## 48. Attribution payload 包含什么

Shell 将 wire bearer 与 AuthManager 当前/过期 token 关联，生成：

- `sent_key_prefix`；
- `current_key_prefix`；
- `mint_age_seconds`；
- `expires_at_seconds_from_now`；
- `consumer`；
- `is_stale_snapshot`。

`is_stale_snapshot` 只有在：

```text
确实发送了 bearer
并且它和 manager 当前持有的 token 不同
```

才为 true。

“没有发送”和“manager 为空”不被误判为 stale snapshot。

---

## 49. 为什么读取 `current_or_expired`

401 最常在 token 已 hard-expired 时到达。

如果 attribution 使用 `current()`，过期 token 会被过滤为 None。

日志就无法回答：

```text
服务端拒绝的 token，是否就是进程仍持有的过期 token？
```

所以诊断读取 `current_or_expired()`。

它只用于归因，不表示过期 token 可以继续作为 wire-valid credential 使用。

---

## 50. Attribution 为什么有两个 sink

### Unified log

本地 `unified.jsonl`，便于 Session 级诊断和 artifact 上传。

### 独立 OTel span

`auth_401_attribution`，便于服务端聚合查询。

使用独立 warn span 而不是普通 tracing event，是因为后台 task 或 `spawn_blocking` 可能没有 parent span。

独立 span 即使没有父上下文也能被 OTel layer 捕获。

---

## 51. Auth 恢复失败怎样变成用户动作

`AuthManager::auth_remedy()` 会根据失败状态生成：

- stable turn error type；
- 可选用户建议。

`apply_auth_remedy` 把 advice 追加到错误信息。

例如可能提示重新登录，而不是只显示：

```text
HTTP 401
```

同时 terminal failure 记录：

- auth mode；
- status；
- reauthable；
- key suffix；
- expiry；
- scrubbed/capped message。

---

## 52. 为什么 terminal failure 要先发 `RetryState::Failed`

ACP `session/prompt` 最终会返回 Err。

但 UI 的 pager、重认证 banner 与 turn-failed block 依赖 session notification。

所以每个 terminal failure 都欠客户端一次：

```rust
RetryState::Failed { error_type, message }
```

源码特别修复过 auth retry budget exhausted 只返回 ACP Error、没有 Failed notification 的路径。

没有该通知，Turn 在 UI 上会“静默死亡”。

---

## 53. Rate limit 为什么使用独立 ACP code

Rate limit 映射到稳定 code：

```text
-32003
```

而不是普通 internal error。

这样 Client 可：

- 显示自己的 quota/upgrade UI；
- 不把它当 generic server crash；
- `prompt_complete` 使用 `stopReason = rate_limit`；
- 省略重复 server detail。

服务端 detail 仍可按 OAuth/API-key 语境重写成正确的用户文案。

---

## 54. ACP error mapping 为什么不是简单 `to_string`

`map_sampling_err_to_acp` 保留协议语义：

| Sampling failure | ACP 映射 |
| --- | --- |
| Auth / 401 | `auth_required` |
| InvalidConfiguration / Serialization | `invalid_params` |
| 400 / 413 | `invalid_params` |
| 404 | `resource_not_found` |
| 429 | stable rate-limit code |
| 403 | `internal_error`，但保留 policy 文案 |
| overload | 统一短文案 |
| stream/http failure | `internal_error` |

5xx 的 data 还保存 `http_status`，供后续 hook/goal classifier 使用。

---

## 55. 为什么 error data 要保留结构

ACP Error 的 `data` 可包含：

```json
{
  "message": "...",
  "http_status": 503,
  "error_kind": "max_tokens_truncation",
  "promptUsage": {...}
}
```

这些字段服务不同消费者：

- UI 取 message；
- hook 分类 status；
- TurnCompleted 判定 MaxTokens；
- error path 保留 usage；
- telemetry 做聚合。

如果只把所有信息拼成字符串，后续只能脆弱地 regex。

---

## 56. Error path 为什么仍要附加 usage

失败的模型请求也可能产生 token 花费。

Turn 失败不应让 usage 消失。

`attach_prompt_usage` 在 ACP Error data 上附加 `promptUsage`。

如果原 data 是 string 或其他 JSON，它会规范化为 object，并保留原 message。

Turn completion emission 优先从 error 取 usage；没有则查询 fallback ledger。

---

## 57. Workflow child 为什么对 usage 更严格

Budgeted workflow child 有输出 token grant。

若采样失败，实际消费可能未知。

它不能像普通 interactive Turn 一样乐观继续，否则可能突破父任务预算。

因此：

- budgeted child → usage closed/fail-closed；
- retry-only-before-output child → 标记 usage incomplete；
- Doom recovery 也可能禁用。

这里优先保证成本/预算正确性，而不是最大化自动恢复。

---

## 58. Turn 外循环怎样接受 recovery outcome

`run_turn_via_sampler` 返回：

```rust
Response
CompactAndResubmit
RefreshAuthAndResubmit { credential, store }
```

Turn 主循环处理：

- Response → 继续 Tool/Chat commit；
- CompactAndResubmit → reset auth incident 并 `continue`；
- RefreshAuthAndResubmit → 查询 AuthRetrySchedule、pace、再 `continue`；
- Err → 结束 Turn。

所以 Session recovery 不会递归调用整轮函数，而是返回显式控制信号给外循环。

---

## 59. 为什么成功 Model Response 会 reset auth incident

一旦得到成功 Response，说明当前 credential 与 endpoint 组合已经工作。

之前所有：

- charged 401；
- uncharged resubmit；
- suspend reset；

都不应污染下一次独立 incident。

因此 `reset_on_success()` 重建完整 AuthRetrySchedule。

---

## 60. Tool failure 为什么通常不让 Turn 失败

模型调用 Tool 是 Agentic Loop 的正常探索过程。

Tool 失败可能意味着：

- 参数 JSON 错；
- 文件不存在；
- shell command 非零；
- permission denied；
- MCP server unavailable；
- 网络错误。

通常正确做法是把失败转换为：

```text
ToolCallUpdate(status = Failed)
ToolResult(error text)
```

然后让模型看到错误并决定：

- 修参数；
- 换路径；
- 换工具；
- 向用户解释。

这叫 model-recoverable failure。

---

## 61. Tool parse error 怎样回填

`handle_tool_parse_error` 会：

1. 记录 typed tool failure；
2. 发 ToolCall Failed update；
3. 生成模型可行动的错误文案；
4. 包含原始 arguments 的 capped prefix；
5. 如果 JSON 非法，附加 serde 的位置与原因；
6. push 一个 ToolResult 进入 Chat State；
7. 返回 `Ok(())` 让 Agentic Loop 继续。

`Ok(())` 不是“Tool 成功”，而是“失败已经被正确编码进 Conversation”。

---

## 62. Tool 401 为什么只自动重试一次

`call_with_auth_retry`：

```text
call tool
    -> non-auth error: return
    -> auth error + AuthManager:
         try_recover_unauthorized(Background)
             -> success: call once more
             -> failure: return original result
```

它不像 model Turn 有 1/2/4 秒的多次 post-recovery budget。

因为 Tool Call 自身会回填模型；持续失败后模型或用户可决定下一步。

无限自动重试 Tool 还可能重复外部副作用。

---

## 63. 同一 Tool batch 的 401 怎样去重 refresh

多个并发 Tool Call 可能同时收到 401。

`shared_recovery` 使用：

```rust
tokio::sync::OnceCell<bool>
```

所有调用共享：

```rust
get_or_init(|| am.try_recover_unauthorized(...))
```

只有一个实际 refresh future 执行，其他 Tool 等待同一 bool 结果。

这是一种 singleflight。

它防止同一 batch 的十个 401 同时刷新 token 十次。

---

## 64. ToolError 何时会升级成 Turn Error

常规执行/解析错误被模型消费。

但以下情况可能升级：

- Tool Runtime/actor 的控制通道整体不可达；
- 关键内部不变量被破坏；
- Turn 被 cancel/shutdown；
- budgeted task usage 无法确认；
- Session future panic；
- hook/permission gate 返回必须终止的控制结果。

判断关键不是“Tool 是否成功”，而是：

> 失败能否被表示成一个合法 ToolResult，并让 Conversation 继续保持协议完整？

---

## 65. 为什么 dangling Tool Call 必须修复

Conversation 协议要求 assistant Tool Call 与 ToolResult 配对。

取消、crash 或 torn history 可能留下：

```text
Assistant(tool_call id=abc)
    // missing ToolResult
```

或：

```text
ToolResult(id=abc)
    // owning ToolCall missing
```

下一次 API 请求可能返回 400。

运行中取消路径会插入 synthetic ToolResult；磁盘级 corruption 则由 repair 处理。

---

## 66. Session Repair 解决什么“无法在带内恢复”的问题

典型坏历史：

```text
unexpected tool_use_id found in tool_result blocks
```

每次模型请求都会 400。

Compaction 也需要模型请求，所以同样失败。

因此必须有 out-of-band extension：

```text
x.ai/session/repair
```

它不依赖一次正常 Agent Turn。

---

## 67. Repair 做哪些规范化

`repair_history` 返回 `HistoryRepairReport`，包括：

- 删除重复 ToolResult；
- 删除 orphaned/displaced ToolResult；
- 为未回答 ToolCall 插入 synthetic result。

它应该满足：

```text
repair(repair(history)) == repair(history)
```

即幂等。

dry-run 则在副本上计算报告，不修改内存或磁盘。

---

## 68. Resident Repair 为什么走 Actor rail

如果 Session resident：

```text
extension
    -> session_handle_waiting_for_load
    -> SessionCommand::RepairHistory
    -> SessionActor
    -> ChatStateActor
```

不能直接改磁盘，因为内存 Conversation 仍是权威运行态。

直接改盘会造成：

```text
disk repaired
memory still corrupt
next persistence write restores corruption
```

---

## 69. Resident Repair 为什么拒绝 mid-turn

Turn 进行中时，某些 Tool Call 合法地尚未有 ToolResult。

此时 repair 会把它们误判为 dangling 并插 synthetic result。

所以两层检查：

1. Session 的 `session_turn_active` fast path；
2. ChatStateActor command handler 再检查同一 per-session flag。

第二层才是 race-free authority。

如果只在 extension handler 外面检查，检查完成到 actor mutation 之间可能刚好开始新 Turn。

---

## 70. Non-resident Repair 为什么走 disk rail

没有 resident handle 时：

1. 按 SessionId 找 summary；
2. 使用 resume 同类的 corruption-tolerant loader；
3. 应用 legacy upgrade；
4. repair in memory；
5. 非 dry-run 时 atomic replace `chat_history.jsonl`；
6. 返回详细 report。

它不需要为了修复而启动完整 SessionActor、MCP、Tool Runtime 与 model client。

---

## 71. Repair racing load 为什么先等待

Extension 使用：

```rust
session_handle_waiting_for_load
```

若 repair 与 reconnect load 并发，它会等 attach settle。

否则可能出现：

```text
load 正在构建内存 Conversation
repair 查不到 resident -> 改磁盘
load 使用修复前已读出的旧快照 -> 注册 actor
```

等待能确保选择正确 rail。

---

## 72. Persistence Actor 为什么大量使用 best-effort

`SessionPersistence::run` 对许多消息采用：

```text
attempt write
    -> warn on error
    -> keep actor alive
```

例如：

- generated title；
- signals；
- announcement state；
- git HEAD；
- feedback；
- summary patch；
-普通 update/chat append。

理由是一次辅助状态写失败不应直接杀死正在运行的 Agent。

如果磁盘短暂异常，后续写入仍可能成功。

---

## 73. Best-effort 的代价是什么

告警后继续意味着：

- live UI 可能看见未落盘的 update；
- crash 后 replay 缺事件；
- summary counter/title 可能落后；
- 某些 signal 不可恢复。

因此 best-effort 只适合：

```text
失败不会让调用者基于错误的“已提交”事实执行不可逆动作
```

需要严格交付语义的路径必须使用 ack/commit-aware API。

---

## 74. `AppendUpdateDurablyAndAck` 为什么更严格

`PersistenceHandle::append_update_durably`：

1. 把 durable append 放到旧 queued writes 之后；
2. 等 PersistenceActor；
3. 要求 storage 执行 durable commit-aware append；
4. 返回详细结果。

错误区分：

```rust
NotCommitted
Committed
AcknowledgementLost
```

这三个状态对 retry 完全不同。

---

## 75. 为什么必须区分 Committed 与 NotCommitted

写盘可能分成：

```text
append bytes
sync file
update summary/bookkeeping
send ack
```

如果 append 已成功，但 summary update 失败：

```text
记录已经 committed
```

调用者若盲目 retry，会写入重复事件。

如果 append 本身没发生，retry 才安全。

如果 actor 在 commit 后、ack 前退出，调用者只知道 `AcknowledgementLost`，提交状态未知，必须采用 idempotency key 或重新读取确认，而不是猜测。

---

## 76. `FlushAndAck` 真正保证了什么

队列顺序上，它保证：

```text
FlushAndAck 之前进入 PersistenceActor mailbox 的消息
    已被 actor 处理到 flush 点
```

但当前 `flush_pending()` 对 `drain_pending` 和 file sync 错误主要是 logging，不把 `io::Error` 放进 ack；`FlushAndAck` 的 response 类型也是 `()`。

因此严格来说，当前 ack 证明的是：

```text
persistence actor 已执行 barrier
```

而不是对所有前序写入提供可传播的“磁盘成功证明”。

需要 disk-commit 精确语义的代码应使用 durable/acknowledged message，而不是仅依赖 `FlushAndAck`。

这是阅读源码时值得特别记住的边界。

---

## 77. Repair 的 flush 注释与实现怎样理解

Resident repair 在 chat-state actor 中触发 `ReplaceChatHistory`，然后等待 `FlushAndAck`。

源码意图是：

```text
返回 repair success 前，rewrite 已经到达 persistence barrier
```

但由于 `ReplaceChatHistory` 写失败只 warn，Flush ack 不携带 write result，当前调用者无法区分真正写盘成功与 actor 已处理但写失败。

因此学习时应分开：

- **排序保证**：成立；
- **错误传播保证**：对这个具体路径并不完整。

如果将来强化，应让 ReplaceChatHistory 支持 acknowledged `io::Result<()>`。

---

## 78. 初始化/加载的 I/O 错误为什么直接返回 ACP Error

创建或 load Session 时，Persistence 尚未成为后台 best-effort subsystem。

如果连 session directory、summary 或 history 都无法初始化，就没有可信 Session 可运行。

因此 constructor/load error 通过：

```rust
io_error_to_acp
```

转换为稳定 code：

- `FS_DISK_QUOTA_EXCEEDED`；
- `FS_NOT_FOUND`；
- `FS_PERMISSION_DENIED`；
- `FS_OTHER`。

这里是 fail-closed。

---

## 79. 为什么错误文案与稳定 code 要分开

用户需要可读文案：

```text
No space left on device
```

Telemetry 与自动化需要稳定机器码：

```text
FS_DISK_QUOTA_EXCEEDED
```

直接把 `io::Error::to_string()` 当分类会受：

- 操作系统；
- locale；
- filesystem；
- Rust 版本；

影响。

所以边界层同时提供 human message 与 stable data code。

---

## 80. JSONL 为什么允许 corruption-tolerant load

append-only JSONL 不是 crash-atomic。

进程 kill 或 ENOSPC 可能留下半行。

Loader 可跳过无法解析的 torn line，使较早完整记录仍能恢复。

但 tolerance 可能造成工具配对缺口：

```text
ToolCall line 完整
ToolResult line torn and skipped
```

所以 load tolerance 与 history repair 是互补关系：

- tolerance 让 Session 尽量能打开；
- repair 让恢复后的 Conversation 再次满足 API 协议约束。

---

## 81. Full-file replace 为什么使用 temp + rename

Chat history compaction/repair 是重写整个文件。

若直接 truncate target 再写：

```text
crash midway -> 原文件与新文件都失去
```

源码先写 temp，再 rename 覆盖 target。

失败时旧 target 仍保留。

这提供 crash atomicity，但不代表 directory entry 已 durable 到掉电级别；更严格场景还需要 fsync file/dir。

---

## 82. SessionActor panic 后发生什么

Actor thread/JoinHandle 由 Registry 持有。

Supervisor 周期执行 `sweep_dead_sessions`。

若发现：

```text
thread finished + session still resident
```

则视为 unexpected exit：

- 标记 `DeadFailed`；
- 移除 runtime resources；
- 保留磁盘 Conversation 可恢复性。

若 thread finished 且已 non-resident，则视为预期 clean exit，只清 thread。

---

## 83. Actor death 为什么不删除 Session

SessionActor 是当前进程中的执行实例，不是 Session 历史本身。

历史已分离持久化到：

- chat history；
- updates；
- summary；
- signals；
- rewind points。

Actor panic 后删除 Session 会把 runtime failure 变成数据丢失。

当前策略是：

```text
reap dead runtime
    -> 允许之后 session/load 重建 actor
```

能恢复到哪里取决于最后成功落盘的边界。

---

## 84. `session failed to respond` 表示什么

协议层等待 SessionCommand oneshot 时，如果 sender 被 drop，会返回：

```text
session failed to respond
```

它不等于模型返回错误。

更可能表示：

- SessionActor 已退出；
- command handler panic；
- respond_to 在未发送结果前被 drop；
- shutdown/cancel path 漏了显式 completion。

源码中 queue clear 特别对被移除 prompt 显式返回 Cancelled，避免 bare drop 被误报成 Turn failed。

---

## 85. Turn completion 为什么必须 ownership-gated

Cancel path 可能已经终结 Prompt，旧 turn task 随后才返回 stale completion。

`handle_completion` 检查：

```text
completion prompt_id 是否仍是 queue front / running prompt
```

只有 owned completion 才：

- dequeue；
- resolve Client oneshot；
- clear running task；
- emit TurnCompleted。

未知 prompt completion 只告警并丢弃。

否则同一 Prompt 会产生两个 terminal，甚至旧 completion 会清掉新 promoted Turn 的 task。

---

## 86. 为什么既有 `prompt_complete` 又有 `TurnCompleted`

### `x.ai/session/prompt_complete`

从 `MvpAgent::prompt` 发出的 fire-and-forget notification，服务当前实时 Client。

### Durable `TurnCompleted`

从 SessionActor 的 completion chokepoint 发出，进入 persistence + replay rail。

它保证中途断开的客户端 load 后仍能知道 Turn 已结束，不会永远停在 Waiting。

两者都调用 `prompt_complete_fields`，避免 stop reason 与 error detail 漂移。

---

## 87. Error terminal 怎样分类给 Hook

`stop_failure_error_type` 优先读取结构化 marker/status，再看 JSON-RPC code。

可得到：

- RateLimit；
- AuthenticationFailed；
- InvalidRequest；
- ServerError；
- MaxOutputTokens；
- Unknown。

为什么 status 优先？

多个 HTTP 错误都可能包装成 ACP internal error，但 `http_status` 仍能区分 403、503、529。

Hook 得到稳定分类后可以选择不同 cleanup/notification 策略。

---

## 88. Goal mode 怎样对 infra failure 降级

Turn error 若属于 rate-limit、auth 或 internal infra，Goal runtime 不会盲目继续自动循环。

它可暂停 Goal，并提示：

```text
/goal resume
```

这样避免后台 goal 在服务不可用时无限消耗 retry/请求。

但这属于上层 orchestration degradation，不改变原 Turn Error 的协议终态。

---

## 89. Fail-open、Fail-closed 与 Best-effort 的区别

### Fail-open

辅助机制失败时继续主流程。

例如某些 classifier/hook/telemetry 辅助路径。

### Fail-closed

无法证明安全/预算/权限正确时终止。

例如 permission、budget usage、Session init、team pin mismatch。

### Best-effort

尝试完成非关键副作用，失败记录但不改变主结果。

例如部分 telemetry、title、signals persistence。

不要把三者混为“忽略错误”。

选择依据是失败后继续会破坏什么不变量。

---

## 90. 一个 transport 503 的完整恢复时序

```text
HTTP/SSE returns 503
    -> SamplingError::Api(status=503)
    -> classify_error: retryable
    -> first retry: backoff + rebuild HTTP/1.1 client
    -> emit RetryState::Retrying
    -> later retries: exponential backoff

if success:
    -> only one SamplingEvent::Completed
    -> Turn continues

if budget exhausted:
    -> SamplingEvent::Failed
    -> handle_sampling_failure
    -> RetryState::Failed
    -> ACP Error(data.http_status=503)
    -> durable TurnCompleted(stopReason=error)
    -> Goal may auto-pause as infra
```

---

## 91. 一个 401 的完整恢复时序

```text
wire response 401
    -> attribution callback records sent/current token suffix
    -> SamplingError Auth/Api401
    -> Sampler emits to Session, no generic retry
    -> Session auth gate selects session-token or provider route
    -> UnauthorizedRecovery:
         disk reload -> authority refresh -> devbox fallback
    -> rebuild sampler config
    -> AuthRetrySchedule decides uncharged / 1s / 2s / 4s
    -> Turn resubmits

if model response succeeds:
    -> reset auth incident

if repeated 401 exhausts:
    -> apply AuthRemedy
    -> RetryState::Failed
    -> ACP Error
    -> TurnCompleted
    -> reauth UI
```

---

## 92. 一个 malformed Tool Call 的完整恢复时序

```text
Model emits invalid tool arguments
    -> ToolError(parse)
    -> ToolCallUpdate(Failed)
    -> build actionable error with original capped JSON
    -> ChatState.push_tool_result(error)
    -> Agentic Loop samples again
    -> Model fixes arguments or chooses another tool
```

整个 Turn 可以最终成功。

这说明 Tool failure 与 Turn failure 不应共用同一个 boolean。

---

## 93. 一个 corrupted history 的完整恢复时序

```text
session/load skips torn JSONL line
    -> next model request gets tool pairing 400
    -> ordinary retry vetoed
    -> compaction cannot call model either
    -> Client invokes x.ai/session/repair

if resident:
    -> SessionCommand -> ChatStateActor repair
    -> reject if mid-turn
    -> replace history + persistence barrier

if dormant:
    -> tolerant disk load
    -> repair
    -> atomic file replace

next session/load / prompt
    -> valid tool pairing restored
```

---

## 94. 最容易出现的误读

### 误读一：`is_retryable = true` 就一定会重试

还要看 budget、output observed、server veto 与具体 caller。

### 误读二：Sampler Fatal 就一定是 Turn Fatal

Auth/context overflow 仍可能由 Session 改变条件后 resubmit。

### 误读三：Tool Failed 就是 Prompt Failed

Tool failure 通常回填模型，Turn 可以继续。

### 误读四：FlushAndAck 等于所有磁盘写成功

当前实现主要提供队列 barrier；部分写入错误只告警。

### 误读五：Actor dead 等于 Session 数据消失

Actor 是 resident runtime，Session 可从磁盘重建。

### 误读六：403 应该重新登录

403 是 authenticated-but-forbidden，不是 credential rejection。

---

## 95. 修改 Retry 时的检查清单

- 新 error variant 是否更新 `is_retryable`？
- `clone_error` 是否保持原类别？
- `SamplingErrorInfo` round-trip 是否保持 retry semantics？
- `map_sampling_err_to_acp` 是否有映射？
- `stop_failure_error_type` 是否能分类？
- `prompt_complete_fields` 是否保持用户语义？
- error data 是否泄漏 server body/secret？
- 已有输出后是否允许 retry？
- 429 与 generic budget 是否仍分开？
- `x-should-retry: false` 是否被尊重？

---

## 96. 修改 Auth Recovery 时的检查清单

- 401 与 403 是否仍严格区分？
- BYOK 是否可能收到 session bearer？
- first-party Unknown fallback 是否受 endpoint gate？
- rejected credential provenance 是否保留？
- concurrent Tool 401 是否 singleflight？
- fresh-mint guard 是否防 refresh storm？
- Missing credential 是否 uncharged 但有 runaway cap？
- success 是否 reset incident？
- suspend reset 是否有上限？
- terminal path 是否发送 `RetryState::Failed`？
- attribution 是否只使用 token suffix？

---

## 97. 修改 Persistence/Repair 时的检查清单

- 这个写入是 best-effort 还是 correctness-critical？
- 调用者是否需要知道 NotCommitted/Committed/Unknown？
- ack 是否真的携带 `io::Result`？
- full-file rewrite 是否 temp + rename？
- repair 是否幂等？
- resident 与 dormant 是否走不同 authority rail？
- repair racing load 是否等待 attach？
- mid-turn repair 是否在 actor 内再次检查？
- disk full 是否映射稳定 code？
- tolerant load 跳过记录后是否可能破坏 Tool pairing？

---

## 98. 推荐验证的核心测试

### Sampler

- 503 第一次 retry 重建 HTTP/1.1 client；
- Serialization 不会 round-trip 成 retryable；
- `should_retry=false` 立即 veto；
- 413/image error 先 strip；
- 429 使用独立 cap；
- cancel 打断 backoff；
- retry attempts 只发一个 terminal；
- output observed 后禁止配置要求的 retry。

### Doom

- doom budget 不消耗 transport budget；
- mid-stream 与 completed signal 都能 poison attempt；
- budget 后 disarm 并接受；
- telemetry 不包含 generation body。

### Auth

- 403 不刷新；
- concurrent 401 只执行一次 refresh；
- Missing credential 不收费但触发 runaway guard；
- 1/2/4 秒 schedule 固定；
- suspend reset capped；
- success 清空 incident；
- BYOK endpoint 不注入 session token。

### Repair/Persistence

- orphan、duplicate、dangling 均被修复；
- repair 二次执行 no-op；
- dry-run 不写盘；
- mid-turn repair 被 actor 拒绝；
- disk repair atomic replace；
- durable append 区分 commit outcome；
- disk-full 映射稳定 ACP code。

---

## 99. 读完后应该建立的最终模型

可以把整套 Failure Runtime 压缩成十句话：

1. 底层错误必须保留结构，否则上层无法安全恢复。
2. Sampler 只重试不需要 Session 语境的 transient attempt failure。
3. 服务器 veto、context overflow 与 deterministic parse failure不能原样重试。
4. 一次 attempt 的 terminal 被 retry loop 吸收，Session 只看见最终 Completed/Failed。
5. Doom、transport、rate-limit 与 post-refresh 401 各有独立预算。
6. Session 用 Conversation、AuthManager 与 model metadata 执行 compact/auth 等高层恢复。
7. Tool failure通常变成模型可消费的 ToolResult，而不是直接杀死 Turn。
8. Persistence 按不变量选择 best-effort、acknowledged 或 commit-aware 语义。
9. Actor death只结束 resident runtime，持久化 Session 仍可 load/repair。
10. 所有无法恢复的 Turn 最终都必须产生一致的 ACP Error、RetryState 与 durable TurnCompleted。

---

## 100. Glossary：本文名词白话解释

| 名词 | 白话解释 | 在本文源码中的含义 |
| --- | --- | --- |
| failure runtime | 处理失败、重试、降级和恢复的运行时逻辑 | 跨 HTTP、Sampler、Session、Tool、Persistence 与 UI 的责任链 |
| error taxonomy | 对错误按性质分类 | Auth、HTTP、API、Stream、Empty、MaxTokens、Doom 等 |
| structured error | 不是纯字符串、而是带字段的错误 | `SamplingError` 保存 status、header、credential provenance 等 |
| attempt | 对模型 API 的一次实际请求尝试 | 一个 logical sampling request 可包含多个 attempt |
| sampling request | Sampler 接收的一次逻辑请求 | 内部可 retry 多次，但只产出一个最终 terminal |
| Turn | 一次用户 Prompt 驱动的完整 Agent 循环 | 可包含多轮模型请求与多个 Tool Call |
| terminal event | 表示一次请求/Turn 已最终结束的事件 | Completed、Failed、TurnCompleted 等 |
| transient | 条件可能随时间改变的暂时错误 | 网络中断、5xx、服务过载 |
| deterministic failure | 相同输入再次执行仍会失败 | Serialization、context overflow、部分 400 |
| retry | 在改变时间或执行环境后重新尝试 | Sampler transport retry、auth resubmit 等 |
| resubmit | Session 改变凭据或 Conversation 后重新提交 | compact/auth recovery 后重新进入 sampler |
| retry budget | 自动重试次数上限 | generic 15、rate-limit 2、auth 3、doom policy 独立 |
| backoff | 重试前逐渐增加等待时间 | 2/4/8/16/30s 或 auth 1/2/4s |
| exponential backoff | 等待时间按指数增长 | 减少故障服务压力 |
| jitter | 在 backoff 上加入随机扰动 | 防止大量客户端同时重试 |
| thundering herd | 大量客户端同一时刻重试造成的新冲击 | jitter 主要防止该问题 |
| retry classifier | 把错误映射为下一步动作的纯逻辑 | `classify_error` 返回 `RetryDecision` |
| retry veto | 即使表面可重试也明确禁止的条件 | `x-should-retry=false`、context overflow |
| `Retry-After` | 服务端建议的等待秒数 | 429/部分 API error 的 backoff 来源 |
| `x-should-retry` | 服务端对错误是否 transient 的提示 header | false 会阻止原样重试 |
| EmitToSession | Sampler 停止重试、交给 Session 决策 | 典型是 auth 与 encrypted history |
| Fatal | 当前层确认不再自动尝试 | 作为最终 sampling failure 返回 |
| rich error | 保留原始类型和字段的错误 | tee 从 raw stream 捕获的 `SamplingError` |
| error round-trip | 错误经序列化信息再重建 | 必须保持 Serialization 等 retry 语义 |
| SSE | Server-Sent Events 流式响应协议 | 模型 token 与 terminal event 的传输形式之一 |
| L2 transform | 将各 API 的 raw stream 归一化成 SamplingEvent 的层 | Chat/Responses/Messages 各有 transform |
| tee | 不改变主流、旁路观察或保存数据 | `tee_errors` 捕获第一个 raw error |
| output observed | 已有用户可见模型输出 | 某些 policy 下之后不允许 retry |
| EmptyResponse | 请求完成但没有可用 text/tool call | 携带 EmptyResponseContext，通常 retryable |
| ContentFilter | 模型因内容策略拒绝 | 可合法无内容，但不应重复采样 |
| MaxTokensTruncation | 输出达到 token cap 被截断 | 不做普通 retry，映射 MaxTokens 终态 |
| Doom Loop | 模型输出进入高置信循环 | 当前 attempt 被丢弃并快速 resample |
| poisoned attempt | 虽有输出但不能作为可信结果接受的尝试 | confident doom signal 的 attempt |
| disarm | 关闭某个检测/中止机制 | Doom budget 耗尽后允许 attempt 完成 |
| accepted after budget | 恢复次数用完后接受下一份输出 | 防止 doom detector 无限阻塞 |
| 401 | HTTP Unauthorized | credential 缺失、过期或被服务端拒绝 |
| 403 | HTTP Forbidden | 已认证但不允许执行，不能误触发 refresh |
| credential provenance | 请求在 wire 上到底带了什么凭据 | `SentCredential` 与 bearer suffix attribution |
| SentCredential | 凭据是否真的发送 | Sent、Missing、Unknown |
| BYOK | Bring Your Own Key | 某些 model 使用用户/第三方 key，不能注入 session bearer |
| first-party endpoint | xAI 自己控制的 API host | Unknown BYOK 时只在此允许 session-token fallback |
| proactive refresh | 请求前提前刷新即将过期 token | `refresh_token_if_expired` |
| reactive recovery | 服务端已返回 401 后的恢复 | `UnauthorizedRecovery` |
| UnauthorizedRecovery | 401 恢复状态机 | disk reload、authority refresh、devbox recovery、done |
| authority | 有权签发/刷新 token 的来源 | OIDC、external provider command 等 |
| fresh-mint guard | 刚 mint 后暂不再次 mint 的保护 | 防止迟到 401 造成 refresh storm |
| refresh storm | 多个 401 连续触发大量 token refresh | guard 与 singleflight 用于抑制 |
| team pin | Session/进程锁定到指定团队的策略 | Recovery 后 credential 必须再次通过 gate |
| Auth Provider | 通过配置命令动态提供模型 token 的来源 | 与 AuthManager session-token route 分离 |
| auth incident | 从首个 post-recovery 401 到成功/耗尽的一段故障 | `AuthRetrySchedule` 管理 |
| uncharged resubmit | 未发送凭据的 401 不消耗 credential-rejected slot | 仍有等待、计数与 runaway cap |
| runaway guard | 防止不收费路径无限循环的硬上限 | 50 次 missing-credential rejection |
| suspend | 机器睡眠使 wall clock 与 monotonic clock 出现漂移 | 可有限次数 reset auth incident |
| attribution | 解释错误由哪个 token/consumer 产生 | 401 时比较 wire bearer 与 manager token |
| bearer suffix | Token 尾部的短片段 | 用于诊断匹配，不记录完整 secret |
| stale snapshot | 请求发送的 token 与 manager 当前 token 不同 | attribution payload 的核心布尔字段 |
| consumer | 发出失败请求的调用点类别 | Chat stream、Storage upload、WebSearch 等 |
| OTel span | OpenTelemetry 可导出的追踪片段 | `auth_401_attribution` 独立诊断记录 |
| AuthRemedy | 把 auth 状态转换为用户可行动建议 | 决定 reauth error type 与 advice |
| RetryState | 发给 Client 的重试 UI 状态 | Retrying、Exhausted、Failed |
| ACP Error | `session/prompt` 的协议错误响应 | 携带 code、message 与结构化 data |
| stable error code | 不随系统文案变化的机器码 | rate-limit -32003、FS_* code |
| error data | ACP Error 上的结构化附加字段 | http_status、error_kind、promptUsage |
| usage incomplete | 无法证明全部 token 花费已归集 | workflow child 失败时保守标记 |
| ToolError | Tool Runtime 的执行/解析错误 | 通常转换为模型可消费的 ToolResult |
| model-recoverable | 模型看到错误后可修正策略继续 | 文件不存在、参数解析错误等 |
| ToolResult | Tool Call 对应的 Conversation 回填 | 失败结果也必须配对写入 |
| singleflight | 多个并发调用共享一次实际恢复操作 | Tool batch 401 的 OnceCell refresh |
| dangling Tool Call | Tool Call 没有配对 ToolResult | cancel/crash/corruption 后需 synthetic repair |
| orphan ToolResult | 找不到 owning Tool Call 的结果 | 会使某些模型 API 返回 400 |
| out-of-band repair | 不依赖正常模型 Turn 的修复入口 | `x.ai/session/repair` |
| resident rail | Session 在内存时通过 actor 修改 | 保证 ChatState 权威性与串行化 |
| disk rail | Session 不在内存时直接修磁盘 | 使用 tolerant load 与 atomic replace |
| dry run | 只报告将修改什么，不实际写入 | repair request 的 `dryRun` |
| idempotent | 重复执行不会继续改变结果 | Repair 第二次应是 no-op |
| Persistence Actor | 串行处理 Session 写盘消息的 actor | `SessionPersistence::run` |
| best-effort | 失败记录但不让主流程失败 | 辅助状态与部分普通写入 |
| fail-open | 辅助机制失败后继续主功能 | 仅在继续不会破坏安全不变量时使用 |
| fail-closed | 无法证明正确/安全时终止 | auth/team/permission/budget/init 等 |
| acknowledged write | 调用者等待写入结果 | Workflow state、durable append 等 |
| barrier | 确认队列中更早消息已处理到某点 | `FlushAndAck` 的主要顺序语义 |
| commit-aware | 错误能说明数据是否已经提交 | AppendUpdateError::Committed/NotCommitted |
| NotCommitted | 写入确认没有生效 | 通常可以安全 retry |
| Committed | 数据已落入目标，但后续 bookkeeping 出错 | 盲目 retry 可能重复 |
| AcknowledgementLost | actor/ack channel 断开，提交状态未知 | 需要读取确认或 idempotency |
| durable append | 要求比普通 buffered append 更强的落盘语义 | `append_update_durably` |
| torn line | JSONL 写到一半进程退出留下的半行 | tolerant loader 可跳过 |
| atomic replace | 先写临时文件再 rename 覆盖 | full history repair/compaction 的崩溃保护 |
| crash atomicity | crash 后看到旧版本或完整新版本，不看半版本 | temp + rename 提供的核心性质 |
| Actor death | SessionActor task 意外结束 | supervisor 标记 DeadFailed 并回收 runtime |
| DeadFailed | Session resident actor 异常退出的 hosting 状态 | 不等于持久化 Session 被删除 |
| stale completion | 已被 cancel/替换的旧 Turn 后来才完成 | ownership gate 丢弃，不能重复 terminal |
| ownership gate | 只有仍拥有 prompt 的 completion 可收敛状态 | 防止旧任务清除新任务或双发终态 |
| TurnCompleted | 持久化、可 replay 的 Turn 终态 | attach 后客户端可恢复结束状态 |
| prompt_complete | 当前实时连接的 fire-and-forget 终态提示 | 与 TurnCompleted 共享字段推导 |
| StopFailure hook | Turn 失败时触发的 hook 事件 | 使用稳定错误分类 |
| degradation | 主功能无法完整运行时降低自动化程度 | Goal 遇 infra error 自动暂停 |

---

## 101. 下一篇适合继续精读什么

沿着 Agent 基础知识继续，下一篇最值得写的是：

> **源码精读 24：Agent Context & Instruction Runtime——AGENTS.md、System Prompt、Skills、Memory、Tool Result、Interjection 与 Compaction Summary 怎样在多轮运行中进入、退出和重建上下文。**

源码精读 04 已解释单次 Prompt 的拼接顺序；第 24 篇会进一步研究动态生命周期：

- 哪些 instruction 是 Session 固定快照；
- 哪些每轮重新加载；
- workspace/cwd 变化怎样改变项目指令；
- Skill 正文何时进入 Conversation；
- Tool Result 与 system reminder 怎样被消费；
- compaction 后哪些上下文必须重新注入；
- subagent 为什么拥有不同的 audience 与 instruction scope。
