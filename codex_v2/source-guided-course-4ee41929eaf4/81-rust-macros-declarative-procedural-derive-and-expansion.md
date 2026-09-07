# 81：Rust 宏系统、声明宏、过程宏、Derive 与代码展开

> 源码基线：`4ee41929eaf4`。本章解决“源码里只写了一次定义，编译器为什么能看见成百上千行重复实现，以及怎样把宏调用还原成普通 Rust 来阅读和调试”。

## 1. 本章解决什么问题

阅读 Codex 时，经常能看到 `feedback_tags!(...)`、`client_request_definitions! {...}`、`#[derive(...)]`、`#[tokio::test]` 和 `#[tracing::instrument]`。这些位置没有直接写出最终执行代码，宏会在编译期间生成或改写代码。

## 2. 先说人话：Macro 是编译前的代码加工器

可以先把宏理解成：输入一段 Rust token，按规则产生另一段 Rust token；编译器再对展开结果做名称解析、类型检查、借用检查和机器码生成。

```text
你写的源码 → 解析 token/语法 → 展开宏 → 类型与借用检查 → 编译
```

## 3. 宏不在程序运行时执行

宏展开通常发生在编译期。宏生成的普通代码会在运行时执行，但“匹配哪条宏规则、生成哪些 impl”不是每次启动程序时重新决定的。

## 4. Macro 与 Function 的第一处区别

函数接收已经具有确定类型的运行时 value；宏接收语法 token。宏因此可以生成 item、impl、match arm、field、测试函数等普通函数无法返回的语法结构。

## 5. 第二处区别：参数不一定是 Value

宏参数可以是 identifier、type、path、literal、pattern 或任意 token tree。例如 `v2_enum_from_core!` 同时接收 enum 名称、源类型路径、attributes 和 variant identifiers。

## 6. 第三处区别：调用形式带 `!`

```rust
vec![1, 2, 3]
println!("value={value}")
feedback_tags!(cached = true)
```

结尾的 `!` 提醒读者：这里不是普通函数调用，输入语法可能被复制、包裹或转换。

## 7. 三类主要 Macro

- Declarative macro：`macro_rules!`，用 pattern 匹配 token；
- Procedural derive macro：`#[derive(Name)]`，为类型生成附加代码；
- Attribute-like procedural macro：`#[name(...)]`，接收并替换它标注的 item；
- Function-like procedural macro：`name!(...)`，外观像声明宏，但实现是 Rust 编译期函数。

## 8. Built-in Macro

`include_str!`、`concat!`、`env!`、`option_env!`、`cfg!`、`format_args!` 等由编译器或标准工具链特殊支持。它们也在展开期工作，但不一定能在仓库里找到 `macro_rules!` 定义。

## 9. Token

Token 是语法的基本单位，如 identifier、关键字、literal、punctuation。宏主要处理 token，而不是直接处理运行时 bytes 或最终机器指令。

## 10. Token Tree

Token tree，常写作 `tt`，可以是一个 token，或一组由 `()`、`[]`、`{}` 包围的嵌套 token。它让声明宏暂时接收自己并不理解的内部语法，再原样传给另一个宏。

## 11. Macro Invocation 的三种括号

`m!(...)`、`m![...]`、`m! {...}` 都可能合法，具体取决于出现位置和宏设计。花括号常用于较大的自定义 DSL，但括号形状本身不决定宏类别。

## 12. Declarative Macro 的基本形状

```rust
macro_rules! twice {
    ($value:expr) => {
        ($value, $value)
    };
}
```

左侧叫 matcher，右侧叫 transcriber 或 expansion template。

## 13. Metavariable

`$value` 是宏 metavariable，不是运行时变量。Matcher 捕获调用处的一段语法，transcriber 用 `$value` 把它插入输出 token。

## 14. Fragment Specifier

`$value:expr` 中的 `expr` 规定要匹配 Rust expression。Fragment specifier 让编译器按某类语法解析输入，并为匹配边界消除部分歧义。

