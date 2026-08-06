# xai-grok-sampler 代码导读

> `crates/codegen/xai-grok-sampler` · HTTP 流式推理、重试、多后端
> 配套：[xai-chat-state](./xai-chat-state.md) · [09 端到端](../09_end_to_end_request_flow.md)

---

## 1. 职责与三层 API

从 shell 抽出的 **采样层 Actor**，负责：

- 向 x.ai API 发 HTTP 流式请求
- 多请求并发、取消、重试退避
- 原始 chunk → `SamplingEvent` 转换

```text
Layer 1: SamplingClient     → 原始 HTTP chunk Stream
Layer 2: stream::*          → SamplingEvent Stream
Layer 3: SamplerHandle/Actor → 并发 + retry + cancel
```

```text
SessionActor
  chat_state.build_conversation_request()
  sampler_handle.submit(request_id, request)
       ↓ SamplingEvent channel
  SessionActor 处理 Chunk / Done / Error
  chat_state.push_assistant_response / push_tool_result loop
```

---

## 2. SamplerActor 结构

`actor/mod.rs`：

```text
SamplerActor::spawn(config, retry_policy, event_tx) → SamplerHandle

run() loop (tokio::select!, biased):
  1. tasks.join_next() — 清理完成的 per-request task
  2. cmd_rx.recv() — Submit / Cancel / UpdateConfig / IsActive / ActiveCount

Submit → spawn request_task（可多个并发）
Cancel → CancellationToken + 从 active_requests 移除
```

| 文件 | 作用 |
| --- | --- |
| `actor/state.rs` | `ActorState`、`ActiveRequest` |
| `actor/request_task.rs` | 单请求：client → stream → retry → event |
| `handle.rs` | `submit` / `cancel` / `submit_and_collect` |
| `commands.rs` | `SamplerCommand` 枚举 |

---

## 3. SamplerHandle API

| 方法 | 行为 |
| --- | --- |
| `submit` | fire-and-forget；结果走共享 `event_tx` |
| `submit_with_config` | 单请求覆盖 `SamplerConfig`（如换模型） |
| `submit_and_collect` | await 完成 + 仍发事件给 UI（compaction/summary） |
| `cancel` | 取消 in-flight `RequestId` |
| `update_config` | 更新 actor 默认 config |
| `is_active` / `active_count` | 查询并发数 |
| `noop` | 测试用空 handle |

---

## 4. SamplingClient（Layer 1）

`client.rs` — `SamplingClient` + `ApiBackend`：

| Backend | stream 入口 | 说明 |
| --- | --- | --- |
| Chat Completions | `conversation_stream_chat_completions` | OpenAI 兼容 |
| Responses | `conversation_stream_responses` | x.ai Responses API |
| Messages | `conversation_stream_messages` | Anthropic 风格 messages |

`SamplerConfig.api_backend` 决定 `request_task` 调用哪条路径。
`shared_http.rs` — 连接池复用。

---

## 5. Stream 转换（Layer 2）

`stream/mod.rs`：

| 函数 | 文件 | 输出 |
| --- | --- | --- |
| `stream_chat_completions` | `chat_completions.rs` | `SamplingEvent` |
| `stream_responses` | `responses.rs` | `SamplingEvent` |
| `stream_messages` | `messages.rs` | `SamplingEvent` |
| `collect_response` | `collect.rs` | 非流式聚合 |

事件类型见 `events.rs`：`TextDelta`、`ToolCallDelta`、`Done`、`Error` 等。

---

## 6. 重试与错误分类

`retry.rs`：

- `classify_error` → `RetryDecision`（重试 / 立即失败 / 换策略）
- `retry_backoff_with_jitter` — 指数退避
- `DEFAULT_MAX_RETRIES`、`RATE_LIMIT_RETRY_THRESHOLD`
- `format_sampling_error` — 用户可见错误文案

`config.rs` — `RetryPolicy`、`BearerResolver`、`HeaderInjector`、`AuthScheme`。

---

## 7. 配置 SamplerConfig

关键字段（`config.rs`）：

- `model_id`、`api_backend`、`base_url`
- `bearer_resolver` / `header_injector` — 鉴权
- `reasoning_effort`、thinking 相关开关
- `origin_client_info` — telemetry / attribution

Shell 在登录、换模型、`update_config` 时推送新 config。

---

## 8. 指标与日志

| 模块 | 作用 |
| --- | --- |
| `metrics.rs` | `InferenceLatencyStats`、`compute_percentiles` |
| `sampling_log.rs` | 结构化采样日志、`AuthInfo` |
| `attribution.rs` | `SamplingConsumer`、401 归因回调 |
| `doom_loop.rs` | `DoomLoopSignalCollector` — 检测重复 tool 循环 |

