# 82：Rust 条件编译、Cfg、Cargo Feature、Target 与 Build Script

> 源码基线：`4ee41929eaf4`。本章解决“同一个仓库为何在测试、生产、Linux、macOS、Windows、Cargo 和 Bazel 下实际编译出不同代码，以及怎样证明这些代码分支都能工作”。

## 1. 本章解决什么问题

阅读源码时看到 `#[cfg(unix)]`，不能把被排除的代码理解为“运行时不会走到”。它在当前构建中可能根本不存在，连 import、type 和 function 都不会进入后续编译阶段。

## 2. 先说人话：条件编译决定代码是否存在

运行时 `if` 在一个 binary 中保留选择逻辑；条件编译在生成 binary 之前就选择源码集合。不同 target/configuration 因此可能拥有不同 module graph、依赖、API 实现和测试。

## 3. 两个世界

```text
编译时条件：这个 item 是否进入本次 crate？
运行时条件：已经编译进去的代码，本次执行走哪个 branch？
```

先分清这两个世界，是理解 `cfg` 的第一步。

## 4. `#[cfg(...)]`

```rust
#[cfg(unix)]
fn detach_from_tty() { ... }
```

只有 predicate 为 true 时，item 才参与当前构建。Predicate 为 false 时，可把它近似想成源码预处理阶段已删除该 item。

## 5. `cfg` 可以标注什么

它可放在 module、use、struct/enum field、impl、function、statement、expression 等允许 attribute 的位置。粒度越细，越要小心不同配置下类型 shape 是否仍一致。

## 6. Predicate

`unix`、`windows`、`test`、`debug_assertions` 是单值 predicate；`target_os = "linux"`、`feature = "sandbox"` 是 key-value predicate。

## 7. `all`

```rust
#[cfg(all(target_os = "linux", bwrap_available))]
```

只有所有子条件都为 true。Codex Bwrap 用它选择“Linux 且 C 实现已成功构建”的真正入口。

## 8. `any`

```rust
#[cfg(any(target_os = "freebsd", target_os = "openbsd"))]
```

任意子条件为 true 即保留。它适合几个平台共享同一种依赖或实现，但仍要确认这些 OS 的 API 合同确实一致。

## 9. `not`

```rust
#[cfg(not(target_os = "linux"))]
```

对子条件取反。`not(unix)` 不只表示 Windows；未来或特殊 target 也可能落入其中，因此公共 fallback 应按真实能力命名和设计。

## 10. 嵌套 Predicate

```rust
#[cfg(all(test, any(unix, windows)))]
```

可组合成布尔表达式。条件过长时，优先通过 platform module 或小型 capability abstraction 降低重复，而不是让每个 callsite 复制同一串逻辑。

## 11. `cfg!(...)`

```rust
let name = if cfg!(windows) { "codex.exe" } else { "codex" };
```

`cfg!` 是内建宏，展开为常量 `true` 或 `false`。它不删除整个 `if` 的另一条源码 branch。

## 12. `cfg!` 的关键陷阱

```rust
if cfg!(unix) {
    unix_only_function();
}
```

在 Windows 上，编译器通常仍要解析和类型检查两个 branch；若 `unix_only_function` 根本不存在，就会失败。需要排除不合法代码时用 `#[cfg]`，不是 `cfg!`。

## 13. 何时适合 `cfg!`

两边代码在所有 target 都合法，只是选择常量、路径文本或小型行为差异时适合。`managed_codex_file_name` 用它选择 `codex.exe` 或 `codex`，两边都只是 `&str`。

## 14. `#[cfg_attr(...)]`

```rust
#[cfg_attr(test, derive(Debug, PartialEq, Eq, Serialize))]
struct PrecomputedExports { ... }
```

条件为 true 时才应用后面的 attribute。它不是删除 struct，而是在 test 构建中额外生成调试、比较和序列化能力。

## 15. 多个 Conditional Attributes

