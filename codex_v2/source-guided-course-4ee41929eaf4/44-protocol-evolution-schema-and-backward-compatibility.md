# 44：协议演进、Schema 与向后兼容测试——代码能编译，不代表旧客户端还能工作

第 9 章介绍过 app-server v2，第 29 章介绍过 Rollout 和恢复，第 34、35 章也多次要求检查 breaking change。本章把这些知识合在一起，回答一个更接近真实维护的问题：当你修改一个 Rust 类型、JSON 字段、命令行参数或配置项时，怎样判断它是否破坏了已经存在的使用者？

这里的“使用者”不只包括另一个 Rust 模块，还包括旧版桌面客户端、VS Code 扩展、自动化脚本、用户已经保存的 `config.toml`，以及几个月前写入磁盘的会话记录。

> 源码基线：`4ee41929eaf4`。本章以 `codex-rs/app-server-protocol`、`codex-rs/app-server/README.md`、CLI 的 Clap 定义、配置 schema/alias、Rollout 反序列化与 thread resume 测试为证据。官方 Codex app-server 页面在本次检索中没有返回可读取正文，因此当前仓库内部协议细节以本提交源码和随仓库维护的 README 为准，不把它们扩大解释成永远不变的产品承诺。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 解释“协议”为什么不等于“某个 Rust struct”；
2. 找出一个改动的真实消费者，而不只看编译调用点；
3. 区分源码类型、wire JSON、生成 Schema、运行行为和持久化格式；
4. 分析 old client/new server 与 new client/old server 两个方向；
5. 看懂 JSON-RPC request、response 和 notification；
6. 理解 `serde` 与 `ts-rs` rename 必须保持一致；
7. 区分 omitted、`null`、空集合和默认值；
8. 判断新增字段何时是兼容的，何时仍会破坏严格客户端；
9. 判断 enum variant、tag 和 method name 的兼容风险；
10. 理解 stable schema 与 experimental schema 的边界；
11. 使用 schema fixture 发现意外 API 漂移；
12. 解释 raw response event 为什么即使内部使用也要谨慎修改；
13. 识别 CLI、配置文件和 Rollout 也是外部契约；
14. 为配置重命名设计 alias 与迁移期；
15. 为旧 Rollout 设计 default、兼容读取或显式迁移；
16. 设计覆盖序列化、协议行为和旧数据恢复的测试；
17. 给 breaking change 制定分阶段弃用方案；
18. 在代码审查中写出具体、可验证的兼容性结论。

---

## 2. 先说人话：协议像插座，不像屋内的一根电线

你家墙里的电线可以重新布置，只要最终插座仍然提供约定的电压和孔位，旧电器就不必知道屋内发生了什么。

但是如果你把插座从两孔换成一个从未见过的形状，即使屋内电路设计得更漂亮，旧电器仍然无法使用。

软件中也一样：

```text
内部 Rust 重构
    ↓
边界转换层
    ↓
对外 JSON / CLI / config / 持久化数据
    ↓
旧客户端、脚本和旧数据
```

内部函数签名可以在一次提交中整体改完，因为编译器会指出大多数调用点。边界格式的消费者却可能：

- 不在当前仓库；
- 仍运行旧版本；
- 是用户自己写的脚本；
- 是已经躺在磁盘上的数据；
- 只在某个少见流程才被读取。

所以协议评审的核心问题不是：

> 当前代码能不能一起编译？

而是：

> 没有和本次提交一起升级的消费者，还能不能正确理解这个边界？

---

## 3. 一个改动可能同时触碰五份契约

为了避免只盯着 struct，可以把契约分成五层。

| 层 | 例子 | 主要消费者 |
|---|---|---|
| 源码类型 | Rust `ThreadStartParams` | 当前仓库编译单元 |
| Wire 格式 | JSON 的 `threadId`、method 名 | 已发布客户端 |
| 生成描述 | TypeScript、JSON Schema | 客户端开发、验证器 |
| 运行行为 | 错误码、顺序、默认值、重试语义 | UI、自动化、状态机 |
| 持久化格式 | Rollout JSONL、config.toml | 新版本程序读取旧数据 |

### 3.1 源码类型不是最终真相

Rust 中字段叫：

```rust
pub thread_id: String
```

由于 `#[serde(rename_all = "camelCase")]`，线上 JSON 是：

```json
{
  "threadId": "thread-123"
}
```

只搜索 `thread_id`，看不到 TypeScript 客户端对 `threadId` 的依赖。

### 3.2 Schema 也不是全部真相

Schema 能描述字段和类型，却不一定完整表达：

- `initialize` 必须先调用一次；
- 某通知只会在某个 capability 开启后发送；
- 同一个 thread 内事件必须保持什么顺序；
- cursor 是否只能原样传回；
- 重试相同请求是否会产生重复副作用。

这类约定属于 behavioral contract，即行为契约。

### 3.3 磁盘上的旧数据也是消费者

Rollout 文件不会“跟着代码一起重新编译”。新版本启动后读取一年前的文件，相当于：

```text
当前 reader  ←  旧版本 writer 产生的数据
```

因此持久化格式兼容可以理解成跨越时间的客户端/服务端兼容。

---

## 4. 先画兼容方向，不要笼统说“向后兼容”

“兼容”必须说明谁新、谁旧。

| 场景 | 需要回答的问题 |
|---|---|
| old client → new server | 新服务端还能接受旧请求吗？旧客户端能读新响应吗？ |
| new client → old server | 新客户端是否会发送旧服务端不认识的字段或 method？ |
| new binary → old config | 新版本还能读取用户原有配置吗？ |
| new binary → old rollout | 新版本还能恢复旧会话吗？ |
| old binary → new data | 回滚后，旧程序能否读取新程序已经写入的数据？ |

最后一行常被遗漏。发布后如果新版本开始写入一种旧版本完全无法读取的格式，那么“直接回滚二进制”可能并不能恢复服务。

### 4.1 Backward compatibility

通常指新实现继续支持旧消费者或旧数据：

```text
new reader accepts old data
new server accepts old client
```

### 4.2 Forward compatibility

通常指旧实现对未来扩展有一定容忍度：

```text
old reader safely ignores an unknown optional field
```

它不是自动成立的。严格 JSON decoder、穷举 enum 的客户端、`deny_unknown_fields` 都可能让新增字段失败。

### 4.3 Rollback compatibility

发布工程还要单独问：

```text
新版本写过数据后，旧版本是否还能接管？
```

