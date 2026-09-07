# Codex Skill 当前处理机制与未来搜索方案

本文基于当前 Codex 仓库实现，说明 Skill 如何被发现、加载、展示给 LLM、触发和缓存，以及现有 Skill Search 的 shadow 实验逻辑。最后给出一个将搜索正式接入模型上下文的建议流程。

> 状态说明：
>
> - “当前正式流程”描述现在实际影响 LLM 行为的逻辑。
> - “Shadow Search”描述已经运行、但只记录指标且不改变 LLM 输入的实验逻辑。
> - “未来方案”是建议设计，不代表当前代码已经实现。

## 一、整体架构

Skill 采用分层暴露和按需加载，而不是把所有 `SKILL.md` 全文直接发送给 LLM。

```text
Skill roots
  ↓
扫描和解析 SKILL.md
  ↓
构建完整 Skill Catalog
  ↓
生成有上下文预算限制的 Skill 摘要目录
  ↓
LLM 或用户选择 Skill
  ↓
按需读取对应 SKILL.md
  ↓
将 Skill 指令注入当前 turn
```

完整 Catalog 和模型可见目录是两个不同概念：

- 完整 Catalog 保存在后端，包含本轮已发现的全部 Skill 元数据。
- 模型可见目录受 token/字符预算限制，通常只包含完整 Catalog 的一部分。
- `SKILL.md` 正文不会预先全部加载，只有 Skill 被选中后才会读取。

## 二、Skill 来源

当前 Skill Provider 分为三类。

### 1. Host Skill

Host Skill 位于 Codex 主机可访问的文件系统中，主要来源包括：

- 项目配置目录下的 `.codex/skills`。
- 从项目根目录到当前工作目录之间各级 `.agents/skills`。
- `$HOME/.agents/skills`。
- 兼容旧版本的 `$CODEX_HOME/skills`。
- Codex 内置并缓存到 `$CODEX_HOME/skills/.system` 的 System Skill。
- 管理员配置目录，例如 `/etc/codex/skills`。
- Plugin 提供的 Skill roots。
- app-server 运行时设置的额外 roots。

### 2. Executor Skill

Executor Skill 属于某个执行环境。

Codex 不把它转换为本地主机路径，而是通过对应 environment 的文件系统接口进行：

- capability discovery；
- 目录扫描；
- 文件读取。

### 3. Orchestrator Skill

Orchestrator Skill 通过 MCP resource 暴露，资源 URI 通常使用 `skill://...`。

Codex 通过 MCP：

- `resources/list` 发现 Skill；
- `resources/read` 读取 Skill 资源；
- 向模型提供 `skills.list` 和 `skills.read` 工具。

Provider 间通过 authority 隔离。由哪个 Provider 发现的资源，就必须通过同一个 Provider 读取，不能把远程或 environment resource 当作普通本地路径处理。

## 三、Skill 发现与解析

### 1. 目录扫描

Host 和 Executor Skill 使用受限递归目录遍历寻找 `SKILL.md`，主要限制包括：

- 最大扫描深度：6；
- 每个 root 最大目录数：2,000；
- 每个 root 最大目录项：20,000；
- 最多并发扫描 8 个 root；
- 单个 root 内最多并发加载 64 个 Skill；
- 默认跳过隐藏目录；
- User、Repo、Admin root 可以跟随目录符号链接；
- System root 不跟随目录符号链接。

这里的“搜索”是文件发现，不是根据用户请求进行相关性检索。

### 2. `SKILL.md` frontmatter

每个 Skill 的主文件是 `SKILL.md`，典型 frontmatter 如下：

```yaml
---
name: presentations
description: Create and edit PowerPoint or Google Slides presentations.
metadata:
  short-description: Create and edit slide decks
---
```

主要规则：

- `name` 可省略，默认使用 Skill 目录名；
- `description` 必填；
- name 有长度限制；
- description 有长度限制；
- frontmatter 字段会被清理成单行；
- 部分第三方 Skill 中常见的未加引号 YAML 冒号会被有限修复。

### 3. 可选的 `agents/openai.yaml`

Skill 目录可额外包含：

```text
agents/openai.yaml
```

它可以声明：

- display name；
- short description；
- 图标和品牌色；
- default prompt；
- MCP/tool dependencies；
- `allow_implicit_invocation`；
- product 限制。

该文件采用 fail-open 策略。读取或解析失败时，Codex 忽略额外元数据，但不会阻止 `SKILL.md` 被加载。

