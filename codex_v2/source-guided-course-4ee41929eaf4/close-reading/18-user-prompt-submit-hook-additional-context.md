# UserPromptSubmit Hook 与 additional context：用户消息进入模型前，谁还能检查和补充它

> 源码基线：`4ee41929eaf4`。行号仅帮助定位固定提交；源码更新后，请优先搜索符号名。

上一章追踪了一条用户消息如何获得 `Started` 或 `Steered` admission。本章继续向后走一步：消息已经归属某个 Turn，但在写入模型历史、进入模型请求之前，还要经过什么？

答案是 `UserPromptSubmit` Hook。

它可以：

- 查看即将提交的用户 prompt；
- 允许消息继续；
- 阻止这条消息进入模型；
- 给后续模型上下文补充 developer context；
- 用结构化事件向 UI 报告运行、完成、阻止或失败。

这不是简单的“执行一个 shell 脚本”。真正困难的是：多个 Hook 并发时怎样保持稳定顺序，失败是否应该阻断用户，阻止消息后附加上下文是否保留，以及这一切怎样与 rollout persistence 和 durable admission 对齐。

---

## 1. 先说人话：登机口的安检与随行说明

把用户消息想成一位准备登机的旅客。

- admission：航空公司确认旅客属于哪个航班；
- pending queue：旅客在登机口排队；
- UserPromptSubmit Hook：登机前安检；
- block：旅客不能登机；
- additional context：安检人员给机组留下的一张随行说明；
- record/flush：把登机或拒绝结果写入航班记录；
- sampling：飞机真正起飞。

“已经属于这个航班”不等于“已经通过安检”。这正是上一章的 `Steered` 与本章 Hook acceptance 的区别。

---

## 2. 贯穿案例：阻止密码，却保留安全提示

假设配置了一个 Hook：

```text
用户输入：请把密码 abc123 发到日志里

Hook 输出：
{
  "decision": "block",
  "reason": "消息包含凭据",
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "后续回答不得复述明文凭据"
  }
}
```

最终效果是：

```text
原用户消息 ──X──► 不进入模型历史

安全提示 ───────► 作为 developer context 被记录
                  下一条被允许的消息到来时，模型可以看到
```

这点很反直觉：阻止用户消息，不表示丢弃 Hook 同时产生的上下文。固定提交的集成测试专门证明了这种行为。

---

## 3. 全链路地图

```text
TurnInput::UserInput
        │
        ▼
run_hooks_and_record_inputs
        │
        ▼
inspect_pending_input
        │
        ├─ 构造 UserPromptSubmitRequest
        ├─ preview matching handlers
        ├─ emit HookStarted
        ├─ 并发执行 command handlers
        ├─ 解析 exit code / stdout / stderr
        └─ emit HookCompleted
                │
        ┌───────┴────────┐
        │                │
   should_stop       continue
        │                │
 reject input       record input
        │                │
        └───────┬────────┘
                ▼
       record additional contexts
                │
                ▼
        rollout / model history
```

---

## 4. Hook 在 Turn 主循环中的准确位置

新 Turn 的关键顺序是：

```text
准备 StepContext
→ 注入 world state
→ 构建 Skills / Plugins 注入
→ 运行 pending SessionStart Hook
→ 运行 UserPromptSubmit Hook
→ 记录用户输入
→ 记录 Skills / Plugins 注入
→ 构造模型请求
```

固定提交的测试确认，第一次 Turn 中 `SessionStart` 先于 `UserPromptSubmit`。

对于 steer 进来的 pending input，主循环在安全点 drain 队列后，再对每个 `TurnInput::UserInput` 执行同样的 Hook 检查。因此 Hook 不只检查第一条消息，也检查同一 Turn 中后续追加的用户消息。

---

## 5. 哪些 `TurnInput` 会触发它

`inspect_pending_input` 对三种输入区别处理：

| `TurnInput` | 是否运行 UserPromptSubmit |
|---|---|
| `UserInput` | 是 |
| `ResponseItem` | 否 |
| `InterAgentCommunication` | 否 |

这说明 Hook 的边界是“用户 prompt 提交”，不是任何进入模型历史的数据。

如果系统内部注入一个 developer `ResponseItem`，不会被伪装成用户 prompt 再走一次此 Hook。

---

