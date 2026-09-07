# 34：从最小修复到可审查 PR——让评审者快速理解、验证并安全合并

第 33 章已经得到一个最小补丁和回归测试。但“本地测试通过”不等于“适合合并”。Pull Request 还要向评审者证明：问题值得修、根因判断正确、改动范围受控、兼容性已考虑、验证足够，而且 CI 失败能够被解释。

> 源码基线：`4ee41929eaf4`。GitHub workflow 会持续变化，阅读其他版本时请优先查看 `.github/workflows/README.md`、`blocking-ci.yml` 和 `docs/contributing.md`。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分 working tree、commit、branch 和 PR；
2. 把一个补丁组织成可独立理解的原子提交；
3. 判断改动是否过大、是否需要拆分或堆叠；
4. 写出回答 What / Why / How / Tests / Risks 的 PR 正文；
5. 系统检查 app-server、CLI、配置和 rollout breaking changes；
6. 看懂 Codex 仓库主要 CI 检查各自保护什么；
7. 区分代码失败、测试不稳定和 CI 基础设施失败；
8. 根据 review feedback 修改代码、测试和 PR 说明；
9. 知道什么时候可以 Ready for review，什么时候应保持 Draft。

---

## 2. 先说最重要的仓库边界：外部 PR 需要邀请

当前 `docs/contributing.md` 明确说明：Codex 外部代码贡献只接受团队成员邀请，未受邀请的 PR 会被关闭而不进入评审。

如果你是外部贡献者，发现问题后优先提供：

- 稳定复现步骤；
- 受影响版本和环境；
- 最后正常边界与事件证据；
- 根因假设；
- 可能的修复方向；
- 风险与兼容性分析。

团队可能在问题已充分理解、方案符合方向且优先级合适时邀请外部贡献。

### 2.1 本章为什么仍值得学习

因为可审查 PR 的方法适用于：

- 受邀 Codex 贡献；
- 你自己的仓库；
- 公司内部 monorepo；
- Codex 帮你准备的任何代码改动；
- 即使不提交 PR，也能用于自我检查补丁质量。

学习工作流不等于当前自动获得提交权限。

---

## 3. 四个对象不要混淆

| 对象 | 通俗解释 | 解决的问题 |
|---|---|---|
| Working tree | 磁盘上尚未提交的当前文件状态 | 我正在改什么？ |
| Commit | 一份有父节点、作者和说明的代码快照 | 哪个逻辑步骤改变了？ |
| Branch | 指向一串 commit 的可移动名字 | 这组工作与 main 怎样分开？ |
| Pull Request | 请求把分支差异评审并合入目标分支的协作对象 | 为什么应该合并？ |

PR 不是“一个大 commit 的网页”。它还包含：

- issue 关联；
- 讨论和 review threads；
- CI 状态；
- CODEOWNERS / reviewer；
- Draft/Ready 状态；
- base/head 比较；
- merge 方式。

---

## 4. 从上一章的教学修复继续

上一章的教学缺陷是：

```text
Core 已发 TurnComplete，
但没有 final assistant message 时，
app-server 的 thread status 仍保持 Active。
```

最小补丁包含：

```text
实现：terminal event 统一清理 active state
测试：无 final message 时也观察到 Active → 非 Active
```

一个可审查 PR 应让陌生评审者在几分钟内回答：

1. 这是哪个用户问题？
2. 根因在哪里？
3. 为什么只改这些文件？
4. 测试怎样证明旧 bug？
5. 是否改变 wire schema？
6. 失败、取消和正常完成是否仍一致？

---

## 5. 提交前先清点工作区

不要把工作区里所有变化都默认当成自己的修改。

常用只读检查：

```bash
git status --short
git diff --stat
git diff --check
git diff
```

分别回答：

| 命令 | 主要问题 |
|---|---|
| `git status --short` | 哪些文件 modified/untracked/staged？ |
| `git diff --stat` | 改动规模和文件分布如何？ |
| `git diff --check` | 是否有 whitespace error？ |
| `git diff` | 具体行为变化是什么？ |

### 5.1 脏工作区的原则

如果已有用户改动：

- 不覆盖；
- 不用 destructive reset；
- 不把无关文件顺手提交；
- 与本次修改重叠时先理解来源；
- 无法安全分离时向用户说明。

“让 git status 变干净”不是授权删除他人工作。

---

## 6. Branch 是工作边界，不是质量证明

`docs/contributing.md` 建议受邀贡献从 `main` 创建 topic branch，例如：

```text
feat/interactive-prompt
```

