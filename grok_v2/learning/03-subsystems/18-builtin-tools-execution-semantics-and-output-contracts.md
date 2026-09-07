# 内置工具实现与执行语义：Bash、读取、搜索、编辑、后台任务和输出契约

> 本篇讨论的是“工具真正开始执行以后发生什么”。工具如何注册、如何注入 `Resources`，见[ToolBridge、工具注册表与 Resources](04-tool-bridge-registry-and-resources.md)；权限与审批见[权限判定与审批状态机](05-permission-approval-state-machine.md)。

## 1. 先给结论

理解 Grok Build 的内置工具，最容易犯的错误是把它们想成一组“输入 JSON，返回字符串”的普通函数。源码里的实际模型更丰富：

1. 工具用带关联类型的 `Tool` trait 描述参数和输出，运行时再通过 `ToolDyn` 擦除具体类型。
2. 参数既要经过 JSON 反序列化，也可能经过额外的资源级约束校验；JSON Schema 是模型看到的契约，不等于全部运行时语义。
3. 工具可以产生若干 `Progress`，但必须以且仅以一个 `Terminal` 结束。
4. 最终结果至少有两种投影：机器可序列化的干净输出，以及发给模型的 `prompt_text`。后者可以加入提示和提醒，不应反向污染协议数据。
5. Bash、读文件和 grep 都有各自的限额与流式策略，不能把“截断”理解成同一种算法。
6. `search_replace` 是单文件的精确匹配编辑器；`apply_patch` 是多文件补丁解释器。二者的匹配、失败和原子性边界不同。
7. Bash 后台任务不是一个被阻塞的工具调用。启动调用先返回任务句柄，进程继续由终端后端拥有，随后通过查询、等待、通知或终止工具管理。
8. “先在内存中算完所有 patch”只保证写入前的补丁可计算性检查，不等于多文件写入事务；第三阶段中途失败时，已经写入的文件不会由这段代码自动回滚。

一句话心智模型是：

```text
模型参数
  → 动态名称还原与参数校验
  → typed Tool::execute / Tool::run
  → 文件系统、终端等后端
  → typed output / progress / notifications
  → 干净协议结果 + 模型提示投影 + 客户端事件
```

## 2. 本篇边界

`xai-grok-tools` 中的内置工具很多，包括计划模式、Todo、目标、Subagent、网页搜索、媒体生成、LSP、工作流和调度器。本篇不逐个翻译实现，而是选择最能说明执行语义的五组工具：

| 工具族 | 代表工具 | 要理解的核心问题 |
| --- | --- | --- |
| 命令执行 | `run_terminal_cmd` | 超时、进程所有权、流式输出、前后台切换 |
| 文件读取 | `read_file` | 路径解析、格式分派、行窗口、Token 上限 |
| 搜索与浏览 | `grep`、`list_dir` | 外部搜索进程、结果预算、结构化错误 |
| 文件编辑 | `search_replace`、`apply_patch` | 精确匹配、补丁计算、写入边界、通知 |
| 后台控制 | `get_task_output`、`kill_task` | 句柄、快照、等待、取消与最终状态 |

这些工具覆盖了只读、写入、长任务、流式和多文件修改，因此足以建立阅读其他内置工具所需的通用框架。

## 3. 代码地图

主要源码位于：

```text
crates/common/xai-tool-runtime/src/
└── tool.rs                         # Tool、ToolDyn、ToolStream、TypedToolOutput

crates/codegen/xai-grok-tools/src/
├── implementations/
│   ├── grok_build/
│   │   ├── bash/mod.rs             # run_terminal_cmd
│   │   ├── read_file/mod.rs        # read_file
│   │   ├── grep/                    # grep 与 ripgrep 定位
│   │   ├── list_dir/               # 目录读取
│   │   ├── search_replace/         # 单文件字符串替换
│   │   ├── task_output/            # 后台任务输出与等待
│   │   └── kill_task/              # 后台任务终止
│   ├── codex/apply_patch/          # Codex patch 解析与应用
│   └── editor_infra/               # 编辑器基础设施
├── computer/
│   ├── types.rs                    # FileSystem、TerminalBackend 等后端接口
│   └── local/terminal.rs           # 本地终端任务实现
├── types/
│   ├── context.rs                  # TruncationConfig
│   ├── output.rs                   # 统一工具输出及模型投影输入
│   ├── params_validation.rs        # 参数错误定位
│   └── resources.rs                # 运行依赖容器
└── util/truncate.rs                # 截断、软换行和 Token 粗估
```

命名空间也很重要。同一种能力不一定只有一种外观：

- `grok_build` 是 Grok Build 风格的主工具集；
- `grok_build_concise` 提供更紧凑的模型输出；
- `grok_build_hashline` 提供 hashline 风格的读取与编辑；
- `codex` 包含 `apply_patch` 等 Codex 风格工具；
- `opencode` 提供另一组兼容外观。

因此“编辑工具”是能力分类，不是单一实现名。

## 4. `Tool`：编译期类型契约

`crates/common/xai-tool-runtime/src/tool.rs` 中的 `Tool` trait 使用两个关联类型：

```rust
trait Tool {
    type Args: DeserializeOwned + JsonSchema + Send;
    type Output: Serialize + ToolOutput + Send;

    fn id(&self) -> ToolId;
    fn description(&self, ctx: &ListToolsContext) -> ToolDescription;
    async fn run(&self, ctx: ToolCallContext, args: Self::Args)
        -> Result<Self::Output, ToolError>;
    async fn execute(&self, ctx: ToolCallContext, args: Self::Args)
        -> ToolStream<Self::Output>;
}
```

这是概念化摘录，阅读时重点看四件事：

- `Args` 决定 JSON 如何被反序列化，也能生成模型调用所需的 Schema；
- `Output` 保留领域结构，不要求每个工具手写字符串协议；
- 普通工具只实现 `run` 即可；默认 `execute` 会把它包装成只有一个终态的流；
- 需要实时进度的工具可以覆盖 `execute`，但仍然必须遵守流的不变量。

### 4.1 为什么还需要 `ToolDyn`

注册表必须把不同的工具放进同一个集合，但它们的 `Args` 和 `Output` 各不相同。`ToolDyn` 在运行边界把参数和结果转成 JSON 值，完成类型擦除：

```text
注册与调度层：dyn ToolDyn + JSON
                 │
                 ▼
具体工具内部：BashToolInput → BashToolOutput
              ReadFileInput → ReadFileOutput
```

类型擦除不表示系统内部失去类型。相反，它把无类型边界压缩在注册与协议层，具体实现仍能依靠 Rust 枚举表达分支。

### 4.2 `run` 与 `execute` 的区别

可以把二者理解为：

- `run`：一次性得到最终结果；
- `execute`：得到一个事件流，事件可以是进度，也可以是最终结果。

默认实现相当于：

```text
run(args).await
  → Terminal(Ok(output))
  或 Terminal(Err(error))
```

