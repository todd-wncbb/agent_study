# xai-grok-shell-base

> 路径：`crates/codegen/xai-grok-shell-base`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Foundation modules for the grok shell crate family: environment presets, CPU profiling, and process/filesystem utilities.

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `cpu_profile` | Legacy: kept so new clients can still decode adverts from old leaders. |
| `env` | GrokBuildEnvironment configuration for the shell crate family. |
| `util` | Generate a pseudo-random f64 in [0.0, 1.0). |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `cpu_profile.rs` | 1171 | Legacy: kept so new clients can still decode adverts fr | struct`ControlError`, struct`CpuProfileStartOptions`, struct`CpuProfil |
| `env.rs` | 100 | GrokBuildEnvironment configuration for the shell crate  | fn`custom_bridge_disabled`, fn`parse_gateway_url` |
| `lib.rs` | 7 | Foundation modules shared by the grok shell crate famil | — |

### `util/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `changelog.rs` | 359 | Changelog fetching from CDN with local disk cache. | fn`bullets_from_entries`, struct`ChangelogEntry`, struct`Changelog`, s |
| `event_id.rs` | 180 | Event ID generation for session notifications. | fn`generate_event_id`, fn`ensure_event_id_meta`, fn`ensure_event_count |
| `grok_home.rs` | 5 | — | — |
| `mod.rs` | 348 | Generate a pseudo-random f64 in [0.0, 1.0). | fn`random_f64`, fn`probabilistic_sample`, fn`is_cli_chat_proxy_url`, f |
| `secure_file.rs` | 309 | Cross-platform secure file operations. | fn`write_secure_file`, fn`open_secure_file`, fn`ensure_owner_only_perm |
| `tips.rs` | 152 | Tip of the Day — selection logic for tips served from r | fn`pick_and_advance` |
| `uname.rs` | 156 | OS version string for the `<user_info>` preamble. | fn`os_kernel_and_release` |

---

## 4. 工作区依赖

`xai-grok-config` · `xai-grok-env` · `xai-grok-shared` · `xai-grok-version` · `xai-tty-utils`

---

## 5. 被谁依赖

`xai-grok-shell` · `xai-grok-shell-base` · `xai-grok-shell-session-support`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-shell-base
cargo test -p xai-grok-shell-base
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

