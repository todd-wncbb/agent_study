# 源码精读 05：Skill 如何发现、解析、去重、公告并加载完整 `SKILL.md`

> 本篇关注 Agent 基础设施中的 Skill 主链：一个磁盘上的 `SKILL.md`，如何先变成轻量目录项，再因用户 Slash、模型选择、Agent 预加载或运行期路径发现而进入模型上下文。
>
> 源码基线：`ed6d543`。若后续源码变化，请优先按本文给出的类型名和函数名重新定位。

---

## 1. 先给结论：Skill 不是一次“加载”完成的

Grok Build 把 Skill 拆成两个阶段：

1. **发现阶段**：扫描路径，只读取 frontmatter 和少量正文，生成 `SkillInfo`。
2. **激活阶段**：需要使用时，才读取完整正文，去掉 frontmatter，解析内部链接，替换参数，然后注入模型上下文。

第一阶段解决“有哪些 Skill、各自什么时候适用”；第二阶段解决“这次具体要遵守哪些完整指令”。

这是一种 Progressive Disclosure：目录常驻，正文按需加载。

---

## 2. 不要把四种“搜索”混在一起

源码中没有一个统一的 `search_skills(query)` 语义检索服务。用户口中的“Skill 如何搜索”，实际对应四条不同路径：

| 场景 | 真正机制 | 核心位置 |
| --- | --- | --- |
| 启动时找 Skill | 固定配置目录的文件系统扫描 | `prompt/skills.rs` |
| 用户输入 `/foo` | 已构建 Catalog 的名称解析 | `slash_commands.rs` |
| 模型决定用哪个 Skill | 阅读名称、描述、触发条件和路径，由模型语义判断 | `<agent_skills>` 目录 |
| 运行中进入新目录 | 文件工具输出触发向上扫描 | `SkillDiscoveryReminder` |

理解这四种机制，是读懂整个子系统的第一把钥匙。

---

## 3. 全链路地图

```text
磁盘 / 插件 / Server / Bundled
            |
            v
list_skills_with_plugins
            |
            +--> 路径发现、Frontmatter 解析
            +--> Scope 排序、Path/Name 去重
            +--> ignore 删除、disabled 标记
            |
            v
        Vec<SkillInfo>
          /    |     \
         /     |      \
        v      v       v
首轮目录公告  Slash目录  AvailableSkills
   |           |          |
   |           |          +--> 运行时投影
   |           +--> /foo 精确解析
   v                      
模型看见摘要和 fullPath
   |                         
   +--> 模型调用 Read(fullPath)
   |
用户 /foo
   +--> Shell 直接读取完整正文
        + 参数替换
        + <skill_information>
        + 紧跟 <user_query>
```

这里最重要的中间类型是 `SkillInfo`。它不是完整 Skill 文件，而是 Skill 在发现期、目录期、运行期之间传递的元数据载体。

---

## 4. 模块职责不是集中在一个 crate

Skill 主链横跨三个 crate：

| crate | 主要责任 |
| --- | --- |
| `xai-grok-agent` | 启动发现、来源优先级、插件合并、Agent Definition 预加载 |
| `xai-grok-tools` | 元数据类型、文件解析、正文加载、动态发现、公告状态、Read 特例 |
| `xai-grok-shell` | Slash 解析、Turn 期正文注入、动态公告落进 Conversation |

因此只读 `prompt/skills.rs` 会看到“Skill 列表从哪里来”，却看不到“用户输入 `/foo` 后正文在哪里进入 Prompt”。

---

## 5. 第一核心类型：`SkillScope`

文件：

```text
crates/codegen/xai-grok-tools/src/implementations/skills/types.rs
```

枚举顺序就是优先级顺序：

```rust
pub enum SkillScope {
    Local = 0,
    Repo = 1,
    User = 2,
    Server = 3,
    Bundled = 4,
    Plugin = 5,
}
```

数值越小，优先级越高。

这使 `skills.sort_by_key(|s| s.scope)` 不只是排序展示，而是在为后续 first-seen-wins 去重准备输入。

---

## 6. Scope 回答的是“谁覆盖谁”

- `Local`：当前工作目录附近的配置，最贴近当前任务。
- `Repo`：仓库级配置。
- `User`：用户全局配置。
- `Server`：Launcher 注入的服务端同步 Skill。
- `Bundled`：平台内置内容。
- `Plugin`：插件来源的最低 bare-name 优先级。

源码不是在每次冲突时写六层 `if`；它先按 Scope 排序，再让先出现者取得名称所有权。

---

## 7. 第二核心类型：`SkillInfo`

`SkillInfo` 大致分成六组字段。

### 7.1 身份字段

```rust
name: String
display_name: Option<String>
path: String
scope: SkillScope
```

`name` 是可调用身份；`display_name` 是展示标签。

两者分离，是为了解决复制目录、插件命名空间等冲突。

### 7.2 匹配和展示字段

```rust
description: String
when_to_use: Option<String>
short_description: Option<String>
argument_hint: Option<String>
```

模型主要通过 `description` 与 `when_to_use` 判断何时使用；UI 可以用更短的 `short_description` 和参数提示。

### 7.3 来源字段

```rust
config_source: Option<ConfigSource>
plugin_name: Option<String>
plugin_version: Option<String>
plugin_root: Option<String>
plugin_data: Option<String>
```

这些字段不仅用于 UI；Plugin Root/Data 还参与正文变量替换。

### 7.4 执行提示字段

```rust
allowed_tools: Option<Vec<String>>
model: Option<String>
effort: Option<String>
```

这些是 Skill 声明的执行元数据。阅读时必须区分“字段被解析并保存”和“当前调用路径真的强制执行了它”；不能仅凭字段存在就推断所有路径都会切模型或重建工具集。

### 7.5 可见性字段

```rust
user_invocable: bool
disable_model_invocation: bool
enabled: bool
paths: Option<Vec<String>>
```

它们表达的是不同维度：

- `user_invocable=false`：用户 Slash 路径不可调用。
- `disable_model_invocation=true`：模型目录中不公告，不能自动选择。
- `enabled=false`：配置层整体禁用，但对象仍保留。
- `paths=...`：先隐藏，直到相关文件被触碰才激活。

### 7.6 延迟正文

```rust
body: Option<String>
```

正常磁盘发现后是 `None`。Agent Definition 预加载或产品注入内容时，才可能已经带正文。

---

## 8. `name` 与 `display_name` 为什么不能合并

假设用户复制目录：

```text
skills/japandi/SKILL.md   name: japandi
skills/japandi2/SKILL.md  name: japandi
```

若只保留 frontmatter 名称，第二个 Skill 必然被遮蔽。

源码会尝试把冲突方重新识别为目录名：

```text
name = japandi2
display_name = japandi
```

调用身份不冲突，作者原本写的展示名仍然保留。

---

## 9. `dedup_key()` 与 qualified name

普通 Skill 的去重键就是 `name`。

插件 Skill 的去重键是：

