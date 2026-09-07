# 源码精读 26：Connection teardown、resource ownership 与有界排空

> 源码基线：`4ee41929eaf4`。本文只描述该固定提交中的实现。以后源码移动时，请搜索符号名，不要依赖行号。

上一篇讲到：connection 关闭后，request context 最终必须被清掉。

但一个 App-server connection 拥有的远不止 request context。它还可能拥有：

- 尚未执行或正在执行的 RPC handler；
- outbound writer 与等待写出的消息；
- filesystem watches；
- `command/exec` 或 `process/*` 启动的子进程；
- thread subscriptions 与 attestation capability；
- 等待最终 response 的 delayed requests；
- 正在运行、但生命周期长于 connection 的 Core threads。

如果把“断开连接”简单写成删除一个 map entry，可能出现：

- queued handler 在断开后才开始产生副作用；
- 已开始 handler 还没回包，trace context 已被提前删除；
- 文件监听继续向不存在的客户端发通知；
- PTY/进程失去 owner 后继续运行；
- 已断开的 connection 又被自动订阅回 thread；
- shutdown 永远等待一个不会结束的 task；
- 为了快速退出而直接 abort，导致可持久化状态没有完成收尾。

本文要解决的核心问题是：

> Codex 怎样把 teardown 拆成“先阻止新工作、再给已开始工作一个有界收尾机会、最后按资源 owner 清理”的多阶段协议？单连接断开、优雅进程重启、强制退出和 in-process shutdown 为什么不是同一条路径？

---

## 1. 先给最重要的结论

固定提交中的单连接 teardown 可以概括成：

```text
physical reader/writer stops
  → emit ConnectionClosed
  → processor removes connection from live map
  → close per-connection RPC admission gate
  → outbound router removes writer
  → spawn independent cleanup task
  → wait already-started RPCs, at most 30s
  → purge request contexts
  → drop filesystem watches
  → terminate command/exec processes
  → kill process/* processes
  → remove thread connection/subscription indexes
```

全局优雅 shutdown 则是另一条更大的链：

```text
first signal
  → while assistant turns remain: keep accepting requests
  → when running turn count becomes zero
  → stop acceptors + request outbound disconnect-all
  → wait connection RPC gates
  → drain connection cleanup tasks
  → drain App-server background tasks, bounded
  → shut down all Core threads, bounded
  → processor drops outgoing senders
  → outbound router exits when channel closes
  → await telemetry reloader and transport accept tasks
```

最值得记住的五句话：

> teardown 是协议，不是一个 `drop(connection)` 动作。

> `close` 负责停止接纳，`shutdown` 负责停止接纳并等待已开始工作。

> 单连接断开通常只移除该 connection 的 ownership，不会立即关闭共享 Core thread。

> deadline 到期后的“继续清理”不表示旧任务已停止，只表示 shutdown 不再无限等待它。

> graceful、forced 与 in-process shutdown 有不同承诺，读源码时必须先确认自己走的是哪条入口。

---

## 2. 贯穿全文的两个案例

### 案例 A：两个 WebSocket 客户端中的一个掉线

```text
connection 41
  ├─ request 7 正在 handler 中
  ├─ fs watch: watch-head
  ├─ process handle: terminal-A
  └─ subscribed to thread T

connection 92
  └─ also subscribed to thread T
```

connection 41 突然断开时，期望是：

- 41 不再接收或提交新 RPC；
- 41 的 writer 不再成为路由目标；
- 已经开始的 request 7 获得有限时间自然收尾；
- 41 的 watch 和 process 被清理；
- 41 从 thread T 的订阅集合移除；
- connection 92 的订阅不受影响；
- thread T 和它正在运行的 Turn 通常继续存在。

### 案例 B：WebSocket App-server 收到 SIGTERM

此时有一个 assistant Turn 正在等待 Responses API。

第一次 SIGTERM 的期望不是立即杀死它，而是等待 running assistant Turn 数量变为 0。若又收到第二次可强制信号，则缩短承诺，进入 forced path。

---

## 3. 第一张源码地图

| 层级 | 固定提交路径 | 重点符号 |
|---|---|---|
| physical stdio close | `app-server-transport/src/transport/stdio.rs` | stdin EOF → `ConnectionClosed` |
| physical WebSocket close | `app-server-transport/src/transport/websocket.rs` | inbound/outbound task race、disconnect token |
| transport event | `app-server-transport/src/transport/mod.rs` | `TransportEvent::ConnectionClosed` |
| main connection owner | `app-server/src/lib.rs` | `connections` map、close event branch |
| outbound connection owner | `app-server/src/transport.rs`、`lib.rs` | `OutboundConnectionState`、`OutboundControlEvent` |
| RPC admission/drain | `app-server/src/connection_rpc_gate.rs` | `ConnectionRpcGate` |
| cleanup task owner | `app-server/src/connection_cleanup.rs` | `ConnectionCleanupTasks` |
| per-connection cleanup | `app-server/src/message_processor.rs` | `connection_closed` |
| request context cleanup | `app-server/src/outgoing_message.rs` | `connection_closed` |
| filesystem watch cleanup | `app-server/src/fs_watch.rs` | connection-scoped entries |
| command process cleanup | `app-server/src/command_exec.rs` | `Terminate` control |
| process API cleanup | `app-server/src/request_processors/process_exec_processor.rs` | `Kill` control |
| subscription cleanup | `app-server/src/thread_state.rs` | `remove_connection` |
| App-server background drain | `app-server/src/request_processors/thread_processor.rs` | `TaskTracker`、10s wait |
| Core thread shutdown | `core/src/thread_manager.rs` | `shutdown_all_threads_bounded` |
| process signal shutdown | `app-server/src/lib.rs` | `ShutdownState`、graceful/forced |
| embedded shutdown | `app-server/src/in_process.rs` | explicit staged shutdown、5s task deadlines |

---

## 4. 先分清五个看似相同的动作

| 动作 | 它真正改变什么 |
|---|---|
| close connection | 某个 transport peer 不再参与通信 |
| close gate | 不再让新的/排队的 handler body 开始 |
| drain | 等待已经开始的工作自然结束 |
| cancel/terminate/kill | 主动要求工作停止 |
| abort task | Tokio 直接取消 task 的后续 polling |

这些动作强度不同，也不互相自动包含。

例如 `ConnectionRpcGate::close()` 不会取消已开始 handler；`TaskTracker::wait()` 也不会主动杀死 task；`timeout(wait)` 到期更不等于被等待的 task 已结束。

---

## 5. 什么叫 teardown

`teardown` 可以理解为“拆除运行时结构”。它不只是释放内存，还包括：

- 停止新入口；
- 断开数据生产者与消费者；
- 发出协作式停止信号；
- 等待关键终态；
- 从索引和 registry 中移除 ownership；
- 在 deadline 后选择继续、abort 或记录警告；
- 保证其他连接和共享资源不被误伤。

所以 teardown 的正确性通常由**顺序、作用域和超时策略**共同决定。

---

## 6. physical close 从哪里产生

不同 transport 用不同物理事件判断连接结束。

### stdio

stdin reader 遇到：

- EOF；
- read error；
- forwarding path 要求停止；

就跳出循环并发送：

```rust
TransportEvent::ConnectionClosed { connection_id }
```

### WebSocket

inbound 或 outbound task 只要一个先结束：

```text
winner exits
  → cancel shared disconnect token
  → abort the other task
  → emit ConnectionClosed
```

物理 I/O owner 只报告事实；业务资源清理由上层 processor owner 完成。

---

## 7. WebSocket 的 disconnect token

每条 WebSocket connection 创建一个 `CancellationToken`，同时交给：

