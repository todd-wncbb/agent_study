# Walkthrough：`/btw` 与 Session Recap 如何借用主会话上下文，却不污染正式 Conversation

> 场景一：主 Agent 正在执行工具，用户临时输入 `/btw 这个错误是什么意思？`，希望立即得到一个只依赖已有上下文的旁路答案，又不打断主 Turn。场景二：用户离开一段时间后回来，希望看到一句“刚才做到哪里”的 Recap。两项功能都要读取主 Conversation，却不能把辅助指令、回答或摘要写回正式历史；它们还要处理进行中的 Tool Call、长上下文、并发 Prompt、缓存复用、失败重试和 UI 生命周期。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
/btw <question>
  -> Pager Action::SendBtw（绕过 Prompt queue）
  -> loading overlay
  -> ACP x.ai/btw（等待最终回答）
  -> Shell SessionCommand::SideQuestion
  -> Conversation read-only snapshot
  -> 修剪未完成的 trailing tool run
  -> 追加一次性 side-question instruction
  -> conversation_collect（无客户端 tool loop）
  -> overload-only bounded retry
  -> btw_history.jsonl
  -> ACP response.result.answer
  -> overlay Done / Error

/recap or automatic away recap
  -> Pager Action::SendRecap（绕过 Prompt queue）
  -> manual spinner / auto silent request
  -> ACP x.ai/recap（仅确认受理）
  -> Shell SessionCommand::Recap
  -> gate + watermark + recap_epoch
  -> Conversation read-only snapshot
  -> budget + trailing tool repair + recap instruction
  -> conversation_collect
  -> clean + artifact persistence + commit watermark
  -> SessionRecap notification
  -> fill spinner / append display-only scrollback block
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| Shell 辅助采样 | `xai-grok-shell/src/session/acp_session_impl/recap.rs` | `handle_side_question`、`handle_recap` |
| Recap 纯函数 | `xai-grok-shell/src/session/helpers/session_recap.rs` | budget、gate、clean、watermark |
| `/btw` 扩展 | `xai-grok-shell/src/extensions/feedback.rs` | `btw` handler |
| Recap 扩展 | `xai-grok-shell/src/extensions/recap.rs` | feature gate、fire-and-forget |
| 旁路持久化 | `xai-grok-shell/src/session/persistence.rs` | `BtwEntry` |
| Pager 发起请求 | `xai-grok-pager/src/app/dispatch/notes.rs` | `dispatch_send_btw`、`dispatch_send_recap` |
| Pager 网络 Effect | `xai-grok-pager/src/app/effects/mod.rs` | `Effect::SendBtw`、`SendRecap` |
| Pager 收通知 | `xai-grok-pager/src/app/acp_handler/session_notification.rs` | `SessionRecap` |
| `/btw` 面板 | `xai-grok-pager/src/views/btw_overlay.rs` | `BtwOverlayState` |
| 自动触发 | `xai-grok-pager/src/notifications/focus_tracker.rs` | away period、retry backoff |

## 3. 两个功能共享的核心模型

它们都不是主 Agent 的新 Turn，而是从主 Conversation 拍一张只读快照，在快照末尾追加一条临时指令，发起独立的一次模型采样。

## 4. 为什么叫“辅助调用”

源码用 `AuxCall` 抽象 `/btw`、Recap 和 AI suggest 的共同请求骨架。它们借用父会话的 items、模型、reasoning effort 和工具前缀，但拥有独立 request id。

## 5. Snapshot 不是 Clone Session

快照只是某一时刻的 `Vec<ConversationItem>`。它不创建可继续对话的 Child Session，不拥有自己的 ChatState actor，也不会持续接收父 Turn 后续产生的新消息。

## 6. Out-of-band 的准确含义

这里的旁路是“主 Turn 控制流之外”。主 Agent 可以继续执行；辅助调用单独请求模型、单独返回 UI，但共享此前已经进入 Conversation 的知识。

## 7. 最重要的不变量

```text
Conversation_after == Conversation_before
```

