# 42：依赖管理与软件供应链安全——从“加一个库”追到用户安装的每个字节

第 39 章讲过源码怎样变成发布制品，第 41 章讲过怎样主动制造故障验证韧性。本章把视线移到一条更长、也更容易被忽略的链：Codex 的代码并不只由仓库里的第一方源码组成，它还依赖 Rust crate、npm package、Python package、GitHub Action、Bazel module、预构建 V8 静态库、平台签名服务和发布注册表。

因此，“这个 PR 只加了一个依赖”并不等于“只多了几行代码”。一个依赖可能在安装时执行脚本、在编译时运行 build script、引入几十个传递依赖、改变许可证义务，或者让 CI 下载新的可执行文件。软件供应链安全要回答的是：这些字节从哪里来、为什么可信、怎样被固定和校验、以什么权限执行、最后怎样证明用户拿到的是预期制品。

> 源码基线：`4ee41929eaf4`。本章只把当前公开仓库中能直接验证的 manifest、lockfile、`cargo-deny`、pnpm policy、Bazel lock、CI permission、checksum 和 signing workflow 写成“源码事实”。SBOM、组织内部依赖审批和构建基础设施若没有公开证据，只作为通用工程方法介绍，不声称是 OpenAI 内部现状。

---

## 1. 这一章要解决什么问题

读完后，你应该能够：

1. 区分 manifest、resolver 和 lockfile；
2. 区分直接依赖、传递依赖、开发依赖和构建依赖；
3. 解释版本范围与锁定版本为什么不是一回事；
4. 说明 checksum 能证明什么、不能证明什么；
5. 说明签名、来源证明和 SBOM 各自回答什么问题；
6. 识别 build script、proc macro 和 npm lifecycle script 的执行风险；
7. 看懂 Codex 的 `Cargo.toml`、`Cargo.lock` 和 `deny.toml` 分工；
8. 看懂 pnpm 的 frozen lockfile、依赖构建白名单和版本冷却政策；
9. 理解为什么 Cargo 与 Bazel 的锁状态必须同步；
10. 解释 Git 依赖为什么应固定到不可变 revision；
11. 解释 GitHub Action 为什么固定到完整 commit SHA；
12. 用最小 CI permission 和 `persist-credentials: false` 限制爆炸半径；
13. 区分许可证合规、安全公告和实际可利用性；
14. 为正常依赖升级设计可审查、可回滚的流程；
15. 为依赖安全事故设计“先只读取证、后修复轮换”的流程；
16. 避免把“锁住了”“扫描通过了”“签名了”误当成绝对安全。

---

## 2. 先说人话：软件项目像一家餐厅

假设你经营一家餐厅。

菜单写着需要牛肉、面粉和番茄。这像 manifest：声明“我需要什么”，但没有说明今天具体用了哪一批货。

采购系统根据菜单、库存和供应商规则，决定购买：

- A 供应商的 2026-08-01 批次牛肉；
- B 供应商的 5 kg 高筋面粉；
- C 供应商的第 184 批番茄。

这份精确采购单像 lockfile：它记录一次解析后实际选择的版本和来源。

供应商送货时，你还会检查：

- 包装是否完整；
- 批次号是否匹配；
- 运输封签是否被破坏；
- 供应商证照是否有效；
- 食材是否在召回名单中；
- 冷链是否全程合格。

这些分别近似软件供应链中的 checksum、signature、source policy、advisory、provenance 和可信构建过程。

只核对批次号并不代表食材无毒。类似地：

> lockfile 能帮助你重复取得同一份依赖，但“重复取得同一份有问题的代码”仍然有问题。

软件供应链防线必须是组合：

```text
知道需要什么
    ↓
固定实际选择
    ↓
限制允许的来源
    ↓
核对下载字节
    ↓
限制构建和发布权限
    ↓
扫描已知风险与许可证
    ↓
测试真实行为
    ↓
签名并记录来源
```

---

## 3. 一条完整的软件供应链

只看 `Cargo.toml` 会漏掉大半条链。更完整的模型是：

```text
开发者提出依赖变化
  → manifest 声明版本/来源/feature
  → resolver 计算完整依赖图
  → lockfile 固定解析结果
  → registry / Git / release 下载源码或制品
  → checksum / source policy 核对来源与字节
  → build script / proc macro / lifecycle script 运行
  → compiler / linker 生成二进制
  → CI 收集、打包并生成摘要
  → signing / notarization 认证制品
  → GitHub Release / npm / R2 分发
  → installer 下载并核对
  → 用户执行
```

攻击者不一定修改 Codex 第一方 Rust 源码。他可以尝试攻击任意一段：

- 发布一个拼写相近的恶意包；
- 接管一个维护者账号并发布恶意新版本；
- 重定向一个可变 Git tag；
- 在安装脚本中读取 CI token；
- 替换下载服务器上的预构建库；
- 污染共享构建缓存；
- 让发布 job 使用过宽权限；
- 替换未签名的发布压缩包。

所以供应链安全不是某个 scanner 的名字，而是对整条链建立相互独立的证据和限制。

---

## 4. Manifest：项目“想要什么”

Manifest 是依赖声明文件。

在本仓库中常见的例子：

| 生态 | Manifest | 主要内容 |
|---|---|---|
| Rust | `codex-rs/Cargo.toml` 与各 crate 的 `Cargo.toml` | crate、版本范围、feature、Git/path 来源 |
| Node.js | 根 `package.json`、`sdk/typescript/package.json` | dependency、devDependency、script、engine |
| Python | `sdk/python/pyproject.toml` | Python 版本、runtime/dev dependency、构建配置 |
| Bazel | `MODULE.bazel`、`BUILD.bazel` | module、repository、target 和构建输入 |

下面是教学化的 Cargo 声明：

```toml
[dependencies]
serde = { version = "1", features = ["derive"] }
reqwest = { version = "0.12", features = ["cookies"] }
```

它表达：

- 需要 `serde` 1.x 兼容版本；
- 打开 `derive` feature；
- 需要 `reqwest` 0.12 兼容版本；
- 打开 `cookies` feature。

它通常没有单独告诉你：

- resolver 最终选择 `serde` 的哪个补丁版本；
- `serde` 又带来哪些传递依赖；
- 不同平台实际启用哪些条件依赖；
- 下载压缩包的 checksum 是什么。

这些由解析结果和 lockfile 补充。

### 4.1 `dependencies` 不都一样

| 类型 | 什么时候需要 | 风险提示 |
|---|---|---|
| Runtime/direct dependency | 正常构建和运行路径 | 会进入产品行为或最终制品 |
| Dev dependency | 测试、benchmark、开发工具 | 仍可能在 CI 中执行代码、接触凭据 |
| Build dependency | 编译阶段 | 构建时直接执行，权限往往很高 |
| Target dependency | 特定 OS/CPU | 容易因只测一个平台而漏审 |
| Optional dependency | feature 打开时 | 默认测试不一定覆盖 |
| Path dependency | 同一工作区本地路径 | 来源清楚，但仍要维护边界 |

