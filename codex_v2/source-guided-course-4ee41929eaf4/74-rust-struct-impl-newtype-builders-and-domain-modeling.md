# 74：Rust Struct、Impl、Newtype、Builder 与领域建模

> 源码基线：`4ee41929eaf4`。本章解决“怎样把一堆基础值组织成有业务含义、难以误用、构造后可信的 Rust 类型”。

## 1. 本章解决什么问题

源码里经常同时出现 `String`、`ThreadId`、`SessionId`、`AbsolutePathBuf` 和 `ConfigBuilder`。它们底层可能仍是字符串、UUID 或路径，但类型名、字段可见性和构造方法替代码保存了额外规则。本章学习怎样读懂这些规则。

## 2. 先说人话：Struct 是贴着规则的数据盒子

Struct 不只是“把几个变量装在一起”。一个设计良好的 Struct 会同时说明：

- 这组数据合起来代表什么；
- 哪些字段必须同时存在；
- 谁能直接修改字段；
- 什么输入才允许成为有效实例；
- 实例能执行哪些领域动作。

## 3. 最小 Named-field Struct

```rust
struct User {
    name: String,
    age: u32,
}
```

`User` 是类型名，`name` 和 `age` 是具名字段。每个 `User` value 必须同时拥有一个 `String` 和一个 `u32`。

## 4. 构造 Struct Value

```rust
let user = User {
    name: "Lin".to_string(),
    age: 20,
};
```

这里不是调用函数，而是使用 struct literal。字段名让参数顺序不重要，也让调用处比 `User("Lin", 20)` 更容易阅读。

## 5. Field Init Shorthand

```rust
let name = "Lin".to_string();
let age = 20;
let user = User { name, age };
```

局部变量名和字段名相同时，`name: name` 可缩写成 `name`。这叫 field init shorthand。

## 6. 读取和修改字段

```rust
let mut user = User {
    name: "Lin".to_string(),
    age: 20,
};
user.age += 1;
```

是否能修改由整个 binding 的 `mut` 和借用状态决定；Rust 不能只把某个普通字段单独声明为 `mut`。

## 7. Struct Update Syntax

```rust
let moved = User {
    age: 21,
    ..user
};
```

`..user` 从旧值补齐未写字段。非 `Copy` 字段可能被 move，因此旧值之后不一定还能整体使用。

## 8. Tuple Struct

```rust
struct RequestId(i64);
```

Tuple struct 有类型名但字段没有业务名称，通过 `.0`、`.1` 访问。它常用于只有一个底层值的 newtype。

## 9. Unit-like Struct

```rust
struct InvalidIdentifier;
```

它没有字段，value 本身就表达一种事实。Codex Code Mode protocol 用 `InvalidIdentifier` 表达“标识符为空”这一类错误，不需要额外 payload。

## 10. Struct 与 Enum 的分工

Struct 表示“这些字段同时存在”；Enum 表示“这些形状择一存在”。例如网络审批上下文的 `host` 和 `protocol` 同时需要，适合 Struct；审批结果的多个互斥状态适合 Enum。

## 11. `impl` Block

```rust
impl User {
    fn birthday(&mut self) {
        self.age += 1;
    }
}
```

`impl User` 把行为关联到 `User`。它不会把方法数据存入每个实例；方法仍是由类型命名空间组织的函数。

## 12. Method 与 Associated Function

第一个参数是 `self`、`&self` 或 `&mut self` 的是 method，可写 `user.birthday()`。没有 self receiver 的是 associated function，通常写 `User::new(...)`。

## 13. 三种常见 Receiver

```rust
fn name(&self) -> &str;       // 共享借用
fn rename(&mut self, name: String); // 可变借用
fn into_name(self) -> String; // 消费整个值
```

先看 receiver，就能快速判断调用是否只读、是否原地修改、是否转移 ownership。

## 14. 点号调用隐藏了借用调整

`user.name()` 可能对应 `User::name(&user)`。编译器会做有限的 auto-reference 和 auto-dereference，所以读源码时要回到签名确认真实 receiver。

## 15. 一个类型可以有多个 `impl`