这里比较的是正式 ChatState。辅助请求可以写审计文件、Recap artifact 和 Pager scrollback，但不能 `append` 到主 Conversation。

## 8. UI 历史不等于模型历史

Recap block 出现在 scrollback，不代表下一次模型会看到它；`/btw` 折叠块也只是客户端展示。区分“可见历史”与“采样上下文”是理解本功能的第一道门槛。

## 9. Glossary（一）：共同架构

- **Conversation**：模型的正式会话项序列，包含 System、User、Assistant、Reasoning、ToolResult 等。
- **Snapshot**：在一个时点复制出的 Conversation 值；复制后与原状态脱钩。
- **Out-of-band / 旁路**：不进入主 Turn 状态机的辅助流程。
- **One-shot sampling**：只发起一次“输入到最终响应”的采样，不接着执行工具再把结果喂回模型。
- **Display-only**：只进入客户端展示层，不进入模型 Conversation。
- **Auxiliary call**：借用父会话前缀完成特定小任务的辅助模型请求。

## 10. `/btw` 从 Slash Command 开始

`/btw` 要求非空参数，解析后生成 `Action::SendBtw(question)`。它不是普通 Prompt，因此不会进入待发送 Prompt queue。

## 11. 为什么必须绕过 Prompt queue

普通 Prompt 在 Agent 忙碌时往往要排队；`/btw` 的产品价值恰恰是主 Turn 运行中也能问一句。如果排队，它会退化为下一轮普通问题。

## 12. Pager 先建立 Loading Overlay

`dispatch_send_btw` 清空输入框并设置 `BtwOverlayState::Loading { question }`，再返回 `Effect::SendBtw`。网络尚未完成时，用户已经得到明确反馈。

## 13. Overlay 为什么不抢走 Prompt 焦点

Loading 阶段 Prompt 继续持有焦点；只有回答完成后面板才适合滚动和阅读。这让用户在等待旁路答案时仍能操作主界面。

## 14. Minimal Mode 使用另一条展示路径

最小模式通过 `start_minimal_btw` 记录 request id，不建立全屏 overlay；响应仍用同一个 `TaskResult::BtwResponse` 回来，并用 id 防止错配。

## 15. Pager 的 `/btw` 是阻塞式 ACP 调用

Effect 发出 `x.ai/btw`，异步任务会一直等待 Shell 返回最终 answer。成功后解析 `result.answer`，失败后转换 ACP error。

## 16. “阻塞式”不等于阻塞 UI 线程

它表示协议请求直到答案产生才完成；实际请求运行在异步 task 中，Pager event loop 仍能刷新动画和处理输入。

## 17. Shell 如何找到 Session

扩展 handler 根据 `sessionId` 找 resident handle，发送 `SessionCommand::SideQuestion`，再等待 oneshot channel。这里要求目标 Session 已经驻留。

## 18. 为什么 `/btw` 不采用 load-race 容忍

它面向当前活跃、正在运行的 Session；如果 Session 已卸载或身份失效，返回错误比暗中加载一个可能已经过期的运行态更清晰。

## 19. 每次问题都有独立旁路 ID

Shell 创建 `btw-<uuid>`，同时保留 parent session id。前者标识本次辅助采样，后者表明它从哪个正式会话分叉。

## 20. 先准备采样客户端

`prepare_chat_completion(false)` 复用当前认证、backend 和模型访问方式。`false` 表明这里不是主 Turn 的完整准备路径。

## 21. 快照包含什么

`get_conversation()` 返回完整历史，包括 System prompt、用户消息、Assistant 回答、已完成的 Tool Call 和 ToolResult。旁路问题因此能理解“刚才讨论的那个函数”。

## 22. Backend 可能要求剥离 Reasoning

某些 Messages backend 不能在缺少匹配顶层 thinking 配置时重放 reasoning block。此时先 `strip_reasoning_blocks`；其他 backend 尽量原样保留前缀。

## 23. Mid-turn 快照可能结构不完整

主 Agent 可能刚输出带 `tool_calls` 的 Assistant item，但工具结果还没回来。把这个尾部直接交给 Provider，可能违反 Tool Call 与 ToolResult 的配对约束。

