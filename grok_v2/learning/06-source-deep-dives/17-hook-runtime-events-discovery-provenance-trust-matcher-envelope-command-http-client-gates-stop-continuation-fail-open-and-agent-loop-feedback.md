# 源码精读 17：Hook Runtime 如何发现、匹配、执行，并把决策接回 Agent Loop

> 上一篇追到 Tool Runtime。本篇把 Tool 前后的 Hook 扩展成完整系统：Hook 从哪里来，怎样变成 Registry，事件怎样携带上下文，Command、HTTP、Client Callback 怎样执行，以及 Tool Gate 与 Stop Gate 怎样真正改变 Agent 行为。

## 0. 本文回答什么

1. Hook、事件、Handler、Matcher 和 Gate 分别是什么；
2. Hook 从配置层、全局目录、项目目录、兼容配置、插件、Agent 和客户端怎样进入 Session；
3. 项目 Hook 为什么受 Folder Trust 约束；
4. 多来源 Hook 如何排序、命名和去重；
5. 15 种事件为什么不共享同一种控制语义；
6. `Observe`、`Tool`、`Stop` 三类 Gate 有什么不同；
7. Matcher 如何兼容 `Bash` 等外部 Tool 名；
8. `HookEventEnvelope` 为什么要统一元数据和事件 Payload；
9. 大 Tool Input/Result 怎样限制到 128 KiB；
10. Command Hook 怎样启动进程、传 stdin、注入环境变量和回收进程树；
11. HTTP Hook 怎样限制 HTTPS、私网地址和 Redirect；
12. PreToolUse 的 JSON、Exit Code 和失败开放规则；
13. Stop Hook 的 Block、Additional Context、Force Stop 如何聚合；
14. Stop Hook 为什么最多让同一 Turn 继续 8 次；
15. Client Hook 为什么使用 ACP Reverse Request/Notification；
16. Hook 结果如何进入 UI、Telemetry、Conversation 和下一轮模型推理。

源码基线：

```text
ed6d543
```

---

## 1. 先建立正确心智模型

Hook 不是一段“在某处顺手调用的脚本”。它是五层结构：

```text
Source
  配置层 / JSON 文件 / Plugin / Agent / Client
    │
    ▼
HookSpec
  event + matcher + handler + timeout + env + provenance
    │
    ▼
HookRegistry
  按 Event 索引、按优先级去重的快照
    │
    ▼
Envelope
  Session 公共字段 + Event-specific Payload
    │
    ▼
Dispatcher
  Observe / Tool Gate / Stop Gate
    │
    ▼
Runner
  Command / HTTP / Client reverse RPC
    │
    ▼
Effect
  记录 / Deny Tool / Keep Working / Force Stop / Additional Context
```

最重要的区分是：

> Event 说明“发生了什么”，Handler 说明“由谁处理”，Gate 说明“处理结果有没有控制权”。

---

## 2. 核心源码地图

### 2.1 独立 Hook Runtime

```text
crates/codegen/xai-grok-hooks/src/
  event.rs
  config.rs
  discovery.rs
  matcher.rs
  dispatcher.rs
  result.rs
  runner/mod.rs
  runner/command.rs
  runner/http.rs
  trust.rs
```

### 2.2 Session 接线层

```text
crates/codegen/xai-grok-shell/src/session/
  acp_session/hooks.rs
  acp_session_impl/hook_dispatch.rs
  acp_session_impl/stop_gate.rs
  acp_session_impl/tool_calls.rs
  acp_session_impl/run_loop.rs
  acp_session_impl/hooks_plugins.rs
  compaction.rs
```

### 2.3 来源与协议适配

```text
crates/codegen/xai-grok-shell/src/util/hooks.rs
crates/codegen/xai-grok-shell/src/extensions/hooks.rs
crates/codegen/xai-grok-agent/src/plugins/hooks_adapter.rs
crates/codegen/xai-grok-config/src/global_hook_sources.rs
```

---

## 3. `HookEventName` 是事件系统的单一事实源

`event.rs` 用 `hook_events!` 宏从一张表生成：

- Enum Variant；
- Deserialize 与别名解析；
- Canonical Display；
- `ALL` 稳定顺序；
- 每个事件的 `EventTraits`。

这避免新增事件时只改反序列化、忘记改 Matcher 或 Hub 转发策略。

表中每一行都声明：

```text
display + aliases + (gate, matcher, hub_forward)
```

因此事件名不只是字符串，而是携带运行语义的 Typed Key。

---

## 4. 完整事件表

| Event | Gate | Matcher | Hub | 主要时机 |
| --- | --- | --- | --- | --- |
| `SessionStart` | Observe | Tested | 是 | Session 建立/恢复 |
| `UserPromptSubmit` | Observe | Ignored | 是 | 真人 Prompt 提交 |
| `PreToolUse` | Tool | Tested | 否 | Tool Permission 之前 |
| `PostToolUse` | Observe | Tested | 是 | Tool 成功后 |
| `PostToolUseFailure` | Observe | Tested | 是 | Tool Runtime 失败后 |
| `PermissionDenied` | Observe | Tested | 是 | Permission 拒绝后 |
| `Stop` | Stop | Ignored | 是 | 顶层 Agent 准备结束 |
| `StopFailure` | Observe | Tested | 是 | Turn 因 API 错误结束 |
| `Notification` | Observe | Tested | 是 | 需要用户关注的通知 |
| `SubagentStart` | Observe | Tested | 是 | 子 Agent 启动 |
| `SubagentStop` | Stop | Tested | 是 | 子 Agent 准备结束 |
| `SubagentEnd` | Stop | Tested | 是 | `SubagentStop` 兼容别名 |
| `PreCompact` | Observe | Tested | 是 | 压缩前 |
| `PostCompact` | Observe | Tested | 是 | 压缩后 |
| `SessionEnd` | Observe | Tested | 是 | Session 关闭 |

`PreToolUse` 不转发 Hub，是因为它必须在本地关键路径上得到确定的 Gate 结果；客户端 Gate 另走显式 Reverse RPC。

---

## 5. 三种 Gate 语义

### 5.1 Observe

Runner 会执行，但输出不具有控制权。

- Exit 0：Success；
- 非 0：Failed；
- 不会 Deny Tool；
- 不会让 Agent 继续工作。

