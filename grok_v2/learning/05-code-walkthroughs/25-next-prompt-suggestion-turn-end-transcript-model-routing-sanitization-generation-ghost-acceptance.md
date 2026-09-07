# Walkthrough：一次 Next-Prompt Suggestion 如何从 Turn End 变成可接受的 Ghost Text

> 场景：Agent 完成“修复问题并运行测试”中的修复部分，Prompt 输入框此时为空。Pager 在后台请求模型预测用户最可能输入的下一句话，例如“run the tests”，并用灰色 ghost text 显示。用户可以按 Tab 或右方向键接受，也可以继续打字、按 Esc 忽略。系统必须控制每轮附加成本，避免建议旧问题、Agent 口吻或大段文本，还要确保迟到响应不会覆盖新 Turn 的输入状态。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
Clean agent Turn completes
  -> Pager handle_prompt_response
  -> clear previous suggestion + invalidate old generation
  -> strict fetch gate
  -> PromptSuggestionController::begin_fetch
  -> Effect::FetchPromptSuggestion
  -> ACP x.ai/suggestPrompt
  -> Shell SessionCommand::SuggestPrompt
  -> resolve dedicated small model
  -> snapshot Conversation
  -> build compact text-only transcript
  -> System instruction + CWD + transcript
  -> conversation_collect（tools = []）
  -> sanitize + deterministic repeat filter
  -> ACP response { suggestion, generation }
  -> TaskResult::PromptSuggestionLoaded
  -> generation guard + install
  -> per-frame visibility gate
  -> dim ghost suffix
  -> Tab / Right accepts remainder; Esc dismisses
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| Turn End 触发 | `xai-grok-pager/src/app/dispatch/prompt.rs` | `handle_prompt_response` |
| Pager Controller | `xai-grok-pager/src/views/prompt_suggestion.rs` | `PromptSuggestionController` |
| Prompt 接入 | `xai-grok-pager/src/views/prompt_widget/mod.rs` | ghost、accept、render |
| 输入优先级 | `xai-grok-pager/src/app/agent_view/prompt.rs` | Tab、Right、Esc |
| 显示 Gate/Telemetry | `xai-grok-pager/src/app/agent_view/cta.rs` | refresh、shown latch |
| ACP Effect | `xai-grok-pager/src/app/effects/mod.rs` | `FetchPromptSuggestion` |
| Shell 扩展 | `xai-grok-shell/src/extensions/suggest/mod.rs` | `handle_suggest_prompt` |
| Session 采样 | `xai-grok-shell/src/session/acp_session_impl/recap.rs` | `handle_suggest_prompt` |
| Transcript 与过滤 | `xai-grok-shell/src/session/helpers/prompt_suggest.rs` | build、sanitize、repeat |
| 模型覆盖 | `xai-grok-shell/src/config/mod.rs` | `PromptSuggestModelPin` |

## 3. 先区分两种 Suggestion

仓库里同时存在 shell command suggestion 和 next-prompt suggestion。前者服务 Bash 模式的命令、历史和路径补全，走 `x.ai/suggest`；本文讨论后者，预测自然语言下一 Prompt，走 `x.ai/suggestPrompt`。

## 4. 两种 Ghost Text 为什么容易混淆

它们都显示在光标后、都可由 Tab 接受，却由不同 Controller、不同 Provider、不同触发时机和不同输入模式拥有。

## 5. Next-Prompt Suggestion 不是自动 Prompt

它只向 TextArea 提供候选文本。接受后也只是把文字插入编辑器，不会自动提交、不创建 Turn，也不会执行建议中的动作。

## 6. 这是推测，不是计划

模型回答的是“用户大概率会输入什么”，不是“系统下一步应该做什么”。Prompt 中甚至用测试问题约束：用户是否会觉得“我正准备输入这句”。

## 7. 为什么在 Turn End 才触发

只有 Agent 最新回复完成后，系统才有足够上下文预测后续。Turn 进行中生成会依据半截回答，且可能与最终结论相反。

## 8. Glossary（一）：功能边界

