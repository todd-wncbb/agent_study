# 46：安全删除、废弃代码与技术债治理——“搜索不到调用”只是调查的开始

第 44 章讲协议演进，第 45 章讲如何建立新边界。本章继续完成迁移最后、也最容易被拖延的一步：删除旧路径。

删除代码看起来比新增代码简单。编译器会告诉你静态调用点，删掉文件还能减少行数。但真实系统中的消费者可能藏在配置字符串、JSON method、旧 Rollout、平台专有构建、生成 Schema、自动化脚本或尚未升级的客户端里。“当前 workspace 编译通过”只能证明其中很小一部分。

> 源码基线：`4ee41929eaf4`。本章以 removed feature key 的 no-op 兼容、deprecated app-server notification、sandbox legacy field 的接受/拒绝分流、removed config 对 config lock 的兼容、deprecated Rust alias、conditional `dead_code` 以及 schema fixture 清理为证据。官方 OpenAI 文档检索没有找到 Codex 仓库内部安全删除的公开专题，因此具体实现以本提交源码和仓库指导为准。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 解释为什么“没有静态引用”不等于“没有消费者”；
2. 区分 dead code、deprecated code、compatibility code 和 dormant code；
3. 找出 Rust 编译器看不到的动态入口；
4. 评估 app-server、CLI、config 和 Rollout 四类外部删除风险；
5. 区分停止调用、停止产生、停止读取和物理删除；
6. 为 feature flag 设计完整退休生命周期；
7. 判断旧输入应忽略、迁移、警告还是拒绝；
8. 理解为什么安全相关旧字段不能总是静默忽略；
9. 使用 telemetry 证明真实流量已经离开旧路径；
10. 评估平台、feature、test 和 Bazel 条件编译下的隐藏引用；
11. 判断 `#[allow(dead_code)]` 是合理边界还是债务掩盖；
12. 清理生成文件、Schema、文档和测试中的残留；
13. 区分应该删除的旧测试和必须保留的兼容性测试；
14. 防止删除后的旧路径再次被引入；
15. 建立包含 owner、利息、退出条件的技术债条目；
16. 用风险、频率和修复成本安排技术债优先级；
17. 避免“顺手清理”把小修复扩大成高风险改动；
18. 为删除 PR 提供可审查的证据链。

---

## 2. 先说人话：拆掉旧桥前，先确认没有车在桥上

城市修好一座新桥后，不能因为地图 App 默认推荐新桥，就立即炸掉旧桥。

还要确认：

- 路牌是否都已更新；
- 公交路线是否迁移；
- 老导航是否仍把车导向旧桥；
- 应急车辆有没有专用入口；
- 桥上是否还有车辆；
- 拆桥后能否快速回退；
- 旧入口是否会把司机引到断崖。

软件里的对应关系：

```text
新实现上线
  ≠ 旧实现已经无人使用
  ≠ 旧数据已经迁移
  ≠ 旧客户端已经升级
  ≠ 旧配置可以安全报错
```

安全删除的本质不是“删除动作”，而是证明旧路径已经失去职责。

---

## 3. 四种长得像“没用”的代码

### 3.1 Dead code

在所有受支持构建和运行路径中都不可达，也没有兼容责任。

它通常可以删除，但仍要检查生成、反射、注册表和外部边界。

### 3.2 Deprecated code

仍然可用，但调用者已被告知迁移到新 API：

```rust
#[deprecated(note = "use ThreadManager")]
pub type ConversationManager = ThreadManager;
```

它不是死代码，而是有计划结束的兼容 API。

### 3.3 Compatibility code

当前业务路径可能不主动使用，却负责读取旧配置、旧 JSON、旧 Rollout 或维护回滚能力。

### 3.4 Dormant code

默认关闭，但会在某个平台、feature、实验 cohort、恢复流程或故障降级时启用。

把 dormant 当 dead 删除，会在少见但重要的路径上失败。

---

## 4. “谁还在用”至少要查八类消费者

| 消费者 | 例子 | 编译器通常能发现吗 |
|---|---|---|
| Rust 静态调用 | 函数、类型、trait impl | 能发现较多 |
| 注册表/宏 | JSON-RPC method、tool registry | 只发现定义，不一定发现外部调用 |
| 字符串入口 | CLI flag、config key、event name | 不能证明外部使用 |
| 生成物 | TS、JSON Schema、proto | 可能在其他仓库消费 |
| 持久化数据 | Rollout、SQLite、config lock | 不能 |
| 平台构建 | Windows/macOS/Linux 专用代码 | 当前平台可能看不到 |
| 远程客户端 | 已发布 TUI、IDE、Cloud client | 不能 |
| 人工流程 | runbook、shell script、文档示例 | 不能 |

因此第一轮 `rg` 只是建立 inventory，不是删除许可。

---