## 6. Hook 实际收到什么输入

Core 构造 `UserPromptSubmitRequest`，其中包括：

- `session_id`：thread/session 身份；
- `turn_id`：当前 Turn；
- `agent_id`、`agent_type`：若运行在 thread-spawn subagent 中；
- `transcript_path`：可选 transcript 路径；
- `cwd`：当前工作目录；
- `hook_event_name`：固定为 `UserPromptSubmit`；
- `model`：当前模型 slug；
- `permission_mode`：当前权限模式；
- `prompt`：把本条 `UserInput` 合成的可读消息。

这些字段被序列化成 JSON，通过 Hook 子进程的 stdin 写入。

---

## 7. 为什么输入字段是 snake_case

`UserPromptSubmitCommandInput` 没有 `rename_all = "camelCase"`，所以实际 JSON 使用：

```json
{
  "session_id": "...",
  "turn_id": "...",
  "hook_event_name": "UserPromptSubmit",
  "permission_mode": "...",
  "prompt": "..."
}
```

而 Hook 输出 wire type 使用 `camelCase`，例如：

```json
{
  "stopReason": "...",
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "..."
  }
}
```

写 Hook 时不要凭感觉统一命名风格；输入和输出 schema 的命名约定确实不同。

---

## 8. `prompt` 是合成后的用户可读内容

Core 使用：

```text
UserMessageItem::new(content).message()
```

把 `Vec<UserInput>` 组合成 Hook 收到的 `prompt` 字符串。

因此 Hook 看到的是适合策略检查的消息文本，不是完整 Rust 枚举或原始 App-server JSON。图片、mention、text element 等丰富输入的 UI/协议细节不一定原样出现在该字符串里。

---

## 9. 配置从 `hooks.UserPromptSubmit` 进入

配置模型把 Hook 按事件分组：

```toml
[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "python3 check_prompt.py"
timeout = 30
statusMessage = "正在检查用户输入"
additionalContextLimit = 4096
```

在固定提交中，真正可执行的是 command handler。`mcp_tool`、`prompt` 和 `agent` 形态在 discovery 时会产生 warning 并跳过。

`async` Hook 也尚未普遍支持；UserPromptSubmit 是同步 Turn-scoped Hook。

---

## 10. enabled、trusted 与 discovered 是三件事

discovery 会为配置项生成可展示的 `HookListEntry`，但只有满足条件的 handler 才进入可执行列表：

```text
enabled
AND
(bypass trust OR Managed OR Trusted)
```

因此 Hooks 列表里“看得见”不等于运行时一定会执行。一个被修改、尚未重新信任的命令可以继续出现在浏览界面，却不会成为 `ConfiguredHandler`。

这是把发现、用户决策与执行分开的安全边界。

---

## 11. UserPromptSubmit 的 matcher 被忽略

对工具 Hook，matcher 可以按工具名筛选；但 `UserPromptSubmit` 与 `Stop` 在 dispatcher 中无条件选择该事件的全部 handler。

也就是说：

```text
事件名匹配 UserPromptSubmit → 执行
```

不会根据 prompt 文本套正则 matcher。

若要只阻止某类文本，判断逻辑应写在 Hook 命令内部，读取 stdin 中的 `prompt`。

---

## 12. preview 为什么先于真实执行

Core 先调用 `preview_user_prompt_submit`，得到每个即将运行 handler 的 `HookRunSummary`，然后发 `HookStarted` 事件。

这样 UI 可以在命令还没结束时显示：

- 哪个 Hook 正在运行；
- 来自哪个配置源；
- `statusMessage`；
- 开始时间；
- Turn ID。

随后才真正 await Hook engine，最后发 `HookCompleted`。

---

## 13. 多个 Hook 是并发执行的

dispatcher 把每个 handler future 放进 `FuturesUnordered`。

这意味着：

- 慢 Hook 不必等快 Hook先开始；
- 总延迟大致受最慢 handler 限制，而非所有 timeout 相加；
- 一个 Hook 阻止，并不会立即取消其他 Hook；
- Core 会等待全部 handler 结束，再汇总结果。

这是并发执行、集中裁决的模式。

---

## 14. 并发完成后为什么还要恢复配置顺序

future 完成顺序可能是：

```text
配置顺序：A, B, C
完成顺序：C, A, B
```