可以写多个 `cfg_attr`，也可让其应用 lint、derive、path 等属性。若组合后会产生重复 derive 或矛盾 representation，应在配置矩阵中验证。

## 16. `cfg(test)`

Rust 以 test harness 方式编译当前 crate 时设置 `test`。常见用途是加入 unit-test module、测试 helper、fixture exporter 或仅供当前 crate 测试访问的 API。

## 17. Unit Test 与 Integration Test 的差别

Unit tests 与 library 以 test configuration 一起编译，因此能看到 `#[cfg(test)]` private items。Integration test 通常把 library 当普通 dependency 使用，library 本身不因 integration test 自动获得同样的 private test surface。

## 18. Dependency 不会普遍继承 `cfg(test)`

测试 crate A 时，它的普通 dependency B 通常按正常 library configuration 构建。不要把 B 的必要公共行为藏在 `cfg(test)` 后，再期待 A 的 integration test 能调用。

## 19. Codex Protocol 的 Test Provider

`app-server-protocol/src/lib.rs` 在 test 构建导入真实 `schemars::JsonSchema` 和 `ts_rs::TS` derives，并加入 export/schema fixtures modules。

## 20. Production Provider

非 test 构建则从 `app-server-protocol-noop-macros` 导入同名 derives。这些宏接受 annotations 但不生成 runtime 不需要的 schema/TS impl。

## 21. 同一源码，不同 Generated API

协议类型上的 `#[derive(JsonSchema, TS)]` 文本没有变化，但 macro name 在不同 cfg 下解析到不同 provider。因此理解最终代码必须同时考虑 macro expansion 与条件编译。

## 22. `debug_assertions`

该 cfg 表示编译时启用了 debug assertions，通常 debug profile 为 true、release profile 为 false，但应以实际 rustc configuration 为准，不要把它简单等同于“程序正在开发机器运行”。

## 23. `debug_assert!`

`debug_assert!` 在关闭 debug assertions 时通常不执行检查；它适合昂贵的内部一致性验证，不能承担生产安全、输入验证或授权合同。

## 24. Target

Target 是生成代码准备运行的平台，由 target triple 描述。它决定 OS、architecture、environment/ABI、pointer width、endianness 和可用系统 API。

## 25. Target Triple

例如 `x86_64-unknown-linux-gnu` 可粗分为 architecture、vendor、OS、environment。不是所有 triple 都严格四段，但它比“我的电脑是 macOS”更精确地描述制品目标。

## 26. Host 与 Target

Host 是执行 compiler/build script 的机器；target 是最终 binary/library 要运行的平台。普通本机构建两者相同，cross-compilation 时不同。

## 27. Build Script 运行在哪里

`build.rs` 被编译成 host 可执行程序并在 host 上运行，但它为 target crate 准备输出。它不能用“当前运行 OS”代替目标 OS 判断，应读取 Cargo 提供的 `CARGO_CFG_TARGET_*`。

## 28. Codex Bwrap 的 Target 判断

`bwrap/build.rs` 读取 `CARGO_CFG_TARGET_OS`。只有目标是 Linux、且未设置 skip 环境变量时，才尝试编译 vendored Bubblewrap C sources 和探测 libcap。

## 29. `target_os`

`#[cfg(target_os = "linux")]` 精确选择 Linux；常见值还有 macOS、Windows 等。若代码依赖 `prctl`，用宽泛 `unix` 就不够精确。

## 30. `target_family`

`unix`/`windows` 常用于更宽的 family capability。File descriptor、Unix permission 等可以按 family 组织；具体 syscall 仍应按 target OS 缩小。

## 31. `target_arch`

Architecture 如 `x86_64`、`aarch64` 会影响指令、alignment、atomic support 和 native artifact。不要因为 OS 相同就假设所有 architecture 可复用预编译制品。

## 32. `target_env`

