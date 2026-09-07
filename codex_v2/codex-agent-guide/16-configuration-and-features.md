# 16. 配置系统与 Feature Flag

## 1. 为什么配置是理解源码的前提

Codex 的许多代码路径不是“编译进去就一定执行”，而是由配置、模型能力、产品要求和 Feature Flag 共同决定。例如 `tool_search` 是否出现，可能同时依赖：

-Feature 是否启用；
-当前模型是否支持相应 Tool Mode；
-Registry 是否存在 Deferred Tool；
-Session source 是否允许；
-管理员 requirements 是否禁用。

因此看到 `if config.features.enabled(...)` 时，必须继续追踪配置从哪里来、是否被管理策略约束，以及最终在哪个生命周期被冻结。

## 2. 两个主要层次

配置系统大致分为：

```text
codex-config
  负责读取、标记来源、合并 TOML、requirements 和诊断

codex-core::config
  把通用配置解析成 Core 可直接执行的 Config、权限、网络和服务对象
```

Feature 定义和依赖关系主要位于独立的 [`codex-rs/features`](../codex-rs/features/src/lib.rs)。

## 3. Config Layer

每一层配置都保留来源，不只是一个匿名 TOML 值。`ConfigLayerSource` 位于 [`config/src/config_layer_source.rs`](../codex-rs/config/src/config_layer_source.rs)，来源包括：

-MDM；
-系统配置；
-Enterprise Managed；
-用户配置和 profile；
-项目 `.codex` 配置；
-Session/CLI flags；
-兼容旧版本的 managed config。

源码为每类来源定义确定的 precedence。`ConfigLayerStack` 按顺序保存 layer、requirements 和原始 requirement TOML，使系统不仅知道最终值，还知道值来自哪里。

## 4. 值合并与要求约束不是一回事

需要区分：

### Value Layer

回答“这个键最终取什么值”。后续高优先级 layer 可以覆盖普通低优先级值。

### Requirements

回答“允许取哪些值”。例如管理员可以要求某类权限不得被放宽。即使 CLI override 优先级更高，也不能绕过强制要求。

正确心智模型是：

```text
所有 value layers 合并
        +
requirements 对结果施加约束
        ↓
有效 Config 或明确诊断
```

## 5. 加载顺序

交互式启动时，TUI 先找到 Codex home 和 cwd，再调用 bootstrap config loader。Loader 大体执行：

1.确定用户配置文件和可选 profile；
2.发现项目配置根；
3.加载系统/企业/MDM layer；
4.加载用户和项目 TOML；
5.解析 CLI `-c`、`--enable`、`--disable` 等 Session flags；
6.合并 layers；
7.应用 key alias 和兼容迁移；
8.检查 requirements 和 strict-config；
9.输出带 diagnostics 的配置结果；
10.Core 再解析权限、HTTP、模型和环境等运行对象。

加载实现从 [`config/src/loader/mod.rs`](../codex-rs/config/src/loader/mod.rs) 开始，合并逻辑位于 [`config/src/merge.rs`](../codex-rs/config/src/merge.rs)。

## 6. 为什么要保留 Layer Stack

只保存最终 TOML 会丢失：

-某字段是谁设置的；
-为什么用户覆盖没有生效；
-某个 Hook 或 Permission 是来自项目还是管理员；
-reload 时应该替换哪一层；
-UI 如何解释 managed 状态。

Codex 会把 `config_layer_stack` 保存在 Core Config 中，Hook discovery、exec policy 和权限解析都可能读取来源信息。

## 7. Profile

Profile 是一组用户配置选择，不是独立进程。启动时 profile 会影响用户 config path/profile name，随后走同一 loader。

需要注意：

-profile 影响配置来源身份；
-CLI override 仍可作用于 profile 结果，但受 requirements 限制；
-远程 App Server 不一定能采用所有本地启动 override，所以 TUI 会判断是否可以复用 daemon。

## 8. Strict Config

普通模式可以把部分无效或未知配置报告为 warning 并采用 fallback；strict config 更倾向于启动失败。它适合 CI、企业部署和确定性测试。

严格模式的价值不是让 TOML 更“漂亮”，而是避免拼错键后 Agent 静默使用不安全或意外默认值。

## 9. `ConfigToml` 与运行时 `Config`

`ConfigToml` 是文件形态，字段大量使用 `Option` 表示“本层没有设置”。Core 运行时 `Config` 则应尽量是已解析的有效值，例如：

-有效 cwd 和 workspace roots；
-resolved permission profile；
-HTTP client factory；
-模型 provider；
-Feature set；
-Skill/MCP/Plugin 配置。

转换过程中还会结合平台、环境变量、managed requirements 和默认值。

