# xai-gix-status

> 路径：`crates/codegen/xai-gix-status`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Shared gix status helpers: thread budget under RLIMIT_NPROC so produce-worker spawn cannot abort under panic=abort

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 588 | Shared helpers for `gix` status scans. | fn`compute_gix_status_thread_limit_from`, fn`compute_gix_status_thread |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-fast-worktree` · `xai-gix-status` · `xai-hunk-tracker`

---

## 6. 开发命令

```sh
cargo check -p xai-gix-status
cargo test -p xai-gix-status
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

