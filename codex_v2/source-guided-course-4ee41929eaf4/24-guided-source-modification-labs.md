# 24：引导式源码修改实验

## 这章怎样使用

这一章不直接给完整补丁，而是训练你在修改前回答五个问题：

1. 用户可观察行为是什么？
2. 真正拥有这项行为的是哪一层？
3. 哪些协议、状态和安全边界会受影响？
4. 最小的可审查改动是什么？
5. 用哪一层测试证明它真的生效？

建议为每个实验新建一份笔记，先写设计再看源码。不要为了“做完实验”一次修改多个相邻功能。

## 通用实验模板

```markdown
# 实验名称

## 用户行为
用户最终能看到或做到什么？

## 所有权
哪一层创建、持有和结束这项行为？

## 数据流
入口 → 内部类型 → Runtime → Event/Test

## 修改文件
只列必要文件，并解释为什么需要它。

## 风险
协议兼容、权限、副作用、上下文、并发、平台。

## 测试
失败前的测试、实现后的断言、需要运行的命令。

## 尚未覆盖
刻意推迟到后续改动的内容。
```

## 实验 1：新增一个只读 Tool

### 目标

设计一个 `workspace.repo_summary` 工具，返回当前仓库的有限摘要，例如 workspace root、是否存在 `.git` 和顶层文件数量。

它只是教学题，不要求你真的提交这个产品功能。

### 为什么从只读工具开始

只读工具仍然涉及完整闭环：

```text
ToolSpec → Registry → Handler → ToolOutput → History → Follow-up
```

但不需要先解决写操作审批、幂等和补偿问题，适合作为第一个改动。

### 第一步：读一个最小参考实现

阅读 `core/src/tools/handlers/current_time.rs`，分别标出：

- `CurrentTimeHandler`：执行器；
- `tool_name()`：模型调用身份；
- `spec()`：模型可见 schema；
- `handle()`：Runtime 行为；
- `CurrentTimeOutput`：日志、模型输出和 Code Mode 输出；
- `CoreToolRuntime`：接入 Core Registry 的标记。

先不要复制代码，写下每一部分为什么存在。

### 第二步：设计参数和输出

只读摘要是否需要参数？如果不需要，schema 应明确拒绝额外字段。

建议的结构化输出：

```json
{
  "workspaceRoot": "/workspace/codex",
  "isGitRepository": true,
  "topLevelEntryCount": 37
}
```

同时回答：

- 路径来自当前 environment 还是本机全局 cwd？
- 远端 executor 时用哪个 filesystem？
- 是否暴露绝对路径给模型？
- 数量是否有上限或超时？

### 第三步：列修改点

可能涉及：

- 新 Handler/Spec 文件；
- `handlers/mod.rs` 私有模块与显式导出；
- `tools/spec_plan.rs` 注册与 Feature/能力条件；
- Unit test 验证 schema；
- Core integration test 验证 call → output → follow-up。

不要先改 app-server：模型内部 Tool 不一定需要新增外部 RPC API。

### 第四步：设计集成测试

测试应包含两次模型响应：

1. 第一次返回 `workspace.repo_summary` Tool Call；
2. 第二次返回 assistant message。

断言第二个 outbound request 中包含相同 call ID 的结构化 Tool Output，而不仅是断言 Handler 单独返回了正确数字。

### 完成标准

- 能解释 ToolSpec 与 Handler 为什么都需要；
- 本地与远端 filesystem 语义一致；
- 输出有硬边界；
- 集成测试证明模型看到了结果。

## 实验 2：给 `turn/start` 新增可选字段

### 目标

假设需要一个教学字段 `clientLabel?: string | null`，让客户端为 Turn 附加一个非安全敏感、非模型可见的展示标签。

真正实现前应先确认产品是否确实需要新 API；本实验重点是学习协议影响面。

### 第一步：判断字段属于哪种状态

先回答：

- 只影响本次 request，还是后续 Turn 也保持？
- 是否写入 thread store/rollout？
- 是否传给模型 metadata？
- 是否出现在 notification 中？
- 客户端省略和显式 `null` 是否不同？

这些问题决定它不能只是在 struct 中加一行。

### 第二步：修改 v2 类型

入口是：

```text
app-server-protocol/src/protocol/v2/turn.rs::TurnStartParams
```

