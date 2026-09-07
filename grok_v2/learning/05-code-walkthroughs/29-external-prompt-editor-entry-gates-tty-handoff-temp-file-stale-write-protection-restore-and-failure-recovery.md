# Walkthrough：外部编辑器如何接管 Prompt，并在 TTY、并发和失败下保住 Draft

> 场景：用户在 Minimal Mode 中按 Ctrl+G，把当前 Prompt Draft 交给 Vim、Neovim 或其他 `$VISUAL/$EDITOR`。Pager 必须让出 stdin 和终端显示权，等待编辑器退出，再把合法结果写回原 Agent；但 Voice、Paste Probe、Chip、图片、Modal 或 Queue Edit 可能正拥有输入，用户也可能在请求排队后改变界面状态，编辑器可能退出失败、写出非 UTF-8 或超大文件，TUI Writer 还可能尚未完成上一帧。系统必须保证不会争抢 TTY、不会丢失较新的 Draft、不会把结果写到错误 Agent，也不会因为任何失败自动发送 Prompt。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
Ctrl+G / Command Palette / typed /edit-prompt
  -> Action::EditPromptExternal
  -> minimal mode + active Agent + ownership gates
  -> snapshot { agent_id, original_text }
  -> app.pending_editor
  -> stop processing later events in same input batch
  -> next event-loop top: run_pending_suspends
  -> revalidate ownership immediately before handoff
  -> resolve $VISUAL > $EDITOR > vi
  -> create 0600 temp Markdown file
  -> park input-reader thread (<= 500 ms)
  -> drain async terminal writer (<= 750 ms)
  -> leave alternate screen / disable raw mode
  -> spawn editor argv + temp path; block until exit
  -> enable raw mode / re-enter alternate screen
  -> discard child-exit terminal replies; resume reader
  -> success + valid UTF-8 + <= 4 MiB
  -> compare live Draft with original snapshot
  -> replace original Agent Prompt, refresh editor state
  -> delete temp file by Drop
  -> full repaint; never auto-submit
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| 入口 Action | `crates/codegen/xai-grok-pager/src/actions/defaults.rs` | `mode_ctrl_g_action` |
| Slash 入口 | `crates/codegen/xai-grok-pager/src/slash/commands/edit_prompt.rs` | `EditPromptCommand` |
| Prompt 准入 | `crates/codegen/xai-grok-pager/src/app/agent_view/input.rs` | `ExternalPromptEditorAccess` |
| Dispatch 快照 | `crates/codegen/xai-grok-pager/src/app/dispatch/external_editor.rs` | `dispatch_edit_prompt_external` |
| 请求与文件生命周期 | `crates/codegen/xai-grok-pager/src/app/external_editor.rs` | request、prepare、finish、apply |
| TTY 移交 | `crates/codegen/xai-grok-pager/src/app/event_loop.rs` | park、suspend、retry、restore |
| Typed Slash 特例 | `crates/codegen/xai-grok-pager/src/app/dispatch/prompt.rs` | `/edit-prompt` Draft 处理 |
| Palette 路由 | `crates/codegen/xai-grok-pager/src/app/modals.rs` | `PaletteCommand::EditPromptExternal` |
| Prompt 回写 | `crates/codegen/xai-grok-pager/src/views/prompt_widget/mod.rs` | `set_text`、history、slash、suggestion |

## 3. 外部编辑器是 TTY Handoff，不是普通异步任务

Vim 等交互程序必须直接拥有终端 stdin、raw mode 与屏幕。把它简单放进 Tokio Task 会让 Pager 的 Crossterm Reader 和子进程同时读按键。

## 4. 为什么 Event Loop 可以阻塞等待 Child

进入编辑器后，用户预期 Pager 暂停。`Command::status()` 同步等待正好表达“子进程期间 TUI 不继续处理业务事件”，关键是阻塞前必须安全移交终端。

## 5. Prompt Editor 不等于发送 Prompt

编辑器结果只替换 Composer Draft。无论 Agent Idle 还是 Turn Running，都不会创建 Pending Prompt、Cancel Turn 或调用 ACP Prompt。

## 6. 失败契约是保留原 Draft

解析失败、临时文件失败、Child 启动失败、非零退出、非法 UTF-8、超过 4 MiB 和 stale write 都不覆盖当前 Prompt。

## 7. 结果绑定原 Agent

请求保存 `AgentId`，完成时不会依据当前 Active View 猜目标。用户切换界面后，结果仍只可能作用于原 Agent；原 Agent 消失则安全丢弃。

## 8. Glossary（一）：核心边界

- **External editor**：Pager 之外、直接运行在终端中的 Vim、Nano 等程序。
- **TTY**：终端设备及其输入、输出和模式状态。
- **Handoff**：Pager 暂停占用，把 TTY 所有权暂时交给子进程。
- **Composer**：用户发送前编辑 Prompt Draft 的输入区域。
- **Draft**：尚未发送的 Prompt 文本。
- **Child process**：由 Pager 启动的外部编辑器进程。
- **Failure contract**：失败时系统承诺维持的状态，这里是保留当前 Draft。

## 9. Ctrl+G 的含义按 Screen Mode 切换

Minimal Mode 中 Ctrl+G 是 `EditPromptExternal`；Fullscreen 中同一快捷键是 Tasks Pane。Action Registry 在构建时保证一个 Mode 只有一个 owner。

## 10. 为什么只有 Minimal Prompt 支持外部编辑

Minimal UI 本就与主屏终端输出共存，适合暂时让出 TTY；Fullscreen 使用 Alternate Screen，并把 Ctrl+G 用于任务面板。源码直接在 Dispatch 做 Minimal Gate。

## 11. Command Palette 只在 Minimal 显示入口