- **Next-prompt prediction**：预测用户下一条自然语言输入的后台模型调用。
- **Ghost text**：未真正进入编辑器内容、以弱化样式显示的候选后缀。
- **Shell completion**：针对命令、路径或历史的 Bash 输入补全，与自然语言建议不同。
- **Acceptance**：把候选后缀插入编辑器；不等于发送。
- **Turn boundary**：一次 Agent Turn 开始或结束时的状态边界。
- **Prediction**：对用户行为的概率性猜测，不是 Agent 的行动承诺。

## 9. Turn End 先清理旧建议

`handle_prompt_response` 在多个 early return 之前调用 `prompt_suggestion.clear()`，删除上轮 ghost 并递增 generation。

## 10. 为什么清理要放在 Early Return 之前

Reconnect pending、额度失败等路径可能提前结束函数。如果先 return，旧建议会跨 Turn 残留，看起来像是针对最新回答生成的。

## 11. `clear` 同时让网络响应过期

它不只清空 `full_text`，还增加 generation。已经在路上的旧请求即使成功返回，也无法重新安装旧建议。

## 12. 只有成功 Turn 才考虑生成

Fetch gate 要求 Prompt result 成功。错误之后用户通常需要先读报错并判断，不应由模型替用户抢答下一步。

## 13. Cancelled Turn 不生成

用户主动中断已经表达了意图变化。此时预测“继续”既冒进，又可能重新推动用户刚刚停止的工作。

## 14. Bash Turn 不生成自然语言建议

`was_bash_turn` 会关闭这条管线。Bash 模式由专门的 shell suggestion 系统负责，避免两种 ghost 在同一输入语义上竞争。

## 15. Prompt 必须为空

Turn 完成时如果用户已写下 draft，说明真实意图已经开始形成。系统不再花钱预测，也不让候选覆盖用户的半成品。

## 16. 本地 Prompt Queue 必须为空

`pending_prompts` 中已经有明确下一步，不需要再猜。生成建议只会增加成本和视觉噪声。

## 17. Shared Queue 也必须为空

多客户端或服务端权威队列可能已经安排后续 Prompt。只检查 Pager 本地队列会在分布式状态下误判“没有下一步”。

## 18. Session 必须真正 Idle

Gate 位于 `maybe_drain_queue` 之后。若队列刚提升了下一条 Prompt，Session 会重新变为 running，建议请求自然不触发。

## 19. Session ID 必须存在

`x.ai/suggestPrompt` 需要把请求路由到正确 SessionActor。尚未创建正式 Session 时不能仅凭 Pager Agent id 猜测目标。

## 20. Feature Setting 必须开启

`[ui].prompt_suggestions` 默认开启，可在 Settings 中动态切换。Pager 在 Turn End 读它决定是否 fetch，在显示前还会再次读取。

## 21. 环境变量拥有最高 Feature 优先级

`GROK_PROMPT_SUGGESTIONS=0/1` 覆盖持久设置，并由 `OnceLock` 缓存进程级解析结果。

## 22. Setting 改动如何持久化

Pager 先更新 appearance cache 和当前 UI 镜像，再发 `Effect::PersistSetting` 让 Shell 写回 `[ui].prompt_suggestions`；失败时设置框架可回滚。

## 23. 完整 Fetch Gate

```text
enabled
AND result.is_ok
AND not cancelling
AND not bash turn
AND prompt empty
AND local queue empty
AND shared queue empty
AND session idle
AND session id exists
```

它体现了一个原则：只有用户尚未表达下一步且系统也没有已知工作时，预测才有价值。

## 24. `begin_fetch` 分配 Generation

Controller 用 wrapping counter 生成本次版本号。这个值随 Effect、ACP request 和 TaskResult 一路传播。

## 25. Generation 不是 Request ID

Generation 表达“这个响应是否仍属于当前 UI 草稿时代”；Request ID 表达“哪一次网络请求”。它们解决不同问题。

## 26. Pager 同时解析模型 Hint

客户端优先使用 `GROK_PROMPT_SUGGESTIONS_MODEL`；否则只有 Catalog 包含 `grok-build-0.1` 时才发送该 hint；否则传 `None`，让 Shell 做权威解析或跳过。

