# 75：Rust From、TryFrom、AsRef、Borrow、Cow 与类型转换

> 源码基线：`4ee41929eaf4`。本章解决“两个类型之间怎样转换、会不会失败、会不会分配、会不会转移 ownership，以及应该选择哪个 Trait”。

## 1. 本章解决什么问题

Rust 源码里到处是 `.into()`、`.try_into()?`、`.as_ref()`、`.borrow()`、`.to_owned()` 和 `Cow::Borrowed`。它们都像“换一种形式”，但成本和保证完全不同。本章建立一套看到签名就能判断语义的方法。

## 2. 先说人话：转换不是一种动作

“转换”至少可能表示：

- 换一个类型名但保留全部信息；
- 验证外部输入并建立领域类型；
- 临时借用另一种视图；
- 消费旧 value，转交其内部 allocation；
- clone 出新的 owned value；
- 格式化成人类可读文本；
- 有损替换无法表示的内容。

先区分这些动作，才知道该读哪个 Trait。

## 3. 一张总览表

| 需求 | 常见接口 | 失败 | 常见 ownership/分配 |
|---|---|---|---|
| 明确、可靠的 owned 转换 | `From` / `Into` | 否 | 可能 move，也可能分配 |
| 需要验证的 owned 转换 | `TryFrom` / `TryInto` | 是 | 依实现而定 |
| 借用一种廉价视图 | `AsRef` | 否 | 通常不分配 |
| 借用并保持 Eq/Hash 语义 | `Borrow` | 否 | 不分配 |
| 从借用创建 owned value | `ToOwned` | 否 | 通常分配或 clone |
| 借用或拥有二选一 | `Cow` | 否 | 快路径可不分配 |
| 从文本解析 | `FromStr` / `parse` | 是 | 依类型而定 |

## 4. `From<T> for U`

```rust
impl From<ThreadId> for String {
    fn from(value: ThreadId) -> Self {
        value.to_string()
    }
}
```

它声明从 `ThreadId` 到 `String` 的标准、不可失败转换。调用者可写 `String::from(id)`。

## 5. `Into` 通常自动获得

标准库提供 blanket implementation：实现 `From<T> for U` 后，通常自动获得 `Into<U> for T`。因此调用者也可写：

```rust
let text: String = id.into();
```

一般优先实现 From，让正反两种调用风格都可用。

## 6. `.into()` 为什么有时需要类型提示

`.into()` 只说明“转换成上下文要求的目标类型”。若上下文没有唯一目标，编译器无法推断：

```rust
let text: String = id.into();
```

左侧 `String` 消除了歧义。排错时把 `.into()` 暂时展开为 `String::from(id)` 往往更清楚。

## 7. `From` 应当不可失败

如果某些输入不能合法转换，就不应 panic、悄悄塞默认值或丢弃错误来实现 From。应改用 `TryFrom`、明确的 parse 函数，或更精确的输入类型。

## 8. `From` 不代表“零成本”

`From<ThreadId> for String` 需要格式化 UUID 并创建字符串。From 只承诺转换不会报告失败，并不承诺不分配、O(1) 或没有复制。

## 9. `From` 的方向很重要

`From<AbsolutePathBuf> for PathBuf` 是安全的“放宽”：绝对路径当然也是 PathBuf。反方向不是每个 PathBuf 都满足绝对路径规则，所以必须 TryFrom。

## 10. 信息与保证可以减少

把 `AbsolutePathBuf` 变成 `PathBuf` 不丢路径字节，却丢失了类型层面的“绝对路径”保证。转换后调用者不能再依赖该 invariant，除非重新验证。

## 11. `TryFrom<T> for U`

```rust
impl TryFrom<&str> for ThreadId {
    type Error = uuid::Error;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::from_string(value)
    }
}
```

它把验证失败写入 Result。成功的 `ThreadId` 才携带“这是合法 UUID”这一事实。

## 12. `TryInto` 的自动关系

实现 `TryFrom<T> for U` 后，调用方通常可写：

```rust
let id: ThreadId = raw.try_into()?;
```

`?` 将转换错误沿当前函数返回；目标类型仍常由左侧或函数参数推断。

## 13. `TryFrom<&str>` 与 `TryFrom<String>`

Codex 的 `ThreadId` 两者都实现：借用字符串时无需交出 ownership；传入 String 时则消费调用方的 String。两条转换最终复用同一个 `from_string(&str)` 验证逻辑。

