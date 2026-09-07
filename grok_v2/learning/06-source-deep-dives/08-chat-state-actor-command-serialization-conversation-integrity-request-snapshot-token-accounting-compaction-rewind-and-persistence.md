# 源码精读 08：Chat State Actor 如何维护 Conversation、构建请求并安全替换历史

> 本篇深入 Agentic Loop 的权威状态源 `xai-chat-state`：为什么 Conversation 不直接放在 `SessionActor` 的共享锁里；User、Assistant、Reasoning、Backend Tool 与 Tool Result 如何按命令顺序追加；Build Request 为什么同时承担完整性修复、裁剪、图片预算与 Memory 注入；Token 估算和 Provider Usage 如何协作；Compaction、Rewind、Resume 与 Turn Capture 又如何在整段历史替换时避免丢消息。
>
> 源码基线：`ed6d543`。源码变化后优先按本文列出的类型、Command Variant 与函数名重新定位。

---

## 1. 先给结论：Chat State 不是一个 `Vec<Message>`

它至少同时承担六种职责：

1. 保存模型可见的 canonical Conversation。
2. 串行化来自多个异步任务的读写。
3. 在写边界修复 Tool Call / Tool Result 协议完整性。
4. 从一致快照构建下一次 `ConversationRequest`。
5. 同步增量持久化或整段替换持久化。
6. 维护 Prompt Index、Token、Usage、Rewind 和 Turn Capture 等控制状态。

因此更准确的模型是：

```text
Session / Tool / Sampler Tasks
          |
          | ChatStateCommand
          v
┌───────────────────────────────────┐
│ ChatStateActor                    │
│                                   │
│  Conversation + Config + Tokens  │
│  Prompt Index + Usage + Capture  │
│                                   │
│  sequential command processing   │
└───────────┬───────────┬───────────┘
            |           |
            |           +--> ChatStateEvent
            |
            +--------------> ChatPersistence
```

---

## 2. 本篇主线文件

| 文件 | 责任 |
| --- | --- |
| `xai-chat-state/src/lib.rs` | Crate 边界与公开导出 |
| `xai-chat-state/src/actor/mod.rs` | Actor Spawn、Run Loop 与 Command Dispatch |
| `xai-chat-state/src/actor/state.rs` | Actor 独占的 `ChatState` 与 Token 估算 |
| `xai-chat-state/src/commands.rs` | 所有 Mutation、Query 与 Reply 协议 |
| `xai-chat-state/src/handle.rs` | Session 使用的可 Clone Handle |
| `xai-chat-state/src/actor/mutations.rs` | 消息追加、完整性修复、替换、Token 与 Capture |
| `xai-chat-state/src/actor/request_builder.rs` | Request Clone、Pruning、Image Budget、Memory 注入 |
| `xai-chat-state/src/actor/queries.rs` | Snapshot、Rewind、Auto Compact 与窄查询 |
| `xai-chat-state/src/persistence.rs` | 持久化抽象与测试替身 |
| `xai-chat-state/src/usage.rs` | Prompt / Session Usage Ledger |
| `xai-chat-state/src/compaction_utils.rs` | Compacted History 校验和严格 History Repair |
| `xai-grok-sampling-types/src/conversation.rs` | Conversation Item 与基础 Tool Pair Repair |
| `xai-grok-shell/src/session/chat_persistence.rs` | Shell 的 Channel-backed 持久化适配器 |

---

## 3. 为什么使用 Actor 而不是 `Arc<Mutex<ChatState>>`

`ChatStateActor` 在独立 Tokio Task 中拥有全部可变状态：

```rust
pub struct ChatStateActor {
    state: ChatState,
    pruning_config: PruningConfig,
    persistence: Box<dyn ChatPersistence>,
    cmd_rx: mpsc::UnboundedReceiver<ChatStateCommand>,
    event_tx: mpsc::UnboundedSender<ChatStateEvent>,
    cancellation_token: CancellationToken,
}
```

所有字段只由 Actor Task 访问，所以内部不需要为 Conversation、Token、Prompt Index 分别加锁。

更重要的是，Actor 让复合操作成为同一串行处理单元：

```text
修复历史
  -> 持久化修复
  -> 追加 User
  -> 估算 Token Delta
```

如果这些动作由多个锁保护，很容易出现中间状态被其他任务读取。

---

## 4. Actor 的所有权边界

`ChatStateActor::spawn_with_pruning`：

1. 创建 Unbounded Command Channel。
2. 用初始 Conversation 构造 `ChatState`。
3. 将 Persistence 实现移入 Actor。
4. `tokio::spawn(actor.run())`。
5. 返回只持有 Sender 的 `ChatStateHandle`。

调用者没有 `&mut ChatState`，只能发 Command。

这形成一个强约束：

> Conversation 的正式变更只能经过 Actor Command Handler。

---

## 5. `ChatStateHandle` 为什么可以廉价 Clone

Handle 只有：

```rust
mpsc::UnboundedSender<ChatStateCommand>
```

Clone Handle 等价于 Clone Sender，不会 Clone Conversation。

Sampler Event Drainer、Tool Execution、Turn Driver 和 Session Control Flow 可以各自持有 Handle，但它们最终都向同一个 Receiver 发消息。

Actor 按收到的 Command 顺序逐个处理；并发发送者不再直接争用内部字段。

---

## 6. Actor Run Loop 的停止条件

主循环使用带 `biased` 的 `tokio::select!`：

```text
CancellationToken cancelled
  -> 退出

cmd_rx.recv()
  -> None：所有 Handle 已释放，退出
  -> Some(command)：处理命令
```

Cancellation 分支优先，意味着 Session Shutdown 不需要等命令 Channel 自然关闭。

Actor 停止后：

- Fire-and-forget Send 会失败但调用处通常忽略。
- Query 会记录 `send failed` 或 `reply dropped` 并返回 `None`。

---

## 7. Command 分成 Mutation 与 Query

`ChatStateCommand` 的大结构：

```text
Mutations
  PushUserMessage
  PushAssistantResponse
  PushToolResult
  RecordTokenUsage
  IncrementPromptIndex
  ReplaceConversation
  RepairHistory
  RestoreSnapshot
  ...

Queries
  BuildConversationRequest
  GetConversation
  GetPromptIndex
  Snapshot
  CheckAutoCompactNeeded
  GetLastAssistantText
  ...
```

Query 自带 `oneshot::Sender<T>`，Actor 处理后回传结果。

Mutation 不必然都是 fire-and-forget；需要确认完成语义的 Mutation 也带 Reply，例如 `PushUserMessageAndAck` 和严格 CWD Switch Append。

---

## 8. Fire-and-forget 的精确含义

例如：

```rust
handle.push_tool_result(item)
```

只是把 Command 放进 Channel，不等待：

- Actor 是否已经处理。
- Persistence Actor 是否已经写盘。
- 文件是否已经 Flush。

它提供低开销排队语义，不提供 Durable Commit Ack。

如果之后同一控制流发送 Query，Actor 处理 Query 时可以形成一个状态观察点；但不应把来自不同并发发送任务的相对发送时间想象成全局事务顺序。

---

## 9. 为什么有 `PushUserMessageAndAck`

某些路径需要确认 Actor 已经接受并处理 User Item，再继续外层动作。

