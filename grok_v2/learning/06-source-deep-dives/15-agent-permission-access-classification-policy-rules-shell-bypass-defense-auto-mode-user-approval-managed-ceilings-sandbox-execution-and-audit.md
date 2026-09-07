# 源码精读 15：Agent Permission 如何分类访问、合并规则、抵抗 Shell 绕过、请求批准并由 Sandbox 执行兜底

> 本文精读 grok-build 的本地权限与 Sandbox 主链。核心不是“哪里弹出确认框”，而是回答一个更重要的问题：模型提出一个 Tool Call 后，系统怎样把它逐层收窄为一次可执行、可拒绝、可审计、受 OS 约束的动作。

## 0. 本文定位

前面的源码精读已经说明：

- Tool 怎样注册并进入模型请求；
- Agentic Loop 怎样解析 Tool Call；
- Hook 与 Permission 怎样出现在执行前；
- Subagent 怎样继承 Session 上下文。

本文继续向下钻，专门研究：

1. 模型“看得到工具”为什么不等于“可以执行工具”；
2. `ToolInput` 怎样归一化成 `AccessKind`；
3. 多来源 Permission Rule 怎样合并；
4. 为什么规则顺序不决定安全优先级；
5. Bash 为什么必须按链、包装器、内联脚本和文件操作多次分析；
6. Ask、Auto、Always Approve 到底有什么不同；
7. Auto Mode 为什么既不是静态白名单，也不是无条件授权；
8. 一次批准、会话批准和持久批准怎样保存；
9. 父 Agent、Subagent 与不同工作目录怎样共享权限而不串错路径；
10. 用户关闭、请求方消失、Classifier 超时怎样失败关闭；
11. Permission 与 Sandbox 为什么是两道不同的门；
12. Landlock、Seatbelt、bubblewrap、seccomp 在本项目里各管什么；
13. 审批、执行与审计数据怎样对应起来。

本文记录的源码基线：

```text
ed6d543
```

源码变化后，请优先按本文给出的符号名定位，而不是依赖行号。

---

## 1. 先建立最重要的心智模型

一次 Tool Call 至少经过四类约束：

```text
模型请求里存在 Tool Schema
        │
        ▼
Capability / Registry：这个 Agent 是否拥有该工具
        │
        ▼
Hook / Mode Gate：当前流程是否允许进入该类动作
        │
        ▼
Permission：这一次具体访问是否获准
        │
        ▼
Sandbox / OS：进程在内核层实际上能做什么
        │
        ▼
Tool 实现：参数校验、执行、结果或错误
```

必须把下面四句话分开：

- Tool 可见：模型收到了它的 Schema；
- Tool 可调：Registry 能解析并 Dispatch 它；
- Tool 获批：Permission 对本次 `AccessKind` 返回 `Allow`；
- Tool 能成功：实际操作没有被 Sandbox、OS、远端服务或 Tool 自身拒绝。

因此：

> `Permission Allow` 不是“保证执行成功”，更不是“解除 Sandbox”。

反过来也一样：

> Sandbox 存在，不代表 Permission 可以跳过所有语义判断。

Sandbox 不知道“`git push` 是不是用户刚刚明确要求的”，Permission Classifier 可以判断意图；Permission 不一定能阻止某个进程通过内核系统调用触碰越界路径，Sandbox 可以兜底。

---

## 2. 核心源码地图

### 2.1 Permission Workspace

主要目录：

```text
crates/codegen/xai-grok-workspace/src/permission/
```

关键文件：

| 文件 | 责任 |
| --- | --- |
| `types.rs` | 权限请求、访问种类、决策、规则和遥测的类型 |
| `manager.rs` | 权限 Actor、决策主链、模式、Grant、Prompt 与状态更新 |
| `resolution.rs` | 从 requirements、managed settings、Grok config、Claude settings 合并配置 |
| `rules.rs` | Permission Rule DSL 与 `defaultMode` 解析 |
| `policy.rs` | 预编译规则、路径/命令匹配与规则优先级 |
| `gate_preflight.rs` | 把直接规则、Bash 命令 Gate、Shell 文件 Gate 合成一次预检 |
| `bash_command_splitting.rs` | Shell 语法拆分、命令链识别 |
| `shell_access.rs` | 从 Shell 命令识别读写路径并防止文件规则绕过 |
| `exec_risk.rs` | 执行风险与 Git 等命令的只读/危险分类 |
| `auto_mode.rs` | Auto Fast Path、Classifier 上下文、Prompt 与 Verdict |
| `prompter.rs` | 面向 ACP/Hub 的批准选项与结果映射 |
| `state.rs` | 持久化的命令、域名和 MCP Grant |
| `hub_permission.rs` | 通过服务端/Chat 传输审批请求 |
| `claude_settings.rs` | Claude settings 兼容导入 |

### 2.2 Sandbox

主要目录：

```text
crates/codegen/xai-grok-sandbox/src/
```

关键文件：

| 文件 | 责任 |
| --- | --- |
| `lib.rs` | Sandbox 生命周期、全局状态、应用与安装 |
| `profiles.rs` | 内置和自定义 Profile 的解析 |
| `deny/*` | Deny Path 与 Glob 的内核约束 |
| `hook_write_deny.rs` | 对 Hook、启动脚本等高价值入口施加写保护 |
| `child_net.rs` | Linux 子进程网络过滤 |
| `network_policy.rs` | 网络 Policy 类型 |
| `logging.rs` / `types.rs` | Sandbox 事件与指标 |

启动接线位于：

```text
crates/codegen/xai-grok-shell/src/config/mod.rs
```

命令子进程的网络接线还分布在：

```text
crates/codegen/xai-grok-tools/src/computer/local/terminal.rs
crates/codegen/xai-grok-shell/src/terminal/streaming_local_terminal.rs
```

---

## 3. `AccessKind`：先把不同 Tool 降维成安全语义

### 3.1 为什么不能直接用 Tool 名判断

Permission 不应该为几十个 Tool 各写一套完全独立的逻辑。

例如：

- `read_file` 与目录读取都属于读；
- `search_replace`、`write`、`apply_patch` 都属于编辑；
- `run_terminal_command` 与长任务监控涉及 Bash；
- 每个 MCP Tool 名字不同，但都属于第三方工具调用。

`types.rs` 用 `AccessKind` 把具体工具映射到较少的安全类别：

```rust
pub enum AccessKind {
    Read(Option<String>),
    Grep { path: Option<String>, glob: Option<String> },
    Edit(String),
    Bash(String),
    MCPTool { name: String, input: serde_json::Value },
    WebFetch(String),
    WebSearch(String),
}
```

这个类型是 Tool 层与 Permission 层之间的安全协议。

### 3.2 `From<&ToolInput>` 是安全归一化边界

`ToolInput` 到 `AccessKind` 的映射大致为：

| ToolInput 类别 | AccessKind |
| --- | --- |
| 文件读取、目录列表 | `Read(path)` |
| Grep | `Grep { path, glob }` |
| SearchReplace、ApplyPatch、HashlineEdit、Write | `Edit(path)` |
| Bash、Monitor | `Bash(command)` |
| MCP UseTool | `MCPTool { name, input }` |
| Web Fetch | `WebFetch(url)` |
| Web Search | `WebSearch(query)` |
| Todo、Task Output、Wait、Kill、Skill 等协调类动作 | `Read(None)` |

最后一类容易误解。

`Read(None)` 在这里不一定表示“读取某个文件”，而可能表示：

> 此动作没有可供路径策略匹配的高风险写入或外部副作用，复用最低风险访问类别。

### 3.3 MCP 为什么保留 Input

MCP 访问不是只有名字：

```rust
MCPTool {
    name,
    input,
}
```

同一个 Tool：

- 查询 issue；
- 创建 issue；
- 删除 issue；

风险完全不同。

因此 `manager.rs` 会生成长度受限的 `access_detail`，同时给：

- Auto Classifier；
- Permission Telemetry。

### 3.4 路径上下文不能藏在 Manager 全局里

`RequestPathContext` 同时保存：

- `real_cwd`；
- `display_cwd`。

这是因为父 Agent 和 Subagent 可以共享同一个 `PermissionHandle`，但它们的工作目录可能不同。

如果 Manager 永远用父 Session 的 CWD，那么子 Agent 的：

```text
Read(./src/config.rs)
```

可能错误地匹配父目录下的 `./**` 规则。

所以每次 Request 都携带请求者自己的路径上下文。

这是一个非常典型的安全设计：

> 共享决策状态，不共享隐含路径解释。

---

## 4. `Decision` 不是简单的布尔值

`types.rs` 的决策包括：

| Decision | 含义 |
| --- | --- |
| `Allow` | 本次动作可以继续 |
| `Ask` | 必须进入交互审批 |
| `FollowupMessage(text)` | 用户没有选允许/拒绝，而是发来补充消息 |
| `Reject(reason)` | 普通拒绝 |
| `PolicyDeny(reason)` | Policy 明确拒绝，调用方应把错误反馈给模型 |
| `Cancelled` | 审批或请求生命周期被取消 |