### 5.2 Tool Gate

只用于 `PreToolUse`：

- `allow`：继续；
- `deny`：Tool 不执行；
- Timeout、Crash、Malformed：记录失败，但 Fail Open。

### 5.3 Stop Gate

用于 `Stop` 与 `SubagentStop`：

- Block：不允许当前结束，Agent 继续；
- Additional Context：给 Agent 新反馈并继续；
- `continue: false`：强制结束，覆盖 Block；
- 执行失败：Fail Open，让 Agent 正常结束。

这里最反直觉的是：Stop Hook 的“Block”阻止的是停止，不是阻止继续。

---

## 6. Event Alias 与 Canonicalization

事件接受 PascalCase、snake_case 和部分兼容别名，例如：

```text
PreToolUse
pre_tool_use
preToolUse
beforeShellExecution
beforeMCPExecution
beforeReadFile
```

`SubagentEnd` 是保留的独立 Variant，但：

```rust
SubagentEnd.canonical() == SubagentStop
```

这样旧配置可以 Round-trip，Dispatch 又不会把同一语义分成两个世界。

---

## 7. `HookPayload` 是事件自己的数据契约

公共 Envelope 不使用一个巨大的可选字段 Struct，而是 Flatten 一个 Untagged Enum：

```text
HookEventEnvelope
  hookEventName
  sessionId
  cwd
  workspaceRoot
  timestamp
  transcriptPath?
  clientIdentifier?
  promptId?
  permissionMode?
  + HookPayload fields
```

优点是：

- Session 元数据统一；
- 每个事件的必填字段由 Rust 类型约束；
- Wire JSON 保持扁平，兼容外部 Hook；
- Fire Site 不需要手写公共字段。

---

## 8. Tool Hook Payload 保存的不是“展示字符串”

`PreToolUse` 包含：

```text
toolName
toolUseId
toolInput
toolInputTruncated
subagentType?
```

`PostToolUse` 还包含：

```text
toolResult
toolResultTruncated
durationMs?
isBackgrounded
```

`PostToolUseFailure` 保存 Runtime Error，`PermissionDenied` 则表示 Tool 根本没执行。

这三者不能合并：逻辑成功、执行失败、授权拒绝是不同阶段的事实。

---

## 9. Meta-tool 必须暴露真正目标名

模型可能调用的是 `use_tool` 或外部 MCP Dispatcher，但 Hook Matcher 应看见实际目标：

```text
模型 Wire Name: use_tool
Resolved Target: github__create_issue
Hook toolName: github__create_issue
```

`tool_calls.rs` 从 `dispatch_target_name` 构造 `resolved_tool_name`；后续 `PreparedToolCall::hook_tool_name()` 继续保持这个规则。

否则按 MCP Tool 名编写的安全 Hook 永远匹配不到。

---

## 10. Payload 大小保护

`truncate_payload` 将单个 `toolInput` 或 `toolResult` 限制为 128 KiB。

处理方法：

1. 先序列化 JSON；
2. 未超限则保留原始 Typed JSON；
3. 超限时寻找 UTF-8 字符边界；
4. 截成字符串并追加 `[truncated]`；
5. 同时设置对应 Boolean 标志。

所以超限后数据类型可能从 Object 变成 String。Hook 必须先检查 `toolInputTruncated`，不能盲目按原 Schema 读取。

---

## 11. Stop Payload 是一张“为何还不能结束”的运行态快照

顶层 `Stop` 除了最后一条 Assistant Message，还包含：

- 当前 Shell/Monitor 后台任务；
- 活跃 Subagent；
- Session Scheduler 与 `/loop` 任务；
- `stopHookActive`；
- `reason`。

每段自由文本最多 1000 字符，避免一个任务描述撑爆 Hook stdin。

这允许 Hook 实现诸如：

> 如果还有后台测试或子 Agent 在跑，就让 Agent 继续等待，而不是提前宣布完成。

---

## 12. `match_value()` 把不同 Payload 投影成统一选择键

不同事件的 Matcher 对象不同：

| Payload | Match Value |
| --- | --- |
| Tool 事件 | `toolName` |
| Notification | `notificationType` |
| Subagent 事件 | `subagentType` |
| SessionStart/Compact | `source` |
| SessionEnd | `reason` |
| StopFailure | Error Kind |
| Stop/UserPromptSubmit | 无，Matcher 被忽略 |

新增 Tested 事件时，如果忘记为它提供 Match Value，显式 Match 会退化为 Fire-all，因此源码用 Exhaustive Match 降低遗漏风险。

---

## 13. JSON 配置如何变成 `HookSpec`

外部格式是：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bin/check.sh",
            "timeout": 5,
            "env": { "POLICY": "strict" }
          }
        ]
      }
    ]
  }
}
```

解析分三层：

```text
HooksMap
  Event -> Vec<MatcherGroup>
MatcherGroup
  matcher + Vec<RawHandler>
RawHandler
  type + command/url + timeout + env
