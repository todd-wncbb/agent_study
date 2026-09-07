# Walkthrough：一次 Session Model Switch 如何校验、重建 Harness 并更新采样与认证

> 场景：用户在已有 Session 中选择另一个模型，并可能调整 reasoning effort。Shell 不会只替换 model 字符串：它先验证模型目录与允许列表，检查目标模型要求的 Harness 是否兼容，必要时在零 Turn 阶段重建 Agent；然后由 SessionActor 串行更新采样配置、Context Window、压缩阈值、Backend Search、凭据、认证缓存和 System Prompt，最后持久化并广播结果。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
ACP session/set_model
  -> MvpAgent::set_session_model
     -> resolve_model_id
     -> user_selectable / allowed_models gate
     -> model_switch::apply
        -> wait Session loaded
        -> resolve target ModelEntry + required agent_type
        -> query active_agent_type + turn_count
        -> harness compatibility gate
           compatible           -> continue
           mismatch + history   -> reject; suggest new Session
           mismatch + zero-turn -> discover definition + rebuild Agent
        -> prepare target SamplerConfig
        -> validate reasoning_effort
        -> recompute auto-compact threshold
        -> SessionCommand::SetSessionModel
           -> SessionActor::handle_set_session_model
              -> update compaction/backend-search controls
              -> update ChatState SamplingConfig
              -> update credentials/auth type
              -> invalidate auth memo
              -> optionally rewrite System Prompt
              -> persist CurrentModel
        -> update only target SessionHandle
        -> broadcast ModelChanged
        -> telemetry / process compatibility projection
  -> clear unavailable-model block on success
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| ACP 入口 | `agent/mvp_agent/acp_agent.rs` | `set_session_model` |
| 外层编排 | `agent/handlers/model_switch.rs` | `apply` |
| Session 提交 | `session/acp_session_impl/model_switch.rs` | `handle_set_session_model` |
| Harness 重建 | 同上 | `handle_rebuild_agent_for_definition` |
| Actor 命令 | `session/commands.rs` | `SetSessionModel`、`RebuildAgentForDefinition` |
| Catalog 解析 | `agent/mvp_agent/agent_ops.rs` | `resolve_model_id` |
| 兼容 helper | `agent/mvp_agent/mod.rs` | `harnesses_are_compatible` 等 |
| Catalog policy | `agent/models/resolution.rs` | allowed/selectable 计算 |
| Session 隔离测试 | `agent/mvp_agent/tests.rs` | cross-contamination test |
| Auth 回归测试 | `auth_error_no_retry_tests.rs` | same-ID memo invalidation |

## 3. 先区分五个对象

| 对象 | 职责 |
| --- | --- |
| `ModelId` | ACP wire 上的模型身份 |
| `ModelEntry` | Catalog 中模型配置与能力的完整条目 |
| Sampler `SamplerConfig` | 外层准备的 Endpoint、认证、参数配置 |
| ChatState `SamplingConfig` | 当前 Session 后续 Turn 实际读取的运行配置 |
| `SessionHandle.model_id` | MvpAgent registry 中的 Session 模型投影 |

切换成功要求这些投影按顺序收敛。

## 4. Model 不等于 Harness

Model 决定推理 Provider、能力和采样参数；Harness 决定 System Prompt、用户消息模板、工具协议、工具集和 Agent policy。

同一个 Harness 可服务多个兼容模型；某些模型则要求严格专属 Harness。

## 5. 公开入口先做 allowed_models gate

`MvpAgent::set_session_model()` 先解析 ModelEntry，再检查 `model.info.user_selectable`。若为 false，返回 invalid params，说明当前 `allowed_models` 不允许用户选择它。

## 6. “存在”与“可选择”是两步

`resolve_model_id()` 可以按 catalog map key 或条目中的真实 model 字段命中。命中只证明模型已知；公开调用还要通过 user-selectable policy。

## 7. 内部 apply 为什么不重复 gate

模块把 `apply` 标为 ungated path。用户入口必须先 gate；new/load Session 等内部调用已经在各自初始化流程中确定模型，可直接复用提交编排。

## 8. Unknown 与 Disallowed 错误不同

