# 69：Rust Enum、模式匹配与状态机——把“不可能状态”挡在类型之外

> 源码基线：`4ee41929eaf4`。本章承接第 68 章：Trait 适合开放的行为集合，enum 适合封闭的状态或消息集合。

## 1. 本章解决什么问题

你会在 Codex 中反复看到：

```rust
match status {
    AgentStatus::Completed(Some(message)) => message.clone(),
    AgentStatus::Completed(None) => String::new(),
    AgentStatus::Errored(error) => format!("Agent errored: {error}"),
    // ...
}
```

本章解释 enum 为什么能携带数据、pattern 如何同时判断和取值，以及穷尽 match 怎样帮助协议和状态随代码一起演进。

## 2. 先说人话：Enum 是“只能选一种的表格”

假设 Agent 同一时刻只能是：等待初始化、运行、被中断、完成、失败、关闭或不存在。

与其保存七个 bool，不如直接写一个 enum。一个 enum value 同时只属于一个 variant。

## 3. 为什么多个 Bool 容易制造不可能状态

```rust
struct BadStatus {
    running: bool,
    completed: bool,
    errored: bool,
}
```

它允许 `running == true` 且 `completed == true`。如果业务不允许，类型却允许，所有调用方都得防御非法组合。

## 4. 用 Enum 收窄状态空间

```rust
enum Status {
    Running,
    Completed,
    Errored,
}
```

此时单个值无法同时是两个 variant。“非法组合不可表示”比运行时检查更可靠。

## 5. Variant

`Running`、`Completed` 等叫 variant，可译为变体、分支或枚举项。

它不是独立类型；完整类型仍是 `Status`。

## 6. Unit variant

```rust
AgentStatus::Running
```

不携带额外数据，称为 unit-like variant。状态名本身已表达全部信息。

## 7. Tuple variant

```rust
AgentStatus::Errored(String)
```

像 tuple 一样按位置携带数据。失败状态与错误文字不能分离。

## 8. 嵌套 Payload

```rust
AgentStatus::Completed(Option<String>)
```

它区分：Agent 已完成，并且可能有最终消息。外层状态和内层“消息有无”是两层不同语义。

## 9. Struct variant

```rust
ThreadStatus::Active {
    active_flags: Vec<ThreadActiveFlag>,
}
```

字段有名字，适合多个 payload 字段或需要自解释的协议结构。

## 10. Enum 是 sum type

Struct 要同时拥有所有字段，可理解为 product type；enum 从多个 variant 中选一个，可理解为 sum type。

不用背数学名称，关键是“同时拥有”与“择一存在”。

## 11. 构造 Unit variant

```rust
let status = AgentStatus::Running;
```

路径 `AgentStatus::` 指明 variant 属于哪个 enum。

## 12. 构造 Tuple variant

```rust
let status = AgentStatus::Errored("network lost".to_string());
```

括号中的值成为该 variant 的 payload。

## 13. 构造 Struct variant

```rust
let status = ThreadStatus::Active {
    active_flags: vec![ThreadActiveFlag::WaitingOnApproval],
};
```

字段名也成为协议和代码的可读文档。

## 14. Pattern 不只是比较

模式可以一次完成三件事：

- 判断形状；
- 解构内部数据；
- 把内部数据绑定到新名字。

## 15. `match` 是表达式

```rust
let text = match status {
    Status::Running => "running",
    Status::Completed => "done",
};
```

Match 会产生值，不只是控制流程语句。

## 16. 每个 Arm

```rust
pattern => expression,
```

左边是 pattern，右边是匹配成功后的表达式。每一对称为 match arm。

## 17. Match 必须穷尽

编译器要求所有可能值都有处理路径。新增 variant 后，旧 match 往往直接编译失败，提醒维护者检查新语义。

## 18. Exhaustiveness 是演进提醒器

它不保证每个 arm 的业务逻辑正确，但能防止“新增状态后某些旧代码完全忘记处理”。

## 19. Wildcard `_`

```rust
_ => None
```

匹配所有尚未命中的值，并且不绑定它。

## 20. `_` 会降低新增 Variant 的提醒

使用 wildcard 后，新增 variant 可能继续落入旧默认分支，不触发编译错误。

