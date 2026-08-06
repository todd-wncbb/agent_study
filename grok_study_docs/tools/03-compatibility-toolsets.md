# Codex、OpenCode、Concise 与 Hashline Tool 实现

这些 Tool 并不是重复造轮子。它们让同一个 runtime 以不同模型熟悉的名称、参数 Schema、
输出格式和编辑协议暴露能力。选择发生在 Agent toolset preset，而不是运行时让模型自由切换
namespace。

## 一、Codex Toolset

Codex preset 组合标准 GrokBuild Bash/任务辅助工具与以下 4 个 Codex 文件 Tool。

### 1. `Codex:apply_patch`

- Args：`ApplyPatchInput`，核心字段为 Codex Patch DSL 文本。
- Output：`ApplyPatchOutput`，包含每个文件的 action、old/new text、move target 和 Prompt 文本。
- Kind/Scope：Edit / Write。
- 入口：
  [`codex/apply_patch/tool.rs`](../../crates/codegen/xai-grok-tools/src/implementations/codex/apply_patch/tool.rs)

实现分层：

```text
tool.rs       Tool contract、Resources、锁、I/O、通知
parser.rs     *** Begin Patch / Update File / Add File / Delete File DSL
seek_sequence.rs  在文件中定位上下文序列和容错匹配
apply.rs      纯函数：把 Patch 应用到旧文本
errors.rs     Parse/Apply 错误分类
```

Tool 先完整解析所有操作，再解析和校验目标路径，使用共享 file-operation lock 串行化重叠
文件修改，读取旧内容、计算新内容，最后执行写入/移动/删除并汇总结果。多文件 Patch 的返回值
被 `SkillDiscoveryReminder` 用来激活所有被触及路径对应的条件 Skill。

设计重点是“验证后应用”和可解释的上下文不匹配错误，而不是把 Patch 交给 Shell 命令执行。

### 2. `Codex:list_dir`

- Args：目录路径、depth、offset、limit 等分页参数。
- Output：统一 `ListDirOutput`。
- Kind/Scope：ListDir / Read。
- 入口：
  [`codex/list_dir/tool.rs`](../../crates/codegen/xai-grok-tools/src/implementations/codex/list_dir/tool.rs)

采用 BFS 遍历，支持深度限制和分页；路径先经过 model-path 到真实 workspace path 的转换，
输出再使用 display cwd。与 GrokBuild `list_dir` 的差异主要是 Codex 兼容格式、分页和显式深度，
而非“大目录自动按扩展名汇总”的 GrokBuild 表达。

### 3. `Codex:grep_files`

- Args：regex pattern、search path、可选 glob/limit。
- Output：`CodexGrepFilesOutput`。
- Kind/Scope：Grep / Read。
- 入口：
  [`codex/grep_files/tool.rs`](../../crates/codegen/xai-grok-tools/src/implementations/codex/grep_files/tool.rs)

底层运行 ripgrep，但目标是“找哪些文件匹配”，而不是返回所有匹配行。实现校验 regex、解析
路径、构造 `rg --files-with-matches` 类参数，限制输出数量，并返回稳定、模型可消费的路径列表。

### 4. `Codex:read_file`

- Args：`file_path`、`offset`、`limit`、`mode`、`indentation?`。
- Output：`ReadFileOutput`。
- Kind/Scope：Read / Read。
- 入口：
  [`codex/read_file/tool.rs`](../../crates/codegen/xai-grok-tools/src/implementations/codex/read_file/tool.rs)

两种模式：

- `slice`：从 1-based offset 返回最多 limit 行。
- `indentation`：以 anchor line 为中心，根据缩进选择包含父级、header 和可选 siblings 的语义块。

Indentation 配置包括 `anchor_line`、`max_levels`、`include_siblings`、`include_header`、
`max_lines`。返回格式使用 Codex 熟悉的 `L{n}: content`。主要算法拆在
`indentation.rs`、`slice.rs` 和 `text_utils.rs`。

## 二、OpenCode Toolset

