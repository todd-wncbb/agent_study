# 80：Rust Unsafe、FFI、原始指针、OS Handle 与安全边界

> 源码基线：`4ee41929eaf4`。本章解决“当 Rust 必须调用 C 或操作系统 API 时，怎样把编译器无法证明的前提缩小、写清并重新包装成安全接口”。

## 1. 本章解决什么问题

看到 `unsafe`，初学者常在两个极端之间摇摆：要么觉得“这里一定危险”，要么觉得“加上 unsafe 编译器就不管了”。两种理解都不对。

本章要学会逐句回答：这次操作为什么需要 `unsafe`、调用前必须成立什么、代码怎样建立这些条件、条件能维持多久、资源最后由谁释放。

## 2. 先说人话：Unsafe 是一份人工证明

Safe Rust 让编译器替你证明引用有效、别名规则和许多所有权条件。`unsafe` 表示：这几个特定条件编译器证明不了，程序员在这里接手证明责任。

因此，`unsafe` 更像“人工验收签字”，不是“关闭所有安全检查”。

## 3. `unsafe` 不会关闭借用检查器

在 `unsafe` block 中，普通变量仍然受 move、borrow、lifetime 和类型检查约束。它只额外允许少数 unsafe operations，例如解引用 raw pointer、调用 unsafe function。

## 4. Safe Code 与 Unsafe Code

- Safe code：只使用编译器认为无需额外前提的操作；
- Unsafe code：执行了调用者必须另外证明前提的操作；
- Sound safe API：内部可以有 unsafe，但任何合法的 safe caller 都不能借它制造 Undefined Behavior。

## 5. Unsafe Block

```rust
let value = unsafe { *ptr };
```

Block 表示只有这一小段执行了 unsafe operation。理想状态是 block 很小，验证、错误检查和普通控制流留在外面。

## 6. Unsafe Function

```rust
unsafe fn read_value(ptr: *const u32) -> u32 {
    unsafe { *ptr }
}
```

`unsafe fn` 表示调用者必须满足函数的 safety contract。函数作者仍应把函数体内每个 unsafe operation 当成单独证明点。

## 7. Unsafe Function 与 Unsafe Block 的责任不同

- 调用 `unsafe fn`：调用者证明函数文档中的前提；
- 编写 `unsafe fn`：实现者证明“只要前提成立，函数体就不会破坏安全”；
- 写 `unsafe { ... }`：明确指出哪一步依赖该证明。

## 8. Unsafe Trait

`unsafe trait` 表示实现该 Trait 会向其他代码作出编译器无法验证的承诺。典型例子是线程安全相关的 `Send`、`Sync`；错误实现可能让完全 safe 的调用者触发数据竞争。

## 9. Safety Contract

Safety contract 是调用 unsafe API 前必须成立的条件，例如：

- 指针非空、正确对齐且指向已初始化值；
- 这段内存至少有 `len` 个元素；
- handle 当前有效并由调用者拥有；
- callback 在调用期间一直存活；
- 没有违反共享引用和可变引用的别名规则。

## 10. Safety Invariant

Invariant 是代码在某个范围内持续维护的事实。Contract 更像边界输入要求；invariant 更像对象或模块在整个生命周期中始终保证的状态。

## 11. Undefined Behavior

Undefined Behavior，简称 UB，表示程序违反了 Rust/目标平台要求，编译器不再保证程序含义。它不等于“稳定报错”或“偶尔崩溃”；看似正常运行也不能证明没有 UB。

## 12. Memory Safety 与业务正确性不同

向错误 PID 发信号可能是严重业务事故，但不一定造成 Rust UB；解引用 dangling pointer 可能造成 UB，即使当次看起来读到了预期数字。Unsafe review 首先证明 memory/type safety，之后仍要审查业务和安全策略。

## 13. Raw Pointer

`*const T` 是指向 T 的只读意图 raw pointer，`*mut T` 是可变意图 raw pointer。Raw pointer 本身不携带 Rust reference 那样完整的有效性、生命周期和别名保证。

## 14. 创建 Raw Pointer 不一定 Unsafe

```rust
let value = 7_u32;
let ptr = &value as *const u32;
```

产生指针通常可以是 safe 的；真正解引用它需要 unsafe，因为从 raw pointer 类型本身无法证明 pointee 仍存在且可访问。

## 15. Null Pointer