内部封闭状态优先穷尽列举；真正需要前向兼容或只关心少数事件时再使用 `_`。

## 21. `agent_status_from_event` 为什么合理使用 `_`

`EventMsg` 很大，而绝大多数事件根本不改变 Agent 状态。函数只识别状态相关事件，其余统一返回 `None`。

这里 `_` 表达明确策略：“非状态事件无影响”。

## 22. 绑定 Tuple payload

```rust
AgentStatus::Errored(error) => error
```

`error` 不是再次查询字段，而是在 pattern 成功时直接绑定内部 String。

## 23. 嵌套 Pattern

```rust
AgentStatus::Completed(Some(message))
```

它同时匹配 `Completed` 和内部 `Some`，并取出 message。

## 24. 同一 Variant 可拆成多个 Arm

```rust
Completed(Some(message)) => message,
Completed(None) => String::new(),
```

这种写法让“完成但无消息”成为显式情况，而不是隐藏在 unwrap/default 中。

## 25. Struct destructuring

```rust
ThreadStatus::Active { active_flags } => active_flags
```

按字段名解构 struct variant。

## 26. `..` 忽略其余字段

```rust
ResponseItem::Message { role, content, .. }
```

只绑定需要的字段。它不等同于 wildcard arm；仍要求外层是 Message variant。

## 27. `field: new_name`

若要换绑定名：

```rust
Message { role: message_role, .. }
```

字段叫 role，局部变量叫 message_role。

## 28. Reference pattern 的 ownership 取决于被匹配值

匹配 `status: AgentStatus` 可能移动 payload；匹配 `&status` 或 `status: &AgentStatus` 通常只借用 payload。

先确认 scrutinee 的类型，再判断绑定得到 `String` 还是 `&String`。

## 29. Match ergonomics

Rust 会在许多引用匹配中自动调整绑定方式，所以不总需要显式写 `ref`。便利不代表 ownership 消失；IDE 类型提示很有帮助。

## 30. 避免无意移动

如果 match 后仍要使用原 enum，常见做法是：

```rust
match &status {
    AgentStatus::Errored(error) => { /* error: &String */ }
    _ => {}
}
```

## 31. `ref` Pattern

`ref name` 可显式让绑定成为引用，但现代 Rust 很多场景依靠 match ergonomics 更清楚。读旧代码时仍需认识它。

## 32. Or-pattern `|`

```rust
PendingInit | Running | Interrupted => None
```

表示多个 pattern 共享同一处理逻辑。

## 33. Or-pattern 两侧绑定必须兼容

若多个分支绑定变量，它们必须提供相同名字和兼容类型，否则右侧表达式无法统一使用。

## 34. Codex 的终态判断

`is_final` 使用：

```rust
!matches!(status, PendingInit | Running | Interrupted)
```

它把非终态集合列出，再取反。新增状态时需要重新审查它属于终态还是非终态。

## 35. `matches!`

当你只需要 bool、不需要取出 payload 时：

```rust
matches!(status, AgentStatus::Running)
```

比写一个两臂 match 更紧凑。

## 36. `matches!` 也支持 Or-pattern

```rust
matches!(status, Idle | NotLoaded)
```

`resolve_thread_status` 用它识别两种需要被真实 running fact 校正的投影状态。

## 37. `if let`

只关心一个成功形状时：

```rust
if let Some(value) = optional {
    use_value(value);
}
```

它相当于省略了“不匹配时什么也不做”的 match。

## 38. `if let` 可以解构复杂 Enum

```rust
if let ToolPayload::Function { arguments } = payload {
    // 使用 arguments
}
```

并不限于 Option。

## 39. `else if let`

可串联少量优先分支；如果分支逐渐增多，完整 match 通常更容易检查是否遗漏。

## 40. `let else`

```rust
let Some(value) = optional else {
    return;
};
```

表示：不满足期望形状就走发散路径，满足后 value 在后续作用域可用。

## 41. Else 必须 Diverge

`let else` 的 else 需要 `return`、`break`、`continue`、panic 等离开当前正常路径，不能普通执行后继续。

## 42. 为什么 Guard clause 易读

连续使用 `let else` 可以先排除不相关形状，让主逻辑保持较浅缩进。