### 4. 合并与过滤

扫描完成后，Codex 会：

1. 为 Plugin Skill 增加命名空间，例如 `plugin-name:skill-name`。
2. 根据 product 限制过滤。
3. 根据配置按 name 或 path 启用、禁用 Skill。
4. 按路径去重。
5. 构建 Skill 到所属文件系统的映射。
6. 构建用于检测 Skill 文件或脚本读取的路径索引。

## 四、当前如何给 LLM 展示 Skill

### 1. 第一层：模型可见摘要目录

Codex 首先向 LLM 提供 developer role 的 Skill 目录，大致如下：

```xml
<skills_instructions>
## Skills

### Available skills
- presentations: Create and edit slide decks. (file: /.../SKILL.md)
- pdf: Read, create, and inspect PDF files. (file: /.../SKILL.md)
</skills_instructions>
```

每条记录通常包括：

- name；
- short description 或 description；
- 文件路径或 opaque resource locator。

目录不包含所有 `SKILL.md` 正文。

### 2. 上下文预算

Host Skill 摘要预算通常根据模型 context window 计算，默认约为 context window 的 2%。部分 World State 路径还会设置额外上限。

Executor/Orchestrator 扩展目录有独立的字节上限。

当所有 Skill 无法完整放入预算时，Codex依次尝试：

1. 使用 Skill root alias 缩短重复的绝对路径。
2. 缩短 description。
3. 尽量只保留 name 和 locator。
4. 如果最小表示仍然放不下，省略后续 Skill。

### 3. 当前展示顺序

Host Skill 的模型可见排序是确定性的，不是随机抽样：

```text
System
  ↓
Admin
  ↓
Repo
  ↓
User / Plugin
```

同一 scope 内按以下顺序排序：

```text
name 字典序
  ↓
SKILL.md 路径字典序
```

因此，假设完整 Catalog 中有 1,000 个 Skill，而预算只能容纳 150 个最小条目，模型看到的是按上述顺序能够放入预算的 Skill，不是随机选择，也不是根据当前用户请求选择。

目录层级不会用于轮询或公平分配。例如 10 个目录各有 100 个 Skill，Codex 不会自动从每个目录选择 15 个，而是把 Skill 打平成列表后统一排序。

### 4. 当前机制的偏置

固定排序加预算截断会产生以下偏置：

- System/Admin Skill 优先于 Repo/User Skill；
- 同 scope 下字典序靠前的 Skill 更容易展示；
- 不保证每个目录都有代表；
- 不保证每个 Plugin 都有代表；
- 与当前任务高度相关、但排序靠后的 Skill 可能被省略；
- 被省略的 Skill 对 LLM 来说通常不可发现。

## 五、当前如何选择和加载 Skill

### 1. 显式选择

Codex 可以从用户输入中识别：

- 结构化 `UserInput::Skill`；
- 带 Skill path 的 Mention；
- `$skill-name`；
- `[$skill-name](.../SKILL.md)`；
- `skill://...` resource URI。

后端使用完整 Catalog 解析显式 mention。因此，即使某个 Skill 没有出现在模型可见摘要中，只要用户提供了准确名称、结构化选择或路径，后端仍可能找到并加载它。

路径匹配比普通名称匹配更可靠，尤其是在存在同名 Skill 时。

### 2. 语义选择

如果用户没有显式点名 Skill，例如：

```text
帮我创建一个季度汇报 PPT。
```

当前正式流程不会使用 BM25 或 embedding 自动选择 `presentations`。

实际流程是：

1. LLM 查看模型可见 Skill 摘要。
2. developer instructions 要求 LLM 在任务明显匹配某个 Skill description 时使用该 Skill。
3. LLM 自己进行语义判断。
4. LLM 主动读取相应 `SKILL.md`。

因此，当前自然语言语义路由主要依赖 LLM，而不是后端检索器。

如果相关 Skill 因预算被省略，LLM 通常不知道该 Skill 存在，除非：

- 用户显式点名；
- UI 将其转换为结构化 Skill mention；
- LLM 主动扫描 Skill roots；
- 对 Orchestrator Skill 调用 `skills.list`。

### 3. 按需注入正文

显式选中 Skill 后，Codex 通过对应 Provider 读取主 `SKILL.md`，并向当前 turn 注入 user role fragment：

```xml
<skill>
<name>presentations</name>
<path>/.../presentations/SKILL.md</path>
...Skill instructions...
</skill>
```