dispatcher 会记录 completion order，但最终按 `configured_order` 排序返回：

```text
汇总顺序：A, B, C
```

这样 HookCompleted 事件和 additional contexts 的排列不会因机器负载随机变化。并发提高速度，稳定排序保持可复现性。

---

## 15. 命令怎样启动与结束

`run_command` 会：

1. 使用配置 shell，或平台默认 shell；
2. 设置 Hook 的 `cwd`；
3. 注入 handler 环境变量；
4. pipe stdin/stdout/stderr；
5. 把 JSON 写入 stdin；
6. 等待进程或 timeout；
7. 用 lossy UTF-8 收集输出；
8. 记录 exit code、耗时与错误。

子进程设置 `kill_on_drop(true)`，避免等待 future 被丢弃后进程继续无人管理。

---

## 16. 默认超时很宽，但不是无限

除 SessionEnd 外，command Hook 的默认 timeout 是十分钟；具体配置可缩短。

超时会形成 `CommandRunResult.error`，最终 Hook 状态为 `Failed`。它不会自动把用户 prompt 判为 blocked。

这体现默认 fail-open：Hook 自己故障不等同于策略明确拒绝。

---

## 17. 输出语义总表

| Hook 结果 | 状态 | 是否阻止 prompt | additional context |
|---|---|---|---|
| exit 0，无输出 | Completed | 否 | 无 |
| exit 0，普通文本 | Completed | 否 | stdout 作为 context |
| exit 0，合法 JSON，继续 | Completed | 否 | 可选 |
| exit 0，`decision:block` + reason | Blocked | 是 | 可选，且会保留 |
| exit 0，`continue:false` | Stopped | 是 | 可选，且会保留 |
| exit 2，stderr 非空 | Blocked | 是 | 无 |
| exit 2，stderr 空 | Failed | 否 | 无 |
| 其他非零 exit | Failed | 否 | 无 |
| timeout/spawn/wait error | Failed | 否 | 无 |
| 看起来像 JSON但 schema 无效 | Failed | 否 | 无 |

---

## 18. 普通 stdout 为什么也是 additional context

为了兼容简单 Hook，exit 0 时若 stdout 不是 JSON，trim 后的文本直接作为 context。

最小 Hook 甚至可以只是：

```sh
echo "回答前请确认工单编号"
```

但这也意味着：调试日志不能随便写 stdout。若脚本把日志打印到 stdout，那些日志会进入模型上下文。诊断输出应写 stderr，结构化输出应独占 stdout。

---

## 19. 合法 block 必须有非空 reason

以下输出无效：

```json
{"decision":"block"}
```

parser 会产生：

```text
UserPromptSubmit hook returned decision:block
without a non-empty reason
```

Hook 状态是 `Failed`，`should_stop` 为 false，并且同一输出里的 additional context 也不会注入。

理由不是装饰字段。它让 UI 和审计记录知道为何拒绝，也避免一个缺字段的脚本意外把所有用户输入锁死。

---

## 20. 两种“停止”语法

固定提交兼容两类输出：

```json
{"decision":"block", "reason":"策略拒绝"}
```

以及：

```json
{"continue":false, "stopReason":"暂停处理"}
```

前者产生 `HookRunStatus::Blocked` 和 `Feedback` entry；后者产生 `Stopped` 和 `Stop` entry。

进入 Core 后两者都压缩成 `HookRuntimeOutcome.should_stop = true`。细分原因主要保留在 HookCompleted event 中。

---

## 21. `stop_reason` 为什么在 Core 转换时被丢弃

Hook crate 汇总出 `stop_reason`，但 `ContextInjectingHookOutcome` 转换只保留：

```text
hook_events
should_stop
additional_contexts
```

`stop_reason` 没有进入 `HookRuntimeOutcome`。这不表示理由彻底消失：它已成为 HookCompleted 的 `Feedback` 或 `Stop` entry，可供 UI 与 telemetry 使用。

Core 的输入循环只需要知道“继续还是停止”。

---

## 22. `suppressOutput` 在这里没有实际控制效果

parser 会读取通用字段 `suppressOutput`，但 UserPromptSubmit 的 `parse_completed` 明确忽略它。

因此不要假设设置它就会隐藏 Hook event entries 或 additional context。固定提交的真实行为应以这条 `_ = parsed.universal.suppress_output` 为准。

