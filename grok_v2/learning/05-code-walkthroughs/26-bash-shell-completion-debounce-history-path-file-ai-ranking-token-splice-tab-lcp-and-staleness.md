# Walkthrough：Bash Shell Completion 如何聚合 History、PATH、File 与 AI，并安全完成 Token Splice

> 场景：用户进入 Bash 输入模式，键入 `ls | gr` 后按 Tab。系统应把 `gr` 补成 `grep`，而不是把整行错误替换为 `grep`；键入 `cat My\ D` 时应补全带空格文件名并保持正确转义；存在多个候选时，第一次 Tab 应先填入最长公共前缀，无法确定时再打开下拉框。自动联想还可从命令历史或 AI 产生 ghost text，但迟到结果、光标移动、不完整目录扫描和跨版本协议都不能破坏现有 Draft。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
As-you-type（env gated）
  -> Bash text changed
  -> progressive ghost match OR generation++
  -> 50 ms debounce
  -> x.ai/suggest { tokenOnly: false }
  -> History + PATH + File providers in parallel
  -> optional AI provider
  -> stable priority aggregation
  -> ghost + completion rows
  -> generation guard
  -> ghost rendering / dropdown on demand

Tab（always on in Bash mode）
  -> current items usable?
     -> yes: InstaAccept / LCP Fill / Open dropdown
     -> no: deterministic fetch
  -> x.ai/suggest { tokenOnly: true, includeAi: false }
  -> PATH + File providers only
  -> token span + safely quoted token + compat whole line
  -> response lands and re-runs Tab policy once
  -> token-in-place splice
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| Shell 扩展入口 | `xai-grok-shell/src/extensions/suggest/mod.rs` | `handle_suggest`、`aggregate` |
| History Provider | `.../suggest/history_provider.rs` | 本地、Shell、跨 CWD 历史 |
| PATH Provider | `.../suggest/path_provider.rs` | executable scan、prefix lookup |
| File Provider | `.../suggest/file_provider.rs` | path split、fuzzy rank、expansion |
| Shell tokenizer | `.../suggest/shell_token.rs` | `parse_current_token`、quoting |
| AI Provider | `.../suggest/ai_provider.rs` | timeout、whole-line suggestion |
| AI 采样 | `xai-grok-shell/src/session/acp_session_impl/recap.rs` | `handle_ai_suggest` |
| Pager Controller | `xai-grok-pager/src/views/suggestion_controller/mod.rs` | generation、Tab、splice |
| Tab 执行 | `xai-grok-pager/src/app/agent_view/shell_completion.rs` | fetch、fill、accept |
| Debounce 路由 | `xai-grok-pager/src/app/dispatch/prompt.rs` | `handle_suggestion_debounce_expired` |
| ACP Effect | `xai-grok-pager/src/app/effects/mod.rs` | `FetchShellSuggestions` |

## 3. 先与 Next-Prompt Suggestion 分开

上一篇预测用户下一句自然语言；本文补全 Bash command line。两者虽都画 ghost text，但 Shell Completion 需要理解 token、引号、重定向、PATH、文件名和局部替换。

## 4. Shell Completion 有两个入口

自动入口随输入变化触发，可生成 History/AI ghost；显式 Tab 入口始终可用，追求类似终端的确定性 token completion。

## 5. 为什么两个入口不能完全共用策略

History 与 AI 返回整行候选，PATH 与 File 返回局部 token。若把它们混在 Tab 的单候选判断里，“只有一个文件候选”可能被一条历史记录伪装成歧义。

## 6. 自动联想默认关闭

`SuggestionController::new` 读取 `GROK_SUGGESTIONS`，未设置时为 false。它控制 debounce fetch 和 ghost surface。

## 7. Tab 补全不受该开关控制

在 Bash mode 按 Tab 会直接走 deterministic fetch。用户关闭自动联想，只是不要系统边打字边请求，并不意味着失去显式补全能力。

## 8. AI 又有独立开关

`GROK_SUGGESTIONS_AI` 决定自动请求是否允许 AI Provider，`GROK_SUGGESTIONS_AI_MODEL` 可指定模型。Tab 路径始终 `includeAi: false`。

## 9. Glossary（一）：两个 Surface

- **As-you-type**：每次输入变化后自动产生候选的交互面。
- **Deterministic Tab**：用户显式按 Tab 后，仅用确定性本地信息补全 token。
- **Ghost text**：光标后的弱化候选后缀。
- **Completion dropdown**：包含多个可导航候选的下拉列表。
- **Whole-line completion**：接受后替换整条命令的候选。
- **Token completion**：只替换光标所在命令 token 的候选。
- **Provider**：生成某一来源候选的组件，如 History、PATH、File 或 AI。

