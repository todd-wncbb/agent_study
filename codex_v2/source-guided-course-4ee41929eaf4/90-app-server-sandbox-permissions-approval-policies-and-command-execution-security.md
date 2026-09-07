# 90：App-server Sandbox、权限配置、审批策略与命令执行安全链路

> 源码基线：4ee41929eaf4。本章解决“客户端选择的权限怎样从 JSON-RPC 参数进入 Core，审批为什么不能代替 Sandbox，以及一条模型提出的命令最终怎样在操作系统限制下启动”。

## 1. 本章解决什么问题

上一章解释了“需要问人时，App-server 怎样等待答案”。这一章继续追问：即使答案是“允许”，命令到底能读什么、写什么、是否能联网？

核心答案是：审批负责做决定，Sandbox 负责强制执行边界。

## 2. 资料边界

当前[官方 Sandbox 文档](https://learn.chatgpt.com/docs/sandboxing)、[Permissions 文档](https://learn.chatgpt.com/docs/permissions)、[Agent approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security)和[App Server 文档](https://learn.chatgpt.com/docs/app-server)提供产品合同。本章内部类型和分支以固定提交 4ee41929eaf4 为准。

当前官方文档可能晚于固定提交。遇到字段或平台实现差异时，不把今天的行为倒推成旧提交事实。

## 3. 先说人话：门卫和围墙

把审批想成门卫，把 Sandbox 想成围墙。

- 门卫回答“这次是否开门”。
- 围墙决定“开门以后能走到哪里”。
- 权限配置是围墙、门和通行区域的设计图。

门卫说“可以”不会让围墙自动消失。

## 4. 最重要的区分

```text
Approval policy：什么时候需要询问，能不能提出询问
Approvals reviewer：由谁回答这个询问
Permission/Sandbox policy：进程实际上能访问什么
Platform sandbox：操作系统怎样强制实现这些限制
```

四者属于同一条安全链，却不是同一个开关。

## 5. 为什么只靠模型自觉不够

模型可以被提示“不要读取密钥”，但提示是行为指导，不是操作系统权限。

真正的安全边界必须让一次错误、误解或恶意输入即使产生了危险命令，也无法轻易越过限制。

## 6. 为什么只靠每次弹窗也不够

如果每个 `ls`、`git status` 和测试命令都弹窗，用户会形成机械点击“允许”的习惯。

Sandbox 允许低风险操作在明确边界内自动运行，把注意力留给真正越界的动作。

## 7. 两条相互独立的轴

可以把配置画成二维坐标：

```text
                  审批：更常询问
                        ↑
  窄 Sandbox + 常询问   |   宽 Sandbox + 常询问
                        |
  窄 Sandbox + 不询问   |   宽 Sandbox + 不询问
                        +----------------------→ Sandbox：更宽
```

“不询问”不必然等于“全权限”，“全权限”也不必然等于“已获用户逐次同意”。

## 8. 一个反直觉组合

`read-only` 加 `never` 的意思不是全自动随便做，而是：只在只读边界内自动工作，越界时不弹审批，直接失败。

它适合只读分析或非交互 CI。

## 9. 真正的全访问组合

`danger-full-access` 去掉 Sandbox 边界；`never` 又不产生审批提示。

两个同时使用才接近“无 Sandbox、无审批”的危险全访问模式。

## 10. App-server 位于哪一层

App-server 不是 macOS Seatbelt、Linux sandbox 或 Windows sandbox 本身。

它接收客户端配置、建立 Thread/Turn 设置、把 Core 的审批请求转成 JSON-RPC，并把最终权限交给本地或远端执行链。

## 11. 端到端总图

```text
client thread/start 或 turn/start
        ↓
解析 approval / reviewer / sandbox / permissions
        ↓
建立或更新 Thread 的 sticky settings
        ↓
模型提出 shell/exec 工具调用
        ↓
Core 计算 ExecApprovalRequirement
        ↓
必要时 App-server 向 client 发审批 request
        ↓
得到允许、拒绝、取消或策略修订
        ↓
SandboxManager 选择平台 SandboxType
        ↓
把抽象权限转换成 wrapper argv、env 和平台策略
        ↓
spawn 子进程，收集输出、退出码、超时与 Sandbox 拒绝
```

## 12. 第一层输入：ThreadStartParams

固定提交中 `thread/start` 可以接收：

- `approvalPolicy`
- `approvalsReviewer`
- `sandbox`
- 实验字段 `permissions`

前三项是旧式兼容界面的一部分，`permissions` 选择命名权限配置。

## 13. thread/start 的 sandbox

`sandbox` 使用简化的 `SandboxMode`：

- `read-only`
- `workspace-write`
- `danger-full-access`

客户端只需选择模式，不必在这个字段里描述完整策略。

## 14. 第二层输入：TurnStartParams

固定提交中 `turn/start` 对应字段是：

- `approvalPolicy`
- `approvalsReviewer`
- `sandboxPolicy`
- 实验字段 `permissions`

注意 Thread 使用 `sandbox`，Turn 使用更完整的 `sandboxPolicy`，字段名和数据形状不同。

## 15. 为什么 Turn 是 sandboxPolicy

`SandboxPolicy` 不只是三个名字，它可以携带 `writableRoots`、`networkAccess` 和临时目录控制。

因此它比 `SandboxMode` 表达得更细。

## 16. Sticky 是什么意思

`TurnStartParams` 的注释明确说这些覆盖对“本 Turn 及后续 Turn”生效。

它不是只包住当前一次工具调用的临时变量，而是更新 Thread 的持续运行设置。

## 17. Sticky 不等于永久全局配置

Sticky 的作用域是当前加载的 Thread/Session 设置。

它不等于改写用户所有项目的 `config.toml`，也不自动改变别的 Thread。

## 18. sandbox 与 permissions 不能同时给

`thread/start` 的 `sandbox` 与 `permissions` 互斥；`turn/start` 的 `sandboxPolicy` 与 `permissions` 也互斥。

固定提交会返回 invalid request，而不是猜客户端更想要哪一个。

## 19. 为什么必须互斥

如果两个来源同时出现，可能一个说“workspace write”，另一个 profile 却 deny 某子目录。

默默选择其中一个会让客户端显示的权限和真正执行的权限不一致。

## 20. 错误示例

```json
{
  "method": "turn/start",
  "id": 7,
  "params": {
    "threadId": "thr-1",
    "input": [{"type": "text", "text": "运行测试"}],
    "sandboxPolicy": {"type": "workspaceWrite"},
    "permissions": "project-edit"
  }
}
```

固定提交应拒绝它，因为两个字段都试图定义执行边界。

## 21. 正确的旧式示例

```json
{
  "method": "turn/start",
  "id": 8,
  "params": {
    "threadId": "thr-1",
    "input": [{"type": "text", "text": "运行测试"}],
    "approvalPolicy": "on-request",
    "sandboxPolicy": {
      "type": "workspaceWrite",
      "writableRoots": [],
      "networkAccess": false
    }
  }
}
```

这条请求使用完整 legacy sandbox policy，没有再选择命名 profile。

## 22. 正确的新式示例

```json
{
  "method": "turn/start",
  "id": 9,
  "params": {
    "threadId": "thr-1",
    "input": [{"type": "text", "text": "运行测试"}],
    "approvalPolicy": "on-request",
    "permissions": "project-edit"
  }
}
```

这里 `project-edit` 是配置中已定义的 profile ID。

## 23. 命名 Permission Profile 解决什么问题

单个 `workspace-write` 名字难以表达“项目可写、`.env` 不可读、文档目录只读、只允许特定域名”。

命名 profile 把这些规则组合成可复用、可展示、可由组织约束的权限集合。

## 24. PermissionProfileSummary

`permissionProfile/list` 返回的摘要包含：

- `id`
- 可选 `description`
- `allowed`

`allowed: false` 表示 profile 存在，但有效 requirements 不允许当前用户选择它。

## 25. 为什么 list 需要 cwd

配置可以分层，并受项目目录影响。

同一个 profile ID 在不同工作目录下是否存在、是否被组织要求允许，可能需要针对 cwd 解析。

## 26. ActivePermissionProfile

Thread 响应可报告当前 profile 的：

- `id`
- 可选 `extends`

它提供来源信息，让客户端不必从兼容 SandboxPolicy 反推用户实际选择。

## 27. extends 是什么

一个命名 profile 可以基于父 profile 扩展。

`extends` 表示选中配置的父 profile 身份，不是说客户端应该自己重新实现继承算法。

## 28. 为什么需要兼容投影

旧客户端认识 `sandboxPolicy`，新客户端可能认识 `activePermissionProfile`。

服务端可以给旧客户端一个兼容视图，同时给新客户端准确的 profile provenance；兼容视图不应被误当成完整原始配置。

## 29. SandboxPolicy 的四种固定提交 variant

固定提交的 v2 `SandboxPolicy` 有：

- `dangerFullAccess`
- `readOnly`
- `externalSandbox`
- `workspaceWrite`

`externalSandbox` 表示进程已经处于外部 Sandbox，磁盘边界由外层承担，但仍携带网络设置。

## 30. ReadOnly

`readOnly` 禁止写入；固定提交的 wire shape 仍可携带 `networkAccess` 布尔值。

“只读”只描述文件系统写能力，不自动等同“无网络”。

## 31. WorkspaceWrite

`workspaceWrite` 在只读基础上允许当前工作区和额外 writable roots 写入。

它还携带网络开关以及是否排除 `$TMPDIR` 和 `/tmp` 的选项。

## 32. DangerFullAccess

`dangerFullAccess` 表示不施加这些 Sandbox 限制。

名字中的 `Danger` 是有意的风险提示，不是普通“高级模式”的中性名称。

## 33. ExternalSandbox

它适合宿主已经用容器或其他机制建立隔离的情况。

但“外层有 Sandbox”是部署合同；若外层实际上没有隔离，Codex 内层类型名本身不会凭空制造安全性。

## 34. Writable root 不是简单字符串前缀

Core 的 `WritableRoot` 同时带：

- root 路径
- root 下仍保持只读的子路径
- 受保护的 metadata 名称

所以“仓库可写”并不等于仓库内每个位置都可写。

## 35. 为什么 `.git` 等元数据要保护

修改 Git hooks 或 Agent 配置可能让未来的可信命令执行攻击者植入的代码。

安全边界必须考虑“现在写文件”怎样影响“以后执行程序”。

## 36. cwd 和 writable root 不是一个概念

`cwd` 是子进程开始运行的位置。

`writable root` 是它被允许修改的目录范围；设置 cwd 不应被理解为自动授予该目录之外的写权限。

## 37. runtimeWorkspaceRoots

固定提交还支持实验性的运行时 workspace roots 覆盖。

它影响当前 Thread 怎样解释工作区集合，路径必须是绝对路径，并且同样属于 sticky 设置。

## 38. 为什么要求绝对路径

相对路径的含义依赖解析时 cwd。

安全策略若在不同层以不同 cwd 解析同一字符串，可能得到不同目录；绝对路径减少这种歧义。

## 39. 特殊文件系统路径

权限协议支持：

- root
- minimal
- project roots
- tmpdir
- slash tmp
- unknown special path

它们是语义化位置，不只是某台机器上的硬编码字符串。

## 40. FileSystemAccessMode

每条文件系统规则的访问模式是：

- `read`
- `write`
- `deny`

`deny` 让宽范围授权中可以挖出更窄的禁止区域。

## 41. 最具体规则优先的直觉

例如整个 workspace 可写，但 `.env` deny。

判断 `.env` 时不能因为先看见 workspace write 就停止；更具体的 deny 必须覆盖更宽的 write。

## 42. deny 的重要性

只有 allow 列表往往难以表达“多数项目文件可改，但密钥、配置元数据和生成凭据不可碰”。

显式 deny 是最小权限设计的重要组成部分。

## 43. Network permissions

固定提交的附加网络权限主要通过 `enabled: Option<bool>` 表达。

不要把它和命令审批混为一谈：命令获准运行后，网络仍可能被网络 Sandbox 阻断。

## 44. Managed network

执行链可带 `NetworkProxy`，把出站访问交给受管理的网络路径。

这时 Sandbox 不一定只是简单“允许所有网络/禁止所有网络”，还要确保命令只能按受管路径访问。

## 45. 网络审批为何是单独语义

固定提交的审批 payload 可带 `networkApprovalContext`。

它说明被阻断的是目标 host/protocol 的网络访问；客户端不应把底层代理命令当成对用户有意义的普通 shell 预览。

## 46. 第二条安全链：审批策略

`AskForApproval` 固定提交包含：

- wire 值 `untrusted`，Rust variant 名 `UnlessTrusted`
- `on-request`
- 实验性的 `granular`
- `never`

Rust 名和 wire 名不总是一字不差。

## 47. UnlessTrusted / untrusted

它允许已知可信的只读命令自动运行，对不可信命令要求审批。

“可信”来自执行策略分类，不等于模型自己宣称命令安全。

## 48. OnRequest

Agent 先在当前 Sandbox 内工作；确实需要越界时，可以提出审批请求。

这是“默认有边界，必要时显式升级”的模式。

## 49. Never

它不是“永远批准”，而是“永远不询问”。

当动作超出当前权限时，应把失败返回模型，而不是自动把 Sandbox 拿掉。

## 50. Granular

固定提交的 granular policy 分别控制：

- sandbox approval
- rules approval
- skill approval
- request_permissions
- MCP elicitations

某项为 false 的含义是该类提示被自动拒绝，而不是偷偷放行。

## 51. ApprovalsReviewer

固定提交支持：

- `user`
- `auto_review`

旧值 `guardian_subagent` 可作为反序列化 alias 读入，但规范输出使用 `auto_review`。

## 52. Auto-review 不改变 Sandbox

它只把原本给人的审批交给一个专门 reviewer agent。

主 Agent 的文件、网络和 Sandbox 边界保持不变。

## 53. 一句判断法

问：“把 reviewer 从 user 改成 auto_review 后，这条命令能访问更多目录吗？”

答案应是不能；若能，说明实现把“谁决定”错误地混进了“边界是什么”。

## 54. 工具调用进入 Core

模型生成 shell/exec 参数后，Tool handler 先解析：

- command
- cwd
- timeout
- sandbox permissions 请求
- justification
- additional permissions

它不会一解析完就直接 `spawn`。

## 55. ExecApprovalRequirement

Orchestrator 首先计算当前调用的审批要求：

- `Skip`
- `Forbidden`
- `NeedsApproval`

这是决策结果，不是操作系统 Sandbox 类型。

## 56. Skip

表示当前策略允许不弹普通审批。

它仍可能在 Sandbox 内执行；“跳过审批”绝不等于“跳过 Sandbox”。

## 57. Forbidden

表示策略不允许这次操作或不允许提出相应审批。

Orchestrator 直接返回 rejected，不应继续尝试执行。

## 58. NeedsApproval

Orchestrator 构造精确 action 和 ApprovalContext，再通过 Session 请求审批。

App-server 将这个 Core 事件翻译成上一章学习的 Server Request。

## 59. 审批成功以后发生什么

`already_approved = true` 只记录决策已通过。

接下来仍要根据 permissions 和 sandbox preference 选择第一次执行的 Sandbox。

## 60. 三种每命令 SandboxPermissions

Shell 工具可表达：

- `use_default`
- `with_additional_permissions`
- `require_escalated`

这是单次命令的请求，不等于 Thread 的基础 profile。

## 61. use_default

使用 Turn 当前有效权限。

大多数普通命令应走这条路径。

## 62. with_additional_permissions

命令请求一个有限的额外文件系统或网络 overlay。

它适合“只让这次命令读取另一个目录”，比完全 unsandboxed 更窄。

## 63. require_escalated

命令请求无 Sandbox 执行。

这是更大的边界跨越，需要显式 justification，并受审批策略约束。

## 64. Additional permissions 不是任意自授

固定提交要求相关 feature 和合适审批策略。

模型把 `additional_permissions` 写进参数，只是提出请求，不代表权限已经生效。

## 65. 预批准与最终有效权限

经过审批的 overlay 会与基础 profile 组合为 effective permission profile。

真正交给 SandboxManager 的是解析后的有效 profile，而不是原始 JSON 文本。

## 66. 为什么 overlay 优于全局扩大

只为了下载一个依赖而把整个 Session 永久改成全网络访问，会放大后续所有命令的风险。

单命令、单目标、单作用域的能力更符合最小权限。

## 67. request_permissions 与 per-command overlay

二者都能请求额外能力，但生命周期入口不同：

- `request_permissions` 是内建工具发起的 Turn/Session grant。
- per-command overlay 绑定具体 exec 调用。

客户端 UI 不应只因它们都包含“permissions”就合并状态。

## 68. requested 与 granted 必须求交集

客户端只能授予请求集合的子集。

若请求只读 A，响应却写 A、读 B，最终不能因为响应更宽就获得那些额外能力。

## 69. 第一次执行尝试

Orchestrator 先根据权限、工具偏好和 managed network 判断是否需要 Sandbox。

需要时调用 `SandboxManager::select_initial`；不需要时使用 `SandboxType::None`。

## 70. SandboxablePreference

工具可声明：

- `Forbid`
- `Require`
- `Auto`

它表达工具和 Sandbox 的兼容偏好，不是用户审批决定。

## 71. Auto 怎样判断

`should_sandbox` 查看有效文件系统权限、网络权限和 managed network 要求。

只要这些限制需要平台强制，就应选择平台 Sandbox。

## 72. SandboxType

固定提交的具体执行类型包括：

- `None`
- macOS Seatbelt
- Linux sandbox 路径
- Windows restricted token 路径

协议层 `SandboxPolicy` 和执行层 `SandboxType` 不应混为一个 enum。

## 73. Policy 和 Type 的区别

Policy 描述“允许什么”。

Type 描述“在这台主机上用什么机制执行它”。同一份权限在不同 OS 上会选择不同 Type。

## 74. SandboxManager::transform

该函数把可移植的：

- program 与 args
- cwd 与 env
- permission profile
- managed network
- platform choice

转换成可真正启动的 `SandboxExecRequest`。

## 75. 为什么 transform 是安全边界

在它之前，命令仍是抽象请求；在它之后，argv 可能已被包成 `sandbox-exec` 或 Linux helper 调用。

安全审查要确认所有执行路径都经过等价转换，不能存在一个“方便的旁路 spawn”。

## 76. macOS 路径

固定提交在 macOS 为 Seatbelt 构造策略参数，再把真实命令包装进 `/usr/bin/sandbox-exec`。

文件系统和网络规则被翻译为 Seatbelt 能执行的策略。

## 77. Linux 路径

固定提交的 Linux `SandboxType` 名仍是 `LinuxSeccomp`，并通过 Codex Linux sandbox helper 构造命令。

阅读固定提交时应以其 manager、helper 参数和 feature 分支为准，不用今天文档中的最新实现名字覆盖它。

## 78. Windows 路径

Windows 使用 restricted token 或 elevated backend 相关分支，并额外解析文件系统 overrides。

Windows 的进程 token、路径规则和代理设置不同于 Unix，不能只测试 Linux 后宣称跨平台安全成立。

## 79. None 的含义

`SandboxType::None` 表示这一执行路径没有使用 Codex 平台 Sandbox wrapper。

它可能来自全访问策略、外部 Sandbox 合同或已批准的 unsandboxed 重试；必须结合上游原因判断，不能只看 enum。

## 80. Spawn 前的路径转换

固定提交尽量让编排和 transport 保持 `PathUri`，到主机执行边界才转成本机绝对路径。

转换失败会产生 invalid request 或 Sandbox transform error，而不是随意猜测路径。

## 81. Spawn 真正做什么

`spawn_child_async` 最终设置：

- program 和 args
- cwd
- 清理后的 env
- stdin/stdout/stderr 策略
- Unix 进程组和父进程死亡处理
- kill-on-drop

Sandbox wrapper 已经在更早的 transform 阶段进入 command。

## 82. 子进程继承边界

官方合同强调，Agent 启动的 Git、包管理器和测试 runner 都继承相同 Sandbox 边界。

否则只限制第一层 shell、却让它的子进程无限制访问，会使 Sandbox 失去意义。

## 83. env_clear 的意义

固定提交用 `env_clear` 后只设置准备好的环境。

这让子进程环境更可控，但哪些变量被保留仍由上游环境构建逻辑决定，不能把它当成自动“无秘密环境”。

## 84. stdin 为什么可能是 null

Shell tool 通常不应让命令无限等待交互输入。

将 stdin 设为 null 既减少挂起，也避免某些工具因为检测到 stdin 而改变行为。

## 85. 输出和权限是两回事

stdout/stderr capture、输出上限、timeout 和 cancellation 控制资源生命周期。

它们不授予文件或网络权限，但同样属于安全执行链，防止命令无限输出或永久占用进程。

## 86. 第一次尝试被 Sandbox 拒绝

失败并不自动意味着“去掉 Sandbox 再跑一次”。

Orchestrator 会检查审批策略、工具是否允许 escalation、文件系统是否允许 unsandboxed，以及是否是可识别的 managed network denial。

## 87. 为什么不能自动重试无 Sandbox

否则 Sandbox 只会变成一次探测：危险命令先失败，然后系统自动以全权限完成它。

这等于没有安全边界。

## 88. OnRequest 下的重试

当策略允许、工具允许且错误属于可审批越界时，系统构造新的审批理由。

用户或 reviewer 批准后，才进入相应的升级重试路径。

## 89. Never 下的失败

`never` 要求不弹审批。

Sandbox denial 应回到模型，由模型寻找边界内替代方案，而不是把“不能问”解释成“默认同意”。

## 90. Network denial 的特殊处理

如果受管网络返回可识别的 policy decision，系统可建立目标明确的 network approval context。

若 payload 不足以安全识别目标，固定提交倾向保留拒绝，而不是展示含糊的全局网络批准。

## 91. 已审批为什么有时还要再次审批

第一次审批可能只覆盖“在 Sandbox 内运行这条命令”。

命令实际运行后才发现必须脱离 Sandbox；严格 auto-review 下，这个新风险边界需要新的 reviewer 判断。

## 92. Approval cache

`acceptForSession` 或策略修订可以减少后续重复询问。

但 cache 命中只能影响审批决策，不应直接绕过最终有效 permission profile 和平台 Sandbox 构造。

## 93. thread/shellCommand 是重要例外

当前 App-server 合同明确说明 `thread/shellCommand` 是用户主动发起的 shell command，在 Sandbox 外运行，并且不继承 Thread Sandbox policy。

客户端必须把它和模型工具调用清楚区分，不能复用一个看不出权限差异的“运行命令”按钮。

## 94. 为什么用户主动命令仍需显眼标识

“用户点的”解释了授权来源，却没有降低命令本身的破坏力。

UI 应展示 cwd、命令和无 Sandbox 性质，避免用户误以为它受 Agent 工作区边界保护。

## 95. App-server 自身 transport 也是安全边界

若未经授权的远端客户端能连接 App-server，它可能调用高权限 API 或回答审批请求。

因此 WebSocket bind、认证和连接身份安全不能被 Sandbox 章节忽略。

## 96. Sandbox 不能解决什么

Sandbox 不能自动解决：

- 已允许读取的数据被发送到已允许网络
- 用户主动批准危险命令
- 外层 Sandbox 配置与声明不一致
- App-server transport 被未授权方控制
- 宿主内核或 Sandbox 实现漏洞

安全性来自多层组合，不来自一个万能开关。

## 97. Approval 不能解决什么

审批不能证明用户理解了命令，也不能保证批准后命令只做预览中看起来的事。

脚本、构建系统和依赖都可能执行间接行为，所以批准后仍需最小 Sandbox。

## 98. Permission Profile 不能解决什么

声明式 profile 只有被正确解析、传递并由平台强制执行才有效。

如果某个执行路径丢失 profile 或直接调用裸 `spawn`，配置文件写得再精细也没有用。

## 99. 三层验证法

审查一项权限功能时分别验证：

1. Wire：客户端发送和接收什么。
2. Decision：Core 如何计算审批和有效权限。
3. Enforcement：平台最终怎样限制真实子进程。

只测试其中一层不够。

## 100. Wire 层测试什么

- kebab-case 与 camelCase 是否正确
- 实验字段是否需要 capability
- `sandboxPolicy` 与 `permissions` 是否互斥
- 绝对路径是否被验证
- 旧 alias 是否只宽读、不继续宽写

## 101. Decision 层测试什么

- `never` 是否 fail closed
- granular false 是否拒绝而非允许
- additional permissions 是否需要审批
- requested/granted 是否求交集
- session cache 是否只匹配正确 action

## 102. Enforcement 层测试什么

- 允许目录真能读写
- deny 子路径真被拒绝
- 网络关闭时子进程和孙进程都不能直连
- wrapper/helper 缺失时是否报错
- macOS、Linux、Windows 是否各有原生覆盖

## 103. 不要只测试错误字符串

更强的测试是实际尝试创建文件、读取受限文件或连接测试目标，并检查结构化结果。

错误文字可能变化，边界行为才是合同核心。

## 104. 一个完整示例：工作区内运行测试

假设 profile 允许读依赖工具链、写项目 workspace、禁止网络。

`cargo test` 无需越界时：审批 requirement 可为 Skip，SandboxManager 仍选择平台 Sandbox，测试及子进程都在边界内运行。

## 105. 示例中的常见误解

看到“没有弹窗”不要推断“命令以全权限运行”。

恰恰可能是 Sandbox 已经让这项工作在预先批准的边界内安全完成。

## 106. 一个完整示例：下载缺失依赖

测试命令尝试联网，受管网络阻断目标 host。

Core 识别 network denial，生成网络专用审批；批准后只按允许的网络能力重试，而不是默认开放整个文件系统。

## 107. 一个完整示例：写相邻仓库

命令需要修改 workspace 外的 sibling repo。

更好的做法是添加窄 writable root 或请求单次 filesystem overlay，而不是切换 `dangerFullAccess`。

## 108. 一个完整示例：never 模式

同样的 sibling repo 写入在 `never` 下被 Sandbox 拒绝。

Agent 应改为生成 patch、说明所需权限，或在现有 workspace 内完成替代工作；它不会弹窗，也不会自动越界。

## 109. 一个完整示例：auto_review

在 `on-request + auto_review + workspace-write` 下，普通 workspace 测试直接运行。

越界写请求交给 reviewer；即使 reviewer 批准，执行仍只获得该路径对应的批准能力，基础 Sandbox 不会因为 reviewer 是 Agent 而消失。

## 110. 配置排错：为什么明明允许仍失败

依次检查：

1. 选中的 profile 是否被 requirements 允许。
2. Turn 是否后来 sticky override 了 profile。
3. cwd/workspace roots 是否是预期路径。
4. 更具体 deny 是否覆盖宽 allow。
5. 平台 Sandbox/helper 是否可用。
6. 错误是文件、网络、进程启动还是命令自身失败。

## 111. 配置排错：为什么没有弹审批

可能原因包括：

- 动作本来就在 Sandbox 内
- `approvalPolicy` 是 `never`
- granular 对该提示类别为 false
- 命中已有 session approval
- 当前工具不允许 escalation
- 请求在到达 UI 前已被 Turn 转换取消

“没有弹窗”不能单独证明 bug。

## 112. 配置排错：为什么批准后仍被拒绝

可能批准的是命令执行，但没有批准所需网络或文件 overlay；也可能平台边界比预览更窄。

先区分审批结果、effective permission profile 和最终 Sandbox denial，不能只看一个 `accept`。

## 113. 配置排错：为什么 profile 选择被拒绝

显式设置更新会检查加载配置的 startup warning。

若组织 requirements 不允许该 profile，固定提交将设置请求作为 invalid request 拒绝，而不是悄悄接受后再换成别的 profile。

## 114. 观测日志应记录什么

建议记录低敏感度结构化信息：

- thread/turn/item/request ID
- profile ID 和来源
- approval requirement 与 decision source
- SandboxType
- denial category
- 是否发生升级重试
- duration、timeout、exit status

不要默认记录秘密环境变量和完整敏感命令输出。

## 115. 安全不变量一

`Skip approval` 不推出 `SandboxType::None`。

这是阅读 Orchestrator 时最值得反复检查的不变量。

## 116. 安全不变量二

`Never` 不推出 `Approved`。

不能询问的越界请求应失败。

## 117. 安全不变量三

客户端 grant 不得比 request 更宽。

最终能力必须限制在 request 与 grant 的交集内。

## 118. 安全不变量四

Thread 的 `sandbox`/`sandboxPolicy` 与命名 `permissions` 不得同时成为权威来源。

互斥拒绝比隐式优先级安全。

## 119. 安全不变量五

抽象权限必须一直传到最终 process launch。

中间的协议转换、exec-server transport 或平台 wrapper 不能把 profile 丢掉。

## 120. 安全不变量六

Sandbox denial 后的无 Sandbox 重试必须是显式、可审计的升级路径。

它不能隐藏在普通 retry 中。

## 121. 安全不变量七

审批者身份不改变原始 Sandbox 边界。

user 和 auto_review 只改变 decision routing。

## 122. 安全不变量八

受保护元数据和 deny 子路径必须覆盖更宽 writable root。

否则表面的最小权限会被策略优先级破坏。

## 123. 阅读源码的推荐顺序

先读协议类型，再读 Turn settings builder，然后读 Orchestrator，最后读 SandboxManager 和 spawn。

不要一上来钻进 Seatbelt policy 字符串，否则很容易失去“这个策略从哪里来”的全局链路。

## 124. 第一步：看协议形状

打开：

- `app-server-protocol/src/protocol/v2/shared.rs`
- `app-server-protocol/src/protocol/v2/permissions.rs`
- `app-server-protocol/src/protocol/v2/thread.rs`
- `app-server-protocol/src/protocol/v2/turn.rs`

先列出 wire 字段和 enum variant。

## 125. 第二步：看设置怎样进入 Thread

打开 `app-server/src/request_processors/turn_processor.rs`，寻找 `build_thread_settings_overrides`。

重点看互斥校验、profile 加载、requirements warning 和 preview validation。

## 126. 第三步：看审批决策

打开：

- `core/src/exec_policy.rs`
- `core/src/tools/orchestrator.rs`
- `core/src/tools/handlers/shell.rs`

把 requirement、approval request 和 first attempt 分成三个阶段标注。

## 127. 第四步：看平台选择

打开 `sandboxing/src/manager.rs`。

追踪 `should_sandbox`、`select_initial`、`transform`，观察抽象 profile 在不同 OS 分支怎样变成命令。

## 128. 第五步：看到真正 spawn

打开 `core/src/exec.rs` 和 `core/src/spawn.rs`。

确认 wrapper argv、cwd、env、stdio、timeout、kill-on-drop 和输出收集在哪里接上。

## 129. 动手练习一：画两条轴

为以下组合写出“会不会询问”和“能实际做什么”：

- read-only + never
- read-only + on-request
- workspace-write + on-request
- danger-full-access + never

不要用“严格/宽松”一个词同时代替两条轴。

## 130. 动手练习二：设计最小 profile

目标：允许修改当前项目源码，允许读取工具链，禁止读取 `.env`，禁止网络。

写出规则后，再列出三个正例和三个拒绝例；不要只写配置不写行为 oracle。

## 131. 动手练习三：追踪一次命令

选择 `cargo test`，从 JSON tool args 开始记录：

- approval requirement
- effective profile
- SandboxType
- transform 后 argv
- spawn cwd/env
- 最终 exit status

每一项都写出 owner 类型。

## 132. 动手练习四：失败矩阵

分别模拟：

- profile 不允许
- 互斥输入
- Sandbox helper 缺失
- 文件系统 deny
- managed network deny
- approval decline
- approval cancel
- timeout

判断失败发生在 wire、decision 还是 enforcement 层。

## 133. 理解检查

如果你能不看文档回答以下问题，就掌握了主干：

1. `never` 为什么不是“永远批准”？
2. Auto-review 为什么不扩大 writable roots？
3. `sandboxPolicy` 和 `permissions` 为什么互斥？
4. 审批通过后为什么仍需 SandboxManager？
5. policy、profile、SandboxType 分别是什么？
6. Sandbox denial 为什么不能自动无 Sandbox 重试？

## 134. 源码检查点

- `codex-rs/app-server-protocol/src/protocol/v2/shared.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/permissions.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/turn.rs`
- `codex-rs/app-server/src/request_processors/thread_processor.rs`
- `codex-rs/app-server/src/request_processors/turn_processor.rs`
- `codex-rs/app-server/src/request_processors/thread_summary.rs`
- `codex-rs/core/src/exec_policy.rs`
- `codex-rs/core/src/tools/orchestrator.rs`
- `codex-rs/core/src/tools/handlers/shell.rs`
- `codex-rs/core/src/tools/handlers/mod.rs`
- `codex-rs/core/src/tools/network_approval.rs`
- `codex-rs/core/src/exec.rs`
- `codex-rs/core/src/spawn.rs`
- `codex-rs/sandboxing/src/manager.rs`
- `codex-rs/sandboxing/src/seatbelt.rs`
- `codex-rs/sandboxing/src/bwrap.rs`
- `codex-rs/app-server/tests/suite/v2/command_exec.rs`

## 135. 本章词汇表

| 术语 | 字面含义 | 本章中的具体意思 |
|---|---|---|
| Sandbox | 沙箱 | 用操作系统机制限制子进程文件、网络等访问的执行边界 |
| Approval | 审批 | 对一次越界或受控动作作出允许、拒绝或取消决定 |
| Approval policy | 审批策略 | 决定哪些动作可直接运行、哪些能询问、哪些直接拒绝 |
| Approvals reviewer | 审批审查者 | 回答合格审批请求的用户或auto-review Agent |
| Auto-review | 自动审查 | 用专门Reviewer Agent替代人工回答，不改变Sandbox |
| Permission profile | 权限配置 | 可复用的文件系统、网络和平台执行权限集合 |
| Active permission profile | 活跃权限配置 | 当前Thread实际选择的profile ID及父profile来源 |
| SandboxMode | 沙箱模式 | thread/start使用的read-only/workspace-write/full-access简化选择 |
| SandboxPolicy | 沙箱策略 | 带writable roots、network等参数的legacy执行边界描述 |
| SandboxType | 沙箱类型 | 当前OS真正采用的None、Seatbelt、Linux或Windows执行后端 |
| Enforcement | 强制执行 | 让权限规则对真实进程产生不可仅靠程序意愿绕过的限制 |
| Writable root | 可写根目录 | 允许写入的根及其只读子路径、受保护元数据规则 |
| Protected metadata | 受保护元数据 | `.git`、`.codex`等可影响未来可信执行的特殊位置 |
| Deny | 拒绝规则 | 在更宽read/write范围中明确禁止访问的更具体规则 |
| CWD | Current Working Directory | 子进程启动时的当前目录，不自动等于全部授权范围 |
| Workspace root | 工作区根 | 当前任务认定的项目边界，可参与权限规则展开 |
| Sticky override | 粘性覆盖 | 从本Turn起继续影响同一Thread后续Turns的设置更新 |
| Legacy | 旧式兼容 | 为旧客户端/旧配置保留但不再是最精确表达的接口 |
| Provenance | 来源信息 | 当前有效profile来自哪个ID以及extends哪个父profile |
| UnlessTrusted | 除非可信 | wire值untrusted对应的Rust variant，只自动运行可信集合 |
| Granular approval | 细粒度审批 | 分别控制Sandbox、rules、skill、permission和MCP提示类别 |
| ExecApprovalRequirement | 执行审批要求 | Core计算出的Skip、Forbidden或NeedsApproval |
| Skip | 跳过 | 不需要普通审批；并不代表无Sandbox |
| Forbidden | 禁止 | 当前策略不允许动作或不允许发起该类审批 |
| NeedsApproval | 需要审批 | 执行前必须得到有效decision |
| use_default | 使用默认 | 单命令使用Turn当前有效permissions |
| with_additional_permissions | 带附加权限 | 对单命令请求有限文件/网络overlay |
| require_escalated | 请求升级 | 对单命令请求无Sandbox执行的高风险路径 |
| Overlay | 覆盖层 | 叠加在基础profile上的窄范围临时能力 |
| Effective permission profile | 有效权限配置 | 继承、工作区、grant和overlay解析后真正交给执行层的profile |
| Least privilege | 最小权限 | 只给当前任务所需的最少能力、最窄路径和最短作用域 |
| Managed network | 受管网络 | 通过受控代理和策略处理的出站网络路径 |
| Network approval context | 网络审批上下文 | 描述被阻断host/protocol的专用审批语义 |
| SandboxManager | 沙箱管理器 | 判断是否需Sandbox、选择平台类型并转换执行请求的组件 |
| Transform | 转换 | 把可移植command/profile变成平台wrapper argv和执行参数 |
| SandboxExecRequest | 沙箱执行请求 | 已完成平台转换、准备进入主机launch边界的数据 |
| Seatbelt | 安全带 | macOS的原生Sandbox机制 |
| Seccomp | Secure Computing | Linux系统调用过滤机制；固定提交Linux SandboxType名字的一部分 |
| Restricted token | 受限令牌 | Windows限制进程权限的一类执行机制 |
| Wrapper | 包装程序 | 在真实命令外层启动并施加Sandbox策略的程序/helper |
| Spawn | 生成进程 | 用最终program、argv、env和cwd真正创建子进程 |
| Fail closed | 封闭失败 | 解析、审批或Sandbox异常时默认拒绝而非扩大权限 |
| Escalation | 权限升级 | 从现有边界请求更高权限或无Sandbox重试 |
| Approval cache | 审批缓存 | 对匹配后续动作复用的session决定，不代替Sandbox |
| External sandbox | 外部沙箱 | Codex假定隔离由容器或宿主外层提供的执行合同 |
| Wire layer | 线协议层 | JSON-RPC字段、tag、命名和兼容输入所在层 |
| Decision layer | 决策层 | Core计算审批要求和有效权限所在层 |
| Enforcement layer | 强制层 | 平台Sandbox与进程启动真正落实权限所在层 |
