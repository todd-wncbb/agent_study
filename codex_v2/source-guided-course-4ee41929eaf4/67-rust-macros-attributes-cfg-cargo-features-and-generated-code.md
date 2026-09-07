# 67：Rust 宏与条件编译——Attributes、`macro_rules!`、Derive、`cfg`、Cargo Features 与生成代码

> 源码基线：`4ee41929eaf4`
>
> 第 15 章讨论 Codex 产品 feature，第 61 章介绍 Cargo feature 和构建图，第 62 章把 macro expansion 放进编译流水线。本章专门解决源码阅读中的一个错觉：你打开的 `.rs` 文件并不总是 compiler 最终 type-check 的程序。宏会生成 enum、impl、match 和测试导出；`cfg` 会删除某些 items；derive 与 attribute proc macros 会增加或改写实现。学会还原这些步骤，才能准确追踪协议和跨平台行为。

## 1. 本章解决什么问题

看到以下代码时，你应该知道去哪里找“消失的实现”：

```rust
client_request_definitions! {
    Initialize => "initialize" {
        params: InitializeParams,
        serialization: None,
        response: InitializeResponse,
    },
}

#[derive(Debug, Parser, Serialize, Deserialize)]
#[cfg_attr(test, derive(PartialEq))]
struct Args { /* ... */ }
```

本章回答：

- Macro 为什么可以“写出源码里看不到的代码”？
- `macro_rules!` 的 matcher、metavariable、fragment、repetition 怎样读？
- `$ident:ident`、`$ty:ty`、`$expr:expr`、`$tt:tt` 分别是什么？
- `$(...)*`、`+`、`?` 与 separator 怎样展开？
- Macro hygiene 和 `$crate` 解决什么问题？
- Declarative macro 与 procedural macro 有何区别？
- `derive`、attribute proc macro、function-like proc macro 各生成什么？
- Serde、Clap、thiserror、tokio、tracing attributes 是 compiler 内建的吗？
- `#[cfg(...)]` 与 `cfg!(...)` 为什么行为不同？
- `#[cfg(test)]`、`debug_assertions`、`target_os` 和 `feature = "..."` 何时为真？
- Cargo feature 与 Codex 产品 feature flag 为什么完全不是一层？
- `build.rs` 怎样声明和启用 custom cfg？
- 为什么 Cargo 构建能看到文件，Bazel 却还需 `compile_data`？
- 怎样手工展开大型协议宏并验证生成物没有漂移？

## 2. 先说人话：宏像“编译前的代码模板机器”

普通函数在 runtime 接收 values：

```rust
fn add(a: i32, b: i32) -> i32 { a + b }
```

宏在 compile time 接收 tokens，并产出新的 Rust tokens：

```rust
make_enum! { Started, Finished }
```

可能展开为：

```rust
enum Event {
    Started,
    Finished,
}
```

Compiler 随后才对展开结果做 name resolution、type checking 和 borrow checking。

## 3. Macro 不是字符串替换

Rust macros 操作 token/token stream，并受语法 fragment 与 hygiene 规则约束。它们不像 C preprocessor 那样只做任意文本粘贴。

仍然可能生成复杂、难读或错误的代码，但 compiler 能在结构化 Rust syntax 上继续检查。

## 4. 三大类宏

| 类别 | 调用形状 | 实现方式 |
|---|---|---|
| Declarative macro | `name!(...)` | `macro_rules!` pattern → expansion |
| Derive procedural macro | `#[derive(Name)]` | Proc-macro crate 根据 item tokens 生成附加实现 |
| Attribute procedural macro | `#[name(...)] item` | Proc-macro 接收 attribute + item，并返回替换 tokens |
| Function-like procedural macro | `name!(...)` | Proc-macro function 接收/返回 TokenStream |

“Function-like”描述调用外形，不表示它是 runtime function。

## 5. Compiler built-in macro

Rust 还提供内建 macros，例如：

```text
format!  vec!  println!
include_str!  env!  cfg!
concat!  stringify!  file!  line!
```

它们不是当前 crate 的 `macro_rules!` 定义，但阅读方式仍是“调用在编译期产生代码/value”。

## 6. 为什么大型项目大量使用宏

当一份声明必须同步生成多套重复结构时，宏能建立单一事实来源：

```text
协议 method 列表
→ request enum
→ response enum
→ method_name match
→ From implementations
→ schema/TypeScript export visitors
→ experimental test tables
```

手写每套列表更容易漏改，但宏也会扩大一次输入的影响面。

## 7. 第一条原则：先找 invocation，再找 definition

读宏调用时记录：

```text
macro 名称
传入的每一类 token
所在 module
生成的 symbol 名称
```

再用 `rg -n 'macro_rules! name'` 找 definition。只看 invocation 无法知道它生成多少代码。

## 8. 第二条原则：把一个最小 invocation 手工展开

不要一开始展开 100 个协议 methods。选一个 entry：

```rust
Ping => "ping" {
    params: PingParams,
    serialization: None,
    response: PingResponse,
},
```

把 repetition body 只替换一次，就能理解整体结构。

## 9. `macro_rules!` 的基本结构

```rust
macro_rules! make_getter {
    ($name:ident, $ty:ty) => {
        fn $name(&self) -> &$ty {
            &self.$name
        }
    };
}
```

每个 arm 由 matcher `(...)` 和 transcriber `{...}` 组成。输入从上到下尝试匹配第一个适用 arm。

## 10. Metavariable

```rust
$name:ident
```

- `$name` 是 macro metavariable；
- `ident` 是 fragment specifier；
- 匹配到的 identifier tokens 可在 expansion 中重复使用。

它不是 runtime variable，也不是 generic type parameter。

## 11. 常见 fragment specifiers

| Fragment | 匹配内容 |
|---|---|
| `ident` | identifier 或 keyword-compatible identifier position |
| `ty` | Rust type |
| `expr` | expression |
| `literal` | literal token |
| `path` | path |
| `pat` / `pat_param` | pattern |
| `stmt` | statement |
| `item` | function/struct/impl 等 item |
| `block` | `{ ... }` block |
| `meta` | attribute 内的 metadata |
| `vis` | visibility，例如 `pub(crate)` |
| `lifetime` | lifetime token |
| `tt` | 单个 token tree |

最宽泛的 `tt` 灵活，但能提供的结构保证更少。

## 12. `ident` 例子

固定协议宏中：

```rust
$variant:ident
```

匹配 `Initialize`、`ThreadStart` 等 variant identifier，并生成：

```rust
Self::$variant { ... }
```

## 13. `literal` 例子

```rust
$wire:literal
```

匹配：

```rust
"thread/start"
```

Macro 可将同一个 literal 同时放进 Serde rename、TS rename、method_name match 和测试表。

## 14. `ty` 例子

```rust
$params:ty
$response:ty
```

可匹配单一 identifier、generic type 或 qualified path。Expansion 可用于 field type、trait invocation 和 schema generator generic argument。

## 15. `expr` 例子

```rust
$reason:expr
```

