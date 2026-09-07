# 94：App-server 配置 API、Layer Stack、来源、写入、Requirements 与运行时刷新

> 源码基线：`4ee41929eaf4`。本章解决“同一个配置为什么能同时出现在系统、云端、用户文件、项目目录和启动参数中；App 为什么不能只读写 `config.toml`；一次写入怎样判断冲突、覆盖、管理员约束与是否需要刷新正在运行的任务”。

## 1. 本章解决什么问题

假设设置页显示：

```text
model = "gpt-example"
approval_policy = "never"
```

只看最终值，你仍然不知道：

- `model` 是用户选的、项目要求的，还是启动参数临时覆盖的？
- `approval_policy` 能不能改？
- 点“保存”后，是写用户文件、项目文件，还是管理员文件？
- 保存成功后，当前任务是否马上采用新值？
- 两个窗口同时保存时，后保存者会不会静默覆盖先保存者？
- 文件里写成了一个值，但最终采用另一个值时，UI 应该显示哪一个？

这些不是 TOML 语法问题，而是配置系统的状态、权限和并发问题。

## 2. 资料边界

公开配置项及 `config.toml` 用法可查[官方 Codex 配置参考](https://developers.openai.com/codex/config-reference)。

App-server 的分层来源、版本指纹、写入状态、requirements 映射和热刷新细节，以固定提交中的这些模块为准：

- `codex-rs/app-server-protocol/src/protocol/v2/config.rs`
- `codex-rs/app-server/src/config_manager_service.rs`
- `codex-rs/app-server/src/request_processors/config_processor.rs`
- `codex-rs/config/src/state.rs`
- `codex-rs/config/src/loader/mod.rs`

## 3. 先说人话：配置不是一张纸，而是一摞透明胶片

把每个配置来源想成一张透明胶片。

底层胶片先放，越靠上的胶片优先级越高；上层写了某个格子，就遮住下层同一格。

```text
最上面：旧式 MDM managed config   ← 最能覆盖
         旧式文件 managed config
         本次启动参数 -c
         项目 .codex/config.toml
         用户 profile 文件
         用户 config.toml
         企业云端配置
最下面：系统 config.toml           ← 最容易被覆盖
```

这摞胶片叫 `ConfigLayerStack`。

## 4. 三个必须分开的答案

配置 UI 至少需要回答三件不同的事：

| 问题 | 对应概念 | 示例答案 |
|---|---|---|
| 最终采用什么？ | effective config | `model = "project-model"` |
| 这个最终值来自哪里？ | origin | 项目 `.codex/config.toml` |
| 管理员允许什么？ | requirements | 只允许 `read-only` sandbox |

把三者混成一个对象，会产生大量误判。

## 5. 最重要的一句话

`config` 告诉客户端“现在实际采用什么”，`origins` 告诉它“谁赢了”，`layers` 告诉它“所有参赛者各自写了什么”，`requirements` 则是另一条独立的管理员约束链。

## 6. 本章贯穿案例

假设有四个普通配置层：

```toml
# system config
model = "system-model"
sandbox_mode = "read-only"
```

```toml
# $CODEX_HOME/config.toml
model = "user-model"
approval_policy = "on-request"
```

```toml
# repo/.codex/config.toml
model = "project-model"
```

启动时还有：

```text
codex -c approval_policy="never"
```

最终结果是：

```toml
model = "project-model"
sandbox_mode = "read-only"
approval_policy = "never"
```

但三个字段的来源不同。

## 7. 为什么不能只返回最终 TOML

如果 API 只返回上面的最终值，用户看到 `model = "project-model"`，却不知道修改用户文件为何没效果。

来源信息不是调试附属品，而是设置 UI 正确解释状态的必要数据。

## 8. App-server 暴露的四个核心方法

| RPC method | 作用 |
|---|---|
| `config/read` | 读取最终配置、来源和可选的完整层 |
| `config/value/write` | 写一个 key path |
| `config/batchWrite` | 一次写多个 key path |
| `configRequirements/read` | 读取管理员约束 |

前三个围绕普通配置层；第四个读取 requirements。

## 9. 为什么方法名在 wire 上是 camelCase

Rust 字段名通常是 `include_layers`、`key_path`、`expected_version`。

JSON wire 使用：

```json
{
  "includeLayers": true,
  "keyPath": "features.memories",
  "expectedVersion": "sha256:..."
}
```

这是 Serde/TS 协议映射，不代表 TOML 键也改成 camelCase。

## 10. `config/read` 请求

请求只有两个业务字段：

```json
{
  "includeLayers": true,
  "cwd": "/repo/subdir"
}
```

- `includeLayers` 默认 `false`。
- `cwd` 可省略或为 `null`。
- `cwd` 决定要沿哪个项目路径寻找 `.codex` 层。

## 11. 为什么读配置需要 `cwd`

项目配置不是全局唯一的。

你在 `/repo-a` 和 `/repo-b` 中运行 Codex，可能得到不同项目层；甚至从一个仓库子目录向上走到项目根时，可能遇到多级 `.codex/`。

所以“Codex 的配置是什么”这个问题不完整。

更准确的问题是：

```text
从这个 cwd 看出去，Codex 的有效配置是什么？
```

## 12. 省略 `cwd` 的含义

固定提交中，`config/read` 在没有 `cwd` 时加载 `thread-agnostic config`。

它不关联某条任务或项目根，因此不包含仓库内 `.codex/` 层。

这适合全局设置页；不适合回答某个具体项目实际采用了什么。

## 13. `cwd` 必须是绝对路径

服务端把字符串转成 `AbsolutePathBuf`。

相对路径没有稳定的解释基础：它究竟相对 App、app-server 进程，还是远程执行环境？因此读取层时明确要求绝对路径。

## 14. `config/read` 响应的三部分

简化响应：

```json
{
  "config": {
    "model": "project-model",
    "approval_policy": "never",
    "sandbox_mode": "read-only"
  },
  "origins": {
    "model": { "name": { "type": "project", "dotCodexFolder": "/repo/.codex" }, "version": "sha256:..." },
    "approval_policy": { "name": { "type": "sessionFlags" }, "version": "sha256:..." },
    "sandbox_mode": { "name": { "type": "system", "file": "/etc/codex/config.toml" }, "version": "sha256:..." }
  },
  "layers": []
}
```

`layers` 只在 `includeLayers=true` 时出现。

## 15. `Config` 为什么既有类型字段又有 `additional`

协议中的常见字段有强类型，例如：

- `model: Option<String>`
- `approval_policy: Option<AskForApproval>`
- `sandbox_mode: Option<SandboxMode>`
- `model_reasoning_effort: Option<ReasoningEffort>`

同时用 flatten 的 `additional: HashMap<String, JsonValue>` 保留其他配置。

这让客户端既能类型安全地处理常见项，又不会因为协议类型暂未列出某个合法键就丢数据。

## 16. `additional` 不等于“随便什么都合法”

它只是 wire 投影的兼容容器。

写入时，服务端仍会把结果反序列化成 `ConfigToml` 并执行配置校验；未知或类型错误的内容仍可能被拒绝。

## 17. Layer 的四个字段

每个 `ConfigLayer` 包含：

| 字段 | 含义 |
|---|---|
| `name` | 来源身份，不只是显示名称 |
| `version` | 该层内容的稳定指纹 |
| `config` | 这一层自身的 JSON 值 |
| `disabledReason` | 此层为何被展示但不参加合并 |

## 18. 为什么字段叫 `name`，值却是枚举对象

源码里的 `name: ConfigLayerSource` 并非普通字符串。

它可能携带文件路径、MDM domain/key、企业层 ID/name 或项目 `.codex` 路径。因此更接近“来源描述符”。

## 19. 八种普通配置来源

固定提交中的 `ConfigLayerSource` 有：

1. `mdm`
2. `system`
3. `enterpriseManaged`
4. `user`
5. `project`
6. `sessionFlags`
7. `legacyManagedConfigTomlFromFile`
8. `legacyManagedConfigTomlFromMdm`

## 20. `mdm`

macOS 管理设备策略来源，携带：

```json
{ "type": "mdm", "domain": "...", "key": "..." }
```

在普通配置优先级中它位于最低端；不要把它和 requirements 中的 MDM 管理约束混为一谈。

## 21. `system`

主机范围的配置文件。

Unix 默认位置可见 `/etc/codex/config.toml`；Windows 使用系统配置路径。wire 携带绝对 `file` 路径。

## 22. `enterpriseManaged`

由企业云端配置 bundle 下发。

它携带稳定 `id` 和管理员可读 `name`，以便诊断时指出应该联系管理员修改哪一层。

## 23. `user`

通常是 `$CODEX_HOME/config.toml`。

来源还带 `profile: Option<String>`：

- `null` 表示基础用户文件。
- 有值表示选中的 profile-v2 文件层。

## 24. `project`

来自项目 `.codex/` 目录。

wire 返回 `dotCodexFolder`，客户端可据此向用户解释究竟哪个项目目录覆盖了全局设置。

## 25. `sessionFlags`

来自本次进程的 `-c` / `--config` 覆盖。

它是临时层，不表示用户文件已经改变；关闭本次 session 后通常不再存在。

## 26. 两个 `legacyManaged...`

旧式 `managed_config.toml` 曾被设计为压在所有普通配置之上。

固定提交仍保留文件来源和 MDM 来源，作为迁移期的 best effort 兼容层；新设计倾向用 `requirements.toml` 表达管理约束。

## 27. 精确优先级

源码给每种来源一个数值；数值越大，越能覆盖：

| 来源 | precedence |
|---|---:|
| MDM 普通配置 | 0 |
| System | 10 |
| Enterprise managed | 15 |
| User base | 20 |
| User profile-v2 | 21 |
| Project | 25 |
| Session flags | 30 |
| Legacy managed file | 40 |
| Legacy managed MDM | 50 |

## 28. 为什么 MDM 一会儿最低、一会儿最高

因为这里存在两种历史对象：

- `Mdm`：普通托管偏好层，precedence 0。
- `LegacyManagedConfigTomlFromMdm`：旧式“最后覆盖所有东西”的 managed config，precedence 50。

仅看“MDM”三个字会误读，必须看完整 variant。

## 29. 内部存储顺序与 API 展示顺序相反

`ConfigLayerStack` 内部从低到高存：

```text
base ... top
```

这样按顺序 fold 时，后面的 overlay 覆盖前面。

但 `config/read(includeLayers=true)` 用 `all_layers_high_to_low()` 返回，客户端首先看到最高优先级层。

## 30. `disabledReason` 的意义

某层可能需要展示给 UI，但因信任或其他原因不能参与合并。

它仍在 `layers` 中，带 `disabledReason`；`effective_config()` 与 `origins()` 会跳过它。

所以：

```text
被发现 ≠ 被启用 ≠ 对最终值有贡献
```

## 31. 合并的基本规则

普通 TOML 合并使用递归 overlay：

- 两边都是 table：逐键递归合并。
- 其他类型：高层值整体替换低层值。
- array 通常作为非 table 整体替换，不逐项拼接。

## 32. 一个递归 table 例子

低层：

```toml
[features]
a = true
b = false
```

高层：

```toml
[features]
b = true
```

结果：

```toml
[features]
a = true
b = true
```

高层没有写 `a`，因此低层的 `a` 保留。

## 33. 一个整体替换例子

低层：

```toml
allowed_commands = ["git", "rg"]
```

高层：

```toml
allowed_commands = ["cargo"]
```

通常结果是 `["cargo"]`，而不是三个元素拼接。

## 34. 合并还包含领域特例

固定提交会额外处理：

- key alias 规范化。
- 网络 domain host 规范化。
- shell environment filter 的大小写和新旧表示切换。
- `multi_agent_v2` 从 bool 到 table 形态的兼容。

因此不能用一个通用 JSON merge 库替代 Codex 的配置合并。

## 35. Origin 是怎样计算的

系统从低到高遍历启用层。

遇到标量叶子时，把当前层 metadata 写到以点连接的路径上；高层再写同一路径，就覆盖之前的 metadata。

最后留下的就是赢家。

## 36. Origin 路径例子

配置：

```toml
[sandbox_workspace_write]
network_access = false
writable_roots = ["/work"]
```

可能产生：

```text
sandbox_workspace_write.network_access
sandbox_workspace_write.writable_roots.0
```

数组索引也成为路径 segment。

## 37. 为什么 origin 记录叶子，不只记录整张 table

同一 table 的不同字段可能由不同层获胜。

若只记录 `features` 来自项目层，就无法表达 `features.a` 来自用户层、`features.b` 来自项目层。

## 38. Version 是什么

每层的 `version` 是：

```text
sha256:<hex>
```

生成过程是：

1. TOML 转 JSON。
2. 对 JSON object key 递归排序。
3. 序列化 canonical JSON。
4. 计算 SHA-256。

## 39. Version 不是什么

它不是：

- Git commit。
- 文件修改时间。
- 全局递增序号。
- 数据库 row version。
- 用户可编辑的配置项。

它是某一层规范化内容的稳定指纹。

## 40. 为什么不直接用 mtime

mtime 可能因为复制、恢复、编辑器行为或文件系统精度而不可靠。

内容指纹直接回答“客户端读到的配置内容和当前内容是否还是同一份”。

## 41. `expectedVersion` 是乐观并发控制

客户端先读到用户层版本 `V1`，编辑后提交：

```json
{
  "keyPath": "model",
  "value": "new-model",
  "mergeStrategy": "replace",
  "expectedVersion": "V1"
}
```

服务端重新加载层；若用户层已变成 `V2`，返回 `configVersionConflict`。

## 42. 丢失更新案例

时间线：

```text
窗口 A 读 V1
窗口 B 读 V1
窗口 A 写入，得到 V2
窗口 B 仍按 V1 写入
```

若没有 `expectedVersion`，B 可能覆盖 A 的修改。

有版本检查时，B 被要求重新读取、合并用户意图再重试。

## 43. 为什么 `expectedVersion` 是可选的

有些调用者接受 last-write-wins，或者只进行内部条件清理。

协议允许省略，但交互式设置 UI 应优先传入最近读到的用户层版本。

## 44. 单值写请求

```json
{
  "keyPath": "features.memories",
  "value": true,
  "mergeStrategy": "replace",
  "filePath": "/home/me/.codex/config.toml",
  "expectedVersion": "sha256:..."
}
```

`filePath` 与 `expectedVersion` 可省略。

## 45. 写入目标受到严格限制

即使请求允许传 `filePath`，服务端也只允许它与当前 active user config path 匹配。

想借此写项目层、系统层或任意 TOML 文件，会得到：

```text
configLayerReadonly
```

## 46. 为什么允许传路径又只接受一个路径

路径帮助客户端明确自己正编辑哪一个 active user layer，也支持 profile-v2 的实际文件。

但权限边界仍是“只能改当前用户层”，不能把通用配置 API 变成任意文件写接口。

## 47. 省略 `filePath`

默认写用户配置路径。

若选中了 profile-v2，active user layer 的语义需要结合 loader overrides 与实际层判断；不要在客户端硬编码“永远是 `$CODEX_HOME/config.toml`”。

## 48. `keyPath` 怎样拆分

普通点号分段：

```text
mcp_servers.docs.enabled
```

拆成：

```text
["mcp_servers", "docs", "enabled"]
```

空路径、连续点、尾点都会被拒绝。

## 49. 带点的键名怎样写

引号 segment 中的点不分段：

```text
permissions.default.network.domains."api.example.com"
```

引号内还允许用反斜线转义点号或引号等标点。

## 50. `keyPath` 不是 JSON Pointer

它没有 `/a/b` 语法，也不是完整 TOML dotted-key parser。

这是 App-server 为配置编辑定义的点路径格式；要按 `parse_key_path()` 的规则理解。

## 51. `null` 表示删除

写请求中的：

```json
"value": null
```

不会把 TOML 写成 null，因为 TOML 没有 JSON null 值。

服务端把它解释成清除该路径。

## 52. 删除不存在的路径

是成功的 no-op。

它不会为了删除一个不存在的嵌套键而创建空 table，也不会无故改写文件。

## 53. `replace` 策略

`replace` 把目标路径设置为给定值。

目标是 table 时，也以给定 table 作为该路径的新值，而不是保证保留旧 table 中未提供的成员。

## 54. `upsert` 策略

`upsert` 的核心含义是“存在则合并/更新，不存在则插入”。

固定提交主要在目标和新值都是 table 时建立 sparse overlay 并递归合并。

## 55. Replace 与 Upsert 对比

原始用户层：

```toml
[apps.demo]
enabled = true
destructive_enabled = false
```

编辑值：

```json
{ "enabled": false }
```

在 `apps.demo` 上：

- `replace`：可能只剩 `enabled=false`。
- `upsert`：保留 `destructive_enabled=false`，更新 `enabled`。

## 56. 为什么不能一律 Upsert

有时用户明确要用新 table 完整替换旧结构；无条件递归合并会留下本应删除的旧键。

策略必须由调用者根据编辑意图选择。

## 57. Batch Write

批量请求：

```json
{
  "edits": [
    { "keyPath": "model", "value": "gpt-example", "mergeStrategy": "replace" },
    { "keyPath": "model_reasoning_effort", "value": "high", "mergeStrategy": "replace" }
  ],
  "expectedVersion": "sha256:...",
  "reloadUserConfig": false
}
```

## 58. 为什么需要批量写

多个字段可能构成一个逻辑设置。

若分两次写：

1. 第一项成功。
2. 第二项校验失败。
3. 文件停在半完成状态。

批量写先在内存中应用所有 edits 并整体校验，再统一落盘。

## 59. Batch 的失败原子性

只要任一 edit 在解析、requirements、legacy profile 或整体配置校验上失败，持久化步骤不会发生。

例如先改 `model`、后非法写 `profile`，测试确认原文件保持不变。

## 60. Batch 不是多文件事务

所有 edits 仍针对同一个 active user config 文件。

它不提供跨项目文件、系统文件与 requirements 文件的分布式事务。

## 61. 写入前的校验顺序

简化链路：

```text
校验目标路径
  -> 重新加载 layer stack
  -> 检查 expectedVersion
  -> 逐项解析 keyPath/value
  -> 拒绝 requirements 管理项
  -> 应用内存编辑
  -> 校验用户层 ConfigToml
  -> 校验 feature requirements
  -> 注入更新后的用户层，重算 effective config
  -> 校验最终配置
  -> 持久化
  -> 检查是否被高层覆盖
```

## 62. 为什么用户层和最终配置都要校验

用户层单独合法，不代表与其他层合并后一定合法；反过来，也不能依赖高层覆盖来隐藏用户层里的类型错误。

两次校验保护两个不同不变量。

## 63. 为什么不能写 legacy `profile`

固定提交拒绝新增：

```text
profile
profiles.*
```

错误说明应改用 `--profile <name>` 和 `<name>.config.toml` 的 profile-v2 机制。

删除旧值仍可作为迁移清理；拒绝的是继续写入旧表示。

## 64. 文件不存在时怎么办

服务端为 active user layer 创建空配置文件，再把它作为用户层参加编辑。

创建使用原子写路径；随后正常计算版本与响应。

## 65. Symlink 为什么需要特殊处理

写配置前会解析 symlink 的读取路径和实际写入路径。

这样既能读取链接指向的现有内容，又避免用普通覆盖操作意外把 symlink 本身替换成常规文件。

## 66. 怎样保留注释和顺序

服务端不是把整个 TOML 反序列化后重新漂亮打印。

它用 `ConfigEditsBuilder` 和 `toml_edit` 对具体路径做 set/clear，因此测试能证明原有注释与字段顺序保留。

## 67. 为什么“语义对象校验”和“文本编辑”要分开

- `toml::Value` / `ConfigToml` 适合合并和校验语义。
- `toml_edit` 适合保留用户手写文件的格式、注释和顺序。

同一个表示很难同时把两件事都做好。

## 68. 写响应

```json
{
  "status": "ok",
  "version": "sha256:...",
  "filePath": "/home/me/.codex/config.toml",
  "overriddenMetadata": null
}
```

返回的新 `version` 应用于下一次乐观并发写。

## 69. 两种成功状态

| status | 含义 |
|---|---|
| `ok` | 用户层写入后，该路径的用户值也是最终值 |
| `okOverridden` | 文件写成功，但至少一个编辑路径被更高层覆盖 |

两者都不是持久化错误。

## 70. `okOverridden` 贯穿案例

项目层已有：

```toml
model = "project-model"
```

用户设置页写：

```json
{ "keyPath": "model", "value": "user-new", "mergeStrategy": "replace" }
```

用户文件确实变为 `user-new`，但项目层优先级更高，最终仍是 `project-model`。

因此响应是 `okOverridden`。

## 71. `overriddenMetadata`

它包含：

- `message`：适合解释给用户的覆盖原因。
- `overridingLayer`：赢家的来源和版本。
- `effectiveValue`：真正采用的值。

这让 UI 能说“已保存到用户配置，但当前项目仍覆盖为 X”。

## 72. Batch 只报告第一个覆盖

`first_overridden_edit()` 按 edits 顺序寻找第一个被覆盖项。

响应不是所有覆盖项的数组。因此复杂设置页保存后，仍可重新 `config/read` 做完整对账。

## 73. 七类写错误

固定提交定义：

| code | 典型原因 |
|---|---|
| `configLayerReadonly` | 试图写非 active user 文件 |
| `configRequirementReadonly` | 路径被 requirements 精确管理 |
| `configVersionConflict` | `expectedVersion` 已过期 |
| `configValidationError` | 路径、值或整体配置非法 |
| `configPathNotFound` | 配置路径找不到 |
| `configSchemaUnknownKey` | schema 不认识该键 |
| `userLayerNotFound` | 更新后未找到用户层 |

并非每个 code 都一定由本函数的每条分支直接产生；它们构成 wire 错误分类合同。

## 74. App 应怎样处理错误

- readonly：禁用控件或显示管理来源。
- version conflict：重新读取，不要盲目重试旧 payload。
- validation：保留用户输入并显示具体消息。
- path/schema：提示客户端与服务端能力可能不匹配。
- 内部 IO/error：不要伪装成字段校验失败。

## 75. 请求为什么还需要 Serialization Scope

版本检查只能发现冲突；服务端还需避免自己的多个配置请求交叉执行。

协议注册表中：

- `config/read` 使用 `global_shared_read("config")`。
- value/batch write 使用 `global("config")`。
- requirements read 也进入全局 config 域。

## 76. Shared Read 与 Write 的效果

多个配置读取可以并发。

写请求在同一个全局 `config` resource 上串行，并会与共享读协调，从而避免读到服务端编辑中间态。

这与第 87 章的 serialization scope 是同一套机制在配置领域的应用。

## 77. Requirements 不是最高优先级普通 Layer

这是本章最重要的边界之一。

普通 layer 说：

```text
“我给这个键提供一个候选值。”
```

requirement 说：

```text
“这个键只能取哪些值，或必须精确取什么值。”
```

它们分别组合、分别追踪。

## 78. 为什么要分开

若把管理员约束伪装成最高层普通值：

- UI 无法区分“目前恰好是 false”和“组织强制 false”。
- 写入后只能看到被覆盖，不能在写前明确 readonly。
- allow-list 约束无法表达，因为它不是一个单一最终值。

## 79. Requirements 来源

固定提交能组合的管理来源包括：

- MDM managed preferences。
- 企业云端 managed requirements。
- 系统 `requirements.toml`。
- 旧式 managed config 的兼容约束来源。

多个来源还可形成 `Composite`，并保留参与来源。

## 80. `configRequirements/read`

该方法没有业务参数。

响应：

```json
{ "requirements": null }
```

表示没有配置 requirements；不是读取失败，也不是“所有能力都禁止”。

## 81. Requirements API 能表达什么

固定提交包含的代表性类别：

- 允许的 approval policies / reviewers。
- 允许的 sandbox modes 与 Windows sandbox implementations。
- permission profiles 与默认权限。
- 允许的 web search modes。
- managed hooks、appshots、remote control。
- computer/browser use。
- feature requirements。
- residency 与 network constraints。
- 新任务模型默认值。
- sqlite/log/model catalog 路径。
- update check、login shell、feedback。

## 82. Allow-list 与 Exact Requirement

两种常见约束：

```text
允许集合：sandbox_mode ∈ {read-only, workspace-write}
精确要求：allow_login_shell = false
```

前者让用户在集合内选；后者把字段固定为唯一值。

## 83. 读取最终配置时怎样应用 exact requirement

`config/read` 先合并普通层成 `effective_config_toml`，再调用：

```text
requirements_toml.apply_exact_to_config(...)
```

因此响应的 `config` 已反映精确管理要求。

## 84. 为什么 exact requirement 字段不留在普通 origins

读取时，服务端会从 `origins` 中移除被 exact requirement 管理的路径。

原因是普通 layer origin 已不能准确解释最终值；该值来自 requirement 约束链。

客户端应去 `configRequirements/read` 理解它，而不是误标成某个普通配置文件获胜。

## 85. 写入 requirement 管理字段

每个 edit 解析出 segments 后，服务端调用：

```text
exact_requirement_for_config_path(...)
```

如果存在，立即返回 `configRequirementReadonly`，不会先写入再告诉你被覆盖。

## 86. Requirements 也参与 feature 校验

用户层编辑完成后，服务端会调用 feature requirements 校验。

这可拒绝组织要求开启/关闭某功能时的冲突用户配置。

## 87. “被更高层覆盖”和“被 requirement 锁定”的差别

| 情况 | 文件是否写入 | 结果 |
|---|---|---|
| 高层普通 config 覆盖 | 是 | `okOverridden` |
| exact requirement 管理 | 否 | `configRequirementReadonly` |

客户端提示文案也应不同。

## 88. `allow_login_shell` 的默认补齐

固定提交在 `config/read` 中若该值仍为空，会补成 `true`。

这提醒我们：API 返回的 effective config 不一定只是机械合并结果，还可能包含默认化与 requirement 投影。

## 89. Experimental feature 的运行时覆盖

`ConfigRequestProcessor::read()` 在普通 config manager 读取后，还会加载最新 runtime config，并对一组支持的实验功能重写响应中的 `features` 值。

这组运行时 enablement 可能不是持久化 TOML 层。

## 90. 为什么 runtime feature 不应伪装成文件来源

它来自 app-server 内存中的 enablement map，用于当前运行时能力协商。

若 UI 把它当成用户文件内容，会让用户误以为重启后也必然保留。

## 91. 写完配置后为什么清缓存

单值写成功后，processor 清理：

- plugin manager cache。
- skills service cache。

因为配置可能改变 plugin/skill 的启用状态、路径或能力；继续复用旧缓存会产生“文件已保存但能力列表没变”的错觉。

## 92. Batch 的缓存例外

若非空 batch 只改以下 session defaults：

- `model`
- `model_reasoning_effort`
- `plan_mode_reasoning_effort`
- `service_tier`
- `personality`

固定提交把它识别为 `session_defaults_only`，不清 plugin/skill cache，也不热刷新已有任务。

## 93. 为什么 session defaults 不刷新当前任务

这些字段是“新 session/turn 的默认选择”与任务创建语义的一部分。

强行改写已有任务可能让同一 thread 中途换模型、推理强度或人格，破坏可预测性。

## 94. `reloadUserConfig`

它只存在于 batch write 请求，默认 `false`。

为 `true` 且 batch 不属于 session-defaults-only 时，写入后尝试把可热更新设置刷新进已加载 threads。

## 95. 热刷新的总链路

```text
batch 写成功
  -> 清 plugin/skill cache
  -> load_latest_config 做全局预检
  -> list_thread_ids
  -> 逐 thread 获取 current config
  -> load_latest_config_for_thread(current)
  -> rebuild_preserving_session_layers
  -> thread.refresh_runtime_config(next_config)
```

## 96. 为什么先做全局预检

若最新配置连全局重建都失败，就没有必要逐任务刷新。

服务端记录 warning 并保留现有任务配置，不把已经成功的文件写入伪装成失败回滚。

## 97. 为什么逐 Thread 重建

每条任务可能有不同：

- cwd 与项目层。
- session overrides。
- environment/启动期选择。

不能用一个全局 Config 对象直接覆盖所有任务。

## 98. `rebuild_preserving_session_layers`

刷新时先根据该任务 cwd 加载最新基础/用户/项目配置，再保留任务自己的 session layers。

这实现：

```text
更新可变的共享配置 + 不抹掉任务创建时的局部选择
```

## 99. 热刷新是 best effort

遍历中：

- 某 thread 已消失：跳过。
- 某 thread 重建失败：记录 warning，继续其他 thread。
- 不会因一条任务失败而撤销已写文件或停止刷新所有任务。

## 100. 写成功、缓存刷新、Thread 热刷新是三个阶段

必须分别判断：

1. 文件是否写成功。
2. 能力发现缓存是否失效。
3. 已有任务是否采用新运行时配置。

“保存成功”只直接保证第一件事。

## 101. Plugin toggle telemetry

写前 processor 从 key/value 收集可能的 plugin enabled 变化。

写成功后解析 plugin ID，并记录 enabled/disabled analytics；失败写入不会发送成功切换事件。

## 102. 一次读取的完整内部链路

```text
ClientRequest::ConfigRead
  -> MessageProcessor dispatch
  -> ConfigRequestProcessor::read
  -> ConfigManager::read
  -> load_config_layers / load_thread_agnostic_config
  -> ConfigLayerStack::effective_config
  -> requirements exact projection
  -> ConfigToml -> JSON -> API Config
  -> origins filtering
  -> optional layers high-to-low
  -> runtime experimental feature projection
  -> ConfigReadResponse
```

## 103. 一次写入的完整内部链路

```text
ClientRequest::ConfigValueWrite / ConfigBatchWrite
  -> global("config") serialization
  -> ConfigRequestProcessor
  -> ConfigManager::apply_edits
  -> load current stack + version check
  -> parse/apply/validate all edits
  -> ConfigEditsBuilder + toml_edit persist
  -> recompute override metadata
  -> ConfigWriteResponse
  -> plugin analytics
  -> cache invalidation
  -> optional thread runtime refresh
```

## 104. 设置页的推荐读取算法

打开设置页时：

1. 用目标项目绝对 `cwd` 调 `config/read(includeLayers=true)`。
2. 调 `configRequirements/read`。
3. 显示 `config` 的最终值。
4. 用 `origins` 标注普通来源。
5. 用 requirements 禁用或限制受管理控件。
6. 从 active user layer 取版本，供保存时传 `expectedVersion`。

## 105. 设置页的推荐保存算法

1. 把同一逻辑操作组成 batch。
2. 选择清楚 replace/upsert。
3. 带最近的用户层 version。
4. 只把 active user file path 作为 `filePath`。
5. 对 version conflict 重新读取并请用户确认。
6. 对 `okOverridden` 显示“已保存，但当前被 X 覆盖”。
7. 保存后重新读取并对账。

## 106. 不要从 `config` 反推用户文件内容

`config` 是普通层合并、requirements 精确投影、默认化及部分运行时覆盖后的结果。

把它整份写回用户文件会把项目值、启动参数或管理员值错误复制到用户层。

## 107. 不要把最高层数组的第一项当 active user layer

`layers` 从高到低返回；第一项可能是 session flags 或 legacy managed config。

应按 `name.type == "user"` 和 profile/path 语义寻找目标，并理解 active user 的服务端规则。

## 108. 不要在冲突后自动去掉 `expectedVersion` 重试

那等价于说“既然发现别人改过，就静默覆盖别人”。

正确流程是重新读 V2、重新应用用户意图，必要时展示冲突。

## 109. 不要把 `okOverridden` 当写失败

用户文件已成功改变，可能在离开当前项目或去掉 session flag 后生效。

若 UI 显示“保存失败”，用户会重复操作并困惑文件为何已经变化。

## 110. 不要把 requirement readonly 当普通覆盖

管理员约束不是另一个偏好候选。

UI 应明确显示“由组织管理”，而不是鼓励用户不断重试保存。

## 111. 不要承诺所有设置都能热刷新

`reloadUserConfig=true` 也有 session-default 例外，且逐任务刷新是 best effort。

设置说明应区分：

- 立即影响当前任务。
- 只影响新任务。
- 需要重启进程。

## 112. 常见故障：文件改了，界面没变

按顺序检查：

1. 是否读了正确 `cwd`？
2. 是否被项目层或 session flag 覆盖？
3. `config/read` 是否返回 `okOverridden` 对应来源？
4. 是否是 requirement 精确管理项？
5. 是否只清文件却没重新读？
6. 是否属于不热刷新的 session default？

## 113. 常见故障：两个窗口互相覆盖

检查：

- 保存请求是否带 `expectedVersion`？
- 版本取自 active user layer，还是误取了项目层？
- 收到 `configVersionConflict` 后是否无条件重试？
- 保存后是否使用响应的新 version？

## 114. 常见故障：打开不同仓库，设置来源变化

这通常不是缓存 bug。

比较两个 `cwd` 的 `config/read(includeLayers=true)`，检查项目 `.codex` 层和 disabled reason。

## 115. 常见故障：删除字段后最终值仍存在

删除只清用户层对应路径。

低优先级 system/enterprise 层可能重新显露，或者高优先级 project/session 层继续提供值；最终配置不一定变成“无值”。

## 116. 常见故障：批量写了一半

按固定提交逻辑，应用/校验失败不应产生部分持久化。

若观察到半写，优先确认：

- 客户端是否其实发了多次 value/write。
- 是否有外部编辑器并发改文件。
- 是否读取了错误的文件路径。

## 117. 测试为什么比读实现更有价值

`config_manager_service_tests.rs` 直接证明：

- 注释和顺序保留。
- 删除缺失路径是 no-op。
- legacy profile 写入被拒。
- batch 失败不改原文件。
- origins/layers 的顺序和来源正确。
- 嵌套 App/MCP 路径可写。

测试把协议意图变成可执行合同。

## 118. 值得补的测试矩阵

修改配置链路时至少考虑：

| 维度 | 用例 |
|---|---|
| 层 | user/project/session/managed |
| 写法 | replace/upsert/null clear |
| 并发 | version match/conflict |
| 约束 | none/allow-list/exact |
| 结果 | ok/okOverridden/error |
| 文件 | missing/comment/symlink/profile |
| runtime | reload false/true/session-default exception |

## 119. 修改 wire 类型时的同步工作

若改 `ConfigReadParams`、`ConfigWriteResponse` 或 requirements API：

1. 保持 v2 camelCase wire 规则。
2. request 的可选字段使用协议规定的 nullable TS 标注。
3. 更新 app-server README 示例。
4. 运行 app-server schema 生成。
5. 运行 `codex-app-server-protocol` 测试。

## 120. 修改 ConfigToml 时的同步工作

除了行为测试，还要运行配置 schema 生成，避免 `config.schema.json` 漂移。

App-server API schema 与 `config.toml` schema 是两个相关但不同的合同，不能只更新一个。

## 121. 修改优先级的风险

调整一个 precedence 数值会同时影响：

- 最终配置。
- origins。
- `okOverridden` 判断。
- 设置 UI 的来源说明。
- 旧用户对 CLI/项目/托管配置的预期。

这是兼容性变化，不是简单排序重构。

## 122. 修改 merge 规则的风险

递归合并改成整体替换，或反过来，会改变既有文件组合语义。

必须用多个层、嵌套 table、array、alias 和领域特例测试，而不能只测单层反序列化。

## 123. 修改热刷新的风险

若把 session defaults 也刷新进已有任务，要回答：

- 正在进行的 turn 是否中途改变？
- 模型上下文和工具配置是否一致？
- 远程 environment 是否需重建？
- 缓存与 telemetry 是否同步？
- 恢复旧 thread 时采用旧值还是新值？

## 124. 一个完整诊断示例

症状：用户把 sandbox 改成 workspace-write，但当前项目仍是 read-only。

步骤：

1. `config/read` 确认 effective `sandbox_mode`。
2. 查 `origins["sandbox_mode"]`。
3. 若 origin 是 project/session，说明普通高层覆盖。
4. 若 origin 缺失，再查 `configRequirements/read` 是否 exact 管理。
5. 若写响应是 readonly，解释管理员约束。
6. 若写响应是 okOverridden，解释用户文件已保存但赢家不同。
7. 若当前 thread 未变，检查是否请求热刷新及该字段是否支持。

## 125. 一张最终心智图

```text
ordinary sources
  system -> enterprise -> user -> profile -> project -> session -> legacy managed
       \_______________________________________________________________/
                         merge low to high
                                  |
                           effective ordinary config
                                  |
requirements sources              |  apply exact constraints
  MDM / enterprise / system ------+
                                  |
                       defaulting + runtime projections
                                  |
                      config/read.config shown to App

Each ordinary leaf -> origins
All ordinary layers -> optional layers[]
Requirements -> configRequirements/read
```

## 126. 本章结论

1. Codex 配置是带优先级的 layer stack，不是一份文件。
2. `cwd` 决定项目层，因此 effective config 是上下文相关的。
3. `config`、`origins`、`layers` 和 `requirements` 回答四个不同问题。
4. requirements 是约束链，不是最高优先级普通配置层。
5. 只允许写 active user layer；`filePath` 不是任意文件写权限。
6. `expectedVersion` 用内容指纹阻止丢失更新。
7. `null` 是清除，replace 与 upsert 表达不同编辑意图。
8. batch 先整体校验再持久化，适合一个逻辑设置的原子修改。
9. `okOverridden` 表示写成功但最终值被更高普通层覆盖。
10. 文件写入、缓存失效与 thread 热刷新是三个独立阶段。

## 127. 理解检查

### 问题 1

用户文件写 `model=A`，项目层写 `model=B`。最终是什么，origin 是谁？

答案：最终是 B，origin 是项目层。

### 问题 2

用户把 A 改成 C，写响应最可能是什么？

答案：文件写成功，但项目 B 继续获胜，因此是 `okOverridden`，并返回项目层与 effective B。

### 问题 3

若管理员用 exact requirement 固定该字段，结果仍是 `okOverridden` 吗？

答案：不是。写入在持久化前被拒绝，返回 `configRequirementReadonly`。

### 问题 4

为什么不能把 `config/read.config` 整份写回用户文件？

答案：它混合了多个普通层、requirements、默认化和运行时投影，不等于用户层原始内容。

### 问题 5

两个窗口都读 V1 后先后保存，怎样防止第二个静默覆盖第一个？

答案：都传 `expectedVersion=V1`；第一个写成 V2 后，第二个得到 version conflict，重新读取再合并。

### 问题 6

为什么 `reloadUserConfig=true` 不代表所有已有任务都采用新模型？

答案：模型等 session defaults 被显式排除；逐任务刷新也采用 best effort 并保留 session layers。

## 128. 源码检查点

建议按顺序搜索：

```bash
rg -n 'pub enum ConfigLayerSource|pub struct ConfigReadResponse|pub struct ConfigValueWriteParams' codex-rs/app-server-protocol/src/protocol/v2/config.rs
rg -n 'ConfigRead =>|ConfigValueWrite =>|ConfigBatchWrite =>' codex-rs/app-server-protocol/src/protocol/common.rs
rg -n 'pub\(crate\) async fn read|async fn apply_edits|fn parse_key_path' codex-rs/app-server/src/config_manager_service.rs
rg -n 'fn effective_config|fn origins|all_layers_high_to_low' codex-rs/config/src/state.rs
rg -n 'fn merge_toml_values|record_origins|version_for_toml' codex-rs/config/src
rg -n 'reload_user_config|session_defaults_only|handle_config_mutation' codex-rs/app-server/src/request_processors/config_processor.rs
```

## 129. 本章词汇表

| 英文 | 字面意思 | 在本章中的实际含义 |
|---|---|---|
| configuration / config | 配置 | 控制 Codex 行为的数据，不等同于单一文件 |
| layer | 层 | 一个独立配置来源及其内容、版本、状态 |
| layer stack | 层栈 | 按优先级组合的多个配置层 |
| source | 来源 | system、user、project、session 等层身份 |
| precedence | 优先次序 | 决定同一路径由哪一层覆盖的顺序 |
| effective config | 有效配置 | 所有普通层合并并进一步投影后实际采用的配置 |
| origin | 起源、来源 | 某个最终叶子值由哪个普通层获胜提供 |
| metadata | 元数据 | 描述层身份与版本、而非业务配置值的数据 |
| disabled reason | 禁用原因 | 某层被展示但不参与合并的原因 |
| profile | 配置档 | 覆盖基础用户配置的一组命名用户配置 |
| session flags | 会话标志 | 本次启动通过 `-c/--config` 注入的临时高优先级层 |
| managed | 受管理的 | 由设备、组织或企业控制，而非普通用户自由选择 |
| legacy | 遗留的 | 为旧行为兼容暂时保留的设计 |
| requirement | 要求、约束 | 管理员规定允许集合或精确值的独立政策数据 |
| exact requirement | 精确要求 | 把某路径固定为唯一值的管理约束 |
| allow-list | 允许列表 | 某字段可以选择的有限值集合 |
| key path | 键路径 | 用点分段定位嵌套配置字段的字符串 |
| segment | 片段 | key path 中的一个路径组成部分 |
| merge | 合并 | 把高层/新编辑叠加到低层/原值上的过程 |
| overlay | 覆盖层 | 合并时拥有更高优先级的输入 |
| replace | 替换 | 用新值替换目标路径值的编辑策略 |
| upsert | 更新或插入 | 存在则更新/合并，不存在则创建的策略 |
| sparse overlay | 稀疏覆盖 | 只构造到目标路径所需的最小嵌套 table |
| fingerprint | 指纹 | 从规范化层内容计算出的 SHA-256 version |
| canonical JSON | 规范 JSON | 对对象键稳定排序后用于一致 hashing 的 JSON |
| optimistic concurrency | 乐观并发 | 假设冲突少，写时用版本检查发现并发修改 |
| version conflict | 版本冲突 | 客户端依据的旧层版本已不是当前版本 |
| lost update | 丢失更新 | 后写者在不知情时覆盖先写者修改 |
| atomic / atomicity | 原子的 / 原子性 | 一组编辑整体成功或在持久化前整体失败 |
| no-op | 无操作 | 请求合法但因目标不存在或值未变而无需改文件 |
| readonly | 只读 | 当前 API 或管理约束不允许修改该目标 |
| overridden | 被覆盖 | 用户层值存在，但更高普通层提供最终值 |
| hot reload | 热刷新 | 不重启进程，向已加载任务应用可更新配置 |
| runtime projection | 运行时投影 | 在持久化层合并之外根据当前运行状态修正 API 结果 |
| cache invalidation | 缓存失效 | 配置改变后丢弃 plugin/skill 的旧派生结果 |
| best effort | 尽力而为 | 单个刷新失败被记录并跳过，不回滚整个已成功操作 |
| wire | 线上格式 | App 与 app-server 之间实际传输的 JSON 形状 |
| camelCase | 驼峰命名 | `expectedVersion` 这类 JSON 字段命名形式 |
| snake_case | 蛇形命名 | `approval_policy` 这类 TOML/Rust 字段形式 |
| flatten | 展平 | 把未显式列出的键收进 `additional` 并保持同层 JSON 形状 |
| serialization scope | 串行化作用域 | 让同一 config 资源的写入按规则协调执行的并发边界 |