Palette 构建后过滤掉 Fullscreen 的 `EditPromptExternal` Entry，避免展示一个随后必然 no-op 的动作。

## 12. Ctrl+G 可以保留非空 Draft

快捷键直接派发 Action，Dispatch Snapshot 当前 `prompt.text()`；多行或非空普通文本无需先清空。

## 13. Palette 也保留现有 Draft

选择 Palette Entry 时先关闭 Palette，再返回 `Action::EditPromptExternal`。Composer 原文未被 Slash Command 消费。

## 14. Typed `/edit-prompt` 只能编辑空 Draft

命令字符串本身占用 Composer。Prompt Dispatch 在识别到该 Action 后先清空 `/edit-prompt`，再打开 Editor，所以编辑器初始文件为空。

## 15. 为什么 Typed Route 不保留命令文本

用户想编辑 Prompt，而不是打开文件继续编辑字面量 `/edit-prompt`。Slash Description 明确建议：要保留 Draft，应使用 Palette 或 Ctrl+G。

## 16. Slash Command 必须有 Active Session

`EditPromptCommand::run` 检查 `session_id`，没有 Session 时返回错误，不尝试构造 Editor Request。

## 17. Mode Support 提供可解释 Remedy

命令在 Full TUI 中报告 Minimal-only，并说明 Ctrl+G 在 Fullscreen 属于 Tasks Pane，而不是默默改变快捷键含义。

## 18. Glossary（二）：入口语义

- **Action Registry**：按 Mode、Context 和 Key Binding 解析动作的注册表。
- **Screen Mode**：Pager 的 Minimal、Inline 或 Fullscreen 显示模式。
- **Command Palette**：搜索并执行 UI Action 的面板。
- **Typed route**：用户把 Slash Command 直接输入 Composer 并提交的入口。
- **Draft-preserving route**：不清空现有 Composer 就触发编辑器的入口。
- **Remedy**：功能不可用时给用户的替代操作说明。

## 19. Dispatch 的第一层 Gate 是 Minimal Mode

`dispatch_edit_prompt_external` 在非 Minimal Mode 立即返回空 Effects，既不创建文件，也不显示误导性子进程错误。

## 20. 同一时间只允许一个 Pending Editor

`app.pending_editor.is_some()` 时重复动作 no-op。单个 TTY 不可能同时交给两个 Editor Child。

## 21. 必须有 Active Agent

Welcome、Dashboard 等顶层页面不建立 PromptDraft Request；实现只匹配 `ActiveView::Agent(agent_id)`。

## 22. Agent 必须仍存在

Active View 中的 ID 若在 Agent Map 找不到，安全返回；不使用 unwrap 推断状态一致。

## 23. 准入结果是四态枚举

`ExternalPromptEditorAccess` 包含 `Ready`、`Attachments`、`PastePending` 与 `OwnedElsewhere`，让拒绝原因保持 Typed，而不是散落多个 Bool。

## 24. Minimal Logical Prompt 与 Active Pane 分离

Minimal 的 Composer 在逻辑上持续拥有输入，即使 Vim Startup 或旧 Pane Field 仍写着 Scrollback；调用时传 `minimal_logical_prompt=true` 修正这种表示差异。

## 25. Prompt Mode 必须是 Normal

Editing Queued 等 Mode 代表 Composer 当前承载另一份对象。外部编辑器若回写普通 Draft，会破坏 Queue Row 的编辑事务。

## 26. Active Subagent 会拒绝

Fullscreen Subagent View 拥有当前输入与 Scrollback；父 Agent Composer 不是用户可见目标。

## 27. Overlay 与 Modal 都属于 Owned Elsewhere

Active Modal、Extensions、Agents、Persona Detail、Line/Image/Video/Block Viewer、Gboom、Goal Detail 和 Btw Focus 都会阻止 Editor。

## 28. 交互式系统状态也会拒绝

Permission Queue、Question View、Plan Approval、Casual Comment、Cancel Turn、Rewind、Inline Edit 和 Jump State 都具有更具体的输入语义。

## 29. 任意 Prompt Dropdown 打开也会拒绝

Slash、File Search、History 或 Suggestion Dropdown 正在解释当前文本与键盘。交出 TTY 会留下半完成的 UI transaction。

## 30. Owned Elsewhere 为什么通常静默

用户从一个占有输入的 Surface 触发 Action 时，Agent Input Layer 可直接消费；Dispatch 也 no-op。对明显的所有权冲突不额外污染 Scrollback。

## 31. Glossary（三）：输入所有权

- **Typed enum**：用枚举 Variant 表达互斥状态与原因。
- **Logical focus**：业务上拥有输入的对象，可能与遗留 Pane Field 不完全相同。
- **Prompt mode**：Composer 当前编辑普通 Draft、Queue Row 或特殊反馈的状态。
- **Owned elsewhere**：另一个可见交互对象正在拥有输入。
- **Overlay**：覆盖主界面并拦截操作的临时层。
- **UI transaction**：需要保持连续状态的一段交互，例如审批或 Queue Edit。

## 32. Paste Chip 被归类为 Attachment

只要 TextArea 含任意 Element，External Editor 就拒绝，包括 Paste Chip 和 File Reference，并不只检查 Image Array。

## 33. 为什么纯文本文件无法表达 Element Metadata

Editor 只能修改 backing characters，无法保留 Element ID、Kind、Display、Range 和 Undo Metadata。回写会把 Chip 悄悄退化为普通文本。

## 34. Image Array 也单独检查

即使 Element Metadata 异常丢失，只要 `prompt.images` 非空仍拒绝，构成双重一致性保护。

## 35. 用户如何编辑 Paste Chip 内容

