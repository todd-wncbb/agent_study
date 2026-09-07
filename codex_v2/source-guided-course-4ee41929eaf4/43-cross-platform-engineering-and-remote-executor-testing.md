# 43：跨平台工程与远程执行测试——同一条 Turn，App 在这里，命令可能在另一种 OS 上运行

第 12 章讲过执行环境和 Sandbox，第 27 章讲过异步 task 与取消，第 42 章讲过依赖怎样跨构建平台进入制品。本章专门处理一个在 Codex 中非常容易产生错误直觉的问题：运行 app-server 的操作系统，不一定等于真正执行命令和访问工作区文件的操作系统。

例如测试进程可以运行在 Linux host 上，通过 WebSocket 连接一个在 Wine 中运行的 Windows exec-server。此时 Rust 测试代码里的 `cfg!(windows)` 是 `false`，但目标命令必须使用 Windows 路径、Windows Shell 和 Windows 进程语义。如果把“当前测试进程是什么 OS”和“目标执行环境是什么 OS”混为一谈，本地测试可能全部通过，远程 Windows 执行却在路径、命令、编码或终止阶段失败。

> 源码基线：`4ee41929eaf4`。本章以当前仓库的 `TestEnvironment`、`TestCodexBuilder::build_with_auto_env()`、`TestAppServer` auto environment、Docker remote executor、Wine Windows executor、`PathUri`、Shell detection、exec-server protocol 和 skip macros 为证据。官方 OpenAI 文档搜索没有找到一篇覆盖当前仓库内部 remote-test harness 的公开专题，因此具体实现以本提交源码和仓库技能说明为准，不把内部测试细节伪装成公开产品承诺。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分 host OS、target OS 和 execution placement；
2. 解释 local executor 与 remote exec-server 的职责边界；
3. 画出 app-server/exec-server 跨进程执行链；
4. 理解为什么远程测试不能用 host 的 `std::fs` 准备目标文件；
5. 区分 `PathBuf`、`AbsolutePathBuf`、`LegacyAppPathString` 和 `PathUri`；
6. 解释 POSIX path、Windows drive path 和 UNC path；
7. 解释为什么 wire protocol 应避免把 foreign path 当作 host-native `PathBuf`；
8. 区分 lexical normalization、canonicalization 和 symlink resolution；
9. 识别大小写、分隔符、扩展名、换行与编码差异；
10. 理解 zsh/bash/sh、PowerShell 和 cmd 的语法不是可互换的；
11. 解释 argv 与 shell script string 的不同安全边界；
12. 理解 Unix signal/process group 与 Windows process tree 的差异；
13. 使用 auto-env helper 让同一集成测试覆盖 local、Docker 和 Wine；
14. 正确选择 `skip_if_target_windows!`、`skip_if_host_windows!` 等宏；
15. 为 skip 写出能够指导未来移除的原因；
16. 判断一个失败是产品 bug、fixture 放错位置、测试断言写死，还是 Wine 限制；
17. 设计最小但有代表性的跨平台测试矩阵；
18. 避免用大量 `#[cfg]` 把真实跨平台问题隐藏起来。

---

## 2. 先说人话：前台接单，不代表厨房就在前台后面

想象一个餐饮平台：

- 手机 App 在上海接收订单；
- 调度中心在北京决定由哪家店处理；
- 真正做菜的厨房在深圳；
- 菜谱和单位必须按深圳厨房的设备解释。

如果上海 App 说“烤箱第三层”，不能假设北京调度中心也有同一台烤箱。真正需要解释这条指令的是深圳厨房。

Codex 的拆分也类似：

```text
Client / TUI
    ↓
app-server / core
    ↓ 选择 environment，生成 portable intent
exec-server
    ↓ 按目标 OS 解释路径、Shell、进程和 Sandbox
目标文件系统与进程
```

关键不是“请求从哪发出”，而是：

> 谁最终解释路径字符串、启动程序、访问磁盘并终止进程？

这个“最后解释者”所在的环境，才决定 Windows/POSIX 语义。

---

## 3. 三个必须分开的坐标轴

跨平台讨论经常只说“这是 Windows 测试”，信息不够。至少要分三轴。

### 3.1 Host OS

运行当前测试二进制、app-server 或控制逻辑的操作系统。

Rust 编译条件通常描述它：

```rust
#[cfg(windows)]
#[cfg(unix)]
cfg!(target_os = "linux")
```

### 3.2 Target / Executor OS

真正解释命令、路径和文件系统操作的执行环境 OS。

测试中用 `test_target_os()` 表示：

```text
TestTargetOs::Linux
TestTargetOs::MacOs
TestTargetOs::Windows
```

### 3.3 Placement

执行发生在当前进程本地，还是通过远程 exec-server。

```text
Local
Remote Docker
Remote WineExec
```

### 3.4 为什么三轴不能压成一个 bool

当前测试矩阵中的典型组合：

| Host | Placement | Target | 示例 |
|---|---|---|---|
| macOS | Local | macOS | 开发者本机测试 |
| Linux | Local | Linux | Linux CI |
| Windows | Local | Windows | Windows CI |
| Linux | Remote | Linux | Docker exec-server |
| Linux | Remote | Windows | Wine exec-server |

最后一行最重要：

```text
cfg!(windows) == false
test_target_os() == Windows
is_remote_test_environment() == true
is_wine_exec_test_environment() == true
```

如果 API 只有 `is_windows: bool`，你无法知道它指 host 还是 target；如果只有 `is_remote: bool`，你也无法知道远程端是 Linux 还是 Windows。这就是为什么语义明确的 enum 和命名函数比布尔参数更安全。

---

## 4. 当前 TestEnvironment 模型

`codex-rs/core/tests/common/test_environment.rs` 定义：

```rust
enum TestEnvironment {
    Local,
    Docker { container_name: String },
    WineExec,
}
```

它派生目标 OS：

```text
Local      → 当前 host OS
Docker     → Linux
WineExec   → Windows
```

并提供：

- `is_remote()`；
- `target_os()`；
- `path_convention()`；
- `remote_cwd()`；
- Docker container name（只在需要直接编排 fixture 时使用）。

选择来源是环境变量：

```text
CODEX_TEST_ENVIRONMENT=local|docker|wine-exec
CODEX_TEST_REMOTE_EXEC_SERVER_URL=ws://...
CODEX_TEST_REMOTE_ENV_CONTAINER_NAME=...
```

还保留 legacy Docker 变量兼容。

测试对配置做 fail-fast 校验：

- 未配置时默认 local；
- `docker` 缺 container name 会报错；
- 未知值会报错；
- 空 container name 会报错；
- Wine exec 目前只支持 Linux host。

这比“读到奇怪值后悄悄回落 local”更安全，因为后者可能让你以为远程测试通过，实际根本没有走远程链路。

---

## 5. App-server / Exec-server 拆分

简化架构：

```text
┌─────────────────────────────────────┐
│ Host：app-server / core / test      │
│                                     │
│  选择 environment                    │
│  组织 Turn / model / tool loop       │
│  发送 ExecParams / filesystem RPC    │
└────────────────┬────────────────────┘
                 │ WebSocket / JSON-RPC
┌────────────────▼────────────────────┐
│ Target：exec-server                 │
│                                     │
│  报告 shell / cwd / capability       │
│  解释 PathUri                        │
│  组装目标 OS argv / Sandbox          │
│  启动、写 stdin、终止 process         │
│  执行文件系统操作                    │
└────────────────┬────────────────────┘
                 │
          Target filesystem/process
```

### 5.1 Core 不应发送平台 wrapper 细节