## 10. 自动管线只在 Bash Mode 工作

`notify_suggestion_text_changed` 发现当前不是 Bash mode 时会清除 shell ghost，并且不启动 debounce。自然语言 Prompt 不应混入命令历史候选。

## 11. Slash UI 会压制 Shell Suggestion

Slash menu active 或拥有 inline ghost 时，Controller 不只是隐藏候选，而是 invalidates 整个 Draft generation，防止在途响应稍后从菜单背后重新冒出。

## 12. 空文本同样使 Draft 失效

没有 prefix 时 History 和 AI 不具备有效输入；清空还意味着之前所有 token range 都失去定位依据。

## 13. 输入变化先尝试 Progressive Match

如果屏幕已有 ghost，且新文本只比上次请求锚点多一个字符，并且该字符等于 ghost 首字符，就直接消费 ghost 的该字符。

## 14. 为什么 Progressive Match 不发网络请求

用户正在逐字接受已知候选。继续请求只会浪费延迟和带宽，还可能让新响应闪烁覆盖当前 ghost。

## 15. 必须恰好新增一个 Unicode 字符

粘贴多个字符、删除、光标中插入或前缀变化都会判定不匹配。实现按 `char` 比较，并按 `len_utf8()` 安全 drain。

## 16. 不匹配时清 Ghost 与 Dropdown

旧候选是为旧 Draft 计算的。即使没有 ghost，纯 PATH/File dropdown 也必须关闭，不能保留过时 range。

## 17. 然后递增 Generation

每次需要新请求的编辑都会使旧 timer 和旧 ACP response 失效。Generation 是整个异步管线的逻辑版本。

## 18. Debounce 为 50ms

Effect 启动短 timer。快速连续键入时会产生多个 timer，但只有最后一个 generation 到期后仍匹配，才真正发 ACP 请求。

## 19. Timer 按 Arming Agent 路由

Debounce 携带 agent id，而不是到期时读取 active view。用户在 50ms 内切换页面，不会把 A 的 Draft 请求错误发给 B。

## 20. Timer 到期还要检查 Bash Mode

用户可能已切回 Normal mode。过期 timer 在 dispatch 层直接 no-op，不让 Shell suggestion 跨输入模式复活。

## 21. 自动请求携带的字段

它发送完整 text、cursor byte offset、cwd、limit=50、generation、AI 开关与模型、session id，以及 `tokenOnly: false`。

## 22. 为什么同时发送 Text 与 Cursor

补全目标是光标所在 token，不一定是整行末尾。Text 用于产生安全的完整兼容行，cursor 决定 tokenizer 只分析此前 prefix。

## 23. Cursor 首先被规范化

Shell 将 cursor clamp 到 `text.len()`，若落在 UTF-8 字符中间则向前退到 char boundary，避免任意 wire 数据导致字符串切片 panic。

## 24. Glossary（二）：Debounce 与时序

- **Debounce**：等待短暂静默后才执行，合并快速连续输入。
- **Generation**：与某一版 Draft 对应的递增版本号。
- **Arming agent**：创建 timer 或请求的原始 Agent。
- **Stale response**：generation 已落后的迟到响应。
- **Cursor byte offset**：光标在 UTF-8 字符串中的字节位置。
- **Char boundary**：可安全切开 UTF-8 字符串的位置。
- **Draft invalidation**：清理候选并让所有基于旧文本的异步工作失效。

## 25. Shell 同时运行三个确定性 Provider

`tokio::join!` 并行请求 History、PATH 和 File，减少串行等待。`tokenOnly` 为 true 时 History 返回空，但 PATH/File 仍并行。

## 26. AI 在确定性 Provider 之后决定

系统先查看 History 质量，再判断是否值得发 AI 请求。这样明显历史匹配能省掉一次模型调用。

## 27. History Provider 的三个来源

优先依次是当前 CWD 的 grok Bash prompts、用户真实 Shell history、其他 CWD Session 的 Bash prompts。

## 28. History 只做 Prefix Match

候选必须 `starts_with(prefix)`，并通过 HashSet 去重。它不是 fuzzy search，因为 ghost 必须能自然延续当前整行。

## 29. Local History 为什么优先

同一项目中重复执行的命令最可能相关；系统 Shell history 次之，其他项目的命令只作为弱回退。

## 30. History 最多返回十条

Priority 从约 10 随结果位置下降到 0。第一条被标记为 ghost candidate，其余主要进入 dropdown。

## 31. Exact History Match 获得 +30

