# 64：Serde 与数据边界——JSON、JSONL、TOML、反序列化防线与 Schema

> 源码基线：`4ee41929eaf4`
>
> 第 20 章沿一次请求追踪数据，第 44 章讨论协议怎样兼容演进，第 63 章解释值在内存里怎样存在。本章补上中间的转换层：Rust value 怎样编码成 JSON/TOML bytes，外部 bytes 又怎样经过解析、类型转换、容量限制和语义验证，才成为程序愿意信任的 value。

## 1. 本章解决什么问题

读 Codex 源码时，你会反复遇到：

```rust
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Params {
    thread_id: String,
    #[serde(default)]
    limit: Option<u32>,
}
```

本章要让你真正看懂：

- `Serialize`、`Deserialize` 和 Serde 分别是什么；
- Rust type、Serde data model、JSON syntax、wire bytes 为什么不是一回事；
- `rename`、`default`、`alias`、`flatten`、`tag`、`untagged` 改变了什么；
- missing、`null`、空数组和默认值为何不能混为一谈；
- `serde_json::Value` 何时方便，何时会把错误推迟；
- JSONL 为什么适合 append-only rollout；
- TOML 配置为何常先解析成 `toml::Value`，合并后再转成 `ConfigToml`；
- 自定义 `Visitor` 怎样拒绝重复键和“结构膨胀”攻击；
- JSON Schema、TypeScript type 与运行时校验是什么关系；
- 怎样测试真实 wire shape，而不只测试 Rust 对象。

## 2. 先说人话：序列化像“装箱”，反序列化像“安检后拆箱”

假设 Rust 内存里有：

```rust
Params {
    thread_id: "abc".to_string(),
    limit: Some(20),
}
```

序列化后可能是：

```json
{"threadId":"abc","limit":20}
```

反序列化不是简单地“把字符串变回 struct”。它至少包含：

```text
bytes
  → 检查 JSON/TOML 语法
  → 建立格式中的 value
  → 按字段名和 enum 形状匹配 Rust type
  → 检查数字范围、必填字段和自定义规则
  → 得到 typed value
  → 再做跨字段与业务验证
```

外部输入在最后几步完成以前都不应被当成可信业务状态。

## 3. 四层模型不要混在一起

| 层 | 例子 | 负责什么 |
|---|---|---|
| Rust domain model | `Params { thread_id, limit }` | 程序内部要表达的含义 |
| Serde data model | struct、map、sequence、string、integer | 序列化器和 Rust type 之间的抽象语言 |
| Format | JSON、TOML | 字段和 value 的文本/二进制语法 |
| Framing/transport | JSONL newline、HTTP body、stdio line | 多条 message 怎样分界和传输 |

Serde 不是 JSON；JSON 也不规定消息一定通过 HTTP 发送。

## 4. `serde` crate 做什么

Serde 定义通用的两侧接口：

- Rust value 实现 `Serialize`，把自身描述给 `Serializer`；
- Rust type 实现 `Deserialize`，从 `Deserializer` 请求所需数据；
- `serde_json`、`toml` 等 crate 实现具体 format。

因此同一个 `ConfigToml` 可以由 TOML deserializer 构造，也可以在测试中从别的兼容表示构造；业务 type 不必手写每种文本格式的 parser。

## 5. `Serialize` 与 `Deserialize` 不是互逆魔法

它们是两个独立 contract：

```text
Serialize:   Rust value → format representation
Deserialize: format representation → Rust value
```

一个 type 可以：

- 只实现序列化；
- 只实现反序列化；
- 读取多个旧 shape，但只写一个 canonical shape；
- 读取 string，内部却保存成验证过的 `PathUri`。

所以 `decode(encode(x)) == x` 很重要，但不是全部测试。

## 6. `derive` 实际帮你生成什么

`#[derive(Serialize, Deserialize)]` 是 procedural macro。它查看 struct fields、enum variants 和 `#[serde(...)]` 属性，生成 trait implementation。

可以把：

```rust
#[derive(Deserialize)]
struct User { name: String, age: u32 }
```

粗略理解成：“要求输入是一个 map，识别 `name`、`age`，分别调用 `String` 和 `u32` 的反序列化，再构造 `User`。”

## 7. JSON 的六类 value

JSON 的核心只有：

```text
null
boolean
number
string
array
object
```

它没有 Rust 的 tuple struct、`PathBuf`、`Duration`、`u128`、enum variant 或 raw bytes 概念。这些必须约定一种 JSON shape。

## 8. JSON 文本先是 bytes

网络或文件层拿到的是 bytes。JSON 文本通常按 UTF-8 解释；引号、反斜杠和控制字符需要 escaping。

```json
{"message":"line 1\nline 2","path":"C:\\work\\repo"}
```

这里 `\n` 是 JSON 文本中的两个字符表示，解码后才成为 newline；`\\` 解码后成为一个反斜杠。

## 9. Syntax error 与 type error 不同

```json
{"limit": 20
```

缺右花括号，是 syntax error。

```json
{"limit": "twenty"}
```

JSON 语法正确，但若目标字段是 `u32`，就是 type error。

```json
{"min": 20, "max": 10}
```

也许语法和字段类型都正确，却违反 `min <= max` 的 semantic invariant。

## 10. 三道边界检查

```text
语法检查 ── 这是不是合法 JSON/TOML？
类型检查 ── 它能否构造成目标 Rust type？
语义检查 ── 这个 value 在业务上是否允许？
```

只成功调用 `serde_json::from_str`，不代表所有业务约束已经成立。

## 11. 常用 JSON API 的方向

| API | 输入 → 输出 |
|---|---|
| `to_string` | Rust value → JSON `String` |
| `to_vec` | Rust value → UTF-8 JSON `Vec<u8>` |
| `to_writer` | Rust value → writer |
| `to_value` | typed Rust value → `serde_json::Value` |
| `from_str` | JSON `&str` → typed value |
| `from_slice` | JSON bytes → typed value |
| `from_reader` | reader → typed value |
| `from_value` | `serde_json::Value` → typed value |

`to_value/from_value` 不产生最终 wire bytes，而是在 typed representation 和 JSON tree 之间转换。

## 12. `serde_json::Value` 是一棵动态树

它大致对应：

```rust
enum Value {
    Null,
    Bool(bool),
    Number(Number),
    String(String),
    Array(Vec<Value>),
    Object(Map<String, Value>),
}
```

优点是 shape 灵活；代价是字段错误往往到更晚才暴露，并且整棵 tree 有 allocation 和节点开销。

## 13. Typed struct 与 `Value` 的取舍

