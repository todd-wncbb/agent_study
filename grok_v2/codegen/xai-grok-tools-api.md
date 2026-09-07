# xai-grok-tools-api

> 路径：`crates/codegen/xai-grok-tools-api`
> 版本：0.1.220-alpha.4
> 类型：库 crate

---

## 1. 概述

Protobuf API definitions for Grok tools

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `pb` | — |
| `config_validation` | Validation of [`ToolConfigEntry`](crate::ToolConfigEntry) fields, |
| `slash_commands` | Canonical slash-command wording (`/loop`, `/imagine`, `/imagine-video`, `/goal`) |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `config_validation.rs` | 257 | Validation of [`ToolConfigEntry`](crate::ToolConfigEntr | fn`parse_params_json`, fn`validate_name_override`, fn`first_unknown_to |
| `lib.rs` | 143 | Shared API definitions for Grok tools: protobuf types,  | fn`default_client_name` |
| `slash_commands.rs` | 238 | Canonical slash-command wording (`/loop`, `/imagine`, ` | fn`loop_usage_message`, fn`loop_schedule_instruction`, fn`imagine_usag |

---

## 4. 工作区依赖

`xai-tool-protocol`

---

## 5. 被谁依赖

`xai-grok-tools` · `xai-grok-tools-api` · `xai-grok-workspace`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-tools-api
cargo test -p xai-grok-tools-api
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

