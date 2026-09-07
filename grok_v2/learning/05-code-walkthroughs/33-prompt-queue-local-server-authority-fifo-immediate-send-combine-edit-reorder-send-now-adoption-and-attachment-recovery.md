# Walkthrough：Prompt Queue 如何在本地与服务端之间排队、编辑、提升并对账

> 场景：Agent 正在生成回答，用户连续提交三个 Follow-up，其中一个带图片、一个随后被编辑，另一个点击 Send now。Pager 既有尚未绑定 Session 时积累的本地队列，又有 Shell `pending_inputs` 的权威共享队列；多个客户端可能同时编辑、删除或换序；发送 RPC 与 `x.ai/queue/changed` 广播还可能乱序到达。系统必须维持 FIFO，不把同一条 Prompt 同时显示为 Running 和 Queued，不丢图片、Paste 或 `@file` Chip，不让 Combine 吞掉正在编辑的行，也不能在 Send now 取消交接期间重复绘制 User Bubble。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
User submits prompt
  -> dispatch_send_prompt_inner
  -> choose route
       idle / no authoritative server queue
         -> Pager local pending_prompts
         -> maybe_drain_queue
         -> Effect::SendPrompt
       server busy + bound session + local queue empty + plain/no-image
         -> optimistic shared-queue echo
         -> Effect::SendPrompt immediately to Shell
         -> Shell SessionActor::queue_input
         -> pending_inputs + QueueEntryMeta
         -> x.ai/queue/changed broadcast
         -> Pager reconciles echo and shared mirror

When a turn ends
  -> Shell or Pager promotes the next owner-appropriate row
  -> optional combine of consecutive eligible plain prompts
  -> running_prompt_id + display text broadcast
  -> Pager turn-start adoption shim
  -> prompt-id gate admits only the running turn's events