## 27. Effect 为什么携带 Agent ID

同一个 Pager 可管理多个 Agent。结果回来时必须安装到发起请求的 Agent Controller，而不能按当前 active view 猜测。

## 28. ACP Request 包含什么

`x.ai/suggestPrompt` 发送 generation、可选 model hint 和 sessionId。它不发送 Pager 当前 Prompt 文本，因为正常 fetch gate 已要求其为空。

## 29. ACP 请求有 45 秒 Timeout

小模型冷缓存或 reasoning 仍可能耗时较长，所以 timeout 不是极短交互阈值；但后台预测也不能永久占住 oneshot。

## 30. Timeout 为什么仍可设得较宽

Turn 结束后用户通常在阅读回答。只要 generation 与输入 Gate 能丢掉过时结果，稍晚但仍相关的建议依旧有价值。

## 31. 找不到 Session 时返回什么

Shell 不把它升级为主流程错误，而是返回 `suggestion: null`。这是 best-effort 功能，失效应退化为普通空 Prompt。

## 32. Command Channel 关闭也静默退化

发送失败、responder dropped、timeout 都记录 debug 并返回 `None`。Pager 不展示 toast，不打断用户。

## 33. Shell Command 在 Actor 外异步执行

Run loop 收到 `SessionCommand::SuggestPrompt` 后 `spawn_local` 调用 handler，再通过 oneshot 返回。长达数十秒的辅助采样不会卡住命令接收循环。

## 34. Glossary（二）：异步身份

- **Generation counter**：表示 UI 草稿/请求世代的递增版本号，用于拒绝迟到结果。
- **Request ID**：一次物理模型请求的追踪标识。
- **Agent ID**：Pager 内目标 Agent view 的身份。
- **Session ID**：Shell 中持有 Conversation 和运行态的会话身份。
- **Oneshot channel**：只发送一次结果的异步通道。
- **Stale response**：对应旧 generation、已不再适合当前 UI 的响应。
- **Best-effort**：失败时安静放弃，不影响主功能正确性。

## 35. Shell 为什么再次解析模型

客户端 Catalog 只是提示，Shell 才掌握真实认证、remote setting、本地 config 和可采样 Catalog。模型决策不能由 UI 单方面决定。

## 36. 模型优先级

```text
GROK_PROMPT_SUGGESTIONS_MODEL
  > [models].prompt_suggestion
  > remote prompt_suggestion_model
  > client model hint
  > grok-build-0.1 default
```

本地 config 在 resolve 阶段阻止 remote 覆盖，随后环境变量再覆盖二者。

## 37. 为什么使用 `PromptSuggestModelPin` 枚举

普通 `Option<String>` 分不清“来自环境变量的强制逃生口”和“需要 Catalog 校验的普通 pin”。枚举保留来源语义。

## 38. `Env` Pin 绕过 Catalog Guard

环境变量被视为操作者明确指定，允许使用未列在 Catalog 的测试或内部模型；最终能否请求成功仍由 Provider 决定。

## 39. 其他层级必须在 Catalog 中

本地/remote pin、client hint 和默认模型若不可采样，`effective_suggest_model` 返回 `None`，整个请求直接跳过。

## 40. OAuth 用户为何常被跳过

默认 `grok-build-0.1` 可能只出现在 API-key Catalog，不在 OAuth Catalog 中。与其每轮发一个注定失败的请求，系统把成本降为零。

## 41. 绝不回退到 Session Model

这是代码当前的重要不变量：Suggestion 是每个合格 Turn 都可能发生的附加调用，回退到昂贵 reasoning model 会放大延迟和费用。

## 42. 为什么客户端仍发 Model Hint

Pager 能从自己收到的 Catalog 选择偏好小模型；Shell 配置没有 pin 时可采用该提示。但它只是较低优先级建议。

## 43. 一处应以实现为准的注释差异

`SessionCommand::SuggestPrompt` 附近旧注释提到可能使用 Session model，但实际 `effective_suggest_model` 明确不会这样做，测试也守住该行为。阅读代码时应交叉核对 consumer。