在 Codex App 代表用户自动创建分支的场景中，产品约定可能使用 `codex/` 前缀；实际贡献时应遵守当前仓库、组织或用户明确指定的命名规则。

一个好 branch name 应能表达主题：

```text
fix/terminal-thread-status
```

避免：

```text
changes
my-work
fix-stuff
```

但分支名字再漂亮，也不能代替回归测试和 PR 说明。

---

## 7. 什么叫 Atomic Commit

Atomic commit 是一个可以独立解释、构建、测试，并在必要时独立回滚的逻辑步骤。

### 7.1 本例的一种组织方式

如果补丁很小，测试与实现可以放在同一 commit：

```text
fix(app-server): clear active status on terminal turns
```

这个 commit 同时包含：

- 触发 bug 的回归测试；
- 恢复不变量的最小实现。

这样 commit 自身保持 green。

### 7.2 什么时候拆成多个 commit

合理拆分：

```text
1. refactor(app-server): isolate terminal status transition
2. fix(app-server): clear status without a final message
```

前提是第一步行为不变、独立通过测试，并且确实降低第二步风险。

不合理拆分：

```text
1. add imports
2. add helper
3. add if statement
4. fix formatting
```

这些只是编辑顺序，不是可审查逻辑单位。

### 7.3 当前仓库的要求

贡献指南要求 commit 保持 atomic，每个 commit 应能编译并通过测试，这有利于评审和回滚。最终维护者采用 squash-and-merge，并不意味着评审期间的 commit 结构可以混乱。

---

## 8. Commit Message 要解释意图

推荐结构：

```text
fix(app-server): clear active status on terminal turns
```

它包含：

- `fix`：变更类别；
- `app-server`：主要所有者；
- 动词短语：具体行为变化。

不推荐：

```text
update files
fix bug
address comments
wip
```

如果正文有必要，可以解释：

```text
Terminal turn events are authoritative even when the turn has no final
assistant message. Clear the active projection from the terminal branch and
cover the no-message path in the v2 status integration test.
```

不要在 commit message 中粘贴一整份 PR 正文；它应解释这个 commit 的单一逻辑步骤。

---

## 9. Change Size：多大开始难以评审

根目录规则建议：

- 非机械变更总量不超过约 800 行；
- 复杂逻辑最好低于约 500 行；
- 更大时寻找可独立落地的最小阶段。

这不是“801 行自动失败”的机械门槛，而是 reviewability signal。

### 9.1 行数不能单独判断复杂度

| 改动 | 行数 | 真实复杂度 |
|---|---:|---|
| 生成 schema 更新 | 2,000 | 多数机械、但必须核对来源 |
| 一个并发状态机改动 | 80 | 可能非常高风险 |
| 重命名 200 个符号 | 600 | 机械但易混入行为变化 |
| 新协议字段 | 15 | 可能 breaking |

PR 应区分 handwritten 与 generated、mechanical 与 semantic changes。

---

## 10. 怎样拆一个过大的 PR

拆分依据应是依赖和可独立验证的行为，不是平均分行数。

### 10.1 常见阶段

```text
PR 1：行为不变的抽象/模块提取
PR 2：新增内部能力与测试
PR 3：接入公开 API/UI
PR 4：迁移旧调用方并删除兼容层
```

### 10.2 本例不应怎样拆

```text
PR 1：只加一个永远失败的测试
PR 2：几天后再修生产分支
```

除非团队明确接受红色中间状态，否则每个可合并阶段都应保持主分支健康。

### 10.3 Stacked PR

后续 PR 依赖前一个尚未合并的 PR 时形成 stack。每个 PR 应说明：

- base 是 main 还是前一分支；
- 哪些 commit 属于本层；
- 应先评审哪一层；
- 前层合并后怎样 rebase/retarget。

不要让评审者把整个 stack 的重复 diff 当成当前 PR。

---

## 11. PR 标题是最终变更摘要

好标题：

```text
Clear app-server thread status after terminal turns
```

它描述合并后发生的行为。

较差标题：

```text
Fix #12345
Changes requested by reviewer
Try another approach
```

Issue 编号可以关联在正文，标题应让 changelog、历史搜索和 reviewer 一眼知道主题。

### 11.1 标题避免承诺过度

如果只修无 final message 的终态路径，不要写：

```text
Fix all thread status bugs
```

标题范围必须与 diff 一致。

---

## 12. PR 正文的五个核心问题

贡献指南要求至少回答 What / Why / How，并链接 bug 或 enhancement request。一个更完整的模板是：

