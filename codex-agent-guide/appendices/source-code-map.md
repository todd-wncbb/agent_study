# 附录 A：Codex Agent 源码地图

## 1. 如何使用这份地图

不要从 `codex-rs/core/src/lib.rs` 开始逐文件读。建议选择一条行为链，先找入口函数，再沿类型和调用向下追踪。

例如研究工具调用：

```text
session/turn.rs::try_run_sampling_request
  → stream_events_utils::handle_output_item_done
  → tools/router.rs::ToolRouter
  → tools/registry.rs::CoreToolRuntime
  → tools/handlers/*
  → Session::record_conversation_items
```

## 2. 外部控制面

### `codex-rs/core/src/thread_manager.rs`

-创建、恢复和 fork Thread；
-维护 live thread map；
-共享 models、auth、skills、plugins、MCP、state 等服务；
-创建 subagent thread。

### `codex-rs/core/src/codex_thread.rs`

-调用方持有的线程句柄；
-提交 `Op`；
-读取 `Event`；
-查询状态、关闭和等待线程。

### `codex-rs/protocol`

-客户端和 Core 共享的操作、事件、模型 item；
-`ResponseItem`、`ContentItem`、`UserInput`；
-Thread/Turn 相关协议 DTO。

## 3. Session 和 Agent Loop

### `core/src/session/mod.rs`

-Session 创建与大部分公共方法；
-捕获 StepContext；
-构建初始上下文；
-记录 conversation item；
-写 rollout；
-World State 更新。

### `core/src/session/session.rs`

-Session 数据结构；
-线程级 services 与 mutable state。

### `core/src/session/turn_context.rs`

-一次用户 Turn 的稳定配置快照。

### `core/src/session/step_context.rs`

-一次模型请求的环境、MCP、ToolRouter、AGENTS.md 快照。

### `core/src/session/turn.rs`

最重要的主链：

- `run_turn()`：外层 Turn 循环；
- `run_sampling_request()`：重试和 Prompt 重建；
- `try_run_sampling_request()`：消费模型事件；
- `build_skills_and_plugins()`：本轮能力注入；
- `built_tools()`：为 Step 构建工具。

## 4. Prompt 与模型客户端

### `core/src/client_common.rs`

- `Prompt`；
- `ResponseStream`；
-模型请求中间表示。

### `core/src/client.rs`

- `ModelClient` / `ModelClientSession`；
-构建 `ResponsesApiRequest`；
-HTTP/WebSocket 选择；
-请求与事件 stream 映射。

### `protocol/src/prompts`

-模型基础指令模板。

### `protocol/src/openai_models.rs`

- `ModelInfo`；
-上下文窗口、工具模式、reasoning、输入模态等能力。

## 5. Context 和 World State

### `core/src/context`

具体上下文片段：

-环境；
-权限；
-Personality；
-Apps/Plugins；
-Multi-agent；
-模型切换；
-时间和 token budget。

### `core/src/context/world_state`

-各类 World State section；
-full/diff 渲染；
-AGENTS.md；
-环境、权限、工具等动态状态。

### `core/src/session/world_state.rs`

-从 Step 构建当前 World State；
-Session 级记录和更新入口。

### `context-fragments`

-统一的 Contextual Fragment trait 与标记机制。

## 6. History 和 Compaction

### `core/src/context_manager/history.rs`

- `ContextManager`；
-append、truncate、rollback；
- `for_prompt()`；
-World State baseline。

### `core/src/context_manager/normalize.rs`

-Call/Result 配对；
-孤立 output；
-媒体兼容；
-请求前 History 修复。

### `core/src/compact.rs`

-本地 compact；
-pre-turn/mid-turn 初始 context 插入；
-replacement history 构造。

### `core/src/compact_remote.rs`

-远程 compact endpoint；
-输出过滤和安装 checkpoint。

## 7. Skills

### `core-skills/src/model.rs`

- `SkillMetadata`；
- `SkillLoadOutcome`；
-scope、root、filesystem 映射。

### `core-skills/src/loader.rs`

-Host/Executor Skill 加载入口；
-front matter 解析；
-配置规则。

### `core-skills/src/loader/discovery.rs`