“只是 dev dependency”不是“不需要安全审查”。CI 恰恰经常持有源码读取权、artifact 写入权或发布身份。

---

## 5. Resolver：从版本范围算出完整依赖图

Resolver（解析器）读取所有 manifest，结合生态规则，算出一个能同时满足约束的图。

假设：

```text
应用 → A ^1.2
应用 → B ^3.0
A → C ^2.1
B → C >=2.3,<3
```

Resolver 要为 C 找到同时满足 A、B 的版本。若生态允许同包多版本，也可能保留两份 C。

依赖不是平面清单，而是图：

```text
codex
├── A
│   └── C 2.4.1
└── B
    └── C 2.4.1
```

或者：

```text
codex
├── A
│   └── C 1.9.8
└── B
    └── C 2.4.1
```

这就是为什么发现漏洞后不能只搜索 manifest。问题包可能根本没有被第一方直接声明，而是第 4 层传递依赖。

### 5.1 版本范围不等于实际版本

教学化理解：

| 声明 | 大意 | 实际结果由谁固定 |
|---|---|---|
| `serde = "1"` | 接受兼容的 1.x | `Cargo.lock` |
| `reqwest = "0.12"` | 接受兼容的 0.12.x | `Cargo.lock` |
| `"^3.5.3"` | 接受 SemVer 兼容升级 | `pnpm-lock.yaml` |
| `"=3.0.0"` | 要求精确版本 | manifest + lockfile |
| Git `rev = "..."` | 固定提交 | manifest + lockfile |

请注意：各生态对 `^`、裸版本和 `0.x` 的兼容规则有细节差异。读源码时先确认使用的是 Cargo、npm 还是其他 resolver，不要把一套规则机械套到另一套。

---

## 6. Lockfile：把一次解析结果固定下来

Lockfile 记录 resolver 实际选中的版本、来源和依赖关系。当前仓库至少能看到：

- `codex-rs/Cargo.lock`；
- 根 `pnpm-lock.yaml`；
- `MODULE.bazel.lock`；
- 多个 Python `uv.lock`；
- 独立工具目录自己的 `Cargo.lock`。

它解决的核心问题是：

> 今天和下周、开发机和 CI，能否基于同一份提交解析到同一组依赖？

例如 `Cargo.lock` 的 registry package 条目会记录：

```toml
[[package]]
name = "example"
version = "1.2.3"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "..."
```

真实仓库中的 Git 依赖还会把 revision 放进 `source`，例如 `nucleo` 被固定到一个完整 commit。

### 6.1 Lockfile 的三个重要价值

1. **Repeatability**：重复解析得到相同版本图；
2. **Reviewability**：PR 中能看到新增和升级了哪些传递依赖；
3. **Integrity metadata**：registry package 带有用于核对下载内容的 checksum。

### 6.2 Lockfile 不能保证什么

Lockfile 不自动保证：

- 依赖没有恶意代码；
- 锁定版本没有已知漏洞；
- 许可证适合项目；
- build script 不做危险动作；
- 编译器和 runner 没被攻击；
- 最终发布制品一定由这份锁文件构建；
- 所有平台走了同一依赖分支。

一句话记忆：

> Lockfile 回答“是哪一份”，不回答“这一份是否善良和适用”。

### 6.3 `--frozen` / `--frozen-lockfile`

Codex CI 的 Node 安装使用：

```bash
pnpm install --frozen-lockfile
```

Python SDK CI 使用 `uv sync --frozen`，随后用 `--frozen --no-sync` 运行检查。

“frozen”表示：manifest 与 lockfile 不一致时失败，不允许 CI 悄悄重算并继续。这样遗漏的 lockfile 更新会变成明确失败，而不是让开发机和 CI 各自选择不同版本。

---

## 7. 一个仓库为什么会有多个 Lockfile

Codex 同时使用 Cargo、pnpm、uv 和 Bazel，它们观察的依赖世界不同。

```text
Cargo.toml ──resolver──> Cargo.lock
package.json ──pnpm──> pnpm-lock.yaml
pyproject.toml ──uv──> uv.lock
MODULE.bazel / Cargo metadata ──Bazel──> MODULE.bazel.lock
```

多个锁文件不是简单重复。它们可能固定：

- 不同语言生态；
- 不同构建工具所需的 repository/module 元数据；
- 独立发布单元；
- 独立工具的依赖图。

风险在于只更新其中一份。

本仓库的指导明确要求：Rust dependency 发生变化时，运行 `just bazel-lock-update`，并把 `MODULE.bazel.lock` 一起提交。`justfile` 还提供：

```text
bazel-lock-update → bazel mod deps --lockfile_mode=update
bazel-lock-check  → 检查 lock 是否过期，过期则失败
```

这说明 Cargo 构建成功还不够；Bazel 的依赖视图也必须一致。

### 7.1 常见错误

```text
改 Cargo.toml
→ 本地 cargo build 自动更新 Cargo.lock
→ 忘了 MODULE.bazel.lock
→ Cargo 路径通过
→ Bazel CI 发现漂移
```

正确心智模型不是“Bazel 又要一份麻烦文件”，而是“同一个仓库存在第二个必须被证明一致的依赖消费者”。

---

## 8. Checksum：证明下载字节没有变

Checksum/digest 是对文件字节计算出的固定长度摘要。常见的是 SHA-256。

如果预期摘要是：

```text
abc123...
```

下载后重新计算得到：

```text
abc123...
```

你可以较有把握地说：下载字节与生成预期摘要时的字节相同。

### 8.1 它能证明什么

- 传输后文件是否与预期字节一致；
- 镜像或缓存是否给了另一份内容；
- artifact 是否在摘要生成后被修改。

### 8.2 它不能单独证明什么

- 预期摘要是谁发布的；
- 生成摘要的原始文件是否安全；
- 构建过程是否可信；
- 发布者身份是否可信。

如果攻击者同时替换 artifact 和旁边的 checksum 文件，单纯“下载两者再比较”仍可能通过。因此 checksum 需要和可信发布渠道、签名或来源证明组合。

### 8.3 Codex 中的真实例子：rusty_v8

`.github/actions/setup-rusty-v8/action.yml` 会下载：

- 预构建静态库；
- Rust binding 源文件；
- 包含恰好两行的 SHA-256 清单。

然后根据平台用 `sha256sum -c` 或 `shasum -a 256 -c` 验证。验证失败则构建停止。

这个例子很适合学习，因为预构建原生库不是普通源码依赖：它会直接进入链接结果，因此必须先验证下载字节。

---

## 9. Signature：证明谁为这份字节负责

数字签名通常同时回答：

1. artifact 在签名后有没有变化；
2. 哪个受信身份完成了签名。

Checksum 更像“指纹是否一致”；签名还要验证“是谁盖的章”。

Codex 当前 release workflow 展示了多平台机制：