## 14. 消费输入不保证复用 Allocation

虽然 `TryFrom<String>` 接收 owned String，ThreadId 内部保存的是 UUID，不是原字符串，所以解析后旧字符串仍会被释放。签名中的 ownership 与是否复用 buffer 是两个问题。

## 15. `TryFrom` Error 是合同的一部分

`type Error = uuid::Error` 让调用者能传播或分类失败。公共 API 更换 Error 类型可能影响 match、conversion 和用户诊断，不能只看成功值。

## 16. `Infallible`

泛型转换有时统一返回 Result，但某个具体方向实际上不会失败，错误类型可能是 `std::convert::Infallible`。它表示没有可构造的错误情况，而不是运行时吞掉错误。

## 17. `FromStr`

```rust
let uri: PathUri = "file:///tmp/a".parse()?;
```

`.parse()` 依赖 `FromStr`。它适合“文本语法 → typed value”，通常可失败，并由目标类型决定如何解释字符串。

## 18. `parse()` 也需要目标类型

```rust
let uri = "file:///tmp/a".parse::<PathUri>()?;
```

若左侧或返回类型不能推断目标，就使用 turbofish `::<PathUri>`。它不是额外转换，只是告诉编译器选择哪个 FromStr 实现。

## 19. `ThreadId::from_string` 与 Trait 入口

具名函数可突出领域语义，TryFrom 让泛型代码使用统一接口。两者可以共存，但应共享一份验证，避免不同入口接受范围不一致。

## 20. `PathUri::parse`

`PathUri` 只接受有效 `file:` URI。它的 `FromStr` 调回 `Self::parse`，Deserialize 也调用 parse；命令行、程序调用和 wire 输入因此复用同一验证边界。

## 21. `TryFrom<Url> for PathUri`

已经是 `Url` 仍不代表满足 PathUri：scheme 可能是 `https`，file URL 结构也可能无效。所以转换继续返回 `PathUriParseError`，并执行 scheme、authority 和路径验证。

## 22. `From<AbsolutePathBuf> for PathUri`

这条转换却是不可失败的。原因不是所有系统路径都天然能表示成普通 URI，而是实现对无法直接表达的路径使用保留的 opaque fallback URI 编码，因此仍能保证产生 PathUri。

## 23. API 设计可以改变转换类别

若 PathUri 没有 fallback，绝对路径到 URI 可能必须 TryFrom。加入无碰撞的保底表示后，同一领域动作可以成为 From。是否 fallible 取决于完整合同，而不只取决于底层库函数。

## 24. 不要让 From 做隐蔽的有损转换

不可失败不等于可以静默丢数据。若转换会截断、替换或选择含糊默认值，最好用明确动词，如 `to_string_lossy`、`truncate_to_limit`，让调用点看见代价。

## 25. `AsRef<T>`

```rust
impl AsRef<Path> for AbsolutePathBuf {
    fn as_ref(&self) -> &Path {
        &self.0
    }
}
```

AsRef 提供廉价共享借用视图。它不消费 AbsolutePathBuf，也不创建新的 PathBuf。

## 26. `impl AsRef<Path>` 参数

```rust
fn inspect(path: impl AsRef<Path>) {
    let path: &Path = path.as_ref();
}
```

调用者可以传 PathBuf、&Path、&str 等支持该视图的类型。函数内部应尽早 `.as_ref()`，之后按统一 `&Path` 工作。

## 27. 泛型参数的真实含义

`P: AsRef<Path>` 不是“P 是 Path 的子类”，Rust 也没有这种继承。它只说明 P 能可靠、廉价地提供一个 `&Path` 视图。

## 28. AsRef 通常不用于复杂计算

AsRef 应是轻量引用转换。若每次调用都要解析、分配或可能失败，应使用具名方法或 TryFrom，而不是把昂贵工作藏在 `as_ref()` 中。

## 29. `AsMut<T>`

AsMut 是可变视图的对应 Trait，返回 `&mut T`。它会让调用者直接修改底层表示，所以对带 invariant 的 newtype 要格外谨慎：公开 AsMut 可能绕过所有验证。

## 30. `as_`、`to_`、`into_` 命名约定

