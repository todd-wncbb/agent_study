# 85：App-server 协议版本演进、前后兼容与迁移策略

> 源码基线：`4ee41929eaf4`。本章解决“已经有人使用的 JSON-RPC 协议怎样继续变化，以及怎样让旧客户端、新客户端、旧服务端、新服务端和旧持久化数据尽可能继续互相理解”。

## 1. 本章解决什么问题

上一章说明 Rust、TypeScript、JSON Schema 和预计算生成物必须同步。但“所有文件同步”只证明各份描述一致，不证明变化对旧用户兼容。

本章进一步研究：v1/v2 在源码里到底是什么、`initialize` 协商了什么、字段与 enum 怎样迁移、弃用为何分阶段，以及恢复旧 thread 时还多出哪一条时间轴。

## 2. 资料边界

本章的实现事实来自固定提交 `4ee41929eaf4`。官方 OpenAI 文档检索没有提供与该提交一一对应的公开 App-server 协议说明，因此不把当前产品页面反推为旧提交事实。

这也是源码导读的重要习惯：当前公开文档、当前主分支和固定历史提交属于三个时间点，不能混用。

## 3. 先说人话：协议演进像改变插头标准

服务端像墙上的插座，客户端像电器插头。只把新插座和新插头一起测试，当然能工作；真正困难的是旧插头还能否插新插座，新插头遇到旧插座时能否安全降级。

Adapter、alias、default、capability 和 deprecation period 就是不同形状的转接方案。

## 4. Compatibility 不等于所有版本完全相同

兼容允许新版本增加能力，只要求某些既有组合仍有定义良好的行为。新客户端不使用新能力时可能兼容旧服务端；一旦调用新 method，旧服务端则应返回可识别错误或由客户端提前避免。

因此兼容性总要说明方向、范围和前提。

## 5. 四个基本组合

| Client | Server | 需要回答的问题 |
|---|---|---|
| 旧 | 旧 | 基线行为是否保留 |
| 旧 | 新 | 新服务端是否继续接收旧请求并返回旧客户端可读响应 |
| 新 | 旧 | 新客户端是否能探测/避免旧服务端没有的能力 |
| 新 | 新 | 新功能自身是否正确 |

后面每种迁移策略都要放回这四格检查。

## 6. Backward Compatibility

从新实现的视角看，backward compatibility 通常指新服务端仍理解旧客户端/旧数据。例如继续接受旧 enum 字符串 `guardian_subagent`。

“Backward”指向过去产生的输入。

## 7. Forward Compatibility

Forward compatibility 常指旧实现面对未来版本产生的数据时不会灾难性失败。例如旧客户端忽略 response 中不认识的可选字段。

它通常要求 reader 对未知内容有明确容忍策略。

## 8. Reader/Writer 思维

比背前向/后向更可靠的方法是分别问：谁写 JSON，谁读 JSON？Reader 能接受哪些历史或未来 shape？Writer 承诺输出哪一种 canonical shape？

同一类型在 request 和 response 方向可能得出相反结论。

## 9. Protocol Version 有三种常见含义

“版本”可能指：

- 客户端软件版本，如 `0.1.0`；
- API 代际，如旧方法与 v2 方法；
- 持久化记录由哪个程序版本写出。

三者都出现 `version` 一词，但用途完全不同。

## 10. 固定提交的 `clientInfo.version`

`InitializeParams.clientInfo` 包含 `name`、`title` 和 `version`。初始化处理器把 version 存入 connection state，并用于 user-agent/analytics 和后续客户端信息传递。

在本章检查到的固定源码中，它没有被解释成“请求使用 v1 还是 v2”的协商数字。

## 11. 不要把 Client Version 当 Protocol Negotiation

如果客户端把自己从 `1.2.3` 升到 `1.2.4`，不代表 wire protocol 自动切换。反过来，旧 client version 也可能调用某些 v2 method。

Version string 只有在 server 明确按它选择行为时才构成协商；本基线的主要 gate 不是它。

## 12. 固定提交里的 v1/v2 共存

`protocol/common.rs` 的 `client_request_definitions!` 同时列出：

- `initialize` 使用 v1 payload；
- `thread/start`、`turn/start` 等使用 v2 payload；
- `getConversationSummary` 等旧方法仍在同一请求枚举末尾。

这不是连接级“只能选 v1 或 v2”，而是同一 server surface 中新旧 method 共存。

## 13. Method Namespace 表达 API 代际

旧方法常是 `getConversationSummary`、`gitDiffToRemote` 这样的动词式名字；新 v2 API 多使用 `thread/start`、`account/read` 这样的资源/动作路径。