## 44. 先读取 Conversation Snapshot

模型选定后，Shell 从 ChatState 获取当前正式 Conversation。但它不会像 Recap 那样把完整 items 原样发给 Provider。

## 45. 为什么不用父 Prompt Cache

Suggestion 固定走独立小模型，往往与 Session model 不同，父模型的 KV cache无法复用。此时缩小输入比维持完整 prefix shape 更划算。

## 46. Transcript 只保留两种角色

真实 User 消息变成 `User: ...`，Assistant 文本变成 `Agent: ...`。System、Reasoning、Tool Call 和 ToolResult 全部丢弃。

## 47. Synthetic User 消息被跳过

内部提醒、Goal continuation 等合成内容并不代表用户写作风格或真实意图，把它们当用户样本会让预测偏向系统语言。

## 48. 为什么 Tool Result 不进入 Transcript

原始日志、文件内容和 Diff 通常体积大；用户下一步更依赖 Agent 如何总结结果，而不是工具的全部原始输出。

## 49. 每条消息最多约 1500 Bytes

常量命名为字符 cap，但实现使用 UTF-8 `len()` 并通过 `floor_char_boundary` 安全切割，因此实际预算更接近字节数，同时不会截断多字节字符。

## 50. 总 Transcript 预算约 24,000 Bytes

源码按 bytes/4 粗估约 6K tokens。它从最新消息向旧消息回走，预算满后停止，再反转为正常时间顺序。

## 51. 为什么从后向前收集

近期 User 意图和 Agent 结果对“下一句”信号最强。长会话预算有限时，应优先保留最新完整对话。

## 52. 第一条超预算 Line 仍可能保留

判断只有在已有 lines 时才 break，保证极端情况下仍留下至少一个近期候选行，而不是因单条偏长直接产生空 transcript。

## 53. 必须至少见到 Assistant 文本

没有 Agent 最新回复就没有“Turn 完成后的下一步”可预测。`build_transcript` 因此在未见有效 Assistant line 时返回 `None`。

## 54. Transcript 不是 Conversation

它是有损、纯文本、专为预测任务构造的派生输入；没有 Tool Call id、结构化 Content Part 或正式角色对象的完整语义。

## 55. CWD 作为额外上下文

User message 在 Transcript 前加入当前工作目录，帮助模型理解项目或路径语境，但不会把整个文件系统状态注入请求。

## 56. 独立 System Prompt 定义任务

它要求模型预测用户口吻、优先具体的明显后续、Agent 问问题时预测回答，并在不明显时只输出 `NONE`。

## 57. Prompt 刻意区分“会输入”与“应该输入”

一个工程上合理的下一步不一定符合用户习惯。预测型 UI 若不断教育用户，会变成干扰型推荐系统。

## 58. Prompt 禁止重新建议旧请求

Transcript 是历史证据，不是菜单。已处理的原始任务不应原样再次出现在 ghost 中，除非是 `yes`、`continue` 之类天然重复的短确认。

## 59. Prompt 允许返回 `NONE`

“不显示”是一等结果。没有明显下一步时保持空白，比勉强制造一个建议更符合编辑器辅助功能。

## 60. Glossary（三）：Transcript 与预算

- **Compact transcript**：从正式 Conversation 派生的短文本对话记录。
- **Lossy / 有损**：为了成本和任务相关性，主动丢弃部分原始信息。
- **Synthetic message**：运行时内部生成、并非用户真实输入的消息。
- **Character boundary**：UTF-8 中不会切进某个多字节字符内部的合法切点。
- **Token estimate**：用字节或字符近似真实 tokenizer 数量的预算方法。
- **Signal**：对当前预测任务真正有帮助的信息。
- **Cold cache**：目标模型没有可复用前缀计算、需要从头处理请求的状态。

## 61. Request 中真正只有两个 Items

一个专用 System instruction，加一个含 CWD 和 transcript 的 User item。正式 Conversation 本身不会随请求发送。

## 62. Tools 明确为空

与第 24 篇 `/btw`、Recap 为缓存形状保留 Tool Specs 不同，Suggestion 请求 `tools: vec![]`，没有客户端或 Hosted Tool 需求。

