# 39：发布、灰度、回滚与事故响应——代码合并以后，怎样安全到达用户

第 34 章讲过怎样把修复组织成可审查的 PR，第 38 章讲过系统过载时怎样安全变慢。本章补上两者之间和之后的一整段：代码通过 review 并合入主分支以后，怎样变成用户真正运行的版本；新版本怎样逐步扩大影响范围；指标变坏时怎样止血；回滚为什么有时并不安全；事故结束后又怎样把经验变成系统能力。

> 源码基线：`4ee41929eaf4`。本章对 Codex 的构建、签名、打包和公开分发链路引用仓库事实；“1%→10%→50%→100%”之类流量灰度、值班角色和线上监控阈值是通用教学模型，不代表 OpenAI 内部部署细节。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分 merge、build、artifact、release、deploy 和 rollout；
2. 看懂 Codex release workflow 的主要依赖关系；
3. 理解版本、Tag、commit、checksum、签名和 provenance 各证明什么；
4. 区分 alpha、prerelease、stable 与灰度流量比例；
5. 设计分阶段 rollout 的观察窗口、成功条件和中止条件；
6. 理解“发布二进制”和“开放功能”为什么应尽量解耦；
7. 区分 stop、disable、rollback、roll-forward 和 data repair；
8. 判断数据库迁移、协议变化和持久化状态是否允许旧版本回来；
9. 用兼容性矩阵检查新旧 client/server 和 reader/writer 组合；
10. 在事故中先止血、再诊断，而不是边猜边扩大改动；
11. 写出时间线、影响范围、根因、促成因素和行动项；
12. 为发布和回滚路径设计可自动验证的测试。

---

## 2. 先说人话：装修商场不能只准备“开门”按钮

假设商场要更换新的收银系统。开发完成不等于可以一夜之间给全部门店切换。

更稳妥的过程是：

1. 固定一个确切版本；
2. 为不同机器制作安装包；
3. 验证安装包没有损坏，也确实来自可信发布者；
4. 先让内部测试门店使用；
5. 再选择少量普通门店；
6. 比较新旧系统的支付成功率、耗时和退款异常；
7. 指标正常才继续扩大；
8. 指标异常立即停止扩张，必要时关闭新能力或换回旧系统；
9. 恢复后保存证据并复盘。

这里至少有三个不同按钮：

```text
发布按钮：新安装包可被下载
部署按钮：某台机器装上新安装包
功能按钮：新代码路径是否真正被使用
```

把三个按钮绑成一个按钮，故障时就只剩“全开”或“全退”。把它们解耦，才可能只关闭有问题的能力，而不撤回同时包含安全修复的整个版本。

---

## 3. Merge、Release、Deploy、Rollout 不一样

### 3.1 Merge

代码进入主分支。它说明变更被仓库接受，不说明已生成用户可安装的制品。

### 3.2 Build

编译或打包源代码，生成二进制、压缩包、npm tarball、wheel 等文件。

### 3.3 Artifact

构建产生、可保存和传递的具体文件，中文常叫“制品”。同一 commit 针对不同 OS/CPU 会有不同 artifact。

### 3.4 Release

给一组制品赋予版本和发布说明，并放到用户或后续系统可取得的位置。

### 3.5 Deploy

把某个版本安装或启动在一个具体运行环境中。CLI 用户下载新包也可以理解为一种分散式部署。

### 3.6 Rollout

控制新版本或新功能从小范围逐渐扩大到全部目标对象的过程。

因此：

```text
merged ≠ built
built ≠ released
released ≠ installed everywhere
installed ≠ feature enabled
```

排查时一定要问“哪一步完成了”，不要只说“已经上线”。

---

## 4. Codex 仓库能确认的发布入口

`.github/workflows/rust-release.yml` 由形如下面的 Git Tag 触发：

```text
rust-v1.2.3
rust-v1.2.3-alpha.1
rust-v1.2.3-beta.1
```

工作流开头的 `tag-check` 会检查：

- 当前确实由 Tag 触发；
- Tag 符合允许的版本格式；
- Tag 中版本与 `codex-rs/Cargo.toml` 的版本一致。

可以把它理解为发布入口的第一条不变量：

```text
对外声称的版本 == 源码中声明的版本
```

否则，用户看到 `1.2.3`，二进制内部却声称是 `1.2.2`，日志、故障定位和依赖判断都会混乱。

---

## 5. 一条简化的 Codex 制品流水线

根据当前 workflow，可以画成：

```text
rust-v* Tag
    │
    ▼
校验 Tag 与 Cargo 版本
    │
    ├── Linux 多架构 build → strip → sign → package
    ├── macOS 多架构 build → sign/notarize → package → verify
    ├── Windows 多架构 build/package
    └── supplemental artifacts
                 │
                 ▼
       汇总制品 + SHA-256 清单 + config schema
                 │
                 ▼
             GitHub Release
              /    |     \
             /     |      \
           R2   DotSlash   npm / WinGet / other stable-only jobs
```

图中省略了很多平台细节，但保留了重要边界：构建任务成功不直接等于发布成功；最终 `release` job 明确依赖各平台构建、macOS 最终验证和补充制品任务全部成功。

这叫 gate：只有前置条件满足，后续动作才被允许。

---

## 6. 为什么要为每个平台分别构建

一个 Rust 项目虽然共享源代码，最终二进制仍受这些条件影响：

