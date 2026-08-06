# GrokBuild 可选 Tool 实现

默认 18 个 Tool 已在
[grok-build-tools-and-mcp-discovery.md](../grok-build-tools-and-mcp-discovery.md)
逐项说明。本篇覆盖注册表中存在、但需要特定 preset、配置或资源才会加入 Agent 的
GrokBuild Tool，以及 Memory Tool。

## 1. `kill_terminal_command`

- ID：`GrokBuild:kill_terminal_command`
- Args：`KillTaskToolInput { task_id }`
- Output：`KillTaskOutput`
- Kind：`KillTaskAction`
- 源码：
  [`kill_task/terminal_command.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/kill_task/terminal_command.rs)

它是 `kill_task` 的 workspace-only、subagent-free 门面，`run()` 直接委托
`KillTaskTool`。差异主要在 description 和 requirements：只要求存在 background Bash
相关能力，不向模型宣称可以终止子 Agent。底层仍使用 `TerminalBackend::kill_task()`，在
Unix 上执行 SIGTERM/SIGKILL 路径，在 Windows 上对应 Job Object 生命周期。

## 2. `get_terminal_command_output`

- ID：`GrokBuild:get_terminal_command_output`
- Args：`TaskOutputToolInput { task_ids, timeout_ms }`
- Output：`TaskOutputOutput`
- Kind：`BackgroundTaskAction`
- 源码：
  [`task_output/terminal_command.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/task_output/terminal_command.rs)

这是 `get_task_output` 的 subagent-free 门面，执行直接委托 `TaskOutputTool`。传一个 ID
得到单任务状态，传多个 ID 且 timeout 大于 0 时等待它们完成。大输出返回截断内容和
`output_file`，模型可再调用 Read。它存在的主要原因是让不支持子 Agent 的 toolset 使用
更准确的描述，而不是复制一套任务后端。

## 3. `web_search`

- ID：`GrokBuild:web_search`
- Args：`{ query: String, allowed_domains?: String[] }`
- Output：`WebSearchOutput { query, content, citations, allowed_domains }`
- Kind/Scope：`WebSearch` / Read-only
- 源码：
  [`web_search/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/web_search/mod.rs)

`run()` 从 Resources 取得预构造的 `WebSearchClient`，调用 Responses API 的 Web Search
能力，并把正文和 citations 分开保存。Tool 自身不负责认证初始化；Agent finalize 时只有
成功建立 Client 才能正常执行。`allowed_domains` 在请求层实现域名约束。

与 MCP 搜索的区别：`web_search` 搜索互联网内容；`search_tool` 搜索 MCP Tool 定义。

## 4. `web_fetch`

- ID：`GrokBuild:web_fetch`
- Args：`{ url: String }`
- Output：`WebFetchOutput`
- Kind/Scope：`WebFetch` / Read-only
- Params：代理 endpoint、缓存、响应和溢出策略
- 源码入口：
  [`web_fetch/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/web_fetch/mod.rs)

执行链路：

```text
解析 URL
→ HTTP 自动升级 HTTPS
→ SSRF / host / redirect 检查
→ 可选代理或直接 HTTP Client
→ Content-Type 与正文抽取
→ HTML 转 Markdown
→ 缓存
→ 长内容截断并将完整 artifact 写入 SessionFolder
→ 返回正文、状态和 artifact 提示
```

关键子模块：

- `ssrf.rs`：阻止 loopback、私网、危险解析结果和重定向逃逸。
- `domain.rs`：host/domain 规范化。
- `http.rs`：HTTP 请求和 redirect。
- `cache.rs`：会话缓存。
- `overflow.rs` / `artifact.rs`：超长结果落盘。
- `client.rs`：上述流程的组合。

Tool description 明确提示：认证或私有 URL 应使用对应 MCP/Connector，而不是 `web_fetch`。

## 5. `lsp`