## 5. 删除不是一个时刻，而是四个不同动作

假设旧字段叫 `legacyMode`。

### 5.1 Stop calling

仓库内新调用方不再主动使用它。

### 5.2 Stop producing / writing

服务端不再发送该字段，新配置 writer 不再写它，新 Rollout 不再产生它。

### 5.3 Stop reading / accepting

当前 reader 不再理解旧输入，或改为清晰拒绝。

### 5.4 Physically remove

删除类型、解析分支、Schema、文档、flag 和桥接测试。

安全迁移通常按这个顺序推进，而不是四步一次完成。

---

## 6. 为什么通常先停写，再停读

如果 reader 先删除，而旧 writer 仍存在：

```text
旧 writer → 仍写 legacyMode
新 reader → 不认识
结果       → 混合版本失败
```

更安全的顺序：

```text
所有 writer 停止产生旧格式
  → 等待旧数据/客户端迁移窗口
  → 观察旧读取命中归零
  → reader 停止兼容
```

这就是协议章节中的 reader-first/writer-last 思想在删除阶段的反向应用：先让读取能力覆盖混合期，最后再收缩 reader。

---

## 7. 删除证明需要三种证据

### 7.1 Static evidence

- `rg` 没有剩余调用；
- compiler/clippy 没有引用；
- crate dependency 已移除；
- registry 不再登记生产路径；
- feature combinations 能构建。

### 7.2 Behavioral evidence

- 新路径的集成测试通过；
- old fixture 能迁移或得到预期错误；
- 不再发出旧 notification；
- rollback/fallback 行为符合计划；
- snapshot/schema diff 已审核。

### 7.3 Operational evidence

- 旧路径 telemetry 命中归零；
- 已覆盖足够发布周期和长尾客户端；
- 没有依赖旧行为的事故/runbook；
- owner 确认回滚窗口关闭。

三者缺一时，结论应写成“当前证据支持”，而不是绝对的“无人使用”。

---

## 8. 从入口向内删，不要从实现向外删

一条旧功能链可能是：

```text
config key
  → Feature enum
  → registry/spec
  → handler
  → runtime
  → event
  → UI
  → docs/schema/tests
```

如果先删 runtime 实现，却留下 config 和 UI：

- 用户仍以为开关有效；
- Schema 仍推荐无效 key；
- UI 可能等待永远不会来的 event；
- strict config 或 config lock 行为漂移。

应先画完整 vertical slice，再明确每一层的退休状态。

---

## 9. Feature Flag 的完整生命周期

Feature flag 不是永久配置。典型生命周期：

```text
实验实现
  → 小流量启用
  → 默认启用
  → 全量启用
  → 行为成为无条件逻辑
  → 旧 key 暂时作为 no-op 接受
  → key 最终退休
```

### 9.1 为什么“默认开启”还不能删 flag

- 企业或 profile 可能显式关闭；
- rollback 仍依赖开关；
- 测试矩阵仍验证两种实现；
- managed config 仍设置它；
- 旧文档和用户配置仍存在。

### 9.2 退休前要做的选择

最终行为是哪一个？

```text
always on
always off
由另一个更高层策略决定
整个功能删除
```

只有答案明确，才能删除分支，而不是留下隐含默认。

---

## 10. 当前实例：Removed Feature Key 仍被识别

`codex-rs/features` 中可看到 removed compatibility flag，例如：

```text
TerminalResizeReflow
CodeModeBufferedExec
MultiAgentMode
SpawnCsv
```

有些功能已经 always-on，有些行为已删除，但旧 key 仍被识别或忽略。

测试例如：

```text
from_sources_ignores_removed_terminal_resize_reflow_feature_key
from_sources_ignores_removed_js_repl_feature_keys
from_sources_ignores_removed_apply_patch_freeform_feature_key
```

### 10.1 这里真正保留的是什么

不是旧功能实现，而是配置兼容：

```text
旧 config 中仍有 key
  → 新版本加载不失败
  → key 不改变当前 canonical behavior
```

这是一种“功能已删，输入壳仍留”的阶段。

### 10.2 为什么需要测试 no-op

如果未来重构误把旧 key 当成 unknown，strict config 或用户启动可能失败；如果误把它重新连接到某个 Feature，又会让废弃行为复活。

测试同时证明：接受输入，但不产生效果。

---

## 11. Alias 与 Removed No-op 不一样

### Alias

旧名字仍映射到当前功能：

```text
connectors → Apps
imagegenext → ImageGeneration
```

旧配置仍有真实效果，并可记录 deprecation usage。

### Removed no-op

旧名字被接受，但不影响当前行为：

```text
terminal_resize_reflow=false
  → resize reflow 仍然始终开启
```

### 为什么要区分

如果把 removed key 当 alias，可能意外重新提供已删除控制；如果把 alias 当 no-op，用户设置会悄悄失效。