- inbound loop；
- outbound loop；
- outbound connection state。

任一方向故障或服务器要求断开时，token 被 cancel，两个 I/O loop 都能观察到。

这比只 drop 某一个 sender 更明确：读和写可能分别阻塞在不同 future 上，需要共享取消信号把两边汇合到同一 connection terminal。

---

## 8. 为什么还要 abort 对侧 task

token 是协作式取消。task 只有再次被 poll 并进入 `select!` 的 cancel branch 才会退出。

`run_websocket_connection` 在一侧已经结束后，还会直接 abort 另一侧 task，缩短 teardown 等待。

这说明实现组合了两种策略：

```text
CancellationToken  → 给两个 loops 一个共同停止原因
JoinHandle::abort   → 确保 loser task 不会无限拖住 connection owner
```

---

## 9. `ConnectionClosed` 是内部事件，不是 JSON-RPC 消息

它属于 `TransportEvent` control plane：

```text
TransportEvent
  ├─ ConnectionOpened
  ├─ ConnectionClosed
  └─ IncomingMessage
```

客户端不会发送一个 JSON-RPC method 叫 `ConnectionClosed`。这是 transport 将 socket/stdio 生命周期事实报告给 processor 的内部消息。

---

## 10. 主 processor 首先从 live map 删除连接

close branch 的第一步：

```rust
let Some(connection_state) = connections.remove(&connection_id) else {
    continue;
};
```

删除具有几个立即效果：

- 后续相同 connection ID 的 incoming request 找不到 owner；
- response/error/notification 也会被视为 unknown connection；
- shutdown 的 connection count 不再包含它；
- 重复 close event 是幂等式 no-op。

先从权威 live map 移除，建立了明确的 generation fence：它从这一刻起不再是活连接。

---

## 11. 为什么要先改权威状态，再慢慢清资源

若先逐项关闭 watch/process/thread，再从 live map 移除：

```text
cleanup takes time
  ↔ main loop may still accept late incoming messages
```

新工作可能在 teardown 中途重新创建刚清掉的资源。

当前实现先撤销“live”资格，然后让 cleanup task 异步做慢工作。这是常见的两阶段删除：

```text
logical removal first
physical/resource cleanup second
```

---

## 12. 第二步：立即关闭 RPC gate

```rust
connection_state.session.rpc_gate.close().await;
```

它完成两件事：

- `accepting=false`；
- `TaskTracker.close()`。

之后进入 gate 的 queued/late future 会被直接 drop，不再 poll handler body。

但已经取得 token 的 handler 继续运行。

---

## 13. `close` 与 `shutdown` 的区别

`ConnectionRpcGate` 的实现可白话化为：

```text
close():
  accepting = false
  close task tracker
  return immediately

shutdown():
  close()
  wait until all acquired tokens are dropped
```

所以 close 是**停止接纳屏障**，shutdown 是**停止接纳 + drain 屏障**。

---

## 14. gate token 表示什么

`run(future)` 在 poll handler body 前：

1. 锁住 `accepting`；
2. 若已关闭，直接 return；
3. 否则从 TaskTracker 取得 token；
4. 执行 future；
5. future 完成后 drop token。

token 表示“这项 handler 工作已经越过准入边界，shutdown 应知道它仍在进行”。

它不是 OS process handle，也不是允许调用某 API 的认证 token。

---

## 15. 为什么检查 accepting 和取得 token 要在同一锁内

若拆开：

```text
task A sees accepting=true
task B closes tracker
task A later acquires token / starts body
```

就可能在 close 之后漏进一项未被正确 tracking 的工作。

当前代码在同一个 mutex 临界区完成“检查 + token 获取”，使 close 和 admission 有单一串行化点。

---

## 16. queued future 被 drop before polling 的意义

serialization queue 可能已经保存了一个 handler future。但 gate 关闭以后调用 `run(future)`：

```text
accepting=false
  → return
  → future body 从未被 poll
```

因此其中的副作用不会“连接断了以后才开始”。

测试 `run_drops_future_without_polling_after_close` 直接用 atomic flag 证明 body 没执行。

---

## 17. 已开始 handler 不会被 close 取消

测试 `close_returns_while_started_run_remains_active` 证明：

- handler 已进入 body；
- close 很快返回；
- inflight count 仍为 1；
- 释放测试 barrier 后 handler 才完成。

这是刻意的非破坏性语义：close 阻止新增工作，但让已经承诺执行的 RPC 有机会形成 response/error 或安全收尾。

---

## 18. 第三步：从 outbound router 删除 writer

processor 向独立 outbound task 发送：

```rust
OutboundControlEvent::Closed { connection_id }
```

outbound task 从自己的 `outbound_connections` map 删除该 state。

从此新的 `ToConnection` envelope 即使迟到，router 也会记录“dropping message for disconnected connection”，而不是把它送到旧 writer。

---

## 19. 为什么 processor map 与 outbound map 分开

两个 task 负责不同工作：

| owner | 保存什么 | 为什么独立 |
|---|---|---|
| processor loop | session、RPC gate、connection origin | 处理 incoming 与业务准入 |
| outbound loop | writer、capability projection、disconnect token | 处理可能慢的每连接发送 |

它们通过 `OutboundControlEvent` 同步 open/close，而不是共享一个大 mutex。

teardown 因此也必须在两个 owner 中各删除一次。

---

## 20. outbound control 使用 biased select

outbound router 的 `tokio::select!` 标记 `biased;`，control events 分支写在 outgoing envelope 之前。

这提高了 close/disconnect control 相对普通消息的优先级：当两边同时 ready 时，先处理连接状态变化，再处理可能发往该连接的 envelope。

它不能消除所有并发时序，但减少“close 已排队、旧消息仍继续路由”的窗口。

---

## 21. 为什么 cleanup 不能直接在主循环 await

per-connection cleanup 最多要等 RPC 30 秒，还可能向多个 process control channel 发送命令。

若主 processor loop 原地 await：

- 其他连接的新 request 无法及时处理；
- 其他 close/open events 堵在 channel；
- remote status 和 thread-created events 也停住。

所以它把 cleanup 放进 `ConnectionCleanupTasks` 的 `JoinSet`，主循环继续服务其他连接。

---

## 22. `ConnectionCleanupTasks` 是一个 task owner

它封装 `JoinSet<()>` 并提供：

| 方法 | 作用 |
|---|---|
| `spawn` | 启动一个 connection cleanup future |
| `reap_next` | 回收一个已完成 cleanup，记录非取消 panic/error |
| `drain` | 等待所有 cleanup tasks 完成 |
| `abort` | 强制取消全部 cleanup tasks |

主循环拥有这个对象，因此不会把 cleanup task 完全 fire-and-forget 后失去管理能力。

---

## 23. 空 JoinSet 为什么返回 pending future

`reap_next` 在 tasks 为空时执行：

```rust
pending::<()>().await;
```

它用于 `select!` 分支。若空集合时立即返回，主循环会反复选择这个永远“立刻 ready”的分支，形成 busy loop。

返回永不就绪的 future，等价于“没有 cleanup task 时禁用此分支”。

---

## 24. cleanup task 的第一项工作：有界等待 active RPC

`MessageProcessor::connection_closed` 先执行：

```rust
timeout(
    CONNECTION_RPC_DRAIN_TIMEOUT,
    session_state.rpc_gate.shutdown(),
).await
```

固定 timeout 是 30 秒。

因此已开始 handler 可以继续完成，但一个卡死 handler 不会让该 connection 的所有资源永远无法清理。

---

## 25. 30 秒 timeout 到期不等于 handler 被 abort

这是最重要的 deadline 误区之一。

