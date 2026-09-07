# 源码精读 18：Agent Configuration Runtime 如何分层加载、解析、约束并传播配置

> 前几篇分别追踪 Prompt、Skill、Tool、Permission、Hook。本篇回到这些系统共同的上游：配置。重点不是罗列 TOML 字段，而是理解一个值怎样从文件、环境变量、CLI、Remote Settings 和管理策略中胜出，何时成为 Typed Config，何时被 Session 快照，以及哪些修改能热更新、哪些只能在下次 Rebuild 生效。

## 0. 本文回答什么

1. `$GROK_HOME/config.toml`、`managed_config.toml`、`requirements.toml` 和系统/MDM 层各自是什么；
2. `ConfigLayers` 按什么顺序 Deep Merge；
3. 为什么“Managed”不一定在基础 Merge 中覆盖 User；
4. Requirements 怎样在 Campaign 和运行时解析后继续保持最高约束；
5. `version_overrides` 怎样按 CLI SemVer 应用 Patch；
6. Campaign 为什么是可撤销的 Soft Overlay；
7. 环境变量、CLI、Config、Remote 和 Default 为什么没有一条适用于所有字段的统一顺序；
8. Raw TOML 怎样变成大而 Typed 的 `Config`；
9. Serde 字段与 `#[serde(skip)]` Runtime Field 有何区别；
10. Unknown Key、Malformed Model/Auth Provider 为什么采取不同错误策略；
11. `RuntimeResolutionContext` 怎样补齐 CLI、Remote 和运行模式；
12. Effective Config 怎样传播到 Prompt、Tool、Skill、MCP、Hook、Memory 和 Model；
13. Project Config 为什么只对部分能力生效；
14. File Watcher、Reloader、Typed Update 与 ACP Internal Request 怎样组成热更新链；
15. 为什么很多配置变化只影响下一次 Agent Build 或新 Session；
16. 怎样判断一个配置值的最终来源和生效时机。

源码基线：

```text
ed6d543
```

---

## 1. 总体配置流水线

```text
System Managed TOML
  + User Managed TOML
  + User Config TOML
  + User/System/MDM Requirements
        │
        ├── 每层先应用 Version Overrides
        ├── 提取 Campaign 定义
        ▼
ConfigLayers::effective_config_base
        │ Deep Merge
        ▼
Pre-campaign Raw TOML
        │
        ├── Resolve Campaigns
        ├── Apply Campaign Patches
        └── Reapply Requirements
        ▼
Effective Raw TOML
        │
        ├── Parse Model/Auth/MCP 特殊表
        ├── Merge Typed Defaults
        ├── Serde Deserialize
        └── Collect Warnings
        ▼
Config
        │
        ├── Env Overrides
        ├── Managed Settings Enforcement
        ├── Requirements Pins
        ├── Remote Settings
        ├── CLI Overrides
        └── RuntimeResolutionContext
        ▼
Resolved Runtime Config
        │
        ├── AgentRebuildSpec
        ├── ModelsManager / Permissions / MCP State
        ├── Prompt + Skills + Compat
        └── ToolBridge Resources
        ▼
Session Snapshot
```

核心结论：

> Grok 的配置不是一次 Merge，而是“文件分层 → 软 Overlay → Typed Parse → Policy Clamp → 运行时解析 → Session 快照”的多阶段系统。

---

## 2. 核心源码地图

### 2.1 通用配置加载

```text
crates/codegen/xai-grok-config/src/
  loader.rs
  validation.rs
  version_overrides.rs
  config_override.rs
  campaigns.rs
  paths.rs
  macos_managed.rs
  global_hook_sources.rs
```

### 2.2 Shell Typed Config 与解析

```text
crates/codegen/xai-grok-shell/src/
  agent/config.rs
  agent/init.rs
  config/mod.rs
  util/config/load.rs
  util/config/campaigns.rs
  util/config/resolve/*.rs
```

### 2.3 运行时传播与热更新

```text
crates/codegen/xai-grok-shell/src/
  config/watcher.rs
  config/reloader.rs
  agent/app.rs
  session/agent_rebuild.rs
  session/acp_session_impl/hooks_plugins.rs
  session/mcp_restart.rs
```

---

## 3. 首先区分五种“配置”

### Raw Layer

某一个文件解析出的 `toml::Value`。

### Effective Raw Config

多个 Layer 和 Campaign 合并后的 `toml::Value`。

### Typed Config

Effective TOML 反序列化得到的 `agent::config::Config`。

### Resolved Runtime Config

Typed Config 再结合 Env、CLI、Remote、Mode 和 Requirements 得到的实际运行值。

### Session Snapshot

Session 创建时从 Resolved Config 提取并固化到 Agent、Sampler、ToolBridge、Hook Registry 等对象的值。

如果不区分这五层，就会误以为“文件里改了值，所有现有 Session 都应立即变化”。

---

## 4. 主要文件层

| 层 | 典型位置 | 角色 |
| --- | --- | --- |
| System Managed | `/etc/grok/managed_config.toml` 等 | 系统级默认/管理配置 |
| Managed | `$GROK_HOME/managed_config.toml` | 服务同步的管理配置 |
| User | `$GROK_HOME/config.toml` | 用户显式偏好 |
| User Requirements | `$GROK_HOME/requirements.toml` | 云缓存或用户层约束 |
| System Requirements | 系统目录 `requirements.toml` | Root/Admin 约束 |
| MDM Requirements | macOS Managed Preferences | OS 保护的最高约束 |

项目 `.grok/config.toml` 不直接加入这个全局 `ConfigLayers`。它由 MCP、Plugin 等 CWD-aware Resolver 单独读取。

---

## 5. 没有 User Home 时为什么返回空表

`load_user_config_layer` 在无法解析 User Grok Home 时返回空 TOML Table，而不是使用 CWD-relative `.grok/config.toml`。

安全原因：

```text
无法找到 HOME
  ≠ 可以把当前仓库 .grok/config.toml 提升为 User Tier
```

否则仅仅进入陌生项目目录，就可能让项目配置获得全局用户配置的 Authority。

同样规则用于 User Requirements。

