# 11：多 Agent

## 先用团队协作来理解

父 Agent 像项目负责人。它可以让子 Agent A 调查失败测试的原因，让子 Agent B 搜索相关历史实现。A、B 各自拥有独立的对话记录、工具调用和进度，最后把结果交回父 Agent。

这比让一个 Agent 在同一份笔记里来回扮演多个角色更清楚，因为每个子任务有自己的状态边界。

## 1. 核心模型：Agent 是 Thread

子 Agent 不是父 Agent 中的一个异步函数，也不是共享同一 history 的临时 prompt。每个子 Agent 都有独立 `ThreadId`、session、turn、history、rollout 和模型循环。父子关系由 Agent metadata 与持久 graph edge 表达。

这样设计带来：

- 子 Agent 可独立执行多轮工具循环；
- 可以等待、打断、恢复或卸载；
- 父子 history 不会默认相互污染；
- 拓扑可以跨进程恢复。

但创建子 Agent 也有成本：它需要自己的上下文、推理和协调。通常只应把彼此相对独立、确实能并行推进的工作拆出去。

## 2. 三类状态

| 状态 | 所有者 | 用途 |
|---|---|---|
| 活动身份与 path | `AgentRegistry` | path ↔ thread ID、nickname、role、并发槽 |
| 控制能力 | `AgentControl` | spawn、send、wait、status、shutdown、residency |
| 持久父子拓扑 | `AgentGraphStore` | open/closed edge、children、BFS descendants |

一棵 Agent 树共享同一个 `AgentControl` 和 session ID，但不是全局共享：registry 的作用域是这棵 root thread tree。

## 3. `spawn_agent`

V2 spawn 的主要步骤是：

1. 解析目标 path、role、model/reasoning 和 fork 选项；
2. 预留 agent path、nickname、thread/execution/residency 容量；
3. 构造子 Agent config，同时保留 runtime 的 cwd、approval 和 permission profile 边界；
4. 新建 thread，或从父 thread history 构造受控 fork；
5. 注册 metadata，持久化 parent → child graph edge；
6. 向新 thread 提交初始 `InterAgentCommunication`，触发首个 turn；
7. 返回 thread ID、agent path/name 和初始 status。

Fork 不是共享可变 history。实现会先 materialize/flush 父 rollout，再加载模型上下文，按 full/last-N 策略清理 usage hints、父级 instructions 和不适合继承的 item。

## 4. Message 与 Follow-up 的区别

- `send_message`：向目标 history/mailbox 发送通信，`trigger_turn = false`；适合补充上下文，不应凭空启动一个新任务。
- `followup_task`：发送新任务，`trigger_turn = true`；目标空闲时启动新 turn，运行中则进入输入队列/边界处理。

`InterAgentCommunication` 保留 author、recipient、message 类型和 trigger 语义。V2 direct plaintext message 还会经过专用格式化；非直接来源使用加密/受控表示，避免把内部消息处理成任意用户文本。

### 三种容易混淆的操作

- 子 Agent A 仍在运行，但需要补充“优先检查权限逻辑”：发送消息，把新信息送进正在进行的工作；
- 子 Agent A 已经完成，现在要它继续研究另一个问题：发送 follow-up，启动新的工作轮次；
- 只是想知道 A 做到哪里了：读取或等待状态，不要重复派发同一个任务。

## 5. Wait 不是轮询 sleep

`wait_agent` 订阅状态和 mailbox 活动：

- 任一目标进入 final status 时可返回；
- 新的 inter-agent mail 可以提前唤醒父 Agent；
- timeout 有配置的默认、最小和最大边界；
- 已排队的 mail 应立即完成 wait；
- 返回状态摘要，但不把完成内容重复塞进 tool output，因为完成消息会经 mailbox/history 交付。

## 6. Interrupt、Unload 与 Resume

Interrupt 停止当前 turn，但不等于关闭 thread。V2 residency 可以卸载空闲子 Agent 来控制内存；再次操作时，`ensure_v2_agent_loaded()` 从 thread store/rollout 恢复运行实例和 metadata。持久 graph edge 决定哪些 descendants 仍是 open，而不是单纯相信当前内存 registry。

因此要分清：

- `Running/Completed/Interrupted/Shutdown` 等运行状态；
- resident / unloaded 的内存状态；
- graph edge open / closed 的拓扑状态。

## 7. 防爆炸边界

多 Agent 同时受多种限制：spawn 总量、并发执行数、residency 容量、旧版 depth 限制以及 session rollout budget。预留对象采用 commit-on-success；失败或 drop 时归还槽位，避免半创建 Agent 泄漏容量。

## 8. 什么时候不该使用子 Agent

- 后一步严格依赖前一步结果，无法真正并行；
- 任务很小，协调成本高于实际工作；
- 多个 Agent 会同时修改同一小段代码，容易冲突；
- 只是想获得“第二种说法”，但没有独立、明确的子任务。

## 读完后自测

1. 为什么子 Agent 不是父 Agent history 中的一个临时角色？
2. 给正在运行的 Agent 补充信息，与让已完成 Agent 开始新任务，有何不同？
3. 哪些任务看似能拆分，实际会因为强依赖或文件冲突而不适合并行？

## 本章词汇表

| 词语 | 直译 | 在多 Agent 中的意思 |
|---|---|---|
| Spawn | 创建、生成 | 创建子 Agent thread、metadata 和初始 Turn |
| Fork | 分叉 | 从父 thread 的受控历史快照创建独立历史 |
| Registry | 注册表 | 保存活动 Agent 身份、路径和运行状态 |
| Graph store | 图存储 | 持久保存父子 Agent 拓扑关系 |
| Residency | 驻留 | Agent 的运行实例当前是否在内存中 |
| Mailbox | 邮箱 | Agent 间消息排队、交付和唤醒的通信机制 |
| Follow-up | 后续任务 | 为目标 Agent 触发新的工作轮次 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

1. 阅读 `AgentControl` 的注释，解释它为什么持有 `Weak<ThreadManagerState>`。
2. 比较 `send_message` 与 `followup_task` handler 构造的 `trigger_turn`。
3. 在 `agent/control_tests.rs` 找 fork、completion notification、residency reload 三类测试。
4. 在 `multi_agents_tests.rs` 找 wait 被 mailbox 唤醒的测试，说明它为什么不返回 completed content。
