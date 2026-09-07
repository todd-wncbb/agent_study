# Sandbox approval lifecycle：一条命令为什么直接运行、询问、失败或升级

> 源码基线：`4ee41929eaf4`  
> 本篇重点：approval policy、exec policy、permission profile、平台 Sandbox、Guardian、审批缓存、首次尝试、沙箱拒绝识别与升级重试。  
> 建议先读：[工具分派](05-tool-dispatch.md)、[Unified exec](06-unified-exec.md)和主题章[执行环境、审批与 Sandbox](../12-execution-environments.md)。

## 1. 先说人话：这里其实有两道不同的问题

看到 Codex 执行命令时，初学者很容易把下面两件事混成一件：

1. 这条命令要不要先得到批准？
2. 这条命令获准以后，可以接触哪些文件和网络？

第一问是 **approval（审批）**，第二问是 **sandbox / permission（沙箱与权限）**。

可以把它们想成进入实验室：

- 审批像门卫判断“你能不能进去”；
- 沙箱像实验室内部的隔离柜，决定“进去以后能碰什么”；
- 门卫放行，不代表隔离柜消失；
- 不弹门卫窗口，也不代表命令拥有整台电脑的权限。

因此，这四种组合都可能存在：

| 是否询问 | 是否在 Sandbox 内 | 白话含义 |
|---|---|---|
| 不询问 | 是 | 策略允许直接尝试，但仍由 OS 边界限制 |
| 询问 | 是 | 动作本身需同意；同意后仍只获得受限权限 |
| 询问 | 否 | 用户同意一次明确的越界执行 |
| 不询问 | 否 | 环境本来就 unrestricted，或明确规则信任该命令 |

本篇最重要的一句话是：

> **批准的是动作；Sandbox 约束的是动作真正执行时的能力。两者有关联，但不是同一个开关。**

## 2. 官方公开概念与固定源码边界

OpenAI 的公开说明把两个控制面分开：

