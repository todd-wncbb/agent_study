# 66：Rust 错误系统——`Result`、`Option`、`?`、`thiserror`、`anyhow` 与 Panic 边界

> 源码基线：`4ee41929eaf4`
>
> 第 22 章区分模型、工具、取消和内部失败，第 59 章讨论进程错误分类、重试与用户诊断。本章不再重复那些运行策略，而是深入 Rust 本身：失败怎样成为普通 value，`?` 怎样提早返回并转换 error，typed enum 怎样保存机器可判定语义，`anyhow` 怎样在应用层连接上下文与 cause chain，以及 async task 为什么同时存在业务 `Result` 和 `JoinError`。

## 1. 本章解决什么问题

看到下面代码，你应该能逐层解释，而不是只读成“出错就返回”：

```rust
let result = task::spawn_blocking(move || write_atomically(&path, ""))
    .await
    .map_err(|err| Error::join("write task failed", err))?
    .map_err(|err| Error::io("write file failed", err))?;
```

本章回答：

- `Result<T, E>` 和 `Option<T>` 为什么都是普通 enum？
- `?` 对 `Result` 和 `Option` 分别做什么？
- `From` 如何让 `?` 自动转换 error type？
- `map`、`map_err`、`and_then`、`ok_or_else`、`transpose` 怎样选？
- Typed error enum 与 `anyhow::Error` 各适合哪一层？
- `thiserror` 的 `#[error]`、`#[source]`、`#[from]`、`transparent` 生成什么？
- `anyhow::Context` 如何保留底层原因？
- 为什么把 error 变成 String 会切断类型链？
- `Display`、`Debug`、`{error:#}` 各面向谁？
- Panic、`unwrap`、`expect` 与 recoverable error 有何区别？
- `JoinHandle<Result<T, E>>` 为什么 await 后得到两层 `Result`？
- `JoinError::is_cancelled()` 和 panic 分别代表什么？
- 怎样设计稳定分类、可行动上下文、可检查 source chain 与安全公开消息？

## 2. 先说人话：错误是值，不是另一条神秘通道

Rust 函数常返回：

```rust
Result<Config, ConfigError>
```

它就是：

```rust
enum Result<T, E> {
    Ok(T),
    Err(E),
}
```

Caller 必须决定如何处理两个 variant。语言没有自动抛出并跳过所有中间函数的 exception 通道；`?` 只是简洁地表达一种显式传播规则。

## 3. `Option<T>` 也表示分支

```rust
enum Option<T> {
    Some(T),
    None,
}
```

`None` 通常表示“正常地没有 value”，而不是携带原因的 failure。

```text
找不到 optional cache entry → Option
读取配置文件失败           → Result
```

如果调用者需要知道“为什么没有”，就不应只返回 `None`。

## 4. `Result` 的 T 与 E 都是类型设计

```rust
Result<Session, SessionStartError>
```

不仅成功值有类型，失败也有结构。E 可以保存：

- stable variant；
- path、ID、operation；
- retry metadata；
- underlying source；
- safe user hint。

错误 type 是 API contract 的一部分。

## 5. `match` 是最完整的处理方式

```rust
match load_config() {
    Ok(config) => run(config),
    Err(err) => report(err),
}
```

当不同 variants 需要不同恢复动作时，显式 match 最清楚。组合器和 `?` 适合结构简单、动作一致的传播。

## 6. `?` 的概念展开

```rust
let config = load_config()?;
```

可先近似理解为：

```rust
let config = match load_config() {
    Ok(value) => value,
    Err(err) => return Err(err.into()),
};
```

真正语言机制更一般，但这个模型足以解释绝大多数应用代码。

## 7. `?` 做了两件事

```text
Ok(value)  → 解包 value，继续当前函数
Err(error) → 转成当前函数的 error type，提前 return
```

第二步的“转换”经常被忽略，它通常依赖 `From`/`Into`。

## 8. `?` 不会自动记录日志

传播 error 与观察 error 是两回事：

```rust
do_work()?;
```

不会自动 log、retry、上报 telemetry 或显示给用户。调用栈中的某个 owner boundary 必须决定这些动作。

## 9. `?` 也不自动添加业务上下文

底层可能只返回：

```text
permission denied
```

但上层真正需要知道：

```text
failed to read rules file /repo/.codex/rules: permission denied
```

上下文要通过 typed variant fields、`map_err` 或 `Context` 明确添加。

## 10. `?` 作用于 `Option`

在返回 `Option<T>` 的函数中：

```rust
let value = map.get(key)?;
```

概念上：

```text
Some(value) → 继续
None        → 提前 return None
```

它没有 error object，也没有自动告诉你哪个 key 不存在。

## 11. 不要混淆 `None` 与 `Err`

```text
Optional value 没配置      → None
配置文本无法解析           → Err(ParseError)
配置要求唯一对象但有两个   → Err(Ambiguous)
```

若把后三者都压成 None，调用者无法区分正常缺省与系统故障。

## 12. `map` 转换成功值

```rust
parse().map(|number| number * 2)
```

| 输入 | 输出 |
|---|---|
| `Ok(x)` | 对 x 调 closure，得到 `Ok(y)` |
| `Err(e)` | 原样保留 `Err(e)` |

适合纯 success transformation。

## 13. `map_err` 转换错误值

```rust
read(path).map_err(|source| Error::Read {
    path: path.to_path_buf(),
    source,
})
```

它保留成功值，只在 Err 分支改变分类或添加 context。

## 14. `and_then` 串联会失败的下一步

```rust
load().and_then(validate)
```

`validate` 自己返回 Result，因此不会产生 `Result<Result<T,E>,E>`。

它适合一条短 pipeline；步骤多或需要 await 时，普通 `?` 常更易读。

## 15. `or_else` 在错误分支恢复

```rust
load_primary().or_else(|_| load_fallback())
```

但不要无条件丢弃第一条 error。Fallback 也失败时，诊断可能需要同时保留 primary 与 fallback context。

## 16. `unwrap_or` 与 `unwrap_or_else`

前者会先计算默认值，后者只在需要时调用 closure：