### 4.1 为什么 `PolicyDeny` 与 `Reject` 分开

这两者最终都不执行工具，但对 Agentic Loop 的语义不同。

`PolicyDeny` 表示：

- 动作碰到了确定的组织或配置规则；
- 应让模型看到结构化错误；
- 模型可以换一种合法方案继续。

普通用户拒绝可能意味着：

- 当前意图被撤销；
- 该 Turn 应停止；
- 不应该立即“换个写法绕过”。

所以源码注释明确保留了不同 Variant。

### 4.2 为什么有 `FollowupMessage`

审批 UI 不一定只产生 Yes/No。

用户可能回复：

```text
不要推 main，先创建一个新分支。
```

这不是 Deny Reason，而是新的真人输入。

把它建模成独立 Variant，可以让 Session 将它重新送回 Agent，而不是伪装成 Tool Error。

### 4.3 为什么 `Cancelled` 也必须独立

取消可能来自：

- 用户关闭审批；
- Turn 被 Cancel；
- Requester Future 被 Drop；
- Gateway 断开；
- Session 结束。

取消不是安全策略拒绝，也不是模型犯错。

独立建模避免错误重试和错误归因。

---

## 5. Permission 配置来自哪里

`resolution.rs` 合并多个来源：

1. `requirements.toml`；
2. `managed-settings.json`；
3. Managed `config.toml`；
4. 用户 `~/.grok/config.toml`；
5. 从 Git Root 到当前 CWD 的项目 `.grok/config.toml`；
6. 尚未完成迁移时的 `.claude/settings.json`；
7. Session/CLI 在其他接线点增加的规则。

### 5.1 规则列表与标量 Mode 的合并不同

Rule 是集合：

- 所有来源都可以贡献；
- 最终按 `deny > ask > allow` 求值；
- 来源顺序主要用于 Provenance 展示。

`defaultMode` 是标量：

- Managed Settings 的值拥有更高优先级；
- 一旦 Managed Scope 提供 Mode，用户/项目 Scope 的 Mode 不再覆盖；
- 但其他 Scope 的具体规则仍可合入。

这叫做：

> “规则可累积，Mode 由更高管理域占有。”

### 5.2 为什么项目规则受 Folder Trust 约束

未信任仓库可以自带：

```toml
[permission]
allow = ["Bash(*)"]
```

如果 Session 启动时无条件读取，它就能通过配置让自己获得权限。

所以 `project_trusted` 控制项目级：

- `.grok/config.toml`；
- `.claude/settings.json`。

全局、用户和管理员层仍可加载。

### 5.3 配置只在 Session 开始时解析

`resolution.rs` 明确写道：

> Rules are read when a session starts.

这意味着：

- Session 内的有效 Policy 是稳定 Snapshot；
- 修改文件后通常要新 Session 才生效；
- 运行中的决策不会因配置文件被 Tool 编辑而突然漂移。

### 5.4 解析错误为什么要保留 `SkippedPermission`

安全配置错误不能悄悄吞掉。

解析器会记录：

- 原规则；
- 未加载原因；
- 来源。

这样 `grok inspect` 可以解释：

- 哪条规则生效；
- 哪条规则被跳过；
- 为什么跳过。

### 5.5 未知 `defaultMode` 的 Fail-safe 行为

未知字符串：

- 被视为 `Default`；
- 即回到 Prompt 语义；
- 记录 Warning 与 Skip。

而且该具体 Scope 仍“声明拥有这个值”。

这防止一个拼错的、更具体配置意外落回更宽松的父级配置。

---

## 6. Rule DSL 与 Policy 编译

### 6.1 规则动作

每条 `PermissionRule` 有：

- `RuleAction::Deny`；
- `RuleAction::Ask`；
- `RuleAction::Allow`。

规则还包含：

- `ToolFilter`；
- 可选 Pattern；
- `PatternMode::Glob` 或 `PatternMode::Domain`。

### 6.2 支持的 Rule 形状

典型语法：

```text
Read(./src/**)
Edit(./docs/**)
Bash(cargo test*)
MCPTool(github__create_issue)
WebFetch(domain:docs.rs)
WebSearch
```

裸 Tool 名表示工具级规则。

`Bash(cmd:*)` 是前缀授权习惯写法。

### 6.3 不支持的规则不会被“猜”

例如：

- 未知 Tool 前缀；
- 括号畸形；
- 尚未支持的 `EnterWorktree`；

都会返回 Typed Parse Error，而不是模糊匹配成 `Any`。

安全解析器应尽量避免“我猜你想表达的是”。

### 6.4 `CompiledPolicy` 为什么预编译 Glob

同一个 Session 会处理很多 Tool Call。

`CompiledPolicy` 在初始化时：

- 保存 `PermissionConfig`；
- 把可编译的 Glob 变成 `glob::Pattern`；
- 预计算是否存在文件限制；
- 预计算是否存在 Bash 限制；
- 预计算是否存在 Bash Allow。

这些布尔值让不相关的请求跳过昂贵 Gate。

---

## 7. 最核心的不变量：`deny > ask > allow`

`CompiledPolicy::evaluate_with_cwd` 并不采用“第一条命中”或“最后一条命中”。

它遍历所有匹配规则：

1. 遇到 Deny，立即返回 Reject；
2. 没有 Deny，但至少有 Ask，返回 Ask；
3. 没有 Deny/Ask，但至少有 Allow，返回 Allow；
4. 没命中，返回 None。

因此：

```text
Deny > Ask > Allow > No Match
```

且与配置来源顺序无关。

### 7.1 为什么不能 Last-write-wins

假设管理员写：

```text
Deny Bash(rm -rf *)
```

项目写：

```text
Allow Bash(*)
```

如果后者覆盖前者，项目仓库就能解除管理员限制。

采用安全优先级后，广泛 Allow 永远不能盖过具体 Deny。

### 7.2 Ask 也不能被 Allow 吞掉

Ask 表示：

> 此动作没有被禁止，但真人必须看一眼。

若 Allow 能覆盖 Ask，某个宽泛白名单会意外消除人工确认。

### 7.3 路径先按请求 CWD 解释

`evaluate_with_cwd` 会先把相对路径与 Request CWD 结合，再做词法规范化与 Glob 匹配。

这里强调“词法”：

- 折叠 `.` 与 `..`；
- 不依赖路径必须已经存在；
- 避免错误地用 Manager CWD。

---

## 8. `defaultMode` 不是一个单一开关

`DefaultPermissionMode` 包括：

| Mode | 主要效果 |
| --- | --- |
| `Default` | PromptPolicy Ask |
| `AcceptEdits` | 合成 `Allow Edit` |
| `Plan` | 保持默认 Permission 语义，编辑还受 Plan Gate |
| `Auto` | PromptPolicy Auto |
| `DontAsk` | PromptPolicy Deny |
| `BypassPermissions` | 合成 Catch-all Allow |

### 8.1 `acceptEdits` 为什么实现为合成规则

它不是在 Manager 里塞一个特殊 if，而是追加：

```text
Allow Edit
```

这样显式 Deny 仍然能靠 `deny > ask > allow` 获胜。

### 8.2 `dontAsk` 不是 Always Deny

它的准确含义是：

> 对无法自动解决而原本需要 Prompt 的动作，不询问，直接拒绝。

明确 Allow、Safe Fast Path 或已存在 Grant 仍可能通过，取决于主链位置。

### 8.3 `bypassPermissions` 与运行期 YOLO

`bypassPermissions` 产生 Catch-all Allow Rule。

运行期 Always Approve/YOLO 则是 Manager Mode。

两者效果相近，但来源与治理方式不同。

---

## 9. Managed Ceiling：为什么用户不能把安全上限抬高

### 9.1 `disable_bypass_permissions_mode`

`requirements.toml` 可以设置：

```toml
[ui]
disable_bypass_permissions_mode = true
```

旧键 `yolo = false` 也兼容为 Pin。

`yolo_disabled_by_policy()` 返回一个具体原因，而不只是 bool。

### 9.2 Pin 同时钳制运行期 Mode

`PermissionHandle::set_yolo_mode` 会同步调用 `clamp_yolo`：

- Requested true；
- Managed Pin 存在；
- 实际原子状态仍为 false。

命令仍发送给 Actor，Actor 会：

- 再次钳制；
- 记录一次拒绝日志；
- 保证内部状态一致。

同步更新 Atomic 的原因是：

> 调用者在 Actor 消费命令前查询 `is_yolo_mode()`，也不能看见短暂的假 true。

### 9.3 Pin 还会过滤非管理员 Catch-all Allow

只禁用 UI 的 YOLO 不够。

用户还可能配置：

```text
Allow Any(*)
Allow Bash(*)
Allow MCPTool(*)
Allow WebFetch(*)
```

`drop_untrusted_catchall_allows` 会在 Pin 存在时：

