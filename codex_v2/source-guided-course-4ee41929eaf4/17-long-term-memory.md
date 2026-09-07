# 17：长期 Memory

## 先分清 History 和 Memory

假设你告诉 Codex：“这个仓库提交前必须运行 `just fmt`。”

- 在当前 thread 中继续工作时，这条信息主要依靠 **conversation history**；
- 几周后新建另一个 thread，系统若仍能按需找到这条经验，依靠的是 **长期 Memory**。

History 像当前会议的逐轮记录；Memory 像会后整理进知识库的长期经验。不能把所有旧对话原封不动塞进每次 prompt，否则上下文会迅速爆炸，而且大量内容与当前任务无关。

## 1. 当前实现采用“写入”和“读取”分离

```text
旧 rollout
  → Phase 1：逐条提取可复用信息
  → State DB 中的结构化 memory records
  → Phase 2：全局筛选和整合
  → MEMORY.md / memory_summary.md / rollout_summaries
  → 新 thread 按需搜索、读取并引用
```

写入链路负责把经历变成资料；读取链路负责在未来任务中用有限上下文找到相关资料。

## 2. 为什么不是每次对话结束就同步整理

Memory pipeline 在符合条件的根 Session 启动后异步运行，并要求例如：不是 ephemeral session、Memory Feature 已启用、不是子 Agent、State DB 可用。

后台运行的好处是不会让当前用户等待完整整理。但这也意味着 Memory 是“稍后提炼出的知识”，不是完成当前任务的同步事务保证。

## 3. Phase 1：从单条 rollout 提取候选记忆

Phase 1 会从 State DB 领取有界数量的合格 rollout，过滤出与记忆有关的内容，再让模型产生结构化结果，例如详细 raw memory、简短 rollout summary 和可选 slug。

领取前使用 claim/lease 很重要。假设两个 Codex 进程同时启动，如果都扫描到同一条旧 rollout 并开始处理，就会浪费模型调用并产生竞争。Lease 表示“这项工作暂时由我负责”，失败后再按 backoff 重试。

并行同样必须有上限：旧 rollout 可能很多，启动一次应用不能无界创建模型请求。

## 4. Phase 2：把很多候选整理成可使用的知识库

Phase 2 不是简单拼接所有 Phase 1 输出。它会：

- 获取全局 Phase 2 锁，避免多个进程同时改 Memory workspace；
- 按使用次数和最近使用/生成时间选择有界输入；
- 同步 `raw_memories.md` 和 `rollout_summaries/`；
- 计算相对上次成功基线的 workspace diff；
- 必要时启动受限的内部 consolidation Agent；
- 成功后更新 Git baseline 和 State DB 状态。

Git baseline 在这里不是为了让用户提交代码，而是为了明确告诉整合 Agent：“从上次成功整理后，哪些记忆新增、变化或删除了。”

## 5. 为什么整合 Agent 权限非常受限

Memory 内容来自旧对话，本质上仍是不可信文本。整合 Agent 只需要整理本地 Memory workspace，因此当前设计限制网络、审批和协作能力，避免旧内容诱导它访问无关系统或递归创建 Agent。

长期保存某段文字，不会让这段文字自动升级成高优先级系统指令。

## 6. 读取时为什么只先放摘要

新 thread 启动时，如果把完整 `MEMORY.md` 和所有 rollout summary 都放进 prompt，会造成：

- 大量 token 占用；
- 与当前任务无关的信息干扰；
- prompt cache 前缀频繁变化；
- Memory 规模无法设上限。

因此读取 Extension 更适合先注入有界摘要和“如何搜索/读取”的说明，再通过专用工具按路径或关键词获取细节。

这与 Skill catalog 的设计相似：**先给目录，再按需展开正文。**

## 7. Memory citation 有什么作用

模型使用 Memory 时，应能保留来源关联，而不是把长期记忆当作凭空事实。Citation 可以关联到原 rollout/thread，并用于记录哪些 Memory 被真正使用。

使用统计又会反过来帮助 Phase 2 排序：经常使用、最近使用的内容通常比长期无人读取的内容更值得保留在有限集合中。

## 8. 一次完整例子

旧 thread 中，Codex 发现项目测试必须设置特殊环境参数。Phase 1 提取出这条可复用经验；Phase 2 把它整理进项目相关 Memory。

后来新 thread 收到“为什么测试在 CI 失败”：

1. Prompt 先看到 Memory 摘要和读取方法；
2. 模型搜索与项目测试相关的 Memory；
3. 读取具体条目并检查当前源码；
4. 将 Memory 当作调查线索，而不是未经验证的最终答案；
5. 回答中保留引用关系，系统记录此次使用。

## 常见误解

- **“Memory 是所有历史消息的永久副本。”** 它是经过提取、筛选和整合的知识。
- **“写进 Memory 的内容以后一定正确。”** 仓库会变化，Memory 应作为可追溯线索并重新验证。
- **“Memory 越多越好。”** 无界 Memory 会损害选择质量、上下文和性能。
- **“子 Agent 也应各自启动整理流水线。”** 当前入口刻意避免递归后台整理。

## 读完后自测

1. 为什么需要 Phase 1 和 Phase 2，而不是一次模型调用完成所有整理？
2. Claim/lease 解决了什么并发问题？
3. 为什么只把摘要和读取方法放进初始 prompt？

## 本章词汇表

| 词语 | 直译 | 在长期 Memory 中的意思 |
|---|---|---|
| Extraction | 提取 | 从单条 rollout 中生成可复用的结构化记忆 |
| Consolidation | 整合 | 将多条候选记忆筛选并整理成长期资料 |
| Claim | 领取 | 原子取得一项 Memory 后台工作的处理权 |
| Lease | 租约 | 在限定时间内声明工作由当前 worker 负责 |
| Watermark | 水位线 | 记录后台流水线已稳定处理到哪个时间/位置 |
| Citation | 引用 | 把使用的 Memory 关联回来源 rollout/thread |
| Backoff | 退避 | 失败后延迟再次尝试，避免不断立即重试 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

- `codex-rs/memories/README.md`：当前两阶段语义；
- `codex-rs/memories/write/src/phase1.rs`：单 rollout 提取；
- `codex-rs/memories/write/src/phase2.rs`：全局整合；
- `codex-rs/memories/write/src/workspace.rs`：Git baseline 与 diff；
- `codex-rs/ext/memories/src/extension.rs`：读取 Extension；
- `codex-rs/ext/memories/src/tools/`：搜索与读取工具；
- `codex-rs/memories/read/src/citations.rs`：引用解析。
