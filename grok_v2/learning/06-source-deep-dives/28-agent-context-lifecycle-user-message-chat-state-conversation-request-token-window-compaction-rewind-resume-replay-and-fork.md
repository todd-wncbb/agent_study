# 源码精读 28：Agent Context Lifecycle——用户消息、ChatState、Context Window、Compaction、Rewind、Resume、Replay 与 Fork

> 源码基线：`ed6d543`
>
> 本篇不再只研究“Prompt 由哪些字符串拼起来”，而是追踪上下文本身的一生：一条用户消息怎样进入状态、怎样变成模型请求、怎样被压缩、怎样回退、怎样在重启后恢复，又怎样从同一历史分叉出新会话。

---

## 1. 本篇解决什么问题

读完后应能回答：

- 用户输入在 `handle_prompt` 中经历哪些转换？
- `ConversationItem` 为什么不是简单的 `role + content`？
- `ChatStateActor` 为什么要独占对话状态？
- `conversation`、`chat_history.jsonl`、`updates.jsonl` 分别是谁的事实来源？
- 为什么“用户在 UI 里看见的历史”不等于“模型下一次看见的历史”？
- Token 的模型真实 Usage 与本地估算怎样拼成当前上下文占用？
- Tool Result 裁剪、图片驱逐与 Compaction 有什么本质区别？
- 两阶段 Compaction 怎样避免使用已经过期的第一阶段摘要？
- 为什么跨越 Compaction 的 Rewind 不能直接截断当前数组？
- Resume 时为什么同时需要恢复模型状态和重放客户端事件？
- Session Fork 与 subagent `fork_context` 为什么不是同一件事？
- 修改 Context Runtime 时，哪些不变量最容易被破坏？

---

## 2. 先给出核心结论

Grok Build 没有一份包办所有用途的“聊天记录”。它至少维护三种投影：

| 投影 | 主要载体 | 服务对象 | 是否会被整体替换 |
| --- | --- | --- | --- |
| 模型对话状态 | `ChatState.conversation`、`chat_history.jsonl` | 下一次模型请求 | 会，Compaction、Rewind、Repair 都可能替换 |
| 客户端事件时间线 | `updates.jsonl` | UI replay、断线重连、跨压缩重建 | 正常只追加，以 Marker 表示逻辑回退 |
| 运行时辅助状态 | prompt index、token ledger、file snapshots、todo/plan/tool sidecars 等 | 调度、计量、回退、恢复 | 按各自协议更新或快照 |

最重要的阅读公式是：

```text
用户看到的 Session
    ≠ 模型下一次收到的 ConversationRequest
    ≠ 磁盘上 append-only 的原始事件流
```

这不是数据重复，而是三个消费者需要不同性质的状态。

---

## 3. 全生命周期总图

```text
ACP PromptRequest
      │
      ▼
SessionActor::handle_prompt
      ├── 写 UserMessageChunk ───────────────► updates.jsonl ──► UI Replay
      ├── parse / skill / image / origin
      ├── increment_prompt_index
      └── ConversationItem::User
                    │
                    ▼
            ChatStateActor mailbox
                    │
          repair → persist → append
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
 in-memory conversation   chat_history.jsonl
          │                   │
          ▼                   └──────────────► Resume model state
 build_conversation_request
          │
 image eviction / tool pruning / memory injection
          │
          ▼
 ConversationRequest + ToolSpec
          │
          ▼
       Provider
          │
 Assistant / Reasoning / ToolCall / Usage
          │
          └──────────────► ChatState + updates.jsonl

Context approaching limit
          │
          ▼
 Compaction checkpoint + summary + replace_history

Rewind across compaction
          │
          ▼
 updates.jsonl + checkpoint ──► reconstructed conversation
```

---

## 4. 核心源码地图

| 领域 | 主要源码 |
| --- | --- |
| 用户 Turn 入口 | `xai-grok-shell/src/session/acp_session_impl/turn.rs` |
| Prompt 解析和动态上下文 | `acp_session_impl/prompt_build.rs` |
| ChatState Handle | `xai-chat-state/src/handle.rs` |
| ChatState 命令循环 | `xai-chat-state/src/actor/mod.rs` |
| 状态结构与 Token 估算 | `xai-chat-state/src/actor/state.rs` |
| 消息追加与历史修复 | `xai-chat-state/src/actor/mutations.rs` |
| 模型请求组装 | `xai-chat-state/src/actor/request_builder.rs` |
| 持久化端口 | `xai-chat-state/src/persistence.rs` |
| 自动压缩与完整替换 | `xai-grok-shell/src/session/compaction.rs`、`helpers/full_replace_compaction.rs` |
| 两阶段压缩 | `session/compaction_segments.rs`、`compaction_config.rs` |
| Rewind | `session/acp_session_impl/rewind.rs` |
| 跨压缩重建 | `session/helpers/replay.rs` |
| Resume 客户端回放 | `agent/mvp_agent/session_setup.rs`、`agent/mvp_agent/mod.rs` |
| Session Fork | `session/fork.rs`、`session/storage/jsonl/copy.rs` |
| subagent context fork | `agent/subagent/handle_request.rs`、`agent/subagent/mod.rs` |

---

# 第一部分：用户消息怎样进入 Context

## 5. 外部输入首先是 ACP ContentBlock

`handle_prompt` 接收的不是一条纯字符串，而是一组 ACP `ContentBlock`。

它们可以包含：

- 文本；
- 图片；
- 客户端附带的上下文；
- Slash Command；
- Skill 引用；
- 不同来源的合成 Prompt。

因此“把用户文本 push 到 history”只描述了很小一部分工作。

---

## 6. Host-only Slash Command 不进入模型 Turn

部分内置 Slash Command 在 Host 内完成，不需要推理。

这条路径会：

1. 用 `persist_host_turn_user_echo` 保存用户输入；
2. 给事件加 `hostTurn` 元数据；
3. 执行本地命令并输出结果；
4. 在正常模型 Turn 建立之前返回。

所以 `updates.jsonl` 可以保留这次用户操作，但模型 history 不必伪造一个 User 消息。

这解释了为什么事件 replay 不能无条件把每个 `UserMessageChunk` 变成模型 User turn。

---

## 7. 真正的模型 Turn 先分配 promptIndex

进入模型 Turn 后，`handle_prompt`：

1. 读取当前 `prompt_index`；
2. 把它写入 ACP User chunk 的 `_meta.promptIndex`；
3. 调用 `increment_prompt_index()`；
4. 缓存裁剪后的 prompt 文本；
5. 让文件状态跟踪器以该 index 开始一次快照周期。

