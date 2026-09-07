# xai-hunk-tracker

> 路径：`crates/codegen/xai-hunk-tracker`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Track file hunks (diffs) with agent/external attribution

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `actor` | HunkTrackerActor - runs in a dedicated tokio task and owns all state. |
| `commands` | Commands sent to the HunkTrackerActor. |
| `diff` | Diff computation using the `similar` crate. |
| `events` | Events emitted by the HunkTrackerActor. |
| `handle` | Handle to communicate with HunkTrackerActor. |
| `loc` | LOC (Lines of Code) tracking — hunk-level attribution records. |
| `types` | Core types for hunk tracking. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `commands.rs` | 159 | Commands sent to the HunkTrackerActor. | enum`HunkTrackerCommand` |
| `diff.rs` | 876 | Diff computation using the `similar` crate. | fn`generate_unified_patch`, fn`generate_hunk_patch`, fn`compute_hunks` |
| `events.rs` | 74 | Events emitted by the HunkTrackerActor. | enum`HunkRemovalReason`, enum`HunkEvent` |
| `handle.rs` | 309 | Handle to communicate with HunkTrackerActor. | struct`HunkTrackerHandle` |
| `lib.rs` | 82 | xai-hunk-tracker - Track file hunks (diffs) with agent/ | — |
| `types.rs` | 970 | Core types for hunk tracking. | struct`HunkId`, struct`HunkLineInfo`, struct`Hunk`, struct`FileSummary |

### `actor/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `actions.rs` | 635 | Action commands for the HunkTrackerActor. | — |
| `file_utils.rs` | 330 | Utilities for safe file reading with binary/UTF-8 detec | fn`is_lfs_pointer`, fn`is_binary`, fn`classify_bytes`, fn`classify_str |
| `git.rs` | 513 | Git operations for the HunkTrackerActor. | — |
| `hunks.rs` | 222 | Hunk recomputation and diff event emission for the Hunk | — |
| `mod.rs` | 604 | HunkTrackerActor - runs in a dedicated tokio task and o | struct`CoalescedBatch`, struct`HunkTrackerActor`, enum`CoalescedPathAc |
| `mutations.rs` | 719 | Mutation commands for the HunkTrackerActor. | — |
| `queries.rs` | 204 | Query commands for the HunkTrackerActor. | — |
| `state.rs` | 145 | Internal state types for the HunkTrackerActor. | struct`RepoSyncState`, struct`FileHunkState`, enum`FileContentState`,  |
| `tests.rs` | 5843 | Tests for HunkTrackerActor hunk management. | — |

### `loc/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 545 | LOC (Lines of Code) tracking — hunk-level attribution r | fn`run_loc_sink`, struct`HunkRecord`, struct`JsonlHunkRecordWriter`, s |
| `tests.rs` | 782 | In-memory writer for testing. | — |

---

## 4. 工作区依赖

`xai-gix-status`

---

## 5. 被谁依赖

`xai-grok-shell` · `xai-grok-workspace` · `xai-hunk-tracker`

---

## 6. 开发命令

```sh
cargo check -p xai-hunk-tracker
cargo test -p xai-hunk-tracker
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

