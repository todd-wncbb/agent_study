# Walkthrough：`@` 文件引用如何从模糊补全变成模型上下文

> 场景：用户在 Prompt 中输入 `请检查 @src/mai`，Pager 需要从当前 Session CWD 异步搜索文件，避免把 Email 当成文件引用，允许进入隐藏文件模式和目录下钻，把选中的文件折叠成不可拆坏的原子 Chip，还能通过 Viewer 选择行号范围。发送后，Chip 的底层文本必须进入队列；Shell 再识别 `@path`、读取文件、限制 Context 预算、添加行号，并把用户 Query 与附件 Context 分开交给模型。与此同时，旧搜索结果、Worktree 切换、空格路径、单行范围和手写 Token 都存在容易混淆的边界。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
User types "@src/mai"
  -> PromptWidget::update_file_search_context
  -> context::detect_with_drill(text, cursor, drill_prefix)
  -> AtContext { range, cursor, query }
  -> FileSearchState::start_query
  -> lazy FuzzyFileMatcherDaemon
  -> ignore WalkBuilder + nucleo fuzzy matcher
  -> generation-stamped top-k snapshots
  -> Pager tick polls latest non-stale results
  -> dropdown navigation / directory drill / file acceptance
  -> atomic KIND_FILE_REF element whose backing text remains "@src/main.rs"

Optional line selection
  -> ':' or Ctrl-L
  -> LineViewerState reads file relative to Session CWD
  -> visual selection
  -> element becomes "@src/main.rs:10-20"

Submit
  -> PromptWidget stash / into_submission
  -> QueuedPrompt { text, chip_elements, images, ... }
  -> Effect::SendPrompt or SendPromptBlocks
  -> Shell parse_prompt_with_skills
  -> collect_file_references from Text blocks
  -> FileReference::parse
  -> resolve relative path against working_directory
  -> async read + optional line slice + token cap
  -> <user_query> plus <system-reminder><attached_files>...
  -> model request
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| `@` Token 检测 | `crates/codegen/xai-grok-pager/src/views/file_search/context.rs` | `AtContext`、`detect_with_drill` |
| 搜索状态机 | `crates/codegen/xai-grok-pager/src/views/file_search/state.rs` | `FileSearchState` |
| 文件树与模糊线程 | `crates/codegen/xai-grok-workspace/src/file_system/fuzzy.rs` | `FuzzyFileMatcher`、Daemon |
| Dropdown 渲染 | `crates/codegen/xai-grok-pager/src/views/file_search/dropdown.rs` | `render_dropdown` |
| Composer 接入 | `crates/codegen/xai-grok-pager/src/views/prompt_widget/mod.rs` | Accept、Element、Viewer Request |
| 行号 Viewer | `crates/codegen/xai-grok-pager/src/views/file_search/line_viewer.rs` | `LineViewerState` |
| Viewer 生命周期 | `crates/codegen/xai-grok-pager/src/app/agent_view/viewer.rs` | Open、Confirm、Cancel |
| Queue 附件状态 | `crates/codegen/xai-grok-pager/src/app/dispatch/queue.rs` | `drain_prompt_state_to_last_queued` |
| Shell Prompt Parser | `crates/codegen/xai-grok-shell/src/session/prompt_parser.rs` | `parse_prompt_with_skills` |
| 文件内容渲染 | `crates/codegen/xai-grok-workspace/src/file_system/attach_file.rs` | `FileReference`、`render_file_reference` |

## 3. `@` 引用同时有三种身份

它在编辑时是补全 Token，在 TextArea 中是原子 Element，在 Wire 上又退化成普通 `@path` 文本。理解三种身份的转换是本篇主线。

## 4. Chip 不是独立附件协议

Pager 没有把本地 `@` 文件引用发送成 ACP `ResourceLink`；正常路径仍发送 Text，Shell 从 Text 中重新扫描 `@path`。

## 5. 文件内容不是 Pager 搜索时读取的

补全只枚举路径和类型；真正内容在 Shell 接受 Prompt 后才读取并拼进模型上下文。

## 6. UI 路径与模型上下文路径是两个阶段

第一阶段优化交互、匹配和 Draft 安全；第二阶段负责 I/O、范围切片、格式化与预算。

## 7. 为什么不在每次按键时读文件

用户可能只是继续输入路径，读取内容会制造高频 I/O、阻塞和过早暴露大文件；只有提交才需要内容。

## 8. Glossary（一）：三层表示

- **At mention**：以 `@` 标记的文件引用输入。
- **Completion token**：正在参与补全、仍可自由编辑的文本片段。
- **Atomic element**：TextArea 中作为整体移动、删除和渲染的元素。
- **Chip**：原子元素的紧凑视觉表现。
- **Backing text**：Chip 背后仍保存在文本缓冲区中的真实字符串。
- **Wire text**：通过 ACP 发送的文本内容。
- **Attachment context**：最终附加给模型的文件内容区块。

## 9. `AtContext` 保存 Byte Range

字段包括完整 Token Range、Cursor Byte Position 与从 `@` 后到 Cursor 的 Query。Range 包含 `@`。

## 10. 为什么使用 Byte Offset

Rust `String` 与 TextArea Replacement 以 UTF-8 Byte Range 操作；检测入口先验证 Cursor 位于 Char Boundary，防止切开多字节字符。

## 11. 从 Cursor 向左找最右侧 `@`

同一 Prompt 可以有多个引用。Detector 只关心 Cursor 所在的最近候选，而不是始终取全文第一个 `@`。

## 12. Email 防误触规则

若 `@` 前一个字符是 Alphanumeric 或 `_`，Detector 返回 None，因此 `user@example.com` 不打开文件菜单。

