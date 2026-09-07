# 异步任务、Channel 与取消：一次“停止生成”到底发生了什么

> 本章解决的问题：当 Codex 一边接收模型输出、一边执行工具、一边向界面发送事件时，这些工作怎样同时推进？用户按下“停止”以后，又怎样让它们有秩序地停下来？

阅读本章前，建议先看：

- [00：概念地图与贯穿示例](00-concepts-and-running-example.md)
- [22：错误、取消与恢复](22-errors-cancellation-and-recovery.md)
- [26：Codex 源码中的常用 Rust 模式](26-rust-patterns-used-by-codex.md)

---

## 1. 先用一个具体场景理解异步

假设你让 Codex：

> 搜索项目中的配置读取逻辑，修改代码，然后运行测试。

执行过程中可能同时存在这些工作：

1. 主循环等待新的用户指令；
2. 网络任务等待模型返回下一段内容；
3. 工具任务正在搜索文件或运行命令；
4. 事件转发任务把增量输出送给界面；
5. 界面仍要响应你点击“停止”；
6. 持久化逻辑准备把本轮记录写入 rollout。

如果所有工作都按“一个结束后才能做下一个”的方式运行，那么只要模型请求或命令执行很慢，界面就会像卡死一样，连停止按钮都无法及时响应。

异步编程要解决的核心问题不是“让每件事都更快”，而是：

> 某项工作正在等待时，把执行机会让给其他工作。

例如，网络请求等待下一批字节时，CPU 不需要干等，可以转去处理用户的中断请求。

---

## 2. 操作系统线程、Tokio task 和 Future 不是一回事

初学异步 Rust 时，最容易把下面三个概念混在一起。

### 2.1 操作系统线程

线程由操作系统调度，拥有自己的调用栈。创建大量线程通常比创建大量异步任务更昂贵。

### 2.2 Tokio task

Tokio task 是由 Tokio 运行时调度的轻量任务。很多 task 可以共享少量操作系统线程。

可以把它想象成：

```text
操作系统线程
    └── Tokio executor
          ├── task A：等待模型响应
          ├── task B：转发事件
          ├── task C：等待用户操作
          └── task D：记录运行状态
```

这里的 executor 是“任务调度员”。哪个任务现在能继续，它就去推进哪个任务。

### 2.3 Future

Future 表示“一项可能还没有完成的计算”。

```rust
async fn load_data() -> Data {
    // ...
}
```

调用 `load_data()` 时，通常不是马上从头执行到尾，而是先得到一个 Future。Future 被 `.await`、被 spawn，或由其他方式交给 executor 后，才会被不断推进。

一句话区分：

- Future：尚未完成的计算；
- task：正在由运行时管理和推进的 Future；
- thread：实际执行机器指令的操作系统资源。

---

## 3. `.await` 不是“卡住整个程序”

看到：

```rust
let event = receiver.recv().await;
```

可以先翻译成：

> 如果现在有事件，就继续；如果还没有，就暂时让出执行机会，等事件到达后再回来。

它不是说“让整个进程停在这里”。通常只是当前 task 暂停，executor 仍可以推进其他 task。

Future 在被推进时，会被 executor 调用 `poll`：

- `Poll::Ready(value)`：已经完成，结果是 `value`；
- `Poll::Pending`：暂时不能继续，之后准备好时请再来检查。

为了避免 executor 不停地空转检查，Future 会通过 Waker 告诉运行时：“我现在有进展了，可以再 poll 我。”

阅读业务代码时，你一般不需要手写 `poll` 或 Waker，但知道这个模型后，`.await` 就不再像魔法。

---

## 4. `tokio::spawn`：让一项工作独立推进

Codex 启动一个 task 时，常见结构可以简化为：

```rust
let session = Arc::clone(&session);
let cancellation_token = cancellation_token.child_token();

let handle = tokio::spawn(async move {
    run_task(session, cancellation_token).await;
});
```

逐句理解：

1. `Arc::clone(&session)`：为新 task 创建一个共享所有权句柄；
2. `child_token()`：给新 task 一个属于这棵取消树的子令牌；
3. `async move`：把 task 需要的数据所有权移进去；
4. `tokio::spawn(...)`：交给 Tokio 独立调度；
5. 返回的 handle：以后可用于等待或终止该 task。

为什么经常要 `move` 和 `Arc`？

