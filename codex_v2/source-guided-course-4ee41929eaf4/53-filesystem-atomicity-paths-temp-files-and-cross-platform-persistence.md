# 53：文件系统原子性、路径、临时文件与跨平台持久化——“写成功了”到底意味着什么

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方资料说明，Codex 的 sandbox 会限制命令可以修改哪些文件，并且 macOS、Linux、WSL2 和 Windows 使用不同的平台原生实现。本章进一步研究 sandbox 边界之内，Codex 自己怎样处理路径、临时文件、原子替换、文件锁和崩溃恢复。实现事实以当前源码与测试为准。
>
> 官方对照资料：[Codex Sandbox](https://learn.chatgpt.com/docs/sandboxing)。

## 1. 本章解决什么问题

假设 Codex 要把新配置写进 `config.toml`：

```rust
std::fs::write("config.toml", new_contents)?;
```

这段代码在正常情况下完全能用，但它没有回答：

- 写到一半断电，原配置还在吗？
- 另一个进程恰好同时读取，会看到完整内容还是半份内容？
- `config.toml` 是符号链接时，应该替换链接还是修改链接指向的文件？
- 临时文件为什么必须建在目标文件的同一目录？
- `flush()`、`sync_all()` 和 `rename()` 分别保证什么？
- 两个 Codex 进程同时追加历史记录时，怎样避免两行搅在一起？
- `../outside/secret`、软链接和大小写差异会不会绕过目录检查？
- 为什么路径应使用 `Path`/`PathBuf`，而不是一律转成 `String`？
- Windows 的盘符、UNC 路径和符号链接与 Unix 有什么不同？

本章的核心认识是：

> 文件系统不是一个简单的 `HashMap<Path, Bytes>`。路径只是名字，文件是另一个对象；写入、发布、落盘、并发互斥和崩溃恢复也是不同问题。

## 2. 先用“打印新合同”理解安全写入

不要直接在唯一的纸质合同上边擦边改。更安全的流程是：

1. 在旁边完整打印一份新合同；
2. 检查新合同内容；
3. 一次性把桌上的旧合同换成新合同；
4. 如果停电后也必须找得到，再确认合同和目录记录都已进入保险柜。

对应文件系统操作：

```text
打印新合同       → 在目标目录创建临时文件
写完并检查       → write_all / serialize / validate
一次性换掉旧合同 → rename / NamedTempFile::persist
确保断电后存在   → file.sync_all + parent directory sync
```

“一次性换名字”和“物理介质已经保存”不是同一种保证。

## 3. 先分清五个问题

| 问题 | 常见机制 |
|---|---|
| 内容是否完整生成？ | 临时文件、`write_all`、校验 |
| 读者是否只看到旧版或新版？ | 同文件系统内的原子 `rename`/replace |
| 多个 writer 是否互相覆盖？ | file lock、唯一创建、进程内 mutex、版本检查 |
| 断电后是否仍能看到新版？ | `sync_all`、父目录同步、数据库/WAL |
| 崩溃后怎样知道做到哪一步？ | journal、marker、幂等恢复流程 |

一种机制通常只解决其中一部分。例如原子替换不能自动防止两个 writer 的 lost update。

## 4. `Path` 与 `PathBuf` 是什么

Rust 中常见：

```rust
fn load(path: &Path) { /* 借用路径 */ }

struct Config {
    codex_home: PathBuf, // 自己拥有路径
}
```

可以先类比：

- `&str`：借用文字；
- `String`：拥有文字；
- `&Path`：借用操作系统路径；
- `PathBuf`：拥有、可拼接的操作系统路径。

`Path` 不是“文件”。路径可以不存在，也可能指向目录、普通文件或符号链接。

## 5. 为什么路径不能总是 `String`

Unix 文件名本质上可以包含非 UTF-8 字节。Windows 路径又有盘符、UNC 和设备路径语义。

所以：

```rust
let path: PathBuf = ...;
let display = path.display();          // 给人看
let lossy = path.to_string_lossy();    // 明确允许有损转换
```

如果业务身份依赖路径，过早 `to_string_lossy()` 可能让两个不同的非 UTF-8 路径折叠成相同字符串。Connector cache 的实现因此特意不把 `codex_home` 混入 JSON 字符串 hash。

## 6. 相对路径必须有基准目录

`notes/a.md` 单独看没有唯一位置。它必须相对于某个 base：

```rust
let absolute = AbsolutePathBuf::resolve_path_against_base(
    "notes/a.md",
    "/workspace",
);
```

结果的解释是 `/workspace/notes/a.md`。

不要让深层函数偷偷依赖进程当前目录，因为别的代码可能改变 current working directory，remote executor 的 cwd 也可能属于另一台机器。

## 7. `AbsolutePathBuf` 提供的是哪种保证

源码注释非常关键：`AbsolutePathBuf` 保证路径是绝对且经过词法规范化，但不保证：

- 路径存在；
- 已解析所有符号链接；
- 指向普通文件；
- 调用者有权限访问；
- 仍位于某个安全目录内。

所以类型名中的 `Absolute` 不能被理解成“安全且真实”。

## 8. 词法规范化是什么

`absolutize.rs` 逐个处理 `Component`：

```text
CurDir (`.`)       → 忽略
ParentDir (`..`)   → 弹出上一段
Normal (`src`)     → 加入结果
RootDir (`/`)      → 保留根
Prefix (`C:`)      → 保留 Windows 前缀
```

例如：

```text
/workspace/src/./ui/../core
                 ↓
/workspace/src/core
```

这个过程不访问磁盘，因此不存在的目标也能规范化。

## 9. `canonicalize` 又是什么

`canonicalize` 会访问真实文件系统，通常会：

- 要求目标存在；
- 消除 `.` 和 `..`；
- 跟随符号链接；
- 返回绝对路径；
- 在 Windows 上可能产生不便展示的 verbatim/device 前缀，因此 Codex 常用 `dunce` 简化。

对比：

| 操作 | 访问磁盘 | 缺失路径 | 跟随 symlink |
|---|---:|---:|---:|
| 词法 absolutize | 否 | 可以 | 否 |
| canonicalize | 是 | 通常报错 | 是 |

## 10. 逻辑路径和物理路径可能都重要

用户从 `/workspace-link/project` 打开项目，而该路径指向 `/real/project`。

- 安全边界有时关心物理目标；
- UI、配置和开发工具有时希望保留用户选择的逻辑路径；
- Git/worktree 或远程路径映射也可能依赖逻辑名字。

`canonicalize_preserving_symlinks` 因此不是简单地“永远 canonicalize”。它会保留嵌套 symlink 下的逻辑路径，同时仍允许顶层系统别名按既有预期规范化。

## 11. `starts_with` 前必须先明确路径语义

直接判断：

```rust
candidate.starts_with(root)
```

只比较路径 component，并不会自动解决符号链接。

Thread Store 的 `scoped_rollout_path` 先分别 canonicalize 根目录和 rollout 文件，再检查：

```rust
canonical_rollout_path.starts_with(&canonical_root)
```

这用于阻止数据库或调用参数把归档、删除操作指向 sessions 根目录之外。

## 12. 为什么字符串前缀判断是错误的

下面不能证明目录包含关系：

```text
root      = /work/app
candidate = /work/application/secrets
```

字符串上 candidate 以 `/work/app` 开头，但它不是 root 的后代。

应使用路径 component 语义；存在 symlink 风险时还要明确是否 canonicalize。

## 13. 文件名也可以承载不变量

Thread Store 不只验证父目录，还调用 `matching_rollout_file_name`，要求文件名以对应的：

```text
<thread_id>.jsonl
<thread_id>.jsonl.zst
```

结尾。

这是第二层约束：即便路径在 sessions 目录内，也不能拿 thread A 的请求去删除 thread B 的文件。

## 14. 符号链接不是普通快捷方式

符号链接保存的是另一个路径，而不是目标文件内容：

```text
config.toml → configs/work.toml
```

常用 API：

- `metadata(path)`：通常跟随最终链接，查看目标；
- `symlink_metadata(path)`：查看链接本身；
- `read_link(path)`：读取链接保存的 target；
- `canonicalize(path)`：解析整条链并得到物理路径。

检查链接自身时不能误用 `metadata`。

## 15. 直接替换 symlink 会发生什么

如果 `config.toml` 是 symlink，而程序在 `config.toml` 旁创建临时文件后执行 replace，常见结果是：

- symlink 目录项被新普通文件替换；
- 原来指向的 `configs/work.toml` 没被修改；
- 用户不知不觉失去了链接结构。

因此 Codex 写配置前调用 `resolve_symlink_write_paths`。

## 16. `resolve_symlink_write_paths` 怎样工作

它返回两个概念：

```rust
pub struct SymlinkWritePaths {
    pub read_path: Option<PathBuf>,
    pub write_path: PathBuf,
}
```

- `read_path`：读取已有配置的位置；
- `write_path`：原子替换应发布到的位置。

它会逐段跟随最终文件的 symlink chain，并正确处理相对链接 target。

## 17. 为什么要检测 symlink cycle

可能存在：

```text
a → b
b → a
```

若只是不停 `read_link`，程序会无限循环。源码用 `HashSet<PathBuf>` 保存 visited path；再次遇到已访问路径即判定 cycle。

这里没有随意设置“最多跟 8 层”，而是依据重复节点识别真正的环。

## 18. 解析失败时为何要保守

`resolve_symlink_write_paths` 遇到 metadata/read-link/路径解析失败时，会让 `read_path` 为 `None`，并使用原始 root 作为 write path。

这意味着它不会把一个不可靠的中间解析结果当成可信目标。调用方也不会从可疑位置读取旧配置后再覆盖另一个位置。

## 19. 最简单的原子替换模式

Codex 公共 path utility 的核心流程是：

```rust
let parent = write_path.parent().ok_or(...)?;
std::fs::create_dir_all(parent)?;
let mut tmp = NamedTempFile::new_in(parent)?;
tmp.write_all(contents.as_bytes())?;
tmp.persist(write_path)?;
```

成功发布前，旧文件不动；任何序列化或临时写入错误都只影响临时文件。

## 20. 为什么临时文件必须在目标目录

`rename` 的原子性通常要求源和目标位于同一个文件系统/volume。

错误思路：

```text
/tmp/new-config → /mounted-home/.codex/config.toml
```

如果 `/tmp` 和 home 属于不同文件系统，rename 可能返回 cross-device error，无法作为单步原子发布。

`NamedTempFile::new_in(parent)` 让临时文件与目标具有相同父目录，显著降低这个问题。

## 21. `persist` 解决的是发布，不是所有持久性

`NamedTempFile::persist(target)` 可以把临时文件发布为目标文件。读者在支持原子替换的平台/文件系统上应看到旧版或新版，而不是逐步增长的新版。

但它不自动意味着：

- 字节已经进入物理介质；
- 父目录更新已经耐久；
- 两个 writer 不会互相覆盖；
- 远程/特殊文件系统与本地文件系统保证完全相同。

“atomic publish”和“durable after power loss”必须分开说。

## 22. `write`、`write_all` 与短写

底层 `write` 允许只写入 buffer 的一部分，并返回实际字节数。`write_all` 会循环到所有字节写完或出现错误：

```rust
tmp.write_all(contents.as_bytes())?;
```

因此“调用一次 write”不等于“整个 buffer 已写完”。

消息历史注释所说的“单次系统调用”属于更具体的并发追加策略，不能机械替换成任意场景下的普通 `write`。

## 23. `flush()` 保证什么

`flush()` 主要要求语言库/用户态 buffer 把数据交给下一层。例如 `BufWriter` 不再把字节留在自己的内存中。

它通常不等于：

```text
数据已经安全写入磁盘，突然断电也不会丢失
```

这个更强目标通常需要 `sync_data` 或 `sync_all`，具体保证仍取决于操作系统和文件系统。

## 24. `sync_all()` 为什么更强

`File::sync_all()` 请求操作系统同步文件内容及相关 metadata。

Rollout migration 在发布前会：

1. 写 staged rollout；
2. `flush`；
3. 对文件调用 `sync_all`；
4. 建立完整 SQLite projection；
5. rename staged file 覆盖旧 rollout；
6. 同步父目录。

这是因为 rollout 是恢复 thread 的权威历史，容错要求高于可丢弃 cache。

## 25. 为什么还要同步父目录

文件内容是一件事，目录里“这个名字指向哪个文件”是另一件事。

rename、create、remove 修改的是目录项。Unix 上为了让目录项变更在崩溃后也尽量耐久，源码会打开父目录并调用 `sync_all()`：

```rust
std::fs::File::open(parent)?.sync_all()
```

`sync_parent_directory` 在非 Unix 分支当前是 no-op，体现了平台 API 和保证并不完全相同。

## 26. 原子性、持久性和一致性不是同义词

| 词 | 关注点 |
|---|---|
| Atomicity | 不暴露半完成的发布状态 |
| Durability | 成功返回后，崩溃/断电恢复仍保留状态 |
| Consistency | 状态满足业务不变量，多个文件/数据库之间可解释 |
| Isolation | 并发操作不会产生不可接受的交错 |

原子 rename 只直接覆盖其中一部分。

## 27. 两个 writer 仍可能发生 lost update

假设 A、B 同时：

```text
A 读取 version 1
B 读取 version 1
A 修改 model 并原子写入 version 2
B 修改 theme 并原子写入 version 2'
```

每次文件都完整，但 B 可能覆盖 A 的 model 修改。

解决方式可能是：

- 进程内 mutex；
- 跨进程 file lock；
- version/CAS；
- 单一 writer；
- 把多个 edit 聚合成一次事务式更新。

## 28. Codex 配置编辑怎样减少破坏

`ConfigEditsBuilder`/`ConfigDocument` 的思路不是重建一份最小 TOML，而是：

- 读取已有文档；
- 使用 `toml_edit` 修改指定路径；
- 尽量保留 decor、注释和格式；
- 多个 edit 在内存中一起应用；
- 最后只执行一次原子替换。

这里的“批量原子”表示不会把半批 edit 写到磁盘；它本身不等于跨进程并发事务。

## 29. 阻塞文件 I/O 为什么放进 `spawn_blocking`

标准文件锁、同步读写和 `sync_all` 可能阻塞线程。如果直接在 Tokio worker 上长时间执行，会拖慢其他异步任务。

源码常见：

```rust
tokio::task::spawn_blocking(move || {
    // std::fs、file lock、压缩、sync
})
.await??;
```

它只是把阻塞工作移到专用线程池，不会让文件系统操作本身变成异步或自动可取消。

## 30. 消息历史为什么使用 append-only JSONL

`history.jsonl` 每一行是一个完整 JSON object：

```json
{"session_id":"...","ts":123,"text":"hello"}
```

优点：

- 新记录通常只需追加；
- 旧记录不必整体重写；
- 单行损坏较容易隔离；
- 标准 JSONL 工具可读取；
- 文件过大时可以按完整行裁剪。

但并发 append 仍需要明确互斥和边界。

## 31. advisory file lock 是什么

`append_entry` 调用 `File::try_lock()` 取得独占 advisory lock；读取单条记录时用 `try_lock_shared()`。

`advisory` 表示参与者必须自觉遵守锁协议。另一个完全不加锁的程序仍可能修改文件。

对比：

- exclusive lock：writer 独占；
- shared lock：多个 reader 可共享；
- advisory：合作式约定；
- mandatory：操作系统强制阻止冲突访问，较少作为跨平台默认模型。

## 32. 为什么锁要有重试上限

历史模块最多尝试 `MAX_RETRIES`，每次 `WouldBlock` 后等待 `RETRY_SLEEP`。

如果无限阻塞：

- 持锁进程卡死会拖住所有调用者；
- 异常情况下无法向用户报告；
- shutdown 可能永远等不到。

有界等待把锁竞争变成可诊断错误。

## 33. 锁必须覆盖整个 read-modify-write

历史文件超过硬上限时，会在独占锁内：

1. 追加新行；
2. flush；
3. 扫描行边界；
4. 保留较新的尾部；
5. 重写并截断。

如果只锁住最后的 `write`，两个进程可能都根据旧长度决定裁剪，仍然产生 lost update。

## 34. `create_new(true)` 是原子的名字预留

`OpenOptions::create_new(true)` 表示“只有路径不存在时才创建，否则报 `AlreadyExists`”。

它比下面的 check-then-create 更安全：

```rust
if !path.exists() {
    File::create(path)?;
}
```

后者在检查和创建之间有竞态窗口。App-server daemon 用 `create_new(true)` 预留 PID 文件，再结合 reservation lock 判断谁负责启动进程。

## 35. TOCTOU 是什么

TOCTOU = Time Of Check To Time Of Use：检查时与使用时之间，文件系统对象发生变化。

```text
检查 candidate 位于 workspace 内
              ↓ 攻击者替换某级目录为 symlink
打开 candidate 并写入
```

单次 `canonicalize` 后再按原始字符串打开，并不能消除所有强对手竞态。高安全级别实现可能需要基于已打开 directory handle、`openat`/`openat2`、no-follow 标志或平台 sandbox enforcement。

## 36. 为什么应用层检查仍然有价值

应用层 canonicalize/scope 检查不能替代 sandbox，但仍能：

- 拒绝数据库中的错误或过期路径；
- 给出更明确的业务错误；
- 防止普通 bug 越界删除；
- 缩小交给底层执行器的输入范围；
- 形成 defense in depth。

官方资料强调，sandbox 才是限制命令可访问文件范围的技术边界；approval policy 决定何时需要请求越界授权。

## 37. Journal 解决跨多个持久系统的崩溃中间态

Rollout migration 同时涉及：

- JSONL rollout；
- SQLite projection；
- staged/compressed 文件；
- “迁移已完成”的 metadata。

一个 rename 无法让文件和 SQLite 跨系统共同原子提交。因此源码先创建：

```text
rollout-migrations/<thread_id>.pending
```

这个 durable journal 告诉重启流程：“此 thread 可能停在迁移中间，需要恢复或清理。”

## 38. Journal 为什么必须在完成后才删除

迁移的大致提交顺序：

```text
写并同步 .pending
  → 生成并同步 staged rollout
  → 建立完整 SQLite projection
  → 原子发布新 rollout
  → 标记 DB metadata
  → 删除 .pending 并同步目录
```

如果过早删除 marker，随后的崩溃会留下无法判断来源的半完成组合。

## 39. 发布前为何重新检查源文件

迁移可能耗时。旧 Codex writer 不认识新 migration lock，仍可能追加源 rollout。

因此发布前源码再次比较：

- file length；
- modified time。

若变化就返回 conflict，不用基于旧快照生成的 staged 文件覆盖新数据。

这是 optimistic concurrency check；它降低竞态风险，但 size + mtime 并不是通用密码学身份。

## 40. Cache 和权威日志为什么采用不同耐久等级

Connector runtime cache 使用：

```text
bounded read
serialize
同目录 NamedTempFile
persist replace
```

失败时可以重新向权威源获取，所以没有走完整 journal + directory sync 流程。

Rollout 承担 thread 恢复，则采用 staged file、`sync_all`、projection 校验、journal 和恢复逻辑。工程上应按“丢失后果”选择成本，而不是所有文件一律最高强度。

## 41. 读取也需要大小上限

Connector cache 先读 metadata length，再用：

```rust
file.take(MAX_BYTES + 1).read_to_end(&mut bytes)?;
```

之后再次检查实际读取长度。

原因是文件可能在 metadata 检查后继续增长。双重检查避免把本地异常文件无限读入内存，也是对 TOCTOU 的一种有界防护。

## 42. Unix 文件权限 `0o600` 表示什么

消息历史在 Unix 上将文件设为：

```text
rw-------
```

也就是 owner 可读写，group 和 other 无权限。

这能降低同机其他普通账号读取的风险，但不等于：

- 文件内容加密；
- root/管理员不可读；
- 备份和日志不会泄漏；
- Windows 拥有等价的 mode bit 语义。

## 43. `rename` 是移动还是原子替换

同一 API 可能用于两个不同业务意图：

- 发布：临时文件 rename 到最终名称；
- 搬家：active rollout rename 到 archived 目录。

Thread archive/unarchive 都在 `codex_home` 下创建目标目录后使用 `std::fs::rename`。若未来允许 archive 目录位于另一个 volume，就必须考虑 cross-device fallback；而 copy + delete 不再天然是单步原子移动。

## 44. Windows 和 Unix 的 symlink API 不同

Git utility 的平台分支说明：

- Unix：一个通用 `std::os::unix::fs::symlink`；
- Windows：需要选择 `symlink_file` 或 `symlink_dir`；
- Windows 创建 symlink 还可能受权限或系统设置影响。

所以跨平台抽象不应假设“链接不区分文件和目录”。

## 45. Windows 路径还多了哪些概念

常见形式：

```text
C:\work\repo
\\server\share\repo        # UNC
\\?\C:\very\long\path    # device/verbatim namespace
```

`AbsolutePathBuf` 会识别并规范 Windows device alias；`normalize_for_native_workdir` 在 Windows 使用 `dunce::simplified`，避免把内部可用但用户和部分工具不易处理的 verbatim 前缀到处传播。

## 46. WSL 的路径比较为何可能转小写

WSL 内部 Linux 路径通常区分大小写，但 `/mnt/c/...` 常映射到 Windows 文件系统，路径比较往往需要按 ASCII 大小写不敏感处理。

`normalize_for_wsl` 只对形如：

```text
/mnt/<single-letter-drive>/...
```

的路径做 ASCII lower-case，而不是把所有 Linux 路径都转小写。

## 47. 进程内 Mutex 和文件锁不能混为一谈

`PLUGIN_SHARE_LOCAL_PATHS_LOCK: Mutex<()>` 只协调当前进程中的线程。如果同时运行两个 Codex 进程，它们各有自己的 Mutex。

文件锁或数据库锁才可能跨进程协调；即使如此，也要确认具体平台和文件系统是否支持预期语义。

选择前先问并发范围：同 task、同进程、同机器多进程，还是网络中的多台机器？

## 48. 常见错误清单

### 错误一：原地 truncate 后重写重要配置

崩溃可能只留下前半份，优先考虑同目录临时文件 + replace。

### 错误二：把 `rename` 当成已经断电耐久

高价值数据还要考虑文件和父目录同步、恢复 marker。

### 错误三：先 `exists()` 再创建唯一文件

使用 `create_new(true)` 或真正的锁/原子数据库约束。

### 错误四：用字符串前缀证明路径位于目录内

使用 path component；涉及 symlink 时选择合适的 canonicalization 和底层强制边界。

### 错误五：把 Path 无条件转成 UTF-8 String

核心身份和 I/O 保留 `Path`/`OsStr`，只在展示或协议要求时转换。

### 错误六：认为进程内 Mutex 能协调多个进程

明确锁的可见范围和 advisory 性质。

### 错误七：临时文件放在系统 `/tmp`

可能跨文件系统，失去 rename 发布保证。

### 错误八：检查完路径后认为永远安全

考虑 TOCTOU、symlink swap 和 sandbox/handle-relative API。

## 49. 理解检查

### 问题一

为什么 `write_all + persist` 仍不等于两个并发配置编辑不会丢失？

<details>
<summary>参考答案</summary>

它保证每次发布的文件内容完整，但两个 writer 仍可能都从旧版本读取，再由后发布者覆盖先发布者。需要额外的锁、version/CAS 或单 writer 协议。

</details>

### 问题二

为什么临时文件通常放在目标目录，而不是统一放 `/tmp`？

<details>
<summary>参考答案</summary>

为了让临时文件与目标大概率处于同一文件系统，使 rename/replace 可以作为原子发布；跨 device rename 可能直接失败。

</details>

### 问题三

`AbsolutePathBuf` 是否证明路径存在并位于 workspace 内？

<details>
<summary>参考答案</summary>

否。它只保证绝对和词法规范化，不保证存在、canonicalized、无 symlink 或处于特定安全根中。

</details>

### 问题四

为什么 rollout migration 需要 `.pending` journal，而 cache 不一定需要？

<details>
<summary>参考答案</summary>

迁移跨 JSONL、SQLite 和多个发布步骤，崩溃后必须识别并修复中间态；cache 可从权威源重建，容忍一次写失败，采用较轻的原子替换即可。

</details>

### 问题五

`flush()` 和 `sync_all()` 的核心区别是什么？

<details>
<summary>参考答案</summary>

flush 主要排空语言库/用户态 buffer；sync_all 进一步请求 OS 将文件内容和 metadata 同步到持久介质。两者仍受具体平台与文件系统语义约束。

</details>

## 50. 本章词汇表与代码名称翻译

| 英文或代码名 | 字面意思 | 在本章中的具体含义 |
|---|---|---|
| Filesystem | 文件系统 | 管理文件内容、目录项、metadata 和持久化语义的 OS 子系统 |
| `Path` | 路径 | 借用的 OS 路径值，不代表目标一定存在 |
| `PathBuf` | 路径缓冲 | 拥有、可构造的 OS 路径值 |
| `OsStr` / `OsString` | OS 字符串 | 可表达平台原生、未必是 UTF-8 的名字 |
| `AbsolutePathBuf` | 绝对路径缓冲 | Codex 中保证绝对且词法规范化的路径 newtype |
| Base / cwd | 基准/当前工作目录 | 解释相对路径所依赖的位置 |
| Component | 路径组成段 | Root、Prefix、Normal、`.`、`..` 等语义单元 |
| Lexical normalization | 词法规范化 | 不访问磁盘地消除 `.`、处理 `..` 并组合 base |
| Canonicalize | 规范成真实路径 | 访问磁盘并解析 symlink 的路径转换 |
| Logical / physical path | 逻辑/物理路径 | 用户看到的入口名称与解析链接后的真实目标 |
| Symlink | 符号链接/软链接 | 内容是另一个路径的特殊目录项 |
| `symlink_metadata` | 链接自身元数据 | 不跟随最终 symlink 地检查目录项类型 |
| `read_link` | 读取链接 | 取得 symlink 保存的 target 路径 |
| Symlink cycle | 链接环 | 链条重新回到已访问路径，无法得到最终目标 |
| Atomic write | 原子写入 | 读者不观察到发布中间态的写入协议 |
| Staging | 暂存/预备 | 在不可见或非最终路径生成完整新内容 |
| `NamedTempFile` | 有名字的临时文件 | 在指定目录安全创建并由 RAII 管理的临时文件 |
| `persist` | 持久发布 | 将 NamedTempFile 原子替换/移动到最终路径 |
| `rename` | 改名/移动 | 修改目录项；同文件系统内常用于原子发布或搬家 |
| Cross-device | 跨设备/卷 | 源目标不在同一文件系统，普通 rename 可能失败 |
| Short write | 短写 | 一次 write 只接受部分 buffer |
| `write_all` | 写完全部 | 循环写到完整 buffer 完成或报错 |
| `flush` | 刷新缓冲 | 将用户态 buffer 交给下一层，不自动等于断电耐久 |
| `sync_all` / fsync | 同步落盘 | 请求 OS 同步文件数据与 metadata |
| Parent directory sync | 父目录同步 | 使 create/rename/remove 等目录项变化更耐久 |
| Atomicity | 原子性 | 操作对观察者表现为未发生或完整发生 |
| Durability | 持久性 | 成功提交后，崩溃恢复仍保留结果 |
| Isolation | 隔离性 | 并发操作不会形成不可接受的交错 |
| Lost update | 丢失更新 | 后 writer 基于旧快照覆盖先 writer 的修改 |
| File lock | 文件锁 | 协调同一文件 reader/writer 的 OS 锁机制 |
| Advisory lock | 建议锁 | 只约束遵守协议的参与者 |
| Shared / exclusive | 共享/独占 | reader 可并存，writer 需要独占 |
| `WouldBlock` | 将阻塞 | 非阻塞取锁当前失败，调用方可退避重试 |
| `create_new(true)` | 仅当不存在才创建 | 原子地创建名字或返回 AlreadyExists |
| TOCTOU | 检查到使用的时间差 | 文件可在验证后、实际操作前被替换的竞态 |
| Journal | 日志/恢复标记 | 记录跨步骤操作尚未完成，供重启恢复 |
| `.pending` | 待完成 | rollout migration 仍可能处于中间态的耐久 marker |
| Optimistic check | 乐观检查 | 发布前验证源状态未变，否则报告 conflict |
| `spawn_blocking` | 在线程池执行阻塞工作 | 避免 std I/O、锁和 sync 阻塞 Tokio worker |
| JSONL | 每行一个 JSON | 适合 append、逐条扫描和按完整记录裁剪的格式 |
| File mode `0o600` | owner 读写 | Unix 权限位 `rw-------`，不是加密 |
| UNC path | 通用命名约定路径 | Windows 的网络共享路径形式 |
| Verbatim path | 原样设备路径 | Windows `\\?\` namespace 下减少路径重写的形式 |
| WSL | Windows Subsystem for Linux | Linux 进程访问 Windows mounted drive 的环境 |
| Defense in depth | 纵深防御 | 业务校验、路径解析、sandbox 和 approval 共同缩小风险 |

## 51. 源码检查点

建议按这个顺序亲自打开：

1. `codex-rs/utils/path-utils/src/lib.rs`
   - 看 `resolve_symlink_write_paths`、`write_atomically` 和 WSL normalization。
2. `codex-rs/utils/absolute-path/src/lib.rs`
   - 看 `AbsolutePathBuf` 明确保证和“不保证”的内容。
3. `codex-rs/core/src/config/edit.rs`
   - 看配置如何 read-edit-write，并只在最后原子发布。
4. `codex-rs/connectors/src/connector_runtime/persistence.rs`
   - 看 cache 的 bounded read 和同目录 `NamedTempFile`。
5. `codex-rs/message-history/src/lib.rs`
   - 看 JSONL、append、shared/exclusive lock 与权限处理。
6. `codex-rs/thread-store/src/local/helpers.rs`
   - 看 canonical root scope 和 thread-id 文件名验证。
7. `codex-rs/thread-store/src/local/rollout_migration.rs`
   - 看 staged rollout、发布前冲突检查和 commit 顺序。
8. `codex-rs/thread-store/src/local/rollout_migration/publish.rs`
   - 看 `sync_all`、journal 和 parent directory sync。
9. `codex-rs/app-server-daemon/src/backend/pid.rs`
   - 看 `create_new(true)`、reservation lock 和临时 PID 文件发布。
10. `codex-rs/git-utils/src/platform.rs`
    - 对比 Unix/Windows symlink 创建差异。

## 52. 一句话总结

读到文件操作时，不要只问“它有没有调用 write”。依次问：

```text
路径怎样解析，是否可能越界或经过 symlink？
新内容是否先完整 staging？
发布是否原子，临时文件是否同目录？
多个 writer 如何协调，锁覆盖哪个临界区？
flush、file sync、directory sync 做到了哪一级？
跨文件/数据库崩溃后，是否有 journal 可以恢复？
Unix、Windows、WSL 和 remote filesystem 的保证是否相同？
```

能回答这七组问题，才真正知道“写成功了”在这段 Codex 源码里意味着什么。