这决定故障时能否安全回滚，还是只能向前修复。

---

## 5. JSON-RPC 的最小心智模型

Codex app-server 使用双向 JSON-RPC 2.0 风格协议，当前 README 特别说明 wire 上省略了 `"jsonrpc":"2.0"` 字段。

### 5.1 Request

请求有 `id`，调用方等待对应响应：

```json
{
  "id": 17,
  "method": "thread/start",
  "params": {
    "model": "gpt-example",
    "cwd": "/work/repo"
  }
}
```

### 5.2 Success response

```json
{
  "id": 17,
  "result": {
    "thread": { "id": "thread-123" },
    "model": "gpt-example"
  }
}
```

响应通过相同 `id` 和请求配对。

### 5.3 Error response

```json
{
  "id": 17,
  "error": {
    "code": -32602,
    "message": "invalid params"
  }
}
```

错误码、错误发生条件和 message 是否被脚本解析，也可能构成行为契约。

### 5.4 Notification

通知没有请求 `id`，接收方不返回普通 response：

```json
{
  "method": "turn/completed",
  "params": {
    "threadId": "thread-123",
    "turnId": "turn-456"
  }
}
```

不要把 notification 当成“不重要的日志”。客户端常用它驱动 UI 状态机；漏发、重复、乱序或改名都可能让界面永远停在“运行中”。

---

## 6. 当前仓库怎样集中登记协议方法

`codex-rs/app-server-protocol/src/protocol/common.rs` 中的宏集中登记：

- client → server request；
- server → client request；
- server → client notification；
- client → server notification。

概念上，每个登记项包含：

```text
Rust variant
  ↔ wire method string
  ↔ Params type
  ↔ Response type
  ↔ serialization scope
  ↔ experimental information
```

例如 method string 是协议身份的一部分：

```text
thread/start
thread/resume
turn/start
turn/completed
```

仓库约定新 v2 method 使用：

```text
<singular-resource>/<method>
```

所以是 `thread/read`，而不是随意混用 `threads/read`、`readThread` 或 `thread_read`。

### 6.1 为什么中心 registry 很重要

如果 method 名分别散落在 handler、Schema generator 和客户端类型中，很容易只改其中一个。中心 registry 让同一登记生成：

- typed request enum；
- response 映射；
- 序列化/反序列化；
- Schema export；
- experimental method 列表。

这减少漂移，但并不替代兼容性判断：中心登记可以一致地生成一个 breaking change。

---

## 7. v2 类型命名：名字本身在表达方向

仓库对 app-server v2 的约定是：

| 后缀 | 含义 |
|---|---|
| `*Params` | 请求携带的参数 |
| `*Response` | 请求成功后的结果 |
| `*Notification` | 单向事件消息 |

例如：

```rust
ModelListParams
ModelListResponse
ModelReroutedNotification
```

这些后缀不是装饰。看到 `Option<T>` 时，必须先知道它处于 request 还是 response：

- request 可选字段涉及“调用者有没有提供”；
- response 中的 `null` 是服务端明确输出的数据状态；
- notification 字段会影响客户端事件处理。

---

## 8. Rust 名字、JSON 名字和 TypeScript 名字必须对齐

典型 v2 类型：

```rust
#[derive(Serialize, Deserialize, JsonSchema, TS)]
#[serde(rename_all = "camelCase")]
#[ts(export_to = "v2/")]
pub struct ModelListParams {
    #[ts(optional = nullable)]
    pub cursor: Option<String>,
}
```

这里有三套表示：

```text
Rust:        next_cursor
JSON:        nextCursor
TypeScript:  nextCursor
```

如果针对单个字段写：

```rust
#[serde(rename = "oldName")]
```

却忘了匹配的：

```rust
#[ts(rename = "oldName")]
```

那么运行时 JSON 和生成的客户端类型就会互相说谎：TypeScript 告诉用户字段叫一个名字，服务端实际发另一个名字。

### 8.1 Tagged union 也要两边一致

判别联合通常需要明确 tag：

```rust
#[serde(tag = "type", rename_all = "camelCase")]
#[ts(tag = "type", rename_all = "camelCase")]
enum Item { ... }
```

`type` 字段决定接收方选择哪个 variant。改 tag 名、tag 值或 payload 形状都不是普通内部重构。

---

## 9. 最容易误解的四种状态：omitted、null、empty、default

假设一个字段名为 `items`，下面四种 JSON 不一定等价：

```json
{}
```

```json
{ "items": null }
```

```json
{ "items": [] }
```

```json
{ "items": ["a"] }
```

| 表示 | 常见语义 |
|---|---|
| omitted | 调用者没有指定，由服务端继承或选择默认 |
| `null` | 调用者明确表示“无值”或“清除” |
| `[]` | 调用者明确指定一个空集合 |
| 非空值 | 调用者明确选择该值 |

### 9.1 为什么 `Option<T>` 不总够用

普通 `Option<String>` 在默认 Serde 反序列化下，omitted 和 `null` 都可能变成 `None`。如果 API 需要区分：

```text
omitted → 保持现有值
null    → 清除现有值
string  → 设置新值
```

就需要三态表示。当前 `ThreadStartParams::service_tier` 使用 `Option<Option<String>>` 加定制 serializer/deserializer，正是在表达这类差异。

### 9.2 先写真值表，再选 Rust 类型

不要先看到 `Option` 就套用。应先写：

| 输入 | 产品语义 | 内部表示 |
|---|---|---|
| omitted | 使用目录默认值 | outer `None` |
| `null` | 明确不使用 tier | `Some(None)` |
| `"fast"` | 使用指定 tier | `Some(Some("fast"))` |

只有产品真的需要三态，才值得承担双层 `Option` 的复杂度。

---

## 10. `#[ts(optional = nullable)]` 在说什么

v2 client → server 的 `*Params` 中，可选字段按仓库约定标记：

```rust
#[ts(optional = nullable)]
pub cursor: Option<String>,
```

它让生成的 TypeScript 表达“调用者可以不提供，也可以提供 null”。概念上类似：

```ts
cursor?: string | null;
```

注意两个限定：

1. 这是请求参数的 wire 设计；
2. 不应机械地给所有 response 字段加同样标记。

Response 是服务端输出的确定形状。把 response 字段从 required 改成 optional，会把协议承诺变模糊，也会迫使每个客户端增加分支。

---

## 11. 新增字段一定兼容吗

常见说法是“JSON object 新增字段是兼容改动”。更准确的答案是：取决于方向和 decoder。