---

## 6. 每个 Layer 先独立处理 Version Override

`load_config_file` 做两件事：

1. 解析 TOML；
2. 对该 Layer 应用 `[[version_overrides]]`。

Version Patch 不是所有文件合并后统一执行，而是每个来源先形成“适用于当前 CLI 版本的 Layer”，再参与跨层 Merge。

这样一个 Layer 的版本兼容逻辑不会读写其他 Layer 的内容。

---

## 7. `version_overrides` 的结构

```toml
[[version_overrides]]
minimum_version = "1.8.0"
maximum_version = "1.9.999"

[version_overrides.features]
telemetry = false
```

边界是 Inclusive：

```text
minimum_version <= installed_version <= maximum_version
```

缺失 Minimum 表示 `0.0.0`，缺失 Maximum 表示无上界。

---

## 8. Version Patch 的确定性顺序

实现先解析所有 Bound，再按 `minimum_version` 稳定排序。

```text
较低 Minimum 先应用
较高 Minimum 后应用
相同 Minimum 保持声明顺序
后应用的 Leaf 胜出
```

先完整验证 Bound，再开始 Merge，避免执行到一半才遇到非法 SemVer，留下半应用状态。

无论是否命中，`version_overrides` Key 最终都会从 Effective Layer 移除。

---

## 9. Patch 不能递归注入控制面

统一 `apply_patches` 会从 Patch 顶层剥离：

```text
version_overrides
campaigns
auth_provider
model_providers
```

前两项避免 Patch 再定义 Patch；后两项避免软 Overlay 注入可执行 Credential Helper 或 Provider Command Table。

Model 仍可引用已存在的 Provider 名，但不能通过 Campaign/Version Patch 新造 Provider 执行定义。

---

## 10. `ConfigLayers` 保存的是未合并事实

```rust
ConfigLayers {
    system_managed,
    managed,
    user,
    user_requirements,
    system_requirements,
    mdm_requirements,
    campaigns,
}
```

它保留每层原值，使后续可以：

- 计算 Effective Config；
- 追踪 Origin；
- 单独处理 Campaign；
- 在 Campaign 后重新应用 Requirements；
- 给安全决策识别 System Layer。

若一开始只生成一个合并表，这些 Provenance 信息将不可恢复。

---

## 11. 基础 Merge 的真实优先级

`effective_config_base` 依次 Deep Merge：

```text
system_managed
  < managed
  < user
  < user_requirements
  < system_requirements
  < mdm_requirements
```

右侧覆盖左侧同一 Leaf。

一个重要事实是：

> 在普通基础配置模型里，User Config 可以覆盖 Managed Config；真正不可绕过的约束由 Requirements/MDM 和后续 Clamp 表达。

不要仅凭“managed”这个名字推断它一定比 User 高。

---

## 12. Deep Merge 的规则

```text
Table + Table -> 递归逐 Key Merge
其他组合       -> Override 整体替换 Base
```

因此：

- 两个 Table 的不同字段可以共存；
- Array 默认整体替换，不逐项追加；
- Scalar 覆盖 Table 会抹掉整棵子树；
- Table 覆盖 Scalar 会把 Scalar 替换成 Table。

这也是 `patch_touches_path` 必须把“非 Table 祖先”视为触碰所有子 Leaf 的原因。

---

## 13. Managed Layer 的加载采用隔离失败

`managed_config_layers_at` 分别读取 System 与 User Managed File：

- 文件缺失：跳过；
- 单层解析失败：警告并跳过；
- 另一层仍可加载。

这与主 `ConfigLayers::load` 的严格路径略有不同。前者用于需要逐层 Provenance 的场景，强调一层故障不丢其他层。

---

## 14. Requirements 是约束，不只是高优先级默认值

Requirements 同时存在于两条路径：

### Structural Merge

进入 Effective Raw TOML，使 Typed Parse 一开始就看到约束值。

### Typed Enforcement

`apply_requirements` 再把关键字段写入 `Config.requirements: Constrained<T>` 并 Clamp Runtime Field。

双路径的意义是：

- 普通 Consumer 读取 Effective TOML 也能看到正确值；
- Runtime Resolver 不会被后续 Env/Remote 再次绕过；
- Diagnostics 能显示 Pin 的来源。

---

## 15. Requirements 层顺序

`requirements_layers()` 返回 Apply Order：

```text
User Requirements
  < System Requirements
  < macOS MDM Requirements
```

MDM 最后且 `is_system=true`。

安全判断应信任 Loader 给出的 `is_system`，不要从可能受 `GROK_HOME` 影响的 Path 字符串重新猜测 Authority。

---

## 16. Requirements 的 Fail-closed 只针对明确范围

普通 Loader 对无法读取或无效的 Requirements Layer Soft-fail，记录错误并跳过。

但若 Layer 设置：

```toml
fail_closed = true
```

并且 `version_overrides` 对当前版本无效，Startup `validate_requirements()` 返回错误，可阻止继续启动。

环境变量只能把 Fail-closed 收紧为 True，不能把文件中的 True 放松为 False。

这不是“所有配置错误都 Fail Closed”，而是 Admin 显式选择的特定验证边界。

---

## 17. `Constrained<T>` 保存 Value 与 Source

```rust
Constrained<T> {
    pin: Option<T>,
    source: Option<RequirementSource>,
}
```

Resolver 可以写成：

```text
Requirement Pin exists?
  yes -> use pin
  no  -> continue normal precedence
```

Source 允许 UI/日志说明：

```text
features.web_fetch = false
from /etc/grok/requirements.toml
```

它比只覆盖最终 Boolean 更可解释。

---

## 18. External Managed Settings 是另一种 Enforcement 来源

`apply_managed_settings_features` 读取外部 `managed-settings.json`，当前可强制关闭 Telemetry、Feedback 等能力。

顺序是：

```text
apply_managed_settings_features
then apply_requirements
```

所以 Requirements 冲突时再次胜出。

这条路径与 `managed_config.toml` 的 Deep Merge 不同：它是 Typed Config 上的显式 Clamp。

---

## 19. Campaign 是 Soft Overlay

