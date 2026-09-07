# xai-tty-utils

> 路径：`crates/codegen/xai-tty-utils`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Lightweight process-spawning utilities for TTY safety — detach from controlling terminal, suppress interactive pagers, process-group lifecycle

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 947 | Lightweight process-spawning utilities for TTY safety. | fn`detach_from_tty`, fn`detach_from_tty`, fn`detach_command`, fn`detac |
| `process_scope.rs` | 307 | Kill-handle for a session's child-process trees (`Send  | fn`global_process_scope`, struct`ProcessScope` |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-fast-worktree` · `xai-grok-agent` · `xai-grok-config` · `xai-grok-mermaid` · `xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-pager-pty-harness` · `xai-grok-pager-render` · `xai-grok-plugin-marketplace` · `xai-grok-shared` · `xai-grok-shell` · `xai-grok-shell-base` · `xai-grok-test-support` · `xai-grok-tools` · `xai-grok-voice`

---

## 6. 开发命令

```sh
cargo check -p xai-tty-utils
cargo test -p xai-tty-utils
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