## 13. 特殊字符后允许触发

空格、左括号、逗号等之后的 `@foo` 可以进入补全，适合自然语言中的文件引用。

## 14. Token 终止符不只有空格

Whitespace、逗号和分号都会终止 UI Token；这让 `查看 @foo.rs, 然后...` 的编辑边界符合直觉。

## 15. Cursor 必须仍在 Token 内

Cursor 移到终止符之后，旧 `@` Context 会关闭；后续普通文本不再被文件搜索 Dropdown 接管。

## 16. Query 只截到 Cursor

Cursor 位于 `@foo/bar` 中间时，Range 仍覆盖整个 Token，但 Query 只包含 `@` 到 Cursor 的部分，允许中间编辑重新过滤。

## 17. `path_range()` 不含控制前缀

它跳过 `@`，Hidden Mode 时还跳过 `!`。目录下钻只替换 Path 部分，不误删模式标记。

## 18. Glossary（二）：文本坐标

- **Byte offset**：以 UTF-8 字节数计算的位置。
- **Character boundary**：UTF-8 字符可以安全切分的边界。
- **Range**：半开区间 `start..end`。
- **Delimiter**：终止 Token 的分隔符。
- **Cursor-local context**：只由 Cursor 附近文本决定的交互上下文。
- **False positive**：普通文本被错误识别成特殊语法。
- **Path range**：只覆盖路径、排除 `@` 与可选 `!` 的替换范围。

## 19. 普通模式默认尊重隐藏与 Ignore 规则

文件 Walker 使用 `ignore::WalkBuilder`，开启 `.gitignore`、Global Git Ignore、`.git/info/exclude`、普通 Ignore 和 Hidden 过滤。

## 20. `.git` 被显式排除

Override 中固定加入对 `.git` 的排除，即使其他隐藏搜索条件发生变化，也不把 Git 内部对象当普通上下文候选。

## 21. `@!` 开启 Hidden Mode

Query 以 `!` 开头时，`matcher_query()` 去掉 `!`，Walker 则关闭 Hidden、Ignore 与 Git Ignore 过滤。

## 22. `!` 是搜索控制符，不是文件名一部分

它告诉 Pager扩大枚举范围。最终选中文件生成 `@path` Chip 时不会把 `!` 交给 Shell。

## 23. Hidden Mode 需要重走目录树

普通 Query 变化只需复用已有索引；从普通切到 Hidden 会改变可枚举集合，因此 `restart_walk(hidden)`。

## 24. 从 Hidden 切回普通也重走

否则旧 Hidden/Gitignored Entry 仍可能留在 Nucleo Snapshot 中，造成模式关闭后继续泄露候选。

## 25. `@!` 不是安全绕过语法

它只改变补全枚举。最终文件能否读取仍取决于 Shell 进程权限与路径；当前链路没有借此提升 OS 权限。

## 26. Glossary（三）：隐藏与忽略

- **Hidden file**：按平台或名称约定隐藏的文件，Unix 常以 `.` 开头。
- **Gitignored**：被 Git Ignore 规则排除跟踪的路径。
- **Walker**：遍历目录树并产出 Entry 的组件。
- **Ignore rule**：决定遍历时跳过哪些路径的规则。
- **Hidden mode**：通过 `@!query` 扩大候选范围的补全模式。
- **Enumeration**：列举目录树中可搜索路径的过程。
- **OS permission**：操作系统对进程文件访问的许可。

## 27. File Search Daemon 是 Lazy 的

`FileSearchState::new` 只保存 Root；第一次出现有效 `@` Context 才创建 Nucleo Pool、Walker 与 Daemon Thread。

## 28. Lazy 避免无用线程

许多 Session 从不使用 `@`。启动时全部创建搜索线程会放大线程数和 `EAGAIN` 风险。

## 29. 第一次 `@` 承担一次性成本

延迟创建没有消除成本，只把 Thread Spawn 与初始 Walk 移到首次使用时。

## 30. Root 来自当前 Session CWD

`PromptWidget` 构造 File Search 时以 Session 工作目录为 Root，使显示路径通常是项目相对路径。

## 31. Session/Worktree 切换必须 Retarget

Lifecycle、Fork 和 Dashboard Change-directory 路径调用 `retarget(new_root)`；它重置整个 State，下一次使用在新树 Lazy Rebuild。

## 32. 为什么 Retarget 直接丢 Daemon

旧 Worker 正在遍历另一个 Root，复用其 Snapshot 会把父 Worktree 文件混进新 Session；重建更容易保证路径身份。

## 33. Matcher 最多向 State 提供 Top 1000

`MATCHER_TOP_K = 1000` 保留足够的导航候选；Dropdown 实际只显示其中少量行。

## 34. Dropdown 最多显示 8 行

Renderer 的 `MAX_DROPDOWN_ROWS = 8`，超出部分通过 Selection、Scroll Offset 与 Scrollbar 浏览。

## 35. Glossary（四）：线程与根目录

- **Lazy initialization**：第一次真正使用时才创建昂贵资源。
- **Daemon**：持续接收 Query、后台推进并发布 Snapshot 的工作线程。
- **Thread pool**：复用一组线程执行匹配工作的资源池。
- **EAGAIN**：系统暂时无法创建更多线程/进程时常见的错误。
- **Root**：文件搜索所有相对路径的起点。
- **Retarget**：把同一 UI 能力重新指向另一个目录树。
- **Top-K**：只保留排序最高的 K 个结果。

## 36. Thread 不足时有分级降级

Nucleo Pool 无法创建时，Matcher 进入 Browse-only；空 Query 仍可串行列出 Root 顶层 Entry，有 Query 则返回空。

