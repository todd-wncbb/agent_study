# 26：Codex 源码中的常用 Rust 模式

## 这不是普通 Rust 语法课

本章不从变量、循环和函数开始，而是解决阅读 Codex 时最常见的障碍：

```rust
Arc<dyn ToolHandler>
Option<Result<T, E>>
Pin<Box<dyn Future<Output = T> + Send + 'a>>
```

这些类型看起来很长，但可以从外向内拆开。先问“最外层表达什么控制语义”，再看里面装的业务类型。

## 1. 先学会从外向内读类型

例如：

```rust
Option<Arc<TurnContext>>
```

从外向内读：

1. `Option<...>`：这个值可能没有；
2. `Arc<...>`：如果有，多个异步任务可以共同持有；
3. `TurnContext`：共同持有的是一次 Turn 的稳定配置。

再看：

```rust
Result<Box<dyn ToolOutput>, FunctionCallError>
```

1. `Result<..., FunctionCallError>`：执行可能成功或失败；
2. `Box<...>`：成功对象放在堆上，并通过指针持有；
3. `dyn ToolOutput`：具体输出类型可以不同，只要求实现 `ToolOutput` 行为。

长类型不是一整块神秘语法，而是多个小语义层叠加。

## 2. Move、Borrow 与 Clone

Rust 默认将很多值按 move 语义传递：

```rust
let second = first;
// first 对非 Copy 类型通常不再可用
```

借用表示临时访问，不取得所有权：

```rust
fn inspect(turn: &TurnContext) { ... }
```

`Clone` 表示显式产生另一个可使用的值，但“克隆什么”取决于类型：

- `String::clone()` 通常复制字符串数据；
- `Arc::clone()` 只增加共享引用计数，不复制内部大对象；
- 某些 handle 的 clone 可能创建指向同一 channel/runtime 的新句柄。

所以看到 `.clone()` 时，不要自动理解成“深复制”。先检查该类型的语义。

## 3. `Arc<T>`：跨 Task 共享所有权

`Arc` 是 Atomic Reference Counted pointer，普通话可以理解为“线程安全的共享所有权指针”。

Codex 的一个 Turn 可能同时涉及：

- Agent 主循环；
- 模型 stream 消费；
- Tool 执行；
- Event 转发；
- Telemetry；
- 取消和收尾。

这些异步任务都可能需要访问同一个 `Session` 或 `TurnContext`：

```rust
let session_for_task = Arc::clone(&session);

tokio::spawn(async move {
    run_work(session_for_task).await;
});
```

`Arc::clone(&session)` 没有复制整个 Session，只让新 Task 也拥有一份指向相同 Session 的强引用。

最后一个强引用被释放时，内部对象才会 Drop。

## 4. `Arc` 不等于“里面可以随便修改”

`Arc<T>` 解决共享所有权，不自动提供可变访问。若多个 Task 需要修改状态，通常组合：

```rust
Arc<Mutex<State>>
```

或对象内部字段使用锁：

```rust
struct Session {
    state: Mutex<SessionState>,
}
```

这叫 interior mutability：外部可能只有共享引用 `&Session`，内部仍通过同步原语安全修改状态。

## 5. `Mutex` 与 `RwLock`

### `Mutex<T>`

同一时刻只允许一个任务持有写访问：

```rust
let mut state = session.state.lock().await;
state.active_turn = Some(turn);
```

### `RwLock<T>`

允许多个并发读者，写入时独占：

```rust
let models = catalog.read().await;
```

选择依据不是“读多就一定用 RwLock”，还要考虑临界区长度、写竞争和复杂度。

### 阅读时要检查锁作用域

```rust
let value = {
    let state = session.state.lock().await;
    state.value.clone()
}; // guard 在这里释放

slow_network_call(value).await;
```

通常应尽量缩短 guard 生命周期，避免持锁等待慢网络或调用未知外部代码。否则容易阻塞其他 Task，甚至造成死锁。

## 6. `Weak<T>`：观察对象，但不阻止释放

两个对象互相持有 `Arc` 会形成引用环：

```text
Session ──Arc──> Manager
Manager ──Arc──> Session
```

即使外部不再使用它们，强引用计数也不会归零。

`Weak<T>` 不增加强引用生命周期：

