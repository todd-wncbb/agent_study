# 35：代码审查实战——从教学 Diff 中找出真正会伤害用户的问题

第 34 章站在作者角度准备一个可审查 PR。本章交换位置：你是 reviewer，要判断一份“测试已经通过”的改动是否真的可以合并。

本章先给出一份教学 diff。你可以暂停阅读，自己记录 findings，再与后半部分答案比较。

> 源码基线：`4ee41929eaf4`。教学 diff 使用接近 Codex 的类型和路径，但并不是仓库当前真实 PR，也不声称这些缺陷存在于当前实现。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分 bug finding、设计建议和纯风格意见；
2. 只报告由本次 diff 引入、能够具体触发的问题；
3. 为 finding 选择 P0/P1/P2/P3 优先级；
4. 给出准确文件、紧凑行号、触发条件和用户影响；
5. 检查 model-visible context 的容量、增量性和缓存影响；
6. 检查 app-server、CLI、配置和 rollout breaking changes；
7. 在异步状态机中寻找迟到事件、错误关联和锁问题；
8. 判断测试是否覆盖真实公开行为；
9. 判断大型 diff 是否应拆成可独立落地的阶段。

---

## 2. 代码审查不是“找得越多越好”

高质量 review 的目标是降低合并风险，不是证明 reviewer 比作者聪明。

一条有效 finding 通常需要同时满足：

```text
由本次改动引入
+ 存在具体触发路径
+ 会导致可观察的错误或重要维护风险
+ 能定位到相关代码
+ 作者可以据此采取行动
```

下面这些通常不是 bug finding：

- “我更喜欢另一个变量名”；
- 与本次 diff 无关的旧问题；
- 没有触发条件的抽象担忧；
- formatter 会自动处理的格式；
- 只说“这里可能有 race”，却解释不了两个事件怎样交错；
- 重复报告同一个根因造成的多个症状。

---

## 3. Review 的三个输出层次

### 3.1 Blocking finding

合并前必须修复，例如数据丢失、权限绕过、兼容性破坏、确定性崩溃、无界模型上下文。

### 3.2 Non-blocking suggestion

当前代码可正确工作，但有更清楚或更易维护的写法。应明确标记 suggestion，不要伪装成 bug。

### 3.3 Question

证据不足，需要作者解释设计约束。例如：

```text
这个 endpoint 是否保证每次只存在一个 active turn？
```

问题不是 finding，除非进一步证据证明实现确实错误。

---

## 4. 优先级怎样理解

| 级别 | 含义 | 典型例子 |
|---|---|---|
| P0 | 必须立即阻断；高确定性灾难性后果 | 凭据外传、广泛数据损坏、单个 model-context item 可无界超过安全上限 |
| P1 | 高优先级；常见路径严重错误或明确兼容性破坏 | 错 turn 被结束、稳定客户端无法解析新 wire shape |
| P2 | 正常优先级；条件性 bug、测试缺口或重要维护风险 | 某失败分支缺测试、特定输入产生错误状态 |
| P3 | 低优先级；影响较小但确实可观察 | 少见提示文案错误、非关键性能浪费 |

优先级由**影响 × 发生可能性 × 可恢复性**共同决定，不是由代码行数决定。

### 4.1 P0 应非常少

如果每条评论都是 P0，等级就失去意义。Codex 的 model-context 规则明确把单个可能超过 1K token 的新注入项标为 P0 额外审查对象，并要求任何注入都必须有硬上限、不得超过 10K tokens。

---

## 5. 教学 PR 的目标

PR 标题：

```text
Expose recent tool context to models and v2 clients
```

PR 正文：

```text
What: Preserve recent tool output so the model and v2 clients can diagnose
failed commands.

How: Store tool output strings on Session, append them to the next prompt,
and return them from turn/completed.

Tests: Added unit tests for the new buffer and serializer.
```

Diff 统计：

```text
9 files changed, 1,146 insertions(+), 83 deletions(-)
```

作者说所有 unit tests 均通过。

---

## 6. 教学 Diff A：把工具输出保存到 Session

下面的行号只用于本章练习。

```diff
  41 pub(crate) struct SessionState {
  42     pub(crate) active_turn: Option<ActiveTurn>,
+ 43     pub(crate) recent_tool_context: Vec<String>,
  44 }

 118 pub(crate) fn record_tool_output(&mut self, output: String) {
+119     self.recent_tool_context.push(output);
 120 }

 173 pub(crate) fn take_recent_tool_context(&mut self) -> Vec<String> {
+174     std::mem::take(&mut self.recent_tool_context)
 175 }
```

文件：`codex-rs/core/src/state/session.rs`（教学路径）

先自己想：

