# 45：大规模重构、模块边界与渐进式迁移——不是把一个大文件切成十个小文件

第 33 章讲最小修复，第 34、35 章讲可审查 PR，第 44 章讲协议兼容。本章处理另一类真实工程任务：代码已经能工作，但一个核心模块承担了太多职责，修改越来越慢、冲突越来越多，也越来越难证明安全。怎样在不暂停产品开发、不一次重写全部调用点的情况下，逐步建立更清晰的边界？

> 源码基线：`4ee41929eaf4`。本章以当前仓库的 500/800 行指导、TUI 大型热点模块、`codex-core` API、deprecated type alias、`ForkSnapshot` 适配、`ToolInvocation` 双路径迁移、`motion` 架构测试以及独立测试文件为证据。官方 Codex 文档检索没有找到仓库内部重构方法的公开专题，因此具体工程规则以本提交源码和 `AGENTS.md` 为准。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分拆文件、模块化、解耦和架构迁移；
2. 用 change reason 而不只是行数识别模块边界；
3. 解释 cohesion、coupling、fan-in 和 fan-out；
4. 识别 god module、shotgun surgery 和 dependency cycle；
5. 判断新代码应该留在原模块、进入新 module，还是进入新 crate；
6. 设计最小且明确的 crate API；
7. 使用 facade、adapter、shim 和 deprecated alias 保护调用方；
8. 理解 expand–migrate–contract 在源码重构中的用法；
9. 先迁移 reader/caller，再切换 owner，而不做 big bang rewrite；
10. 使用 branch by abstraction 保持主干可运行；
11. 处理共享状态、所有权和异步 trait 的边界；
12. 避免为了“整洁”制造大量只用一次的 helper；
13. 将相关测试和类型文档移动到新的 owner 附近；
14. 用 characterization test 锁定重构前行为；
15. 用 architecture test 防止调用方绕过新边界；
16. 按依赖关系把大型 diff 拆成可审查阶段；
17. 判断何时应该停止抽象或回退方案；
18. 写出包含迁移地图和删除条件的重构计划。

---

## 2. 先说人话：搬厨房，不是把一只柜子锯成十块

假设一家餐厅的厨房、收银、库存和外卖调度全挤在一个房间。

最表面的“重构”是画几条线：

```text
大房间
  → 厨具区
  → 收银区
  → 库存区
```

但如果所有人仍能随手改库存、直接从收银台开火、外卖员可以绕过出餐检查，那么只是视觉上分区，责任没有改变。

真正的模块化需要：

```text
谁拥有数据
谁允许修改
通过什么入口协作
失败怎样表达
哪些内部细节不能被绕过
```

代码也一样：

> 把 2000 行复制到十个文件，可能只是让同一团耦合分散到十处。

---

## 3. 四个经常混在一起的词

### 3.1 File split

只把代码移动到不同文件。它可以改善导航，但不保证职责或依赖变化。

### 3.2 Modularization

把相关状态和行为放进明确 module，并限制外部访问入口。

### 3.3 Decoupling

减少两个部分对彼此具体实现的了解。例如调用方只依赖 trait 或 protocol type，不依赖整个运行时对象。

### 3.4 Architectural migration

改变长期所有权或依赖方向，例如让 TUI 从直接操作 Core 转向通过 app-server protocol 交互。

四者可能一起发生，也可能只发生一项。重构计划必须说明目标是哪一种。

---

## 4. 行数为什么是警报，不是自动裁决

当前仓库指导：

- 普通 Rust module 目标小于约 500 LoC（测试除外）；
- 超过约 800 LoC 的热点文件，应优先把新功能放进新 module；
- 非机械改动总变化通常不超过 800 行；
- 复杂逻辑改动更适合控制在 500 行内。

这些数字是 reviewability budget，不是“第 501 行一定错误”。

### 4.1 大文件真正的问题

- 多种需求持续修改同一区域；
- reviewer 无法一次装下完整状态机；
- merge conflict 频繁；
- 私有状态被所有方法随意访问；
- 测试只能构造巨大的宿主对象；
- 新功能总是顺手加进去。

### 4.2 当前热点说明什么

基线中：

```text
tui/src/app.rs                         约 1431 行
tui/src/chatwidget.rs                  约 2013 行
tui/src/bottom_pane/chat_composer.rs   约 12637 行
app-server/src/lib.rs                  约 1409 行
```