`ExecParams` 传递：

- 逻辑 `process_id`；
- `argv`；
- `cwd: PathUri`；
- environment policy；
- TTY/stdin intent；
- portable filesystem sandbox intent；
- managed network intent。

真正的平台 Sandbox wrapper 在 executor 侧解析：

- macOS 可能使用 Seatbelt `/usr/bin/sandbox-exec`；
- Linux 根据 policy 选择 bubblewrap/Landlock 路径；
- Windows 使用对应 sandbox 机制。

如果 app-server 在 Linux 上提前拼出 `/usr/bin/sandbox-exec`，再发送给 Windows executor，架构已经错位。

### 5.2 `process_id` 不是 OS PID

`ExecParams.process_id` 是 client 选择、只在连接/session 内使用的逻辑句柄，不是 Windows/Unix 内核 PID。

这样协议层可以用稳定 ID 做：

- `process/write`；
- output route；
- cancellation；
- lifecycle correlation。

目标 OS 的真实 PID 和 process group/job semantics 由 exec-server 内部负责。

---

## 6. Environment 握手：先问目标会什么

exec-server protocol 的 `EnvironmentInfo` 返回：

```text
shell
cwd: Option<PathUri>
capabilities
```

Shell 信息包含：

- stable name：`zsh`、`bash`、`powershell`、`sh`、`cmd`；
- target-native executable path 或 command name。

Capability 当前包括诸如：

- executor-local network proxy launch；
- capability discovery 是否应用每个 root 的 filesystem sandbox。

这体现一个重要兼容原则：

> Client 不能因为自己支持新字段，就假设远程 executor 也支持；先根据握手 capability gate。

### 6.1 Status 查询不应暗中启动环境

app-server v2 的 environment status 有：

```text
Ready
Pending
Disconnected
Unknown
```

当前注释强调 status 是观察，不应为查询状态而启动或恢复环境。已经连接的远程环境可以走现有连接做 fail-fast probe；未启动的 lazy environment 仍是 Pending。

这是 control plane 常见原则：

```text
read status ≠ mutate/start resource
```

否则一个 UI 刷新动作可能无意创建昂贵远程环境。

---

## 7. 路径是跨 OS 最容易错的协议字段

在本机代码中，`PathBuf` 很自然。但它按当前 host 的路径规则解释内容。

假设 Linux app-server 收到：

```text
C:\Users\alice\repo\README.md
```

若立刻构造 host-native `PathBuf` 并调用 Linux 的：

```rust
path.is_absolute()
path.parent()
path.join(...)
```

结果可能完全不符合 Windows 语义。对 Linux 来说，`C:` 不一定是 drive root，反斜杠也不一定是 separator。

所以要先问：

> 这个路径属于当前 host 的文件系统，还是另一个 executor 的文件系统？

---

## 8. 四种路径类型的职责

### 8.1 `PathBuf`

Rust 标准库的 host-native 路径容器。

适合：

- 当前进程会直接访问的路径；
- 临时目录；
- local config file；
- `Command::current_dir` 的本地输入。

不适合：

- Linux 进程内部保存一个 Windows executor path 并按 Windows 规则操作。

### 8.2 `AbsolutePathBuf`

Codex wrapper，保证路径是绝对并规范化，但：

- 不保证文件存在；
- 不保证已经 canonicalize；
- 仍然是当前 host-native `PathBuf` 语义。

适合：

- host-local absolute path；
- 已知由当前进程直接访问的 cwd/runtime path。

### 8.3 `LegacyAppPathString`

app-server API 为兼容现有 client 保留的 UTF-8 native-path 字符串。

它可以暂存：

- POSIX spelling；
- Windows spelling；
- 相对 path text；
- 当前 host 无法原生解释的 foreign path。

在协议边界应转换到 `PathUri` 后再做跨平台内部操作。

### 8.4 `PathUri`

不可变、跨平台的 `file:` URI：

```text
file:///home/alice/repo/src/main.rs
file:///C:/Users/alice/repo/src/main.rs
file://server/share/repo/file.txt
```

它的 lexical 操作不依赖当前运行 Codex 的 host OS：

- `basename()`；
- `parent()`；
- `join()`；
- Windows drive/UNC root 识别；
- Windows ASCII case-insensitive identity；
- POSIX case-sensitive identity。

exec-server protocol 的路径字段优先使用 `PathUri`。

---

## 9. 为什么 `file:` URI 有用

统一 URI separator：

```text
/
```

即使表示 Windows path，也是：

```text
file:///C:/repo/src/lib.rs
```

而不是把 JSON 中的反斜杠转义层层叠加：

```json
"C:\\repo\\src\\lib.rs"
```

### 9.1 URI 不是“把反斜杠替换成斜杠”

还要处理：

- drive letter；
- UNC authority/share；
- percent encoding；
- 空格和 Unicode；
- POSIX 非 UTF-8 byte；
- root boundary；
- 大小写 identity。

应使用集中类型的方法，不要在业务文件中新增一次性字符串 helper。

### 9.2 Windows identity 与 POSIX identity

当前 `PathUri` equality/hash：

- Windows path 对 ASCII case 不敏感；
- POSIX path 保持 case-sensitive。

因此：

```text
file:///C:/Repo/File.txt
file:///c:/repo/file.txt
```

按 Windows identity 可以等价；而：

```text
file:///repo/File.txt
file:///repo/file.txt
```

按 POSIX identity 不应自动等价。

这不是 UI 格式细节，而会影响 allowlist、cache key、workspace root 和 sandbox boundary。

---

## 10. App-server 兼容边界与目标状态

当前迁移规则是：

```text
旧 App Client
  ↔ LegacyAppPathString（兼容 wire spelling）
  → PathUri（内部跨平台操作）
  ↔ Exec-server protocol（PathUri）
```

原因：

- 已有 app-server client 仍发送/接收 native-path string；
- app-server 必须保留 foreign-platform path；
- exec-server API 已使用 `file://` URI；
- 本地专用配置仍可用 `AbsolutePathBuf` / `PathBuf`；
- model tool argument 仍可能是原始相对/绝对字符串，需要 feature-specific handling。

### 10.1 为什么不一次性全改

路径是外部协议的一部分。立即把所有 string 改 URI 可能破坏：

- 旧 desktop/client；
- rollout 恢复；
- 数据库存储；
- model-visible text；
- UI 显示；
- relative tool argument。

迁移要求 local-only 行为和 model-visible text 不应无故变化，也暂不把 URI 写入 rollout/database 等持久存储。这是兼容迁移，不是全局机械替换。

---

## 11. 安全相关路径要 Fail Closed

同一个转换错误在不同场景应有不同策略。

### 11.1 Security-relevant path

例如：

- writable root；
- denied carveout；
- approval grant path；
- sandbox cwd。

无法把 path 转成目标语义时，应 fail closed：拒绝执行或不给权限。

绝不能：

```text
转换失败
→ 当作整个磁盘可写
```

### 11.2 UI / Diagnostics path

如果只是错误消息或界面标签，无法转换时可以 fail open：保留原始 string 或显示 URI。

```text
安全授权：不确定 → 拒绝
诊断显示：不确定 → 尽量展示原文
```

这解释了为什么同一 path type 可能有严格转换 API和 best-effort rendering API。

---

## 12. Lexical、Absolute、Canonical 不是同义词

### 12.1 Lexical operation

只按字符串 segment 规则计算：

```text
/repo/a/../b → /repo/b
```

不访问文件系统。

### 12.2 Absolute path

具有 root：