## 63. 为什么这里能安全删掉 Tools

它没有父 Prompt Cache 复用目标，且任务只需输出短文本。保留工具只会增加 tokens 和模型漂移机会。

## 64. Conversation ID 是全新 UUID

`promptsuggest-<uuid>` 标识独立的一次预测对话，不冒充父 Conversation，也不建立可继续的支线 Session。

## 65. Request ID 也是全新 UUID

`xai-promptsuggest-<uuid>` 用于日志和追踪。当前路径没有 `/btw` 那样的显式重试循环。

## 66. Parent Session ID 仍被保留

`x_grok_session_id` 指向正式 Session，用于归属、Telemetry 和后端策略；它不意味着响应会写回该 Session 的 Conversation。

## 67. Temperature 保持未设置

代理和 Provider 可能有自己的模型兼容默认值。辅助功能不强行设置一个会与 thinking 配置冲突的 temperature。

## 68. Max Output Tokens 也保持未设置

Reasoning model 可能把很小 output cap 全耗在内部 reasoning，最终正文为空。严格 System prompt 与 sanitizer 承担输出收敛职责。

## 69. Reasoning Effort 不显式传递

一些候选模型不接受该字段或会返回 400；Suggestion 也不应继承主 Session 的昂贵 reasoning 设置。

## 70. 采样仍是 One-shot

`conversation_collect` 收集一次 Assistant response；没有 Tool Loop、自动追问或把结果写入 ChatState。

## 71. 失败统一返回 `None`

准备客户端失败、模型请求失败、空 transcript、无可用模型和输出过滤失败，对用户都表现为没有 ghost。

## 72. 为什么不显示错误 Toast

用户没有显式请求这个预测。后台便利功能若失败却主动弹错误，会比缺少建议更打断工作。

## 73. 第一层输出处理：只取第一行

模型若漂移为多行，sanitizer 只检查首行；后续解释不会被插入 Prompt。Controller 仍再次拒绝包含换行的 payload。

## 74. 去掉常见引号 Wrapper

模型可能输出 `"run the tests"` 或反引号包裹；过滤器删除对称场景下常见的首尾引号字符，让候选适合直接编辑。

## 75. 空值与 Meta 值被拒绝

`NONE`、`N/A`、`no suggestion`、`null`、`silence` 等都映射为 `None`，不会作为字面 ghost 显示。

## 76. Markdown 被拒绝

星号、代码围栏、开头标题或列表标记说明模型没有遵循“单行用户输入”契约，直接丢弃比试图修复更稳妥。

## 77. Agent Voice 被拒绝

以 `I'll`、`I will`、`Let me`、`Here's` 等开头的输出属于 Agent 口吻，不应伪装成用户下一句话。

## 78. Label Prefix 被拒绝

`Suggestion: run tests`、`User: continue` 等带单词标签的格式不会显示。UI 需要候选正文，不需要模型解释字段名。

## 79. 多句输出被拒绝

过滤器查找句末标点、空格、下一大写字母的模式，阻止明显的多句 Agent prose 进入单行 ghost。

## 80. 最大长度与词数

建议必须短于 120 bytes，并且不超过 16 个空白分词。Prompt 目标更严格，为 2–12 words；代码上限提供少量漂移空间。

## 81. 单词建议必须进 Allowlist

`yes`、`no`、`continue`、`commit`、`push`、`retry`、`undo` 等短确认可接受；任意孤立单词通常信息不足，会被拒绝。Slash command 例外允许。

## 82. 第二层确定性 Anti-repeat

Sanitize 后，Shell 把四词及以上建议与所有真实历史 User message 做大小写、空白和尾部标点归一化比较；完全相同则拒绝。

## 83. 为什么只拦四词以上

`yes`、`run tests`、`try again` 在同一 Session 中重复可能完全合理。主要故障模式是模型把较长旧任务当成候选重新吐出。

## 84. Prompt Rule 与 Deterministic Filter 的关系

Prompt 提高总体质量，代码过滤守住明确不变量。只靠 Prompt 无法保证模型每次都服从；只靠过滤也难以生成自然建议。