Bash、`read_file` 和 `grep` 会覆盖 `execute`，因为它们可以在结束前提供有意义的增量内容。

## 5. 参数从 JSON 到领域输入

工具参数要经过不止一层检查。

### 5.1 第一层：Schema 与反序列化

`schemars::JsonSchema` 生成模型看到的参数结构，Serde 把调用 JSON 变成 `Args`。部分字段使用“宽松反序列化器”：

- Bash 的 `timeout` 接受 JSON 数字，也接受数字字符串；
- Bash 的 `is_background` 接受宽松布尔值；
- `read_file.offset` 使用宽松整数解析；
- grep 的 `-i`、`multiline` 使用宽松布尔解析。

这是协议兼容策略：模型偶尔会把 `120000` 写成 `"120000"`，工具不必因此拒绝一个语义清楚的请求。

### 5.2 第二层：路径明确的参数错误

`types/params_validation.rs` 使用 `serde_path_to_error` 保存失败字段路径，并尝试分类：

- 缺少字段；
- 未知字段；
- 类型错误；
- 枚举 variant 不合法；
- 资源参数约束失败。

所以好的参数错误不是“deserialize failed”，而应告诉调用方哪个字段、收到什么值、期待什么类型。

### 5.3 第三层：跨字段约束

单个字段都合法，不代表组合合法。例如 `BashParams` 中：

```text
auto_background_on_timeout = true
enabled_background          = false
```

这两个布尔值分别都能反序列化，但组合自相矛盾，因此 `validate_params_value` 返回带字段路径的 `params_constraint` 错误。

### 5.4 Schema 不是完整运行规则

Bash Schema 可以宣传 timeout 的默认值和最大值，但后台任务有不同语义：省略或传 `0` 表示不由包装器限时。`read_file` Schema 允许 `limit`，运行时仍会把它压到最大读取行数。grep 的 `output_mode` 可以在 wire 上接受，却刻意不出现在 Schema 中。

阅读一个工具时，至少要同时查看：

1. 输入结构及 Serde 属性；
2. `versioned_definition` 或动态 Schema 修改；
3. `run` 中的业务校验；
4. 参数资源类型的 `validate_params_value`。

## 6. 工具流的终态不变量

工具流的合法形状是：

```text
[Progress, Progress, ..., Terminal]
```

约束是：

- 可以没有 `Progress`；
- `Terminal` 必须恰好一次；
- `Terminal` 必须是最后一项；
- 终态里包含成功输出或 `ToolError`。

这个不变量使上层不必猜“流断了是成功还是失败”。它也解释了 Bash 的实现为何在 `run` 返回前排空剩余通知：最后一段输出必须在 `Terminal` 之前发出。

### 6.1 Progress 与 Notification 不是同一条通道

Bash 同时使用两种事件机制：

- `ToolProgress` 是当前工具调用的流内事件；
- `ToolNotificationHandle` 是 session 级副通道，可用于 TUI、持久化或后台完成提醒。

当启用前台流式输出时，Bash 创建 `PerCallNotificationSink`，再通过 `tee` 把输出块同时送往 session 通知和当前调用的进度流。关闭流式展示时，它跳过每次调用的 channel 分配，但 session 通知仍存在。

这两个通道服务的生命周期不同：工具流在 `Terminal` 后结束，后台任务通知可以在启动调用早已返回后继续到达。

## 7. 输出为什么分成“干净结果”和“模型文本”

`types/output.rs` 中的 `ToolRunResult` 是理解结果边界的关键：

| 字段 | 用途 | 是否应被提醒文字污染 |
| --- | --- | --- |
| `output` | 结构化、可序列化的工具真实输出 | 否 |
| `prompt_text` | 下一轮提供给模型的可读投影 | 可以追加提醒 |
| `effective_tool_name` | 元工具转发后真正执行的工具名 | 不适用 |

假设 `read_file` 返回 `FileContent`。协议或 UI 可能需要绝对路径、offset、总行数和原始内容；模型通常只需要带行号的阅读文本以及“还有更多行”的提示。如果只有一个字符串，要么丢结构，要么把提示文字误当成文件内容。

### 7.1 `TypedToolOutput`

运行时的 `TypedToolOutput` 保存：

- 工具 ID；
- 序列化后的 JSON 值；
- 非空的 `model_output`；
- 可选的 `chat_completion_output`。

默认模型输出可以由结构化值序列化得到。图片、PDF 页面或其他多模态结果则可以覆盖 chat-completion 投影，而不必把 base64 当普通文本塞给模型。

### 7.2 为什么 `effective_tool_name` 有用

如果模型调用的是一个元工具，而元工具最终分派给 `grep`，审批、提醒、统计或展示可能需要知道实际能力。记录有效工具名能避免所有下游逻辑都把调用误认为外层包装器。

## 8. 三种不同的“输出太大”

源码没有采用一刀切的截断算法，因为不同内容的可恢复性不同。

### 8.1 grep：单行裁剪可以接受

`truncate_line` 按 Unicode 字符边界裁剪一条匹配行，并加入总字符数提示。对 grep 来说，某一条上下文行过长通常不是核心信息，而且用户可以缩小模式或直接读文件。

### 8.2 Bash 与后台输出：保留头尾和完整日志

Bash 总输出有字节预算。超出时，模型看到开头和结尾，中间插入截断标记；完整输出写在 session 的 terminal 日志中，结果携带 `output_file` 和 `total_bytes`。

长单行还会用 `soft_wrap_line` 插入换行。软换行不丢字符，只给模型增加视觉锚点。它与 `truncate_line` 的语义完全不同。

### 8.3 `read_file`：行窗口加 Token 闸门

`read_file` 默认最多读 1000 行，然后估算格式化内容的 Token 数。若超过 25,000 Token，不返回半截内容，而返回 `FileTooLarge`，提示改用更小的 `offset`/`limit` 或 grep。

这里故意没有通用的“每行截断”：JSON、压缩代码或生成文件可能只有一条超长行，裁掉中间部分会让后续无法通过行号恢复。对这种文件，结果会建议用命令行工具按字符或结构提取。

### 8.4 通用截断配置的优先级

`TruncationConfig` 对非 MCP 工具的概念优先级是：

```text
该工具专属配置
  > 全局默认配置
  > 内置默认值
```

MCP 工具在其中还可以插入 MCP 专属配置。Schema 与描述中的数字也会根据最终配置更新，避免模型看到的上限和执行上限不一致。

## 9. Bash：从输入到终端后端

`BashToolInput` 有四个值得注意的字段语义：

| 字段 | 含义 |
| --- | --- |
| `command` | 原样交给当前 shell 的命令文本 |
| `timeout` | 毫秒；前后台解析规则不同 |
| `description` | 说明命令目的，也进入后台任务元数据 |
| `is_background` | 是否立即返回后台任务句柄 |

工具 ID 是 `run_terminal_cmd`，能力标记为执行类。真正的进程管理委托给 `Terminal` 资源中的 `TerminalBackend`。