- 谁控制 `output` 大小？
- Vec 最多保存多少项？
- 什么时候清空？
- 如果 turn 被取消或一直不再 sampling，会怎样？
- 多个 turn 是否共享这份数据？

---

## 7. 教学 Diff B：重写旧工具结果并追加新上下文

```diff
 201 pub(crate) async fn prepare_next_prompt(
 202     session: &Session,
 203     history: &mut Vec<ResponseItem>,
 204 ) -> Prompt {
+205     history.retain(|item| !matches!(item, ResponseItem::FunctionCallOutput { .. }));
+206
+207     let recent = session.state.lock().await.take_recent_tool_context();
+208     history.push(ResponseItem::Message {
+209         role: "user".to_string(),
+210         content: recent.join("\n\n"),
+211     });
 212
 213     Prompt::new(history.clone())
 214 }
```

文件：`codex-rs/core/src/session/prompt.rs`（教学路径）

需要检查：

- Codex history 是否允许删除旧项？
- 工具结果为何被改成普通 user message？
- `join` 后有没有 token hard cap？
- 空 `recent` 会不会每次加入变化项？
- 这会怎样影响 prompt cache？
- `session.state` lock 的作用域是否合理？

---

## 8. 教学 Diff C：终态事件清除 Active Turn

```diff
 310 pub(crate) async fn handle_turn_complete(
 311     state: &Arc<Mutex<ThreadState>>,
 312     event: TurnCompleteEvent,
 313 ) {
 314     let mut state = state.lock().await;
+315     state.active_turn = None;
+316     state.last_tool_context = event.tool_context.clone();
 317     state.completed_turns.insert(event.turn_id.clone(), event.into());
 318 }
```

文件：`codex-rs/app-server/src/thread_state.rs`（教学片段）

背景：同一 thread 在旧 turn 结束后，队列可能已经开始一个新 turn；旧 turn 的终态投影也可能迟到。

思考：

- `event.turn_id` 与 `active_turn.turn_id` 比较了吗？
- 迟到的 turn A complete 是否可能清掉 turn B？
- `last_tool_context` 属于哪一个 turn？
- state lock 内是否有 await？本片段暂时没有。

---

## 9. 教学 Diff D：修改 App-server v2 Payload

旧类型：

```rust
pub struct TurnCompletedNotification {
    pub thread_id: String,
    pub turn: Turn,
}
```

新类型：

```diff
  88 #[serde(rename_all = "camelCase")]
  89 pub struct TurnCompletedNotification {
  90     pub thread_id: String,
  91     pub turn: Turn,
+ 92     #[serde(skip_serializing_if = "Vec::is_empty")]
+ 93     pub tool_context: Vec<String>,
  94 }
```

文件：`codex-rs/app-server-protocol/src/protocol/v2/turn.rs`（教学片段）

作者没有运行 `just write-app-server-schema`，认为空数组不需要发送。

需要检查：

- v2 response/notification 是否允许 `skip_serializing_if`？
- 字段在 TypeScript/schema 中是 required、optional 还是 nullable？
- 旧客户端收到新字段怎样处理？
- 新客户端读取旧 server 响应会怎样？
- 每次通知携带大数组会不会造成 payload 膨胀？

---

## 10. 教学 Diff E：重命名配置项

旧配置：

```toml
tool_output_limit = 32768
```

新类型：

```diff
 501 pub struct ConfigToml {
-502     pub tool_output_limit: Option<usize>,
+502     pub recent_context_bytes: Option<usize>,
 503 }
```

读取逻辑：

```diff
-611 let limit = config.tool_output_limit.unwrap_or(32_768);
+611 let limit = config.recent_context_bytes.unwrap_or(1_048_576);
```

作者没有 serde alias、迁移说明或 config schema 更新。

思考：

- 旧配置会被忽略、报错还是落回新默认？
- 默认从 32 KiB 变为 1 MiB 是不是独立行为变化？
- managed config/requirements 怎样解释旧 key？
- byte limit 是否等于 token limit？

---

## 11. 教学 Diff F：测试

```rust
#[test]
fn recent_context_can_be_taken() {
    let mut state = SessionState::default();
    state.record_tool_output("first".to_string());
    state.record_tool_output("second".to_string());

    assert_eq!(
        state.take_recent_tool_context(),
        vec!["first".to_string(), "second".to_string()]
    );
}

#[test]
fn turn_completed_omits_empty_tool_context() {
    let notification = make_notification(Vec::new());
    let json = serde_json::to_value(notification).unwrap();
    assert!(json.get("toolContext").is_none());
}
```

除此之外没有：

- Core Agent integration test；
- app-server JSON-RPC integration test；
- TUI snapshot；
- context-size boundary test；
- resume/rollout test；
- old-config compatibility test。

---

## 12. 暂停：先自己写 Findings

建议用下面模板：