应先在 Prompt 中展开 Chip，使其变成普通文本，再启动 External Editor；这一步显式接受 Element Metadata 被移除。

## 36. Pending Paste 是独立状态

`paste_probe_in_flight > 0` 或已有 `deferred_send` 时返回 `PastePending`，优先于 Attachment Check。

## 37. 为什么 Probe 期间不能 Snapshot

异步结果可能稍后插入 Image Chip 或 Deferred Caption。Editor Snapshot 若先形成，会遗漏尚未归并的内容。

## 38. 为什么仅有 Deferred Send 也拒绝

即使 Probe Counter 已归零，等待重发的发送意图仍可能消费 Draft；Editor 与 Reissue 会竞争同一 Composer。

## 39. Voice 用 App-level Target 单独 Gate

`voice_recording_target() == Agent(agent_id)` 时报告 Voice Message。ColdStart、Recording 与 Stopping 都不能与 Editor 并存。

## 40. 为什么 Stopping 也算 Voice Active

停止后仍可能收到 trailing Final 或保留 Interim。过早 Snapshot 会漏掉最后一段转写。

## 41. 可见拒绝消息进入原 Agent Scrollback

Voice、Paste Pending 和 Attachments 都通过 `report_prompt_failure` 写 System Block，Draft 保持不变，用户也知道如何解除条件。

## 42. Glossary（四）：富文本与并发 Gate

- **Text element**：Textarea 中具有 Kind、Range 和 Display 的原子元素。
- **Element metadata**：让 Chip 保持原子导航、显示和撤销所需的结构信息。
- **Attachment**：这里宽泛指无法安全往返纯文本 Editor 的 Chip、File Ref 或 Image。
- **Paste probe**：后台判断剪贴板是否含图片或 File URL 的任务。
- **Deferred send**：等待 Paste Probe 完成后重建的发送意图。
- **Trailing final**：停止录音后仍可能到达的最后语音转写。

## 43. 准入成功后不立即创建文件

Dispatch 只写 `PendingEditorRequest::PromptDraft { agent_id, original_text }`。Request 中没有 Temp Path，也没有打开的 File Handle。

## 44. 为什么延迟物化敏感 Draft

TTY 暂时无法安全移交时，请求可能重试。若一开始就创建文件，用户 Prompt 会在 `/tmp` 中停留更久，而且超时重试更难管理清理。

## 45. `original_text` 同时承担两项职责

它既是 Temp File 的初始内容，也是完成时 Stale-write compare 的基准 Snapshot。

## 46. 请求是 Cloneable 的原因

TTY Park 或 Writer Drain 超时前会保存 `retry_request`；Child 从未启动时把同一 One-shot Request 放回 `pending_editor`。

## 47. Config Editor 复用同一请求通道

`PendingEditorRequest::ConfigFile` 保存既有 Path 和可选 Agents Modal Tab。它不需要 Prompt Snapshot 或 Temp File。

## 48. Prompt 与 Config 共享 TTY Handoff，不共享结果语义

Prompt 成功后读回、校验并写 Composer；Config Editor 只在退出后刷新 Modal 数据，Editor 非零也不把文件回滚。

## 49. Same-batch Event 必须停止

Input Batch 中某 Event arm 了 `pending_editor` 后，`drain_and_process` 立即停止处理后续已缓冲 Event，防止第二个 Event 改变 Ownership 或 Draft 后才 Handoff。

## 50. 为什么不直接在 Action Handler 启动 Child

Action 可能来自 Key、Tick、Task 或 ACP Select Arm。统一在 Event Loop 顶部消费 Pending Request，才能在一个位置完成 Reader Park、Writer Drain 和 Terminal Restore。

## 51. Glossary（五）：Pending Request

- **Pending request**：已经记录但尚未执行的单次操作。
- **Materialization**：把内存 Draft 真正写成临时文件。
- **Snapshot**：某一时刻保存的状态副本，用于准备或冲突检测。
- **One-shot**：成功消费一次后不能重复执行的请求。
- **Same-batch stop**：请求建立后停止处理同批后续输入。
- **Config editor**：直接编辑 Agents/Personas 等真实配置文件的外部编辑流程。

## 52. Event Loop 顶部优先处理 Suspend

每次 Loop 在进入新的 `select!` 前调用 `run_pending_suspends`。任何来源刚建立 Request，都无需等待下一次无关键盘或网络 Event。

## 53. Retry Deadline 是显式状态

`suspend_retry_after` 控制下一次安全移交尝试；Deadline 未到时，Loop 正常处理其他事件而不再次阻塞。

## 54. Retry Timer 自己能唤醒 Loop

Select 中的 `suspend_retry` 睡到 Deadline，触发后只清 Gate，实际 Handoff 仍由下一轮 Loop Top 统一执行。

## 55. Retry Delay 是 250 ms

`SUSPEND_RETRY_DELAY` 避免输入线程或 Writer 一时未就绪时形成立即重试的 Busy Loop。

## 56. 等待提示只报告一次

`SuspendWaitReports.editor_reported` 跨重试记住是否已提示；Request 消失后才 Reset，避免每 250 ms 刷一条相同消息。

## 57. Minimal 的等待提示用 System Block

Minimal Mode 的 Toast 未必在 Handoff 等待期间可靠可见，所以提示写入 Scrollback；其他 Mode 的 Config Editor 使用 Toast。

## 58. Prepare 会在每次真正尝试前重新校验

Request 从 Dispatch 到 Loop Top 之间，Voice、Paste、Modal 或 Queue Edit 可能刚获得 Ownership。`revalidate` 再跑同一 Access Logic。

## 59. 二次校验是 TOCTOU 防线

第一次 Check 与实际创建文件/启动 Editor 之间存在时间窗口。只在入口检查会导致“检查时安全、使用时已失效”。

