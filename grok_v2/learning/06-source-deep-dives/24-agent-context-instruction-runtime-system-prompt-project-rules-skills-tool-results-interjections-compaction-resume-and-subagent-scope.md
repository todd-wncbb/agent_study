# 源码精读 24：Agent Context & Instruction Runtime——System Prompt、项目规则、Skills、Tool Result、Interjection、Compaction 与 Resume

> 源码基线：`ed6d543`
>
> 源码精读 04 研究的是“一个请求最终怎样被组装”；本篇研究更容易被忽略的另一半：这些上下文分别在什么时候创建、什么时候重新读取、什么时候持久化、什么时候只对单次请求生效，以及压缩、恢复、切换模型和派生 Subagent 后怎样避免丢失或重复。

---

## 1. 先给结论：Agent Context 不是一段不断增长的字符串

在 Grok Build 中，模型输入更接近下面这个结构：

```text
ConversationRequest
├── items: Vec<ConversationItem>
│   ├── System
│   ├── User prefix
│   ├── ProjectInstructions
│   ├── SystemReminder
│   ├── real User
│   ├── Assistant + ToolCall
│   ├── ToolResult
│   └── Compaction summary / Interjection / completion notice ...
├── tools: Vec<ToolSpec>
├── hosted_tools
├── tool_choice
├── model / reasoning_effort / max_output_tokens
└── trace、schema、request identity 等控制字段
```

所以“Prompt 如何拼接”至少包含三件不同的事：

1. 文本 instruction 怎样进入 `ConversationItem`；
2. 历史 item 怎样随每轮继续积累或被压缩；
3. Tool Schema 和请求控制参数怎样在发送前与历史并列进入 `ConversationRequest`。

把所有东西都叫“Prompt 字符串”，会看不清它们完全不同的生命周期。

---

## 2. 本篇要回答的核心问题

- System Prompt 是每轮重算，还是 Session 启动时冻结？
- `AGENTS.md` 修改后，正在运行的 Session 会不会自动获得新正文？
- Skill Catalog 和 Skill 正文是不是同一份上下文？
- `/skill args` 为什么不一定需要模型先调用一次 Skill Tool？
- Tool Result 在下一轮为什么会自然成为上下文？
- 用户在工具执行期间发送 Interjection，模型什么时候才能看到？
- 动态 `system-reminder` 为什么实际上是 User Item？
- Memory 是临时修改请求副本，还是永久修改历史？
- Compaction 为什么不能只保存一段摘要？
- Resume、模型切换、Subagent fork 分别继承哪一份 instruction？
- Tool Schema 是 Session 固定、每个用户 Turn 固定，还是每次采样都重建？

---

## 3. 核心源码地图

| 主题 | 主要源码 |
|---|---|
| System Prompt 的结构化输入 | `xai-grok-agent/src/prompt/context.rs` |
| Agent 构建、AGENTS 与 Skill 初始发现 | `xai-grok-agent/src/builder.rs` |
| 项目规则发现 | `xai-grok-agent/src/prompt/agents_md.rs` |
| Skill 初始发现 | `xai-grok-agent/src/prompt/skills.rs` |
| 首条 User Prefix | `xai-grok-agent/src/prompt/user_message.rs` |
| Session 初始化与 Prefix 延迟注入 | `xai-grok-shell/src/session/acp_session_impl/session_setup.rs` |
| 普通 User Prompt 解析与组装 | `xai-grok-shell/src/session/acp_session_impl/prompt_build.rs`、`prompt_parser.rs` |
| Turn 与 Agentic Loop | `xai-grok-shell/src/session/acp_session_impl/turn.rs` |
| Skill slash 展开 | `xai-grok-shell/src/session/slash_commands.rs` |
| Skill Manager 与动态发现 | `xai-grok-tools/src/types/skill_discovery_tracker/`、`reminders/skill_discovery.rs` |
| Interjection | `xai-interjection-core`、`acp_session_impl/interjection.rs` |
| Reminder | `acp_session_impl/reminders.rs` |
| 最终请求快照 | `xai-chat-state/src/actor/request_builder.rs` |
| Compaction 重建 | `xai-grok-shell/src/session/compaction.rs`、`xai-grok-compaction/src/code_compaction/assemble.rs` |
| Subagent 上下文 | `xai-grok-shell/src/agent/subagent/handle_request.rs` |
| System Prompt 替换与零 Turn 重建 | `acp_session_impl/model_switch.rs` |
| 上下文持久化 | `acp_session.rs` 中的 `save_prompt_context`、`save_system_prompt` |

---

## 4. 用四个时间尺度理解上下文

最实用的阅读方法，是把上下文按刷新时机分为四层。

| 时间尺度 | 典型内容 | 刷新时机 |
|---|---|---|
| Agent Build | System Prompt、初始 AGENTS、Tool Registry、Skill baseline | Session 创建或允许的 Agent rebuild |
| User Turn | 真人查询、附件、slash Skill 正文、日期/MCP/任务提醒 | 每次 `handle_prompt` |
| Agent Loop Round | Interjection、Tool Result、动态 Skill reminder、Memory、Tool Schema 快照 | 同一 Turn 的每次重新采样前 |
| Context Epoch | Compaction summary、重播的项目指令与运行态 | Compaction、fork、resume、cwd relocation |

这里的 “Context Epoch” 可以理解为：从一次完整历史或压缩后历史开始，到下一次整体替换历史为止的一段时期。

---

## 5. 三类存活语义：Durable、Request-local、External

除了刷新时间，还要区分内容存在哪里。

### 5.1 Durable Conversation Context

进入 `ChatStateActor.state.conversation`，通常也会写入 `chat_history.jsonl`：

- System；
- Project Instructions；
- real User；
- Assistant；
- Tool Result；
- Interjection；
- System Reminder；
- Compaction summary。

### 5.2 Request-local Projection

只在构造 `ConversationRequest` 的副本上变化：

- 旧 Tool Result 的 soft prune；
- 接近请求字节上限时的旧图片驱逐；
- 配置为不持久化时的 Memory reminder；
- 本轮附加的 Tool Schema、hosted tools、JSON schema。

### 5.3 External Recoverable State

不一定完整进入对话，但 Compaction 时会重新快照：

- 正在运行的 background task；
- 正在运行的 subagent；
- TODO state；
- MCP server catalog；
- Memory index；
- Skill registry；
- cwd relocation generation。

这三个存储层解释了为什么“模型现在看到了”不等于“Session 恢复后还能看到”。

---

## 6. 第一条不变量：Conversation 才是发送历史的权威来源

`PromptContext` 很重要，但它不是每轮模型请求的权威历史。

构建完成后，真正发送的是 ChatState 中的 `Vec<ConversationItem>`。`PromptContext` 的主要作用是：

- 保存渲染 System Prompt 所需的结构化输入；
- 让开发者能检查当时有哪些占位符和发现结果；
- 支持持久化诊断；
- 支持构建时渲染。

而 Resume 后是否保留旧 System、Compaction 后有哪些消息，最终都由 Conversation 决定。

---

## 7. 第二条不变量：Conversation Head 是 System Prompt 的权威值

源码明确说明：

```text
chat_history.jsonl 的第一个 System Item
    = 会话真正使用的 System Prompt

system_prompt.txt
    = 便于查看的镜像文件

prompt_context.json
    = 结构化构建输入和诊断材料
```

`system_prompt.txt` 即使与 Conversation Head 发生偏差，也不是最终权威。

---

## 8. 第三条不变量：动态指令通常追加，不原地改 System

日期变化、任务完成、Skill 发现、计划模式、用户中断等内容，大多通过：

```rust
ConversationItem::system_reminder(...)
```

追加为一个带 `SyntheticReason::SystemReminder` 的 User Item，而不是不断重写 System Item。

这样做有三个收益：

1. 保持旧前缀稳定，有利于 Provider 的 KV cache；
2. 保留事件发生顺序；
3. 可以让 compaction、rewind、analytics 根据结构化原因区别真人输入和运行时注入。

---

## 9. `PromptContext` 保存什么

`PromptContext` 包括：

- schema `version`；
- `prompt_mode`；
- `audience`；
- `prompt_body`；
- `TemplateOverride`；
- 初始发现的 `agents_md_files`；
- Memory 是否启用以及路径；
- role/persona instructions；
- OS、shell、working directory、current date；
- 是否 non-interactive；
- System Prompt identity label；
- build timestamp。

这是一份“Agent 构建快照”，而不是实时环境对象。

---

## 10. 为什么 `PromptContext.current_date` 会变旧

日期是在 `AgentBuilder::build()` 中取一次：