```text
plugin-name:skill-name
```

因此两个插件都提供 `deploy` 时，仍可通过：

```text
plugin-a:deploy
plugin-b:deploy
```

分别访问。

`format_skill_name()` 对非插件 Skill 则生成：

```text
local:commit
repo:commit
user:commit
```

---

## 10. 启动入口：`AgentBuilder::build`

Agent 构建时执行：

```rust
list_skills_with_plugins(
    Some(&working_dir_str),
    &self.skills_config,
    self.plugin_registry.as_deref(),
    self.compat,
).await
```

前提是 `definition.discover_skills` 为真；否则初始列表为空。

得到的 `skill_info` 会同时流向：

1. Agent Definition 的预加载解析。
2. `SessionContext.skills`。
3. Tool Registry 的 `AvailableSkills` 与 `SkillManager`。
4. 第一用户前缀中的 Skill Catalog。

这说明发现结果不是只给 Prompt 使用，它还是多个运行时投影的共同输入。

---

## 11. `SkillsConfig` 的五类输入

`prompt/skills.rs::SkillsConfig` 包含：

```rust
paths
ignore
disabled
server_skill_dirs
bundled_skill_dirs
```

其中：

- `paths` 增加发现来源。
- `ignore` 按规范化路径前缀彻底删除。
- `disabled` 按名称保留对象但设为不可用。
- `server_skill_dirs` 由 Launcher 注入并标记为 Server。
- `bundled_skill_dirs` 由 Launcher 注入并标记为 Bundled。

---

## 12. `ignore` 与 `disabled` 的语义差异

| 配置 | 是否仍在 `Vec<SkillInfo>` | 是否公告 | 是否可调用 | 适用场景 |
| --- | --- | --- | --- | --- |
| `ignore` | 否 | 否 | 否 | 完全隐藏某来源或目录 |
| `disabled` | 是，`enabled=false` | 否 | 否 | 保留配置可见性但停用 |

`filter_skills` 对每条 ignore 路径展开 `~`、做 canonicalize，然后用 `starts_with` 过滤整个子树。

---

## 13. 发现目录的权威函数

`collect_skill_config_dirs` 是启动发现和文件 Watcher 共用的目录来源函数。

这是一处很重要的设计：发现器和监听器必须对“哪些目录算 Skill 配置目录”达成一致，否则会出现启动能看到、修改却不刷新，或启动看不到、Watcher 却乱报的漂移。

---

## 14. 从 cwd 向 Git Root 上行

当存在 Git 仓库时，函数从 cwd 一层层向上，直到 Git Root：

```text
repo/a/b/.grok
repo/a/.grok
repo/.grok
```

越靠近 cwd 的目录越早进入列表，因此同名时优先。

这允许 Monorepo 的子目录覆盖仓库根配置。

---

## 15. Vendor 兼容目录不是无条件扫描

`.grok` 与 `.agents` 是原生目录。

`.claude` 与 `.cursor` 由 `CompatConfig` 的 skills 开关控制。兼容配置决定是否纳入这些厂商格式，而不是散落在每个调用方分别判断。

动态发现有一个刻意差异：它扫描 `.grok`、`.agents`，按开关扫描 `.claude`，但不动态扫描 `.cursor`；源码注释说明这是保持历史行为。

---

## 16. Workspace User 与 Global User

除 cwd/Git Root 链外，还可能加入：

1. 可选的 workspace user directory。
2. Grok Home。
3. `~/.agents`。
4. 兼容开关允许时的 `~/.claude`、`~/.cursor`。
5. `[skills].paths` 指定目录。

只加入实际存在的目录，并按 canonical path 去重。

---

## 17. `scope_for_config_dir`

Scope 的推导规则是：

1. Home 直接子目录：`User`。
2. 配置目录的父目录就是 cwd：`Local`。
3. 位于 Git Root 内：`Repo`。
4. 其余：`User`。

Scope 是来源优先级，不等于文件权限或 Sandbox 权限。

---

## 18. 标准目录里究竟扫描什么

对一个配置根，例如：

```text
repo/.grok
```

会扫描两类内容：

```text
repo/.grok/skills/**/SKILL.md
repo/.grok/commands/*.md
```

Skill 先于 Command 收集，所以名字冲突时 Skill 优先。

Command 目录只平铺扫描 `.md`；Skill 目录递归寻找 `SKILL.md`。

---

## 19. 递归深度与确定性顺序

`walk_for_skill_md` 有两个重要约束：

- 最大深度 `MAX_SKILL_WALK_DEPTH = 5`。
- 每层子目录先排序再遍历。

排序不是为了美观。后续冲突策略是 first-seen-wins，如果依赖文件系统的随机 `read_dir` 顺序，相同目录结构在不同机器上可能选出不同 winner。

---

## 20. 配置路径的两种形式

`SkillsConfig.paths` 中每一项可以是：

1. 一个具体 `SKILL.md`。
2. 一个需要递归扫描的目录。

`~/...` 会经 `expand_tilde` 展开。

若解析路径位于 Git Root 内，Scope 为 Repo；否则为 User。

解析后还会写入 `ConfigSource::ConfigToml`，供 inspect 和 UI 区分“自动发现”与“配置显式加入”。

---

## 21. Server 与 Bundled 注入

`collect_injected_skills` 接收目录和指定 Scope：

```rust
collect_injected_skills(server_dirs, SkillScope::Server)
collect_injected_skills(bundled_dirs, SkillScope::Bundled)
```

不存在的目录被静默跳过；存在的目录递归寻找 `SKILL.md`。

它们放在本地、仓库、用户与配置路径之后，优先级更低。

---

## 22. Plugin Skill 的发现

`collect_plugin_skills` 遍历 `registry.enabled_plugins()`。

每个插件有两类入口：

- `skill_dirs`：递归寻找 Skill。
- `command_dirs`：平铺扫描 Markdown Command。

解析后调用 `stamp_plugin_fields`，补充 Plugin Name、Version、Root、Data 与 ConfigSource。

---

## 23. Plugin 为什么按目录名建立身份

插件中的 frontmatter 可能复制后未更新，或多个 sibling 写了相同 `name`。

`stamp_plugin_fields` 把 Skill 目录 basename 规范化后作为调用身份；原 frontmatter name 放进 `display_name`。

这样 qualified identity 稳定为：

```text
plugin-name:directory-name
```

而不是依赖可能过期的作者标签。

---

## 24. Frontmatter 发现只读前 4096 Bytes

`read_frontmatter_only` 的上限：

```rust
MAX_FRONTMATTER_BYTES = 4096
```

它逐行读取：

1. 跳过开头空行。
2. 寻找第一个 `---`。
3. 读取到闭合 `---`。
4. 超过上限就停止。

启动发现不需要把数百 KB 的 Skill 正文全部放进内存。

---

## 25. Frontmatter 的容错是分层的

`parse_skill_frontmatter` 不会因为一个字段类型错误就丢掉整份 Skill。

解析顺序：