| 选择 | 优点 | 风险 |
|---|---|---|
| Typed struct/enum | 编译器和反序列化器尽早检查 shape | 协议扩展需要改 type |
| `Value` | 可保存任意扩展数据 | typo、类型错误、缺字段更晚出现 |
| Typed core + `Value` extension | 核心 contract 清晰，扩展仍灵活 | 必须明确扩展由谁验证 |

Codex 的 MCP types 常把 `name`、`uri` 等核心字段 typed，同时把 extension metadata 或 schema 保存为 `Value`。

## 14. `rename_all = "camelCase"`

Rust 常用 snake_case，JSON API 常用 camelCase：

```rust
#[serde(rename_all = "camelCase")]
struct Params {
    thread_id: String,
}
```

wire 上是：

```json
{"threadId":"abc"}
```

Rust 字段名没有改变；改变的是 serializer/deserializer 看到的外部名称。

## 15. `rename` 是单字段或单 variant 的精确映射

固定源码中的 `CapabilityRootLocation::Environment` 明确写了：

```rust
#[serde(rename = "environmentId")]
#[ts(rename = "environmentId")]
environment_id: String,
```

Serde 和 TypeScript rename 同时存在，是为了让运行时 wire 与生成的客户端类型讲同一种语言。

## 16. `alias` 通常只影响读取

MCP bridge 中：

```rust
#[serde(rename = "inputSchema", alias = "input_schema")]
input_schema: serde_json::Value,
```

它可以读取新旧两种 spelling，但正常序列化写出 canonical `inputSchema`。

这是一种“宽读、窄写”：兼容旧 producer，同时阻止系统继续制造旧格式。

## 17. Missing、`null`、empty 是三种状态

```json
{}
{"items":null}
{"items":[]}
```

它们分别表示：

- 字段没有出现；
- 字段出现且明确为 null；
- 字段出现且值是空 collection。

业务语义可能相同，也可能完全不同，不能只凭 `Option` 猜。

## 18. `Option<T>` 的常见读取语义

对普通 derived struct field，`Option<T>` 通常让 missing 和 explicit `null` 都变成 `None`：

```text
missing       → None
null          → None
valid T       → Some(T)
wrong type    → error
```

如果业务必须区分 missing 与 null，需要额外 representation，例如 nested option、自定义 enum 或显式 patch type。

## 19. `Option<T>` 的默认写出语义

没有属性时，`None` 通常会写成：

```json
{"field":null}
```

加上：

```rust
#[serde(skip_serializing_if = "Option::is_none")]
```

才会省略字段。省略与 null 的 wire compatibility 影响要单独审查。

## 20. `#[serde(default)]` 处理 missing

它表示字段缺失时调用 `Default::default()`，或调用指定函数：

```rust
#[serde(default)]
enabled: bool, // missing → false
```

它通常不把错误类型变成默认值；`"enabled":"yes"` 仍应失败。

## 21. `default` 不是“吞掉一切错误”

安全的 mental model：

```text
没有提供 → 使用默认
提供且合法 → 使用输入
提供但非法 → 报错
```

如果 custom deserializer 把非法输入也静默变成 `None`，那是额外的产品决定，不是 `default` 自带行为。

## 22. 一个四格表比凭感觉更可靠

为每个 optional field 写下：

| 输入 | 预期 |
|---|---|
| missing | default / None / error |
| null | None / error / 特殊清除语义 |
| valid value | typed value |
| invalid value | error / 有意 lossy |

这张表直接决定测试用例。

## 23. Struct 在 JSON 中通常是 object

```rust
struct Point { x: i32, y: i32 }
```

通常编码为：

```json
{"x":1,"y":2}
```

字段在文本中的顺序不应成为业务 contract；JSON object 的语义应按 key 访问。

## 24. Enum 必须选择 wire representation

Rust enum 能表达不同 shape：

```rust
enum Message {
    Text { text: String },
    Image { url: String },
}
```

JSON 没有原生 enum，所以必须决定 discriminator 放在哪里。

## 25. Externally tagged：Serde 默认 enum 形状

概念示例：

```json
{"Text":{"text":"hello"}}
```

variant name 是最外层 object key。这对 Rust 内部数据方便，但不一定符合公共 API 风格。

## 26. Internally tagged：`tag = "type"`

```rust
#[serde(tag = "type", rename_all = "camelCase")]
enum Location {
    Environment { environment_id: String },
}
```

wire shape：

```json
{"type":"environment","environmentId":"env-1"}
```

Codex 的 `CapabilityRootLocation` 就使用这种清晰的 discriminator。

## 27. Adjacently tagged：`tag` + `content`

App-server notification enum 使用类似：

```rust
#[serde(tag = "method", content = "params")]
enum ServerNotification { /* ... */ }
```

概念 wire：

```json
{"method":"item/updated","params":{"id":"x"}}
```

`method` 决定 variant，`params` 承载 payload。

## 28. Untagged enum：靠 shape 猜 variant

```rust
#[serde(untagged)]
enum RequestId {
    String(String),
    Integer(i64),
}
```

输入是 string 就选第一种，是 integer 就选第二种。wire 很简洁，但 variant shape 重叠时可能含糊。

## 29. Untagged 的顺序风险

反序列化器通常依次尝试 variant。若“宽泛 variant”放得太前，它可能抢先接受本应属于后面 variant 的输入。

审查时要问：

- 各 variant 能否由同一个 JSON value 同时匹配？
- 新增 variant 是否改变旧输入的归类？
- 错误信息是否因多次尝试而模糊？

## 30. `flatten` 把嵌套字段铺到同一 object

Rollout writer 的引用结构：

```rust
struct RolloutLineRef<'a> {
    timestamp: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    ordinal: Option<u64>,
    #[serde(flatten)]
    item: &'a RolloutItem,
}
```

`item` 不会成为 `"item": {...}`，其字段被合并进同一行 object。

## 31. `flatten` 的好处与成本

好处是 wire 更扁平、兼容已有 line shape。成本是：

- 两侧字段名可能碰撞；
- owner 边界不如嵌套 object 明显；
- unknown-field 与 schema 行为更复杂；
- 修改被 flatten type 可能意外改变外层 wire。

## 32. Custom deserializer 是边界 adapter

固定源码中的 path 字段在 wire 上是 string，内部却是 `PathUri`：

```rust
#[serde(deserialize_with = "deserialize_path_uri_from_api_path")]
#[schemars(with = "String")]
#[ts(type = "string")]
path: PathUri,
```

读取函数先接受 `LegacyAppPathString`，尝试解析 URI，再走 compatibility conversion；失败通过 `serde::de::Error::custom` 回到字段错误。

## 33. 为什么不先收 `String`，以后再检查

越早建立 validated type，越少内部调用点需要重复问：

```text
它为空吗？
它是合法 URI 吗？
它来自 foreign OS 时还能保持原义吗？
```

反序列化边界适合验证单字段的 representation invariant；跨字段或需要 I/O 的规则通常留给后续 validation。