Campaign 可来自：

- Requirements Layer；
- Remote Settings；
- User Config；
- Managed Config；
- System Managed Config；
- `GROK_CAMPAIGNS_OVERRIDE`。

它是“推荐、实验或迁移”配置，而不是管理员不可绕过约束。

典型例子是临时改变 `models.default`，用户显式选择后可以 Dismiss。

---

## 20. Campaign 定义先从 Layer 中取走

`ConfigLayers::load` 对每层调用 `take_campaign_entries`。

结果：

- Campaign Metadata 不会作为普通 Config 字段参与 Base Merge；
- Base 代表没有 Soft Overlay 的真实配置；
- Campaign 在单独阶段解析优先级、Dismiss 和 Kill Switch。

这使系统能比较 Pre-campaign 与 Effective Value，判断某字段是否真的由 Campaign 驱动。

---

## 21. Campaign 来源优先级

同 ID 的 Campaign 采用 First-ID-wins：

```text
Requirements
  > Remote
  > User
  > Managed
  > System Managed
```

这是 Campaign Definition 的选择优先级，不是 Config Leaf 的最终 Authority。

选中的 Campaign Patch 应用后，Requirements 仍会重新 Merge，保证 Admin Pin 不被软 Patch 覆盖。

---

## 22. Campaign Resolution 顺序

```text
GROK_CAMPAIGNS_OVERRIDE present?
  yes -> 完全替换所有来源
  no  -> 检查 Kill Switch
          -> Merge Layer + Remote Campaigns

然后：过滤已 Dismiss ID
最后：按顺序应用 Patch
再最后：Reapply Requirements
```

空 Override 数组表示明确关闭全部 Campaign。

无效 Override JSON 也解析成“无 Campaign”，而不是回退真实来源；因为变量的语义是完全替换，拼写错误不应静默重新启用它本想屏蔽的 Campaign。

---

## 23. Requirements 为什么必须在 Campaign 后重放

Campaign 是 Full-power TOML Patch，理论上能触碰任意字段。

若只依赖“Requirements 已在 Base 中合并”，Campaign 后应用就能覆盖它。

因此：

```text
Base includes Requirements
  -> Campaign Patch
  -> Reapply Requirements
```

这是 Authority 的结构性保证，不依赖每个 Campaign 作者自觉避开敏感字段。

---

## 24. Dismiss 状态如何持久化

`campaigns_state.json` 保存已 Dismiss ID，最多 32 个，FIFO 淘汰最旧项。

写入具备：

- 进程内 Mutex；
- 跨进程 Advisory File Lock；
- Read-modify-write；
- Temp File + Rename；
- Corrupt 文件重命名为 `.corrupt`；
- 多进程共享 `$GROK_HOME` 时降低 Lost Update。

用户持久化选择时先写 Dismiss，再写 Config。若中途崩溃，结果偏向“不再 Nudge”，而不是重新覆盖用户选择。

---

## 25. Disk-only 与 Remote-aware Effective Config

两个名字刻意区分：

```text
load_effective_config_disk_only
load_effective_config
```

前者：

- Disk Layers；
- Disk Campaign；
- Disk Dismiss；
- 无 Remote Cache；
- 无 Campaign Override Env。

后者：

- 额外使用进程内 Remote Campaign Cache；
- 尊重 `GROK_CAMPAIGNS_OVERRIDE`。

一次性 CLI 若从未 Seed Remote Cache，应显式使用 Disk-only，避免名字不清导致“以为读了 Remote，实际 Cache 永远为空”。

---

## 26. Remote Campaign Cache 为什么 `None` 不清空

```text
Remote Settings = None
  -> 可能是 Fetch 失败
  -> 保留旧 Cache

Remote Settings = Some(empty)
  -> 服务明确撤回 Campaign
  -> 清空 Cache
```

这区分“没有新事实”和“新事实是空集合”。

同样模式在其他配置刷新系统中也很重要。

---

## 27. Effective TOML 还不是最终 Runtime Config

`load_effective_config()` 返回 `toml::Value`。

之后才有：

```text
Config::new_from_toml_cfg
apply_env_overrides
apply_managed_settings_features
apply_requirements
ensure_remote_settings_side_effects
sync_campaign_fields
resolve_runtime_fields
```

所以在 Effective TOML 中看到某值，并不保证运行时没有被 CLI、Env、Remote 或 Requirement Pin 再解析。

---

## 28. Typed Config 为什么先从 Default 序列化

`new_from_toml_cfg` 大致执行：

```text
Config::default()
  -> serialize to TOML
  -> remove special sections
  -> deep merge raw config
  -> deserialize Config
```

这样 Nested Default 能统一参与 Serde，而不要求每个字段都在手写 Loader 中逐项赋值。

但特殊表仍被提前拿出，避免通用 Serde 对复杂兼容结构误判。

---

## 29. 为什么 Model、Auth Provider、MCP 要特殊解析

这些表需要比普通 Serde 更细的行为：

- 单条 Malformed 不应丢整份 Config；
- Warning 必须指向具体 Provider/Model；
- 引用不存在的 Provider 要 Fail Closed；
- MCP Transport 有多种兼容 Shape；
- Model Entry 需要 Legacy/Custom Provider 归一化。

因此它们先从 Raw TOML 解析到专用集合，再从通用 Merge 文档中移除，最后挂回 Typed Config。

---

## 30. `auth` 是 `grok_com_config` 的别名

`expand_auth_alias` 把 `[auth]` 搬到 `[grok_com_config]`。

若两者同时存在：

```text
显式 grok_com_config 子字段胜出
auth 只补缺失字段
```

Alias 在 Typed Deserialize 前展开，后续代码只需处理一个 Canonical Section。

---

## 31. Unknown Key 的错误策略

使用 `serde_ignored` 收集未识别路径，但只把来自 User Config 顶层的未知键归责给用户。

原因：序列化 Default 生成的内部字段或其他来源不应误报成用户拼写错误。

Unknown Key 进入 `config_warnings`，通常不让整个 Config 加载失败。

少数由 Raw Resolver 消费、并非 Config Serde 字段的路径放入 `NON_SERDE_CONFIG_PATHS`，避免假阳性。