```

验证后才得到可执行的 `HookSpec`。

---

## 14. 当前 Handler 只有 Command 和 HTTP

`HandlerType` 只有：

```rust
Command
Http
```

配置里的 `type: "prompt"` 会产生 `UnsupportedHandlerType`，不会调用模型。

因此要区分：

- `UserPromptSubmit`：Hook Event；
- `promptId`：Envelope 关联字段；
- Prompt：Agent 输入概念；
- Prompt Hook Handler：当前不存在。

这是阅读 Hook 代码时最容易被名称带偏的地方之一。

---

## 15. JSON 与 TOML 的错误隔离不同

JSON `HooksMap::from_value`：某个事件 Group 结构损坏，会使该来源解析失败。

TOML 配置层 `from_toml_value`：损坏事件被跳过，其他事件继续加载。

共同点：未知事件名会进入 `skipped_events` 并警告，而不是让整个应用无法启动。

这是“配置错误可见”与“其他 Hook 仍可用”的折中。

---

## 16. `HookSpec` 是验证后的执行计划

关键字段：

| 字段 | 作用 |
| --- | --- |
| `name` | 稳定标识、UI、禁用表与 Telemetry |
| `event` | Registry 索引和 Gate 语义 |
| `handler_type` | Command 或 HTTP |
| `configured_matcher` | 原始展示/持久化形式 |
| `matcher` | 已编译运行形式 |
| `enabled` | Spec 自身开关 |
| `command/url` | 已扩展执行目标 |
| `command_raw/url_raw` | 不泄露 Secret 的展示形式 |
| `timeout_ms` | 单 Hook Deadline |
| `source_dir` | 相对命令解析基准 |
| `extra_env` | 用户/插件注入环境 |
| `layer` | Provenance 与 Authority |

Raw 与 Expanded 同时保留，是为了执行正确和显示安全可以兼得。

---

## 17. Timeout 默认值取决于 Gate

```text
普通 Hook 默认：5 秒
Stop Gate 默认：600 秒
```

Stop Hook 常运行 Build/Test。若也只有 5 秒，它会频繁 Timeout，而 Timeout 又 Fail Open，等于验证策略被静默绕过。

显式 `timeout` 单位是秒，内部使用饱和乘法转毫秒，避免极端配置整数溢出。

---

## 18. Hook Name 是结构化生成的

典型名称：

```text
global/tool-logger:post_tool_use[0].hooks[0]
project/safe-shell:pre_tool_use[0].hooks[0]
plugin/my-plugin/hooks:pre_tool_use[0].hooks[0]
agent:reviewer/...
```

名称编码来源、事件、Group 序号、Handler 序号，用于：

- 精确禁用；
- Deny 归因；
- UI 标注；
- Telemetry；
- Provenance 回退分类。

---

## 19. Hook 来源不是一张目录列表

Session 的文件来源包括：

- Grok 全局 Hook 目录；
- Grok 固定 Registry Slot 之外的配置来源；
- `hooks-paths` 自定义来源；
- 项目 `.grok/hooks`；
- 可选 Claude 全局/项目 Settings；
- 可选 Cursor 全局/项目 Hooks；
- Config Layer 内嵌 Hook；
- Plugin Hook 文件或 Inline Hook；
- Agent Definition Hook；
- ACP Client 注册 Hook。

最后一类不进入文件 `HookRegistry`，它有独立的 `ClientHooks` Map。

---

## 20. Global Hook Source 的文件系统防护

`xai-grok-config/global_hook_sources.rs` 负责：

- 固定 Hook 目录与 Registry File Slot；
- 检查 Symlink Component；
- 检查 Hook JSON 不是 Symlink；
- 拒绝 Hard Link Hook 文件；
- 只枚举 Direct Child JSON；
- 计算 Sandbox 需要 Pin 的祖先 Mountpoint。

这里保护的是“加载了哪个文件”。Runner 的 Sandbox/Process Scope 保护的是“文件运行后能做什么”。两者不是同一层。

---

## 21. Project Hook 受 Folder Trust 控制

`HookSourcePaths::as_sources(include_project)` 直接决定是否把项目来源交给 Discovery。

```text
trusted = false
  Global Sources -> load
  Project Sources -> empty

trusted = true
  Global Sources -> load
  Project Sources -> load
```

项目 Hook 与 Repo-local MCP/LSP 共用 Folder Trust，而不是维护另一套 Hook Trust。

这是合理的，因为仅仅打开陌生仓库不应该自动执行仓库内脚本。

---

## 22. Registry 是 Session 级快照

`HookRegistry` 按 Event 保存 `Vec<HookSpec>`。

默认情况下，磁盘修改只在新 Session 生效；但 `/hooks reload` 可以在会话中重新：

1. 解析 Git Root；
2. 重新求 Folder Trust；
3. 读取最新 Config Layer；
4. 重做文件发现；
5. 重新追加活跃 Plugin Hook；
6. 原子替换 Session Registry；
7. 通知 UI `HooksChanged`。

它是 Point-in-time Snapshot，不是 File Watcher。

---

## 23. Source 顺序决定 Authority

`assemble_hooks` 先放 Config Layer，再放文件来源。

文件来源内部：

```text
Global before Project
```

Dedup 采用 First-wins，所以调用者必须先放高 Authority Spec。

Plugin Hook 在 Reload 后 Append；Agent Hook 也通过适配层加入。理解顺序时应找 Registry 的组装点，而不是只看目录遍历。

---

## 24. Dedup Key 为什么不包含 Name

去重键是：

```text
(canonical event, command_raw, url_raw, configured_matcher)
```

不包含：

- 自动生成 Name；
- Timeout；
- Extra Env。

设计目标是同一实际 Hook 经兼容来源或多层配置出现时只执行一次。First-wins 让高 Authority 版本的 Timeout/Env 一并保留。

`SubagentEnd` 和 `SubagentStop` 先 Canonicalize，因此兼容拼写也能去重。

---

## 25. Plugin Hook 不是第二套引擎

Plugin Adapter 做四件事：

1. 预过滤不支持的 Event 并产生 Warning；
2. 仍调用统一的 `parse_hook_file`；
3. 注入 `GROK_PLUGIN_*` 和兼容变量；
4. 标记 `HookProvenance::Plugin` 并加 Namespace。

最终仍生成普通 `HookSpec`，由同一个 Dispatcher 和 Runner 执行。

这避免 Plugin Hook 与本地 Hook 的 Matcher、Timeout、Gate 语义漂移。

---

## 26. Matcher 有三种形式

### Match All

```text
缺失 matcher
""
"*"
```

### Simple Exact

只含 `[A-Za-z0-9_|]`：

```text
read_file
read_file|list_dir
Bash|Edit
```

每一项精确匹配，不是 Regex。

### Regex

包含其他字符时作为未 Anchor 的 Regex：

```text
run_.*
^run_.*$
```

是否 Anchor 完全由作者决定。

---

## 27. 为什么 `a|b` 不是普通 Regex

如果简单地包装为 `^a|b$`，Anchor 只约束两端的部分分支，容易意外 Over-match。

源码把 Simple Form 按 `|` 拆成 Exact Name Set：

```text
read_file|list_dir
  -> exact(read_file) OR exact(list_dir)
