#!/usr/bin/env python3
"""Regenerate study_docs/codegen/*.md from crate sources."""
import re
from pathlib import Path
from collections import defaultdict

CODEGEN = Path(__file__).resolve().parents[2] / "crates" / "codegen"
DOCS = Path(__file__).resolve().parent

SKIP_PARTS = ("/tests/", "_tests.rs", "/test_helpers/", "/fixtures/", "/benches/")


def skip(p: Path) -> bool:
    s = str(p)
    return any(x in s for x in SKIP_PARTS)


def doc_block(path: Path, n: int = 6) -> list[str]:
    out: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            started = False
            for line in f:
                st = line.strip()
                if st.startswith("//!") or st.startswith("///"):
                    started = True
                    t = st.lstrip("/! ").strip()
                    if t and not t.startswith("#!["):
                        out.append(t[:200])
                elif started:
                    break
                if len(out) >= n:
                    break
    except OSError:
        pass
    return out


def pub_items(path: Path, limit: int = 15) -> list[str]:
    try:
        t = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    items: list[str] = []
    for pat, k in [
        (r"^pub(?:\(crate\))?\s+(?:async\s+)?fn\s+(\w+)", "fn"),
        (r"^pub(?:\(crate\))?\s+struct\s+(\w+)", "struct"),
        (r"^pub(?:\(crate\))?\s+enum\s+(\w+)", "enum"),
        (r"^pub(?:\(crate\))?\s+trait\s+(\w+)", "trait"),
    ]:
        for m in re.finditer(pat, t, re.M):
            items.append(f"{k}`{m.group(1)}`")
    return items[:limit]