这里要区分两个值：

```text
current_prompt_index = 本轮的 0-based 标识
ChatState.prompt_index = 已开始的真实模型 Turn 数
```

例如第一轮写入的 marker 是 `0`，随后 actor 内计数变为 `1`。

---

## 8. 为什么 promptIndex 必须显式写进事件

旧格式只能靠“连续 User chunk 算一轮”推断边界，但现代运行时还会产生：

- 隐藏的 interjection；
- 合成 User correction；
- 取消后的相邻 Prompt；
- host-only turn；
- 没有 Assistant chunk 隔开的用户输入。

显式 `promptIndex` 把“哪一段代表真实用户轮次”变成可恢复事实，而不是脆弱启发式。

---

## 9. User Echo 的隐藏不等于不持久化

某些 PromptOrigin 不应显示在普通 scrollback 中。

实现使用：

```text
hideFromScrollback = true
```

而不是省略事件。

原因是 Rewind、Fork 和 Resume 仍需知道真实 Turn 边界。显示策略属于客户端投影，持久化策略属于恢复协议，二者不能绑死。

---

## 10. PromptOrigin 决定消息语义

同一条文本可能来自：

- 用户输入；
- Task 完成通知；
- Subagent 完成通知；
- Workflow 完成或通知排空；
- Goal summary；
- Goal classifier nudge；
- Scheduler；
- Plan resume。

Runtime 会按来源构造不同的 synthetic `ConversationItem::User`。这些消息可能对模型可见，但不等于真实用户主动开启的新轮次。

---

## 11. Prompt 解析后才形成 ConversationItem

`parse_prompt_with_skills` 把输入拆为：

- context；
- query；
- skill information；
- images；
- Cursor harness 标志。

随后 Runtime 再合并项目上下文、Skill 正文、图片规范化结果和来源信息，构造最终 `ConversationItem::User`。

也就是说：

```text
UI 输入文本
    → ACP blocks
    → 解析后的语义片段
    → 模型对话项
```

这三层数据不能假设完全相同。

---

## 12. 持久化确认有两个强度

`push_user_message_and_ack` 的 ack 表示 ChatStateActor 已处理命令。

当调用方还要求真正的磁盘屏障时，Session 层会继续发送 `PersistenceMsg::FlushAndAck` 并等待。

因此：

```text
actor ack ≠ 必然已 fsync/flush 到目标持久化边界
```

调用者必须明确自己需要的是“已线性化”还是“已耐久”。

---

# 第二部分：ChatState 怎样拥有对话

## 13. ChatStateActor 是单写者

`ChatStateHandle` 可以被多个异步任务 clone，但所有命令最终进入同一个 unbounded MPSC mailbox。

Actor 独占：

- conversation；
- sampling config；
- prompt index 与 prompt texts；
- token state；
- compaction marker；
- usage ledger；
- turn capture；
- credentials 与若干 turn metadata。

多个任务共享的是 Handle，不是可变 `Vec<ConversationItem>`。

---

## 14. Actor 模型提供的核心价值是线性化

假设工具执行、模型 stream、取消处理和持久化都能直接修改同一个 history，就会出现：

- Assistant tool call 已写入，ToolResult 尚未写入时被错误修复；
- Rewind 与后台 ToolResult 交叉；
- Compaction 替换 history 后旧任务继续追加；
- Token usage 对应不上消息快照。

Actor mailbox 让修改具有一个明确顺序。

---

## 15. ConversationItem 比 role/content 更丰富

核心变体包括：

| 变体 | 含义 |
| --- | --- |
| `System` | 系统身份、规则和持久动态上下文 |
| `User` | 真实或合成用户侧输入，可含图片和 prompt index |
| `Assistant` | 文本、Tool Calls、模型元数据 |
| `ToolResult` | 某个 Tool Call 的返回值 |
| `BackendToolCall` | Provider 托管工具的调用/摘要 |
| `Reasoning` | 独立 reasoning block 或加密内容 |

最终到 Chat Completions、Responses 或 Messages API 的角色格式，由 sampling-types 层转换。

---

## 16. history 必须满足 Tool Call 配对不变量

Assistant 的 Tool Call ID 必须有对应 ToolResult；重复 ToolResult 也不能无限保留。

不满足时 Provider 可能直接返回 400，而不是“模型表现差”。

`ChatState::new` 会：

- 去重 ToolResult；
- 为 dangling tool call 补出取消语义的结果。

这使崩溃或 Ctrl+C 留下的半轮对话仍能恢复。

---

## 17. 修复只能发生在安全写边界

`ensure_conversation_integrity` 明确不能放在任意读操作中。

安全边界包括：

- ChatState 初始化；
- 新 User 消息追加前；
- `BuildConversationRequest` 处理前。

原因是工具仍在运行时，暂时没有 ToolResult 是正常状态。若后台查询顺手“修复”，就会把 in-flight call 误判成 dangling call。

---

## 18. Push 的顺序是 persist 后 append

通用 `push_message` 先调用 persistence port，再把 item 放入内存 conversation。

从 Actor 的角度，它把“请求持久化”和“更新内存”放在同一个命令序列中；具体持久化实现再通过独立 actor/channel 完成文件操作。

关键不是把所有 I/O 同步化，而是让状态变更请求保持一致顺序。

---

## 19. Turn Capture 与 live conversation 是两种视图

测试/Trace 导出可能只想捕获本轮新产生的消息。

Turn capture 保存 conversation offset；但 Repair、Compaction、Restore 可能在 offset 之前插入、删除或替换项目。

因此 mutation 会：

1. 先 snapshot 当前 turn slice；
2. 修改 conversation；
3. rebase capture offset。

否则 trace 会漏消息或把上一轮内容算进本轮。

---

## 20. Harness Trace 不进入 live conversation

Goal planner、verifier 等 harness subagent 的合成 task pair 可以被单独保存为 trace turn，但不会送进主 Agent 的实时模型上下文。

这是另一种重要隔离：

```text
为了调试而记录
    ≠ 为了推理而注入
```

---

# 第三部分：下一次模型到底看见什么

## 21. Sampler 不直接读取 conversation

每轮 agentic loop 在采样前调用：

```text
chat_state_handle.build_request(...)
```

传入：

- 本轮有效 ToolSpec；
- Memory reminder；
- 是否持久化 Memory；
- TraceContext；
- conversation ID 与 request ID。