```

这是兼容性与安全性都更稳定的选择。

---

## 28. 外部 Tool Alias 如何参与 Matcher

`HookMatcher` 使用共享 Tool Name Registry：

- `Bash` 可映射 Grok Terminal Tool；
- `Read` 可映射 `read_file`；
- Regex 也会测试 Tool 的 Claude Alias。

因此迁移来的 Hook 不必立即重写 Matcher。

注意：Simple Matcher 是双向展开后的 Exact Match，不是字符串模糊匹配。

---

## 29. Matcher 的 Fail-open 与 Fail-closed 不矛盾

运行时规则：

```text
missing matcher OR missing match value -> fire
```

这是 Matcher 应用的 Fail-open。

但无效 Regex：

- 初次解析时该 Group 被拒绝；
- Wire Restore 后重新编译失败时装入 `Matcher::never()`。

这是 Fail-closed，防止原本想匹配少量 Tool 的 Deny Hook 意外变成 Match-all。

二者针对的异常不同。

---

## 30. Disabled Hook 与 Matcher Miss 的可观测性不同

Dispatcher 的 `eligible_or_record_skip`：

- `enabled=false` 或名字在 disabled-hooks 中：记录 `Skipped`；
- Matcher 不命中：不产生 Result。

原因是 Disable 是用户操作，需要 UI 可解释；Matcher Miss 是正常筛选，不应制造大量噪声。

---

## 31. Command Hook 的输入协议

Runner 将完整 Envelope 序列化为单个 JSON，写到子进程 stdin。

Hook 应读取 EOF，而不是假设有换行：

```sh
payload="$(cat)"
```

`GROK_HOOK_DEBUG=1` 只记录 Payload 字节数，不直接把敏感正文写入普通日志。

---

## 32. Direct Exec 与 `sh -c`

命令含下列特征时走 Shell：

- 空格；
- Pipe、`&&`、Redirect、Semicolon；
- `$` 环境变量；
- 开头 `~`。

否则按可执行文件路径直接启动，相对路径基于 Hook 文件的 `source_dir`。

Direct Exec 减少不必要 Shell 解释，Shell Branch 则兼容迁移来的命令串。

---

## 33. 未解析环境变量会在 Spawn 前失败

Shell Command 中的 `$VAR`/`${VAR}` 会检查是否来自：

- Runner 固定注入；
- Hook `extra_env`；
- 父进程环境；
- 命令中的局部赋值。

带默认值或替换修饰的 `${VAR:-x}` 等形式不报错。

相比让 Shell 把缺失变量变成空字符串后返回模糊的 127，这能给出明确配置错误。

---

## 34. Runner 身份环境变量不能被伪造

Runner 总是设置：

```text
GROK_HOOK_EVENT
GROK_HOOK_NAME
GROK_SESSION_ID
GROK_WORKSPACE_ROOT
CLAUDE_PROJECT_DIR
```

解析配置时会移除 `env` 中同名键；Spawn 时又保证用户/插件 Env 先注入、Runner 身份变量最后覆盖。

这是一种双重防御：警告作者，同时保证运行时 Authentic Identity。

---

## 35. Hook 进程生命周期属于 Session

Command 设置 `kill_on_drop(true)`，还尝试：

1. 创建 Process Group；
2. Attach Child；
3. 注册到 Session `ProcessScope`；
4. Timeout 或异常时 Kill Process Group。

`kill_on_drop` 只能可靠处理直接 Child，Process Group 才能覆盖 Grandchild。

如果 Session Scope 已关闭，注册会拒绝并杀掉 Child，Runner 不再向“尸体进程”写 stdin。

---

## 36. stdin 与 wait 必须并发

Runner 在 Timeout 内同时：

```text
write_all(stdin_json)
child.wait_with_output()
```

如果先完整写 stdin，再开始读取 stdout/stderr，一个不读 stdin、却持续输出的 Hook 可能互相堵塞 Pipe。

因此 Timeout 必须包围整个并发 I/O，而不是只包围 `wait()`。

---

## 37. Command 输出上限

stdout 与 stderr 各最多保留 64 KiB，超出后追加 `[truncated]`。

注意两个不同预算：

```text
Envelope 内 Tool Payload：128 KiB
Hook Process stdout/stderr：64 KiB
```

前者控制输入，后者控制外部进程输出和 UI/日志内存。

---

## 38. PreToolUse Command 结果优先级

Tool Gate 先尝试解析 stdout JSON：

```json
{"decision":"allow"}
{"decision":"deny","reason":"unsafe command"}
```

规则：

1. JSON Deny 在任意 Exit Code 上都生效；
2. JSON Allow 通常生效；
3. 但 Exit 2 会覆盖 JSON Allow，仍 Deny；
4. 无可用 JSON时，Exit 0 Allow；
5. Exit 2 Deny；
6. 其他 Exit Code 是 Failed，Dispatcher Fail Open。

Exit 2 是显式 Gate Signal，不等价于普通脚本崩溃。

---

## 39. Stop Command 的结果词汇不同

stdout 可返回：

```json
{
  "decision": "block",
  "reason": "tests are still failing",
  "continue": true,
  "hookSpecificOutput": {
    "additionalContext": "Run the integration suite next."
  }
}
```

或强制结束：

```json
{
  "continue": false,
  "stopReason": "budget exhausted"
}
```

无 JSON 时：

- Exit 0：允许停止；
- Exit 2：阻止停止，stderr 作为反馈；
- 其他 Exit：Failed，Fail Open。

---

## 40. HTTP Hook 的请求协议

HTTP Runner：

1. 展开 URL 环境变量；
2. 做 SSRF 校验；
3. POST Envelope JSON；
4. 设置 `Content-Type: application/json`；
5. Observe 只看 2xx；
6. Gate 根据 Response Body 解析同一 Decision JSON。

因此 Command 和 HTTP 共享 Gate Vocabulary，而不是两套决策模型。

---

## 41. HTTP Hook 的 SSRF 边界

只允许 HTTPS，并拒绝：

- RFC1918 私网；
- Link-local 与云 Metadata；
- CGNAT；
- Unspecified Address；
- IPv6 ULA/Link-local；
- IPv4-mapped IPv6 私网。

Loopback 被允许，方便本地开发服务。

Redirect 被完全禁用，否则初始安全 URL 可以跳转到内网。

源码明确记录一个 Known Gap：验证 DNS 后，请求层再次解析 Host，仍存在 DNS Rebinding Window。

---

## 42. HTTP 错误为什么不能直接 Display

展开后的 URL 可能包含 `env` Secret。`reqwest::Error::Display` 常附带 Request URL。

Runner 使用 `without_url()`，对外展示优先采用 `url_raw`，防止 Secret 进入：

- `HookRunResult::Failed`；
- Pager Scrollback；
- 普通日志。

`HttpInfo.url` 仍用于诊断，但用户展示必须优先 Raw URL。

---

## 43. Dispatcher 为什么顺序执行文件 Hook

`dispatch_pre_tool_use`、`dispatch_stop`、`dispatch_non_blocking` 都按 Registry 顺序 Await 每个 Spec。

好处：

- Authority/Config 顺序稳定；
- Side Effect 顺序可预测；
- 第一个 Tool Deny 的归因确定；
- Stop Block 与 Context 的聚合顺序确定；
- Telemetry 与 Scrollback 可复现。

代价是多个慢 Hook 延迟相加，因此单 Hook Timeout 很关键。

---

## 44. PreToolUse 是 First Deny Wins

执行逻辑：

```text
for matching hook in registry order:
  disabled -> Skipped
  allow -> record Success, continue
  failed -> record Failed, continue
  deny -> record Blocked, return Deny immediately