### 11.1 Request 新增 optional 字段

通常对旧客户端 → 新服务端较安全：旧客户端不发送，新服务端使用默认行为。

但还要确认：

- omitted 真的保留旧行为；
- 服务端没有突然把它当 required；
- 新客户端连接旧服务端时，旧服务端是否拒绝 unknown field；
- capability 或版本协商是否阻止过早发送。

### 11.2 Response 新增字段

宽松 JavaScript 客户端通常会忽略，但严格 decoder 可能拒绝 unknown field。对外协议不能仅凭“JSON 理论上可扩展”就断言安全。

### 11.3 Required 字段新增

对旧 writer/new reader 最危险：旧数据根本没有该字段。

持久化类型常通过：

```rust
#[serde(default)]
pub turn_id: String,
```

允许旧记录缺失。但空字符串必须是安全且可识别的语义；不能只为了“反序列化成功”而给一个会导致错误行为的默认值。

### 11.4 集合的 omitted 与 empty

对 request collection，仓库约定优先：

```rust
#[ts(optional = nullable)]
pub environments: Option<Vec<TurnEnvironmentParams>>,
```

而不是用 `#[serde(default)] Vec<_>` 把 omitted 偷偷压成 empty。因为当前 `ThreadStartParams::environments` 明确区分：

```text
omitted  → 选择默认 environment
empty    → 禁用 environment access
nonempty → 选择列表中的 environment
```

这正是“类型相同，不代表语义相同”的例子。

---

## 12. Bool 字段：省略应当等于 false 时

如果一个 request bool 的设计是：

```text
omitted == false
```

当前仓库常用：

```rust
#[serde(default, skip_serializing_if = "std::ops::Not::not")]
pub experimental_raw_events: bool,
```

含义是：

- 读取时缺失得到 `false`；
- 写出时 `false` 可以省略；
- `true` 明确出现在 JSON 中。

不要在产品需要“未指定 / true / false”三态时这样做。那时 `Option<bool>` 才保留了调用者是否明确选择。

---

## 13. Enum 演进比新增 object 字段更危险

假设服务端原来只发送：

```ts
type Status = "running" | "completed";
```

后来增加：

```text
"paused"
```

旧客户端可能写了穷举分支：

```ts
switch (status) {
  case "running": ...
  case "completed": ...
  default: assertNever(status)
}
```

新服务端一旦真的发送 `paused`，旧客户端就会进入异常路径。因此：

> “新增 enum variant”在 Rust 源码中看似 additive，在 wire 上却可能是 breaking。

### 13.1 评审 enum 要问四个问题

1. 新 variant 只是类型中存在，还是马上会在线上发送？
2. 是否有 capability 能保证只发给理解它的客户端？
3. 旧客户端遇到 unknown variant 会忽略、降级还是崩溃？
4. 能否先发布 reader，再发布 writer？

### 13.2 Reader-first rollout

跨版本演进常用两阶段：

```text
阶段 A：所有消费者先学会读取新旧两种表示，但生产者仍只写旧表示
阶段 B：确认消费者覆盖后，生产者开始写新表示
```

这叫 expand then contract 的前半段。直接同时改 writer 和 reader，只证明同版本互通，不能证明混合版本安全。

---

## 14. Method 名、tag 和 field rename 不是普通重命名

Rust 内部可以用 IDE 一键 rename，但 wire name 是外部身份。

### 14.1 错误做法

```text
thread/start → thread/create
```

然后删除旧 method。旧客户端仍发送 `thread/start`，新服务端会报告 method not found。

### 14.2 兼容迁移

常见方案：

1. 新服务端同时接受旧名和新名；
2. 文档和新客户端改用新名；
3. telemetry 观察旧名使用量；
4. 明确版本边界和移除窗口；
5. 最后才删除旧名。

如果没有长期支持双名的价值，也可以保留旧 method 作为薄 adapter，内部转到新 handler。

### 14.3 字段 rename 也可先做 alias

持久化数据可使用 Serde alias 兼容读取旧名，同时只写新名。配置系统也可先规范化 legacy key，再进入统一解析逻辑。

但 alias 解决的是“读取什么”，不自动解决：

- 旧版本能否读取新名字；
- 两个名字同时出现时谁优先；
- 用户是否收到迁移提示；
- 何时可以真正移除旧名。

---

## 15. 分页也是协议，不只是性能优化

当前 `ModelListParams` 展示了新 list method 的标准形状：

```rust
pub struct ModelListParams {
    pub cursor: Option<String>,
    pub limit: Option<u32>,
    pub include_hidden: Option<bool>,
}

pub struct ModelListResponse {
    pub data: Vec<Model>,
    pub next_cursor: Option<String>,
}
```

调用流程：

```text
请求 cursor = omitted
    ↓
响应 data + nextCursor
    ↓
客户端把 nextCursor 原样传回
    ↓
直到 nextCursor = null
```

### 15.1 Opaque cursor

`opaque` 表示客户端不应解析、递增或自行构造 cursor。它只应原样保存和传回。

这样服务端以后可以改变内部分页实现，而不破坏客户端。

### 15.2 分页行为契约

Schema 只告诉你 `cursor` 是 string，却不会自动保证：

- 同一项是否可能跨页重复；
- 列表变化时 cursor 是否仍有效；
- `limit=0` 怎样处理；
- 无下一页用 omitted 还是 `null`；
- cursor 是否绑定过滤条件。

这些都应在 README 或行为测试中明确。

---

## 16. Stable 与 Experimental 不是“重要”和“不重要”

当前 app-server 默认提供 stable-only API。experimental API 需要在初始化时协商：

```json
{
  "id": 1,
  "method": "initialize",
  "params": {
    "capabilities": {
      "experimentalApi": true
    }
  }
}
```

没有 opt-in 时，experimental method 或字段会被拒绝，并指出对应 descriptor 需要 `experimentalApi` capability。

### 16.1 三种 experimental 标注

概念上包括：

- 整个 method 是 experimental；
- stable method 中某个字段是 experimental；
- 字段内部嵌套类型含 experimental variant。

字段级检查要求 registry 对 params 执行 `ExperimentalApi` 检查；嵌套值也要递归检查，不能只看最外层字段名。

### 16.2 为什么初始化时一次协商

capability 在连接初始化时固定，可以让双方建立稳定假设：

```text
这个连接是否允许实验字段？
生成/处理哪些通知？
未知 experimental variant 应怎样拒绝？
```

如果每条请求临时改变，客户端状态机和服务端授权边界都会更难推理。

