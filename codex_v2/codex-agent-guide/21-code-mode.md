# 21. Code Mode：在受控 Runtime 中组合工具

## 1. Code Mode 解决什么

普通 Direct Tool Calling 每调用一个工具，通常都需要一次模型响应：

```text
模型 → tool A → 模型 → tool B → 模型 → tool C → 模型
```

Code Mode 允许模型生成一段受控 JavaScript，在一个 cell 内进行分支、循环、转换和多个嵌套工具调用：

```text
模型 → code_mode.execute(JavaScript)
              ├─ tool A
              ├─ tool B
              └─ tool C
       → Runtime result → 模型
```

它减少模型往返，并让结构化数据处理留在代码 Runtime 中。

## 2. Code Mode 不等于 Shell

Code Mode 运行的是受控 JavaScript/V8 环境，不是直接把代码交给系统 shell。它只能通过显式暴露的 host callbacks 调用 Tool。

安全边界仍然是：

```text
JavaScript
  → CodeModeSessionDelegate.invoke_tool()
  → Core ToolRouter
  → Approval / Sandbox
  → Tool Runtime
```

代码不能因为运行在 V8 中就绕过 Tool policy。

## 3. 主要 crate

| Crate | 职责 |
|---|---|
| `code-mode-protocol` | Execute/Wait、Cell、Nested Tool Call 协议 |
| `code-mode-runtime` | V8 runtime、cell actor、globals、timer、module loader |
| `code-mode-host` | 独立进程/服务端 host |
| `code-mode` | Process/WebSocket Session Provider |
| `core/src/tools/code_mode` | Codex Tool Handler、Delegate 和 Response 适配 |

## 4. `CodeModeSession`

[`code-mode-protocol/src/session.rs`](../codex-rs/code-mode-protocol/src/session.rs) 定义持久 Session：

```rust
pub trait CodeModeSession: Send + Sync {
    fn execute(&self, request: ExecuteRequest) -> ...StartedCell;
    fn wait(&self, request: WaitRequest) -> ...WaitOutcome;
    fn terminate(&self, cell_id: CellId) -> ...WaitOutcome;
    fn shutdown(&self) -> ...;
}
```

同一 Codex Thread 的 cells 可以共享存储值；不同 Thread 的 CodeModeSession 必须隔离。

## 5. Session Provider

`CodeModeSessionProvider` 创建 Thread-owned session。当前实现包括：

-Disabled provider；
-Process-owned host provider；
-WebSocket host provider。

Provider 可以共享一个 host 进程，但每个 Thread 仍得到逻辑隔离 Session。

## 6. Execute Request

```rust
pub struct ExecuteRequest {
    pub tool_call_id: String,
    pub enabled_tools: Vec<ToolDefinition>,
    pub source: String,
    pub yield_time_ms: Option<u64>,
    pub max_output_tokens: Option<usize>,
}
```

这里的 `source` 是模型提交的 JavaScript；`enabled_tools` 明确列出当前 cell 可以调用的嵌套工具。Runtime 不能访问 Registry 中未授权给 Code Mode 的任意工具。

## 7. Cell

每次 execute 创建 `CellId` 和 `StartedCell`。Cell 可能：

-在 yield 时间内完成；
-产生部分输出后继续运行；
-等待异步嵌套工具；
-被显式 terminate；
-因错误结束。

Cell ID 让模型之后可以调用 wait，而不必让第一次 Tool Call 永久阻塞。

## 8. Runtime Response

主要状态：

```text
Yielded      cell 仍活跃，返回目前内容
Terminated   被取消或显式终止
Result       正常完成或返回 error_text
```

响应还包含有界 `FunctionCallOutputContentItem`，可以携带文本或其他支持内容。

## 9. `code_mode.execute`

Core Handler 位于 [`core/src/tools/code_mode/execute_handler.rs`](../codex-rs/core/src/tools/code_mode/execute_handler.rs)。执行顺序：

1.验证 Code Mode 可用；
2.取得 Thread 的 CodeModeSession；
3.从当前 ToolRouter 计算可嵌套工具定义；
4.构造 ExecuteRequest；
5.启动 cell；
6.等待 initial response 到 yield deadline；
7.适配成模型 Tool Output；
8.若仍运行，返回 cell ID 供 wait。