---

## 9. SamplingEvent 消费（Shell 侧）

典型处理（`SessionActor` turn 循环）：

```text
SamplingEvent::TextDelta        → ACP AgentMessageChunk
SamplingEvent::ToolCallStart    → 准备 PreparedToolCall
SamplingEvent::ToolCallDelta    → 累积 arguments JSON
SamplingEvent::Done             → push_assistant + 执行 tools
SamplingEvent::Error            → 失败 UI + repair 路径
```

---

## 10. SamplerCommand

| 变体 | 字段 |
| --- | --- |
| `Submit` | request_id, request, config?, completion_tx? |
| `Cancel` | request_id |
| `UpdateConfig` | config |
| `IsActive` | request_id, reply |
| `ActiveCount` | reply |

---

## 11. SamplingEvent 全量

| 变体 | 含义 |
| --- | --- |
| `StreamStarted` | HTTP 流建立 |
| `FirstToken` | 首个 content token |
| `ChannelToken` | Text 或 Reasoning 通道增量 |
| `ToolCallDelta` | tool call arguments 流式片段 |
| `Completed` | 成功结束 + ConversationResponse |
| `Retrying` | 重试中（含 doom loop 信息） |
| `Failed` | 最终失败 |
| `ModelMetadata` | 响应头中的模型元数据 |
| `BackendToolCallStarted` | 服务端托管工具开始 |
| `BackendToolCallCompleted` | 服务端托管工具完成 |

---

## 12. request_task 重试循环

`actor/request_task.rs` — `run_request_task`：

```text
SamplingClient::new(config)
loop attempt in 0..=max_retries:
  选择 stream_* 按 api_backend
  消费 SamplingEvent 流
  AttemptOutcome: Completed | Empty | Failed | Cancelled | InitFailed
  classify_error → RetryDecision::Retry | GiveUp
  retry_backoff_with_jitter → 下一 attempt
emit Completed / Failed + completion_tx
```

`DEFAULT_IDLE_TIMEOUT_SECS` = 300（5 分钟 chunk 空闲超时）。

---

## 13. RetryDecision (`retry.rs`)

- `classify_error` — 根据 `SamplingError` 判断是否可重试
- `Retry` / `GiveUp` / rate-limit 特殊阈值
- `doom_loop_backoff` — doom loop 检测后的退避
- `format_sampling_error` — UI 错误文案

---

## 14. SamplerConfig 主要字段

`api_key` · `base_url` · `model` · `api_backend` · `auth_scheme` ·
`max_completion_tokens` · `temperature` · `top_p` · `extra_headers` ·
`max_retries` · `idle_timeout_secs` · `reasoning_effort` · `doom_loop_recovery`

Shell 在 `resolve_model_to_sampling_config` / `reconstruct_full_config` 构建。

---

## 15. SamplingErrorKind

- `Auth`
- `Http`
- `Api`
- `Serialization`
- `IdleTimeout`
- `EmptyResponse`
- `Cancelled`
- `DoomLoopDetected`
- `Other`

Session 对 `Api { status: 400 }` 结合 `ModelMetadata` 判断是否 context window 溢出。

---

## 16. client.rs 与三后端

| 方法 | 后端 |
| --- | --- |
| `conversation_stream_chat_completions` | OpenAI Chat Completions |
| `conversation_stream_responses` | x.ai Responses API |
| `conversation_stream_messages` | Anthropic Messages |

`SamplingClient::new` 校验 config；`user_agent_string_for` 构建 UA。

---

## 17. stream/ 各文件职责

| 文件 | 职责 |
| --- | --- |
| `chat_completions.rs` | 解析 SSE chunk → ToolCallDelta / ChannelToken |
| `responses.rs` | Responses API 事件流 + `stream_responses_tracked` |
| `messages.rs` | Anthropic message delta |
| `collect.rs` | 非流式 `collect_response` |

---

## 18. doom_loop 与 attribution

- `doom_loop.rs` — `DoomLoopSignalCollector` 检测重复 tool 模式
- `attribution.rs` — 401 归因、`SamplingConsumer` 回调
- `sampling_log.rs` — `request_span` 结构化日志
- `metrics.rs` — TTFT、总延迟分位数

---

## 19. SamplerHandle 完整 API

| 方法 | async | 说明 |
| --- | :---: | --- |
| `submit` |  | 提交请求 |
| `submit_with_config` |  | 带 config 覆盖 |
| `cancel` |  | 取消 |
| `update_config` |  | 更新默认 config |
| `is_active` | ✓ | 是否 in-flight |
| `active_count` | ✓ | 并发数 |
| `submit_and_collect` | ✓ | await 完成 |
| `noop` |  | 空 handle |

