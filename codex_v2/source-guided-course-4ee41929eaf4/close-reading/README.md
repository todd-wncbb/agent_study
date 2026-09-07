# Codex 源码精读系列

> 源码基线：`4ee41929eaf4`。本目录中的“第几行”只用于帮助你在这个固定提交中定位；源码更新后，请优先搜索符号名。

这个系列不再以“一个主题涉及哪些模块”为主要组织方式，而是选择一个足够重要的函数，慢下来逐块阅读。

普通主题章更像地图：它告诉你一条链路经过哪些地方。源码精读更像拿着放大镜走路：它会解释局部变量为什么存在、某个检查为什么排在这里、失败时哪些状态已经改变、测试究竟证明了什么。

## 适合怎样阅读

每篇建议读三遍：

1. 第一遍只读“白话全景”和贯穿案例，不要求看懂 Rust。
2. 第二遍对照源码块，逐个确认输入、输出和内存状态变化。
3. 第三遍阅读测试证据，并完成文末练习。

遇到不认识的英文词，先看本篇末尾的局部术语表；仍不清楚时，再查[课程术语总表](../glossary.md)。

## 精读时使用的固定问题

每一块代码都问六个问题：

1. 它收到什么数据？
2. 它产生或修改什么数据？
3. 它只改内存，还是已经产生外部副作用？
4. 它可能在哪些地方提前返回？
5. 为什么必须放在前一块之后、后一块之前？
6. 哪个测试能证明我们的解释不是猜测？

## 代码标记约定

- “源码摘录”表示来自固定提交，可能省略与当前解释无关的行。
- “等价白话代码”是教学性伪代码，不是仓库里的真实 Rust。
- “推论”表示根据多处源码和测试得出的结论，不把它冒充为代码注释写明的设计意图。
- 官方文档只用于说明公开配置概念；内部调用顺序和错误行为以固定提交源码为准。

## 路线图

