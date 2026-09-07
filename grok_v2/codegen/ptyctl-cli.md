# ptyctl-cli

> 路径：`crates/codegen/ptyctl-cli`
> 版本：0.1.0
> 类型：二进制 crate

---

## 1. 概述

CLI for ptyctl headless PTY controller

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `cli.rs` | 227 | CLI argument definitions using clap derive. | struct`Cli`, struct`Target`, enum`Commands` |
| `main.rs` | 174 | ptyctl CLI — headless PTY controller. | — |
| `registry.rs` | 127 | Named session registry stored at ~/.local/state/ptyctl/ | fn`register_session`, fn`lookup_session`, fn`unregister_session`, fn`s |

### `commands/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `client.rs` | 196 | Client commands — send/screen/status/cursor/resize/stop | fn`send`, fn`screen`, fn`cursor`, fn`status`, fn`resize` |
| `mod.rs` | 2 | — | — |
| `run.rs` | 119 | `ptyctl run` — spawn a PTY session and start the HTTP s | fn`run` |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`ptyctl-cli`

---

## 6. 开发命令

```sh
cargo check -p ptyctl-cli
cargo test -p ptyctl-cli
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