```text
Agent 构建时间
    ↓
PromptContext.current_date
    ↓
渲染 System Prompt
```

长 Session 跨过午夜后，代码不会为了更新日期而重写整个 System Prompt，而是由 `maybe_inject_date_rollover_reminder()` 追加一次提醒。

这正是“稳定前缀 + 增量修正”的典型设计。

---

## 11. System Prompt 的构建边界

`AgentBuilder::build()` 的关键顺序是：

```text
解析 AgentDefinition
    ↓
构建并 finalize ToolBridge
    ↓
初始发现 AGENTS / rules
    ↓
seed AgentsMdTracker
    ↓
初始发现 Skills
    ↓
seed SkillManager
    ↓
创建 PromptContext
    ↓
PromptContext.render(tool_bridge)
    ↓
得到 Agent.system_prompt
```

ToolBridge 必须先 finalize，因为 System Prompt 模板可能引用模型实际可见的工具名。

---

## 12. System Prompt 为什么不是每轮重新 Render

如果每轮都重新渲染，会产生几个问题：

- 日期、路径、Tool Catalog 的微小变化会破坏稳定前缀；
- Resume 后的行为可能与原 Session 不一致；
- 修改磁盘配置会悄悄改变一个正在进行的对话；
- Provider prompt cache 更难命中；
- 很难审计某轮究竟使用了哪套基础身份。

因此正常多轮运行直接复用 Conversation Head。

---

## 13. `prompt_context.json` 目前主要是诊断材料

Session spawn 时会调用 `save_prompt_context()` 将结构化值写到：

```text
{session_dir}/prompt_context.json
```

源码也提供 `load_prompt_context()`，但在当前基线被标注为面向未来 viewer/debug tool 的 dead-code API。

这意味着 Resume 的主路径不是“读 JSON 后重新渲染 System”，而是恢复已经持久化的 Conversation。

---

## 14. Fresh Session 的初始历史形状

初始化阶段大致形成：

```text
[0] System(rendered system prompt)
[1] User(first-user-message prefix)           // 延迟注入
[2] User(ProjectInstructions)                 // 如果存在
[3] User(SystemReminder: Skill baseline)      // 取决于模板与技能
```

具体相对位置会受初始化路径和模板影响，但核心结构不是把所有内容拼到 System 字符串末尾。

---

## 15. 首条 User Prefix 为什么延迟构建

`build_prefix_background()` 可能等待或收集：

- MCP handshake；
- VCS root/status；
- 项目规则；
- Skill metadata；
- MCP server instructions；
- 本地日期和 shell 信息。

为了减少 Session 创建延迟，它在后台构建；第一条 Prompt 真正开始前，`ensure_prefix_ready()` 等待结果。

---

## 16. Prefix 等待失败怎样降级

`ensure_prefix_ready()` 最多等待后台任务 10 秒：

```text
background 成功 → 使用结果
background panic → 同步重建
background timeout → abort 后同步重建
```

所以 Deferred Prefix 是延迟优化，不是正确性依赖。

---

## 17. Prefix 为什么插在 Index 1

Index 0 保留给 System。

```rust
let insert_at = conversation.len().min(1);
conversation.insert(insert_at, ConversationItem::user(prefix));
```

这使其成为稳定的第一条 User-side metadata，而不会跑到已有真人对话尾部。

---

## 18. Prefix 包含的是快照，不是持续订阅

Prefix 可能含：

- Workspace Path；
- OS / shell；
- 当前日期；
- Git/JJ 状态；
- workspace/user rules；
- Skill listing；
- MCP server metadata。

这些数据在 Prefix 构建时采集。Git 状态之后改变，不会自动重写早期 Prefix。

这同样是为了保持历史前缀稳定。

---

## 19. Prefix 什么时候会重建

主要有三类路径：

1. Fresh Session 首次构建；
2. 零 Turn 的 Agent/harness rebuild；
3. Compaction 后构建新的压缩历史。

普通的第 N 个用户 Turn 不会重建 Index 1。

---

## 20. 项目规则的初始发现范围

`read_agents_config_with_paths()` 会处理：

- CWD 到 repo root 的目录链；
- `~/.grok/` 等用户级来源；
- `AGENTS.md`、`CLAUDE.md` 等兼容文件名；
- `.grok/rules/*.md`；
- 兼容开关允许时的 `.claude/rules/*.md`、`.cursor/rules/*.md`；
- gitignore 与 canonical-path dedup。

规则文件同时保存路径和完整正文。

---

## 21. 项目规则的顺序为什么重要

`PromptContext.agents_md_files` 记录从较外层到较内层的发现结果：

```text
repo root rule
    ↓
intermediate directory rule
    ↓
CWD-near rule
```

更靠近工作目录的规则可以表达更具体的约束。它不是 HashSet 随机拼接。

---

## 22. 项目规则为什么是 `ProjectInstructions` User Item

构造函数：

```rust
ConversationItem::project_instructions(...)
```

会产生：

```text
ConversationItem::User
synthetic_reason = ProjectInstructions
```

结构化标签使系统能：

- Resume 时去重；
- Compaction 时原样重播；
- 不把它当成真人 Prompt；
- 不依赖文本前缀判断新格式。

---

## 23. 为什么项目指令需要“原样重播”

如果只让 Compaction LLM 总结 `AGENTS.md`，可能丢失：

- 精确命令；
- 禁止事项；
- 文件作用域；
- 格式约束；
- 安全边界。

因此 `build_compacted_history()` 会把项目指令作为独立 item 放回压缩历史，不依赖 summary 的概括质量。

---

## 24. Resume 怎样避免重复注入项目规则

`conversation_has_project_instructions()` 同时识别：

1. 新格式：`SyntheticReason::ProjectInstructions`；
2. 旧格式：首段文本具有 legacy reminder prefix。

Resume 或 fork 时，如果 Conversation 已有该项，就不再插入第二份。

---

## 25. 初始项目规则修改后会怎样

已经进入 Conversation 的正文不会因为磁盘文件变化而原地更新。

这是刻意的稳定性语义：当前 Session 保存的是启动时看到的 instruction snapshot。

如果希望获得全新的规则正文，可靠边界通常是新建/重建 Agent，或者让模型显式读取文件。

---

## 26. `AgentsMdTracker` 试图解决什么

它的目标是：当 Agent 后来访问初始 CWD 链以外的 repo 子目录时，从目标路径向 git root 上行，发现新的项目指令文件，只提醒一次。

它维护：

- `checked_dirs`；
- `initial_discovery`；
- `reminded`；
- `git_root`；
- gitignore；
- compat flags。

---

## 27. 动态项目规则发现只返回路径，不加载正文

`AgentsMdTracker::check_path()` 的设计语义是 path-only reminder：

```text
发现新的 AGENTS.md
    ↓
告诉模型文件路径
    ↓
模型自行决定是否 Read
```

它不会把运行途中遇到的任意规则正文无条件塞进上下文，以免一次文件访问突然引入大量 instruction。

---

## 28. 一个值得注意的基线事实：Tracker 当前没有生产调用入口

在 `ed6d543` 基线上，对 `AgentsMdTracker::check_path()` 的引用只出现在该模块自己的测试中；生产代码可见的是：

- Builder seed；
- Compaction reset；
- 读取 `reminded_paths()` 构建 compaction reminder。

因此当前代码具备动态发现的数据结构与测试，但看不到把普通 Tool Output 接到 `check_path()` 的生产调用。

阅读源码时必须区分：

```text
“实现了一个能力组件”
    ≠
“主运行路径已经调用这个组件”
```

这也意味着本基线不能仅凭注释推断“访问新目录一定会提醒 AGENTS.md”。

---

## 29. Compaction 为什么仍会 reset `AgentsMdTracker`

`on_agents_md_compaction()` 会清空：

- `reminded`；
- `checked_dirs`。

但不会清空 `initial_discovery`，因为初始项目规则会被原样重播。

这为未来或其他接线方式保留了“压缩后重新提醒动态规则”的正确状态语义。

---

## 30. Skill 有三种不同的上下文形态

必须区分：

1. **Skill Catalog**：名称、描述、触发条件、绝对路径；
2. **Skill Body**：`SKILL.md` frontmatter 之后的完整正文；
3. **Skill Runtime Resources**：Skill 内引用的脚本、模板、图片和其他文件。

Catalog 用来选择；Body 用来执行流程；Resources 按正文需要再读取。

---

## 31. 为什么启动时只列 Catalog

如果几十个 Skill 都把全文放入基础 Prompt，会：

- 大量占用 context window；
- 降低模型对当前任务的注意力；
- 破坏 prompt cache；
- 让不相关流程也变成 instruction。