- CPU 架构：`x86_64`、`aarch64`；
- OS：Linux、macOS、Windows；
- C runtime 和链接方式；
- 平台专属 sandbox/helper；
- 可执行文件后缀和包装格式；
- 签名、公证要求。

当前 Codex workflow 的 matrix 明确列出多个 target。例如 Linux 主包使用 MUSL target，macOS 同时构建 Intel 和 Apple Silicon 版本。

`matrix` 不是数学矩阵，在 GitHub Actions 中表示“一组参数组合，各跑一份相似 job”。

一个平台成功不能证明其他平台成功，因此发布汇总必须等待所有被要求的平台结果。

---

## 7. Artifact identity：你回滚的到底是哪一个文件

讨论回滚前必须能唯一确定当前和目标制品。至少记录：

| 维度 | 示例 | 作用 |
|---|---|---|
| Version | `1.2.3-alpha.1` | 面向人和包管理器的版本 |
| Git tag | `rust-v1.2.3-alpha.1` | 发布入口引用 |
| Commit SHA | `abc123...` | 精确源码快照 |
| Target | `aarch64-apple-darwin` | OS/CPU 构建目标 |
| Artifact name | `codex-aarch64-apple-darwin.tar.gz` | 实际文件 |
| Digest | SHA-256 值 | 验证文件字节未变化 |
| Signature | `.sigstore` 或平台签名 | 验证发布身份与完整性 |

只记录“回滚到昨天版本”不够。昨天可能有多个 alpha，包管理器缓存也可能使不同机器拿到不同字节。

---

## 8. Checksum、签名和公证分别解决什么

### 8.1 Checksum

Checksum 是文件内容计算出的摘要。当前 release job 会为 package archive 生成 `codex-package_SHA256SUMS`。

它主要回答：

```text
我拿到的文件，字节是否和发布者列出的摘要一致？
```

如果攻击者能同时替换文件和摘要，裸 checksum 本身不能证明发布者身份。

### 8.2 Code signing

签名用发布者控制的密钥对制品摘要进行认证，主要回答：

```text
这个制品是否由持有可信签名身份的一方签出，签名后是否被修改？
```

当前 workflow 对 Linux artifact 使用签名动作；macOS 二进制走受保护的代码签名环境。

### 8.3 Notarization

macOS 公证是平台服务对签名软件的额外检查和登记。当前流程还会对 DMG 执行签名、公证和 staple。

`staple` 可以理解为把公证票据附着到制品，使系统在某些离线场景也能验证。

三者是互补关系，不是三种同名安全措施。

---

## 9. Strip 与 symbols：为什么发布包和调试信息分开

当前 workflow 会先归档 symbols，再 strip binaries。

- `symbol`：把机器地址还原为函数名、源码位置所需的调试信息；
- `strip`：从发布二进制移除部分符号/调试信息，减小体积；
- `symbols archive`：单独保存调试信息，供 crash 分析使用。

这形成一个重要事故能力：用户安装包可以更小，但维护者仍能把崩溃地址映射回有意义的调用栈。

若制品已发布，却没有保存对应 symbols，事后可能只能看到一串地址，显著降低定位能力。

---

## 10. Stable、alpha、beta 与 canary 不在同一维度

这些词经常混用：

| 概念 | 回答的问题 |
|---|---|
| Stable release | 这个版本被标记为什么成熟度/分发通道？ |
| Alpha/Beta | 版本是否是预发布版本？ |
| Canary | 哪一小组对象最先接触新版本或新能力？ |
| Rollout percentage | 当前影响范围有多大？ |
| Feature stage | 某项能力在代码中处于开发、实验、稳定或淘汰状态？ |

当前 Codex release workflow 会把带 `-` 后缀的版本标成 prerelease，并避免把它设为 latest；稳定版本和某些编号 alpha 才会进入特定 npm 发布分支。

这能确认“发布通道”，但不能据此推断线上实际有多少用户收到版本。

另一个易错点：仓库的 `v8-canary.yml` 是验证 V8/rusty_v8 构建组合的 canary workflow，不是证据表明 Codex 产品按某个用户百分比灰度。

---

## 11. Feature Flag：让“二进制到达”与“风险暴露”解耦

`codex-rs/features/src/lib.rs` 中，`FeatureSpec` 包含：

```rust
pub struct FeatureSpec {
    pub id: Feature,
    pub key: &'static str,
    pub stage: Stage,
    pub default_enabled: bool,
}
```

`Features` 又提供 `enabled`、`enable`、`disable` 和 `set_enabled`。

这说明当前代码确实具有按配置决定代码路径的机制。它带来一种发布策略：

```text
先让含新代码的二进制稳定分发
             ↓
保持新 feature 默认关闭
             ↓
在受控对象上开启并观察
             ↓
逐步扩大或快速关闭
```

但要注意：仓库中的本地 feature flag 不自动等于服务端集中式百分比开关。是否能远程改变、按用户分组和多久生效，必须从相应配置分发系统另行确认。

---

## 12. Feature stage 不是运行时开关状态

当前 `Stage` 有：

- `UnderDevelopment`；
- `Experimental`；
- `Stable`；
- `Deprecated`；
- `Removed`。

`stage` 描述生命周期/产品成熟度，`default_enabled` 描述默认是否开启，`Features.enabled(...)` 才表示解析配置后的有效状态。

例如：

```text
Stable + default_enabled=true
```

通常表示稳定且默认开启；但源码注释明确说 Stable flag 仍可为临时启停保留。这使事故止血不一定要撤回整个 artifact。

