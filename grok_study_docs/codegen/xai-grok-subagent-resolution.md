# xai-grok-subagent-resolution

> 路径：`crates/codegen/xai-grok-subagent-resolution`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Shared subagent definition, runtime, prompt, and resume resolution

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `config` | Subagent role and persona configuration types. |
| `context` | Fork-context normalization: summarizes parent conversation for child sessions. |
| `definition` | Production subagent definition discovery and tool-policy resolution. |
| `overrides` | Runtime override resolution: merges explicit, role, and persona defaults. |
| `resume` | Resume identity validation: ensures that a resumed subagent matches the |
| `types` | Public API types for subagent resolution. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `config.rs` | 357 | Subagent role and persona configuration types. | struct`SubagentRole`, struct`SubagentPersona`, struct`PersonaIOField` |
| `context.rs` | 1073 | Fork-context normalization: summarizes parent conversat | fn`normalize_forked_context` |
| `definition.rs` | 408 | Production subagent definition discovery and tool-polic | fn`subagent_harness_flavor_is_representable`, fn`apply_harness_toolset |
| `lib.rs` | 40 | Subagent configuration resolution crate. | — |
| `overrides.rs` | 793 | Runtime override resolution: merges explicit, role, and | fn`intersect_capability_modes`, fn`resolve_effective_overrides` |
| `resume.rs` | 193 | Resume identity validation: ensures that a resumed suba | fn`validate_resume_identity`, enum`ResumeValidationError` |
| `types.rs` | 159 | Public API types for subagent resolution. | struct`EffectiveRuntimeConfig`, struct`ResumeSourceData`, enum`Context |

---

## 4. 工作区依赖

`xai-grok-agent` · `xai-grok-sampling-types` · `xai-grok-tools`

---

## 5. 被谁依赖

`xai-grok-shell` · `xai-grok-subagent-resolution`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-subagent-resolution
cargo test -p xai-grok-subagent-resolution
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

