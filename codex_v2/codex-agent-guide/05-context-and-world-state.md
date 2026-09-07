# 05. Context、World State 与 AGENTS.md

## 1. Context 是模型可见事实，不只是聊天记录

Coding Agent 除了对话，还需要知道：

- 当前工作目录和 workspace roots；
- 操作系统、Shell、时间与时区；
- 文件系统和网络权限；
- 当前模型、personality 和 collaboration mode；
- AGENTS.md 中的仓库约束；
- Skills、Apps、Plugins 与多 Agent 可用性；
- 执行环境是否 ready。

这些内容统称为运行时 Context。Codex 用 World State 把其中“当前有效且可能变化”的部分组织起来。

## 2. `ContextualUserFragment`

各种上下文不是由一个巨大的条件函数直接拼接。它们实现统一的上下文片段接口，负责：

- 渲染模型可见文本；
-声明使用 developer 还是 user role；
-提供稳定 marker，便于识别和替换；
-转换成 `ResponseItem`。

具体类型位于 [`core/src/context`](../codex-rs/core/src/context) 和独立的 `context-fragments` crate。

常见片段包括：

- `UserInstructions`；
- `PermissionsInstructions`；
- `EnvironmentContext`；
- `ModelSwitchInstructions`；
- `MultiAgentModeInstructions`；
- `AppsInstructions` / `PluginInstructions`；
- `SkillInstructions`；
- current-time、token-budget 和 hook 提示。

## 3. World State 的职责

World State 不是所有 History 的副本。它是“一组当前事实的规范化快照”。Session 同时保存：

- 当前 World State snapshot；
-模型 History 中已经注入到哪一个 baseline；
-从 baseline 到新 snapshot 的 patch。

它支持两种渲染：

```text
render_full()   新线程、恢复后缺少基线、compaction 后重注入
render_diff()   正常 Step，只描述发生变化的部分
```

## 4. 为什么不每轮发完整状态

假设权限说明 3,000 字，AGENTS.md 8,000 字，Skill catalog 5,000 字。每次 Tool Call 后都重发完整状态会：

- 快速耗尽上下文窗口；
-破坏稳定 Prompt 前缀，降低 cache 命中；
-让模型误以为同一条指令被反复强调；
-使 rollback 和审计更难判断状态何时改变。

增量模型则是：首次完整注入，之后只追加变化；compact 开启新窗口时再安装新的完整 baseline。

## 5. AGENTS.md

AGENTS.md 是仓库或目录范围内给 Agent 的工作约束，例如代码风格、测试命令和目录规则。其关键语义是作用域：

- 上层 AGENTS.md 对其目录树生效；
- 更深层文件可以为子树提供更具体规则；
-直接的 system/developer/user 指令优先于仓库文件；
-修改某个文件时，需要遵守覆盖该文件路径的全部有效指令。

当前 Step 捕获时，Session 会刷新 `agents_md_manager`，并把 `loaded_agents_md` 放进 StepContext。对应 World State contributor 再把它渲染为 `UserInstructions`。

主要入口：

- [`context/world_state/agents_md.rs`](../codex-rs/core/src/context/world_state/agents_md.rs)
- [`session/mod.rs`](../codex-rs/core/src/session/mod.rs) 中的 Step 捕获逻辑

## 6. 环境上下文

Environment Context 需要同时服务模型理解和 Tool 执行：

-模型要知道 cwd、Shell、平台和可写根；
- Tool Runtime 必须使用同一份环境选择和 sandbox context；
-远程 Executor 可能拥有与 host 不同的操作系统和文件系统。

因此不要只向模型写一句“你在 Linux 上”，却让 Shell 工具在另一个环境执行。Codex 把环境选择放入 Turn/Step，并由同一 Step 构建 World State 和 ToolRouter。

## 7. 权限上下文

权限说明告诉模型哪些动作可能直接执行、哪些需要升级批准。Runtime 仍然会做真实检查；Prompt 中的权限文字只是帮助模型提前选择合理方案。

这是典型的“双层防护”：

```text
Prompt policy：减少模型提出违规动作
Runtime enforcement：即使模型提出也不能越权执行
```

只依靠 Prompt 不是安全边界。

## 8. 模式和 Personality

Plan/Default、collaboration mode、multi-agent mode、realtime mode 等会改变可用工具和行为规则。这类信息既影响 Prompt，也影响 Runtime：

- Plan mode 可以改变允许的交互工具；
- collaboration mode 注入 developer instructions；
- multi-agent mode 决定是否注册 spawn/message/wait；
-模型 personality 可能烘焙进 base instructions，也可能作为增量片段。

如果只改变 Prompt 不改变 Tool Registry，模型可能看到不能执行的能力；只改变 Registry 不改变 Prompt，模型则不知道新语义。因此两者应从同一配置快照派生。

## 9. World State 的持久化顺序

当状态变化时，Session 的顺序是：

1. 生成模型可见 diff items；
2. 把这些 items 记录进 History；
3. 更新 History 中的 World State baseline；
4. 把 WorldState patch 写入 rollout。

注释特别强调：patch 要在描述它的模型上下文已经存在后再持久化。恢复逻辑才能按相同顺序重建。

## 10. 上下文大小必须有硬上限

任何自动注入模型的来源都可能增长：

- AGENTS.md 可能非常长；
- Skill 数量可能达到数百；
- Tool 输出可能包含整个构建日志；
-环境或 Plugin metadata 也可能膨胀。

成熟实现必须为每种片段设置独立预算，并提供：

-截断标记；
-总目录预算；
-单项上限；
-警告和 telemetry；
-按需读取替代一次性注入。

Skill 和 Tool Search 正是“目录化、按需展开”的两个例子。

## 11. 自己实现时的状态模型

可以把 World State 简化为：

```rust
struct WorldState {
    cwd: PathBuf,
    permissions: PermissionSnapshot,
    repository_instructions_hash: String,
    enabled_capabilities: Vec<String>,
}
```

每次请求前生成 snapshot，与上次模型已知 snapshot 比较并追加 diff。关键不是字段多少，而是明确区分：

-真实当前状态；
-模型已经知道的状态；
-两者之间需要发送的变化。