这不表示应在一个 PR 中把它们全部重写。恰恰相反：既有热点越大，新功能越应在旁边建立清晰 owner，并让原文件只保留 orchestration。

---

## 5. 用“变化原因”寻找边界

好的 module 内部代码通常因为同一类原因一起变化。

例如 ChatWidget 周围可以看到：

```text
streaming
interrupts
tool_requests
permissions_menu
model_popups
transcript_export
usage
```

如果“修改权限弹窗”不应该迫使你理解流式 Markdown 合并，那么它们适合成为不同 owner。

### 5.1 Change reason 问题

对一组函数逐个问：

1. 哪种产品需求会修改它？
2. 它读写哪些状态？
3. 它必须和哪些 invariant 一起变化？
4. 哪些测试最直接证明它？

答案相近的代码有高 cohesion；答案完全不同却放在一起，通常是边界信号。

---

## 6. Cohesion 与 Coupling

### 6.1 Cohesion：模块内部是否围绕同一职责

高 cohesion 的 `motion` 模块集中处理：

- 是否允许动画；
- reduced-motion fallback；
- activity indicator；
- shimmer 文本。

它们共同维护一个不变量：关闭动画时，不应通过别的路径重新产生时间变化的视觉效果。

### 6.2 Coupling：模块之间需要知道多少

耦合不仅是 `use` 数量，还包括：

- 直接访问对方内部字段；
- 共享同一个巨型 context；
- 必须按隐藏顺序调用多个方法；
- 复制对方 enum 的 match；
- 测试必须启动完整系统才能验证小逻辑。

目标不是“零耦合”，而是让必要依赖显式、单向、稳定。

---

## 7. Fan-in 与 Fan-out

### 7.1 Fan-in

有多少调用者依赖这个模块。

高 fan-in 的公共类型移动风险大，因为许多调用点和外部 crate 都会受影响。

### 7.2 Fan-out

这个模块又依赖多少其他模块。

高 fan-out 的 orchestration module 可能合理；但一个“纯格式化 helper”如果依赖 Core session、config、network 和 state DB，说明边界泄漏。

### 7.3 组合判断

| 情况 | 可能含义 |
|---|---|
| 高 fan-in、低 fan-out | 稳定基础 API，修改要谨慎 |
| 低 fan-in、低 fan-out | 容易提取或替换 |
| 高 fan-in、高 fan-out | 架构枢纽或 god module，高风险 |
| 低 fan-in、高 fan-out | 专用 orchestration，需防止继续膨胀 |

---

## 8. Orchestration 与 Policy 要分开

Orchestration 决定步骤怎样连接：

```text
收到事件
  → 找到目标 thread
  → 调用状态转换
  → 触发渲染
```

Policy 决定“应该怎么选”：

```text
何时允许动画
何时需要审批
怎样选择 history mode
失败是否重试
```

如果一个大函数同时做路由、解析、策略、IO 和渲染，任何规则变化都会触碰整个流程。

更易测试的形状是：

```text
orchestrator
  ├─ 调用纯 policy 决策
  ├─ 调用 owner 修改状态
  └─ 调用 adapter 执行副作用
```

---

## 9. `mod`、`pub(crate)` 与 `pub use` 是边界工具

Rust module 默认私有。当前仓库倾向：

```rust
mod thread_manager;
pub use thread_manager::ThreadManager;
```

外部 crate 看见 `codex_core::ThreadManager`，但看不见 `thread_manager` 内部全部实现。

### 9.1 为什么不直接 `pub mod`

`pub mod` 会让所有内部路径都可能成为调用方依赖：

```text
codex_core::thread_manager::internal_detail
```

未来移动实现就更难。私有 module + 精确 re-export 能保持小 API surface。

### 9.2 `pub(crate)`

只允许当前 crate 使用。它适合内部协作，但不要把所有内容都升为 `pub(crate)` 来绕过边界设计。

---

## 10. 什么时候只建新 Module

适合 module 的情况：

- 与宿主共享大量 crate-private 类型；
- 生命周期与宿主一致；
- 不需要独立复用或编译；
- 提取目标主要是职责和导航；
- 新边界不需要反转 crate 依赖。

例如 TUI 的 `chatwidget/interrupts.rs` 仍属于 TUI，但把中断职责从中央 `chatwidget.rs` 移到相关文件。

### 10.1 Module 提取最小阶段

第一阶段可以只做机械移动：

```text
不改行为
不改 wire API
保留原调用方式
移动实现和相关测试
```