同为 Linux，也可能使用 GNU、musl 等环境。Codex Core manifest 对 musl triples 声明专门依赖，说明 OS 名相同仍不足以选择完整构建图。

## 33. `target_pointer_width`

常见为 32 或 64，影响 `usize`、pointer 和部分 FFI layout。协议中的 wire integer 不应靠本机 `usize` 表达稳定宽度。

## 34. `target_endian`

Little/big endian 影响 bytes 与整数的解释。网络协议通常显式规定字节序，不能让 host/target native endian 隐式决定 wire format。

## 35. Platform-specific `use`

```rust
#[cfg(target_os = "windows")]
use codex_feedback::WINDOWS_SANDBOX_LOG_ATTACHMENT_FILENAME;
```

只有 Windows implementation 需要的 symbol 应连同 use 一起 gate，避免其他 target 出现 unresolved import 或 unused import。

## 36. 配对实现

`feedback_processor.rs` 在 Windows 提供真正读取 sandbox log 的函数，在非 Windows 提供同签名、返回 `None` 的 fallback。Callsite 因而可以保持跨平台一致。

## 37. Capability Facade

相同函数签名 + 平台内部实现是一种 facade：上层只表达“尝试取得 Windows sandbox attachment”，平台 module 决定支持或无结果，而不是让 cfg 散落到全部调用者。

## 38. No-op 是否合适

No-op 只有在“平台不支持等价于无结果/无需动作”时正确。若缺少功能会破坏安全或用户合同，应返回明确 Unsupported error 或在构建期失败，不能静默成功。

## 39. Codex Process Group 配对

`utils/pty/src/process_group.rs` 对 Linux 提供 `prctl` parent-death signal，对其他 OS 提供同签名 no-op；对 Unix 提供 `setsid/setpgid`，非 Unix 则有 fallback。

## 40. `unix` 与 Linux 不等价

macOS 属于 Unix family，但没有 Linux `prctl(PR_SET_PDEATHSIG)`。源码因此用 `target_os = "linux"` gate parent-death syscall，却用 `unix` gate `setsid` 等更通用接口。

## 41. Module-level Gate

```rust
#[cfg(unix)]
mod unix_impl;
#[cfg(windows)]
mod windows_impl;
```

平台实现较大时应拆进不同文件，再由小 facade re-export 统一 API，避免单文件充满逐行 cfg。

## 42. `#[path = ...]` 与 Cfg

可根据条件把同一 module name 指向不同文件，但显式 `mod unix; mod windows;` 往往更易搜索。选择应优先保持 ownership 和平台 invariant 清楚。

## 43. Target-specific Dependency

Cargo manifest 可写：

```toml
[target.'cfg(target_os = "windows")'.dependencies]
windows-sys = { ... }
```

非 Windows target 不需要编译该 dependency，也不应在未 gate 的 Rust code 中引用它。

## 44. Codex Config Manifest

`config/Cargo.toml` 在 Unix 加入 `dns-lookup`/`libc`，macOS 加入 Core Foundation，Windows 加入 `windows-sys` 与所需 Win32 feature list。依赖图与源码 cfg 必须对齐。

## 45. Exact Triple Dependency

`[target.x86_64-unknown-linux-musl.dependencies]` 比 cfg predicate 更精确，只作用于那个 triple。适合 architecture/environment 特定 native package，不适合本应覆盖所有 Linux 的逻辑。

## 46. Cargo Feature 是什么

Cargo feature 是 package 定义的编译期开关，用于启用代码、依赖 feature 或 optional dependency。它不等同于 Codex 产品配置里的 runtime feature flag。

## 47. 定义 Feature

```toml
[features]
sandbox = ["v8/v8_enable_sandbox"]
```

`codex-v8-poc` 的 `sandbox` feature 不直接列出本 crate 代码，而是把能力转发给依赖 V8 的 `v8_enable_sandbox` feature。

## 48. 使用 Feature Predicate