- 删除非 Admin Source 的危险 Catch-all Allow；
- 保留 Read/Edit/Grep 这种文件访问 Allow；
- 保留真正 Admin Source 的规则；
- 写入 Skip 记录。

关键不是 Path 看起来像不像系统路径，而是 `RequirementSource` 的 Typed Provenance。

### 9.4 Managed `dontAsk` 与 Pin 要配合

源码文档强调：

- Session YOLO 在主链中早于 PromptPolicy Deny；
- 若组织要保证 `dontAsk` 绝不被 Always Approve 绕过；
- 必须同时 Pin 掉 Bypass。

这是配置语义中很容易漏掉的一点。

---

## 10. `PermissionHandle`：Actor 为什么适合权限状态

`PermissionHandle` 有两种形态：

```rust
pub enum PermissionHandle {
    Actor { ... },
    AllowAll,
}
```

`AllowAll` 主要供测试和 Harness 使用。

生产 `Actor` 持有：

- `cmd_tx`：向单一 Actor 发送命令；
- `yolo_state`：共享只读快照；
- `auto_state`：共享只读快照；
- `side_query_wired`；
- `yolo_pin`；
- `deny_read_globs`；
- `in_flight`。

### 10.1 为什么用 Actor

Permission State 包含：

- 当前 Mode；
- Classifier；
- Transcript；
- Project Instructions；
- 持久化 Grant；
- 会话级 Edit Grant；
- Auto Denial 计数；
- Prompter；
- Telemetry Sender。

如果多个 Tool Future 直接用多个 Mutex 修改，很容易出现：

- Prompt 同时覆盖状态；
- 批准后持久化顺序不一致；
- Reset 与 Grant 竞态；
- Auto Denial 计数丢更新。

Actor 让写入串行化。

### 10.2 为什么仍保留 Atomic Mode

调用侧有时只想快速知道：

- 当前是不是 YOLO；
- 当前是不是 Auto；
- 是否已接上 LLM Side Query。

这些查询不需要排队进入 Actor。

所以写状态由 Actor 权威维护，同时用 Atomic 发布即时 Snapshot。

### 10.3 YOLO 与 Auto 互斥

开启 YOLO：

- 清除 Auto。

开启 Auto：

- 清除 YOLO；
- 如果 Classifier 缺失，安装默认 Classifier。

两者不是叠加模式。

### 10.4 `InFlightGuard`

每次请求：

- RAII Guard 增加 `in_flight`；
- Future 完成或 Drop 时减少；
- Clone 的 PermissionHandle 共用计数。

它支持父/子 Agent 并发审批时的 `queue_depth` 遥测。

---

## 11. Permission Request 的消息协议

`PermissionCommand` 主要包括：

- `Request`；
- `SetYoloMode`；
- `SetAutoMode`；
- `SetClassifier`；
- `SetClassifierTranscript`；
- `SetProjectInstructions`；
- `ResetState`；
- `Shutdown`。

`Request` 携带：

- `access`；
- `tool_call_update`；
- `path_context`；
- `respond_to`；
- 请求 Session ID；
- Subagent Type；
- Subagent Description。

### 11.1 为什么 Request 带 ToolCallUpdate

Prompt UI 需要：

- Tool Call ID；
- 当前 Tool 状态；
- 与 Timeline 中 Tool Call 对应。

Permission 不是脱离会话显示一个普通 Confirm。

### 11.2 为什么 Response 用 One-shot

每次请求只能有一个最终决定。

`respond_to`：

- 防止多个结果；
- Requester Drop 可由 `is_closed()` 感知；
- Actor 无需长期保存回调对象。

---

## 12. 请求方消失：先检查，再做昂贵工作，再检查

Manager 收到 Request 后首先检查：

```rust
if respond_to.is_closed() { ... }
```

若请求方已经消失：

- 不运行昂贵分析；
- 发出 Cancelled 遥测；
- 原因是 `requester_gone`；
- 继续处理下一个请求。

Bash Ambient Risk Scan 可能进入 `spawn_blocking`。

扫描结束后源码再次检查 Receiver：

- 若 Requester 在扫描期间消失；
- 放弃后续审批；
- 仍记录 Cancelled。

这是“取消不仅是 UI 状态，也要切断后台安全计算后的迟到提交”。

---

## 13. Bash 为什么是权限系统里最难的一类

### 13.1 一条字符串可能包含多条命令

```bash
echo ok && git push
```

只检查开头的 `echo` 会漏掉后半段。

### 13.2 Wrapper 可以隐藏真实程序

```bash
env FOO=1 timeout 30 nice -n 5 git push
```

Policy 必须剥掉可信 Wrapper，再看内部命令。

### 13.3 内联 Shell 可以再包含脚本

```bash
bash -c 'echo ok; rm -rf build'
```

需要递归解析 `-c` 的 Literal Script。

### 13.4 `env -S` 会再次拆词

```bash
env -S 'bash -c ...'
```

因此源码共享递归深度预算。

### 13.5 Shell 同时是文件读写器

即使 Tool 类型是 Bash，它也可能：

```bash
cat ~/.ssh/id_rsa
echo payload > .git/hooks/pre-commit
cp secret /tmp/out
sed -i ... file
```

如果 Permission 只应用 `Bash(...)` Rule，`Read(...)` 和 `Edit(...)` Rule 就能被 Shell 绕过。

---

## 14. 三路 Preflight

`GatePreflight::evaluate` 一次计算三路结果：

1. Direct Policy：
   - 直接用 `AccessKind` 匹配规则；
2. Bash Command Gate：
   - 拆开命令链，识别内部实际命令；
3. Shell File Gate：
   - 从 Shell AST/参数/重定向识别文件访问。

再用 `combine_decisions` 合并。

### 14.1 总优先级

```text
Reject > Ask > Allow
```

### 14.2 Gate 内部还区分两种 Ask

`GateDecision` 有：

- `AskRuleMatch`；
- `AskFailClosed`；
- `Reject`。

内部排序：

```text
Reject > AskRuleMatch > AskFailClosed
```

### 14.3 为什么必须记录 Ask 来源

两种 Ask 的含义不同：

- Rule Match：管理员或用户明确写了“这里必须问”；
- Fail Closed：分析器无法证明命令安全。

Auto Mode 中：

- Rule Match Ask 必须保留；
- 单纯 Fail-closed Ask 可以交给 Classifier 仲裁；
- 但 Classifier Block 仍然 Prompt，不能静默 Deny。

如果预检只返回一个 bool `needs_prompt`，这些语义会丢失。

---

## 15. Bash Command Gate 如何抵抗绕过

`CompiledPolicy::evaluate_bash_command_gate` 只做升级：

- 可以返回 Reject；
- 可以返回 Ask；
- 不单独返回 Allow。

### 15.1 为什么 Gate 不负责 Allow

一个链式命令只有在每个 Segment 都独立满足 Allow 时，整体才能 Allow。

```bash
cargo test && curl -X POST ...
```

不能因为第一段被允许，就批准整条链。

### 15.2 原始形式和规范化形式都要检查

源码会检查：

- Wrapper 尚未剥离的原始 Words；
- Wrapper 剥离后的内部 Words。

这样规则既能约束用户写出的完整形式，也能约束实际执行主体。

### 15.3 解析不出来时 Ask，而不是放行

以下情况会产生 `AskFailClosed`：

- Shell Script 无法拆解；
- Wrapper 预算耗尽；
- 参数存在歧义；
- `env` Option 不确定；
- Split String 形状不可信；
- 内联脚本不是 Literal；
- 递归深度耗尽。

这体现：

> 无法分析，不等于没有风险。

### 15.4 Allow 是 Conjunctive

Bash Allow 的语义是：

> 所有剥离和拆分后的命令段都必须独立命中 Allow。

不是“任意一个片段命中 Allow”。

---

## 16. Shell File Gate：文件规则不能被 Bash 绕过

`shell_access.rs` 的目标非常直接：

> Deny/Ask 不能通过 Shell Reader、Writer 或 Redirect 绕过。

### 16.1 需要识别的读操作

例如：

- `cat file`；
- `head file`；
- `tail file`；
- `sed` 读输入；
- `grep` 输入文件；
- `find`；
- Shell Input Redirect；
- 可能递归展开的 Reader。

### 16.2 需要识别的写操作

例如：

- `>`、`>>`；
- `tee`；
- `cp`、`mv`；
- `sed -i`；
- `touch`；
- `mkdir`；
- 写 Sink；
- 某些命令的 Output Path。

### 16.3 无法固定到路径时为什么 Ask

若路径来自：

- 动态变量；
- Command Substitution；
- 复杂 Expansion；
- 无法确定的 Shell 结构；

Policy 无法证明它不会触碰 Deny Path。

因此 Gate 提升为 Ask。

### 16.4 Shell File Ask 不能被 Bash Grant 满足

假设用户持久批准了：

```text
Always allow: cat
```

但 Policy 对 `~/.ssh/**` 有 Read Ask。

`cat ~/.ssh/id_rsa` 不能因为已有 Bash Prefix Grant 就自动通过。