Rust 允许按主题拆分 inherent impl，也允许分别实现 `Display`、`Serialize` 等 Trait。搜索类型时不能只停在定义处，还要搜索 `impl Type` 和 `impl Trait for Type`。

## 16. `new` 只是约定，不是关键字

Rust 不内置 constructor 语法。`Type::new` 是常见 associated function，但也可以有 `parse`、`from_absolute_path_checked`、`empty` 或 `try_new`，名称应说明输入语义。

## 17. 私有字段是构造闸门

```rust
pub struct NonEmptyName(String);
```

外部 module 能命名 `NonEmptyName`，却不能写 `NonEmptyName(String::new())`。它只能走作者公开的构造方法，因此作者可以集中检查规则。

## 18. Invariant

Invariant 是“只要这个类型已经成功构造，就始终成立”的规则。例如：

- `ProtocolVersion` 不为零；
- `Capability` 去除首尾空白后不为空；
- `AbsolutePathBuf` 表示绝对、已规范化的路径；
- `CapabilitySet` 不含重复项。

## 19. Primitive Obsession

如果 thread ID、session ID、request ID 全部使用 `String`，编译器无法阻止传错位置。这种过度依赖裸字符串、整数和布尔值的设计常被称为 primitive obsession。

## 20. Newtype Pattern

```rust
struct ThreadId(Uuid);
struct SessionId(Uuid);
```

两者运行时布局可能完全相同，但编译器把它们视为不同类型。Newtype 用几乎零额外运行成本换取命名、隔离和不变量。

## 21. Type Alias 不等于 Newtype

```rust
type ThreadId = Uuid;
```

Alias 只是旧类型的另一个拼写，不能阻止把普通 `Uuid` 当 `ThreadId`。Newtype 才创建新的类型身份。

## 22. Codex 的 `ThreadId`

固定提交的 `codex-rs/protocol/src/thread_id.rs` 定义：

```rust
pub struct ThreadId {
    pub(crate) uuid: Uuid,
}
```

外部 crate 可以持有、复制和显示 `ThreadId`，但不能直接替换内部 UUID；protocol crate 内部仍可访问该字段。

## 23. 为什么不是 `pub uuid`

公开字段会允许调用者直接构造或修改内部表示，也会把 `Uuid` 变成 API 合同的一部分。`pub(crate)` 保留 crate 内便利，同时阻断外部对表示细节的依赖。

## 24. `ThreadId::new` 与 `from_u128`

`new()` 生成 UUIDv7；`from_u128` 从明确的 128-bit 表示构造。两个名字告诉读者“生成新身份”和“从已有值恢复”是不同意图。

## 25. Fallible Constructor

`ThreadId::from_string` 返回 `Result<Self, uuid::Error>`。任意字符串不一定是合法 UUID，所以失败被写进类型签名，而不是生成一个表面有效的占位 ID。

## 26. `Default` 的领域含义

`ThreadId::default()` 委托给 `new()`，因此默认值是新 UUID，而不是全零 UUID。`Default` 应产生可用而合理的值，不能机械地把每个字段归零。

## 27. ID 类型防止位置传错

```rust
fn load(thread: ThreadId, session: SessionId) {}
```

即使两种 ID 最终都序列化成字符串，调用 `load(session, thread)` 也无法通过类型检查。这是领域类型带来的直接收益。

## 28. `RequestId` 与 `DelegateRequestId`

Code Mode protocol 把两个 `i64` 分别包装成不同 tuple struct。一个关联 client operation，另一个关联 host delegate request；类型系统防止在相似协议字段之间串线。

## 29. `const fn new`

这两个 ID 的构造器是 `pub const fn new(value: i64) -> Self`。`const fn` 既可在普通运行时代码调用，也可能在允许的编译期常量上下文求值。

## 30. `ProtocolVersion(NonZeroU32)`

这里有两层保证：标准库 `NonZeroU32` 排除零，领域 newtype 再说明这个非零整数代表协议版本，而不是重试次数或端口。

## 31. Option 型构造器

`ProtocolVersion::new(0)` 返回 `None`，其他值返回 `Some(version)`。当失败只有“满足/不满足”且不需要详细原因时，`Option<Self>` 可以比自定义 Error 更轻。

## 32. Result 型构造器