Catalog 找不到返回 `unknown model id`；找到但不可选择返回 allowed-models 错误。前者查目录刷新，后者查配置 policy。

## 9. 切换先等待 Session load

`session_handle_waiting_for_load()` 避免模型切换与恢复过程竞争。最终不存在则返回 unknown session，不隐式新建。

## 10. required agent type 的回退

```text
model.info.agent_type 存在 -> 模型要求的类型
否则                      -> Session default agent profile
```

`resolve_required_agent_type()` 把这条规则做成纯函数。

## 11. default、active 与 handle name 不同

- Session default profile 是初始化偏好；
- active_agent_type 是当前真实 Harness；
- `SessionHandle.agent_name` 是外层投影，供 child spawn 等读取。

切换时不能用默认值冒充当前状态。

## 12. Turn count 决定能否重建

代码从 Signals snapshot 读取 `turn_count`。Harness mismatch 是否可以修复，关键不是 Session 创建多久，而是是否已有真实对话历史。

## 13. Active Agent 通过 Actor 查询

外层发送 `GetActiveAgent` oneshot command，而不是直接越过 Actor 读取内部状态。这维持 SessionActor 的状态所有权。

## 14. Stock Harness 彼此兼容

两个 non-strict Harness 即使名称不同也兼容，因为共享默认 wire format 与工具约定。这样普通模型切换不会无谓重建 Agent 或抹掉客户端 agentProfile。

## 15. Strict Harness 只与自身兼容

两个 strict 类型必须名称相同；strict 与 stock 的任意方向都不兼容。判断的是协议 family，不是模型品牌。

## 16. 有历史的不兼容切换被拒绝

当 `is_mismatch && turn_count > 0`，返回结构化 `MODEL_SWITCH_INCOMPATIBLE_AGENT`，包含 active/required type、model ID，并建议 `start_new_session`。

## 17. 为什么不能改写旧历史

旧 Conversation 的 System Prompt、user template、Tool Call 名称和 Tool Result 都由旧 Harness 产生。新 strict Harness 可能无法解释它们；拒绝比猜测性迁移安全。

## 18. 拒绝也有 telemetry

`ModelSwitched` event 记录 previous/new model、success=false、error code 和双方 Agent type，可统计真实不兼容率。

## 19. Zero-turn 是重建窗口

Mismatch 且 `turn_count == 0` 时，没有真实用户历史需要保持，可以发现目标 AgentDefinition 并重建完整 Agent。

## 20. Definition 发现考虑项目与 Plugin

`by_name_in_cwd_with_plugins()` 使用 required type、Session cwd 和 Plugin registry snapshot；目标 Harness 不一定是内置定义。

## 21. 找不到 Definition 的退化行为

当前实现记录 warning 后继续使用 stale Harness 切换模型。这是可用性优先路径，也意味着排查模型行为异常时必须查看该 warning。

## 22. Reasoning effort 来自 request meta

`parse_reasoning_effort_meta()` 解析 override。只有 ModelsManager 声明目标模型支持时才写入 SamplerConfig；不支持则 warning 并忽略，不让整个模型切换失败。

## 23. SamplerConfig 是解析后的完整请求配置

`prepare_sampling_config_for_model()` 综合 ModelEntry、Endpoint、auth preference、origin client、backend capability 和 generation parameters，不只是复制 model ID。

## 24. Gateway gate 控制 Prompt 改写

若 `gateway_enabled` 已关闭：

- `apply_prompt_override=false`；
- 取消 pending Harness rebuild；
- 仍允许更新采样模型。

这保护镜像/Fork 继承的 Prompt 不被中途污染。

## 25. Harness rebuild 必须先提交

外层先发送 `RebuildAgentForDefinition` 并等待。只有成功后才发送 SetSessionModel；Builder 失败会终止切换并发出 `MODEL_SWITCH_REBUILD_FAILED`。

## 26. Rebuild handler 再检查 running task

外层校验与 Actor 执行之间可能启动 Turn。Handler 再检查 `running_task.is_some()`，拒绝重建，防御 TOCTOU 竞态。

## 27. RebuildSpec 是完整配方