因为 spawn 出来的 task 可能比当前函数活得更久。它不能借用一个马上就要离开作用域的局部变量，因此通常要拥有所需数据，或拥有指向共享数据的 `Arc`。

### 4.1 JoinHandle、AbortOnDropHandle 与“放任不管”

- `JoinHandle`：可以等待 task 结束并取得结果；
- abort：强制停止 task 的继续执行；
- `AbortOnDropHandle`：句柄离开作用域时会自动 abort，防止后台任务意外失去管理；
- detached task：句柄被丢弃后，任务仍自行运行，容易成为无人负责的“孤儿任务”。

并不是每个后台任务都必须等待，但应该明确它由谁管理、怎样停止、关闭时是否允许继续。

---

## 5. Channel：任务之间传递消息的管道

共享内存加锁适合“读取或修改同一份状态”，channel 更适合表达：

> 我产生了一条消息，请另一个任务处理。

Codex 使用了多种 channel，因为“消息怎样被消费”并不总是相同。

---

## 6. `mpsc`：多个发送者，一个接收者

`mpsc` 是 multiple producer, single consumer 的缩写。

```text
界面提交 ─────┐
内部操作 ─────┼──> submission queue ──> session loop
恢复操作 ─────┘
```

它适合这种语义：

- 可以有多个地方产生 Submission；
- 由一个 session loop 按顺序处理；
- 每条消息通常只被处理一次。

`SessionIo` 中的 submission sender 和 event receiver，就能帮助调用方与 session 主循环交换消息。

### 6.1 有界队列与背压

如果 channel 容量是 64，而消费者一时处理不过来，队列最终会满。这时：

```rust
sender.send(message).await
```

可能需要等待空间。

这种让生产者慢下来的机制叫背压。它是在告诉上游：

> 下游已经忙不过来了，请不要无限制地继续堆消息。

没有容量上限的队列看似方便，但慢消费者可能导致内存持续增长。对模型增量、工具输出等可能很多的数据，边界尤其重要。

### 6.2 一个危险组合：持锁时等待发送

想象下面的逻辑：

```rust
let mut state = state.lock().await;
sender.send(message).await;
```

如果 channel 已满，发送方在持锁时等待；而接收方恰好需要同一把锁才能消费消息，就可能互相等待。

通常更安全的思路是：

1. 在锁内只完成必要的状态读取或修改；
2. 构造好要发送的消息；
3. 释放锁；
4. 再执行可能等待的 `send().await`。

这不是绝对规则，但看到“锁守卫跨越 `.await`”时，应当停下来检查。

---

## 7. `oneshot`：只返回一次结果

`oneshot` channel 只有一个发送者和一个接收者，并且只传递一个值。

它很像给一条请求附带一个一次性回复地址：

```text
任务 A --请求 + reply_sender--> 任务 B
任务 A <--一次性结果----------- 任务 B
```

适合的场景包括：

- 等待一次审批结果；
- 等待初始化完成；
- 请求某项操作并等待一次答复；
- 等待一条记录确认已经写入。

如果 sender 在发送前被丢弃，receiver 会得到 channel 已关闭的错误。这通常意味着负责回答的一方提前结束了，调用方需要把它当成错误或取消处理，而不能永久等待。

---

## 8. `watch`：我只关心“最新状态”

`watch` channel 保存一个当前值。接收者关心的是最新状态，而不是每一次变化的完整历史。

例如 Agent 状态可能依次变化：

```text
Idle → Running → WaitingForApproval → Running → Idle
```

一个较晚开始观察的界面，通常最需要知道“现在是什么状态”，不一定要补收此前每一步。因此 `watch` 很合适。

不要把 `watch` 当事件日志：如果状态从 A 很快变成 B、再变成 C，慢接收者可能只看到最新的 C。

选择时可以问：

- 我必须处理每一条消息吗？用队列或事件流；
- 我只需要随时读取最新版吗？考虑 `watch`。

---

## 9. `broadcast`：一条消息发给所有订阅者

`broadcast` 适合“一件事发生后，多个独立观察者都应该知道”。

例如新 thread 创建后：

```text
                    ┌──> 观察者 A
thread created ─────┼──> 观察者 B
                    └──> 观察者 C
```

与 `mpsc` 不同，它不是让多个消费者争抢同一条消息，而是每个订阅者都能收到自己的副本。