---

## 20. SamplerCommand 逐条说明

| 命令 | 说明 |
| --- | --- |

---

## 21. Shell 源码对照（sampler）

| Shell 文件 | 调用 |
| --- | --- |
| `session/handle.rs` | sampler_handle 字段 |
| `session/acp_session_impl/turn*.rs` | submit, cancel, 消费 SamplingEvent |
| `session/compaction.rs` | submit_and_collect 做 summary |
| `agent/config.rs` | resolve_model_to_sampling_config |
| `session/acp_session.rs` | reconstruct_full_config, update_config |

---

## 22. actor/ 与 stream/ 函数索引

### actor/

**`actor/mod.rs`：** `spawn`, `run`, `handle_command`

**`actor/request_task.rs`：** `run_request_task`, `apply_retry_decision`, `sleep_or_cancel`, `run_one_attempt`, `tee_errors`, `drive_l2`, `retag`, `synthesize_from_info`, `build_empty_context`, `emit_failed`, `emit_retrying`, `handle_cancellation`, `send_completion`, `synthesize_idle_timeout_extracts_elapsed_secs`, `synthesize_api_500_round_trips`, `synthesize_rate_limited_preserves_retry_after`, `synthesize_serialization_stays_serialization`, `retry_sleep_returns_immediately_on_cancellation`, `retry_decision_cancellation_emits_terminal_cancel`, `tee_captures_first_error_only`

**`actor/state.rs`：** `new`, `register`, `remove`, `cancel`, `update_config`, `cfg`, `cancel_unknown_request_returns_false`, `register_then_cancel_removes`, `register_returns_previous_when_same_id`


### stream/

**`stream/chat_completions.rs`：** `stream_chat_completions`, `rid`, `make_chunk`, `text_chunk`, `final_chunk`, `collect`, `empty_stream_yields_started_then_completed`, `text_only_stream_emits_first_token_then_channel_tokens_then_completed`, `reasoning_chunk_emits_reasoning_channel_and_first_token_once`, `tool_call_stream_emits_deltas_and_assembles_final_call`, `mid_stream_error_yields_failed_no_completed`, `idle_timeout_when_stream_stalls`, `model_metadata_yielded_after_stream_started`, `usage_is_extracted_from_chunk`, `cost_is_extracted_and_zero_is_unreported`, `later_missing_cost_does_not_clobber_earlier_ticks`

**`stream/collect.rs`：** `collect_response`, `rid`, `text_chunk`, `final_chunk`, `happy_path_returns_response_and_metrics`, `failure_path_returns_error`, `truncated_stream_returns_error`, `intermediate_events_are_dropped`

**`stream/messages.rs`：** `messages_event_has_meaningful_content`, `stream_messages`

**`stream/responses.rs`：** `responses_event_has_meaningful_content`, `responses_event_may_have_output`, `stream_responses`, `stream_responses_tracked`, `rid`, `build_response`, `empty_completed_response`, `failed_response_with_error`, `text_delta_event`, `completed_event`, `collect`, `missing_completed_event_yields_failed`, `text_delta_then_completed_yields_completed_with_stop`, `empty_failed_response_is_not_treated_as_output`, `response_failed_yields_failed_500`, `mid_stream_transport_error_yields_failed`, `idle_timeout_when_stream_stalls`, `model_metadata_yielded_after_stream_started`, `meaningful_content_classifier_basics`, `output_classifier_covers_non_forwarded_backend_events`, `tracked_stream_marks_non_forwarded_refusal_as_output`, `function_call_added_event`, `function_call_args_delta_event`, `tool_call_deltas`, `function_call_emits_initial_id_name_then_arg_deltas`, `function_call_args_delta_without_added_event_is_dropped`, `multiple_function_calls_get_distinct_tool_indices`, `doom_loop_collector_signals_land_on_completed_response`, `confident_signal_aborts_stream_unless_disarmed`, `doom_loop_signals_empty_without_collector_or_triggers`