## 60. Agent 已消失时 Prepare 返回 None

不显示无目标错误，也不创建 Temp File。Request 被安全消费，Presenter 仅请求普通重绘。

## 61. Glossary（六）：重试与二次校验

- **Retry deadline**：下一次允许尝试的最早时间。
- **Busy loop**：不等待地持续重试，占满 CPU 和 Event Loop。
- **Deduplication**：相同等待提示在一个 Request 生命周期只显示一次。
- **Revalidation**：真正执行前再次检查当前状态。
- **TOCTOU**：Time Of Check To Time Of Use，检查与使用之间状态变化的竞态。
- **Presenter**：合并 Draw Request 并协调异步 Frame Writer 的组件。

## 62. Editor 命令优先级是 VISUAL、EDITOR、vi

非空 `$VISUAL` 胜过 `$EDITOR`；两者都缺失或空白时回退到 `vi`。

## 63. 为什么 VISUAL 优先

Unix 约定中 VISUAL 通常表示全屏交互编辑器，EDITOR 可能是更基础的行编辑器。这里遵循常见工具链行为。

## 64. 命令使用 Shlex 解析

`shlex::split` 支持 `nvim --wait` 和带引号参数，例如 `editor --name 'prompt draft'`，比简单 `split_whitespace` 更忠实。

## 65. 不通过 Shell 执行

解析后的第一个 Token 交给 `Command::new`，其余 Token 作为 `.args()`；不会运行 `sh -c`，因此 Shell 重定向、管道和命令替换不自动生效。

## 66. 为什么不运行 Shell 更安全

环境变量仍由用户控制，但避免额外一层 Shell 解释和引号歧义，也能把 Editor Program 与 Arguments 明确分离。

## 67. Unterminated Quote 是 Prepare Error

Shlex 解析失败或 Program 为空时报告 `could not parse $VISUAL or $EDITOR`，原 Draft 不变。

## 68. Editor 必须阻塞到编辑完成

GUI Editor 命令通常需要 `--wait`。Pager 只等待启动的进程；若命令自己立即返回，文件会立刻读回，无法猜测另一个 GUI 进程何时保存。

## 69. Temp 文件名使用随机 UUID

路径形如系统临时目录中的 `grok-prompt-<uuid>.md`，降低冲突，并用 `.md` 给 Editor 语法高亮提示。

## 70. 创建使用 `create_new(true)`

如果同名路径已存在则失败，而不是截断未知文件。随机 UUID 与独占创建共同保证 Ownership。

## 71. Unix Permission 是 0600

只有 Owner 可读写，避免同机其他用户读取敏感 Prompt。Mode 在文件创建时设置，而不是创建后再 chmod 留出窗口。

## 72. 写入后显式 Flush

初始 Draft 的 Bytes 在启动 Child 前写完并 Flush，Editor 打开时看到完整内容。

## 73. Glossary（七）：命令与临时文件

- **VISUAL/EDITOR**：Unix 生态约定的外部编辑器环境变量。
- **Shlex**：按 Shell 风格引号规则把一条命令字符串拆成 Argument Vector。
- **Argv**：Program 与命令行参数组成的字符串数组。
- **Shell interpretation**：由 Shell 处理管道、重定向、变量和命令替换的过程。
- **UUID**：高概率全局唯一的随机标识。
- **Exclusive create**：仅在文件不存在时成功创建，拒绝覆盖。
- **0600**：Unix 文件权限，仅 Owner 可读写。

## 74. Prepared Request Own Temp File

`PreparedEditorRequest::PromptDraft` 同时保存 `EditorLaunch`、Agent ID、Original Text 与 `PromptEditorFile` Owner。

## 75. Path 生命周期由 RAII 管理

`PromptEditorFile::Drop` 删除文件；成功、失败、Prepare 后未 Spawn、TTY Timeout 等路径只要 Owner 被 Drop 都会清理。

## 76. NotFound 不算 Cleanup Error

用户或 Editor 自己删掉文件时，Drop 静默接受；其他删除失败写 Warning，避免覆盖更重要的编辑结果消息。

## 77. TTY Timeout 时为何先 Drop Prepared

Prepared 中的 Temp File 已物化，但 Child 尚未启动。Timeout 分支 Drop 它，再把只有 Original Text 的 Pending Request Requeue；下次尝试创建一个新文件。

## 78. 这样不会跨 Retry 保留敏感 Path

每次失败 Handoff 都立即缩短落盘时间，Request 队列仍只保存内存 Snapshot。

## 79. Config File 不由 RAII 删除

它是用户真实配置，Prepared Request 只借用 Path 作为 Launch 参数；外部编辑是持久变更，不能在 Child 退出时清理。

## 80. Glossary（八）：资源所有权

- **RAII**：资源随对象构造取得、随 `Drop` 自动释放的 Rust 模式。
- **Owner object**：对资源生命周期负唯一清理责任的对象。
- **Drop**：Rust 值离开作用域时执行的析构逻辑。
- **Requeue**：执行尚未开始时把 One-shot Request 放回等待队列。
- **Persistent file**：真实配置等预期长期存在的文件。
- **Sensitive-at-rest**：敏感数据写在磁盘上而非只存在内存中的状态。

## 81. Reader Thread 平时独占 Crossterm Read

独立 OS Thread 每 100 ms `poll()` 一次，读取成功后通过 MPSC Channel 发送 `TimedInputEvent` 给 Main Loop。

## 82. 为什么使用 Poll 而非永久 Blocking Read

Reader 需要及时观察 `input_paused` 和 Channel Close；100 ms Timeout 让 Shutdown 与 Handoff 都有有界响应时间。

## 83. `input_paused` 是 Handoff 请求

