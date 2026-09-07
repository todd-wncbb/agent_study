# 18：模型、Provider 与认证

## 先分清三个名字

用户选择“使用哪个模型”时，实际涉及三层：

| 概念 | 回答的问题 |
|---|---|
| Model | 要使用哪一种推理能力和上下文规格？ |
| Provider | 请求发到哪里、使用哪种 wire API 和重试策略？ |
| Auth | 用什么凭据证明调用者有权限？ |

同一个模型名称可能由不同 Provider 提供；同一个 Provider 也可能展示多个模型。把三者混在一起，常导致“模型选对了，请求却发错端点”之类问题。

## 1. ModelsManager 像模型目录管理员

`ModelsManager` 负责获取、缓存、合并和筛选模型目录，再生成适合选择器使用的 `ModelPreset`。

它支持三种直觉明确的刷新策略：

- Online：总是尝试获取最新目录；
- Offline：只使用缓存，不访问网络；
- OnlineIfUncached：缓存可用且足够新时复用，否则联网刷新。

这解决的是“有哪些模型可以选”，不是“本轮模型请求怎样流式传输”；传输细节仍由前面讲过的 `ModelClient` 负责。

## 2. 为什么模型目录需要缓存

每次打开选择器都联网会慢，也会让离线启动失败。模型目录因此可以保存在内存和磁盘缓存中，并带有刷新时间等元数据。

缓存的风险是过期或身份混淆。设计 cache key 时要考虑 Provider、租户或 API base URL 等身份；否则切换服务后可能误用上一个服务的模型目录。

## 3. 默认模型与显式模型

如果用户明确配置了模型，系统通常应保留这个选择，并在不支持时给出明确诊断，而不是悄悄换成“差不多”的模型。

如果用户没有指定，ModelsManager 才从当前认证可见的模型目录中选默认项。默认值不仅受排序影响，也可能受认证方式、模型可见性和 Provider fallback policy 影响。

## 4. ModelInfo 会真正改变 Agent 行为

`ModelInfo` 不只是 UI 上的一行名称。它可以携带：

- context window 和自动压缩阈值；
- 支持的 reasoning effort；
- tool call 或输入模态能力；
- 默认提示和 token-budget 建议；
- 可见性、优先级及迁移信息。

因此“换模型”可能改变工具暴露、压缩时机和 prompt 构造，不能只替换请求 JSON 中的 model 字符串。

## 5. Provider 决定怎样连接服务

`ModelProviderInfo` 描述的内容包括：

- base URL 和 wire API；
- query 参数与 HTTP headers；
- API key 所在环境变量；
- request/stream retry 与 idle timeout；
- 是否支持 WebSocket 或独立 Web Search；
- 是否要求 OpenAI 登录，或使用命令、AWS 等认证。

这是一份连接和传输配置，不是模型能力目录。

## 6. 为什么认证方式要互斥校验

假设一个 Provider 同时配置：环境变量 API key、固定 bearer token、命令生成 token 和 AWS 签名。程序若自行猜优先级，用户很难知道最终用了哪个身份，也可能把凭据发往错误端点。

所以 `ModelProviderInfo::validate()` 会拒绝不兼容组合。例如 AWS 配置与其他几种认证字段不能任意并存；当前 AWS 模式也不能声称支持尚未实现签名的 WebSocket upgrade。

原则是：**有歧义时启动失败，比静默选择一种凭据更安全。**

## 7. AuthManager 的职责

AuthManager 负责当前登录状态和可用凭据，例如 ChatGPT/OAuth、API key 或其他受支持模式。它还影响模型目录过滤：某些模型可能只对特定 backend auth 可见。

认证信息不应被复制进 conversation history、普通工具输出或错误日志。模型只需要知道可用能力，不需要看到原始 token。

## 8. 从启动到模型请求

```text
读取 Config
  → 解析 ModelProviderInfo 并验证认证组合
  → AuthManager 解析当前身份
  → ModelsManager 读取/刷新模型目录
  → 选择显式或默认模型
  → ModelInfo 派生上下文和工具能力
  → ModelClient 按 Provider 配置发送请求
```

每一步失败的含义不同。模型目录拉取失败，不等于 token 一定无效；HTTP 401 也不等于模型名称不存在。

## 9. 故障排查顺序

### 模型选择器没有最新模型

检查 refresh strategy、缓存新鲜度、认证可见性和远端目录响应。

### 请求发错地址

检查选择的 Provider、base URL、wire API 与 query/header 配置，不要先改模型名。

### 反复出现未授权

检查当前 AuthMode、凭据来源、刷新结果以及请求实际使用的 header；避免在日志中打印秘密值。

### 换模型后工具消失

比较两个 `ModelInfo` 的能力和 Tool plan，而不只比较 slug。

## 常见误解

- **“模型名唯一决定请求发到哪里。”** 端点和传输由 Provider 决定。
- **“能登录就应该看到所有模型。”** 模型目录还会按认证与可见性过滤。
- **“Provider 可以同时配置多种凭据，系统挑一个即可。”** 歧义认证应被明确拒绝。

## 读完后自测

1. ModelsManager 与 ModelClient 的职责有什么区别？
2. 为什么切换 Provider 后，模型目录缓存身份很重要？
3. 换模型为什么可能改变工具和压缩行为？

## 本章词汇表

| 词语 | 直译 | 在模型与认证中的意思 |
|---|---|---|
| Model | 模型 | 一组推理、上下文和工具能力规格 |
| Provider | 提供方 | 模型请求的服务来源、端点和传输配置 |
| Auth | 认证 | 证明调用者身份和权限的凭据机制 |
| Catalog | 目录 | 当前身份可见的模型及其 metadata 集合 |
| Preset | 预设 | 为 UI 选择器整理好的模型选择项 |
| Endpoint | 端点 | 接收模型 API 请求的网络地址 |
| Credential | 凭据 | API key、token 或签名材料等秘密身份数据 |
| Refresh strategy | 刷新策略 | 决定在线拉取还是使用缓存模型目录的规则 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

- `codex-rs/models-manager/src/manager.rs`：目录、刷新和默认选择；
- `codex-rs/models-manager/src/model_info.rs`：模型 fallback metadata；
- `codex-rs/protocol/src/openai_models.rs`：`ModelInfo`；
- `codex-rs/model-provider-info/src/lib.rs`：Provider 配置与校验；
- `codex-rs/login/src/auth/`：AuthManager 实现；
- `codex-rs/protocol/src/auth.rs`：认证协议类型。
