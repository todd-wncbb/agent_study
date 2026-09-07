# 精读 09：Rollout resume——历史记录怎样重新变成可运行的 thread

> 源码基线：`4ee41929eaf4`  
> 公开入口：`thread/resume`  
> App-server 主文件：`codex-rs/app-server/src/request_processors/thread_processor.rs`  
> Core 主文件：`codex-rs/core/src/thread_manager.rs`、`codex-rs/core/src/session/mod.rs`  
> 重建算法：`codex-rs/core/src/session/rollout_reconstruction.rs`  
> 持久化层：`codex-rs/thread-store/src/live_thread.rs`、`codex-rs/thread-store/src/local/`  
> 前置阅读：[上下文、持久化与恢复](../05-context-state-resume.md)、[Rollout、Thread Store 与状态数据库](../29-rollout-thread-store-and-state-db.md)、[精读 03：Session 初始化](03-session-initialization.md)

[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)给出了公开边界：`thread/read` 读取已存储数据但不恢复运行，`thread/resume` 继续已存储任务；仅执行 resume 不会更新时间，开始新 Turn 才会更新；持久化的 dynamic tools 默认会恢复。本篇继续回答文档没有展开的内部问题：磁盘记录怎样经过筛选和重放，重新变成模型历史、运行状态与客户端视图。

## 1. 先说结论：resume 不是复制聊天气泡

第一次看到“恢复对话”，很容易想象成：

```text
读取旧界面上的聊天文字
-> 把文字复制进新窗口
-> 继续聊天
```

Codex 做的事情更像恢复一架飞机的运行现场：

```text
飞行记录器 rollout
  ├─ 对话与工具调用
  ├─ Turn 开始、完成、取消、回滚等事件
  ├─ 当时的模型、cwd、权限等 Turn 设置
  ├─ 压缩后的替代历史
  └─ world-state 的完整快照与增量

恢复程序
  ├─ 找到正确的 thread 和记录文件
  ├─ 重新建立模型可见历史
  ├─ 恢复运行所需的设置与比较基线
  ├─ 重新打开同一个持久化 writer
  ├─ 创建新的内存 Session（冷恢复时）
  └─ 为客户端生成 Thread / Turn / Item 视图
```

最重要的一句话是：

> rollout 是包含多种事实的耐久记录；模型上下文、UI 历史和运行状态，是从这些事实得到的不同投影。

它们不是同一个东西，也不能互相简单替代。

## 2. 一个贯穿全文的例子

假设昨天有一条 thread，做了这些事：

1. 用户要求“检查测试失败”。
2. Codex 调用 shell，得到测试输出。
3. Codex 解释失败原因。
4. 历史太长，系统做过一次 compaction。
5. 用户又要求“修复它”。
6. Codex 修改到一半，应用关闭了。

它的 rollout 可以教学性地简化为：

```text
1  SessionMeta(thread=A, cwd=/repo, dynamic_tools=[...])
2  TurnStarted(turn=1)
3  TurnContext(turn=1, model=M1, cwd=/repo, ...)
4  EventMsg::UserMessage("检查测试失败")
5  ResponseItem::Message(user, "检查测试失败")
6  ResponseItem::FunctionCall(shell, "cargo test ...")
7  ResponseItem::FunctionCallOutput("test x failed")
8  ResponseItem::Message(assistant, "失败原因是……")
9  TurnComplete(turn=1)
10 Compacted(replacement_history=[压缩后的有效模型历史])
11 TurnStarted(turn=2)
12 TurnContext(turn=2, model=M1, cwd=/repo, ...)
13 EventMsg::UserMessage("修复它")
14 ResponseItem::Message(user, "修复它")
15 ResponseItem::FunctionCall(apply_patch, ...)
16 EventMsg::TurnAborted(turn=2)
```

恢复时至少有四个消费者关心这份记录：

| 消费者 | 它关心什么 | 它通常不直接使用什么 |
|---|---|---|
| 模型上下文重建 | replacement history、有效 ResponseItem、回滚 | UI 展示状态、任意 telemetry |
| Session 状态恢复 | 上次模型、TurnContext、world-state、token info、Interrupted 状态 | 纯展示文本的排列方式 |
| App-server 响应 | Thread、Turn、Item、状态、分页游标 | Core 内部所有上下文细节 |
| rollout writer | 原路径、历史模式、下一个 ordinal、尾部是否合法 | UI 是否展开某个气泡 |

所以“恢复成功”不只是能看见旧消息。它还要保证：

- 下一次请求带给模型的历史是正确的；
- 不会把已回滚的 Turn 又交给模型；
- 不会在 compaction 后重复塞入更早的完整历史；
- 新记录继续追加到正确位置；
- 同一 thread 不会同时出现两个 writer；
- 客户端能订阅后续事件。

## 3. 先分清两种 resume：重新加入与冷恢复

`thread/resume` 这个公开方法内部先问：

```text
这个 thread 是否仍在 ThreadManager 的内存表里？
```

答案不同，行为差异很大。

### 3.1 热路径：thread 仍在运行或仍被加载

例如桌面应用打开第二个窗口，而原任务仍在后台运行：

```text
客户端 1 ── 已订阅 ──> 现有 CodexThread / Session
客户端 2 ─ thread/resume
                         └─> 加入同一个现有对象
```

这里不会从磁盘创建第二个 Session。App-server 会：

1. 找到现有 `CodexThread`；
2. 检查请求中的 path 是否与活动 path 一致；
3. 确保 listener task 正在运行；
4. 让新连接订阅这条 thread；
5. 从持久化数据和当前活动状态构造 resume response；
6. 如果 Turn 正在进行，继续向新客户端流式发送事件。