Null 常被 C API 用作“没有值”或失败 sentinel。Rust reference 不能为 null；raw pointer 可以。把 raw pointer 转为 reference、slice 或 owner 前必须按 API contract 检查 null。

## 16. Dangling Pointer

Dangling pointer 曾经指向有效对象，但对象已释放、移动到不允许的地址或资源已关闭。地址中仍有数字不代表该地址仍能合法访问。

## 17. Alignment

类型 T 通常要求地址按某个字节边界对齐。外部 byte buffer 不保证适合直接 cast 成 `*const T`；必要时应验证 alignment，或使用明确允许未对齐访问的操作。

## 18. Initialization

一块已分配内存不等于里面已有合法 T。某些 bit pattern 对 `bool`、reference 或 enum 无效；读取未初始化或无效表示可能造成 UB。

## 19. Provenance

Pointer provenance 可理解为“这个指针从哪一块合法分配和访问权而来”。仅把整数拼成一个相同地址，并不自动恢复对该对象的合法访问权。

## 20. Aliasing

Rust 的核心规则之一是：共享引用可有多个但不可同时被不受控地修改；独占可变引用在其有效期内不能有其他冲突访问。Raw pointer 绕过静态检查，不代表规则消失。

## 21. Pointer Arithmetic

`ptr.add(n)` 按 T 的元素大小前进 n 个位置，而不是 n 个字节。调用者还要保证结果没有越出允许的 allocation 范围；整数溢出检查不能替代 pointer validity 证明。

## 22. `slice::from_raw_parts`

```rust
let items = unsafe { std::slice::from_raw_parts(ptr, len) };
```

这一步把“地址 + 长度”升级成 Rust slice，承诺范围连续、已初始化、对齐、总大小合法，并且在借用期间不会发生冲突修改。

## 23. 长度必须先做 Checked Arithmetic

如果外部数据给出 count，先用 `checked_mul`、`checked_add` 计算所需字节数，再验证不超过真实 buffer。先构造 slice、后检查长度，证明顺序已经反了。

## 24. Codex 的 Windows Buffer 案例

`windows_tcp_attribution.rs` 从 Windows API 得到 `TOKEN_GROUPS` 风格 buffer。源码先验证 `byte_len`、结构 offset，再对 `group_count * element_size + offset` 使用 checked arithmetic，最后才构造 slice。

## 25. 为什么 Buffer 用 `Vec<usize>`

该案例的 `aligned_buffer` 用 `Vec<usize>` 承载原始 bytes，不只是为了容量；它同时提供至少适合 `usize` 的 alignment。之后仍需验证布局和长度，alignment 只解决一个条件。

## 26. `read_unaligned`

`std::ptr::read_unaligned` 允许从不满足 T 常规 alignment 的地址复制一个值。它只放宽 alignment 前提，不会自动解决越界、未初始化、无效 bit pattern 或生命周期问题。

## 27. `NonNull<T>`

`NonNull<T>` 表示一个已知非空的 raw pointer，并利用这一事实帮助类型布局和 API 表达。它仍不证明 pointee 存活、对齐、已初始化或独占。

## 28. `MaybeUninit<T>`

`MaybeUninit<T>` 用于“存储已经存在，但 T 还未由外部 API 初始化”的阶段。只有确认初始化成功后才能 `assume_init`；把 zeroed 当成任意 T 的默认值是不安全的。

## 29. `ManuallyDrop<T>`

`ManuallyDrop` 抑制自动 Drop，常用于特殊 ownership 转移或 union 实现。它不会替你释放资源；调用者必须证明既不会 double-drop，也不会忘记最终清理。

## 30. FFI

FFI 是 Foreign Function Interface，即 Rust 与其他语言/二进制接口交互的边界。Codex 会借它调用 libc、Win32、Core Foundation，以及链接进来的 C 实现。

## 31. ABI

ABI 是 Application Binary Interface，规定参数如何传递、symbol 如何命名、返回值和 stack 如何组织。函数签名字段看似一致，调用约定不一致仍可能破坏执行。

## 32. `extern "C"`

```rust
unsafe extern "C" {
    fn bwrap_main(argc: libc::c_int, argv: *const *const c_char) -> libc::c_int;
}
```

`extern "C"` 指定 C ABI；`unsafe extern` 表示编译器无法验证外部 symbol 的真实实现是否遵守声明。

## 33. 声明不是验证

