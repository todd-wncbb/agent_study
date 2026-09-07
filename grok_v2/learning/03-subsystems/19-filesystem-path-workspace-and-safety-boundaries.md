# 文件系统抽象、工作区路径与安全边界：本地/ACP 后端、路径映射、Gitignore 与策略分层

> 本篇承接[内置工具实现与执行语义](18-builtin-tools-execution-semantics-and-output-contracts.md)，专门回答文件工具最终访问的是哪套文件系统、模型看到的路径为何可能不是真实路径，以及路径规范化、权限策略、gitignore 和 sandbox 各自负责什么。

## 1. 先给结论

Grok Build 的文件访问不是简单的 `tokio::fs::read(path)`：

1. 仓库中存在两套同名但不同用途的 `AsyncFileSystem` trait：工具执行层的一套，以及 workspace/session 状态层的一套。
2. 工具执行层只有 read/write/delete 三个核心操作，后端可以是本地磁盘，也可以通过 ACP 把请求交回编辑器客户端。
3. workspace 文件系统还持有 root、exists、`try_read_file`，并通过 `AsyncFsWrapper` 统一绝对/相对路径转换。
4. `Cwd` 是真实执行路径；`DisplayCwd` 是模型应继续使用的稳定路径。fork、worktree 或 AB overlay 下二者可能不同。
5. `resolve_model_path` 是路径映射器和模型输入清理器，不是授权器，也不是 sandbox。
6. canonicalize 用于解析 symlink 和规范化路径，但不同调用点对失败的处理不同：有的回退原路径，有的必须保留 `ErrorKind`。
7. `.gitignore` 是产品级可见性策略，不是保密边界；受管理的 `Read` deny 才是权限策略，OS sandbox 才是最后的强制边界。
8. grep 的递归搜索需要把 deny 规则转换成 ripgrep exclude，因为仅审批入口路径无法覆盖遍历过程中遇到的每个文件。
9. ACP 文件后端当前只传输文本；删除未受协议支持，二进制写入还会在 UTF-8 转换处失败。
10. 本地工具写入会自动创建父目录，并只对 Windows sharing/lock violation 做短暂重试；它不是原子写入。

最有用的心智模型是四层：

```text
模型路径空间
  → 路径清理与 DisplayCwd 映射
  → 权限/策略判定
  → FileSystem 后端（Local 或 ACP）
  → OS、远端编辑器或 overlay 中的真实文件
```

## 2. 为什么“路径”不是一个字符串

同一文件可能同时拥有三种身份：

| 身份 | 示例 | 使用者 |
| --- | --- | --- |
| 模型展示路径 | `/testbed/project/src/main.rs` | 对话历史、工具输出 |
| session 真实路径 | `~/.grok/worktrees/.../b-overlay/src/main.rs` | 工具执行、权限上下文 |
| canonical 路径 | 解析 symlink 后的 `/private/.../main.rs` | gitignore、保护目标、安全核对 |

如果系统只保存一个字符串，会出现两类问题：

- 把内部 worktree 路径泄漏给模型，随后模型不断引用短生命周期目录；
- 沿用对话里的原项目路径，把修改错误写回主工作区而非隔离 overlay。

`Cwd`/`DisplayCwd` 的分离就是为了解决这个矛盾。

## 3. 源码地图

```text
crates/codegen/xai-grok-tools/src/
├── computer/types.rs
│   └── 工具层 AsyncFileSystem、ComputerError
├── computer/local/
│   ├── file_system.rs       # 工具层 LocalFs
│   └── mock_fs.rs           # 工具单测内存后端
├── types/resources.rs
│   ├── Cwd / DisplayCwd
│   ├── FileSystem
│   ├── resolve_model_path
│   ├── GitignoreFilter / RespectGitignore
│   └── DenyReadGlobs / PathNotFoundHints
├── util/fs.rs               # canonicalize 与 Unicode 文件名补救
└── util/path_suggestions.rs # NotFound 提示

crates/codegen/xai-grok-workspace/src/
├── file_system/
│   ├── fs.rs                # workspace 层 AsyncFileSystem + AsyncFsWrapper
│   ├── local_fs.rs
│   ├── acp_fs.rs
│   ├── adapter.rs           # tools trait 的 ACP adapter
│   └── mock_fs.rs
└── permission/
    ├── manager.rs
    ├── policy.rs
    ├── resolution.rs
    └── shell_access.rs

crates/codegen/xai-grok-shell/src/session/
├── agent_rebuild.rs         # 更新 RespectGitignore、PathNotFoundHints
└── acp_session_impl/
    └── session_setup.rs     # 注入 ACP FS、DenyReadGlobs 等
```

## 4. 两套 `AsyncFileSystem` 不要混淆