源码把这条路径放在 `resume_running_thread`。它返回：

```rust
RunningThreadResumeResult::Handled
```

外层看到 `Handled` 就结束，不会继续创建 Session。

### 3.2 冷路径：内存中没有这条 thread

例如应用重启后再次打开昨天的任务：

```text
rollout / thread store
  -> InitialHistory::Resumed
  -> ThreadManager::resume_thread_with_history
  -> Session::spawn
  -> 新的内存 Session
```

这里“新的”指运行时对象新建，而不是业务身份新建：

```text
thread_id       仍是 A
rollout_path    仍指向 A 的记录
Session 内存对象 是新创建的
channel/task    是新创建的
```

### 3.3 一张判断表

| 场景 | 是否新建 Session | 是否重读模型历史 | 是否复用 thread id | 是否复用 rollout |
|---|---:|---:|---:|---:|
| 仍在运行，新客户端加入 | 否 | 通常不为 Core 重建 | 是 | 是 |
| 已加载且可直接复用 | 否 | 不为 Core 重建 | 是 | 是 |
| 已卸载，从磁盘恢复 | 是 | 是 | 是 | 是 |
| 请求自带 `history` | 创建一条 fork 风格的新任务 | 只使用给定 ResponseItem | 否 | 否 |

最后一行很反直觉。`resume_thread_from_history` 会把客户端给的 `history` 包成：

```rust
InitialHistory::Forked(...)
```

也就是说，它不是“证明这些消息属于原 thread 并续写原 rollout”，而是以这批消息作为起点创建新的身份。

## 4. 公开 API：read 与 resume 的差别

可以把两个方法理解为：

```text
thread/read   = 给我档案
thread/resume = 让我重新加入或启动这条任务
```

### `thread/read`

- 读取存储信息；
- 可以返回 Thread / Turn / Item 投影；
- 不创建新的 Core Session；
- 不自动让连接订阅未来事件；
- 不加载模型上下文来准备下一次推理。

### `thread/resume`

- 读取并验证存储记录；
- 必要时重建 Core Session；
- 恢复模型上下文与运行基线；
- 打开持久化续写器；
- 自动附加 conversation listener；
- 返回可继续操作的 thread 配置与视图。

因此，以下推断是错的：

```text
read 能显示历史
=> read 已经把 thread 恢复了
```

正确关系是：

```text
显示旧内容所需的数据
小于
安全继续执行下一 Turn 所需的数据与资源
```

## 5. `thread_resume_inner` 的总流程

把约四百行分支压缩成主干伪代码：

```text
thread_resume_inner(params):
    如果 thread 正在 closing:
        返回“稍后重试”

    如果 permissions 与 sandbox 同时给出:
        返回参数错误

    尝试 resume_running_thread(params)
    如果已经处理:
        return

    决定历史来源:
        客户端 history
        或已找到的 StoredThread
        或按 thread id / path 读取 StoredThread

    得到 InitialHistory
    从历史取得 cwd
    合并显式覆盖、持久化元数据与默认配置

    ThreadManager.resume_thread_with_history(config, history)
        -> 必要时 Session::spawn

    恢复/同步持久化投影
    附加 listener
    构造 API Thread / Turn / Item
    发送 ThreadResumeResponse
    补发 token usage / goal / idle 等恢复事件
```

顺序很重要。尤其不能先创建 Session，再检查 thread 是否已在运行；否则同一个业务 thread 可能拥有两个活动 writer 和两套内存状态。

## 6. 第一组保护：closing、参数互斥和序列化

### 6.1 正在关闭时拒绝恢复

`pending_thread_unloads` 表示 thread 已进入卸载流程。此时立即恢复会形成竞态：

```text
旧 Session 正在 flush / shutdown
新 Session 同时尝试打开同一个 rollout
```

因此源码返回：

```text
thread ... is closing; retry thread/resume after the thread is closed
```

这不是“找不到历史”，而是生命周期暂时冲突。

### 6.2 `sandbox` 与 `permissions` 不能同时提供

两者是不同代际、不同抽象层的执行限制表达。如果同时接受，优先级会含糊。入口在产生运行副作用之前拒绝它。

### 6.3 与上一篇的 serialization scope 连接起来

`thread/resume` 会按 thread 资源进入请求串行化范围。这只能协调 App-server handler 的进入顺序；真正防止重复 Session、重复 writer 的检查仍在 App-server、`ThreadManager` 和 Thread Store 多层完成。

一句话：

> 队列降低竞态机会，领域检查维护不变量；不能只靠其中一层。

## 7. 历史从哪里来

冷恢复有三类输入来源。

### 7.1 请求直接携带 `history`

要求非空，然后把每个 `ResponseItem` 包成 `RolloutItem::ResponseItem`，形成 `InitialHistory::Forked`。

它没有 SessionMeta、TurnContext、EventMsg 等完整原始记录，因此不能声称它精确恢复了原任务。

### 7.2 请求提供 `path`

App-server 调用：

```rust
read_thread_by_rollout_path(...)
```

path 决定源记录。测试 `thread_resume_uses_path_over_non_running_thread_id` 证明：当目标没有在运行时，外部 path 可以作为真实查找来源。

但运行中的 thread 不允许拿一个不匹配 path 来“覆盖”它。否则同一个 ID 指向两个不同历史来源。

### 7.3 请求只提供 `thread_id`