Rust 能检查你按声明传入 `c_int` 和 pointer，却不能检查链接到的 C 函数是否真有同一签名、是否写越界、是否保留 pointer，或何时释放返回资源。

## 34. `repr(C)`

跨 FFI 共享 struct/enum 时通常需要 `#[repr(C)]` 固定与 C 兼容的 layout。Rust 默认 representation 不承诺字段顺序和 padding 可作为外部协议。

## 35. C 类型别名

`libc::c_int`、`c_char`、平台 SDK 的 `HANDLE` 等表达目标 ABI 的类型。不要凭“本机都是 32 位整数”随意换成 Rust 类型；跨 target 宽度和 signedness 可能不同。

## 36. C String

C string 以第一个 NUL byte 结束，不携带 Rust `String` 的长度与 UTF-8 保证。`CString` 是 owned、内部无 NUL 且末尾有 NUL 的构造；`CStr` 是借用视图。

## 37. Codex 的 `bwrap_main` 案例

`codex-rs/bwrap/src/main.rs` 先把每个 OS argument 转为 `CString`，再收集其指针，最后追加 null pointer，构造 C 惯例的 `argv`。

## 38. `argv` 的两层生命周期

`argv_ptrs` 保存的是指向各个 `CString` 内容的 pointer。因此必须同时保证：`cstrings` 元素没有在调用前销毁，`argv_ptrs` 自身也没有在调用期间失效。

## 39. 为什么先收集 `CString` 再收集 Pointer

这样 ownership 关系最容易看懂：`cstrings` 先完整拥有所有字符串，`argv_ptrs` 再借出每个字符串的地址。同步调用结束前两个 vector 都没有离开 scope，因此 pointer 所指的字符串 storage 一直存活。

## 40. Null-terminated Pointer Vector

C 的 `argv` 不仅每个字符串以 NUL 结束，pointer 数组还常以 null pointer 收尾。两种 terminator 属于不同层，不能混为一谈。

## 41. `argc` 与 `argv`

源码传给 `argc` 的是实际 argument 数量 `cstrings.len()`，不包含末尾 sentinel；`argv` 则包含额外 null。二者必须互相一致并满足被调用 C API 的约定。

## 42. Safety Comment 应证明什么

该调用前的注释说明了两个关键事实：vector 已 null-terminated，所有 pointer 在调用期间有效。好注释不是“调用 C 所以 unsafe”，而是逐项对应 callee contract。

## 43. Callback

FFI callback 是把 Rust function pointer 和常见的 `void *context` 交给外部库，稍后由外部调用。要证明 ABI、context 类型、存活时间、线程约束、调用次数和释放时机。

## 44. Callback 不能让 Panic 穿过未知 ABI

除非 ABI 和两侧合同明确支持 unwind，否则不要让 Rust panic 穿过 FFI 边界。通常在 callback 内转换为 error/status，或在受控边界捕获并终止当前操作。

## 45. OS Resource 不是普通整数

Unix fd 和 Windows HANDLE 在底层看似整数或 pointer，但它们代表内核资源的访问权和生命周期。数值相同不代表同一时刻仍是同一资源；关闭后编号还可能被复用。

## 46. Borrowed Raw Handle

`as_raw_fd()`、`as_raw_handle()` 暂时借出底层标识。原 owner 仍负责关闭；调用外部函数期间必须让 owner 保持存活，外部函数也不能擅自接管 ownership。

## 47. Consuming Raw Handle

`into_raw_fd()`、`into_raw_handle()` 消费 Rust owner 并交出 raw resource。自动 Drop 不再关闭它；接收方必须接管或最终恢复成 owned wrapper，否则会泄漏。

## 48. 从 Raw Handle 建立 Owner

`File::from_raw_fd`、`OwnedHandle::from_raw_handle` 表示“从现在开始，这个 Rust value 独占负责关闭资源”。调用者必须保证 raw value 有效且 ownership 尚未被其他 owner 持有。

## 49. Double Close

若同一个 fd 同时被两个 `File::from_raw_fd` 接管，两个 Drop 都会 close。更糟的是，第一次 close 后编号可能被系统复用，第二次 close 可能关闭一个完全不同的新资源。

## 50. Resource Leak

如果调用 `into_raw_fd` 后既没传给明确接管者，也没恢复成 owner，Rust Drop 无法清理它。Unsafe 不只会造成非法访问，也可能让系统资源永久滞留。

## 51. RAII Wrapper