所以启动时主要暴露 metadata，只有真正触发时才加载正文。

---

## 32. Skill 初始发现的优先级

`list_skills_with_plugins()` 综合：

```text
Local / CWD-near
→ intermediate directories
→ Repo
→ Workspace user
→ User
→ config.paths
→ Server
→ Bundled
→ Plugins
```

然后应用 ignore、disabled、scope 排序、同名覆盖与 plugin qualified-name 规则。

---

## 33. `SkillManager` 是 Session 内的技能权威状态

它同时保存：

- startup baseline；
- dynamically discovered skills；
- conditional `paths:` skills；
- already announced names；
- checked directories；
- pending reconciliation；
- slash-command projection；
- listing budget 与 display cwd rewrite。

`PromptContext` 并不是运行中 Skill Catalog 的权威来源。

---

## 34. Skill 状态有三种 Projection

一次 reconciliation 会产生不同消费者需要的视图：

```text
runtime_skills
    → 写入 AvailableSkills，供 Skill Tool 调用

system_reminder
    → 追加到 Conversation，供模型发现

slash_skills
    → 给客户端命令列表和 /skill 解析
```

这避免 UI、模型和 Tool Runtime 各自维护一套容易漂移的列表。

---

## 35. Baseline Skill 怎样进入新 Session

`SkillManager::seed()` 会在 fresh session 且有可列技能时设置：

```text
pending = BaselineChange
```

`initialize()` 随后调用：

```text
inject_baseline_skill_reminder()
    ↓
ToolBridge.apply_pending_skill_update()
    ↓
SkillManager.take_pending()
```

最终把 listing 作为 SystemReminder User Item 放进 Conversation。

---

## 36. 为什么 Resume 不应重复播报 Skill Catalog

Session 会持久化已宣布的 Skill 名称。

恢复 Agent 时，顺序必须是：

```text
restore_announced_skill_names(...)
    ↓
seed_skill_discovery(...)
```

`seed()` 看到 `announced_names` 非空，就知道旧 Conversation 已经带有 listing，不再制造新的 `BaselineChange`。

---

## 37. Skill Catalog 有独立预算

默认 listing 预算约等于 context window 的 50%，未知窗口时按 200k token 推导字符预算。

单个条目的 description + `when_to_use` 还有组合上限。空间不足时会逐步降级为更短描述甚至仅名称。

它控制的是 Catalog，而不是用户显式调用后加载的完整 Skill Body。

---

## 38. `/skill` 的 zero-round-trip 路径

用户 Prompt 开头引用已知 Skill 时：

```text
slash_commands::resolve
    ↓
ParsedSkillRef { name, args, skill_path, ... }
    ↓
build_skill_information_for_refs
    ↓
load_skill_content
    ↓
apply_substitutions
    ↓
<skill_information> ... </skill_information>
    ↓
与 <user_query> 一起进入当前 User Item
```

模型第一轮推理就能看到 Skill 正文，不必先花一次模型回合调用 Skill Tool。

---

## 39. 为什么叫 zero-round-trip，而不是没有 Tool

“zero-round-trip”只表示加载 Skill instruction 不需要额外的模型 → Tool → 模型往返。

Skill 正文进入上下文后，模型仍可能按照它的要求调用 Read、Bash、Apply Patch、MCP 等工具。

---

## 40. `/skill` 只识别已知技能

解析器扫描空白边界处的 `/word`，但必须在有效 command catalog 中解析成功。

所以：

```text
/commit fix typo      → 可能是 Skill
/api/v2/users         → 不会因为有斜杠就当 Skill
/tmp/file             → 不会误触发
```

这是避免路径和 URL 被误解释的关键边界。

---

## 41. 多个 Skill 引用怎样分配参数

解析器从左到右记录每个命中位置。

一个 Skill 的 args 是：

```text
该 /skill token 结束
    到
下一个已知 /skill token 开始或输入结束
```

因此一次 Prompt 可以加载多个 Skill Block，并保持原顺序。

---

## 42. Skill Body 从哪里来

`load_skill_content()` 有两条路径：

1. `SkillInfo.body` 已预加载：直接使用；
2. 否则按 `SkillInfo.path` 从磁盘读文件，剥掉 YAML frontmatter。

包含 `://` 的合成 product path 如果没有预加载 body，不会尝试当成本地文件读取。

---

## 43. Skill 相对链接为什么会改写为绝对路径

`resolve_skill_internal_links()` 使用 Markdown parser 找到 link/image target，只在以下条件满足时改写：

- 目标确实存在；
- canonical target 仍位于 skill directory 内；
- link 类型可安全定位原文范围。

这样 Skill 中的 `scripts/run.sh`、`references/api.md` 对模型来说不会因 Session cwd 不同而失效，同时避免 `../` 越界改写。

---

## 44. Skill 参数替换支持什么

`apply_substitutions()` 支持：

- `$ARGUMENTS`；
- `$ARGUMENTS[N]`；
- `$N`；
- `${SKILL_DIR}` / `${CLAUDE_SKILL_DIR}`；
- `${SESSION_ID}` / `${CLAUDE_SESSION_ID}`；
- plugin root/data 变量。

如果正文没有消费 argument token，则把参数追加成 `**ARGUMENTS:** ...`，保证参数不会悄悄丢失。

---

## 45. 最终 Skill Envelope 的形状

Prompt-start 展开使用：

```xml
<skill_information>
  <skills_referenced>
    <skill name="commit" path="/abs/path/SKILL.md"/>
  </skills_referenced>
  <skill name="commit" args="fix typo">
    ...完整正文...
  </skill>
</skill_information>
```

索引告诉模型加载了哪些技能和来源；Block 提供真正的 instruction。

---

## 46. Skill 信息与 User Query 的相对位置

`ParsedPrompt::assemble_parts_with_skills()` 构造：

```text
<user_query>...</user_query>
<skill_information>...</skill_information>

attached/resource context...
```

Skill 紧跟用户请求，避免二者被大量附件上下文隔开。

---

## 47. 源码注释与当前实现有一处值得警惕的漂移

`prompt_parser.rs` 的部分注释仍描述 `is_cursor == true` 时采用 query-last；但当前 `assemble_parts_with_skills()` 实际忽略 `is_cursor`：

```rust
let _ = is_cursor;
format!("{query_block}\n\n{context}")
```

也就是当前实现仍为 query/skill first。

源码精读不能只复制注释，必须核对实际分支。

---

## 48. 大 Prompt 怎样保护 Skill instruction

超过 25,000 bytes 时，完整消息会尝试写入 Session 文件，in-band 内容被压缩。

预算策略不是简单截前 25k：

- Skill 有独立最多 4,000 bytes 的 inline budget；
- Query 获得剩余预算的大约 80%；
- Query 和 Skill 都采用 head + tail，保留尾部问题；
- Context 使用剩余空间；
- 消息附带完整文件路径，要求模型先 Read。

---

## 49. 为什么 Skill 需要独立截断预算

如果 Query 很长，普通拼接很容易把 Skill 正文全部挤掉。

那会造成一种危险假象：UI 显示 Skill 已被调用，但模型实际没有看到它的关键规则。

独立预算至少保证一部分 Skill instruction 与路径提示留在当前请求中；完整版本仍在 offload 文件。

---

## 50. Offload 写失败为什么不能退回完整原文

完整原文已经超大，写文件失败后若把它直接发送，会重新触发 context overflow。

因此失败路径仍发送 bounded preview，只把“请读取某文件”替换成“文件未保存，请基于摘录回答，必要时让用户重发”。

这是典型的 bounded fail-safe。

---

## 51. Skill Tool 路径与 slash 展开路径的区别

模型也可以根据 Catalog 主动调用 Skill Tool。该路径返回 `SkillOutput`，其中 `skill_message` 用统一 `<skill>` envelope 包装正文。

两条路径共享正文加载和格式化原则，但触发时机不同：

```text
用户明确 /skill
    → Prompt assembly 直接展开

模型根据 Catalog 判断适用
    → 模型调用 Skill Tool，再得到 ToolResult
```

---

## 52. 动态 Skill Discovery 怎样触发

`SkillDiscoveryReminder` 观察 Tool Output：

- ReadFile；
- ListDir；
- SearchReplace；
- ApplyPatch 用于激活 `paths:` 条件技能。

它从相关路径附近查找 `.grok/.agents/.claude/.cursor` 下的 skills，并把新 Skill 加入 `SkillManager`。

---

## 53. 为什么动态发现不直接返回 Reminder 文本

Tools 层只更新状态：

```text
SkillDiscoveryReminder
    ↓
SkillManager.add_discovered(...)
    ↓
pending = Discovery
```