**`client.rs`：** `apply`, `deserialize_response_event`, `apply_terminal_event_overrides`, `extract_context_total`, `record_stream_request_failure`, `extract_retry_after`, `extract_should_retry`, `extract_model_metadata`, `apply_env_http_headers`, `fmt`, `new`, `url_for_path`, `current`, `agent_version`, `user_agent_string_for`, `new`, `api_backend`, `post`, `current_sent_bearer_prefix`, `extract_sent_bearer`, `record_401_attribution`, `auth_info`, `is_sensitive_header`, `body_preview`, `log_request_headers`, `endpoint`, `apply_defaults`, `handle_response`, `chat_completion`, `chat_completion_stream`, `apply_response_defaults`, `create_response`, `create_response_stream`, `apply_message_defaults`, `create_message`, `create_message_stream`, `apply_conversation_defaults`, `conversation_stream`, `conversation`, `conversation_stream_responses`, `conversation_responses`, `conversation_stream_messages`, `conversation_messages`, `conversation_collect`, `minimal_config`, `streaming_chat_request_serializes_correctly`, `extract_retry_after_parses_seconds`, `extract_retry_after_caps_at_120`, `extract_retry_after_zero_is_valid`, `extract_retry_after_ignores_http_date`


## 23. 逐文件导读

### `actor/mod.rs` (145 行)

> Sampler actor: owns global state, spawns per-request tasks.
> The actor task itself is single-threaded -- it processes one
> command at a time -- but it spawns `tokio::spawn` per-request
> tasks for the actual streaming work, so multiple requests can be
> in flight concurrently.

**公开 API：** struct`SamplerActor`

**函数（节选）：** `spawn`, `run`, `handle_command`

---

### `actor/request_task.rs` (1017 行)

> Per-request streaming task.
> Spawned by the actor's `Submit` handler. Owns the retry loop and
> consumes a Layer 2 stream from the matching backend transform.
> Cancellation is cooperative via `CancellationToken`.

**公开 API：** fn`run_request_task`

**函数（节选）：** `run_request_task`, `apply_retry_decision`, `sleep_or_cancel`, `run_one_attempt`, `tee_errors`, `drive_l2`, `retag`, `synthesize_from_info`, `build_empty_context`, `emit_failed`, `emit_retrying`, `handle_cancellation`, `send_completion`, `synthesize_idle_timeout_extracts_elapsed_secs`, `synthesize_api_500_round_trips`, `synthesize_rate_limited_preserves_retry_after`, `synthesize_serialization_stays_serialization`, `retry_sleep_returns_immediately_on_cancellation`, `retry_decision_cancellation_emits_terminal_cancel`, `tee_captures_first_error_only`

---

### `actor/state.rs` (150 行)

> Actor-internal state.
> All fields are touched only from the actor task, so no mutex /
> atomic synchronization is needed -- the actor's command-loop
> serialization gives us a "single-threaded with shared state"
> discipline matching the hunk-tracker pattern.

**公开 API：** struct`ActiveRequest`, struct`ActorState`

**函数（节选）：** `new`, `register`, `remove`, `cancel`, `update_config`, `cfg`, `cancel_unknown_request_returns_false`, `register_then_cancel_removes`, `register_returns_previous_when_same_id`

---

### `attribution.rs` (120 行)

> 401 attribution callback hook for the sampling client.
> Every 401 response site can optionally emit an attribution event so
> a downstream observer can split production 401s into "client sent a
> stale snapshot bearer that the server rejected" vs. "client sent
> the live token from its auth source and the server still rejected
> it" buckets.
> `xai-grok-sampler` is intentionally decoupled from `xai-grok-shell`
> (no shell types, no logging crate, no auth-manager dependency). The
> caller wires an implementation of [`Auth401AttributionCallback`]
> into [`crate::SamplerConfig::attribution_callback`]; the sampler

**公开 API：** enum`SamplingConsumer`, trait`Auth401AttributionCallback`

**函数（节选）：** `as_endpoint`, `record_401`

---

### `client.rs` (2789 行)

> HTTP client for the xAI sampling APIs.
> Owns the `reqwest::Client`, default request headers, and per-method
> defaults. Talks to three backend shapes:
> * Chat Completions (`/chat/completions`)
> * Responses API (`/responses`)
> * Anthropic Messages API (`/messages`)
> All trace-upload and URL-based header injection is intentionally
> *not* here. The session is responsible for putting any per-request
> headers (proxy auth, OTel context, etc.)
> into [`SamplerConfig::extra_headers`] before constructing the client.

**公开 API：** fn`user_agent_string_for`, struct`SamplingClient`

**函数（节选）：** `apply`, `deserialize_response_event`, `apply_terminal_event_overrides`, `extract_context_total`, `record_stream_request_failure`, `extract_retry_after`, `extract_should_retry`, `extract_model_metadata`, `apply_env_http_headers`, `fmt`, `new`, `url_for_path`, `current`, `agent_version`, `user_agent_string_for`, `new`, `api_backend`, `post`, `current_sent_bearer_prefix`, `extract_sent_bearer`, `record_401_attribution`, `auth_info`, `is_sensitive_header`, `body_preview`, `log_request_headers`

