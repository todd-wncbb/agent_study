# xai-chat-state 代码导读

> `crates/codegen/xai-chat-state` · 对话状态 Actor、`ConversationRequest` 组装、compaction
> 配套：[09 端到端](../09_end_to_end_request_flow.md) · [xai-grok-sampler](./xai-grok-sampler.md)

---

## 1. 职责与系统位置

从 `xai-grok-shell` 的 `acp_session.rs` 抽出的 **对话状态 Actor**，管理：

- `Vec<ConversationItem>` 历史
- `SamplingConfig`、token 计数、prompt_index
- 组装发往模型的 `ConversationRequest`（含 prune / 图片压缩 / memory 注入）
- 持久化（`ChatPersistence` trait）

```text
SessionActor (shell)
  push_user / push_tool_result / push_assistant
       ↓ ChatStateHandle (mpsc)
  ChatStateActor (专用 tokio task，无锁 State)
       ↓ ChatStateEvent
  SessionActor 订阅事件

  每轮推理前：
  BuildConversationRequest → ConversationRequest
       ↓
  SamplerHandle::submit
```

---

## 2. Actor 模式

| 组件 | 文件 | 作用 |
| --- | --- | --- |
| `ChatStateActor` | `actor/mod.rs` | 单线程消费 `ChatStateCommand` |
| `ChatStateHandle` | `handle.rs` | 廉价 `Clone`，跨 task 发命令 |
| `ChatStateCommand` | `commands.rs` | Mutation + Query（oneshot 回复）|
| `ChatStateEvent` | `events.rs` | 状态变更通知（compaction 等）|
| `actor/state.rs` | — | `State` 结构体（conversation、tokens…）|
| `actor/mutations.rs` | — | push/replace/repair 实现 |
| `actor/queries.rs` | — | Get* 查询 |
| `actor/request_builder.rs` | — | **`build_conversation_request`** |

与 `xai-hunk-tracker`、`xai-grok-sampler` 同一套 **Command + Handle + Actor** 模式。

---

## 3. ChatStateHandle 主要 API

### 3.1 Fire-and-forget 变更

| 方法 | 命令 | 时机 |
| --- | --- | --- |
| `push_user_message` | PushUserMessage | 用户 prompt / bash mode 用户块 |
| `push_assistant_response` | PushAssistantResponse | 模型回复（文本+tool_calls） |
| `push_tool_result` | PushToolResult | 工具执行结果写入历史 |
| `record_token_usage` | RecordTokenUsage | 流式累计 token |
| `record_last_turn_usage` | RecordLastTurnUsage | 单轮 TokenUsage 快照 |
| `increment_prompt_index` | IncrementPromptIndex | 完成一轮用户 prompt |
| `update_sampling_config` | UpdateSamplingConfig | 换模型 / context_window |
| `flush` | Flush | 强制刷持久化 |

### 3.2 需要 await 的查询 / 构建

| 方法 | 命令 | 返回 |
| --- | --- | --- |
| `build_conversation_request` | BuildConversationRequest | `ConversationRequest` |
| `get_conversation` | GetConversation | `Vec<ConversationItem>` |
| `get_total_tokens` | GetTotalTokens | `u64` |
| `get_prompt_index` | GetPromptIndex | `usize` |
| `check_auto_compact_needed` | CheckAutoCompactNeeded | 是否触发自动 compaction |
| `repair_history` | RepairHistory | 修复 dangling tool calls |
| `snapshot` | Snapshot | `ChatStateSnapshot`（rewind 用）| |

完整命令列表见 §8。

---

## 4. build_conversation_request 流水线

`actor/request_builder.rs` — 每轮采样前由 `BuildConversationRequest` 命令触发：

```text
ensure_conversation_integrity()   // 命令 handler 先修复 dangling
  ↓
1. 若 body ≥ 50MB → compact_images_to_byte_budget（驱逐最老 inline 图）
2. 若 token > 50% context_window → prune_conversation（软/硬 trim 旧 tool_result）
3. 若需 memory_reminder → inject_memory_reminder（可 persist 到 actor state）
4. 组装 ConversationRequest { items, tool_definitions, trace, ... }
```

关键常量（同文件）：

- `HARD_CLEAR_PLACEHOLDER` = `[Tool result omitted — too old]`
- `IMAGE_COMPACT_TRIGGER_BYTES` — 接近 50MB 才触发图片驱逐（避免每轮 bust KV cache）
- `should_prune(total_tokens, context_window)` — 50% 利用率阈值

**Repair 不变量**：integrity 在 clone 之前已修复，request_builder 不对 clone 再跑 O(n) repair。

---

## 5. Compaction 与历史修剪

| 模块 | 作用 |
| --- | --- |
| `compaction_mode.rs` | `CompactionMode` 枚举 |
| `compaction_transcript.rs` | compaction 产物分类、`CompactionDetail` |
| `compaction_utils.rs` | 共享工具函数 |
| `conversation_util.rs` | item 级 helper |

自动 compaction：`CheckAutoCompactNeeded` + shell 侧触发 summary 请求，
`RecordCompactionAt(prompt_index)` 记录截断点。`TruncateToPromptIndex` 用于 rewind。

---

## 6. 持久化

`persistence.rs` — `ChatPersistence` trait：

- `NullChatPersistence` — 无操作
- `MockChatPersistence` — 测试
- 生产实现由 shell 注入（写 session JSONL / sqlite 等）

`ReplaceConversation`、`Flush`、memory 注入等路径会调用 `persistence.replace_history`。

---

## 7. Token 估算

`actor/state.rs` 导出：

- `estimate_conversation_tokens`
- `estimate_item_tokens` / `estimate_messages_tokens`
- `estimate_tool_definition_tokens`

用于 UI context bar、auto-compact 决策；与 provider 计费 token 可能略有偏差。

---

## 8. ChatStateCommand 全量

