# 04：工具系统与安全边界

## 先说人话：模型不是操作系统用户

模型输出“运行 `rm file`”只是生成了一段结构化文字，不会自动删除任何东西。真正执行前，Codex runtime 还要确认：

- 是否真的存在这个工具；
- 参数能否解析；
- 当前策略是否需要用户批准；
- sandbox 是否允许访问目标；
- 输出最多能有多大。

可以把模型看成提出施工方案的设计师，runtime 是审核和施工团队。设计师不能因为在图纸上写了“拆墙”，墙就自动消失。

## 1. 最重要的原则

**模型只能提出 tool call；Codex runtime 决定这个调用是否存在、是否允许、在哪里执行，以及返回什么。**

这条边界把概率性的模型输出与有副作用的系统动作隔开。

## 2. 一次调用经过的形态

```text
ToolSpec（模型看到的能力）
  ↓ 模型生成
ResponseItem::FunctionCall / CustomToolCall / ToolSearchCall
  ↓ ToolRouter::build_tool_call
ToolCall { tool_name, call_id, payload, ... }
  ↓ registry 查找 runtime + 策略/审批/sandbox
ToolInvocation
  ↓ 执行
dyn ToolOutput
  ↓ to_response_item(call_id, payload)
ResponseInputItem（下一次模型请求可见）
```

## 3. ToolRouter 的双重职责

`ToolRouter` 同时持有：

- `registry`：runtime 真正能路由的工具；
- `model_visible_specs`：本 step 暴露给模型的 schema。

这两个集合相关但不能混为一谈。工具可能已注册却延迟暴露，也可能因 feature、模型能力、MCP snapshot 或 policy 而不在当前请求中出现。

## 4. Call ID 是闭环关键

Tool call 与 tool output 通过 `call_id` 配对。缺失、重复、被截断或顺序错误都会破坏模型上下文的一致性。`ContextManager::for_prompt()` 和相关边界函数会维护/修复模型需要的结构，但不能把任意损坏历史都视为合法。

例如：

```text
call-7：运行 core 测试
call-8：读取配置文件

output(call-7)：测试失败日志
output(call-8)：配置文件内容
```

如果两个 output 的 ID 对调，模型会误以为配置文件内容是测试输出，后面的判断即使语言上流畅也会建立在错误事实之上。

## 5. 并发并不是默认假设

`ToolRouter::tool_supports_parallel()` 查询 registry 的能力。即使模型一次返回多个 tool call，也不表示所有工具都适合无序并发。执行层还要考虑：

- 工具自身是否声明可并行；
- 输出顺序是否要稳定；
- 取消时是否等待 runtime；
- 多个写操作是否存在资源冲突。

`try_run_sampling_request()` 使用 `FuturesOrdered`，体现“可以有 in-flight 工作，但交付顺序仍需可预测”的设计取向。

## 6. 审批与 sandbox 是不同层

- **审批策略**回答：执行前是否需要用户授权。
- **sandbox policy**回答：即使获准，进程在 OS/远端执行环境里实际能访问什么。
- **tool handler/runtime**回答：参数怎样解析、命令怎样启动、输出怎样截断和编码。
- **hooks/guardian/policy**可能在执行前后追加检查或反馈。

批准一个动作不等于获得无限系统权限；sandbox 允许访问也不等于产品策略一定允许调用。

### 一个具体例子

用户批准“运行测试”，表示允许尝试执行命令。但测试脚本随后试图写入 `/etc/example`：

- approval：用户已经同意运行测试；
- sandbox：仍然可以拒绝写 `/etc/example`；
- runtime：把 permission denied 和退出码收集为工具结果；
- 模型：看到失败结果后，决定是否提出一个范围明确的新授权请求。

因此“用户批准了，为什么还失败”并不是矛盾。

## 理解检查

- 模型看到 ToolSpec 是否代表该工具必定能成功执行？
- 用户批准某条命令后，sandbox 还有没有作用？
- 为什么工具输出必须有大小上限？

## 7. 修改工具时的核对清单

1. 模型可见的名称和 schema 在哪里生成？
2. registry 如何注册对应 runtime？
3. payload 类型与 handler 期待的类型是否一致？
4. 是否支持并行、取消和重试？
5. approval 与 sandbox 语义是什么？
6. 输出是否有硬上限，是否安全地进入模型上下文？
7. app-server 是否需要转发审批或动态 tool request？
8. 是否有 agent 集成测试覆盖 call → output → follow-up？

## 本章词汇表

| 词语 | 直译 | 在工具系统中的意思 |
|---|---|---|
| Tool spec | 工具规格 | 给模型看的名称、说明和参数 schema |
| Handler | 处理器 | 真正接收工具参数并实现行为的代码 |
| Router | 路由器 | 根据工具名找到对应 Handler |
| Registry | 注册表 | 保存当前可用工具实现的集合 |
| Approval | 批准 | 允许 Runtime 尝试动作，不代表取消沙箱 |
| Sandbox | 沙箱 | 执行端实际强制文件、网络和进程边界的机制 |
| Parallel-safe | 可安全并行 | 多次调用重叠执行不会破坏状态或顺序语义 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

- 阅读 `core/src/tools/router.rs::build_tool_call()` 的 exhaustive match。
- 阅读 `core/src/tools/context.rs` 中不同 `ToolOutput::to_response_item()`。
- 在 `core/src/tools/registry.rs` 查找 unsupported tool 的错误路径。
- 在 `core/tests/suite/tools.rs` 选择一个工具测试，确认它断言了 outbound request 中的 function call output。