## 24. `pop_trailing_tool_run` 如何修尾

它从尾部连续删除：带 Tool Call 的 Assistant、ToolResult、Reasoning，直到遇到安全边界。其目标不是撤销主会话，只是让辅助请求的副本可被 Provider 接受。

## 25. 为什么连尾部 ToolResult 也要删

孤立 ToolResult 同样非法：它引用的 Tool Call 可能已经因截断不在有效尾段。与其猜配对关系，不如回退到上一个完整自然语言边界。

## 26. 然后追加 side-question instruction

临时 User item 告诉模型：这是独立轻量实例、主 Agent 未被打断、只回答一次、不要承诺后续动作、不知道就直说。

## 27. 为什么强调“不要说我被打断了”

模型看到完整上下文后容易延续主 Agent 人设，误说“我先暂停手头工作”。实际上父 Agent 仍在后台运行，这种表述会制造错误心智模型。

## 28. 为什么强调不会有后续 Turn

如果模型回答“让我检查一下”，它隐含需要工具结果或下一轮。one-shot 路径没有这个机会，因此 prompt 要求当前响应内直接给出可用答案。

## 29. 一个看似矛盾的设计：仍然发送 Tool Specs

Instruction 明令禁止工具，但 `ConversationRequest.tools` 并不为空。源码刻意复用主 Turn 的相同工具定义，以保持 Conversation 前缀的序列化形状和 Prompt Cache 命中机会。

## 30. Tool Schema 存在不等于工具会执行

客户端没有 sampler actor 的 Tool Call 执行循环。即使模型错误地产生 client tool call，也不会调用用户机器上的工具，更不会有下一轮读取 ToolResult。

## 31. Hosted Tool 是例外

Hosted search 由 Provider 在服务端完成，可能在单次 `conversation_collect` 内运行。因此“无工具循环”不等于“所有工具能力绝对关闭”。

## 32. Hosted Search 为什么仍带 Cutoff

`hosted_tools_for_turn()` 使用当前 Turn 的搜索限制。旁路调用不能绕过 active cutoff 去搜索主 Agent 被禁止访问的时间范围。

## 33. 更严谨的安全描述

`/btw` 不执行任何客户端工具；prompt 要求模型不要调用工具；Provider 托管工具仍可能服务端执行，并受当前 hosted tool 配置约束。

## 34. Glossary（二）：工具与采样

- **Tool Spec / Tool Schema**：告诉模型某个客户端工具名称及参数形状的定义。
- **Client tool loop**：模型发 Tool Call，客户端执行，再把 ToolResult 回传模型并继续采样的循环。
- **Hosted Tool**：Provider 服务端直接运行的工具，如托管搜索，不经过本机 Tool executor。
- **Trailing tool run**：Conversation 尾部连续的 Reasoning、Tool Call、ToolResult 片段。
- **Structurally valid**：消息角色、Tool Call id 与 ToolResult 等满足 Provider 协议约束。
- **Cutoff**：对搜索时间或知识范围设置的上界，防止辅助路径扩大权限。

## 35. `conversation_collect` 做什么

它把一次请求流完整收集为 `ConversationResponse`。相比主 sampler actor，它没有多轮 Agent loop，也不自动采用主请求的完整重试与恢复预算。

## 36. `/btw` 自己实现有限重试

策略总计最多三次尝试：首次加两次重试，退避约 500ms 到 1s并带 jitter。它刻意比主 Turn 的恢复机制更短。

## 37. 只重试 Overload

`should_retry_side_question` 只接受 overload，且必须没有共享 retry veto。普通 5xx、流错误、上下文过长不会在此无限扩散。

## 38. 为什么旁路重试必须保守

它是便利功能，不应在全局容量事件中把每个主 Session 额外放大成重试风暴，也不该抢占主工作请求的恢复预算。

## 39. 每次重试获得新 Request ID

base request 每次 clone 后重写 `x_grok_req_id`。这样日志、指标和 Provider 请求不会把多个物理尝试误认为同一事件。