---

### `commands.rs` (47 行)

> Internal actor protocol.
> `SamplerCommand` is `pub(crate)` because it is the wire between
> [`SamplerHandle`](crate::handle::SamplerHandle) and the actor task,
> not a public type. External callers always go through `SamplerHandle`.

**公开 API：** enum`SamplerCommand`

---

### `config.rs` (258 行)

> Sampler configuration types.
> [`SamplerConfig`] is the per-request configuration handed to the
> sampler. It deliberately does **not** alias
> `xai_grok_sampling_types::SamplingConfig` so that the sampler crate
> avoids transitive dependencies on shell-specific types
> (`xai-grok-tools`, etc.).

**公开 API：** struct`SamplerConfig`, struct`RetryPolicy`, struct`OriginClientInfo`, enum`AuthScheme`, trait`BearerResolver`, trait`HeaderInjector`

**函数（节选）：** `default`, `current_bearer`, `inject`, `default`, `retry_policy_defaults`, `config_without_doom_loop_recovery_deserializes_to_none`

---

### `doom_loop.rs` (229 行)

> Per-request transport for server-reported doom-loop signals.
> The wire shapes and tolerant parsers live in
> [`xai_grok_sampling_types::doom_loop`]; this module only moves the parsed
> signals across the layer boundary: the Layer-1 SSE decoder in
> [`crate::client`] records them as raw payloads arrive, and the Layer-2
> transform in [`crate::stream::responses`] drains them into the final
> `ConversationResponse`.

**公开 API：** struct`DoomLoopSignalCollector`

**函数（节选）：** `new`, `disarm_abort`, `abort_triggers`, `absorb`, `take`, `record`, `log_malformed_once`, `absorb_swallows_check_event_and_records_signals`, `absorb_swallows_check_event_without_sse_name`, `absorb_dedupes_cumulative_sets_by_raw_label`, `absorb_forwards_ordinary_and_terminal_payloads`, `malformed_check_event_swallowed_without_signals`, `named_event_with_garbage_payload_still_swallowed`, `take_drains_once`, `abort_triggers_requires_confidence_and_honors_disarm`

---

### `events.rs` (367 行)

> Outbound events emitted by the sampler.

**公开 API：** struct`SamplingErrorInfo`, enum`SamplingChannel`, enum`SamplingEvent`, enum`SamplingErrorKind`

**函数（节选）：** `as_str`, `from`, `auth_variant_classified_as_auth`, `invalid_configuration_classified_as_api`, `serialization_variant_classified_as_serialization`, `api_500_classified_as_api_and_retryable`, `api_429_classified_as_rate_limited_and_extracts_retry_after`, `api_400_classified_as_api_and_not_retryable`, `event_stream_error_classified_as_http_and_retryable`, `stream_error_classified_as_api_and_retryable`, `idle_timeout_classified_as_idle_timeout_and_not_retryable`

---

### `handle.rs` (157 行)

> Public handle for talking to the sampler actor.

**公开 API：** struct`SamplerHandle`

**函数（节选）：** `new`, `noop`, `submit`, `submit_with_config`, `cancel`, `update_config`, `is_active`, `active_count`, `submit_and_collect`, `drop`

---

### `lib.rs` (54 行)

> xai-grok-sampler - Actor-based sampling layer for xAI grok.
> This crate extracts the HTTP streaming + retry logic out of
> `xai-grok-shell`'s session actor into a standalone, reusable
> component built on the same actor pattern as `xai-hunk-tracker`.
> ## Layered API
> - **Layer 1**: [`client::SamplingClient`] returns raw chunk streams.
> - **Layer 2**: [`stream`] transforms raw streams into [`SamplingEvent`]s.
> - **Layer 3**: [`SamplerHandle`] manages concurrent requests with retry,
> cancellation, and event-based coordination via the actor.
> The type skeleton, the pure retry / metrics / client logic, the

---

### `metrics.rs` (246 行)

> Per-response inference latency metrics.
> Captures token-level timing from streaming inference responses:
> TTFB, TTLB, and inter-token latency (ITL) statistics.

**公开 API：** fn`compute_percentiles`, struct`InferenceLatencyStats`

**函数（节选）：** `compute_percentiles`, `record_on_span`, `from_timestamps`, `offset`, `test_empty_timestamps`, `test_single_chunk`, `test_two_chunks`, `test_many_chunks`, `test_p99_does_not_overflow`, `test_ttlb_uses_stream_end_not_last_chunk`