```text
/repo/file
C:\repo\file
\\server\share\file
```

绝对不等于存在。

### 12.3 Canonicalization

访问文件系统，解析：

- symlink；
- `.` / `..`；
- 系统 alias；
- 实际 casing/namespace；
- 必须存在的 ancestor。

### 12.4 为什么远程端才有资格 canonicalize

Linux host 无法 canonicalize Windows executor 的 `C:\repo`。即使字符串能转换，也没有那套文件系统。

所以：

```text
跨 wire：PathUri / lexical identity
到 executor：转 native path
executor filesystem：存在性检查 / canonicalize / symlink handling
```

---

## 13. Symlink 在 Windows 与 Unix 也不同

Unix 常用统一的 symlink 创建 API；Windows 需要区分目标是 file 还是 directory，并可能受到权限/developer mode 影响。

`exec-server/src/local_file_system.rs` 的复制逻辑明确分支：

```text
Unix    → std::os::unix::fs::symlink
Windows → symlink_dir 或 symlink_file
```

测试不要假设：

- 创建 symlink 在所有 runner 都被允许；
- file 和 directory link API 相同；
- canonical path 与用户输入 spelling 相同；
- host 创建的 symlink fixture 会出现在 remote filesystem。

如果测试跳过 remote，因为 fixture 是 host-local external symlink，要写出这个具体原因，而不是“Windows flaky”。

---

## 14. 远程 Fixture 必须在远程文件系统创建

这是最常见的测试错误之一。

错误示例：

```rust
let path = tempdir.path().join("input.txt");
std::fs::write(&path, "hello")?;
// 然后让 remote exec-server 读取 path
```

这份文件存在于 host，不一定存在于 Docker/Wine target。

正确思路：

```rust
let path_uri = test.executor_environment().cwd_uri().join("input.txt")?;
test.fs().write_file(&path_uri, bytes, None).await?;
```

当前 `view_image` 等测试 helper 会通过 executor filesystem abstraction：

- create directory；
- write file；
- 使用 `PathUri`；
- 在 selected environment cwd 下创建 fixture。

### 14.1 Host fixture 与 target fixture 的区别

| Fixture | 创建方式 | 谁读取 |
|---|---|---|
| mock Responses server | Host socket/process | Host core 连接 |
| `CODEX_HOME` config | Host `std::fs` | Host app-server 读取 |
| 远程 workspace file | executor filesystem RPC | Remote tool/command 读取 |
| Wine `C:\...` cwd | PathUri + remote FS | Windows exec-server |

“测试涉及远程”不代表所有东西都远程。要按所有权逐项放置。

---

## 15. Shell 不是一个抽象的“命令解释器”

Codex 支持检测：

```text
Zsh
Bash
Sh
PowerShell
Cmd
```

不同 Shell 在以下方面不同：

| 问题 | POSIX Shell | PowerShell | cmd.exe |
|---|---|---|---|
| 变量 | `$NAME` | `$env:NAME` | `%NAME%` |
| 命令串联 | `&&`, `;` | `;`, 版本相关 `&&` | `&&`, `&` |
| 空设备 | `/dev/null` | `$null` | `NUL` |
| 路径引用 | 单/双引号规则 | PowerShell quote/escape | cmd quote/escape |
| 环境设置 | `NAME=value cmd` | `$env:NAME='x'` | `set NAME=x` |
| 查找命令 | `command -v` | `Get-Command` | `where` |

因此这段不能直接发送给 Windows cmd：

```sh
sleep 1; printf late
```

exec-server 测试中，Windows 用类似：

```text
cmd.exe /C "ping -n 3 127.0.0.1 >NUL && echo late"
```

而 Unix 用：

```text
/bin/sh -c "sleep 1; printf late"
```

这不是测试噪声，而是目标 Shell 的真实产品语义。

---

## 16. Argv 与 Shell String 的区别

### 16.1 直接 argv

```rust
Command::new("git").args(["status", "--short"])
```

操作系统收到程序和独立参数，不需要再经过 Shell 解析。

### 16.2 Shell script string

```rust
Command::new("bash").args(["-lc", "git status --short"])
```

第三个参数还会被 Bash 解析一次。

### 16.3 为什么跨平台时优先结构化 argv

- 少一层 quote/escape；
- 空格路径更稳定；
- 不依赖 `&&`、pipe、redirect 语法；
- 减少 injection surface；
- 更容易精确测试。

只有需要 Shell feature 时才发送 script string，并且必须用目标 environment 报告的 Shell 语义构造。

模型生成的 shell command 天然需要 shell-aware 处理，但 runtime 内部不应把能用 argv 表达的调用无故拼接成字符串。

---

## 17. Shell Detection 必须发生在目标环境

`EnvironmentInfo::local()` 在 exec-server 一侧检测默认 Shell并报告给 client。

当前 fallback 大意：

- Windows 优先 PowerShell，最终可回退 `cmd.exe`；
- macOS 用户 Shell不可用时倾向 zsh，再 Bash；
- 其他 Unix 倾向 Bash，再 zsh，最终 `/bin/sh`。

为什么不能 app-server 自己检测？

```text
Linux app-server 检到 /bin/bash
Windows exec-server 实际只有 PowerShell/cmd
```

这时 host 检测结果完全无关。

### 17.1 Path 字段为什么仍是 String

Shell executable 可能只是 command name：

```text
cmd.exe
pwsh
zsh
```

不保证是绝对 path，所以 `ShellInfo.path` 不是 `PathUri`。类型选择应表达数据真实不变量，不能为了“所有路径都 URI”硬把 command name 伪装成 absolute path。

---

## 18. PowerShell Encoding 是真实兼容问题

Windows PowerShell 的 console output encoding 与 Unix UTF-8 假设可能不同。

`codex-rs/shell-command/src/powershell.rs` 提供前缀：

```powershell
try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}
```

并确保同一前缀不会重复插入。

这解决的是：

- 非 ASCII 输出被错误解码；
- 流式字节转换产生乱码；
- snapshot 在 Windows 不稳定；
- error message 无法搜索。

跨平台测试不能只用 `hello`。至少应有包含中文、emoji 或带重音字符的用例，否则编码 bug 可以长期隐藏。

---

## 19. 环境变量的跨平台差异

### 19.1 名称大小写

Windows 环境变量名通常大小写不敏感；Unix 通常大小写敏感。

当前 filesystem sandbox 环境过滤对 Windows `PATH` 使用 case-insensitive 比较。

错误做法：

```text
只过滤 "PATH"
但 Windows input 使用 "Path"
```

### 19.2 值的分隔符

`PATH` entry separator：

```text
Unix    → :
Windows → ;
```

应使用平台 API，例如 `std::env::split_paths` / `join_paths`，而不是字符串 `.split(':')`。

### 19.3 Inherit 与 Set

exec protocol 传递 `ExecEnvPolicy` 和显式 env。要分清：

- host 环境；
- app-server process 环境；
- remote exec-server 环境；
- child process environment。

不应默认把 host `PATH` 原样发送到 foreign target；其中目录可能完全不存在。

---

## 20. 可执行文件发现

跨平台差异包括：

- Windows 常有 `.exe`、`.cmd`、`.bat`；
- Windows 使用 `PATHEXT` 影响命令查找；
- Unix executable bit 决定能否执行；
- shebang 在 Unix 有意义，Windows 处理方式不同；
- 同名程序可能在不同 Shell 下解析不同。

测试中如果要启动 workspace binary，仓库要求使用 `codex_utils_cargo_bin::cargo_bin` 等适配 Cargo/Bazel runfiles 的工具，避免依赖当前目录或 `CARGO_MANIFEST_DIR`。