```rust
value.unwrap_or(expensive_default())
value.unwrap_or_else(expensive_default)
```

Lazy 版本还可使用 error/absence 信息。

## 17. `ok_or` 把 Option 变 Result

```rust
optional.ok_or(Error::Unavailable)
```

若构造 error 有分配或格式化成本，用：

```rust
optional.ok_or_else(|| Error::Missing { id: id.clone() })
```

只有 None 时才构造。

## 18. Codex 中 `Context` 也能作用于 Option

固定源码：

```rust
let outgoing = outgoing
    .upgrade()
    .context("app-server current-time provider is unavailable")?;
```

`Weak::upgrade()` 返回 `Option<Arc<_>>`。`anyhow::Context` 将 None 转为带说明的 `anyhow::Error`。

## 19. `transpose` 翻转 Option 与 Result

```rust
Option<Result<T, E>>
    .transpose()
// Result<Option<T>, E>
```

常用于 optional field：没提供是 `Ok(None)`，提供但解析失败是 `Err(e)`，提供且成功是 `Ok(Some(t))`。

## 20. `collect` 可以聚合一串 Results

```rust
let values: Result<Vec<_>, _> = inputs
    .into_iter()
    .map(parse)
    .collect();
```

通常遇到第一条 Err 就停止。若需要收集所有诊断，要显式设计 aggregate error，而不是使用默认 collect。

## 21. `From<E1> for E2` 让 `?` 转换错误

```rust
impl From<std::io::Error> for AppError {
    fn from(source: std::io::Error) -> Self {
        Self::Io(source)
    }
}
```

于是返回 `Result<_, AppError>` 的函数可对 `io::Result<_>` 使用 `?`。

## 22. 自动转换要保持语义

不是每个底层 error 都应无条件变成同一外层 variant。若同一个 `io::Error` 在不同 operation 中需要不同 path/context，显式 `map_err` 更准确。

```text
ReadConfig { path, source }
WriteRollout { path, source }
```

比统一 `Io(source)` 更可行动。

## 23. `#[from]` 的便利与约束

`thiserror` 可生成 From：

```rust
#[error("failed to update in-memory rules: {source}")]
AddRule {
    #[from]
    source: ExecPolicyRuleError,
}
```

适合一种 source 明确对应一种外层语义的情况。若需要额外 runtime fields，通常仍要手动构造。

## 24. `map_err` 不只是改文字

它可能执行三种不同动作：

```text
加 operation context
改变 typed classification
跨 API/protocol boundary 降低信息精度
```

Review 时要分清是哪一种；第三种尤其要确认丢失是否有意。

## 25. Error type 的两个大方向

| 类型 | 强项 | 典型位置 |
|---|---|---|
| Typed enum/struct | 可 match、稳定分类、字段明确 | Library/domain/protocol boundary |
| `anyhow::Error` | 可接多种 source、快速加 context | Application orchestration/internal plumbing |

它们不是互斥。常见结构是内部 typed errors，上层组合为 anyhow，外部再映射到稳定协议 error。

## 26. `thiserror` 是 derive 工具

```rust
#[derive(Debug, thiserror::Error)]
enum LoadError {
    #[error("failed to read {path}: {source}")]
    Read {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
}
```

它生成 `Display` 和 `std::error::Error` 实现；不会自动决定 retry、HTTP status 或用户展示策略。

## 27. `#[error(...)]` 定义 Display

```rust
#[error("failed to parse rules file {path}: {source}")]
```

这是 human-readable summary。Variant fields 仍保留 machine-readable data，调用者可 match `ParsePolicy { path, source }`。

## 28. `#[source]` 建立原因链

```rust
Io {
    context: &'static str,
    #[source]
    source: std::io::Error,
}
```

外层 error 说明“哪个 operation”，底层 `io::Error` 保留 OS kind/code。标准 `Error::source()` 可沿链访问。

## 29. Source chain 不是字符串拼接

字符串：

```text
failed to read config: permission denied
```

错误链：

```text
ConfigManagerError::Io
  context = "failed to read config"
  source = io::Error
    kind = PermissionDenied
    raw_os_error = ...
```

后者仍能 typed match/downcast。

## 30. `#[error(transparent)]`

```rust
#[error(transparent)]
Processing(#[from] ImageProcessingError)
```

Wrapper 的 Display/source 行为透明地委托给内部 error，适合只是扩大 enum union、不想添加新文本层的 variant。

## 31. Transparent 不等于分类消失

外层仍有 `ImagePreparationError::Processing(...)` variant，代码仍可 match；只是展示和 source forwarding 不额外包一层重复文案。

## 32. `ConfigManagerError` 展示 typed wrapper

固定源码把来源分为：

```text
Write { code, message }
Io { context, source }
Json { context, source }
Toml { context, source }
Anyhow { context, source }
```

它既保留 domain write code，也保留不同底层 source 类型。

## 33. 为什么 context 是 `&'static str`

这些 context 是代码中稳定 operation label，例如：

```text
failed to read configuration layers
failed to serialize configuration
```

使用 static string 避免每次分配，也限制 context 不随任意输入无限增长。动态 path/value 则应作为独立安全字段或审慎格式化。

## 34. Helper constructor 统一 variant 构造

```rust
fn io(context: &'static str, source: io::Error) -> Self {
    Self::Io { context, source }
}
```

调用点简洁，又不会把 source 先 `.to_string()`。Helper 同时固定参数次序和 ownership。

## 35. Typed error 便于业务映射

`write_error_code()` 只对 `Write` variant 返回协议 code：

```rust
match self {
    Self::Write { code, .. } => Some(code.clone()),
    _ => None,
}
```

若所有错误早已变成 String，就只能脆弱地搜索文本。

## 36. `anyhow::Error` 是 type-erased error container

它能包住满足条件的具体 error，并支持：

- 添加 context；
- 遍历 cause chain；
- downcast 到具体类型；
- 可选 backtrace；
- 统一 `anyhow::Result<T>`。

适合不想让 orchestrator enum 枚举所有底层库错误的层。

## 37. `anyhow!` 创建应用错误

```rust
return Err(anyhow!("current-time response is outside the supported range"));
```

