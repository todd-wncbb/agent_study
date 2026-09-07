# xai-grok-telemetry

> 路径：`crates/codegen/xai-grok-telemetry`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Telemetry engine: product events + Mixpanel emission + Sentry error reporting for Grok Build sessions

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `client` | Core telemetry tracking — product events + Mixpanel. |
| `config` | Telemetry-engine configuration. |
| `context` | Git context collection for telemetry events. |
| `debug_log` | Reusable non-blocking file-logging tracing layers for the `--debug` firehose. |
| `enums` | Shared telemetry/config enums extracted from shell. |
| `events` | Telemetry event structs. Every struct needs a `telemetry_event!` binding. |
| `external` | Opt-in, content-redacted **external OTEL** telemetry stream. |
| `hooks_log` | Hooks and plugins tracing target and optional file-based logging layer. |
| `http` | Origin/client identification used by the telemetry engine. |
| `id` | Stable agent identifier. |
| `instrumentation` | A wrapper layer that filters events by target name. |
| `memory_log` | Memory system tracing target and optional file-based logging layer. |
| `memory_telemetry` | Memory subsystem telemetry. Routes through `log_event` (product tier, |
| `otel_layer` | Shared OpenTelemetry tracing layer for exporting spans to the cli-chat-proxy. |
| `prompt_timing` | Per-turn prompt latency measurement. |
| `sampling_log` | Tracing layer for `target: "sampling_log"` → `~/.grok/logs/sampling.jsonl`. |
| `sentry` | Per-host config; everything that varies between binaries lives here. |
| `session_ctx` | Ambient session context for telemetry — product events + Mixpanel via |
| `session_metrics` | Session lifecycle event structs. |
| `unified_log` | Centralized unified log for cross-component session observability. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `appender.rs` | 46 | Shared non-blocking file appender + worker-guard regist | fn`non_blocking_file_writer`, fn`flush_file_log_guards` |
| `client.rs` | 525 | Core telemetry tracking — product events + Mixpanel. | fn`is_enabled`, fn`is_session_metrics_enabled`, fn`track`, fn`sync_pro |
| `config.rs` | 237 | Telemetry-engine configuration. | fn`env_telemetry_mode`, fn`deployment_id_from_key`, struct`TelemetryCo |
| `context.rs` | 14 | Git context collection for telemetry events. | fn`collect_git_context`, struct`GitContext` |
| `debug_log.rs` | 961 | Reusable non-blocking file-logging tracing layers for t | fn`install_firehose`, fn`flush`, fn`resolve_debug_target`, fn`sweep_ol |
| `enums.rs` | 64 | Shared telemetry/config enums extracted from shell. | enum`McpInitStrategy`, enum`PrCreationSource`, enum`PermissionMode` |
| `events.rs` | 2162 | Telemetry event structs. Every struct needs a `telemetr | struct`Login`, struct`LoginPickerShown`, struct`LoginMethodChosen`, st |
| `hooks_log.rs` | 124 | Hooks and plugins tracing target and optional file-base | fn`layer` |
| `http.rs` | 21 | Origin/client identification used by the telemetry engi | fn`origin_client_info_from_env` |
| `id.rs` | 156 | Stable agent identifier. | fn`agent_id`, fn`agent_instance_id`, fn`has_workspace_env_markers`, fn |
| `instrumentation.rs` | 634 | A wrapper layer that filters events by target name. | fn`current_mode`, fn`layer`, fn`install_panic_hook`, fn`generate_chrom |
| `lib.rs` | 41 | Telemetry engine for Grok Build sessions: product event | — |
| `memory_log.rs` | 127 | Memory system tracing target and optional file-based lo | — |
| `memory_telemetry.rs` | 118 | Memory subsystem telemetry. Routes through `log_event`  | struct`MemorySessionInit`, struct`MemorySearch`, struct`MemorySearchEm |
| `otlp_http.rs` | 77 | Shared construction of the blocking `reqwest` client us | fn`build_blocking_client`, struct`BlockingOtlpClient` |
| `prompt_timing.rs` | 57 | Per-turn prompt latency measurement. | struct`PromptTiming` |
| `redact_common.rs` | 72 | Redaction helpers shared by the **internal** OTLP span  | fn`redact_owned`, fn`redact_to_owned`, fn`url_origin` |
| `sampling_log.rs` | 70 | Tracing layer for `target: "sampling_log"` → `~/.grok/l | fn`layer` |
| `sentry.rs` | 427 | Per-host config; everything that varies between binarie | fn`init`, fn`flush_on_shutdown`, struct`Config` |
| `session_ctx.rs` | 326 | Ambient session context for telemetry — product events  | fn`begin_prompt_id`, fn`external_ctx_snapshot`, fn`with_session_ctx`,  |
| `session_metrics.rs` | 213 | Session lifecycle event structs. | struct`SessionStarted`, struct`Turn`, struct`TurnCompletedLifecycle`,  |
| `unified_log.rs` | 505 | Centralized unified log for cross-component session obs | fn`set_version`, fn`file_size`, fn`trim_file`, fn`emit`, fn`ingest_cli |

### `external/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `config.rs` | 743 | Configuration resolution for the external OTEL stream. | fn`parse_header_list`, struct`ContentGates`, struct`ExternalClientInfo |
| `emit.rs` | 269 | The *how* of external emission: content-gate applicatio | fn`emit_record`, struct`Instruments` |
| `mod.rs` | 480 | Opt-in, content-redacted **external OTEL** telemetry st | fn`init`, fn`is_active`, fn`emit`, fn`set_identity`, fn`set_identity_o |
| `providers.rs` | 585 | Provider construction for the external stream: `SdkLogg | fn`build`, struct`BuiltProviders` |
| `redact.rs` | 248 | Export-time fail-closed validators for the external str | struct`ExportHealth`, struct`RedactingLogExporter`, struct`ValidatingM |
| `schema.rs` | 1120 | External OTEL schema v1: event names, attribute keys, t | fn`external_allowed_keys`, fn`gate_for_key`, fn`sanitize_client_identi |
| `tests.rs` | 994 | Unit tests for the external stream: pinned allowlists,  | — |
| `truncate.rs` | 186 | Truncation helpers for the external OTEL stream. | fn`truncate_value`, fn`truncate_value_owned`, fn`truncate_content`, fn |

### `otel_layer/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 769 | Shared OpenTelemetry tracing layer for exporting spans  | fn`build_otel_layer`, fn`shutdown_otel`, fn`otel_guard`, struct`OtelLa |
| `redact.rs` | 636 | Adding a span attribute (default-deny via `enforce_allo | — |

---

## 4. 工作区依赖

`xai-grok-env` · `xai-grok-secrets` · `xai-mixpanel` · `xai-grok-config` · `xai-grok-sampler` · `xai-token-estimation` · `xai-file-utils` · `xai-grok-auth`

---

## 5. 被谁依赖

`xai-grok-http` · `xai-grok-mcp` · `xai-grok-memory` · `xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-pager-render` · `xai-grok-shell` · `xai-grok-telemetry` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-telemetry
cargo test -p xai-grok-telemetry
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