Main Thread 设为 true 后，Reader 不再调用 Crossterm，只休眠并设置 `reader_parked=true` 作为确认。

## 84. Park 前先清旧 Acknowledgement

`park_input_reader` 先把 `reader_parked=false`，再设置 Pause，避免误把上一轮的 true 当成本轮已停止读取。

## 85. Atomic Ordering 保护跨线程可见性

Release Store 与 Acquire Load 确保 Main Thread 观察到 Acknowledgement 时，也观察到 Reader 已离开 Active Read Path。

## 86. Park 最多等待 500 ms

超时则清 `input_paused` 并返回 TimedOut；Child 绝不会在 Reader 仍可能持有 Crossterm Lock 时启动。

## 87. Reader Park 后还不能立刻启动 Child

异步 Frame Writer 可能仍排队输出上一帧。它若在 Editor 启动后写入，会破坏 Editor Screen。

## 88. Writer Drain 最多等待 750 ms

`writer_sync.wait_drained` 区分 Drained、TimedOut 与 Error；只有完全 Drained 才继续 Handoff。

## 89. Park 或 Drain Timeout 都不启动 Child

Caller 重新排队 Request、设置 250 ms Retry，并显示一次等待提示。Safe Handoff 优先于“赶快打开”。

## 90. Glossary（九）：线程同步

- **Input reader thread**：专门调用 Crossterm poll/read 的 OS Thread。
- **MPSC channel**：多个发送者、一个接收者的消息队列。
- **Park**：让 Reader 停止碰 stdin，并等待其确认。
- **Acknowledgement**：Reader 明确表示已经进入暂停状态。
- **Atomic ordering**：规定跨线程读写可见顺序的内存模型约束。
- **Writer drain**：等待所有已排队终端 Frame 真正写完。
- **Bounded wait**：具有最大时长、超时可恢复的等待。

## 91. Minimal Mode 会先探测 Cursor

Startup 已证明终端支持 CPR，因此 Handoff 前记录 Cursor Position；Child 退出后再次查询，判断它是否在 Main Screen 留下输出。

## 92. Fullscreen Config Editor 离开 Alternate Screen

Prompt Editor 本身被 Minimal Gate 限制，但 Config Editor 可复用 Handoff；Fullscreen 时先执行 `LeaveAlternateScreen`。

## 93. Raw Mode 必须关闭

Pager 的 Raw Mode 会改变行缓冲、回显和控制键；Child Editor 需要按正常终端约定自行设置，因此 Handoff 前调用 `disable_raw_mode`。

## 94. Child Command 追加 Temp Path

`Command::new(argv[0]).args(argv[1..]).arg(path).status()` 保证文件路径是最后一个独立参数，不受空格或特殊字符拆分。

## 95. Child 退出后恢复 Raw Mode

无论 Exit Status 是否成功，只要 `status()` 返回，Pager 都重新 Enable Raw；Fullscreen 还重新进入 Alternate Screen。

## 96. 为什么 Terminal Restore 不依赖成功 Exit

非零退出只是编辑业务失败，不能让 TUI 留在 Cooked Mode 或错误 Screen。终端资源恢复与内容采纳必须分离。

## 97. Child-exit ANSI Query Reply 会被丢弃

某些 Editor 退出时留下 DA、DSR 或 Cursor Report。Reader 仍 Park，Main Thread 非阻塞 Poll/Read 清空它们，避免稍后被当成用户输入。

## 98. Pre-park Channel Race 也会清空

Pause 生效前 Reader 可能刚向 `input_rx` 发送少量 Event；Child 退出后 Drain Channel，防止用户在 Editor 中输入的尾部按键落回 Pager。

## 99. 最后才解除 `input_paused`

终端模式、协议残留和 Channel 都处理完后 Reader 才恢复 Ownership。

## 100. Glossary（十）：终端模式

- **Raw mode**：终端把按键立即交给应用、关闭常规行编辑与回显的模式。
- **Cooked mode**：终端执行常规行缓冲和回显的模式。
- **Alternate screen**：Fullscreen TUI 使用、退出后可恢复原主屏内容的终端缓冲区。
- **CPR**：Cursor Position Report，查询当前光标坐标的终端协议。
- **DA/DSR**：终端设备属性或状态查询及回复序列。
- **Terminal residue**：Child 退出时残留、不能交给 Pager 业务层的控制序列或按键。

## 101. Child 结果先分 Exit Status

只有 `Ok(status) && status.success()` 才读取 Prompt File；非零 Status 映射为明确的 Unsuccessful Message。

## 102. Spawn/Wait I/O Error 是另一类失败

Program 不存在、权限不足或 Wait 失败会记录 Warning，并显示通用 `External prompt editor failed`。

## 103. 读回前先检查 Metadata Length

文件长度超过 4 MiB 立即拒绝，避免无界分配和读取。

## 104. 读取本身还有第二层 4 MiB + 1 Guard

Metadata 与实际读取之间文件仍可能变化；`take(MAX + 1)` 后再次检查 Bytes Length，抵御 TOCTOU 增长。

## 105. 为什么上限是 Bytes 而不是字符

文件 I/O 和内存成本按 Bytes 发生；UTF-8 多字节字符不能用字符数准确限制读取资源。

## 106. 结果必须是合法 UTF-8

Prompt 内部使用 Rust String。`String::from_utf8` 失败时拒绝全部结果，不做 Lossy Replacement，以免静默改变代码或数据。

## 107. 空文件是合法结果

用户删空并正常退出会把 Composer 清空；这仍不是发送动作。

## 108. 尾随换行会保留

File Read 直接构造 String，没有 Trim。Editor 保存的 Markdown、缩进和末尾 Newline 原样进入 Prompt。

## 109. Glossary（十一）：结果验证