---

### `retry.rs` (890 行)

> Retry classification, backoff, and decision-making.
> Pure logic only: no I/O, no notifications, no logging side-effects.
> The actor (M4) wraps this with the actual retry loop.
> # Retry behavior summary
> **Retried** (up to [`DEFAULT_MAX_RETRIES`] = 15, ~6 min with 30s backoff cap):
> - 500, 502, 503, 504, 520 (server errors)
> - Connection errors (timeout, refused, reset)
> - `EventStreamError` / `StreamError` (mid-stream failures)
> - `EmptyResponse` (model returned no content/tool calls)
> **Retried with lower cap** ([`RATE_LIMIT_RETRY_THRESHOLD`] = 2):

**公开 API：** fn`resolve_max_retries_with_env`, fn`resolve_max_retries`, fn`doom_loop_backoff`, fn`retry_backoff_with_jitter`, fn`classify_error`, fn`format_sampling_error`, fn`clone_error`, enum`RetryDecision`

**函数（节选）：** `resolve_max_retries_with_env`, `resolve_max_retries`, `doom_loop_backoff`, `retry_backoff_with_jitter`, `classify_error`, `format_sampling_error`, `clone_error`, `api_err`, `api_err_with_retry_after`, `resolve_max_retries_env_override_takes_precedence`, `resolve_max_retries_falls_back_to_model`, `resolve_max_retries_default`, `resolve_max_retries_invalid_env_falls_through`, `backoff_first_retry_is_around_two_seconds`, `backoff_doubles_then_caps_at_thirty_seconds`, `backoff_zero_retry_count_is_well_defined`, `classify_auth_error_emits_to_session`, `classify_unauthorized_emits_to_session`, `classify_encrypted_content_emits_to_session`, `classify_payload_too_large_strips_images`, `classify_image_processing_error_400_strips_images`, `classify_image_processing_error_500_wrapped_strips_images`, `classify_image_processing_error_takes_priority_over_5xx_retry`, `classify_rate_limited_uses_retry_after`, `classify_rate_limited_capped_at_threshold`

---

### `sampling_log.rs` (37 行)

> Sampling log — emits `tracing` events with `target: "sampling_log"`.
> A dedicated layer in `xai-grok-telemetry` routes these to
> `~/.grok/logs/sampling.jsonl`. Enable with `--log-sampling`.

**公开 API：** fn`request_span`, struct`AuthInfo`

**函数（节选）：** `request_span`

---

### `shared_http.rs` (156 行)

> Process-wide shared `reqwest::Client`s for sampling requests.
> Sharing one client across all `SamplingClient` instances is safe because
> the builders below take no config-derived input: auth, extra headers, base
> URL, and User-Agent are all applied per-request in `SamplingClient::post`.
> Stale-connection exposure is bounded by HTTP/2 keepalive pings (15s
> interval, 5s timeout, while idle), the 90s idle-pool eviction, and the
> first-retry HTTP/1.1 rebuild escape hatch (that client never pools, so
> every use opens a fresh connection).
> Wire-level behavior (connection reuse, header isolation, pool-less http1
> fallback, kill switch) is pinned by the `shared_http_wire` and

**公开 API：** fn`client`, fn`client_http1`

**函数（节选）：** `sharing_disabled`, `shared`, `client`, `client_http1`, `build_http_client`, `build_http_client_http1`, `flaky_build`, `shared_does_not_cache_build_failures`, `shared_disabled_bypasses_cell`

---

### `stream/chat_completions.rs` (774 行)

> Layer-2 stream transform for the Chat Completions API.
> Consumes a raw `ChatCompletionChunk` stream and produces
> [`SamplingEvent`]s. Pure: no I/O, no shell coupling.

**公开 API：** fn`stream_chat_completions`

**函数（节选）：** `stream_chat_completions`, `rid`, `make_chunk`, `text_chunk`, `final_chunk`, `collect`, `empty_stream_yields_started_then_completed`, `text_only_stream_emits_first_token_then_channel_tokens_then_completed`, `reasoning_chunk_emits_reasoning_channel_and_first_token_once`, `tool_call_stream_emits_deltas_and_assembles_final_call`, `mid_stream_error_yields_failed_no_completed`, `idle_timeout_when_stream_stalls`, `model_metadata_yielded_after_stream_started`, `usage_is_extracted_from_chunk`, `cost_is_extracted_and_zero_is_unreported`, `later_missing_cost_does_not_clobber_earlier_ticks`

---

### `stream/collect.rs` (182 行)