Codex 的 rollout 过滤代码先要求 Item 必须是 Message，再继续检查 role/content。

## 43. `while let`

```rust
while let Some(item) = queue.pop() {
    process(item);
}
```

只要 pattern 持续匹配就循环，适合 iterator、queue、channel 和 stream。

## 44. `for` 背后也依赖 Pattern

```rust
for (thread_id, outcome) in shutdowns {
    // tuple destructuring
}
```

Loop variable 的位置本身就是 pattern。

## 45. 函数参数也能 Destructure

简单 tuple/struct 可在参数中解构，但大型公开 API 常保留具名参数，以免签名过密且演进困难。

## 46. `@` Binding

```rust
source @ SubAgentSource::ThreadSpawn { parent_thread_id, .. }
```

既把整个匹配值绑定为 source，又取出内部 parent_thread_id。

## 47. 为什么需要 `@`

后续代码可能既要读取某个字段，又要把完整 variant 传给 telemetry 或转换函数。`@` 避免重复匹配或重建值。

## 48. Literal Pattern

```rust
match role.as_str() {
    "system" | "developer" | "user" => true,
    "assistant" => ...,
    _ => false,
}
```

字符串字面量也能参与 pattern，但 String 通常先转换为 `&str`。

## 49. Range Pattern

```rust
11..=13 => "th"
```

`..=` 包含上下界，适合数字或 char 范围匹配。

## 50. Match guard

```rust
Some(value) if value > 0 => ...
```

先匹配形状，再检查额外布尔条件。

## 51. Guard 不参与穷尽证明的方式不同

Guard 可在运行时失败，所以带 guard 的 arm 通常不能单独证明该 pattern 已完全覆盖；仍需无 guard 的后备处理。

## 52. Pattern 与普通布尔条件的选择

优先用 pattern 表达数据形状，用 guard 表达无法自然写进 pattern 的额外关系。不要把所有逻辑都塞进巨大 guard。

## 53. Irrefutable pattern

一定匹配的模式叫不可反驳模式，例如：

```rust
let (a, b) = pair;
```

Tuple 一定有两个位置，所以不需要 else。

## 54. Refutable pattern

可能失败的 pattern，例如 `Some(x)`、某个 enum variant。它必须出现在 `match`、`if let`、`let else` 等允许失败的位置。

## 55. 为什么 `let Some(x) = value;` 报错

因为 value 可能为 None，普通 let 没有定义失败路径。改为 match、if let 或 let else。

## 56. Pattern 的绑定名会遮蔽外部变量

```rust
let status = 200;
match response {
    Http { status } => { /* 这里是字段绑定 */ }
}
```

同名 binding 会 shadow 外层变量。必要时改名避免误读。

## 57. 常量与新 Binding 的歧义

小写名字通常会成为新绑定，而不是与外部变量比较。需要匹配常量时使用清晰的常量路径和命名规范。

## 58. Destructuring assignment

Rust 也支持对已有变量进行部分解构赋值，但源码阅读中更常见的是 let/match 解构。先确认左侧是在声明还是赋值。

## 59. `Option<T>` 就是 Enum

概念上：

```rust
enum Option<T> {
    None,
    Some(T),
}
```

所以 `if let Some`、`let Some ... else` 都是普通 enum pattern matching。

## 60. `Result<T, E>` 也是 Enum

```rust
enum Result<T, E> {
    Ok(T),
    Err(E),
}
```

第 66 章的 `?` 本质上也建立在这种二选一控制流上。

## 61. Enum Method

Enum 可以有普通 impl：

```rust
impl ToolPayload {
    pub fn log_payload(&self) -> Cow<'_, str> { ... }
}
```

调用方不必到处重复 match；与 enum 自身语义紧密的转换可集中管理。

## 62. `ToolPayload::log_payload`

Function 和 Custom 的 String 可直接借用；ToolSearch 需要从 query clone 出 owned String。返回 `Cow<str>` 统一“借用或拥有”的结果。

## 63. Pattern 可决定 Ownership 策略

同一个 match 的不同 arm 可以产生 `Cow::Borrowed` 或 `Cow::Owned`，但所有 arm 的最终静态类型仍必须相同。

## 64. Match Arm 类型必须统一