## 37. Parallel Walker 不可用时退到 Serial

代码先探测所需 Thread Slot；不足则使用不会建立 Ignore Pool 的串行 Walk，而不是让整个搜索崩溃。

## 38. Daemon Thread 也可能单独失败

如果 Matcher 已创建但 Worker Thread 被拒绝，Daemon 进入 Disabled，`get()` 返回完成态空 Snapshot。

## 39. 为什么 Disabled Generation 是 `usize::MAX`

State 有 Stale Generation Fence；极大 Generation 可以越过 Fence 并发布“已完成但无结果”，避免 UI 永久以为仍在等待旧 Query。

## 40. Walk 支持取消与 Join

重启前先设置 Cancel 并 Join 旧线程；Drop 也 Join，防止 Matcher 资源先销毁而 Worker 仍在写 Injector。

## 41. Probe-then-build 本身仍有 Race

源码明确承认 Thread Probe 只能降低风险；探测成功到真正 Spawn 之间，其他任务仍可能耗掉 Slot，因此所有实际 Spawn 仍处理失败。

## 42. Empty Query 有专用 Browse 路径

它只进行 Depth-1、按文件名排序的串行枚举，不需要等待完整 Nucleo Index，输入单独 `@` 时能尽快出现顶层内容。

## 43. Keyed Query 使用 Nucleo Smart Case

非空 Query 经过 Path-oriented Config、Smart Case 与 Smart Normalization，支持跨路径片段的模糊匹配。

## 44. Glossary（五）：降级策略

- **Browse-only**：只能列出空 Query 的顶层目录，不能处理模糊 Query。
- **Disabled**：后台能力完全不可用，但以正常空结果收敛。
- **Serial fallback**：并行资源不足时改用单线程遍历。
- **Thread slot**：系统允许创建线程的容量名额。
- **Cancellation flag**：工作线程周期检查并停止的共享标志。
- **Join**：等待线程退出并回收其资源。
- **Graceful degradation**：依赖不足时保留部分功能而非整体失败。

## 45. Query 更新通过有界同步 Channel

Daemon 接收 Restart Walk、Set Query 与 Stop Message；Channel Capacity 为 1024，UI 不直接持有 Worker 内部 Matcher。

## 46. Worker 忙时每 250 微秒检查消息

未完成匹配时用 `recv_timeout` 交替消费命令和推进 Nucleo Tick；完成后阻塞等待下一条消息，避免 Idle Busy-loop。

## 47. 每次状态推进都带 Generation

Restart、Set Query 与发布 Snapshot 会推进代数，使 State 能判断一个结果属于旧输入还是当前输入。

## 48. State 还有单调 `min_generation`

每次新 Query 抬高 Fence；`poll()` 只接受 Generation 不低于 Fence 的 Snapshot。

## 49. 为什么只比较 Shared Arc 还不够

`Arc::ptr_eq` 能判断是否拿到同一 Results Array，却不能证明新 Array 属于当前 Query；Generation 才解决跨 Query 迟到。

## 50. 中间空结果通常不立即发布

若 Top-K 暂时为空且 Matching 尚未 Done，State 保留旧显示，减少输入时 Dropdown 闪烁。

## 51. Done 的空结果必须发布

搜索最终确实没有命中时，旧候选必须消失；否则用户会接受一个与当前 Query 不相干的路径。

## 52. 新结果会 Clamp Selection

若 Result Count 变小，Selection 被限制到最后合法 Index，避免 Right/Enter 访问越界。

## 53. Query 变化还会重置 Hover 与 Scroll

新列表从第一项开始，旧鼠标 Hover 和滚动位置不能错误指向同一数字 Index 下的新文件。

## 54. Glossary（六）：异步新鲜度

- **Generation**：标识异步结果属于哪一代输入的单调编号。
- **Stale result**：来自旧 Query、已不适合当前 UI 的迟到结果。
- **Fence**：拒绝低于某一 Generation 的边界。
- **Snapshot**：Worker 某时刻发布的不可变结果视图。
- **Flicker**：候选在中间状态频繁消失重现的视觉抖动。
- **Clamp**：把数值限制在合法区间。
- **Bounded channel**：容量有限、避免消息无限积压的队列。

## 55. Matcher 不是简单 Substring Search

Nucleo 对 Path 计算 Fuzzy Score，低于随 Query 长度增长的阈值会被过滤，同分时再按 Path Length 与 Path 排序。

## 56. Match Result 保存 Character Indices

Dropdown 用这些位置把命中的字符绘成 Accent Color，使用户理解非连续匹配为何成立。

## 57. Directory Flag 来自 File Type

Walker 只接收 Regular File 或 Directory，并把 `is_dir` 存入 Match Entry，后续键盘语义据此分支。

## 58. Symlink 不会被 Follow

`follow_links(false)` 避免目录循环、意外越出树和重复遍历；Symlink 自身是否成为候选还取决于 Entry Type 过滤。

## 59. Empty Query 与 Keyed Query 排序不同

Browse 按顶层文件名；Keyed Search 主要按 Score，再用较短 Path 和字典序解决 Tie。

## 60. `./` 只在显示时 Normalize

`normalize_display_path` 去掉前导 `./`，让 Chip 和 Dropdown 更紧凑；路径仍相对于 Root 解释。

## 61. Glossary（七）：模糊结果

- **Fuzzy score**：近似匹配质量分数，越高越相关。
- **Match indices**：候选中匹配字符的位置集合。
- **Tie-breaker**：主分数相同后的稳定排序规则。
- **Path normalization**：把等价路径整理成一致显示形式。
- **Symlink**：指向另一文件或目录的文件系统链接。
- **Regular file**：普通文件，不是目录或特殊设备。