---

## 32. `#[serde(skip)]` 字段代表什么

大 `Config` 中许多字段不直接来自 TOML，例如：

- CLI Override；
- Remote Settings；
- Resolved Memory Config；
- Resolved Compat Config；
- Storage Mode；
- Subagent Effective Toggle；
- Requirement Pins；
- Campaign-driven 状态；
- Runtime Model Selection。

`#[serde(skip)]` 表示它们需要专用 Resolver，不能期待 Deserialize 自动给出最终值。

---

## 33. `Config::default()` 也会读取部分环境变量

Default 不完全是纯常量。它会构造 Endpoint/Auth 等默认配置，并在最后调用 `apply_env_overrides()`。

`new_from_toml_cfg` 完成 Deserialize 后又调用一次 Env Override，确保 Raw Config Merge 没有覆盖应当由环境变量主导的字段。

因此测试若依赖进程环境，必须隔离 Env，不能把 `Config::default()` 当作纯函数。

---

## 34. 不存在一条全局统一的来源顺序

常见 Boolean 使用：

```text
Requirement > CLI > Env > Config > Managed > Remote > Default
```

String Flag 常见：

```text
CLI > Env > Config > Remote
```

Compat Cell：

```text
Env > [compat] TOML > Remote > Default ON
```

Agent Selection：

```text
ACP Meta > CLI Profile > Config > GROK_AGENT > Default
```

具体字段必须阅读自己的 Resolver。文档中声称“所有配置都是 Env > Config”通常不准确。

---

## 35. `Resolved<T>` 让来源成为数据

```rust
Resolved<T> {
    value: T,
    source: ConfigSource,
}
```

`ConfigSource` 包含：

```text
Requirement
Cli
Env
SystemManagedConfig
ManagedConfig
UserConfig
Config
Remote
Default
```

Resolver 若返回裸 Boolean，会丢掉“为什么是这个值”；返回 `Resolved<T>` 可供 Inspect、日志和 UI 解释。

---

## 36. `BoolFlag` 是可复用的优先级构建器

调用者显式提供每一层：

```rust
BoolFlag::env("GROK_AUTO_WAKE")
    .config(config_value)
    .feature_flag(remote_value)
    .default(true)
    .resolve()
```

未设置的层保持 `None`，不是 False。

这是配置系统的重要原则：

> “没有意见”必须与“明确关闭”区分，否则低层 Default 无法正确参与决策。

---

## 37. Section Presence 有时也是信号

`resolve_enabled` 对 Memory/Subagents 等 Section-based Config 使用：

```text
有本地 Section -> Config enabled 值参与
无本地 Section -> Remote Feature Flag 才能作为 Fallback
```

即使 Section 里没有显式 `enabled`，它的存在也可能表示用户选择进入本地配置模式。

因此不能只抽取一个 Boolean 而忽略“Section 是否存在”。

---

## 38. `RuntimeResolutionContext` 收集非 TOML 输入

它携带：

- Raw Effective Config；
- Remote Settings；
- 是否 Headless；
- CLI Subagents；
- CLI Web Search/Session Summary Model；
- Memory CLI Flags；
- Disable Web Search；
- Todo Gate；
- Laziness Debug Log；
- Storage Mode Override。

`resolve_runtime_fields` 使用它填充所有 Eager Resolved、`serde(skip)` 字段。

这样多个 Binary/启动入口可以复用同一 Typed Resolution，而不把 CLI Parser 耦合进 Config 类型。

---

## 39. Runtime Resolution 的主要输出

一次调用会解析：

- Subagent Enable、Depth、Model Override、Roles、Personas；
- `respect_gitignore`；
- ZDR 不兼容 Tool；
- Managed MCP 与 Gateway Tool；
- Web Search、Summary、Image Description、Prompt Suggest Model；
- Memory；
- Storage Mode；
- Path-not-found Hint；
- Auto Wake；
- Vendor Compat。

这一步是 Raw/TOML 世界与运行对象世界之间的主要边界。

---

## 40. Requirement Pin 必须在 Resolver 内再次检查

例如 `respect_gitignore`：

```text
if requirements.respect_gitignore.pinned()
  -> use pin
else
  -> use ToolsConfig::resolve(raw_config)
```

仅在早期覆盖 `config.features` 不够，因为后续 Resolver 可能重新从 Env/Raw/Remote 计算。

`Constrained<T>` 的存在就是为了让下游 Resolver 显式尊重 Admin Clamp。

---

## 41. Remote Settings 不是配置文件 Merge Layer

Remote Settings 通常不会整棵 Deep Merge 到 TOML。

它被单个 Resolver 作为一个候选来源：

```text
Config local value present?
  yes -> local wins
  no  -> remote feature flag
  no  -> default
```

优点：

- Remote 不能意外注入任意未知 Config；
- 每个字段能选择不同 Authority；
- Kill Switch、Rollout 与 Default 可单独建模。

Campaign 是 Remote 能提供 Full-power Patch 的独立通道，但 Requirements 会重放。

---

## 42. Remote Settings 的刷新语义

`refresh_settings_and_reapply`：

1. 刷新 Remote Settings；
2. Seed Campaign Cache；
3. 同步 Campaign-driven Fields；
4. 重新加载 Effective TOML；
5. `re_resolve_runtime_fields`；
6. 更新 Collection Gate、Announcements 等。

注释明确说明：

> In-flight Sessions 不受影响；它们在创建时快照配置。

新 `/new` Session 会看到刷新后的值。

---

## 43. Campaign Runtime State 为什么单独同步

对于 `models.default`，Config 不只保存最终值，还保存：

```text
default_is_campaign_driven
pre_campaign_default
```

这让 Model Catalog 缺少 Campaign 指定模型时可以回退 Pre-campaign Value，也让用户显式选择后正确解除 Nudge。

`sync_campaign_fields` 最后再次调用 `apply_requirements`，防止 Runtime 同步过程松开 Admin Pin。

---

## 44. Config 初始化的关键顺序

`agent/init.rs::resolve_config` 的核心顺序：