Session 层在 Tool 完成后调用 `apply_pending_skill_update()`，再负责：

- 更新 Runtime `AvailableSkills`；
- 发送新的 model reminder；
- 刷新客户端 slash commands。

这保持 Tools 层与 Conversation/UI 能力解耦。

---

## 54. `paths:` 条件 Skill 为什么单独处理

有些 Skill 只应在触碰特定文件时出现。

`activate_conditional_skills_for_paths()` 会检查本次工具实际涉及的路径；ApplyPatch 的多文件修改和 move destination 都会参与匹配。

未命中前，Skill 不进入模型 listing，避免无关技能过早污染 Catalog。

---

## 55. Turn 运行中发现 Skill 怎样安全注入

如果当前没有 Turn，Skill reminder 可以直接追加 Conversation。

如果模型或工具正在运行，则先放入：

```text
pending_skill_reminders: Mutex<Vec<ConversationItem>>
```

随后在安全点由 `flush_pending_skill_reminders()` 追加，避免与正在构造的请求或工具批次并发修改历史。

---

## 56. Skill Reminder 的安全点

源码中的主要 drain 点包括：

- 每次 Agent Loop 采样前；
- Tool batch 收尾；
- Turn end/cancel 收敛；
- 某些 Session event-loop 路径。

因此新 Skill 通常在下一次模型采样时可见，而不是修改已经飞往 Provider 的请求。

---

## 57. Concise Mode 的动态 Skill 限制

源码注释明确说明：`SkillDiscoveryReminder` 目前仍耦合到 System Reminder 机制。

若 `SystemRemindersEnabled(false)`，动态 Skill Discovery 也不会触发。

这是当前 V1 layering compromise，不应误读成“只是不显示提醒，后台仍会发现”。

---

## 58. Compaction 后 Skill 怎样恢复

Compaction 前会获取 `slash_skills_for_resolve()` 的完整当前列表，并用标准 formatter 写入 post-compaction System Reminder。

然后：

```text
SkillManager.on_compaction()
    clears announced_names
    clears checked_dirs
    preserves discovered_skills
```

结果是：

- 已知技能仍可用于 slash commands 和 compaction listing；
- 之后访问目录时可以重新发现/宣布；
- 完整 Skill Body 不会全部复制进 compaction history。

---

## 59. `/clear` 与 Compaction 的 Skill 语义不同

`on_clear()` 会：

- 清空动态发现技能；
- 清空 canonical paths、checked dirs、announced names；
- 重新隐藏 conditional skills；
- 保留 startup baseline；
- 排队一次新的 `BaselineChange`。

Compaction 是缩短历史但保留 Session 能力；Clear 是重置动态上下文。

---

## 60. 普通 User Prompt 进入 Context 的主链

```text
ACP ContentBlock[]
    ↓
slash command / skill resolution
    ↓
parse_prompt_with_skills
    ├── Text 合并
    ├── @file 加载
    ├── Resource / ResourceLink 渲染
    ├── Image 分离
    └── Skill Information 附加
    ↓
large-prompt offload / image normalization
    ↓
ConversationItem::User
    ↓
ChatStateActor.push_user_message
```

用户 UI Echo 与模型实际 User Item 不是完全相同的字符串：Skill Body、附件正文、图片转写、offload notice 都可能只存在于模型侧组装结果。

---

## 61. 为什么 UI Echo 不应直接等同于模型输入

Turn 开始时，客户端收到的是原 `prompt_blocks` 的 UserMessageChunk。

之后 Shell 才会：

- 解析 file reference；
- 加载 Skill Body；
- 规范化图片；
- 加入附加资源；
- 大 Prompt offload；
- 注入内部 reminder。

所以调试模型行为时，应检查 Conversation/trace artifact，而不能只看 UI 上用户发送的短文本。

---

## 62. `SyntheticReason` 是上下文控制面的关键字段

主要类型包括：

- `CompactionMeta`；
- `SystemReminder`；
- `ProjectInstructions`；
- `AutoContinue`；
- `AutoRecovery`；
- `Interjection`；
- `TaskCompleted`；
- `SubagentCompleted`；
- `NotificationDrain`；
- `GoalSummary`；
- `StopHookFeedback`；
- `WorkingDirectorySwitch`。

它让系统不必通过脆弱的字符串匹配判断“这是不是一个真人新问题”。

---

## 63. Synthetic User Item 为什么仍使用 User Role

许多 Provider API 对 role 和消息顺序有严格限制，而且模型需要把运行时状态当成当前轮输入来关注。

Grok Build 选择：

```text
协议 Role：User
运行时语义：SyntheticReason
文本边界：<system-reminder> 等 envelope
```

Role 服务模型协议；SyntheticReason 服务内部状态机。

---

## 64. System Reminder 怎样防止 Wrapper 注入

`push_system_reminder_with_tag()` 会先把正文中的 closing tag：

```text
</system-reminder>
```

改成 escaped 形式，再包裹外层 tag。

这样动态内容不能提前闭合 reminder envelope，降低边界混淆。

---

## 65. Turn 开始前会注入哪些动态上下文

在真实 User Item push 前后，Session 会处理多类状态：

- MCP available/connecting reminder；
- date rollover；
- plan mode；
- resumed background tasks；
- deferred completions；
- workflow status；
- prior interrupt reminder；
- task/subagent completion；
- user images and attached resources。

具体函数有各自 gate 和 dedup 状态，不是每轮无条件重复。

---

## 66. 日期提醒为什么只向前推进

`date_rollover_reminder(today, last_announced)` 只在：

```text
today > last_announced
```

时返回提醒。

系统时钟向后调整不会注入“新日期”，同一天重复 Turn 也不会重复播报。

---

## 67. Resumed Background Task Reminder 为什么是一次性

Shutdown 时把仍在运行的任务写入 manifest；Resume 后：

```text
load_and_clear_manifest
    ↓
生成 reminder
    ↓
删除/清空 manifest
```

所以它是一次性恢复上下文，而不是每 Turn 重复扫描历史文件。

---

## 68. Tool Schema 属于哪里

Tool Schema 不作为普通 ConversationItem 持久化。

在 `process_conversation_turn_with_recovery()` 开始时：

```text
prepare_tool_definitions_timed()
    ↓
等待所需 MCP 初始化
    ↓
得到 ToolDefinition 列表
```

每次 Agent Loop Round 构建请求时，再投影成 `ToolSpec`，应用 backend-search 过滤和 structured-output tool 附加。

---

## 69. Tool Definition 在同一个 Turn 内是否每轮重建

基础 `tool_definitions` 在进入当前 conversation turn 时准备一次，随后 Agentic Loop 的多次采样复用这份列表。

但是每轮仍会重新计算：

- `effective_tools`；
- structured output tool 是否附加；
- hosted tools；
- Tool Overrides；
- 完整 Conversation items。

因此是“Turn-level base snapshot + Round-level request projection”。

---

## 70. MCP Progressive 初始化意味着什么

Blocking 策略会在准备 Tool Definitions 前等待 MCP initialized。

Progressive 策略允许 Session 更早开始，MCP 工具和提醒可在后续状态变化时进入可见范围。

这说明 Tool Catalog 也不是只能在进程启动时确定；但已经发出的某次请求当然不会被原地修改。

---

## 71. Hosted Tool 为什么不在普通 Tool Schema 中

Web/X search 等可以作为 Provider 原生的 hosted tool 发送。

`ConversationRequest` 中它们位于 `hosted_tools`，并受 backend capability 与 per-turn override 控制；开启 backend search 时，本地同名 `web_search` ToolSpec 会被过滤，避免模型看到两条冲突执行路线。

---

## 72. 一次 Model Response 怎样改变下一轮 Context

如果响应包含文本和 Tool Calls：

```text
Assistant/Reasoning/BackendToolCall items
    ↓
写入 Conversation
    ↓
本地工具执行
    ↓
ToolResult items
    ↓
下一次 build_request clone 全部 Conversation
    ↓
模型看到“我调用了什么 + 工具返回了什么”
```

Agentic Loop 的核心并不是在内存里拼一个临时 tool result 字符串，而是持续扩展结构化历史。

---

## 73. Tool Result 为什么必须保留 `tool_call_id`

Provider 需要把：

```text
Assistant ToolCall(id=X)
```

与：

```text
ToolResult(tool_call_id=X)
```

配对。

这就是 Repair、Cancellation 和 Compaction 都必须维护 ToolCall/ToolResult 完整性的原因。只有文本内容正确但 ID 丢失，整个后续请求仍可能被 Provider 拒绝。

---

