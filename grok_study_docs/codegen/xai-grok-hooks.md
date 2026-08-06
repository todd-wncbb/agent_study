# xai-grok-hooks

> 路径：`crates/codegen/xai-grok-hooks`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Runtime hook system for Grok — file-based discovery, command execution, and policy enforcement

pre/post tool hooks。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `config` | The parsed `hooks` object from a compatible JSON settings file. |
| `discovery` | The loaded set of hooks, indexed by event type for fast lookup. |
| `dispatcher` | Disabled/trust-disabled specs record a `Skipped` result; a matcher miss |
| `error` | Errors that can occur during hook loading, parsing, or execution. |
| `event` | Maximum serialized size for `toolInput` or `toolResult` in bytes (128 KB). |
| `matcher` | A compiled hook matcher for tool names. The pattern semantics are chosen so that |
| `result` | The outcome of a blocking (`pre_tool_use`) hook dispatch. |
| `runner` | How a hook's output is interpreted, per the event's [`GateKind`]: `Observe` |
| `trust` | Path to the legacy project-hook trust file |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `config.rs` | 1101 | The parsed `hooks` object from a compatible JSON settin | fn`parse_hooks_from_value`, fn`parse_hooks_from_value_with_dir`, fn`pa |
| `discovery.rs` | 972 | The loaded set of hooks, indexed by event type for fast | fn`load_hooks_from_sources`, fn`load_hooks`, struct`HookRegistry`, enu |
| `dispatcher.rs` | 1161 | Disabled/trust-disabled specs record a `Skipped` result | fn`dispatch_pre_tool_use`, fn`stop_detail`, fn`dispatch_stop`, fn`disp |
| `env_expand.rs` | 856 | Environment variable expansion helper for hook config s | fn`expand_env_vars_with_extra`, fn`iter_env_var_references`, struct`En |
| `error.rs` | 52 | Errors that can occur during hook loading, parsing, or  | enum`HookError` |
| `event.rs` | 792 | Maximum serialized size for `toolInput` or `toolResult` | fn`clip_text`, fn`clip_stop_entry_text`, fn`truncate_payload`, struct` |
| `lib.rs` | 50 | # xai-grok-hooks | — |
| `matcher.rs` | 216 | A compiled hook matcher for tool names. The pattern sem | fn`matcher_allows`, struct`HookMatcher` |
| `result.rs` | 72 | The outcome of a blocking (`pre_tool_use`) hook dispatc | struct`StopHookOutcome`, struct`StopOverride`, struct`HttpInfo`, enum` |
| `test_support.rs` | 122 | Test-only helpers shared across `xai-grok-hooks` unit + | fn`with_env_var` |
| `trust.rs` | 171 | Path to the legacy project-hook trust file | fn`legacy_trust_file_path`, fn`list_trusted_projects_with_file`, fn`is |

### `runner/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `command.rs` | 1378 | Maximum bytes to capture from hook stdout or stderr (64 | fn`run_command_hook`, fn`resolve_command_path` |
| `http.rs` | 855 | HTTP hook handler runner. | fn`run_http_hook` |
| `mod.rs` | 134 | How a hook's output is interpreted, per the event's [`G | fn`gate_json_to_decision`, fn`stop_json_to_outcome`, fn`run_hook`, str |

---

## 4. 工作区依赖

`xai-grok-config` · `xai-grok-tools`

---

## 5. 被谁依赖

`xai-grok-agent` · `xai-grok-hooks` · `xai-grok-shell` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-hooks
cargo test -p xai-grok-hooks
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

