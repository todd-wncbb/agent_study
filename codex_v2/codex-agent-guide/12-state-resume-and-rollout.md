# 12. 状态持久化、恢复与 Rollout

## 1. 三种“状态”

需要区分：

| 状态 | 目的 |
|---|---|
| Live Session state | 当前进程继续执行 |
| Conversation History | 下一次模型请求使用 |
| Rollout / Thread store | 恢复、fork、审计和回放 |

只保存聊天文本无法恢复一个工具型 Agent，因为还缺少 Tool Call、Result、配置、compaction 和 World State checkpoint。

## 2. Rollout 是事件日志

Session 在发生重要事实时 append rollout item，例如：

-用户、assistant 和 Tool ResponseItem；
-Session metadata；
-Turn context；
-World State full snapshot 或 patch；
-Compacted replacement history；
-inter-agent communication metadata。

事件日志的优势是可以按发生顺序重放，并保留“当时系统知道什么”。

## 3. 记录顺序

Session 的统一记录方法通常按以下顺序：

1.规范化准备写入的 item；
2.更新内存 History；
3. append rollout；
4.向客户端发送 raw item/event。

不同类型可能有专门顺序约束。例如 World State patch 在模型可见 diff 已进入 History 后持久化；Compaction replacement 在新 baseline 前持久化。

## 4. Resume

恢复线程不是把最后一条 assistant 文本放回聊天框。Runtime 需要：

1.读取 rollout items；
2.重建 Session metadata 和配置；
3.识别最近一次 compaction replacement；
4.恢复其后的 ResponseItems；
5.重建 World State full + patches；
6.恢复 reference turn context；
7.规范化不完整 Tool pair；
8.创建新的 live Session 服务和连接。

主要逻辑可从 [`session/rollout_reconstruction.rs`](../codex-rs/core/src/session/rollout_reconstruction.rs) 开始阅读。

## 5. 为什么保存 Turn Context

恢复时当前配置可能与历史运行时不同。例如：

-用户切换了模型；
-工作目录改变；
-权限策略改变；
-personality 或 collaboration mode 改变。

如果不保存历史 Turn Context，就无法解释过去消息是在什么条件下产生，也无法正确决定首次恢复请求需要完整注入还是只发送 diff。

## 6. Fork

Fork 创建一个共享过去、拥有独立未来的新线程。语义上应做到：

-复制或引用 fork 点之前的有效 History；
-新线程拥有新的 thread ID；
-记录 parent/fork metadata；
-从 fork 点建立新的持久化流；
-未来设置、Tool Call 和 compaction 不影响原线程。

Fork 不是在同一个 Session 上增加分支数组，而是创建新的状态所有者。

## 7. 未完成 Tool Call

进程可能在副作用工具执行后、Result 持久化前崩溃。恢复时存在三种风险：

-工具根本没执行；
-工具执行了一半；
-工具执行成功但 Result 丢失。

因此副作用工具需要按风险选择：

-使用外部幂等 key；
-记录 started/completed lifecycle；
-恢复时查询外部状态；
-要求用户确认是否重试；
-或明确把未知状态作为 Tool error 给模型。

绝不能默认把所有缺失 Result 的调用重新执行。

## 8. 状态兼容性

长期存在的 rollout 可能来自旧版本。恢复层应：

-对新增可选字段提供默认；
-保留未知 item 或安全忽略；
-在协议边界使用稳定 string ID；
-不要依赖当前内存指针或枚举序号；
-对破坏性 schema 变化提供迁移或明确拒绝。

## 9. 审计和调试

Rollout 是定位 Agent 问题的重要证据：

-模型当时实际看到了哪些 item；
-哪个 Tool Call 由哪次响应产生；
-权限是否在该 Step 已改变；
-compact 前后丢失了什么；
-恢复后为何选择了不同模型或工具。

因此日志应保存结构化事实，而不是只有自然语言 debug line。

## 10. 实现建议

使用 append-only event store，并定期生成 checkpoint：

```text
SessionStarted
UserMessage
ModelItem
ToolStarted
ToolCompleted
WorldStatePatch
CompactionCheckpoint
TurnCompleted
```

恢复时从最近 checkpoint 开始重放。事件需有稳定 thread/turn/step/call ID，并明确哪些事件代表事实提交，哪些只是观察或 UI 展示。