`PushUserMessageAndAck` Handler：

```text
push_user_message(item)
  -> reply.send(())
```

这个 Ack 表示 Chat State Mutation 已完成，但普通 Persistence Trait 的 `persist_message` 仍只是下游 Channel Send，不表示磁盘 Fsync。

要区分：

```text
Actor Acceptance Ack != Durable Persistence Ack
```

---

## 10. 严格 CWD Switch 为什么有单独协议

Working Directory Switch 使用：

```rust
AppendWorkingDirectorySwitchAndAck
```

它先要求 Persistence 返回：

```text
Appended
AlreadyPresent(authoritative_item)
NotCommitted
Committed { acknowledgement, source }
Indeterminate
```

然后 Actor 才让内存状态收敛到磁盘权威项。

它使用 `cwd_generation` 做幂等键：重试同一 Generation 时，不会盲目追加重复 Switch。

---

## 11. Actor 内部的核心状态字段

`ChatState` 主要持有：

| 字段 | 作用 |
| --- | --- |
| `conversation` | 完整 canonical Conversation |
| `sampling_config` | Model、Context Window、Temperature 等 |
| `prompt_index` | 已开始的真实 Prompt Turn 计数 |
| `prompt_texts` | Rewind Preview 用的 Prompt 文本 |
| `total_tokens` | 最近 Provider 报告或替换后重估的上下文 Token |
| `estimated_tokens_since_model` | 最近 Model Response 后新增 Item 的估算 Delta |
| `estimate_at_last_response` | 最近 Token Usage 时 Conversation 的静态估值 |
| `last_turn_usage` | 最近一个模型响应的 TokenUsage |
| `prompt_usage` | 当前 Prompt 的 Billing Ledger |
| `session_usage` | Session 生命周期 Billing Ledger |
| `last_compaction_prompt_index` | 最近 Compaction 边界 |
| `agent_edited_paths` | Agent 编辑过的路径 |
| `turn_capture` | 当前 Turn 的消息切片捕获状态 |
| `harness_trace_*` | Planner/Verifier 等独立 Trace Turn |

---

## 12. `ChatState::new` 不是简单赋值

初始化时先对传入历史执行：

1. `dedup_duplicate_tool_results`。
2. `repair_dangling_tool_calls(UserCancelled)`。
3. `estimate_conversation_tokens`。

这处理一种常见恢复场景：进程在 Assistant Tool Call 已持久化、Tool Result 尚未持久化时崩溃。

Session Resume 后，初始内存历史不会一直保持 Provider 无法接受的悬空状态。

---

## 13. canonical Conversation 有哪些 Item

核心 Variant：

```text
System
User
Assistant
ToolResult
BackendToolCall
Reasoning
```

Chat State 不把 Reasoning 或 Backend Tool Call 压进 Assistant String；它保存规范化的平铺有序序列。

因此 `conversation` 是跨 Backend 继续采样、Session Replay 和 Compaction 的语义历史，不只是 UI Chat Bubble 列表。

---

## 14. `PushAssistantResponse` 和 `PushToolResult` 最终走同一函数

Command Handler 都调用：

```rust
self.push_message(item)
```

`push_message` 会：

1. 必要时增加 `estimated_tokens_since_model`。
2. 调用 `persistence.persist_message(&item)`。
3. `conversation.push(item)`。

这里的 Handler 不再按方法名校验 Variant。调用者传入的 `ConversationItem` 才决定实际角色。

这解释了上一篇看到的现象：Reasoning 与 BackendToolCall sibling 也可经 `push_tool_result` Handle 方法进入 Actor。

---

## 15. 为什么 Assistant 不增加 `estimated_tokens_since_model`

代码只对非 Assistant Item 增加增量估算：

```rust
let count_in_delta = !matches!(item, ConversationItem::Assistant(_));
```

Agent Loop 收到完整响应后先用 Provider `usage.total_tokens` 更新 `total_tokens`，这个数字已经覆盖刚生成的 Assistant 输出。

随后再追加 Assistant 时若重复计入 Delta，会二次收费。

而 Tool Result、Interjection、Reminder 等是在模型响应后新增，下一次 Provider Usage 尚未覆盖，因此要累加估算 Delta。

---

## 16. `PushUserMessage` 比普通 Push 多做两件事

它先：

```text
ensure_conversation_integrity
```

然后追加 User，最后：

```text
prune_retained_conversation
```

原因：新真实用户输入通常代表上一 Turn 已确定结束，是修复悬空 Tool Call 的安全写边界；同时也是回收极老 Tool Result 内存的自然时机。

---

## 17. 为什么不能在所有 Read Query 前自动 Repair

假设当前 Tool 正在运行：

```text
Assistant(tool_call A)
  -> A 仍在执行
```

此时后台任务调用 `GetConversation`。若 Read Handler 自动 Repair，会把 A 当成“缺少结果”，立即插入取消结果；真实结果稍后回来就重复。

所以源码明确规定：

- 普通 Read Query 是纯读取。
- Repair 只在上一 Turn 明确结束的写边界。
- `BuildConversationRequest` 保留防御性 Repair，因为 Agent Loop 在工具完成后才调用它。

---

## 18. 什么叫 Dangling Tool Call

一个 Assistant Item 声明：

```text
tool_calls = [A, B]
```

紧接着的连续 ToolResult 段只包含：

```text
ToolResult(A)
```

那么 B 是 Dangling。

“紧接着”非常关键：Provider Function Calling 历史要求结果位于声明调用后的连续结果段，而不是只要未来某处存在相同 ID 就算有效。

---

## 19. 基础 Repair 如何扫描

`repair_dangling_tool_calls` 两阶段执行：

### Phase 1

从头扫描每个含 Tool Call 的 Assistant，收集其后连续 ToolResult 的 ID，找出未回答调用。

### Phase 2

按反向 Index 应用插入，避免早期插入导致后续位置失效。

合成 Result 保持原 Tool Call 顺序。

---

## 20. 合成 Tool Result 的文案为何区分原因

`DanglingToolCallReason` 当前包括：

```text
UserCancelled
HarnessHalted { class }
```

生成的 Tool Result 会告诉模型：

- 用户取消，工具未执行。
- Harness 因某类内部原因中止，工具未执行。

这不只是满足 Provider Schema；它还给模型下一轮可操作的观察信息。

---

## 21. Duplicate Tool Result 如何处理

取消路径可能先插入合成结果，稍后真实 Tool Future 又完成：

```text
ToolResult(A, cancelled)
ToolResult(A, real output)
```

Provider 要求一个 Tool Call 只有一个 Result。

`dedup_duplicate_tool_results` 在紧邻 Result Run 内保留最后一个同 ID Result，删除前面的重复项。

保留最后一个的设计意图是让迟到的真实结果覆盖先前合成取消结果。

---

## 22. `ensure_conversation_integrity` 还要照顾 Turn Capture

Repair 会在 Conversation 中插入或删除 Item，可能让正在记录的 `turn_start_offset` 失效。

因此 Repair 前：

```text
snapshot_turn_slice
```

Repair 后：

```text
rebase_turn_capture_offset
```

这与整段 Replace 使用同一套 Capture 保护模式。

---

## 23. 普通 Integrity Repair 与显式 History Repair 不同