Actor 在处理 `BuildConversationRequest` 前先修复 history，再返回完整 `ConversationRequest`。

---

## 22. Request Builder 的真实步骤

当前实现可归纳为：

1. 判断是否需要 Tool Result pruning；
2. 必要时把 Memory reminder 持久注入 live System；
3. 测量 conversation 精确 JSON body 大小；
4. 达到高水位时，在请求副本驱逐旧图片；
5. 超过 50% context 时，在请求副本裁剪旧 Tool Result；
6. 必要时仅在请求副本注入 Memory；
7. 组装模型、采样参数、tools、trace 和 request IDs。

---

## 23. live history 与 request copy 必须分开看

多数请求优化只作用于 clone：

- 图片驱逐：request copy；
- soft trim Tool Result：request copy；
- 非持久 Memory reminder：request copy。

但以下会改 live history：

- 持久 Memory 注入；
- 很旧 Tool Result 的 retained hard clear；
- Repair；
- Compaction；
- Rewind。

调试“磁盘上明明有，模型为什么没看到”时，首先检查是否是 request-copy transform。

---

## 24. Tool Result pruning 是局部降级

当 `total_tokens > context_window / 2` 时，Builder 从尾部倒数 User 边界：

- 最近若干轮完全保留；
- 中等年龄的大结果保留 head 与 tail，中间插入 trimmed marker；
- 足够旧的结果替换为 omitted placeholder。

它优先牺牲可再获取、体积大、年龄高的 Tool 输出，而不是马上总结整段历史。

---

## 25. Retained hard clear 是内存管理

每次新增 User 消息后，`prune_retained_conversation` 还会检查极老 ToolResult。

它与请求时 pruning 的区别：

| 机制 | 修改对象 | 触发 | 目的 |
| --- | --- | --- | --- |
| request pruning | 请求 clone | context 超过 50% | 降低模型输入 |
| retained hard clear | live conversation + chat history | 每个真实 User turn 后检查年龄 | 回收长期内存占用 |

它只 hard clear，不做 soft trim。

---

## 26. Synthetic User 会干扰“轮次年龄”

Tool pruning 倒序数 User item，但 synthetic User 并不增加真实 `prompt_index`。

retained pruning 用：

```text
synthetic_count = total_user_items - prompt_index
effective_threshold = hard_clear_age_turns + synthetic_count
```

补偿这种偏差，避免合成提醒让旧 ToolResult 被过早删除。

---

## 27. 图片首先受 HTTP Body 上限约束

图片是 base64 `data:` URL，可能尚未耗尽 Token Window，就先撞上代理 50 MiB request body 限制。

Builder 在约 47 MiB 触发，驱逐到约 25 MiB 的低水位。

这是经典 hysteresis：高水位触发、低水位回收，避免每新增一张图片就重写一次前缀。

---

## 28. 图片 placeholder 必须诚实

被驱逐的图片会替换为明确说明：图片已不可见，如需要请用户重传；不要凭记忆描述。

这不只是 UX 文案，而是幻觉防线。若静默移除图片，模型可能把旧 attention 印象当作仍可观察事实。

---

## 29. Body 测量为什么不直接 serde 到 Vec

完整编码几十 MiB base64 会产生大分配并反复扫描。

实现：

- 用 `ByteCounter` 接收 `serde_json::to_writer`；
- clone 时把 image URL 暂时置空；
- 精确序列化其余结构；
- 再加回 base64 URL 原始字节数。

base64 不含需要 JSON escape 的字符，因此贡献可以直接按长度补回。

---

## 30. Memory 注入有持久与临时两种模式

若 `persist_memory_reminder` 为 true，Builder 会把 Memory 写进 live System head，并 replace history；否则只进入本次请求 clone。

持久注入还必须 snapshot/rebase turn capture，因为在头部插入内容会移动所有 index。

---

## 31. Tool 定义也占上下文

`ConversationRequest.items` 不是全部输入成本。Tool name、description 和 JSON schema 同样占 Token，也占 HTTP body。

本地估算按：

```text
name bytes + description bytes + serialized parameters bytes
------------------------------------------------------------
                              4
```

粗略计算 Tool Definition Token。

---

## 32. Request 返回后 Session 仍会补字段

`turn.rs` 在取得 Builder 结果后继续设置：

- session ID；
- turn index；
- agent ID；
- deployment ID；
- native structured-output schema；
- hosted tools；
- task output token clamp。

所以 ChatState Builder 是核心组装点，但不是最终 wire request 的唯一修改点。

---

# 第四部分：Token 状态与自动压缩

## 33. total_tokens 不是实时逐字计数

Provider 返回的 Usage 是上一次模型响应时的权威锚点。

响应之后又可能追加：

- ToolResult；
- synthetic User correction；
- 下一条真实 User 输入。

因此当前估算为：

```text
estimated current total
    = provider-reported total_tokens
    + estimated_tokens_since_model
```

---

## 34. 为什么 Assistant 不加入增量估算

Assistant 内容通常伴随 Provider Usage 返回；若本地再加一次，就可能重复计数。

因此通用 push 对非 Assistant item 增加 `estimated_tokens_since_model`，随后 `record_token_usage` 用 Provider total 重新锚定并把 delta 清零。

---

## 35. Token 估算的粒度

当前本地估算大体是 bytes / 4，并针对类型展开：

- System：content；
- User：文本字节 + 每张图片固定估值；
- Assistant：正文 + Tool Call arguments；
- ToolResult：content；
- BackendToolCall：text summary；
- Reasoning：明文与 encrypted content。

它不是 tokenizer 的精确替代，而是 Provider 响应之间的保守控制信号。

---

## 36. 自动压缩不只有一个触发点

源码中至少包括：

- 采样前达到配置阈值；
- Tool Result 追加后的 preflight 超出 window；
- Provider 报告实际窗口更小；
- 切换到 context window 更小的模型；
- 用户手动 `/compact`；
- debug 强制触发。

多触发点是必要的，因为 context 可以在模型调用前、调用后或配置变化时突然失配。

---

## 37. Pruning 与 Compaction 不同

```text
Pruning
  └── 局部删减旧 ToolResult/图片，尽量保持前缀结构

Compaction
  └── 让模型总结旧历史，再以新 conversation 整体替换
```

前者是局部、确定性、便宜的降级；后者是语义压缩、需要额外推理且可能失败。

---

## 38. 自动压缩有抑制状态机

压缩失败后不能每次循环都重试同一个确定性错误。

Runtime 区分：