```text
clone parsed Config
  -> report Managed Origins
  -> apply external Managed Settings
  -> apply Requirements
  -> fetch/apply Remote Settings side effects
  -> sync Campaign fields
  -> resolve Storage/Auth gates
  -> continue building Agent Runtime
```

这里再次体现：Authority 不是靠一次 TOML Merge 完成，而是靠多个专用阶段共同保证。

---

## 45. `config_origins` 的能力与限制

它遍历 System Managed、Managed、User Layer，为每个 Dotted Leaf 记录最后来源。

用途：

- Inspect；
- 日志说明 Managed Fields；
- UI Provenance。

限制：

- 当前只覆盖三类普通配置层；
- Requirements、MDM、Campaign、Env、CLI、Remote 需由各自 Resolver/EnforcedField 解释；
- Array/整体替换的细粒度来源不一定能逐元素表示。

Origin Map 不是完整的全系统决策追踪器。

---

## 46. Config 怎样进入 Prompt 与 Agent Definition

配置会决定：

- Agent Definition 选择；
- System Prompt Label；
- Prompt Audience；
- Role/Persona Instructions；
- Compat 下是否加载 Claude/Cursor Rules 与 AGENTS；
- Context Window；
- Reminder 与 Compaction Policy。

这些值在 Session Spawn 时被整理进 `AgentRebuildSpec`，再调用 `AgentBuilder`。

Prompt 本身不在每次采样时重新读取 `config.toml`。

---

## 47. `AgentRebuildSpec` 是配置传播的编译产物

它保存构建 Session-bound Agent 所需的全部输入：

```text
Working Directory
Backends and Channels
Models Manager
Policies
Memory
Web/Image/Video Config
Tool Feature Gates
Skills + Compat
Plugins
Tool Params
Subagent Runtime
Context Window
System Prompt Label
ToolBridge Resources
```

源码保证 Shell 内只有这里调用 `AgentBuilder::new`。

新增 `AgentBuilder::with_*` 参数时必须同步扩展 Spec；完整 Destructure 配合 `deny(unused_variables)` 让遗漏尽量成为编译错误。

---

## 48. 为什么 Agent 不能跨 Session 共享

Agent 持有 Session-scoped `ToolBridge`：

- Notification Channel；
- Terminal/FS Backend；
- Subagent Sender；
- Scheduler；
- Plugin Registry；
- Attribution Callback；
- Session ID 与 Process State。

所以 Config 不能只生成一个全局 Agent Singleton。

每个 Session 必须用当时的配置和自己的 Runtime Resource 构建 Agent。

---

## 49. 配置怎样进入 Skill

`AgentRebuildSpec` 保存：

```text
skills_config
compat
working_directory
plugin_registry
```

`AgentBuilder` 用它们完成 Skill/Rule/AGENTS Discovery。

Subagent 可接收 Parent Preloaded Skills，绕开重新扫描；Resume 可传 `persisted_skill_names`，避免重复注入 Skill Announcement。

这说明 Skill Config 的真正生效点是 Agent Build，而不只是 File Watcher 发出 Change Event。

---

## 50. 配置怎样进入 Tool

传播方式有三类：

### Toolset Construction

Feature Gate 决定 Tool 是否注册，如 Web Fetch、Write File、Image/Video、Subagent。

### Tool Params

`ResolvedToolParamsJson` 把 `[toolset.bash]`、Ask User Timeout 等 JSON Map 传给 Builder。

### ToolBridge Resource

`RespectGitignore`、`PathNotFoundHints`、Scheduler Mode、Managed Gateway Client 等作为 Typed Resource 注入。

Tool Implementation 不需要自己反复读取全局 TOML。

---

## 51. 配置怎样进入 MCP

MCP 有多层来源：

- Global `[mcp_servers]`；
- Ancestor Project `.grok/config.toml`；
- `.mcp.json`；
- Claude-compatible Config；
- Managed MCP Service；
- Plugin MCP；
- Client/Proxy Runtime。

因为 Project Source 与 CWD 相关，MCP 不能只使用全局 `ConfigLayers`。

热更新也区分：

- Global Change：广播所有 Session；
- Project Change：只重载 CWD 等于或位于该项目下的 Session。

---

## 52. 配置怎样进入 Hook

Hook 是特殊路径：`hook_config_layers()` 不先合并各层，而是按最高 Authority First 返回每层 Raw `hooks` Subtree。

```text
requirements/system
requirements/user
user
managed
system_managed
```

原因：Hook 是 Additive Collection，不是单值覆盖。

后续 Hook Runtime 对内容去重，First-wins 保留更高 Authority 的同一 Hook。

Hook Command 的 `${VAR}` 也不会在通用 Config Loader 中提前展开，避免 Double Expansion。

---

## 53. 配置怎样进入 Plugin

Plugin Effective Config 使用：

1. Global Effective `[plugins]`；
2. 祖先 Project `.grok/config.toml`；
3. Folder Trust；
4. Claude User `enabledPlugins` 兼容合并。

Project Trusted 时才扩展 Project Plugin Path；Project Disabled List 即使不可信也可继续收紧。

不读取 Project Claude `enabledPlugins` 来自动启用 Plugin，避免恶意仓库启用带 SessionStart Hook 的攻击者代码。

---

## 54. Project Config 不是全局 Config 的普通高优先级层

这是常见误解。

Project `.grok/config.toml` 主要由 CWD-aware Consumer 读取，例如：

- Project MCP；
- Plugin Paths/Disabled；
- 其他显式 Project Resolver。

它不会自动覆盖所有全局 Agent Config 字段。

设计好处是：打开仓库不会让它任意改变 Telemetry、Auth、全局模型等用户级设置。

---

## 55. Environment Expansion 与 Environment Override 不同

### Expansion

字符串值中的 `$VAR`/`${VAR}` 被替换，例如 URL 或 Path。

### Override

专用变量直接作为配置来源，例如 `GROK_AUTO_WAKE`。

前者改变一个已有字符串，后者参与来源优先级决策。

Hook Config 故意延迟 Expansion；普通 Config/MCP 等可能在自己的消费路径中展开。不能假设所有 TOML String 在同一个阶段统一展开。