`shell_file_forced_prompt` 专门阻止这种跨维度授权。

---

## 17. Confirmation Floor：即使命中 Allow，也可能必须问

源码中存在一组“请求确认下限”：

- 真实文件写入；
- 不安全环境变量；
- Opaque Shell；
- Exec Risk；
- Protected Edit；
- 用户交互类 Tool。

这意味着：

```text
Policy Allow ≠ 永远跳过 Prompt
```

### 17.1 为什么需要 Floor

规则可能只表达：

```text
Allow Bash(cargo *)
```

但命令可以带：

- 执行影响环境变量；
- 插件加载路径；
- 外部脚本；
- 输出重定向；
- 难以分析的嵌套。

Floor 是对“规则匹配的表达能力有限”的补偿。

### 17.2 Protected Edit

某些文件一旦被修改，会影响后续代码执行：

- Grok/Claude Hook；
- Git Hook；
- Shell Startup 文件；
- Claude Settings；
- Cursor Hook。

如果 Agent 先修改这些入口，再触发普通命令，可能绕过下一次独立审批。

所以 Protected Edit 必须抬高到明确确认。

---

## 18. Manager 决策主链

可把 `manager.rs` 的 Request Arm 压缩成以下顺序：

```text
1. Requester 是否已消失
2. 计算 Bash Evaluation / Ambient Exec Risk
3. 识别 Protected Edit
4. GatePreflight：Direct + Bash Command + Shell File
5. Policy Deny
6. YOLO（但不能越过 Shell Forced Prompt）
7. 已存在的 Session/Persisted Grant
8. Auto 下的显式 Policy Allow
9. Auto Fast Path / Classifier
10. Sandbox Bash Auto Allow
11. 应用剩余 Policy Allow/Ask
12. 普通 Safe/Grant Pre-decision
13. PromptPolicy::Deny
14. 向用户 Prompt
15. 把 Prompt Outcome 映射为 Decision 并更新 State
```

这份顺序比单独记住每个 if 更重要。

---

## 19. Policy Deny 为什么最早

`GatePreflight` 之后，Manager 首先处理：

```rust
Some(Decision::Reject(reason))
```

并转换为：

```rust
Decision::PolicyDeny(reason)
```

它早于：

- YOLO；
- Auto；
- Sandbox Auto Allow；
- Session Grant；
- 静态白名单。

因此任何自动批准都不能覆盖 Deny。

---

## 20. YOLO / Always Approve 的真实边界

YOLO 会在没有 Shell Forced Prompt 时快速返回 Allow。

但它仍不能绕过：

- 明确 Policy Deny；
- Bash/Shell Gate 的 Forced Ask；
- Managed Pin 对 YOLO 本身的禁用。

所以源码里的 YOLO 不是“不运行任何安全代码”。

更准确地说：

> 它跳过普通交互审批，但仍受不可绕过的 Policy 与分析 Gate 约束。

---

## 21. Grant：一次批准、会话批准和持久批准

`PermissionState` 保存：

- `edit_policy`；
- `allow_bash_execute`；
- `allowed_bash_commands`；
- `disallowed_bash_commands`；
- `allowed_bash_globs`；
- `allowed_web_fetch_domains`；
- `allowed_mcp_tools`；
- `allowed_mcp_servers`；
- MCP Server Grant Schema Version。

### 21.1 Literal Prefix 与 Glob 分开

`allowed_bash_commands` 是 Literal Prefix。

`allowed_bash_globs` 是用户明确编辑的 Glob。

二者不能混在一起。

否则用户批准一个包含 `*` 字符的普通命令时，可能被错误提升成通配符。

### 21.2 Edit Session Grant 不持久化

`AllowEditsForSession`：

- 只在内存中设置 `allow_edits_for_session`；
- Session 结束即消失；
- 不写入 Permission State 文件。

### 21.3 Web Fetch Grant 按 Domain

用户可以批准：

- 本次 URL；
- 该 Domain 在当前授权范围内继续访问。

Domain 必须先规范化后再匹配。

### 21.4 MCP Grant 分 Tool 与 Server

两种 Scope：

- 精确 Tool Name；
- Qualified MCP ID 的 Server Component。

Server Grant 不能靠字符串前缀随意截取。

源码会：

- 验证完整 Qualified ID；
- 提取 Server；
- 记录 Schema Version；
- 对旧版本中无法证明来源的 Server Grant 做迁移清理。

### 21.5 状态按 CWD 和 Client 隔离

State 路径以 Session CWD 派生。

若存在 Client Identifier，则文件名包含经过清洗的 Client ID：

```text
permission_<client>.toml
```

否则：

```text
permission.toml
```

### 21.6 为什么原子写

`persist_state` 使用配置层的 Atomic Write：

- 避免进程中断留下半个 TOML；
- 避免下一次启动解析出部分 Grant；
- 安全状态不能依赖普通覆盖写。

### 21.7 Stale State Cleanup

系统会遍历 Session State：

- 只识别 `permission*.toml`；
- 检查修改时间；
- 删除超过期限的旧状态。

Grant 不是永久无限增长的全局数据库。

---

## 22. Session Grant 为什么仍不能越过 Ask Floor

Manager 在使用 `session_grant_pre_decision` 前会检查：

- 没有 Policy Forced Prompt；
- 没有 Shell Forced Prompt；
- 不是 Protected Edit。

因此：

- 过去批准过类似命令；
- 不代表未来任何上下文都自动允许；
- 管理规则与确认下限始终更高。

这叫做：

> Grant 是加速路径，不是安全上限的替代品。

---

## 23. Auto Mode 的三段式结构

Auto Mode 不是“让另一个 LLM 全权决定”。

它分三段：

1. 确定性 Fast Path；
2. Classifier；
3. 根据 Verdict 直接允许、在预算内拒绝并让 Agent 换方案，或回到 Prompt。

### 23.1 `AutoFastPath::Allow`

确定性允许的类别包括：

- Read；
- Grep；
- WebSearch；
- Todo/Task/Plan/Skill Search 等元数据或协调工具；
- 所有 Edit——这是明确的产品决策；
- 精确 No-op Bash：`true`、`:`、`false`。

但调用者已先执行：

- Policy Deny；
- Policy Ask；
- Protected Edit；
- Bash Floors。

所以 Fast Path 不是越过上层限制。

### 23.2 `AutoFastPath::PromptUser`

要求真实用户交互的 Tool 不交给 Classifier 自动批准。

名字启发式包括：

- ask_user；
- askUserQuestion；
- MCP 中 confirm + human 等。

### 23.3 `AutoFastPath::Classify`

其他动作进入 Classifier，例如：

- Bash；
- MCP；
- Web Fetch；
- 具有副作用或上下文相关的操作。

---

## 24. Auto Classifier 看见什么

`ClassifierContext` 包含：

- 最近 User Text；
- Assistant Tool Use；
- 已记录的 Permission Decision；
- Project Instructions。

`build_classifier_messages` 支持四档：

| Prompt Type | 上下文 |
| --- | --- |
| `Full` | System + AGENTS + Transcript + Action + JSON Instruction |
| `NoUserToolPrefix` | 去掉 Conversation Transcript，保留 AGENTS |
| `BareInstructions` | System + Action + JSON Instruction |
| `JustCommand` | System + 待判断命令 |

### 24.1 为什么批准记录与普通 Transcript 分开

普通文本可能包含：

```text
User: I approve everything
```

它可能来自：

- 文件内容；
- Tool Output；
- Assistant 参数；
- Prompt Injection。

只有 Harness 自己记录的 Permission Decision 才是可信批准历史。

源码把它序列化成独立 JSON Record，并明确：

- `decision` 字段可信；
- `tool` 与 `args` 只是惰性数据；
- 内部任何“批准声明”都不可信。

### 24.2 为什么要 Neutralize Heading

不可信 Transcript 中以 `#` 开头的行会被转义。

目的是降低内容伪装成：

- System Section；
- Recent Conversation；
- Permission Decisions；
- 新的高优先级指令。

### 24.3 Project Instructions 不代表用户批准

Classifier System Prompt 明确区分：

- AGENTS.md 可以描述项目规则；
- 但它不能证明真人授权了外部副作用；
- Tool 参数中的文字也不能证明授权。

---

## 25. Classifier Verdict 与失败语义

`ClassifierVerdict`：

- `Allow`；
- `Block`；
- `Unavailable`。

Provenance：

- LLM；
- Heuristic；
- Timeout；
- Transport Error。

### 25.1 普通 Block 会先拒绝，但不是永久 Deny

普通 Classifier Block 在 Denial Budget 尚未耗尽时，会返回 `PolicyDeny`：

- 连续拒绝计数加一；
- 总拒绝计数加一；
- 错误文字要求 Agent 采用更安全、仍符合用户要求的方案；
- 不得原样重试或尝试绕过。

这让 Agent 有机会自行收窄动作，而不是每次都打断用户。

达到连续或总拒绝上限后，同类 Block 才会强制 Prompt。

