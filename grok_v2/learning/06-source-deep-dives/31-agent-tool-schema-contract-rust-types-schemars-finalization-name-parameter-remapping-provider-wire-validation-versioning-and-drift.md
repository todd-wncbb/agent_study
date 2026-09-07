# 源码精读 31：Agent Tool Schema Contract——Rust 类型、Schemars、Finalize、名称映射、Provider Wire、Validation、版本与漂移

> 源码基线：`ed6d543`
>
> 上一篇解释 Tool Call 怎样执行。本篇往执行前再追一层：模型为什么知道有哪些工具、每个参数叫什么、哪些字段必填，以及它输出的 JSON 为什么能被 Runtime 还原成同一个 Rust 类型。

---

## 1. 本篇解决什么问题

读完后应能回答：

- 一个 Rust `Tool` 如何变成模型可见的 Function Tool？
- `T::Args`、`ToolInput`、`ToolDefinition`、`ToolSpec` 分别是什么？
- Schemars 如何决定 `properties`、`required`、`default` 和 description？
- `serde(default)` 与 `schemars(default)` 为什么不是一回事？
- 为什么 schema 中的默认值不一定会直接写入 Runtime input？
- 工具名和参数名被重命名后，Runtime 怎样反向恢复 canonical JSON？
- 为什么参数映射只处理顶层字段？
- Description template 为什么必须在整个 toolset finalize 后才渲染？
- Tool 配置如何让 schema 随运行能力改变？
- behavior version 如何改变模型合同而不复制执行框架？
- MCP Tool 的远程 schema 如何接入本地 Registry？
- 三种 Provider 分别怎样携带 Function Tool schema？
- Function Tool strict 与 Structured Output strict 有何区别？
- Tool Choice 为何也是合同的一部分？
- 什么叫 schema/parser drift，应该怎样测试？

---

## 2. 先给出双向合同图

```text
                    广告方向

Rust Tool::Args
   │ schemars JsonSchema
   ▼
canonical input_schema
   │ ToolMetadata::versioned_definition
   │ + behavior version
   │ + effective params
   │ + description template
   │ + tool/parameter renames
   ▼
ToolDefinition
   ▼
ToolSpec in ConversationRequest
   ├─ Chat Completions function.parameters
   ├─ Responses function.parameters
   └─ Messages input_schema
   ▼
Model generates {wire parameter names}

                    执行方向

wire tool name + JSON args
   │ FinalizedTool lookup by client_name
   ▼
reverse_params: wire name → canonical name
   ▼
canonical JSON
   │ registration-time parse_input closure
   ▼
serde_json::from_value::<T::Args>
   ▼
typed T::Args → ToolInput → dispatch
```

核心不变量：

```text
模型看到的每一种合法输入，Runtime 都应该能解析；
Runtime 接受的每一个重要输入形状，Schema 都应该向模型准确公告。
```

---

## 3. 核心源码地图

| 领域 | 主要源码 |
| --- | --- |
| ToolDefinition 类型 | `xai-grok-tools/src/types/definition.rs` |
| ToolMetadata 合同 | `xai-grok-tools/src/types/tool_metadata.rs` |
| Registry 注册与 Finalize | `xai-grok-tools/src/registry/types.rs` |
| ToolInput enum | `xai-grok-tools/src/types/tool_io.rs` |
| Schema helper | `xai-grok-tools/src/types/schema.rs` |
| 名称/参数重映射 | `xai-grok-tools/src/util/remap.rs` |
| Description 渲染 | `xai-grok-tools/src/types/template_renderer.rs` |
| Tool contract version | `xai-grok-tools/src/versions.rs` |
| Tool taxonomy | `xai-grok-tools/src/tool_taxonomy.rs` |
| 外部兼容名称表 | `xai-grok-tools/src/types/claude_alias.rs` |
| Bridge 导出与解析 | `xai-grok-tools/src/bridge.rs` |
| 内部 ToolSpec | `xai-grok-sampling-types/src/conversation.rs` |
| Chat Completions 转换 | `xai-grok-sampling-types/src/conversation/chat_completions.rs` |
| Responses 转换 | `xai-grok-sampling-types/src/conversation/responses.rs` |
| Messages 转换 | `xai-grok-sampling-types/src/conversation/messages.rs` |

---

# 第一部分：先区分五种类型

## 4. `xai_tool_runtime::Tool`

具体工具实现这个 trait，给出关联类型：

```text
T::Args
T::Output
```

并实现真正的 `run()`。

它是执行能力，不等于模型广告。

## 5. `ToolMetadata`

Grok Build 在通用 Tool trait 外增加 metadata：

- ToolKind；
- ToolNamespace；
- description template；
- requirements；
- read-only 语义；
- versioned definition；
- reminders 与 notifications。

## 6. `T::Args`

每个工具自己的输入 struct，例如：

```rust
pub struct BashToolInput {
    pub command: String,
    pub timeout: Option<u64>,
    pub description: String,
    pub is_background: bool,
}
```

这是 schema producer 和 Runtime parser 应共同遵守的 canonical contract。

## 7. `ToolInput`

`ToolInput` 是所有 built-in input 的总和 enum：

```text
ReadFile(ReadFileInput)
Bash(BashToolInput)
SearchReplace(SearchReplaceInput)
...
Dynamic(Value)
```

注册时要求 `T::Args: Into<ToolInput>`，因此 typed args 能进入统一 Session 状态机。

## 8. `ToolDefinition`

模型 API 侧最小定义是：

```rust
ToolDefinition {
    kind: Function,
    function: FunctionTool {
        name,
        description,
        parameters,
    }
}
```

`parameters` 是 JSON Schema `Value`。

## 9. `ToolSpec`

Sampling 层使用 Provider-neutral `ToolSpec`：

```text
name + optional description + parameters
```

`From<ToolDefinition>` 去掉外层 `type:function` 包装，使 ConversationRequest 与具体 API 解耦。

## 10. `ConversationRequest.tools`

这是本次 Sampling 真正公告给模型的 client-side tools。

