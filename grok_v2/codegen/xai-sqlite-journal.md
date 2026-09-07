# xai-sqlite-journal

> 路径：`crates/codegen/xai-sqlite-journal`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Filesystem-aware SQLite journal-mode selection: WAL on local disks, rollback journal on network mounts where WAL's mmap'd -shm is unsafe

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 779 | Filesystem-aware SQLite journal-mode selection. | fn`is_network_fs`, enum`JournalMode` |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-fast-worktree` · `xai-grok-memory` · `xai-grok-shell` · `xai-grok-workspace` · `xai-sqlite-journal`

---

## 6. 开发命令

```sh
cargo check -p xai-sqlite-journal
cargo test -p xai-sqlite-journal
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