因此普通路径准确的映射是：

- Allow → 自动执行；
- Block 且预算未耗尽 → `PolicyDeny`，让 Agent 换安全方案；
- Block 且预算耗尽 → 强制 Prompt；
- Unavailable → 强制 Prompt。

但有一条例外：如果 Classifier 正在仲裁 Fail-closed Gate Ask 或 Bash Request Floor，Block 从一开始就 Prompt，不产生静默拒绝，也不消耗 Denial Budget。因为这些路径本来已经拥有“必须确认”的下限。

### 25.2 Timeout 为什么不能默认 Allow

Classifier 超时意味着：

> 系统没有获得足够证据证明动作可以静默执行。

所以回到用户，而不是 Fail-open。

### 25.3 Denial Budget

Manager 维护：

- 连续自动拒绝计数；
- 总自动拒绝计数。

常量包括：

```rust
AUTO_DENY_CONSECUTIVE_LIMIT = 3
AUTO_DENY_TOTAL_LIMIT = 20
```

这用于防止 Auto 在长会话中反复进入无效拒绝循环。

具体 Gate Deferral 的 Block 不一定消耗同样预算，因为它本来就是一条必须回到用户的失败关闭路径。

---

## 26. Fail-closed Ask 如何与 Auto 协作

`GatePreflight` 只有在同时满足以下条件时，才让 Gate Ask 交给 Classifier：

- 当前是 Auto Mode；
- Ask 来自分析失败；
- 没有任何明确 Rule Match Ask。

结果：

| 场景 | Auto 是否可仲裁 |
| --- | --- |
| 明确 Ask Rule 命中 | 否 |
| 只有 Bash 解析失败 Ask | 是 |
| 解析失败 Ask + 另一个 Ask Rule | 否 |
| Deny 命中 | 否，直接 Deny |

这套设计保住两个目标：

- 管理员写出的 Ask 永远具约束力；
- 复杂但常见的 Shell 形状不必全部人工确认。

---

## 27. Sandbox Auto Allow Bash

Manager 中还有：

```rust
xai_grok_sandbox::should_auto_allow_bash()
```

只有同时满足：

- 配置开启 `auto_allow_bash`；
- Sandbox 确实 Active；
- Bash Evaluation 允许这条 Fast Path；
- 没有 Policy Forced Prompt；
- Auto 没有强制 Prompt；

才会以 `sandbox_auto` 理由批准。

### 27.1 为什么不能只看“配置选择了 Sandbox”

`should_auto_allow_bash` 要求：

```text
AUTO_ALLOW_BASH == true && is_active() == true
```

若平台不支持、应用失败或未真正安装：

- 仅有 Profile 名称不够；
- 不得据此减少 Prompt。

### 27.2 Sandbox Auto Allow 仍低于 Policy

它发生在：

- Policy Deny 之后；
- Forced Ask 之后；
- Auto Forced Prompt 之后。

因此 Sandbox 只能作为减少普通 Bash Prompt 的证据，不能解除显式安全规则。

---

## 28. `PromptPolicy::Deny` 的位置

当：

- 没有 Policy Allow；
- 没有 Safe/Grant Pre-decision；
- 没有 Auto Allow；
- 原本需要询问；

且 Prompt Policy 是 Deny 时，系统直接拒绝。

这就是 `dontAsk`。

它不是最前面的总开关，而是：

> 最终 Prompt 分支的非交互替代。

---

## 29. Prompter 如何构造不同批准 Scope

`AcpPrompter` 根据 `AccessKind` 与 Client Type 构造选项。

`PromptOutcome` 包括：

- `AllowOnce`；
- `AllowAlways`；
- `AllowEditsForSession`；
- `AllowAlwaysBashCommand(prefix)`；
- `AllowAlwaysBashGlob(pattern)`；
- `AllowAlwaysDomain(domain)`；
- `AllowAlwaysMcpTool(name)`；
- `AllowAlwaysMcpServer(server)`；
- `RejectOnce`；
- `RejectAlwaysBashCommand(prefix)`；
- `Cancelled`；
- `FollowupMessage`；
- `Error`。

### 29.1 Client Type 为什么会影响选项形状

不同客户端支持：

- Rich Metadata；
- Scope Editor；
- 自定义 Option ID；
- 普通 ACP Fallback。

所以 TUI/Desktop 可以显示更精细的：

- Always allow command；
- Always allow MCP Tool/Server；
- Always allow domain。

旧客户端则回落到通用选项。

### 29.2 `remember_tool_approvals`

当该 Gate 关闭时，会移除：

- Command 永久 Allow/Deny；
- MCP 永久 Allow；
- Domain 永久 Allow；
- Generic Always Allow/Reject。

但仍保留：

- Allow Once；
- Reject Once；
- Enable Always Approve；
- Edit Session Allow。

默认 false 是 Fail-safe。

### 29.3 Enable Always Approve 选项为什么映射为 Allow Once

该选项的 UI 意图是：

- 允许当前调用；
- 另外由 Shell 切换 Mode。

它本身不能在 `PromptOutcome` 中伪装成普通 `AllowAlways`，否则可能把当前 Tool 的 Scope 错误持久化。

---

## 30. Local ACP 与 Hub 审批

`AcpPrompter::request` 有两条传输：

1. 配置了 `hub_permission`：
   - 通过服务端/Chat 请求；
2. 没有：
   - 通过本地 ACP Gateway `request_permission`。

两条传输最后都归一化成 `PromptOutcome`。

因此 Manager 不关心：

- 用户是在桌面弹窗点的；
- 还是在远端 Chat 点的。

它只处理 Typed Outcome。

---

## 31. Prompt 生命周期与 Drop Guard

Prompt 开始时写：

```text
PermissionRequested
```

正常结束时写：

```text
PermissionResolved
```

并记录从真正显示 Prompt 开始计算的 `wait_ms`。

### 31.1 为什么还有 `ResolvedOnDrop`

如果 Future：

- 被取消；
- Panic Unwind；
- 提前 Return；

普通尾部代码可能不会执行。

`ResolvedOnDrop` 的 Drop 会补发：

```text
PermissionResolved(decision = Cancelled)
```

这样事件流不会出现永远悬空的 Requested。

这是 RAII 在可观测性上的经典用途。

---

## 32. Prompt Outcome 怎样影响 State

Manager 收到 Outcome 后会：

- Allow Once：只回复当前请求；
- Allow Edit Session：设置内存标记；
- Allow Bash Prefix：加入 Literal Prefix Set 并持久化；
- Allow Bash Glob：加入 Glob Set 并持久化；
- Reject Bash Prefix：加入 Deny Set 并持久化；
- Allow Domain：加入域名 Set；
- Allow MCP Tool：加入精确 Tool Set；
- Allow MCP Server：验证后加入 Server Set；
- Followup：返回 `Decision::FollowupMessage`；
- Cancelled：返回 `Decision::Cancelled`；
- Error：按拒绝/错误路径收敛。

这里有两个不变量：

1. 先验证 Scope，再写 State；
2. 当前 Request 的 Allow 与未来 Request 的 Grant 是两件事。

---

## 33. Permission Telemetry 记录什么

`PermissionEvent` 包含：

- Tool ID 与 Tool Name；
- Access Kind 与受限 Access Detail；
- 当前 Permission Mode；
- 是否 YOLO；
- 是否自动批准；
- 是否真的 Prompt 用户；
- 最终 Decision；
- Prompt Outcome；
- Reject Reason；
- Decision Reason；
- Classifier Source 与延迟；
- Auto Denial 计数；
- Prompt/处理等待时间；
- Queue Depth；
- Subagent Session、Type、Description；
- Timestamp。

### 33.1 `decision` 与 `decision_reason` 不同

例如：

```text
decision = allow
decision_reason = persisted_grant
```

或：

```text
decision = allow
decision_reason = sandbox_auto
```

前者告诉你“结果”，后者告诉你“为什么走到这个结果”。

### 33.2 常见 Reason

包括：

- `yolo`；
- `policy_allow` / `policy_deny` / `policy_ask`；
- `bash_command_gate_ask`；
- `shell_file_gate_ask`；
- `auto_fast_path`；
- `auto_classifier_allow`；
- `auto_classifier_block`；
- `auto_classifier_timeout`；
- `auto_classifier_unavailable`；
- `auto_denial_limit`；
- `sandbox_auto`；
- `persisted_grant`；
- `session_grant`；
- `static_allowlist`；
- `safe_command`；
- `session_deny`；
- `prompt_deny`；
- `needs_user`；
- `bash_request_floor`；
- `opaque_shell`；
- `requester_gone`。

### 33.3 Tool Name 的单一来源

`prompter::tool_name_for_access` 同时被：

- `events.jsonl`；
- Upload Permission Event；

使用。

这样两个观测系统不会对同一次调用使用不同名字。

---

## 34. Permission 与 Sandbox 的责任边界

### 34.1 Permission 是语义授权

它理解：