```text
[P?] 标题
文件:行号

当【触发条件】发生时，这段代码会【错误行为】，导致【用户/系统影响】。
应当【最小修复方向】。
```

先回答：

1. 最严重的问题在哪里？
2. 哪些是同一个根因，不应重复报告？
3. 哪些只需要 question，而不是 finding？
4. 哪些测试即使通过也没有证明 PR 安全？

下面开始公布教学答案。

---

## 13. Finding 1：[P0] 给模型注入的工具上下文没有硬上限

建议评论位置：

```text
codex-rs/core/src/session/prompt.rs:207-210
```

Finding：

```text
[P0] Bound tool context before adding it to the model prompt

`recent` can contain an arbitrary number of arbitrarily large command/MCP
outputs, and `join` inserts the entire buffer as one prompt item. A long-running
command or repeated tool loop can therefore create a single unbounded context
fragment, exceed the model window/request limits, and make the turn fail before
the model can respond. Apply an explicit token-aware hard cap before creating
the contextual item; the current byte-valued config is not sufficient by itself.
```

### 为什么是 P0

仓库 model-visible-context 规则要求：

- 所有注入项必须有 hard cap；
- 单项不能超过 10K tokens；
- 可能超过 1K tokens 的新单项必须作为 P0 额外人工审查；
- 注入 fragment 应定义在 `core/context` 并实现 `ContextualUserFragment`。

这段代码不仅可能超过 1K，而是完全无界，所以在这套仓库规则下应阻断合并。

### 为什么评论在 207–210 行

`Vec::push` 是容量来源，但真正把无界数据变成 model-visible item 的地方是 `join` 和 `history.push`。评论应贴在最直接触发风险的紧凑行范围。

### 不要怎样写

```text
This might be too much data.
```

它没有说明无界输入、模型窗口失败和具体修复要求。

---

## 14. Finding 2：[P1] 删除既有 FunctionCallOutput 会重写历史

评论位置：

```text
codex-rs/core/src/session/prompt.rs:205
```

Finding：

```text
[P1] Preserve previously recorded tool outputs in incremental history

Removing every `FunctionCallOutput` rewrites the model-visible conversation on
each sampling step. The next request no longer extends the previously sent
history, so tool-call/result pairs disappear, prompt-cache prefixes change, and
resume/replay can reconstruct a different conversation from the live request.
Keep history append-only and add a bounded contextual fragment instead of
deleting prior items.
```

### 触发路径

```text
sampling 1: model emits function_call(call-1)
tool executes
history appends FunctionCallOutput(call-1)
sampling 2: line 205 deletes the result before request construction
model sees an unmatched/altered history
```

### 为什么不是与 Finding 1 重复

- Finding 1：容量无界；即使不删除历史也会发生。
- Finding 2：历史语义被重写；即使 context 很小也会发生。

它们需要不同修复和测试，所以应分别报告。

---

## 15. Finding 3：[P1] 迟到的旧 TurnComplete 会清掉新 Active Turn

评论位置：

```text
codex-rs/app-server/src/thread_state.rs:315
```

Finding：

```text
[P1] Only clear the active turn that matches the terminal event

This unconditionally clears `active_turn`. If turn A's completion is processed
after queued turn B has already become active, A's late terminal event removes
B from the live projection and the thread can be reported idle while B is still
running. Compare the active turn ID with `event.turn_id` and only clear the
matching turn; keep late completion data associated with A.
```

### 精确交错

```text
1. A task body ends
2. queue starts B and sets active_turn = B
3. A TurnComplete reaches projection late
4. line 315 sets active_turn = None
5. B continues running, UI/status says Idle
```

这比“可能有 race”更有说服力，因为它给出了两个 turn 和确切事件顺序。

### 优先级为什么是 P1

它会让 thread 的公开运行状态错误，可能导致客户端允许冲突操作、错误调度或丢失 B 的 UI 生命周期。影响严重，但不像无界上下文规则那样被仓库明确标记为 P0。

---

## 16. Finding 4：[P1] v2 Notification 违反 Payload 约定

评论位置：

```text
codex-rs/app-server-protocol/src/protocol/v2/turn.rs:92-93
```

Finding：

```text
[P1] Keep v2 notification fields present on the wire

V2 response/notification payloads must not use `skip_serializing_if`; this makes
`toolContext` disappear for empty turns even though the generated type is a
non-optional `Vec<String>`. Clients generated from the schema can therefore
receive a payload that violates their required-field contract. Serialize an
empty array (or redesign the field according to the v2 compatibility rules),
then regenerate and review the app-server schema fixtures.
```

### 这里有两个不同问题

1. 当前 Rust/TS/schema 契约自相矛盾：非 optional 字段却在 runtime 省略；
2. 作者没重新生成 schema。