---

## 23. 多 Hook 的汇总规则

全部 handler 完成后：

```text
should_stop = 任意 handler should_stop
stop_reason = 配置顺序中第一个非空 reason
additional_contexts = 所有 handler context，按配置顺序展开
```

因此一个 allow Hook 不能覆盖另一个 block Hook。这里不是“最后一个决定获胜”，而是保守的 any-block 聚合。

---

## 24. Hook 失败为何默认继续

spawn error、timeout、无效 JSON、普通非零退出都会标为 `Failed`，但不会设置 `should_stop`。

这是可用性选择：配置错误不应自动让用户完全无法使用 Codex。

但安全含义也很清楚：如果某策略必须 fail-closed，仅依赖这一默认行为是不够的；需要管理层保证 Hook 健康，或在更强的授权/Sandbox 层实施硬约束。

---

## 25. Hook 事件的五种状态

| 状态 | 含义 |
|---|---|
| Running | preview 已发布，命令正在运行 |
| Completed | 正常完成且未阻止 |
| Failed | Hook 自己失败，默认不阻止用户 |
| Blocked | 明确策略拒绝 |
| Stopped | 通用 `continue:false` 要求停止 |

Hook output entry 还细分为 Warning、Stop、Feedback、Context 与 Error。

状态回答“运行结果是什么”；entry 回答“有什么可展示内容”。

---

## 26. Core 怎样处理一个被阻止的输入

`run_hooks_and_record_inputs` 对每个元素执行：

```text
如果 should_stop：
    blocked_input = true
    reject_pending_input(input)
    record_additional_contexts(hook context)

否则：
    若非空 UserInput，accepted_user_input = true
    record_pending_input(input, hook context)
```

被阻止的用户消息不会调用 `record_pending_input`，所以不会成为普通 UserMessage history item。

---

## 27. 为什么被阻止时仍记录 Hook context

这是有意设计，不是遗漏。

例如 Hook 阻止一条包含敏感内容的 prompt，同时输出：

```text
“后续不要复述检测到的秘密；建议用户轮换凭据。”
```

即使原消息不能进入模型，这条策略上下文仍可能帮助下一次正常回答。

单元测试名直接写明 `continue_false_preserves_context_for_later_turns`；集成测试进一步证明，下一 Turn 的模型请求能看到 context，而看不到被阻止 prompt。

---

## 28. Hook context 以 developer role 进入模型

`record_additional_contexts` 把每个字符串包装成 `HookAdditionalContext`。

它实现 `ContextualUserFragment` 时声明：

```text
role = developer
markers = ("", "")
body = Hook 原始/裁剪后文本
```

所以 Hook context 不是新的用户消息，也没有 `<external_...>` 外壳，而是 developer message。

这是一项重要信任决定：只有受信任、可执行的 Hook 才应产生这种上下文。

---

## 29. 它为什么不会产生 UserMessage UI item

普通用户输入通过 `record_user_prompt_and_emit_turn_item` 发出 UserMessage item started/completed。

Hook context 则通过 `record_conversation_items` 直接记录 developer `ResponseItem`。因此 UI 不应把它渲染成用户又发送了一条消息。

Hook 自身的可见性来自 HookStarted/HookCompleted 事件，而不是聊天消息 item。

---

## 30. 同名陷阱：客户端 additional context 不是 Hook context

源码里还有 App-server `turn/start.additionalContext`。两者对比如下：

| 属性 | 客户端 additional context | Hook additional context |
|---|---|---|
| 来源 | App-server 请求 | 受信 Hook stdout |
| 数据形态 | key → `{value, kind}` | 一个或多个字符串 |
| 信任分类 | Untrusted / Application | developer role |
| 包装 | `<external_key>` 或 `<key>` | 无 marker |
| 长度机制 | 每 value 约 1000 token 中部截断 | 每 handler spill limit，默认 2500 token |
| 状态 | Store 保存整份 keyed snapshot | 作为 history item 追加 |

阅读变量名时必须结合类型，而不能只看 `additional_context` 这个词。

---

## 31. 客户端 context 的 Untrusted 与 Application

客户端 `AdditionalContextKind::Untrusted` 会变成 user role，并包装：

```text
<external_browser_info>...</external_browser_info>
```