Session 保存 `AgentRebuildSpec`，由它结合新 AgentDefinition 构造全新的 Agent，而不是在旧 Agent 上逐字段修补。

## 28. Prefire compaction 必须取消

旧 Harness 下预启动的压缩任务可能晚到并覆盖新上下文。重建会 abort、await 并 clear prefire 状态。

## 29. 替换 Agent 后同步 active type

Live Agent 与 `active_agent_type` 一起更新，随后重新解析 Tool overrides 和 Harness 特有的 Plan exit reminder policy。

## 30. Workspace toolset 需要重新 bind

新 Agent 可能拥有不同工具。Rebuild 用新 ToolBridge toolset 重绑 local workspace Session；失败记录 warning，但不会回滚已构建 Agent。

## 31. 新 ToolBridge 要重新注入 Resources

包括 ToolIndex、managed gateway client、PlanFilePath、display cwd、Workflow/Goal handles、task reservations/wake gates 与 deny-read globs。

## 32. MCP 工具需要重新注册

若 MCP configs 存在但 handshake 未完成，最多等待 5 秒，再把动态工具注册到新 Bridge。超时避免模型切换无限卡死。

## 33. User prefix 也属于 Harness

旧 deferred prefix task 会被 abort，随后重新构建 user message prefix。不同 Harness 的用户消息包装可能不同，不能复用旧结果。

## 34. Zero-turn Conversation 可以安全重写

Rebuild 替换/插入 System head，重写启动 prefix，补 project instructions 与 baseline skill reminder。没有真实 Turn，所以不会改写用户历史。

## 35. Prompt artifacts 必须一起持久化

成功后保存 normalized prompt context、system prompt 与 chat_history JSONL，保证 Session 恢复不会捡回旧 Harness。

## 36. Rebuild 后刷新客户端命令表

`send_available_commands_update()` 让 UI 看到新 Agent 的 slash commands，而不是继续展示旧 Harness 能力。

## 37. did_rebuild 会跳过第二次 Prompt rewrite

新 Harness 已安装正确 System Prompt，因此 SetSessionModel 使用 `skip_prompt_rewrite=true`，避免 concise/default 分支再次覆盖它。

## 38. 同模型 ID 也跳过 Prompt rewrite

`model_unchanged` 同样设置 skip，因为 same-ID 操作常用于刷新 effort 或凭据，不应无谓改写 Conversation head。

## 39. Same-ID 切换不是 no-op

它仍会刷新 SamplerConfig、credentials、auth memo、压缩参数、reasoning effort、持久状态和广播，只省略不必要的 Prompt rewrite。

## 40. Auto-compact threshold 按目标模型重算

外层结合新模型 ID、ModelInfo、remote settings 与用户 TOML 解析 threshold。继续使用旧模型阈值可能过早或过晚压缩。

## 41. SessionCommand 是提交边界

外层把 sampling、concise flag、Prompt policy 和 threshold 打包成 `SetSessionModel`，通过 oneshot 等待 SessionActor 返回 `Result<ModelId>`。

## 42. Context Window 的优先级

```text
Session context_window_override
  > 新 SamplerConfig 的非零 window
  > DEFAULT_CONTEXT_WINDOW
```

显式 Session override 不因切换丢失。

## 43. Compaction 更新不只有 window

Handler 同时更新 threshold percent、compactions remaining 与 compaction_at_tokens，它们共同决定压缩时间与次数。

## 44. Backend Search capability 随模型更新

`supports_backend_search` 与目标 SamplerConfig 同步，后续 Turn 的 hosted-tool 准备不能沿用旧模型能力。

## 45. ChatState SamplingConfig 的字段

提交包括 base URL、model、completion limit、temperature、top_p、API backend、headers、query params、context window、effort 与 stream-tool-calls。

这份配置才是后续模型请求的直接真相源。

## 46. 为什么边界处显式转换类型

外层 SamplerConfig 包含模型解析期信息；ChatState 使用稳定的运行类型与 NonZero window。显式复制能审计哪些字段真正进入 Session。

## 47. Credentials 与 SamplingConfig 同步提交