### 4.1 工具执行层 trait

`xai-grok-tools/src/computer/types.rs`：

```rust
#[async_trait]
pub trait AsyncFileSystem: Send + Sync {
    async fn read_file(&self, path: &Path) -> Result<Vec<u8>, ComputerError>;
    async fn write_file(&self, path: &Path, data: &[u8]) -> Result<(), ComputerError>;
    async fn delete_file(&self, path: &Path) -> Result<(), ComputerError>;
}
```

它刻意很小，因为 `read_file`、`search_replace`、`apply_patch` 只需要这三个原语。实现对象被包进：

```rust
pub struct FileSystem(pub Arc<dyn AsyncFileSystem>);
```

再作为 session resource 注入工具。

### 4.2 Workspace 层 trait

`xai-grok-workspace/src/file_system/fs.rs` 的同名 trait 还包含：

- `root()`；
- `exists()`；
- `try_read_file()`；
- read/write/delete。

它服务于 session 状态、rewind 和 ACP session 文件视图，因此需要知道相对路径基准。

### 4.3 为什么不应强行合并理解

两套 trait 的错误类型、root 所有权和调用者不同：

| 维度 | tools trait | workspace trait |
| --- | --- | --- |
| 错误 | `ComputerError` | `FsError` |
| root | 调用方已解析路径 | 后端自己持有 root |
| exists | 无 | 有 |
| 可选读取 | 无 | 有 `try_read_file` |
| 主要用户 | 内置工具 | session 文件状态与适配层 |

同名说明它们表达相似端口，不代表 Rust 类型兼容。ACP 场景用 adapter 显式桥接两边。

## 5. `ComputerError` 为什么保存 `ErrorKind`

工具层错误主要是：

```text
IOError(message, Option<io::ErrorKind>)
CommandNotQuoted
```

保留 `ErrorKind` 让工具把底层 I/O 映射成领域结果：

- `NotFound` → FileNotFound；
- `IsADirectory` → IsADirectory；
- `PermissionDenied` → 权限错误或 sandbox 诊断；
- 其他错误 → ToolError 或通用错误输出。

若远端 adapter 只保留字符串，工具只能靠文本猜错误类型。`AcpFsAdapter` 因此把 ACP `ResourceNotFound` 映射成 `io::ErrorKind::NotFound`，也会识别包含 permission denied 的消息。

这仍不是完美的 typed error：远端权限识别部分依赖文案。但它至少保住了最重要的恢复分支。

## 6. 本地工具文件系统

`computer/local/file_system.rs` 中的 `LocalFs` 直接使用 Tokio 文件 API。

### 6.1 读取

```text
tokio::fs::read
  → bytes
  或 ComputerError::IOError(message, kind)
```

工具层决定 bytes 是文本、图片、PDF 还是二进制，不由 LocalFs 解码。

### 6.2 写入

写入前会递归创建父目录。随后使用 `tokio::fs::write`，它的语义通常是创建或截断目标文件。

这意味着：

- “写文件”隐含 mkdir 副作用；
- 已有文件可能被完整覆盖；
- 当前实现不是“写临时文件 → fsync → rename”的原子写；
- 崩溃或磁盘错误时，不承诺旧文件一定完整保留。

workspace trait 源码中甚至留有 `TODO: handle atomic write`，进一步说明不能推断原子保证。

### 6.3 Windows 短暂锁重试

编辑器、索引器或杀毒软件可能短暂锁住文件。LocalFs 只针对 Windows raw error 32/33 重试，延迟依次为：

```text
25ms → 50ms → 100ms → 200ms → 400ms
```

普通权限错误、ACL 拒绝、sandbox 拒绝不会重试。这个策略吸收短暂竞争，却不会用无限重试掩盖持续故障。

### 6.4 Sandbox 违规日志

遇到 `PermissionDenied` 时，本地实现调用 `xai_grok_sandbox::log_violation`，记录 read/write/delete/mkdir 及路径。

注意因果方向：

```text
sandbox/OS 拒绝
  → tokio::fs 返回 PermissionDenied
  → LocalFs 记录违规
```

LocalFs 本身不是执行 sandbox 的组件，它只观察并记录拒绝。

## 7. Workspace 的 `AsyncFsWrapper`

workspace 层的 `AsyncFsWrapper` 包住 `Arc<dyn AsyncFileSystem>`，接受实现 `ToAbsPath` 的多种路径类型：

- `AbsPathBuf`；
- `RelPathBuf`；
- `&Path`；
- `&PathBuf`。

每次操作都相对于后端 `root()` 转成绝对路径。这样 session 状态代码不必重复判断“这次传入的是绝对还是相对”。

### 7.1 `try_read_file` 避免 TOCTOU 与远端往返