`timeout(wait_future)` 到期时：

- 被丢弃的是等待 `TaskTracker::wait()` 的 future；
- 已开始的 handler task 本身不一定被取消；
- cleanup 记录 warning 并继续删除其他资源。

所以 timeout 的承诺是“cleanup 最多等这么久”，不是“工作一定在这个时间内停止”。

---

## 26. 为什么 request context 在 drain 之后清

顺序是：

```text
wait active RPCs
  → outgoing.connection_closed
```

这样 active handler 若在 30 秒内自然调用 `send_response` 或 `send_error`，仍能 take 到原 request context 和 span。

若先清 context，所有仍在收尾的 handlers 都会失去 tracing/response correlation 状态。

---

## 27. 为什么 writer 却在 drain 之前移除

物理连接已经断开，继续保留 writer 不会让 response 真正可交付，反而可能积压消息或引用失效 I/O。

因此两个目标顺序不同：

```text
outbound routability  → 立即撤销
request context       → 给 active handler 一个有界收尾窗口后再清
```

context 可用于完整记录工作终结；writer 则代表已经不存在的传输能力。

---

## 28. cleanup 顺序总览

`MessageProcessor::connection_closed` 的 processor-specific 顺序是：

1. 等 RPC gate shutdown，最多 30 秒；
2. 清 incoming request contexts；
3. 清 filesystem watches；
4. terminate `command/exec` sessions；
5. kill `process/*` sessions；
6. 从 thread state 移除 connection。

这些调用都按 connection ID 限定作用域。

---

## 29. request context cleanup 做什么

实现按 key retain：

```text
保留 connection_id != closed 的 entries
```

这会释放：

- delayed response 的 request identity；
- 保存的 request span handle；
- parent trace fallback。

不会影响其他连接，即使它们使用了相同 JSON-RPC request ID。

---

## 30. 反向 request callback 为什么没有在这里全清

`request_id_to_callback` 是 server → client reverse request 的全局 pending map，不按单一 connection 作为 owner。

一个 reverse request 可能广播或与 thread 绑定；某个 connection 掉线时，其他订阅者仍可能回答。

因此 per-connection `outgoing.connection_closed` 只清 incoming `request_contexts`，不盲目 `cancel_all_requests`。

反向 requests 由首答、Turn transition、thread unload、业务 timeout 或整个 runtime shutdown 等更合适的 owner 收口。

---

## 31. 这也带来一个真实边界

如果某项 reverse request 实际只发给已经断开的唯一客户端，generic connection cleanup 不会仅凭 close event 自动取消全局 callback。

它需要依赖：

- 该请求自身的 timeout；
- Turn/thread 生命周期取消；
- runtime teardown 时 map owner drop/cancel。

不能把“连接断开”错误等价为“所有 server requests 都无人可答”。在多客户端系统中，这个判断必须由 thread subscription/targeting 语义完成。

---

## 32. filesystem watch 的 ownership key

watch key 是：

```rust
WatchKey {
    connection_id,
    watch_id,
}
```

不同 connections 可重复使用同一个 `watchId`。teardown 按 connection ID `extract_if`，只删除掉线客户端拥有的 entries。

---

## 33. 删除 watch entry 为什么能让 task 退出

`WatchEntry` 保存：

- `terminate_tx`；
- file watcher subscriber；
- registration guard。

connection cleanup 删除 entry 后，这些 owners 被 drop：

- terminate sender drop 使 receiver 完成；
- subscriber/registration guard drop 解除底层 watch ownership；
- spawned loop 下一次 select 后退出。

这是 RAII 与 channel closure 共同实现的 cleanup。

---

## 34. `unwatch` 与 connection close 的保证不同

显式 `fs/unwatch` 会建立一个 done oneshot，并等待 watch task 确认退出，保证 unwatch response 后不再发送该 watch 的 notifications。

connection close 只删除 entries，不等待 done ack。

原因是：连接已经不可路由，不再需要向它承诺“response 之后绝无通知”；此时优先是快速释放 ownership。

---

## 35. fs cleanup 测试证明什么

`connection_closed_removes_only_that_connections_watches` 建立：

- connection 1 的两个 watches；
- connection 2 的一个 watch；
- close connection 1；
- 最终只剩 connection 2 的 entry。

它证明 connection-scoped selection，不证明底层 OS watcher task 在同一瞬间已经完全退出。

---

## 36. `command/exec` process key 也带 connection scope

```text
ConnectionProcessId
  = connection_id + internal process ID
```

同一个 client-supplied process ID 在不同连接中不会冲突。

连接关闭时，manager 先在 mutex 内找出并 remove 所有属于该 connection 的 sessions，然后在锁外逐个发送 `Terminate`。

---

## 37. 为什么 process controls 要先从 map 取出

如果边持有 sessions mutex，边对 control channel `.send().await`：

- channel 背压会长时间占锁；
- process task 完成时无法删除自己；
- 其他连接的 write/resize/terminate 请求也被阻塞。

当前模式是：

```text
短锁内：collect + remove ownership
锁外：await Terminate sends
```

这与上一章 terminal take 的原则相同。

---

## 38. `Terminate` 是请求，不是完成确认

connection cleanup 发送：

```rust
CommandControlRequest {
    control: CommandControl::Terminate,
    response_tx: None,
}
```

没有 response waiter，因此 cleanup 只要求把终止意图交给 process owner，不等待 OS process 真正退出。

process runner 随后负责终止、drain output、移除残余状态。

---

## 39. Windows unsupported session 的特殊边界

`CommandExecSession` 还可能是 `UnsupportedWindowsSandbox`。

cleanup 从 map 删除它，但只有 `Active` variant 才有 control channel 可以发送 `Terminate`。

这说明 teardown 必须对 runtime state variants 做 exhaustive ownership 判断，不能假设所有 registry entry 都有同一种停止手段。

---

## 40. `process/*` 使用相似但独立的 owner

`ProcessExecManager` 的 key 是：

```text
ConnectionProcessHandle
  = connection_id + process_handle
```

connection close 同样先 remove sessions，再在锁外发送：

```text
ProcessControl::Kill
```

`command/exec` 与 `process/*` 是两套 API/manager，因此 MessageProcessor 必须分别调用两次 cleanup。

---

## 41. Terminate 与 Kill 的命名不要过度推断

在这两个 manager 中：

- `CommandControl::Terminate`；
- `ProcessControl::Kill`。

名字不同反映各自 API abstraction，不足以仅凭字面断言具体 OS signal 强度。要判断 SIGTERM/SIGKILL、Windows behavior 或 graceful PTY close，必须继续进入各自 runner 和 `ProcessHandle` 实现。

本文只断言 connection cleanup 发出了对应 control variant。

---

## 42. thread cleanup 首先移除 live capability

`ThreadStateManager::remove_connection` 会从：

- `live_connections`；
- `thread_ids_by_connection`；
- 每个 `ThreadEntry.connection_ids`；

同步移除该 connection。

这同时撤销：

- notification subscription；
- attestation responder 资格；
- 后续 auto-subscribe 的 live connection 条件。

---

## 43. 双向索引为什么要一起改

thread routing 同时保存：

```text
connection → thread IDs
thread → connection IDs
```

关闭连接时用前者快速找到受影响 threads，再在后者中删除 connection。

若只改一边：

- fan-out 可能继续选中已断开的连接；或
- cleanup/查询认为它仍订阅 thread。

双向索引必须在同一 state mutex 临界区保持一致。

---

## 44. `has_connections_watcher` 也要更新

每个 ThreadEntry 有一个 `watch::Sender<bool>`，表示它是否还有 subscribers。

remove connection 后调用 `update_has_connections()`：