### Mutations

`PushUserMessage` · `PushUserMessageAndAck` · `AppendWorkingDirectorySwitchAndAck` · `PushUserMessageWithRepairReason`
`PushAssistantResponse` · `PushToolResult` · `RecordTokenUsage` · `RecordLastTurnUsage`
`RecordModelCallUsage` · `RecordSubagentUsage` · `MarkUsageIncomplete` · `IncrementPromptIndex`
`UpdateSamplingConfig` · `RecordAgentEditedPath` · `RecordStreamStart` · `RecordTurnStart`
`ReplaceConversation` · `RepairHistory` · `ReplaceSystemHead` · `CachePromptText`
`RecordCompactionAt` · `Flush` · `UpdateCredentials` · `RestoreSnapshot`
`BeginTurnCapture` · `AppendHarnessTraceItems` · `FlushHarnessTraceTurn` · `RepairDanglingAfterHarnessHalt`

### Queries / 构建

`BuildConversationRequest` · `GetConversation` · `GetPromptIndex`
`GetLastCompactionPromptIndex` · `GetTotalTokens` · `GetLastTurnUsage`
`GetPromptUsage` · `GetSessionUsage` · `GetEstimatedTotalTokens`
`GetEstimatedMessagesTokens` · `GetSamplingConfig` · `GetAgentEditedPaths`
`GetNotificationMeta` · `Snapshot` · `TruncateToPromptIndex`
`CheckAutoCompactNeeded` · `GetCredentials` · `GetLastModelMetadata`
`TakeTurnMessages` · `TakeHarnessTraceTurns` · `GetConversationLen`
`HasDanglingToolCalls` · `GetLastAssistantText` · `GetLastAssistantTextInTurn`
`GetFirstUserText` · `GetConversationItemAt` · `GetLastUserQueryText`
`GetConversationCounts` · `GetSystemMessage`

---

## 9. Shell 集成点

| Shell 位置 | 用法 |
| --- | --- |
| `SessionActor::chat_state_handle` | 持有 `ChatStateHandle` |
| turn 开始 | `build_conversation_request` → sampler |
| tool 完成后 | `push_tool_result` |
| 流式结束 | `push_assistant_response` + `record_last_turn_usage` |
| `/rewind` | `TruncateToPromptIndex` / `Snapshot` |
| compaction | `CheckAutoCompactNeeded` + 替换 conversation |

---

## 10. 类型与 Usage

- `types.rs` — `ChatStateSnapshot`、`TurnCapture`、`Credentials`、`PruningConfig`
- `usage.rs` — `UsageLedger`、`UsageTotals`（session 级用量汇总）
- 对话 item 类型来自 **`xai-grok-sampling-types`**（`ConversationItem`）

---

## 11. ChatState 内部字段 (`actor/state.rs`)

| 字段 | 含义 |
| --- | --- |
| `conversation` | 完整 `Vec<ConversationItem>` |
| `sampling_config` | 模型、context_window 等 |
| `prompt_index` | 用户轮次计数 |
| `prompt_texts` | rewind 预览用缓存 |
| `total_tokens` | 累计 token |
| `agent_edited_paths` | agent 编辑过的路径 |
| `last_compaction_prompt_index` | 上次 compaction 截断点 |
| `last_turn_usage` | 最近一轮 TokenUsage |
| `turn_capture` | offset 式 turn 捕获 |
| `harness_trace_buffer / harness_trace_turns` | harness 子 agent trace |

---

## 12. ChatStateEvent

| 变体 | 用途 |
| --- | --- |
| `PromptIndexChanged` | hunk tracker 归属 |
| `TokensUpdated` | 通知 meta、auto-compact |
| `ConversationReset` | compaction/rewind 后重置 |
| `ImageBudget` | inline 图驱逐观测 |

---

## 13. 单轮 Turn 生命周期

```text
push_user_message → build_request → sampler
→ push_assistant_response → push_tool_result (循环)
→ record_last_turn_usage → increment_prompt_index
```

---

## 14. ChatPersistence

`persist_message` · `replace_history` · `flush` · `persist_working_directory_switch_and_ack`

---

## 15. ChatStateHandle 完整 API

| # | 方法 | async |
| ---: | --- | :---: |
| 1 | `noop` |  |
| 2 | `push_user_message` |  |
| 3 | `push_user_message_and_ack` | ✓ |
| 4 | `append_working_directory_switch_and_ack` | ✓ |
| 5 | `push_user_message_with_repair_reason` |  |
| 6 | `push_assistant_response` |  |
| 7 | `push_tool_result` |  |
| 8 | `record_token_usage` |  |
| 9 | `record_last_turn_usage` |  |
| 10 | `record_model_call_usage` |  |
| 11 | `record_subagent_usage` | ✓ |
| 12 | `mark_usage_incomplete` | ✓ |
| 13 | `increment_prompt_index` |  |
| 14 | `update_sampling_config` |  |
| 15 | `record_agent_edited_path` |  |
| 16 | `record_stream_start` |  |
| 17 | `record_turn_start` |  |
| 18 | `replace_conversation` |  |
| 19 | `replace_conversation_for_compaction` |  |
| 20 | `repair_history` | ✓ |
| 21 | `replace_system_head` | ✓ |
| 22 | `cache_prompt_text` |  |
| 23 | `record_compaction_at` |  |
| 24 | `flush` |  |
| 25 | `update_credentials` |  |
| 26 | `restore_snapshot` |  |
| 27 | `begin_turn_capture` |  |
| 28 | `append_harness_trace_items` |  |
| 29 | `flush_harness_trace_turn` |  |
| 30 | `repair_dangling_after_harness_halt` |  |
| 31 | `build_request` | ✓ |
| 32 | `get_conversation` | ✓ |
| 33 | `get_prompt_index` | ✓ |
| 34 | `get_last_compaction_prompt_index` | ✓ |
| 35 | `get_total_tokens` | ✓ |
| 36 | `get_last_turn_usage` | ✓ |
| 37 | `try_get_prompt_usage` | ✓ |
| 38 | `try_get_session_usage` | ✓ |
| 39 | `get_estimated_total_tokens` | ✓ |
| 40 | `get_estimated_messages_tokens` | ✓ |
| 41 | `get_sampling_config` | ✓ |
| 42 | `get_agent_edited_paths` | ✓ |
| 43 | `get_notification_meta` | ✓ |
| 44 | `snapshot` | ✓ |
| 45 | `truncate_to_prompt_index` | ✓ |
| 46 | `get_credentials` | ✓ |
| 47 | `get_last_model_metadata` | ✓ |
| 48 | `take_turn_messages` | ✓ |
| 49 | `take_harness_trace_turns` | ✓ |
| 50 | `check_auto_compact_needed` | ✓ |
| 51 | `get_conversation_len` | ✓ |
| 52 | `has_dangling_tool_calls` | ✓ |
| 53 | `get_last_assistant_text` | ✓ |
| 54 | `get_last_assistant_text_in_turn` | ✓ |
| 55 | `get_first_user_text` | ✓ |
| 56 | `get_conversation_item_at` | ✓ |
| 57 | `get_last_user_query_text` | ✓ |
| 58 | `get_conversation_counts` | ✓ |
| 59 | `get_system_message` | ✓ |