### 9.1 执行前读取哪些依赖

`BashTool::run` 从共享资源和调用上下文取得：

- 当前工作目录；
- `Terminal` 后端；
- session 文件夹；
- session 环境变量；
- 通知句柄；
- 可选的 owner session ID；
- `BashParams` 与截断配置；
- 当前 tool call ID。

日志路径按调用 ID 生成：

```text
<session-folder>/terminal/<tool-call-id>.log
```

因此结构化结果中的日志路径不是临时文案，而是恢复完整输出的重要句柄。

### 9.2 命令不是毫无检查地交给 shell

运行前会做一些针对真实故障模式的校验：

- 后台能力关闭时，拒绝 `is_background=true`；
- 根据 Bash、PowerShell Core、Windows PowerShell、cmd.exe 的 `&` 语义判断是否存在不允许的后台操作符；
- 拒绝明显会匹配并杀死包装 shell 自身的 `pkill -f`/`pgrep -f` 模式；
- 可按配置给命令添加统一前缀。

这里不是通用 shell parser，而是对产品已知危险模式的防护。审批和 sandbox 仍在外层完成，不能因为这里有校验就认为 Bash 自身是安全边界。

## 10. Bash 的超时语义

最重要的区别是“等待多久”和“进程最多活多久”并不总是同一个概念。

### 10.1 前台命令

前台规则可概括为：

```text
有效 timeout
  = 用户给出的正数，或 session 默认值
  = 再受前台最大 timeout 限制
```

内置默认等待为 120 秒，未另行配置时模型可请求的前台上限为 5 分钟；代码另有 10 小时的绝对安全上限，供显式配置的极端情况使用。

如果没有启用自动转后台，前台命令还会经过 `clamp_foreground_block`，避免单次 turn 被超长命令长期占住。

### 10.2 显式后台命令

后台规则不同：

- `timeout` 为正数：把它当作后台进程的 kill backstop，只受绝对安全上限约束；
- `timeout` 为 `0` 或省略：包装器不设时限；
- 生命周期由后台任务工具和 session 关闭逻辑管理。

所以“Schema 默认 120000”不能机械套到后台分支。源码特意让 Serde 的省略值保持 `None`，否则反序列化阶段就会丢失“省略”与“显式默认”的区别。

### 10.3 自动转后台

启用 `auto_background_on_timeout` 后，前台调用到达阻塞预算时可以转成后台任务，而不是杀死进程。终端请求同时携带：

- 真正的进程 timeout；
- 前台阻塞预算；
- 是否允许超时后转后台。

这可以表达：

```text
前台最多占住本轮 15 秒
但进程可以继续运行到 120 秒或更久
```

默认短阻塞预算由终端后端控制，并可由环境变量覆盖；配置为 `0` 表示不使用短预算，只在真正 timeout 到达时转后台。

### 10.4 超时后的进程树

工具描述明确告诉模型：

- Unix 上终止子进程组，先发 `SIGTERM`，约一秒后升级为 `SIGKILL`；
- Windows 上依靠 Job Object 终止后代进程；
- 通过 `setsid`、`nohup` 等脱离进程组的进程不一定跟随退出。

这也是为什么“工具返回 timeout”不应被解读为操作系统中绝对没有残留进程。

## 11. Bash 的前台流式输出

只有同时满足“前台调用”和 `WorkspaceViewerContext.stream_tool_progress=true` 时，Bash 才建立调用内进度流。

其核心算法是：

1. 创建 per-call notification channel；
2. `tee` 到 session 通知句柄和 per-call sink；
3. 并发等待命令 future 与输出块；
4. 一次性排空当前已排队的块，只保留最新累计快照；
5. 根据上次已发送的累计字节数生成 delta；
6. `run` 完成后再排空残留通知；
7. 最后发送唯一 `Terminal`。

“合并 queued chunks”是背压策略：UI 来不及消费时，不需要展示每个内部 tick，但不能漏掉累计输出的尾部。

后台调用不走这条流，因为启动调用应尽快返回 `BackgroundTaskStarted`；后续输出属于 session 级后台生命周期。

## 12. Bash 结果不是只有 exit code

前台 `BashOutput` 会保存或派生：

- stdout/stderr 合并后的可见输出；
- prompt 专用输出；
- exit code；
- signal 或合成的终止原因；
- cwd；
- 是否截断；
- 原始总字节数；
- 完整日志文件路径。

合成终止原因包括：

- `timeout`；
- `max_runtime`；
- `cancelled`；
- `killed`；
- POSIX `signal N`。

格式化层会把没有真实退出码的场景写成 `exit: killed (reason)`，而不是误导性的 `exit: -1`。

## 13. 后台任务：句柄代替阻塞

显式后台 Bash 调用会：

1. 为 Python 默认加入 `PYTHONUNBUFFERED=1`，让输出及时到达日志和管道；
2. 构造 `TerminalRunRequest`；
3. 调用 `TerminalBackend::run_background`；
4. 发送 backgrounded notification；
5. 返回 `BackgroundTaskStarted`。

句柄中包括：

- `task_id`；
- 类型 `bash`；
- `output_file`；
- `status=running`；
- 原命令；
- 可选 PID；
- 如何取回结果的动态提示。

检索提示不会硬编码工具别名，而是通过 `TemplateRenderer` 查找当前工具集里 `BackgroundTaskAction` 的实际名称和参数名。

### 13.1 `get_task_output` 是快照工具，也是有限等待工具

省略 `timeout_ms` 时，它做非阻塞快照；传入正的 timeout 时，它等待完成，但会受最大等待上限约束。30 秒默认值只用于已经进入等待模式、却没有给出时长的 legacy 或内部调用路径，并不是 `get_task_output` 省略参数时的行为。单次阻塞最大值默认可到 10 分钟，也可由环境配置调整。

查询顺序允许同一个入口同时处理：

- 终端后台任务；
- Subagent 任务。

如果任务仍在运行，结果会解释是没有请求等待、等待已到期，还是因为另一任务完成而提前返回。它还强调完成时会自动通知，避免模型忙轮询。

### 13.2 `kill_task` 的含义

`kill_task` 会尝试终止后台 Bash 或取消 Subagent。它是写作用域工具，因为它改变运行状态；“读取一个 PID”与“终止任务”不能共享只读能力标记。

找不到任务时，当前协议会尽量列出已知 Bash task ID；旧行为版本则保留历史错误文案。这个例子说明行为版本不仅影响成功格式，也影响可观测错误。

### 13.3 任务 ID 的作用域

后台任务属于 session 资源所持有的终端/协调器，而不是全局随处可查的操作系统任务。工具会携带 owner session ID，查询和通知也围绕 session 工作。

不要把 `task_id` 等同于 PID：

- PID 是操作系统进程标识；
- task ID 是 Grok Build 的任务句柄；
- Subagent 甚至可能没有对应的单一 OS PID。

## 14. `read_file`：先识别格式，再决定如何呈现

`ReadFileInput` 包含：

