# xai-workflow

> 路径：`crates/codegen/xai-workflow`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Rhai-scripted dynamic workflow engine: scripts orchestrate agents through a host channel

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `engine` | — |
| `host` | — |
| `journal` | — |
| `meta` | — |
| `run` | — |
| `validate` | — |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `engine.rs` | 1779 | — | fn`run_workflow`, struct`WorkflowRunParams` |
| `host.rs` | 128 | — | struct`AgentOpts`, struct`AgentResult`, struct`BudgetState`, enum`Host |
| `journal.rs` | 498 | — | fn`request_hash`, struct`JournalEntry`, struct`Journal`, enum`JournalE |
| `lib.rs` | 44 | — | fn`with_rhai_hint` |
| `meta.rs` | 331 | — | fn`extract_meta`, struct`WorkflowMeta`, struct`PhaseMeta`, enum`MetaEr |
| `run.rs` | 48 | — | enum`PauseKind`, enum`WorkflowOutcome` |
| `validate.rs` | 297 | — | fn`default_probe_args`, fn`validate_script`, fn`validate_script_with_a |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-grok-shell` · `xai-workflow`

---

## 6. 开发命令

```sh
cargo check -p xai-workflow
cargo test -p xai-workflow
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

