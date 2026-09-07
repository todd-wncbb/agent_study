# Turn 与 Step：从代码理解 Agent 运行时

本文基于 `codex-rs` 源码（非 `study_codex_docs` 其他文档）说明：**用户一次提问**在运行时如何对应 **Turn** 和 **Step**。

## 场景

用户连续问两个问题：

1. **问题 A**：这个世界上有多少个国家？
2. **问题 B**：（另一个问题，流程类似）

每个问题的典型链路：

```
用户提问 → Agent 发给 LLM → LLM 要求调用 web_search
→ web_search 返回结果 → Agent 把结果再发给 LLM → LLM 生成最终回答给用户
```

下面说明 Turn、Step 分别对应上述链路的哪一段。

---

## 三个层级的概念

| 层级 | 含义 | 对外事件 |
|------|------|----------|
| **Thread** | 一整条对话线程，可包含多次用户轮次 | — |
| **Turn** | 用户的一次输入及其完整处理周期 | `TurnStarted` / `TurnComplete` |
| **Step** | Turn 内部的一次 LLM sampling request | 无独立协议事件（内部概念） |

一句话：

- **Turn** 对齐「你问了一句」。
- **Step** 对齐「Agent 向 LLM 发了一次请求（以及该次响应触发的 tool 执行）」。

---

## Turn：一次用户问题

### 代码入口

用户消息经 `handlers` 进入 `spawn_task`，启动 `RegularTask`：

```rust
// codex-rs/core/src/session/handlers.rs
sess.spawn_task(
    Arc::clone(&current_context),
    task_input,
    crate::tasks::RegularTask::new(),
)
```

`RegularTask` 发出 `TurnStarted`，调用 `run_turn`，结束后由 task 生命周期发出 `TurnComplete`：

```rust
// codex-rs/core/src/tasks/regular.rs
let event = EventMsg::TurnStarted(TurnStartedEvent {
    turn_id: ctx.sub_id.clone(),
    // ...
});
sess.send_event(ctx.as_ref(), event).await;
// ...
let last_agent_message = run_turn(/* ... */).await?;
```

### Turn 何时结束

`run_turn` 顶部注释（`codex-rs/core/src/session/turn.rs`）：

- 模型 **只返回 assistant 消息**、没有需要跟进的 tool call → **Turn 结束**。
- 模型 **请求 function call** → 执行 tool，在 **下一次 sampling request** 里把结果送回模型。

因此：

| 你的操作 | 代码 |
|----------|------|
| 问题 A | **Turn 1**（`turn_id` = `TurnContext.sub_id`） |
| 问题 B | **Turn 2**（新的 `turn_id`） |

---

## Step：一次 LLM 请求（sampling request）

### 定义

`StepContext` 注释（`codex-rs/core/src/session/step_context.rs`）：

> Request-scoped state that may change between model sampling requests.

即：**Step = `run_turn` 主循环里的一次「capture step 上下文 + 向模型发起 sampling」**。

主循环（`codex-rs/core/src/session/turn.rs`）：

```rust
loop {
    let step_context = /* capture_step_context ... */;
    run_sampling_request(
        sess,
        step_context,
        // ...
    ).await;
    // 根据 needs_follow_up 决定是否 continue 进入下一个 Step
}
```

### Step 里没有 `StepStarted`

Step 是运行时内部粒度；客户端 / rollout 上可见的是 Turn 级别的 `TurnStarted`、`TurnComplete`。

### 一个 Step 里发生什么

在 `try_run_sampling_request` 中：

1. **一次** HTTP/WebSocket 流式调用 LLM。
2. 若响应含 tool call → 放入 `in_flight`，流结束后 `drain_in_flight` **执行 tool**。
3. Tool 输出通过 `record_conversation_items` **写入 conversation history**。
4. 若 `needs_follow_up == true`，**不在本 Step 内再次调 LLM**；由 `run_turn` 的 `loop` 进入 **下一个 Step** 再请求模型。