命名风格能提示代际，但真正 routing 仍由精确 method string 决定。

## 14. `protocol/v1.rs` 与 `protocol/v2/`

源码目录把 payload 类型按代际组织，方便维护者知道长期兼容负担和新开发位置。公共 request DSL 再把这些类型接到 wire method。

文件目录本身不会自动阻止新代码调用 v1；约束来自项目规则与 review。

## 15. 为什么新 API 应进入 v2

继续给 v1 增加 surface 会扩大旧设计的维护期，也使迁移永远无法收敛。仓库约定所有活跃 App-server API 开发发生在 v2。

这是一项 API governance 决策，而不只是文件摆放偏好。

## 16. Initialize Handshake

连接首先发送 `initialize` request，得到 response 后再发送 `initialized` notification。初始化前发送普通请求会被 `message_processor` 以 `Invalid request: Not initialized` 拒绝。

Handshake 建立连接级 identity 和 capabilities，而不是创建 thread。

## 17. Initialize 只能成功一次

Connection state 用 `OnceLock<InitializedConnectionSessionState>` 保存初始化结果。再次 initialize 返回 `Already initialized`。

这意味着 capability 不能在同一连接中途悄悄变化；若要另一组能力，应建立新的连接。

## 18. Capability Negotiation

`InitializeCapabilities` 是客户端声明“我能处理什么/我希望什么”的地方。固定提交包括 experimental API、attestation、notification opt-out 和 MCP extension settings。

Capability 比软件版本字符串更精确，因为它直接描述能力而非猜测版本范围。

## 19. `experimentalApi`

客户端在 initialize 中设置 `capabilities.experimentalApi: true`，表示愿意使用和接收实验协议面。省略 capabilities 或该字段时，Serde default 使其为 false。

默认关闭是 safe default：没有明确同意的旧客户端不会意外进入实验合同。

## 20. Request-side Experimental Gate

普通 request dispatch 前，server 调用 `codex_request.experimental_reason()`。若请求整体或 params 中某字段实验，而连接没有 opt-in，就返回 invalid request。

所以实验字段不是“Schema 里隐藏但 runtime 仍随便接受”。生成过滤与执行 gate 是两条都要成立的防线。

## 21. Notification-side Experimental Gate

广播 notification 时，transport 查看每个连接是否初始化、是否启用 experimental API。实验 notification 对未 opt-in 的连接直接跳过。

同一 server 实例连接多个客户端时，每个连接可能看到不同通知集合。

## 22. Server Request 中的实验字段

服务端主动向客户端发 request 时，固定提交还有连接级过滤：例如未启用实验能力时，从 command approval params 中 strip experimental fields。

这说明 gating 不只管 client→server method，也要管 server→client payload。

## 23. Per-connection Scope

固定提交把 `experimental_api_enabled` 放在每个 connection session state。一个连接 opt-in，不会自动替另一个连接 opt-in。

源码 TODO 还指出共享 thread 上跨客户端行为可能因此复杂。这是实现事实与待演进设计的边界。

## 24. Capability 与 Feature Flag 不同

Capability 描述对端能否/是否愿意处理某协议；server feature flag 描述服务端制品或配置是否启用实现。

客户端 opt-in experimental 不保证服务端一定具备所有实验 method；服务端具备能力也不能绕过客户端未 opt-in。

## 25. Unknown Method 与 Unsupported Operation

未知 method、已知 method 但 backend 不支持、已知实验 method 但没 opt-in，是三种不同失败。

固定提交部分 backend capability 缺失会返回 JSON-RPC `-32601` method not found；实验未 opt-in 和初始化错误则走 `-32600` invalid request。

## 26. 固定提交的未知 Method 解析路径

JSON-RPC envelope 被转成 tagged `ClientRequest` enum。未匹配的 method 会造成 Serde 反序列化错误，并被包装成 `Invalid request: ...`。

因此不要把 JSON-RPC 规范中的常见期望直接当成本基线实际错误码；应以具体处理路径和测试为证据。

## 27. Client 应如何面对能力缺失

Client 不应把所有失败都显示成“服务器坏了”。至少区分：输入 shape 错、需要 experimental opt-in、method/backend unavailable、transient server error。

这样才能决定修请求、降级 UI、隐藏功能或稍后重试。

## 28. Additive Change

Additive change 指保留旧 surface，同时增加 method、可选字段或 enum variant。它通常比删除/改名容易兼容，但不自动安全。

添加 response field 仍可能破坏拒绝未知属性的旧 reader；添加 enum variant 仍可能破坏穷尽 switch。

