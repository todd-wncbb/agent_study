# 04. Prompt 与 Instructions 如何拼装

## 1. Prompt 不是一个字符串

在简单聊天示例中，Prompt 常被描述成一段文本。Codex 的真实请求至少由四部分组成：

```text
base instructions     线程级基础行为
input                  按时间排列的结构化历史
tools                  本 Step 模型可见的工具 schema
sampling controls      reasoning、并行工具、输出 schema 等
```

核心结构 [`Prompt`](../codex-rs/core/src/client_common.rs) 直接体现了这四部分。它是 core 内部的请求中间表示，不等同于最终 HTTP JSON。

## 2. Base Instructions

`BaseInstructions` 是相对稳定的线程级基础指令。来源可能是：

1. 配置显式覆盖；
2. 恢复线程时保存的基础指令；
3. 当前 `ModelInfo` 提供的模型模板，并结合 personality 解析。

它通常回答：

- Agent 的身份和总体职责；
- 如何使用工具；
- 安全和协作原则；
- 输出风格；
- 当前模型特有的行为约束。

稳定基础指令有利于服务端 Prompt Cache。Codex 避免为了一个小环境变化就重写整个基础前缀。

## 3. Developer 和 Contextual User 消息

动态指令不会全部拼进 base instructions。它们作为 `ResponseItem::Message` 进入 History，主要分为：

- developer context：权限、模式、Skill catalog、Plugin 使用规则等高优先级运行约束；
- contextual user context：AGENTS.md、环境信息、Skill 正文等与当前工作空间或任务有关的材料；
-真实 user message：用户明确输入的任务。

这种区分具有两个作用：

1. 保留来源和优先级，而不是把所有文字揉成不可追踪的大字符串；
2. 允许 World State 用增量消息更新动态状态，而不重写过去历史。

## 4. 初始上下文如何组装

Session 在新线程或新上下文窗口中会调用初始上下文构造逻辑。其输入包括：

- `turn_context.developer_instructions`；
- extension 的 thread/turn context contributors；
-推荐 Plugin 信息；
- token budget 提示；
-完整 World State；
- collaboration/multi-agent/realtime 等模式说明。

构造器把片段分进三个槽：

```text
developer_sections
separate_developer_sections
contextual_user_sections
```

普通 developer 片段可以聚合；需要独立审计或顺序语义的策略可以保持为单独 developer item；user context 则被包装成标准化 contextual user message。

主要实现位于 [`Session::build_initial_context_with_world_state()`](../codex-rs/core/src/session/mod.rs)。

## 5. 增量上下文

稳定运行后，Codex 不会每 Step 重新注入完整环境。`record_step_world_state_if_changed()` 会：

1. 从当前 Step 构建新的 `WorldState`；
2. 与上一 baseline 比较；
3. 生成模型可见 diff fragments；
4. 把 diff 追加到 History；
5. 持久化对应的 WorldState patch；
6. 更新 baseline。

模型因此会看到类似“权限已改变”“当前目录已变化”的新消息，而不是看到整段历史被悄悄修改。

## 6. History 如何成为 Prompt.input

每次采样前：

```rust
let input = session
    .clone_history()
    .await
    .for_prompt(&turn_context.model_info.input_modalities);
```

`for_prompt()` 不只是简单 clone。它会根据模型输入能力和历史规范化规则，确保：

- Tool Call 与 Tool Result 关系合法；
- 不适合当前模型的内容得到处理；
- 图片等多模态内容符合输入能力；
-历史中的内部对象被转换成可发送形式。

## 7. Tool 定义怎样加入

Tool schema 不需要手工写入文本 Prompt。`build_prompt()` 从当前 `ToolRouter` 取得 `model_visible_specs()`，再由 API 客户端序列化。

普通 Responses 请求中：

```text
Prompt.tools → create_tools_raw_json_for_responses_api() → request.tools
```

Responses Lite 是一个重要分支：工具定义和基础指令会以前缀 `ResponseItem` 的形式插入 `input`，顶层 `instructions` 为空，顶层 `tools` 也可能省略。文档或调试工具不能假设所有模型都使用同一种 wire 形态。

## 8. 最终 Responses 请求

[`ModelClientSession::build_responses_request()`](../codex-rs/core/src/client.rs) 产生的核心字段可简化为：

```json
{
  "model": "selected-model",
  "instructions": "base instructions",
  "input": ["developer/user/assistant/tool items"],
  "tools": ["tool schemas"],
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "reasoning": {},
  "stream": true,
  "include": ["reasoning.encrypted_content"],
  "prompt_cache_key": "...",
  "text": {}
}
```

这是教学投影，不是完整抓包。Provider、Responses Lite、输出 schema 和模型能力会改变字段。

## 9. 指令优先级

理解 Prompt 时应把优先级和时间顺序分开：

- 基础/系统和 developer 规则约束模型行为；
- 用户任务提供目标；
- AGENTS.md、Skill 等提供局部方法和上下文；
- 后出现的状态 diff 描述最新事实，但不能越权覆盖更高层策略；
- Tool Result 是外部世界观察结果，不是新的策略来源。

Codex 通过角色、标记和固定包装格式帮助模型识别来源，但最终仍需要基础指令明确告诉模型怎样解释这些片段。

## 10. 为什么必须结构化

如果把全部内容拼成一个字符串，会失去：

- Tool Call 和 Result 的 call ID 关系；
-角色优先级；
- 多模态内容；
- 流式 item 生命周期；
- 增量上下文与缓存；
- rollback/compaction 时的结构边界。

因此实现 Agent 时，应让 Prompt Builder 输出结构化请求，而不是返回 `String`。

## 11. 调试建议

检查 Prompt 问题时按以下顺序：

1. `BaseInstructions` 实际文本是什么；
2. History 中最后几个 `ResponseItem` 是什么；
3. 当前 Step 的 `model_visible_specs` 是什么；
4. `build_responses_request()` 是否因模型能力改变了表示；
5. Provider 收到的最终 JSON 是什么。

很多“模型没遵守指令”的问题，实际是上下文没有进入正确 role、被预算截断、只存在于 Registry 但未对模型可见，或 compact 后没有重新建立 baseline。