```rust
#[cfg(feature = "sandbox")]
fn sandbox_specific() { ... }
```

Feature name 必须属于当前 package；依赖的 feature 通常由 manifest 转发/启用，而不是在当前 crate 用 `cfg(feature = "dep_feature")` 猜测。

## 49. `cfg!(feature = ...)`

V8 POC 测试把 `linked_v8_has_sandbox()` 与 `cfg!(feature = "sandbox")` 比较。这里两边表达式在两种构建中都合法，所以用 `cfg!` 形成布尔期望很合适。

## 50. Feature 的 Additive 原则

Cargo features 设计上应尽量只增加能力。依赖图中多个使用者启用的 features 会合并；若 A 需要 `foo`、B 不写 `foo`，最终 package 仍可能以 `foo` 构建。

## 51. Feature Unification

同一 package version 在同一解析图中的 feature requests 通常取并集。`default-features = false` 不能保证全图关闭 defaults；其他 dependency path 仍可能启用它们。

## 52. Resolver v2 的改进

现代 Cargo resolver 会在某些 target、build dependency、dev dependency 场景避免不必要统一，但不能据此把 feature 当互斥开关。诊断时应查看实际 resolved feature graph。

## 53. Default Feature

Package 可在 `[features] default = [...]` 声明默认集合。下游若不显式关闭就启用它；给已发布 package 随意增加 default feature 可能改变全部消费者的构建和行为。

## 54. `default-features = false`

Dependency declaration 可请求不从这条依赖边启用 defaults，再显式列出需要的 features。Codex 多处对 axum/rmcp 等这样收窄能力，但最终仍受整个依赖图统一影响。

## 55. Optional Dependency

`optional = true` 让 dependency 可由 feature 打开，现代 Cargo 常用 `dep:name` 显式关联。启用 optional dependency 会改变 dependency graph、compile time 和可能的平台要求。

## 56. Dependency Feature Forwarding

`my_feature = ["dep_crate/feature_name"]` 表示启用本 crate feature 时同时请求依赖 feature。它建立公开 feature contract，下游不必知道内部依赖的具体开关。

## 57. Features 不应表达互斥模式

若 `backend_a` 和 `backend_b` 同时启用就语义冲突，依赖图的加法合并会制造问题。可允许二者共存并运行时选择，或用 compile error 明确拒绝非法组合，但 API 设计仍要谨慎。

## 58. Feature 不是权限边界

关闭某个 feature 可让代码和依赖不进入制品，但“feature 已开启”不等于用户已获授权。Runtime 请求仍需认证、审批、sandbox 和输入验证。

## 59. Product Feature Flag

Codex 的 `codex-features`、config `[features]` 或服务端 rollout 通常决定运行时行为；Cargo feature 在构建制品时固定。两者都叫 feature，却处在不同生命周期。

## 60. 一个制品能否动态切换

Cargo feature 被编译进具体 artifact，运行时一般不能重新打开缺失代码；产品 flag 可在同一 artifact 中按配置/账户选择已编译的路径。设计文档必须说清是哪一种。

## 61. Build Script

Package root 的 `build.rs` 在编译 crate 前运行，可探测 native library、编译 C/C++、生成文件，并通过标准输出的 `cargo:` 指令影响 rustc/linker。

## 62. Build Script 不是普通 Runtime Code

它运行在构建机器上，拥有独立依赖 `[build-dependencies]` 和 OUT_DIR。Build script 的文件/网络/environment 依赖会影响 reproducibility，必须显式声明和约束。

## 63. `cargo:rustc-cfg`

```rust
println!("cargo:rustc-cfg=bwrap_available");
```

它为当前 package 的 Rust compilation 增加自定义 cfg。`bwrap_available` 不是 rustc 内建平台 predicate，而是构建探测成功后的 capability fact。

## 64. `cargo:rustc-check-cfg`