Registry 里存在某个工具，不代表每个请求都一定携带它；Agent 配置、能力过滤、模式和结构化输出策略都可能影响最终集合。

## 11. 五种类型的关系

| 类型 | 主要读者 | 主要职责 |
| --- | --- | --- |
| `Tool` | Runtime | 执行 typed input |
| `T::Args` | Serde/Schemars | 输入真值类型 |
| `ToolInput` | Session | 统一分派和权限语义 |
| `ToolDefinition` | Registry/API adapter | 完整 Function 声明 |
| `ToolSpec` | Sampling | Provider-neutral 请求字段 |

---

# 第二部分：注册时同时捕获 Schema 与 Parser

## 12. `register_with_params<T, P>` 的泛型约束

关键约束包括：

```rust
T::Args: DeserializeOwned + JsonSchema + Into<ToolInput>
T::Output: Serialize + DeserializeOwned + Into<ToolOutput>
```

同一个 `T::Args` 同时支持广告和解析。

## 13. Schema closure 与 parser closure 同源

注册时保存：

```text
input_schema = generate_schema::<T::Args>()
parse_input  = |json| from_value::<T::Args>(json)?.into()
```

这比手写一份 schema、另写一个 parser 更不容易漂移。

## 14. 但“同源”不等于“绝对同步”

以下机制仍可能让二者不一致：

- 自定义 Schemars attribute；
- 自定义 Serde deserializer；
- versioned definition 修改导出 schema；
- 参数重命名遗漏反向映射；
- 动态 MCP schema override；
- description 宣称了代码没有的规则。

## 15. `ToolEntry` 是预 Finalize 状态

Builder 中每个 `ToolEntry` 保存：

- namespace/id/kind；
- requirements；
- default params；
- canonical input schema；
- metadata；
- params validation/application closures；
- input parse closure；
- local registry registration closure。

## 16. 为什么先 type erase

Registry 同时容纳多种 `T`，无法把不同具体泛型直接放进一个 Vec。

它在注册时捕获闭包和 trait object，Finalize/dispatch 时只操作统一接口。

## 17. `P` 不是 Tool Call 参数

`P` 是工具的 Session/config params，例如 Bash 是否允许 background、输出上限等。

`T::Args` 是模型每次调用发送的参数。

两者不要混淆：

```text
P       → 配置工具能力和广告
T::Args → 一次具体调用
```

## 18. Params 为什么也会影响 Schema

某项能力被配置关闭后，继续向模型公告对应字段会诱导无效调用。

因此 `versioned_definition(... effective_params ...)` 可以输出配置专属 schema。

---

# 第三部分：`generate_schema` 做了什么

## 19. 使用 JSON Schema Draft-07

`generate_schema<T>` 构造：

```rust
SchemaSettings::draft07()
```

Provider 接收的并不是任意 Rust 元数据，而是标准 JSON Schema 文档。

## 20. inline subschemas

设置 `inline_subschemas = true`。

这尽量把子结构内联到使用点，减少 `$ref`/definitions 对模型和不同 Provider 实现的兼容负担。

## 21. 删除根 `title`

Schemars 常把 Rust struct name 放在根 title。

Registry 会删除它，避免参数 schema 泄露 canonical 类型名，尤其是在工具已经被改名时。

## 22. 删除根 `description`

根 description 也被删除。

工具整体说明由 `FunctionTool.description` 单独控制；参数 schema 只保留 property descriptions。

## 23. 保留 `$schema`

源码测试明确要求 Draft 声明仍存在。

清理不是把 Schema 压成最小 object，而是去掉会与 client-facing identity 冲突的根展示信息。

## 24. object 必有 `properties`

若根 type 为 object 且 Schemars 没生成 properties，代码补空 object。

这样零参数工具仍有稳定形状：

```json
{"type":"object","properties":{},"required":[]}
```

## 25. object 必有 `required`

同理，缺失 required 时补空数组。

Provider、测试和消费端不必处理“字段不存在”和“空集合”两种等价表示。

## 26. 为什么不删除所有 Schema 噪声

字段 description、enum、default、items、minimum 等会真实帮助模型构造参数。

清理只针对根 identity boilerplate。

---

# 第四部分：Serde 与 Schemars 是两套语义

## 27. Serde 决定 Runtime 接受什么

`serde_json::from_value::<T::Args>` 读取：

- `#[serde(rename = ...)]`
- `#[serde(default)]`
- `#[serde(deny_unknown_fields)]`
- custom `deserialize_with`
- enum tag/alias 等。

## 28. Schemars 决定模型看到什么

`JsonSchema` 和 `#[schemars(...)]` 决定：

- JSON type；
- properties；
- required；
- defaults；
- descriptions；
- enum/oneOf/items；
- additionalProperties。

## 29. 两套 derive 会互相参考，但不是同一执行器

Schemars 通常理解常见 Serde rename/default 标记，但 custom deserializer 的全部宽容行为不一定能从 schema 自动表达。

所以必须测试 wire schema 与真实 parser。

## 30. 必填字段如何产生

没有 Serde default 的普通字段通常进入 `required`。

例如 Bash：

```text
command     required
description required
timeout     optional
is_background optional/default false
```

## 31. `Option<T>` 通常表示可缺省

`timeout: Option<u64>` 配合 `serde(default)`，遗漏时 Runtime 得到 `None`。

Schema 可以同时公告一个 UI/模型默认值，但这不代表反序列化后一定得到 `Some(default)`。

## 32. Schema default 与 Runtime default 的关键区别

Bash timeout 的注释明确区分：

```text
Schema advertised default: 120000
Serde omitted value: None
```

后续 Runtime 根据 foreground/background policy 解释 `None`。

## 33. 为什么这种区别是必要的

若 Serde 直接把遗漏写成 `Some(120000)`，background command 的“未指定即不设相同前台上限”语义可能丢失。

Schema default 是给模型的建议，不必等于存储层 materialization。

## 34. ReadFile offset 也采用 schema-only default

