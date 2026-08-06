# 12. 代码阅读顺序（推荐阅读路线）

本文是 Grok Build 的**主阅读路线图**：按什么顺序读文档、读哪些 crate、打开哪些文件、每步要搞懂什么。适合已经能跑通 `cargo check -p xai-grok-pager-bin` 的读者。

若 Rust 基础薄弱，请先读 [06_learning_path.md](06_learning_path.md)。若只想查某个功能怎么追，用 [07_code_reading_recipes.md](07_code_reading_recipes.md)。

---

## 0. 一张图：先建立全局观

```text
grok 命令
  └─ xai-grok-pager-bin          ← 第 1 站：main()、CLI 分流
       └─ xai-grok-pager         ← 第 2 站：TUI、Action/Effect
            └─ [ACP]             ← 第 3 站：见 10_acp_protocol.md
                 └─ xai-grok-shell    ← 第 4～6 站：Agent 运行时（最核心）
                      ├─ xai-grok-agent      ← prompt / agent 定义
                      ├─ xai-chat-state      ← 对话历史
                      ├─ xai-grok-sampler    ← 调 LLM
                      ├─ xai-grok-tools      ← 执行工具
                      └─ xai-grok-workspace  ← 文件 / 权限 / git
```

**8 个核心 crate（优先读完）：**

| 优先级 | Crate | 一句话 |
| --- | --- | --- |
| P0 | `xai-grok-shell` | Agent 运行时，体积最大、最核心 |
| P0 | `xai-grok-pager` | 终端 UI + ACP 客户端 |
| P0 | `xai-grok-pager-bin` | 二进制入口 |
| P1 | `xai-grok-agent` | System prompt、agent 定义、skills |
| P1 | `xai-grok-tools` | 内置工具实现 |
| P1 | `xai-grok-workspace` | 工作区、权限、VCS |
| P1 | `xai-chat-state` | 对话历史 Actor |
| P1 | `xai-grok-sampler` | HTTP 推理流 |

其余 50+ crate 见 [codegen/README.md](codegen/README.md)，按需查阅。

---

## 阶段 A：环境与地图（约 0.5 天）

### A1. 文档

| 顺序 | 文档 | 目标 |
| --- | --- | --- |
| 1 | [README.md](../README.md)（仓库根） | 项目是什么、怎么编译 |
| 2 | [01_project_map.md](01_project_map.md) | 目录分层、关键 crate |
| 3 | [08_glossary.md](08_glossary.md) | 术语：SessionActor、ToolBridge、ACP… |

### A2. 命令

```sh
cargo install dotslash          # protoc 需要
cargo check -p xai-grok-pager-bin
cargo run -p xai-grok-pager-bin -- --help
```

### A3. 验收

- [ ] 能说出四层架构：pager-bin → pager → shell → tools/workspace
- [ ] 知道开发时应用 `cargo check -p <crate>`，不要一上来 `--workspace`

---

## 阶段 B：启动与 CLI（约 0.5～1 天）

### B1. 文档

| 文档 | 目标 |
| --- | --- |
| [02_startup_modes.md](02_startup_modes.md) | TUI / headless / agent stdio / leader 等模式 |

### B2. 代码（按顺序打开）

| 顺序 | 文件 | 搞懂什么 |
| --- | --- | --- |
| 1 | `crates/codegen/xai-grok-pager-bin/src/main.rs` | `main` → `async_main`、jemalloc、模式分发 |
| 2 | `crates/codegen/xai-grok-pager/src/app/cli.rs` | `Command` 枚举、子命令定义 |
| 3 | `crates/codegen/xai-grok-pager/src/app/mod.rs` | `run()` 如何启动 TUI |
| 4 | `crates/codegen/xai-grok-shell/src/agent/app.rs` | `grok agent stdio` / leader 入口（扫一眼即可） |

### B3. 搜索练习

```sh
rg -n "match command|Command::" crates/codegen/xai-grok-pager-bin/src/main.rs
rg -n "pub enum Command" crates/codegen/xai-grok-pager/src/app/cli.rs
```

### B4. 验收

- [ ] 默认 `grok` 无参数时走 TUI（`xai_grok_pager::app::run`）
- [ ] `grok -p` 走 headless，`grok agent stdio` 走 ACP 服务端

---

## 阶段 C：TUI 架构（约 1～2 天）

### C1. 文档

| 文档 | 目标 |
| --- | --- |
| [03_tui_architecture.md](03_tui_architecture.md) | Action / Effect / event loop |