Handler写入新 API key、重新推导 auth type、保留 alpha-test key，并更新 client version。Endpoint 变了而凭据没变可能把错误 token 发给错误 Provider。

## 48. Auth type 不是简单复制

`resolve_chat_state_auth_type()` 结合目标 model、Session 当前/过期 auth key 与旧 auth type 推导新值。

## 49. 每次都失效 model-auth memo

`invalidate_model_auth_memo()` 无条件执行，包括 same-ID switch，保证 BYOK/provider 事实与最新 credentials 对齐。

## 50. Same-ID memo invalidation 有回归测试

测试先填充 memo，再用相同 model ID 更新配置，确认下一次认证状态重新计算。它固定了 credential rotation 语义。

## 51. Signals 记录 models used

`record_model_usage()` 把新模型加入该 Session 的使用集合，供 trace 和反馈分析去重记录。

## 52. Prompt rewrite 有三种结果

| apply | skip | 行为 |
| --- | --- | --- |
| true | false | concise 或当前 Agent System Prompt 覆盖首个 System item |
| false | 任意 | 保留继承 Prompt |
| true | true | Rebuild/same-ID 已处理，不再覆盖 |

## 53. Rewrite 只修改首个 System item

代码找到第一个 System ConversationItem 后替换并 break，不清除用户、assistant 和 tool history。

## 54. CurrentModel 是持久化真相

Persistence message 保存 model ID、当前 Agent definition name 与 reasoning effort，供 Session load 恢复。

## 55. Actor 成功后才更新外层 registry

外层等待 oneshot；成功后用 `with_resident_mut(session_id)` 更新目标 handle 的 model、effort 和必要的 agent name。

## 56. 只修改目标 Session

测试固定 Session A 的 model_id 改变不会影响 Session B。Leader 模式必须始终使用 per-session 投影。

## 57. agent_name 只在真实 rebuild 后改变

`agent_name_after_model_switch()`：rebuild 后采用新 Harness type；否则保留原名称，避免兼容 stock switch 抹掉 agentProfile。

## 58. ModelChanged 是在线广播

成功后通过 `x.ai/session_notification` 广播 model ID 与 applied effort，所有订阅该 Session 的 follower 同步 UI。

## 59. Broadcast 不等于 Persistence

ModelChanged 是 broadcast-only、无 eventId、不持久化；恢复依赖 CurrentModel。客户端 pending 只负责在线收敛，不是权威存储。

## 60. Leader 与非 Leader 的全局行为不同

非 Leader 还同步 ModelsManager process current model/effort。Leader 同时服务多 Session，不能让一个 Session 的切换成为全局默认，因此跳过。

## 61. Process static API key 最后同步

`sync_process_static_api_key()` 在 Session 提交成功后执行，为仍读取进程级 key 的兼容路径对齐目标模型。

## 62. 成功后解除 unavailable block

若旧模型不可用导致 Prompt 被阻塞，用户成功切到新模型后才清除 registry 标记；失败不会错误解除。

## 63. Response meta 返回服务端结果

`SetSessionModelResponse.meta.model` 携带 Actor 返回的 updated model，客户端应以它结束 optimistic pending。

## 64. Auto-switch 是另一种通知

系统自动迁移模型发送 `ModelAutoSwitched(previous,new,reason)`；用户显式切换走本文主链并广播 ModelChanged。

## 65. OverrideModelName 不是普通切换

它允许不存在于本地 Catalog 的 opaque routing name，保留 base URL/API key，只改 model、headers 与可选 window，并更新 primary model signals。

## 66. 为什么不能用 Override 绕过公开入口

普通 SetSessionModel 承诺目录、allowed policy、能力、认证与 Harness 一致性；Override 是受控传输层路由能力，不适合作为任意用户选择后门。

## 67. 正在运行 Turn 的边界

普通 config command由 Actor 串行处理，新配置主要供后续采样。Harness rebuild 会改变消息协议，因此额外硬拒绝 in-flight Turn。

## 68. Model generation 可中止长期等待

ModelsManager 的 switch generation 变化会让 idle/laziness 等待检测模型变化并重置 nudge 状态，避免旧模型时期启动的等待继续。