Schema 公告 offset 默认 1；Runtime 保留 `Option<i64>`，执行时 `unwrap_or(1)`。

这保住了“用户明确写 1”和“用户省略”的可区分性，直到真正需要求值。

## 35. 自定义宽容数字解析

Bash timeout 使用 `deserialize_lenient_u64`，可接受：

```json
120000
"120000"
```

Schema 仍公告 integer，因为希望模型生成规范形状；parser 对常见模型偏差更宽容。

## 36. 自定义宽容布尔解析

`is_background`、`replace_all` 等使用 lenient bool deserializer。

同样体现：

```text
Schema = 推荐/合同形状
Parser = 合同形状 + 有界兼容
```

## 37. `deny_unknown_fields`

某些 input/config struct 使用该属性，未知字段会直接报错。

另一些为了前向兼容故意忽略未知字段。严格度是每个类型的显式设计选择，不是整个 Tool Runtime 的统一开关。

## 38. `additionalProperties` 与 deny_unknown 的对应

理想情况下：

```text
deny_unknown_fields ↔ additionalProperties: false
动态任意对象       ↔ additionalProperties: true
```

但最终仍应检查生成 JSON，而不能只凭直觉推断 derive 行为。

## 39. UseTool 的动态内部参数

`UseToolInput.tool_input` 使用自定义 schema：

```json
{"type":"object","additionalProperties":true}
```

因为真实字段由运行时搜索到的 MCP Tool 决定，静态 Rust struct 无法提前列出。

## 40. 外层严格、内层动态

UseTool 外层固定需要：

- `tool_name`
- `tool_input`

但 `tool_input` 内部开放任意 properties。

这是一种有边界的动态类型，而不是整个调用都退化成 raw string。

---

# 第五部分：Finalize 才生成真正对外合同

## 41. Builder schema 只是 canonical 原料

`ToolEntry.input_schema` 来自 Rust type，但尚未考虑当前 Session 的：

- enabled toolset；
- name override；
- parameter rename；
- behavior preset；
- effective params；
- description renderer context；
- truncation config。

## 42. Finalize 先验证配置

Finalize 会确保：

- 引用的 Tool ID 存在；
- requirements 满足；
- params 合法；
- client-facing names 不重复；
- behavior version 可解析；
- parameter overrides 有意义。

## 43. Fully-qualified ID 与 client name

内部注册 ID 类似：

```text
GrokBuild:read_file
```

模型看到的 client name 通常是：

```text
read_file
```

二者服务不同命名空间。

## 44. `name_override`

ToolConfig 可将 client-facing name 改为其他字符串。

这允许：

- 兼容某种 harness；
- 避免两个 namespace 下同名工具冲突；
- 做受控实验或随机化；
- 适配不同 Agent persona。

## 45. 重名必须在 Finalize 拒绝

若两个 enabled tools 最终解析到相同 client name，模型无法区分，Registry lookup 也会歧义。

代码返回 `duplicate_client_name` RequirementError，而不是后注册覆盖前注册。

## 46. `params_name_overrides`

配置保存 canonical → client-facing 映射：

```text
old_string → find
new_string → replace_with
```

它必须同时作用于广告与执行两个方向。

## 47. 广告方向重命名

`remap_schema_properties`：

- 修改根 `properties` 的 key；
- 修改根 `required` 中的字符串；
- 未映射字段保持 canonical name。

## 48. 执行方向反向映射

Finalize 构造：

```text
reverse_params:
find         → old_string
replace_with → new_string
```

`try_parse` 在 Serde 前调用 `remap_json_keys`。

## 49. 为什么 required 也必须改名

只改 properties 而不改 required，会生成自相矛盾 schema：

```json
properties: {"find": ...}
required: ["old_string"]
```

模型和 strict validator 都会得到错误合同。

## 50. 映射只处理顶层

源码注释明确：nested objects 不递归重命名。

这是当前合同范围，不应假设配置能随机化任意深度字段。

## 51. 顶层限制的好处

顶层映射简单、可逆，避免：

- 两条嵌套路径冲突；
- array item schema 重写；
- `$ref` target 修改；
- 动态 map key 被误当字段名。

## 52. 顶层限制的风险

若未来配置试图重命名 nested property，广告或 Runtime 至少一侧不会按预期工作。

应在配置验证层明确拒绝不支持的路径语法。

## 53. key collision

两个 canonical fields 若映射到同一 client name，会在 JSON object 中覆盖。

`reverse_map` 有 debug assertion；生产配置还应由 Finalize validation 提前阻止。

## 54. 未映射未知字段原样通过

`remap_json_keys` 对不在 reverse map 的 key 保持原名。

之后是否接受由具体 `T::Args` 的 Serde strictness 决定。

---

# 第六部分：Description 也是合同

## 55. Description 不是装饰文字

Schema 告诉模型“形状”，description 告诉模型：

- 什么时候调用；
- 字段如何组合；
- 哪些副作用危险；
- 返回值怎样继续使用；
- 与其他工具怎样配合。

## 56. 原始 description 是 MiniJinja template

ToolMetadata 可以引用：

```text
${{ tools.by_kind.search }}
${{ params.edit.old_string }}
${{ system_reminders_enabled }}
```

## 57. 为什么注册时不能渲染

注册单个工具时还不知道最终 enabled toolset、client names 和 parameter aliases。

只有 Finalize 后 TemplateRenderer 才有完整映射。

## 58. Description override

ToolConfig 可完全替换默认 description template。

替换后仍经过 renderer；因此 override 也能使用当前 toolset placeholder。

## 59. 渲染失败的降级

默认 `versioned_definition` 在 renderer 失败时调用 marker stripping fallback。

模型不会看到原始 `${{ ... }}` 模板垃圾。

## 60. Property descriptions 也会二次渲染

Finalize 调用：

```text
renderer.render_schema_descriptions(parameters)
```

它递归进入 nested property/item schema，替换字段说明中的工具名和参数名。

## 61. 为什么字段 description 必须重渲染

SearchReplace 的 `new_string` description 引用 `old_string`。