## 85. Glossary（四）：输出契约

- **Sanitizer**：把模型自由文本验证并收敛成 UI 可接受格式的确定性函数。
- **Allowlist**：明确允许通过的一组有限值。
- **Agent voice**：以 Agent 行动者身份说话的措辞，与用户输入口吻相反。
- **Meta reply**：描述“没有答案”或输出格式的文字，而非实际候选。
- **Deterministic backstop**：不依赖模型服从度、由代码保证的最后防线。
- **Normalization**：忽略大小写、重复空白或尾部标点后进行语义较弱但稳定的比较。
- **Output contract**：模型结果进入下游前必须满足的格式与内容边界。

## 86. ACP Response Echo Generation

Shell 返回 `{ suggestion, generation }`，让协议层保留请求世代。Pager Effect 自己也闭包捕获 generation，并把它放入 `TaskResult`。

## 87. 网络错误与空建议走同一路径

ACP 失败时 Effect 生成 `suggestion: None`。Controller 根据 generation 接收后清空候选，不把后台失败升级成界面错误。

## 88. TaskResult 按 Agent ID 路由

只有对应 Agent 仍存在时才处理结果。用户关闭 Agent 后返回的预测不会安装到其他视图。

## 89. `on_loaded` 首先比较 Generation

不相等立即返回，连已有 `full_text` 都不改。一个旧的空响应也不能清除更新一代的有效建议。

## 90. Controller 再次验证单行非空

即使 Shell sanitizer 已经执行，Pager 仍拒绝 whitespace-only 和含换行 payload。这是跨协议边界的 defence in depth。

## 91. 安装建议会重置 Dismissed

新 generation 的有效建议设置 `full_text`，清掉 dismissed，并重新武装 shown telemetry latch。

## 92. Hidden-at-load 仍可安装

响应到达时用户可能已经输入一个与建议不匹配的 draft。Controller 保存候选但 `ghost_for` 返回 None；用户清空输入后，同一建议可能再次可见。

## 93. 为什么不在每次按键时改写候选

Visibility 是纯派生：若当前 text 是 `full_text` 的 proper prefix，就显示剩余 suffix。这样不会维护一套容易漂移的逐键状态机。

## 94. 匹配输入会自然缩短 Ghost

建议为 `run the tests`，用户输入 `run ` 后，`strip_prefix` 得到 `the tests`。完整文本始终保留，候选不会因逐键删除而丢失原值。

## 95. 分歧输入会隐藏 Ghost

用户输入 `review` 时不是建议前缀，`strip_prefix` 失败。系统尊重真实输入，不试图在中间替换或纠正。

## 96. 完整输入后 Ghost 消失

当前 text 等于 `full_text` 时 remainder 为空，因此不再显示。这不代表自动发送，只表示用户已经自己写完同一句。

## 97. 光标必须在文本末尾

Ghost 是“继续当前文字”的视觉隐喻。光标移到中间后仍画在末尾或光标处都会产生错误插入预期，所以直接隐藏。

## 98. Session 与 Prompt Mode Gate

每帧刷新要求 Session 不 busy、`PromptInputMode::Normal` 且 `PromptMode::Normal`。Turn 开始、进入 Bash/Remember/Feedback 或 Queue Edit 时立即隐藏。

## 99. 其他补全 UI 拥有更高优先级

已有 shell ghost、文件搜索、历史搜索、Slash menu 或 Slash inline ghost 时，next-prompt ghost 让位，防止一行出现两个候选来源。

## 100. Voice Interim 也拥有渲染区域

语音识别 interim 文本出现时，render path 跳过两类 ghost，避免预测与尚未定稿的语音文字重叠。

## 101. Ghost 只画可用宽度

Renderer 从光标位置计算当前行剩余 cells，按可见宽度截取，并使用主题的 dim ghost style。未显示部分不影响完整 Controller 候选。

## 102. Tab 与 Right 接受候选

这沿用 fish/zsh autosuggestion 习惯：Ghost 只在末尾可见，此时 Right 本来没有常规右移效果，适合用作接受键。