```md
## What

Clear the app-server active-turn projection whenever Core emits a terminal
turn event, including turns without a final assistant message.

## Why

The existing path tied status cleanup to final-message presence. A completed
turn could therefore continue to appear Active to v2 clients.

Fixes #12345.

## How

- Treat TurnComplete/TurnAborted as the lifecycle authority.
- Remove the final-message condition from active-state cleanup.
- Add a v2 integration test for a terminal turn with no final message.

## Tests

- `just test -p codex-app-server`
- `just fmt`

## Risks

- No wire-schema changes.
- Normal final-message completion keeps the same status transition.
- Cancellation and failure terminal paths were reviewed for duplicate cleanup.
```

### 12.1 What 不是文件清单

不够好：

```text
Changed thread_status.rs and a test file.
```

更好：

```text
Terminal turn events now always clear the app-server active projection.
```

### 12.2 Why 必须说明用户影响

“代码不够优雅”通常不够。说明客户端为何会持续 loading、为何无法正确调度下一项工作，才解释改动价值。

### 12.3 How 应解释关键设计选择

不必逐行复述 diff。解释为何以 terminal event 为权威、为什么修在 app-server 而非 UI。

### 12.4 Tests 要写实际运行结果

不要在没运行时写“all tests pass”。可以诚实写：

```text
- PASS: `just test -p codex-app-server`
- NOT RUN: full `just test`（目标改动未触及 core/protocol；CI will cover broader Bazel checks）
```

### 12.5 Risks 不要只写 None

即使风险低，也说明为什么低：没有 wire shape、权限、持久化或模型上下文变化。

---

## 13. Draft 与 Ready for Review

### Draft 适合

- 方案希望尽早获得方向反馈；
- CI 尚未全部稳定；
- 依赖前置 PR；
- 仍在补测试或生成物；
- 已知有未解决问题。

### Ready 适合

- 实现范围稳定；
- PR 正文与 diff 一致；
- 目标测试通过；
- lint/format/生成物已处理；
- 分支已解决与 main 的冲突；
- 作者认为达到可合并状态。

贡献指南明确要求只有在认为 PR mergeable 时才标 Ready for review。

Draft 不是允许长期提交不可理解 diff 的借口；至少要写清当前状态和希望 reviewer 回答的问题。

---

## 14. Breaking Change 不一定看起来很大

Codex 仓库要求重点检查四类外部集成面：

```text
app-server APIs
raw response item events
CLI parameters
configuration loading
resuming sessions from existing rollouts
```

### 14.1 App-server API

可能 breaking 的变化：

- 删除/重命名 method；
- 改 field 名或类型；
- 改 enum wire value；
- 从 optional 变 required；
- 改 notification ordering/terminal semantics；
- Rust serde rename 与 TypeScript rename 不一致。

### 14.2 CLI

- 删除 flag；
- 改默认值；
- 改 exit code；
- 改脚本依赖的 stdout/stderr shape；
- 让以前非交互命令开始等待输入。

### 14.3 Config

- key 重命名；
- 合并优先级变化；
- 默认 sandbox/approval 改变；
- 旧值不再解析；
- managed requirement 可被本地层意外绕过。

### 14.4 Rollout/Resume

- 旧记录反序列化失败；
- 历史重放得到不同状态；
- terminal event 解释变化；
- fork/reference 边界失效；
- DB projection 与 rollout 不一致。

### 14.5 “新增 optional 字段”也要检查

它通常比删除字段安全，但仍要确认：

- v2 Params 是否正确 `optional = nullable`；
- server response 是否禁止意外省略；
- schema fixture 是否更新；
- 老客户端忽略未知字段的行为；
- TypeScript 和 Rust wire name 一致。

---

## 15. PR 中怎样写 Breaking-change 分析

可以添加一段：

```md
## Compatibility

- App-server wire shape: unchanged.
- CLI flags/output: unchanged.
- Config parsing/defaults: unchanged.
- Rollout/resume: terminal event payload unchanged; only the live app-server
  projection is corrected.
```

如果确实有 breaking change：

- 明确旧行为和新行为；
- 谁受影响；
- 是否有迁移期/兼容层；
- schema/docs/examples 怎样更新；
- 为什么不能保持兼容；
- rollout/older client 如何处理。

不要让 reviewer 自己从 600 行 diff 猜兼容性。

---

## 16. CODEOWNERS 回答“谁应看”，不回答“代码是否对”

`.github/CODEOWNERS` 把特定路径映射给维护团队。例如当前文件把 `codex-rs/core/`、`codex-rs/codex-mcp/`、`exec-server` 等核心路径交给 core agent team。

它的作用包括：

- 自动建议/请求适当 reviewer；
- 让敏感路径由负责团队评审；
- 防止所有权文件自身随意变化。