命名、日志和测试都应明确两类语义。

---

## 12. 当前实例：停止 Emit，但暂时保留协议类型

App-server v2 仍定义：

```text
item/fileChange/outputDelta
FileChangeOutputDeltaNotification
```

源码和 README 明确说明它是 deprecated legacy notification，服务端已不再发出。

### 12.1 这是删除的中间状态

```text
producer 已停
registry/type 仍在
generated client 仍能认识旧名称
```

这给客户端迁移时间，也避免一次性删除生成类型造成编译破坏。

### 12.2 下一步删除前要查什么

- 是否有客户端仍引用生成类型；
- stable Schema 是否承诺了它；
- 是否有客户端把“类型存在”等同于“事件会发送”；
- 最低受支持客户端版本；
- 是否需要协议 major version 才移除。

“不再 emit”是重要证据，但不是立即删 registry 的充分条件。

---

## 13. 当前实例：旧字段可以忽略，也可能必须拒绝

`SandboxPolicy` 自定义反序列化对旧访问字段做了两种处理。

### 13.1 Legacy fullAccess

旧 `access: fullAccess` / `readOnlyAccess: fullAccess` 可以忽略，因为当前 policy 的其余字段能够等价表达结果。

测试断言旧 JSON 仍反序列化成当前 canonical policy。

### 13.2 Legacy restricted

旧 `restricted` 不能安全忽略。当前 API 要求用 `permissionProfile` 表达 restricted reads。

如果悄悄忽略，可能把“受限读取”变成更宽的访问。因此 parser 返回明确错误：

```text
... is no longer supported; use permissionProfile ...
```

### 13.3 删除策略由语义决定

| 旧输入 | 能否等价映射 | 正确策略 |
|---|---|---|
| 旧名字、同一语义 | 能 | alias/migrate |
| 冗余字段、结果不变 | 能 | ignore + test |
| 无法表达但可安全降级 | 视产品而定 | warning/fallback |
| 忽略会放宽安全边界 | 不能 | 清晰拒绝 |
| 输入本身危险或损坏 | 不能 | fail closed |

删除不是语法问题，而是语义和风险问题。

---

## 14. Fail Open 与 Fail Closed

### Fail open

无法理解输入时仍继续，通常选择较宽松行为。

### Fail closed

无法证明安全时拒绝操作或选择更保守行为。

权限、sandbox、网络和审批字段删除时，应特别警惕“为了兼容就忽略”。兼容不应成为扩大权限的理由。

错误信息应告诉调用方替代方案，而不只是 `invalid params`。

---

## 15. 当前实例：Removed Config 不应制造 Lock Drift

`config_lock` 用于验证重放时有效配置一致。测试：

```text
lock_validation_ignores_removed_apps_mcp_path_override
```

构造一个旧 lock，其中包含已移除的 `apps_mcp_path_override`，当前配置不含该项；验证不应因此报告有效配置漂移。

### 15.1 这里为什么不能直接严格比较原始 TOML

原始输入可能包含 compatibility-only key，但它不再参与 canonical effective config。

```text
raw old config ≠ raw current config
canonical old meaning == canonical current meaning
```

锁应比较对运行行为有意义的规范状态，而不是把已退休的语法当成行为差异。

### 15.2 Schema 为什么还暂时描述 removed input

`config/src/schema.rs` 为该 removed key 保留旧 bool/object 形状，使旧配置仍能通过输入验证。这并不表示功能还有效，而是兼容读取的一部分。

---

## 16. Config Key 的退休阶梯

一个可控顺序：

1. 新配置 writer 停止写旧 key；
2. 文档和 Schema 推荐 canonical key；
3. reader 接受旧 key，并记录 deprecation；
4. 若语义已固定，旧 key 变 no-op；
5. config lock/merge 转成 canonical meaning；
6. 观察旧 key 使用归零；
7. 根据 strict-config 承诺决定忽略还是报迁移错误；
8. 最终从 Schema、parser 和兼容测试中删除。

如果允许旧二进制回滚，还要问新 writer 是否会写旧 reader 不认识的新格式。

---

## 17. CLI 删除比函数删除更隐蔽

删除 CLI flag 前要查：

- Clap 定义和 aliases；
- help text；
- shell completion；
- README 与示例；
- CI workflow；
- installer/launch script；
- 用户自动化；
- stdout/stderr 与 exit code。

### 17.1 常见过渡方式

- hidden alias：不再推荐但继续解析；
- parse + warning：告诉用户替代 flag；
- parse + no-op：仅在旧行为已固定且无误导风险时；
- parse + actionable error：无法安全兼容时；
- version boundary：在明确 major/version policy 下删除。

不要只删 `#[arg(long)]` 后依赖 Clap 的 unknown-argument 错误；它通常不能告诉用户如何迁移。