这样 reviewer 可以主要验证“搬家是否完整”。行为改进留给下一阶段。

---

## 11. 什么时候应该新建 Crate

适合新 crate 的信号：

- 多个上层 crate 都需要同一能力；
- 能力不应依赖 `codex-core` 的巨大运行时；
- 需要独立测试或复用；
- 依赖方向需要被编译器强制；
- API 可以足够小且语义稳定。

当前仓库明确提醒：`codex-core` 已经很大，引入新概念前应先问能否放进现有专用 crate，或创建新 crate，而不是继续让所有功能都依赖 Core。

### 11.1 新 crate 的成本

- Cargo workspace 登记；
- Cargo/Bazel build 文件；
- dependency/lock 更新；
- API 设计和文档；
- 跨平台构建；
- 版本与发布关系。

所以“代码只有 80 行”不自动值得一个 crate。边界价值必须高于维护成本。

---

## 12. 依赖方向比目录层级更重要

理想方向常类似：

```text
UI / app-server
    ↓
application orchestration
    ↓
protocol / domain types
    ↓
small utilities
```

底层 crate 不应为了方便反向依赖 UI 或巨大 Core。

### 12.1 Dependency inversion

如果底层流程需要上层提供行为，可以让底层定义它真正需要的窄接口：

```rust
trait EventSink {
    fn send(&self, event: Event) -> impl Future<Output = Result<()>> + Send;
}
```

上层实现 trait。这样底层依赖抽象，不依赖某个 UI object。

当前仓库不鼓励用 `#[async_trait]` 隐藏成本；新增 trait 应写角色文档，并优先使用带明确 `Send` bound 的原生 RPITIT future。

---

## 13. 不要把巨型 Context 当成模块接口

常见伪解耦：

```rust
fn handle(context: &EverythingContext)
```

函数虽然移到新文件，仍能访问 session、config、network、UI、store 和所有 mutable state。

更清楚的边界是传入真正需要的值：

```rust
fn resolve_motion_mode(animations_enabled: bool) -> MotionMode
```

或者传入语义化 snapshot：

```rust
fn decide(input: ApprovalDecisionInput) -> ApprovalDecision
```

输入越窄，隐式耦合越少，测试越容易。

---

## 14. 数据 Owner 必须唯一

拆模块时最危险的状态是两边都认为自己拥有同一份数据。

```text
旧模块 state A
新模块 state B
两个方向互相同步
```

如果没有 source of truth，就会出现：

- 一边更新、一边漏更；
- 事件顺序导致覆盖；
- 恢复时选择错误副本；
- 测试只覆盖同步成功路径。

迁移期可以存在双表示，但必须写清：

```text
canonical owner 是谁
另一份是 projection 还是 compatibility field
同步方向是什么
删除条件是什么
```

---

## 15. 实例一：Deprecated Type Alias

`codex-rs/core/src/lib.rs` 当前包含：

```rust
#[deprecated(note = "use ThreadManager")]
pub type ConversationManager = ThreadManager;

#[deprecated(note = "use NewThread")]
pub type NewConversation = NewThread;

#[deprecated(note = "use CodexThread")]
pub type CodexConversation = CodexThread;
```

### 15.1 它解决什么问题

内部概念已经从 conversation 命名迁到 thread，但旧调用点不必在同一提交全部修改。

```text
旧名字 ──alias──> 新 canonical type
```

### 15.2 为什么标 deprecated

只保留 alias 而不发信号，会让新代码继续使用旧名字。deprecated warning 把迁移方向写进编译反馈。

### 15.3 Alias 的限制

它适合语义仍等价的 rename。如果新旧类型行为已经不同，type alias 会掩盖差异，应使用 adapter 或显式转换。

---

## 16. 实例二：用转换保护旧调用点

`ThreadManager` 的 fork API 需要更明确的 `ForkSnapshot`：

```rust
enum ForkSnapshot {
    TruncateBeforeNthUserMessage(usize),
    Interrupted,
}
```

为保留旧 `fork_thread(usize, ...)` 调用方式，当前实现提供：

```rust
impl From<usize> for ForkSnapshot {
    fn from(value: usize) -> Self {
        Self::TruncateBeforeNthUserMessage(value)
    }
}
```

### 16.1 演进意义

旧 API 只有一个数字，数字含义隐晦；新 enum 为未来模式建立了自解释空间。

迁移顺序可以是：