可以合并为一条 finding，因为同一修复动作需要移除 skip 并更新 schema。不要重复写两条相同根因的评论。

### 新字段本身是否一定 breaking

不一定。新增 response/notification 字段对能忽略未知字段的旧客户端通常是 additive；但新客户端连接旧 server、严格 decoder、生成类型和字段省略行为都需要明确兼容分析。

这里能确定的 bug 是：当前声明 required，却在空值时省略。

---

## 17. Finding 5：[P1] 配置重命名让旧限制静默失效

评论位置：

```text
codex-rs/core/src/config/config_toml.rs:502
```

Finding：

```text
[P1] Preserve the existing `tool_output_limit` configuration during migration

Renaming the field without a serde alias or migration makes existing
`tool_output_limit` settings stop controlling the feature. Those users either
fail config parsing or silently fall back from their 32 KiB limit to the new
1 MiB default, which is a large behavior and resource-usage change. Continue to
accept the old key (with a documented precedence/deprecation plan), preserve
the old default unless separately approved, and regenerate the config schema.
```

### 为什么默认变化也要写进去

即使加了 alias，32 KiB → 1 MiB 仍是独立的行为扩大。如果 PR 没解释依据，reviewer 应要求拆开或证明必要性。

### Byte cap 不是 Token cap

修配置兼容仍不能关闭 Finding 1。字节数和 token 数没有固定一一映射，且 1 MiB 明显可能超过 model-context 单项上限。

---

## 18. Finding 6：[P2] 测试没有覆盖 Agent 与 App-server 的公开行为

评论位置可以贴在新增测试文件最相关的测试定义上。

Finding：

```text
[P2] Add integration coverage for the prompt and v2 notification paths

The new tests only prove that a Vec can be drained and that the serializer
omits a field; they never run an Agent sampling loop or an app-server client.
They would pass while tool-call/result history is removed, the model request is
unbounded, or a late completion clears the wrong active turn. Add a `test_codex`
integration test that inspects consecutive model requests, plus a v2 JSON-RPC
test covering terminal status and the generated notification payload. Any
user-visible TUI rendering change also needs snapshot coverage.
```

### 为什么是 P2 而不是“没有测试所以 P1”

具体的 P0/P1 行为 bug 已经单独报告。这条 finding 保护的是 PR 的验证策略和尚未覆盖的真实连接。它是重要合并要求，但优先级不应与已证明的严重运行时错误重复膨胀。

---

## 19. Finding 7：改动规模应该怎样处理

1,146 行中既有 Core context、App-server API、Config 和 UI。如果大部分是手写语义代码，这超过非机械 800 行、复杂逻辑 500 行的指导。

这更适合作为 change-size finding 或总体 review summary：

```text
[P2] Split the context plumbing from the public v2/UI exposure

This PR combines new model-context semantics, Session storage, a config rename,
an app-server wire change, and UI work in 1,146 changed lines. These pieces have
different owners and failure modes, and the context behavior can land and be
tested independently before public exposure. Please split the first coherent
stage into the bounded `ContextualUserFragment` plus Core integration coverage;
follow with v2/UI exposure after its wire contract is agreed.
```

### 最小可落地阶段

```text
PR 1：有界 ContextualUserFragment + Core integration tests
PR 2：App-server additive wire exposure + schema/tests
PR 3：TUI visualization + snapshots
PR 4：配置迁移（如果仍需要）
```

拆分不是为了数字好看，而是让各层能独立设计、验证和回滚。

---

## 20. 还有哪些内容目前只能问，不能直接报 Bug

### 20.1 `last_tool_context` 是否需要持久化

如果产品契约只要求 live diagnostic，它可能无需进入 rollout。没有 PR 需求和恢复语义时，应先问：

```text
Is this field intended to survive resume, or is it explicitly live-only?
```

不能只因为“看起来应该持久化”就报 P1。

### 20.2 工具输出中是否包含秘密

这是重要安全问题，但需要看现有 output redaction、telemetry 和 API exposure policy。可以要求 threat analysis；若 diff 明确把 raw secrets 发给新客户端，才形成具体 finding。

### 20.3 `lock().await` 是否死锁

片段在拿锁后没有另一个 `.await`，不能仅凭异步 mutex 就报告死锁。真正 review 要继续看 `take_recent_tool_context()` 是否同步、`Prompt::new` 是否无 await，以及调用链锁顺序。

### 20.4 Vec 是否一定内存泄漏

它会无界增长，这是有效容量问题，但 Rust 仍可能在 `take` 或 Session drop 时释放。不要错误使用“memory leak”这个术语；更准确是 unbounded retention/growth。

---

## 21. 为什么不能只审 Changed Lines

Diff 告诉你改了哪里，bug 常需要 unchanged context 才能证明。

对于 line 315，需要继续看：