def line_count(path: Path) -> int:
    try:
        return sum(1 for _ in open(path, encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def cargo_info(crate_dir: Path) -> dict:
    info = {"name": crate_dir.name, "desc": "", "deps": [], "version": ""}
    cp = crate_dir / "Cargo.toml"
    if not cp.exists():
        return info
    text = cp.read_text(encoding="utf-8", errors="replace")
    for key in ("name", "version", "description"):
        m = re.search(rf'^{key}\s*=\s*"([^"]+)"', text, re.M)
        if m:
            info[key if key != "description" else "desc"] = m.group(1)
    in_deps = False
    for line in text.splitlines():
        if line.strip() == "[dependencies]":
            in_deps = True
            continue
        if in_deps and line.startswith("["):
            break
        if in_deps:
            km = re.match(r"^([a-zA-Z0-9_-]+)\s*=", line)
            if km and km.group(1).startswith("xai"):
                info["deps"].append(km.group(1))
    return info


def lib_modules(crate_src: Path) -> list[str]:
    lib = crate_src / "lib.rs"
    if not lib.exists():
        lib = crate_src / "main.rs"
    if not lib.exists():
        return []
    return re.findall(r"^pub mod (\w+)", lib.read_text(encoding="utf-8", errors="replace"), re.M)


def reverse_deps(crate_name: str) -> list[str]:
    users: list[str] = []
    for d in sorted(CODEGEN.iterdir()):
        if d.is_dir() and (d / "Cargo.toml").exists():
            if crate_name in (d / "Cargo.toml").read_text(errors="replace"):
                users.append(d.name)
    return users[:15]


def gen_tools_doc(crate_dir: Path) -> str:
    src = crate_dir / "src"
    L: list[str] = []

    def w(s: str = "") -> None:
        L.append(s)

    w("# xai-grok-tools 代码导读")
    w("")
    w("> `crates/codegen/xai-grok-tools` · 内置 agent 工具、注册表、`ToolBridge`")
    w("> 配套：[xai-grok-shell](./xai-grok-shell.md) · [05 工具权限](../05_tools_workspace_permissions.md)")
    w("")
    w("---")
    w("")
    w("## 1. 系统位置")
    w("")
    w("```text")
    w("AgentBuilder (xai-grok-agent)")
    w("  → ToolRegistryBuilder::register::<T>()")
    w("  → ToolBridge::finalize_builder(config, SessionContext)")
    w("SessionActor (xai-grok-shell)")
    w("  → WorkspaceOps::call_tool → ToolBridge::call")
    w("  → ToolRunResult → chat_state.push_tool_result")
    w("```")
    w("")
    w("| 常量 | 值 | 含义 |")
    w("| --- | ---: | --- |")
    w("| `DEFAULT_TOOL_OUTPUT_BYTES` | 40000 | 工具输出字节上限 |")
    w("| `DEFAULT_TOOL_OUTPUT_CHARS` | 20000 | bash 字符上限 |")
    w("")
    w("---")
    w("")
    w("## 2. 顶层模块")
    w("")
    for m, d in [
        ("bridge", "ToolBridge 门面：call、definitions、skill/AGENTS 种子"),
        ("registry", "ToolRegistryBuilder → FinalizedToolset"),
        ("implementations", "grok_build / codex / opencode 等工具实现"),
        ("types", "ToolKind、ToolInput/Output、Resources"),
        ("tool_taxonomy", "x.ai/tool _meta 契约"),
        ("computer", "TerminalBackend、AsyncFileSystem"),
        ("reminders", "执行后 system-reminder"),
        ("notification", "流式工具通知"),
        ("util", "截断、spawn、MCP truncate"),
        ("normalization", "参数 canonical 化"),
        ("versions", "behavior_preset"),
    ]:
        w(f"- **`{m}`** — {d}")
    w("")
    w("---")
    w("")
    w("## 3. 执行路径")
    w("")
    w("### 构建（session spawn）")
    w("1. `ToolBridge::get_builder()`")
    w("2. `register::<ReadFileTool>()` …")
    w("3. `finalize_builder(ToolServerConfig, SessionContext)`")
    w("")
    w("### 运行（tool call）")
    w("```text")
    w("dispatch_tool → call_tool → ToolBridge::call")
    w("  → FinalizedToolset::call → try_parse → Tool::execute")
    w("```")
    w("")
    w("---")
    w("")
    w("## 4. registry/types.rs 核心类型")
    w("")
    w("| 类型 | 作用 |")
    w("| --- | --- |")
    for t, d in [
        ("ToolConfig", "per-tool id、override、params、kind"),
        ("ToolServerConfig", "启用列表 + behavior_preset"),
        ("SessionContext", "terminal、fs、cwd、subagent、skills"),
        ("ToolRegistryBuilder", "register、finalize"),
        ("FinalizedToolset", "call、tool_definitions、MCP register"),
        ("register_tool_pack", "进程级外部 tool pack 扩展"),
    ]:
        w(f"| `{t}` | {d} |")
    w("")
    w("---")
    w("")
    w("## 5. ToolBridge 方法")
    w("")
    for m, d in [
        ("call / try_parse", "执行 / 仅解析"),
        ("tool_definitions / tool_definitions_builtins_only", "LLM tools 数组"),
        ("tool_for_kind / tool_kind", "按 ToolKind 查名"),
        ("render_prompt", "minijinja `${{ tools.by_kind.* }}`"),
        ("register_mcp_tools", "运行时注册 MCP"),
        ("seed_skill_discovery / seed_agents_md", "skill 与 AGENTS"),
        ("kill_foreground_commands", "取消时杀 bash"),
    ]:
        w(f"- `{m}` — {d}")
    w("")
    w("---")
    w("")
    w("## 6. ToolKind / ToolNamespace")
    w("")
    w("**Namespace：** GrokBuild · GrokBuildConcise · GrokBuildHashline · Codex · OpenCode · MCP")
    w("")
    w("**Kind（节选）：** Read · Edit · Execute · Search · ListDir · Task · WebSearch · WebFetch · Skill · EnterPlan · ExitPlan · AskUser · Workflow · Memory*")
    w("")
    w("---")
    w("")
    w("## 7. implementations/grok_build/")
    w("")
    w("| 子模块 | 说明 |")
    w("| --- | --- |")
    for name, desc in [
        ("bash", "Shell、background、block_until_ms"),
        ("read_file", "文件/图片/PDF/PPTX"),
        ("search_replace", "编辑"),
        ("grep", "ripgrep"),
        ("list_dir", "目录"),
        ("task", "子 agent"),
        ("task_output / wait_tasks", "后台输出"),
        ("kill_task", "杀任务"),
        ("web_fetch / web_search", "网络"),
        ("todo", "TodoWrite"),
        ("enter_plan_mode / exit_plan_mode", "Plan"),
        ("ask_user_question", "反向提问"),
        ("scheduler", "/loop"),
        ("monitor", "monitor 事件"),
        ("image_gen / video_gen", "媒体"),
        ("workflow", "xai-workflow"),
        ("lsp", "LSP"),
    ]:
        w(f"| `{name}/` | {desc} |")
    w("")
    w("其他 toolset：`codex/` · `opencode/` · `grok_build_concise/` · `grok_build_hashline/` · `skills/` · `memory/`")
    w("")
    w("---")
    w("")
    w("## 8. 依赖与被依赖")
    w("")
    w("依赖：xai-tool-runtime · xai-grok-sandbox · minijinja · async-lsp · reqwest")
    w("")
    w("被依赖：xai-grok-shell · xai-grok-agent · xai-grok-workspace · xai-grok-hooks · xai-grok-mcp")
    w("")
    w("---")
    w("")
    w("## 9. 全文件索引")
    w("")
    all_rs = sorted(p for p in src.rglob("*.rs") if not skip(p))
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for p in all_rs:
        by_dir[str(p.parent.relative_to(src))].append(p)
    for dname in sorted(by_dir):
        w(f"### `{dname}/`")
        w("")
        w("| 文件 | 行数 | 摘要 |")
        w("| --- | ---: | --- |")
        for p in sorted(by_dir[dname], key=lambda x: x.name):
            doc = doc_block(p, 1)
            w(f"| `{p.name}` | {line_count(p)} | {(doc[0] if doc else '—')[:65]} |")
        w("")
    w("---")
    w("")
    w("## 10. 改动指南")
    w("")
    w("| 目标 | 入口 |")
    w("| --- | --- |")
    w("| 新工具 | implementations + ToolRegistryBuilder::register |")
    w("| MCP | register_mcp_tools |")
    w("| 输出上限 | DEFAULT_TOOL_OUTPUT_* |")
    w("| 行为版本 | versions/ + behavior_preset |")
    w("")
    w(gen_tools_deep_dive(crate_dir))
    return "\n".join(L)


def gen_tools_deep_dive(crate_dir: Path) -> str:
    """Sections 11+ — code-level deep dive appended to xai-grok-tools doc."""
    L: list[str] = []

    def w(s: str = "") -> None:
        L.append(s)

    w("---")
    w("")
    w("## 11. ToolRegistryBuilder 注册机制")
    w("")
    w("`register_with_params<T, P>()`（`registry/types.rs` ~567 行）为每个工具建立 `ToolEntry`：")
    w("")
    w("```text")
    w("T::default()")
    w("  → name = \"{namespace}:{id}\"   // 如 grok_build:read_file")
    w("  → input_schema = generate_schema::<T::Args>()")
    w("  → metadata / output_converter / parse_input 闭包")
    w("  → register_in_local: LocalRegistry.register(T)")
    w("```")
    w("")
    w("类型约束（编译期）：")
    w("")
    w("- `T: xai_tool_runtime::Tool + ToolMetadata + Default`")
    w("- `T::Args: DeserializeOwned + JsonSchema + Into<ToolInput>`")
    w("- `T::Output: Serialize + DeserializeOwned + Into<ToolOutput>`")
    w("- `P: ResourceType` — 工具级配置（如 `BashParams`）存入 `Resources`")
    w("")
    w("`finalize(config, ctx)` 遍历 `ToolServerConfig` 中启用的工具名，校验 `requires_expr`，")
    w("把 `SessionContext` 注入 `Resources`，构建 `TemplateRenderer`、`FinalizedToolset`。")
    w("")
    w("---")
    w("")
    w("## 12. FinalizedToolset::call 分发链")
    w("")
    w("入口：`call` → `call_with_cancellation` → `call_streaming_with_cancellation`（~1496–1599 行）")
    w("")
    w("```text")
    w("prepare_dispatch(tool_name, tool_args, tool_call_id, cwd_override, cancellation)")
    w("  ├─ 解析 client name → ToolEntry（支持 name override）")
    w("  ├─ parse_input → ToolInput")
    w("  ├─ 组装 ToolContext（Resources + Cwd override）")
    w("  └─ LocalRegistry handle → execute(ctx, canonical_params)")
    w("         ↓ stream")
    w("finalize_output(typed_value, output_converter, effective_tool_name)")
    w("  ├─ reminders（LSP / skill / task completion）")
    w("  ├─ truncate（DEFAULT_TOOL_OUTPUT_BYTES/CHARS）")
    w("  └─ ToolRunResult { output, prompt_text }")
    w("```")
    w("")
    w("**`cwd_override`**：单次调用的工作目录覆盖，栈局部、不共享，避免并发 tool call 竞态。")
    w("")
    w("**`call_streaming`**：Progress 事件原样转发；Terminal 经 `finalize_output` 后产出 `ToolRunResult`。")
    w("")
    w("---")
    w("")
    w("## 13. ToolBridge 与 shell 边界")
    w("")
    w("`bridge.rs` 持有 `Arc<FinalizedToolset>` + 可选 `Arc<dyn TerminalBackend>`。")
    w("")
    w("**为何 terminal 单独存放？** 取消时 `kill_foreground_commands()` 需在 `call()` 持锁期间")
    w("仍能访问 PTY，避免死锁（见 `bridge.rs` 注释 Cancellation Safety）。")
    w("")
    w("```rust")
    w("// bridge.rs — finalize 后抽出 terminal")
    w("terminal = finalized_toolset.resources.lock().await.get::<Terminal>().map(|t| t.0.clone());")
    w("```")
    w("")
    w("**`ToolBridgeResult`**：`output`（JSON/ACP）+ `prompt_text`（含 system-reminder，喂给模型）。")
    w("")
    w("Shell 调用链（`tool_dispatch.rs`）：")
    w("")
    w("```text")
    w("SessionActor::execute_tool_calls")
    w("  → dispatch_tool(workspace_ops, prepared, session_id)")
    w("  → WorkspaceOps::call_tool(name, args, call_id, Some(session_id))")
    w("  → workspace 内 ToolBridge::call")
    w("```")
    w("")
    w("同文件还有：`lock_path_for_args`（同文件编辑串行化）、`build_tool_parse_error_message`（参数 JSON 修复提示）。")
    w("")
    w("---")
    w("")
    w("## 14. SessionContext 与 Resources")
    w("")
    w("`SessionContext`（finalize 时传入）典型字段：")
    w("")
    w("| 资源 | 用途 |")
    w("| --- | --- |")
    w("| `Terminal` / `TerminalBackend` | bash、前台命令 |")
    w("| `AsyncFileSystem` | read/write/grep 路径访问 |")
    w("| `Cwd` | 默认工作目录 |")
    w("| `OwnerSessionId` | 子 agent / task 归属 |")
    w("| `State` | 会话可变状态（todo、plan mode） |")
    w("| `TemplateRenderer` | prompt 占位符、kind→name |")
    w("| `AgentsMdTracker` / skill trackers | AGENTS.md、skill 发现 |")
    w("")
    w("`types/resources.rs`（~1652 行）实现类型安全的异构容器：`insert/get/register_params`。")
    w("工具通过 `ToolContext` 在 `execute` 时读取，而非全局静态。")
    w("")
    w("---")
    w("")
    w("## 15. AgentBuilder 如何注册工具")
    w("")
    w("`xai-grok-agent/src/builder.rs` 在 `build()` 中：")
    w("")
    w("1. 根据 `AgentDefinition.tools` / preset 解析工具名列表")
    w("2. `ToolBridge::get_builder()` + 按 toolset 调用 `register_grok_build_tools` 等")
    w("3. `ToolServerConfig { enabled_tools, behavior_preset, ... }`")
    w("4. `ToolBridge::finalize_builder(builder, config, session_ctx).await`")
    w("5. 产物挂到 `Agent { tool_bridge, ... }`")
    w("")
    w("vendor 兼容：`claude_tool_kind(name)` → `xai_grok_tools::types::kind_for(name)`，")
    w("让 Claude Code 风格 `tools:` 白名单映射到 `ToolKind`。")
    w("")
    w("---")
    w("")
    w("## 16. 代表性工具实现")
    w("")
    w("### 16.1 Bash (`implementations/grok_build/bash/`)")
    w("")
    w("- 参数：`command`、`description`、`is_background`、`block_until_ms` / `timeout`")
    w("- 经 `TerminalBackend::run(TerminalRunRequest)` 执行")
    w("- 输出：`BashOutput` → truncate → `prompt_text` 可含 working_dir、exit_code")
    w("- Shell **bash mode** 绕过模型，直接 `terminal.run`（见 shell `tool_dispatch.rs` `handle_direct_bash_command`）")
    w("")
    w("### 16.2 Read (`read_file/`)")
    w("")
    w("- 支持文本、图片 base64、PDF/PPTX 提取")
    w("- `util/binary.rs` 跳过已知二进制扩展名")
    w("- `util/image_validate.rs` 校验 MIME/尺寸")
    w("")
    w("### 16.3 SearchReplace (`search_replace/`)")
    w("")
    w("- 结构化编辑；输出 `edit.lines` 遥测")
    w("- shell 用 `lock_path_for_args` 对同 `file_path` 串行")
    w("")
    w("### 16.4 Task (`task/`)")
    w("")
    w("- 启动子 agent（`SubagentEntry`）")
    w("- 与 `reminders/task_completion.rs`（~2014 行）配合：后台完成时注入 reminder")
    w("")
    w("### 16.5 Grep / ListDir")
    w("")
    w("- grep 封装 ripgrep；list_dir 受 workspace 权限与 ignore 规则约束")
    w("")
    w("---")
    w("")
    w("## 17. Reminder 管道")
    w("")
    w("`reminders/mod.rs` 在 `finalize_output` 之后追加 **system-reminder** 文本到 `prompt_text`：")
    w("")
    w("| 模块 | 触发 |")
    w("| --- | --- |")
    w("| `lsp_diagnostics.rs` | 文件变更后 LSP 诊断 |")
    w("| `skill_discovery.rs` | 访问路径附近发现新 skill |")
    w("| `task_completion.rs` | 子 agent / 后台 bash 完成 |")
    w("")
    w("模型看到的是 **tool_result 正文 + reminder**；`output` 字段保持「干净」供 ACP/持久化。")
    w("")
    w("---")
    w("")
    w("## 18. MCP 动态工具")
    w("")
    w("- `ToolBridge::register_mcp_tools` — session 运行时把 MCP server 工具注册进 registry")
    w("- `util/mcp_truncate.rs` — MCP 返回体大小限制")
    w("- shell：`acp_session_impl/mcp.rs` 继承共享 MCP 客户端并注册到当前 session bridge")
    w("")
    w("工具名通常带 server 前缀；`tool_definitions_builtins_only` 过滤 MCP，仅给需要内置 schema 的场景。")
    w("")
    w("---")
    w("")
    w("## 19. tool_taxonomy 与 x.ai/tool _meta")
    w("")
    w("`tool_taxonomy.rs` 定义 LLM wire 与内部 `ToolKind` 的 `_meta` 契约（`x.ai/tool`）。")
    w("Shell `stamp_tool_meta` 把 wire name + `ToolInput` 写入 ACP `ToolCall.meta`，pager 据此选渲染器。")
    w("")
    w("相关：`types/claude_alias.rs`（Claude 工具名 ↔ Grok 名）、`types/compat.rs`（第三方 agent 表面兼容）。")
    w("")
    w("---")
    w("")
    w("## 20. 输出截断与图像处理")
    w("")
    w("```text")
    w("工具原始输出")
    w("  → types/output.rs 格式化")
    w("  → util/truncate.rs（软换行、字节/字符上限）")
    w("  → util/base64_images.rs（大图从 prompt 剥离）")
    w("  → util/mcp_truncate.rs（MCP 专用）")
    w("```")
    w("")
    w("常量：`DEFAULT_TOOL_OUTPUT_BYTES=40000`、`DEFAULT_TOOL_OUTPUT_CHARS=20000`（`lib.rs` 导出）。")
    w("")
    w("---")
    w("")
    w("## 21. 外部 Tool Pack")
    w("")
    w("`register_tool_pack(fn)` 在进程级注册扩展：out-of-tree crate 可在 finalize 前向")
    w("`ToolRegistryBuilder` 贡献 `register::<T>()`。用于内部实验或插件化工具集。")
    w("")
    w("---")
    w("")
    w("## 22. 本 crate 推荐阅读顺序")
    w("")
    w("1. `lib.rs` — 模块树与常量")
    w("2. `bridge.rs` — 对外 API")
    w("3. `registry/types.rs` — `ToolRegistryBuilder` / `FinalizedToolset::call`")
    w("4. `types/tool.rs` + `tool_taxonomy.rs` — Kind/Namespace")
    w("5. `types/resources.rs` + `types/output.rs`")
    w("6. `implementations/grok_build/mod.rs` — 默认工具集注册列表")
    w("7. 选一个工具目录（如 `bash/`）对照 `xai_tool_runtime::Tool` trait")
    w("8. shell：`tool_dispatch.rs` + `xai-grok-workspace` 的 `call_tool`")
    w("")
    return "\n".join(L)


def gen_agent_doc(crate_dir: Path) -> str:
    src = crate_dir / "src"
    info = cargo_info(crate_dir)
    users = reverse_deps(info["name"])
    mods = lib_modules(src)
    all_rs = sorted(p for p in src.rglob("*.rs") if not skip(p))
    L: list[str] = []

    def w(s: str = "") -> None:
        L.append(s)

    w(f"# {info['name']} 代码导读")
    w("")
    w("> `crates/codegen/xai-grok-agent` · Agent 定义、构建器、系统 prompt 组装")
    w("> 配套：[xai-grok-tools](./xai-grok-tools.md) · [09 端到端](../09_end_to_end_request_flow.md)")
    w("")
    w("---")
    w("")
    w("## 1. 职责")
    w("")
    w("从 `xai-grok-shell` 抽出的 **可移植 Agent 对象**：工具集、system prompt、")
    w("compaction/reminder 策略、模型配置。Shell 的 `SessionActor` 持有一个 `Agent`。")
    w("")
    w("```text")
    w("AgentDefinition (.md / preset)")
    w("  → AgentBuilder::from_definition / with_*")
    w("  → ToolBridge::finalize_builder")
    w("  → Agent { tool_bridge, prompt, policies }")
    w("```")
    w("")
    w("---")
    w("")
    w("## 2. 顶层模块")
    w("")
    w("| 模块 | 文件 | 作用 |")
    w("| --- | --- | --- |")
    for m, f, d in [
        ("agent", "agent.rs", "`Agent` 结构体：tool_bridge、definition、render_system_prompt"),
        ("builder", "builder.rs", "`AgentBuilder` 流式 API，`build().await`"),
        ("config", "config/", "`AgentDefinition` 解析、preset、toolset_for_preset"),
        ("prompt", "prompt/", "PromptContext、模板、AGENTS.md 注入"),
        ("compaction", "compaction.rs", "`CompactionPolicy`"),
        ("system_reminder", "system_reminder.rs", "`ReminderPolicy`"),
        ("discovery", "discovery.rs", "子 agent / subagent 发现"),
        ("plugins", "plugins.rs", "插件 agent 加载"),
        ("repo", "repo.rs", "agent 定义仓库扫描"),
        ("timing", "timing.rs", "构建耗时指标"),
    ]:
        w(f"| `{m}` | `{f}` | {d} |")
    w("")
    w("---")
    w("")
    w("## 3. AgentBuilder 构建流程")
    w("")
    w("```text")
    w("AgentBuilder::new(cwd, prompt_cwd, notification_handle)")
    w("  .terminal_backend(arc) / .fs_backend(arc)")
    w("  .from_definition(AgentDefinition)   // 或 .with_name / .with_tools")
    w("  .build().await")
    w("    ├─ 解析 tools 列表（preset + allow/deny）")
    w("    ├─ register_*_tools on ToolRegistryBuilder")
    w("    ├─ SessionContext { terminal, fs, cwd, skills, ... }")
    w("    ├─ ToolBridge::finalize_builder")
    w("    ├─ 渲染 system prompt（minijinja + PromptContext）")
    w("    └─ Agent { ... }")
    w("```")
    w("")
    w("**`prompt_working_directory`**：fork/worktree 时向模型隐藏真实 overlay 路径，")
    w("工具执行仍用真实 `working_directory`。")
    w("")
    w("---")
    w("")
    w("## 4. AgentDefinition")
    w("")
    w("`config/` 解析 front matter + markdown body：")
    w("")
    w("- `name`、`description`、`tools`、`skills`、`permission_mode`")
    w("- `prompt_mode`：Full / Minimal 等")
    w("- builtin preset：`BuiltinAgentName`、 `toolset_for_preset`")
    w("")
    w("文件发现：`discovery` + `repo` 扫描 `.grok/agents`、`AGENTS.md` 链接。")
    w("")
    w("---")
    w("")
    w("## 5. 与 xai-grok-tools 的接口")
    w("")
    w("| Agent 侧 | Tools 侧 |")
    w("| --- | --- |")
    w("| `AgentBuilder::build` | `ToolRegistryBuilder::register` |")
    w("| `agent.tool_bridge()` | `ToolBridge` |")
    w("| `SessionContext` 字段 | `resources.rs` 类型 |")
    w("| `tool_id_eq` / `short_tool_name` | 工具 wire 名解析 |")
    w("")
    w("---")
    w("")
    w("## 6. System Prompt 组装")
    w("")
    w("`prompt/context.rs` — `PromptContext` 提供 `${{ os_name }}`、`${{ working_directory }}` 等。")
    w("`ToolBridge::render_prompt` 解析 `${{ tools.by_kind.read }}` 等工具名占位符。")
    w("")
    w("模板文件：`templates/prompt.md`（crate 内默认 system prompt 骨架）。")
    w("")
    w("---")
    w("")
    w("## 7. 源码文件索引")
    w("")
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for p in all_rs:
        by_dir[str(p.parent.relative_to(src))].append(p)
    for dname in sorted(by_dir):
        w(f"### `{dname}/`")
        w("")
        w("| 文件 | 行数 | 文档 |")
        w("| --- | ---: | --- |")
        for p in sorted(by_dir[dname], key=lambda x: x.name):
            doc = doc_block(p, 1)
            w(f"| `{p.name}` | {line_count(p)} | {(doc[0] if doc else '—')[:60]} |")
        w("")
    w("---")
    w("")
    w("## 8. 依赖与被依赖")
    w("")
    w("依赖：" + (" · ".join(f"`{d}`" for d in info["deps"]) if info["deps"] else "见 Cargo.toml"))
    w("")
    w("被依赖：" + (" · ".join(f"`{u}`" for u in users) if users else "—"))
    w("")
    w("---")
    w("")
    w("## 9. 阅读顺序")
    w("")
    w("1. `lib.rs` → `agent.rs` → `builder.rs`（`build` 函数体）")
    w("2. `config/agent_definition.rs` — 定义文件格式")
    w("3. `prompt/context.rs` + `templates/prompt.md`")
    w("4. 对照 [xai-grok-tools](./xai-grok-tools.md) 的 finalize 流程")
    w("5. shell：`session/agent.rs` 或 `SessionActor` 如何 `borrow` Agent")
    w("")
    return "\n".join(L)


def gen_workspace_doc(crate_dir: Path) -> str:
    src = crate_dir / "src"
    info = cargo_info(crate_dir)
    users = reverse_deps(info["name"])
    all_rs = sorted(p for p in src.rglob("*.rs") if not skip(p))
    L: list[str] = []

    def w(s: str = "") -> None:
        L.append(s)

    w("# xai-grok-workspace 代码导读")
    w("")
    w("> `crates/codegen/xai-grok-workspace` · 工作区 FS/VCS/权限、会话子系统、`WorkspaceOps`")
    w("> 配套：[05 工具权限](../05_tools_workspace_permissions.md) · [xai-grok-tools](./xai-grok-tools.md)")
    w("")
    w("---")
    w("")
    w("## 1. 职责")
    w("")
    w("把 **工具执行、git/worktree、权限、MCP hub、会话状态** 封装成 `WorkspaceOps` trait，")
    w("供 shell 的 `SessionActor` 通过 channel/RPC 调用（本地 in-process 或远程 workspace daemon）。")
    w("")
    w("```text")
    w("grok pager / shell")
    w("  → WorkspaceHandle / connect_local_workspace")
    w("  → WorkspaceOps::call_tool / permission / git / ...")
    w("  → session 内 ToolBridge")
    w("```")
    w("")
    w("---")
    w("")
    w("## 2. 核心模块")
    w("")
    w("| 模块 | 作用 |")
    w("| --- | --- |")
    for m, d in [
        ("workspace_ops", "`WorkspaceOp` 枚举 + `WorkspaceOps` trait（call_tool 等）"),
        ("session", "`WorkspaceSession`、会话级 tool bridge 与状态"),
        ("permission", "工具/路径权限判定、folder trust"),
        ("file_system", "抽象 FS、overlay、ignore"),
        ("worktree", "git worktree / fast-worktree 集成"),
        ("hub / hub_server", "多会话 hub、认证、channel"),
        ("mcp", "workspace 侧 MCP 配置"),
        ("handle", "`WorkspaceHandle` 连接与生命周期"),
        ("config", "`WorkspaceConfig`、`AgentSessionConfig`"),
        ("recovery", "崩溃恢复"),
    ]:
        w(f"| `{m}` | {d} |")
    w("")
    w("---")
    w("")
    w("## 3. call_tool 路径")
    w("")
    w("```text")
    w("WorkspaceOps::call_tool(tool_name, args, call_id, session_id)")
    w("  → session 查找对应 ToolBridge")
    w("  → ToolBridge::call(...).await")
    w("  → ToolRunResult 返回 shell")
    w("```")
    w("")
    w("实现见 `workspace_ops.rs` + `session/`；shell 侧入口 `tool_dispatch::dispatch_tool`。")
    w("")
    w("---")
    w("")
    w("## 4. 权限与信任")
    w("")
    w("- `permission/` — 工具调用前检查（读/写/执行/network）")
    w("- `folder_trust` / `trust` — 用户确认的目录信任")
    w("- 与 shell `SessionCommand::ApproveTool` 等命令联动")
    w("")
    w("---")
    w("")
    w("## 5. 源码文件索引")
    w("")
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for p in all_rs:
        by_dir[str(p.parent.relative_to(src))].append(p)
    for dname in sorted(by_dir):
        w(f"### `{dname}/`")
        w("")
        w("| 文件 | 行数 | 文档 |")
        w("| --- | ---: | --- |")
        for p in sorted(by_dir[dname], key=lambda x: x.name):
            doc = doc_block(p, 1)
            w(f"| `{p.name}` | {line_count(p)} | {(doc[0] if doc else '—')[:60]} |")
        w("")
    w("---")
    w("")
    w("## 6. 依赖与被依赖")
    w("")
    w("依赖：" + (" · ".join(f"`{d}`" for d in info["deps"]) if info["deps"] else "见 Cargo.toml"))
    w("")
    w("被依赖：" + (" · ".join(f"`{u}`" for u in users) if users else "—"))
    w("")
    w("---")
    w("")
    w("## 7. 阅读顺序")
    w("")
    w("1. `lib.rs` 导出表")
    w("2. `workspace_ops.rs` — 所有 RPC 操作定义")
    w("3. `session/mod.rs` — 会话如何持有 ToolBridge")
    w("4. `permission/` + `handle.rs`")
    w("5. shell 中 `workspace_ops` 字段的初始化（`session_setup.rs`）")
    w("")
    return "\n".join(L)


def all_fn_names(path: Path, limit: int = 25) -> list[str]:
    try:
        t = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return re.findall(
        r"^\s*(?:pub(?:\(crate\))?\s+)?(?:async\s+)?fn\s+(\w+)",
        t,
        re.M,
    )[:limit]


def write_per_file_guide(src: Path, w, title: str = "逐文件导读") -> None:
    """Detailed per-file section for small crates."""
    all_rs = sorted(p for p in src.rglob("*.rs") if not skip(p))
    w(f"## {title}")
    w("")
    for p in all_rs:
        rel = p.relative_to(src)
        doc = doc_block(p, 10)
        pubs = pub_items(p, 25)
        fns = all_fn_names(p, 25)
        w(f"### `{rel}` ({line_count(p)} 行)")
        w("")
        if doc:
            for line in doc:
                w(f"> {line}")
            w("")
        if pubs:
            w("**公开 API：** " + ", ".join(pubs))
            w("")
        if fns:
            w("**函数（节选）：** " + ", ".join(f"`{n}`" for n in fns))
            w("")
        w("---")
        w("")


def write_symbol_index(src: Path, w, title: str) -> None:
    """Appendix: every pub/pub(crate) symbol per file."""
    w(f"## {title}")
    w("")
    all_rs = sorted(p for p in src.rglob("*.rs") if not skip(p))
    for p in all_rs:
        rel = str(p.relative_to(src))
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        syms: list[str] = []
        for pat, prefix in [
            (r"^pub(?:\(crate\))?\s+async\s+fn\s+(\w+)", "fn"),
            (r"^pub(?:\(crate\))?\s+fn\s+(\w+)", "fn"),
            (r"^pub(?:\(crate\))?\s+struct\s+(\w+)", "struct"),
            (r"^pub(?:\(crate\))?\s+enum\s+(\w+)", "enum"),
            (r"^pub(?:\(crate\))?\s+trait\s+(\w+)", "trait"),
            (r"^pub(?:\(crate\))?\s+type\s+(\w+)", "type"),
        ]:
            for m in re.finditer(pat, t, re.M):
                syms.append(f"{prefix}`{m.group(1)}`")
        if not syms:
            continue
        w(f"### `{rel}`")
        w("")
        for i in range(0, len(syms), 6):
            w(" · ".join(syms[i : i + 6]))
        w("")
    w("---")
    w("")


def parse_enum_variants(path: Path, enum_name: str) -> list[tuple[str, str]]:
    """Return (variant_name, doc_line) from a Rust enum."""
    try:
        t = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    m = re.search(rf"pub enum {enum_name}\s*\{{", t)
    if not m:
        return []
    body = t[m.end() :]
    depth = 1
    end = 0
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    body = body[:end]
    variants: list[tuple[str, str]] = []
    doc_lines: list[str] = []
    for line in body.splitlines():
        st = line.strip()
        if st.startswith("///"):
            doc_lines.append(st.lstrip("/ ").strip())
        elif st.startswith("// ═"):
            doc_lines = []
        elif re.match(r"^[A-Z]\w*", st):
            name = re.match(r"^(\w+)", st)
            if name:
                variants.append((name.group(1), doc_lines[0] if doc_lines else ""))
                doc_lines = []
    return variants


def write_all_fn_index(directory: Path, w, title: str, root: Path) -> None:
    """List every fn in a directory tree."""
    w(f"### {title}")
    w("")
    files = sorted(p for p in directory.rglob("*.rs") if not skip(p))
    for p in files:
        rel = str(p.relative_to(root))
        fns = all_fn_names(p, 50)
        if not fns:
            continue
        w(f"**`{rel}`：** " + ", ".join(f"`{n}`" for n in fns))
        w("")
    w("")


def write_file_index(src: Path, w, include_pub: bool = False) -> None:
    """Write § file index grouped by directory."""
    all_rs = sorted(p for p in src.rglob("*.rs") if not skip(p))
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for p in all_rs:
        by_dir[str(p.parent.relative_to(src))].append(p)
    for dname in sorted(by_dir):
        w(f"### `{dname}/`")
        w("")
        if include_pub:
            w("| 文件 | 行数 | 文档 | 公开 API（节选）|")
            w("| --- | ---: | --- | --- |")
            for p in sorted(by_dir[dname], key=lambda x: x.name):
                doc = doc_block(p, 1)
                pubs = ", ".join(pub_items(p, 5))
                w(
                    f"| `{p.name}` | {line_count(p)} | "
                    f"{(doc[0] if doc else '—')[:55]} | {pubs[:70] or '—'} |"
                )
        else:
            w("| 文件 | 行数 | 摘要 |")
            w("| --- | ---: | --- |")
            for p in sorted(by_dir[dname], key=lambda x: x.name):
                doc = doc_block(p, 1)
                w(f"| `{p.name}` | {line_count(p)} | {(doc[0] if doc else '—')[:65]} |")
        w("")


def gen_chat_state_doc(crate_dir: Path) -> str:
    src = crate_dir / "src"
    info = cargo_info(crate_dir)
    users = reverse_deps(info["name"])
    L: list[str] = []

    def w(s: str = "") -> None:
        L.append(s)

    w("# xai-chat-state 代码导读")
    w("")
    w("> `crates/codegen/xai-chat-state` · 对话状态 Actor、`ConversationRequest` 组装、compaction")
    w("> 配套：[09 端到端](../09_end_to_end_request_flow.md) · [xai-grok-sampler](./xai-grok-sampler.md)")
    w("")
    w("---")
    w("")
    w("## 1. 职责与系统位置")
    w("")
    w("从 `xai-grok-shell` 的 `acp_session.rs` 抽出的 **对话状态 Actor**，管理：")
    w("")
    w("- `Vec<ConversationItem>` 历史")
    w("- `SamplingConfig`、token 计数、prompt_index")
    w("- 组装发往模型的 `ConversationRequest`（含 prune / 图片压缩 / memory 注入）")
    w("- 持久化（`ChatPersistence` trait）")
    w("")
    w("```text")
    w("SessionActor (shell)")
    w("  push_user / push_tool_result / push_assistant")
    w("       ↓ ChatStateHandle (mpsc)")
    w("  ChatStateActor (专用 tokio task，无锁 State)")
    w("       ↓ ChatStateEvent")
    w("  SessionActor 订阅事件")
    w("")
    w("  每轮推理前：")
    w("  BuildConversationRequest → ConversationRequest")
    w("       ↓")
    w("  SamplerHandle::submit")
    w("```")
    w("")
    w("---")
    w("")
    w("## 2. Actor 模式")
    w("")
    w("| 组件 | 文件 | 作用 |")
    w("| --- | --- | --- |")
    w("| `ChatStateActor` | `actor/mod.rs` | 单线程消费 `ChatStateCommand` |")
    w("| `ChatStateHandle` | `handle.rs` | 廉价 `Clone`，跨 task 发命令 |")
    w("| `ChatStateCommand` | `commands.rs` | Mutation + Query（oneshot 回复）|")
    w("| `ChatStateEvent` | `events.rs` | 状态变更通知（compaction 等）|")
    w("| `actor/state.rs` | — | `State` 结构体（conversation、tokens…）|")
    w("| `actor/mutations.rs` | — | push/replace/repair 实现 |")
    w("| `actor/queries.rs` | — | Get* 查询 |")
    w("| `actor/request_builder.rs` | — | **`build_conversation_request`** |")
    w("")
    w("与 `xai-hunk-tracker`、`xai-grok-sampler` 同一套 **Command + Handle + Actor** 模式。")
    w("")
    w("---")
    w("")
    w("## 3. ChatStateHandle 主要 API")
    w("")
    w("### 3.1 Fire-and-forget 变更")
    w("")
    w("| 方法 | 命令 | 时机 |")
    w("| --- | --- | --- |")
    for m, c, when in [
        ("`push_user_message`", "PushUserMessage", "用户 prompt / bash mode 用户块"),
        ("`push_assistant_response`", "PushAssistantResponse", "模型回复（文本+tool_calls）"),
        ("`push_tool_result`", "PushToolResult", "工具执行结果写入历史"),
        ("`record_token_usage`", "RecordTokenUsage", "流式累计 token"),
        ("`record_last_turn_usage`", "RecordLastTurnUsage", "单轮 TokenUsage 快照"),
        ("`increment_prompt_index`", "IncrementPromptIndex", "完成一轮用户 prompt"),
        ("`update_sampling_config`", "UpdateSamplingConfig", "换模型 / context_window"),
        ("`flush`", "Flush", "强制刷持久化"),
    ]:
        w(f"| {m} | {c} | {when} |")
    w("")
    w("### 3.2 需要 await 的查询 / 构建")
    w("")
    w("| 方法 | 命令 | 返回 |")
    w("| --- | --- | --- |")
    for m, c, ret in [
        ("`build_conversation_request`", "BuildConversationRequest", "`ConversationRequest`"),
        ("`get_conversation`", "GetConversation", "`Vec<ConversationItem>`"),
        ("`get_total_tokens`", "GetTotalTokens", "`u64`"),
        ("`get_prompt_index`", "GetPromptIndex", "`usize`"),
        ("`check_auto_compact_needed`", "CheckAutoCompactNeeded", "是否触发自动 compaction"),
        ("`repair_history`", "RepairHistory", "修复 dangling tool calls"),
        ("`snapshot`", "Snapshot", "`ChatStateSnapshot`（rewind 用）|"),
    ]:
        w(f"| {m} | {c} | {ret} |")
    w("")
    w("完整命令列表见 §8。")
    w("")
    w("---")
    w("")
    w("## 4. build_conversation_request 流水线")
    w("")
    w("`actor/request_builder.rs` — 每轮采样前由 `BuildConversationRequest` 命令触发：")
    w("")
    w("```text")
    w("ensure_conversation_integrity()   // 命令 handler 先修复 dangling")
    w("  ↓")
    w("1. 若 body ≥ 50MB → compact_images_to_byte_budget（驱逐最老 inline 图）")
    w("2. 若 token > 50% context_window → prune_conversation（软/硬 trim 旧 tool_result）")
    w("3. 若需 memory_reminder → inject_memory_reminder（可 persist 到 actor state）")
    w("4. 组装 ConversationRequest { items, tool_definitions, trace, ... }")
    w("```")
    w("")
    w("关键常量（同文件）：")
    w("")
    w("- `HARD_CLEAR_PLACEHOLDER` = `[Tool result omitted — too old]`")
    w("- `IMAGE_COMPACT_TRIGGER_BYTES` — 接近 50MB 才触发图片驱逐（避免每轮 bust KV cache）")
    w("- `should_prune(total_tokens, context_window)` — 50% 利用率阈值")
    w("")
    w("**Repair 不变量**：integrity 在 clone 之前已修复，request_builder 不对 clone 再跑 O(n) repair。")
    w("")
    w("---")
    w("")
    w("## 5. Compaction 与历史修剪")
    w("")
    w("| 模块 | 作用 |")
    w("| --- | --- |")
    w("| `compaction_mode.rs` | `CompactionMode` 枚举 |")
    w("| `compaction_transcript.rs` | compaction 产物分类、`CompactionDetail` |")
    w("| `compaction_utils.rs` | 共享工具函数 |")
    w("| `conversation_util.rs` | item 级 helper |")
    w("")
    w("自动 compaction：`CheckAutoCompactNeeded` + shell 侧触发 summary 请求，")
    w("`RecordCompactionAt(prompt_index)` 记录截断点。`TruncateToPromptIndex` 用于 rewind。")
    w("")
    w("---")
    w("")
    w("## 6. 持久化")
    w("")
    w("`persistence.rs` — `ChatPersistence` trait：")
    w("")
    w("- `NullChatPersistence` — 无操作")
    w("- `MockChatPersistence` — 测试")
    w("- 生产实现由 shell 注入（写 session JSONL / sqlite 等）")
    w("")
    w("`ReplaceConversation`、`Flush`、memory 注入等路径会调用 `persistence.replace_history`。")
    w("")
    w("---")
    w("")
    w("## 7. Token 估算")
    w("")
    w("`actor/state.rs` 导出：")
    w("")
    w("- `estimate_conversation_tokens`")
    w("- `estimate_item_tokens` / `estimate_messages_tokens`")
    w("- `estimate_tool_definition_tokens`")
    w("")
    w("用于 UI context bar、auto-compact 决策；与 provider 计费 token 可能略有偏差。")
    w("")
    w("---")
    w("")
    w("## 8. ChatStateCommand 全量")
    w("")
    w("### Mutations")
    w("")
    mutations = [
        "PushUserMessage", "PushUserMessageAndAck", "AppendWorkingDirectorySwitchAndAck",
        "PushUserMessageWithRepairReason", "PushAssistantResponse", "PushToolResult",
        "RecordTokenUsage", "RecordLastTurnUsage", "RecordModelCallUsage", "RecordSubagentUsage",
        "MarkUsageIncomplete", "IncrementPromptIndex", "UpdateSamplingConfig",
        "RecordAgentEditedPath", "RecordStreamStart", "RecordTurnStart", "ReplaceConversation",
        "RepairHistory", "ReplaceSystemHead", "CachePromptText", "RecordCompactionAt",
        "Flush", "UpdateCredentials", "RestoreSnapshot", "BeginTurnCapture",
        "AppendHarnessTraceItems", "FlushHarnessTraceTurn", "RepairDanglingAfterHarnessHalt",
    ]
    for i in range(0, len(mutations), 4):
        w(" · ".join(f"`{x}`" for x in mutations[i : i + 4]))
    w("")
    w("### Queries / 构建")
    w("")
    queries = [
        "BuildConversationRequest", "GetConversation", "GetPromptIndex",
        "GetLastCompactionPromptIndex", "GetTotalTokens", "GetLastTurnUsage",
        "GetPromptUsage", "GetSessionUsage", "GetEstimatedTotalTokens",
        "GetEstimatedMessagesTokens", "GetSamplingConfig", "GetAgentEditedPaths",
        "GetNotificationMeta", "Snapshot", "TruncateToPromptIndex", "CheckAutoCompactNeeded",
        "GetCredentials", "GetLastModelMetadata", "TakeTurnMessages", "TakeHarnessTraceTurns",
        "GetConversationLen", "HasDanglingToolCalls", "GetLastAssistantText",
        "GetLastAssistantTextInTurn", "GetFirstUserText", "GetConversationItemAt",
        "GetLastUserQueryText", "GetConversationCounts", "GetSystemMessage",
    ]
    for i in range(0, len(queries), 3):
        w(" · ".join(f"`{x}`" for x in queries[i : i + 3]))
    w("")
    w("---")
    w("")
    w("## 9. Shell 集成点")
    w("")
    w("| Shell 位置 | 用法 |")
    w("| --- | --- |")
    w("| `SessionActor::chat_state_handle` | 持有 `ChatStateHandle` |")
    w("| turn 开始 | `build_conversation_request` → sampler |")
    w("| tool 完成后 | `push_tool_result` |")
    w("| 流式结束 | `push_assistant_response` + `record_last_turn_usage` |")
    w("| `/rewind` | `TruncateToPromptIndex` / `Snapshot` |")
    w("| compaction | `CheckAutoCompactNeeded` + 替换 conversation |")
    w("")
    w("---")
    w("")
    w("## 10. 类型与 Usage")
    w("")
    w("- `types.rs` — `ChatStateSnapshot`、`TurnCapture`、`Credentials`、`PruningConfig`")
    w("- `usage.rs` — `UsageLedger`、`UsageTotals`（session 级用量汇总）")
    w("- 对话 item 类型来自 **`xai-grok-sampling-types`**（`ConversationItem`）")
    w("")
    w("---")
    w("")
    w("## 11. ChatState 内部字段 (`actor/state.rs`)")
    w("")
    w("| 字段 | 含义 |")
    w("| --- | --- |")
    for f, d in [
        ("conversation", "完整 `Vec<ConversationItem>`"),
        ("sampling_config", "模型、context_window 等"),
        ("prompt_index", "用户轮次计数"),
        ("prompt_texts", "rewind 预览用缓存"),
        ("total_tokens", "累计 token"),
        ("agent_edited_paths", "agent 编辑过的路径"),
        ("last_compaction_prompt_index", "上次 compaction 截断点"),
        ("last_turn_usage", "最近一轮 TokenUsage"),
        ("turn_capture", "offset 式 turn 捕获"),
        ("harness_trace_buffer / harness_trace_turns", "harness 子 agent trace"),
    ]:
        w(f"| `{f}` | {d} |")
    w("")
    w("---")
    w("")
    w("## 12. ChatStateEvent")
    w("")
    w("| 变体 | 用途 |")
    w("| --- | --- |")
    w("| `PromptIndexChanged` | hunk tracker 归属 |")
    w("| `TokensUpdated` | 通知 meta、auto-compact |")
    w("| `ConversationReset` | compaction/rewind 后重置 |")
    w("| `ImageBudget` | inline 图驱逐观测 |")
    w("")
    w("---")
    w("")
    w("## 13. 单轮 Turn 生命周期")
    w("")
    w("```text")
    w("push_user_message → build_request → sampler")
    w("→ push_assistant_response → push_tool_result (循环)")
    w("→ record_last_turn_usage → increment_prompt_index")
    w("```")
    w("")
    w("---")
    w("")
    w("## 14. ChatPersistence")
    w("")
    w("`persist_message` · `replace_history` · `flush` · `persist_working_directory_switch_and_ack`")
    w("")
    w("---")
    w("")
    w("## 15. ChatStateHandle 完整 API")
    w("")
    w("| # | 方法 | async |")
    w("| ---: | --- | :---: |")
    handle_path = src / "handle.rs"
    if handle_path.exists():
        t = handle_path.read_text(encoding="utf-8", errors="replace")
        idx = 0
        for m in re.finditer(
            r"^\s+pub (async )?fn (\w+)\(",
            t,
            re.M,
        ):
            idx += 1
            is_async = "✓" if m.group(1) else ""
            w(f"| {idx} | `{m.group(2)}` | {is_async} |")
    w("")
    w("---")
    w("")
    w("## 16. request_builder 详细步骤")
    w("")
    w("`build_conversation_request` 内部顺序（`actor/request_builder.rs`）：")
    w("")
    for i, step in enumerate(
        [
            "读取 `total_tokens` 与 `context_window`，判断 `needs_prune`",
            "若 `persist_memory_reminder`：snapshot_turn_slice → inject → persistence → rebase",
            "测量 `conversation_body_bytes`（wire-accurate，跳过 base64 全扫描）",
            "若 body ≥ IMAGE_COMPACT_TRIGGER：compact_images_to_byte_budget",
            "若 needs_prune：prune_conversation（软 trim + HARD_CLEAR）",
            "若仍有 memory_reminder：inject 到 system 或 user 侧",
            "附加 tool_definitions、trace、conv_id、req_id",
            "若有 image eviction：发送 ChatStateEvent::ImageBudget",
            "返回 ConversationRequest（可能基于 clone 后的 items）",
        ],
        1,
    ):
        w(f"{i}. {step}")
    w("")
    w("**PruningConfig**（`types.rs`）：控制保留最近 N 个 tool result、软 trim 比例等。")
    w("")
    w("---")
    w("")
    w("## 17. mutations 与 integrity")
    w("")
    w("`actor/mutations.rs` 处理 push/replace 类命令；关键路径：")
    w("")
    w("- `push_user_message` — 追加 + persist + 可能 repair")
    w("- `push_tool_result` / `push_assistant_response` — 同上")
    w("- `replace_conversation` — compaction/rewind；发 `ConversationReset`")
    w("- `ensure_conversation_integrity` — `repair_dangling_tool_calls` + dedup")
    w("- `snapshot_turn_slice` / `rebase_turn_capture_offset` — turn 捕获与替换协调")
    w("")
    w("`actor/queries.rs` — 所有 `Get*` 命令的只读实现。")
    w("")
    w("---")
    w("")
    w("## 19. ConversationItem 类型（来自 xai-grok-sampling-types）")
    w("")
    w("| 变体 | 在 history 中的角色 |")
    w("| --- | --- |")
    for v, d in [
        ("System", "system prompt / 规则 / memory reminder"),
        ("User", "用户消息（text + image parts）"),
        ("Assistant", "模型回复 text + tool_calls"),
        ("ToolResult", "工具执行结果（喂回模型）"),
        ("BackendToolCall", "服务端托管工具摘要"),
        ("Reasoning", "thinking / encrypted reasoning blob"),
    ]:
        w(f"| `{v}` | {d} |")
    w("")
    w("---")
    w("")
    w("## 20. ChatStateCommand 逐条说明")
    w("")
    w("| 命令 | 说明 |")
    w("| --- | --- |")
    cmds_path = src / "commands.rs"
    for name, doc in parse_enum_variants(cmds_path, "ChatStateCommand"):
        w(f"| `{name}` | {(doc or '—')[:70]} |")
    w("")
    w("---")
    w("")
    w("## 21. compaction 模块协作")
    w("")
    w("```text")
    w("shell: check_auto_compact_needed → 超阈值")
    w("  → 发起 summary 采样（sampler submit_and_collect）")
    w("  → replace_conversation_for_compaction")
    w("  → record_compaction_at(prompt_index)")
    w("")
    w("compaction_transcript::classify_compaction_path")
    w("  → 工具读路径是否算 compaction artifact（shell tool_dispatch 用）")
    w("```")
    w("")
    w("```")
    w("")
    w("---")
    w("")
    w("## 22. actor/ 函数索引")
    w("")
    write_all_fn_index(src / "actor", w, "mutations / queries / request_builder", src)
    w("---")
    w("")
    w("## 23. Shell 源码对照（chat-state）")
    w("")
    w("| Shell 文件 | 调用 |")
    w("| --- | --- |")
    for f, c in [
        ("session/handle.rs", "SessionHandle 持有 chat_state_handle"),
        ("session/compaction.rs", "get_conversation, replace_conversation_for_compaction"),
        ("session/acp_session_impl/tool_calls.rs", "push_tool_result, build_request"),
        ("session/acp_session_impl/tool_dispatch.rs", "compaction_artifact_read → classify_compaction_path"),
        ("session/goal_*.rs", "harness trace: append_harness_trace_items"),
        ("session/rewind.rs", "snapshot, truncate_to_prompt_index"),
    ]:
        w(f"| `{f}` | {c} |")
    w("")
    write_per_file_guide(src, w, "24. 逐文件导读")
    w("---")
    w("")
    w("## 25. 源码文件索引（简表）")
    w("")
    write_file_index(src, w)
    w("---")
    w("")
    write_symbol_index(src, w, "26. 符号索引（pub API）")
    w("")
    w("## 27. 依赖与被依赖")
    w("")
    w("依赖：" + (" · ".join(f"`{d}`" for d in info["deps"]) if info["deps"] else "见 Cargo.toml"))
    w("")
    w("被依赖：" + (" · ".join(f"`{u}`" for u in users) if users else "—"))
    w("")
    w("---")
    w("")
    w("## 28. 常见问题（读代码时）")
    w("")
    w("**Q: 为什么 conversation 修改有时 clone 有时 in-place？**")
    w("A: `build_conversation_request` 仅在需要 prune/compact/inject 时 clone；")
    w("否则直接引用 actor 内 `conversation`，减少分配。")
    w("")
    w("**Q: dangling tool call 是什么？**")
    w("A: assistant 发了 tool_calls 但没有对应 tool_result；`repair_history` 注入 synthetic result。")
    w("")
    w("**Q: prompt_index 与 conversation len 区别？**")
    w("A: `prompt_index` 计用户轮次；conversation 含 system、tool、assistant 全部 item。")
    w("")
    w("**Q: TurnCapture 为何用 offset 而非 Vec 拷贝？**")
    w("A: 避免每条 push 克隆 item；take 时一次性 `conversation[offset..].to_vec()`。")
    w("")
    w("**Q: 与 xai-grok-compaction crate 关系？**")
    w("A: `EstimatedItemTokenCounter` 实现共享 compaction 引擎的 token 计数接口。")
    w("")
    w("---")
    w("")
    w("## 28. ChatStateActor::handle_command 分发（节选）")
    w("")
    w("`actor/mod.rs` — 每个 `ChatStateCommand` 变体映射到 mutations/queries：")
    w("")
    for cmd, handler in [
        ("PushUserMessage", "push_user_message"),
        ("PushAssistantResponse / PushToolResult", "push_message"),
        ("BuildConversationRequest", "ensure_integrity → build_conversation_request"),
        ("ReplaceConversation", "replace_conversation + ConversationReset 事件"),
        ("RepairHistory", "repair_history（可能返回 RepairHistoryBlocked）"),
        ("TruncateToPromptIndex", "truncate + 重置 turn_capture"),
        ("GetConversation", "clone conversation → oneshot"),
        ("CheckAutoCompactNeeded", "token 阈值 + CompactionMode"),
        ("Flush", "persistence.flush()"),
    ]:
        w(f"- `{cmd}` → `{handler}`")
    w("")
    w("---")
    w("")
    w("## 29. 阅读顺序")
    w("")
    w("1. `lib.rs` 架构图注释")
    w("2. `handle.rs` — 对外 API")
    w("3. `commands.rs` — 命令枚举")
    w("4. `actor/mod.rs` → `mutations.rs` / `request_builder.rs`")
    w("5. `compaction_transcript.rs` — 与 shell compaction 对照")
    w("6. [09 端到端](../09_end_to_end_request_flow.md) 采样段落")
    w("")
    return "\n".join(L)


def gen_sampler_doc(crate_dir: Path) -> str:
    src = crate_dir / "src"
    info = cargo_info(crate_dir)
    users = reverse_deps(info["name"])
    L: list[str] = []

    def w(s: str = "") -> None:
        L.append(s)

    w("# xai-grok-sampler 代码导读")
    w("")
    w("> `crates/codegen/xai-grok-sampler` · HTTP 流式推理、重试、多后端")
    w("> 配套：[xai-chat-state](./xai-chat-state.md) · [09 端到端](../09_end_to_end_request_flow.md)")
    w("")
    w("---")
    w("")
    w("## 1. 职责与三层 API")
    w("")
    w("从 shell 抽出的 **采样层 Actor**，负责：")
    w("")
    w("- 向 x.ai API 发 HTTP 流式请求")
    w("- 多请求并发、取消、重试退避")
    w("- 原始 chunk → `SamplingEvent` 转换")
    w("")
    w("```text")
    w("Layer 1: SamplingClient     → 原始 HTTP chunk Stream")
    w("Layer 2: stream::*          → SamplingEvent Stream")
    w("Layer 3: SamplerHandle/Actor → 并发 + retry + cancel")
    w("```")
    w("")
    w("```text")
    w("SessionActor")
    w("  chat_state.build_conversation_request()")
    w("  sampler_handle.submit(request_id, request)")
    w("       ↓ SamplingEvent channel")
    w("  SessionActor 处理 Chunk / Done / Error")
    w("  chat_state.push_assistant_response / push_tool_result loop")
    w("```")
    w("")
    w("---")
    w("")
    w("## 2. SamplerActor 结构")
    w("")
    w("`actor/mod.rs`：")
    w("")
    w("```text")
    w("SamplerActor::spawn(config, retry_policy, event_tx) → SamplerHandle")
    w("")
    w("run() loop (tokio::select!, biased):")
    w("  1. tasks.join_next() — 清理完成的 per-request task")
    w("  2. cmd_rx.recv() — Submit / Cancel / UpdateConfig / IsActive / ActiveCount")
    w("")
    w("Submit → spawn request_task（可多个并发）")
    w("Cancel → CancellationToken + 从 active_requests 移除")
    w("```")
    w("")
    w("| 文件 | 作用 |")
    w("| --- | --- |")
    w("| `actor/state.rs` | `ActorState`、`ActiveRequest` |")
    w("| `actor/request_task.rs` | 单请求：client → stream → retry → event |")
    w("| `handle.rs` | `submit` / `cancel` / `submit_and_collect` |")
    w("| `commands.rs` | `SamplerCommand` 枚举 |")
    w("")
    w("---")
    w("")
    w("## 3. SamplerHandle API")
    w("")
    w("| 方法 | 行为 |")
    w("| --- | --- |")
    for m, d in [
        ("`submit`", "fire-and-forget；结果走共享 `event_tx`"),
        ("`submit_with_config`", "单请求覆盖 `SamplerConfig`（如换模型）"),
        ("`submit_and_collect`", "await 完成 + 仍发事件给 UI（compaction/summary）"),
        ("`cancel`", "取消 in-flight `RequestId`"),
        ("`update_config`", "更新 actor 默认 config"),
        ("`is_active` / `active_count`", "查询并发数"),
        ("`noop`", "测试用空 handle"),
    ]:
        w(f"| {m} | {d} |")
    w("")
    w("---")
    w("")
    w("## 4. SamplingClient（Layer 1）")
    w("")
    w("`client.rs` — `SamplingClient` + `ApiBackend`：")
    w("")
    w("| Backend | stream 入口 | 说明 |")
    w("| --- | --- | --- |")
    w("| Chat Completions | `conversation_stream_chat_completions` | OpenAI 兼容 |")
    w("| Responses | `conversation_stream_responses` | x.ai Responses API |")
    w("| Messages | `conversation_stream_messages` | Anthropic 风格 messages |")
    w("")
    w("`SamplerConfig.api_backend` 决定 `request_task` 调用哪条路径。")
    w("`shared_http.rs` — 连接池复用。")
    w("")
    w("---")
    w("")
    w("## 5. Stream 转换（Layer 2）")
    w("")
    w("`stream/mod.rs`：")
    w("")
    w("| 函数 | 文件 | 输出 |")
    w("| --- | --- | --- |")
    w("| `stream_chat_completions` | `chat_completions.rs` | `SamplingEvent` |")
    w("| `stream_responses` | `responses.rs` | `SamplingEvent` |")
    w("| `stream_messages` | `messages.rs` | `SamplingEvent` |")
    w("| `collect_response` | `collect.rs` | 非流式聚合 |")
    w("")
    w("事件类型见 `events.rs`：`TextDelta`、`ToolCallDelta`、`Done`、`Error` 等。")
    w("")
    w("---")
    w("")
    w("## 6. 重试与错误分类")
    w("")
    w("`retry.rs`：")
    w("")
    w("- `classify_error` → `RetryDecision`（重试 / 立即失败 / 换策略）")
    w("- `retry_backoff_with_jitter` — 指数退避")
    w("- `DEFAULT_MAX_RETRIES`、`RATE_LIMIT_RETRY_THRESHOLD`")
    w("- `format_sampling_error` — 用户可见错误文案")
    w("")
    w("`config.rs` — `RetryPolicy`、`BearerResolver`、`HeaderInjector`、`AuthScheme`。")
    w("")
    w("---")
    w("")
    w("## 7. 配置 SamplerConfig")
    w("")
    w("关键字段（`config.rs`）：")
    w("")
    w("- `model_id`、`api_backend`、`base_url`")
    w("- `bearer_resolver` / `header_injector` — 鉴权")
    w("- `reasoning_effort`、thinking 相关开关")
    w("- `origin_client_info` — telemetry / attribution")
    w("")
    w("Shell 在登录、换模型、`update_config` 时推送新 config。")
    w("")
    w("---")
    w("")
    w("## 8. 指标与日志")
    w("")
    w("| 模块 | 作用 |")
    w("| --- | --- |")
    w("| `metrics.rs` | `InferenceLatencyStats`、`compute_percentiles` |")
    w("| `sampling_log.rs` | 结构化采样日志、`AuthInfo` |")
    w("| `attribution.rs` | `SamplingConsumer`、401 归因回调 |")
    w("| `doom_loop.rs` | `DoomLoopSignalCollector` — 检测重复 tool 循环 |")
    w("")
    w("---")
    w("")
    w("## 9. SamplingEvent 消费（Shell 侧）")
    w("")
    w("典型处理（`SessionActor` turn 循环）：")
    w("")
    w("```text")
    w("SamplingEvent::TextDelta        → ACP AgentMessageChunk")
    w("SamplingEvent::ToolCallStart    → 准备 PreparedToolCall")
    w("SamplingEvent::ToolCallDelta    → 累积 arguments JSON")
    w("SamplingEvent::Done             → push_assistant + 执行 tools")
    w("SamplingEvent::Error            → 失败 UI + repair 路径")
    w("```")
    w("")
    w("---")
    w("")
    w("## 10. SamplerCommand")
    w("")
    w("| 变体 | 字段 |")
    w("| --- | --- |")
    w("| `Submit` | request_id, request, config?, completion_tx? |")
    w("| `Cancel` | request_id |")
    w("| `UpdateConfig` | config |")
    w("| `IsActive` | request_id, reply |")
    w("| `ActiveCount` | reply |")
    w("")
    w("---")
    w("")
    w("## 11. SamplingEvent 全量")
    w("")
    w("| 变体 | 含义 |")
    w("| --- | --- |")
    for v, d in [
        ("StreamStarted", "HTTP 流建立"),
        ("FirstToken", "首个 content token"),
        ("ChannelToken", "Text 或 Reasoning 通道增量"),
        ("ToolCallDelta", "tool call arguments 流式片段"),
        ("Completed", "成功结束 + ConversationResponse"),
        ("Retrying", "重试中（含 doom loop 信息）"),
        ("Failed", "最终失败"),
        ("ModelMetadata", "响应头中的模型元数据"),
        ("BackendToolCallStarted", "服务端托管工具开始"),
        ("BackendToolCallCompleted", "服务端托管工具完成"),
    ]:
        w(f"| `{v}` | {d} |")
    w("")
    w("---")
    w("")
    w("## 12. request_task 重试循环")
    w("")
    w("`actor/request_task.rs` — `run_request_task`：")
    w("")
    w("```text")
    w("SamplingClient::new(config)")
    w("loop attempt in 0..=max_retries:")
    w("  选择 stream_* 按 api_backend")
    w("  消费 SamplingEvent 流")
    w("  AttemptOutcome: Completed | Empty | Failed | Cancelled | InitFailed")
    w("  classify_error → RetryDecision::Retry | GiveUp")
    w("  retry_backoff_with_jitter → 下一 attempt")
    w("emit Completed / Failed + completion_tx")
    w("```")
    w("")
    w("`DEFAULT_IDLE_TIMEOUT_SECS` = 300（5 分钟 chunk 空闲超时）。")
    w("")
    w("---")
    w("")
    w("## 13. RetryDecision (`retry.rs`)")
    w("")
    w("- `classify_error` — 根据 `SamplingError` 判断是否可重试")
    w("- `Retry` / `GiveUp` / rate-limit 特殊阈值")
    w("- `doom_loop_backoff` — doom loop 检测后的退避")
    w("- `format_sampling_error` — UI 错误文案")
    w("")
    w("---")
    w("")
    w("## 14. SamplerConfig 主要字段")
    w("")
    w("`api_key` · `base_url` · `model` · `api_backend` · `auth_scheme` ·")
    w("`max_completion_tokens` · `temperature` · `top_p` · `extra_headers` ·")
    w("`max_retries` · `idle_timeout_secs` · `reasoning_effort` · `doom_loop_recovery`")
    w("")
    w("Shell 在 `resolve_model_to_sampling_config` / `reconstruct_full_config` 构建。")
    w("")
    w("---")
    w("")
    w("## 15. SamplingErrorKind")
    w("")
    for kind in [
        "Auth", "Http", "Api", "Serialization", "IdleTimeout",
        "EmptyResponse", "Cancelled", "DoomLoopDetected", "Other",
    ]:
        w(f"- `{kind}`")
    w("")
    w("Session 对 `Api { status: 400 }` 结合 `ModelMetadata` 判断是否 context window 溢出。")
    w("")
    w("---")
    w("")
    w("## 16. client.rs 与三后端")
    w("")
    w("| 方法 | 后端 |")
    w("| --- | --- |")
    w("| `conversation_stream_chat_completions` | OpenAI Chat Completions |")
    w("| `conversation_stream_responses` | x.ai Responses API |")
    w("| `conversation_stream_messages` | Anthropic Messages |")
    w("")
    w("`SamplingClient::new` 校验 config；`user_agent_string_for` 构建 UA。")
    w("")
    w("---")
    w("")
    w("## 17. stream/ 各文件职责")
    w("")
    w("| 文件 | 职责 |")
    w("| --- | --- |")
    for f, d in [
        ("chat_completions.rs", "解析 SSE chunk → ToolCallDelta / ChannelToken"),
        ("responses.rs", "Responses API 事件流 + `stream_responses_tracked`"),
        ("messages.rs", "Anthropic message delta"),
        ("collect.rs", "非流式 `collect_response`"),
    ]:
        w(f"| `{f}` | {d} |")
    w("")
    w("---")
    w("")
    w("## 18. doom_loop 与 attribution")
    w("")
    w("- `doom_loop.rs` — `DoomLoopSignalCollector` 检测重复 tool 模式")
    w("- `attribution.rs` — 401 归因、`SamplingConsumer` 回调")
    w("- `sampling_log.rs` — `request_span` 结构化日志")
    w("- `metrics.rs` — TTFT、总延迟分位数")
    w("")
    w("---")
    w("")
    w("## 19. SamplerHandle 完整 API")
    w("")
    w("| 方法 | async | 说明 |")
    w("| --- | :---: | --- |")
    for name, async_, desc in [
        ("submit", False, "提交请求"),
        ("submit_with_config", False, "带 config 覆盖"),
        ("cancel", False, "取消"),
        ("update_config", False, "更新默认 config"),
        ("is_active", True, "是否 in-flight"),
        ("active_count", True, "并发数"),
        ("submit_and_collect", True, "await 完成"),
        ("noop", False, "空 handle"),
    ]:
        w(f"| `{name}` | {'✓' if async_ else ''} | {desc} |")
    w("")
    w("---")
    w("")
    w("## 20. SamplerCommand 逐条说明")
    w("")
    w("| 命令 | 说明 |")
    w("| --- | --- |")
    for name, doc in parse_enum_variants(src / "commands.rs", "SamplerCommand"):
        w(f"| `{name}` | {(doc or '—')[:70]} |")
    w("")
    w("---")
    w("")
    w("## 21. Shell 源码对照（sampler）")
    w("")
    w("| Shell 文件 | 调用 |")
    w("| --- | --- |")
    for f, c in [
        ("session/handle.rs", "sampler_handle 字段"),
        ("session/acp_session_impl/turn*.rs", "submit, cancel, 消费 SamplingEvent"),
        ("session/compaction.rs", "submit_and_collect 做 summary"),
        ("agent/config.rs", "resolve_model_to_sampling_config"),
        ("session/acp_session.rs", "reconstruct_full_config, update_config"),
    ]:
        w(f"| `{f}` | {c} |")
    w("")
    w("---")
    w("")
    w("## 22. actor/ 与 stream/ 函数索引")
    w("")
    write_all_fn_index(src / "actor", w, "actor/", src)
    write_all_fn_index(src / "stream", w, "stream/", src)
    client = src / "client.rs"
    if client.exists():
        fns = all_fn_names(client, 50)
        w("**`client.rs`：** " + ", ".join(f"`{n}`" for n in fns))
        w("")
    w("")
    write_per_file_guide(src, w, "23. 逐文件导读")
    w("---")
    w("")
    w("## 24. 源码文件索引（简表）")
    w("")
    write_file_index(src, w)
    w("---")
    w("")
    write_symbol_index(src, w, "25. 符号索引（pub API）")
    w("")
    w("## 26. 依赖与被依赖")
    w("")
    w("依赖：" + (" · ".join(f"`{d}`" for d in info["deps"]) if info["deps"] else "见 Cargo.toml"))
    w("")
    w("被依赖：" + (" · ".join(f"`{u}`" for u in users) if users else "—"))
    w("")
    w("---")
    w("")
    w("## 27. 常见问题（读代码时）")
    w("")
    w("**Q: 为何 actor 单线程却支持并发请求？**")
    w("A: Actor 只管理状态；每个 `Submit` spawn 独立 `request_task`，通过 `JoinSet` 清理。")
    w("")
    w("**Q: 事件 channel 与 completion_tx 区别？**")
    w("A: `event_tx` 给 UI 流式更新；`completion_tx` 仅 `submit_and_collect` 用，返回最终 Result。")
    w("")
    w("**Q: 三个 api_backend 如何选？**")
    w("A: Shell `SamplingConfig` / 模型配置决定；`request_task` match 后调对应 `stream_*`。")
    w("")
    w("**Q: Empty response 为何重试？**")
    w("A: 模型可能只返回 reasoning 无 text/tool；视为 transient，走 retry 循环。")
    w("")
    w("**Q: doom loop 谁消费？**")
    w("A: `DoomLoopSignalCollector` + shell recovery policy；`Retrying` 事件带 trigger 标签。")
    w("")
    w("---")
    w("")
    w("## 28. SamplerActor::handle_command")
    w("")
    w("```text")
    w("Submit → spawn run_request_task (JoinSet)")
    w("Cancel → active.cancel_token.cancel() + remove")
    w("UpdateConfig → state.config = config")
    w("IsActive → reply.send(active_requests.contains(id))")
    w("ActiveCount → reply.send(active_requests.len())")
    w("```")
    w("")
    w("---")
    w("")
    w("## 29. SamplingEvent → Shell 映射")
    w("")
    w("| Event | Shell 侧典型处理 |")
    w("| --- | --- |")
    for e, h in [
        ("StreamStarted", "记录 stream 开始时间"),
        ("ChannelToken", "ACP AgentMessageChunk（text/reasoning）"),
        ("ToolCallDelta", "累积 tool call JSON"),
        ("Completed", "push_assistant + 调度 tool_calls"),
        ("Failed", "错误 UI + 可能 reauth"),
        ("Retrying", "状态栏/日志显示重试"),
        ("BackendToolCallStarted", "后端搜索等 UI 块"),
    ]:
        w(f"| `{e}` | {h} |")
    w("")
    w("---")
    w("")
    w("## 30. 阅读顺序")
    w("")
    w("1. `lib.rs` 三层 API 注释")
    w("2. `handle.rs` + `commands.rs`")
    w("3. `actor/mod.rs` + `request_task.rs`")
    w("4. `client.rs` — HTTP 入口")
    w("5. `stream/responses.rs`（或你用的 backend）")
    w("6. `retry.rs` — 错误路径")
    w("7. shell turn 循环中对 `SamplingEvent` 的 match")
    w("")
    return "\n".join(L)


def gen_pager_doc(crate_dir: Path) -> str:
    src = crate_dir / "src"
    info = cargo_info(crate_dir)
    users = reverse_deps(info["name"])
    mods = lib_modules(src)
    L: list[str] = []

    def w(s: str = "") -> None:
        L.append(s)

    w("# xai-grok-pager 代码导读")
    w("")
    w("> `crates/codegen/xai-grok-pager` · Grok Build TUI（ratatui + ACP 客户端）")
    w("> 配套：[03 TUI 架构](../03_tui_architecture.md) · [xai-grok-shell](./xai-grok-shell.md)")
    w("")
    w("---")
    w("")
    w("## 1. 职责")
    w("")
    w("终端 UI：**输入、scrollback 渲染、ACP 会话、权限弹窗、slash 命令**。")
    w("业务逻辑在 `xai-grok-shell`（SessionActor）；pager 是 **ACP Client + 视图层**。")
    w("")
    w("```text")
    w("用户按键/鼠标")
    w("  → input → Action")
    w("  → dispatch (同步，纯函数)")
    w("  → Vec<Effect>")
    w("  → effects (spawn async: ACP RPC, 文件, 网络)")
    w("  → TaskResult → dispatch")
    w("")
    w("ACP 通知 (shell → pager)")
    w("  → acp_handler → 更新 AgentSession / ScrollbackState")
    w("  → 下一帧 draw")
    w("```")
    w("")
    w("---")
    w("")
    w("## 2. Action / Effect / TaskResult 管道")
    w("")
    w("定义在 `app/actions.rs`（~2900 行 `Action` 枚举）：")
    w("")
    w("| 类型 | 生产者 | 消费者 | 约束 |")
    w("| --- | --- | --- | --- |")
    w("| `Action` | `input/` 键鼠 | `dispatch::dispatch` | 同步、无副作用 |")
    w("| `Effect` | dispatch | `effects/` + `event_loop` | 描述异步工作 |")
    w("| `TaskResult` | 完成的任务 | dispatch | 结果回灌 |")
    w("")
    w("**dispatch 不变量**（`app/dispatch/mod.rs` 注释）：")
    w("")
    w("- 不直接操作 terminal / 网络 / 文件")
    w("- 所有 mutation 同步、可单测")
    w("- 异步 = 返回 `Effect`，由 event loop 执行")
    w("")
    w("---")
    w("")
    w("## 3. app/ 子系统")
    w("")
    w("| 模块 | 作用 |")
    w("| --- | --- |")
    for m, d in [
        ("actions", "Action / Effect / TaskResult"),
        ("dispatch", "router + session/turn/settings/permissions…"),
        ("effects", "Effect → tokio::spawn"),
        ("event_loop", "biased select: input, ACP, effects, tick"),
        ("acp_handler", "ACP 通知 → scrollback + 权限队列"),
        ("agent", "AgentSession, TurnState, AgentId"),
        ("agent_view", "单 agent 输入框 + pane"),
        ("app_view", "根视图：welcome / agent / dashboard"),
        ("session_startup", "会话创建、worktree 选择"),
        ("turn_completion", "turn 结束协调"),
        ("cli", "PagerArgs, 子命令"),
    ]:
        w(f"| `{m}` | {d} |")
    w("")
    w("---")
    w("")
    w("## 4. acp_handler 路由")
    w("")
    w("`app/acp_handler/mod.rs` — 处理 `AcpClientMessage`：")
    w("")
    w("| 子模块 | 职责 |")
    w("| --- | --- |")
    w("| `routing` | session_id → AgentId 匹配 |")
    w("| `session_notification` | `x.ai/session_notification`、replay |")
    w("| `permissions` | tool permission 队列 UI |")
    w("| `queue` | prompt 队列、running adoption |")
    w("| `mcp` | MCP 相关通知 |")
    w("| `background` | 后台任务状态 |")
    w("| `workflow_ingest` | workflow 更新 |")
    w("| `follow_ups` | 后续 prompt |")
    w("")
    w("核心类型：`AcpUpdateTracker`（`acp/tracker.rs`）把 ACP update 转成 scrollback blocks。")
    w("")
    w("---")
    w("")
    w("## 5. scrollback 渲染管线")
    w("")
    w("`scrollback/mod.rs`：")
    w("")
    w("```text")
    w("ACP update → blocks/* (AgentMessage, ToolCall, Thinking, UserPrompt…)")
    w("           → entry::ScrollbackEntry")
    w("           → state::ScrollbackState (scroll, selection, turns)")
    w("           → wrappers/* (BlockRenderer, padding, accent)")
    w("           → render.rs → ratatui Buffer")
    w("```")
    w("")
    w("| 目录 | 内容 |")
    w("| --- | --- |")
    w("| `blocks/` | 各类型 block 的 layout + 内容 |")
    w("| `blocks/tool/` | 工具调用 UI（bash, edit, search…）|")
    w("| `state/` | timeline, layout, selection |")
    w("| `wrappers/` | 组合渲染、CSI 过滤 |")
    w("")
    w("---")
    w("")
    w("## 6. views/ 与输入")
    w("")
    w("| 区域 | 模块 |")
    w("| --- | --- |")
    for v, d in [
        ("prompt_widget", "多行输入、图片粘贴"),
        ("permission_view", "工具权限审批"),
        ("session_picker", "欢迎页选会话"),
        ("dashboard", "用量/状态面板"),
        ("status_bar / context_bar", "底部状态"),
        ("settings_modal", "设置 UI"),
        ("tasks_pane / queue_pane", "后台任务、prompt 队列"),
    ]:
        w(f"| `{v}` | {d} |")
    w("")
    w("`input/` — 键盘（vim mode）、鼠标、Kitty 协议、`keyboard_normalizer`。")
    w("")
    w("---")
    w("")
    w("## 7. slash 命令")
    w("")
    w("`slash/registry.rs` 注册；实现在 `slash/commands/*.rs`：")
    w("")
    w("`/help` `/model` `/theme` `/rewind` `/tasks` `/mcp` `/plan` `/export` …")
    w("")
    w("命令解析 → `Action` → dispatch 同路径。")
    w("")
    w("---")
    w("")
    w("## 8. 与 xai-grok-pager-render")
    w("")
    w("`lib.rs` re-export `xai_grok_pager_render`：")
    w("`theme` `glyphs` `render` `syntax` `terminal` `clipboard` …")
    w("")
    w("pager = 业务 + 布局；render crate = 纯绘制原语。")
    w("")
    w("---")
    w("")
    w("## 9. minimal 模式接缝")
    w("")
    w("- `minimal_hook.rs` — full pager → minimal dispatch（fn 指针 IoC）")
    w("- `minimal_api.rs` — minimal → pager 只读 facade")
    w("- 实际 minimal UI 在 **`xai-grok-pager-minimal`** crate")
    w("")
    w("---")
    w("")
    w("## 10. 顶层 lib.rs 模块一览")
    w("")
    w("| 模块 | 说明 |")
    w("| --- | --- |")
    for m in mods:
        mp = src / m / "mod.rs"
        if not mp.exists():
            mp = src / f"{m}.rs"
        doc = doc_block(mp, 1) if mp.exists() else []
        w(f"| `{m}` | {(doc[0] if doc else '—')[:75]} |")
    w("")
    w("---")
    w("")
    w("## 11. 源码文件索引")
    w("")
    write_file_index(src, w, include_pub=True)
    w("---")
    w("")
    w("## 12. 依赖与被依赖")
    w("")
    w("依赖：" + (" · ".join(f"`{d}`" for d in info["deps"]) if info["deps"] else "见 Cargo.toml"))
    w("")
    w("被依赖：" + (" · ".join(f"`{u}`" for u in users) if users else "—"))
    w("")
    w("---")
    w("")
    w("## 13. 阅读顺序")
    w("")
    w("1. `app/mod.rs` 模块图")
    w("2. `app/actions.rs` — Action 全貌（可搜索关键字）")
    w("3. `app/dispatch/router.rs` — 入口 dispatch")
    w("4. `app/event_loop.rs` — 主循环")
    w("5. `app/acp_handler/routing.rs` + `session_notification.rs`")
    w("6. `scrollback/blocks/tool/` — 工具 UI")
    w("7. [03 TUI 架构](../03_tui_architecture.md)")
    w("")
    return "\n".join(L)


ROLE_HINTS = {
    "xai-grok-agent": "Agent 定义、AgentBuilder、prompt/skills/AGENTS.md。",
    "xai-grok-pager": "TUI：Action/Effect、ACP 客户端。",
    "xai-grok-pager-bin": "组合根 main()、CLI 分流。",
    "xai-grok-workspace": "WorkspaceOps、权限、git、worktree。",
    "xai-grok-sampler": "SamplerActor、HTTP 推理流。",
    "xai-chat-state": "ChatStateHandle、build_request。",
    "xai-grok-mcp": "MCP 客户端（rmcp 隔离）。",
    "xai-acp-lib": "ACP channel/gateway。",
    "xai-grok-config": "config.toml 加载。",
    "xai-grok-hooks": "pre/post tool hooks。",
    "xai-grok-sampling-types": "ConversationItem、ToolSpec 类型。",
}

LINKS = {
    "xai-grok-agent": ["../09_end_to_end_request_flow.md", "./xai-grok-tools.md"],
    "xai-grok-pager": ["../03_tui_architecture.md", "./xai-grok-shell.md"],
    "xai-grok-workspace": ["../05_tools_workspace_permissions.md", "./xai-grok-tools.md"],
    "xai-grok-sampler": ["./xai-chat-state.md"],
    "xai-chat-state": ["../09_end_to_end_request_flow.md"],
    "xai-grok-mcp": ["../11_mcp_protocol.md"],
    "xai-acp-lib": ["../10_acp_protocol.md"],
    "xai-grok-tools": ["../05_tools_workspace_permissions.md", "./xai-grok-agent.md"],
}


def gen_crate_doc(crate_dir: Path) -> str:
    name = crate_dir.name
    if name == "xai-grok-tools":
        return gen_tools_doc(crate_dir)
    if name == "xai-grok-agent":
        return gen_agent_doc(crate_dir)
    if name == "xai-grok-workspace":
        return gen_workspace_doc(crate_dir)
    if name == "xai-chat-state":
        return gen_chat_state_doc(crate_dir)
    if name == "xai-grok-sampler":
        return gen_sampler_doc(crate_dir)
    if name == "xai-grok-pager":
        return gen_pager_doc(crate_dir)
    if name == "xai-grok-shell":
        existing = DOCS / "xai-grok-shell.md"
        if existing.exists() and existing.stat().st_size > 100_000:
            return existing.read_text(encoding="utf-8")

    src = crate_dir / "src"
    info = cargo_info(crate_dir)
    users = reverse_deps(info["name"])
    mods = lib_modules(src) if src.exists() else []
    all_rs = sorted(p for p in src.rglob("*.rs") if src.exists() and not skip(p))

    L: list[str] = []

    def w(s: str = "") -> None:
        L.append(s)

    w(f"# {info['name']}")
    w("")
    w(f"> 路径：`crates/codegen/{name}`")
    if info["version"]:
        w(f"> 版本：{info['version']}")
    is_bin = (src / "main.rs").exists() and not (src / "lib.rs").exists()
    w(f"> 类型：{'二进制 crate' if is_bin else '库 crate'}")
    w("")
    w("---")
    w("")
    w("## 1. 概述")
    w("")
    w(info["desc"] or f"`{name}` 是 Grok Build codegen 工作区成员。")
    if name in ROLE_HINTS:
        w("")
        w(ROLE_HINTS[name])
    w("")
    w("---")
    w("")
    w("## 2. 顶层模块 (`lib.rs` / `main.rs`)")
    w("")
    if mods:
        w("| 模块 | 说明 |")
        w("| --- | --- |")
        for m in mods:
            # try find mod.rs doc
            mp = src / m / "mod.rs"
            if not mp.exists():
                mp = src / f"{m}.rs"
            doc = doc_block(mp, 1) if mp.exists() else []
            w(f"| `{m}` | {(doc[0] if doc else '—')[:80]} |")
    else:
        w("（无可解析 `pub mod`）")
    w("")
    w("---")
    w("")
    w("## 3. 源码文件索引")
    w("")
    if not all_rs:
        w("无 `src/*.rs`。")
    else:
        by_dir: dict[str, list[Path]] = defaultdict(list)
        for p in all_rs:
            by_dir[str(p.parent.relative_to(src))].append(p)
        for dname in sorted(by_dir):
            w(f"### `{dname}/`")
            w("")
            w("| 文件 | 行数 | 文档 | 公开 API（节选）|")
            w("| --- | ---: | --- | --- |")
            for p in sorted(by_dir[dname], key=lambda x: x.name):
                doc = doc_block(p, 1)
                pubs = ", ".join(pub_items(p, 5))
                w(
                    f"| `{p.name}` | {line_count(p)} | "
                    f"{(doc[0] if doc else '—')[:55]} | {pubs[:70] or '—'} |"
                )
            w("")
    w("---")
    w("")
    w("## 4. 工作区依赖")
    w("")
    w(" · ".join(f"`{d}`" for d in info["deps"]) if info["deps"] else "见 Cargo.toml")
    w("")
    w("---")
    w("")
    w("## 5. 被谁依赖")
    w("")
    w(" · ".join(f"`{u}`" for u in users) if users else "（工作区内无直接引用）")
    w("")
    w("---")
    w("")
    w("## 6. 开发命令")
    w("")
    w("```sh")
    w(f"cargo check -p {info['name']}")
    w(f"cargo test -p {info['name']}")
    w("```")
    w("")
    w("---")
    w("")
    w("## 7. 相关阅读")
    w("")
    for link in LINKS.get(name, ["../01_project_map.md", "./README.md"]):
        w(f"- [{Path(link).name}]({link})")
    w("")
    return "\n".join(L)


def main() -> None:
    crates = sorted(d for d in CODEGEN.iterdir() if d.is_dir() and (d / "Cargo.toml").exists())
    for crate_dir in crates:
        content = gen_crate_doc(crate_dir)
        (DOCS / f"{crate_dir.name}.md").write_text(content + "\n", encoding="utf-8")
    print(f"Wrote {len(crates)} docs to {DOCS}")


if __name__ == "__main__":
    main()