| 平台 | 仓库中的机制 | 输出/身份 |
|---|---|---|
| Linux | `cosign sign-blob` | 为二进制产生 `.sigstore` bundle，使用 OIDC |
| Windows | Azure Trusted Signing | 通过 OIDC 登录并签名 `.exe` |
| macOS | Azure Key Vault + 签名/公证脚本 | 签名、验证、notarization |

签名仍不是“代码绝对安全”的证明。被可信身份签名的 bug 仍然是 bug。它主要建立 artifact identity、完整性和责任链。

---

## 10. Provenance 与 SBOM：两个不同问题

### 10.1 Provenance

Provenance（来源证明）回答：

- 由哪份源码 commit 构建；
- 在哪个 workflow/job 中构建；
- 使用了哪些输入和 builder；
- 产物 digest 是什么。

它把“源码”和“最终 artifact”连接起来。

### 10.2 SBOM

SBOM（Software Bill of Materials，软件物料清单）回答：

- 这个产品包含哪些组件；
- 各自版本和来源是什么；
- 可能有哪些许可证和已知公告。

它像包装上的配料表。

### 10.3 二者为什么不能互换

```text
SBOM：里面有什么？
Provenance：它怎样被做出来？
Signature：谁为这份 artifact 签名？
Checksum：这份字节有没有变化？
```

当前公开仓库能直接确认 checksum、签名和发布 workflow；本章没有找到足够证据断言每个 Codex 发布都公开附带某种特定格式 SBOM 或标准 provenance attestation。因此这里将 SBOM/provenance 作为应理解的通用机制，而不是仓库现状声明。

---

## 11. Cargo 依赖：声明、锁定与集中管理

`codex-rs/Cargo.toml` 使用 `[workspace.dependencies]` 集中声明大量内部和外部依赖。

这样做的学习价值是：

- 多个 crate 可以继承同一版本策略；
- 升级入口更集中；
- review 更容易看到全 workspace 影响；
- 减少同一个库被各 crate 随意声明不同版本。

例如当前 workspace 中能看到：

```toml
reqwest = { version = "0.12", features = ["cookies"] }
rmcp = { version = "=3.0.0", default-features = false }
```

这里有三个容易漏看的信息：

- `version`：可接受版本约束；
- `features`：额外启用的代码能力；
- `default-features = false`：关闭依赖默认能力，再明确选择需要的部分。

安全 review 不应只看版本号。feature 会改变编译进来的代码、原生依赖、网络能力和攻击面。

### 11.1 `*` 为什么值得警惕

当前 workspace 中也存在 `openssl-sys = "*"`。星号表示非常宽的版本要求。

但不能只看到 `*` 就断言构建每次随机变化：提交的 `Cargo.lock` 仍固定实际解析版本。正确结论是：

- manifest 接受范围宽；
- lockfile 固定当前结果；
- 更新 lock 时 resolver 有更大选择空间；
- review lock diff 更重要。

`codex-rs/deny.toml` 当前把 wildcard lint 配为 `allow`，所以这不是被 policy 禁止的写法。文档必须区分“我个人觉得值得谨慎”和“仓库明确禁止”。

---

## 12. Git 依赖与 Fork：为什么固定 `rev`

Registry version 通常对应发布过的不可变包；Git 依赖直接从仓库取代码。

不安全或不稳定的写法可能是：

```toml
example = { git = "https://example.com/repo", branch = "main" }
```

`main` 会移动。相同 manifest 在不同时间可能指向不同源码。

更明确的写法是：

```toml
example = { git = "https://example.com/repo", rev = "完整提交哈希" }
```

当前 Codex 的 `nucleo`、`runfiles` 以及 `[patch.crates-io]` 下的若干 fork 都固定完整 `rev`。

`codex-rs/deny.toml` 进一步规定：

```toml
[sources]
unknown-registry = "deny"
unknown-git = "deny"
required-git-spec = "rev"
```

并列出允许的 registry 和 Git URL。

这形成两道不同检查：

1. 只允许已知来源；
2. Git 来源必须固定到 revision。

### 12.1 Fork 的额外责任

`[patch.crates-io]` 可以让某个 registry crate 改用 fork。它通常用于尚未上游发布的修复或项目定制，但会带来维护责任：

- 为什么必须 fork；
- 与 upstream 差异是什么；
- 如何跟进安全更新；
- 何时可以回归 upstream；
- 哪些 package 被全局替换。

Fork 不天然更危险或更安全；关键是来源、固定、差异和退出条件是否可审查。

---

## 13. Build Script：依赖不仅“被编译”，还可能先执行

很多人把依赖理解为“一堆等待 compiler 读取的源码”。实际构建中，依赖可能先执行代码。

### 13.1 Rust 的执行面

- `build.rs`：编译期间运行；
- procedural macro：编译其他源码时运行；
- native build helper：调用 C/C++ compiler、linker 或下载工具；
- test helper：CI 测试阶段运行。

### 13.2 npm 的执行面

- `preinstall`；
- `install`；
- `postinstall`；
- `prepare`；
- package scripts 调用的任意工具。

如果 CI job 同时具有网络、缓存、token 或 artifact 写权限，恶意构建脚本可能读取或污染这些资源。

所以“扫描源码后再构建”和“先安装所有依赖再看看”风险完全不同。遇到真实供应链事故时，第一遍应尽量只读查看 manifest、lockfile、CI workflow 和脚本，不要立刻在高权限环境执行不可信安装。

---

## 14. Codex 的 pnpm 构建政策

根 `pnpm-workspace.yaml` 不只列 workspace，还写了供应链限制：

```yaml
minimumReleaseAge: 10080
blockExoticSubdeps: true
strictDepBuilds: true
trustPolicy: no-downgrade
allowBuilds:
  "@modelcontextprotocol/conformance": true
```

逐项理解：

### 14.1 `minimumReleaseAge: 10080`

10080 分钟等于 7 天。新发布版本需要经过冷却期，降低“恶意版本刚发布就被自动升级吃进来”的概率。

冷却期不是安全证明，只是增加发现窗口：社区、扫描器和维护者有时间暴露异常。

### 14.2 `blockExoticSubdeps: true`

限制传递依赖使用非常规来源。它降低依赖图偷偷跳到难审查 URL/Git 来源的机会。

### 14.3 `strictDepBuilds: true`

依赖需要运行构建脚本时采用严格政策，而不是默许任意新 package 执行安装期代码。

### 14.4 `allowBuilds`

当前白名单允许官方 MCP conformance CLI 构建。白名单的意义是把“谁可以在安装时执行”变成显式、可 review 的变化。

### 14.5 `ignoredBuiltDependencies`

当前还列出 `esbuild`。阅读这类配置时不要把 `ignored` 理解成“信任并执行”；应结合 pnpm 对该字段的具体语义确认它是忽略 build、忽略警告，还是其他行为。最安全的源码阅读习惯是：字段名只给线索，最终行为需用工具版本文档和 CI 结果验证。

---

## 15. `cargo-deny`：把政策变成 CI Gate