但 CODEOWNER approval 不能替代测试、设计说明和 CI。

跨多个 ownership domain 的 PR 往往也是“是否应拆分”的信号。

---

## 17. 当前 Codex PR 的 Blocking CI 总览

`.github/workflows/blocking-ci.yml` 是阻止 PR 合并的统一入口。它聚合：

| Job family | 主要保护什么 |
|---|---|
| Bazel | 多平台 build/test/clippy 和构建图一致性 |
| Blob size policy | 防止意外加入过大的二进制/文件 |
| cargo-deny | 依赖许可、安全公告和策略 |
| codespell | 拼写错误 |
| repo-checks | 仓库结构、manifest、边界和脚本检查 |
| rust-ci | 快速 Cargo/Rust 格式与专用 lint |
| sdk | SDK 相关生成和测试 |
| CI required | 汇总所有依赖结果，确保失败不能被 skipped 掩盖 |

`required` job 使用 `always()` 收集上游结果，因此某个依赖失败后，最终门禁不会因为默认 skip 语义看起来成功。

---

## 18. Bazel 与 Cargo CI 为什么分工

`.github/workflows/README.md` 说明：

### PR 路径

- `bazel.yml` 是 Rust 主 pre-merge 验证；
- 运行 Bazel test 和 Bazel clippy；
- `rust-ci.yml` 保持较快，只承担 Cargo-native 的关键检查，例如 fmt、cargo shear、argument-comment-lint。

### Main 合并后

- `bazel.yml` 再次验证并保持缓存；
- `rust-ci-full.yml` 运行较重的完整 Cargo clippy/nextest、release builds、跨平台 lint 和 remote-env tests。

所以“PR 页面没显示完整 Cargo 全矩阵”不一定是漏配，可能是仓库有意把重验证放到 post-merge，同时用 Bazel 做 pre-merge 主路径。

---

## 19. Path-based CI：为什么有些 Job 没运行

`rust-ci.yml` 先检测 changed paths，再决定哪些 job 相关。

例如：

- `codex-rs/*` 变化触发 Rust 相关检查；
- argument-comment-lint 或 workflow 变化触发专用 package tests；
- `.github/*` 变化触发 workflow 相关路径。

Job 显示 skipped 可能是预期的路径过滤，不应自动理解成 CI 故障。

但最终 required gate 必须正确理解 skipped/cancelled/failure 的组合；这正是汇总脚本存在的原因。

---

## 20. Clean Worktree Check 保护什么

许多 CI job 最后运行 `.github/actions/check-clean-worktree`。

它能发现：

- formatter 会产生但作者未提交的变化；
- schema/generator 输出漂移；
- 测试修改了 tracked fixtures；
- 构建脚本意外污染源码树。

如果 CI 报工作区不干净，不应简单忽略生成文件。先查看 diff，找到哪条命令产生了未提交变化，再运行正确 generator 并审查结果。

---

## 21. CI 失败的四类来源

| 类别 | 特征 | 处理方式 |
|---|---|---|
| Deterministic code failure | 同一断言/编译错误稳定复现 | 修代码或测试 |
| Generated drift | clean-worktree/schema/lock 变化 | 运行 generator 并提交正确产物 |
| Flaky test | 相同 SHA 偶发、时序/资源相关 | 收集多次证据，修稳定性或谨慎重跑 |
| Infrastructure failure | runner、cache、网络、服务故障 | 查看平台状态/log，必要时重跑或等待 |

### 21.1 不要一失败就点 Re-run

先记录：

- job 名；
- failing step；
- 第一个有意义的 error；
- runner/target；
- commit SHA；
- 是否只在一个平台；
- 是否能本地复现。

重跑可能提供 flaky 证据，但不能代替诊断。

### 21.2 不要只看最后的 `exit code 1`

最后一行只是结果。向上找第一个编译错误、断言 diff、missing generated file 或超时目标。

---

## 22. CI 失败定位矩阵

| 失败 | 优先检查 |
|---|---|
| `cargo fmt --check` | 是否运行 `just fmt`、是否漏提交格式变化 |
| Bazel compile，但 Cargo 本地通过 | `BUILD.bazel` data/deps、cfg/platform、runfiles |
| MODULE lock check | 是否改 Cargo deps 后漏 `just bazel-lock-update` |
| Snapshot pending | 是否审查并接受预期 `.snap.new` |
| App-server schema drift | 是否运行 `just write-app-server-schema` |
| Config schema drift | 是否运行 `just write-config-schema` |
| argument-comment-lint | opaque bool/None/number 是否有精确参数注释 |
| Windows-only test | path、quoting、process、PTY 或权限差异 |
| codespell | 新文案/注释拼写，必要的术语是否配置例外 |
| cargo-deny | 新依赖许可、来源、重复或 advisory |
| blob size | 大 fixture/binary 是否必要，能否缩小或生成 |
| clean worktree | CI 命令产生了未提交文件 |