Thread Store 负责解析该 ID 对应的 rollout。读取时使用 `include_archived: true` 是为了能够辨认“它存在但被归档”，随后 App-server 明确返回要求先 unarchive 的错误。

这和直接使用 `include_archived: false` 有细小但重要的区别：

```text
没找到
与
找到了，但状态禁止 resume
```

可以得到更准确的诊断。

## 8. `StoredThread`、`InitialHistory` 与 API `Thread` 不是同一类型

恢复链路里有几个名字很像的对象：

| 类型 | 所在层 | 主要用途 |
|---|---|---|
| `StoredThread` | Thread Store | 存储元数据、rollout path、history mode、可选历史 |
| `InitialHistory` | Protocol/Core 交接 | 告诉 Session 这是 New、Cleared、Resumed 还是 Forked |
| `ResumedHistory` | `InitialHistory` 载荷 | 原 conversation id、RolloutItem 列表、原 rollout path |
| `CodexThread` | Core 运行时 | 向 Session 提交操作、订阅事件、读取配置快照 |
| API `Thread` | App-server v2 | 给客户端看的 id、状态、turns 等投影 |

把它们画成方向：

```text
StoredThread + rollout
        │
        ▼
InitialHistory::Resumed(ResumedHistory)
        │
        ▼
Session / CodexThread
        │
        ├────────> 后续事件流
        │
        └────────> API Thread 投影
```

`InitialHistory` 是非常重要的“语义标签”。如果只传 `Vec<RolloutItem>`，下游无法知道应该复用 ID，还是创建 fork 的新 ID。

## 9. Legacy 与 Paginated 两种历史模式

固定提交中同时支持两类历史模式。

### 9.1 Legacy

恢复时通常加载完整 rollout items，再交给 Core 重建。

优点是路径直接；缺点是历史越长，启动时读取越多。

### 9.2 Paginated

UI 的完整 Turn 列表与模型继续推理所需的上下文被拆开处理：

```text
模型恢复：load_latest_model_context
UI 历史：分页读取 / SQLite materialized projection
```

`load_latest_model_context` 不必总从第一行正向读取到最后。对于普通 paginated JSONL，它会：

1. 读取 canonical `SessionMeta`；
2. 沿 rollout lineage 从最新 segment 向前扫描；
3. 寻找足够恢复的最新 compaction checkpoint 与 Turn 元数据；
4. 找到有界截止点就停止；
5. 返回 `SessionMeta + 必要尾部`。

这解释了一个关键区别：

> “恢复模型上下文”不等于“把整个 UI 历史全部载入内存”。

`excludeTurns` 正是便宜恢复路径的一部分：客户端暂时不要 turns 时，App-server 避免只为了响应展示而重建完整旧历史。

## 10. 为什么反向扫描，而不是从头读到尾

假设 rollout 有一百万行，中间已经有一个完整 replacement history：

```text
很早的 90 万行
-> Compacted(replacement_history=完整的新基底)
-> 最近 10 万行
```

对于下一次模型请求，compaction 之前的逐条原历史通常已被 replacement history 取代。

反向扫描能先找到：

```text
最近的有效 checkpoint
+ checkpoint 之后的尾部
+ 恢复设置所需的最近有效 TurnContext
```

一旦这些事实齐全，更老的记录不再影响当前模型上下文，就可以停止。

这里的“可以停止”不是仅凭看到任意 `Compacted`。算法还要处理：

- checkpoint 是否带 `replacement_history`；
- 它所属 Turn 是否后来被 rollback；
- 最新有效用户 Turn 的设置是否已找到；
- world-state 恢复是否还需要更早的完整基线；
- fork lineage 是否跨多个物理 rollout segment。

## 11. Core 怎样重新建立模型历史

`Session::record_initial_history` 遇到 `InitialHistory::Resumed` 后调用：

```rust
apply_rollout_reconstruction(...)
```

后者再调用：

```rust
reconstruct_history_from_rollout(...)
```

重建不是简单的：

```rust
rollout_items
    .filter_map(ResponseItem)
    .collect()
```

因为它还要理解 compaction、rollback 和上下文基线。

### 11.1 第一遍：从新到旧找有效基底和元数据

反向扫描把记录按 Turn 划成 replay segment，收集：

- 最新仍然有效的 `replacement_history`；
- 上个有效用户 Turn 的模型设置；
- 最新有效 `TurnContextItem`；
- world-state 需要重放的记录；
- context window 编号与 ID；
- 尚需跳过多少个被 rollback 的用户 Turn。

### 11.2 第二遍：从旧到新建立精确历史

得到基底后，对必要尾部正向重放：

| RolloutItem variant | 对模型历史的处理 |
|---|---|
| `ResponseItem` | 记录进 `ContextManager` |
| `InterAgentCommunication` | 转成模型输入 item 后记录 |
| `Compacted` | 用 replacement history 替换旧历史；旧格式则重建摘要历史 |
| `ThreadRolledBack` | 删除最后 N 个用户 Turn |
| `EventMsg` 其他事件 | 不直接放入模型 history |
| `TurnContext` | 不作为聊天 item；用于恢复设置和比较基线 |
| `WorldState` | 不作为普通聊天 item；恢复 world-state baseline |
| `SessionMeta` | 不作为聊天 item；提供 session 级元数据 |

所以“rollout 里有这一行”和“模型下一次能看到这一行”完全不是同一个命题。

## 12. Compaction 不是一条普通摘要消息

新格式 `CompactedItem` 可以携带：

```rust
replacement_history: Option<Vec<ResponseItem>>
```

它表达的是：