如果某个订阅者落后太多，旧消息可能已被环形缓冲区覆盖。源码中遇到 lagged 一类错误时，它表达的不是“发送失败”，而是：

> 这个订阅者消费得太慢，已经漏掉一部分广播消息。

---

## 10. `Notify`：只负责“叫醒”，不携带业务数据

`Notify` 可以理解成一个异步门铃：

```rust
done.notified().await;
```

表示等待“完成通知”；另一侧调用 `notify_waiters()` 或相关方法叫醒等待者。

它与 channel 的差别是：

- channel 通常传递消息或状态；
- `Notify` 主要表达“某件事现在可以继续了”。

Codex 的运行任务管理中，会用完成通知配合取消逻辑：先请求任务合作退出，再等待它报告已经结束。

注意：`Notify` 不是完整事件历史。若业务需要可靠记录每一次事件，不应仅靠它代替消息队列。

---

## 11. `tokio::select!`：同时等待多个可能先完成的事情

下面是一个简化例子：

```rust
tokio::select! {
    _ = cancellation_token.cancelled() => {
        handle_cancel().await;
    }
    event = receiver.recv() => {
        handle_event(event).await;
    }
}
```

它的意思不是启动两个操作系统线程，而是：

> 同时关注这两个 Future，哪个先准备好，就执行哪个分支。

Codex 的事件转发逻辑会采用类似结构，在等待子任务事件的同时，也监听取消信号。否则子任务没有新事件时，转发者可能永远卡在 `next_event().await`，不能及时停止。

### 11.1 没赢的分支发生了什么

一个分支胜出后，其他分支在本次 `select!` 中创建的 Future 通常会被丢弃。这带来“取消安全”问题：

- Future 被中途丢弃后，下次重新创建并等待，会不会漏数据？
- 它是否已经做了一半不可恢复的状态修改？
- 被丢弃是否会遗留锁、资源或半条协议消息？

因此不能机械地把任意 `.await` 塞进 `select!`。需要理解相应异步操作是否 cancellation-safe。

---

## 12. CancellationToken：取消是一条信号，不是瞬间抹除

`CancellationToken` 提供一种可共享的取消信号：

```rust
tokio::select! {
    _ = token.cancelled() => return,
    result = do_work() => handle(result),
}
```

调用另一端的：

```rust
token.cancel();
```

只是把取消状态设为已触发，并叫醒监听者。真正停止工作仍依赖各 task：

1. 在合适的位置监听 token；
2. 收到信号后退出循环或返回；
3. 必要时清理子进程、临时状态和资源；
4. 发出一致的终止事件。

这叫协作式取消。它不是操作系统替你自动撤销全部副作用。

### 12.1 clone 与 child token

- clone：多个组件观察同一个取消状态；
- child token：建立父子关系，父 token 取消时子 token 也会取消；
- 子 token 取消通常不代表反向取消父 token。

它适合表达结构化关系：

```text
本轮 turn token
    ├── 模型流 task token
    ├── 工具执行 task token
    └── 事件转发 task token
```

停止整轮时，取消父 token；只停止一个局部工作时，可以取消对应子 token，而不必让整个 session 都退出。

---

## 13. 用户按下“停止”后的完整时间线

结合 `codex-rs/core/src/tasks/mod.rs`，可以把一次中断理解为下面九步。

```text
用户点击停止
      │
      ▼
找到当前 RunningTask
      │
      ▼
触发 CancellationToken
      │
      ├──> 模型/工具/转发逻辑在安全点观察到取消
      │
      ▼
等待任务发出 done 通知 ─────┐
      │                      │ tokio::select!
      └── 等待宽限期超时 ────┘
                 │
       ┌─────────┴─────────┐
       ▼                   ▼
 任务已合作退出        仍未退出，abort handle
       └─────────┬─────────┘
                 ▼
       执行任务类型相关的中止收尾
                 ▼
       写入 interrupted 历史标记
                 ▼
             flush rollout
                 ▼
          向界面发 TurnAborted
```

逐步解释：

1. session 收到中断操作；
2. 找到当前仍在运行的 `RunningTask`；
3. 调用 token 的 `cancel()`；
4. 各子流程在自己的等待点或循环中观察到取消；
5. 管理者用 `select!` 等待“完成通知”和“宽限期超时”哪个先发生；
6. 如果任务不合作或卡住，才使用 handle 强制 abort；
7. 执行任务类型需要的额外中止逻辑；
8. 把 interrupted 标记写入历史并 flush，避免恢复时误以为上一轮正常完成；
9. 发出 `TurnAborted`，让界面进入正确状态。