- `target_file`：相对 workspace 或绝对路径；
- `offset`：1-based 起始行，可为负数；
- `limit`：读取行数；
- `pages`：PDF 页范围；
- `format`：PDF 的 image 或 text 投影。

读取流程大致是：

```text
解析 cwd / display cwd
  → 解析模型路径并尝试 canonicalize
  → Unicode 文件名补救
  → gitignore 策略
  → FileSystem::read_file
  → 图片 / PDF / PPTX / 二进制 / 文本分派
  → 文本行窗口与 Token 闸门
  → FileContent 或结构化错误
```

### 14.1 `DisplayCwd` 与真实 `Cwd`

真实文件访问要使用可解析的文件系统路径，错误信息则可能需要显示用户熟悉的工作目录。源码因此分别保留 `Cwd` 与 `DisplayCwd`，并通过 `resolve_model_path` 处理模型提供的路径。

### 14.2 canonicalize 失败并不立即终止

不存在的路径无法 canonicalize，但工具仍需要生成准确的 NotFound，或尝试修复 Unicode 文件名差异。因此 canonicalize 失败时会保留拼接路径继续处理，而不是统一映射成内部错误。

### 14.3 gitignore 是产品策略

当前行为版本可以在启用 `RespectGitignore` 时拒绝读取被忽略文件；`legacy-0.4.10` 保留旧规则。编辑工具也有对应策略，但默认值并不完全相同，阅读时不要把“读”和“写”的 gitignore 默认行为混为一谈。

## 15. `read_file` 的格式分派

读取到 bytes 后按下列顺序处理：

1. 若元数据识别为图片，返回多模态 `ImageContent`；
2. 若文件头或扩展名识别为 PDF，按页渲染图片或提取文本；
3. `.pptx` 进入带大小和 60 秒处理上限的文档抽取；
4. 已知二进制扩展名或内容检查命中时拒绝普通文本读取；
5. 其余内容用 UTF-8 有损投影读取：非法序列由 `from_utf8_lossy` 替换；
6. 文本进入行号、范围和 Token 规则。

PDF 每次最多读取 20 页；超过 10 页的 PDF 需要调用方指定页范围。PPTX 当前提取 DrawingML 文本，不等价于完整渲染幻灯片视觉布局。

### 15.1 图片与文本结果为何是枚举

`ReadFileOutput` 不是字符串，而是：

```text
FileContent
FileNotFound
IsADirectory
PermissionDenied
FileTooLarge
FileReadError
ImageContent
ImageSizeError
PdfPageImages
```

上层可以按 variant 决定是生成文本工具消息、图片 content block，还是错误卡片，无需解析脆弱的字符串前缀。

## 16. 文本读取的行语义

### 16.1 行号是稀疏锚点

格式化文本在第一条可见行和文件每个第 10 行写入：

```text
1→第一行
第二行
...
10→第十行
```

行号前缀不是文件内容。编辑时复制 `old_string`，不能把 `10→` 一起复制进去。

### 16.2 offset 是 1-based

- 正数 `1` 表示第一行；
- `0` 也归一到第一行；
- 负数从尾部计算；
- 计算规则还保留历史 harness 对尾随换行与“phantom field”的兼容行为。

这是典型的兼容性细节：不能只用直觉重写为 `lines().skip(...)`，否则无尾随换行文件和负 offset 的边界测试会变化。

### 16.3 SKILL 文档例外

以下内容会完整读取，不应用普通行数和 Token 上限：

- 文件名恰好为 `SKILL.md`；
- 路径含精确 `skills` 组件的 Markdown 文件。

原因是 Skill 指令及其引用资料不能被静默截断。路径检查会词法折叠 `.` 和 `..`，但不会解析 symlink。

### 16.4 从文本中抽取 base64 图片

行格式化前会尝试识别嵌入的 base64 图片，把图片保存进 `FileContent.extracted_images`，同时用清理后的文本继续展示。这个字段需要跨过 `ToolDyn` 的 JSON 往返，之后由 session 层转成多模态 follow-up。

## 17. `read_file` 的流式输出

只有普通行式文本结果可流式发送，图片、PDF 和 PPTX 保持终态一次性返回。流式 delta 的目标大小约为 4 KiB，并在字符边界切分；拼接所有 delta 应逐字节还原最终卡片正文。

同样受 `WorkspaceViewerContext.stream_tool_progress` 控制。这个 gate 说明“工具声明支持 streaming”与“当前用户实际收到 streaming”是两个条件。

## 18. grep：受控地包装 ripgrep

`grep` 不是在 Rust 中重新实现正则引擎，而是启动 ripgrep。输入支持：

- regex `pattern`；
- 搜索 `path`；
- `glob` 或 ripgrep file type；
- `-B`、`-A`、`-C` 上下文；
- `-i` 忽略大小写；
- multiline；
- `head_limit`；
- wire 上的 `content`、`files_with_matches`、`count` 输出模式。

### 18.1 默认限制与硬上限

| 模式 | 省略 `head_limit` | 显式请求硬上限 |
| --- | ---: | ---: |
| content | 200 行 | 2000 行 |
| files/count | 500 项 | 10000 项 |

此外还有：

- ripgrep stdout 最多读取 5 MB；
- 非 WSL 默认墙钟超时 20 秒；
- WSL 默认 60 秒；
- 单行默认最多 1000 字符；
- 总模型输出默认受约 40 KB 的工具预算控制。

达到行数或字节预算后会尽早终止 `rg`，避免为了一个已经确定要截断的结果继续遍历巨大仓库。

### 18.2 为什么还有 100ms exact-fit probe

当缓冲区恰好填满预算时，系统不知道结果是“刚好结束”还是“后面还有内容”。代码最多等待约 100ms 再读一个字节：

- 立刻 EOF：可以报告精确结果；
- 又有字节：确认发生截断，可写“at least N”；
- 不能无限等，因为那会把已有结果拖到整个 grep timeout 后丢失。

### 18.3 grep 的流式内容不是原始 stdout

流式分支发送格式化后的匹配卡片正文，而不是裸 `rg` stdout。终态再添加 `<workspace_result ...>` 包装和汇总 footer。因此进度流是最终卡片正文的忠实前缀，但不包含终态专属 footer。

### 18.4 `rg` exit code 不都代表工具失败

ripgrep 通常用 `0` 表示找到匹配，`1` 表示没有匹配，其他值表示错误。工具输出保存 stdout、stderr、exit code、match count 和结构化 file matches，让格式化层区分“零结果”和“搜索进程失败”。

## 19. `list_dir`：结构化文件系统错误

目录读取虽然比 grep 简单，仍体现相同设计原则。`ListDirOutput` 用枚举区分：

- 正常内容；
- NotFound；
- 目标是文件；
- 目标不是目录；
- PermissionDenied；
- 未分类 Error。

正常结果还保留 `absolute_root_path`。模型看到的树状文本与系统保存的定位信息不是同一字段。

## 20. `search_replace`：单文件精确替换

