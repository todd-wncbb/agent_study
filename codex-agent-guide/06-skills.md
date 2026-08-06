# 06. Skill：发现、选择与按需加载

## 1. Skill 的本质

Codex Skill 是一份给模型阅读的工作流资源，入口通常是带 metadata 的 `SKILL.md`。它可以说明：

-什么时候应该使用这套工作流；
-执行任务的步骤；
-必须阅读哪些参考材料；
-优先使用哪些脚本、模板或工具；
-如何验证最终产物。

Skill 本身通常不直接执行代码。它通过改变模型上下文，让模型更可靠地组合 Tool。

## 2. 两阶段加载

Codex 不把所有 `SKILL.md` 正文一次性塞给模型，而采用：

```text
发现阶段：读取 metadata，生成有预算的 Skill catalog
选择阶段：用户或模型选择后，读取完整正文并注入当前 Turn
```

原因很直接：如果有 100 个 Skill，每个 2,000 tokens，完整注入根本不可行，而且大部分内容与当前任务无关。

## 3. 数据模型

Host Skill 的核心元数据由 [`SkillMetadata`](../codex-rs/core-skills/src/model.rs) 表示，包含：

- name 和 description；
- `SKILL.md` 路径；
- scope；
- interface/display metadata；
- dependencies；
- implicit invocation policy；
- Plugin 归属等。

`SkillLoadOutcome` 除成功 Skill 外还保存：

-解析错误；
-禁用路径；
-Skill roots 与来源映射；
-每个 Skill 对应的文件系统；
-用于识别 Skill 脚本或文档访问的索引。

## 4. Skill 来源

当前架构支持多种 authority：

### Host

主进程可访问的用户、仓库、系统和 Plugin Skill。它们可以来自多个配置 root，并受 scope、product policy 和 enable/disable 规则过滤。

### Executor

属于具体执行环境的 Skill。它们不能被假装成本机路径，必须通过对应 Executor filesystem 发现和读取。

### Orchestrator

通过外部资源协议暴露的 Skill，常使用 `skill://...` 形式定位。读取时必须保留 authority 和 package identity。

这条安全原则非常重要：**由哪个 authority 发现的资源，就通过哪个 authority 读取。**

## 5. 发现和解析

Loader 会在配置的 Skill roots 中查找 `SKILL.md`，读取 front matter 并构造 metadata。真实实现还处理：

-目录深度和条目上限；
-隐藏目录和符号链接策略；
-并发扫描；
-同一路径去重；
-Plugin namespace；
-额外的 interface/dependency metadata；
-解析失败时的 bounded warnings。

主要源码：

- [`core-skills/src/loader.rs`](../codex-rs/core-skills/src/loader.rs)
- [`core-skills/src/loader/discovery.rs`](../codex-rs/core-skills/src/loader/discovery.rs)
- [`core-skills/src/root_loader.rs`](../codex-rs/core-skills/src/root_loader.rs)
- [`core-skills/src/service.rs`](../codex-rs/core-skills/src/service.rs)

## 6. Catalog 如何给模型看

Skill extension 将 metadata 渲染为有预算的目录，大致如下：

```text
### Available skills
- pdf: Read, create, inspect and verify PDF files. (file: r1/pdf/SKILL.md)
- code-review: Run a final code review. (file: r0/code-review/SKILL.md)
```

目录还附带使用规则，告诉模型：

-什么情况下必须使用 Skill；
-选择后要完整读取 `SKILL.md`；
-相对引用如何解析；
-不要无边界读取整个 references 目录；
-Skill 不可用时怎样 fallback。

为减少绝对路径重复，Host catalog 可以用 `r0` 等 root alias。目录渲染和预算逻辑位于 [`ext/skills/src/render.rs`](../codex-rs/ext/skills/src/render.rs) 与 [`catalog_prompt.rs`](../codex-rs/ext/skills/src/catalog_prompt.rs)。

## 7. 显式选择

当前最确定的选择方式是显式 mention：