### 16.3 Experimental 仍然需要兼容性思维

“实验性”表示稳定性承诺较弱，不表示可以不追踪真实消费者。尤其内部客户端可能已经依赖 experimental event。修改前仍应：

- 搜索生产者与所有消费者；
- 检查部署是否允许独立升级；
- 设计明确错误或 capability；
- 更新 experimental schema 与行为测试。

---

## 17. Schema 怎样从源码生成

app-server 提供按当前 Codex 版本生成 TypeScript 和 JSON Schema 的命令：

```bash
codex app-server generate-ts --out DIR
codex app-server generate-json-schema --out DIR
```

仓库维护者使用：

```bash
just write-app-server-schema
just write-app-server-schema --experimental
```

生成文件带有：

```text
GENERATED CODE! DO NOT MODIFY BY HAND!
```

这句话的意思是：不要直接修生成结果；应修改 Rust source of truth，再重新生成。

### 17.1 Stable export

默认生成器过滤 experimental method、field 和 variant，得到稳定客户端可见的表面。

### 17.2 Experimental export

使用 `--experimental` 生成完整实验表面，用于 opt-in 客户端和仓库校验。

### 17.3 为什么两套都要测

只测 full schema 可能漏掉过滤 bug：

- stable schema 意外暴露实验字段；
- stable type 引用了已被过滤的实验类型；
- experimental schema 又漏掉应存在的字段。

所以当前 fixture tests 同时比较 stable 和 experimental 输出。

---

## 18. Schema fixture 是 API Diff 探测器

`schema_fixtures_tests.rs` 会重新生成 TypeScript/JSON Schema，并与提交进仓库的 fixture 比较。

当测试失败时，不要立即运行生成命令然后无脑接受。正确顺序是：

1. 阅读 `.diff` 或生成文件变化；
2. 找出是 method、字段、optional、enum 还是 tag 变化；
3. 判断 stable/experimental 是否符合预期；
4. 分析旧客户端与新服务端；
5. 补行为测试或迁移；
6. 最后才接受 fixture。

生成 fixture 的价值不是让 CI 变绿，而是把原本藏在 derive 宏里的 API 变化变成可审查文本。

### 18.1 Schema 测试能抓到什么

- 字段改名；
- required/optional 变化；
- 类型变化；
- enum variant 变化；
- method 登记变化；
- stable/experimental 过滤变化；
- Rust 与预计算 export 漂移。

### 18.2 Schema 测试抓不到什么

- 默认行为改变；
- 通知先后顺序改变；
- 同一请求突然产生两次副作用；
- cursor 语义改变；
- 错误码或重试语义改变；
- 恢复旧 Rollout 后上下文不正确。

因此 fixture 是必要证据，不是充分证据。

---

## 19. Raw response event：未进入公开 Schema 也不是随便改

当前 registry 包含：

```text
rawResponseItem/completed
rawResponse/completed
```

它们面向内部精确转发上游 Responses API 使用，并被排除在生成的 JSON Schema bundle 之外。`experimentalRawEvents` 还控制相关原始事件是否发出。

这容易产生一个错误结论：

> “既然没在公开 Schema 中，改了就不算 breaking。”

更安全的判断是：

1. 它是否真的有已部署消费者？
2. 消费者与服务端是否原子升级？
3. 是否原样依赖上游 item 形状？
4. 修改会不会影响事件顺序、usage 或 response ID？

内部协议仍是协议。只要两端可能独立发布，就存在版本错配窗口。

---

## 20. 行为兼容：形状不变也可能 breaking

下面改动都可能完全不改变 Schema：

- 默认 model 改了；
- omitted `cwd` 的解析位置改了；
- 以前一次通知，现在重复两次；
- `turn/completed` 提前到 item 完成之前；
- cursor 从稳定快照改成实时偏移；
- invalid params 从同步错误变成稍后 notification；
- 重试过去幂等，现在创建第二个 thread；
- 初始化前请求的错误类型改变。

### 20.1 App-server 生命周期也是契约

当前主流程是：

```text
initialize
  → initialized
  → thread/start 或 thread/resume
  → turn/start
  → 流式 notifications
  → turn/completed
```

初始化前请求会被拒绝，重复初始化也会被拒绝。即使所有 JSON 字段不变，放松或改变这些状态转换仍可能破坏依赖既有顺序的客户端。

### 20.2 测试要断言 observable behavior

不要只断言 handler 返回 `Ok(())`。应通过公共 JSON-RPC 边界断言：

- 发出了什么响应；
- notification 的 thread/turn ID；
- 顺序和终态；
- 错误是否清晰；
- 未 opt-in 时是否拒绝实验字段。

---

## 21. CLI 是给人和脚本使用的协议

Clap 定义看起来是进程入口代码，但这些都可能是外部契约：

- subcommand 名；
- `--long-flag` 与 `-s`；
- positional argument 顺序；
- default value；
- accepted values 与大小写；
- `conflicts_with` / `requires`；
- stdout、stderr 和 exit code；
- `--json` 输出结构；
- `--help` 中可发现的用法。

### 21.1 一个“只是改默认值”的例子

假设：

```text
codex exec 默认 human-readable output
```

改成默认 JSON，字段完全没有被删除，但现有 shell pipeline 可能立刻失效。

### 21.2 CLI 兼容测试

当前 `codex-rs/cli/src/main.rs` 有大量 `MultitoolCli::try_parse_from(...)` 测试，覆盖：

- flag 在不同层级的位置；
- 冲突参数；
- resume/fork 参数；
- help short-circuit；
- strict config 转发；
- app-server listen 参数。

解析测试证明 argv 映射。若改动 stdout/exit code，还需要进程级测试，不能只测 Clap struct。

---

## 22. Config 是长期存在的用户 API

配置兼容至少包含：

```text
key 名
value 类型
默认值
多来源合并优先级
strict 模式
managed requirement
CLI override
生成的 config schema
```

### 22.1 Legacy key alias

当前 `codex-rs/config/src/key_aliases.rs` 会把旧键规范化到新键，例如：

```text
[agents]
max_threads
    ↓
max_concurrent_threads_per_session
```

实现先移除 legacy key，再在 canonical key 不存在时填入。这还定义了冲突策略：如果用户同时写新旧键，新键优先。

### 22.2 为什么先 normalize 再 parse

把兼容逻辑集中在入口：

```text
各种旧写法
  → alias normalization
  → canonical ConfigToml
  → 后续业务只理解一种名字
```