`File`、`OwnedFd`、`OwnedHandle` 把 raw resource 变成 RAII owner：构造成功后由 Drop 自动关闭。Safe wrapper 的价值是把“每条 return path 都记得 close”变成结构性保证。

## 52. Codex 的 Windows OwnedHandle 案例

`owned_handle` 先把 Win32 返回的 `HANDLE == 0` 解释为失败；成功后才调用 `OwnedHandle::from_raw_handle`。从这一行开始，Rust wrapper 对 handle 的关闭负责。

## 53. `as_raw_handle` 为什么不是 Ownership Transfer

调用 `OpenProcessToken(process.as_raw_handle(), ...)` 时，`process` 仍在当前 scope 存活。API 只借用 handle 来执行操作，没有取得关闭它的责任。

## 54. 两阶段 Windows Buffer Query

许多 Win32 API 第一次以 null buffer 查询所需长度，返回 `ERROR_INSUFFICIENT_BUFFER`；第二次传入已分配 buffer 读取内容。第一次的“失败码”在这个协议中其实是预期控制流。

## 55. Error Convention 属于 FFI Contract

不同 API 用 `-1`、0、null、HRESULT 或线程局部 last-error 表示失败。不能统一假设“非零就是错误”；必须按具体函数文档解释返回值，并及时读取对应错误源。

## 56. `last_os_error`

调用失败后应尽快构造 `io::Error::last_os_error()`，避免中间调用覆盖线程局部 errno/GetLastError。错误 message 可以添加操作上下文，但不能丢失原始错误分类。

## 57. Unix File Descriptor 借用案例

`linux-sandbox/src/exec_util.rs` 从 `&[File]` 调用 `as_raw_fd()`，再用 `fcntl` 读取和修改 `FD_CLOEXEC`。`File` slice 在调用期间保活，因此 fd 是 borrowed，而非新 owner。

## 58. `FD_CLOEXEC`

Close-on-exec flag 表示成功执行新程序映像时自动关闭 fd。清除它会让指定资源传给被 exec 的程序；这既是资源生命周期选择，也是权限传播边界。

## 59. `fcntl(F_GETFD)`

源码先读取当前 flags，检查负返回值，再只清除 `FD_CLOEXEC` bit。它没有凭空覆盖其他 flags，因此维持了“只改变目标属性”的局部不变量。

## 60. Bit Mask

`flags & !FD_CLOEXEC` 的意思是把该 bit 清零、保留其他 bits。`| FLAG` 设置 bit，`& !FLAG` 清除 bit，`& FLAG != 0` 检查 bit；这类表达式常出现在 OS API 周围。

## 61. Safe Wrapper 应在边界前验证

理想流程是：safe input → 验证/转换 → 最小 unsafe call → 立刻检查结果 → 尽快包装成 owned safe type。不要让 raw pointer/handle 在业务层四处传播。

## 62. Windows Allocator Pairing

`SHGetKnownFolderPath` 成功后返回由 `CoTaskMem` 分配的 UTF-16 pointer，必须用 `CoTaskMemFree` 释放。用 Rust allocator、`free` 或错误 API 释放都违反 allocator pairing。

## 63. Codex 的 Known Folder 案例

源码先检查 HRESULT，再检查 null；随后扫描 UTF-16 terminator，构造 slice 和 `PathBuf`，最后调用 `CoTaskMemFree`。Pointer 的读取与释放责任在一个局部 block 中完成。

## 64. 早退与清理风险

手工 raw pointer cleanup 容易在新增 `?` 或 early return 时泄漏。复杂逻辑应尽快建立带 Drop 的 guard/wrapper，让所有正常、错误和 panic unwind 路径共享清理规则。

## 65. `pre_exec`

Unix 的 `CommandExt::pre_exec` 注册一段在 `fork` 后、`exec` 前于子进程执行的 closure。它是 unsafe API，因为多线程进程 fork 后的执行环境受到严格限制。

## 66. Fork 后为什么特殊

Fork 只复制调用线程，但内存里可能保留其他线程持有状态时的锁。子进程若调用会获取这些锁的普通 runtime/library 功能，可能永久死锁或违反库约定。

## 67. Async-signal-safe

在 fork 后、exec 前通常只能使用平台允许的 async-signal-safe 操作，并避免分配、复杂日志、普通 mutex 和不可证明安全的库调用。这里的“async”不是 Rust `async fn`，而是 Unix signal 语境。

## 68. Codex 的 Spawn 案例