```rust
println!("cargo:rustc-check-cfg=cfg(bwrap_available)");
```

它告诉 rustc 这是预期自定义 cfg，帮助 `unexpected_cfgs` lint 发现拼写错误。声明合法名字不等于把条件设为 true。

## 65. Check 与 Set 必须分清

- `rustc-check-cfg`：允许/检查 predicate 名称和值域；
- `rustc-cfg`：本次实际启用 predicate；
- 忘记前者可能有 lint；
- 忘记后者会始终走 false branch。

## 66. Bwrap 的 Capability Probe

Build script 只有成功定位 vendored sources、libcap 并编译 C library 后才输出 `bwrap_available`。因此 cfg 表达的是“目标 Linux”之外更具体的“native implementation 已链接”。

## 67. `rerun-if-changed`

Build script 输出源文件路径，使 Cargo 在相关 C source 改变时重跑。若漏掉输入，增量构建可能继续使用陈旧结果。

## 68. `rerun-if-env-changed`

Bwrap 声明 source dir、pkg-config 和 skip 变量变化会触发重跑。环境参与构建结果却未声明，会造成“clean build 有效、incremental build 过期”的隐蔽差异。

## 69. OUT_DIR

生成的 `config.h` 写入 Cargo 提供的 OUT_DIR，而不是污染源码树。Rust 代码若用 `include!`/`include_bytes!` 读取它，也要保证路径和 rebuild dependency 正确。

## 70. Native Link Metadata

Build script 输出 `rustc-link-search` 和 `rustc-link-lib`，让最终 linker 找到 libcap；`cc::Build` 则编译 vendored C sources。Cfg 为 true 与 native symbol 真正链接必须同时成立。

## 71. Bwrap 的三个 `main`

1. Linux + `bwrap_available`：调用 C `bwrap_main`；
2. Linux + not available：启动后给出构建缺失说明；
3. 非 Linux：明确说明只支持 Linux。

三个 predicates 互斥且覆盖全部 target，保证每种配置恰好有一个 `main`。

## 72. Exhaustive Configuration Partition

设计配对 cfg 时，问两个问题：是否存在两个实现同时为 true 导致 duplicate definition；是否存在全部为 false 导致 missing symbol。用 `all/not/any` 画真值表比凭直觉可靠。

## 73. Cargo 与 Bazel 的差异

Bazel 构建 Bwrap 时禁用 Cargo build script，Linux `select` 显式加入 C target dependency，并通过 `rustc_flags_extra = ["--cfg=bwrap_available"]` 建立同一 capability。

## 74. 为什么不能只看 `build.rs`

仓库有多个构建前端时，事实可能由不同机制产生。修改自定义 cfg、native dependency 或 generated input，必须同时搜索 Cargo build script、Bazel rule/select 和 CI packaging。

## 75. 两个构建系统必须同义

Cargo 和 Bazel 不必用相同实现，但最终对 Rust crate 提供的 cfg、linked symbols、include files 和 target restrictions 必须语义一致，否则同一源码会产生构建系统特有 bug。

## 76. Unknown Cfg 拼写风险

`#[cfg(bwarp_available)]` 拼错后通常为 false，可能安静地删除真正实现。Check-cfg/lint 应覆盖自定义名称，并在关键配置用构建或行为测试证明 true branch 确实存在。

## 77. Cfg 删除会隐藏错误

在 macOS 构建 Linux-only module 不会验证其中的普通类型错误。只有对应 target/configuration 的编译才能检查该 branch；代码评审不能用“本机 cargo check 通过”代表全矩阵通过。

## 78. Feature Branch 也会腐烂

默认关闭的 feature 若 CI 从不启用，重构后可能长期无法编译。每个受支持 feature combination 至少需要定期 `check/test`，但无需穷举理论上的全部 2^N 组合。

## 79. 选择有意义的 Matrix