自动注入的正文有大小限制，超出时会截断并产生 warning。模型侧指令仍要求完整读取 `SKILL.md`，因此大型 Skill 需要通过文件读取工具或 `skills.read` 继续读取。

## 六、缓存与刷新

### Host Skill

- 按有效 roots 和 Skill 相关配置缓存不可变 snapshot。
- 本地文件 watcher 监听 Skill roots。
- watcher 事件经过节流后清空缓存并发送 `skills/changed` 通知。
- Plugin 生命周期和配置变化也会清空缓存。

### Executor Skill

- 按 selected capability root 缓存在当前 thread。
- selected root 被视为稳定。
- 当前不做文件 watcher 或基于内容的失效。

### Orchestrator Skill

- Catalog 按 MCP client cache key 缓存。
- Resource read 结果有条目数和总字节数限制。
- Discovery 有超时、分页数和 Skill 数量限制。

## 七、当前 Shadow Skill Search

当前仓库已经实现 Skill 相关性搜索，但只在 shadow mode 运行。

它会：

1. 对完整的候选 Catalog 运行多种搜索算法。
2. 生成各算法的 Top-K。
3. 不把 Top-K 提供给 LLM。
4. 不改变 Skill 目录。
5. 不自动加载 Skill。
6. 观察本轮真正调用了哪个 Skill。
7. 统计搜索结果是否命中真实调用。

因此它是在线实验和评估系统，不是正式 Skill 路由器。

### 1. 搜索文档

每个候选 Skill 临时转换为：

```rust
SkillSelectionDocument {
    id,
    name,
    short_description,
    description,
}
```

当前不搜索：

- `SKILL.md` 正文；
- references；
- scripts；
- assets；
- dependencies；
- 文件路径；
- 历史调用数据。

当前 shadow candidate 主要包括 enabled、prompt-visible 的 Host 和 Orchestrator Skill，不包括 Executor Skill。

### 2. Query 构造

Query 来自当前 turn 的：

- 文本输入；
- 结构化 Skill name；
- Mention name。

多个输入用空格连接，并设置总字节上限。每种 selector 还会限制：

- query 字节数；
- query term 数；
- 候选数；
- 单字段字节数；
- 单字段 term 数；
- 返回结果数。

候选最多取前 1,000 个，因此严格来说，在超过 1,000 个符合条件的 Skill 时，它也不是对无限完整 Catalog 搜索。

### 3. 没有持久化索引

当前搜索没有：

- Lucene/Tantivy；
- SQLite FTS；
- 磁盘倒排索引；
- embedding；
- 向量数据库；
- HNSW/ANN；
- 跨 turn 的分词缓存；
- 增量索引。

每个 turn 会重新：

```text
遍历 Catalog
  ↓
分词和归一化
  ↓
建立临时 HashMap / HashSet
  ↓
全量打分
  ↓
排序并选 Top-K
```

对于约 1,000 个 Skill，这是可以接受的廉价线性扫描；对于数万到数十万个 Skill，则需要正式的持久化或线程级索引。

## 八、现有四种搜索算法

### 1. Weighted Lexical

Weighted Lexical 对三个字段赋予不同的人工权重：

```text
name              高权重
short_description 中权重
description       低权重
```

大致评分包括：

| 匹配方式 | 分数 |
|---|---:|
| Query 包含完整 Skill name phrase | 256 |
| name 完全等于 query term | 128 |
| name 包含完整 term | 64 |
| name 前缀近似 | 24 |
| short description 包含 term | 16 |
| short description 前缀近似 | 6 |
| description 包含 term | 4 |
| description 前缀近似 | 1 |

如果一个 Skill 命中多个不同 query term，还会增加覆盖奖励。

它适合：

- Skill name 明确；
- description 中存在直接关键词；
- 数据规模较小；
- 需要可解释的稳定排序。

### 2. Fielded BM25

Fielded BM25 把 Skill 分为三个字段：

```text
name              weight = 8
short_description weight = 4
description       weight = 1
```

参数：

```text
k1 = 1.2
b  = 0.75
```

每个 turn 临时计算：

- 每个字段的平均长度；
- 每个 term 的 document frequency；
- 每个文档每个字段内的 term frequency。

IDF 大致为：

```text
idf = ln(1 + (N - df + 0.5) / (df + 0.5))
```

字段 term frequency 会经过长度归一化，然后乘以字段权重。

这里的“索引”只是临时的：

```rust
HashMap<String, usize> // term -> document frequency
```