适合没有独立 typed recovery 的应用失败。若调用者需要按“超时/取消/范围”采取不同动作，应考虑 typed variant，而不是只靠 message。

## 38. `bail!` 是提前返回 Err

```rust
bail!("current-time request timed out after {}s", seconds);
```

概念上等于：

```rust
return Err(anyhow!(...));
```

它不 panic，也不自动 log。

## 39. `ensure!` 表达前置条件

概念示例：

```rust
ensure!(limit > 0, "limit must be positive");
```

条件为 false 时返回 error，适合应用层 validation。公共 library 若需要 caller 区分规则，typed validation error 更合适。

## 40. `.context(...)` 添加固定上下文

```rust
serde_json::from_value(result)
    .context("invalid current-time response")?
```

外层说明 operation，底层 `serde_json::Error` 仍在 chain 中。

## 41. `.with_context(...)` 延迟构造上下文

```rust
fs::read(&path)
    .with_context(|| format!("failed to read {}", path.display()))?;
```

Closure 只在 Err 时执行，适合涉及 format/clone 的动态 context。

## 42. Context 应回答“做什么时失败”

好的层次：

```text
failed to initialize session
→ failed to open rollout
→ permission denied
```

差的层次：

```text
error
→ operation failed
→ failure occurred
```

每层应增加新信息，避免重复底层文字。

## 43. Context 不应泄露 secret

不要把完整 auth header、token、request body、用户隐私或巨大 output 放进 error context。因为 error 可能进入：

- log；
- telemetry；
- UI；
- protocol response；
- crash report。

Context 要有信息，也要有数据治理边界。

## 44. `chain()` 遍历所有原因

固定源码 `map_session_init_error`：

```rust
err.chain()
    .find_map(|cause| cause.downcast_ref::<ThreadStoreError>())
```

它不是只看最外层 anyhow message，而是在 cause chain 中寻找 typed domain error。

## 45. Downcast 恢复机器语义

找到 `ThreadStoreError` 后，代码可以按：

```text
Unsupported → UnsupportedOperation
Conflict    → InvalidRequest
其他        → 继续寻找更合适映射
```

这依赖 source type 仍保存在 chain 中。

## 46. 为什么还要查整个 chain 中的 `io::Error`

底层 I/O 可能被多层 context 包装。固定实现遍历所有 causes，找 `io::ErrorKind`，再根据 session storage path 给出 permission、missing、corruption 等可行动建议。

若中间层只保存 `err.to_string()`，这种 typed recovery 会失效。

## 47. `root_cause()` 与 chain

`root_cause()` 只取最底层 cause；`chain()` 保留全部层次。

最底层知道 OS failure，外层知道 operation。诊断通常需要两者，不应假设 root cause 自己足够。

## 48. `Display` 面向简洁展示

```rust
format!("{error}")
```

通常输出当前 error 的 Display。对 anyhow，它常突出最外层 context，不一定展开全部 chain。

## 49. Alternate Display：`{error:#}`

Anyhow 的 alternate Display 常用于把 context chain 连接成更完整的一行/多段信息。固定 MCP response mapping 使用：

```rust
internal_error(format!("{error:#}"))
```

这是跨 protocol boundary 的显式文本化；到这里 typed chain 不再直接传给客户端。

## 50. `Debug` 不是公共 API

```rust
format!("{error:?}")
```

可能包含 type、chain、backtrace 或库版本相关格式。适合开发诊断，不应成为稳定 wire contract 或 snapshot 文案，除非明确接受这种不稳定性。

## 51. `std::error::Error` 的核心角色

Trait 大致提供：

- 可展示的 Display/Debug 基础；
- `source()` 原因链接；
- 与 type erasure/downcast 生态配合。

它不规定 error code、severity、retryability 或公开安全性。

## 52. `Box<dyn Error>`

这是标准库风格的 type erasure：

```rust
Result<(), Box<dyn Error + Send + Sync>>
```

适合简单 binary/main 边界。与 anyhow 相比，它缺少一些方便的 context/chain 格式化 API，但概念相近。

## 53. 为什么常加 `Send + Sync`

Error 可能跨 Tokio task/thread 返回或进入 shared container。`Box<dyn Error + Send + Sync>`/`anyhow::Error` 的线程安全条件让上层可在并发 runtime 中传递。

这不代表具体错误的业务恢复动作可并发执行。

## 54. Error 必须是 `'static` 才容易 downcast

Downcast 需要具体 type identity，并通常要求 error 不借用短期 stack data。长期/跨层 error types 因而偏向 owned String/PathBuf/Arc，而不是保存 `&str`/`&Path`。

这与第 65 章的 task ownership boundary 一致。

## 55. 何时用 typed error

优先 typed error，当 caller 需要：

- match 不同 failure；
- retry/不 retry；
- 映射稳定 protocol code；
- 读取 path、ID、output metadata；
- 做测试断言；
- 保证 exhaustive handling。

## 56. 何时用 anyhow

适合：

- binary/application orchestrator；
- 一条操作链可能来自许多 libraries；
- 当前边界只需停止并报告；
- 需要快速增加 context；
- 最终仍在明确边界映射/记录。

Library public API 若全返回 anyhow，会让 caller 难以稳定分类。

## 57. Typed → Anyhow → Typed boundary

Codex session init 展示一种现实路径：

```text
ThreadStoreError / io::Error
→ anyhow context chain（内部 orchestration）
→ inspect/downcast chain
→ CodexErr stable product classification
```

Type erasure 并不必然丢类型，前提是使用 wrapping/source，而不是 stringify。

## 58. Stringify 是信息终点

```rust
map_err(|err| format!("failed: {err}"))
```

从此通常只剩 String：

- 不能 downcast；
- 不能读 `io::ErrorKind`；
- 不能稳定 match variant；
- 只能解析文本，极脆弱。

只应在明确的文本 boundary 或目标 API 本来只接受 String 时做。

## 59. Protocol error 是另一个类型系统

JSON-RPC error 常只有：

```text
code
message
optional data
```

内部 Rust chain 不应原样 wire 序列化。Boundary mapper 应决定：