- 只抑制当前 Turn；
- 对当前 context size/schema 持续抑制；
- 信用恢复前抑制；
- 认证恢复前抑制。

手动 `/compact` 不受普通自动抑制影响，因为它表达了新的用户意图。

---

## 39. Compaction 也必须可取消

完整替换压缩会产生额外模型请求，可能持续较久。

Runtime 使用共享 cancel gate 和 RAII guard，使 Ctrl+C/Stop 能终止正在 stream 的压缩，而不是等摘要完成后才响应。

---

## 40. 压缩输入有 Verbatim 与 Lossy

Verbatim 尽量保留：

- Tool I/O；
- 图片；
- 完整消息关系。

Lossy 会进一步：

- 删除 ToolResult；
- 把 Tool Call 名称扁平化为文本；
- 去掉 Reasoning 和图片。

降级顺序体现一个原则：先保语义结构，只有在输入仍过大时才牺牲细节。

---

## 41. Overflow 的降级梯子

压缩请求自身也可能超过窗口，因此实现按层退让：

```text
Verbatim
  → VerbatimFitted：删除最老的完整 turn/tool-run unit
  → Lossy：更激进地去掉高体积细节
  → 确定性 size suppression
```

删除必须尽量按完整 turn 或 Tool Call/Result 单元进行，不能留下新的协议残片。

---

## 42. 为什么要预留摘要输出预算

若压缩输入把 context window 吃满，模型没有空间生成 summary。

实现为 summary 留出固定的大块输出余量，再对输入做 fitted/lossy 处理。压缩的目标不是“请求能发出去”，而是“请求还能产出足够完整的可继续上下文”。

---

## 43. 两阶段压缩解决尾延迟

达到正式阈值前约 10 个百分点，Runtime 可以后台对稳定前缀执行 pass 1，得到 `NOTE1`。

正式压缩时 pass 2 只需处理：

```text
NOTE1 + 自 pass1 之后的新尾部
```

这样把一部分摘要成本提前到用户仍在正常操作时完成。

---

## 44. pass1 cache 不能只按长度验证

缓存记录：

- prefix length；
- exact prefix fingerprint；
- model slug；
- summary；
- latency。

正式使用前必须同时确认：

- 当前 conversation 足够长；
- 模型未变化；
- 同长度前缀 fingerprint 完全一致。

仅检查长度会把 Rewind 后不同内容的前缀误当成同一历史。

---

## 45. Compaction 不是只留下 System + Summary

重建历史还可能包含：

- System head；
- 原始 user info/preamble；
- AGENTS reminder；
- 当前任务、subagent、todo、MCP、skills、memory 状态；
- summary；
- Plan Mode reminder；
- transcript/segment pointer；
- 必要的 recent messages。

压缩是在构造一个“能继续工作的新初始状态”，不是写会议纪要。

---

## 46. 新 history 写入前还要协议清洗

Compaction 输出可能与保留的 recent messages 组合出孤立 ToolResult。

Runtime 会 sanitize；若仍违反协议，则退到不带 recent messages 的最小历史。

正确性优先于多保留几条细节。

---

## 47. Checkpoint 必须先于 live history 替换

顺序是：

1. 生成新的 compacted history；
2. 写 checkpoint 文件；
3. 在 `updates.jsonl` 追加 checkpoint marker；
4. 再 replace live conversation / `chat_history.jsonl`。

这样一旦旧细节从当前 history 消失，恢复路径已经有证据知道如何跨过压缩边界。

---

## 48. CompactionCheckpoint 保存什么

Schema v1 的核心包括：

- compacted history；
- `prompt_index_at_compaction`；
- created timestamp；
- original user info；
- auto-continue metadata。

它不是完整 Session snapshot，但足以让 replay 在压缩边界两侧选择正确起点。

---

## 49. Summary、Transcript 与 Segments 模式

| 模式 | 行为 |
| --- | --- |
| Summary | 只在 live context 中保留摘要 |
| Transcript | 摘要提示原始 `updates.jsonl` transcript 可用 |
| Segments | 把压缩前详细内容写入 `compaction/segment_N.md` 与索引 |

Segments 让模型上下文保持紧凑，同时给人和后续工具保留更详细档案。

---

# 第五部分：Rewind 怎样跨过压缩边界

## 50. Rewind 的语义是回到 Prompt N 之前

`target_prompt_index = N` 表示保留 prompts `0..N-1`。

它不是“数组截断到第 N 个 item”，因为一个 Turn 可以包含多个 User/Assistant/Reasoning/Tool 项。

---

## 51. Rewind 有三个域

- `All`：对话与文件都恢复；
- `ConversationOnly`：只改变对话；
- `FilesOnly`：只改变文件。

预览模式 `force=false` 先检测外部文件冲突，不修改任何状态。

---

## 52. 没有 Compaction 时可以结构化截断

简单路径用 `conversation_truncate_for_prompt` 找到 Turn 边界，并保留 System/preamble。

它仍不是直接 `Vec::truncate(target_prompt_index)`，而是识别哪些 User item 真正构成 prompt boundary。

---

## 53. 有过 Compaction 后必须完整 Replay

一旦当前历史中存在 compaction marker，任何目标都走 `replay_to_prompt`。

即使目标看起来在最近摘要之后，也不能仅靠当前 User 数量判断，因为摘要已经把多个旧 Turn 合并成少数 item。

---

## 54. Replay 为什么必须扫描完整 append log

`updates.jsonl` 可能包含：

```text
turn 0
turn 1
turn 2
rewind_marker(target=1)
new turn 1
```

后面的 marker 会让逻辑时间倒退。因此不能看到 prompt counter 达到目标就提前停止；后续记录可能声明前面的分支已死亡。

---

## 55. ReplayState 只重建必要消息

跨压缩 conversation replay 主要消费：

- ACP UserMessageChunk；
- ACP AgentMessageChunk；
- CompactionCheckpoint；
- RewindMarker。

Tool 状态、UI 状态等事件不是全部都要转成模型消息。这里重建的是模型可继续对话，不是完整 UI。

---

## 56. User chunks 需要合并成 run

一个用户消息可能拆成多个 ACP chunks。

ReplayState 持有 pending user buffer；连续 chunk 合并，边界变化时 flush 成一个 `ConversationItem::User`。

带 `promptIndex` 的 run 是权威真实 Turn。兼容旧数据时，在第一次看到 marker 前仍会按旧式 User run 计数。

---

## 57. Progressive compatibility 的意义

