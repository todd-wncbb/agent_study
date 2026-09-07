# 分层阅读路线

## 使用方法

不要同时追求“覆盖所有 crate”和“理解完整运行时”。先选择一个可验收目标，完成后再扩张。每个阶段都要求输出一张自己画的图或一段口头解释；只打开过文件不算完成。

## 路线一：3 小时建立地图

适合第一次接触仓库，目标是能向别人解释主要组件和一次请求的大致路径。

### 第 1 小时：入口和边界

阅读：

1. 本套文档的 [项目导读](01-project-orientation.md)。
2. [Workspace 全景图](../01-architecture/01-workspace-map.md) 的 A 级表格。
3. 根 `Cargo.toml` 的 `[workspace]` 和 `[workspace.dependencies]`。
4. `xai-grok-pager-bin/Cargo.toml`。
5. `xai-grok-pager-bin/src/main.rs` 中 `main` 与主要 `Command` 分支。

验收：不看资料画出“binary → pager/shell → sampler/tools/workspace”的图，并说明它是阅读模型而非完整 Cargo 图。

### 第 2 小时：追一次请求

阅读目标：找到以下节点，不要求读懂内部实现。

```text
用户输入
→ Session 接收
→ 请求上下文组装
→ Sampling
→ 流式事件
→ Tool call（如果有）
→ Tool result
→ Session/UI 更新
```

优先搜索：

```sh
rg "run_headless|run_stdio_agent|pub async fn run" crates/codegen/xai-grok-{pager-bin,pager,shell}
rg "prompt|sample|tool_call|tool_result" crates/codegen/xai-grok-shell/src/session
```

验收：为每个节点至少写出一个文件路径；不确定的地方标问号，不能凭名称补全。

### 第 3 小时：选一个工具验证

推荐追读文件工具或 shell 工具：

1. 找到工具定义和 schema。
2. 找到注册位置。
3. 找到 dispatch。
4. 找到 permission 判断。
5. 找到 workspace 的实际操作。
6. 找到 Tool Result 回到会话的位置。

验收：能解释“模型请求执行”和“宿主执行副作用”之间至少隔了哪些边界。

## 路线二：2～3 天系统学习

目标是理解核心 runtime，能够定位大多数功能问题。

### 阶段 A：启动和配置

- `xai-grok-pager-bin/src/main.rs`
- `xai-grok-pager/src/app/mod.rs`
- `xai-grok-shell/src/agent/app.rs`
- `xai-grok-config`

问题清单：

- CLI 在哪里定义、哪里解析、哪里消费？
- TUI、headless、stdio、leader 如何分流？
- tracing、更新和认证在主业务前何时发生？
- 哪些配置在入口处覆盖，哪些延迟到 session？

### 阶段 B：Agent 和 Session

- `xai-grok-agent/src/lib.rs` 及其 `agent`、`builder`、`prompt`
- `xai-grok-shell/src/session/mod.rs`
- session 中的 user message、events、turn completion 与 signals

问题清单：

- Agent 持有什么，Session 持有什么？
- 一次 turn 从哪个事件开始、由什么标记结束？
- session 对外暴露 handle 还是直接共享内部状态？
- 取消信号怎样传播？

### 阶段 C：Sampling 和上下文

- `xai-grok-shell/src/sampling/`
- `xai-grok-sampler`
- `xai-grok-sampling-types`
- session compaction 与 `xai-grok-compaction`

问题清单：

- 一次模型请求的最终输入类型是什么？
- streaming event 类型在哪个 crate 定义？
- tool call 如何导致下一轮 sampling？
- token 超限前后，历史发生了什么变化？

### 阶段 D：工具和权限

- `xai-grok-shell/src/tools/`
- `xai-grok-tools`
- `xai-tool-runtime` / `xai-tool-types`
- `xai-grok-workspace`
- `xai-grok-sandbox`

问题清单：