---

## 16. request_builder 详细步骤

`build_conversation_request` 内部顺序（`actor/request_builder.rs`）：

1. 读取 `total_tokens` 与 `context_window`，判断 `needs_prune`
2. 若 `persist_memory_reminder`：snapshot_turn_slice → inject → persistence → rebase
3. 测量 `conversation_body_bytes`（wire-accurate，跳过 base64 全扫描）
4. 若 body ≥ IMAGE_COMPACT_TRIGGER：compact_images_to_byte_budget
5. 若 needs_prune：prune_conversation（软 trim + HARD_CLEAR）
6. 若仍有 memory_reminder：inject 到 system 或 user 侧
7. 附加 tool_definitions、trace、conv_id、req_id
8. 若有 image eviction：发送 ChatStateEvent::ImageBudget
9. 返回 ConversationRequest（可能基于 clone 后的 items）

**PruningConfig**（`types.rs`）：控制保留最近 N 个 tool result、软 trim 比例等。

---

## 17. mutations 与 integrity

`actor/mutations.rs` 处理 push/replace 类命令；关键路径：

- `push_user_message` — 追加 + persist + 可能 repair
- `push_tool_result` / `push_assistant_response` — 同上
- `replace_conversation` — compaction/rewind；发 `ConversationReset`
- `ensure_conversation_integrity` — `repair_dangling_tool_calls` + dedup
- `snapshot_turn_slice` / `rebase_turn_capture_offset` — turn 捕获与替换协调

`actor/queries.rs` — 所有 `Get*` 命令的只读实现。

---

## 19. ConversationItem 类型（来自 xai-grok-sampling-types）

| 变体 | 在 history 中的角色 |
| --- | --- |
| `System` | system prompt / 规则 / memory reminder |
| `User` | 用户消息（text + image parts） |
| `Assistant` | 模型回复 text + tool_calls |
| `ToolResult` | 工具执行结果（喂回模型） |
| `BackendToolCall` | 服务端托管工具摘要 |
| `Reasoning` | thinking / encrypted reasoning blob |

---

## 20. ChatStateCommand 逐条说明