匹配 experimental reason string 或更一般 expression。Expansion 中可直接产生 `Some($reason)`。

## 16. `meta` 例子

```rust
$(#[$variant_meta:meta])*
```

捕获 variant 上零个或多个 attributes，然后在生成 enum variant 前原样再发射：

```rust
$(#[$variant_meta])*
```

这让 doc、experimental、deprecated 等 metadata 穿过宏边界。

## 17. `tt` 是什么

Token tree 是单个 token 或一组平衡 delimiters 包围的 tokens。它适合把尚不想理解的 syntax 透传给另一个 macro。

```rust
$($tt:tt)*
```

可捕获任意数量 token trees。代价是错误可能推迟到后续 expansion，diagnostic 较远。

## 18. Repetition：`$(...)*`

```rust
$( $variant:ident ),*
```

读作：重复匹配 `$variant:ident` 零次或多次，中间用 comma 分隔。

Expansion：

```rust
$( enum_or_arm_using_$variant, )*
```

对每个匹配 entry 生成一次。

## 19. `+` 与 `?`

```text
$(...)*  → 0 次或更多
$(...)+  → 1 次或更多
$(...)?  → 0 次或 1 次
```

固定协议宏大量使用 `?` 表达 optional attribute/wire override，使用 `*` 表达 method list 和 doc attributes。

## 20. Separator

```rust
$( $item:ident ),* $(,)?
```

第一段接受逗号分隔列表，第二段允许 optional trailing comma。

Trailing comma 支持能减少大列表 diff 噪声。

## 21. Nested repetition

```rust
$(
    $(#[doc = $variant_doc:literal])*
    $variant:ident { ... }
),*
```

外层遍历 variants，内层遍历当前 variant 的 doc attributes。展开时 metavariables 必须处于与匹配一致的 repetition 层级。

## 22. Optional group

固定宏：

```rust
$(inspect_params: $inspect_params:tt,)?
```

表示某 entry 可省略整段字段。Expansion 中也必须用 matching optional repetition 引用 `$inspect_params`。

## 23. 多个 Macro arms 表达小型语法

`serialization_scope_expr!` 有不同 arm：

```text
None
global("key")
thread_id(params.thread_id)
thread_or_path(params.thread_id, params.path)
...
```

这相当于为协议声明定义一个很小的 domain-specific language（DSL）。

## 24. Macro matcher 不是普通函数参数

```rust
thread_id(params.thread_id)
```

Macro 可匹配 token 形状 `$params:ident . $field:ident`，并在 expansion 中选择只使用 `$field`。Runtime function 无法根据调用语法 token 做这种分派。

## 25. Arms 按顺序匹配

更具体的 arms 通常放前面，更宽泛的 `$($tt:tt)*` fallback 放最后。否则宽泛 arm 可能抢先接住输入。

这与 untagged enum 的“宽泛 variant 抢先”有相似阅读风险，但发生在 compile time。

## 26. Macro error 常出现在调用点

若输入不符合任何 matcher，compiler 可能说：

```text
no rules expected this token
```

先检查：

- comma/semicolon；
- optional field spelling；
- fragment 预期是 ident、literal 还是 type；
- matcher 是否允许 trailing comma；
- entry 是否放在错误层级。

## 27. Expansion error 也可能指向生成代码

Macro 成功匹配，但产出的 Rust 不合法或 type-check 失败时，diagnostic 会同时涉及 invocation 和 macro definition 内部。

要先还原具体展开，再按普通 Rust 错误分析。

## 28. `stringify!`

```rust
stringify!($response)
```

把 tokens 的源码 spelling 变成 static string，不执行表达式。协议 schema exporter 用它生成 type 名称。

它不提供完整 type reflection；alias/path spelling 改变时 string 也会改变。

## 29. `concat!`

```rust
concat!(stringify!($variant), "Response")
```

在编译期拼接 literals，生成 static string。它只能处理可在编译期成为 literal 的输入。

## 30. `format!` 与 `format_args!`

`format!` 是 macro，因为 format string 和 arguments 需要编译期检查并支持可变数量。它通常分配 String；`format_args!` 产生临时 formatting arguments，供 `write!`/logging 使用。

宏语法不意味着零成本，仍需理解 expansion 与运行时工作。

## 31. Macro hygiene

Hygiene 让宏内部引入的 identifiers 不轻易与调用者局部变量意外冲突，并控制名称从 definition site 或 invocation site 解析。

它不是“宏绝不会发生名字问题”；paths、export scope、captured metavariables 和显式 identifiers 仍需设计。

## 32. `$crate`

可复用 exported macro 若要引用定义它的 crate 中的 helper，通常使用：

```rust
$crate::helper::function()
```

而不是假设调用者以某个固定依赖别名导入该 crate。

## 33. 为什么固定宏中常见绝对 path

例如：

```rust
::std::path::Path
::anyhow::Result
::ts_rs::TS
```

这样降低调用 scope 中同名 imports/types 对 expansion 的影响，也让生成代码的依赖更明确。

## 34. Macro scope

未导出的 `macro_rules!` 常受 lexical/module scope 影响。定义位置、`use`、`#[macro_export]` 和 module 顺序会决定 invocation 是否可见。

遇到 `cannot find macro` 时，不只检查 Cargo dependency，还要检查 macro 是否导出/导入以及定义是否在可见范围。

## 35. `#[macro_export]`

它通常把 macro 暴露到 crate root path。Public macro 变成外部 API：matcher syntax、generated paths 和 diagnostics 都需要兼容性考虑。

Codex 内部大型协议宏留在 module 内，可避免扩大 crate API surface。

## 36. Declarative macro 的优点

- 无需单独 proc-macro crate；
- Pattern 与 expansion 在源码中可读；
- 适合重复 declarations 和小 DSL；
- Compile-time 生成 typed Rust；
- 对固定列表保持一致性。

## 37. Declarative macro 的成本

- IDE/navigation 有时跳不到生成 symbol；
- Error 指向 expansion；
- Nested repetition 难读；
- 一行 input 可能改变多套 API；
- Review 容易只看 entry，不看 generator；
- Compile time 与 code size 可能增加。

## 38. App-server `client_request_definitions!` 是什么

固定宏从 method entries 生成：

- `ClientRequest` typed enum；
- `id()` 与 `method_name()`；
- serialization scope；
- JSONRPC request conversion；
- typed `ClientResponse`；
- erased response payload；
- From implementations；
- experimental inspection；
- TS/JSON Schema export helpers。

所以新增一条 entry 远不只是“加一条 match arm”。

## 39. 手工展开一个 request variant

输入概念：

```rust
Ping => "ping" {
    params: PingParams,
    serialization: None,
    response: PingResponse,
}
```

至少生成：

```rust
enum ClientRequest {
    #[serde(rename = "ping")]
    #[ts(rename = "ping")]
    Ping { request_id: RequestId, params: PingParams },
}
```

以及相应 match、response 和 export paths。

## 40. Macro 建立的协议一致性

同一个 `$wire` literal 同时驱动 Serde、TS 和 method_name，降低手写名称漂移。