## 69. Generation 与 auth invalidation 目的不同

Generation 关注模型身份是否变化；auth memo 无条件失效关注凭据是否变化。同 ID 时前者可不变，后者仍必须执行。

## 70. 常见误读：切换只改 model 字段

实际还涉及 Harness、Endpoint、Backend、Window、压缩、effort、headers、credentials、auth cache、Prompt、Persistence 和 UI 投影。

## 71. 常见误读：已有 Session 可切任意模型

不兼容 strict Harness 且已有 Turn 时必须新建 Session。

## 72. 常见误读：zero-turn 一定重建成功

Definition 发现、Builder、running-task 复查、Workspace bind 与 MCP 重注册都可能失败或退化。

## 73. 常见误读：请求 effort 必然生效

只有目标模型声明支持才应用；客户端应读取服务端响应与广播中的 applied value。

## 74. 常见误读：same ID 完全无操作

Same ID 仍是 credential rotation、effort 和 config refresh chokepoint。

## 75. 常见误读：全局 current model 等于 Session model

Leader 中每个 Session 有自己的 Handle 与 ChatState。全局 current 只能用于非 Leader 兼容路径。

## 76. 调试：unknown model

检查请求 ID、Catalog key/info.model、目录刷新、客户端 available-model state，以及它是否其实是 opaque routing alias。

## 77. 调试：known but disallowed

检查 `allowed_models` glob、user_selectable 计算、hidden/disabled policy 与默认模型校验，不要绕过公开 gate。

## 78. 调试：Harness incompatible

记录 active/required agent type、strict 分类、turn_count 和原 agentProfile。有历史时新建 Session 通常是正确答案。

## 79. 调试：切换成功但 Prompt 仍旧

检查 gateway gate、apply/skip flags、did_rebuild、same-ID、Conversation System item，以及 system_prompt.txt/chat_history 是否一致。

## 80. 调试：切换后认证失败

检查 base_url/backend、API key、auth type、memo invalidation、process static key 和目标 Provider 的 BYOK 支持。

## 81. 调试：切换后过早压缩

检查 window override、目标模型 window、per-model threshold、compaction_at_tokens 与 compactions_remaining。

## 82. 调试：Session 互相污染

检查请求路径是否优先读取目标 SessionHandle/ChatState，而非 ModelsManager process current；Leader 不应更新全局 current。

## 83. 推荐测试矩阵

| 条件 | 期望 |
| --- | --- |
| unknown | invalid params |
| known, disallowed | allowed-models error |
| compatible stock + history | 成功，无 rebuild |
| strict mismatch + history | incompatible error |
| strict mismatch + zero-turn | rebuild 后成功 |
| rebuild failure | 整体失败 |
| same ID + new key | auth memo invalidated |
| unsupported effort | 成功但忽略 override |
| preserve inherited prompt | sampling 更新，Prompt 不改 |
| Session A switch | Session B 不变 |

## 84. 六条设计不变量

1. 用户模型必须存在且可选择。
2. 已有历史不能跨不兼容 Harness。
3. 必需的 Harness rebuild 先成功，模型配置才提交。
4. Sampling 与 Credentials 在同一 Actor handler 更新。
5. 外层 registry 只在 Actor 成功后更新。
6. Leader 中 Session 模型相互隔离。

## 85. Glossary：模型与采样

### Model Catalog

已知模型及其能力、Endpoint、限制和展示信息的目录。

### ModelEntry

Catalog 中一个模型的完整配置条目。

### Sampler

向模型 Provider 发送 Conversation、工具与生成参数并处理响应的组件。

### SamplingConfig

模型请求使用的 model、Endpoint、token limits、backend、headers 与生成参数。

### Reasoning Effort

支持该能力的模型用于控制推理投入的枚举参数。

### Context Window

一次模型请求容纳的上下文 token 上限。

### Auto-compaction Threshold

上下文用量达到某百分比时触发压缩的阈值。

### Backend Search

由模型 Provider 原生提供、需要模型能力支持的搜索工具。

## 86. Glossary：Harness 与重建

### Harness

包围模型的 Prompt、消息模板、工具协议和策略组合。