## 40. 其余请求内容保持相同

除了 request id，每次尝试的 items、tools、model 和 reasoning effort 保持一致，便于复现、比较缓存和解释 attempts。

## 41. 空响应也是失败

采样成功但 `assistant_text()` 为空时，返回 `SideQuestionError::EmptyResponse`，而不是向 Pager 谎报成功并展示空白面板。

## 42. 成功与失败都会持久化

`BtwEntry` 写入 `btw_history.jsonl`，保存旁路 id、父 id、时间、问题、答案、模型、success、error 和 attempts。

## 43. 为什么失败也值得记录

它能回答“用户确实发起了吗”“重试了几次”“是模型空响应还是协议失败”，同时不会污染正式 Conversation。

## 44. 旧记录兼容

`attempts` 使用默认值 1 反序列化，字段加入前的 JSONL 仍可读取。这是 append-only 历史格式演进常见做法。

## 45. 回到 Pager 后的状态

`TaskResult::BtwResponse` 把 Loading 变为 Done 或 Error。Done 持有渲染后的 Markdown 和 scroll offset；用户按 Esc 后可将内容折叠进 scrollback。

## 46. 折叠进 Scrollback 仍非 Conversation

这是客户端视觉记录，不会自动通过 ACP Prompt 重新发送给 Shell。除非用户主动引用答案，否则主 Agent 不知道旁路模型说了什么。

## 47. Recap 与 `/btw` 从此处分叉

`/btw` 回答用户给定问题；Recap 由固定 instruction 自动提炼“做到哪里”，还需要 idle gate、watermark、epoch 和显示去重。

## 48. Recap Capability 先决定客户端是否可发

Shell 初始化 metadata 暴露 `sessionRecap`；Pager 缺失或非布尔值时按 false。客户端据此隐藏或拒绝不可用路径。

## 49. Shell Feature Gate 仍是权威

功能还受 config、remote setting 或环境配置控制。Pager gate 改善体验，但真正是否接受请求由 Shell 决定，不能只信客户端。

## 50. 手动入口是 `/recap`

别名 `/summarize` 生成 `Action::SendRecap { auto: false }`，同样绕过 Prompt queue。

## 51. 手动 Recap 先做空会话检查

若 replay 已结束且 scrollback 没有用户消息，Pager 直接显示 `No messages yet`，避免造出一个永远等不到内容的 spinner。

## 52. 为什么扫描 entries 而不是 `turn_count`

Session replay 的 batch 中，entry 已插入但 turns 可能尚未 rebuild。扫描实际 scrollback entries 能避开加载中间态的假零值。

## 53. 手动 Recap 建立 Running Block

Pager 立即插入带动画的空 Recap entry，并保存 `pending_recap_entry`。结果到达后原位填充，失败时原位删除。

## 54. 连续按 `/recap` 不叠加 Spinner

如果 pending entry 仍为 running，复用它。Shell 还有 `recap_in_flight` 防止重复采样，前后端形成双层收敛。

## 55. 自动 Recap 不显示 Spinner

自动路径是 best-effort。它只记录一次 auto attempt，等待真正 notification；未满足 Shell gate 时静默无结果，避免用户看到周期性失败。

## 56. Recap 的 ACP 请求只确认受理

`x.ai/recap` handler 发送 Session command 后立即返回 `{ ok: true }`。最终摘要通过后续 `SessionRecap` notification 广播。

## 57. 为什么不用 `/btw` 的同步返回

Recap 可能由 away timer 触发，客户端不应为一句展示信息长期占住 request-response 生命周期；异步通知也方便 replay 和 Session update 统一处理。

## 58. Recap 的两阶段协议

```text
Request accepted != recap generated
```

第一阶段只说明 command 已送达；第二阶段 notification 才携带 summary。请求错误和生成不可用是两类不同失败。

## 59. 自动触发的客户端 Gate

Pager 通常要求：Capability 开启、用户通知配置允许、away threshold 到期、Agent idle、没有 modal/question、Session 有效且无运行中的后台任务。