---

## 18. Rollout 删除要跨越时间

删除持久化字段/variant 前，要调查：

- 历史 Rollout 中是否存在；
- resume、fork、archive、migration 是否会读取；
- 后台 migration 是否覆盖所有本地文件；
- 压缩/归档文件是否跳过；
- 新 writer 写入后旧 binary 能否回滚；
- 未完成/截断 JSONL 如何处理。

### 18.1 Stop writing 不会清除历史

即使今天不再写某 variant，用户磁盘上仍可能有几年前的数据。除非：

- reader 永久保留；
- 启动时可靠迁移；
- 首次读取懒迁移；
- 产品明确不再支持该历史版本。

否则删除 reader 会把“老会话”变成无法恢复的数据损失。

---

## 19. 数据迁移必须可恢复

安全的数据退休计划包含：

```text
发现旧格式
  → 读取并验证
  → 写入新格式或新位置
  → fsync/原子替换
  → 更新索引
  → 标记迁移完成
  → 保留或清理旧副本
```

还要测试：

- 迁移中崩溃；
- 重复运行；
- 部分文件损坏；
- 空目录；
- 磁盘不足；
- 新旧版本交错。

“已经写了 migration function”不证明所有现实数据都已迁移。

---

## 20. App-server 删除检查面

对 method、field、notification 或 enum 删除，逐项搜索：

- `common.rs` registry；
- v2 params/response/notification 类型；
- Serde/TS wire rename；
- stable 与 experimental Schema；
- handler/producer；
- app-server README；
- generated clients；
- public JSON-RPC integration tests；
- raw response events；
- 已发布客户端和能力协商。

删掉 producer 但保留 type，与删掉 wire type，是两个不同兼容阶段。

---

## 21. 编译器看不到的动态引用

### 21.1 String registry

```text
"thread/start"
"item/fileChange/outputDelta"
"features.undo"
```

外部消费者按字符串调用，Rust 引用图无法显示它们。

### 21.2 Serde

字段只通过反序列化被构造，源码可能没有显式赋值。

### 21.3 Macro / generated code

宏登记和 build-time generation 可能创建使用点；只搜索展开前或展开后都可能不完整。

### 21.4 File discovery

文件名、目录名或 frontmatter 由运行时扫描，不需要静态函数引用。

### 21.5 Scripts and configs

Shell、YAML、JSON、TOML、GitHub Actions 和外部 repo 都可能是调用者。

因此搜索至少覆盖所有文本类型和生成流程，而不是只用 `rg symbol -g '*.rs'`。

---

## 22. 条件编译下的“假死代码”

当前 macOS/Linux 上未使用的 Windows enum，可能有：

```rust
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
```

它表达的是：

```text
在非 Windows 构建中不可达
在 Windows 构建中有真实职责
```

### 22.1 删除前检查矩阵

- `cfg(target_os)`；
- debug/release；
- `cfg(test)`；
- Cargo features；
- Bazel targets；
- remote executor target；
- optional dependency；
- experimental API generation。

只在当前开发机运行 `cargo check` 不能覆盖完整矩阵。

---

## 23. 怎样审查 `#[allow(dead_code)]`

看到 allowance 不应立即删代码，也不应默认合理。给它分类。

### 合理候选

- 平台专用类型；
- generated proto；
- test/debug-only helper；
- 外部宏/FFI 使用；
- 明确的短期兼容桥。

### 可疑候选

- 没有原因注释；
- crate-wide allowance；
- 多年不变的“未来会用”；
- 实现和测试都没有调用；
- 旧 feature 已全量结束；
- 通过 allowance 掩盖失败的迁移。

### 改善方法

把 allowance 收窄到 item 或 cfg 条件，并写明隐藏消费者或删除条件。不要用全模块允许来清空警告。

---

## 24. 生成文件也会留下尸体

如果生成器只覆盖当前文件，但不删除旧输出：

```text
旧类型 A.ts 已不生成
磁盘上的 A.ts 仍存在
index 或发布包仍可能包含它
```

当前 app-server Schema fixture writer 会先清空目标目录，再重新生成，以移除 stale artifacts。

这类策略适合完全由生成器拥有的目录。不要对混合手写文件的目录盲目清空。

### 24.1 生成物删除检查

- output directory ownership；
- index/barrel file；
- package manifest；
- Bazel data；
- checked-in fixture；
- release artifact；
- downstream codegen cache。

---

## 25. Dependency 删除的完整范围

删除一个 Rust dependency 不只改 `Cargo.toml`：

- workspace dependency；
- crate manifest；
- `Cargo.lock`；
- `MODULE.bazel.lock`；
- `BUILD.bazel`；
- licenses/notices；
- feature forwarding；
- platform-specific dependency；
- build script 与下载缓存。