- 用户意图；
- Tool 种类；
- Command；
- Path；
- Domain；
- MCP Scope；
- Policy；
- 历史批准；
- 是否应该问用户。

### 34.2 Sandbox 是能力约束

它限制：

- 哪些路径可读；
- 哪些路径可写；
- 哪些路径完全 Deny；
- 子进程是否有网络；
- Hook 等敏感入口是否可写。

### 34.3 两者组合

```text
Permission Reject
    → 不尝试执行

Permission Allow + Sandbox Allow
    → 执行可能成功

Permission Allow + Sandbox Deny
    → OS 拒绝，Tool 返回错误

Permission Ask + User Allow + Sandbox Deny
    → 用户授权了意图，但系统能力上限仍拒绝
```

---

## 35. Sandbox 生命周期：进程启动时一次应用

`SandboxManager` 的使用模式：

```rust
let mut sandbox = SandboxManager::new(profile, workspace);
sandbox.apply(workspace)?;
sandbox.install();
```

### 35.1 `apply`

负责：

- 解析 Profile；
- 检测平台支持；
- 构造 Capability Set；
- 应用内核规则；
- 记录成功或失败；
- 设置 `applied`。

### 35.2 为什么是 Irreversible

内核 Sandbox 一旦对进程生效，不能在每个 Tool Call 前任意放宽。

这防止 Agent 通过运行期代码切换到更宽能力。

### 35.3 `install`

把：

- Profile；
- Logger；
- Applied 状态；
- Network Restriction；

放进全局 `OnceLock`。

之后其他模块可以查询：

- `is_active()`；
- `profile_name()`；
- `should_restrict_child_network()`；
- `metrics()`。

---

## 36. Configured、Requested 与 Active 必须区分

Sandbox 暴露三个不同概念：

### 36.1 Configured Profile

`configured_profile_name()`：

- 用户/配置最终选择了什么；
- 即使是 `off` 也记录。

### 36.2 Requested Confinement

`requested_confinement_profile()`：

- 选择的是非 Off Profile；
- 表示调用方要求受约束；
- 不证明 `apply` 成功。

### 36.3 Active

`is_active()`：

- 内核 Sandbox 是否实际应用。

这三个值不能互换。

尤其 `sandbox_auto_allow_bash` 只能依赖 Active。

---

## 37. 内置 Sandbox Profile

### 37.1 `workspace`

- 默认可读整个文件系统；
- Workspace 与必要路径可写；
- 默认不限制网络；
- 对敏感 Hook 入口施加写保护。

适合普通本地开发。

### 37.2 `devbox`

- 大多数文件系统可写；
- `/data` 例外；
- 不限制网络；
- 不施加普通 Hook Write Deny。

它用于本身已经是隔离开发环境的场景。

### 37.3 `read-only`

- 默认可读；
- 只有最小必要路径可写；
- 子进程网络受限；
- Hook 入口写保护。

### 37.4 `strict`

- 默认不可读；
- 显式允许系统运行时路径、Workspace、Grok Home；
- Workspace 与必要路径可写；
- 子进程网络受限；
- Hook 入口写保护。

### 37.5 `off`

- 不应用本地 Sandbox；
- 不能作为 Custom Profile 的 Base。

---

## 38. 自定义 Profile

配置位置：

```text
~/.grok/sandbox.toml
<workspace>/.grok/sandbox.toml
```

字段：

- `extends`；
- `restrict_network`；
- `read_only`；
- `read_write`；
- `deny`。

### 38.1 项目配置只能新增名字

若全局已定义一个 Custom Profile：

```text
secure-build
```

项目不能用同名 Profile 覆盖它。

`merge_project_profiles` 使用 Entry-or-insert：

- Global 定义保留；
- Project 只能添加新名字。

否则恶意仓库可以保持“可信名字”，却把 Deny 清空。

### 38.2 Custom 只能继承 Built-in

Custom Profile：

- 可以继承 workspace/devbox/read-only/strict；
- 不能继承 off；
- 不能继承另一个 Custom。

这避免循环、深层覆盖与难以解释的安全继承。

---

## 39. OS Enforcement：不同平台的组合

### 39.1 macOS

通过 nono 使用 Seatbelt 能力模型。

### 39.2 Linux

通过 nono 使用 Landlock。

对某些 Deny Read 和 Hook Write Deny，还会在启动前：

- 构造 bubblewrap re-exec；
- 把目标路径只读绑定；
- 用不可读占位覆盖 Deny Read；
- 丢弃 Capability。

### 39.3 子进程网络

Agent 主进程必须访问模型 API，不能简单封掉整个进程网络。

所以：

- 主进程网络保留；
- 已知 Linux Child Launch Path 在 `pre_exec` 中安装 seccomp Network Filter；
- `read-only` 与 `strict` 默认请求 Child Network Restriction。

---

## 40. 为什么 Linux 需要 bubblewrap + Landlock

Landlock 更自然地表达“允许哪些父路径”。

但某些需求是：

- 父目录广泛可读；
- 某个子路径完全不可读；
- 某些 Hook 路径必须只读。

源码注释指出，子路径例外不能简单靠授权父目录后再 Deny。

bubblewrap 的 Bind Mount 可以：

- 把特定路径挂成只读；
- 用占位路径覆盖读取；
- 在进入 Agent 主进程前完成。

因此两者互补。

---

## 41. Fail-open 与 Fail-closed 的细致边界

`SandboxManager::apply` 本身对一般不支持平台会：

- Warning；
- 记录 ApplyFailed；
- 返回 Ok；
- `applied = false`。

这是通用兼容性上的优雅降级。

但 Shell 启动接线对“必须保护”的情形更严格：

- Custom Profile；
- Hook Write Deny；
- Linux Deny Read；

如果无法落实，会打印错误并退出进程。

因此不是简单的“Sandbox 失败永远继续”。

准确说：

> 普通 Profile 可以告警降级；声明了关键 Deny/Hook 保护的 Profile 缺失保护时拒绝启动。

---

## 42. bwrap Marker 为什么不能盲信

环境变量：

```text
__GROK_INSIDE_BWRAP
```

可能被外部伪造。

源码不会只因 Marker 存在就相信保护已经完成。

当 Hook Write Deny 必需时，会调用：

```rust
verify_hook_write_deny_enforced()
```

检查 Mount 实际不可写。

若验证失败：

- 认为可能发生 Marker Spoof；
- 拒绝启动。

---

## 43. Hook Write Deny 为什么跨越 Permission 与 Sandbox

Permission 有 `ProtectedEdit`，先要求明确批准。

Sandbox 又对关键 Hook Source 做 Write Deny。

两层看起来重复，实际目的不同：

- Permission：防止 Agent 在用户不知情时修改；
- Sandbox：即使某个路径识别、Tool 类型、Shell 语法分析有遗漏，也不让进程写入。

而某些 Profile 可能允许更宽行为，故两层配置仍各自独立。

---

## 44. 文件系统 Tool 怎样记录 Sandbox Violation

本地 File System Tool 在操作失败时会调用：

```rust
xai_grok_sandbox::log_violation(target, operation)
```

操作类型包括：

- read；
- mkdir；
- write；
- delete。

`SandboxLogger`：

- 保存事件；
- 增加 Metrics；
- 立即 Flush 到磁盘。

注意：

> 记录 Violation 不是 Enforcement 本身；真正拒绝来自内核。

---

## 45. Sandbox Telemetry

Sandbox Event 类型包括：

- Profile Applied；
- Apply Failed；
- FS Violation；
- Net Violation；
- Bypass Granted；
- Bypass Denied。

Session Upload 还记录：

- Configured Profile；
- 是否 Applied。

这能区分：

```text
用户选了 strict，但应用失败
```

与：

```text
用户根本选择了 off
```

---

## 46. Subagent 的权限继承

Subagent 通常拿到 Clone 的 `PermissionHandle`。

因此共享：

- 权限 Actor；
- 持久和会话 Grant；
- YOLO/Auto 状态；
- Managed Pin；
- Deny Read Globs；
- In-flight Counter。

但每个 Request 仍携带：

- 自己的 Session ID；
- Agent Type；
- Description；
- Path Context。

### 46.1 为什么共享 Manager

如果每个 Child 自建 Manager：

- 用户对同一动作反复审批；
- 父子 Grant 不一致；
- 多窗口同时修改 Permission State；
- 审计难以建立统一顺序。

### 46.2 为什么不能共享 CWD

Child 可能：

- 在不同工作目录；
- 在 Worktree；
- Resume 到原 Session；
- Fork 不同路径。

所以规则求值必须请求级绑定 CWD。

### 46.3 Child 不能抬高上限

Clone 保留：

- `yolo_pin`；
- Policy；
- Deny Globs。

Child 可以拥有更少 Capability，但不能通过重建 Handle 获得更宽权限。

---

## 47. Permission 与 Tool Result 怎样回到模型

典型映射：

### 47.1 Allow

- Tool 实现执行；
- Result 进入 Conversation；
- Agentic Loop 继续采样。

### 47.2 PolicyDeny

