# xai-grok-http

> 路径：`crates/codegen/xai-grok-http`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Shared reqwest HTTP clients and User-Agent construction for the grok CLI.

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 636 | HTTP clients for the application. | fn`origin_client_info_from_env`, fn`origin_client_info_from_client_typ |

---

## 4. 工作区依赖

`xai-grok-auth` · `xai-grok-sampler` · `xai-grok-telemetry` · `xai-grok-version` · `xai-grok-workspace`

---

## 5. 被谁依赖

`xai-grok-http` · `xai-grok-memory` · `xai-grok-shell` · `xai-grok-shell-session-support`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-http
cargo test -p xai-grok-http
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