- 工具列表在什么时候冻结或重建？
- schema、dispatch 和具体副作用分别由谁负责？
- permission decision 的输入是什么？
- 拒绝、取消和执行失败如何表示给模型？

### 阶段 E：UI 和协议

- `xai-grok-pager`
- `xai-grok-pager-render`
- `xai-acp-lib`
- shell session 中 `acp_*` 模块
- shell session 中 `mcp_*` 模块与 `xai-grok-mcp`

问题清单：

- TUI 和 ACP 是否复用同一个 session 核心？
- session event 如何映射到不同 surface？
- MCP 生命周期属于 agent、session 还是全局服务？

### 阶段 F：持久化、恢复与测试

- session persistence、replay、fork、summary
- `xai-chat-state`
- `xai-sqlite-journal`
- `xai-grok-test-support`
- PTY harness

问题清单：

- 哪些数据是事实日志，哪些是派生视图？
- 崩溃恢复和正常 restore 是否走同一路径？
- 如何在不调用真实模型的情况下测试一次 turn？

## 路线三：2～4 周深入掌握

### 第 1 周：主链与状态所有权

- 完成 3 小时路线。
- 精读启动、prompt、sampling、tool 和 permission 五条链。
- 为每条链维护一份类型清单：创建者、所有者、借用者、销毁条件。
- 运行核心 crate 的定向测试。

周验收：能够从一个 UI 现象反向定位到 session 和 runtime，不依赖全文搜索同一句提示文本。

### 第 2 周：并发、取消和恢复

- 追踪 Tokio task、专用线程、actor 和 channel。
- 记录每个 channel 的发送者、接收者、容量、关闭语义。
- 研究中途取消、permission 等待、tool timeout、provider retry。
- 研究 compaction、persistence 和 replay。

周验收：画出一次 turn 的并发时序图，并解释三种非正常结束方式。

### 第 3 周：扩展边界

- ACP、MCP、hooks、plugins、skills、memory。
- subagent、workflow、worktree。
- managed config、auth、remote services。

周验收：选择一个新能力，指出它需要接入的配置、prompt/tool、session、权限、持久化和 UI/协议边界。

### 第 4 周：修改与验证

- 完成一个小型只读工具实验。
- 添加或修改一个 session event。
- 为失败和取消路径补测试。
- 使用日志/trace 复盘一次真实执行。
- 写一篇自己的 walkthrough，并与源码复核。

周验收：提交一份“改动影响矩阵”，列出功能、协议、安全、恢复、测试和文档影响。

## 按目标走捷径

### 只想理解 Agent Loop

顺序：

1. `xai-grok-agent`
2. shell session 的 turn/user-message 入口
3. shell sampling
4. `xai-grok-sampler`
5. shell tools bridge
6. tool result 回流与 turn completion
7. compaction 和 retry

### 只想理解工具系统

顺序：

1. `xai-tool-types`
2. `xai-tool-runtime`
3. `xai-grok-tools-api`
4. `xai-grok-tools`
5. shell tools bridge/context
6. workspace 执行
7. permission 和 sandbox
8. MCP 动态工具

### 只想理解 TUI

顺序：

1. pager application 入口
2. 输入事件到 command/message
3. app state 更新
4. session update 消费
5. scrollback 与渲染模型
6. textarea、inline/minimal 模式
7. PTY harness 与 snapshot

不要先读所有 widget；先追“提交 prompt”和“收到 token”两个事件。

### 只想理解权限与安全

顺序：

1. folder trust
2. tool permission 数据类型
3. shell/tool 调用的审批入口
4. command/path 分类
5. workspace enforcement
6. OS sandbox
7. managed policy
8. subagent 和 MCP 的继承/隔离

任何结论都要同时检查 allow、ask、deny 三种结果。

### 想借项目学习大型 Rust 工程

选择真实案例学习：

- trait 和依赖倒置：tool/workspace 边界；
- actor/channel：session 与 UI；
- enum 状态机：sampling、task 或 workflow；
- serde：ACP/MCP/config/persistence；
- cancellation：turn、terminal 和网络请求；
- 测试隔离：test support、mock inference、PTY harness。

