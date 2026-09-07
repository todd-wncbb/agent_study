# 16：Hooks 与 Extensions

## 先用“门卫”和“内置部门”来理解

Hooks 和 Extensions 都能扩展 Codex，但方式不同：

- **Hook** 像流程节点旁的门卫：在用户提交、工具执行前后、压缩或结束时，运行配置好的检查；
- **Extension** 像系统内部新增的专业部门：用 Rust 类型安全地贡献上下文、工具、状态和生命周期逻辑。

Hook 适合“不重新编译 Codex，也能接入审计脚本”；Extension 适合“实现一项长期、结构化的产品能力”。

## 1. Hook 监听的是明确生命周期事件

`HookEventName` 当前包括工具前后、权限请求、压缩前后、Session 起止、用户输入、子 Agent 起止和 Stop 等事件。

这比“每隔一会儿检查一下系统”更可靠，因为每个事件都有明确时机和输入。例如 Pre Tool Use 发生在 handler 执行前，Post Tool Use 发生在工具已经产生结果之后。

## 2. 一个 Pre Tool Use 例子

假设组织规定：删除生产目录前必须经过额外检查。

```text
模型产生 shell tool call
  → PreToolUse Hook 收到工具名、call ID、参数和上下文
  → Hook 返回结构化阻止理由
  → Core 不执行该工具
  → 模型或用户收到有界说明
```

即使 Hook 返回允许，调用仍然要经过普通 approval、exec policy 和 sandbox。Hook 不是超级权限通道。

## 3. 为什么 Hook 输入输出必须结构化

如果只约定“stdout 包含 deny 就拒绝”，会产生大小写、日志噪声和注入歧义。Codex 为不同事件定义独立 schema，并由 output parser 转换为 typed outcome。

结构化协议可以明确区分：

- Hook 自己执行失败；
- Hook 成功运行并决定阻止；
- Hook 允许继续但附加上下文；
- Hook 输出格式无效。

Hook 的 stdout/stderr 也必须有界，不能把无限日志塞进 prompt 或事件流。

## 4. Post Tool Use 的副作用边界

假设工具已经成功创建了一个外部工单，随后 Post Hook 失败。系统不能简单重跑整个工具，否则可能创建重复工单。

所以需要分别记录：

```text
工具是否已经完成副作用？
Post Hook 是否成功完成后处理？
```

“后处理失败”不能被误写成“工具肯定没有执行”。这也是所有外部写操作的重要设计原则。

## 5. Stop Hook 为什么可能让 Turn 继续

当模型准备结束、普通循环认为 `needs_follow_up=false` 时，Stop Hook 可以要求继续，例如提醒：“提交最终回答前还必须运行测试。”这段 continuation 会进入 history，再触发一次 sampling。

Runtime 必须限制这种继续行为，避免 Hook 永远说“再做一步”而形成无限循环。

## 6. Extension 贡献能力，而不是随意改 Core

`codex-rs/ext/extension-api` 提供 `ExtensionRegistry`。不同 contributor 可以贡献：

- Prompt / Context；
- World State；
- Tools；
- MCP 配置；
- Turn 输入或展示 item；
- Thread、Turn、Tool 生命周期逻辑。

Extension 不应直接伸进 Session 内部修改 history vector。它返回定义好的贡献，由 Core 统一排序、限制大小并放入正确角色。

## 7. ExtensionData 的作用域

扩展经常需要保存状态，但不同状态活得不一样久：

| Store | 适合保存 |
|---|---|
| Session | 整个根会话共享的服务或统计 |
| Thread | 某条对话的选择和缓存 |
| Turn | 当前任务中的临时判断 |
| Step | 本次能力快照使用的数据 |

把 Turn 临时状态放进 Session store，会污染后续任务；把 Thread 缓存放进 Step，又会重复工作。`ExtensionData` 用类型化值和明确作用域降低这种错误。

## 8. Skill Extension 是怎样组合多种贡献的

Skills 能力不是只做一件事：

1. Context contributor 展示有界 Skill catalog；
2. 用户明确选择后，Turn input 路径读取完整说明；
3. Tool contributor 提供列出或读取 Skill 的工具；
4. Extension state 记录当前 thread/turn 的选择和调用。

这说明一个 Extension 可以同时贡献多种能力，但每一种仍通过统一边界进入 Core。

## 9. 该选择 Hook 还是 Extension

| 需求 | 更适合 |
|---|---|
| 用户配置一个审计命令 | Hook |
| 无需重新编译即可增加检查 | Hook |
| 新增类型安全的 Tool runtime | Extension |
| 在多个 Turn 间保存内部状态 | Extension |
| 为 Prompt 贡献结构化上下文 | Extension |

## 常见误解

- **“Hook 允许后，沙箱就不再检查。”** Hook、approval 和 sandbox 是不同边界。
- **“Post Hook 失败，可以安全地重跑工具。”** 写操作可能已经完成副作用。
- **“Extension 就是可以修改任何 Core 状态的插件。”** 它应通过 contributor 和 registry 贡献能力。

## 读完后自测

1. 为什么 Hook 运行成功与 Hook 决定允许是两件事？
2. 工具成功、Post Hook 失败时，为什么不能自动重试工具？
3. Thread store 和 Turn store 混用会造成什么问题？

## 本章词汇表

| 词语 | 直译 | 在扩展机制中的意思 |
|---|---|---|
| Hook | 钩子 | 在明确生命周期节点运行的外部检查或脚本 |
| Extension | 扩展 | 通过 Rust API 贡献内部能力的模块 |
| Contributor | 贡献者 | Extension 中提供某一类上下文、工具或生命周期能力的实现 |
| Typed outcome | 有类型的结果 | 已按 schema 解析、可明确区分允许/阻止/失败的输出 |
| Pre/Post | 之前/之后 | 某动作发生前或已经发生后的生命周期位置 |
| Spill | 溢出转存 | 超大 Hook 输出不直接注入，而改为有界表示或外部引用 |
| Continuation | 继续内容 | Stop Hook 要求继续时写入 history 的补充片段 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

- `codex-rs/protocol/src/protocol.rs::HookEventName`：事件全集；
- `codex-rs/hooks/src/engine/`：发现、执行和输出解析；
- `codex-rs/hooks/src/events/`：typed request/outcome；
- `codex-rs/ext/extension-api/src/registry.rs`：Extension 注册；
- `codex-rs/ext/extension-api/src/contributors.rs`：各类 contributor；
- `codex-rs/ext/extension-api/src/state.rs`：`ExtensionData`。