`.github/workflows/cargo-deny.yml` 调用固定 SHA 的 `cargo-deny-action`，对 `codex-rs/Cargo.toml` 执行检查。它由 blocking CI 汇总，因此不是可有可无的本地提示。

当前 `codex-rs/deny.toml` 主要覆盖四类问题。

### 15.1 Advisories

检查 RustSec 等公告数据库中的已知问题。

当前配置存在若干 `ignore`，但每条不是只有编号，还写有：

- 依赖路径；
- 当前为什么不能移除；
- 是否触发受影响 API/feature；
- 将来由什么条件解除例外。

这比无理由忽略强得多，因为例外变成可追踪债务。

### 15.2 Licenses

`allow` 列出接受的 SPDX license identifier，例如 Apache-2.0、MIT、BSD-3-Clause 等，并设置检测置信阈值。

它回答的是“许可证政策是否允许”，不是“依赖是否安全”。

### 15.3 Bans

当前 policy：

- 多版本依赖产生 warning；
- 对部分 crate 设置禁止或 wrapper 例外；
- 例如第一方新增 `async-trait` 受到约束；
- `reqwest` 的直接拥有者被逐步收敛到 `codex-http-client`。

这里的 ban 不一定因为恶意或漏洞，也可能用于架构边界、编译性能和迁移棘轮。

### 15.4 Sources

限制允许的 registry/Git URL，并要求 Git `rev` 固定。

### 15.5 扫描通过不等于没有风险

`cargo-deny` 只能检查它知道和配置覆盖的东西：

- 新的 0-day 可能尚无 advisory；
- 恶意逻辑可能没有 CVE/RUSTSEC 编号；
- 合法许可证不代表符合所有分发场景；
- 公告命中也不等于实际路径可利用。

它是 gate，不是神谕。

---

## 16. License：开源不等于“随便用”

依赖许可证规定你如何使用、修改和分发代码。

审查至少要问：

- 许可证是否被项目政策允许；
- 是否要求保留 copyright/license notice；
- 是否对修改或再分发有额外义务；
- 是否存在多许可证表达式；
- 包元数据与实际 LICENSE 文件是否一致；
- 最终二进制/安装包是否需要附带 notices。

SPDX identifier 是标准化写法，例如：

```text
Apache-2.0
MIT
BSD-3-Clause
Apache-2.0 WITH LLVM-exception
```

许可证 scanner 的价值是自动发现偏离 policy 的变化，但复杂情况仍需要人工/法律判断。不要从本章推导具体法律结论。

---

## 17. Advisory、Severity 与 Reachability

某个依赖出现在安全公告中，需要分三层判断。

### 17.1 Presence

受影响版本是否真的出现在 lockfile？

### 17.2 Path

哪条依赖路径把它引入？

```text
codex-tui → syntect → 某传递依赖
```

### 17.3 Exposure / Reachability

受影响代码是否在当前 feature、target 和输入条件下可达？

例如：

- 只在未启用 feature 中；
- 只用于测试；
- 只解析受信构建时 XML；
- 受影响 API 从未调用；
- 只在 Windows，而事故环境是 Linux。

这会影响处置优先级，但不能用模糊的“应该不可达”关闭问题。必须给出源码、feature graph、调用点或测试证据。

### 17.4 Evidence status 与 Severity 分开

推荐记录两条独立维度：

```text
证据状态：confirmed / needs verification / ruled out
风险等级：critical / high / medium / low
```

“高危但尚待确认”与“低危且已经确认”是两个不同事实。若混成一个标签，团队容易把不确定性误认为低风险。

---

## 18. GitHub Actions 本身也是依赖

下面这行会在 CI runner 上执行第三方维护的代码：

```yaml
uses: actions/checkout@...
```

所以 GitHub Action 与 Rust crate 一样属于供应链依赖，而且通常权限更敏感。

### 18.1 为什么固定完整 SHA

Codex workflow 中可见大量写法：

```yaml
uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
```

完整 SHA 固定实际执行的 Action 源码；注释保留人类可读版本。

若只写：

```yaml
uses: actions/checkout@v6
```

tag 可能移动，相同 workflow 文本将来可能执行不同代码。

固定 SHA 不保证该 commit 没问题，但消除了 tag 被重新指向后静默改变执行内容的一个风险。

### 18.2 更新 SHA 怎么做

不能只改注释或只改 SHA。Review 应核对：

- 新 SHA 是否确属目标官方仓库；
- 从旧 SHA 到新 SHA 的 release/diff；
- 是否改变输入、权限或 runtime；
- 是否需要修改 workflow 参数；
- 更新是否由 Dependabot 等自动化提出但仍经过 review。

当前 `.github/dependabot.yaml` 每周检查 GitHub Actions、Cargo、Docker、Rust toolchain 等生态，并设 7 天 cooldown。

---

## 19. 最小权限：即使依赖被攻破，也限制它能做什么

供应链防线不能假设所有依赖永远可信。还要问：最坏情况下它拿到什么权限？

### 19.1 Workflow `permissions`

GitHub Actions job 可以显式声明：

```yaml
permissions:
  contents: read
```

发布签名 job 才按需要增加：

```yaml
permissions:
  id-token: write
  contents: read
```

这符合 least privilege：普通测试不应自动拥有发布或写仓库权限。

### 19.2 `persist-credentials: false`

Codex 多处 checkout 使用：

```yaml
with:
  persist-credentials: false
```

这样 checkout 不把 GitHub token 长期写进本地 Git credential 配置，减少后续脚本意外读取或使用它的机会。

### 19.3 Secrets 与不可信 PR

Review workflow 时要检查：

- fork PR 是否能进入有 secret 的 job；
- `pull_request_target` 是否执行了 PR 中可修改的代码；
- artifact 从哪个 job/commit 产生；
- cache key 是否允许不可信分支污染高权限构建；
- shell 参数是否直接拼接不可信输入。

最小权限不阻止恶意代码运行，但能显著缩小 blast radius。

---

## 20. OIDC：用短期身份替代长期发布 Token

Codex 的 npm 发布 job 明确使用 OIDC trusted publishing：

```yaml
permissions:
  id-token: write
  contents: read
```

并注明不需要 `NODE_AUTH_TOKEN`。

简化流程：

```text
受信 GitHub workflow
  → 请求短期 OIDC 身份令牌
  → npm 验证仓库/workflow/环境声明
  → 允许此次发布
```

好处是减少长期 npm token：

- 不必把永久凭据保存为 secret；
- 泄漏窗口更短；
- 身份可绑定到特定 workflow 和仓库条件；
- 审计能看到是谁、从哪里申请。

但 OIDC 仍要求保护 workflow 本身。如果攻击者能修改受信发布 workflow 或绕过 environment gate，他仍可能借合法身份发布。因此 CODEOWNERS、branch protection、environment 和 SHA pinning 仍重要。

---

## 21. CODEOWNERS：把敏感路径交给明确 Reviewer

`.github/CODEOWNERS` 当前为 release workflow 和签名 setup action 指定团队 owner。

