# 65：Rust 生命周期与异步边界——Borrow、`'static`、`Send`、`Sync` 和 Spawn

> 源码基线：`4ee41929eaf4`
>
> 第 26 章给出了所有权、`Arc`、Future 与 lifetime 的快速读法，第 27 章解释 Tokio task、channel 和取消，第 63 章解释值的内存布局。本章专门深入它们之间最容易卡住的一层：编译器怎样证明 reference 仍然有效，Future 为何有时不能 spawn，`Send + Sync + 'static` 究竟分别保证什么，以及 Codex 为什么在短借用和后台 owned task 之间使用不同 API 形状。

## 1. 本章解决什么问题

读大型 Async Rust 项目时，最常见的困惑往往不是业务逻辑，而是这些错误和签名：

```rust
fn wait_until_ready<'a>(
    &'a self,
    session: &'a Arc<Session>,
) -> Option<BoxFuture<'a, ()>>;

fn spawn(
    &mut self,
    future: impl Future<Output = ()> + Send + 'static,
);
```

本章回答：

- Lifetime 是什么，又不是什么？
- 为什么多数 lifetime 不写出来也存在？
- `&T`、`&mut T`、move、reborrow 分别改变什么？
- `'static` 为什么通常不表示“永不释放”？
- `T: 'static` 与 `&'static T` 有何区别？
- `Send`、`Sync` 为什么是两个独立条件？
- `async move` 为什么仍可能不是 `'static` 或 `Send`？
- 为什么借用能穿过某些 `.await`，却不能进入 `tokio::spawn`？
- `BoxFuture<'a, T>` 与 `BoxFuture<'static, T>` 各表达什么？
- Codex 的 `SessionTask` 为什么先返回 `impl Future + Send`，再擦除成 `BoxFuture`？
- 怎样把 borrow checker 报错翻译成“谁可能比谁活得更久”？
- 何时 clone、何时 `Arc::clone`、何时缩短 scope、何时改 API？

## 2. 先说人话：引用是一张有失效期限的借阅证

```rust
let text = String::from("hello");
let view = &text;
println!("{view}");
```

`view` 不拥有字符 buffer。它只是借用 `text` 所拥有的数据。

编译器必须证明：

```text
view 的每一次使用
都发生在 text 仍然有效的范围内
```

Lifetime 是这项证明中的“有效范围关系”，不是运行时倒计时器。

## 3. Lifetime 不是对象活了多少秒

`'a` 不保存 timestamp，也不会在程序运行时递减。

```rust
fn first<'a>(input: &'a str) -> &'a str {
    input.split(',').next().unwrap_or(input)
}
```

它表达的是：返回 reference 不能比 `input` reference 的有效范围更长。

## 4. Lifetime 参数描述关系，不负责延长寿命

给引用标 `<'a>` 不会让 owner 多活一会儿：

```rust
fn bad<'a>() -> &'a str {
    let s = String::from("temporary");
    &s
}
```

函数结束时 `s` 被 drop。无论把 `'a` 写得多漂亮，返回 reference 都会悬空，因此编译器拒绝。

## 5. Owner 与 borrower

| 角色 | 例子 | 负责什么 |
|---|---|---|
| Owner | `String`、`Vec<T>`、`Arc<T>` | 决定 owned resource 何时释放 |
| Shared borrower | `&T` | 临时只读访问 |
| Exclusive borrower | `&mut T` | 临时独占可变访问 |
| Lifetime | `'a` | 约束 reference 的可用范围 |

分析报错时先找 owner，再找 reference；不要从 `'a` 字母本身猜答案。

## 6. Shared reference：`&T`

同一时段可存在多个 shared references：

```rust
let a = &value;
let b = &value;
use_both(a, b);
```

它们不能通过普通 `&T` 修改 `T`。这使“多人读”不会与普通写操作同时发生。

## 7. Mutable reference：`&mut T`

`&mut T` 表示在该借用范围内的独占访问：

```rust
let item = &mut value;
item.update();
```

关键不只是“可以修改”，而是同一段数据此刻没有其他仍在使用的冲突 reference。

## 8. Borrow rule 的简化版本

在同一有效范围内，通常允许：

```text
任意多个 &T
或者一个 &mut T
但不能让发生冲突的借用重叠
```

这是 data-race freedom 的编译期基础之一。

## 9. Non-lexical lifetimes

现代 Rust 常在 reference 最后一次使用后结束借用，而不是机械等到大括号结束：

```rust
let r = &value;
println!("{r}"); // r 最后一次使用
mutate(&mut value); // 可以
```

这称为 non-lexical lifetimes（NLL）。源码 scope 仍重要，但“最后使用点”更重要。

## 10. Move 与 borrow 的根本区别

```rust
let b = a;  // 对非 Copy 类型通常移动 ownership
let r = &b; // 只借用
```

Move 后旧变量通常不能再用；borrow 期间 owner 仍是原对象，只是某些操作暂时受限。

## 11. `Copy` 为什么看起来没有 move

`u32`、`bool` 等实现 `Copy` 的类型在赋值时复制 bits：

```rust
let a = 7u32;
let b = a;
println!("{a} {b}");
```

`String` 不实现 `Copy`，因为按位复制 pointer 会制造两个 owner，最后 double free。

## 12. Clone 是显式的新 ownership 决定

```rust
let b = a.clone();
```

它表示调用 type 定义的 clone 语义：`String` 深拷贝 payload，`Arc` 只增加 strong count，`Bytes` 常共享 backing storage。

为解决 lifetime 报错而 clone 前，必须先知道 clone 的成本和 ownership 意义。

## 13. Reborrow 是“借来的权限再短借一次”

```rust
fn inspect(_: &mut String) {}

let r = &mut text;
inspect(&mut *r);
r.push('!');
```

调用期间产生更短的 mutable reborrow；结束后原来的 `r` 可继续使用。这解释了为何 `&mut T` 参数并不总把 reference 本身永久 move 掉。

## 14. Deref coercion

`&String` 常可传给接收 `&str` 的函数，因为 `String: Deref<Target = str>`：

```rust
fn show(s: &str) {}
let owned = String::from("hello");
show(&owned);
```

这不是复制 string，而是 compiler 插入适当 dereference/coercion。

## 15. Lifetime elision

很多函数不写 `'a`：

```rust
fn trim(input: &str) -> &str;
```

Compiler 根据 elision rules 推断它相当于：

```rust
fn trim<'a>(input: &'a str) -> &'a str;
```

省略的是标注，不是约束。

## 16. 多个输入引用时为什么可能要显式标注

```rust
fn choose(a: &str, b: &str) -> &str
```

返回的是 `a` 还是 `b`？Compiler 无法仅从签名判断。需要写出关系，例如：

```rust
fn choose<'a>(a: &'a str, b: &'a str) -> &'a str;
```

这并不要求两者实际寿命完全相等；调用时会选择双方都覆盖的可用范围。

## 17. Method 中 `&self` 的 elision 特例

```rust
fn name(&self) -> &str;
```