1. 新增 enum；
2. 接口接收 `impl Into<ForkSnapshot>`；
3. 旧数字调用继续编译；
4. 新调用点使用明确 variant；
5. 迁移完成后再考虑收紧接口。

### 16.2 不要滥用 `Into`

如果转换有失败、歧义或副作用，应使用 `TryFrom` 或命名构造函数。兼容适配不能牺牲语义清楚。

---

## 17. 实例三：Compatibility Field 双路径迁移

`ToolInvocation` 当前同时包含：

```rust
pub turn: Arc<TurnContext>,
pub(crate) step_context: Arc<StepContext>,
```

注释说明旧 `turn` 字段会在 handlers 迁移到 `step_context.turn` 后删除。router 在构造时确保：

```rust
let turn = Arc::clone(&step_context.turn);
```

### 17.1 这是一个 strangler 迁移

```text
旧 handler → invocation.turn
新 handler → invocation.step_context.turn
                    ↑
          两者暂时来自同一 source of truth
```

它允许 handlers 分批迁移，不需要一个巨大 PR 同时修改所有工具。

### 17.2 迁移期不变量

- 两个路径必须指向同一个 turn state；
- 新代码不得只更新其中一份；
- 每迁移一个 handler 都应移除旧访问；
- 搜索结果归零后删除 compatibility field；
- 删除时补测试证明没有语义变化。

### 17.3 迁移注释需要退出条件

“以后删除”不够。好的迁移注释应能回答：谁迁移完、用什么搜索验证、哪项兼容承诺结束后删除。

---

## 18. 实例四：Architecture Test 固守新边界

TUI 的 `motion.rs` 集中动画与 reduced-motion fallback，并有测试扫描 TUI Rust 文件：

```text
除 motion.rs / shimmer.rs 外
不得直接调用 spinner(...)
不得直接调用 shimmer_spans(...)
```

发现绕过时，错误会提示使用 `crate::motion`。

### 18.1 为什么普通单元测试不够

单元测试能证明 `motion` 自己正确，却不能阻止未来开发者在另一个文件直接调用旧 primitive。

Architecture test 验证的是依赖规则：

```text
所有调用方 → motion policy → primitive
```

### 18.2 何时值得写架构测试

- 边界容易被“方便地”绕过；
- 绕过不会导致编译失败；
- 全仓库搜索规则简单稳定；
- 违规后果重要，例如 accessibility、安全或 telemetry。

不要用脆弱文本扫描约束复杂语义；能用类型可见性和 crate dependency 强制时，优先让编译器完成。

---

## 19. Strangler Pattern

Strangler 的意思不是立即移除旧系统，而是在它旁边建立新路径，并逐步接管流量。

```text
入口 facade
  ├─ 未迁移场景 → legacy implementation
  └─ 已迁移场景 → new implementation
```

随着覆盖扩大：

```text
legacy 100% → 70% → 20% → 0% → 删除
```

关键是路由条件可观测、可测试，而且不会让新旧实现同时产生副作用。

---

## 20. Branch by Abstraction

当不能用长期 feature branch 停止主干开发时，可以：

1. 在旧实现前引入稳定 abstraction；
2. 让旧实现成为第一个 implementation；
3. 修改调用方只依赖 abstraction；
4. 并行实现新版本；
5. 通过配置/feature 切换；
6. 对照验证；
7. 切默认；
8. 删除旧实现和临时 abstraction。

这里的 abstraction 应服务于真实替换边界，不是提前猜测所有未来需求的万能 trait。

---

## 21. Expand–Migrate–Contract 用在源码里

第 44 章用它迁移 wire protocol；源码重构也相同。

### Expand

- 增加新 module/type/API；
- 旧 API 通过 alias/adapter 继续工作；
- 行为仍由单一实现负责。

### Migrate

- 按 owner 或调用簇分批迁移；
- 每批保持测试通过；
- 新调用点禁止使用旧路径；
- 记录剩余引用。

### Contract

- 删除旧字段、alias、adapter 和 feature flag；
- 收紧可见性；
- 删除只为迁移存在的测试；
- 保留新边界的行为和架构测试。

Contract 不是可选清理；永不收缩会让系统永久承担两套概念。

---

## 22. 最小可落地阶段怎样选

change-size 指导要求非机械 diff 通常不超过 800 行，复杂逻辑尽量低于 500 行。超过时应根据实际依赖拆阶段。

### 22.1 好阶段的特征

- 单独合并仍有价值；
- 主干持续可构建、可测试；
- 没有半写入的数据格式；
- reviewer 能明确说出不变量；
- 回退该阶段不会依赖未来提交。