不要写出这种错误判断：

```rust
if feature.stage() == Stage::Stable {
    // 错误地假设一定已经启用
}
```

成熟度和当前状态是两个维度。

---

## 13. 通用灰度模型：不是只看错误率

下面是一种教学用阶段：

```text
内部验证 → 1% → 10% → 25% → 50% → 100%
```

每个阶段都应该定义：

1. 谁被选中；
2. 至少观察多久；
3. 至少需要多少样本；
4. 与哪个 control group 比较；
5. 哪些指标必须正常；
6. 哪个阈值触发暂停；
7. 哪个阈值触发回退；
8. 谁可以做决定。

百分比只是“影响面旋钮”。没有观测窗口和停止条件的百分比，不构成安全灰度。

---

## 14. 为什么阶段不能升得太快

假设 1% 阶段只观察 30 秒，恰好没有用户执行 resume：

```text
新版本 resume 有 20% 失败率
30 秒内 resume 样本数 = 0
观测结果 = 看起来一切正常
```

这不是功能健康，只是没有覆盖风险路径。

阶段晋升应同时满足：

- 最小时间窗口；
- 最小关键事件样本；
- 不同平台/地区/配置覆盖；
- 指标没有持续恶化趋势；
- 日志中没有新的高严重度错误簇。

低频但高风险操作可能需要合成测试或主动验证，不能等自然流量碰巧覆盖。

---

## 15. Control group 与基线

只看新版本自身指标容易误判。例如全站下游 API 同时抖动，新版本和旧版本都会变慢。

理想比较是：

```text
实验组：新版本/新 feature
对照组：旧版本/关闭 feature
```

然后比较同一时间窗、尽量相近用户和工作负载的：

- 成功率；
- p50/p95/p99；
- crash-free rate；
- cancel/timeout 比例；
- 资源用量；
- 数据正确性；
- 用户可见关键行为。

若实验组和对照组一起恶化，优先怀疑共享依赖；若只有实验组恶化，新变更嫌疑更大。这仍是相关性证据，不是自动证明因果。

---

## 16. Guardrail metric 与业务成功指标

### 16.1 Guardrail

用于防止明显伤害的边界指标，例如：

- crash rate；
- fatal error rate；
- thread resume failure；
- 数据损坏或完整性检查失败；
- 审批/sandbox 绕过信号；
- p99 latency；
- 内存和队列持续增长。

### 16.2 Success metric

用于判断变更是否真正实现目标，例如：

- 首次成功率提高；
- TTFT 降低；
- 工具调用完成率提高；
- 用户重试次数下降。

一个版本“没有触发 guardrail”只说明没看到明显伤害，不代表它带来了预期收益。

---

## 17. Abort threshold 必须提前写

如果等故障发生后才讨论“多高算严重”，团队会在压力下不断移动标准。

教学示例：

```text
暂停扩张：实验组 resume failure 连续 10 分钟高于对照组 1 个百分点
立即关闭：出现任何已确认的数据损坏或安全边界绕过
回退 artifact：crash-free rate 显著下降，且 feature flag 无法隔离路径
```

真实阈值必须由业务基线和风险容忍度决定，上面的数字不是 Codex 线上配置。

阈值还要说明：

- 使用绝对值还是相对值；
- 要连续几个窗口；
- 最小样本数；
- 是否区分平台；
- 谁能 override；
- override 如何记录。

---

## 18. 出现异常后的第一目标：Stop the bleeding

事故早期最重要的问题不是“哪一行代码错了”，而是：

```text
怎样让新增伤害停止？
```

常见止血动作从小到大：

1. 暂停 rollout，不再扩大；
2. 关闭有问题的 feature；
3. 降低非关键工作或入口流量；
4. 将流量切回健康路径；
5. 回滚 artifact；
6. 若回滚不安全，roll-forward 一个最小修复；
7. 隔离或修复已产生的坏数据。

先止血不是放弃根因分析，而是把影响面固定下来，为分析争取稳定环境。

---

## 19. 五个动作不要都叫“回滚”

| 动作 | 实际含义 | 适合情况 |
|---|---|---|
| Halt/Pause | 停止继续扩大 | 当前范围尚可控，需要观察 |
| Disable | 关闭一个功能路径 | 问题被 flag 隔离，基础版本仍健康 |
| Rollback | 恢复旧 artifact/config | 旧版本与当前数据/协议兼容 |
| Roll-forward | 发布含修复的新版本 | 旧版本回来不安全或修复极小明确 |
| Repair | 修复已写入的数据 | 仅切版本不能消除既有损害 |

如果事故记录只写“已回滚”，后续读者无法知道究竟做了什么，也无法解释为何影响仍持续。

---

## 20. 为什么“部署旧版本”不一定是真回滚

新版本可能已经改变了环境：

- 新增/删除数据库列或表；
- 重写本地 rollout 文件；
- 写入旧版本不认识的 enum variant；
- 发布旧 client 不理解的新事件；
- 改变缓存键或序列化格式；
- 触发不可逆外部副作用；
- 更新了密钥、权限或基础设施。

此时旧二进制即使成功启动，也可能：

- 无法读取新数据；
- 把新字段覆盖丢失；
- 重复执行已完成动作；
- 与新 server 协议不兼容；
- 表面健康但静默损坏数据。

所以回滚计划必须回答：

```text
旧 reader 能否读新 writer 写出的数据？
旧 writer 会不会破坏新 schema 中的数据？
新旧进程短暂并存是否安全？
```

