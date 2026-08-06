# xai-ratatui-textarea

> 路径：`crates/codegen/xai-ratatui-textarea`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

`xai-ratatui-textarea` 是 Grok Build codegen 工作区成员。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `editor` | External cursor requests use nearest grapheme boundaries; ties go left for deter |
| `render` | — |
| `textarea` | Stable, unique identifier for a text element. Monotonically increasing, never re |
| `wrapping` | Like `wrap_ranges` but returns ranges without trailing whitespace and |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `editor.rs` | 940 | External cursor requests use nearest grapheme boundarie | struct`EditDelta`, struct`EditPlan`, struct`SingleLineViewport`, struc |
| `editor_keys.rs` | 177 | — | fn`classify_key_event` |
| `lib.rs` | 31 | — | fn`is_altgr`, fn`is_altgr` |
| `textarea.rs` | 9767 | Stable, unique identifier for a text element. Monotonic | fn`is_undo_input`, struct`ElementId`, struct`ElementKind`, struct`Inte |
| `wrapping.rs` | 605 | Like `wrap_ranges` but returns ranges without trailing  | fn`wrap_ranges`, fn`wrap_ranges_trim`, fn`word_wrap_line`, fn`word_wra |

### `editor_tests/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `editing.rs` | 278 | — | — |
| `keys.rs` | 318 | — | — |
| `mod.rs` | 21 | — | — |
| `planning.rs` | 278 | — | — |
| `viewport.rs` | 247 | — | — |

### `render/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `line_utils.rs` | 59 | Clone a borrowed ratatui `Line` into an owned `'static` | fn`line_to_static`, fn`push_owned_lines`, fn`is_blank_line_spaces_only |
| `mod.rs` | 1 | — | — |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-grok-markdown` · `xai-grok-pager` · `xai-grok-pager-render` · `xai-ratatui-textarea`

---

## 6. 开发命令

```sh
cargo check -p xai-ratatui-textarea
cargo test -p xai-ratatui-textarea
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

