# 89：App-server 审批状态机、Server Request、用户输入与多客户端协作

> 源码基线：4ee41929eaf4。本章解决“Agent 等待审批或用户回答时，App-server 如何暂停工作、把问题发给一个或多个客户端、接收第一份有效响应、关闭其他客户端的重复 UI，并把决定安全地送回 Core”。

## 1. 本章解决什么问题

Agent 不能对所有命令、文件修改或权限请求直接执行。有些工作必须停下来问用户。

问题不是只做一个弹窗，而是建立一条不会重复执行、不会答错对象、不会在 Turn 已结束后继续使用旧答案的异步状态机。

## 2. 资料边界

当前[官方 OpenAI App Server 文档](https://learn.chatgpt.com/docs/app-server)给出了审批、`tool/requestUserInput`、权限请求和 MCP elicitation 的 wire 顺序。本章实现细节来自固定提交 4ee41929eaf4。

当前文档与固定提交可能继续演进；代码字段和异常路径以固定提交为准。

## 3. 先说人话：柜员暂停办理

把 Agent 想成柜员。遇到高风险步骤时，柜员把当前业务暂停，向有权限的窗口发出确认单。

任何一个窗口先给出答案，柜员就继续办理；其他窗口必须把同一张确认单收起来。

## 4. 为什么是 Server Request

普通 notification 只负责告知，不要求对方回答。

审批需要客户端返回结构化决定，因此 App-server 主动发送带 ID 的 JSON-RPC request。

## 5. 双向 JSON-RPC

通常是 client 请求 server；审批时方向反过来：

```text
App-server  -> client: request(id, method, params)
client      -> server: response(id, result)
```

同一条 transport 同时承载两个调用方向。

## 6. Server Request 不是 Notification

Server Request 有 `id`，期待 success response 或 error response。

如果客户端只显示 UI 却不响应，等待该结果的 Core 工作可能一直停住。

## 7. 一条标准审批时间线

官方合同的核心顺序是：

```text
item/started
server request
client response
serverRequest/resolved
item/completed
```

这五步分别描述业务 Item、待回答 RPC、答案、UI 收口和最终业务结果。

## 8. 为什么既有 Item 又有 Request

Item 是“正在执行的命令或文件修改”。

Request 是“这一次需要人回答的审批问题”。同一个 Item 在复杂场景中可能对应多个审批 callback。

## 9. 四类常见 ID

审批代码里经常同时出现：

- threadId
- turnId
- itemId
- requestId

它们不是重复字段，而是四个不同作用域的身份。

## 10. threadId

threadId 定位整条对话任务线。

多窗口同时打开不同 Thread 时，必须先用它隔离 UI 状态。

## 11. turnId

turnId 定位哪一轮 Agent 工作正在等待。

旧 Turn 的迟到答案不能应用到新 Turn。

## 12. itemId

itemId 定位具体命令、文件变更、权限工具调用或用户输入工具调用。

它连接 `item/started`、审批 request 和最终 `item/completed`。

## 13. requestId

requestId 对位一次 Server Request 与一次 response。

它属于 RPC 生命周期，不等于 itemId。

## 14. 为什么不能用 itemId 代替 requestId

固定提交支持 subcommand approval：一个父 command Item 可产生多个独立审批 callback。

所有 callback 共用 itemId 时，必须靠另外的 requestId 或 approvalId 区分。

## 15. approvalId

CommandExecutionRequestApprovalParams 还有可选 approvalId。

普通 shell/unified_exec 为 null；zsh bridge 的 subcommand approval 使用独立 opaque UUID 路由。

## 16. 三种“调用 ID”不要混淆

- itemId：UI 业务对象。
- approvalId：Core 内部特定审批 callback。
- JSON-RPC requestId：App-server 等待客户端 response 的键。

名字都像 ID，但生命周期完全不同。

## 17. ServerRequestPayload

固定提交的 Server Request DSL 包含：

- command execution approval
- file change approval
- tool request user input
- MCP elicitation
- permission approval
- dynamic tool call
- auth token refresh
- attestation
- current time read
- legacy approvals

并非每种 Server Request 都是“安全审批”。

## 18. Dynamic Tool Call 不是普通审批

`item/tool/call` 是让 client 执行客户端提供的动态工具，并返回内容。

它也使用同一 pending callback 基础设施，但语义是能力调用，不只是 accept/decline。

## 19. PendingCallbackEntry

OutgoingMessageSender 用 HashMap 保存：

- oneshot callback sender
- 可选 threadId
- 原始 ServerRequest

这三项分别用于唤醒等待者、按 Thread 取消/重放、重新发送相同请求。

## 20. Callback 是什么

Callback 在这里不是同步函数指针，而是一条 oneshot channel 的发送端。

发送响应后，等待 receiver 的异步 task 才会继续。

## 21. 为什么用 oneshot

一次 Server Request 只应该有一个最终结果。

oneshot 的类型语义正好表达“最多成功交付一次”。

## 22. next_server_request_id

App-server 用 AtomicI64 生成递增 Server Request ID。

固定提交的 fetch_add 使用 Relaxed，因为唯一编号不需要借它建立其他内存数据的同步顺序。

## 23. Server Request ID 的作用域

request_id_to_callback 是 OutgoingMessageSender 进程内的全局 map。

因此同时发给多个 connection 的同一逻辑请求仍使用同一个 ID 和同一个 callback entry。

## 24. Client Request ID 与 Server Request ID

Client 发来的 request ID 由 connection namespace 隔离。

Server 主动生成的 request ID 由 App-server 自己统一分配，不能把两个方向的管理方式混为一谈。

## 25. 发送前先登记 Callback

send_request_to_connections 先把 entry 插入 map，再把 request 放进 outgoing channel。

这防止极快 response 返回时找不到等待者。

## 26. 经典竞态：先发后登记

错误顺序：

```text
send request
client immediately responds
lookup callback -> missing
register callback too late
```

先登记再发送消除了这个窗口。

## 27. 发送失败时清理

若 outgoing channel 发送失败，固定提交会移除刚登记的 callback entry。

否则 map 中会留下永远不可能收到响应的僵尸请求。

## 28. Receiver 会发生什么

entry 被移除且 sender 被 drop 后，receiver 得到 RecvError。

不同业务 handler 再把这种基础设施失败映射成拒绝、空回答、取消或 failed。

## 29. ThreadScopedOutgoingMessageSender

它把通用 OutgoingMessageSender 包装成：

- 固定 threadId
- 固定 subscriber connection 列表

审批请求因此只发给当前 Thread 的订阅者，而不是所有无关客户端。

## 30. Connection 列表是快照

ThreadScoped sender 创建时持有 connection IDs 的 Arc<Vec>。

后加入的 subscriber 需要通过 pending request replay 获取尚未完成的请求。

## 31. 为什么需要 Replay

用户在桌面端触发命令，然后从另一客户端重新加入 Thread；审批仍在等待。

若不 replay，新客户端看见 Active/WaitingOnApproval，却没有可回答的请求。

## 32. pending_requests_for_thread

该函数从 callback map 中筛选 threadId 相同的 entries，clone 原始 request，再按 request ID 排序。

排序让重放顺序稳定。

## 33. Replay 不创建新 Request

重放发送原始 ServerRequest，保留相同 request ID。

它没有创建第二个 callback，也不会让 Core 等待两次。

## 34. 多客户端共享一个 Pending Request

同一 Server Request 可以被发送或重放给多个 connection。

它们看到的是同一个问题，竞争回答同一个 callback entry。

## 35. 首答胜出

notify_client_response 先调用 take_request_callback，从 map 中 remove entry。

第一份响应取得 entry 并完成 oneshot；第二份响应查不到 entry，只记录 warning。

## 36. 为什么 Remove 就能实现首答胜出

remove_entry 在 Mutex 保护的 map 内完成。

两个 response 并发到达时，只有一个能取走同一个 key。

## 37. 这是业务竞选，不是投票

系统不会收集多个客户端意见后多数决。

第一份被 App-server 处理的 response 直接成为结果。

## 38. Client 应怎样显示“另一个窗口已回答”

不能只等自己的 response 成功与否，因为 JSON-RPC response 是 client 发给 server 的消息。

App-server 另发 `serverRequest/resolved`，让所有订阅 UI 收口。

## 39. serverRequest/resolved

通知包含：

- threadId
- requestId

客户端用 requestId 找到弹窗，用 threadId 防止跨 Thread 误关。

## 40. Resolved 不携带最终决策

它的职责是说明请求已不再待处理，而不是广播谁选择了什么。

业务最终结果从 ItemCompleted、Turn 状态或其他对应事件观察。

## 41. 为什么不把决策放进 Resolved

审批回答可能包含敏感信息或复杂类型；不同请求的结果 shape 也不同。

统一 resolved 通知只承诺生命周期结束，保持协议简单。

## 42. Resolved 的排序

各 response handler 等待 callback 后，通过 ThreadListenerCommand 请求 listener 发 resolved。

这样 resolved 与其他 Thread 通知共享 listener 顺序。

## 43. 为什么不在 notify_client_response 立即广播

底层 callback map 只知道 RPC 已被取走，不一定拥有正确的 Thread listener 排序上下文。

业务 handler 通过 thread_state 把 resolved 排进正确的 per-thread 流。

## 44. ResolveServerRequest Command

ThreadListenerCommand::ResolveServerRequest 携带 requestId 和一个 completion oneshot。

调用方等待 completion，确保 resolved 已进入 listener 的发送顺序后再继续后续业务提交。

## 45. 两层 oneshot

这里容易看晕：

- 第一条 oneshot 等 client response。
- 第二条 oneshot 等 listener 完成 resolved notification。

它们分别控制“答案到达”和“UI 收口顺序”。

## 46. 为什么要先 Resolved 再继续

若先让 Core 继续，很快可能产生 item/completed 或下一条事件，而旧审批弹窗仍在其他客户端上。

先排 resolved 能让 UI 生命周期更清楚。

## 47. Resolved 与真正写出之间的边界

send_server_notification 把消息送进 outgoing channel；通常不等于客户端屏幕已经渲染。

合同是服务端输出排序，不是端到端视觉确认。

## 48. Command Approval 的字段

固定提交可能发送：

- command/cwd/commandActions
- reason
- environmentId
- networkApprovalContext
- additionalPermissions
- proposed policy amendments
- availableDecisions

客户端不能假设每一项都存在。

## 49. 为什么 command 可以为 null

Managed network approval 的重点是目标网络访问，不一定把 shell command 作为有意义的展示主体。

UI 应按 networkApprovalContext 渲染网络专用提示。

## 50. commandActions

它是 best-effort 解析出的 Read、ListFiles、Search 或 Unknown 等友好动作。

它适合改善展示，不是重新执行命令的权威 argv。

## 51. availableDecisions

服务端可告诉客户端这一提示允许展示哪些决定。

客户端应尊重服务端集合，不要永远硬编码所有按钮。

## 52. Accept

Accept 只批准当前请求。

它映射为 Core ReviewDecision::Approved。

## 53. AcceptForSession

AcceptForSession 还允许匹配的未来请求使用 session-scoped approval cache。

它不是永久修改操作系统权限，也不应被 UI 文案成“永远允许”。

## 54. Decline

Decline 拒绝当前操作，但 Agent 可以继续本 Turn，寻找替代方案。

命令或文件 Item 通常最终显示 declined。

## 55. Cancel

Cancel 不仅拒绝当前操作，还要求立即中断 Turn。

因此 Decline 与 Cancel 必须用不同按钮文案和确认提示。

## 56. AcceptWithExecpolicyAmendment

它批准当前命令，同时接受服务端提出的 execpolicy amendment。

未来匹配命令可能无需再提示；客户端不应自行构造未被提议的规则。

## 57. Network Policy Amendment

用户可选择对目标 host 应用 allow 或 deny 规则。

Deny amendment 会把当前命令完成状态映射为 Declined；allow 则继续执行。

## 58. Policy Amendment 不是普通 Accept

它改变未来相似请求的政策行为，影响范围更大。

UI 应把“本次允许”和“保存规则”区分清楚。

## 59. File Change Approval

文件变更审批 params 包含 threadId、turnId、itemId、开始时间、reason 和可选 grantRoot。

它的决策集合较简单：Accept、AcceptForSession、Decline、Cancel。

## 60. grantRoot 的边界

固定提交把 grantRoot 标成不稳定，并明确注释其是否实际生效并不清楚。

学习时不要把字段存在误认为完整产品保证。

## 61. 审批请求之前的 ItemStarted

命令或文件修改会先成为 InProgress Item，再进入审批等待。

UI 可以在同一条 Item 上显示“等待批准”，而不是另外创建孤立弹窗记录。

## 62. 审批后的 ItemCompleted

批准后工作继续执行，最终 Item 可能 Completed 或 Failed。

拒绝时 Item 可直接以 Declined 完成；completed 仍是权威业务终态。

## 63. 审批 Response 不等于 Item Completed

Accept 只是允许执行，命令仍可能退出非零或因环境错误失败。

所以客户端不能点击 Accept 后立刻把命令标成成功。

## 64. Request User Input

Agent 可以调用 request_user_input 工具向用户问结构化问题。

它不是安全批准，但同样会暂停或协调 Turn，因此也走 Server Request callback。

## 65. Question 的字段

固定提交的问题包含：

- id
- header
- question
- isOther
- isSecret
- optional options

question id 用于 response map 对位答案。

## 66. 为什么每个问题还有 ID

一个 request 可以包含多个问题。

response 用 HashMap 从 question ID 映射到答案列表，不能靠显示顺序猜测。

## 67. Answer 是字符串数组

每个 ToolRequestUserInputAnswer 保存 `answers: Vec<String>`。

这同时覆盖单选、多选和自由输入场景。

## 68. isOther

isOther 表示 UI 是否支持用户填写预设选项之外的内容。

它不是“自动添加 Other 选项”的唯一产品合同，客户端仍应按协议版本实现。

## 69. isSecret

isSecret 提醒客户端答案属于敏感输入。

UI 应遮罩显示，并避免把答案写入日志、analytics、错误报告和剪贴板预览。

## 70. Secret 不等于自动端到端加密

字段只描述内容性质和 UI 行为。

传输安全仍依赖安全 transport、认证和客户端存储策略。

## 71. isBlocking

isBlocking 表示请求是否应该阻塞当前工作。

固定提交在反序列化旧 payload 缺失该字段时默认 true。

## 72. autoResolutionMs

固定提交仍携带 autoResolutionMs，但注释已建议用 isBlocking 判断是否阻塞。

当前官方文档说明 host 可在给定时间后自动解决；实现客户端时应以实际版本 schema 为准。

## 73. 用户输入 Response

Response shape 是：

```text
answers[questionId] = { answers: ["..."] }
```

未知 question ID、缺失答案和空数组都应有明确客户端策略。

## 74. Malformed 用户输入响应

固定提交反序列化失败时记录错误，并向 Core 提交空答案 map。

这是一种“不要卡死 Turn”的 fallback，但业务上不代表用户真的选择了空答案。

## 75. Client Error 的用户输入处理

若 client 返回普通 JSON-RPC error，handler 同样向 Core 提交空答案。

若错误原因是 turnTransition，则直接返回，不向旧 Turn 注入答案。

## 76. turnTransition

App-server 在 Turn 开始、完成或中断导致 pending request 失效时，使用结构化 error data：

```text
reason = "turnTransition"
```

业务 handler 用它区分正常状态转换取消和真实客户端失败。

## 77. 为什么不能只比较错误文本

文本可能改写、翻译或补充上下文。

结构化 reason 是更稳定的程序判断条件。

## 78. TurnStarted 时清理旧请求

固定提交在处理 TurnStarted 时调用 abort_pending_server_requests。

这是额外保险：上一轮遗留的审批不能穿越到新 Turn。

## 79. TurnComplete 时清理

Turn 完成后，所有该 Thread 的 pending requests 都不再属于可继续的工作。

取消 callback 会唤醒等待 task，让它们走 turnTransition 分支退出。

## 80. TurnAborted 时清理

中断同样取消 pending requests。

否则用户稍后点击旧审批，可能把答案错误应用到已终止 Turn。

## 81. Cancel Request 与 Cancel Decision

二者不同：

- cancel_request：App-server 内部移除 pending RPC callback。
- `Cancel` decision：用户拒绝操作并中断 Turn。

看到 cancel 一词时要先确认对象。

## 82. cancel_requests_for_thread

函数在一次 map lock 中先找出该 Thread 的 request IDs，再移除 entries。

之后在锁外向各 oneshot sender 发送同一 error。

## 83. 为什么锁外发送

Channel send 或后续 drop 可能唤醒其他 task。

先释放 map mutex 能缩小临界区并避免不必要的锁耦合。

## 84. 为什么批量移除

同一 Turn 可能同时等待多个请求，例如工具输入、权限或动态工具。

Thread 终结时必须全部清理，不能只取消屏幕上最显眼的一项。

## 85. cancel_all_requests

进程关闭等全局场景会 drain 整个 callback map。

若提供 error，会将 error 发给所有等待者；否则 sender drop 使 receiver 得到 RecvError。

## 86. Permission Guard

ThreadWatchManager 在审批发出时创建 ThreadWatchActiveGuard，并增加 pending permission counter。

这让 ThreadStatus 的 activeFlags 包含 WaitingOnApproval。

## 87. User Input Guard

request_user_input 使用另一类 guard，增加 pending user input counter。

ThreadStatus 因此可显示 WaitingOnUserInput，而不是把所有等待都叫审批。

## 88. RAII Guard

Guard 被 drop 时异步减少对应 counter。

不管 response 正常、错误还是 callback channel 关闭，离开 handler 都有机会收口状态。

## 89. 为什么用 Counter 而不是 Bool

一个 Thread 可能同时存在多个待处理请求。

若使用 bool，第一项完成就会错误清除仍存在的第二项等待状态。

## 90. Saturating Add

pending counter 增加使用 saturating_add。

极端溢出时停在最大值，而不是 wrap 回零并错误显示无人等待。

## 91. Active Flags 的生成

pending_permission_requests > 0 产生 WaitingOnApproval。

pending_user_input_requests > 0 产生 WaitingOnUserInput；两者可同时出现。

## 92. Guard 释放是异步的

Drop 内部 spawn 一个 task 更新 manager。

因此 response 完成到 ThreadStatusChanged 发出之间可能有很短的调度延迟。

## 93. UI 不应只靠弹窗推断 ThreadStatus

弹窗可能已在某客户端关闭，而 status notification 尚在路上。

分别维护 pending request UI 和 ThreadStatus，再按事件最终收敛。

## 94. Permission Request

`item/permissions/requestApproval` 请求结构化网络或文件系统权限。

Response 不是简单 bool，而是 granted permission profile 加 grant scope。

## 95. Requested 与 Granted

服务端保存原始 requested permissions。

收到客户端 granted profile 后，会与 requested profile 求交集。

## 96. 为什么必须求交集

假设请求只要读取 A，但恶意或有 bug 的客户端响应“允许写 B”。

交集确保未请求的能力不会因 response 被扩大。

## 97. Least Privilege

只授予当前操作确实请求且用户同意的最小权限，称为最小权限原则。

权限响应校验是安全边界，不只是 schema 转换。

## 98. PermissionGrantScope

固定提交支持：

- Turn
- Session

Turn 只覆盖当前轮；Session 可影响同一 session 后续工作。

## 99. Session Scope 的风险

Session scope 减少重复提示，但扩大授权持续时间。

UI 应明确说明影响后续操作，不能只写模糊的“允许”。

## 100. strictAutoReview

它表示本 Turn 后续每个命令在正常沙箱执行前都经过严格自动审查。

固定提交只允许它用于 Turn-scoped grant。

## 101. strictAutoReview 与 Session 冲突

若响应同时选择 session scope 和 strict auto review，固定提交记录错误并回退为空权限、Turn scope、strict=false。

这是 fail closed。

## 102. Path Localize 失败

若 granted filesystem paths 无法本地化，App-server 发 Turn error 并中断 Turn。

错误路径不能被静默当成已授权路径。

## 103. Malformed Permission Response

反序列化失败时使用空 GrantedPermissionProfile、Turn scope。

权限类错误默认不给权限，符合安全保守原则。

## 104. Turn Transition 下的 Permission Response

若 callback 因 turnTransition 被取消，转换函数返回 None。

handler 不再向 Core 提交一个伪造的空权限答案。

## 105. MCP Elicitation

MCP server 也可以暂停流程向用户索取结构化内容或 URL 操作。

App-server 把内部 request 转成 typed MCP elicitation Server Request。

## 106. Elicitation 的三种 Action

- Accept
- Decline
- Cancel

Accept 携带内容；Decline/Cancel 通常不携带内容。

## 107. MCP Request ID 与 JSON-RPC Request ID

MCP elicitation 自己有 MCP request ID；App-server 转发时又创建 Server Request ID。

handler 保存两者，响应到来后再把决定路由回原 MCP request。

## 108. Elicitation 转换失败

若内部 elicitation schema 无法转换成 typed wire shape，固定提交直接向 Core 提交 Cancel。

它不会向 client 发送一个无法正确表达的问题。

## 109. Malformed Elicitation Response

反序列化失败或普通 callback error 时默认 Decline。

turnTransition 则映射 Cancel，表达请求因生命周期变化而失效。

## 110. Decline 与 Cancel 的语义仍不同

Decline 表示用户或客户端没有提供请求内容。

Cancel 更强调交互被状态变化或主动取消终止。

## 111. Dynamic Tool Call 流程

```text
item/started(dynamicToolCall)
item/tool/call server request
client returns contentItems + success
item/completed(dynamicToolCall)
```

它复用相同 callback map，但固定提交的 dynamic_tools::on_call_response 没有像审批 handler 那样发送 serverRequest/resolved。客户端不能把“所有 Server Request 都一定有 resolved”当成固定提交的普遍合同。

## 112. Auth 与 Attestation 请求

全局 Server Request 也可用于 token refresh、attestation 或 current time。

这些 request 的 threadId 为 None，不参与按 Thread 的 pending replay/cancel。

## 113. Thread-scoped 与 Global Pending

PendingCallbackEntry 的 thread_id 是 Option。

Some 用于审批和 Thread 工具；None 用于全局客户端能力调用。

## 114. 为什么不能把所有 Request 都按 Thread 取消

Token refresh 可能服务整个 App-server，而不属于某个 Turn。

Turn 完成不应误杀无关全局 request。

## 115. Client 返回 Error Response

notify_client_error 同样 remove callback，并把 JSONRPCErrorError 送入 waiter。

它仍遵守“只有第一份终结消息能取得 entry”。

## 116. Unmatched Response

找不到 callback 可能因为：

- 另一客户端已回答
- Turn transition 已取消
- request 已主动 cancel
- response ID 错误
- 重复 response

日志不能直接把它全部判定成攻击。

## 117. 但 Unmatched 仍应观测

持续大量 unmatched responses 可能表示客户端 bug、版本不匹配或错误重试。

建议按 method、connection 和原因做有界指标，不记录敏感 payload。

## 118. Response Shape 校验

底层 callback 先交付通用 JSON value。

各业务 handler 再反序列化为 Command、File、UserInput、Permission 等具体 response 类型。

## 119. 为什么不在底层统一解析

不同 ServerRequest method 的 response shape 不同。

原始 request 被保存在 entry 中，可用于 analytics 类型识别；业务语义仍由对应 handler 负责。

## 120. Malformed Command Response

命令审批 response 解析失败时映射为 denied，并将兼容 completion Item 标成 Failed。

它不会默认 Accept。

## 121. Malformed File Response

文件审批 response 解析失败时映射为 denied("approval request failed")。

文件写入安全边界同样 fail closed。

## 122. 普通 Client Error 的默认值

不同业务有不同 fallback：

- command/file：deny 或 failed
- permission：空权限
- user input：空答案
- MCP elicitation：decline

不能写一个全局“error 等于 cancel”规则。

## 123. 为什么 Fallback 不统一

安全审批的默认应拒绝；普通信息输入可能用空值让 Agent 自行处理；生命周期取消则应静默停止旧请求。

Fallback 是领域语义，不只是错误处理风格。

## 124. 审批与 Core Op

解析完成后，App-server 向 CodexThread 提交对应 Op：

- ExecApproval
- PatchApproval
- UserInputAnswer
- RequestPermissionsResponse
- ResolveElicitation

这才真正让 Core 状态机继续。

## 125. 为什么不直接执行命令

App-server 是协议适配层，不应绕过 Core 的审批通道自行运行操作。

提交 Op 保留 Core 对 item、sandbox、Turn 和持久化的统一管理。

## 126. Submit 失败

若 conversation.submit 失败，固定提交记录 error。

此时 RPC 已 resolved，但业务可能没有继续；客户端最终应以 Item/Turn 后续状态为准。

## 127. Multi-client 安全模型

固定提交的首答胜出机制解决重复响应和 UI 协调。

它本身不证明每个已连接客户端都拥有相同组织权限；连接认证和授权仍属于 transport/deployment 边界。

## 128. 不要把“能订阅”自动等同“能审批”

当前实现会向 Thread subscribers 发送请求。

若产品需要审批角色区分，应在连接能力、订阅授权或路由层另建明确策略。

## 129. Confused Deputy

若低权限 client 能借高权限 server 批准它本无权批准的操作，就可能形成 confused deputy 问题。

部署层必须验证谁能连接、订阅和回答敏感请求。

## 130. Response 不携带 Connection Identity 给 Callback

固定提交的 process_response 主要按 Server Request ID 查 callback。

因此安全性依赖 request ID 不可被未授权 peer 获取，以及 transport 只接纳可信连接。

## 131. Request ID 不是 Secret

它是 correlation key，不应被当成唯一授权凭证。

真正权限仍应由认证连接和服务端授权策略保证。

## 132. Replay 的隐私边界

重放可能包含 command、cwd、reason、问题文本或权限详情。

只有被允许订阅该 Thread 的 connection 才应收到 replay。

## 133. Secret Answer 的日志边界

即使 request 标记 isSecret，client response 仍会穿过 JSON parser 和业务转换。

日志、tracing 和 analytics 不应输出完整 result。

## 134. Approval Reason 的展示

reason 是解释“为什么需要权限”，不是可信 command summary 的替代。

UI 应同时展示操作对象、影响范围和持续时间。

## 135. 安全按钮文案

推荐区分：

- 仅允许这一次
- 本会话允许类似操作
- 拒绝并继续
- 拒绝并停止本轮

模糊的 Yes/No 无法表达协议决策差异。

## 136. 网络审批展示

至少显示 host、protocol 和可用 port 信息；若 command 为 null，不要渲染空命令框误导用户。

持久 allow/deny 规则应明显区别于单次授权。

## 137. 文件审批展示

显示将修改的路径和变更摘要，并明确 grantRoot 若存在代表更大目录范围。

不要只显示 Agent 提供的 reason。

## 138. 超时属于谁

OutgoingMessageSender 的通用 pending map 没有统一业务 timeout。

某些请求由 client host 自动解决，某些上游或 Core 自己处理超时；不能假设所有审批 N 秒后自动拒绝。

## 139. 无界等待风险

若没有 client、没有 timeout、Turn 也不转换，pending request 可能长期存在。

生产系统应监控 pending age，并提供明确取消或重新连接路径。

## 140. Pending Age 指标

建议记录：

- request 创建时间
- 首次发出时间
- replay 次数
- resolved 时间
- resolution 类型

但不要记录 secret answers 或完整 command output。

## 141. First Responder 指标

多客户端场景可记录哪类 connection 先响应、其他响应是否 unmatched。

这有助于发现重复 UI 和延迟，不必记录具体决策内容。

## 142. 审批状态机的服务端视图

```text
Created
  -> Registered
  -> Sent/Replayed
  -> Responded | ClientError | Cancelled
  -> CallbackDelivered
  -> ResolvedNotificationQueued
  -> CoreOpSubmitted
  -> ItemCompleted
```

并非每条异常路径都经过最后两个节点。

## 143. 客户端视图

```text
Hidden
  -> Pending
  -> AnswerSubmitted
  -> Resolved
  -> Hidden
```

客户端提交答案后仍应等待 resolved，而不是永远保持 disabled 弹窗。

## 144. 为什么 AnswerSubmitted 是独立状态

网络发送可能尚未完成，或另一客户端已抢先回答。

先禁用重复按钮，再等待 resolved，可避免用户连点产生多份 response。

## 145. Optimistic Close 的取舍

点击后立即关闭弹窗体验更快，但发送失败时难以恢复。

更稳妥的是进入“正在提交”状态，并在 resolved 或明确错误后关闭。

## 146. 多窗口时间线

```text
server -> A,B: request #7
A -> server: accept #7
server removes callback #7
B -> server: decline #7  (unmatched, too late)
server -> A,B: resolved #7
server -> A,B: item/completed
```

最终业务只采用 A 的第一份答案。

## 147. 新客户端重连时间线

```text
request #9 pending on A
B resumes thread
server sends resume snapshot
server replays request #9 to B
B answers #9
server sends resolved #9 to A,B
```

重放没有改变 request identity。

## 148. Turn 转换取消时间线

```text
request #12 pending
turn interrupted
server removes #12 with reason=turnTransition
waiter exits without injecting stale decision
server emits resolved #12
turn completes interrupted
```

这条路径不是“用户拒绝了审批”。

## 149. 常见错误一：用 ItemCompleted 关闭 Request

一个 Item 可能存在多个 request，或 request 可能在 Item 完成前被取消。

审批 UI 应按 requestId 监听 resolved。

## 150. 常见错误二：用 requestId 找 Item

requestId 是 RPC key，不保证等于 itemId。

渲染 Item 状态必须使用 params.itemId。

## 151. 常见错误三：第二个客户端覆盖第一答案

客户端不能假设“最后回答生效”。

服务端 callback entry 被第一份 response 取走，后续响应不会覆盖。

## 152. 常见错误四：Decline 与 Cancel 合并

两者都表示不执行当前操作，但 Cancel 还中断 Turn。

合并会让用户无法表达“拒绝但请继续尝试”。

## 153. 常见错误五：权限响应直接信任

客户端返回的 granted profile 必须被服务端限制在 requested profile 内。

只做 schema validation 不足以保证权限安全。

## 154. 常见错误六：收到 TurnCompleted 仍保留弹窗

Turn transition 会让旧 request 失效。

即使漏掉 resolved，客户端也应在终态事件后清理属于该 Turn 的 pending UI，并记录诊断。

## 155. 常见错误七：重连时生成新本地 Request ID

Replay 使用原 request ID。

客户端应 upsert 同一个 pending request，而不是显示两张重复审批卡。

## 156. 常见错误八：记录 Secret 答案

调试日志中打印整个 JSON-RPC response 很方便，却可能泄露 isSecret 内容。

日志只记录 request ID、method、状态和安全的长度信息。

## 157. 测试：Pending 顺序

固定提交测试确认 `pending_requests_for_thread` 按 request ID 返回多个 Thread requests。

这为 deterministic replay 提供证据。

## 158. 测试：批量取消

测试创建 dynamic tool 和 user input requests，再 cancel_requests_for_thread。

两个 waiter 都收到相同 error，pending 列表最终为空。

## 159. 测试：Client Error 转发

测试确认 notify_client_error 会把 JSON-RPC error 交给对应 waiter。

底层不会把 error response 当成 unmatched notification 丢弃。

## 160. 测试：Turn Transition Reason

server_request_error 测试验证 data.reason=turnTransition 能被识别，其他 reason 不会误判。

结构化取消原因是业务分支的直接测试合同。

## 161. 测试：Active Flags

thread_status 测试覆盖 permission/user input guard 对 WaitingOnApproval、WaitingOnUserInput 的影响。

应特别验证两个 counter 并存和逐个释放。

## 162. 推荐的客户端 Reducer Key

```text
pendingRequests[threadId][requestId]
items[threadId][turnId][itemId]
```

审批卡和业务 Item 分开存储，再通过 params.itemId 建关联。

## 163. Pending Request 数据模型

至少保存：

- requestId
- method
- params
- local submit state
- firstSeenAt
- replaySeenCount

secret method 的 params 应避免持久化。

## 164. onServerRequest

```text
if requestId exists:
  verify method/thread consistency
  increment replay count
else:
  insert pending request
```

同 ID 却 method 不同应视为严重协议异常。

## 165. onResolved

```text
find by threadId + requestId
mark resolved
close interaction UI
remove after animation or immediately
```

找不到时可记录低噪声诊断，因为它可能来自重连边界。

## 166. onTurnTerminal

清理所有 params.turnId 匹配的 pending UI，但不要向 server 伪造 response。

Server 端 lifecycle cancellation 才负责唤醒真实 callback。

## 167. 审批 UI 审查清单

1. 是否显示具体操作和影响范围？
2. 是否区分一次、Turn、Session、持久规则？
3. 是否只展示 availableDecisions？
4. 是否保护 secret answers？
5. 是否处理 replay 和 resolved？
6. 是否防止重复提交？
7. 是否在 Turn 终结后清理？

## 168. 服务端审查清单

1. callback 是否在发送前登记？
2. 发送失败是否移除 entry？
3. 是否绑定正确 threadId？
4. response 是否 fail closed？
5. permission 是否取 requested 交集？
6. turn transition 是否取消全部旧请求？
7. resolved 是否与 Thread 事件排序？
8. waiter 和 guard 是否总能释放？

## 169. 安全审查清单

1. 谁能连接 App-server？
2. 谁能订阅指定 Thread？
3. 谁能回答敏感 Server Request？
4. request ID 是否可能泄露给未授权 peer？
5. response payload 是否进入日志？
6. Session grant 是否被清楚展示和限制？
7. 未请求权限能否被 response 扩大？

## 170. 观测性审查清单

1. pending request 数量和最大 age
2. send/replay/resolved 计数
3. unmatched response 比率
4. callback RecvError 数量
5. turnTransition cancellation 数量
6. malformed response 数量
7. permission intersection 削减数量

## 171. 阅读源码的推荐顺序

1. protocol/common.rs：Server Request method DSL
2. v2/item.rs：command/file/user-input payload
3. v2/permissions.rs：permission response 与 scope
4. outgoing_message.rs：callback map、首答胜出、replay、cancel
5. bespoke_event_handling.rs：事件翻译与 response fallback
6. thread_state.rs/thread_lifecycle.rs：resolved 排序
7. thread_status.rs：等待 flags 和 RAII guard
8. server_request_error.rs：turnTransition reason

## 172. 本章核心结论

审批系统的正确性来自五个边界：

1. Item identity 与 RPC request identity 分离。
2. Pending callback 在发送前登记，并由第一份 response 原子取走。
3. `serverRequest/resolved` 负责让所有客户端关闭同一交互。
4. Turn transition 会取消旧请求，防止迟到答案污染新状态。
5. 安全响应 fail closed，权限 grant 不能超过原始 request。

## 173. 理解检查

请尝试回答：

1. 为什么 itemId 和 requestId 不能合并？
2. 多客户端怎样实现首答胜出？
3. 为什么还需要 serverRequest/resolved？
4. Decline 和 Cancel 有什么业务差别？
5. turnTransition 为什么不能被当成普通 client error？
6. 权限响应为什么必须和 requested permissions 求交集？
7. replay 为什么必须保留原 request ID？

## 174. 动手练习一：多客户端模拟器

实现一个 callback map，把同一 request 发给 A、B 两个模拟客户端。

让 A/B 以随机延迟回答，证明只有第一份 response 能完成 oneshot，第二份成为 unmatched。

## 175. 动手练习二：客户端 Pending Reducer

实现：

- onServerRequest
- onAnswerSubmitted
- onServerRequestResolved
- onTurnCompleted

加入 replay、重复 response 和 resolved-before-local-submit-complete 测试。

## 176. 动手练习三：权限求交集

构造 requested profile 只允许读取目录 A，client response 却允许写 A 和读取 B。

实现交集并证明最终 grant 既没有写 A，也没有读 B。

## 177. 动手练习四：异常响应矩阵

为 command、file、user input、permission、MCP elicitation 分别输入：

- valid response
- malformed JSON shape
- client JSON-RPC error
- turnTransition error
- oneshot RecvError

记录每种 handler 最终向 Core 提交什么。

## 178. 源码检查点

- `codex-rs/app-server-protocol/src/protocol/common.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/item.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/permissions.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/mcp.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/notification.rs`
- `codex-rs/app-server/src/outgoing_message.rs`
- `codex-rs/app-server/src/bespoke_event_handling.rs`
- `codex-rs/app-server/src/dynamic_tools.rs`
- `codex-rs/app-server/src/thread_state.rs`
- `codex-rs/app-server/src/request_processors/thread_lifecycle.rs`
- `codex-rs/app-server/src/thread_status.rs`
- `codex-rs/app-server/src/server_request_error.rs`
- `codex-rs/app-server/src/message_processor.rs`

## 179. 本章词汇表

| 术语 | 字面含义 | 本章中的具体意思 |
|---|---|---|
| Approval | 审批 | 用户决定是否允许命令、文件修改或权限请求 |
| Server Request | 服务端请求 | App-server 主动发给 client、必须响应的 JSON-RPC request |
| Callback | 回调 | 本章中主要指等待 client response 的 oneshot sender |
| Pending request | 待处理请求 | 已登记但尚无最终 response/error/cancel 的 Server Request |
| Correlation ID | 对位ID | 把一次 request 和对应 response 关联起来的 requestId |
| Business identity | 业务身份 | threadId/turnId/itemId 表示的领域对象身份 |
| approvalId | 审批ID | 一个 command Item 内特定 Core approval callback 的可选 ID |
| First responder wins | 首答胜出 | 多客户端中第一份 response 原子取走共享 callback |
| Replay | 重放 | 把仍 pending 的原 Server Request 发送给新 subscriber |
| Resolved | 已解决 | request 已回答或清理，客户端应关闭交互 UI |
| oneshot | 单次通道 | 只交付一次 callback 结果的异步 channel |
| RAII guard | 资源生命周期守卫 | 创建时增加等待计数、drop时减少的对象 |
| Active flag | 活跃标记 | WaitingOnApproval或WaitingOnUserInput等Thread状态原因 |
| Accept | 接受 | 只批准当前操作 |
| AcceptForSession | 本会话接受 | 当前批准并允许匹配后续请求使用session cache |
| Decline | 拒绝 | 不执行当前操作，但Turn可继续 |
| Cancel | 取消 | 拒绝当前操作并中断Turn |
| Policy amendment | 策略修订 | 对未来匹配命令或网络目标生效的规则变化 |
| request_user_input | 请求用户输入 | Agent发起结构化问题并等待答案的工具流程 |
| isBlocking | 是否阻塞 | 该用户输入请求是否阻止当前工作继续 |
| isSecret | 是否秘密 | 答案需要遮罩且不得进入普通日志的提示 |
| Elicitation | 引导取值 | MCP server向用户索取表单内容或URL交互 |
| Permission profile | 权限配置 | 请求或授予的网络/文件系统能力集合 |
| Grant scope | 授权作用域 | 权限只持续当前Turn还是整个Session |
| Intersection | 交集 | 只保留requested与granted双方都有的权限 |
| Least privilege | 最小权限 | 只授予完成操作所需的最少能力 |
| Fail closed | 封闭失败 | 异常时拒绝或授予空权限，而不是默认放行 |
| turnTransition | Turn转换 | 新Turn、完成或中断使旧pending request失效的结构化原因 |
| Unmatched response | 无匹配响应 | callback已被取走/取消或ID错误的迟到response |
| Confused deputy | 糊涂代理 | 低权限方借高权限服务执行本无权执行的操作 |
| Pending age | 等待时长 | Server Request 从创建到resolved的时间 |
