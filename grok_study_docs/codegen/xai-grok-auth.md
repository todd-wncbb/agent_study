# xai-grok-auth

> 路径：`crates/codegen/xai-grok-auth`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Auth dependency-inversion seam: HttpAuth + AuthCredentialProvider traits

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `auth_provider` | Credential dependency-inversion seam for outbound HTTP made by the |
| `retry_middleware` | `reqwest-middleware` layer: stamps auth headers and retries on 401. |
| `visibility` | Apply auth headers to outbound visibility requests. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `auth_provider.rs` | 118 | Credential dependency-inversion seam for outbound HTTP  | struct`CredentialSnapshot`, struct`StaticAuthCredentialProvider`, trai |
| `lib.rs` | 14 | Auth dependency-inversion seam shared between `xai-file | — |
| `retry_middleware.rs` | 272 | `reqwest-middleware` layer: stamps auth headers and ret | struct`AuthRetryMiddleware` |
| `visibility.rs` | 7 | Apply auth headers to outbound visibility requests. | trait`HttpAuth` |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-file-utils` · `xai-grok-auth` · `xai-grok-http` · `xai-grok-memory` · `xai-grok-shell` · `xai-grok-telemetry` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-auth
cargo test -p xai-grok-auth
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