end -> Allow
```

后面的 Hook 不会在已有 Deny 后继续执行，因为 Tool 已确定不能运行。

---

## 45. Fail Open 是显式 Threat Model 选择

文件 Hook 的 Timeout、Crash、Command Not Found、缺失 Env、Malformed Output 都不会阻止 Tool 或阻止 Agent 停止。

源码给出的理由是：Grok 已运行在受保护环境中，Hook 不是唯一安全边界；Fail Closed 会因为无关配置故障大面积阻塞正常工作。

因此：

> Hook 可强化策略，但不能替代 Permission、Managed Policy 与 OS Sandbox。

若把关键安全性只寄托在 Hook 上，就误读了系统边界。

---

## 46. Stop Dispatch 为什么不短路

每个 Stop Hook 都执行，收集：

- 所有 Block Reason；
- 所有 Additional Context；
- 第一个 Force Stop；
- 每个 Hook 的 Run Result。

这样模型一次得到完整修正清单，而不是每继续一轮才发现下一个问题。

`continue: false` 的第一个结果胜出，并覆盖 Block。

---

## 47. `StopDispatchResult` 的决策公式

```text
wants_continuation =
  prevent_continuation is None
  AND (blocks not empty OR additional_context not empty)
```

三种终态：

| 聚合结果 | Agent 行为 |
| --- | --- |
| Force Stop | 立即允许结束 |
| Block/Context | Keep Working |
| 无信号或全失败 | 正常结束 |

---

## 48. Stop Feedback 怎样进入模型

`format_stop_feedback` 生成：

```text
Stop hook feedback:
- tests are failing
- generated files are dirty

Run the integration suite next.
```

每条反馈最多 10,000 字符。`run_stop_gate` 返回：

```rust
StopGateDecision::KeepWorking { feedback }
```

Agent Loop 把它作为新反馈继续下一次采样。

这就是 Hook 从外部脚本反向影响下一轮模型推理的关键接点。

---

## 49. 为什么 Stop 最多继续 8 次

`MAX_STOP_HOOK_CONTINUATIONS_PER_TURN = 8`。

达到上限后：

- 不再运行文件 Hook；
- 不再请求 Client Hook；
- 不再发送 Observe Notification；
- 标注已达到限制；
- 强制结束 Turn。

它防止永远无法满足的 Hook 条件形成 Agent-Hook 无限循环。

`stopHookActive` 告诉 Hook 当前已是同 Turn 的后续尝试，Hook 可以主动避免重复阻塞。

---

## 50. Session End 的 Stop 是 Observe-only 特例

Session 真正关闭时已没有下一轮可继续，但仍希望兼容 Stop 审计脚本。

`dispatch_session_end_stop`：

- 使用 Stop 解析语义运行；
- 最多给整个 Dispatch 5 秒 Shutdown Budget；
- 丢弃 Block 决策；
- 将 UI 中的 `Blocked` 降级为 `Success`；
- Subagent 不走这条顶层 Session-end Stop。

这防止“决策已无法生效，Telemetry 却声称成功阻止”的假象。

---

## 51. Client Hook 的注册模型

客户端在 `session/new` Meta 中发送：

```json
{
  "x.ai/hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hookCallbackIds": ["policy-1"],
        "timeout": 10
      }
    ]
  }
}
```

Agent 只保存 Callback ID、Matcher 和 Timeout，不执行客户端代码。

Reconnect：

- Meta 含 `x.ai/hooks`：替换，空 Map 表示显式清除；
- Meta 不含：保留 Live Registration。

---

## 52. Client Observe 与 Gate 使用不同 RPC

Observe Event：

```text
x.ai/hooks/event
fire-and-forget notification
```

Gate Event：

```text
x.ai/hooks/run
awaited reverse request
```

前者没有 Decision，后者必须收到 Response 或 Timeout。

把两条路径分开，可以避免观察者意外获得控制权，也避免普通 Event 阻塞 Agent Hot Path。

---

## 53. Client Gate 并发，文件 Hook 串行

同一 Client Gate 的多个 Callback 使用 `FuturesUnordered` 并发请求，每个 Callback 有独立 Timeout。

原因：客户端 Callback 彼此独立，一个慢 Callback 不应让其他 Callback 排队。

但结果处理仍有确定性：

- PreToolUse：完成顺序中的第一个 Deny 生效；
- Stop：先并发收齐，再按注册顺序聚合，保证 First Force-stop 稳定。

同一 Callback ID 注册在多个 Group 时只 Dispatch 一次。

---

## 54. Client Gate 也 Fail Open

以下情况都映射为默认 Continue：

- Malformed Response；
- Transport Error；
- Timeout；
- Unknown Decision。

但 Telemetry 分别记录：

```text
Malformed
TransportError
TimedOut
UnknownDecision
```

Fail Open 不等于吞掉错误；运行控制与可观测性是两个维度。

---

## 55. Client Timeout 与文件 Hook 对齐

```text
PreToolUse Client 默认：30 秒
Stop Client 默认：600 秒
单 Group 最大：600 秒
```

Client Tool Gate 比文件普通 Hook 更长，因为网络/IPC 往返需要余量；Stop 仍给验证任务 10 分钟。

非正数、NaN、Infinity Timeout 被丢弃并回退默认值。

---

## 56. 文件与客户端 PreToolUse 的顺序

`prepare_tool_call` 的关键顺序：

```text
Typed Parse
  -> Plan Edit Gate
  -> Tool Call UI Start
  -> Build PreToolUse Envelope
  -> File Hook Gate
  -> Client Hook Gate
  -> Permission Decision
  -> PreparedToolCall
