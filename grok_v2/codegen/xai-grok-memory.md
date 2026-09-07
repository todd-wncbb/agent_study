# xai-grok-memory

> 路径：`crates/codegen/xai-grok-memory`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

`xai-grok-memory` 是 Grok Build codegen 工作区成员。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `archive` | Build a `memory.tar.gz` archive containing session logs and MEMORY.md files. |
| `backend` | Concrete `MemoryBackend` implementation using hybrid search. |
| `chunker` | Markdown-aware semantic chunking. |
| `dream` | autoDream gating and execution logic. |
| `dream_lock` | Dream lock file and session counting infrastructure. |
| `embedding` | Embedding provider abstraction for memory vector search. |
| `index` | SQLite-backed memory index with FTS5 keyword search and optional sqlite-vec KNN. |
| `mmr` | Maximal Marginal Relevance (MMR) diversity re-ranking. |
| `query_expansion` | Query expansion for FTS-only search mode. |
| `schema` | SQL schema constants for the memory index. |
| `search` | Hybrid search combining FTS5 BM25 + sqlite-vec KNN + temporal decay + source wei |
| `storage` | Markdown-based memory file storage. |
| `text_utils` | Pure text-classification helpers shared by the memory flush |
| `watcher` | File watcher for detecting external memory edits. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `archive.rs` | 116 | Build a `memory.tar.gz` archive containing session logs | fn`build_memory_archive` |
| `backend.rs` | 1616 | Concrete `MemoryBackend` implementation using hybrid se | struct`EndpointScopedCredentials`, struct`MemoryBackendParams`, struct |
| `chunker.rs` | 367 | Markdown-aware semantic chunking. | fn`chunk_hash`, fn`chunk_markdown`, fn`header_level`, struct`Chunk` |
| `dream.rs` | 1471 | autoDream gating and execution logic. | fn`check_dream_gates`, fn`is_scaffold_template`, fn`build_dream_user_m |
| `dream_lock.rs` | 488 | Dream lock file and session counting infrastructure. | fn`sessions_since`, struct`DreamLock` |
| `embedding.rs` | 283 | Embedding provider abstraction for memory vector search | struct`ApiEmbeddingProvider`, struct`MockEmbeddingProvider`, trait`Emb |
| `index.rs` | 1286 | SQLite-backed memory index with FTS5 keyword search and | fn`init_sqlite_vec`, struct`ChunkRecord`, struct`FtsResult`, struct`Re |
| `lib.rs` | 109 | Memory system for cross-session knowledge persistence. | fn`embed_missing_chunks` |
| `mmr.rs` | 348 | Maximal Marginal Relevance (MMR) diversity re-ranking. | fn`mmr_rerank` |
| `query_expansion.rs` | 279 | Query expansion for FTS-only search mode. | fn`extract_keywords` |
| `schema.rs` | 98 | SQL schema constants for the memory index. | fn`schema_sql` |
| `search.rs` | 1349 | Hybrid search combining FTS5 BM25 + sqlite-vec KNN + te | fn`hybrid_search`, struct`SearchResult` |
| `storage.rs` | 1862 | Markdown-based memory file storage. | fn`normalize_memory_content`, fn`extract_repo_identity`, fn`slugify`,  |
| `text_utils.rs` | 54 | Pure text-classification helpers shared by the memory f | fn`has_markdown_headers`, fn`is_no_reply` |
| `watcher.rs` | 192 | File watcher for detecting external memory edits. | struct`MemoryFileWatcher` |

---

## 4. 工作区依赖

`xai-grok-auth` · `xai-grok-config-types` · `xai-grok-http` · `xai-grok-telemetry` · `xai-grok-tools` · `xai-grok-version` · `xai-sqlite-journal`

---

## 5. 被谁依赖

`xai-grok-memory` · `xai-grok-shell`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-memory
cargo test -p xai-grok-memory
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