## 34. 不要把所有业务逻辑塞进 `Deserialize`

反序列化最好保持确定、快速、无副作用。不要在其中：

- 发网络请求；
- 查询数据库；
- 读取当前用户权限；
- 修改全局状态；
- 根据当前时间产生难复现结果。

这些属于 application validation 或 command handling。

## 35. 单字段 validation 与跨字段 validation

```text
deserialize_with:
  "300" 能否成为 u16？
  path string 能否成为 PathUri？

post-deserialize validation:
  min <= max 吗？
  path 在允许 root 内吗？
  当前 principal 有权限吗？
```

前者关心 value 自身，后者关心其他 fields 和运行环境。

## 36. Unknown field 的默认行为

很多 Serde derived struct 会忽略不认识的 fields。这有利于新 producer 与旧 reader 共存，但 typo 也可能悄悄失效：

```toml
model_contex_window = 200000
```

若 `model_contex_window` 被忽略，用户以为配置生效，实际没有。

## 37. `deny_unknown_fields` 与外部严格检查

可在适合的 type 上使用 Serde 的严格属性；Codex config schema 也用 `#[schemars(deny_unknown_fields)]` 描述未知字段约束。

但 Codex 的 strict config 还用 `serde_ignored` 收集被忽略路径，再把第一条未知字段变成带 source range 的诊断。这样可以兼顾 typed decode 和用户友好错误。

## 38. `serde_ignored` 做什么

它包装 deserializer，并在某个字段没有被目标 type 消费时回调：

```text
profiles.work.features.typo
```

Codex 把 path segments 保存起来，再寻找 TOML key 在原文中的位置，报告：

```text
unknown configuration field `profiles.work.features.typo`
```

## 39. `serde_path_to_error` 做什么

普通 type error 可能只说“expected integer”。`serde_path_to_error` 还保留失败发生的 nested path。

概念示例：

```text
profiles.work.mcp_servers.github.timeout_sec
```

Codex diagnostics 再结合 TOML parser span，把路径提示映射到具体文本 range。

## 40. Duplicate JSON object key 为什么危险

这段文本语法上常能被 parser 接受：

```json
{"method":"safe","method":"dangerous"}
```

不同实现可能 first-wins、last-wins 或拒绝。如果 validator 与 executor 选择不同，攻击者就能制造 interpretation differential。

## 41. Exec-server 明确拒绝重复 key

固定源码的 `BoundedValueVisitor::visit_map` 在插入后检查：

```rust
if values.contains_key(&key) {
    return Err(de::Error::custom(format!(
        "duplicate JSON object key `{key}`"
    )));
}
```

这比先解析为一个可能已经覆盖旧 key 的普通 map 再检查更可靠。

## 42. 解析成功也可能耗尽内存

一个几 MiB 的紧凑 JSON array 可以包含数百万个短 value：

```json
[0,0,0,0,0,0,0,...]
```

文本很紧凑，但解码成 `Vec<Value>` 后，每个 element 都有 enum/collection 开销，heap amplification 可能远大于 wire bytes。

## 43. Byte limit 与 node limit 解决不同问题

| 限制 | 阻止什么 | 不能阻止什么 |
|---|---|---|
| Wire byte cap | 巨大 body/string | 小文本产生很多 tree nodes |
| Value node cap | 巨大 array/object tree | 一个超大 scalar string |
| Depth cap | 极深递归/stack risk | 很宽但浅的 array |
| Field length cap | 巨大 string/blob/key | 很多小字段 |

安全边界通常需要组合，而非只选一个数字。

## 44. `BoundedValueSeed` 怎样计数

Exec-server 在每次反序列化一个 JSON value 前：

```rust
let Some(remaining) = self.remaining.checked_sub(1) else {
    return Err(/* exceeds limit */);
};
```

root、array element、object value 都消费 budget。达到 `MAX_JSONRPC_VALUE_NODES = 256 * 1024` 后拒绝消息。

## 45. `DeserializeSeed` 为什么适合携带 budget

普通 `Deserialize` 只描述“怎样造出 T”。`DeserializeSeed` 还能携带外部状态，例如：

- 剩余节点数；
- dictionary/interner；
- 已知 schema context；
- arena 或共享 state。

这里每个递归 child 都共享同一个 `remaining`，因此无法各自重置预算。

## 46. `Visitor` 是 streaming decode 接口

Deserializer 看到 bool、integer、string、sequence 或 map 时，调用相应 `visit_*` 方法。

`visit_seq` 不需要先得到完整 array 再遍历；它通过 `SeqAccess` 一个个取 element。`visit_map` 同理通过 `MapAccess` 逐 key/value 处理。

## 47. Streaming 接口不等于零内存

Exec-server visitor 最终仍构造完整 `Value::Array(Vec<Value>)` 和 `Value::Object(Map<...>)`。它利用 streaming 过程做早期计数和重复键检查，但结果仍是一棵 owned DOM tree。

如果 consumer 能逐项处理并丢弃，才可能避免保存完整 tree。

## 48. 节点上限不是字符串 byte 上限

固定测试明确允许一个长度超过 node-limit 数值的 scalar string，因为它只算一个 JSON value。

所以准确表述是：

```text
MAX_JSONRPC_VALUE_NODES 限制结构复杂度，
不限制单个 string 的 bytes，也不替代 transport body cap。
```

## 49. Raw JSON wrapper 也必须计入预算

启用 serde_json `raw_value` 时，某段 JSON 可先作为 raw encoded string 暂存。如果随后解开却不计 child nodes，就能绕过结构限制。

固定实现复用 wrapper root 已消费的一个 slot，同时继续为所有 child 收费；测试专门验证 raw wrapper 不能绕过上限。

## 50. Arbitrary precision number 的特殊表示

启用 serde_json `arbitrary_precision` 后，超出普通整数范围或带精确小数/指数的 lexical number 会通过一个内部 synthetic map token 交给 visitor。

Exec-server 识别 `$serde_json::private::Number`，把保存的文本重新解析为 `Number`，以保留大整数和精确词法数值。

## 51. “保留 JSON Number”不等于“能装进 i64”

```text
JSON Value::Number 可保留一个很大的整数
        ↓ from_value::<TypedStruct>()
i64 field 仍必须做 i64 range check
```

动态 tree 能保存，不代表每个业务 type 都能接受。

## 52. JavaScript number 是另一条边界

JSON 文本只说 number，consumer 语言却可能用 IEEE-754 double。大整数即使在 Rust 中被精确保留，进入 JavaScript `number` 后仍可能失真。

跨语言 ID/ordinal 若要求完整整数精度，要审查生成的 TS type 和客户端实际 representation；必要时使用 string contract。

## 53. JSON 不接受 NaN 与 Infinity