## 29. 新增 Optional Request Field

旧客户端不会发送它，新服务端必须为 omission 定义旧行为。这通常保护“旧 client → 新 server”。

但“新 client → 旧 server”是否兼容取决于旧 reader 是否忽略未知 request property。

## 30. 新增 Required Request Field

旧客户端无法提供新字段，新服务端若直接 required 会拒绝旧请求。这通常是 breaking。

常用迁移是先 optional + server default，等旧版本退出支持期后再考虑收紧。

## 31. 新增 Optional Response Field

新服务端向旧客户端多发字段，兼容取决于旧客户端 decoder 是否宽松。新客户端读旧服务端响应时，还必须能处理字段缺席。

因此新客户端的本地类型通常也应先将它视为 optional。

## 32. 新增 Required Response Field

即使新服务端总会返回，旧服务端不认识它。新 client 若把它当必填，就无法连接旧 server。

除非 client/server 总是锁步发布，否则应配合 capability、method version 或 optional fallback。

## 33. 删除字段

删除 request field 可能让新 server 拒绝旧 client 仍发送的内容；删除 response field 会让旧 client 读取失败或行为缺失。

安全迁移通常先停止依赖，再停止写出，最后才停止读取。

## 34. Rename 等于 Delete + Add

把 `oldName` 改成 `newName`，从 wire 角度是删掉旧 key、增加新 key。Rust field 只改变量名则未必影响 wire。

迁移必须明确 reader 接受哪些名字，writer 输出哪个名字，以及双写是否会产生冲突。

## 35. Serde Alias

`#[serde(alias = "old_value")]` 让 reader 接受旧名字，同时 canonical serialization 仍输出新名字。

这是“宽读、窄写”的典型实现：输入兼容历史，输出逐渐收敛到一个标准形状。

## 36. `ApprovalsReviewer` 真实案例

固定提交的 `ApprovalsReviewer::AutoReview`：

```rust
#[serde(rename = "auto_review", alias = "guardian_subagent")]
AutoReview,
```

Reader 接受 `auto_review` 和旧值 `guardian_subagent`，writer 只输出 `auto_review`。

## 37. 为什么 Schema 仍列旧 Alias

该类型为 JsonSchema 自定义 enum values，把 `user`、`auto_review`、`guardian_subagent` 都列为可接受输入，并在说明中标记 legacy。

若 runtime 接受 alias 而 schema 拒绝，严格 client/validator 会在请求到达 server 前误判。

## 38. Canonical Writer

Canonical writer 始终产生一种推荐形状。它能让新数据自然淘汰旧拼法，减少系统长期同时生成两种值。

只要 reader 保留兼容窗口，旧数据仍可读，新数据则逐步收敛。

## 39. Dual Write 要谨慎

同时输出 `oldField` 和 `newField` 有时能帮助旧 reader，但会产生 precedence 问题：两者值不同时信谁？是否扩大 payload？旧 reader 是否真会忽略新字段？

只有明确消费方和冲突规则时才双写。

## 40. Default 作为兼容工具

新 reader 遇到旧数据缺少字段时，可以用 `#[serde(default)]` 恢复旧语义。例如固定提交让旧 Turn 缺少 `itemsView` 时默认为 `Full`。

Default 必须模拟“字段尚不存在的年代”原有行为，而不是随意选择当前产品偏好。

## 41. `isBlocking` 的真实案例

新版 `ToolRequestUserInputParams` 包含 required `isBlocking`，但测试证明旧 payload 缺少它时会默认 true。

这样新 reader 能读取历史/旧 sender 数据；新 writer 则可以显式发出字段，使合同更清楚。

## 42. Default 不是无风险兼容

若旧行为依赖上下文，单一 default 可能解释错误。需要先重建旧语义，再决定常量 default、custom deserializer 或 migration step。

默认值也应有 regression test，防止以后改 `Default` 实现而悄悄重解释历史数据。

## 43. Ignored Deprecated Field

有时为让旧 client 继续发送，server 保留字段但不再采用其值。固定提交中多个 `multiAgentMode` 字段被标记 deprecated/ignored，推荐改用 Ultra reasoning effort。

这保护了解析兼容，但必须明确“接受”不等于“仍然生效”。

## 44. 为什么 Ignored Field 仍可能回传固定值

某些 response/settings 中 deprecated `multiAgentMode` 始终报告 `explicitRequestOnly`。它为旧 client 保留预期 shape，同时把真实新行为来源迁到 reasoning effort。

固定投影比继续维护两套独立状态更可控。

## 45. Compatibility Projection