若一个 arm 返回 String、另一个返回整数，除非外层再统一成某个共同类型，否则编译器拒绝。

Match 是表达式，因此它必须拥有一个确定结果类型。

## 65. `return` Arm 为什么能共存

`return None` 的类型是永不返回到当前表达式的 never type `!`，它可与其他 arm 的结果协调。

## 66. Never type

`return`、`break`、panic 等发散表达式没有正常结果值，所以能用于 let-else 失败路径和提前退出 arm。

## 67. Enum 与状态机不是同义词

一个状态 enum 只列出状态集合。真正的状态机还应定义：

- 哪些输入触发转换；
- 哪些转换合法；
- 转换时有什么副作用；
- 谁拥有当前状态；
- 如何处理重复、乱序和恢复。

## 68. `AgentStatus` 是生命周期投影

源码注释说明它由 emitted events 推导。`agent_status_from_event` 将 TurnStarted、TurnComplete、TurnAborted、Error、ShutdownComplete 投影为状态。

它不是 Agent 全部内部状态的唯一权威表示。

## 69. Event → State Projection

```text
TurnStarted       -> Running
TurnComplete      -> Completed(message?)
TurnAborted       -> Interrupted 或 Errored
Error             -> Errored(message)
ShutdownComplete  -> Shutdown
其他 Event         -> 不改变状态
```

这张表比只读 enum 定义更接近状态语义。

## 70. 同一 Event Variant 可能产生不同状态

TurnAborted 内部还有 reason。Interrupted/BudgetLimited 映射为可继续接收输入的 Interrupted，其他 abort reason 映射为 Errored。

所以状态转换需要解构嵌套 payload，而不是只看外层事件名。

## 71. Terminal state

Completed、Errored、Shutdown、NotFound 被当前 `is_final` 视为终态；PendingInit、Running、Interrupted 被视为非终态。

终态是当前函数定义的语义，不是所有系统都必须使用同一分类。

## 72. Interrupted 为什么不是终态

`AgentStatus` 注释明确它仍可能接收更多输入。这说明名称之外还要读注释和消费者逻辑。

## 73. `ThreadStatus` 是另一种投影

App-server 的 ThreadStatus 面向客户端：NotLoaded、Idle、SystemError 或 Active。Active 还能携带等待审批/等待输入 flags。

它和 AgentStatus 服务不同消费者，不能按名字硬做一一映射。

## 74. Flags 与 Variant 的组合

Active 是互斥主状态，但等待审批与等待用户输入可以同时成立，所以它们被建模为 `Vec<ThreadActiveFlag>`。

主状态用 enum，能并存的附加事实用集合，避免产生大量组合 variant。

## 75. 为什么不是 `ActiveWaitingApprovalWaitingInput`

每增加一个可组合 flag，组合 variant 数量会爆炸。结构体 variant 加 flags 更适合正交事实。

## 76. `RuntimeFacts` 与公开 Status

Thread watch 内部保存 running、pending counters、system error 等事实，再由 `loaded_thread_status` 计算公开 enum。

内部事实模型和外部投影模型可以不同。

## 77. 投影优先级

`loaded_thread_status` 的顺序表达业务优先级：未加载先返回；有 running 或 active flags 则 Active；否则 system error；最后 Idle。

交换判断顺序可能改变外部可见状态。

## 78. `resolve_thread_status` 修正观测窗口

如果已经确认有进行中的 turn，但 watch 投影暂时仍是 Idle/NotLoaded，函数返回无 flags 的 Active。

这说明状态常由多条事件流组合，不能只看 enum 定义理解一致性。

## 79. 状态转换应集中

若多个调用点各自随意改 status，新增 variant 或不变量时很难审查。集中 projection/transition function 更容易测试完整表格。

## 80. Transition Function

理想化的纯函数形状：

```rust
fn transition(state: State, event: Event) -> Result<State, InvalidTransition>
```

真实系统可能还要产出 effects，但“旧状态 + 输入 → 新状态”仍是有用阅读框架。

## 81. State 与 Effect 分离

状态计算和发送通知、写数据库、启动 task 若完全混在一起，测试困难。可先决定 transition，再由 owner 执行 effect；但要结合原子性需求设计。

## 82. 状态 Owner