1. 正常 `serde_yaml` 解析。
2. 对含 YAML 指示字符的简单标量加引号后重试。
3. 仍失败时，逐行恢复少量关键标量。

可恢复字段仅包括：

```text
name
description
when-to-use
when_to_use
```

列表和 Map 不在逐行恢复范围，避免错误地把复杂结构拼成字符串。

---

## 26. Name 规范化

`normalize_skill_name` 执行：

1. 去首尾空白。
2. 转小写。
3. 非 `[a-z0-9]` 字符改成 `-`。
4. 连续 `-` 合并。
5. 去首尾 `-`。

例如：

```text
Tool v1.2 -> tool-v1-2
```

有效名称最长 64 字符，并且不能空、不能首尾连字符、不能出现双连字符。

---

## 27. Name 的 fallback

若 frontmatter 没有合法 `name`，解析器尝试使用：

- `SKILL.md` 的父目录名。
- 普通 Command Markdown 的文件 stem。

这意味着一个没有 frontmatter 的：

```text
.grok/skills/review/SKILL.md
```

仍可成为名为 `review` 的 Skill。

---

## 28. Description 的 fallback

如果 frontmatter 没有有效 description，解析器最多查看正文前：

```rust
MAX_BODY_PEEK_BYTES = 2048
```

顺序是：

1. 第一段顶层 prose。
2. 若没有 prose，再找首个标题或 prose。
3. 最后退回 Skill Name。

列表、表格、代码块、引用和图片 alt text 不被当作首段描述。

---

## 29. `has_user_specified_description`

这个布尔值记录 description 是否真的来自 frontmatter。

普通本地/仓库 Skill 即使描述来自正文 fallback，也可进入目录；插件 Skill 若既没有作者 description，也没有 `when_to_use`，在非兼容展示模式下会被排除。

原因是插件可能附带大量 Markdown Command，自动抽出的标题并不一定足以成为可靠的模型触发说明。

---

## 30. `allowed-tools` 的解析

支持两种写法：

```yaml
allowed-tools: Read, Grep, Bash(git diff:*)
```

或：

```yaml
allowed-tools:
  - Read
  - Grep
```

字符串切分时会保留括号内部的空格和逗号，因此 `Bash(...)` 不会被拆碎。

---

## 31. Boolean 的严格语义

`parse_boolean_frontmatter` 只把以下值视为真：

```yaml
true
"true"
```

`yes` 等 YAML 风格值不会被当作真。

字段默认值：

- `user-invocable` 缺省为 true。
- `disable-model-invocation` 缺省为 false。

---

## 32. `paths:` 是条件公告，不是发现路径

Frontmatter 中的：

```yaml
paths:
  - src/**
  - tests/{unit,integration}/**
```

不是告诉系统去哪里找 Skill，而是告诉系统“触碰哪些工作区文件后，才向模型公开这个 Skill”。

尾部 `/**` 会归一化；全是 `**` 等价于无条件。

---

## 33. Frontmatter Parsing 的单一 choke point

`parse_skill_files` 是启动扫描、动态发现和 Host 驱动扫描共同经过的解析入口。

统一入口保证：

- Name 规范化一致。
- Description fallback 一致。
- Vendor Builtin denylist 一致。
- `SkillInfo` 字段默认值一致。

---

## 34. Vendor Builtin denylist

源码会丢弃出现在对应 Vendor 目录下的已知默认 Skill，例如 Cursor 的 `shell`、Claude 的 `pdf` 等。

判断同时检查：

1. Name 在 denylist。
2. Path 确实位于 `/.cursor/` 或 `/.claude/`。

所以用户自己放在 `~/.grok/skills/shell` 的同名 Skill 不会被误删。

---

## 35. Skill 发现不读取 `.gitignore`

这与 AGENTS.md 发现不同。

Skill 扫描只进入已知配置根，团队经常故意把 `.grok` 或 `.claude` 放进 `.gitignore`，但仍希望本地 Agent 加载它们。

因此隐藏 Skill 应使用 `[skills] ignore`，不能依赖 `.gitignore`。

---

## 36. 第一层去重：Canonical Path

同一个文件可能同时由自动发现和 `[skills].paths` 找到。

`dedupe_skills` 先 canonicalize 路径；若已存在：

- 不再插入副本。
- 若被丢副本带 `config_source`，而保留项没有，则把来源 stamp 合并回保留项。

Scope 不因来源 stamp 改变。

---

## 37. 第二层去重：Name

名称冲突通常遵循 first-seen-wins。

由于上游已经按 Scope 排序，因此：

```text
Local > Repo > User > Server > Bundled
```

Cross-scope 同名就是有意覆盖，低优先级项被隐藏。

---

## 38. Same-scope 冲突的恢复

同一 Scope 两个 Skill 声明相同 frontmatter name 时，源码不会立即丢后者。

它调用 `rekey_to_dir_basename`，条件是：

- 目录名存在。
- 规范化后合法。
- 不等于当前名称。
- 目录名尚未被占用。

成功后，目录名成为调用身份，frontmatter name 成为展示名。

---

## 39. 真正的 basename owner 可以夺回名称

冲突算法还处理一个更难的顺序问题：

1. 先到的 copied Skill 通过 re-key 暂时占了某名称。
2. 后到 Skill 的目录 basename 与其 frontmatter name 完全一致。

这时源码尝试迁移旧 claimant；若旧 claimant 已无法再迁移，真正的 frontmatter/basename owner 可以把它驱逐。

这保证排序细节不会让过期复制品永远遮住真正所有者。

---

## 40. Server/Bundled 的冲突更简单

Server 与 Bundled Skill 同名冲突时不会执行 sibling re-key 恢复，而是按设计被更高优先级项 shadow。

它们属于平台供应层，不应因为目录复制细节在用户命名空间中额外冒出别名。

---

## 41. Native 与 Plugin 的合并

`merge_skills_with_plugins` 先完成 Native 去重，再追加 Plugin Skills。

若 Plugin bare name 与 Native 冲突：

- Native bare name 保持主导。
- Plugin qualified form 仍保留。

Plugin 之间按 `plugin:name` 去重。

---

## 42. 到这里仍未加载完整正文

启动发现后的典型 `SkillInfo`：

```text
name = "review"
description = "Review a change..."
path = "/repo/.grok/skills/review/SKILL.md"
body = None
```

目录所需信息已经齐全，但完整指令仍在磁盘上。

这是理解后续所有路径的分界点。

---

## 43. 第一种激活：Agent Definition 预加载

Agent Definition 可以声明：

```yaml
skills:
  - review
  - plugin-a:deploy
```

`resolve_preloaded_skills` 对每个名称：

1. 在 discovered 列表中按 bare 或 qualified name、忽略大小写查找。
2. 调用 `load_skill_with_body` 读取完整文件。
3. 把正文写入克隆后的 `SkillInfo.body`。

---

## 44. 预加载进入 System Prompt Body

`AgentBuilder::build` 对预加载结果调用：

```rust
format_skills_for_injection(&preloaded)
```