它不是完整 posting-list 倒排索引，搜索时仍会扫描文档并打分。

### 3. Character N-gram

Character N-gram 用来改善：

- 中文等没有空格的语言；
- 单复数和词尾变化；
- 部分拼写近似；
- 长词之间的局部匹配。

它通常生成 2–5 字符的 n-gram。较长 ASCII term 通常从 3-gram 开始，中文可以从 2-gram 开始。

例如：

```text
calendar
→ cal, ale, len, end, nda, dar
→ cale, alen, lend, enda, ndar
```

```text
创建演示文稿
→ 创建, 建演, 演示, 示文, 文稿
```

每个 gram 使用 document frequency 计算稀有度，并按 name、short description、description 的字段权重累加。

### 4. Multi-query Lexical

Multi-query Lexical 用于处理复合任务。

例如：

```text
检查日历，然后起草邮件，同时总结 PDF。
```

会拆成多个 query view：

```text
完整 Query
检查日历
起草邮件
总结 PDF
```

拆分符包括：

- 换行；
- 句号、问号、感叹号、分号；
- `and then`；
- `and`；
- `then`；
- `also`。

每个 view 独立执行 Weighted Lexical，最后根据：

1. 任一 view 中的最佳排名；
2. 完整 query 中的排名；
3. 命中的 view 数量；
4. 首次出现的 view；
5. document id；

进行排名融合。

## 九、Shadow Search 如何评估效果

四种算法各自产生候选排名后，系统继续观察本轮实际 Skill 调用。

当发生以下行为时，可以视为 Skill invocation：

- 后端显式注入某个 Skill；
- 读取某个 Orchestrator Skill；
- 读取某个 Skill 的 `SKILL.md`；
- 执行某个 Skill `scripts` 目录中的脚本。

系统将真实调用与每种 selector 的 Top-K 对比，记录：

- hit / miss；
- 真实 Skill 的排名；
- 1、2–5、6–10、11–20 等 rank bucket；
- 候选缩减比例；
- 搜索耗时；
- query term 数量；
- query 是否被截断；
- candidate set 是否被截断；
- Query 脚本类型，例如 ASCII、CJK、mixed。

这套指标的目标是评估：哪一种廉价方法最适合成为未来正式 Skill 召回器。

## 十、未来将搜索正式接入的建议流程

未来不应简单地用搜索结果完全替换当前 Skill 目录，而应使用“保留项 + 显式项 + 检索项”的混合策略。

### 1. 推荐链路

```text
完整 Skill Catalog
  ↓
过滤 disabled / product-incompatible Skill
  ↓
解析用户显式 mention
  ↓
提取必须始终可见的 System/Admin Skill
  ↓
针对剩余 Catalog 进行相关性搜索
  ↓
召回 Top-K
  ↓
去重并按优先级合并
  ↓
在上下文预算内渲染摘要
  ↓
LLM 选择 Skill
  ↓
按需读取完整 SKILL.md
```

### 2. 候选集合组成

建议模型可见集合由以下部分组成：

```text
强制可见 Skill
+ 用户显式 mention 的 Skill
+ 当前会话近期使用的 Skill
+ 搜索召回 Top-K
+ 少量探索性或默认 Skill
```

优先级建议：

```text
P0：用户显式 mention
P1：安全、管理或产品强制 Skill
P2：搜索高相关 Skill
P3：本线程近期使用 Skill
P4：默认/探索性 Skill
```

### 3. 第一阶段可直接复用现有搜索

最小可落地方案可以不引入新依赖：

1. 继续使用完整内存 Catalog。
2. 对最多 1,000 个 Skill 运行现有 selectors。
3. 选择一个线上算法，例如 Fielded BM25 或 Weighted Lexical。
4. 获取 Top 20–50。
5. 将 Top-K 合并到强制项和显式项。
6. 用现有 bounded renderer 生成 `<skills_instructions>`。

这一步可以快速验证搜索正式接入是否提升 Skill invocation recall。

### 4. 推荐的混合召回

单一算法可能对不同语言和任务类型表现不稳定，建议使用混合召回：

```text
Weighted Lexical Top 30
+ Fielded BM25 Top 30
+ Character N-gram Top 30
+ 最近使用 Skill
  ↓
去重
  ↓
Rank fusion 或轻量 rerank
  ↓
最终 Top 20–50
```

推荐优先保证：

- name 精确匹配；
- namespace-qualified name 精确匹配；
- 用户显式提到的普通 Skill 名称；
- 中文 n-gram 召回；
- description 中的 BM25 相关性。