```text
从这里开始，模型历史基底应替换为这批 ResponseItem
```

而不是：

```text
在旧历史末尾再追加一条摘要
```

如果恢复时把旧历史和 replacement history 都保留，模型会看到重复内容，token 也会膨胀。

旧 rollout 可能只有 compaction message、没有完整 replacement history。源码保留兼容路径：收集用户消息并构造压缩历史，但会清除无法可靠延续的 reference context baseline，下一次真实 Turn 再注入规范初始上下文。

这是一种保守策略：

> 无法证明旧比较基线仍正确时，宁可重新注入，也不基于错误基线生成 diff。

## 13. Rollback 为什么必须与元数据一起处理

假设用户回滚最后一个 Turn：

```text
Turn 1: model=M1, cwd=/repo-a
Turn 2: model=M2, cwd=/repo-b
ThreadRolledBack(num_turns=1)
```

如果只删除 Turn 2 的聊天消息，却仍把 Turn 2 的 `TurnContext` 当作“上次设置”，下一 Turn 会基于错误的模型和 cwd 做差异判断。

因此反向重建按 Turn segment 同时决定：

```text
这个 Turn 的 ResponseItem 是否存活
这个 Turn 的 previous settings 是否存活
这个 Turn 建立的 reference context 是否存活
这个 Turn 的 world-state 变化是否存活
```

测试名 `reconstruct_history_rollback_keeps_history_and_metadata_in_sync_for_completed_turns` 直接针对这个不变量。

注意 rollback 计数的是“用户 Turn”，而不是所有后台任务 segment。没有真实用户边界的内部 Turn 不应误消耗回滚次数。

## 14. `TurnContext` 恢复的不是聊天文字

`TurnContextItem` 保存某个真实用户 Turn 的有效运行设置，例如：

- `turn_id`
- `cwd`
- workspace roots
- 当前日期与时区
- approval policy
- sandbox / permission profile
- model
- comp hash
- personality
- collaboration mode
- reasoning effort

恢复算法从最新仍有效的用户 Turn 得到 `PreviousTurnSettings`：

```text
previous model
previous comp_hash
previous realtime_active
```

它还恢复 `reference_context_item`。下一 Turn 可以比较：

```text
之前已经向模型注入的上下文
vs
当前应该向模型注入的上下文
```

然后只生成必要的差异。

如果 reference 丢失或被 compaction 明确清除，系统会走完整初始上下文注入，而不是假装知道旧基线。

## 15. `WorldState` 为什么又是一套重放

world-state 记录有两种：

```text
full snapshot  建立完整基线
patch          在基线上应用 merge patch
```

恢复时必须按时间正序处理：

```text
full S0
patch P1
patch P2
=> 当前 baseline = merge(merge(S0, P1), P2)
```

遇到 compaction 时，旧 baseline 会被清除，因为新的上下文窗口可能重新建立比较基线。

异常策略也是保守的：

- patch 前没有 full snapshot：忽略并警告；
- snapshot 无法反序列化：清空 baseline；
- merge patch 应用失败：清空 baseline。

这避免下一 Turn 根据半损坏状态生成看似精确、实际错误的增量。

## 16. 从历史恢复 Session 身份

`Session::new` 对四种 `InitialHistory` 做身份选择：

```text
New / Cleared / Forked -> 生成新的 thread_id
Resumed                -> 使用 resumed conversation_id
```

这正是 resume 与 fork 的核心身份差异。

对于 `session_id`，源码还会查看 `SessionMeta`。根 Agent 通常让 session id 与 thread id 对齐；subagent 则需要保留其所属 session tree 的关系。

不要把这几个 ID 都叫“对话 ID”：

| ID | 回答的问题 |
|---|---|
| thread id | 这是哪一条可独立恢复的任务？ |
| session id | 它属于哪棵协作会话树？ |
| turn id | 这是任务里的哪一次用户工作单元？ |
| request id | 这是连接上的哪次 RPC 请求？ |

## 17. 配置恢复：历史值、持久化元数据和显式覆盖

冷恢复不是原封不动冻结所有旧配置。App-server 重新调用与新建 thread 相近的 config 加载逻辑，同时提供历史 cwd 和多种覆盖。

教学性优先级可理解为：

```text
当前配置层与默认值
+ 从历史 / state DB 恢复的特定元数据
+ 请求明确给出的 resume override
```

但不要把这句话机械应用到每个字段；不同字段有专门合并逻辑。

确定的例子包括：

- 没有显式 cwd 时，用历史里的 cwd 作为配置加载上下文；
- 持久化 approval policy 可以恢复；
- 请求显式 approval policy 会胜过持久化值；
- approvals reviewer 有专门的合并逻辑；
- model / reasoning effort 会判断请求是否明确覆盖；
- base instructions、dynamic tools 可从 `SessionMeta` 恢复。

### 模型变化

如果上次有效 Turn 使用 `M1`，恢复配置最终选择 `M2`，Core 会：

1. 写 warning 日志；
2. 发出用户可见 Warning event；
3. 在需要完整初始上下文时加入 `<model_switch>` developer message。

它不是禁止切换，而是明确告诉使用者与新模型：历史由另一个模型产生，行为可能变化。

## 18. 恢复持久化 writer：继续同一卷记录

`Session::new` 在恢复分支构造：

```rust
ResumeThreadParams {
    thread_id: resumed_history.conversation_id,
    rollout_path: resumed_history.rollout_path.clone(),
    history: Some(resumed_history.history.clone()),
    ...
}
```

随后：