## 60. Shell 还有自己的 Idle Gate

自动 Recap 需要距最近 API request 至少约三分钟。客户端可能更早试探，Shell 静默 no-op，客户端之后依据 backoff 再试。

## 61. 为什么存在双层 Gate

Pager 知道窗口焦点和 UI 干扰；Shell 知道真实模型请求时间、主 Turn 数和持久化 watermark。单侧都看不到完整事实。

## 62. Main Turn 如何计数

只计算 `ConversationItem::User` 且 `synthetic_reason.is_none()` 的真实用户输入。内部提醒、自动生成指令和合成 User item 不应让 Session 看似更活跃。

## 63. 自动 Recap 至少需要三个 Main Turns

过短会话的信息量不足，自动弹一句摘要反而噪声大。手动 Recap 不受三轮限制，只要求至少一个真实用户 Turn。

## 64. Watermark 记录什么

Session 目录的 `last_recap_main_turn` 保存最近一次已提交 Recap 对应的主 Turn 数。自动路径要求当前计数严格大于它。

## 65. Watermark 防止同一历史反复摘要

用户多次离开又回来，但期间没有新 Prompt 时，不再重复生成相同 Recap。它是持久状态，重启 Session 后仍有效。

## 66. Watermark 超过当前计数怎么办

Compaction、rewind 或历史变化可能让旧 watermark 大于当前 main turns。Shell 会把它修复到安全值，而不是让自动 Recap 永久被锁死。

## 67. `recap_in_flight` 解决并发重复

这是 Actor 内的布尔锁：已有 Recap 采样时，新请求不再启动另一个。它保护的是昂贵模型调用，而不只是 UI spinner。

## 68. `recap_epoch` 解决过期结果

开始 Recap 前读取 epoch。若随后用户提交新 Prompt，Shell 增加 epoch；旧 Recap 即使完成，也不能再作为当前状态展示。

## 69. 为什么仅比较 Turn 数还不够

Recap 采样过程中 Prompt 可能已接受但尚未完整进入 Conversation，或发生其他状态变化。epoch 是显式的“快照已失效”令牌。

## 70. 新 Prompt 何时取消 Pending Recap

真实用户 Prompt 被接受或进入相关 fallback 路径时调用 `cancel_pending_recap_for_new_prompt`。目标是用户一开始新工作，旧“你刚才做到……”就不再抢占界面。

## 71. 手动与自动取消的 UX 不同

自动 Recap 过期可静默丢弃；手动 Recap 有 visible spinner，必须发送 `SessionRecapUnavailable` 让 Pager 清掉它。

## 72. Glossary（三）：Gate 与并发

- **Gate**：在执行昂贵或可见动作前检查的一组准入条件。
- **Idle threshold**：距离最近活动达到的最短空闲时间。
- **Main turn**：真实用户触发的会话轮次，不含 synthetic User item。
- **Watermark**：已处理到哪个主 Turn 的持久进度标记。
- **In-flight**：请求已经开始、尚未完成的状态。
- **Epoch**：随关键状态变化递增的版本号，用来拒绝旧异步结果。
- **Stale result**：基于旧快照产生、到达时已不再适合展示的结果。

## 73. Recap 也从 Conversation 快照开始

Shell 复制当前完整 Conversation，绝不在原数组末尾插入 Recap instruction。后续预算、修尾和剥离 Reasoning 都只操作副本。

## 74. Recap Instruction 要求什么

输出一句、约 25–40 个英文词，只给 body；问答型以“You asked…”开头，已落地修改以“We fixed/merged/wired…”等过去式开头，不用列表、标签或代码围栏。

## 75. 为什么 UI 自己添加 `Recap —`

模型只生成语义正文，Pager 统一负责外观标签。这样模型不容易重复输出 `Recap — Recap —`，手动与自动样式也保持一致。

## 76. Instruction 为什么放在末尾 User Item

如果另建或修改 System prompt，缓存前缀会在开头就分叉。保留父 Conversation 原样并只在末尾追加，Provider 更可能复用已计算的 KV prefix。

