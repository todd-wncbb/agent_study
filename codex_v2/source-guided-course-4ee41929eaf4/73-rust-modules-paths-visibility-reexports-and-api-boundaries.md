# 73：Rust Module、路径、可见性、Re-export 与 API 边界

> 源码基线：`4ee41929eaf4`。本章解决“大仓库里一个符号究竟从哪里进入编译图、谁能访问、为什么公开路径与文件路径不同”。

## 1. 本章解决什么问题

看到 `codex_core::ThreadManager`，定义却在 `thread_manager.rs`；看到文件存在，却搜索不到编译行为；同一函数能在兄弟模块调用，却不能从其他 crate 调用——这些都属于模块系统。

## 2. 先说人话：文件树不等于 API 树

文件是实现的物理组织；module 是编译器中的命名空间；`pub use` 可以把深层符号放到简洁公共路径。三者有关，但不一一相同。

## 3. Crate Root

Library crate 通常从 `src/lib.rs` 开始，binary crate 从 `src/main.rs` 或声明的 bin path 开始。Root 决定顶层 module tree。

## 4. `mod name;`

它声明一个子 module，编译器通常寻找 `name.rs` 或 `name/mod.rs`。仅创建文件不会自动把它编译进 crate。

## 5. Inline Module

```rust
mod helpers {
    fn work() {}
}
```

Module 内容也可直接写在声明处，不必单独文件。

## 6. Module 与 File 可分离

`#[path = "custom.rs"] mod thing;` 让 module 使用不同物理文件。Codex 测试常用 `#[path = "parser_tests.rs"] mod tests;`。

## 7. 一个文件可不在编译图中

没有可达的 mod/include/build 声明，普通 `.rs` 文件只是磁盘内容。排查“代码为何没生效”先沿 crate root 找 module 声明。

## 8. `pub mod`

Module 名称对父级可见范围之外公开，调用者可沿路径进入其公开 items：`codex_core::config::...`。

## 9. `mod` 默认私有

`mod client;` 只让 module 在当前可见性规则内使用。内部 item 即使写 pub，也不能穿过私有祖先路径被外部直接访问。

## 10. `pub` 不是“全宇宙公开”

Item 的实际可达性受所有祖先 module 可见性限制。公开门后的公开房间才真正从外部可达。

## 11. `pub(crate)`

整个当前 crate 可访问，其他 crate 不可访问。适合跨内部 module 协作但不承诺外部 API。

## 12. `pub(super)`

仅父 module 可访问。Codex 的 request processor 子模块用它向上层 orchestration 暴露实现，而不扩散到整个 crate。

## 13. `pub(self)`

仅当前 module，通常等价默认 private；较少显式书写。

## 14. `pub(in path)`

把可见性限制到某个祖先 module：`pub(in crate::foo)`。Path 必须描述合法祖先范围。

## 15. 可见性从窄到宽

Private → `pub(super)`/`pub(in ...)` → `pub(crate)` → `pub`。选择最小满足消费者的范围，降低耦合和兼容责任。

## 16. `use`

Use 把路径引入当前 scope，方便写短名。它不复制定义，也默认不向外公开。

## 17. `pub use`

既导入又重新导出，使调用者能从新路径访问原 item。它塑造 crate 的公共 API 树。

## 18. Core Root 案例

`thread_manager` module 私有，但 root 写：

```rust
pub use thread_manager::ThreadManager;
```

外部使用 `codex_core::ThreadManager`，无需依赖实现文件层级。

## 19. Protocol ID 案例

`thread_id`、`session_id` modules 私有，根部 re-export `ThreadId`、`SessionId`。实现可移动，公共路径保持稳定。

## 20. Facade

Crate root 或专门 module 从多个内部模块挑选一组稳定 API，像建筑正门。调用者只依赖 facade，不穿越内部走廊。

## 21. Re-export 外部 Crate Item

```rust
pub use codex_protocol::config_types::ModelProviderAuthInfo;
```

当前 crate 可把依赖类型纳入自己的 API。这样会形成公共依赖承诺，应谨慎。

## 22. Alias

```rust
pub use codex_prompts as review_prompts;
```

重新导出时可改名，建立符合当前领域的路径。

## 23. `as` 解决名称冲突

```rust
use std::sync::Mutex as StdMutex;
use tokio::sync::Mutex;
```

名字显式区分也提醒读者两种锁语义不同。

## 24. `crate::`

从当前 crate root 起算的绝对路径。内部重构时比依赖当前 module 深度的多层 `super::` 更稳定。

## 25. `self::`

从当前 module 起算，常用于明确引用同层子项。

## 26. `super::`

进入父 module。`super::turn_processor` 表明两个实现共享父级边界，但多层 `super::super` 会变脆弱。

## 27. 外部 Crate Path

`codex_protocol::ThreadId` 从依赖 crate root 起算。Cargo dependency name 决定可用 crate 名，可能通过 manifest rename 改名。

## 28. Rust 2018 Path 习惯

Use 通常从 crate 名或 `crate::` 开始；module 内普通路径解析还涉及当前 scope。遇到歧义，写明确前缀并查看 import。

## 29. Prelude