但 params/response types 自己的 derives/attributes 仍可能不一致，因此生成 schema 和 wire tests 仍必要。

## 41. Macro 中的 test-only generated code

固定宏在 `#[cfg(test)]` 下生成：

- experimental method/type tables；
- response TS export；
- type visitors；
- param/response schema exports。

这说明 `cfg(test)` 不只是手写测试函数，也能控制宏展开产物中的 items。

## 42. Optional experimental attribute 的传播

Matcher 捕获：

```rust
$(#[experimental($reason:expr)])?
```

然后在不同生成位置用于 runtime gating 和 test metadata。一个 attribute token 被宏解释成多种一致行为。

## 43. Macro helper macro

`experimental_reason_expr!`、`experimental_method_entry!`、`experimental_type_entry!` 把大 macro 中的 optional cases拆开。

这能减少 nested repetition，但 helper 与主宏形成隐含协议，修改任一方都要检查所有调用形状。

## 44. Empty expansion 是有意策略

```rust
($variant:ident, $response:ty, manual) => {};
```

当 entry 标记 manual conversion 时，helper 不生成自动 From impl。空 expansion 不是遗漏，而是 DSL 的一个控制分支。

## 45. 重复生成的 match 应 exhaustive

宏已拥有所有 variants 列表，生成 exhaustive match 能让同一次 expansion 内部保持一致。若生成 wildcard，会削弱新增 entry 时的 compiler 检查。

## 46. Procedural macro 在哪里运行

Proc-macro crate 编译给 host，并在 rustc 编译目标 crate 时执行。Cross compilation 中：

```text
proc macro executable → host platform
generated target code → target platform
```

这也是供应链与构建执行面的一部分。

## 47. Proc macro 看到什么

它主要收到 token stream 和 syntax structure，并非已经完成 type checking 的完整 compiler semantic database。

因此 derive 常根据 fields/attributes 生成 impl，让后续 compiler 再解析、resolve 和 type-check。

## 48. Derive macro

```rust
#[derive(Serialize, Deserialize, JsonSchema, TS)]
struct Params { ... }
```

原 struct 保留，derive macros 通常附加生成 trait impl/helper code。不同 derives 相互独立，却必须对 wire naming 等 attributes 达成一致。

## 49. Built-in derives 与 proc-macro derives

`Debug`、`Clone`、`PartialEq` 等由 compiler 支持；`Serialize`、`Parser`、`Error`、`JsonSchema`、`TS` 通常来自依赖的 proc-macro exports。

同样写在 `derive(...)` 里，不代表实现来源相同。

## 50. Helper attributes

Serde derive 认识：

```rust
#[serde(rename_all = "camelCase")]
```

Clap derive 认识 `#[arg(...)]`/`#[command(...)]`，thiserror derive 认识 `#[error(...)]`/`#[source]`。

这些不是任意 runtime annotations，而是对应 macro 在 compile time 解析的输入。

## 51. Attribute 的基本外形

Outer attribute：

```rust
#[derive(Debug)]
struct Item;
```

Inner attribute：

```rust
#![allow(dead_code)]
```

Outer 修饰后一个 item/field/expression 等；inner 修饰其所在 crate/module 等容器。

## 52. Attribute 不都来自 proc macro

类别包括：

- Compiler built-in：`cfg`、`allow`、`derive` 容器、`repr`；
- Derive helper：`serde`、`error`、`arg`；
- Attribute proc macro：`tokio::main`、`tokio::test`、`tracing::instrument`；
- Tool attributes：`clippy::...`、`rustfmt::...`。

先识别 owner，才能知道语义。

## 53. `#[derive(Clone)]` 不是总能成功

Generated impl 要求每个相关 field 都 Clone。若加了 non-Clone field，error 可能指向 derive invocation。

不要为满足 derive 盲目给底层 type 增加 Clone；先确认复制语义正确。

## 54. `#[derive(Default)]`

它通常逐 field 调 `Default::default()`。默认值是 API semantics，不只是减少 constructor 代码。

对配置/protocol type，要检查 derived default 是否与 missing-field、product default 和 backward compatibility 一致。

## 55. `#[derive(PartialEq, Eq, Hash)]`

Generated equality/hash 基于 fields。加入一个 field 会自动改变 identity/hash semantics，可能影响 HashMap key、cache 或 tests。

Derived 不代表无需 review。

## 56. Serde derive

`Serialize` 和 `Deserialize` 各生成独立方向实现；`#[serde(...)]` 决定 rename、tag、default、flatten 等。第 64 章讲 wire 语义，本章强调这些实现并不直接写在源码中。

## 57. Thiserror derive

`#[derive(Error)]` 读取 `#[error]`、`#[source]`、`#[from]` 并生成标准 Error/Display/From 代码。第 66 章看到的 cause chain 很多来自这次 compile-time expansion。

## 58. Clap derive

```rust
#[derive(Debug, Parser)]
struct Args { ... }
```

`Parser` derive 根据 fields 和 `#[arg]` metadata 生成 CLI parsing/command description。CLI surface 改动必须看最终 help/schema，不只看 struct。

## 59. Tokio attribute macro

```rust
#[tokio::main]
async fn main() { ... }
```

Attribute macro 会把 async entrypoint 改写为创建 runtime 并 block_on future 的同步入口形状。具体 runtime flavor/features 由 attributes 和 Tokio feature 决定。

## 60. `#[tokio::test]`

它为 async test 生成普通 test harness 可调用的 wrapper/runtime。它不同于 `#[test] async fn`，后者普通 test harness 不会自动 await。

## 61. `#[tracing::instrument]`

Attribute macro 包装函数执行以创建/进入 span，并可记录 arguments/fields。对 async 函数要遵循仓库规则：优先 instrument 定义，并检查被调用实现是否已 instrument，避免重复 spans。

## 62. Attribute macro 可以替换整个 item

与 derive“通常添加 impl”不同，attribute proc macro 接收原 item 后返回任意 replacement tokens。阅读行为时不能假设函数 body 原样执行；应查该 macro 文档或 expansion。

## 63. Function-like proc macro

例如某些 schema/query DSL 用：

```rust
some_macro!(custom syntax)
```

它外形像 `macro_rules!` invocation，但实现来自 proc-macro crate。用 `rg macro_rules!` 找不到时，检查 imports/Cargo dependency。

## 64. Macro expansion 的编译顺序直觉

```text
parse token/syntax
→ resolve/expand macros and attributes（多轮）
→ name/type/trait checks on expanded code
→ MIR/codegen
```

实际 compiler pipeline 更复杂，但关键是：borrow checker 检查的是 expansion 后程序。

## 65. `#[cfg(...)]` 是条件编译

```rust
#[cfg(target_os = "windows")]
fn platform_path() { ... }
```

Condition false 时，该 item 不进入当前 compilation。它不是 runtime if，也不会出现在该 artifact 中。

## 66. `cfg!(...)` 是 boolean expression

```rust
if cfg!(windows) {
    windows_path()
} else {
    unix_path()
}
```