```rust
struct Runtime {
    session: Weak<Session>,
}

if let Some(session) = self.session.upgrade() {
    // Session 仍存在，可以临时取得 Arc
}
```

Codex 在 MCP session 回指、Agent 控制等位置使用这种模式。`upgrade()` 返回 `Option<Arc<T>>`，因为目标可能已经释放。

## 7. `Option<T>`：不存在是正常状态

`Option<T>` 有两个变体：

```rust
Some(value)
None
```

它适合表达：

- 本轮没有 output schema；
- 没有 active Turn；
- Provider 没有 server retry delay；
- 恢复数据中缺少旧版本新增字段。

常见读法：

```rust
let Some(turn) = active_turn else {
    return;
};
```

普通话是：“如果没有 active turn，就提前返回；否则下面的 `turn` 已经是确定存在的值。”

## 8. `Result<T, E>` 与 `?`

```rust
enum Result<T, E> {
    Ok(T),
    Err(E),
}
```

`?` 的直觉：成功就取出值继续，失败就按转换规则提前返回错误。

```rust
let prompt = build_prompt(history)?;
let response = model.stream(prompt).await?;
```

近似理解为：

```rust
let prompt = match build_prompt(history) {
    Ok(value) => value,
    Err(error) => return Err(error.into()),
};
```

`?` 不会自动记录日志或决定重试；它只是传播错误。重试、转成 Tool Result 或发 Event 仍由上层语义决定。

## 9. `map`、`and_then`、`map_err`

这些方法让代码在 `Option`/`Result` 内转换值：

```rust
let model_name = config.model.as_ref().map(String::as_str);
```

如果有 model，转换成 `&str`；没有则仍是 `None`。

```rust
let output = handler.execute().await.map_err(ToolError::Runtime)?;
```

成功值不变，失败类型转换后通过 `?` 返回。

读链式调用时，每一步都问：当前容器是 `Option` 还是 `Result`？里面的值类型发生了什么变化？

## 10. `enum` 与 Exhaustive `match`

Codex 大量使用 enum 表达有限状态：

```rust
enum TurnTerminalState {
    Complete,
    Aborted,
    Failed,
}
```

`match` 可以迫使调用者处理每个变体：

```rust
match state {
    Complete => ...,
    Aborted => ...,
    Failed => ...,
}
```

未来新增 `Replaced` 时，编译器会指出哪些位置还没有决定其语义。

这就是仓库偏好 exhaustive match、谨慎使用 `_` wildcard 的原因：协议和安全状态不应被静默吞掉。

## 11. `if let`、`let else` 与 `matches!`

### 只关心一个变体

```rust
if let Some(delay) = error.retry_delay() {
    sleep(delay).await;
}
```

### 不满足就提前离开

```rust
let Some(handler) = registry.get(name) else {
    return Err(UnknownTool);
};
```

### 只得到布尔判断

```rust
if matches!(event, EventMsg::TurnComplete(_)) {
    break;
}
```

三者都是模式匹配，只是表达目的不同。

## 12. Trait：描述行为契约

Trait 类似“必须实现哪些行为”的接口：

```rust
trait ToolOutput {
    fn log_preview(&self) -> String;
    fn to_response_item(&self, call_id: &str) -> ResponseItem;
}
```

不同工具输出可以有不同内部字段，只要都能转换成统一模型结果。

Trait 的价值不是为了名字抽象，而是让调用方依赖稳定职责，而不是依赖每个具体实现。

## 13. 泛型 Trait 与 Trait Object

### 静态泛型

```rust
fn run<T: ToolOutput>(output: T) { ... }
```

编译器为具体 `T` 生成调用路径，类型在编译期确定。

### Trait Object

```rust
Box<dyn ToolOutput>
Arc<dyn ThreadStore>
```

具体实现可以在运行时不同。Registry 需要把 Shell、MCP、Extension 等不同 Handler 放进同一集合，因此经常使用 `dyn Trait`。

看到 `dyn` 时可以读成：“我不知道具体类型，只承诺它实现这组方法。”

## 14. `Send + Sync` 为什么高频出现

异步 Runtime 可能把 Future 或共享对象放到多线程 executor：