若历史命令与当前 prefix 完全相同，会得到高优先级。它能压过其他来源，但 suffix 为空时 Pager 不会画无内容 ghost。

## 32. History 候选是 Whole-line

它描述完整历史命令，接受时应替换整行，而不是尝试猜某个 token。Shell 会标记整行 range，wire parser 再按兼容规则处理。

## 33. Cross-CWD Cache 的边界

它读取最近约 20 个 Session 目录，累计最多 200 条 prompt，TTL 60 秒；并用 AtomicBool 防止多个请求同时重扫。

## 34. Shell History Cache 更长

真实历史文件通常不会频繁变化，TTL 为 300 秒。解析依据当前 shell，并尊重 `HISTFILE`。

## 35. Bash、Zsh、Fish 格式分别处理

Bash 通常一行一命令；Zsh 可带 `: timestamp:duration;command`；Fish 使用 YAML-like `- cmd:`。读取器跳过空项并限制条数。

## 36. Cache 使用 ArcSwap 的意义

读者可无锁取得当前 immutable snapshot；一个刷新者在后台构造新 Vec 后原子替换，其他并发请求可继续使用旧 snapshot。

## 37. Glossary（三）：History 与 Cache

- **Local history**：当前工作目录下由 grok 记录的 Bash Prompt 历史。
- **Shell history**：Bash、Zsh 或 Fish 自身保存的命令历史文件。
- **Cross-CWD**：来自其他工作目录 Session 的历史。
- **TTL**：缓存内容在多少时间内被视为新鲜。
- **ArcSwap**：原子替换共享 `Arc` snapshot、降低读锁开销的结构。
- **Prefix match**：候选以当前输入开头的精确匹配。
- **Priority**：跨 Provider 聚合时决定排序位置的整数分值。

## 38. PATH Provider 只负责命令位置

Tokenizer 要求当前 token 是当前 pipeline segment 的第一个 word，且不是 redirect target、不是空值、不是 flag-looking token。

## 39. Segment 遇到什么会重置

未被引号包裹的 `|`、`;`、`&` 会结束前一个 segment，后续第一个 token 再次属于命令位置。例如 `ls | gr` 中 `gr` 可查 PATH。

## 40. 引号中的分隔符不是语法

`echo "a | gr` 中 `|` 属于 quoted data，不能把 `gr` 当新命令。统一 tokenizer 保存 quote state 来避免误判。

## 41. PATH Scan 只保留可执行文件

遍历 `PATH` 各目录，跳过非文件；Unix 还检查 `0o111` executable bits。结果排序并去重。

## 42. PATH Cache 同时绑定 PATH 值

TTL 为 60 秒，但环境变量 `PATH` 改变时即使 TTL 未到也需要刷新，否则新工具或新目录不会出现。

## 43. Executable Prefix 用 Binary Search

缓存 Vec 已排序，`partition_point` 定位第一个不小于 prefix 的位置，再连续读取 `starts_with` 项，避免每次全表扫描。

## 44. PATH Provider 最多十项

若第十一项仍匹配，所有已返回 row 标记 `truncated=true`，告诉 Pager 当前集合并不穷尽。

## 45. 可执行文件名也必须重新 Quote

名为 `zz;echo PWNED` 的 executable 必须作为一个 token 插入，不能让分号变成第二条命令。PATH 与 File 共享 `build_insert_token`。

## 46. Windows 为什么不提供确定性 Provider

当前 tokenizer 与 quoting 是 POSIX shell 语义；CMD/PowerShell 会不同。与其生成危险转义，Windows 上 PATH/File Provider 返回空。

## 47. File Provider 覆盖哪些位置

任意命令的非首 token、redirect target，以及本身含 `/` 或等于 `~` 的首 token都可做文件补全。

## 48. 为什么首个普通单词不给 File Provider

`gr` 更可能是 executable，应由 PATH Provider 处理；`./script`、`/usr/bi`、`~/pro` 明确像路径，才由 File Provider 接管。

## 49. Flag-looking Token 被排除

`-x`、`--output` 不做文件或 PATH 补全，避免把选项误判为文件名。真正的 flag-aware completion 不在当前实现范围。

## 50. Known File Commands 只是 Boost

`cat`、`cd`、`vim`、`rm` 等文件常用命令使 file candidates priority +2，但不是 gate；未知命令的参数仍可文件补全。

## 51. File Boost 的排序位置

分值 2 高于 PATH 的 0 和 History 尾部弱匹配，却低于 History 中上段候选。它在相关性与用户习惯之间折中。

## 52. 文件匹配有三个 Tier

先精确大小写 prefix，再 case-insensitive prefix，最后使用 Nucleo fuzzy subsequence；同 tier 内结合 score、目录优先和名字排序。