仓库要求 Rust dependency 变化后运行：

```bash
just bazel-lock-update
```

还应检查依赖是否通过 transitive dependency 继续存在；“从一个 manifest 删除”不等于供应链已移除。

---

## 26. Test 删除：哪些该删，哪些必须留

仓库指导明确：

- 不为已删除逻辑新增 negative tests；
- 不测试静态定义值本身。

### 26.1 应随实现删除的测试

- 只验证旧内部算法；
- 只为已删除 private helper 存在；
- 断言旧 feature 分支的行为，而产品已确定只保留新行为。

### 26.2 应继续保留的测试

- 旧 config key 被有意忽略且不复活；
- 旧 Rollout 仍需恢复；
- deprecated wire input 有明确兼容或拒绝语义；
- canonical behavior 不受 removed input 影响；
- 架构测试阻止调用方重新绕回旧入口。

关键区别：测试的是“已删除内部逻辑”，还是“仍然存在的外部兼容承诺”。

---

## 27. 防止旧路径复活

删除完成后，新开发者可能因为不知道迁移历史而重新使用旧 primitive、key 或模式。

可用防线：

- 收紧 visibility；
- 删除 re-export；
- architecture test；
- reserved/ignored legacy key test；
- lint 或 forbidden dependency；
- owner 文档；
- 明确 canonical API；
- Schema 不再推荐旧表示。

第 45 章的 `motion` 测试就是例子：不是只证明新入口存在，还扫描并阻止直接调用旧动画 primitive。

---

## 28. Telemetry 怎样证明可以删

迁移期可以记录：

- legacy alias 命中次数；
- deprecated method/field 使用；
- fallback 次数；
- old rollout reader 分支命中；
- no-op removed feature 出现；
- 旧 CLI flag 使用。

### 28.1 “零”需要时间窗口

一天为零不代表长尾客户端不存在。观察窗口应覆盖：

- 发布周期；
- 企业升级延迟；
- 周/月度任务；
- 离线设备重新上线；
- archive/resume 长尾。

### 28.2 Telemetry 也有盲区

- opt-out 用户；
- 离线运行；
- 数据采样；
- metric 标签变更；
- 失败发生在上报前。

所以 telemetry 是删除证据之一，不是唯一证明。

---

## 29. Deprecation Warning 设计

好的 warning 告诉用户：

```text
什么已弃用
替代方案是什么
是否仍生效
何时计划移除（若有可靠承诺）
在哪里修改
```

差的 warning：

```text
deprecated
```

### 29.1 避免刷屏

- 每个 config source 或 session 只报一次；
- 聚合重复项；
- 使用结构化 event；
- 不在热循环每次调用都记录；
- 保留足够定位信息但避免泄漏敏感数据。

Warning 本身也应有删除条件，否则迁移完成后会成为永久噪声。

---

## 30. Tombstone 与 Reserved Name

有时删除后仍需保留“墓碑”：名称不能重新分配给新语义。

例如旧 config key `undo` 曾有明确含义。几年后把同一个 key 用于完全不同功能，会让旧配置意外开启新行为。

Tombstone 可以是：

- ignored legacy-key table；
- 明确 removed error；
- protocol reserved enum number/name；
- migration metadata；
- 文档中的不可复用记录。

墓碑不是旧实现；它只防止名称重用造成歧义。

---

## 31. 回滚如何影响删除时间

假设版本 N 停止写旧字段，版本 N+1 删除 reader。

如果线上从 N+1 回滚到 N-1：

- N-1 能否读 N/N+1 写入的数据？
- Schema/DB migration 是否可逆？
- feature flag 是否还能切回旧实现？
- 旧二进制是否依赖已删除文件？

删除计划要明确：

```text
rollback-safe
forward-fix-only
需要数据 restore
需要先降级 schema
```

不能默认“代码删了，git revert 就能回来”。

---

## 32. 安全删除的阶段模板

### Phase 0：Inventory

画出入口、生产者、读取者、持久化、生成物、平台和外部消费者。

### Phase 1：Introduce canonical replacement

新路径可用，旧路径仍工作；建立行为对照。

### Phase 2：Stop new use

新代码禁止旧入口，文档/Schema 不再推荐，增加 deprecation signal。

### Phase 3：Stop producing

服务端不再 emit，writer 不再写，feature 行为固定。

### Phase 4：Migrate and observe

迁移数据/调用方，观察 telemetry，覆盖长尾窗口。

### Phase 5：Retire reader

根据语义选择 alias、ignore、warning、actionable error 或完全不再接受。

### Phase 6：Physical cleanup

删除实现、依赖、flag、generated artifacts、旧文档和不再有意义的测试。

### Phase 7：Prevent resurrection

收紧 API，保留 tombstone/compatibility test/architecture test。

---

## 33. 删除 PR 的证据模板