`Application` 会变成 developer role：

```text
<automation_info>...</automation_info>
```

这种差别告诉模型来源信任等级不同。Hook context 已位于受信 Hook 边界之后，所以直接采用 developer role。

---

## 32. 客户端 context 是 snapshot merge，不是永久 append

`AdditionalContextStore::merge` 比较新旧 keyed map：

- 值或 kind 改变才生成新的 fragment；
- 完全相同不重复注入；
- 合并后 store 被整份新 map 替换。

Hook context 则没有 keyed 去重：每次 Hook 输出都会成为新的 history item。

这反映不同语义：客户端 context 描述当前外部状态快照，Hook context 描述本次生命周期产生的指令或说明。

---

## 33. Hook context 的默认 2500 token 限制

每个 command handler 有独立 `additionalContextLimit`：

- 未设置：默认 2500 token；
- 设置为 `0`：禁用 spill；
- 其他数值：该 handler 自己的近似 token 阈值。

限制是逐个 context 应用，不是先把所有 Hook 输出连接后共享一个总预算。

---

## 34. 超长 context 为什么 spill 到文件

若输出超过阈值，`HookOutputSpiller`：

1. 在 OS 临时目录创建 `hook_outputs/<thread_id>/`；
2. 把完整文本写入随机 `.txt` 文件；
3. 给模型保留头尾预览；
4. 在预览末尾附上完整文件路径。

这样既限制 prompt 体积，又保留按需恢复完整内容的可能。

如果创建目录或写文件失败，则退化为纯文本截断，不让 spill I/O 故障阻塞用户输入。

---

## 35. 为什么路径 footer 也要计入预算

spiller 先估算：

```text
Full hook output saved to: /path/...
```

所占 token，再用剩余预算截断正文。

否则先把正文截到上限、再追加 footer，会悄悄突破配置预算。

这是实现有界上下文时很容易忽略的小细节。

---

## 36. blocked prompt 与 durable admission 怎样对齐

若消息携带 durable admission 的 client ID，`reject_pending_input` 会完成：

```text
Err(UserMessageAdmissionError::RejectedByHook)
```

因此调用者不会收到“已持久接纳”。

与此同时，Hook additional context 会单独写入 history/rollout。两件事不矛盾：

- 用户消息没有被持久接纳；
- Hook 的安全说明作为另一种 context 被保留。

---

## 37. 一批 pending inputs 不会因一条 block 全部丢弃

假设同一次 drain 得到：

```text
A：允许
B：阻止
```

循环会逐项处理，而不是在 B 处立即 return。最终判断是：

```text
persistence_failed
OR
(blocked_input AND 没有 accepted_user_input)
```

因此 A 会被记录，B 被拒绝；只要这一批存在被接受的用户输入，Turn 仍可继续 sampling。

固定提交的 `blocked_queued_prompt_does_not_strand_earlier_accepted_prompt` 测试证明了这一点。

---

## 38. 为什么不能遇到第一条 block 就短路

队列已被 drain，所有元素已离开 pending storage。若处理第一条 block 后直接返回，后面的消息既不在队列，也没进入 history，会被静默丢失。

所以代码必须继续检查和记录同批后续输入，再在批次末尾决定是否停止 sampling。

这是“取走一批工作后必须逐项收口”的通用队列原则。

---

## 39. 全部被阻止时为何结束当前 Turn 工作

若一批输入中有 block，且没有任何被允许的非空用户输入，函数返回 true，调用方停止当前 `run_turn` 路径，不发模型请求。

否则模型可能在没有合法新用户输入的情况下，基于旧上下文产生一次意外回复。

被阻止 prompt 本身不会出现在模型请求里。

---

## 40. Hook 序列化失败为何也 fail-open

如果连 Hook stdin JSON 都无法序列化，engine 为每个 matched handler生成 Failed completion event，但返回：

```text
should_stop = false
additional_contexts = []
```

这让异常可观察，却不把序列化 bug 变成整个产品的全局输入锁。

---

## 41. HookStarted 与 HookCompleted 的事件顺序

对一次 UserPromptSubmit 检查：

```text
所有 preview summaries → 依配置顺序发 HookStarted
并发运行所有 commands
全部结束并恢复配置顺序
→ 依配置顺序发 HookCompleted
```