- `as_path(&self) -> &Path`：借用视图；
- `to_path_buf(&self) -> PathBuf`：从借用创建 owned value；
- `into_path_buf(self) -> PathBuf`：消费 self 并取出 owned value。

这套约定不是编译器规则，但在 Rust API 中非常重要。

## 31. `to_` 往往意味着复制或分配

`AbsolutePathBuf::to_path_buf(&self)` clone 内部 PathBuf。原值继续可用，但出现第二个 owned buffer。性能排查时要留意循环中的 `to_...`。

## 32. `into_` 往往能复用 Ownership

`into_path_buf(self)` 直接移出内部 PathBuf，通常无需复制其路径 buffer；代价是原 AbsolutePathBuf 被消费，且类型保证随 wrapper 一起消失。

## 33. `as_` 不等于强制类型转换 `as`

方法名前缀 `as_path()` 是命名约定；Rust 运算符 `value as u32` 是语言级 cast，主要用于数值和指针等有限转换。不要把两者混为一谈。

## 34. `Borrow<T>`

Borrow 也返回借用视图：

```rust
trait Borrow<Borrowed> {
    fn borrow(&self) -> &Borrowed;
}
```

但它比 AsRef 带有更强的语义预期：owned 与 borrowed 形式的 Eq、Ord 和 Hash 行为应一致。

## 35. Borrow 为什么常见于 Map Lookup

`HashMap<String, V>` 可用 `&str` 查找，是因为 String 可 Borrow<str>，并且相等与哈希语义一致。这样无需为了查询临时分配 String。

## 36. AsRef 与 Borrow 怎样选择

只需要一般输入适配时用 AsRef；当 borrowed key 必须与 owned key 在 Eq/Hash/Ord 上等价时用 Borrow。不要仅因为两个 Trait 都能返回引用就随意互换。

## 37. Codex 的 `HistoryItemGroup<T>`

它约束 `T: Borrow<ResponseItem>`。T 可以是 owned ResponseItem，也可以是适合的借用/包装形式；估算 token 和识别附加 notice 时统一取得 `&ResponseItem`。

## 38. Borrow 不改变序列元素 Ownership

`history_item_groups` 最终仍产出原来的 `I::Item`，只在检查阶段短暂 borrow。泛型函数既能读统一视图，又不会强迫调用方把元素 clone 成统一 owned 类型。

## 39. `Deref` Coercion

`String` 在需要 `&str` 时常能通过 Deref coercion 工作，`AbsolutePathBuf` 也实现 `Deref<Target = Path>`。这是编译器在引用上下文中的自动调整，不等于发生 From/Into 转换。

## 40. Deref 与 AsRef 的边界

Deref 让 wrapper 像目标引用一样使用，影响方法查找和 coercion；AsRef 是显式的泛型转换合同。API 参数通常写 AsRef，领域 newtype 是否实现 Deref 则需更谨慎。

## 41. `&String`、`&str` 与 `String`

- `String`：拥有可增长 UTF-8 buffer；
- `&String`：借用具体 owned 容器；
- `&str`：借用 UTF-8 text slice。

只读文本参数通常优先 `&str`，因为既能接 String 的切片，也能接字符串字面量。

## 42. `PathBuf` 与 `Path`

关系类似 String 与 str：PathBuf 拥有路径 buffer，Path 是借用的路径 slice。需要保存时获取 owned PathBuf，只做检查时接收 `&Path` 或 `impl AsRef<Path>`。

## 43. `Clone` 与 `ToOwned`

Clone 从 `&T` 产生同类型 T；ToOwned 可以从 borrowed form 产生对应 owned form，例如 `str.to_owned()` 得到 String，`Path.to_owned()` 得到 PathBuf。

## 44. `ToOwned` 是 Cow 的基础

`Cow<'a, B>` 要求 borrowed 类型 B 能通过 ToOwned 得到 owned 类型。对 `str`，两个 variant 分别大致装 `&'a str` 和 String。

## 45. `Cow` 是什么

Cow 是 Clone-on-Write 的缩写，但实际阅读中先把它理解为“返回值可能借用输入，也可能拥有新值”的 enum：

```rust
Cow::Borrowed(&value)
Cow::Owned(new_value)
```

## 46. Cow 解决的典型分支

某函数多数时候原样返回输入，少数时候需要修改：总是返回 owned 会让快路径也分配；只返回 borrow 又无法容纳新生成结果。Cow 用一个类型覆盖两条路径。