## 15. 常见 Fragment

| 写法 | 接收什么 | 例子 |
|---|---|---|
| `ident` | 标识符 | `ThreadStart` |
| `expr` | 表达式 | `config.enabled()` |
| `ty` | 类型 | `v2::ThreadStartParams` |
| `path` | 路径 | `codex_protocol::Op` |
| `literal` | 字面量 | `"thread/start"` |
| `meta` | attribute 内部 metadata | `serde(rename = "id")` |
| `pat` | pattern | `Some(value)` |
| `item` | 完整 item | `struct X;` |
| `tt` | 单个 token tree | 暂不解析的嵌套输入 |

## 16. Codex `feedback_tags!` 的 Matcher

```rust
($( $key:ident = $value:expr ),+ $(,)?) => { ... };
```

它接收一项或多项 `identifier = expression`，项之间用逗号分隔，并允许最后再有一个可选逗号。

## 17. Repetition

`$( ... )*` 表示重复零次或多次，`+` 表示一次或多次，`?` 表示零次或一次。`$(...),*` 中 matcher 与重复符号之间的逗号是 separator。

## 18. Optional Trailing Comma

`$(,)?` 单独匹配零个或一个逗号。它让多行调用可以在最后一项后保留逗号，减少添加下一项时的 diff。

## 19. Repetition 中的同步变量

`$key` 与 `$value` 在同一 repetition 层捕获，因此展开时按相同索引成对出现。不能在不兼容的重复深度随意使用 metavariable。

## 20. `feedback_tags!` 展开后做什么

调用：

```rust
feedback_tags!(model = model_name, cached = true);
```

可以近似读成：

```rust
tracing::info!(
    target: "feedback_tags",
    model = tracing::field::debug(&model_name),
    cached = tracing::field::debug(&true),
);
```

真正展开还包含宏 hygiene 和 tracing 宏的下一层展开，但这一步已足够理解业务含义。

## 21. Expression 是否会被执行多次

宏若在 expansion 中重复写 `$value`，调用表达式就可能运行多次。`twice!(next_id())` 不是天然等于“先算一次再复制结果”；宏作者需要显式绑定临时变量来保证 single evaluation。

## 22. `feedback_tags!` 的 Evaluation

固定源码中每个 `$value` 只在对应 field 中出现一次，并以引用交给 `debug`。审查宏时，先数每个 expression metavariable 在展开模板里出现几次。

## 23. Block 包裹

宏常展开为 `{{ ... }}`：外层 `{}` 属于 macro arm，内层 `{}` 形成 expression block 和局部 scope。它防止临时变量泄露，也让多条 statement 作为一个 expression 返回。

## 24. 多个 Macro Arms

```rust
macro_rules! choose {
    (none) => { None };
    ($value:expr) => { Some($value) };
}
```

编译器按定义顺序尝试 arms，使用第一个匹配项。宽泛的 `$tt:tt` arm 放太前面，可能吞掉本应由更具体规则处理的输入。

## 25. Codex 的 `serialization_scope_expr!`

`app-server-protocol/src/protocol/common.rs` 为 `None`、`global(...)`、`thread_id(params.field)` 等 DSL shape 分别定义 arm，再生成对应 `ClientRequestSerializationScope`。

## 26. Macro 可以形成小型 DSL

```rust
serialization: thread_id(params.thread_id),
```

它不是普通 Rust 函数调用；它是 `client_request_definitions!` 输入语言的一部分，稍后被转交给 `serialization_scope_expr!` 解释。

## 27. DSL 的价值

协议作者只写一次 method、params、response 和 serialization key；宏统一生成 request enum、method name、conversion、response payload、experimental metadata 和 schema export traversal。

## 28. DSL 的代价

新读者看不到生成代码；错误可能指向 macro invocation；IDE navigation 有时绕远；修改 grammar 可能影响全部 entries。因此大型 DSL 必须有清晰注释、稳定结构和生成结果测试。

