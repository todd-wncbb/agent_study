# Tool 的权限、并发、输出、提醒与安全边界

Tool 的正确行为不只在各自 `run()` 中。很多关键保证由注册表、Resources、Workspace、Shell
和 Reminder 共同实现。

## 1. Read-only 是能力声明，不一定等于零写入

`ToolCapabilities` 提供 `is_read_only` 和 `ToolScope`，用于权限 UX 和审批策略。例如
EnterPlanMode 标为 Read，因为它在产品上是模式门；实现仍可能在 session 管理目录 seed 一个
空 plan file。审计 Tool 时要同时看 capability、实际 Resource 调用和 orchestration 行为。

典型分类：

- Read：read/list/grep/web/LSP/memory。
- Write：search_replace/apply_patch/write/image/video/kill。
- Execute：Bash、Task、Workflow、Monitor、Scheduler。

## 2. 路径解析与 display cwd

fork/worktree session 可能有真实 cwd 和展示 cwd。输入路径通过 `resolve_model_path` 映射到真实
文件系统，输出则改写回 display cwd，避免模型看到 overlay 临时路径。各 Tool 不应自行简单
`cwd.join(input)` 后直接把 canonical path 暴露给模型。

## 3. 文件操作锁

并发 Agent 或并行 Tool 可能同时修改同一文件。共享
[`editor_infra/file_operation_lock.rs`](../../crates/codegen/xai-grok-tools/src/implementations/editor_infra/file_operation_lock.rs)
按目标路径串行化重叠操作。ApplyPatch、SearchReplace、OpenCode Write/Edit 等应在读取旧状态和
写入新状态的完整临界区持锁，否则检查和写入之间存在 TOCTOU。

## 4. 文件系统抽象

Tool 从 Resources 获取 `AsyncFileSystem`，而不是散落使用 `std::fs`。这允许本地、受限、远程或
测试 backend 复用相同实现，并把路径权限、错误映射和通知集中在 Workspace 层。

## 5. Terminal 与后台任务

Bash/Monitor/Task/Scheduler 的长生命周期不塞在一次 request future 中。后台命令由
Terminal actor 管理并返回 task ID；输出持续写入文件/环形状态，`get_task_output` 读取快照或
等待完成，`kill_task` 做统一取消。子 Agent 有自己的 coordinator，但通过统一任务查询 Tool
汇总为相同模型协议。

## 6. 输出预算和 artifact

模型可见输出有字符/字节预算。实现通常保留：

- 完整原始结果。
- 截断后的 Prompt 文本。
- truncation 标记。
- 完整输出 artifact path。

截断必须在 UTF-8 边界进行；图片/PDF 使用多模态 block 而不是把二进制写进文本。WebFetch、
Bash 等在超预算时将完整内容写进 `SessionFolder`，并提示模型后续使用 Read 分段读取。

## 7. Reminder 执行顺序

一次 Tool 成功/失败转换为 `ToolOutput` 后，cross-cutting reminder 检查输出类型：

1. LSP diagnostics 可在编辑后补充诊断。
2. Task completion 可提醒后台状态变化。
3. Skill discovery 根据访问/编辑路径更新 SkillManager。

Skill reminder 本身只更新 tracker；Session 随后 drain pending reconciliation，再把 synthetic
system-reminder 写入 conversation。这避免在持有 Resources lock 时做文件 I/O 或修改聊天状态。

## 8. Requirements 是 finalize-time 能力图

`Expr<ToolRequirement>` 支持 And/Or/Value。它既可表达“存在某种 ToolKind”，也可要求具体
namespace:id 及参数条件。例如：

- Enter 和 Exit Plan 必须成对。
- 仅 background Bash 开启时才需要 output/kill helpers。
- Reminder 只有在存在相关 Read/Edit/List kind 时才注册。

requirements 防止产生“模型能调用入口，但系统缺少完成链路的辅助能力”的死配置。

## 9. Tool 名称和参数重写

内部 ID、canonical name 和 client name 是三个层次。Preset 可以重命名 Tool 和参数；
TemplateRenderer 同步改写 description 内引用；输入层再把模型字段归一化回 Rust Args。审计日志
和 MCP 纠错应优先使用内部唯一 ID，用户文案使用 client name。

## 10. MCP 安全边界

`search_tool` 只读 Tool metadata/index，不执行远端副作用。`use_tool` 才调用目标 MCP，并处理：

- Tool 名称必须有效。
- native Tool 误传时给出纠错。
- 非限定名尝试唯一候选解析。
- local MCP 与 managed gateway 分发。
- 远端结果截断和 ContentBlock 转换。

是否允许某个 MCP Server、OAuth/session 权限和网关认证不由 BM25 索引决定。

## 11. WebFetch SSRF

WebFetch 必须把 URL parsing、DNS/host 检查和 redirect 每跳验证看成一个安全单元。只验证初始
URL 不够，因为公网 URL 可以重定向到 loopback/私网。HTTP 自动升级 HTTPS、代理模式和缓存
key 也不能绕过相同规则。

## 12. 媒体输入

ImageEdit 的 `[Image #N]` 是 UI display token，不是文件路径。Session 每轮用
`AttachedImages` 建立 token 到 durable path/data URL 的映射，并整体替换旧 registry，避免
上一轮附件被错误复用。Tool 拒绝模型臆造的 token。

## 13. 持久化

`ResourcesPersistence` 为需要跨调用保存的类型做后台 snapshot/write。不是所有 Resource 都能
序列化：Terminal、channel、client 等是 ephemeral；Todo、Goal、Skill announced state 等由各
自生命周期选择性持久化。关闭时 `flush_persistence()` 只冲刷已排队 snapshot，
`save_and_flush_persistence()` 会先抓取一次最新内存状态。

## 14. 如何验证一篇 Tool 文档

对每个 Tool 至少做以下源码核对：

```text
registry/types.rs         是否注册
xai-grok-agent/config.rs  哪个 preset 启用/重命名
ToolMetadata impl         namespace/kind/requirements/description
Tool impl                 Args/Output/id/run
types/output.rs           模型结果转换
Resources                 实际后端依赖
Reminder                  调用后副作用
tests                     边界和回归语义
```

仅查看 struct 名称或 description 不足以判断权限、动态可用性和真实执行路径。