- stable code；
- safe message；
- 是否公开 structured data；
- 内部日志如何关联同一次 failure。

## 60. Internal error 不等于把 Debug 全发给用户

内部诊断可能含 path、server name、payload excerpt 或 source chain。公开前要做：

- secret redaction；
- size bound；
- terminal/control character handling；
- stable wording；
- actionable but not overly revealing detail。

## 61. Error code 与 message 分工

```text
code/variant → 程序稳定判断
message      → 人类理解当前实例
fields/data  → 结构化上下文
source chain → 工程诊断根因
```

不要让客户端通过英文 message substring 推断 category。

## 62. Panic 与 `Result::Err` 不同

`Err(E)` 是函数签名声明的可恢复路径。Panic 表示当前执行无法按正常 contract 继续，通常源于：

- violated internal invariant；
- bug；
- `unwrap`/index 等检查失败；
- 显式 `panic!`。

Panic 不应代替普通输入验证。

## 63. `panic!` 会怎样传播

在 unwind 配置下，stack frames 依次 unwind 并 drop locals，直到线程/task boundary 或 catch point。某些 build 可配置 abort，直接终止进程。

因此不能把“panic 一定能 catch”当跨构建平台的业务恢复协议。

## 64. `unwrap()` 的含义

```rust
result.unwrap()
option.unwrap()
```

它断言当前一定是 Ok/Some，否则 panic。适合：

- 测试中直接暴露失败；
- 已由局部代码显然证明的不变量；
- 初始化时真正不可继续且项目策略允许 panic。

不适合不可信输入和普通 I/O。

## 65. `expect()` 应写不变量，不要复述动作

较好：

```rust
value.expect("parser guarantees one root node")
```

较差：

```rust
value.expect("failed")
```

Message 应帮助定位哪条假设被破坏。

## 66. Test 中的 panic 很常见

```rust
match event {
    Expected(x) => x,
    other => panic!("unexpected event: {other:?}"),
}
```

测试失败本来就应立即停止并显示 unexpected value。不要把这种 test-only pattern 机械搬到生产 request handler。

## 67. `unreachable!()` 也是不变量断言

它表示控制流按类型/前置逻辑不可能到达。若输入或版本变化可能触发，就应返回 typed error 或增加 exhaustive variant，而不是用 `unreachable!` 掩盖。

## 68. `debug_assert!` 只在特定 build 启用

适合昂贵或仅开发期的不变量检查；不能依赖它保护 production safety/security。外部输入校验必须在 release build 同样执行。

## 69. `catch_unwind` 不是普通 error conversion

它用于隔离某些 unwind panic，但存在 `UnwindSafe` 等约束，也无法可靠恢复所有被破坏的业务 state。

常见边界是 plugin/test/FFI/task supervisor；恢复策略必须保守。

## 70. Panic payload 不一定是 String

Rust 可 panic 任意 `Any + Send` payload。把 panic 转诊断时不能假设总能直接拿到 `&str`；runtime `JoinError` 提供更合适的状态接口。

## 71. Async task 有两层失败

```rust
let handle: JoinHandle<Result<T, E>> = tokio::spawn(async { work().await });
let result = handle.await;
```

得到：

```text
Result<Result<T, E>, JoinError>
└ task 运行层 └ 业务层
```

外层 Err 表示 task 未正常产出 inner value；内层 Err 是 work 主动返回的业务失败。

## 72. 双 `?` 的概念

```rust
let value = handle.await??;
```

第一（从左到右概念）层先传播 JoinError，第二层再传播业务 E，前提是当前 error type 能从两者转换。

在重要边界显式 `map_err` 往往能给两层不同 context。

## 73. 固定源码中的两层 `map_err`

```rust
spawn_blocking(move || write_atomically(&write_path, ""))
    .await
    .map_err(|err| Error::anyhow("config persistence task panicked", err.into()))?
    .map_err(|err| Error::io("failed to create empty user config.toml", err))
```

外层 JoinError 与内层 I/O error 被分成两个 operation context。

## 74. JoinError 可能是 cancelled

Task 被 abort/cancel 时，join 结果可为 cancelled。固定 connection cleanup 代码：

```rust
if let Err(err) = result
    && !err.is_cancelled()
{
    warn!(...);
}
```

Owner 已主动 abort cleanup tasks 时，cancelled 是预期终态，不应都记录成故障噪声。

## 75. JoinError 也可能代表 panic

若 task panic，JoinError 不是业务 E。Supervisor 应决定：

- 记录 panic；
- 终止 connection/session/process；
- 是否允许其他 tasks 继续；
- 怎样避免把内部 panic payload直接公开。

## 76. “Task panicked” context 需要准确

JoinError 也可能来自 cancellation，所以如果代码不先区分 `is_cancelled()`，一概写 “panicked” 可能不精确。

固定 config 写路径使用该文案是特定局部语境；设计新 API 时应按实际 JoinError variants 区分。

## 77. Dropping JoinHandle 不会自动取消 task

普通 Tokio JoinHandle 被 drop 后 task 通常继续 detached 运行。若 owner 想要 abort-on-drop，需要显式 wrapper/policy。

所以“不处理 join error”甚至可能意味着根本不会看到它。

## 78. Cancellation 不是 Error 的同义词

Cancellation 可能是：

- 用户主动中断；
- shutdown；
- newer task replacement；
- deadline；
- parent failure propagation。

是否映射为 Err、特殊 variant 或正常 terminal outcome，由 API contract 决定。

## 79. Timeout 常产生嵌套 Result

```rust
timeout(duration, operation()).await
```

概念类型：

```text
Result<OperationOutput, Elapsed>
```

若 operation output 自己是 `Result<T,E>`，又形成两层。要分清 timeout 与 operation failure。

## 80. Current-time request 的三层结果

固定源码匹配：

```text
timeout_at deadline
  → oneshot receiver result
    → JSON-RPC response result
```

分别对应：超时、channel 取消、远端 error，不能用一个 `?` 后统一叫“请求失败”。

## 81. Match 嵌套 Result 的价值

```rust
match timeout_at(deadline, rx).await {
    Ok(Ok(Ok(result))) => result,
    Ok(Ok(Err(remote))) => ...,
    Ok(Err(canceled)) => ...,
    Err(elapsed) => ...,
}
```