## 10. Thread 配置与 Turn 覆盖

启动 Thread 时，Config 被转换为 `SessionConfiguration`。用户开始新 Turn 时，可以提交 `ThreadSettingsOverrides`：

-model；
-reasoning effort/summary；
-service tier；
-collaboration mode；
-personality；
-permission profile；
-environment selection。

`user_input_or_turn_inner()` 先调用 `new_turn_with_sub_id()` 应用更新，再得到稳定 `TurnContext`。因此：

```text
持久/当前 Thread 设置
  + 本次请求显式 override
  → TurnContext
```

正在运行的 Turn 不会因为另一个请求修改 Session 设置而被静默改变。

## 11. Feature 的数据模型

Feature 不应只理解为布尔值。一个 Feature 通常拥有：

-稳定 key；
-Stage；
-默认状态；
-可能的配置 payload；
-依赖或 implied feature；
-legacy key 映射。

Stage 常见语义：

-UnderDevelopment：开发中；
-Experimental：可试用但可能变化；
-Stable：正式支持；
-Deprecated：仍兼容但应迁移；
-Removed：不能再启用。

CLI 的 `codex features list/enable/disable` 使用这些 metadata，而不是硬编码另一套列表。

## 12. Feature 依赖

某些 Feature 会隐含启用另一个基础 Feature。例如 CodeModeOnly 需要 CodeMode 基础能力。`Features::enabled()` 返回的是解析依赖后的有效状态，而不一定等于 TOML 中某个单独布尔值。

测试可以在 [`features/src/tests.rs`](../codex-rs/features/src/tests.rs) 中看到 enable、disable、config payload 和依赖解析行为。

## 13. Feature 如何改变 Prompt

Feature 可能控制：

-是否注入 Skills 使用说明；
-是否注入 Apps/Plugins；
-是否提供 token budget 提示；
-是否提供 Multi-Agent mode；
-是否启用某个上下文 contributor。

改变 Prompt 的 Feature 需要特别关注 cache 和 History：动态变化通常应该通过 World State diff，而不是重写旧消息。

## 14. Feature 如何改变 Tools

[`tools/spec_plan.rs`](../codex-rs/core/src/tools/spec_plan.rs) 是观察 Feature 效果的好入口。它会根据 Feature 和模型能力决定：

-Shell 工具形态；
-Apply Patch；
-Plan/request user input；
-Tool Search；
-Code Mode；
-Web Search；
-Image Generation；
-Multi-Agent；
-Plugin 安装工具。

同一 Feature 还可能改变 exposure，而不是简单增加/删除 Runtime。

## 15. Feature 如何改变安全

安全相关能力不能只由普通用户 Feature Flag 放宽。最终权限仍受：

-managed requirements；
-permission profile；
-approval policy；
-sandbox implementation；
-exec policy；
-平台限制。

Feature 可以开启某条实现路径，但不能替代 Runtime enforcement。

## 16. Reload

`Op::ReloadUserConfig` 让 Session 重新加载用户层。Reload 后要考虑：

-哪些字段可安全热更新；
-哪些只影响下一 Turn；
-MCP 是否需要 refresh；
-World State 是否需要告诉模型；
-旧 TurnContext 是否保持不变；
-managed layer 是否仍然有效。

正确做法不是直接把 `Session.config` 指针换掉后让所有正在运行 future 读取新值。

## 17. 一个追踪例子：为什么 `tool_search` 没出现

排查顺序：

```text
1. 配置里 Tool Suggest/Search Feature 是否有效启用？
2. managed requirements 是否禁用？
3. 当前 ModelInfo 是否支持对应工具表示？
4. built_tools() 是否找到 discoverable candidates？
5. Registry 中是否存在 exposure=Deferred 且有 search_info 的工具？
6. finalize_tool_router() 是否注册 tool_search？
7. model_visible_specs 是否因 Code Mode/namespace 能力被过滤？
```

只看第 1 步通常不够。

## 18. 测试建议

-比较完整 layer stack 和最终 Config；
-覆盖每个来源的 precedence；
-验证 requirements 不能被高层值绕过；
-测试 profile 与 CLI override；
-测试 strict/lenient diagnostics；
-Feature dependency 使用完整对象比较；
-改变 Prompt/Tool 的配置用 Core 集成测试证明最终请求发生变化；
-配置 schema 变化后更新生成 schema。

## 19. 可复用设计原则

1. 配置值与配置来源一起保存。
2. 普通覆盖与强制约束分开。
3. 文件形态和运行时形态分开。
4. Feature metadata 只有一个权威来源。
5. 每次任务固定有效配置快照。
6. 动态 reload 在明确边界生效。
7. 测试最终行为，不只测试布尔值。