OpenCode 模块来自 `sst/opencode` 风格的接口适配：
[`opencode/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/mod.rs)。

### 5. `OpenCode:bash`

- Args：`command`、`timeout?`、`workdir?`、description 等 OpenCode 命名字段。
- Output：`BashToolOutput`。
- Kind：Execute。
- 源码：
  [`opencode/bash/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/bash/mod.rs)

它把 OpenCode 协议转成共享 Terminal request，支持 timeout、工作目录检查、输出截断和进程
结果。description 强调每次调用的 Shell 状态语义；底层仍是 grok-build 的 TerminalBackend，
不是启动一个 OpenCode 进程。

### 6. `OpenCode:read`

- Args：`filePath`、`offset?`、`limit?`。
- Output：`ReadFileOutput`。
- Kind：Read。
- 源码：
  [`opencode/read/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/read/mod.rs)

既能读文件也能读目录，并处理图片、PDF 等内容类型。输入通过 `ToolInput::Dynamic` 做类型擦除
适配；路径、offset/limit 归一化后复用共享读取能力，最终转换为 OpenCode 的结果格式。

### 7. `OpenCode:edit`

- Args：`filePath`、`oldString`、`newString`、`replaceAll?`。
- Output：`SearchReplaceOutput`。
- Kind：Edit。
- 源码：
  [`opencode/edit/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/edit/mod.rs)

这是 OpenCode 参数约定下的精确替换。它复用 SearchReplace 的匹配和写入语义，包括零匹配、
多匹配、replace-all、路径校验和编辑结果，但保持模型训练时熟悉的 camelCase Schema。

### 8. `OpenCode:write`

- Args：`{ file_path, content }`。
- Output：类型别名到 `SearchReplaceOutput`。
- Kind：Write。
- Notification：`FileWritten`。
- 源码：
  [`opencode/write/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/write/mod.rs)

执行整体覆盖或创建文件。它通过 AsyncFileSystem 和文件操作锁写入，返回与编辑工具兼容的结果，
并发送 FileWritten 通知。与 `edit` 最大差异是无需旧文本前置条件，因此误覆盖风险更高。

### 9. `OpenCode:grep`

- Args：pattern、path?、include? 等 OpenCode 搜索字段。
- Output：`GrepSearchOutput`。
- Kind：Grep。
- 源码：
  [`opencode/grep/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/grep/mod.rs)

构造 ripgrep 调用并转换成 OpenCode 排序、截断和显示格式。与 GrokBuild grep 相比，主要差异
在字段命名和 Prompt output，而不是搜索引擎。

### 10. `OpenCode:glob`

- Args：`{ pattern, path? }`。
- Output：`GlobOutput { entries, count, total_count, truncated, cwd_for_display }`。
- Kind：Glob。
- 源码：
  [`opencode/glob/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/glob/mod.rs)

用文件枚举/匹配能力找到符合 glob 的文件，按修改时间倒序排列并施加数量、字节预算。Output
同时保存结构化绝对路径和预格式化 Prompt 文本；`cwd_for_display` 防止 overlay/worktree 下
调用方自行重新推导展示根目录而出错。

### 11. `OpenCode:todowrite`

- Args：`{ todos: OpenCodeTodoItem[] }`，数组是完整新状态。
- Output：`TodoWriteOutput`。
- Kind：TodoWrite。
- 源码：
  [`opencode/todowrite/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/todowrite/mod.rs)

它把 OpenCode 的 status/priority 字符串映射到内部 Todo 类型，并整体替换当前列表。未知 status
回退 Pending，未知 priority 回退 Medium。与 GrokBuild `todo_write` 的 merge 模式不同，调用方
必须始终传完整列表。

### 12. `OpenCode:skill`