## 74. Tool 后生成的 Reminder 怎样进入下一轮

Tool execution 会收集 deferred followups，例如：

- 新 Skill listing；
- post-tool reminder；
- task completion context；
- permission/hook feedback。

批次完成后，先把这些 `ConversationItem` 追加到 ChatState，再 drain interjection 和 pending skill reminders，然后返回 `ToolLoop::Continue`。

下一次采样自然看到它们。

---

## 75. Interjection 与新 User Turn 有什么不同

Interjection 是用户在当前 Turn 尚未结束时发送的 steering message。

它：

- 不取消当前 Turn；
- 不消耗一个新的 prompt index；
- 用 `SyntheticReason::Interjection` 标记；
- 在安全点进入 Conversation；

而普通 User Prompt 会开始一个新的 Turn，并参与 Prompt Queue 调度。

---

## 76. Interjection Buffer 保证什么

共享 `xai-interjection-core` 使用 FIFO `EventQueue`：

- 每个输入保持独立，不合并；
- 顺序不变；
- host 可先清理路径等敏感 artifact；
- 统一用 `format_interjection()` 包装。

包装形状：

```text
The user sent a message while you were working:
<user_query>
...
</user_query>
```

---

## 77. Interjection 什么时候被 drain

主要安全点：

1. 每次采样前；
2. Tool batch 完成后；
3. 模型准备无 Tool 结束时；
4. Turn bookkeeping 后再检查一次迟到消息。

最后两个检查消除了“刚判断可以结束，用户消息恰好到达”的竞态窗口。

---

## 78. 为什么 Interjection 到达后不会修改 in-flight 请求

HTTP/SSE 请求已经发出后，不能往 Provider 的同一请求体里追加新 Item。

所以 Interjection 必须等当前响应产生一个安全边界，然后促使 Agent Loop `continue`，使用更新后的 Conversation 重新采样。

---

## 79. 迟到或 Idle Interjection 怎样避免丢失

如果 Interjection 到达时没有正在运行的 Turn，或错过最后 drain，它会被转换为独立的 fallback Prompt：

```text
interject-fallback-{uuid}
```

并插入 Queue 前部；若队首已有 in-flight Prompt，则放在它后面，不能破坏完成处理对队首的所有权假设。

---

## 80. Mid-turn `/skill` 怎样展开

Interjection 不经过 Turn-start 的 `slash_commands::resolve()`，因此有专门的：

```text
interjection_skill_information()
```

它只在文本以 `/` 开头时解析 Skill，加载正文并把 `<skill_information>` 放在 wrapped `<user_query>` 后面。

这样 send-now 的 `/skill` 与普通 Turn-start `/skill` 具有一致 instruction 内容。

---

## 81. 为什么 Interjection 的持久化 Echo 不包含 Skill Body

模型侧 `ConversationItem::interjection` 包含扩展后的 Skill Information。

但写到 UI replay 的 UserMessageChunk 只使用 wrapped 用户文本，不带完整 `SKILL.md`。

原因是：

- 用户回放应看到自己发的简洁命令；
- 不应把几千行 Skill 正文伪装成用户输入显示；
- 模型历史仍保留执行所需 instruction。

---

## 82. Interjection 的大文本怎样处理

共享 formatter 在 25,000 bytes 左右截断，并保证 UTF-8 边界。

与普通大型 Prompt 不同，Interjection 不走完整 file offload 路径；它强调及时 steering，所以采用较简单的 bounded injection。

---

## 83. Memory Context 的首次注入 Gate

`first_turn_memory_reminder()` 首先检查：

- 本 Context Epoch 是否已经尝试注入；
- 配置是否启用；
- storage/backend 是否存在；
- System 中是否已有 `<memory-context>`。

已有 block 时不会重新搜索，以保持历史与 prompt cache 稳定。

---

## 84. Memory Search 使用什么 Query

默认取最后一条 real user query。

如果为空、太短或像 greeting，则使用：

```text
project conventions preferences architecture
```

再搜索若干条高相关 Memory，格式化成 reminder。

---

## 85. Memory Reminder 的 request-local 与 durable 两种模式

`ChatStateActor::build_conversation_request()` 接受：

```rust
memory_reminder: Option<String>
persist_memory_reminder: bool
```

如果要求持久化，它会先把 Memory block upsert 到真实 System Item，并写回 history；否则只在 request clone 上注入。

Shell 当前调用时将 `self.memory.is_enabled()` 作为 persist flag，因此启用 Memory 的正常路径会把首次结果固定到 Session System 中。

---

## 86. 为什么 Memory 使用 Upsert 而不是 Append

`upsert_memory_reminder_text()` 会定位已有 `<memory-context>` 开始位置，并替换旧 block。

这样不会在 System Prompt 尾部累积多个 Memory 副本，也不会每次搜索导致前缀无限增长。

---

## 87. Compaction 后为什么重置 Memory Gate

压缩完成后：

```text
memory.context_injected = false
```

下一 Turn 会重新检查。若压缩历史仍有 Memory block，则跳过搜索；若 block 没有被保留，则可重新注入相关 Memory。

这叫“重新检查”，不等于“无条件重新搜索”。

---

## 88. 请求构建前还会做哪些 Context Projection

`build_conversation_request()` 的顺序：

1. 必要时持久化 Memory；
2. 测量精确请求 body bytes；
3. 接近 50 MB 时驱逐最老 inline image；
4. context 使用超过 50% 时 prune 旧 Tool Result；
5. 若 Memory 未持久化，只注入 request clone；
6. 组装 `ConversationRequest`。

这些操作不都具有相同的持久化语义。

---

## 89. Tool Result Pruning 默认只改请求副本

超过一半 context window 时，旧 Tool Result 会根据距当前 Turn 的年龄：

- recent：保留；
- 较旧且很大：保留 head + tail；
- 很旧：替换成 `[Tool result omitted — too old]`。

`build_conversation_request()` 在 clone 上执行该投影，所以不能简单认为磁盘历史已经被同样截断。

另外 ChatState 的 retained-conversation 路径还有主动 hard-clear，用于限制长期内存；两者要分别阅读。

---

## 90. Image Eviction 为什么也是 Request Projection

旧图片很大，会触发 Provider 请求体大小限制并破坏 cache。

接近上限时，请求副本把较旧图片替换为明确占位文本，告诉模型图片已经不可见、需要时让用户重新发送。

不能静默删除，否则模型可能根据早期文字“假装仍看得到图片”。

---

## 91. Compaction 的目标不是“把历史变成一段摘要”

完整重建结构是：

```text
[0] original System
[1] rebuilt User Prefix
[2] ProjectInstructions（原样）
[3] last real User Query
[4...] selected recent messages
[N] Compaction summary
[N+1] reconstructed System Reminder
```

这是一份新的、结构完整的 Conversation，而不是把所有消息替换成一个 summary 字符串。

---

## 92. Compaction Summary 保存什么

摘要适合保存：

- 已完成工作；
- 当前目标；
- 重要决策；
- 未解决问题；
- 关键文件与验证结果。

但不适合单独承担：

- System identity；
- 精确项目规则；
- ToolCall/ToolResult 协议结构；
- 当前运行任务 ID；
- Skill Catalog；
- MCP 使用方法。

后几类需要确定性重建。

---

## 93. Post-compaction Reminder 重建哪些运行态

`to_system_reminder()` 可包含：

- Agent 编辑过的文件；
- 动态发现的项目指令路径；
- 当前 Skill Catalog；
- running background tasks；
- actionable TODOs；
- running subagents；
- connected MCP servers；
- relevant Memory；
- plan mode state。

这让外部状态与被压缩的 Conversation 重新对齐。

---

## 94. 为什么运行态不能完全交给 Summary LLM

Summary LLM 采样开始后，外部状态可能继续变化：

- background task 完成；
- subagent 新增或结束；
- MCP 连接状态改变；
- TODO 被工具更新。

因此 Compaction 在重建阶段从权威 state 再快照，而不是相信输入 transcript 中的旧描述。

---

## 95. Compaction 怎样保留最后一个真实用户问题

`CompactionStateContext` 区分 real User 与 Synthetic User。

最后真实 Query 会重新包装：

```xml
<user_query>
...
</user_query>
```

这样摘要描述“做到哪里”，最后问题仍明确描述“用户原本要什么”。

---

## 96. Recent Messages 为什么还要保留

只保留摘要可能丢失刚发生的：

- Assistant ToolCall；
- 配对 ToolResult；
- 最新错误细节；
- Interjection；
- 尚未被摘要充分吸收的回答片段。

所以重建允许把最后真实 User 后的一部分消息原样放在 summary 前。