Session 可能跨版本运行，早期事件没有 `promptIndex`，后期事件有。

规则是：

- 第一个 marker 出现前，unmarked User run 可计为 Turn；
- marker 出现后，只有 marked run 计为真实 Turn；
- unmarked phantom 仍可保留为消息，但不推进 prompt counter。

这允许一份 JSONL 同时容纳旧格式与新格式。

---

## 58. 目标位于 Checkpoint 之前

Replay 从原始 updates 重建目标之前的 raw turns。

但仍可能读取 checkpoint 的 `original_user_info`，以恢复压缩前的稳定 preamble；随后用当前 System head + original user info + replayed turns 组成历史。

---

## 59. 目标位于 Checkpoint 之后

Replay 加载 checkpoint 中的 compacted history，把它当作 base，再处理 checkpoint 后存活的 User/Agent chunks。

此时 checkpoint 内部 User item 不应再次计作 checkpoint 后的新 Turn。

---

## 60. Checkpoint 损坏时必须 fail closed

若 marker 指向的 checkpoint：

- 丢失；
- JSON 损坏；
- schema 不支持；
- 内容无法安全使用；

Rewind 会中止，而不是退回对当前 compacted array 做猜测性截断。

错误回退可能让 UI 看似成功，但模型历史已经落在错误时间点。

---

## 61. Rewind 提交后仍向 append log 写 Marker

`updates.jsonl` 不删除旧分支，而是追加：

```text
RewindMarker { target_prompt_index }
```

这样旧事件仍可审计，最新逻辑分支也能通过 replay 计算出来。

这是 event log 与 mutable cache 的典型分工：

```text
updates.jsonl       保存发生过什么
chat_history.jsonl  保存现在模型从哪里继续
```

---

## 62. ConversationOnly 也必须处理文件快照账本

只回退对话时，磁盘文件保持当前样子。但未来一次完整 Rewind 仍应能撤销这些文件影响。

因此被移除 Turn 的 file effects 会合并到前一个存活点，而不是简单丢掉文件快照元数据。

---

## 63. Edit-and-retry 怎样识别

Rewind 会缓存目标 prompt 的文本到 `rewind_pending_prompt`。

下一条用户输入：

- 与原文相同：可视为 regeneration；
- 与原文不同：可视为 edit and retry。

这是用户意图层元数据，不需要把两种操作编码成不同的 ConversationItem。

---

## 64. Rewind 后压缩抑制状态的源码细节

当前实现除 `SUPPRESS_UNTIL_SUCCESS` 外，会清除其他 compaction suppression。

附近注释仍把 account-state suppression 描述得更宽，但代码条件只显式保留 `SUPPRESS_UNTIL_SUCCESS`。阅读和修改这里应以实际条件为准，并把注释漂移视为待校准风险。

---

# 第六部分：Resume 与 Client Replay

## 65. Resume 不是把 JSONL 全部塞回模型

Session 启动时，模型状态主要从 `chat_history.jsonl` 恢复。

同时还会扫描 `updates.jsonl` 获取：

- 最近 compaction checkpoint index；
- user prompt texts；
- UI replay events；
- token/event metadata；
- 未完成后台任务等运行时线索。

不同恢复目标使用不同 loader。

---

## 66. prompt_texts 为什么从 updates 恢复

Compaction 或 hard clear 后，当前 chat history 不一定保留原始用户输入的完整时间线。

`load_user_prompts_from_updates` 用选择性 iterator 扫描 User chunks 和 RewindMarker，恢复当前存活分支上的 prompt 文本，供 rewind picker 和 edit-and-retry 使用。

---

## 67. UI Replay 有独立 Replay Gate

Load/Resume 时不能一边回放旧事件，一边让新 Session 输出穿插进客户端。

高层顺序是：

1. 关闭 live-output gate；
2. 从 `updates.jsonl` 准备并转发存活事件；
3. 等待这些转发完成；
4. flush Session，抓取读取期间新增的 delta；
5. 打开 gate；
6. 排空 delta completion。

这保证客户端观察到“旧历史在前，新输出在后”。

---

## 68. replayed event 会带 isReplay

完整恢复时，转发给客户端的旧通知会标记 `_meta.isReplay = true`。

Cursor-based reconnect 若成功定位到客户端已见位置，则后面的事件是真正未见 delta，不必假装成全量 replay。

---

## 69. noReplay 不等于什么都不恢复

客户端可请求跳过 UI transcript replay，但 Runtime 仍要：

- 恢复模型 conversation；
- 获取初始 token 信息；
- 协调 stale task；
- 打开 gateway；
- 保持 Session 可继续。

`noReplay` 控制的是客户端展示成本，不是 Agent 状态恢复。

---

## 70. chat_history.jsonl 是可替换的派生缓存

源码甚至提供从 durable updates 重建 derived chat history 的 repair 路径。

但这不表示日常每次 Resume 都应从全部 updates 重算：当前 history 是高效启动点，updates/checkpoint 是恢复、审计和跨边界 replay 的依据。

---

# 第七部分：Fork 的两种含义

## 71. Session Fork 是磁盘级分支

`fork_session`：

- 接收 source session/cwd；
- 创建或接受新 UUIDv7 session ID；
- 复制 Session 数据到新目录；
- 可切换 model 和 cwd；
- 可裁到某个 prompt index；
- 返回后并不会自动启动新 Session。

后端 upsert 是异步 telemetry-grade 操作，本地复制成功不依赖网络注册完成。

---

## 72. 为什么 Fork copy 放到 spawn_blocking

JSONL 和 sidecar 复制使用同步文件 I/O。

Session 运行在 `LocalSet` 时，直接做同步大文件复制会阻塞单线程 executor，使并发 Fork 实际串行。`spawn_blocking` 把它移到 blocking pool。

---

## 73. chat history 的 Fork 裁剪

若指定 `target_prompt_index`，copy 使用：

```text
conversation_truncate_for_prompt(target + 1)
```

这里 API 的 target 是 inclusive：Fork 保留目标 Prompt 本身；这与 Rewind “回到 N 之前”的语义不同，不能共享未经转换的数字理解。

---

## 74. updates.jsonl 的 Fork 是两遍流式算法

有目标 cut 时：

1. 第一遍只记录每个非空行的 index 与 `RewindStep` 分类；
2. 应用 Rewind marker 过滤和 prompt cut；
3. rewind 同一个已打开文件句柄；
4. 第二遍只复制 surviving line。

没有 target 时则单遍流式复制，保留 dead branch 与 marker。

