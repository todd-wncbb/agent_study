# 19：Token 预算与性能

## 先纠正两个直觉

第一，模型的上下文窗口不是“聊天消息条数”，而是 instructions、history、工具定义、工具输出、图片等共同占用的 token 空间。

第二，一次 Turn 很慢不一定是模型慢。时间可能花在采样前准备、模型首 token、工具阻塞、compaction、重试或等待输入上。

所以 Token 和性能都需要分层测量，不能只看一个总数字。

## 1. 上下文窗口里装了什么

一次模型请求通常包含：

- 基础与开发者 instructions；
- conversation history；
- world state、Skill 或 Memory 片段；
- Tool specs；
- 模型输出、工具调用和工具结果；
- output schema 与 reasoning 配置。

其中最危险的是无界内容，例如巨大的命令输出或一次列出所有外部工具。它们不仅占 token，还会破坏稳定前缀和缓存效果。

## 2. `ContextWindowTokenStatus` 回答什么

当前实现同时追踪：

- active context 已使用多少 token；
- 自动压缩作用域内使用多少；
- 自动压缩阈值；
- 模型完整 context window 的硬上限；
- 距离可用窗口还剩多少；
- 是否已经触发压缩条件。

为什么有两种使用量？因为自动压缩可以针对整个 active context，也可以只计算初始前缀之后新增的 body。无论选择哪种作用域，模型完整窗口始终是不能越过的硬上限。

## 3. 剩余空间为什么取更严格的限制

假设自动压缩阈值还剩 8k token，但模型完整窗口只剩 3k，那么真实可安全使用空间最多是 3k。

```text
可报告剩余空间 = min(
  自动压缩作用域剩余量,
  模型完整窗口剩余量
)
```

只看其中一个限制，会在另一个限制先到达时发生意外溢出。

## 4. Token Budget 提醒不是每轮重复唠叨

Token Budget Feature 可以根据模型默认值或用户显式设置，在剩余空间低于阈值时写入一次有界提醒。Session state 会 claim 这次提醒，避免每次 sampling 都重复追加相同文本。

显式用户设置优先于模型建议默认值；模型提供的默认配置也必须先验证，不能直接信任。

## 5. Compaction 不等于简单删除前半段

压缩需要保留未来继续工作的必要信息，例如：

- 当前目标和已经完成的工作；
- 关键决定、约束与文件变化；
- 当前 world-state baseline；
- 后续工具调用需要的结构关系。

如果随便截断 history，可能留下 tool output 却丢掉对应 call，或者丢失“这个环境快照相对什么基线发生变化”。

Token-budget compaction 还可以走不调用模型摘要的窗口重建路径；它与传统“让模型生成一段摘要再替换 history”不是同一机制。

## 6. 工具输出为什么必须先截断

设想命令打印 5 MB 构建日志。若完整写入 history：

- 当前请求可能立刻超过窗口；
- 后续每轮都重复携带无关日志；
- prompt cache 和传输成本都恶化。

正确策略是保存有界、对模型有用的表示，并在需要时提供分页、文件引用或后续搜索手段。截断信息还应明确告诉模型“这里只是部分输出”，避免把缺失部分误解为不存在。

## 7. Cache 友好的上下文

服务端更容易复用稳定前缀，所以应尽量：

- 让 history 只追加，不频繁改写旧 item；
- 把变化状态表示为有界 diff；
- 保持 instructions 和 tool specs 稳定；
- 只在真正需要时展开 Skill、Memory 或 deferred tools；
- 避免把时间戳、随机顺序等高频变化内容放进前缀。

这与第 13 章的增量传输相关，但仍不是同一件事：稳定前缀有利于缓存，严格前缀扩展决定能否少传 input。

## 8. 一次 Turn 应怎样分解耗时

`TurnTimingState` 记录的不只是总时长，还包括：

- before first sampling；
- sampling；
- compaction；
- between-sampling overhead；
- tool blocking；
- sampling retry count；
- TTFT：到第一个模型 token；
- TTFM：到第一条用户可见 message/item。

### 三个例子

- TTFT 很高：更可能是模型排队、网络或请求体问题；
- TTFT 正常但 TTFM 高：模型可能先产生大量非消息事件，或产品层迟迟没有可展示 item；
- sampling 很短但 tool blocking 很高：应优化工具、远端环境或外部服务，而不是换模型。

## 9. 优化顺序

1. 先确认是哪一阶段慢；
2. 消除无界工具输出和上下文注入；
3. 检查不必要的重复 sampling 或 retry；
4. 检查工具能否安全并行；
5. 改善稳定前缀和按需加载；
6. 最后再考虑模型、网络和更激进的缓存策略。

没有分阶段数据时，“优化 Agent”很容易变成凭感觉改代码。

## 常见误解

- **“上下文窗口只计算用户和助手文本。”** 工具定义、结果和其他结构化内容也占空间。
- **“压缩就是删掉最旧的一半。”** 结构关系和恢复 baseline 可能因此损坏。
- **“Turn 慢就是模型慢。”** 工具阻塞、compaction 和重试可能才是主因。
- **“payload 变小就说明 prompt cache 命中。”** 传输增量与服务端缓存仍是不同机制。

## 读完后自测

1. 为什么自动压缩阈值与模型硬窗口需要同时检查？
2. 巨大工具输出除了占 token，还会造成哪些长期影响？
3. TTFT 正常、总 Turn 很慢时，你下一步会检查什么？

## 本章词汇表

| 词语 | 直译 | 在容量与性能中的意思 |
|---|---|---|
| Token | 词元 | 模型计量文本和结构化输入长度的基本单位 |
| Context window | 上下文窗口 | 一次模型请求能容纳的 token 总空间 |
| Threshold | 阈值 | 达到某个数值后触发提醒或压缩的边界 |
| Truncation | 截断 | 按硬上限保留部分内容并明确省略其余部分 |
| TTFT | 首 Token 时间 | Turn 开始后到模型第一个 token 到达的耗时 |
| TTFM | 首消息时间 | Turn 开始后到首个用户可见 message/item 的耗时 |
| Prefill | 前缀填充 | 模型处理请求已有上下文前缀的阶段或 token 量 |
| Overhead | 额外开销 | 不属于核心采样/工具本身、但流程必须付出的时间 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

- `codex-rs/core/src/session/context_window.rs`：窗口状态计算；
- `codex-rs/core/src/session/token_budget.rs`：默认值、提醒与 fallback；
- `codex-rs/core/src/compact_token_budget.rs`：Token-budget 窗口重建；
- `codex-rs/core/src/context/`：有界上下文片段；
- `codex-rs/core/src/turn_timing.rs`：TTFT、TTFM 与阶段耗时；
- `codex-rs/models-manager/src/model_info.rs`：模型相关窗口和截断配置。