输入由 `file_path`、`old_string`、`new_string`、`replace_all` 组成。当前工具的主语义是：

```text
old_string 为空
  → 新文件创建或整文件写入分支

old_string 非空
  → 读取文件
  → 精确查找所有位置
  → 0 个、1 个或多个匹配分支
  → 生成新文本
  → 写入并发 FileWritten 通知
```

### 20.1 唯一匹配是默认安全条件

默认 `replace_all=false`：

- 0 个匹配：返回 `NoMatchesFound`；
- 1 个匹配：执行替换；
- 多个匹配：返回 `MultipleMatchesFound`，要求增加上下文；
- `replace_all=true`：替换全部位置。

这使 `old_string` 同时承担轻量并发校验：如果用户或其他工具改过文件，旧片段可能不再唯一或根本不存在，工具会拒绝盲写。

但它不是强并发控制。当前 `grok_build/search_replace` 实现没有获取 `FileOperationLockManager`；从读取到写入之间仍存在 TOCTOU 窗口。

### 20.2 CRLF 处理

若原文件含 CRLF，匹配前会把内容统一成 LF，完成替换后再恢复 CRLF。这让模型可以使用通常的 `\n` 文本编辑 Windows 风格文件。

### 20.3 Unicode confusable fallback

默认只做精确匹配。可选开启 `unicode_normalized_fallback`：当精确匹配为零时，尝试统一智能引号、em dash 等易混 Unicode 字符；只有结果无歧义时才修改。

输出中的 `unicode_normalized=true` 会告诉下游这不是字节级原样匹配。精确匹配永远优先，避免 fallback 抢走本可明确定位的片段。

### 20.4 空 `old_string` 的兼容陷阱

空 `old_string` 用于创建文件。`empty_old_string_does_not_override=true` 时，它只能创建新文件或填充空文件，不能覆盖已有非空文件。

但该保护是 opt-in；默认值保留旧行为，可以把空 `old_string` 当整文件替换。因此工具描述会根据最终参数动态移除或保留保护句，不能只看源文件里的静态描述模板。

### 20.5 成功输出为什么保存编辑上下文

`SearchReplaceEditsApplied` 不只返回“成功”，还保存：

- 旧串与新串；
- 绝对路径；
- 每处修改的新旧行号；
- 前后三行上下文；
- 行内前缀；
- 可选 patch；
- 模型提示文本与 concise 投影。

这些信息可用于 diff 展示、行数统计、通知和回放，而无需写入后重新猜测发生了什么。

## 21. `apply_patch`：多文件补丁解释器

`apply_patch` 接受 Codex patch 文本，语法包含：

- Add File；
- Delete File；
- Update File；
- Move to；
- 一个文件中的若干 context hunk。

补丁路径必须是相对路径。执行分四个阶段：

```text
1. parse_patch
2. compute_all_changes：读取旧文件并在内存推导全部新内容
3. 逐个写入、删除或移动
4. 生成结构化文件结果与摘要
```

### 21.1 先计算再写的价值

若任意 hunk 找不到上下文，`compute_all_changes` 会在尚未写文件时失败。这样可以避免“前几个 hunk 已写，最后一个 hunk 才发现匹配失败”。

每个 `FileChange` 都保存执行所需的完整信息：

- Add：目标与新内容；
- Delete：目标与原内容；
- Update：目标、原内容和新内容；
- Move：源、目标、原内容和目标新内容。

### 21.2 它不是事务

第三阶段是普通顺序 I/O：

```text
for change in changes {
    write/delete...
}
```

没有临时文件整体提交、journal 或失败回滚。若第一个文件写成功、第二个文件因权限失败，第一项不会自动恢复。

Move 也分成“写目标，再删源”两步；删源失败时可能同时存在源和目标。文档或调用方不能把它宣传为文件系统原子 rename。

### 21.3 Add 是否拒绝覆盖已有文件

`compute_all_changes` 的 Add 分支只构造目标内容，应用阶段直接 `write_file`。从这里的实现看，不存在独立的“目标必须不存在”检查。因此安全性依赖外层权限、补丁生成纪律和所用 `AsyncFileSystem` 的具体行为，不能仅凭 `Add File` 名字推断排他创建。

### 21.4 成功通知

每次实际变更后发送 `FileWritten`：

- Add：`previous_content=None`；
- Update：带原内容；
- Delete：新内容为空，带原内容；
- Move：目标新增通知加源删除通知。

通知在每项 I/O 后立即发出，因此部分失败时，已经完成的修改仍有对应事件。

## 22. 两种编辑工具如何选择

| 场景 | `search_replace` | `apply_patch` |
| --- | --- | --- |
| 单处明确替换 | 最合适 | 可用但更重 |
| 全局重命名 | `replace_all` 可用 | 可显式列出多个 hunk |
| 新建单文件 | 空 `old_string` | Add File |
| 同时改多文件 | 需要多次调用 | 一次补丁描述 |
| 删除或移动文件 | 不适合 | 原生支持 |
| 匹配依据 | 完整旧字符串 | hunk 上下文和位置推导 |
| 写入前全补丁可计算检查 | 单文件自然完成 | 有，覆盖所有 hunks |
| 真正多文件事务 | 否 | 否 |

“一次调用修改多个文件”减少模型和工具之间的往返，但扩大单次部分写入的故障面。高价值修改后仍应运行 `git diff`、编译或测试验证。

## 23. 文件操作锁：存在，但不要误判覆盖范围

`implementations/editor_infra/file_operation_lock.rs` 定义了 `FileOperationLockManager`：

- 同一路径的锁串行；
- 不同路径锁可并行；
- exclusive lock 阻塞所有路径锁；
- FIFO 队列防止 exclusive waiter 被后来者长期饿死；
- RAII guard drop 后异步释放；
- 被取消的 waiter 不会留下 phantom lock。

然而当前源码搜索显示，这个管理器只在自己的模块和测试中出现，`grok_build/search_replace` 与 `codex/apply_patch` 没有调用它。因此正确结论是：仓库有这套编辑基础设施，但不能据此宣称这两个主工具已获得其并发保证。

这是阅读大型仓库的重要方法：发现一个设计良好的类型后，还要用引用搜索确认生产调用链是否真的接入。

## 24. “错误输出”与 `ToolError`

内置工具经常用两条不同通道表达失败。

### 24.1 领域内可恢复结果

例如：

- `ReadFileOutput::FileNotFound`；
- `SearchReplaceOutput::MultipleMatchesFound`；
- `ApplyPatchOutput::ParseError`；
- `TaskOutputOutput::TaskNotFound`。

这些是成功执行了工具逻辑后得到的领域结果。模型可以据此换路径、重读文件或修正 patch。

### 24.2 运行基础设施错误

例如缺少必需 `Resources`、终端后端启动失败、文件系统写入返回无法分类的错误，会成为 `ToolError`。

判断标准不是“用户想要的事情有没有成功”，而是：这个结果是否属于工具公开、可序列化、可恢复的业务分支。

