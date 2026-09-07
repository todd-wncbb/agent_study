# xai-grok-plugin-marketplace

> 路径：`crates/codegen/xai-grok-plugin-marketplace`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

`xai-grok-plugin-marketplace` 是 Grok Build codegen 工作区成员。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `catalog` | Parse the CI-generated `plugin-index.json` component catalog. |
| `config` | Parse marketplace sources from `~/.grok/config.toml`. |
| `error` | Error types for the marketplace crate. |
| `git` | Git marketplace source support. |
| `index` | Parse repo-level marketplace index. |
| `install_resolve` | Pure resolution logic for `grok plugin install <name>` marketplace refs. |
| `installer` | Install plugins from a marketplace source into the managed plugin storage. |
| `matcher` | Pure keyword matcher over marketplace plugin metadata. |
| `scanner` | Marketplace plugin discovery. |
| `types` | Core types for marketplace browse and install. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `catalog.rs` | 276 | Parse the CI-generated `plugin-index.json` component ca | fn`load_catalog`, struct`PluginCatalog`, struct`CatalogEntry` |
| `config.rs` | 464 | Parse marketplace sources from `~/.grok/config.toml`. | fn`load_require_sha`, fn`env_require_sha`, fn`load_sources`, fn`load_e |
| `error.rs` | 20 | Error types for the marketplace crate. | enum`MarketplaceError` |
| `git.rs` | 680 | Git marketplace source support. | fn`sync_source_cache`, fn`force_sync_source_cache`, fn`sync_source_cac |
| `index.rs` | 588 | Parse repo-level marketplace index. | fn`load_index`, struct`MarketplaceIndex`, struct`IndexOwner`, struct`I |
| `install_resolve.rs` | 677 | Pure resolution logic for `grok plugin install <name>`  | fn`parse_marketplace_ref`, fn`slugify`, fn`addressable_qualifier`, fn` |
| `installer.rs` | 1525 | Install plugins from a marketplace source into the mana | fn`install_from_marketplace`, fn`install_from_remote_url`, fn`update_f |
| `lib.rs` | 122 | Plugin marketplace browse and index crate. | fn`is_official_source_url`, fn`canonical_github_owner_repo` |
| `matcher.rs` | 257 | Pure keyword matcher over marketplace plugin metadata. | fn`match_plugin_keyword`, struct`KeywordCandidate` |
| `scanner.rs` | 721 | Marketplace plugin discovery. | fn`scan_marketplace` |
| `types.rs` | 292 | Core types for marketplace browse and install. | struct`MarketplaceRelativePath`, struct`MarketplaceSource`, struct`Mar |

---

## 4. 工作区依赖

`xai-tty-utils` · `xai-grok-agent` · `xai-grok-config` · `xai-hooks-plugins-types`

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-plugin-marketplace` · `xai-grok-shell`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-plugin-marketplace
cargo test -p xai-grok-plugin-marketplace
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