## 103. 接受时只插入 Remainder

当前已输入前缀不会被重写。Controller 返回 `ghost_for(text)` 的剩余部分，TextArea 在光标末尾插入它。

## 104. 接受后 Controller 清空

同一建议不会在后续编辑中重新出现。Prompt 现在包含完整文本，但仍等待用户按 Enter 确认发送。

## 105. Esc 只在空 Prompt 时 Dismiss

空输入下 Esc 明确表示“不要这个建议”；非空 Draft 时 Esc 还有清理、取消和模式退出等既有语义，Suggestion 不抢占。

## 106. Dismiss 的生命周期

它设置 dismissed，使当前候选在本 Turn 剩余时间不可见；下一次有效 loaded suggestion 会重新清除 dismissed。

## 107. 为什么 Dismiss 不等于 Feature Disable

Dismiss 是对一次候选的局部反馈；Settings 或环境变量才控制未来每个 Turn 是否继续生成。

## 108. Glossary（五）：编辑器状态

- **Derived visibility**：由当前文本和 Gate 即时计算是否显示，而非单独维护 visible 状态。
- **Proper prefix**：比完整候选短、且与候选开头完全相同的文本。
- **Suffix / remainder**：去掉已输入前缀后尚未写入的候选部分。
- **Dismiss**：隐藏当前候选，但不关闭整个功能。
- **Invalidate**：通过 generation 变化使在途结果失去安装资格。
- **Input ownership**：多个 UI 功能竞争同一按键或显示区域时的优先级规则。
- **Defence in depth**：Shell 与 Pager 分别验证同一安全/格式边界。

## 109. Telemetry 只记录尺寸

`PromptSuggestion` 事件记录 shown、accepted、dismissed，以及完整建议的字符数和词数，不记录候选文本内容。

## 110. 为什么 Shown 不能在 Loaded 时无条件记录

响应可能藏在 divergent draft 或关闭的 Gate 后，从未真正出现在屏幕上。把 loaded 当 shown 会虚增曝光量并压低接受率。

## 111. Shown Latch 何时触发

加载后和每次关键输入/Gate 刷新后检查实际 visibility；第一次可见时 `mark_shown_logged` 返回 true，此后同一候选不重复记录。

## 112. 为什么先记录 Shown 再处理 Tab/Esc

用户可能在建议刚可见的同一次输入路径立即接受或忽略。先 latch shown 能保证漏斗中 accepted/dismissed 不超过 shown。

## 113. Accepted 统计完整文本尺寸

即便用户先手输了一部分，接受事件仍以最终完整建议计算 chars/words，使 shown 与 accepted 的分桶口径一致。

## 114. Dismissed 只记录可见空输入候选

Esc handler 在 ghost 可见且 Prompt 为空时运行，因此记录的尺寸就是完整 ghost，不会把隐藏或部分候选误算成主动拒绝。

## 115. 这条链路不修改正式 Conversation

Transcript 来自只读快照，Suggestion response 只进入 Pager Controller。只有用户接受、检查并按 Enter 后，文本才作为下一条普通 Prompt 进入模型历史。

## 116. 与 `/btw`、Recap 的对照

| 维度 | `/btw` | Recap | Next-Prompt Suggestion |
| --- | --- | --- | --- |
| 触发 | 用户命令 | 用户/away timer | 合格 Turn End |
| 输入 | 完整父快照 + 问题 | 完整/裁剪父快照 + instruction | 有损纯文本 transcript |
| 模型 | Session model | Session model | 独立小模型，绝不回退 Session model |
| 父缓存 | 尽量复用 | 尽量复用 | 不追求复用 |
| Tools | 保留 specs，无 client loop | 保留 specs，无 client loop | 空 |
| 输出 | 旁路答案 | 一句状态摘要 | 用户下一句话候选 |
| UI | Overlay | Scrollback block | Prompt ghost |
| 进入 Conversation | 否 | 否 | 接受并发送后才会 |

## 117. 为什么这不是浪费一次完整模型调用

