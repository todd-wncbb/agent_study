# xai-codebase-graph

> 路径：`crates/codegen/xai-codebase-graph`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

High-performance code graph generation using tree-sitter queries

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `index_manager` | Channel-based IndexManager for incremental reindexing. |
| `interner` | Arena-based string interner for memory-efficient string deduplication. |
| `languages` | Registry of all supported languages. |
| `manager` | Index management: building, caching, locking, and updating. |
| `navigation` | Location-based navigation APIs for go-to-definition and go-to-references. |
| `scope_graph` | ScopeGraph module for per-file symbol tracking. |
| `types` | Core types for the goto_index crate. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `index_manager.rs` | 2185 | Channel-based IndexManager for incremental reindexing. | fn`is_binary_content`, struct`FileEvent`, struct`QueryResult`, struct` |
| `interner.rs` | 413 | Arena-based string interner for memory-efficient string | struct`StringId`, struct`StringInterner` |
| `lib.rs` | 102 | # xai-codebase-graph | — |
| `navigation.rs` | 844 | Location-based navigation APIs for go-to-definition and | struct`NavigationResult`, struct`Location`, struct`Navigator`, enum`Na |

### `bin/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `bench_file_listing.rs` | 233 | Benchmark for comparing git CLI vs git2 file listing. | — |
| `bench_index.rs` | 64 | Benchmark binary for index building. | — |
| `code_graph.rs` | 394 | CLI tool for code graph navigation. | — |

### `languages/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `golang.rs` | 73 | — | fn`golang` |
| `javascript.rs` | 94 | JavaScript/JSX language configuration. | fn`js_lang` |
| `mod.rs` | 146 | Registry of all supported languages. | struct`LanguageRegistry` |
| `python.rs` | 38 | Python language configuration. | fn`python_lang` |
| `rust.rs` | 198 | — | fn`rust_lang` |
| `ts.rs` | 241 | — | fn`ts_lang` |
| `types.rs` | 82 | Function type for getting a tree-sitter language gramma | struct`TSLanguageConfig` |

### `manager/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `builder.rs` | 507 | Parallel pipelined index builder with thread-local cach | struct`IndexBuilder`, enum`IndexError` |
| `cache.rs` | 106 | Index caching for fast loading. | fn`get_cache_path`, fn`load_index`, fn`save_index`, fn`save_index_asyn |
| `lock.rs` | 539 | Workspace-level locking for index operations. | fn`try_lock`, fn`is_operation_in_progress`, struct`WorkspaceLockGuard` |
| `mod.rs` | 14 | Index management: building, caching, locking, and updat | — |

### `scope_graph/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `edges.rs` | 22 | Edge types for the ScopeGraph. | enum`EdgeKind` |
| `graph.rs` | 1726 | The main ScopeGraph structure. | fn`scope_graph_from_definitions_query`, fn`extract_symbols_fast`, stru |
| `mod.rs` | 38 | ScopeGraph module for per-file symbol tracking. | fn`build_scope_graph`, struct`ScopeGraphResult` |
| `nodes.rs` | 180 | Node types for the ScopeGraph. | struct`Symbol`, struct`SymbolId`, struct`LocalScope`, struct`LocalDef` |

### `types/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `file_event.rs` | 78 | File change events for live index updates. | enum`FileEvent` |
| `location.rs` | 108 | Location type for query results. | struct`Location` |
| `mod.rs` | 119 | Core types for the goto_index crate. | struct`IndexStats`, struct`SymbolOccurrence`, struct`SymbolAlias`, str |
| `range.rs` | 365 | Position and Range types for representing source code l | struct`Position`, struct`Range` |

---

## 4. 工作区依赖

`xai-grok-paths`

---

## 5. 被谁依赖

`xai-codebase-graph` · `xai-grok-shell` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-codebase-graph
cargo test -p xai-codebase-graph
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