详细错误分类与恢复策略见[错误分类、重试、降级与恢复状态机](16-error-taxonomy-retry-degradation-and-recovery-state-machines.md)。

## 25. 行为版本为什么深入工具内部

`BehaviorVersion` 会影响：

- `read_file` 是否执行当前 gitignore 策略；
- 不存在、目录等错误映射和历史文案；
- Bash 对后台 `&` 的兼容判断；
- `search_replace` 的失败消息；
- task query/kill 的 not-found 展示。

兼容层不是只在网络协议入口改字段，它会深入到模型可见的行为。重构工具时，不能为了“统一错误”删除看似重复的 legacy 分支，除非同时更新兼容承诺和 fixtures。

## 26. 能力标记不是装饰

工具通过 `ToolCapabilities` 声明：

- 是否只读；
- read/write scope；
- 是否支持 streaming 及 progress subkind。

典型例子：

| 工具 | `is_read_only` | scope |
| --- | --- | --- |
| `read_file` | true | Read |
| `grep` | true | Read |
| `apply_patch` | false | Write |
| `kill_task` | false | Write |

能力会被上层用于审批、客户端展示和流式协商。新增工具若只实现业务代码却漏掉能力，可能执行正确但被错误授权或错误展示。

## 27. 修改工具时的副作用清单

每个工具实现至少要检查以下副作用：

| 副作用 | 常见载体 | 需要核对什么 |
| --- | --- | --- |
| 文件变化 | `AsyncFileSystem` | 路径、旧内容、部分失败、gitignore |
| 进程变化 | `TerminalBackend` | timeout、进程组、后台所有权、取消 |
| 模型上下文 | `prompt_text` | 是否把提醒误写进干净结果 |
| 客户端事件 | `NotificationHandle` | 事件顺序、call ID、重复或遗漏 |
| 流式展示 | `ToolProgress` | delta 是否连续、Terminal 是否唯一 |
| session 磁盘 | terminal log | 限额、恢复路径、生命周期 |
| Telemetry | tracing spans | 是否记录必要状态且不泄漏敏感内容 |

只测试函数返回值通常覆盖不了这些边界。

## 28. 一个完整场景：运行大输出命令

假设模型调用：

```json
{
  "command": "cargo test -p some-crate -- --nocapture",
  "timeout": 120000,
  "description": "验证目标 crate 的修改",
  "is_background": false
}
```

链路是：

1. 动态层把 JSON 还原成 `BashToolInput`；
2. 工具运行管线完成权限与审批；
3. Bash 解析 cwd、参数、输出预算和通知句柄；
4. 校验后台操作符与已知自杀式命令模式；
5. 构造 `TerminalRunRequest`；
6. 本地终端 actor 启动 shell 和进程组；
7. 输出写入 `<session>/terminal/<call-id>.log`；
8. 若流式 gate 开启，累计输出块被转成 progress delta；
9. 若输出超预算，模型保留头尾并收到完整日志路径；
10. 进程退出、被超时终止或自动转后台；
11. 最后一个进度块先发出，随后唯一 Terminal；
12. `BashOutput` 进入干净结构化结果，格式化器生成模型可读 header。

此场景跨越工具、终端 actor、通知和输出投影，不能只在 `BashTool::run` 一个函数里找全部行为。

## 29. 一个完整场景：编辑陈旧文件

假设模型先读到：

```text
fn mode() {
    old_call();
}
```

之后用户把它改成两处 `old_call()`，模型再调用默认 `search_replace`。

1. 工具重新读取当前文件，而不是依赖旧 prompt 内容；
2. 精确搜索得到两个位置；
3. 因 `replace_all=false` 返回 `MultipleMatchesFound`；
4. 不写文件，也不发成功 `FileWritten`；
5. 错误提示使用当前客户端映射后的 `replace_all` 参数名；
6. 模型应重新读取或增加上下文，而不是重复相同调用。

这是一种乐观并发检测，但由于读写间无生产锁，若文件恰好在步骤 2 与写入之间变化，仍可能覆盖竞争修改。

## 30. 调试路线

### 30.1 工具没有出现在列表里

先查注册、requirements 和动态 definition，不要从 `run` 开始。参见[ToolBridge、工具注册表与 Resources](04-tool-bridge-registry-and-resources.md)。

### 30.2 参数看起来正确却被拒绝

按顺序查：

1. 客户端参数别名是否被还原；
2. Serde 字段名和 `rename`；
3. `serde_path_to_error` 报出的字段；
4. resource 参数约束；
5. `run` 内的组合校验；
6. behavior version 是否改变规则。

### 30.3 工具成功但模型看到奇怪文字

同时打印或测试：

- typed `output`；
- `prompt_text`；
- `chat_completion_output`；
- 客户端 notification。

如果只断言最终字符串，很容易把投影层 bug 误认为执行 bug。

### 30.4 Bash 看起来卡住

确认：

- 是否显式后台；
- 是否启用 auto-background；
- 前台 block budget 与 kill timeout 各是多少；
- 子进程是否脱离进程组；
- task snapshot 是否仍为 running；
- 完整日志是否继续增长。

### 30.5 patch 报错后文件却改了一部分

区分错误阶段：

- Parse/ApplicationError：通常发生在写入前；
- 第三阶段 `ToolError`：可能已有前序文件写入；
- Move 的删除失败：目标可能已写、源仍存在。

## 31. 修改清单

### 31.1 新增只读工具

- 定义 typed Args/Output；
- 生成准确 Schema；
- 标 `is_read_only=true` 和 Read scope；
- 使用 `FileSystem`/后端抽象而非散落直接 I/O；
- 定义 NotFound、PermissionDenied 等领域结果；
- 明确输出预算和恢复方式；
- 如支持 progress，验证 `[Progress*, Terminal]`。

### 31.2 新增写工具

- 标记 Write scope；
- 明确路径解析和 gitignore 策略；
- 记录修改前后内容或可恢复差异；
- 定义部分失败边界；
- 发出关联 call ID 的通知；
- 验证审批与 sandbox；
- 不要未经证据承诺“原子”或“事务”。

### 31.3 修改 Bash

- 分别测试 foreground/background；
- 分别测试 timeout 省略、0、正数和超大值；
- 测试 auto-background on/off；
- 覆盖 Unix 与 Windows 的 `&` 语义；
- 验证截断后日志路径可读；
- 验证 progress 拼接与最终总字节数；
- 验证取消后进程树和任务状态。

### 31.4 修改读取或搜索

- 测试空文件、尾随换行、负 offset；
- 测试超长单行和多字节 UTF-8；
- 测试图片/PDF/PPTX/二进制；
- 测试 gitignore 与 legacy contract；
- 测试无匹配、精确到达上限和真正溢出；
- 验证流式正文能还原终态正文前缀。

## 32. 推荐源码阅读顺序

第一次阅读建议按这个顺序：