## 62. `@src/` 在 Context 层称 Dir Mode

`AtContext::is_dir_mode()` 只检查 Query 是否以 `/` 结尾。

## 63. State 注释与底层 Matcher 有历史差异

当前 `FileSearchState::start_query` 始终调用 `daemon.set_query(query, false)`，所以尾随 `/` 主要形成路径 Query/交互状态，并未把 Matcher 的 `dirs` 参数设为 true。

## 64. 因而 Dir Mode 仍可能看到文件

PromptWidget 的测试明确覆盖 `@src/` 下选中 `src/main.rs` 并生成文件引用；不要把“Dir Mode”理解成结果只含目录。

## 65. Tab/Enter 选文件会 Commit

选中 File 时，整个 `@query` Range 被替换成完整 `@path` Atomic Element，并插入尾随空格，Dropdown 关闭。

## 66. Right 选文件与 Tab 完全相同

文件下没有下一层可钻；Right 也 Commit File、插入空格并关闭，测试要求输出 Byte-identical。

## 67. Right 选目录是不加 `/` 的 Drill

它替换 Path 部分但保留可编辑文本和 Context，不创建 File-ref Element；没有尾随 `/`，下一轮可同时看到匹配的 File 与 Directory。

## 68. 用户可自行输入 `/` 收窄目录意图

Right 的默认行为是“进入这个 Prefix 看内部内容”；若还想只继续找子目录，可再输入 `/`。

## 69. Tab 在尾随 `/` 状态下可继续下钻目录

`try_replace` 给选中目录补 `/`，保持 Completion Context；再次接受相同已存在路径时可视为 Commit 并 Dismiss。

## 70. Replacement 只改 `path_range`

因此 `@!src` 下钻后仍是 `@!src/...`，Hidden Marker 不会被 Directory Selection 静默丢掉。

## 71. Glossary（八）：目录导航

- **Dir mode**：Query 末尾带 `/` 的目录导航状态。
- **Drill-down**：用选中目录替换当前 Prefix，继续浏览更深层。
- **Commit**：把候选确定为最终引用并退出补全。
- **No-op append**：补 `/` 后与已有文本相同，用来识别已确认目录。
- **Prefix**：路径中当前已选择或输入的前缀。
- **Trailing space**：提交文件引用后自动插入的分隔空格。

## 72. 空格目录需要额外 Drill Anchor

普通 Detector 把空格视为 Token 终止符；选择 `my dir` 后若无额外状态，`@my dir` 会立刻失去 Context。

## 73. `drill_prefix` 暂时扩展 Token 边界

`detect_with_drill` 发现 Text 的 Path 部分仍以 Anchor 开头时，把 Anchor 内部空格当路径内容，而不是终止符。

## 74. Hidden Marker 后也能使用 Anchor

检测会从 `@!` 之后验证 Prefix，所以 `@!my dir` 下钻既保留 Hidden Mode，也保持 Dropdown。

## 75. Anchor 是 Self-validating 的

文本不再以原 Prefix 开头时立即丢弃，避免 Undo 或 Paste 后旧 Anchor 在未来某次输入中错误复活。

## 76. Esc 和离开 `@` Mode 都清 Anchor

关闭 Context 只清 Results 不够；Anchor 若残留，重新把 Cursor 移回空格路径可能未经用户操作自动打开菜单。

## 77. Anchor 不进入提交文本

它是纯 UI State；最终 Chip Backing Text 只包含真实 `@path`。

## 78. Glossary（九）：空格路径状态

- **Drill anchor**：临时记录已选择目录前缀、允许其内部空格留在 Token 中的 UI 状态。
- **Self-validating state**：每次使用前重新验证依赖条件，失效即清除的状态。
- **Stale anchor**：文本已变化但仍残留的旧目录锚点。
- **Internal whitespace**：路径名内部而非 Token 分隔用途的空格。
- **Ephemeral state**：只服务当前交互、不持久化或发送的状态。

## 79. File Commit 创建 `KIND_FILE_REF`

TextArea Element Kind 固定为 2。Backing Text 是 `@path`，Display 则由 `file_ref_display` 构造带颜色的紧凑行。

## 80. Atomic 不代表文本消失

Element 只是给一段 Range 附加 Kind 与 Display；`textarea.text()` 仍含原字符串，发送与 History 可以继续使用它。

## 81. 为什么文件引用必须 Atomic

若 Cursor 能停在 Chip 中间并删掉一个字符，视觉 Display 与 Backing Path 可能分裂，Line Viewer 也无法稳定找到引用边界。

## 82. 接受操作放进一个 Undo Group

替换长 Query、建立 Element 和追加空格在一次 Ctrl-Z 中整体撤销，不让用户退到半个 Chip 状态。

## 83. Atomic Element 抑制再次打开补全

Cursor 位于 Element Range 内或边界时，`update_file_search_context` 主动 Clear；Chip 自己的 `@` 不应再次被当新 Query。

## 84. Chip Display 会拆 Path 与行号

`styled_file_ref` 给 `@`、Path、冒号和数字使用不同 Theme Style；Back Text 仍是单一字符串。

## 85. Chip Metadata 会随 Queue 保存

`PromptWidget::stash().into_submission()` 产出 Text、Images 与 `chip_elements`；队列条目保存 Range、Kind 与可选 Display。

## 86. Chip Metadata 主要用于 UI 恢复

发送给 Shell 的语义来自 Backing Text；`chip_elements` 留在 Pager 的 Queued/In-flight State，Rewind 或 Queue Edit 时恢复折叠样式。

## 87. Range 必须与未 Trim 的 Text 对齐