这套设计同时追求两件事：

- 尽量给任务机会正常清理；
- 又不能让“停止”无限期等待。

---

## 14. 为什么不能只调用 `abort()`

直接 abort 看起来最省事，但 Future 被丢弃不等于它做过的事情被撤销。

它此前可能已经：

- 创建或修改了文件；
- 启动了一个外部子进程；
- 向界面发送了部分增量；
- 写入了部分 rollout；
- 向外部服务发送了请求。

强制停止只能阻止这段 Rust Future 继续运行，不能让外部世界自动回到原样。

所以更稳妥的次序是：

1. 先发出协作式取消；
2. 给任务一个有限的清理时间；
3. 超时后再强制终止本地 task；
4. 管理外部资源和持久化状态；
5. 对外只发出一致的最终状态。

---

## 15. 取消发生在不同阶段，处理重点不同

### 15.1 正在等待模型流

应停止继续消费响应，并确保不会在取消后又把晚到的内容当成本轮正常完成。

### 15.2 正在等待用户审批

审批等待本身也必须能被取消。否则用户已经停止 turn，系统却仍卡在一个无人再处理的批准对话上。

### 15.3 正在执行命令

停止 Rust task 不一定会停止已经创建的操作系统子进程。执行层需要负责信号传递、终止进程组或其他平台相关清理。

### 15.4 正在执行 MCP 工具

本地等待可以被取消，但远端是否真的终止，取决于协议、连接和服务端能力。调用方仍要正确处理迟到响应和连接状态。

### 15.5 正在写入历史

不能留下“内容写了一半，但终态没有记录”的模糊状态。中断标记和 flush 让之后的恢复逻辑能理解刚才发生了什么。

---

## 16. 超时和取消不是同一种原因

两者都会让工作提前结束，但含义不同：

- 取消：用户或上层明确表示“不再需要”；
- 超时：在允许时间内没有完成；
- 错误：工作尝试过，但遇到了失败。

一个等待逻辑可能同时监听三件事：

```rust
tokio::select! {
    _ = token.cancelled() => Outcome::Cancelled,
    _ = tokio::time::sleep(timeout) => Outcome::TimedOut,
    result = do_work() => Outcome::Finished(result),
}
```

不要把它们全都压成一个字符串错误，因为恢复策略不同：

- 用户取消通常不该自动重试；
- 网络超时可能允许有限重试；
- 参数错误即使重试也不会变好。

---

## 17. 结构化并发：谁创建任务，谁负责它的结束

结构化并发不是某一个语法，而是一条设计原则：

> 子任务的生命周期应当处在清晰的父级范围内，父级知道如何等待、取消和收尾。

对 Codex 一轮 turn，可以这样检查：

- 哪个对象拥有取消 token？
- 哪个对象保存 task handle？
- 正常完成时谁等待它？
- 用户中断时谁取消它？
- session 关闭时谁确保它不再运行？
- task 失败时错误被送到哪里？

如果这些问题没人回答，就容易产生：

- session 已结束但后台 task 仍运行；
- task panic 后没有任何可见事件；
- sender 永远不释放，receiver 永远等不到关闭；
- 同一个 turn 同时发出 completed 和 aborted 两个终态。

---

## 18. Channel 关闭本身也是一种控制信号

当所有 sender 都被丢弃时，receiver 通常会观察到 channel 已关闭。

这常被用作自然的关闭机制：

```text
所有 Submission sender 被释放
             │
             ▼
session loop 的 recv() 返回 None
             │
             ▼
主循环知道不会再有新操作，可以结束
```

因此，`Arc` 中无意保留一个 sender clone，可能导致接收循环一直无法结束。排查“程序为什么不退出”时，应该问：

> 是不是还有某个发送端活着？

---

## 19. 常见误解与源码阅读陷阱

### 误解一：`async fn` 会自动并行执行

不会。它产生 Future。只有被 `.await`、spawn 或以其他方式驱动，工作才会推进；并发也不必然等于多核并行。

### 误解二：`.await` 会阻塞整个线程

通常它会暂停当前 task，并把机会交还 executor。真正的阻塞调用若直接放入异步 task，才可能卡住运行时线程。