看似啰嗦，却保留了失败层级与可行动语义。

## 82. Error boundary 应只转换一次语义

推荐：

```text
底层 library：具体 cause
domain layer：业务 category + source
application layer：operation context
protocol/UI：safe stable projection
```

若每层都随意 stringify/reclassify，最终既无法恢复也无法定位。

## 83. 何时加 context

在调用点知道而底层不知道的信息：

- operation；
- target path/ID；
- stage；
- dependency name；
- user action（安全时）。

底层已经表达的 “permission denied” 不必重复三遍。

## 84. 何时创建新 typed variant

当 caller 需要不同处理：

- InvalidInput；
- NotFound；
- Conflict；
- Unsupported；
- PermissionDenied；
- Transient transport；
- Internal invariant。

不要因为文案不同就增加 variant；要因为控制行为不同而增加。

## 85. 何时保留原 error 不加层

如果当前函数只是透明 helper，没有新增 operation context，也不改变 recovery semantics，直接 `?` 可能最清楚。

无意义包装会拉长 chain、污染日志和测试。

## 86. Error aggregation

并发或批量操作可能多个对象失败。设计选择：

- first error fail-fast；
- best-effort 并返回 partial success；
- 收集 `Vec<ItemError>`；
- primary error + suppressed cleanup errors。

必须定义 ordering、上限和用户展示，避免 error list 无界。

## 87. Cleanup error 不应覆盖 primary error

```text
主操作失败 A
清理又失败 B
```

若只返回 B，真正根因 A 丢失。常见做法是保留 A 为 primary，并把 B log、attach 或组合成 aggregate；具体取决于 durability/safety 风险。

## 88. Error logging 的 owner

同一个 error 每层都 log，会产生重复：

```text
library logs
domain logs
handler logs
top-level logs
```

通常底层返回结构化 error，在真正消费/终止边界记录一次；底层只对无法返回的 background failure 主动 log。

## 89. Logging 后再返回可能是对的

若当前层有只有它知道的 transient telemetry，又仍需 caller 决定行为，可以 log event 并返回。但日志应有 stable category/request ID，避免与最终 error log 无法去重。

## 90. Error 与 telemetry label 分开

高基数 message 不适合作 metric label：

```text
error_type = "permission_denied"  // bounded
message = "failed to read /user/..." // log only
```

Typed variant 是生成稳定 label 的可靠来源。

## 91. Backtrace

Backtrace 记录 error/panic 经过的调用位置，是否捕获和展示取决于配置与库。它辅助定位代码路径，但不替代：

- source chain；
- operation context；
- request/thread IDs；
- user input分类；
- async task/span tracing。

## 92. Async backtrace 的局限

`.await` 把逻辑调用拆成 polls，普通 stack backtrace 未必像同步调用栈一样直观。Tracing spans、task names 与 stable IDs 常需一起使用。

## 93. `io::ErrorKind` 是稳定分类入口

固定 session init mapping 检查：

```text
PermissionDenied
NotFound
AlreadyExists
InvalidData / InvalidInput
IsADirectory / NotADirectory
```

再生成针对 session path 的提示。不要用操作系统英文 message substring 判断这些类型。

## 94. Raw OS error 仍可能有价值

`ErrorKind` 跨平台较稳定，但可能过于粗。内部诊断可保留 raw OS code/source；外部行为仍优先按 portable kind 决定。

第 43 章的跨平台原则在 error type 上同样适用。

## 95. Parse error 应保留位置

JSON/TOML/parser errors 常带 line、column、span 或 path。包装时不要只留下“invalid config”；保留 source 才能生成第 64 章所讲的精确诊断。

## 96. Validation error 与 parse error 分开

```text
Syntax invalid       → parse error
Type mismatch        → deserialize error
Cross-field invalid  → validation error
Policy disallowed    → policy error
```

它们对用户修复方式不同，typed category 也应不同。

## 97. Error message 应有动作与对象

推荐结构：

```text
failed to <operation> <target>: <cause>
```

例如：

```text
failed to parse rules file /repo/rules: unexpected token at line 4
```

同时确保 path 是否适合公开。

## 98. 不要把否定双重包装

差：

```text
operation failed: failed to perform operation: unable to complete operation
```

好：

```text
failed to initialize session: cannot open rollout /path: permission denied
```

每层只新增自己知道的名词。

## 99. User-facing hint 与 source message 分开

底层：

```text
EACCES
```

用户提示：

```text
Codex cannot access session files ... fix ownership ...
```

提示是产品映射，不应修改/丢弃底层 typed `io::Error` 才能实现。

## 100. Error 中保存大 payload 的风险

某些 sandbox denial 需要保留 output 才能给模型解释，但必须有：

- truncation；
- original length metadata；
- token/byte cap；
- redaction policy；
- clone 成本意识。

Error object 也属于内存容量模型。

## 101. Error 是否可 Clone

很多底层 errors 不实现 Clone，因为含 OS/resource/context。不要为了 broadcast 轻率把所有 source 转 String。

可选方案：

- `Arc<Error>`；
- 投影成较小 cloneable status；
- 单 owner 保存详细 error，事件只传 ID/category；
- 重新构造 safe protocol error。

## 102. `Arc<ExecServerError>` 的语义

将 error 放 Arc 可在多处共享同一个失败对象，不需 clone 内部 source chain。但 Arc strong ownership 也会延长其 payload 存活，尤其 error 含大 buffers 时要有界。

## 103. Error type 可否序列化

Rust `std::error::Error` 不自动 Serialize。Protocol error 应定义独立 wire struct/enum，而不是直接把内部 anyhow/thiserror object序列化。

这允许稳定兼容、脱敏与字段上限。

## 104. Exhaustive match 的价值

对内部 closed enum：

```rust
match err {
    NotFound => ...,
    Conflict => ...,
    Permission => ...,
}
```

新增 variant 时 compiler 提醒所有 mapping。Wildcard `_` 虽方便，却可能把新安全/重试语义静默归入错误分支。

## 105. Public non-exhaustive error