### What is removed

列出实现、API、配置、数据和生成物，不要只列文件。

### Why it is safe

- replacement 已全量；
- producer 在哪个版本停止；
- telemetry 的观察窗口；
- 最低客户端版本；
- old data migration 状态；
- rollback policy。

### Search evidence

说明搜索范围和剩余命中为什么合理，例如文档墓碑或 fixture。

### Test evidence

列出目标测试、Schema/snapshot/lock 更新和旧 fixture 行为。

### Residual compatibility

哪些 alias/no-op/parser/tombstone 暂时保留，退出条件是什么。

---

## 34. “顺手删掉”为什么危险

修一个小 bug 时看到旁边旧字段，就顺手删除，会引入与原问题无关的兼容面：

- reviewer 注意力被分散；
- 回滚粒度变粗；
- 测试失败难归因；
- 删除证据通常不完整；
- PR 行数扩大。

如果删除是明显私有、编译器可证明且无外部面，可以一起做；否则记录独立债务并用专门 PR 提供证据。

---

## 35. 什么是技术债

技术债不是“我不喜欢这段代码”。它是为了更快交付或兼容现实而暂时承担、会增加未来变更成本或风险的设计负担。

例如：

- compatibility field 让两套访问路径共存；
- no-op feature key 增加 parser/schema 复杂度；
- 巨型 module 增加冲突和认知成本；
- 缺少 integration test 让修改需要人工验证；
- legacy sandbox fallback 增加安全矩阵。

债务可能是合理选择。问题是没有 owner、利息度量和退出条件。

---

## 36. 技术债的常见类型

| 类型 | 例子 | 主要利息 |
|---|---|---|
| 结构债 | God module、错误依赖方向 | 每次修改理解更慢 |
| 兼容债 | alias、旧字段、双协议 | 每次 API 演进多一条路径 |
| 数据债 | 多种 Rollout/DB 格式 | 恢复与迁移风险 |
| 测试债 | 缺集成/跨平台覆盖 | 回归和人工验证成本 |
| 依赖债 | 旧 crate、重复库 | 安全和升级成本 |
| 运维债 | 手工 runbook、不可观测 fallback | 事故恢复变慢 |
| 安全债 | 宽松 legacy policy | 影响面和审计风险 |
| 文档债 | 当前行为与说明漂移 | 使用错误和重复支持成本 |

分类的目的不是贴标签，而是选择不同偿还证据。

---

## 37. 技术债条目应该写什么

一个可执行条目包括：

```text
Context：为什么当时引入
Owner：谁负责判断和推进
Principal：彻底解决需要多少工作
Interest：现在每次变化付出什么成本
Risk：继续保留可能造成什么后果
Trigger：什么事件使它必须处理
Exit criteria：怎样证明可以关闭
Dependencies：先完成哪些迁移
Last verified：最近一次核对事实的时间
```

“以后重构这里”没有可执行性；“所有 handler 改用 `step_context.turn`，`rg invocation.turn` 仅剩兼容构造后删除字段”则有明确终点。

---

## 38. 怎样安排技术债优先级

可以从五个维度评估：

1. **Impact**：出问题影响多少用户和安全边界；
2. **Change frequency**：这个区域多常被修改；
3. **Interest rate**：每次修改额外付出多少；
4. **Uncertainty**：行为是否难以证明；
5. **Paydown cost**：现在解决的成本和依赖。

粗略思路：

```text
优先级 ∝ 影响 × 修改频率 × 风险增长
          ─────────────────────
                 偿还成本
```

这不是精确数学，只是防止按“最丑代码”排序。

安全边界、数据丢失和高频热点通常比低频审美问题优先。

---

## 39. Opportunistic 与 Planned Paydown

### Opportunistic paydown

在本来就修改该 owner 时，顺手做低风险、直接相关的小清理：

- 删除已迁移调用点；
- 收紧一个不再需要的 visibility；
- 补退出条件注释；
- 把新代码放到正确 module。

### Planned paydown

需要跨层证据、数据迁移、发布观察或多个 PR 的债务，单独规划。

判断标准是 scope 与验证成本，而不是开发者是否“顺便有空”。

---

## 40. Debt Budget 与 Ratchet

完全禁止新增债务不现实。更有效的是 ratchet：允许历史状态存在，但新改动不能继续恶化。

例子：

- `chatwidget.rs` 已很大，但新非平凡逻辑进入新 module；
- 旧 deprecated alias 保留，但新调用不得使用；
- 旧 config key 可读取，但 writer 不再写；
- 当前有若干 `allow(dead_code)`，新 allowance 必须有理由和 cfg 范围；
- 旧协议 type 暂留，但 server 不再 emit。

Ratchet 让系统单向改善，而不要求一次清零全部历史债务。

---

## 41. 常见错误做法

