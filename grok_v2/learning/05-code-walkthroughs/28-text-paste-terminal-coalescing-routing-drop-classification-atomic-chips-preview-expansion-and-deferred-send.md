# Walkthrough：文本粘贴如何跨过终端碎片、路由、原子 Chip，并完整进入 Prompt

> 场景：用户把一段多行代码粘贴进 Agent Prompt。理想终端会交付一个 `Event::Paste`，但另一些终端可能把它拆成字符、Tab 和 Enter 的高速按键流；Finder 或 Windows Terminal 的文件拖放又可能长得像文本。Pager 必须识别一次逻辑粘贴，避免中间的 Enter 提前提交，按当前 Surface 路由输入，区分普通文本、文件路径与图片，规范化换行，把大段内容压成可操作的 `[Pasted: ...]` Chip，同时保证发送给模型的仍是完整原文。用户还可能在异步剪贴板附件探测完成前立即按 Enter，系统不能因此漏掉附件或误发旧 Draft。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
terminal input
  -> TimedInputEvent
  -> drain immediately buffered events
  -> optional 2 ms detect + 10 ms continuation window
  -> filter terminal protocol replies
  -> coalesce fragmented key stream
  -> Event::Paste(text) + PasteProvenance
  -> AppView input-layer routing
  -> active surface / popup / pane
  -> dropped-path classifier first
       image path     -> [Image #N]
       non-image path -> canonical absolute path text
       no path match  -> ordinary text paste
  -> normalize bare CR
  -> selection replacement / repaste-to-expand
  -> short text inline OR large/multiline atomic paste element
  -> refresh @-file and slash/suggestion state
  -> preview / expand / undo / delete
  -> submit reads underlying full textarea text
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| 终端事件批处理 | `crates/codegen/xai-grok-pager/src/app/event_loop.rs` | `drain_and_process`、`detect_paste`、`coalesce_rapid_keys` |
| 输入来源标记 | `crates/codegen/xai-grok-pager/src/app/app_view.rs` | `PasteProvenance`、`handle_input_at_with_paste_provenance` |
| Agent Paste 路由 | `crates/codegen/xai-grok-pager/src/app/agent_view/paste.rs` | 文本插入、附件探测、Drop classifier |
| Agent 输入层级 | `crates/codegen/xai-grok-pager/src/app/agent_view/input.rs` | popup、pane、`Event::Paste` arms |
| Prompt 键盘入口 | `crates/codegen/xai-grok-pager/src/app/agent_view/prompt.rs` | Cmd/Ctrl+V、Interject、Enter |
| Paste Chip | `crates/codegen/xai-grok-pager/src/views/prompt_widget/mod.rs` | `handle_paste`、`KIND_PASTE`、preview、expand |
| 原子文本元素 | `crates/codegen/xai-ratatui-textarea/src/textarea.rs` | `TextElement`、`insert_element`、`inline_element` |
| 路径与图片解析 | `crates/codegen/xai-grok-pager-render/src/prompt_images.rs` | dropped paths、anchor、persist；由 Pager crate root 重新导出 |
| 异步结果归并 | `crates/codegen/xai-grok-pager/src/app/dispatch/task_result.rs` | completion、deferred resend |
| Dashboard 分支 | `crates/codegen/xai-grok-pager/src/views/dashboard/state.rs` | dispatch/peek paste routing |

## 3. 这不是一个单函数问题

文本粘贴横跨终端协议、异步事件批处理、应用输入路由、Prompt 编辑器和发送逻辑。只阅读 `PromptWidget::handle_paste` 会漏掉最危险的部分：多行 Paste 在到达 Widget 前，Enter 可能已经被当成 Submit。

## 4. 三类入口看起来相同，语义并不相同

第一类是终端 bracketed paste 产生的 `Event::Paste(text)`；第二类是 Cmd/Ctrl+V，Pager 主动读取系统剪贴板；第三类是没有 bracketed paste 支持时到达的一串普通 Key Event。

## 5. 为什么 Cmd/Ctrl+V 不能只交给 TextArea

系统剪贴板可能含图片、file URL 和文字 caption。Agent View 需要先做附件探测和路径分类，因此会在 Prompt Widget 之前截获 Paste 快捷键。

## 6. 为什么合成的 `Event::Paste` 是好边界

Widget 不需要知道终端是否可靠。事件循环把碎片统一成 Paste Event 后，后续路由和编辑逻辑只面对“一个不可被 Enter 拆开的输入单元”。

## 7. 最重要的安全性质

多行粘贴中的 Enter 只能成为 `\n`，不能触发发送；而用户正常键入一句话后按 Enter，仍必须提交，不能因为输入较快被误判为 Paste。

## 8. Glossary（一）：入口与边界

- **Bracketed paste**：终端用控制序列明确标记一段粘贴开始与结束的协议。
- **Key Event**：一次字符、Enter、Tab 或控制键的按下、重复、释放事件。
- **Synthetic Event**：应用从多个原始事件合成的新事件；这里是 `Event::Paste`。
- **Surface**：当前真正拥有输入的 UI 区域，例如 Agent Prompt、Dashboard Dispatch 或 Modal。
- **Input routing**：按优先级把同一个 Event 交给正确状态对象的过程。
- **Submit boundary**：输入从可编辑 Draft 变成一次正式发送请求的边界。

## 9. 每个输入事件先带时间戳

事件循环接收 `TimedInputEvent`，它保存 `event` 与 `arrived_at`。批处理不会把事件发生时间偷换成处理完成时间。

## 10. 为什么时间戳要保留

输入处理还参与双击、双按确认和时序判断。如果合并后使用“现在”，绘制延迟会改变用户动作的语义。

## 11. `drain_and_process` 先取出已缓冲事件

应用绘制期间 Crossterm 可能积累许多事件。`drain_immediate` 用非阻塞接收把它们一次取出，既降低重复重绘，也为识别高速 Paste 提供批次。

## 12. 先过滤 XTVERSION，再识别 Paste

终端能力查询回复也可能表现为字符片段。`XtversionFilter` 必须先移除这些协议字节，否则它们可能被错误并进用户 Paste。

## 13. 为什么过滤器在延长收集后还要再跑一次

检测 Paste 时会继续从 Channel 拉取稍晚到达的事件；这部分此前没经过仍处于 armed 状态的过滤器，所以收集结束后要再次过滤。

## 14. CSI Fragment 过滤发生在 Coalesce 之后

事件循环还维护 `CsiFragmentFilter`，用于移除泄漏的鼠标或 Focus 控制序列。Paste 修复和终端协议清理共同构成输入标准化层。

## 15. `/gboom` 是明确例外

小游戏需要 press/release 配对且不会 Paste，因此它激活时跳过快速按键合并。局部功能可以声明自己拥有更原始的输入语义。

## 16. Glossary（二）：终端输入管线

- **Crossterm**：Rust 终端输入、输出与事件库。
- **Channel**：异步任务之间传递输入事件的队列。
- **Drain**：不等待地取走当前已经排队的所有消息。
- **XTVERSION**：终端版本能力查询及其回复协议。
- **CSI**：Control Sequence Introducer，一类终端控制序列前缀。
- **Armed filter**：已经发出查询、正在等待并识别对应回复的过滤状态。
- **Coalesce**：把多个相邻碎片聚合成一个逻辑事件。

## 17. 正常字符为什么需要额外等待 2 ms

一个普通按键到达时，立即缓冲区可能只有它自己；Paste 的下一批字符可能仍在输入线程途中。`detect_paste` 最多等待 `PASTE_DETECT_TIMEOUT = 2 ms` 寻找后续证据。

## 18. 哪些批次才进入等待

`should_extend_for_paste` 要求批次中至少有一个 pasteable key，并且不能已有 `Event::Paste`。终端已经明确标记 Paste 时，不额外猜测。

## 19. 2 ms 窗口的取舍

它给高速 Paste 一个被聚合的机会，同时把普通键入增加的最坏首轮等待限制在很小范围。它不是判断“人类能否打这么快”的业务规则。

## 20. 第一个后续事件不一定是 Paste 证据

Mouse、Focus 或 Release 可以被一并收集，但只有新的 pasteable key 才让 `detect_paste` 返回 true。

## 21. 检测成功后使用 10 ms continuation

一旦已有连续 Key 证据，`collect_remaining_paste` 每轮最多等待 `PASTE_CONTINUE_TIMEOUT = 10 ms`，让跨读取边界的剩余内容到齐。

## 22. 非 Key Event 不延长尾部窗口

它们会保留在 Batch 中，却不会让收集循环无限续命。只有真正可能属于 Paste 的字符、Enter 或 Tab 才值得延长。

## 23. 收集有 5000 Event 安全上限

`PASTE_EXTEND_MAX_EVENTS` 限制一次延长 pass，防止持续事件流长期占有 Event Loop。极大 Paste 可以在后续批次继续处理，而 UI 不应永久饿死。

## 24. 这里解决的是 Event 碎片，不是内容大小

5000 是单轮事件数量 cap；Prompt Widget 的 10000-byte Chip 阈值是显示策略；Drop classifier 的 10 MB cap 又是路径扫描成本保护。三者不能混为一个限制。

## 25. Glossary（三）：时间窗口与背压

- **Detection window**：为了判断后续 Event 是否属于同一 Paste 而短暂等待的时间。
- **Continuation window**：已确认可能是 Paste 后，用于收集尾部碎片的更宽窗口。
- **Safety cap**：防止单次循环处理无限数据的硬上限。
- **Backpressure**：消费者限制生产者或输入流占用资源的机制；这里主要通过有界 pass 避免饿死。
- **Starvation**：某类事件长期占用循环，令绘制、取消或网络结果得不到处理。
- **Batch**：同一轮一起标准化和处理的一组 Event。

## 26. 什么是 pasteable key

`is_pasteable_key_event` 只接收 `Press` 类型的字符、无 modifier 的 Enter 和 Tab。

## 27. 字符允许哪些 modifier

无 modifier、仅 Shift，或 AltGr 都可以。Shift 是输入大写与符号所需；AltGr 是许多键盘布局输入字符所需。

## 28. 为什么拒绝 Ctrl、Alt 和 Super 字符

这些通常代表快捷键而不是文本。把 Ctrl+C 或 Cmd+V 自己拼进 Paste 会吞掉用户意图并污染内容。

## 29. Repeat 不是 Paste

`KeyEventKind::Repeat` 来自长按按键，不代表剪贴板数据。若纳入，会把用户按住一个字符误判成 Paste。

## 30. Release 没有文本语义

大部分 Release 在合并前删除，以免切断连续 run；Voice hold chord 的 Release 是例外，因为它负责关闭麦克风。

## 31. 为什么最少要求三个 Event

`PASTE_COALESCE_THRESHOLD = 3` 是 Fast Path 和候选 Run 的最低长度。更短序列不足以证明一次多行粘贴，也不值得改变普通键盘语义。

## 32. 真正的多行判据不是“出现 Enter”

连续 run 必须达到阈值，并且 Enter 之后还出现字符或 Tab，才设置 `multiline_paste=true`。

## 33. “type + submit”为什么不会误判

用户输入 `hello` 后按 Enter 时，Enter 是 run 的最后一个 Event，没有 `has_char_after_enter`，因此原始 Key Event 会依次路由，最后一个 Enter 正常 Submit。

## 34. “paste two lines”为什么会合并

`a`、Enter、`b` 中 Enter 后还有 `b`，满足三 Event 与尾随字符条件，最终成为 `Event::Paste("a\nb")`。

## 35. Tab 也能证明 Enter 后仍有内容

粘贴代码可能以缩进开头。Enter 后的 Tab 会设置 `has_char_after_enter`，避免把缩进代码拆开。

## 36. 原字符怎样被组装

Char 原样 push，Enter 映射为 `\n`，Tab 映射为 `\t`。合成 Event 继承 run 第一个 Event 的 `arrived_at`。

## 37. 不满足判据时完全保留原 Event

算法不会把候选字符预先消费后丢弃；它把原 Slice clone 回结果，维持正常快捷键与 Submit 行为。

## 38. Glossary（四）：误判控制

- **Pasteable key**：可以合理构成粘贴文本的 Key Press。
- **Modifier**：Ctrl、Alt、Shift、Super 等修饰键状态。
- **AltGr**：部分键盘布局用于输入第三层字符的修饰键。
- **Run**：Batch 中连续、未被其他事件打断的一段 pasteable keys。
- **False positive**：把正常键入误判为 Paste。
- **Trailing input**：Enter 之后仍属于同一 run 的字符或 Tab。
- **Fast path**：明显无需复杂处理时尽快返回的分支。

## 39. 已有 `Event::Paste` 时为什么仍可能合并

Windows Terminal 可能把大 Paste 拆成一个 Paste Fragment 加若干普通 Key Event。若 Batch 同时含两者，`merge_paste_fragments` 会恢复一个逻辑 Paste。

## 40. Paste Fragment 按遇到顺序拼接

`Event::Paste` 的字符串直接追加；pasteable Key 仍按 Char、Enter、Tab 规则追加。原始顺序决定最终文本。

## 41. 非文本 Key Fragment 会被丢弃

同一 Fragment Group 内的 Ctrl+C、Backspace、Arrow 和普通 Release 被视为终端碎片 artifact，不进入内容。

## 42. Mouse、Resize、Focus 不会被丢弃

遇到非 Key、非 Paste Event 时，算法先 flush 已合并文本，再保留该 Event，然后继续。这样 Paste 周围的 UI 事件仍保持相对顺序。

## 43. 空 Fragment 不产生空 Paste

只有 `merged_text` 非空才 emit Event，避免下游把空字符串当一次编辑。

## 44. 多个 Paste Fragment 可以合成一个

例如 `Paste("aa\n")`、`Paste("bb\n")`、Key `c` 会成为 `Paste("aa\nbb\nc")`。

## 45. Windows Drag-Drop 有第二判据

Windows 上，长度至少 `PATH_COALESCE_THRESHOLD = 8` 且组装文本以 drop-style path anchor 开头时，即便没有换行，也可合成 Paste。

## 46. 路径 Anchor 复用同一个检测器

Event Layer 调用 `prompt_images::starts_with_drop_anchor`，与后续 Drop classifier 共用“什么像路径”的定义，避免两层规则漂移。

## 47. 非 Windows 不启用单行路径猜测

其他平台通常可靠地产生 bracketed paste；额外猜测会扩大正常快速键入被误判的范围。

## 48. Glossary（五）：碎片恢复

- **Fragment**：一次逻辑 Paste 被终端拆出的局部片段。
- **Artifact**：协议或实现产生、但不属于用户内容的附带 Event。
- **Flush**：把当前积累的合并文本先输出为 Event。
- **Relative order**：不同 Event 在时间线上的前后关系。
- **Path anchor**：字符串开头表明它可能是本地路径或 `file://` URL 的形状。
- **Rule drift**：不同模块各自实现同一概念，后来修改不同步造成的分歧。

## 49. Paste Event 还会携带来源

标准化后得到 `RoutedInputEvent { event, arrived_at, paste_provenance }`。来源不是塞进文本，而是与 Event 并行传递。

## 50. `Terminal` 来源包含两种情况

真正的 bracketed paste 和 Pager 合成的 Paste 都标为 `PasteProvenance::Terminal`；它们都可以进入常规附件探测语义。

## 51. Linux 中键粘贴是 `X11Primary`

无 modifier 的 Middle Button Down 会尝试读取 X11 PRIMARY selection，成功后替换为 `Event::Paste(text)` 并标记 `X11Primary`。

## 52. 为什么 X11 PRIMARY 不能探测 Clipboard Attachment

PRIMARY 与 CLIPBOARD 是不同选择缓冲区。若中键 Paste 后再读取系统 Clipboard 的图片，可能把与用户中键文本无关的附件插入 Prompt。

## 53. Provenance 是不可变事实

来源从标准化一直随 Event 传到 `AppView::handle_input_at_with_paste_provenance`，目标 Surface 只能据此决定允许哪些副作用，不能重写其历史。

## 54. 非 Paste Event 不允许伪造来源

Debug assertion 要求只有 Paste Event 可以携带非 Terminal provenance，帮助开发期发现路由错误。

## 55. `ActionThenForward` 不能丢失来源

Welcome 等 Surface 可能先创建 Session，再把同一个 Event 转发给新 Agent。二次处理复用原 `arrived_at` 和 `paste_provenance`，避免语义在跨 Surface 路由时改变。

## 56. Glossary（六）：Provenance 与 Selection

- **Provenance**：数据从哪里来的来源事实。
- **X11 PRIMARY**：Linux/X11 中通常由鼠标选区写入、中键读取的 Selection。
- **CLIPBOARD**：通常由复制命令写入、Ctrl/Cmd+V 读取的系统剪贴板 Selection。
- **Side effect**：除插入文字之外的行为，例如读取图片剪贴板或创建 Session。
- **Forwarding**：完成一个前置 Action 后，把原输入再交给新目标处理。
- **Invariant**：必须始终成立、可用测试保护的系统性质。

## 57. AppView 先执行全局输入层级

Pending confirmation、Active View、Modal 与 Global Action 都可能先于 Agent Prompt。Paste 不总是落进聊天输入框。

## 58. Modal 拥有输入时必须消费 Paste

Rename、Picker、Settings、Question Input 等各有自己的 `Event::Paste` arm。若 Modal 可见却让 Event 穿透，文字会悄悄写进被遮住的 Prompt。

## 59. Popup 中不是每个区域都可编辑

例如 Question View 只有 Input focus 时才把 Paste 送入共享 Prompt；选项区域只消费 Event 而不修改 Draft。

## 60. Agent 的 Active Pane 决定末级目标

Prompt Pane 进入 Prompt Paste；Todo、Tasks、Catalog、Queue 交给各自的列表搜索；Scrollback Search 打开时进入搜索框。

## 61. 未消费与已消费要区分

列表 `handle_paste` 返回 bool，映射为 `InputOutcome::Changed` 或 `Unchanged`。这会影响重绘与后续全局路由。

## 62. Prompt Paste 会清理局部 UI 状态

进入主 Prompt 时会关闭 Btw focus，并清掉 Clipboard Image contextual tip，避免 Paste 后还显示过期提示。

## 63. Wrap Host Image Magic 最先检查

包装环境可能用特殊文本协议传递图片。`try_handle_wrap_host_image_paste` 在普通路由前解析，成功或明确 NoImage 时都不会让魔法字符串泄漏进 Prompt。

## 64. Popup Paste 也统一走路径分类

Plan Feedback、Permission Followup、Plan Approval 和 Question Input 共用 `route_popup_paste`：先尝试 dropped path，再 fallback 到 `PromptWidget::handle_paste`。

## 65. 为什么不能让 Popup 直接调用 `handle_paste`

否则同一个本地图片路径在主 Prompt 会变成 Image Chip，在 Popup 却变成普通文字；共享一个 Prompt Widget 的不同显示状态会出现不一致语义。

## 66. Glossary（七）：UI Ownership

- **Active View**：应用当前顶层页面，如 Welcome、Agent 或 Dashboard。
- **Pane**：一个 View 内可切换焦点的区域，如 Prompt、Scrollback、Todo。
- **Modal**：覆盖主界面并暂时拥有输入的对话框。
- **Popup**：围绕特定交互显示的临时层，例如 Permission Followup。
- **Input ownership**：当前哪个状态对象有权消费并修改输入。
- **Fallthrough**：当前层不处理 Event，让下一层继续尝试。
- **Hidden prompt pollution**：输入误写进当前不可见 Prompt 的 bug。

## 67. 路径分类必须先于 Clipboard 图片探测

Finder 复制文件时，系统 Clipboard 可能同时带文件图标 raster。若先探测图片，一个普通文本文件可能错误变成图标 Image Chip，而真实路径丢失。

## 68. SSH 下直接禁用本地 Drop classifier

Paste 中的路径来自远程终端上下文，却会在本地 Pager 文件系统解析。`terminal_context().is_ssh` 时返回 None，避免错误读取本地同名路径。

## 69. classifier 对 Payload 大小设 10 MB 上限

真实的一个或多个 `file://` URL 通常只有几 KB。大于等于 10 MB 的内容更可能是日志或代码，不值得逐行尝试路径解析。

## 70. 10 MB 上限不拒绝 Paste 本身

它只让路径分类返回 None；文本仍会进入普通 Paste 流程，并通常显示为 Size Chip。

## 71. `try_read_dropped_paths` 返回有序结果

每个 Entry 是 `DroppedPath::Image` 或 `DroppedPath::NonImage`。调用方按源 Token 顺序插入，因此图片和路径混合 Drop 不会被重新分组。

## 72. 图片路径变成真实 Image Chip

图片会读取、校验、持久化到 Session images 目录，并插入 `[Image #N]` 元素。这部分完整细节见第 23 篇。

## 73. 非图片路径变成规范路径文字

它使用解析后的 `path.display()` 并追加一个空格，让用户可以继续输入说明，也避免相邻多个路径黏在一起。

## 74. 一次混合 Drop 是一个 Undo Group

第一次实际插入前 `begin_undo_group`，循环结束 `end_undo_group`。用户按一次 Undo 就能撤销整个逻辑 Drop。

## 75. 空操作不会制造 Undo Step

只有准备插入 Entry 时才打开 Group；全部因限制失败时，不应让 Ctrl+Z 先经历一个看不见的空步骤。

## 76. Image Cap 在 Batch 中只提示一次

Prompt 最多 10 张图片。达到上限后设置 `image_cap_reached`，后续图片直接跳过，避免一组 Drop 连续弹出相同 Toast。

## 77. Slash 与 Completion 只统一刷新一次

若至少插入一项，循环末尾调用 `refresh_slash`。每张图片都刷新会形成 N+1 次重复工作。

## 78. Suggestion 只响应非图片路径

文件路径文字影响 @-file 和下一 Prompt Suggestion；Image placeholder 本身不应被当成用户自然语言变化，因此通知条件分开。

## 79. classifier 命中即 claim Event

只要解析出 Drop Entry，返回 `(InputOutcome, ClipboardPasteCompletion)`；即便受 cap 拒绝，也报告已经处理过的失败，防止 fallback 再把原路径作为文字插入。

## 80. Glossary（八）：Drop Classifier

- **Classifier**：把 Paste Payload 判为路径、图片或普通文本的逻辑。
- **Canonical path**：经过 URL 解码、绝对化或文件系统解析后的规范路径表示。
- **Raster**：像素图像数据；Finder 的文件图标也可能以 raster 出现在剪贴板。
- **Source-token order**：按原始 Payload 中各条目出现顺序处理。
- **Undo group**：多个编辑动作合并成一次撤销步骤。
- **Claim event**：声明 Event 已处理，阻止它继续 fallback。
- **Toast**：短暂显示的用户提示消息。

## 81. 普通 Bracketed Paste 进入 `insert_bracketed_prompt_text`

Drop classifier 未命中后，Agent View 将原字符串交给统一的 `insert_prompt_text`，并标记 `activate_bash=true`。

## 82. 空白 Paste 被忽略

`text.trim().is_empty()` 返回 `Unchanged/Empty`。它不会制造空 Chip，也不会无意义刷新 Slash 和 Suggestion。

## 83. 空 Prompt 中的 `! ` 有 Bash 特殊语义

Normal Mode、Prompt 为空且 Bracketed Paste 以 `! ` 开头时，输入模式切到 Bash，并只把前缀后的命令插入。

## 84. 为什么 Cmd/Ctrl+V Plain Text 不一定激活 Bash

`insert_prompt_plain_text` 传 `activate_bash=false`。显式 terminal Paste 与主动剪贴板 probe completion 保留不同入口语义，不能仅凭最终字符串假定来源。

## 85. Edited 后刷新三个关联状态

Prompt 会刷新 Slash Dropdown；Agent View 通知 Next-Prompt Suggestion 和 Plugin CTA 文本已变化。

## 86. `PromptEvent::Ignored` 映射为 Failed

底层空字符串等拒绝不会被误报为成功插入；Completion Reducer 可据此决定 Clipboard key 的最终诊断。

## 87. macOS/Windows 可能并行探测附件

Bracketed Paste 若 Payload 形状表明剪贴板可能有附件，会在同步插入文字后 enqueue probe，并记录本次文字插入结果。

## 88. Linux X11 PRIMARY 不走这条附件路径

上层 provenance gate 保证中键读取的 PRIMARY 文本不会附带读取 CLIPBOARD raster。

## 89. Glossary（九）：文本入口语义

- **Caption**：与剪贴板图片同时存在的文字描述。
- **Bash mode**：Prompt 被解释为 Shell 命令输入的编辑模式。
- **Slash refresh**：根据当前文本重算 Slash Command 候选。
- **Suggestion invalidation**：文本改变后取消或重算旧建议。
- **CTA**：Call To Action，由 Plugin 等功能根据 Draft 内容显示的操作提示。
- **Completion reducer**：把附件、文件和文字各分支结果归并成一个最终 Paste 结果的函数。

## 90. Widget 首先拒绝空字符串

`PromptWidget::handle_paste("")` 返回 `PromptEvent::Ignored`。上层即使漏做空检查，底层仍有最后防线。

## 91. Bare CR 被转换为 LF

`normalize_cr` 把不属于 CRLF 的 `\r` 改为 `\n`，兼容旧式 Mac 或异常终端输入。

## 92. CRLF 为什么原样保留

规范化函数不直接把 `\r\n` 改成 `\n`；后续 TextArea 的 canonical edit adapter 负责自己的文本规范，避免重复处理破坏 Repaste equality。

## 93. Selection Replacement 是一个 Undo Group

存在 Selection 时，先 begin group、删除选区、插入 Paste，再 end group。用户一次 Undo 可恢复被替换的原文和元素元数据。

## 94. Selection 还会触发图片同步

删除范围可能覆盖 Image Chip；结束后 `sync_images_with_textarea` 清理已不再存在的图片记录，防止 metadata 与文本元素失配。

## 95. Repaste-to-expand 先于普通插入

没有 Selection、Cursor 位于 Paste Chip 上或紧邻其右侧，且新 Paste 经相同规范化和 Tab 展开后与 Chip 原文逐 Byte 相等时，第二次 Paste 直接展开原 Chip。

## 96. 为什么比较必须是 Exact Byte Equality

尾随换行不同就代表不同内容；模糊比较可能把用户真正想追加的相似文本误认为展开手势。

## 97. Tab 也要先按 TextArea 规则展开

初次 `insert_element` 会展开 Tab，底层 Buffer 已是规范形式。Repaste 比较使用 `textarea.expand_tabs(text)`，才能让逻辑相同的内容命中。

## 98. 有 Selection 时不做 Repaste Expand

Selection 明确表达“替换所选内容”。展开附近 Chip 不能抢走更具体的编辑意图。

## 99. Glossary（十）：规范化与替换

- **CR**：Carriage Return，字符 `\r`。
- **LF**：Line Feed，字符 `\n`。
- **CRLF**：Windows 常见的两字符换行序列 `\r\n`。
- **Canonicalization**：把多个等价输入转换为内部统一形式。
- **Selection replacement**：用 Paste 内容原子地替换当前选区。
- **Repaste-to-expand**：在 Chip 附近再次粘贴完全相同内容，以展开该 Chip。
- **Byte equality**：按编码后的每个 Byte 完全相等，不做忽略空白等模糊匹配。

## 100. Chip 的行数阈值取决于 Compact Mode

Normal UI 至少 4 行才 Chip；Compact Mode 至少 2 行。较小终端更积极压缩多行内容，保护 Prompt 可视空间。

## 101. `.lines().count()` 不把尾随换行算成额外空行

`"hello\n"` 计为一行。这符合用户对“内容行”的直觉，也避免单行 Paste 因末尾换行越过阈值。

## 102. 单行大文本也会 Chip

长度大于 `PASTE_CHIP_DISPLAY_BYTES = 10_000` bytes 时，无论行数都创建 Paste Element。

## 103. 10000 Bytes 只是显示阈值

内容没有 offload 到磁盘，也没有截断。它仍完整存在 TextArea Buffer 中；Chip 只是替代其屏幕呈现。

## 104. 行数 Chip 与大小 Chip 的 Label 不同

按行触发显示 `[Pasted: N lines]`；按大小触发优先显示 Decimal `KB` 或一位小数 `MB`，避免 1 MB 单行内容显示成“一行”而掩盖体积。

## 105. 阈值比较是严格大于 10000 Bytes

恰好 10000 bytes 的单行 Paste 不因大小条件 Chip；只有 `text.len() > 10000` 命中。

## 106. 短 Paste 直接 `insert_str`

少于行数阈值且不超大小阈值时，文字立即成为普通可编辑字符，没有 Element Metadata。

## 107. 大 Paste 调用 `insert_element`

原文、`KIND_PASTE` 和 Styled Display 一起交给 TextArea；TextArea 插入原文，再登记覆盖该 Byte Range 的 `TextElement`。

## 108. 每次 Paste 最后都更新 File Search Context

Bracketed Paste 跳过逐字符 Key Handler，因此必须显式重算光标附近的 `@` Query，否则 Dropdown 会保持旧候选直到下一次按键。

## 109. Glossary（十一）：Paste Chip

- **Chip**：把一段复杂内容压缩成一枚短标签显示的交互元素。
- **Display threshold**：决定何时改用 Chip 显示的阈值，不是内容上限。
- **Offload**：把大内容从内存移到文件或外部存储；Paste Chip 没有这样做。
- **Backing text**：Chip 背后仍保存在 Text Buffer 中的完整原文。
- **Element metadata**：元素 ID、Range、Kind 和自定义 Display 等结构信息。
- **Compact mode**：为较小终端使用更紧凑布局和更低 Chip 行数阈值的 UI 模式。
- **Decimal unit**：按 1000 而非 1024 计算的 KB/MB 显示。

## 110. `TextElement` 是原子编辑单元

它包含稳定 `ElementId`、底层 Byte Range、Host-defined `ElementKind` 与可选 Display。Cursor 不能停在元素内部。

## 111. Chip 不是把原文替换成 Label

Range 指向的仍是粘贴原文；Render 时才用 Display Line 替代。调用 `prompt.text()` 看到的是完整内容，不是 `[Pasted: ...]`。

## 112. 这保证发送路径无需专门展开

普通 `Action::SendPrompt(text)` 直接读取 Prompt Text 就能得到完整 Paste。UI 压缩不侵入 ACP、Session 或模型协议。

## 113. Cursor 插入后停在元素末尾

这解释了为什么新 Chip 创建后立刻出现 Preview，也解释了 Repaste 检查要接受“位于 Chip 右侧”。

## 114. Cursor 移动会跨过整个 Element

左右移动从开始跳到结束，Backspace 或 Delete 也按原子 Range 删除，用户不会意外进入隐藏内容中间并破坏 Metadata。

## 115. 普通编辑会平移后续 Element Range

TextArea 的 Edit Plan 扩展到元素边界并由 `shift_elements` 更新 Range，保证 Buffer 改变后 Element 仍指向正确 Byte Slice。

## 116. Undo/Redo 保存元素身份

Element Mutation 进入 TextArea Undo State；撤销插入会移除原文与 Metadata，Redo 能恢复 Element 与 ID，而不是只恢复裸文字。

## 117. `inline_element` 只移除 Metadata

展开操作保持 Buffer Content 完全不变，只让这段内容恢复为普通可编辑字符，并把 Cursor 放到 Region 末尾。

## 118. 展开本身也是一次可撤销操作

用户展开后按 Undo，可以恢复 Chip 表示。这说明“显示态变化”也属于编辑器状态，而不是临时 Render Flag。

## 119. Glossary（十二）：原子元素

- **Atomic element**：导航和删除时不可从中间拆开的文本单元。
- **Byte range**：UTF-8 String 中以 Byte Offset 表示的半开区间。
- **Host-defined kind**：Textarea 不理解 Paste/Image 业务，只保存宿主定义的类型 Tag。
- **Edit plan**：实际修改 Buffer 前计算的替换范围、规范化文本和元素边界方案。
- **Range shifting**：前方文字增删后平移后续 Element Byte Range。
- **Inline element**：保留 backing text，仅去掉 Chip Metadata，使其变成普通文字。

## 120. Preview 在 Chip 上或右侧显示

`paste_element_for_preview` 使用 near-cursor 语义，因此刚插入完成时 Cursor 虽在末尾，也能看到内容 Overlay。

## 121. Enter 展开只接受严格 on-chip

`try_element_interaction` 使用 `element_at_cursor`。Cursor 在 Chip 右侧时，Enter 保持普通 Submit/Newline 语义，不能突然展开。

## 122. Preview Hint 会诚实区分位置

Cursor 在 Chip 上时提示 `enter`；只在右侧时提示 `paste again`；两者都可 double-click expand。

## 123. Double-click 为什么两处都能工作

Mouse click 先把 Cursor 放到 Chip 上，再由双击分支调用 `expand_paste_element_at_cursor`，所以严格 on-chip helper 仍足够。

## 124. Preview 读取 backing text

Overlay 将 `TextArea.text()[elem.range]` 交给通用 `render_preview_overlay`，不是从 Label 反推内容。

## 125. Paste Preview 与 Image Preview 使用不同 Kind

`KIND_PASTE` 的 Enter 行为是 inline；`KIND_IMAGE` 的 Enter 返回 `ImagePreview`，不会把 `[Image #N]` Placeholder 展成普通文字。

## 126. 删除 Chip 会删除完整内容

原子删除作用于 backing Range，不只是删除屏幕上的短 Label。Undo 可恢复整段 Paste。

## 127. Glossary（十三）：预览与交互

- **Overlay**：覆盖在主 Prompt 附近、显示 Chip 内容的临时渲染区域。
- **Near-cursor**：Cursor 在 Element 内或恰好位于其右边界。
- **Strict on-chip**：Cursor 必须位于 Element Range 内，右边界不算。
- **Affordance**：界面暗示用户可以执行某动作的线索，例如 Hint。
- **Double-click expansion**：双击 Chip 将其还原为内联文本。
- **Element interaction**：Enter、Click、Hover 针对 Chip 而非普通字符的行为。

## 128. Cmd/Ctrl+V 只读取一次文字剪贴板

Agent Prompt 的 Key Handler 主动调用 `system_clipboard_read_text`，把结果包装为 `ClipboardTextRead`，避免 macOS 旧路径重复启动两次 `pbpaste`。

## 129. 文件路径命中是同步优先分支

Clipboard Text 若能解析成 Drop Paths，立即处理并返回；不会再等待 raster probe。

## 130. 可能含附件时启动异步 Probe

`attachment_probe_gate` 返回 change count 后，Agent 增加 `paste_probe_in_flight` 并发出 `Effect::ProbeClipboardAttachment`，重读取、解码、持久化都离开 Event Loop。

## 131. Probe Context 绑定 Agent ID 与 Images Dir

异步任务完成时不会依据“当前屏幕”猜目标，而是使用启动时保存的 `ClipboardPasteTarget::AgentPrompt`。

## 132. 没有 Raster 时才补插 Deferred Text

Image 命中时由 Chip 代表附件；`NoRaster`、`ProbeDropped` 或 `ProbeFailed` 才考虑把原 Clipboard Text 作为普通文字插入，避免 caption 重复或错误优先级。

## 133. Completion Reducer 统一决定结果

Attachment、file URLs 和 deferred text 各自有 Handled、Miss、Dropped、Failed 结果；Reducer 结合 Paste Source 决定最终 Completion 与是否记录空剪贴板诊断。

## 134. `paste_probe_in_flight` 是计数器而非 Bool

用户可以连续 Paste 多次。只有所有 Probe 都完成，才允许取出等待发送的 `deferred_send`。

## 135. Glossary（十四）：异步 Clipboard Probe

- **Probe**：读取并判断剪贴板是否包含图片或 File URL 的探测任务。
- **Change count**：系统剪贴板内容变化的版本号，用于识别 stale read。
- **In flight**：异步任务已经启动但尚未归并结果。
- **Bound target**：任务启动时固定的结果归属对象。
- **Reducer**：把多个局部分支的状态归并成一个结果。
- **Full miss**：附件、文件和文字路径都没有处理任何内容。

## 136. Paste 后立即 Enter 存在真实竞态

同步文字已在 Draft 中，但图片 Probe 仍在后台。若 Enter 立即清空 Prompt 并发送，稍后到达的 Image Chip 就会丢失或落入下一条 Draft。

## 137. 普通 Send 会先检查 Probe Counter

只有会消费输入的 Send 且 `paste_probe_in_flight > 0` 时，Dispatch 把 `AgentDeferredSend::SendPrompt` 存入 Agent 并暂不产生发送 Effect。

## 138. Interject 也有独立 Deferred Kind

Turn 运行中 Send Now 本来要 Drain Images、清空 Prompt 并 Cancel Current Turn；Probe 未完成时保存 `AgentDeferredSend::Interject`，Draft 保持不动。

## 139. 为什么 Stash 只保存 Send Kind

它不缓存旧 Text Payload。Probe 完成后重新读取更新后的 Prompt，才能包含刚插入的 Image Chip 与正确对齐的 Image Metadata。

## 140. 所有 Probe 完成后才 Drain Stash

`take_deferred_send_after_paste` 在 Counter 非零时返回 None；归零时 take 一次，防止多个 Completion 重复发送。

## 141. Reissue 前还要验证目标仍 Active

Task Result 只在原 Agent 仍是 Active View 时构建 Action；用户已切走时丢弃自动重发，但保留 Draft，避免后台结果在错误 Surface 发消息。

## 142. Interject Reissue 再次检查运行态

若 Turn 已结束或 Draft 已空，`interjection_possible` 会拒绝；异步等待不能把过期的 Send Now 意图强行执行。

## 143. Interject 只在真正 Reissue 时消费 Draft

构建有效 Action 后才 Drain Images、`set_text("")`。被丢弃的 Reissue 留下 Draft，用户可自行检查和发送。

## 144. 外部编辑器会拒绝 Pending Paste

`external_prompt_editor_access` 在 Probe 或 Deferred Send 存在时返回 `PastePending`；此外任何 Text Element 或 Image 也返回 `Attachments`，避免纯文本外部编辑器丢失原子元素元数据。

## 145. Glossary（十五）：Deferred Send

- **Race condition**：执行结果取决于两个并发动作先后顺序的错误风险。
- **Deferred send**：先记录发送意图，等待 Paste Probe 完成后再重新构建发送动作。
- **Stash**：临时保存的最小意图状态；这里保存 Send Kind 而非旧 Payload。
- **Reissue**：条件满足后重新构造并派发原发送意图。
- **Stale intent**：等待期间上下文已改变、不再适合执行的旧意图。
- **Consume draft**：发送时清空输入文字并取走附件。

## 146. Dashboard 有镜像实现

Dashboard Dispatch 与 Peek Reply 也支持 Text/Image Paste、Drop Path、Probe Counter 和 Deferred Sends，但 Target 类型显式区分 New Dispatch 与某个 Peek Agent。

## 147. Dashboard Rename 不等于 Prompt Paste

Rename Field 只做受长度与字符策略限制的普通文本插入，不应该创建 Paste Chip、解析图片或触发 Prompt Suggestion。

## 148. Picker/List Paste 通常只更新 Query

历史搜索、Session Picker、Catalog 等把多行 Paste 规范化后用于过滤；它们不复用 Prompt 的 Chip Threshold，因为目标不是发送长文本。

## 149. Welcome Forwarding 保证首次 Paste 不丢

当输入动作触发创建 Session 后，`ActionThenForward` 把同一 Paste Event 再交给新 Agent，使用户在 Welcome 直接 Paste 也能落到刚创建的 Prompt。

## 150. Shell Completion 必须在 Paste 后失效

Prompt 文本发生整体变化时，旧 Completion 的 Token Range 和 Request ID 已不可靠；上层的 suggestion notification 会取消或替换旧候选。

## 151. 图片篇与本文的边界

第 23 篇重点追踪 Image Bytes、压缩、Placeholder Alignment、模型 Content Block 和持久化；本文重点追踪文本事件如何不被 Enter 拆开、如何变成 backing text 与原子 Chip，以及异步附件对 Send 时序的影响。

## 152. Glossary（十六）：Surface 差异

- **Dashboard Dispatch**：Dashboard 中用于创建或派发新 Agent 的输入框。
- **Peek Reply**：Dashboard 中针对当前 Peek Agent 的回复输入框。
- **Rename field**：只编辑显示名称的短文本字段。
- **Query field**：用于过滤列表而非提交 Agent Prompt 的搜索文字。
- **Mirror implementation**：不同 Surface 维护等价状态机，但拥有不同 Target 类型和 UI 状态。
- **Placeholder alignment**：图片占位元素与真实图片数组保持一一对应的关系。

## 153. 用一个两行 Paste 手工推演

假设终端送出 `a`、Enter、`b`：

1. 第一个 Key 进入 Batch；
2. 2 ms 内发现后续 pasteable key；
3. 10 ms 窗口收齐 Enter 和 `b`；
4. Run 长度为 3；
5. Enter 后有字符，判定 multiline Paste；
6. 合成为 `Event::Paste("a\nb")`；
7. Provenance 为 Terminal；
8. Active Agent Prompt 取得 ownership；
9. Drop classifier 不命中；
10. Widget 规范化并计两行；
11. Normal UI 下少于四行，内联插入；
12. 中间 Enter 从未经过 Submit Handler。

## 154. 用一个五行 Paste 手工推演

前半链路相同；Widget 统计五行，调用 `insert_element`。Buffer 保存五行全文，Element Range 覆盖全文，Render 显示 `[Pasted: 5 lines]`。此时直接按 Enter 会发送 Buffer 全文；把 Cursor 移到 Chip 上再 Enter 才展开。

## 155. 用一个本地文件 Drop 手工推演

`file:///tmp/example.rs` 先由 Drop classifier 解析。若是非图片文件，插入规范绝对路径和尾随空格；若是图片，持久化并插入 Image Chip。该 Event 不再 fallback 为普通 Paste Chip。

## 156. 用 Paste-then-Send 手工推演

Cmd+V 同时有 caption 与图片时先启动 Probe。用户立即 Enter，Dispatch 只 Stash `SendPrompt`。Probe 完成后 Image Chip 加入原 Prompt，Counter 归零，系统重新从最新 Draft 构建 Action；若用户已切到别的 Agent，则不自动重发并保留原 Draft。

## 157. 常见误读一：Chip 文字会发给模型

错误。`[Pasted: N lines]` 是 `TextElement.display`；发送读取 backing Buffer，因此模型收到原始多行内容。

## 158. 常见误读二：所有快速输入都会变 Paste

错误。必须形成连续 pasteable run，并满足 Enter 后仍有内容；普通 type-and-submit 保持原 Event。

## 159. 常见误读三：10 MB 是 Prompt 限制

错误。10 MB 只是 Drop classifier 的扫描短路；文本仍能进入 Widget。真实模型请求大小还会受上游 Prompt/Context 与协议限制。

## 160. 常见误读四：Preview 展开后才会发送全文

错误。展开只改变编辑显示态；Backing Text 从 Chip 创建时就已完整存在。

## 161. 常见误读五：Paste Provenance 只是 Telemetry

错误。它直接决定能否读取 Clipboard Attachment，防止 X11 PRIMARY 文本错误绑定 CLIPBOARD 图片。

## 162. 调试“多行 Paste 被提前发送”的顺序

1. 终端是否产生 Bracketed Paste；
2. Input Thread 是否把事件标成 Press/Repeat/Release；
3. `should_extend_for_paste` 是否命中；
4. 2 ms Detection 是否收到第二个 pasteable key；
5. 10 ms Continuation 是否收齐 Enter 后字符；
6. 是否被 `/gboom` 特例跳过；
7. CSI/XTVERSION Filter 是否误删；
8. `coalesce_rapid_keys` 的 run 是否被非 pasteable Event 切断；
9. 最终进入 AppView 的是否是一个 `Event::Paste`。

## 163. 调试“Paste 内容不完整”的顺序

1. 检查 Fragment Merge 的原 Event 顺序；
2. 检查 5000 Event pass cap 是否导致多批；
3. 比较 CR、CRLF、LF 和 Tab 规范化结果；
4. 检查 Selection 是否替换了既有 Range；
5. 查看 TextArea backing text，而不是屏幕 Chip Label；
6. 检查 Element Range 是否覆盖完整 UTF-8 Byte Slice；
7. 检查发送 Action 使用的 text 是否来自最新 Prompt；
8. 若含附件，检查 Probe Counter 与 Deferred Reissue。

## 164. 调试“文件 Drop 变成错误图片”的顺序

1. 是否 SSH Context；
2. Payload 是否超过 classifier cap；
3. Drop anchor 与 URL decode 是否成功；
4. 路径是否存在且能 Canonicalize；
5. 路径 classifier 是否在 attachment probe 之前运行；
6. Finder raster 是否被错误优先；
7. 图片格式判断是否正确；
8. Event 是否已 claim 后又发生 fallback。

## 165. 调试“Paste 后立即发送丢图片”的顺序

1. `paste_probe_in_flight` 是否在 enqueue 前加一；
2. Task 的 Target 是否绑定正确 Agent/Peek；
3. Send 是否属于 consume-input 路径；
4. `deferred_send` 保存的是 SendPrompt 还是 Interject；
5. 每个 Probe Completion 是否恰好减一次 Counter；
6. Counter 归零前是否提前 take；
7. Reissue 是否从最新 Prompt 重建 Payload；
8. Target 已非 Active 时是否保留 Draft 而非发送。

## 166. 值得长期守住的测试不变量

- Bracketed Paste 不再做时序猜测；
- 两行快速 Paste 合成，一个 type-and-submit 不合成；
- Enter 后 Tab 也保住多行 Paste；
- Repeat、Release、Ctrl/Alt/Super 快捷键不进入文本；
- 混合 Paste Fragment 与 Key Event 按序恢复；
- Mouse/Resize/Focus 在 Fragment 周围仍保留顺序；
- X11 PRIMARY 不触发 CLIPBOARD attachment probe；
- Welcome forwarding 保留 provenance 与 timestamp；
- Drop Path 在 raster probe 之前获胜；
- SSH 与 10 MB guard 正确 fallback 为文本；
- 混合 Drop 保持源顺序且一次 Undo；
- bare CR、CRLF、Tab 与 Unicode 边界稳定；
- 4/2 行和 10000-byte 显示阈值准确；
- Chip backing text 始终完整，Label 不进入发送 Payload；
- Atomic navigation、delete、undo、redo 和 inline 不破坏 Range；
- Repaste 只有 Exact Content 且无 Selection 时展开；
- Preview right-after 与 Enter strict-on-chip 语义不冲突；
- 多 Probe 归零后只 Reissue 一次；
- inactive target 不自动发送且 Draft 不丢失；
- Modal、Popup、Pane 不把 Paste 泄漏到隐藏 Prompt。

## 167. 一句话记忆

Grok Build 的 Paste 不是“把字符串塞进输入框”，而是一条分层的输入恢复与所有权管线：Event Loop 用短时间窗和保守判据把终端碎片恢复为带来源的单个 Paste，AppView 把它交给唯一可见 Surface，Agent 先区分 Drop Path、图片和普通文本，Prompt Widget 再把短内容内联、把长内容登记成仍保留完整 backing text 的原子 Chip；预览和展开只改变编辑体验，发送始终读取全文，而异步附件探测通过 Target Binding、Probe Counter 和 Deferred Reissue 保证 Paste 后立即 Enter 也不会漏附件或污染别的 Draft。

## 168. 复习问题

1. 为什么多行 Paste 的危险发生在 Prompt Widget 之前？
2. Detection 2 ms、Continuation 10 ms 和 5000 Event cap 分别解决什么问题？
3. 为什么“Enter 后仍有字符”能区分 Paste 与 type-and-submit？
4. Repeat、Release 与 Ctrl/Alt/Super 字符为什么不能并入 Paste？
5. `merge_paste_fragments` 怎样同时保留 Paste 内容和 Mouse/Resize 顺序？
6. `PasteProvenance::X11Primary` 为什么必须禁止 Clipboard attachment probe？
7. 为什么 Drop classifier 必须先于 Finder raster probe？
8. 10 MB、10000 bytes 和 4/2 lines 三个阈值各控制什么？
9. Paste Chip 的 Label、Element Metadata 与 Backing Text 分别存在哪里？
10. 为什么模型不需要先 Expand Chip 才能看到完整内容？
11. Repaste-to-expand 为什么要求 Exact Byte Equality 且不能有 Selection？
12. Cursor 在 Chip 右侧时，Preview 与 Enter 为什么故意采用不同匹配范围？
13. `paste_probe_in_flight` 为什么是 Counter，Deferred Send 为什么只保存 Kind？
14. Reissue 为什么必须从最新 Prompt 重建，并再次检查 Active Target 与运行态？
15. Modal、Popup、Pane 和 Dashboard 为什么不能共用一个无条件 Paste arm？