## 53. 为什么 Directory 优先

目录接受后通常还要继续钻取，且同名前缀下目录导航更常见。Candidate display 会加 `/` 并标注 `directory`。

## 54. Directory Scan 最多 1000 项

病态大目录不能无限阻塞补全。超过 scan cap 或结果超过 50 时标记 truncated。

## 55. Symlink Stat 也有预算

每次 scan 最多对约 64 个 symlink 做分类 stat；之后保守当作 file。代价是部分 symlinked directory 可能没有 trailing slash，而非让网络文件系统拖垮请求。

## 56. `~` 与 `$VAR` 只用于 Listing

Provider 会展开它们来决定读取哪个目录，但插入结果保留用户原始 `~`、变量、引号和 escape spelling。

## 57. 为什么不能把 Expanded Absolute Path 插回去

用户输入 `$PROJECT/src` 往往希望保留可移植表达式。展开只是一种查找手段，不应改写用户命令风格或泄露冗长路径。

## 58. Bare `~` 的特殊处理

未引用、未转义的 `~` 列出 home 并构造 `~/...`；若 home 无法解析，则按字面 `cwd/~` 查找，而不是错误列出 cwd。

## 59. Glossary（四）：PATH 与 File

- **PATH**：Shell 查找 executable 的目录列表环境变量。
- **Executable bit**：Unix 文件权限中表示可执行的位。
- **Redirect target**：位于 `<` 或 `>` 后、作为输入/输出文件的 token。
- **Fuzzy match**：字符可非连续出现的近似匹配。
- **Tier**：先按匹配类型划分的排序层级。
- **Truncated set**：因扫描或结果上限而不完整的候选集合。
- **Expansion for listing**：只为找到目录而展开 `~`/变量，插入文本仍保留原 spelling。

## 60. Minimal Tokenizer 理解什么

它处理单/双引号、反斜杠、空白、`| ; &`、`< >`，并追踪当前 segment 中此前 token 数、command word 和 redirect 状态。

## 61. 它明确不实现完整 Shell Grammar

Subshell、`$(...)`、heredoc、brace/glob expansion、`~user` 等不在范围。Completion 必须把它视为有限 parser，而非 Bash AST。

## 62. `CurrentToken.value` 是去 Quote 后的值

Provider 用它匹配真实文件名或 executable；`start`、`dir_raw_end` 和 quote metadata 则保留重建安全插入文本所需的信息。

## 63. `plain_mask` 保存字符来源

只有未引用且未 escape 的位置才允许 Shell 展开 `$` 或 `~`。`'$HOME'`、`\$HOME` 与普通 `$HOME` 语义不同。

## 64. Token Range 包含 Opening Quote

局部替换必须能够重建完整 token，包括尚未闭合的引号，否则只替换内部 value 会留下不平衡 syntax。

## 65. Directory 与 File 对 Open Quote 的处理不同

完成 file 时关闭引号，得到可执行 token；完成 directory 时保留 quote open 并追加 `/`，方便下一次 Tab 继续进入子目录。

## 66. Unquoted Name 会转义 Shell Metacharacters

空格、引号、反斜杠、`$`、反引号、管道、分号、glob 字符等前面加 backslash。控制字符无法可靠反斜杠处理时改用单引号。

## 67. Single Quote 内的 Quote 如何处理

单引号本身无法直接出现在单引号字符串内，Builder 使用关闭、escape、重新打开的经典 ` '\'' ` 结构。

## 68. 以 `-` 开头的 Bare File 更严格

若 raw directory 为空，候选名以 `-` 开头，会插为 `./-name`。仅加引号仍会被 `rm` 等程序当作 option，显式相对路径才安全。

## 69. 这是安全策略而非纯 Bash 模仿

原生 Bash 可能给出 `-rf`，但在单候选 insta-accept 中用户甚至看不到 dropdown。grok-build 选择更保守的 explicit path。

## 70. Glossary（五）：Tokenizer 与 Quoting

- **Tokenizer**：把命令前缀分解为当前 token 和少量语法状态的解析器。
- **Quote state**：光标处位于无引号、单引号或双引号中的状态。
- **Escape**：用反斜杠等方式让特殊字符作为字面值进入一个 token。
- **Raw spelling**：用户在 TextArea 中实际输入、尚未去引号或展开的文本。
- **Unquoted value**：用于匹配的去引号、去 escape 逻辑值。
- **Shell metacharacter**：可能改变命令结构或触发展开的特殊字符。
- **Explicit path**：带 `./` 或其他目录部分、不会被当成 option 的文件参数。