## 77. Fast Path 优先缓存命中

快照在预算内时，除 backend 必须 strip reasoning 外尽量逐项原样保留，只修剪不完整尾部并追加 instruction。

## 78. Recap 有保守上下文上限

有效窗口取实际模型窗口与 500,000 的较小值，再使用 85% 阈值并预留 4,000 tokens headroom 和 instruction 预算。

## 79. 为什么不把整个窗口吃满

Token estimator、Provider 序列化和真实 tokenizer 可能有差异。摘要是辅助功能，应主动留余量，避免与主工作争抢最后的上下文空间。

## 80. Over-budget Path 会牺牲缓存

一旦超预算，先准备可摘要历史、剥离 Reasoning、修剪不完整 Tool Run，再从前部裁剪到预算，保留 System 和最近上下文。

## 81. 为什么优先保留最近上下文

Recap 回答“刚才做到哪里”，近期工作比很早的寒暄更重要；System 则决定模型身份和行为，不能随意丢弃。

## 82. 修尾必须发生在裁剪前

若先按 token 裁剪，可能留下半个 Tool Run，再追加 User instruction 后形成非法角色序列。先规范边界才能安全计算和截取。

## 83. Recap 仍发送相同 Tool Specs

原因与 `/btw` 相同：保持父前缀缓存形状。Instruction 要求纯文本；客户端不运行 Tool Call loop；Hosted Tool 配置仍按当前 cutoff 镜像。

## 84. 模型与 Reasoning Effort 沿用当前 Session

辅助调用不暗中切换模型。否则输出风格、能力、缓存前缀乃至认证路径都可能与用户当前 Session 不一致。

## 85. Temperature 与 Max Output Tokens 保持未设置

部分代理会注入 thinking 配置，而 Messages API 对 temperature 有兼容要求。输出长度主要由严格 instruction 和后处理约束。

## 86. Prompt Cache Key 使用父 Session ID

这告诉支持显式 cache key 的 backend：辅助请求与父会话共享前缀缓存命名空间，而不是从冷缓存开始。

## 87. Conversation ID 根据 Backend 分支

会转发 `prompt_cache_key` 的 Responses 类 backend 可给辅助调用独立 `btw-*` 或 `recap-*` conv id；不转发 cache key 的 backend 必须保留父 session id 才能维持会话关联。

## 88. Session ID 与 Request ID 又各司其职

`x_grok_session_id` 保持父 id，便于归属和策略；`x_grok_req_id` 带辅助调用标签和 UUID，便于逐请求追踪。不要把缓存、会话、请求三个身份混成一个字段。

## 89. Cache Hit 会记录指标

成功响应若含 usage，日志记录 cached prompt tokens、prompt tokens，以及 backend 是否真的转发 cache key。后者能区分“没命中”和“根本没发送 key”。

## 90. Glossary（四）：缓存与请求身份

- **Prompt Cache / Prefix Cache**：Provider 复用相同 Prompt 前缀计算结果的机制。
- **KV Cache**：Transformer 对已处理 token 保存的 key/value 中间状态。
- **Prompt cache key**：帮助 Provider 将可复用前缀归入同一缓存命名空间的标识。
- **Conversation ID**：Provider 或代理用来关联一次会话序列的身份。
- **Session ID**：grok-build 中正式用户 Session 的身份。
- **Request ID**：一次物理 API 尝试的追踪身份；重试应有新值。
- **Cache shape**：items、tools 等序列化后构成的前缀结构；字段变化可能使缓存分叉。

## 91. Recap 响应先经过清洗

`clean_recap_text` 折叠多余空白、移除模型擅自添加的 leading label 和对称引号，再做 UTF-8 安全的字符上限裁剪。

## 92. 硬上限是 1200 字符

正常目标远短于此；上限用于防 runaway 输出。裁剪会加省略号，并确保不切断多字节字符。

## 93. 自动 Recap 还有 Long-tail 抑制

原始响应超过 500 bytes，或清洗结果触及硬上限并以省略号结尾时，自动 Recap 会保存但不显示。

