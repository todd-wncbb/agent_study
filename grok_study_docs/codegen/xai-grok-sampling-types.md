# xai-grok-sampling-types

> 路径：`crates/codegen/xai-grok-sampling-types`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Pure data types for the xAI sampling / chat-completion API layer

ConversationItem、ToolSpec 类型。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `conversation` | API-agnostic conversation representation. |
| `doom_loop` | Server-side doom-loop check: wire contract types and tolerant parsers. |
| `error` | Sampling error types. |
| `messages` | Anthropic Messages API (`/v1/messages`) wire types. |
| `serde_helpers` | Deserialize `Option<Option<T>>`: absent (`None`) leaves, `null` (`Some(None)`) |
| `tool_overrides` | The `toolOverrides` wire contract for backend-hosted `x_search` / `web_search`. |
| `types` | Object-safe trait for opaque tracing context attached to requests. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `conversation.rs` | 9755 | API-agnostic conversation representation. | fn`apply_tool_overrides`, fn`reported_cost_ticks`, fn`reasoning_item_t |
| `doom_loop.rs` | 527 | Server-side doom-loop check: wire contract types and to | fn`peek_doom_loop`, fn`is_check_event`, struct`DoomLoopRecoveryPolicy` |
| `error.rs` | 868 | Sampling error types. | fn`status_user_message`, fn`parse_error_bytes`, fn`user_facing_api_err |
| `lib.rs` | 33 | Pure data types for the xAI sampling / chat-completion  | — |
| `messages.rs` | 421 | Anthropic Messages API (`/v1/messages`) wire types. | struct`MessagesRequest`, struct`OutputConfig`, struct`Message`, struct |
| `serde_helpers.rs` | 19 | Deserialize `Option<Option<T>>`: absent (`None`) leaves | fn`empty_string_as_none`, fn`double_option` |
| `tool_overrides.rs` | 252 | The `toolOverrides` wire contract for backend-hosted `x | fn`drop_empty`, struct`SearchDateBound`, struct`XSearchOptions`, struc |
| `types.rs` | 1523 | Object-safe trait for opaque tracing context attached t | fn`chat_truncate_for_prompt`, fn`parse_canonical_effort_token`, fn`sup |

---

## 4. 工作区依赖

`xai-grok-compaction` · `xai-grok-tools`

---

## 5. 被谁依赖

`xai-chat-state` · `xai-grok-agent` · `xai-grok-sampler` · `xai-grok-sampling-types` · `xai-grok-shell` · `xai-grok-subagent-resolution`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-sampling-types
cargo test -p xai-grok-sampling-types
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