这样比让每个下游模块都判断新旧 key 更容易删除，也更容易测试。

### 22.3 单值到列表的兼容读取

有些历史配置曾接受一个字符串，新设计需要字符串列表。兼容 reader 可暂时接受：

```toml
workspace_id = "a"
```

以及：

```toml
workspace_ids = ["a", "b"]
```

但不要把含糊的逗号字符串偷偷拆分，否则空格、转义和 ID 本身的逗号会产生歧义。当前实现对不安全的逗号形式给出明确错误。

### 22.4 Strict 与 non-strict

non-strict 模式可能容忍未知键，方便新旧版本交错；strict 模式会把未知字段报告为配置错误，帮助发现拼写错误。

因此“新增 config key 对旧版本是否安全”取决于用户是否会回滚到 strict 的旧版本。配置 schema 和 parser 测试都要考虑。

### 22.5 修改 ConfigToml 后

仓库要求运行：

```bash
just write-config-schema
```

更新 `codex-rs/core/config.schema.json`。Schema fixture 变化同样需要人工审查，而不是机械接受。

---

## 23. Rollout 是跨时间的 Wire Protocol

Rollout 使用逐行 JSON 记录会话。简化后的每行包含：

```text
timestamp
ordinal（较新字段，可选）
type
payload
```

`RolloutItem` 使用 tagged enum：

```rust
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum RolloutItem { ... }
```

例如 `type = "session_meta"` 决定 payload 要按 `SessionMetaLine` 读取。

### 23.1 为什么恢复比“成功反序列化”要求更高

恢复需要重建：

- thread metadata；
- 模型可见 history；
- compaction 后替换历史；
- 最近 turn context；
- permission/sandbox 信息；
- 尚未完成的状态；
- fork 所需边界。

一个旧文件能 parse 成 Rust 值，却可能重建出错误上下文。因此要从 public `thread/resume` 行为验证，而不是只写 serde round-trip。

---

## 24. 当前源码中的持久化兼容手法

### 24.1 缺字段时使用安全 default

`PatchApplyBeginEvent::turn_id`：

```rust
#[serde(default)]
pub turn_id: String,
```

源码注释明确说明用于 backwards compatibility。

适用条件：缺失值有安全、可解释的旧语义。

### 24.2 Optional 新字段

`CompactedItem` 的 window identity 字段使用：

```rust
#[serde(default, skip_serializing_if = "Option::is_none")]
pub window_id: Option<String>,
```

旧记录缺少字段时仍能读取，新记录可携带更完整的窗口链信息。

### 24.3 兼容旧字段名

`SessionMetaLine` 的自定义反序列化在缺少 `session_id` 时读取旧 `id`，再填入 canonical 字段。

这相当于：

```text
reader accepts old id and new session_id
writer uses current canonical representation
```

### 24.4 保留 compatibility-only 字段

`TurnContextItem::summary` 的注释说明：它不再用于当前上下文重建，但仍写入默认值，让旧 Codex 版本可以反序列化较新的 turn-context。

这是 rollback compatibility 的真实例子：不是新 reader 兼容旧数据，而是新 writer 暂时照顾旧 reader。

### 24.5 旧表示到新模型的转换

有些 legacy delivery item 在读取后被重建成当前模型可见的 `agent_message`。兼容层不一定原样保留旧内部类型，也可以把旧 wire 表示转换为新的 canonical model。

---

## 25. Default 不是免费的兼容魔法

给所有新增字段加 `#[serde(default)]`，确实可能让测试不再报 missing field，但可能隐藏语义错误。

例如新增：

```rust
#[serde(default)]
pub approval_required: bool,
```

默认 `false` 会不会让旧记录恢复后绕过原本需要的审批？如果答案不确定，就不能为了 parse 成功选 `false`。

选择 default 时要证明：

1. 它对应旧 writer 当时的真实行为；
2. 它不会放宽安全边界；
3. 它不会制造无法区分的状态；
4. 恢复后能产生正确的用户可见行为。

必要时应使用 `Option<T>` 保留“旧数据未知”，再由明确迁移逻辑处理。

---

## 26. 未知 Enum：容忍还是拒绝

对未来 unknown enum 有两种策略，没有一刀切答案。

### 26.1 使用 Unknown / Other

适合可以安全降级的展示性分类：

```text
未知图标类型 → 显示通用图标
```

### 26.2 明确拒绝

适合语义影响安全或历史重建：

```text
未知 history mode
未知 approval policy
未知 sandbox enforcement
```

如果程序不知道新 variant 的语义，假装成功可能比清晰报错更危险。当前 Rollout 测试就包含拒绝未知 canonical history mode 的场景。

原则是：

> 只有在能够定义安全降级行为时才容忍 unknown；否则尽早、清楚地失败。

---

## 27. 兼容迁移的 Expand–Migrate–Contract

假设要把 config key `old_name` 改为 `new_name`。

### 阶段 A：Expand reader

```text
读取 old_name
读取 new_name
冲突时 new_name 优先
写出/文档推荐 new_name
```

### 阶段 B：Migrate usage

```text
新客户端只写 new_name
向旧 key 用户发 deprecation warning
telemetry/调查确认剩余使用量
```

### 阶段 C：Contract

```text
在已声明的 breaking version 或迁移窗口后
删除 old_name reader
删除 warning 和兼容测试
保留迁移说明
```

对 method、event、字段和持久化表示也可采用相同结构。

### 27.1 为什么不要急着删除兼容层

代码已全部改用新名字，只能说明当前仓库没有旧调用点。它不能证明：

- 已发布客户端都升级；
- 用户配置都重写；
- 所有 Rollout 都经过迁移；
- 回滚窗口已经关闭。

---

## 28. 五个常见改动的风险判断

### 28.1 给 request 增加 optional `includeHidden`

较安全方案：

- omitted 保持原行为；
- 新客户端只在确认服务端能力后发送，或旧服务端容忍 unknown；
- stable/experimental 属性正确；
- Schema fixture 与行为测试更新。

### 28.2 把 response 字段 `models` 改成 `data`

这是字段 rename，对旧客户端 breaking。可先同时提供两者，或新增版本化 method；不能只改 Rust 字段名后重生成。

### 28.3 给 status enum 增加 `paused`

类型层 additive，wire 上有风险。先升级 readers/capability，再开始发送；测试旧客户端降级策略。

### 28.4 把 config 单值改成列表

reader 暂时接受两种表示，内部统一成列表；明确冲突和无效输入；生成 schema；测试旧 config。