Rust 自动导入一小组常用名称，如 Option、Result、Vec。Tokio/futures Trait 方法往往仍需显式 import，例如 `use futures::StreamExt;`。

## 30. Trait Import 影响方法解析

类型实现了 Trait，但 Trait 不在 scope 时，扩展方法可能显示“不存在”。搜索方法定义与 imports，而不只搜索 inherent impl。

## 31. Glob Import `*`

`use module::*` 导入所有可见名称，测试或 prelude 中有时方便；生产大模块中会隐藏来源并增加新增名称冲突。

## 32. Grouped Imports

`use foo::{A, B, nested::C};` 只是组织语法。阅读时仍把每个 item 还原成完整来源。

## 33. `pub use` 不等于 Wrapper

Re-export 后仍是同一个类型/函数身份，不增加验证、转换或运行时逻辑。Wrapper/newtype 才能改变行为或类型边界。

## 34. Public API Surface

所有外部可达 pub items、Trait impl、re-export 和类型签名共同构成 API surface。它越大，兼容和文档成本越高。

## 35. Private Module, Public Type

这是常见设计：隐藏实现组织，只公开少量稳定类型。调用者不能依赖内部 helper 和模块层级。

## 36. Public Module

`pub mod config` 让模块本身成为 API namespace。适合内部子结构也有稳定、可解释的公共组织时。

## 37. 不要把方便测试当成 Public 理由

扩大 pub 只为单元测试会污染 API。Crate 内测试可利用私有/`pub(crate)` 范围；集成测试应测试真实公共边界。

## 38. `#[doc(hidden)]`

从生成文档导航隐藏 item，但不改变 Rust 可见性。若仍 `pub`，外部代码技术上可能依赖它。

## 39. Hidden 不等于 Unstable

Doc hidden 不能替代明确实验 API、版本策略或私有化。兼容性评审仍需把外部可达符号算在内。

## 40. Deprecated Re-export/Alias

Core 保留 Conversation→Thread 的 deprecated aliases，为旧调用方提供迁移期。删除前需搜索外部集成和版本承诺。

## 41. Module Privacy 与 Encapsulation

私有 module 可强迫所有修改经过 owner 方法，保护不变量。若调用者能直接改字段/内部 map，抽象边界会失效。

## 42. `pub` Field

Public struct 的 public field 允许外部直接构造、读取和修改，并把字段类型纳入 API。需要演进空间时使用私有字段和 constructor/accessor。

## 43. Non-exhaustive Struct/Enum

协议类型还可用 non_exhaustive 控制外部构造/匹配演进。它与 module visibility 共同塑造外部能力。

## 44. Sealed Trait

公共 Trait 若只允许本 crate 实现，可让它继承私有 sealed Trait。这样能公开调用能力而封闭实现集合；是否采用取决于扩展目标。

## 45. Feature-gated Module

`#[cfg(...)] mod x;` 条件不满足时，整个 module 不存在于当前编译。不是函数运行时返回 false，而是名字无法解析。

## 46. Platform Facade

可在不同 cfg 下编译平台实现，再由共同路径暴露统一 API。调用方不应散布大量 OS 条件。

## 47. Test Module

仓库约定新测试模块放 sibling `*_tests.rs`，实现文件末尾用：

```rust
#[cfg(test)]
#[path = "thing_tests.rs"]
mod tests;
```

## 48. 为什么 `cfg(test)` 有边界

它只对当前 crate 的 test build 生效。外部 integration test 编译 library 时，某些 test-only API 不一定存在；第 67 章已有详细说明。

## 49. Circular Module Reference

Module 可通过 crate paths 互相引用某些 items，但双向高层依赖仍会导致架构耦合。编译允许不代表边界合理。

## 50. Circular Crate Dependency 不允许

Cargo crate dependency graph 必须无环。出现 A 需要 B、B 需要 A 时，应抽取共同类型/contract 到更低层 crate 或反转依赖。

## 51. Module 还是 Crate

Module 共享同一编译/版本边界；crate 有独立依赖、可见性和编译单元。第 45/61 章讨论重构与构建图，本章关注语言路径。

## 52. Source Navigation 固定步骤

1. 从使用路径识别 crate。
2. 打开该 crate `lib.rs/main.rs`。
3. 找 pub use 或 pub mod。
4. 沿 re-export 回到定义。
5. 查实际 module 是否有 cfg/path。
6. 搜索消费者，判断真实 API 层级。

## 53. `rg` 搜索技巧

若 `rg 'struct ThreadManager'` 找到定义，再搜索 `pub use .*ThreadManager`。若路径不存在，搜索 `mod thread_manager` 和 Cargo crate 名映射。

## 54. IDE Go-to-definition

IDE 能穿过 re-export，但源码评审仍应看 crate root：它展示作者有意公开的 facade，而不只是最终定义位置。

## 55. “Module is Private” 错误

可能 item 本身 pub，但祖先 module private。使用公开 re-export 路径，或重新评估是否真的应扩大 module visibility。

## 56. “Unresolved Import” 错误

检查拼写、crate dependency、feature/cfg、re-export 路径和 item visibility。文件存在不是充分证据。

