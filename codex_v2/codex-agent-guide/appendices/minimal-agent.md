# 附录 C：从零实现一个最小 Agent

## 1. 目标

下面不是复制 Codex，而是把它压缩成一个教学版 Runtime。最小版本具备：

-结构化 History；
-Function Tool；
-模型—工具循环；
-Tool 参数校验；
-取消和步数预算；
-Skill 按需加载；
-简单权限中间件；
-持久化事件接口。

## 2. 最小协议类型

```rust
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Item {
    Message {
        role: Role,
        text: String,
    },
    ToolCall {
        id: String,
        name: String,
        arguments: Value,
    },
    ToolResult {
        call_id: String,
        ok: bool,
        output: String,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Role {
    Developer,
    User,
    Assistant,
}

#[derive(Clone, Debug, Serialize)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
}

pub struct ModelRequest {
    pub instructions: String,
    pub input: Vec<Item>,
    pub tools: Vec<ToolSpec>,
}

pub struct ModelResponse {
    pub items: Vec<Item>,
}
```

重点是 Tool Call 和 Tool Result 保持结构化，并通过 `id/call_id` 关联。

## 3. Tool 接口

```rust
use std::future::Future;

#[derive(Clone)]
pub struct ToolContext {
    pub cwd: std::path::PathBuf,
    pub cancellation: tokio_util::sync::CancellationToken,
}

#[derive(Debug)]
pub struct ToolError {
    pub message: String,
    pub retryable_by_model: bool,
}

pub trait Tool: Send + Sync {
    fn spec(&self) -> ToolSpec;

    fn execute(
        &self,
        context: ToolContext,
        arguments: Value,
    ) -> impl Future<Output = Result<String, ToolError>> + Send;
}
```

实际 Rust 若需要把不同 Tool 放进同一个 `HashMap<String, Arc<dyn Tool>>`，应使用对象安全的返回类型，例如 `BoxFuture`，或像 Codex 一样建立统一 executor trait。这里先突出语义。

对象安全版本：

```rust
use futures::future::BoxFuture;

pub trait DynTool: Send + Sync {
    fn spec(&self) -> ToolSpec;

    fn execute(
        &self,
        context: ToolContext,
        arguments: Value,
    ) -> BoxFuture<'static, Result<String, ToolError>>;
}
```

## 4. Registry

```rust
use std::collections::BTreeMap;
use std::sync::Arc;

pub struct ToolRegistry {
    tools: BTreeMap<String, Arc<dyn DynTool>>,
}

impl ToolRegistry {
    pub fn register(&mut self, tool: Arc<dyn DynTool>) -> anyhow::Result<()> {
        let name = tool.spec().name;
        if self.tools.insert(name.clone(), tool).is_some() {
            anyhow::bail!("duplicate tool: {name}");
        }
        Ok(())
    }

    pub fn specs(&self) -> Vec<ToolSpec> {
        self.tools.values().map(|tool| tool.spec()).collect()
    }

    pub fn get(&self, name: &str) -> Option<Arc<dyn DynTool>> {
        self.tools.get(name).cloned()
    }
}
```

使用有序映射能让 Tool schema 顺序稳定。

## 5. Model Client 接口

```rust
pub trait ModelClient: Send + Sync {
    fn respond(
        &self,
        request: ModelRequest,
        cancellation: tokio_util::sync::CancellationToken,
    ) -> futures::future::BoxFuture<'static, anyhow::Result<ModelResponse>>;
}
```

第一版可以等完整响应；第二版再把结果改成 `Stream<Item = ModelEvent>`。不要一开始把 SSE parsing、UI delta 和 Agent Loop 写在同一个函数里。

## 6. 最小 Agent Loop

```rust
pub struct Agent {
    model: std::sync::Arc<dyn ModelClient>,
    tools: ToolRegistry,
    history: Vec<Item>,
    base_instructions: String,
    cwd: std::path::PathBuf,
    max_steps: usize,
}

impl Agent {
    pub async fn run(
        &mut self,
        user_text: String,
        cancellation: tokio_util::sync::CancellationToken,
    ) -> anyhow::Result<String> {
        self.history.push(Item::Message {
            role: Role::User,
            text: user_text,
        });

        for _step in 0..self.max_steps {
            let request = ModelRequest {
                instructions: self.base_instructions.clone(),
                input: self.history.clone(),
                tools: self.tools.specs(),
            };

            let response = self
                .model
                .respond(request, cancellation.child_token())
                .await?;

            let mut needs_follow_up = false;
            let mut final_text = None;

            for item in response.items {
                match item {
                    Item::ToolCall {
                        id,
                        name,
                        arguments,
                    } => {
                        self.history.push(Item::ToolCall {
                            id: id.clone(),
                            name: name.clone(),
                            arguments: arguments.clone(),
                        });

                        let result = self
                            .execute_tool(&name, arguments, cancellation.child_token())
                            .await;

                        self.history.push(Item::ToolResult {
                            call_id: id,
                            ok: result.is_ok(),
                            output: match result {
                                Ok(output) => bound_output(output, 20_000),
                                Err(error) => error.message,
                            },
                        });
                        needs_follow_up = true;
                    }
                    Item::Message {
                        role: Role::Assistant,
                        text,
                    } => {
                        final_text = Some(text.clone());
                        self.history.push(Item::Message {
                            role: Role::Assistant,
                            text,
                        });
                    }
                    other => self.history.push(other),
                }
            }

            if !needs_follow_up {
                return final_text.ok_or_else(|| {
                    anyhow::anyhow!("model stopped without an assistant message")
                });
            }
        }

        anyhow::bail!("agent exceeded the maximum number of steps")
    }
}
```

这里已经出现了成熟 Agent 的核心闭环：