-结构化 `UserInput::Skill`；
-带 `skill://` 路径的 Mention；
-文本中的 `$skill-name`；
-可解析到唯一 Skill 的名称或路径。

[`collect_explicit_skill_mentions()`](../codex-rs/core-skills/src/injection.rs) 会：

1. 优先解析结构化选择；
2. 跳过禁用路径；
3. 解析文本 mention；
4. 避免同名歧义；
5. 按稳定顺序去重。

Skill extension 对跨 authority catalog 有对应的选择逻辑，位于 [`ext/skills/src/selection.rs`](../codex-rs/ext/skills/src/selection.rs)。

## 8. 隐式和动态选择

并非所有 Skill 都允许模型根据描述自动选择。Policy 可以限制 implicit invocation，避免敏感或高成本工作流被意外触发。

仓库还包含动态 Skill selector，使用 lexical、字段权重、字符 n-gram 和 RRF 等策略，对用户输入与 Skill routing metadata 做相关性排序。阅读时必须区分：

-模型基于 catalog description 自己决定使用某 Skill；
-Runtime 显式 mention 解析；
-动态 selector 或 shadow experiment 给出的候选。

这些路径的配置和实验状态可能不同，不能把“代码存在”直接等同于“所有产品默认启用”。主要源码位于 [`ext/skills/src/dynamic_skill_selector`](../codex-rs/ext/skills/src/dynamic_skill_selector) 与 [`shadow_selection_experiment.rs`](../codex-rs/ext/skills/src/shadow_selection_experiment.rs)。

## 9. 正文读取和注入

被选中的 Skill 通过其 Provider 读取 main prompt：

```text
selected catalog entry
  → provider.read_main_prompt()
  → 限制单个正文大小
  → 构造 SkillInstructions
  → 作为 ContextualUserFragment 注入 Turn
```

Host 旧路径中的 `build_skill_injections()` 也会读取对应文件系统，并生成 `{name, path, contents}`。扩展迁移期间使用 `InjectedHostSkillPrompts` 避免同一正文被新旧路径重复注入。

## 10. `skills.list` 与 `skills.read`

对于模型不能直接用本地文件工具读取的 Executor/Orchestrator Skill，Skills extension 可以暴露专用工具：

- `skills.list`：列出指定 authority 下可用 package；
- `skills.read`：按 authority、package 和 resource ID 读取内容。

工具实现位于：

- [`ext/skills/src/tools/list.rs`](../codex-rs/ext/skills/src/tools/list.rs)
- [`ext/skills/src/tools/read.rs`](../codex-rs/ext/skills/src/tools/read.rs)

它们保证远程资源不会被错误转换成本机文件路径。

## 11. Skill 引用其他资源

正文可能引用：

```text
scripts/render.py
references/api.md
assets/template.pptx
```

正确读取规则是：

1. 相对路径相对于 Skill package；
2. 使用与 main prompt 相同的 authority/provider；
3. 按 `SKILL.md` 的路由说明只读任务需要的材料；
4. 保持单项和总上下文预算；
5. 脚本优先复用，不把大段代码重新抄进 Prompt。

## 12. 一个完整例子

用户说：

> 用 `$pdf` Skill 检查 report.pdf 的页面布局。

流程是：

```text
输入解析出 pdf mention
  → 在 enabled catalog 中唯一定位
  → 读取 pdf/SKILL.md
  → 注入 SkillInstructions
  → Skill 指示先使用 PDF 渲染工具
  → 模型调用对应 Tool
  → Runtime 执行并返回页面图像
  → 模型按 Skill 的验证步骤检查
```

## 13. 设计经验

自己实现 Skill 系统时建议：

-只在全局上下文放 metadata；
-正文按需加载；
-明确来源 authority；
-每项和总目录都有硬预算；
-选择记录进入 telemetry；
-显式选择优先于隐式猜测；
-Skill 只提供方法，不绕过 Tool 权限；
-加载失败应可见，但通常允许 Agent 采用普通方法继续。

