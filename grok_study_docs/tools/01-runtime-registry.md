# Tool Runtime、注册表与调用链

这篇先解释所有 Tool 共用的基础设施。看懂这里后，每个具体 Tool 的实现都可以归纳为：
声明契约、从 Resources 取后端、执行、返回强类型 Output。

## 1. 从 Rust 类型到 LLM Tool Definition

静态注册入口位于
[`ToolRegistryBuilder::new`](../../crates/codegen/xai-grok-tools/src/registry/types.rs)。
典型注册代码：

```rust
b.register_with_params::<grok_build::BashTool, BashParams>();
b.register::<grok_build::TodoWriteTool>();
```

`register<T>()` 在编译期知道具体 Tool 类型，因此能够收集：

- `Tool::id()`：namespace 内部短 ID。
- `ToolMetadata::tool_namespace()`：GrokBuild、Codex、OpenCode 等。
- `ToolMetadata::kind()`：Read、Edit、Execute、Task 等能力分类。
- `Tool::Args`：通过 `schemars` 生成 JSON Schema。
- `description_template()`：尚未解析模型可见工具名的模板。
- `requires_expr()`：该 Tool 对其他 Tool/Resource/能力的依赖。
- 可选默认参数和 behavior preset 版本参数。

内部唯一键是：

```text
{namespace}:{tool_id}
```

例如：

```text
GrokBuild:read_file
Codex:read_file
OpenCode:read
```

相同能力可以同时注册多套协议，而不会因短名称相同而冲突。

## 2. ToolConfig 与 ToolServerConfig

`ToolConfig` 是一个 Tool 在某个 Agent 中的选择和覆写：

- 完全限定 `id`
- 模型可见 `name_override`
- 参数名覆写
- Tool 默认参数
- kind 覆写
- description 覆写

`ToolServerConfig` 则是某个 Agent 的启用列表和 `behavior_preset`。默认 Grok Build
preset 定义在
[`xai-grok-agent/src/config.rs`](../../crates/codegen/xai-grok-agent/src/config.rs)。

这解释了为什么注册表有 50 个实现，而默认只启用 18 个。

## 3. finalize 阶段

Agent 构建期间执行：

```text
AgentBuilder
  → ToolBridge::get_builder()
  → ToolRegistryBuilder::finalize(config, SessionContext)
  → FinalizedToolset
```

`SessionContext` 注入当前会话拥有的后端：

- `Terminal`
- `FileSystem`
- `Cwd`
- `SessionFolder`
- `SessionEnv`
- `NotificationHandle`
- `AvailableSkills`
- 子 Agent backend/event sender/depth/session ID
- Memory backend
- Web client、认证 provider

这些值被放进类型化 `Resources` 容器。Tool 运行时通过类型取值，不依赖一个巨大、固定的
上下文 struct。

finalize 还会：

1. 校验 `ToolConfig.id` 是否已注册。
2. 合并 behavior preset、默认参数和 per-Agent 覆写。
3. 计算 requirements，移除或拒绝无法满足的 Tool。
4. 建立 canonical name、client-facing name、kind 的映射。
5. 渲染 description 中的 `${{ tools.* }}` 和参数名引用。
6. 生成最终 JSON Schema。
7. 建立输入解析、输出转换和 dispatch closure。
8. 选择满足 requirements 的 cross-cutting Reminders。

## 4. Description 模板为什么要延迟渲染

Tool description 经常引用其他 Tool，例如“先使用 Read”。但模型可见名称可能被 preset
改成另外一个名字。因此源码使用 MiniJinja 风格占位符：

```text
${{ tools.read_file }}
${{ tools.by_kind.read }}
${{ params.read_file.target_file }}
```

finalize 后，description 中引用的名字和实际发给模型的名字保持一致。模板渲染实现位于
[`types/description.rs`](../../crates/codegen/xai-grok-tools/src/types/description.rs)。

## 5. JSON Schema

每个 Tool 的 `Args` 都实现 `schemars::JsonSchema`。注册表使用 Draft 7 生成 Schema，并移除
根对象上 Rust struct 名称产生的 `title` 和 boilerplate `description`，避免内部类型名泄漏给
模型。对象 Schema 会确保存在：

```json
{
  "type": "object",
  "properties": {},
  "required": []
}
```

字段 doc comment 和 `#[schemars(description = "...")]` 会成为模型可见字段说明。