### AgentDefinition

声明 Agent 名称、Prompt、Toolset、policy 与模板的配置。

### Stock Harness

共享默认 wire format 的 Harness family，可在成员间兼容切换。

### Strict Harness

需要专属消息/工具协议，只与同类型兼容的 Harness。

### Active Agent Type

SessionActor 当前实际运行的 Harness 类型。

### Zero-turn

Session 尚无真实用户 Turn，可安全重写启动 Prompt 的阶段。

### RebuildSpec

重新构建 Agent 所需依赖和配置的缓存配方。

### Wire Format

模型看到的消息布局、工具名与 Tool Call/Result 表示方式。

## 87. Glossary：并发与状态

### SessionActor

串行拥有 Session 可变运行状态并处理命令的 Actor。

### SessionHandle

MvpAgent registry 中控制某 Session 并保存外层投影的句柄。

### Oneshot Channel

只传递一次结果的异步请求—响应通道。

### TOCTOU

检查与使用之间状态变化的竞态；Handler 的 running-task 复查用于防御。

### Defense in Depth

在多层重复检查关键不变量，减少并发或旁路绕过。

### Projection

同一状态在 registry、UI 或持久化层中的副本。

### Cross-contamination

一个 Session 的模型切换错误影响另一个 Session。

### Generation Counter

模型真实变化时递增，使长时间等待能检测切换。

## 88. Glossary：认证与通知

### BYOK

Bring Your Own Key，使用用户自己的 Provider key。

### Auth Type

决定请求如何携带凭据的认证类别。

### Auth Memo

按模型缓存的认证/provider 判断；切换时必须失效。

### Credential Rotation

模型 ID 不变但 key/token 已变化的更新。

### Persistence Message

写入 Session durable state 的 typed message，如 CurrentModel。

### Broadcast

向在线订阅者发送、不作为 durable truth 的实时通知。

### ModelChanged

用户模型切换成功后的在线广播。

### ModelAutoSwitched

系统自动迁移模型时带原因发送的通知。

## 89. 一页复习版

```text
SetSessionModel = catalog/policy gate
                + Harness compatibility migration
                + Session sampling/auth commit
                + persistence/UI projection

history + incompatible Harness -> reject
zero-turn + incompatible        -> rebuild first

Actor commit updates:
  context + compaction + backend search
  sampling config + credentials + auth memo
  optional system prompt + CurrentModel persistence

same model ID != no-op
ModelChanged broadcast != durable CurrentModel
Leader global model != per-Session model
```

## 90. 源码证据索引

| 结论 | 直接证据 |
| --- | --- |
| allowed gate | `MvpAgent::set_session_model` |
| 双重 catalog 匹配 | `resolve_model_id` |
| stock/strict 兼容 | `harnesses_are_compatible` |
| history mismatch 拒绝 | `model_switch::apply` |
| zero-turn rebuild | `RebuildAgentForDefinition` |
| running-task 复查 | rebuild handler |
| Resource/MCP 重接线 | rebuild handler |
| threshold 重算 | apply 中 target-model resolution |
| Sampling/Credentials 更新 | `handle_set_session_model` |
| same-ID memo 失效 | unconditional invalidation |
| Session 隔离 | `with_resident_mut(session_id)` + test |
| durable state | `PersistenceMsg::CurrentModel` |

## 91. 阅读完成后应该能回答的问题

1. Model 与 Harness 的职责分别是什么？
2. 为什么 known model 仍可能不可选择？
3. Stock 名称不同为何仍可兼容？
4. 为什么已有历史的 strict mismatch 必须新建 Session？
5. Zero-turn rebuild 为什么要重新注入 Tool Resources？
6. did_rebuild 为什么必须跳过后续 Prompt rewrite？
7. Same-ID switch 为什么仍需失效 auth memo？
8. Context Window 与压缩控制如何随模型改变？
9. CurrentModel 与 ModelChanged 有何区别？
10. Leader 为什么不能依赖全局 current model？

能回答这些问题，就能把模型切换理解成一次受兼容性保护的 Session 配置迁移，而不是 UI 下拉框对字符串的赋值。
