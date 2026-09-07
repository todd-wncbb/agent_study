# Walkthrough：Tool Overrides 如何在 Prompt 晋升时约束搜索、回显并传给 Subagent

> 场景：客户端提交 Prompt，并用 `_meta.toolOverrides` 限制 `x_search` 的内容日期范围，或限制 `web_search` 的允许域名。这份更新先随 Prompt 排队，不立即影响正在运行的 Turn；Prompt 晋升时才合并进 Session 状态。Shell 把最终约束应用到 Backend Hosted Tools，只有真实随请求发送时才在 PromptResponse 中回显。同时，它发布一份继承快照，使本 Turn 新建的 Subagent 不会搜索到父 Agent 截止日期之外。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
Client initialize
  <- capability.toolOverrides（支持哪些搜索工具/字段）

Client Prompt
  _meta.toolOverrides = {
    "xSearch": {"dateBound": {"toDate": "2026-08-10"}}
  }
  -> MvpAgent::prompt
     -> ToolOverridesUpdate::parse
     -> invalid -> ACP invalid_params
     -> valid -> SessionCommand::Prompt(update)
  -> queue_input
     -> InputItem.tool_overrides_update
     -> 此时不改变 live Session
  -> Prompt 晋升为 running front
     -> take update
     -> apply_tool_overrides_update
        -> tri-state merge 到 Session override
        -> emit_resolved_tool_overrides
           -> AgentDefinition seed + Session override
           -> ArcSwapOption inheritance snapshot
  -> sampling
     -> backend_search_active?
     -> resolve_hosted
        -> clone Agent hosted tools
        -> apply_tool_overrides in-place
        -> 得到 applied echo
     -> request.hosted_tools
  -> Turn complete/cancel
     -> effective_tool_overrides
     -> PromptTurnOk.tool_overrides
     -> PromptResponse._meta.toolOverrides

本 Turn spawn Subagent
  -> parent SessionHandle.resolved_tool_overrides
  -> SubagentSpawnContext.inherited_tool_overrides
  -> child SessionCommand::SetToolOverrides（首 Prompt 前）
  -> child hosted search 使用相同或更受限边界
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| Wire types | `xai-grok-sampling-types/src/tool_overrides.rs` | `ToolOverrides(Update)` |
| Hosted Tool 应用 | `xai-grok-sampling-types/src/conversation.rs` | `apply_tool_overrides` |
| Prompt 入口 | `xai-grok-shell/.../mvp_agent/acp_agent.rs` | metadata parse |
| Queue | `session/acp_session_impl/prompt_queue.rs` | InputItem 字段 |
| 晋升边界 | `.../notification_drain.rs` | take + apply update |
| Session 状态 | `.../sampler_turn.rs` | resolve/apply/emit helpers |
| Turn 结果 | `.../turn_end.rs`、`tasks_cancel.rs` | effective echo |
| Response meta | `agent/mvp_agent/mod.rs` | `tool_overrides` |
| Subagent 捕获 | `agent/mvp_agent/subagent_coordinator.rs` | inherited snapshot |
| Child 注入 | `agent/subagent/handle_request.rs` | `SetToolOverrides` |
| Queue 测试 | `prompt_queue_actor_tests.rs` | promotion/echo/inheritance |

## 3. Tool Overrides 当前控制什么

当前 wire contract 面向 Backend Hosted Search：

- `xSearch.dateBound`：内容日期窗口；
- `webSearch.allowedDomains`：域名 allowlist。

它不是任意工具参数覆盖框架，也不会修改本地 grep/read 工具。

## 4. Hosted Tool 与普通 ToolDefinition 不同

普通工具以名称、描述、JSON parameters schema 发送，调用后由 Shell dispatch。

Hosted Tool 由模型 Backend 原生执行，作为请求的 `hosted_tools` 字段发送；Shell 不运行其实现。

## 5. 初始化时声明 capability

Agent 的 initialize 响应告诉客户端支持哪些 Tool Overrides。客户端应根据 capability 生成合法 UI/metadata，而不是盲发未来字段。

## 6. Prompt metadata 使用 camelCase

入口读取 `_meta.toolOverrides`，内部 Rust 字段使用 snake_case；Serde 负责 `xSearch`、`webSearch`、`dateBound`、`allowedDomains` 映射。

## 7. 未知字段被拒绝

相关 structs 使用 `deny_unknown_fields`。拼错字段不会静默忽略，而是返回 invalid params，避免用户以为约束生效。

## 8. Resolved state 与 Update 分开

- `ToolOverrides`：当前已解析状态，可回显和继承；
- `ToolOverridesUpdate`：一次 Prompt 的 patch，字段具有三态。

