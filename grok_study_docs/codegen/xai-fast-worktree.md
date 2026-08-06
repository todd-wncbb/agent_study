# xai-fast-worktree

> 路径：`crates/codegen/xai-fast-worktree`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

High-performance git worktree creation using CoW cloning

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `btrfs` | BTRFS snapshot support for fast worktree creation. |
| `db` | SQLite-backed metadata database for tracking worktrees. |
| `discovery` | Filesystem scanner for discovering worktrees not yet tracked in the DB. |
| `sync` | Sync a pre-created worktree to match a source repo's current state. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `api.rs` | 4746 | Public API for fast worktree creation. | fn`cwd_test_guard`, fn`remove_worktree`, fn`remove_worktree_with_deleg |
| `auto_gc.rs` | 2379 | Throttled automatic worktree GC (feature `metadata`). | fn`default_max_age_by_kind`, fn`process_cwd_scan_available`, fn`age_ex |
| `discovery.rs` | 399 | Filesystem scanner for discovering worktrees not yet tr | fn`discover_worktrees`, fn`path_under_managed_worktree_roots`, fn`rebu |
| `lib.rs` | 82 | High-performance git worktree creation using CoW clonin | fn`count_tracked_files` |
| `mount_info.rs` | 531 | Shared `/proc/self/mountinfo` parser. | fn`parse_mountinfo`, fn`overlay_upperdirs_all_namespaces`, fn`parse_mo |
| `sync.rs` | 1912 | Sync a pre-created worktree to match a source repo's cu | fn`collect_source_dirty_state`, struct`SourceDirtyState`, struct`SyncR |
| `util.rs` | 7 | Return current time as a unix timestamp string (e.g., ` | fn`unix_timestamp_string` |

### `bin/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `cli.rs` | 214 | CLI for fast git worktree creation. | — |
| `pool_perf_bench.rs` | 808 | Pool performance benchmark — emulates the A/B worktree  | — |

### `btrfs/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `detect.rs` | 728 | BTRFS filesystem and subvolume detection. | fn`is_btrfs`, fn`get_bind_mount_info`, fn`get_btrfs_mount_point`, fn`i |
| `mod.rs` | 24 | BTRFS snapshot support for fast worktree creation. | — |
| `snapshot.rs` | 1048 | BTRFS snapshot creation. | fn`create_snapshot`, fn`create_snapshot_with_symlink`, fn`snapshot_des |

### `copy/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `cow.rs` | 117 | Copy-on-Write file cloning using the reflink-copy crate | fn`clone_file`, fn`replace_symlink` |
| `engine.rs` | 442 | Parallel copy engine. | fn`copy_parallel` |
| `gitdir.rs` | 569 | Selective CoW copy of `.git/` directory for standalone  | fn`copy_git_dir`, struct`GitDirCopyStats` |
| `mod.rs` | 15 | Filesystem replication engine used by fast worktree cre | — |
| `shard.rs` | 90 | Hash-based shard assignment for parallel file operation | fn`shard_for_path`, fn`short_path_hash` |
| `skip.rs` | 112 | Skip logic for copy operations (gitignore + additional  | fn`build_skip_matcher`, fn`collect_unignored_paths` |
| `types.rs` | 77 | Shared types for copy operations. | struct`DirtyFilesReport`, struct`CopyStats`, struct`CopyEntry`, struct |
| `worker.rs` | 135 | Worker logic for replicating a single filesystem entry. | fn`run_worker`, struct`WorkerCtx` |

### `db/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 461 | SQLite-backed metadata database for tracking worktrees. | fn`id_from_path`, fn`repo_name_from_path`, fn`now_epoch_secs`, fn`reso |
| `queries.rs` | 231 | — | fn`register`, fn`unregister`, fn`unregister_by_path`, fn`mark_dead`, f |
| `schema.rs` | 35 | — | — |
| `tests.rs` | 690 | The derived id keeps the basename (minus any `worktree- | — |

### `git/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `checkout.rs` | 1229 | Git checkout/reset/clean operations used during worktre | fn`git_command`, fn`git_reset_hard_command`, fn`git_clean_fd`, fn`chec |
| `discovery.rs` | 151 | Repository/worktree discovery helpers. | fn`find_git_dir`, fn`find_worktree_git_dir`, fn`find_worktree_root`, f |
| `index.rs` | 307 | Git index operations used during worktree creation. | fn`copy_git_index`, fn`update_index_stats` |
| `mod.rs` | 20 | Git operations used by fast worktree creation. | — |
| `status.rs` | 106 | Git status helpers (compute dirty paths). | fn`get_modified_files`, struct`ModifiedFilesResult` |
| `worktree.rs` | 30 | Git worktree operations. | fn`worktree_add_no_checkout` |

### `overlay/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `detect.rs` | 190 | FUSE+overlay detection. | fn`detect_fuse_overlay`, fn`detect_fuse_overlay_from_entries`, struct` |
| `mod.rs` | 15 | Overlay-on-FUSE worktree support. | — |
| `snapshot.rs` | 769 | Overlay worktree creation and removal. | fn`create_overlay_worktree`, fn`remove_overlay_worktree`, fn`try_remov |

### `worktree/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `execute.rs` | 1731 | Worktree plan execution. | fn`cleanup_snapshot_git_state`, fn`execute_create_worktree` |
| `mod.rs` | 1352 | Worktree orchestration: plan + execute. | fn`execute_plan`, struct`CreateWorktreeResult` |
| `plan.rs` | 64 | Worktree execution planning. | struct`WorktreePlan` |

---

## 4. 工作区依赖

`xai-gix-status` · `xai-sqlite-journal` · `xai-tty-utils`

---

## 5. 被谁依赖

`xai-fast-worktree` · `xai-grok-pager` · `xai-grok-shell` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-fast-worktree
cargo test -p xai-fast-worktree
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