## 47. `ToolPayload::log_payload`

Function 和 Custom payload 已经存有可借用字符串，因此返回 `Cow::Borrowed`；ToolSearch 需要从结构中取得并拥有 query clone，因此返回 `Cow::Owned`。调用者只需处理统一的字符串视图。

## 48. Borrowed Variant 的 Lifetime

`Cow<'_, str>` 中的 `'_` 由编译器推断，并与 `&self` 借用相关。若结果是 Borrowed，它不能活得比 ToolPayload 更久；Owned variant 虽有自己的 String，返回类型仍受该统一 lifetime 表达约束。

## 49. `Cow::as_ref()`

无论 variant 是 Borrowed 还是 Owned，都可取得 `&str`。只读消费者通常无需 match；只有要判断是否分配、提取 ownership 或修改时才关心 variant。

## 50. `into_owned()`

它把 Cow 变成 owned value：Owned 时移出已有值，Borrowed 时调用 ToOwned/clone。调用点写 `into_owned()` 就是在说“从这里开始必须拥有，必要时接受分配”。

## 51. Cow 不保证整个流程零分配

Owned 分支已经分配，Borrowed 在之后 `into_owned()` 也会分配。Cow 只让“无需修改且只需借用”的路径有机会避免 clone。

## 52. Clone-on-write 的修改语义

`Cow::to_mut()` 在 Borrowed 状态首次需要修改时 clone 为 Owned，之后返回可变引用。若代码从不修改，借用可保持到底。

## 53. 路径规范化的 Cow 案例

`AbsolutePathBuf` 的 `normalize_path_for_platform` 在无需 Windows device-path 变换时返回 `Cow::Borrowed(path)`；只有确实产生不同路径表示时才构造 `PathBuf` 并返回 Owned。

## 54. `flat_tool_name` 的 Cow 案例

默认 namespace 或没有 namespace 时直接借用现有 name；需要拼接 namespace 与 name 时才预分配 String。返回类型把“可能需要组合”这一成本事实暴露出来。

## 55. `String::from_utf8_lossy`

它返回 `Cow<str>`：字节本来就是合法 UTF-8 时可以借用；出现非法序列时需要分配并插入替换字符。函数名中的 `lossy` 明确警告信息可能改变。

## 56. Lossy 不应伪装成 Parse

严格协议、身份和签名材料通常必须拒绝非法编码；诊断日志为了可显示可能接受 lossy。是否允许替换取决于边界用途，而不是哪个 API 写起来更短。

## 57. `map` 不是类型转换 Trait

Option/Result/Iterator 的 `map` 是容器内部 value 的变换：

```rust
let parsed = raw.map(ThreadId::try_from);
```

先判断外层容器，再判断内部转换是否 fallible，避免把 `Option<Result<T, E>>` 看成普通 T。

## 58. `transpose`

`Option<Result<T, E>>` 可 transpose 为 `Result<Option<T>, E>`。它不验证 T 本身，而是重新排列“缺失”和“失败”两层控制流，相关基础已在错误专题学习。

## 59. `collect` 也能执行转换管线

Iterator 中逐项 `map(TryFrom::try_from)` 后，可 `collect::<Result<Vec<_>, _>>()`。任何一项失败都会短路；这适合把一组 raw DTO 转为全部可信的 domain values。

## 60. `?` 与错误转换

在 Result 上使用 `?` 时，错误可能通过 From 转为当前函数的 Error。看到错误类型似乎不同却能传播，应搜索 `impl From<SourceError> for TargetError` 或 derive 生成的转换。

## 61. Conversion Chain

一行 `raw.try_into()?.into()` 可能先验证、再放宽表示。调试时拆成有名称的局部变量：

```rust
let id = ThreadId::try_from(raw)?;
let text = String::from(id);
```

这样更容易看清失败点、move 和目标类型。

## 62. 转换链会丢失类型证据

`AbsolutePathBuf -> PathBuf -> String` 逐步丢失“绝对路径”和“路径字节不一定 UTF-8”的信息。若后续仍需要这些保证，应尽量延迟转换到普通 String。

## 63. Boundary Conversion

推荐在系统边界集中转换：输入边界 raw → validated domain；核心逻辑保持精确类型；输出边界 domain → DTO/wire/display。中间频繁往返会增加验证、分配和语义丢失。