- Args：`{ name }`。
- Output：`SkillOutput`。
- Kind：Skill。
- 源码：
  [`opencode/skill/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/skill/mod.rs)

从 `AvailableSkills` 精确查找 bare 或 qualified name；短名称多匹配时返回候选限定名，不静默
选择。成功后读取 `SKILL.md`、去掉 frontmatter、枚举同目录最多 10 个资源，返回
`<skill_content>`。默认 GrokBuild 没有这个 Tool，模型通过 `read_file` 加载 Skill；完整比较见
[Skill 文档](04-skill-implementation.md)。

## 三、GrokBuildConcise Toolset

Concise namespace 只定义 3 个核心变体：

### 13. `GrokBuildConcise:run_terminal_cmd`

Args/Output 与 GrokBuild Bash 相同。wrapper 使用更短 description 和更紧凑的模型结果，执行
仍委托共享 Bash 核心。源码：
[`grok_build_concise/bash.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build_concise/bash.rs)。

### 14. `GrokBuildConcise:read_file`

Args/Output 与标准 ReadFile 相同，只改变 contract/呈现。源码：
[`grok_build_concise/read_file.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build_concise/read_file.rs)。

### 15. `GrokBuildConcise:search_replace`

Args/Output 与标准 SearchReplace 相同，只改变 contract/呈现。源码：
[`grok_build_concise/search_replace.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build_concise/search_replace.rs)。

Concise 模式的一个跨层限制是关闭 system reminders 后，当前 V1 的动态 Skill discovery 也不会
运行，因为发现逻辑暂时挂在 Reminder 生命周期上。

## 四、GrokBuildHashline Toolset

Hashline 的目标是让模型编辑它真正读过的文件版本，降低纯字符串替换因文件变化而误改的风险。

### 16. `GrokBuildHashline:hashline_read`

- Args：与 ReadFile 的目标、范围参数兼容。
- Output：`ReadFileOutput`，文本行附带 hash anchor。
- Params：`HashlineSchemeParams`。
- 源码：
  [`grok_build_hashline/read_file.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build_hashline/read_file.rs)

读取后按 scheme 为行生成短、稳定 anchor。scheme 控制 salt/宽度/格式等细节；输出 anchor
是后续 edit 的乐观并发前置条件。

### 17. `GrokBuildHashline:hashline_grep`

- Args：`GrepSearchInput`。
- Output：`GrepSearchOutput`，匹配行带 anchor。
- 源码：
  [`grok_build_hashline/grep.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build_hashline/grep.rs)

搜索本身仍基于 Grep/ripgrep，但会读取匹配上下文并使用同一 scheme 编码 anchor，使模型可以
从搜索结果直接构造安全编辑。

### 18. `GrokBuildHashline:hashline_edit`

- Args：`{ file_path, edits: HashlineOp[] }`。
- Output：`SearchReplaceOutput`。
- 源码：
  [`grok_build_hashline/edit/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build_hashline/edit/mod.rs)

`edits` 容错接受数组、单个对象或被错误 double-encode 的 JSON 字符串。实现流程：

```text
读取当前文件
→ 解析每个 anchor/range
→ 重新计算当前行 hash
→ 拒绝 stale/mismatch anchor
→ 验证 edits 不冲突
→ 自底向上应用，避免前一个 edit 改变后续行偏移
→ 原子写入并返回 Patch/上下文
```

`anchor.rs` 定义 anchor，`scheme.rs` 负责编码/验证，`range_policy.rs` 处理范围语义，
`edit/apply.rs` 完成纯文本变换，`mutate.rs` 组合文件 I/O。

## 五、怎么选

| Toolset | 更适合 |
| --- | --- |
| GrokBuild | 默认产品行为，最完整的媒体、任务、Scheduler、MCP 能力 |
| Codex | 模型熟悉 Codex Patch 和 indentation read 协议 |
| OpenCode | 模型熟悉 `read/edit/write/glob/bash/skill` Schema |
| Concise | 更小 Tool descriptions 和输出；接受 Reminder 能力减少 |
| Hashline | 强调读写一致性、anchor-based 乐观并发控制 |

它们不是按功能数量简单排序；关键是给当前模型选择其训练分布最熟悉、且满足产品安全边界的
协议。