所以某个很快 Hook 的 Completed 不会在另一个慢 Hook 的 Started 之前穿插。Core 把一次 Hook 批次表现为清晰的开始阶段和完成阶段。

---

## 42. 事件里为什么同时保留 source path 与 source

`HookRunSummary` 包括：

- 稳定 run ID；
- event name；
- command handler type；
- sync execution mode；
- Turn scope；
- source path；
- source 分类；
- display order；
- status/message/timing/entries。

路径回答“来自哪个文件”，source 回答“属于 user、project、managed、plugin 等哪一层”。两者结合，调试时才能判断究竟是哪份配置生效。

---

## 43. run ID 为什么包含 display order 与 source path

`ConfiguredHandler::run_id` 组合：

```text
event label : display order : source path
```

相同命令在同一文件出现两次仍能通过 order 区分；相同 order 在不同来源又能通过 path 区分。

源码注释也承认 positional suffix 未来可能被 durable hook ID 替代，所以不要把当前字符串格式当永久公共身份合同。

---

## 44. 子 Agent 运行时 Hook 还会得到什么

对于 thread-spawn subagent，输入可包含 `agent_id` 与 `agent_type`。

这让 Hook 可以区分：

- 主 thread 的用户 prompt；
- 某个被派生 Agent 中的 prompt。

但不是所有内部/system subagent 都按完全相同方式运行 lifecycle Hook；边界由 `SessionSource` 与 subagent 类型决定。

---

## 45. 一个较安全的 Hook 脚本结构

教学性伪代码：

```python
payload = json.load(sys.stdin)
prompt = payload["prompt"]

if contains_secret(prompt):
    print(json.dumps({
        "decision": "block",
        "reason": "检测到可能的凭据",
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "不要复述检测到的凭据"
        }
    }))
else:
    print(json.dumps({
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "输入已完成凭据扫描"
        }
    }))
```

脚本应把诊断日志写 stderr，stdout 只写一种明确结果。

---

## 46. 常见误解一：Hook 在 admission 之前执行

错误。

消息先获得 Turn 归属，之后在 `run_turn` 处理 initial/pending input 时运行 Hook。正因如此，durable admission 才需要额外等待 Hook 与 persistence，而不能只等待 Started/Steered。

---

## 47. 常见误解二：Hook block 会删除整条 Turn

错误。

它阻止的是当前用户输入。若同批还有允许的消息，Turn 可以继续；若没有允许消息，本轮停止 sampling。既有 history 和 Turn 身份并不会因此被抹掉。

---

## 48. 常见误解三：Hook command 失败等于用户被拒绝

错误。

失败状态默认 fail-open。只有合法 block、`continue:false`，或 exit 2 且 stderr 有理由，才设置 `should_stop`。

---

## 49. 常见误解四：所有 additional context 都是 developer 指令

错误。

客户端 `Untrusted` context 是 user role、带 external marker；客户端 `Application` 和 Hook context 才是 developer role，但它们仍有不同包装与存储语义。

---

## 50. 常见误解五：多个 Hook 按配置顺序串行运行

错误。

它们并发执行，只是在汇总和事件输出时恢复配置顺序。

---

## 51. 常见误解六：blocked 输出中的 context 会被丢弃

错误。

合法 block 或 `continue:false` 可以同时携带 additional context；Core 会拒绝原输入，但记录 context，供后续 Turn 使用。

---

## 52. 调试 Hook 没有生效的顺序

1. Hooks 功能是否 enabled？
2. 配置是否被 discovery 读取？
3. handler 是否 enabled？
4. hash 是否 Trusted/Managed，还是 Modified？
5. handler type 是否是固定提交支持的 command？
6. 是否错误依赖了 UserPromptSubmit matcher？
7. 是否收到 HookStarted？
8. Hook stdin 的字段名是否按 snake_case 读取？
9. command 是否在正确 cwd/shell 下启动？
10. 是否 timeout、exit nonzero 或输出无效 JSON？
11. block 是否带非空 reason？
12. 是否把日志误写 stdout，变成了 context？
13. HookCompleted 的 status 与 entries 是什么？
14. 模型请求中 developer context 是否出现？

---

## 53. 测试证据地图

### Hook parser 单元测试

`codex-rs/hooks/src/events/user_prompt_submit.rs` 验证：