Chip Element Range 是 Byte Offset。若 Queue 在保存前随意 Trim 或改写 Text，恢复时会把样式附到错误字符上。

## 88. Glossary（十）：原子元素

- **Element kind**：TextArea 用于区分 Paste、File Ref、Image 等原子块的类型标签。
- **Element range**：元素在 Backing Text 中对应的 Byte Range。
- **Display override**：不改变底层文本的自定义视觉内容。
- **Undo group**：把多次编辑合成一次撤销操作。
- **Metadata**：辅助显示和恢复、但不一定发送给模型的数据。
- **Range alignment**：Element Offset 与保存文本保持完全对应。

## 89. Dropdown 键盘优先于普通编辑

可见时 Up/Down、Ctrl-P/N/K/J、PageUp/Down 与 Ctrl-U/D 先导航候选，不移动普通 Text Cursor。

## 90. Enter/Tab 只在合法 Selection 时接受

显式检查 `selected < result_count`；即使异步列表刚改变，越界 Selection 也不会触发错误 Replacement。

## 91. Modified Right 不会误接受

只有无 Modifier 的 Right 命中；Shift/Alt/Ctrl Right 继续交给 TextArea 做选择扩展或 Word Jump。

## 92. `:` 和 Ctrl-L 可以边选边开 Viewer

Dropdown 中选中 File 时，这两个键先建立 Atomic Element，再发出 Pending Viewer Request；Directory 不允许用 File Viewer 打开。

## 93. Esc 只关闭文件补全

它清 Context、Anchor 和 Results，不删除用户已经输入的 `@query` 文本，让用户能继续普通编辑或手写引用。

## 94. Mouse 有独立 Hover 与 Select

Hover Index 与键盘 Selected Index 分开保存；Click 时才把合法 Hover 转成 Selected 并接受。

## 95. Scroll Offset 保证选中项可见

Renderer 根据可见行数调用 `ensure_visible`，Selection 超出 Viewport 时更新 Offset，并在结果过多时显示 Scrollbar。

## 96. Glossary（十一）：输入路由

- **Key interception**：Overlay 先消费与自身相关的按键。
- **Pass-through key**：当前组件不处理，交还普通编辑器的按键。
- **Modifier**：Ctrl、Alt、Shift 等组合键状态。
- **Hover**：鼠标当前指向但尚未确认的候选。
- **Viewport**：Dropdown 当前能显示的列表窗口。
- **Scroll offset**：Viewport 顶部对应的结果 Index。

## 97. 已有 Chip 也能重新打开 Viewer

Cursor 在 Element 上或相邻位置时 Ctrl-L；恰在 Element Start/End 输入 `:` 时，也会建立 Viewer Request，而不是插入普通冒号。

## 98. `:` 的触发范围比 Ctrl-L 更严格

Ctrl-L 容许 Element 内、End 以及尾随空格附近；冒号只在精确 Start 或 End，减少用户正常输入 `:` 被抢走。

## 99. Viewer 以 Session CWD 解析相对路径

`AgentView::open_line_viewer` 对相对 Path 执行 `session.cwd.join(path)`，与后续 Shell Attach 的基本路径语义一致。

## 100. Viewer 读取失败会取消 Undo Group

刚从 Dropdown 创建的临时 Chip 不应在 Viewer 打不开时残留；Cancel Group 把 Draft 恢复到进入 Viewer 前。

## 101. Viewer 记录 Element ID

确认时通过稳定 ID 找到原 Element，而不是假设它仍位于旧 Byte Range；编辑期间 Range 可能移动。

## 102. Source Line 使用 1-based Line Number

显示和最终 `@file:N-M` 都符合开发者常用行号，内部 Selection Range 则使用半开区间。

## 103. Visual Mode 形成连续范围

用户用 Viewer 的 Visual Selection 选多行；Enter with Range 将范围编码进 Element，普通确认则只保留整个文件路径。

## 104. 单行显示为 `:N`

`line_range_suffix()` 对单行生成 `:4`，多行生成 `:1-3`，Chip Parser 也能把单行恢复成内部 `4..5`。

## 105. Confirm 后才结束 Undo Group

Viewer 打开期间，临时 Element 与 Range 修改属于同一个 Group；确认追加空格并 Commit，取消则整体回滚。

## 106. Glossary（十二）：行号 Viewer

- **Line viewer**：用于预览文件并选择引用行范围的 Modal。
- **Visual mode**：以 Anchor 和 Cursor 选择连续行区间的模式。
- **Element ID**：不随文本 Range 移动而变化的元素标识。
- **1-based line**：第一行编号为 1 的用户表示法。
- **Half-open range**：包含 Start、不包含 End 的内部区间。
- **Line suffix**：附在路径后的 `:N` 或 `:N-M`。
- **Modal**：暂时接管输入的覆盖界面。

## 107. Submit 时 Backing Text 进入 Queue

普通 Prompt 最终以 `QueuedPrompt.text` 保存完整 `@path`；Chip Display 不参与 Shell Parse。

## 108. `drain_prompt_state_to_last_queued` 保住 Chip

提交先 Enqueue，再从 PromptWidget Stash 中取出 Images 与 Chip Elements，放入刚创建的最后一个 Queue Entry，最后才清 Composer。

## 109. In-flight Prompt 继续保存 Chip Snapshot

开始发送时，Plain Prompt 的 Text、Images、Scrollback Entry 与 Chip Elements 被保存在 `InFlightPrompt`，支持 Cancel/Rewind Restore。

## 110. 正常 Text-only Prompt 用 `Effect::SendPrompt`

