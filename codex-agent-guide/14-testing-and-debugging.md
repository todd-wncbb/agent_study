# 14. Agent 的测试与调试

## 1. 为什么单元测试不够

Agent 的关键行为跨越：

```text
Prompt → Model Event → Tool Call → Runtime → Tool Result → 下一次 Prompt
```

单独测试 Prompt Builder 或 Tool Handler 都无法证明这条链正确。功能改变 Agent 逻辑时，应优先写 Core 集成测试。

## 2. 分层测试策略

### 纯函数测试

适合：

-Tool JSON schema；
-Skill mention 解析；
-World State diff；
-History normalize；
-错误分类；
-路径和名称规则。

### Runtime 单元测试

适合：

-Registry 重名处理；
-Router 路由；
-Tool exposure；
-Hook middleware；
-输出截断。

### Core 集成测试

适合：

-Agent Loop；
-工具执行后 follow-up；
-Skill 注入；
-MCP tool discovery；
-compaction；
-resume/fork；
-权限请求；
-多 Agent。

## 3. Mock Responses API

仓库测试使用响应 mock 构造 SSE 序列。典型测试：

1.挂载第一次响应：模型产生 Tool Call；
2.提交 `Op::UserTurn`；
3.等待 Tool 执行；
4.挂载第二次响应：模型给最终 message；
5.检查两次 `/responses` 请求；
6.断言第二次 input 包含正确 call output。

测试帮助方法位于 Core tests support 的 responses 模块。应保留返回的 `ResponseMock`，通过结构化 helper 检查 request body，而不是手工层层索引 JSON。

## 4. 必测的 Agent Loop 行为

-只有 assistant message 时结束；
-Tool Call 后继续采样；
-多个 Tool Call 的结果顺序稳定；
-不可并行工具不会并发；
-用户取消传播到 stream 和 Tool；
-pending input 在下一采样出现；
-stream 在 completed 前关闭时失败；
-可重试 stream error 不重复 Tool 副作用；
-Stop Hook 可以要求继续；
-token 阈值触发 mid-turn compaction。

## 5. Prompt 测试

不要只断言 Prompt 包含某个子串。优先比较完整结构或 snapshot：

-角色顺序；
-每个 content item；
-base instructions；
-ToolSpec 列表；
-Responses Lite 分支；
-World State full/diff；
-模型切换增量。

对用户可见 UI 或文本变化，仓库要求相应 `insta` snapshot 覆盖。

## 6. Skill 测试

-多个 root 的发现顺序；
-损坏 front matter；
-禁用和 product policy；
-同名歧义；
-结构化 path mention；
-Host/Executor/Orchestrator authority；
-正文大小截断；
-Catalog token budget；
-新旧注入路径去重；
-`skills.read` 不允许跨 authority 读取。

仓库已有大量案例位于 `core-skills` 和 `ext/skills/tests`。

## 7. Tool 测试

-Spec 与 handler 名称一致；
-合法和非法参数；
-未知工具；
-重复外部工具；
-Deferred Tool 不直接可见；
-Tool Search 后加载；
-namespace merge；
-Code Mode exposure；
-Tool Output 的模型、Hook 和 telemetry 视图。

## 8. Sandbox 与审批测试

应测试结果而不是只测试 Prompt 文案：

-未授权写入被拒绝；
-允许根内写入成功；
-批准后只放宽指定范围；
-网络限制实际传给执行器；
-拒绝批准后模型收到可理解结果；
-远程执行环境使用正确 sandbox context。

不要在测试中修改进程环境变量来模拟所有配置，优先从上层注入配置或依赖。

## 9. History 和恢复测试

-缺失 Tool Result 的规范化；
-孤立 output 清理；
-不支持媒体的模型切换；
-rollback 保持结构合法；
-compact replacement 和 baseline；
-resume 重建与原 live history 相等；
-fork 后两条线程独立；
-旧 rollout 兼容。

## 10. 调试一次错误行为

推荐按证据链排查：

1.用户输入是否正确进入 protocol；
2.TurnContext 的模型、权限和模式是否正确；
3.StepContext 捕获了哪些环境/MCP；
4.World State diff 注入了什么；
5.ToolRouter 的 Registry 和 model-visible specs 各是什么；
6.最终 Responses request 是什么；
7.服务端返回了哪些 raw event；
8.Tool Call 如何解析和路由；
9.Tool Result 是否进入 History；
10.下一次请求是否包含它；
11.rollout 是否按相同顺序持久化。

## 11. 可观测性维度

至少记录：

-thread/turn/request/call ID；
-模型与 provider；
-TTFT 和总 sampling 时间；
-input/output/reasoning/cache tokens；
-工具名称、耗时、状态和输出大小；
-approval/sandbox decision；
-Skill 选择和加载结果；
-MCP server/tool identity；
-retry 和 compaction 次数；
-取消来源。

日志中不要直接暴露 secret、完整敏感 URL、headers 或未截断外部内容。

## 12. 回归测试的核心原则

-比较完整对象，避免逐字段零散断言；
-Agent 行为变化使用端到端测试；
-错误测试验证模型下一步能看到什么；
-测试确定性顺序；
-覆盖取消和恢复，而不仅是成功路径；
-对每个自动注入源验证硬大小上限；
-将协议请求作为最终证据，而不是只看内部 helper 返回值。