> Buffered consumer for [`SamplingEvent`] streams.
> Drains a Layer-2 event stream into the final
> `(ConversationResponse, InferenceLatencyStats)` pair. Used by
> callers that don't need streaming UI updates (e.g., compaction,
> `/btw`, dream-model calls).

**公开 API：** fn`collect_response`

**函数（节选）：** `collect_response`, `rid`, `text_chunk`, `final_chunk`, `happy_path_returns_response_and_metrics`, `failure_path_returns_error`, `truncated_stream_returns_error`, `intermediate_events_are_dropped`

---

### `stream/messages.rs` (530 行)

> Layer-2 stream transform for the Anthropic Messages API.
> Consumes a raw `MessageStreamEvent` stream and produces
> [`SamplingEvent`]s. Pure: no I/O, no shell coupling.

**公开 API：** fn`messages_event_has_meaningful_content`, fn`stream_messages`

**函数（节选）：** `messages_event_has_meaningful_content`, `stream_messages`

---

### `stream/mod.rs` (19 行)

> Layer-2 stream transforms: turn raw HTTP chunk streams into
> [`SamplingEvent`](crate::events::SamplingEvent) streams.
> Each backend has its own transform because the raw chunk types
> differ; backend dispatch happens in M4's
> [`actor::request_task`](crate::actor::request_task), which knows
> the API backend from `SamplerConfig.api_backend` and calls the
> matching `SamplingClient::conversation_stream*` method before
> handing the result to the corresponding transform here.

---

### `stream/responses.rs` (1133 行)

> Layer-2 stream transform for the OpenAI Responses API.
> Consumes a raw `rs::ResponseStreamEvent` stream and produces
> [`SamplingEvent`]s. Pure: no I/O, no shell coupling.

**公开 API：** fn`responses_event_has_meaningful_content`, fn`responses_event_may_have_output`, fn`stream_responses`, fn`stream_responses_tracked`

**函数（节选）：** `responses_event_has_meaningful_content`, `responses_event_may_have_output`, `stream_responses`, `stream_responses_tracked`, `rid`, `build_response`, `empty_completed_response`, `failed_response_with_error`, `text_delta_event`, `completed_event`, `collect`, `missing_completed_event_yields_failed`, `text_delta_then_completed_yields_completed_with_stop`, `empty_failed_response_is_not_treated_as_output`, `response_failed_yields_failed_500`, `mid_stream_transport_error_yields_failed`, `idle_timeout_when_stream_stalls`, `model_metadata_yielded_after_stream_started`, `meaningful_content_classifier_basics`, `output_classifier_covers_non_forwarded_backend_events`, `tracked_stream_marks_non_forwarded_refusal_as_output`, `function_call_added_event`, `function_call_args_delta_event`, `tool_call_deltas`, `function_call_emits_initial_id_name_then_arg_deltas`

---

### `types.rs` (75 行)

> Core sampler types.

**公开 API：** struct`RequestId`

**函数（节选）：** `random`, `as_str`, `fmt`, `from`, `from`, `from_string_roundtrips`, `from_str_roundtrips`, `display_matches_inner_string`, `random_produces_unique_values`

---

---

## 24. 源码文件索引（简表）

### `./`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `attribution.rs` | 120 | 401 attribution callback hook for the sampling client. |
| `client.rs` | 2789 | HTTP client for the xAI sampling APIs. |
| `commands.rs` | 47 | Internal actor protocol. |
| `config.rs` | 258 | Sampler configuration types. |
| `doom_loop.rs` | 229 | Per-request transport for server-reported doom-loop signals. |
| `events.rs` | 367 | Outbound events emitted by the sampler. |
| `handle.rs` | 157 | Public handle for talking to the sampler actor. |
| `lib.rs` | 54 | xai-grok-sampler - Actor-based sampling layer for xAI grok. |
| `metrics.rs` | 246 | Per-response inference latency metrics. |
| `retry.rs` | 890 | Retry classification, backoff, and decision-making. |
| `sampling_log.rs` | 37 | Sampling log — emits `tracing` events with `target: "sampling_log |
| `shared_http.rs` | 156 | Process-wide shared `reqwest::Client`s for sampling requests. |
| `types.rs` | 75 | Core sampler types. |

### `actor/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 145 | Sampler actor: owns global state, spawns per-request tasks. |
| `request_task.rs` | 1017 | Per-request streaming task. |
| `state.rs` | 150 | Actor-internal state. |

### `stream/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `chat_completions.rs` | 774 | Layer-2 stream transform for the Chat Completions API. |
| `collect.rs` | 182 | Buffered consumer for [`SamplingEvent`] streams. |
| `messages.rs` | 530 | Layer-2 stream transform for the Anthropic Messages API. |
| `mod.rs` | 19 | Layer-2 stream transforms: turn raw HTTP chunk streams into |
| `responses.rs` | 1133 | Layer-2 stream transform for the OpenAI Responses API. |

