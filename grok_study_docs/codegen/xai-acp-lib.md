# xai-acp-lib

> 路径：`crates/codegen/xai-acp-lib`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

`xai-acp-lib` 是 Grok Build codegen 工作区成员。

ACP channel/gateway。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `channel.rs` | 103 | Receiver/sender pair, either for client/agent or agent/ | fn`acp_channels`, fn`acp_send`, struct`AcpChannel` |
| `common.rs` | 115 | The two distinct ways an [`acp_send`](crate::acp_send)  | fn`acp_internal_error`, fn`acp_channel_failure_error`, fn`acp_channel_ |
| `gateway.rs` | 695 | Callback that creates a `tracing::Span` from `_meta` fo | fn`acp_gateway`, struct`AcpGatewayReceiver`, struct`AcpGatewaySender` |
| `lib.rs` | 30 | — | — |
| `line_reader.rs` | 303 | Cancel-safe line-buffered [`AsyncRead`] wrapper. | struct`LineBufferedRead` |
| `message.rs` | 634 | Marker trait representing one side of the ACP connectio | struct`AcpArgsGeneric`, struct`Unboxed`, struct`Boxed`, trait`AcpSide` |
| `normalize.rs` | 181 | Foundation escaped-slash normalization for inbound ACP  | fn`normalize_json_line` |
| `stdin_reader.rs` | 216 | Dedicated-thread reader for the ACP stdio transport's s | fn`spawn_stdin_line_reader` |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-acp-lib` · `xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-shell` · `xai-grok-test-support` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-acp-lib
cargo test -p xai-acp-lib
```

---

## 7. 相关阅读

- [10_acp_protocol.md](../10_acp_protocol.md)

