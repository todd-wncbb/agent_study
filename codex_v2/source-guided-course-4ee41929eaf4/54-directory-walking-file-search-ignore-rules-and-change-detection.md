# 54：目录遍历、文件搜索、忽略规则与变化检测——Codex 怎样“找到正确的文件”

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方用例把“理解大型代码库、追踪请求流、快速找到正确文件”列为 Codex 的典型工作流，但不承诺具体的本地遍历、ignore 或 watcher 实现。本章的实现事实以当前仓库源码和测试为准。
>
> 官方对照资料：[ChatGPT 与 Codex Use cases](https://learn.chatgpt.com/use-cases)。

## 1. 本章解决什么问题

在 TUI 输入：

```text
@chatwid
```

Codex 很快列出类似：

```text
codex-rs/tui/src/chatwidget.rs
codex-rs/tui/src/chatwidget/tests.rs
```

看起来只是“搜索文件名”，实际上要回答：

- 从哪个目录开始搜？
- 搜文件、目录，还是文件内容？
- 为什么 `.gitignore` 中的文件可能搜不到？
- 隐藏文件是否参与？
- symlink 指向的目录是否继续遍历？
- 多个 root 重叠时结果属于哪个 root？
- 用户每输入一个字符，都重新扫描整个仓库吗？
- 扫描尚未完成时，能否先展示部分结果？
- 新文件在扫描完成后创建，旧 search session 会自动发现吗？
- watcher 一次报告十几个事件时，为什么要合并？
- 被监听的文件还不存在时，如何等待它将来出现？
- OS watcher 事件能否被当成权威状态？

核心认识是：

> “发现候选路径”“按查询排序候选路径”和“感知文件系统发生变化”是三项不同工作；Codex 用不同组件承担它们。

## 2. 用“图书馆找书”建立直觉

可以把代码仓库看成图书馆：

1. 馆员沿书架登记所有可见书名；
2. 读者输入几个不完整字符；
3. 检索器从登记表里按相似度排序；
4. 新书上架铃响后，相关系统决定是否重新登记。

对应源码：

```text
沿书架登记      → ignore::WalkBuilder
登记表          → nucleo index / Injector
模糊排序        → nucleo Pattern + score
输入变化        → FileSearchSession::update_query
新书上架铃      → codex-file-watcher / notify
重新登记或清缓存 → watcher consumer 的业务逻辑
```

“铃响了”只说明可能有变化，并不直接告诉你整个书架的最终状态。

## 3. 三条不要混淆的链路

| 链路 | 输入 | 输出 |
|---|---|---|
| Directory walk | root + ignore 策略 | 候选路径流 |
| Fuzzy match | query + 已索引路径 | 排序后的 top-N |
| File watch | watch path + OS events | 粗粒度 changed paths |

本章会先讲 file search，再讲 watcher，最后解释二者如何组合。

## 4. `codex-file-search` 搜的不是文件内容

这个 crate 的核心列是相对路径：

```rust
cols[0] = Utf32String::from(relative_path);
```

所以它匹配：

```text
src/chatwidget.rs
```

而不是打开文件搜索其中的 `struct ChatWidget`。

内容搜索通常应使用 `rg pattern`；`@` popup 主要解决“我记得文件名大概长什么样”。

## 5. 一条搜索结果包含什么

`FileMatch` 包含：

```rust
pub struct FileMatch {
    pub score: u32,
    pub path: PathBuf,
    pub match_type: MatchType,
    pub root: PathBuf,
    pub indices: Option<Vec<u32>>,
}
```

各字段职责：

- `score`：模糊匹配相关度；
- `path`：相对于某个 search root 的路径；
- `root`：该结果所属根目录；
- `match_type`：文件或目录；
- `indices`：命中的字符位置，供 UI 高亮。

## 6. 为什么同时保存 `root` 和相对 `path`

相对路径适合展示和插入 prompt：

```text
codex-rs/core/src/lib.rs
```

真正访问时可以恢复：

```rust
pub fn full_path(&self) -> PathBuf {
    self.root.join(&self.path)
}
```

多个 workspace root 中可能都有 `README.md`，只保存相对路径无法区分它们。

## 7. 文件和目录都会进入索引

walker 从 `entry.file_type()` 取得类型：

```rust
Some(file_type) if file_type.is_dir() => MatchType::Directory,
_ => MatchType::File,
```

因此搜索 `guides` 可以直接返回 `docs/guides` 目录，而不只返回目录里的文件。

源码把 walker 已观察到的类型保存在 `IndexedEntry` 中，生成结果时无需再次 `stat`，减少额外 I/O 和检查/使用间的变化窗口。

## 8. 搜索分为 walker worker 和 matcher worker

`create_session` 启动两个长期线程：

```text
walker_worker  → 遍历路径并注入 Nucleo
matcher_worker → 处理 query、tick、snapshot 和通知
```

它们通过：

- Nucleo `Injector`；
- `crossbeam_channel` 中的 `WorkSignal`；
- atomic cancellation/shutdown flag；

进行协作。

## 9. 为什么不在 UI 线程里遍历

大型仓库可能包含几十万目录项、网络挂载或较慢磁盘。若 TUI 每次输入都同步遍历：

- 按键响应会卡顿；
- 首批结果必须等全量扫描；
- 用户修改 query 会浪费之前工作。

后台 walker 让 UI 可以在扫描过程中收到逐步改善的 snapshot。

## 10. WalkBuilder 负责什么

源码使用 `ignore::WalkBuilder`，它不仅是递归 `read_dir`：

- 并行遍历多个 root；
- 处理 `.gitignore`、`.ignore`、Git global/exclude；
- 处理 hidden 过滤策略；
- 选择是否跟随 symlink；
- 应用 override glob；
- 支持提前 `Quit`。

这比手写递归更容易保持 Git 风格 ignore 语义。

## 11. 默认为什么只用两个遍历线程

`FileSearchOptions::default()` 的 `threads` 是 2。CLI 注释说明，经验上文件树遍历受 I/O 限制，超过两个线程收益有限。

App-server 单次搜索会根据 CPU 数选择线程，但封顶 `MAX_THREADS = 12`。

这体现一个原则：

> CPU 多不代表磁盘元数据遍历可以线性并行；并发必须有上限。

## 12. 隐藏文件默认允许参与

walker 配置：

```rust
.hidden(false)
```

这里的 `false` 是“不要启用 hidden entry 过滤”，所以 `.vscode/settings.json` 等隐藏路径可以成为候选。

但“允许隐藏”不等于“忽略规则失效”：隐藏文件仍可能被有效 `.gitignore` 排除。

## 13. symlink 目录默认会继续遍历

配置中有：

```rust
.follow_links(true)
```

测试证明，指向目录的 `guides-link` 会被分类成 `Directory`。

跟随链接的影响包括：

- 可以搜到通过链接暴露的内容；
- 同一物理内容可能有多个逻辑路径；
- 遍历范围可能扩大；
- symlink loop/error 由 walker library 处理，当前 callback 对 entry error 选择继续。

不要把 file search 的便利策略当成 sandbox 安全边界。

## 14. `.gitignore` 为什么不是一个全局正则

Git ignore 规则有目录作用域、顺序和否定规则：

```gitignore
.vscode/*
!.vscode/
!.vscode/settings.json
```

含义是先忽略 `.vscode` 下内容，再重新允许目录和指定文件。后续规则可以改变前面规则的结果。

因此不能简单把所有行拼成一个“不包含这些字符串”的判断。

## 15. `respect_gitignore` 开启时读取哪些规则

默认值是 `true`。walker 可考虑：

- repository 内 `.gitignore`；
- Git global ignore；
- repository exclude；
- `.ignore` 文件；
- 合适范围内的 parent ignore。

但源码额外设置 `require_git(true)`，限制 `.gitignore` 只在存在 Git context 时生效。

## 16. `require_git(true)` 修复了什么真实问题

`ignore` crate 在非 Git 场景下可以读取所有祖先目录的 `.gitignore`。如果 home 目录恰有：

```gitignore
*
```

它可能意外隐藏下面一个尚无 `.git` 的项目全部文件。

Codex 设置 `require_git(true)` 以贴近 Git 自己的语义：没有 Git repository context 时，不让祖先 `.gitignore` 悄悄吞掉所有结果。

源码中有针对 issue #3493 的回归测试。

## 17. 有 `.git` 时本地规则仍会生效

另一个测试创建 `.git` 目录后验证：

- `package.json` 被白名单放回，可搜索；
- `.vscode/extensions.json` 仍被忽略；
- `.vscode/settings.json` 因否定规则可搜索。

所以 `require_git(true)` 不是关闭 `.gitignore`，而是限定它在哪种上下文中合法生效。

## 18. 关闭 `respect_gitignore` 意味着什么

关闭时源码一起禁用：

```rust
.git_ignore(false)
.git_global(false)
.git_exclude(false)
.ignore(false)
.parents(false)
```

这比“只忽略 `.gitignore` 文件”范围更广。调用者应该把选项理解成“关闭 ignore-file processing”，而不是一个单一文件开关。

## 19. `exclude` 参数怎样工作

`FileSearchOptions.exclude` 进入 `OverrideBuilder`。每个排除 pattern 会被改写为：

```rust
format!("!{exclude}")
```

在 ignore override 语义中，这表示显式排除匹配路径。

override 是调用本次搜索的额外政策，与仓库自己的 `.gitignore` 来源不同。非法 glob 会让 session 创建失败，而不是悄悄忽略错误配置。

## 20. 多个 root 如何遍历

第一个 root 创建 `WalkBuilder`，其他 root 通过 `.add(root)` 加入同一次并行 walk。

这允许 App-server 搜索多个 workspace root，而不用启动完全独立的 matcher。

每个结果仍保留所属 root，客户端才能构造完整路径。

## 21. 重叠 root 时为什么选择更深的 root

假设：

```text
root 0 = /work
root 1 = /work/project
file   = /work/project/src/lib.rs
```

`get_file_path` 会比较 component depth，选择 `/work/project`，于是相对路径是：

```text
src/lib.rs
```

而不是 `project/src/lib.rs`。更具体的 root 通常更符合调用者的 workspace 语义。

## 22. 非 UTF-8 路径在 file search 中会怎样

walker 先要求：

```rust
let Some(full_path) = path.to_str() else { continue; };
```

相对路径也必须 `to_str()` 成功。因此非 UTF-8 path 会被跳过。

这与上一章的“核心文件 I/O 尽量保留 Path/OsStr”并不矛盾：Nucleo 匹配和协议/UI 需要 Unicode 字符序列，当前产品边界选择不索引无法表示的名称。

## 23. Nucleo 是什么

Nucleo 是用于增量模糊匹配的引擎。Codex 建立：

```rust
Nucleo::new(Config::DEFAULT.match_paths(), ...)
```

然后 walker 通过 `Injector` 不断加入路径。

matcher 不必自己维护“所有 String + 每次全量排序”的简单但昂贵实现。

## 24. Fuzzy match 与 substring match 的区别

substring 要求连续出现：

```text
query: chatwid
path:  chatwidget.rs  → 命中
```

fuzzy match 允许字符按顺序分散，并根据紧密程度、边界等计算 score：

```text
query: cwr
path:  chat_widget_renderer.rs
       ^    ^      ^
```

具体分值属于 matcher 实现细节，调用方应依赖排序而不是硬编码某个 score 数字。

## 25. 大小写和字符 normalization

query reparse 使用：

```rust
CaseMatching::Ignore
Normalization::Smart
```

直观理解：默认忽略大小写，并让匹配引擎按 smart normalization 处理字符差异。

不要将它误解成文件系统路径相等规则；这是搜索相关度语义，不是 Windows/Unix identity 判断。

## 26. 为什么用户继续输入时不重建索引

`FileSearchSession::update_query` 只发送：

```rust
WorkSignal::QueryUpdated(query)
```

walker thread 不会因此重新启动。matcher 对已有和仍在注入的候选重新计算。

这就是 session API 的主要价值：昂贵的目录遍历与高频 query update 解耦。

## 27. 追加字符还能进一步优化

matcher 判断：

```rust
let append = query.starts_with(&last_query);
```

如果旧 query 是 `chat`，新 query 是 `chatw`，新结果一定是旧候选的进一步收窄，Nucleo 可以利用 append 信息增量 reparse。

删除字符或修改中间字符时则不能做同样假设。

## 28. 为什么扫描没结束也能出结果

Nucleo 注入新 entry 时触发 `NucleoNotify`。matcher 进行 `tick` 后形成 snapshot，并设置：

```rust
walk_complete: false
```

测试明确要求 session 在完整 walk 之前产生 update。

用户因此先看到“当前已知的最佳结果”，随后结果会继续变化。

## 29. Snapshot 不是最终真相

`FileSearchSnapshot` 包含：

```rust
query
matches
total_match_count
scanned_file_count
walk_complete
```

当 `walk_complete == false`：

- `scanned_file_count` 还会增长；
- top-N 可能被后来发现的高分路径替换；
- `total_match_count` 只是当前索引的匹配数。

UI 应允许结果刷新，而不是把第一批结果锁死。

## 30. `limit` 与 `total_match_count` 为什么同时存在

假设共有 300 个匹配，但只显示 20 个：

```text
matches.len()      = 20
total_match_count  = 300
```

调用者可以显示“仅展示前 20 个”，而不会误以为仓库只有 20 个匹配。

CLI 默认 limit 是 64，library 默认是 20，App-server 使用 50；默认值属于不同入口，不应混为一个全局常量。

## 31. 排序怎样保证稳定可读

App-server 最终按：

1. score 降序；
2. path 升序；

排序。

分数相同时用路径作为 tie-breaker，避免 Hash/并行遍历顺序导致结果随机抖动。

## 32. `indices` 为什么是可选的

计算命中字符位置需要额外 matcher 工作。只有 `compute_indices: true` 时生成：

```text
chatwidget.rs
^^^^   ^
```

索引会排序并去重，UI 可以直接用来高亮。纯 CLI/后台调用若不展示高亮，可以关闭以减少成本。

## 33. Matcher update 为什么做短 debounce

Nucleo notify 可能在大量 entry 注入时频繁发生。matcher 将通知合并，并用约 10ms tick window 更新结果。

目标是平衡：

- 太频繁：CPU 和 UI event 压力高；
- 太稀疏：首批结果显得迟钝。

这里是搜索结果更新节流，不是 OS file watcher 的 200ms/10s 策略。

## 34. 取消为何不是每个文件都检查

walker callback 每处理 `CHECK_INTERVAL = 1024` 个 entry 才检查 cancel/shutdown flag。

每项都做 atomic load 会增加热路径成本；检查太少又会让取消延迟过长。1024 是当前实现的折中，不是通用最佳值。

matcher 即使暂时没有 channel signal，也通过 100ms default branch 周期检查 flag。

## 35. Drop session 与共享 cancel flag 的区别

`FileSearchSession::drop` 设置它自己的 `shutdown`，并发送 `WorkSignal::Shutdown`。

它不会把调用者传入的共享 `cancel_flag` 改成 true。测试验证：两个 session 共享一个 cancel flag 时，drop A 不会误取消 B。

所以：

- `shutdown`：该 session 生命周期结束；
- `cancelled`：调用者要求相关工作取消。

## 36. 为什么 App-server 要过滤过期 query snapshot

后台可能先开始计算 `cha`，用户已经输入 `chat`。旧 snapshot 若晚到，会让 UI 倒退。

App-server reporter 比较：

```rust
if snapshot.query != latest_query {
    return;
}
```

TUI 本地 manager 还使用 `session_token` 隔离旧 session。它们都是 generation/staleness fencing 的具体应用。

## 37. 旧 session 会自动发现后来创建的文件吗

不会保证。

file-search session 启动一个 walker；walk complete 后，更新 query 只复用已有 Nucleo index。源码没有把 `codex-file-watcher` 接到这个 index 上做增删维护。

若 CWD 变化，TUI 明确 drop 当前 session，下次 query 重建。若普通文件在完成后新增，通常需要新 session/重新扫描才能可靠出现。

## 38. “搜索”与“变化检测”为何分成两个 crate

两者的正确性和负载模型不同：

- search 要枚举完整候选并计算 top-N；
- watcher 只需低成本提示某些路径可能变化；
- search 可容忍扫描期间逐渐完整；
- watcher 必须管理长生命周期注册、取消和事件风暴。

拆分后 Skills、App-server fs API 等都能复用 watcher，而不依赖 fuzzy matcher。

## 39. `codex-file-watcher` 的输出为什么是粗粒度事件

`FileWatcherEvent` 只有：

```rust
pub paths: Vec<PathBuf>
```

它没有承诺每条路径当前是新增、修改还是删除，也不携带完整文件内容。

因为不同 OS backend 对 rename、批量写入和目录变化的事件拆分不同。消费者收到通知后应重新读取/扫描权威状态。

## 40. 哪些 OS event 会向上传递

当前只接受：

```text
Create
Modify
Remove
```

普通 `Access(Open)` 会被过滤，避免“仅仅读文件”触发无意义的缓存失效或通知循环。

测试分别覆盖 create/modify/remove 为 true，access 为 false。

## 41. 多 subscriber 怎样共享底层 watch

`FileWatcher` 为每个 subscriber 保存独立 path registration 和 channel，同时对实际 OS path 维护 ref count：

```rust
PathWatchCounts {
    non_recursive,
    recursive,
}
```

只要有任何 recursive 注册，effective mode 就是 recursive；recursive guard drop 后可降级回 non-recursive，而不是永远保持更昂贵模式。

## 42. RAII registration 解决什么

`register_paths` 返回 `WatchRegistration`。guard drop 时自动 unregister；subscriber drop 时清理它拥有的所有注册。

这避免错误返回或连接关闭后遗留 OS watch 和 receiver。App-server 将 guard 保存在 `WatchEntry` 中，让注册寿命与 `(connection_id, watch_id)` 一致。

## 43. 为什么事件路径要排序去重

Receiver 内部使用：

```rust
BTreeSet<PathBuf>
```

同一个编辑动作可能产生多条重复 backend event。集合会去重，BTreeSet 还提供稳定排序。

这降低下游重复 reload，并让测试、协议输出更确定。

## 44. Throttle 与 debounce 有什么区别

### Throttle

限制两次输出之间的最小间隔。Skills watcher 使用 10 秒 throttle，避免文件安装/解压产生大量缓存清理。

### Debounce

收到第一条事件后打开固定窗口，把窗口内事件合并成一批。App-server `fs/watch` 使用 200ms debounce。

简单记忆：

```text
throttle → 最多多久发一次
debounce → 等一小段，把一阵变化合起来
```

## 45. 为什么关闭 receiver 前还要 flush pending paths

`DebouncedWatchReceiver` 和 `ThrottledWatchReceiver` 测试都验证 shutdown 时不会直接丢掉已收集的变化。

若 sender 已关闭但集合里还有 `a`、`b`，receiver 先返回最后一个 batch，下一次才返回 `None`。

这使正常 teardown 不会吞掉已经进入本地 channel 的最后变化提示。

## 46. 监听一个尚不存在的文件怎么办

OS watcher 通常不能直接 watch 不存在的目标。`actual_watch_path` 会寻找最近存在的 directory ancestor：

```text
请求监听 /repo/.git/FETCH_HEAD
目标不存在
实际先监听 /repo/.git（non-recursive）
```

当 ancestor event 表明目标出现后，watch 会移动到更接近、最终等于 requested path 的位置。

## 47. 为什么 fallback ancestor 不使用 recursive watch

假设请求 `/repo/a/b/target`，最近存在 ancestor 是 `/repo`。直接 recursive watch `/repo` 可能带来海量无关事件。

源码对 fallback 使用 non-recursive，并在路径 component 逐步创建时移动 actual watch。

这是“扩大监控范围”和“可能漏掉深层创建”之间通过逐级迁移实现的受控策略。

## 48. requested、matched 与 actual 三种路径

watcher 内部区分：

| 路径 | 含义 |
|---|---|
| requested | subscriber 原本要求、回报给客户端的逻辑路径 |
| matched | canonicalized 后用于匹配 backend event 的路径 |
| actual | 当前真正交给 OS watcher 的已存在路径 |

例如 macOS backend 可能把 `/var/...` 事件报告成 `/private/var/...`。内部用 matched 对齐，输出再映射回 requested，避免客户端看到实现细节。

## 49. Skills watcher 收到变化后做什么

Skills watcher 递归注册相关 skill roots。事件通过 10 秒 throttle 后：

1. 排除只属于生成 system skill cache 的事件；
2. `skills_service.clear_cache()`；
3. 发送 `SkillsChanged` notification。

它不尝试根据单条 watcher event 精确 patch 缓存，因为粗粒度 invalidation 更容易保持正确。

## 50. App-server `fs/watch` 的所有权边界

watch key 是：

```text
(connection_id, watch_id)
```

不同连接可以使用相同文字 watch ID，不会互相覆盖。同一连接重复 ID 会返回 invalid request。

`unwatch` 还等待 watcher task 确认结束，再返回 response，保证响应之后不继续发送该 watch 的 notification。

## 51. Git fsmonitor 为什么需要安全覆盖

Git 的 built-in fsmonitor daemon 可以减少反复扫描所有 tracked file 和 untracked directory。

但 repository 配置也可能把 `core.fsmonitor` 设置为任意 executable helper。Codex 的内部 Git 命令不会直接执行仓库选择的 helper：

- 有效值为安全的 boolean true；
- Git 明确报告 built-in daemon capability；
- 才保留 `core.fsmonitor=true`；
- 其他情况统一覆盖为 `core.fsmonitor=false`。

性能优化不能扩大不可信仓库的代码执行面。

## 52. 为什么每次都重新探测 Git fsmonitor

源码注释说明 Git config 是分层的，还可能使用 conditional include，并能在 Codex 运行期间变化。

因此 `detect_fsmonitor_override` 每次探测 effective value，而不是永久缓存第一次结论。

它还要求 probe 有界、成功退出并返回可解析 stdout；timeout、非零退出、异常格式都 fail closed 为 Disabled。

## 53. 常见错误清单

### 错误一：把 fuzzy file search 当成全文搜索

它索引相对路径；找符号定义或字符串内容优先使用 `rg`、语言服务或语义工具。

### 错误二：用户每输入一个字符就重新 walk

应复用 session index，只更新 query。

### 错误三：第一批 snapshot 当成最终完整结果

检查 `walk_complete`，允许 top-N 在扫描中变化。

### 错误四：以为 hidden(false) 会关闭所有忽略规则

它只允许 hidden entries；`.gitignore` 等仍是另一层过滤。

### 错误五：把关闭 gitignore 理解成只关闭一个文件

当前选项还关闭 global/exclude、`.ignore` 和 parent processing。

### 错误六：把 watcher event 当成精确变更日志

事件是提示；收到后重新读取权威状态。

### 错误七：watch missing path 直接失败

可以先监听最近存在 ancestor，再随目录出现移动 watch。

### 错误八：忘记清理 registration

使用 RAII guard，并把 guard 生命周期绑定到 connection/session owner。

### 错误九：执行仓库配置的 fsmonitor helper

不可信 Git config 可能选择 executable；内部命令应覆盖或严格验证。

## 54. 理解检查

### 问题一

为什么用户把 query 从 `chat` 改成 `chatw` 时不需要重新遍历仓库？

<details>
<summary>参考答案</summary>

walker 已经把路径注入 Nucleo index；query update 由 matcher worker 对同一索引重新匹配，追加字符还可利用 append 增量信息。

</details>

### 问题二

`walk_complete == false` 的 snapshot 能不能展示？

<details>
<summary>参考答案</summary>

可以，它就是为流式首批结果设计的；但 UI 必须允许后续 snapshot 替换 top-N，不能把当前 count 当成最终值。

</details>

### 问题三

为什么 file watcher 收到 `Modify(path)` 后不直接认为文件一定存在且内容完整？

<details>
<summary>参考答案</summary>

不同 OS backend 的事件可能合并、拆分、延迟或与 rename/delete 交错；事件只是促使消费者重新读取权威状态的粗粒度信号。

</details>

### 问题四

一个 file-search session 完成 walk 后，新建 `brand-new.rs` 会自动加入吗？

<details>
<summary>参考答案</summary>

当前实现没有用 watcher 增量维护 search index，因此不能保证。需要重建 session/重新 walk 才能可靠纳入。

</details>

### 问题五

为什么 Codex 不直接信任仓库中的 `core.fsmonitor=/path/helper`？

<details>
<summary>参考答案</summary>

该配置会让内部 Git 操作执行仓库选择的程序，扩大不可信代码执行面。Codex 只在确认是受支持的 built-in boolean mode 时保留加速，否则禁用。

</details>

## 55. 本章词汇表与代码名称翻译

| 英文或代码名 | 字面意思 | 在本章中的具体含义 |
|---|---|---|
| Directory walk | 目录行走/遍历 | 从 root 递归枚举候选文件和目录 |
| Search root | 搜索根 | 相对结果路径的基准目录 |
| Candidate | 候选项 | 已遍历发现、可以进入 matcher 的路径 |
| Index | 索引 | Nucleo 保存并用于重复 query 匹配的候选集合 |
| `WalkBuilder` | 遍历构建器 | 配置 roots、threads、ignore、hidden、symlink 的 walker |
| `Injector` | 注入器 | walker 向 Nucleo 增量加入 entry 的接口 |
| `IndexedEntry` | 已索引条目 | 保存 full path 与 walker 已观察 match type 的数据 |
| Fuzzy match | 模糊匹配 | query 字符可不连续但保持顺序的相关度匹配 |
| `Nucleo` | 模糊匹配引擎名 | 维护候选、pattern、score 和 snapshot 的 library |
| Query | 查询 | 用户当前输入的路径搜索文字 |
| Pattern | 模式 | matcher 解析后的查询表示 |
| Score | 分数 | 模糊匹配相关度，越高排序越靠前 |
| Match indices | 命中下标 | query 对应字符在 path 中的位置，供 UI 高亮 |
| Top-N | 前 N 项 | 只返回分数最高的 limit 个结果 |
| Tie-breaker | 平分决胜规则 | score 相同时按 path 升序稳定排序 |
| Snapshot | 快照 | 某次 tick 时 query、matches、count 和 complete 状态 |
| `walk_complete` | 遍历完成 | walker 是否已经结束全部 root 扫描 |
| `scanned_file_count` | 已扫描条目数 | 当前进入 matcher index 的 item 数，扫描中单调增加 |
| Streaming update | 流式更新 | 完整 walk 前先发部分 top-N，随后继续校正 |
| `WorkSignal` | 工作信号 | QueryUpdated、NucleoNotify、WalkComplete、Shutdown |
| Ignore rule | 忽略规则 | 决定某路径是否不进入候选集的层级 pattern |
| `.gitignore` | Git 忽略文件 | 在 repository scope 内按 Git 风格过滤路径 |
| Negation rule | 否定规则 | 用 `!` 将先前忽略的特定路径重新纳入 |
| `require_git` | 要求 Git context | 只在仓库上下文中应用 `.gitignore` |
| Override glob | 覆盖 glob | 调用本次搜索额外提供的 include/exclude 政策 |
| Hidden entry | 隐藏条目 | Unix 点文件等通常不默认展示的路径 |
| Follow links | 跟随链接 | 遍历 symlink 指向目录的内容 |
| Cancellation flag | 取消标志 | worker 周期读取、请求提前停止的 AtomicBool |
| Session token | 会话世代号 | 防止旧 session snapshot 更新新 popup |
| File watcher | 文件观察器 | 通过 OS backend 接收路径变化提示的长期组件 |
| `notify` | Rust watcher library | 提供平台适配的 `RecommendedWatcher` |
| Watch subscriber | 观察订阅者 | 拥有独立注册集合与事件 receiver 的逻辑消费者 |
| Watch registration | 观察注册 guard | Drop 时自动注销路径的 RAII 对象 |
| Recursive watch | 递归监听 | 接受目标及所有后代路径事件 |
| Non-recursive watch | 非递归监听 | 只接受目标或直接子项范围的事件 |
| Ref count | 引用计数 | 多 subscriber 共享一个底层 OS watch 的注册数量 |
| Requested path | 请求路径 | 客户端要求监听并希望在通知中看到的逻辑路径 |
| Matched path | 匹配路径 | canonical namespace 中匹配 backend event 的路径 |
| Actual path | 实际监听路径 | 当前交给 OS watcher 的已存在目标或 ancestor |
| Fallback watch | 回退监听 | requested 不存在时临时监听最近存在 ancestor |
| Coalescing | 合并 | 多个事件路径去重并组成一批 |
| Throttle | 节流 | 限制连续两次输出的最小间隔 |
| Debounce | 防抖/合并窗口 | 收到首事件后等待固定窗口收集同一批变化 |
| Mutating event | 修改类事件 | Create、Modify、Remove，而不是 Access/Open |
| Cache invalidation | 缓存失效 | watcher 提示后清空派生状态并重新加载 |
| fsmonitor | 文件系统监视加速 | Git 用变化提示减少 status/diff 全量扫描的机制 |
| Built-in daemon | 内建守护进程 | Git 自己实现、经 capability 确认的 fsmonitor 模式 |
| Helper executable | 辅助可执行程序 | repository config 可指定、Codex 内部命令不应盲信的程序 |
| Fail closed | 失败时关闭能力 | 探测失败或格式异常时禁用 fsmonitor helper/加速 |

## 56. 源码检查点

建议按以下顺序阅读：

1. `codex-rs/file-search/src/lib.rs`
   - 先看 `FileSearchOptions`、`create_session`、`walker_worker`、`matcher_worker`。
2. 同文件测试区
   - 看流式 snapshot、query reuse、cancel、symlink 和 gitignore regression。
3. `codex-rs/tui/src/file_search.rs`
   - 看 `@` query 怎样复用 session，并用 token 丢弃旧结果。
4. `codex-rs/app-server/src/fuzzy_file_search.rs`
   - 看 multi-root、50 result limit、indices 和 notification fencing。
5. `codex-rs/app-server/src/request_processors/search.rs`
   - 看 cancellation token、session start/update/stop 与 map owner。
6. `codex-rs/file-watcher/src/lib.rs`
   - 看 subscriber、ref count、RAII registration、fallback path 和 event routing。
7. `codex-rs/file-watcher/src/file_watcher_tests.rs`
   - 看 missing target、recursive scope、dedupe、throttle/debounce 和 shutdown flush。
8. `codex-rs/app-server/src/fs_watch.rs`
   - 看 `(connection_id, watch_id)` 所有权、200ms debounce 和 unwatch barrier。
9. `codex-rs/app-server/src/skills_watcher.rs`
   - 看 10 秒 throttle、recursive root 和 coarse cache invalidation。
10. `codex-rs/git-utils/src/fsmonitor.rs`
    - 看 repository helper 防护、boolean normalization 和 capability probe。

## 57. 一句话总结

读目录搜索或 watcher 代码时，按下面顺序提问：

```text
root 是什么，结果是相对哪个 root？
搜的是路径还是内容，文件和目录是否都纳入？
hidden、gitignore、override 和 symlink 各自怎样过滤或扩大范围？
遍历与 query matching 是否解耦，能否流式返回？
snapshot 是否完整，旧 query/session 怎样被栅栏挡住？
index 是否会因后续文件变化自动刷新？
watcher 事件是权威事实还是重新读取的提示？
注册怎样共享、合并、节流并在 Drop/unwatch 时清理？
```

能回答这些问题，就能解释“为什么 Codex 找到了这个文件，也为什么另一个文件暂时没有出现”。