```text
LiveThread::resume
-> ThreadStore::resume_thread
-> LocalThreadStore 的 live_writer::resume_thread
-> RolloutRecorder::new(RolloutRecorderParams::resume(path))
```

这里不是创建空文件后复制旧内容，而是打开同一个 rollout path 进入追加模式。

### 为什么必须防止两个 writer

本地实现先取得 thread 级锁，并调用：

```text
ensure_live_recorder_absent(thread_id)
```

还会取得 writer ownership lock。目标不变量是：

```text
一个 thread 在当前所有权范围内只有一个活动 recorder
```

相关 App-server 测试分别覆盖 legacy 与 paginated rollout 被另一个进程持有时拒绝 resume。

### ordinal 怎样继续

Paginated rollout 的每条持久化记录带单调 `ordinal`。恢复 recorder 时要从现有记录确定下一个位置，而不能重新从 1 开始。

测试 `resumed_paginated_rollout_continues_after_ordinal_gap` 说明：即使历史 ordinal 中间有空洞，追加也依据安全的已有尾部继续，而不是把“记录数量”误当作最后序号。

### 不完整文件尾部

进程可能恰好在写一行 JSON 时崩溃：

```text
完整 JSONL 行\n
{"timestamp": ..., "ordinal": 5, "type": "response_i
```

恢复 writer 必须先修复不安全尾部，再追加新行。`resumed_paginated_rollout_repairs_unsafe_tail` 检查追加后文件以换行结束，且 ordinal 序列符合预期。

## 19. 状态数据库不是模型历史的唯一真相

固定提交中的注释明确说：

```text
Paginated JSONL is canonical,
but its SQLite projection can lag after a previous write failure.
```

可以翻译为：

```text
JSONL 是耐久权威记录；SQLite 是为了查询和展示而物化的投影，可能暂时落后。
```

因此 paginated cold resume 打开 writer 后，会调用 `persist_thread`，让 durable rollout 重新投影到 SQLite，再用它补充 legacy response hydration 或完整 Turn 视图。

不要误解为 SQLite 不重要。它承担：

- thread 列表与快速查询；
- 持久化元数据；
- materialized Turn / Item；
- 分页游标相关读取。

但当 JSONL 与投影因先前失败而短暂不一致时，源码明确选择从 durable JSONL 重建投影。

## 20. UI 投影为什么不能直接作为模型历史

UI 希望看见：

```text
Turn
  ├─ 用户消息 Item
  ├─ 命令执行 Item（状态、输出摘要）
  ├─ 文件修改 Item
  └─ Assistant 消息 Item
```

模型需要的是 Responses API 兼容的输入序列：

```text
user message
assistant tool call
tool output
assistant message
...
```

两边的形状和目的都不同。例如 UI 可以把流式 delta 合并成一个最终气泡；模型历史必须保持 tool call 与 tool output 的配对关系。UI 还会把未完成的旧 Turn 显示为 Interrupted，这不等于要向模型添加一句“Interrupted”。

所以正确方向是：

```text
rollout facts
  ├─> model context projection
  ├─> API Thread/Turn/Item projection
  ├─> token usage/status projection
  └─> SQLite query projection
```

而不是：

```text
UI text -> model context
```

## 21. 未完成 Turn 怎样呈现

如果 rollout 最后一个 Turn 只有 Started、没有正常终态，应用重启后不能继续假装它仍由旧进程执行。

冷恢复时：

- Session 重建可以根据最后事件恢复 Interrupted agent status；
- App-server 构造 Thread 投影时把 stale in-progress Turn 调整为 interrupted；
- 但 running-thread rejoin 不应把真正仍在执行的 Turn 错改为 interrupted。

这就是为什么函数常带类似：

```text
has_live_in_progress_turn
```

的事实，而不是只看存储记录里的最后状态。

持久化记录告诉你“上次写到哪里”；内存运行对象告诉你“现在是否仍活着”。恢复视图需要二者结合。

## 22. listener 为什么要在响应附近建立

恢复不只要返回旧快照，还要接住响应之后的新事件。

冷恢复成功后，App-server 调用：

```text
ensure_conversation_listener(thread_id, connection_id, ...)
```

热路径则确保已有 listener task 正常，并通过 command channel 让它在事件顺序边界上发送 resume response。

热路径不直接在普通 handler 中随意读取“当前状态后再订阅”，因为可能出现：

```text
读取快照
  [这里恰好产生一个事件]
开始订阅
```

中间事件可能漏掉。运行中的 resume 需要让 snapshot、active Turn 和后续 stream 在同一 listener 协调点对齐。

这也是 `thread_resume_keeps_in_flight_turn_streaming` 与 `thread_resume_rejoins_running_paginated_thread_with_initial_page` 等测试所关心的边界。

## 23. response 里为什么还有那么多恢复信息

`ThreadResumeResponse` 不只返回 `thread`，还返回：

- model 与 model provider；
- service tier；
- cwd 与 runtime workspace roots；
- instruction sources；
- approval policy 与 approvals reviewer；
- sandbox 与 active permission profile；
- reasoning effort；
- initial turns page；
- turns/items backwards cursor。

原因是客户端恢复后要立即知道：

```text
我连接的是哪条任务？
当前有效配置是什么？
历史从哪里开始分页？
下一次 Turn 应使用什么状态？
```

响应成功后还可能定向补发恢复的 token usage，让状态栏在下一 Turn 开始前就正确。但 `excludeTurns` 是便宜路径，因此不会为了找 usage 属于哪个 Turn 而强制重建全部历史。