---

## 21. Codex 状态迁移提供的真实兼容案例

`codex-rs/state/src/migrations.rs` 中的 `runtime_migrator` 设置：

```rust
ignore_missing: true
```

源码注释给出明确目的：允许旧 Codex binary 打开已经被并行运行的新 binary 迁移过的数据库。

同时，它仍然校验自己认识的 migration checksum。这意味着策略不是“忽略一切迁移问题”，而是只放宽：

```text
数据库存在比当前二进制更新的已应用 migration
```

对应测试 `open_state_sqlite_tolerates_newer_applied_migrations` 先写入未来版本迁移记录，证明严格 migrator 会拒绝，再证明 runtime migrator 可以打开。

这是回滚友好设计的一部分，但不能推出所有 schema 变化都自动向后兼容。若新 migration 删除了旧代码必需的列，`ignore_missing` 也救不了旧查询。

---

## 22. Expand–Migrate–Contract

数据库演进常用三步：

### 22.1 Expand

先增加新结构，同时保持旧 reader/writer 可用。例如新增 nullable 列或新表，而不马上删旧列。

### 22.2 Migrate

逐步回填旧数据，必要时双写；监控覆盖率和错误。

### 22.3 Contract

确认所有运行实例和可回滚版本都不再依赖旧结构后，才删除旧列或兼容代码。

时间线示例：

```text
R1: 添加 new_value，仍读 old_value
R2: 双写 old_value + new_value，后台回填
R3: 读 new_value，必要时 fallback old_value
R4: 所有旧版本退出回滚窗口后，停止 old_value
R5: 删除 old_value
```

如果 R1 直接删除旧列，R0 就无法安全回滚。

---

## 23. 持久化迁移还要考虑 crash consistency

当前 thread-store rollout migration 的 `publish.rs` 使用 staged temporary path、`.pending` journal、写入同步和恢复清理。

它表达了一个关键问题：迁移可能在“新文件已写入”和“SQLite metadata 已完成”之间崩溃。

可靠迁移应定义每个中断点重启后的行为：

```text
开始前崩溃 → 原数据未变，可重试
临时文件写一半 → 不把它当正式文件
新文件发布后崩溃 → journal 指示继续完成 metadata
全部完成 → 删除 journal
```

发布事故不只来自新算法算错，也可能来自进程恰好在迁移中间退出。

---

## 24. 协议兼容性要画组合矩阵

对于 client/server 分离系统，发布时可能同时存在：

| Client | Server | 必须怎样 |
|---|---|---|
| old | old | 原行为正常 |
| new | old | 新 client 能降级或得到明确 unsupported |
| old | new | 新 server 保持旧 wire contract |
| new | new | 新能力正常 |

若只测试 `new/new`，灰度期间最常见的混合组合反而没有证据。

需要检查：

- 新字段是否 optional；
- 未知字段是否被安全忽略；
- 新 enum variant 对旧 client 会怎样；
- request/notification 名称是否变化；
- 恢复旧 rollout 是否仍可解析；
- 同一 thread 是否会被新旧进程交替打开。

仓库 `AGENTS.md` 也把 app-server API、raw response events、CLI 参数、配置加载和恢复旧 rollout 列为 breaking-change 重点。

---

## 25. 配置与 Feature Flag 本身也要版本化

关闭 flag 看似最轻，但也可能失败：

- 老版本根本不认识这个 key；
- 配置缓存很久才刷新；
- flag 只在进程启动时读取；
- 部分实例收到 `false`，部分仍是 `true`；
- 关闭路径长期没测试，已经失效；
- 新代码写出的状态无法由旧路径读取。

因此 runbook 要写清：

```text
flag 名称是什么？
在哪一层读取？
改变后多久生效？
是否需要重启？
怎样证明全部目标已收敛？
关闭后哪些数据仍需修复？
```

Kill switch 不是拥有一个布尔变量，而是拥有一条被测试、可观测、能及时收敛的关闭链路。

---

## 26. 一个完整教学事故：Resume 灰度失败

假设新版本优化 thread 索引，并包含 migration。灰度到 10% 后出现：

- 总体请求成功率只下降 0.1%；
- 但 macOS 上 cold resume failure 从 0.2% 升到 8%；
- 新建 thread 正常；
- warm resume 正常；
- 错误集中在较老 rollout。

### 26.1 错误做法

看到总体成功率变化小，继续扩大到 50%；同时多人直接修改 migration 猜测修复。

### 26.2 更稳妥的做法

1. 暂停 rollout 在 10%；
2. 单独关闭后台迁移 feature，若它可隔离；
3. 保存失败样本的版本、平台、rollout 格式和最后事件；
4. 比较 10% 实验组与旧版本对照组；
5. 复现 `old rollout + new reader`；
6. 判断已经迁移的数据能否被旧版本安全读取；
7. 若能，回滚 artifact；若不能，保持版本并 roll-forward parser 修复；
8. 对受影响 rollout 做幂等 repair；
9. 修复后重新从小范围开始，而不是直接回到 50%。

关键点是总体指标会稀释关键子路径，必须按平台、数据年代和操作类型切片。

---

## 27. 事故响应的四个阶段

### 27.1 Detect

通过告警、用户报告、日志错误簇或人工观察确认异常。

### 27.2 Contain

暂停扩张、关闭 feature、切流或回滚，使影响不再增长。

### 27.3 Recover

恢复服务目标，验证队列、错误率、数据和用户操作真正回到正常。