普通 Repair：

- 去重紧邻重复结果。
- 为紧邻段中缺失的调用补合成 Result。

显式 `repair_history`：

1. 去重。
2. 删除 Orphaned 或 Displaced ToolResult。
3. 为因此变成未回答的 Tool Call 补合成 Result。

后者解决会让 Provider 持续 400 的更严重历史损坏。

---

## 24. 什么是 Orphaned 与 Displaced Result

### Orphaned

```text
User
ToolResult(A)   // 前面没有声明 A 的 Assistant
```

### Displaced

```text
Assistant(tool A)
User/Assistant/Reasoning
ToolResult(A)   // 有 owner，但已不在紧邻 Result Run
```

显式 Repair 都会删除这些 Result，然后必要时在正确位置插入合成 Result。

---

## 25. 为什么 `sanitize_compacted_history` 比显式 Repair 宽松

Compaction Sanitizer 只要求：

> 每个 ToolResult 的 ID 在历史前方出现过。

而显式 `strip_displaced_tool_results` 要求：

> ToolResult 必须位于声明它的 Assistant 后面紧邻的连续 Result Run。

两者用途不同：

- Sanitizer 是 Compaction 产物的基础防护。
- Explicit Repair 面向已经被 Provider 严格邻接规则拒绝的 Session。

文档和源码注释都明确后者更严格。

---

## 26. 显式 Repair 为什么要检查 `turn_active`

用户可通过 Session Repair 接口主动修历史，但此时可能恰好有 Turn 开始。

只在调用者发送 Command 前检查有竞态：

```text
caller checks false
  -> new turn starts
  -> repair command reaches actor
  -> in-flight call 被误修
```

所以 Command 携带共享 Atomic Flag，Actor 在实际处理 `RepairHistory` 时再检查。

若为 Active，返回 `RepairHistoryBlocked`。

---

## 27. Repair 的原子性边界

显式非 Dry Run Repair：

1. `mem::take` 当前 Conversation。
2. 在本地 Vec 上执行三遍修复。
3. Changed 时走 `replace_conversation`。
4. No-op 时把原 Vec 放回。

整个流程发生在单个 Actor Command Handler 中，其他 Command 不会在三个 Pass 中间观察到半修复状态。

---

## 28. `BuildConversationRequest` 是 Query，为什么会 Mutation

它从 API 形态看是 Query，因为返回 Request；但 Handler 在构建前调用：

```text
ensure_conversation_integrity
```

并且 Memory Reminder 配置为持久化时，也可能修改 System Head 和持久化历史。

所以它更准确地属于“返回值型复合操作”，不是纯函数式 Read。

---

## 29. Build Request 的完整阶段

```text
Actor receives BuildConversationRequest
  |
  v
ensure_conversation_integrity(authoritative state)
  |
  v
decide prune / image compact / memory injection
  |
  +--> persistent memory may update authoritative state
  |
  v
clone authoritative conversation
  |
  v
mutate request working copy if needed
  |
  v
assemble ConversationRequest
  |
  v
oneshot reply
```

---

## 30. 为什么先修权威状态再 Clone

如果只修 Request Clone：

- 本次 Provider 请求可能成功。
- 但内存历史和磁盘历史仍损坏。
- 下一次 Clone 还要重复 Repair。
- Resume/Fork 仍可能读到坏历史。

当前实现对 Actor 自身 Conversation Repair，并在有变化时 `replace_history`。

Request Clone 因此从已修复状态开始，不需要再做 O(n) 的重复扫描。

---

## 31. Build Request 的 Hot Path

若：

- Context 不需要 Tool Result Prune。
- 没有临时 Memory Reminder。
- 请求体未接近图片大小上限。

则直接：

```rust
self.state.conversation.clone()
```

Conversation 中大量字符串多为 `Arc<str>`，Clone 主要增加引用计数，而不是复制所有正文和 Base64 字节。

---

## 32. Request Copy Pruning 何时触发

`should_prune` 条件是：

```text
total_tokens > context_window / 2
```

注意是严格大于 50%，等于 50% 不触发。

这个 Prune 在 Request Clone 上运行，主要减少发给模型的旧 Tool Result 内容，不必立刻重写 Actor 的完整 Conversation。

---

## 33. Soft Trim 与 Hard Clear

默认 `PruningConfig`：

```text
keep_last_n_turns = 3
soft_trim_threshold = 4000 chars
soft_trim_head = 1500 chars
soft_trim_tail = 1500 chars
hard_clear_age_turns = 10
```

Request Copy 中：

- 最近三 Turn 不裁剪。
- 较老且超过 4000 字符的 Result 保留 Head + Tail。
- 十 Turn 以上的 Result 替换为 Placeholder。

字符串切片按 Unicode `char`，不会在 UTF-8 字节中间截断。

---

## 34. Request Copy Prune 不等于持久化 Prune

构建 Request 时：

```text
clone conversation
  -> prune clone
  -> send clone to model
```

Actor 的 canonical Conversation 可以仍保留原 Tool Result。

这样 Request Context 可以受控，而 Session Replay 或后续需要时仍可能访问原始历史。

但极老 Result 还有另一条 retained-memory hard clear 路径，会真的修改权威状态。

---

## 35. Retained Conversation Hard Clear 何时发生

每次 `push_user_message` 后调用 `prune_retained_conversation`。

它只执行 Hard Clear，不做 Soft Trim：

```text
old ToolResult content
  -> [Tool result omitted — too old]
```

有变化时：

- Actor Conversation 被修改。
- `chat_history.jsonl` 通过 `replace_history` 同步。
- `updates.jsonl` 不被修改。

所以 Cross-compaction Replay 仍可依赖更完整的更新日志。

---

## 36. Synthetic User 为什么会影响 Turn Age

倒序 Prune 通过数 `User` Item 推断 Tool Result 属于多少 Turn 前。

但 System Reminder、Interjection 等也以 Synthetic User Item 保存，它们不代表真实 Prompt Turn。

Retained Hard Clear 通过：

```text
synthetic_count = total_user_items - prompt_index
effective_threshold = hard_clear_age_turns + synthetic_count
```

提高阈值，避免 Synthetic User 让旧 Result 看起来过早老化。

---

## 37. 图片预算为什么按字节而不是 Token

Inline Base64 Image 的直接风险不是模型 Context Token，而是 Proxy 请求体 50 MB 上限。

所以 Build Request 计算 Conversation JSON Body 的精确序列化字节数。

触发阈值：

```text
50 MB - 3 MB headroom
```

3 MB 预留给：

- Tool Definitions。
- Request Envelope。
- Sampling Params。
- Internal 到 Wire Format 的小差异。

---

## 38. 如何避免每轮扫描几十 MB Base64

`conversation_body_bytes`：

1. 浅 Clone Conversation。
2. 把 Clone 中的 Image URL 替换为空字符串。
3. 对小型 JSON 精确序列化计数。
4. 加回每个 Base64 URL 的原始长度。

Base64 不需要 JSON Escape，因此其原始长度就是序列化贡献。

这样得到精确结果，又避免 Serde 每 Turn 扫描全部大图字节。

---

## 39. Image Eviction 为什么有高水位和低水位

达到约 47 MB 才触发，但一旦触发会回收到约 25 MB。