系统通过严格 Turn gate、专用小模型、24 KB transcript 上限、无工具、可跳过的 Catalog guard 和静默失败，把每轮附加成本限制在可控范围。

## 118. 常见误解：Suggestion 会读取整个代码库

不会。请求只有 CWD 字符串和对话 transcript，没有文件工具、搜索工具或代码库内容扫描。

## 119. 常见误解：建议来自当前 Session Model

不会。即使默认小模型不可用，当前实现也选择跳过，而不是用 Session model 顶替。

## 120. 常见误解：迟到结果一定会覆盖 Draft

不会覆盖文本。Generation 防旧 Turn 响应；同代响应若遇到 divergent draft 只被隐藏，Controller 从不主动替换 Draft。

## 121. 常见误解：Tab 接受后任务立即执行

接受只插入 remainder。用户仍可编辑、删除或放弃，直到显式提交才创建普通 Prompt。

## 122. 常见误解：`NONE` 是一个可见建议

不是。它是模型声明没有高质量预测的协议值，sanitizer 将其转成 `None`。

## 123. 调试“Turn 后没有建议”的顺序

1. Feature setting/env 是否开启；
2. Turn 是否成功、非取消、非 Bash；
3. Prompt/local queue/shared queue 是否为空；
4. Session 是否 idle 且有 session id；
5. 小模型是否通过 Shell Catalog guard；
6. Transcript 是否含 Assistant 文本；
7. ACP 是否在 45 秒内返回；
8. Sanitizer/repeat filter 是否拒绝；
9. Generation 是否仍匹配；
10. 当前 Draft、光标和其他 completion UI 是否隐藏 ghost。

## 124. 调试“建议错误覆盖”的顺序

先确认是视觉 ghost 还是 TextArea 真值；再看 generation 是否在 Turn boundary、Session switch 和 clear 时递增；最后验证 accept 只插 remainder，且 divergent draft 的 `ghost_for` 返回 None。

## 125. 值得长期守住的测试不变量

- 失败、取消、Bash Turn、非空 Draft 或任一 Queue 非空时不 fetch；
- Turn boundary 无条件清旧候选并使旧 generation 失效；
- 默认/普通 pin 不在 Catalog 时跳过，绝不回退 Session model；
- transcript 跳过 System、Tool、Reasoning 和 synthetic User；
- 没有 Assistant 文本时不采样；
- request 无 tools 且不修改 Conversation；
- sanitizer 拒绝 meta、Markdown、Agent voice、长文和旧长 Prompt；
- stale generation 不安装，也不清除新候选；
- matching prefix 只缩短 ghost，divergence 隐藏；
- shell/file/history/slash/voice UI 优先于 next-prompt ghost；
- accept 只写入 remainder，不自动发送；
- shown 只在首次真实可见时记录且不采集文本。

## 126. 一句话记忆

Next-Prompt Suggestion 是 Turn End 后的一次低成本、可放弃预测：Pager 只在“用户和队列都还没表达下一步”时发请求，Shell 把 Conversation 压成近期纯文本并路由给可用小模型，确定性过滤结果后由 generation 守住异步时序，最终只以可撤销、不会自动发送的 ghost suffix 出现在编辑器中。

## 127. 复习问题

1. Next-prompt suggestion 与 Bash shell completion 如何区分？
2. 为什么 Turn End 要先 clear，再经过 fetch gate？
3. 本地 Queue 和 Shared Queue 为什么都必须为空？
4. Generation 与 Request ID 分别解决什么问题？
5. 模型选择为何要保留 `Env`、`Pinned`、`Unpinned` 三种来源？
6. 默认小模型不在 Catalog 时为什么跳过，而不是使用 Session model？
7. 为什么本功能构建 transcript，而 Recap 尽量保留完整父 prefix？
8. Transcript 为什么排除 ToolResult、Reasoning 和 synthetic User？
9. Prompt 规则与 sanitizer/anti-repeat filter 如何分工？
10. Hidden-at-load 的建议为什么仍可能在清空 Draft 后出现？
11. Tab 接受为什么只插入 remainder，而且不自动提交？
12. Shown telemetry 为什么必须在第一次实际可见时才记录？