- `Send`：值可以安全移到另一个线程；
- `Sync`：多个线程可以安全持有 `&T`；
- `ToolExecutor: Send + Sync`：工具执行器允许被并发 Runtime 安全持有。

这不代表方法内部自动没有逻辑竞态。它只说明类型满足 Rust 的线程安全约束，业务不变量仍要由设计保证。

## 15. Future 到底是什么

调用 `async fn` 不会立刻把函数完整执行完，而是得到一个 Future：

```rust
let future = model.stream(prompt);
let response = future.await?;
```

`.await` 表示当前 Task 在等待期间可以让 executor 运行其他 Task，而不是阻塞整个操作系统线程。

Future 只有被 poll 才推进；丢弃 Future 通常意味着不再继续这项异步计算，但具体外部副作用是否停止仍取决于实现和取消传播。

## 16. 为什么会看到 `Pin<Box<dyn Future<...>>>`

`ToolExecutor` 当前返回：

```rust
type ToolExecutorFuture<'a> =
    Pin<Box<dyn Future<Output = Result<Box<dyn ToolOutput>, FunctionCallError>>
        + Send
        + 'a>>;
```

从外向内拆：

1. `Pin<...>`：Future 被固定位置，满足自引用状态机等要求；
2. `Box<...>`：Future 放到堆上，使返回值大小固定；
3. `dyn Future`：不同 Handler 可返回不同具体 Future；
4. `Output = Result<...>`：完成后得到 ToolOutput 或 FunctionCallError；
5. `Send`：Future 可跨线程调度；
6. `'a`：Future 最长不能活过它借用的数据。

这里使用 boxed Future 便于 object-safe Registry。仓库新增普通 Trait 时则通常偏好原生 RPITIT：

```rust
fn load(&self) -> impl Future<Output = Data> + Send;
```

不要看到一种写法就认为整个仓库所有 Trait 都应该机械改成同一种形式。

## 17. Lifetime `'a` 不是“对象活多少秒”

Lifetime 描述引用之间的有效范围关系：

```rust
fn name<'a>(&'a self) -> &'a str
```

返回的 `&str` 不能比 `self` 活得更久。

在 boxed Future 中，`'a` 常表示 Future 借用了 `&self` 或参数，因此 Future 必须在这些借用失效前完成或被丢弃。

阅读时先问“谁借用了谁”，不要把 lifetime 当 Runtime 定时器。

## 18. `From`、`Into` 与错误转换

```rust
impl From<SandboxErr> for CodexErr { ... }
```

表示 Sandbox error 可以转换成统一 Codex error。

```rust
let error: CodexErr = sandbox_error.into();
```

`?` 传播错误时也会利用 `From` 转换。这样底层保留具体错误，上层又能使用统一错误协议。

但转换不应丢掉关键语义，例如“用户拒绝”和“Sandbox denial”不能都压成一个无类型字符串。

## 19. Builder Pattern

复杂对象参数很多时，Builder 分步收集设置：

```rust
let test = TestCodexBuilder::new()
    .with_model("example")
    .with_config(config)
    .build()
    .await?;
```

优点：调用位置更可读、optional 设置不需要一长串 `None/false`、构造阶段可以统一验证。

Builder 不是 Runtime manager；它通常只负责创建对象。

## 20. `Drop` 与 RAII Guard

RAII 的直觉是：对象存在代表资源已占用；对象离开作用域时自动收尾。

`TurnProfileTimingGuard` 的简化形状：

```rust
impl Drop for TurnProfileTimingGuard {
    fn drop(&mut self) {
        if self.active {
            self.timing.end_phase(self.phase);
        }
    }
}
```

函数即使因 `?` 提前返回，guard 仍会 Drop，计时更不容易漏结束。

其他 Guard 可以用于：

- 归还并发槽；
- 恢复 refresh 标志；
- 中止遗留 Task；
- 释放临时目录。

阅读 Guard 时要找 `Drop`，否则容易只看到“创建了一个没被使用的变量”。前缀 `_guard` 往往就是为了让它活到当前作用域结束。

## 21. `pub`、`pub(crate)` 与私有模块

- `pub`：可作为 crate 对外 API；
- `pub(crate)`：只在当前 crate 内可见；
- 无 `pub`：通常只在当前模块及其规则允许范围可见；
- 私有 `mod` + 显式 `pub use`：控制真正暴露的 API 表面。