若 schema key 已改成 `find`，说明仍写 old_string，会让模型困惑甚至生成错误字段。

## 62. TruncationConfig 也进入合同

Finalize 会把输出字节限制、最大等待时间等当前配置插入：

- function description；
- parameter descriptions/schema。

模型看到的是本 Session 的真实限制，而非编译期常量假象。

## 63. Description 与 Runtime 必须一致

SearchReplace 根据 effective param 决定是否保留“空 old_string 不覆盖已有非空文件”的句子。

若 Runtime 配置关闭该 guard，description 也删除该承诺。

---

# 第七部分：`versioned_definition` 是合同定制点

## 64. 默认实现

默认 `ToolMetadata::versioned_definition`：

1. 选择 override 或默认 description；
2. 用 TemplateRenderer 渲染；
3. 按 param map 重命名 schema；
4. 构造 `ToolDefinition::function`。

## 65. 工具可以 override

Bash、SearchReplace、WebFetch、TaskOutput 等工具有自定义实现。

原因通常是 schema/description 会随 params 或 contract version 改变。

## 66. Bash 的 params-aware schema

Bash 根据 effective params 生成 exported input schema。

例如 background 能力关闭时，不应继续鼓励模型发送 `is_background`。

## 67. Bash 为什么显式选择 timeout 参数名

源码注释强调只使用该 ToolConfig 自己的 param map，不能误用同 ToolKind 下另一个 Execute 工具的 alias。

Kind 共享行为类别，不代表共享参数合同。

## 68. SearchReplace 的 params-aware description

它根据 `empty_old_string_does_not_override` 决定是否公告 guard sentence。

这是行为配置影响自然语言合同的实例。

## 69. Contract version

Finalize 使用：

```text
behavior preset
+ per-tool behavior_version override
→ concrete contract_version
```

版本解析集中在 `versions.rs`。

## 70. 为什么叫 contract version

它可能影响：

- 模型看到的 schema；
- description；
- Runtime behavior；
- 兼容旧 harness 的字段语义。

这比单纯“实现版本”更贴近模型/API 边界。

## 71. 未管理工具不能随便指定版本

只有版本表登记的工具能接受 behavior override。

否则配置错误应在 Finalize 暴露，而不是静默忽略一个看似生效的版本号。

## 72. `FinalizedTool.contract_version`

解析后的具体版本存入 FinalizedTool，dispatch 时也可进入 ToolCallContext。

广告与执行因此能读取同一个版本选择。

## 73. 版本迁移的正确方式

迁移时应保持：

```text
old version schema ↔ old version parser/behavior
new version schema ↔ new version parser/behavior
```

不能只更新 description 或只更新 Runtime。

---

# 第八部分：FinalizedTool 保留两份 Schema

## 74. canonical `input_schema`

FinalizedTool 保存从 Rust type 生成的 canonical schema。

它用于 requirement checks 和 TemplateRenderer 的参数名暴露。

## 75. exported `definition.function.parameters`

另一份是模型实际看到的 schema，可能已经：

- 改名；
- 隐藏字段；
- 按版本调整；
- 渲染 description；
- 注入当前限制。

## 76. 为什么不能只保留 exported schema

内部 requirement 以 canonical param name 表达。

如果内部逻辑读取已随机化名称，会使配置、提醒器和 Rust 代码都依赖某次 client 展示选择。

## 77. 为什么不能只保留 canonical schema

模型必须看到与 client-facing name、参数 aliases 和当前能力完全一致的合同。

两份 schema 是两种坐标系，不是无意义重复。

---

# 第九部分：Tool Definition 怎样进入请求

## 78. `ToolBridge::tool_definitions`

Bridge 直接克隆 FinalizedToolset 中预构建的 definitions。

每轮无需重新执行 Schemars 和所有模板渲染。

## 79. Builtins-only 快照

`tool_definitions_builtins_only` 过滤 client name 中包含 MCP delimiter 的动态工具。

它用于只需要稳定原生工具前缀的场景。

## 80. ToolDefinition → ToolSpec

构建 ConversationRequest 时，外层 `type:function` 被归一为 Provider-neutral ToolSpec。

name、description、parameters 不丢失。

## 81. Tool Choice

`ConversationToolChoice` 有：

- Auto
- None
- Required
- Function(name)

Schema 决定“怎样调用”；Tool Choice 决定“是否/必须调用谁”。

## 82. 空 tools 时不发 tool choice

Chat Completions converter 只有在 tools 非空时才设置 tool_choice。

否则某些 Provider 会因“指定工具策略却无工具定义”拒绝请求。

## 83. Function(name) 依赖 client-facing name

强制特定工具时必须使用最终 client name，而不是 fully-qualified registry ID 或 canonical default name。

否则 schema 已公告 A，tool choice 却要求不存在的 B。

---

# 第十部分：三种 Provider 的 Function Tool Wire

## 84. Chat Completions

ToolSpec 重新包成：

```json
{
  "type": "function",
  "function": {
    "name": "...",
    "description": "...",
    "parameters": {...}
  }
}
```

## 85. Responses

转换成 Responses `Tool::Function`：

```text
name
description
parameters: Some(schema)
strict: None
```

## 86. Messages

转换成 `ToolParam`：

```text
name
description
input_schema
```

字段名不同，但 schema Value 相同。

## 87. Provider adapter 不应修改业务 Schema

参数 rename、版本、description render 都应在 Registry Finalize 完成。

Provider adapter 只负责 wire shape，避免三个后端各自产生不同工具合同。

## 88. Responses 中 hosted tool 冲突

如果 client function name 与 hosted `web_search`/`x_search` 相同，Responses converter 丢弃 function tool，并记录 warning。

因为同时发送同名 hosted 与 function tool 会被 Provider 拒绝。

## 89. hosted tool 优先的含义

同名时执行位置从本地 Runtime 变成 Provider backend。

这是显式的 request construction policy，不能只看 Registry 判断本轮实际能力。

---

# 第十一部分：不要混淆两种 Strict

## 90. Function Tool strict