- 还有 connection 92 → 保持 true；
- 最后一个 subscriber 被删 → 变成 false。

这个变化会驱动无订阅 thread 的延迟卸载逻辑，而不是在 close handler 中立即杀掉 thread。

---

## 45. 单连接 close 为什么不取消 listener

案例 A 中 connection 92 仍订阅 thread T。

测试 `removing_auto_attached_connection_preserves_listener_for_other_connections` 证明：

- 删除 connection A；
- 返回的待 unload threads 为空；
- listener cancel oneshot 没有触发；
- connection B 仍在 subscriber 列表。

listener 属于 thread，不属于任意一个单独 connection。

---

## 46. 最后一个 subscriber 离开也不等于立即 shutdown thread

`remove_connection` 返回“现在没有 connections 的 thread IDs”。ThreadProcessor 还会检查 Core thread 是否已经不存在；若只是无人订阅但 Core thread 仍在，常规 no-subscriber unload 策略接管。

前一篇 thread watch 文档讲过，它通常还有宽限期与 active-state 复核。

因此：

```text
connection close ≠ thread interrupt
connection close ≠ thread shutdown
connection close ≠ immediate unload
```

---

## 47. 为什么 close 后不能被 auto-subscribe 重新加回

auto-subscribe helper 先检查 `live_connections`。

测试 `closed_connection_cannot_be_reintroduced_by_auto_subscribe` 证明：

- connection 先 initialize；
- remove_connection；
- 再尝试 ensure subscription；
- 返回 `None`，thread 仍无 subscriber。

先删除 live capability 就是一道 generation fence，防止迟到 thread-created/attach 工作复活旧连接。

---

## 48. 单连接 cleanup 的完整状态变化

对案例 A：

```text
processor connections:        remove 41
RPC gate 41:                  accepting=false
outbound connections:         remove writer 41
request contexts:             remove keys whose connection=41
fs watches:                   remove WatchKey.connection=41
command sessions:             remove + send Terminate
process sessions:             remove + send Kill
live connection capabilities: remove 41
thread T subscribers:         {41,92} → {92}
Core thread T:                unchanged
connection 92 resources:      unchanged
```

这就是 connection-scoped teardown 的边界。

---

## 49. stdio close 为什么会结束整个服务器

主循环记录 connection origin。若：

```text
single_client_mode && stdio_closed
```

就以 `stdio_connection_closed` 作为 processor exit reason。

stdio 模式只有一个客户端；stdin EOF 后继续保留无客户端 App-server 通常没有意义。

WebSocket 则可以有多条连接，一个 peer close 不应结束整个 server。

---

## 50. 全局 shutdown 的入口：signal state machine

非 single-client transport 可安装 signal handler：

- Unix Ctrl-C / SIGTERM：`Forceable`；
- Unix SIGHUP：`GracefulOnly`；
- 非 Unix Ctrl-C：`Forceable`。

`ShutdownState` 保存：

- 是否已 requested；
- 是否已 forced；
- 上次记录的 running turn count。

这不是一个 boolean，因为第一次与重复信号的语义不同。

---

## 51. 第一次信号不会立刻停止接纳 requests

固定提交的日志明确写：

```text
requests still accepted until no assistant turns are running
```

第一次 signal 只把 shutdown 标为 requested。只要 running assistant Turn count 仍大于 0，主 select loop 继续处理 transport events 和 requests。

这是一种 graceful restart drain 策略：等待现有 assistant work 自然归零，同时暂不立即关闭 connections。

---

## 52. 这是一个需要看清的产品权衡

很多服务器在第一次 shutdown signal 后立刻停止接纳新请求；当前固定实现没有这么做。

因此可能出现：

```text
shutdown requested
  → existing turn still running
  → client submits another request
  → request may still be accepted
```

能否导致新的 assistant Turn 取决于上层调用行为。读 runbook 时不能把它假设成标准 HTTP server 的“立即 readiness=false”。

---

## 53. 什么条件允许 graceful finish

每轮 loop 顶部调用：

```text
shutdown_state.update(running_turn_count, connection_count)
```

若：

- forced=true；或
- requested=true 且 running_turn_count=0；

返回 `Finish`。

注意条件关注的是 running assistant Turns，不是 connection count 必须变成 0。

---

## 54. Finish 时先做两项 control-plane 动作

```text
transport_shutdown_token.cancel()
outbound_control_tx.send(DisconnectAll)
```

前者要求 WebSocket/unix/remote acceptors 停止继续接入；后者要求 outbound router 断开所有 connection-oriented clients 并清空 writer map。

这是“关闭入口 + 撤销所有出站路由能力”。

---

## 55. `DisconnectAll` 怎样工作

outbound router 遍历当前 states：

```text
for each connection:
  request_disconnect()
clear outbound_connections
```

只有有 disconnect token 的 connection 能被主动 cancel；stdio 没有该 token，但 stdio 不使用这套 multi-client graceful signal restart。

清 map 后，迟到 outgoing envelopes 会被 drop。

---

## 56. graceful processor exit 后的四个 drain 阶段

若不是 forced：

1. 对 `connections` map 中每个 session 调 `rpc_gate.shutdown()`，并发 `join_all`；
2. `connection_cleanup_tasks.drain()`；
3. `processor.drain_background_tasks()`；
4. `processor.shutdown_threads()`。

顺序表达了依赖：先不再有 handler 继续制造新 background work，再等已有 connection cleanup，再收 App-server background tasks，最后关闭 Core threads。

---

## 57. 全局 RPC gate drain 没有本地 30 秒 wrapper

per-connection cleanup 使用 30 秒 timeout；全局 graceful exit 对当前 `connections` 中的 gates 直接 `join_all(...shutdown())`。

因此一个不结束的 active handler 理论上可以拖住 graceful processor exit。

这是 graceful 承诺与 availability 的权衡，也是第二次 forceable signal 存在的原因之一。

---

## 58. cleanup task drain 为什么排在 background drain 前

单连接 cleanup tasks 可能仍在：

- 等 handler；
- 清 process/watch/subscription；
- 释放 request contexts。

先 `drain` 它们，能让 connection-scoped ownership 尽量完整收口，再关闭 thread-start 等 App-server background tasks。

多个 connection cleanups 自己并发运行，所以 drain 等的是整个 JoinSet 清空，而不是按连接串行等待 30 秒。

---

## 59. ThreadProcessor background tasks 包含什么

典型是 `thread/start` 把长操作 spawn 到 `background_tasks: TaskTracker`。

shutdown 时：

```text
background_tasks.close()
timeout(10s, background_tasks.wait())
```

在 10 秒内完成最好；否则 warning 后继续。

---

## 60. background drain timeout 同样不会自动 abort task

`TaskTracker::close` 关闭 tracker 的等待状态，`wait` 等已有 tasks；timeout 只停止等待。

代码没有在 timeout branch 调 `abort_all`。

因此文档必须说“有界等待后继续”，不能说“10 秒后所有 background tasks 已被杀死”。

---

## 61. Core threads 使用独立的 10 秒 bounded shutdown

ThreadProcessor 调：

```rust
thread_manager.shutdown_all_threads_bounded(Duration::from_secs(10))
```

ThreadManager 对当前 tracked threads 并发执行：

```text
timeout(10s, thread.shutdown_and_wait())
```

最终返回 completed、submit_failed、timed_out 三组 IDs。

---

## 62. Core `shutdown_and_wait` 做什么

它：

1. 向 Session submission channel 发送 `Op::Shutdown`；
2. 若 session 已经 died，视为无需再提交；
3. 等待 session loop termination future。

所以这是一个明确的业务 lifecycle op + 终止确认，不只是 drop `Arc<CodexThread>`。