## 64. Conversion 与 Formatting 不同

`Display` 负责面向人或稳定文本表示的格式化，`Debug` 面向开发诊断，Serialize 面向 wire contract。`to_string()` 调 Display，不应默认拿 Debug 文本当协议格式。

## 65. Conversion 与 Serialization 不同

From/TryFrom 在 Rust value 间工作；Serde 跨 data model/wire 工作。两者应共享验证语义，但序列化 shape 可能与内部结构完全不同，例如 ThreadId 内部是 UUID，JSON 中是字符串。

## 66. Orphan Rule 对转换的影响

实现 Trait 时，Trait 或 involved type 至少有一个必须是当前 crate 本地定义。不能任意为两个外部类型补 `From`；常见解决方法是引入本地 newtype 或写本地具名函数。

## 67. Blanket Impl 冲突

标准库已提供许多 From/Into、TryFrom/TryInto 和引用实现。新增过宽泛型 impl 可能与现有或未来实现重叠。遇到 conflicting implementations，要检查 blanket bounds，而不只检查完全相同的文本。

## 68. API 参数选择清单

- 只读文本：优先 `&str`；
- 只读路径且愿意接多种输入：`impl AsRef<Path>`；
- 需要保存输入：接 owned T，或明确 clone；
- 需要 hash/equality 等价的 borrowed key：Borrow；
- 输入可能无效：TryFrom/parse；
- 大多数情况借用、少数生成新值：Cow。

## 69. API 返回值选择清单

- 返回内部只读视图：`&T` / `as_...`；
- 交出内部 ownership：T / `into_...`；
- 总要新建 owned value：T / `to_...`；
- 可能失败：Result 或 Option；
- 借用与新建二选一：Cow；
- 不要仅为方便而返回对可变内部表示的引用。

## 70. 性能阅读清单

看到转换时依次问：输入是 move 还是 borrow？是否 clone？是否分配？是否遍历全部内容？失败是否保留原输入？循环里是否重复转换？结果马上又被转回原类型吗？

## 71. 常见编译错误翻译

- “type annotations needed”：`.into()`、`.parse()` 的目标类型不明确。
- “trait bound X: From<Y> is not satisfied”：不存在该标准转换或方向写反。
- “the trait TryFrom is not implemented”：该验证必须走具名函数，或输入类型不匹配。
- “borrowed value does not live long enough”：返回的 AsRef/Cow 借用超过 owner 生命周期。
- “use of moved value”：`into_...` 或 owned conversion 消费了原值。

## 72. 测试转换的重点

- 合法值 round trip 后身份是否保持；
- 非法值是否在正确边界失败；
- Deserialize 是否与 TryFrom/FromStr 接受同一范围；
- equality/hash 是否与 Borrow view 一致；
- Cow 快路径是 Borrowed，变换路径是 Owned；
- lossy 转换是否只用于允许替换的用途。

## 73. 怎样阅读陌生 `.into()`

1. 从赋值、参数或返回值确定目标类型；
2. 搜索 `impl From<源> for 目标`；
3. 看函数接收 owned 还是 reference；
4. 检查实现是否分配、格式化或丢保证；
5. 若有 `?`，再追 Error 的 From 链；
6. 必要时把表达式拆成显式局部变量。

## 74. 源码检查点

1. `codex-rs/protocol/src/thread_id.rs`：TryFrom<&str/String> 与 From<ThreadId> for String。
2. `codex-rs/utils/absolute-path/src/lib.rs`：AsRef<Path>、From、四种 TryFrom、as/to/into 和 Cow 路径规范化。
3. `codex-rs/utils/path-uri/src/lib.rs`：TryFrom<Url/String>、From<AbsolutePathBuf>、FromStr 与 fallback。
4. `codex-rs/tools/src/tool_payload.rs`：payload 日志的 Borrowed/Owned 分支。
5. `codex-rs/core/src/tools/mod.rs`：只有拼接 tool namespace 时才创建 owned String。
6. `codex-rs/core/src/compact_remote_history.rs`：Borrow<ResponseItem> 泛型读取。
7. `codex-rs/core/src/config/permissions.rs`：跨平台路径规范化的 Cow。

## 75. 搜索命令