### 22.2 常见顺序

```text
PR 1：characterization tests
PR 2：引入新 type/module，旧行为不变
PR 3：迁移一组调用方
PR 4：迁移剩余调用方并切默认
PR 5：删除 legacy 路径和收紧 API
```

第一阶段往往不是“创建空框架”，而是能独立降低下一步风险的测试或类型边界。

---

## 23. Mechanical Move 与 Behavior Change 分开

一个 PR 同时做：

- 移动 1500 行；
- 重命名类型；
- 改错误处理；
- 改异步并发；
- 更新 UI；

reviewer 很难知道 diff 是搬家还是新逻辑。

更清晰的阶段：

```text
A. 纯移动，保留 git 可追踪性和行为
B. 在新 owner 内重命名/收紧 API
C. 单独改变行为并补回归测试
```

机械阶段可以超过一般行数预算，但必须真的机械；不能用“主要是移动”隐藏复杂行为变化。

---

## 24. Characterization Test

当旧代码行为复杂、文档不足时，先用测试记录当前 observable behavior。

它回答：

> 在重构之前，系统现在到底做什么？

例如提取 TUI event routing 前，固定：

- 哪个 thread 收到事件；
- inactive thread 是否更新；
- notification 顺序；
- terminal event 后状态；
- snapshot 输出。

Characterization test 不表示旧行为一定理想。它先防止无意变化；如果要改行为，再用独立 PR 和产品理由明确修改。

---

## 25. 测试也要跟着 Owner 迁移

仓库指导：提取大型 module 时，把相关测试和 module/type docs 移到新 implementation 附近。

新建 test module 时使用同级文件：

```rust
#[cfg(test)]
#[path = "parser_tests.rs"]
mod tests;
```

这样：

- 实现文件不被数千行测试淹没；
- owner 与不变量靠近；
- 文件名说明测试对象；
- Bazel/Cargo 定位更清楚。

不要为了遵守新约定机械搬动所有既有 inline tests；只在实际提取或新增时控制范围。

---

## 26. UI 重构必须保留 Snapshot 证据

TUI 可见输出变化需要对应 `insta` snapshot 覆盖。

提取渲染模块时，即使目标是“无行为变化”，snapshot diff 也能暴露：

- 空格或换行改变；
- style span 丢失；
- wrap 宽度改变；
- loading/terminal state 顺序改变。

流程包括运行目标测试、查看 `.snap.new`、人工确认，再接受预期 snapshot。不要把大批 snapshot 更新当作机械噪声。

---

## 27. Shared State 的迁移风险

单线程纯函数容易移动；`Arc<Mutex<State>>`、channel 和 cancellation token 则会带来隐藏顺序。

### 27.1 常见风险

- 新 module 持锁跨 `.await`；
- 锁顺序改变产生 deadlock；
- channel sender clone 延长生命周期，receiver 永不关闭；
- 新旧路径都消费同一 event；
- cancellation 只到达其中一边；
- terminal cleanup 执行两次或零次。

### 27.2 迁移前先写所有权图

```text
谁创建 state
谁 clone handle
谁可以 mutate
谁发事件
谁消费
谁关闭
谁在取消时收尾
```

如果这张图画不出来，先不要移动并发代码。

---

## 28. API Shape：避免 bool 与模糊 Option

重构是改善内部 API 语义的机会，但应放在机械移动之后。

难读：

```rust
fork(3, false, None)
```

更清楚：

```rust
fork(ForkSnapshot::TruncateBeforeNthUserMessage(3), ForkPersistence::Persist)
```

仓库鼓励 enum、命名方法和 newtype，让调用点自解释。无法修改 API 时，小型位置字面量使用精确 `/*param_name*/` 注释。

---

## 29. 不要制造“只用一次的 Helper 森林”

为了把大函数变短，有人会创建几十个只调用一次、名字含糊的方法：

```text
handle_part_one
do_stuff
process_inner
finish_helper
```

行数分散了，读者却必须跨文件追踪局部变量和控制流。

当前仓库明确不鼓励创建只引用一次的小 helper。提取应至少带来一种真实价值：

- 独立职责；
- 收紧状态访问；
- 可复用语义；
- 独立测试；
- 清楚的错误边界；
- 降低调用方认知负担。

---

## 30. Facade、Adapter、Shim 的区别

### Facade

给复杂子系统提供一个窄入口：

