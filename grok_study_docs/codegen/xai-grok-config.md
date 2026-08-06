# xai-grok-config

> 路径：`crates/codegen/xai-grok-config`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Shared config loading for Grok — grok_home, effective config (requirements > user > managed), TOML merge

config.toml 加载。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `campaigns` | `[[campaigns]]` overlays. Priority (first id wins): requirements > remote > |
| `config_override` | Shared take/apply for `[[version_overrides]]` / `[[campaigns]]` arrays. |
| `fs_atomic` | Atomic file writes, shared by the managed-cache marker, the signature |
| `global_hook_sources` | Grok-owned direct global hook paths shared by shell discovery and sandbox |
| `managed_text` | Item-addressable edits for marked blocks in line-comment config files. |
| `shell` | Windows shell detection for terminal command execution. |
| `signed_policy` | Ed25519-signed, identity-bound managed-policy envelope. |
| `version_overrides` | Version-aware config layering. A `[[version_overrides]]` array carries |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `campaigns.rs` | 294 | `[[campaigns]]` overlays. Priority (first id wins): req | fn`take_campaigns`, fn`build_campaign_entries`, fn`merge_campaign_entr |
| `config_override.rs` | 175 | Shared take/apply for `[[version_overrides]]` / `[[camp | fn`take_patch_array`, fn`patch_touches_path`, fn`patch_touches_any`, f |
| `fs_atomic.rs` | 43 | Atomic file writes, shared by the managed-cache marker, | fn`write_atomically` |
| `global_hook_sources.rs` | 569 | Grok-owned direct global hook paths shared by shell dis | fn`path_has_symlink_component`, fn`existing_ancestor_chain`, fn`is_fil |
| `lib.rs` | 82 | Config file loading for Grok. | fn`env_bool` |
| `loader.rs` | 709 | TOML loading, layered merging, and `$VAR` expansion. | fn`load_toml_file`, fn`toml_error_detail`, fn`load_config_file`, fn`lo |
| `macos_managed.rs` | 196 | macOS MDM managed-preferences layer. | fn`managed_preferences_requirements` |
| `managed_cache.rs` | 600 | The managed-config cloud-cache subsystem: the sync mark | fn`is_managed_config_stale_for`, fn`mark_managed_config_synced`, fn`ma |
| `paths.rs` | 332 | Filesystem locations for grok config files and binaries | fn`default_grok_home`, fn`grok_home`, fn`user_grok_home`, fn`grok_appl |
| `shell.rs` | 682 | Windows shell detection for terminal command execution. | fn`detect_windows_shell`, fn`chain_separator`, fn`has_unix_utilities`, |
| `signed_policy.rs` | 546 | Ed25519-signed, identity-bound managed-policy envelope. | fn`verification_active`, fn`embedded_key_id_trusted`, fn`verify_signed |
| `validation.rs` | 467 | Requirements layers and fail-closed enforcement. | fn`requirements_layers`, fn`load_merged_requirements`, fn`load_require |
| `version_overrides.rs` | 212 | Version-aware config layering. A `[[version_overrides]] | fn`apply_version_overrides`, struct`VersionOverrideMeta`, enum`Version |

### `managed_cache/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `tests.rs` | 1366 | The authoritative signed verdict wins over the marker B | — |

### `managed_text/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `format.rs` | 526 | — | struct`CommentSyntax` |
| `mod.rs` | 292 | Item-addressable edits for marked blocks in line-commen | struct`ManagedItem`, struct`ManagedConfigRequest`, struct`ManagedTextI |
| `source.rs` | 421 | — | — |
| `tests.rs` | 632 | — | — |
| `transaction.rs` | 454 | — | — |
| `validator.rs` | 244 | Optional syntax checker. `path` is appended after `args | struct`SyntaxValidator` |

### `signed_policy/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `tests.rs` | 969 | Pins the wire contract: a raw server-shaped JSON payloa | — |

---

## 4. 工作区依赖

`xai-tty-utils` · `xai-grok-version`

---

## 5. 被谁依赖

`xai-grok-agent` · `xai-grok-config` · `xai-grok-config-types` · `xai-grok-hooks` · `xai-grok-mcp` · `xai-grok-memory` · `xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-pager-render` · `xai-grok-plugin-marketplace` · `xai-grok-sandbox` · `xai-grok-shared` · `xai-grok-shell` · `xai-grok-shell-base` · `xai-grok-telemetry`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-config
cargo test -p xai-grok-config
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