- ID：`GrokBuild:lsp`
- Args：`LspToolInput`
- Output：`LspToolOutput(String)`
- Kind/Scope：`Lsp` / Read-only
- 入口：
  [`grok_build/lsp/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/lsp/mod.rs)
- 核心：
  [`implementations/lsp/`](../../crates/codegen/xai-grok-tools/src/implementations/lsp)

输入：

```rust
operation: LspOperation,
file_path: Option<String>,
line: Option<u32>,       // 0-based
character: Option<u32>,  // 0-based
query: Option<String>,   // workspaceSymbol
```

支持的主要操作包括：`goToDefinition`、`findReferences`、`hover`、
`goToImplementation`、`documentSymbol`、`workspaceSymbol`。Tool wrapper 从 Resources 获取
`Arc<dyn LspBackend>`，实际生命周期由 LSP Manager 管理：配置解析、语言识别、server
启动、文档同步、请求 pending map、崩溃重启和通知。

它会发出 `LspServerStarting/Ready/Retrying/Crashed/Failed` 通知。若未配置可用语言服务器，
返回明确的 unavailable 错误，而不是静默退化为 grep。

## 6. `image_gen`

- ID：`GrokBuild:image_gen`
- Args：`{ prompt, aspect_ratio = "auto" }`
- Output：统一 `ToolOutput`，成功时包含保存路径/可渲染图像信息
- Kind/Scope：`ImageGen` / Write
- 源码：
  [`image_gen/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/image_gen/mod.rs)

执行时从 Resources 获取 `ImageGenClient`，校验账户/feature 状态，调用 Imagine API，解码
base64 图像，再通过 session-scoped storage 生成不会覆盖已有文件的路径。支持的比例由
字段 Schema 描述，默认 `auto`。保存结果使用绝对路径作为内部事实，同时提示模型向用户
展示 session-relative 路径。

## 7. `image_edit`