---

## 63. completed threads 才从 manager 移除

`shutdown_all_threads_bounded` 只把 `report.completed` 中的 thread IDs 从 manager map 删除。

`submit_failed` 和 `timed_out` 仍保留 tracked，便于调用者重试或检查——至少在 manager 继续存活的语境中如此。

随后 App-server 进程若整体退出，runtime drop 仍会结束剩余 tasks；但 report 语义本身不伪装成全部成功。

---

## 64. per-thread timeout 是并发的

所有 shutdown futures 放入 `FuturesUnordered`。

这意味着 100 个 threads 不会简单变成 `100 × 10 秒` 的串行最坏等待；每个拥有自己的 10 秒 deadline，并发推进。

最后 report 还按 thread ID 排序，使测试与日志结果稳定。

---

## 65. forced signal 怎样改变承诺

第一次 `Forceable` signal：requested=true。

如果已经 requested，又收到 `Forceable`：forced=true。

下一次 update 立即 Finish，即使还有 running Turns。processor exit 后：

```rust
if !forced {
    graceful drains...
} else {
    connection_cleanup_tasks.abort();
}
```

也就是跳过 RPC/background/thread 的优雅等待，并 abort connection cleanup tasks。

---

## 66. repeated SIGHUP 为什么不能 force

SIGHUP 被标成 `GracefulOnly`。

在已经 requested 时，只有 `Forceable` 才把 forced 设为 true。重复 SIGHUP 仍继续等 running Turns。

集成测试明确覆盖“repeated SIGHUP keeps waiting”。这使操作语义可以区分“请求平滑重启”与“管理员明确要求强制退出”。

---

## 67. signal 集成测试证明什么

Unix WebSocket suite 用一个延迟 Responses API mock 建立运行中 Turn，然后验证：

- 第一次 Ctrl-C 不在 300ms 内退出；
- Turn 完成后进程在期限内成功退出；
- 第二次 Ctrl-C 可在 Turn 仍运行时强制退出；
- SIGTERM 有相同 graceful/forceable 行为；
- 重复 SIGHUP 仍等待，不进入 forced；
- 最终 WebSocket 断开。

它验证的是外部进程行为，而不仅是 `ShutdownState` unit logic。

---

## 68. processor、outbound、acceptor 的最终 join 顺序

外层 main 在 processor task 结束后：

```text
await processor_handle
  → await outbound_handle
  → cancel transport token again
  → await OTEL reloader
  → await all transport accept handles
```

processor drop 会逐步释放 outgoing sender owners；global outgoing channel 关闭后 outbound router 才退出。

这避免 outbound router 在 producers 仍可能发送时过早消失。

---

## 69. 为什么 await outbound handle 依赖 sender ownership

outbound loop 在 control channel 或 outgoing channel 关闭时退出。

若 detached work 仍持有 `Arc<OutgoingMessageSender>`，它也间接持有 outgoing `mpsc::Sender`，channel 不会关闭。

这就是为什么 background task ownership 必须先 drain，不能只 drop processor 主变量然后盲等 channel closure。

in-process 源码甚至直接注释：detached processor work 可能保留 outgoing senders，所以不能只靠 channel closure 关闭 outbound router。

---

## 70. in-process shutdown 为什么更显式

embedded runtime 没有 WebSocket physical disconnect 和 OS acceptor。`InProcessClientHandle::shutdown` 通过 channel 发 `Shutdown { done_tx }`，等待 acknowledgement，再等待 runtime task。

runtime 内部显式拆成：

- processor command loop；
- outbound router；
- client response waiter map；
- server event channel；
- runtime owner task。

所以它需要自己构造一套 terminal protocol。

---

## 71. in-process processor 的收尾顺序

processor command channel 关闭后，processor task 依次：

1. `clear_runtime_references()`；
2. `cancel_active_login()`；
3. `connection_closed(IN_PROCESS_CONNECTION_ID, session)`；
4. `clear_all_thread_listeners()`；
5. `drain_background_tasks()`；
6. `shutdown_threads()`。

比 generic connection close 多了 runtime-global owners，因为 embedded runtime 本身正整体退出。

---

## 72. 为什么先 clear runtime references

`clear_runtime_references` 会断开 account external auth、apps、model refresh worker 和 skills watcher 等长期引用/worker。

它们可能持有 processor、channels 或外部资源。先停止这些生产者，可以减少 shutdown 后半段又产生新工作或维持 sender 引用的机会。

这里应理解为 runtime-global producer shutdown，不是 per-connection cleanup。

---

## 73. clear listeners 与 shutdown threads 是两件事

`clear_all_thread_listeners`：

- 发送 listener cancel oneshot；
- 清 command sender；
- reset current-turn projection；
- drop weak thread link 与 watch registration。

`shutdown_threads`：

- 向 Core sessions 提交 `Op::Shutdown`；

- 等 session loops 终止。

前者停 App-server 的事件投影消费者，后者停 Core execution owners。

---

## 74. in-process 先取消 reverse requests

外层 runtime loop 结束后：

```rust
outgoing_message_sender.cancel_all_requests(Some(internal_error(
    "in-process app-server runtime is shutting down",
))).await;
```

这会 drain server → client pending callback map，并用明确 error 唤醒 waiters。

embedded runtime 已确定没有其他 client connection 可以回答，因此可以安全地全局取消。

---

## 75. in-process client response waiters 也要逐个终结

`pending_request_responses` 保存客户端发 request 后等待 server response 的 oneshot senders。

runtime shutdown 会把剩余 entries 全部发送：

```text
internal error: in-process app-server runtime is shutting down
```

否则调用 `client.request(...).await` 的 embedder future 只能依赖 sender drop 得到模糊 channel closed，而不是明确业务错误。

---

## 76. 为什么 in-process 主动 drop writer receiver 和 processor sender

```text
drop(writer_rx)
drop(processor_tx)
```

它们分别告诉：

- producer：不再接受 writer output；
- processor loop：不再有新的 commands。

channel closure 是一种结构化 shutdown signal，但只有所有 sender/receiver ownership 配合时才有效。

---

## 77. in-process outbound router 有显式 shutdown oneshot

由于 detached tasks 可能保留 outgoing sender，单等 outgoing channel closed 可能永远不结束。

所以 in-process outbound router 的 select 还有：

```text
shutdown_rx → break
```

runtime 在 processor 阶段后发送这个 oneshot，显式终结 router owner。

这是“不要让 ref-count/channel closure 成为唯一 shutdown 协议”的好例子。

---

## 78. in-process 的 5 秒 task deadline 与 abort

固定 `SHUTDOWN_TIMEOUT` 是 5 秒。

对 processor handle 和 outbound handle：

```text
timeout(5s, join handle)
  → timeout 时 JoinHandle::abort
  → await aborted handle
```

这与前面的 TaskTracker timeout 不同：这里 deadline 到期后明确 abort 对应 Tokio task。

---

## 79. shutdown acknowledgement 也有独立 deadline

public `InProcessClientHandle::shutdown`：

1. 发送 `Shutdown { done_tx }`；
2. 等 `done_rx`，使用 `SHUTDOWN_ACK_TIMEOUT`；
3. 再等整个 runtime handle，使用 `SHUTDOWN_TIMEOUT`；
4. runtime 仍不结束则 abort。

ack 表示内部 staged cleanup 已走到最后；runtime join 则表示 owner task 真正结束。两者不是同一屏障。

---

## 80. analytics flush 位于 in-process terminal 尾部

processor/outbound tasks 收口后，runtime 调 analytics client `flush().await`，然后才发送 shutdown ack。

