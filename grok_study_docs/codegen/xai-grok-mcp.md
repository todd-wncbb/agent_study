# xai-grok-mcp

> 路径：`crates/codegen/xai-grok-mcp`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

MCP integration crate. Quarantines rmcp + reqwest 0.13 (rmcp 2.1 requires reqwest >= 0.13.2 while the rest of the workspace uses reqwest 0.12) and owns the MCP credential store and OAuth flow orchestrator.

MCP 客户端（rmcp 隔离）。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `acp_transport` | rmcp transport bridge over the ACP reverse channel. |
| `credentials` | Persistent credential storage for MCP server OAuth tokens. |
| `liveness` | Per-`Ready`-client transport-closed poller. |
| `mcp_http_client` | MCP HTTP client wrapper that throttles SSE reconnects with exponential |
| `oauth` | OAuth flow orchestration for local MCP servers. |
| `oauth_config` | OAuth configuration types for MCP servers. |
| `servers` | MCP server integration using the official rmcp SDK. |
| `wire` | Single source of truth for the `x.ai/mcp/*` ACP wire strings. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `acp_transport.rs` | 735 | rmcp transport bridge over the ACP reverse channel. | fn`acp_bridge_transport`, trait`AcpReverseInvoker` |
| `credentials.rs` | 518 | Persistent credential storage for MCP server OAuth toke | struct`McpCredentialStore`, struct`McpCredentialStoreAdapter`, enum`Mc |
| `lib.rs` | 38 | MCP integration crate. | — |
| `liveness.rs` | 354 | Per-`Ready`-client transport-closed poller. | fn`spawn_transport_liveness`, struct`TransportLivenessHandle` |
| `mcp_http_client.rs` | 497 | MCP HTTP client wrapper that throttles SSE reconnects w | struct`WarnBudget`, struct`McpHttpClient` |
| `oauth.rs` | 708 | OAuth flow orchestration for local MCP servers. | fn`authenticate_mcp_server_dedup` |
| `oauth_config.rs` | 27 | OAuth configuration types for MCP servers. | struct`McpOAuthConfig` |
| `servers.rs` | 7597 | MCP server integration using the official rmcp SDK. | fn`validate_tool_name`, fn`sanitize_descriptor_segment`, fn`mcp_server |
| `wire.rs` | 27 | Single source of truth for the `x.ai/mcp/*` ACP wire st | — |

---

## 4. 工作区依赖

`xai-grok-version` · `xai-grok-config` · `xai-file-utils` · `xai-grok-tools` · `xai-grok-telemetry` · `xai-grok-workspace-types` · `xai-tool-protocol` · `xai-tool-runtime` · `xai-tool-types`

---

## 5. 被谁依赖

`xai-grok-config-types` · `xai-grok-mcp` · `xai-grok-shell` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-mcp
cargo test -p xai-grok-mcp
```

---

## 7. 相关阅读

- [11_mcp_protocol.md](../11_mcp_protocol.md)

