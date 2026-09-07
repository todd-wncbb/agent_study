# ptyctl

> 路径：`crates/codegen/ptyctl`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Headless PTY controller built on alacritty_terminal

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `keys` | Vim-style key notation parser. |
| `pty` | PTY wrapper using `portable-pty` for cross-platform pseudoterminal support. |
| `server` | HTTP + WebSocket server exposing PTY session control. |
| `session` | Core PTY session — ties PTY + Terminal + I/O channels together. |
| `styled` | Styled output formatters — styled JSON and HTML rendering. |
| `term` | Wrapper around `alacritty_terminal::Term` for headless terminal emulation. |
| `wait` | Event-driven wait/expect primitives over the terminal grid. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `keys.rs` | 193 | Vim-style key notation parser. | fn`parse_keys` |
| `lib.rs` | 13 | ptyctl — Headless PTY controller built on alacritty_ter | — |
| `pty.rs` | 155 | PTY wrapper using `portable-pty` for cross-platform pse | struct`PtyConfig`, struct`PtyHandle`, struct`PtyMaster`, struct`PtyChi |
| `server.rs` | 495 | HTTP + WebSocket server exposing PTY session control. | fn`build_router`, struct`ScreenParams`, struct`SendRequest`, struct`Re |
| `session.rs` | 606 | Core PTY session — ties PTY + Terminal + I/O channels t | struct`SessionConfig`, struct`SessionStatus`, struct`PtySession` |
| `styled.rs` | 316 | Styled output formatters — styled JSON and HTML renderi | fn`extract_styled_line`, fn`render_html`, struct`StyledRun`, struct`St |
| `term.rs` | 358 | Wrapper around `alacritty_terminal::Term` for headless  | struct`SessionListener`, struct`ScreenOpts`, struct`ScreenOutput`, str |
| `wait.rs` | 213 | Event-driven wait/expect primitives over the terminal g | fn`push_raw_tail`, struct`WaitOutcome`, struct`WaitDiagnostics`, struc |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`ptyctl` · `ptyctl-cli` · `xai-grok-pager-pty-harness`

---

## 6. 开发命令

```sh
cargo check -p ptyctl
cargo test -p ptyctl
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