### C2. 代码（按顺序）

| 顺序 | 文件 | 搞懂什么 |
| --- | --- | --- |
| 1 | `xai-grok-pager/src/app/actions.rs` | `Action`、`Effect`、`TaskResult` |
| 2 | `xai-grok-pager/src/app/event_loop.rs` | 事件循环：输入 → dispatch → effects |
| 3 | `xai-grok-pager/src/app/dispatch/mod.rs` | `dispatch(action, app)` 路由 |
| 4 | `xai-grok-pager/src/app/dispatch/prompt.rs` | `SendPrompt` 怎么产生 effect |
| 5 | `xai-grok-pager/src/app/effects/mod.rs` | `Effect::SendPrompt` → `acp_send` |
| 6 | `xai-grok-pager/src/app/agent_view/mod.rs` | `AgentView`、`AgentState` |
| 7 | `xai-grok-pager/src/acp/spawn.rs` | 进程内 spawn `MvpAgent` + ACP channel |

### C3. 搜索练习

```sh
rg -n "SendPrompt|CreateSession|LoadSession" crates/codegen/xai-grok-pager/src/app
```

### C4. 验收

- [ ] 能画出：`按键 → Action → dispatch → Effect → acp_send`
- [ ] 知道 `AppView` 管全局，`AgentView` 管单会话

---

## 阶段 D：ACP 协议层（约 0.5～1 天）

### D1. 文档

| 文档 | 目标 |
| --- | --- |
| [10_acp_protocol.md](10_acp_protocol.md) | Client↔Agent 消息、标准方法、`x.ai/*` 扩展 |
| [codegen/xai-acp-lib.md](codegen/xai-acp-lib.md) | channel / gateway |

### D2. 代码（按顺序）

| 顺序 | 文件 | 搞懂什么 |
| --- | --- | --- |
| 1 | `xai-acp-lib/src/message.rs` | `AcpAgentMessage` / `AcpClientMessage` |
| 2 | `xai-acp-lib/src/channel.rs` | `acp_channels()`、`acp_send()` |
| 3 | `xai-acp-lib/src/gateway.rs` | Gateway 转发 |
| 4 | `xai-grok-shell/src/agent/mvp_agent/acp_agent.rs` | `impl Agent for MvpAgent`：`initialize`、`new_session`、`prompt` |
| 5 | `xai-grok-pager/src/app/acp_handler/mod.rs` | Client 如何处理 `session/update` |

### D3. 验收

- [ ] 能说出 `session/prompt` 从 pager 到 shell 的路径
- [ ] 知道 `session/update` 是 Agent 推 UI 的主通道

---

## 阶段 E：Agent 运行时与 Session（约 2～3 天）⭐ 最重要

### E1. 文档

| 文档 | 目标 |
| --- | --- |
| [04_agent_session_runtime.md](04_agent_session_runtime.md) | session、turn、actor |
| [**09_end_to_end_request_flow.md**](09_end_to_end_request_flow.md) | **从输入到多轮推理的完整链路** |

### E2. 代码（按顺序）

| 顺序 | 文件 | 搞懂什么 |
| --- | --- | --- |
| 1 | `xai-grok-shell/src/agent/mvp_agent/mod.rs` | `MvpAgent` 结构与 ext 路由 |
| 2 | `xai-grok-shell/src/session/handle.rs` | `SessionHandle`、`cmd_tx` |
| 3 | `xai-grok-shell/src/session/commands.rs` | `SessionCommand` 枚举 |
| 4 | `xai-grok-shell/src/session/acp_session_impl/run_loop.rs` | 收 `SessionCommand::Prompt`、入队 |
| 5 | `xai-grok-shell/src/session/acp_session_impl/turn.rs` | `handle_prompt`、`process_conversation_turn` |
| 6 | `xai-grok-shell/src/session/acp_session_impl/session_setup.rs` | `initialize`、`ensure_prefix_ready`、skills/AGENTS 注入 |
| 7 | `xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs` | `run_turn_via_sampler` |
| 8 | `xai-grok-shell/src/session/acp_session_impl/tool_calls.rs` | `execute_tool_calls`、`prepare_tool_call` |

### E3. 搜索练习

```sh
rg -n "handle_prompt|process_conversation_turn|execute_tool_calls" crates/codegen/xai-grok-shell/src/session
```

### E4. 验收

- [ ] 能串起：prompt → 入历史 → build_request → sampler → tool_calls → push_tool_result → 下一轮
- [ ] 知道 `SessionActor` 是单会话状态机，`MvpAgent` 是多会话 ACP 入口