## 24. resume 为什么不立即更新 `updatedAt`

恢复可能只是用户打开旧任务查看，并没有产生新的业务活动。

如果每次打开都更新时间：

```text
最近修改排序
```

就会被“浏览”污染，旧任务会无故跳到列表顶部。

固定提交的集成测试 `thread_resume_defers_updated_at_until_turn_start` 验证：

```text
resume
-> updatedAt 不变

随后 turn/start
-> updatedAt 更新
```

rollout 文件修改时间也遵循相同精神：打开 recorder 本身不应伪造一条新业务记录；真正追加项目后才发生持久化变化。

## 25. 热路径中的 override 为什么可能被忽略

假设已有 Session 正被客户端 1 使用，客户端 2 请求：

```text
thread/resume(thread=A, model=M2)
```

现有 Session 实际配置是 M1。不能在第二个客户端加入时偷偷把所有观察者共享的运行对象改成 M2。

源码会收集 mismatch：

- 如果对象只是无人订阅、空闲的缓存项，可以先安全 shutdown，再走冷恢复创建替代 Session；
- 如果仍有人订阅、仍在运行，或 shutdown 没完成，则保留 rejoin 语义，记录 warning，并忽略不兼容 override。

因此：

> resume override 是冷恢复配置输入，不是任意改写已共享活动 Session 的命令。

如果请求在 thread 正运行时还携带 `history`，则直接拒绝，因为它试图给同一活动身份指定另一套内存历史。

## 26. 失败分类：看到错误时该查哪一层

| 错误现象 | 首先检查 | 可能含义 |
|---|---|---|
| thread is closing | App-server 生命周期 | unload 尚未完成，应重试 |
| invalid session id | API 参数解析 | thread id 格式不合法 |
| archived | Thread Store / App-server | 记录存在，但必须先 unarchive |
| no rollout found | Thread Store 查找 | ID/path 没有可恢复记录 |
| stale/mismatched path | 热路径身份校验 | 请求 path 与活动 writer 不同 |
| writer owned by another process | persistence ownership | 另一进程仍持有记录写权 |
| failed to scan model context | JSONL/lineage | rollout 损坏或读取失败 |
| config load error | ConfigManager | 历史 cwd 或 override 下的配置无效 |
| required MCP init failure | Session 初始化 | 历史已找到，但运行依赖无法建立 |
| listener channel closed | App-server thread state | 已加载对象与 listener 管理状态不一致 |

“历史能读出来”不代表 resume 一定成功。恢复还要重新建立配置、MCP、工具、持久化 writer、listener 等运行依赖。

## 27. 源码逐段阅读路线

第一次对照源码时，建议按这个顺序，而不是从单个大函数头读到底。

### 第一步：公开入口与两路分叉

```text
thread_processor.rs
  thread_resume
  thread_resume_inner
  resume_running_thread
```

先只回答：什么时候 `Handled`，什么时候进入 cold resume。

### 第二步：存储查找与模型上下文加载

```text
thread_processor.rs
  read_stored_thread_for_resume
  load_resume_initial_history_from_stored_thread

thread-store/local/model_context.rs
  load_latest_model_context
  scan_model_context_from_lineage_blocking
```

回答：Legacy 为什么加载完整历史，Paginated 为什么只加载必要模型尾部。

### 第三步：身份与 Session 创建

```text
thread_manager.rs
  resume_thread_with_history
  spawn_thread

session/session.rs
  Session::new
```

回答：哪条路径复用 ID，哪条路径生成新 ID，持久化初始化怎样分支。

### 第四步：模型历史重建

```text
session/mod.rs
  record_initial_history
  apply_rollout_reconstruction

session/rollout_reconstruction.rs
  reconstruct_history_from_rollout
```

回答：ResponseItem、Compacted、Rollback、TurnContext 和 WorldState 各自怎样处理。

### 第五步：续写与响应投影

```text
thread-store/local/live_writer.rs
  resume_thread

thread_processor.rs
  resume response 构造
```

回答：为什么继续写同一路径；为什么 response history 与 model history 不共用一个简单 Vec。

## 28. 测试究竟证明了什么

### App-server 集成测试

`codex-rs/app-server/tests/suite/v2/thread_resume.rs` 中值得先读：

| 测试 | 能证明什么 |
|---|---|
| `thread_resume_returns_rollout_history` | 冷恢复能把持久化历史投影到响应 |
| `thread_resume_can_skip_turns_for_metadata_only_resume` | `excludeTurns` 不必返回完整 Turn 历史 |
| `thread_resume_and_read_interrupt_incomplete_rollout_turn_when_thread_is_idle` | 无 live 执行时，旧未完成 Turn 显示为 interrupted |
| `thread_resume_defers_updated_at_until_turn_start` | resume 本身不算新业务活动 |
| `thread_resume_keeps_in_flight_turn_streaming` | 热恢复能继续接收活动 Turn 流 |
| `thread_resume_rejects_history_when_thread_is_running` | 活动身份不能被另一批 history 替换 |
| `thread_resume_rejects_mismatched_path_for_running_thread_id` | 活动 ID 与 rollout path 必须一致 |
| `thread_resume_uses_path_over_non_running_thread_id` | 冷恢复时 path 可以指定来源 |
| `thread_resume_preserves_persisted_approval_policy` | 特定持久化配置会恢复 |
| `thread_resume_approval_policy_override_wins_over_persisted_policy` | 显式 override 的优先级 |
| `thread_resume_rejects_*_writer_owned_by_another_process` | 单 writer ownership 不变量 |

