# Apply patch lifecycle：一段补丁怎样从模型文本变成真实文件变化

> 源码基线：`4ee41929eaf4`  
> 本篇重点：freeform tool、grammar、shell interception、parse、verify、preview、path permission、approval cache、Sandbox、escalation、partial success、`AppliedPatchDelta`、事件与模型结果回填。  
> 建议先读：[Tool dispatch](05-tool-dispatch.md)、[Sandbox approval lifecycle](12-sandbox-approval-lifecycle.md)。

## 1. 先说人话：它解决什么问题

模型可以说：

```text
请把 config.rs 里的旧字段改名，再补一个测试。
```

但一句自然语言并不能让程序安全、精确地修改文件。宿主程序至少还要知道：

- 改哪个文件；
- 哪些旧行必须存在；
- 哪些新行要写进去；
- 是新增、更新、删除，还是移动文件；
- 目标路径是否允许写；
- 是否需要用户审批；
- 写到一半失败时，哪些变化已经发生；
- 怎样把成功或失败告诉模型，让它决定下一步。

`apply_patch` 就是模型和文件系统之间的一种结构化编辑协议。

它不是“让 LLM 直接拿到文件系统写权限”，也不只是“把一段 diff 丢给系统 `patch` 命令”。固定源码中，Codex 自己完成：

```text
识别调用
→ 解析自定义 patch grammar
→ 读取文件并验证上下文
→ 预计算变化
→ 判断路径安全与审批
→ 在 Sandbox 中逐项写入
→ 记录实际已提交变化
→ 发 UI 事件
→ 把结果回给模型
```

## 2. 公开协议与固定源码不是同一层