它们是某些浮点环境的 value，不是合法 JSON number。需要表达时必须另定 string/null/tagged representation，并明确语义。

不要假设 Rust `f64` 的每个 bit pattern 都能无损进入标准 JSON。

## 54. Integer、decimal 与 money 不应混用

- count/ordinal 通常适合 integer；
- measurement 可能接受 float 误差；
- money/精确十进制通常需要 decimal type 或 string；
- opaque ID 不应因为“看起来都是数字”就做算术。

类型选择就是 wire contract 的一部分。

## 55. JSON object key 必须是 string

Rust 的 `HashMap<u64, T>` 不能天然映射成拥有数字 key 的 JSON object；serializer 需要把 key 变成 string，或改写成 array of entries。

反序列化时还要定义 `"01"` 和 `"1"` 是否相同。

## 56. Bytes 在 JSON 中通常需要编码

JSON 没有 byte array scalar。常见选择：

- base64 string；
- integer array；
- 外部 blob URI；
- multipart/另一条 binary channel。

base64 会扩大 wire size，解码时还会暂时同时持有 encoded string 与 decoded bytes，所以两侧都要限额。

## 57. 借用式反序列化

Serde 的 lifetime `'de` 允许某些 format 从输入 buffer 借用 `&str`/`&[u8]`，减少 allocation。但前提是：

- 输入 buffer 活得够久；
- parser 能给出连续、无需 unescape 的 slice；
- 目标 type 声明可借用。

含 escape 的 JSON string 往往必须新建 decoded buffer；不要把“Serde 支持 borrow”理解为所有 string 都零拷贝。

## 58. Owned `Value` 适合跨 async 边界

`serde_json::Value` 通常拥有 string、array 和 object。它更容易放进 channel、task 或长期 state，不被输入 buffer lifetime 限制。

代价就是 allocation 与 clone 成本；第 63 章的 object graph 思维在这里直接适用。

## 59. `to_value`/`from_value` 是 adapter，不是 validation 终点

App-server 把通用 `JSONRPCRequest` 拆成 id/method/params，重建 `Value::Object`，再 `from_value` 成 typed `ClientRequest`。

这一步完成 method discriminator 与 params shape 的 typed dispatch，但 authorization、thread ownership 等规则仍由后续 handler 判断。

## 60. JSON-RPC envelope 的分类

Exec-server custom decoder 先得到 bounded object，再按字段分类：

```text
有 method + id → Request
有 method      → Notification
有 result      → Response
否则           → Error
```

这是 Codex dialect 的实际 decode rule。阅读协议时要看实现，而非仅凭 JSON-RPC 名称猜。

## 61. Request ID 使用 untagged string/integer

```json
{"id":"req-17","method":"x"}
{"id":17,"method":"x"}
```

两者都能构成 `RequestId`，但 response 必须原样关联同一个逻辑请求。不要在日志或 map key 中不加类型地都格式化成 `"17"`，否则 string 与 integer 可能碰撞。

## 62. Notification 为什么没有 id

Request 期待 response，所以需要 correlation ID；notification 不期待 response，通常没有 id。

这不是“字段忘了写”，而是 envelope 类型的语义区分。Custom decoder 也正是用 id 是否存在区分 request 与 notification。

## 63. JSONL 是 framing，不是新 data model

JSON Lines 的核心规则是：

```text
每一行是一条完整 JSON value
newline 是 record delimiter
```

每行内部仍然是普通 JSON；JSONL 解决的是多个 record 怎样拼在同一文件/stream 中。

## 64. JSONL 与 JSON array 对比

| JSONL | 单个巨大 JSON array |
|---|---|
| 易 append 新行 | 追加前通常要处理结尾 `]` |
| 可逐行处理 | parser 常面对一个整体容器 |
| 单行天然是定位单位 | element 位置需在 array 中找 |
| 尾部半行可单独识别 | 截断可能使整个 array 不完整 |

这使 JSONL 很适合持续增长的 rollout ledger。

## 65. Codex Rollout writer 的真实动作

固定源码中 `JsonlWriter::write_line`：

```rust
let mut json = serde_json::to_string(item)?;
json.push('\n');
self.file.write_all(json.as_bytes()).await?;
self.file.flush().await?;
```

也就是“序列化一条完整 record → 加 newline → 写 bytes → flush”。

## 66. Append 前为什么检查末尾 newline

恢复旧 rollout 时，若现有文件最后没有 `\n`，直接 append 会把两条 JSON 粘成一行。

`ensure_rollout_is_newline_terminated` 读取最后一个 byte；不是 newline 就补写并 flush，再继续 append。

## 67. Flush 不等于 crash-proof durability

`flush` 确保用户态 buffered writer 将数据交给更下层，但通常不等于 storage 已经 durable。要讨论掉电保证，还需检查 `sync_data`/`fsync`、目录同步、filesystem 和硬件语义。

JSONL 解决 framing；durability 是另一层 contract。

## 68. 一行损坏时怎样思考

Reader 可以逐行：

```text
读取 line
→ parse JSON
→ deserialize RolloutLine
→ 成功则消费，失败则记录 line/offset
```

是否跳过坏行、停止、修复尾行或重建 projection，是 reader policy；JSONL 本身不替你决定。

## 69. `flatten` 让 RolloutLine 保持 canonical shape

Timestamp 和 optional ordinal 与具体 `RolloutItem` fields 同处一层，便于既保存 envelope metadata 又维持既有 item shape。

但 reader、schema 与兼容测试都必须以实际 flattened JSON 为准，不能只看 Rust struct nesting。

## 70. TOML 是配置格式，不只是“更好看的 JSON”

TOML 有 table、array、string、integer、float、boolean、datetime 等自己的 syntax 和语义：

```toml
model = "gpt-example"
approval_policy = "on-request"

[features]
example = true
```

`[features]` 是 table header，不是 JSON object brace 的换皮。

## 71. TOML key 与 Rust field 的映射

Codex `ConfigToml` fields 多数使用 snake_case，与用户文件一致：

```toml
model_context_window = 200000
```

这与 App-server wire 常用 camelCase 是不同 contract。不要为了内部字段统一，随意把两条外部表面改成同一种命名。

## 72. 先解析成 `toml::Value` 的意义

Codex config loader 会把各来源先读成动态 TOML tree。这样可以在 typed conversion 前：

- 组合多个 layer；
- 应用 CLI/runtime overrides；
- 处理 project trust；
- 规范化 aliases；
- 保留 source 与优先级信息。

## 73. Config layer merge 与 typed decode 是两步

```text
system/managed/user/project/session TOML
               ↓ parse each
          several TomlValue trees
               ↓ merge by precedence
          effective TomlValue
               ↓ typed deserialize
             ConfigToml
```

