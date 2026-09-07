# 23. 长期记忆：从会话提取到按需读取

> 本章讨论的“记忆”不是当前 Thread 的 `History`。`History` 服务于正在进行的推理；长期记忆把已结束或空闲的 rollout 提炼成可跨 Thread 使用的知识。

## 1. 先建立正确模型

Codex 的长期记忆不是一个不断增长、每轮完整注入的字符串，而是两条相互解耦的链路：

```text
写链路：rollout -> Phase 1 提取 -> DB 记录 -> Phase 2 整合 -> 文件化记忆
读链路：memory_summary.md -> 小型路由提示 -> list/search/read -> 按需读取细节
```

这样设计同时解决三个问题：

1. 原始会话太长，不能无限注入上下文；
2. 记忆生成较慢，不应阻塞用户当前 Turn；
3. 大多数任务只需要少量相关记忆，不需要把全部历史交给模型。

主要代码分布：

- `codex-rs/memories/write/`：写入、提取与整合；
- `codex-rs/ext/memories/`：Extension 形式的读取工具；
- `codex-rs/memories/read/`：读取路径、引用和使用统计；
- `codex-rs/state/`：Phase 1/2 的持久状态和租约；
- `codex-rs/protocol/src/protocol.rs`：`ThreadMemoryMode` 等协议类型。

## 2. 启动条件

`codex-rs/memories/README.md` 给出了写流水线的入口条件。它在根 Session 启动后异步触发，并要求：

- Session 不是 ephemeral；
- 记忆生成 Feature 已启用；
- 当前 Session 不是子 Agent；
- State DB 可用。

这些条件体现了一个通用原则：后台学习任务不应污染临时会话，也不应在子 Agent 中递归启动。

## 3. Phase 1：逐个 rollout 提取

入口是 `codex-rs/memories/write/src/phase1.rs::run`。代码注释已经明确了严格顺序：

```rust
// 1) claim eligible rollout jobs
// 2) build one stage-1 request context
// 3) run stage-1 extraction jobs in parallel
// 4) emit metrics and logs
```

### 3.1 为什么先 claim

`claim_startup_jobs` 调用 State DB 的 `claim_stage1_jobs_for_startup`，传入：

- 扫描上限；
- 单次最大领取数；
- rollout 最大年龄；
- 最短空闲时间；
- 允许的交互 Session 来源；
- lease 时长。

“先领取再执行”使多个 Codex 实例同时启动时不会重复总结同一 rollout。Lease 还能处理进程崩溃：所有权过期后，任务可以再次被领取。

### 3.2 提取请求

Phase 1 使用单独的模型请求上下文，而不是把任务塞回当前对话。模型默认值来自 Provider 的 `memory_extraction_preferred_model()`，用户也可通过 `memories.extract_model` 覆盖。

输出被 JSON Schema 约束为：

```json
{
  "rollout_summary": "用于路由的短摘要",
  "rollout_slug": "可选文件名标识",
  "raw_memory": "详细 Markdown 记忆"
}
```

对应 `StageOneOutput` 使用 `#[serde(deny_unknown_fields)]`，Schema 也设置 `additionalProperties: false`。Agent 实现中，这种“模型输出 + 严格 Schema + 强类型反序列化”的组合，比靠自然语言约定可靠。

### 3.3 并行但有界

`run_jobs` 使用：

```rust
futures::stream::iter(claimed_candidates)
    .map(...)
    .buffer_unordered(crate::stage_one::CONCURRENCY_LIMIT)
```

因此多个 rollout 可以并发提取，但并发度有硬上限。结果分为：

- `SucceededWithOutput`；
- `SucceededNoOutput`；
- `Failed`。

失败任务通过数据库退避重试，而不是在当前启动过程中热循环。生成字段还会经过 secret redaction，避免长期文件固化凭据。

## 4. Phase 2：全局整合

入口是 `codex-rs/memories/write/src/phase2.rs::run`。它把多个 Phase 1 结果整合成适合人和 Agent 阅读的文件层级。

### 4.1 严格执行顺序

代码把流程分为十步：

1. 领取全局 Phase 2 锁；
2. 准备记忆目录的 Git baseline；
3. 构造锁定权限的整合 Agent 配置；
4. 从 DB 选择当前输入；
5. 同步输入到记忆 workspace；
6. 用 Git diff 判断是否真正变化；
7. 写入 `phase2_workspace_diff.md`；
8. 启动整合子 Agent；
9. 异步处理心跳、完成状态和 baseline 重置；
10. 记录指标。

Phase 2 是全局串行的，因为多个整合器同时修改一套共享文件很难安全合并；Phase 1 则天然可以按 rollout 并行。

### 4.2 输入选择不是“最近 N 条”这么简单

选择逻辑考虑：

- `max_raw_memories_for_consolidation` 上限；
- `max_unused_days` 保留窗口；
- 使用次数；
- 最近使用时间；
- 没有使用记录时退回生成时间。

这意味着长期有用的记忆可以因持续被引用而保留，陈旧且从未再用的内容会逐步退出。

### 4.3 文件层级

同步和整合后的核心文件包括：

```text
~/.codex/memories/
├── memory_summary.md       # 很短的全局路由摘要
├── MEMORY.md              # 可搜索的主题/项目级路由层
├── raw_memories.md         # Phase 1 原始记忆集合
├── rollout_summaries/      # 每个 rollout 的摘要
├── skills/                 # 可沉淀出的程序化知识
└── extensions/             # 扩展拥有的记忆资源
```