### 20.1 不要把 `.exe` 到处手工拼接

错误：

```rust
let binary = format!("tool{}", if cfg!(windows) { ".exe" } else { "" });
```

如果这是 target binary，而判断发生在 Linux host + Wine target，就会漏掉 `.exe`。

应让 build/runfiles helper 或明确的 target metadata 决定，不用 host `cfg!` 猜 foreign artifact 名称。

---

## 21. Newline 与文本断言

常见换行：

```text
LF   → \n
CRLF → \r\n
```

不要不加思考地把所有输出 `replace("\r\n", "\n")`：

- 若产品契约要求保留原始字节，这会隐藏 bug；
- 若只是比较逻辑行，`lines()` 或明确 normalization 更合适；
- binary output 绝不能按文本换行处理；
- PTY output 可能还有终端控制序列。

测试应先定义断言层级：

| 目标 | 断言方式 |
|---|---|
| 原始文件字节 | `Vec<u8>` 完整相等 |
| 跨平台逻辑文本 | 明确规范化后比较 |
| UI snapshot | 使用 UI 层约定的 normalization |
| 协议 JSON | 反序列化结构深比较 |

---

## 22. File Permission 与 Executable Bit

Unix mode bit 与 Windows ACL/attributes 不是一套模型。

风险示例：

- 测试用 `chmod +x`，Windows 没有等价语义；
- 断言 `0o755`，只在 Unix 有意义；
- 假设删除 open file 一定成功；
- 假设 rename 可覆盖正在使用的 executable；
- 假设 read-only attribute 等价 Unix permission bit。

平台专有测试可以用 `#[cfg(unix)]`，但产品功能若承诺跨平台，应为 Windows 定义等价行为，而不是直接不编译。

### 22.1 `ExecutableFileBusy`

当前 app-server test harness 启动 binary 时，会对 `ExecutableFileBusy` 做有限重试。这类错误在并发构建/替换 executable 场景可能出现。有限重试必须：

- 只针对明确可恢复错误；
- 有次数上限；
- 最终保留原错误上下文。

---

## 23. 进程终止：Signal 不是跨平台协议

Unix 常见：

- PID；
- process group；
- `SIGTERM`；
- `SIGKILL`；
- signal 导致的 exit status。

Windows 常见：

- process handle；
- process tree；
- console control；
- Job Object 或 `taskkill /T /F`；
- 不同 exit code 语义。

exec-server connection cleanup 当前按平台分支：

```text
Unix    → terminate/kill process group
Windows → taskkill /PID <pid> /T /F
fallback → kill direct child
```

这说明“停止一个命令”不能只实现：

```rust
child.kill().await
```

否则 shell 启动的孙进程可能留下。

### 23.1 测试什么

- direct child 退出；
- shell child/grandchild 不残留；
- stdout/stderr reader 收尾；
- logical process 获得 terminal event；
- repeated cancellation 幂等；
- connection drop 不泄漏 process tree。

---

## 24. TTY、PTY 与 ConPTY

交互式命令与普通 pipe 不同：

- Unix 使用 PTY；
- Windows 使用 ConPTY；
- terminal size/resize 行为不同；
- echo、line discipline 和 signal 传播不同；
- ANSI support 与 code page 可能不同。

当前 Wine remote test README 明确说 ConPTY/TTY 尚未覆盖。这是测试能力边界，不等于产品一定不支持，也不应伪装成已经被 Wine 测试证明。

遇到 Wine 中的 TTY 失败时，先问：

1. Windows 产品语义本身错误？
2. Wine 没模拟完整 ConPTY？
3. Bazel fixture 没提供 console？
4. 测试能否在 native Windows CI 覆盖？

不要直接把所有 Windows TTY 测试归类为 flaky。

---

## 25. Sandbox Enforcement 必须在 Executor

Sandbox policy 是 portable intent，最终 enforcement 必须发生在有目标文件系统和进程的 exec-server。

```text
Core:
  workspace roots / read / write / network intent
          ↓
Exec-server:
  把 PathUri 转 target-native path
  加入平台 system read roots
  选择 Seatbelt / bubblewrap / Landlock / Windows sandbox
  启动 child
```

### 25.1 为什么 app-server 不能只检查一次

如果 app-server 在 Linux 上检查：

```text
C:\repo\file 是否位于 C:\repo
```

但使用 POSIX path rules，安全判断可能错误。

真正 enforcement 需要目标 Windows semantics，并在进程启动前 fail closed。

### 25.2 平台默认 read roots

Windows sandbox 可能需要系统目录读取：

```text
C:\Windows
C:\Program Files
C:\Program Files (x86)
C:\ProgramData
```

Linux/macOS 所需系统 roots 不相同。portable policy 可以表达“允许平台默认系统读取”，具体目录由 executor materialize。

---

## 26. Auto-env：让一份测试自动选择执行位置

Core 集成测试推荐：

```rust
let test = test_codex()
    .build_with_auto_env(&responses_server)
    .await?;
```

行为：

- 未配置或 `local` → 临时 local environment；
- `docker` → 连接 `CODEX_TEST_REMOTE_EXEC_SERVER_URL`，target Linux；
- `wine-exec` → 连接 Windows exec-server。

只有自动选择的 environment 会注册。若测试确实要同时选择 remote/local，使用更明确的 `build_with_remote_and_local_env`。

### 26.1 App-server 测试

当前源码的标准方式是：

```rust
let mut app = TestAppServer::builder()
    .with_codex_home(codex_home.path())
    .build()
    .await?;

let id = app
    .send_thread_start_request_with_auto_env(ThreadStartParams::default())
    .await?;
```

Builder 默认准备 auto environment；`without_auto_env()` 才显式关闭。

`send_thread_start_request_with_auto_env()` 要求调用者把 `params.environments` 留为 `None`，helper 再注入 fixture selection。这样测试不能一边声称 auto-env，一边偷偷覆盖为固定 local selection。

### 26.2 指南与源码不一致时怎么办

仓库的 `remote-tests` 技能文本仍提到一个旧式 `new_with_auto_env()` 名称，但当前源码搜索不到该方法，现有测试使用 builder `.build()`。教学文档应以当前 commit 源码为准，并把指南差异视为待更新的项目文档债务，而不是发明不存在的 API。

---

## 27. Auto-env 如何创建目标 CWD

当前 TestEnvironment 为每次测试生成隔离路径：

```text
Docker:
  file:///tmp/codex-core-test-cwd-<instance>

Wine Windows:
  file:///C:/codex-core-test-cwd-<instance>
```

然后通过 executor filesystem 创建 directory。

### 27.1 Wine 的 Host-compatible Projection

部分旧 Core config 仍要求 host `AbsolutePathBuf`。在 Linux host + Windows target 下，测试 harness 临时把：

```text
file:///C:/repo
```

投影为 Linux 可保存的：

```text
/C:/repo
```

注释明确这是兼容桥梁，未来目标是让相关类型直接支持跨平台 URI。不要把 `/C:/...` 当成用户真正的 Windows path spelling；它是 host test harness 的中间投影。

### 27.2 测试 expected text 也要用 Target spelling

例如 missing image error 在 Wine 下应显示：

```text
C:\codex-core-test-cwd-...\missing\example.png
```

测试会从 `PathUri` 推断 Windows native string，而不是用 Linux host 的 `.display()` 写死 `/C:/...`。

---

## 28. Docker Remote Executor 如何启动

`scripts/test-remote-env.sh` 是 source-only setup script。

简化流程：

