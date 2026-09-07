# xai-fsnotify

> 路径：`crates/codegen/xai-fsnotify`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Local-filesystem event source: single causal stream of semantic FsEvents

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `error.rs` | 14 | Terminal in-process error from [`crate::FsEventSource:: | enum`FsNotifyError` |
| `event.rs` | 76 | Public event types — the wire contract for `xai-fsnotif | enum`FsEvent`, enum`FsEventKind`, enum`GitMetaKind` |
| `lib.rs` | 20 | Local-filesystem event source. Single causal stream of  | — |
| `paths.rs` | 85 | `.git/` path classification. Component-based against th | fn`classify_git_path` |
| `source.rs` | 1498 | [`FsEventSource`] — owns the OS watcher, runs the async | fn`stats`, fn`set_runtime_handle`, fn`shared`, struct`FsWatcherStats`, |
| `state.rs` | 373 | Lock-state machine. Pure data + pure transition functio | fn`drive`, struct`StaleWarn`, enum`LockState`, enum`LockTransition` |
| `watcher.rs` | 3881 | Filesystem notifications with debouncing and gitignore  | fn`sapling_enabled`, fn`watch_strategy`, fn`max_watch_budget`, fn`find |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-fsnotify` · `xai-grok-shell` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-fsnotify
cargo test -p xai-fsnotify
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