它表示 Provider 是否严格约束模型生成的函数参数符合该 Tool schema。

在当前 Responses function-tool conversion 中：

```text
strict: None
```

## 91. Structured Output strict

`ConversationRequest.json_schema` 是最终回答的结构化输出 schema，不是某个普通 Tool 的参数 schema。

Chat Completions 与 Responses 转换会设置：

```text
strict: true
```

## 92. 两者为什么不同

普通 Tool Runtime 已有 typed parser、错误 Tool Result 和 resampling recovery。

Structured Output 是最终交付合同，需要 Provider 尽可能直接保证格式。

## 93. Messages 的特殊限制

源码注释说明：Messages wire schema 会抑制 tool calls。

Agent 因此在需要“先用工具、最后给结构化结果”时，通过合成 `StructuredOutput` Tool 协调，而不是无条件依赖 native output schema。

## 94. `strict: None` 不等于没有校验

即使 Provider 不做 strict generation，Runtime 仍执行：

```text
JSON parse
→ reverse param mapping
→ serde_json::from_value::<T::Args>
```

失败会成为可纠正的 Tool Result。

## 95. Provider strict 也不能替代 Runtime parser

Provider 可能版本不同、兼容字段有限，历史 Tool Call 也可能来自别的后端。

本地执行前必须始终以 typed parser 为最终边界。

---

# 第十二部分：MCP Tool 是动态合同

## 96. MCP schema 不来自本地 Rust Args

远端 MCP server 在运行时提供工具名、说明和 input schema。

本地 binary 不可能为每个第三方工具预先定义 Rust struct。

## 97. `register_mcp_tools`

Bridge 将 MCP-qualified name、passthrough Tool 和 optional input schema 交给 FinalizedToolset 动态注册。

## 98. schema override 优先

动态 `register_tool` 使用：

```text
input_schema_override.unwrap_or_else(generate_schema::<T::Args>)
```

有远端 schema 时保真采用；没有时才从 passthrough Args 推导。

## 99. MCP parser 为什么是 Dynamic

动态条目的 parse closure 不反序列化成第三方 Rust struct，而是构造：

```rust
ToolInput::MCPTool {
    tool_name,
    tool_input: json,
}
```

真实 schema validation/执行由 MCP 边界继续完成。

## 100. 动态工具没有 param rename

源码注释明确：动态注册路径不支持 name override 或 parameter remapping。

模型看到和发送的字段必须与 MCP server schema 一致。

## 101. 动态重名也拒绝

若 client name 已存在，注册返回 `Tool already registered`。

不能让新 MCP Tool 覆盖 built-in 或另一个 server 的可执行入口。

## 102. MCP schema 可信度问题

本地 Registry 能保证传递远端 schema，却无法自动证明远端实现完全遵守它。

因此 MCP failure 仍需通过 Tool Result 回填，而不是假设 schema 足以消除运行错误。

---

# 第十三部分：外部“别名”有三种，不要混为一谈

## 103. ToolConfig `name_override`

它真正改变模型 Function name 与 Registry client lookup key。

这是执行协议层 rename。

## 104. `params_name_overrides`

它改变模型参数 key，并在 dispatch 前反向恢复。

这是输入协议层 rename。

## 105. `claude_alias` compatibility table

`types/claude_alias.rs` 映射 Read、Bash、Edit、Task 等外部 settings/hook 术语到 Grok ToolKind 或 tool names。

它主要服务 allowlist resolution 和 Hook matcher。

## 106. Compatibility alias 不一定是可调用别名

例如外部配置中的 `Bash` 可以解析为 Execute kind，但模型实际 Function name 仍可能是 `run_terminal_command`。

不要因为 alias table 有某名字，就认为 Registry 接受模型用该字符串调用。

## 107. 一对多映射

Claude `Read` 可对应 `read_file`、`hashline_read`；`Edit` 可对应多个实现。

这说明 compatibility term 表示能力类别/匹配集合，不是唯一 dispatch target。

## 108. match-only 行

某些条目可用于 Hook matcher，却不允许解析为 capability allowlist。

例如进入/退出 Plan 必须成对配置，不能把单个名称简单映射成一个独立 kind。

## 109. 单表防漂移

同一 `CLAUDE_TOOLS` 表同时支持正向、反向和 kind lookup，并有唯一性/活性测试。

比维护多份名字表更不容易出现 Hook 与 Agent Builder 理解不一致。

---

# 第十四部分：Schema 与 Parser 怎样真正对接

## 110. lookup by client name

`FinalizedToolset::try_parse` 先在 tools Vec 中查找：

```text
t.client_name == tool_name
```

因此模型输出必须匹配本轮公告的最终名称。

## 111. 找不到工具

返回 typed not-found ToolError，Session 再生成配对 Tool Result。

这也是名称合同漂移最直接的症状。

## 112. 复制 reverse map 与 parse closure

代码在短暂 RwLock read guard 内只克隆必要对象，然后释放锁。

反向 remap 与 parsing 不持有 Registry read lock。

## 113. canonicalization

输入是 object 时，顶层 wire keys 按 reverse map 改回 canonical keys。

非 object 原样通过，随后通常由 typed parser 拒绝。

## 114. registration-time parser

最终调用注册时捕获的：

```rust
serde_json::from_value::<T::Args>(canonical_json)
```

成功后 `Into<ToolInput>`。

## 115. try_parse 与 call 使用同样 remap

Registry 在预解析和真实 dispatch 路径都应用 reverse params。

否则 Permission 看到的 typed input与实际执行 input可能不同，构成安全问题。

## 116. Inner dispatch 也要 remap

`call_raw` 等 meta-dispatch 路径同样取得目标 FinalizedTool 的 reverse params。

绕过外层 Bridge 不代表绕过目标工具的输入合同。

## 117. 参数名资源

Dispatch context 还注入 `InvokingToolParamNames::from_reverse_params`。

工具结果/reminder 可以用本次 client-facing 参数名解释错误，避免模型只看到 canonical 内部名。

---

# 第十五部分：最常见的合同漂移