- active turn 是否带 ID；
- 新 turn 何时启动；
- terminal event 是否保证顺序；
- listener/replay 是否可能迟到；
- TurnAborted 是否走同一路径。

对于 config rename，需要搜索：

```bash
rg -n 'tool_output_limit|recent_context_bytes' .
```

检查：

- config schema；
- requirements layer；
- example config；
- tests；
- managed config；
- CLI override；
- documentation。

Review 要扩展到必要上下文，但不要无边界阅读整个仓库。

---

## 22. 一套高效 Review 顺序

### 第一遍：理解意图和规模

```text
PR title/body
→ issue/reproduction
→ diff stat
→ changed files by subsystem
→ AGENTS.md / local instructions
```

### 第二遍：找公开边界

```text
API/wire
CLI
config
persistence/resume
model-visible context
permissions/security
```

### 第三遍：追状态与数据流

```text
谁创建数据？
谁拥有它？
谁修改它？
是否有大小上限？
事件是否带 identity？
错误/取消/迟到事件怎样处理？
```

### 第四遍：审测试

```text
测试是否在正确层？
旧实现会失败吗？
覆盖公开行为吗？
边界、并发、失败和兼容性呢？
生成物与 snapshot 呢？
```

### 第五遍：整理 Findings

- 合并重复根因；
- 删除不能证明的问题；
- 校准优先级；
- 收紧行号；
- 让每条评论能独立理解；
- 最严重 finding 在前。

---

## 23. 审查 Model-visible Context 的专用清单

仓库规则要求：

- [ ] History 只增量构建，不重写旧项；
- [ ] 避免频繁变化造成 prompt cache miss；
- [ ] 每个注入来源都有数量/大小 hard cap；
- [ ] 单项不超过 10K tokens；
- [ ] 可能超过 1K tokens 的单项按 P0 额外审查；
- [ ] Fragment 定义在 `core/context`；
- [ ] 实现 `ContextualUserFragment`；
- [ ] 截断方式保持语义和来源；
- [ ] 工具结果不被伪装成用户授权；
- [ ] Compaction/resume 后语义一致；
- [ ] 有 integration test 检查真实 outbound model request。

### 23.1 为什么 cache miss 也是 review 问题

在 Prompt 前缀中每次插入不同临时内容，会让后续请求无法复用稳定 prefix。即使功能正确，也可能显著增加延迟和成本。

但性能 finding 需要证据：指出注入位置、每 step 变化方式和缓存前缀怎样被破坏，不要只写“可能变慢”。

---

## 24. 审查 Breaking Change 的专用清单

### App-server

- method/field/variant 是否重命名或删除？
- serde 与 TS rename 是否一致？
- v2 Params optional 是否 `nullable`？
- response/notification 是否错误省略字段？
- schema fixture 是否更新？
- notification ordering/terminal semantics 是否变化？

### CLI

- flag、默认值、exit code 是否变化？
- stdout/stderr 是否被脚本依赖？
- 非交互行为是否开始等待输入？

### Config

- 旧 key/value 是否继续解析？
- 默认和 layer precedence 是否变化？
- managed requirement 能否被削弱？
- `config.schema.json` 是否更新？

### Rollout/Resume

- 旧 JSONL/DB 是否反序列化？
- replay 是否重建相同 history/state？
- fork/reference 是否保持边界？
- terminal events 是否仍可恢复？

不要找到第一个 breaking issue 就停止；四类 surface 都要过一遍。

---

## 25. 审查并发状态机的专用清单

| 问题 | 例子 |
|---|---|
| Identity | terminal event 是否只修改相同 turn/call？ |
| Ordering | Begin/End、old/new turn 是否可能交错？ |
| Late event | 旧事件晚到会覆盖新状态吗？ |
| Duplicate event | 重试后是否双重完成/双重释放？ |
| Cancellation | token 触发后 pending sender/guard 是否清理？ |
| Lock scope | 持锁期间是否进行外部 await？ |
| Lock order | 两个路径是否反向获取锁？ |
| Channel close | receiver 消失时 sender 怎样结束？ |
| Idempotence | 同一 terminal event 重放两次会怎样？ |
| Backpressure | producer 是否能无界领先 consumer？ |

### 25.1 写出 Event Schedule

发现 race 时，用编号交错证明：

```text
1. A reads active = A
2. B sets active = B
3. A completion clears active unconditionally
4. observer reads Idle while B runs
```

没有 schedule 的“可能 race”通常需要继续调查。

---

## 26. 审查测试的专用清单

仓库 testing guidance 强调：

- Agent 逻辑优先 integration test；
- 重大用户行为必须列出并测试；
- unit test 放独立 `*_tests.rs`；
- 避免生产代码中的 test-only helper；
- 先找现有 helper；
- UI 变化必须 snapshot coverage；
- 优先完整对象 equality；
- 不测试静态定义值；
- 不为删除逻辑写无意义负向测试。