---

## 23. Rebase 与 Merge Conflict

贡献指南要求 PR 分支跟上 `main` 并解决冲突。

### 23.1 Rebase 的目的

把你的 commit 重新放到更新后的 base 上，让：

- diff 只显示自己的变化；
- CI 在接近合并的代码上运行；
- 冲突由作者结合意图解决。

### 23.2 冲突解决不是选 ours/theirs

需要理解双方语义：

```text
main 是否已经以另一种方式修复？
类型/函数是否重命名？
新测试 harness 是否应替代旧写法？
合并两个状态分支会不会重复发 terminal event？
```

解决后重新检查 diff 和目标测试。

### 23.3 不要用危险命令清空工作区

尤其有用户未提交修改时，不要使用 `git reset --hard` 或类似操作。先明确目标和可恢复性。

---

## 24. Review Feedback 有哪些类型

| 类型 | 示例 | 响应重点 |
|---|---|---|
| Correctness | 取消路径会不会仍 Active？ | 补证据、代码和测试 |
| Architecture | 状态逻辑是否应属于 app-server？ | 讨论所有权与依赖方向 |
| Compatibility | 老客户端怎样处理新 variant？ | wire/schema/migration 分析 |
| Testing | 测试只调用私有 helper | 改成公开边界的回归测试 |
| Scope | PR 混入无关重构 | 拆分或撤出无关变化 |
| Style | 使用仓库已有 helper | 采用本地约定并保持可读性 |
| Question | 为什么 terminal event 是权威？ | 解释不变量和现有协议 |

不是每条评论都等于“照字面改代码”。先理解 reviewer 试图保护的风险。

---

## 25. 怎样回应 Review

### 25.1 先复述问题

```text
你担心 TurnAborted 路径会绕过新的清理，因此 Active 状态仍可能泄漏。
```

### 25.2 给出证据

```text
TurnComplete 和 TurnAborted 都进入同一个 terminal branch；我补了一条
aborted-turn integration test，并确认两者都产生非 Active status。
```

### 25.3 说明实际改动

```text
已把清理移动到 terminal event 的共同路径，并删除完成分支中的重复清理。
```

### 25.4 报告验证

```text
PASS: just test -p codex-app-server
```

避免只回复：

```text
fixed
done
```

评审对话也是未来维护者理解设计的记录。

---

## 26. Reviewer 的建议与事实冲突时怎么办

不要无依据地服从，也不要防御性拒绝。

可以：

1. 指出具体源码/测试；
2. 给出能区分两种判断的最小实验；
3. 说明 tradeoff；
4. 如果要求会扩大范围，建议后续 PR；
5. 在不确定时承认不确定，并收集证据。

例如：

```text
当前 v2 response 字段不能使用 skip_serializing_if；如果省略 error 字段，
会违反 app-server v2 payload 约定。建议保持字段为 null，并更新测试预期。
```

高质量协作靠可检查证据，而不是谁语气更坚定。

---

## 27. Review 后的 Commit 怎么组织

评审期间可以提交小的 follow-up commits，便于 reviewer 看新增差异：

```text
fix(app-server): clear active status on terminal turns
test(app-server): cover aborted terminal status cleanup
```

也可以在准备最终合并前按团队偏好 squash/fixup。当前 Codex 维护流程最终由 maintainer squash-and-merge。

关键是：

- review 中每轮变化可追踪；
- 最终 PR diff 自洽；
- 不因 history 清理丢失重要测试或生成物；
- force-push 后提醒 reviewer 基准发生变化。

不要为了“commit 很漂亮”反复重写正在被评审的 history，导致评论定位失效，而不说明。

---

## 28. PR 正文必须随实现更新

Review 后可能发生：

- 修复位置从 TUI 移到 app-server；
- 增加取消路径；
- wire schema 最终保持不变；
- 测试范围扩大；
- PR 被拆成两层。

此时旧正文会误导 reviewer。应同步更新：

- What；
- How；
- Tests；
- Risk/Compatibility；
- stacked dependency；
- 未解决项。

PR 正文是当前 diff 的地图，不是最初计划的纪念碑。

---

## 29. 安全问题不要走普通公开 PR 流程