没有 Image、Skill Wire Blocks 或 Combined Segments 时，Queue 把 Text 原样交给 ACP Effect，因此 `@` Token 保持可扫描。

## 111. Image Prompt 仍有 Text Block

Builder 产生 Text + Image Content Blocks；Shell 会收集所有 Text Block 再 Join，因此其中 `@path` 仍能触发文件附加。

## 112. Skill Wire Blocks 由 Skill 自己决定是否含引用

当 `wire_blocks` 存在时它替代 Display Text 发送。用户看到的 `/skill ...` 不一定就是 Shell 扫描的文本，因此引用语义取决于扩展后的 Block。

## 113. Chip Metadata 不跨 ACP

Shell 不知道 `KIND_FILE_REF`、Element ID 或 Display Style；这也是手写 `@path` 与点击补全最终共用同一 Parser 的原因。

## 114. Glossary（十三）：提交与队列

- **Stash**：临时取出 Composer 的完整 Draft 状态。
- **Submission tuple**：提交时产出的 Text、Images 与 Chip Metadata。
- **In-flight prompt**：已开始发送、仍可能撤回恢复的 Prompt 快照。
- **Text-only prompt**：只含 Text Content 的请求。
- **Structured blocks**：Text、Image、Resource 等 ACP Content Block 集合。
- **Display/wire split**：用户看到的文本与实际发送 Payload 不同。

## 115. Shell 先归并所有 Text Block

`parse_prompt_with_skills` 遍历 ACP Blocks：Text 进入 `message_parts`，Image 单独保存，ResourceLink 和 Embedded Resource 进入各自集合。

## 116. Text Parts 用一个空格 Join

多个 Text Block 的边界被标准化为空格；这可能影响原 Block 间空白，但使后续 Query 与 `@` Scan 处理一个 String。

## 117. `collect_file_references` 再扫描每个 `@`

它从左到右寻找 `@`，取其后直到下一个 Whitespace 的 Token，返回给 `FileReference::parse`。

## 118. Shell Scanner 比 Pager Detector 更宽松

它不检查 `@` 前字符，所以 Email 中的 `@example.com` 也会尝试按 Path 读取；失败后只是没有 Attachment Context。

## 119. Shell Scanner 只认空白终止

逗号和分号不会在此截断。手写 `@foo.rs,` 会尝试读取文件名 `foo.rs,`；补全路径因自动追加空格通常避开这一问题。

## 120. 空 Token 被忽略

末尾单独 `@` 不生成 FileReference；Query 本身仍保留原文进入 `<user_query>`。

## 121. 同一文件可被扫描多次

Collector 没有 Dedup Set；重复 `@file` 会重复读取并生成多个附件片段，消耗更多 Context。

## 122. Parse 失败不让整个 Prompt 失败

某个 Token 不符合 FileReference Grammar 时跳过；文件不存在或不是 UTF-8 时 `render_file_reference` 返回 None，用户 Query 仍继续。

## 123. 每次尝试写 `at_mention` Trace Span

Span 记录 Mention Type 与 Success Boolean，便于诊断为什么某个引用没有产生内容。

## 124. Glossary（十四）：Shell 扫描

- **Content block**：ACP Prompt 中具有具体类型的内容单元。
- **Scanner**：在完整字符串中寻找候选 Token 的轻量逻辑。
- **Token grammar**：Parser 接受的路径与行号语法规则。
- **Best-effort attachment**：附件失败不阻断主 Query 的策略。
- **Deduplication**：合并重复引用，当前 Scanner 未执行。
- **Trace span**：记录一次操作上下文与结果的可观测性范围。

## 125. `FileReference` 支持相对和绝对路径

Grammar 接受可选开头 `@`、路径，以及可选的 `:start-end` 或 `:Lstart-Lend`。

## 126. 相对路径基于 Session Working Directory

Shell 先执行 `working_directory.join(file_ref.path)`，Pager 补全生成的项目相对路径因此指向同一项目文件。

## 127. 绝对路径会替代 Join 左侧

Rust `PathBuf::join` 遇到绝对 RHS 时结果是绝对路径本身，所以手写绝对引用并不局限于 CWD。

## 128. 当前 Attach 路径没有 Workspace Confinement Check

此函数直接 `tokio::fs::read` 解析后的路径；可读范围由 Shell 进程与外部 Sandbox 决定，不复用 File Tool 的 Root Containment Policy。

## 129. 这是显式用户输入边界，不是 Tool Permission 流

`@` Attachment 不经过模型 Tool Call、PreToolUse Hook 或 Permission Modal。安全评审时应把它当 Client Prompt Ingestion 路径单独检查。

## 130. 文件必须能完整解码 UTF-8

读取成功后用 `String::from_utf8`；Binary 或非法 UTF-8 返回 None，不生成普通 File Contents。

## 131. Range 是 1-based Inclusive

Start 转成 `saturating_sub(1)` 的 Slice Start，End 直接作为 Exclusive Slice Index，恰好实现用户语义中的 Inclusive End。

## 132. 越界范围被 Clamp

Start 与 End 都限制到文件行数；Start 大于 End 的异常组合可能在 Slice 时产生风险，但 UI Viewer 正常只生成有序范围。

## 133. 每一行带 `N→` 前缀

模型拿到的内容保留真实 Source Line Number，回答时可以基于附件位置引用或继续调用 Read Tool。

## 134. Glossary（十五）：路径与读取边界

- **Relative path**：依赖 Working Directory 解释的路径。
- **Absolute path**：从文件系统根开始、无需 CWD 的路径。
- **Confinement**：强制路径不能逃出指定 Root 的限制。
- **Path traversal**：通过 `..` 等写法访问父目录的路径行为。
- **UTF-8 decoding**：把文件字节解释成 UTF-8 文本。
- **Inclusive range**：Start 与 End 两端都属于用户选择。
- **Clamp to file**：把超出文件行数的范围缩到合法边界。