若每层先各自转成填满 defaults 的 `ConfigToml` 再 merge，可能无法区分“没写”与“显式写默认值”。

## 74. 为什么 config fields 大量使用 `Option<T>`

在 layer merge 语境中，`Option` 常表达“这个 layer 是否提供 override”。

```text
None         → 本层未指定，允许下层/默认决定
Some(false)  → 本层明确关闭
```

若过早把 missing 变成 false，就丢失 precedence 所需信息。

## 75. Effective config 才适合施加运行默认值

推荐区分：

```text
ConfigToml / raw layer shape   保留用户是否提供
Effective runtime config       所有必要默认与政策已经解析
```

名字相近时要看 type 与构造位置，别把“可缺省输入”误当成“运行时可能没有答案”。

## 76. Custom config error 比 parser 字符串更有用

Codex diagnostics 组合：

- source file path/display name；
- nested field path；
- TOML parser span；
- 映射到用户文本的 range；
- 清楚的 error message。

这让 UI 能指出哪个文件、哪段 key 出错，而非只说“配置加载失败”。

## 77. Untagged config compatibility 例子

`ForcedChatgptWorkspaceIds` 允许：

```toml
forced_chatgpt_workspace_id = "one-id"
```

或：

```toml
forced_chatgpt_workspace_id = ["one-id", "another-id"]
```

内部用 untagged `Single(String)` / `Multiple(Vec<String>)` 兼容两种 shape。

## 78. Custom error 可以教用户怎样修

同一 deserializer 主动拒绝 comma-separated single string，并在错误里给出 TOML list 写法。

好错误不仅说“失败”，还说明：

```text
接受哪些形状
当前形状为何有歧义
正确替代语法是什么
```

## 79. Lossy deserialization 必须显式命名

MCP bridge 的 `deserialize_lossy_opt_i64` 对无法装进 i64 的 number 返回 `None`。`lossy` 写进函数名非常重要：这是有意丢失，而不是普通严格转换。

使用前要问：丢失 size metadata 是否可接受？若字段是权限、金额、ordinal 或安全上限，通常不能静默丢失。

## 80. Schema 是机器可读的“允许形状说明”

JSON Schema 可以描述：

- object properties；
- required fields；
- string/number/array 类型；
- enum/discriminator 组合；
- definitions 与 references；
- 部分 range、pattern、unknown-field 限制。

Codex 使用 `schemars::JsonSchema` 从 Rust types 生成许多 schema fixtures。

## 81. Schema 不执行 Rust deserializer

Schema 是描述，runtime deserializer 是代码。它们可能不一致，尤其存在：

- `deserialize_with`；
- 手写 `Deserialize`；
- conditional/semantic validation；
- lossy conversion；
- compatibility alias；
- generator 不理解的 generic/wrapper。

因此 schema validation 不能替代 runtime tests。

## 82. `schemars(with = "String")` 为什么必要

`PathUri` 内部 type 可能很复杂，但 public wire 是 string。若只 derive schema，generator 可能描述内部 representation。

```rust
#[schemars(with = "String")]
#[ts(type = "string")]
path: PathUri,
```

是在明确告诉两个 generator：外部 consumer 看到的是 string。

## 83. TypeScript type 也不是 runtime guard

TS type 在编译期帮助客户端开发者，但网络来的 JSON 不会因为写了 interface 就自动安全。

```text
TypeScript compile-time type
≠ JSON parser validation
≠ Rust Deserialize
≠ authorization
```

边界 consumer 仍需真实 runtime decode。

## 84. Serde、TS 与 Schema rename 要对齐

若 Rust serializer 写 `environmentId`，TS 却生成 `environment_id`，客户端会根据错误 contract 编码。

所以固定源码在有显式 rename 时，同时标注：

```rust
#[serde(rename = "environmentId")]
#[ts(rename = "environmentId")]
```

生成物 diff 是发现漂移的重要证据。

## 85. Generated fixture 为什么要提交

提交 schema/TS fixture 能让 reviewer 直接看到 wire surface 的变化，也让 CI 检查源码 derive 与生成物是否同步。

在 Codex 修改 app-server protocol 后要关注 `just write-app-server-schema`；修改 `ConfigToml` 或 nested config type 后要关注 `just write-config-schema`。

## 86. Pretty JSON 与 compact JSON 语义通常相同

```json
{"x":1,"y":2}
```

和：

```json
{
  "x": 1,
  "y": 2
}
```

解析出的 data model 相同。不要用空白格式做签名、cache key 或 equality，除非 contract 明确就是 exact bytes。

## 87. Canonicalization 是额外协议

JSON object key order、escape spelling、number lexical form 可能不同但语义相同。若要签名或按 bytes 去重，必须定义 canonical encoding，并对“真正发送的 exact bytes”计算签名。

“都用了 serde_json”并不足以证明不同版本/语言得到完全相同 bytes。

## 88. Round-trip test 能证明什么

```rust
let bytes = serde_json::to_vec(&value)?;
let decoded: T = serde_json::from_slice(&bytes)?;
assert_eq!(decoded, value);
```

它证明自己的 writer 与 reader 对该 value 配对，但不能证明：

- wire key 恰好符合公共 contract；
- 旧 fixture 还能读；
- 其他语言能读；
- unknown/missing/null 行为正确；
- 恶意输入受限。

## 89. Wire-shape test

对公共协议，应直接断言 JSON：

```rust
assert_eq!(
    serde_json::to_value(value)?,
    serde_json::json!({
        "type": "environment",
        "environmentId": "env-1",
        "path": "file:///work"
    }),
);
```

这样 rename、tag、flatten、omit/null 的变化会清楚失败。

## 90. Reader fixture test

保存真实旧 JSON/TOML/JSONL fixture，然后只用当前 reader 解析。它能捕捉：

- variant rename；
- required field 新增；
- alias 删除；
- numeric range 收窄；
- custom deserializer 改变；
- rollout 恢复失败。

## 91. 边界矩阵测试

至少覆盖：

| 维度 | 样例 |
|---|---|
| Presence | missing / null / present |
| Type | correct / wrong type |
| Number | zero / max / overflow / fraction |
| Collection | empty / normal / over limit |
| Object | unknown key / duplicate key |
| Enum | every variant / unknown discriminator / ambiguous shape |
| Compatibility | canonical name / alias / old fixture |
| Framing | full line / truncated tail / two records |

## 92. Fuzzing 适合 parser 边界

Fuzzer 可不断生成 nested arrays、奇怪 escape、极端 number、重复/未知 key 和截断 bytes，寻找 panic、OOM、超时或不一致解释。

重要 property：

```text
任意输入只能得到受控 error 或合法 value，不能 panic；
资源消耗必须受输入与硬上限约束。
```

## 93. 深度攻击与 stack safety