`docs/contributing.md` 指示：发现 vulnerability 或 responsible AI concern 时，应通过 `security@openai.com` 私下报告。

不要在公开 issue/PR 中提供：

- 可利用的未修复漏洞细节；
- 真实凭据；
- 攻击 payload 和生产目标；
- 用户隐私数据；
- 能扩大利用范围的操作步骤。

普通代码 review 与 coordinated security disclosure 是不同流程。

---

## 30. 外部贡献者还需要 CLA

受邀外部贡献者仍需签署 Contributor License Agreement。当前说明要求在 PR 中粘贴指定声明；已经签过时可回复 `recheck`。

这属于贡献资格/法律流程，不是代码测试。CLA 通过不能证明代码正确；代码 CI 通过也不能代替 CLA。

---

## 31. 一个完整 PR 示例

### 标题

```text
Clear app-server thread status after terminal turns
```

### 正文

```md
## What

Make terminal Core turn events clear the live app-server active-turn
projection even when no final assistant message was produced.

## Why

The projection previously coupled lifecycle cleanup to final-message presence.
As a result, v2 clients could receive `turn/completed` while the thread still
appeared Active.

Fixes #12345.

## Root cause

`TurnComplete` reached app-server, but the no-final-message path skipped the
state transition that removes the active turn.

## How

- Move cleanup to the shared terminal-event path.
- Keep message extraction independent from lifecycle completion.
- Add an app-server v2 integration regression test.

## Tests

- PASS: `just test -p codex-app-server`
- PASS: `just fmt`

## Compatibility

- No app-server wire-shape changes.
- No CLI, config, model-context, or rollout changes.
- Existing completion and abort terminal semantics are preserved.

## Risk

Low and scoped to live thread-status projection. The test observes the public
JSON-RPC status and completion notifications rather than a private helper.
```

### 评审者的阅读路线

```text
Issue reproduction
→ new regression test
→ minimal implementation branch
→ existing terminal-path tests
→ PR compatibility section
→ CI result
```

好的 PR 主动提供这条路线。

---

## 32. 从打开 PR 到合并的状态机

```text
Issue/方案达成共识
        ↓
Topic branch + atomic commits
        ↓
本地目标验证
        ↓
Draft PR（需要早期方向时）
        ↓
PR body / issue / reviewers / CI
        ↓
Ready for review
        ↓
Review feedback ↔ 修改 + 测试 + 更新正文
        ↓
CI 全绿 + review consensus
        ↓
Maintainer squash-and-merge
        ↓
Main 上的 post-merge full verification
```

任何一层失败，都应回到对应责任边界，而不是盲目重开 PR。

---

## 33. PR 自查清单

### 问题与范围

- [ ] 有 issue/讨论链接；外部贡献已获明确邀请。
- [ ] 标题描述最终行为，不夸大范围。
- [ ] PR 只解决一个连贯问题。
- [ ] handwritten semantic diff 大小适合评审。
- [ ] 无关用户改动没有混入。

### 实现

- [ ] 修复最早违反不变量的所有者层。
- [ ] 没有不必要扩大 `codex-core` 或 public API。
- [ ] error/cancel/timeout/并发路径已考虑。
- [ ] 权限、approval、Sandbox 和日志安全未被削弱。

### 测试与产物

- [ ] 回归测试在旧行为下能正确失败。
- [ ] 测试走合适的公开边界。
- [ ] 目标 crate tests 已运行。
- [ ] schema/snapshot/Bazel lock 等生成物已同步并审查。
- [ ] `just fix`/`just fmt` 按仓库要求完成。
- [ ] 未运行的 broader tests 已明确说明。

### 兼容性

- [ ] app-server API/raw events 已检查。
- [ ] CLI flags/output 已检查。
- [ ] config loading/defaults 已检查。
- [ ] rollout/resume/fork 已检查。
- [ ] Linux/macOS/Windows 差异已考虑。

### PR 协作

- [ ] What / Why / How / Tests / Risks 完整。
- [ ] Draft/Ready 状态准确。
- [ ] 分支基于最新 main，冲突已按语义解决。
- [ ] CI 失败已诊断，不是无脑重跑。
- [ ] Review 回复包含证据、改动和验证。
- [ ] PR 正文已随最终实现更新。

---

## 34. 本章词汇表

完整总表见[课程术语表](glossary.md)。

