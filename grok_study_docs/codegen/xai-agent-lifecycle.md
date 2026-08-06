# xai-agent-lifecycle

> 路径：`crates/codegen/xai-agent-lifecycle`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

`xai-agent-lifecycle` 是 Grok Build codegen 工作区成员。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `local` | — |
| `send` | — |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 17 | Host-agnostic agent lifecycle hooks shared by multiple  | — |
| `local.rs` | 8 | — | — |
| `send.rs` | 10 | — | — |

### `local/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `contributors.rs` | 9 | — | — |
| `registry.rs` | 101 | Mutable registry used while hosts register typed runtim | struct`LocalExtensionRegistryBuilder`, struct`LocalExtensionRegistry` |

### `local/contributors/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `command.rs` | 27 | `?Send` twin of [`CommandContributor`] for single-threa | trait`LocalCommandContributor` |
| `session_lifecycle.rs` | 19 | `?Send` twin of [`SessionLifecycleContributor`]. | trait`LocalSessionLifecycleContributor` |
| `turn_input.rs` | 22 | `?Send` twin of [`TurnInputContributor`] for single-thr | trait`LocalTurnInputContributor` |
| `turn_lifecycle.rs` | 40 | `?Send` twin of [`TurnLifecycleContributor`] for single | trait`LocalTurnLifecycleContributor` |

### `send/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `contributors.rs` | 12 | — | — |
| `registry.rs` | 226 | Mutable registry used while hosts register typed runtim | struct`ExtensionRegistryBuilder`, struct`ExtensionRegistry` |

### `send/contributors/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `command.rs` | 32 | A slash command a contributor advertises; the host maps | struct`CommandSpec`, struct`CommandInvocation`, enum`CommandAction`, t |
| `session_lifecycle.rs` | 10 | Input supplied when the host observes the session settl | struct`SessionIdleInput`, trait`SessionLifecycleContributor` |
| `turn_input.rs` | 23 | Turn facts supplied when the host pulls extension input | struct`TurnInputContext`, struct`TurnInputFragment`, trait`TurnInputCo |
| `turn_lifecycle.rs` | 52 | Input supplied when the host starts a turn. | struct`TurnStartInput`, struct`TurnDoneInput`, struct`TurnAbortInput`, |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-agent-lifecycle` · `xai-grok-shell`

---

## 6. 开发命令

```sh
cargo check -p xai-agent-lifecycle
cargo test -p xai-agent-lifecycle
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