---

## 56. Config 写入为什么不能简单覆盖整文件

用户设置更新通常是：

```text
Read current TOML
  -> mutate specific section/path
  -> preserve unrelated keys
  -> lock / atomic write
  -> watcher notices change
  -> content diff suppresses self-write noise
```

某些字段还有专用 Writer，例如 Model Default 必须同时 Dismiss 相关 Campaign。

绕过专用 Writer 直接写 Leaf，可能导致 Campaign 继续把用户选择覆盖回来。

---

## 57. File Watcher 的职责很窄

Watcher 只把文件系统事件归一为：

```text
AuthChanged
GlobalConfigChanged
ModelsCacheChanged
ProjectConfigChanged { path }
McpConfigChanged { path }
HomeClaudeJsonChanged
```

它不解析完整 Config，不直接修改 Agent。

这种分层避免 OS Watch Callback 承担复杂业务和异步 Session 操作。

---

## 58. 为什么丢弃 Access Event

Linux Inotify 读取被监控文件也可能产生 Access Event。

若 Reload 自己重新读文件又触发下一次 Reload，就会形成约每秒一次的自维持风暴。

`AccessFilteredWatcher` 丢弃 Access，只保留 Write/Create/Metadata 等实际变化信号。

这是典型的“观察系统自身行为产生反馈环”问题。

---

## 59. Debounce 与 Batch

Watcher 默认 1 秒 Debounce，处理编辑器常见的：

```text
write temp
rename
chmod
multiple modify events
```

Reloader 收到第一条后再 `try_recv` Drain 同 Tick 事件，形成 Batch。

Batch 内：

- 相同 Event 去重；
- Project CWD 保持顺序去重；
- Global 与 Project Update 可以同时产生。

---

## 60. 为什么 Project Watch 非递归

每个 CWD 只 Watch：

```text
<cwd>/
<cwd>/.grok/
```

递归 Watch 整仓库会遍历：

```text
node_modules
target
.git
vendor trees
```

不仅昂贵，还可能耗尽 `fs.inotify.max_user_watches`。

Session 打开新 CWD 时动态注册，关闭后可 Unwatch，控制长期积累。

---

## 61. Self-write 不用时间窗口抑制

进程自己写 `config.toml` 也允许触发 Watcher。

Reloader 重新读取后用内容比较、Auth Key Hash、Project Config Hash 等去重。

优点是不会因“刚好在 Self-write 抑制窗口内”吞掉另一个进程的真实写入。

多读一次文件的成本小于错过外部更新的风险。

---

## 62. Reloader 使用 Last-known-good

Global Config 解析失败时：

```text
记录错误
保留 last_global_config
不发送破坏性 Update
```

Auth 文件不可读也保留旧 Credential；只有文件可读且目标 Scope 明确不存在时才发送 `AuthCleared`。

这区分：

- 新事实：用户确实删除 Scope；
- 无法获得事实：文件损坏或临时不可读。

---

## 63. `ConfigUpdate` 是 Send-safe 差异消息

主要 Variant：

- `Auth/AuthCleared`；
- Global/Project MCP Changed；
- Models/Models Cache Changed；
- Memory；
- Skills；
- Compat；
- UI。

Reloader 在线程安全的 Tokio Task 中运行，只发送 Typed、`Send` 安全消息。

真正操作 `Rc/RefCell` Session 状态的逻辑回到 Agent `LocalSet` 执行。

---

## 64. 为什么不发送整个新 Config

不同 Subsystem 的更新成本和安全边界不同：

- Auth 可以 Hot Swap；
- MCP 需要 Restart/Reload；
- Model Catalog 要串行重建；
- UI 可直接广播；
- Compat 需下次 Agent Rebuild；
- Memory/Skills 当前 Watcher 路径主要记录变化，完整应用依赖后续 Build/专用 Reload。

发送差异事件迫使每个 Consumer 明确自己的 Apply Semantics，避免“一把替换 Config”造成半更新对象图。

---

## 65. ACP Internal Request 是串行化屏障

Model/MCP Reload 并不直接从 Config Update Task 修改 Session，而是向 Agent 的 ACP 输入流注入 Internal Method。

好处：

- 与普通 Session Request 排序；
- 避免 Model Config Reload 与 Models Cache Reload 交错；
- 复用现有 Session 遍历和错误处理；
- 不在 Watcher Task 中跨越 Local Runtime 所有权。

这是 Actor/Event-loop 系统中常见的“把外部变化重新注入权威命令流”模式。

---

## 66. Global MCP 与 Project MCP 不能合并为一个事件

Global `[mcp_servers]` 或 Home `~/.claude.json` 影响所有 Session。

Project `.grok/config.toml`、`.mcp.json`、Project `.claude.json` 只影响匹配 CWD。

如果把 Home `.claude.json` 错当作 `$HOME` Project Event，位于 `$HOME` 之外的 Session 会被过滤掉。

因此 Watcher 甚至会 Canonicalize Home，抵御 macOS FSEvents 返回 `/private/var/...` 等路径形式差异。

---

## 67. Project MCP Reload 的内容去重

Watcher Event 可能只是 Mtime 变化。

Reloader 对每个 Project CWD 维护 Config Hash：

```text
hash unchanged -> skip
hash changed   -> emit scoped update
hash unknown   -> conservative dispatch
```

不确定时选择 Reload，确定相同才跳过。

---

## 68. 热更新支持矩阵

| 子系统 | 当前变化后的主要行为 |
| --- | --- |
| Auth | In-memory Hot Swap/Clear，刷新 Model/MCP |
| Global MCP | 重载所有活跃 Session |
| Project MCP | 重载匹配 CWD Session |
| Models Config | 重建 Model List |
| Models Cache | 串行重新载入 Catalog |
| UI | 广播 `x.ai/config_changed` |
| Memory | 发现并记录变化 |
| Skills | 发现并记录；另有 Skill 文件 Watch/Reload 路径 |
| Compat | 下次 Agent Rebuild 生效 |
| Hook Config | 显式 `/hooks reload` 或新 Session |
| 多数 AgentBuilder 字段 | 新 Session/Zero-turn Rebuild |