| 名词 | 常见写法 | 通俗解释 |
|---|---|---|
| Working tree | working tree | 磁盘上当前已改、未提交和未跟踪文件的状态 |
| Commit | commit | 带父节点、作者和说明的一份 Git 快照 |
| Branch | branch | 指向一串 commit 末端的可移动引用 |
| Pull Request | PR | 请求评审并把 head branch 合入 base branch 的协作对象 |
| Base / Head | base/head | PR 的目标分支与提供改动的来源分支 |
| Topic branch | topic branch | 只承载一个功能或修复主题的分支 |
| Atomic commit | atomic commit | 可独立理解、构建、测试和回滚的逻辑提交 |
| Draft PR | draft | 尚未宣称可合并、用于早期协作的 PR 状态 |
| Ready for review | ready | 作者认为实现、测试和说明已达到可合并评审状态 |
| Reviewability | reviewability | 评审者理解和验证改动的难易程度 |
| Semantic change | semantic diff | 真正改变行为或契约的代码变化 |
| Mechanical change | mechanical diff | 按固定规则生成、重命名或格式化的变化 |
| Stacked PR | stacked PR | 后一 PR 以尚未合并的前一 PR 分支为 base |
| Rebase | rebase | 把 commit 重新应用到更新后的 base 上 |
| Merge conflict | conflict | 两条历史对同一区域作出无法自动合并的变化 |
| CODEOWNERS | code owners | 把路径映射到负责评审团队的仓库文件 |
| Blocking CI | required checks | 未通过就不能合并的检查集合 |
| Path filter | changed-path detection | 根据 diff 路径决定哪些 CI job 相关 |
| Flaky test | flaky test | 同一代码输入下偶发通过或失败的测试 |
| Infrastructure failure | infra failure | runner、缓存、网络或外部服务导致的非代码失败 |
| Squash and merge | squash merge | 把 PR commits 压成一个 commit 合入目标分支 |
| Force push | force push | 重写远端分支历史，改变已有 commit identity |
| CLA | Contributor License Agreement | 外部贡献者提交代码所需的许可协议 |
| Security disclosure | coordinated disclosure | 私下报告并协调修复漏洞的流程 |

### 代码和 Git 单词短语拆解

- `pull`：拉取；在 PR 中是请求目标仓库接纳改动的历史名称。
- `request`：请求；不是命令维护者必须合并。
- `working tree`：工作树；当前目录文件与 Git 快照的差异状态。
- `staged`：已暂存；被选入下一次 commit 的变化。
- `unstaged`：未暂存；已修改但尚未选入 commit 的变化。
- `untracked`：未跟踪；Git 还没有纳入版本历史的文件。
- `commit`：提交；保存一份有父关系的快照。
- `branch`：分支；一个随新 commit 向前移动的名字。
- `topic`：主题；一个边界清楚的功能或修复。
- `atomic`：原子的；作为一个逻辑单位不可再随意拆开。
- `base`：基线/目标；PR 希望合入的分支。
- `head`：头部/来源；PR 改动所在分支的最新 commit。
- `diff`：差异；base 与 head 之间的内容变化。
- `hunk`：差异块；diff 中一段连续修改。
- `semantic`：语义的；影响程序含义和行为。
- `mechanical`：机械的；按固定转换规则产生。
- `reviewable`：可评审的；范围和证据让人能可靠检查。
- `draft`：草稿；尚未声明达到最终评审状态。
- `mergeable`：可合并的；冲突、测试和策略条件允许合入。
- `rebase`：变基；把改动重新放到新的基础历史上。
- `conflict`：冲突；Git 无法自动确定正确合并结果。
- `resolve`：解决；根据双方语义写出最终版本。
- `stack`：堆叠；多个有依赖顺序的 PR。
- `retarget`：重设目标；改变 PR 的 base branch。
- `squash`：压缩；把多个 commit 合成一个。
- `force push`：强制推送；让远端引用接受非快进历史。
- `review`：评审；检查正确性、设计、兼容性和维护成本。
- `approve`：批准；reviewer 表示当前改动达到合并标准。
- `request changes`：请求修改；评审发现合并前必须处理的问题。
- `thread`：讨论线程；围绕一条评论的连续对话，不是 Codex task。
- `resolve conversation`：解决讨论；确认问题已处理或达成结论。
- `CI`：continuous integration，持续集成；自动构建、测试和检查。
- `job`：作业；workflow 中独立调度的一组 steps。
- `step`：步骤；job 中顺序执行的一项动作。
- `runner`：运行器；实际执行 CI job 的机器/环境。
- `matrix`：矩阵；用多个 OS、target 或配置展开相似 job。
- `shard`：分片；把大测试集合拆给多个并行 runner。
- `cache`：缓存；复用依赖或构建产物以减少耗时。
- `artifact`：制品；CI 上传保存的日志、二进制或报告。
- `required check`：必需检查；分支保护要求通过的状态。
- `skipped`：跳过；条件不满足而未运行，不自动等于失败。
- `cancelled`：取消；job 未正常跑完，常因新 commit 或人工操作。
- `flaky`：不稳定；相同条件下结果偶发变化。
- `rerun`：重跑；再次执行同一检查，应有诊断目的。
- `CODEOWNERS`：代码所有者表；路径到 reviewer/team 的映射。
- `CLA`：贡献者许可协议。
- `disclosure`：披露；安全问题按受控渠道告知维护方。