不能用 resolved type 表达“保持不变”和“显式清除”的区别。

## 9. ClearableField 的三态

```text
字段 absent -> 保持旧值
字段 null   -> 清除 Session override
字段 object -> 设置新值
```

类型是 `Option<Option<T>>`，外层 Option 表示出现与否，内层 Option 表示 null 或 value。

## 10. 为什么普通 Option 不够

反序列化后，缺失与 null 通常都会成为 None。Patch 协议必须区分它们，否则客户端无法只修改一个工具，也无法显式撤销约束。

## 11. 空 object 不等于 clear

`{}` 没有实际约束，被 normalize 成 empty，并回退旧值。只有 `null` 才清除。

这防止 UI 序列化空表时意外移除安全 cutoff。

## 12. ToolOverrides::is_empty

只有 xSearch 和 webSearch 都没有非空约束时才为 true。空状态通常归一化为 None，减少 wire 噪音。

## 13. drop_empty 是统一规则

Update merge 与 Hosted Tool apply 都调用同一个 empty normalization helper，避免一边认为 `{}` 有效、另一边认为无效。

## 14. x_search 的日期窗口

`SearchDateBound` 支持可选 `fromDate` 和 `toDate`：

- fromDate inclusive；
- toDate 是命名日期 00:00 UTC 的 exclusive 上界。

## 15. 为什么 toDate 是 exclusive

它表达“搜索此日期之前的内容”，适合知识 cutoff。若要包含 8 月 10 日全天，应传下一天作为上界。

## 16. 日期必须是规范格式

要求严格零填充 `YYYY-MM-DD`。`2026-8-1` 即使人能理解也被拒绝，保证 lexical/display/wire 一致。

## 17. Year 0 被拒绝

Chrono 的 proleptic calendar 可能接受 year 0；代码额外要求 year >= 1，保持常规公历产品语义。

## 18. 反向窗口被拒绝

若 fromDate > toDate，返回 `InvertedWindow`。相等允许，表示空时间窗口而非语法错误。

## 19. 日期校验覆盖所有入口

Serde `try_from` 最终调用 `SearchDateBound::new()`，不仅手工构造路径校验。不能通过 JSON 反序列化绕过不变量。

## 20. x_search wire 转换

`to_tool_entry()` 输出 Backend 所需 snake_case：

```json
{"type":"x_search","from_date":"...","to_date":"..."}
```

客户端 contract 与 Provider request contract 可使用不同命名。

## 21. web_search 的 domain allowlist

`allowedDomains` 是字符串数组；缺失或空数组表示无约束，因此被视作 empty，而不是“禁止所有域名”。

## 22. 为什么空 allowlist 不表示 deny-all

当前协议明确把空值归一化为 unbounded。需要 deny-all 时应使用独立显式语义，不能让空容器含义含糊。

## 23. 入口先完整解析 Update

`ToolOverridesUpdate::parse(value)` 校验字段、日期和类型。失败返回 ACP invalid params，并加 `toolOverrides:` 前缀。

## 24. 无 override 与空 update 不同但最终可能同效

metadata 缺失得到 `None`，promotion 不调用 apply；空 object 得到 Some(default update)，apply 后因全部 absent 保持原状态。

## 25. Update 随 Prompt 入队

它被保存为 `InputItem.tool_overrides_update`，与 Prompt 文本、mode、schema 一起冻结。

## 26. 入队时绝不应用

这是关键并发不变量。未来 Prompt 即使已排队，也不能提前改变当前运行 Turn 或更早队列项的搜索范围。

## 27. Promotion 才是应用边界

Scheduler 取得 running front 时从 InputItem `take()` update，然后调用 `apply_tool_overrides_update()`，再开始该 Prompt。

## 28. 为什么使用 take

Update 是一次性 patch。取走后，即使同一 InputItem 的展示信息被重复读取，也不会重复应用产生意外副作用。

## 29. Queue 顺序例子

```text
P1 running，cutoff=A
P2 queued，update=B
P3 queued，update=C

P1 完成前：live 仍为 A
P2 晋升：live 变 B
P3 晋升：live 变 C
```

## 30. Session override 是持续状态

晋升后更新 `self.tool_overrides`，后续未携带 patch 的 Prompt 继续使用该状态，直到新 update 修改或清除。

## 31. apply 使用旧 slot 作为 base

`update.apply(slot.take())` 先取走旧 resolved Session override，逐字段 merge，再写回 Option。这样 patch 可独立修改 x/web 工具。

## 32. null 清除的是 Session override