`cfg!` 展开为 compile-time `true`/`false`，但普通 `if` 的两个 branches 仍须能被当前 compilation 解析/type-check。若 branch 引用只存在于 Windows 的 symbol，单用 `cfg!` 不够。

## 67. `#[cfg]` 与 `cfg!` 对照

| 写法 | False 时发生什么 | 适合 |
|---|---|---|
| `#[cfg(cond)] item` | Item 被移除 | 平台专属 imports/types/functions |
| `if cfg!(cond)` | Condition 是常量 false，branch 仍编译检查 | 两边代码在当前平台都合法，仅选择 value/behavior |

这是跨平台编译问题的高频根因。

## 68. `#[cfg_attr]`

```rust
#[cfg_attr(test, derive(PartialEq))]
struct Item { ... }
```

Condition true 时应用后面的 attribute；false 时不应用。适合条件 derive、lint、representation 或文档属性。

## 69. `all`、`any`、`not`

```rust
#[cfg(all(unix, not(target_os = "macos")))]
#[cfg(any(test, debug_assertions))]
```

它们组合 cfg predicates。复杂表达式最好写成清楚的 module/function分支，并有 platform matrix 测试。

## 70. `unix` 与 `windows`

这是 target family 类条件，不等同于具体 OS：

- Linux/macOS 等通常满足 `unix`；
- Windows 满足 `windows`；
- 更精确差异使用 `target_os`。

不要用 `unix` 推断所有 Unix 平台拥有同一命令或 filesystem 行为。

## 71. `target_os`

```rust
#[cfg(target_os = "windows")]
#[cfg(target_os = "linux")]
#[cfg(target_os = "macos")]
```

它按编译 target 决定，不按当前运行 shell 或 host OS 决定。Cross compile 时 host 与 target 可以不同。

## 72. `target_arch`

```rust
#[cfg(target_arch = "x86_64")]
```

用于 architecture-specific code/assembly/ABI。不要把它与 Cargo package target 或 Bazel target 名词混淆。

## 73. `target_env` 与 ABI

Windows `msvc`、GNU/LLVM variants，Linux musl/gnu 等可能需要 `target_env` 或完整 target triple 维度。只检查 target_os 可能遗漏 ABI/linking 差异。

## 74. `debug_assertions`

```rust
#[cfg(debug_assertions)]
```

由 compilation profile/settings 决定，不严格等同于“cargo run”或“开发机器”。Release-like custom profile 也可能配置不同。

固定 app-server 的 managed-config test hook 只在 debug assertions 下存在，production artifact 不应依赖它。

## 75. `#[cfg(test)]`

它在当前 crate 以 test configuration 编译时启用，常用于 unit-test modules/helpers。

Integration test 是独立 crate，待测 library 通常作为正常 dependency 构建；不要假设 library 内所有 `cfg(test)` helpers 对 integration test 可见。

## 76. 为什么不应滥用 test-only public API

若生产实现为测试新增大量 `#[cfg(test)] pub fn`，Cargo 与 Bazel/integration test 可见性可能不同，也扩大维护面。

优先通过真实 public boundary 测试；确需 helper 时保持最小且理解 compilation target。

## 77. `#[cfg(not(test))]`

可以在测试时替换 watcher/clock/global integration，但会让 test artifact 与 production 编译不同。

每次使用都要问：测试是否仍覆盖生产 control flow，还是测试了另一套实现？

## 78. Feature cfg

```rust
#[cfg(feature = "some-capability")]
```

只有当前 Cargo package 定义并启用该 feature 时为真。它不是任意配置字符串，也不是 Codex `Feature::CodeMode` runtime flag。

## 79. Cargo feature 在 manifest 中定义

概念示例：

```toml
[features]
default = ["client"]
client = ["dep:reqwest"]
```

然后当前 crate 才能使用 `cfg(feature = "client")`。依赖的 feature 列表和当前 package feature namespace 不应混淆。

## 80. Dependency feature

固定 manifest：

```toml
serde = { workspace = true, features = ["derive"] }
tokio = { workspace = true, features = ["macros", "rt-multi-thread"] }
```

这启用依赖 crate 的 capabilities：Serde derive proc macro、Tokio attribute macros/runtime。它不会自动创建当前 crate 的 `cfg(feature = "derive")`。

## 81. `default-features = false`

```toml
rmcp = {
    default-features = false,
    features = ["base64", "macros", "server"],
}
```

先禁用依赖默认集合，再明确选用。本 crate 的源码必须只依赖这些启用的 API。

## 82. Cargo features 通常是 additive

依赖图中多个 consumers 启用的 features 往往取 union。Feature 设计应倾向“增加 capability”，而不是让两个 features 互斥改变同一 API。

若需要互斥 backend，要用 compile_error/check 或更清晰 package separation 明确禁止非法组合。

## 83. Feature unification

同一 package version 在某解析上下文中通常以统一 feature set 编译，而不是每个 dependent 都得到完全独立 copy。

Resolver 2 改善 target/build/dev 等隔离，但仍要用 `cargo tree -e features`/metadata 查看真实 resolved graph。

## 84. Cargo feature 改变 artifact

它可能：

- 增加 dependencies；
- 启用 modules/APIs；
- 改代码大小；
- 增加 native build；
- 改 compile time；
- 改 Send/Sync/trait impl 可用性。

通常需要重新编译，无法在已发布 binary 中运行时切换。

## 85. Codex 产品 Feature 是 runtime/config 层

`codex-rs/features/src/lib.rs` 的 `Feature` enum 描述 ShellTool、CodeMode、UnifiedExec 等产品开关，并有 Stage、default、配置解析和 telemetry。

它们存在于编译后的程序中，可根据 config/policy/thread context 决定行为。

## 86. 两类 Feature 对照

| Cargo feature | Codex product feature |
|---|---|
| Manifest/依赖解析 | Rust enum/config registry |
| Compile-time 选择代码/API | Runtime 选择产品行为 |
| 常需重新构建 | 常可由 config/policy 改变 |
| `cfg(feature = "x")` | `features.enabled(Feature::X)` 等 |
| 影响 dependency graph | 影响 tool/flow exposure |

同名“feature”只是英文重合。

## 87. 为什么产品 feature 不应全变 Cargo feature

灰度、用户设置、企业 policy、实验菜单、telemetry 和运行时回滚需要同一 binary 同时包含两条路径。Compile-time 删除路径会让运行时无法切换。

但安全/平台根本不支持的实现可能适合 cfg 删除；这是不同决策。

## 88. 编译期 gate 与运行时 gate 可同时存在

```text
Cargo/cfg：这个 artifact 是否包含 backend
Product Feature：本次 session 是否启用 backend
Policy/approval：本次 operation 是否允许使用
```

三层解决 capability availability、rollout 和 authorization，不可互相替代。

## 89. `build.rs` 可以设置 custom cfg

固定 bwrap build script：

```rust
println!("cargo:rustc-check-cfg=cfg(bwrap_available)");
// native build 成功后
println!("cargo:rustc-cfg=bwrap_available");
```