---

## 97. Compaction 为什么还要 Sanitize Tool 结构

Recent Messages 的裁剪边界可能留下 orphaned ToolResult。

流程会：

```text
build_compacted_history
    ↓
sanitize_compacted_history
    ↓
validate_compacted_history
```

若仍不合法，就回退为不带 recent messages 的 minimal compacted history。

Context 完整性优先于多保留几条近期细节。

---

## 98. Compaction 后哪些 Tracker 会 Reset

成功替换 Conversation 后：

- Memory injection gate reset；
- TODO persistence state清理/重建；
- AgentsMdTracker compaction reset；
- SkillManager compaction reset；
- announcement state 持久化；
- plan mode reset-after-compaction；
- auto-compaction suppression 根据新 token 状态调整。

所以 Compaction 同时也是一个 runtime epoch transition。

---

## 99. System Prompt 在 Compaction 中为什么保留原值

重建读取当前 Conversation 的 System Item，并把它作为新历史第 0 项。

不会从最新磁盘配置重新生成，也不会用 Summary LLM 改写。

这保持 Session identity 与已经生效的 client override。

---

## 100. User Prefix 在 Compaction 中为什么重新生成

与 System 不同，Compaction 会调用 `build_user_message_prefix()`。

这允许新的压缩 Epoch 获得较新的：

- 日期；
- VCS status；
- MCP metadata；
- rules/skills listing（取决于模板）；
- workspace state。

它以整体历史替换为边界，不会在普通 Turn 中频繁破坏前缀。

---

## 101. CWD Relocation 后项目规则怎样变化

Working-directory switch 使用：

```text
SyntheticReason::WorkingDirectorySwitch
cwd_generation
```

并通过 durable acknowledgement 保证同一 generation 的 append 幂等收敛。

Compaction 时，如果 `cwd_generation > 0`，项目指令不再默认使用原 Agent 的初始 `agents_md_reminder`，而是使用 `destination_project_instructions`。

这是“工作区身份已经改变”的明确 Context Epoch 语义。

---

## 102. 为什么 CWD Switch 要带 Generation

如果只用 reminder 文本去重：

- crash 后不知道是否已经写入；
- 重试可能重复追加；
- 相同路径文本无法区分两次 relocation；
- Memory 与 Disk 可能采用不同版本。

Generation 让系统可以按结构化事务身份决定 `Appended` 或 `AlreadyPresent`。

---

## 103. Resume 的 System Prompt 语义

`install_system_prompt()` 对 top-level resumed session 的规则是：

```text
Conversation 已有 leading System
    → 保留 stored System
```

即使当前 AgentBuilder 能渲染一个“更新”的 System，也不会自动覆盖旧 Session 身份。

这保证 Resume 是继续原对话，不是偷偷迁移到新 instruction。

---

## 104. 缺少 System 时怎样修复

如果恢复的 Conversation 没有 leading System，则插入当前渲染值；若存在 `inherited_prefix_len`，同步加一。

这个长度调整很重要，否则 fork/compaction 可能错误地释放或保留前缀范围。

---

## 105. Client `systemPromptOverride` 怎样生效

Attach 时的 override 不清空历史，只通过 ChatStateActor 原子替换 leading System。

同时更新 `system_prompt.txt` 镜像。

如果是 `preserve_inherited_system` 的 verbatim mirror fork，则明确跳过 override，避免破坏父上下文的字节级继承。

---

## 106. 模型切换为什么只允许零 Turn 重建完整 Agent

不同模型/agent type 可能具有不同：

- System template；
- Tool set 与名称；
- User Prefix template；
- Skill listing format；
- MCP registration；
- capability gates。

在已有多轮历史后整体替换这些内容，会让旧 ToolCall 和新 Schema、旧 instruction 和新行为混合。

所以完整 harness rebuild 在 zero-turn 边界最安全。

---

## 107. Zero-turn Rebuild 会更新什么

流程会：

- 构建新 Agent/ToolBridge；
- 等待并重新注册 MCP tools；
- 替换 System head；
- 重建 first User Prefix；
- 如缺少则插入 ProjectInstructions；
- 注入新的 baseline Skill reminder；
- 保存新的 PromptContext 与 System artifact；
- 原子重写 Conversation snapshot；
- 刷新 Available Commands。

这是一次受控的完整 Context 重建。

---

## 108. Subagent 为什么需要自己的 `PromptAudience`

`PromptAudience::Subagent` 会选择更紧凑的基础模板，并清理 primary-only catalog，例如 persona summaries。

但项目规则仍完整提供，因为子 Agent 同样可能修改或验证仓库文件。

“更短的身份模板”不等于“可以忽略项目约束”。

---

## 109. Subagent 的 System Prompt 继承有两种模式

普通 Subagent spawn：

```text
is_subagent = true
preserve_inherited_system = false
    → 用 child fresh System 覆盖 leading System
```

Verbatim mirror fork：

```text
preserve_inherited_system = true
    → 原样保留 parent System
```

前者强调角色隔离，后者强调上下文字节一致性。

---

## 110. Subagent 怎样获得项目规则

`PromptContext::agents_md_user_reminder()` 对 Primary 和 Subagent 都返回完整 block。

Spawn 时若 forked Conversation 已有 ProjectInstructions，则去重；没有则插入 child Agent 当前发现的项目指令。

Compaction 后仍按同样的结构化 item 保留。

---

## 111. Subagent 怎样获得 Skills

Agent definition 的 `inherit_skills` 决定是否继承 Parent 发现结果。

启用时，父 Session 可先完整发现 skill list，再通过 `preloaded_skills` 传给 Child Builder，避免 Child 重复磁盘发现并保持父子视图一致。

禁用时，Child 使用空/default SkillsConfig 或自己的定义范围。

---

## 112. 为什么 Subagent Task Prompt 仍要直接写关键约束

即使 Subagent 有项目规则和 Skill Catalog，任务描述仍应包含完成当前子任务必需的具体边界。

原因包括：

- child 可能使用 compact prompt；
- Skill 只有 metadata，未必自动加载 Body；
- fork mode 不同；
- 项目规则很长，关键约束可能不够显著；
- Subagent 只应完成一个 bounded task。

Catalog 是可发现性，不是任务委托本身。

---

## 113. Tool Result、Skill Body 与 Project Rule 的信任语义不同

从模型视角它们都是文本，但运行时来源不同：

| 内容 | 来源 | 典型作用 |
|---|---|---|
| System Prompt | 产品/Agent definition | 身份和最高层运行规则 |
| ProjectInstructions | workspace/user config files | 仓库约束 |
| Skill Body | 被选择的 workflow package | 某类任务的操作流程 |
| Tool Result | 外部世界/本地执行结果 | 事实、输出、错误 |
| Interjection | 用户 mid-turn 输入 | 即时改向 |
| SystemReminder | runtime state machine | 当前状态与继续规则 |

系统用 envelope 和 SyntheticReason 保留来源边界，但模型仍需要遵守更高优先级 instruction，不应把 Tool Output 中的任意文本当成同级系统规则。

---

## 114. 为什么“进入 Conversation”还不等于“永远保留全文”

一个 item 后续可能被：

- request-local prune；
- retained-history hard clear；
- image eviction；
- compaction summary 替代；
- rewind 删除；
- session repair 修改；
- fork prefix policy 裁剪。

持久化意味着可恢复，不意味着永不经过上下文管理。

---

## 115. Prompt Cache 稳定性的真实边界

有利于 cache 的做法：

- System 固定在 index 0；
- Prefix 固定在 index 1；
- ProjectInstructions 一次注入并结构化去重；
- 日期用尾部 reminder 修正；
- Skills 用增量 listing；
- Memory 已存在时不重搜；
- Reasoning/BackendToolCall 顺序稳定保存。

会主动打破 prefix 的边界：

- zero-turn Agent rebuild；
- client System override；
- Compaction 整体替换；
- cwd relocation 后的 Context Epoch；
- 接近 body 上限的图片驱逐。

---

## 116. 一次 Fresh Session 的 Context 时序

```text
AgentBuilder
  ├─ finalize tools
  ├─ discover AGENTS/rules
  ├─ discover Skill metadata
  ├─ seed trackers
  └─ render System Prompt
        ↓
spawn SessionActor
  ├─ save PromptContext/System
  ├─ install System
  ├─ insert ProjectInstructions
  ├─ initialize Skill baseline reminder
  └─ start deferred Prefix build
        ↓
first real Prompt
  ├─ ensure Prefix ready at index 1
  ├─ expand /skills and attachments
  ├─ inject dynamic reminders
  ├─ push real User Item
  └─ start Agentic Loop
```