`Capability::new` 返回 `Result<Self, InvalidIdentifier>`，因为调用者需要知道构造失败。选择 Option 还是 Result，取决于错误信息是否属于调用契约。

## 33. 私有的 `NonEmptyString`

Code Mode protocol 先定义内部 `NonEmptyString(String)`，统一执行 `trim().is_empty()` 检查；`Capability` 和 `SessionId` 再分别包装它，复用“非空”规则但保持不同业务身份。

## 34. 分层 Newtype

```text
String
  └─ NonEmptyString：保证非空
       ├─ Capability：代表能力名称
       └─ SessionId：代表会话身份
```

底层 wrapper 保存通用不变量，上层 wrapper 添加领域含义。这比在每个调用点重复 `if value.is_empty()` 更可靠。

## 35. 构造器不是唯一入口

反序列化也能创建类型。若 `Deserialize` 直接写入私有字段而不复用验证，外部数据就可能绕过 invariant。因此 `NonEmptyString::deserialize` 调回 `Self::new(...)`。

## 36. `serde(transparent)`

Newtype 在 Rust 中有独立类型，但 wire 上有时仍应表现为内部字符串或整数。`#[serde(transparent)]` 表达这种单字段透明编码；Rust 类型边界与 wire shape 可以不同。

## 37. Aggregate Invariant

单项合法不代表集合合法。`CapabilitySet` 使用 `BTreeSet<Capability>`，并由 `try_new` 拒绝输入中的重复 capability；这是跨多个元素的集合级规则。

## 38. 为什么有 Set 还主动拒绝重复

直接 `collect::<BTreeSet<_>>()` 会悄悄去重，使错误输入看似成功。`try_new` 检查 `insert` 返回值并报告 `DuplicateCapability`，保留协议错误的可见性。

## 39. 非空集合也是领域规则

`SupportedProtocolVersions::try_new` 同时拒绝 duplicate 和 empty。调用者一旦拿到该类型，就不必在每次协商前重新判断“是否至少支持一个版本”。

## 40. `AbsolutePathBuf` Newtype

`codex-rs/utils/absolute-path/src/lib.rs` 使用私有 tuple field 包装 `PathBuf`：

```rust
pub struct AbsolutePathBuf(PathBuf);
```

类型文档承诺路径绝对且经过词法规范化，但不承诺文件存在，也不承诺已经 canonicalize。

## 41. 类型名不能代替精确定义

看到 `AbsolutePathBuf` 不应自行推断“真实文件一定存在”。必须读类型文档与构造器；领域类型只保证它明确承诺的内容。

## 42. 多种 Smart Constructor

它提供 `from_absolute_path`、`from_absolute_path_checked`、`relative_to_current_dir` 和 `resolve_path_against_base`。它们都产生同一类型，但对相对路径和 base 的处理合同不同。

## 43. Smart Constructor

Smart constructor 是带验证、规范化或推导逻辑的构造函数。相对“把字段直接塞进去”，它能保证创建出的对象立即符合领域规则。

## 44. 方法也必须维护 Invariant

`AbsolutePathBuf::join`、`parent` 和 `ancestors` 返回新的 `AbsolutePathBuf`，实现中继续确保结果绝对。构造时验证一次还不够，每个 mutation 或派生入口都不能破坏规则。

## 45. Borrowing 与 Consuming 转换

```rust
fn as_path(&self) -> &Path;
fn to_path_buf(&self) -> PathBuf;
fn into_path_buf(self) -> PathBuf;
```

`as_` 通常借用，`to_` 通常创建 owned value，`into_` 通常消费 self。命名约定帮助读者预判 clone 和 ownership 成本。

## 46. Derived Trait

`#[derive(Debug, Clone, PartialEq, Eq, Hash)]` 让 Struct 获得调试、复制、比较和哈希能力。但每个 derive 都会进入类型合同，例如 `Clone` 允许复制身份值，`Ord` 决定集合排序能力。

## 47. 不要无脑 Derive

含 secret 的类型未必适合 `Debug`；代表唯一资源 ownership 的类型未必适合 `Clone`；浮点字段也会影响 `Eq`。先问语义是否成立，再问编译器能否生成。

## 48. Builder 解决什么问题