清除后仍可能看到 AgentDefinition seed，因为 definition-level 默认约束是下层基线。用户 patch 不能通过 null 绕过配置 seed。

## 33. Definition seed 是什么

AgentDefinition 可以自带 `tool_overrides`。Builder 会把 seed 烘焙进 Hosted Tool options，使 Agent 天生带搜索边界。

## 34. Per-turn/Session override 优先于 seed

每个工具独立解析：非空 override 优先，否则使用非空 seed。一个 patch 可覆盖 xSearch，同时让 webSearch 继续继承 seed。

## 35. apply_tool_overrides 修改 Hosted Tools clone

每轮先 clone Agent 的 Hosted Tools，再把当前 Session override 覆盖到 options 中。不会永久修改 AgentDefinition 原对象。

## 36. 返回 applied echo 很重要

`apply_tool_overrides()` 一边修改 tools，一边从修改后的实际 options 构造 ToolOverrides。回显因此反映真正发往 Backend 的值，而不是用户原始输入。

## 37. 为什么不能原样 echo 请求

原始 update 可能是 partial、null、empty，且还受 seed 和工具是否存在影响。只有应用后的 Hosted Tools 才是可证明的最终状态。

## 38. 没有对应 Hosted Tool 就不回显

循环只对实际 tools 设置 applied 字段。配置了 xSearch cutoff 但 Agent 没有 XSearch Tool 时，不能声称已应用。

## 39. Backend Search 是双重 gate

`backend_search_active()` 要求：

- AgentDefinition 启用 backend search；
- 当前模型 SamplingConfig 支持 backend search。

任一为 false，request.hosted_tools 为空。

## 40. effective_hosted_tools 是 ungated helper

它解析并返回 Hosted Tools，但注释要求 Turn 优先使用 `hosted_tools_for_turn()`，后者加入 backend gate。

测试或旁路误用 ungated helper可能错误模拟实际 wire。

## 41. Backend gate 关闭时不回显

`effective_tool_overrides()` 在 backend search inactive 时返回 None，即使 Session slot 保存了 override。

原则是：不能 attest 一个本次请求根本没发送的 cutoff。

## 42. 保存但不回显并非丢失

模型切换后 Backend Search 暂时不支持时，配置仍留在 Session。以后换回支持模型可再次生效，而当前响应不会虚假声称已应用。

## 43. 本地 web_search definition 会被去重

Backend search active 时，`turn_base_tool_specs()` 从普通 ToolDefinitions 中移除名为 `web_search` 的本地工具，避免同时暴露本地与 Hosted 版本。

## 44. 请求最终携带 hosted_tools

Turn 构建 Conversation request 后设置：

```text
request.hosted_tools = self.hosted_tools_for_turn()
```

这里的 options 已包含 resolved overrides。

## 45. Turn 完成时回显 effective value

`turn_end` 把 `effective_tool_overrides()` 写进 `PromptTurnOk.tool_overrides`。取消路径也按是否确有 running Turn选择是否附带。

## 46. 未运行的排队 Prompt不回显

如果 Prompt 在晋升前被取消，它的 update 从未应用、token 为 0，结果明确 `tool_overrides=None`。

## 47. PromptResponse meta 使用 toolOverrides

`build_prompt_response_meta()` 把内部 resolved value序列化到 `_meta.toolOverrides`；空/None 时省略。

## 48. 回显是一种 Attestation

客户端可比较“我要求的更新”和“服务端实际应用值”。它不是简单 UI echo，而是对本 Turn wire constraints 的声明。

## 49. Session new/load 也返回 applied state

外层用 `GetToolOverrides` command读取 `effective_tool_overrides()`，把它插入 new/load response meta。恢复客户端能看到当前真正有效配置。

## 50. 为什么 Get 走 Actor command

Tool override slot 与 Hosted Tools/Backend capability 属于 SessionActor。外层通过 oneshot 查询，避免绕过 Actor 读出不一致快照。

## 51. resolved_tool_overrides 是继承快照

SessionHandle 持有 `ArcSwapOption<ToolOverrides>`，供 Subagent Coordinator 无 await、线程安全读取父边界。

## 52. 为什么继承快照不受 backend gate

父 Agent 当前可能不能搜索，但它 spawn 的 child 可能使用支持搜索的模型。安全 cutoff 必须传给 child，不能因为父请求没发送 Hosted Tool 而消失。

## 53. emit_resolved_tool_overrides 合并 seed 与 slot

它使用 `resolve_configured_cutoff(seed, base)` 得到 per-tool 最终约束，非空时发布 Arc，空时存 None。

## 54. Promotion 后立即发布快照

