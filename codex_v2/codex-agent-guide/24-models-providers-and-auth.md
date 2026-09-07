# 24. 模型、Provider 与认证

## 1. 三个容易混淆的概念

```text
ModelInfo          模型能力元数据：上下文、工具、Prompt、可见性等
ModelsManager      模型目录的发现、缓存、合并、筛选与默认选择
ModelProviderInfo  请求发到哪里、用什么协议、认证和重试怎样配置
```

“模型名”不能独自决定 Agent 行为。相同 slug 在不同 Provider 下可能有不同端点和认证方式；相同 Provider 中不同模型又可能支持不同工具与上下文窗口。

## 2. ModelsManager 的职责

核心 trait 位于 `codex-rs/models-manager/src/manager.rs`：

```rust
pub trait ModelsManager {
    fn list_models(...);
    fn raw_model_catalog(...);
    fn get_remote_models(...);
    fn get_default_model(...);
    fn get_model_info(...);
    fn refresh_if_new_etag(...);
}
```

实现分为：

- `OpenAiModelsManager`：Bundled catalog + 本地缓存 + 远端 `/models`；
- `StaticModelsManager`：进程内目录是权威来源，适合静态 Provider 或测试。

模型列表在返回给 picker 前会按 `priority` 排序、按认证能力筛选，并标记默认模型。

## 3. 三种刷新策略

`RefreshStrategy` 明确区分：

- `Online`：总是访问远端；
- `Offline`：绝不访问远端，只尝试缓存；
- `OnlineIfUncached`：新鲜缓存优先，否则联网。

不要用一个模糊的 `refresh: bool` 代替这种枚举。调用点可以直接表达离线启动、强制刷新或普通启动的意图。

## 4. 模型目录的来源与合并

`OpenAiModelsManager` 初始化时先读取随程序发布的 `models.json`，再视策略加载：

```text
bundled models.json
       +
fresh models_cache.json
       +
remote /models response
       -> active catalog
```

当 ChatGPT 认证用户收到至少一个可列出的远端模型时，远端列表可以成为权威列表；其他场景则按 slug 把远端元数据覆盖或追加到 bundled 列表。

这避免了两个极端：完全依赖网络会导致离线不可用；永远只用静态目录又无法及时获得服务端能力变化。

## 5. 缓存语义

`ModelsCacheManager` 将以下内容保存在 `~/.codex/models_cache.json`：

```rust
struct ModelsCache {
    fetched_at: DateTime<Utc>,
    etag: Option<String>,
    client_version: Option<String>,
    models: Vec<ModelInfo>,
}
```

默认 TTL 是 300 秒。缓存只有在客户端版本匹配且未过期时才可使用。ETag 未变化时，代码可只续期缓存 TTL；变化时执行在线刷新。

注意源码中的 TODO：当前缓存资格还没有包含 Provider identity。因此设计自己的系统时，应把 Provider ID、租户或 API base URL 纳入 cache key，避免切换 Provider 后复用错误目录。

## 6. 默认模型与显式模型

一般规则是：

1. 用户显式指定模型时优先保留；
2. 未指定时，从可用 preset 中找 `is_default`；
3. 没有默认值时用第一个；
4. 目录为空才退回空值或上层兜底。

`StaticModelsManager` 还支持 `allow_provider_model_fallback`：请求模型不可用时，Provider 可以选择自己的默认模型。

## 7. ModelInfo 如何改变 Agent

`ModelInfo` 不只是 UI 描述。`codex-rs/models-manager/src/model_info.rs` 中的 fallback 元数据展示了关键字段：

- `base_instructions`；
- `model_messages` 与 personality 模板；
- `supports_parallel_tool_calls`；
- shell、apply_patch、web search 工具类型；
- `context_window` / `max_context_window`；
- `auto_compact_token_limit`；
- 工具输出截断策略；
- Responses Lite、reasoning summary、verbosity 等能力；
- 实验工具和输入模态。

因此模型解析发生在 Prompt 和 Tool 构造之前：能力元数据会决定“给模型看什么”和“如何解释模型输出”。

未知 slug 会通过 `model_info_from_slug` 构造保守 fallback，并标记 `used_fallback_model_metadata = true`。这比直接崩溃更可用，但日志和 UI 应让用户知道能力判断可能不准确。

## 8. 配置覆盖的边界

`with_config_overrides` 支持覆盖上下文窗口、自动压缩阈值、工具输出上限、基础指令和 personality。