Compatibility projection 是从新内部模型计算一个旧协议字段，而不是继续让旧字段拥有独立真相。固定提交的 thread start/resume/fork response 保留 legacy `sandbox` 投影。

新客户端可读更精确的 permission profile，旧客户端仍看到熟悉的 sandbox shape。

## 46. Projection 的核心不变量

旧投影与新模型不能互相矛盾。若权限 profile 表示只读，而 legacy sandbox 投影成 full access，兼容层就造成安全错误。

Projection 应来自同一个 canonical state，并有完整对象/行为测试。

## 47. Input Bridge

新 request 仍接受 legacy `sandbox` shorthand，但不能与新 `permissions` 同时出现。它把旧输入映射到新内部权限模型。

拒绝两者并存是为了避免 precedence ambiguity，而不是故意降低兼容性。

## 48. Precedence Rule

若迁移期允许新旧字段同时出现，必须定义：新字段优先、旧字段优先、两者必须相同，还是直接报错。

“不能组合”常是最安全的规则，因为调用者会立即知道请求含糊。

## 49. Deprecated Method 的软迁移

保留旧 method 可给 client 升级时间。源码将 `getConversationSummary` 等列在 `DEPRECATED APIs below`，但仍保留 params、response 和 dispatch entry。

代码注释本身不通知远程客户端，仍需要文档、notice 或 telemetry 支持迁移。

## 50. `deprecationNotice`

v2 定义 `DeprecationNoticeNotification { summary, details }`。服务端可在旧功能仍工作时告诉客户端替代方案和原因。

这是“成功但应迁移”的信号，不应与立即失败的 error 混为一谈。

## 51. `thread/rollback` 案例

固定提交在非 TUI client 调用 `thread/rollback` 时，先向该 connection 发送 deprecation notice，再继续执行 rollback。

这是软弃用：调用没有立刻被切断，但 consumer 获得迁移提示。

## 52. 为什么 Notice 要定向到 Connection

弃用是由某个客户端请求触发的。只通知该连接可避免其他订阅同一 thread 的客户端收到与自己无关的警告。

多连接 server 中，广播范围本身也是协议语义。

## 53. Deprecated Notification 可保留但不再 Emit

`item/fileChange/outputDelta` 仍保留协议 entry，README 说明它为兼容保留但 server 不再发送。

这样旧录制数据或类型引用还能被解析，新 live client 则应迁移到 canonical FileChange lifecycle。

## 54. Retained for Parsing 与 Emitted Live 不同

一个 variant 可以存在于 enum/schema 中，却不再由实时路径产生。保留 reader 是为了历史数据，停止 writer 是为了协议收敛。

审查删除时必须分别搜索 deserialize、serialize、dispatch 和 event fan-out。

## 55. Core Event 与 App-server Event 不必一一对应

固定提交中 core 仍 fan out 某些 deprecated event，供 raw-event/rollout compatibility consumer；App-server v2 则把它们投影成 canonical TurnItem，或不再发旧 notification。

内部事件兼容期可比外部 wire notification 更长。

## 56. Tombstone

Tombstone 是保留一个名字或 ID 的“墓碑”，明确它已经停止使用，防止后来误把同一 wire value 赋予新含义。

保留 deprecated enum variant/方法常隐含 tombstone 作用，即使 live path 不再生成。

## 57. 不要复用旧 Method Name

旧客户端可能缓存、重放或迟到调用旧 method。如果新功能复用同名但改变 params/response，它会把旧流量误解释成新语义。

宁可增加新 method，也不要让同一 wire 名跨时代表达两个不兼容动作。

## 58. Enum 新增 Variant

服务端新增 response/notification enum value，对旧 client 是否兼容取决于 decoder 和 switch 是否允许 unknown/default。

Rust 服务端的 exhaustive match 只保护当前代码，不会替外部 TypeScript 客户端生成 fallback。

## 59. Unknown Variant 策略

可选策略包括：拒绝、映射成 `Other(String)`、保留 raw JSON、忽略单条 notification，或要求 capability 后才发送。

选择取决于是否能安全忽略。权限、安全和支付状态不应随便映射为 benign unknown。

## 60. Open Enum 与 Closed Enum

Closed enum 承诺值集合有限，consumer 可以穷尽处理；open enum 允许未来增加值，consumer 必须有 fallback。

JSON Schema 的 `enum` 常表现为 closed，但长期演进的 server-owned 状态可能需要更明确的扩展策略。

## 61. Request Method 通常是 Closed Set

Server 必须知道怎样执行 method；未知 method 无法安全“忽略并成功”。因此 method set 比 response 的额外字段更接近 closed contract。