### 28.5 Rollout 新增安全相关字段

不能随便 default false。先定义旧记录对应的真实语义，必要时标记 unknown，并从 resume 集成测试证明安全行为。

---

## 29. 兼容性测试金字塔

### 第一层：Serde / 类型级测试

验证：

- JSON wire name；
- omitted/null/value；
- tag 和 enum string；
- legacy alias；
- unknown field/variant 策略。

这层快，但只证明形状。

### 第二层：Schema golden/fixture

验证：

- TypeScript 和 JSON Schema 与 source of truth 一致；
- stable 输出不泄漏 experimental；
- expected API diff 被提交并审查。

### 第三层：公共协议集成测试

通过 app-server JSON-RPC：

```text
发送旧形状 request
  → 当前 server
  → 断言 response、notification、错误和顺序
```

App-server 改动应优先测试公共 v2 API，不要只直接调用内部 handler。

### 第四层：历史 fixture 恢复测试

保存由旧版本产生的最小 Rollout/config fixture：

```text
old bytes
  → current reader
  → thread/resume
  → 断言恢复后的完整行为
```

### 第五层：混合版本 / 回滚测试

高风险发布还要验证：

- old client + new server；
- new client + old server；
- new writer 写入后 old reader；
- 灰度期间新旧实例交错。

---

## 30. 不要只写 Round-trip 测试

下面测试很常见：

```text
current Rust value
  → current serializer
  → current deserializer
  → equal
```

它证明当前 writer 与当前 reader 自洽，却完全没有旧版本参与。

真正的兼容测试需要把一端固定为历史表示：

```json
{
  "type": "session_meta",
  "payload": {
    "id": "old-thread-id"
  }
}
```

然后验证当前 reader 把旧 `id` 解释为当前 `session_id`。

### 30.1 Fixture 要小而有出处

好的历史 fixture：

- 标注来自哪个版本或变更前格式；
- 只保留触发兼容路径所需字段；
- 不由当前 serializer 在测试运行时生成；
- 断言最终行为，不只断言 parse success。

如果用当前 serializer 生成“旧 fixture”，格式一变化，测试输入也会同步变化，从而失去防回归价值。

---

## 31. Protocol Review 的搜索路线

看到 v2 字段变化时，可按下面顺序搜索。

### 31.1 找定义和 wire annotation

```bash
rg -n 'struct ThreadStartParams|enum ThreadHistoryMode' \
  codex-rs/app-server-protocol/src
```

检查：

- `serde(rename...)`；
- `ts(rename...)`；
- `optional = nullable`；
- `experimental(...)`；
- default/alias/custom deserialize。

### 31.2 找 registry

```bash
rg -n 'ThreadStart|thread/start' \
  codex-rs/app-server-protocol/src/protocol/common.rs
```

检查 method、response、serialization scope 与 `inspect_params`。

### 31.3 找生产者和消费者

```bash
rg -n 'thread/start|ThreadStartParams' codex-rs
```

不要只看构造类型的位置，还要找字符串、生成 TS、handler 和测试。

### 31.4 看 Schema diff

运行对应 generator 后，按 API 变化审查生成文件。

### 31.5 找 public-boundary tests

优先搜索：

```text
codex-rs/app-server/tests/suite/v2/
```

例如 thread resume 应通过 JSON-RPC API 验证，而不是直接调用内部恢复函数。

---

## 32. Config 与 Rollout 的搜索路线

### 32.1 Config

```bash
rg -n 'ConfigToml|normalize_key_aliases|strict_config' \
  codex-rs/config codex-rs/core/src/config
```

需要连着看：

- 类型定义；
- 多来源 loader；
- alias normalization；
- strict validation；
- config schema；
- legacy 配置测试。

### 32.2 Rollout

```bash
rg -n 'RolloutLine|RolloutItem|SessionMetaLine|TurnContextItem' \
  codex-rs/protocol codex-rs/rollout codex-rs/core/src/session
```

再看：

```text
codex-rs/app-server/tests/suite/v2/thread_resume.rs
```

因为兼容的最终目标不是“JSON 被读出来”，而是 thread 能正确继续。

---

## 33. 一份可执行的 Breaking-change 清单

修改协议前逐项回答。

### App-server / JSON-RPC

- [ ] method name 是否变化？
- [ ] request/response/notification 方向是否变化？
- [ ] wire 字段名或 tag 是否变化？
- [ ] required/optional/nullability 是否变化？
- [ ] enum 是否新增、删除或改名？
- [ ] 默认值、错误、顺序、幂等性是否变化？
- [ ] stable 与 experimental 标注是否正确？
- [ ] raw response event 是否受影响？
- [ ] TypeScript 与 Serde rename 是否一致？
- [ ] schema fixture 是否人工审查？

### CLI

- [ ] flag、short alias、subcommand 或 positional 是否变化？
- [ ] 默认行为或 accepted value 是否变化？
- [ ] stdout/stderr/exit code/JSON 输出是否变化？
- [ ] 脚本还能否工作？

### Config

- [ ] key、类型、默认值、优先级是否变化？
- [ ] strict 与 non-strict 行为是否都考虑？
- [ ] 是否需要 alias、warning 或 migration？
- [ ] config schema 是否更新？

### Persistence / Resume

- [ ] 当前 reader 能否读取旧 Rollout？
- [ ] 缺字段的 default 是否真实且安全？
- [ ] unknown enum 是否能安全降级？
- [ ] 新 writer 是否破坏旧 reader 和回滚？
- [ ] 是否有固定旧 fixture + public resume 测试？

---

## 34. 常见错误做法

### 34.1 “Cargo test 通过，所以兼容”

当前仓库内调用点一起升级，只能证明同版本自洽。

### 34.2 “只是加了 enum variant”

旧客户端可能穷举解析，新值一旦发出就会崩。

### 34.3 “加 `serde(default)` 就好了”

反序列化成功不代表恢复语义正确，安全字段尤其危险。

### 34.4 “Experimental 可以随便改”

实验能力仍可能有独立发布的真实消费者。

### 34.5 “生成文件变了，重新生成提交即可”

Schema diff 是审查信号，不是需要消除的噪声。

### 34.6 “旧字段仓库里已经没人用了，可以删”

仓库搜索看不到已发布客户端、用户配置和磁盘历史。

### 34.7 “Round-trip 等于兼容测试”

current writer 与 current reader 同时犯同一个错，round-trip 仍可通过。

---

## 35. 实战案例：给 thread/start 增加字段