[OpenAI 官方 Apply Patch 文档](https://developers.openai.com/api/docs/guides/tools-apply-patch)把公开工作流描述为：模型产生结构化 patch operation，应用程序在自己的环境中应用它，再以对应 call output 把状态和错误回传，模型可以继续修改或解释结果。

固定提交 `4ee41929eaf4` 中，Codex Core 的具体实现是：

- 注册一个名为 `apply_patch` 的 **Freeform/Custom tool**；
- 用 Lark grammar 约束模型输出格式；
- handler 收到 `ToolPayload::Custom`；
- 成功结果转换成 `CustomToolCallOutput`。

所以阅读时要分清：

```text
公开文档：Apply Patch 作为 Responses API 能力的通用宿主协议
固定源码：Codex 在该版本中用 freeform custom tool 落地这项能力
```

二者的共同核心仍是：模型提出结构化变化，宿主负责执行并回报结果。

## 3. 贯穿案例

假设工作目录里有：

```text
src/config.rs
tests/config_test.rs
old_notes.md
```

模型生成：

```text
*** Begin Patch
*** Update File: src/config.rs
@@
-pub const RETRIES: u8 = 2;
+pub const RETRIES: u8 = 3;
*** Add File: tests/retry_test.rs
+#[test]
+fn retries_is_three() {
+    assert_eq!(crate::RETRIES, 3);
+}
*** Delete File: old_notes.md
*** End Patch
```

我们将追踪它怎样经历五种状态：

```text
patch 文本
→ Vec<Hunk>
→ ApplyPatchAction（预期变化）
→ ApplyPatchRequest（带权限与审批信息）
→ AppliedPatchDelta（实际已写变化）
```

## 4. 先建立三个“变更”概念

这是全篇最重要的区分。

### 4.1 正在生成的 patch 文本

模型可能一段一段流式输出：

```text
*** Begin Patch
*** Update File...
...
```

此时内容可能还不完整，不能落盘。它主要用于实时预览。

### 4.2 `ApplyPatchAction`

完整 patch 解析后，验证阶段读取当前文件，算出：

- 绝对目标路径；
- 删除前的内容；
- 更新后的完整新内容；
- 用于 UI/审批的 unified diff；
- 移动目标路径。

这是“如果现在应用，预计会怎样”的结构化计划。

### 4.3 `AppliedPatchDelta`

执行阶段真正写文件后，记录：

- 哪个变化确实已经提交；
- 修改前和修改后内容；
- 新增是否覆盖了旧文件；
- move 是否覆盖目标；
- 这份记录是否 `exact`。

它回答的是“文件系统实际上发生了什么”。

### 4.4 为什么不能只用一个对象

因为“计划”和“事实”可能不同：

- 审批前文件可能被别的进程改动；
- 第一个 hunk 成功，第二个 hunk 失败；
- 写文件发生错误后，目标可能已经被截断；
- move 可能先写好目标，却无法删除源文件。

因此：

```text
ApplyPatchAction = proposed / verified plan
AppliedPatchDelta = observed committed effects
```

## 5. 全景调用链

```text
模型输出 apply_patch
  ├─ 直接 freeform/custom tool
  │    → ApplyPatchHandler::handle_call
  │
  └─ shell / unified exec 里写 apply_patch heredoc
       → intercept_apply_patch

parse_patch
  → StreamingPatchParser
  → ApplyPatchArgs { patch, hunks, workdir, environment_id }

verify_apply_patch_args
  → 解析 effective cwd
  → 读取 Delete/Update 的现有内容
  → 匹配 update chunks
  → 产生 ApplyPatchAction

execute_verified_patch
  → 计算受影响路径
  → 合并已授予权限
  → assess_patch_safety
  → PatchApply begin / FileChange started
  → ApplyPatchRequest
  → ToolOrchestrator
      → 初次审批（若需要）
      → Sandbox attempt
      → ApplyPatchRuntime::run
          → codex_apply_patch::apply_patch
          → 重新 parse
          → 按 hunk 顺序写文件
          → AppliedPatchDelta
      → 若疑似 Sandbox denial，可能审批后无 Sandbox 重试

ToolEmitter::finish
  → completed / failed / declined UI event
  → 更新 turn diff
  → 文本结果或错误回给模型
```

## 6. 第一入口：直接 freeform tool

`create_apply_patch_freeform_tool` 返回：

```rust
ToolSpec::Freeform(FreeformTool {
    name: "apply_patch".to_string(),
    description: "...FREEFORM tool...do not wrap the patch in JSON...",
    format: FreeformToolFormat {
        r#type: "grammar".to_string(),
        syntax: "lark".to_string(),
        definition,
    },
})
```

这里的 `freeform` 意思是：参数不是一个普通 JSON object，而是一整段受 grammar 约束的文本。

错误写法：

```json
{
  "patch": "*** Begin Patch ..."
}
```

正确形态是直接给 patch：

```text
*** Begin Patch
...
*** End Patch
```

## 7. Lark grammar 在约束什么

固定 grammar 的骨架是：

```text
start: begin_patch hunk+ end_patch
hunk: add_hunk | delete_hunk | update_hunk
```

也就是说，顶层必须有：

1. `*** Begin Patch`；
2. 至少一个 hunk；
3. `*** End Patch`。

可用操作：

```text
*** Add File: path
*** Delete File: path
*** Update File: path
*** Move to: new-path
```

更新行的首字符有语义：

```text
+ 新行
- 旧行，要删除
  上下文行，必须保留
```

`@@` 开启一个 change chunk；`@@ function_name` 还可给出定位上下文；`*** End of File` 表示该 chunk 应落在文件尾部。

## 8. Grammar 不是完整安全验证

Grammar 主要约束模型输出的形状。它不能单独回答：

- 文件是否存在；
- 旧行是否匹配；
- 路径能否写；
- 是否越过 workspace；
- 用户是否批准；
- 写入时会不会 I/O 失败。

所以后面仍要经历 parser、verification、safety、approval 和 Sandbox。

可以类比：语法检查只确认申请表填得像申请表，不代表申请内容真实，也不代表已经获批。

## 9. 流式预览：`ApplyPatchArgumentDiffConsumer`

模型的 tool argument 可能是流式到达的。`ApplyPatchArgumentDiffConsumer` 内部持有：

```rust
parser: StreamingPatchParser
last_sent_at: Option<Instant>
pending: Option<PatchApplyUpdatedEvent>
```

每个 delta 到来时：

1. 推进 streaming parser；
2. 取出目前已经完整识别的 hunks；
3. 转成 protocol `FileChange`；
4. 发 `PatchApplyUpdatedEvent`。

这使 UI 可以在模型尚未结束输出时逐渐展示变更。

## 10. 为什么预览要做 500ms 节流

常量：

```rust
APPLY_PATCH_ARGUMENT_DIFF_BUFFER_INTERVAL = 500ms
```

如果每个 token 都产生一次 UI 事件，会造成：

- 事件数量过多；
- UI 反复重绘；
- 中间状态噪声大；
- 网络或 IPC 开销增加。

因此 500ms 内的新预览先放进 `pending`，结束时再补发最后一个 pending event。

这叫 throttle/buffer，不是延迟真正文件写入；此阶段本来就不会落盘。

## 11. 预览为什么不等于最终验证结果

流式预览把已经解析到的 hunk 转成 `FileChange`，但它没有读取所有目标文件来证明 old lines 确实匹配。

例如模型正在生成：

```text
*** Update File: src/config.rs
@@
-RETRIES = 2
+RETRIES = 3
```

UI 可以展示“模型想这么改”，但如果真实文件里是 `RETRIES = 4`，完整验证仍会失败。

所以 `PatchApplyUpdatedEvent` 是 proposal preview，不是 commit event。

## 12. 第二入口：shell 中的 `apply_patch`

历史上模型也可能生成：

```bash
apply_patch <<'PATCH'
*** Begin Patch
...
*** End Patch
PATCH
```

或者：

```bash
cd subdir && apply_patch <<'PATCH'
...
PATCH
```

`shell.rs` 和 unified exec 的 `exec_command.rs` 都会在真正启动普通命令之前调用：

```rust
intercept_apply_patch(...)
```

一旦识别成功，就走同一个验证、审批、Sandbox 和 patch runtime，不再把它当普通任意 shell 命令执行。

## 13. 为什么要拦截，而不是照常执行 shell

拦截后 Codex 可以获得结构化信息：

- 确切受影响文件；
- add/update/delete/move 类型；
- 变更预览；
- 每路径审批 cache key；
- patch 专用 UI item；
- 实际 committed delta。

如果只把它当不透明 shell 字符串，安全层只能看到“执行一条命令”，很难给用户展示准确的文件变化。

## 14. shell 识别为什么很保守

`maybe_parse_apply_patch` 支持：

- `apply_patch <patch-body>`；
- `applypatch <patch-body>` 别名；
- shell heredoc；
- 可选 `cd <path> && apply_patch ...`。

但 heredoc 脚本必须是允许的完整顶层形态。下面这些不会被轻率地认作纯 patch：

```bash
echo before; apply_patch <<'PATCH'
...
PATCH
```

```bash
apply_patch <<'PATCH'
...
PATCH
echo after
```

```bash
cd dir || apply_patch <<'PATCH'
...
PATCH
```

原因是拦截意味着“不要按原 shell 语义执行，而改走 patch 专用路径”。识别过宽可能吞掉其他命令或改变脚本语义。

## 15. 为什么使用 Tree-sitter Bash

源码不是简单搜索字符串 `apply_patch`，而是解析 shell AST 并用严格 query 匹配。

这能区别：

- 命令名；
- heredoc redirect；
- `cd` 参数；
- `&&` 连接；
- 顶层前后是否还有别的 statement。

字符串包含不等于语法角色。比如注释、echo 参数或引号里的 `apply_patch` 不应该被当成真实调用。

## 16. `ImplicitInvocation` 防什么

若 command array 只有一段裸 patch body，却没有明确调用 `apply_patch`，系统返回 correctness error：

```text
ImplicitInvocation
```

它阻止“看起来像 patch 的任意命令参数”被静默当成文件写操作。

专用写入行为应有明确工具身份；这既方便安全审查，也避免误判。

## 17. parser 的第一关：边界 marker

`parse_patch` 要求：

```text
第一行：*** Begin Patch
最后行：*** End Patch
```

否则返回 `InvalidPatchError`。

parser 当前采用 lenient mode，额外兼容被当成直接参数传入的 heredoc wrapper：

```text
<<'EOF'
*** Begin Patch
...
*** End Patch
EOF
```

这不是允许任意宽松格式，而是针对已知模型调用形态做有限兼容。

## 18. parser 产出 `ApplyPatchArgs`

```rust
pub struct ApplyPatchArgs {
    pub patch: String,
    pub hunks: Vec<Hunk>,
    pub workdir: Option<String>,
    pub environment_id: Option<String>,
}
```

逐词理解：

- `patch`：去掉兼容 wrapper 后的规范 patch 文本；
- `hunks`：结构化编辑单元；
- `workdir`：shell 形式里 `cd` 提供的相对工作目录；
- `environment_id`：多环境模式选择目标执行环境。

## 19. `Hunk` 是什么

```rust
enum Hunk {
    AddFile { path, contents },
    DeleteFile { path },
    UpdateFile { path, move_path, chunks },
}
```

`hunk` 可理解为“一块独立编辑说明”。

注意 move 没有单独的 `MoveFile` variant。它被表示成：

```text
UpdateFile + move_path
```

因为 move 可以同时修改内容。

## 20. `UpdateFileChunk` 是什么

一个 update hunk 可以包含多个 chunk：

```rust
change_context: Option<String>
old_lines: Vec<String>
new_lines: Vec<String>
is_end_of_file: bool
```

例如：

```text
@@ fn first()
-old_a
+new_a
@@ fn second()
-old_b
+new_b
```

两个 chunk 可以修改同一文件的不同位置，而不是生成两个文件操作。

## 21. 路径怎样解析

相对路径基于 effective cwd：

```text
工具 cwd
  + 可选 shell `cd` workdir
  + hunk path
```

绝对路径保持绝对。最终 `ApplyPatchAction` 按设计持有绝对 `PathUri`。

`PathUri` 的意义不只是“换一种 PathBuf”：它允许本地宿主描述远端或不同路径约定的执行环境，避免错误地按 app-server 所在机器解释路径。

## 22. 多环境模式

若工具规格允许多环境，grammar 可以在开头接受：

```text
*** Environment ID: remote
```

handler 用它选择 `TurnEnvironment`。若当前 turn 不允许选择环境却带了 ID，会明确报错；不会悄悄忽略后写到默认环境。

没有可用 environment filesystem 时，也会返回 `apply_patch is unavailable in this session`。

## 23. verification 为什么要读文件

`verify_apply_patch_args` 不只是检查文本语法。

对不同 hunk：

- Add：已有目标内容不是验证必需条件；
- Delete：先读出待删除文件内容；
- Update：读取当前文件，定位 chunks，推导完整新内容和 unified diff。

这一步把“文本指令”变成“针对当前文件状态的具体计划”。

## 24. Update 的上下文匹配

假设文件是：

```text
fn run() {
    retries = 2;
}
```

patch：

```text
@@ fn run()
-    retries = 2;
+    retries = 3;
```

验证器要找到 change context 后面的 `old_lines`。找不到就失败，而不是凭猜测把相似文本替掉。

这就是 patch 比“按行号直接写”更耐文件位移、又比“自然语言编辑”更可验证的地方。

## 25. fuzzy matching 是有限容错

实现存在多轮上下文匹配，并对某些常见 Unicode 标点差异做规范化。例如源码行里是 typographic dash，而模型 patch 用 ASCII `-`，测试证明这种情况可以匹配。

但 fuzzy 不等于任意模糊搜索。关键旧行或上下文找不到，仍应失败，防止修改错误位置。

## 26. verification 产出 `ApplyPatchAction`

```rust
pub struct ApplyPatchAction {
    changes: HashMap<PathUri, ApplyPatchFileChange>,
    pub patch: String,
    pub cwd: PathUri,
}
```

其中 `ApplyPatchFileChange` 为：

```text
Add    { content }
Delete { content }
Update { unified_diff, move_path, new_content }
```

Delete 保存旧内容，Update 保存新内容。这些数据之后用于：

- safety 判断；
- 审批 UI；
- begin event；
- 受影响路径计算。

## 27. 空 patch 的两个层次

Grammar 规格要求至少一个 hunk；底层 parser 仍可能解析出空 hunks，提供更一般的库行为。

到了 `assess_patch_safety`：

```text
action.is_empty() → Reject("empty patch")
```

也就是说，即使某入口绕过 grammar 约束，安全层仍拒绝空操作。

这体现 defense in depth：不同入口不假设上游永远完美。

## 28. `file_paths_for_action` 为什么单独存在

审批和权限需要目标路径列表。普通 Add/Delete/Update 加入源 path；若 Update 带 move destination，还要把目标 path 一并加入。

例如：

```text
old/name.txt → renamed/name.txt
```

必须同时审查：

- 能否读写/删除 old；
- 能否在 renamed 下写目标；
- session approval cache 是否覆盖两边。

只检查源路径会漏掉跨边界写入。

## 29. 写权限为什么取 parent directory

`write_permissions_for_paths` 对每个文件取 parent：

```text
/outside/report.md → /outside
```

原因是创建新文件或 move destination 时，文件本身可能尚不存在；真正需要授权的是在父目录创建或替换条目的能力。

路径会先去重，再构造最小的 `AdditionalPermissionProfile`。

## 30. 已授予权限怎样合并

`effective_patch_permissions` 合并：

- Session 级已授予权限；
- Turn 级已授予权限；
- environment 基础 permission profile；
- workspace roots；
- 本 patch 目标缺少的额外写目录。

然后得到：

```text
file_paths
effective_additional_permissions
effective file_system_sandbox_policy
```

这里的 effective 表示“把基础策略和已经批准的扩展叠加后，本次真正生效的结果”。

## 31. 路径匹配失败时为何不是直接放行

如果 PathUri 不能投影成本机 native path，代码走 `patch_permissions_without_path_matching`。

它不会把权限标成 preapproved，而是：

```text
sandbox_permissions = UseDefault
additional_permissions = None
permissions_preapproved = false
```

注释说明：foreign path 暂时跳过本机 path matching，但 managed turn 仍由执行平台 Sandbox fail closed。

“本机无法判断”不等于“允许写”。

## 32. `assess_patch_safety` 的三态

```rust
enum SafetyCheck {
    AutoApprove,
    AskUser,
    Reject { reason },
}
```

它不是简单 bool，因为系统要区分：

- 安全条件已满足，可以自动批准；
- 需要询问；
- 当前 policy 根本不允许询问，直接拒绝。

## 33. 怎样自动批准

核心问题是：所有写路径是否被当前 filesystem sandbox policy 的 writable roots 约束。

若都在可写根内：

- Disabled/External profile 没有 Codex 外层 Sandbox，按其语义可 AutoApprove；
- Managed profile 只有在当前平台确实有可用 Sandbox 时才 AutoApprove；
- 没有可执行的 Sandbox 时，不能因为路径“看起来在范围内”就假装约束已生效。

源码还特别提醒 hard link 风险，因此即使路径检查通过，Managed 情况下仍要真正运行 Sandbox。

## 34. 哪些情况 AskUser

典型情况：

- patch 触及 writable roots 外；
- `UnlessTrusted` policy；
- 路径看似可约束，但当前平台无可用 Sandbox；
- policy 允许 sandbox approval。

AskUser 只表示进入审批流程，不代表用户批准后一定不会再遇到 I/O 或 Sandbox 错误。

## 35. 哪些情况直接 Reject

典型情况：

- 空 patch；
- 写到项目外且 `AskForApproval::Never`；
- granular policy 关闭 sandbox approval；
- read-only managed policy 没有 writable roots，并且不允许升级。

错误会以 `patch rejected: ...` 回给模型。

## 36. 路径规范化防什么

安全检查会处理 `.` 和 `..`：

```text
/workspace/src/../secrets.txt
```

不能只做未经规范化的字符串 prefix 判断。源码先做词法规范化，再问 sandbox policy 是否允许该路径。

这不等于解析 symlink；真正执行仍必须有 Sandbox，因为路径别名、hard link 和文件系统竞态无法靠字符串检查完全覆盖。

## 37. `prepare_apply_patch` 产生什么

```rust
ApplyPatchRuntimeInvocation {
    action,
    auto_approved,
    exec_approval_requirement,
}
```

对应关系：

```text
AutoApprove
→ auto_approved = true
→ ExecApprovalRequirement::Skip

AskUser
→ auto_approved = false
→ ExecApprovalRequirement::NeedsApproval

Reject
→ 直接 RespondToModel
```

它只准备 runtime invocation，还没有写文件。

## 38. 为什么 begin event 在审批前发

`execute_verified_patch` 先创建 ApplyPatch emitter，然后调用 `begin`，再进入 orchestrator。

这样客户端能展示：

- 哪些文件准备变化；
- 是否 auto-approved；
- 当前 file-change item 已开始。

若随后用户拒绝，仍会有对应 completed item，状态为 Declined，而不是 UI 上凭空消失。

## 39. `ApplyPatchRequest` 装了哪些层的数据

```text
turn_environment             执行在哪个环境
action                       验证后的 patch 计划
file_paths                   审批与 cache 用路径
changes                      protocol/UI 变化
exec_approval_requirement    Skip 或 NeedsApproval
additional_permissions       本次需要叠加的权限
permissions_preapproved      权限是否已提前获批
```

这说明 request 不是纯 patch 内容，而是“可交给通用 orchestrator 执行的完整安全请求”。

## 40. 为什么复用 `ToolOrchestrator`

Apply Patch 没有另写一套审批与 Sandbox 状态机。它实现：

- `Sandboxable`；
- `Approvable<ApplyPatchRequest>`；
- `ToolRuntime<ApplyPatchRequest, ApplyPatchRuntimeOutput>`。

于是可复用统一流程：

```text
approval requirement
→ approval action
→ sandbox selection
→ first attempt
→ sandbox denial classification
→ optional escalation approval
→ retry
```

第 12 篇讲的是通用机器，本篇讲 Apply Patch 怎样插入这台机器。

## 41. patch approval 展示什么

`ApprovalAction::ApplyPatch` 包含：

```text
call id
environment id
cwd
files
raw patch
structured changes
permissions_preapproved
```

用户审批 UI 主要收到 structured changes，所以可以展示文件级 diff，而不必让用户只读一整条 shell command。

Guardian request 还会带 cwd、files 和 raw patch。

## 42. patch 审批 cache 为什么按文件

每个 cache key 是：

```text
ApplyPatchApprovalKey {
    environment_id,
    path,
}
```

它没有按整段 patch 文本缓存。

含义是：用户可以在当前 Session 中信任“在某环境编辑某路径”，而不是只批准完全相同的某一段文本。

## 43. 多文件 patch 怎样命中 cache

一段 patch 修改 A、B、C，系统会产生三个 key。`with_cached_approval` 只有在所有 key 都已缓存批准时才跳过询问。

例如：

```text
A 已批准
B 已批准
C 未批准
```

结果仍需审批。

这避免“其中一个熟悉文件”替整批新目标取得授权。

## 44. move 为什么产生两个 cache key

move 同时影响 source 与 destination：

```text
src/a.rs → ../outside/a.rs
```

即使 source 在已批准目录，destination 仍可能越界。`file_paths_for_action` 把两者都加入 cache keys 和 permission 计算。

## 45. `ApprovedForSession` 才写 cache

一次性 `Approved` 只批准当前请求；`ApprovedForSession` 才把各 path key 写入 Session cache。

所以“这次允许”和“以后这个 Session 里同路径也允许”是不同决定。

cache 还包含 environment id，remote 的 `/repo/a.rs` 不会因为 local 同名路径获批而自动通过。

## 46. `permissions_preapproved` 的短路

若 request permissions 工具或 turn permission flow 已提前批准所需额外权限，并且当前不是失败后的 retry reason，patch approval 可直接视为 Approved。

这避免同一权限扩展先批一次、真正 apply 时又重复弹一次。

但失败升级带有 reason 时，仍可重新进入明确审批，不能让 preapproval 吞掉新的风险信号。

## 47. Apply Patch 的 Sandbox 偏好

```rust
fn sandbox_preference(&self) -> SandboxablePreference {
    SandboxablePreference::Auto
}
```

`Auto` 意味着由 orchestrator 和当前 permission profile 决定平台 Sandbox，而不是 runtime 强制总开或总关。

`sandbox_cwd` 使用 `action.cwd`，即相对 patch 路径解析所用的 effective cwd。

## 48. 为什么 `escalate_on_failure()` 返回 true

如果 sandboxed attempt 失败，而且输出被识别为很可能的 Sandbox denial，runtime 将其包装成：

```text
SandboxErr::Denied
```

orchestrator 可以据此请求用户批准无 Sandbox 重试。

普通 patch correctness error 不会被当成 Sandbox denial；“上下文找不到”不能靠提权解决。

## 49. 哪些 approval policy 允许无 Sandbox 重试

`wants_no_sandbox_approval`：

```text
Never          → false
OnRequest      → true
UnlessTrusted  → true
Granular       → 看 sandbox_approval flag
```

所以 runtime 想升级不等于一定能升级。最终仍受用户/管理员审批策略约束。

## 50. `file_system_sandbox_context_for_attempt`

每次 attempt 都根据：

- exec-server 基础 permissions；
- 本 patch additional permissions；
- attempt 的 cwd/workspace roots；
- Windows sandbox level；
- legacy Landlock 配置；

构造执行文件系统收到的 `FileSystemSandboxContext`。

若 attempt 的 `SandboxType::None`，返回 `None`，表示这次是明确的无 Sandbox 执行，而不是忘了传策略。

## 51. 真正执行时为什么又 parse 一次

`ApplyPatchRuntime::run` 调用：

```rust
codex_apply_patch::apply_patch(
    &req.action.patch,
    &req.action.cwd,
    ...
)
```

底层 `apply_patch` 会重新 `parse_patch`，然后 `apply_hunks`。

前面的 `ApplyPatchAction` 主要服务于验证、预览、安全与审批；真正落盘仍以规范 patch 文本和当时的文件系统状态执行。

## 52. verification 与 execution 之间不是锁住的快照

验证阶段读过文件，不代表文件在审批等待期间被冻结。

如果别的进程修改了目标：

- execution 重新读取并匹配 chunks；
- 可能仍能在新内容上正确应用；
- 也可能因为旧上下文消失而失败。

因此不能把 `ApplyPatchAction` 当作事务锁或 compare-and-swap token。实际结果要看 runtime 的 `AppliedPatchDelta`。

## 53. 底层按 hunk 顺序执行

`apply_hunks_to_files`：

```rust
for hunk in hunks {
    match hunk {
        AddFile => ...
        DeleteFile => ...
        UpdateFile => ...
    }
}
```

这很直白，但带来一个重要结论：多文件 patch 不是自动原子事务。

## 54. Add File 的真实语义

Add 流程：

1. 尝试读取目标旧内容，供 delta 记录覆盖情况；
2. 写入新内容；
3. 若父目录不存在，创建目录后重试；
4. 记录 `AppliedPatchFileChange::Add`；
5. summary 标为 `A`。

固定测试表明，Add 可以覆盖已有文件，并在 delta 中记录 `overwritten_content`。

所以 `Add File` 在这份实现里不是严格的“目标必须不存在”。

## 55. Delete File 的真实语义

Delete 流程：

1. 检查现有路径是否适合精确 delta；
2. 读取待删除内容；
3. 确认目标不是目录；
4. 用非递归、非 force remove 删除；
5. 记录 Delete delta；
6. summary 标为 `D`。

删除目录会失败，不会递归清空目录。

## 56. Update File 的真实语义

普通 Update：

1. 读取当前文件；
2. 根据 chunks 推导 `original_contents` 和 `new_contents`；
3. 写回同一路径；
4. 记录 old/new content；
5. summary 标为 `M`。

不存在的文件不能用 Update 凭空创建；应使用 Add。

## 57. Move 的真实顺序

Update + Move：

1. 推导新内容；
2. 读取 destination 是否已有内容；
3. 先把新内容写到 destination；
4. 暂时记录 destination Add delta；
5. 再删除 source；
6. 删除成功后，把临时 delta 改写成 source Update with move_path。

这个顺序保证目标内容先就位，但也产生一个重要失败边界。

## 58. Move 删除源失败会怎样

如果 destination 已写成功，但 source 因权限错误无法删除：

```text
source 仍存在
destination 已存在新内容
整体调用失败
delta 记录 destination 已新增
```

源码测试明确覆盖了这个场景。

所以“move 失败”不一定等于“文件系统完全没变化”。

## 59. 多 hunk 也可能部分成功

fixture `015_failure_after_partial_success_leaves_changes`：

```text
先 Add created.txt
再 Update 不存在的 missing.txt
```

最终：

- `created.txt` 保留；
- 后续 update 失败；
- 整体命令失败。

这正是为什么本篇不能把 Apply Patch 描述成原子事务。

## 60. `AppliedPatchDelta` 的结构

```rust
pub struct AppliedPatchDelta {
    changes: Vec<AppliedPatchChange>,
    exact: bool,
}
```

`changes` 用 `Vec` 而不是 `HashMap`，因为它需要保留实际应用顺序。

`exact` 表示系统是否确信 delta 完整、准确地描述了已提交文本变化。

## 61. `exact = false` 什么时候出现

典型情况：

- write 报错，但文件可能在报错前已经截断或部分写入；
- 目标是 symlink 或不是普通文件；
- 旧内容无法读取；
- 覆盖目标是非 UTF-8 文件；
- remove 失败后无法证明路径仍保持原状。

`false` 不是说“一定发生了未知变化”，而是说“系统不能保证记录精确”。

## 62. Runtime 为什么累计 `committed_delta`

`ApplyPatchRuntime` 自身持有：

```rust
committed_delta: AppliedPatchDelta
```

每次 run attempt 的 delta 都 append 进去。

这对 Sandbox escalation 很重要：第一次 sandboxed attempt 可能已提交前缀，随后因为 denial 失败；第二次 attempt 又继续产生变化。最终事件需要知道整个 runtime 生命周期已经发生的实际变化，而不只是最后一次 attempt。

## 63. 一个危险误解：失败就可以直接重跑整段 patch

不一定。

若第一次 attempt 已成功 Add 文件，后来失败，再从头重跑：

- Add 可能覆盖已创建文件；
- earlier update 的旧上下文可能已不再存在；
- move destination 可能已写入。

实现虽然允许 orchestrator 做升级重试，但通过 accumulated delta 诚实记录已发生变化。写调用方时不能假设失败天然可幂等重试。

## 64. stdout 与 stderr

成功时底层打印类似：

```text
Success. Updated the following files:
A nested/new.txt
M src/config.rs
D old_notes.md
```

失败时把 parser 或 I/O 错误写到 stderr。

Runtime 把两者包装进 `ExecToolCallOutput`，并设：

```text
exit_code = 0  成功
exit_code = 1  apply_patch 返回错误
timed_out = false
```

## 65. Sandbox denial 怎样识别

底层 I/O failure 本身不一定带统一“Sandbox denied”类型。runtime 根据：

- 当前 `SandboxType`；
- stdout/stderr/exit code；
- `is_likely_sandbox_denied` 启发式；

把疑似 Sandbox 阻止转换为 `SandboxErr::Denied`。

只有这个分类才触发通用 orchestrator 的无 Sandbox 升级路径。

## 66. 普通 correctness error 为什么不升级

例如：

```text
Invalid patch hunk
Failed to find expected lines
Update target does not exist
```

这些是 patch 与文件状态不匹配，不是权限不足。给更高文件权限不会让错误旧行突然存在。

系统应把错误回给模型，让模型重新读取文件并生成新 patch。

## 67. begin/update/end 三类事件

不要混淆：

```text
PatchApplyUpdatedEvent
  模型仍在生成参数时的提案预览

FileChange started / legacy PatchApplyBegin
  完整验证后，执行生命周期开始

FileChange completed / legacy PatchApplyEnd
  执行完成、失败或被拒绝
```

它们分别对应“想改什么”“开始处理什么”“最终怎样”。

## 68. End status 有三种

```text
Completed
Failed
Declined
```

- Completed：exit code 0；
- Failed：执行或 I/O 失败；
- Declined：审批拒绝路径。

`Declined` 与 `Failed` 分开，使 UI 能区别“用户不允许”和“系统尝试后没成功”。

## 69. 失败事件中的 changes 是什么

协议 `PatchApplyEndEvent.changes` 注释说它 mirrors begin changes。也就是说它仍描述预期/展示用变化，不必然等于实际全部提交。

实际 turn diff 更新优先使用 `AppliedPatchDelta`；如果没有可靠 delta，则可能 invalidate tracker，要求之后重新计算，而不是拿 proposal 冒充事实。

## 70. Turn diff tracker 为什么需要 delta

Turn diff 要告诉用户本轮真正修改了什么。

如果 patch 完全成功，计划与事实通常一致；若部分失败，仅靠 begin changes 会多报未完成的文件。`tracker_update_for_known_delta` 使用实际 delta 更新。

delta 不可用或不精确时，tracker 可失效并重新获取状态，宁可多算一次，也不展示虚构结果。

## 71. Denied 之后也可能已有 delta

源码特别处理：Sandbox denial 可能发生在已经提交某些 hunk 之后。

因此事件表面仍标记失败，但 turn diff 会消费已知 committed prefix。

这听起来反直觉，却是诚实的副作用建模：

```text
操作结果 = 失败
文件系统变化 = 可能非空
```

两者并不矛盾。

## 72. 结果怎样回给模型

直接 freeform tool 成功后，handler 构造：

```text
ApplyPatchToolOutput::from_text(content)
```

固定提交中 payload 是 `ToolPayload::Custom`，所以 `function_tool_response` 产生 `CustomToolCallOutput`，`success = true`。

失败则通过 `FunctionCallError::RespondToModel` 把格式化 stdout/stderr 或错误文本送回模型。

模型下一步可以：

- 解释已完成变化；
- 读取文件确认状态；
- 修正上下文后再发 patch；
- 告诉用户审批被拒绝；
- 避免盲目重复已部分成功的操作。

## 73. shell 拦截入口怎样回填

若 patch 是从 shell 或 unified exec 中拦截出来，执行成功后包装成普通 `FunctionToolOutput`，并标记 success。

对模型来说，call/output 配对仍属于原 shell 工具调用；对执行和 UI 来说，内部已经走 patch 专用事件与安全路径。

这就是 adapter：外部保持原调用协议，内部复用更精确的 patch 实现。

## 74. Hooks 在哪里介入

直接 `ApplyPatchHandler` 实现 `CoreToolRuntime`：

- PreToolUse payload 把 raw patch 放在 `{ "command": patch }`；
- hook 可以返回更新后的 command；
- handler 用更新后的 patch 替换原 `ToolPayload::Custom`；
- PostToolUse 收到最终 tool response。

Hook 修改输入后仍会重新 parse 和 verify，不会继承旧 patch 的验证结果。

## 75. 四类失败要分开

### 75.1 语法失败

例如缺少 `*** End Patch`。

发生在 parser，尚未写文件。

### 75.2 正确性验证失败

例如旧行找不到、待删除文件不存在。

发生在 verification，通常尚未进入 runtime 写入。

### 75.3 审批拒绝

patch 结构可能完全正确，但用户或 policy 不允许目标路径。

结果是 Declined/RespondToModel。

### 75.4 执行失败

验证和审批都通过，但实际写入时 I/O、竞态或 Sandbox denial 出错。

可能已经有部分 delta。

## 76. 失败矩阵

| 失败点 | 已读真实文件 | 已发 begin | 可能已写文件 | 模型得到什么 |
|---|---:|---:|---:|---|
| grammar / parse | 否 | 否 | 否 | parse error |
| verify old context | 是 | 否 | 否 | verification failed |
| safety reject | 是 | 否 | 否 | patch rejected |
| user declines | 是 | 是 | 否 | patch rejected by user |
| first hunk I/O failure | 是 | 是 | 可能 | formatted error + delta/invalidation |
| later hunk failure | 是 | 是 | 是 | 整体失败 + committed prefix |
| sandbox denial then approval denied | 是 | 是 | 可能 | rejection + known delta |
| successful runtime | 是 | 是 | 是 | success summary |

## 77. Add、Update、Delete、Move 对比

| 操作 | 需要目标预先存在 | 可能创建父目录 | 可能覆盖 | 实际主要副作用 |
|---|---:|---:|---:|---|
| Add | 否 | 是 | 是，记录旧内容 | 写目标 |
| Update | 是 | 否 | 写回自身 | 读、匹配、写目标 |
| Delete | 是 | 否 | 不适用 | 非递归删除 |
| Update + Move | 源必须存在 | 目标父目录可创建 | 目标可覆盖 | 先写目标，再删源 |

## 78. 为什么不用系统 `git apply`

固定实现自己解析和应用，因而可以：

- 使用专门面向模型的 marker grammar；
- 在执行前形成结构化 changes；
- 支持远端 `ExecutorFileSystem`；
- 把 Sandbox context 传到每个 filesystem 操作；
- 精确记录 partial committed delta；
- 对模型常见格式偏差做有限兼容；
- 不要求工作目录一定是 Git 仓库。

这并不表示自定义 patch 永远优于标准 unified diff，而是它服务于不同的 agent-tool contract。

## 79. 为什么验证阶段和执行阶段都读取文件

验证读取：为了审批前知道预计变化，并尽早拒绝错误 patch。

执行读取：为了按执行瞬间的真实状态应用 chunks，并记录实际 delta。

两次读取不是无意义重复；它们分别服务于：

```text
preflight truth
execution truth
```

但两者之间存在 time-of-check/time-of-use 窗口，因此运行结果仍是最终事实。

## 80. 安全边界压缩

Apply Patch 的安全不是单点完成，而是多层：

```text
grammar
  限制模型输出形状

parser + verifier
  限制操作语义并核对当前内容

path safety
  判断所有源/目标是否在 writable roots

approval
  让 policy、Hook、Guardian 或用户决定扩展

Sandbox
  在操作系统/执行环境边界强制限制

actual delta
  失败时也如实记录副作用
```

少任何一层，其他层都不能完全替代它。

## 81. 建议的源码阅读顺序

第一遍只看主干：

1. `core/src/tools/handlers/apply_patch_spec.rs`；
2. `core/src/tools/handlers/apply_patch.rs::handle_call`；
3. `apply-patch/src/invocation.rs::verify_apply_patch_args`；
4. `core/src/apply_patch.rs::prepare_apply_patch`；
5. `core/src/tools/handlers/apply_patch.rs::execute_verified_patch`；
6. `core/src/tools/runtimes/apply_patch.rs::run`；
7. `apply-patch/src/lib.rs::apply_hunks_to_files`。

第二遍补入口与 UI：

8. `intercept_apply_patch`；
9. shell 和 unified exec 的调用点；
10. `ApplyPatchArgumentDiffConsumer`；
11. `ToolEmitter::ApplyPatch`；
12. `ApplyPatchToolOutput`。

第三遍补安全和失败：

13. `core/src/safety.rs::assess_patch_safety`；
14. `core/src/tools/approvals.rs` 的 ApplyPatch 分支；
15. `AppliedPatchDelta`；
16. fixtures 004、015；
17. runtime 与 approvals 测试。

## 82. 固定源码搜索命令

```bash
git grep -n 'create_apply_patch_freeform_tool' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'intercept_apply_patch' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'verify_apply_patch_args' 4ee41929eaf4 -- codex-rs/apply-patch/src
git grep -n 'assess_patch_safety' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'execute_verified_patch' 4ee41929eaf4 -- codex-rs/core/src
git grep -n 'AppliedPatchDelta' 4ee41929eaf4 -- codex-rs/apply-patch/src
git grep -n 'ApplyPatchApprovalKey' 4ee41929eaf4 -- codex-rs/core/src
```

## 83. 理解检查

### 问题 1：为什么 streaming preview 不能证明 patch 会成功？

<details><summary>参考答案</summary>

它主要从尚在生成的文本提取 hunk，并没有完成对目标文件当前内容、路径权限和实际 I/O 的验证。

</details>

### 问题 2：为什么 move 要检查 source 和 destination？

<details><summary>参考答案</summary>

move 会在目标写新内容并删除源；任一侧都可能跨 writable root，也都属于审批和 cache 的安全范围。

</details>

### 问题 3：为什么整体失败时文件仍可能改变？

<details><summary>参考答案</summary>

底层按 hunk 顺序执行，不提供整批事务回滚；前面 hunk 或 move 的目标写入可能已经成功。

</details>

### 问题 4：`ApplyPatchAction` 与 `AppliedPatchDelta` 有何区别？

<details><summary>参考答案</summary>

前者是验证阶段基于当时文件状态形成的预期计划；后者记录执行阶段确定已提交的实际变化。

</details>

### 问题 5：上下文找不到为什么不申请无 Sandbox 重试？

<details><summary>参考答案</summary>

它是 patch correctness 问题，不是权限拒绝；提高权限不会让缺失的 old lines 出现。

</details>

### 问题 6：为什么 approval cache 不只按 patch 文本？

<details><summary>参考答案</summary>

当前语义允许用户在 Session 内批准某环境的某路径；多文件必须全部命中，move 的两端也分别受控。

</details>

### 问题 7：为什么执行时再次 parse？

<details><summary>参考答案</summary>

Runtime 调用可独立工作的 `codex_apply_patch::apply_patch` 库函数，以规范 patch 文本和执行时文件状态为准；预计算 action 用于安全、审批和预览。

</details>

## 84. 动手练习

### 练习一：手工转换状态

把贯穿案例依次写成：

```text
Vec<Hunk>
ApplyPatchAction.changes
ApplyPatchRequest.file_paths
成功时 AppliedPatchDelta.changes
```

### 练习二：判断是否可能部分成功

分析：

```text
Add A
Update B
Delete C
```

分别讨论 B 不存在、C 是目录、C 删除被 Sandbox 阻止时 A/B/C 的可能状态。

### 练习三：计算 cache keys

```text
Environment: remote
Update /repo/a.rs
Move /repo/b.rs → /outside/b.rs
```

列出全部 environment/path keys。

### 练习四：区分四类失败

把以下错误分到 parse、verify、approval、execution：

```text
缺少 End Patch
old line 不存在
用户点拒绝
写文件 ENOSPC
```

### 练习五：解释 UI 三阶段

用自己的话说明 Updated、Begin、End 分别能保证什么、不能保证什么。

## 85. 本篇局部术语表

| 名词 / 代码词 | 中文理解 | 本篇中的具体含义 |
|---|---|---|
| apply patch | 应用补丁 | 把结构化文件编辑提案真正写入执行环境 |
| patch | 补丁 | 用 marker、路径和增删行描述的一组编辑 |
| lifecycle | 生命周期 | 从模型生成到解析、审批、执行、回填的全过程 |
| freeform tool | 自由格式工具 | 参数是一段文本，而不是固定 JSON object |
| custom tool | 自定义工具 | 固定提交中承载 apply_patch 的 Responses 工具类型 |
| grammar | 语法规则 | 约束模型能输出哪些 patch 结构 |
| Lark | 语法描述格式 | 工具 spec 用来声明 patch grammar 的 syntax |
| marker | 标记行 | `*** Begin Patch` 等有特殊语义的文本 |
| hunk | 补丁块 | 一个 Add、Delete 或 Update 文件操作 |
| chunk | 更新片段 | Update hunk 内一次连续 old/new lines 替换 |
| context line | 上下文行 | 用于定位且保持不变的行 |
| change context | 变更定位上下文 | `@@ name` 提供的函数或区域提示 |
| EOF marker | 文件尾标记 | `*** End of File`，要求 chunk 位于文件尾 |
| streaming parser | 流式解析器 | 参数未全部到达时逐步识别完整 hunk |
| argument diff | 参数增量 | 模型本次新输出的一小段 tool argument |
| throttle | 节流 | 限制 preview event 的发送频率 |
| pending | 待发送 | 节流窗口内保留的最新预览事件 |
| shell interception | Shell 拦截 | 在普通命令执行前识别 apply_patch 并改走专用路径 |
| heredoc | 多行输入重定向 | shell 中把多行 patch 传给命令的写法 |
| Tree-sitter | 增量语法解析库 | 用 AST 严格识别 shell apply_patch 形态 |
| AST | 抽象语法树 | 命令的结构表示，不只是原始字符串 |
| implicit invocation | 隐式调用 | 裸 patch 未明确声明 apply_patch 工具身份 |
| parse | 解析 | 把 patch 文本变成 `ApplyPatchArgs` 和 hunks |
| lenient | 有限宽松 | 兼容少数已知 heredoc wrapper，而非接受任意格式 |
| `ApplyPatchArgs` | 补丁参数 | raw patch、hunks、workdir、environment id |
| `Hunk` | 补丁操作块 | AddFile、DeleteFile、UpdateFile 三种 variant |
| `UpdateFileChunk` | 文件更新片段 | old/new/context/EOF 信息 |
| `PathUri` | 路径 URI | 可跨本地和远端环境表达目标路径 |
| effective cwd | 有效工作目录 | 工具 cwd 与可选 `cd` 合成后的路径基准 |
| verify | 验证 | 读取当前文件并预计算具体变化 |
| fuzzy matching | 有限模糊匹配 | 容忍部分空白或常见标点差异的上下文定位 |
| unified diff | 统一差异 | 给 UI/审批展示的标准化增删行描述 |
| `ApplyPatchAction` | 补丁行动计划 | 验证完成后的绝对路径与预期 changes |
| proposal | 提案 | 尚未证明已实际提交的变化 |
| preflight | 执行前检查 | 真正写入前的语法、内容和安全验证 |
| writable root | 可写根目录 | filesystem sandbox policy 允许修改的目录树 |
| additional permissions | 额外权限 | 本次目标超出基础权限时请求叠加的最小授权 |
| permission profile | 权限配置 | environment 基础文件与网络能力 |
| preapproved | 已预先批准 | 所需扩展权限在更早的权限流程中已获批 |
| `SafetyCheck` | 安全结论 | AutoApprove、AskUser 或 Reject |
| hard link | 硬链接 | 不同路径名指向同一底层文件的文件系统关系 |
| TOCTOU | 检查与使用时差 | verification 后到 execution 前文件可能变化 |
| `ApplyPatchRequest` | 补丁运行请求 | action 加 environment、审批与 Sandbox 数据 |
| orchestrator | 编排器 | 复用审批、Sandbox、失败升级和重试状态机 |
| `ApprovalAction::ApplyPatch` | 补丁审批动作 | 给 Hook、Guardian 或用户的结构化审批材料 |
| approval cache key | 审批缓存键 | environment id 与单个受影响 path |
| Sandbox attempt | 沙箱尝试 | 在选定 Sandbox 与权限上下文中的一次运行 |
| escalation | 升级 | 疑似 Sandbox denial 后申请无 Sandbox 重试 |
| correctness error | 正确性错误 | patch 格式或上下文不符合当前文件 |
| committed | 已提交 | 文件系统副作用已经发生 |
| `AppliedPatchDelta` | 已应用变化增量 | 按顺序记录实际确定写入的文本变化 |
| exact | 精确标记 | delta 是否能完整准确描述已发生变化 |
| partial success | 部分成功 | 整体失败前已有部分 hunk 写入 |
| atomic | 原子的 | 要么全部成功、要么完全不变；本实现多 hunk 不保证此性质 |
| rollback | 回滚 | 撤销已发生变化；底层不会自动回滚整批 hunk |
| idempotent | 幂等 | 重复执行效果不变；失败后的整段 patch 不可默认视为幂等 |
| `overwritten_content` | 被覆盖旧内容 | Add/Move 覆盖目标时 delta 保存的旧文本 |
| begin event | 开始事件 | 完整验证后宣布 file-change 生命周期开始 |
| updated event | 更新事件 | 模型生成参数时的流式 proposal preview |
| end event | 结束事件 | Completed、Failed 或 Declined 的最终 UI 状态 |
| turn diff tracker | 本轮差异跟踪器 | 汇总本轮实际文件变化的状态组件 |
| invalidate | 使缓存失效 | 无法精确增量更新时要求重新计算 diff |
| `CustomToolCallOutput` | 自定义工具调用输出 | 固定提交把直接 apply_patch 结果回给模型的 item |
| adapter | 适配层 | shell 外部协议不变、内部转到 patch 专用实现 |

## 86. 最后压缩成一句话

Apply Patch lifecycle 的真正主线不是“模型写一段 diff，程序执行一下”，而是：

> Codex 用 grammar 约束模型的 freeform patch，通过直接 handler 或保守的 shell interception 识别调用；parser 将文本拆成 hunks，verification 读取目标文件形成绝对路径和预期 `ApplyPatchAction`；路径 policy、预授予权限、每文件审批 cache 与通用 orchestrator 决定是否在 Sandbox 中执行或升级；底层再次解析并按 hunk 顺序写入，允许 Add/Delete/Update/Move，但不承诺多文件原子性，因此用带 `exact` 标记的 `AppliedPatchDelta` 记录已提交前缀；最后 UI 的 Updated/Begin/End 事件和模型 call output 分别报告提案、生命周期和真实结果。

下一篇计划精读 tool output/context feedback lifecycle：不同工具结果怎样被规范化、截断、配对、写入模型上下文，并推动下一轮 sampling。

返回[源码精读系列目录](README.md)或[课程总目录](../README.md)。