因此 in-process ack 的承诺包含一次 analytics flush 尝试。

generic transport path 则由 OTEL reloader/进程级 telemetry owner 的 join 处理其生命周期，不能把两条路径的具体步骤假设完全一致。

---

## 81. 四条 shutdown 路径对照

| 路径 | 触发 | 主要承诺 |
|---|---|---|
| 单 WebSocket close | peer/I/O/disconnect token | 只清该 connection ownership，其他连接和 threads 继续 |
| stdio EOF | stdin 关闭 | 单客户端 server 整体退出，并走非 forced drains |
| graceful signal restart | first signal + running turns 归零 | disconnect all、drain RPC/cleanup/background、bounded thread shutdown |
| forced signal restart | requested 后第二次 forceable signal | 立即 finish，跳过 graceful drains并 abort cleanup tasks |
| in-process shutdown | explicit handle command/channel close | 明确终结 waiters、listeners、background、threads、router，并在 deadline 后 abort tasks |

---

## 82. timeout、cancel、abort 的精确对照

| 代码形式 | 对目标意味着什么 |
|---|---|
| `timeout(d, wait()).await` | 最多等待 d；到期不自动停止底层工作 |
| `CancellationToken::cancel()` | 发布协作式取消；目标必须观察 token |
| oneshot sender drop/send | 唤醒唯一 receiver，常用于 task stop/ack |
| control channel `Terminate/Kill` | 请求 process owner 停止 OS process |
| `JoinHandle::abort()` | Tokio 停止继续 poll task；已完成的同步副作用不会回滚 |
| `JoinSet::abort_all()` | abort 集合中所有 tracked Tokio tasks |
| drop last `mpsc::Sender` | receiver 最终得到 channel closed |

读 shutdown 代码时必须指出具体使用了哪一种。

---

## 83. 为什么 cleanup 必须按 owner，而不是按资源类型写一个万能函数

每种资源的真正 owner 和停止协议不同：

```text
RPC handler       → gate token / TaskTracker
writer            → outbound router map + transport loop
request context   → OutgoingMessageSender registry
fs watch          → WatchEntry RAII guards + oneshot
process           → manager map + control channel + runner
subscription      → ThreadStateManager 双向索引
Core thread       → ThreadManager + Op::Shutdown + termination future
```

万能 `cleanup_everything(connection_id)` 若不调用这些 owner 的语义 API，容易只删索引却留下真实工作。

---

## 84. “从 map 删除”有三种不同含义

| 删除动作 | 含义 |
|---|---|
| processor connections remove | 撤销 live connection authority |
| outbound connections remove | 撤销消息路由能力并 drop writer sender |
| process sessions remove | 转移 control ownership，随后仍要发 Terminate/Kill |

所以看到 `.remove()` 不能立即下结论“资源已经关闭”。要继续查看 value drop 和删除后是否还有 control action。

---

## 85. 常见误解一：connection close 会终止 Turn

不一定。

多客户端可以观察同一个 thread；Core thread 生命周期也长于连接。单连接 close 主要撤销订阅和 connection-scoped resources。

只有显式 interrupt、thread shutdown/unload 或全局 runtime shutdown 才改变 Core execution owner。

---

## 86. 常见误解二：30 秒后 handler 一定没了

错误。

30 秒只包住 gate `shutdown().wait()`。timeout 后 cleanup 继续，但 handler task 可能仍在执行，之后可能尝试发送迟到 response 或产生已开始的副作用。

若需要“deadline 后必须停止 handler”，还要有明确 cancellation/abort owner；当前这段 per-connection代码没有提供这一保证。

---

## 87. 常见误解三：关 sender 就一定能结束 receiver

只有**最后一个** sender 被 drop，receiver 才看到 channel closed。

Arc、detached task、clone 都可能延长 sender ownership。in-process explicit outbound shutdown 正是为了解决“某处仍持有 sender”的不确定性。

---

## 88. 常见误解四：forced shutdown 是更快的 graceful shutdown

它改变了保证，不只是把 timeout 调小：

- 不等 RPC gates；
- 不 drain cleanup tasks；
- 不 drain thread-start background tasks；
- 不调用 bounded Core thread shutdown；
- abort connection cleanup tasks。

它以更快退出换取更少的收尾保证。

---

## 89. 常见误解五：第一次 signal 后 server 已停止接新工作

固定实现明确继续接收 requests，直到 running assistant Turns 归零。

若部署系统需要 signal 后立刻撤 readiness 或拒绝新 Turns，必须由外部负载均衡/acceptor policy 或未来实现额外提供，不能从当前 `ShutdownState` 推断。

---

## 90. 设计新 connection-scoped 资源时的检查表

1. key 是否包含 connection ID？
2. connection live authority 由哪个 map 判断？
3. close event 是否在慢 cleanup 前先撤销 authority？
4. 资源是直接 drop 即可，还是要发 cancel/terminate 并等待 ack？
5. 是否在 mutex 内只做短 ownership 转移，把 await 放锁外？
6. cleanup 会不会误伤其他连接共享的 thread/resource？
7. queued work 能否在 close 后首次 poll？
8. active work给多长 drain deadline？
9. timeout 后工作究竟继续、cancel 还是 abort？
10. 全局 shutdown 是否还要额外清 runtime-global owner？
11. detached task 是否持有 sender/Arc，阻止 channel closure？
12. 测试是否覆盖“只清目标连接”和“其他连接继续工作”？

---

## 91. 调试“连接断了但进程还在跑”

按 owner 逐层检查：

1. physical transport task 是否发出 `ConnectionClosed`；
2. main processor 是否从 `connections` remove；
3. gate inflight count 是否归零，是否触发 30s warning；
4. cleanup task 是否仍在 JoinSet；
5. command/process runner 是否收到 control；
6. thread 是否只是无订阅但仍在宽限期；
7. 是否有 detached background task 持有 sender/Arc；
8. 当前期望是单连接 cleanup，还是整个 server shutdown。

很多“泄漏”其实是把长于 connection 的 thread 生命周期误判为 connection-owned。

---

## 92. 调试“shutdown 卡住”

先确定卡在哪个 barrier：

```text
running turn count waiting?
RPC gate join_all?
connection cleanup drain?
background TaskTracker wait?
Core thread shutdown_and_wait?
outgoing channel closure / outbound_handle?
transport accept handle?
telemetry reloader?
```

再看对应 owner 的 pending count、warning 和 cancellation signal。不要笼统地说“Tokio 卡住了”。

---

## 93. 调试“断线后还有通知”

区分三个层级：

- notification producer 仍生成事件；
- outgoing sender 成功把 envelope 放入 global queue；
- outbound router 是否仍有 connection writer。

connection close 后 producer 可能短暂继续运行，但 router state 已先 remove，消息应被丢弃。若客户端实际仍收到，则重点检查 close event/control ordering、旧 writer queue 中已排队消息以及 physical socket task 是否真正结束。

---

## 94. 调试“子进程还活着”

检查：

- 使用的是 `command/exec` 还是 `process/*` manager；
- session key 是否真的带目标 connection ID；
- cleanup 是否走过对应 manager；
- control channel send 是否失败；
- runner 是否处理 Terminate/Kill；
- OS process/PTY 的子孙进程组语义；
- timeout 只是不再等待，还是明确 abort/kill。

本文源码只证明 control intent 被发送，不证明所有平台上的整个进程树瞬间消失。

---

## 95. 本章词汇表

