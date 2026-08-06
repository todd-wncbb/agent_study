# xai-grok-shared

> 路径：`crates/codegen/xai-grok-shared`
> 类型：库 crate

---

## 1. 概述

`xai-grok-shared` 是 Grok Build codegen 工作区成员。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `clipboard` | Cross-platform system clipboard access. |
| `placeholder_images` | Shared helper for resolving `[Image #N: <path>]` placeholders into |
| `session` | — |
| `stderr` | Serialized access to the TUI's stderr writer. |
| `ui_config` | Model ID to use for the secondary agent when forking. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `clipboard.rs` | 3347 | Cross-platform system clipboard access. | fn`get_text`, fn`get_primary_text`, fn`x11_display_env_present`, fn`ge |
| `lib.rs` | 9 | Shared utilities used by both `xai-grok-shell` and its  | — |
| `placeholder_images.rs` | 1526 | Shared helper for resolving `[Image #N: <path>]` placeh | fn`display_number_meta`, fn`display_number_from_meta`, fn`attached_ima |
| `stderr.rs` | 50 | Serialized access to the TUI's stderr writer. | fn`stderr_lock`, fn`with_locked_stderr` |
| `ui_config.rs` | 407 | Model ID to use for the secondary agent when forking. | struct`UiConfig`, struct`ContextualHints` |

### `session/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `info.rs` | 8 | Session identity: `id` + `cwd`. | struct`Info` |
| `mod.rs` | 13 | — | fn`session_dir` |

---

## 4. 工作区依赖

`xai-grok-config-types` · `xai-grok-models` · `xai-grok-tools` · `xai-tty-utils`

---

## 5. 被谁依赖

`xai-grok-pager-render` · `xai-grok-shared` · `xai-grok-shell` · `xai-grok-shell-base`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-shared
cargo test -p xai-grok-shared
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