## 118. Schema 新增 required，Rust 仍允许缺省

模型被迫发送字段，但 Runtime 实际不需要，增加 token 和失败面。

## 119. Rust 新增必填字段，Schema 没公告

模型按旧 schema 调用，每次都在 typed parse 失败，形成纠错循环。

## 120. Schema 改名，reverse map 没改

模型发送新名称，Serde 只认识 canonical 旧名称。

这是参数随机化最典型的镜像缺失。

## 121. 只改 properties，忘记 required

Schema 内部自相矛盾，strict Provider 可能在请求阶段拒绝整个定义。

## 122. Description 引用旧参数名

JSON shape technically valid，但模型受自然语言引导生成不存在的 key。

因此 schema description renderer 与 key remapper同样重要。

## 123. Runtime 宽容但 Schema 过窄

例如 parser 接受数字字符串，schema 只公告 integer。

这通常是有意的：鼓励规范输出，同时兼容偶发偏差；文档和测试应明确它是单向宽容。

## 124. Schema 宽松但 Runtime 严格

这是更危险的方向：Provider 允许模型生成某形状，本地却必然拒绝。

应尽量避免，除非拒绝本身是明确设计的二阶段 validation。

## 125. Default 漂移

Description 写 120 秒、Schema default 写 60 秒、Runtime fallback 用 30 秒，会使模型行为和实际执行完全不同。

默认值需要至少覆盖三处一致性测试。

## 126. Config 改行为但没改广告

关闭 background，却仍公告 `is_background`；关闭覆盖保护，却仍声称绝不覆盖。

这类 drift 来自 effective params 未进入 versioned definition。

## 127. Provider adapter 单独改 schema

如果只在 Responses 路径注入字段，Agent 在 Chat Completions 与 Messages 上会拥有不同合同。

业务定制应尽量前移到 Finalize。

## 128. MCP server 自报 schema 漂移

远端 schema 与实现不一致，本地无法靠 Rust derive 发现。

需要 MCP contract test 或实际 tool call fixture。

---

# 第十六部分：四个源码 Walkthrough

## 129. Walkthrough A：ReadFile rename

Rust 字段：

```rust
#[serde(rename = "target_file")]
pub path: String
```

链路：

1. Schemars 生成 `target_file` property；
2. Schema 公告给模型；
3. 模型发送 `{"target_file":"src/lib.rs"}`；
4. Serde rename 把它读入 Rust `path` 字段；
5. Session AccessKind/dispatch 使用 `input.path`。

这里 wire/canonical JSON 名是 `target_file`，Rust 内存字段名是 `path`。

## 130. Walkthrough B：SearchReplace 参数随机化

假设配置：

```text
old_string → find
new_string → replace_with
```

广告方向：

```text
properties.old_string → properties.find
required.old_string   → required.find
description 中引用也渲染为 find
```

执行方向：

```text
{"find":"a","replace_with":"b"}
→ {"old_string":"a","new_string":"b"}
→ SearchReplaceInput
```

## 131. Walkthrough C：Bash schema-only timeout default

1. Schema 公告 timeout integer，default 120000；
2. 模型可能省略 timeout；
3. Serde 得到 `None`，而非 `Some(120000)`；
4. foreground resolution 应用产品默认；
5. background resolution可以保留不同语义；
6. 模型若发送 `"120000"`，lenient parser 仍接受。

## 132. Walkthrough D：动态 MCP Tool

1. MCP server 返回 name、description、inputSchema；
2. Session 生成 qualified client name；
3. `register_tool` 使用远端 schema override；
4. ToolDefinition 加入 live toolset；
5. 模型按远端 schema生成 JSON；
6.本地 parse为 `MCPToolInput { tool_name, tool_input: Value }`；
7. MCP adapter把 Value 发回 server；
8. server负责最终业务 validation。

---

# 第十七部分：如何测试 Tool Contract

## 133. Schema snapshot 测试

对每个关键工具断言：

- property names；
- required 集合；
- type/enum/items；
- additionalProperties；
- defaults；
- descriptions 不含未渲染模板。

## 134. Schema-to-parser 正向测试

构造一份符合 exported schema 的最小 JSON，走真实：

```text
FinalizedToolset::try_parse(client_name, json)
```

不能只直接 `from_value::<T::Args>`，否则绕过 rename。

## 135. Parser-to-schema 反向测试

枚举 Runtime 有意接受的兼容输入，确认：

- 是否应该写进 schema；
- 若不写，是否明确为容错而非主合同；
- 不会产生含糊语义。

## 136. Rename round-trip 测试

至少验证：

```text
canonical schema
→ exported renamed schema
→ model-facing JSON
→ reverse remap
→ typed input
```

Registry 已有 `params_name_overrides` 的端到端测试，应作为新增 alias 的模板。

## 137. Duplicate name 测试

同时启用两个默认同名工具时，Finalize 必须失败；给其中一个 `name_override` 后应成功。

这固定了“拒绝歧义，不静默覆盖”的规则。

## 138. Description render 测试

断言 Function description 与 nested property descriptions：

- 不含 `${{` 或 `{%`；
- 引用最终 tool name；
- 引用最终 parameter name；
- 包含当前 limit；
- 与 effective params 行为一致。

## 139. Provider parity 测试

同一个 ToolSpec 分别转换到三种 API，抽取 name/description/schema 后做语义等价比较。

Wire 外壳可以不同，合同内容不应漂移。

## 140. Strict 请求测试

分别断言：

```text
ordinary Responses Function Tool strict = None
Responses Structured Output strict = true
Chat response_format strict = true
Messages schema routing符合当前能力策略
```

## 141. Version matrix 测试

每个受管理工具至少覆盖：

- current preset；
- legacy preset；
- per-tool override；
- unmanaged tool错误指定版本；
- 广告和 Runtime 都拿到同一 concrete version。

## 142. MCP fixture 测试

用固定远端 schema 注册动态工具，验证：

- definition 原样携带关键约束；
- duplicate name 被拒绝；
- args 以 Dynamic Value 保真；
- invalid server result 形成 Tool Result error。