客户端 request 的 optional 字段需要遵守 v2 规则，例如 `#[ts(optional = nullable)]`，serde 与 TypeScript wire name 必须一致。

如果字段是实验性的，还要使用正确的 experimental gate，并决定 `common.rs` 是否需要参数检查。

### 第三步：追踪 request processor

在 `app-server/src/request_processors/turn_processor.rs` 中找到：

```text
TurnStartParams
  → validation/mapping
  → ThreadSettingsOverrides 或 Op::UserInput metadata
  → Session submission
```

如果字段只是客户端 UI 标签，不应顺手塞进模型 Prompt。每跨一层都要有明确用途。

### 第四步：生成和测试

协议形状变化通常需要：

```bash
cd codex-rs
just write-app-server-schema
just test -p codex-app-server-protocol
```

行为测试应通过 public JSON-RPC API 发 `turn/start`，覆盖：

- 字段省略；
- 字段为 `null`；
- 字段有值；
- 若有 gate，未启用时的行为；
- 相关 response/notification 或持久化结果。

### 完成标准

- Rust、serde、TypeScript 和 schema 一致；
- 没有把内部类型直接暴露成 wire contract；
- optional/null 语义明确；
- 公共 API 测试证明行为。

## 实验 3：新增一个 Feature Flag

### 目标

假设新增实验 Feature `workspace_repo_summary`，控制实验 1 的工具是否可用。

### 第一步：写行为句子

先写成可以测试的句子：

> Feature 关闭时，最终模型请求中没有 `workspace.repo_summary`；开启且环境支持时，该工具出现在 model-visible specs 中。

这比“配置布尔值变成 true”更接近用户行为。

### 第二步：定义 Feature metadata

阅读 `features/src/lib.rs`：

- `Feature` enum；
- `FeatureSpec`；
- `FEATURES` registry；
- stage 和默认值；
- 依赖/组合逻辑。

实验 Feature 默认通常关闭。不要使用另一个不相关的 Feature 作为临时开关。

### 第三步：在能力计划中使用

Feature 检查应靠近工具注册/暴露决策：

```text
if config.features.enabled(Feature::WorkspaceRepoSummary) {
    registry.add(...)
}
```

还要判断 Model capability、environment 和 admin requirement 是否构成额外条件。

### 第四步：测试最终行为

至少两类测试：

- Features crate：默认值、enable/disable 和依赖解析；
- Core：开关前后 outbound request 的 tools 完整对比。

不要只对 `enabled()` 写断言就结束。

### 完成标准

- Key、stage 和默认状态合理；
- Feature 只控制目标路径；
- 关闭时不存在残留 ToolSpec/Handler 暴露；
- 测试观察到最终模型请求差异。

## 实验 4：新增一种 Hook Event

### 目标

设计一个教学事件 `BeforeTurnComplete`：在普通 Turn 即将发出终止事件前执行一次只读审计 Hook。

这个实验改动面较大，建议只做设计或分阶段实现。

### 为什么它比新增 enum variant 复杂

一个完整 Hook Event 涉及：

```text
Protocol enum
→ config key/label
→ typed command input/output schema
→ discovery 和 matcher 规则
→ dispatcher scope
→ Core 生命周期触发点
→ HookStarted/HookCompleted events
→ 超时、失败和阻止语义
→ schema fixtures 与测试
```

### 第一步：先决定语义

必须明确：

- 它与现有 `Stop` Hook 有什么不同？
- 能否阻止完成，还是只观察？
- 是 Turn scope 还是 Thread scope？
- 同步还是允许 async？
- 多个 Hook 的顺序是什么？
- Hook 失败是否让 Turn 失败？

如果无法和 `Stop` 区分，就不应该新增事件。

### 第二步：查找 exhaustive match

新增 `HookEventName` variant 后，编译错误会指出需要同步处理的位置。重点查看：

- `protocol/src/protocol.rs`；
- `hooks/src/lib.rs` labels；
- `hooks/src/engine/dispatcher.rs` scope/label；
- `hooks/src/engine/discovery.rs` 支持规则；
- `hooks/src/schema.rs` wire types；
- `hooks/src/events/` typed implementation。

不要用 wildcard match 隐藏新增 variant。

### 第三步：测试失败语义

除了 happy path，还要覆盖：

- Hook command 不存在；
- timeout；
- 非零退出；
- JSON 无效；
- 输出超过上限；
- 多 Hook 顺序；
- Turn interrupt 时 Hook 如何取消。