Client 使用新 method 前需要版本锁步、能力探测或可处理的 unavailable error。

## 62. Notification 可以有不同策略

Notification 没有 response ID，consumer 有时能安全忽略不认识的方法。但若通知驱动资源生命周期，忽略可能导致状态永久错误。

协议文档应区分 informational 与 state-critical notification。

## 63. Notification Opt-out 不是 Version Negotiation

`optOutNotificationMethods` 让客户端按精确 method 名抑制已知通知。它不表示 server 自动选择某个完整协议版本。

Opt-out 适合降低不需要的流量，不适合隐藏所有不兼容变化。

## 64. Capability Detection 与 Error Probing

理想情况下 server 明确宣告能力，client 再选择行为。没有 capability surface 时，client 也可能调用并根据 method unavailable 降级。

Error probing 会制造失败请求和日志噪声，也容易混淆暂时错误与永久不支持，通常次于显式能力。

## 65. Client Version Gating 的风险

按 `clientInfo.version >= X` 猜能力会遇到 fork、backport、预发布字符串和第三方 client。同版本号未必同 capability。

若确实需要 version gate，必须采用明确的版本语义和 parser；更好的是协商具体 capability。

## 66. Server Version 也不是万能答案

Client 知道 server package version，仍不一定知道 feature flag、backend、平台或实验配置是否启用。

Package version 可以辅助诊断，不能替代 runtime capability。

## 67. API Generation 与 Runtime Gate 要对齐

稳定 TS/Schema 不应包含必须 experimental opt-in 才能使用的字段；实验 export 应包含它们。Runtime 又必须按同一 metadata 拒绝或过滤。

只有生成过滤而无 runtime gate，会让手写 JSON 绕过边界；只有 runtime gate 而稳定 schema 泄漏，会误导 client。

## 68. One Metadata Pipeline

固定提交通过 `#[experimental(...)]`、`ExperimentalApi` derive 和 request DSL `inspect_params`，尽量让 generation 与 runtime 共享实验原因。

共享 metadata 能减少“文档稳定、执行实验”这类双重真相。

## 69. Field-level Gate 的兼容性

稳定 method 加实验 field 时，旧 client 仍可调用稳定部分；opt-in client 才能发送实验字段。这比复制整个 method 更节省 surface。

但 server 必须能识别“params 中实际用了实验字段”，不能因为 method 大体稳定就跳过检查。

## 70. Nested Field 的兼容性

实验/新字段可能位于嵌套 object。顶层字段本身存在多年，不代表内部增加任何 required member 都安全。

Compatibility review 要沿整棵 payload tree，而不只看 RPC 顶层 params。

## 71. Persisted Data 是第五种参与者

Client/server 四象限之外，还有旧版本写下的 rollout、thread metadata 和缓存。新 server 恢复 thread 时必须读取它们。

这使兼容性多了一条“存储 writer 版本 → 当前 reader”的时间轴。

## 72. `cli_version` 在持久化中的意义

Session metadata 记录写出数据的 CLI version，可用于诊断和有条件迁移。但仅记录版本不会自动实现兼容。

Reader 仍需要 default、alias、custom deserializer、projection 或显式 migration。

## 73. Resume Contract

`thread/resume` 不只恢复一个 ID，还要把持久化 history 投影成当前 v2 `Thread`/`Turn`/`TurnItem`。旧记录可能没有现在新增的字段或 canonical item。

恢复测试应使用历史 shape，而不只是当前 writer 刚写出的文件。

## 74. Rebuild Legacy History

`protocol/thread_history.rs` 和相关 projection 逻辑会从旧 event/rollout item 重建用户消息附件、client ID、compaction-only turn 等当前模型需要的信息。

它是 data adapter：输入是历史事件流，输出是当前 API DTO。

## 75. Projection 与 Migration 的区别

Projection 每次读取时把旧记录解释成新视图，通常不改原文件；migration 则把存储格式永久转换成新形状。

Projection 易回滚但每次读都复杂；migration 简化未来读取但需要原子性、备份与失败恢复。

## 76. Lazy Migration

Lazy migration 在真正读取或写回某个 thread 时转换它。启动快、摊销成本低，但长期会同时存在多种格式。

所有路径必须能识别“尚未迁移”和“已经迁移”。

## 77. Eager Migration

Eager migration 在升级或启动时批量转换。完成后内部模型统一，但大数据量、崩溃中断和降级回旧版本都更难。

应有 progress、resume、atomic publish 和 corruption handling。

## 78. Read-old/Write-new