---

## 75. 为什么不把巨大 updates 全读入内存

`updates.jsonl` 无界增长。Fork copy 的峰值内存应接近：

- 一条有大小上限的 line buffer；
- cut 模式下每行一个小 classification record。

实现还限制单行最大 64 MiB，超长或 torn line 会跳过并告警，避免损坏日志制造无限分配。

---

## 76. Fork 会重写哪些内容

标准 Session Fork：

- 重写 update 中的 session ID；
- 非 worktree 且 cwd 改变时，转换 conversation 中的 cwd；
- 更新 summary 中 parent、model、session kind 等；
- 复制 plan、signals、plan-mode、tool、announcement sidecars；
- 复制 surviving checkpoint files；
- 可复制 compaction segment archive。

Workflow/Goal projection 会被过滤，避免复制旧编排状态造成幽灵任务。

---

## 77. Checkpoint copy 必须跟 surviving markers 对齐

复制 updates 时同时收集保留下来的 `CompactionCheckpoint` 引用。

只复制这些 marker 实际引用的 checkpoint 文件，防止：

- 新分支缺少必要 checkpoint；
- 无关旧 checkpoint 无限膨胀；
- target cut 后仍带入未来历史。

---

## 78. subagent fork_context 是上下文继承

subagent `fork_context=true` 的目标不是复制一份供用户 Resume 的完整时间线，而是给 child Agent 一个安全的父上下文起点。

它会：

- 从 live parent conversation 或 parent session 获取上下文；
- 做 fork-safety filter；
- 去掉尾部不完整响应与 synthetic 噪声；
- 可 strip reasoning；
- 记录 `inherited_prefix_len`；
- 通常让 child updates transcript 从空开始。

---

## 79. inherited_prefix_len 是 Compaction 边界

Child 后续压缩时，需要知道哪一段是从父 Agent 继承的稳定前缀。

`inherited_prefix_len` 告诉 compaction：这部分不是 child 自己逐轮产生的普通历史，应按 fork 继承契约处理。

若插入新的 project instructions，代码还必须同步调整这个长度，否则边界会错一位。

---

## 80. Verbatim mirror fork 会保留 inherited System

普通 child spawn 可能用 child persona/system 替换 leading System。

但 verbatim mirror fork 设置 `preserve_inherited_system`，避免：

- 覆盖父 Agent 的 System head；
- 重复插入 AGENTS/project instructions；
- 破坏前缀字节稳定性。

这是一种明确的“镜像父推理环境”语义。

---

## 81. Fork 默认不应继承 Chain-of-Thought

`CopySessionOptions.strip_reasoning` 用于去掉 reasoning blocks/内容。

Child 应继承完成任务所需事实、工具结果和上下文，而不是把父模型私有推理轨迹当作新的事实输入。

---

## 82. 两种 Fork 对照

| 维度 | Session Fork | subagent `fork_context` |
| --- | --- | --- |
| 面向对象 | 用户可恢复的新会话 | 子 Agent 初始推理上下文 |
| 主要载体 | 文件复制 | filtered inherited conversation |
| updates | 保留/裁剪并重写 session ID | bootstrap copy 通常清空 |
| sidecars | 多种状态一起复制 | 按 child 语义选择 |
| history cut | 可按 prompt target | 截到安全完整边界 |
| reasoning | 标准 fork 可按 options | 通常剥离 |
| 启动 | 复制后不自动启动 | 作为 child spawn 的一部分 |

---

# 第八部分：关键不变量与修改指南

## 83. 不变量一：模型 history 必须协议合法

始终保证：

- Tool Call/Result 配对；
- 不重复消费 ToolResult；
- trailing incomplete response 被过滤或修复；
- Provider 协议转换后角色序列合法。

任何 History transform 都要验证这个不变量。

---

## 84. 不变量二：真实 Turn 与 synthetic User 分离

需要同时维护：

- `prompt_index`；
- User item 数量；
- event 中的 `promptIndex` marker；
- hostTurn 和 hidden echo 元数据。

不要用“数 User message”替代正式 Turn 语义。

---

## 85. 不变量三：Append log 不物理删除分支

Rewind 通过 Marker 表示；Fork cut 通过逻辑过滤产生新文件。

原 Session 的 `updates.jsonl` 保留发生过的事实，当前模型 cache 可以被替换。

---

## 86. 不变量四：先落恢复依据，再删除当前细节

Compaction 必须先 checkpoint/marker，后 replace history。

任何颠倒都会制造崩溃窗口：旧细节已丢，新恢复依据还不存在。

---

## 87. 不变量五：Replay 与 live output 有顺序屏障

Resume 客户端必须先收到存活旧事件，再收到新事件。

仅仅“都发送成功”不够；观察顺序属于协议正确性。

---

## 88. 不变量六：Request transform 的持久性必须显式

新增裁剪策略时先回答：

```text
只改变这一次模型请求？
还是要改变 live state？
是否同步 replace chat_history？
updates 是否仍保留原始内容？
Rewind 能否恢复？
```

若这些问题没有答案，Context bug 往往只会在几小时后的 Resume/Rewind 中暴露。

---

## 89. 新增 ConversationItem 变体要改哪些地方

至少审计：

- Token estimation；
- JSON serialization compatibility；
- 三种 Provider 转换；
- Compaction verbatim/lossy transform；
- request body measurement；
- cwd transform；
- fork filter；
- replay reconstruction；
- trace/export/redaction；
- tests 与 downgrade 工具。

新增 enum variant 只是编译工作的开始。

---

## 90. 修改 Compaction 时的测试矩阵

至少覆盖：

- 正常 single pass；
- pass1 exact fingerprint 命中；
- 同长度不同内容导致缓存失效；
- model switch；
- overflow 各级降级；
- Ctrl+C cancel；
- checkpoint 写入失败；
- replacement 后 Tool protocol integrity；
- compact 后 Rewind 到 checkpoint 前后；
- Fork 后 checkpoint/segment 仍可解析。

---

## 91. 修改 Replay 时的测试矩阵

至少覆盖：

- 多 chunk User 合并；
- back-to-back marked prompts；
- 新旧 marker 混合；
- phantom User 不计数；
- 多次 RewindMarker；
- checkpoint 前/后 target；
- missing/corrupt checkpoint；
- hostTurn 排除；
- malformed/torn JSONL；
- replay 期间产生 delta 的顺序。

---

## 92. 修改 Fork 时的测试矩阵

至少覆盖：