`core/src/spawn.rs` 在 `pre_exec` 中按 stdio policy 脱离 TTY；Linux 上还设置 parent-death signal，使父 Codex 被杀后 child 收到 SIGTERM。

## 69. Parent PID 的竞态

源码在 fork 前记录 parent PID，并把它交给 `set_parent_death_signal`。Helper 在 `prctl` 成功后再次读取 `getppid()`；若已经不是原 parent，便主动向自己发 SIGTERM，补上“父进程恰好在设置期间退出”的竞态窗口。

## 70. Process Group 与负 PID

Unix `kill(pid, signal)` 对正 PID、0、负 PID 有不同含义；负值常表示 process group。类型系统只看到整数，因此调用前必须证明数值语义和目标身份正确。

## 71. PID Reuse

进程退出后 PID 可被操作系统复用。保存一个整数稍后发 signal，必须考虑它是否仍代表原进程；资源 handle、generation 或父子关系验证比裸 PID 更强。

## 72. Platform `cfg`

`#[cfg(unix)]`、`#[cfg(windows)]` 不只是让不同 import 编译通过；它承认 fd/HANDLE、error convention、ABI、进程模型和字符串编码是不同合同，不能硬凑成一套假抽象。

## 73. Safe Abstraction 的形状

一个好的 safe wrapper 通常：

1. 用类型表达 ownership；
2. 构造时验证 raw input；
3. 把 unsafe block 缩到必要操作；
4. 将平台失败映射为 Result；
5. 用 Drop/RAII 自动清理；
6. 不向 safe caller 暴露隐藏前提。

## 74. Safety Comment 写作模板

```text
// SAFETY:
// - ptr 来自哪个 API/owner；
// - 为什么非空、对齐、已初始化；
// - 可读写范围是多少；
// - 为什么在本次调用期间仍存活；
// - ownership 是否转移、最后由谁释放。
```

不需要每次机械写五行，但关键条件不应只存在作者脑中。

## 75. Comment 要贴近 Unsafe Operation

注释放在 block 或 call 前，reviewer 才能把证明与具体操作一一对应。模块顶部一句“这些 pointer 都有效”很容易在后续修改中失效。

## 76. 最小化 Unsafe Scope

不要把整个函数包进一个大 `unsafe {}`。大 block 会让新增代码自动获得危险能力，也难以判断每条语句依赖哪项前提。

## 77. Unsafe 数量不是唯一指标

十个各自有清晰 contract 的单行 FFI call，可能比一个把 raw pointer 长期存进共享对象的 block 更易审计。关注 invariant 的复杂度、持续时间和调用者数量。

## 78. Unsafe 与并发

Mutex 或 atomic 只能协调访问顺序，不能让 dangling/misaligned pointer 变有效。反过来，pointer 有效也不能证明跨线程共享符合 Send/Sync 和数据竞争规则。

## 79. Unsafe 与 `Pin`

Pin 保护地址稳定合同，但只有在 safe API 不提供移动 pointee 的漏洞时才有效。编写 pin projection 或自引用类型的 unsafe code，必须证明对象不会在依赖地址期间被移动。

## 80. Unsafe 与 Cancellation

Async Future 在 await 点被 drop 时，RAII owner 会清理；裸资源和外部 callback 可能仍在运行。Safe wrapper 必须定义取消时注销、等待或转移 ownership 的协议，避免 callback 访问已释放 context。

## 81. 输入验证与 Unsafe 的先后

来自网络、文件或 OS 的 count、offset、tag 都是不可信数据。先在 safe code 中做长度、overflow、variant 和权限验证，再把最小、已验证视图交给 unsafe operation。

## 82. 测试不能单独证明 Soundness

测试只能覆盖运行到的输入和平台；UB 还可能因优化、alignment、并发时序或 target ABI 才出现。测试是证据之一，contract 推理和专用工具同样必要。

## 83. Miri

Miri 解释执行 Rust MIR，可发现许多 invalid access、aliasing 和未初始化读取。它不能运行所有 FFI/OS 行为，也不能证明未覆盖路径安全。

## 84. Sanitizer

AddressSanitizer、ThreadSanitizer、MemorySanitizer 等在原生执行中查找越界/use-after-free、数据竞争或未初始化使用。支持范围和误报/漏报取决于平台与构建方式。

## 85. Fuzzing