```text
ThreadManager facade
  → store
  → session
  → models
```

### Adapter

把一种接口或数据转换成另一种，例如 `usize → ForkSnapshot`。

### Shim

短期兼容薄层，让旧调用继续工作，通常计划删除。

### Re-export

改变代码组织时保留原公共路径。

这些层应薄且单向。若 shim 开始拥有复杂业务状态，它已变成第二套实现。

---

## 31. Feature Flag 与 Shadow Path

高风险迁移可暂时保留两种实现，但要区分：

### Runtime switch

选择旧或新实现真正产生结果。必须定义默认、回滚和配置生命周期。

### Shadow execution

新实现旁路计算，只比较结果，不产生外部副作用。

```text
真实输出 ← old
比较日志 ← old vs new
```

Shadow 适合纯计算或可隔离读取；不能让两个路径都执行命令、发消息或写数据库。

---

## 32. Migration Telemetry

分批迁移需要回答：

- 新路径使用比例；
- 两路径结果差异；
- fallback 次数；
- 新路径错误率和延迟；
- legacy 调用还来自哪里。

Telemetry 应使用有界、低基数标签。不要把 thread ID、文件路径等高基数值直接作为 metric label。

迁移完成的证据不只是代码搜索归零，还包括真实运行中 legacy path 归零并经过足够观察窗口。

---

## 33. Crate Extraction 的最小步骤

假设从 `codex-core` 提取一个纯解析能力。

1. 列出要移动的类型、函数、测试和依赖；
2. 确认新 crate 不反向依赖 Core；
3. 定义最小 public API，其余保持 private；
4. 移动实现与测试；
5. Core 通过窄 API 调用；
6. 必要时在 Core 临时 re-export；
7. 更新 workspace `Cargo.toml` 和 `BUILD.bazel`；
8. 若依赖变化，运行 `just bazel-lock-update`；
9. 运行新 crate 和调用方测试；
10. 后续阶段迁移其他调用者并删除桥接。

如果使用 `include_str!`、`include_bytes!` 等编译期文件读取，还要更新 Bazel `compile_data`/相关 data，否则 Cargo 通过不代表 Bazel 通过。

---

## 34. 避免 Dependency Cycle

假设：

```text
core → new-crate
new-crate → core
```

Cargo 会拒绝 crate cycle。不要通过把更多东西塞进第三个“common”垃圾桶来敷衍。

先找真正的共享抽象：

- wire/domain type 可进入低层 protocol crate；
- callback 能力由低层 trait 表达；
- orchestration 留在上层；
- 通用 utility 只有在语义稳定且不依赖业务时才提取。

“common”如果什么都收，最终会成为新的 Core。

---

## 35. 保持 Exhaustive Match 的价值

模块迁移时，给 enum 增加 wildcard arm：

```rust
_ => {}
```

看似减少调用点修改，却会让新 variant 静默丢失。仓库更倾向 exhaustive match。

它让编译器在领域模型扩展时指出每个 policy owner。迁移期如果必须兼容 unknown wire value，应在边界层显式转换，不要让内部状态机到处忽略。

---

## 36. 大型重构的 Review 地图

PR 描述至少给出：

```text
Before ownership
After ownership
Dependency direction
Compatibility bridge
Behavior intentionally unchanged/changed
Test evidence
Remaining migration work
Deletion condition
```

Reviewer 应能快速区分：

- move-only 文件；
- API shape 变化；
- 行为变化；
- 生成文件；
- compatibility scaffolding。

如果所有内容混成一个 3000 行 diff，优先讨论如何拆阶段，而不是要求 reviewer 靠耐力猜风险。

---

## 37. 何时不该重构

- 还不能解释当前行为；
- 没有测试覆盖关键不变量；
- 正处在高风险发布或事故恢复期；
- 新边界只是审美偏好；
- 目标需求很小，重构扩大了不相关影响面；
- 两个并行大型迁移会修改同一 owner；
- 没有资源完成 contract/删除阶段。

可以先做更小工作：补 characterization test、加 telemetry、写 owner 文档、阻止新代码继续进入热点。

---

## 38. 什么时候应该停止继续抽象

抽象出现以下信号时应暂停：

- 调用方仍要知道所有 implementation 细节；
- trait 只有一个实现且没有边界价值；
- 参数对象不断增长成 EverythingContext；
- adapter 比真实业务更复杂；
- 为统一两个表面相似、语义不同的流程加入大量 bool；
- 测试更难写而非更容易；
- 错误信息失去领域上下文。