## 每次阅读的固定动作

### 阅读前

写下：输入是什么、输出是什么、谁可能拥有状态、失败会在哪里发生。

### 阅读中

只记录五类内容：入口、核心类型、状态所有者、异步边界、外部副作用。其余细节先放入“待回看”。

### 阅读后

1. 不看源码重画流程。
2. 用 `rg` 找到每个节点。
3. 找一个测试验证关键不变量。
4. 写下一个尚未回答的问题。
5. 第二天用五分钟复述。

## 反模式

- 从某个大 crate 第一行读到最后一行。
- 按文件名猜调用方向。
- 只读 trait，不找 production implementation。
- 只读 happy path，不看 cancellation 和 error。
- 看到 `Arc<Mutex<_>>` 就笼统写成“线程安全”。
- 把 UI chat history 当成模型实际上下文。
- 复制代码很多，却说不出状态由谁拥有。

## 本篇术语表

| 名词 | 白话解释 | 在本篇中的具体含义 |
| --- | --- | --- |
| 调用链 | 一个函数或组件怎样依次调用下一个 | 阅读时从入口追到最终副作用或返回值 |
| 状态所有权 | 哪个对象负责保存、修改和最终释放某份状态 | 不只指 Rust 语法 ownership，也包括架构上的责任归属 |
| actor | 独自拥有状态，通过消息处理请求的运行单元 | 需要从消息枚举、channel 和事件循环共同确认，不能看到 task 就称 actor |
| channel | 并发任务之间发送消息的通道 | 要记录发送者、接收者、容量和关闭后行为 |
| Tokio task | Tokio runtime 调度的轻量异步任务 | 不等于操作系统线程，也不等于用户要求完成的“任务” |
| cancellation | 请求正在运行的工作尽快停止 | 常通过 cancellation token、关闭 channel 或显式事件传播 |
| streaming event | 模型生成过程中逐步到达的事件 | 可能包含文本增量、工具调用、用量或结束原因 |
| tool call | 模型输出的结构化工具调用请求 | 还没有等同于宿主已成功执行副作用 |
| tool result | 工具执行后返回给 agent/模型的结构化结果 | 可能表达成功、失败、拒绝或取消 |
| schema | 描述输入字段和约束的机器可读结构 | 工具通常使用 JSON Schema |
| side effect / 副作用 | 对函数外部世界造成的变化 | 如修改文件、启动进程、写数据库或发网络请求 |
| snapshot | 某一时刻状态或输出的固定记录 | snapshot test 用它检查之后输出是否意外变化 |
| mock | 用可控制的假实现替代真实依赖 | 例如不用真实模型也能测试 session 的模型响应处理 |
| dependency injection / 依赖注入 | 从外部把实现交给使用者，而不是在内部写死创建方式 | 有利于替换真实和测试实现 |
| trait | Rust 中定义一组行为能力的接口 | 阅读 trait 后必须继续找 production implementation 和调用点 |
| production implementation | 产品真正运行时使用的实现 | 与 mock、test double 相对 |
| serde | Rust 常用的序列化/反序列化框架 | 项目用它处理 JSON、TOML 和协议类型等 |
| impact matrix / 影响矩阵 | 修改项与潜在受影响区域的对应表 | 用来检查协议、安全、持久化、UI 和测试是否遗漏 |

## 最终验收

完成系统学习路线后，应该能在不看资料的情况下回答：

1. 程序从 `main` 到可接收 prompt 经历哪些关键边界？
2. 一次 turn 和一次 sampling iteration 有什么区别？
3. 工具定义、权限判断和宿主副作用分别在哪里？
4. session 如何把 streaming 更新提供给 TUI 或协议客户端？
5. context 过长、用户取消、工具拒绝和网络失败分别如何收束？
6. 修改一个共享事件类型，可能影响哪些 crate 和测试？