---

## 117. 一次 Tool Round 的 Context 时序

```text
Round start
  ├─ drain Interjections
  ├─ flush pending Skill reminders
  ├─ inject monitor/MCP state
  ├─ maybe inject Memory
  ├─ maybe compact
  ├─ project Tool Specs
  └─ build ConversationRequest
        ↓
Model response
  ├─ append Assistant/Reasoning/BackendTool items
  └─ parse ToolCalls
        ↓
Tool batch
  ├─ append ToolResults
  ├─ dynamic Skill discovery
  ├─ append deferred reminders
  ├─ drain late Interjections
  └─ continue next Round
```

---

## 118. 一次 Compaction 的 Context 时序

```text
snapshot old Conversation
    ↓
select/prepare compaction input
    ↓
LLM generates summary
    ↓
re-read authoritative runtime state
    ├─ Prefix
    ├─ running tasks/subagents
    ├─ TODO
    ├─ Skills
    ├─ MCP
    ├─ Memory
    └─ plan mode
    ↓
assemble canonical compacted history
    ↓
sanitize + validate Tool structure
    ↓
persist checkpoint
    ↓
replace Conversation
    ↓
reset epoch-scoped trackers
```

---

## 119. 常见误解：每轮都会重读 `AGENTS.md`

不会。

初始规则是 Agent Build snapshot；普通 Turn 复用 Conversation 中的 ProjectInstructions。Compaction 会重播已有初始 block，并可能使用 relocation destination instructions，而不是普通每轮重扫磁盘覆盖正文。

---

## 120. 常见误解：Skill 已出现在 Catalog，模型就看过 Skill 正文

错误。

Catalog 只含选择所需 metadata。只有 slash expansion、Skill Tool 调用或 agent definition preload 等激活路径，才会加载 Body。

---

## 121. 常见误解：System Reminder 就是 System Role

错误。

在内部它通常是带 `SyntheticReason::SystemReminder` 的 User Item，只是正文用 `<system-reminder>` 包装。

---

## 122. 常见误解：Tool Result 只是给 UI 显示

错误。

它是后续模型推理的核心结构化输入，并必须与 Assistant ToolCall ID 配对。UI rendering 只是另一个消费者。

---

## 123. 常见误解：Interjection 会立即打断 HTTP Stream

错误。

它不会取消 Turn，也不能修改已发送请求。它在下一个安全点进入 Conversation，然后触发新的 Agent Loop 采样。

---

## 124. 常见误解：Compaction 只保留 Summary

错误。

System、Prefix、Project Instructions、last query、recent protocol items 和 runtime reminder 都有各自的确定性重建路径。

---

## 125. 常见误解：`prompt_context.json` 决定 Resume Prompt

当前主路径不是这样。

Conversation Head/History 是权威；PromptContext JSON 主要支持检查、调试和未来重新渲染能力。

---

## 126. 修改 System Prompt 生命周期时的检查清单

- Fresh 与 Resume 是否语义不同？
- Top-level 与 Subagent 是否语义不同？
- `preserve_inherited_system` 是否仍生效？
- Conversation Head 和 `system_prompt.txt` 是否同步？
- PromptContext 是否保存对应结构化输入？
- 是否无意破坏 KV-cache prefix？
- Compaction 是否保留 override 后的当前 System？
- zero-turn rebuild 测试是否覆盖？

---

## 127. 修改项目规则加载时的检查清单

- 发现顺序是否 deterministic？
- deeper rule 是否保持更高具体性？
- user scope 与 workspace scope 是否正确分区？
- compat flags 是否统一作用于启动和运行期？
- gitignore 与 symlink canonicalization 是否一致？
- Resume 是否去重旧格式和新格式？
- Compaction 是否原样重播关键正文？
- 动态 tracker 是否真的接入生产调用，而不只是有测试？

---

## 128. 修改 Skill 生命周期时的检查清单

- Catalog 与 Body 是否仍按需分离？
- Slash、Skill Tool、Interjection 是否使用相同正文加载语义？
- args/path/plugin substitutions 是否一致？
- dynamic discovery 是否在 Resources lock 外做文件 I/O？
- runtime、reminder、slash projection 是否同时更新？
- Turn 运行时是否先排队到安全点？
- Resume 是否恢复 announced names？
- Compaction 与 `/clear` 是否保持不同 reset 语义？
- listing budget 是否不会把一个条目截成无意义内容？

---

## 129. 修改 Interjection 时的检查清单

- FIFO 是否保持？
- 一条输入是否仍是一条独立 Synthetic User Item？
- 已发送的请求是否不会被并发修改？
- Tool batch 后和 Turn end 前是否都有 drain？
- 迟到消息是否进入 fallback Prompt 而不是丢失？
- queue front 是否不会挤走 in-flight owner？
- 图片路径是否已清理？
- `/skill` 是否展开且 UI replay 不泄漏完整 Body？
- 超长 UTF-8 文本是否安全截断？

---

## 130. 修改 Compaction Context 时的检查清单

- System 是否保留当前权威版本？
- Prefix 是否在明确 epoch 边界重建？
- ProjectInstructions 是否 deterministic 重播？
- relocated Session 是否使用 destination rules？
- last real User 是否排除 Synthetic User？
- Recent ToolCall/ToolResult 是否完整？
- Summary 后的 runtime state 是否来自最新权威来源？
- Agents/Skills/Memory/Plan tracker 是否正确 reset？
- fallback minimal history 是否仍协议合法？
- 新增外部状态是否需要加入 post-compaction reminder？

---

## 131. 推荐阅读源码的顺序

如果要亲自跟代码，建议按下面顺序：

1. `ConversationItem` 与 `SyntheticReason`；
2. `PromptContext`；
3. `AgentBuilder::build()`；
4. `spawn_session_actor()` 的 System/ProjectInstructions 安装；
5. `SessionActor::initialize()` 与 `ensure_prefix_ready()`；
6. `process_conversation_turn()` 的 slash + prompt parse；
7. `process_conversation_turn_with_recovery()` 的 Agentic Loop；
8. `ChatStateActor::build_conversation_request()`；
9. Tool batch 与 Interjection drain；
10. `run_compact_inner()` 与 `build_compacted_history()`；
11. Subagent spawn；
12. zero-turn rebuild 和 System override。

这个顺序从数据模型开始，最后再看特殊生命周期，不容易迷路。

---

## 132. 推荐验证的测试组

### Prompt / Session 安装

```bash
cargo test -p xai-grok-shell prompt_context_persistence --lib
cargo test -p xai-grok-shell project_instructions_idempotence --lib
cargo test -p xai-grok-shell replace_system_prompt --lib
```

### Skill

```bash
cargo test -p xai-grok-tools skill_discovery_tracker --lib
cargo test -p xai-grok-tools implementations::skills --lib
cargo test -p xai-grok-shell build_skill_information_for_refs --lib
```

### Interjection

```bash
cargo test -p xai-interjection-core --lib
cargo test -p xai-grok-shell interjection --lib
```

### Request Context / Compaction

```bash
cargo test -p xai-chat-state build_request --lib
cargo test -p xai-grok-compaction code_compaction --lib
cargo test -p xai-chat-state compacted_history --lib
```

Shell crate 当前存在一个与本文无关的已知测试编译阻塞时，应优先运行较小 crate 的纯逻辑测试，并明确记录阻塞来源，不能把“未能编译整个 Shell 测试目标”写成本文逻辑失败。

---

## 133. 最终心智模型：Context 是一组有所有者的状态投影

可以把整个系统记成下面这张图：

```text
AgentDefinition + Config + Workspace discovery
                    │
                    ▼
            Agent Build Snapshot
        ┌───────────┼────────────┐
        ▼           ▼            ▼
  System Prompt  Project Rules  Skill Baseline
        │           │            │
        └──────┬────┴──────┬─────┘
               ▼           ▼
       Durable Conversation   Runtime Resources
               │           │
User/Tool/Interjection ─────┤
               │           │
               └─────┬─────┘
                     ▼
          Per-round Request Projection
     items + ToolSpec + HostedTools + Memory
                     │
                     ▼
                  Model
                     │
            Assistant / ToolCall
                     │
                     ▼
             ToolResult 再回到历史

Compaction = 从 Durable Conversation + Runtime Resources
             重建一个新的 Context Epoch
```

真正需要掌握的不是“有哪些字符串”，而是：

- 谁拥有这份数据；
- 它何时快照；
- 如何进入模型请求；
- 是否持久化；
- 怎样去重；
- Compaction 后由谁重建；
- fork/resume 时继承哪个版本。

---

## 134. Glossary：本文名词白话解释