```text
History → Model → Tool Call → Tool Result → History → Model
```

## 7. 工具路由和错误回传

```rust
impl Agent {
    async fn execute_tool(
        &self,
        name: &str,
        arguments: Value,
        cancellation: tokio_util::sync::CancellationToken,
    ) -> Result<String, ToolError> {
        let Some(tool) = self.tools.get(name) else {
            return Err(ToolError {
                message: format!("unknown tool: {name}"),
                retryable_by_model: true,
            });
        };

        let context = ToolContext {
            cwd: self.cwd.clone(),
            cancellation,
        };
        tool.execute(context, arguments).await
    }
}
```

未知工具不 panic，而是生成模型可见错误。模型可以改用已知工具。

## 8. 输出边界

```rust
fn bound_output(mut output: String, max_bytes: usize) -> String {
    if output.len() <= max_bytes {
        return output;
    }

    let mut cut = max_bytes;
    while !output.is_char_boundary(cut) {
        cut -= 1;
    }
    output.truncate(cut);
    output.push_str("\n[output truncated]");
    output
}
```

真实实现最好保留头尾、原始大小和继续读取句柄。这里至少保证 UTF-8 边界和硬上限。

## 9. 添加权限中间件

不要在 Shell Tool 内硬编码所有权限。增加策略接口：

```rust
pub enum Decision {
    Allow,
    Deny(String),
    RequireApproval { reason: String },
}

pub trait ToolPolicy: Send + Sync {
    fn check(&self, tool: &str, arguments: &Value) -> Decision;
}
```

执行流程变成：

```text
Registry resolve
  → JSON/schema validation
  → ToolPolicy.check
  → approval callback
  → Tool.execute in sandbox
  → output bounding
```

Prompt 可以描述权限，但 `ToolPolicy` 才是真正 enforcement。

## 10. 添加 Skill

最小 Skill metadata：

```rust
pub struct SkillMetadata {
    pub name: String,
    pub description: String,
    pub path: std::path::PathBuf,
}
```

启动时只把目录加入 developer context：

```text
Available skills:
- rust-test: Diagnose and run Rust tests. (file: .../SKILL.md)
```

收到明确 `$rust-test` mention 后：

1.在 enabled metadata 中唯一定位；
2.检查路径属于允许 Skill root；
3.读取正文并限制大小；
4.追加 contextual user message；
5.再开始第一次模型请求。

不要让模型返回任意文件路径并以高权限读取为 Skill。

## 11. 添加 Step Snapshot

当工具会动态变化时，不要让 Loop 直接访问全局 Registry。每次采样前构建：

```rust
pub struct StepSnapshot {
    pub tool_router: std::sync::Arc<ToolRegistry>,
    pub cwd: std::path::PathBuf,
    pub permissions_version: u64,
}
```

生成 request.tools 和执行本次响应 Tool Call 时，使用同一 snapshot。

## 12. 添加持久化

定义 append-only event：

```rust
pub enum AgentEvent {
    UserMessage(Item),
    ModelItem(Item),
    ToolStarted { call_id: String, name: String },
    ToolFinished(Item),
    TurnCompleted,
}
```

关键提交顺序：

```text
收到 Tool Call
  → persist ToolStarted
  → execute
  → persist ToolFinished
  → append ToolResult to live History
```

生产实现需要更明确地处理“持久化成功但内存更新失败”以及相反情况，可以通过单线程状态机或事务 store 保证顺序。

## 13. 添加流式输出

将 Model Client 改为事件流：

```text
Created
OutputItemAdded
TextDelta
OutputItemDone
Completed
```

规则：

-Delta 只发送 UI，不直接作为多个 History message；
-完整 item done 后进入 History；
-没有 Completed 就按错误处理；
-consumer drop 取消后台 reader；
-Tool Call 只有参数完整后才执行。

## 14. 添加 Compaction

当估算 token 超过阈值：

1.选择旧 History 范围；
2.让模型生成结构化 summary；
3.保留最近用户意图和未完成 Tool 状态；
4.替换旧 History；
5.写 Compaction checkpoint；
6.重新注入当前环境和权限 baseline。

第一版不要自动删除最旧消息，这很容易删掉仍未完成的用户要求。

## 15. 演进路线

建议按这个顺序扩展：

```text
1. 单模型 + 单工具 + 有限循环
2. 结构化错误和取消
3. 多工具 Registry 与 schema 校验
4. 权限中间件和 sandbox
5. 流式事件
6. Skill catalog 与按需加载
7. Step snapshot 与动态工具
8. append-only persistence 和 resume
9. compaction 与 Prompt cache
10. MCP/Plugin
11. multi-agent
```

不要先做多 Agent。单 Agent 的 History、工具副作用、取消和恢复没有可靠之前，多 Agent 只会放大不一致。

## 16. 与 Codex 的对应关系

| 教学实现 | Codex 对应实现 |
|---|---|
| `Agent::run()` | `session/turn.rs::run_turn()` |
| `ModelRequest` | `client_common::Prompt` + `ResponsesApiRequest` |
| `Vec<Item>` | `ContextManager` + `Vec<ResponseItem>` |
| `ToolRegistry` | `core::tools::registry::ToolRegistry` |
| `execute_tool()` | `ToolRouter` + `CoreToolRuntime` |
| `ToolPolicy` | Hook + Approval + Sandbox |
| Skill metadata | `SkillMetadata` / `SkillCatalogEntry` |
| StepSnapshot | `StepContext` |
| AgentEvent | Rollout items + protocol events |

读完这个最小实现后，再回看 Codex，会发现大部分复杂度都来自跨平台、安全、动态能力、长线程恢复和产品兼容，而核心闭环仍然相同。