优先覆盖：默认 features、no-default、每个关键独立 feature、必须支持的组合、每个 native target，以及 build/test/release 中会改变 API 的配置。

## 80. Platform Behavior Test

编译成功只能证明 symbols/types 对齐。Process signal、keyring、path、console 和 sandbox 还需 native behavior tests；cross-check 常不能执行目标 binary。

## 81. Compile-only Target Check

能安装 target standard library 时，可用 `cargo check --target ...` 发现多数 platform type/import 错误；需要目标 linker/native library/build script 的 crate 仍可能要求专门工具链。

## 82. Remote/Native CI

Linux、macOS、Windows 的系统 API 最可靠地由各自 runner 验证。Wine/容器适合补充执行位置测试，但不能完全替代原生权限、终端、签名和 sandbox 行为。

## 83. Feature Graph 诊断

可用 Cargo tree 的 feature 视图追踪“哪个 dependency path 启用了某 feature”。不要只看当前 crate 一行 `default-features=false` 就断言最终解析结果。

## 84. Target Cfg 诊断

Rustc 可打印某 target 的 cfg 集合；Cargo verbose build 可显示 rustc flags。自定义 cfg 还要检查 build-script output 或 Bazel action flags。

## 85. Missing Symbol 的诊断顺序

1. 当前 host/target triple 是什么？
2. Symbol definition 有何 cfg？
3. Callsite/use/module 是否使用相同 predicate？
4. Cargo target dependency 是否存在？
5. Build script 是否设置 custom cfg/link metadata？
6. Bazel/CI 是否建立相同事实？

## 86. Duplicate Definition

两个配对实现 predicates 重叠时会得到同名重复定义。把条件化简成互斥集合，例如 `windows` 与 `not(windows)`，并避免多个近似但不等价的 OS 列表。

## 87. Unexpected Unused Warning

Import、constant 或 helper 只被某平台使用时，应与消费者一起 gate，或移动进平台 module。随意加 `allow(unused)` 会掩盖 predicate 不对齐和真正死代码。

## 88. `cfg_attr(..., allow(...))`

某 item 在特定平台因 facade 需要保留但暂未读取时，可以条件化 lint allowance。注释应解释跨平台 API shape，而不是把所有 dead code warning 一概关闭。

## 89. API Surface 差异

Public item 若只在 feature/platform 下存在，下游必须用相同 cfg 才能引用。更稳定的方案常是保持公共 facade，使用 capability query、Result::Unsupported 或 trait implementation 表达差异。

## 90. Wire Protocol 不应随本地 Feature 隐式漂移

若序列化 enum/field 因 Cargo feature 消失，不同制品可能拥有不兼容 wire schema。外部协议变化应使用明确 version/capability/experimental gate，而不是仅靠本地编译开关。

## 91. Persisted Data 也要稳定

Feature-off reader 仍可能遇到 feature-on 版本写出的 rollout/config/database data。需要兼容 reader、unknown variant 策略或显式 migration；删除类型代码不等于历史数据消失。

## 92. Security Review

- Fallback 是否 fail-open？
- 非目标平台是否意外获得弱实现？
- Feature 是否被误当授权？
- Debug-only 验证是否承担生产安全？
- Custom cfg 是否可能因探测失败安静降级？
- 不同 build system 是否链接不同 sandbox/native code？

## 93. 设计原则：按 Capability 命名

`bwrap_available` 比 `has_libcap_header` 更接近 Rust callsite 真正需要的能力。好的 custom cfg 表达已建立的完整构建事实，而不是偶然探测步骤。

## 94. 设计原则：集中平台边界

把 OS imports、FFI、error mapping 和 tests 放进同一 platform module，上层依赖小而一致的 safe API。这样新增平台时不需在整个 crate 搜索几十个 `cfg` branch。

## 95. 设计原则：Feature 保持 Additive

启用 feature 应增加 API/实现能力，不应把同一 symbol 偷换成不兼容含义。若行为需要运行时选择，使用 enum/config/capability negotiation 往往更清楚。