看到 enum 后要继续问：谁保存当前 value？谁有权修改？是 watch channel、Mutex 内 struct、数据库，还是从 event log 临时推导？

## 83. Derived state

可以从权威事实重算的状态叫派生状态。缓存它时要考虑失效与对账，不能自动把缓存当权威。

## 84. Invalid transition

重复完成、结束后再次 Running 等输入可能是 bug、重放或乱序。策略可能是拒绝、幂等忽略、记录告警或依据 generation 丢弃。

Enum 本身不会自动阻止非法转换顺序。

## 85. Typestate

更强的做法是让不同状态成为不同类型，使只有某些方法在某状态存在。这能把更多转换约束移到编译期，但会增加泛型和 API 复杂度。

## 86. 何时普通 Enum 已足够

如果状态需存储、序列化、跨 channel 传递，或转换由运行时事件决定，enum + 集中 transition 常更实际。

## 87. Enum Dispatch 与 Trait Object

Enum 适合实现集合封闭、调用方应知道所有情况；Trait object 适合实现集合开放、调用方只依赖共同行为。

## 88. 修改 Enum 与新增 Trait 实现的方向相反

- Enum：新增操作容易集中写 match；新增 variant 会触及许多 match。
- Trait：新增实现通常容易；新增 required method 会触及所有实现。

这常被称为 expression problem 的一个工程视角。

## 89. 协议 Enum 的额外责任

如果 enum 跨进程序列化，variant 不仅是 Rust 内部选择，还对应 wire discriminator、payload shape、Schema 和其他语言客户端。

第 64 章详细讲 Serde；本章只强调新增 variant 可能是外部兼容变化。

## 90. Tagged Enum

`EventMsg` 使用 `type` 字段作为 discriminator。接收方先看 type，再按相应 variant 解析 payload。

Rust match 的穷尽性不会自动保护旧版外部客户端。

## 91. `#[non_exhaustive]`

`Op` 标记 non_exhaustive，意味着外部 crate 匹配时必须保留 wildcard，为未来新增 variant 留余地。

这是 API 演进选择，不等于内部代码永远应该忽略新 variant。

## 92. Non-exhaustive 的代价

下游无法依靠穷尽 match 获得新增 variant 编译提醒。它必须定义 unknown/fallback 行为，并通过 telemetry 或协议版本管理观察变化。

## 93. 内部 Match 尽量 Exhaustive

同一 crate 内部若能完整列出 variant，通常优先这样做。这样源码变更会把相关语义决策推到编译期。

## 94. Wildcard 不应只为消除编译错误

新增 variant 后直接补 `_ => {}` 可能隐藏行为缺口。先决定新 variant 在当前消费者中应当等同哪一类，再写明确 arm 或有注释的 fallback。

## 95. Nested Match

`keep_forked_rollout_item` 先 match `RolloutItem`，遇到 ResponseItem 后再 match 内部 ResponseItem/role。

嵌套 match 反映数据层次；若缩进太深，可提取有明确职责的函数。

## 96. Nested Pattern

也可以直接写：

```rust
RolloutItem::ResponseItem(ResponseItem::Message { role, .. })
```

它把两层 enum 形状在一个 pattern 中验证。

## 97. 大型 Or-pattern

多个 ResponseItem variant 统一返回 false，源码用 `|` 连接。它保留显式 variant 清单，因此新增 variant 会迫使重新审查。

## 98. Pattern 太大时如何读

先忽略 payload 字段，只列外层 variant → 结果；再展开少数有特殊逻辑的 variant。不要逐字符横向阅读一整屏。

## 99. Match 既是分类器也是数据转换器

有的 match 只返回 bool，有的把 Event 映射成 Status，有的把 Status 格式化成 message。先确定输出语义，再读每个 arm。

## 100. 先画 Decision Table

复杂 match 可整理成：

| 输入 variant | 附加条件 | 输出/动作 |
|---|---|---|
| TurnStarted | 无 | Running |
| TurnComplete | message 有/无 | Completed(payload) |
| TurnAborted | 特定 reason | Interrupted |
| TurnAborted | 其他 reason | Errored |
| 其他 Event | 无 | None |

这比直接修改代码更容易发现遗漏。

## 101. Compiler Error：Non-exhaustive Patterns