### Core 重建测试

`codex-rs/core/src/session/rollout_reconstruction_tests.rs` 重点验证：

- rollback 同时删除历史和相应元数据；
- incomplete Turn 的 segment 边界；
- compaction 后 reference context 是否清除或重建；
- world-state 从最新窗口正确重放；
- 没有真实用户消息的 Turn 不错误计入 rollback；
- 旧格式 compaction 的保守兼容行为。

### Thread Store / rollout 测试

`model_context_tests.rs` 验证反向扫描只返回足够的模型上下文，并在没有受支持 checkpoint 时退化为完整历史。

`recorder_tests.rs` 验证：

- resume 后 ordinal 继续；
- ordinal gap 不导致编号倒退；
- 不完整尾部先修复再追加；
- legacy rollout 不凭空出现 ordinal；
- subagent 的继承前缀必须完整。

测试组合起来才能证明完整 resume；任何单个测试都没有覆盖所有层。

## 29. 五个常见误解

### 误解一：rollout 就是聊天记录

错。它还包括运行事件、Turn 设置、压缩检查点、world-state 和元数据。

### 误解二：UI 能显示历史，模型就一定拿到了同样历史

错。UI 与模型使用不同投影，且 paginated 模式可分别加载。

### 误解三：每次 resume 都会创建新 Session

错。活动或已加载 thread 优先走 rejoin 热路径。

### 误解四：新建了 Session，所以 thread id 也会变

错。冷 resume 创建新运行对象，但 `InitialHistory::Resumed` 复用 conversation id。

### 误解五：打开旧任务就应该更新时间

错。resume 本身只是恢复/查看；开始新 Turn 才代表新的业务活动。

## 30. 用贯穿案例重新走一遍

回到第 2 节的 rollout。

### 情况 A：应用仍在后台，打开第二个窗口

```text
thread/resume(A)
-> ThreadManager 已有 A
-> 校验 path 与 override
-> 启动或复用 listener
-> 构造当前 Thread 视图
-> 第二个窗口继续收到 Turn 2 的流式事件
```

不会把 1—16 行重新安装进另一个 Session。

### 情况 B：应用完全退出后重启

```text
thread/resume(A)
-> Thread Store 找到 A 的 rollout
-> paginated loader 从尾部反向寻找 checkpoint
-> 构造 InitialHistory::Resumed(A, items, path)
-> Session::new 复用 thread id A
-> reconstruct 以第 10 行 replacement_history 为基底
-> 正向加入第 14、15 行等有效 ResponseItem
-> 第 16 行表明最后 Turn 已 aborted/interrupted
-> 恢复上一有效设置、world-state、token info
-> RolloutRecorder 以 resume(path) 打开同一文件
-> listener 附加
-> 返回 ThreadResumeResponse
```

下一次用户说“继续”时，系统不是把 UI 上所有文字复制给模型，而是使用上面得到的规范模型 history，加上新 Turn 的当前上下文更新。

## 31. 理解检查

先不看答案，试着回答：

1. 为什么运行中的 resume 不应该创建第二个 Session？
2. `InitialHistory::Resumed` 与 `InitialHistory::Forked` 在 ID 上有什么差异？
3. 为什么 `EventMsg::TurnComplete` 通常不直接成为模型输入？
4. compaction 的 replacement history 为什么是“替换”而不是“追加”？
5. rollback 为什么要同时影响历史与 Turn 设置？
6. paginated 模式为什么能不读取完整 UI 历史就恢复模型？
7. state DB 落后于 JSONL 时，哪一个是 canonical？
8. 冷恢复为什么还可能因为 MCP 初始化失败而失败？
9. 为什么 resume 后 `updatedAt` 不立即变化？
10. 为什么运行中的 thread 可能忽略 resume override？

### 参考答案

1. 否则同一身份会拥有两套状态和两个 writer。
2. Resumed 复用原 conversation id；Forked 生成新 thread id。
3. 它是生命周期事实，不是 Responses API 对话 item。
4. 它代表压缩后的完整新基底；追加会重复旧上下文。
5. 被删除 Turn 的设置和 baseline 也已失效。
6. 反向扫描找到最新有效 checkpoint 与必要尾部即可。
7. 当前实现明确以 paginated JSONL 为 canonical durable source。
8. resume 需要建立可运行 Session，不只是读取文件。
9. 查看/恢复不是新的业务修改，开始 Turn 才是。
10. 已有 Session 可能被其他客户端共享，不能被新加入者暗中改写。

## 32. 动手练习

### 练习一：给每行记录分类

对下面项目标出“模型历史 / 运行恢复 / UI 投影 / 多者”：

```text
SessionMeta
ResponseItem::FunctionCall
EventMsg::TurnComplete
TurnContext
WorldState::patch
Compacted(replacement_history)
EventMsg::TokenCount
```

重点不是唯一标签，而是说清楚每个消费者怎样使用它。

### 练习二：模拟一次 rollback

画出三个用户 Turn，在第三个 Turn 后追加：

```text
ThreadRolledBack(num_turns=2)
```

说明最终应保留哪些 ResponseItem、哪个 `PreviousTurnSettings`、哪个 reference context。

### 练习三：设计一个错误实现

故意写出下面错误算法的伪代码：

```text
读取所有 ResponseItem
忽略 Compacted 和 ThreadRolledBack
```

然后列出它会造成的三个用户可见问题。

## 33. 本篇局部术语表