每份正文经 `build_skill_message` 包成：

```xml
<skill name="review" description="..." path="/.../SKILL.md">
完整正文
</skill>
```

然后放到 `definition.prompt_body` 前面。

因此预加载 Skill 属于 Agent 身份的一部分，不需要每轮再显式激活。

---

## 45. 第二种激活：首轮 Skill Catalog

未预加载的普通 Skill 以摘要目录进入第一用户消息。

`UserMessageContext::render_skill_listing_xml` 使用共享 formatter 生成：

```xml
<agent_skill fullPath="/abs/path/SKILL.md">
描述 — Use when: 触发条件
</agent_skill>
```

外层模板负责 `<agent_skills>` / `<available_skills>` 包装和“用 Read 读取完整内容”的说明。

---

## 46. Catalog 中为何必须有 absolute path

Catalog 不携带完整正文，只提供定位地址。

模型选择某个 Skill 后，可以直接调用 Read Tool 读取 `fullPath`，无需猜测 cwd、Scope 或插件安装目录。

Fork/Overlay Session 中，公告可把真实 cwd 前缀替换为展示 cwd；运行时仍保留真实路径。

---

## 47. 模型“搜索” Skill 的真实机制

对模型而言，没有 BM25、Embedding 或关键词索引调用。

它看到：

- Skill 描述。
- `when_to_use`。
- Absolute Path。

然后利用语言模型自身的语义判断选择候选，并读取全文。

所以高质量 description/when-to-use，实际上就是 Skill 的检索索引。

---

## 48. Catalog 的预算

标准默认预算来自：

```text
Context Window × 4 chars/token × 50%
```

未知上下文时按 200K Token 推导默认字符预算。

单项 description + when-to-use 上限为 400 Bytes。

---

## 49. 三档降级策略

目录超过预算时：

1. 完整但单项封顶的描述。
2. 按比例缩短 description 与 when-to-use。
3. 只保留名称，并给出还有多少 Skill 与来源目录。

预算裁剪只影响目录，不影响后续真正加载的 Skill 正文。

---

## 50. `disable_model_invocation` 的落点

`format_announcement_xml` 和 Markdown formatter 都过滤：

```rust
s.enabled && !s.disable_model_invocation
```

所以模型不会在目录里看到这种 Skill。

它仍可能保留在管理列表中，并根据 `user_invocable` 通过用户 Slash 使用。

---

## 51. 第三种激活：用户输入 `/skill`

Slash 路径由 `xai-grok-shell/src/session/slash_commands.rs` 处理。

核心类型：

```rust
struct ParsedSkillRef {
    name: String,
    args: String,
    skill_path: String,
    qualified_name: String,
    plugin_name: Option<String>,
}
```

解析结果已经绑定到具体 `skill_path`，后面不需要再次按名称猜测。

---

## 52. Slash 只匹配注册过的 Skill

`parse_skill_references` 从左到右扫描以空白为边界的 `/word`。

只有 `EffectiveCommandCatalog::skill(word)` 能解析的 token 才算 Skill。

因此这些普通文本不会被误认：

```text
/api/v2/users
/tmp/file
```

除非它们刚好是已注册命令。

---

## 53. 多 Skill 与参数边界

输入：

```text
/review src/auth.rs /test unit only
```

扫描器记录两个 hit。

每个 Skill 的 args 是从自身 token 结束，到下一个 Skill token 开始之间的文本；最后一个取到输入末尾。

---

## 54. Builtin 与 Skill 冲突

Slash Resolve 先通过统一 `EffectiveCommandCatalog` 处理 Builtin、Workflow 和 Skill 的有效命令面。

测试覆盖 Builtin shadow 同名 Skill、qualified skill 绕过 bare-name 冲突等场景。

这说明 Slash 补全列表和真正执行解析必须共享同一冲突规则。

---

## 55. `/skill` 是零额外模型轮次加载

`build_skill_information_for_refs` 在发起当前模型请求前完成：

1. 按 `skill_path` 找 `SkillInfo`。
2. `load_skill_content` 读取完整正文。
3. `apply_substitutions` 替换参数和环境变量。
4. `build_skill_block` 包装正文。
5. `build_skill_information` 合并多份 Skill。

模型第一次看到当前用户问题时，完整 Skill 已经在同一个请求中。

---

## 56. `load_skill_content`

加载顺序：

1. 若 `SkillInfo.body` 非空，直接信任预加载正文。
2. 若 path 是 `scheme://...` 且没有正文，返回错误，因为它不是磁盘文件。
3. 异步读取完整文件。
4. `extract_skill_body` 去掉 YAML frontmatter。
5. `resolve_skill_internal_links` 处理相对链接。

磁盘全文加载与发现期的 4096/2048 Bytes 读取上限是两件不同的事。

---

## 57. 为什么预加载 `body` 不再剥 frontmatter

生产者在写入 `body` 前已经剥过 frontmatter。

若再次调用 `extract_skill_body`，一个正文开头恰好是 Markdown Horizontal Rule 的：

```markdown
---
```

可能被误判为新 frontmatter。

所以 `body` 非空时直接返回。

---

## 58. 内部链接不是递归自动加载

`resolve_skill_internal_links` 做的是：

- 找 Markdown Link/Image。
- 若相对目标真实存在。
- canonicalize 后仍位于 Skill 目录内部。
- 把目标改成绝对路径。

它不会自动读取链接目标并把内容拼入 Prompt。

后续是否读取 `references/foo.md`，仍由模型依照 Skill 指令调用 Read。

---

## 59. 链接安全边界

只有 canonical target 仍以 canonical skill directory 为前缀时才重写。

因此指向目录外部的 `../secret` 不会因为链接解析器而被包装成可信的 Skill 内部资源。

注意：这只是链接重写边界，不替代 Read Tool 自身的权限和 Sandbox 检查。

---

## 60. 参数变量

Skill 正文支持：

```text
$ARGUMENTS
$ARGUMENTS[N]
$N
```

其中 N 从 0 开始，参数按空白切分。

替换顺序先处理 `$ARGUMENTS[N]`，再 `$N`，最后 `$ARGUMENTS`，避免短 token 抢先吃掉长 token。

---

## 61. 路径与 Session 变量

支持 Grok 原生名与兼容别名：

```text
${SKILL_DIR} / ${CLAUDE_SKILL_DIR}
${SESSION_ID} / ${CLAUDE_SESSION_ID}
${GROK_PLUGIN_ROOT} / ${CLAUDE_PLUGIN_ROOT}
${GROK_PLUGIN_DATA} / ${CLAUDE_PLUGIN_DATA}
```

Plugin Root/Data 来自发现时 stamp 到 `SkillInfo` 的字段。

---

## 62. 未消费参数时的兼容后缀

若正文没有使用任何参数 token，但用户传了参数，系统追加：

```markdown
**ARGUMENTS:** 原始参数
```

仅使用 `${SKILL_DIR}` 等路径变量不算消费参数，所以仍追加该后缀。