```text
High-water Trigger: 47 MB
Low-water Target:   25 MB
```

这叫 Hysteresis：一次移除一批最老图片，换取之后多个 Turn 不再重写 Prompt Prefix。

如果只删到刚低于 47 MB，下一张图就会再次触发，持续破坏 KV Cache。

---

## 40. Image Eviction 修改哪里

它只修改 Request Working Copy：

```text
Image Part
  -> 诚实的 Text Placeholder
```

Actor canonical Conversation 不因此丢图。

Placeholder 明确告诉模型图片已经不可见，不要凭记忆描述，需要时让用户重新发送。

这比静默删除更能防止幻觉。

---

## 41. 为什么移除最老图片

Eviction 收集所有 Image Part 的 `(item_idx, part_idx, exact_bytes)`，按历史顺序从旧到新删除，直到达到低水位。

结果：

- 最新图片优先保留。
- 一张图片只会从“可见”转为 Placeholder。
- 稳定 Prefix 中不会因普通 Build Request 在可见/不可见间反复切换。

---

## 42. ImageBudget Event 的作用

只要 Conversation 含 Inline Image，Actor 会发：

```text
body_bytes
trigger_bytes
reclaim_target_bytes
inline_images
needs_image_compaction
evicted
body_bytes_after
```

Session Event Loop 将它写入 Unified Log。

这是观察性事件，不反向改变 Chat State。

---

## 43. Memory Reminder 有持久化与临时两种模式

Build Request 接收：

```text
memory_reminder
persist_memory_reminder
```

若持久化：

- 在 Actor authoritative Conversation 的 System Head 中 Upsert。
- `replace_history` 持久化。
- Request Clone 不再重复注入。

若不持久化：

- 只在本次 Working Copy 注入。
- Actor Conversation 不变。

---

## 44. Memory Upsert 为什么需要稳定 Tag

共享常量：

```text
<memory-context>
</memory-context>
```

若 System Prompt 已有该段，新 Reminder 会替换旧段，而不是不断追加。

Tag 必须由 Shell 的格式化方和 Chat State 的检测方共享；若文字漂移，Prompt Prefix 会积累多个 Memory Block。

---

## 45. Memory 注入如何处理没有 System Item 的历史

若第一个 Item 是 System：

- 在其文本中 Upsert。

否则：

- 在 Index 0 插入新的 System Item。

持久化注入可能移动 Active Turn Capture 的 Index，因此同样执行 Snapshot + Rebase。

---

## 46. 最终 `ConversationRequest` 从哪些字段组装

Actor 填写：

- `items`
- `tools`
- `model`
- `temperature`
- `max_output_tokens`
- `top_p`
- `x_grok_conv_id`
- `x_grok_req_id`
- `trace`
- `reasoning_effort`

暂留默认的字段包括：

- `hosted_tools`
- `tool_choice`
- Session / Turn / Agent / Deployment IDs
- `json_schema`

这些由上一篇中的 Turn Driver 在 Actor 返回后补齐。

---

## 47. 为什么 `build_request` 返回 `Option`

Handle 的通用 `query` 在两类 Actor 死亡场景返回 `None`：

1. Command Send 失败。
2. Reply Sender 被丢弃。

Agent Turn 当前调用处使用：

```rust
.expect("chat state actor should be alive")
```

这表明活跃 Session 中 Chat State Actor 消失属于内部不变量破坏，而不是普通可恢复业务错误。

---

## 48. Prompt Index 在何时增加

`IncrementPromptIndex` 注释说明：在每个真实 User Turn 开始时调用。

Handler：

```text
prompt_usage = None
prompt_index += 1
emit PromptIndexChanged
```

因此 Prompt Index 同时：

- 标识 Turn 编号。
- 开启一个新的 Prompt Billing Scope。
- 支持 Rewind 与 Compaction 边界。

---

## 49. Synthetic User 不应自动增加 Prompt Index

Interjection、System Reminder、Auto Continue、Tool-derived Image Follow-up 等可能保存成 `User` Variant，但不是新的外部 Prompt Turn。

它们通常通过 `push_user_message` 追加，却不一定调用 `increment_prompt_index`。

所以：

```text
User Item Count != Prompt Index
```

任何按 User Variant 计 Turn 的算法，都必须明确是否要排除 Synthetic Reason。

---

## 50. `get_last_assistant_text_in_turn` 如何找边界

它倒序扫描：

- 遇到非空 Assistant Text 就返回。
- 遇到真正开始 Prompt Turn 的 User Item就停止并返回 None。
- Mid-turn Synthetic Injection 会被跨过。

边界判断使用：

- `prompt_index.is_some()`。
- `synthetic_reason` 为空的真实 User。
- `SyntheticReason::starts_prompt_turn()`。

这比简单“遇到任何 User 就停止”更精确。

---

## 51. Rewind 的 `truncate_to_prompt_index`

语义：

```text
target 0 -> 只保留 User Turn 之前的前缀
target 1 -> 保留第一个 User Turn，删除第二个 User 起的内容
target N -> 保留 N 个 Turn
```

执行后：

- 截断 Conversation。
- 截断 Prompt Text Cache。
- 设置 Prompt Index。
- 重新估算 Token。
- 清零 Post-model Delta。
- Replace History。
- 发 ConversationReset。

---

## 52. 阅读 Rewind 实现时的一个警惕点

当前 `truncate_to_prompt_index` 查找位置时直接计数 `ConversationItem::User`，没有在该循环中检查 `synthetic_reason`。

而其他路径明确承认 User Item Count 与 Prompt Index 可不同。

这不等于本文断言存在可复现 Bug，但它是修改 Rewind、Synthetic Message 或历史格式时必须重点验证的契约交界。

测试应覆盖含 System Reminder、Interjection 和 Auto Continue 的多 Turn Rewind。

---

## 53. Rewind 后为什么清除 Turn Capture 与 Prompt Usage

Command Handler 在 Truncate 后额外：

```text
turn_capture = None
prompt_usage = None
```

因为当前 Turn 已被放弃：

- 原 Offset 已不再对应有效历史。
- 当前 Prompt 的 Billing 不应继续附着到 Rewind 后的状态。

Harness Trace Buffer 则故意保留，因为 Planner/Verifier 确实已经执行，可继续作为独立 Trace Artifact 上传。

---

## 54. Snapshot 包含什么

`ChatStateSnapshot` 包含：

- Conversation。
- Sampling Config。
- Prompt Index。
- Total Tokens。
- Estimate at Last Response。
- Edited Paths。
- Prompt Texts。
- Stream / Turn Timing。
- Last Compaction Prompt Index。
- Credentials。

它用于 Forking、Rewind/Restore 和 Session 初始化恢复。

Usage Ledger 与 Harness Trace Buffer 不在这个持久 Snapshot 中。

---

## 55. Restore Snapshot 为什么不是 `self.state = snapshot`

Restore 要保留一些瞬态状态，并特殊处理：

- Active Turn Capture 先 Snapshot Tail，再 Rebase。
- Harness Trace Buffer 不被覆盖。
- `estimated_tokens_since_model` 清零。
- 旧 Snapshot 没有 `estimate_at_last_response` 时重新估算。
- Prompt Usage 被清除。
- Session Lifetime Ledger 不被 Snapshot 倒退。

