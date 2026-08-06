# xai-grok-secrets

> 路径：`crates/codegen/xai-grok-secrets`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Regex sanitizer for Grok Build outbound data (Sentry / Mixpanel / product-event scrubbing)

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 5 | — | — |
| `sanitizer.rs` | 563 | Vendor API keys with `sk-`/`sk_` prefixes and xAI (`xai | fn`redact_secrets`, fn`walk_json_strings`, fn`redact_json_string_value |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-grok-secrets` · `xai-grok-telemetry` · `xai-mixpanel`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-secrets
cargo test -p xai-grok-secrets
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

