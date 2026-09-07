# xai-ratatui-inline

> 路径：`crates/codegen/xai-ratatui-inline`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

`xai-ratatui-inline` 是 Grok Build codegen 工作区成员。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `common.rs` | 135 | Trait for terminal operations needed by emit_to_scrollb | fn`with_synchronized_output`, trait`TerminalLike` |
| `lib.rs` | 16 | — | — |
| `resize.rs` | 357 | Handles terminal resize by completely re-rendering the  | fn`resize_purge_rerender`, fn`resize_viewport_height` |
| `scrollback.rs` | 234 | — | fn`emit_to_scrollback` |
| `segment.rs` | 372 | Represents a line segment (physical row) with its conte | fn`split_into_line_segments`, struct`LineSegment` |
| `terminal.rs` | 1448 | A hyperlink region on a single screen row, in absolute  | struct`LinkSpan`, struct`OurFrame`, struct`Terminal` |
| `tests.rs` | 417 | Mock terminal for testing | struct`MockTerminal`, struct`MockWriter` |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-pager-minimal` · `xai-grok-pager-render` · `xai-ratatui-inline`

---

## 6. 开发命令

```sh
cargo check -p xai-ratatui-inline
cargo test -p xai-ratatui-inline
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