对 buffer parser、长度/offset 转换和 FFI 前置验证做 fuzz，可持续探索边界组合。Oracle 应至少断言无 panic、无 sanitizer/Miri failure，并保持结构不变量。

## 86. 跨平台测试

Linux 上通过不能验证 Windows HANDLE 和 UTF-16 cleanup，macOS 测试也不能覆盖 Linux fork/prctl。平台 FFI 需要对应 native CI；可以把纯 buffer validation 抽成跨平台 safe test。

## 87. Unsafe Review：先从 Callee Contract 开始

不要先读 comment 并相信它。先查 unsafe function/OS API 的正式 contract，再逐条在 callsite 找证据；缺少任何一项就记录为未证明，而不是凭经验补全。

## 88. Unsafe Review 清单

- Raw input 的来源和值域是什么？
- Null、alignment、length、overflow 是否验证？
- Pointer/handle 在调用期间由谁保活？
- 是借用还是 ownership transfer？
- 成功和失败分别由谁释放？
- 外部函数是否保留 pointer/callback？
- Return/error convention 是否正确解释？
- 并发、fork、panic、取消和平台差异是否改变 contract？

## 89. 常见故障定位

- 随优化级别变化：优先怀疑 UB、未初始化、aliasing；
- 只在特定架构失败：检查 alignment、width、endianness、ABI；
- 偶发关闭错误资源：检查 double close、编号复用和 ownership；
- FFI 后随机崩溃：检查签名、buffer 长度、callback lifetime；
- Fork 后卡死：检查 pre-exec 中锁、分配和非安全调用；
- 长期 handle 增长：检查 early return 和 ownership transfer 后的泄漏。

## 90. 源码检查点

1. `codex-rs/bwrap/src/main.rs`：`unsafe extern "C"`、CString、两层 argv 和同步 FFI 调用。
2. `codex-rs/linux-sandbox/src/exec_util.rs`：borrowed fd、`fcntl`、错误检查和局部 SAFETY 注释。
3. `codex-rs/config/src/loader/mod.rs`：Windows known-folder pointer、UTF-16 读取和 `CoTaskMemFree` 配对。
4. `codex-rs/network-proxy/src/windows_tcp_attribution.rs`：两阶段 buffer query、checked layout、slice 构造和 OwnedHandle。
5. `codex-rs/core/src/spawn.rs`：Unix `pre_exec`、TTY/process lifecycle 设置和平台 cfg。
6. `codex-rs/utils/pty/src/process_group.rs`：`prctl`、parent PID recheck、`setsid`、process group signal。
7. `codex-rs/linux-sandbox/src/linux_run_main.rs`：fork、pipe、signal、`File::from_raw_fd` 和显式 close 的综合边界。

## 91. 搜索命令

```bash
rg -n 'unsafe \{|unsafe fn|unsafe extern|SAFETY:' codex-rs
rg -n 'from_raw_fd|into_raw_fd|as_raw_fd' codex-rs
rg -n 'from_raw_handle|into_raw_handle|as_raw_handle' codex-rs
rg -n 'pre_exec|libc::fork|libc::kill|libc::fcntl' codex-rs
rg -n 'from_raw_parts|read_unaligned|MaybeUninit|NonNull' codex-rs
```

## 92. 小练习：审计 `bwrap_main`

不看 SAFETY comment，自己列出 `bwrap_main(argc, argv)` 的前提。分别指出：字符串 terminator、pointer-vector terminator、`argc`、pointer lifetime 和同步调用怎样由源码建立。

## 93. 小练习：辨认 Handle Ownership

对 `as_raw_fd`、`into_raw_fd`、`from_raw_fd` 各写一句：“调用前谁拥有、调用后谁拥有、谁负责 close”。再解释为什么把同一个 raw fd 调用两次 `from_raw_fd` 是错误的。

## 94. 小练习：把 Raw Buffer 升级成 Slice

假设外部 buffer 头部给出 count，后面是固定大小 records。写出构造 slice 前的检查顺序：最小 header、读取 count、checked multiply/add、与实际 byte length 比较、alignment/representation、最后 `from_raw_parts`。

## 95. Glossary：代码单词与短语