常见策略是 reader 接受多个历史 shape，writer 只写最新 canonical shape。随着数据被重新写出，旧格式自然减少。

它与 Serde alias 的“宽读、窄写”是同一个思想在存储层的应用。

## 79. Old Binary Reading New Data

新 writer 写出新字段/variant 后，用户若降级到旧 binary，旧 reader 未必能读取。这是 forward compatibility 和 downgrade support 问题。

支持升级不等于支持任意降级；产品必须明确承诺范围。

## 80. Rollback 与 Rollout 容易混淆

`thread/rollback` 是线程操作；rollout 是持久化会话记录。软件 release rollback 又是部署回退。三者名称相似但对象不同。

排错时先说清“回退代码版本、回退 thread 内容，还是读取 rollout 文件”。

## 81. Custom Deserializer 何时需要

Alias/default 无法表达字段组合、类型变化或旧值映射时，可写 custom deserializer。例如旧对象拆成新 enum，或特殊路径字符串需要验证/丢弃。

Custom code 权力更大，也更需 fixture test 和 bounded error message。

## 82. Tolerant Reader 的边界

宽松 reader 不等于吞掉所有错误。旧 `fullAccess` 字段在某些 sandbox shape 中可以忽略，但危险的受限读权限字段可能必须拒绝，防止解释成更宽权限。

安全语义优先于“尽量不报错”。

## 83. Fail Open 与 Fail Closed

Fail open 是解析失败后继续使用较宽松/默认行为；fail closed 是拒绝或采用更保守行为。权限、认证和审批迁移通常应 fail closed。

诊断性 advisory 字段则可能安全地 warning + omit。

## 84. Malformed Legacy Advisory Data

固定提交对某些 legacy instruction source path 做解析，畸形值会 warning/省略而不是阻止整个 thread 恢复，因为它们属于 advisory diagnostics。

同一种策略若用于 sandbox root 就可能不安全；策略必须随字段职责变化。

## 85. Error Contract 也要版本化

Client 往往依赖 error code 判断重试、提示或降级。把同一情况从 invalid params 改成 internal error，可能破坏客户端控制流。

错误 message 可供人读，但稳定程序逻辑应优先依赖明确 code/structured data。

## 86. JSON-RPC 标准码

固定提交定义 `-32600` invalid request、`-32601` method not found、`-32602` invalid params、`-32603` internal error，以及项目自定义 overload/input-too-large 标识。

新增兼容行为时应选择最接近真实失败层的 code，不要所有错误都塞进 internal error。

## 87. Error Message 不应承担隐形 Capability

让 client 用 substring 匹配“not supported yet”很脆弱，文案修改和本地化都会破坏判断。

若降级逻辑重要，应使用稳定 code、data discriminator 或 capability field。

## 88. Streaming Compatibility

流式 API 不只包含最终 response，还包含 started、delta、completed、error 的顺序和终态保证。新增/删除一类 delta 可能破坏 client state machine。

迁移时需保证旧 client 仍能到达 terminal state，不能只比较单条 JSON shape。

## 89. Event Ordering Contract

字段完全相同但事件顺序改变，也可能 breaking。例如 completed 在最后一个 delta 前到达，旧 client 会提前关闭 UI。

Compatibility tests 应验证完整 journey，而不仅是 serde round trip。

## 90. Idempotency 与 Retry

Client 在网络断开后重试 request，旧新 server 对重复调用的处理也属于行为合同。创建、删除、审批等 mutation 尤其需要稳定 ID 或明确 non-idempotent 说明。

版本迁移不能让原本安全的 retry 变成重复副作用。

## 91. Pagination Contract

新增 cursor pagination 时，cursor 是 opaque server token。Client 不应解析内部格式，server 也要定义 cursor 在数据变化、升级和错误输入时的行为。

把 cursor 内部结构暴露给 client 会使未来存储迁移变成 wire breaking change。

## 92. Opaque ID

Thread ID、turn ID、request ID 应按协议声明作为 opaque identity 传递。Client 根据长度、UUID 版本或前缀猜语义，会阻碍 server 改变生成策略。

强类型用于避免混用，不代表允许外部拆解内部编码。

## 93. Deprecation Lifecycle

一项可控弃用通常经历：

1. 提供替代 API。
2. 文档标记 deprecated。
3. Runtime notice/telemetry 观察使用量。
4. Client 停止依赖。
5. Server 停止 emit，但保留 read。
6. 支持窗口结束后删除 dispatch/read。
7. 保留 tombstone，避免名字复用。

## 94. Telemetry 的作用

没有使用数据时，很难知道旧 method 是否还能删除。Initialize 的 client name/version 和 request tracking 可帮助识别调用来源。

