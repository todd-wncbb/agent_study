# 59：子进程错误分类、错误传播、重试与用户可读诊断——失败以后怎样保留真相

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方文档说明 Codex CLI 能运行本地命令，并会受到审批与 sandbox 策略约束；官方文档没有规定本章涉及的内部 Rust 错误枚举、sandbox 拒绝启发式、远程恢复退避或 UI 字节上限。这些实现细节均以当前源码为准。
>
> 官方对照资料：[Codex CLI](https://learn.chatgpt.com/docs/codex/cli)。

## 1. 本章解决什么问题

命令失败以后，最容易写出的代码是：

```rust
return Err("command failed".to_string());
```

但这句话几乎丢掉了所有有用信息：

- 是程序正常返回了非零退出码，还是根本没有启动？
- 是用户代码报错，还是 sandbox 拦截？
- 是运行超时，还是读取结果的 RPC 超时？
- stdout、stderr、exit code 和运行时长还在吗？
- 自动重试会不会重复写文件、发请求或发布制品？
- 这个错误应该交给模型继续处理，还是终止整个 turn？
- 给用户看的提示应该保留多少内部细节？

本章沿 Codex 当前源码回答这些问题。

## 2. 先说人话：失败不等于 Error

假设 Codex 执行：

```bash
rg TODO missing-directory
```

`rg` 成功启动、运行、输出错误信息，然后返回非零退出码。这次“执行系统”完成了任务，只是被执行程序报告失败。

再假设 Codex 想启动一个不存在的 executable：

```text
/missing/bin/tool
```

这一次连进程都没有创建。它属于启动基础设施错误。

两个场景都可被口语称为“命令失败”，但处理方式完全不同。

## 3. 用快递系统建立直觉

把执行命令想成寄快递：

| 情况 | 进程世界中的对应物 |
|---|---|
| 包裹送到，收件人拒收 | 程序执行完成并返回非零 exit code |
| 快递员根本没能出发 | spawn/create process 失败 |
| 小区门禁不让进 | sandbox denied |
| 规定时间内没送到 | timeout |
| 运输系统断线 | exec-server transport disconnected |
| 地址格式不属于这个城市 | foreign path / invalid request |
| 运单备注太长，只展示摘要 | UI error truncation |

“拒收”不能伪装成“快递系统崩溃”；否则调用方就不知道下一步应该修改包裹，还是修复运输系统。

## 4. 第一条核心原则：区分 outcome 与 infrastructure error

本章使用两个教学词：

```text
Outcome
  执行已完成，拿到了 stdout/stderr/exit code 等结果

Infrastructure error
  执行机制未能按契约完成，例如无法创建进程、RPC 断线
```

Codex 的类型名不一定直接叫这两个词，但源码的处理边界体现了这个思想。

## 5. 普通非零退出通常仍是输出对象

传统命令和 unified exec 都会构造 `ExecToolCallOutput`。它包含的核心事实包括：

```text
exit_code
stdout
stderr
aggregated_output
duration
timed_out
```

因此：

```text
exit_code != 0
```

不自动等于：

```text
Rust 函数必须返回 Err
```

上层可以看到结果，再决定是修命令、换参数、向用户解释，还是停止。

## 6. 为什么这个区别很重要

如果把每个非零 exit code 都变成基础设施错误：

- stdout/stderr 容易在字符串转换中丢失；
- 模型不容易看见编译器、测试或 CLI 的真实诊断；
- telemetry 会把用户代码失败误计为系统故障；
- 通用重试器可能盲目重复有副作用的命令；
- UI 无法提供 exit code、耗时和截断信息。

## 7. 四层失败分类

可以先用四层心智模型阅读源码：

```text
第 1 层：被执行程序的结果
  exit 0 / exit 1 / stderr / timed_out

第 2 层：执行与安全机制
  spawn、sandbox、process store、stdin、path

第 3 层：传输与编排
  RPC、WebSocket、JSON、remote recovery、approval

第 4 层：Agent 与产品协议
  RespondToModel、CodexErr、CodexErrorInfo、UI message
```

同一个底层事实可能在跨层时被包装，但不应无理由丢掉关键诊断字段。

## 8. 一个错误对象通常承担三种职责

错误设计常混淆三件事：

1. **控制流**：调用方应该继续、重试还是终止？
2. **机器分类**：这是 sandbox、timeout、auth 还是 connection？
3. **人类诊断**：用户实际应看到什么？

只存一个字符串，会让三个问题都依赖脆弱的文本匹配。

## 9. `UnifiedExecError` 是什么

`codex-rs/core/src/unified_exec/errors.rs` 定义 unified exec 边界的错误：

```rust
enum UnifiedExecError {
    CreateProcess { message: String },
    ProcessFailed { message: String },
    UnknownProcessId { process_id: i32 },
    WriteToStdin,
    StdinClosed,
    MissingCommandLine,
    SandboxDenied { /* output 与截断元数据 */ },
    ForeignPath { path: PathUri },
}
```

它没有把所有失败都写成 `String`，而是保留调用方需要区别处理的 variant。

## 10. Variant 是什么

Rust `enum` 的每一种可能形状称为 variant，可理解为“带类型的分支”：

```rust
CreateProcess { message }
SandboxDenied { message, output, ... }
```

调用方可以用 `match` 精确判断，而不必搜索文本中是否包含 `sandbox`。

## 11. `CreateProcess` 与 `ProcessFailed`

两者名字容易混：

- `CreateProcess`：创建或打开执行会话阶段失败；
- `ProcessFailed`：已有 unified process 在运行/转发期间进入失败状态。

它们都不同于“程序正常执行后 exit 1”。

## 12. `UnknownProcessId` 是会话状态错误

模型可能调用 `write_stdin`，却提供一个已经退出、被清理或不存在的 process ID。

此时问题不是 shell 命令语法，而是：

```text
请求引用的会话状态不存在
```

错误中保留 `process_id`，提示调用方应检查身份与生命周期。

## 13. `StdinClosed` 展示了“可行动提示”

它的错误文本不是只有：

```text
stdin closed
```

而是说明：

```text
rerun exec_command with tty=true to keep stdin open
```

好诊断不仅描述过去发生了什么，还告诉调用方下一步怎样改变请求。

## 14. `ForeignPath` 为什么应是独立分支

远程 executor 可能使用与 host 不同的操作系统路径约定。Windows 路径不能被本地 POSIX `PathBuf` 随意解释，反之亦然。

`ForeignPath { path: PathUri }` 保留原路径和命名空间错误，而不是把它误报成“文件不存在”。

## 15. `SandboxDenied` 为什么携带完整输出

该 variant 除了 message，还保留：

```text
ExecToolCallOutput
original_token_count
output_omitted_bytes
```

原因是 sandbox 拒绝常通过 stderr 表现，例如 permission denied。只保留一句分类会丢掉具体文件、系统调用或网络主机信息。

## 16. 错误里也要保留截断元数据

输出有容量上限。若诊断被 head-tail 截断，调用方应知道：

- 原始输出大约有多少 token；
- 中间省略了多少 bytes；
- 当前看到的文本不是完整原文。

`with_output_collection_metadata()` 只为 `SandboxDenied` 补充这些字段，其余 variant 原样返回。

## 17. `ToolError` 再分一次边界

`codex-rs/core/src/tools/sandboxing.rs` 的 `ToolError` 有两类：

```rust
Rejected(String)
Codex(CodexErr)
```

可这样理解：

- `Rejected`：策略、验证或业务规则决定不执行；
- `Codex`：执行过程中出现了结构化的 core/protocol 错误。

拒绝不是 crash，也不一定是 OS sandbox 拒绝。

## 18. Approval rejection 与 sandbox denial 不一样

审批在动作执行前决定“是否允许尝试”；sandbox 在真正执行时强制限制系统调用、文件或网络访问。

```text
approval rejected
  命令通常尚未运行

sandbox denied
  命令可能已经启动并产生部分输出
```

因此两者的副作用状态、输出证据和重试条件不同。

## 19. `SandboxErr` 的语义分支

`codex-rs/protocol/src/error.rs` 定义：

```text
Denied             sandbox 拒绝并带执行输出
Timeout            命令超时并带执行输出
Signal             命令被 signal 终止
SeccompInstall      Linux seccomp 安装失败
SeccompBackend      Linux seccomp backend 失败
LandlockRestrict    Landlock 未能完整实施规则
```

虽然它们都位于 sandbox 相关类型中，用户体验不应把它们展示成同一句“sandbox error”。

## 20. Timeout 在类型位置与 UX 位置可以不同

当前实现中命令 timeout 是：

```rust
CodexErrorDetails::Sandbox(SandboxErr::Timeout { output })
```

但 `get_error_message_ui()` 特意展示为：

```text
error: command timed out after N ms
```

源码注释明确说，从 UX 角度不应把 timeout 呈现为笼统 sandbox error。

## 21. `CodexErr` 把分类与重试延迟分开

`CodexErr` 内部包含：

```rust
details: CodexErrorDetails,
retry_delay: Option<Duration>,
```

语义错误类别放在 `details`；服务端建议的等待时间单独放在 `retry_delay`。

这比把“retry after 3s”拼进 message 更适合机器处理。

## 22. `CodexErrorDetails` 是更大的语义总表

它不仅覆盖进程执行，还覆盖：

- stream 中断；
- request timeout；
- context window 用尽；
- usage/quota；
- HTTP 状态与连接；
- auth refresh；
- sandbox；
- JSON/I/O/Tokio task；
- fatal internal error。

因此阅读 `CodexErr::is_retryable()` 时，不要误以为它只判断 shell 命令。

## 23. `thiserror::Error` 帮了什么

`#[derive(thiserror::Error)]` 根据注解生成标准 `std::error::Error` 和 `Display` 实现。

例如：

```rust
#[error("failed to spawn exec-server: {0}")]
Spawn(#[source] std::io::Error)
```

外层获得清楚的上下文，内层 `io::Error` 仍留在 error source chain。

## 24. `source` 是错误原因链

以下两句并不等价：

```text
failed to spawn exec-server
No such file or directory
```

包装后可以表达：

```text
failed to spawn exec-server
  caused by: No such file or directory
```

`#[source]` 让日志、调试器或上层错误框架能够继续追踪底层原因。

## 25. `Display` 与 `Debug` 服务不同读者

一般来说：

- `Display`：稳定、较友好的人类说明；
- `Debug`：variant、字段和内部结构，适合开发诊断。

当前 unified exec handler 对部分内部错误使用 `{err:?}` 返回模型，能暴露更多类别信息；真正 UI 文本则经过专门转换和截断。

## 26. 不要让 Debug 文本成为公共协议

Debug 格式可能随字段重构改变，不应被客户端解析。

若消费者需要机器判断，应该提供：

```text
enum category
structured fields
protocol error info
```

而不是要求它正则匹配 Rust 的 `Debug` 输出。

## 27. 错误怎样返回给模型

模型可见工具调用使用 `FunctionCallError`：

```rust
enum FunctionCallError {
    RespondToModel(String),
    Fatal(String),
}
```

这是一个控制流选择，而不只是文案风格。

## 28. `RespondToModel` 表示什么

在 `stream_events_utils.rs` 中，`RespondToModel(message)` 被转成 `FunctionCallOutput`，记录进 conversation，然后设置：

```text
needs_follow_up = true
```

也就是说，模型能看到工具错误，并有机会：

- 修正参数；
- 换命令；
- 解释失败；
- 请求用户提供信息。

## 29. `Fatal` 表示什么

`FunctionCallError::Fatal(message)` 会转成 `CodexErr::Fatal(message)` 并终止当前处理路径。

它适合“系统不能可靠继续”的错误，而不是每次测试失败或命令 exit 1。

## 30. 一个小例子：参数错了不应杀死 turn

模型调用：

```json
{"process_id": 42, "chars": "hello"}
```

若 process 42 不存在，把提示返回模型通常更有用。模型可以检查之前的 tool output 或重新启动命令。

若核心状态损坏到无法维持会话不变量，才更接近 fatal。

## 31. Sandbox denial 的特殊回传路径

`exec_command` handler 遇到 `UnifiedExecError::SandboxDenied` 时，不只是返回一行错误字符串。

它构造 `ExecCommandToolOutput`，保留：

```text
raw_output
wall_time
exit_code
original_token_count
output_omitted_bytes
process_id = None
```

模型因此能直接读取实际拒绝诊断。

## 32. 为什么 `process_id = None`

Sandbox denial 已是终态。没有可供后续 `write_stdin` 恢复的活进程。

这避免模型误以为它还能继续与被拒绝的会话交互。

## 33. 其他 unified exec 错误怎样处理

其他 `UnifiedExecError` 会变成类似：

```text
exec_command failed for `<display command>`: <debug error>
```

并通过 `RespondToModel` 交给模型。

这里保留 display command 很重要：同一 turn 可能执行多条命令，只说“创建失败”无法定位是哪一条。

## 34. 边界映射可能降低精度

`process_manager.rs` 打开 sandbox session 时：

- `SandboxErr::Denied` 精确映射成 `UnifiedExecError::SandboxDenied`；
- 其他 `CodexErr` 多数映射成 `CreateProcess(format!("{err:?}"))`；
- 其他 `ToolError` 也归入 `CreateProcess`。

这是一个需要留意的“分类变粗”边界。它未必是 bug，但调试时要知道原始类别可能只存在于更底层。

## 35. 错误映射的两个目标会冲突

跨模块映射希望：

1. 上层只依赖稳定、较小的错误 API；
2. 诊断不要失去原始语义。

解决办法通常不是让上层依赖所有底层类型，而是保留少量稳定 category 加结构化 context/source。

## 36. Sandbox denial 无法完全确定

`is_likely_sandbox_denied()` 的注释直说：当前没有完全确定的方法。

原因包括：

- 用户 shell 初始化文件也可能输出 permission denied；
- 程序本身可能访问了无权限文件；
- sandbox、普通文件权限和容器限制可能产生相似文本；
- 不同平台返回的 exit code 不同。

## 37. 当前启发式先做的快速判断

函数立即返回 false 的情况：

```text
sandbox type == None
exit code == 0
```

如果根本没用 sandbox，或命令成功，就不应猜测为 sandbox denial。

## 38. 当前查找的关键词

它在 stderr、stdout 和 aggregated output 中查找：

```text
operation not permitted
permission denied
read-only file system
seccomp
sandbox
landlock
failed to write file
```

命中任一关键词就判断“很可能被 sandbox 拒绝”。

## 39. 为什么三份输出都要查

不同 backend 可能：

- 保持 stdout/stderr 分离；
- 在 PTY 中合流；
- 只在 aggregated transcript 中保留正确顺序。

只查 stderr 会漏掉 PTY 或包装器输出。

## 40. Exit code 2、126、127 的保守处理

没有关键词时，当前实现对这些常见 shell code 快速返回 false：

```text
2    shell builtin/usage 类错误
126  找到命令但不能执行
127  command not found
```

注意 126 也可能伴随 permission denied；若关键词已经命中，前一步仍会返回 true。

## 41. Linux seccomp 的 signal 线索

Unix 上，Linux seccomp backend 若看到：

```text
128 + SIGSYS
```

会视为 sandbox denial 线索。

这是平台特定证据，不应复制成 Windows 或所有 Unix 的通用规则。

## 42. Heuristic 必然有 false positive 与 false negative

```text
False positive
  实际不是 sandbox，却被判断成 denial

False negative
  实际是 sandbox denial，却没识别出来
```

所以函数名是 `is_likely_...`，不是 `is_sandbox_denied_with_certainty`。

## 43. 启发式函数为什么应无副作用

源码注释强调它是 side-effect free。分类函数只回答判断，不自己弹审批、记录审计或重试。

这样调用方才能在拥有完整上下文的位置决定：

- 记录什么 telemetry；
- 是否请求提升权限；
- 是否允许第二次执行。

## 44. 第二条核心原则：可重试不等于应该重跑命令

`CodexErr::is_retryable()` 判断的是通用 Codex turn/model 请求错误是否属于瞬时类别。

它不是：

```text
这个 shell command 能否安全再执行一次？
```

两者必须分开。

## 45. `CodexErr::is_retryable()` 当前认为可重试的类别

包括：

```text
Stream
Timeout
RequestTimeout
UnexpectedStatus
ResponseStreamFailed
ConnectionFailed
InternalServerError
InternalAgentDied
Io
Json
TokioJoin
```

这些多是连接、请求、流或内部瞬时失败。

## 46. 当前明确不自动重试的类别

包括但不限于：

```text
InvalidRequest
ToolCollision
Sandbox
Spawn
ContextWindowExceeded
Quota/usage/auth
UnsupportedOperation
CyberPolicy
RetryLimit
Fatal
TurnAborted/Interrupted
```

重复相同输入通常不能修复确定性错误，或继续尝试存在安全/产品风险。

## 47. 为什么 `Spawn` 不在通用重试列表

找不到 executable、无权限或管道建立失败常是确定性配置问题。立即原样重试大概率得到同样结果。

更好的下一步是：

- 检查路径与 PATH；
- 检查 cwd；
- 检查文件权限；
- 修改执行环境。

## 48. 为什么 `Sandbox` 不走通用重试

Sandbox denial 需要审批政策、permission profile 和安全上下文共同决定是否升级。

如果让普通网络重试器直接重跑，它既不知道能否绕过 sandbox，也无法向用户解释权限变化。

因此 sandbox 有自己的 orchestrator 路径。

## 49. Tool orchestrator 的主流程

`core/src/tools/orchestrator.rs` 的模块注释概括为：

```text
approval
  → select sandbox
  → first attempt
  → sandbox denial 时评估 escalated retry
  → second attempt
```

它把审批、sandbox 选择和有限重试集中在一个 owner 中。

## 50. 不是所有第一次失败都会升级

第一次执行失败后，只有精确匹配：

```rust
CodexErrorDetails::Sandbox(SandboxErr::Denied { ... })
```

才进入升级评估。

timeout、signal、spawn、JSON 或普通 tool rejection 都不会通过这条机制无条件再跑。

## 51. 升级重试还要满足哪些条件

源码还会检查：

- tool 是否允许 `escalate_on_failure()`；
- approval policy 是否允许请求对应权限；
- filesystem policy 是否允许 unsandboxed execution；
- managed network denial 是否有可用审批上下文；
- strict auto-review 是否要求新的 guardian review。

“识别为 denial”只是起点，不是绕过安全边界的通行证。

## 52. Network denial 也要保留结构化上下文

`SandboxErr::Denied` 可携带 `network_policy_decision`。Orchestrator 会尝试提取 host 等审批上下文。

若 managed network 已启用、payload 存在却无法映射为有效审批上下文，当前实现保守返回原 denial，不继续升级。

## 53. Retry reason 与 error message 不是同一用途

面向审批的 retry reason 需要短、稳定、能解释权限变化。

当前普通 sandbox denial 使用：

```text
command failed; retry without sandbox?
```

网络拒绝则说明被 policy 阻止的 host。完整 stderr 仍保存在 output 中，不必把所有细节塞进审批标题。

## 54. 为什么只有第二次 attempt

当前 orchestrator 明确执行 first attempt 和 second attempt。第二次再失败就返回错误，没有无限循环。

有界重试可避免：

- 重复副作用；
- 无限审批；
- 错误分类误判造成循环；
- 持续消耗 CPU、网络和 token；
- 隐藏真正根因。

## 55. 第三条核心原则：重试前先问副作用是否已发生

以下命令失败前可能已经改变外部世界：

```bash
curl -X POST https://example.test/orders
npm publish
git push
python migrate_database.py
```

即使返回“连接断开”，也无法仅凭客户端错误证明服务端没有收到请求。

## 56. 三种重试安全等级

可以这样审查：

| 等级 | 示例 | 默认态度 |
|---|---|---|
| 只读且确定 | `rg pattern src` | 通常可重试 |
| 幂等写入 | `PUT` 同一资源版本、原子替换同内容 | 有身份与前置条件时可重试 |
| 非幂等副作用 | 创建订单、发消息、publish | 不应盲目重试 |

“网络错误可重试”只描述错误来源，不自动证明操作语义安全。

## 57. Idempotency key 的作用

若远端写操作支持稳定 operation ID：

```text
第一次请求成功但响应丢失
第二次携带同一 idempotency key
服务端返回第一次操作的结果，而不是再执行一遍
```

这才把“可能重复”变成可安全恢复的协议。仅靠 sleep/backoff 做不到。

## 58. Retry delay 为什么要独立存储

服务器可能返回 Retry-After。`CodexErr` 用 `Option<Duration>` 保存建议等待时间。

调用方可组合：

```text
server-provided delay
local exponential backoff
remaining total deadline
cancellation
```

若只把秒数写进文本，就很难正确计算。

## 59. 远程 exec-server 有独立恢复系统

远程执行不是本地进程调用的简单同义词。它还可能失败于：

- WebSocket connect timeout；
- WebSocket connection error；
- initialize handshake timeout；
- transport closed/disconnected；
- JSON serialization；
- RPC protocol/server error；
- environment registry request/auth/config；
- provisioning mode conflict。

这些由 `ExecServerError` 结构化表达。

## 60. `ConnectionAttempt(Arc<ExecServerError>)` 的意义

多个调用者可能同时等待同一次连接。连接尝试结果被共享，外层用 `ConnectionAttempt` 保留真正的 source。

这样可以避免：

- 每个 caller 同时发起重复连接；
- 失败后只剩相同字符串的多个副本；
- 丢掉原始 variant。

## 61. Startup 与 reconnect 的缓存策略不同

初次启动结果通过共享 attempt 保存，使等待者看到同一结果。

重连 attempt 完成后会从 `reconnect` 槽移除；因此后续操作在一次失败之后仍能发起新的恢复尝试。

这是一种 singleflight：同一波故障共享工作，但不把一次重连失败永久缓存。

## 62. Status 检查不会偷偷触发恢复

`status()` 和 health check 使用 fail-fast 路径，报告已知 disconnected 状态，不为了“查询状态”启动或恢复连接。

这是良好的只读 API 语义：观察不应意外产生昂贵或有副作用的连接动作。

## 63. 远程恢复只重试明确的瞬时类别

`is_retryable_recovery_error()` 当前接受：

- transport closed；
- WebSocket connect timeout/error；
- initialize timeout；
- 部分 registry 瞬时错误；
- session already attached 特定 server code。

它没有对全部 `ExecServerError` 返回 true。

## 64. Registry 哪些状态可重试

当前包括：

```text
连接错误或请求 timeout
HTTP 5xx
HTTP 408 Request Timeout
HTTP 429 Too Many Requests
HTTP 409 + environment_offline
```

最后一项源码带 TODO，承认这是较粗的策略，未来更适合显式状态机。

## 65. 哪些远程错误不应原样反复重试

例如：

- registry authentication error；
- registry configuration error；
- provisioning mode conflict；
- 普通 protocol incompatibility；
- 非瞬时 server rejection。

凭据、配置或协议错不会因为等待 100 ms 自动变好。

## 66. 远程恢复有总 deadline

非测试构建的 session recovery deadline 当前为 25 秒，给服务端 30 秒保留窗口留出余量。

循环内每次 sleep 都限制为：

```text
min(retry_delay, deadline - now)
```

所以局部 backoff 不会突破总恢复预算。

## 67. 固定间隔与指数退避

普通恢复当前使用 100 ms 间隔；registry/rendezvous 路径从 500 ms 开始指数增长，base delay 上限为 5 秒。

概念上：

```text
base = min(500ms × 2^attempt, 5s)
actual = base + deterministic jitter
```

## 68. Jitter 为什么存在

若许多 client 同时断线，又在完全相同的 1、2、4 秒重试，服务端会遇到同步洪峰。

Jitter 给等待时间增加差异，使重试分散。当前实现用 session ID 和 attempt hash 生成确定性抖动，便于稳定又避免所有 session 对齐。

## 69. 恢复失败时怎样组合上下文

最终错误类似：

```text
<原断线原因>;
failed to resume exec-server session:
<最后恢复错误或 recovery timeout>
```

这同时回答：

- 为什么开始恢复；
- 恢复本身为什么没成功。

只保留最后一次 connect error 会丢掉事件起点。

## 70. RPC 错误也有多个阶段

`RpcCallError` 区分：

```text
Closed
Json
Server
TimedOut { method, timeout }
PendingRequestLimitExceeded { limit }
```

尤其要区分：

```text
执行的命令超时
等待某个 RPC method 响应超时
```

二者可能需要不同 owner 清理和不同用户提示。

## 71. RPC 映射会补充 method 与 timeout

`RpcCallError::TimedOut` 被映射为 protocol message：

```text
timed out waiting for exec-server `<method>` response after <duration>
```

这比“request timeout”更可定位：读者知道卡在哪个方法、等了多久。

## 72. Transport closed 会归一为 disconnected

`map_rpc_call_result()` 先将 RPC error 转为 `ExecServerError`；若属于 transport closed，再转成统一的 `Disconnected` message。

归一化使恢复逻辑不必认识每一种底层关闭表现，但原始诊断仍应在适当日志/上下文中保留。

## 73. 晚到的网络拒绝怎样处理

managed network 的拒绝可能在进程刚退出后才到达。Process manager 会短暂等待 late denial，并让 network monitor 与 process exit 协调。

若拒绝最终到达，它会让进程进入 failure 并 terminate，防止把受 policy 拒绝的执行误报成普通成功。

## 74. 第一条 failure message 应保持稳定

`fail_process_with_message` 若发现 process 已有 failure message，会保留第一条，再执行 terminate。

为什么不覆盖？因为后续“清理失败”常只是原始故障的结果。覆盖后用户只看到次生错误，真正触发点反而消失。

## 75. 错误上下文应该保存什么

进程错误至少考虑：

```text
操作：spawn/read/write/signal/terminate/recover
命令：安全展示形式，不泄漏 secret
执行位置：local/remote/environment ID
cwd 与目标路径命名空间
exit code / signal / timed_out
stdout / stderr / aggregated order
duration / deadline
sandbox 与 permission context
network host/decision
process ID / call ID / session ID
重试次数与最后错误
截断元数据
```

并非每一项都应展示给终端用户，但需要在正确层保留。

## 76. Secret 与诊断完整性存在张力

错误中加入完整 command/env 有助调试，却可能泄露：

- token；
- Authorization header；
- 密码；
- 私有 URL query；
- stdin 输入的密钥。

因此“保留上下文”必须配合结构化敏感字段、脱敏和最小展示，而不是无条件 dump 全部环境。

## 77. 用户可读错误的五个问题

一条好提示尽量回答：

1. **什么操作失败？** 启动、执行、等待还是恢复？
2. **失败对象是谁？** 哪条命令、哪个 process 或 environment？
3. **系统知道的原因是什么？** exit、permission、timeout、connection？
4. **是否产生了部分结果？** output、副作用、截断？
5. **下一步是什么？** 修参数、换路径、请求权限、稍后重试？

## 78. 三个提示的对比

差：

```text
Something went wrong.
```

稍好：

```text
Command failed.
```

可行动：

```text
Failed to start `cargo`: executable was not found in PATH.
Check the selected environment or use an absolute executable path.
```

最后一条同时包含动作、对象、原因和下一步。

## 79. 不要把内部类型名直接当用户说明

用户不必理解：

```text
CodexErrorDetails::Sandbox(SandboxErr::Timeout)
```

他需要的是：

```text
command timed out after 10000 ms
```

内部 category 为机器决策服务，展示层负责翻译。

## 80. `CodexErrorInfo` 是更小的客户端协议分类

`to_codex_protocol_error()` 将复杂内部错误映射为较稳定的客户端类别，例如：

```text
Sandbox → SandboxError
auth refresh → Unauthorized
thread/unsupported/agent limit → BadRequest
connection → HttpConnectionFailed
stream → ResponseStreamConnectionFailed
usage/quota → UsageLimitExceeded
```

未专门公开的类别归为 `Other`。

## 81. 为什么客户端协议不复制全部内部 enum

如果每个 Rust 内部重构都改变公开 wire enum，所有 App、TUI 和 SDK 都要同步升级。

较小的协议分类提供兼容边界；详细 message 和 telemetry 仍可提供诊断。

## 82. UI error 有 2 KiB 上限

`get_error_message_ui()` 最终使用：

```text
TruncationPolicy::Bytes(2 * 1024)
```

这是展示上限，不代表底层 output buffer 也只有 2 KiB。

## 83. 为什么 UI 必须有界

未经限制的 stderr 可能：

- 淹没终端；
- 卡顿或撑大事件；
- 挤掉更关键的上下文；
- 把巨量不可信文本呈现给用户；
- 增加模型上下文和日志成本。

完整诊断、模型可见 tool output、telemetry 和 UI 摘要可以有不同预算。

## 84. Sandbox denial 的 UI 输出优先级

当前顺序是：

1. 非空 aggregated output；
2. stderr 与 stdout；
3. 只有 stderr；
4. 只有 stdout；
5. 都为空则展示 sandbox 内失败及 exit code。

优先 aggregated output 能保留两条 stream 的相对顺序。

## 85. 为什么一定要有空输出 fallback

Sandbox 可能直接拒绝某个系统调用而没有可读输出。若 UI 只显示空字符串，用户会误以为界面坏了。

Fallback 至少提供：

```text
command failed inside sandbox with exit code N
```

## 86. 截断应该保留头还是尾

进程错误常同时需要：

- 开头：命令、配置、编译阶段；
- 结尾：最终 exception、summary、exit reason。

因此课程前章介绍的 head-tail buffer 比只留 prefix 更适合长命令输出。UI 再根据自己的 2 KiB 预算做第二层截断。

## 87. Telemetry 分类不能依赖最终文案

Orchestrator 的 `sandbox_outcome_from_tool_error()` 用 enum match 分类：

```text
denied
timed_out
signal
```

这比对 UI message 做字符串搜索可靠。文案可本地化或调整，机器指标仍保持稳定。

## 88. 一次失败怎样穿过各层

以 sandbox denial 为例：

```text
OS/program output
  ↓
ExecToolCallOutput
  ↓
SandboxErr::Denied
  ↓
CodexErrorDetails::Sandbox
  ↓
ToolError::Codex
  ↓
ToolOrchestrator 判断是否允许第二次 attempt
  ↓ 若最终仍拒绝
UnifiedExecError::SandboxDenied
  ↓
ExecCommandToolOutput（保留 raw output 与截断元数据）
  ↓
模型读取诊断并决定下一步
```

客户端级 fatal error 则还可能映射到 `CodexErrorInfo::SandboxError` 和有界 UI message。

## 89. 诊断决策树

遇到“命令失败”，依次问：

```text
进程是否成功创建？
├─ 否 → spawn/path/env/cwd/permission
└─ 是
   ├─ 是否拿到 ExecToolCallOutput？
   │  ├─ 是 → 看 exit code、timeout、stdout/stderr
   │  └─ 否 → 看 process/RPC/transport error
   ├─ 是否明确或很可能 sandbox denied？
   │  ├─ 是 → 看 policy、审批与原始 output
   │  └─ 否 → 不要擅自升级权限
   ├─ 是否远程断线？
   │  ├─ 是 → 看 session recovery 与最后事件
   │  └─ 否 → 定位本地 owner
   └─ 重试是否可能重复副作用？
      ├─ 是 → 先对账或依赖 idempotency key
      └─ 否 → 在总 deadline 内有界重试
```

## 90. 示例一：测试失败

```bash
just test -p codex-tui
```

返回 exit 1 和断言 diff。

正确理解：

- 进程执行系统成功；
- 测试程序报告失败；
- 应把 diff 交给模型；
- 修改代码或 snapshot 后再由工作流决定是否重跑；
- 不应因 exit 1 自动请求绕过 sandbox。

## 91. 示例二：找不到命令

```text
No such file or directory
```

排查顺序：

1. executable/argv 是否正确？
2. PATH 是否被 env policy 清理？
3. 远程 target 是否安装该工具？
4. cwd 是否存在？
5. 路径是否属于另一个 OS namespace？

原样重试通常没有意义。

## 92. 示例三：permission denied

它至少可能表示：

- 普通 Unix 文件权限；
- 文件不可执行；
- sandbox 拒绝写入；
- read-only filesystem；
- shell profile 中某条命令失败。

因此必须结合 sandbox type、exit code、完整 output、目标路径和执行阶段判断。

## 93. 示例四：远程断线但 publish 可能已成功

```text
npm publish
→ 服务端收到并完成
→ response 返回前 WebSocket 断开
```

客户端只知道结果未知。盲目重跑可能得到重复版本错误，甚至重复外部动作。

正确恢复通常是先查询 registry/制品状态，再决定是否需要补偿，而不是看到 `Disconnected` 就重放命令。

## 94. 示例五：命令 timeout

Timeout 说明外部 deadline 到达，不等于程序内部没有产生结果。

应检查：

- 已收集 output；
- 是否发送 terminate/kill；
- 后代进程是否清理；
- 是否完成 stdout/stderr drain；
- 外部副作用是否已发生；
- UI 是否清楚显示持续时间。

## 95. 写错误类型时的模式

优先考虑：

```rust
#[derive(Debug, thiserror::Error)]
enum MyError {
    #[error("failed to open environment `{environment_id}`: {source}")]
    OpenEnvironment {
        environment_id: String,
        #[source]
        source: io::Error,
    },
    #[error("operation timed out after {timeout:?}")]
    TimedOut { timeout: Duration },
}
```

关键是稳定分类、具体 context 和 source chain，不是追求很长的句子。

## 96. 什么时候添加 context

在最了解该动作的边界添加。例如底层只知道 `io::Error`，调用层才知道它正在：

```text
spawn exec-server
for environment abc
using websocket URL xyz
```

不要要求最底层猜业务对象；也不要等到 UI 层才丢失所有结构后拼字符串。

## 97. 什么时候转换错误类别

当跨越明确抽象边界时转换，例如：

```text
RpcCallError → ExecServerError
SandboxErr → UnifiedExecError
CodexErr → CodexErrorInfo
FunctionCallError → conversation output 或 turn error
```

转换时逐项问：控制流、机器分类、人类诊断是否仍足够。

## 98. 错误测试应该断言什么

比起只断言字符串包含 `failed`，更应验证：

- 精确 variant；
- source 是否保留；
- exit code、duration、method、process ID；
- stdout/stderr 选择顺序；
- 截断上限和 UTF-8 安全；
- retryable/non-retryable 分类；
- 重试次数与 deadline；
- 第二次执行是否需要审批；
- 非幂等动作是否未被自动重放。

## 99. Error injection 的价值

真实断线和超时很难稳定复现。测试可在受控边界注入：

```text
spawn error
RPC closed
initialize timeout
sandbox denied output
late network denial
recovery first fail then succeed
deadline exhausted
```

然后验证终态、输出、重试次数和清理，而不只验证“返回了 Err”。

## 100. 审查错误与重试代码的清单

```text
普通非零 exit 是否仍保留为 outcome？
spawn、timeout、signal、sandbox、RPC 是否可区分？
错误跨层转换时丢了哪些字段？
source chain 是否存在？
Display、Debug、protocol category、UI 文案是否分工明确？
模型能否看见足够信息自我修正？
Fatal 是否只用于真正无法继续的情况？
sandbox denial 判断是否明确承认启发式？
升级是否受审批和 permission policy 约束？
重试是否有次数或总 deadline？
重试操作是否只读、幂等，或有 idempotency key？
response 丢失时是否先对账副作用？
first failure 是否会被 cleanup failure 覆盖？
output、exit code、duration 与 truncation metadata 是否保留？
UI 是否有界并提供空输出 fallback？
日志是否泄漏命令参数、env 或 secret？
telemetry 是否依赖结构化 variant 而非文案？
测试是否覆盖失败之后的资源清理？
```

## 101. 理解检查

### 问题 1

为什么命令 `exit 1` 不一定让 Rust 执行函数返回 `Err`？

<details><summary>参考答案</summary>

因为进程成功创建并完成，执行系统已取得 stdout、stderr 和 exit code。非零是被执行程序的 outcome，不一定是执行基础设施故障。

</details>

### 问题 2

为什么不能看到 `permission denied` 就无条件绕过 sandbox 重试？

<details><summary>参考答案</summary>

文本可能来自普通文件权限、shell 初始化或 sandbox；即使确为 sandbox denial，升级仍必须满足审批与 permission policy。

</details>

### 问题 3

`CodexErr::is_retryable()` 为 true，是否证明 shell 命令可安全重跑？

<details><summary>参考答案</summary>

不能。它描述通用错误的瞬时性质；命令是否可重跑还取决于副作用、幂等性、operation identity 和结果是否未知。

</details>

### 问题 4

为什么 sandbox denial 要保留原始 output？

<details><summary>参考答案</summary>

分类只能说明受限，output 才可能指出具体文件、系统调用、host 和工具诊断，并让模型修正下一步。

</details>

### 问题 5

`RespondToModel` 与 `Fatal` 的关键区别是什么？

<details><summary>参考答案</summary>

前者把错误作为 tool output 放回 conversation，让模型继续处理；后者转成 Codex fatal error，结束当前路径。

</details>

### 问题 6

为什么远程恢复既需要单次 retry delay，也需要总 deadline？

<details><summary>参考答案</summary>

Delay 控制每次尝试节奏和服务端压力；总 deadline 保证整个恢复过程有界，不因不断重试永久卡住。

</details>

### 问题 7

为什么先保留第一条 failure message？

<details><summary>参考答案</summary>

后续 terminate、drain 或 cleanup 错误往往是次生结果；覆盖第一条会隐藏真正触发故障的原因。

</details>

### 问题 8

为什么 UI 只显示 2 KiB 不代表底层只保存 2 KiB？

<details><summary>参考答案</summary>

不同边界有不同容量预算。底层执行输出、模型 tool result、telemetry 和最终 UI 摘要会分别保存或截断。

</details>

## 102. 本章词汇表与代码名称翻译

| 代码或术语 | 中文理解 | 在本章中的作用 |
|---|---|---|
| Error | 错误 | 操作未能按其接口契约完成的结构化结果 |
| Outcome | 执行结果 | 程序已完成并给出 exit/output，不等同基础设施错误 |
| Infrastructure error | 基础设施错误 | spawn、transport、RPC 等执行机制故障 |
| Classification / category | 分类/类别 | 供 match、重试、协议和指标稳定判断的机器语义 |
| Diagnostic | 诊断信息 | 帮助人或模型解释根因与下一步的上下文 |
| Variant | 枚举分支 | Rust enum 中一种带类型的错误形状 |
| `thiserror` | 错误派生库 | 生成 Error/Display 并声明 source 的 Rust 库 |
| Source / error chain | 原因/错误链 | 外层操作上下文连接到底层原因的链条 |
| Context | 上下文 | command、cwd、method、ID、duration 等定位信息 |
| `Display` | 面向显示的格式 | 相对稳定、可读的错误文本 |
| `Debug` | 调试格式 | 展示 variant 和内部字段的开发诊断 |
| `UnifiedExecError` | 统一执行错误 | unified exec 内部启动、会话、stdin、sandbox、path 错误 |
| `ToolError::Rejected` | 工具拒绝 | 策略或验证决定不执行，不等于 runtime crash |
| `ToolError::Codex` | Codex 结构化错误 | 将 `CodexErr` 带过 tool orchestrator |
| `SandboxErr` | sandbox 错误 | 拒绝、超时、signal 与平台 sandbox 设置故障 |
| `CodexErrorDetails` | Codex 错误详情 | core/protocol 范围的语义错误总表 |
| `CodexErr` | Codex 错误包装 | 组合 details 与可选 retry delay |
| `FunctionCallError` | 工具调用控制错误 | 决定错误返回模型还是升级为 fatal |
| `RespondToModel` | 回应给模型 | 把错误变成 tool output，让 Agent 自我修正 |
| `Fatal` | 致命错误 | 当前处理无法可靠继续 |
| `CodexErrorInfo` | 客户端错误类别 | 比内部 enum 更小、更稳定的 wire 分类 |
| Sandbox denial | sandbox 拒绝 | 安全限制阻止文件、网络或系统动作 |
| Approval rejection | 审批拒绝 | 动作执行前的授权决定 |
| Heuristic | 启发式 | 有证据但不保证完全正确的分类规则 |
| False positive | 假阳性 | 不是 denial 却被判断成 denial |
| False negative | 假阴性 | 是 denial 却没有识别出来 |
| Retryable | 可重试 | 错误可能是瞬时的，不自动证明操作可安全重放 |
| Retry / replay | 重试/重放 | 再次发起尝试或重新执行相同操作 |
| Escalation | 权限升级尝试 | 在政策允许和审批后改变 sandbox 策略再执行 |
| Attempt | 一次尝试 | first/second execution 的一个有界实例 |
| Idempotent | 幂等 | 重复操作与执行一次具有相同业务效果 |
| Idempotency key | 幂等键 | 让服务端识别重复业务操作的稳定 ID |
| Backoff | 退避 | 失败后逐步延长重试间隔 |
| Exponential backoff | 指数退避 | 间隔按近似 2 的幂增长并设上限 |
| Jitter | 抖动 | 打散多个 client 同步重试的时间偏移 |
| Deadline | 总截止点 | 限制整个恢复过程的绝对时间边界 |
| Singleflight | 单航班/请求合并 | 同一波 caller 共享一次连接或恢复尝试 |
| `ExecServerError` | 远程执行服务错误 | 远程连接、registry、RPC、protocol 等错误 |
| `RpcCallError` | RPC 调用错误 | closed、JSON、server、timeout、容量错误 |
| Transport | 传输层 | WebSocket/stdio 等承载 RPC 消息的连接 |
| Handshake | 握手 | client 与 exec-server initialize 建立会话的步骤 |
| Recovery | 恢复 | 断线后在 deadline 内重连并恢复 session |
| Aggregated output | 聚合输出 | 按观察顺序合并 stdout/stderr 的 transcript |
| Truncation metadata | 截断元数据 | 原 token 数、省略 byte 数等不完整性说明 |
| Fallback | 兜底 | 主诊断为空时提供最低限度提示 |
| Telemetry outcome | 遥测结果分类 | 用稳定 variant 统计 denied/timeout/signal |
| Redaction | 脱敏 | 隐去 token、密码和其他敏感字段 |
| Side effect | 副作用 | 写文件、发请求、publish 等改变外部世界的动作 |
| Reconciliation | 对账 | 结果未知时查询权威状态再决定补偿或重试 |

## 103. 源码检查点

1. `codex-rs/core/src/unified_exec/errors.rs`
   - 看 `UnifiedExecError` 的分类、`SandboxDenied` payload 与截断元数据。
2. `codex-rs/protocol/src/error.rs`
   - 看 `SandboxErr`、`CodexErrorDetails`、`CodexErr::is_retryable()`、协议映射与 UI message。
3. `codex-rs/sandboxing/src/denial.rs`
   - 看 sandbox denial 关键词、快速拒绝 code 和 Linux SIGSYS 启发式。
4. `codex-rs/core/src/tools/sandboxing.rs`
   - 看 `ToolError`、`SandboxAttempt`、`ToolRuntime::escalate_on_failure()`。
5. `codex-rs/core/src/tools/orchestrator.rs`
   - 看 approval → first attempt → denial-only escalation → second attempt。
6. `codex-rs/core/src/unified_exec/process_manager.rs`
   - 看 sandbox error 映射、late network denial、第一条 failure 与 terminate。
7. `codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`
   - 看 `SandboxDenied` 怎样保留 raw output，其他错误怎样 `RespondToModel`。
8. `codex-rs/tools/src/function_call_error.rs`
   - 看 `RespondToModel` 与 `Fatal` 两个控制流分支。
9. `codex-rs/core/src/stream_events_utils.rs`
   - 看 tool error 怎样进入 conversation 或升级为 `CodexErr::Fatal`。
10. `codex-rs/exec-server/src/client.rs`
    - 看 `ExecServerError`、lazy startup/reconnect、fail-fast status 和 RPC 映射。
11. `codex-rs/exec-server/src/rpc.rs`
    - 看 `RpcCallError` 对 closed、JSON、server、timeout 和容量的区分。
12. `codex-rs/exec-server/src/client_recovery.rs`
    - 看 retryable 分类、25 秒 deadline、100 ms interval、registry backoff 与 jitter。
13. `codex-rs/protocol/src/error_tests.rs`
    - 看 sandbox output 选择、空输出 exit-code fallback 和其他错误格式测试。
14. `codex-rs/exec-server/src/client_recovery_tests.rs`
    - 看 registry 退避、可重试分类和 process event 重排测试。

## 104. 一句话总结

> 可靠的错误处理不是把失败变成一句字符串，而是先把程序 outcome 与执行基础设施错误分开，跨层保留 category、source、output、时间和截断证据，只在策略允许、总预算有界且副作用可安全去重时重试，最后再把内部事实翻译成模型与用户都能采取下一步行动的诊断。
