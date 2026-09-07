# 22. 执行环境与 Remote Executor

## 1. 为什么环境不是一个 `cwd`

Agent UI、Core、文件系统和命令执行器可能运行在不同机器或操作系统。只保存 `/workspace/project` 这个字符串无法回答：

-这是 Host 路径还是远程 Executor 路径？
-路径采用 Unix 还是 Windows 语义？
-哪个文件系统负责读取？
-命令在哪台机器执行？
-sandbox 在哪里落实？

因此 Codex 把 Environment 作为一等对象。

## 2. Host 与 Executor

### Host

运行 Codex Core/App Server 的环境。它可能读取本地配置、管理 UI 连接和持久化。

### Executor

实际提供 workspace 文件系统和命令执行的环境。可以是本机，也可以是远程容器、VM 或其他 OS。

Host 与 Executor 相同是常见情况，但实现不能依赖这个假设。

## 3. Environment Manager

Environment Manager 负责：

-发现配置的 environments；
-准备默认环境；
-追踪 readiness；
-返回环境文件系统和 exec client；
-处理本地/远程连接生命周期。

TUI bootstrap 会先准备 Environment Manager，再根据目标是否使用远程 workspace 决定 config cwd 语义。

## 4. Environment Selection

Thread 启动时建立 `TurnEnvironmentSelections`。一个 Turn 获得稳定选择，Step 再调用 `refresh_readiness()` 得到 `TurnEnvironmentSnapshot`。

这一区分很重要：

-选择哪个环境属于 Turn 意图；
-环境是否已经 ready 属于动态 Step 状态。

## 5. Readiness

远程环境可能正在启动、重连或失败。Tool Planning 可以：

-只暴露 ready 环境的执行工具；
-提供 `wait_for_environment`；
-在 World State 告诉模型环境尚未可用；
-用户输入明确依赖某环境时等待或报错。

不能在 Prompt 中宣称环境可用后，执行时才发现连接根本没建立。

## 6. Step Capture

捕获 Step 时：

```text
turn_context.environments.refresh_readiness()
  → refresh AGENTS.md with selected filesystems
  → resolve capability roots
  → executor capability discovery
  → build sandbox contexts
  → build MCP and Tools
```

World State 与 ToolRouter 使用同一 snapshot。

## 7. Executor FileSystem

代码不应直接到处调用 `std::fs`。Executor FileSystem 抽象允许：

-读取 Host 文件；
-读取远程文件；
-在测试中使用 fake filesystem；
-用统一 PathUri 表达路径；
-应用相应 sandbox context。

Skill Loader 保存每个 Skill 对应的 filesystem，确保 Executor Skill 正文通过 Executor 读取。

## 8. `PathBuf`、Absolute Path 与 `PathUri`

不同路径类型表示不同保证：

-`PathBuf`：操作系统路径，但可能相对；
-`AbsolutePathBuf`：已经验证为绝对路径；
-`PathUri`：可携带跨环境/协议资源语义；
-`skill://`、`mcp://`：不是普通文件路径。

跨环境边界时应保留 URI/authority，不能对远程 Windows 路径使用 Host macOS 的路径解析规则。

## 9. Capability Roots

Capability Root 表示环境中可发现 Skills、Tools 或其他能力的根。Step 会解析 selected roots，并只对 ready roots 做 Executor discovery。

这让远程镜像可以携带自己的 Skill 包和执行能力，而不要求先复制到 Host。

## 10. Remote Skill

Executor Skill 的流程：

```text
Environment capability discovery
  → Executor filesystem 扫描 SKILL.md
  → CatalogEntry(authority=Executor)
  → 模型选择
  → skills.read 或 provider.read
  → 同一 Executor filesystem 返回正文
```

任何一步都不应把它转换为 Host 本地 `std::fs::read_to_string()`。

## 11. Tool 在哪里执行

Tool Handler 从 StepContext 解析目标 environment。执行请求应包含：

-environment ID；
-cwd；
-command；
-环境变量策略；
-sandbox/permission context；
-网络策略；
-timeout、TTY 等运行参数。

Exec Server 或本地 backend 再在对应环境启动进程。

## 12. Sandbox 在执行端落实

如果 Core 在 Host、命令在 Executor，只在 Host 检查路径是不够的。Executor 必须根据传入且可信的 sandbox policy 强制执行：

-文件系统 roots；
-网络规则；
-进程能力；
-平台特定隔离。

远程服务不能信任模型提供的“我已经得到批准”字符串，必须使用协议中的结构化权限决定。

## 13. 跨 OS

可能存在：

```text
App/TUI: macOS
App Server/Core: Linux
Executor: Windows
```

需要避免：

-使用 Host path separator 解析 Executor 路径；
-假设 `/bin/sh` 存在；
-把 Unix signal 当作 Windows 进程控制；
-使用 `CARGO_MANIFEST_DIR` 查远程 fixture；
-将 Host cwd 发送给远程工具。

`TurnContext` 和集成测试的 auto-env helper 用于覆盖这些组合。

## 14. Environment World State

模型需要看到：

-环境 ID/名称；
-cwd；
-OS 和 shell；
-workspace roots；
-readiness；
-文件系统权限摘要。

但不要把底层认证 token、连接地址或完整内部 sandbox 配置暴露给模型。

## 15. Environment 变化

环境从 starting 变为 ready 后，下一 Step：

1.refresh readiness；
2.World State 生成 diff；
3.ToolRouter 增加相应工具；
4.模型在新请求中同时看到状态和 ToolSpec。

当前正在进行的 Step 不被中途替换。

## 16. 失败场景

### 环境启动超时

返回明确状态，允许用户重试或选择其他环境。

### 文件系统读取失败

错误必须包含 environment 和资源 identity，而不只是“file not found”。

### Exec 连接中断

结果可能处于未知状态。只读命令可重试，副作用命令需要谨慎确认。

### 环境被移除

旧 Step 仍按快照完成或失败；下一 Step 刷新 Tool/World State。

### 路径来自错误环境

在类型或边界校验时拒绝，不尝试字符串拼接修复。

## 17. 远程集成测试

Core 测试默认使用 `TestCodexBuilder::build_with_auto_env()`，App Server 使用对应 auto-env start helper。测试应覆盖：

-App/Core 与 Executor 不同 OS；
-不同 cwd；
-Tool request 发送正确 environment ID；
-远程 Skill 通过正确 filesystem；
-sandbox 在远端生效；
-环境 readiness 更新下一 Step；
-资源路径不依赖 Host manifest dir。

## 18. 设计原则

```text
路径必须带环境语义。
选择在 Turn 固定，readiness 在 Step 刷新。
Prompt 状态和 Tool 执行来自同一环境快照。
Sandbox 必须在实际执行端强制。
远程资源由所属 authority/filesystem 读取。
所有协议和测试默认考虑跨 OS。
```