Telemetry 只能辅助决策，不能替代公开迁移承诺和 consumer 沟通。

## 95. Sunset Date

若产品承诺删除时间，应明确最后支持版本/日期、替代接口和失败方式。模糊的“soon”容易让 consumer 无法排期。

固定源码注释可提醒维护者，但对外合同仍需要用户可见渠道。

## 96. Compatibility Test 层次

至少需要：

- Serde test：旧 JSON 能否读、canonical JSON 怎样写；
- Schema fixture：可接受 shape 是否正确公开；
- Processor test：gate/error/notice 是否正确；
- Journey test：旧新交互和事件顺序；
- Persistence fixture：真实旧 rollout 能否 resume；
- Build/export test：stable/experimental 文件是否同步。

## 97. Golden Old Payload

不要用当前 struct serialize 后再 deserialize 来假装验证旧格式；它只证明当前格式 round trip。

测试应手写或保存真正历史 JSON，明确缺少哪些字段、使用哪些 alias 和旧 enum value。

## 98. Negative Compatibility Test

宽读仍需边界测试：冲突的新旧字段是否拒绝、危险 legacy 权限是否 fail closed、未 opt-in 的实验字段是否报错。

只测“旧东西都能读”会把过度宽松误当成功。

## 99. Whole-object Equality

迁移测试应尽量比较解析后的完整对象，证明 default/alias 没有让其他字段丢失。只断言一个目标字段容易漏掉 silent data loss。

这也符合仓库测试约定中的 deep equality 偏好。

## 100. 一次字段迁移的设计模板

写代码前回答：

1. 旧 reader/writer 的 shape 是什么？
2. 新 canonical shape 是什么？
3. Reader 接受窗口多长？
4. Writer 何时停止旧输出？
5. 冲突字段如何处理？
6. Stable/experimental 哪边可见？
7. 持久化旧数据如何恢复？
8. 哪些测试证明四象限与安全边界？

## 101. 源码阅读路线

建议依次读：

1. `protocol/v1.rs` 的 initialize/client capabilities。
2. `protocol/common.rs` 的新旧 method 共存与实验 metadata。
3. `initialize_processor.rs` 的连接状态建立。
4. `message_processor.rs` 和 `transport.rs` 的双向 gate。
5. `protocol/v2/shared.rs` 的 alias/canonical writer。
6. `protocol/v2/tests.rs` 的 legacy payload regression。
7. `thread_processor.rs` 的 deprecation notice。
8. `thread_history.rs` 的旧记录 projection。

## 102. 源码检查点

- `codex-rs/app-server-protocol/src/protocol/v1.rs`：ClientInfo、InitializeCapabilities 和 default capability。
- `codex-rs/app-server-protocol/src/protocol/common.rs`：v1/v2 method 共存、experimental reason、deprecated notification entry。
- `codex-rs/app-server/src/request_processors/initialize_processor.rs`：Once-only initialize 和 per-connection state。
- `codex-rs/app-server/src/message_processor.rs`：未初始化、实验 request gate 和 client version 保存。
- `codex-rs/app-server/src/transport.rs`：实验 notification 跳过与 server-request field stripping。
- `codex-rs/app-server-protocol/src/protocol/v2/shared.rs`：`guardian_subagent` alias 与 canonical `auto_review`。
- `codex-rs/app-server-protocol/src/protocol/v2/tests.rs`：legacy missing field/default/alias tests。
- `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`：ignored deprecated field 和 sandbox compatibility projection。
- `codex-rs/app-server/src/request_processors/thread_processor.rs`：rollback deprecation notice。
- `codex-rs/app-server/src/bespoke_event_handling.rs`：deprecated core event 与 v2 canonical projection 边界。
- `codex-rs/app-server-protocol/src/protocol/thread_history.rs`：legacy rollout/history rebuild。
- `codex-rs/app-server/src/error_code.rs`：JSON-RPC 和项目错误码。

## 103. 练习一：不要猜版本

看到 `clientInfo.version = "0.1.0"`，从源码证明它被存在哪里、传到哪里，并证明哪段代码没有用它选择 v1/v2。再找出真正控制 experimental request 的字段。

## 104. 练习二：设计 Rename 迁移

把 `guardian_subagent` → `auto_review` 案例扩展成字段 rename：写出 alias、canonical writer、schema、冲突规则、deprecation notice、stable/experimental 以及旧持久化数据测试。

## 105. 练习三：分析四象限

对“新增 required response field”和“新增 optional request field”分别填写四象限。不要只写兼容/不兼容，要说明 reader 是否忽略 unknown、writer 是否省略、是否需要 capability。