`apply_tool_overrides_update()` 合并 slot 后立刻 emit，保证同一 Turn 随后调用 Task 时 Coordinator 读取的是本 Prompt 边界。

## 55. SetToolOverrides 也立即发布

Child 在首 Prompt 前接收 inherited override，`set_tool_overrides()` 写 slot 并 emit。因此 child 再 spawn grandchild 时边界继续传播。

## 56. Subagent Coordinator 如何捕获

它从 parent SessionHandle 的 ArcSwap load_full，clone 成 `SubagentSpawnContext.inherited_tool_overrides`。

捕获是 spawn 时快照；父后续 Prompt 更新不会倒流修改已创建 child。

## 57. Child 如何接收

`handle_request` 在发送 child 首 Prompt 前，如果存在 inherited overrides，就发送 `SessionCommand::SetToolOverrides`。

顺序保证 child 第一次采样已经受限。

## 58. 为什么不把 update 原样传给 child

父 update 可能只修改一个字段，其他字段来自旧 slot 或 Definition seed。Child 需要完整 resolved cutoff，不能脱离父 base 重新解释 partial patch。

## 59. 防止知识截止污染

父任务要求“只使用某日期前知识”时，child 若不继承 cutoff，可能通过搜索得到未来信息并回传，污染父答案。继承快照封住该旁路。

## 60. Recap 也复用 Hosted Tools

Side recap/summary请求镜像主 Turn 的 hosted tools，避免用于辅助回答的搜索越过 active cutoff。

## 61. Agent rebuild 要重新发布 seed

模型切换导致 Harness rebuild 后，AgentDefinition seed 可能变化。Rebuild 调用 `emit_resolved_tool_overrides()`，刷新 inheritance cell。

## 62. 常见误读：Override 只作用当前 Prompt

Update 在 Prompt 晋升时改变 Session slot，后续未更新的 Prompt会继承。它是 per-Prompt 触发的持久 Session patch，而非执行结束自动回滚的临时变量。

## 63. 常见误读：null 会移除所有限制

Null 只清除 Session override；Definition seed 仍会重新显现。

## 64. 常见误读：空 object 就是 clear

空 object 是 no instruction，保持 base。显式 clear 必须用 null。

## 65. 常见误读：配置存在就应回显

只有对应 Hosted Tool 实际存在且 Backend Search active 才回显。否则会虚假 attest。

## 66. 常见误读：父不能搜索就无需传给 child

Child 可能使用另一模型或 Harness并具备搜索能力，所以 inheritance snapshot 特意不受父 backend gate。

## 67. 常见误读：入队即改变截止日期

真正应用点是 promotion。否则 P2 的未来配置会污染仍运行的 P1。

## 68. 调试：Prompt 更新没有生效

检查：

1. metadata key/camelCase；
2. parse 是否 invalid params；
3. Prompt 是否真正晋升；
4. update 是否被 take；
5. backend search 双 gate；
6. Agent 是否有对应 Hosted Tool；
7. response meta 是否有 applied echo。

## 69. 调试：null 后仍有 cutoff

检查 AgentDefinition seed。清除 Session patch 后回退 seed 是预期行为。

## 70. 调试：父回显 None，Child 却有限制

父 Backend Search inactive 时不会 echo，但 inheritance cell仍保存 configured cutoff。此行为用于约束有搜索能力的 child。

## 71. 调试：日期被拒绝

检查严格零填充、日历合法性、year >= 1、from <= to，以及 toDate exclusive 的业务期望。

## 72. 调试：Subagent 搜索越界

检查 promotion 是否先 emit、SessionHandle 是否共享同一 ArcSwap、Coordinator 捕获值、child SetToolOverrides 顺序，以及 child Hosted Tool 是否实际应用 options。

## 73. 推荐测试矩阵

| 场景 | 期望 |
| --- | --- |
| absent update | 保持旧值 |
| object update | 覆盖指定工具 |
| empty object | 保持旧值 |
| null | 清除 slot、回退 seed |
| invalid date | ACP invalid params |
| queued future update | 当前 Turn 不变 |
| promotion | 此时才应用并发布 |
| backend inactive | hosted tools空、echo None |
| missing Hosted Tool | 不虚假 echo |
| spawn child | 继承 resolved cutoff |
| Agent rebuild | 重新发布新 seed |

## 74. 设计不变量

1. Update 必须完整校验后才能入队。
2. 排队 Prompt 不得提前改变 live Session。
3. 回显必须来自实际 Hosted Tools，而非原始请求。
4. Backend 未发送约束时不得 attest。
5. Child 必须继承 configured cutoff，即使父当前不能搜索。
6. Empty、absent、null 三种语义不得混淆。