```

所以 PreToolUse 在一般 Permission Prompt 之前运行。

若文件 Hook Deny，Client Gate 不再调用；若文件允许，再询问 Client。

Plan Mode 的硬编辑 Gate 更早，拒绝时不会触发 PreToolUse。

---

## 57. Hook Deny 怎样变成 Tool Result

`deny_tool` 同时完成：

1. 记录 `HookBlocked` Telemetry；
2. 调用 `handle_tool_not_executed`；
3. 生成与 Tool Use ID 配对的未执行结果；
4. 向 UI 发送 Hook Annotation；
5. 返回 `ToolLoop::HookDenied`。

模型不会只看到一个 UI Toast；它会在 Conversation 中看到 Tool 未执行及 Reason，从而能调整下一步。

---

## 58. PermissionDenied 为什么是 Observe Event

Permission 系统已经作出拒绝，Hook 不应推翻用户或 Managed Policy。

因此 `PermissionDenied` 用于：

- 审计；
- 通知；
- 外部记录；
- 统计。

它在 `handle_tool_not_executed` 后 Dispatch，不能将 Deny 改为 Allow。

Cancelled 和 Followup 走不同控制路径，并不等同于 Policy/User Reject。

---

## 59. PostToolUse 与 PostToolUseFailure 的边界

Tool Runtime 返回 `Ok(ToolRunResult)` 时触发 `PostToolUse`，即使结构化 Output 内部表达某种业务失败，也仍属于成功返回。

Runtime `Err` 才触发 `PostToolUseFailure`。

顺序上，成功 Tool 先完成主要 Result 处理，再 Dispatch Post Hook；失败则先写 Error Tool Result，再 Dispatch Failure Hook。

Hook 是 Post-flight Observer，不参与篡改已经写回的 Tool Output。

---

## 60. Observe Dispatch 的 Inert Fast Path

`dispatch_hook` 先调用 `hook_event_active`：

```text
没有 Registry 且该 Event 没有 Client Hook
  -> 不构造 Payload/Envelope
  -> 不做序列化
  -> 直接返回
```

有任意文件 Registry 时这个检查比较粗，真正是否存在该 Event 的 Spec 由 Dispatcher 再判断。

Stop Hot Path 使用更精确的 `has_enabled_hooks_for_canonical`，避免无 Stop Hook 时构造昂贵的后台任务快照。

---

## 61. `fire_hook` 只适用于 Observe 通知语义

`fire_hook` 做：

```text
make_hook_envelope
notify_client_hooks
return envelope
```

Stop Turn Gate 不使用它，而直接 `make_hook_envelope`，因为 Client Stop 必须走 Awaited `x.ai/hooks/run`，不能提前 Fire-and-forget。

Force Stop 是特例：文件 Hook 已决定结束后，跳过 Client Gate，但仍发送 Observe Notification 让客户端知道 Turn End。

---

## 62. Hook 执行结果的三条输出路径

每次文件 Hook Dispatch 后通常产生：

### UI

`HookExecution` 携带每个 Hook 的 Success、Skipped、Failed、Blocked 和 Duration。

### Telemetry

`HookExecuted` 记录 Event、Tool Name、Duration 与 Outcome；Block 另有 `HookBlocked`。

### Agent Control

- Tool Deny -> `ToolLoop::HookDenied`；
- Stop Block/Context -> `KeepWorking`；
- Force Stop -> `AllowStop`；
- Observe -> 无控制影响。

不要把 UI Result 当成控制真相；控制真相由 Typed Decision 返回值传播。

---

## 63. Notification Hook 不是每个 UI Update 都触发

`notification_hook_for_update` 只选择用户注意事件，例如：

- Diff Review 请求；
- Auto Recovery Exhausted；
- Retry Exhausted/Failed。

Hook Scrollback、Retry In-progress、Config Change 等高频内部更新被过滤，避免 Notification Hook 递归触发自己或制造事件风暴。

---

## 64. Hub Forward 与本地 Gate 是两条扩展边界

`hub_hook_kind` 对允许转发的事件生成：

```text
hook.session_start
hook.post_tool_use
hook.stop
...
```

`PreToolUse` 的 `hub_forward=false`。

Hub Turn/Tool Hook 还会把 Shell 内部更细的 Outcome 压缩成协议的 Completed/Cancelled/Error 或 Success/Error/Cancelled。

这是一种 Anti-corruption Layer：内部状态机不直接泄漏到外部协议。

---

## 65. Hook 的安全边界总结

| 风险 | 防线 |
| --- | --- |
| 陌生 Repo 自动执行脚本 | Folder Trust |
| Source Symlink/Hardlink 替换 | Global Source Validation |
| Matcher 拼写扩大匹配 | Invalid Regex 拒绝/Never |
| 身份 Env 被伪造 | Reserved Key Strip + Last-write Wins |
| 子进程逃逸 Session | ProcessGroup + ProcessScope |
| Hook 无限运行 | Per-hook Timeout |
| Stop 无限继续 | 每 Turn 8 次上限 |
| HTTP 访问内网 | HTTPS + DNS/IP Check + No Redirect |
| URL Secret 泄漏 | Raw URL Display + `without_url` |
| 巨大 Payload/Output | 128 KiB / 64 KiB 截断 |
| Hook 故障瘫痪 Agent | Fail Open |

这些防线分属加载、匹配、执行、网络、生命周期和 Agent Loop，不应只看 Runner 一处。

---

## 66. 一次 PreToolUse 的端到端时序

```text
Model emits Tool Call
  -> Session parses Typed Input
  -> resolves underlying Tool Name
  -> builds truncated PreToolUse Payload
  -> File Registry matches specs
       -> Command/HTTP sequentially run
       -> first explicit deny stops chain
  -> Client callback groups match
       -> reverse requests concurrently run
       -> first completed deny blocks
  -> if allowed, Permission evaluates
  -> if permitted, PreparedToolCall enters runtime