通常把返回 reference 关联到 `self` 的 borrow。这符合 getter 的常见意图。

但若返回值来自另一个参数，往往需要显式 lifetime 避免误判。

## 18. Struct 保存 reference

```rust
struct View<'a> {
    text: &'a str,
}
```

它表示 `View` value 不能比被借用的 string data 活得更久。`View<'a>` 自己仍是一个普通 owned struct，只是内部含 reference。

## 19. 为什么带引用的长期 state 更难管理

若 struct 被放进 collection、channel、cache 或 background task，它的存活范围可能扩大。内部 reference 会把 owner 的 lifetime 一起传播到 API 各层。

因此跨 task、长期存储的 state 常偏向 owned `String`、`PathBuf`、`Arc<T>`，而函数内短暂 view 常用 `&str`、`&Path`。

## 20. Self-referential struct 的陷阱

一个 struct 同时拥有 `String` 并保存指向该 String 内部的 reference，会遇到移动后地址可能变化的问题：

```text
struct owns buffer
struct also points inside its own buffer
moving struct may invalidate internal pointer
```

普通 safe references 很难直接表达。常见做法是保存 offset、重新切片、使用 owner/view 分离，或在确有需要时使用经过验证的 pin/self-referential abstraction。

## 21. `Cow<'a, T>`：可借用也可拥有

`Cow` 是 clone-on-write：

```rust
Cow::Borrowed(&'a str)
Cow::Owned(String)
```

输入无需修改时可避免 clone；需要规范化或跨越更长边界时转为 owned。它不是总能省分配，应该按 hot path 和 API 复杂度选择。

## 22. `&'static str` 是静态引用

String literal 嵌在程序 binary 中：

```rust
let label: &'static str = "request";
```

这条 reference 在整个程序运行期间有效。Codex 用 `&'static str` 保存稳定 telemetry label、task span name 和 global serialization key。

## 23. `T: 'static` 不是“这个 value 永远存在”

它表示 `T` 不包含会在 `'static` 之前失效的借用。Owned `String` 满足 `String: 'static`，但局部 `String` 仍可在下一行被 drop：

```rust
let s = String::from("temporary");
drop(s);
```

`'static` bound 约束 type 可独立存活，不承诺实际存活时长。

## 24. `&'static T` 与 `T: 'static`

| 写法 | 含义 |
|---|---|
| `&'static T` | 这条具体 reference 整个程序期间有效 |
| `T: 'static` | T 内部没有较短借用，可由 owner 自己决定何时 drop |

这是理解 `tokio::spawn` 的关键区别。

## 25. 为什么 Spawn 常要求 `'static`

Spawned task 可能比创建它的函数活得更久：

```rust
fn start() {
    let local = String::from("x");
    tokio::spawn(async { use_it(&local).await });
} // local 在这里 drop，但 task 也许还没 poll
```

Runtime 不能接受这种潜在悬空引用，所以要求 task future 是 `'static`。

## 26. `'static` task 仍然会结束和释放

```rust
tokio::spawn(async move {
    do_work(owned).await;
});
```

只要 future 拥有自己需要的数据，它可以满足 `'static`。Task 完成后 future 和 owned fields 正常 drop；并非内存泄漏。

## 27. `async move` 做什么

`move` 要求 async block 按 value 捕获外部变量，而不是默认借用：

```rust
let name = String::from("job");
tokio::spawn(async move {
    run(name).await;
});
```

`name` ownership 进入 future state machine，因此创建函数退出后仍安全。

## 28. `async move` 不保证所有 captured data 都是 owned

若变量本身就是短 reference：

```rust
let borrowed: &str = local.as_str();
tokio::spawn(async move { use_it(borrowed).await });
```

Move 的只是 reference value，不是其 referent。它仍带原 lifetime，因此可能不满足 `'static`。

## 29. 解决 Spawn lifetime 的三种常见方式

```text
数据很小且语义独立     → clone/to_owned 后 move
大对象需要共享         → Arc::clone 后 move
任务不该逃离当前 scope → 不 spawn，直接 await 或 scoped concurrency
```

不要把所有错误都机械修成 `Arc<Mutex<_>>`。

## 30. Codex 的 Connection cleanup 边界

固定源码：

```rust
pub(crate) fn spawn(
    &mut self,
    future: impl Future<Output = ()> + Send + 'static,
)
```

`ConnectionCleanupTasks` 把 future 放进 `JoinSet`。它可能在调用函数返回后继续，所以同时要求：

- `Future`：可被 poll；
- `Output = ()`：只关心完成；
- `Send`：可在 multithread runtime 调度；
- `'static`：不借用即将消失的 stack data。

## 31. `Send` 的含义

若 `T: Send`，把 T 的 ownership 移到另一个 OS thread 是 memory-safe 的。

常见 Send：

- primitive values；
- `String`、`Vec<T: Send>`；
- `Arc<T>`，前提通常是 T 满足所需线程安全 bounds。

典型 non-Send：`Rc<T>`。

## 32. `Sync` 的含义

若 `T: Sync`，多个 threads 同时持有 `&T` 是 memory-safe 的。

等价直觉：

```text
T: Sync
大致意味着
&T: Send
```

普通 `RefCell<T>` 不是 Sync，因为它的 borrow flag 不是线程安全同步机制。

## 33. `Send` 与 `Sync` 不代表业务绝无竞态

`Arc<Mutex<Account>>` 可满足类型层线程安全，但业务仍可能出现：

```text
先读余额
释放锁
再根据旧余额执行写入
```

类型系统防 data race 和内存不安全，不自动证明 transaction invariant、ordering 或幂等。

## 34. Auto trait

`Send`、`Sync` 多数由 compiler 根据 fields 自动推导：

```text
struct Job { name: String, config: Arc<Config> }
```

若所有组成部分满足相应规则，`Job` 通常自动满足。加入一个 `Rc<_>` 或 non-Send guard，整个 future/struct 可能随之变成 non-Send。

## 35. `Rc<T>` 与 `Arc<T>`

| 类型 | 引用计数 | 使用范围 |
|---|---|---|
| `Rc<T>` | 非原子 | 单线程 ownership sharing |
| `Arc<T>` | 原子 | 可跨线程共享 ownership |

`Arc<T>` 只解决 owner count 的线程安全；内部 T 是否能共享修改仍取决于 T。

## 36. `RefCell<T>` 与 `Mutex<T>`

| 类型 | 检查时机 | 典型范围 |
|---|---|---|
| `RefCell<T>` | 运行时 borrow check，违反会 panic | 单线程 interior mutability |
| `Mutex<T>` | 运行时互斥锁 | 多线程/多 task 排他访问 |

把 `Rc<RefCell<T>>` 替成 `Arc<Mutex<T>>` 不是纯语法替换：await、poisoning、lock scope 和性能语义都会改变。

## 37. `Arc::clone` clone 的是什么

```rust
let task_session = Arc::clone(&session);
```

它复制 Arc handle 并增加 strong count，不复制整个 Session。新 task 获得独立 owner handle，因此不再借用调用者 stack 中的 `session` variable。

