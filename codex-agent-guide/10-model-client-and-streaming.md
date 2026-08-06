# 10. 模型客户端、Responses API 与流式传输

## 1. 四层客户端

模型调用没有被实现成一个简单的 `generate(prompt)`，而是分层：

```text
codex-core ModelClient(Session)
  → codex-api：Responses 语义、事件和 API error
  → codex-client：重试、SSE 辅助、request telemetry
  → codex-http-client：HTTP、代理、CA、连接池
```

分层的意义是让 Agent 编排不依赖 `reqwest`，HTTP 层也不需要理解 Tool Call 或 reasoning event。

## 2. `ModelClient` 与 `ModelClientSession`

线程级 `ModelClient` 保存 provider、认证和共享配置；Turn 内创建的 `ModelClientSession` 保存：

-可复用 WebSocket connection；
-sticky routing 信息；
-该 Turn 的 fallback 状态；
-请求间可持续的传输状态。

`run_turn()` 在整个 Turn 中复用同一个 `ModelClientSession`，避免每次 Tool follow-up 都重新握手或丢失路由状态。

## 3. 请求构建

Core 的 `Prompt` 经 [`build_responses_request()`](../codex-rs/core/src/client.rs) 转成 API 请求。构建时考虑：

-模型 slug 和能力；
-Responses Lite 表示；
-reasoning effort、summary 与 context；
-verbosity；
-output JSON schema；
-parallel tool calls；
-service tier；
-prompt cache key；
-provider 是否为 Azure；
-内部 metadata 是否可发送给非 OpenAI provider。

这说明“选择模型”不仅改变一个字符串。`ModelInfo` 会改变 Prompt 表示、工具能力、上下文策略和返回事件处理。

## 4. HTTP 与 WebSocket

`ModelClientSession::stream()` 根据 provider 和 feature 选择：

1. 优先使用 Responses WebSocket；
2.如果连接或 provider 不支持，则 fallback 到 HTTP streaming；
3.一旦 Turn 内判定 WebSocket 不健康，可以永久切换到 HTTP 并清理 WebSocket 状态。

无论底层 transport，Core 都消费统一的 `ResponseStream<ResponseEvent>`。

## 5. SSE 流

HTTP streaming 的数据大致经历：

```text
HTTP response body bytes
  → SSE frame parser
  → data JSON
  → codex-api ResponseEvent
  → core try_run_sampling_request()
```

`codex-client::sse_stream()` 提供最小 SSE 辅助：

-把字节流交给 eventsource parser；
-通过 channel 转发 `data`；
-为每次等待设置 idle timeout；
-解析失败、提前关闭或超时都先发送 `StreamError` 再退出任务。

Idle timeout 是“多久没有下一事件”，不是“整个请求最长多久”。长推理只要持续产生事件就不会触发 idle timeout。

## 6. `ResponseStream` 的取消

Core 的 `ResponseStream` 包装 Tokio channel，并持有 `consumer_dropped` token。消费者提前 drop 时会取消 mapper task，避免底层仍然读取并解析一个无人消费的长连接。

这是异步流常被忽略的资源问题：结束上层 future 不一定自动停止所有 spawned task，必须显式传播取消。

## 7. 事件映射

Provider 事件会被归一化为 Core 可处理的 `ResponseEvent`，包括：

-响应创建；
-output item added/done；
-assistant text delta；
-reasoning summary/raw content；
-usage/completed；
-rate limit 与模型 metadata。

UI 流式事件和 History item 不完全相同：delta 可实时展示，但只有完整 item 才适合进入 conversation history。

## 8. 两层重试

需要区分：

### 请求策略重试

`codex-client` 的 `RetryPolicy` 处理：

-429；
-5xx；
-timeout；
-network error。

它使用指数退避与随机 jitter，并在每个 attempt 重建 Request。

### Response stream 重试

Core 的 `run_sampling_request()` 对可重试的流错误按 provider 的 `stream_max_retries` 重试。每次重试重新从当前 History 构建 Prompt，并保留 Turn 级 client session。

这两层的关注点不同：前者是传输请求策略，后者知道 Agent 的采样语义和 History。

## 9. 指数退避

[`codex-client/src/retry.rs`](../codex-rs/codex-client/src/retry.rs) 中的退避近似为：

```text
delay = base × 2^(attempt-1) × random(0.9, 1.1)
```

Jitter 避免大量客户端在服务恢复瞬间同时重试。饱和运算避免 attempt 或毫秒乘法溢出。

## 10. 错误分层

| 层 | 错误示例 |
|---|---|
| HTTP transport | DNS、TLS、proxy、timeout |
| HTTP protocol | 429、500、无效 status/body |
| Streaming | SSE 解析错误、completed 前关闭 |
| API semantics | usage limit、context window exceeded |
| Agent semantics | 无效图片、不可恢复的 Tool/History 状态 |

每层只转换自己理解的错误。越上层越能决定“重试、compact、提示用户还是停止 Turn”。

## 11. 遥测

`RequestTelemetry::on_request()` 为每次 attempt 提供：

-attempt 编号；
-status；
-transport error；
-duration。

Core 还记录 sampling timing、TTFT、token usage、cache read/write、reasoning tokens 和 retry 次数。一次用户 Turn 可能有多次模型请求，因此指标必须同时带 thread/turn/request identity。

## 12. 实现建议

-把 transport retry 和 Agent retry 分开；
-重试前确认没有重复提交副作用；
-完整响应终止事件缺失时按失败处理；
-统一 HTTP/WebSocket 的上层事件接口；
-consumer drop 必须取消后台 reader；
-client/connection 应长期复用；
-为 URL、headers 和日志设置敏感信息边界。