| 词或短语 | 直译 | 在代码中的含义 |
|---|---|---|
| Unsafe block / operation | 不安全块/操作 | 局部启用需要人工证明前提的语言能力 |
| Unsafe function / contract | 不安全函数/合同 | 调用者必须满足额外 safety requirements 的函数 |
| Soundness / UB | 健全性/未定义行为 | Safe caller 不会触发 UB，以及违反语言前提后的无保证状态 |
| Invariant | 不变量 | 某个对象或范围内必须持续成立的事实 |
| Raw pointer / pointee | 原始指针/被指向值 | 不携带完整引用保证的地址值及其目标对象 |
| Null / dangling | 空/悬垂 | 没有目标的 sentinel，以及目标已不再有效的 pointer |
| Alignment / initialization | 对齐/初始化 | 地址边界要求和内存中是否已形成合法 T |
| Provenance / aliasing | 来源权/别名 | Pointer 的合法来源，以及同一内存存在多个访问路径的规则 |
| FFI / ABI | 外部函数接口/二进制接口 | 跨语言调用边界和机器级调用、布局约定 |
| `extern "C"` / `repr(C)` | C 调用约定/C 布局 | 固定函数 ABI 和数据 representation 的标记 |
| `CString` / `CStr` / NUL | C 字符串/借用 C 字符串/零字节 | Owned/borrowed 的 NUL-terminated byte string 形式 |
| Sentinel | 哨兵值 | 用 null、-1、0 等表示结束或失败的特殊值 |
| File descriptor / fd | 文件描述符 | Unix 内核资源表中的进程局部整数 handle |
| HANDLE / raw handle | 句柄/原始句柄 | Windows 内核或 API resource 的无类型底层标识 |
| Borrow / ownership transfer | 借用/所有权转移 | 临时使用资源与接管最终清理责任 |
| `as_raw_*` / `into_raw_*` / `from_raw_*` | 查看/交出/接管原始资源 | 借用标识、消费 owner、从 raw 建立新 owner 的三类 API |
| RAII / Drop | 资源获取即初始化/析构 | 让 value scope 自动承担 close/free 的机制 |
| Double close / leak | 重复关闭/泄漏 | 两个 owner 清理同一资源，以及无人最终清理资源 |
| Allocator pairing | 分配器配对 | 使用创建资源的 API 所规定的对应释放函数 |
| `errno` / `GetLastError` / HRESULT | Unix 错误号/Windows 最近错误/结果码 | 不同平台 API 的失败编码与诊断来源 |
| `pre_exec` / fork / exec | 执行前钩子/复制进程/替换程序 | Unix 子进程在 fork 后、加载目标程序前的受限阶段 |
| Async-signal-safe | 异步信号安全 | Unix signal/fork 特殊上下文允许调用的有限操作性质 |
| `FD_CLOEXEC` | 执行时关闭 | exec 新程序时由内核自动关闭 fd 的 flag |
| Checked arithmetic | 检查式算术 | 计算 offset/length 时显式拒绝整数 overflow |
| Miri / sanitizer / fuzzing | MIR 解释器/检测器/模糊测试 | 从语义、原生执行和输入探索三个方向寻找 unsafe 缺陷 |

## 96. 常见误解

- “Unsafe block 中编译器完全不检查”：类型、move、borrow 等检查仍在。
- “代码里没有 unsafe 就绝不可能出问题”：业务错误、安全漏洞、死锁和被错误 safe wrapper 隐藏的 UB 仍可能存在。
- “出现 unsafe 就说明作者写坏了”：FFI 和 OS API 必然需要某处承担人工证明，关键是边界是否健全。
- “非空 pointer 就可以解引用”：还需要对齐、范围、初始化、provenance 和 aliasing 条件。
- “Raw handle 是整数，复制整数就是复制资源”：复制标识不会复制 ownership，可能导致 double close。
- “`from_raw_fd` 只是类型转换”：它接管 close 责任，是 ownership 操作。
- “API 返回错误就一定没有输出”：有些两阶段 API 用特定错误告诉你所需 buffer 大小。
- “Mutex 能让 raw pointer 安全”：锁只协调并发，不证明 pointer validity。
- “测试通过就证明没有 UB”：未覆盖路径、优化和其他 target 仍可能暴露问题。
- “SAFETY 注释写得长就可信”：它必须逐项对应真实 callee contract 和当前代码证据。

## 97. 一句话收束

Unsafe Rust 的核心不是勇敢地操作指针，而是把编译器无法证明的事实限制在最小边界：先验证 raw 输入，用精确 contract 证明 pointer/ABI/lifetime/ownership，再立刻检查外部结果并恢复成 RAII safe type；谁拥有、谁保活、谁释放始终必须只有一个明确答案。
