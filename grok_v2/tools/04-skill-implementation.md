# Skill 发现、加载与 OpenCode `skill` Tool

## 结论

默认 GrokBuild 没有模型可调用的 `skill` Tool。它把 Skill 的 description、when-to-use 和
`SKILL.md` 绝对路径告诉 LLM，模型需要时直接调用 `read_file`。仓库里的专用 `skill` Tool
属于 OpenCode namespace。

因此有四条正文加载路径：

| 场景 | 谁加载 | 方式 |
| --- | --- | --- |
| 模型主动匹配 Skill | LLM | `read_file(fullPath)` |
| 用户输入 `/skill args` | Shell | 采样前 `load_skill_content()` 并注入 `<skill_information>` |
| Agent Definition 声明 `skills:` | AgentBuilder | 构建时 `load_skill_with_body()` 注入 prompt body |
| OpenCode toolset | LLM | 调用 `OpenCode:skill { name }` |

## 1. SkillInfo

定义：
[`skills/types.rs`](../../crates/codegen/xai-grok-tools/src/implementations/skills/types.rs)。

主要字段：

- 身份：`name`、`display_name`、`path`、`scope`。
- 匹配：`description`、`when_to_use`、`short_description`、`paths`。
- 调用控制：`enabled`、`user_invocable`、`disable_model_invocation`。
- 执行提示：`allowed_tools`、`model`、`effort`、`argument_hint`。
- 插件：`plugin_name/version/root/data`、`config_source`。
- 正文：`body`，常规发现为 None，仅预加载时填充。

Scope 优先级：

```text
Local > Repo > User > Server > Bundled > Plugin
```

插件 dedup key 为 `plugin:name`；普通 Skill 为 bare name。

## 2. 启动发现

总入口：
[`list_skills_with_plugins`](../../crates/codegen/xai-grok-agent/src/prompt/skills.rs)。

从 cwd 向 Git Root 扫描 `.grok/.agents`，按 compat 选择 `.claude/.cursor`；再加入用户目录、
`skills.paths`、server/bundled 目录和插件贡献。标准目录递归查找 `skills/**/SKILL.md`，旧式
`commands/*.md` 只扫一层。扫描不服从 `.gitignore`，隐藏使用 `[skills].ignore`。

同名冲突先按 scope；同 scope 的复制目录可能把失败方 re-key 为目录 basename，并把
frontmatter name 放到 `display_name`。插件裸名称不覆盖 native，但保留 qualified name。

## 3. Frontmatter 解析

入口：
[`parse_skill_files`](../../crates/codegen/xai-grok-tools/src/implementations/skills/discovery.rs)。

限制：name 64、description 1024、frontmatter 4096 bytes、正文 fallback peek 2048 bytes、
递归深度 5。name 被规范成小写 `[a-z0-9-]` slug。

YAML 失败时先尝试给问题 scalar 加引号，再逐行恢复 name/description/when-to-use。没有
description 时，从正文第一个顶层 prose paragraph 推导；跳过表格、列表、代码、blockquote
和图片 alt，最后才回退 heading/name。

普通解析只保留元数据，不保存完整正文。

## 4. 传给 LLM 的 Skill 目录

目录 formatter：
[`skill_discovery_tracker/listing.rs`](../../crates/codegen/xai-grok-tools/src/types/skill_discovery_tracker/listing.rs)。

典型 XML：

```xml
<agent_skills>
  <available_skills description="Use the Read tool with the provided absolute path to fetch full contents.">
    <agent_skill fullPath="/repo/.grok/skills/review/SKILL.md">
      Review changes — Use when: reviewing a diff or PR
    </agent_skill>
  </available_skills>
</agent_skills>
```

它过滤 disabled 和 `disable_model_invocation`，插件 Skill 没有作者明确写的 description 或
when-to-use 时，在 Grok budgeted listing 中不展示。单条 description + trigger 最多 400
bytes，总量超预算后依次降级为缩短描述、只留名称、丢弃尾部并提示剩余来源目录。

普通生命周期用 synthetic system-reminder 携带 Listing；自定义兼容模板可把它内联进首条
user message。`announced_names` 防止重复介绍，compaction 后可重建目录。

## 5. SkillManager 与动态发现

状态中心：
[`skill_discovery_tracker/mod.rs`](../../crates/codegen/xai-grok-tools/src/types/skill_discovery_tracker/mod.rs)。