编译器通常会列出未覆盖 pattern。不要立刻加 wildcard；把缺失 variant 放回业务表格，决定其正确语义。

## 102. Compiler Error：Binding Type Mismatch

Or-pattern 中同名 binding 类型或绑定方式不一致。拆开 arms，检查每个 variant payload 的真实类型。

## 103. Compiler Error：Use of Moved Value

Match 按值取走非 Copy payload 后，又使用原 value。改为匹配引用、借用字段，或确实需要新 owner 时显式 clone。

## 104. Compiler Error：Refutable Pattern in Local Binding

普通 let 使用了可能失败的 pattern。补齐失败路径：match、if let 或 let else。

## 105. Compiler Error：Arm Types Incompatible

先写出每个 arm 的最终表达式类型。注意末尾分号会把表达式变成 `()`，常是意外来源。

## 106. Pattern Guard 的 Borrow 问题

Guard 在 arm body 前执行。若 pattern 已移动部分值，又在 guard/body 使用整体 value，可能产生部分移动错误。优先匹配引用或仅绑定所需字段。

## 107. Partial Move

从 struct/variant 按值取走一个非 Copy 字段后，其余未移动字段可能仍可用，但整个原值通常不能再整体使用。复杂代码中借用解构更容易维护。

## 108. 测试整个 Enum 值

仓库约定偏好深度 equality：

```rust
assert_eq!(actual, AgentStatus::Completed(Some("done".into())));
```

比逐字段断言更能证明 variant 和 payload 同时正确。

## 109. 状态机测试应覆盖转换

不要只测试 enum 常量存在。应测试：

- 关键 event → state；
- 非状态 event 不改变状态；
- payload 保留/截断规则；
- 重复、乱序或恢复行为；
- 公开 notification 只在状态变化时发送。

## 110. 新增 Variant 的检查清单

1. 搜索所有 `match`、`if let`、`matches!`。
2. 检查序列化名称和 Schema。
3. 检查 UI/CLI 展示。
4. 检查终态、重试和恢复分类。
5. 检查旧客户端和 rollout reader。
6. 更新 exact fixture、snapshot 和集成测试。

## 111. 删除 Variant 的检查清单

先证明无生产者，再停读或迁移历史数据，最后删除代码。旧 rollout、配置和客户端仍可能携带该 discriminator。

## 112. 重命名 Variant 不只是 Rust Rename

若 Serde wire name 随之改变，就可能是 breaking change。Rust 名和 wire 名可以用显式 rename 独立演进。

## 113. Pattern API 的选择表

| 需求 | 首选 |
|---|---|
| 所有情况都要处理 | `match` |
| 只关心是否属于某形状 | `matches!` |
| 命中一个形状才执行 | `if let` |
| 不命中就提前离开 | `let else` |
| 持续取出同一形状 | `while let` |
| 形状之外再加条件 | match guard |

## 114. 源码检查点

1. `codex-rs/protocol/src/protocol.rs`
   - 找 `Op`、`EventMsg`、`AgentStatus`。
2. `codex-rs/core/src/agent/status.rs`
   - 看 Event → AgentStatus projection 和 `is_final`。
3. `codex-rs/core/src/session_prefix.rs`
   - 看嵌套 Some/None 和穷尽终态格式化。
4. `codex-rs/tools/src/tool_payload.rs`
   - 看 struct variants 与 Cow 返回。