## 71. AI Provider 何时会被调用

只在自动路径 `include_ai=true`、`token_only=false`，且 History 不足以直接回答时调用。空 prefix 或找不到 resident Session 时返回空。

## 72. 强 History 如何跳过 AI

首条 History priority ≥30，通常代表 exact match；或非空 prefix 已有至少三条 History match 时，`should_skip_ai` 返回 true。

## 73. 为什么先看 History 再花模型成本

用户实际执行过的命令比生成式猜测更可靠、更快，也没有额外采样成本。

## 74. AI Provider 最多等待两秒

外层 `ai_provider::suggest` 对 Session command oneshot 设置 2s timeout。超时只是不加入 AI row，不影响本地 Provider 结果。

## 75. Session 内的 AI 请求长什么样

System 要求只输出完整 shell command，User item 只包含 CWD 和 partial command；`tools=[]`，默认模型 `grok-build`，temperature 0.1，max output 50。

## 76. AI 不读取正式 Conversation

虽然请求通过 SessionActor 获得 sampling client，它没有把聊天历史放进 items。它预测的是 command prefix completion，不是依据主对话继续工作。

## 77. AI Streaming 有五秒 Idle Timeout

Session handler按 ChatCompletions、Responses 或 Messages backend 分支收集流。外层两秒 timeout 通常更先约束用户可见结果。

## 78. AI 返回 Prefix 还是 Suffix 都能处理

若模型返回完整命令直接使用；若返回 continuation，则与 prefix 直接拼接。模型应自己提供需要的空格，代码不会擅自加 separator。

## 79. AI Priority 是 -10

它排在 History、File 和 PATH 后，只作为弱回退。AI row 可成为 ghost candidate，但不会压过更可靠的 History ghost。

## 80. AI 候选是 Whole-line

它没有 token-level 结构证明，因此接受时替换整行；Tab 的 token-only path根本不请求它。

## 81. 四类结果如何聚合

History、PATH、File、AI 按该顺序 chain，之后根据 priority 降序做稳定排序，再选第一个 `is_ghost_candidate` 和前 limit 个 completion rows。

## 82. Stable Sort 是承载语义的

File Provider 内部已经按 tier、score、dirs-first、name 排好，同一 response 的 file rows priority 相同。稳定排序才能保留 Provider 内部顺序。

## 83. 为什么不能换成 `sort_unstable`

相等 priority 的顺序可能随机变化，使最好 fuzzy match 或目录优先规则在 wire 上丢失，即使所有分值看起来没变。

## 84. Ghost 只从标记候选中选

History 第一项和 AI row 可标记 ghost；PATH/File 默认只进入 dropdown/Tab，不自动把随机文件名画在用户后面。

## 85. Ghost Suffix 如何计算

Aggregator 尝试从整行 `insert_text` 去掉当前 cursor prefix；若不是 prefix，则退回完整 insert text。Pager 会拒绝空 suffix。

## 86. Glossary（六）：AI 与聚合

- **AI Provider**：通过一次小型模型调用产生整行命令候选的低优先级来源。
- **Resident Session**：仍驻留、可接收 `SessionCommand` 的 SessionActor。
- **Stable sort**：相等排序键的元素保持输入相对顺序。
- **Ghost candidate**：允许进入自动灰色后缀的候选；不是所有 completion 都有资格。
- **Fallback**：更可靠来源不足时才采用的较弱路径。
- **Whole-line evidence**：只能证明整行候选，不具备安全局部 token range 的结果。

## 87. Wire 为什么同时有 `insertText` 与 Token Pair

跨版本 Pager 可能不知道 range。协议保证 `insertText` 永远是安全完整命令；新 Pager 额外读取 `replaceRange + tokenText` 做原地 token splice。

## 88. Shell 如何构造兼容 Whole Line

PATH/File Provider 先生成 token replacement，再由 `splice_token_into_line` 把它嵌回 request text，保存为 `insert_text`，同时把原 token 移到 `token_text`。

## 89. Atomic Pair 为什么重要

Range 没 token 时若拿 whole-line `insertText` 塞进局部 span，`cat no` 会变成 `cat cat notes.md`；token 没 range则不知道写到哪里。

## 90. Pager 如何处理 Half Pair

JSON parser 只有在两者同时合法时才保留；缺一项就一起降级为 None，随后使用 whole-line compatibility behavior。

## 91. History/AI 的 Range 为何最终可降级

它们本来就是整行候选，`token_text` 为空。新 Pager 的 atomic parser把它当 rangeless whole-line，语义仍正确。

## 92. `truncated` 也是 Wire 兼容字段