trait 的默认实现是：

```text
exists(path)
  → true  → read_file(path)
  → false → None
```

这有两个缺点：

- 文件可能在 exists 与 read 之间消失；
- ACP 后端需要两次 RPC。

LocalFs、AcpSessionFs 和 MockFs 都覆盖 `try_read_file`，用一次读取直接区分 NotFound，减少竞态和往返。

它仍然不是事务快照，只是消除了最明显的“双调用窗口”。

## 8. ACP 文件后端

ACP 模式下，文件不一定存在于 agent 进程能直接访问的本地磁盘。客户端声明 `fs.readTextFile`/`writeTextFile` 能力后，工具请求可通过 gateway 交回编辑器执行。

### 8.1 `AcpFsAdapter`

它实现 tools 层 trait：

```text
read_file   → ACP ReadTextFileRequest  → String → bytes
write_file  → bytes → UTF-8 String → ACP WriteTextFileRequest
delete_file → 返回 unsupported error
```

由此得到三个明确限制：

1. 读取是文本协议，不是真正的任意二进制 byte channel；
2. 写入数据必须是合法 UTF-8；
3. `apply_patch` 的 Delete/Move 在这种后端上会失败，因为 ACP 尚无删除操作。

### 8.2 `AcpSessionFs`

它实现 workspace 层 trait，持有：

- `root`；
- ACP gateway；
- session ID；
- 可选 `display_cwd`。

除了读写 RPC，它还在 `resolve_path` 中执行第二次 display→overlay 映射。这是 defense in depth：即使上游工具遗漏 `resolve_model_path`，只要路径位于 display root 下，adapter 仍会把它改写到隔离 overlay。

但相对路径会原样透传；正常调用应先通过 wrapper 相对 root 转成绝对路径。

## 9. `Cwd` 与 `DisplayCwd`

### 9.1 `Cwd`

`Cwd(PathBuf)` 是 session 工具真正执行的当前目录。相对路径最终会落到它下面。

### 9.2 `DisplayCwd`

`DisplayCwd(PathBuf)` 是稳定的模型路径。在 fork session、worktree 或 AB overlay 中：

```text
DisplayCwd = 对话一直使用的项目路径
Cwd        = 当前隔离副本真实路径
```

工具输出 NotFound、绝对路径提示和读取结果时应优先使用 display 空间，防止把内部实现路径带入后续模型调用。

### 9.3 映射例子

```text
Cwd        = /internal/worktrees/run-42
DisplayCwd = /testbed/project
模型输入   = /testbed/project/src/lib.rs
真实访问   = /internal/worktrees/run-42/src/lib.rs
```

若模型输入 `/etc/hosts`，它不以 `DisplayCwd` 为前缀，因此保持绝对路径 `/etc/hosts`；是否允许访问由权限与 sandbox 决定。

## 10. `resolve_model_path` 的完整职责

函数大致依次做：

1. 清理模型参数外层空白；
2. 清理模型偶尔附加的成对或游离引号；
3. 对成对引号中的尾随字面 `\\n`、`\\r`、`\\t` 做补救；
4. 展开开头的 `~` 或 `~/`；
5. 如果绝对路径位于 `DisplayCwd` 下，映射到真实 `Cwd`；
6. 其他绝对路径保持绝对；
7. 相对路径拼到 `Cwd`；
8. 对遗漏开头 `/`、但文本其实包含完整 cwd 的常见模型错误做补救。

### 10.1 它刻意不做什么

- 不判断路径是否存在；
- 不 canonicalize；
- 不阻止 `..`；
- 不判断是否越出 workspace；
- 不匹配权限 policy；
- 不执行 sandbox。

因此下面是合法的“解析结果”，但不一定是合法的“访问决定”：

```text
resolve_model_path(cwd, _, "../secret")
  → cwd/../secret
```

授权层必须在同样的路径语义下重新判断，不能把路径 join 当安全检查。

### 10.2 为什么保留未配对引号细节

清理器只有在确认首尾都被引号包住时才删除尾部字面 escape。否则 Windows 路径中真实存在的反斜杠序列可能被误删。

这类测试看起来琐碎，却是在“容忍模型格式错误”和“不篡改真实文件名”之间划边界。

## 11. Canonicalize 的两种包装

### 11.1 `canonicalize_with_timeout`