- 作为可解释 Tool Error 回填；
- 模型可以选择合法替代方案；
- 不应伪装成用户主动 Cancel。

### 47.3 Reject

- 表示用户或普通权限路径拒绝；
- Session 可按当前 Turn 语义停止或反馈。

### 47.4 FollowupMessage

- 作为新的用户指导进入 Loop；
- 模型按新约束重新规划。

### 47.5 Cancelled

- 映射为取消终态；
- 不应自动重试同一副作用。

---

## 48. 常见错误理解

### 48.1 “Tool 在 Prompt 里，所以模型能随便用”

错。

Prompt 中的 Schema 只提供调用语言；Capability、Hook、Permission 和 Sandbox 仍在运行期约束。

### 48.2 “YOLO 会越过所有 Deny”

错。

Policy Deny 在 YOLO 之前；Forced Ask 也能阻挡 YOLO。

### 48.3 “Auto Mode 就是 YOLO”

错。

Auto 有 Fast Path、Classifier、Prompt 与 Denial Budget；YOLO 是交互批准旁路。

### 48.4 “Sandbox 开着就不需要 Permission”

错。

Sandbox 不理解用户是否同意 Push、发消息、创建工单或调用第三方 MCP。

### 48.5 “Permission Allow 就能写任何地方”

错。

Sandbox 和 OS 仍可能拒绝。

### 48.6 “Allow Bash(cargo *) 可以允许整条命令链”

错。

链中每个 Segment 都必须独立满足 Allow；Gate 还会递归检查 Wrapper 与 Inline Shell。

### 48.7 “项目配置优先级更高，所以能覆盖 Admin Deny”

错。

Rule Evaluation 与来源顺序无关，Deny 永远优先。

### 48.8 “历史批准可以当作自然语言放进 Prompt”

危险。

只有 Harness 生成的 Typed Permission Decision 才是可信批准记录。

---

## 49. 一次完整案例：`cat secret && git push`

假设模型调用：

```bash
cat ./secret.txt && git push origin main
```

可能经过：

1. Tool Registry 找到 Bash Tool；
2. ToolInput 转成 `AccessKind::Bash`；
3. Hook 与 Mode Gate 放行到 Permission；
4. Bash Splitter 得到两个 Segment；
5. Command Gate 分别检查 `cat` 与 `git push`；
6. Shell File Gate 识别 `./secret.txt` 的 Read；
7. Direct Policy 检查完整 Bash；
8. 若 `Read(./secret.txt)` Deny：
   - 合并结果 Reject；
   - 早于 YOLO/Auto；
   - 返回 PolicyDeny；
9. 若 Read Ask 且 Bash Allow：
   - Ask 胜过 Allow；
   - Prompt 用户；
10. 若用户批准：
   - Tool 尝试执行；
11. 若 Sandbox 不允许读该文件：
   - 内核仍拒绝；
   - Tool Error 回填。

---

## 50. 一次完整案例：Subagent 在 Worktree 编辑

父 Agent CWD：

```text
/repo
```

Child CWD：

```text
/repo/.worktrees/feature
```

Child 请求：

```text
Edit(./src/lib.rs)
```

流程：

1. Child 使用共享 `PermissionHandle`；
2. Request 携带 Child `real_cwd`；
3. Manager 不使用自己的父 CWD；
4. 路径解析为 Worktree 中的文件；
5. Policy Glob 以 Child CWD 匹配；
6. Telemetry 写 Child Session ID/Type/Description；
7. 用户批准的 Scope 写入共享 State；
8. 实际写入仍受进程 Sandbox Profile 限制。

---

## 51. 一次完整案例：Auto Classifier 超时

模型请求一个非 Fast Path 的 MCP Tool：

1. Policy 无 Deny；
2. 没有现成 MCP Grant；
3. 不是用户交互 Tool；
4. Auto 进入 Classifier；
5. Side Query 超时；
6. Outcome 是 `Unavailable`，Source 是 `Timeout`；
7. Manager 设置 `auto_forced_prompt`；
8. Sandbox Auto Allow 不适用于 MCP；
9. 静态 Grant 不能覆盖 Forced Prompt；
10. 用户看到审批；
11. Telemetry 同时记录 Timeout Source 与最终 Prompt Outcome。

系统没有因“自动判断服务坏了”而放宽授权。

---

## 52. 测试想固定哪些不变量

阅读 Permission 测试时，建议按不变量分组，而不是按文件顺序：

### 52.1 Rule Resolution

- Deny > Ask > Allow；
- Managed Mode 覆盖 User Mode；
- 项目配置受 Trust Gate；
- Catch-all Allow 在 Pin 下被过滤；
- Skip Provenance 被保留。

### 52.2 Bash Analysis

- 命令链每段都检查；
- Wrapper 不能隐藏危险命令；
- `bash -c` 递归；
- 无法解析时 Ask；
- Allow 必须全链满足。

### 52.3 Shell File Access

- Reader/Writer/Redirect 不能绕过 Read/Edit Rule；
- 动态路径失败关闭；
- Shell File Ask 不被 Bash Grant 满足。

### 52.4 Mode

- YOLO 与 Auto 互斥；
- Pin 同步钳制；
- Deny 早于 YOLO；
- 普通 Auto Block 在预算内返回 PolicyDeny，预算耗尽后 Prompt；
- Deferred Gate/Floor Block 与 Unavailable 直接回到 Prompt。

### 52.5 Prompt

- Option ID 与 Outcome Scope 对应；
- Remember Gate 会移除持久 Grant；
- Edit Session Allow 不持久化；
- Drop 会补 Cancelled Event。

### 52.6 Sandbox

- Profile 解析；
- Project 不能覆盖 Global 同名 Profile；
- Hook Write Deny；
- Deny Path E2E；
- Network Inheritance；
- bwrap Marker 验证；
- Configured 与 Applied Telemetry 区分。

---

## 53. 推荐源码阅读顺序

第一次阅读：

1. `permission/types.rs`；
2. `permission/manager.rs` 的 `PermissionHandle`；
3. Request Arm 的主决策链；
4. `gate_preflight.rs`；
5. `policy.rs` 的 `evaluate_with_cwd`；
6. `prompter.rs` 的 `PromptOutcome`；
7. `state.rs`；
8. `sandbox/profiles.rs`；
9. `sandbox/lib.rs`；
10. Shell 启动的 `apply_sandbox`。

第二次安全专题阅读：

1. `bash_command_splitting.rs`；
2. `shell_access.rs`；
3. `exec_risk.rs`；
4. `policy.rs` 的 Bash Gate；
5. Protected Edit；
6. Hook Write Deny；
7. Deny Path E2E Tests。

第三次 Auto 专题阅读：

1. `AutoFastPath`；
2. `ClassifierContext`；
3. `build_classifier_messages`；
4. System Prompt；
5. Manager 的 Classifier Verdict 分支；
6. Denial Counter Tests。

---

## 54. 你应该记住的十条结论

1. Tool Schema 只是调用入口，不是执行授权。
2. `AccessKind` 是 Tool 世界到安全世界的归一化协议。
3. Permission Rule 永远按 `Deny > Ask > Allow`，不按文件覆盖顺序。
4. Bash 必须同时检查命令语义和文件访问语义。
5. 无法分析的 Shell 默认 Ask，不默认 Allow。
6. YOLO、Auto 和 Sandbox Auto Allow 都不能越过 Policy Deny。
7. Grant 是快捷路径，不能越过 Forced Ask 与 Protected Edit。
8. 父子 Agent 可共享 Manager，但每次 Request 必须携带自己的 CWD。
9. Permission 决定是否尝试，Sandbox 决定内核最终允许什么。
10. 安全系统不仅要给出决定，还要记录来源、等待、取消和执行环境。

---

## 55. Glossary

### Access Detail

对具体访问内容的长度受限描述，例如 Bash Command、URL 或 MCP Input 摘要。用于 Classifier 与 Telemetry。

### AccessKind

Permission 层统一的访问类别：Read、Grep、Edit、Bash、MCP、Web Fetch、Web Search。

### ACP

Agent Client Protocol。Session、Tool Update、Permission Request 等前后端交互使用的协议类型。

### Actor

拥有可变状态并通过 Channel 串行处理命令的并发模型。Permission Manager 用它避免多请求并发修改 Grant 与 Mode。

### Allow

允许本次动作继续。它不保证 Sandbox 或外部服务最终成功。

### Allow Once

只允许当前 Tool Call，不产生未来 Grant。

### Allow Always

按某个 Scope 创建可复用 Grant；实际 Scope 可能是命令、Glob、Domain、MCP Tool 或 MCP Server。

### Always Approve

运行期自动批准模式，也常称 YOLO。它跳过普通 Prompt，但仍受不可绕过 Policy 与 Gate 限制。

### Ambient Exec Risk

不只由命令字面决定，而要查看当前目录、仓库或环境才能判断的执行风险。

### Ask

策略要求进入用户审批的决定。

### Ask Fail Closed