## 6. 一次 Tool 调用的完整路径

```text
LLM tool_call(name, JSON)
  → Shell WorkspaceOps / SessionActor
  → ToolBridge::try_parse
  → client name 映射为 FinalizedTool
  → serde 反序列化 Args
  → 权限/审批与 hook
  → xai_tool_runtime::Tool::run(ctx, args)
  → 强类型 Output
  → ToolOutput / multimodal content 转换
  → 输出截断、落盘和通知
  → cross-cutting Reminder
  → Conversation tool_result
  → 下一轮采样
```

区分 `try_parse` 和真正执行非常重要：前者可以在不产生副作用的情况下检查 Tool 名称与
参数是否合法；权限和生命周期层可以在确认后再执行。

## 7. ToolMetadata 的四个关键维度

### `kind()`

它不是展示名称，而是能力分类。Prompt 模板、权限、Agent allowlist 和 reminder
requirements 都可按 kind 工作。例如 Skill 的兼容 allowlist 可以映射到 Read，因为默认
GrokBuild 通过 `read_file` 加载 `SKILL.md`。

### `tool_namespace()`

决定内部完全限定 ID，也区分不同模型训练分布下的协议风格。

### `requires_expr()`

表达 Tool 是否可用，例如 Task 需要子 Agent backend；后台 Bash 需要输出和 kill 辅助
能力；某些媒体 Tool 需要认证 provider 或客户端。

### `description_template()`

描述“什么时候调用”和“如何正确调用”，与 Args Schema 共同组成发给 LLM 的 Tool
Definition。

## 8. Output 并不总是普通字符串

不同 Tool 的输出可包含：

- 普通文本。
- 结构化成功/错误枚举。
- 图片、多页 PDF 图片等多模态 ContentBlock。
- 后台 task ID。
- 完整输出落盘位置和给 Prompt 的截断版本。
- 需要由 Session 层执行的扩展事件。

因此很多实现把“执行结果”和“模型可见结果”分开保存，例如 Bash 同时持有完整输出、
截断输出、退出码、signal、timeout 和 artifact 路径。

## 9. Cross-cutting Reminders

注册表目前注册三类 reminder：

- `LspDiagnosticsReminder`：编辑后向模型补充相关诊断。
- `TaskCompletionReminder`：后台任务完成时提醒。
- `SkillDiscoveryReminder`：文件访问后发现或激活新的 Skill。

Reminder 不属于某一个 Tool，而是在每次调用后检查 `ToolOutput` 类型，并根据
`requires_expr` 和资源状态决定是否产生 synthetic system-reminder。

## 10. MCP 动态注册与间接调用

MCP Tool 不在静态 50 个内。Session 获得 MCP Server 的 tool list 后会构造元数据 snapshot，
并可注册到最终 Toolset。大量 MCP Tool 默认通过：

```text
search_tool(query)
  → ToolIndex/BM25 返回名称、描述、Schema
use_tool(tool_name, tool_input)
  → local MCP 或 managed gateway 分发
```

这样 LLM 不必永久携带所有 MCP Tool Definition。完整实现见
[MCP Tool 发现文档](../grok-build-tools-and-mcp-discovery.md)。

## 11. Tool Pack

`register_tool_pack()` 允许二进制链接的其他 crate 在构建注册表时增加 Tool。这些工具不在
核心仓库静态清单中，因此文档应把它们视为开放扩展点，而不是遗漏。

## 12. 推荐的代码追踪顺序

1. [`registry/types.rs`](../../crates/codegen/xai-grok-tools/src/registry/types.rs)：注册与 finalize。
2. [`bridge.rs`](../../crates/codegen/xai-grok-tools/src/bridge.rs)：跨 crate 门面。
3. [`types/tool.rs`](../../crates/codegen/xai-grok-tools/src/types/tool.rs)：kind、namespace、Reminder。
4. [`types/tool_io.rs`](../../crates/codegen/xai-grok-tools/src/types/tool_io.rs)：统一输入类型。
5. [`types/output.rs`](../../crates/codegen/xai-grok-tools/src/types/output.rs)：统一输出枚举。
6. [`types/resources.rs`](../../crates/codegen/xai-grok-tools/src/types/resources.rs)：运行时依赖。
7. 任意具体 Tool 的 `ToolMetadata` 和 `Tool::run()`。