- `continue:false` 保留 context；
- block + reason 真正阻止；
- block 无 reason 变成 Failed 且不注入 context；
- exit code 2 + stderr 形成 Blocked。

### Hook order 集成测试

`codex-rs/core/tests/suite/hooks.rs` 验证第一次 Turn 中：

```text
SessionStart → UserPromptSubmit
```

并检查 Hook 收到正确 prompt。

### blocked context 集成测试

同一文件验证：

- 被阻止 prompt 不进入模型；
- Hook context 在下一 Turn 以 developer message 出现；
- 下一条允许 prompt 正常进入模型；
- 两次 Hook 都收到非空 Turn ID。

### queued batch 集成测试

它还验证同一 Turn 中一条 accepted queued prompt 不会被后来的 blocked queued prompt 连带丢失。

### spill 单元测试

`codex-rs/hooks/src/output_spill_tests.rs` 验证：

- 小输出保持 inline；
- 大输出保存完整文件并返回截断预览；
- 每个 context 独立应用 limit；
- limit 0 与高阈值不会 spill。

---

## 54. 建议亲自跟读的符号

```bash
rg -n 'inspect_pending_input|run_hooks_and_record_inputs' codex-rs/core/src
rg -n 'record_pending_input|reject_pending_input' codex-rs/core/src
rg -n 'UserPromptSubmitRequest|UserPromptSubmitOutcome' codex-rs/hooks/src
rg -n 'parse_user_prompt_submit|parse_completed' codex-rs/hooks/src
rg -n 'execute_handlers|select_handlers' codex-rs/hooks/src
rg -n 'HookOutputSpiller|AdditionalContextLimit' codex-rs/hooks/src
rg -n 'HookAdditionalContext' codex-rs/core/src
rg -n 'AdditionalContextStore' codex-rs/core/src
```

每读一个函数，回答：

1. 它处理的是客户端 context 还是 Hook context？
2. 此时用户消息是否已归属 Turn？
3. 失败是 fail-open 还是 fail-closed？
4. context 以 user 还是 developer role 记录？
5. 哪个事件或 rollout item 能证明结果？

---

## 55. 理解检查

### 问题 1

Hook 返回 block 后，原用户消息还会进入模型请求吗？

不会。Core 调用 `reject_pending_input`，不记录该 UserMessage。

### 问题 2

同一个 block 输出中的 additional context 呢？

合法输出中的 context 会作为 developer message 记录，可在后续 Turn 中被模型看到。

### 问题 3

两个 Hook 一个允许、一个阻止，最终怎样决定？

任意一个 `should_stop` 即整体 stop；所有 handler 仍会跑完，contexts 按配置顺序汇总。

### 问题 4

Hook timeout 会阻止用户吗？

固定提交中不会。它产生 Failed Hook event，默认 fail-open。

### 问题 5

为什么 UserPromptSubmit matcher 不能筛选 prompt？

dispatcher 对此事件忽略 matcher；文本条件应由命令读取 stdin 的 `prompt` 后自行判断。

### 问题 6

为什么 Hook context 与客户端 Untrusted context 不能混为一谈？

它们来源和信任边界不同：前者来自受信可执行 Hook、使用 developer role；后者来自客户端外部数据、使用 user role 与 external marker。

### 问题 7

为什么多个 Hook 并发完成后还要排序？

减少总延迟的同时，让事件、context 顺序与测试结果保持确定性。

---

## 56. 本章词汇表

