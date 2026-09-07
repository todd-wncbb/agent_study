# xai-crash-handler

> 路径：`crates/codegen/xai-crash-handler`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Cross-platform crash handler (Unix signals + Windows SEH) with startup crash detection

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `format` | Binary crash blob format ("GCRX"). |
| `symbolicate` | Backtrace symbolication for crash reports. |
| `terminal` | Terminal restore sequences for signal handler context. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `format.rs` | 214 | Binary crash blob format ("GCRX"). | struct`CrashBlob` |
| `handler.rs` | 982 | Cross-platform crash handler for fatal memory faults. | fn`install`, fn`install_terminal_restore_only`, fn`enable_terminal_esc |
| `lib.rs` | 259 | Cross-platform crash handler with startup crash detecti | fn`install`, fn`install_terminal_restore_only`, fn`enable_terminal_esc |
| `symbolicate.rs` | 143 | Backtrace symbolication for crash reports. | fn`resolve_frames`, fn`format_report`, fn`signal_name`, struct`Resolve |
| `terminal.rs` | 134 | Terminal restore sequences for signal handler context. | fn`restore_in_signal_handler`, fn`restore_in_signal_handler`, fn`resto |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-crash-handler` · `xai-grok-pager` · `xai-grok-pager-bin` · `xai-sqlite-journal`

---

## 6. 开发命令

```sh
cargo check -p xai-crash-handler
cargo test -p xai-crash-handler
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