1. `xai-tool-runtime/src/tool.rs`：先理解统一执行契约；
2. `xai-grok-tools/src/types/output.rs`：理解结构化结果；
3. `xai-grok-tools/src/util/truncate.rs`：认识不同输出策略；
4. `grok_build/read_file/mod.rs`：从较清楚的只读工具入门；
5. `grok_build/grep/mod.rs`：观察外部进程和预算；
6. `grok_build/search_replace/mod.rs`：观察领域错误与编辑上下文；
7. `codex/apply_patch/tool.rs`：观察多阶段、多文件修改；
8. `grok_build/bash/mod.rs`：最后处理超时、后台和流式的复杂组合；
9. `computer/local/terminal.rs`：追进程真正由谁拥有；
10. `task_output` 与 `kill_task`：补齐后台生命周期。

不要从三千多行的 Bash 文件开始，否则会先陷入 shell 兼容细节，反而看不到统一工具模型。

## 33. 建议实验

### 实验一：证明输出有多个投影

找一个 `SearchReplaceOutput::EditsApplied`，分别观察序列化 JSON 和模型提示文本，列出两边独有字段。

### 实验二：验证 `read_file` 行边界

构造四个文件：空文件、有尾随换行、无尾随换行、单行超长 JSON。分别测试 offset `1`、`0`、`-1` 与 limit。

### 实验三：验证 grep exact-fit

创建结果条目数恰好等于 `head_limit` 和多一条的两个目录，观察汇总是否区分精确数量和“at least”。

### 实验四：观察后台句柄

启动一个短后台命令，先做非阻塞 snapshot，再带 timeout 等待。记录 task ID、PID、日志文件和通知的不同生命周期。

### 实验五：制造 patch 部分写入

只在临时目录中操作：让第一个变更可写、后续目标不可写，验证第三阶段失败不会回滚第一项。这个实验能直接纠正“compute all 等于 transaction”的误解。

## 34. 自测题

1. `ToolDyn` 为什么不等于“所有工具内部都无类型”？
2. `execute` 默认如何利用 `run`？
3. 工具流为什么要求唯一且最后的 Terminal？
4. Progress 与 session notification 的生命周期有什么差异？
5. `ToolRunResult.output` 和 `prompt_text` 为什么必须分开？
6. Bash 后台省略 timeout 为什么不能套用 Schema 的 120 秒默认值？
7. auto-background 中“前台等待预算”和“进程 kill timeout”分别控制什么？
8. 为什么 Bash 更适合保留头尾，而 grep 可以裁剪单条上下文行？
9. `read_file` 为什么不对超长单行做不可恢复的统一裁剪？
10. `SKILL.md` 为什么绕过普通读取上限？
11. grep 为什么要做 exact-fit probe？
12. `search_replace` 的唯一匹配能防住什么，不能防住什么？
13. Unicode normalized fallback 为什么必须让精确匹配优先？
14. `apply_patch` 的第二阶段保证了什么？第三阶段又没有保证什么？
15. 为什么 Move 不能被描述为原子 rename？
16. 仓库中存在 FileOperationLockManager，为什么仍不能说主编辑工具已经加锁？
17. FileNotFound 什么时候适合做 output variant，什么时候会成为 ToolError？
18. task ID 与 PID 有什么区别？
19. 为什么能力标记会影响安全，而不只是 UI 图标？
20. 修改工具后，为什么只断言最终字符串不够？

## 35. 本篇术语表

| 名词 | 白话解释 | 在本篇中的具体含义 |
| --- | --- | --- |
| built-in tool / 内置工具 | 仓库自身提供的工具 | 与运行时动态接入的 MCP 工具相对 |
| `Tool` | 带具体输入、输出类型的执行接口 | 工具实现的编译期契约 |
| `ToolDyn` | 抹去具体类型后的统一接口 | 让注册表能存放不同工具，并在 JSON 边界分派 |
| associated type / 关联类型 | trait 为实现者预留的类型槽位 | `Args` 和 `Output` 由每个工具分别指定 |
| type erasure / 类型擦除 | 在统一边界隐藏具体泛型类型 | 不是删除类型安全，而是把动态性限制在协议边界 |
| JSON Schema | 描述 JSON 参数形状的机器可读规则 | 提供给模型的工具定义，但不包含所有运行语义 |
| lenient deserialize / 宽松反序列化 | 接受几种等价 JSON 表达 | 如数字 `120000` 与字符串 `"120000"` |
| cross-field validation / 跨字段校验 | 检查多个字段组合是否自洽 | 如自动后台依赖后台能力已启用 |
| progress | 工具未结束时发送的增量 | Bash 输出块、读文件片段或 grep 匹配片段 |
| terminal item / 终态项 | 工具流最后的成功或失败 | 每次执行必须恰好一个，和终端 shell 不是同一个“terminal” |
| notification / 通知 | session 级事件 | 可在工具调用结束后继续报告后台完成 |
| side channel / 副通道 | 主返回值之外的事件路径 | 本篇主要指 `ToolNotificationHandle` |
| tee | 把同一事件复制到多个消费者 | Bash 同时发 session 通知和 per-call progress |
| coalescing / 合并 | 把多个密集更新压成较少更新 | 防止输出 tick 淹没消费者 |
| projection / 投影 | 从同一结构生成某种用途的表示 | 结构化结果、模型文本、chat completion 内容 |
| clean output / 干净输出 | 不混入提醒文字的真实工具结果 | 用于序列化、协议、diff 与回放 |
| `prompt_text` | 给模型看的结果文本 | 可以加入下一步提示或 system reminder |
| chat completion output | 发给模型 API 的富内容表示 | 可包含图片等 content block |
| truncation / 截断 | 在预算内只保留部分输出 | 算法因 Bash、grep、read_file 而异 |
| soft wrap / 软换行 | 插入换行但不删除字符 | 用于让 Bash 超长单行更易阅读 |
| head/tail truncation / 头尾截断 | 保留开头和结尾，删除中间 | Bash 大输出仍能看到启动信息和最终错误 |
| recovery pointer / 恢复指针 | 指向完整内容的位置 | 例如 Bash 的 `output_file` |
| wall-clock timeout / 墙钟超时 | 按现实经过时间限制操作 | grep 的 20/60 秒执行上限 |
| kill backstop | 最迟终止进程的保险上限 | 后台正 timeout 不等于前台等待预算 |
| foreground block budget | 一次 turn 愿意同步等待多久 | 到期后可自动转后台，不一定杀进程 |
| process group / 进程组 | OS 中一起接收信号的一组进程 | Unix timeout 尝试终止命令及未脱离的后代 |
| Job Object | Windows 的进程集合管理机制 | 用于终止命令的后代进程 |
| task handle / 任务句柄 | 管理后台工作的逻辑标识 | `task_id`，不是 PID |
| snapshot / 快照 | 某一时刻的任务状态与输出 | 不保证任务已完成 |
| busy polling / 忙轮询 | 高频重复查询等待变化 | 后台通知机制试图避免这种行为 |
| canonicalize | 解析并规范化真实文件路径 | 失败后工具仍可能继续做 NotFound 或 Unicode 补救 |
| `DisplayCwd` | 面向用户展示的工作目录 | 可与实际文件系统 `Cwd` 不同 |
| lossily decoded UTF-8 | 非法字节用替代字符解码 | `String::from_utf8_lossy` 的文本读取行为 |
| sparse line anchor / 稀疏行锚点 | 不是每行都打印的行号 | 第一行与每个第 10 行使用 `N→` |
| phantom field | split 兼容计算中的虚拟尾字段 | 影响负 offset 与无尾随换行的历史边界 |
| multimodal / 多模态 | 文本之外还包含图片等内容 | 图片读取与 PDF 页面投影 |
| ripgrep | 高性能文本搜索程序 `rg` | `grep` 工具实际启动的外部搜索后端 |
| exact-fit probe | 达到预算后再探测少量数据 | 区分“恰好结束”和“还有结果” |
| structured error / 结构化错误 | 用枚举 variant 表达的失败 | 可恢复业务结果，而非基础设施 `ToolError` |
| exact match / 精确匹配 | 字节/字符串内容完全一致 | `search_replace` 的首选定位方式 |
| Unicode confusable | 看起来相似但码点不同的字符 | 智能引号、普通引号、不同 dash 等 |
| CRLF | Windows 常见的 `\r\n` 换行 | 替换时临时统一 LF，写回时恢复 |
| optimistic concurrency / 乐观并发 | 用旧值仍匹配来判断可安全更新 | 能发现一部分陈旧读取，不能关闭 TOCTOU 窗口 |
| TOCTOU | 检查到使用之间状态发生变化 | search_replace 读完到写入前文件可能再被修改 |
| patch hunk | 补丁中的一段上下文与增删行 | `apply_patch` 用它推导新文件内容 |
| in-memory compute | 写盘前先在内存算出结果 | 保证所有 hunk 可计算，不保证写阶段事务性 |
| atomic / 原子 | 外部只能看到修改前或修改后 | 当前多文件 `apply_patch` 不提供此保证 |
| transaction / 事务 | 全部成功或全部回滚 | 当前 `apply_patch` 没有 rollback |
| journal | 为恢复记录操作步骤的日志 | 当前 apply 阶段没有事务 journal |
| RAII guard | 对象销毁时自动释放资源 | 文件操作锁 guard drop 后触发释放 |
| writer starvation / 写者饥饿 | 写锁长期被新读者插队 | 锁队列用 exclusive waiter 优先规则避免 |
| phantom lock | 等待者取消后遗留的假锁 | 锁管理器发送失败时撤销授权 |
| behavior version | 选择历史兼容行为的版本值 | 会改变 gitignore、错误文案和若干工具规则 |
| capability | 工具声明的安全与传输属性 | 只读/写 scope、streaming spec 等 |