---

## 阶段 F：Prompt 与 Agent 定义（约 1 天）

### F1. 文档

| 文档 | 目标 |
| --- | --- |
| [codegen/xai-grok-agent.md](codegen/xai-grok-agent.md) | AgentBuilder、frontmatter |
| [09 第三节](09_end_to_end_request_flow.md) | skills / AGENTS.md 注入落点 |

### F2. 代码（按顺序）

| 顺序 | 文件 | 搞懂什么 |
| --- | --- | --- |
| 1 | `xai-grok-agent/templates/prompt.md` | 基础 system prompt 模板 |
| 2 | `xai-grok-agent/src/config.rs` | `AgentDefinition`、`PromptMode` |
| 3 | `xai-grok-agent/src/builder.rs` | `AgentBuilder::build()` 全流程 |
| 4 | `xai-grok-agent/src/prompt/context.rs` | Extend vs Full 渲染 |
| 5 | `xai-grok-agent/src/prompt/skills.rs` | skill 发现与预加载 |
| 6 | `xai-grok-agent/src/prompt/agents_md.rs` | AGENTS.md 发现 |
| 7 | `xai-grok-shell/src/session/agent_rebuild.rs` | shell 如何调用 `AgentBuilder` |

### F3. 验收

- [ ] 区分：system prompt vs synthetic user（user_info、AGENTS、skill 列表）
- [ ] 知道改 `prompt.md` 后要跑 `encrypt_templates.py`

---

## 阶段 G：对话历史与 LLM 推理（约 1 天）

### G1. 代码（按顺序）

| 顺序 | 文件 | 搞懂什么 |
| --- | --- | --- |
| 1 | `xai-chat-state/src/handle.rs` | `ChatStateHandle` 对外 API |
| 2 | `xai-chat-state/src/actor/request_builder.rs` | `build_conversation_request`、tools 附在请求上 |
| 3 | `xai-chat-state/src/actor/mutations.rs` | `push_message`、持久化 |
| 4 | `xai-grok-sampling-types/src/conversation.rs` | `ConversationItem`、`ToolSpec` |
| 5 | `xai-grok-sampler/src/handle.rs` | `submit_and_collect` |
| 6 | `xai-grok-sampler/src/actor/request_task.rs` | HTTP 重试 |
| 7 | `xai-grok-sampler/src/stream/responses.rs` | SSE → `ToolCallDelta` |

### G2. 文档

| 文档 | 目标 |
| --- | --- |
| [codegen/xai-chat-state.md](codegen/xai-chat-state.md) | |
| [codegen/xai-grok-sampler.md](codegen/xai-grok-sampler.md) | |

### G3. 验收

- [ ] 知道 assistant `tool_use` 在工具执行**之前**写入历史
- [ ] 知道 `tool_definitions_builtins_only()` 与 MCP 工具的关系

---

## 阶段 H：工具与工作区（约 1～2 天）

### H1. 文档

| 文档 | 目标 |
| --- | --- |
| [05_tools_workspace_permissions.md](05_tools_workspace_permissions.md) | 工具注册、权限 |
| [11_mcp_protocol.md](11_mcp_protocol.md) | MCP 集成（可选，用 MCP 时读） |

### H2. 代码（按顺序）

| 顺序 | 文件 | 搞懂什么 |
| --- | --- | --- |
| 1 | `xai-grok-tools/src/bridge.rs` | `ToolBridge` 门面 |
| 2 | `xai-grok-tools/src/registry/types.rs` | `FinalizedToolset`、`tool_definitions` |
| 3 | `xai-grok-tools/src/implementations/grok_build/` | 任选一个：`read_file`、`bash` |
| 4 | `xai-grok-shell/src/session/acp_session_impl/tool_dispatch.rs` | `dispatch_tool` |
| 5 | `xai-grok-workspace/src/workspace_ops.rs` | `call_tool` local vs proxy |
| 6 | `xai-grok-workspace/src/permission/manager.rs` | 权限弹窗逻辑 |
| 7 | `xai-grok-hooks/src/dispatcher.rs` | `pre_tool_use` 钩子（可选） |

### H3. 验收

- [ ] 能说出 `prepare_tool_call` 里：解析 → 钩子 → 权限 → dispatch
- [ ] 知道 workspace 抽象了本地盘与远程 hub

---

## 阶段 I：配置与横切（按需）