### 26.1 测试通过仍可能完全没覆盖 Bug

教学 unit test 证明：

```text
Vec push/take 能工作
serializer 确实省略空字段
```

它没有证明：

```text
模型 history 保持成对
context 有 token cap
迟到 turn 不清错状态
v2 client 能解析 payload
旧 config 仍生效
resume 行为一致
```

审测试时要从用户行为反推，而不是数 test functions。

---

## 27. 怎样写一条高质量 Inline Finding

结构：

```text
[Priority] Imperative title

Trigger + incorrect behavior + user impact + minimal direction.
```

### 27.1 标题

```text
[P1] Match terminal events to the active turn
```

标题应表达要保护的行为，不要写：

```text
[P1] Bug here
```

### 27.2 Body

一段通常足够。不要写成长篇设计文档，也不要只写一句结论。

### 27.3 行号

选择引入问题的最小范围。一般 1–5 行，不要把整文件 1–600 行标红。

### 27.4 语气

陈述证据和影响：

```text
If A completes after B starts, this clears B...
```

避免评价作者：

```text
You clearly did not think about concurrency.
```

---

## 28. False Positive 是怎样产生的

### 28.1 没读调用方

Reviewer 说输入可能为空，但调用前已有强校验。

### 28.2 忽略类型不变量

Reviewer 报 `None.unwrap()`，但类型构造器保证该状态不可能出现，且字段私有。

### 28.3 把平台差异当成全平台 bug

某命令只在 `#[cfg(unix)]` 路径运行，Windows 结论不适用。

### 28.4 把理论风险当成可触发缺陷

“usize 可能溢出”但输入有远低于上限的 parser cap。

### 28.5 报告 pre-existing issue

Diff 只移动代码，没有改变该行为。可以另开 issue，但不应说 PR 引入。

### 28.6 把文档偏好当 correctness

“我会用另一种模式”不是阻塞理由，除非仓库约定或可观察问题支持。

---

## 29. 怎样验证 Finding 不是误报

对每一条问：

1. 本次 diff 改了哪一行，使问题成为可能？
2. 输入/状态怎样到达这行？
3. 哪个现有 guard 没有拦住？
4. 错误结果在哪里被观察？
5. 是所有平台还是特定平台？
6. 现有测试是否已覆盖并反驳？
7. 最小复现或 event schedule 是什么？
8. 修复后哪项不变量恢复？

无法回答时，把 finding 降为 question 或继续调查。

---

## 30. Finding 应按根因去重

教学 diff 中，无界 Vec 会造成：

- 内存增长；
- Prompt 过大；
- 请求失败；
- 延迟增加；
- 费用增加。

不应为每个症状写五条评论。如果主要合并阻断点是“无硬 cap 的 model-visible item”，一条完整 P0 更清楚。

但历史删除是另一个独立根因，不能因为也发生在 prompt.rs 就合并掉。

去重标准：

```text
同一个最小代码修改能否同时解决？
```

如果能，通常是一条 finding；如果需要不同修复和测试，通常应分开。

---

## 31. Review Summary 应怎样写

Findings 是主输出。Summary 可以帮助作者理解总体风险：

```text
The PR has three blocking correctness/compatibility issues: the model-visible
fragment is unbounded and rewrites history, late terminal events can clear a
newer turn, and the v2/config migrations violate existing contracts. The
current unit tests do not exercise the Agent or JSON-RPC paths. I recommend
splitting bounded Core context plumbing from v2/UI exposure before proceeding.
```

不要用 summary 稀释 finding，也不要在 summary 突然新增没有行号的重要问题。

如果没有 findings，直接说明未发现 actionable issue；不要为了显得努力而创造 nit。

---

## 32. 一份完整教学 Review 输出

```text
1. [P0] Bound tool context before adding it to the model prompt
   codex-rs/core/src/session/prompt.rs:207

2. [P1] Preserve previously recorded tool outputs in incremental history
   codex-rs/core/src/session/prompt.rs:205

3. [P1] Only clear the active turn that matches the terminal event
   codex-rs/app-server/src/thread_state.rs:315

4. [P1] Keep v2 notification fields present on the wire
   codex-rs/app-server-protocol/src/protocol/v2/turn.rs:92

5. [P1] Preserve the existing configuration during migration
   codex-rs/core/src/config/config_toml.rs:502

6. [P2] Add integration coverage for the prompt and v2 notification paths
   <new test file>: first relevant test

7. [P2] Split context plumbing from public v2/UI exposure
   PR-level change-size finding
```

顺序遵循：P0 → P1 → P2；同级中先 correctness/security，再 compatibility/testing/scope。

---

## 33. 作者怎样处理这份 Review