更多通用名词见[全局术语表](../appendices/glossary.md)。

## 36. 源码证据索引

| 结论 | 源码入口 |
| --- | --- |
| typed 工具、动态擦除和流终态 | `crates/common/xai-tool-runtime/src/tool.rs` 中 `Tool`、`ToolDyn`、`ToolStreamItem`、`TypedToolOutput` |
| 参数错误路径与资源校验 | `crates/codegen/xai-grok-tools/src/types/params_validation.rs` |
| 干净输出与模型文本分离 | `crates/codegen/xai-grok-tools/src/types/output.rs` 中 `ToolRunResult` |
| 全局与逐工具截断配置 | `crates/codegen/xai-grok-tools/src/types/context.rs` 中 `TruncationConfig` |
| UTF-8 安全裁剪、软换行、头尾保留 | `crates/codegen/xai-grok-tools/src/util/truncate.rs` |
| Bash 输入、timeout、流式与前后台分支 | `crates/codegen/xai-grok-tools/src/implementations/grok_build/bash/mod.rs` 中 `BashToolInput`、`resolve_effective_timeout`、`execute`、`run` |
| 终端任务与日志保留 | `crates/codegen/xai-grok-tools/src/computer/local/terminal.rs` |
| 后台快照与等待 | `crates/codegen/xai-grok-tools/src/implementations/grok_build/task_output/mod.rs` |
| 后台取消 | `crates/codegen/xai-grok-tools/src/implementations/grok_build/kill_task/mod.rs` |
| 文本、图片、PDF、PPTX 读取 | `crates/codegen/xai-grok-tools/src/implementations/grok_build/read_file/mod.rs` |
| ripgrep、结果预算与 streaming | `crates/codegen/xai-grok-tools/src/implementations/grok_build/grep/mod.rs` |
| 单文件精确替换 | `crates/codegen/xai-grok-tools/src/implementations/grok_build/search_replace/mod.rs` |
| patch 解析、预计算与顺序写入 | `crates/codegen/xai-grok-tools/src/implementations/codex/apply_patch/tool.rs` |
| 尚未接入主工具的文件锁实现 | `crates/codegen/xai-grok-tools/src/implementations/editor_infra/file_operation_lock.rs` |

## 37. 最小验证命令

```sh
# 查看统一工具契约
rg "trait Tool|trait ToolDyn|ToolStreamItem|TypedToolOutput" \
  crates/common/xai-tool-runtime/src/tool.rs

# 查看 Bash 前后台与超时分支
rg "resolve_effective_timeout|run_background|auto_background_on_timeout" \
  crates/codegen/xai-grok-tools/src/implementations/grok_build/bash/mod.rs

# 查看读取上限与格式分派
rg "MAX_NUM_TOKENS|is_skill_markdown|handle_pdf|handle_pptx|is_binary" \
  crates/codegen/xai-grok-tools/src/implementations/grok_build/read_file/mod.rs

# 查看 grep 的默认、硬上限和超时
rg "CONTENT_LINE_DEFAULT|CONTENT_LINE_LIMIT|MAX_STDOUT_BYTES|grep_timeout" \
  crates/codegen/xai-grok-tools/src/implementations/grok_build/grep/mod.rs

# 查看两个编辑工具的关键阶段
rg "handle_replacement|compute_all_changes|Phase 3|FileWritten" \
  crates/codegen/xai-grok-tools/src/implementations/grok_build/search_replace/mod.rs \
  crates/codegen/xai-grok-tools/src/implementations/codex/apply_patch/tool.rs

# 用引用搜索验证文件锁是否接入生产工具
rg "FileOperationLockManager|wait_for_exclusive_lock|wait_for_lock" \
  crates/codegen/xai-grok-tools/src
```

如果要运行测试，先查看目标 crate 的真实包名，再采用目标测试过滤，不建议把整个 workspace 当作第一步：

```sh
cargo test -p xai-grok-tools read_file
cargo test -p xai-grok-tools grep
cargo test -p xai-grok-tools search_replace
cargo test -p xai-grok-tools apply_patch
```

包名或测试组织若随版本变化，以对应 `Cargo.toml` 和 `cargo test -p <package> -- --list` 为准。
