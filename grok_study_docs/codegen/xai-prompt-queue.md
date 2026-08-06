# xai-prompt-queue

> 路径：`crates/codegen/xai-prompt-queue`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Shared prompt-queue wire types for xai-grok-shell and xai-grok-pager

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `combine.rs` | 247 | Pure merge rules for `[ui].combine_queued_prompts`. | fn`can_merge_front`, fn`can_merge_follower`, fn`combine_prefix_len`, f |
| `lib.rs` | 10 | Shared prompt-queue wire types and combine-queued-promp | — |
| `types.rs` | 218 | Content-block `_meta` key for per-prompt display texts  | struct`QueueEntryMeta`, struct`QueueEntryWire`, struct`QueueChanged` |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-shell` · `xai-prompt-queue`

---

## 6. 开发命令

```sh
cargo check -p xai-prompt-queue
cargo test -p xai-prompt-queue
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