| 顺序 | 精读对象 | 主要问题 | 状态 |
|---|---|---|---|
| 01 | [`ConfigManager::apply_edits`](01-config-manager-apply-edits.md) | 一批配置修改怎样做到先验证、后落盘，并报告版本冲突与覆盖 | 已完成 |
| 02 | [`ModelsManager::list_models`](02-models-manager-catalog-refresh.md) | 本地缓存、远端目录、认证过滤和 ETag 怎样汇合 | 已完成 |
| 03 | [`Session::spawn` 与 `Session::new`](03-session-initialization.md) | 一条 thread 怎样组装模型、配置、状态、服务、持久化与首事件 | 已完成 |
| 04 | [`run_turn`](04-turn-main-loop.md) | 模型输出、工具执行、结果回填、重采样与 Turn 收尾怎样组成循环 | 已完成 |
| 05 | [`ToolRouter` 与 `ToolRegistry`](05-tool-dispatch.md) | 工具名怎样找到 handler，Hook、审批、Sandbox 和执行环境在哪里介入 | 已完成 |
| 06 | [Unified exec](06-unified-exec.md) | 一个命令怎样启动、流式输出、返回 session id、接收 stdin 并安全结束 | 已完成 |
| 07 | [App-server JSON-RPC 分派](07-app-server-json-rpc-dispatch.md) | 一条 JSONL 消息怎样变成 typed handler 调用 | 已完成 |
| 08 | [Serialization scope](08-request-serialization-scope.md) | 并发请求怎样按资源键排队而不把整个服务器串行化 | 已完成 |
| 09 | [Rollout resume](09-rollout-resume.md) | 历史记录怎样重建 thread，而不是简单复制 UI 文本 | 已完成 |
| 10 | [MCP tool call 生命周期](10-mcp-tool-call-lifecycle.md) | discovery、审批、调用、结果和刷新怎样闭环 | 已完成 |
| 11 | [Context compaction](11-context-compaction.md) | 长历史怎样变成 replacement history，并保持工具调用配对与上下文基线 | 已完成 |
| 12 | [Sandbox approval lifecycle](12-sandbox-approval-lifecycle.md) | 命令为什么有时直接运行、有时询问、有时失败后请求升级 | 已完成 |
| 13 | [Managed network approval](13-managed-network-approval.md) | 网络请求怎样经过代理策略、host 级审批与规则持久化 | 已完成 |
| 14 | [Apply patch lifecycle](14-apply-patch-lifecycle.md) | Patch 怎样被解析、授权、写入并回填结果 | 已完成 |
| 15 | [Tool output 与 context feedback](15-tool-output-context-feedback.md) | 工具结果怎样规范化、截断、配对并推动下一轮 sampling | 已完成 |
| 16 | [Cancellation 与 interruption lifecycle](16-cancellation-interruption-lifecycle.md) | 用户停止、steer、Turn 切换和 Session shutdown 怎样统一终态 | 已完成 |
| 17 | [User input admission 与 pending queue](17-user-input-admission-pending-queue.md) | 用户消息怎样启动新 Turn、steer 当前 Turn、等待持久化或因竞态失败 | 已完成 |
| 18 | [UserPromptSubmit Hook 与 additional context](18-user-prompt-submit-hook-additional-context.md) | 输入进入模型历史前怎样被检查、阻止或追加上下文 | 已完成 |
| 19 | [Turn/Item event projection 与客户端状态重建](19-turn-item-event-projection-client-state.md) | Core history、生命周期事件和 App-server snapshot 怎样形成 UI 状态 | 已完成 |
| 20 | [Thread watch、订阅与多客户端路由](20-thread-watch-subscription-multi-client-routing.md) | 多个连接怎样观察同一 thread，并与活跃状态和自动卸载协调 | 已完成 |
| 21 | [App-server 反向请求与待决响应收口](21-server-request-pending-response-lifecycle.md) | 审批和用户输入怎样选中客户端、等待响应，并在断线或 Turn 结束时取消 | 已完成 |
| 22 | [Outgoing router、传输写入与交付边界](22-outgoing-router-transport-write-delivery-boundaries.md) | typed 消息怎样定向连接、排队并真正写入 WebSocket/stdio | 已完成 |
| 23 | [Incoming transport、envelope 分类与 handler 准入](23-incoming-transport-envelope-handler-admission.md) | 一条 JSONL/WebSocket 消息怎样通过解析、初始化门、RPC gate 和 typed dispatch | 已完成 |
| 24 | [Initialize 握手、capability 协商与连接状态发布](24-initialize-handshake-capability-session-publication.md) | 一次 initialize 怎样提交 connection session，并安全开放定向通知和广播 | 已完成 |
| 25 | [Request tracing、context ownership 与延迟回包](25-request-tracing-context-ownership-delayed-response.md) | W3C parent、client metadata 和 request span 怎样从入口保持到最终 response/error | 已完成 |
| 26 | [Connection teardown、resource ownership 与有界排空](26-connection-teardown-resource-ownership-bounded-drain.md) | RPC gate、后台任务、pending callback、订阅和进程资源怎样按顺序关闭 | 已完成 |
| 27 | [Analytics、telemetry projection 与业务终态](27-analytics-telemetry-projection-business-terminal.md) | request/response/notification、错误和 latency 怎样记录，观测失败为何不能破坏主流程 | 已完成 |
| 28 | [Error taxonomy、structured payload 与恢复策略](28-error-taxonomy-structured-payload-recovery.md) | JSON-RPC code、CodexErr、TurnError 和 retryable failure 怎样逐层转换 | 已完成 |
| 29 | Rollout、state DB 与历史修复 | partial write、旧 schema、损坏记录和 live runtime 不一致时怎样恢复 | 计划中 |

## 这一系列不会做什么

- 不会为了逐行而逐行解释 `use` 列表和机械样板代码。
- 不会把一个大文件原封不动复制一遍。
- 不会只给中文翻译而不解释状态变化。
- 不会因为测试名看起来相符，就宣称测试证明了它没有断言的性质。

返回[课程总目录](../README.md)。