### 误解三：调用 `cancel()` 后代码立刻消失

不会。它只是发出信号，任务要在协作点观察并退出。

### 误解四：drop Future 会自动回滚副作用

不会。已经写入文件、发出的请求和启动的进程不会自动撤销。

### 误解五：`watch` 能保存所有状态变化

它强调最新值，不是完整事件日志。

### 误解六：无限 channel 最不容易死锁，所以最安全

它把“上游过快”的问题变成不受控的内存增长。边界和背压往往是系统稳定性的一部分。

### 误解七：只要收到 `TurnAborted`，所有外部工作一定已经停止

本地状态已经进入 aborted，并不自动证明所有远端服务或操作系统进程都能被瞬间撤销。要继续检查各执行后端的终止语义。

---

## 20. 怎样阅读一个异步函数

遇到很长的异步函数，不要从第一行一路读到底。按下面顺序标记：

### 第一步：找输入、输出和所有权

- 参数中有没有 `Arc`？
- 有没有 `CancellationToken`？
- 返回 `Result`、事件，还是一个 handle？

### 第二步：圈出所有 `.await`

每个 `.await` 都是潜在的暂停点。问：暂停时持有哪些锁、借用和半完成状态？

### 第三步：找 spawn

每个 spawn 都问：谁拥有 handle？谁取消？错误去哪？

### 第四步：识别 channel 类型

- `mpsc`：每条消息由一个消费者处理；
- `oneshot`：一次答复；
- `watch`：最新状态；
- `broadcast`：所有订阅者；
- `Notify`：只唤醒。

### 第五步：找循环的退出条件

- channel 关闭？
- token 取消？
- 收到终态事件？
- 超时？
- 错误返回？

### 第六步：找唯一终态

确认正常完成、错误和取消不会重复发出互相冲突的终态。

---

## 21. 如何测试异步与取消逻辑

异步测试最怕依赖“睡 100 毫秒，希望另一边已经运行”。这种测试在机器繁忙时很容易偶发失败。

更可靠的方法是用明确的同步点：

- `oneshot`：测试任务通知“我已经走到这里”；
- `Notify`：放行一个被刻意暂停的任务；
- 模拟响应：精确控制模型事件何时到达；
- 保存 handle 或 mock：断言请求、取消和终态事件；
- bounded channel：有意制造背压，验证不会死锁。

针对一次中断，至少要确认：

1. 运行中的工作观察到取消；
2. 必要的清理逻辑被调用；
3. 历史中留下 interrupted 信息；
4. 对外发出 `TurnAborted`；
5. 不再发出同一 turn 的正常完成事件；
6. session 之后还能接受下一轮输入。

如果涉及远端 executor 或子进程，还要分别验证本地 task 停止和远端工作停止，不能假设两者天然等价。

---

## 22. 本章名词表

完整总表见 [课程术语表](glossary.md)。下面列出本章最需要掌握的词。

| 名词 | 代码中的常见写法 | 通俗解释 |
|---|---|---|
| 异步 | `async` / `.await` | 工作等待时可以让出执行机会，而不是占住线程干等 |
| Future | `Future<Output = T>` | 一项可能尚未完成、以后会产生 `T` 的计算 |
| task | `tokio::spawn(...)` | 交给异步运行时管理和推进的工作单元 |
| executor | Tokio runtime | 反复推进已准备好 task 的调度器 |
| poll | `Poll::Ready` / `Pending` | executor 询问 Future 现在能否继续或完成 |
| Waker | `Waker` | Future 准备好后用于叫醒 executor 的机制 |
| channel | `mpsc`、`oneshot` 等 | 异步任务之间传递消息的管道 |
| `mpsc` | `mpsc::Sender<T>` | 多个发送者、一个接收者，每条消息通常只处理一次 |
| `oneshot` | `oneshot::Sender<T>` | 只传一个结果的一次性回复通道 |
| `watch` | `watch::Receiver<T>` | 保存并观察最新状态，不保证看到全部变化 |
| `broadcast` | `broadcast::Sender<T>` | 把同一事件发送给所有订阅者 |
| `Notify` | `Notify::notified()` | 不携带业务数据的异步唤醒信号 |
| 背压 | backpressure | 下游忙不过来时，让上游减慢速度 |
| `select!` | `tokio::select!` | 同时等待多个 Future，先完成者进入对应分支 |
| 取消安全 | cancellation safety | Future 中途被丢弃后，不会破坏协议或丢失关键状态 |
| 协作式取消 | cooperative cancellation | 任务主动观察取消信号并自行收尾退出 |
| 宽限期 | grace period | 发出取消后，留给任务正常清理的一小段时间 |
| graceful shutdown | 优雅关闭 | 停止接收新工作、等待或取消现有工作、释放资源后退出 |
| JoinHandle | `JoinHandle<T>` | 用于等待、观察或终止已 spawn task 的句柄 |
| detached task | 丢弃 handle 后仍运行 | 没有明确父级等待的后台任务 |
| child token | `token.child_token()` | 父级取消会向下传播的子取消令牌 |
| terminal event | `TurnComplete` / `TurnAborted` | 表示一轮已经进入最终状态的事件 |