## 38. 为什么显式写 `Arc::clone`

`session.clone()` 也能工作，但 `Arc::clone(&session)` 更清楚地提示 reviewer：

```text
这是 shared ownership clone，
不是深拷贝整个 Session。
```

在大量 clone 的 async orchestration 中，这种可读性很有价值。

## 39. Strong count 改变运行寿命

每个 spawned task 持有的 `Arc` clone 都可能让对象继续存活。Borrow checker 问题解决了，不代表 lifecycle 就一定正确。

若 task 泄漏或永不结束，Session、sender 和 caches 也可能被强引用保留。第 48、63 章的 owner graph 仍需结合使用。

## 40. `Weak<T>` 不取得存活权

长期 callback/listener 若只需“对象还活着就用”，可持有 `Weak<T>`：

```rust
if let Some(session) = weak.upgrade() {
    session.do_work().await;
}
```

它避免 observer 反过来延长 owner 生命周期，也能打破 strong cycle。

## 41. Future 是捕获状态的值

调用 async fn 时会得到 Future；它保存：

- 尚未完成的局部变量；
- 捕获的 arguments/owners/references；
- 当前执行到哪个 suspension point；
- 继续执行所需状态。

因此“future 是否 Send/`'static`”取决于这些保存状态，而不只是函数名。

## 42. `.await` 是潜在 suspension point

在 `.await` 时，当前 task 可暂停，future 被 runtime 保存，稍后可能在另一个 worker thread 继续 poll。

所有跨越 await 仍需使用的 locals 都成为 future state 的一部分。

## 43. 借用可以跨 `.await`

这不是一律禁止：

```rust
async fn use_ref(input: &Config) {
    inspect(input).await;
}
```

返回 future 的 lifetime 会受 `input` 限制。只要调用者在 future 完成前保持 Config 有效，直接 `.await` 是安全的。

## 44. 借用跨 `.await` 与 spawn 的差别

```text
use_ref(&config).await
```

Future 通常不会逃离当前 async scope；parent 等它结束。

```text
tokio::spawn(use_ref(&config))
```

Child 可能独立存活，因此普通 spawn 通常要求 `'static`，短 borrow 不再足够。

## 45. 借用 Future 的真实 Codex 例子

`TimeProvider` 定义：

```rust
pub type TimeFuture<'a> =
    Pin<Box<dyn Future<Output = Result<DateTime<Utc>>> + Send + 'a>>;

fn current_time(&self, thread_id: ThreadId) -> TimeFuture<'_>;
```

返回 future 可以借用 provider 的 `self`，因此 lifetime 是 `'_`，而不是强迫每个实现返回 `'static` future。

## 46. 为什么 `TimeFuture<'_>` 适合直接 await

调用通常形如：

```text
provider.current_time(thread_id).await
```

Provider 在 await 完成前保持被借用。这里不需要让 future 脱离 provider 独立后台运行，借用 API 更自然，也避免为了满足 `'static` 额外 clone owner。

## 47. `BoxFuture<'a, T>` 展开

Futures crate 的别名可理解为：

```rust
Pin<Box<dyn Future<Output = T> + Send + 'a>>
```

逐层含义：

- `Future<Output = T>`：完成时产生 T；
- `dyn`：隐藏具体 future type；
- `+ Send`：可跨 worker thread；
- `+ 'a`：内部借用至少在 `'a` 范围有效；
- `Box`：固定大小 pointer；
- `Pin`：满足 poll 时不可随意移动的要求。

## 48. `BoxFuture<'static, T>`

它表示 erased future 不包含短期借用，可独立保存到 queue、struct 或 spawned task：

```rust
type BoxFutureUnit =
    Pin<Box<dyn Future<Output = ()> + Send + 'static>>;
```

App-server serialized request queue 就保存这种 future，因为排队时间可能超过原 handler stack frame。

## 49. Queue 为什么要求 owned future

`QueuedInitializedRequest` 把 future 存为 field：

```rust
pub(crate) struct QueuedInitializedRequest {
    gate: Option<Arc<ConnectionRpcGate>>,
    future: BoxFutureUnit,
}
```

它可能先在 `VecDeque` 等待其他 request，再被 drain。若内部借用调用者局部变量，排队期间 owner 可能已经离开 scope。

## 50. `Pin` 解决什么

Compiler 生成的 async future 可能含“逻辑上指向自身状态”的关系。Future 一旦开始 poll，随意移动其 storage 可能破坏这种关系。

`Pin<Box<F>>` 固定 heap allocation 的位置，并通过 API 限制移动。Pin 不延长 lifetime，也不自动使 F 成为 Send。

## 51. `impl Future` 与 `dyn Future`

| 形式 | 具体类型 | 大小 | 分派 |
|---|---|---|---|
| `impl Future` | 编译期已知但对调用者隐藏 | 静态已知 | 静态分派 |
| `dyn Future` | 运行时 trait object | 通过 pointer | 动态分派 |

前者通常无 box/vtable 成本；后者适合 collection、object-safe adapter 或统一 return type。

## 52. RPITIT 是什么

Return-position `impl Trait` in trait：

```rust
trait Task {
    fn run(&self) -> impl Future<Output = Result<()>> + Send;
}
```

每个 implementation 可返回自己的匿名 future type，同时 trait contract 明确返回 future 是 Send。

## 53. 为什么 Codex 不用隐藏 `Send` 的 async trait shortcut

固定仓库约定偏好：

```rust
fn foo(&self) -> impl Future<Output = T> + Send;
```

这样调用者能直接看到 future 的 thread-mobility contract，也避免 macro boxing 成本和 lifetime 规则被藏起来。

## 54. `SessionTask` 的核心签名

```rust
pub(crate) trait SessionTask: Send + Sync + 'static {
    fn run(
        self: Arc<Self>,
        session: Arc<Session>,
        ctx: Arc<TurnContext>,
        input: Vec<TurnInput>,
        cancellation_token: CancellationToken,
    ) -> impl Future<Output = SessionTaskResult> + Send;
}
```

Task implementation、Session、TurnContext、input、token 都以 owned/shared-owned value 进入 future，天然适合 background spawn。

## 55. `self: Arc<Self>` 是什么 receiver

它不是普通 `&self`。调用 method 时消费一个 Arc handle，让 future 自己拥有 task 的 shared ownership：

```text
&self          → future 借用 task
self: Arc<Self> → future 拥有 task 的 Arc handle
```

后者更容易返回 `'static` future。

## 56. 为什么 trait 自身要求 `Send + Sync + 'static`

- `Send`：Task owner 可跨 thread 移动；
- `Sync`：共享 `&Task` 可跨 thread 使用；
- `'static`：Task implementation 不藏短期 reference；
- `Arc<Self>`：让多个 orchestration components 共享 ownership。

这些是执行容器的结构约束，不保证 task 的业务结果正确。

## 57. Native trait 到 erased trait adapter

Codex 另有 `AnySessionTask`：

```rust
fn run(...) -> BoxFuture<'static, SessionTaskResult>;
fn abort<'a>(&'a self, ...) -> BoxFuture<'a, ()>;
```