---

## 25. 符号索引（pub API）

### `actor/mod.rs`

struct`SamplerActor`

### `actor/request_task.rs`

fn`run_request_task` · type`CompletionResult`

### `actor/state.rs`

struct`ActiveRequest` · struct`ActorState`

### `attribution.rs`

enum`SamplingConsumer` · trait`Auth401AttributionCallback` · type`SharedAttributionCallback`

### `client.rs`

fn`user_agent_string_for` · struct`SamplingClient`

### `commands.rs`

enum`SamplerCommand`

### `config.rs`

struct`SamplerConfig` · struct`RetryPolicy` · struct`OriginClientInfo` · enum`AuthScheme` · trait`BearerResolver` · trait`HeaderInjector`
type`SharedBearerResolver` · type`SharedHeaderInjector`

### `doom_loop.rs`

struct`DoomLoopSignalCollector`

### `events.rs`

struct`SamplingErrorInfo` · enum`SamplingChannel` · enum`SamplingEvent` · enum`SamplingErrorKind`

### `handle.rs`

struct`SamplerHandle`

### `metrics.rs`

fn`compute_percentiles` · struct`InferenceLatencyStats`

### `retry.rs`

fn`resolve_max_retries_with_env` · fn`resolve_max_retries` · fn`doom_loop_backoff` · fn`retry_backoff_with_jitter` · fn`classify_error` · fn`format_sampling_error`
fn`clone_error` · enum`RetryDecision`

### `sampling_log.rs`

fn`request_span` · struct`AuthInfo`

### `shared_http.rs`

fn`client` · fn`client_http1`

### `stream/chat_completions.rs`

fn`stream_chat_completions`

### `stream/collect.rs`

fn`collect_response`

### `stream/messages.rs`

fn`messages_event_has_meaningful_content` · fn`stream_messages`

### `stream/responses.rs`

fn`responses_event_has_meaningful_content` · fn`responses_event_may_have_output` · fn`stream_responses` · fn`stream_responses_tracked`

### `types.rs`

struct`RequestId`

---


## 26. 依赖与被依赖

依赖：`xai-grok-sampling-types` · `xai-grok-version`

被依赖：`xai-grok-http` · `xai-grok-sampler` · `xai-grok-shell` · `xai-grok-telemetry`

---

## 27. 常见问题（读代码时）

**Q: 为何 actor 单线程却支持并发请求？**
A: Actor 只管理状态；每个 `Submit` spawn 独立 `request_task`，通过 `JoinSet` 清理。

**Q: 事件 channel 与 completion_tx 区别？**
A: `event_tx` 给 UI 流式更新；`completion_tx` 仅 `submit_and_collect` 用，返回最终 Result。

**Q: 三个 api_backend 如何选？**
A: Shell `SamplingConfig` / 模型配置决定；`request_task` match 后调对应 `stream_*`。

**Q: Empty response 为何重试？**
A: 模型可能只返回 reasoning 无 text/tool；视为 transient，走 retry 循环。

**Q: doom loop 谁消费？**
A: `DoomLoopSignalCollector` + shell recovery policy；`Retrying` 事件带 trigger 标签。

---

## 28. SamplerActor::handle_command

```text
Submit → spawn run_request_task (JoinSet)
Cancel → active.cancel_token.cancel() + remove
UpdateConfig → state.config = config
IsActive → reply.send(active_requests.contains(id))
ActiveCount → reply.send(active_requests.len())
```

---

## 29. SamplingEvent → Shell 映射

| Event | Shell 侧典型处理 |
| --- | --- |
| `StreamStarted` | 记录 stream 开始时间 |
| `ChannelToken` | ACP AgentMessageChunk（text/reasoning） |
| `ToolCallDelta` | 累积 tool call JSON |
| `Completed` | push_assistant + 调度 tool_calls |
| `Failed` | 错误 UI + 可能 reauth |
| `Retrying` | 状态栏/日志显示重试 |
| `BackendToolCallStarted` | 后端搜索等 UI 块 |

---

## 30. 阅读顺序

1. `lib.rs` 三层 API 注释
2. `handle.rs` + `commands.rs`
3. `actor/mod.rs` + `request_task.rs`
4. `client.rs` — HTTP 入口
5. `stream/responses.rs`（或你用的 backend）
6. `retry.rs` — 错误路径
7. shell turn 循环中对 `SamplingEvent` 的 match