Codex 倾向缩小 crate API。一个类型能被当前模块使用，不代表应该公开给所有 crate。

## 22. Derive 与 Attribute

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TurnStartParams { ... }
```

- `derive` 让编译器/宏生成常见 Trait 实现；
- `serde` attribute 控制 wire serialization；
- `cfg(test)` 只在测试构建启用；
- `tracing::instrument` 为函数创建 Trace Span。

Attribute 可能改变对外协议或构建行为，不应把它当注释忽略。

## 23. 阅读复杂函数的实用顺序

遇到 200 行异步函数时，不要从第一行逐字符读到最后：

1. 先看参数与返回类型；
2. 找长期对象：`Arc<Session>`、`Arc<TurnContext>`；
3. 找状态锁及其作用域；
4. 找 `.await` 和 `tokio::spawn` 边界；
5. 找 `match` 的主要状态分支；
6. 找 `?` 的错误退出；
7. 找 Guard/Drop 收尾；
8. 最后再读具体字段转换。

## 常见误解

- **“`Arc::clone()` 会复制整个 Session。”** 它只增加共享引用计数。
- **“用了 `Arc` 就可以直接修改里面的值。”** 共享可变状态仍需要锁或其他同步设计。
- **“`.await` 会阻塞整个线程。”** 它通常挂起当前 Task，让 executor 运行其他工作。
- **“`?` 会自动重试。”** 它只传播错误。
- **“`Send + Sync` 表示业务逻辑绝对不会竞态。”** 它只保证类型满足线程安全约束。
- **“创建 Guard 后没调用方法，所以没作用。”** 它可能在 `Drop` 中完成关键收尾。

## 本章词汇表

| 词语 | 直译 | 在本章中的意思 |
|---|---|---|
| Ownership | 所有权 | 谁负责持有并最终释放一个值 |
| Borrow | 借用 | 临时通过引用访问值，不接管其所有权 |
| Reference count | 引用计数 | 记录当前有多少强引用共同持有对象 |
| Interior mutability | 内部可变性 | 通过锁等机制从共享引用安全修改内部状态 |
| Trait object | Trait 对象 | 运行时使用不同具体实现的 `dyn Trait` 值 |
| Object safety | 对象安全 | Trait 是否能合法用作 `dyn Trait` 的规则集合 |
| Lifetime | 生命周期参数 | 描述引用之间有效范围关系的编译期约束 |
| RAII | 资源获取即初始化 | 用对象生命周期自动管理资源和收尾 |
| Guard | 守卫对象 | 持有锁、槽位或计时状态，并在 Drop 时释放/结束 |
| RPITIT | Trait 中返回位置的 `impl Trait` | 用 `impl Future + Send` 表达 Trait 异步返回契约 |

完整解释见[术语总表](glossary.md)。

## 读完后自测

1. `Option<Arc<TurnContext>>` 应怎样从外向内解释？
2. `Arc::clone()` 和 `String::clone()` 的成本与语义为何不同？
3. 为什么 `Weak<Session>::upgrade()` 返回 `Option<Arc<Session>>`？
4. `?`、Retry 和错误转成 Tool Result 分别由哪一层负责？
5. `Pin<Box<dyn Future + Send + 'a>>` 中每一层解决什么问题？
6. 一个 `_guard` 变量看似未使用时，为什么应该先查 `Drop`？

## 源码检查点

- `codex-rs/core/src/tasks/mod.rs`：`Arc`、Task spawn、Cancellation 与 Guard；
- `codex-rs/core/src/session/mod.rs`：共享 Session 状态和 locks；
- `codex-rs/tools/src/tool_executor.rs`：Trait object、boxed Future、exposure enum；
- `codex-rs/ext/extension-api/src/contributors.rs`：`ExtensionFuture<'a, T>`；
- `codex-rs/core/src/turn_timing.rs`：RAII timing guard；
- `codex-rs/core/src/session/mcp_refresh.rs`：Drop 时恢复状态；
- `codex-rs/core/src/mcp_tool_exposure.rs`：`Weak<McpBinding>`；
- `codex-rs/protocol/src/error.rs`：enum、`From` 与 exhaustive match。
