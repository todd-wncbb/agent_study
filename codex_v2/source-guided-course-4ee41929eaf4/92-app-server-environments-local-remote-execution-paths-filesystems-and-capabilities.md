# 92：App-server Environment、本地与远程执行、路径、文件系统与能力边界

> 源码基线：4ee41929eaf4。本章解决“Codex 到底在哪台机器上工作、环境怎样被选择、远程命令和远程文件为何能使用同一套工具，以及 macOS App-server 如何安全表示 Windows 执行机路径”。

## 1. 本章解决什么问题

第 91 章多次出现“本地进程”和“远程 exec-server”，但没有系统解释“远程”究竟挂在哪里。

本章把 Environment 从 App-server 请求一直追到 Core、执行后端和文件系统后端。

## 2. 资料边界

当前[官方 OpenAI App Server 文档](https://learn.chatgpt.com/docs/app-server)描述了实验性的 `environment/info`、环境原生 `cwd`，以及未知环境或连接失败时的请求错误。

字段细节、选择状态机和本地/远程实现以固定提交 4ee41929eaf4 为准。今天的官方文档可能包含该提交之后的变化。

## 3. 先说人话：Environment 是哪一张工作台

把 Codex 想成一个可以在多间工坊工作的工程师。

- Thread 是工作任务单。
- Turn 是这次具体施工。
- Environment 是施工所在的工坊。
- `cwd` 是进门后站在哪张工作台前。
- workspace roots 是本次项目涉及的几个材料区。
- shell 是工坊里用来下命令的语言和解释器。
- filesystem 是工坊自己的文件柜。
- exec backend 是工坊自己的机器启动器。

环境不是一个路径，也不是一个进程，更不是 Thread 的别名。

## 4. 最重要的一句话

`environmentId` 回答“去哪里做”，`cwd` 回答“到了那里以后站在哪个目录做”。

这两个值缺一不可，也不能互相替代。

## 5. 一个最小例子

```json
{
  "environmentId":"devbox",
  "cwd":"/workspace/project",
  "runtimeWorkspaceRoots":["/workspace/project"]
}
```

含义不是“本机有一个叫 devbox 的目录”，而是“选择注册名为 devbox 的执行环境，并在那台环境的 `/workspace/project` 中工作”。

## 6. 本地环境

固定提交保留特殊 ID：

```text
local
```

它表示 App-server 所在主机上配置的本地执行/文件系统环境。

`local` 是保留 ID，动态添加远程环境不能抢占这个名字。

## 7. 远程环境

远程环境把执行和文件操作转发给 exec-server。

App-server 可以在 macOS，exec-server 可以在 Linux 或 Windows；环境的路径、shell 和文件系统语义属于 exec-server 所在侧。

## 8. Environment 不是只有 exec

固定提交的 `Environment` 同时持有：

- `ExecBackend`
- `ExecutorFileSystem`
- `HttpClient`
- 可选远程 client
- 可选本地 runtime helper paths
- provisioning 和连接状态

所以“切换环境”同时切换命令、文件和环境拥有的网络能力。

## 9. 本地与远程实现对照

| 能力 | 本地 Environment | 远程 Environment |
|---|---|---|
| 执行 | `LocalProcess` | `RemoteProcess` |
| 文件系统 | `LocalFileSystem` | `RemoteFileSystem` |
| HTTP | `RouteAwareHttpClient` | 远程client |
| shell/info | 本机检测 | `environment/info` |
| 连接 | 不需要网络连接 | 惰性/可重连连接 |

上层依赖 trait，而不是在每个工具里重写一遍 local/remote `if`。

## 10. EnvironmentManager 是什么

`EnvironmentManager` 是进程级环境注册表。

它保存：

- 默认环境 ID
- ID 到 `Arc<Environment>` 的映射
- 可选本地环境
- 本地 helper 路径
- 出站 HTTP factory

## 11. 为什么要 Manager

客户端和模型只应选择稳定 ID，不应拿到 WebSocket、子进程对象或文件系统实现细节。

Manager 把“逻辑身份”和“物理连接”隔开。

## 12. 三种身份不要混淆

| 身份 | 例子 | 生命周期 |
|---|---|---|
| Environment ID | `devbox` | 配置/注册表级 |
| 连接/session ID | 一次 exec-server 初始化连接 | 可断开、恢复或替换 |
| process ID | 一次命令会话 | 单连接或管理器作用域 |

断线重连不应自动把 `devbox` 变成另一个逻辑环境。

## 13. 环境从哪里来

固定提交可从以下来源准备 Manager：

- `CODEX_HOME/environments.toml`
- legacy `CODEX_EXEC_SERVER_URL`
- Noise rendezvous 环境变量
- App-server `environment/add`
- provisioned environment 上报

这些是注册来源，不是每轮 Turn 的选择方式。

## 14. environments.toml 的优先含义

如果 `CODEX_HOME/environments.toml` 存在，它定义配置环境。

否则 Manager 保留 legacy `CODEX_EXEC_SERVER_URL` 行为。

这里最重要的是“发现配置”与“开始使用”仍是两个阶段。

## 15. 禁用环境访问

固定提交允许把 legacy URL 配成 `none`。

此时默认环境为空，本地环境也不加入；上层据此关闭模型可见的 shell/filesystem 工具。

这不是“只能本地”，而是“没有执行环境”。

## 16. 默认环境

Manager 保存一个可选默认环境 ID。

新 Thread 没有显式环境选择时，Core 由默认环境构造初始 selection。

## 17. 默认列表的顺序

`default_environment_ids()` 先放默认环境，再追加其他配置环境。

因此第一项具有“当前/primary 环境”的语义，后续项是可附加选择的其他环境。

## 18. environment/add

固定提交的参数是：

```json
{
  "environmentId":"devbox",
  "execServerUrl":"ws://127.0.0.1:9000",
  "connectTimeoutMs":10000
}
```

它在 Manager 中添加或替换一个远程环境，但不修改默认环境。

## 19. upsert 的含义

Upsert = 不存在就插入，存在就替换。

替换同 ID 环境会产生新的 `Environment` 实例；已经捕获旧实例的 Turn snapshot 不应悄悄变成新实例。

## 20. environment/add 的校验

固定提交至少拒绝：

- 空 environment ID
- 保留 ID `local`
- 被禁用或缺失的远程 URL

校验在注册边界完成，而不是等第一条命令才报错。

## 21. environment/info 解决什么问题

客户端不能假定远程环境使用 bash，也不能假定默认目录是 `/workspace`。

`environment/info` 让环境自己报告 shell 和默认 `cwd`。

## 22. environment/info 请求

```json
{
  "method":"environment/info",
  "id":21,
  "params":{"environmentId":"devbox"}
}
```

## 23. environment/info 响应

```json
{
  "id":21,
  "result":{
    "shell":{"name":"bash","path":"/bin/bash"},
    "cwd":"file:///workspace/project"
  }
}
```

注意：响应中的 `cwd` 是规范 `file:` URI，不是普通路径字符串。

## 24. cwd 为什么可以是 null

exec-server 可能无法读取继承工作目录，或旧协议没有该字段。

因此客户端必须处理 `cwd: null`，不能把它当成空字符串或根目录。

## 25. 未知 ID 的 info 行为

`environment/info` 先查 Manager。

未知 ID 返回 invalid request；已知环境但 info/连接失败映射为 internal error。

“未知配置”和“已配置但不可达”是两类诊断。

## 26. environment/status 解决什么问题

Info 可能建立或等待连接，而 status 用于非破坏性观察。

固定提交的 status 不主动启动或恢复环境。

## 27. 四种 status

- `ready`
- `pending`
- `disconnected`
- `unknown`

`disconnected` 不保证永远失败；下一次正常使用仍可能恢复。

## 28. status 为什么不自动恢复

状态页频繁刷新不应意外启动昂贵远程机器或制造连接风暴。

观察和改变状态被有意分开。

## 29. Ready 不等于永远在线

对本地环境，Ready 是直接事实。

对已连接远程环境，固定提交通过现有连接做 fail-fast `environment/status` probe；网络随后仍可能断开。

## 30. Pending 的两种常见含义

- 惰性环境从未开始连接。
- 初始启动还没有结束。

Pending 不是错误，也不是已经可以执行。

## 31. Provisioning 与 Connecting 的区别

Provisioning 是“远程工作机是否准备好”。

Connecting 是“App-server 到 exec-server 的协议通道是否建立”。

机器准备好后，连接仍可能失败；连接参数存在时，机器也可能还没 provision 完。

## 32. 惰性环境

Provisioned remote environment 只有被选择使用时才启动连接。

这样未使用的环境不会无谓占用连接和启动成本。

## 33. ordinary remote 的预连接

普通配置的 remote environment 在加入 Manager 后可后台 `start_connecting()`。

它不阻塞注册调用，实际使用仍需 `wait_until_ready()`。

## 34. ThreadStart 的 environments

固定提交的 `thread/start` 支持实验性：

```json
"environments":[...]
```

它建立 Thread 的 sticky environments。

## 35. 省略、空、非空是三种值

| 输入 | Thread start 含义 |
|---|---|
| 省略 | 环境访问开启时选择默认环境 |
| `[]` | 后续未覆盖 Turn 禁用环境访问 |
| 非空 | 第一项为当前环境，其余也附加到 Turn |

空数组绝不能被当成“未填写”。

## 36. TurnStart 的 environments

Turn 也能提供环境选择，而且是 sticky 的：从本轮起影响后续 Turns。

## 37. Turn 的三态语义

| 输入 | Turn 含义 |
|---|---|
| 省略 | 使用 Thread sticky environments |
| `[]` | 本轮禁用环境访问 |
| 非空 | 本轮以第一项为 current environment |

固定提交的协议注释明确区分这三态。

## 38. 为什么 override 还会 sticky

App-server 的多项 Turn 设置采用“本轮及后续轮次”模型，例如 cwd、workspace roots、权限和环境。

客户端若只想临时切换一轮，下一轮必须显式切回。

## 39. TurnEnvironmentParams 的三个字段

```rust
environment_id
cwd
runtime_workspace_roots
```

每个选中环境都有自己的 cwd 和 roots，而不是所有环境共用本机路径。

## 40. runtimeWorkspaceRoots 省略时

单个环境选择中未给 roots，App-server 默认使用：

```text
[cwd]
```

这是环境内默认，不是全局 server cwd。

## 41. roots 的去重

解析环境选择时，重复的 workspace root 被保序去重。

保序很重要，因为客户端显示和后续策略展开可能依赖稳定顺序。

## 42. 重复 environment ID

`ThreadEnvironments::update_selections` 用 `HashSet` 跳过同一列表内重复 ID。

一个 Turn 不会因为重复列出同一 ID 而获得两个独立的同名工作台。

## 43. 第一项为什么是 primary

`TurnEnvironmentSnapshot::primary()` 返回第一个 Ready 环境。

模型没有提供 `environment_id` 时，工具默认选择 primary。

## 44. primary 不一定是 local

若列表是：

```json
[devbox, local]
```

默认工具目标是 `devbox`，本地只是额外环境。

## 45. local() 与 primary() 不同

`local()` 查找第一个非远程环境。

`primary()` 只看选择顺序中的第一个 Ready 环境。

调用者必须按需求选 accessor，不能用 `local()` 冒充“当前环境”。

## 46. single_local_environment()

一些 legacy API 只有在“恰好一个环境、它是本地、没有 starting 环境”时才安全工作。

固定提交用 `single_local_environment()` 显式表达这个强前提。

## 47. 为什么 starting 也会阻止 single local

假设 local 已 Ready，devbox 仍 Starting。

若此时把世界解释成“只有 local”，等 devbox Ready 后同一轮语义会突然改变；所以兼容路径选择保守拒绝。

## 48. 顶层 cwd 与环境 cwd

`turn/start.cwd` 是 legacy、本机 `PathBuf` 兼容入口。

`TurnEnvironmentParams.cwd` 是指定环境原生路径字符串。

多环境代码应优先使用后者，顶层 cwd 只用于兼容 fallback。

## 49. legacy_fallback_cwd

Core 的 TurnContext 仍保留 deprecated 本机 cwd，供未迁移消费者使用。

显式 environments 中若包含可转换的 local selection，优先拿它的 cwd；否则沿用 Thread config cwd。

## 50. 为什么叫 fallback

它不是远程环境的真实 cwd。

它只是尚未迁移到 `PathUri` 的本地消费者所需兼容值。

## 51. 顶层 runtimeWorkspaceRoots 的作用范围

显式 environment selections 自己拥有 roots，直接透传。

顶层 `runtimeWorkspaceRoots` 只作为默认环境兼容输入，不能覆盖每个显式环境自己的 roots。

## 52. cwd-only 更新怎样处理 roots

没有显式环境列表时，legacy cwd 更新会把旧 cwd root 重定向到新 cwd，同时保留其他 roots并去重。

这是为了保持引入 environments 之前的部分更新行为。

## 53. 一个双环境例子

```json
{
  "environments":[
    {
      "environmentId":"linux-build",
      "cwd":"/work/repo",
      "runtimeWorkspaceRoots":["/work/repo","/work/cache"]
    },
    {
      "environmentId":"local",
      "cwd":"/Users/me/repo",
      "runtimeWorkspaceRoots":["/Users/me/repo"]
    }
  ]
}
```

相同代码项目可以在两台机器上有完全不同绝对路径。

## 54. 路径不能靠 std::path 一把梭

`PathBuf` 按当前进程操作系统解释路径。

macOS App-server 看到 `C:\repo` 时，不能让 macOS `PathBuf` 决定它是不是 Windows 绝对路径。

## 55. PathConvention

固定提交把路径语法抽象为：

- `Posix`
- `Windows`

它描述语法，不直接表示某一台具体操作系统。

## 56. POSIX 路径示例

```text
/workspace/project/src/main.rs
```

以 `/` 开头，分隔符为 `/`，大小写比较保持敏感。

## 57. Windows drive 路径示例

```text
C:\repo\src\main.rs
```

drive letter 加根分隔符被推断为 Windows absolute path。

## 58. Windows UNC 路径示例

```text
\\server\share\repo
```

它也属于 Windows 绝对路径语法，不能当成 POSIX 的双斜杠字符串随便处理。

## 59. 相对路径为什么被拒绝

环境 selection 的 cwd 必须是绝对路径。

`repo/src` 不说明它相对 App-server、exec-server、默认 cwd 还是 Thread legacy cwd，接受它会产生歧义和权限绕过风险。

## 60. LegacyAppPathString 是什么

它是 App-server 边界暂时保留普通路径字符串的 newtype。

它先原样保存，再在明确需要时推断 POSIX/Windows 语法并转为 `PathUri`。

## 61. 为什么名字有 Legacy

新内部表示倾向使用类型化 `PathUri`。

但现有 App 客户端仍发送 `/a/b` 或 `C:\a\b`，所以边界需要迁移桥梁。

## 62. to_inferred_path_uri

该转换只接受可推断的绝对 POSIX 或 Windows 路径。

失败时 App-server 在请求边界报告：“不使用绝对 POSIX 或 Windows path syntax”。

## 63. PathUri 是什么

`PathUri` 是不可变、跨平台、只接受 `file:` scheme 的类型。

例如：

```text
file:///workspace/project
file:///C:/repo
file://server/share/repo
```

## 64. 为什么内部用 file URI

它提供统一、可序列化、可验证的跨平台载体。

路径属于哪种语法可以从 URI 检查，而不必先把它错误转换为当前宿主 `PathBuf`。

## 65. PathUri 不是 Web URL

固定提交只接受 `file:`，并拒绝：

- 用户名/密码
- port
- query
- fragment

它表示文件资源，不用于 HTTP 下载。

## 66. URI 的 percent encoding

文件名含空格时可能表示为：

```text
file:///work/My%20Project
```

不能用普通字符串替换 `%20` 或直接按 `/` 手写切割。

## 67. Windows drive canonicalization

PathUri 会把 Windows drive letter 规范为大写。

Windows 路径 equality/hash 对 ASCII case 不敏感；POSIX 路径保持大小写敏感。

## 68. 规范化不等于解析真实文件系统

PathUri 的词法操作不会解析 symlink、Unicode normalization 或所有文件系统 alias。

`file:///a/../b` 的表示处理和“真实磁盘上它指向哪个 inode”是两层问题。

## 69. join 的正确语义

工具参数中的 `workdir` 可以相对选中环境 cwd 做 URI lexical join。

例如 primary cwd 为 `/work/repo`，`workdir: crates/core` 得到该环境中的 `/work/repo/crates/core`。

## 70. 为什么不允许任意 absolute join

Join API要求相对 path，避免调用者以为是在 cwd 下，实际却悄悄替换整个 root。

绝对目标应显式作为完整路径处理和重新校验权限。

## 71. foreign path 的陷阱

在 POSIX 上，`file:///C:/repo` 可能机械转换成 `/C:/repo`。

这不代表它是 POSIX 原生目录；代码还必须比较 `infer_path_convention()` 与 `PathConvention::native()`。

## 72. 一个错误示例

```rust
if cwd.to_abs_path().is_ok() {
    // 错误地断言 cwd 属于本机
}
```

正确判断还要验证 inferred convention 是否与 host native convention 一致。

## 73. Shell 从哪里来

本地环境使用 App-server 已检测的本地 shell。

远程环境等待 Ready 后调用 info，把远程报告的 shell name/path 转成 Core `Shell`。

## 74. 远程 shell 示例

App-server 在 macOS 使用 zsh，远程 Windows exec-server 报告：

```json
{"name":"powershell","path":"powershell.exe"}
```

远程命令必须按 PowerShell 语义构造，不能拿本机 zsh quoting 发过去。

## 75. shell path 不一定是绝对路径

协议允许 `cmd.exe` 这样的 command name。

因此 `EnvironmentShellInfo.path` 是 String，不是 `PathUri`。

## 76. requested shell 的限制

固定提交暂未在远程环境任意解析请求 shell。

若模型请求的 shell 类型与环境报告的默认 shell 不一致，handler 返回面向模型的错误。

## 77. shell info 失败怎么办

环境连接成功但 info 或 shell 解析失败时，selection resolution 可以保留 Environment，shell 为 `None`。

命令路径可能回退到 session shell，但远程 requested-shell 校验会更加保守。

## 78. Shell snapshot

Core 为选中环境和其 cwd 异步构建 shell snapshot。

它用于捕获该工作环境的 shell 状态，不应把本地 snapshot 错配给不同远程 cwd。

## 79. 相同选择可复用

`update_selections` 若发现 environment ID、cwd、roots 完全相同且旧 resolution 没失败，会复用 selection 与连接解析工作。

这样 sticky Turn 不必每轮重建连接和 shell resolution。

## 80. 相同 ID、不同 cwd

连接状态属于 Environment 实例，不属于 cwd。

因此切换 `devbox` 内的 cwd 可以复用同一连接，但需要新的 selection/shell snapshot 语义。

## 81. Ready/Starting 状态机

Turn snapshot 中每项是：

- `Ready(TurnEnvironment)`
- `Starting(StartingTurnEnvironment)`

环境启动是异步的，不能把“已经选择”假装成“已经可用”。

## 82. non-blocking snapshot

某些 snapshot 不等待所有远程环境启动。

它保留 Starting 项，让 Turn 可以先获得部分可用环境，而不是被最慢环境整体阻塞。

## 83. blocking snapshot

需要完整准备的路径会 await resolution。

成功变 Ready，失败环境被跳过；错误不会伪装成 Ready handle。

## 84. failed selection 的语义

固定提交在 resolution 失败时记录日志并从 ready snapshot 中跳过该环境。

所以“请求中列出过”不保证最终工具一定可选择到。

## 85. wait_for_environment 工具

若环境仍 Starting，模型可以用 `wait_for_environment(environment_id)` 等待。

成功后返回 ready；失败则明确告诉模型环境不可用，应继续不用它。

## 86. 为什么要给模型 wait 工具

多环境启动速度不同。

让整个 Turn 在开始前等待所有环境，会牺牲可并行工作；让模型按需等待更灵活。

## 87. connection event

Thread 为远程环境订阅 connection state，并转成：

- EnvironmentConnected
- EnvironmentDisconnected

App-server 再把它们关联到 threadId 和 environmentId 通知客户端。

## 88. 为什么通知必须带两个 ID

Environment ID 说明哪台工作台断了。

Thread ID 说明哪个任务的 UI/状态正在观察这次连接；同一环境可被多个 Thread 使用。

## 89. selection 移除时的 listener 清理

当新列表不再引用旧连接 listener，逻辑移除后 abort task。

即使 ArcSwap reader 还持有旧 snapshot，也不继续向当前 Thread 泄漏连接事件。

## 90. ArcSwap 的作用

读者可低成本取得一致的 selection vector snapshot，更新者整体替换 `Arc<Vec<...>>`。

旧 reader 继续看旧快照，新 reader 看新快照，避免原地修改造成半更新状态。

## 91. 模型工具怎样选环境

通用规则是：

- 工具参数有 `environment_id`：按 ID 查当前 Turn Ready environments。
- 没有：使用 primary。
- 找不到：返回面向模型的 unknown turn environment error。

## 92. environment_id 不是任意 Manager ID

工具只能使用当前 Turn 已选择并 Ready 的环境。

即使 Manager 注册了 `secret-box`，未附加到 Turn 也不能靠工具参数越过 selection 边界。

## 93. 为什么这是一条安全边界

Manager 表示“server 知道哪些环境”。

Turn snapshot 表示“本任务获准使用哪些环境”。

把两者混用会把配置发现能力扩大成任务访问权限。

## 94. exec_command 的环境解析

Unified exec handler 先解析 `environment_id`，再从选中环境取得：

- environment backend
- cwd
- filesystem
- shell
- permission profile
- workspace roots

后续命令所有关键输入来自同一个 `TurnEnvironment`。

## 95. workdir 属于谁

`workdir` 相对所选环境 cwd，而不是 App-server 进程 cwd。

双环境场景中，相同 `workdir: target` 会分别得到 `/work/repo/target` 和 `/Users/me/repo/target`。

## 96. 命令怎样转发

上层构造平台中立的执行请求，经所选 Environment 的 `ExecBackend`：

- local -> 本地主进程/PTY实现
- remote -> exec-server RPC

远程 exec 请求携带 target 的 `PathUri` cwd、argv、env、tty 和 Sandbox intent。

## 97. 协议 process_id 不是 OS PID

exec-server `ExecParams.process_id` 是客户端选择的逻辑 handle，作用域在连接/session。

它用于 `process/write`、resize、terminate 和通知对位，而不是让客户端直接发系统信号给某个 PID。

## 98. Sandbox 在哪边落地

跨平台执行时，App/Core表达 portable Sandbox intent。

具体 wrapper argv 由执行环境一侧解析；否则 macOS App-server 不可能正确拼出 Windows Sandbox 启动方式。

## 99. foreign path 与 Sandbox

固定提交仍有迁移边界：平台 Sandbox 需要 host-native `AbsolutePathBuf` 的部分逻辑不能安全处理 foreign cwd。

真正 `SandboxType::None` 的 foreign execution 可绕开某些本机路径转换；需要 Sandbox 时则保守报错。

## 100. 为什么不能“先放行再说”

Sandbox 规则中的 cwd 和 roots 若被按错误操作系统解释，可能把允许范围扩到意外位置。

无法可靠转换时 fail closed 比猜测路径安全。

## 101. Permission profile 是 per environment 的

`TurnEnvironment` 持有有效 `EnvironmentConfig`，其中含 permission profile snapshot。

执行时 workspace roots 会物化进该环境权限，而不是套用另一个环境的本地 roots。

## 102. 权限缓存为何带 environment ID

同样的 `/workspace` 在两个远程环境是两个完全不同资源。

审批 key 和已授予权限必须包含 environment ID，防止 A 环境授权被错误复用于 B。

## 103. request_permissions 的默认目标

未指定 environment ID 时，它使用 primary。

相对文件权限路径也相对所选环境 cwd 解析。

## 104. apply_patch 怎样选择环境

多环境模式下，自由格式 patch 可包含：

```text
*** Environment ID: devbox
```

然后 handler 使用该 `TurnEnvironment` 的 filesystem 和权限。

## 105. 为什么 patch 也需要 ID

`src/main.rs` 在 local 与 devbox 中可能内容不同。

不指定目标会造成“模型读了远程文件，却把补丁写到本地”的严重一致性错误。

## 106. view_image 也属于环境能力

远程图片不是 App-server 本机路径。

view_image 先选择 TurnEnvironment，再从该环境 filesystem 读取 bytes，最后交给视觉处理路径。

## 107. 文件系统抽象

`ExecutorFileSystem` 为上层提供 read、write、metadata、directory、copy、remove 等操作。

本地和远程实现共享接口，使 apply_patch、图片和能力发现不必知道传输细节。

## 108. RemoteFileSystem

远程实现把 `PathUri` 和 Sandbox context 序列化成 exec-server `fs/*` RPC。

二进制文件通过 base64 等协议字段传输，不经过本地 `std::fs`。

## 109. LocalFileSystem

本地实现把 host-native PathUri 安全转换成本机路径，并调用本地文件系统/helper。

它仍可带 Sandbox context，不等于所有本地文件操作天然无限制。

## 110. App-server fs/* 是一个容易混淆的例外

固定提交的 App-server 客户端直调 `fs/readFile`、`fs/writeFile` 等 API 只取 `try_local_environment()`。

它们不是任意远程环境浏览 API；未配置 local filesystem 时返回错误。

## 111. 两层 fs/* 不要混淆

| 层 | 调用者 | 目标 |
|---|---|---|
| App-server `fs/*` | App客户端 | 固定提交中仅本地 Environment |
| exec-server protocol `fs/*` | RemoteFileSystem | 远程 exec-server 文件系统 |

方法名相似，不代表暴露边界相同。

## 112. command/exec 也是 local-only 例外

第 91 章的 App-server `command/exec` 是独立、本地 server 命令 API。

它与 Agent 在 Turn 中通过 selected Environment 使用 unified exec 不是同一个入口。

## 113. process/spawn 也是 local-only 例外

固定提交的显式 `process/spawn` 管理 App-server 本地裸进程。

若要让 Agent 在远程环境执行，应使用 Turn environment + 模型工具链，而不是把所有 API 都想成自动远程路由。

## 114. thread/shellCommand 的本地主机语义

固定提交把它定义成 host shell escape hatch。

它不因 Thread primary 是 devbox 就自动变成 devbox shell；调用前必须阅读该 API 自己的合同。

## 115. 能力边界的判断方法

看到一个“执行/文件”API时问三件事：

1. 它是否接收 environment ID？
2. 它是从 Turn snapshot 还是 Manager/local accessor 取 backend？
3. Sandbox 与所有权在哪一层？

不能仅凭方法名称猜测。

## 116. EnvironmentCapabilities

exec-server info 还包含 capability flags，例如：

- `network_proxy_launch`
- `capability_discovery_sandbox`

客户端必须先 gate 新字段/行为，再发送只有新 server 支持的请求。

## 117. 为什么 capabilities 需要协商

App-server 与 exec-server 可能版本不同。

若新 client 默认发送旧 server 不懂的字段，远程执行会因为版本偏差整体失败。

## 118. 缺失 capabilities 的兼容行为

固定提交为 capabilities 使用 default。

旧 exec-server 未返回时，各 flag 为 false，调用方走保守兼容路径，而不是假定支持。

## 119. capability discovery

Environment 还提供高层 capability-root discovery，可发现插件和 skills manifest。

远程环境通过 exec-server discovery；本地环境在本地 filesystem 上发现。

## 120. discovery Sandbox gate

若请求 root 带需要执行的 filesystem Sandbox，而远程 info 未声明 `capability_discovery_sandbox`，固定提交返回 protocol error。

它不会悄悄无 Sandbox 扫描。

## 121. selected capability roots

Provisioning ready info 可给一个环境上报有序 capability roots。

固定提交限制最多 256 个，并要求 ID 非空、唯一且 location 的 environment ID 与所属环境一致。

## 122. 为什么 root 必须绑定环境

远程 skill/plugin 文件必须在产生它的环境读取和执行。

若 root 声称属于另一个 ID，就可能把路径和授权送到错误后端。

## 123. AGENTS.md 与环境

项目指令也可能来自不同环境的不同 workspace root。

Core 测试明确覆盖 local 与 remote 各自项目 instructions；模型上下文需要标明来源环境，避免同路径文本互相覆盖。

## 124. 同名路径不等于同一文件

`/repo/AGENTS.md` 在 local 和 devbox 上是两个资源身份。

完整身份至少是：

```text
(environment_id, path_uri)
```

## 125. HTTP 也属于环境

Environment 持有 `HttpClient`，用于环境拥有的网络请求能力。

远程环境可让网络流量从执行侧发生，而不是错误地从 App-server 主机发生。

## 126. Managed network 的 environment ID

命令的网络审批与 proxy audit 需要知道目标执行环境。

否则“允许 devbox 访问某 host”可能被本地主机或另一 remote 错误复用。

## 127. 断线后的 filesystem

普通 `get_filesystem()` 可通过惰性 client 等待或恢复连接。

`get_filesystem_without_reconnect()` 则要求 fail-fast，适合状态观察或不能触发恢复的清理路径。

## 128. 为什么需要 without_reconnect

退出、状态页或错误清理不应为了读取一个陈旧资源而重启远程连接。

控制副作用需要由调用场景明确选择。

## 129. 初始失败与后续重连

固定提交区分初始 startup 结果和已建立连接后的恢复。

`wait_until_ready()` 对初始永久失败不会无限重试；已工作连接之后的断开可由正常请求路径尝试恢复。

## 130. 连接事件不是命令结果

EnvironmentDisconnected 只说明通道状态。

某条命令是否完成、失败或结果未知，仍要看该命令协议的 terminal signal；不能用一个连接事件替代全部 process 状态。

## 131. Snapshot 的一致性

Turn 捕获的 snapshot 保留原 selection 顺序和具体 Environment handle。

Manager 后续 upsert 同名 ID，不应穿透并改变已捕获 Turn 的资源身份。

## 132. refresh_readiness

Snapshot 可把自己已经捕获的 Starting resolution 提升为 Ready。

它不会采用 Thread 更新后的全新 selections，因此避免一轮执行中环境集合漂移。

## 133. current、selected、configured 的区别

- configured：Manager 里存在。
- selected：Thread/Turn 列表中被选中。
- current/primary：selected 列表中的第一项。
- ready：异步启动已经成功。

一个环境可以 configured + selected，但还不是 ready。

## 134. 常见误解一：remote 是一个布尔开关

错误想法：`remote=true` 后所有文件和命令都自动远程。

真实设计：具体 Environment ID、每环境 cwd/roots、每个 API 的 backend 选择共同决定目标。

## 135. 常见误解二：cwd 唯一决定环境

错误想法：看到 `/workspace` 就知道是 devbox。

真实设计：多台 Linux 环境都可能有 `/workspace`，只有 environment ID 能区分。

## 136. 常见误解三：客户端主机路径可发给远程

错误想法：把 `/Users/me/repo` 直接作为 Windows devbox cwd。

真实设计：cwd 必须使用目标环境原生路径，远程也必须实际存在该路径。

## 137. 常见误解四：注册即可访问

错误想法：Manager 有 ID，模型就能用。

真实设计：还必须进入 Turn selection，并成功 Ready。

## 138. 常见误解五：连接断开等于环境删除

Disconnected 表示当前连接观察失败，逻辑 Environment ID 和配置仍可存在。

Unknown 才表示 Manager 中没有该 ID。

## 139. 常见误解六：所有 fs API 都自动远程

Agent 工具可经选中 Environment 访问远程 filesystem。

但固定提交的 App-server 直调 `fs/*` 明确只使用 local environment。

## 140. 客户端启动建议

一个支持环境的客户端可按以下顺序：

1. 确认实验性 API capability。
2. 获取或知道 environment IDs。
3. 对候选环境调用 `environment/info`。
4. 用目标原生 cwd 构造 Thread selections。
5. 监听连接 notification。
6. 对 Starting 环境展示准备中状态。

## 141. cwd 选择建议

优先使用用户明确选定的目标环境目录。

若没有，可使用 `environment/info.cwd`；若为 null，要求用户或宿主平台提供，不要猜 `/`、`C:\` 或 App-server cwd。

## 142. UI 展示建议

路径旁应展示环境标签，例如：

```text
devbox · /workspace/project
local  · /Users/me/project
```

只显示路径会让同名目录和操作目标难以辨认。

## 143. 错误分类建议

至少区分：

- unknown environment
- provisioning pending/failed
- connect timeout
- initialize/protocol failure
- disconnected
- unsupported capability
- foreign path/Sandbox mismatch
- path 不存在或权限拒绝

把它们都显示成“命令失败”会丢失真正修复方向。

## 144. fail closed 检查表

- 未知 ID 不回退 primary。
- 不合法绝对路径不回退 server cwd。
- capability=false 不试探发送新字段。
- foreign Sandbox path 不按本机路径猜测。
- 环境 A 的授权不复用给 B。
- remote fs 失败不偷偷改读 local 同名文件。

## 145. 可观测性建议

日志和 span 至少带：

- environment_id
- thread_id/turn_id
- local/remote
- connection/session identity 的安全摘要
- operation 类型
- startup/connect/info/exec/fs phase

不要记录 auth token、Noise 授权材料或秘密环境变量。

## 146. 测试矩阵

环境功能不能只测 local Linux happy path。

至少覆盖：

- local -> local
- POSIX App-server -> POSIX remote
- POSIX App-server -> Windows remote
- Windows App-server -> POSIX remote
- omitted/empty/non-empty selections
- primary remote + secondary local
- starting/ready/failed/disconnected
- capabilities 缺失与版本偏差

## 147. 选择三态测试

为 Thread 和 Turn 分别证明：

```text
None != Some([]) != Some([selection])
```

这类测试能防止序列化层或客户端默认值把“禁用”改成“沿用”。

## 148. 路径测试

测试应包含：

- `/repo`
- `C:\repo`
- `C:/repo`
- `\\server\share\repo`
- 相对 `repo`
- 含空格、百分号和非 UTF-8 fallback
- Windows drive 大小写 equality/hash

## 149. 连接竞态测试

让 environment selection 更新、连接成功/失败、listener removal 和 Turn snapshot 同时发生。

断言旧 snapshot 不采用新实例、removed listener 不再发送、terminal state 不重复。

## 150. 权限隔离测试

创建 A/B 两个环境，都有 `/workspace`。

只给 A 写权限，断言 B 的 apply_patch/exec 仍需独立授权或失败。

## 151. 版本偏差测试

用旧 exec-server response：没有 cwd、没有 capabilities。

断言反序列化成功，cwd 为 None、flags 为 false，并且需要新能力的请求被 gate。

## 152. 从源码阅读的第一条路线：协议

依次读：

- `app-server-protocol/src/protocol/v2/environment.rs`
- `app-server-protocol/src/protocol/v2/thread.rs`
- `app-server-protocol/src/protocol/v2/turn.rs`
- `exec-server-protocol/src/protocol.rs`

先写下 wire 字段、可选性、三态和兼容 default。

## 153. 第二条路线：注册与连接

读 `exec-server/src/environment.rs`。

追踪 `EnvironmentManager::from_snapshot`、default IDs、upsert、provisioning、`Environment::local`、`remote_with_transport`、status 和 info。

## 154. 第三条路线：Turn selection

读：

- `app-server/src/request_processors.rs`
- `app-server/src/request_processors/turn_processor.rs`
- `core/src/environment_selection.rs`
- `core/src/session/turn_context.rs`

画出 request string -> PathUri -> selection -> Starting/Ready -> TurnEnvironment。

## 155. 第四条路线：工具

读：

- `core/src/tools/handlers/mod.rs`
- `core/src/tools/handlers/unified_exec/exec_command.rs`
- `core/src/tools/handlers/apply_patch.rs`
- `core/src/tools/handlers/view_image.rs`
- `core/src/tools/handlers/wait_for_environment.rs`

确认每个工具怎样解析 ID、默认 primary，并取得同一环境的 backend/cwd/permissions。

## 156. 第五条路线：远程后端

读：

- `exec-server/src/remote_process.rs`
- `exec-server/src/remote_file_system.rs`
- `exec-server/src/client.rs`
- `exec-server/src/client_recovery.rs`
- `exec-server/src/server/request_dispatcher.rs`

追踪 RPC、连接恢复、process control 和 fs bytes。

## 157. 第六条路线：路径

读：

- `utils/path-uri/src/lib.rs`
- `utils/path-uri/src/api_path_string.rs`
- `exec-server/src/*_file_system_path_uri_tests.rs`

重点理解 URI、native path text、host-native `PathBuf` 三者的转换边界。

## 158. 动手练习一：解释一个请求

给定：App-server 在 macOS，primary 是 Windows devbox，secondary 是 local。

逐项写出未带 environment ID 的 `exec_command`、带 local ID 的 apply_patch、App-server `fs/readFile` 各自落到哪里。

## 159. 动手练习二：设计环境选择器

写一个客户端 reducer，输入 info/status/connection notification，输出：

- 可选环境列表
- primary
- cwd
- preparing/ready/disconnected badge
- Start Turn 按钮是否可用

不得把 Pending 当 Unknown。

## 160. 动手练习三：PathUri 转换

实现并测试：

```text
LegacyAppPathString -> inferred PathConvention -> PathUri -> target-native display string
```

解释为什么中间不能先转当前宿主 `PathBuf`。

## 161. 动手练习四：故障注入

让远程环境依次发生 connect timeout、info 缺 shell、执行中断线和恢复。

记录每一层应该发 request error、connection notification、tool error 还是 process terminal signal。

## 162. 理解检查

1. Environment 为什么不是 environment ID 加一个 cwd 就结束了？
2. `None` 和 `Some([])` 的环境语义有什么区别？
3. 为什么第一项是 primary，但 primary 不一定是 local？
4. 为什么 `file:///C:/repo` 在 POSIX 上能转 PathBuf 仍不代表它是 native path？
5. 为什么 Manager 已注册的环境不能直接被工具使用？
6. App-server `fs/*` 和 RemoteFileSystem 的 `fs/*` 有什么区别？
7. status 为什么不应自动恢复连接？
8. 为什么权限 cache 必须包含 environment ID？
9. capability=false 时为什么要保守 gate？
10. Starting 环境为什么仍属于 Turn snapshot 的重要状态？

## 163. 源码检查点

- `codex-rs/app-server-protocol/src/protocol/v2/environment.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/turn.rs`
- `codex-rs/app-server/src/request_processors/environment_processor.rs`
- `codex-rs/app-server/src/request_processors.rs`
- `codex-rs/app-server/src/request_processors/turn_processor.rs`
- `codex-rs/app-server/src/request_processors/fs_processor.rs`
- `codex-rs/exec-server/src/environment.rs`
- `codex-rs/exec-server/src/environment_provider.rs`
- `codex-rs/exec-server/src/environment_toml.rs`
- `codex-rs/exec-server/src/client.rs`
- `codex-rs/exec-server/src/client_recovery.rs`
- `codex-rs/exec-server/src/remote_process.rs`
- `codex-rs/exec-server/src/remote_file_system.rs`
- `codex-rs/exec-server/src/local_process.rs`
- `codex-rs/exec-server/src/local_file_system.rs`
- `codex-rs/exec-server-protocol/src/protocol.rs`
- `codex-rs/core/src/environment_selection.rs`
- `codex-rs/core/src/session/turn_context.rs`
- `codex-rs/core/src/tools/handlers/mod.rs`
- `codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`
- `codex-rs/core/src/tools/handlers/apply_patch.rs`
- `codex-rs/core/src/tools/handlers/view_image.rs`
- `codex-rs/core/src/tools/handlers/wait_for_environment.rs`
- `codex-rs/utils/path-uri/src/lib.rs`
- `codex-rs/utils/path-uri/src/api_path_string.rs`
- `codex-rs/core/tests/suite/remote_env.rs`

## 164. 本章词汇表

| 术语/代码词 | 字面含义 | 本章中的具体意思 |
|---|---|---|
| Environment | 环境 | 一套绑定的执行、文件系统、HTTP、shell、连接与能力后端 |
| Environment ID | 环境标识 | Manager 和 Turn 用于选择逻辑工作台的稳定字符串 |
| local | 本地 | App-server 主机上的保留环境 ID |
| remote | 远程 | 通过 exec-server client 提供能力的 Environment |
| EnvironmentManager | 环境管理器 | 保存默认环境、ID注册表、本地环境和创建策略的共享对象 |
| Registry | 注册表 | 从逻辑 ID 查找具体 Environment handle 的映射 |
| Default environment | 默认环境 | 新 Thread 未显式选择时排在第一位的环境 |
| Primary/current | 主/当前 | Turn selection 中第一个 Ready 环境，工具省略 ID 时的默认目标 |
| Selected | 已选择 | 已进入 Thread/Turn 环境列表，不等于已 Ready |
| Configured | 已配置 | Manager 知道该 ID，不等于 Turn 有权使用 |
| TurnEnvironmentParams | Turn环境参数 | wire 上环境 ID、目标原生 cwd 和 roots 的组合 |
| TurnEnvironmentSelection | Turn环境选择 | 路径已转换为 PathUri 后的 Core selection 数据 |
| TurnEnvironment | Turn环境 | Ready 的 Environment handle 加 cwd、roots、shell和权限配置 |
| Sticky | 粘性 | 本轮更新继续影响同 Thread 后续轮次 |
| Override | 覆盖 | 用新值替代 Thread 当前设置的请求输入 |
| cwd | Current working directory | 目标环境内命令和相对路径的基准目录 |
| Runtime workspace roots | 运行时工作区根 | 目标环境内本任务涉及、用于权限展开的绝对项目根 |
| Legacy fallback cwd | 旧兼容回退目录 | 尚未迁移 PathUri 的本地消费者使用的兼容本机 cwd |
| LegacyAppPathString | 旧App路径字符串 | API边界保留 POSIX/Windows 原生拼写的 UTF-8 newtype |
| PathUri | 路径URI | 可跨宿主保存、校验和词法操作的不可变 `file:` URI |
| PathConvention | 路径约定 | POSIX 或 Windows 路径语法，而非具体连接身份 |
| Host-native | 宿主原生 | 符合当前 App-server 进程操作系统路径语法 |
| Target-native | 目标原生 | 符合所选执行环境操作系统路径语法 |
| Foreign path | 外来路径 | 对当前 host 非原生、但对远程 target 可能原生的路径 |
| POSIX | 可移植操作系统接口 | 本章指以 `/` 为根和分隔符的路径表示习惯 |
| Drive path | 驱动器路径 | 如 `C:\repo` 的 Windows 绝对路径 |
| UNC | Universal Naming Convention | 如 `\\server\share` 的 Windows 网络共享路径 |
| file URI | 文件URI | 如 `file:///repo` 的结构化文件资源字符串 |
| Percent encoding | 百分号编码 | URI中用 `%HH` 表示特殊bytes的方式 |
| Canonical | 规范形式 | 经校验和统一规则后的稳定序列化表示 |
| ExecBackend | 执行后端 | 启动与控制命令进程的 local/remote trait abstraction |
| ExecutorFileSystem | 执行器文件系统 | 读写目标环境文件的 local/remote trait abstraction |
| LocalProcess | 本地进程 | 在 App-server 主机启动命令的 ExecBackend |
| RemoteProcess | 远程进程 | 经 exec-server RPC 启动和控制命令的 ExecBackend |
| LocalFileSystem | 本地文件系统 | 操作 App-server 主机文件的 filesystem 实现 |
| RemoteFileSystem | 远程文件系统 | 经 exec-server `fs/*` 操作目标机器文件的实现 |
| Exec-server | 执行服务 | 在目标机器执行进程、文件、HTTP与环境信息协议的服务 |
| Transport | 传输 | WebSocket、Noise rendezvous 或 stdio 等连接方式 |
| Lazy | 惰性 | 直到被选择/首次使用才完成启动或连接 |
| Provisioning | 置备 | 准备远程执行机器和其能力材料的阶段 |
| Connecting | 连接中 | 建立并初始化 App-server 到 exec-server 通道的阶段 |
| Ready | 就绪 | 环境已能提供可用 handle/响应 probe |
| Starting/Pending | 启动中/待定 | 已选择或已配置，但尚未完成初始准备 |
| Disconnected | 已断开 | 已观察连接失败，后续正常使用仍可能恢复 |
| Unknown | 未知 | Manager 中不存在请求的 environment ID |
| Environment info | 环境信息 | exec-server 报告的 shell、默认 cwd 和 capabilities |
| Environment status | 环境状态 | 不触发启动/恢复的 Ready/Pending/Disconnected/Unknown 观察 |
| Probe | 探测 | 通过既有连接快速确认环境仍响应的小请求 |
| Fail-fast | 快速失败 | 不等待、不启动、不重连，当前不可用立即返回 |
| Fail closed | 封闭失败 | 无法确定路径、能力或授权时拒绝而不扩大访问 |
| Shell | 命令解释器 | bash/zsh/powershell/cmd等目标环境命令语义 |
| Shell snapshot | Shell快照 | 针对具体 Environment+cwd 异步捕获的 shell 状态文件 |
| Capability | 能力 | exec-server显式声明支持的新协议或执行特性 |
| Capability gate | 能力门控 | 只有远端声明支持时才发送相关字段或启用行为 |
| Capability root | 能力根 | skill/plugin等可发现资源所在的环境绑定根位置 |
| Provisioned environment | 已置备环境 | 由外部ready/failed上报驱动、选中后才连接的远程环境 |
| Arc | 原子引用计数 | 让Manager、Thread和snapshot共享同一Environment实例 |
| ArcSwap | 原子Arc替换 | 让读者获取稳定旧快照、更新者整体发布新selection列表 |
| Snapshot | 快照 | 一轮捕获的有序 Ready/Starting 环境集合 |
| Resolution | 解析/准备结果 | 等待连接、info、shell和snapshot后得到的共享异步结果 |
| Backend | 后端 | 实际完成执行、文件或HTTP操作的实现对象 |
| Upsert | 插入或替换 | 按ID新增或用新Environment实例替换已有注册项 |
| Native path | 原生路径 | 使用指定 target/host 路径约定的普通路径文本 |
| Lexical | 词法 | 只按路径字符和segments处理，不查询真实磁盘或symlink |
| Sandbox intent | 沙箱意图 | 跨平台发送、由目标执行侧具体实现的权限限制描述 |
| Version skew | 版本偏差 | App-server与exec-server协议版本不完全一致 |
| Reconnect | 重连 | 逻辑Environment不变时重建物理exec-server连接 |
| Listener | 监听任务 | 把某Environment连接状态变化转成Thread事件的async任务 |
| environment_id argument | 环境参数 | 模型工具显式选择当前Turn内某个Ready环境的字段 |