| 代码词/短语 | 字面翻译 | 本篇中的具体含义 |
|---|---|---|
| teardown | 拆除 | 停入口、撤 ownership、发停止信号、等待终态和释放资源的完整协议 |
| graceful shutdown | 优雅关闭 | 尽量让已经承诺的工作完成后再退出 |
| forced shutdown | 强制关闭 | 跳过部分 drain/cleanup，以更少保证换取更快退出 |
| admission | 准入 | handler body 是否允许开始执行的边界 |
| gate | 门 | 串行决定接受新工作还是直接 drop future 的 owner |
| drain | 排空 | 停止新增后等待已有工作归零 |
| bounded drain | 有界排空 | 只等待到 deadline，防止无限卡住 |
| inflight | 执行中 | 已取得 gate token、尚未完成的 handler |
| `TaskTracker` | 任务追踪器 | 用 token/task membership 统计并等待一组异步工作 |
| task token | 任务凭证 | 表示某项工作已开始且 drain 应等待它 |
| `JoinSet` | Join 集合 | 拥有并回收一组 Tokio tasks，可 drain 或 abort all |
| reap | 收割/回收 | 取得已完成 task 的 JoinResult，避免完成结果无人处理 |
| busy loop | 忙循环 | future 立即反复 ready，消耗 CPU 却没有有效进展 |
| logical removal | 逻辑移除 | 先撤销 live/routable authority，阻止新工作进入 |
| physical cleanup | 物理清理 | 后续关闭 writer、watch、process 等实际资源 |
| generation fence | 世代围栏 | 旧连接删除后，迟到异步工作不能把它重新视为 live |
| control plane | 控制面 | Opened/Closed/DisconnectAll 等改变路由状态的消息 |
| data plane | 数据面 | 普通 request/response/notification payload 流 |
| disconnect token | 断开令牌 | WebSocket inbound/outbound loops 共享的 CancellationToken |
| connection-scoped | 连接作用域 | ownership key 中包含 connection ID，只影响一个 peer |
| runtime-global | 运行时全局 | apps、model refresh、所有 threads 等整个 runtime 共享 owner |
| `Terminate` | 终止请求 | command process manager 收到的协作式停止 control |
| `Kill` | 杀进程请求 | process API manager 收到的停止 control；具体 OS 强度需继续读 runner |
| shutdown acknowledgement | 关闭确认 | cleanup protocol 完成到约定阶段后通过 oneshot 发出的 ack |
| timeout | 超时 | 停止等待的 deadline；不自动等于目标任务被停止 |
| abort | 中止 task | 停止 Tokio 对 future 的后续 polling，不回滚既有副作用 |
| channel closure | 通道关闭 | 最后 sender/receiver drop 后，对侧观察到不再有消息 |
| ownership transfer | 所有权转移 | 从 map remove value 后在锁外执行异步停止动作 |
| shutdown report | 关闭报告 | Core threads 的 completed/submit_failed/timed_out 分类结果 |
| `DisconnectAll` | 全部断开 | outbound router cancel 可断连接并清空所有 writer states |
| running turn count | 运行 Turn 数 | graceful signal state 判断何时可以开始最终退出的 watch 值 |

---

## 96. 代码单词拆解

### `ConnectionCleanupTasks`

```text
Connection     哪个作用域
Cleanup        做资源清理
Tasks          多个异步任务，由集合 owner 管理
```

它不是清理逻辑本身，而是 cleanup futures 的生命周期 owner。

### `shutdown_all_threads_bounded`

```text
shutdown       请求停止并等待
all_threads    当前 manager 追踪的全部 threads
bounded        每条 shutdown 有时间上限
```

返回 report，说明“有界”允许部分失败/超时，而不是承诺全部成功。

### `clear_runtime_references`

```text
clear          撤销/释放
runtime        整个 embedded runtime 范围
references     指向长期 worker/external owner 的引用
```

它不同于清单一 connection 的 map entry。

### `request_disconnect`

```text
request        发出请求，不保证已经完成
disconnect     让 transport loops 结束连接
```

函数调用成功只表示 cancel signal 已发布。

---

## 97. 理解检查

### 练习一

为什么 connection close branch 要先从 processor `connections` map remove，再 spawn 慢 cleanup？

<details>
<summary>参考答案</summary>

先撤销 live authority，后续迟到消息会被拒绝，避免 cleanup 期间新 handler 又创建资源；慢 cleanup 放独立 task，避免阻塞其他连接。

</details>

### 练习二

`rpc_gate.close()` 后，一个已经进入 body 的 handler 会立即停止吗？

<details>
<summary>参考答案</summary>

不会。close 只禁止新的/queued body 开始；已取得 token 的 handler 继续执行，shutdown/wait 才等待它归零。

</details>

### 练习三

为什么 outbound writer 立即删除，而 request context 要等 active RPC drain 后再删除？

<details>
<summary>参考答案</summary>

物理连接已不存在，writer 不再有交付价值；context 仍能帮助已开始 handler 完成 response/error 与 tracing 收尾，所以给它有界保留窗口。

</details>

### 练习四

connection 41 和 92 都订阅 thread T。41 断开后，是否应取消 T 的 listener？

<details>
<summary>参考答案</summary>

不应。listener 属于 thread，92 仍需要它。实现只从双向索引移除 41，并保持 listener 与 Core thread。

</details>

### 练习五

`timeout(30s, rpc_gate.shutdown())` 到期后，可以断言 handler 已被取消吗？

<details>
<summary>参考答案</summary>

不能。它只停止等待 TaskTracker 归零；代码没有在该分支 abort handler。handler 可能继续执行并产生迟到结果。

</details>

### 练习六

为什么单连接 close 不直接 `cancel_all_requests` 清空 reverse callbacks？

<details>
<summary>参考答案</summary>

reverse request 可能广播给多个连接或由 thread 其他 subscriber 回答，不属于某一个 connection 的独占状态。它应由首答、Turn/thread 生命周期、timeout 或 runtime shutdown 收口。

</details>

### 练习七

第一次 SIGTERM 后，当前固定实现是否立即拒绝所有新 requests？

<details>
<summary>参考答案</summary>

否。只要仍有 running assistant Turns，processor loop 继续接收 requests；Turn 数归零后才进入 DisconnectAll 和最终 drains。

</details>

### 练习八

in-process outbound router 为什么需要显式 shutdown oneshot？

<details>
<summary>参考答案</summary>

detached work 可能仍持有 outgoing sender，单靠等待 channel closure 可能永远不结束。oneshot 让 runtime owner 可以明确命令 router 退出。

</details>

---

## 98. 源码导航