所以它逐字段恢复，而不是替换整个 Struct。

---

## 56. Replace Conversation 的常见来源

主要包括：

- Compaction。
- Rewind。
- Explicit History Repair。
- Replace System Head。
- 某些 Snapshot/恢复流程。

这些操作都可能让旧 Vec 的 Index、Token Estimate 和持久化形式失效，因此统一走 Actor 内的 Replace 逻辑。

---

## 57. `replace_system_head` 为什么必须在 Actor 内原子执行

危险写法：

```text
GetConversation
  -> caller 修改 System
  -> ReplaceConversation
```

两步之间若 Tool Result 到达，Caller 的旧 Clone 会覆盖新 Result，造成 Lost Update。

当前 Command 在 Actor 内：

1. 比较 Canonical System Prompt。
2. 浅 Clone 当前 Conversation。
3. 修改 Head。
4. 立即 Replace。

整个过程与其他 Push Command 串行。

---

## 58. System Prompt 比较为何容忍尾部换行

`replace_system_head` 使用 `canonical_system_prompt_eq`，避免只因无意义的 trailing newline 差异重写整个 Conversation。

无谓 Rewrite 会：

- 重置 Token Estimate。
- 发 ConversationReset。
- 重写持久化历史。
- 破坏 Provider Prompt Prefix Cache。

因此 No-op Detection 也是性能和缓存正确性的一部分。

---

## 59. Turn Capture 为什么不用每次 Push 都 Clone

`BeginTurnCapture` 只记录：

```text
turn_start_offset = conversation.len()
```

Take 时 Clone：

```text
conversation[turn_start_offset..]
```

这样普通 Push 不需要同时维护一个重复消息 Buffer。

只有发生 Conversation Replacement 时，才把即将丢失的旧 Tail 保存到 `pre_replacement_messages`。

---

## 60. Mid-turn Compaction 时 Capture 如何不丢前半段

假设：

```text
BeginCapture(offset=100)
append User, Assistant, ToolResult
Compaction Replace Conversation
append AutoContinue, Assistant
TakeTurnMessages
```

Replace 前：

```text
old[100..] -> pre_replacement_messages
```

Replace 后：

```text
turn_start_offset = new_conversation.len()
compaction_occurred = true
```

Take 时返回：

```text
pre_replacement_messages + new_tail
```

Trace 因此仍能看到一次用户 Turn 跨 Compaction 的完整消息。

---

## 61. `turn_tail` 为什么不用直接切片

直接：

```rust
conversation[offset..]
```

在 Offset 账目异常时会 Panic，生产中甚至曾导致 Session Abort。

当前实现：

- Debug 下 `debug_assert!`。
- Production 用 `get(offset..)`。
- 越界记录 Error 并返回空 Slice。

Trace 丢一段比终止用户 Session 更可接受。

---

## 62. Compaction Replacement 与普通 Replacement 的区别

Command 都是：

```rust
ReplaceConversation { items, is_compaction }
```

`is_compaction` 影响：

- Active Turn Capture 的 `compaction_occurred`。
- Token Reseed 是否携带 Provider-side Overhead Ratio。
- Compaction 后 Token 不得看起来增加。

Rewind 或 System Head Replace 则按新的静态 Conversation 估值重新开始。

---

## 63. 为什么 Compaction 后不能只用 `bytes / 4`

Provider 报告的 `total_tokens` 通常包含本地静态估算无法完全覆盖的开销：

- Chat Template。
- Role/Envelope。
- Tool Schema。
- Backend Tokenization 差异。

若 Compaction 后只用 `estimate_conversation_tokens(new_items)`，上下文利用率会突然被低估，后续 Auto Compact 触发过晚。

---

## 64. Compaction Token Reseed 公式

若有可信历史值：

```text
ratio = provider_total_before / static_estimate_at_last_response
reseed = static_estimate_after * ratio
reseed = min(reseed, provider_total_before)
```

若缺少可信 Provider Count 或静态基线为 0：

```text
reseed = static_estimate_after
```

这个 Ratio 模型让开销随保留内容比例缩放，而不是把旧固定 Overhead 全部加到很短的新历史上。

---

## 65. 为什么 Reseed 要 Cap 到 Compaction 前 Total

Compaction 的语义是释放上下文。

浮点 Ratio、估算偏差或特殊内容可能让计算值略高于旧 Total。

代码明确：

```text
compaction reseed <= pre_replace_total
```

否则一次“压缩成功”会在监控上看起来反而增加 Context Usage，并可能立刻重触发 Compaction。

---

## 66. `record_token_usage` 同时重置三个基线

收到 Provider Total 后：

```text
estimated_tokens_since_model = 0
estimate_at_last_response = estimate(current conversation)
total_tokens = provider total
emit TokensUpdated
```

之后新追加的 Tool Result/User Reminder 再从零累加 Delta。

这形成：

```text
estimated current total
  = provider total at last response
  + local deltas after that response
```

---

## 67. 为什么有 `GetTotalTokens` 和 `GetEstimatedTotalTokens`

`GetTotalTokens` 返回最近 Provider/Reseed 基线。

`GetEstimatedTotalTokens` 返回：

```text
total_tokens + estimated_tokens_since_model
```

前者适合基于权威 Response Usage 的统计；后者适合在 Tool Result 已增大、下一次模型尚未返回 Usage 时做 Preflight Overflow 检查。

---

## 68. Token Estimator 如何处理各 Item

| Item | 估算方式 |
| --- | --- |
| System | 文本 bytes / 4 |
| User Text | 文本 bytes / 4 |
| User Image | 每图固定 Image Token Estimate |
| Assistant | Text + Tool Arguments bytes / 4 |
| ToolResult | Content bytes / 4 |
| BackendToolCall | Text Summary bytes / 4 |
| Reasoning | Text + Encrypted Content bytes / 4 |

它不是精确 BPE Tokenizer，而是统一、廉价、可在 Compaction Budget 中复用的估值模型。

---

## 69. Tool Definition Token 也有单独估算

一个 Tool Definition 估算：

```text
name bytes
+ description bytes
+ JSON parameters bytes
-----------------------
           / 4
```

Conversation Token 与 Tool Definition Token 分开计算，因为 Tool Schema 不属于 `conversation` Vec，却占用实际 Request Context。

---

## 70. `last_turn_usage` 与 `total_tokens` 不同

`last_turn_usage` 保存最近一次 Model Response 的详细：

- Prompt Tokens。
- Completion Tokens。
- Reasoning Tokens。
- Cached Read Tokens。
- Cache Creation Tokens。

它用于构造 Prompt Response Metadata。

`total_tokens` 则是当前上下文长度/估算基线，用于 Context Management。

---

## 71. Prompt Usage 与 Session Usage

每次 Main Agent Model Call 同时写：

```text
prompt_usage
session_usage
```

新 Prompt Index 会清空 `prompt_usage`，但 `session_usage` 继续累计。

Subagent Usage：

- 可选择是否归因到当前 Prompt。
- 始终可归入 Session Ledger。
- 不增加 Main Loop Model Call Count。

---

## 72. 为什么 Usage 读取要 Fail Closed

Handle 对 Billing Query 提供：