```bash
rg -n 'impl (Try)?From|impl FromStr|impl AsRef' codex-rs/protocol/src/thread_id.rs codex-rs/utils/absolute-path/src/lib.rs codex-rs/utils/path-uri/src/lib.rs
rg -n 'Cow::Borrowed|Cow::Owned|into_owned' codex-rs/core/src codex-rs/tools/src
rg -n 'Borrow<ResponseItem>|\.borrow\(\)' codex-rs/core/src/compact_remote_history.rs
```

## 76. 小练习：判断接口

为下面需求分别选择接口：

1. 任意字符串尝试成为 ThreadId；
2. AbsolutePathBuf 暂时交给只读文件 API；
3. 消费 AbsolutePathBuf 取回 PathBuf；
4. 日志名称通常复用已有字符串，偶尔需要拼接；
5. 把可能含非法 UTF-8 的 shell stdout 仅用于人类诊断。

建议答案依次是 TryFrom、AsRef/&Path、into_path_buf/From、Cow、from_utf8_lossy；但仍要结合错误和 ownership 合同。

## 77. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Conversion / coercion / cast | 转换/自动调整/强制转换 | Trait 驱动的显式变换、编译器引用调整和 `as` 运算符 |
| `From` / `Into` | 从……转换/转换进入 | 标准、不可失败的 owned 类型转换及其调用侧形式 |
| `TryFrom` / `TryInto` | 尝试从/尝试转入 | 返回 Result 的可失败类型转换 |
| Source/target type | 源/目标类型 | 转换前 value 的类型与转换后期望类型 |
| Type inference / annotation | 类型推断/标注 | 编译器从上下文判断目标，以及代码显式写出类型 |
| `FromStr` / parse | 从字符串/解析 | 将文本语法验证并构造成 typed value |
| `AsRef` / `AsMut` | 作为共享/可变引用 | 提供廉价 borrowed view 的 Trait |
| `Borrow` | 借用等价形式 | 保持 Eq/Hash/Ord 语义一致的 borrowed view |
| `Clone` / `ToOwned` | 克隆/转为拥有 | 复制同类型，以及从 borrowed form 创建 owned form |
| `Cow` | Clone-on-Write，写时克隆 | 在 Borrowed 和 Owned 两种表示间统一返回 |
| `into_owned` / `to_mut` | 转 owned/转可变 | 必要时 clone 借用值，以及修改前确保 owned |
| Deref coercion | 解引用自动调整 | 引用上下文中从 wrapper reference 自动得到 target reference |
| Infallible | 不可失败 | 错误类型没有任何可构造 value 的转换 |
| Fallible | 可失败 | 结果需要用 Option 或 Result 表示 |
| Lossless/lossy | 无损/有损 | 是否完整保留原始信息或可能替换、截断内容 |
| Allocation / clone / move | 分配/克隆/移动 | 创建新存储、复制 value 和转交 ownership |
| Round trip | 往返转换 | A→B→A 后检查身份与信息是否保持 |
| Boundary conversion | 边界转换 | 在输入输出层集中 raw、domain 和 wire 类型变换 |
| Blanket impl | 毯式实现 | 为所有满足条件的类型自动提供 Trait implementation |
| Orphan rule | 孤儿规则 | 禁止为两个都不属于当前 crate 的外部项随意实现 Trait |

## 78. 常见误解

- “`.into()` 一定零成本”：它可能格式化、clone 或分配。
- “From 和 TryFrom 只是返回类型不同”：选择它们是在声明转换能否失败的 API 合同。
- “AsRef 会得到新的 owned value”：它通常只返回与输入 lifetime 绑定的引用。
- “Borrow 就是另一个 AsRef”：Borrow 还要求相等、排序和哈希语义等价。
- “Cow 一定不分配”：Owned 分支和 Borrowed 后的 into_owned 都可能分配。
- “接收 String 总比 &str 灵活”：若函数只读，String 会不必要地要求调用者交出 ownership。
- “能 to_string 就适合作为协议格式”：Display 文本、Debug 文本和 wire serialization 是不同合同。
- “转换成功后原来的领域保证还在”：转成较宽基础类型后，类型证据可能已经丢失。

## 79. 一句话收束

From/Into 表达可靠的 owned 转换，TryFrom/TryInto 和 FromStr 建立可失败验证边界，AsRef 与 Borrow 提供不同强度的借用视图，ToOwned 把借用变为拥有，Cow 则让常见快路径保持借用、只在确有必要时创建新值。
