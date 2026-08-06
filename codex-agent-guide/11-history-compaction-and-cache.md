# 11. History、Compaction 与 Prompt Cache

## 1. History 是 Agent 的工作记忆

[`ContextManager`](../codex-rs/core/src/context_manager/history.rs) 保存模型下一次请求可以看到的结构化历史。它包含：

- `Vec<ResponseItem>`；
- history version；
- token usage；
- reference turn context；
- World State baseline。

这里的 History 不等于 UI transcript，也不等于完整 rollout。它只保存模型继续推理所需的项目。

## 2. History 中有什么

常见 `ResponseItem`：

- developer/user/assistant message；
- reasoning item；
- function/custom tool call；
-对应 call output；
-client/server tool search item；
-web search、image generation；
-compaction item。

流式 delta、某些 UI lifecycle event 和内部 telemetry 不直接成为 History item。

## 3. 记录时截断

Tool Result 可能非常大。`record_items()` 会按模型的 truncation policy 限制 function/custom tool output。提前在写入 History 时做有界化，能避免每次 `for_prompt()` 都处理无限增长内容。

截断后应保留：

-结果是否成功；
-前后关键片段；
-明确的截断说明；
-继续读取结果的方式（如果工具支持）。

## 4. `for_prompt()` 规范化

发送模型前，History 会执行规范化：

-缺少 output 的 Tool Call 补合成输出；
-孤立 output 被移除；
-不支持图片的模型移除或降级图片；
-不支持音频的模型移除音频；
-为合成 item 分配稳定 ID；
-保持原始顺序和 call/result 配对。

为什么要补缺失 output？Responses 协议通常要求 Tool Call 与 Tool Result 成对。中断、rollback 或旧 rollout 可能留下不完整对，规范化使下一次请求仍合法。

## 5. Append 与 Rewrite

正常对话只 append，新消息不会修改过去 History。这有利于：

-审计；
-恢复；
-Prompt Cache；
-增量传输。

只有 compaction、rollback、修复旧媒体等操作才会 rewrite，并推进 history version。传输层可以据此判断旧增量 session 是否还能复用。

## 6. 为什么需要 Compaction

上下文窗口有限，而 Coding Agent 的历史增长很快：

-每次命令都有输出；
-工具 schema 占空间；
-Skill 与环境上下文可能很长；
-一个用户 Turn 内就可能采样十几次。

Compaction 把较早的详细交互压缩成保留任务状态的摘要，并创建新的 context window。

## 7. 本地与远程 Compaction

Provider 能力决定使用本地 compact prompt 还是远程 compact endpoint。两条路径最终都要：

1.构建 compact 输入；
2.获得摘要或 replacement history；
3.过滤不应保留的 item；
4.注入必要初始 context；
5.安装新 History；
6.建立新 World State baseline；
7.记录 checkpoint/window metadata。

主要源码：

- [`compact.rs`](../codex-rs/core/src/compact.rs)
- [`compact_remote.rs`](../codex-rs/core/src/compact_remote.rs)

## 8. Pre-turn 与 Mid-turn Compaction

两者顺序不同：

### Pre-turn

在新用户输入前压缩旧历史。之后正常注入本轮 context 和用户消息。

### Mid-turn

Agent 已经开始执行，Tool follow-up 尚未完成。Replacement history 必须保留当前任务的继续语义，并把初始 context 插入到正确位置，使 compaction item 或 summary 保持模型训练所期望的末尾结构。

这就是为什么 compaction 不能简单写成“对所有消息做摘要然后清空数组”。

## 9. 新窗口与 World State

旧 History 被替换后，旧 World State diff 可能已经消失。Session 必须为新窗口安装完整 baseline，并把它持久化。否则后续只发送 diff 时，模型会缺少 diff 的起点。

## 10. Prompt Cache

服务端缓存通常依赖稳定前缀。Codex 的相关设计包括：

-Base Instructions 尽量稳定；
-正常 History append-only；
-World State 使用增量消息；
-Tool 顺序保持确定性；
-使用 prompt cache key；
-模型切换用增量上下文说明，而不是任意重写历史。

缓存优化不能破坏语义。状态真实变化时仍必须告诉模型，即使这会造成部分 cache miss。

## 11. Rollback

Rollback 不只删除最后 N 个字符串。它需要考虑：

-Tool Call/Result 配对；
-contextual developer/user fragments；
-reference context item；
-World State baseline；
-History version；
-持久化记录。

正确做法是以结构化 item 边界回退，再运行 normalize。

## 12. 摘要应保留什么

一个 Agent compaction summary 至少应保留：

-用户目标和尚未完成的要求；
-已做出的重要决定；
-修改过的文件；
-测试结果；
-重要错误和排除过的假设；
-当前计划和下一步；
-不可重复的外部副作用；
-仍有效的约束。

不必保留每条搜索输出的逐字内容，但不能把“测试失败”压缩成“运行过测试”。

## 13. 设计建议

-History 始终结构化；
-Call 与 Result 通过 ID 关联；
-append 和 rewrite 使用不同版本语义；
-每种自动注入都有预算；
-compact 是显式 checkpoint；
-摘要质量需要集成测试；
-compact 后重新建立动态状态 baseline；
-长线程应允许用户主动新建线程，而不是无限摘要。

