# 13. Multi-Agent：委派、通信与等待

## 1. Multi-Agent 解决什么

多 Agent 适合把独立、边界清晰的子任务并行处理，例如：

-分别调查两个互不依赖的模块；
-一个 Agent 跑耗时验证，另一个继续分析；
-让专门角色做独立 review。

它不适合把一个紧密串行的小修改强行拆碎。协调成本、共享工作区冲突和上下文复制都会抵消收益。

## 2. 子 Agent 仍是完整 Thread

子 Agent 不是一个普通 Tool Future。它通常拥有：

-自己的 thread/session/turn；
-独立 History 和模型采样循环；
-父子关系与 canonical task name；
-自己的状态和最终输出；
-可配置的上下文继承方式。

父 Agent 的 `spawn_agent` Tool 只是创建和控制子线程的入口。

## 3. Agent Tree

canonical task name 表示层级，例如：

```text
/root
├── /root/api_review
│   └── /root/api_review/test_check
└── /root/ui_review
```

内部还需要稳定 Agent ID。名称便于模型和用户理解，ID 便于持久化和路由；不要只使用可变显示名称作为唯一身份。

## 4. Spawn

Spawn 请求通常包含：

-任务描述；
-task name；
-fork turns 策略；
-可选模型和 reasoning override；
-可选角色。

实现必须检查：

-并发槽位；
-spawn 深度或版本规则；
-任务名合法性和冲突；
-允许的模型 override；
-父线程权限和 sandbox 如何继承；
-是否应该复制上下文。

## 5. 上下文继承

完整复制所有父 History 成本很高，也可能把无关敏感信息带给子任务。常见策略：

- `none`：只发送子任务说明和系统上下文；
-最近 N turns；
-完整可见 History；
-显式摘要。

父 Agent 应为子任务提供足够的目标、边界、文件位置和期望输出，而不能假设子 Agent 自动理解父线程未传递的隐含意图。

## 6. 共享工作区

多 Agent 可以共享文件系统。这带来优势，也带来竞态：

-一个 Agent 的修改立即对另一个可见；
-两个 Agent 同时 patch 同一文件可能冲突；
-测试结果可能包含其他 Agent 尚未完成的变更；
-Git working tree 属于所有协作者，而不是单一 Agent。

实践上应按模块或任务边界分工，并让父 Agent 负责最终集成和验证。

## 7. 通信工具

当前多 Agent 工具族包括：

- `spawn_agent`：创建子 Agent；
- `send_message`：给运行中 Agent 追加信息；
- `followup_task`：为空闲 Agent 启动新任务；
- `wait_agent`：等待状态或 mailbox 活动；
- `list_agents`：查看 Agent tree；
- `interrupt_agent`：中断当前 Turn；
-旧版本还包含 send_input、resume、close 等语义。

工具 schema 和不同版本实现位于 [`tools/handlers/multi_agents_spec.rs`](../codex-rs/core/src/tools/handlers/multi_agents_spec.rs) 及相邻目录。

## 8. Mailbox

Agent 间消息不是直接修改对方当前 Prompt。消息进入 mailbox/input queue，在安全采样边界成为结构化 inter-agent context。这样可以：

-保留发送者身份；
-持久化通信；
-避免破坏正在进行的 stream；
-让 waiting Agent 在新邮件到达时唤醒。

## 9. Wait 不是轮询死循环

`wait_agent` 应等待状态通知或 mailbox activity，并设置最小、默认和最大 timeout。父 Agent 不应频繁轮询；如果仍有独立工作，应继续工作，只有关键路径真正依赖结果时才等待。

## 10. 结果汇总

子 Agent 的最终文本只是证据之一。父 Agent 还应：

-检查共享文件的实际 diff；
-验证测试；
-解决不同子 Agent 结论冲突；
-确认子任务是否完整；
-向用户给出统一而非简单拼接的答案。

## 11. 取消和失败

-中断 Agent 应停止当前 Turn，但保留可恢复状态；
-子 Agent 失败应返回结构化状态和最后进展；
-父 Agent 结束时要决定是否保留仍运行的子 Agent；
-并发限制不是普通错误，父 Agent 可以改为本地执行；
-共享工作区已有修改不能因子 Agent 失败被破坏性回滚。

## 12. 实现建议

先实现“任务树 + mailbox + 状态通知”，再增加复杂角色和模型 override。核心类型至少包括：

```text
AgentId
AgentPath
ParentId
AgentStatus
MailboxMessage
SpawnRequest
CompletionResult
```

多 Agent 的价值来自独立并行工作，而不是让更多模型同时讨论同一个模糊问题。