它给 `tokio::fs::canonicalize` 30 秒上限，成功后用 `dunce::simplified` 清理 Windows `\\?\` 前缀；失败或超时返回原路径。

适合“规范化有帮助，但失败不能阻断主流程”的调用点。

### 11.2 `try_canonicalize`

它保留 `io::Error`，不设置超时。`read_file` 和 `search_replace` 需要知道失败是否是 `NotFound`，才能决定 Unicode 文件名补救、新文件创建或结构化错误。

这里有明确权衡：人为制造 `TimedOut` 会改变调用点基于 `ErrorKind` 的语义，所以错误保留版本没有使用统一 timeout。

### 11.3 Symlink 的安全含义

canonicalize 能把 symlink 目标暴露为真实路径，因此对以下检查很重要：

- gitignore 根匹配；
- protected edit target；
- workspace 内外判断；
- macOS `/var` 与 `/private/var` 等别名。

但新文件尚不存在，不能 canonicalize 目标本身。`GitignoreFilter::is_ignored` 会退而 canonicalize 父目录，再拼回文件名。

## 12. Unicode 文件名补救

macOS 截图名称可能在 AM/PM 前使用 U+202F narrow no-break space，模型通常生成普通空格 U+0020。

`try_resolve_unicode_filename` 在直接 NotFound 后：

1. 读取父目录；
2. 仅对特定 Unicode 空白做 ASCII 归一化；
3. 比较目标文件名；
4. 恰好一个匹配才接受；
5. 多个匹配视为歧义，返回 None；
6. 最多运行 30 秒。

它与 `search_replace` 的 Unicode confusable 内容匹配是两套策略：

- 文件名补救只处理 OS 常生成、模型很难精确生成的字符；
- 内容 fallback 处理引号、dash 等文本差异，并受独立配置控制。

缩小归一化集合能降低“错误打开另一个相似文件”的风险。

## 13. NotFound 提示系统

`PathNotFoundHints` 默认关闭，host 可通过配置或 remote setting 开启。开启后错误可以包含：

- 当前 display cwd；
- “漏掉 repo 文件夹”的修正路径；
- 父目录下最多三个相似名称。

### 13.1 为什么提示扫描只有 100ms

错误补救不是主操作，不能因网络盘或巨大目录阻塞工具。所有同步探测放进一个 `spawn_blocking`，再加 100ms 上限；失败就只返回基础 NotFound。

### 13.2 显示空间回映射

候选实际在内部 worktree 中找到后，会把 cwd 前缀改回 display cwd。否则一次错误提示就会破坏稳定路径抽象。

### 13.3 提示不是自动纠错

“Did you mean”只写进错误，不自动改目标。自动改写一个近似路径可能让写工具修改错误文件，因此相似名称提示保留给模型或用户确认。

Unicode filename fallback 与它不同：前者只在唯一且受限归一化匹配时自动选取真实路径。

## 14. `.gitignore`：可见性策略，不是秘密保险箱

session 启动时可以构建 `GitignoreFilter`，保存 gitignore matcher 与 git root。`RespectGitignore` 默认值为 true，并由 agent rebuild 明确注入。

启用后：

- `read_file` 拒绝被忽略文件；
- `search_replace` 拒绝被忽略文件；
- `list_dir`/`grep` 的遍历配置遵循忽略规则；
- legacy behavior version 可能保留历史差异。

但 `.gitignore` 的原始目的只是版本控制忽略。用户完全可能把构建产物、缓存或大型测试数据放进去，并不表示它们敏感；反过来，密钥也可能没有被 gitignore。

所以：

```text
gitignore = 产品工具是否默认看见
permission deny = 策略是否允许读取
sandbox = 进程最终能否触达
```

三者不能互相替代。

## 15. `DenyReadGlobs`：递归搜索的纵深防御

受管理权限可配置 `Read(**/.env)`、`Read(**/*.pem)` 等 deny。显式 `read_file(.env)` 可在 PermissionManager 入口直接拒绝，但 grep 的输入往往只是仓库根目录：

```text
grep(pattern="token", path=".")
```

若只审批 `.`，ripgrep 仍可能在递归时打开 `.env`。因此 session setup 把所有 Read deny glob 提取成 `DenyReadGlobs`，grep 转为：

```text
--glob '!**/.env'
--glob '!**/*.pem'
```

显式传入被拒路径仍在 permission manager 更早阻断，因为 ripgrep 对显式路径和 ignore/exclude 的行为存在差异。

这是一种双层防护：

```text
显式目标 → PermissionManager
递归发现 → ripgrep exclude
```

## 16. 保护性编辑目标

PermissionManager 在 Edit 访问上解析模型路径，然后调用 `edit_target_protection`。源码会识别一些不能静默修改的高风险目标，例如：

- Git hooks，包括 submodule gitdir 下的 hooks；
- 系统或宿主关键路径；
- 经 symlink 指向保护位置的路径。

保护目标通常强制进入提示/审批，而不是依赖普通 workspace allow。重要的是检查发生在路径映射之后，并会考虑 canonical/real target；否则可通过 display path、`..` 或 symlink 绕过字符串前缀判断。

具体审批状态机见[权限判定与审批状态机](05-permission-approval-state-machine.md)。

## 17. 路径策略为何必须 fail closed

路径判定最危险的做法是：解析失败时默认允许。典型不确定性包括：

- shell 命令中动态变量生成路径；
- `..` 与 symlink 组合；
- 远端文件系统无法 canonicalize；
- glob 递归访问未知后代；
- 嵌套 shell、重定向或脚本参数；
- DisplayCwd 与真实 cwd 不一致。

权限模块对不能可靠分类的 shell 文件访问会提高风险、要求询问或拒绝。工具后端返回 PermissionDenied 时也由 sandbox 日志补充证据。

“路径助手尽量容错”和“安全判定保守失败”并不矛盾：前者改善明确意图的格式问题，后者处理无法证明安全的访问范围。

## 18. 状态与所有权

| 状态 | 拥有者 | 生命周期 |
| --- | --- | --- |
| tools `FileSystem` | Tool registry Resources | session |
| workspace filesystem | Workspace/session actor | session |
| `Cwd` | 调用上下文或 Resources | session/单次调用覆盖 |
| `DisplayCwd` | Tool registry Resources + ACP FS | fork/overlay session |
| `GitignoreFilter` | Tool registry Resources | repo/session，可 rebuild |
| `RespectGitignore` | session rebuild 注入 | 配置变化后更新 |
| `DenyReadGlobs` | session setup 从 managed policy 提取 | session/policy |
| `PathNotFoundHints` | Resources | session 配置 |
| MockFs 内容 | MockFs 内部 `RwLock<HashMap>` | 单测实例 |

工具不应把全局进程 cwd 当权威状态；同一进程可同时承载多个 session，它们可能使用不同 worktree。

## 19. 并发与竞态

### 19.1 本地读写

Tokio 让 I/O 不阻塞 async executor，但不会自动串行同一文件的多个工具调用。两个并发 writer 的最终内容由完成顺序决定。

### 19.2 `exists` + `read`

默认 workspace trait 的两步实现有 TOCTOU；具体后端覆盖 `try_read_file` 后缩为单操作。

### 19.3 canonicalize + 使用

即使 canonicalize 成功，路径也可能在真正 open 前被替换。真正强安全保证要依赖 openat/句柄式 API、sandbox 或受控远端后端；当前字符串 PathBuf 流程不提供不可竞态证明。

### 19.4 Gitignore filter 更新

filter 作为 resource 在 session rebuild/seed 时更新。一次工具调用通常先 clone 或借出所需依赖，再执行 I/O；配置更新不会神奇地撤销已经开始的系统调用。

## 20. Local、ACP 与 Mock 对比

| 能力 | Tools LocalFs | AcpFsAdapter | Workspace LocalFs | AcpSessionFs | MockFs |
| --- | --- | --- | --- | --- | --- |
| bytes read | 是 | 文本转 bytes | 是 | 文本转 bytes | 是 |
| arbitrary bytes write | 是 | 必须 UTF-8 | 是 | 必须 UTF-8 | 是 |
| delete | 是 | 不支持 | 是 | 不支持 | 是 |
| root | 调用方管理 | 调用方管理 | 后端持有 | 后端持有 | 后端持有 |
| display→overlay guard | 上游 resolver | 上游 resolver | 无 | 有第二层 | 无 |
| transient lock retry | Windows 有 | 客户端决定 | 无专门重试 | 客户端决定 | 不需要 |
| sandbox violation log | PermissionDenied 时有 | 无本地 OS 观察 | 无 | 无 | 无 |

“trait 相同”只保证方法形状相同，不保证能力完全等价。调用方需要认识后端能力缺口，尤其是删除与二进制。

## 21. 一次真实链路：fork 中读取旧对话路径

假设主对话曾读取：

```text
/repo/project/src/lib.rs
```

随后 fork session 的真实目录是：

```text
/internal/worktrees/fork-7/src/lib.rs
```

执行过程：

1. fork session 把 `/internal/worktrees/fork-7` 注入 `Cwd`；
2. 把 `/repo/project` 注入 `DisplayCwd`；
3. 模型沿用历史绝对路径 `/repo/project/src/lib.rs`；
4. `resolve_model_path` 识别 display 前缀；
5. 去掉 `/repo/project`，把 `src/lib.rs` 拼到真实 Cwd；
6. 权限 manager 使用对应 path context 评估访问；
7. FileSystem 后端读取 fork 文件；
8. `FileContent.absolute_path` 可保存真实定位信息；
9. 模型可见错误和建议尽量回映射为 `/repo/project/...`。

如果是 ACP AB overlay，`AcpSessionFs.resolve_path` 还会做一次相同方向的保护性重写。

## 22. 一次失败链路：读取受管 `.env`

假设 managed policy 包含：

```text
Read(**/.env)
```

### 显式读取

```text
read_file("config/.env")
  → PermissionManager 规范化访问路径
  → deny rule 命中
  → 工具后端不应收到读取请求
```

### 递归 grep

```text
grep(pattern="SECRET", path=".")
  → 入口 path 本身未必被 deny
  → DenyReadGlobs 注入 '!**/.env'
  → ripgrep 遍历时排除该文件
```

### Bash 间接读取

```text
bash("cat config/.env")
  → shell_access 分解命令与文件参数
  → Read deny 命中，或不确定时 fail closed/询问
  → 即使静态分析遗漏，sandbox 仍是最后边界
```

这说明同一策略需要针对直接工具、递归工具和通用 shell 分别落地。

## 23. 常见误解

### 误解一：`resolve_model_path` 会把路径限制在 workspace

不会。非 display 前缀的绝对路径会原样保留，相对 `..` 也不会被它拒绝。限制由权限和 sandbox 负责。

### 误解二：canonicalize 后就不存在竞态

不会。canonicalize 和真正 open 是两个操作，路径可在中间变化。

### 误解三：gitignored 文件一定是秘密

不是。gitignore 是版本控制/产品可见性策略；秘密规则来自 managed deny 与 sandbox。

### 误解四：ACP 文件系统等价于远端 POSIX 文件系统

不是。它目前基于 read/write text RPC，不支持 delete，也不支持任意二进制写。

### 误解五：LocalFs 的 write 是原子替换

不是。它创建父目录后直接 `fs::write`；没有临时文件提交协议。

### 误解六：有 MockFs 就证明本地行为

MockFs 适合验证业务控制流，但不会复现 symlink、ACL、Windows sharing violation、磁盘满、网络盘延迟或原子性。

## 24. 调试路线

### 24.1 修改写到了错误 worktree

检查：

1. Tool Resources 中的 `Cwd`；
2. 是否注入 `DisplayCwd`；
3. 调用路径是否经过 `resolve_model_path`；
4. ACP `AcpSessionFs` 是否配置 `with_display_cwd`；
5. 权限请求的 `PathContext` 是否包含 real/display cwd；
6. 工具输出是否把真实路径错误复制回模型。

### 24.2 明明存在却 NotFound

检查：

- 参数是否带尾随换行或引号；
- 是否遗漏 repo 文件夹或开头 `/`；
- Unicode 空格是否不同；
- symlink/overlay 是否映射到另一 root；
- ACP 客户端返回码是否正确映射为 NotFound；
- `PathNotFoundHints` 是否启用。

### 24.3 grep 读到了不该读的文件

检查：

- managed policy 是否正确解析出 Read deny；
- `DenyReadGlobs` 是否注入；
- `build_rg_args` 是否生成排除 glob；
- 显式路径是否在 PermissionManager 提前拒绝；
- broad user glob 是否改变 gitignore，但不能覆盖 managed deny；
- shell 方式是否绕开专用 grep 并进入另一条权限分析路径。

### 24.4 ACP patch 删除失败

不要先查 patch parser。确认当前 FileSystem 是否是 `AcpFsAdapter`，因为 delete 明确返回 unsupported。Add/Update 成功不表示 Delete/Move 可用。

## 25. 修改影响清单

### 修改路径清理器

- 覆盖 Unix、Windows 分隔符与 drive prefix；
- 覆盖成对/非成对引号；
- 覆盖真实换行与字面 `\\n`；
- 覆盖 `~`、`~user`；
- 覆盖遗漏开头 `/`；
- 复核 PermissionManager 使用同一 resolver；
- 防止 display prefix 的部分字符串误匹配。

### 修改 DisplayCwd

- fork/overlay 写入目标；
- NotFound 建议回映射；
- ACP adapter defense in depth；
- 工具通知中的绝对路径；
- 对话压缩和重放后的历史路径；
- 子 session 的继承规则。

### 修改文件系统 trait

- LocalFs；
- ACP adapters；
- MockFs；
- registry 注入；
- 工具单测的临时实现；
- 错误类型和 `ErrorKind` 映射；
- 远端能力协商。

### 修改 gitignore 或 deny

- read_file/search_replace；
- list_dir/grep walk flags；
- grep recursive excludes；
- legacy behavior version；
- session rebuild 动态更新；
- shell 文件访问分析；
- 配置文档和模型工具描述。

## 26. 推荐实验

### 实验一：DisplayCwd 映射

直接运行 `resolve_model_path` 相关测试，画出 display、real 和结果三列。特别观察“不匹配的绝对路径”不会被改写。

### 实验二：Unicode 文件名

在临时目录创建含 U+202F 的截图名，用普通空格请求，随后再创建第二个归一化后同名文件，观察唯一匹配成功、歧义匹配失败。

### 实验三：Windows 锁重试逻辑

阅读 `write_file_with_retry_hooks` 测试，记录每次 delay；验证非 retryable 错误不会 sleep。

### 实验四：ACP 能力差异

用 fake gateway 验证 text read/write，再调用 delete。说明为何相同 trait 不代表完全相同语义。

### 实验五：DenyReadGlobs

创建 `.env`、`.pem` 和普通源文件，对比专用 grep 在有/无 DenyReadGlobs 时构造的 rg 参数与结果。

## 27. 自测题

1. 为什么仓库需要两套 `AsyncFileSystem`？
2. tools trait 为何不持有 root？
3. `try_read_file` 相比 exists+read 改善了什么？仍没保证什么？
4. `Cwd` 与 `DisplayCwd` 分别代表什么？
5. 为什么 fork session 不能把内部 worktree 路径持续暴露给模型？
6. `resolve_model_path` 为什么不是安全边界？
7. 对不匹配 DisplayCwd 的绝对路径会怎样处理？
8. 为什么有两个 canonicalize helper？
9. canonicalize 新文件路径时为什么需要父目录 fallback？
10. Unicode 文件名补救为什么要求唯一匹配？
11. NotFound 相似建议为什么不自动执行？
12. `.gitignore`、Read deny 和 sandbox 有什么区别？
13. grep 为什么需要 `DenyReadGlobs`？
14. ACP 后端为何不能承诺删除和二进制写？
15. LocalFs 的 Windows 重试会重试 PermissionDenied 吗？
16. `fs::write` 为什么不等于原子写？
17. symlink 为什么既影响正确性也影响安全？
18. MockFs 无法覆盖哪些真实故障？
19. DisplayCwd 映射为什么在工具层和 ACP session 层各做一次？
20. 从读取 `.env` 的三条路径看，策略为何需要多点落实？

## 28. 本篇术语表

| 名词 | 白话解释 | 在本篇中的具体含义 |
| --- | --- | --- |
| filesystem abstraction / 文件系统抽象 | 用接口隐藏文件到底存在哪里 | 两套 `AsyncFileSystem` trait |
| backend / 后端 | 真正完成接口操作的实现 | LocalFs、ACP adapter、MockFs |
| port / 端口 | 业务层依赖的抽象接口 | 工具通过 trait 依赖文件能力 |
| adapter / 适配器 | 把一种接口翻译成另一种接口 | 把 tools 文件调用变成 ACP 请求 |
| root | 相对路径的基准目录 | workspace filesystem 自己持有的根 |
| Cwd | current working directory | session 真正执行 I/O 的目录 |
| DisplayCwd | 稳定展示用工作目录 | 模型可见路径，可能映射到内部 worktree |
| worktree | Git 的额外工作目录 | fork/隔离 session 可使用独立副本 |
| overlay | 覆盖在原目录语义上的隔离文件视图 | AB 测试或隔离修改使用的真实 root |
| path space / 路径空间 | 某一参与者看到的一套路名字 | 模型空间与真实文件空间可能不同 |
| path mapping / 路径映射 | 把一个路径空间转换到另一个 | DisplayCwd 前缀改写为 Cwd |
| sanitize | 清理可明确识别的格式噪声 | 去空白、引号和特定尾随 escape |
| tilde expansion | 把 `~` 展开为用户主目录 | `shellexpand::tilde` 完成 |
| canonicalize | 解析 `.`、`..`、symlink 并得到规范路径 | Tokio/dunce 包装 |
| symlink | 指向另一路径的文件系统入口 | 可能让表面 workspace 路径落到外部目标 |
| `dunce::simplified` | 清理 Windows verbatim 路径外观 | 避免传播 `\\?\` 前缀 |
| ErrorKind | 标准 I/O 错误类别 | NotFound、PermissionDenied 等恢复依据 |
| TOCTOU | 检查与使用之间状态变化 | exists+read、canonicalize+open 的竞态 |
| RPC | 跨进程/机器调用远端方法 | ACP gateway 文件请求 |
| round trip / 往返 | 一次请求出去再收到响应 | try_read 可减少 ACP 两次调用 |
| ACP | Agent Client Protocol | agent 与编辑器宿主之间的协议 |
| capability negotiation | 双方声明支持哪些能力 | 客户端是否支持 read/write text file |
| defense in depth / 纵深防御 | 同一风险放置多层保护 | resolver 与 ACP adapter 双重路径改写 |
| binary-safe | 能原样传输任意字节 | ACP text RPC 当前不是 binary-safe write |
| atomic write | 外部只能看到旧文件或完整新文件 | 当前 LocalFs 没有这项保证 |
| truncate | 打开写入时把旧文件长度清零 | `fs::write` 对已有文件的典型行为 |
| sharing violation | Windows 文件被其他程序占用 | raw error 32，可短暂重试 |
| lock violation | Windows 锁冲突 | raw error 33，可短暂重试 |
| ACL | 操作系统访问控制列表 | 持续拒绝不属于临时锁重试 |
| sandbox | OS/运行环境强制的访问限制 | LocalFs 观察 PermissionDenied 并记录 |
| gitignore | Git 忽略规则 | 工具默认可见性策略，不是秘密分类 |
| managed policy | 组织或宿主下发的权限规则 | 可包含 Read/Edit deny glob |
| deny glob | 匹配一类禁止路径的模式 | 如 `**/.env`、`**/*.pem` |
| recursive walk | 从目录向下遍历全部后代 | grep/list_dir 可能间接触达敏感文件 |
| fail closed | 无法证明安全时拒绝或询问 | 路径/命令分析的不确定分支 |
| protected edit target | 即使普通写可用也要额外保护的目标 | Git hooks、系统关键文件等 |
| Unicode normalization | 把特定不同码点映射成可比形式 | 文件名只处理受限特殊空格 |
| narrow no-break space | U+202F 窄不换行空格 | macOS 截图名称常见字符 |
| ambiguity / 歧义 | 多个目标同样符合补救规则 | 文件名 fallback 此时拒绝自动选择 |
| MockFs | 内存中的测试文件系统 | 验证业务分支，不模拟真实 OS |
| `RwLock` | 允许多读或单写的异步锁 | MockFs 保护内存文件 map |
| rewind | 把 session 文件恢复到过去状态 | workspace filesystem 需要 delete 支持 |

更多通用名词见[全局术语表](../appendices/glossary.md)。

## 29. 源码证据索引

| 结论 | 源码入口 |
| --- | --- |
| tools 文件系统接口与错误类型 | `crates/codegen/xai-grok-tools/src/computer/types.rs` 中 `AsyncFileSystem`、`ComputerError` |
| 本地 I/O、父目录创建、Windows 重试和 sandbox 日志 | `crates/codegen/xai-grok-tools/src/computer/local/file_system.rs` |
| tools MockFs | `crates/codegen/xai-grok-tools/src/computer/local/mock_fs.rs` |
| Cwd、DisplayCwd 与路径映射 | `crates/codegen/xai-grok-tools/src/types/resources.rs` 中 `resolve_model_path` |
| Gitignore 与 deny resources | 同文件 `GitignoreFilter`、`RespectGitignore`、`DenyReadGlobs` |
| canonicalize 与 Unicode 补救 | `crates/codegen/xai-grok-tools/src/util/fs.rs` |
| NotFound 建议 | `crates/codegen/xai-grok-tools/src/util/path_suggestions.rs` |
| workspace 文件系统接口与 wrapper | `crates/codegen/xai-grok-workspace/src/file_system/fs.rs` |
| workspace Local/Mock | `file_system/local_fs.rs`、`file_system/mock_fs.rs` |
| ACP workspace 文件视图 | `file_system/acp_fs.rs` 中 `AcpSessionFs` |
| tools→ACP adapter | `file_system/adapter.rs` 中 `AcpFsAdapter` |
| protected edit 目标 | `crates/codegen/xai-grok-workspace/src/permission/shell_access.rs` 中 `edit_target_protection` |
| Edit path 在权限入口映射 | `permission/manager.rs` 中 `resolve_model_path` 调用 |
| grep deny 排除 | `xai-grok-tools/.../grok_build/grep/mod.rs` 中 `DenyReadGlobs` |
| session 注入 deny | `xai-grok-shell/src/session/acp_session_impl/session_setup.rs` |

## 30. 最小验证命令

```sh
# 比较两套同名 trait
rg "trait AsyncFileSystem" \
  crates/codegen/xai-grok-tools/src/computer/types.rs \
  crates/codegen/xai-grok-workspace/src/file_system/fs.rs

# 路径映射与边界测试
cargo test -p xai-grok-tools --lib resolve_model_path

# canonicalize、Unicode 名称和路径提示
cargo test -p xai-grok-tools --lib unicode_fallback
cargo test -p xai-grok-tools --lib path_not_found

# LocalFs 的锁重试
cargo test -p xai-grok-tools --lib transient_lock

# ACP display→overlay 映射
cargo test -p xai-grok-workspace --lib resolve_path

# grep managed deny
cargo test -p xai-grok-tools --lib deny_read_globs
```

测试包和过滤名以后若变化，以各 crate 的 `Cargo.toml` 与 `cargo test -- --list` 为准。