## 29. `client_request_definitions!` 的输入

每个 entry 大致包含：

```text
Variant => "wire/method" {
    params: ParamsType,
    serialization: scope-rule,
    response: ResponseType,
}
```

某些 entry 还可以携带 doc、`#[experimental(...)]`、`inspect_params` 或 manual conversion flag。

## 30. 同一 Repetition 生成多个输出

宏先遍历 entries 生成 `ClientRequest` variants，又用同一批 `$variant/$wire/$params/$response` 生成多个 match、response enum 和 export function。它把跨结构必须同步的名单集中成一个 source of truth。

## 31. Exhaustive Match 是生成收益

每加一个 request entry，生成的 `method_name`、`id` 和 serialization match 都会获得对应 arm。若手写多份列表，容易漏掉其中一份；宏用同一 repetition 保持结构一致。

## 32. 生成一致不等于语义一定正确

如果 entry 写错 wire name 或 serialization key，宏会一致地生成错误代码。宏消除机械漂移，不替代协议设计、兼容性检查和行为测试。

## 33. Macro Nesting

`client_request_definitions!` 的 expansion 内又调用 `serialization_scope_expr!`、`experimental_reason_expr!` 和 `client_response_payload_from_impl!`。阅读时应一层层展开，不要一次把整棵树塞进脑中。

## 34. `tt` 转发

复杂宏常用 `$($serialization_args:tt)*` 捕获参数，再原样传给 helper macro。外层只负责分组，内层才理解具体 grammar，这能拆分 matcher 复杂度。

## 35. Macro Hygiene

Hygiene 让宏定义内部引入的某些名字与调用处名字不轻易意外冲突。它不是“所有名字自动正确”；路径、导入、导出的 macro scope 仍需明确设计。

## 36. `$crate`

Macro expansion 中的 `$crate` 指向定义该宏的 crate，而不是调用者 crate。公开宏用它访问自身 helper，可避免调用者给依赖重命名后路径失效。

## 37. Codex 的 `$crate` 案例

`otel/src/events/shared.rs` 的宏使用 `$crate::targets`、`$crate::events::shared::timestamp()`。无论宏从 crate 内哪个 module 调用，都回到 `codex-otel` 自己的定义。

## 38. 绝对路径

`feedback_tags!` 展开中使用 `::tracing::info!` 和 `::tracing::field::debug`。前导 `::` 从 extern prelude/root 解析，减少被调用处同名局部 module 遮蔽的风险。

## 39. Macro 中的 `self`

`log_event!` 捕获 `$self:expr`，再访问 `$self.metadata`。它没有依赖调用处恰好存在名为 `self` 的隐含变量，而是把对象表达式显式作为输入。

## 40. Macro 可见性

普通 `macro_rules!` 有自己的词法和 module scope 规则。定义位置、`use`、`pub(crate) use` 与 `#[macro_export]` 会影响哪里能以何种路径调用。

## 41. `#[macro_export]`

它把声明宏导出到 crate root 的公开宏命名空间。`feedback_tags!` 虽定义在 `core/src/util.rs`，外部示例使用 `codex_core::feedback_tags!`。

## 42. `pub(crate) use` Macro

OTEL macros 在 module 内定义后以 `pub(crate) use` 暴露给 crate 内其他模块，但不承诺成为外部公共 API。可见性应和生成代码的真实消费者范围一致。

## 43. `local_inner_macros`

旧式公开宏有时依赖 `local_inner_macros` 帮助内部宏解析；现代代码通常更推荐显式 `$crate::helper!`，因为依赖关系更清楚。

## 44. Built-in `stringify!`

`stringify!(SomeType)` 把输入 token 的文本形式变成字符串，不求值也不做类型反射。协议宏用它生成测试/导出所需的类型名称。

## 45. Built-in `concat!`