Rust source 随后可 `#[cfg(bwrap_available)]` 编译真实 wrapper。

## 90. `rustc-check-cfg`

它告诉 compiler 哪些 custom cfg 名称/values 是合法预期，帮助发现 typo 和 unexpected cfg warnings。

只发 `rustc-cfg` 而不声明 check-cfg，会在严格 lint 环境遇到 unexpected cfg 诊断。

## 91. `rustc-cfg`

它真正为当前 target compilation 增加 cfg predicate。是否发出可由 build script 检查 platform、dependency、header 或 native build 结果决定。

这意味着 `.rs` 行为依赖 build script output，不能只搜索源码 attributes。

## 92. Build script 运行在 Host

Cross compile 时，build.rs 在 host 执行，却为 target crate 发 link/cfg metadata。它必须正确区分：

- host environment；
- `TARGET`；
- target toolchain/sysroot；
- host tool dependencies。

否则可能错误启用 target cfg。

## 93. Cargo 与 Bazel 可用不同步骤实现同一 cfg

第 62 章看到：Cargo build.rs 编译 bwrap 后发 `bwrap_available`；Bazel 可直接通过 native target 和 rustc flags 设置相同 cfg。

语义要一致，但不必强行执行同一 build script。

## 94. Environment macros

```rust
env!("CARGO_MANIFEST_DIR")
option_env!("NAME")
```

它们在 compile time 读取环境并嵌入 artifact。前者缺失会编译失败，后者得到 Option。

不要用它们读取应在 runtime 变化的 secret/config。

## 95. `include_str!` 与 `include_bytes!`

```rust
const TEMPLATE: &str = include_str!("template.md");
```

Compiler 在 build time 读取文件并把 bytes 嵌入 artifact。Path 通常相对当前 source file，目标文件必须进入构建 action inputs。

## 96. Cargo 看到不等于 Bazel 看到

Cargo 常能直接读取 source tree 文件；Bazel sandbox 只暴露声明 inputs。新增 `include_str!`/`include_bytes!`/compile-time migration 时必须更新相应 `BUILD.bazel` 的 `compile_data`/`build_script_data`。

否则 Cargo 通过，Bazel 报文件不存在。

## 97. Macro 生成物与外部生成文件

```text
Macro expansion
→ 通常只存在 compiler 内部，不提交展开 .rs

Schema/TS/codegen command
→ 写出仓库文件，需要更新并 review diff
```

两者都叫“生成代码”，生命周期和验证方式不同。

## 98. Proc macro 生成 schema impl，命令再写 schema fixture

例如 `#[derive(JsonSchema)]` 生成 trait impl；`just write-app-server-schema` 运行 exporter，调用这些 impl，最终写 JSON files。

因此 source derive、runtime exporter、生成命令和 committed fixture 是一条链。

## 99. `cargo expand`

常用来查看 macro/proc-macro 展开后的 Rust。它特别适合：

- derive 到底生成什么 bounds；
- attribute macro 怎样包函数；
- `macro_rules!` 产生哪些 items；
- error 来自 invocation 还是 expansion。

使用时要选正确 package、target、features 和 platform cfg；否则展开的不是失败构建那一份程序。

## 100. 没有 expand 工具时的手工法

1. 找 macro definition；
2. 只选一个 invocation entry；
3. 标出每个 metavariable substitution；
4. 展开 repetition 一次；
5. 删除 false cfg items；
6. 列出 derive 预计生成的 traits；
7. 再做普通 type/control-flow 阅读。

## 101. `rustc --print cfg`

它可显示特定 target/compiler 的 built-in cfg 集合。排查平台 condition 时要确保使用与实际构建相同 target triple/toolchain，而不是只看 host 默认。

## 102. Cargo resolved features 的检查

可用 metadata/tree 类命令查看：

```text
哪个 package 启用了 feature
是谁启用的
default feature 是否仍存在
target/dev/build dependency 是否参与
```

不要仅搜索 manifest 某一行就断言最终 feature set。

## 103. `unexpected cfg` 排错

依次检查：

1. Predicate 是否拼错？
2. 是 built-in 还是 custom cfg？
3. Build script 是否发 `rustc-check-cfg`？
4. 是否在该 build path 执行 build.rs？
5. Bazel rule 是否传同样 rustc cfg？
6. Target/platform selection 是否正确？

## 104. `cannot find macro` 排错

检查：

- macro 定义/导出位置；
- module lexical scope；
- 是否需要 `use path::macro_name`；
- Proc macro dependency 是否在 Cargo.toml；
- 依赖 feature 是否启用 macro export；
- 名称是否被重命名。

## 105. `no rules expected token` 排错

把 invocation 与 matcher逐 token 对齐：

```text
期望 ident，实际是 path？
期望 literal，实际是 const expression？
缺 comma？
optional block 的 trailing comma 不匹配？
attribute 顺序/形状不被 matcher 接受？
```

Macro DSL 比普通 Rust syntax 更窄。

## 106. Derive 报 trait bound 不满足

例如 `#[derive(Clone)]` 生成 `field.clone()`，某 field non-Clone。处理顺序：

1. Type 真需要 Clone 吗？
2. 哪个 caller 要求？
3. Field 能否用 Arc/owned handle 表达正确 clone？
4. 是否手写 impl 只 clone 合法部分？
5. 还是应移除 derive 并重构 API？

## 107. Proc macro 报错位置模糊

检查 helper attributes 与 macro crate 版本，尝试最小化 item，查看 expansion。若诊断来自 generated code，通常会出现宏 backtrace/“originates in macro”提示。

不要直接编辑 registry/cache 中依赖源码作为长期修复。

## 108. Platform symbol 缺失

若 Linux 构建报 Windows import 不存在：

- Import 是否有同样 `#[cfg]`？
- Function 与 caller cfg 是否对称？
- 是否误用 `cfg!` 保留了两边 type-check？
- Target-specific dependency 是否只在正确 manifest section？
- Test module 是否引用了被删 item？

## 109. Feature 在本地有、CI 没有

检查：

- Local command 是否启用了 `--features`/`--all-features`？
- Default features 是否不同？
- Workspace feature unification 是否偶然带入能力？
- CI/Bazel 是否声明相同 feature set？
- Test/dev dependency 是否让本地 test build 偶然可用？

## 110. Feature 在本地没有、依赖却“神秘出现”

另一个 workspace package/dependency edge 可能启用了同一依赖 feature，Cargo resolution 合并后当前 build 获得它。用 feature graph 找来源，而不是假设 Cargo 忽略了 manifest。

## 111. `cfg(test)` 导致 dead code 差异

测试 build 可能有额外 imports/functions，production build 则没有。只跑 tests 不一定发现 non-test compilation 下的 unused/import/cfg 问题；CI 应包含正常 build/check 路径。

## 112. Debug-only hook 的风险

`#[cfg(debug_assertions)]` 管理测试 hook 时，必须证明：