### 5. 对大规模 Catalog 建立真正索引

当 Skill 数量达到数万以上时，应在 Catalog 更新时构建索引，而不是每个 turn 全量重新分词。

建议索引结构：

```text
term
  → posting list [
      {
        skill_id,
        field,
        term_frequency
      }
    ]
```

同时保存：

- 每个字段的文档长度；
- 字段平均长度；
- document frequency；
- name 精确匹配表；
- namespace/name 前缀表；
- 中文字符 n-gram posting；
- Catalog generation/version。

更新方式：

```text
Skill watcher / Plugin lifecycle / MCP generation change
  ↓
生成新的 Catalog generation
  ↓
增量更新或重建索引
  ↓
原子切换到新索引
```

查询时：

```text
用户 Query
  ↓
Query normalization
  ↓
倒排索引召回
  ↓
Fielded BM25 打分
  ↓
字符 n-gram 补召回
  ↓
Rank fusion
  ↓
Top-K
```

### 6. 是否需要 embedding

第一阶段不一定需要 embedding。Skill 的 name 和 description 通常很短，BM25、字段权重和字符 n-gram 已经能覆盖大量场景。

只有在以下情况明显存在时，再考虑 embedding：

- 用户请求与 Skill description 的词汇重叠很少；
- 同义表达很多；
- Skill description 质量不稳定；
- 跨语言检索需求强；
- Catalog 非常大。

如果引入 embedding，建议使用混合检索：

```text
Lexical/BM25
+ Character N-gram
+ Vector similarity
  ↓
Rank fusion / rerank
```

不建议只使用向量相似度，因为：

- Skill name 精确匹配非常重要；
- namespace 和工具名具有强 lexical 信号；
- 向量检索可能把语义相似但操作权限不同的 Skill 排得过高；
- lexical 结果更容易解释和调试。

## 十一、正式接入时需要关注的问题

### 1. Prompt 稳定性

搜索结果每轮变化会改变 developer context，可能降低 prompt cache 命中率。

可以采用：

- 稳定排序；
- 只有 Top-K 集合发生实质变化时更新；
- 保留 thread-level 最近 Skill；
- 将动态 Skill 目录放入 World State 增量更新。

### 2. 权限和 authority

搜索结果必须保留：

- Skill authority；
- package ID；
- main resource；
- 正确的 Provider 路由。

不能把 Executor 或 Orchestrator resource 转换为主机路径。

### 3. 恶意或低质量描述

Skill description 会直接参与搜索并展示给模型，需要：

- 长度限制；
- 控制字符过滤；
- XML/markup escaping；
- product 和 policy 过滤；
- 可信来源优先级；
- 避免通过 description 注入额外指令。

### 4. 同名 Skill

搜索结果应以 authority + package ID 作为唯一标识，而不是只使用 name。

模型可见目录需要展示：

- namespace-qualified name；
- 来源；
- 必要时显示短 locator。

### 5. 评估指标

正式接入前后至少比较：

- 真实 Skill invocation 的 Recall@K；
- MRR；
- 搜索耗时；
- Skill 摘要 token 数；
- prompt cache 命中率；
- 错误 Skill 调用率；
- 用户显式 Skill 被遗漏的比例；
- 不同语言的召回率；
- Host、Executor、Orchestrator 各来源的召回率。

## 十二、总结

当前正式 Skill 流程是：

```text
扫描全部 Skill
  ↓
构建完整 Catalog
  ↓
固定排序
  ↓
压缩路径和描述
  ↓
按预算展示部分 Skill
  ↓
LLM 根据可见摘要自行选择
  ↓
按需读取 SKILL.md
```

当前 Shadow Search 流程是：

```text
用户请求
  ↓
对最多 1,000 个候选 Skill 运行四种廉价搜索
  ↓
各自产生 Top-K
  ↓
不提供给 LLM
  ↓
与真实调用对比并记录指标
```

推荐的未来正式流程是：

```text
完整 Catalog
  ↓
显式项和强制项优先
  ↓
Lexical + BM25 + Character N-gram 搜索
  ↓
召回并融合 Top-K
  ↓
在预算内展示相关 Skill
  ↓
LLM 选择
  ↓
按 authority 读取完整 SKILL.md
```

核心变化不是“把更多 Skill 塞给 LLM”，而是“在相同上下文预算内，把固定顺序的 Skill 替换成当前任务最相关的 Skill”。