`concat!` 在编译期连接 literal。它常与 `include_str!` 或 `env!` 组合，构造编译时已知路径；它不能连接任意运行时 String。

## 46. `include_str!`

`include_str!(path)` 在编译时读取 UTF-8 文件并把内容嵌入 binary。运行时无需再从原路径读取，但源码文件变化会影响编译输入和制品。

## 47. Codex TUI Frames 案例

`tui/src/frames.rs` 的 `frames_for!($dir:literal)` 展开 36 个 `include_str!(concat!(...))`，再对 default、codex 等目录分别调用，形成多个编译期 animation frame arrays。

## 48. Compile Data 不是自动可见

Cargo 常能从源码树读取 `include_str!` 文件；Bazel action 只看显式输入。新增 compile-time file read 时还必须把文件加入对应 `BUILD.bazel` 的 compile data，否则 Cargo 通过而 Bazel 失败。

## 49. `env!` 与 `option_env!`

`env!("NAME")` 在编译时要求变量存在，否则编译失败；`option_env!` 生成 `Option<&'static str>`。它们读取的是 compile environment，不是程序运行时 environment。

## 50. 为什么 `find_resource!` 必须是 Macro

`utils/cargo-bin/src/lib.rs` 注释明确说明：它要在调用 crate 的 callsite 捕获 `CARGO_MANIFEST_DIR`/`BAZEL_PACKAGE` 编译环境。若改成普通库函数，`env!` 会读取库自身编译位置。

## 51. Callsite

Callsite 是宏被调用的位置。某些 built-in 值、诊断 span 和相对文件路径以 callsite 为语境，因此“宏定义在哪”和“宏在哪展开”都可能影响结果。

## 52. Derive Macro

```rust
#[derive(Debug, Clone, Serialize)]
struct Item { ... }
```

Derive 接收类型 item 的 token，通常在原 item 旁生成 Trait impl。它不是运行时调用，也不是把 `derive` 当作 Trait method 执行。

## 53. Built-in 与外部 Derive

`Debug`、`Clone` 等 derive 由工具链支持；`Serialize` 来自 Serde 的 proc-macro crate；`thiserror::Error`、`TS`、`JsonSchema` 也由各自过程宏实现。

## 54. Derive 生成的是代码

`#[derive(Clone)]` 可以近似理解为编译器替类型写了 `impl Clone for Type { ... }`。最终 impl 仍要通过 trait bound、类型和可见性检查。

## 55. 自动添加 Trait Bounds

泛型 derive 常按字段使用情况生成 bounds。例如含有 T 字段的自动 Clone impl 可能要求 `T: Clone`。若生成的 bound 过强或不符合语义，就需要手写 impl 或使用该 derive 支持的配置。

## 56. Helper Attribute

Serde derive 声明可识别 `#[serde(...)]`；Codex 的 `ExperimentalApi` derive 声明可识别 `#[experimental(...)]`。Helper attribute 为 derive 提供配置，本身不必是独立 attribute macro。

## 57. `#[serde(...)]` 不是单独执行的普通函数

`#[serde(rename_all = "camelCase")]` 由 Serialize/Deserialize derive 读取，影响生成实现。若没有相应 derive 或 attribute registration，它可能成为未知 attribute 错误。

## 58. Procedural Macro Crate

过程宏必须由 Cargo target 声明 `proc-macro = true` 的 crate 提供。它编译成供 compiler 在构建其他 crate 时加载的扩展，不作为普通运行时 library 使用。

## 59. `TokenStream`

Proc macro 的入口接收 `proc_macro::TokenStream` 并返回 TokenStream。它不直接接收已完成类型检查的 Rust object，也不能像运行中程序那样查询任意 value。

## 60. `syn`

`syn` 把输入 tokens 解析成 Rust syntax tree，如 `DeriveInput`、`Data::Struct`、`Field`、`Attribute`。Proc macro 因而可以按 struct/enum、named/tuple fields 等结构生成不同代码。