### 27.4 Learn

完成根因分析和行动项，使同类问题更难发生、更快发现、更容易恢复。

事故没有在“图表变绿”时完全结束。如果 backlog 仍在处理、坏数据仍存在或用户需要重新操作，恢复工作还没有完成。

---

## 28. 事故角色为什么要分开

小事故可以一人兼任，但职责仍应清楚：

| 角色 | 主要工作 |
|---|---|
| Incident commander | 维护全局、定优先级、批准高风险动作 |
| Operations lead | 执行止血、回滚、切流、恢复验证 |
| Investigation lead | 收集证据、形成和证伪技术假设 |
| Communications lead | 向用户、支持和相关团队同步一致信息 |
| Scribe | 记录时间线、决定、命令、指标和结果 |

如果所有人都同时改代码，就没人维护影响面和决定记录；如果所有人都在聊天，又没人真正执行止血。

---

## 29. 事故时间线应该记录什么

一个有用时间线不是只写“10:00 发生事故”。应包含：

```text
09:42 版本 1.2.3 rollout 从 10% 升到 25%
09:49 macOS cold-resume failure 告警触发，8.1%
09:52 值班确认实验组显著高于 control
09:55 暂停 rollout，保持 25%
10:01 关闭 background migration flag
10:07 新失败停止增长，积压仍为 1,240
10:18 确认旧二进制不能安全读取部分新格式，决定 roll-forward
10:46 修复版本进入 1% 验证
11:20 错误率恢复，开始 repair backlog
```

每一项最好带证据链接或查询条件。时间要统一时区。

时间线既帮助当下协作，也帮助事后区分因果顺序。

---

## 30. Evidence preservation：别在修复时毁掉证据

事故中容易发生：重启清掉内存状态、日志滚动、临时文件被 cleanup、dashboard 查询窗口改变。

在不扩大风险的前提下，应保存：

- artifact version、commit、digest；
- 生效配置和 feature 状态；
- 失败请求/thread/turn 的稳定 ID；
- 关键日志和 trace；
- 指标查询语句、过滤条件和时间窗；
- crash dump 与匹配 symbols；
- 迁移前后 schema/version；
- 执行过的操作和结果。

注意隐私和 secret：保存证据不等于把 token、用户内容和凭据复制进公共工单。

---

## 31. 沟通内容要事实化

事故更新可以用固定结构：

```text
现象：macOS cold resume 失败率升高
影响：09:42 后进入实验组且恢复旧 thread 的部分用户
当前状态：rollout 已暂停，迁移功能已关闭
已知：新建 thread 和 warm resume 未受影响
未知：是否存在需要修复的已迁移 rollout
下一步：验证旧版本读取兼容性，10:20 再更新
```

避免：

- 未验证就宣布根因；
- 只说“正在看”；
- 用“全部恢复”掩盖仍在修复的数据；
- 每个频道给出不同版本的事实。

---

## 32. Root cause 与 trigger 不一样

假设 rollout 到 25% 后事故出现：

- Trigger：流量比例提高，使更多旧 rollout 被迁移；
- Direct cause：parser 不能处理某种旧记录；
- Contributing factor：测试 fixture 没覆盖该历史格式；
- Detection gap：总体成功率掩盖 cold-resume 子路径；
- Recovery gap：关闭 flag 只阻止新迁移，不能修复已迁移数据。

只写“发布新版本导致事故”没有学习价值。新版本是时间关联，真正需要修复的是具体失效机制和防线缺口。

---

## 33. Blameless 不等于没有责任

Blameless postmortem 的意思不是“谁都没做错”，而是避免把结论停在“某人粗心”。

更有价值的问题是：

- 为什么一个合理的人在当时会做这个决定？
- 哪些信息缺失或误导？
- 哪个自动 gate 本应阻止扩大？
- 哪个测试本应覆盖旧格式？
- 为什么 kill switch 没有覆盖已迁移数据？
- 哪些职责和权限不清晰？

人仍对行动项负责，但系统改进目标是减少对完美记忆和临场英雄主义的依赖。

---

## 34. Postmortem 的推荐结构

1. 摘要；
2. 用户影响；
3. 起止时间和关键时间线；
4. 检测方式；
5. 技术根因；
6. 促成因素；
7. 哪些防线有效；
8. 哪些防线失效；
9. 恢复过程；
10. 行动项、owner 和期限；
11. 仍待回答的问题。

不要把全文写成日志流水账。摘要应让不了解系统的人也能明白：什么坏了、影响谁、多久、为何、怎样恢复。

---

## 35. 好行动项必须改变系统

弱行动项：

```text
以后发布更小心。
提醒大家检查 migration。
```

强行动项：

```text
为最近五个历史 rollout fixture 增加 old-data/new-reader 集成测试。
rollout gate 新增按 platform + resume_kind 切片的失败率阈值。
发布前自动验证 N-1 binary 能打开 N migration 后的数据库。
为 migration 增加 dry-run 统计和独立 kill switch。
runbook 增加 artifact digest、flag 收敛验证和 data-repair 步骤。
```

每个行动项需要：

- 可验证的完成条件；
- 一个 owner；
- 优先级和期限；
- 对应哪个故障机制；
- 如果暂不做，明确接受什么风险。

---

## 36. Release runbook 应包含什么

发布前：