Blanket implementation 对任意 `T: SessionTask` 执行 `Box::pin(...)`。这样实现者享受 RPITIT，orchestrator 又能统一持有 erased tasks。

## 58. 为什么 `run` 是 `'static`，`abort` 是 `'a`

`run` 获取 `self: Arc<Self>` 和其他 owned Arc/Vec/token，因此能成为独立 background future。

`abort` 只借用 `&'a self`，返回 future 可能继续使用该 borrow，所以返回 `BoxFuture<'a, ()>`。两者准确反映不同 ownership。

## 59. 这不是格式差异，而是架构信息

看到：

```text
BoxFuture<'static, T>
```

应问“谁需要长期保存或 spawn 它？”

看到：

```text
BoxFuture<'a, T>
```

应问“它借用了哪个 owner，谁保证 await 结束前 owner 还活着？”

## 60. Tool readiness 的借用 Future

`CoreToolRuntime`：

```rust
fn wait_until_ready<'a>(
    &'a self,
    session: &'a Arc<Session>,
) -> Option<BoxFuture<'a, ()>>;
```

同一个 `'a` 把 future 同时约束在 runtime 与 Session borrow 的共同有效范围内。

## 61. MCP implementation 为什么需要 `'a`

```rust
Some(Box::pin(async move {
    session
        .wait_for_mcp_server(&self.tool_info.server_name)
        .await;
}))
```

Future 使用 `session` 和 `self.tool_info` 的 references，因此不能脱离两者。调用者立即 await 它，这种短 borrow 正合适。

## 62. Spawn 前先 clone owners，Spawn 内再短借

工具并行执行路径先把 `session`、runtime、router、turn 等 owned handles move 进 spawned future：

```rust
tokio::spawn(async move {
    if let Some(readiness) = tool_runtime.wait_until_ready(&session) {
        readiness.await;
    }
    // ...
})
```

外层 future 是 `'static`；在它内部产生的 readiness borrow 只活到内部 await 完成。这是非常常见的组合。

## 63. “把 borrow 放到 task 内部”为什么有效

错误形状：

```text
在 stack owner 外部创建 borrow
→ 把 borrow move 进独立 task
```

正确形状：

```text
把 owner/Arc move 进 task
→ task 内临时 borrow owner
→ await 完后 borrow 结束
```

Owned boundary 外移后，内部仍可享受零拷贝借用。

## 64. `Send` Future 的判定看跨 await 状态

Future 若把 non-Send value 保存在某个 await 之间，就可能不是 Send：

```rust
let local = Rc::new(data);
some_wait().await;
use_it(local);
```

因为 runtime 可能在另一 worker thread 恢复，而 `Rc` 不能跨线程移动。

## 65. 缩短 non-Send value scope

若 non-Send value 不需要跨 await，可在 await 前结束：

```rust
let result = {
    let local = Rc::new(data);
    compute(&local)
}; // local drop
some_wait().await;
use_result(result);
```

Compiler 只需 future 在 suspension 时保存 Send state。

## 66. `drop(x)` 有时不如 lexical scope 清楚

显式 `drop(guard)` 常可缩短 guard 生命周期，但 compiler 对复杂 pattern/capture 的判断可能更容易从一个明确 inner block 得出。

为了可读性和 future Send 问题，优先让 lock/guard 有小 scope。

## 67. 持锁跨 `.await`

```rust
let mut guard = state.lock().await;
network_call().await;
guard.update();
```

即使 guard 是 Send，这也可能让其他 tasks 在慢 I/O 期间一直等锁。若 guard 不是 Send，还可能导致整个 future 不能 spawn。

## 68. 推荐的锁内/锁外分离

```rust
let snapshot = {
    let guard = state.lock().await;
    guard.snapshot()
};

let result = network_call(snapshot).await;

{
    let mut guard = state.lock().await;
    guard.apply(result);
}
```

但必须重新检查版本/代数，避免锁外期间 state 已改变造成 stale write。

## 69. `std::sync::Mutex` 与 Tokio Mutex

同步 Mutex 的 guard 通常适合非常短、不会 await 的临界区；Tokio Mutex 允许异步等待并可有意跨 await。

选择依据不是“代码在 async fn 里”，而是：

- 竞争时能否阻塞 worker？
- guard 是否必须跨 await？
- critical section 多长？
- 访问频率和 contention 如何？

## 70. `Send` 错误常指向 `.await`

Compiler 可能说：

```text
future cannot be sent between threads safely
value is used across an await
```

真正根因通常在更早：某个 local/guard/reference 被创建，并在 await 后仍被使用。要沿 diagnostic notes 找“captured value 的类型”。

## 71. `tokio::spawn` 与 `spawn_local`

`tokio::spawn` 面向可在线程间调度的 task，要求 Send。

`spawn_local` 可运行 non-Send future，但必须位于 `LocalSet`/local runtime context。它把架构限制成单线程调度，不应只为绕过一个错误随意采用。

## 72. `spawn_blocking` 也要求 owned/thread-safe inputs

Blocking closure 在线程池运行，可能晚于调用函数结束：

```rust
tokio::task::spawn_blocking(move || expensive_sync(owned))
```

Closure 和返回结果通常需要 `Send + 'static`。把短 `&Path` 传进去常需先 `to_path_buf()`。

## 73. 为什么常看到 `PathBuf` 而不是 `&Path`

同步 helper 内部用 `&Path` 很高效；进入 queue、blocking thread 或 background task 时，owner 不再受调用 stack 保证，因此常转为 owned `PathBuf`。

这不是 Rust “偏爱复制”，而是 API 生命周期改变。

## 74. Channel 会转移 ownership

```rust
tx.send(message).await?;
```

Message 被 move 给 channel/receiver。若 message 内含 reference，它的 lifetime 必须覆盖 receiver 可能使用的时间；长期 channel message 因而常使用 owned fields。

## 75. `mpsc::Sender<T>` 的 T 为什么常需 Send

Sender 与 receiver 可能在不同 Tokio worker threads。T 经过 queue 从 producer ownership 转移到 consumer，因此必须能安全跨线程移动。

Channel 保证队列同步，不保证 message 内部业务状态一致。

## 76. Returning reference 与 returning owner

```rust
fn get_name(&self) -> &str
```

避免 clone，但调用者被 `self` lifetime 约束。

```rust
fn get_name(&self) -> String
```

调用者获得独立 owner，但要分配/复制。

```rust
fn get_name(&self) -> Arc<str>
```

共享 owner，clone 较轻，但增加引用计数和 heap indirection。

## 77. API 不应为了 borrow 最小化而过度复杂

若 data 很小、调用频率低、要跨 async boundary，返回 owned value 往往更清楚。若是 hot path 的大 immutable buffer，borrow 或 `Bytes`/`Arc<[u8]>` 可能更合适。

正确目标是整体生命周期和成本清楚，而不是 clone 数量绝对为零。

## 78. `Deserialize<'de>` 与 `DeserializeOwned`

第 64 章中的 Serde lifetime 现在可以更精确理解：