## 61. `quote`

`quote!` 用接近 Rust 源码的写法构造输出 token，并以 `#name` 插入 Rust 变量代表的 syntax fragment；`#(...)*` 在生成阶段重复一组 token。

## 62. 注意两套重复语法

声明宏用 `$($item)*`；`quote!` 用 `#(#item)*`。它们看起来相似，但前者由 `macro_rules!` matcher/transcriber解释，后者由 quote crate 在 proc-macro 实现中解释。

## 63. Codex `ExperimentalApi` Derive

`codex-experimental-api-macros/src/lib.rs` 用 `#[proc_macro_derive(ExperimentalApi, attributes(experimental))]` 注册 derive，并将输入解析为 `DeriveInput`。

## 64. Struct 展开

对于 struct，它遍历 fields：若 field 有 `#[experimental("reason")]`，生成 presence check、field metadata 和 inventory registration；若是 `#[experimental(nested)]`，则委托嵌套值的 Trait 实现。

## 65. Enum 展开

对于 enum，它为每个 variant 生成 match arm：实验 variant 返回 `Some(reason)`，稳定 variant 返回 `None`。Named、tuple、unit variants 使用不同 pattern。

## 66. Unsupported Syntax 应生成 Compile Error

该 derive 不支持 union 时，用 `syn::Error::new_spanned(...).to_compile_error()` 返回定位到类型名的编译错误，而不是在 proc macro 内直接产生难读 panic。

## 67. Span

Proc macro token 携带 source span，帮助编译器把错误指回调用源码。新 literal 可用 call-site span；引用输入 ident 时通常保留输入 span，从而产生更准确的诊断。

## 68. Proc Macro 通常没有完整类型信息

Derive 看到 `Option<Vec<T>>` 的语法，但展开阶段通常不能向 rustc 查询“这个 path 最终解析成哪个具体类型、实现了哪些 Trait”。它必须基于 syntax、attributes 和生成后类型检查协作。

## 69. 语法识别可能有边界

如果 proc macro 通过 path 最后一个 ident 判断 `Option`，type alias 或不同写法可能改变识别结果。阅读 derive 时要区分“Rust 类型语义”和“宏实际匹配的语法 shape”。

## 70. Attribute-like Proc Macro

Attribute macro 接收 attribute 参数和被标注 item，可返回修改后的 item、多个 items，甚至空 token。`#[tokio::test]` 会把 async test 改造成普通 test 加 runtime 驱动代码。

## 71. `#[tracing::instrument]`

它改写函数体，使调用建立 span 并让 Future/执行进入相应 context。源码只看到 attribute，实际运行行为来自展开代码，所以调试 parent/span 时要记得这一隐藏层。

## 72. `#[tokio::main]` 与 `#[tokio::test]`

Rust 原生 `main`/普通 `#[test]` 不能直接由 executor await 一个 async body；Tokio attribute 生成 runtime setup，再在其中 block_on/驱动 async 函数。

## 73. Attribute 顺序

一个 item 上有多个 attributes 时，cfg、derive 和 attribute macro 的展开/保留关系会影响输入。不要假设交换两行 attribute 永远等价；需要按各宏文档和实际展开验证。

## 74. Function-like Proc Macro

它以 `name!(tokens)` 调用，但实现可以用 syn 做任意复杂解析并用 quote 输出代码。与 `macro_rules!` 相比，它更适合复杂 grammar 和高质量自定义诊断，但会增加独立 proc-macro crate 与编译成本。

## 75. No-op Derive 也可以有价值

`app-server-protocol-noop-macros` 的 `JsonSchema` 和 `TS` derive 接受 input 和 helper attributes，却返回空 TokenStream。它们不生成 impl，但保留协议源码上的可读 annotations。

## 76. 为什么生产构建使用 No-op