旧 Shell 不发送时默认 false；新 Shell 发现候选不穷尽时标 true，阻止新 Pager做过度自信的 Tab 结论。

## 93. Response Echo Generation

Shell 原样返回 request generation。Pager 只有与 Controller 当前 generation 相等才安装 ghost 和 dropdown。

## 94. Dropdown 保存 Request Anchor

它原子保存 response items、request_text 和 request_cursor。所有 byte range 都是针对该 request text，而不是响应到达时的任意新 Draft。

## 95. Response 不会自动打开 Dropdown

自动路径可只画 ghost；显式 Tab 到达时会执行 Tab policy。网络 landing 本身不应让列表突然抢占界面。

## 96. `last_request_text` 与 `request_text` 不同

前者是 progressive ghost 相对的当前文本锚点；后者固定为 range 计算时的 fetch 文本。混用会让逐字匹配与局部替换互相破坏。

## 97. Glossary（七）：Wire 与兼容性

- **Wire contract**：跨进程、跨版本序列化字段必须遵守的协议约定。
- **Backward compatibility**：新 Shell 仍让旧 Pager 安全工作。
- **Additive upgrade**：保留旧字段语义，再增加可选字段提升能力。
- **Replace range**：要被替换的 `[start,end)` UTF-8 byte span。
- **Token text**：写入 replace range 的局部 replacement。
- **Atomic pair**：两个字段必须同时存在或同时放弃。
- **Request anchor**：候选计算时使用的原始 text 和 cursor。

## 98. Tab 先检查现有 Candidate Set

Dropdown 即使尚未 open 也可缓存 items。`tab_decision` 统一判断应 InstaAccept、Fill、Open 还是 Nothing。

## 99. 没有可用 Items 时发 Deterministic Fetch

Tab 调用 `begin_tab_completion(true)`，generation++，记录 `tab_pending`，发送 tokenOnly request。

## 100. 重复 Tab 不重复发 RPC

若当前 generation 已有 pending Tab fetch，再按 Tab只等待同一 landing。这样网络慢时不会堆积相同请求。

## 101. Landing 会自动运行一次 Tab Policy

TaskResult 安装 response 后，若 `take_pending_tab(generation)` 成功，就调用 `shell_completion_tab`。用户第一次 Tab 不必等完再按第二次。

## 102. 编辑会解除 Pending Tab

任何 invalidation 或新 generation 都让 pending mark 失效。旧 landing 可以到达，但不会再替用户执行 Tab 语义。

## 103. Token-only Request 排除 History 与 AI

`tokenOnly=true` 时 History 直接为空，AI 条件也不成立；只有 PATH/File rows 能参与单候选与 LCP 判断。

## 104. 只有 Complete Token Shape 才能 InstaAccept

所有 items 必须来自 PATH/File、同时有 range+token、且全部 `truncated=false`。任何 whole-line、旧协议或不完整集合都只允许 Open。

## 105. 单一 Token Candidate 直接接受

当穷尽集合只有一个候选时无需闪一下 dropdown，直接在原 span 替换。

## 106. 多候选先计算 LCP

所有 items 必须针对同一 span；它们的 token replacements 若共享一个严格长于已输入 token 的 prefix，第一次 Tab 用该 prefix 填充。

## 107. LCP 是 Longest Common Prefix

例如 `src/main.rs` 与 `src/model.rs` 对输入 `s` 共享 `src/m`，系统可先把确定部分写进去，再重新 fetch 更窄候选。

## 108. 为什么 LCP 要修剪悬空 Backslash