```rust
// codex-rs/core/src/session/turn.rs — drain_in_flight
while let Some(res) = in_flight.next().await {
    let response_item = response_input.into();
    sess.record_conversation_items(&turn_context, std::slice::from_ref(&response_item))
        .await;
}
```

---

## 问题 A 的完整映射（含 web_search）

典型情况：**1 个 Turn，2 个 Step**。

```
Turn 1（问题 A：世界上有多少个国家？）
│
├── Step 1
│   ├── capture_step_context（本步的工具列表、MCP、环境快照等）
│   ├── run_sampling_request → 调用 LLM（用户问题 + 历史上下文）
│   ├── LLM 流式返回 web_search tool call
│   ├── drain_in_flight → 执行 web_search
│   ├── 搜索结果写入 conversation history
│   └── needs_follow_up = true
│
├── Step 2
│   ├── capture_step_context（可能刷新 step 级状态）
│   ├── run_sampling_request → 再次调用 LLM（历史已含搜索结果）
│   ├── LLM 返回 assistant 最终回答
│   └── needs_follow_up = false
│
└── TurnComplete
```

若模型 **不调 tool、直接回答**，通常是 **1 Turn + 1 Step**。

问题 B 是 **新的 Turn**；内部同样可能是 1 个或多个 Step（取决于是否调 tool、是否 compaction、是否有 steer 等）。

---

## 流程图（ASCII）

```
Thread（对话线程）
│
├── Turn 1（问题 A）
│   ├── Step 1:  LLM ──► web_search ──► 结果入 history
│   └── Step 2:  LLM ──► 最终回答 ──► TurnComplete
│
├── Turn 2（问题 B）
│   ├── Step 1:  ...
│   └── Step 2:  ...（若需要）
│       └── TurnComplete
│
└── ...
```

---

## 与「用户心智模型」的对应

| 用户心智中的步骤 | 代码中的归属 |
|------------------|--------------|
| 收到用户问题 A | Turn 1 开始（`TurnStarted`） |
| 第一次发给 LLM | Turn 1 · Step 1 · `run_sampling_request` |
| LLM 说要调 web_search | 仍在 Step 1 的流式响应里 |
| 执行 web_search | Step 1 · `drain_in_flight`（同 Step 内） |
| 把搜索结果再发给 LLM | Turn 1 · **Step 2**（新的 sampling request） |
| LLM 把答案给用户 | Step 2 的 assistant 消息；Turn 1 结束 |
| 用户问问题 B | **Turn 2** 开始 |

注意：**「把 tool 结果再发给 LLM」不是同一次 Step 里的第二次请求**，而是 `run_turn` 循环的下一步 Step。

---

## 相关源码索引

| 主题 | 路径 |
|------|------|
| Turn 主循环与结束条件 | `codex-rs/core/src/session/turn.rs`（`run_turn` 注释与 `loop`） |
| Step 上下文 | `codex-rs/core/src/session/step_context.rs` |
| 捕获 Step 上下文 | `codex-rs/core/src/session/mod.rs`（`capture_step_context`） |
| Sampling + tool drain | `codex-rs/core/src/session/turn.rs`（`run_sampling_request`、`try_run_sampling_request`、`drain_in_flight`） |
| Turn 任务与生命周期事件 | `codex-rs/core/src/tasks/regular.rs`、`codex-rs/core/src/tasks/mod.rs` |
| 用户消息入口 | `codex-rs/core/src/session/handlers.rs` |
| Turn 级可变状态 | `codex-rs/core/src/state/turn.rs`（`ActiveTurn`、`TurnState`） |
| App-server Turn 类型 | `codex-rs/app-server-protocol/src/protocol/v2/thread_data.rs`（`Turn`） |

---

## 延伸阅读

若需与 Session、协议事件、端到端数据流对照，可参考 `study_codex_docs/codex-agent-guide` 系列；本文刻意 **只从源码行为** 归纳 Turn/Step。