- Release artifact 不含该入口；
- Test 不依赖 production 不存在的安全绕过；
- Environment variable 不会在 release 被读取；
- CI 构建 profile 覆盖正确。

## 113. Macro 影响 change size

源码 diff 只加一行 entry，semantic diff 可能增加多个 enums、impls、schema types 和 exported client APIs。

Review size 应按展开影响和生成物，而不只按文本行数。

## 114. Macro 是 Architecture boundary

大型宏定义了 contributors 能表达什么：

```text
每个 request 必须声明 params/response
每个 method 自动获得 serialization scope
每个 response 进入 schema export
experimental metadata 统一检查
```

改变 macro grammar 等于改变许多调用点的 architecture contract。

## 115. 何时不该用宏

- 只有一个调用点且普通 function/generic 足够；
- 主要变化是 runtime values；
- 生成逻辑比重复代码更难读；
- Error diagnostics 对用户非常重要；
- DSL 无法表达例外，开始堆大量 optional switches；
- IDE/navigation 成本超过一致性收益。

## 116. 何时宏很合适

- 重复的是 items/types/impls 而非 values；
- 一份列表必须产生多套一致代码；
- Variants 数量大且结构稳定；
- Compiler 可检查生成结果；
- 有 generation/snapshot tests；
- Macro grammar 有明确 domain vocabulary。

## 117. 避免“万能宏”

如果一个宏拥有几十种 unrelated flags、nested optional tokens 和 manual escape hatches，它可能已经变成难以验证的私有编程语言。

可拆成：

- typed input structs + build-time generator；
- 多个职责单一宏；
- 普通 trait/generic helpers；
- 分阶段 codegen。

## 118. Macro 输入应让调用点自说明

固定 request DSL 使用：

```text
params:
serialization:
response:
```

比位置参数 `entry!(A, "x", B, None, C, false)` 更易 review。宏语法也应避免 opaque bool/None 参数。

## 119. Generated public API 需要 docs

Macro 可传播 doc attributes：

```rust
$(#[doc = $variant_doc])*
```

若生成 public types/methods，调用 entry 或宏 definition 必须提供足够说明；不要让 rustdoc 只有宏内部的空泛模板。

## 120. Lint attributes 在宏中的作用

固定宏对 large enum、vec init 等局部使用：

```rust
#[allow(clippy::large_enum_variant)]
#[allow(clippy::vec_init_then_push)]
```

Allow 应尽量贴近生成模式，并有结构原因。不要在 crate root 大范围关闭 lint 来掩盖宏 expansion 问题。

## 121. `cfg` 也影响 Public API

若 public function 只在某 target/feature 存在，下游 crate 的可编译 API 随构建条件改变。需要：

- docs 标明 availability；
- target-specific implementations 或 facade；
- CI matrix 覆盖；
- 避免平台调用点散落。

## 122. Facade 隔离平台 cfg

推荐结构：

```rust
#[cfg(windows)] mod platform;
#[cfg(unix)] mod platform;

pub use platform::run;
```

上层使用统一 interface，平台差异集中在 module boundary。若行为确实不同，用 typed capability/error 显式表达。

## 123. `compile_error!`

可在非法 cfg/feature组合下主动失败：

```rust
#[cfg(all(feature = "a", feature = "b"))]
compile_error!("features a and b cannot be enabled together");
```

这比让后续出现模糊 duplicate symbol/type mismatch 更可行动。

## 124. `cfg_if!` 类 helper

第三方 declarative macro 可把复杂互斥 cfg branches 组织成 if/else-like syntax。它最终仍生成带 cfg 的 items，不是 runtime branching。

阅读时仍需为目标平台选择实际 branch。

## 125. Tests 应覆盖 Macro grammar

至少覆盖：

- 最小 entry；
- optional fields present/absent；
- manual escape hatch；
- experimental metadata；
- generated serialization scope；
- generated schema/export registration；
- invalid combinations（compile-fail 若有 harness）。

## 126. Tests 应覆盖生成行为

只验证 macro invocation 能编译不够。要测试最终：

- Enum wire shape；
- method → params/response pairing；
- From conversion；
- schema fixtures；
- exhaustive method lists；
- product gating。

## 127. Compile-fail tests

Macro API 的重要错误可能只在“不应编译”的输入中体现。可用适合的 compile-test/trybuild 类 harness 固定 diagnostics 或至少固定 rejection。

但不要让 snapshots 过度依赖 compiler 版本的整段文案；聚焦关键原因。

## 128. Platform matrix

```text
Linux gnu/musl
macOS
Windows MSVC/gnullvm
不同 architecture
debug/release-like profile
default/minimal feature set
```

不一定每次本地全跑，但 CI/Bazel 要覆盖 cfg branches。未在当前 target 编译的代码会悄悄腐烂。

## 129. Minimal feature build

只跑 default/all features 可能漏掉：

- 缺 optional dependency；
- cfg branch 中未导入 symbol；
- feature 间错误隐式依赖；
- no-default build 不成立。

若 crate 宣称支持 minimal features，CI 应显式验证。

## 130. `--all-features` 不是万能验证

它可能：

- 启用互斥或非典型组合；
- 大幅增加 build matrix/disk；
- 掩盖某 feature 忘记声明对另一个 feature 的依赖；
- 与真实 release feature set 不同。

应测试支持的有意义组合。

## 131. Generated fixture drift

修改 macro input/derive/cfg 后，源码可编译但 committed schema/TS/snapshot 未更新。Generator tests 和 clean-worktree checks 用于发现这种 drift。

这也是为什么 app-server API 修改要运行 schema 写入命令。

## 132. 阅读一个宏生成类型的固定流程

1. 找使用 symbol 的代码；
2. 搜索显式定义，若无则搜 macro invocation；
3. 找 macro definition；
4. 选一个 entry 手工展开；
5. 应用 cfg；
6. 列出 derive/attribute生成职责；
7. 对照 schema/snapshot/test；
8. 再判断修改影响面。

## 133. 修改协议宏 entry 的检查表

- Wire literal 是否正确？
- Params/response types 是否 derive 所需 traits？
- Serialization scope 是否指向正确 entity ID？
- Experimental metadata 是否正确？
- Automatic From 是否适用，还是需 manual？
- TS/Serde rename 是否一致？
- Generated schema/fixtures 是否更新？
- Stable/experimental export 是否都覆盖？

## 134. 修改宏 definition 的检查表

- 所有 invocation shapes 是否仍匹配？
- Nested repetition 层级是否正确？
- Optional metavariables 是否只在对应 repetition 中引用？
- Public generated API 是否 breaking？
- Paths 是否 hygiene-safe？
- Lints/docs/cfg 是否传播？
- Generated tests 是否覆盖新 branch？
- Semantic change 是否远大于 text diff？

## 135. 修改 cfg 的检查表

- Condition 基于 host 还是 target？
- 应删除 item 还是只选择 runtime value？
- Imports、types、functions 的 cfg 是否对称？
- Manifest target dependency 是否一致？
- Build.rs/Bazel 是否提供 custom cfg？
- False branch 是否仍被 type-check？
- 所有 platform branches 有 CI 吗？
- Public API availability 是否改变？