好的抽象压缩知识；坏的抽象只是把知识藏起来。

---

## 39. 一份渐进式重构模板

### Problem

哪个热点造成什么具体维护成本？用冲突、调用路径、状态所有权或测试困难说明。

### Invariants

哪些用户可见行为、协议、持久化和并发语义必须不变？

### Target boundary

新 owner 拥有什么数据和行为？明确不负责什么。

### Migration map

列出调用簇，不要只写“迁移所有调用方”。

### Compatibility bridge

alias、adapter、facade、feature flag 或双字段由谁维护？

### Stages

每个 PR 的独立价值、测试和回滚方式。

### Exit criteria

哪些搜索、测试和 telemetry 归零后可以删除旧路径？

---

## 40. 源码阅读路线

1. 根目录 `AGENTS.md`
   - 看模块 500/800 行、Core 膨胀、API、测试与 build 约定。
2. `codex-rs/core/src/lib.rs`
   - 看 private module、精确 re-export 和 deprecated alias。
3. `codex-rs/core/src/thread_manager.rs`
   - 看 `ForkSnapshot` 与 `From<usize>` 兼容转换。
4. `codex-rs/core/src/tools/context.rs`
   - 看 `ToolInvocation` 的 compatibility field。
5. `codex-rs/core/src/tools/router.rs`
   - 看新旧字段怎样绑定到同一 `TurnContext`。
6. `codex-rs/tui/src/motion.rs`
   - 看 centralized policy 与 architecture test。
7. `codex-rs/tui/src/chatwidget.rs` 及 `chatwidget/`
   - 看中央 orchestration 与大量职责模块。
8. `codex-rs/tui/src/app.rs` 及 `app/`
   - 看 app event/lifecycle/routing 的拆分形状。
9. `codex-rs/core/src/tools/`
   - 看 handlers、runtimes、registry、router 和 tests 的职责分组。
10. `codex-rs/*/Cargo.toml`
    - 看真实 crate 依赖方向，而不是只凭目录猜测。

---

## 41. 动手练习

### 练习 1：边界而非切文件

任选一个超过 800 行的模块，列出三种 change reason。选择一个候选 owner，并写明它不应访问的状态。

### 练习 2：迁移旧 API

把 `load(bool)` 设计成 enum API。给出兼容旧调用点的 adapter，以及最终删除 adapter 的条件。

### 练习 3：双字段不变量

为 `ToolInvocation.turn` 与 `step_context.turn` 画 source-of-truth 图，说明如果分别构造两个 `Arc<TurnContext>` 会发生什么。

### 练习 4：架构测试

设计一条“所有网络请求必须经过 policy module”的可执行约束。比较 visibility、trait 和源码扫描三种方案。

### 练习 5：拆 PR

把一个“提取模块 + 修改行为 + 增加 feature + 删除旧 API”的 1800 行 diff 拆成至少四个可独立落地阶段。

### 练习 6：Crate 判断

为一个 120 行 parser 判断应成为 module 还是 crate。不要只看行数，要列出消费者和依赖方向。

---

## 42. 理解检查

1. 为什么拆成多个文件不一定降低耦合？
2. 行数规则为什么是警报而不是裁决？
3. 什么是 change reason？
4. 高 cohesion 和低 coupling 分别说明什么？
5. orchestration 与 policy 为什么值得分开？
6. private module + precise re-export 有什么好处？
7. 什么时候新 crate 比新 module 更合适？
8. 巨型 context 为什么是伪解耦？
9. deprecated alias 适合哪类迁移？
10. `From<usize> for ForkSnapshot` 保护了什么？
11. compatibility field 为什么必须有单一 source of truth？
12. architecture test 与普通 unit test 的差别是什么？
13. expand–migrate–contract 的 contract 阶段为什么不可省略？
14. 什么是 characterization test？
15. 为什么机械移动和行为修改应分开？
16. shadow path 为什么不能产生外部副作用？
17. crate cycle 应怎样从边界上解决？
18. 一份重构计划的 exit criteria 应包含什么？

---

## 43. 本章词汇表