### 41.1 “`rg` 没结果，直接删”

遗漏 wire/config/script/持久化和外部消费者。

### 41.2 “Deprecated 就是没人用”

deprecated 正说明迁移窗口仍存在；需要 usage 和版本证据。

### 41.3 “旧字段一律 ignore”

安全语义可能被放宽；无法等价映射时应清晰拒绝。

### 41.4 “旧字段一律报错”

冗余/no-op 输入可能本可安全兼容，强制报错会破坏长尾配置。

### 41.5 “删实现但留开关”

制造无效配置和虚假产品承诺。

### 41.6 “把所有旧测试都删掉”

可能丢掉仍有效的兼容和防复活契约。

### 41.7 “用 crate-wide dead_code allowance”

把真正的死亡代码和平台专用代码混在一起。

### 41.8 “Feature 全量后当天删 fallback”

没有观察、回滚和长尾升级窗口。

---

## 42. 源码阅读路线

1. `codex-rs/features/src/lib.rs`
   - 看 removed compatibility `Feature` 和 removed key 的应用逻辑。
2. `codex-rs/features/src/legacy.rs`
   - 区分 alias 到当前 Feature 与真正 removed no-op。
3. `codex-rs/features/src/tests.rs`
   - 看旧 key 被接受但不影响 canonical behavior。
4. `codex-rs/app-server-protocol/src/protocol/v2/item.rs`
   - 看不再 emit 的 `FileChangeOutputDeltaNotification`。
5. `codex-rs/app-server-protocol/src/protocol/common.rs`
   - 看 deprecated notification 仍留在 registry。
6. `codex-rs/app-server/README.md`
   - 看公开给客户端的 deprecated/retained/no-longer-emitted 说明。
7. `codex-rs/app-server-protocol/src/protocol/v2/permissions.rs`
   - 看 legacy fullAccess 被忽略、restricted 被拒绝。
8. `codex-rs/app-server-protocol/src/protocol/v2/tests.rs`
   - 看两种安全语义的边界测试。
9. `codex-rs/config/src/schema.rs`
   - 看 removed config input 如何暂留 Schema。
10. `codex-rs/core/src/session/config_lock.rs`
    - 看 removed input 不应制造 effective-config drift。
11. `codex-rs/app-server-protocol/src/schema_fixtures.rs`
    - 看生成前清空目录以删除 stale artifacts。
12. `codex-rs/tui/src/app_event.rs`
    - 看平台条件下收窄的 `allow(dead_code)`。

---

## 43. 动手练习

### 练习 1：消费者清单

任选一个 deprecated symbol，除了 Rust 引用外，再列出 registry、Schema、config、Rollout、CLI、文档和外部客户端检查点。

### 练习 2：四个删除时刻

为一个旧 notification 分别写出 stop calling、stop emitting、stop accepting 和 physical removal 的完成条件。

### 练习 3：Ignore 还是 Reject

设计两个旧权限字段：一个能等价映射，一个忽略后会扩大权限。分别写 parser 行为和错误信息。

### 练习 4：Feature 退休

为一个默认开启 feature 写从 cohort 到 always-on，再到 removed no-op key 的完整阶段和回滚方案。

### 练习 5：Dead-code Allowance

从 TUI 找一个 `cfg_attr(... allow(dead_code))`，证明它在哪个平台/构建模式有调用；若找不到，写出进一步验证计划。

### 练习 6：技术债条目

把“清理旧 config”改写为带 owner、interest、trigger、依赖和 exit criteria 的可执行条目。

---

## 44. 理解检查

1. 为什么没有 Rust 静态引用仍可能有消费者？
2. dead、deprecated、compatibility 和 dormant code 有什么区别？
3. stop producing 与 physical removal 为什么要分开？
4. 为什么通常先停写再停读？
5. 删除证明需要哪三类证据？
6. alias 与 removed no-op 有什么区别？
7. feature always-on 后为什么还可能保留旧 key？
8. 不再 emit 的 notification 类型为什么可能继续留在 Schema？
9. legacy `fullAccess` 与 `restricted` 为什么采用不同处理？
10. fail closed 对权限删除有什么意义？
11. removed config 为什么不应造成 config lock drift？
12. 旧 Rollout reader 为什么可能需要长期保留？
13. 条件编译怎样制造“假死代码”？
14. 哪些 `allow(dead_code)` 更合理，哪些可疑？
15. 生成目录为什么要先清空再生成？
16. 删除旧实现后哪些测试仍应保留？
17. telemetry 为零为什么仍不是绝对证明？
18. tombstone 防止了什么？
19. 技术债的 principal 与 interest 分别是什么？
20. ratchet 怎样让历史热点逐步改善？

---

## 45. 本章词汇表

