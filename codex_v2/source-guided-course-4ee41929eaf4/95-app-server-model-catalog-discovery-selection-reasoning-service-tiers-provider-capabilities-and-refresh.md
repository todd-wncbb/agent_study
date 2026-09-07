# 95：App-server 模型目录、选择、Reasoning、Service Tier、Provider 能力与刷新

> 源码基线：`4ee41929eaf4`。本章解决“App 的模型选择器从哪里得到数据；目录里显示的默认模型、默认推理强度和加速档位，怎样与配置、Thread、Turn、Provider 及真正采样请求汇合；目录刷新后，已经运行的任务会不会自动换模型”。

## 1. 本章解决什么问题

假设模型选择器显示：

```text
Model A
Reasoning: low / medium / high
Default: medium
Service tier: priority
Input: text / image
```

用户点了 `high`，但你仍需要回答：

- 这行模型来自内置目录、磁盘缓存还是远端 `/models`？
- 为什么另一个登录方式看不到它？
- `isDefault=true` 是后端直接指定，还是客户端重新计算？
- 推理强度数组能否按名称自行排序？
- 目录说支持 image，是否等于 Provider 支持 image generation？
- 模型的 `priority`、`visibility` 和 `hidden` 有什么区别？
- 选中的模型何时成为 Thread 的稳定设置？
- Turn 中换模型后，不支持的旧 effort 怎么处理？
- 目录刷新后，当前 Turn 会不会突然切到新模型？
- 服务端实际把请求路由到另一模型时，App 如何知道？

## 2. 资料边界