## 135. 单文件 Inline 上限是估算 5000 Token

`MAX_FILE_TOKENS = 5_000`。Line Number Prefix 加入后再估算，因此预算针对实际准备注入的正文。

## 136. 超限文件不会静默消失

Renderer 返回 Metadata-only `<file_contents ... skipped="true">`，写明估算量、限制，并提示模型使用 `read_file` 读取局部。

## 137. 行范围可以绕过整文件过大

先 Slice 再估算；用户在 Viewer 只选需要的数十行，通常可以在 5000 Token 内完整附加。

## 138. 每个引用单独应用上限

当前函数不是总 Prompt Context Budget；多个各 4900 Token 的文件仍可能产生很大 Context，后续还有更高层 Context Window/Compaction 规则。

## 139. 附件被包进 `<file_contents>`

整文件使用 `isFullFile="true"`，范围使用 `startLine` 与 `endLine` Attributes，Body 是编号后的内容。

## 140. `is_cursor` 参数当前不改变 File Reference Tag

注释仍描述 Cursor 模式的 `<code_selection>`，但实现将 `is_cursor` 绑定到 `_`，统一输出 `<file_contents>`；文档应以当前代码为准。

## 141. Glossary（十六）：Context 预算

- **Inline content**：直接放进当前模型请求的文件正文。
- **Token estimate**：发送前对文本 Token 数的近似计算。
- **Metadata-only stub**：只说明文件存在与跳过原因、不含正文的标签。
- **Per-file limit**：逐文件应用、不是整个请求总量的限制。
- **Context window**：模型一次请求能处理的最大上下文规模。
- **Attribute**：XML-like Tag 上的结构化键值信息。

## 142. User Query 与 Attachment Context 分开保存

`ParsedPrompt` 有 `query`、`context`、`skill_information`、`images` 与 `is_cursor`，避免后续靠字符串搜索重新寻找边界。

## 143. 非 Verbatim Query 包在 `<user_query>` 中

原始 Message 仍包含 `@path`，模型既看到用户写法，也会在后面得到对应文件正文。

## 144. Attached Files 包在 System Reminder 中

成功渲染的 Embedded Resource 与 File Reference 合并到 `<attached_files>`，作为“可能相关信息”而非用户正文。

## 145. Query 当前总是排在 Context 前

`assemble_parts_with_skills` 忽略实际 `is_cursor` 排序差异，统一生成 Query/Skill，空两行，再 Context；部分旧注释仍描述 Query-last。

## 146. Skill Information 紧跟 Query

若存在 Skill Block，它与 User Query 先组合，再追加附件 Context，让模型把调用意图与 Skill Instruction 一起理解。

## 147. Image 保持独立 Content

Shell 提取 Image 后不 Base64 塞进 Attached-files String；它们在后续模型请求中继续作为 Image Part。

## 148. ResourceLink 是另一条文件上下文路径

外部 Editor 可提供 Focused/Open File Metadata，或普通 Resource Link；它与 Pager 本地 `@` Text Scanner 并行存在，不应混为 Chip Protocol。

## 149. Glossary（十七）：Prompt 组装

- **ParsedPrompt**：把 Query、Context、Skill 与 Image 分区保存的中间结构。
- **User query envelope**：用 `<user_query>` 明确标记用户请求的包装。
- **System reminder**：向模型说明附件性质的系统上下文包装。
- **Attached files**：成功读取并格式化的文件正文集合。
- **ResourceLink**：ACP 原生的资源链接 Content Block。
- **EmbeddedResource**：直接携带文本或二进制资源内容的 ACP Block。
- **Prompt assembly**：按模型协议顺序组合各类输入的过程。

## 150. 当前存在单行范围的端到端错位

Pager Viewer 对单行生成 `@file:4`，PromptWidget 也能重开并解析；Shell `FileReference::parse` 的 Regex 却只接受 `:4-4` 一类 Start-End Pair。

## 151. 单行 `:4` 会被当成路径一部分

Regex 的 Path Group 会吞掉 `file:4`，随后 Shell 尝试读取名为 `file:4` 的文件，通常失败，结果是 Query 保留但没有附件正文。

## 152. 当前存在空格路径的端到端错位

Pager Drill Anchor 和 Atomic Element 可以显示 `@my dir/file.rs`；Shell Collector 用 `split_whitespace()`，只取得 `my`，无法读取完整路径。

## 153. UI Test 通过不等于 Attachment 成功

PromptWidget 测试锁定空格下钻、单行 Chip 和 Undo 行为；若没有 Shell Parser Integration Test，就看不到跨 Crate Grammar 不一致。

## 154. 手写标点也有 Scanner 差异

Pager 以逗号/分号终止 Token，Shell 只按空白终止；自动补全插空格可规避，手写 `@file.rs,` 则可能 Attach 失败。

## 155. Email 只在 UI 层防误触

Pager 不弹 Dropdown，但 Shell 仍会尝试读取 Email Domain 作为 Path；通常静默失败，却会产生一次无用 I/O/Trace。

## 156. 多文件总预算没有在此聚合

单文件 5000 Token 并不能保证附件总量可控；性能或 Context 问题需要同时检查引用数量与更高层请求预算。

## 157. Glossary（十八）：跨层契约错位

- **End-to-end contract**：从 UI 生成直到后端消费都一致的完整契约。
- **Grammar mismatch**：生产者能生成消费者不识别的语法。
- **Local test**：只证明单个模块内部行为的测试。
- **Integration test**：跨模块验证真实输入能到达最终结果的测试。
- **Silent degradation**：主流程继续，但某项附加能力在无明显错误下消失。
- **Parser drift**：多个 Parser 随演进逐渐支持不同语法集合。