```text
检查 Docker daemon 和 Cargo
→ 构建 codex CLI binary
→ 启动 Ubuntu 24.04 privileged test container
→ 安装 python3 / zsh / bubblewrap
→ 复制 codex binary 到 container
→ 启动 codex exec-server WebSocket listener
→ 等待端口 ready
→ 导出 remote URL / container / environment 变量
→ 测试运行
→ cleanup 删除 container 并 unset 变量
```

### 28.1 为什么要 `source`

脚本需要把环境变量导出到调用者当前 Shell。直接执行会在子进程结束时丢失变量，所以脚本检测不是 source 就报错。

### 28.2 为什么要 Cleanup Trap

推荐命令使用：

```bash
trap codex_remote_env_cleanup EXIT
```

否则测试失败/中断时可能残留：

- privileged container；
- exec-server process；
- 环境变量；
- fixture directory。

这是测试 RAII 的 Shell 版本：无论成功失败都清理。

### 28.3 为什么容器需要特殊权限

当前脚本为 bubblewrap mount propagation 使用 privileged container 并关闭默认 seccomp。它是专用测试容器配置，不应直接复制成生产部署建议。

---

## 29. Wine Windows Executor 如何运行

Wine 模式：

```text
Linux x86_64 host
  ├── app-server/core test binary（Linux）
  └── pinned Wine
        └── Windows exec-server binary
```

Runner：

1. 启动隔离 Wine exec-server；
2. 得到 WebSocket URL；
3. 给测试进程设置 `CODEX_TEST_ENVIRONMENT=wine-exec`；
4. 设置 remote exec-server URL；
5. 移除 stale Docker variables；
6. 运行同一 integration test binary；
7. 退出时停止环境。

### 29.1 为什么只有 Bazel

Windows exec-server 需要 cross-platform build artifact 和 runfiles。当前仓库把 Wine shared suites 放在 Bazel target 中：

```text
//codex-rs/core:core-all-wine-exec-test
//codex-rs/app-server:app-server-all-wine-exec-test
```

### 29.2 当前边界

- host 必须 x86_64 Linux；
- Windows target 也限 x86_64；
- 每个 test process 使用 fresh `WINEPREFIX` 和 isolated wineserver；
- ConPTY/TTY 未覆盖；
- Wine 仍依赖 host 提供兼容 glibc/shared objects。

这些限制要在失败归因时保留，不能把 Wine 等同一台完整 native Windows 机器。

---

## 30. 五种 Skip Macro 怎样选

原则：先尝试让测试在所有组合运行；确实不能运行时，按“造成失败的维度”选择最窄 skip。

### 30.1 `skip_if_target_windows!`

目标 Windows 语义导致失败。

示例：断言写死 POSIX apply-patch error path。

它会跳过：

- Windows host local；
- Linux host + Wine Windows target。

不会跳过 Linux host + Docker Linux target。

### 30.2 `skip_if_wine_exec!`

只有 Wine runner 能力不足，native Windows 产品语义可能正常。

示例：依赖 Wine 未覆盖的特定 TTY/fixture 行为。

不要用它隐藏所有 Windows failure。

### 30.3 `skip_if_host_windows!`

测试控制逻辑本身不能在 Windows host 运行，例如 host fixture 依赖 Unix-only daemon/script。

它不会跳过 Linux host + Wine target，因为 host 仍是 Linux。

### 30.4 `skip_if_remote!`

测试本质依赖 host-local fixture，尚未通过 executor filesystem/transport 迁移。

原因应具体：

```text
"command hooks use host-local script and log paths"
```

而不是：

```text
"remote does not work"
```

### 30.5 `skip_if_no_remote_env!`

测试专门验证 remote-only 行为，本地运行没有意义。

例如确认 remote filesystem/connection 特性。

---

## 31. Skip 是债务记录，不是修复

好的 skip reason 应回答：

- 哪个 fixture/behavior 依赖当前环境；
- 是产品限制还是 harness 限制；
- 未来如何移除；
- 哪种其他测试覆盖了缺口。

坏例子：

```rust
skip_if_remote!(Ok(()), "flaky");
```

好例子：

```rust
skip_if_remote!(
    Ok(()),
    "the external symlink fixture is host-local",
);
```

后者给未来修改者一个明确迁移方向：把 symlink fixture 建到 executor filesystem，或提供 remote-aware helper。

### 31.1 Skip Ratchet

Review 新 skip 时要问：

1. 失败是否暴露真实产品 bug；
2. 能否用 target-aware expected value 修复；
3. 能否通过 remote FS 准备 fixture；
4. 能否缩小到 Wine-only；
5. 是否有 issue/解除条件。

跳过测试会缩小证据范围，必须像依赖安全例外一样可追踪。

---

## 32. `#[cfg]` 的正确与错误用途

### 32.1 正确用途

- 引用平台专属标准库模块；
- 实现 Unix symlink 与 Windows symlink；
- 调用 Seatbelt/bubblewrap/taskkill；
- 平台根本没有对应 API；
- 编译平台专属测试。

### 32.2 错误用途

```rust
if cfg!(windows) {
    expected_target_path = ...
}
```

当代码运行在 Linux host、目标是 Wine Windows 时，这个判断错误。

应该根据：

```rust
match test_target_os() {
    TestTargetOs::Windows => ...,
    TestTargetOs::Linux | TestTargetOs::MacOs => ...,
}
```

### 32.3 编译条件与运行条件

`#[cfg(windows)]` 会让代码在非 Windows 根本不存在；`cfg!(windows)` 只是编译成 bool。

如果函数使用 `std::os::windows::*`，需要 `#[cfg(windows)]` 保护导入和实现；如果只是选择 target expected spelling，通常需要 runtime target enum，而不是 compile-time host cfg。

---

## 33. 一份测试怎样覆盖三种 Environment

教学化例子：模型调用 shell 创建文件，再读取内容。

```rust
#[tokio::test]
async fn shell_creates_and_reads_workspace_file() -> Result<()> {
    let server = responses::start_mock_server().await;
    mount_model_tool_sequence(&server).await;

    let mut builder = test_codex();
    let test = builder.build_with_auto_env(&server).await?;

    let file_uri = test.executor_environment()
        .cwd_uri()
        .join("result.txt")?;

    test.submit(...).await?;

    let bytes = test.fs().read_file(&file_uri, None).await?;
    assert_eq!(bytes, b"done\n");
    Ok(())
}
```

同一 binary 在不同配置下：

```text
无环境变量       → local
Docker setup      → Linux remote
Wine Bazel runner → Windows remote
```

要做到这一点，测试必须避免：

- host `tempdir` 作为 target workspace；
- `/bin/sh` 写死；
- `/tmp` 写死；
- POSIX error string 写死；
- `std::fs` 读 remote output；
- host `cfg!` 推断 target。

---

## 34. 失败分类决策树

当 local 通过、remote 失败时：

```text
1. 测试真的选择 remote 了吗？
   ├─ 否 → 检查 CODEX_TEST_ENVIRONMENT / URL / helper
   └─ 是
      ↓
2. Fixture 在哪？
   ├─ 只在 host → 用 executor filesystem 创建
   └─ target 可见
      ↓
3. Expected value 用谁的 OS 规则？
   ├─ host cfg/display → 改用 target OS / PathUri
   └─ target-aware
      ↓
4. Command 用目标 Shell 吗？
   ├─ 写死 bash/powershell → 使用 EnvironmentInfo
   └─ 正确
      ↓
5. 是 transport/lifecycle 问题吗？
   ├─ connection/status/cancel → 检查 exec-server events
   └─ 否
      ↓
6. 是 target 产品行为还是 Wine 限制？
   ├─ native Windows 也失败 → 产品 bug
   ├─ 仅 Wine → harness debt / narrow skip
   └─ 未知 → 补最小 native/target test
```

