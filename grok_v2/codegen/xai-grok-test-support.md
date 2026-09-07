# xai-grok-test-support

> 路径：`crates/codegen/xai-grok-test-support`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Shared test-support for grok-build crates: mock inference server, SSE generators, ACP stdio client, headless runner, env sandbox

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `acp_client` | ACP stdio clients for testing grok sessions end-to-end: the typed |
| `counting_server` | Minimal connection-counting HTTP/1.1 server for wire-level tests that need |
| `env` | Binary resolution, serial env guards, and git sandbox creation. |
| `headless` | Headless mode (`grok -p`) test runner. |
| `leader` | Leader-mode (`grok agent --leader stdio`) test harness. |
| `mock_server` | Mock inference server with request logging and automatic cleanup. |
| `process` | Shared subprocess lifecycle ownership for grok-build test harnesses. |
| `sandbox` | Hermetic filesystem and child-environment owner for grok integration tests. |
| `scripted` | Data-driven scripted responses for the mock inference server: plain |
| `sse` | SSE stream generators for mock inference endpoints. |
| `uds_proxy` | Frame-aware fault-injection proxy for unix-domain-socket IPC. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `acp_client.rs` | 560 | ACP stdio clients for testing grok sessions end-to-end: | struct`GrokStdioClient`, struct`RawStdioClient` |
| `counting_server.rs` | 68 | Minimal connection-counting HTTP/1.1 server for wire-le | fn`spawn_counting_server` |
| `env.rs` | 131 | Binary resolution, serial env guards, and git sandbox c | fn`grok_binary`, fn`git_workdir`, struct`EnvGuard` |
| `headless.rs` | 361 | Headless mode (`grok -p`) test runner. | fn`run_headless`, fn`run_headless_with_env`, fn`run_headless_in_sandbo |
| `inference_override.rs` | 638 | Inference endpoint matched by a scripted expectation. | struct`InferenceRequestMatcher`, struct`InferenceExpectation`, struct` |
| `leader.rs` | 920 | Leader-mode (`grok agent --leader stdio`) test harness. | fn`leader_binary`, fn`client_binary`, fn`leader_lock_path`, fn`read_le |
| `lib.rs` | 70 | Shared test utilities for grok-build crates: mock infer | fn`scaled` |
| `mock_server.rs` | 2103 | Mock inference server with request logging and automati | struct`LogEntry`, struct`RequestLog`, struct`MockModelEntry`, struct`S |
| `process.rs` | 1253 | Shared subprocess lifecycle ownership for grok-build te | fn`process_has_exited_without_reap`, struct`TestProcessConfig`, struct |
| `sandbox.rs` | 919 | Hermetic filesystem and child-environment owner for gro | struct`TestSandbox`, struct`TestSandboxBuilder` |
| `scripted.rs` | 174 | Data-driven scripted responses for the mock inference s | struct`SseEvent`, struct`ScriptedResponse`, enum`ScriptedBody` |
| `sse.rs` | 1118 | SSE stream generators for mock inference endpoints. | fn`messages_api_events`, fn`chat_completion_events`, fn`chat_completio |
| `uds_proxy.rs` | 512 | Frame-aware fault-injection proxy for unix-domain-socke | struct`FaultPlan`, struct`FaultHandle`, struct`UdsProxy`, enum`FaultDi |

---

## 4. 工作区依赖

`xai-acp-lib` · `xai-tty-utils`

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-pager-pty-harness` · `xai-grok-sampler` · `xai-grok-shell` · `xai-grok-test-support`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-test-support
cargo test -p xai-grok-test-support
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