## 57. “Private Type in Public Interface”

Public API 泄漏不可访问类型会被编译器/lint 拒绝或限制可用性。公共签名的所有组成类型都应有相容 visibility。

## 58. Name Defined Multiple Times

两个 use 或本地定义同名。使用 alias、移除重复 import，或避免 glob；不要仅为过编译随意改公共名称。

## 59. 修改可见性的 Review 清单

- 新消费者真的跨越哪个边界？
- 能否移动逻辑而非公开内部 item？
- 新 pub 是否成为外部兼容承诺？
- 是否暴露内部依赖类型？
- 测试为何需要它？
- 文档和安全不变量是否足够？

## 60. 移动 Module 的清单

更新 mod/path、use/pub use、cfg、Bazel source/compile data、测试 path、宏绝对路径和文档链接；保持公共 re-export 可减少调用方 churn。

## 61. 源码检查点

1. `codex-rs/core/src/lib.rs`：私有 modules、pub modules、root re-exports、deprecated aliases。
2. `codex-rs/protocol/src/lib.rs`：私有 ID modules 与公开根路径。
3. `codex-rs/app-server/src/lib.rs`：大量私有实现 module 与少量 transport/code-mode facade。
4. `codex-rs/core/src/lib.rs`：root 中的 inline `mentions` 如何形成 crate 内 re-export facade。
5. `codex-rs/core/src/agent/control.rs`：sibling test `#[path]`。
6. `codex-rs/app-server/src/request_processors/feedback_processor.rs`：平台 cfg items。

## 62. 搜索命令

```bash
rg -n '^(pub )?mod |^pub(\(crate\))? use ' codex-rs/core/src/lib.rs
rg -n 'pub\(super\)|pub\(in crate::' codex-rs
rg -n '#\[path =|#\[cfg\(' codex-rs/core/src codex-rs/app-server/src
rg -n 'pub use .*ThreadManager|struct ThreadManager' codex-rs/core/src
```

## 63. 理解检查与答案

1. 文件存在是否自动编译？否，需进入 crate module/include/build 图。
2. 私有 module 中 pub type 能否从外部直达？不能，除非有可达 re-export。
3. `use` 与 `pub use` 区别？后者还为消费者建立新公开路径。
4. `pub(crate)` 表达什么？整个当前 crate 可用，外部不可用。
5. Re-export 是否创建新类型？否，仍是同一 item。
6. Trait 方法不存在为何先看 imports？扩展 Trait 需在 scope 才参与方法解析。

## 64. 本章词汇表

| 术语 | 直译或展开 | 实际含义 |
|---|---|---|
| Crate root / module tree | Crate 根/模块树 | 编译入口及其声明形成的命名空间图 |
| `mod` / inline module | 模块声明/内联模块 | 把文件或块内容接入 module tree |
| Visibility | 可见性 | Private、super、crate 或 public 的访问范围 |
| `use` / `pub use` | 导入/公开重导出 | 当前 scope 短名，以及为消费者建立新路径 |
| Re-export / facade | 重导出/门面 | 隐藏实现层级并挑选稳定公共 API |
| `crate` / `self` / `super` | 根/当前/父级 | Rust module path 的三个相对起点 |
| API surface | API 表面积 | 所有外部可达 items、impl 和签名的集合 |
| Encapsulation | 封装 | 限制调用方越过 owner 方法破坏内部不变量 |
| Prelude / glob import | 预导入/通配导入 | 自动常用名称和 `*` 批量引入 |
| Alias | 别名 | 用 `as` 或 type alias 提供另一名称 |
| `doc(hidden)` | 文档隐藏 | 不在文档导航展示，但不改变 pub 可达性 |
| Sealed trait | 封闭 Trait | 公开调用但限制外部 crate 自行实现的模式 |
| Feature-gated module | 条件模块 | cfg 不成立时完全不进入当前编译的 module |

## 65. 常见误解速查

| 误解 | 更准确的理解 |
|---|---|
| 一个 rs 文件就是一个自动 module | 必须由 crate root/module 声明接入 |
| Item 写 pub 就一定外部可见 | 所有祖先路径也必须可达 |
| pub use 复制了一份定义 | 它只增加访问路径 |
| doc hidden 表示私有 | 仍可能是外部可达 API |
| cfg false 是运行时不执行 | Item/module 在该编译中根本不存在 |
| 为测试改 pub 没成本 | 会扩大真实 API 和兼容责任 |

## 66. 本章结论

阅读 Rust 大仓库时，应同时维护两张图：物理文件树告诉你实现放在哪，module/API 树告诉你编译器和调用者能看到什么。`mod` 把源码接入编译图，visibility 限定访问范围，`use` 改善当前 scope，`pub use` 则有意塑造外部路径。

Codex 的 crate root 大量采用“私有实现模块 + 根级精选 re-export”，让内部文件可重组而公共路径稳定。新增 `pub` 前先确认真正消费者和兼容责任；移动文件时保留 facade，并检查 cfg、测试 path 和构建 inputs。一个符号的定义位置只是起点，它如何被声明、条件编译和重导出，才决定它在架构中的真实边界。