```json
[[[[[[[[[[0]]]]]]]]]]
```

极深 nesting 可能触发 parser recursion limit 或后续递归 walk 的 stack overflow。即使 parser 自己安全，业务代码递归遍历 `Value` 时也要考虑深度。

Node cap 限宽度总量，depth cap 限纵向嵌套；两者不可互相完全替代。

## 94. 解压后再解析要有两层预算

压缩 body 很小，不代表解压后小：

```text
compressed byte cap
→ bounded decompression / expanded byte cap
→ JSON node/depth/string caps
→ typed validation
```

若只在压缩前检查 Content-Length，compression bomb 仍可耗尽内存或 CPU。

## 95. 错误日志不要原样打印整个 payload

不可信 body 可能：

- 包含 secret/token；
- 极大，放大日志成本；
- 包含终端控制字符；
- 制造高基数字段。

优先记录 bounded preview、byte count、content hash、request ID、错误 path 和安全分类后的字段。

## 96. 一条推荐的 decode pipeline

```text
transport byte limit
  → decompression limit（若有）
  → UTF-8/format parser
  → structure/depth/duplicate-key limits
  → typed Deserialize
  → field + cross-field validation
  → authorization / policy
  → handler side effect
```

解析成功绝不能跳过 authorization。

## 97. 排错：`missing field`

依次检查：

1. Wire 上字段真的存在吗？
2. `rename_all` 后的名字是什么？
3. 字段嵌套层级是否因 `content`/`flatten` 不同？
4. 它是否应该是 `Option<T>` 或有 `default`？
5. Producer 使用的是旧 alias 吗？
6. 你是否在解析错误的 envelope type？

## 98. 排错：`invalid type`

依次检查：

1. 错误 path 指向哪个 nested field？
2. 输入是 string、number、object 还是 null？
3. 目标是 `i64`、`u64` 还是 `f64`？
4. Number 是否 overflow 或带 fraction？
5. Untagged variant 是否被另一分支抢先尝试？
6. Custom deserializer 是否又做了一次转换？

## 99. 排错：Rust 看起来没变，客户端却坏了

优先对比实际生成的 JSON/Schema/TS，而非只看字段 type：

- 是否改了 rename/tag/content/flatten？
- `None` 从 omitted 变成 null 了吗？
- 新 required field 是否没有 default？
- schema fixtures 是否未重新生成？
- 客户端是否把大整数放入 JS number？
- transport framing 是否改变？

## 100. 排错：配置写了却没有生效

沿链路逐层查：

```text
原始 TOML key 拼写
→ 该文件是否被 loader 选中
→ layer source 与 precedence
→ alias normalization
→ merged TomlValue
→ strict unknown-field diagnostic
→ ConfigToml typed field
→ effective runtime config
→ feature/policy 是否覆盖
```

不要只打印最终 bool；那会丢掉“值来自哪一层”的关键证据。

## 101. 排错：JSONL 恢复到中途失败

检查：

1. 失败 line number 与 byte offset；
2. 前一行是否有 newline；
3. 最后一行是否是 crash 留下的 partial write；
4. 单行 JSON 语法是否完整；
5. `RolloutLine` tagged/flattened shape 是否兼容；
6. number、timestamp、ordinal 是否超出新 type；
7. reader policy 是停止、跳过还是只修尾部。

## 102. 一个端到端例子：App-server request

假设 wire：

```json
{
  "id": "r-1",
  "method": "thread/read",
  "params": {"threadId": "t-1", "includeTurns": true}
}
```

解码路径可以理解为：

```text
bytes
→ JSONRPCRequest { id, method, params: Value }
→ 重建带 method discriminator 的 Value object
→ ClientRequest::ThreadRead { request_id, params }
→ handler 做 thread lookup / authorization
→ typed response
→ Value result
→ JSON bytes
```

## 103. 一个端到端例子：Config

```toml
model = "gpt-example"
model_context_window = 200000

[features]
example = true
```

概念路径：

```text
UTF-8 file
→ TOML parser + source span
→ TomlValue layer
→ aliases / trust / precedence merge
→ serde_ignored + serde_path_to_error
→ ConfigToml
→ requirements and defaults
→ effective runtime config
```

## 104. 一个端到端例子：Rollout JSONL

```text
RolloutItem
→ RolloutLineRef { timestamp, ordinal, flatten(item) }
→ serde_json::to_string
→ append '\n'
→ write_all + flush
→ later read one line
→ JSON Value / RolloutLine
→ history reconstruction or projection
```

它同时涉及 representation、framing、persistence 和 replay；不要只把它叫“写 JSON”。

## 105. 阅读 Serde 属性的固定顺序

看到一个 protocol type，建议按此顺序：

1. 先看 container-level `tag`、`content`、`untagged`、`rename_all`；
2. 再看 variant rename；
3. 再看 field rename、alias、flatten；
4. 再看 default、skip、custom deserialize；
5. 同时对照 TS/Schema annotations；
6. 最后打开 exact JSON fixture/test。

先从 wire 外形读，再进入业务含义，通常更不容易迷路。

## 106. 代码评审清单：表示层

- Rust field 与 wire key 是否一致？
- Enum representation 是否明确且无歧义？
- Omitted/null/default 是否逐项定义？
- Alias 是否只负责读取旧 shape？
- Flatten 是否可能发生字段碰撞？
- Number 和 bytes 在所有 consumer 中能否无损？
- Exact JSON fixture 是否更新？

## 107. 代码评审清单：不可信输入

- 有 transport byte cap 吗？
- 有解压后 cap 吗？
- 有 node、depth、string/collection cap 吗？
- Duplicate key policy 是否一致？
- Custom deserializer 会 panic 或做 I/O 吗？
- 错误是否带 path 且不泄露 payload？
- Parse 之后是否还有 semantic validation 与 authorization？

## 108. 代码评审清单：生成物

- `Serialize`、`Deserialize`、`JsonSchema`、`TS` 是否描述同一 wire？
- Custom type 是否需要 `schemars(with=...)` / `ts(type=...)`？
- Stable 与 experimental schema 是否走对生成路径？
- 生成物是否重新写入并 review？
- 旧 fixture reader test 是否仍通过？
- 是否意外扩大或缩小 public contract？

## 109. 常见误解一：`Value` 什么都能接，所以更稳

它只是把强类型检查推迟。若后续代码散落着：

```rust
value.get("threadId").and_then(Value::as_str)
```

typo、missing 与 wrong type 很容易被折叠成同一个 `None`。稳定核心字段更适合 typed struct。

## 110. 常见误解二：有 Schema 就不会收到坏数据

Schema 文件不会自动站在 socket 前验证每个 byte。即使 gateway 做了 schema validation，Rust runtime 仍必须把网络输入视为不可信，并处理版本差异、自定义规则和资源上限。