```text
try_get_prompt_usage -> Result<Option<Ledger>, ()>
try_get_session_usage -> Result<Ledger, ()>
```

Actor 不可用不能被解释成“没有费用”或“零 Token”。

因此：

```text
Ok(None) = Actor 明确回答当前没有 Prompt Ledger
Err(())  = Actor 没有回答，账单状态未知
```

---

## 73. `UsageLedger.incomplete` 表示什么

它是单调标记：账单可能少计，例如：

- Subagent Usage Drain 超时。
- Nested Subagent 本身报告 Incomplete。
- Usage Apply 失败。
- 显式 `mark_usage_incomplete`。

一旦置为 True，本 Ledger 不会因为后来又收到部分数据而自动恢复 Complete。

---

## 74. Persistence Trait 的四个动作

```rust
persist_message(item)
persist_working_directory_switch_and_ack(item)
replace_history(items)
flush()
```

Actor 独占 `Box<dyn ChatPersistence>`，Trait 方法使用 `&mut self`，不需要 Persistence 实现再包内部锁。

单条追加和整段替换被明确区分，便于 Compaction/Rewind 使用原子性更强的 Replace 通道。

---

## 75. Shell 的真实 Persistence Adapter

`ChannelChatPersistence` 映射：

| Chat State 动作 | Shell Persistence Message |
| --- | --- |
| `persist_message` | `PersistenceMsg::Chat` |
| CWD Strict Append | `AppendCwdSwitchAndAck` |
| `replace_history` | `ReplaceChatHistory` |
| `flush` | `Flush` |

普通 Persist 调用只是发给另一个 Unbounded Channel。

因此 Actor State 与 Disk Writer 解耦，但普通 Append 不是同步磁盘写。

---

## 76. 为什么 Actor 先发送 Persist 再 Push 内存

`push_message` 当前顺序：

```text
persistence.persist_message(&item)
conversation.push(item)
```

对于 Channel Adapter，这保证 Persistence Message 的排队发生在 Actor 内存追加之前。

但 Sender Send 成功只表示消息进入下游 Queue，不表示文件已写入。真正要求落盘边界时仍要发 `Flush`，或使用专门的 Ack 协议。

---

## 77. `ChatStateEvent` 为什么不承载 Conversation 内容

事件只有协调信号：

- `PromptIndexChanged`
- `TokensUpdated`
- `ConversationReset`
- `ImageBudget`

完整 Conversation 不通过 Event 广播，因为：

- 内容大。
- 容易产生多个镜像状态。
- 消费者需要时可 Query Actor。

Persistence 也由 Actor 内部处理，不通过 Session Event Consumer 反向写入。

---

## 78. `ConversationReset` 的 Shell 副作用

Session Run Loop 收到 Reset 后：

- 更新 Idle Flush 的 Conversation Length 水位。
- 重新开启 First-turn Memory Injection 检查。

PromptIndex 和 Token Update 当前主要是信息事件；需要值的消费者直接 Query Actor。

这避免 Event Payload 变成另一份权威状态。

---

## 79. Narrow Query 为什么值得存在

`GetConversation` 必须 Clone 整个 Vec。

很多调用只需要：

- Length。
- 是否有 Dangling Call。
- 最后 Assistant Text。
- 第一个 User Text。
- 某个 Index Item。
- Role Counts。
- System Message。

窄 Query 在 Actor 内扫描并只返回小结果，可减少长 Session 的 Clone 成本和内存峰值。

---

## 80. Snapshot Query 与 Conversation Query 的成本区别

`GetConversation` Clone Conversation。

`Snapshot` 除 Conversation 外还 Clone：

- Sampling Config。
- Prompt Texts。
- Edited Paths。
- Credentials 等。

所以 Snapshot 应用于 Fork/Restore 等真正需要完整状态的场景，不应用于只想读最后回答的热路径。

---

## 81. Auto Compact 检查读哪个 Token

`check_auto_compact_needed` 当前比较：

```text
state.total_tokens
vs
context_window * threshold_percent
```

而 Tool Result 后、下一次模型前的增量溢出使用 `GetEstimatedTotalTokens` 或 Shell 的 Preflight Overflow 路径。

这区分：

- 基于 Provider Context Total 的常规 Auto Compact。
- 基于本地新增 Tool Result Delta 的下一请求保护。

---

## 82. Compaction 在 Shell 与 Actor 之间如何分工

Shell Compaction Pipeline 负责：

- 选择要总结的历史。
- 调用 Summary Model。
- 构建 Compacted History。
- Sanitize/Validate Tool Pair。
- 保存 Compaction Segment/Checkpoint。

Chat State Actor 负责：

- 记录 Compaction Prompt Index。
- 替换 canonical Conversation。
- 保护 Turn Capture。
- Reseed Token。
- Replace Persistence。
- 发 Reset Event。

Actor 不负责生成 Summary 内容。

---

## 83. Compacted History 为什么仍要校验 Tool Pair

Summary 前后可能保留部分最近原文：

```text
Assistant(tool_calls)
ToolResult
Reasoning
User Reminder
```

若截取边界不正确，可能留下没有 Owner 的 Tool Result。

Shell 在 Replace 前：

1. `sanitize_compacted_history`。
2. `validate_compacted_history`。
3. 仍有问题则回退到不含 Recent Messages 的最小历史。

只有随后才交给 Actor Replace。

---

## 84. 一次普通 Agent Round 的 Chat State 时序

```text
IncrementPromptIndex
BeginTurnCapture
PushUserMessage
  -> repair previous dangling
  -> persist + append

BuildRequest
  -> integrity guard
  -> clone/prune/inject

Provider Response
RecordTokenUsage(provider total)
RecordLastTurnUsage(details)
RecordModelCallUsage(billing)
PushAssistantResponse

Tool Dispatch
PushToolResult(A)
PushToolResult(B)

BuildRequest
  -> sees Assistant + both Results

PushAssistantResponse(final)
TakeTurnMessages
Flush
```

---

## 85. 一次 Cancel + Resume 的修复时序

```text
Assistant(tool A, tool B) appended
ToolResult(A) appended
User cancels before B settles
Process exits / turn ends

next PushUserMessage or BuildRequest
  -> dedup existing result run
  -> detect B unanswered
  -> insert synthetic ToolResult(B, cancelled)
  -> replace persisted history
  -> append new User
```

下一次 Provider 不会再因 B 没有 Result 而拒绝整个历史。

---

## 86. 一次 Mid-turn Compaction 时序

```text
BeginTurnCapture(offset=N)
Push User / Assistant / ToolResult
  |
  v
Shell generates compacted history
  |
  v
ReplaceConversation(is_compaction=true)
  - snapshot old turn tail
  - mark compaction_occurred
  - persist replacement
  - ratio reseed tokens
  - rebase capture offset
  - emit reset
  |
  v
Push AutoContinue / new Assistant
  |
  v
TakeTurnMessages
  = old tail + post-replace tail
```

---

## 87. 最容易误读的五个点

### 1. `PushToolResult` 不强制参数 Variant

Reasoning/BackendToolCall sibling 也可能通过它追加。

### 2. `BuildConversationRequest` 不是纯 Read

它会 Repair，且可能持久化 Memory Reminder。

### 3. Fire-and-forget Persist 不等于写盘完成