- **Exit status**：子进程退出码及是否成功的状态。
- **Non-zero exit**：按 Unix 约定表示程序未成功完成。
- **Metadata length**：文件系统记录的文件 Byte Size。
- **4 MiB**：4 × 1024 × 1024 Bytes 的读回上限。
- **Invalid UTF-8**：不能无损构造 Rust String 的 Byte Sequence。
- **Lossy decoding**：用替代字符吞掉非法 Byte；这里故意不采用。

## 110. 成功读回后仍不能直接覆盖

`apply_prompt_outcome` 先比较 Live `agent.prompt.text()` 与 Request 中的 `original_text`。

## 111. Draft 改变时采用 Newer-wins

只要不完全相等，就报告 Stale Message 并保留新 Draft，不尝试三方 Merge。

## 112. 为什么 Exact Compare 足够

External Editor 期间 Reader 已 Park，正常键盘不能改 Prompt；但 Request 排队、异步 Voice/Paste 或程序状态变化仍可能修改它。任何差异都说明 Snapshot 过期。

## 113. 为什么不自动 Merge

Prompt 可以包含代码、路径和结构化命令，通用文本 Merge 可能产生语法正确但语义错误的混合内容。拒绝覆盖更可预测。

## 114. Stale 判断先于读取错误应用

Live Draft 已变化时优先说明“较新 Draft 被保留”；无论 Editor Outcome 是新文本还是错误，都不能让旧 Request 覆盖当前状态。

## 115. Agent 消失时安全 No-op

`apply_prompt_text` 与 `report_prompt_failure` 都用 Map Lookup；Session 被关闭后不会 Panic，也不会把结果转投当前 Agent。

## 116. Error Message 绑定原 Agent Scrollback

用户即使已浏览另一个 Agent，失败记录仍进入请求所属 Agent，保持诊断与状态 Ownership 一致。

## 117. Glossary（十二）：并发写保护

- **Stale write**：基于旧 Snapshot 的结果覆盖了更新后的状态。
- **Newer-wins**：检测冲突后保留当前较新值，拒绝旧结果。
- **Exact compare**：逐 Byte/String 完全比较，不猜测语义等价。
- **Three-way merge**：用 Base、Local、Remote 三份内容自动合并冲突的方法；这里不采用。
- **Target affinity**：结果和错误始终归属于启动操作的原对象。

## 118. 合法结果先关闭 History Search

External Text 是完整替换，不是 History Picker 的一次导航。`history_search.deactivate()` 防止旧 Search State 继续控制 Composer。

## 119. `set_text` 替换整个 Prompt

它清除旧文本并建立新的普通文本状态；准入 Gate 已保证没有 Element/Image Metadata 需要保留。

## 120. Prompt History Cursor 被清理

`clear_history()` 退出 Up/Down 浏览位置，使新 Draft 成为当前独立编辑内容。

## 121. Cursor 移到结果末尾

使用 `text.len()` 的 Byte Offset；Prompt Widget/TextArea 以合法 UTF-8 Boundary 处理 Cursor。

## 122. Slash Dropdown 重新计算

新文本可能是 `/model` 或其他命令，`refresh_slash(&models)` 让 Dropdown 与完整替换后的内容同步。

## 123. Next-Prompt Suggestion 被清空

旧 Ghost Text 基于旧 Draft，回写后不再可信；`prompt_suggestion.clear()` 防止显示或接受过期候选。

## 124. Composer Input Mode 保持不变

Request 和 Apply 不重写 `prompt_input_mode`。Normal、Bash、Feedback、Remember 等 Mode 的 Draft 都可被纯文本编辑，只要没有其他 Ownership 冲突。

## 125. Turn Running 状态也保持不变

编辑器只是准备下一条 Draft。当前 Turn 不会被 Cancel，也不会自动 Queue 或 Interject。

## 126. Glossary（十三）：回写后的派生状态

- **History search**：在历史 Prompt 中筛选和回填的编辑器子状态。
- **History cursor**：Up/Down 浏览当前位于哪条历史记录的位置。
- **Slash dropdown**：根据 `/` 开头 Draft 展示的命令候选。
- **Ghost text**：尚未真正写入 Draft 的预测提示。
- **Derived state**：可从 Prompt Text 重算、不能沿用旧值的 UI 状态。
- **Input mode preservation**：替换文字但不改变其 Bash/Feedback 等解释模式。

## 127. Config Editor 完成后刷新 Modal Tab

若 Request 带 `refresh_agents_modal`，Child 退出后检查当前 Active Agent 与仍打开的 Agents Modal，再调用 `refresh_after_editor(tab)`。

## 128. Config Child Error 只记录 Warning

真实配置文件可能部分保存，Pager 无法用 Temp Snapshot回滚；流程仍刷新，让 UI 读取磁盘上的实际状态。

## 129. Prepare Error 的呈现依 Screen Mode

Minimal 用原/active Subagent Scrollback System Block；Fullscreen/Inline 用 Toast。这保证消息落到当前可见的反馈通道。

## 130. Prompt Error 永远写原 Agent Scrollback

这与 Config Error 不同，因为 Prompt Request 有明确 Agent ID，错误应成为该会话可追溯的状态事件。

## 131. Child 后必须 Full Repaint

Editor 直接写过终端，Ratatui 的旧 Buffer Diff 已不可信。`Presenter.request_presentation(..., true)` 强制清屏/完整重绘。

## 132. Minimal Main-screen 输出需要重新锚定 Viewport

若 Child 像 `cat` 一样把 Cursor 留在别处，`restore_after_child` 根据 Post Cursor 调整 Viewport，使 Composer 重新落在正确可见区域。

## 133. Alternate-screen Child 无需同样 Re-anchor