5. `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
   - 看 tagged ThreadStatus 和 Active flags。
6. `codex-rs/app-server/src/thread_status.rs`
   - 看 RuntimeFacts → ThreadStatus 以及观测窗口校正。
7. `codex-rs/core/src/agent/control/spawn.rs`
   - 看 nested pattern、or-pattern、let-else 和 `@` binding。
8. `codex-rs/core/src/context/world_state/mod.rs`
   - 看带 lifetime/generic payload 的 `PreviousSectionState`。

## 115. 搜索命令

```bash
rg -n 'enum AgentStatus|agent_status_from_event|fn is_final' codex-rs
rg -n 'enum ThreadStatus|loaded_thread_status|resolve_thread_status' codex-rs
rg -n 'enum ToolPayload|fn log_payload' codex-rs/tools
rg -n 'let .* else|if let|while let|matches!' codex-rs/core/src
rg -n '#\[non_exhaustive\]' codex-rs
```

## 116. 小实验一：把 Bool 改成 Enum

设计 Download 状态：Queued、Running、Completed(bytes)、Failed(message)。比较它与四个 bool 加两个 Option 的非法组合数量。

## 117. 小实验二：事件投影

定义 DownloadEvent，并写纯函数 `next_status(event) -> Option<Status>`。为每个 event 写表格驱动测试。

## 118. 小实验三：Ownership

分别对 `Status` 和 `&Status` match，观察 String payload 的 binding 类型，以及 match 后能否继续使用原 status。

## 119. 小实验四：穷尽演进

在 enum 新增 Paused。先不要修改 match，阅读编译器列出的影响面；再决定 Paused 是否终态，而不是加 wildcard。

## 120. 小实验五：组合 Flags

为 Running 增加 waiting_network 和 waiting_user 两个可并存事实。比较四个组合 variants 与 `Running { flags }` 的扩展成本。

## 121. 理解检查

1. `Completed(Option<String>)` 表达哪两层状态？
2. `_` 与 `..` 有什么区别？
3. `if let` 和 `let else` 分别适合什么控制流？
4. 为什么新增 variant 后编译失败通常是好事？
5. `@` binding 保存了什么？
6. Enum 为什么不能独自保证转换顺序合法？
7. AgentStatus 与 ThreadStatus 为什么不能按名字硬映射？
8. Non-exhaustive 为谁提供了什么演进空间？

## 122. 理解检查答案

1. 外层表示已完成；内层表示最终消息存在或正常缺失。
2. `_` 可匹配整个未关心值；`..` 在已确认的 tuple/struct/variant 内忽略剩余字段。
3. `if let` 适合命中才执行；`let else` 适合不命中就提前离开、成功 binding 供后文使用。
4. 它迫使所有相关消费者决定新状态语义。
5. 同时保存完整匹配值，并可继续解构其中字段。
6. Enum 限制单个值的形状，但不会记录旧状态或限制 event 顺序。
7. 两者是面向不同消费者、由不同事实推导的状态投影。
8. 让定义 enum 的 crate 未来可新增 variant；外部 crate 必须准备 fallback。

## 123. 常见误解速查

| 误解 | 更准确的理解 |
|---|---|
| Enum 只能放数字常量 | Variant 可携带 tuple/struct payload |
| Match 只是 switch | Pattern 可嵌套解构和绑定，match 还是表达式 |
| `_` 总是最佳兼容写法 | 它也会隐藏新增状态的编译提醒 |
| `..` 等于其他所有 variant | 它只忽略当前已匹配结构内的剩余字段 |
| `if let` 只适用于 Option | 它适用于任意 refutable pattern |
| 状态 enum 就是完整状态机 | 还需 transition、owner、effects 和乱序策略 |
| Flags 都该变成 variants | 能同时成立的正交事实常更适合集合 |
| Non-exhaustive 自动解决兼容 | 下游仍需有意义的 unknown/fallback 策略 |

## 124. 本章词汇表

| 英文/代码 | 字面翻译 | 在本章中的实际含义 |
|---|---|---|
| Enum | 枚举 | 多个可能形状中同时只能取一个的 sum type |
| Variant | 变体 | Enum 的一种具体情况，可携带 payload |
| Unit/tuple/struct variant | 单元/元组/结构变体 | 无数据、按位置带数据、按字段名带数据的三种形状 |
| Payload | 载荷 | Variant 内随状态或消息一起保存的数据 |
| Pattern | 模式 | 判断数据形状并可解构、绑定内部值的语法 |
| Pattern matching | 模式匹配 | 按 shape 选择分支并取出数据 |
| Scrutinee | 被检查值 | `match value` 中的 value |
| Match arm | 匹配分支 | `pattern => expression` 的一条规则 |
| Exhaustive | 穷尽 | 所有可能输入 shape 都有处理路径 |
| Wildcard / `_` | 通配符 | 匹配且忽略任何剩余值 |
| Rest pattern / `..` | 剩余模式 | 忽略当前结构内未列出的其余字段 |
| Or-pattern / `|` | 或模式 | 多个 pattern 共享一个 arm |
| Match guard | 匹配守卫 | Pattern 命中后还需满足的布尔条件 |
| Destructure | 解构 | 从 tuple/struct/variant shape 中取出组成部分 |
| Binding | 绑定 | 给 pattern 中取出的值起局部名字 |
| `@` binding | 整体绑定 | 保留完整匹配值的同时继续解构内部字段 |
| Refutable/irrefutable | 可反驳/不可反驳 | Pattern 可能失败或一定成功 |
| Match ergonomics | 匹配人体工学 | 编译器对引用 pattern 的自动调整规则 |
| Partial move | 部分移动 | 按值取走部分非 Copy 字段后原整体不可再用 |
| Never type / `!` | 永不返回类型 | return/break/panic 等不产生正常结果的表达式类型 |
| State machine | 状态机 | 状态、输入、合法转换、effects 与 owner 的完整模型 |
| Transition | 状态转换 | 在输入作用下从旧状态到新状态 |
| Terminal state | 终态 | 当前生命周期定义下不再继续运行的状态 |
| Derived state / projection | 派生状态/投影 | 从更权威事件或事实计算出的面向消费者状态 |
| Discriminator / tag | 判别字段/标签 | Wire JSON 中说明当前 enum variant 的字段 |
| `non_exhaustive` | 非穷尽 API | 要求外部消费者为未来新增 variant 保留 fallback |
| Typestate | 类型状态 | 用不同 Rust 类型在编译期限制合法操作和转换 |
| Enum dispatch | 枚举分发 | 对封闭 variant 集合进行穷尽 match 的行为选择 |

## 125. 代码单词和短语拆解

| 代码词 | 常见直译 | 在 Codex 案例中的含义 |
|---|---|---|
| `PendingInit` | 等待初始化 | Agent 尚未进入运行阶段 |
| `Running` | 运行中 | Agent 当前有活动 turn |
| `Interrupted` | 已中断 | 当前 turn 停止但 Agent 可继续接收输入 |
| `Completed` | 已完成 | 携带可选最终消息的完成状态 |
| `Errored` | 已出错 | 携带错误文字的失败状态 |
| `Shutdown` | 已关闭 | Agent 生命周期已完成 shutdown |
| `NotFound` | 未找到 | 请求引用的 Agent 不存在 |
| `Active` | 活跃 | Thread 正运行或等待交互，携带 active flags |
| `Idle` | 空闲 | 已加载但当前无活动工作或交互等待 |
| `SystemError` | 系统错误 | Thread watch 投影出的系统失败状态 |
| `WaitingOnApproval` | 等待审批 | Active thread 有未完成权限审批 |
| `WaitingOnUserInput` | 等待用户输入 | Active thread 有未完成结构化提问 |
| `from_event` | 从事件推导 | 把事件映射为可能的新状态 |
| `is_final` | 是否最终 | 判断当前状态是否属于终态集合 |
| `resolve_status` | 校正状态 | 用额外权威事实修正暂时滞后的投影 |
| `keep_item` | 保留项目 | Match 分类 rollout item 是否进入 fork history |
| `log_payload` | 日志载荷 | 从不同 tool payload 取得统一文本视图 |
| `active_flags` | 活跃标志 | 可同时存在的交互等待事实集合 |

## 126. 与前后章节的关系

- 第 26 章首次介绍 enum、match、if let 和 let else。
- 第 47 章从分布式角度解释重复、乱序、generation 和唯一终态。
- 第 64 章解释 enum 如何映射为 tagged/untagged JSON。
- 第 66 章展开 Option/Result 两个标准 enum 的错误控制流。
- 第 68 章比较封闭 enum dispatch 与开放 Trait object。

## 127. 本章结论

Enum 的价值不是少写几个 bool，而是让类型精确表达“当前只能是哪一种情况”；Pattern matching 则把形状判断、数据提取和控制流合在一个可由编译器检查的边界。

阅读 Codex 中的 enum 时，应依次追踪：variant 与 payload、所有生产者、关键消费者 match、当前状态 owner、Event→State 转换、终态分类，以及 wire/历史兼容性。只有列出状态还不算状态机；真正可靠的设计还要明确合法转换、并发顺序、effects 和恢复策略。
