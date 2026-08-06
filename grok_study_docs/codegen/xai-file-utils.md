# xai-file-utils

> 路径：`crates/codegen/xai-file-utils`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Local data collection: per-turn event tracking

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `events` | Per-session event log (`events.jsonl`). |
| `gcs` | Shared upload utilities for session persistence and agent telemetry. |
| `queue` | Spill-to-disk upload queue for cloud storage trace artifacts. |
| `s3` | Map an S3 HeadObject error to NotFound / Unauthorized / ProbeFailed. |
| `storage_client` | REST client for uploading files to GCS via cli-chat-proxy. |
| `trace_context` | Extract the current span's W3C `traceparent` string for propagation |
| `upload_config` | Upload destination config and archive-restore metadata shared by the |
| `workspace_classifier` | — |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `circuit_breaker_observer.rs` | 86 | Tracing-based observer for the storage circuit breaker. | struct`TracingObserver` |
| `gcs.rs` | 893 | Shared upload utilities for session persistence and age | fn`upload_bytes`, fn`upload_bytes_signed`, fn`upload_file`, fn`upload_ |
| `lib.rs` | 60 | Local data collection: per-turn event tracking, upload  | fn`with_auth_retry`, fn`sha256_hex`, fn`sha256_hex_from_file` |
| `queue.rs` | 6475 | Spill-to-disk upload queue for cloud storage trace arti | fn`try_remove_temp`, fn`sidecar_path_for`, fn`temp_path_for_sidecar`,  |
| `s3.rs` | 1540 | Map an S3 HeadObject error to NotFound / Unauthorized / | fn`build_s3_client`, fn`presign_put_url`, fn`presign_get_url`, fn`uplo |
| `storage_client.rs` | 3462 | REST client for uploading files to GCS via cli-chat-pro | struct`RetryConfig`, struct`HttpUploadError`, struct`UploadResponse`,  |
| `trace_context.rs` | 316 | Extract the current span's W3C `traceparent` string for | fn`current_traceparent`, fn`inject_trace_context_into_request`, fn`tra |
| `upload_config.rs` | 166 | Upload destination config and archive-restore metadata  | fn`skip_dir_set`, fn`default_untracked_exclude_globs`, fn`default_excl |
| `workspace_classifier.rs` | 375 | — | fn`is_project_dir` |

### `events/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `log.rs` | 179 | Shared event writer for `events.jsonl`. `Clone + Send + | struct`EventWriter` |
| `mod.rs` | 12 | Per-session event log (`events.jsonl`). | — |
| `tracker.rs` | 261 | Per-session event state. `!Send` — lives on the session | struct`EventTracker` |
| `types.rs` | 861 | Schema version for the event log format. Bumped on brea | struct`McpConfigServer`, enum`Event`, enum`InterjectionSource`, enum`R |

---

## 4. 工作区依赖

`xai-circuit-breaker` · `xai-grok-version` · `xai-grok-auth`

---

## 5. 被谁依赖

`xai-file-utils` · `xai-grok-mcp` · `xai-grok-pager` · `xai-grok-shell` · `xai-grok-shell-session-support` · `xai-grok-telemetry` · `xai-grok-tools` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-file-utils
cargo test -p xai-file-utils
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