| 名词 | 白话解释 | 本文中的具体含义 |
|---|---|---|
| Context | 模型作答时能看到的全部输入 | 历史消息、指令、工具定义、附件与动态状态 |
| Instruction | 告诉模型应该怎样行动的规则 | System、项目规则、Skill 正文、Runtime reminder |
| Prompt | 一次模型请求的输入 | 不只是用户问题，也包括历史和 Tool Schema |
| System Prompt | 最高层的 Agent 身份与通用规则 | Conversation 第一个 `System` Item |
| PromptContext | 渲染 System Prompt 的结构化输入快照 | 可序列化，但不是每轮历史权威 |
| Conversation | Session 内按顺序保存的消息历史 | `Vec<ConversationItem>` |
| ConversationItem | 一条结构化历史记录 | System、User、Assistant、ToolResult 等 enum variant |
| Conversation Head | Conversation 的第一项 | 正常应是权威 System Prompt |
| Context Window | 模型一次请求可处理的 token 上限 | 超过阈值会触发 pruning/compaction |
| Context Epoch | 两次整体上下文重建之间的时期 | Compaction 或 relocation 会开启新 epoch |
| Durable | 进入持久化历史，可在 Resume 后恢复 | Conversation 与 JSONL 中的消息 |
| Request-local | 只改变本次发送副本 | Tool Result prune、图片 eviction 等 |
| Projection | 从权威状态生成某个消费者所需视图 | Conversation → Request、Skills → Catalog |
| Snapshot | 某一时刻数据的固定副本 | Build 时的 PromptContext、Turn-level Tool Definitions |
| Reconciliation | 将多个 Skill 状态投影同步收敛 | runtime、reminder、slash commands 一起更新 |
| KV Cache | Provider 缓存稳定 Prompt 前缀的机制 | 频繁改 System/Prefix 会降低命中 |
| Prefix | System 后面的首条环境元数据 User Item | 工作区、日期、VCS、规则、Skill、MCP 等 |
| Deferred Prefix | 后台构建、首次 Prompt 前再注入的 Prefix | 延迟优化，失败会同步 fallback |
| AGENTS.md | 项目给 Agent 的约束文件 | 也泛指兼容规则文件集合 |
| ProjectInstructions | 项目规则的结构化 Synthetic User Item | `SyntheticReason::ProjectInstructions` |
| Rule Scope | 规则的来源/作用范围 | user scope 或 workspace scope |
| Compat | 对 Claude/Cursor 等兼容目录的开关 | 控制哪些规则和 Skill 路径参与发现 |
| Canonical Path | 解析 symlink、`..` 后的稳定路径 | 用于去重和安全边界判断 |
| Tracker | 记录已检查、已提醒状态的对象 | AgentsMdTracker、SkillManager |
| Catalog | 为选择而提供的简短目录 | Skill 名称、描述、触发条件和路径 |
| Skill | 可按需加载的任务流程包 | `SKILL.md` 加脚本、参考资料、资源 |
| Skill Body | `SKILL.md` frontmatter 后的完整正文 | 触发时才加载进模型上下文 |
| Frontmatter | Markdown 顶部 `---` 包围的 YAML metadata | name、description、paths、tools 等 |
| Baseline | Session 启动时的初始技能集合 | `startup_skills` |
| Dynamic Discovery | Tool 访问新路径后发现额外 Skill | 更新 SkillManager pending state |
| Conditional Skill | 只有命中特定文件路径才激活的 Skill | frontmatter 的 `paths:` gate |
| Slash Expansion | 把 `/skill args` 展开成完整正文 | 当前 Prompt 的 zero-round-trip 路径 |
| Zero-round-trip | 无需先让模型调用工具就获得 Skill 正文 | Shell 在 Prompt assembly 时直接加载 |
| Skill Tool | 模型主动加载 Skill 的工具路线 | 返回正文作为 Tool Result |
| Skill Information | User Query 后的 Skill instruction envelope | `<skill_information>` |
| Substitution | 把 Skill 正文变量换成实值 | `$ARGUMENTS`、`${SKILL_DIR}` 等 |
| Tool Definition | 工具的名称、描述和参数 Schema | Registry 输出的描述对象 |
| ToolSpec | 真正放入模型请求的 Tool Schema | `ConversationRequest.tools` 的元素 |
| Hosted Tool | Provider 服务器直接执行的工具 | 不走本地 Function Tool dispatch |
| Agentic Loop | 模型调用工具并继续采样的循环 | Response → Tool → Result → Resample |
| Round | Agentic Loop 中的一次模型采样 | 同一个 User Turn 可以有多轮 |
| ToolCall | Assistant 请求执行某个工具 | 带唯一 call id |
| ToolResult | 工具执行后回给模型的结构化结果 | 必须与 ToolCall ID 配对 |
| Synthetic User | 不是人直接输入、但使用 User role 的消息 | 由 `SyntheticReason` 进一步分类 |
| SyntheticReason | Synthetic User Item 的结构化来源标签 | Reminder、Interjection、AutoContinue 等 |
| System Reminder | 运行时追加的状态/控制提示 | 实际通常是 Synthetic User Item |
| Envelope | 用 XML-like tag 标记文本边界 | `<user_query>`、`<skill>` 等 |
| Interjection | 用户在 Turn 中途发出的改向消息 | 不取消 Turn，在安全点追加历史 |
| Safe Point | 可安全修改历史并重新采样的边界 | 采样前、Tool batch 后、Turn end 前 |
| In-flight Request | 已经发送、正在等待/流式接收的模型请求 | 不能被 Interjection 原地修改 |
| Fallback Prompt | 错过 Turn drain 的 Interjection 转成的新 Turn | 防止消息滞留或丢失 |
| User Echo | 客户端显示/持久化的用户输入 | 可能比模型实际输入更简洁 |
| Memory Reminder | 从跨 Session Memory 搜索出的相关上下文 | 可 upsert 进 System 或只改请求副本 |
| Upsert | 有则替换、无则插入 | 防止多个 Memory block 累积 |
| Pruning | 对旧而大的上下文做确定性缩减 | 主要针对 Tool Result |
| Soft Trim | 保留头尾、删除中间 | 较旧的大 Tool Result |
| Hard Clear | 用占位符替代完整正文 | 非常旧的 Tool Result |
| Image Eviction | 为请求体字节预算移除旧 inline image | 用明确“已不可见”占位符代替 |
| Compaction | 用摘要和确定性重播重建较短历史 | 一次 Context Epoch transition |
| Compaction Summary | LLM 对旧工作进展的概括 | 不能代替 System/规则/协议结构 |
| Recent Messages | 压缩时仍原样保留的尾部消息 | 保持最新 Tool 结构和细节 |
| Sanitize | 删除或修正不合法的压缩历史结构 | 例如 orphaned ToolResult |
| Runtime State | Conversation 之外的活跃状态 | tasks、subagents、TODO、MCP 等 |
| Resume | 从持久化 Session 继续运行 | 默认保留 stored System 和历史 |
| Rebuild | 重新创建 Agent 与 ToolBridge | 通常只在 zero-turn 安全边界完整执行 |
| System Override | Client 指定的新 System Prompt | 原子替换 Conversation Head |
| PromptAudience | Prompt 面向 Primary 还是 Subagent | 决定基础模板和 Catalog 抑制 |
| Subagent | 由父 Agent 派生的子执行单元 | 有独立 Session、Prompt 和 Tool scope |
| Verbatim Mirror Fork | 字节级继承父上下文的 fork | `preserve_inherited_system = true` |
| inherited_prefix_len | fork 中受保护前缀的长度 | Compaction 释放/保留继承上下文时使用 |
| CWD Relocation | Session 工作目录迁移 | 使用 generation 和 destination instructions |
| Generation | 一次状态迁移的单调身份编号 | 保证 CWD reminder 重试幂等 |
| Idempotent | 重复执行结果与执行一次相同 | Resume 注入和 relocation 重试都需要 |
| Dedup | 避免重复加入相同内容 | 项目指令、Skill listing、completion reminder |
| Prompt Cache Bust | 修改稳定前缀导致缓存失效 | System rewrite、Compaction 等会触发 |

---

## 135. 下一篇适合继续精读什么

下一篇可以继续研究：

> **源码精读 25：Agent Decision Runtime——模型如何从 Prompt 与 Tool Catalog 中选择行动，Tool Choice、Reasoning、Stop Condition、Stationarity、TodoGate、Hook Continuation 和 Goal Loop 又怎样共同决定“继续做还是结束”。**

它会把本篇的“模型看到了什么”继续推进到“模型与 Runtime 如何共同决定下一步”。