## 10. `code_mode.wait`

Wait Handler 使用 cell ID 和 yield time：

-Cell 仍运行：返回新的部分结果；
-Cell 完成：返回 final result；
-Cell 不存在：返回 missing-cell 语义，而不是 panic；
-取消：传播到等待，但是否终止 cell 由明确策略决定。

## 11. Nested Tool Call

JavaScript 内调用工具时，Runtime 生成：

```rust
CodeModeNestedToolCall {
    cell_id,
    runtime_tool_call_id,
    tool_name,
    tool_kind,
    input,
}
```

`CodeModeSessionDelegate::invoke_tool()` 把它交回 Core。Core Delegate：

1.确认工具属于本 cell enabled list；
2.构造内部 ToolCall；
3.使用当前 Step/Turn 的安全上下文；
4.通过 ToolRouter 执行；
5.把 Tool Output 转成 JSON 返回 V8。

## 12. Tool Exposure

工具在 Code Mode 中可能是：

-Direct 且也可嵌套；
-只在 Code Mode 中可用；
-Direct-model-only，不能被 JavaScript 调用；
-Hidden；
-Deferred，需要先搜索。

`spec_plan.rs` 会注册 Code Mode executors，并为可嵌套工具生成规范化名称和定义。若两个工具规范化后冲突，需要确定 winner，不能让 JS 名称随机映射。

## 13. Namespace 与工具名

JavaScript 标识符不能直接表达所有 MCP namespace 字符，因此需要规范化 Code Mode identifier。内部仍保留完整 `ToolName`，规范化名称只用于 JS 暴露面。

这是另一个“展示名称不能替代稳定身份”的例子。

## 14. `notify`

Runtime 可以通过 Delegate `notify(call_id, cell_id, text, token)` 发出中间通知，让用户看到长任务进展，而不把每次通知当成最终 Result。

通知需要有界，并关联 cell/call ID。

## 15. 持久值与隔离

同一 CodeModeSession 的多个 cell 可以共享 stored values，适合：

-缓存解析结果；
-保存中间表；
-避免把大对象往返模型。

但必须保证：

-不同 Thread 隔离；
-Session shutdown 释放值；
-值有大小和数量上限；
-不能持有越权文件句柄；
-恢复策略明确，不能假装内存值已持久化。

## 16. 取消

存在三层取消：

-模型 Tool Call 被取消；
-wait 被取消但 cell 可能继续；
-显式 terminate cell。

Runtime 必须清理 delegate 的 per-cell 状态。`cell_closed()` 是释放关联状态的边界。

## 17. 输出预算

ExecuteRequest 包含 `max_output_tokens`，协议还有默认输出上限。JavaScript 可能循环打印或聚合巨大 JSON，Runtime 必须：

-限制直接输出；
-限制 nested Tool Result；
-避免把巨大值自动序列化给模型；
-提供截断标记；
-限制定时器、执行时间和内存。

## 18. 一个示例

模型可以生成概念上类似：

```javascript
const matches = await tools.exec_command({
  cmd: "rg -n 'TODO|FIXME' src",
  workdir: "/workspace"
});

const files = parseFiles(matches.output);
const summaries = [];
for (const file of files.slice(0, 5)) {
  summaries.push(await tools.read_file({ path: file }));
}

return { files, summaries };
```

每个 `tools.*` 都回到 Core ToolRouter。V8 只负责编排和数据转换。

## 19. 何时适合 Code Mode

适合：

-多个独立查询；
-结构化数据转换；
-循环处理有限集合；
-减少模型网络往返；
-中间数据不值得全部进入模型上下文。

不适合：

-需要用户频繁判断；
-每一步都是高风险副作用；
-任务很短；
-复杂代码难以审计；
-工具不支持嵌套或返回不稳定文本。

## 20. 测试建议

-Session 间值隔离；
-execute 完成、yield、terminate；
-wait live/missing cell；
-nested function/freeform tool；
-未启用工具被拒绝；
-名称规范化冲突；
-delegate 取消和 cell_closed；
-超大输出截断；
-host 进程断开；
-WebSocket 与 process provider 行为一致；
-Tool approval 仍然有效。