Editor 自己恢复 Cursor 时 `moved_cursor=None`；Fullscreen 回到 Pager Alternate Screen 后依赖 Full Repaint 即可。

## 134. Presenter 还要尊重 In-flight Frame

请求 Presentation 不代表盲目同时写；Presenter 与 Writer Sequence 协调，避免 Child 后的恢复帧与异步 Writer 再次乱序。

## 135. Glossary（十四）：恢复显示

- **Full repaint**：不信任旧屏幕 Diff，重新绘制完整 Viewport。
- **Ratatui diff**：比较上次 Buffer 与新 Buffer，仅写变化 Cell 的优化。
- **Viewport anchor**：Minimal TUI 在主屏 Scrollback 中占据的起始位置。
- **In-flight frame**：已提交给 Writer 但尚未确认写完的画面。
- **Writer sequence**：用递增编号确认 Frame 写入顺序与完成状态。

## 136. TTY Park Timeout 后原 Draft 仍只在内存 Request 中

Prepared Temp File 会 Drop，Pending Request 重新保存 Agent ID 与 Original Text；用户看到等待消息，但没有 Child 或半完成写回。

## 137. Writer Drain Error 与 Timeout 不完全相同

Timeout 可重试；真实 I/O Error 向上返回并结束正常 Handoff 路径，因为继续启动 Child 无法保证终端一致性。

## 138. Prepare Revalidation 拒绝不是错误重试

若 Voice 或 Modal 在 Request 后获得 Ownership，`prepare` 返回 `Ok(None)`；Request 被消费，显示拒绝原因，不会每 250 ms 反复尝试。

## 139. Env Parse Failure 也不重试

配置本身不会因等待 250 ms 自动修复。`PrepareError` 显示一次，Original Draft 留在 Composer。

## 140. Nonzero Child 不读取文件

即便 Editor 写了部分内容，只要 Exit Status 失败就拒绝采纳，避免把崩溃中间态当成用户确认保存。

## 141. Invalid UTF-8 和 Too Large 都删除 Temp File

Outcome Error 处理后 Prepared Request 离开作用域，RAII Cleanup 同样运行，不因内容非法泄漏文件。

## 142. Stale Write 也删除 Temp File

新 Draft 被保留，Editor 结果不应用，Owner 仍 Drop；安全拒绝不会遗留临时副本。

## 143. Glossary（十五）：失败分类

- **Retryable timeout**：资源短暂未就绪，保持 Request 后稍后重试。
- **Terminal I/O error**：无法保证继续安全操作的读写错误。
- **Prepare rejection**：二次校验发现业务条件已不允许启动。
- **Partial save**：Editor 异常退出前可能写入的不完整内容。
- **Cleanup symmetry**：成功与各种失败路径都由同一 Owner 执行资源清理。

## 144. 手工推演：Ctrl+G 编辑普通 Draft

1. Registry 在 Minimal 解析为 `EditPromptExternal`；
2. Access 返回 Ready；
3. Snapshot Agent ID 与 Original Text；
4. Same Batch 停止；
5. Loop Top 再校验；
6. 解析 Editor Argv；
7. 创建 0600 Temp Markdown；
8. Park Reader、Drain Writer；
9. 关闭 Raw Mode，等待 Child；
10. 恢复 Terminal；
11. 读回合法 UTF-8；
12. Live Draft 仍等于 Original；
13. 回写文本、重算派生状态；
14. Drop 删除 Temp File；
15. Full Repaint；
16. Draft 等待用户显式发送。

## 145. 手工推演：Paste Probe 后立即 Ctrl+G

Access 返回 PastePending，写入 System Block 并保持 Draft、Probe Counter 和 Deferred Send 原样。Probe 完成后只执行它原本的 Paste/Send 语义，不会因为曾拒绝 Editor 而凭空建立发送意图。

## 146. 手工推演：Request 后 Voice 开始

Dispatch 时可能 Ready，但 Loop Top 的 `revalidate` 看到 Voice Target 指向同一 Agent，返回 None、报告 Voice Message，且从未创建 Temp File。

## 147. 手工推演：Writer 未 Drain

Reader 已 Park，但 Writer 750 ms 内未空。Handoff 解除 Pause、Drop Prepared Temp File、Requeue Snapshot、提示一次并等待 250 ms。Child 从未启动，下一次从 Revalidation 和新 Temp File 重新开始。

## 148. 手工推演：Editor 打开期间 Draft 变更

Child 退出后合法读到 Edited Text，但 Live Draft 与 Original Snapshot 不同。系统报告 Stale，保留 Live Draft，拒绝自动 Merge，并删除 Temp File。

## 149. 常见误读一：`pending_editor` 已经表示 Editor 在运行

错误。它只是尚未物化的 Request；真正运行时 Request 已从 App 取走，Prepared Owner 位于同步 Handoff 栈帧中。

## 150. 常见误读二：Editor 结果会自动发送

错误。Apply 只修改 Prompt Widget；Turn、Queue 和 ACP 都不变。

## 151. 常见误读三：有 Paste Chip 也能当普通文本编辑

错误。Backing Text 虽然完整，但外部文件无法往返 Chip Metadata；必须先显式展开。

## 152. 常见误读四：只要 Child 成功就覆盖 Draft

错误。Exit Success、Size、UTF-8 和 Original Snapshot Equality 四层条件都要满足。

## 153. 常见误读五：TTY Suspend 只是关闭 Raw Mode

错误。还必须 Park Reader、Drain Writer、处理 Alternate Screen、清 Terminal Replies、Drain pre-park Channel、恢复 Reader 并 Full Repaint。

## 154. 调试“Ctrl+G 无反应”的顺序