| 命令 | 说明 |
| --- | --- |
| `PushUserMessage` | Push a user message into the conversation. |
| `PushUserMessageAndAck` | Push a user message and acknowledge once the chat-state actor has |
| `AppendWorkingDirectorySwitchAndAck` | Append one working-directory switch without repair or pruning, then |
| `PushUserMessageWithRepairReason` | Push a user message with an explicit dangling-repair reason. |
| `PushAssistantResponse` | Record the assistant's response (text + tool calls). |
| `PushToolResult` | Record a tool result. |
| `RecordTokenUsage` | Record accumulated token usage from a streaming response. |
| `RecordLastTurnUsage` | Stash the per-turn `TokenUsage` from the most recent model response. |
| `RecordModelCallUsage` | — |
| `RecordSubagentUsage` | Subagent usage into session (and prompt when attributable). Replies wh |
| `MarkUsageIncomplete` | Nested subagent bill may under-count. |
| `IncrementPromptIndex` | Increment prompt_index (called at start of each user turn). |
| `UpdateSamplingConfig` | Update the sampling config (e.g., model switch). |
| `RecordAgentEditedPath` | Track that the agent edited a file path. |
| `RecordStreamStart` | Record stream timing metadata. |
| `RecordTurnStart` | Record turn timing metadata. |
| `ReplaceConversation` | Replace conversation history. |
| `RepairHistory` | Out-of-band history repair (`x.ai/session/repair`): run |
| `Result` | — |
| `ReplaceSystemHead` | Atomically align the leading `System` message with `prompt` (inserting |
| `CachePromptText` | Cache prompt text for rewind preview. |
| `RecordCompactionAt` | Record compaction boundary for rewind. |
| `Flush` | Flush pending persistence writes to disk (end of turn). |
| `UpdateCredentials` | Update opaque credential secrets held by the actor. |
| `RestoreSnapshot` | Restore from a snapshot. |
| `BeginTurnCapture` | Start capturing turn messages. Clears any previous buffer. |
| `AppendHarnessTraceItems` | Append synthetic `task` pairs for a harness-spawned subagent (goal |
| `FlushHarnessTraceTurn` | Seal the harness items accumulated since the last flush into one |
| `RepairDanglingAfterHarnessHalt` | Repair dangling tool calls after a harness-initiated halt. |
| `BuildConversationRequest` | Build a ConversationRequest ready to send to the API. |
| `GetConversation` | Get a clone of the full conversation. |
| `GetPromptIndex` | Get current prompt index. |
| `GetLastCompactionPromptIndex` | Get the prompt index at which the last compaction occurred. |
| `GetTotalTokens` | Get total accumulated tokens. |
| `GetLastTurnUsage` | Retrieve the most recent stashed per-turn `TokenUsage`. Returns |
| `GetPromptUsage` | — |
| `GetSessionUsage` | — |
| `GetEstimatedTotalTokens` | `total_tokens` + bytes/4 delta from tool results since last model resp |
| `GetEstimatedMessagesTokens` | Bytes/4 estimate of all non-system conversation items. |
| `GetSamplingConfig` | Get sampling config. |
| `GetAgentEditedPaths` | Get the set of agent-edited file paths. |
| `GetNotificationMeta` | Get notification meta (timing info). |
| `Snapshot` | Snapshot state for forking or rewind. |
| `TruncateToPromptIndex` | Truncate conversation to a target prompt index (for rewind). |
| `CheckAutoCompactNeeded` | Check if auto-compact is needed (returns token info). |
| `GetCredentials` | Get credential secrets. |
| `GetLastModelMetadata` | — |
| `TakeTurnMessages` | Take the accumulated turn messages and end the capture. |
| `TakeHarnessTraceTurns` | Drain the sealed harness trace turns (goal planner + verifier panels). |
| `GetConversationLen` | Get the number of items in the conversation. |
| `HasDanglingToolCalls` | Whether any assistant tool call lacks a matching `ToolResult` (i.e. th |
| `GetLastAssistantText` | Get the text content of the last assistant message with non-empty text |
| `GetLastAssistantTextInTurn` | Like `GetLastAssistantText`, but bounded to the current prompt turn: |
| `GetFirstUserText` | Get the text of the first `Text` content part in the first `User` mess |
| `GetConversationItemAt` | Get a single conversation item by index (0-based). |
| `GetLastUserQueryText` | Get the processed text of the last user query (metadata tags stripped) |
| `GetConversationCounts` | Get item counts for the conversation by role. |
| `GetSystemMessage` | Get the first `System` message in the conversation, if any. |

---

## 21. compaction 模块协作

```text
shell: check_auto_compact_needed → 超阈值
  → 发起 summary 采样（sampler submit_and_collect）
  → replace_conversation_for_compaction
  → record_compaction_at(prompt_index)

compaction_transcript::classify_compaction_path
  → 工具读路径是否算 compaction artifact（shell tool_dispatch 用）
```

```

---

## 22. actor/ 函数索引

### mutations / queries / request_builder

**`actor/mod.rs`：** `send_event`, `spawn`, `spawn_with_pruning`, `run`, `handle_command`

**`actor/mutations.rs`：** `item_kind_str`, `conversation_content_bytes`

**`actor/request_builder.rs`：** `should_prune`, `prune_conversation`, `write`, `flush`, `serialized_json_bytes`, `image_part_bytes`, `inline_image_count`, `conversation_body_bytes`, `compact_images_to_byte_budget`, `upsert_memory_reminder_text`, `safe_char_slice`, `safe_char_slice_tail`, `should_prune_gating`, `prune_disabled_is_noop`, `inject_memory_into_existing_system`, `inject_memory_prepends_when_no_system`, `user_with_image`, `user_with_image_of_bytes`, `has_image`, `has_placeholder`, `no_eviction_when_at_or_below_target`, `evicts_oldest_until_under_target`, `evicts_more_oldest_for_lower_target`, `eviction_reclaims_batch_to_low_water_mark`, `evicts_all_when_target_below_one_image`, `eviction_keeps_newest_and_is_idempotent`, `evicted_image_uses_honest_placeholder`, `conversation_body_bytes_empty_is_json_array`, `conversation_body_bytes_matches_serde_json_exactly`, `conversation_body_bytes_matches_serde_json_with_large_image`, `conversation_body_bytes_small_image_is_below_trigger`, `conversation_body_bytes_large_image_reaches_trigger`, `body_bytes_parity_multi_image_unicode_escaping`, `no_eviction_when_exactly_at_target`, `terminates_when_placeholder_exceeds_image`, `evicts_oldest_image_parts_first`, `image_parts`, `escaped_remote_url_is_a_lower_bound_only`

**`actor/state.rs`：** `estimate_system_message_tokens`, `estimate_tool_definition_tokens`, `estimate_tool_definitions_tokens`, `estimate_item_tokens`, `estimate_conversation_tokens`, `count_item_tokens`, `estimate_messages_tokens`, `new`, `test_sampling_config`, `estimated_item_token_counter_matches_estimate_item_tokens`, `new_state_has_correct_defaults`, `new_state_preserves_initial_conversation`, `new_state_estimates_tokens_from_conversation`, `estimate_system_message_tokens_only_counts_system_items`, `estimate_tool_definition_tokens_counts_name_desc_params`, `estimate_messages_tokens_excludes_system_and_sums_rest`, `estimate_messages_tokens_zero_when_only_system`, `estimate_messages_tokens_zero_for_empty`, `estimate_tool_definitions_tokens_sums_across_slice`

**`actor/tests.rs`：** `test_config`, `test_config_with_window`, `new`, `with_conversation`, `with_context_window`, `with_config`, `with_manual_persistence_ack`, `with_persistence`, `next_event`, `drain_events`, `drain_persistence`, `actor_spawns_and_shuts_down_via_cancellation`, `actor_shuts_down_when_all_handles_dropped`, `push_user_message_appends_and_persists`, `push_user_message_and_ack_waits_for_actor_acceptance`, `strict_switch_append_preserves_prefix_and_deduplicates_generation`, `strict_switch_append_ack_waits_for_persistence`, `committed_storage_result_converges_actor_memory`, `already_present_replaces_stale_switch_in_actor_memory`, `committed_already_present_replaces_retry_candidate_in_actor_memory`, `dropped_storage_reply_is_indeterminate_and_leaves_memory_unchanged`, `uncommitted_storage_error_leaves_actor_memory_unchanged`, `push_assistant_response_appends_and_persists`, `push_tool_result_appends_and_persists`, `record_token_usage_emits_event`, `record_last_turn_usage_round_trip`, `prompt_usage_ledger_via_handle_resets_and_clears`, `estimated_tokens_tracks_tool_result_delta`, `estimated_tokens_resets_on_model_response`, `estimated_tokens_tracks_synthetic_user_message_delta`, `estimated_tokens_tracks_real_user_message_and_resets_on_response`, `assistant_response_push_does_not_bump_estimated_delta`, `estimated_tokens_resets_on_truncate`, `increment_prompt_index_emits_event`, `replace_conversation_persists_and_emits_reset`, `compaction_reseed_carries_provider_overhead`, `compaction_reseed_scales_overhead_down_with_deleted_content`, `compaction_reseed_excludes_post_response_deltas_from_overhead`, `compaction_overhead_unaffected_by_pruning_after_last_response`, `restore_snapshot_preserves_frozen_estimate_for_overhead`, `restore_snapshot_without_frozen_estimate_falls_back_to_recompute`, `compaction_reseed_without_provider_count_matches_plain_estimate`, `non_compaction_replace_does_not_carry_overhead`, `flush_calls_persistence_flush`, `restore_snapshot_restores_all_fields`, `get_conversation_returns_current_state`, `get_total_tokens_returns_zero_initially`, `replace_system_head_swaps_head_and_preserves_turns`, `replace_system_head_noop_when_head_matches_modulo_newline`, `replace_system_head_inserts_when_absent`


---

## 23. Shell 源码对照（chat-state）

| Shell 文件 | 调用 |
| --- | --- |
| `session/handle.rs` | SessionHandle 持有 chat_state_handle |
| `session/compaction.rs` | get_conversation, replace_conversation_for_compaction |
| `session/acp_session_impl/tool_calls.rs` | push_tool_result, build_request |
| `session/acp_session_impl/tool_dispatch.rs` | compaction_artifact_read → classify_compaction_path |
| `session/goal_*.rs` | harness trace: append_harness_trace_items |
| `session/rewind.rs` | snapshot, truncate_to_prompt_index |

## 24. 逐文件导读

### `actor/mod.rs` (430 行)

> ChatStateActor — runs in a dedicated tokio task and owns all chat state.
> This module is organized into submodules by responsibility:
> - `state`: Internal state types (ChatState)
> - `mutations`: State mutation handlers (push_user_message, replace_conversation, etc.)
> - `queries`: Read-only query handlers (get_conversation, snapshot, etc.)

**公开 API：** struct`ChatStateActor`

**函数（节选）：** `send_event`, `spawn`, `spawn_with_pruning`, `run`, `handle_command`

---

### `actor/mutations.rs` (563 行)

> Mutation handlers for the ChatStateActor.

**函数（节选）：** `item_kind_str`, `conversation_content_bytes`

---

### `actor/queries.rs` (258 行)

> Query handlers for the ChatStateActor.

---

### `actor/request_builder.rs` (866 行)

> ConversationRequest assembly — image compaction, pruning, repair, memory injection.

**公开 API：** fn`should_prune`, fn`prune_conversation`, fn`compact_images_to_byte_budget`, struct`ImageEvictionOutcome`

**函数（节选）：** `should_prune`, `prune_conversation`, `write`, `flush`, `serialized_json_bytes`, `image_part_bytes`, `inline_image_count`, `conversation_body_bytes`, `compact_images_to_byte_budget`, `upsert_memory_reminder_text`, `safe_char_slice`, `safe_char_slice_tail`, `should_prune_gating`, `prune_disabled_is_noop`, `inject_memory_into_existing_system`, `inject_memory_prepends_when_no_system`, `user_with_image`, `user_with_image_of_bytes`, `has_image`, `has_placeholder`, `no_eviction_when_at_or_below_target`, `evicts_oldest_until_under_target`, `evicts_more_oldest_for_lower_target`, `eviction_reclaims_batch_to_low_water_mark`, `evicts_all_when_target_below_one_image`

---

### `actor/state.rs` (404 行)

> Internal state types for the ChatStateActor.

**公开 API：** fn`estimate_system_message_tokens`, fn`estimate_tool_definition_tokens`, fn`estimate_tool_definitions_tokens`, fn`estimate_item_tokens`, fn`estimate_conversation_tokens`, fn`estimate_messages_tokens`, struct`EstimatedItemTokenCounter`, struct`ChatState`

**函数（节选）：** `estimate_system_message_tokens`, `estimate_tool_definition_tokens`, `estimate_tool_definitions_tokens`, `estimate_item_tokens`, `estimate_conversation_tokens`, `count_item_tokens`, `estimate_messages_tokens`, `new`, `test_sampling_config`, `estimated_item_token_counter_matches_estimate_item_tokens`, `new_state_has_correct_defaults`, `new_state_preserves_initial_conversation`, `new_state_estimates_tokens_from_conversation`, `estimate_system_message_tokens_only_counts_system_items`, `estimate_tool_definition_tokens_counts_name_desc_params`, `estimate_messages_tokens_excludes_system_and_sums_rest`, `estimate_messages_tokens_zero_when_only_system`, `estimate_messages_tokens_zero_for_empty`, `estimate_tool_definitions_tokens_sums_across_slice`

---

### `actor/tests.rs` (4723 行)

> Tests for ChatStateActor.

**函数（节选）：** `test_config`, `test_config_with_window`, `new`, `with_conversation`, `with_context_window`, `with_config`, `with_manual_persistence_ack`, `with_persistence`, `next_event`, `drain_events`, `drain_persistence`, `actor_spawns_and_shuts_down_via_cancellation`, `actor_shuts_down_when_all_handles_dropped`, `push_user_message_appends_and_persists`, `push_user_message_and_ack_waits_for_actor_acceptance`, `strict_switch_append_preserves_prefix_and_deduplicates_generation`, `strict_switch_append_ack_waits_for_persistence`, `committed_storage_result_converges_actor_memory`, `already_present_replaces_stale_switch_in_actor_memory`, `committed_already_present_replaces_retry_candidate_in_actor_memory`, `dropped_storage_reply_is_indeterminate_and_leaves_memory_unchanged`, `uncommitted_storage_error_leaves_actor_memory_unchanged`, `push_assistant_response_appends_and_persists`, `push_tool_result_appends_and_persists`, `record_token_usage_emits_event`

---

### `commands.rs` (528 行)

> Commands sent to the ChatStateActor.

**公开 API：** struct`ModelMetadata`, struct`RepairHistoryBlocked`, enum`StrictAppendAck`, enum`StrictAppendError`, enum`ChatStateCommand`

**函数（节选）：** `fmt`, `command_variants_are_constructible`

---

### `compaction_mode.rs` (149 行)

> Compaction mode — how much structure the model gets to recover detail the
> lossy summary dropped. In `xai-chat-state` so flag resolution and the
> transcript-hint builder share one definition.

**公开 API：** enum`CompactionMode`

**函数（节选）：** `parse`, `with_segment_detail`, `segment_detail`, `writes_segments`, `transcript_hint`, `parse_maps_names_and_rejects_unknown`, `with_segment_detail_only_affects_segments`, `transcript_hint_needs_a_location`

---

### `compaction_transcript.rs` (821 行)

> Pure rendering of a compacted segment into self-contained markdown, aligned
> with the Python compaction implementation (`render_segment_to_markdown` /
> `compute_turn_stats`; INDEX built incrementally via [`INDEX_HEADER`] +
> [`render_index_row`]). No I/O.
> Not byte-identical — the data models differ (Python `Turn`/channels vs our
> [`ConversationItem`]) — but headers, sections, detail levels, and INDEX
> columns match.

**公开 API：** fn`segment_filename`, fn`parse_segment_index`, fn`classify_compaction_path`, fn`render_segment_md`, fn`render_index_row`, fn`extract_keywords`, enum`CompactionDetail`, enum`CompactionArtifact`

**函数（节选）：** `parse`, `role_label`, `segment_label`, `segment_filename`, `parse_segment_index`, `segment_index`, `classify_compaction_path`, `truncate_chars`, `with_thousands`, `arg_value_plain`, `tool_args`, `compute_turn_stats`, `render_stats_block`, `render_turn_verbose`, `render_turn_balanced`, `render_turn_signature`, `render_segment_md`, `render_index_row`, `extract_keywords`, `user`, `segment_md_matches_skeleton`, `detail_levels_select_turns_section`, `verbatim_turns_truncate_at_turn_boundary`, `index_row_matches_columns`, `segment_filename_round_trips`

---

### `compaction_utils.rs` (3745 行)

> Pure utility functions and types for compaction support.
> These are stateless functions that operate on conversation data only —
> no I/O, no actor state. They live in `xai-chat-state` so that both
> this crate and `xai-grok-shell` can share them without duplication.

**公开 API：** fn`strip_tool_messages_for_conversation_item`, fn`strip_reasoning_blocks`, fn`strip_images`, fn`prepare_conversation_for_summarization`, fn`prepare_conversation_for_segment`, fn`truncate_trailing_incomplete_tool_call`, fn`prepare_conversation_for_verbatim_summarization`, fn`fit_conversation_to_budget`, fn`extract_user_query`, fn`extract_last_user_query`, fn`is_synthetic_extracted_query`, fn`is_real_user_turn`, fn`extract_real_user_queries`, fn`extract_last_real_user_query`, fn`extract_messages_since_last_user`, fn`extract_messages_since_last_real_user`, fn`format_compact_summary`, fn`format_compact_summary_content`, fn`is_degenerate_summary`, fn`bound_captured_output`, fn`format_transcript_location`, fn`wrap_user_query`, fn`build_compacted_history`, fn`validate_compacted_history`, fn`sanitize_compacted_history`

**函数（节选）：** `strip_tool_messages_for_conversation_item`, `strip_reasoning_blocks`, `strip_images`, `prepare_conversation_for_summarization`, `prepare_conversation_for_segment`, `truncate_trailing_incomplete_tool_call`, `prepare_conversation_for_verbatim_summarization`, `estimate_item_tokens`, `fit_conversation_to_budget`, `recover_truncated_tail_unit`, `truncate_item_to_tokens`, `truncate_text_to_bytes`, `strip_system_tags`, `extract_user_query`, `extract_last_user_query`, `is_bootstrap_reminder_text`, `is_synthetic_extracted_query`, `is_real_user_turn`, `extract_real_user_queries`, `extract_last_real_user_query`, `extract_messages_since_last_user`, `extract_messages_since_last_real_user`, `is_actionable`, `tag`, `build`

---

### `conversation_util.rs` (117 行)

> Pure conversation-shape helpers, kept crate-neutral so both the session
> layer (`xai-grok-shell`) and the `ChatStateActor` can share one definition
> of "align the leading System message with a prompt".

**公开 API：** fn`canonical_system_prompt_eq`, fn`replace_or_insert_system_head`

**函数（节选）：** `canonical_system_prompt_eq`, `replace_or_insert_system_head`, `system_prompt`, `canonical_system_prompt_eq_ignores_trailing_newlines`, `canonical_system_prompt_eq_respects_interior_and_leading_whitespace`, `replace_or_insert_system_head_replaces_stored_head`, `replace_or_insert_system_head_noop_when_unchanged`, `replace_or_insert_system_head_inserts_when_first_is_not_system`, `replace_or_insert_system_head_inserts_into_empty`

---

### `events.rs` (52 行)

> Events emitted by the ChatStateActor.

**公开 API：** enum`ChatStateEvent`

**函数（节选）：** `event_variants_are_constructible`

---

### `handle.rs` (678 行)

> Handle to communicate with ChatStateActor.

**公开 API：** struct`ChatStateHandle`

**函数（节选）：** `new`, `noop`, `push_user_message`, `push_user_message_and_ack`, `append_working_directory_switch_and_ack`, `push_user_message_with_repair_reason`, `push_assistant_response`, `push_tool_result`, `record_token_usage`, `record_last_turn_usage`, `record_model_call_usage`, `record_subagent_usage`, `mark_usage_incomplete`, `increment_prompt_index`, `update_sampling_config`, `record_agent_edited_path`, `record_stream_start`, `record_turn_start`, `replace_conversation`, `replace_conversation_for_compaction`, `send_replace`, `repair_history`, `replace_system_head`, `cache_prompt_text`, `record_compaction_at`

---

### `lib.rs` (55 行)

> xai-chat-state — Actor-based chat state management for xAI agents.
> This crate extracts conversation state management from `xai-grok-shell`'s
> `acp_session.rs` into a standalone actor. It follows the same actor pattern
> as `xai-hunk-tracker`:
> ```text
> ┌────────────────┐                  ┌──────────────────────────────────────┐
> │ SessionActor   │ ─── Command ───▶ │        ChatStateActor                │
> │  (push_user,   │                  │  (runs in dedicated tokio task)      │
> │   build_req)   │                  │                                      │
> └────────────────┘                  │  State (no locks needed):            │

---

### `persistence.rs` (292 行)

> Chat persistence trait and mock implementation.
> The actor owns persistence exclusively (`Box<dyn ChatPersistence>`), so the
> trait uses `&mut self` — no locks, no atomics, no shared state.
> The mock uses a channel to report records to the test, keeping everything
> in the actor / message-passing paradigm.

**公开 API：** struct`MockChatPersistence`, struct`MockPersistenceReceiver`, struct`NullChatPersistence`, enum`PersistenceRecord`, trait`ChatPersistence`

**函数（节选）：** `persist_message`, `persist_working_directory_switch_and_ack`, `replace_history`, `flush`, `new`, `new_with_manual_persistence_ack`, `drain`, `next_persistence_ack`, `messages`, `persist_message`, `persist_working_directory_switch_and_ack`, `replace_history`, `flush`, `persist_message`, `persist_working_directory_switch_and_ack`, `replace_history`, `flush`, `mock_persistence_records_messages`, `mock_persistence_records_multiple_messages`, `mock_persistence_records_replace_history`, `mock_persistence_records_flush`, `mock_persistence_deduplicates_working_directory_generation`, `null_persistence_does_not_panic`

---

### `types.rs` (261 行)

> Shared domain types for the chat state actor.

**公开 API：** struct`ChatStateConfig`, struct`ChatStateSnapshot`, struct`NotificationMeta`, struct`PruningConfig`, struct`Credentials`, struct`TurnCapture`, struct`ConversationCounts`, struct`AutoCompactTrigger`, enum`AuthType`

**函数（节选）：** `default`, `snapshot_round_trips_through_serde_json`, `snapshot_round_trips_with_data`

---

### `usage.rs` (195 行)

> Per-prompt and per-session billing ledgers (not serialized).
> `total_tokens()` is input + output: Responses wire `total` is live context
> length. Compaction and other side calls never call `record_main_loop_call`.
> # Completeness ownership
> Wire incomplete is the OR of these stores (each has a distinct role):
> - **`UsageLedger.incomplete`** — durable on the bill snapshot. Set by nested
> subagent incomplete fold, drain timeout, true apply-miss, and
> `mark_usage_incomplete`. Monotonic for a ledger instance.
> - **Sticky (`subagent_usage_not_applied` on the coordinator)** — pin-scoped
> **report** signal (session-only attribution or apply-miss report). Not a

**公开 API：** struct`UsageTotals`, struct`UsageLedger`

**函数（节选）：** `from_call`, `total_tokens`, `cost_is_partial`, `fold_totals`, `merge_cost_ticks`, `record_main_loop_call`, `record_subagent`, `mark_incomplete`, `fold_entry`, `tu`, `ledger_sums_partial_subagent_and_zero_cost`

---

---

## 25. 源码文件索引（简表）

### `./`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `commands.rs` | 528 | Commands sent to the ChatStateActor. |
| `compaction_mode.rs` | 149 | Compaction mode — how much structure the model gets to recover de |
| `compaction_transcript.rs` | 821 | Pure rendering of a compacted segment into self-contained markdow |
| `compaction_utils.rs` | 3745 | Pure utility functions and types for compaction support. |
| `conversation_util.rs` | 117 | Pure conversation-shape helpers, kept crate-neutral so both the s |
| `events.rs` | 52 | Events emitted by the ChatStateActor. |
| `handle.rs` | 678 | Handle to communicate with ChatStateActor. |
| `lib.rs` | 55 | xai-chat-state — Actor-based chat state management for xAI agents |
| `persistence.rs` | 292 | Chat persistence trait and mock implementation. |
| `types.rs` | 261 | Shared domain types for the chat state actor. |
| `usage.rs` | 195 | Per-prompt and per-session billing ledgers (not serialized). |

### `actor/`

| 文件 | 行数 | 摘要 |
| --- | ---: | --- |
| `mod.rs` | 430 | ChatStateActor — runs in a dedicated tokio task and owns all chat |
| `mutations.rs` | 563 | Mutation handlers for the ChatStateActor. |
| `queries.rs` | 258 | Query handlers for the ChatStateActor. |
| `request_builder.rs` | 866 | ConversationRequest assembly — image compaction, pruning, repair, |
| `state.rs` | 404 | Internal state types for the ChatStateActor. |
| `tests.rs` | 4723 | Tests for ChatStateActor. |

---

## 26. 符号索引（pub API）

### `actor/mod.rs`

struct`ChatStateActor`

### `actor/request_builder.rs`

fn`should_prune` · fn`prune_conversation` · fn`compact_images_to_byte_budget` · struct`ImageEvictionOutcome`

### `actor/state.rs`

fn`estimate_system_message_tokens` · fn`estimate_tool_definition_tokens` · fn`estimate_tool_definitions_tokens` · fn`estimate_item_tokens` · fn`estimate_conversation_tokens` · fn`estimate_messages_tokens`
struct`EstimatedItemTokenCounter` · struct`ChatState`

### `commands.rs`

struct`ModelMetadata` · struct`RepairHistoryBlocked` · enum`StrictAppendAck` · enum`StrictAppendError` · enum`ChatStateCommand`

### `compaction_mode.rs`

enum`CompactionMode`

### `compaction_transcript.rs`

fn`segment_filename` · fn`parse_segment_index` · fn`classify_compaction_path` · fn`render_segment_md` · fn`render_index_row` · fn`extract_keywords`
enum`CompactionDetail` · enum`CompactionArtifact`

### `compaction_utils.rs`

fn`strip_tool_messages_for_conversation_item` · fn`strip_reasoning_blocks` · fn`strip_images` · fn`prepare_conversation_for_summarization` · fn`prepare_conversation_for_segment` · fn`truncate_trailing_incomplete_tool_call`
fn`prepare_conversation_for_verbatim_summarization` · fn`fit_conversation_to_budget` · fn`extract_user_query` · fn`extract_last_user_query` · fn`is_synthetic_extracted_query` · fn`is_real_user_turn`
fn`extract_real_user_queries` · fn`extract_last_real_user_query` · fn`extract_messages_since_last_user` · fn`extract_messages_since_last_real_user` · fn`format_compact_summary` · fn`format_compact_summary_content`
fn`is_degenerate_summary` · fn`bound_captured_output` · fn`format_transcript_location` · fn`wrap_user_query` · fn`build_compacted_history` · fn`validate_compacted_history`
fn`sanitize_compacted_history` · fn`repair_history` · fn`strip_displaced_tool_results` · struct`RunningSubagentSummary` · struct`BackgroundTaskSummary` · struct`CompactionServerSummary`
struct`TodoSummary` · struct`CompactionStateContext` · struct`CompactionInputs` · struct`CompactionAttempt` · struct`CompactedHistoryInput` · struct`SanitizeResult`
struct`HistoryRepairReport` · enum`TodoSummaryStatus`

### `conversation_util.rs`

fn`canonical_system_prompt_eq` · fn`replace_or_insert_system_head`

### `events.rs`

enum`ChatStateEvent`

### `handle.rs`

struct`ChatStateHandle`

### `persistence.rs`

struct`MockChatPersistence` · struct`MockPersistenceReceiver` · struct`NullChatPersistence` · enum`PersistenceRecord` · trait`ChatPersistence`

### `types.rs`

struct`ChatStateConfig` · struct`ChatStateSnapshot` · struct`NotificationMeta` · struct`PruningConfig` · struct`Credentials` · struct`TurnCapture`
struct`ConversationCounts` · struct`AutoCompactTrigger` · enum`AuthType`

### `usage.rs`

struct`UsageTotals` · struct`UsageLedger`

---


## 27. 依赖与被依赖

依赖：`xai-grok-compaction` · `xai-grok-sampling-types` · `xai-token-estimation`

被依赖：`xai-chat-state` · `xai-grok-shell`

---

## 28. 常见问题（读代码时）

**Q: 为什么 conversation 修改有时 clone 有时 in-place？**
A: `build_conversation_request` 仅在需要 prune/compact/inject 时 clone；
否则直接引用 actor 内 `conversation`，减少分配。

**Q: dangling tool call 是什么？**
A: assistant 发了 tool_calls 但没有对应 tool_result；`repair_history` 注入 synthetic result。

**Q: prompt_index 与 conversation len 区别？**
A: `prompt_index` 计用户轮次；conversation 含 system、tool、assistant 全部 item。

**Q: TurnCapture 为何用 offset 而非 Vec 拷贝？**
A: 避免每条 push 克隆 item；take 时一次性 `conversation[offset..].to_vec()`。

**Q: 与 xai-grok-compaction crate 关系？**
A: `EstimatedItemTokenCounter` 实现共享 compaction 引擎的 token 计数接口。

---

## 28. ChatStateActor::handle_command 分发（节选）

`actor/mod.rs` — 每个 `ChatStateCommand` 变体映射到 mutations/queries：

- `PushUserMessage` → `push_user_message`
- `PushAssistantResponse / PushToolResult` → `push_message`
- `BuildConversationRequest` → `ensure_integrity → build_conversation_request`
- `ReplaceConversation` → `replace_conversation + ConversationReset 事件`
- `RepairHistory` → `repair_history（可能返回 RepairHistoryBlocked）`
- `TruncateToPromptIndex` → `truncate + 重置 turn_capture`
- `GetConversation` → `clone conversation → oneshot`
- `CheckAutoCompactNeeded` → `token 阈值 + CompactionMode`
- `Flush` → `persistence.flush()`

---

## 29. 阅读顺序

1. `lib.rs` 架构图注释
2. `handle.rs` — 对外 API
3. `commands.rs` — 命令枚举
4. `actor/mod.rs` → `mutations.rs` / `request_builder.rs`
5. `compaction_transcript.rs` — 与 shell compaction 对照
6. [09 端到端](../09_end_to_end_request_flow.md) 采样段落