当构造参数很多、绝大多数可选，长位置参数很难阅读：

```rust
// 看不出 None 和 false 分别代表什么
Config::new(home, None, None, false, None).await?;
```

Builder 把参数变成具名步骤，并允许最后统一验证和执行昂贵工作。

## 49. Codex `ConfigBuilder`

它先保存 `codex_home`、CLI overrides、loader overrides、strict mode、thread loader 和 fallback cwd 等输入，字段全部私有。调用者通过具名方法逐项配置。

## 50. Fluent Builder

```rust
ConfigBuilder::default()
    .codex_home(home)
    .strict_config(true)
    .build()
    .await?;
```

每个 setter 接收 `mut self` 并返回 `Self`，所以调用可以链式书写。Builder 的 ownership 会沿链移动，不需要共享可变状态。

## 51. `mut self` 不等于 `&mut self`

`fn strict_config(mut self, value: bool) -> Self` 消费旧 Builder、在函数内部修改，然后返回新 value。`fn strict_config(&mut self, ...)` 则借用同一个 Builder。两种 API 的调用和临时值组合能力不同。

## 52. `build` 是提交边界

`ConfigBuilder::build(self)` 消费 Builder，解析绝对路径、加载配置层、合并 overrides 并返回 `Result<Config>`。构建失败时不会留下一个“半有效 Config”给调用者使用。

## 53. Builder 不自动保证有效

Builder 本身可以处于缺字段或冲突状态；保证通常只从成功的 `build()` 结果开始。阅读时要分别列出 Builder state 和 Product invariant。

## 54. Typestate Builder

更严格的 Builder 可以用不同泛型状态表示“必填字段尚未设置/已经设置”，从而只在完整状态提供 `build`。这种方式编译期更强，但类型和错误信息也更复杂，不必为简单配置滥用。

## 55. `ConfigEditsBuilder` 是命令计划

它不只是收集最终字段，而是按顺序收集 `Vec<ConfigEdit>`。`set_model`、`set_feature_enabled` 等方法追加编辑命令，最后一次原子应用；这里 Builder 表示待提交操作序列。

## 56. Builder 与普通 Mutable Struct 的区别

两者都能暂存字段，但 Builder 明确区分“正在准备的输入”和“构造完成的产品”。它可以隐藏产品字段、执行跨字段验证、规范化输入或产生异步结果。

## 57. 避免含糊的位置参数

`start(false, None, 3)` 很难读。可选方案包括具名 Builder method、语义 Enum、newtype 或专门入口，例如 `strict_config(true)` 比裸 `true` 更自解释。

## 58. 布尔字段何时仍合理

结构体 literal 中的 `enabled: false` 已有字段名，通常清楚；问题主要出在函数位置参数 `set(false)` 或多个相邻 bool。是否包装应看调用点的可读性和状态复杂度。

## 59. Public-field DTO

跨协议传输的 `NetworkApprovalContext { host, protocol }` 使用 public fields 很自然：消费者需要按 wire contract 读写完整数据，且字段本身就是稳定合同。

## 60. Private-field Domain Model

`ProfileV2Name(String)` 隐藏内部字符串，并通过 `FromStr` 只接受字母、数字、下划线和连字符。它代表“已验证的 profile name”，而不是任意 wire 字符串。

## 61. DTO 与 Domain Model

DTO 重点是按边界搬运数据，常见 public fields 和 Serde derives；domain model 重点是保持业务规则，常见 private fields 和 smart constructors。一个类型可以兼具两者，但必须明确验证发生在哪里。

## 62. Parse, Validate, Then Store

边界输入先是 `String`、JSON 或 TOML，经过解析与验证后才变成 `ProfileV2Name`、`ThreadId` 等可信类型。核心逻辑应尽量接收已验证类型，避免重复防御裸输入。

## 63. Parse, Don’t Validate

这句设计口号不是说“不要检查”，而是说不要检查完仍把值保存为含义模糊的 `String`。把验证结果解析成更精确的类型，后续函数签名就能利用该事实。

## 64. Struct Destructuring

```rust
let ConfigBuilder {
    codex_home,
    strict_config,
    ..
} = self;
```