Library 若需未来新增 variants，可使用 non-exhaustive 策略，caller 必须保留 fallback。代价是无法完全 exhaustive match。

内部 workspace crate 是否需要它，应按发布兼容边界判断，不应自动添加。

## 106. Error type 大小

Enum size 通常由最大 variant 决定。若某个 variant 含巨大 payload，所有 Result 的 Err representation 可能变大。

可考虑 Box/Arc 大 source，但要基于 profile 和 API 清晰度，不要过早优化。

## 107. Result 的 happy path size

`Result<T,E>` 需要容纳 T 或 E 加 tag；compiler 还能利用 niche optimization。不要凭肉眼假设精确字节数，应在关键场景用 `size_of`/profile 验证。

## 108. Error conversion 的性能通常不是首要问题

Error path 相对少见时，优先保证分类和上下文。高频 parse rejection、validation loop 或 overload path 则可能成为热路径，应避免大 allocation、backtrace 和无界 formatting。

## 109. Retry 要读取结构，不读文本

```rust
match err {
    Transport(Timeout) => retry,
    InvalidInput => stop,
    _ => policy,
}
```

而不是：

```rust
if err.to_string().contains("timeout") { ... }
```

后者会受语言、库文案和嵌套 context 变化影响。

## 110. Error 与 idempotency

Timeout 只说明“没在期限内观察到结果”，不证明副作用没发生。Typed error 可保存 stage/operation ID，帮助上层决定查询、对账或幂等重试。

第 47、59 章负责这层运行策略；Rust Result 自身不会判断。

## 111. Test：断言 variant 而非整段文字

```rust
assert!(matches!(err, LoadError::NotFound { .. }));
```

或解构后比较整个结构化 object。只有 public UX 文案/快照本身是 contract 时才精确断言 Display string。

## 112. Test：断言 source chain

有包装逻辑时验证：

- 最外层 context 正确；
- source 存在；
- chain 中能 downcast 到预期 typed error；
- `io::ErrorKind` 未丢；
- secret 未进入 public message。

## 113. Test：Join 两层失败

分别覆盖：

```text
task 正常返回 Ok
task 正常返回 Err(E)
task panic → JoinError
task abort → cancelled JoinError
```

不要只测 inner Result 而漏掉 task runtime layer。

## 114. Test：Context 不应改变 recovery

加 `anyhow::Context` 后，下游 `chain().downcast_ref::<TypedError>()` 应仍能找到 source。若改成 String 则测试应暴露语义丢失。

## 115. Test：Protocol projection

内部具体 error 映射到 wire 时，应断言：

- stable code；
- safe bounded message；
- required data；
- unknown/internal fallback；
- source/secret 没有意外暴露。

## 116. Fault injection

在每个 boundary 注入：

- I/O permission denied；
- parse failure；
- channel close；
- timeout；
- task panic；
- cancellation；
- cleanup failure。

验证最终 category、chain、log 次数和用户 action，而不只验证“返回 Err”。

## 117. 排错：`?` 无法转换 error

Compiler 常说：

```text
the trait From<SourceError> is not implemented for TargetError
```

依次问：

1. 当前函数返回的 E 是什么？
2. 被 `?` 的表达式 E 是什么？
3. 两者应自动一一转换吗？
4. 需要 `#[from]`、手写 From，还是包含 path/context 的 `map_err`？

## 118. 排错：得到嵌套 Result

先写出完整类型：

```text
Result<Result<T, BusinessError>, JoinError>
Result<Result<T, RemoteError>, Elapsed>
Option<Result<T, ParseError>>
```

再逐层决定传播、分类或 transpose；不要盲目连续 `.unwrap()`。

## 119. 排错：错误链断了

搜索中间层：

```rust
err.to_string()
format!("{err}")
anyhow!("{err}") // 只插入文字时也可能失去 source
```

改为 typed `source` field、`Error::new(err).context(...)` 或 `map_err` 构造保留 source 的 variant。

## 120. 排错：日志只有底层错误

若只见：

```text
channel closed
```

检查每个调用点是否添加 operation/target context，以及日志 formatter 是否只打印 root cause 而忽略 chain。

## 121. 排错：用户看到内部堆栈

检查 protocol/UI mapper 是否使用 `Debug`、`{:#?}` 或完整 anyhow chain。把内部 diagnostics 留在受控 log，以 stable safe projection 返回用户。

## 122. 排错：取消被记录成故障

检查 JoinError、CancellationToken、oneshot close 和 timeout 是否有独立分类。固定 cleanup code 明确忽略 `is_cancelled()`，就是为了不把主动 abort 当 task failure。

## 123. 排错：错误被重试但不该重试

检查 retry decision 是否依据：

- typed category；
- operation stage；
- side-effect/idempotency；
- retry budget；
- caller cancellation。

若依据 message substring，优先修复 error contract。

## 124. 代码评审清单：Result 控制流

- `None` 是正常缺省还是丢失的错误？
- `?` 的 source E 与 target E 怎样转换？
- `map`/`map_err`/`and_then` 是否作用在正确分支？
- Nested Result 每层分别是什么？
- Fallback 是否保留 primary failure？
- Batch 是否需要 all-errors 而非 first-error？

## 125. 代码评审清单：Error type

- Caller 是否需要 match variants？
- Variant 按恢复行为还是按文案划分？
- Path/ID/stage 是否是 fields？
- Source chain 是否保留？
- `#[from]` 是否一一对应且不丢 context？
- `transparent` 是否避免重复层？
- Enum 新 variant 是否被 exhaustive mappings 覆盖？

## 126. 代码评审清单：Anyhow

- 当前层真的是 application/orchestration boundary 吗？
- Context 是否增加新信息？
- 动态 context 是否用 `with_context`？
- Chain 能否 downcast 原 typed error？
- 是否过早 stringify？
- 最终在哪里映射为 stable external error？

## 127. 代码评审清单：Panic/Task

- Panic 是真实 invariant，还是普通输入失败？
- `unwrap/expect` 的前提能否由局部代码证明？
- JoinHandle 是否被观察？
- 业务 Err、panic、cancel 是否分开？
- Detached task failure 谁记录？
- Cleanup failure 会不会覆盖 primary error？
- Shutdown cancellation 是否产生噪声告警？