| 阅读目标 | 固定提交路径 | 重点符号 |
|---|---|---|
| Transport lifecycle events | `codex-rs/app-server-transport/src/transport/mod.rs` | `TransportEvent`、`ConnectionOrigin` |
| Stdio close emission | `codex-rs/app-server-transport/src/transport/stdio.rs` | stdin reader loop、EOF、`ConnectionClosed` |
| Stdio writer closure | 同上 | stdout writer loop |
| WebSocket connection owner | `codex-rs/app-server-transport/src/transport/websocket.rs` | `run_websocket_connection` |
| Inbound/outbound race | 同上 | select、token cancel、opposite task abort |
| WebSocket outbound stop | 同上 | `run_websocket_outbound_loop` |
| WebSocket inbound stop | 同上 | `run_websocket_inbound_loop` |
| Acceptor graceful stop | 同上 | `start_websocket_acceptor`、shutdown token |
| Processor connection map | `codex-rs/app-server/src/lib.rs` | `connections` |
| Connection close branch | 同上 | remove、gate close、outbound Closed、cleanup spawn |
| Single-client stdio exit | 同上 | `single_client_mode && stdio_closed` |
| Outbound control enum | 同上 | `OutboundControlEvent` |
| Outbound router control priority | 同上 | biased select |
| Disconnect all | 同上 | `OutboundControlEvent::DisconnectAll` |
| Signal state machine | 同上 | `ShutdownState`、`ShutdownSignal`、`ShutdownAction` |
| Graceful wait condition | 同上 | `running_turn_count_rx`、`ShutdownState::update` |
| Graceful final drains | 同上 | gate join_all、cleanup/background/thread drain |
| Forced path | 同上 | `connection_cleanup_tasks.abort()` |
| Final task joins | 同上 | processor/outbound/OTEL/accept handles |
| Outbound connection owner | `codex-rs/app-server/src/transport.rs` | `OutboundConnectionState` |
| Disconnect token request | 同上 | `request_disconnect` |
| Drop disconnected message | 同上 | `send_message_to_connection` |
| RPC gate | `codex-rs/app-server/src/connection_rpc_gate.rs` | `ConnectionRpcGate` |
| Gate admission token | 同上 | `run` |
| Close vs shutdown | 同上 | `close`、`shutdown` |
| Gate no-poll test | 同上 | `run_drops_future_without_polling_after_close` |
| Active survives close test | 同上 | `close_returns_while_started_run_remains_active` |
| Shutdown waits test | 同上 | `shutdown_waits_for_started_run_to_finish` |
| Late run fencing test | 同上 | `shutdown_drops_late_runs_while_waiting_for_inflight_work` |
| Cleanup task owner | `codex-rs/app-server/src/connection_cleanup.rs` | `ConnectionCleanupTasks` |
| Empty set pending behavior | 同上 | `reap_next` |
| Graceful drain/forced abort | 同上 | `drain`、`abort` |
| Per-connection deadline | `codex-rs/app-server/src/message_processor.rs` | `CONNECTION_RPC_DRAIN_TIMEOUT` |
| Cleanup fan-out sequence | 同上 | `connection_closed` |
| Runtime-global reference cleanup | 同上 | `clear_runtime_references` |
| Background/thread wrapper methods | 同上 | drain/clear/shutdown helpers |
| Incoming request context purge | `codex-rs/app-server/src/outgoing_message.rs` | `connection_closed` |
| Reverse callback global map | 同上 | `request_id_to_callback`、`cancel_all_requests` |
| Request context cleanup test | 同上 | `connection_closed_clears_registered_request_contexts` |
| Filesystem watch owner | `codex-rs/app-server/src/fs_watch.rs` | `WatchKey`、`WatchEntry` |
| Explicit unwatch ack | 同上 | `unwatch` |
| Connection watch cleanup | 同上 | `connection_closed` |
| Watch isolation test | 同上 | `connection_closed_removes_only_that_connections_watches` |
| Command exec owner | `codex-rs/app-server/src/command_exec.rs` | `CommandExecManager` |
| Command process key | 同上 | `ConnectionProcessId` |
| Command teardown | 同上 | `connection_closed`、`CommandControl::Terminate` |
| Process API owner | `codex-rs/app-server/src/request_processors/process_exec_processor.rs` | `ProcessExecManager` |
| Process key | 同上 | `ConnectionProcessHandle` |
| Process teardown | 同上 | `connection_closed`、`ProcessControl::Kill` |
| Thread subscription indexes | `codex-rs/app-server/src/thread_state.rs` | `ThreadStateManagerInner` |
| Remove connection | 同上 | `remove_connection` |
| Listener clear semantics | 同上 | `ThreadState::clear_listener`、`clear_all_listeners` |
| Preserve other subscriber test | `codex-rs/app-server/src/request_processors/thread_processor_tests.rs` | `removing_auto_attached_connection_preserves_listener_for_other_connections` |
| Closed connection fencing test | 同上 | `closed_connection_cannot_be_reintroduced_by_auto_subscribe` |
| ThreadProcessor connection wrapper | `codex-rs/app-server/src/request_processors/thread_processor.rs` | `connection_closed` |
| Background TaskTracker drain | 同上 | `drain_background_tasks` |
| Core thread shutdown wrapper | 同上 | `shutdown_threads` |
| Core shutdown report | `codex-rs/core/src/thread_manager.rs` | `ThreadShutdownReport` |
| Bounded concurrent thread shutdown | 同上 | `shutdown_all_threads_bounded` |
| Core thread shutdown test | `codex-rs/core/src/thread_manager_tests.rs` | `shutdown_all_threads_bounded_submits_shutdown_to_every_thread` |
| Session shutdown submission | `codex-rs/core/src/session/mod.rs`、`codex_thread.rs` | `shutdown_and_wait` |
| Signal integration tests | `codex-rs/app-server/tests/suite/v2/connection_handling_websocket_unix.rs` | Ctrl-C、SIGTERM、SIGHUP tests |
| In-process public shutdown | `codex-rs/app-server/src/in_process.rs` | `InProcessClientHandle::shutdown` |
| In-process processor cleanup | 同上 | processor task tail |
| Pending callback cancellation | 同上 | `cancel_all_requests` |
| Client response waiter drain | 同上 | `pending_request_responses` loop |
| Explicit outbound stop | 同上 | `outbound_shutdown_tx` |
| 5s task deadline/abort | 同上 | `SHUTDOWN_TIMEOUT`、processor/outbound handles |
| Analytics terminal flush | 同上 | `analytics_events_flush_client.flush` |

---

## 99. 本篇收束

固定提交中的 teardown 不是一次删除，而是一套分层 owner protocol：

- stdio/WebSocket transport 先把物理终态变成统一 `ConnectionClosed` event；
- main processor 立即从 live connection map 删除 owner，阻止迟到消息继续准入；
- per-connection RPC gate 先 close，使 queued future 不被 poll，同时允许已取得 token 的 handler 完成；
- outbound control plane 优先移除 writer，撤销已断连接的路由能力；
- 慢 cleanup 进入受 JoinSet 管理的独立 tasks，不阻塞其他 connections；
- active RPC 最多获得 30 秒 drain 窗口，timeout 后继续清理但不自动 abort handler；
- request contexts 在 drain 后清，使及时完成的 handler 仍能正确终结 trace；
- reverse callbacks 是多连接/线程级状态，不能在任一 connection close 时盲目全清；
- filesystem watches 依靠 connection-scoped key、RAII guards 和 oneshot closure 退出；
- command/process managers 先在短锁内转移 ownership，再在锁外发送 Terminate/Kill；
- thread state 同时更新 live capability、双向订阅索引和 has-connections watch；
- 一个 subscriber 离开不会取消其他 subscriber 共享的 listener，也不会立即终止 Core thread；
- stdio EOF、单 WebSocket close、graceful signal、forced signal 与 in-process shutdown 是不同路径；
- 第一次 graceful signal 在 running Turns 归零前仍接受 requests；
- graceful finalization 依次等待 gates、connection cleanups、App-server background tasks 和 Core threads；
- TaskTracker timeout 只停止等待，而 in-process JoinHandle timeout 会明确 abort task；
- Core thread shutdown 通过 `Op::Shutdown` 和 termination future 确认，并把完成/提交失败/超时分类报告；
- in-process runtime 显式清 external workers、login、listeners、callbacks、client waiters、router 和 analytics；
- channel closure 只有在最后 owner drop 后才可靠，因此关键 owner 需要显式 shutdown signal；
- forced shutdown 是保证降级，而不只是“更短的 graceful timeout”。

下一篇适合继续精读 App-server 的 analytics 与 telemetry projection：request/response/notification、thread originator、错误类型和 latency 在哪里记录，哪些事件只是“尝试”，哪些能代表业务终态，以及 telemetry failure 为什么不能反向破坏主流程。

返回[源码精读目录](README.md)，或查看[课程术语总表](../glossary.md)。