## 111. 常见误解三：`skip_serializing_if` 只是省几个 bytes

它改变 field presence。对 patch、default、旧 reader 和 generated client 来说，missing 与 null 可能代表不同动作，因此这是协议语义，不只是压缩技巧。

## 112. 常见误解四：Node cap 已经防住所有大输入

一个 100 MiB string 只是一节点；一个极深但节点总数未超限的结构仍可能挑战递归；一个 base64 string 解码后还会扩大。容量模型必须覆盖每个 expansion stage。

## 113. 常见误解五：JSONL 每行就天然原子

进程可在写一行中途崩溃，filesystem 也不承诺任意长度 append 都原子。Reader 必须考虑 truncated tail；需要更强 durability 时还要增加 fsync/journal/transaction 等机制。

## 114. 常见误解六：`Deserialize` 成功就是“安全对象”

它最多证明 representation contract。一个合法 `path` 仍可能越过授权 root；一个合法 `method` 仍可能要求审批；一个合法 URL 仍可能被网络策略禁止。

## 115. 本章最重要的心智模型

```text
外部 bytes
  │  不可信、可能巨大、可能歧义
  ▼
有界 format decode
  │  语法、重复键、节点/深度/长度
  ▼
typed representation
  │  字段、enum、range、custom conversion
  ▼
semantic validation
  │  跨字段 invariant
  ▼
authorization / policy
  │  当前身份与环境是否允许
  ▼
side effect
```

每个箭头都是一个独立边界，不能用“Serde 已经处理了”一笔带过。

## 116. 源码检查点

按以下顺序打开固定提交：

1. `codex-rs/exec-server-protocol/src/rpc.rs`
   - `JSONRPCMessage::deserialize`
   - `BoundedValueSeed`
   - `BoundedValueVisitor`
   - `RequestId`
2. `codex-rs/exec-server-protocol/src/rpc_tests.rs`
   - round-trip
   - arbitrary-precision number
   - duplicate-key rejection
   - raw-value budget
   - compact-array amplification
   - large scalar string
3. `codex-rs/app-server-protocol/src/protocol/common.rs`
   - `client_request_definitions!`
   - `server_notification_definitions!`
   - `to_value` / `from_value` bridge
4. `codex-rs/protocol/src/capabilities.rs`
   - internally tagged enum
   - aligned Serde/TS rename
   - custom `PathUri` deserializer
5. `codex-rs/protocol/src/mcp.rs`
   - typed fields + flexible `Value`
   - `alias`
   - lossy integer conversion
6. `codex-rs/rollout/src/recorder.rs`
   - `RolloutLineRef`
   - `JsonlWriter::write_line`
   - newline repair before append
7. `codex-rs/config/src/config_toml.rs`
   - `ConfigToml`
   - untagged compatibility type
   - custom user-facing error
8. `codex-rs/config/src/loader/mod.rs`
   - layered `TomlValue` merge
   - typed conversion
9. `codex-rs/config/src/strict_config.rs`
   - `serde_ignored`
   - unknown path collection
10. `codex-rs/config/src/diagnostics.rs`
    - `serde_path_to_error`
    - path/span/range mapping
11. `codex-rs/app-server-protocol/src/export.rs`
    - `schema_for!`
    - JSON Schema writers
12. `codex-rs/app-server-protocol/src/schema_fixtures.rs`
    - stable/experimental generated fixtures
13. `codex-rs/config/src/schema.rs`
    - config schema generation

## 117. 动手练习一：画出 wire shape

任选一个带 `#[serde(...)]` 的 app-server enum，不运行代码，先手写三个 variant 的 JSON。然后查看 schema fixture 或 test，对比：

- discriminator 名称；
- payload 是 flattened 还是在 `params` 中；
- field casing；
- optional field 是 omitted 还是 null。

错的地方通常就是你尚未真正理解的属性。

## 118. 动手练习二：制作 optional truth table

选一个 `Option<T>` field，分别准备 missing、null、valid、wrong-type 四个 payload，记录 decode 与 encode 结果。

再分别加入/移除 `default` 和 `skip_serializing_if`，观察两个方向各自变化。不要只记结论，要分清 Serialize 与 Deserialize。

## 119. 动手练习三：比较三种容量限制

构造：

1. 一个超长 string；
2. 一个超宽 array；
3. 一个超深 nested array。

预测 byte cap、node cap、depth cap 分别会拦住哪个，再对照 parser/transport 的真实配置。

## 120. 动手练习四：追踪 Config typo

在隔离测试中写一个不存在的 nested TOML key，沿：

```text
TomlValue
→ serde_ignored::Path
→ path segments
→ source span
→ ConfigError range/message
```

解释为什么只在 `ConfigToml` 上随处加 `deny_unknown_fields`，不一定能得到同样友好的 layer/source 诊断。

## 121. 动手练习五：审查一次协议改动

假设要把 `environmentId` 改名，列出必须检查的对象：

```text
Serde rename
TS rename
JSON Schema
writer
current reader
old reader/new writer compatibility
new reader/old writer compatibility
fixtures
rollout/persisted data（若存在）
```

然后决定是 hard break、alias read compatibility，还是增加新 field/discriminator。

## 122. 理解检查

你应该能不看答案解释：

1. 为什么 Serde、JSON 和 HTTP 是三个不同层？
2. `default` 与 `skip_serializing_if` 分别作用在哪个方向？
3. 为什么 missing、null、empty 不能自动视为相同？
4. Internally tagged 与 untagged enum 的主要差异是什么？
5. `flatten` 为什么会扩大外层 type 的 wire surface？
6. `Value` 为什么既灵活又可能增加内存和验证风险？
7. Duplicate keys 为什么可能成为安全问题？
8. Node cap 为什么不能替代 byte/depth/string cap？
9. JSONL 为什么适合 rollout，又为什么不天然 durable？
10. TOML layer 为什么通常先 merge raw values 再 typed decode？
11. Schema 和 TS type 为什么都不能替代 runtime validation？
12. `deserialize_with` 之后为什么仍需 authorization？

## 123. 本章小结

- Serialization 把内部 value 编成外部 representation；deserialization 是不可信输入边界。
- Rust type、Serde data model、JSON/TOML format 与 transport framing 是四层。
- Serde attributes 同时决定 field name、presence、enum shape 和兼容读法。
- Missing、null、empty、default 必须按字段明确设计和测试。
- Typed core 能尽早暴露错误，`Value` 适合真正开放的 extension surface。
- JSONL 通过 newline framing 支持逐条 append/read，但原子性与 durability 仍需额外机制。
- Config 先以 `TomlValue` 合并 layer，再 typed decode，才能保留 precedence 和“是否提供”的信息。
- Exec-server 用 custom seed/visitor 拒绝重复 key，并以节点预算限制 heap amplification。
- Byte、node、depth、string、解压后大小是不同资源维度。
- Schema、TS、Serde runtime 必须对齐，但任一生成描述都不能替代真实 reader。
- 最可靠的测试组合是 exact wire、旧 fixture、边界矩阵、over-limit 与语义/授权测试。