- 无 target 的全量流式复制；
- target inclusive cut；
- source 已有 RewindMarker；
- marker 引用 checkpoint 的选择性复制；
- cwd rewrite 与 worktree skip；
- session ID 重写；
- sidecar 开关；
- 64 MiB 超长行；
- torn tail；
- subagent fork_filter 与 reasoning strip；
- inherited prefix 在后续 compaction 中保持。

---

# 第九部分：逐步调试手册

## 93. 模型没看到用户刚输入的内容

按顺序检查：

1. `handle_prompt` 是否进入真正模型 Turn，而不是 host-only slash；
2. User echo 是否带正确 `promptIndex`；
3. parsed query/context 是否为空或被命令改写；
4. `push_user_message_and_ack` 是否成功；
5. ChatState actor 是否仍存活；
6. `build_request.items` 是否含该 User；
7. Provider adapter 是否正确转换该 item。

---

## 94. UI 有消息但模型历史没有

这可能完全正常：

- hostTurn；
- UI-only tool/status event；
- replayed notification；
- 已被 Compaction 总结的旧消息；
- request-copy pruning；
- subagent harness trace。

先确认该事件是否本来就属于模型 conversation。

---

## 95. 模型看到消息但 Resume 后 UI 不显示

重点检查：

- `updates.jsonl` 是否写入对应 ACP chunk；
- Persistence flush barrier；
- replay filtering 是否错误判为 dead branch；
- cursor 是否定位错误；
- gateway replay gate 是否过早打开；
- `isReplay`/target client routing。

不要只看 `chat_history.jsonl`，它不是 UI transcript。

---

## 96. Rewind 后模型上下文错位

重点检查：

- target 是 before semantics 还是 inclusive fork semantics；
- 是否存在 compaction marker；
- promptIndex marker 是否连续；
- synthetic User 是否被误计数；
- checkpoint base length；
- RewindMarker 是否处理到文件末尾；
- replace 后 prompt index/texts/compaction marker 是否一起恢复。

---

## 97. Compaction 反复触发

检查：

- replacement 后 token reseed；
- summary 是否仍超过 threshold；
- inherited fork prefix 是否无法释放；
- suppression 是否设置并在何事件清除；
- context window 是否在 model metadata 更新后变小；
- Provider usage 与 local delta 是否重复计数。

---

## 98. Fork 后出现未来事件

检查：

- chat cut 是否使用 `target + 1` inclusive 语义；
- updates 第一遍是否先应用 Rewind filter；
- surviving line indexes 与第二遍是否基于同一 pinned handle；
- checkpoint files 是否只来自 surviving marker；
- sidecar 是否包含不应继承的编排投影。

---

# 第十部分：学习实验

## 99. 实验一：画出一轮消息的双写

选一条普通 Prompt，记录：

1. `updates.jsonl` 中 UserMessageChunk；
2. `chat_history.jsonl` 中 User item；
3. 两者的 prompt index；
4. 模型回答后两份文件新增内容。

然后执行 host-only slash，对比它为什么只形成事件边界。

---

## 100. 实验二：观察 request-copy pruning

构造多个大 ToolResult，使 context 超过 50%。

验证：

- `build_request` 返回的旧结果被 trim；
- live conversation 是否仍保留原内容；
- 达到 retained hard-clear 年龄后，live history 才真正替换内容。

---

## 101. 实验三：制造 pass1 stale cache

准备长度相同但内容不同的前缀，模拟 Rewind 后再增长到同样 item count。

断言 pass1 cache 因 fingerprint 不同被拒绝，而不是仅凭 prefix length 命中。

---

## 102. 实验四：跨 Checkpoint Rewind

构造：

```text
prompt 0 → prompt 1 → compact at 2 → prompt 2 → prompt 3
```

分别 rewind 到 1、2、3，观察：

- raw replay；
- checkpoint base；
- tail replay；
- prompt counter；
- last compaction marker。

---

## 103. 实验五：对比两个 Fork

对同一父 Session：

1. 执行用户 Session Fork；
2. 启动 `fork_context=true` subagent。

比较：

- child `chat_history.jsonl`；
- child `updates.jsonl`；
- summary parent metadata；
- reasoning blocks；
- inherited prefix；
- sidecars。

---

# 第十一部分：完整心智模型

## 104. 一条消息的一生

```text
输入层
  ACP blocks + PromptOrigin
        │
        ├── 客户端事件投影：UserMessageChunk + promptIndex
        │
        └── 模型语义投影：ConversationItem::User
                              │
状态层                         ▼
  ChatStateActor: repair → persist → append → token delta
                              │
请求层                         ▼
  clone → image budget → tool pruning → memory → tools
                              │
模型层                         ▼
  protocol conversion → stream → assistant/tool/reasoning/usage
                              │
长期演进                       ▼
  local pruning → compaction checkpoint → replacement
                              │
时间操作                       ▼
  rewind = replay event log + checkpoint
  resume = model restore + client replay + runtime reconcile
  fork = copy/cut persisted branch OR inherit safe subagent prefix
```

---

## 105. 最值得记住的设计判断

1. Context 不是字符串，而是一组有协议约束、有来源、有寿命的 typed items。
2. ChatStateActor 的核心职责不是“保存 Vec”，而是线性化所有 history mutation。
3. `chat_history.jsonl` 服务快速模型恢复，`updates.jsonl` 服务事件时间线与跨边界重建。
4. UI 隐藏、模型排除和磁盘不记录是三种完全不同的行为。
5. Token pruning、HTTP body image eviction 与 semantic compaction 解决三个不同资源问题。
6. Compaction 只有和 checkpoint、replay、rewind 一起设计才是完整功能。
7. Rewind 用 Marker 建立新逻辑分支，不篡改发生过的事件。
8. Resume 必须分别恢复模型、客户端和运行时辅助状态。
9. Session Fork 与 subagent context fork 共享“分支”直觉，但数据契约不同。
10. 任何 Context 优化都必须回答：模型现在看到什么、磁盘保存什么、未来如何恢复。

---

## 106. 下一篇建议

下一篇适合继续做：

**源码精读 29：Sampling Protocol Runtime——ConversationRequest 如何转换为 Responses、Chat Completions 与 Messages，请求怎样流式组装 Reasoning、Tool Call、Usage 和终态，又怎样处理 Cache、Retry、413 与协议差异。**

这样可以从本篇的 `ConversationRequest` 继续向下，一直追到 Provider wire format 和 stream accumulator。

---

## 107. Glossary（术语表）

