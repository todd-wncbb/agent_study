# 15：配置系统与 Feature Flag

## 先从一个常见困惑开始

你在配置里打开了某个 Feature，但对应工具没有出现在模型请求中。这不一定是 bug，因为“配置里写了 true”和“当前 Step 最终拥有这项能力”之间还有多层判断。

以一个假想工具为例，它可能同时要求：

- Feature 已启用；
- 当前模型支持这种工具表示；
- 管理员要求没有禁止；
- Tool Registry 中确实注册了 runtime；
- 当前 exposure 允许直接展示给模型。

因此，Feature Flag 更像“允许进入某条代码路径的开关”，不是“保证功能一定出现”的按钮。

## 1. 为什么配置不能只是一个大对象

同一个配置键可能来自：

- 系统或企业管理配置；
- 用户的 `config.toml`；
- 项目级 `.codex` 配置；
- 选中的 profile；
- CLI 或当前 session overrides。

如果只保留最终值，就无法回答“这个值是谁设置的”“为什么用户覆盖没有生效”。`codex-config` 因此保留 `ConfigLayerStack`，让最终值与来源都可以被追踪。

可以把它想成透明纸叠图：每层都画了一部分配置，最后看到的是合并结果，但仍能掀开每张纸检查来源。

## 2. 普通覆盖与强制要求不是一回事

这两个概念最容易混淆：

| 概念 | 回答的问题 | 示例 |
|---|---|---|
| Value layer | 最终希望使用什么值？ | 用户选择某个模型 |
| Requirement | 哪些值根本不允许？ | 企业禁止放宽网络权限 |

高优先级的普通值可以覆盖低优先级普通值，但不能靠 CLI 参数绕过管理员 requirement。

```text
各层配置值合并
       +
requirements 检查和约束
       ↓
有效运行时 Config，或明确诊断
```

## 3. 文件配置与运行时配置

`ConfigToml` 是“用户可以写下什么”，很多字段使用 `Option`，因为某层没有设置也是有效状态。

`codex_core::config::Config` 是“程序已经解析并准备使用什么”。它会结合默认值、平台、环境变量、requirements 和 Feature 解析，得到 cwd、权限、模型 provider、工具能力等运行对象。

这类似表单与已审核订单：表单允许空字段；订单进入执行系统前必须补默认值、验证约束并解析成确定状态。

## 4. Profile、Thread 与 Turn

Profile 是一组可选择的用户配置，不是另一种 Session。

Thread 启动时获得一份有效配置。随后 `turn/start` 可以携带 model、reasoning、permission profile、environment selection 等覆盖，形成本轮稳定的 `TurnContext`：

```text
当前 Thread 设置 + 本轮显式覆盖 → TurnContext
```

正在运行的 Turn 不应因为另一个请求更新了 Session 设置，就在中途悄悄改变行为。新的设置应在明确的生命周期边界生效。

## 5. Feature 不只是布尔值

`codex-rs/features/src/lib.rs` 中的 `Feature` 和 `FeatureSpec` 还描述：

- 稳定的配置 key；
- 所处阶段，如开发中、实验、稳定、废弃或移除；
- 默认是否启用；
- 某些 Feature 的附加配置和依赖关系。

例如基础能力和“仅暴露某种能力”的模式可能有依赖关系。读取 `Features::enabled()` 时，看到的是解析后的有效状态，不一定等于 TOML 中某一个裸布尔值。

## 6. Feature 最终怎样改变系统

Feature 可能影响不同层：

- Prompt：是否注入某段说明或上下文；
- Tool plan：工具是否注册、直接展示或延迟发现；
- Runtime：选择哪种执行实现；
- UI：是否展示实验能力入口；
- Security：启用某条受控流程，但不能取消审批和沙箱。

观察工具变化时，`core/src/tools/spec_plan.rs` 比 Feature 定义本身更接近最终行为。

## 7. 配置重新加载

Reload 不等于把全局指针换掉，让所有异步任务立即读到新值。需要分别决定：

- 哪些设置立即更新；
- 哪些只影响下一个 Turn 或 Step；
- MCP 或工具目录是否需要刷新；
- 模型是否需要通过 world-state update 得知变化；
- 已经开始的 Turn 是否继续使用旧快照。

## 8. 排查“功能为什么没出现”

按从配置到最终请求的顺序检查：

1. 有效 layer stack 中 Feature 是否真的启用？
2. 是否被 requirements 或平台约束？
3. 当前模型是否支持？
4. Tool runtime 是否注册？
5. Tool exposure 是否允许模型直接看到？
6. 最终 outbound request 的 tools 中是否存在？

最后一步尤其重要：不要只证明某个布尔值为 true，要证明用户可观察行为真的改变了。

## 常见误解

- **“CLI override 优先级最高，所以能覆盖企业策略。”** 普通值优先级与强制 requirement 是两套机制。
- **“Feature 打开就一定多一个工具。”** 工具还受模型能力、注册和 exposure 决定。
- **“配置 reload 后，正在执行的 Step 应立即变化。”** 这会破坏 Step 一致性。

## 读完后自测

1. 为什么 Codex 要保留配置来源，而不只保存最终 TOML？
2. `ConfigToml` 与运行时 `Config` 为什么需要是两种类型？
3. 如何用 outbound model request 证明一个 Feature 真正生效？

## 本章词汇表

| 词语 | 直译 | 在配置系统中的意思 |
|---|---|---|
| Layer | 层 | 带来源和优先级的一组配置值 |
| Precedence | 优先次序 | 多层设置同一字段时，哪一层的普通值获胜 |
| Requirement | 强制要求 | 对最终允许值施加的约束，不是普通覆盖值 |
| Profile | 配置档 | 用户选择的一组命名配置，不是独立 Session |
| Feature flag | 功能开关 | 允许进入某条能力路径的配置，不保证最终可用 |
| Effective config | 有效配置 | 合并各层并应用要求后真正使用的结果 |
| Reload | 重新加载 | 重新读取可更新配置，并在明确边界应用变化 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

- `codex-rs/config/src/state.rs`：`ConfigLayerStack`；
- `codex-rs/config/src/loader/mod.rs` 与 `merge.rs`：加载和合并；
- `codex-rs/config/src/config_requirements.rs`：强制要求；
- `codex-rs/core/src/config/mod.rs`：运行时 `Config`；
- `codex-rs/features/src/lib.rs`：`Feature`、`FeatureSpec`；
- `codex-rs/core/src/tools/spec_plan.rs`：Feature 对模型可见工具的实际影响。