```text
T: Deserialize<'de>
```

允许 T 从输入 `'de` 借用。

```text
T: DeserializeOwned
```

表示 T 不依赖某个特定输入 buffer lifetime，适合 input buffer 解析后即释放的 API。

## 79. `DeserializeOwned` 的直觉

若函数读取临时 String，然后返回 T：

```rust
fn load<T: DeserializeOwned>(path: &Path) -> T
```

函数结束时原文本 buffer 会 drop，所以返回 T 不能含指向该 buffer 的借用。

这与 `tokio::spawn` 的 owned boundary 有相似精神：结果需要独立于临时输入活着。

## 80. Higher-ranked trait bound

```rust
for<'a> LookupSpan<'a>
```

读作：“对任意调用者选择的 lifetime `'a`，都实现这个 trait。”

它比“对某一个预先固定的 `'a`”更强，常用于 callback、borrowed view 和 tracing subscriber API。

## 81. `for<'a>` 不会制造静态引用

HRTB 表达实现具有普适性：每次可接受一个合适的短 borrow。它与 `'static` 恰恰不同：前者支持任意短 lifetime，后者表示不依赖短 borrow。

## 82. Trait object 为什么常带 lifetime

```rust
Box<dyn Future<Output = T> + Send + 'a>
```

`dyn Future` 隐藏具体实现后，compiler 仍必须知道里面可能保存的 references 能活多久，所以 trait object lifetime bound 不能凭空消失。

## 83. Trait object 的默认 lifetime 容易被误读

某些 object contexts 会推断默认 object lifetime，可能最终要求 `'static`。遇到“borrowed data escapes”时，检查是不是：

```rust
Box<dyn Trait>
```

应实际写成：

```rust
Box<dyn Trait + 'a>
```

但只有 container 本身真的不会越过 `'a` 时才这样改。

## 84. Object safety 与 erased adapter

含 RPITIT/generic methods 的 trait 不一定能直接做 `dyn Trait`。Codex 的 `SessionTask`/`AnySessionTask` 分层展示了一种办法：

```text
implementation-facing static trait
→ blanket adapter + Box::pin
→ orchestration-facing erased trait
```

Boxing 成本集中在需要动态分派的边界。

## 85. Associated type 也会传播约束

`WorldStateSection`：

```rust
pub(crate) trait WorldStateSection: Send + Sync + 'static {
    const ID: &'static str;
    type Snapshot: DeserializeOwned + Serialize;
}
```

Section 可长期保存并跨线程使用；ID 是稳定 static string；Snapshot 必须 owned 地从持久数据恢复并可再次序列化。

## 86. `DeserializeOwned` 在 persisted snapshot 中的意义

Rollout JSON 被读入后，解析 buffer 可释放，但 Snapshot 仍要进入 world state 比较逻辑。若 Snapshot 借用了临时 JSON text，它无法安全存活。

因此 associated type 的 bound 直接反映持久化 owner boundary。

## 87. Generic bound 是调用者与实现者的合同

```rust
T: Send + Sync + 'static
```

不是装饰。它决定：

- 什么类型能传入；
- 实现内部可安全执行什么；
- future 能否 spawn；
- trait object 能否跨线程保存；
- test double 也必须满足哪些性质。

## 88. Bound 放太宽会污染 API

若一个同步 helper 根本不跨线程，却无理由要求 `T: Send + Sync + 'static`，会排除合法的 borrowed/non-Send callers，并迫使多余 clone/Arc。

Bound 应放在真正需要线程或存储独立性的边界。

## 89. Bound 放太晚会产生难懂错误

如果通用 queue 接受任意 F，却到内部 `tokio::spawn` 才失败，diagnostic 可能离调用点很远。

像 `enqueue_background(... future: impl Future + Send + 'static)` 这样在 API 边界声明要求，错误更接近 producer。

## 90. Variance 的最低限度直觉

某些 container 允许较长 reference 当作较短使用，另一些因可变性不能安全缩短/替换。无需先背完整 variance 表，但要记住：

```text
shared immutable view 通常更灵活
mutable/invariant container 更严格
```

遇到 lifetime “明明够长却不能转换”时，检查类型是否包含 `&mut`、interior mutability 或 invariant generic。

## 91. Mutable reference 为什么更严格

若允许把 `&mut Container<&'long T>` 任意当成 `&mut Container<&'short T>`，就可能写入 short reference，随后通过 long view 读出悬空引用。

很多看似苛刻的 lifetime 规则是在阻止这种“通过可变位置写进更短借用”。

## 92. `'a: 'b` 怎样读

```rust
'a: 'b
```

读作：`'a` 至少和 `'b` 一样长，或 `'a` outlives `'b`。

```rust
T: 'a
```

表示 T 内部引用在 `'a` 内有效。它不是说变量 T 一定被保留到 `'a` 结束。

## 93. 编译错误 E0382：use of moved value

翻译：ownership 已被前一个操作转移。

排查：

1. 哪一行消费了 self/value？
2. Callee 参数是 T、`&T` 还是 `Arc<T>`？
3. 后续确实还需要同一 owner 吗？
4. 应改 borrow、先计算、还是显式 clone？

## 94. E0502：同时 mutable 与 immutable borrow

翻译：一个仍会被使用的 shared borrow 与 mutation scope 重叠。

修复优先级：

```text
缩短 shared borrow
→ 先复制所需小字段
→ 拆分不相交 fields
→ 重构 API
→ 最后才考虑 interior mutability
```

## 95. E0515：返回局部值的引用

翻译：returned reference 的 referent 将在函数结束时 drop。

通常应返回 owned value、让 caller 提供 storage，或让 reference 指向 caller-owned input。增加 lifetime parameter 不能拯救局部 owner。

## 96. E0521：borrowed data escapes

翻译：短 borrow 被放进了可能活得更久的 container/task/callback。

检查：

- 是否进入 `tokio::spawn`？
- 是否存进 struct/queue？
- Trait object 是否默认要求 `'static`？
- Closure 是借用捕获还是 move owned value？

## 97. “Future is not Send”

翻译：Future 在 suspension state 中保存了不能跨线程移动的 value。

寻找 diagnostic 中：

```text
which value is not Send
where it is created
which await keeps it alive
where Send is required
```

常见根因是 `Rc`、`RefCell` reference、non-Send guard 或 trait object 缺少 Send bound。

## 98. “May not live long enough”

不要盯着 `'1`、`'2`。把它改写成人话：

```text
返回值/field/future 需要至少活到 B
但它借用了只保证活到 A 的数据
A 可能早于 B 结束
```

再画 owner 与 consumer 的 scope，通常比反复加 annotation 有效。

## 99. 编译错误中的 “required because” 链

Rust diagnostic 常沿 type 层层解释：

```text
Rc<X> non-Send
→ captured by async block
→ async block becomes non-Send
→ required by tokio::spawn
```

最上面是症状，最下面附近常是 boundary requirement，中间第一项常是真正 problematic field。

## 100. 修复决策树