## 124. 本章词汇表

| 英文/代码词 | 字面意思 | 在本章中的具体含义 |
|---|---|---|
| Serialization / `Serialize` | 序列化/可序列化 | 把 Rust value 描述给 format serializer |
| Deserialization / `Deserialize` | 反序列化/可反序列化 | 从不可信 format 输入构造 Rust value |
| Serde | serialization/deserialization 框架名 | 连接 Rust types 与 JSON/TOML 等 formats 的通用 trait 系统 |
| Serializer / Deserializer | 编码器/解码器 | 将 Serde model 写成具体 format，或从 format 提供 value |
| Data model | 数据模型 | Bool、number、string、sequence、map 等双方共享的抽象类型集合 |
| Format / syntax | 格式/语法 | JSON/TOML 对 bytes/text 的具体书写规则 |
| Framing | 分帧 | 区分连续消息边界的规则，例如 JSONL newline |
| Wire shape | 线上形状 | 真正传给另一进程/版本的 key、tag、nesting 与 presence |
| Domain model | 领域模型 | 程序内部按业务含义组织的 typed values |
| `serde_json::Value` | 动态 JSON 值 | Owned JSON DOM tree，可装任意 JSON shape |
| DOM / tree | 文档对象模型/树 | 完整保存在内存中的 array/object/value 节点图 |
| Streaming decode | 流式解码 | 通过访问接口逐项读取，而非先产生另一棵完整中间树 |
| `Visitor` | 访问者 | 接收 bool/string/sequence/map 等反序列化事件的实现 |
| `DeserializeSeed` | 带种子的反序列化 | 携带剩余 budget 等外部状态参与递归 decode |
| `rename_all` | 全部重命名规则 | 统一把 Rust snake_case 映射为 camelCase 等 wire 命名 |
| `rename` / `alias` | 重命名/别名 | 指定 canonical wire name；额外接受兼容输入名 |
| `default` | 默认值 | 字段 missing 时提供 value 的反序列化规则 |
| `skip_serializing_if` | 条件跳过序列化 | 写出时满足条件便省略字段 |
| Missing / omitted | 缺失/省略 | Object 中根本没有该 key |
| Null | 空值 | Key 存在，JSON value 明确是 `null` |
| Empty collection | 空集合 | 合法存在但元素数为零的 array/map |
| Tagged enum | 带标签枚举 | 用 `type`、`method` 等 discriminator 选择 variant |
| Internally tagged | 内部标签 | Tag 与 variant fields 位于同一 object |
| Adjacently tagged | 相邻标签 | Tag 与单独 content/params 字段并列 |
| Externally tagged | 外部标签 | Variant name 作为 payload 外层 key |
| Untagged | 无标签 | 根据输入 shape 和 variant 尝试顺序推断 |
| Discriminator | 判别字段 | 决定 union/enum variant 的 `type` 或 `method` |
| `flatten` | 铺平 | 将嵌套 struct/map fields 合并进当前 object |
| Custom deserializer | 自定义反序列化器 | 手写读取、转换、兼容或字段级验证逻辑 |
| Validation / invariant | 验证/不变量 | 确认 typed value 仍满足字段间和业务规则 |
| Unknown field | 未知字段 | 输入提供但目标 type 不消费的 key |
| Duplicate key | 重复键 | 同一 JSON object 文本中多次出现同名 key |
| `serde_ignored` | Serde 忽略追踪库 | 记录未被 typed target 消费的 nested field path |
| `serde_path_to_error` | 错误路径库 | 为 nested deserialize error 保存准确字段路径 |
| Span / range | 文本跨度/范围 | 原始配置中可定位并高亮的 byte/character 区域 |
| JSONL / JSON Lines | 逐行 JSON | 每行一条完整 JSON record 的 framing |
| Record delimiter | 记录分隔符 | JSONL 中区分相邻 records 的 newline |
| Append-only | 只追加 | 不重写旧 records，只在尾部增加数据 |
| Partial/truncated line | 半写/截断行 | Crash 时只写出一部分的尾部 record |
| TOML table | TOML 表 | 由 header/key 组织的配置映射 |
| Configuration layer | 配置层 | Managed、user、project、session 等有 precedence 的来源 |
| Precedence | 优先级 | 多个 layer 对同一 key 冲突时谁覆盖谁 |
| Effective config | 生效配置 | Merge、policy、defaults 后实际供 runtime 使用的值 |
| JSON Schema | JSON 模式 | 机器可读的 JSON shape 描述与生成 fixture |
| `schemars` | Rust schema 库 | 从 Rust `JsonSchema` implementations 生成 JSON Schema |
| `ts-rs` / `TS` | TypeScript 生成库/trait | 从 Rust protocol types 生成 TS declarations |
| Schema drift | 模式漂移 | Runtime wire 与提交的 schema/TS 描述不一致 |
| Arbitrary precision | 任意精度 | 保留超出普通整数/浮点直接承载范围的 JSON number 表示 |
| Numeric overflow | 数值溢出 | 输入数值超出目标 Rust numeric type range |
| Lossy conversion | 有损转换 | 无法精确保留时丢弃、截断或降级部分信息 |
| Escaping | 转义 | 用 `\n`、`\\`、Unicode escape 表示特殊字符 |
| UTF-8 | Unicode 编码 | JSON/TOML 文本 bytes 的常见字符编码 |
| Base64 | 二进制文本编码 | 将 bytes 表达成 JSON string，会扩大体积 |
| Node budget | 节点预算 | 一条消息最多允许构造多少 JSON values |
| Depth limit | 深度上限 | Nested containers 的最大层数 |
| Heap amplification | 堆放大 | 紧凑 wire 解码成大量对象后占用远更多内存 |
| Deserialization bomb | 反序列化炸弹 | 利用深度、宽度、压缩或编码放大资源消耗的输入 |
| Canonical encoding | 规范编码 | 为签名/哈希定义唯一 bytes representation 的额外规则 |
| Round trip | 往返 | Encode 后 decode 并恢复等价值 |
| Fixture | 固定样本 | 提交在仓库中用于验证 wire/schema/旧数据的文件 |
| Runtime guard | 运行时守卫 | 真正在收到输入时执行的检查，不是静态 TS 声明 |
| Correlation ID | 关联 ID | 将 response 与原 request 对应的标识 |
| Authorization | 授权 | Typed input 成功后仍需判断当前主体是否允许执行动作 |