Destructuring 按字段取出 ownership。Codex 的 `build_inner` 解构整个 Builder，使之后处理每项输入时不必反复写 `self.`。

## 65. 私有字段也限制 Update Syntax

外部 module 无法用 struct literal 或 `..old` 构造含私有字段的类型。这是兼容性优势：作者以后可以修改内部字段，而调用者仍走稳定 constructor/method。

## 66. Composition 而非 Inheritance

Rust Struct 没有 class inheritance。常用方式是字段组合、Trait 共享行为、newtype 委托能力。`Capability(NonEmptyString)` 就是组合已有不变量，而不是继承字符串类。

## 67. Getter 应暴露语义，不必镜像字段

`Capability::as_str()` 提供只读字符串视图；`ProtocolVersion::get()` 返回数值。Getter 不一定要叫 `get_x`，也不应自动为每个内部字段提供可变引用，否则封装会被掏空。

## 68. `Deref` 不是通用委托工具

`AbsolutePathBuf` 实现 `Deref<Target = Path>`，让许多 Path 只读能力自然可用。但为每个 newtype 都实现 Deref 会模糊领域边界；优先提供明确的 `as_` 方法，只在确实具有透明引用语义时使用 Deref。

## 69. Public Struct 的兼容成本

若外部调用者使用 struct literal，新增必填 public field 会破坏编译。稳定 API 可使用 private fields + constructor、Builder，或在 wire 类型上结合默认值和演进规则；`#[non_exhaustive]` 也能限制外部完整构造或匹配。

## 70. 测试 Invariant，而不是只测字段

有价值的测试包括：零版本被拒绝、空白 capability 被拒绝、重复 capability 被报告、反序列化无法绕过检查、相对路径按指定 base 解析。仅断言静态常量等于它的字面值通常没有收益。

## 71. 怎样阅读一个陌生 Struct

按这个顺序：

1. 找定义和字段可见性；
2. 读 doc comment 与 derives；
3. 搜索所有 inherent/Trait impl；
4. 找 constructor、parser、Deserialize 入口；
5. 看 receiver 判断 borrow、mutation、consume；
6. 找测试提炼 invariant；
7. 搜索调用点确认实际用途。

## 72. 常见编译错误翻译

- “field is private”：调用者越过了构造边界；找公开 constructor/getter。
- “mismatched types”：两个底层相似的 newtype 仍是不同领域类型。
- “use of moved value”：方法接收 self 或 update syntax 移走了字段。
- “cannot borrow as mutable”：当前只有共享引用或 binding 未声明 mut。
- “no function named new”：`new` 不是自动生成的；查看实际 smart constructor。

## 73. 设计一个新领域类型的清单

- 它与现有基础类型是否有不同业务身份？
- 哪些状态必须永远不出现？
- 字段需要 public、crate-visible 还是 private？
- 构造会成功、返回 Option，还是返回带原因的 Result？
- Deserialize、数据库加载等入口是否复用验证？
- 哪些方法借用，哪些 clone，哪些消费 self？
- Default、Clone、Debug、Eq、Hash 是否符合真实语义？
- API 未来新增字段时能否兼容？

## 74. 源码检查点

1. `codex-rs/protocol/src/thread_id.rs`：具名字段 Struct、私有表示、构造与序列化。
2. `codex-rs/code-mode-protocol/src/host/types.rs`：Request ID、ProtocolVersion、NonEmptyString、CapabilitySet。
3. `codex-rs/utils/absolute-path/src/lib.rs`：路径 newtype、smart constructors 与派生方法。
4. `codex-rs/core/src/config/mod.rs`：fluent `ConfigBuilder` 与 async build 边界。
5. `codex-rs/core/src/config/edit.rs`：以编辑序列为产品的 `ConfigEditsBuilder`。
6. `codex-rs/protocol/src/config_types.rs`：`ProfileV2Name` 的私有字段与 FromStr 验证。
7. `codex-rs/protocol/src/approvals.rs`：public-field protocol Struct。

## 75. 搜索命令

