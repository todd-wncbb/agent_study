# 12：执行环境、审批与 Sandbox

先看一个很容易误解的场景：用户批准了运行 `just test -p codex-core`，但测试尝试访问网络或 workspace 外的文件，命令仍然失败。

这并不矛盾。批准表示“允许尝试这项操作”，沙箱回答的是“这个进程实际上能访问什么”。

## 1. 四个不同问题

一次命令执行至少要回答：

1. **在哪里执行？** local、remote 或选中的某个 environment。
2. **是否允许尝试？** approval policy、exec policy、用户授权。
3. **允许访问什么？** permission profile、network policy、additional permissions。
4. **由谁强制？** 实际执行端的 OS sandbox / exec-server。

把这四层混在一起，最容易产生安全误解。

## 2. Environment 抽象

`EnvironmentManager` 管理可用环境，每个 `Environment` 向 core 提供两个重要接口：

- `ExecBackend`：启动、读写、signal、terminate 进程；
- `ExecutorFileSystem`：读写、遍历、metadata、copy/remove 等文件操作。

Local environment 使用本机 backend；remote environment 使用 exec-server client。普通远端环境添加后开始连接，provisioned environment 可在被选择后再准备。连接状态与“是否被当前 turn 选择”是两件事。

例如：桌面 App 运行在本地 macOS，但仓库和编译器位于远端 Linux。此时命令执行、文件读写和沙箱强制都发生在远端；本地客户端主要负责交互与转发。不能因为 UI 在本机，就用本机路径解释远端命令。

## 3. Thread 选择与 Step 快照

Thread 保存 sticky environment selections；turn/start 可以覆盖。`TurnEnvironmentSnapshot` 保留有序选择，第一个是 primary。`StepContext` 进一步刷新 readiness，并让工具、filesystem、MCP capability roots 与本次 request 使用同一环境视图。

工具不应自行读取一个全局 cwd。它应使用当前 `TurnEnvironment` 中的 cwd、filesystem、permission profile 和 backend。

## 4. Shell 执行编排

`run_exec_like()` 的主路径：

1. 解析 cwd、command、timeout、environment 和 additional permissions；
2. 合并已经批准的 sticky turn permissions；
3. 验证显式 escalation 是否与 approval policy 相容；
4. 必要时拦截 `apply_patch`，走结构化 patch 路径；
5. 由 exec policy 计算 approval requirement；
6. 构造 `ShellRequest`；
7. 交给 `ToolOrchestrator` 执行 approval → sandbox → runtime；
8. 采集输出、diff、denial 与事件。

Approval 只是允许 runtime 尝试；真正访问仍受 sandbox 强制。

## 5. Unified Exec

`exec_command`/`write_stdin` 需要管理持续运行的 PTY/process，因此 `core/src/unified_exec/` 维护 process manager、process ID、sequence/output buffer、异步 watcher 和 cancellation。

本地 backend 最终由 `codex-sandboxing` 转换 spawn request；远端 backend 把 process、filesystem 和 sandbox context 发送给 exec-server。Core 不应假定远端路径能在本机 `std::fs` 打开。

## 6. SandboxManager

`SandboxManager` 根据 permission profile、平台和可用 backend：

- 判断是否需要 sandbox；
- 选择 Seatbelt、bubblewrap/Landlock、Windows restricted/elevated 等实现；
- 把抽象 policy 转换为启动参数或 wrapper；
- 保留 network 和 filesystem carve-outs；
- 在兼容性无法保证时 fail closed，而不是静默放宽。

平台细节会变化，但稳定原则是：policy 在 core 决策，强制在执行端完成。

把前面的例子拆成四层，会更清楚：

| 问题 | 示例答案 |
|---|---|
| 在哪里执行？ | 远端 Linux environment |
| 是否允许尝试？ | 用户已批准这条测试命令 |
| 允许访问什么？ | 仓库可读、workspace 可写、网络受限 |
| 由谁强制？ | 远端 exec-server 所在 Linux 的 sandbox |

## 7. Denial 与重试

进程失败不一定是 sandbox denial。`UnifiedExecProcess` 综合 executor-reported denial、sandbox type 和输出启发式进行分类。只有策略允许时，orchestrator 才能在 denial 后采用不同 sandbox attempt；已完成的 approval 可缓存，避免对同一动作重复询问。

Network approval 还有独立生命周期：注册、等待 proxy/denial、进程退出后完成和清理，不能只看进程 exit code。

## 8. 远端测试原则

测试要区分 host OS、target executor OS 和 local/remote transport。Agent 集成测试默认使用 `build_with_auto_env()`，避免把本机路径和行为写死。需要 spawn workspace binary 时使用 `codex_utils_cargo_bin::cargo_bin()`，确保 Cargo/Bazel runfiles 都能解析。

## 9. 常见误解

- **“用户批准后，命令就一定能做任何事。”** 不对，批准与资源访问限制是不同层。
- **“远端执行只是把本地 shell 命令通过 SSH 发出去。”** 过于简单。Codex 还抽象了进程、文件系统、权限信息、状态和输出。
- **“命令非零退出就是被沙箱拒绝。”** 不对，程序自身报错、缺少依赖和 sandbox denial 必须区分。

## 读完后自测

1. 为什么批准命令和允许网络访问不能合并成一个判断？
2. UI 在 macOS、executor 在 Linux 时，哪一端的路径和沙箱规则才决定命令行为？
3. 为什么 runtime 需要专门判断 sandbox denial，而不能只看 exit code？

## 本章词汇表

| 词语 | 直译 | 在执行环境中的意思 |
|---|---|---|
| Environment | 环境 | 命令和文件操作实际发生的一组执行资源 |
| Executor | 执行器 | 真正运行命令、访问目标文件系统的一端 |
| Backend | 后端实现 | `ExecBackend` 的本地或远端具体实现 |
| Provision | 配置供应 | 创建或准备一个可用远端执行环境 |
| PTY | 伪终端 | 让持续进程像在交互终端中运行的系统接口 |
| Denial | 拒绝 | 执行端报告访问被 sandbox 或策略阻止 |
| Fail closed | 失败时关闭 | 无法确认安全实现时拒绝，而非静默放宽 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

1. 阅读 `EnvironmentManager` 注释，区分 configured、selected、ready、connected。
2. 从 `run_exec_like()` 标出 approval、apply_patch interception 和 sandbox request 的顺序。
3. 比较 `LocalProcess` 与 `RemoteProcess` 的 `ExecBackend` 实现。
4. 找 `remote_process_preserves_executor_sandbox_type` 测试，解释 denial 判断为什么需要执行端报告 sandbox type。