这保证旧 Skill 即使没有显式变量，也不会丢掉 Slash 后面的用户输入。

---

## 63. `<skill_information>` 的结构

最终形式近似：

```xml
<skill_information>
<skills_referenced>
<skill name="review" path="/repo/.grok/skills/review/SKILL.md"/>
</skills_referenced>
<skill name="review" args="src/auth.rs">
完整正文
</skill>
</skill_information>
```

`skills_referenced` 按 `(name, path)` 去重但保留原顺序。

---

## 64. Skill 正文在 User Query 后面

`ParsedPrompt::assemble_message` 保证：

```text
<user_query>...</user_query>
<skill_information>...</skill_information>
其他 context
```

Query-last 模式会把其他 context 放前面，但 `<skill_information>` 仍紧跟 `<user_query>`。

这种邻接使模型容易把 Skill 与当前问题关联起来。

---

## 65. 超长 Prompt 中 Skill 的保护

当完整用户消息需要 Offload 时：

- in-band 前缀总预算约 25,000 Bytes。
- Skill Inline 单独最多保留 4,000 Bytes。
- 使用 Head + Tail 截断。
- 完整请求写入 Offload 文件，并明确要求模型读取。

所以超长 Skill 不保证全文留在首个 inline message，但完整合成请求有可恢复路径。

---

## 66. Mid-turn Interjection 也能加载 Skill

Turn 已运行时追加的消息，会复用 `build_skill_information_for_refs`。

因此：

- Turn 开始时 `/skill`。
- 运行中强制发送 `/skill`。

二者使用相同的正文读取、变量替换和 XML 包装语义。

---

## 67. 第四种激活：模型自动选择后 Read

默认的 Catalog 路径不会预先读取所有正文。

模型根据目录判断某 Skill 适用后，对 `fullPath` 调 Read。Read 结果作为 Tool Result 回到下一次采样，模型由此获得完整指令。

与用户 `/skill` 相比，这通常多一个 Tool Call Round，但节省初始上下文。

---

## 68. Read Tool 对 Skill 文件的特殊待遇

`grok_build/read_file/mod.rs::is_skill_markdown` 判定：

1. 文件名恰好为 `SKILL.md`；或
2. `.md` 文件路径中存在精确的 `skills` 组件。

这类文件：

- 忽略模型传入的 offset/limit。
- 跳过通常的 25K Token 上限。
- 整份读取。

目的就是避免 Skill 指令或其 Markdown Reference 被静默截断。

---

## 69. Read 特例比发现目录更宽

发现只认特定配置根下的 Skill。

Read 的“Skill Markdown”判断更宽：任意精确名为 `SKILL.md` 的文件，或路径含 `skills` 组件的 Markdown 都可整份读。

这允许插件、Bundled、自定义路径和引用文档共享完整读取语义。

---

## 70. Skill 正文不是代码执行

`build_skill_message` 注释明确：`<skill>` 内是 Additional Instructions，不是某种脚本 VM。

Skill 本身通过文本指导模型：

- 应读取哪些文件。
- 应调用哪些 Tool。
- 应遵循什么工作流。

真正的副作用仍由 Tool 层执行，并受权限、Hooks、Sandbox 等控制。

---

## 71. `SkillManager` 的定位

文件：

```text
crates/codegen/xai-grok-tools/src/types/skill_discovery_tracker/mod.rs
```

它旧语义类似 Discovery Tracker，但现在管理完整生命周期：

- Startup Baseline。
- Dynamic Discoveries。
- Conditional Skills。
- Runtime Projection。
- Announcement Dedup。
- Compaction 与 Clear Reset。

SessionActor 不直接拥有这些 Skill 状态。

---

## 72. `SkillManager` 的主要状态

```text
startup_skills               启动基线
discovered_skills            本 Session 动态发现
discovered_canonical_paths   动态路径去重
checked_dirs                 已扫描目录缓存
announced_names              已公告名称
conditional                  paths: 条件 Skill
pending                      待对账状态
```

另有 cwd、git_root、预算、Tool 展示名和路径改写信息。

---

## 73. Seed 阶段

Tool Registry Finalize 时把 `SessionContext.skills` 写进：

1. `AvailableSkills`。
2. 新建的 `SkillManager` Startup Baseline。
3. `discovery_snapshot_names`。

`SkillManager::seed` 会把无条件 Skill 放入 startup list，把 `paths:` Skill 放进 Conditional 容器。

---

## 74. 首次 Baseline 公告

Fresh Session 且存在 Skill 时：

```text
pending = BaselineChange
```

Shell 调用 `apply_pending_skill_update` 后得到 `SkillUpdateEffects`：

- 可注入 `<system-reminder>`。
- 可刷新 Slash Commands。
- 标记更新类型是 BaselineChange。

System Prompt 本身不会因 Skill 动态变化而被原地修改。

---

## 75. Resume 避免重复公告

`announced_names` 可以从持久化状态恢复，并且必须在 `seed` 前完成。

若恢复集合非空，`seed` 不再设置首次 Baseline Pending，因为历史 Conversation 已含此前目录公告。

这避免 Resume 后模型重复看到同一批 Skill。

---

## 76. 两种 Projection

SkillManager 至少形成两个不同投影：

1. **Runtime Skills**：写入 `AvailableSkills`，动态发现优先，并按 canonical path 去重。
2. **Slash Skills**：启动 + 动态，按 canonical path 和 dedup key 去重，用于命令展示与解析。

公告列表还会进一步过滤 disabled、model-disabled 和不合格插件项。

不要假设“一个列表服务所有消费者”。

---

## 77. `apply_pending_skill_update`

ToolBridge 是 Session 与 SkillManager 的边界：

```text
SkillManager.take_pending()
        |
        +--> runtime_skills
        +--> SkillUpdateEffects
```

Bridge 在 Resources Lock 内把 runtime projection 写入 `AvailableSkills`，只把需要 Session 执行的副作用返回给 Shell。

这避免 SessionActor 再复制一份 Skill 权威状态。

---

## 78. 动态发现的触发器

`SkillDiscoveryReminder` 是跨工具 Reminder。

它能从成功输出中提取路径：

- ReadFile。
- ListDir。
- SearchReplace。
- ApplyPatch 用于条件 Skill 激活。

Bash/Grep 路径不用于激活，因为输出路径不可可靠解析或可能只是偶然文本。

---

## 79. 动态发现的两项工作

每次相关工具完成后：

1. 用触碰文件激活已有 `paths:` 条件 Skill。
2. 从代表性访问路径向上寻找此前未知的配置目录。

这两件事不同：前者激活启动时已经知道的 Skill；后者找到启动时根本没扫描到的 Skill。

---

## 80. 直接读写 `SKILL.md` 的快速路径

若 Tool 直接访问一个位于支持目录中的 `SKILL.md`：

1. 根据 cwd/Git Root 推导 Scope。
2. 直接 `parse_skill_files`。
3. `add_discovered`。

