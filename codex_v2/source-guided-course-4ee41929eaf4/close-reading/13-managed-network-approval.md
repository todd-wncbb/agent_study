# Managed network approval：网络请求怎样被识别、暂停、批准与记住

> 源码基线：`4ee41929eaf4`  
> 本篇重点：managed proxy、baseline policy、execution attribution、host/protocol/port key、inline approval、once/session/persistent decisions、Immediate/Deferred 生命周期、并发去重与 fail-closed。  
> 建议先读：[Sandbox approval lifecycle](12-sandbox-approval-lifecycle.md)和[Unified exec](06-unified-exec.md)。

## 1. 先说人话：不是先看见 `curl`，再猜它访问哪里

初学者可能把网络审批想成：

```text
模型写出 curl https://example.com
→ Codex 从命令字符串提取 example.com
→ 先弹审批
→ 批准后启动 curl
```

固定源码中的 managed network 主路径更接近：

```text
为这次命令准备带身份的代理
→ 启动受 Sandbox 约束的命令
→ 程序真的向代理请求 example.com:443
→ 代理解析真实协议、host、port、method
→ 基线策略允许？直接放行
→ 基线策略硬拒绝？直接阻止
→ 只是 allowlist 未命中？暂停请求并进入审批
```

好处是 Python、Node、Git、Cargo 等任何走代理的程序都能统一治理；重定向、子进程和运行时计算出的 URL 也在真实出口处检查。

## 2. 网络控制也有两条不同的线

本章要区分：

1. **Baseline network policy**：allowlist、denylist、私网和 HTTP method 规则怎么判；
2. **Dynamic approval flow**：仅当某类缺口允许覆盖时，怎样临时放行。

可以类比公司访客制度：黑名单不能由前台临时放行；未预约者可以询问接待人；已预约者直接通过；接待人同意某楼层，也不等于交出整栋楼钥匙。

## 3. 官方公开边界