需求：允许调用者选择 `historyMode`。

### 第一步：定义省略语义

```text
omitted → 使用当前默认 history mode
```

如果 `null` 没有单独产品语义，不必设计三态。

### 第二步：选择 API 稳定级别

历史模式会影响持久化和恢复，可能先标 experimental：

```rust
#[experimental("thread/start.historyMode")]
#[ts(optional = nullable)]
pub history_mode: Option<ThreadHistoryMode>,
```

### 第三步：确保 registry 检查 params

stable method 含 experimental field 时，登记需要 `inspect_params: true`，否则未 opt-in 客户端可能绕过字段级 gate。

### 第四步：检查 enum 风险

`ThreadHistoryMode` variant 是否会出现在 response、Rollout 或仅作为 request？旧 reader 遇到未来 variant 怎样处理？

### 第五步：测试四层

1. omitted 使用旧默认；
2. 未开启 experimental capability 时拒绝字段；
3. 开启后行为正确；
4. stable schema 隐藏、experimental schema 包含；
5. 恢复持久化 thread 时 history contract 不丢失。

这说明“加一个字段”可能横跨协议 gate、Schema、Core 行为和 Rollout。

---

## 36. 实战案例：重命名配置键

需求：把含糊的 `max_threads` 改成 `max_concurrent_threads_per_session`。

### 不安全版本

直接改 `ConfigToml` 字段并删除旧名。已有配置在 strict mode 下报 unknown field；non-strict 下甚至可能静默忽略，退回错误默认值。

### 兼容版本

```text
读取 TOML
  → 在 [agents] 表内把 legacy key 映射到 canonical key
  → 若新旧同时存在，保留 canonical value
  → 用统一 ConfigToml 解析
```

测试应覆盖：

- 只有旧键；
- 只有新键；
- 新旧同时存在；
- alias 只在正确 table path 生效；
- strict mode；
- schema 只推荐 canonical key。

---

## 37. 实战案例：给 Rollout 增加字段

需求：为 compacted window 增加 `window_id`。

### 兼容读取

旧记录没有 ID，因此：

```rust
#[serde(default, skip_serializing_if = "Option::is_none")]
pub window_id: Option<String>,
```

### 行为设计

恢复旧记录时 `None` 不能被误认为某个真实 ID。需要：

- 允许旧链缺少 identity；
- 从新产生的 window 开始建立 identity；
- 不对缺失 ID 做错误去重；
- fork/compaction 逻辑明确处理 unknown。

### 测试

```text
旧 fixture 无 window_id
  → current thread/resume
  → history 正确
  → 下一次 compaction 写出当前格式
```

如果支持二进制回滚，还要测试旧 reader 能否忽略新字段；否则发布计划必须标明回滚限制。

---

## 38. 怎样写兼容性 Review Finding

差的评论：

> 这里可能不兼容。

好的评论要包含四部分：

1. 哪个外部契约变了；
2. 哪种旧消费者会触发；
3. 具体失败结果；
4. 可验证的修复方向。

示例：

> `ModelStatus` 新增 `paused` 后，服务端在未做 capability 协商的 stable notification 中开始发送该值。旧 TypeScript 客户端对 union 做穷举并在 default 分支抛错，因此灰度期间 old client/new server 会中断 turn 更新。请先把 variant gate 在 experimental capability 后，或先发布能容忍 unknown status 的 reader，并增加混合版本行为测试。

这比“enum 有风险”更容易验证和修复。

---

## 39. 源码阅读检查点

按顺序打开：

1. `codex-rs/app-server/README.md`
   - 看 transport、初始化、生命周期、Schema 命令和 experimental API。
2. `codex-rs/app-server-protocol/src/protocol/common.rs`
   - 看 request/notification registry 与 experimental 检查。
3. `codex-rs/app-server-protocol/src/protocol/v2/model.rs`
   - 看 cursor/limit/data/nextCursor。
4. `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
   - 看 optional params、bool default、三态 service tier 和实验字段。
5. `codex-rs/app-server-protocol/src/export.rs`
   - 看 stable/experimental TS 和 JSON Schema 过滤。
6. `codex-rs/app-server-protocol/src/schema_fixtures_tests.rs`
   - 看生成输出如何与 fixture 比较。
7. `codex-rs/config/src/key_aliases.rs`
   - 看 legacy key 如何统一成 canonical key。
8. `codex-rs/core/src/config/schema_tests.rs`
   - 看 config schema 漂移校验。
9. `codex-rs/protocol/src/protocol.rs`
   - 看 `RolloutItem`、`SessionMetaLine` 和兼容 default。
10. `codex-rs/app-server/tests/suite/v2/thread_resume.rs`
    - 看恢复怎样从公共 JSON-RPC 边界验证。

---

## 40. 动手练习

### 练习 1：Optional 真值表

为 `ThreadStartParams::environments` 写 omitted、null、empty、non-empty 四行表，并解释哪两项不能合并。

### 练习 2：Enum 风险

假设给一个 stable notification 的 enum 增加 `Throttled`。分别分析：

- Rust 客户端；
- TypeScript exhaustive switch；
- 宽松 JSON logger；
- 旧版本 UI。

### 练习 3：Schema Diff

任选一个 v2 params 字段，预测把 `Option<String>` 改成 `String` 后 TypeScript 和 JSON Schema 会发生什么变化，以及哪个兼容方向失败。

### 练习 4：Legacy Config

设计一个 key rename，写出只有旧键、只有新键、新旧冲突和错误 table path 四个测试输入。

### 练习 5：Old Rollout

从源码找一个带 `#[serde(default)]` 且注释说明 backwards compatibility 的字段。回答：默认值对应什么历史行为？只测 parse 是否足够？

### 练习 6：Review Finding

把“这个改动可能 breaking”改写成包含契约、触发者、后果和修复的完整 finding。

---

## 41. 理解检查

1. 为什么当前仓库全部编译通过仍不能证明协议兼容？
2. `thread_id` 与 `threadId` 分别属于哪一层？
3. omitted、null 和 empty 为什么可能是三种产品指令？
4. 为什么新增 enum variant 可能比新增 optional field 更危险？
5. `#[ts(optional = nullable)]` 为什么主要用于 client request params？
6. stable method 内的 experimental field 怎样被 gate？
7. 为什么 stable 和 experimental Schema 要分别生成、分别测试？
8. 为什么 Schema fixture 不能发现通知乱序？
9. raw response event 没进入公开 JSON Schema，为什么仍要查消费者？
10. CLI 的默认输出变化为什么属于 breaking change？
11. config alias 应在哪一层统一，为什么？
12. `serde(default)` 的值为什么必须对应真实历史语义？
13. 为什么旧 Rollout 测试不能由当前 serializer 动态生成输入？
14. 什么是 reader-first rollout？
15. expand–migrate–contract 的三个阶段分别做什么？
16. compatibility-only writer 字段在保护哪个方向？
17. unknown enum 什么时候应容忍，什么时候应拒绝？
18. 一份好的 compatibility finding 要包含哪些信息？