### 代码单词和短语拆解

- `spawn`：生成、启动；在这里是启动异步 task。
- `join`：汇合；等待另一个 task 结束再继续。
- `abort`：中止；强制阻止 task 继续推进。
- `notify`：通知、叫醒。
- `sender` / `receiver`：发送端 / 接收端。
- `pending`：尚未准备好。
- `ready`：已经准备好或已经完成。
- `poll`：轮询、询问当前进展。
- `lagged`：落后；广播接收者没跟上消息速度。
- `bounded` / `unbounded`：有容量上限 / 没有固定容量上限。
- `graceful`：有秩序、留出清理机会的。
- `detached`：脱离父级管理的。
- `interrupted`：被中断的，不等同于正常完成。

---

## 23. 自测题

1. 为什么“异步”不等于“每项工作都在不同线程并行运行”？
2. `.await` 时，暂停的是整个程序还是当前 task？
3. 为什么 spawn 的闭包经常写成 `async move`？
4. submission queue 为什么适合使用 `mpsc`？
5. Agent 当前状态为什么适合使用 `watch`，但操作历史不适合？
6. `Notify` 与 `oneshot` 最大的语义差别是什么？
7. bounded channel 如何形成背压？
8. 为什么持锁跨越 `send().await` 可能危险？
9. `CancellationToken::cancel()` 为什么不是强制回滚？
10. 为什么 Codex 先协作式取消，等待宽限期后才 abort？
11. `select!` 未选中的 Future 会怎样？为什么要考虑取消安全？
12. 为什么同一 turn 不应同时出现 `TurnAborted` 和正常完成终态？

如果你能不看正文回答这些问题，就已经具备阅读 Codex 异步主链路的基础。

---

## 24. 源码检查点

建议按以下顺序对照源码：

1. `codex-rs/core/src/session/mod.rs`
   - 找 `SessionIo`；
   - 观察 Submission sender、Event receiver 和 AgentStatus watch receiver；
   - 思考为什么三者的消息语义不同。
2. `codex-rs/core/src/tasks/mod.rs`
   - 找 `CancellationToken`、`Notify`、`tokio::spawn` 和 `RunningTask`；
   - 沿着中断逻辑观察“先 cancel、再等待、最后 abort”的顺序。
3. `codex-rs/core/src/codex_delegate.rs`
   - 找 `forward_events`；
   - 看它如何用 `tokio::select!` 同时等待取消与下一个事件。
4. `codex-rs/core/src/exec.rs`
   - 查找 `cancelled()` 和 `tokio::select!`；
   - 观察执行过程怎样响应取消。
5. `codex-rs/core/src/thread_manager.rs`
   - 查找 `broadcast`；
   - 思考 thread-created 为什么是“一对多通知”。
6. `codex-rs/core/src/state/turn.rs` 和 `codex-rs/core/src/session/mod.rs`
   - 查找 `pending_approvals`、`tx_approve` 和 `oneshot`；
   - 观察审批请求怎样保存一次性 sender，以及请求方怎样等待一次结果。

阅读时不要只记 API。每遇到一种并发原语，都问它表达的是哪种业务关系：队列、一次答复、最新状态、广播，还是单纯唤醒。

---

## 25. 一句话总结

Codex 的异步系统可以理解为：多个轻量 task 通过不同语义的 channel 协作，用有界队列控制压力，用 `select!` 同时等待事件和停止信号，并通过“协作取消—限时等待—必要时强制终止—记录一致终态”把一次中断安全地收回来。