这样即使 User Scope Skill 位于 Git Root 外，也能立即进入 Session。

---

## 81. 普通动态向上扫描

`discover_skills_for_paths` 从访问路径所在目录向 cwd 上行，但 cwd 本身不重复扫描。

若有 Git Root，则越界即停止。

每层检查：

```text
.grok/skills
.agents/skills
.claude/skills   # compat 开启时
```

结果采用 deepest-first，使离访问文件更近的 Skill 优先。

---

## 82. `checked_dirs` 是性能缓存

每次已经 stat/扫描的 canonical directory 放进 `checked_dirs`。

后续工具再访问同一区域时跳过，避免每次 Tool Call 都重复磁盘遍历。

即使没有发现 Skill，也要把 snapshot 合并回 Manager；“这里没有 Skill”同样是值得缓存的结果。

---

## 83. 为什么 I/O 在锁外执行

Reminder 先在 Resources Lock 内复制 cwd、git_root、checked_dirs、compat，然后释放锁做文件系统扫描，最后重新加锁合并结果。

若持锁执行递归 I/O，其他 Tool 需要访问 Resources 时会被无谓阻塞。

这是典型的：

```text
锁内快照 -> 锁外慢操作 -> 锁内提交
```

---

## 84. Reminder 不直接返回公告文本

`collect_reminders` 最终返回空 Vec。

它只修改 SkillManager 并设置 Pending；Session 在 Tool Call 后统一调用 `apply_pending_skill_update`，再决定如何注入 Conversation 和刷新客户端命令。

这让发现逻辑与 UI/Conversation 副作用解耦。

---

## 85. Conditional Skill 的状态流转

```text
启动发现
  |
  +-- paths=None ----------> startup_skills
  |
  +-- paths=Some(pattern) -> conditional pending
                              |
                       文件工具触碰匹配路径
                              |
                              v
                       add_discovered
                              |
                              v
                    公告 + Runtime + Slash
```

动态发现到的条件 Skill 也不会绕过 gate。

---

## 86. `paths:` 匹配触碰的是工作区文件

Conditional Matcher 以 cwd 为基准处理相对路径。

ApplyPatch 会把每个文件的原路径和 move target 都作为候选，因此重命名到相关目录也能激活 Skill。

---

## 87. 公告去重发生在预算裁剪前

Formatter 在判断某 Skill 合格后，就把 dedup key 插入 `announced_names`，然后才做预算裁剪。

因此一个 Skill 即使因极端预算没有出现在最终可见文本中，也可能已经算“公告过”。

这是节省重复公告的选择，但读调试日志时要知道 announced 不等于肉眼可见。

---

## 88. Baseline Change 与 Discovery 的公告范围

- `Discovery`：只公告动态发现集合中尚未公告的项。
- `BaselineChange`：清空 announced，合并动态 + startup，再重新公告整个有效集合。

Plugin Reload、Bundle Sync、`/clear` 会走 Baseline Change 语义。

---

## 89. Plugin Reload

Shell 的 `reload_skills_from_disk`：

1. 重新读取 Skills Config。
2. 获取 Plugin Registry Snapshot。
3. 再次执行 `list_skills_with_plugins`。
4. `update_skill_baseline`。
5. `apply_pending_skill_update`。
6. 刷新 Slash Commands。

动态发现列表会保留，Startup Baseline 被替换。

---

## 90. Compaction 的 Skill 语义

`on_compaction`：

- 清 `announced_names`。
- 清 `checked_dirs`。
- 不清 `discovered_skills`。

压缩可能从模型上下文中移除了旧公告，因此允许后续重新公告；同时保留已发现 Skill 作为压缩后的继续工作基础。

---

## 91. `/clear` 的 Skill 语义

`on_clear`：

- 清动态 Skill。
- 清动态 canonical paths。
- 清 checked dirs。
- 清 announced names。
- 重新隐藏 Conditional Skills。
- 保留并重建 Startup Baseline。

它比 Compaction 更接近 Session 内 Skill 状态的完整重置。

---

## 92. Concise Mode 的当前限制

源码注释指出：当 `SystemRemindersEnabled(false)` 时，V1 的动态 Skill Discovery 也被禁用。

原因不是扫描本身不能工作，而是动态发现暂时耦合在 Reminder Delivery 机制上。

这是架构限制，不应误解成“Concise Mode 只是不显示提示文本”。

---

## 93. User Slash 与 Model Auto-Invoke 是正交开关

可以画成二维表：

| `user_invocable` | `disable_model_invocation` | 用户 `/skill` | 模型目录选择 |
| --- | --- | --- | --- |
| true | false | 可以 | 可以 |
| true | true | 可以 | 不公告 |
| false | false | 不作为用户 Skill 命令 | 可以公告 |
| false | true | 两者均不可 | 两者均不可 |

再加 `enabled=false` 时整体停用。

具体消费者各自过滤字段，因此修改此处必须同时检查 Slash Catalog、Listing Formatter 与 `AvailableSkills`。

---

## 94. 名称解析不是全文检索

Slash 路径是 Catalog Lookup：

```text
typed word -> bare/qualified command identity -> SkillInfo
```

它不搜索正文，也不做模糊语义匹配。

UI 的模糊补全可以帮助用户找到候选，但提交后执行仍必须落到有效 Catalog Entry。

---

## 95. 模型选择也不是自动执行函数

模型看到目录后，是普通推理的一部分：

1. 判断描述与用户任务是否匹配。
2. 读取全文。
3. 按全文指令继续调用 Tool。

所谓 auto-invoke，在这条 Grok Build Catalog 路径里主要意味着“允许向模型公开并由模型自行选择”，不是后台规则引擎直接运行 Skill。

---

## 96. 一个最小例子的完整旅程

磁盘：

```markdown
---
name: review
description: Review a code change for correctness.
when-to-use: User asks for a code review.
argument-hint: path or commit
---

Read the diff first.
Then inspect tests related to $ARGUMENTS.
```

启动时：

```text
read_frontmatter_only
-> parse_skill_frontmatter
-> SkillInfo { body: None }
-> dedupe/sort
-> <agent_skill fullPath="...">...</agent_skill>
```

用户输入：

```text
/review src/auth.rs
```

Turn 前：

```text
parse_skill_references
-> load_skill_content
-> $ARGUMENTS = src/auth.rs
-> <skill_information>
-> ParsedPrompt
-> Conversation Request
```

---

## 97. 模型自动选择同一 Skill 的旅程

用户只写：

```text
帮我审查 src/auth.rs 的改动
```

模型已经在第一用户前缀看到 `review` 的摘要和绝对路径。

它可能执行：

```text
Read(/.../review/SKILL.md)
```

Read 返回完整正文后，下一轮再依据 Skill 读取 Diff 和测试。

这条路径与 `/review` 的结果目标相同，但时序不同。

---

## 98. 失败边界：文件消失

发现与激活之间存在时间差，`SKILL.md` 可能被删除。

Slash Expansion 时：

