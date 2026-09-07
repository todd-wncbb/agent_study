# xai-grok-workspace-client

> 路径：`crates/codegen/xai-grok-workspace-client`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Lightweight typed client for hub-proxied workspace.* RPCs (shared by xai-grok-shell proxy mode and other consumers)

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 810 | Typed client for hub-proxied `workspace.*` RPC methods  | fn`consume_stream_terminal`, fn`is_transport_fatal`, struct`WorkspaceC |

---

## 4. 工作区依赖

`xai-computer-hub-sdk` · `xai-grok-workspace-types` · `xai-tool-protocol` · `xai-tool-runtime`

---

## 5. 被谁依赖

`xai-grok-workspace` · `xai-grok-workspace-client`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-workspace-client
cargo test -p xai-grok-workspace-client
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