## 128. 代码评审清单：公开诊断

- Code 是否稳定？
- Message 是否安全、有界、可行动？
- 是否泄露 secret/path/payload？
- Telemetry label 是否低基数？
- Internal chain 是否有 request/thread ID 可关联？
- 客户端是否错误地解析 message 文本？
- Debug/backtrace 是否只在受控位置出现？

## 129. 常见误解一：`?` 等于 catch/throw

它是当前函数显式返回类型约束下的 early return，并可能通过 From 转换 error。它不自动穿越 task、thread、channel 或 callback boundary。

## 130. 常见误解二：Anyhow 会丢失所有具体类型

正确 wrapping 时，具体 source 仍可通过 chain/downcast 找到。真正经常丢类型的是 `.to_string()` 后重新创建纯文本 error。

## 131. 常见误解三：`thiserror` 会决定用户文案

它生成 Display/Error。是否直接展示、映射哪个 code、是否脱敏和截断，仍由产品 boundary 决定。

## 132. 常见误解四：Panic 就像 Err，可随便恢复

Panic 通常说明 invariant/bug，可能发生在状态更新中途。即使能 catch/join，也要保守处理受影响 state，不能假设重跑安全。

## 133. 常见误解五：Task 返回 Err 会成为 JoinError

不会。Task 正常完成并返回 `Err(E)` 时，join 外层是 `Ok(Err(E))`。只有 panic/cancel 等任务运行层异常才产生 `Err(JoinError)`。

## 134. 常见误解六：错误越详细越好

内部要足够详细，外部要安全、有界、稳定。把 secret、巨大 payload 或不稳定 Debug 全部公开，既不安全也不可维护。

## 135. 本章最重要的心智模型

```text
底层 operation
  → Result<T, ConcreteSourceError>
  → domain typed error { category, fields, source }
  → application anyhow context chain（按需）
  → task boundary: Result<..., JoinError>
  → product/protocol mapper
      ├── stable code/category
      ├── safe bounded message
      └── internal log/trace correlation
```

每次转换都问：新增了什么信息，保留了什么类型，丢弃了什么，以及谁还需要机器判断。

## 136. 源码检查点

按以下顺序打开固定提交：

1. `codex-rs/app-server/src/config_manager_service.rs`
   - `ConfigManagerError`
   - `#[error]`、`#[source]`
   - typed Write/Io/Json/Toml/Anyhow variants
   - `write_empty_user_config` 的 nested Result
2. `codex-rs/app-server/src/current_time.rs`
   - Option `.context(...)`
   - `bail!`、`anyhow!`
   - timeout/oneshot/remote response 三层 match
   - deserialize context
3. `codex-rs/app-server/src/connection_cleanup.rs`
   - `JoinSet`
   - `Result<(), JoinError>`
   - cancelled 与 failure 分流
4. `codex-rs/core/src/exec_policy.rs`
   - `ExecPolicyError`
   - path + source fields
   - `ExecPolicyUpdateError::JoinBlockingTask`
   - `#[from]`
5. `codex-rs/core/src/image_preparation.rs`
   - typed user conditions
   - `#[error(transparent)]`
6. `codex-rs/core/src/session_rollout_init_error.rs`
   - anyhow `chain()`
   - typed `downcast_ref`
   - `io::ErrorKind` mapping
   - safe actionable projection
7. `codex-rs/core/src/unified_exec/errors.rs`
   - semantic variants
   - error 中保留 output 与 truncation metadata
8. `codex-rs/app-server/src/request_processors/mcp_processor.rs`
   - anyhow result 跨 task
   - `{error:#}` 文本化 protocol boundary
9. `codex-rs/core/src/config_lock.rs`
   - `map_err` 添加 operation label
   - `?` pipeline
   - validation error 构造
10. `codex-rs/protocol/src/error.rs`
    - product-level `CodexErr`/details 分类与内部 source 的区别

## 137. 动手练习一：手工展开 `?`

选一个同时包含 `map_err` 和 `?` 的函数，把每个表达式展开成 match：

```text
Ok 分支得到什么 type？
Err 分支原 type 是什么？
何处发生 From/Into？
当前函数最终返回什么 E？
```

再还原成最清楚的写法。

## 138. 动手练习二：构造 Typed Error

为一个配置加载器设计：

```text
NotFound
Read { path, source }
Parse { path, source }
Invalid { field, reason }
```

实现 Display/source/From，说明哪些 variant 可以自动 From、哪些必须手动携带 path。

## 139. 动手练习三：验证 Anyhow chain

从底层 `io::Error(PermissionDenied)` 开始，连续添加“open rollout”“initialize session” context。然后分别打印：

```text
{error}
{error:#}
{error:?}
```

遍历 `chain()` 并 downcast `io::Error`，验证 ErrorKind 未丢。

## 140. 动手练习四：Async 双层错误

写 `JoinHandle<Result<u32, WorkError>>`，分别让 task：

1. 返回 Ok；
2. 返回 Err；
3. panic；
4. 被 abort。

打印外层/内层 variant，不要只用 `unwrap`。

## 141. 动手练习五：设计 Protocol Projection

把内部 errors 映射到：

```rust
struct PublicError {
    code: StableCode,
    message: String,
}
```

为每个分支写清：公开什么、内部 log 什么、如何关联、如何截断、哪些 source 不可暴露。

## 142. 理解检查

你应该能不看答案解释：

1. `Result` 为什么是普通 value？
2. `?` 在 Err 分支为什么可能调用 `From`？
3. `Option` 的 `?` 与 Result 有何差异？
4. `map`、`map_err`、`and_then` 各作用在哪一侧？
5. `#[source]` 与把 source 放进 Display 字符串有何差异？
6. `#[from]` 什么时候方便，什么时候会丢 operation context？
7. Typed error 与 anyhow 各适合哪层？
8. `.context()` 为什么不会必然丢掉底层 type？
9. `.to_string()` 为什么常切断 chain？
10. `JoinHandle<Result<T,E>>` await 后为什么有两层 Result？
11. `Ok(Err(E))` 与 `Err(JoinError)` 分别表示什么？
12. Panic 为什么不应表达普通用户输入错误？
13. `{error}`、`{error:#}`、`{error:?}` 的用途为何不同？
14. 外部 protocol 为什么不直接序列化内部 anyhow error？