这张表比“支持热更新”这个笼统说法更接近源码事实。

---

## 69. 为什么不能让所有字段都即时热更新

一个 Config 值可能已经被编译成：

- Prompt Text；
- Tool Registry；
- Skill Catalog；
- Sampler Client；
- Process Backend；
- Permission Engine；
- Hook Registry；
- Typed ToolBridge Resource。

只替换 `Config` Struct 不会自动重建这些派生对象。

要安全热更新，必须定义：

1. 哪些对象需要重建；
2. In-flight Turn 如何处理；
3. 旧资源何时释放；
4. Conversation 是否需要新 System Prompt；
5. Tool Schema 是否要通知模型/客户端。

因此新 Session Snapshot 是许多配置的清晰一致性边界。

---

## 70. Zero-turn Rebuild 的边界

Session 在尚未发送真人消息时，某些 Model/Agent Definition 变化可以用 `AgentRebuildSpec` 重建 Agent。

Rebuild 必须复用原 Session 的 Channel Sender 和 Backend，否则会：

- 孤立现有 Coordinator；
- 丢失 Tool Notification 路由；
- 创建无人消费的新 Channel；
- 破坏 Session ID 与 Process 所有权。

这就是 Rebuild Spec 保存大量 Runtime Resource，而不仅是 Config 值的原因。

---

## 71. 配置排错方法

对任意不生效字段，依次问：

1. 它由哪个 Loader 读取：Global 还是 CWD-aware？
2. Raw 文件是否成功 Parse/Version Override？
3. 它是普通 Leaf、Campaign、Hook Additive Collection，还是 Requirements Pin？
4. Effective TOML 中最终值是什么？
5. Typed Config 是否识别该 Key，是否产生 Warning？
6. 字段是否 `serde(skip)`，需要 Runtime Resolver？
7. CLI/Env/Remote 的字段专用优先级是什么？
8. Requirement/Managed Settings 是否 Clamp？
9. 生效点是立即、Rebuild、下一 Session，还是显式 Reload？
10. 现有 Session 是否持有旧 Snapshot？

不要只反复编辑 TOML，而不先定位 Consumer。

---

## 72. 推荐源码阅读顺序

第一遍：基础 Layer 和 Authority。

```text
xai-grok-config/src/loader.rs
xai-grok-config/src/validation.rs
xai-grok-config/src/version_overrides.rs
xai-grok-config/src/config_override.rs
```

第二遍：Soft Overlay 与 Typed Parse。

```text
util/config/campaigns.rs
agent/config.rs::Config
Config::new_from_toml_cfg
Config::resolve_runtime_fields
```

第三遍：传播。

```text
agent/init.rs::resolve_config
session/agent_rebuild.rs
util/config/mcp.rs
util/hooks.rs
```

第四遍：Live Update。

```text
config/watcher.rs
config/reloader.rs
agent/app.rs ConfigUpdate loop
session/mcp_restart.rs
```

---

## 73. 可动手验证的实验

### 实验一：Deep Merge

在 Managed 与 User 中分别设置同一 Table 的不同 Leaf，再把 User 的父节点改成 Scalar，观察递归 Merge 与整树替换差异。

### 实验二：Requirements 重放

让 Campaign 与 Requirements 同时改 `models.default`，确认 Campaign 应用后 Requirements 仍胜出。

### 实验三：版本边界

为相同 Leaf 写多个 Min/Max Version Patch，验证 Inclusive Boundary 和相同 Minimum 的声明顺序。

### 实验四：None 与 False

对 `BoolFlag` 分别提供 `None`、`Some(false)`，观察 Remote/Default 是否还能进入决策。

### 实验五：Last-known-good

先加载合法 Config，再写入非法 TOML，确认 Reloader 保留旧值；修复后确认再次产生差异 Update。

### 实验六：Project Scope

打开两个不同 CWD 的 Session，只修改其中一个 `.mcp.json`，确认只重载匹配 Session。

### 实验七：Snapshot

修改 Compat/Skill Config，比较现有 Session、新 `/new` Session 和 Zero-turn Rebuild 的行为差异。

---

## 74. 常见误解

### 误解一：Managed Config 永远覆盖 User Config

基础 Merge 中 User 在 Managed 之后；不可绕过约束由 Requirements/MDM/Typed Clamp 表达。

### 误解二：Effective TOML 就是最终值

之后还有 Env、CLI、Remote、Requirements 和 Runtime Resolver。

### 误解三：所有字段优先级都相同

每个 Resolver 可定义不同来源顺序。

### 误解四：Remote Settings 是另一个 TOML Layer

多数 Remote Field 逐项参与 Resolver，不做整树 Merge。

### 误解五：Project Config 可以覆盖全部 Agent 设置

它只由明确的 CWD-aware Consumer 使用。

### 误解六：Watcher 发现变化等于现有 Session 已更新

Watcher 只发事件；Consumer 还必须实现 Apply/Rebuild。

### 误解七：改 `Config` Struct 就能改变 Prompt 和 Tool

Prompt、Registry、Bridge Resource 已是派生对象，通常要 Rebuild。

### 误解八：配置文件损坏后应清空运行配置

Reloader 保留 Last-known-good，避免瞬时编辑状态破坏运行 Session。

### 误解九：环境变量只有一种用途

既有字符串 Expansion，也有独立 Override Source。

### 误解十：Campaign 是强制策略

Campaign 是可 Dismiss Soft Overlay；Requirements 才是约束层。

---

## 75. 设计上最值得学习的模式

### Keep Layers Until Provenance Is No Longer Needed

先保留 Layer，晚些再合并，避免过早丢失 Authority 信息。

### Separate Soft Defaults from Hard Constraints

Campaign 可撤销，Requirements 可 Clamp，两者不用同一个“高优先级配置”概念混淆。

### Resolve at the Consumer Boundary

Remote、CLI、Env 由字段专用 Resolver 决定，避免全局 Merge 误赋 Authority。

### Snapshot Complex Object Graphs

新 Session 是 Prompt、Tool、Skill、Hook 等派生对象的一致性边界。

