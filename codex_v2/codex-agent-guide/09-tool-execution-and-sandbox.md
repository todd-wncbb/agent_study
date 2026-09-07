# 09. Tool 执行、权限、沙箱与结果回传

## 1. Prompt 不是安全边界

即使基础指令明确告诉模型“不要访问未授权目录”，模型仍可能因为误判、注入攻击或参数错误提出违规调用。因此安全必须由 Runtime 强制执行：

```text
模型建议动作
  → Runtime 解析
  → Policy / Hook
  → Approval
  → Sandbox
  → 实际执行
```

## 2. 调用解析

模型返回的 Tool Call 首先转换成内部 `ToolCall`。Runtime 校验：

-工具名是否注册；
-payload 类型是否匹配；
-JSON 是否能反序列化；
-必填字段是否存在；
-参数值是否满足额外约束。

解析错误通常应转成模型可见 Tool error，让模型有机会修正参数，而不是让整个 Session panic。

## 3. Tool Context

执行器获得的不只是参数，还需要受控上下文：

-当前 Session/Thread/Turn ID；
-本 Step 的环境；
-CancellationToken；
-approval 与 sandbox policy；
-事件发送器；
-turn diff tracker；
-extension data；
-调用来源，例如 direct、code mode 或 nested。

调用来源很重要：同一个工具由模型直接调用和由受控 Code Mode 嵌套调用，可能使用不同暴露或日志策略。

## 4. Hook 生命周期

Tool lifecycle hook 可以在执行前后观察结构化数据：

- pre-tool-use：审查、拒绝、修改策略或记录；
- post-tool-use：记录结果、补充上下文或触发后续动作。

Hook-facing payload 应与模型可见输出分离。模型需要简洁结果，Hook 可能需要命令、cwd、退出码和安全元数据。

## 5. Approval

Approval 用于无法由当前权限直接完成、但可以在用户授权后执行的动作。正确流程是：

1. 用只读检查精确解析目标；
2. 判断现有规则是否已允许；
3. 生成具体、可理解的批准请求；
4. 用户批准后仅放宽本次或匹配的规则范围；
5.在批准范围内执行；
6. 记录决定供审计和后续 Prompt 使用。

批准不能是模糊的“允许所有命令”。规则要按工具、命令前缀或资源范围表达。

## 6. Sandbox

Sandbox 的目标不是判断模型是否善意，而是从操作系统层限制实际能力。Codex 的执行路径会结合：

-文件系统读写策略；
-网络策略；
-平台实现，例如 Seatbelt、bubblewrap 或 Windows sandbox；
-当前环境；
-额外批准权限。

Shell Handler 负责把高层策略转为实际进程执行配置。模型提示中看到的权限描述必须与实际 sandbox policy 对齐，但真正有效的是后者。

## 7. Shell 与 Apply Patch

两者都是修改或观察工作区的工具，但输入语义不同：

- Shell/exec：结构化命令、cwd、yield、TTY 和 escalation；
- apply_patch：自由格式 patch，由 parser 验证目标和 hunk。

专用 patch 工具的优势是：

-变更更容易审计；
-避免复杂 shell quoting；
-可以精确限制可写路径；
-失败能返回 hunk 级诊断。

## 8. 并行执行

工具是否可并行由 Runtime 声明，而不只由模型的 `parallel_tool_calls` 决定。并行安全取决于副作用：

-两个只读搜索通常可并行；
-两个修改同一文件的 patch 不应盲目并行；
-共享 PTY/session 的操作需要串行；
-需要用户审批的调用会形成交互边界。

主循环用有序 future 集合提交结果，尽量保持 History 稳定。

## 9. 输出控制

命令可能输出数 MB 日志，而模型上下文有限。Tool Output 应包含：

-退出状态；
-关键 stdout/stderr；
-明确的截断说明；
-必要时提供继续读取的句柄；
-敏感信息脱敏；
-external-context 标记。

截断必须是确定的、有硬上限的，不能只依赖模型“不要输出太多”。

## 10. Result 如何回到模型

执行完成后，Runtime 生成与 `call_id` 对应的：

- `FunctionCallOutput`；或
- `CustomToolCallOutput`；或
- `ToolSearchOutput`。

Session 按顺序记录该 item：

1.规范化为 History 可接受的形式；
2. append 到 `ContextManager`；
3.持久化为 rollout item；
4.向客户端发 raw item/event；
5.主循环在下一次 sampling 时把它放入 Prompt.input。

## 11. 错误分类

| 错误 | 是否给模型 | 是否终止 Turn |
|---|---|---|
| 参数 JSON 错误 | 是，让模型修正 | 通常否 |
| 工具未找到 | 是 | 通常否 |
| 命令退出非零 | 是，含退出码 | 通常否 |
| 用户拒绝批准 | 是，说明限制 | 通常否 |
| Sandbox 内部故障 | 是并向 UI 报错 | 视情况 |
| Runtime invariant/panic | 不应伪装成普通结果 | 通常终止当前 Turn |
| 用户取消 | 明确 aborted | 终止当前 Turn |

关键在于：业务失败是模型可以推理的新事实；基础设施一致性失败则可能无法安全继续。

## 12. 防止重复副作用

网络重试、恢复和 stream 中断都可能让系统再次看到相似 Tool Call。应使用：

-稳定 call ID；
-已执行调用记录；
-幂等 API key；
-提交前/提交后状态；
-恢复时的未完成调用处理策略。

不要仅凭工具名和参数文本去重，因为两个相同命令可能是用户确实要求执行两次。

## 13. 实现建议

将工具安全实现为中间件流水线：

```text
Parse
  → Resolve Tool
  → Validate Schema
  → Pre Hook
  → Risk Classification
  → Approval
  → Sandbox Context
  → Execute
  → Bound/Redact Output
  → Post Hook
  → Persist Result
```

每一层只负责一个决定，并保留结构化审计信息。这样比在每个 Tool Handler 中复制审批和沙箱逻辑更可靠。