它维护 startup/discovered/conditional skills、canonical path 去重、checked dirs、已宣布名称和
pending reconciliation。`take_pending()` 计算运行时 `AvailableSkills`，同时返回需要 Session
执行的 reminder 和 slash-command refresh。

`SkillDiscoveryReminder` 在 read/list/edit 后从被访问目录向 cwd/Git Root 上行，查找新的
`.grok/.agents/.claude` Skill；checked dirs 避免重复 stat。直接读写受支持目录内的
`SKILL.md` 会立即注册。`.cursor` 当前只参加启动发现，不参加动态上行扫描。

`paths:` 使用 gitignore-style matcher。条件 Skill 在匹配文件被工具触及前不展示；激活在本
session 保持，`/clear` 重新隐藏。

## 6. 默认模型加载：`read_file`

默认 Toolset 没有 GrokBuild Skill Tool。Listing 的 instruction 明确要求 Read fullPath，模型
调用通用 `read_file` 后，整个文件作为 tool result 进入上下文。此路径会看到 frontmatter 和
正文，与宿主内部 loader 的“只取正文”略有不同。

## 7. `/skill` 零额外轮次加载

Shell 的 slash catalog 只把当前已知 Skill 名称识别为命令，避免把 `/api`、`/tmp` 误判。
找到后按 path 获取 `SkillInfo`，调用 `load_skill_content()`，替换参数，然后构造：

```xml
<skill_information>
  <skills_referenced>
    <skill name="review" path="/.../SKILL.md"/>
  </skills_referenced>
  <skill name="review" args="src/a.rs">
    ...正文...
  </skill>
</skill_information>
```

该块紧跟 `<user_query>`，第一轮采样已经拥有完整正文，不需要模型先调用 Read。

## 8. 正文加载和替换

内部 loader：
[`skills/skill.rs`](../../crates/codegen/xai-grok-tools/src/implementations/skills/skill.rs)。

它读取文件、去掉 YAML frontmatter，并把存在且 canonical path 仍在 Skill 目录内的相对
Markdown link/image 转成绝对路径。支持替换：

```text
$ARGUMENTS       $ARGUMENTS[N]       $N
${SKILL_DIR}     ${SESSION_ID}
${GROK_PLUGIN_ROOT}  ${GROK_PLUGIN_DATA}
```

以及 Claude 兼容别名。若正文没有消费参数 token，则在尾部追加 `**ARGUMENTS:** ...`。

## 9. Agent Definition 预加载

AgentBuilder 按 bare/qualified name 解析 definition 的 `skills:`，加载正文后用 `<skill name=
description= path=>` 包装并 prepend 到 `prompt_body`。预加载 path 从普通 Listing 排除，避免
重复占用上下文。

## 10. `OpenCode:skill`

源码：
[`opencode/skill/mod.rs`](../../crates/codegen/xai-grok-tools/src/implementations/opencode/skill/mod.rs)。

输入只有 `{ name }`。它读取 Resources 中的 `AvailableSkills`，优先匹配 qualified name，再按
bare name 查找；bare name 多匹配时返回所有限定候选。成功后去掉 frontmatter，并列出 Skill
目录最多 10 个资源，返回 `<skill_content>`、base directory 和 sampled file list。

它是精确名称查找，不是 BM25/向量 Skill 搜索。

## 11. 大规模 Skill 的边界

当前机制是 Prompt Catalog，不是 Retrieval Index：目录会截断，但没有 `search_skill`。
Skill 达到数百到数千时，适合演进为：少量 Local/Repo/已激活 Skill 常驻，加
`search_skill(query, filters, limit)` 返回 Top-K，再用 `load_skill(id,args)` 加载正文。

## 12. 当前值得关注的实现细节

- Listing 默认预算常量为 context 的 50%，上限很宽，值得重新验证产品意图。
- Concise mode 关闭 reminders 时也关闭动态 Skill discovery，发现与呈现尚未解耦。
- 部分旧注释仍提到已删除的 GrokBuild dedicated Skill Tool，应以默认 `read_file` 测试为准。
- OpenCode Skill 的调用过滤与 `AvailableSkills::has_skill()` 对
  `user_invocable/disable_model_invocation` 的语义值得统一检查。
- 若 XML builder 对 description/args 等 attribute 没有统一 escape，需要防止结构破坏。