- 加载失败会记录 Warning。
- 该 Skill Block 被跳过。
- 若没有任何正文成功加载，返回 `None`。

发现目录不是内容快照；Path 只是延迟加载句柄。

---

## 99. 失败边界：Synthetic Product Path

Server/Product Skill 可能使用：

```text
chat-product://pdf
```

这种 path 没有本地文件。

只有 `body` 已预加载时才能返回正文；body-less synthetic path 在 Shell Expansion 中被识别并安静跳过，而不是尝试访问虚构文件。

---

## 100. 失败边界：错误 Frontmatter

策略总体是“尽量保住可用 Skill”：

- YAML 小错误：引用修复后重试。
- YAML 大错误：恢复关键标量。
- 无 Frontmatter：用目录名与正文描述。
- Name 最终仍无效：才丢弃。

这是一种面向用户扩展格式的宽容解析策略。

---

## 101. 修改发现逻辑时的风险清单

若修改目录或优先级，应同时检查：

1. `collect_skill_config_dirs`。
2. `list_skills_with_options`。
3. `discover_skills_for_paths`。
4. Watcher/Reload 使用方。
5. `dedupe_skills`。
6. Slash 命令刷新。

启动和运行期发现若不一致，Bug 往往只在“进入子目录后”出现。

---

## 102. 修改字段解析时的风险清单

新增 Frontmatter Field 时至少检查：

1. `ParsedFrontmatter`。
2. `parse_skill_frontmatter`。
3. NoFrontmatter/YAML Error fallback 构造。
4. `SkillInfo`。
5. Serialization 默认值。
6. Listing、Slash、Tool 消费者。
7. Fixture 与兼容测试。

Rust struct 新字段会帮助暴露漏改的构造点，但 serde 默认语义仍需人工验证。

---

## 103. 修改加载逻辑时的风险清单

完整正文有四条入口需要保持一致：

- 用户 Slash Expansion。
- Mid-turn Interjection。
- Model Read Tool。
- Agent Definition Preload。

正文剥离、内部链接、变量替换、XML Envelope 的共享程度不同，改动前要明确目标路径。

---

## 104. 为什么测试很多

Skill 子系统的复杂度主要来自组合：

```text
来源 × Scope × 同名 × 同路径 × Plugin × Compat × 条件路径 × 可调用开关
```

单个函数看似只是遍历 Vec，但一个小改动可能改变优先级、Slash 身份或 Resume 公告。

因此这里的测试不是重复，而是在固定组合不变量。

---

## 105. `prompt/skills.rs` 测试群

重点覆盖：

- Local/Repo/User/Server/Bundled 优先级。
- Flat/Nested/Mixed 发现。
- 递归深度限制。
- Config Paths、Scope 与来源 stamp。
- Workspace User Dedup。
- Ignore 与 Disabled。
- Gitignored Skill 仍加载。
- Skill 优先于 Command。
- Plugin Identity 与 Qualified Name。
- Same-scope 复制目录 re-key。
- Frontmatter owner 驱逐 stale claimant。

---

## 106. `discovery.rs` 测试群

重点覆盖：

- Name 规范化与校验。
- YAML 容错恢复。
- Description fallback。
- UTF-8 截断安全。
- Allowed Tools 字符串/数组格式。
- Metadata、License、Compatibility。
- Boolean 严格语义。
- Vendor Builtin 过滤。
- 动态发现 Vendor Gate。
- 确定性遍历顺序。

---

## 107. `skill.rs` 测试群

重点覆盖：

- Frontmatter 剥离。
- Preloaded Body 信任。
- Synthetic Path 拒绝。
- `$ARGUMENTS`、Indexed Args、`$N`。
- Skill/Session/Plugin 路径变量。
- 兼容别名。
- 未消费参数的后缀。
- Internal Link Resolution。
- `<skill>` 与 `<skill_information>` 格式。

---

## 108. `SkillManager` 测试群

重点覆盖：

- Baseline Seed 与首次 Pending。
- Resume Announcement Restore。
- Dynamic Discovery Dedup。
- Runtime/Slash Projection。
- Conditional Activation。
- Budget 与 XML 格式。
- Baseline Replacement。
- Compaction/Clear Reset。
- Overlay Path Rewrite。

---

## 109. 推荐断点顺序

如果要动态调试一个 `/review`：

1. `AgentBuilder::build`：检查 `skill_info`。
2. `list_skills_with_plugins`：确认来源和 Scope。
3. `parse_skill_references_with_catalog`：确认命中 Path。
4. `build_skill_information_for_refs`：确认正文和 args。
5. `parse_prompt_with_skills`：确认 `skill_information` 保留。
6. `ParsedPrompt::assemble_message`：确认最终相对顺序。

---

## 110. 推荐日志字段

排查时至少记录：

```text
skill.name
skill.path
skill.scope
skill.plugin_name
skill.enabled
user_invocable
disable_model_invocation
discovery source
activation trigger
```

只打印 name 很难解释到底命中了 Local、User 还是 Plugin 版本。

---

## 111. 阅读练习一：预测冲突结果

准备：

```text
repo/sub/.grok/skills/deploy/SKILL.md
repo/.grok/skills/deploy/SKILL.md
~/.grok/skills/deploy/SKILL.md
plugin-x/skills/deploy/SKILL.md
```

问题：

1. bare `/deploy` 指向谁？
2. Plugin 如何调用？
3. 若 Local 被 disabled，Repo 是否自动接管 bare name？

第三问尤其值得用测试验证：Disabled 是去重后标记，不等同于发现前移除，不能凭直觉作答。

---

## 112. 阅读练习二：观察两阶段加载

建立一个正文很长的 Skill，分别观察：

1. 启动后首轮 Prompt 中是否只有摘要。
2. `/skill args` 时是否直接出现 `<skill_information>`。
3. 模型 Read 时是否忽略 offset/limit。
4. 引用的 `references/foo.md` 是否被自动拼入正文。

第四项预期为否：链接会绝对化，但内容仍需另一次读取。

---

## 113. 阅读练习三：Conditional Skill

给 Skill 添加：

```yaml
paths:
  - src/payments/**
```

依次测试：

1. Session 启动时是否公告。
2. Read 无关文件是否激活。
3. Read `src/payments/mod.rs` 是否激活。
4. 再次访问是否重复公告。
5. Compaction 后再次访问是否允许重公告。

---

## 114. 一个可复用的心智模型

把 Skill 看成三层对象：

| 层 | 内容 | 生命周期 |
| --- | --- | --- |
| Registry Record | `SkillInfo` 元数据与 Path | Session 长期 |
| Catalog Entry | 预算化摘要与触发条件 | 首轮/动态公告/压缩后 |
| Activated Instructions | 完整正文与当前参数 | 某个 Agent 或某个 Turn |

许多困惑来自把这三层都叫“Skill 已加载”。

---

## 115. 设计收益

这套拆分带来：

