# xai-grok-sandbox

> 路径：`crates/codegen/xai-grok-sandbox`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

OS-level sandboxing for Grok Build using kernel primitives (Landlock/Seatbelt) via nono

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `child_net` | Seccomp: child network filter (pre_exec) and process-wide namespace lockdown. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `child_net.rs` | 363 | Seccomp: child network filter (pre_exec) and process-wi | — |
| `hook_write_deny.rs` | 437 | Grok-owned hook write-deny: plan, identity revalidation | fn`profile_enforces_hook_write_deny`, fn`capture_path_identity`, fn`re |
| `lib.rs` | 901 | OS-level sandboxing for Grok Build via [nono](https://c | fn`requires_hook_write_deny`, fn`is_inside_bwrap`, fn`trust_bwrap_mark |
| `logging.rs` | 105 | Sandbox event logger. | struct`SandboxLogger` |
| `network_policy.rs` | 501 | Pure policy modeling for future child website egress. | struct`WebsiteOrigin`, struct`WebsitePolicy`, struct`NetworkPolicySnap |
| `paths.rs` | 91 | Filesystem path tables for sandbox profiles. | fn`grok_home`, fn`temp_writable_paths`, fn`essential_writable_paths`,  |
| `profiles.rs` | 1011 | Sandbox profiles. Built-in: `workspace`, `devbox`, `rea | fn`load_sandbox_config`, fn`sandbox_profile_conflicts`, struct`Sandbox |
| `types.rs` | 193 | Types for sandbox events, metrics, and profile configur | struct`SandboxEvent`, struct`SandboxMetrics`, enum`SandboxEventType` |

### `deny/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `glob.rs` | 843 | Glob deny entries: detection, the macOS Seatbelt-regex  | fn`is_glob`, fn`partition_deny_entries`, fn`validate_deny_glob`, fn`ap |
| `mod.rs` | 458 | Kernel-enforced deny paths for sandbox profiles. | fn`ancestors_within_writable_roots`, fn`apply_write_deny_paths_to_capa |

---

## 4. 工作区依赖

`xai-grok-config`

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-sandbox` · `xai-grok-shell` · `xai-grok-tools` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-sandbox
cargo test -p xai-grok-sandbox
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

