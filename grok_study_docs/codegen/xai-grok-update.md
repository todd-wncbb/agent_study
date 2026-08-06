# xai-grok-update

> 路径：`crates/codegen/xai-grok-update`
> 版本：0.1.220-alpha.4
> 类型：库 crate

---

## 1. 概述

`xai-grok-update` 是 Grok Build codegen 工作区成员。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `auto_update` | Manual-install one-liner for this platform's bootstrap installer. |
| `version` | Primary CLI base URL: Cloudflare-fronted x.ai endpoint with edge caching |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `auto_update.rs` | 4975 | Manual-install one-liner for this platform's bootstrap  | fn`print_update_status`, fn`check_update_status`, fn`auto_update_targe |
| `lib.rs` | 7 | — | — |
| `version.rs` | 784 | Primary CLI base URL: Cloudflare-fronted x.ai endpoint  | fn`fetch_npm_tag_for_test`, fn`fetch_npm_version_for_test`, fn`fetch_g |
| `version_policy.rs` | 208 | Startup enforcement of the version policy. The hard `re | fn`check_install_target`, fn`enforce_version_policy_or_exit`, enum`Ver |

---

## 4. 工作区依赖

`xai-grok-shell` · `xai-grok-tools` · `xai-grok-version`

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-update`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-update
cargo test -p xai-grok-update
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