```text
这个工作必须独立 spawn 吗？
├─ 否 → 直接 await，保留短 borrow
└─ 是
   ├─ 数据可便宜拥有？ → clone/to_owned + async move
   ├─ 大对象共享？ → Arc::clone + async move
   ├─ non-Send 只在 await 前使用？ → 缩小 scope
   ├─ 本质单线程？ → 有意设计 LocalSet/spawn_local
   └─ API owner 不清？ → 重画 ownership，别先堆 Arc<Mutex>
```

## 101. 不推荐：到处加 `.clone()` 直到编译

这样可能：

- 深拷贝大 payload；
- 无意延长 Session 生命周期；
- clone sender 导致 channel 永不关闭；
- 掩盖 task 本应被 join 的事实；
- 让 stale snapshot 被异步使用。

每次 clone 都应回答“复制的是 bytes、handle 还是共享 owner？”

## 102. 不推荐：为了 `'static` 泄漏内存

`Box::leak` 能得到 static reference，但它永久放弃自动释放。除非对象确实设计为进程级永久数据，否则这不是普通 spawn lifetime 修复。

更常见答案是 owned move、Arc、直接 await 或 scoped task。

## 103. 不推荐：把所有 state 放进 `Arc<Mutex<_>>`

它可能通过编译，却引入：

- lock ordering；
- contention；
- await-under-lock；
- owner cycle；
- unclear mutation boundary；
- 更难测试的共享状态。

优先使用 message passing、immutable Arc、单 owner task 或较小的锁粒度。

## 104. 不推荐：随意使用 `unsafe impl Send/Sync`

这等于人工向 compiler 承诺内部跨线程行为安全。承诺错误会导致 undefined behavior，而不是普通业务 bug。

只有能逐条证明内部 invariants，并且现有安全 abstraction 无法表达时才考虑；代码评审应极严格。

## 105. Scoped concurrency

有些 task 必须并发，却不应逃离 parent scope。Scoped concurrency 允许 child 借用 parent data，同时保证 scope 结束前 child 已完成。

选择它时要验证具体 runtime/API 的取消、panic 和 join semantics；不能把普通 detached spawn 当 scoped spawn。

## 106. Structured concurrency 的 ownership 好处

```text
parent scope owns child tasks
→ parent waits/cancels them
→ child lifetime 不超过 parent
→ short borrows 有机会安全表达
```

这也减少无人持有 JoinHandle 的 detached task 和 Arc retention。

## 107. JoinHandle 本身也是 owner signal

若保存 `JoinHandle<T>`，调用者可以等待结果、观察 panic、abort task。直接 `drop(tokio::spawn(...))` 表示有意 detach；应确认错误、清理与 shutdown 由谁负责。

Lifetime 编译通过后，还要审查运行时 task ownership。

## 108. Cancellation 与 borrow safety 是两回事

Rust 保证 future drop 时 references 不悬空；但 cancellation 可能使业务 cleanup 没完成、锁前后状态只做了一半。

因此还需要 cancellation-safe API、RAII guard、显式 shutdown 和 terminal outcome。第 48 章处理的是这层运行语义。

## 109. Panic safety 与 `UnwindSafe`

某些 boundary 会要求 unwind-related traits，表达 panic unwind 后捕获 state 是否可安全观察。它不同于 Send/Sync，也不表示业务 transaction 自动 rollback。

Codex 常把 task panic 体现在 `JoinError`，cleanup owner 再决定记录、终止或继续。

## 110. 一个完整例子：错误的 Spawn

```rust
async fn start(session: &Session) {
    tokio::spawn(async move {
        session.flush().await;
    });
}
```

问题：Move 进 task 的只是 `&Session`；task 可能比 `start` 的 borrow 活得久。

## 111. 一个完整例子：Owned Spawn

```rust
async fn start(session: Arc<Session>) {
    let task_session = Arc::clone(&session);
    tokio::spawn(async move {
        task_session.flush().await;
    });
}
```

Task 拥有一个 Arc handle，满足独立 lifetime。仍需决定谁保存 JoinHandle、何时取消，以及这个 Arc 是否会让 Session 活得过久。

## 112. 一个完整例子：不必 Spawn

```rust
async fn flush(session: &Session) {
    session.flush().await;
}
```

若 caller 本来就要等待结果，直接 await 更简单：无需 `'static`、无需 Arc clone，错误和取消自然传播给 caller。

## 113. 一个完整例子：Spawn 内短 borrow

```rust
let session = Arc::clone(&session);
let runtime = Arc::clone(&runtime);

tokio::spawn(async move {
    if let Some(wait) = runtime.wait_until_ready(&session) {
        wait.await;
    }
    runtime.run(session).await;
});
```

外层 owners 独立；内层借用只覆盖 readiness await。这正是 Codex 工具执行路径的核心形状。

## 114. 一个完整例子：Boxed borrowed Future

```rust
fn wait<'a>(&'a self, state: &'a State) -> BoxFuture<'a, ()> {
    Box::pin(async move {
        self.check(state).await;
    })
}
```

Future lifetime 不能超过 `self` 与 `state` 的共同 borrow；调用者应在它完成前保持两者有效。

## 115. 一个完整例子：Boxed static Future

```rust
fn queued(state: Arc<State>) -> BoxFuture<'static, ()> {
    Box::pin(async move {
        state.run().await;
    })
}
```

Future 拥有 Arc，因此可以排队、存入 struct 或 spawn。`'static` 说的是“不借短数据”，不是“永远运行”。

## 116. 阅读复杂签名的固定顺序

看到：

```rust
Pin<Box<dyn Future<Output = Result<T>> + Send + 'a>>
```

从内到外读：

1. 完成产出 `Result<T>`；
2. 是某个被擦除的 Future；
3. Future 可 Send；
4. 内部借用最多受 `'a` 限制；
5. Box 提供统一大小；
6. Pin 保证 poll 所需的位置稳定。

## 117. 阅读 Spawn 前的固定检查

```text
捕获了哪些变量？
每个变量是 owner、Arc handle 还是 reference？
async block 是否 move？
哪些 locals 会跨 await？
它们都 Send 吗？
Future 是否含短 borrow？
谁持有 JoinHandle？
谁取消、drain 和观察 panic？
Arc clone 是否改变 shutdown 条件？
```

## 118. 代码评审清单：API lifetime

- 返回 reference 真有性能价值吗？
- Lifetime 是准确关联到 input/self，还是被无谓拉长？
- Long-lived struct 是否不必要地保存 borrow？
- Owned boundary 是否使用 String/PathBuf/Arc 等清楚表示？
- `T: 'static` 是否只放在真正存储/spawn 的边界？
- Trait object lifetime 是否明确？
- Custom future alias 是否清楚区分 `'a` 和 `'static`？

## 119. 代码评审清单：Send/Sync

- Future 真会跨 worker thread 吗？
- 哪些值跨越 `.await`？
- 是否持有 non-Send guard/Rc/reference？
- `Arc<T>` 的 T 是否真的可共享？
- Interior mutability 是否使用正确同步原语？
- Lock 是否跨慢 await？
- `Send + Sync` bounds 放在最小真实边界吗？
- 有无不安全的手写 Send/Sync 承诺？