固定源码在非 test 构建 re-export no-op derives；test 构建才 re-export `schemars::JsonSchema` 和 `ts_rs::TS`。正常 app-server 不需要运行 schema export impl，因此避免生成不可达实现和相应成本。

## 77. 同一个 `#[derive(TS)]` 为何行为不同

名称在 `app-server-protocol/src/lib.rs` 中通过 `#[cfg(test)]` 绑定到不同 macro provider。阅读 derive 不能只看名字；还要追 import/re-export 和当前 build configuration。

## 78. Macro 与 Conditional Compilation 的组合

宏展开、`cfg` 和 feature 共同决定最终代码。测试、生产、目标平台可能得到不同 impl；这也是下一章需要单独学习条件编译的原因。

## 79. Macro Expansion 的阅读顺序

1. 判断是哪类 macro；
2. 找定义或提供它的 crate；
3. 写出调用输入属于哪些 fragments；
4. 只选一个最小 entry/variant；
5. 手工展开成普通 enum、match 或 impl；
6. 再追下一层嵌套 macro；
7. 最后看生成代码怎样被调用和测试。

## 80. 不要一开始展开整个协议 Macro

`client_request_definitions!` 有大量 entries。先只取 `Initialize`，写出它生成的一个 request variant、一个 `method_name` arm 和一个 response conversion，再推广到 repetition。

## 81. 搜索“看不见的 Symbol”

若搜索不到 `impl From<MyResponse>` 的源码，继续搜索类型名所在的 macro invocation、derive list 和宏 definition。Generated symbol 不一定以完整文本存在于原始文件。

## 82. `cargo expand`

安装 cargo-expand 后，可对 crate/module/item 查看宏展开结果。它适合回答“最终生成了什么”，但输出很大，还会包含标准 derive 和内部细节，应配合 symbol 搜索缩小范围。

## 83. IDE Expansion

Rust Analyzer 常能显示 macro expansion、跳到 derive/macro 定义或展示生成 impl。若 navigation 失败，仍以 Cargo feature、target 和真实编译命令为准。

## 84. Compiler Error 中的 Macro Backtrace

错误可能同时列出调用点、宏定义和嵌套 expansion。先找最外层业务 invocation，再找真正产生非法 token/type 的 arm；不要只修最后一行 generated code，因为它无法直接编辑。

## 85. `cargo expand` 不是 Source of Truth 的替代

Expanded output 是特定 feature/target/configuration 的结果。应修改宏定义或 invocation，并用正常 build/test 验证；不要把展开结果复制回来长期手工维护。

## 86. Macro 的测试层次

- 调用测试：验证生成 API 的运行行为；
- Compile-pass：合法输入能编译；
- Compile-fail：非法输入给出预期诊断；
- Snapshot/golden：复杂生成文本或 schema 稳定；
- 多 feature/target：不同 provider/config 都能展开。

## 87. Proc Macro 单元测试

纯 helper 如 snake-to-camel 可普通单测；真正 expansion 可用 TokenStream snapshot 或小 fixture crate。只比较 token 字符串容易受格式影响，关键还要编译生成代码。

## 88. Macro 设计：优先函数

如果只需对 typed values 做运行时计算，先写普通 function/generic/Trait。函数有更直接的类型检查、导航、调试和文档，macro 应解决函数无法表达的语法重复或编译时结构生成。

## 89. 何时用 `macro_rules!`

输入 grammar 较小、输出是规则性 token repetition、无需复杂 AST 分析时适合。`feedback_tags!`、枚举/匹配同步生成和测试断言 wrapper 都是典型场景。

## 90. 何时用 Proc Macro

需要读取 struct/enum fields、attributes、generics，生成定制 impl 或给出精确 syntax diagnostics 时适合。成本包括独立 crate、syn/quote 依赖、编译时间和更复杂维护。

## 91. 何时用 Build Script/Generator

若输入是外部 schema、大文件或要生成可审阅 artifact，build script 或显式 generator 可能更合适。Macro 主要操作 Rust token；`build.rs` 是编译前运行的独立程序，两者不是一回事。