建议迭代：

### 第一阶段

- 撤出 v2/UI 和 config rename；
- 在 `core/context` 定义 bounded fragment；
- 不重写 history；
- 加 Core integration test；
- 保证单项 token hard cap。

### 第二阶段

- 独立设计 additive v2 field；
- 遵守 v2 serialization rules；
- 更新 schema；
- 加 JSON-RPC integration tests；
- 分析 old/new client compatibility。

### 第三阶段

- UI 展示与 snapshots；
- 明确敏感数据 redaction；
- 决定 live-only 还是持久化语义。

### 配置

只有确实需要用户配置时再单独设计，保留旧 key/default 或给出迁移策略。

---

## 34. 这套审查规则怎样影响本章

使用的四套仓库 review 规则分别决定了：

| 规则 | 本章中的作用 |
|---|---|
| `code-review-context` | 把无 hard cap 和 history rewrite 定为核心阻断问题 |
| `code-breaking-changes` | 强制检查 app-server、CLI、config、rollout，而不是找到一个就停止 |
| `code-review-change-size` | 对 1,146 行跨层 semantic diff 提出 staged landing |
| `code-review-testing` | 要求 Agent integration test、独立 unit test file、复用 harness |

它们影响的不只是最后评论格式，而是 reviewer 搜索问题的路线。

---

## 35. 本章词汇表

完整总表见[课程术语表](glossary.md)。

| 名词 | 常见写法 | 通俗解释 |
|---|---|---|
| Code review | review | 合并前检查正确性、兼容性、安全、测试和维护成本 |
| Finding | finding | 有触发路径、影响、位置和修复方向的可执行问题 |
| Blocking | blocking | 合并前必须处理 |
| Non-blocking | suggestion | 可改进但不必阻止当前合并 |
| Priority | P0/P1/P2/P3 | 根据影响、概率和可恢复性标记处理紧迫度 |
| Severity | severity | 问题造成后果的严重程度 |
| Trigger | trigger | 让缺陷实际发生的输入、事件或状态 |
| User impact | impact | 缺陷对用户、数据、兼容性或系统造成的结果 |
| Inline comment | inline finding | 贴在具体 diff 行上的评审意见 |
| Tight line range | tight range | 只覆盖直接引入问题的少量代码行 |
| False positive | false positive | 看起来像问题，实际被上下文不变量或 guard 排除 |
| Root-cause deduplication | dedup | 合并由同一个根因产生的重复评论 |
| Event schedule | interleaving | 用编号说明并发事件怎样交错触发错误 |
| Late event | stale/late event | 属于旧状态、在新状态开始后才到达的事件 |
| Idempotence | idempotent | 同一动作重复应用不会进一步改变正确结果 |
| Unbounded retention | unbounded growth | 数据没有硬上限，随时间/输入持续占用资源 |
| History rewrite | history rewrite | 删除或修改已经建立的模型可见历史 |
| Cache miss | prompt cache miss | 变化的前缀使已有 prompt cache 无法复用 |
| Wire contract | wire contract | 客户端与服务端共同依赖的序列化形状与语义 |
| Staged landing | staged rollout | 把大改动拆成可独立合并验证的阶段 |

### 代码审查单词短语拆解

- `review`：评审；在合并前系统检查改动。
- `reviewer`：评审者；负责判断 diff 是否达到合并标准的人。
- `author`：作者；提出并维护 PR 的人。
- `finding`：发现项；需要作者采取行动的具体问题。
- `actionable`：可执行的；评论足够具体，作者知道怎样调查或修复。
- `blocking`：阻塞的；不解决就不应合并。
- `non-blocking`：非阻塞的；建议改进但可另行处理。
- `nit`：小意见；通常是轻微风格或文字建议。
- `priority`：优先级；应多快、多坚决地处理。
- `severity`：严重度；错误发生后后果有多大。
- `likelihood`：发生可能性；触发条件多常见。
- `recoverability`：可恢复性；错误后能否轻易恢复。
- `trigger`：触发器；让问题从理论变成实际的条件。
- `impact`：影响；最终伤害到什么。
- `repro`：reproduction，复现；稳定触发问题的步骤。
- `line range`：行范围；inline comment 关联的代码区域。
- `tight`：紧凑；只选择直接相关行。
- `context`：上下文；理解 changed line 必须读取的周边定义和调用方。
- `changed line`：变更行；本次 diff 新增或修改的行。
- `pre-existing`：既有的；PR 之前已经存在。
- `false positive`：误报；结论看似合理但实际不成立。
- `guard`：守卫条件；在危险操作前排除不合法状态。
- `invariant`：不变量；所有合法状态都必须满足的规则。
- `interleaving`：交错；并发事件穿插执行的顺序。
- `schedule`：调度顺序；证明 race 可触发的事件序列。
- `stale`：陈旧；属于旧版本、旧 turn 或旧 catalog。
- `late`：迟到；在预期时间之后到达。
- `idempotent`：幂等；重复应用仍得到同一结果。
- `unbounded`：无界；没有最大数量/大小/时间限制。
- `retention`：留存；数据继续被对象持有而未释放。
- `rewrite`：重写；改变已经建立的历史内容。
- `append-only`：只追加；已有历史不修改，只在尾部增加。
- `wire`：线路上的；序列化后跨进程/网络传输的表示。
- `contract`：契约；调用双方共同依赖的稳定规则。
- `additive`：增加式兼容；新增能力而尽量不破坏旧调用方。
- `deduplicate`：去重；合并同一根因的重复发现。
- `summary`：总结；概括主要风险，不替代具体 findings。
- `staged landing`：分阶段落地；按依赖顺序合并多个独立安全阶段。