## 96. 修改 Cfg 的 Review 清单

- 条件来自 rustc、Cargo feature、build script 还是 Bazel flag？
- Host 与 target 是否分清？
- Predicate 用 family、OS、arch 还是 exact triple 才准确？
- 配对分支是否互斥且覆盖完整？
- use/module/dependency/linker input 是否同步 gate？
- `cfg!` 两边是否在所有 target 都能类型检查？
- Public/wire/persisted contract 是否改变？
- Cargo/Bazel/CI 是否一致？
- 哪些 matrix case 会真正编译并执行新 branch？

## 97. 源码检查点

1. `codex-rs/bwrap/build.rs`：target OS、native probe、rerun inputs、check-cfg、rustc-cfg 与 link metadata。
2. `codex-rs/bwrap/src/main.rs`：Linux capability 的三个互斥完整分支。
3. `codex-rs/bwrap/BUILD.bazel`：禁用 build script、Linux select、C dependency 和显式 `--cfg`。
4. `codex-rs/v8-poc/Cargo.toml`：本 crate feature 向 dependency feature 转发。
5. `codex-rs/v8-poc/src/lib.rs`：`cfg!(feature = "sandbox")` 与 linked native capability 测试。
6. `codex-rs/config/Cargo.toml`：Unix/macOS/Windows target-specific dependencies。
7. `codex-rs/utils/pty/src/process_group.rs`：Linux、Unix 和 fallback implementations 的不同粒度。
8. `codex-rs/app-server/src/request_processors/feedback_processor.rs`：platform import 与同签名 Windows/non-Windows facade。
9. `codex-rs/app-server-protocol/src/lib.rs`：test/production modules 和 derive provider 切换。
10. `codex-rs/app-server-protocol/src/precomputed_exports.rs`：`cfg_attr(test, derive(...))`。

## 98. 搜索命令

```bash
rg -n '#\[cfg\(|#\[cfg_attr\(|cfg!\(' codex-rs
rg -n 'cfg\(feature|\[features\]|default-features|optional = true' codex-rs --glob 'Cargo.toml' --glob '*.rs'
rg -n 'rustc-cfg|rustc-check-cfg|CARGO_CFG_TARGET|rerun-if-' codex-rs --glob 'build.rs'
rg -n '^\[target\.|target_os|target_arch|target_env|target_family' codex-rs --glob 'Cargo.toml' --glob '*.rs'
rg -n 'rustc_flags_extra|build_script_enabled|select\(' codex-rs --glob 'BUILD.bazel' --glob '*.bzl'
```

## 99. 小练习：画 Bwrap 真值表

列出 `target_os=linux` 与 `bwrap_available` 的四种组合，指出哪个 `main` 被保留。再解释为什么非 Linux + `bwrap_available=true` 在正常构建协议中不应出现，但源码仍由非 Linux fallback 覆盖。

## 100. 小练习：选择 `cfg` 还是 `cfg!`

分别处理三种需求：选择 executable 文件名、调用 Unix-only Trait、给测试类型额外 derive。为每种选择 `cfg!`、`#[cfg]` 或 `#[cfg_attr]`，并说明另一种写法的问题。

## 101. 小练习：追踪 Feature 来源

从 `codex-v8-poc` 的 `sandbox` feature 出发，画出它怎样启用 `v8/v8_enable_sandbox`，再说明为什么测试仍要调用 native symbol 验证 linked V8，而不能只断言 cfg 为 true。