## 106. 练习四：区分停止写与停止读

以 `item/fileChange/outputDelta` 为例，解释为什么 server 不再 live emit 后，协议 variant 仍可能暂时存在；列出可安全彻底删除前要搜的 consumer：历史 rollout、raw-event、生成 TS/Schema 和外部客户端。

## 107. 本章结论

固定提交中的 App-server 不是通过一个数字切换 v1/v2，而是在同一 method surface 中保留旧入口、发展 v2 入口，并用 initialize capabilities 协商实验能力。兼容性主要靠 additive change、safe default、alias、canonical writer、compatibility projection、实验 gate、deprecation notice 和历史数据 adapter 实现。

真正的协议演进不能只看新 client + 新 server。它必须同时保护旧输入、未来未知值、安全语义、流式顺序和持久化恢复，并明确“何时停止写旧格式”与“何时最终停止读旧格式”是两个不同决定。

## 108. 本章 Glossary

| 术语/代码短语 | 直译或展开 | 在本章中的含义 |
|---|---|---|
| Protocol evolution | 协议演进 | 已被使用的 wire contract 随时间增加、迁移和弃用 |
| Backward compatibility | 向后兼容 | 新 reader/server 仍理解过去版本产生的输入 |
| Forward compatibility | 向前兼容 | 旧 reader 能安全面对未来版本产生的扩展数据 |
| Reader / writer | 读取者/写入者 | 解析 JSON 的一方和产生 JSON 的一方 |
| Client version | 客户端版本 | ClientInfo 中的软件版本标识，不自动等于 API 代际 |
| Protocol generation | 协议代际 | v1/v2 等 API 设计阶段与 namespace |
| Handshake | 握手 | initialize request + initialized notification 建立连接状态 |
| Capability negotiation | 能力协商 | 对端显式声明支持/选择的具体协议能力 |
| Safe default | 安全默认值 | 字段缺席时采用不会意外扩大能力的行为 |
| Per-connection | 每连接 | Capability 与状态只对当前 JSON-RPC 连接生效 |
| Additive change | 加法变更 | 保留已有 surface 并增加 method/field/variant |
| Serde alias | Serde 别名 | Reader 接受旧名字，writer 仍可输出新 canonical 名 |
| Canonical writer | 规范写入者 | 始终输出唯一推荐的新格式 |
| Wide read / narrow write | 宽读/窄写 | 接受多代输入，但只产生当前标准输出 |
| Dual write | 双写 | 迁移期同时输出旧字段与新字段 |
| Compatibility projection | 兼容投影 | 从新 canonical state 计算旧 API 所需视图 |
| Input bridge | 输入桥接 | 把旧 request shape 映射到新内部模型 |
| Precedence | 优先级 | 新旧字段同时出现时决定哪一个生效的规则 |
| Deprecation | 弃用 | 仍可能可用但已要求 consumer 迁移的状态 |
| Deprecation notice | 弃用通知 | Runtime 告知调用者替代方式的非致命消息 |
| Sunset | 终止支持 | 在承诺窗口后移除旧能力的阶段 |
| Tombstone | 墓碑 | 保留旧 wire 名已停用的事实，防止复用成新语义 |
| Unknown variant | 未知变体 | 旧 reader 没有定义的未来 enum/discriminator value |
| Open/closed enum | 开放/封闭枚举 | 是否承诺 consumer 必须容忍未来新增 variant |
| Error probing | 错误探测 | 尝试调用并从 unavailable error 推断能力 |
| Persisted data | 持久化数据 | Rollout/thread metadata 等跨软件升级继续存在的记录 |
| Projection | 投影 | 读取时将旧记录解释成当前 DTO，不一定改写原文件 |
| Migration | 迁移 | 永久把存储数据转换为新格式 |
| Lazy/eager migration | 惰性/预先迁移 | 按需转换单项或升级时批量转换 |
| Read-old/write-new | 读旧写新 | Reader 保持历史兼容、writer 只写新格式 |
| Downgrade support | 降级支持 | 旧 binary 能否读取/处理新版本已经写出的数据 |
| Fail open / fail closed | 开放失败/封闭失败 | 解析异常后继续宽松行为或拒绝/采用保守行为 |
| Opaque ID/cursor | 不透明 ID/游标 | Consumer 只能传递、不应解析内部编码的标识 |
| Golden old payload | 黄金旧样本 | 真正保存/手写的历史 JSON regression fixture |
| Four-quadrant matrix | 四象限矩阵 | 旧/新 client 与旧/新 server 的四种组合 |