- 精确版本、Tag、commit；
- 变更摘要和高风险面；
- schema/protocol/config 兼容性；
- 制品和签名验证；
- 回滚目标版本；
- feature 默认值；
- dashboard、query 和告警；
- 阶段、窗口、样本、abort threshold；
- 当班 owner 和沟通渠道。

发布中：

- 每次阶段变更的时间；
- 实验组和 control 指标；
- 新错误簇；
- config/flag 实际收敛状态；
- go/hold/rollback 决定及理由。

发布后：

- 100% 后继续观察；
- backlog 和 delayed side effects；
- 旧版本存量；
- rollback window 何时关闭；
- 临时 flag/兼容代码的清理计划。

---

## 37. 发布测试分层

### 37.1 Build-time tests

- Tag 与声明版本一致；
- 每个平台成功编译；
- 必需文件存在；
- package 内入口和 helper 正确；
- schema/manifest 生成无漂移。

### 37.2 Artifact tests

- 解压/安装成功；
- `--version` 与 Tag 一致；
- checksum 匹配；
- 签名和公证验证；
- 最小命令 smoke test；
- symbols 与制品版本对应。

### 37.3 Compatibility tests

- old client/new server；
- new client/old server；
- old data/new reader；
- new data/old reader；
- 新旧进程并存；
- flag 开/关两条路径。

### 37.4 Rollout tests

- stage 晋升条件正确；
- abort threshold 会暂停；
- kill switch 真正收敛；
- rollback 目标可取得；
- rollback 后指标恢复；
- repair 可重试且幂等。

---

## 38. Rollback drill：别等事故才第一次练

定期演练可以发现：

- 旧 artifact 已被清理；
- 安装脚本不支持降级；
- 数据 migration 不兼容；
- 权限只允许发布、不允许回退；
- dashboard 无法按版本过滤；
- flag 要重启才能生效；
- runbook 中命令已经过时；
- owner 联系方式失效。

演练不一定在真实用户环境执行破坏性降级。可以在隔离环境中使用生产等价 artifact 和脱敏 fixture，验证完整步骤及证据。

---

## 39. 常见反模式

### 39.1 Big bang release

直接 100%，故障时影响面最大，也失去对照组。

### 39.2 “CI 绿了，所以可以全量”

CI 证明已覆盖测试通过，不证明未知平台、历史数据和真实负载全部安全。

### 39.3 只看总体平均值

少数高风险平台或路径会被平均值稀释。

### 39.4 发布和 feature 同时全开

出现问题时难以区分 artifact、配置和功能路径。

### 39.5 回滚前不检查数据兼容

旧进程启动成功不等于数据读写安全。

### 39.6 修复时不断改多个变量

同时切版本、改 flag、扩容、改数据，让恢复原因不可判断。

### 39.7 Dashboard 变绿就结束

可能仍有 backlog、坏数据、失败用户和关闭中的恢复任务。

### 39.8 Postmortem 只写“加强意识”

没有 gate、测试、观测或自动化变化，同类事故仍会复发。

---

## 40. 阅读 release workflow 的方法

面对一千多行 YAML，不要从第一行逐字背。按问题搜索：

```bash
rg -n '^on:|tags:|tag-check|needs:' .github/workflows/rust-release.yml
rg -n 'Cargo build|Sign|notarize|Verify|Package' .github/workflows/rust-release.yml
rg -n 'SHA256|Create GitHub Release|publish-npm|winget' .github/workflows/rust-release.yml
```

然后画 DAG：

1. 什么事件触发；
2. 哪些 job 可并行；
3. 最终发布依赖谁；
4. 哪些 job 只允许 stable；
5. 哪些权限和 protected environment 才能签名/发布；
6. 哪一步产生、验证、再分发同一 artifact。

阅读重点是依赖和不变量，不是记住 action 的每个版本号。

---

## 41. 一份可直接套用的 Go/Hold/Rollback 清单

### Go

- 当前阶段达到最小观察时间和样本；
- guardrail 正常；
- success metric 符合预期或至少不退化；
- 没有未知高严重度错误簇；
- 平台和关键路径覆盖足够；
- on-call 和 rollback 能力仍可用。

### Hold

- 样本不足；
- 指标波动但未越界；
- 关键 dashboard 缺失；
- 新异常尚未分类；
- 相关依赖正在故障，无法正确比较。

### Rollback/Disable

- 安全或数据完整性受损；
- guardrail 持续越界；
- 实验组显著差于 control；
- 影响继续增长；
- 修复时间大于安全回退时间；
- 已确认回退与当前数据/协议兼容。

---

## 42. 本章词汇表