| 英文 | 字面翻译 | 在本章中的意思 |
|---|---|---|
| Refactor | 重构 | 保持或明确控制外部行为的内部结构改善 |
| Large-scale refactor | 大规模重构 | 横跨多个 owner/调用簇、需要分阶段迁移的结构变化 |
| Module | 模块 | Rust 可见性和职责组织单元 |
| Crate | 包/编译单元 | Cargo 管理、能强制依赖方向的 Rust 单元 |
| Boundary | 边界 | 数据所有权和允许交互方式的分界 |
| Cohesion | 内聚 | 一个模块内部代码是否围绕同一变化原因 |
| Coupling | 耦合 | 模块之间了解彼此实现和状态的程度 |
| Fan-in | 扇入 | 依赖某模块的调用方数量 |
| Fan-out | 扇出 | 某模块依赖的其他模块数量 |
| God module | 上帝模块 | 承担过多不相关职责的中央模块 |
| Hotspot | 热点 | 经常被不同需求修改、冲突和风险集中的区域 |
| API surface | API 表面 | 外部可调用、未来需要兼容的类型和函数集合 |
| Owner | 所有者 | 对某份状态和不变量负主要责任的模块 |
| Source of truth | 事实来源 | 发生冲突时被视为权威的唯一状态 |
| Projection | 投影 | 从权威状态派生、可重建的表示 |
| Orchestration | 编排 | 连接步骤、路由事件和协调生命周期 |
| Policy | 策略 | 决定允许、选择、优先级或降级规则 |
| Facade | 外观入口 | 为复杂子系统提供的窄而稳定的入口 |
| Adapter | 适配器 | 在新旧接口或数据形状间转换 |
| Shim | 垫片/兼容薄层 | 为迁移暂时保留的旧入口桥接 |
| Re-export | 重新导出 | 内部移动实现时保留或整理公共访问路径 |
| Deprecated | 已弃用 | 仍可用但编译器提示应迁移的 API |
| Compatibility field | 兼容字段 | 分批迁移期间暂时保留的旧访问路径 |
| Strangler pattern | 绞杀者模式 | 新实现逐步接管旧实现流量，最后删除旧路径 |
| Branch by abstraction | 通过抽象分支 | 在主干内通过稳定接口并存和切换新旧实现 |
| Characterization test | 特征测试 | 在重构前记录旧实现当前可观察行为 |
| Architecture test | 架构测试 | 验证代码依赖规则没有被绕过的测试 |
| Mechanical change | 机械改动 | 可逐项确认等价、原则上不改变行为的移动/重命名 |
| Behavioral change | 行为改动 | 改变用户、调用方或系统可观察结果的修改 |
| Migration map | 迁移地图 | 调用簇、顺序、桥接和剩余工作的清单 |
| Exit criteria | 退出条件 | 删除兼容层前必须满足的可验证条件 |
| Shadow execution | 影子执行 | 新路径旁路计算并比较，但不产生真实副作用 |
| Dependency inversion | 依赖倒置 | 上层实现由底层需要的窄接口，而非底层依赖上层具体类型 |
| Dependency cycle | 依赖环 | crate/module 互相依赖形成的闭环 |
| RPITIT | trait 中返回位置 `impl Trait` | 用显式 `Future + Send` 表达异步 trait 契约的 Rust 技术 |
| LoC | 代码行数 | Lines of Code，用作规模信号而非质量结论 |
| Reviewability | 可审查性 | reviewer 在有限上下文中验证改动正确性的程度 |

---

## 44. 本章小结

大规模重构的目标不是获得更多文件，而是得到更明确的所有权和更窄的依赖：

```text
旧的中央热点
  → 找 change reason
  → 建立新 owner 与窄 API
  → 用 alias/adapter/facade 保持主干可运行
  → 分批迁移调用方
  → 用行为测试和架构测试锁定边界
  → 删除 legacy 路径并收紧可见性
```

在 Codex 当前源码中可以看到四种互补做法：

- deprecated alias 让命名迁移不必一次完成；
- `From<usize>` 把旧调用适配到更清楚的 enum；
- `ToolInvocation` 临时双路径允许 handler 分批迁移，同时保持单一 turn source of truth；
- `motion` 的 architecture test 防止未来调用方绕过集中策略。

真正安全的渐进式重构需要同时控制三种预算：

1. **行为预算**：哪些 observable behavior 必须不变；
2. **依赖预算**：新边界允许知道哪些状态和类型；
3. **审查预算**：每个阶段是否小到 reviewer 能可靠验证。

当一个重构计划能够明确回答“新 owner 是谁、旧调用怎样继续、每阶段如何验证、什么条件下删除桥接”，它才不再是一场大爆炸式搬家，而是一条可持续落地的迁移路线。