- ID：`GrokBuild:image_edit`
- Args：`{ prompt, image: String[], aspect_ratio = "auto" }`
- Output：`ToolOutput`
- Kind/Scope：`ImageGen` / Write
- 源码：
  [`image_edit/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/image_edit/mod.rs)

引用图的解析优先级：

1. 用户附件 token，如 `[Image #1]`，通过 `AttachedImages` Resource 解析。
2. 用户明确给出的绝对路径。
3. `data:image/...;base64,...`。

空数组直接报参数错误，提醒改用 `image_gen`。单图编辑忽略输出 aspect ratio，保持输入图
比例；多图才使用 `aspect_ratio`。本地文件在请求前读取并编码，输出与 `image_gen` 共用
session storage 语义。

## 8. `image_to_video`

- ID：`GrokBuild:image_to_video`
- Args：`{ image, prompt?, duration?, resolution_name = "480p" }`
- Output：`ToolOutput`
- Kind/Scope：Video / Write
- 源码：
  [`video_gen/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/video_gen/mod.rs)

`image` 支持绝对路径、HTTPS URL 和 data URL。`duration` 接受数字或字符串，但最终只能是
6 或 10；`resolution_name` 为 480p/720p。执行过程获取 `VideoGenClient` 和
`SessionFolder`，上传/规范化输入，创建生成任务，轮询任务状态，下载结果并保存。

## 9. `reference_to_video`

- ID：`GrokBuild:reference_to_video`
- Args：`{ prompt, images[2..7], aspect_ratio, duration?, resolution_name }`
- Output：`ToolOutput`
- Kind/Scope：Video / Write
- 源码同上。

它与单图动画的区别是使用 2–7 张内容/风格参考，不要求某一张作为首帧。实现复用
Video Client、输入图规范化、异步轮询、下载和 session storage，只在请求 payload 和输入
验证上分流。

## 10. `enter_plan_mode`

- ID：`GrokBuild:enter_plan_mode`
- Args：空对象
- Output：`EnterPlanModeOutput`
- Kind：`EnterPlan`
- Notification：`PlanModeEntered`
- 源码：
  [`enter_plan_mode/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/enter_plan_mode/mod.rs)

它要求 `exit_plan_mode` 同时存在，避免进入无法退出的状态。执行时先发送通知，再解析
session plan file；若不存在则创建空文件，但不会截断已有计划。真正的只读权限切换和用户
同意流程由 orchestration/client 完成，所以 Tool 本身是“状态门”和 plan file seed，不是
完整 Plan Mode 状态机。

## 11. `exit_plan_mode`

- ID：`GrokBuild:exit_plan_mode`
- Args：空对象
- Output：`ExitPlanModeOutput`
- Kind：`ExitPlan`
- Notification：`PlanModeExited`
- 源码：
  [`exit_plan_mode/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/exit_plan_mode/mod.rs)

Tool 不接受计划正文，而是从约定的 plan file 读取。这保证提交给用户审批的内容与磁盘上
Agent 实际写入的计划一致。读取后向 client 发送包含正文的 `PlanModeExited`，并将结构化
结果返回模型。审批、反馈和模式切换仍由客户端状态机处理。

## 12. `ask_user_question`

- ID：`GrokBuild:ask_user_question`
- Args：`{ questions[] }`
- Output：`AskUserQuestionOutput`
- Kind：`AskUser`
- Notification：`UserQuestionAsked`
- 源码：
  [`ask_user_question/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/grok_build/ask_user_question/mod.rs)

每个 Question 包含 question、header、2–3 个 options 和可选 multi-select；客户端自动增加
Other。正常路径通过 mpsc 把请求交给 Shell coordinator，再由 ACP ext_method 发给 UI，
Tool 的 `run()` 等待 oneshot 返回或超时。因此它是少数会在 Tool 内部等待人类输入的实现。

迁移兼容路径在缺少 `UserQuestionSender` 时只发送 notification 并立即返回
`QuestionsSent`。超时预算通过 `AskUserQuestionParams` 和 Shell 配置层解析。

## 13. `memory_search`

- ID：`GrokBuild:memory_search`
- Args：`{ query, max_results?, min_score? }`
- Output：格式化 `ToolOutput::Text`
- Kind/Scope：`MemorySearch` / Read-only
- 源码：
  [`memory/search_tool.rs`](../../crates/codegen/xai-grok-tools/src/implementations/memory/search_tool.rs)

从 Resources 获取 `Arc<dyn MemoryBackend>`，在 global、workspace、session memory 中执行
backend 定义的相关度搜索，返回 score、source、文件路径、行范围、staleness note 和 snippet。
若 Memory 未启用，不报 runtime error，而是返回可解释文本。

## 14. `memory_get`

- ID：`GrokBuild:memory_get`
- Args：`{ path, from?, lines? }`
- Output：带行号文本
- Kind/Scope：`MemoryGet` / Read-only
- 源码：
  [`memory/get_tool.rs`](../../crates/codegen/xai-grok-tools/src/implementations/memory/get_tool.rs)

通常在 `memory_search` 后读取完整上下文。客户端 `from` 为 1-based，backend 为 0-based，
实现用 `saturating_sub(1)` 转换，因此 0 也按第一行处理。结果格式为 `N→text`，并刻意使用
`split('\n')` 保留文件结尾空行，避免显示行号与后续读取偏移不一致。

## 15. 可选 Tool 如何加入 Agent

这些 Tool 不应仅因为注册表存在就自动出现。典型加入条件：

- Web：配置启用并成功创建客户端。
- LSP：Agent 配置允许，且运行时存在 LSP backend。
- Image/Video：对应 feature/config/client 可用。
- Plan/Ask：选择 plan 或 ask-user preset。
- Memory：experimental/config 开启并注入 backend。

最终应以 `AgentDefinition.tool_config` 和 `FinalizedToolset::tool_definitions()` 为准，而不是以
源码目录是否存在为准。