---

## 35. 自测题

1. 当前 Codex 仓库对外部 PR 有什么前置限制？
2. Working tree、commit、branch 和 PR 分别是什么？
3. 为什么 topic branch 不是质量证明？
4. 什么叫 atomic commit？
5. 为什么最终 squash merge 仍不意味着评审期间可以乱写 commit？
6. Commit message 与 PR title 分别承担什么作用？
7. Change size 的 800/500 行指导为什么不是机械阈值？
8. 应按什么依据拆分大型 PR？
9. Stacked PR 应说明哪些 base/dependency 信息？
10. PR 正文的 What、Why、How、Tests、Risks 各回答什么？
11. Draft 与 Ready for review 的界限是什么？
12. Codex 重点检查哪四类 breaking-change surface？
13. 新增 optional app-server 字段为什么仍需兼容性检查？
14. CODEOWNERS 能保证什么，不能保证什么？
15. `blocking-ci.yml` 中 `required` 汇总 job 为什么使用 `always()`？
16. PR-time Bazel 和 post-merge full Cargo CI 怎样分工？
17. Path-filtered job 显示 skipped 为什么可能是正常现象？
18. Clean-worktree check 能发现哪些问题？
19. Deterministic、generated drift、flaky 和 infrastructure failure 怎样区分？
20. 为什么不应一看到 CI 失败就重跑？
21. Rebase conflict 为什么必须按语义解决？
22. 高质量 review 回复应包含哪三类信息？
23. PR 实现变化后为什么还要更新正文？
24. 安全漏洞为什么不能直接写公开 PR？

---

## 36. 源码与仓库检查点

1. `docs/contributing.md`
   - 阅读外部邀请、development workflow、atomic commits、PR、review、CLA 和 security 部分。
2. `.github/pull_request_template.md`
   - 看默认模板要求外部贡献链接 issue 并写高质量说明。
3. 根目录 `AGENTS.md`
   - 找 change size、breaking changes、test guidance 和验证命令。
4. `.github/CODEOWNERS`
   - 看核心路径与敏感 CI/签名路径由谁负责。
5. `.github/workflows/README.md`
   - 比较 PR-time Bazel/fast Cargo 与 post-merge full Cargo。
6. `.github/workflows/blocking-ci.yml`
   - 找所有 required job family 和最终 `required` 汇总。
7. `.github/scripts/check_ci_results.py`
   - 看汇总脚本怎样解释 needs results。
8. `.github/workflows/bazel.yml`
   - 看平台 matrix、shards、lock check、test/clippy 和 clean worktree。
9. `.github/workflows/rust-ci.yml`
   - 看 changed-path detection、fmt、cargo shear 与 argument-comment-lint。
10. `.github/workflows/rust-ci-full.yml`
    - 看 post-merge clippy、nextest、release 和跨平台验证。
11. `.github/workflows/repo-checks.yml`
    - 看 manifest、TUI/Core boundary、format 和脚本检查。
12. `.github/actions/check-clean-worktree/action.yml`
    - 理解生成漂移怎样变成 CI 失败。
13. `.github/workflows/blob-size-policy.yml` 与 `.github/blob-size-allowlist.txt`
    - 看大文件策略和受控例外。
14. `.github/workflows/cargo-deny.yml`
    - 看依赖政策检查入口。
15. `.codex/skills/code-review-breaking-changes/SKILL.md`
    - 对照外部 integration surfaces。
16. `.codex/skills/code-review-change-size/SKILL.md`
    - 对照 800/500 行与 staged landing 指导。
17. `.codex/skills/code-review-testing/SKILL.md`
    - 对照 Agent integration test 与 test-only helper 约束。

---

## 37. 一句话总结

可审查 PR 不是把本地 diff 推到 GitHub，而是把问题、根因、不变量、实现、测试、兼容性和风险组织成一条评审者可快速验证的证据链；用原子提交和受控规模降低理解成本，用 breaking-change 清单与 CODEOWNERS 覆盖边界，用分层 CI 验证多平台构建，再以有证据的 review 迭代把当前 diff 推进到真正 mergeable 的状态。