---

## 36. 自测题

1. Blocking finding、suggestion 和 question 有什么区别？
2. 一条有效 finding 通常需要哪五类证据？
3. P0/P1/P2/P3 应按什么因素判断？
4. 为什么教学 diff 的无界 model context 是 P0？
5. 无界上下文与 history rewrite 为什么是两个 finding？
6. 怎样用 event schedule 证明旧 turn 清掉新 turn？
7. v2 required Vec 与 `skip_serializing_if` 为什么冲突？
8. 新增 response 字段是否一定 breaking？还需检查什么？
9. Config rename 为什么可能让资源限制静默失效？
10. Byte cap 为什么不能代替 token hard cap？
11. 两个 unit test 为什么不能证明 Agent/app-server 路径安全？
12. 1,146 行 PR 应按什么依赖阶段拆分？
13. 哪些问题目前只能问，不能直接报 bug？
14. 为什么 review 不能只读 changed lines？
15. Model-visible context 有哪些专用审查规则？
16. Breaking-change review 为什么不能找到一个问题就停止？
17. 并发 finding 为什么最好提供编号交错？
18. 怎样验证 finding 不是 false positive？
19. 怎样判断两条评论是否应按根因去重？
20. Inline finding 的 title、body 和 line range 应怎样写？
21. 如果 review 没发现 actionable issue，应该怎样输出？

---

## 37. 源码与规则检查点

1. 根目录 `AGENTS.md`
   - 对照 model-visible context、breaking changes、testing、change size。
2. `.codex/skills/code-review-context/SKILL.md`
   - 逐条核对 history、cache、hard cap、10K/1K token 和 fragment 类型。
3. `.codex/skills/code-review-breaking-changes/SKILL.md`
   - 核对 app-server、CLI、config 和 resume surfaces。
4. `.codex/skills/code-review-change-size/SKILL.md`
   - 核对 800/500 行与最小 coherent stage。
5. `.codex/skills/code-review-testing/SKILL.md`
   - 核对 integration test、`test_codex` 和 test-only helper。
6. `codex-rs/core/src/context/`
   - 找 `ContextualUserFragment` 的真实实现和大小边界。
7. `codex-rs/core/src/context_manager/`
   - 看 history 怎样增量维护、normalize 和构造模型输入。
8. `codex-rs/core/src/tools/context.rs`
   - 看 tool output 怎样截断并转换成 `FunctionCallOutput`。
9. `codex-rs/core/src/session/turn.rs`
   - 看 step/sampling 如何构造 Prompt 和追加结果。
10. `codex-rs/app-server-protocol/src/protocol/v2/`
    - 检查 Params/Response/Notification 的 serde/TS 约定。
11. `codex-rs/app-server/src/thread_state.rs`、`thread_status.rs`
    - 看 turn identity、terminal event 和 status projection。
12. `codex-rs/config/src/config_toml.rs`
    - 看配置字段、serde、默认值和生成 schema 来源。
13. `codex-rs/core/tests/common/responses.rs`
    - 看 outbound model request 的结构化 assertion helpers。
14. `codex-rs/core/tests/common/test_codex.rs`
    - 看 Agent integration harness。
15. `codex-rs/app-server/tests/suite/v2/thread_status.rs`
    - 看公开 JSON-RPC 状态测试。
16. `codex-rs/tui/src/chatwidget/tests/app_server.rs` 与 snapshots
    - 看 UI notification 和可见输出覆盖。

---

## 38. 一句话总结

代码审查的核心不是罗列所有可疑之处，而是把本次 diff 引入的变化放回真实数据流、状态机和公开契约中，用具体触发路径证明用户影响；随后按严重度排序、按根因去重、给出紧凑行号和最小修复方向，并用 model-context、breaking-change、并发、测试和 change-size 清单确保不会因为找到一个显眼 bug 就漏掉其他独立的合并风险。