不是明确规则命中，而是分析器无法证明安全时产生的 Ask。

### Ask Rule Match

明确的 Ask Rule 命中。Auto Classifier 不能取消它。

### Auto Mode

使用确定性 Fast Path、Classifier 与人工 Prompt 组合减少审批次数的模式。

### Bash Chain

用 `&&`、`||`、`;`、Pipe 等组成的多命令脚本。安全上必须分析每个 Segment。

### Bypass Permissions

配置中的 Default Mode，通过合成 Catch-all Allow 获得宽泛批准；可被 Managed Pin 禁止。

### bubblewrap / bwrap

Linux 用户空间 Sandbox 工具。项目用 Bind Mount 表达 Landlock 难以直接表达的 Deny Read 与 Write Deny。

### Capability

Agent 是否拥有某 Tool 或 OS 进程拥有哪些系统能力。它与单次 Permission Decision 不同。

### Catch-all Rule

没有有效范围限制、基本覆盖整个 Tool 维度的规则，例如 `Allow Bash(*)`。

### Classifier

Auto Mode 中判断动作应自动 Allow 还是回到用户的分类器，可由 Heuristic 或 LLM Side Query 实现。

### Classifier Context

Classifier 可见的近期 Conversation、Permission Decisions 与 Project Instructions。

### Classifier Provenance

分类结果来源：LLM、Heuristic、Timeout 或 Transport Error。

### Confirmation Floor

即使存在 Allow，也要求人工确认的安全下限，例如 Protected Edit、Opaque Shell、Exec Risk。

### Configured Profile

配置解析后选择的 Sandbox 名字，不代表应用成功。

### Decision

Permission Manager 的 Typed 终态：Allow、Ask、Reject、PolicyDeny、FollowupMessage、Cancelled。

### Decision Reason

导致最终决定的路径，例如 policy_deny、persisted_grant、sandbox_auto。

### Denial Budget

Auto Mode 对连续和总拒绝次数的计数上限，防止长会话反复卡在自动拒绝。

### Deny

明确禁止访问。Policy 中优先级最高。

### Deny Glob

禁止读取或访问的路径模式，可能同时用于 Tool 层排除和 Sandbox 层能力收窄。

### Domain Grant

对 Web Fetch 的规范化 Domain 创建的可复用批准。

### Edit Policy

Permission State 中编辑行为的策略状态。

### Fail Closed

当分析、验证或传输无法证明安全时，选择 Ask、Deny 或拒绝启动，而不是放行。

### Fail Open

安全组件失败后仍继续运行。项目只在部分兼容场景告警降级，对声明的关键保护会拒绝启动。

### Fast Path

无需用户 Prompt 或 Classifier 的确定性快捷决策。

### Followup Message

用户在审批界面输入的补充指导，不是简单 Allow/Reject。

### Folder Trust

用户是否信任当前项目。未信任项目不能贡献项目级 Permission 配置。

### Gate

执行链上的前置约束。本文中的 Bash Command Gate 与 Shell File Gate 会把风险升级为 Ask 或 Deny。

### Gate Preflight

一次性合并 Direct Policy、Bash Command Gate 与 Shell File Gate 的预检结果。

### Glob

支持 `*`、`**` 等通配模式的字符串或路径匹配规则。

### Grant

从用户批准派生的可复用授权，可能是 Session-only 或持久化。

### Hook

Tool 执行前后的扩展点。Hook Gate 与 Permission 是相邻但独立的执行层。

### Hook Write Deny

对可能影响后续执行的 Hook/启动入口施加的 Sandbox 写保护。

### In-flight

已进入 Permission 请求生命周期但尚未收敛的请求数量。

### Landlock

Linux 内核文件系统访问控制机制，nono 在 Linux 上用它落实 Capability Set。

### Literal Prefix

按普通字符串前缀匹配命令，不把 `*` 当通配符。

### Managed Ceiling

组织策略设置的安全上限，例如禁止启用 Always Approve；低层配置不能抬高它。

### Managed Pin

把某个宽松能力钳制为关闭的管理员策略。本文主要指 YOLO Pin。

### MCP

Model Context Protocol。MCP Tool 可能连接第三方系统，Permission 保留 Tool Name 与 Input 以判断风险。

### MCP Qualified ID

同时编码 Server 与 Tool 的规范名字。Server-wide Grant 必须从有效 Qualified ID 提取。

### Mode

Permission 的整体工作方式，例如 Ask、Auto、Always Approve。

### nono

项目使用的 OS Sandbox 抽象库，在不同平台接入 Landlock/Seatbelt。

### Opaque Shell

权限分析器无法可靠拆解其真实行为的 Shell 表达式。

### One-shot Channel

只能发送一个结果的异步通道，适合一次 Permission Request 对应一个最终 Decision。

### Pattern Mode

Permission Pattern 的解释方式，例如 Glob 或 Domain。

### Permission

对一次具体 Tool 访问进行语义授权的系统。

### Permission Event

包含 Tool、Access、Mode、Decision、Reason、Classifier 与 Subagent 信息的审计事件。

### Permission Handle

调用方持有的 Permission Manager 接口，可以请求批准、切换 Mode 或共享给 Child Session。

### Permission Manager

串行处理 Request、Policy、Auto、Prompt、Grant 与 Telemetry 的 Actor。

### Permission Rule DSL

用于书写 `Read(...)`、`Bash(...)`、`MCPTool(...)` 等规则的配置语言。

### Permission State

保存命令、Domain、MCP 等 Grant/Deny 的状态对象。

### Policy

由多来源 Permission Rule 编译出的确定性规则求值器。

### PolicyDeny

明确 Policy Deny 的特殊 Decision。它通常作为可解释错误反馈给模型，而不是混同用户取消。

### Pre-decision

在真正 Prompt 前，由 Safe Command、Grant、Allowlist 等得到的快捷结果。

### Profile

一组 Sandbox 文件读写、Deny 与网络约束。

### Prompt Outcome

用户在 Permission UI 中选择或输入的 Typed 结果。

### Prompt Policy

没有其他自动决定时如何处理：Ask、Deny 或 Auto。

### Protected Edit

对 Hook、Shell Startup、Settings 等可能影响后续执行的敏感文件编辑。

### Provenance

规则、Mode 或 Decision 的来源信息。安全合并不能仅看值，还要知道来源是否可信。

### RAII

资源随对象生命周期自动管理的 Rust 模式。`InFlightGuard` 和 `ResolvedOnDrop` 都利用 Drop 保证清理或补事件。

### Request Path Context

随单次权限请求携带的 Real CWD 与 Display CWD，用于正确解释相对路径。

### Requester Gone

等待 Decision 的请求方已经 Drop。Manager 会停止处理并记录 Cancelled。

### Rule Action

Permission Rule 的动作：Deny、Ask、Allow。

### Sandbox

OS/内核级能力隔离。它限制进程能做什么，不判断用户语义意图。

### Sandbox Active

Sandbox 内核规则已实际应用。只有这个状态能支持 Sandbox Auto Allow。

### Sandbox Auto Allow

在 Sandbox Active 且 Bash 风险分析允许时减少普通 Bash Prompt 的决策路径。

### Scope

批准或规则覆盖的范围，例如单次调用、命令前缀、Glob、Domain、MCP Tool、MCP Server、Session。

### Seatbelt

macOS Sandbox 机制，nono 在 macOS 上使用的底层约束。

### seccomp

Linux 系统调用过滤机制。项目用于限制已知 Child Process Launch Path 的网络系统调用。

### Segment

Shell 命令链中独立执行的一段命令。

### Session Grant

只在当前会话有效的批准，例如 Allow All Edits For Session。

### Shell File Gate

从 Bash 中识别文件读写并应用 Read/Edit Rule 的安全 Gate。

### Side Query

不改变主 Conversation 的辅助模型请求。Auto Permission Classifier 可通过它获取 Verdict。

### Skipped Permission

被识别但因解析错误、Managed Pin 或不可信来源而未加载的规则记录。

### Static Allowlist

代码内确定性定义的低风险 Tool/Access 集合。

### Tool Filter

Rule 适用的 Tool 类别，如 Read、Edit、Bash、MCP 或 Any。

### Tool Schema

模型请求中描述 Tool 名、参数与结构的 Function Definition。它不是授权凭证。

### Typed Provenance

使用枚举区分 System Requirements、Managed Settings、User Config 等来源，而不是通过路径字符串猜权限级别。

### YOLO

Always Approve 的历史/内部称呼。

---

## 56. 下一篇建议

沿着 Agent 基础能力继续深入，下一篇最值得写：

> 源码精读 16：Tool Execution Runtime 如何把 Permission Allow 变成真正的文件、终端、MCP 与 Web 执行，并统一 Timeout、Cancellation、Process Tree、Output Truncation、Artifact 与 Tool Result。

它会接住本文最后一个箭头：

```text
Permission Allow
      ↓
Tool Runtime 真正执行
      ↓
进程 / 文件 / MCP / Web
      ↓
流式更新、取消、错误、结果回填
```