```

Deny 时：

```text
Hook Decision
  -> Tool Result: not executed
  -> UI Annotation
  -> Telemetry
  -> Agent receives feedback
```

---

## 67. 一次 Stop Gate 的端到端时序

```text
Sampler says turn can end
  -> choose Stop or SubagentStop
  -> check continuation cap
  -> snapshot last assistant text + background work
  -> build envelope
  -> run all file stop hooks sequentially
  -> file force-stop? allow end + notify client observers
  -> otherwise run client gates concurrently
  -> aggregate in registration order
  -> client force-stop? allow end
  -> blocks/context? format feedback + resample
  -> no signal? end turn
```

这条链说明 Stop Hook 实际是 Agentic Loop 的一个外部收敛判定器。

---

## 68. 常见误解

### 误解一：所有 Hook 都能阻止操作

只有 `PreToolUse`、`Stop`、`SubagentStop` 有 Gate 控制权。

### 误解二：Hook 失败会拒绝 Tool

不会。只有明确 Deny；运行失败 Fail Open。

### 误解三：Stop Block 会终止 Agent

相反，它阻止停止，让 Agent 继续。

### 误解四：`continue: false` 表示继续为 False，所以报错

它是 Force Stop，优先级高于 Block。

### 误解五：Matcher 总是 Regex

Simple Form 是 Exact/Exact List。

### 误解六：`UserPromptSubmit` 意味着存在 Prompt Handler

它只是事件；Handler 仅 Command/HTTP。

### 误解七：项目 Hook 文件存在就会执行

未建立 Folder Trust 时项目来源根本不进入 Discovery。

### 误解八：客户端 Hook 也在本地 Registry

它存在独立 Client Map，通过 ACP Reverse RPC 执行。

### 误解九：Hook 可以代替 Sandbox

Hook 是可失败、Fail-open 的扩展策略，不是最终安全边界。

---

## 69. 推荐源码阅读顺序

第一遍只看语义：

```text
event.rs
result.rs
dispatcher.rs
```

第二遍看加载：

```text
config.rs
matcher.rs
discovery.rs
util/hooks.rs
plugins/hooks_adapter.rs
```

第三遍看执行：

```text
runner/mod.rs
runner/command.rs
runner/http.rs
```

第四遍看 Agent 回接：

```text
tool_calls.rs
acp_session/hooks.rs
stop_gate.rs
hook_dispatch.rs
run_loop.rs
compaction.rs
```

---

## 70. 调试清单

Hook 没运行时依次检查：

1. Event Name 是否被 `parse_key` 接受；
2. Project Folder 是否 Trusted；
3. 文件是否是目录直属、非隐藏的 `.json`；
4. Hook 是否被名字级 Disable；
5. Matcher 是 Exact 还是 Regex；
6. Meta-tool 是否已解析成真实 Tool Name；
7. Handler Type 是否仅为 `command/http`；
8. 相对 Command 是否基于正确 `source_dir`；
9. 所需 Env 是否存在；
10. Timeout 是否过短；
11. Gate 是否只返回了普通失败而不是明确 Deny；
12. 查看 `HookExecution` 与 `HookExecuted`，区分 Skipped、Miss、Failed。

Stop Hook 不继续时再检查：

1. 是否返回 `decision: block`；
2. Exit 2 的 Reason 是否写在 stderr；
3. 是否意外返回 `continue: false`；
4. 是否已达到 8 次 Continuation 上限；
5. 是否在 Session-end Observe-only 阶段；
6. 是否 Timeout 后 Fail Open。

---

## 71. 可动手验证的实验

### 实验一：Matcher Alias

分别配置 `Bash`、`run_terminal_command`、`^Bash$`，观察同一个 Terminal Tool 是否命中。

### 实验二：Exit Code Ladder

让 PreToolUse 依次返回：

```text
exit 0 + empty
exit 2 + empty
exit 1 + empty
exit 1 + JSON deny
exit 2 + JSON allow
```

记录最终 Tool Decision。

### 实验三：Stop 聚合

三个 Hook 分别返回 Block、Additional Context、Force Stop，验证 Force Stop 覆盖但每个 Run Result 仍可观测。

### 实验四：Payload Truncation

构造超过 128 KiB 的 Tool Args，检查值变为 String 且 Truncated Flag 为 True。

### 实验五：Process Reaping

Hook 启动 Grandchild 后 Timeout，确认 Process Group 被整体回收。

### 实验六：Client Completion Order

让两个 Stop Callback 乱序返回，确认最终 First Force-stop 仍按注册顺序决定。

---

## 72. 设计上最值得学习的模式

### Typed Event Traits

把 Gate、Matcher、Forward Policy 与 Event Enum 放在同一事实表。

### Parse Once, Execute Many

启动/Reload 时验证成 `HookSpec`，Hot Path 使用编译后的 Matcher 和 Registry。

### Separate Observation from Control

Observe、Tool Gate、Stop Gate 用类型和不同 Dispatcher 明确分开。

### Stable Aggregation after Concurrent I/O

Client Callback 并发等待，但按注册顺序聚合需要稳定优先级的结果。

### Fail Open with Rich Evidence

控制路径继续，但 UI、Telemetry、Error Result 保留失败原因。

### One Envelope, Multiple Transports

Command stdin、HTTP POST、ACP Reverse RPC 共用同一事件数据契约。

---

## 73. Glossary

### ACP

Agent Client Protocol。Session 与客户端交换请求、通知和扩展方法的协议。

### Additional Context

Stop Hook 返回给 Agent 的补充上下文；即使没有 Block，也会促使 Agent 继续一轮。

### Alias

兼容名称。可指 Event 拼写别名，也可指 Claude Tool Name 与 Grok Tool Name 的映射。

### Anti-corruption Layer

隔离内部模型与外部协议差异的转换层，例如把九种内部 Tool Outcome 压缩成三种 Hub Outcome。

### Authentic Identity

Runner 保证真实注入且不允许 Hook 配置覆盖的 Session、Event、Hook Name 等身份字段。

### Block

对 PreToolUse 表示拒绝 Tool；对 Stop 表示拒绝“停止”，要求 Agent 继续。必须结合 Gate 阅读。

### Callback ID

客户端注册 Hook 时提供的标识。Agent 通过它把 Reverse Request 路由给对应客户端逻辑。

### Canonical Event

事件的规范形式，例如 `SubagentEnd` 在 Dispatch 时归一为 `SubagentStop`。

### Command Hook

将 Envelope JSON 写入本地子进程 stdin，并根据 stdout、stderr 与 Exit Code解释结果的 Handler。

### Dedup

按规范事件、原始 Command/URL 与 Matcher 去除多来源重复 Hook，较早来源胜出。

### Deny

Tool Gate 的显式拒绝决定。只有明确 Deny 才能阻止 Tool，普通运行失败不能。

### Dispatcher

选择符合条件的 Hook、调用 Runner、聚合结果并实施 Gate 规则的组件。

### Envelope

统一 Hook 事件消息，由 Session 公共元数据与 Event-specific Payload 组成。

### Event

Session 生命周期中的语义时点，如 PreToolUse、PostCompact 或 Stop。

### Event Traits

与事件绑定的 Gate Kind、Matcher Policy 和 Hub Forward Policy。

### Exact Matcher

对 Tool 名做完整相等比较的 Matcher；Simple Form 与 `|` 列表使用此模式。

### Fail Closed

异常时采取更限制性的结果。无效 Matcher Restore 成 `Never` 是例子。

### Fail Open

Hook 运行异常时不阻止 Tool 或 Agent Stop，但记录失败证据。

### Fire-and-forget

发送通知后不等待响应。Client Observe Hook 使用这种方式。

### Folder Trust

用户对工作目录的信任判断，控制项目 Hook、Repo-local MCP/LSP 等主动配置是否加载。

### Force Stop

Stop Hook 返回 `continue: false`，强制 Agent 结束并覆盖其他 Block。

### Gate

允许 Hook 输出影响控制流的检查点。当前有 Tool Gate 与 Stop Gate。

### Gate Kind

`Observe`、`Tool`、`Stop` 三种输出解释模式。

### Handler

实际执行 Hook 的机制。当前源码支持 Command 和 HTTP。

### Hook

订阅某个 Event、可带 Matcher，并由 Handler 执行的外部扩展规则。

### Hook Origin

Hook 的来源分类，如 Managed、User File、Project File、Plugin 或 Agent。

### Hook Registry

按 Event 索引的 Session 级 HookSpec 快照。

### Hook Run Result

单次执行的可观测结果：Success、Skipped、Blocked 或 Failed。

### Hook Spec

完成解析和验证、可交给 Dispatcher 执行的 Hook 配置对象。

### Hot Path

对交互延迟敏感且频繁执行的路径，例如每次 Tool Call 前的 PreToolUse。

### HTTP Hook

通过 HTTPS POST Envelope，并从 HTTP Status/Body 解释结果的 Handler。

### Inert Fast Path

没有 Listener 时不构造 Payload、不序列化、不 Dispatch 的快速返回路径。

### Matcher

根据 Tool Name、Notification Type、Subagent Type 等选择 Hook 是否运行的规则。

### Matcher Policy

事件是否真正使用 Matcher。`Ignored` 事件即使配置 Matcher 也始终运行。

### Meta-tool

再次分发到真实目标 Tool 的 Tool，例如 `use_tool`。Hook 应匹配其 Resolved Target。

### Observe Hook

只记录事件、不能改变 Agent 或 Tool 控制流的 Hook。

### Payload

Envelope 中与具体 Event 相关的字段集合。

### Point-in-time Snapshot

某时刻构造后保持稳定的 Registry；磁盘变化需新 Session 或显式 Reload 才生效。

### Process Group

将 Hook 子进程及其后代作为整体发送信号和回收的 OS 进程集合。

### Process Scope

Session 级进程所有权容器；Session 关闭时统一终止已注册进程组。

### Prompt Hook

容易误用的称呼。当前没有 `prompt` Handler；`UserPromptSubmit` 是 Event，`promptId` 是关联字段。

### Provenance

Hook 的权威来源层级，用于分类、显示和 First-wins 组装。

### Raw URL/Command

环境变量展开前的配置文本，用于安全展示和内容去重。

### Regex Matcher

含正则特殊字符的 Matcher，默认不自动 Anchor，并同时测试外部 Tool Alias。

### Reverse Request

由 Agent 反向请求客户端并等待答复的 ACP 调用；Client Gate 使用它取得 Decision。

### Round-trip

数据解析、持久化、再恢复后仍保留原语义和兼容拼写的能力。

### Runner

负责真正执行一个 Hook Handler，并把外部结果转换成 `HookRunnerResult` 的组件。

### Source Directory

Hook 来源文件的目录，是相对 Command Path 的解析基准。

### SSRF

Server-Side Request Forgery。攻击者诱导 HTTP Hook 请求内网、Metadata 或其他受保护地址。

### Stop Gate

Agent 准备结束时运行的控制点，可要求继续、补充上下文或强制结束。

### Stop Hook Active

表示当前已是同一 Turn 被 Stop Hook 要求继续后的再次停止尝试。

### Tool Gate

Tool 执行前的控制点，当前对应 PreToolUse，可 Allow 或 Deny。

### Truncation

按字节预算裁剪 Payload 或进程输出，并保留明确的截断标记。

### Wire Name

协议消息中使用的名称；可能与 Rust Variant、Canonical Display 或真实 Tool Target 不同。

---

## 74. 下一篇建议

下一篇可继续沿 Agent 基础链写：

> 源码精读 18：Agent Configuration Runtime 如何读取多层配置、计算 Authority 与 Override、应用 Managed Policy、兼容环境变量，并把一次配置变更安全传播到 Session、Prompt、Tool、Skill、MCP 和 Hook。

这会把前面多篇反复出现但尚未集中解释的概念串起来：

```text
Config Layer
Provenance
Managed / Requirements / User / Project
Environment Override
Compatibility Config
Live Reload
Effective Configuration
```