## 94. 为什么保存却不显示

长输出对于“回到工作现场的一句话”体验不合格，但仍有调试价值。抑制 UI 与丢弃诊断证据是两回事。

## 95. Recap Artifact 保存什么

Session 的 `recap_requests/{request_id}.json` 记录触发方式、请求 ids、模型、exact items、是否 strip reasoning、reminder tag、原始响应、清洗摘要或错误。

## 96. Artifact 是 Best-effort

写诊断文件失败不应让已生成摘要变成产品失败；主逻辑记录 warning 后仍可继续提交和展示。

## 97. 提交必须再次检查 Epoch

`try_commit_recap(epoch, main_turns)` 只有在 epoch 未变化时才保存 watermark、清掉 in-flight 并允许结果进入通知路径。这是异步工作的 commit point。

## 98. Long-tail 抑制也提交 Watermark

模型已经成功处理了这段历史，只是输出不适合自动展示。若不推进 watermark，系统会对同一历史反复生成同样的坏摘要。

## 99. 失败与取消不推进 Watermark

没有得到可提交结果时保留旧进度，未来条件合适可重试。否则一次瞬时错误会永久跳过这段会话。

## 100. Notification 到 Pager 后仍要检查时机

若 live auto recap 到达时 Agent 已忙碌，Pager 丢弃晚到结果；若自最近 User Turn 后已经显示过 auto recap，也去重丢弃。

## 101. 为什么 Shell 检查后 Pager 还要检查

通知在进程和 event queue 中传播需要时间。Shell 发出时有效，不代表 Pager 应用时仍有效；接收端必须依据最新 UI 状态做最终裁决。

## 102. 手动结果原位替换 Spinner

`apply_recap_block` 优先填充 `pending_recap_entry` 并停止 running 动画；没有 pending entry 时再追加普通 SessionEvent block。

## 103. `SessionRecapUnavailable` 的职责很窄

它主要清理手动请求的 visible pending state。自动路径没有 spinner，因此 gate、取消或失败通常无需产生这条线上的噪声。

## 104. Replay 时为何忽略 Unavailable

Replay 应重建历史事件，不应因过去的一次临时失败在当前界面弹 toast。Unavailable 是 live UI 协调信号，不是持久业务内容。

## 105. 四条路径对照

| 维度 | 普通 Prompt | `/btw` | Recap | Compaction |
| --- | --- | --- | --- | --- |
| 是否进入主 Turn | 是 | 否 | 否 | 主状态维护流程 |
| 是否读取 Conversation | 是 | 快照 | 快照 | 是 |
| 是否改写 Conversation | 追加消息 | 否 | 否 | 是 |
| 客户端 Tool Loop | 是 | 否 | 否 | 通常无 |
| 结果进入模型历史 | 是 | 否 | 否 | 压缩摘要会进入 |
| 结果进入 UI | 正常 scrollback | overlay/折叠块 | Recap block | 通常不作为普通回答 |
| 返回协议 | Turn stream | 同步扩展响应 | ack + notification | 内部状态转换 |
| 主要持久化 | Session history | `btw_history.jsonl` | artifact + watermark | 压缩档案/Conversation |

## 106. Recap 绝不是 Compaction

Recap 为人生成一句位置提示；Compaction 为模型缩短上下文并改变后续采样输入。两者都会“总结”，但状态语义完全不同。

## 107. `/btw` 也绝不是 Subagent

Prompt 文案把它描述为 separate lightweight agent，是为建立行为边界；实现上没有 Child Session、task lifecycle、tool executor 或后续轮次。

## 108. 常见误解：旁路回答会帮助主 Agent

不会自动帮助。主 Conversation 在旁路前后不变，主 Agent 看不到 answer。若需要主 Agent 采用结论，用户必须再发送普通 Prompt 或系统显式设计桥接。

## 109. 常见误解：Tool Specs 应该置空才安全

置空能减少误调用概率，却改变缓存前缀。当前实现选择“保留定义 + 强 prompt + 不实现客户端 tool loop”，并明确 Hosted Tool 是服务端例外。