| 英文 | 字面翻译 | 在本章中的意思 |
|---|---|---|
| Safe deletion | 安全删除 | 有证据证明消费者、数据、回滚和生成物已被处理后的移除 |
| Dead code | 死代码 | 所有受支持路径均不可达且无兼容责任的代码 |
| Deprecated | 已弃用 | 仍可使用、但已通知迁移到替代 API 的状态 |
| Compatibility code | 兼容代码 | 为旧输入、旧数据、旧客户端或回滚保留的逻辑 |
| Dormant code | 休眠代码 | 默认不运行但在平台、feature 或降级条件下可达的代码 |
| Consumer | 消费者 | 调用、读取或依赖旧接口/格式的一方 |
| Producer / Writer | 生产者/写入者 | 发出事件或写入配置/持久化格式的一方 |
| Reader | 读取者 | 接受并解释旧数据的一方 |
| Static evidence | 静态证据 | 搜索、编译、依赖图提供的引用证据 |
| Behavioral evidence | 行为证据 | 集成测试和外部可观察结果提供的证据 |
| Operational evidence | 运行证据 | Telemetry、发布窗口和真实使用提供的证据 |
| No-op | 无操作 | 输入仍被接受但不会改变当前行为 |
| Alias | 别名 | 旧名字仍映射到当前有效能力 |
| Tombstone | 墓碑 | 防止已删除名字被重新用于不同语义的保留记录 |
| Stale artifact | 过期制品 | 已不再生成、但仍残留在输出目录的旧文件 |
| Emit | 发出 | 服务端主动发送 notification/event |
| Fail open | 失败时放行 | 无法理解时仍采取较宽松行为 |
| Fail closed | 失败时关闭 | 无法证明安全时拒绝或采取保守行为 |
| Actionable error | 可执行错误 | 明确说明错误原因和迁移替代方案的信息 |
| Deprecation window | 弃用窗口 | 宣布弃用到最终删除之间的迁移时间 |
| Long-tail client | 长尾客户端 | 更新较慢、离线或低频运行的旧版本客户端 |
| Usage telemetry | 使用遥测 | 统计旧 alias、fallback 或 reader 分支真实命中情况 |
| Inventory | 盘点 | 删除前列出入口、消费者、数据和制品 |
| Vertical slice | 垂直切片 | 从配置/协议入口到 runtime、事件、UI 的完整功能链 |
| Config lock | 配置锁 | 用于重放时验证规范有效配置一致性的记录 |
| Canonical meaning | 规范语义 | 去除旧语法差异后真正影响运行的配置含义 |
| Data migration | 数据迁移 | 将持久化旧格式可靠转换成当前格式 |
| Rollback window | 回滚窗口 | 发布后仍要求旧版本能够重新接管的时期 |
| Technical debt | 技术债 | 为交付/兼容暂时承担、会增加未来成本或风险的设计负担 |
| Principal | 本金 | 彻底偿还一项技术债所需的工作量 |
| Interest | 利息 | 债务存在期间每次修改额外产生的成本 |
| Paydown | 偿还 | 减少或清除技术债的工程工作 |
| Debt owner | 债务负责人 | 负责核对状态、推进迁移和决定关闭的人或团队 |
| Trigger | 触发条件 | 使某项债务必须处理的事件或阈值 |
| Exit criteria | 退出条件 | 可以删除兼容层或关闭债务的可验证标准 |
| Ratchet | 棘轮 | 不强制一次清零历史，但禁止新改动继续恶化的规则 |
| Resurrection | 复活 | 已删除旧模式被新代码重新引入 |

---

## 46. 本章小结

安全删除不是 `Delete` 键，而是一条完整证据链：

```text
盘点全部消费者
  → 建立 canonical replacement
  → 禁止新调用
  → 停止产生旧格式
  → 迁移数据与长尾客户端
  → 观察真实使用归零
  → 按语义忽略、迁移或拒绝旧输入
  → 删除实现、依赖、Schema 和残留制品
  → 保留必要墓碑与防复活约束
```

当前 Codex 源码展示了三种不同退休状态：

- removed feature 实现已消失，但旧 key 暂时作为 no-op 接受；
- legacy apply-patch notification 已停止 emit，但类型和 registry 暂时保留；
- legacy sandbox field 根据能否安全等价映射，分别选择忽略或明确拒绝。

这说明不存在通用的“旧输入全部兼容”或“旧输入全部报错”。正确策略取决于语义、权限风险、数据寿命和发布节奏。

技术债治理也不是追求零债务。更实用的目标是：每项债务有 owner，知道它为何存在、产生什么利息、由什么触发偿还、满足什么证据后可以删除。配合 ratchet，让新代码不再扩大旧债，长期热点才会真正缩小。

当你能对一次删除回答“谁曾经依赖、何时停止产生、旧数据如何处理、回滚是否安全、哪些测试留下、怎样防止复活”，这个删除才算完成。