- 不把所有 Skill 正文塞进 Context。
- Local/Repo/User 覆盖关系清晰。
- Slash 用户操作可零额外模型轮次。
- 模型仍能按语义自动选择。
- 进入陌生子目录后可增量发现。
- Plugin 可保留 qualified identity。
- Resume/Compaction 不会无限重复公告。

---

## 116. 设计代价

相应代价是：

- 同一 Skill 存在多个投影。
- 启动与动态发现必须保持规则同步。
- Name/Path/Scope/Plugin 冲突算法复杂。
- 可见性开关由多个消费者分别过滤。
- 发现与激活之间存在文件变化竞态。
- “自动调用”容易被误解成独立规则引擎。

---

## 117. 本篇 Glossary

| 名词 | 白话解释 | 本篇对应物 |
| --- | --- | --- |
| Skill | 给 Agent 的可复用工作说明 | 一个 `SKILL.md` 及相关资源 |
| SKILL.md | Skill 的入口文件 | YAML Frontmatter + Markdown Body |
| Frontmatter | Markdown 开头的 YAML 元数据 | Name、Description、Trigger 等 |
| Body | Frontmatter 后的完整指令正文 | 激活时才完整加载 |
| Discovery | 找到 Skill 并生成元数据 | 固定目录扫描或动态扫描 |
| Activation | 把完整 Skill 指令交给模型 | Slash、Read、Preload |
| Progressive Disclosure | 先给摘要，需要时再给全文 | Catalog → Read/Expansion |
| SkillInfo | Skill 的运行时元数据记录 | Name、Path、Scope、Flags、Body |
| Scope | 来源优先级分类 | Local/Repo/User/Server/Bundled/Plugin |
| Canonical Path | 消除符号链接等差异后的路径 | 文件身份去重依据 |
| Bare Name | 不带来源前缀的调用名 | `deploy` |
| Qualified Name | 带 Scope/Plugin 前缀的名称 | `user:deploy`、`foo:deploy` |
| Display Name | 给人看的名称 | 冲突 re-key 后保留原标签 |
| Dedup Key | 判定身份冲突的键 | Native Name 或 `plugin:name` |
| Shadow | 高优先级同名项遮住低优先级项 | Local 覆盖 User |
| Re-key | 改用目录名作为调用身份 | 复制 Skill 目录的恢复策略 |
| Catalog | 模型或 UI 看到的 Skill 摘要目录 | `<agent_skill>` Rows |
| Semantic Selection | 模型根据描述判断适用性 | 不是独立向量搜索服务 |
| Slash Catalog | `/name` 到命令对象的注册表 | `EffectiveCommandCatalog` |
| Zero-round-trip | 不先让模型调用 Tool 就注入正文 | 用户 `/skill` 路径 |
| Skill Information | 当前 Turn 已激活 Skill 的包络 | `<skill_information>` |
| Preload | Agent 构建时提前加载正文 | Agent Definition `skills:` |
| Conditional Skill | 触碰匹配文件后才公告的 Skill | Frontmatter `paths:` |
| Dynamic Discovery | Session 中访问新路径后增量找 Skill | `SkillDiscoveryReminder` |
| Baseline | Session 启动时已知的 Skill 集 | `startup_skills` |
| Projection | 从权威状态导出的用途专属列表 | Runtime/Slash/Listing |
| Pending Reconciliation | 状态变化后等待统一对账 | `PendingKind` |
| Announcement Dedup | 防止重复告诉模型同一 Skill | `announced_names` |
| Compaction | 用摘要替换旧 Conversation 历史 | 会清公告与扫描缓存 |
| Synthetic Path | 非本地文件的逻辑地址 | `chat-product://...` |
| Internal Link Resolution | 把 Skill 内相对链接改成绝对路径 | 不自动读取引用内容 |
| Compat | 兼容其他 Agent 产品的目录/变量 | `.claude`、`.cursor`、别名 |
| Choke Point | 多条路径共用的单一入口 | `parse_skill_files` |

---

## 118. 源码定位表

| 主题 | 文件/符号 |
| --- | --- |
| 启动发现编排 | `xai-grok-agent/src/prompt/skills.rs::list_skills_with_plugins` |
| 配置目录来源 | `collect_skill_config_dirs` |
| Scope 推导 | `scope_for_config_dir` |
| Native 去重 | `dedupe_skills` |
| 冲突 re-key | `rekey_to_dir_basename` |
| Plugin Stamp | `stamp_plugin_fields` |
| Plugin 合并 | `merge_skills_with_plugins` |
| Agent 预加载 | `resolve_preloaded_skills` |
| Agent 构建接线 | `xai-grok-agent/src/builder.rs::AgentBuilder::build` |
| 元数据类型 | `xai-grok-tools/src/implementations/skills/types.rs` |
| 文件扫描与解析 | `xai-grok-tools/src/implementations/skills/discovery.rs` |
| Frontmatter 解析 | `parse_skill_frontmatter` |
| 动态路径发现 | `discover_skills_for_paths` |
| 正文读取 | `skills/skill.rs::load_skill_content` |
| 参数替换 | `apply_substitutions` |
| 内部链接 | `resolve_skill_internal_links` |
| Skill 包装 | `build_skill_message` / `build_skill_information` |
| 生命周期状态 | `types/skill_discovery_tracker/mod.rs::SkillManager` |
| 公告预算 | `skill_discovery_tracker/listing.rs` |
| 动态触发器 | `reminders/skill_discovery.rs::SkillDiscoveryReminder` |
| Runtime 更新 | `bridge.rs::apply_pending_skill_update` |
| Slash 引用解析 | `xai-grok-shell/src/session/slash_commands.rs::parse_skill_references` |
| Slash 正文注入 | `build_skill_information_for_refs` |
| Prompt 接收 | `session/prompt_parser.rs::parse_prompt_with_skills` |
| Turn 接线 | `acp_session_impl/turn.rs` |
| Read 完整文件特例 | `grok_build/read_file/mod.rs::is_skill_markdown` |

---

## 119. 验证命令

```sh
# 启动发现、优先级、去重、插件与预加载
cargo test -p xai-grok-agent 'prompt::skills::tests' --lib -- --test-threads=1

# Frontmatter、正文、链接、变量与动态发现
cargo test -p xai-grok-tools skills --lib -- --test-threads=1

# SkillManager 与公告生命周期
cargo test -p xai-grok-tools skill_discovery --lib -- --test-threads=1

# Slash 解析与 skill_information
cargo test -p xai-grok-shell slash_commands --lib -- --test-threads=1

# 完整读取 SKILL.md 的 Read Tool 特例
cargo test -p xai-grok-tools skill_file --lib -- --test-threads=1
```

---

## 120. 一句话总结

Grok Build 的 Skill 机制不是“扫描到文件就把全文塞进 System Prompt”，而是先把多来源文件规范化为有优先级、可去重、可动态更新的 `SkillInfo` 目录，再由 Agent 预加载、用户 Slash、模型 Read 或路径条件激活把完整 `SKILL.md` 精确送进需要它的 Agent 或 Turn。