1. 当前是 Minimal 还是 Fullscreen；
2. Registry 中 Ctrl+G 解析为 Editor 还是 Tasks；
3. Active View 是否 Agent；
4. 是否已有 `pending_editor`；
5. `external_prompt_editor_access` 返回哪一态；
6. 是否 Voice Target 指向该 Agent；
7. Action 是否被 Modal/Dropdown owner 消费；
8. Same-batch stop 后 Loop Top 是否运行 Suspend；
9. 是否进入 250 ms Retry Gate；
10. System Block 是否提示等待安全 Handoff。

## 155. 调试“Editor 没打开”的顺序

1. `$VISUAL`、`$EDITOR` 和 fallback 的实际值；
2. Shlex 是否成功；
3. Program 是否存在且可执行；
4. Revalidation 是否拒绝；
5. Temp File 是否创建成功；
6. Reader 500 ms 内是否 Park；
7. Writer 750 ms 内是否 Drain；
8. Request 是否 Requeue；
9. Spawn Error 是否写入原 Agent Scrollback。

## 156. 调试“保存后 Draft 没变化”的顺序

1. Child Exit Status 是否为 Success；
2. GUI Editor 是否需要 `--wait`；
3. Editor 保存的是否是传入 Temp Path；
4. 文件是否超过 4 MiB；
5. Bytes 是否合法 UTF-8；
6. Live Draft 是否已不同于 Original Snapshot；
7. 原 Agent 是否仍存在；
8. Scrollback 中是 Nonzero、Invalid UTF-8、Too Large 还是 Stale Message。

## 157. 调试“回来后终端错乱”的顺序

1. Reader 是否在 Child 前确认 Park；
2. Writer 是否真正 Drained；
3. Raw Mode 是否在所有 Child Exit 路径恢复；
4. Fullscreen 是否 Leave/Enter Alternate Screen；
5. Child-exit ANSI Replies 是否清空；
6. pre-park `input_rx` 是否 Drain；
7. `input_paused` 是否最后解除；
8. `restore_after_child` 是否检测 Cursor Move；
9. Presenter 是否请求 Force Full Repaint。

## 158. 值得长期守住的测试不变量

- Ctrl+G 在 Minimal 是 Editor、Fullscreen 是 Tasks；
- Palette Entry 只在 Minimal 可见；
- Ctrl+G/Palette 保留非空 Draft，typed Slash 清掉命令自身；
- 非 Agent、重复 Pending 与 Fullscreen Prompt Request no-op；
- Queue Edit、Modal、Viewer、Dropdown 与特殊输入 Surface 拒绝；
- Paste/File/Image Element 不丢 Metadata；
- Voice ColdStart/Recording/Stopping 都拒绝；
- Probe Counter 或 Deferred Send 都拒绝；
- Request 建立后停止处理 same-batch 后续 Event；
- Prepare 前二次校验 Ownership；
- VISUAL > EDITOR > vi，Shlex Quote 正确；
- Temp File 独占创建、Unix 0600、初始文本无损；
- 未 Spawn、Timeout、失败、Stale 后 Temp File 都删除；
- Reader Park 清除 stale acknowledgement；
- Handoff Timeout Requeue 且提示只出现一次；
- 非零退出、I/O Error、非法 UTF-8 与 >4 MiB 不覆盖；
- Metadata Size 与 bounded read 双重防增长；
- Live Draft 变化时 newer-wins；
- 结果与错误只归原 Agent，Agent 消失安全；
- Apply 清 History/Search/Suggestion、刷新 Slash、Cursor 到末尾；
- Apply 不发送、不取消 Turn、不改变 Input Mode；
- Child 后恢复 Raw/Screen、清输入残留并 Full Repaint。

## 159. 一句话记忆

External Prompt Editor 是一条严格的“内存 Snapshot → 安全 TTY 移交 → 短生命周期私有文件 → 校验后条件回写”管线：Minimal Mode 先用 Typed Ownership Gate 排除 Voice、Paste、Chip、附件和其他输入 Surface，只把 Agent ID 与原 Draft 排队；Event Loop 在同批输入边界停止，Loop Top 二次校验，成功 Park Reader 与 Drain Writer 后才物化 0600 Temp File 并阻塞运行 `$VISUAL/$EDITOR`；退出后恢复 Raw/Screen、清理协议残留，以 Exit、4 MiB、UTF-8 和 Stale Snapshot 四层条件决定是否覆盖原 Agent，所有路径由 RAII 删除文件、强制重绘，而且始终只编辑 Draft、绝不自动发送。

## 160. 复习问题

1. 为什么外部 Editor 不能作为普通 Tokio Task 启动？
2. Ctrl+G、Palette 和 typed `/edit-prompt` 对非空 Draft 的语义为何不同？
3. `ExternalPromptEditorAccess` 四个 Variant 分别表示什么？
4. 为什么 Paste Chip 即使有完整 backing text 仍必须拒绝外部编辑？
5. 为什么 Request 排队时不立刻创建 Temp File？
6. Same-batch stop 与 Prepare revalidation 分别防哪类竞态？
7. `$VISUAL/$EDITOR` 为什么使用 Shlex，却不通过 Shell 执行？
8. `create_new`、0600、RAII Drop 分别保护什么？
9. Reader Park 和 Writer Drain 为什么缺一不可？
10. 500 ms、750 ms 和 250 ms 三个时间值分别控制什么？
11. 为什么 Metadata Length 后还要 bounded read `MAX + 1`？
12. Stale Write 为什么采用 newer-wins 而不做自动 Merge？
13. 回写后哪些派生状态要重置，哪些 Agent 状态必须保持？
14. Child Exit 后为什么要清 Terminal Replies 和 pre-park Channel？
15. 为什么 Full Repaint 是 TTY Handoff 的正确恢复方式？