| 英文 / 代码词 | 字面中文 | 在本篇中的实际含义 |
|---|---|---|
| resume | 恢复 / 继续 | 重新加入活动 thread，或从持久化记录创建可继续运行的 Session |
| rollout | 展开记录 / 运行记录 | 按时间追加的 JSONL 耐久事实流，不只是聊天文本 |
| cold resume | 冷恢复 | 内存中没有运行对象，从存储重建 Session |
| hot resume / rejoin | 热恢复 / 重新加入 | 复用已有 CodexThread，让新连接取得快照并订阅事件 |
| canonical | 规范的 / 权威的 | 冲突或投影落后时作为最终依据的数据来源 |
| projection | 投影 | 从同一事实集合选择并变换出特定用途的数据视图 |
| materialize | 物化 | 把事件/JSONL 计算成便于查询的 SQLite Turn/Item 记录 |
| hydration | 填充恢复 | 用持久化数据补齐一个运行对象或 API 响应所需字段 |
| `StoredThread` | 已存储任务 | Thread Store 返回的元数据、路径、模式与可选历史容器 |
| `InitialHistory` | 初始历史 | Core 启动 Session 时区分 New/Cleared/Resumed/Forked 的语义 enum |
| `ResumedHistory` | 恢复历史载荷 | 原 thread id、RolloutItem 和 rollout path 的组合 |
| `RolloutItem` | 运行记录项 | SessionMeta、ResponseItem、Compacted、TurnContext、WorldState、EventMsg 等联合类型 |
| `RolloutLine` | rollout 行 | timestamp、可选 ordinal 与一个 RolloutItem |
| `ResponseItem` | 模型响应项 | Responses API 兼容的消息、工具调用、工具输出等模型可见项目 |
| `SessionMeta` | Session 元数据 | thread/session 身份、cwd、来源、base instructions、dynamic tools 等头部事实 |
| `TurnContextItem` | Turn 上下文记录 | 当时有效的 cwd、model、权限、日期、模式等设置快照 |
| `PreviousTurnSettings` | 上一 Turn 设置 | 恢复后用于检测模型等变化的精简设置 |
| reference context | 参考上下文 | 上次已注入模型的上下文基线，用来计算后续 diff |
| world-state baseline | 世界状态基线 | full snapshot 加 patches 重放后的比较状态 |
| compaction | 压缩 | 用较短且等价的模型历史替换过长上下文 |
| checkpoint | 检查点 | 足以作为后续重放起点的持久化状态位置 |
| replacement history | 替代历史 | compaction 后应整体替换旧模型 history 的 ResponseItem 列表 |
| replay | 重放 | 按记录语义重新计算当前历史和状态，不等于重新执行工具副作用 |
| reverse scan | 反向扫描 | 从最新记录向旧记录查找有效 checkpoint 和恢复元数据 |
| replay segment | 重放片段 | 以 Turn 生命周期边界组织的一组 rollout items |
| rollback | 回滚 | 从有效历史中删除最近若干用户 Turn 及其恢复元数据 |
| lineage | 血缘链 | fork 或分段 rollout 之间的继承关系 |
| history mode | 历史模式 | Legacy 或 Paginated 的持久化/读取策略 |
| ordinal | 顺序号 | paginated rollout 中单调递增的记录位置 |
| unsafe tail | 不安全尾部 | 崩溃造成的缺换行或不完整 JSONL 尾部 |
| recorder | 记录器 | 向 rollout 追加并 flush/shutdown 的 writer 对象 |
| writer ownership | 写者所有权 | 保证同一 thread 不被多个活动进程同时续写的约束 |
| `LiveThread` | 活动持久化句柄 | Session 持有的 Thread Store 适配对象，负责 append/persist/flush/shutdown |
| `CodexThread` | Core thread 句柄 | 上层向 Session 提交 Op、读取事件和配置的运行时接口 |
| listener | 监听器 | 把 Core 事件转成 App-server 通知，并协调恢复快照与后续流 |
| `excludeTurns` | 排除 Turns | 请求只做较便宜的元数据恢复，不返回完整 Turn 列表 |
| initial turns page | 初始 Turn 页 | resume response 可选携带的第一批分页历史 |
| stale in-progress turn | 过期的进行中 Turn | 记录显示进行中，但已没有 live 执行者的旧 Turn |
| override | 覆盖值 | resume 请求明确给出的 model/cwd/权限等配置输入 |
| dynamic tools | 动态工具 | thread 开始时由客户端声明并持久化、恢复时默认继续使用的工具定义 |
| `updatedAt` | 更新时间 | 表示实际业务活动的排序时间；单纯 resume 不推进它 |

## 34. 最后压缩成一条心智模型

```text
thread/resume
  │
  ├─ thread 仍在内存？
  │    └─ 是：校验身份 -> 复用 Session -> listener 协调快照与事件
  │
  └─ 否：查找 StoredThread / rollout
       -> 加载必要 RolloutItem
       -> InitialHistory::Resumed
       -> 复用 thread id，创建新 Session
       -> compaction / rollback / context / world-state 重建
       -> 以 resume(path) 打开唯一 writer
       -> 生成独立的 UI Thread/Turn/Item 投影
       -> 附加 listener 并返回
```

记住三个“不等于”，就抓住了本篇核心：

```text
rollout 不等于 UI 聊天气泡
UI 历史不等于模型上下文
新建运行时 Session 不等于新建业务 thread
```

下一篇将进入[MCP tool call 生命周期](README.md)：远端工具怎样被发现、公开给模型、调用、返回结果，并在工具列表变化后刷新。

返回[源码精读系列目录](README.md)或[课程总目录](../README.md)。
