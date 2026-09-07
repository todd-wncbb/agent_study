# 附录：错误分类与排查目录

## 1. 先按责任边界分类

| 类别 | 典型现象 | 首要检查位置 | 通常是否可重试 |
|---|---|---|---|
| 配置 | 启动失败、Feature 未生效 | config layer/origin、requirements | 修改配置后重试 |
| 认证 | 401、登录过期、模型列表为空 | `AuthManager`、Provider auth | 视永久/瞬态错误 |
| 模型目录 | 模型能力不对、选错默认模型 | `ModelsManager`、cache、fallback metadata | 刷新目录 |
| 请求构造 | Schema/API 拒绝 | `Prompt`、tool specs、wire API | 修复请求，不盲重试 |
| Streaming | 中途断流、idle timeout | stream retry/reconnect | 有界重连 |
| 工具解析 | unknown tool、参数无效 | ToolRegistry/spec 名称与 Schema | 返回结构化错误给模型 |
| Approval | 长时间等待 | approval request/response ID | 等用户或取消 |
| Sandbox | permission denied | policy、writable roots、网络权限 | 变更权限后重试 |
| Executor | 本地可行远端失败 | environment/capabilities/path mapping | 视环境状态 |
| Context | 超窗口、工具输出过大 | token status、truncation、compaction | 压缩/截断后重试 |
| MCP | server 不可达、工具变化 | connection manager、required server | 有界初始化/刷新 |
| Persistence | resume 缺项、rollout 损坏 | recorder/state DB | 隔离坏记录并报告 |
| Hook/Extension | Turn 被停止、功能缺失 | hook outcome、extension data | 依 Hook 策略 |
| Memory | 没生成或读不到 | Phase 1/2 job、artifacts、Feature | 后台退避重试 |

## 2. 错误的四个维度

实现中不要只保留字符串。至少应记录：

```text
source       哪一层产生：model/tool/sandbox/mcp/config
retryability permanent / transient / unknown
scope        item / step / turn / thread / process
visibility   model-visible / user-visible / telemetry-only
```

例如一次 shell 退出码非零通常是“Tool Item 级、模型可见、可由模型修复”，不应升级成整个 Thread 崩溃。认证失效则可能是“Turn 级、用户可见、需要重新登录”。

## 3. 配置错误

排查顺序：

1. 查看最终 effective config；
2. 查看字段 origin，确认是哪一层覆盖；
3. 检查 requirement 是否禁止该值；
4. 检查 profile 是否真的被选择；
5. 检查 Feature stage、依赖和重启要求；
6. 检查 Turn override 是否只对当前 Turn 生效。

常见误判是“文件里写了，所以运行时一定使用”。多层配置系统里，源文件值不等于最终值。

## 4. 模型与认证错误

### 401/403

- 当前 `AuthMode` 是否匹配 Provider；
- token 是否接近过期但刷新失败；
- `base_url` 是否因认证模式选择错误；
- command auth 是否输出空 token；
- Header 名和值是否可解析；
- 权限错误是否其实是模型/组织不可用。

永久 OAuth 错误不要高频重试。刷新 token 已失效或复用时，应停止自动重试并要求用户重新认证。

### 模型能力和实际不一致

检查 `used_fallback_model_metadata`。未知模型 slug 仍可运行，但可能错误判断并行工具、Web Search、上下文窗口或 Responses Lite。

## 5. Streaming 错误

应区分：

- 建连前失败；
- 收到部分内容后断流；
- idle timeout；
- 服务端显式 error event；
- 客户端取消。

收到部分 Tool Call 后重新提交可能重复副作用，必须依赖响应 ID、call ID 或服务端续流语义，而不是一律从头重试。

## 6. 工具错误

### unknown tool

模型可见 ToolSpec 与执行期 ToolRegistry 不一致。检查动态 MCP/Extension 更新是否同时刷新了两边，以及名称 namespace 是否一致。

### invalid arguments

保留原始 call ID，返回短而明确的 Schema 错误。让模型有机会修正参数，不应把反序列化栈追踪整段塞回上下文。

### output too large

应在 Tool Result 进入 History 前截断或 spill to disk。只在 UI 截断但仍把全文发送模型并不能解决上下文问题。

### tool timeout

错误中应包含工具名、超时阶段、是否已产生副作用。对写操作不要自动假设“超时即未执行”。

## 7. Approval 与 Sandbox

Approval denial 是正常业务结果，不是系统异常。它应该转成模型可理解的 denial output，使模型寻找替代方案。

Sandbox denial 的诊断信息应说明：

- 被拒绝的能力：读、写、执行、网络；
- 目标路径或域；
- 当前 policy；
- 是否可以通过 approval 提升；
- 已发生的部分副作用。

永远不要为了“自动修好”而静默绕过 sandbox。

## 8. Context 与压缩错误

症状包括请求体超限、压缩循环、刚压缩又立刻超限。检查：

- model context metadata 是否准确；
- effective percentage 和 auto compact scope；
- 初始 prefix 是否本身过大；
- world state、AGENTS、Skill 是否无界；
- Tool output 是否在记录前截断；
- 压缩后是否重复注入旧长内容；
- fallback buffer 是否配置合理。

若固定前缀已经超过窗口，摘要 History 无法解决问题，必须缩减前缀本身。

## 9. MCP / Plugin / Extension 错误

- required MCP 初始化失败应阻止依赖它的 Turn，optional MCP 可降级；
- 工具目录变化要使下一 Step 重建 ToolSpec；
- Extension 缺少 thread-local data 时应返回空贡献，而不是 panic；
- Hook 停止执行要有明确 outcome，区别于 Hook 自己崩溃；
- Plugin 是能力打包，真正的错误仍应归因到其 Skill/MCP/App 组件。

## 10. Resume 与 Rollout 错误

恢复时要验证：

- rollout item 是否可反序列化；
- function call/output 是否配对；
- working directory 和环境是否仍存在；
- 旧 Tool 名称是否仍可用；
- 配置和模型是否发生不兼容变化；
- 未完成 Turn 应标记中断还是继续。

不要改写历史来“修复”坏记录；更安全的做法是保留原始记录，追加恢复说明或从最后一个一致边界继续。

## 11. 长期记忆错误

Phase 1 常见问题：无 State DB、没有 eligible rollout、lease 冲突、模型输出不符合 Schema、secret redaction 或持久化失败。

Phase 2 常见问题：全局锁未取得、workspace baseline 损坏、输入同步失败、整合 Agent 权限不足、heartbeat 丢失、产物校验失败。

这些后台错误不应让当前用户 Turn 失败；应记录 metrics/log，并依 lease/backoff 在后续启动中恢复。

## 12. 一套实用诊断流程

```text
1. 确定 scope：Item、Step、Turn 还是 Thread
2. 找稳定 ID：thread_id、turn_id、call_id、response_id
3. 沿事件时间线定位最后一个成功边界
4. 检查该边界对应的配置/环境快照
5. 判断是否已有部分副作用
6. 分类永久或瞬态
7. 选择：返回模型、提示用户、有界重试、终止 Turn
8. 验证 History 与持久记录仍保持一致
```

## 13. 错误处理反模式

- 捕获所有错误后返回空结果；
- 对所有 4xx/5xx 使用同一重试策略；
- 工具失败后不生成 call output；
- 将内部敏感 Header 或 token 写进用户错误；
- 把 approval denial 当 panic；
- 工具超时后立即无条件重跑写操作；
- 为恢复方便改写既有 History；
- 后台维护任务失败导致交互 Session 退出。