它的作用不是自动证明 review 高质量，而是让敏感路径变化被路由到知道其风险的人。

适合重点保护的路径通常包括：

- release workflow；
- signing action；
- installer；
- dependency policy；
- lockfile 生成规则；
- registry/publisher 配置；
- scripts that download executable artifacts。

CODEOWNERS 必须和仓库保护规则组合；单独存在一个文本文件并不保证相应 review 一定成为合并门禁。

---

## 22. Reproducible、Repeatable 与 Hermetic 不要混用

### 22.1 Repeatable build

同一组锁定输入能再次完成构建，依赖版本基本相同。

### 22.2 Reproducible build

独立构建者对同一源码和输入，能得到逐字节相同 artifact。

这更严格，时间戳、路径、编译器版本、压缩顺序都可能破坏逐字节一致。

### 22.3 Hermetic build

构建只能读取明确声明的输入，不偷偷依赖宿主机器全局文件、当前时间、任意网络内容等。

三者有关但不等价：

```text
锁文件 → 更容易 repeat
显式工具链/输入 → 更接近 hermetic
消除非确定性 → 更接近 reproducible
```

不要看到 `Cargo.lock` 就声称“构建完全可复现”。那需要更强的跨环境字节级证据。

---

## 23. Cache：性能优化也可能成为信任通道

CI cache 能显著加速构建，但被污染的 cache 可能把恶意文件带入后续 job。

检查点包括：

- cache key 是否包含 lockfile/toolchain hash；
- 不可信分支能否写入受信分支会恢复的 key；
- restore key 是否过宽；
- cache 中保存的是可重新验证的依赖，还是最终可执行制品；
- 恢复后是否做 checksum 或 clean build 验证；
- secret 是否意外写入 cache。

当前 `rust-ci-full.yml` 中能看到对 `Cargo.lock` 和 `rust-toolchain.toml` 计算 SHA-256 用于构建状态。它说明 lock/toolchain 是 cache identity 的重要输入，但 review 仍需沿具体 cache action 检查读写边界。

---

## 24. 依赖混淆、拼写抢注和维护者接管

### 24.1 Dependency confusion

内部包名与公共 registry 同名，resolver 错误选择了公共恶意版本。

防线：

- 明确 registry/source；
- 私有命名空间；
- source allowlist；
- lockfile review；
- 禁止意外 exotic source。

### 24.2 Typosquatting

恶意包使用相近拼写，例如少一个字母或交换字符。

防线：

- 对新增包名人工核对官方主页和维护者；
- 不只看下载量；
- 检查 registry、repository link 和发布历史；
- 先读源码/脚本，再安装。

### 24.3 Maintainer/account compromise

合法包账号被接管并发布恶意版本。

防线：

- lockfile 阻止无 review 自动漂移；
- minimum release age 增加观察窗口；
- 自动扫描与社区告警；
- 发布者 MFA/OIDC；
- 构建脚本最小权限；
- 快速撤回/回滚流程。

没有任何单一道防线能完全解决这类风险。

---

## 25. 正常新增依赖：一份可执行 Review 流程

下面以“为某个 crate 增加 parser 库”为例。

### 第 1 步：证明真的需要新依赖

问：

- 标准库或现有 workspace dependency 能否完成；
- 新依赖是否只为一个很小 helper；
- 自己实现是否反而更难维护/更不安全；
- 它是否把代码推向错误 crate 边界。

目标不是“依赖越少越好”，而是每个依赖的收益覆盖长期成本。

### 第 2 步：确认身份和维护状态

查看：

- 官方 registry 页面；
- source repository；
- 最近 release；
- maintainer 数量和交接历史；
- 安全政策；
- 是否有可疑同名包。

### 第 3 步：阅读执行面

重点查看：

- `build.rs`；
- proc macro；
- npm lifecycle scripts；
- 下载预构建 binary 的逻辑；
- native dependency；
- 默认 feature。

### 第 4 步：选择最小 feature

关闭不需要的 default feature，明确打开实际使用能力。减少代码量、依赖图和平台差异。

### 第 5 步：更新所有锁状态

根据生态更新：

- `Cargo.lock`；
- `pnpm-lock.yaml`；
- `uv.lock`；
- `MODULE.bazel.lock`。

不要手改 lockfile 内容来“看起来对了”；使用对应 resolver/generator。

### 第 6 步：Review lock diff

问：

- 新增了几个 package；
- 是否出现两个大版本；
- 是否增加 Git/native/build dependency；
- source/checksum 是否符合预期；
- 是否出现意外 package rename；
- 哪些 target 才启用。

### 第 7 步：运行 policy 和目标测试

包括：

- dependency policy；
- 受影响 crate 测试；
- 跨平台/target-specific 验证；
- Bazel lock 检查；
- 格式和 lint。

### 第 8 步：记录回退策略

明确：

- 出问题时能否降级；
- lockfile 如何回到旧图；
- 数据格式是否已绑定新依赖；
- 对外 API 是否泄漏依赖类型。

---

## 26. 依赖升级不是“把版本号改大”

升级 PR 应至少形成以下证据链：

```text
为什么升级
  → 版本与来源变化
  → release notes / security fix
  → lockfile 传递变化
  → feature/API/build script 变化
  → 目标测试与平台验证
  → 发布/回滚影响
```

### 26.1 小版本也可能风险很大

SemVer 说明 API 兼容意图，不保证：

- 性能完全不变；
- 行为完全不变；
- MSRV/toolchain 不变；
- 新增传递依赖不变；
- build script 不变；
- 没有发布事故。

### 26.2 大型升级拆分

可按以下阶段拆：

1. 无行为变化的依赖/lock 更新；
2. 编译 API 迁移；
3. 启用新 feature；
4. 删除兼容层；
5. 性能或行为优化。

每一阶段都能独立 review 和回退，比把几千行生成 diff、API 重写和行为变化塞进一个 PR 更安全。

---

## 27. 供应链事故：为什么第一遍必须只读

假设收到告警：“`package-x` 2.4.1 可能在安装时窃取凭据。”

最危险的本能反应之一是：

```text
先在本地/CI 安装它，跑起来看看
```

如果告警是真的，这正好执行了恶意代码。