| 英文 | 中文直觉 | 本章中的具体含义 |
|---|---|---|
| Release | 发布 | 给制品赋予版本并使其可分发 |
| Deployment | 部署 | 将具体版本安装/启动到环境 |
| Rollout | 推广/逐步放量 | 控制影响范围逐步扩大 |
| Artifact | 制品 | 构建产生的二进制、压缩包、tarball 等 |
| Build matrix | 构建矩阵 | 对多个 OS/CPU 参数组合分别构建 |
| Gate | 门禁 | 前置检查通过才允许后续动作 |
| Tag | 标签 | 指向 Git 对象、触发版本发布的命名引用 |
| Commit SHA | 提交摘要 | 唯一定位源码快照的哈希 |
| Checksum/digest | 校验摘要 | 从制品字节计算的完整性标识 |
| Code signing | 代码签名 | 用可信身份认证制品来源和完整性 |
| Notarization | 公证 | 平台对签名软件的检查与登记 |
| Staple | 附票 | 把公证票据附着到制品 |
| Strip | 剥离符号 | 从发布二进制移除部分调试信息 |
| Debug symbols | 调试符号 | 把机器地址映射到函数/源码的信息 |
| Prerelease | 预发布 | alpha/beta 等非稳定版本标记 |
| Stable | 稳定 | 面向常规使用的成熟发布通道/feature 阶段 |
| Canary | 金丝雀 | 最先接触新变化的小范围对象或验证任务 |
| Control group | 对照组 | 保持旧版本/旧行为、用于同期比较的对象 |
| Guardrail | 护栏指标 | 越界时应暂停或回退的伤害边界 |
| Abort threshold | 中止阈值 | 提前定义的暂停/回退条件 |
| Observation window | 观察窗口 | 每阶段至少持续观测的时间范围 |
| Blast radius | 爆炸半径 | 故障可影响的对象和范围 |
| Feature flag | 功能开关 | 运行时选择是否进入某代码路径的配置 |
| Kill switch | 紧急关闭开关 | 为事故止血设计且可快速收敛的关闭能力 |
| Halt/Pause | 暂停 | 停止继续扩大 rollout |
| Rollback | 回滚 | 恢复到旧 artifact 或配置 |
| Roll-forward | 向前修复 | 发布一个更新版本修复当前问题 |
| Data repair | 数据修复 | 修复版本切换无法消除的既有坏数据 |
| Migration | 迁移 | 将 schema 或持久化数据演进到新形式 |
| Backfill | 回填 | 为已有记录补充新结构所需数据 |
| Dual write | 双写 | 过渡期同时写旧结构和新结构 |
| Expand-contract | 扩展-收缩 | 先兼容增加，迁移后再删除旧结构 |
| Backward compatible | 向后兼容 | 新实现仍能处理旧输入/调用者 |
| Forward compatible | 向前兼容 | 旧实现能容忍新实现产生的可预期扩展 |
| Mixed version | 混合版本 | 新旧实例/client/server 同时存在 |
| Incident | 事故 | 对用户、数据、安全或 SLO 造成显著影响的事件 |
| Mitigation | 缓解/止血 | 降低当前影响，不一定修掉根因 |
| Recovery | 恢复 | 服务和数据回到目标状态 |
| Incident commander | 事故指挥 | 维护全局优先级和关键决定的人 |
| Scribe | 记录员 | 维护时间线、动作和证据的人 |
| Runbook | 操作手册 | 可执行、可验证的发布/恢复步骤 |
| Postmortem | 事故复盘 | 事后解释机制、影响和系统改进 |
| Root cause | 根因 | 导致失效的核心技术/系统机制 |
| Trigger | 触发因素 | 让潜在缺陷在该时刻暴露的事件 |
| Contributing factor | 促成因素 | 放大概率、影响或恢复时间的条件 |
| Blameless | 非责备式 | 不把分析停在人为粗心，追问系统条件 |
| Action item | 行动项 | 有 owner、期限和验收条件的改进工作 |
| Provenance | 来源证明 | 制品从哪份源码、经什么可信过程产生的证据 |

### 发布代码单词短语拆解

- `release`：释放；工程中指正式产生并分发一个版本。
- `deploy`：部署；把版本放进具体运行位置。
- `roll out`：铺开；逐步扩大新版覆盖。
- `roll back`：向后滚；恢复旧版本或旧配置。
- `roll forward`：向前滚；用更新修复版本替代问题版本。
- `artifact`：人工制品；构建系统产生的文件。
- `asset`：资源；GitHub Release 中附加的下载文件。
- `bundle`：捆绑包；一组需一起分发的二进制和资源。
- `package`：包；按特定格式组织的可安装/可分发内容。
- `archive`：归档；`.tar.gz`、`.zip` 等压缩集合。
- `target`：目标；编译的 CPU/OS/ABI 组合。
- `matrix`：矩阵；CI 中批量展开的参数组合。
- `stage`：阶段；既可能指 rollout 阶段，也可能指准备文件的 staging 动作，要看上下文。
- `staging`：预备环境/暂存；正式发布前的验证位置或准备动作。
- `latest`：最新通道；包管理器解析到的默认新版本标记。
- `prerelease`：发布之前；alpha/beta 等预发布版本。
- `manifest`：清单；列出包内容、摘要或元数据的文件。
- `checksum`：校验和；检测字节变化的摘要。
- `digest`：摘要；常指 SHA-256 等内容哈希。
- `sign`：签名；用可信密钥认证来源。
- `verify`：验证；检查签名、摘要、包结构或行为。
- `notarize`：公证；提交平台服务检查登记。
- `strip`：剥离；移除发布二进制中的调试信息。
- `symbol`：符号；函数、变量或源码定位信息。
- `gate`：门；未满足条件就不继续。
- `promote`：晋升；把制品/流量推进到下一阶段。
- `hold`：保持；暂不扩大也不立即回退。
- `abort`：中止；停止正在进行的 rollout/操作。
- `threshold`：阈值；超过后触发明确动作的边界。
- `baseline`：基线；用于比较的历史或对照水平。
- `cohort`：群组；按规则选中的一批对象。
- `blast radius`：爆炸半径；最坏影响范围。
- `mitigate`：缓解；先减少伤害。
- `contain`：遏制；把影响限制在现有边界。
- `recover`：恢复；回到可接受服务状态。
- `repair`：修复；改正已经产生的数据或状态。
- `revert`：还原提交；产生一个反向代码变更，不等同于已部署回滚。
- `pin`：固定；锁定确切版本，避免自动漂移。
- `drain`：排空；停止接新工作并处理已有工作。
- `runbook`：操作手册；事故时按步骤执行的流程。
- `postmortem`：事后检查；事故复盘文档和会议。
- `owner`：负责人；对行动项结果负责的人。

