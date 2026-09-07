# 05：上下文、持久化与恢复

## 先用三个比喻区分“历史”

- **Conversation history 像给模型的工作台**：只放下一步思考需要的材料。
- **Rollout 像施工日志**：记录任务过程中发生过的重要事件，便于恢复和审计。
- **Thread store / state DB 像档案索引**：回答有哪些任务、ID 是什么、文件放在哪里、是否归档。

同一件事可能同时影响三者，但三者不需要逐字相同。例如一段 20,000 行测试日志可以保存在执行记录中，却可能只以截断结果或摘要进入下一次模型请求。

## 1. 三份“历史”不是同一个东西

| 层 | 用途 | 典型处理 |
|---|---|---|
| Session conversation history | 构造下一次模型输入 | clone、normalize、compact |
| Rollout items | 记录执行过程并支持重建 | append、reconstruct、trace |
| Thread store / state DB | 查找、列出、元数据与持久索引 | start/resume/fork/archive |

它们会互相派生，但没有“永远逐字相同”的保证。

## 2. 模型上下文是增量构建的

`run_turn()` 首次捕获 step 后调用 `record_context_updates_and_set_reference_context_item()`；后续 step 调用 `record_step_world_state_if_changed()`。目标是：

- 第一次提供完整、可引用的运行环境；
- 后续只在 cwd、权限、时间、实时模式等变化时写入更新；
- 不频繁重写早期 history，减少 cache miss；
- compaction 后能重新建立必要的 context baseline。

`AGENTS.md`、环境、权限等不是随意拼接到一个字符串中，而是以有界的 contextual fragments 进入 history。

## 3. for_prompt 是边界，不是 getter

`ContextManager::for_prompt(input_modalities)` 会消费/规范化 history 的视图。它可能：

- 根据模型能力移除或转换不支持的媒体；
- 保持 tool call/output 的结构有效；
- 处理 synthetic output 的稳定 ID；
- 保持 inter-agent message 等特殊 item 的语义。

因此调试“模型为什么没看到某项”时，要同时检查：

1. 是否记录进 conversation history；
2. `for_prompt()` 是否保留；
3. `build_prompt()` 是否将对应字段送入请求；
4. transport 是否使用增量请求而非重发全部 input。

### 为什么不能直接返回 history

假设旧模型支持图片，新模型只支持文本。Session history 中仍有图片 item，但下一次请求不能原样发给只支持文本的模型。`for_prompt()` 必须根据本次模型能力生成一个合法视图。

再比如 history 中有 tool call 却缺少 output，模型 API 可能无法正确解释。规范化层需要维持调用与结果的结构，而不是把内部 vector 当成已经合法的网络 payload。

## 4. Compaction 是有语义的替换

Compaction 不是简单删除最旧 N 条。它需要在缩短上下文后保留继续完成任务所需的信息，并重新建立 world/context reference。`run_turn()` 既有 turn 前 compaction，也有 context limit 下的 mid-turn auto compaction。

需要特别验证：

- 当前用户任务是否仍存在；
- 未完成的 tool call/output 是否仍配对；
- 环境与权限上下文是否被重新注入；
- 模型切换或 comp hash 变化是否触发正确路径。

可以把 compaction 理解成“给接班工程师写交接摘要”，不是“把最老的聊天记录随便删掉”。好的摘要应该保留：目标、已经尝试的方案、当前修改、未解决问题和必要环境；大量重复日志则可以省略。

## 5. Resume 是重建，不是反序列化一个 Session

恢复 thread 时，`ThreadManager` 从持久层获取记录，转换为 initial history，再创建新的运行期 session/service 组合。运行期对象（channels、HTTP client、cancellation token、MCP live binding）不能直接从旧进程复活。

Fork 则需要选择历史截点、构造 fork history，并处理被截断时尚未完成的 turn 边界。

恢复应用的例子：昨天 Codex 已经修改文件并运行过一次失败测试。今天 resume 时可以重建这些对话事实，但昨天的 HTTP connection、运行中的 shell PID 和 cancellation token 已经不存在，必须创建新的 runtime 对象。

## 6. WebSocket 增量请求与 history

`ModelClientSession` 会检查新 request 是否是上一 request 加 server output 的严格扩展；只有非 input 属性保持一致、前缀匹配时，才使用增量 input delta。否则回退到完整请求。

这说明“不要重写历史”不仅是概念整洁，也直接影响 prompt caching 和传输复用。

## 理解检查

- 为什么完整 rollout 不适合每次全部发给模型？
- Resume 后为什么不能继续使用旧进程里的 shell PID？
- Compaction 与简单删除前 N 条消息的根本差别是什么？

## 本章词汇表

| 词语 | 直译 | 在状态与恢复中的意思 |
|---|---|---|
| History | 历史 | 规范化后可用于构造模型输入的 conversation items |
| Rollout | 运行轨迹 | 为恢复和审计持久保存的事件记录 |
| Thread store | Thread 存储 | 索引并加载持久 thread 状态的存储层 |
| Compaction | 压缩 | 用有语义的短表示替换过长上下文 |
| Resume | 恢复 | 从持久记录创建新的运行对象继续工作 |
| Baseline | 基线 | 后续 world-state diff 比较所依赖的起点 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

- 阅读 `context_manager/history.rs::for_prompt()` 及其测试名，先从测试理解规范化契约。
- 阅读 `session/mod.rs::record_context_updates_and_set_reference_context_item()`。
- 阅读 `client.rs::get_incremental_items()`，列出增量复用失败的条件。
- 阅读 `core/tests/suite/resume.rs` 和 `fork_thread.rs`，区分 resume 与 fork 的行为承诺。