| 英文或代码词 | 字面意思 | 本篇中的具体含义 |
|---|---|---|
| Hook | 钩子 | 在生命周期边界执行的外部 command 扩展 |
| `UserPromptSubmit` | 用户提示提交 | 用户消息写入模型历史前的 Hook 事件 |
| handler | 处理器 | 某个 Hook 事件下配置的一条具体命令 |
| discovery | 发现 | 从配置层和 plugin 来源收集 Hook 定义 |
| configured handler | 已配置处理器 | 通过 enabled/trust 检查、真正可执行的 command |
| matcher | 匹配器 | 部分 Hook 用于筛选触发对象；本事件忽略它 |
| preview | 预览 | 执行前构造 Running summary，供 UI 提前展示 |
| `HookStarted` | Hook 开始事件 | 表示匹配 handler 即将/正在运行 |
| `HookCompleted` | Hook 完成事件 | 携带最终状态、耗时和输出 entries |
| `HookRunSummary` | Hook 运行摘要 | 描述身份、来源、顺序、状态、时间和输出 |
| `Running` | 运行中 | 命令尚未完成 |
| `Completed` | 已完成 | 正常完成且没有阻止 |
| `Failed` | 运行失败 | Hook 自身出错，UserPromptSubmit 默认 fail-open |
| `Blocked` | 已阻止 | 明确拒绝当前用户 prompt |
| `Stopped` | 已停止 | `continue:false` 要求停止当前处理 |
| fail-open | 失败时放行 | Hook 故障不自动拒绝用户消息 |
| fail-closed | 失败时拒绝 | 安全检查故障时阻止动作；本事件默认不采用 |
| `decision:block` | 阻止决定 | 必须配非空 reason 才有效 |
| `continue:false` | 不继续处理 | 通用 Hook 停止语法 |
| stop reason | 停止理由 | 进入 Hook event entry，但不进入 Core 简化 outcome |
| additional context | 附加上下文 | Hook 给模型追加的 developer 信息 |
| Hook-specific output | Hook 专用输出 | JSON 中按事件定义的 `hookSpecificOutput` |
| stdout | 标准输出 | Hook 结构化结果通道；普通文本会变成 context |
| stderr | 标准错误 | exit 2 时提供 block reason，也适合诊断日志 |
| exit code | 退出码 | 0 正常、2 可表达 block、其他通常 Failed |
| timeout | 超时 | 命令超过期限，记录 Failed 并默认放行 |
| `FuturesUnordered` | 无序 Future 集合 | 让多个 Hook command 并发运行 |
| configured order | 配置顺序 | 汇总结果与事件恢复后的稳定顺序 |
| completion order | 完成顺序 | 并发命令实际结束的先后 |
| context entry | 上下文条目 | Hook summary 中可展示的 Context 类型输出 |
| `HookAdditionalContext` | Hook 附加上下文 | 转换为 developer `ResponseItem` 的 Core fragment |
| role | 消息角色 | user/developer/assistant 等模型输入身份 |
| marker | 边界标记 | 标识外部 context 来源的 XML-like 包装 |
| spill | 溢写 | 完整大输出写临时文件，模型只保留预览与路径 |
| inline | 内联 | 文本直接保留在模型上下文中 |
| token limit | token 限制 | 决定 Hook context 是否 spill 的近似阈值 |
| trust status | 信任状态 | Managed、Trusted、Modified 等命令可信度结果 |
| display order | 展示顺序 | 跨来源配置合并后的 handler 顺序编号 |
| Turn scope | Turn 作用域 | Hook 运行属于当前 Turn，而不是整个进程 |
| `UserMessageAdmissionError::RejectedByHook` | 被 Hook 拒绝 | durable admission 对调用者返回的具体失败 |
| batch | 批次 | 一次从 pending queue drain 出来的多个输入 |
| short-circuit | 短路 | 遇到首个结果立即停止；本批输入处理刻意不这样做 |

---

## 57. 本篇结论

UserPromptSubmit Hook 位于一条非常精确的边界上：用户消息已经归属 Turn，但还没有成为模型可见、可持久恢复的普通 UserMessage。

完整顺序是：

```text
Started / Steered
→ drain initial 或 pending input
→ UserPromptSubmit handlers 并发执行
→ HookStarted / HookCompleted
→ allow 或 reject 用户消息
→ 独立记录 Hook additional context
→ rollout persistence
→ model sampling
```

这套实现最值得学习的设计点包括：

- 执行并发，但汇总顺序稳定；
- 明确 block 才阻止，Hook 故障默认 fail-open；
- block 用户消息与保留策略 context 是两个独立决定；
- 已 drain 的批次逐项收口，避免后续消息丢失；
- 超长 context spill 到文件，同时把恢复路径计入预算；
- UI 通过结构化 Hook 事件观察运行，而不是把 Hook context伪装成聊天消息；
- 同名 additional context 依据来源与类型走不同信任管线。

下一篇计划精读 **Turn/Item event projection 与客户端状态重建**：Core 内部 history、TurnItem 生命周期事件和 App-server snapshot 怎样共同让 UI 得到实时、又能最终校正的任务状态。