Queue interactions
  -> edit / delete / reorder / clear / send now
  -> local rows mutate Pager queue
  -> shared rows issue x.ai/queue/* request
  -> serialized SessionActor mutation
  -> authoritative QueueChanged rebroadcast
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| Pager 排队与 Drain | `crates/codegen/xai-grok-pager/src/app/dispatch/queue.rs` | `maybe_drain_queue` |
| 提交路线选择 | `crates/codegen/xai-grok-pager/src/app/dispatch/prompt.rs` | Immediate Server Send |
| 本地队列数据 | `crates/codegen/xai-grok-pager/src/app/agent.rs` | `QueuedPrompt`、`AgentSession` |
| Queue Pane 行为 | `crates/codegen/xai-grok-pager/src/app/agent_view/queue.rs` | 删除、换序、Send now |
| 编辑状态机 | `crates/codegen/xai-grok-pager/src/app/queue_edit.rs` | `PromptMode::EditingQueued` |
| 广播接收与 Adoption | `crates/codegen/xai-grok-pager/src/app/acp_handler/queue.rs` | Queue Changed Handler |
| Echo 对账 | `crates/codegen/xai-grok-pager/src/app/app_view.rs` | `apply_queue_changed` |
| Shell 权威队列 | `crates/codegen/xai-grok-shell/src/session/acp_session_impl/prompt_queue.rs` | `queue_input` |
| Shell Promotion | `crates/codegen/xai-grok-shell/src/session/acp_session_impl/notification_drain.rs` | Front Promotion |
| 共享 Combine 规则 | `crates/codegen/xai-prompt-queue/src/combine.rs` | `combine_prefix_len` |
| Wire Schema | `crates/codegen/xai-prompt-queue/src/types.rs` | `QueueChanged`、`QueueEntryWire` |

## 3. 系统里实际有两条可见队列

Pager 的 `pending_prompts` 是 Client-local Drip-feed Queue；Shell 的 `pending_inputs` 是 Session Actor 权威队列，并通过 Broadcast 形成 Pager 的 `shared_queue` Mirror。

## 4. 两条队列不是主从副本

Local Queue 可能包含 Shell 尚不知道的行，Shared Queue 可能包含其他客户端创建的行。它们表示不同所有权，而不是同一集合的两份缓存。

## 5. Queue Pane 展示两者的并集

合并顺序固定为 Server Rows 在前、Local Rows 在后；这隐含一个重要不变量：所有 Server Row 必须比仍在 Local Queue 的 Row 更早。

## 6. Running Turn 不属于“等待队列”

Shell 的 `pending_inputs` 内部仍可能把 Running Item 放在 Front，但 `QueueEntryWire.entries` 会排除它，另用 `running_prompt_id` 表达当前运行身份。

## 7. Synthetic Input 不出现在用户 Queue

Auto-wake、Nudge、Drain 等没有 `QueueEntryMeta`，仍可在 Actor 内排队，却不生成 Queue Pane Row。

## 8. Glossary（一）：队列身份

- **Local queue**：只由当前 Pager 进程持有、尚未交给 Shell 的等待队列。
- **Server-authoritative queue**：Shell Session Actor 持有的权威 `pending_inputs`。
- **Shared queue mirror**：Pager 根据 Broadcast 保存的服务端队列视图。
- **Running turn**：已经开始消费、正在生成或执行工具的 Prompt。
- **Held row**：仍在等待、尚未成为 Running Turn 的队列行。
- **Synthetic input**：系统生成、通常不展示为用户队列行的输入。
- **Drip-feed**：客户端只在 Agent Idle 时逐条发送本地等待项的策略。

## 9. `QueuedPrompt` 用单调 ID 稳定定位

Pager 本地 Entry ID 在 Session 内递增、不复用。UI 显示的位置可变，编辑、删除和换序仍通过稳定 ID 找到同一行。

## 10. 本地行保存的不只有 Text

字段还包括 Kind、Wire Blocks、Images、Skill Styling、Skill Token Ranges、Cron Metadata、Chip Elements 与 Combined Text Segments。

## 11. `wire_blocks` 允许显示与发送分离

Skill 行可显示 `/commit args`，实际发送扩展后的 Structured Blocks；因此 Queue 操作不能假设 `text` 永远等于 Wire Payload。

## 12. `wire_matches_display()` 决定能否按 Text 提升

Plain Row 和单 Text Block 且内容等于 Display 的 Raw Skill Row 返回 true；Client-expanded Skill 返回 false。

## 13. Queue Kind 是类型而不是字符串约定

Pager 本地使用 `QueueEntryKind::{Prompt, Command, BashCommand, Cron}`，不同 Kind 在 Drain 时进入不同 Effect 和 UI 逻辑。

## 14. Shared Wire 使用 String Kind

跨协议的 `QueueEntryWire.kind` 使用 `prompt`、`bash` 等字符串；Pager 再映射回本地 Display Kind，保持 Wire 向前兼容。

## 15. Glossary（二）：队列 Payload

- **Stable ID**：位置变化后仍能代表同一对象的标识。
- **Queue kind**：决定 Drain 与显示语义的行类型。
- **Wire payload**：真正发送给 Agent/Shell 的内容。
- **Display text**：Queue Pane 与 Scrollback 展示给用户的文本。
- **Client-expanded skill**：Pager 已把 Skill 调用扩展成复杂 Prompt Blocks 的行。
- **Raw skill row**：Wire Text 仍等于用户可见调用文本、由 Shell 后续展开的行。

## 16. Idle 提交通常先进入 Local Queue

Pager Enqueue 后立即调用 `maybe_drain_queue`；若所有 Gate 满足，Front 会在同一次 Dispatch 中 Pop 并发出 Effect，所以用户看起来像直接发送。

## 17. Local Queue 是状态机缓冲而非固定延迟

它统一处理未绑定 Session、Turn Running、Model Switch、Replay、编辑锁和附件；Idle Fast Path 只是立即 Drain。

## 18. `maybe_drain_queue` 首先要求 Session Idle

Turn 正在运行时 Local Row 保持等待，不会开启并发模型 Turn。

## 19. Model Switch 暂停 Drain

旧 Model 的 Session Runtime 还在切换时发送会绑定到错误 Harness 或 Catalog，因此 `model_switch_pending` 阻塞。

## 20. Replay 暂停 Drain

Session History 尚在恢复时不能让新 User Turn 与旧 Event 重放交叉，`loading_replay` 也是硬 Gate。

## 21. 无 Session ID 不能 Drain

Dashboard/Starting Window 可以先积累输入，等 Session 真正绑定后再推进。

## 22. 编辑 Front 时 Drain 必须停止

如果 `EditingQueued.id` 正是 Local Front，发送会从用户手下移走正在编辑的对象，导致保存写入消失行。

## 23. Shared Queue 有 Held Row 时 Local Drain 也停止

Server 已拥有下一 Turn；若 Pager 同时乐观提升 Local Front，会与 Shell 真实 Running ID 分叉，后续 Delta 被 Prompt-id Gate 丢弃。

## 24. Glossary（三）：Drain Gate

- **Drain**：从队列 Front 取出 Entry 并开始执行。
- **Gate**：允许或阻止某条状态转移的条件。
- **Replay window**：历史事件尚未恢复完的时间段。
- **Model switch window**：Session Runtime 正在切换模型的过渡状态。
- **Front edit lock**：编辑队首时阻止它被 Drain 的保护。
- **Divergence**：客户端显示的 Running Row 与服务端实际运行项不一致。

## 25. Server Busy 不只等于 Turn Running

Pager 定义为 `is_turn_running() || !shared_queue.is_empty()`。Turn-end 到下一 Row Adoption 之间，本地可能已显示 Idle，但非空 Shared Queue 证明 Shell 仍拥有工作。

## 26. 只检查 Running 会产生 Leader Race

本地误以为 Idle 并提升新 Prompt，Leader 却把它排在既有队列后面；同一 Prompt 在本客户端显示 Running、其他客户端显示 Queued。

## 27. Immediate Server Send 的基本条件

Server Busy、Session 已绑定、Local Queue 为空、未编辑 Queue、未 Model Switch、未 Replay。

## 28. Local Queue 为空是 FIFO Guard

Starting 阶段可能已有较早 Local Row。若新 Row 直接进入 Server Queue，合并视图的 Server-first 会把后来项显示和执行在旧 Local Row 前面。

## 29. 普通 Immediate Path 还要求无图片

当前 Plain Immediate Server-send 分支不承载 PromptWidget Images；带图片通常留在 Local Queue，等待正常 Blocks Builder。

## 30. 某些 Parked Wait 是图片例外

若 Turn 卡在可打断 Wait、没有既有 Held Queue，带图 Prompt 会走 SendPromptNow/Cancel-and-send 路径，而不是永久等当前 Wait。

## 31. 编辑、Switch、Replay 都强制回 Local

这些状态不是“网络稍慢”，而是 Queue 所有权或 Payload 尚不稳定；先保存在 Pager 更安全。

## 32. Glossary（四）：路线选择

- **Immediate server send**：Turn 忙时立即把新 Prompt RPC 交给 Shell 排队。
- **Server busy**：正在运行或仍持有等待行的服务端状态。
- **FIFO guard**：保证先进入的 Prompt 不被后来项越过的条件。
- **Leader mode**：多个客户端依赖同一个权威 Session Runtime 的模式。
- **Parked wait**：Turn 正阻塞等待外部任务、但允许用户输入解除等待的状态。
- **Route selection**：根据当前状态选择 Local 或 Server 路径。

## 33. Immediate Send 先生成 Prompt ID

Pager 用 UUID 标识这次请求，并记录为 Self-originated，未来 Adoption 后才能区分自己的 Turn 与其他客户端 Turn。

## 34. UI 不等待 RPC 才显示 Queue Row

`push_server_queue_echo` 立即把临时 `QueueEntryWire` 放入 Optimistic Map 和 Shared Mirror，降低网络 Round-trip 的视觉延迟。

## 35. Echo ID 就是客户端 Prompt ID

理想情况下 Shell 原样使用该 ID，权威 Broadcast 到达后可按 ID 精确替换，不产生重复 Row。

## 36. Echo 被标记为 Unconfirmed

`optimistic_queue_ids` 记录 Shell 尚未确认的行。编辑或 Send now 不能过早对它发 `x.ai/queue/*`，因为 Actor 可能还找不到 ID。

## 37. Broadcast 是 Replace Snapshot

`apply_queue_changed` 把 `entries` 视为该 Session 当前完整真相，不做永久增量 Patch；空列表删除整个 Map Entry。

## 38. 未确认 Echo 会被暂时 Re-pin 到尾部

若 Broadcast 比 Prompt RPC 更早到达且不含新 ID，不能立刻删 Echo；它仍可能在下一次广播被确认。

## 39. Content Match 是 Re-key Fallback

若服务端用不同 ID 但 Kind+Text 对得上，Pager 返回 `(old_id,new_id)`，把 Send-now Painted Block 等状态迁移到新 ID。

## 40. Echo 最终离开队列时必须 Retire

RPC 失败、取消恢复、删除或 Drain 后若还保留 Echo，后续每次 Broadcast 都会把它重新钉回尾部，造成“幽灵复活”和顺序破坏。

## 41. Glossary（五）：乐观对账

- **Optimistic echo**：权威确认前先显示的临时队列行。
- **Round-trip**：请求发出到响应/广播返回的往返时间。
- **Reconciliation**：用权威状态修正本地乐观状态。
- **Replace snapshot**：新消息完整替换旧集合的同步语义。
- **Re-pin**：在未确认期间把 Echo 继续保留在合并队列中。
- **Re-key**：同一内容改用权威 ID 后迁移本地关联状态。
- **Ghost row**：已不存在却因缓存残留重新出现的队列行。

## 42. Shell `queue_input` 才是权威 Enqueue

它把完整 Content Blocks、Prompt Mode、Trace、Artifact、Client ID、Tool Overrides、Response Channel 等封装成 `InputItem` 放入 `pending_inputs`。

## 43. Prompt History 在 Enqueue 时就落盘

不是等真正开始 Turn；即使后来从 Queue 删除，用户提交过的真实 Prompt 仍可从 History 找回。

## 44. 新真实用户 Prompt 会使 Recap 失效

Epoch 在任何 Await 前提升，避免旧 Recap 在 Prompt 已被接受后仍晚到并污染显示。

## 45. 用户 Prompt 会抢占待处理 Synthetic Auto-wake

Actor 在不动 Running Slot 的前提下 Sweep 某些 Completion Synthetic，释放 Reservation，让真实用户输入优先。

## 46. Queue Metadata 只给非 Synthetic

包含 ID、Version、Owner、Last Editor、Kind、Display Text 和 Combined Segments，供所有客户端共享显示与编辑。

## 47. Display Text 优先读取 Block Meta

若 Text Block 带 `displayText`，Shared Queue 显示紧凑 Skill Invocation；否则 Join Text Blocks，避免暴露 Client-expanded Instruction。

## 48. Owner 与 Last Editor 分开

Owner 保留最初 Enqueue Client，Edit 只更新 Last Editor，便于 Attribution 与 Owner-scoped Clear。

## 49. Version 初始为 0

每次 Shared Edit Saturating Increment，Remove 和 Send now 可携带 Expected Version，拒绝针对旧文本的操作。

## 50. Glossary（六）：Actor Queue Item

- **InputItem**：Shell 内部承载一次待处理输入及其全部运行依赖的对象。
- **Queue metadata**：专供共享显示、编辑和对账的行信息。
- **Owner**：最初创建队列行的客户端。
- **Last editor**：最近修改该行文本的客户端。
- **Version**：每次原地编辑递增的并发控制编号。
- **Expected version**：调用者认为自己正在操作的版本。
- **Attribution**：记录某项操作由哪个客户端产生。

## 51. Shell 的 Send now 插在 Running Front 后

它不会移走正在运行的 Front，而是把新项放到下一位置，等待当前 Turn Cancel 完成后自然 Promotion。

## 52. 多个 Send now 仍保持 FIFO

插入位置会越过此前已标 `send_now` 的行，所以 Goal Turn 不取消时连续提升不会倒序。

## 53. Send now 是否取消当前 Turn 有额外 Gate

必须是非 Synthetic、Turn Running 且 Goal Loop 不 Active；Goal 自治期间只提升，不强制取消。

## 54. Interruptible Wait 可自动 Send now

Turn 正阻塞 Wait 且没有 Held User Queue 时，新真实 Prompt 自动获得 Send-now 语义，解除无意义等待。

## 55. 已有 Held Queue 时不自动越过

否则新 Prompt 会抢在用户之前排好的 Follow-up 前面；它只能正常 Append。

## 56. Send-now Cancel 有专用 Trigger

Shell 给 Turn Completion 标记 `cancelTrigger=send_now`；Pager 可抑制普通“用户中断”Marker，因为用户是在继续对话，不是宣布停止。

## 57. Send now 不等于 Mid-turn Interjection

当前 `handle_interject_queued_prompt` 实现把选中 Row 提升为下一 Turn，并可能取消当前 Turn；某些旧注释仍使用 Interject 命名，应以 Actor 代码为准。

## 58. Glossary（七）：Send now

- **Send now**：把等待行提升为下一 Turn，并在允许时取消当前 Turn。
- **Promotion**：把等待项移动到下一可运行位置。
- **Cancel-and-send**：取消当前 Turn 后立即运行已提升 Prompt。
- **Stacked send-now**：短时间连续提升多条 Prompt。
- **Cancel trigger**：说明 Turn 因何结束的 Metadata。
- **Goal exemption**：Goal Loop Active 时不因 Send now 强制取消。
- **Mid-turn interjection**：把文本注入仍在运行的同一 Turn，语义不同。

## 59. Queue Pane Send now 可以选任意可见行

Pane Selection/Mouse 使用选中 Stable ID；空 Composer 上的快捷发送则固定选择合并视图 Top Row，符合“下一个将 Drain 的项”。

## 60. Local Send now 只允许 Prompt-like Row

Plain Prompt 或 Wire=Display 的 Raw Skill 可取出后 `SendPromptNow`；Bash、Command、Cron 和 Client-expanded Skill 不能只发送 Display Text。

## 61. Shared Row 可由 Shell 原样提升

完整 InputItem 已在 Actor 内，所以 Shared Bash 或复杂 Payload 也能 `QueueInterjectShared`，不会丢 Wire Blocks。

## 62. Optimistic Shared Row 的 Send now 必须 Park

若 Row 还未出现在权威 Broadcast，立即发 Promotion 会 No-op；Pager 把 ID 放进 `send_now_awaiting_confirm`。

## 63. Confirm Broadcast 后再带权威 Version 发送

`resolve_send_now_awaiting_confirm` 在 Raw Entries 找到 Row 时返回 `(id,version)`，Handler 再排入 QueueInterject Effect。

## 64. 若 Row 已自然成为 Running 则无需再提升

Broadcast 的 `running_prompt_id` 等于 Awaiting ID 时清 Park；自然 Drain 已赢得 Race。

## 65. Row 既未 Queued 也未 Running 时继续等待

可能只是 RPC 尚在飞；不能凭一次缺席 Broadcast 判失败。

## 66. Echo 明确 Retire 时同步清 Park

失败或删除后 Row 永远不会确认，必须丢弃挂起的 Send-now Intent，避免未来误作用到重用内容。

## 67. Glossary（八）：确认竞态

- **Park intent**：暂存用户操作，等待目标对象获得权威身份后执行。
- **Confirmation broadcast**：证明服务端已接纳 Row 的 QueueChanged。
- **Authoritative version**：服务端广播的当前行版本。
- **Natural drain**：无需显式提升，Row 已按队列顺序开始运行。
- **Race winner**：多个并发路径中先完成并决定最终行为的一方。
- **Benign no-op**：条件已变化时安全地不做任何修改。

## 68. QueueChanged 携带 Running Display

Running Row 从 `entries` 排除，但 Broadcast 额外提供 ID、Text、Kind 和 Combined Texts，使 Viewer Client 能在 User Echo 之前建立 Turn UI。

## 69. Prompt ID 是跨通道 Join Key

Queue Broadcast、Session Update、Prompt Response 和 Scrollback Adoption 都依靠同一 ID，处理不同 Channel 的重排。

## 70. 本地已启动同一 ID 时 Adoption 是 No-op

Single-client Local Drain 已经设置 `current_prompt_id`；确认 Broadcast 再来不能重复 Start Turn 或 User Bubble。

## 71. 其他客户端的 Running ID 需要 Adoption Shim

Viewer 没有本地 Queue Payload/Start Effect，使用 Running Display 建立 Boundary、User Block、Bash State 与 Current Prompt ID。

## 72. User Echo 可能早于或晚于 Queue Broadcast

Shim 会尝试复用尾部已经绘制的相同 User Prompt，或提前画 Block 并吞掉后续重复 Echo。

## 73. Adoption Window 可以暂存 Update

在 Running 身份与 UI Block 尚未完整接好时，相关 Session Updates 按 Prompt ID Buffer；确认后只 Flush 目标 ID，其余丢弃。

## 74. Event Cursor 只能向前

Flush 旧 Buffer 时仅在 Sequence 更大时推进 `last_seen_event_id`，防止覆盖另一路已处理的更新 Cursor。

## 75. Turn End Broadcast 也可能超车 Updates

Handler 用 Pending Adoption 的 `turn_ended` One-shot 状态保留必要 Stash，直到 Prompt Response/Update 收拢。

## 76. Glossary（九）：Adoption

- **Adoption**：未发起该 Prompt 的客户端接管其 Running Turn 显示与事件路由。
- **Join key**：跨多条消息关联同一实体的稳定键。
- **Turn-start shim**：用 Queue Broadcast 补建本地 Turn 开始状态的适配逻辑。
- **Duplicate suppression**：已有 User Bubble 时不再重复绘制。
- **Reordering channel**：彼此不保证严格到达顺序的消息通道。
- **Event cursor**：记录已处理到哪个事件的进度标识。

## 77. Combine 是可配置的批量 Drain

启用 `combine_queued_prompts` 后，连续 Plain Follow-up 可以在 Promotion 前合成一个模型 Turn，减少多轮固定成本。

## 78. Pager 与 Shell 共用纯规则 Crate

`xai-prompt-queue` 提供 `CombineGate`、Prefix Length、Separator、Join 和 Display Metadata Stamping，避免两端 Eligibility 漂移。

## 79. Combine 只处理可合并前缀

从 Front 开始遇到第一个不合格 Row 就停止，不会跳过中间 Barrier 再合并后面的 Prompt。

## 80. Front 可以带图片

它自己的 Images 仍可进入合并 Turn；Follower 不能带图片，因为当前 Merge 只把后续 Text 折入 Front。

## 81. Bash、Command、Cron 都是 Barrier

它们有独立执行语义，不能和自然语言 Prompt 用两个换行拼成一个模型输入。

## 82. Expanded Skill 是 Barrier

合并 Display Text 会丢掉扩展 Wire Payload，合并 Expanded Payload 又可能暴露内部指令，因此保持独立。

## 83. Synthetic 与 Per-turn Override 不能合并

Synthetic 有系统调度语义；Follower 自带 Tool Override 时折入 Front 会丢失绑定边界。

## 84. 正在编辑的 Follower 是 Barrier

Local 通过 `editing_id`，Shell 通过 `combine_edit_holds`；Combine 不得让 Composer 对应的 Row 突然消失。

## 85. Glossary（十）：合并规则

- **Combine**：把多个等待 Prompt 合成一个模型 Turn。
- **Mergeable prefix**：从队首开始连续满足合并条件的区间。
- **Barrier**：阻止 Combine 穿越的队列行。
- **Follower**：被折入 Front 的后续行。
- **Per-turn override**：只应作用于某一 Prompt 的工具或执行设置。
- **Edit hold**：服务端暂时禁止某行被 Combine 吸收的标记。

## 86. 文本之间用两个换行连接

共享常量 `TEXT_SEPARATOR = "\n\n"`，既让模型看到段落边界，也使 Chip Range Re-offset 可确定计算。

## 87. 模型 Body 合并但 UI 保留多 Bubble

`combined_texts` 保存原始 Segments；Pager 在 Scrollback 绘制多个 User Block，不把三次用户提交伪装成一次输入。

## 88. Metadata 让 Replay 复原 Segments

首个 Text Block 写入 `combinedDisplayTexts`，Session 重载后仍能恢复多 Bubble，而不是只显示 Join 后大段文字。

## 89. Local Combine 会平移 Chip Range

Follower Chip 的 Byte Range 加上前面 Join Text 长度与 Separator，合并后 Paste/File/Image Chip 仍指向正确位置。

## 90. Multi Combine 清空 Skill Token Ranges

多个 Bubble 各自渲染 Plain Segment；旧 Range 基于单段 Offset，直接保留会错位。

## 91. Merged-away Shell Row 要正常完成 RPC

它的内容确实被 Front 执行，但独立 Queue Row 已消失，因此用 `RemovedFromQueue` Completion 收尾，不能 Drop Sender 造成假 Turn Failure。

## 92. Glossary（十一）：合并显示

- **Segment**：Combine 前每条原始用户 Prompt。
- **Multi-bubble**：一个模型 Turn 在 UI 中保留多个用户输入气泡。
- **Range re-offset**：文本拼接后平移 Element Byte Range。
- **Replay metadata**：用于重放时恢复原显示结构的附加信息。
- **RemovedFromQueue**：Row 不再独立运行时的正常完成类型。
- **Dropped sender**：异步响应通道未显式完成就销毁，常被误认为失败。

## 93. Queue Edit 借用普通 Composer

选择 Row 后把其 Text、Images 和 Chip Elements Restore 进 PromptWidget，并进入 `PromptMode::EditingQueued`。

## 94. 原 Composer Draft 必须 Stash

若用户编辑队列前已有未提交 Draft，退出 Edit Mode 时由唯一 Restore Owner 恢复，不能被队列文本覆盖。

## 95. Stash 恰好 Set 一次、Take 一次

`exit_editing_mode` 集中拥有恢复；重复退出有 Idempotent Guard，避免第二次拿到 Empty Stash 后擦掉原 Draft。

## 96. Local 与 Shared Edit 使用同一 Mode

`server_id=None` 表示本地 Row；`Some(prompt_id)` 表示服务端 Row，Save 时走不同所有权路径。

## 97. Shared Row 进入编辑前先 Hold

Pager 发 `QueueHoldEdit`，Shell 把 ID 加入 `combine_edit_holds`，避免下一次 Promotion 在编辑尚未保存时按旧文本吸收它。

## 98. Optimistic Echo 暂不允许编辑

Shell 尚没有 Row，Hold 会 No-op；稍后确认前 Row 可能被 Combine，造成正在编辑的对象消失。

## 99. Glossary（十二）：编辑所有权

- **EditingQueued**：Composer 当前代表某条等待行而非新 Draft 的模式。
- **Stashed prompt**：进入编辑前保存的原 Composer 完整状态。
- **Restore owner**：唯一负责取出 Stash 并恢复的退出函数。
- **Server ID**：共享 Row 在 Shell 中稳定的 Prompt ID。
- **Edit hold**：编辑期间阻止服务端合并该行的临时集合成员。
- **Idempotent exit**：重复调用也不会二次破坏状态的退出逻辑。

## 100. Bare Enter 保存，Modified Enter 换行

Shift/Alt+Enter 保持 Edit Mode 并插入 Newline；Bare Enter 只在非空文本时保存。

## 101. Esc 或空 Composer Ctrl-C 放弃编辑

退出后恢复原 Draft，并触发 Drain 检查；若刚才锁住 Front，下一 Row 现在可以继续发送。

## 102. Dirty Edit 阻止切换 Pane

用户尚未 Save/Discard 时 Focus Switch 被拦截并显示 Toast，避免不可见 Edit Confirmation Modal 吃掉后续所有输入。

## 103. Clean Edit 可以静默退出

文本与 Original 相同时切 Pane 不需要确认，没有数据损失风险。

## 104. 空白 Save 不会清空 Row

Queue 不允许 Blank Prompt；Modal Save 遇到空白时保留 Original 并退出，而不是把权威行改为空字符串。

## 105. 删除正在编辑的 Local Row 先退出 Mode

顺序必须是 Exit → Remove → Auto-hide，否则 Pane 切换会撞上仍激活的 Dirty Lock。

## 106. Shared Row 消失会自动取消编辑

Broadcast 发现 `server_id` 已不在 Mirror，恢复编辑前 Draft并提示 Row 不存在，防止 Composer 卡在 Ghost Object。

## 107. Glossary（十三）：编辑交互

- **Dirty edit**：当前 Composer Text 与进入编辑时 Original 不同。
- **Focus lock**：Dirty 状态下阻止离开编辑 Pane 的保护。
- **Discard**：放弃修改，保留原 Queue Row。
- **Lost row**：编辑期间被 Drain、删除或被其他客户端改变而消失的行。
- **Ghost object**：UI 仍在编辑、但权威集合已不存在的对象。
- **Auto-hide**：Queue 为空时自动关闭 Pane 的行为。

## 108. Local Save 原地修改完整 Entry

新 Text、Images、Chip Elements 和重新计算的 Skill Token Ranges写回同一个 Stable ID。

## 109. 删除旧图片要清理临时文件

通过 Image Identity 比较保留集合；编辑中移除的旧附件会 Cleanup，不让 Temp Storage 泄漏。

## 110. Local Edit 必须清旧 `wire_blocks`

用户修改后的 Text 已不一定对应原 Skill Expansion；保存后按 Plain Text 发送，由 Shell 再解释可能残留的 Slash。

## 111. Skill Display Flag 同时清除

它只对 Structured Wire 有意义；若留下，会把普通编辑文本错误画成 Skill。

## 112. Shared Save 不直接改 Mirror

Pager 发 `QueueEditShared`，等待 Shell Actor 修改并 Broadcast；本客户端和其他客户端使用同一权威结果。

## 113. Shell Edit 是 Mailbox 序列化的 LWW

它重建 Text Block、保留现有 Image Blocks、更新 Display Text、Version 和 Last Editor；不存在客户端侧 Merge Conflict UI。

## 114. Shared Save 不提前 Release Hold

若先 Release，再发 Edit，Promotion 可能在两条消息间按旧 Text Combine。Edit Handler 在同一 State Lock 下更新 Text 并清 Hold。

## 115. Cancel Shared Edit 才显式 Release

没有后续 Edit Handler 替它清理，所以 Esc/Discard 必须发送 Release；Shell 还会 GC 不再 Live 的陈旧 Hold。

## 116. Glossary（十四）：保存一致性

- **In-place mutation**：不更换 Stable ID，直接修改行内容。
- **LWW**：Last Write Wins，Actor Mailbox 中最后处理的写入获胜。
- **Conflict resolution**：多个编辑竞争时决定最终值的规则。
- **Temporary-file cleanup**：附件被删除后回收本地临时文件。
- **Stale wire payload**：与已编辑 Display Text 不再对应的旧结构化内容。
- **Atomic update-and-release**：同一锁内更新文本并解除 Hold。

## 117. Shared Edit Wire 本身只有新 Text

Pager 从 Shared Mirror 只能恢复文本，没有 Server Row 的 Client Chip/Preview State；但 Shell 修改时保留原 `Image` Blocks。

## 118. 编辑 Shared Row 时新加图片不支持

Queue Edit RPC 没有携带新 Image Payload；Send-now Edit 会清 Composer 新图片并给出准确 Toast。

## 119. Existing Shared Images 与新编辑 Text 仍可共存

Shell `apply_queued_prompt_edit` 构造新 Text Block后把旧 Image Blocks接回，避免 Text Edit 静默拆附件。

## 120. Shared Chip 样式不参与权威状态

Queue Wire 只传 Text/Kind/Version 等；其他客户端显示 Plain Row。真正 `@file` 语义仍可由 Text 在 Shell Prompt Parse 时识别。

## 121. Edit 后 Combined Segments 清空

用户显式重写 Row 后，旧 `combined_texts` 不再代表新文本，Version 更新时置 None。

## 122. Bash Edit 需要重建 Bash Meta

仅换 Text 而保留旧 Bash Metadata 会执行旧命令；完全去掉 Meta 又会把 Row 降级成模型 Prompt，因此 Shell按新 Text重建。

## 123. Glossary（十五）：附件编辑

- **Text-only edit wire**：编辑 RPC 只携带新文本，不携带新附件。
- **Existing attachment**：原 Queue Item 已经拥有的 Image Block。
- **Composer attachment**：当前编辑器中新添加、尚未进入 Server Row 的附件。
- **Bash metadata**：让 Shell 把 Text 作为直接命令而非模型 Prompt 的 Block Meta。
- **Demotion**：因 Metadata 丢失而从 Bash/Skill 语义退回普通 Prompt。

## 124. 删除 Shared Row 使用 Expected Version

Pager可先乐观从 Mirror 隐藏，Shell只在 ID、Version、可选 Owner 与非 Running 条件同时满足时真正 Remove。

## 125. Stale Remove 是安全 No-op

若其他客户端已编辑增加 Version，旧删除不会误删新内容；Shell仍 Broadcast 当前 Queue，让请求方恢复权威视图。

## 126. Remove 必须显式完成 Pending RPC

被删 Row 返回 Cancelled + `RemovedFromQueue` + 0 Token；直接 Drop Response Sender 会在客户端表现成 Session Failure。

## 127. Clear 默认可以按 Owner 限定

只移除调用客户端创建的等待项；其他 Client Row、Synthetic 与 Running Turn 保留。

## 128. Reorder 永不移动 Running Turn

Actor先分离 Running/Synthetic Pinned Items和可重排用户行，再按 Requested ID Rank稳定排序。

## 129. 未出现在 Reorder Payload 的 Row 留在后面

它们保持相对顺序，兼容命令发出后并发新增的 Prompt，不会被意外删除。

## 130. Local Reorder 直接交换 VecDeque 邻居

Local Row 尚无其他客户端可见，Pager按 Stable ID查 Position并 Swap Up/Down，无需协议 Round-trip。

## 131. Shared Reorder 等待 Broadcast 收敛

Pane生成完整 `ordered_ids`，Action转成 `x.ai/queue/reorder`；权威 Actor序列化后给所有客户端相同 Position。

## 132. Glossary（十六）：并发队列操作

- **Optimistic remove**：权威确认前先在本地隐藏被删行。
- **Versioned delete**：只删除调用者看到的指定版本。
- **Owner-scoped clear**：只清理某个创建者的队列行。
- **Pinned item**：换序时位置受保护的 Running 或 Synthetic 项。
- **Stable reorder**：相同 Rank 的未指定项保持原相对顺序。
- **Concurrent append**：换序请求期间另一客户端新增 Row。

## 133. Interject Key 在 Edit Mode 有专门分支

直接落入普通 Interjection 会让 Row 留在 Queue、Composer却退出，造成重复执行与 Dirty Modal Loop。

## 134. Running 时编辑 Local Prompt-like Row可转为 Interjection

Pager移除本地 Row，携带编辑后的 Text 与 Images走真正 `Action::Interject`，所以它进入当前 Turn，而不是 Send-now Next Turn。

## 135. Shared Edited Row 的对应操作走 Queue Send now

它携带 `new_text` 与 Expected Version，Shell在提升前先把编辑写回 Item；当前实现仍是 Next-turn Promotion。

## 136. 非 Prompt-like Local Row不能 Mid-turn

保存编辑并 Toast“当前 Turn 结束后运行”，避免把 Bash/Expanded Skill的 Display Text误注入模型。

## 137. Row 已消失时回退需看 Kind

Prompt-like可把编辑文本当普通 Interjection；非 Prompt Kind则只保存/提示，不能猜测丢失的 Payload。

## 138. Glossary（十七）：编辑后的立即执行

- **Edit-interject**：编辑队列行后把新文本注入当前 Turn 的操作。
- **Prompt-like**：可安全仅凭 Display Text执行的队列行。
- **Payload loss**：只发送显示文本导致 Structured/Bash语义消失。
- **Fallback**：目标 Row 消失时采用的退化处理路径。
- **Duplicate execution**：同一文本既被注入当前 Turn、又保留为后续 Row。

## 139. Local Drain 开始时生成全新 Prompt ID

Local Stable Numeric ID 只服务 Pager Queue；出网时用 UUID 作为跨 ACP Turn Identity。

## 140. Prompt Drain 先画 User Block

然后保存 `InFlightPrompt`、设置 Turn Time、Follow Scrollback，再产生 Send Effect，使 UI立即响应。

## 141. Plain In-flight 可以 Cancel-with-restore

Text、Images、主 Bubble、Combined Earlier Bubbles 与 Chip Elements形成恢复快照；Expanded Skill因 Wire/Display分离不提供同样回填。

## 142. Bash Drain 不画普通 User Prompt

Shell Execute Block 才是视觉 Entry，避免同一命令同时出现 Prompt Bubble 和 Tool Block。

## 143. Cron Drain 使用专用 Prompt ID 与 Wrapper

ID带 `scheduler-fired-`，Scrollback显示原任务文本，Wire则加入 System Reminder 与 Display/Cron Metadata。

## 144. Compact 是 Queue Command

它不开始普通模型 Prompt，而切换 `AgentCommand::Compact`并产生 `Effect::Compact`。

## 145. Follow-up Chips 在新 Turn 开始时清除

它们属于上一条 Response；Local Drain、Immediate Send和 Adoption Shim都必须覆盖各自 Early-return Path。

## 146. Glossary（十八）：Drain 副作用

- **Turn identity**：跨消息关联一次运行的 Prompt ID。
- **In-flight snapshot**：Turn 尚未稳定确认时用于恢复的输入快照。
- **User bubble**：Scrollback 中表示用户输入的 Render Block。
- **Page flip**：发送后 Scrollback是否切换到新 Turn页的显示策略。
- **Cron framing**：把定时任务包装成模型可识别系统语义的文本。
- **Early return path**：跳过函数尾部公共逻辑、需自行补齐清理的分支。

## 147. Prompt Queue 的真正 Source of Truth 会随阶段变化

尚未送出时 Local Entry权威；RPC 发出但未确认时是 Local Echo+Pending Request；Shell接纳后 Actor `pending_inputs`权威；运行后 Running Prompt ID权威。

## 148. 不能用单一 `queue_len` 推断系统空闲

Local Queue可能空但 Shared非空，Shared Entries可能空但 Running ID存在，Actor内部还可能有不展示的 Synthetic。

## 149. 合并视图的 Selection ID 是适配层

Local使用 Numeric ID，Server Row把 String Prompt ID合成为 UI Stable Selection ID；操作前必须再解析 Origin。

## 150. 版本只保护部分操作

Remove与Queue Send now携带 Expected Version；Edit本身采用Actor Mailbox LWW而不要求Expected Version，后到写入覆盖先到写入。

## 151. Broadcast 永远比本地猜测更权威

即使操作 No-op、Stale或Row刚开始运行，Shell也尽量重播当前 Queue，让所有客户端最终一致。

## 152. Glossary（十九）：一致性模型

- **Phase-dependent authority**：对象在不同生命周期阶段由不同组件拥有最终决定权。
- **Eventual consistency**：短暂显示差异经过 Broadcast最终收敛。
- **Mirror**：权威集合在客户端的可读视图。
- **Origin adapter**：把 Local/Server不同 ID体系映射到统一UI Row的层。
- **Serialized mailbox**：Actor按顺序处理修改命令的并发模型。
- **Partial optimistic UI**：只对部分操作先改本地、随后再对账。

## 153. 调试“Prompt 顺序反了”先看路线选择

检查 Immediate Eligibility 的 Local-empty FIFO Guard、Shared-first Merge、Starting Session时已有Local Row，以及Shell Send-now插入位置。

## 154. 调试“Queue Row 重复/复活”看 Echo Lifecycle

检查Optimistic Map是否按ID确认、是否发生Content Re-key、RPC失败是否Retire，以及Broadcast是否持续Re-pin旧Echo。

## 155. 调试“显示 Running 但没有输出”看 Prompt ID

通常是Pager Local Drain与Leader Authority分叉，Session Updates被Current Prompt ID Gate拒绝；检查Shared Queue Gate与Adoption日志。

## 156. 调试“编辑保存后发了旧内容”看 Hold 顺序

确认进入Shared Edit已Hold，Save没有先Release，Shell在同一Lock内Apply Edit并清Hold，Combine才随后运行。

## 157. 调试“编辑后图片消失”先区分Local与Shared

Local检查Image Identity保留和Temp Cleanup；Shared检查Actor `apply_queued_prompt_edit`是否接回旧Image Blocks，以及是否误把Composer新图片当可上传编辑。

## 158. 调试“Send now 没反应”检查确认状态

Row可能仍是Optimistic Echo，Intent在`send_now_awaiting_confirm`；再看权威Broadcast是否把它确认Queued、自然Running或明确Retire。

## 159. 调试“多个Prompt合成一条”检查Combine Metadata

模型Body合并可能是配置预期；UI若也只剩一Bubble，则检查`combined_texts`与`combinedDisplayTexts`是否在Pager、Shell和Replay间保留。

## 160. Glossary（二十）：调试 Join Keys

- **Qtrace**：源码中用于记录Queue Route、Broadcast与Local Drain的专项Tracing Target。
- **Prompt-id gate**：只接受当前Running Prompt相关Update的过滤边界。
- **Echo lifecycle**：Optimistic Row从创建、确认到Retire/Re-key的全过程。
- **Combine metadata**：保存原始Segment显示身份的附加字段。
- **Ordering invariant**：系统在所有竞态中都必须保持的顺序约束。

## 161. 测试应分成本地、Actor和多客户端三层

本地测试 `QueuedPrompt`/Edit/Chip；Actor测试Version、Owner、Promotion、Combine；多客户端测试Echo、Broadcast Reorder、Adoption与Duplicate Suppression。

## 162. Local 必测矩阵

- Idle立即Drain、Running保持等待、Session未绑定不Drain。
- Model Switch、Replay、Front Edit与Shared Held Row阻塞。
- Image/Chip/Paste随Queue Edit、Combine和Cancel Restore保留。
- Bash/Command/Cron/Expanded Skill不被错误Send now或Combine。

## 163. Server 必测矩阵

-普通Append、Send-now插在Running后、Stacked FIFO和Goal Exemption。
-Remove Stale Version No-op、Owner Clear、Running Row不可删除/换序/编辑。
-Edit保留Images、重建Bash Meta、递增Version并清Combine Hold。
-Combine遇Images Follower、Override、Skill、Synthetic和Edit Hold停止。

## 164. Multi-client 必测矩阵

-Broadcast先于Prompt RPC、Echo确认、ID Re-key和RPC Failure Retire。
-一个客户端编辑/删除时另一客户端的Mirror与Lost-row Edit收敛。
-Running Broadcast与User Echo两种到达顺序都只画一个Bubble。
-Send-now在Unconfirmed、Confirmed Queued和Already Running三个状态下都不丢Intent。

## 165. Glossary（二十一）：验证策略

- **Actor test**：直接驱动Serialized Session State与命令处理器的测试。
- **Multi-client test**：模拟多个订阅者共享一个Session Authority的测试。
- **Race test**：刻意交换异步事件到达顺序验证不变量。
- **Golden wire test**：固定JSON字段与序列化格式的兼容测试。
- **State matrix**：系统化组合多个状态维度的测试表。

## 166. 不要把Local Mirror当权威修改Shared Row

除短暂Optimistic Hide外，Edit/Reorder最终结果必须来自Shell Broadcast，否则其他客户端和重连都会把本地结果覆盖。

## 167. 不要让后来的Server Row跨过旧Local Row

任何扩展Immediate Send Eligibility的修改都必须重新证明Server-first + Local-second仍保持全局FIFO。

## 168. 不要在Edit Save前释放Combine Hold

两个Fire-and-forget命令之间没有事务，必须让Edit Handler自己在同一Lock中完成Update+Release。

## 169. 不要按Display Text执行复杂Row

Expanded Skill、Bash和Command的真实Payload可能完全不同；只有`wire_matches_display`或Actor持有完整Item时才可立即提升。

## 170. 不要因删除Row就Drop Response Channel

Queue Maintenance也必须给原RPC明确的Removed Completion，否则调用端会把正常删除误报为Turn失败。

## 171. Glossary（二十二）：实现守则

- **Transaction gap**：两个独立异步命令之间可被其他操作插入的窗口。
- **Payload fidelity**：执行时保持原始结构化内容完整的性质。
- **Explicit completion**：即使未运行也向等待方返回明确终态。
- **Global FIFO**：跨Local与Server所有可见输入仍遵循先入先出。
- **Authority violation**：非权威组件永久决定共享状态的错误。

## 172. 最重要的七个不变量

1. Running Row不能同时作为普通Held Row显示。
2. Server-first + Local-second合并必须保持全局FIFO。
3. 未确认Echo不能被当成Actor中已存在Row操作。
4. Broadcast最终替换所有客户端对Shared Queue的猜测。
5. Combine不能越过非Plain Payload、附件Follower、Override或Edit Hold。
6. Queue Edit不能丢原Draft、Images、Chip Range或误用旧Wire Blocks。
7. Send now必须恰好提升一次，并通过Prompt ID抑制重复Bubble与错误Cancel Marker。

## 173. 推荐源码阅读顺序

先读Pager `QueuedPrompt`与`maybe_drain_queue`建立Local模型，再读`dispatch/prompt.rs`的Immediate Route；然后读`xai-prompt-queue`共享规则、Shell `queue_input`/Mutation/Promotion；最后回到Pager QueueChanged Handler、Adoption、Queue Pane和EditingQueued竞态。

## 174. 一句话记住整个系统

Prompt Queue不是一个`VecDeque`，而是一套跨Client与Session Actor的分阶段所有权协议：Local Queue负责尚不能安全交付的输入，Shell Queue负责共享顺序，Optimistic Echo负责低延迟，Prompt ID、Version与Broadcast负责最终对账。

## 175. 最终心智模型

用户按Enter后，系统首先决定“现在谁有资格拥有这条Prompt”，而不是简单决定“发还是不发”。若Pager仍拥有它，就在所有Drain Gate满足后逐条交付；若Shell已忙且没有更老Local Row，就立即进入权威`pending_inputs`并用Echo遮住往返延迟。编辑、删除、换序、Combine与Send now都必须在当前Owner一侧完成，并通过`x.ai/queue/changed`收敛所有客户端。真正可靠的Queue实现依赖的不是某个按钮，而是Global FIFO、Payload Fidelity、Prompt-ID Adoption和明确Completion这四组不变量同时成立。