### Last-known-good Reload

无法读取新事实时保持旧状态，只有明确删除才 Clear。

### Reinject Changes into the Authoritative Event Loop

热更新通过 ACP Internal Request 串行化，而不是从 Watcher Task 并发修改 Session。

---

## 76. Glossary

### ACP Internal Request

Agent 内部注入 ACP 输入流的控制请求，用于把 Reload 与普通 Session 操作串行化。

### Additive Configuration

多层内容全部保留而非同 Key 覆盖的配置类型；Hook Collection 是例子。

### Advisory Lock

多个进程自愿遵守的文件锁，用于保护跨进程 Read-modify-write。

### Agent Rebuild

用已有 Session Runtime Resource 和新 Definition/配置重新构造 Agent。

### AgentRebuildSpec

保存构建 Session-bound Agent 所有输入的规范 Recipe。

### Authority

一个配置来源覆盖或约束另一个来源的权力。

### Base Config

应用 Campaign 前，由普通 Layer 与 Requirements 合并得到的 Raw TOML。

### BoolFlag

按 Requirement、CLI、Env、Config、Managed、Remote、Default 顺序解析 Boolean 的构建器。

### Campaign

可被 Kill、Dismiss 的软配置 Patch，常用于推荐、实验或迁移。

### Clamp

在 Typed Runtime 阶段强制字段满足管理约束，不允许普通来源重新覆盖。

### Config Layer

一个独立来源的 Raw TOML 及其 Authority/Provenance。

### Config Source

最终值的来源标签，如 CLI、Env、Remote、Requirement 或 Default。

### Constrained

同时保存 Requirement Pin 和 Source 的 Typed Wrapper。

### Consumer

读取并实际应用某个配置字段的子系统，例如 MCP Resolver 或 AgentBuilder。

### CWD-aware Resolver

结合 Session 工作目录读取 Project Config 的解析器。

### Deep Merge

Table 递归合并、其他值整体替换的 TOML 合并算法。

### Default

没有更高来源给值时使用的编译期或类型默认值。

### Dismiss

记录用户不再接受某 Campaign 的选择，使其 Patch 不再生效。

### Effective Config

完成文件分层与 Campaign Overlay 后的 Raw TOML；仍不一定是最终 Runtime Value。

### Enforcement

把 Requirements 或 Managed Settings 作为强制约束应用到 Typed Config。

### Environment Expansion

把字符串中的 `$VAR`/`${VAR}` 替换为环境值。

### Environment Override

把某环境变量作为独立配置来源参与优先级解析。

### Fail Closed

异常时拒绝继续或采用更限制状态。Requirements 可对 Version Validation 显式启用。

### Fail Open

异常时跳过可选 Layer 或功能并继续。许多普通配置加载错误采用这种方式。

### Feature Flag

通常来自 Remote Settings、低于本地显式配置的运行开关。

### First-wins

按候选顺序保留最先出现的同 ID/同内容项。

### Folder Trust

决定 Project Plugin、Hook、MCP 等主动配置能否被加载的目录信任状态。

### Hot Reload

不重启进程，对文件变化进行检测并更新部分运行状态。

### Last-known-good

最近一次成功解析并接受的配置快照；新文件损坏时继续使用它。

### Leaf

TOML 配置树中不再是 Table 的最终值节点。

### Managed Config

服务或管理员提供的配置 Layer；在本项目中不应自动等同于不可覆盖 Requirements。

### Managed Settings

外部管理格式提供的 Typed Enforcement 来源，与 `managed_config.toml` 不同。

### MDM

Mobile Device Management。macOS Managed Preferences 提供的 OS 保护管理层。

### None

某来源没有意见；与显式 `false`、空字符串或空数组语义不同。

### Origin Map

把 Dotted Config Path 映射到普通文件来源的诊断结构。

### Overlay

在 Base 上临时应用的一组 Patch；Campaign 是 Soft Overlay。

### Patch

待 Deep Merge 进配置树的局部 TOML Table。

### Pin

Requirements 对字段设置的强制值和来源。

### Project Config

项目目录内 `.grok/config.toml`；由明确的 CWD-aware 能力读取，不是全局 Layer。

### Provenance

配置、Hook 或策略来自何处的来源信息。

### Raw Config

尚未完全解析成 Runtime Object 的 `toml::Value` 配置树。

### Reapply Requirements

Campaign 或 Runtime 同步后再次合并/Clamp Requirements，恢复 Admin Authority。

### Remote Settings

从服务端获得、逐字段参与 Resolver 的设置与 Feature Flag 集合。

### Resolved

同时包含最终 Value 和 ConfigSource 的解析结果。

### Runtime Field

不能仅靠 Serde 得到、需要 CLI/Env/Remote/Mode Resolver 填充的 Config 字段。

### RuntimeResolutionContext

将 Raw Config、Remote Settings、CLI Flag 和运行模式传给 Runtime Resolver 的输入对象。

### Session Snapshot

Session 创建时固化的一组配置及派生对象；后续全局变化不自动修改它。

### Soft Overlay

可以撤销、Dismiss 或被高 Authority 约束覆盖的配置覆盖层。

### Typed Config

由 Raw TOML 解析出的 Rust `Config` 结构及其专用集合。

### Version Override

按当前 CLI SemVer 条件应用的 Layer-local Patch。

### Watcher

把 OS 文件系统事件归一为 ConfigChangeEvent 的组件。

### Zero-turn Rebuild

Session 尚无真人 Turn 时，用保留的 RebuildSpec 安全重建 Agent。

---

## 77. 下一篇建议

下一篇可继续写：

> 源码精读 19：Agent Observability Runtime 如何把 Session、Turn、Sampling、Tool、Hook、Permission、Token、Error 与 Usage 统一成 Trace、Metric、Event 和可上传 Artifact，同时完成脱敏、采样、失败隔离与跨异步任务关联。

它会集中解释此前多篇不断出现的：

```text
Tracing Span
Telemetry Event
Session Context
Unified Log
Usage Folding
Trace Upload
Diagnostic Upload
Redaction
Attribution
```