---

## 42. 本章词汇表

| 英文 | 字面翻译 | 在本章中的意思 |
|---|---|---|
| Protocol | 协议 | 两个可独立变化的参与者之间约定的数据和行为 |
| Wire format | 线上格式 | 真正在进程/网络边界传输的 JSON 字节形状 |
| Schema | 模式/结构描述 | 对字段、类型、required、enum 等机器可读的描述 |
| JSON-RPC | JSON 远程过程调用 | 用 method、params、id、result/error 表达双向调用的协议 |
| Request | 请求 | 带 id、期望获得 response 的调用 |
| Response | 响应 | 通过 id 与请求配对的 result 或 error |
| Notification | 通知 | 不等待普通 response 的单向事件 |
| Params | 参数 | request 携带的输入对象 |
| Registry | 登记表 | 集中映射 method、类型、response 和属性的定义 |
| Serialize | 序列化 | 把内存对象写成 JSON 等边界表示 |
| Deserialize | 反序列化 | 把边界数据读取为内存对象 |
| Serde | 序列化/反序列化框架 | Rust 中控制 JSON 名字、默认值、tag 和 alias 的库 |
| ts-rs / TS | TypeScript 生成 | 从 Rust 类型导出客户端 TypeScript 类型 |
| JsonSchema | JSON 结构模式 | 用于验证和描述 JSON payload 的 Schema |
| camelCase | 驼峰命名 | 如 `threadId`，app-server v2 常用 wire 命名 |
| snake_case | 蛇形命名 | 如 `thread_id`，Rust 字段和部分持久化 tag 常用 |
| Tagged union | 带标签联合 | 用 `type` 等判别字段选择 enum variant |
| Variant | 变体 | enum 中的一种可能值或数据形状 |
| Omitted | 省略 | JSON object 中完全没有这个 key |
| Null | 空值 | key 存在且值明确为 `null` |
| Nullable | 可为 null | Schema/TS 允许字段值为 null |
| Optional | 可选 | 调用者可以不提供字段 |
| Default | 默认值 | 输入缺失时 reader 或业务选择的值 |
| Alias | 别名 | reader 仍接受的旧字段/配置名称 |
| Canonical | 规范形式 | 兼容输入统一转换后的当前标准表示 |
| Backward compatible | 向后兼容 | 新实现继续读取/服务旧数据或旧消费者 |
| Forward compatible | 向前兼容 | 旧实现能安全容忍部分未来扩展 |
| Rollback compatible | 回滚兼容 | 新版本写过数据后旧版本仍能接管 |
| Breaking change | 破坏性变更 | 让既有消费者或数据不再正确工作的改动 |
| Consumer | 消费者 | 读取协议、事件、CLI 输出、配置或持久化数据的一方 |
| Producer / Writer | 生产者/写入者 | 发送或写出某种表示的一方 |
| Reader | 读取者 | 解析并解释既有表示的一方 |
| Capability | 能力声明 | 初始化时协商客户端是否理解某类 API |
| Experimental | 实验性 | 稳定承诺较弱且需 opt-in 的 API 表面 |
| Gate / Gating | 门控 | 在 capability 未开启时拒绝或隐藏实验能力 |
| Stable-only | 仅稳定 | 过滤实验 method/字段后的默认 API 表面 |
| Fixture | 固定样本 | 提交到仓库用于防止格式漂移的预期文件 |
| Golden test | 黄金文件测试 | 将当前输出与审核过的固定输出比较 |
| Opaque cursor | 不透明游标 | 客户端只能原样传回、不能解释的分页位置 |
| Pagination | 分页 | 用 cursor/limit 分批返回列表 |
| Idempotent | 幂等 | 相同操作重试不会产生额外不同副作用 |
| Behavioral contract | 行为契约 | Schema 之外的默认值、顺序、错误和状态转换约定 |
| Legacy | 遗留/旧版 | 为历史客户端、配置或数据保留的表示 |
| Deprecation | 弃用 | 仍可用但已声明未来移除的 API |
| Migration | 迁移 | 把消费者或数据从旧表示转到新表示 |
| Expand–Migrate–Contract | 扩展—迁移—收缩 | 先兼容新旧，再迁移使用，最后删除旧路径 |
| Round-trip | 往返 | 当前 serializer 写出后再由当前 reader 读回 |
| JSONL | 逐行 JSON | 每行一个 JSON object 的持久化格式 |
| Rollout | 会话流水记录 | 用于恢复、审计和 fork 的持久化 thread 数据 |
| Resume | 恢复 | 从持久化记录重建 thread 并继续工作 |
| Strict config | 严格配置 | 对未知或无效配置字段明确报错的模式 |
| CLI | 命令行界面 | flag、subcommand、输出和退出码组成的脚本契约 |

---

## 43. 本章小结

协议演进最重要的转变，是不再把“能编译”当成“兼容”。

一次看似很小的类型改动，可能同时改变：

```text
Rust source type
  → JSON wire shape
  → TypeScript / JSON Schema
  → runtime behavior
  → config / Rollout persistence
```

安全演进的基本方法是：

1. 先列出生产者、消费者和新旧版本方向；
2. 明确定义 omitted、null、default 和 unknown 的语义；
3. 让 Serde、TypeScript 与 Schema 对同一 wire name 保持一致；
4. 用 capability 隔离 experimental 表面，但仍追踪真实消费者；
5. 把 Schema fixture 当 API diff 人工审查；
6. 对 config 和 Rollout 使用 alias、兼容 reader、迁移和安全 default；
7. 用固定旧 fixture 和公共边界测试，而不只做同版本 round-trip；
8. 采用 reader-first 与 expand–migrate–contract，避免新旧版本交错时突然断裂；
9. 发布前同时评估向后兼容、向前兼容和回滚兼容。

当你能对一个字段准确说出“谁写、谁读、wire 上叫什么、缺失时是什么意思、旧版本会怎样、由哪一层测试证明”，你才真正完成了协议评审。