## 120. 代码评审清单：Task ownership

- Spawn 是必要并发，还是逃避 await？
- Task 是 detached、joined 还是 scoped？
- 谁拥有 cancellation token？
- 谁 drain result 和 panic？
- Task capture 是否让 channel/Session 永不关闭？
- Weak 是否更符合 observer 角色？
- Shutdown 时有没有明确 abort/join 顺序？

## 121. 常见误解一：`'static` 就是全局变量

`String: 'static` 很常见，它仍可立即 drop。只有 `&'static T` 才是具体 static reference；`T: 'static` 只是排除了短借用。

## 122. 常见误解二：`async move` 会深拷贝所有东西

Move 转移 captured values，不自动 clone。Capture 是 String 就转移 String owner；是 Arc 就转移一个 Arc handle；是 `&T` 就转移 reference。

## 123. 常见误解三：`Arc` 让任何类型都线程安全

Arc 只让引用计数并发安全。`Arc<RefCell<T>>` 通常仍不能跨线程共享；需要 T 自身满足 Sync，mutation 还需合适 synchronization。

## 124. 常见误解四：Future 不能借用

Future 完全可以借用，`BoxFuture<'a, T>` 就明确表达它。限制来自 future 是否被保存/Spawn 到可能超过 borrow 的 scope，而不是 async 本身。

## 125. 常见误解五：Borrow checker 只会妨碍重构

它实际上在暴露架构问题：owner 不清、task 逃逸、共享 state 无边界、callback 可能过期。先把错误翻译成 ownership 关系，往往能得到更清晰的设计。

## 126. 常见误解六：编译通过就没有并发 bug

Rust 防止大量 memory safety/data race 问题，但 stale reads、lock ordering、duplicate side effects、deadlock、starvation 和 cancellation consistency 仍需设计与测试。

## 127. 本章最重要的心智模型

```text
短同步/直接 await 范围
  → borrow：&T / &mut T / BoxFuture<'a, T>

跨 queue/task/thread 的独立范围
  → ownership：T / String / PathBuf / Arc<T>
  → mobility：Send
  → shared reference safety：Sync
  → 不依赖短借用：'static

进入独立 task 后
  → 可以再从 owned state 建立短 borrow
```

Borrow 与 ownership 不是二选一：常见最佳结构是“边界上传 owner，边界内部短借用”。

## 128. 源码检查点

按以下顺序打开固定提交：

1. `codex-rs/core/src/tasks/mod.rs`
   - `SessionTask: Send + Sync + 'static`
   - `self: Arc<Self>`
   - RPITIT `impl Future + Send`
   - `AnySessionTask` 的 `BoxFuture<'static>` / `BoxFuture<'a>` adapter
2. `codex-rs/core/src/current_time.rs`
   - `TimeFuture<'a>`、`SleepFuture<'a>`
   - `TimeProvider: Send + Sync`
   - 返回 `TimeFuture<'_>` 的 borrowed future
3. `codex-rs/app-server/src/connection_cleanup.rs`
   - `Future + Send + 'static`
   - `JoinSet`、reap、drain、abort
4. `codex-rs/app-server/src/request_serialization.rs`
   - `BoxFutureUnit`
   - queued owned future
   - clone queue owner 后 `tokio::spawn`
5. `codex-rs/core/src/tools/registry.rs`
   - `wait_until_ready<'a>`
   - shared lifetime of runtime/session/future
6. `codex-rs/core/src/tools/handlers/mcp.rs`
   - async block 借用 `self` 与 `session`
7. `codex-rs/core/src/tools/parallel.rs`
   - Spawn 前 Arc/owned captures
   - Spawn 内部产生短 readiness borrow
8. `codex-rs/core/src/context/world_state/mod.rs`
   - `WorldStateSection: Send + Sync + 'static`
   - `ID: &'static str`
   - `Snapshot: DeserializeOwned + Serialize`
9. `codex-rs/core/src/config/permissions.rs`
   - `Cow<'_, Path>` 的 borrowed/owned return
10. `codex-rs/core/src/client_tests.rs`
    - `for<'a> LookupSpan<'a>` 的 HRTB 例子

## 129. 动手练习一：画 owner 图

从 `tools/parallel.rs` 任选一次 spawn，列出 async block 捕获的每个变量：

| 变量 | 捕获形式 | 深拷贝/共享/移动 | 为什么满足 `'static` |
|---|---|---|---|
| session | `Arc<Session>` | shared owner | Arc 被 move 入 task |
| call | owned/clone | 独立 value | 无短 borrow |
| runtime | Arc/owned handle | shared owner | owner 在 task state 中 |

再标出 task 内创建的短 borrows 在哪个 await 后结束。

## 130. 动手练习二：制造四类编译错误

在临时教学 crate 中分别制造：

1. use-after-move；
2. mutable/shared borrow overlap；
3. local reference return；
4. non-`'static` reference 进入 spawn。

对每条 diagnostic 写一句“owner A 可能早于 consumer B 结束”的翻译，再修复。

## 131. 动手练习三：制造 non-Send Future

让 `Rc<T>` 或一个不适合跨线程的 guard 活过 `.await`，尝试 `tokio::spawn`。然后：

1. 用 inner scope 让它在 await 前 drop；
2. 若语义确需共享，改为合适的 Arc/synchronization；
3. 比较两种修复是否改变业务并发语义。

## 132. 动手练习四：Borrowed 与 Static Future

分别实现：

```rust
fn borrowed<'a>(&'a self) -> BoxFuture<'a, usize>;
fn owned(self: Arc<Self>) -> BoxFuture<'static, usize>;
```

尝试直接 await、存入 Vec、送入 channel 和 spawn，记录 compiler 对每种组合的接受情况。

## 133. 动手练习五：读 `SessionTask` adapter

逐行回答：

1. 为什么实现者侧返回 `impl Future`？
2. 为什么 orchestrator 侧需要 boxed erased future？
3. `run` 怎样得到 `'static`？
4. `abort` 为什么保留 `'a`？
5. `Box::pin` 在哪一个边界付出 allocation/dynamic dispatch 成本？

## 134. 理解检查

你应该能不看答案解释：

1. Lifetime 为什么是关系而不是运行时间？
2. `T: 'static` 与 `&'static T` 有什么区别？
3. `async move` 为什么仍可能捕获短 reference？
4. 直接 await 借用 future 与 spawn 它有什么不同？
5. `Send` 和 `Sync` 分别回答什么问题？
6. 为什么 `Arc<T>` 不自动让 T 的 mutation 安全？
7. Future 是否 Send 为什么取决于跨 await 的 locals？
8. `BoxFuture<'a, T>` 的每一层是什么？
9. `SessionTask::run` 为什么使用 `self: Arc<Self>`？
10. `run` 和 `abort` 的 future lifetime 为什么不同？
11. Queue 保存 future 为什么要求 `'static`？
12. 为什么最佳结构常是“边界上传 owner，内部短借用”？