### 完成标准

- 新事件确实不可由现有事件表达；
- 输入输出 schema 有界且有类型；
- 生命周期触发恰好一次；
- 失败不会重复已发生的副作用；
- Protocol/Core/Hook engine 测试齐全。

## 实验 5：修改一条用户可见通知

### 目标

假设希望 `turn/completed` 中展示一个新的 `durationMs` 字段，当前内部事件已有相应数据，但客户端 notification 尚未暴露。

先核对实际 v2 类型，确认字段是否已经存在；如果已存在，就把实验改成追踪它的来源，而不是重复添加。

### 第一步：画三层投影

```text
Core EventMsg::TurnComplete
  → app-server bespoke_event_handling
  → v2 TurnCompletedNotification
  → TUI/App reducer
```

确认哪一层缺字段。不要在 UI 根据本地时间重新计算一个与 Core 不一致的 duration。

### 第二步：判断 Breaking Change

新增 optional response/notification 字段通常比删除或改类型安全，但仍要检查：

- serde 与 TS rename；
- v2 是否允许 optional；
- 老客户端是否忽略未知字段；
- snapshot/schema fixture；
- raw response item 是否同时受影响；
- TUI 是否会重复展示。

### 第三步：测试链

建议按三层验证：

1. Core 测试产生带 duration 的 `TurnCompleteEvent`；
2. App-server v2 测试断言 notification JSON；
3. 若 UI 展示变化，更新/审查 TUI snapshot。

### 完成标准

- 时间由唯一权威层计算；
- wire contract 和 TypeScript 同步；
- 客户端能处理字段缺失；
- UI 没有重复或乱序状态。

## 实验后的验证顺序

真正修改 Rust 后，遵守仓库规则：

1. 先运行被修改 crate 的目标测试；
2. 大改动运行 scoped `just fix -p <project>`；
3. 最后运行 `just fmt`；
4. 按仓库要求，不在 fix/fmt 后重复跑测试；
5. common/core/protocol 变化若需要全量 `just test`，先取得用户同意；
6. API schema、Config schema 或依赖 lockfile 按对应规则生成。

测试失败时，不要一次修改多个假设。先找最后一个已确认边界，再修最小原因。

## 本章词汇表

| 词语 | 直译 | 在实验中的意思 |
|---|---|---|
| Impact surface | 影响面 | 一项修改会触及的类型、运行路径、协议和测试集合 |
| Acceptance criterion | 验收标准 | 用用户可观察行为判断实验完成的条件 |
| Happy path | 正常路径 | 所有输入和依赖都成功时的主要执行路径 |
| Edge case | 边界情况 | 空值、超限、取消、并发等容易遗漏的情况 |
| Breaking change | 破坏性变更 | 让已有客户端、配置或恢复数据不再兼容的修改 |
| Fixture | 固定测试资料 | Schema、snapshot 或预置输入等测试基准文件 |
| Scoped test | 限定范围测试 | 只运行受改动 crate/功能相关的测试集合 |
| Mechanical change | 机械改动 | 可按固定规则批量完成、几乎不引入新语义的修改 |

完整解释见[术语总表](glossary.md)。

## 读完后自测

1. 新增 Tool 为什么至少需要一次 call → output → follow-up 集成测试？
2. 给 v2 Params 加字段前，为什么必须先决定省略与 `null` 的区别？
3. Feature 测试为什么不能只断言 `enabled() == true`？
4. 新增 Hook Event 为什么可能横跨 Protocol、Config、Engine 和 Core？
5. UI duration 为什么应使用 Core 权威时间，而不是客户端重新估算？

## 源码检查点

- `codex-rs/core/src/tools/handlers/current_time.rs`；
- `codex-rs/core/src/tools/spec_plan.rs` 与 `spec_plan_tests.rs`；
- `codex-rs/core/tests/suite/current_time_reminder.rs`；
- `codex-rs/app-server-protocol/src/protocol/v2/turn.rs`；
- `codex-rs/app-server/src/request_processors/turn_processor.rs`；
- `codex-rs/app-server/tests/suite/v2/`；
- `codex-rs/features/src/lib.rs` 与 `tests.rs`；
- `codex-rs/hooks/src/engine/`、`events/` 和 `schema.rs`；
- `codex-rs/app-server/src/bespoke_event_handling.rs`。