### 34.1 最先记录的证据

- host OS；
- selected TestEnvironment；
- target OS；
- environment ID；
- exec-server URL 是否存在（不要泄漏 token）；
- target cwd URI；
- reported Shell；
- last request/response/process event；
- fixture 创建位置；
- raw error 与 target-rendered path。

只写“Windows CI failed”不足以定位。

---

## 35. 常见失败案例

### 案例 A：Remote 测试找不到文件

症状：local success，Docker/Wine `No such file`。

常见根因：fixture 用 host `std::fs::write`。

修复：通过 selected executor filesystem 写入 `PathUri`。

### 案例 B：Wine expected path 是 `/C:/...`

症状：产品输出 `C:\...`，测试 expected `/C:/...`。

根因：把 host compatibility projection 当 target-native display。

修复：从 `PathUri` 使用 inferred/explicit Windows rendering。

### 案例 C：Linux host + Wine 仍发送 Bash

症状：Windows target 报 `/bin/bash` 不存在。

根因：用 host shell detection 或 `cfg!(windows)`。

修复：使用 exec-server 报告的 Shell。

### 案例 D：取消后孙进程残留

症状：测试结束仍有 server/child 占端口。

根因：只 kill direct shell child。

修复：Unix process group / Windows process tree cleanup，并验证 terminal event。

### 案例 E：只有 Wine TTY 失败

症状：native Windows pass，Wine remote fail。

根因候选：ConPTY 未覆盖。

修复：保留 native Windows coverage，对 Wine 使用最窄、有具体原因的 skip；不要跳过所有 Windows target。

### 案例 F：大小写路径绕过 root 判断

症状：`C:\Repo` 与 `c:\repo` 被当成两个 root。

根因：用 POSIX/字符串 equality 比 Windows path。

修复：使用 `PathUri` Windows identity 或 executor-native安全检查。

---

## 36. 测试矩阵怎样控制成本

不是每个测试都需要在所有 OS、所有 Shell、local/remote、TTY/non-TTY 的笛卡尔积运行。

分层：

### 36.1 纯逻辑测试

适合所有 host 快速运行：

- PathUri parse/join/parent；
- protocol serde；
- environment config parse；
- Shell type detection；
- target OS mapping。

### 36.2 平台组件测试

只在对应 OS：

- symlink API；
- process group/taskkill；
- Seatbelt/bubblewrap/Windows sandbox；
- PTY/ConPTY。

### 36.3 Shared integration suite

优先 auto-env：

- model → tool → exec → result；
- filesystem operations；
- cancellation；
- resume；
- path display；
- environment context。

### 36.4 少量专门 remote test

- WebSocket connection；
- remote-only FS behavior；
- reconnect/status；
- cross-host/target path。

目标是让大多数 feature test 默认可被 remote harness 重放，而不是为 remote 复制一套几乎相同的测试文件。

---

## 37. CI 中当前覆盖什么

`.github/workflows/README.md` 描述：

- PR 主要由 Bazel 验证 Rust target；
- 快速 Cargo PR checks 覆盖 Linux/macOS/Windows 的部分 lint；
- post-merge full Cargo workflow 运行完整 clippy/nextest matrix；
- native Windows ARM64 archive 在 Windows x64 cross-compile，再到 native ARM64 shard 重放；
- Linux remote-env tests 位于重型 post-merge 路径。

Wine shared tests属于 Bazel 目标，可在 x86_64 Linux 运行。

### 37.1 CI 通过仍不代表所有组合已覆盖

要确认具体 test 是否：

- 调用 auto-env builder；
- 被 target skip；
- 被 Wine skip；
- 只在某个平台编译；
- 进入实际 CI target；
- 受网络/sandbox 条件跳过。

“仓库有 Windows CI”不能证明某条 remote Windows path 被执行过。

---

## 38. 新增跨平台功能的实现流程

### 第 1 步：标出 ownership boundary

这个逻辑属于：

- app-server control plane；
- core portable intent；
- exec-server target execution；
- filesystem adapter；
- UI display。

### 第 2 步：标出 path lifetime

- host-local；
- target-local；
- wire；
- model generated relative string；
- persistent historical path。

据此选择 path type。

### 第 3 步：避免 host 推断 target

需要 target 信息时从：

- `EnvironmentInfo`；
- `PathUri`；
- `test_target_os()`；
- explicit environment selection。

不要用 host `cfg!` 猜。

### 第 4 步：将 OS-specific materialization 下推

例如：

- sandbox wrapper；
- system read roots；
- Shell command；
- process tree termination；
- native path conversion。

放到 exec-server/目标适配层。

### 第 5 步：先写 local + auto-env integration test

使用 remote FS helper，避免固定 OS string。

### 第 6 步：运行目标测试矩阵

至少考虑：

- local host；
- Docker Linux remote；
- Wine Windows remote；
- native Windows（Wine 边界之外）；
- macOS platform-specific sandbox。

### 第 7 步：审查 skip

新增 skip 必须说明原因和替代 coverage。

### 第 8 步：审查 error text 和 UI

协议结构深比较；用户可见 path 使用目标 native spelling；安全错误保留 fail-closed。

---

## 39. Debugging Checklist

### Environment

- [ ] `CODEX_TEST_ENVIRONMENT` 实际值是什么？
- [ ] remote URL 是否被设置？
- [ ] Docker stale 变量是否在 Wine runner 被移除？
- [ ] environment handshake 是否完成？
- [ ] status 是 Ready、Pending、Disconnected 还是 Unknown？

### OS and placement

- [ ] host OS 是什么？
- [ ] target OS 是什么？
- [ ] local/remote 是什么？
- [ ] 是否错误使用 `cfg!(windows)` 判断 target？

### Paths

- [ ] path 属于 host 还是 target？
- [ ] wire 上是否使用 `PathUri`/兼容 boundary type？
- [ ] fixture 是否创建在 target filesystem？
- [ ] expected output 是否按 target spelling 渲染？
- [ ] Windows case/UNC/drive root 是否正确？
- [ ] canonicalization 是否在正确 filesystem 上发生？

### Commands

- [ ] Shell 来自 target EnvironmentInfo 吗？
- [ ] 能否用 argv 代替 script string？
- [ ] quote、redirect、null device 是否 target-specific？
- [ ] PowerShell output 是否 UTF-8？

### Process lifecycle

- [ ] cancellation 到达 exec-server 吗？
- [ ] direct child 和 descendants 都终止吗？
- [ ] terminal event 只出现一次吗？
- [ ] connection drop 是否清理 process tree？

### Test evidence

- [ ] auto-env helper 真正被调用吗？
- [ ] 是否被 skip macro 跳过？
- [ ] skip 维度选对了吗？
- [ ] Wine 限制与 native Windows bug 区分了吗？
- [ ] 失败日志包含 target cwd URI 和 shell 吗？

---

## 40. 源码阅读路线

### 路线 A：测试环境选择

1. `codex-rs/core/tests/common/test_environment.rs`
2. `codex-rs/core/tests/common/test_environment_tests.rs`
3. `codex-rs/core/tests/common/test_codex.rs`
4. `codex-rs/core/tests/common/lib.rs`

重点：

- local/docker/wine mapping；
- host vs target；
- remote cwd；
- auto-env builder；
- skip macro conditions。

### 路线 B：App-server auto environment