---

## 43. 自测题

1. Merge、release、deploy 和 rollout 有什么区别？
2. 为什么 released 不等于 feature enabled？
3. Codex release 的入口怎样保证 Tag 与 Cargo version 一致？
4. 为什么构建任务要按 target matrix 展开？
5. Version、commit SHA 和 artifact digest 各定位什么？
6. Checksum 为什么不能单独证明发布者身份？
7. 签名、公证和 staple 各有什么作用？
8. 为什么要在 strip 前保存 symbols？
9. Alpha 与 canary 为什么不是同一个维度？
10. 为什么 `v8-canary.yml` 不能证明产品流量灰度比例？
11. Feature stage、default enabled 和 effective enabled 有什么区别？
12. 一个灰度阶段除了百分比还必须定义什么？
13. 为什么时间足够但样本不足仍不能晋升？
14. Control group 怎样帮助区分共享依赖故障？
15. Guardrail metric 与 success metric 有何不同？
16. Abort threshold 为什么应在发布前确定？
17. Stop the bleeding 为什么早于完整根因分析？
18. Pause、disable、rollback、roll-forward、repair 分别是什么？
19. 为什么旧二进制能启动不等于回滚安全？
20. `runtime_migrator(ignore_missing=true)` 放宽了哪一种情况，又没有放宽什么？
21. Expand–migrate–contract 怎样保留回滚窗口？
22. `.pending` journal 怎样帮助 crash consistency？
23. 为什么要测试 old/new client/server 四种组合？
24. Kill switch 为什么必须验证传播时间和收敛？
25. 总体成功率怎样掩盖 macOS cold resume 故障？
26. Incident response 的 detect、contain、recover、learn 分别是什么？
27. 为什么技术调查和事故指挥最好职责分离？
28. 一条有用的事故时间线至少记录什么？
29. Root cause、trigger 和 contributing factor 有何区别？
30. 什么样的 postmortem action item 才可验收？
31. 为什么 100% rollout 后仍要观察？
32. Rollback drill 最可能提前发现哪些问题？

---

## 44. 源码检查点

1. `.github/workflows/rust-release.yml`
   - 查看 Tag 触发、`tag-check`、多平台 matrix、签名/公证/验证、checksum、GitHub Release、npm 与 stable-only jobs。
2. `.github/workflows/rust-release-windows.yml`
   - 查看 Windows x64/ARM64 构建、打包和验证边界。
3. `.github/workflows/r2-release.yml`
   - 查看 `assets` 与 `finalize` 两阶段分发参数。
4. `.github/scripts/publish_r2_release.py`
   - 查看版本格式校验、release metadata 和 staged publish 行为。
5. `.github/scripts/build-codex-package-archive.sh`
   - 查看 primary/app-server bundle、target resource 和 archive 生成。
6. `.github/scripts/archive-release-symbols-and-strip-binaries.sh`
   - 查看 symbols archive 与 strip 顺序。
7. `.github/scripts/macos-signing/sign_macos_code.sh`
   - 查看 native codesign 与 rcodesign backend 的统一入口。
8. `.github/scripts/macos-signing/notarize_macos_dmg_with_akv.sh`
   - 查看 DMG notarize 与 staple。
9. `.github/actions/linux-code-sign/action.yml`
   - 查看 Linux artifact 签名产物。
10. `.github/workflows/README.md`
    - 区分 PR 快速验证和 main 上完整 Cargo 验证。
11. `codex-rs/features/src/lib.rs`
    - 查看 `Stage`、`FeatureSpec`、`Features::with_defaults/enabled/enable/disable`。
12. `codex-rs/features/src/tests.rs`
    - 查看默认值、显式启停、legacy alias 和 materialize 测试。
13. `codex-rs/state/src/migrations.rs`
    - 查看 runtime migrator 对更高已应用 migration 的容忍边界。
14. `codex-rs/state/src/runtime.rs`
    - 查看旧 binary 容忍 future migration record 的测试。
15. `codex-rs/state/src/migrations_tests.rs`
    - 查看 released migrator 与后续 migration 兼容的测试案例。
16. `codex-rs/state/migrations/0047_rollout_migration_state.sql`
    - 查看后台 rollout migration 的 checkpoint 和 skipped records。
17. `codex-rs/thread-store/src/local/rollout_migration/publish.rs`
    - 查看 temporary path、`.pending` journal、sync 和 crash 恢复语义。
18. `AGENTS.md`
    - 查看 breaking-change、schema、lock、测试与发布相关仓库规则。

---

## 45. 一句话总结

安全发布不是“CI 绿后把新版本推到 100%”，而是先用 Tag、commit、target、digest、签名和公证确定可信制品，再把 artifact 分发与 feature 暴露解耦，以 control group、观察窗口、最小样本、guardrail 和预先定义的 abort threshold 逐步扩大影响；异常时先暂停和止血，再依据数据、协议与迁移兼容性选择 disable、rollback、roll-forward 或 repair；最后用时间线、根因与促成因素分析、可验收行动项和定期 rollback drill，把一次恢复转化为更可靠的发布系统。
