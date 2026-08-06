# 07. Tool：定义、注册、暴露、搜索与路由

## 1. Tool 的两面

一个 Tool 同时具有：

-模型面：名称、描述、输入 schema；
-执行面：解析参数、权限检查、实际动作和输出转换。

把两面完全混在一起会难以测试；完全分离又可能发生 schema 与执行器不一致。Codex 通过稳定的 `ToolName` 和 Runtime trait 把两面关联。

## 2. `ToolSpec`

[`codex-rs/tools`](../codex-rs/tools/src) 定义的 `ToolSpec` 支持：

- Function：JSON Schema 参数；
- Freeform：自定义文本输入，例如 apply patch；
- Namespace：一个命名空间包含多个方法；
- Tool Search；
- Hosted Web Search 等服务端工具。

Function Tool 的典型模型表示：

```json
{
  "type": "function",
  "name": "exec_command",
  "description": "Run a command...",
  "parameters": {
    "type": "object",
    "properties": {
      "cmd": { "type": "string" }
    },
    "required": ["cmd"]
  }
}
```

Schema 是模型生成参数的契约，但 Runtime 仍必须重新校验，不能信任模型一定生成合法 JSON。

## 3. `ToolName` 与 Namespace

工具名称包含可选 namespace，而不是只有一个平面字符串。这对 MCP、App 和多 Agent 尤其重要：

```text
plain:       exec_command
namespaced:  collaboration.spawn_agent
namespaced:  skills.read
```

Namespace 既减少冲突，也能把相关工具以结构化形式给模型。Provider 或模型不支持 namespace tools 时，spec planning 层需要采用兼容表示。

## 4. Tool Registry

[`ToolRegistry`](../codex-rs/core/src/tools/registry.rs) 使用有序映射保存：

```text
ToolName → RegisteredTool { runtime, exposure }
```

注册规则有意区分可信内置工具和外部工具：

-内置重复注册被视为程序错误；
-外部重复注册记录 warning 并跳过；
-某些保留名称不能被外部工具占用。

有序结构使发送给模型的工具顺序更稳定，有利于测试和缓存。

## 5. Exposure

Registry 中的 Tool 不一定都直接给模型。`ToolExposure` 可表达：

-直接模型可见；
-Deferred，需要 Tool Search 后加载；
-Hidden，只供内部或嵌套 Runtime；
-Code Mode 可用；
-Direct-model-only 等组合语义。

因此必须区分：

```text
registry.entries()          Runtime 可以路由的全集
router.model_visible_specs  本次模型请求公开的子集
```

## 6. Tool Router 的构建

[`build_tool_router()`](../codex-rs/core/src/tools/spec_plan.rs) 每个 Step 汇总：

1. Core 内置工具；
2. MCP 工具；
3. Extension 工具；
4. Dynamic tools；
5. Hosted model tools；
6. Tool Search；
7. Code Mode 包装器。

随后 `finalize_tool_router()`：

-应用 exposure override；
-根据 Code Mode 隐藏或包装工具；
-如果存在可搜索的 Deferred Tool，则注册 `tool_search`；
-构造模型可见 spec；
-合并相同 namespace；
-按模型能力过滤 namespace。

## 7. Tool Search

当外部工具太多时，把所有 schema 发给模型会消耗大量 token。Deferred Tool 的流程是：

```text
Registry 注册完整执行器，exposure = Deferred
  → 模型只看到 tool_search 及 namespace 摘要
  → 模型提交搜索 query
  → ToolSearchHandler 对 discoverable metadata 排序
  → 返回匹配工具定义
  → 后续请求增加可用 ToolSpec
  → 模型调用真实工具
```

这类似 Skill 的“目录 + 按需正文”，但被加载的是可调用 schema，而不是工作流文本。

## 8. 模型输出如何变成内部调用

[`ToolRouter::build_tool_call()`](../codex-rs/core/src/tools/router.rs) 处理三类响应：

- `FunctionCall` → `ToolPayload::Function`；
- client-side `ToolSearchCall` → `ToolPayload::ToolSearch`；
- `CustomToolCall` → `ToolPayload::Custom`。

统一后的内部结构包含：

```rust
pub struct ToolCall {
    pub tool_name: ToolName,
    pub call_id: String,
    pub payload: ToolPayload,
    pub encrypted_function_args: Option<Vec<String>>,
}
```

`call_id` 用于把未来的 Tool Result 与这次调用精确关联。

## 9. Router 如何执行

路由阶段会：

1. 按 `ToolName` 查询 Runtime；
2. 检查 payload kind 是否匹配；
3. 构造包含 Session、Step、取消 token 等信息的 invocation；
4. 运行生命周期 Hook、权限和工具逻辑；
5. 得到 `AnyToolResult`；
6. 转换成模型可见 output 和 code-mode result。

Router 使用本 Step 的 Registry，所以模型看到的工具与执行器保持一致。

## 10. Tool Output 不是普通字符串

一个工具结果可能需要多种视图：

-给模型的 `ResponseInputItem`；
-给 UI 的事件；
-给 telemetry 的短 preview；
-给 Hook 的结构化 payload；
-给 Code Mode 的 JSON；
-是否成功、是否含 external context 等 metadata。

将这些全部压成一个字符串，会迫使不同消费者解析日志文本。`codex-tools` 中的输出抽象让每个消费者得到适合自己的表示。

## 11. 工具选择的配置维度

工具集合会受以下条件影响：

-模型能力；
-Feature Flag；
-Plan/Default mode；
-collaboration 和 multi-agent version；
-操作系统；
-sandbox 和 permission profile；
-MCP readiness；
-App/Plugin enablement；
-Code Mode；
-Session source，例如 guardian reviewer。

所以工具列表应当是“从配置和动态状态计算出的计划”，而不是散落在启动代码里的 `registry.add()`。

## 12. 自己实现时的最小接口

一个较好的最小抽象是：

```rust
trait Tool {
    fn name(&self) -> ToolName;
    fn schema(&self) -> ToolSpec;
    fn execute(
        &self,
        context: ToolContext,
        input: Value,
    ) -> impl Future<Output = Result<ToolResult, ToolError>> + Send;
}
```

外层另设 Registry、Exposure Planner、Policy Middleware 和 Router。不要让每个工具自己决定是否对模型可见，也不要让模型提供的名字直接变成任意系统函数调用。