`raw_memories.md` 采用稳定的 Thread ID 升序输出，避免“使用排名变化”导致无意义的大 diff。

### 4.4 为什么用 Git baseline

Phase 2 不是根据“水位是否变化”直接决定要不要运行 Agent，而是先同步当前选择，再查看 workspace 相对上次成功 baseline 的 Git diff。

好处是新增、修改、删除都能准确显现。`phase2_workspace_diff.md` 把变化显式交给整合 Agent，使它无需猜测哪些输入是新的。

### 4.5 整合 Agent 的权限

`phase2.rs` 中构造的 Agent：

- 无需 approval；
- 禁止网络；
- 只获得记忆根目录的本地写权限；
- 禁用协作，避免再次生成子 Agent；
- 关闭自身的记忆生成和读取，避免递归。

这是“内部 Agent 也必须最小权限”的具体实现。任务可信不代表应给予完整宿主权限。

## 5. 读链路：Extension 注入

`codex-rs/ext/memories/src/extension.rs` 定义 `MemoriesExtension`，同时实现四类 contributor：

- `ThreadLifecycleContributor<Config>`：Thread 启动时保存配置；
- `ConfigContributor<Config>`：配置变化时更新；
- `ContextContributor`：提供 Prompt 片段；
- `ToolContributor`：提供专用记忆工具。

`install` 把同一个 Extension 注册到四条扩展通道，避免 Core 为记忆功能写特殊分支。

启用条件由 `MemoriesExtensionConfig::from_config` 计算：

```rust
enabled: config.features.enabled(Feature::MemoryTool)
    && config.memories.use_memories
```

专用工具还受 `config.memories.dedicated_tools` 控制。

## 6. Prompt 中只放摘要和读取方法

`build_memory_tool_developer_instructions`：

1. 读取 `~/.codex/memories/memory_summary.md`；
2. 去除首尾空白；
3. 按固定 Token 上限截断；
4. 渲染 `templates/memories/read_path.md`；
5. 作为 developer policy `PromptFragment` 注入。

所以模型最先看到的是“有什么值得查”和“如何查”，而不是所有原始记忆。这就是 progressive disclosure。

## 7. 专用工具与 Backend

`MemoriesBackend` trait 把存储实现与模型工具解耦，暴露：

```rust
add_ad_hoc_note(...)
list(...)
read(...)
search(...)
```

当前 `LocalMemoriesBackend` 使用文件系统，未来可以在不改变工具协议的情况下替换远程存储。

工具输入输出是强类型的，并有明确边界：

- `list` 支持 path、cursor、max_results；
- `read` 支持 1-based line offset、最大行数和最大 Token；
- `search` 支持 any、同一行全部命中、指定行窗口内全部命中；
- 响应都包含 `truncated` 或 `next_cursor`。

这些上限不是 API 装饰，而是防止单次工具输出无界进入模型上下文。

## 8. ThreadMemoryMode 与污染

协议层提供 `ThreadMemoryMode::Enabled/Disabled`，可通过 `SetThreadMemoryMode` 更新。此外，当 Thread 引入 MCP、Web Search 等外部上下文时，Core 会调用 `mark_thread_memory_mode_polluted_if_external_context` 标记污染。

这里的“污染”不是说外部内容有害，而是说明从该 Thread 提取出的偏好或事实可能来自临时外部材料，不应无条件当作用户的稳定长期知识。

## 9. 一次完整例子

假设用户在 Thread A 中要求修复某仓库的构建命令，并说明“这个项目永远使用 `just test`”。

1. Thread A 的 rollout 持久化；
2. 后续根 Session 启动，Phase 1 领取 A；
3. 模型生成详细 `raw_memory` 和短 `rollout_summary`；
4. Secret redaction 后写入 State DB；
5. Phase 2 选择它，更新 `raw_memories.md` 和 rollout 摘要；
6. 整合 Agent 把稳定项目规则写入 `MEMORY.md`，在 `memory_summary.md` 留一个短路由；
7. Thread B 启动，只注入短路由；
8. 当新任务涉及该项目时，模型使用 `search` 找到规则，再用 `read` 读取必要段落；
9. 记忆工具的使用被记录，影响以后保留和排序。

## 10. 实现自己的 Agent 时应复用的模式

- 短期上下文与长期记忆分开建模；
- 写入异步化，不阻塞当前用户请求；
- 原始提取可并行，全局整合要串行；
- 用租约和幂等状态处理崩溃恢复；
- 模型产物必须经过 Schema 校验和敏感信息清理；
- Prompt 只注入有界索引，细节通过工具按需获取；
- 内部维护 Agent 也使用最小权限；
- 记录记忆是否被真正使用，用于淘汰和排序；
- 区分用户稳定事实与来自外部上下文的临时信息。

## 11. 推荐阅读顺序

1. `codex-rs/memories/README.md`：整体语义；
2. `codex-rs/memories/write/src/phase1.rs`：逐 rollout 提取；
3. `codex-rs/memories/write/src/phase2.rs`：全局整合；
4. `codex-rs/memories/write/src/storage.rs`：稳定文件布局；
5. `codex-rs/ext/memories/src/extension.rs`：读路径的注册；
6. `codex-rs/ext/memories/src/prompts.rs`：摘要如何进入 Prompt；
7. `codex-rs/ext/memories/src/backend.rs`：工具契约和边界。

