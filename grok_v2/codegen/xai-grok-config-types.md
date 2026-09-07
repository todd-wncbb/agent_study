# xai-grok-config-types

> 路径：`crates/codegen/xai-grok-config-types`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Leaf configuration value types for the grok CLI, extracted from xai-grok-shell for dependency inversion.

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `flags.rs` | 164 | Config-value resolution leaf types and per-model lazine | struct`Resolved`, struct`BoolFlag`, struct`LazinessDetectorPerModelCon |
| `lib.rs` | 2060 | A remote `campaigns[]` entry: an `id` gate plus a full- | struct`CampaignOverride`, struct`DoomLoopRecoverySettings`, struct`Wor |
| `mcp.rs` | 657 | MCP server configuration value types, extracted from xa | struct`McpJsonOAuthBlock`, struct`McpSetupConfig`, struct`McpSetupFiel |
| `memory.rs` | 468 | Memory-system configuration value types, extracted from | struct`MemoryIndexConfig`, struct`MemoryEmbeddingConfig`, struct`Memor |
| `permission.rs` | 59 | Permission-policy config value types, extracted from xa | struct`PermissionConfig`, struct`PermissionRule`, enum`PatternMode`, e |
| `pool.rs` | 103 | Worktree-pool configuration value type, extracted from  | struct`PoolConfig` |

---

## 4. 工作区依赖

`xai-grok-announcements` · `xai-grok-config` · `xai-grok-mcp`

---

## 5. 被谁依赖

`xai-grok-config-types` · `xai-grok-memory` · `xai-grok-shared` · `xai-grok-shell` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-config-types
cargo test -p xai-grok-config-types
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