## 136. 修改 Cargo feature 的检查表

- Feature 属于当前 crate 还是 dependency？
- Default feature 是否变化？
- 是否 additive？
- 新 optional dependency 和 lock/Bazel graph 是否更新？
- Minimal/default/release combination 是什么？
- Resolver/unification 是否从其他 edge 偷偷启用？
- 与产品 runtime feature 是否重名/混淆？
- Artifact size/security surface 是否扩大？

## 137. 常见误解一：宏只是少写几行

它可能定义 public API、wire contract、schema export 和 platform implementation。评审应按展开语义，而不是调用文本长度。

## 138. 常见误解二：`derive` 是编译器自动懂业务

Derive 只是根据 fields/attributes 生成通用实现。Default、Clone、Serialize、Error 是否符合产品语义仍需人工判断。

## 139. 常见误解三：`cfg!` 会删除 false branch

不会。它通常只是产生 boolean constant，两个普通 if branches 仍需合法。要移除平台专属代码，用 `#[cfg]`/cfg attribute组织 items。

## 140. 常见误解四：依赖 feature 就是当前 crate feature

`serde features = ["derive"]` 启用 Serde 的 derive capability，不会让当前 crate 的 `#[cfg(feature="derive")]` 为真。

## 141. 常见误解五：Codex `Feature::CodeMode` 是 Cargo feature

它是编译进 binary 的 runtime product flag，可由配置/策略决定。Cargo feature 在 build graph 中选择编译能力，两者生命周期完全不同。

## 142. 常见误解六：本机编译通过说明所有 cfg 都正确

False cfg branch 根本没进入本次 compilation。只有多 target/profile/feature matrix 才能证明其他分支仍可编译和运行。

## 143. 本章最重要的心智模型

```text
Cargo package + resolved dependency features + target/profile
                      │
                      ▼
             build.rs / custom cfg
                      │
                      ▼
source tokens ── cfg 删除/保留 items
                      │
                      ▼
     macro_rules / derive / attribute expansion
                      │
                      ▼
          expanded Rust program
                      │
                      ▼
 name/type/borrow checks → MIR/codegen → artifact
                      │
                      ▼ runtime
             Codex product feature/policy
```

源码阅读必须明确自己正在看哪一层。

## 144. 源码检查点

按以下顺序打开固定提交：

1. `codex-rs/app-server-protocol/src/protocol/common.rs`
   - `experimental_reason_expr!`
   - `serialization_scope_expr!`
   - `client_request_definitions!`
   - `client_response_payload_from_impl!`
   - `server_request_definitions!`
   - notification macros
2. 同一文件中的 `client_request_definitions! { ... }` invocation
   - 选一个 stable entry 与一个 experimental entry 手工展开
3. `codex-rs/app-server/src/main.rs`
   - Clap `Parser` derive
   - debug-only args/constants
   - `#[cfg(debug_assertions)]` 对 symbol 的影响
4. `codex-rs/app-server/Cargo.toml`
   - clap/serde/tokio dependency features
   - Windows target-specific dependency
5. `codex-rs/core/Cargo.toml`
   - rmcp minimal feature list
   - Tokio/Serde derives
   - target-specific dependencies
6. `codex-rs/bwrap/build.rs`
   - `rustc-check-cfg`
   - native build 后 `rustc-cfg`
7. `codex-rs/bwrap/src/main.rs`
   - `bwrap_available` 的 cfg consumers
8. `codex-rs/features/src/lib.rs`
   - Runtime `Feature` 与 `Stage`
   - 对比 Cargo feature namespace
9. `codex-rs/app-server/src/request_processors/feedback_processor.rs`
   - Windows/non-Windows item-level implementations
10. `codex-rs/app-server/src/request_processors/thread_processor.rs`
    - 合法使用 `cfg!(windows)` 选择两边都可编译的 values/logic
11. `codex-rs/core/src/agents_md.rs`
    - 为什么某 helper 不能只用 `cfg(test)`：integration test visibility 注释
12. `codex-rs/app-server-protocol/src/schema_fixtures.rs`
    - Derive impl 到 committed generated fixtures 的后续链路
13. `codex-rs/core/BUILD.bazel`
    - compile_data、features 与 cfg parity
14. `justfile`
    - Schema/codegen recipes 与验证入口

## 145. 动手练习一：展开一个小宏

写：

```rust
macro_rules! make_enum {
    ($name:ident { $($variant:ident),* $(,)? }) => {
        enum $name { $($variant),* }
    };
}
```

分别调用 0、1、3 个 variants，解释 matcher 中每个 `$`、fragment、separator 与 repetition。

## 146. 动手练习二：展开协议 Entry

从 `ClientRequest` 列表选一条，手工写出：

- request enum variant；
- id/method match arm；
- serialization scope arm；
- response variant；
- From impl；
- schema export statement；
- experimental table entry。

再用测试/生成物核对。

## 147. 动手练习三：`cfg!` 与 `#[cfg]`

写两个平台专属函数，只在各自 target 定义。先用 `if cfg!(windows)` 调用，观察另一平台为何仍可能报 symbol 错；再改成 item/expression-level `#[cfg]` 分支。

说明何时应使用 unified facade。

## 148. 动手练习四：两种 Feature

选一个 Cargo dependency feature 和一个 Codex `Feature`：

| 问题 | Cargo feature | Product feature |
|---|---|---|
| 谁定义 | Cargo.toml/dependency | features crate registry |
| 何时选择 | build resolution | runtime config/policy |
| 是否需要 rebuild | 通常需要 | 通常不需要 |
| 怎样观察 | cargo metadata/tree | effective Features/telemetry |

把真实源码位置填进去。

## 149. 动手练习五：追踪生成物

从一个 `#[derive(JsonSchema)]` protocol type 开始，追踪：

```text
derive input
→ generated JsonSchema impl
→ exporter registration（可能由 macro 生成）
→ schema fixture command
→ committed JSON
→ drift test
```

指出每一层出错时会在哪里表现。

## 150. 理解检查

你应该能不看答案解释：

1. `macro_rules!` 为什么不是 runtime function？
2. `$x:ident` 与 `$x:expr` 限制有什么不同？
3. `$(...)*`、`+`、`?` 怎样读？
4. Macro hygiene 和 `$crate` 解决什么问题？
5. Derive macro 与 attribute macro 对原 item 的作用有何不同？
6. Serde helper attribute 由谁读取？
7. `#[cfg]` 与 `cfg!` false branch 的核心差异是什么？
8. `#[cfg(test)]` 为什么不应作为 integration-test API 的当然保证？
9. Dependency feature 为什么不等于当前 crate feature？
10. Cargo feature 与 Codex产品 Feature 为什么不能互换？
11. `rustc-check-cfg` 与 `rustc-cfg` 分别做什么？
12. 为什么 include_str 文件要进入 Bazel compile_data？
13. App-server request macro 一条 entry 会生成哪些职责？
14. 为什么 schema fixture 仍需生成测试，即使 derive 已存在？