## 143. 本章小结

- `Result` 和 `Option` 是显式 enum，失败/缺失都是可 match 的普通值。
- `?` 解包成功值，对失败提前返回，并按需要通过 `From` 转换错误。
- `map` 改成功、`map_err` 改失败、`and_then` 串联会失败的步骤、`transpose` 翻转容器。
- Typed error 保存稳定 variant、结构字段和 source，适合 library/domain/recovery boundary。
- `thiserror` 生成 Display/Error/From 等实现，但不决定 retry 或公开策略。
- Anyhow 适合应用编排，可添加 context、遍历 chain 并 downcast具体 source。
- Context wrapping 能保留 source；stringify 通常切断 typed chain。
- Panic 表示 invariant/bug 路径，不是普通可恢复输入错误。
- Async task 同时有业务 `Result<T,E>` 和 task runtime `Result<_,JoinError>`。
- Cancelled JoinError、panic JoinError 与业务 Err 必须分开处理。
- Error 每跨一层都应明确新增 context、保留类型和有意丢失的信息。
- Product/protocol boundary 输出 stable code 与安全有界 message，内部保留详细 chain 与 trace correlation。

## 144. 本章词汇表

| 英文/代码词 | 字面意思 | 在本章中的具体含义 |
|---|---|---|
| `Result<T, E>` | 结果 | `Ok(T)` 或 `Err(E)` 的显式可恢复控制流 |
| `Option<T>` | 可选值 | `Some(T)` 或正常缺失 `None` |
| `?` operator | 问号运算符 | 解包成功，失败时转换并提前返回 |
| Early return | 提前返回 | 未执行剩余函数体便返回 Err/None |
| `From` / `Into` | 从/转入 | 让 source error 转为当前函数 target error 的 trait |
| `map` / `map_err` | 映射成功/错误 | 只变换 Result/Option 的指定 variant payload |
| `and_then` / `or_else` | 然后/否则 | 在 success/error 分支串联另一个可能失败的操作 |
| `ok_or_else` | 缺失转错误 | Lazy 地把 Option::None 转成 typed Err |
| `transpose` | 翻转 | `Option<Result>` 与 `Result<Option>` 的结构转换 |
| `collect::<Result<...>>()` | 聚合结果 | 遍历成功值并在第一条错误处停止的 collection |
| Typed error | 类型化错误 | 可 match variant、读取字段并保留 source 的 enum/struct |
| Type-erased error | 类型擦除错误 | 统一容纳多种具体 error 的 `anyhow::Error`/`dyn Error` |
| `thiserror` | 错误派生库 | 从 attributes 生成标准 Display/Error/From 实现 |
| `#[error]` | 错误展示属性 | 定义 error 的 Display 模板 |
| `#[source]` | 原因属性 | 将字段接入标准 `Error::source()` chain |
| `#[from]` | 自动转换属性 | 同时标记 source 并生成 `From<Source>` |
| `transparent` | 透明 | Wrapper 的展示/source 直接委托内部 error |
| Source / cause chain | 来源/原因链 | 外层 operation context 到底层具体失败的结构链 |
| Context | 上下文 | 当前调用点知道的 operation、target 或 stage 信息 |
| `anyhow::Error` | Anyhow 错误容器 | 应用层 type-erased、可加 context 与 downcast 的错误 |
| `anyhow!` / `bail!` | 创建/立即返回错误 | 构建 anyhow error，以及从当前函数提前 Err return |
| `Context` / `with_context` | 固定/延迟上下文 | 为 Result/Option 添加 cause-preserving operation 说明 |
| Downcast | 向下转换 | 从 erased chain 恢复具体 error type reference |
| `chain()` / `root_cause()` | 错误链/根因 | 遍历所有 causes，以及获取最底层 cause |
| `Display` / `Debug` | 展示/调试格式 | 简洁人读消息与详细工程诊断 representation |
| Alternate Display `{:#}` | 交替展示 | Anyhow 中常展开完整 context chain 的格式 |
| Error code / variant | 错误码/分支 | 供程序稳定分类的 machine-readable identity |
| Error projection | 错误投影 | 把丰富内部错误映射为安全稳定的协议/UI形状 |
| Stringify | 字符串化 | 将 error 降为文本，通常失去 typed source/downcast |
| Panic | 恐慌 | Invariant/bug 导致的非普通 Result 控制流 |
| Unwind / abort | 展开/中止 | Panic 清理 stack frames，或直接终止进程的策略 |
| `unwrap` / `expect` | 强制取值/带说明强制取值 | 非 Ok/Some 时 panic 的不变量断言 |
| `catch_unwind` | 捕获展开 | 在受限边界捕获 unwind panic，不保证业务 state 可恢复 |
| `JoinHandle` / `JoinError` | 任务句柄/等待错误 | 观察 task 正常结果、panic 或 cancellation 的 Tokio 边界 |
| Nested Result | 嵌套结果 | Timeout/channel/task runtime 与业务失败形成的多层 Result |
| Cancelled | 已取消 | Task 被 abort/shutdown 等停止的 JoinError 状态 |
| Detached task | 分离任务 | JoinHandle 不再被 owner 观察但仍可能继续运行的 task |
| Primary / cleanup error | 主错误/清理错误 | 原 operation failure 与收尾过程中产生的次级 failure |
| Error aggregation | 错误聚合 | 有界保存批量/并发操作的多个 failures |
| `io::ErrorKind` | I/O 错误类别 | 跨平台较稳定的 permission/not-found 等分类 |
| Raw OS error | 原始系统错误 | 平台 errno/code 等更具体但较不便携的底层信息 |
| Backtrace | 回溯 | Error/panic 捕获的代码调用路径辅助信息 |
| Fault injection | 故障注入 | 主动制造 I/O、timeout、panic、cancel 等验证错误 contract |