---

# 第十八部分：现有测试给出的证据

## 143. generate_schema 根清理

`generate_schema_strips_root_title_and_description` 固定：

- title 删除；
- root description 删除；
- `$schema` 保留；
- properties 保留。

## 144. remap 单元测试

`util/remap.rs` 覆盖：

- 普通 key rename；
- 未映射 passthrough；
- 空 map no-op；
- 非 object no-op；
- required rename；
- reverse map。

## 145. duplicate client name

Registry 测试覆盖 config validation 和 finalize 两个入口。

只在前置 validator 检查还不够，Finalize 自身必须守住不变量。

## 146. alias disambiguation

测试证明两个默认名为 `read_file` 的不同实现，可通过 `name_override = codex_read_file` 同时启用。

## 147. UseTool dynamic object

测试断言 `tool_input` schema 的 `additionalProperties == true`。

这防止 Schemars 将任意 MCP args 错误压成空对象。

## 148. 三 Provider structured schema

Sampling Types 测试固定 JSON Schema 在 Chat、Responses、Messages 三条 wire path 的位置和 strict 行为。

---

# 第十九部分：源码阅读中的常见误解

## 149. 误解：ToolDefinition 就是 Rust Tool

错误。一个是模型广告数据，另一个是可执行实现。

## 150. 误解：Schemars 生成后就直接发给模型

错误。还要经过 Finalize、版本、params、rename、模板和 truncation processing。

## 151. 误解：Schema default 会自动写入 Rust 字段

错误。Runtime default 由 Serde 和执行逻辑决定。

## 152. 误解：`strict: None` 表示参数不校验

错误。本地 typed parse 始终存在。

## 153. 误解：外部 Bash alias 等于模型可调用 Bash

错误。Compatibility alias、ToolKind 和 client Function name 是不同命名层。

## 154. 误解：参数 rename 只是改 description

错误。properties、required、description 引用和 Runtime reverse map 都必须改变。

## 155. 误解：所有未知字段策略相同

错误。是否 `deny_unknown_fields` 由具体 input/config type 决定。

## 156. 误解：MCP schema 也有本地 typed struct

错误。动态 MCP args 通常保留为 JSON Value。

## 157. 误解：Schema 越严格越好

错误。对 `use_tool.tool_input` 这类运行时动态对象，错误的严格 schema 会屏蔽所有真实字段。

## 158. 误解：Description 不影响正确性

错误。模型选择工具和组合字段主要依赖自然语言语义。

---

# 第二十部分：维护与 Review 清单

## 159. 修改 Tool Args 时

检查：

- Serde required/default/rename；
- Schemars property/type/default/description；
- ToolInput variant conversion；
- 最小合法 JSON；
- 旧历史兼容。

## 160. 修改工具配置时

检查 effective params 是否同步改变：

- Runtime behavior；
- exported schema；
- function description；
- requirements；
- reminders。

## 161. 修改参数名时

检查：

- properties；
- required；
- description references；
- reverse params；
- error/reminder 使用的展示名；
- traces 中是否需要 canonical 与 client 双字段。

## 162. 新增 Provider 时

确保 Provider adapter：

- 不丢 description；
- 不丢 parameters；
- 正确映射 Tool Choice；
- 明确 strict 支持；
- 处理 hosted/function name collision；
- 保留 call ID 与 Tool Result pairing。

## 163. 新增动态 Tool 来源时

明确：

- 谁提供 schema；
- 谁做最终 validation；
- 名称如何 namespace；
- duplicate 怎样拒绝；
- 是否允许 rename；
- schema 更新何时对当前 Session 生效。

## 164. 修改 contract version 时

不要只做 snapshot diff。还要用每个版本的 advertised minimal input 跑真实 parser/dispatch。

## 165. Review 的一句核心问题

```text
如果模型严格按照本轮实际收到的 name、description 和 parameters 生成调用，
这段 Runtime 是否一定能把它还原成预期的 typed input？
```

---

## 166. 一页纸总结

```text
1. Tool::Args 同时是 Schemars schema 和 Serde parser 的源头。
2. 注册时捕获 canonical schema 与 parse_input closure。
3. generate_schema 使用 Draft-07、内联子 schema，并清理根 title/description。
4. Serde 决定 Runtime 接受什么，Schemars 决定模型看到什么。
5. schema default 不一定 materialize 成 Runtime value。
6. Finalize 把配置、版本、能力、模板和限制编译进最终 ToolDefinition。
7. tool name 与 parameter name 都可改，但执行前必须反向恢复 canonical keys。
8. 参数 remap 当前只支持顶层 properties/required。
9. canonical input_schema 与 exported parameters 是两种坐标系。
10. 三 Provider 只改变 wire 外壳，不应改变业务合同。
11. 普通 Function Tool strict 与 Structured Output strict 是两件事。
12. MCP 使用远端 schema + Dynamic JSON，而非静态 Rust Args。
13. Compatibility alias 不一定是可调用 Function name。
14. Schema/parser drift 必须靠 round-trip、provider parity 和 version matrix 测试发现。
```

---

## 167. 本篇术语表