1. `codex-rs/app-server/tests/common/test_app_server.rs`
2. `codex-rs/app-server/tests/suite/v2/auto_env.rs`
3. `codex-rs/app-server-protocol/src/protocol/v2/turn.rs`
4. `codex-rs/app-server-protocol/src/protocol/v2/environment.rs`

重点：

- builder 默认 auto-env；
- explicit environment 与 auto-env 冲突；
- environment selection 进入 `thread/start`；
- cwd 怎样进入 model-visible environment context。

### 路线 C：路径迁移

1. `codex-rs/utils/absolute-path/src/lib.rs`
2. `codex-rs/utils/path-uri/src/lib.rs`
3. `codex-rs/utils/path-uri/src/api_path_string.rs`
4. `codex-rs/utils/path-uri/src/tests.rs`
5. `codex-rs/exec-server/src/local_file_system_path_uri_tests.rs`

重点：

- host-native absolute path；
- URI segment operation；
- Windows case equality；
- legacy API string conversion；
- foreign path tests。

### 路线 D：命令与进程

1. `codex-rs/shell-command/src/shell_detect.rs`
2. `codex-rs/shell-command/src/powershell.rs`
3. `codex-rs/exec-server-protocol/src/protocol.rs`
4. `codex-rs/exec-server/src/server/processor.rs`
5. `codex-rs/exec-server/src/connection.rs`
6. `codex-rs/exec-server/src/process_sandbox_tests.rs`

重点：

- target Shell report；
- UTF-8 prefix；
- portable ExecParams；
- target wrapper；
- Unix/Windows process tree termination。

### 路线 E：Remote runner

1. `scripts/test-remote-env.sh`
2. `codex-rs/exec-server/testing/wine_remote_test_runner.rs`
3. `codex-rs/core/tests/remote_env_windows/README.md`
4. `.github/workflows/rust-ci-full-nextest-platform.yml`
5. `.github/workflows/README.md`

重点：

- Docker lifecycle；
- Wine environment injection；
- Bazel-only Windows cross build；
- CI 实际覆盖和边界。

---

## 41. 实战练习

### 练习 1：三轴分类

对以下环境写出 host、placement、target：

1. macOS laptop local test；
2. Linux CI Docker remote；
3. Linux Bazel Wine exec；
4. Windows ARM64 native shard。

再写出每种情况下 `cfg!(windows)` 与 `test_target_os()` 是否相同。

### 练习 2：修复 Host-local Fixture

找一个 `skip_if_remote!` 测试：

1. 阅读 skip reason；
2. 找出 host-local file/script/socket；
3. 设计 executor FS 或 transport-aware fixture；
4. 判断是否还能保留 local behavior；
5. 写出移除 skip 后需新增的断言。

不要求实际改代码，先画 ownership map。

### 练习 3：Path Type Review

对每个字段选择类型并解释：

1. app-server 的旧 client cwd；
2. exec-server RPC cwd；
3. host `CODEX_HOME/config.toml`；
4. model tool argument `path`；
5. remote workspace root；
6. shell command name `cmd.exe`。

答案不应全部是 `PathBuf` 或全部是 `PathUri`。

### 练习 4：Shell Portability

把以下意图分别写成 Bash、PowerShell、cmd：

```text
等待约 1 秒，将 UTF-8 文本写入 result.txt，成功后打印 done
```

然后再写一份不依赖 Shell、尽可能用 argv 和 filesystem API 的实现，比较风险。

### 练习 5：Skip Review

对一个新 PR 的：

```rust
skip_if_remote!(Ok(()), "fails remotely");
```

写一条 code review finding：

- 指出缺少哪种证据；
- 建议先检查什么；
- 什么情况下可以改成 Wine-only；
- 若必须保留，reason 应怎样具体化。

### 练习 6：取消进程树

设计一个跨平台 integration test：

1. shell 启动长期 child；
2. child 写 ready marker；
3. 发取消；
4. 等 terminal event；
5. 验证 child 不再写 heartbeat；
6. 在 Unix process group 与 Windows process tree 下都成立。

---

## 42. 理解检查

### 问题 1

Linux host 上运行 Wine Windows executor 时，为什么不能用 `cfg!(windows)` 决定 expected path？

<details>
<summary>参考答案</summary>

`cfg!(windows)` 描述编译并运行测试进程的 host，此时是 Linux，所以为 false；但路径由远程 Windows exec-server 解释。应使用 selected environment 的 target OS、PathConvention 或 PathUri 进行目标语义渲染。

</details>

### 问题 2

为什么 remote workspace fixture 不能直接写进 host `TempDir`？

<details>
<summary>参考答案</summary>

Host 与 remote executor 默认不是同一文件系统。Host `TempDir` 只对 app/test process 可见；目标命令需要通过 executor filesystem 在 selected environment cwd 中创建和读取文件。

</details>

### 问题 3

`AbsolutePathBuf` 是否适合保存任意 foreign Windows path？

<details>
<summary>参考答案</summary>

通常不适合。它仍使用当前 host-native PathBuf 语义，只保证当前平台视角下绝对/规范化。跨 exec-server wire 应使用 PathUri；app-server 兼容边界可暂用 LegacyAppPathString，再转换成 PathUri。

</details>

### 问题 4

什么时候该用 `skip_if_wine_exec!`，什么时候用 `skip_if_target_windows!`？

<details>
<summary>参考答案</summary>

若失败来自 Wine runner 模拟能力，而 native Windows 语义正常，用 Wine-only skip；若测试/功能本身不适用于任何 Windows target，包括 native Windows 和 Wine Windows，则用 target-Windows skip。

</details>

### 问题 5

为什么 Shell 应由 exec-server 报告，而不是 app-server 检测？

<details>
<summary>参考答案</summary>

真正解释命令的是 executor。App-server 可能运行在不同 OS，检测到的 host Shell 在 target 上不存在。Exec-server 的 EnvironmentInfo 才能报告目标可用 Shell 和路径。

</details>

### 问题 6

为什么 sandbox path conversion 失败应 fail closed，而 UI path display 可以 best effort？

<details>
<summary>参考答案</summary>

Sandbox conversion 参与权限判定，不确定时继续执行可能扩大读写范围；UI display 失败只影响可读性，可以保留原始 string/URI，不应因此让整个操作不可用。

</details>

### 问题 7

Wine remote suite 通过是否证明 native Windows ConPTY 正常？

<details>
<summary>参考答案</summary>

不能。当前 Wine remote test README 明确说明 ConPTY/TTY 尚未覆盖。Wine suite 能证明它实际执行的 Windows exec-server 路径，不应外推到未覆盖能力；仍需要 native Windows 相应测试。

</details>

---

## 43. 本章词汇表