## 102. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Conditional compilation | 条件编译 | 按构建 predicate 决定代码是否进入 crate |
| `cfg` / predicate | 配置/谓词 | 条件 attribute 和判断其真假的配置事实 |
| `all` / `any` / `not` | 全部/任一/取反 | 组合 cfg predicate 的布尔运算 |
| `cfg!` | 配置布尔宏 | 生成 true/false 但不删除另一 branch 的内建宏 |
| `cfg_attr` | 条件属性 | 条件为真时应用另一个 attribute |
| `test` / `debug_assertions` | 测试/调试断言 | Test harness 和调试断言是否启用的编译配置 |
| Host / target | 主机构建平台/目标平台 | 运行编译工具的机器和制品准备运行的平台 |
| Target triple | 目标三元组 | 描述 architecture、vendor、OS、environment 的 target 标识 |
| Target family / OS / arch / env | 平台族/系统/架构/环境 | 不同精度的内建 target predicates |
| Cross-compilation | 交叉编译 | Host 与 target 不同的构建过程 |
| Platform facade | 平台门面 | 对上层保持同一 API、内部选择不同 OS 实现 |
| Target-specific dependency | 目标专用依赖 | 只在匹配 Cargo target table 时进入依赖图的 package |
| Cargo feature | Cargo 功能开关 | Package 编译期的 additive capability selection |
| Feature forwarding | Feature 转发 | 本 crate feature 启用 dependency feature |
| Feature unification | Feature 合并 | 多条 dependency path 请求的 feature 取并集 |
| Default/optional feature | 默认/可选功能 | 默认开启集合和由 feature 控制的依赖能力 |
| Runtime feature flag | 运行时功能开关 | 制品启动后按配置/账户选择已编译行为的开关 |
| Build script / `build.rs` | 构建脚本 | 在编译 crate 前于 host 执行的构建程序 |
| Custom cfg | 自定义配置 | Build system 通过 rustc flag 建立的项目 predicate |
| `rustc-check-cfg` / `rustc-cfg` | 检查配置/设置配置 | 声明合法 predicate 与实际启用它的构建指令 |
| Capability probe | 能力探测 | 验证 native source/library/tool 是否真正可用的构建步骤 |
| `rerun-if-*` | 满足条件则重跑 | 声明 build-script 文件和环境输入的 Cargo metadata |
| OUT_DIR / link metadata | 输出目录/链接元数据 | 生成中间文件的位置和传给 linker 的 library 信息 |
| Configuration matrix | 配置矩阵 | Feature、target、profile、test/build-system 的受支持组合 |

## 103. 常见误解

- “`#[cfg]` 等于运行时 if”：False item 在当前构建中通常根本不存在。
- “`cfg!` 能隐藏平台不合法代码”：两个 branch 通常仍需通过解析和类型检查。
- “`unix` 就是 Linux”：macOS/BSD 也可能属于 Unix family，Linux syscall 需更精确条件。
- “运行在 macOS 就一定 target macOS”：Cross-compilation 时 host 与 target 不同。
- “`cfg(test)` 对所有依赖都启用”：Dependency 通常按普通 library configuration 构建。
- “Cargo feature 是用户配置开关”：它在构建 artifact 时固定，不是运行时 rollout flag。
- “`default-features=false` 保证依赖没有默认 feature”：其他依赖路径仍可能通过统一启用。
- “Features 可以安全表示互斥 backend”：Cargo feature 以加法合并为基本模型。
- “Custom cfg 写在源码中就自动存在”：必须由 rustc/build script/build system 实际设置。
- “本机 check 通过说明所有 cfg branch 正确”：被删除的平台/feature branch 完全没被检查。
- “Cargo 和 Bazel 会自动执行同一 build logic”：它们可能用不同机制，必须显式保持语义一致。
- “No-op fallback 总能提高兼容性”：安全关键能力缺失时静默 no-op 可能是 fail-open。

## 104. 一句话收束

条件编译不是给运行时逻辑换一种写法，而是在 target、feature、test、profile 和构建系统共同决定的配置点上生成不同程序；可靠设计要让 predicates 表达真实 capability，让平台依赖、源码、native link 和 public contract 同步，并用有意义的构建与原生测试矩阵持续证明每条被支持的分支没有腐烂。