当前模型家族、公开能力和推理参数应以[官方 OpenAI 模型指南](https://developers.openai.com/api/docs/guides/latest-model)为准；Codex 中 `model`、`model_reasoning_effort` 等公开配置键可查[官方 Codex 配置参考](https://developers.openai.com/codex/config-reference)。

本章不把任何当前模型名称或可用档位写成永久事实。App-server 的目录 wire、缓存、认证过滤、默认选择和运行时解析，以固定提交源码为准。

## 3. 先说人话：模型目录像餐厅菜单，不是后厨订单

`model/list` 像菜单：

- 展示可选菜名。
- 给出说明和推荐搭配。
- 标出默认项、隐藏项和升级提示。

真正开始 Thread/Turn 后，Core 才像后厨订单系统：

- 解析最终模型。
- 加载完整 `ModelInfo`。
- 选择有效 reasoning、service tier、instructions 和工具能力。
- 构造实际采样请求。

菜单与订单相关，但不是同一个对象。

## 4. 四个最容易混淆的对象

| 对象 | 面向谁 | 主要职责 |
|---|---|---|
| `ModelInfo` | Core/runtime | 完整模型行为元数据 |
| `ModelPreset` | 选择器中间层 | 排序、过滤、默认项和展示所需数据 |
| App-server `Model` | App wire | 可序列化的选择器合同 |
| `ModelProviderInfo` | 连接层 | endpoint、认证、wire API、重试和 Provider 能力 |

## 5. 最重要的一句话

模型名称决定“请求哪种模型能力”，Provider 决定“请求发到哪里、怎样认证和传输”，模型目录决定“当前 UI 可以推荐哪些选择”；三个维度不能合并成一个字符串。

## 6. 本章贯穿案例

假设目录有：

```text
A: priority=0, visible, efforts=[low, focused, high], default=focused
B: priority=1, hidden, efforts=[medium, high], default=medium
C: priority=2, visible, supported_in_api=false
```

接下来反复观察：

- ChatGPT auth 与 API-key auth 看见什么。
- `includeHidden` 是否改变 B。
- 谁被标成 `isDefault`。
- 为什么 `focused` 即使客户端没听过也能保留。
- Thread 显式请求 B 时是否应该被静默替换。

## 7. App-server 的两个模型读取方法

| RPC method | 回答的问题 |
|---|---|
| `model/list` | 当前选择器有哪些模型与展示元数据？ |
| `modelProvider/capabilities/read` | 当前配置的 Provider 支持哪些连接级能力？ |

这两个方法都没有 Thread serialization scope；它们不修改 Thread 状态。

## 8. 为什么账户额度不在本章

“模型支持 high reasoning”和“账户今天还能否继续使用”是两类事实。

后者属于 `account/rateLimits/read` 与更新通知，将在后续账户状态专题中讲；不要根据模型目录推断剩余额度。

## 9. `model/list` 请求

```json
{
  "cursor": null,
  "limit": 20,
  "includeHidden": false
}
```

三个字段都可省略或为 `null`。

## 10. `cursor`

游标是服务端返回、客户端原样传回的分页位置。

固定提交的实现恰好把它编码成十进制数组 offset，但协议把它声明为 opaque cursor；客户端不应自己加一、减一或推导内容。

## 11. `limit`

省略时，固定提交一次返回当前全部模型。

传入 `0` 也会通过 `.max(1)` 变成至少一项；随后上限不会超过总模型数。

## 12. `includeHidden`

默认 `false`。

为 `true` 时包含目录中不应出现在默认 picker 的项，并在每项上返回 `hidden=true`。

## 13. Hidden 不等于不可用

隐藏项可能用于：

- 已有配置继续工作。
- 迁移或灰度。
- 深链接和诊断。
- 老 Thread 恢复。

它只是默认选择器可见性，不是授权结论。

## 14. `model/list` 响应

```json
{
  "data": [
    {
      "id": "model-a",
      "model": "model-a",
      "displayName": "Model A",
      "description": "...",
      "hidden": false,
      "supportedReasoningEfforts": [],
      "defaultReasoningEffort": "medium",
      "inputModalities": ["text", "image"],
      "supportsPersonality": true,
      "serviceTiers": [],
      "defaultServiceTier": null,
      "isDefault": true,
      "upgrade": null,
      "upgradeInfo": null,
      "availabilityNux": null
    }
  ],
  "nextCursor": null
}
```

## 15. `id` 与 `model`

固定提交从 `ModelPreset` 投影时，两者通常都来自模型 slug。

协议仍分别保留稳定 preset ID 与实际模型请求名的概念；客户端不要因为当前相同就假设永远可以删除其中一个字段。

## 16. `displayName` 与 `description`

这是 UI 文案，不应作为业务身份。

保存配置、恢复 Thread 和比较模型时使用 `model`/ID，不要使用可能本地化或变化的显示名称。

## 17. `modelSpecialty`

可选字符串，用来标记目录声明的专长类别。

固定提交存在 cybersecurity specialty 常量，但 wire 设计为可扩展字符串；客户端应对未知值宽读，而不是把枚举写死。

## 18. `supportedReasoningEfforts`

每项包含：

```json
{
  "reasoningEffort": "high",
  "description": "More reasoning for difficult work"
}
```

描述由模型目录提供，适合选择器解释成本/质量取舍。

## 19. Reasoning 数组顺序是合同

App-server README 明确要求客户端保留目录数组顺序。

不要自行按：

- 字母顺序。
- 客户端内置的 low/medium/high 表。
- 推测的“强弱”数值。

目录可能给出客户端尚不认识的字符串和特定 progression。

## 20. 为什么 ReasoningEffort 支持 Custom

固定提交的 Rust enum 包含常见 variant，也包含：

```rust
Custom(String)
```

非空未知字符串不会解析失败，而是保留原值。

这允许后端新增 effort，而不必等所有客户端先升级。

## 21. 空 effort 为什么被拒绝

空字符串没有可区分语义。

反序列化要求 reasoning effort 至少一个字符；未知非空值可前向兼容，空值则是无效数据。

## 22. `defaultReasoningEffort`

它是该模型目录的推荐默认 effort。

`ModelInfo.default_reasoning_level` 缺失时，投影为 `ReasoningEffort::None`，但 runtime 的显式/缺省解析还要结合 Thread/Turn 设置。

## 23. 推荐默认值不等于管理员强制值

模型目录默认回答：

```text
“如果用户没选，这个模型建议什么？”
```

requirements 的 new-thread default 或用户配置回答不同问题。不能把 catalog default 当组织政策。

## 24. `inputModalities`

表示模型接受的用户输入类型：

- text
- image
- audio

这是“输入到该模型”的能力，不是“生成图像”的 Provider 能力。

## 25. 旧 payload 的 modality 默认

若旧目录 payload 缺少 `input_modalities`，固定提交保守默认：

```text
[text, image]
```

这是向后兼容默认，不代表所有未来模型都天然支持二者；新目录可显式收窄。

## 26. `supportsPersonality`

不是简单读一个后端 bool。

`ModelInfo` 只有同时满足：

- instructions template 含 personality placeholder。
- instructions variables 提供完整 default/friendly/pragmatic 文案。

才投影为支持 personality。

## 27. 为什么要从模板完整性推导

若只存在占位符却缺少一种 personality 文案，UI 允许选择后会构造不完整 instructions。

能力必须表示真正能安全完成的行为，不是“看起来有相关字段”。

## 28. `serviceTiers`

每个档位包含：

```json
{
  "id": "priority",
  "name": "Fast",
  "description": "..."
}
```

保存和发请求用 `id`；name/description 是展示信息。

## 29. `additionalSpeedTiers`

这是旧字段，协议注释已标为 deprecated，推荐使用 `serviceTiers`。

客户端在兼容窗口内可以宽读旧字段，但新行为应围绕结构化 service tiers。

## 30. `defaultServiceTier`

表示目录为该模型声明的默认 tier ID。

固定提交的 session 初始化不会因为这个字段存在，就在用户未配置 tier 时自动发送它；选择与 request 规范化还有 Feature::FastMode 门控。

## 31. Catalog default tier 与 request default 的差别

目录默认是产品展示/建议元数据。

请求值 `default` 则表示让服务端使用默认路由；Core 在真正构造请求时会把该特殊值过滤掉，而不是作为普通支持 tier 发送。

## 32. `upgrade` 与 `upgradeInfo`

`upgrade` 是兼容旧客户端的目标模型 ID。

`upgradeInfo` 可包含：

- `model`
- `upgradeCopy`
- `modelLink`
- `migrationMarkdown`

新 UI 可以展示更完整迁移引导。

## 33. Upgrade 是建议，不是自动 reroute

目录说“建议迁移到 B”，不表示当前 Thread 已经改用 B。

自动替换模型必须有单独的显式策略或服务端 reroute 事件。

## 34. `availabilityNux`

NUX 是 new user experience/onboarding 提示。

字段含 message，用于某模型新获得可用性时的介绍，不参与模型身份、排序或请求参数。

## 35. `isDefault`

固定提交不是直接信任远端某个 default bool。

它在构造 picker presets 后重新计算，并确保最多一个默认项。

## 36. 默认模型怎样选出

顺序是：

1. `ModelInfo` 按 `priority` 升序排序。
2. 按认证模式过滤。
3. 清除所有 preset 的 `is_default`。
4. 第一个 picker-visible 模型成为默认。
5. 若没有 picker-visible，才使用第一个剩余模型。

## 37. 为什么先过滤再标默认

若先标默认，再按认证过滤，默认项可能被删掉，剩余列表没有默认。

当前顺序保证默认一定来自该用户实际可用的候选集合。

## 38. Priority 小还是大优先

源码用：

```text
sort_by_key(model.priority)
```

因此数值较小排在前面。

不要把 priority 当“分数越高越好”。

## 39. 贯穿案例的默认项

A priority 0 且 visible，B hidden，C priority 2 visible。

过滤后只要 A 仍存在，A 就是默认；B 即便排列靠前，只要 hidden 且另有 visible，也不会成为 picker default。

## 40. 认证过滤

`ModelPreset::filter_by_auth` 的规则：

- ChatGPT mode：保留全部目录模型。
- 非 ChatGPT mode：只保留 `supported_in_api=true` 的模型。

## 41. `supportedInApi` 为什么不直接暴露到 App Model

它在进入 App-server wire 前已用于过滤。

客户端拿到的是当前 auth 下候选集，不必再次复制 Core 的认证判断。

## 42. 登录成功不等于看到相同目录

两种身份都能认证，不代表它们有相同 backend、产品权限或 API 支持集合。

模型选择器差异首先检查 auth mode 与目录过滤，而不是立刻判断缓存损坏。

## 43. `ModelVisibility`

Core 模型元数据有：

- `list`
- `hide`
- `none`

投影到 picker 时，只有 `list` 令 `show_in_picker=true`；App wire 用反向字段 `hidden = !show_in_picker` 简化客户端判断。

## 44. Hide 与 None 在 App wire 中会收敛

两者都可能投影成 hidden。

这说明 App `Model` 是面向选择器的简化投影，不保证暴露 `ModelInfo` 每个内部状态差异。

## 45. 为什么 `model/list` 不能直接返回 ModelInfo

`ModelInfo` 包含大量 runtime-only 内容，例如：

- shell/tool mode。
- instructions template。
- truncation policy。
- context window 与 compaction metadata。
- parallel tool call 支持。
-内部 fallback 标记。

把它全部暴露给 App 会扩大协议和泄露不必要实现细节。

## 46. ModelInfo 真正影响哪些行为

代表性字段：

- context window 与有效百分比。
- auto compact threshold。
- tool output truncation。
- shell/apply patch/web search tool 形态。
- reasoning summary 与 verbosity。
- parallel tool calls。
- instruction templates。
- tool mode、multi-agent version。

因此换模型不是只改 HTTP JSON 的 `model` 字段。

## 47. `model/list` 的入口链路

```text
ClientRequest::ModelList
  -> MessageProcessor
  -> CatalogRequestProcessor::model_list
  -> supported_models
  -> ThreadManager::list_models
  -> ModelsManager::list_models(OnlineIfUncached)
  -> build_available_models
  -> ModelPreset -> App Model
  -> hidden filter
  -> pagination
  -> ModelListResponse
```

## 48. 为什么 refresh strategy 是 OnlineIfUncached

打开 picker 时希望：

- 有新鲜缓存就快速返回。
- 没缓存或缓存过期时尝试远端更新。
- 网络失败时仍可使用已经装载的目录状态。

这比每次强制在线更适合交互 UI。

## 49. 三种 RefreshStrategy

| 策略 | 行为 |
|---|---|
| `Online` | 忽略普通缓存命中逻辑，尝试远端获取 |
| `Offline` | 只尝试缓存，不发远端请求 |
| `OnlineIfUncached` | 新鲜缓存优先，否则联网 |

## 50. 非 root Agent 为什么使用 Offline

Core 创建 session 时，非 root agent 选择 Offline refresh strategy。

子 Agent 不应各自触发模型目录网络刷新；它们优先复用已有/缓存目录，减少并发网络和身份状态波动。

## 51. ModelsManager trait 的职责

它协调：

- raw catalog 获取。
- 缓存与刷新策略。
- 认证过滤。
- 默认模型选择。
- 模型 metadata 查找。
- ETag 触发刷新。
- collaboration mode presets。

它不是采样 HTTP client。

## 52. ModelsEndpointClient 的职责

Provider 侧 endpoint adapter 负责：

- 判断是否使用 Codex backend。
- 判断是否具备 command auth。
- 调 `/models`。
- 处理 Provider auth 和 transport。
- 返回 `Vec<ModelInfo>` 与可选 ETag。

刷新策略和缓存仍归 ModelsManager。

## 53. 为什么接口返回 boxed Future

`ModelsEndpointClient` 与 `ModelsManager` 都作为 trait object 被共享。

显式 future alias 让不同 provider/manager 实现可以在同一动态分发边界后返回异步结果。

## 54. `/models` 请求

固定提交构造：

```text
GET <provider-base>/models?client_version=<whole-version>
```

client version 让后端按客户端可理解的合同返回目录。

## 55. `/models` 的 timeout

Provider endpoint 对刷新使用 5 秒 timeout。

目录网络失败不应无限阻塞 App picker 或 Thread 启动；ModelsManager 会记录刷新失败并继续使用当前内存状态。

## 56. 为什么刷新失败通常不让 list_models 返回错误

`raw_model_catalog` 捕获刷新错误并记录日志，然后返回当前 `remote_models`。

目录属于可降级控制面：旧菜单通常比完全没有菜单更有用。

## 57. 这不等于吞掉所有错误

请求分页 cursor 无效仍返回 JSON-RPC invalid request。

“远端刷新失败可用旧快照”与“客户端参数非法”是两类错误，不能都降级为空列表。

## 58. 内存目录

`OpenAiModelsManager` 用异步 `RwLock<Vec<ModelInfo>>` 保存当前 remote models。

读取会 clone 快照，避免调用者长期持锁；更新整体替换或合并列表。

## 59. 磁盘缓存

默认文件：

```text
$CODEX_HOME/models_cache.json
```

默认 TTL 为 300 秒。

## 60. 缓存条目内容

`ModelsCacheEntry` 包含：

- `fetched_at`
- `etag`
- `client_version`
- `models`

每个字段都参与判断快照是否仍适合复用。

## 61. Fresh 的定义

若：

```text
now - fetched_at <= TTL
```

则 fresh。

TTL 为 0 永远不 fresh；无法转换的异常 Duration 也按不 fresh 处理。

## 62. Client version 为什么进入缓存资格

后端目录可能随客户端版本调整字段或候选。

缓存没有版本或版本不同，固定提交视为 miss，避免新客户端盲用为旧合同生成的目录。

## 63. 当前缓存身份的已知缺口

固定提交源码有 TODO：还应把 Provider identity 纳入缓存资格，以免切换 Provider 后复用另一个 Provider 的新鲜文件。

文档应把这写成当前实现限制，而不是声称已经完全隔离。

## 64. Cache error 的降级

- 文件不存在：普通 miss。
- stale/version mismatch：普通 miss。
- 读取或解析错误：记录 error，按 miss 处理。
- 写缓存失败：记录 error，不让目录获取失败。

## 65. 为什么缓存错误非致命

缓存是可重建副本，不是权威用户数据。

只要远端或 bundled catalog 仍可用，删除/损坏缓存不应阻止 Codex 工作。

## 66. Remote catalog 与 bundled catalog 怎样组合

默认启动会先加载 bundled models。

收到远端目录后，是否完全替换取决于 auth 和目录内容；否则按 slug 将远端项覆盖/追加到 bundled 列表。

## 67. ChatGPT remote source of truth 条件

同时满足：

1. 远端列表非空。
2. 至少一个模型 visibility 为 `list`。
3. 当前 auth 是 ChatGPT account。

则远端目录整体成为 source of truth。

## 68. 为什么要求至少一个 visible 模型

若远端异常返回全隐藏/空目录，直接替换可能让普通 picker 完全消失。

至少一个 list 项是接受“远端权威”的基本健康信号。

## 69. 非权威合并规则

从 bundled models 开始，对每个远端 model：

- slug 已存在：替换该项。
- slug 不存在：追加。

这保留 fallback catalog，同时允许远端覆盖 metadata。

## 70. 为什么按 slug 合并

display name、description 和 priority 都可能变化，不能作为稳定 identity。

slug 是运行时请求名，也是当前合并键。

## 71. ETag 从哪里来

`ModelsClient` 从 `/models` HTTP response 的 `ETag` header 读取字符串，与目录一起返回 manager。

它不是 body 中的模型字段。

## 72. 采样响应也能带 Models ETag

Core 的 `ResponseEvent::ModelsEtag(etag)` 会调用：

```text
models_manager.refresh_if_new_etag(...)
```

这让正常采样流提示“模型目录已有新版本”。

## 73. ETag 相同

若 manager 已有相同 ETag：

- 不重新下载目录。
- 尝试刷新缓存 TTL。

这相当于低成本 revalidation。

## 74. ETag 不同或本地未知

触发 `Online` refresh，重新取远端目录并更新：

- 内存 models。
- 当前 ETag。
- 磁盘 cache。

## 75. TTL refresh 的节流

文件 cache 的 `refresh_ttl` 若条目年龄仍在半个 TTL 以内，直接返回。

这样频繁收到相同 ETag 时不会反复写磁盘更新时间。

## 76. App-server 后台刷新 Worker

固定提交在 MessageProcessor 构造时启动 `ModelsRefreshWorker`。

它立即执行一次 Online list，然后每 3 分钟再次刷新。

## 77. Worker 为什么持 Weak manager

worker 用 `Arc::downgrade` 保存 manager。

后台刷新任务不应反向保活整个模型系统；当最后一个真正 owner 释放，upgrade 失败，worker 自然退出。

## 78. Worker 的关闭

`ModelsRefreshWorker` 持 CancellationToken。

显式 `shutdown()` 或 Drop 都会 cancel；循环在刷新前检查，并在 sleep 与 cancellation 之间 `select!`。

## 79. 为什么刷新不会自动切换当前模型

刷新更新目录 snapshot。

已经建立的 SessionConfiguration 和 TurnContext 拥有解析后的 model/model_info；它们不会因为 picker 列表改变就无条件改写。

## 80. `model/list` 分页的具体规则

固定提交：

- cursor 解析为 start offset。
- 非数字 cursor：invalid request。
- start > total：invalid request。
- start == total：返回空 data，`nextCursor=null`。
- end 未到 total：`nextCursor=end.to_string()`。

## 81. 为什么 start == total 合法

它代表正好位于列表末尾。

返回空末页比报错更便于客户端处理目录在分页期间缩短的边界情况。

## 82. 分页不是稳定快照事务

每次请求都可能刷新/重建模型列表。

若两页之间目录或认证状态变化，offset cursor 可能看到重复、遗漏或提前结束；协议没有声明跨页 snapshot token。

## 83. 客户端怎样应对目录分页变化

模型列表通常很小，可使用足够大的 limit 一次读取。

必须分页时：

- 把 cursor 当 opaque。
- 以 model ID 去重。
- 遇到目录明显变化时重新从第一页读取。

## 84. `modelProvider/capabilities/read`

请求体是空对象：

```json
{}
```

响应三个 bool：

```json
{
  "namespaceTools": true,
  "imageGeneration": true,
  "webSearch": true
}
```

## 85. 这三个能力属于 Provider

- `namespaceTools`：是否支持 namespaced tool 表达。
- `imageGeneration`：Provider 路由是否支持图像生成能力。
- `webSearch`：Provider 是否支持相应 web search 路由。

不是单个 Model picker 行的输入 modality。

## 86. Provider capability 怎样计算

processor：

1. 加载最新 config。
2. 根据 `model_provider` 创建 Provider。
3. 读取 `provider.capabilities()`。
4. 投影三个 bool。

## 87. 为什么每次读取最新配置

第 94 章的配置写入可能改变 `model_provider`。

capability API 不应永远使用 app-server 启动时的旧 Provider snapshot。

## 88. 目录能力与 Provider 能力的二维矩阵

| Model input modalities | Provider image generation | 含义 |
|---|---|---|
| image=true | generation=false | 模型可看图，但连接不支持生成图 |
| image=false | generation=true | Provider能提供生成工具，但当前模型不接收图片输入 |
| image=true | generation=true | 两类能力都有，仍需工具/feature/权限门控 |
| image=false | generation=false | 两类都不应在该组合中启用 |

## 89. 能力 bool 也不是最终工具可用性

最终工具计划还受：

- feature flags。
- auth/workspace policy。
- model-specific metadata。
- config 和 permissions。
-动态工具/MCP/App 可用性。

Provider capability 只是必要输入之一。

## 90. Thread 创建时模型从哪里来

`ThreadStartParams` 可显式提供：

- `model`
- `modelProvider`
- `allowProviderModelFallback`
- `serviceTier`
- `config` 中的 reasoning override 等

省略字段则进入第 94 章讲的配置层解析。

## 91. ThreadStart 为什么没有独立 `effort` 字段

固定提交的 thread/start 顶层有 model/provider/service tier，但 reasoning 通常通过 config override 或后续 turn/settings/collaboration mode 表达。

协议不同入口的字段不必完全对称，不能凭想象补字段。

## 92. Session 初始化的模型选择链

```text
resolved Config.model
  -> choose refresh strategy
  -> pre-list catalog when needed
  -> ModelsManager::get_default_model
  -> log provider fallback if model changed
  -> get_model_info(final model + config overrides)
  -> build SessionConfiguration.collaboration_mode
  -> build TurnContext
```

## 93. 显式模型优先原则

OpenAI-compatible manager 的默认实现看到 `Some(model)` 会原样保留。

目录里暂时没列出，不代表可以静默替换；自定义 slug 和旧 Thread 仍可能有效。

## 94. `allowProviderModelFallback`

这是实验性 thread/start bool，默认 false。

注释限定：允许拥有 authoritative static catalog 的 Provider，在请求模型不可用时替换为 Provider 默认模型。

## 95. 为什么 fallback 不是全局默认行为

静默换模型可能改变：

- 质量与成本。
- 上下文窗口。
- 工具能力。
- 安全和合规预期。
- 输出可复现性。

只有调用者明确授权且 Provider 目录权威时才适合。

## 96. StaticModelsManager 的 fallback

若 `allow_provider_model_fallback=true`：

- 请求模型在可用 presets 中：保留。
- 不可用或未提供：选默认。

若 false：显式模型原样保留；未提供才选默认。

## 97. 默认模型为空列表时怎么办

`default_model_from_available` 找不到 default/first 时返回空字符串。

这是失败边界，后续启动应产生明确问题；客户端不应把空模型当合法可保存选择。

## 98. ModelInfo 查找的最长前缀

给定显式模型字符串，manager 在目录 candidates 中找 slug 前缀匹配，并选择最长的那个。

这支持带 snapshot/变体后缀的模型继承基础 metadata，同时避免较短前缀抢先匹配。

## 99. Namespaced slug fallback

普通前缀匹配失败时，固定提交允许一次窄化重试：

```text
namespace/model-name
```

namespace 只能是简单字母数字、下划线或连字符，suffix 不能再含 `/`。

## 100. 为什么只剥一层 namespace

过度宽松的 suffix 匹配可能让任意路径状字符串误继承某模型 metadata。

限制一层简单 provider ID，兼顾自定义 Provider 与匹配安全。

## 101. 未知模型的 fallback metadata

若目录无匹配，`model_info_from_slug` 构造最小描述：

- slug/display name 使用输入。
- 标记 `used_fallback_model_metadata=true`。
- 提供本地 instructions。
- 使用保守工具和窗口默认。

## 102. Fallback metadata 不代表模型确实存在

它只让 Core 能为显式自定义 slug 构造内部配置。

真正采样仍可能由 Provider 返回 model-not-found；不要把本地 metadata 构造成功当远端可用证明。

## 103. Config overrides 怎样修正 ModelInfo

`with_config_overrides` 可调整：

- context window，并受 max window clamp。
- auto compact limit。
- tool output token/byte truncation。
- base instructions。
- personality template 行为。

目录是基线，最终 runtime ModelInfo 还要经过配置。

## 104. 为什么 context window 要 clamp

用户可配置更小窗口用于测试或兼容。

若配置超过模型声明 `max_context_window`，取较小值，避免仅靠本地配置声称后端支持更大窗口。

## 105. Reasoning 的三层值

至少区分：

1. `supportedReasoningEfforts`：可选集合与顺序。
2. `defaultReasoningEffort`：模型目录默认。
3. Thread/Turn 当前显式 effort：用户/模式的实际选择。

## 106. TurnContext 的 effective reasoning

```text
current reasoning_effort
  or_else model_info.default_reasoning_level
```

显式选择优先；没有才回退模型默认。

## 107. Turn 中换模型

`TurnContext::with_model` 会：

1. 加载新模型 ModelInfo。
2. 收集新模型支持的 effort 序列。
3. 若当前 effort 仍支持，保留。
4. 否则选择序列中间偏低位置。
5. 若序列为空，再用新模型 default。

## 108. “中间偏低位置”公式

源码索引：

```text
(len - 1) / 2
```

例如 4 项时取 index 1，而不是 index 2；这依赖目录顺序，因此客户端/服务端都不能擅自重排 efforts。

## 109. 为什么换模型不保留不支持的 effort

把旧模型的 `ultra` 原样发给只支持 low/high 的新模型，可能导致后端拒绝或语义不确定。

保留兼容值、否则选择新模型稳健中间值，是显式兼容策略。

## 110. TurnStart 的 sticky overrides

`TurnStartParams` 可提供：

- `model`
- `effort`
- `summary`
- `serviceTier`
- `personality`
- `collaborationMode`

注释说明这些覆盖当前 Turn 及后续 Turns，而非一次临时 HTTP 参数。

## 111. CollaborationMode 的优先级

若设置 collaboration mode，它对 model、reasoning effort 和 developer instructions 有更高语义优先级。

客户端不应同时传互相矛盾的独立字段，再猜谁生效。

## 112. Thread settings update 是异步排队

`thread/settings/update` 只确认 update 已 queued。

依赖该更新的下一步操作应等待 `thread/settings/updated`，或把相关 model/effort/tier 放在同一次更新，避免竞态。

## 113. 为什么 model 与 effort 当前存进 CollaborationMode

固定提交有 TODO：未来希望进一步整合。

当前实现把 model 和 reasoning effort 放在 collaboration mode settings 中，即使处于 Default mode；阅读代码时不要误以为只有 Plan/协作模式才拥有模型。

## 114. Service tier 的 runtime 门控

Session 初始化只有在 `Feature::FastMode` 开启时考虑 configured tier。

否则 `get_service_tier` 直接返回 None。

## 115. 不支持的显式 tier

FastMode 开启但模型目录未声明该 tier：

- 产生用户可读 warning。
- 从实际请求配置中省略。

系统不会为了遵从本地字符串而向模型发未知 tier。

## 116. 特殊 `default` tier

它可通过 session 选择校验，但在 `ModelInfo::service_tier_for_request` 中被过滤。

含义是“不要显式指定非默认路由”，而不是后端 catalog 的普通 tier ID。

## 117. Legacy `fast` 到 request value

配置编辑和 session settings 仍兼容旧 `fast` 表示，并规范化到当前 `ServiceTier` request value。

这属于迁移兼容；App 新代码应使用目录返回的结构化 tier ID。

## 118. 模型目录刷新与 Thread 恢复

恢复历史 Thread 时，持久 metadata 中的 model/provider/reasoning 会合并进 request overrides，除非调用者显式覆盖。

目录更新不应让旧 Thread 无提示地恢复成新的 picker default。

## 119. 为什么恢复优先历史值

Thread 的行为连续性比“总是使用今天最新默认模型”更重要。

否则同一 rollout 重开后可能突然改变 instructions、工具能力和 reasoning，难以解释和复现。

## 120. 服务端实际模型与请求模型可能不同

采样流可返回 `ServerModel`。

Session 检测到不匹配时可发一次 warning；`server_model_warning_emitted` 防止同一 Turn 重复提示。

## 121. `model/rerouted`

App-server notification 包含：

- `threadId`
- `turnId`
- `fromModel`
- `toModel`
- `reason`

固定提交公开的 reroute reason 为 high-risk cyber activity。

## 122. Reroute 与 upgrade 的区别

| 概念 | 时机 | 是否表示实际执行模型改变 |
|---|---|---|
| catalog upgrade info | 展示/迁移建议 | 否 |
| provider fallback | Thread 创建解析 | 是，且需策略允许 |
| model/rerouted | 运行时服务路由事件 | 是 |

## 123. `model/verification`

通知按 Thread/Turn 返回 verification 集合。

固定提交支持 trusted access for cyber；Core 对一次 Turn 使用原子 flag，避免重复发送相同 verification UI 事件。

## 124. `turn/moderationMetadata`

将模型/服务端提供的 moderation metadata 作为 JSON value 转发给 App。

这是运行时安全元数据，不是模型目录字段，也不应写回 config。

## 125. `model/safetyBuffering/updated`

包含：

- 当前 model。
- use cases/reasons。
- 是否显示 buffering UI。
- 可选 faster model。

它描述当前 Turn 的安全缓冲状态，不等于目录永久改变。

## 126. 为什么这些通知带 ThreadId 与 TurnId

多任务、多 Turn 可并发。

客户端必须把 reroute、verification、moderation 和 buffering 投影到正确运行实体，不能只更新一个全局“当前模型”标签。

## 127. 一次从 picker 到采样的完整链路

```text
model/list
  -> user selects model/effort/tier
  -> thread/start or settings/turn override
  -> layered config + requirements
  -> ModelsManager resolves model
  -> get_model_info + config overrides
  -> SessionConfiguration
  -> TurnContext effective reasoning/tier
  -> ModelClient request
  -> optional server model/reroute/safety events
  -> App notifications and UI reconciliation
```

## 128. Picker 的推荐实现

1. 调 `model/list`，通常一次取完。
2. 保留返回顺序。
3. 以 `model`/ID 做身份，不用 display name。
4. 只显示 `hidden=false`，除非是诊断/已有选择。
5. reasoning options 保持数组原序。
6. service tier 使用 ID，展示 name/description。
7. 对 Custom effort 和未知 specialty 宽读。
8. 把 `isDefault` 当当前候选集默认，不当管理员强制值。

## 129. 保存模型默认设置的推荐实现

若只是为新 Thread 设置默认：

1. 用第 94 章的 config batch write 同时写 model/effort/tier。
2. 带 expectedVersion。
3. 理解这些字段属于 session-defaults-only，不热刷新已有 Threads。
4. 保存后重新 `config/read` 对账来源。
5. 新建 Thread 时仍读取 start response 的实际 model/effort/tier。

## 130. 修改当前 Thread 的推荐实现

1. 使用 `thread/settings/update` 或同一次 `turn/start` sticky overrides。
2. model 与 effort 一起提交，避免旧 effort 不兼容。
3. 等待 settings updated 事件后发送依赖操作。
4. 用 Thread response/snapshot 确认真正解析值。
5. 监听 reroute 和 safety notifications。

## 131. 常见误解：picker 第一项就是后端第一项

不一定。

目录先按 priority 排序、按 auth 过滤，并可能混合 bundled/remote 数据；App 又可能过滤 hidden。

## 132. 常见误解：hidden 模型不能恢复

hidden 只控制默认 picker。

显式模型和历史 Thread 可能继续解析它；是否远端可用仍由 Provider 决定。

## 133. 常见误解：目录没列出就必须换默认模型

OpenAI-compatible manager 默认保留显式模型。

只有 authoritative static provider 且调用者允许 fallback 时，才按当前策略替换。

## 134. 常见误解：effort 名称可以写死

固定提交已经支持 `Custom(String)`。

客户端写死 enum 会在后端增加新 effort 时成为兼容阻塞点。

## 135. 常见误解：默认 effort 就是当前 effort

当前 Thread/Turn 显式值优先。

目录 default 只在没有显式值时作为 fallback；collaboration mode 还可能提供自己的 reasoning。

## 136. 常见误解：FastMode 开启就一定发 priority

还必须：

- 有 configured tier。
- 模型声明支持它，或它是特殊 default。
- 请求构造阶段没有过滤。

Feature 只是门控，不是完整选择。

## 137. 常见误解：刷新目录会让当前任务更聪明

刷新只更新后续选择和 metadata lookup 的来源。

当前 Session/Turn 不会仅因列表变化自动换模型；真正 reroute 会用独立事件说明。

## 138. 故障排查：看不到某模型

检查顺序：

1. `includeHidden=true` 时是否出现？
2. 当前 auth 是 ChatGPT 还是 API key？
3. `supported_in_api` 是否导致过滤？
4. remote catalog 是否成为 source of truth？
5. cache client version/TTL 是否匹配？
6. `/models` 请求是否 timeout/认证失败？
7. App 是否错误按 display name 去重？

## 139. 故障排查：默认模型不对

检查：

- priority 排序。
- auth 过滤后的第一 visible 项。
- 是否把 hidden 项误当 picker visible。
- 是否有显式 config/thread model 覆盖默认。
- Provider fallback 是否开启。
- 恢复 Thread 是否沿用持久 model。

## 140. 故障排查：换模型后 effort 变化

比较：

- 新旧模型 supported efforts，保持目录顺序。
- 当前 effort 是否在新集合中。
- fallback index `(len-1)/2`。
- 新模型 default effort。
- collaboration mode 是否覆盖独立 effort。

## 141. 故障排查：tier 显示可选但请求没带

检查：

- FastMode 是否启用。
- 保存的是 tier ID 还是 display name。
- 新模型是否仍声明支持该 ID。
- 是否使用特殊 `default`，它会在请求阶段省略。
- 是否仍在用 legacy `fast` 并被规范化。

## 142. 故障排查：目录一直不更新

检查：

- refresh strategy 是 Offline 还是 OnlineIfUncached。
- 是否为 non-root agent。
- cache 是否仍 fresh。
- 后台 refresh worker 是否存活。
- `/models` 5 秒 timeout。
- ETag 是否相同，只刷新了 TTL。
- manager 是否因 auth/provider判定不需要远端 refresh。

## 143. 故障排查：切 Provider 后目录像旧 Provider

固定提交文件 cache 尚有 provider identity TODO。

先验证：

- 当前 provider config 与 capability response。
- cache 文件来源和 client version。
- 强制 Online refresh 后是否恢复。
- 是否使用 static catalog provider。

不要把这个已知身份缺口误诊成纯 UI 排序问题。

## 144. 修改协议时的测试矩阵

| 维度 | 用例 |
|---|---|
| auth | ChatGPT/API key/command auth |
| catalog | bundled/remote/static/cache |
| visibility | list/hide/none |
| effort | known/custom/empty/order |
| tier | structured/legacy/default/unsupported |
| paging | empty/limit1/invalid/end cursor |
| refresh | offline/fresh/stale/ETag same/different |
| selection | omitted/explicit/provider fallback/resume |
| runtime | model switch/reroute/verification/buffering |

## 145. 关键集成测试已经证明什么

`model_list.rs` 覆盖：

- 大 limit 返回可见模型。
- include hidden。
- ChatGPT remote catalog 作为权威来源。
- custom reasoning effort 保序。
- 分页结束。
- invalid cursor 错误。

## 146. Provider capability 测试

固定提交测试对比默认 Provider 与 Amazon Bedrock：

- 默认 Provider 三项为 true。
- Bedrock image generation 为 false，其他两项为 true。

这证明 capability 是 Provider-specific，不应在 App 中硬编码。

## 147. ModelsManager 测试重点

修改 manager 时应检查：

- refresh strategy。
- cache miss/stale/version mismatch/error。
- remote source-of-truth 条件。
- bundled merge。
- auth filtering/default marking。
- ETag TTL renewal。
- explicit/default/static fallback。
- longest-prefix/namespaced metadata。

## 148. 为什么要测完整对象相等

模型 wire 字段很多，逐字段挑几个断言容易漏掉：

- 顺序变化。
- compatibility defaults。
- upgrade metadata。
- modality/personality/tier 漂移。

测试当前使用 `pretty_assertions::assert_eq` 比较完整响应，差异更清晰。

## 149. 修改 ModelInfo 的生成物影响

`ModelInfo` 跨 Core、cache 和 service 边界序列化。

新增字段需要考虑：

- Serde default。
- bundled model JSON。
- cache 兼容。
- App `Model` 是否需要投影。
- TypeScript/JSON Schema。
-旧 payload 读取。

## 150. 为什么新字段常需要 default

磁盘 cache 和旧后端 payload 不会与新 binary 同步升级。

没有安全 default 的新增必填字段，会让旧缓存无法解析并把兼容问题放大成启动/目录故障。

## 151. `base_instructions` 的 legacy 兼容

ModelsResponse 序列化仍为旧客户端产生 deprecated top-level `base_instructions`。

反序列化时，若新 `model_messages.instructions_template` 缺失，则把旧字段提升进去；两者都缺失才报错。

## 152. 为什么 instructions 缺失不能简单默认空串

基础 instructions 是 Agent 行为合同。

静默使用空串会让模型失去工作方式和安全边界；因此兼容 reader 可迁移旧字段，但不能把完全缺失当正常。

## 153. 一张最终心智图

```text
bundled models ----\
fresh cache --------+--> OpenAiModelsManager --> auth filter --> priority/default
remote /models -----/          |                         |
        ^                      ETag                  ModelPreset
        |                       |                         |
sampling ModelsEtag     background refresh          model/list wire

config + thread/turn overrides
             |
        resolve model
             |
   get ModelInfo + config overrides
             |
 reasoning/service tier/instructions/tools
             |
       actual sampling request
             |
 server model/reroute/verification/buffering notifications
```

## 154. 本章结论

1. `model/list` 是 picker 投影，不是完整 runtime ModelInfo。
2. 模型、Provider 与认证是三条相关但独立的选择轴。
3. 目录先按 priority 排序、按 auth 过滤，再标记唯一默认项。
4. hidden 只表示默认 picker 不展示，不等于不可恢复或不可请求。
5. reasoning efforts 是有序、可扩展字符串，客户端必须保序并宽读未知值。
6. input modality 与 Provider image-generation capability 不是同一能力。
7. OnlineIfUncached、5分钟文件TTL、ETag和3分钟worker共同维护目录新鲜度。
8. ChatGPT remote catalog满足健康条件时可成为权威，否则与bundled目录按slug合并。
9. 显式模型默认保留；Provider fallback 需要明确允许和权威static目录。
10. 目录刷新不自动切换当前任务，真正运行时改变由Thread设置或reroute事件表达。
11. effort和service tier还需在TurnContext中按模型支持范围与Feature门控解析。
12. 历史Thread恢复优先保持持久模型选择，而不是追随今天的picker默认。

## 155. 理解检查

### 问题 1

为什么不能按 low、medium、high 的客户端固定顺序重排 efforts？

答案：目录顺序本身表示模型设计的 progression，并允许 Custom effort；换模型 fallback 也依赖该顺序。

### 问题 2

API-key 用户为何可能看不到 ChatGPT 用户能看到的同一模型？

答案：非 ChatGPT mode 会过滤 `supported_in_api=false` 的 preset。

### 问题 3

`inputModalities` 含 image 是否意味着可以调用 image generation？

答案：不意味着。前者是模型输入能力；后者是 Provider capability，还要经过工具和Feature门控。

### 问题 4

远端目录没有显式请求的模型时，系统一定切到默认模型吗？

答案：不一定。普通manager保留显式模型；只有允许Provider fallback且权威static catalog判定不可用时才替换。

### 问题 5

为什么目录刷新后当前Turn不会自动换模型？

答案：目录是控制面snapshot，当前Session/Turn已有解析后的配置；运行时改变需显式设置或reroute事件。

### 问题 6

旧effort不受新模型支持时怎样选择？

答案：取新模型有序支持列表的 `(len-1)/2` 项，列表为空再取模型default。

### 问题 7

为什么 `serviceTier="default"` 最终请求可能没有service_tier字段？

答案：它表示使用默认路由，request构造会过滤该特殊值，而非发送为普通tier ID。

## 156. 源码检查点

```bash
rg -n 'pub struct ModelListParams|pub struct Model|pub struct ModelListResponse' codex-rs/app-server-protocol/src/protocol/v2/model.rs
rg -n 'ModelList =>|ModelProviderCapabilitiesRead =>' codex-rs/app-server-protocol/src/protocol/common.rs
rg -n 'async fn list_models|effective_limit|invalid cursor' codex-rs/app-server/src/request_processors/catalog_processor.rs
rg -n 'supported_models|model_from_preset|reasoning_efforts_from_preset' codex-rs/app-server/src/models.rs
rg -n 'trait ModelsManager|RefreshStrategy|apply_remote_models|refresh_if_new_etag' codex-rs/models-manager/src/manager.rs
rg -n 'ModelsCacheEntry|DEFAULT_MODEL_CACHE_TTL|is_fresh' codex-rs/models-manager/src
rg -n 'impl From<ModelInfo> for ModelPreset|filter_by_auth|mark_default_by_picker_visibility' codex-rs/protocol/src/openai_models.rs
rg -n 'get_default_model|effective_reasoning_effort|with_model|get_service_tier' codex-rs/core/src/session
```

## 157. 本章词汇表

| 英文 | 字面意思 | 在本章中的实际含义 |
|---|---|---|
| model | 模型 | 被请求执行推理、生成和工具调用的模型身份 |
| model slug | 模型短名 | Provider请求和目录合并使用的稳定模型字符串 |
| model catalog | 模型目录 | 当前manager掌握的ModelInfo集合 |
| ModelInfo | 模型信息 | Core使用的完整行为、能力、窗口、指令和工具元数据 |
| ModelPreset | 模型预设 | 经过排序、认证过滤和默认标记的picker中间对象 |
| picker | 选择器 | App让用户选择模型、effort和tier的界面 |
| provider | 提供方 | 决定endpoint、认证、wire、重试和连接级能力的对象 |
| provider capability | 提供方能力 | namespaced tools、image generation、web search等连接能力 |
| input modality | 输入模态 | 模型可接收的text、image、audio输入类型 |
| image generation | 图像生成 | Provider/工具生成图像的能力，不等于接收图片输入 |
| reasoning effort | 推理强度 | 模型目录声明并由Thread/Turn选择的推理投入字符串 |
| Custom effort | 自定义强度 | 新后端值在旧客户端中不丢失的前向兼容variant |
| progression order | 递进顺序 | 目录数组定义的effort选择顺序，客户端必须保留 |
| default effort | 默认强度 | 没有显式Thread/Turn选择时模型建议使用的effort |
| service tier | 服务档位 | 模型目录声明的路由/速度服务等级ID及展示元数据 |
| default service tier | 默认服务档位 | 目录给出的tier建议，不自动等同于实际request字段 |
| fast mode | 快速模式 | 允许Core考虑非默认service tier的Feature门控 |
| visibility | 可见性 | list/hide/none形式的目录picker展示政策 |
| hidden model | 隐藏模型 | 默认picker不显示、但可能仍供恢复或显式选择的模型 |
| priority | 优先次序 | 数字越小越靠前、参与默认选择的目录排序键 |
| source of truth | 权威来源 | 满足条件时整体决定当前目录的远端snapshot |
| bundled catalog | 内置目录 | 随binary提供、可在远端/缓存不可用时使用的模型元数据 |
| remote catalog | 远端目录 | Provider `/models` endpoint返回的最新模型列表 |
| static catalog | 静态目录 | Provider在进程内提供并视为权威的固定模型列表 |
| refresh strategy | 刷新策略 | Online、Offline或OnlineIfUncached的目录获取决策 |
| cache | 缓存 | 可重建的内存或models_cache.json目录副本 |
| TTL | 生存时间 | 缓存条目保持fresh的300秒期限 |
| ETag | 实体标签 | HTTP目录版本标识，用来低成本判断远端是否变化 |
| revalidation | 重新验证 | ETag相同时延长freshness而不重新应用内容 |
| endpoint client | 端点客户端 | 封装Provider认证/transport并真正请求`/models`的adapter |
| client version | 客户端版本 | 附加到models请求并参与cache兼容资格的版本字符串 |
| authentication filter | 认证过滤 | 根据ChatGPT/API模式移除当前身份不应看到的preset |
| provider fallback | 提供方回退 | 明确允许时将不可用显式模型替换为static目录默认 |
| fallback metadata | 回退元数据 | 目录未知slug时Core构造的保守内部ModelInfo |
| longest-prefix match | 最长前缀匹配 | 为带后缀模型选择最具体基础slug metadata的算法 |
| namespaced slug | 带命名空间模型名 | `provider/model`形式、只剥一层重试metadata匹配的名字 |
| upgrade info | 升级信息 | picker展示的新模型迁移建议，不表示已切换 |
| availability NUX | 可用性新手提示 | 模型新开放时给用户看的onboarding message |
| runtime projection | 运行时投影 | 配置与ModelInfo结合后得到实际Thread/Turn行为 |
| sticky override | 粘性覆盖 | 从本Turn开始并延续到后续Turns的model/effort/tier更新 |
| reroute | 重新路由 | 服务端实际将运行模型从fromModel改为toModel |
| verification | 验证状态 | 与当前Thread/Turn模型访问安全相关的运行时证明 |
| safety buffering | 安全缓冲 | 采样期间因安全用例显示等待或更快模型建议的状态 |
| moderation metadata | 审核元数据 | 服务端随Turn返回、供App安全UI使用的结构化信息 |
| opaque cursor | 不透明游标 | 客户端只能原样传回、不能依赖内部offset编码的分页令牌 |

