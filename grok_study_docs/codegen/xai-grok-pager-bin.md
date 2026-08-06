# xai-grok-pager-bin

> 路径：`crates/codegen/xai-grok-pager-bin`
> 版本：0.2.111
> 类型：二进制 crate

---

## 1. 概述

`xai-grok-pager-bin` 是 Grok Build codegen 工作区成员。

组合根 main()、CLI 分流。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `main.rs` | 3166 | jemalloc looks up `extern const char *malloc_conf` — a  | — |

---

## 4. 工作区依赖

`xai-grok-pager` · `xai-grok-pager-minimal` · `xai-grok-shell` · `xai-grok-update` · `xai-grok-version` · `xai-grok-telemetry` · `xai-grok-workspace` · `xai-crash-handler` · `xai-acp-lib` · `xai-tty-utils` · `xai-grok-config` · `xai-grok-sandbox`

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-pager-bin`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-pager-bin
cargo test -p xai-grok-pager-bin
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