## 92. Public Macro 是 API Contract

外部 crate 可能依赖 macro 接受的 grammar、生成类型、路径和 evaluation 次数。修改 matcher 或 expansion 可能构成 breaking change，即使函数签名看起来没变。

## 93. 宏中的安全与性能问题

- 不要意外多次求值带副作用 expression；
- 不要生成无界大代码导致 compile-time/code-size 膨胀；
- 不要把 secret token stringify 到诊断或制品；
- 生成 unsafe 时仍需可审计 safety contract；
- 公开宏路径应使用 `$crate` 或稳定绝对路径；
- 不可信外部输入更适合先由受控 generator/parser 验证。

## 94. Macro Review 清单

- 输入 grammar 能否用 function 更清楚地表达？
- 每个 fragment 类型是否足够具体？
- Arms 从具体到宽泛排列了吗？
- Repetition、separator、optional comma 是否正确？
- Expression 会被求值几次？
- 临时变量是否污染调用 scope？
- `$crate`、绝对路径和 visibility 是否适合调用位置？
- 生成代码在 test/production/不同 target 是否相同？
- 错误 span 和 message 能否指回用户输入？
- 是否有 compile/behavior/generated-artifact 测试？

## 95. 源码检查点

1. `codex-rs/core/src/util.rs`：公开 `feedback_tags!`、fragment repetition、可选尾逗号和绝对 tracing 路径。
2. `codex-rs/app-server-protocol/src/protocol/common.rs`：协议 DSL、嵌套 helper macros、request/response/match/export 同源生成。
3. `codex-rs/app-server-protocol/src/protocol/v2/shared.rs`：从 core enum 生成 v2 enum 与双向转换。
4. `codex-rs/otel/src/events/shared.rs`：`$crate`、tt forwarding、log/trace 不同字段集合。
5. `codex-rs/tui/src/frames.rs`：`include_str!`、`concat!` 和编译期资源嵌入。
6. `codex-rs/utils/cargo-bin/src/lib.rs`：必须在 callsite 捕获 compile environment 的 `find_resource!`。
7. `codex-rs/codex-experimental-api-macros/src/lib.rs`：TokenStream、syn AST、quote、derive 和 compile error。
8. `codex-rs/app-server-protocol-noop-macros/src/lib.rs`：接受 helper attributes 但生成空 TokenStream 的 derive。
9. `codex-rs/app-server-protocol/src/lib.rs`：test/production 下真实与 no-op derive provider 的 cfg 切换。

## 96. 搜索命令

```bash
rg -n 'macro_rules!|#\[macro_export\]' codex-rs
rg -n '#\[derive\(|#\[tokio::(main|test)|#\[tracing::instrument' codex-rs
rg -n 'proc-macro = true|proc_macro_derive|proc_macro_attribute' codex-rs
rg -n '\$crate|\$\(.*\)[*+?]|:[a-z]+\)' codex-rs
rg -n 'include_str!|concat!|env!|option_env!' codex-rs
```

## 97. 小练习：手工展开 `feedback_tags!`

选择两个 fields，其中一个 value 是函数调用。按 expansion 写出 tracing event，并检查函数调用出现几次、value 是 move 还是 borrow、最后逗号是否影响结果。

## 98. 小练习：展开一个协议 Entry

只取 `Initialize => "initialize"`，写出 `ClientRequest::Initialize`、`id()` match arm、`method_name()` arm 和 response payload conversion。完成后再解释 repetition 如何对全部 entries 重复。

## 99. 小练习：阅读 Derive

创建一个含 stable field、`#[experimental("x")] Option<String>` 和 `#[experimental(nested)]` field 的教学 struct。根据 `ExperimentalApi` proc macro，画出它大致生成的 Trait impl 与判断顺序。