| 英文/代码词 | 字面翻译 | 在本章中的具体意思 |
|---|---|---|
| Cross-platform | 跨平台 | 同一功能在 Linux、macOS、Windows 的等价语义 |
| Host OS | 主机操作系统 | 运行当前 app-server/test/control process 的 OS |
| Target OS | 目标操作系统 | 实际解释路径和执行命令的 executor OS |
| Executor | 执行器 | 真正访问目标文件系统并启动进程的组件/环境 |
| Placement | 放置位置 | 执行位于当前进程本地还是远程服务 |
| Local environment | 本地环境 | 与控制进程同机、直接使用本地执行和文件系统 |
| Remote environment | 远程环境 | 通过协议连接另一 exec-server 的执行环境 |
| App-server | 应用服务器 | 接收 client API、组织 thread/turn 并选择 environment |
| Exec-server | 执行服务器 | 在目标 OS 上处理进程、文件系统和 sandbox 的服务 |
| Control plane | 控制平面 | 负责选择、配置、编排和观察执行的层 |
| Data plane | 数据平面 | 真正执行命令、传输输出和操作文件的层 |
| Environment ID | 环境标识 | 在线程/Turn 选择中引用具体执行环境的稳定字符串 |
| EnvironmentInfo | 环境信息 | Exec-server 报告的 Shell、cwd 和 capability |
| Capability | 能力 | 远程 executor 明确声明支持的协议特性 |
| Handshake | 握手 | 连接初始化并交换 session/environment 信息的过程 |
| `PathBuf` | 路径缓冲对象 | Rust 当前 host-native 路径类型 |
| `AbsolutePathBuf` | 绝对路径对象 | 保证 host-native path 绝对/规范化的 Codex wrapper |
| `LegacyAppPathString` | 旧应用路径字符串 | App-server API 兼容 native/foreign path spelling 的边界类型 |
| `PathUri` | 路径 URI | 跨 host 保存和操作 `file:` path 的不可变类型 |
| `file:` URI | 文件 URI | 用统一 URI 语法标识 POSIX、drive 或 UNC file path |
| Path convention | 路径惯例 | POSIX 或 Windows 的 separator/root/case 语法 |
| POSIX path | POSIX 路径 | Linux/macOS 常见 `/repo/file` 语法 |
| Drive path | 驱动器路径 | Windows `C:\repo\file` 语法 |
| UNC path | 通用命名约定路径 | Windows `\\server\share\file` 网络路径 |
| Separator | 分隔符 | POSIX `/`；Windows path 通常支持 `\`，部分场景也接受 `/` |
| Case sensitivity | 大小写敏感 | POSIX 通常区分大小写；Windows path identity 通常不区分 ASCII case |
| Lexical operation | 词法路径操作 | 不访问磁盘，只按 segment 处理 join/parent/normalize |
| Canonicalization | 规范解析 | 访问文件系统解析 symlink、alias 和实际存在路径 |
| Symlink | 符号链接 | 指向另一个文件或目录的 filesystem entry |
| Foreign path | 外来平台路径 | 不属于当前 host，而属于另一 OS/executor 的路径 |
| Native spelling | 原生写法 | 按目标 OS 惯例展示的路径文本 |
| Projection | 投影 | 为兼容旧 host type 临时映射 foreign path 的中间表示 |
| Fail closed | 失败即关闭 | 不确定安全权限时拒绝继续执行 |
| Fail open | 失败仍尽量继续 | 诊断/UI 转换失败时保留原文等 best-effort 行为 |
| Shell | 命令解释器 | Bash、zsh、sh、PowerShell 或 cmd 等脚本语法环境 |
| Argv | 参数向量 | 直接传给程序的独立 argument 列表 |
| Script string | 脚本字符串 | 还要被 Shell 再解析一次的命令文本 |
| Quoting | 引号处理 | Shell 如何把空格和特殊字符组合为参数 |
| Escaping | 转义 | 让特殊字符失去语法意义或表达特殊字节 |
| PowerShell | PowerShell | Windows 常用对象化 Shell，语法不同于 Bash |
| `cmd.exe` | Windows 命令处理器 | Windows 传统命令 Shell |
| Encoding | 编码 | 字符与 byte 之间的转换规则，如 UTF-8 |
| CRLF | 回车换行 | Windows 常见文本行尾 `\r\n` |
| LF | 换行 | Unix 常见文本行尾 `\n` |
| Environment variable | 环境变量 | 传给进程的 key/value 配置，如 PATH |
| `PATH` | 可执行搜索路径 | Shell/OS 查找命令的目录列表 |
| `PATHEXT` | 路径扩展名列表 | Windows 用来决定可执行文件后缀的环境变量 |
| Executable bit | 可执行权限位 | Unix mode 中允许文件直接执行的标记 |
| PID | 进程号 | OS 分配给真实进程的标识 |
| Logical process ID | 逻辑进程标识 | Exec protocol 中由 client 选择、非 OS PID 的句柄 |
| Process group | 进程组 | Unix 中统一发送 signal 的相关进程集合 |
| Process tree | 进程树 | 父进程及其 descendants 的层级 |
| Signal | 信号 | Unix 常见的进程通知/终止机制，如 SIGTERM |
| `taskkill` | 任务终止工具 | Windows 按 PID/process tree 终止进程的命令 |
| TTY | 终端设备 | 提供交互式输入、echo 和 terminal behavior 的接口 |
| PTY | 伪终端 | Unix 为程序模拟 terminal 的机制 |
| ConPTY | Windows 伪控制台 | Windows 提供伪终端能力的 API |
| Wine | Windows 兼容层 | 在 Linux host 运行 Windows binary 的兼容环境 |
| `WINEPREFIX` | Wine 前缀 | 一套隔离的 Wine drive/registry/runtime 状态 |
| Docker | 容器运行时 | 本章用于启动 Linux remote exec-server fixture |
| Auto-env | 自动环境 | 根据测试进程配置选择 local/Docker/Wine 的 harness 模式 |
| Fixture | 测试夹具 | 测试预先创建的文件、server、config 或 process |
| Target-native fixture | 目标原生夹具 | 在 executor 文件系统/OS 内创建的测试资源 |
| Skip macro | 跳过宏 | 按 host/target/placement/Wine 条件跳过测试的工具 |
| Test matrix | 测试矩阵 | Host、target、placement、architecture 等组合 |
| Native Windows | 原生 Windows | 真正 Windows OS runner，而非 Wine 模拟 |
| Cross-compilation | 交叉编译 | 在一种 host/architecture 为另一目标生成 binary |
| Runfiles | 运行文件集合 | Bazel 为 test/binary 声明并定位的运行时资源 |
| Sandbox intent | 沙箱意图 | Core 传递、由 executor 按目标 OS 实现的权限要求 |
| Materialize | 具体化 | 把 portable policy 转成平台路径、wrapper 和参数 |
| Model-visible environment | 模型可见环境 | Prompt 中告诉模型的 target cwd、OS、Shell 等上下文 |
| Wine-specific debt | Wine 专属债务 | 只因 Wine harness 限制而存在的未覆盖行为 |

---

## 44. 本章小结

跨平台工程真正困难的地方，不是到处写 `#[cfg(windows)]`，而是让每项数据和行为由正确的环境解释。

请记住七句话：

1. Host OS、Target OS 和 Local/Remote 是三个独立维度；
2. `cfg!(windows)` 描述当前进程，不能替代远程 target metadata；
3. Host-local path 用 `PathBuf`/`AbsolutePathBuf`，跨 executor wire 用 `PathUri`；
4. Fixture 必须创建在真正读取它的文件系统；
5. Shell、Sandbox wrapper 和 process termination 应在 executor 侧具体化；
6. Auto-env 让同一测试重放到 local、Docker Linux 和 Wine Windows；
7. Skip 必须按最窄失败维度选择，并记录能够指导未来移除的原因。

当前 Codex 仓库已经提供了一条较完整的跨 OS 证据链：Environment 握手报告 target Shell/cwd/capability，exec protocol 使用 `PathUri` 和 portable process/sandbox intent，executor 负责平台具体化，Core 与 App-server test harness 通过 auto-env 复用同一测试，Docker 和 Wine runner 再把 target 从 local OS 扩展到 remote Linux 与 Windows。阅读或修改这条链时，最重要的问题始终是：**现在这段代码看到的是谁的文件系统、谁的 Shell、谁的进程语义？**