## 75. Glossary：Patch 与状态

### Override

在默认/seed 配置之上覆盖某些字段的值。

### Patch

只描述变化部分而非完整目标状态的数据。

### Tri-state

字段具有 absent、null、value 三种状态，分别表示保留、清除和设置。

### ClearableField

用 `Option<Option<T>>` 表达 tri-state 的类型别名。

### Seed

AgentDefinition 提供的基线 Tool Overrides。

### Resolved State

合并 seed 与 Session override 后的完整有效状态。

### Normalization

把语义等价的空结构统一成 absent，减少歧义。

### Promotion

Prompt 从排队项变成 running front 的过程，也是应用 update 的边界。

## 76. Glossary：搜索约束

### Hosted Tool

由模型 Backend 原生执行、随请求配置发送的工具。

### Backend Search

Provider 侧的 `x_search` / `web_search` 能力。

### Date Bound

限制搜索内容日期的 from/to 窗口。

### Inclusive / Exclusive

边界值是否属于集合；fromDate 包含当天，toDate 不包含命名日期零点之后的内容。

### Domain Allowlist

只允许搜索指定域名的列表。

### Cutoff

知识或内容不得晚于某日期的边界。

### Wire Contract

客户端与服务端约定的 JSON 字段、命名和语义。

### Attestation

服务端回显其真正应用的约束，供客户端核验。

## 77. Glossary：并发与继承

### InputItem

Prompt Queue 中冻结正文、模式、Schema 和 Override Update 的队列项。

### Session Slot

SessionActor 内保存当前 override 的可变状态位置。

### ArcSwapOption

可原子替换并无锁读取 `Option<Arc<T>>` 的并发容器。

### Inheritance Snapshot

Subagent spawn 时捕获的父有效配置副本。

### Subagent Spawn Context

创建 Child Session 所需的父模型、认证、cwd、限制和运行参数集合。

### Contamination

未来 Prompt、辅助请求或 Child 越过当前约束，影响父答案的现象。

### Gate

决定能力是否真正进入请求的条件；Backend Search 需要 Agent 与模型双重支持。

### Echo

响应中返回的 applied overrides；它证明真实 wire 状态，不只是重复输入。

## 78. 一页复习版

```text
toolOverrides update 是 tri-state patch：
  absent = keep
  null   = clear Session override（seed 仍在）
  object = set
  {}     = empty/no instruction

时间边界：
  enqueue   -> 只保存在 InputItem
  promotion -> apply Session slot + publish inheritance snapshot
  sampling  -> clone Hosted Tools + apply override
  completion-> echo 实际 applied value

echo gate：
  Agent backend-search enabled
  AND model supports backend search
  AND 对应 Hosted Tool 存在

Subagent inheritance 不受父 backend gate：
  configured cutoff 必须约束可能具备搜索能力的 child。
```

## 79. 源码证据索引

| 结论 | 直接证据 |
| --- | --- |
| tri-state wire | `ToolOverridesUpdate` / `ClearableField` |
| 空 object 保持 base | `merge_field` |
| 日期严格校验 | `SearchDateBound::new` / try_from |
| 入队不应用 | `InputItem.tool_overrides_update` |
| promotion 应用 | `notification_drain.rs` |
| Hosted Tools 实际修改与 echo | `apply_tool_overrides` |
| backend 双 gate | `backend_search_active` |
| inactive 不 attest | `effective_tool_overrides` |
| seed + slot 继承合并 | `resolve_configured_cutoff` |
| 无锁发布 | `resolved_tool_overrides.store` |
| Child 捕获 | `subagent_coordinator.rs` |
| 首 Prompt 前注入 child | `handle_request.rs` |

## 80. 阅读完成后应该能回答的问题

1. ToolOverrides 和 ToolOverridesUpdate 为什么分开？
2. absent、null、object、empty object 分别表示什么？
3. 为什么 update 必须在 Prompt promotion 时应用？
4. toDate 的 exclusive 语义是什么？
5. 为什么 echo 必须从修改后的 Hosted Tools 生成？
6. Backend Search 为什么有 Agent 与模型双 gate？
7. 为什么 inactive 时保留配置却不回显？
8. 清除 Session override 后为何可能仍有 seed cutoff？
9. 父不能搜索时为何仍要把 cutoff 传给 Child？
10. Agent rebuild 为什么要重新发布 inheritance snapshot？

能回答这些问题，就掌握了 Tool Overrides 的核心：它既是有严格三态语义的队列化配置 Patch，也是一条必须跨 Hosted Tool wire、响应证明和 Subagent 继承保持一致的安全边界。