```bash
rg -n 'pub struct ThreadId|impl ThreadId|impl.*ThreadId' codex-rs/protocol/src/thread_id.rs
rg -n 'struct NonEmptyString|struct ProtocolVersion|struct CapabilitySet' codex-rs/code-mode-protocol/src/host/types.rs
rg -n 'pub struct AbsolutePathBuf|impl AbsolutePathBuf' codex-rs/utils/absolute-path/src/lib.rs
rg -n 'pub struct ConfigBuilder|impl ConfigBuilder' codex-rs/core/src/config/mod.rs
```

## 76. 小练习：从裸参数到领域类型

原设计：

```rust
fn open_session(session_id: String, protocol_version: u32) {}
```

尝试改为 `SessionId` 和 `ProtocolVersion`。思考验证应该在 `open_session` 内重复，还是在类型构造时完成；再思考来自 JSON 的输入怎样保证走同一验证。

## 77. 小练习：识别 Builder 的临时状态

阅读 `ConfigBuilder`，列出：哪些字段缺失时有 fallback，哪些输入在 `build_inner` 被规范化，哪一步之后才能称为有效 `Config`。重点不是记字段，而是找“准备阶段 → 提交边界 → 产品”的变化。

## 78. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Struct / field | 结构体/字段 | 一组同时存在的数据及其具名组成部分 |
| Named/tuple/unit struct | 具名/元组/单元结构体 | 有字段名、只有位置、没有字段的三种形状 |
| Struct literal | 结构体字面量 | 用 `Type { field: value }` 直接构造 value |
| Field shorthand/update | 字段简写/更新语法 | 同名变量省略重复，以及从旧 value 补齐字段 |
| `impl` / inherent method | 实现/固有方法 | 类型自身提供、不是来自 Trait 的方法集合 |
| Receiver | 接收者 | method 的 self、&self 或 &mut self 参数 |
| Associated function | 关联函数 | 位于类型命名空间但不接收 self 的函数 |
| Constructor / smart constructor | 构造器/智能构造器 | 创建类型，以及创建时执行验证或规范化的入口 |
| Invariant | 不变量 | 每个有效实例在生命周期内都必须保持的规则 |
| Newtype | 新类型包装 | 用单字段 Struct 为底层值建立新类型身份和规则 |
| Type alias | 类型别名 | 同一类型的另一种拼写，不创建新身份 |
| Primitive obsession | 基础类型迷恋 | 用大量裸 String/number/bool 承担不同领域含义 |
| Fallible | 可能失败的 | 返回 Option 或 Result 而非保证产生 value |
| DTO | Data Transfer Object，数据传输对象 | 为跨进程、协议或存储边界搬运字段的类型 |
| Domain model | 领域模型 | 直接表达业务身份、规则和合法操作的类型 |
| Builder / fluent API | 构建器/流式接口 | 分步收集具名输入并以链式调用生成产品 |
| Product | 产品 | Builder 最终成功创建的目标对象 |
| Typestate | 类型状态 | 用不同编译期类型表示对象所处构建或运行阶段 |
| Transparent serialization | 透明序列化 | Rust 是 wrapper，wire 上仍编码成内部单值 |
| Parse, don’t validate | 解析而非只检查 | 把已验证事实保存进更精确的类型 |
| Composition | 组合 | 将已有类型作为字段复用数据和规则 |
| Destructuring | 解构 | 按 pattern 从 Struct 中取出字段 |

## 79. 常见误解

- “有类型名就一定验证过”：要检查字段是否公开、所有构造入口是否执行验证。
- “Newtype 与 alias 一样”：alias 不建立新的编译期身份。
- “private field 只是编码风格”：它决定外部能否绕过构造规则，也影响 API 演进。
- “Builder 构造过程中一直有效”：很多 Builder 允许临时缺字段，成功 build 后的产品才可信。
- “Default 就是所有字段为零”：Default 应表达合理默认语义，也可能生成新身份。
- “Serde derive 一定维护不变量”：自定义类型需要确认 Deserialize 是否复用 smart constructor。
- “Deref 能少写代码，所以 newtype 都该实现”：过度 Deref 会重新暴露底层语义。

## 80. 一句话收束

Struct 把同时存在的数据组成一个概念，`impl` 把合法操作放在概念旁边，private fields 和 smart constructors 守住不变量，newtype 区分底层相同但业务不同的值，Builder 则把复杂输入准备与最终有效对象明确分开。