[OpenAI 官方 Agent internet access 文档](https://learn.chatgpt.com/docs/cloud/internet-access)说明：网络访问应按环境配置，尽量只允许必要域名和 HTTP 方法；开放互联网会增加 prompt injection、代码或密钥外泄、恶意依赖和许可证内容等风险。

官方云环境与本篇固定源码中的本地/远端 managed proxy 不是完全相同的产品表面。本篇借官方文档确定安全原则，内部类型、事件和顺序仍以提交 `4ee41929eaf4` 为准。

## 4. 贯穿案例：构建脚本访问三个目标

命令：

```bash
python build.py
```

脚本运行时访问：

```text
https://registry.example.com:443
https://new-api.example.net:443
http://127.0.0.1:8080
```

假设 `registry.example.com` 已在 allowlist，私网地址默认禁止，审批策略为 `on-request`：

1. registry：基线命中，直接通过；
2. new-api：只是 allowlist miss，可以暂停并询问；
3. 127.0.0.1：私网保护命中，通常硬拒绝。

同一进程内的三个请求，可以得到三个不同结果。

## 5. 全景调用链

```text
Session 启动 managed NetworkProxy
  ├─ baseline config + managed constraints
  ├─ exec policy network rules
  ├─ NetworkPolicyDecider
  └─ BlockedRequestObserver

ToolOrchestrator::run_attempt
  → begin_network_approval
  → NetworkProxy::for_execution
  → 注册 ActiveNetworkApprovalCall
  → 启动 shell / unified exec

目标程序发起网络请求
  → HTTP / HTTPS CONNECT / SOCKS5 proxy
  → evaluate_host_policy
  → baseline host_blocked
      ├─ Allowed：放行
      ├─ NotAllowed：交给 decider
      └─ 其他拒绝：直接拒绝
  → NetworkApprovalService::handle_inline_policy_request
  → Hook / Guardian / User
  → AllowOnce / AllowForSession / Deny

命令结束
  → finish immediate 或 deferred approval
  → 将拒绝变成工具错误
  → 清理 registration
```

## 6. `NetworkProxySpec` 合并哪些来源

Session 启动代理前形成的 `NetworkProxySpec` 包含：

- 用户或项目 proxy config；
- 管理员 requirements / constraints；
- 当前 permission profile；
- exec policy 中保存的 network rules。

`with_exec_policy_network_rules` 把持久化 allow/deny rules 投影进代理配置，所以保存规则后，未来请求可以在基线层直接决定。

## 7. Managed constraints 不是普通默认值

管理员可以规定代理必须开启、allowlist 只能在许可范围内扩展、denylist 必须保留、不得绑定非 loopback 地址等。

`validate_policy_against_constraints` 会阻止配置越界。动态审批不能靠写一条用户规则绕过管理员硬限制。

## 8. `hard_deny_allowlist_misses`

当 requirements 指定 `managed_allowed_domains_only`，allowlist miss 是硬拒绝。

`NetworkProxySpec::start_proxy` 只有在：

```text
enable_network_approval_flow == true
并且 hard_deny_allowlist_misses == false
```

时才安装动态 decider。管理员由此可以表达“名单外目标不允许询问”。

## 9. 为什么代理需要两个 callback

```text
NetworkPolicyDecider
BlockedRequestObserver
```

- Decider：基线出现可审查的 `NotAllowed` 时决定是否临时 Allow；
- Observer：已经形成阻止结果时记录审计，并把拒绝关联回运行中的工具调用。

一个参与决策，一个观察结果；observer 不能改变 policy。

## 10. 支持哪些协议

固定源码的 `NetworkProtocol` 有：

```text
Http
HttpsConnect
Socks5Tcp
Socks5Udp
```

HTTPS 常通过 `CONNECT host:443` 建立隧道，因此代理内部名是 `HttpsConnect`，审批协议中简化为 `Https`。

## 11. `NetworkPolicyRequest` 里有什么

```rust
protocol
host
port
environment_id
client_addr
method
command
exec_policy_hint
execution_id
```

最重要的是：真实目标 host、端口、协议、可见的 HTTP method，以及环境和执行归属。它们来自代理实际观察，不是模型自述。

## 12. Host 为什么先规范化

`normalize_host` 处理空白、大小写、域名末尾的点、`host:port`、带括号 IPv6 和 scope id。

因此 `EXAMPLE.COM` 与 `example.com.` 可以形成稳定匹配形式。规范化不是放宽规则，而是防止书写差异绕过缓存和 policy。

## 13. Domain pattern 的语义

```text
example.com      → 只匹配精确 host
*.example.com    → 匹配子域，不匹配 apex
**.example.com   → 匹配 apex 和所有子域
```

allowlist 可以显式使用全局 `*`；denylist 拒绝全局 wildcard，以保持拒绝规则可审查。

## 14. Deny 优先于 Allow

若同一 pattern 同时出现 allow 与 deny，effective permission 逻辑让 deny 获胜。

```text
allow **.openai.com
deny  api.openai.com
```

`api.openai.com` 仍拒绝。动态 approval 也不能把显式 deny 简单覆盖掉。

## 15. `host_blocked` 检查什么

`NetworkProxyState::host_blocked` 综合：

1. host 规范化；
2. denylist；
3. allowlist；
4. loopback、本地或非公网 IP；
5. DNS 解析后的地址性质；
6. local binding 配置。

返回 `Allowed` 或 `Blocked(reason)`。

## 16. 为什么域名允许还不一定够

域名可能解析到私网 IP。若只检查字符串，攻击者可借 DNS 访问内部服务，形成 SSRF。

源码还检查 loopback、private、link-local、multicast、unspecified、CGNAT 和测试网段等。网络策略保护的是实际连接目标，不只是 URL 文本。

## 17. 哪一种拒绝可以进入动态审批

`evaluate_host_policy` 的关键分叉：

```text
Allowed                         → Allow
Blocked(NotAllowed) + decider   → 询问 decider
Blocked(NotAllowed) 无 decider  → Deny
Blocked(其他 reason)            → Deny
```

只有普通 allowlist miss，也就是 `NotAllowed`，会交给 decider。

## 18. 常见拒绝原因

```text
denied
not_allowed
not_allowed_local
method_not_allowed
proxy_disabled
```

`not_allowed` 是常规动态审批入口。显式 deny、私网保护、method 限制和代理关闭属于更强边界。

## 19. HTTP method 是独立限制

`NetworkMode::Limited` 只允许：

```text
GET
HEAD
OPTIONS
```

POST、PUT、PATCH、DELETE 等被阻止。允许域名不等于允许向它上传数据。

## 20. HTTPS Limited 为什么更复杂

普通 CONNECT 是加密隧道，代理若不做 MITM 就看不到内部 GET 还是 POST。

固定源码的配置注释因此规定：Limited 下 HTTPS CONNECT 需启用 MITM，让代理能检查内部 method。看不见的属性，不能假装已经按它实施策略。

## 21. 审批发生在命令运行期间

命令通常已经启动。网络调用线程在代理内等待：

```text
pending.wait_for_decision().await
```

批准后当前网络请求继续；拒绝后代理返回拒绝并可能取消对应 execution。这是 inline approval，不是纯命令启动前检查。

## 22. 为什么要注册“这次命令”

Session 必须知道一个网络请求属于哪个 tool call。否则并发时批准 A 可能放行 B，或拒绝 A 却取消 B。

execution attribution 正是连接“proxy request”和“tool execution”的桥。

## 23. `NetworkApprovalSpec`

tool runtime 提供：

```text
network
mode
trigger
command
environment_id
permission_profile
```

`trigger` 保存 call id、tool name、argv、cwd、Sandbox permissions、additional permissions、justification 和 tty。Guardian 因此能看到触发联网的原命令。

## 24. `begin_network_approval`

只有 tool 提供 spec、spec 有 proxy、当前 Turn managed network active，才创建 active approval。

它生成：

```text
registration_id
attribution_token
cancellation_token
```

再调用 `network.for_execution(...)` 并注册 `ActiveNetworkApprovalCall`。

## 25. 三种 ID 不要混淆

| ID | 用途 |
|---|---|
| tool `call_id` | 模型工具调用和 UI item 身份 |
| `registration_id` | NetworkApprovalService 中一次 execution 的身份 |
| `attribution_token` | 可信 proxy bridge 映射 execution 的秘密 token |

模型可见 call id 不应直接充当底层可信归属凭据。

## 26. Execution-scoped proxy

`NetworkProxy::for_execution` 注册 token → environment/execution 映射并返回带 scope 的 proxy clone。scope Drop 时注销 token。

程序通过可信 bridge 连接后，proxy state 得到 `execution_id`，再附到 `NetworkPolicyRequest`。

## 27. `ActiveNetworkApprovalCall`

它保存 registration、Turn、trigger、command、environment、permission profile 和 cancellation token，形成：

```text
网络请求 → execution → tool command → Turn
```

命令结束必须移除它，避免未来请求归给已结束的 execution。

## 28. 请求归属优先级

`resolve_request_attribution` 大致按：

1. 有 execution id：精确查 active call，并核对 environment；
2. 只有 environment：保留环境，唯一 active call 匹配时附 owner；
3. 两者都没有且只有一个 active call：回退到它；
4. 多个 active calls 且无法区分：Ambiguous，拒绝。

## 29. 为什么 Ambiguous 必须拒绝

随便挑一个 owner 会让 Guardian 看到错误命令，让批准绑定错 execution，甚至取消错误进程。

并发时“不知道是谁”是必须 fail closed 的安全条件。

## 30. 没 active call 时为何有时还能审批

若 Turn 仍活跃且可由 primary environment 唯一确定环境，raw proxy request 可以合成：

```text
command = ["network-access", "http://host:port"]
```

供用户审批。但它不会伪造不存在的具体 tool trigger。

## 31. `HostApprovalKey`

Session cache key 是：

```text
environment_id
lowercase host
protocol
port
```

批准 `local/http/example.com/80` 不会自动批准 remote 环境、81 端口或 HTTPS 443。

## 32. Port 为什么必须进入 key

同一 host 的 443、数据库端口和调试端口可能是完全不同的服务。只按域名缓存会把窄批准扩大到整台机器。

## 33. Session 有两套 host cache

```text
session_approved_hosts
session_denied_hosts
```

查询先看 denied，再看 approved。写 allow 时移除对应 deny；写 deny 时移除对应 allow，冲突时拒绝优先。

## 34. `PendingHostApprovalKey` 更窄

等待中的审批还包含：

```text
HostApprovalKey
turn_id
execution_id
```

只有同 Turn、同 execution、同 target 的并发请求共享 pending decision。不同端口、环境、execution 或 Turn 不去重。

## 35. 同一 execution 为什么去重

网页或包管理器可能并发建立多条同 host 连接。第一个请求成为 owner，其他 waiter 等待同一个 `PendingHostApproval`，避免每条连接都弹框。

## 36. 为什么移除 pending 后才唤醒

顺序是：确认 map 仍是同一代对象 → remove → set decision → notify。

若先唤醒再删，新请求可能错误挂到已完成的旧 approval。`Arc::ptr_eq` 还防止旧 owner 误删后来替换的新 generation。

## 37. Owner Drop 默认 Deny

审批 future 可能被取消、panic、interrupt 或提前返回。未 completed 的 owner Drop 会发布 Deny、唤醒 waiter，并在需要时取消 execution。

这既避免永远挂起，也避免取消被解释成允许。

## 38. 哪些环境允许动态网络审批

固定源码只允许 `PermissionProfile::Managed` 进入该 flow。若环境没有可实施窄授权的 managed enforcement，不能凭 UI approval 假装边界存在。

## 39. `Never` 下的行为

`AskForApproval::Never` 禁止网络 approval flow：

```text
Never + allowlist miss → Deny
```

它不是自动批准，也不是让程序绕开代理直连。

## 40. 审批顺序仍是 Hook 优先

`handle_inline_policy_request` 先运行 permission request hooks：

- Hook Allow：等价当前请求 Approved；
- Hook Deny：记录拒绝并返回；
- 无决定：Guardian 或用户。

有 owner 时 Hook 收到原 command；否则收到合成的 `network-access target`。

## 41. Guardian 看见的结构化 action

`GuardianApprovalRequest::NetworkAccess` 包含 id、turn id、target、host、protocol、port 和可选 trigger。trigger 若存在，还包含原 tool call 和命令上下文。

并发测试专门验证：两个同时联网的命令必须把各自精确 trigger 送给 Guardian，不能互换。

## 42. Guardian 取消和超时都 fail closed

Guardian review 有独立 cancellation token：

- Turn interrupt：review 取消，网络请求不继续；
- Guardian 超时：使用 timeout outcome，不偷偷回退到用户审批；
- review task 失败：形成拒绝。

自动审查没有结果，不等于默认安全。

## 43. 用户看到的 approval event

网络请求复用 `ExecApprovalRequestEvent`，但会填入：

```text
command = ["network-access", target]
reason = "host is not in the allowed_domains"
network_approval_context = { host, protocol }
proposed_network_policy_amendments = [allow host, deny host]
```

它仍带 cwd、Turn id 和 environment id，便于客户端展示来源。

## 44. 默认可选决定

存在 network context 时，默认 decisions 是：

```text
Approved
ApprovedForSession
NetworkPolicyAmendment(Allow)
Abort
```

Event 的 proposed amendments 可同时携带 allow 与 deny 候选，但 legacy/default UI 只自动加入持久 allow；其他客户端或 reviewer 仍可能返回 deny amendment。

## 45. `Approved`：只允许当前请求

它解析为 `PendingApprovalDecision::AllowOnce`。当前 pending 请求及同 execution waiter 通过，但 Session host cache 不写入，下一次仍询问。

## 46. `ApprovedForSession`：记住精确 key

它把 `HostApprovalKey` 写入 `session_approved_hosts`。后续同 Session、同环境、同协议、同 host、同 port 直接 Allow。

它不写 `rules/default.rules`，新 Session 不应依赖这份内存缓存。

## 47. 持久化 Allow

`NetworkPolicyAmendment { action: Allow }` 进入 `Session::persist_network_policy_amendment`：

1. 锁住 managed proxy refresh；
2. 校验 amendment host 与审批 host 规范化后完全相同；
3. 生成 exec policy network rule；
4. proxy 已运行时先更新 runtime allowlist；
5. 追加默认 rules 文件并更新内存 exec policy；
6. 注入“规则已保存”的 developer context fragment。

## 48. Host 校验防止什么

用户看到“允许 `new-api.example.net`？”时，客户端不能回传 `host="*"` 或另一个域名来扩大授权。

`validated_network_policy_amendment_host` 比较审批 context 与 amendment 的 normalized host。不匹配就失败，也不会把目标放入 Session allow cache。

## 49. 持久化 Deny

`NetworkPolicyAmendment { action: Deny }` 会：

- 更新 runtime denylist；
- 写 exec policy forbidden network rule；
- 从 session approved cache 移除 key；
- 放入 session denied cache；
- 拒绝当前请求；
- 注入“Denied network rule saved...”上下文。

它表示“现在拒绝，而且以后也拒绝”，不是仅取消本次。

## 50. 为什么需要 `session_policy_commit_lock`

Runtime proxy、磁盘规则和 Session cache 的更新包含多个 await。若两个任务同时 allow/deny，同一 host 可能出现顺序不一致。

Commit lock 让一次 policy commit 与对应 cache 投影按同一顺序完成。

## 51. 这不是完整磁盘事务

Runtime allowlist 更新成功后，追加规则文件仍可能失败。源码用 callback 在 active enforcement 已改变后设置安全的 owner drop decision，避免取消路径把已经生效的 runtime allow 反说成 deny。

它保证并发顺序与 fail-safe handoff，不是数据库式 rollback。最明确的前置失败是 host 不匹配：任何 runtime 修改前就拒绝。

## 52. `NetworkRuleSaved` 为什么进模型上下文

持久规则是后续决策所需状态。只写磁盘而不告诉模型，模型可能重复请求或误判仍未授权。

注入内容类似：

```text
Allowed network rule saved in execpolicy (allowlist): host
Denied network rule saved in execpolicy (denylist): host
```

它使用 developer role，不伪装成用户原话。

## 53. `PendingApprovalDecision` 与 proxy decision

内部结果：

```text
AllowOnce
AllowForSession
Deny
```

前两个交给 proxy 时都映射为 `NetworkDecision::Allow`。Proxy 只需知道当前请求能否继续；是否记住已经由 Service cache 处理。

## 54. 拒绝为什么记录到 active call

Proxy 返回 403 或断连时，目标程序可能只产生模糊错误。Codex 需要把准确原因回填给模型：用户拒绝、policy block、Guardian timeout 或 approval abandoned。

`call_outcomes[registration_id]` 保存结构化结果，命令结束时由 finish 阶段取出。

## 55. `DeniedByApproval` 与 `DeniedByPolicy`

用户明确拒绝应保留“rejected by user”语义；denylist、私网和 method rule 应显示“blocked by policy”。

测试验证：用户拒绝不能被后来观察到的普通 policy block 覆盖成另一类原因；更具体的 approval outcome 也能替换较早的笼统 block。

## 56. Cancellation token 的方向

```text
记录网络拒绝 outcome
→ cancellation_token.cancel()
→ process manager 发现取消
→ 终止或标记 execution 失败
```

长时间运行的进程不会在网络已经被拒绝后继续假装成功等待。

## 57. `Immediate` 模式

Shell runtime 使用 `NetworkApprovalMode::Immediate`。`tool.run` 返回后，Orchestrator 立即 `finish_immediate_network_approval`：移除 active call、读取 outcome；若网络被拒绝，就把工具结果改成 `ToolError::Rejected`。

## 58. `Deferred` 模式

Unified exec 的 `tool.run` 可能只返回仍运行的 process handle 和 `session_id`。此时注销 attribution，会让后台请求失去 owner。

因此它使用 `Deferred`，把 `DeferredNetworkApproval` 与 process entry 一起保存到退出、失败、终止或清理。

## 59. Deferred 为什么保存 execution proxy

`_execution_proxy` 看似未读取，却维持 execution scope 生命周期。只要 clone 存活，token → execution mapping 就不会因 Drop 提前注销。

这是 RAII：字段的作用是持有资源到正确时刻。

## 60. Late network denial grace period

进程退出与 observer 记录拒绝可能竞态：exit 已观察到，但 blocked callback 还在队列里。

Unified exec 最终 finish 前短暂等待 cancellation token，让迟到 denial 有机会落账，避免先报告成功、随后才发现最后一次请求被拒绝。

## 61. Deferred finish 为什么用 `OnceCell`

Exit watcher、`write_stdin` poll、terminate 和 cleanup 都可能 finish 同一 approval。

`OnceCell` 确保第一次消费者从 service 取走 outcome，后续消费者复用同一结果；不会第一次看到拒绝、第二次因 map 已清空而错误看到成功。

## 62. Abandoned approval

Registration 没有正常 outcome，但 cancellation token 已取消时，结果是：

```text
network approval was cancelled before a decision was returned
```

仍按拒绝处理。取消、清理或断线不能默认 Allow。

## 63. `BlockedRequestObserver`

`BlockedRequest` 可包含 host、reason、method、protocol、port、decision、source、execution id 和 timestamp。

Observer 记录 violation，尽可能关联 owner call，保存 policy denial outcome，并取消对应 execution。

## 64. 无 attribution 的 block 不能乱归属

有 execution id 时精确找 owner；没有时只有恰好一个 active call 才回退；多个 active calls 时不归属、不随便取消。

宁可少一条精细错误，也不能终止错误进程。

## 65. 完整案例一：基线 allowlist 命中

```text
curl https://registry.example.com
→ begin 注册 execution
→ proxy 观察真实 target
→ host_blocked = Allowed
→ 不调用 decider
→ 请求通过
→ finish 无拒绝 outcome
→ 工具成功
```

## 66. 完整案例二：Allow once

```text
allowlist miss = NotAllowed
→ decider 找到 exact execution
→ Session cache 未命中
→ 创建 pending owner
→ Hook 无决定
→ 用户 Approved
→ AllowOnce
→ 当前请求继续
→ 不写 Session cache
→ 下一次仍询问
```

## 67. 完整案例三：Approve for session

第一次选择 `ApprovedForSession`，精确 key 写入 approved hosts。相同 Session/环境/协议/host/port 再访问时直接 Allow；换端口、协议或环境仍询问。

## 68. 完整案例四：保存 allow rule

```text
校验 host
→ runtime proxy add_allowed_domain
→ rules/default.rules 追加 network_rule(... decision="allow")
→ Session cache 标记 allow
→ 模型上下文收到 NetworkRuleSaved
```

未来 Session 加载 exec policy 后，基线直接允许。

## 69. 完整案例五：显式 denylist

```text
host_blocked = Blocked(Denied)
→ evaluate_host_policy 不调用 decider
→ observer 记录 blocked by policy
→ execution cancellation
→ finish 返回 ToolError::Rejected
```

即使 approval policy 是 OnRequest，也不弹临时允许。

## 70. 完整案例六：Limited 下 POST

允许 host 后，POST 仍可能被 method policy 拒绝：

```text
host allowed ≠ method allowed
```

结果是 `method_not_allowed`，不是 allowlist miss，不进入 host approval。

## 71. 完整案例七：两个并发命令

```text
command A → a.example → execution A
command B → b.example → execution B
```

Guardian 收到两个各自带正确 trigger 的 action。批准 A 不放行 B。

第三个 raw request 没 attribution 且两者均 active 时，系统判 Ambiguous 并返回 403，不猜 owner。

## 72. 完整案例八：长时间 unified exec

```text
exec_command 启动 dev server
→ 很快返回 session_id
→ DeferredNetworkApproval 留在 ProcessEntry
→ 20 秒后 server 首次请求外部 API
→ 仍可精确触发审批
→ exit watcher / write_stdin 取得拒绝结果
→ OnceCell 保证各路径一致
```

## 73. 用户拒绝与 Abort

`Denied { rejection }` 拒绝网络请求并保留用户理由，Turn 通常仍可继续。`Abort` 则拒绝并中断当前 Turn，等待下一条用户命令。

集成测试验证 Abort 保持用户中断语义，不改写成一般 policy denial。

## 74. 常见误解纠正

### 误解一：批准一个 curl 就允许整条命令任意联网

错误。批准 key 精确包含 environment、host、protocol、port。

### 误解二：名单外一定硬失败

不一定。Managed profile、允许审批、无 hard managed restriction 时，普通 miss 可以询问。

### 误解三：所有拒绝都能点允许

错误。显式 deny、私网、method 限制和 proxy disabled 通常不进入动态 host approval。

### 误解四：系统靠解析 shell 找域名

错误。主路径由 proxy 在真实请求发生时解析 target。

### 误解五：Session approval 跨远端环境复用

错误。Environment id 是 key 的一部分。

### 误解六：保存 allow 只改当前内存

错误。它更新 runtime proxy，并尝试持久化 exec policy。

### 误解七：返回 session id 后 approval 就结束

错误。Unified exec 的 Deferred approval 跟随后台 process。

### 误解八：取消审批后忽略并继续

错误。Owner Drop、Guardian cancellation 和 abandoned 都 fail closed。

## 75. 关键不变量

1. 基线硬拒绝不交给普通 decider 覆盖；
2. Target 来自 proxy 真实请求；
3. 并发归属不猜，Ambiguous 拒绝；
4. Session cache 不跨环境、协议或端口；
5. 取消与缺失决定默认拒绝；
6. 后台进程保留 attribution 到终止；
7. 持久 amendment 不能更换审批 host；
8. Deny 优先于较弱 Allow。

## 76. 测试证据

### `core/src/tools/network_approval_tests.rs`

验证 pending 去重作用域、Session key 的环境/协议/端口边界、owner Drop、Managed profile 限制、Never、ambiguous attribution、outcome 优先级、错误截断和 deferred 多消费者。

### `core/tests/suite/network_approval.rs`

验证 Guardian action/trigger、取消和超时、allow once/session/different port/protocol/Abort、规则持久化、context 注入、非法 amendment、无 attribution 回退、并发 403，以及本地/远端环境隔离。

### `network-proxy/src/runtime.rs` 测试

验证 deny 优先、allowlist、wildcard、loopback/private IP/IPv6/DNS，以及 managed constraints。

### `network-proxy/src/network_policy.rs` 测试

验证 baseline、decider override 和 audit event 中的 decision/source/reason/execution id。

### Unified exec 测试

验证 Deferred approval 随 process exit、poll、terminate 与 late denial 正确结束。

## 77. 推荐源码阅读顺序

第一遍看 Core 状态机：

1. `core/src/tools/network_approval.rs` 顶部类型；
2. `begin_network_approval`；
3. `handle_inline_policy_request`；
4. immediate/deferred finish。

第二遍看 proxy：

5. `network-proxy/src/network_policy.rs`；
6. `network-proxy/src/runtime.rs` 的 `host_blocked`；
7. `network-proxy/src/policy.rs`；
8. `http_proxy.rs` 与 `socks5.rs`。

第三遍看生命周期：

9. `proxy/execution_scope.rs`；
10. `core/src/tools/orchestrator.rs`；
11. shell/unified exec runtime 的 spec；
12. `unified_exec/process_manager.rs`；
13. `Session::persist_network_policy_amendment`。

## 78. 理解检查

### 问题 1：为什么不只解析 curl 字符串？

<details><summary>参考答案</summary>

任意程序、子进程、重定向和运行时 URL 都会产生请求。Proxy 位于实际出口，target 更权威。

</details>

### 问题 2：为什么 denylist 不进入动态审批？

<details><summary>参考答案</summary>

动态审批只补可覆盖的 allowlist miss。若普通批准能覆盖 deny，持久或管理员拒绝就失去意义。

</details>

### 问题 3：为什么 HTTPS 443 approval 不批准 HTTP 80？

<details><summary>参考答案</summary>

协议和端口属于 key；它们可能对应不同服务和风险。

</details>

### 问题 4：为什么 unified exec 使用 Deferred？

<details><summary>参考答案</summary>

首次调用可在进程仍运行时返回。立即注销会让后台请求失去 attribution。

</details>

### 问题 5：为什么不能给 ambiguous request 随机选 owner？

<details><summary>参考答案</summary>

错误归属会让审批、Guardian trigger 或 cancellation 施加到错误命令。

</details>

### 问题 6：为什么 allow host 后 Limited 仍拒绝 POST？

<details><summary>参考答案</summary>

Host 和 method 是独立维度：前者回答去哪里，后者回答以什么操作传输数据。

</details>

## 79. 动手练习

### 练习一：计算 cache key

判断下面四个 target 是否复用：

```text
local/http/example.com/80
local/http/example.com/81
local/https/example.com/443
remote/http/example.com/80
```

### 练习二：给拒绝分类

把 `not_allowed`、`denied`、`not_allowed_local`、`method_not_allowed`、`proxy_disabled` 分成可审批 miss 和硬拒绝。

### 练习三：画 Deferred 生命周期

标出 registration、session id、后台请求、approval、process exit、late grace、OnceCell finish 和 execution proxy Drop。

### 练习四：固定源码搜索

```bash
git grep -n 'handle_inline_policy_request' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'evaluate_host_policy' 4ee41929eaf4 -- codex-rs/network-proxy/src
git grep -n 'pub async fn host_blocked' 4ee41929eaf4 -- codex-rs/network-proxy/src
git grep -n 'begin_network_approval' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'persist_network_policy_amendment' 4ee41929eaf4 -- codex-rs/core/src
```

## 80. 本篇局部术语表

| 名词 / 代码词 | 中文理解 | 本篇中的具体含义 |
|---|---|---|
| managed network | 受管网络 | 由代理、Sandbox、policy 和审批约束的网络出口 |
| proxy | 代理 | 在真实出口检查 host、port、protocol、method 的中间服务 |
| baseline policy | 基线策略 | 动态审批前已有的 allowlist、denylist、私网与 method 规则 |
| allowlist | 允许列表 | 命中后按基线直接访问的 domain patterns |
| denylist | 拒绝列表 | 明确阻止且优先于 allow 的 patterns |
| allowlist miss | 允许列表未命中 | `NotAllowed`，可能进入动态审批 |
| managed constraints | 管理约束 | 用户配置和 amendment 不能越过的管理员边界 |
| hard deny | 硬拒绝 | 不提供普通动态批准机会的结果 |
| `NetworkProxySpec` | 代理规格 | 合并 config、requirements、permission profile 与 exec policy |
| `NetworkPolicyDecider` | 策略决策器 | allowlist miss 时决定临时 Allow/Deny 的 callback |
| `BlockedRequestObserver` | 阻止观察器 | 记录并把拒绝关联回 execution |
| `NetworkProtocol` | 协议类别 | Http、HttpsConnect、Socks5Tcp、Socks5Udp |
| HTTPS CONNECT | HTTPS 隧道建立 | 要求代理连接 host:port 的方式 |
| MITM | 中间人解密代理 | 在受控信任下检查 HTTPS 内部 method |
| `NetworkPolicyRequest` | 网络策略请求 | Proxy 从真实连接构造的 target 与 attribution 数据 |
| `NetworkDecision` | 网络决定 | 当前 proxy request 的 Allow 或带原因的 Deny |
| `HostBlockDecision` | Host 基线结果 | Allowed 或 Blocked(reason) |
| `NotAllowed` | 名单未命中 | 唯一常规送给动态 decider 的 host miss |
| `not_allowed_local` | 本地地址不允许 | loopback/private/link-local 等保护拒绝 |
| `method_not_allowed` | 方法不允许 | Limited 阻止 POST、PUT 等 |
| domain pattern | 域名模式 | exact、`*.` 子域或 `**.` apex+子域规则 |
| apex domain | 顶点域名 | 如 `example.com`，相对于它的子域 |
| loopback | 回环地址 | localhost、127.0.0.0/8、::1 等 |
| private IP | 私网 IP | 不在公网路由的内部地址 |
| SSRF | 服务端请求伪造 | 借可控 URL 访问本地或内部服务的攻击 |
| execution attribution | 执行归属 | 将 proxy connection 映射回 tool execution |
| `registration_id` | 注册/执行 ID | Service 中一次 active call 的身份 |
| `attribution_token` | 归属令牌 | 可信 bridge 获取 execution-scoped state 的秘密 token |
| execution-scoped proxy | 执行级代理 | 携带 environment/execution attribution 的 proxy clone |
| `ActiveNetworkApprovalCall` | 活动调用 | 保存 execution 与 Turn、command、permission 的关联 |
| `HostApprovalKey` | Host 审批键 | environment、host、protocol、port 组合 |
| pending approval | 等待审批 | Proxy request 暂停等待决定 |
| owner | 审批所有者 | 为 pending key 真正发起 review 的首个请求 |
| waiter | 等待者 | 同 execution 同 target 并发请求，复用 owner 决定 |
| generation | 一代 pending | 防止旧 owner 删除新 pending 的对象身份 |
| ambiguous | 归属不明确 | 多 active calls 下无法确定来源，按拒绝处理 |
| inline approval | 内联审批 | 网络 I/O 在 proxy 中暂停等待决定 |
| `AllowOnce` | 允许一次 | 只释放当前 pending，不写 Session cache |
| `AllowForSession` | 本 Session 允许 | 精确 key 在当前 Session 后续通过 |
| network amendment | 网络规则修订 | 持久化 allow/deny host 的 exec policy rule |
| `NetworkApprovalContext` | 网络审批上下文 | 给 UI/reviewer 的真实 host 与 protocol |
| `NetworkRuleSaved` | 规则已保存片段 | 注入模型上下文的 developer 状态说明 |
| commit lock | 提交锁 | 串行 runtime policy、磁盘规则与 cache 更新 |
| `Immediate` | 立即结束模式 | Shell 返回后马上 finish registration |
| `Deferred` | 延迟结束模式 | Approval 跟随后台进程到退出或清理 |
| `DeferredNetworkApproval` | 延迟句柄 | 持有 registration、cancel、OnceCell 与 proxy |
| late denial | 迟到拒绝 | Process exit 后 observer 稍晚到达的竞态 |
| `OnceCell` | 一次初始化容器 | 多 finish 路径共享最终 outcome |
| cancellation token | 取消令牌 | 拒绝通知 process manager 的信号 |
| abandoned | 被遗弃 | 未正常完成但已取消，按拒绝处理 |
| fail closed | 失败时保持拒绝 | 归属、review 或状态不确定时不放行 |
| audit event | 审计事件 | 记录 decision/source/reason/target/execution |
| RAII | 随对象生命周期管理资源 | Proxy Drop 时注销 attribution mapping |

## 81. 最后压缩成一句话

Managed network approval 的真正主线不是“给 curl 弹一个是否联网的框”，而是：

> Session 把管理员约束、用户配置和持久规则编译成 managed proxy 基线；每次工具执行获得独立 attribution，真实请求到达代理后先经过 host、私网和 method policy，只有允许覆盖的 allowlist miss 才按精确 environment/host/protocol/port 暂停并送往 Hook、Guardian 或用户；once、session 和 persistent 决定分别作用于当前 pending、内存 cache 和 runtime+execpolicy，而 Immediate/Deferred 清理确保短命令与后台进程的拒绝都回到正确工具结果。

下一篇计划精读 apply_patch lifecycle：模型给出 patch 后，系统怎样解析文件变化、判断路径权限、请求审批、执行写入并回填结果。

返回[源码精读系列目录](README.md)或[课程总目录](../README.md)。