| 主题 | 先看 | 代码入口 |
| --- | --- | --- |
| 配置加载 | [codegen/xai-grok-config.md](codegen/xai-grok-config.md) | `xai-grok-config/src/loader.rs` |
| 远程 settings | [codegen/xai-grok-config-types.md](codegen/xai-grok-config-types.md) | `RemoteSettings` |
| 认证 | shell README | `xai-grok-shell/src/auth/` |
| 遥测 | [codegen/xai-grok-telemetry.md](codegen/xai-grok-telemetry.md) | `xai-grok-telemetry/src/client.rs` |
| 沙箱 | [codegen/xai-grok-sandbox.md](codegen/xai-grok-sandbox.md) | `xai-grok-sandbox/src/profiles.rs` |
| 渲染层 | [codegen/xai-grok-pager-render.md](codegen/xai-grok-pager-render.md) | `appearance/`、`theme/` |

---

## 按目标选捷径

不必线性读完所有阶段。按你的目标跳转：

| 你想搞懂… | 直接读 |
| --- | --- |
| 用户输入怎么变成 LLM 请求 | 阶段 C → D → E，+[09](09_end_to_end_request_flow.md) |
| `prompt.md` / skills 怎么进模型 | 阶段 F + [09 第三节](09_end_to_end_request_flow.md) |
| 工具怎么执行、权限怎么拦 | 阶段 H + [05](05_tools_workspace_permissions.md) |
| IDE 怎么连 grok | 阶段 D + `agent/app.rs` + [10](10_acp_protocol.md) |
| MCP 服务器怎么接 | [11](11_mcp_protocol.md) + `xai-grok-mcp/src/servers.rs` |
| TUI 怎么画界面 | 阶段 C + [03](03_tui_architecture.md) + `xai-grok-pager-render` |
| 某个 crate 干什么的 | [codegen/README.md](codegen/README.md) 索引表 |

---

## 阅读时用的三个问题

每打开一个文件，先问自己：

1. **这是哪一层？** UI / ACP / Session / Tools / Workspace / 推理？
2. **数据怎么流动？** 返回值、channel、还是写共享 Actor 状态？
3. **我想追的链路经过这里吗？** 不是就跳过细节，只看 `mod.rs` 目录结构。

---

## 建议时间线（全职阅读）

| 周 | 阶段 | 产出 |
| --- | --- | --- |
| 第 1 周 | A + B + C | 能讲清启动与 TUI 数据流 |
| 第 2 周 | D + E | 能讲清 prompt 到第一轮推理 |
| 第 3 周 | F + G + H | 能讲清 prompt 组装、历史、工具循环 |
| 之后 | I + 专题 | MCP、沙箱、compaction、leader 等 |

业余阅读可把每阶段时间 ×2～3。

---

## 文档索引（完整）

| # | 文档 |
| --- | --- |
| 01 | [项目地图](01_project_map.md) |
| 02 | [启动模式](02_startup_modes.md) |
| 03 | [TUI 架构](03_tui_architecture.md) |
| 04 | [Agent Session 运行时](04_agent_session_runtime.md) |
| 05 | [工具与工作区权限](05_tools_workspace_permissions.md) |
| 06 | [Rust 新手学习路线](06_learning_path.md) |
| 07 | [读代码实战食谱](07_code_reading_recipes.md) |
| 08 | [术语表](08_glossary.md) |
| 09 | [端到端请求流程](09_end_to_end_request_flow.md) |
| 10 | [ACP 协议](10_acp_protocol.md) |
| 11 | [MCP 协议](11_mcp_protocol.md) |
| 12 | **本文：阅读顺序** |
| — | [codegen 逐 crate 详解](codegen/README.md) |

---

## 第一个下午的最小路径（3～4 小时）

若时间有限，按这个顺序读**代码 + 一篇文档**即可建立骨架：

1. `README.md` + [01_project_map.md](01_project_map.md)（30 min）
2. `xai-grok-pager-bin/src/main.rs`（20 min）
3. `xai-grok-pager/src/app/actions.rs` + `dispatch/prompt.rs`（40 min）
4. [10_acp_protocol.md](10_acp_protocol.md) 前半（30 min）
5. `xai-grok-shell/.../acp_agent.rs` 的 `prompt` 方法（30 min）
6. [09_end_to_end_request_flow.md](09_end_to_end_request_flow.md) 总览图（30 min）
7. `turn.rs` 里 `handle_prompt` 和 `process_conversation_turn` 扫一眼（40 min）

读完后你应该能回答：**「我在 TUI 敲回车后，代码大概经过哪 5 个模块？」**