| 名词 | 白话解释 | 本文中的具体含义 |
| --- | --- | --- |
| Context | 模型本次推理能看到的输入 | Conversation items、Tool schemas、动态提醒等共同组成的上下文 |
| Context lifecycle | 上下文生命周期 | 从输入、追加、请求组装，到裁剪、压缩、回退、恢复和分叉的全过程 |
| Context window | 上下文窗口 | 模型一次请求可容纳的最大 Token 范围 |
| Turn | 一轮 | 一个真实模型 Prompt 开始，到回答/工具循环收敛的逻辑单位 |
| prompt index | Prompt 序号 | 真实模型 Turn 的 0-based 边界标识；actor 中计数通常表示已开始轮数 |
| PromptOrigin | Prompt 来源 | 区分用户、Scheduler、Goal、Subagent、Workflow 等触发者 |
| ContentBlock | 内容块 | ACP 输入/输出的文本、图片等协议单元 |
| User echo | 用户输入回显 | 写给客户端时间线的 UserMessageChunk，不必等同模型 User item |
| host turn | Host 轮次 | 由本地命令直接处理、不进入模型推理的用户操作 |
| scrollback | 回滚显示历史 | 终端/客户端给用户看的消息时间线 |
| synthetic User | 合成 User 消息 | Runtime 以 User 角色送给模型的提醒或内部结果，不代表真实用户新开一轮 |
| ConversationItem | 对话项 | 内部统一的 System/User/Assistant/ToolResult/Reasoning 等 typed enum |
| ChatState | 聊天状态 | 当前模型对话、token、prompt index、usage 等的聚合状态 |
| Actor | Actor 并发模型 | 一个任务独占可变状态，其他任务通过消息命令访问 |
| mailbox | Actor 邮箱 | 接收 `ChatStateCommand` 的 MPSC channel |
| linearization | 线性化 | 把并发操作解释为一个确定的先后顺序 |
| snapshot | 快照 | 某一时刻状态的可恢复副本 |
| turn capture | 本轮捕获 | 为 trace/export 单独记录本轮新增消息的机制 |
| harness | 测试/评估驱动器 | Planner、verifier 或测试系统调用子 Agent 的运行环境 |
| ConversationRequest | 对话请求 | ChatState 组装出的 Provider 无关模型请求结构 |
| ToolSpec | 工具规格 | 工具名称、描述和 JSON 参数 Schema |
| request copy | 请求副本 | 从 live conversation clone 出、只为本次模型调用转换的 items |
| pruning | 裁剪 | 确定性缩短旧 ToolResult 等高体积内容 |
| soft trim | 软裁剪 | 保留结果头尾，删除中间大段内容 |
| hard clear | 硬清除 | 用固定 placeholder 替换整个旧结果 |
| retained conversation | 保留对话 | ChatState 中长期存在、并映射到 chat history 的 live history |
| inline image | 内联图片 | 以 base64 data URL 直接放入请求的图片 |
| placeholder | 占位文本 | 内容被删除后明确告诉模型发生了什么的替代项 |
| hysteresis | 滞回/高低水位 | 高阈值触发、一次回收到更低阈值，避免频繁重复操作 |
| Token estimate | Token 估算 | Provider Usage 之间用 bytes/4 等规则近似新增成本 |
| usage anchor | Usage 锚点 | Provider 返回的实际 total token 数，重新校准本地估算 |
| Compaction | 上下文压缩 | 用模型总结旧历史并整体重建 conversation |
| full replacement | 完整替换 | 用新 items 原子替代当前 history，而非继续 append |
| Verbatim | 逐项保真模式 | 压缩输入尽量保留工具关系、图片和原始结构 |
| Lossy | 有损模式 | 为满足预算删除 ToolResult、Reasoning、图片等细节 |
| fitted input | 拟合后输入 | 按完整 Turn/Tool unit 删除旧内容，使压缩请求装入窗口 |
| prefire | 提前触发 | 正式阈值前后台启动两阶段压缩的 pass 1 |
| fingerprint | 指纹 | 对精确前缀内容计算的身份标识，用于拒绝 stale cache |
| checkpoint | 检查点 | 保存压缩后 history 与边界元数据、供未来 replay 使用的文件 |
| marker | 标记事件 | 在 append log 中表达 compaction 或 rewind 逻辑边界的记录 |
| transcript | 事件实录 | `updates.jsonl` 中可重放的 Session 更新序列 |
| segment | 压缩档案片段 | Compaction 前详细内容保存成的 Markdown 文件 |
| Rewind | 回退 | 把对话和/或文件恢复到某个 Prompt 之前 |
| replay | 重放 | 顺序解释事件日志，重建逻辑状态或重新呈现给客户端 |
| dead branch | 死分支 | 被后续 RewindMarker 排除、历史上发生过但不再属于当前时间线的事件 |
| progressive compatibility | 渐进兼容 | 一份日志从无 marker 老格式平滑过渡到有 marker 新格式的计数规则 |
| Resume | 恢复会话 | 从持久化文件重新建立模型状态、客户端时间线与运行态 |
| Replay Gate | 回放闸门 | Resume 时阻止新输出穿插到旧事件之前的顺序屏障 |
| delta replay | 增量回放 | 全量读取期间产生的新事件，在 gate 打开前后有序补发 |
| cursor | 游标 | 客户端声明自己已看到的位置，用于跳过旧 replay |
| sidecar | 旁路状态文件 | plan、tool、signals 等不放在 chat history 中的独立持久化文件 |
| Fork | 分叉 | 从已有 Session/Context 创建一条独立后续分支 |
| Session Fork | 会话分叉 | 复制和裁剪持久化文件，形成用户可加载的新 Session |
| fork_context | 上下文分叉 | subagent 继承父 Agent 过滤后对话前缀的启动方式 |
| inherited prefix | 继承前缀 | child conversation 中来自 parent 的稳定部分 |
| fork filter | 分叉过滤 | 去掉 synthetic 噪声、不完整尾部等不宜继承内容的转换 |
| strip reasoning | 移除推理轨迹 | Fork 时去掉 reasoning/chain-of-thought，只继承任务事实 |
| fail closed | 失败时拒绝继续 | checkpoint 不可信时中止 Rewind，而不是猜测性恢复 |
| durability barrier | 耐久屏障 | 调用返回时确保此前持久化消息已跨过指定 flush 边界 |
| protocol repair | 协议修复 | 补齐 dangling Tool Call、去重 ToolResult，使 Provider 可接受历史 |