Channel Send 与 Durable Commit 是不同边界。

### 4. Request Prune 不一定修改权威历史

Soft Trim 与 Image Eviction 主要发生在 Clone。

### 5. `User` Variant 不等于真实 Prompt Turn

Synthetic Reason 与 Prompt Index 必须一起理解。

---

## 88. 设计不变量清单

- [ ] 只有 Chat State Actor 正式修改 canonical Conversation。
- [ ] Read Query 不在 Tool 执行期间擅自 Repair。
- [ ] Assistant Tool Call 后必须有紧邻且唯一的 Tool Result。
- [ ] Tool Call/Result 配对依赖 ID，不依赖并发完成顺序。
- [ ] Build Request 从已 Repair 的权威状态 Clone。
- [ ] Request-only Prune 不意外回写 canonical history。
- [ ] Replace 在 Active Capture 下先 Snapshot Tail。
- [ ] Compaction Reseed 不超过旧 Provider Total。
- [ ] Provider Usage 后清零 Local Delta。
- [ ] Synthetic User 不被无意计成真实 Prompt Turn。
- [ ] Actor 不可用时 Billing Query Fail Closed。
- [ ] Ordinary Ack 不被描述成 Durable Persistence Ack。

---

## 89. 推荐断点

```text
ChatStateActor::handle_command
push_user_message_with_repair_reason
ensure_conversation_integrity_with_reason
repair_history
push_message
build_conversation_request
prune_conversation
compact_images_to_byte_budget
inject_memory_reminder
record_token_usage
replace_conversation
snapshot_turn_slice
rebase_turn_capture_offset
truncate_to_prompt_index
```

重点观察：

```text
conversation.len()
prompt_index
total_tokens
estimated_tokens_since_model
estimate_at_last_response
turn_capture.turn_start_offset
pre_replacement_messages.len()
is_compaction
```

---

## 90. 最小追加实验

依次发送：

```text
PushUserMessage
PushAssistantResponse(tool A)
PushToolResult(A)
BuildConversationRequest
```

验证 Request Items 顺序：

```text
User
Assistant(tool A)
ToolResult(A)
```

同时检查 Mock Persistence Record：

```text
Message(User)
Message(Assistant)
Message(ToolResult)
```

---

## 91. Dangling Repair 实验

构造：

```text
Assistant(tool A, tool B)
ToolResult(A)
```

调用 `BuildConversationRequest` 后验证：

- Actor Conversation 也得到 Synthetic Result B。
- Persistence 收到 ReplaceHistory。
- Request 中 A、B 都有结果。
- 再 Build 一次不发生变化，证明 Idempotent。

---

## 92. Displaced Result 实验

构造：

```text
Assistant(tool A)
Assistant(text)
ToolResult(A)
```

分别验证：

- 普通 Dangling Repair 如何理解这段历史。
- Explicit `repair_history` 删除 displaced A。
- 在 Assistant(tool A) 后插入 Synthetic Result。
- 第二次 Repair Report 全为零。

---

## 93. Request-only Prune 实验

构造超过 Context 50% 的多 Turn History，包含 5000 字符的旧 Tool Result。

Build Request 后比较：

```text
request.items old result = head + trim marker + tail
actor.get_conversation old result = original full content
```

再推进到 Hard Clear Age 并 Push 新 User，验证 Actor History 才真正变成 Placeholder。

---

## 94. Token Delta 实验

```text
RecordTokenUsage(100_000)
PushAssistantResponse(4000 chars)
GetEstimatedTotalTokens -> 100_000
PushToolResult(4000 chars)
GetEstimatedTotalTokens -> about 101_000
```

这个实验直接验证 Assistant 不重复计 Delta，而 Post-response Tool Result 会被估算。

---

## 95. Compaction Reseed 实验

记录：

```text
provider_total_before
estimate_at_last_response
new_compacted_static_estimate
new_total_tokens
```

验证：

```text
new_total ≈ new_estimate * old_provider / old_estimate
new_total <= old_provider_total
```

同时比较普通 `replace_conversation`：它应直接使用新静态估值，不携带 Compaction Ratio。

---

## 96. Turn Capture Replace 实验

1. Begin Capture。
2. Push 两个 Item。
3. Replace Conversation for Compaction。
4. Push 两个新 Item。
5. Take Turn Messages。

期望：

- 返回四个 Item，顺序为 Replace 前两项 + Replace 后两项。
- `compaction_occurred == true`。
- 再 Take 返回 None。

---

## 97. Rewind + Synthetic User 实验

这是最值得补强的边界测试：

```text
Real User 1
Assistant
SystemReminder User
Interjection User
Assistant
Real User 2
Assistant
```

对不同 Target Prompt Index 执行 Truncate，核对真正保留的 Turn。

如果契约要求按真实 Prompt 计数，测试应明确约束 Synthetic User 是否参与定位，而不能只测试纯 User/Assistant 交替历史。

---

## 98. 相关测试命令

```sh
cargo test -p xai-chat-state --lib
cargo test -p xai-grok-sampling-types --lib conversation::tests::test_repair
```

快速定位：

```sh
rg "push_user_message|replace_conversation|build_conversation_request|repair_history|turn_capture" \
  crates/codegen/xai-chat-state/src
```

重点测试组：

- Actor Command 与 Persistence Record。
- Dangling Repair / Duplicate Result。
- Request Pruning 与 Image Eviction。
- Compaction Token Reseed。
- Turn Capture Across Replace。
- Snapshot/Restore。
- Truncate/Rewind。

---

## 99. 一句话记忆模型

```text
Chat State Actor =
  用单一异步所有者串行维护 canonical Conversation
  + 在安全写边界修复 Function Call 历史
  + 为每次模型调用生成隔离的 Request Snapshot
  + 区分 Provider Token 基线与本地 Post-response Delta
  + 将增量追加、整段替换和持久化协调起来
  + 在 Compaction/Rewind 时保护 Turn Capture 和控制状态
```

---

## 100. 本篇术语表

