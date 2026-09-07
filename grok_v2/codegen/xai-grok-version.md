# xai-grok-version

> 路径：`crates/codegen/xai-grok-version`
> 版本：0.2.111
> 类型：库 crate

---

## 1. 概述

Lockstepped grok CLI version.

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 75 | Installed grok CLI version, lockstepped with shipping b | fn`installed`, fn`installed_semver`, fn`display_version`, fn`display_v |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-file-utils` · `xai-grok-config` · `xai-grok-http` · `xai-grok-mcp` · `xai-grok-memory` · `xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-pager-minimal` · `xai-grok-sampler` · `xai-grok-shell` · `xai-grok-shell-base` · `xai-grok-shell-session-support` · `xai-grok-tools` · `xai-grok-update` · `xai-grok-version`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-version
cargo test -p xai-grok-version
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