[OpenAI 官方 dependency incident audit 指南](https://learn.chatgpt.com/use-cases/dependency-incident-audits)强调：第一遍先只读检查暴露面，不安装、不构建、不执行可疑代码，直到理解风险。

### 27.1 第一遍只读检查什么

- manifest；
- lockfile；
- 依赖路径；
- CI workflow 和 permission；
- install/build/postinstall script；
- vendored artifact、container、generated bundle；
- cache；
- token/secret 暴露面；
- 发布记录和 artifact digest。

可使用不会执行依赖的文本搜索和文件读取。即便某个包管理器命令通常只显示图，也要确认它是否会触发解析、下载或脚本。

### 27.2 不要一边取证一边破坏证据

立刻升级、删除 lockfile 或清 cache 可能让你失去：

- 实际安装的精确版本；
- 原始依赖路径；
- cache 中的可疑 artifact；

- 受影响 workflow/log 时间线。

先保存必要证据，再做修复和轮换。

---

## 28. 事故排查模板：事实、风险、动作分三栏

建议维护这样的表：

| 对象 | 证据状态 | 风险等级 | 证据 | 下一步 |
|---|---|---:|---|---|
| lockfile 含 x 2.4.1 | Confirmed | High | `pnpm-lock.yaml` 条目 | 找直接路径 |
| postinstall 在 CI 运行 | Needs verification | Critical if true | policy 似乎阻止 | 检查 CI log/配置 |
| release token 可被读取 | Ruled out | — | OIDC job 无长期 token | 保留证据 |
| developer laptop 曾安装 | Needs verification | High | 尚无时间线 | 查 shell/package log |

这样不会把“尚未确认”写成“没有风险”，也不会把 severity 当成事实存在性的证据。

### 28.1 Confirmed

已有直接证据，例如 lockfile 明确存在受影响版本。

### 28.2 Needs verification

合理怀疑，但缺少决定性证据，例如不确定脚本是否在某个 runner 执行。

### 28.3 Ruled out

有足够证据排除某条路径，例如受影响 feature 未启用且构建图证明不包含相应代码。

“Ruled out”需要证据，不是“我觉得应该没事”。

---

## 29. 一条完整的依赖事故响应流程

### 29.1 Scope

确定：

- 包名、版本、hash、发布时间；
- 受影响生态和平台；
- 恶意行为或漏洞触发条件；
- 首次/最后可能暴露时间。

### 29.2 Presence

搜索所有 manifest、lockfile、vendored tree、container 和 generated artifact。

不要只搜一个根 lockfile；本仓库存在多个独立 lock universe。

### 29.3 Path

找到直接或传递引入者，以及 feature/target 条件。

### 29.4 Execution

确认可疑代码是否真正执行：

- 开发机 install；
- PR CI；
- release CI；
- build script；
- test；
- runtime path。

### 29.5 Privilege and data exposure

执行时能访问：

- 哪些 secret；
- Git credential；
- signing identity；
- npm/R2/GitHub 发布权；
- source code；
- artifact/cache；
- 网络出口。

### 29.6 Containment

按证据选择：

- 暂停相关 CI/release；
- 禁用 package/version；
- 隔离 runner/cache；
- 阻断凭据；
- 固定安全版本或移除依赖。

### 29.7 Credential rotation

若凭据可能暴露，不能只升级依赖。需要撤销/轮换，并确认旧凭据失效。

OIDC 短期令牌可能减少长期泄漏面，但仍要检查当时签发的权限和被执行动作。

### 29.8 Artifact assessment

已经构建或发布的 artifact 是否包含可疑代码？

- 比较 digest；
- 检查构建时间；
- 检查来源 commit；
- 必要时撤回版本；
- 重新构建、签名和发布安全版本。

### 29.9 Recovery and prevention

最后才进入常规修复：

- 安全升级/替换；
- 增加 source/build policy；
- 缩小 permission；
- 添加 detection；
- 记录 incident timeline；
- 补充 runbook 和回归 gate。

---

## 30. 案例：一个恶意 `postinstall` 会怎样穿过 CI

假设某传递 npm package 发布恶意版本：

```json
{
  "scripts": {
    "postinstall": "node steal-secrets.js"
  }
}
```

一条薄弱链可能是：

```text
manifest 使用宽版本范围
→ CI 不用 frozen lock
→ resolver 自动选择刚发布恶意版本
→ pnpm install 执行 postinstall
→ job 有长期 npm token
→ 脚本向外发送 token
→ 攻击者发布伪造包
```

Codex 当前公开配置中的多道防线分别切断不同环节：

| 防线 | 它切断什么 |
|---|---|
| `pnpm-lock.yaml` | 不让已提交解析结果无 review 漂移 |
| `--frozen-lockfile` | manifest/lock 不一致时失败 |
| 7 天 minimum release age | 降低新恶意版本立即进入更新的概率 |
| `strictDepBuilds` + allow list | 不默许新依赖执行构建脚本 |
| Action SHA pin | workflow 依赖不随 tag 静默移动 |
| 最小 permissions | 即使执行也减少可用权限 |
| npm OIDC | 不保存长期 `NODE_AUTH_TOKEN` |
| Dependabot + review | 把升级变成显式 PR |

注意它们没有任何一条单独宣称“绝不可能被攻击”。Defense in depth 的意义就是一层失效后还有其他层。

---

## 31. 案例：下载的预构建静态库被替换

假设 CI 需要下载 `librusty_v8_...a.gz`。

薄弱链：

```text
从 URL 下载
→ 不校验
→ 解压并链接
→ 打包发布
→ 用户运行被替换的机器码
```

当前 setup action 增加：

```text
固定 release tag/命名
→ 下载 archive + binding + checksum manifest
→ 检查 manifest 恰有两个摘要
→ 对下载文件执行 SHA-256 verification
→ 成功后才写入构建环境路径
```

进一步的通用问题还包括：

- checksum manifest 的发布通道是否可信；
- release tag 能否被重写；
- artifact 是否有签名/attestation；
- builder 和上传 job 是否受保护；
- 下载 action 是否固定版本。

不要因为已经有 checksum 就停止沿信任链追问。

---

## 32. 常见误区

### 误区 1：“有 lockfile，所以供应链安全了”

Lockfile 固定版本，不评价代码质量和恶意性。

### 误区 2：“只是传递依赖，不是我们的责任”

只要进入构建或运行图，就会成为产品风险。

### 误区 3：“dev dependency 不会发给用户”

它可能在高权限 CI 中执行并污染 artifact。

### 误区 4：“Checksum 对上，就证明来自官方”

若 artifact 与 checksum 一起被替换，单独比较仍可能通过。

### 误区 5：“签名制品一定安全”

签名证明身份和完整性，不证明没有漏洞。

### 误区 6：“Scanner 没报就是没漏洞”

数据库存在延迟，恶意代码也不一定有公告编号。

### 误区 7：“公告严重度高就一定可利用”

还要看版本、依赖路径、feature、target 和调用可达性。

### 误区 8：“先运行一下可疑包，最快确认”

这可能主动触发攻击。供应链事故第一遍应只读取证。

### 误区 9：“Action 写 `@v6` 已经固定主版本”

tag 可移动；完整 commit SHA 才固定源码对象。

### 误区 10：“自动依赖升级可以自动合并”

Bot 负责发现和提出变化，不替代 lock diff、release notes、测试和权限 review。

---

## 33. 源码阅读路线

按以下顺序打开文件，会比随机搜索 `dependency` 更容易形成全景。

### 路线 A：Rust 依赖政策

1. `codex-rs/Cargo.toml`
2. `codex-rs/Cargo.lock`
3. `codex-rs/deny.toml`
4. `.github/workflows/cargo-deny.yml`
5. `.github/workflows/blocking-ci.yml`

观察问题：

- workspace dependency 从哪里集中声明；
- Git dependency 是否都有 `rev`；
- registry entry 怎样记录 checksum；
- advisory 例外是否有路径和解除条件；
- policy failure 是否进入 blocking gate。

### 路线 B：Node 依赖安装

1. 根 `package.json`
2. `pnpm-workspace.yaml`
3. `pnpm-lock.yaml`
4. `sdk/typescript/package.json`
5. `.github/workflows/repo-checks.yml`

观察问题：

- package manager 版本怎样固定；
- workspace 包有哪些；
- minimum release age 是多少；
- 哪些 dependency 获准 build；
- CI 是否 frozen install。

### 路线 C：构建与发布制品

1. `.github/actions/setup-rusty-v8/action.yml`
2. `.github/actions/linux-code-sign/action.yml`
3. `.github/actions/windows-code-sign/action.yml`
4. `.github/workflows/rust-release.yml`
5. `scripts/install/install.sh`

观察问题：

- 下载后何时验证 checksum；
- 哪些 job 获得 `id-token: write`；
- Linux/Windows/macOS 分别怎样签名；
- release archive 怎样生成 SHA256SUMS；
- npm 为什么不需要长期 token。

### 路线 D：自动升级与所有权

1. `.github/dependabot.yaml`
2. `.github/CODEOWNERS`
3. `AGENTS.md`
4. `justfile`

观察问题：

- 哪些生态每周扫描；
- cooldown 多久；
- release/signing 变化由谁 review；
- dependency 改动还需生成哪个 Bazel lock。

---

## 34. 实战练习

### 练习 1：解释一个 Lock Diff

任取一次只升级一个 direct dependency 的历史 diff，回答：

1. manifest 范围怎样变化；
2. lock 中实际版本怎样变化；
3. 新增/删除哪些传递包；
4. source/checksum 是否变化；
5. 是否出现 build/native dependency；
6. Bazel lock 是否同步。

### 练习 2：追踪一个 Advisory

从 `deny.toml` 的一条 ignore 开始：

1. 找 advisory 对象；
2. 在 lockfile 找版本；
3. 画出依赖路径；
4. 找到使用它的 Codex crate；
5. 解释 reason 中的解除条件；
6. 标出哪些是事实、哪些仍需验证。

### 练习 3：Review 一个 Action Upgrade

对一行 `uses:` 更新：

1. 核对旧/new SHA；
2. 核对版本注释；
3. 查看 release notes；
4. 比较 inputs 和 runtime；
5. 检查 job permissions；
6. 说明回滚方式。

### 练习 4：设计供应链事故表

假设某 npm 包 48 小时前被接管，写出：

- presence 检查；
- execution 时间线；
- secrets/permissions 清单；
- artifact/cache 处置；
- credential rotation；
- safe version 恢复；
- 对用户的影响判断。

每条必须标注 confirmed、needs verification 或 ruled out。

### 练习 5：区分四种证据

为同一个 Linux binary 分别写出：

- checksum 证据；
- signature 证据；
- provenance 证据；
- SBOM 证据。

如果一句话能被另外一种证据完全替代，说明你还没有分清它们的职责。

---

## 35. 理解检查

### 问题 1

`Cargo.toml` 已经写了 `reqwest = "0.12"`，为什么还需要 `Cargo.lock`？

<details>
<summary>参考答案</summary>

Manifest 只声明可接受范围和 feature；resolver 可能在兼容范围内选择不同补丁版本和传递依赖。Lockfile 固定本次实际版本图、来源和 registry checksum，使开发机与 CI 更容易重复同一解析结果并 review 传递变化。

</details>

### 问题 2

为什么 lockfile 中有 checksum 仍不能证明 dependency 安全？

<details>
<summary>参考答案</summary>

Checksum 只说明下载字节与预期摘要一致。若原始包本来就有漏洞/恶意逻辑，或者摘要来源也被替换，校验仍可能成功。还需要可信来源、公告/代码审查、构建隔离、签名和测试等防线。

</details>

### 问题 3

为什么 `uses: action@完整SHA # v1.2.3` 同时保留 SHA 和注释？

<details>
<summary>参考答案</summary>

完整 SHA 固定实际执行代码，避免 tag 移动；版本注释让 reviewer 快速知道人类可读 release。两者更新时都要核对，注释本身没有固定作用。

</details>

### 问题 4

为什么一个安全公告不能只按 CVSS/severity 排队？

<details>
<summary>参考答案</summary>

严重度描述潜在后果，还要确认受影响版本是否存在、通过哪条依赖路径引入、feature/target 是否启用、受影响 API 是否可达，以及执行环境有什么权限。同时应把证据状态与严重度分开记录。

</details>

### 问题 5

依赖事故发生时，为什么不要立刻删除 lockfile 并重新安装？

<details>
<summary>参考答案</summary>

重新安装可能执行恶意脚本；删除/重算还可能破坏版本、路径和 checksum 等关键证据。应先只读保存暴露面和时间线，再隔离、轮换、升级和恢复。

</details>

### 问题 6

OIDC trusted publishing 消除了所有 npm 发布风险吗？

<details>
<summary>参考答案</summary>

没有。它减少长期 token 和泄漏窗口，但受信 workflow、仓库权限、environment 和发布条件仍必须保护。若攻击者能修改受信 workflow，可能借合法 OIDC 身份执行恶意发布。

</details>

---

## 36. 本章词汇表

| 英文/代码词 | 字面翻译 | 在本章中的具体意思 |
|---|---|---|
| Dependency | 依赖 | 项目构建、测试或运行所需的外部/内部组件 |
| Direct dependency | 直接依赖 | 第一方 manifest 直接声明的包 |
| Transitive dependency | 传递依赖 | 被直接依赖继续引入的下游包 |
| Manifest | 清单/声明文件 | 声明依赖范围、feature、script 和项目元数据的文件 |
| Resolver | 解析器 | 根据所有约束计算完整版本图的工具逻辑 |
| Lockfile | 锁文件 | 固定一次解析得到的版本、来源、checksum 和依赖关系 |
| Version constraint | 版本约束 | Manifest 允许 resolver 选择的版本范围 |
| Semantic Versioning / SemVer | 语义化版本 | 用 major/minor/patch 表达兼容意图的版本约定 |
| Registry | 注册表 | 发布、索引和下载 package/crate 的服务 |
| Crate | 箱子 | Rust 的编译/发布包单位 |
| Package | 软件包 | npm/Python 等生态中的发布依赖单位 |
| Workspace | 工作区 | 由同一顶层配置协调的多个项目/package/crate |
| Feature | 功能特性 | Cargo 中选择性启用依赖代码和能力的开关 |
| Default feature | 默认特性 | 未显式关闭时由 dependency 自动启用的 feature 集合 |
| Build dependency | 构建依赖 | 编译阶段需要并可能执行的依赖 |
| Dev dependency | 开发依赖 | 主要用于测试、benchmark 或开发工具的依赖 |
| Target-specific | 特定目标 | 只在某个 OS、CPU 或编译 target 生效 |
| Build script | 构建脚本 | 编译/安装期间运行的程序，如 Rust `build.rs` |
| Lifecycle script | 生命周期脚本 | npm 安装、准备、发布阶段自动运行的 script |
| Procedural macro | 过程宏 | Rust 编译期执行并生成/变换 token 的代码 |
| Native dependency | 原生依赖 | C/C++ library、系统库或预编译机器码等组件 |
| Vendoring | 纳入源码树 | 把外部依赖副本放入仓库/受控存储而非实时下载 |
| Checksum | 校验和 | 对字节计算的摘要，用来核对内容是否一致 |
| Digest | 摘要 | Hash 输出；供应链语境中常指 SHA-256 等内容标识 |
| SHA-256 | 安全哈希算法 256 位 | 常用于 artifact/lock/cache 内容校验的摘要算法 |
| Signature | 数字签名 | 认证签名身份并检测签名后字节变化的密码学证据 |
| Code signing | 代码签名 | 对可执行文件或安装包进行平台/身份签名 |
| Notarization | 公证 | 平台对已签名软件进一步检查、登记的流程 |
| Provenance | 来源证明 | 连接源码、builder、workflow、输入和 artifact 的证据 |
| SBOM | 软件物料清单 | 列出最终软件包含的组件、版本和来源 |
| Artifact | 制品 | 构建生成并等待分发的 binary、archive、wheel、tarball |
| Immutable revision | 不可变版本点 | 完整 Git commit 等不会随分支移动的源码标识 |
| `rev` | revision 缩写 | Cargo Git dependency 中指定确切提交的字段 |
| Patch | 补丁/替换 | Cargo `[patch.crates-io]` 对 registry package 来源的覆盖 |
| Fork | 分叉仓库 | 从上游复制并独立维护的代码仓库 |
| Source allowlist | 来源白名单 | 只允许预先审查的 registry/Git URL |
| Advisory | 安全公告 | 描述受影响版本、风险和修复信息的记录 |
| Vulnerability | 漏洞 | 可导致安全边界被破坏的软件缺陷 |
| Reachability | 可达性 | 受影响代码能否从实际构建和输入路径被执行 |
| Exposure | 暴露面 | 受影响代码实际执行时可接触的数据、身份和权限 |
| License | 许可证 | 规定代码使用、修改、分发义务的法律文本 |
| SPDX | 软件包数据交换标准 | 标准化 license identifier 和物料信息的体系 |
| `cargo-deny` | Cargo 拒绝检查工具 | 执行 advisory、license、ban 和 source policy 的工具 |
| Ban | 禁用规则 | 阻止某 crate 或限定其只能由特定 wrapper 使用的 policy |
| Wrapper | 包装者 | 在 `cargo-deny` 中获准直接依赖被限制 crate 的包 |
| Ratchet | 棘轮 | 允许暂存旧债，但阻止新增同类问题并逐步收紧 |
| Frozen lockfile | 冻结锁文件 | Manifest/lock 不一致时失败、禁止现场重新解析 |
| Minimum release age | 最短发布时间 | 新 package release 进入解析前必须等待的冷却期 |
| Cooldown | 冷却期 | 自动更新前等待一段时间以增加观察窗口 |
| Exotic dependency | 非常规依赖 | 非标准 registry、URL、Git 等更难统一审查的来源 |
| Dependency confusion | 依赖混淆 | 内外部同名包导致 resolver 选中攻击者版本 |
| Typosquatting | 拼写抢注 | 用与正规包相近的名字诱导错误安装 |
| Compromise | 失陷/被攻破 | 账号、包、runner 或服务被攻击者控制 |
| Supply chain | 软件供应链 | 从依赖和构建输入到签名、发布、安装的完整链路 |
| Defense in depth | 纵深防御 | 用多道独立防线避免单点失效造成完整攻破 |
| Least privilege | 最小权限 | 只授予完成当前 job 所需的最少能力 |
| OIDC | OpenID Connect | 让 workflow 用短期身份令牌向发布/签名服务认证 |
| Trusted publishing | 可信发布 | Registry 根据受信 CI 身份允许发布而非长期 token |
| `id-token: write` | 写身份令牌权限 | GitHub job 请求 OIDC token 所需权限，不等于任意仓库写权 |
| `persist-credentials` | 保留凭据 | Checkout 是否把 token 留在本地 Git credential 配置 |
| Commit SHA | 提交哈希 | 精确标识 Git commit 的摘要，常用于固定 Action |
| CODEOWNERS | 代码所有者文件 | 把敏感路径 review 路由到指定人员/团队 |
| Dependabot | 依赖更新机器人 | 定期发现并提出 dependency/Action 更新 PR 的服务 |
| Cache poisoning | 缓存投毒 | 把恶意内容写入会被后续受信构建恢复的 cache |
| Hermetic build | 密封构建 | 只读取明确声明输入、不依赖隐式宿主状态的构建 |
| Repeatable build | 可重复构建 | 相同锁定输入能够再次完成大致相同构建 |
| Reproducible build | 可复现构建 | 独立构建能产生逐字节一致的输出 |
| Evidence status | 证据状态 | Confirmed、needs verification、ruled out 等事实可信度 |
| Severity | 严重度 | 风险一旦成立时潜在影响大小 |
| Confirmed | 已确认 | 有直接证据证明某事实成立 |
| Needs verification | 待验证 | 存在合理怀疑但证据尚不完整 |
| Ruled out | 已排除 | 有足够证据证明特定暴露路径不成立 |
| Containment | 遏制 | 先阻止事故继续扩大而不一定已修复根因 |
| Credential rotation | 凭据轮换 | 撤销可能泄漏的旧凭据并发放新凭据 |
| Blast radius | 影响半径 | 依赖或构建链失陷后最多能影响的系统和用户范围 |

---

## 37. 本章小结

依赖管理不是“让 compiler 能找到库”，而是管理从声明、解析、下载、执行、构建到分发的信任链。

请记住六句话：

1. Manifest 说明允许什么，lockfile 说明这次实际选了什么；
2. Lockfile 固定已知字节，不证明这些字节安全；
3. Build script、proc macro、Action 和 installer 都是会执行的依赖；
4. Checksum、signature、provenance、SBOM 回答不同问题；
5. 最小权限让供应链组件失陷时的伤害仍然有界；
6. 事故第一遍先只读取证，再把事实状态、严重度、遏制与修复分开。

沿当前 Codex 仓库观察，这套思路被分散实现为多道门：Cargo/pnpm/uv/Bazel lock、frozen install、pnpm build policy、`cargo-deny`、Git revision 与 Action SHA pinning、Dependabot cooldown、CODEOWNERS、CI permission、checksum、平台签名和 OIDC trusted publishing。真正的安全不来自其中任何一个文件，而来自这些边界彼此补位，并且每次变化都可审查、可验证、可回退。