| 名词 | 白话解释 | 在本篇中的精确含义 |
| --- | --- | --- |
| Tool contract | 工具合同 | 模型看到的 name、description、parameters 与 Runtime 接受行为的组合 |
| schema | 模式 | 描述 JSON 输入结构、类型、必填项和约束的 JSON Schema |
| JSON Schema | JSON 结构规范 | Provider Function Tool parameters/input_schema 使用的标准描述格式 |
| Draft-07 | Schema 草案版本 | `generate_schema` 采用的 JSON Schema 版本 |
| Schemars | Rust Schema 库 | 从 `JsonSchema` Rust 类型派生 JSON Schema 的 crate |
| Serde | Rust 序列化库 | 将 JSON Value 反序列化成 `T::Args` 的最终 Runtime parser |
| `T::Args` | 工具参数类型 | 某个 Tool 一次调用的具体 Rust input struct |
| `T::Output` | 工具输出类型 | 某个 Tool 执行返回的具体 Rust output type |
| ToolInput | 统一输入 enum | 包装所有 built-in typed inputs 与 Dynamic/MCP input |
| ToolDefinition | 工具定义 | 含 type、function name、description、parameters 的模型 API 类型 |
| FunctionTool | 函数工具 | ToolDefinition 中真正保存 name/description/schema 的部分 |
| ToolSpec | 采样工具规范 | ConversationRequest 使用的 Provider-neutral 工具定义 |
| ToolMetadata | 工具元数据 trait | 提供 kind、namespace、description、requirements 和 versioned definition |
| ToolEntry | 注册期工具条目 | Finalize 前保存 typed closures、canonical schema 和 metadata 的对象 |
| FinalizedTool | 已定型工具 | 已解析 client name、版本、schema、params 和 parser 的 Session 工具 |
| FinalizedToolset | 已定型工具集 | Session 实际用于公告、解析与 dispatch 的 Registry |
| canonical | 规范内部形式 | Rust/Registry 使用的稳定工具名、参数名与 schema 坐标系 |
| client-facing | 面向模型/客户端 | 当前 Session 实际公告的 name、description 与参数名 |
| fully-qualified ID | 全限定标识 | 带 namespace 的内部工具 ID，如 `GrokBuild:read_file` |
| client name | 客户端名称 | 模型 Function Call 中必须使用的最终工具名 |
| name override | 工具改名 | ToolConfig 对 client name 的显式替换 |
| parameter override | 参数改名 | canonical parameter → client-facing parameter 的映射 |
| reverse params | 反向参数映射 | client-facing key → canonical key，用于 dispatch 前恢复 |
| remap | 重映射 | 修改 JSON key 或 Schema properties/required 的过程 |
| properties | 属性表 | JSON Schema 中字段名到字段 schema 的 map |
| required | 必填数组 | JSON Schema 中必须出现的 property names |
| additionalProperties | 额外属性策略 | Schema 是否允许 properties 未列出的 key |
| default | 默认值提示 | Schema 给模型的默认信息，或 Serde/Runtime 的缺省行为；正文会区分 |
| schema-only default | 仅 Schema 默认 | 公告给模型但不在反序列化时自动写入字段的默认值 |
| materialize | 实体化 | 将缺省语义真正写成 Rust 字段中的具体值 |
| custom deserializer | 自定义反序列化器 | 接受字符串数字、宽容 bool 等额外 wire 形状的函数 |
| lenient parser | 宽容解析器 | 接受规范 schema 之外少量常见模型偏差的 parser |
| deny_unknown_fields | 拒绝未知字段 | Serde 遇到未声明 key 时直接失败的策略 |
| inline subschema | 内联子模式 | 将子类型 schema 放在使用位置而非通过 `$ref` 间接引用 |
| root metadata | 根元数据 | Schema 顶层的 title、description、`$schema` 等字段 |
| effective params | 生效配置参数 | defaults 与 ToolConfig overrides 合并后的 Session 工具配置 |
| versioned definition | 版本化定义 | 根据 contract version、params 和 renderer 生成 ToolDefinition 的扩展点 |
| behavior preset | 行为预设 | 为一组版本管理工具选择合同版本的配置名 |
| behavior version | 行为版本覆盖 | 针对某个工具覆盖 preset 的配置字段 |
| contract version | 合同版本 | 解析后的具体版本，供广告与 Runtime 行为共同使用 |
| TemplateRenderer | 模板渲染器 | 将工具/参数别名、配置与限制注入 description 的组件 |
| description template | 说明模板 | 含 MiniJinja placeholder 的工具自然语言说明 |
| property description | 字段说明 | JSON Schema 每个 property 内指导模型如何填值的文本 |
| Tool Choice | 工具选择策略 | Auto、None、Required 或强制某个 Function |
| strict mode | 严格模式 | Provider 在生成阶段强制输出符合 schema 的能力 |
| Function Tool strict | 工具参数严格生成 | Provider 对普通 Function arguments 的 schema enforcement |
| Structured Output strict | 结构化回答严格生成 | Provider 对最终回答 JSON schema 的 enforcement |
| Provider adapter | 协议适配器 | 将 ToolSpec 转成 Chat、Responses 或 Messages wire type 的代码 |
| hosted tool | Provider 托管工具 | 在模型服务端执行、可能与本地 Function name 冲突的工具 |
| dynamic tool | 动态工具 | Session 运行时才注册、schema 来自外部服务的工具 |
| MCP schema override | MCP 模式覆盖 | 用远端 MCP inputSchema 替代本地 Args 派生 schema |
| Dynamic Value | 动态 JSON 值 | 不反序列化为固定第三方 Rust struct 的 serde_json::Value |
| compatibility alias | 兼容别名 | 外部 settings/hook 术语到 ToolKind/tool names 的映射，不必可直接调用 |
| alias table | 别名表 | `claude_alias.rs` 中集中维护的兼容对应关系 |
| schema/parser drift | 模式/解析器漂移 | 模型公告合同与 Runtime 实际接受形状不一致 |
| Provider parity | Provider 一致性 | 三种 API 虽 wire 不同，但携带相同工具语义合同 |
| round-trip test | 往返测试 | 从 exported schema 输入经 reverse remap 到 typed parser 的端到端验证 |
| schema snapshot | Schema 快照 | 固定关键 properties、required、defaults 与 description 的测试证据 |
| version matrix | 版本矩阵 | 对 current、legacy、override 等合同版本逐一验证 |

---

## 168. 下一篇建议

下一篇继续追模型“看到工具”之前的选择问题：

> **源码精读 32：Agent Tool Registry Selection——工具包怎样注册，Agent 配置、Capability、Requirements、模式、MCP Progressive Discovery 与每轮 Snapshot 怎样决定本次请求究竟公告哪些工具**

它会解释 Tool Schema 已经能生成以后，为什么某个工具仍可能不出现在当前 Prompt，以及动态工具集合怎样避免破坏缓存和会话一致性。