## 158. 最值得补的 Integration Tests

1. Pager Viewer 生成单行 Range，经过 ACP Text 后 Shell 必须附加正确一行。
2. 含空格 Directory/File 的 Chip 必须能完整到达 File Reader。
3. `@file,`、`@file;` 与 Email 在 Pager/Shell 两层得到一致 Tokenization。
4. 重复引用是否应 Dedup，并验证总附件预算。
5. `../` 与绝对路径在预期 Sandbox/Confinement 下的行为。

## 159. 统一 Grammar 的一种方向

设计上可以让 Pager 发送结构化 Resource/Metadata，或抽出共享 Tokenizer/FileReference Grammar 给两端复用；关键不是选哪种，而是停止维护两套不相等的扫描规则。

## 160. 为什么不能只修 Regex

单行范围可由 Regex 扩展解决，但空格、标点、Email、Escape 和原子边界仍需 Tokenizer 设计；局部 Patch 容易继续积累歧义。

## 161. 为什么结构化 Block 更清楚

Path、Range 与 Display 可成为独立字段，不依赖 Whitespace Tokenization；代价是 Queue、History、Replay、ACP Compatibility 和普通手写 `@path` 都要定义迁移策略。

## 162. Glossary（十九）：改进方向

- **Shared tokenizer**：生产端与消费端复用同一 Token 切分实现。
- **Structured reference**：把 Path 与 Range 作为字段，而非编码在字符串中。
- **Escape syntax**：表示路径中空格、标点等特殊字符的规则。
- **Migration strategy**：新旧表示并存与逐步切换的方法。
- **Compatibility layer**：在协议升级期间转换旧格式的适配层。
- **Ambiguity**：同一文本可能有多个合法解释。

## 163. 调试“没有 Dropdown”

先检查 Cursor Boundary、`@` 前字符、Token 终止符、Atomic Element Suppression、Context 是否被 Esc 清除，以及 Daemon 是否因 Thread Slot 降级。

## 164. 调试“Dropdown 是旧文件”

检查 Session CWD 与 `retarget`、Hidden Mode 重走、`min_generation` Fence、Results Generation 以及 Poll 是否仍拿旧 Arc。

## 165. 调试“选目录后菜单消失”

检查 Drill Prefix 是否设置、含空格 Prefix 是否仍匹配 Text、Anchor 是否被 Undo/Paste 判 Stale，以及 Right/Tab 是否走了对应分支。

## 166. 调试“Chip 显示正确但模型没看到文件”

不要停在 Pager Element。打印/测试实际 Wire Text，再检查 Shell Collector Token、`FileReference::parse`、Resolved Path、UTF-8 Decode、Token Limit 与 `at_mention success` Span。

## 167. 调试“Rewind 后 Chip 展开成文本”

检查提交前是否 Drain `chip_elements`、Queue Edit 是否保持 Range Alignment、In-flight Snapshot 是否包含 Chip，以及 Restore 是否在 Set Text 后重新安装 Element。

## 168. 调试“切 Worktree 后结果混杂”

确认 Lifecycle/Fork 调用了 File Search Retarget；Root 变化后旧 Daemon 必须 Drop，不能只改一个显示用 CWD 字段。

## 169. Glossary（二十）：调试观察点

- **Wire inspection**：查看真正通过协议发送的 Payload。
- **Resolved path**：相对路径与 CWD 合并后的最终路径。
- **Thread exhaustion**：系统无法再提供搜索所需线程资源。
- **State restoration**：Queue、Cancel 或 Rewind 后恢复 Draft 结构。
- **Observability span**：帮助关联一次引用尝试与成功状态的 Trace 记录。

## 170. 最重要的五个不变量

1. 迟到的旧 Query 结果不能覆盖当前文件候选。
2. 切换 Session CWD/Worktree 后不能继续搜索旧 Root。
3. File Commit 必须保持 Backing Text 与 Atomic Element Range 对齐。
4. Submit 清 Composer 前必须把 Chip Metadata 转移到 Queue/In-flight State。
5. Pager 能生成的每一种引用语法，Shell 都应端到端解析并附加同一文件范围。

## 171. 推荐源码阅读顺序

先读 `context.rs` 的 Token 边界，再读 `state.rs` 的 Context/Generation/Replacement，进入 Workspace `fuzzy.rs` 看线程降级和 Ignore Walk；随后沿 PromptWidget Accept 到 Viewer 与 Queue，最后用 Shell `prompt_parser.rs` 和 `attach_file.rs` 验证模型真正得到什么。

## 172. 一句话记住这条链路

Pager 的 `@` 系统负责让 Path 好找、好选、难以误删，Shell 的附件系统负责让文件可读、可切片、不会单文件撑爆 Context；两端靠普通 `@path` Text 相接，因此 Grammar 一致性比 Chip 外观更重要。

## 173. 最终心智模型

`@` 文件引用不是“把文件拖进 Prompt”这么简单。它先是 Cursor-local Token，经 Ignore-aware Background Walk 与 Generation Fence 变成候选，再经 Directory Navigation 和 Atomic Text Element 保住 Draft 结构，可选地通过 Viewer编码行范围；提交后这些 UI 结构只用于恢复，真正语义重新落回文本，由 Shell 扫描、解析、读盘、编号、限额和组装。阅读或修改该功能时，必须同时验证 Pager Producer 与 Shell Consumer，尤其不能把“Chip 已正确显示”误认为“模型已正确获得文件”。