| 名词 | 白话解释 | 在本篇中的精确含义 |
| --- | --- | --- |
| Chat State | 对话运行状态 | Conversation、Sampling Config、Prompt Index、Token、Usage、Capture 的合集 |
| Actor | 独占状态并串行处理消息的任务 | `ChatStateActor` 在 Tokio Task 中接收 `ChatStateCommand` |
| Handle | 向 Actor 发消息的轻量句柄 | `ChatStateHandle`，只持有 Cloneable Sender |
| Command | 发给 Actor 的操作消息 | Mutation 或带 oneshot Reply 的 Query |
| Mutation | 改变 Actor State 的命令 | Push、Replace、Record Usage、Restore 等 |
| Query | 请求 Actor 返回数据的命令 | Get、Snapshot、Build Request 等；Build Request 仍可能有修复副作用 |
| oneshot | 只发送一次结果的异步通道 | Query Command 用它把处理结果返回 Handle |
| fire-and-forget | 发出后不等待处理结果 | 大多数 Push/Record Handle 方法的语义 |
| Ack | 已确认某个阶段完成 | 普通 Actor Ack 与 Durable Persistence Ack 必须区分 |
| canonical Conversation | 权威模型历史 | Actor 中可持久化并用于后续请求的 `Vec<ConversationItem>` |
| Conversation Item | 历史中的一个规范条目 | System、User、Assistant、ToolResult、BackendToolCall、Reasoning |
| sibling | 与 Assistant 并列保存的响应项 | Reasoning、BackendToolCall 等保持 Provider 输出顺序 |
| authoritative state | 后续判断应依赖的权威状态 | Actor 内部的 Conversation，而非调用者旧 Clone |
| serialization | 把并发操作排成单一处理顺序 | Actor 对收到的 Command 逐个处理；也可指 JSON 编码，正文会注明 |
| Lost Update | 用旧快照覆盖并发新写入 | Actor 内 `ReplaceSystemHead` 避免的读改写竞态 |
| Dangling Tool Call | 声明后没有紧邻结果的调用 | Assistant Tool Call ID 在后续连续 ToolResult Run 中未被回答 |
| Synthetic Tool Result | Runtime 补造的工具结果 | 表明用户取消或 Harness Halt，使 Provider History 合法 |
| Duplicate Result | 同一 Tool Call ID 有多个结果 | Repair 保留紧邻 Run 中最后一个结果 |
| Orphaned Result | 没有合法 Owner 的 Tool Result | 历史前方没有声明它的 Assistant Tool Call |
| Displaced Result | Owner 存在但结果位置不合法 | 被其他非 ToolResult Item 与 Owner 分隔 |
| adjacency | 邻接约束 | Tool Results 必须紧跟声明调用的 Assistant，形成连续 Run |
| Integrity Repair | 恢复 Tool Pair 协议完整性 | 去重、补 Dangling；显式 Repair 还删除 Orphan/Displaced |
| idempotent | 重复执行不会继续改变结果 | Repair 干净历史时第二次 Report 为 No-op |
| write boundary | 可安全修复历史的状态边界 | 上一 Turn 已结束的新 User Push、Startup 或 Build Request |
| turn_active | 当前是否有 User Turn 正在运行 | Explicit Repair 在 Actor 处理时检查的 Atomic Flag |
| Request Snapshot | 一次模型请求使用的历史副本 | 从 authoritative Conversation Clone，再按请求预算修改 |
| Hot Path | 最常见、需低开销的执行路径 | 无 Prune/Memory/Image Mutation 时直接浅 Clone |
| Pruning | 缩短旧 Tool Result 内容 | Request-only Soft Trim 或 retained-history Hard Clear |
| Soft Trim | 保留长结果首尾 | Request Copy 中用 `[…trimmed…]` 连接 Head/Tail |
| Hard Clear | 整段替换为省略提示 | 极老 Tool Result 变成固定 Placeholder |
| retained conversation | Actor 实际长期持有的历史 | 与单次 Request Working Copy 相对 |
| Working Copy | 为构建请求 Clone 的可变历史 | Prune/Image Eviction 通常只作用于此副本 |
| Inline Image | 直接作为 Data URL 放进消息的图片 | 可能使 HTTP Request Body 接近 50 MB |
| Byte Budget | 请求体字节限制 | 与 Model Context Token Budget 不同 |
| Hysteresis | 高阈值触发、低阈值收敛 | 图片到 47 MB 才删，一次回收到约 25 MB |
| High-water Mark | 开始治理的上限 | Image Compaction Trigger |
| Low-water Mark | 治理后回收到的目标 | Image Reclaim Target |
| Memory Reminder | 注入模型上下文的长期记忆块 | 可持久 Upsert System Head 或只注入 Request Clone |
| Upsert | 有则替换、无则插入 | 通过 `<memory-context>` 标记维护单一 Memory Block |
| Prompt Index | 真实 Prompt Turn 的计数 | Turn 开始时增加，不应等同所有 User Item 数量 |
| Synthetic User | Runtime 生成、Wire Role 为 User 的 Item | Reminder、Interjection、AutoContinue 等 |
| Rewind | 把 Session 回退到旧 Prompt 边界 | Truncate Conversation、Prompt Cache 与 Token State |
| Snapshot | Actor 状态的可序列化副本 | 用于 Fork/Restore，不含所有瞬态 Ledger/Trace Buffer |
| Restore | 用 Snapshot 恢复字段 | 保留 Harness Trace、清除 Abandoned Prompt Usage |
| Replace Conversation | 整段替换 canonical history | Compaction、Rewind、Repair 等共享的 Actor Mutation |
| Turn Capture | 收集一个 User Turn 内追加的消息 | 用 Offset + Pre-replacement Buffer，避免每 Push Clone |
| Offset | Vec 中 Turn Tail 的起始下标 | Replace 前必须 Snapshot 并在 Replace 后 Rebase |
| Rebase | 将 Capture Offset 对齐新 Vec | Conversation 结构变化后的索引修复 |
| Compaction | 用摘要替换较长历史 | Summary 由 Shell 生成，Actor 负责 Replace 与 Token Reseed |
| Reseed | 替换历史后重建 Token 基线 | Compaction 使用 Provider/Estimate Ratio，普通 Replace 用静态估值 |
| Provider Total | 模型服务报告的上下文 Token | `usage.total_tokens`，比 bytes/4 更接近真实 Wire Cost |
| Static Estimate | 本地廉价 Token 估算 | 文本约 bytes/4，图片用固定值 |
| Delta | 最近 Provider 响应后新增内容估值 | `estimated_tokens_since_model`，主要覆盖 ToolResult/User 等 |
| Context Window | 模型一次请求可容纳的上限 | Auto Compact 与 Overflow Guard 的预算基准 |
| Usage Ledger | Token、Model Call、Duration、Cost 的账本 | 分当前 Prompt 与整个 Session 两种 Scope |
| fail closed | 无法确认时按不完整/失败处理 | Actor 无回复不能被误判为零成本 |
| Persistence | 将 Conversation 写入长期存储 | Actor 通过 Trait 调用 Shell 的 Persistence Channel |
| Append Persistence | 追加单个 Item | 普通 `PersistenceMsg::Chat` |
| Replace Persistence | 用新 Vec 重写历史 | Compaction、Rewind、Repair 使用 |
| Flush | 请求下游写出排队数据 | 与普通 Channel Send 区分 |
| CWD Generation | 工作目录切换的幂等序号 | Strict Append 重试时识别 AlreadyPresent |
| Event | Actor 发给 Session 的协调通知 | PromptIndex、Tokens、Reset、ImageBudget，不携带完整历史 |
| Narrow Query | 只返回所需小结果的查询 | 避免 Clone 整个长 Conversation |
| KV Cache | Provider 对稳定 Prompt Prefix 的缓存 | 无谓 Replace、图片反复删除会使 Prefix Cache Miss |

更多通用名词见 [全局术语表](../appendices/glossary.md)。

---

## 101. 下一篇建议

Prompt、Skill、Tool、Agentic Loop 和 Chat State 已经组成主干。下一篇最值得继续精读 Sampler：

```text
ConversationRequest 如何按 Backend 转换
  -> Responses / Chat Completions / Messages 三条 Stream Parser
  -> Delta 如何组装 canonical ConversationResponse
  -> Empty Response、Idle Timeout、413、Doom Loop 如何分类
  -> Retry Policy 如何保证失败 Attempt 不污染 Chat State
```

它会把“模型调用”这个目前仍像黑盒的部分彻底展开。