## 151. 本章小结

- Rust macro 在 compile time 接收 tokens 并生成新的 Rust program，不是 runtime function。
- `macro_rules!` 通过 matcher fragments、arms 和 repetitions 定义小型 syntax/DSL。
- 手工展开一个最小 entry 是阅读大型宏最有效的方法。
- Hygiene、`$crate` 和绝对 paths 帮助控制生成名称解析。
- Derive、attribute 和 function-like proc macros 在 host 编译期执行，产物随后接受普通类型与借用检查。
- Attributes 可能属于 compiler、derive helper、proc macro 或 lint tool，必须识别 owner。
- `#[cfg]` 删除 items；`cfg!` 产生 boolean，普通 if 两边仍需编译合法。
- Target、test、profile 和 feature cfg 共同决定当前 artifact 实际包含什么。
- Dependency features 启用依赖能力，不创建当前 crate 同名 cfg。
- Cargo feature 是 build-time capability；Codex Feature 是 runtime product rollout/config。
- Build.rs 可声明并启用 custom cfg，Bazel 路径必须保持语义 parity。
- Macro expansion 与 committed schema/codegen 是不同生成层，却可能串成同一条生成链。
- 修改宏/cfg/feature 时必须按展开影响、平台矩阵和生成物审查，而非只看源码行数。

## 152. 本章词汇表

| 英文/代码词 | 字面意思 | 在本章中的具体含义 |
|---|---|---|
| Macro / expansion | 宏/展开 | Compile time 接收 tokens 并生成 Rust tokens/program 的机制 |
| Token / TokenStream | 词法单元/词法流 | Macro 匹配、接收和输出的结构化编译输入 |
| Token tree / `tt` | 词法树 | 单 token 或平衡 delimiter 包围的一组 tokens |
| Declarative macro | 声明式宏 | `macro_rules!` pattern-to-expansion 规则 |
| Procedural macro | 过程宏 | Host 上运行并处理 TokenStream 的专用 crate/code |
| Derive macro | 派生宏 | `#[derive(Name)]` 根据 item 生成 trait impl/helper code |
| Attribute macro | 属性宏 | `#[name] item` 接收并可替换整个 item 的 proc macro |
| Function-like macro | 类函数宏 | 使用 `name!(...)` 外形的 declarative/procedural macro |
| Matcher / transcriber | 匹配器/转写器 | Macro arm 的输入 pattern 与输出 template |
| Arm | 分支 | 一条 matcher `=>` expansion 规则 |
| Metavariable | 元变量 | `$name` 等在 macro matching 中保存 token fragment 的变量 |
| Fragment specifier | 片段说明符 | `ident`、`ty`、`expr`、`meta`、`tt` 等语法类别 |
| Repetition | 重复 | `$(...)*`、`+`、`?` 对 token list 的重复/可选展开 |
| Separator | 分隔符 | Repetition 中 variants/items 之间的 comma/semicolon |
| Nested repetition | 嵌套重复 | Variant list 内再遍历 attributes 等多层 macro repetition |
| Macro DSL | 宏领域语言 | 由 matcher arms 定义的项目专用声明语法 |
| Hygiene | 卫生性 | 控制宏内名称与调用者名称作用域/冲突的规则 |
| `$crate` | 定义 crate 路径 | Exported macro 稳定引用自身 crate helper 的特殊 metavariable |
| `stringify!` / `concat!` | 词法字符串化/拼接 | 将 tokens 变 static spelling，并在编译期拼 literals |
| Built-in macro | 内建宏 | `cfg!`、`format!`、`include_str!` 等 compiler/library 特殊宏 |
| Attribute | 属性 | `#[...]`/`#![...]` 提供编译、宏或工具 metadata |
| Outer / inner attribute | 外部/内部属性 | 修饰后续 item，或修饰所在 crate/module/container |
| Helper attribute | 辅助属性 | `serde`、`arg`、`error` 等由对应 derive 解析的 metadata |
| `cfg` predicate | 条件编译谓词 | 根据 target/test/profile/feature 决定 item 是否进入 compilation |
| `#[cfg]` / `cfg!` | 条件属性/条件宏 | 删除不匹配 item，以及生成编译期 boolean constant |
| `cfg_attr` | 条件属性 | Condition 成立时才应用另一个 attribute |
| `all` / `any` / `not` | 全部/任一/非 | 组合 cfg predicates 的逻辑操作 |
| Target OS/arch/env | 目标系统/架构/环境 | 编译 artifact 所面向的平台维度，不一定等于 host |
| `debug_assertions` | 调试断言条件 | 由 compilation profile/settings 启用的 cfg |
| `cfg(test)` | 测试条件 | 当前 crate test configuration 下加入的 items |
| Cargo feature | Cargo 功能 | Manifest/依赖解析阶段选择编译 capability 的 additive flag |
| Dependency feature | 依赖功能 | 当前 package 为某依赖启用的 capability，如 Serde derive |
| Feature unification | 功能合并 | Resolution context 中同一 package version 汇总启用 features |
| Default feature | 默认功能 | 未禁用时 Cargo 自动启用的 package feature 集合 |
| Product feature | 产品功能开关 | Codex binary 内由 config/policy/runtime 解析的 Feature enum |
| Build script / `build.rs` | 构建脚本 | Host 上运行并向 Cargo/rustc 发 cfg/link metadata 的程序 |
| Custom cfg | 自定义条件 | Build script/Bazel 明确声明并启用的非 built-in predicate |
| `rustc-check-cfg` | cfg 合法声明 | 告诉 compiler 允许哪些 custom cfg 名称/values |
| `rustc-cfg` | cfg 启用指令 | 为当前 target compilation 实际设置 custom cfg |
| `env!` / `option_env!` | 编译期环境读取 | 将 build environment value/absence 编入 artifact |
| `include_str!` / `include_bytes!` | 编译期嵌入文件 | 读取源文件并把内容放入 binary 的 built-in macros |
| Compile data | 编译数据 | Bazel 中允许 compile-time file read 的显式 action input |
| Generated code | 生成代码 | Compiler 内 macro expansion 或外部命令写出的 source/schema |
| Generated fixture | 生成样本 | 提交并用于 review/drift check 的 JSON Schema/TS/snapshot 文件 |
| `cargo expand` | Cargo 展开工具 | 查看 macros/derives/attributes 展开后 Rust 的调试工具 |
| Feature graph | 功能依赖图 | 哪个 dependency edge 启用了哪些 Cargo features 的解析结果 |
| Compile-fail test | 编译失败测试 | 验证非法 macro/type/cfg usage 必须被 compiler 拒绝 |
| Platform matrix | 平台矩阵 | 多 OS/arch/ABI/profile/feature 组合的构建测试覆盖 |
| Facade | 门面 | 对上层暴露统一接口并在内部集中 cfg 平台实现的 module |
| `compile_error!` | 编译错误宏 | 对非法 feature/cfg组合主动给出明确 build failure |