已 quote 的两个候选可能在 escape 中间共享 `a\`。写入单独反斜杠会成为 line continuation 或不完整 escape，因此奇数尾部 backslash 要移除。

## 109. 没有严格扩展就 Open

若公共前缀不比当前 token 长，或大小写关系不满足 `starts_with`，重复填同样内容没有价值，直接展示 dropdown。

## 110. Truncated Set 不能做 LCP 或单候选推断

被 scan cap 隐藏的候选可能否定“唯一”或缩短公共前缀。信息不完备时显示列表比自动写入更安全。

## 111. Mixed/Whole-line Set 只能 Open

History 或 AI 可能整体替换命令，不能与 token rows 一起做局部 LCP。Controller 将策略集中在一个 seam，View 不再自行猜类型。

## 112. Glossary（八）：Terminal Tab

- **InstaAccept**：唯一且安全的 token candidate 不打开列表直接接受。
- **LCP**：多个字符串的最长公共前缀。
- **Fill**：先写入所有候选共同确定的 prefix，再重新取候选。
- **Exhaustive set**：扫描未截断、可认为包含全部匹配的集合。
- **Token shape**：来源、range、token text 和完整性都满足局部编辑要求。
- **Pending Tab**：正在等待 response、landing 后应自动执行一次 Tab policy 的标记。
- **Ambiguity**：现有证据不足以确定唯一安全编辑的状态。

## 113. Range 在接受前必须重新验证

Response 到达后 Draft 可能继续增长。Controller 不能直接信任 fetch-time offsets，而要对当前 text 再检查。

## 114. Current Text 必须延续 Request Text

若当前 text 不以 request_text 开头，说明发生删除、中间编辑或替换，range 直接 stale，接受成为 no-op。

## 115. 光标移动也会让 Tab 重新 Fetch

即使文本没变，用户用鼠标把 cursor 移到别的 token，旧 items 目标错误。Tab 要求当前 cursor 等于 request cursor 加允许的末尾增长量。

## 116. 只允许末尾 Range 吸收 Progressive Growth

若 range 原本结束于 request text 末尾，且用户继续输入仍是 candidate replacement 的 prefix，range end 可延伸到当前 text 末尾。

## 117. 为什么不能普遍平移 Range

中间插入、删除或多光标变化需要真正的 edit transform。当前系统没有该证明，因此只承认最简单、可验证的末尾增长。

## 118. Stale Accept 必须保护 Draft

Range 无效时返回 `CompletionSplice::Stale`；View 消费按键并保持文本不变，绝不退回 whole-line replacement 猜测。

## 119. `ls | gr` 的关键修复

新协议候选同时携带 `insertText="ls | grep"`、`replaceRange=5..7`、`tokenText="grep"`。新 Pager 只改 `gr`，旧 Pager仍可安全 set整行。

## 120. Prompt Atomic Element 还有额外保护

若 range 会切入 paste/image chip 等原子元素，PromptWidget 拒绝 splice。InstaAccept 会退化为打开 dropdown，而不是先消费候选再写失败。

## 121. Accept 后为何立即 Refetch

接受 directory 后通常得到 trailing `/`；重新生成候选让下一次 Tab 可以进入该目录。LCP Fill 后同样需要基于更长 prefix 更新集合。

## 122. Refetch 仍尊重两种 Surface

自动管线开启时走 debounce；关闭时直接发一个 `run_tab_on_load=false` 的 deterministic fetch。Items 安静落地，等待用户下一次 Tab。

## 123. Ghost 接受也会增加 Generation

Right 可接受全部 ghost，Ctrl+Right 可接受一个 word；每次接受使旧在途 response 失效，并关闭 dropdown，避免旧候选覆盖已编辑文本。

## 124. One-word Acceptance 如何切分

它接受可选前导空白和随后第一个非空白 run，保留余下 ghost。这更接近 Shell autosuggestion 的分段接受体验。

## 125. 与 Next-Prompt Ghost 的优先级

PromptWidget 发现 shell ghost、file search、history search、slash UI 等存在时，会隐藏 next-prompt suggestion。Bash completion 是当前输入语义的更具体 owner。

## 126. Glossary（九）：安全编辑

- **Revalidation**：在真正写入前，用最新 Draft 再检查旧 range 仍成立。
- **Stale range**：已不能正确指向原目标 token 的 byte span。
- **Draft-preserving no-op**：拒绝编辑并保持用户文本原样，同时消费本次危险操作。
- **Atomic element**：不可从中间切开的 Prompt chip 或复合编辑器元素。
- **Splice**：以 replacement 替换某个局部 range。
- **Progressive growth**：用户只在原 request 末尾继续输入、且仍沿候选前缀增长。
- **Refetch**：一次安全编辑后重新生成与新 Draft 对齐的候选集合。

## 127. 四类 Provider 对照

| Provider | 输入 | 候选形状 | Ghost | Priority | Tab token-only |
| --- | --- | --- | --- | --- | --- |
| History | 整行 prefix + 历史 | whole line | 第一项可用 | 约 0–10，exact +30 | 排除 |
| PATH | 当前 command token + PATH | token + compat line | 否 | 0 | 包含 |
| File | 当前 arg/path token + CWD | token + compat line | 否 | 0 或 2 | 包含 |
| AI | CWD + partial command | whole line | 可用 | -10 | 排除 |

## 128. 自动与显式 Tab 对照

| 维度 | As-you-type | Tab |
| --- | --- | --- |
| 开关 | `GROK_SUGGESTIONS` | Bash mode 始终可用 |
| 时机 | 50ms debounce | 按键立即/等待 fetch |
| Providers | History、PATH、File、可选 AI | PATH、File |
| `tokenOnly` | false | true |
| `includeAi` | 可配置 | false |
| 输出重点 | ghost + cached rows | token completion semantics |
| Landing | 不自动开列表 | pending 时执行一次 Tab policy |

## 129. 常见误解：自动联想关闭就没有 Tab

错误。自动 surface 是可选便利功能，deterministic Tab 是 Bash mode 基础交互，故意不读 enabled。

## 130. 常见误解：所有候选都能直接 Ghost

错误。PATH/File rows默认不标 ghost candidate；History 第一条和 AI 才能成为整行延续 ghost。

## 131. 常见误解：只有一个返回项就一定安全 InstaAccept

错误。集合可能 truncated，row 可能是 whole-line 或旧协议缺 range。只有完整、穷尽的 token shape 才能推断唯一。

## 132. 常见误解：Quote 只是显示问题

错误。错误 quote 会把一个文件名拆成多个参数，或把分号、管道变成新命令，是执行语义和安全问题。

## 133. 常见误解：Generation 足以证明 Range 有效

错误。光标移动可能不改变 generation，渐进输入也会让 range end变化。因此还需 request anchor、cursor 与 replacement prefix 验证。

## 134. 调试自动 Ghost 的顺序

1. 是否在 Bash mode；
2. `GROK_SUGGESTIONS` 是否开启；
3. Slash/空文本是否 invalidated；
4. Progressive match 是否已本地消费；
5. 50ms timer generation 是否仍匹配；
6. History 是否产生 ghost candidate；
7. AI 是否开启、被 History skip 或超时；
8. Aggregate priority 与 suffix 是否合理；
9. Response generation 是否仍当前；
10. 其他 Prompt UI 是否拥有 ghost 区域。

## 135. 调试 Tab 补全的顺序

1. 当前 mode 是否 Bash；
2. 现有 items 是否 stale；
3. tokenOnly request 的 text/cursor/cwd；
4. tokenizer 的 token、segment、quote、redirect facts；
5. PATH/File Provider 是否被 policy gate；
6. scan 是否 truncated；
7. wire pair 是否完整；
8. current cursor 是否仍对齐 request cursor；
9. range revalidation 是否接受末尾增长；
10. atomic element 是否阻止 splice；
11. action 是 InstaAccept、Fill、Open 还是 Nothing。

## 136. 值得长期守住的测试不变量

- 自动路径关闭时编辑仍使旧候选失效，但 Tab 仍可 fetch；
- 快速输入只有最新 debounce generation 发请求；
- 切 Agent/Mode 不把 timer 路由错目标；
- quoted separator 不重置 command segment；
- PATH 只返回 executable，并对恶意名字 quote；
- File 参数支持 fuzzy、目录优先、`~`/变量 listing expansion；
- 以 `-` 开头的 bare file 插入 `./`；
- stable aggregate 保留 Provider 内部同分顺序；
- half wire pair 降级 whole-line，不做错误局部 splice；
- truncated rows 不做 InstaAccept/LCP；
- pending Tab 不重复 RPC，编辑后 landing 不执行；
- `ls | gr` 只替换 `gr`；
- stale generation/range/cursor 不覆盖 Draft；
- LCP 不写入悬空 escape；
- splice 不切开 Prompt atomic element。

## 137. 一句话记忆

Bash Shell Completion 是一套“双 Surface、四 Provider、两种编辑粒度”的异步补全系统：自动路径用 History/可选 AI 生成低干扰 ghost，显式 Tab 只信任 PATH/File 的确定性 token evidence；Shell 负责有限 POSIX 解析、匹配、quote 和跨版本完整行，Pager 再用 generation、request anchor、cursor、truncation、LCP 与 atomic-element 检查证明每次局部写入仍然安全。

## 138. 复习问题

1. As-you-type 与 Tab 为什么使用不同 Provider 集合？
2. `GROK_SUGGESTIONS` 关闭后为什么 Tab 仍工作？
3. Progressive Match 如何减少网络请求？
4. History、PATH、File、AI 的候选形状和优先级分别是什么？
5. 为什么 aggregate 必须 stable sort？
6. File expansion 为何只用于 listing，而不改写插入路径？
7. Tokenizer 为什么必须区分 quoted `|` 与语法 `|`？
8. `insertText`、`replaceRange`、`tokenText` 如何兼顾新旧 Pager？
9. `truncated=true` 为什么禁止唯一候选和 LCP 推断？
10. Pending Tab 如何避免重复 RPC，又如何防编辑后的自动接受？
11. Range revalidation 为什么还需要 request cursor 和 replacement prefix？
12. `ls | gr` 的 token splice 为什么比整行 replacement 安全？