- [Sandboxing](https://learn.chatgpt.com/docs/sandboxing)描述文件系统和网络的技术边界；
- [Agent approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security)描述 Agent 在什么情况下暂停并请求授权。

公开文档还特别说明：`never` 的含义是“不弹审批”，不是“关闭 Sandbox”；full access 才会绕过审批和 Sandbox，因此风险完全不同。

本篇精读的是固定提交 `4ee41929eaf4`。公开产品的名称或默认值以后可能变化，所以：

- 当前用户界面与配置方式，以最新官方文档为准；
- 本篇的枚举名、调用顺序、失败行为，以固定源码为准。

固定源码中的 Rust 变体叫 `AskForApproval::UnlessTrusted`，序列化给配置时叫 `untrusted`。看到两个名字，不要误以为它们是两套策略。

## 3. 贯穿案例：三个看起来相似的命令

假设工作区是：

```text
/workspace/project
```

当前文件系统策略允许读取大部分文件，只允许写工作区；网络默认关闭。

### 案例 A：只搜索工作区

```bash
rg "TODO" src
```

它通常可以：

```text
不询问 → 进入 Sandbox → 成功 → 返回输出
```

这里“不询问”不是因为 `rg` 获得了全盘权限，而是可以先让 Sandbox 安全地限制它。

### 案例 B：写工作区外的目录

```bash
cp report.txt /some/outside/directory/report.txt
```

普通首次尝试可能是：

```text
不询问 → 进入 Sandbox → OS 拒绝写入 → 返回 SandboxDenied
```

在固定版本的 `on-request` 策略下，这次调用通常不会立即自己弹出“重试吗”。模型收到失败后，若认为越界确有必要，应发起新的显式升级调用：

```text
sandbox_permissions = require_escalated
justification = "需要把用户要求的报告复制到指定目录"
```

然后才是：

```text
请求审批 → 批准 → 首次就绕过普通 Sandbox → 执行
```

### 案例 C：普通程序自己的错误

```bash
python build_report.py
```

若脚本因为语法错误退出，这不是 Sandbox 拒绝：

```text
进入 Sandbox → Python SyntaxError → 普通命令失败 → 不升级
```

Codex 不能把“一切失败”都解释成权限不足，否则代码 bug 会被错误地拿去提权执行。

## 4. 从模型参数到 `ShellRequest`

shell 工具的入口在：

```text
core/src/tools/handlers/shell.rs
```

`ShellCommandHandler::handle_call` 大致完成：

1. 解析模型传来的 JSON 参数；
2. 取得当前 Turn 的 primary environment；
3. 解析 `cwd`；
4. 读取可选 `prefix_rule`；
5. 转成内部 `ExecParams`；
6. 调用 `run_exec_like`。

参数中的关键字段可以先这样理解：

| 字段 | 含义 |
|---|---|
| `command` | 真正要交给 shell 的命令文本 |
| `workdir` / `cwd` | 命令从哪个目录开始运行 |
| `timeout` | 最长等待时间 |
| `sandbox_permissions` | 本次调用是否请求普通权限或升级权限 |
| `justification` | 为什么需要升级，供审批者判断 |
| `prefix_rule` | 建议未来把哪段命令前缀写成允许规则 |

这里还没有执行命令。它只是在准备一份结构化请求。

## 5. `run_exec_like` 是进入安全链路的门

`run_exec_like` 不是简单调用 `std::process::Command`。它先做几件重要工作：

```text
sticky turn permissions
        ↓
规范化 additional permissions
        ↓
检查显式 escalation 是否符合 approval policy
        ↓
识别 shell 中的 apply_patch 特例
        ↓
用 ExecPolicyManager 计算审批要求
        ↓
构造 ShellRequest
        ↓
ToolOrchestrator::run
```

注意最后一步：真正统一“审批 → Sandbox → 执行 → 必要时重试”的对象是 `ToolOrchestrator`。

## 6. 为什么显式升级要在很早的位置检查

模型可以请求：

```text
SandboxPermissions::RequireEscalated
```

但“模型请求”不是“系统已经授权”。`run_exec_like` 会检查：

- 这份权限是否已经由 sticky turn permissions 预批准；
- 当前审批策略是否允许模型主动请求升级。

若没有预批准，而且策略不是 `OnRequest`，固定源码会拒绝这个显式升级形式。

这条检查的目的不是判断命令最终安全与否，而是限制“谁可以主动发起哪类审批流程”。随后 exec policy、Hook、Guardian 或用户仍可能继续判断。

## 7. 第一条决策线：`ExecPolicyManager`

`ExecPolicyManager::create_exec_approval_requirement_for_command` 收到：

```rust
command
approval_policy
permission_profile
windows_sandbox_level
sandbox_permissions
prefix_rule
```

它输出的不是 `true/false`，而是三态：

```rust
enum ExecApprovalRequirement {
    Skip { bypass_sandbox, proposed_execpolicy_amendment },
    NeedsApproval { reason, proposed_execpolicy_amendment },
    Forbidden { reason },
}
```

### `Skip`

不需要为这次动作询问。

但还要继续看 `bypass_sandbox`：

- `false`：不询问，但仍按权限选择 Sandbox；
- `true`：显式 allow 规则信任了所有解析出的命令段，第一次可绕过 Sandbox。

### `NeedsApproval`

必须得到审批以后才能继续。它可能附带：

- 人能读懂的 `reason`；
- 可选的 `proposed_execpolicy_amendment`。

### `Forbidden`

不允许执行，也不会继续弹一个本来就不允许出现的审批框。

## 8. 为什么不是一个布尔值

若只返回 `needs_approval: bool`，至少会丢失三类信息：

1. “不用问但仍要 Sandbox”和“不用问且可绕过 Sandbox”的区别；
2. 被策略禁止和等待批准的区别；
3. 本次批准与“以后同前缀都允许”的区别。

三态加字段让调用方不必重新猜测 exec policy 的意图。

## 9. 命令会先被拆成多个片段

策略不是总拿整个 shell 字符串做粗糙前缀匹配。例如：

```bash
cd /workspace/project && cargo test -p codex-core
```

解析后可以包含：

```text
["cd", "/workspace/project"]
["cargo", "test", "-p", "codex-core"]
```

这很重要，因为复合命令中只要有一个片段需要询问或禁止，整体就不能因为另一个安全片段而被放行。

复杂 heredoc 等无法可靠完整解析时，源码仍保留兼容的 fallback 匹配，但不会轻率地自动推导持久化 amendment。

## 10. 显式 exec policy 规则优先于启发式默认

策略检查会汇总：

- 用户或管理员写下的 prefix rules；
- 内建的安全/危险命令启发式；
- 未匹配命令在当前 approval policy 和 Sandbox 下的 fallback。

最终决策可能是：

```text
Forbidden > Prompt > Allow
```

白话上可以理解为：一条复合命令不能用一个允许片段抵消另一个禁止片段。

## 11. `untrusted` / `UnlessTrusted`

固定源码中的含义是：

- 已知安全、只读的命令可以自动允许；
- 其他未匹配命令通常请求审批；
- 危险命令也请求审批，除非更强规则直接禁止。

例如 `rg`、读取类命令可能走安全清单；未知脚本通常需要问。

名称中的 trusted 不是说“这个人可信吗”，而是说“这条命令是否落在系统认可的安全集合或显式规则中”。

## 12. `on-request`

`OnRequest` 的核心思想是：在受限文件系统中，普通、非危险、未请求升级的命令先交给 Sandbox 运行，不必为每条命令都打断用户。

固定源码对未匹配命令大致这样判断：

```text
危险命令                         → Prompt
Restricted + 普通权限            → Allow，依赖 Sandbox
Restricted + 请求 sandbox override → Prompt
Unrestricted / ExternalSandbox   → Allow
```

所以 `on-request` 不是“所有命令都由模型任意决定是否弹窗”。exec policy 仍会阻止危险或冲突动作。

## 13. `never`

`Never` 表示：不要请求用户批准。

它不表示：

- 所有命令都允许；
- 所有命令都在无 Sandbox 环境运行；
- 危险命令可以绕过强制规则。

在受限环境中，普通命令可以直接进 Sandbox；若某个决定必须 Prompt，`Never` 与它冲突，结果会变成 `Forbidden`，而不是悄悄批准。

因此：

```text
never = 不问，能在现有边界内做就做，不能就失败
```

## 14. `granular`

`GranularApprovalConfig` 把不同类别的提示拆开：

```text
sandbox_approval
rules
skill_approval
request_permissions
mcp_elicitations
```

本篇最相关的是前两个：

- `sandbox_approval`：能否显示 Sandbox/升级类审批；
- `rules`：能否显示由 exec policy `prompt` 规则触发的审批。

某类设为 `false` 的意思是自动拒绝那类请求，不是自动批准。

## 15. `prompt_is_rejected_by_policy`

同样是 `Decision::Prompt`，来源不同：

- 显式 policy rule 要求 Prompt；
- Sandbox 或启发式要求 Prompt。

`prompt_is_rejected_by_policy` 会据此检查 granular 开关。若 Prompt 来自规则，就看 `rules`；否则看 `sandbox_approval`。

这样管理员可以允许普通提权请求，同时禁止 Agent 建议修改长期规则；也可以反过来配置。

## 16. `proposed_execpolicy_amendment` 是什么

审批框有时不仅能“只允许这一次”，还可以接受一个建议：

```text
以后允许以 ["cargo", "test"] 开头的同类命令
```

源码用 `ExecPolicyAmendment { command: Vec<String> }` 表示这个前缀。

若用户选择 `ApprovedExecpolicyAmendment`：

1. `session::handlers::exec_approval` 收到决定；
2. `Session::persist_execpolicy_amendment` 调用 manager；
3. `append_amendment_and_update` 写入默认 policy 文件；
4. 同时更新内存 policy；
5. 未来匹配命令可能直接 `Skip`。

这和 `ApprovedForSession` 不一样：前者持久化规则，后者只是当前 Session 的审批缓存。

## 17. `ToolOrchestrator::run` 的全景

现在进入本篇主函数。把网络细节暂时折叠后，它可以写成：

```text
计算 ExecApprovalRequirement
        │
        ├─ Forbidden ───────────────→ 返回拒绝
        ├─ NeedsApproval ───────────→ 请求审批
        └─ Skip ────────────────────→ 记录策略允许
                                      │
                                      ▼
决定第一次是否使用 Sandbox
                                      │
                                      ▼
执行第一次 attempt
        │
        ├─ 成功 ────────────────────→ 返回结果
        ├─ 普通错误/超时/信号 ──────→ 返回原错误
        └─ SandboxErr::Denied
                   │
                   ▼
          检查是否允许升级
                   │
          ┌────────┴────────┐
          │否               │是
          ▼                 ▼
       返回拒绝          必要时再审批
                             │
                             ▼
                       第二次 attempt
```

关键是：第二次 attempt 不是所有错误的通用 retry。

## 18. 第一步：取得实际执行环境

Orchestrator 从 tool runtime 取得：

```rust
let environment = tool.turn_environment(req);
let workspace_roots = environment.workspace_roots();
let permission_profile = environment.permission_profile();
let permissions = environment.permission_profile_with_workspace_roots();
```

这里区分两份 permission profile：

- canonical profile：尚未把本机 workspace roots 具体化，可传给远端 exec-server；
- materialized profile：已经结合当前环境工作区，供本机 Sandbox 使用。

这能避免远端执行时把本机绝对路径错误地当成远端路径。

## 19. 第二步：确定审批要求

shell runtime 已经通过 `ExecPolicyManager` 提供自定义 requirement。其他工具若没有提供，则走：

```rust
default_exec_approval_requirement(approval_policy, file_system_sandbox_policy)
```

默认逻辑和 shell 的命令级逻辑不是一回事。默认逻辑不知道命令内容，只能根据审批策略和文件系统是否 Restricted 做较粗判断。

## 20. 第三步：处理 `Skip / NeedsApproval / Forbidden`

### `Forbidden`

立即返回 `ToolError::Rejected`，命令一次都不会启动。

### `NeedsApproval`

runtime 构造 `ApprovalAction`，Orchestrator 构造 `ApprovalContext`，然后调用：

```rust
session.request_approval(action, approval_ctx).await?
```

成功后设置：

```text
already_approved = true
```

这个布尔值只表示本次 Orchestrator 流程前面已经获得授权，不是 Session 级缓存。

### `Skip`

普通情况下记录“由配置批准”，不询问用户。

但开启 `strict_auto_review` 时，即使 exec policy 给出 `Skip`，也仍要送 Guardian 审查。它是一层额外的强制审查，而不是普通 policy 的别名。

## 21. 审批先经过 Hook

`Session::request_approval` 的优先级在源码注释中写得很明确：

```text
1. Permission request Hooks
2. StrictAutoReview / Guardian，或者 User
```

Hook 可以：

- `Allow`：直接形成 `Approved`；
- `Deny`：直接形成拒绝；
- 不给决定：继续交给 reviewer。

因此 Hook 不是“用户点完批准以后才执行的通知”，而是审批决策链中更早的一层。

## 22. Reviewer 是 Guardian 还是用户

若 `strict_auto_review` 为真，强制选择 Guardian。

否则 `ApprovalReviewer::for_turn` 根据 Turn 配置决定：

```text
Guardian enabled/routed → Guardian
其他                   → User
```

Guardian 是自动审查者。它收到结构化动作、审批原因和重试原因，返回允许、拒绝、超时等结果。

这里的“自动审查”仍然是一种审批来源；它不等于 Sandbox，也不改变 OS 权限机制。

## 23. 审批结果并不只有同意和拒绝

`ReviewDecision` 的主要变体是：

| 变体 | 含义 |
|---|---|
| `Approved` | 只批准当前请求 |
| `ApprovedForSession` | 当前 Session 内相同审批 key 可复用 |
| `ApprovedExecpolicyAmendment` | 批准并持久化一条命令前缀规则 |
| `NetworkPolicyAmendment` | 为某个 host 保存网络 allow/deny 规则 |
| `Denied { rejection }` | 拒绝动作，但 Session 可继续 |
| `TimedOut` | 自动审查未及时给出决定，按拒绝处理 |
| `Abort` | 拒绝并中断当前任务，等待下一次用户命令 |

`ApprovalResolution::into_tool_result` 把 Denied、TimedOut、Abort 和网络 deny 转成 `ToolError::Rejected`。只有允许类决定才能继续执行。

## 24. `ApprovedForSession` 到底缓存什么

shell 的 `ApprovalKey` 包含：

```text
environment_id
canonicalized command
cwd
sandbox_permissions
additional_permissions
```

所以批准：

```bash
cargo test
```

并不自然等于批准：

```bash
cargo test && curl example.com
```

也不自然等于批准同一命令在另一个 cwd、另一个执行环境或另一组额外权限下运行。

缓存 key 越精确，越不容易把一次窄授权错误扩大。

## 25. 缓存只保存 `ApprovedForSession`

`with_cached_approval` 的规则是：

1. 所有 key 都已是 `ApprovedForSession`，才跳过提示；
2. 若本次结果正是 `ApprovedForSession`，才写入 store；
3. 普通 `Approved` 不写入 Session cache；
4. Denied 也不当成永久拒绝缓存。

`apply_patch` 可能一次触及多个文件，所以它有多个 key；只有全部命中才能整体跳过。

## 26. 第四步：选择第一次是否绕过 Sandbox

`sandbox_override_for_first_attempt` 主要看三件事：

1. 当前策略有没有 denied-read restrictions；
2. exec policy 是否返回 `Skip { bypass_sandbox: true }`；
3. 本次是否明确 `RequireEscalated`。

结果只有：

```rust
NoOverride
BypassSandboxFirstAttempt
```

`NoOverride` 不一定表示“肯定启用 Sandbox”，只是说“不要在这里强行绕过”；接下来还要让 `SandboxManager::should_sandbox` 根据 permission profile、runtime preference 和 managed network 判断。

## 27. 为什么 denied-read 会阻止无 Sandbox 执行

假设策略是：

```text
可以写工作区
但绝对不能读取 /secret/keys
```

denied-read 只能由 Sandbox 强制执行。若“批准升级”以后完全拿掉 Sandbox，命令反而能读取秘密路径，等于审批意外消除了一个更重要的限制。

所以：

```rust
unsandboxed_execution_allowed(policy)
```

只有在没有 denied-read restrictions 时才返回真。

当显式 `RequireEscalated` 遇到 denied-read 时，`sandbox_permissions_preserving_denied_reads` 会退回 `UseDefault`，继续在 Sandbox 中执行，而不是静默放开读取。

这是一个典型的安全不变量：

> **升级某种权限，不能顺便丢掉另一条拒绝规则。**

## 28. `SandboxManager::should_sandbox`

每个 runtime 有 `SandboxablePreference`：

| 值 | 含义 |
|---|---|
| `Auto` | 根据 permission profile 和 managed network 决定 |
| `Require` | 必须请求 Sandbox |
| `Forbid` | 不套平台 Sandbox |

shell runtime 使用 `Auto`。

`Auto` 会把文件系统策略、网络策略和 managed network requirements 交给 `should_require_platform_sandbox`。它决定的是“政策上需要不需要 Sandbox”，还不是当前操作系统最终能提供哪种实现。

## 29. 平台 Sandbox 类型

`SandboxManager::select_initial` 选择：

| 平台 | `SandboxType` |
|---|---|
| macOS | `MacosSeatbelt` |
| Linux | `LinuxSeccomp` |
| Windows 且 backend 启用 | `WindowsRestrictedToken` |
| 无可用实现或无需 Sandbox | `None` |

名称描述的是启动包装方式。真正的权限内容仍来自 `PermissionProfile`，不是看到 `MacosSeatbelt` 就能知道具体可读写路径。

## 30. `sandbox_requested` 与 `sandbox` 为什么都要保存

`SandboxAttempt` 同时有：

```rust
sandbox: SandboxType
sandbox_requested: bool
```

因为某个 host 可能政策上需要 Sandbox，但平台没有对应实现，于是：

```text
sandbox_requested = true
sandbox = None
```

前者记录政策意图，后者记录实际 wrapper。把两者混成一个值，会掩盖“想限制但宿主无法提供实现”的差异。

## 31. `SandboxAttempt` 是一次执行的完整安全上下文

它保存：

- 选择的 `SandboxType`；
- 本地 materialized permissions；
- 远端用 canonical permissions；
- sandbox cwd 和 workspace roots；
- managed network 是否强制；
- Linux sandbox executable 与 Landlock 选择；
- Windows sandbox level 与 private desktop；
- 网络拒绝取消 token；
- 当前 attempt 使用的 network proxy。

因此 attempt 不是简单的“第几次重试”编号，而是一份可执行的安全配置。

## 32. `SandboxManager::transform` 做什么

runtime 先构造普通 `SandboxCommand`：

```text
program + args + cwd + env + additional permissions
```

`SandboxAttempt::env_for` 再调用 `SandboxManager::transform`，把它变成宿主实际能启动的请求。

大致效果：

```text
macOS   → /usr/bin/sandbox-exec + 生成的 Seatbelt profile + 原命令
Linux   → codex-linux-sandbox + Landlock/seccomp/bwrap 参数 + 原命令
Windows → restricted-token 准备 + 原命令
None    → 原命令
```

这里才接近真正的 OS 执行边界。

## 33. 第一次 attempt 怎样运行

`ToolOrchestrator::run_attempt` 先准备可能存在的 managed network approval，然后调用：

```rust
tool.run(req, &attempt_with_network_approval, &attempt_tool_ctx).await
```

对 shell 而言，`ShellRuntime::run` 会：

1. 解析 shell；
2. 保留 denied-read 约束；
3. 根据是否升级选择 managed network；
4. 构造 `SandboxCommand`；
5. 通过 `attempt.env_for` 转换；
6. 调用执行后端；
7. 收集 exit code、stdout、stderr 和 duration。

## 34. 成功路径最简单

若第一次得到 `Ok(out)`：

```text
返回 out
保留可能 deferred 的 network approval
不再审批
不再重试
```

系统不会因为命令“看起来重要”而在成功后补问一次。

## 35. 普通非零退出不是 Sandbox denial

命令退出码非零，可能是：

- 测试断言失败；
- 编译错误；
- 参数写错；
- 文件本来不存在；
- Python/Node 程序抛异常。

这些应该作为正常工具输出交给模型分析，而不是升级权限。

只有执行层把错误包装为：

```rust
CodexErr::Sandbox(SandboxErr::Denied { ... })
```

Orchestrator 才考虑升级分支。

## 36. 怎样猜测“很可能被 Sandbox 拒绝”

`is_likely_sandbox_denied` 是启发式判断，不是数学证明。

它先排除：

- `SandboxType::None`；
- exit code 为 0。

然后搜索 stdout、stderr、aggregated output 中的常见词：

```text
operation not permitted
permission denied
read-only file system
seccomp
sandbox
landlock
failed to write file
```

Linux 上还识别 seccomp 的 `SIGSYS` 退出形态。

反过来，shell misuse、permission denied、command not found 常见退出码 `2 / 126 / 127` 若没有关键词，不会单凭退出码就判断为 Sandbox denial。

## 37. 为什么这个识别必须保守

假阳性会造成：程序自身失败，却请求无 Sandbox 重跑。

假阴性会造成：真实权限问题被作为普通失败返回，模型需要重新判断并显式请求升级。

安全系统通常宁愿接受第二种麻烦，也不应因模糊错误自动扩大权限。因此代码同时要求：

- 确实使用了某种 Sandbox；
- 命令失败；
- 输出或信号符合已知拒绝特征。

## 38. Timeout 和 Signal 不进入升级分支

`SandboxErr` 还可能是：

```text
Timeout
Signal
Denied
```

Orchestrator 只对 `Denied` 展开升级逻辑。

- Timeout 可能是程序太慢或死锁；
- Signal 可能是崩溃或被取消；
- 它们不自动说明需要更多文件权限。

## 39. 进入升级前还有四道门

即使得到 `SandboxErr::Denied`，仍要检查：

1. runtime 的 `escalate_on_failure()` 是否允许；
2. 当前 approval policy 是否允许请求无 Sandbox 执行；
3. denied-read restrictions 是否允许真正无 Sandbox；
4. 若是 managed network 拒绝，是否能提取有效的 host/protocol 上下文。

任何一道失败，原始拒绝都直接返回。

## 40. `wants_no_sandbox_approval` 的固定版本行为

默认实现是：

| approval policy | 沙箱失败后能否由本次调用请求无 Sandbox 重试 |
|---|---|
| `UnlessTrusted` | 可以 |
| `Never` | 不可以 |
| `OnRequest` | 默认不可以 |
| `Granular` | 取决于 `sandbox_approval` |

这正是最容易凭名字猜错的地方。

`OnRequest` 支持模型在**新调用中显式**请求 `RequireEscalated`；它不等于当前普通调用失败后自动弹窗。

源码对受管网络拒绝有一个窄例外，但网络审批包含 host 级策略、即时/延迟完成等额外状态，本篇只标出分叉，不把它冒充为普通文件系统升级。

## 41. `UnlessTrusted` 为什么可能只问一次

设一个未知命令在执行前得到 `NeedsApproval`：

```text
先询问 → 用户批准 → already_approved = true
                  ↓
              Sandbox 首次执行
                  ↓
              SandboxDenied
```

随后 `should_bypass_approval(policy, already_approved)` 看到已经批准，会避免再问完全相同动作一次；若没有 network approval context，可直接进行升级重试。

这里“只问一次”的理由是：用户在执行前已经批准了这条命令；不是因为 Session cache 必然命中。

## 42. Strict auto-review 为什么可能审两次

strict auto-review 的第一次 Guardian 审查只覆盖 Sandbox 内的 attempt。

若 Sandbox 拒绝、接下来准备无 Sandbox 重试，风险边界已经改变，所以源码明确不复用第一次判断：

```text
Guardian 批准 Sandbox attempt
        ↓
SandboxDenied
        ↓
Guardian 再审无 Sandbox retry
```

这说明“命令文本相同”不代表“动作风险相同”；执行权限也是动作的一部分。

## 43. 第二次 attempt 如何选择 Sandbox

源码先计算：

```rust
unsandboxed_allowed = !has_denied_read_restrictions
```

若允许真正无 Sandbox：

```text
retry_sandbox = None
Linux sandbox executable = None
```

若不能无 Sandbox，但这是可批准的网络上下文，仍可能保留文件系统 Sandbox，只调整网络授权。

所以“escalated retry”不总等于“所有保护全部关闭”。它只放宽被批准的那条边界。

## 44. 第二次失败不会无限循环

Orchestrator 只有：

```text
initial attempt
optional retry attempt
```

第二次失败会直接返回，不会继续第三、第四次自动提权。

有界重试避免：

- 错误分类导致无限循环；
- 反复弹审批；
- 同一副作用命令被不可控地多次执行。

## 45. 完整案例一：`on-request` 下安全只读命令

输入：

```bash
rg "ToolOrchestrator" codex-rs/core/src
```

假设没有显式规则，命令不危险，文件系统 Restricted。

时间线：

```text
ShellCommandHandler
  → ExecPolicy fallback = Allow
  → ExecApprovalRequirement::Skip { bypass_sandbox: false }
  → 不询问
  → SandboxManager 选择平台 Sandbox
  → sandbox-exec / codex-linux-sandbox / Windows restricted token
  → rg 成功
  → 返回输出
```

结论：直接运行与全权限没有任何必然关系。

## 46. 完整案例二：`on-request` 下显式升级

模型发起：

```json
{
  "command": "cp report.txt /outside/report.txt",
  "sandbox_permissions": "require_escalated",
  "justification": "用户要求把报告复制到工作区外的目标目录"
}
```

时间线：

```text
run_exec_like 检查：OnRequest 允许发起这种请求
  → ExecPolicy fallback 看到 sandbox override
  → NeedsApproval
  → Hook
  → Guardian 或 User
  → Approved
  → sandbox_override_for_first_attempt = BypassSandboxFirstAttempt
  → SandboxType::None
  → 执行一次
```

这里没有“先失败再重试”，因为调用本身已经明确请求了升级。

## 47. 完整案例三：`on-request` 普通尝试碰壁

模型没有显式升级：

```json
{
  "command": "cp report.txt /outside/report.txt"
}
```

时间线：

```text
ExecPolicy = Skip { bypass_sandbox: false }
  → 不询问
  → Sandbox 内执行
  → read-only file system / operation not permitted
  → 包装成 SandboxErr::Denied
  → wants_no_sandbox_approval(OnRequest) = false
  → 原拒绝返回模型
```

模型下一步应解释失败，并在确有必要时重新发起案例二那种显式升级调用。

## 48. 完整案例四：`untrusted` 的未知命令

输入：

```bash
./company-build-tool generate-report
```

时间线可能是：

```text
未知命令，不在 known-safe 集合
  → NeedsApproval
  → 用户 Approved
  → 仍在 Sandbox 内执行
  → 若成功，结束
  → 若明确 SandboxDenied
       → already_approved = true
       → 可跳过重复询问
       → 无 Sandbox 第二次执行
```

这里第一次批准后仍在 Sandbox 内，是“审批不等于拿掉 Sandbox”的直接证据。

## 49. 完整案例五：`never`

输入普通构建命令：

```bash
cargo check -p codex-core
```

可能路径：

```text
不要求 Prompt
  → Skip
  → Sandbox 内运行
  → 成功就返回
  → SandboxDenied 就返回，不向用户升级
```

输入危险命令时，启发式要求 Prompt，但 `Never` 不允许 Prompt，于是变成 `Forbidden`。

这比把 `Never` 理解为“自动点击批准”安全得多，也准确得多。

## 50. 完整案例六：显式 allow prefix rule

假设 exec policy 已有：

```text
prefix_rule(["company-ci", "status"], decision="allow")
```

输入：

```bash
company-ci status --job 123
```

若每个解析出的命令片段都被显式 allow rule 覆盖：

```text
Decision::Allow
  → Skip { bypass_sandbox: true }
  → 第一次就 SandboxType::None
```

注意：内建启发式给出的 Allow 不一定设置 `bypass_sandbox=true`。源码只在所有片段都由真正的 policy allow match 覆盖时才把它视为可绕过 Sandbox 的显式信任。

## 51. 拒绝以后发生什么

若 Hook、Guardian 或用户拒绝：

```text
ToolError::Rejected(reason)
```

命令不会启动。

`Denied` 通常让当前 Turn 继续，模型可尝试安全替代方案；`Abort` 则会触发 `interrupt_task`，等用户下一条消息。

因此 UI 中两个看起来都像“拒绝”的按钮，控制流语义可能不同。

## 52. Justification 的作用与边界

`justification` 会进入审批 action，帮助 reviewer 理解为什么需要额外权限。

它不是：

- 权限令牌；
- 自动批准条件；
- 可以覆盖管理员 policy 的文字咒语；
- 真正传给 shell 执行的参数。

好的 justification 应说明“需要访问什么边界、为什么任务离不开它”，而不是只写“请批准”。

## 53. CWD 为什么是审批 key 的一部分

同样的相对命令：

```bash
./deploy.sh
```

在两个 cwd 下可能对应完全不同的文件。因此 canonical command 相同也不能自动复用审批。

环境 ID 同理：本地容器与远端开发机上的同名命令，不应共享窄授权。

## 54. Additional permissions 不等于全权限

`AdditionalPermissionProfile` 可以为本次命令增加指定读写根或网络能力。

它更像：

```text
在原 permission profile 上增加一小块允许区域
```

而不是：

```text
完全关闭 Sandbox
```

它也进入 approval key，避免批准窄权限以后被更宽的请求复用。

## 55. Managed network 是另一条审批子链

文件系统 Sandbox 与受管网络会在同一个 attempt 中汇合，但网络有独立上下文：

```text
host
protocol
network policy decision
immediate / deferred approval
```

Orchestrator 若看到 Sandbox denial 带 `network_policy_decision`，会尝试构造 `NetworkApprovalContext`。若 payload 存在却无法得到有效上下文，它会 fail closed，直接返回拒绝。

这避免系统在不知道目标 host 的情况下弹出含糊的“允许网络”请求。

## 56. 为什么升级时可能不再使用 managed proxy

`managed_network_for_sandbox_permissions` 对 `RequireEscalated` 返回 `None`。

原因是显式升级调用走的是另一种权限边界，不能一边宣称绕过普通 Sandbox，一边无意继续套用与普通 attempt 相同的代理语义。

但若 denied-read 必须保留，系统仍可保持文件系统 Sandbox，只对已识别的网络 host 处理授权。两条轴仍然不能混为一谈。

## 57. 本地与远端执行为什么共享 Orchestrator

`SandboxAttempt` 同时携带：

- 本机已经 materialize workspace roots 的 permissions；
- exec-server 可理解的 canonical permissions。

本地 runtime 可以调用 `SandboxManager::transform`；远端路径则把命令和 Sandbox context 交给 exec-server，在真正执行的 host 上再解释路径和平台能力。

核心原则是：

> 策略决策可以在 Core 编排，但 OS 路径和 Sandbox wrapper 必须在真正执行命令的机器上落地。

## 58. 状态与副作用时间表

| 阶段 | 只改内存/发事件 | 可能产生外部副作用 |
|---|---|---|
| 参数解析 | 是 | 否 |
| exec policy 评估 | 是 | 读取 policy 已在此前完成；此处不执行命令 |
| 请求审批 | 有 callback/事件 | 用户或 Guardian 决策；命令仍未执行 |
| 接受 amendment | 更新内存 policy | 会追加 policy 文件 |
| Sandbox transform | 构造启动请求 | 通常尚未运行目标命令 |
| first attempt | 收集进程状态 | 是，目标命令已执行 |
| denial 分类 | 分析已有输出 | 否 |
| retry approval | callback/事件 | 命令尚未第二次执行 |
| retry attempt | 收集进程状态 | 是，命令第二次执行 |

这个表提醒我们：批准 amendment 和执行命令是两个独立副作用。

## 59. 为什么重试有重复副作用风险

第一次 Sandbox attempt 可能在被拒绝前已经完成一部分操作。例如：

```bash
generate-local-file && copy-to-outside
```

第一段可能成功，第二段才被 Sandbox 拒绝。无 Sandbox 重试整个复合命令时，第一段会再次执行。

因此设计或调用命令时，应尽量：

- 让可重试步骤幂等；
- 把纯工作区步骤与越界步骤拆开；
- 避免把不可重复的外部副作用放在可能被拒绝的步骤之前。

Orchestrator 提供的是安全边界重试，不是事务回滚。

## 60. 常见误解纠正

### 误解一：没弹窗就是 full access

错误。最常见路径正是“不弹窗但在 Sandbox 内运行”。

### 误解二：用户点了批准，Sandbox 一定消失

错误。`NeedsApproval` 后的第一次执行仍可能在 Sandbox 内。

### 误解三：`never` 等于全部自动批准

错误。必须询问的动作会被禁止，普通动作仍依赖现有 Sandbox。

### 误解四：任何 exit code 非零都会提权重试

错误。只有结构化的 `SandboxErr::Denied` 才进入相关分支。

### 误解五：`permission denied` 一定是 Sandbox

错误。文件自身 Unix 权限、远端认证或程序逻辑也可能产生相似文字；源码只能保守启发式判断。

### 误解六：Approve for session 会批准所有 shell

错误。它按环境、规范化命令、cwd 和权限组合缓存。

### 误解七：规则 amendment 只是本次 Session 缓存

错误。接受 amendment 会尝试持久化到 policy 文件并更新内存规则。

### 误解八：升级会删除全部限制

错误。denied-read 约束不能因升级被静默丢弃；网络和文件系统也可分别处理。

## 61. 关键不变量

### 不变量一：模型只能提出动作

模型参数不直接等于权限；policy、Hook、reviewer 和 runtime 共同决定执行。

### 不变量二：禁止态不会伪装成等待审批

`Forbidden` 立即拒绝，避免向用户展示一个即使点击也不应生效的选择。

### 不变量三：无 Sandbox 不能丢失 denied-read

若无法保留拒绝读取规则，就不能使用普通无 Sandbox 路径。

### 不变量四：只有 Sandbox denial 才能触发 Sandbox 升级

普通错误、超时和信号不冒充权限问题。

### 不变量五：自动升级最多一次

第二次失败终止流程，不形成无界重试。

### 不变量六：Session 审批缓存按精确动作作用

命令、cwd、环境或权限变化都会形成不同 key。

### 不变量七：Strict reviewer 要审变化后的风险边界

Sandbox attempt 的批准不自动覆盖无 Sandbox retry。

## 62. 测试怎样证明这些解释

固定提交中的测试分成几层。

### `core/src/tools/sandboxing_tests.rs`

直接验证：

- Restricted + OnRequest 的默认 approval requirement；
- ExternalSandbox 下跳过默认审批；
- granular 禁止 sandbox prompt 时变成拒绝；
- explicit escalation 会让第一次 bypass Sandbox；
- denied-read 会阻止显式 escalation 和 policy bypass。

### `core/tests/suite/approvals.rs`

覆盖大规模审批矩阵，以及：

- apply_patch 的 session cache；
- execpolicy amendment 持久化并跳过未来提示；
- subagent 与 parent 的 amendment 传播；
- allow prefix 在 shell fork 下无 Sandbox 执行；
- active permission profile 的继承；
- 网络 retry 仍保留 denied-read Sandbox；
- 复合命令中一个不安全片段仍要求审批。

### `core/tests/suite/exec_policy.rs`

覆盖：

- 强制危险命令在 granular 下被解释性拒绝或请求批准；
- exec policy 阻止 shell；
- Windows 无可用 Sandbox backend 时更保守的行为；
- 空或空白 shell 输入不崩溃。

### `core/tests/suite/guardian_review.rs`

覆盖 Guardian Session 复用、拒绝理由、取消与超时/中断边界。

### `sandboxing/src/denial.rs` 与 `core/src/exec_tests.rs`

验证哪些输出、退出码和平台信号会或不会被分类为 Sandbox denial。

## 63. 推荐源码阅读顺序

第一遍只看总控：

1. `core/src/tools/handlers/shell.rs` 的 `run_exec_like`
2. `core/src/tools/orchestrator.rs` 的 `ToolOrchestrator::run`
3. `core/src/tools/runtimes/shell.rs` 的 `ShellRuntime`

第二遍拆审批：

4. `core/src/exec_policy.rs` 的 `create_exec_approval_requirement_for_command`
5. 同文件的 `render_decision_for_unmatched_command`
6. `core/src/tools/approvals.rs` 的 `Session::request_approval`
7. `core/src/tools/sandboxing.rs` 的 `with_cached_approval`

第三遍拆执行边界：

8. `core/src/tools/sandboxing.rs` 的 `sandbox_override_for_first_attempt`
9. `sandboxing/src/manager.rs` 的 `SandboxManager`
10. `sandboxing/src/denial.rs` 的 `is_likely_sandbox_denied`
11. `core/src/exec.rs` 把输出包装成 `SandboxErr::Denied` 的位置

最后再读测试，不要先陷入庞大的 scenario matrix。

## 64. 理解检查

### 问题 1

为什么 `Skip` 中还要有 `bypass_sandbox`？

<details>
<summary>参考答案</summary>

因为“不需要审批”和“不需要 Sandbox”不是同一结论。`Skip { bypass_sandbox: false }` 允许直接在受限环境尝试；只有明确规则信任所有命令片段等情况才设为 true。

</details>

### 问题 2

为什么普通 `Approved` 不写入 Session cache？

<details>
<summary>参考答案</summary>

它只表达用户同意当前动作。只有明确选择 `ApprovedForSession`，系统才有授权把相同 key 的未来请求自动放行。

</details>

### 问题 3

为什么 Python 语法错误不会触发无 Sandbox 重试？

<details>
<summary>参考答案</summary>

它是程序自身错误，不是结构化 `SandboxErr::Denied`。自动提权既解决不了语法问题，又会无意义地扩大权限。

</details>

### 问题 4

为什么 denied-read 存在时不能简单拿掉 Sandbox？

<details>
<summary>参考答案</summary>

拒绝读取路径依赖 Sandbox 强制。拿掉它会在批准某项升级时意外授予原本明确禁止的读取权限。

</details>

### 问题 5

`OnRequest` 的普通调用在 Sandbox 中失败，为什么固定源码通常直接返回，而不是当场询问？

<details>
<summary>参考答案</summary>

默认 `wants_no_sandbox_approval(OnRequest)` 为 false。OnRequest 的主要模型是：普通调用先受限运行；若模型确认需要越界，再发起新的 `RequireEscalated` 调用并提供理由。

</details>

### 问题 6

为什么 strict auto-review 在重试前可能需要 Guardian 再审一次？

<details>
<summary>参考答案</summary>

第一次批准的是 Sandbox 内动作；第二次准备无 Sandbox，风险边界改变。严格审查不能仅凭命令文字相同就复用旧决定。

</details>

## 65. 动手练习

### 练习一：给四条命令画二维表

为下面命令分别写出“是否审批”和“是否 Sandbox”两个结论，不要只写“允许/拒绝”：

```text
rg TODO src
未知的 ./build-tool
显式 require_escalated 的 cp
被 prefix allow rule 完整覆盖的 company-ci status
```

### 练习二：模拟 key 是否命中

先批准：

```text
environment=local, cwd=/a, command=cargo test, permissions=default
```

再分别改变 cwd、environment、command 和 permissions，判断 `ApprovedForSession` 是否还能命中。

### 练习三：区分三类失败

为以下输出分类：普通失败、Timeout/Signal、可能 SandboxDenied。

```text
SyntaxError: invalid syntax
operation not permitted
command not found, exit 127
terminated by timeout
read-only file system
```

### 练习四：固定提交源码搜索

```bash
git grep -n 'create_exec_approval_requirement_for_command' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'pub async fn run' 4ee41929eaf4 -- codex-rs/core/src/tools/orchestrator.rs
git grep -n 'sandbox_override_for_first_attempt' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'is_likely_sandbox_denied' 4ee41929eaf4 -- codex-rs
git grep -n 'with_cached_approval' 4ee41929eaf4 -- codex-rs/core/src
```

## 66. 本篇局部术语表

| 名词 / 代码词 | 中文理解 | 本篇中的具体含义 |
|---|---|---|
| approval | 审批 / 授权确认 | 在动作执行前由策略、Hook、Guardian 或用户决定是否放行 |
| approval policy | 审批策略 | 决定哪些类别能直接运行、询问或自动拒绝 |
| Sandbox | 沙箱 / 隔离执行环境 | 用 OS 机制限制文件系统、网络和进程能力 |
| permission profile | 权限档案 | 描述可读写路径、网络和其他执行权限的结构化配置 |
| file system sandbox policy | 文件系统沙箱策略 | permission profile 中专门描述文件访问边界的部分 |
| `AskForApproval` | 是否请求审批 | 固定源码中的 Never、OnRequest、UnlessTrusted、Granular 枚举 |
| `UnlessTrusted` | 除非已信任 | 配置序列化名为 `untrusted`；已知安全只读命令可直接运行，其他通常询问 |
| `OnRequest` | 按请求审批 | 普通动作先受限运行，显式越界请求再审批 |
| `Never` | 从不询问 | 不弹窗；能在现有边界内运行就运行，否则拒绝或失败 |
| `Granular` | 细粒度审批 | 分别控制 sandbox、rules、skill 等审批类别 |
| exec policy | 命令执行策略 | 对解析出的命令前缀给出 allow、prompt 或 forbidden 决策 |
| prefix rule | 前缀规则 | 按 argv 开头的一组 token 匹配命令的规则 |
| heuristic | 启发式 | 根据已知安全命令、危险模式和输出关键词作保守判断 |
| `ExecApprovalRequirement` | 执行审批要求 | Orchestrator 使用的 Skip、NeedsApproval、Forbidden 三态结果 |
| `Skip` | 跳过审批 | 不需要询问；是否绕过 Sandbox 还看 `bypass_sandbox` |
| `NeedsApproval` | 需要审批 | reviewer 允许以后才能启动命令 |
| `Forbidden` | 禁止 | 策略不允许执行，不展示无效审批机会 |
| `bypass_sandbox` | 绕过沙箱 | 第一次 attempt 是否不使用普通平台 Sandbox |
| sandbox override | 沙箱覆盖请求 | 本次命令请求改变默认 Sandbox 行为 |
| `RequireEscalated` | 要求升级权限 | 模型显式声明当前命令需要超出默认 Sandbox 的权限 |
| escalation | 权限升级 | 从受限尝试转为经批准的更宽权限尝试 |
| justification | 升级理由 | 给 reviewer 的人类可读说明，不是权限本身 |
| amendment | 规则修订 | 批准后可追加到 exec policy 的命令前缀 allow 规则 |
| `ApprovedForSession` | 本 Session 批准 | 相同精确 approval key 在当前 Session 后续可复用 |
| approval key | 审批缓存键 | 环境、规范化命令、cwd、Sandbox 权限和额外权限的组合 |
| canonicalize | 规范化 | 把等价命令表示整理成稳定的缓存比较形式 |
| Hook | 钩子 | reviewer 之前运行、可允许或拒绝权限请求的扩展逻辑 |
| Guardian | 自动审查者 | 根据结构化动作与理由自动给出审批结果的 reviewer |
| strict auto-review | 严格自动审查 | 即使普通 policy 为 Skip 也强制 Guardian 审查的模式 |
| reviewer | 审批者 | Guardian 或用户，负责对请求给出 ReviewDecision |
| `ReviewDecision` | 审批决定 | Approved、Denied、Abort、TimedOut 等结果联合类型 |
| attempt | 一次执行尝试 | 带一份确定 Sandbox/permission/network 上下文的运行 |
| initial attempt | 首次尝试 | Orchestrator 第一次启动目标命令 |
| retry attempt | 重试尝试 | 明确 Sandbox denial 且允许升级后最多执行的一次重试 |
| `SandboxAttempt` | 沙箱尝试上下文 | 记录平台类型、权限、cwd、roots、网络和平台开关的数据对象 |
| `SandboxManager` | 沙箱管理器 | 判断是否需要沙箱、选择平台实现并转换启动命令 |
| `SandboxType::None` | 无平台沙箱 wrapper | 实际启动请求未套 Seatbelt/Linux/Windows wrapper；不单独说明其他外部边界 |
| Seatbelt | macOS 沙箱机制 | 固定源码在 macOS 选择的 Sandbox 类型 |
| seccomp | Linux 系统调用过滤 | Linux Sandbox 组合中的一项机制 |
| Landlock | Linux 文件访问限制 | Linux Sandbox 用于限制文件系统访问的机制 |
| restricted token | 受限令牌 | Windows 上降低进程权限的 Sandbox backend |
| materialize | 具体化 | 把抽象 workspace roots 转成当前 host 的实际路径权限 |
| canonical permissions | 规范权限 | 尚未绑定本机路径、适合传给远端执行端的权限描述 |
| denied-read restriction | 禁止读取限制 | 明确不可读的路径；不能因无 Sandbox 升级被丢弃 |
| managed network | 受管网络 | 通过代理和 policy 对目标 host/protocol 作控制与审批的网络路径 |
| network approval context | 网络审批上下文 | host 与 protocol 等足够让 reviewer 判断的结构化信息 |
| fail closed | 失败时保持拒绝 | 无法确认安全上下文时不猜测放行 |
| `SandboxErr::Denied` | 沙箱拒绝错误 | 执行层确认或启发式判断命令很可能被 Sandbox 阻止 |
| false positive | 假阳性 | 普通程序错误被误判成 Sandbox denial |
| false negative | 假阴性 | Sandbox denial 被当成普通程序失败 |
| idempotent | 幂等 | 重复执行不会产生额外不同副作用 |
| side effect | 副作用 | 写文件、发网络请求、修改外部状态等可观察变化 |

## 67. 最后压缩成一句话

Sandbox approval lifecycle 的真正主线不是“危险命令弹窗、安全命令直接跑”，而是：

> shell handler 先把模型请求和 Turn 权限整理成结构化动作，exec policy 将它归类为跳过审批、需要审批或禁止，Hook 与 reviewer 解决“是否允许”，SandboxManager 再解决“在什么边界中执行”；首次执行只有在被结构化识别为 Sandbox 拒绝、策略允许且不会丢失 denied-read 等限制时，才会经过必要审批进行一次有界升级重试。

下一篇计划精读 managed network approval：一个 `curl` 请求怎样经过代理策略、host 级审批、即时/延迟完成与持久化规则。

返回[源码精读系列目录](README.md)或[课程总目录](../README.md)。