-目录发现、深度/数量限制和符号链接。

### `core-skills/src/service.rs`

-Skill snapshot 缓存和刷新。

### `core-skills/src/injection.rs`

-显式 mention；
-读取 Host Skill 正文；
-注入 telemetry。

### `ext/skills/src/extension.rs`

-Skill extension 生命周期；
-thread/turn/world-state contributors；
-Host、Executor、Orchestrator catalog 合并；
-Skill 正文注入。

### `ext/skills/src/render.rs`

-有预算的模型可见 Skill catalog。

### `ext/skills/src/dynamic_skill_selector`

-词法与字符相关性选择算法。

### `ext/skills/src/tools`

- `skills.list` 和 `skills.read`。

## 8. Tools

### `codex-rs/tools/src/tool_spec.rs`

-模型可见 `ToolSpec`。

### `codex-rs/tools/src/tool_definition.rs`

-工具定义和 schema 表示。

### `codex-rs/tools/src/tool_call.rs`

-扩展层 Tool Call 类型。

### `codex-rs/tools/src/tool_output.rs`

-多消费者 Tool Output。

### `core/src/tools/registry.rs`

- `CoreToolRuntime`；
- `ToolRegistry`；
- `ToolExposure`；
-生命周期与执行适配。

### `core/src/tools/spec_plan.rs`

-组合 Core/MCP/Extension/Dynamic/Hosted 工具；
-Code Mode；
-Deferred Tool Search；
-模型可见 specs。

### `core/src/tools/router.rs`

-模型 item 转内部 ToolCall；
-按 ToolName 找 Runtime；
-dispatch。

### `core/src/tools/handlers`

具体工具：

-shell/unified exec；
-apply patch；
-MCP resource；
-plan/request user input；
-view image；
-tool search；
-multi-agent；
-extension tools。

### `core/src/tools/lifecycle.rs`

-Tool 前后 Hook 生命周期。

### `core/src/tools/parallel.rs`

-Tool Future、并发和取消。

## 9. MCP、Plugin 与 Connector

### `core/src/session/mcp*.rs`

-MCP 生命周期、预热、刷新和 Step binding。

### `core/src/mcp_tool_exposure.rs`

-MCP Tool 注册和 exposure。

### `codex-rs/codex-mcp`

-MCP connection manager 与协议调用。

### `codex-rs/plugin` / `core-plugins`

-Plugin manifest、加载与推荐。

### `codex-rs/connectors`

-App/Connector identity、合并和可访问性。

## 10. 模型 API 和传输

### `codex-rs/codex-api`

-Responses endpoint；
-SSE/WebSocket event；
-Provider API 配置；
-API error。

### `codex-rs/codex-client`

-`RetryPolicy`；
-`sse_stream()`；
-`RequestTelemetry`。

### `codex-rs/http-client`

-共享 HTTP client；
-proxy route；
-custom CA；
-connection pool；
-redirect 安全。

## 11. 持久化

### `core/src/session/rollout_reconstruction.rs`

-从 rollout 重建 History 和上下文状态。

### `codex-rs/rollout`

-rollout item 与存储表示。

### `codex-rs/state`

-线程 metadata、查询和状态持久化。

### `core/src/thread_manager.rs`

-start/resume/fork 的编排入口。

## 12. Multi-Agent

### `core/src/agent`

-Agent control、角色、spawn depth、状态与父子关系。

### `core/src/session/multi_agents.rs`

-Session 侧多 Agent 语义。

### `core/src/tools/handlers/multi_agents*`

-spawn、message、followup、wait、interrupt、list 等 Handler 和 schema。

### `codex-rs/agent-graph-store`

-Agent graph 持久状态。

## 13. 测试入口

- `core/tests/suite`：Agent 端到端集成测试；
- `core/src/session/*_tests.rs`：Session/Turn 行为；
- `core/src/tools/*_tests.rs`：Registry/Router/安全；
- `core-skills/src/*_tests.rs`：Host Skill；
- `ext/skills/tests`：跨 authority Skill extension；
- `codex-api/tests`：API client 和 SSE；
- `app-server/tests`：公开 JSON-RPC 行为。