## 110. 常见误解：Ack 成功就是 Recap 成功

`x.ai/recap` 的 `{ok:true}` 只说明请求进入 Session command 通道。模型失败、被 gate、epoch 失效或输出被抑制都可能没有展示结果。

## 111. 常见误解：自动 Recap 没出现就是 Bug

可能是少于三轮、没有新 Turn、尚未满足 Shell idle、已在生成、Agent 又忙了、重复展示、epoch 失效或 long-tail 被抑制。应先看 gate 与 artifact。

## 112. 调试 `/btw` 的顺序

1. Pager 是否产生 `Effect::SendBtw`；
2. `x.ai/btw` 是否找到 resident session；
3. 修尾后的 items 是否仍有合法上下文；
4. backend、tools、hosted cutoff 是否正确；
5. 是否 overload retry、attempts 为几；
6. `btw_history.jsonl` 与 ACP error 是否一致；
7. TaskResult 是否匹配当前 Agent/request。

## 113. 调试 Recap 的顺序

1. Capability 和 feature gate；
2. Pager away/manual gate；
3. main turns、watermark、idle threshold；
4. `recap_in_flight` 与 epoch；
5. budget 后 items 与尾部结构；
6. `recap_requests` artifact 的 raw/clean/error；
7. watermark 是否提交；
8. notification 是否被 late/duplicate UI gate 丢弃。

## 114. 值得长期守住的测试不变量

- `/btw` 与 Recap 前后正式 Conversation 完全相等；
- mid-turn 未配对 Tool Call 不进入辅助请求尾部；
- 客户端 Tool Call 不被执行；
- hosted cutoff 与主 Turn 一致；
- `/btw` 仅 overload 最多三次且每次 req id 不同；
- 手动取消清 spinner，自动取消静默；
- 新 Prompt 使旧 epoch 结果不可展示；
- 失败不推进 watermark，成功与 long-tail suppress 推进；
- Recap budget 内保留 verbatim prefix，超预算仍结构合法；
- Recap block 不回写 Conversation。

## 115. Glossary（五）：输出、协议与持久化

- **ACP extension method**：在标准 Session 协议之外增加的命名请求，如 `x.ai/btw`、`x.ai/recap`。
- **Request/response**：调用方等待同一请求直接返回最终结果。
- **Fire-and-forget**：请求快速确认受理，实际工作不通过原调用同步返回。
- **Notification**：服务端主动推送的 Session update。
- **Artifact**：为诊断或复现保存的一次请求及结果文件。
- **JSONL**：每行一个 JSON 对象的追加式记录格式。
- **Commit point**：异步结果在再次验证状态后，正式更新持久进度或对外可见状态的时刻。
- **Long tail**：偏离期望分布、异常长但未必协议失败的输出。

## 116. 一句话记忆

`/btw` 和 Recap 都是在主 Conversation 旁边临时铺一条只读支线：复制父上下文、修复不完整尾部、追加一次性指令、复用缓存做 one-shot sampling，再把结果送往各自的 UI 与审计存储；支线结束后，主 Conversation 一字不改。

## 117. 复习问题

1. 为什么 `/btw` 必须绕过普通 Prompt queue？
2. Snapshot 与 Child Session 的本质差异是什么？
3. 为什么 mid-turn 辅助请求要删除 trailing tool run？
4. Tool Specs 非空为什么仍可称为“没有客户端工具循环”？
5. Hosted Tool 为什么是需要单独说明的例外？
6. `/btw` 为什么只重试 overload，且每次更换 request id？
7. Recap 的同步 ack 和最终 notification 分别表示什么？
8. Main turn、watermark、in-flight 和 epoch 各解决什么问题？
9. 为什么 Recap 在预算内保留原始前缀，超预算才剥离和裁剪？
10. Prompt cache key、conversation id、session id、request id 为什么不能混用？
11. 为什么 long-tail 自动 Recap 被抑制后仍推进 watermark？
12. Recap 与 Compaction 都会总结，它们对 Conversation 的影响为何相反？

