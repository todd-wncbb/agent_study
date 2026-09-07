# xai-grok-shell-session-support

> 路径：`crates/codegen/xai-grok-shell-session-support`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Session-support modules for the grok shell crate family: managed MCP credential/catalog caching and file-access tracking.

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `managed_mcp` | Managed MCP credential resolution via cli-chat-proxy. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 11 | Session-support modules extracted from `xai-grok-shell` | — |
| `managed_mcp.rs` | 1469 | Managed MCP credential resolution via cli-chat-proxy. | fn`fetch_managed_configs`, fn`call_gateway_tool`, fn`fetch_gateway_too |

---

## 4. 工作区依赖

`xai-file-utils` · `xai-grok-http` · `xai-grok-shell-base` · `xai-grok-version` · `xai-grok-workspace`

---

## 5. 被谁依赖

`xai-grok-shell` · `xai-grok-shell-session-support`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-shell-session-support
cargo test -p xai-grok-shell-session-support
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