上下文窗口覆盖会被 `max_context_window` clamp：

```rust
context_window.min(max_context_window)
```

工具输出 Token 上限会根据模型的截断模式转换为 Token 或近似字节数。基础指令被整体覆盖时，代码会清空依赖旧模板的 instruction messages，避免模板与新指令互相矛盾。

## 9. ModelProviderInfo：传输和认证配置

`codex-rs/model-provider-info/src/lib.rs::ModelProviderInfo` 包含：

- `base_url`；
- API key 环境变量 `env_key`；
- command-backed bearer token `auth`；
- AWS SigV4 `aws`；
- `wire_api`；
- query 参数与静态/环境变量 Header；
- HTTP 与 Stream 重试参数；
- Stream idle、WebSocket connect timeout；
- 是否要求 OpenAI 登录；
- 是否支持 WebSocket 和 standalone web search。

`to_api_provider` 把声明式 Provider 配置转成实际 HTTP 层的 `ApiProvider`，包括 base URL、Header 和 retry policy。

## 10. 认证方式必须互斥校验

Provider 支持多种认证来源，但不能无条件叠加。`validate` 明确禁止：

- AWS 与 `env_key`、静态 bearer、command auth、OpenAI auth 同时配置；
- command auth 与 `env_key`、静态 bearer、OpenAI auth 同时配置；
- AWS 与 WebSocket 同时启用（当前尚不支持升级请求的 SigV4 签名）。

这是很重要的安全模式：不要默默决定多个凭据中“哪个优先”，而应在配置加载阶段拒绝歧义。

## 11. AuthManager

认证实现在 `codex-rs/login/src/auth/`，协议枚举在 `codex-rs/protocol/src/auth.rs::AuthMode`。它覆盖 ChatGPT OAuth、API key、Headers、Agent identity、PAT、外部 bearer、Bedrock 等模式。

`AuthManager` 的职责包括：

- 从配置和认证存储解析当前认证；
- 缓存认证状态；
- 在 access token 接近过期时刷新；
- 刷新成功后持久化 token 和 `last_refresh`；
- 认证文件外部变化时重新读取；
- 区分永久刷新失败和临时网络失败；
- 向模型目录和请求客户端暴露当前 `AuthMode`。

认证模式还影响默认 API base URL：ChatGPT/PAT/Headers 等 Codex backend 模式使用 ChatGPT Codex 端点，普通 API key 默认使用 `https://api.openai.com/v1`。

## 12. 从启动到一次模型请求

```text
加载 Config
  -> 解析 ModelProviderInfo
  -> 创建 AuthManager
  -> 创建 ModelsEndpointClient
  -> 创建 ModelsManager
  -> 按刷新策略获得 catalog
  -> 选择 model slug
  -> get_model_info + config overrides
  -> TurnContext 保存 model_info/provider/auth
  -> Prompt/Tool 构造读取模型能力
  -> ModelClientSession 构造实际 API 请求
  -> HTTP 层按 Provider 添加认证与重试
```

把这些层拆开后，换 Provider 不需要重写 Agent Loop；增加模型能力字段也不需要修改认证代码。

## 13. 常见故障定位

### 模型列表不更新

检查刷新策略、缓存版本/TTL、是否具备 Codex backend 或 command auth、ETag 是否变化。

### 显式模型行为异常

检查 `get_model_info` 是否命中准确 slug、最长前缀或 namespaced suffix；查看是否使用了 fallback metadata。

### 请求发错端点

检查 `AuthMode`、Provider `base_url` 和 `requires_openai_auth`，不要只看模型名。

### Header 没有生效

`env_http_headers` 的值是“环境变量名”，只有变量存在且非空才会添加。Header 名和值解析失败也会被忽略。

### Token 刷新反复失败

区分永久错误（例如 refresh token 失效/复用）和瞬态服务错误；查看磁盘认证是否已被另一个进程替换。

## 14. 可复用设计原则

- 将模型能力、模型目录、请求 Provider、认证分成独立类型；
- 用枚举表达刷新策略和认证模式；
- 内置目录保证离线可用，远端元数据允许动态升级；
- 缓存必须包含版本、时效和来源身份；
- 显式配置优先，但要受服务端硬能力上限约束；
- 认证方式应互斥验证，不要靠隐含优先级；
- 未知模型可降级运行，但必须留下可观测标记；
- AuthManager 负责刷新生命周期，业务层只消费有效认证。