## 135. 本章小结

- Lifetime 是 compiler 对 reference 有效范围的关系证明，不是 runtime timer。
- Borrow 不取得 ownership；shared 与 mutable borrows 受冲突规则约束。
- `T: 'static` 表示不含短借用，不表示 value 永远不 drop。
- Spawned task 可能超出调用 stack，因此通常要求 future `Send + 'static`。
- `async move` 移动 captured value；若 value 本身是 reference，短 lifetime 仍保留。
- `Send` 管 ownership 跨线程移动，`Sync` 管 shared reference 跨线程使用。
- Future 是否 Send 取决于 suspension point 保存的所有 state。
- 借用 Future 适合直接 await；owned/Arc Future 适合 queue 与 spawn。
- Codex 用 RPITIT 给实现者静态 future，用 boxed adapter 给 orchestrator 动态统一类型。
- `SessionTask::run` 通过 owned Arc inputs 形成 static background future；`abort(&self)` 保留 borrowed lifetime。
- Arc clone 解决 ownership 独立性，但也会延长 runtime object lifetime。
- Borrow checker error 应翻译成 owner、consumer 与 scope 的关系，再选择 borrow、owned clone、Arc、缩小 scope或不 spawn。

## 136. 本章词汇表

| 英文/代码词 | 字面意思 | 在本章中的具体含义 |
|---|---|---|
| Ownership / owner | 所有权/所有者 | 决定 value/resource 最终何时 drop 的唯一或共享权利 |
| Borrow / borrower | 借用/借用者 | 临时通过 reference 使用 owner data，不取得释放责任 |
| Reference / referent | 引用/被引用对象 | `&T` handle 与它指向的 T |
| Shared reference | 共享引用 | `&T`，可多方只读借用 |
| Mutable reference | 可变引用 | `&mut T`，一定范围内独占访问 |
| Lifetime | 生命周期参数 | Reference 有效范围之间的编译期关系 |
| Outlives / `'a: 'b` | 活得更久 | `'a` 至少覆盖 `'b` 所要求的范围 |
| Lifetime elision | 生命周期省略 | Compiler 按规则补出未显式书写的 lifetime 关系 |
| NLL | 非词法生命周期 | Borrow 可在最后使用点结束，不必等整个大括号 scope |
| Reborrow | 再借用 | 从已有 reference 建立更短临时 reference |
| Move | 移动 | 转移 ownership，旧 binding 通常不可再用 |
| `Copy` / `Clone` | 隐式位复制/显式克隆 | 小值复制，以及由 type 自定义的新 ownership 语义 |
| Deref coercion | 解引用强制转换 | Compiler 将 `&String` 等适配为 `&str` 等目标 view |
| `Cow<'a, T>` | 写时克隆 | 同一 type 可持 borrowed 或 owned representation |
| `'static` bound | 静态生命周期约束 | Type/future 不依赖短期借用，可独立存活 |
| `&'static T` | 静态引用 | 具体 reference 在整个程序期间有效 |
| `T: 'static` | 静态类型约束 | T 内部不包含较短 lifetime reference，但 value 可随时 drop |
| `Send` | 可发送 | Ownership 可安全移到另一 OS thread |
| `Sync` | 可同步共享 | `&T` 可安全被多个 threads 使用 |
| Auto trait | 自动 trait | Compiler 根据组成 fields 自动推导 Send/Sync 等性质 |
| `Rc<T>` / `Arc<T>` | 单线程/原子引用计数 | 单线程与跨线程 shared ownership handles |
| `RefCell<T>` / `Mutex<T>` | 动态借用盒/互斥锁 | 单线程 runtime borrow check 与线程安全排他访问 |
| Interior mutability | 内部可变性 | 通过 shared outer reference 在受控机制中修改内部状态 |
| Strong / Weak owner | 强/弱 owner | 延长 referent 存活的 Arc 与不延长存活的 Weak |
| Future state machine | Future 状态机 | 保存跨 await locals、captures 和恢复位置的 compiler-generated value |
| Suspension point | 挂起点 | `.await` 处 task 可能暂停并稍后恢复的位置 |
| `async move` | 移动式异步块 | 按 value 捕获外部 bindings 的 future |
| `tokio::spawn` | 生成 Tokio 任务 | 启动通常要求 `Future + Send + 'static` 的独立 task |
| `spawn_local` | 本地任务 | 在 LocalSet 上运行 non-Send future 的显式单线程边界 |
| `spawn_blocking` | 阻塞任务 | 把 owned Send closure 放到 blocking thread pool |
| `JoinHandle` / `JoinSet` | 等待句柄/任务集合 | 观察 task result、panic、abort 与 drain 的 owner |
| Detached task | 分离任务 | 不再由 caller 持有/等待 JoinHandle 的后台 task |
| Scoped concurrency | 有作用域并发 | 保证 child 在 parent scope 结束前完成，从而允许受控借用 |
| Structured concurrency | 结构化并发 | Parent 明确拥有、等待和取消 child tasks 的设计 |
| `Pin` | 固定 | 限制被 poll future 的 storage 不再随意移动 |
| `BoxFuture<'a, T>` | 盒装 Future | `Pin<Box<dyn Future<Output=T> + Send + 'a>>` |
| `impl Future` | 不透明具体 Future | Compiler 已知 concrete type 的静态分派返回值 |
| `dyn Future` | 动态 Future | 通过 trait object 擦除 concrete future type |
| RPITIT | Trait 返回位置 impl Trait | Trait method 直接声明 `impl Future + Send` 的技术 |
| Type erasure | 类型擦除 | 用 trait object/boxed future 统一不同 concrete types |
| Object safety | 对象安全 | Trait 是否能形成 `dyn Trait` 的语言规则集合 |
| Blanket implementation | 覆盖式实现 | 对所有满足 bound 的 T 统一实现 adapter trait |
| HRTB / `for<'a>` | 高阶生命周期约束 | 对任意调用者选择的 lifetime 都满足 trait |
| `DeserializeOwned` | 可拥有式反序列化 | Decode 结果不借用某个特定输入 buffer |
| Associated type | 关联类型 | Trait implementation 自己指定、受 bounds 约束的内部类型 |
| Generic bound | 泛型约束 | `T: Send + Sync` 等调用和实现共同依赖的 contract |
| Variance | 型变 | Generic/lifetime container 是否允许长短 lifetime 的安全替换 |
| Invariant | 不变 | 不能沿 lifetime/type 子关系自由转换的严格 container 性质 |
| Borrowed data escapes | 借用数据逃逸 | 短 reference 被存入可能更长寿的 task/field/callback |
| Future is not Send | Future 不可发送 | 跨 await state 含 non-Send value，不能进 multithread spawn |
| Lock guard | 锁守卫 | Scope/Drop 决定何时释放 lock 的 RAII value |
| Critical section | 临界区 | 持锁并独占/保护 shared state 的代码范围 |
| Deep clone / handle clone | 深克隆/句柄克隆 | 复制 payload 与只复制 Arc/Bytes 等共享 owner handle |