## 100. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Macro / expansion | 宏/展开 | 编译期 token 变换和生成后的 Rust 代码 |
| Invocation / callsite | 调用/调用点 | 写下 `name!(...)` 或 attribute 的源码位置 |
| Declarative macro | 声明宏 | 用 `macro_rules!` pattern 匹配和改写 token |
| Matcher / transcriber | 匹配器/转写模板 | Macro arm 左侧输入规则和右侧输出模板 |
| Metavariable / fragment | 元变量/语法片段 | `$name` 捕获变量及其 `expr`、`ty`、`ident` 等类别 |
| Token / token tree / `tt` | 词法单元/词法树 | 宏处理的基本语法单元和带括号的嵌套单元 |
| Repetition / separator | 重复/分隔符 | `$()*`、`$()+`、`$()?` 及逗号等重复分隔 |
| Macro arm | 宏分支 | 一组 matcher => expansion rule |
| DSL | 领域专用语言 | 由宏 grammar 定义的项目内紧凑声明格式 |
| Hygiene | 卫生机制 | 减少宏定义名与调用处名字意外捕获的规则 |
| `$crate` | 定义宏的 crate | Expansion 中稳定回到 macro provider 的特殊路径 |
| Built-in macro | 内建宏 | include_str、concat、env 等工具链提供的宏 |
| Derive macro | 派生宏 | 读取类型 item 并生成 Trait impl/附加 items 的过程宏 |
| Helper attribute | 辅助属性 | 由 derive 声明并读取的 serde/experimental 等配置 |
| Attribute macro | 属性宏 | 接收 attribute 和整个 item、再返回替换代码的过程宏 |
| Function-like proc macro | 函数形过程宏 | 用 `name!(...)` 调用、由 Rust proc-macro 函数实现的宏 |
| `TokenStream` | Token 流 | Proc macro 的编译器输入和输出形式 |
| `syn` / AST | 语法解析库/抽象语法树 | 将 TokenStream 解析成 struct、enum、field 等节点 |
| `quote!` | 引用式生成宏 | 用 Rust-like 模板构造输出 TokenStream |
| Span / diagnostic | 源码范围/诊断 | Token 来源位置和指向该位置的编译错误信息 |
| No-op derive | 空操作派生 | 接受 annotations 但故意不生成 impl 的过程宏 |
| Compile environment | 编译环境 | env!/option_env! 在构建时读取的变量集合 |
| `cargo expand` | Cargo 展开工具 | 显示特定配置下宏展开结果的调试工具 |

## 101. 常见误解

- “宏就是更快的函数”：宏处理语法并在编译期展开，不是普通运行时调用。
- “宏参数一定只执行一次”：expression 被 expansion 写几次就可能执行几次。
- “搜不到 impl 就说明没有实现”：impl 可能由 derive 或声明宏生成。
- “`#[serde(...)]` 自己完成序列化”：它主要是 Serialize/Deserialize derive 读取的 helper configuration。
- “所有 derive 都是编译器内建”：大量 derive 来自 proc-macro crates。
- “Proc macro 能查询完整类型系统”：它主要看到尚未完成类型检查的 syntax tokens。
- “同名 derive 在所有构建中相同”：re-export 和 cfg 可以选择不同 provider。
- “`include_str!` 是运行时读文件”：内容在编译时读取并嵌入制品。
- “展开成功就保证协议正确”：宏只能机械保持同步，entry 的业务语义仍可能写错。
- “宏越抽象越好”：隐藏控制流、诊断和编译成本会随 DSL 复杂度上升。

## 102. 一句话收束

阅读 Rust 宏时，不要把 `!` 和 attribute 当成魔法：先确认 macro 类别与 provider，再把一个最小输入沿 matcher、repetition、syn/quote 逐层还原为普通 Rust，最后在具体 feature/target 下验证生成 symbol、求值次数、路径、诊断和测试；宏真正的价值是让多份必须一致的结构只维护一份事实，而不是把所有普通函数都改写成 DSL。
