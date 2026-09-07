# 49：缓存设计、失效策略与一致性——快一点很容易，既快又不读错才困难

> 源码基线：`4ee41929eaf4`
>
> 本章主要依据当前仓库中的模型目录、Connector、Plugin、认证与 Git status 实现。OpenAI 官方文档中的 Prompt Caching 只用于帮助区分 API 前缀缓存与本地业务缓存，不把产品文档中未公开的实现当成事实。

## 1. 本章解决什么问题

缓存最表面的作用是“少算一次、少读一次、少请求一次”。真正困难的却是：

- 这份结果属于谁？
- 它依赖哪些输入？
- 原始数据改变后，缓存何时失效？
- 过期时能不能先返回旧值？
- 100 个请求同时 miss 时，是否会发出 100 次远端请求？
- 刷新过程中又发生 invalidation，旧结果会不会重新写回来？
- 缓存故障时，主功能应失败还是绕过缓存？

本章要建立的核心认识是：

> 缓存不是一个 `HashMap`，而是一份带身份、版本、有效期和更新协议的状态副本。

读完后，你应该能独立解释 TTL、ETag、cache key、stale-while-revalidate、singleflight、generation fencing 和 negative caching。

## 2. 先用“餐厅今日菜单”理解缓存

假设餐厅门口放了一块“今日菜单”牌：

- 厨房系统：权威来源；
- 门口菜单牌：缓存；
- 日期、分店、早晚餐：cache key 的一部分；
- 每 30 分钟重写菜单：TTL 刷新；
- 厨房说菜单版本没变：ETag revalidation；
- 菜品售罄后立即划掉：事件驱动 invalidation；
- 服务员先读旧菜单，同时派一人确认：stale-while-revalidate；
- 20 位服务员只派一人去厨房：singleflight。

如果菜单牌只写“宫保鸡丁”，却没写是哪家分店、哪一天，就可能把 A 店早餐错误地展示给 B 店晚餐。

这就是缓存 key 不完整造成的跨身份污染。

## 3. 缓存里必须有四样东西

把一个缓存项抽象成：

```text
CacheEntry = Key + Value + Freshness + ValidationMetadata
```

- `Key`：这份结果属于哪组输入；
- `Value`：可复用的结果；
- `Freshness`：什么时候获取、多久有效；
- `ValidationMetadata`：版本号、ETag、generation、schema version 等。

只存 value 而不存其身份和有效性依据，后续就无法判断它是否还能使用。

## 4. 权威来源和缓存副本必须分开

缓存不是事实本身。对每个缓存，先写出：

| 问题 | 示例答案 |
|---|---|
| 权威来源是什么？ | 远端 model catalog endpoint |
| 缓存副本在哪里？ | 内存中的 `remote_models` 或磁盘 JSON |
| 谁可以更新权威来源？ | 服务端、配置写接口、插件安装流程 |
| 谁可以删除缓存？ | watcher、manager、显式 invalidate API |
| 冲突时相信谁？ | 权威来源或可重放的持久日志 |

如果代码把缓存当作唯一事实来源，缓存损坏就会从“性能下降”升级为“数据丢失”。

## 5. 读缓存时的五个问题

以后看到 `cache`，按顺序问：

1. **Key 是什么？** 是否包含全部会改变结果的输入？
2. **Value 来自哪里？** 权威源是谁？
3. **Freshness 怎样判断？** TTL、版本、事件还是永不过期？
4. **并发 miss 怎样合并？** 是否有 singleflight 或二次检查？
5. **失效和加载竞态怎样处理？** 是否有 generation/version fencing？

这五问比“这里用了什么 Map”更重要。

## 6. Cache key 是一份依赖声明

假设结果由这个函数决定：

```text
result = f(base_url, account_id, workspace, client_version, config)
```

那么 cache key 至少要能区分这些会改变结果的输入。

遗漏 key 维度会产生 false hit：本不该命中却命中。放入太多无关维度则产生 false miss：本可复用却无法复用。

因此 cache key 的设计目标不是“字段越多越安全”，而是：

> 精确表达结果真正依赖的稳定输入。

## 7. 当前实例：Connector 缓存怎样隔离账号

`codex-rs/core/src/connectors.rs` 中，`AccessibleConnectorsCacheKey` 包含：

- `chatgpt_base_url`；
- `account_id`；
- `chatgpt_user_id`；
- `is_workspace_account`。

这意味着同一进程里切换账号、用户、workspace 或服务端地址时，不会误用另一身份的 Connector 列表。

它的 value 是 `Vec<AppInfo>`，还带有 `expires_at`。读取时必须同时满足：

```text
现在未超过 expires_at
        并且
cached.key == current_key
```

## 8. 安全缓存 key 不能直接泄露敏感信息

cache key 可能进入：

- 文件名；
- 日志；
- metric label；
- trace attribute；
- 分布式缓存系统。

账号 ID、token、完整请求文本不能不加判断地直接写入这些位置。

常见处理是：

- key 内部保留必要身份字段；
- 持久化路径使用稳定 hash；
- 日志只记录匿名化或允许公开的维度；
- 永远不要把 bearer token 本身作为可打印 key。

“能正确分区”和“不会泄密”要同时满足。

## 9. TTL 解决什么，不解决什么

`TTL` 是 **Time To Live**，表示缓存项最多被当作 fresh 多久。

它解决：

- 没有事件通知时，旧值不会永久存在；
- 控制远端请求频率；
- 给磁盘快照定义有限有效期。

它不解决：

- TTL 内权威数据已经改变；
- cache key 不完整；
- 多请求同时过期造成击穿；
- 刷新结果被旧 task 覆盖；
- 系统时钟跳变；
- 过期后应该失败还是返回旧值。

TTL 只是 freshness policy 的一个维度。

## 10. `Instant` 和墙上时间各适合什么

进程内 TTL 常用 `std::time::Instant`：

- 单调递增；
- 不受系统时间校准影响；
- 适合“从现在起 5 分钟”。

磁盘缓存需要跨进程保存，通常只能记录 UTC 时间：

- 可以序列化；
- 重启后仍可计算 age；
- 但要处理未来时间、时钟回拨和非法时间。

当前 Connector 内存缓存使用 `Instant`；模型和远端插件磁盘缓存使用 `DateTime<Utc>`。

## 11. 当前实例：模型目录缓存的内容

`codex-rs/models-manager/src/cache.rs` 的 `ModelsCacheEntry` 保存：

- `fetched_at`：最近获取或验证时间；
- `etag`：服务端版本验证标识；
- `client_version`：该目录对应的 Codex 客户端版本；
- `models`：模型列表。

`ModelsCache::load` 的契约要求：

- 过期项返回 `Ok(None)`；
- client version 不匹配也返回 `Ok(None)`；
- 后端错误返回 `Err`；
- manager 将 miss 和 cache error 都回退为 endpoint fetch。

这说明“没有可用缓存”和“缓存系统坏了”在观测上不同，在业务策略上都可以绕过缓存。

## 12. Fresh、Stale、Missing、Error 不应混为一谈

建议至少区分四种读取结果：

| 状态 | 含义 | 可能策略 |
|---|---|---|
| Fresh | 仍在有效期 | 直接返回 |
| Stale | 有旧值但已过期 | 刷新，或先返回再刷新 |
| Missing | 从未缓存或已删除 | 查询权威源 |
| Error | 缓存读取/解析失败 | 记录错误并绕过，或按风险失败 |

如果 API 只返回 `Option<Value>`，就无法区分 stale、missing 和 backend error，也难以制定不同降级策略。

模型缓存故意不返回 stale entry；远端插件目录则会返回 value 加 freshness 状态。两者是不同产品取舍。

## 13. Hard TTL 与 Soft TTL

可以把有效期拆成两道门：

```text
0 ───── soft TTL ───── hard TTL ─────► age
    fresh              stale 可用       不可使用
```

- soft TTL 前：直接命中；
- soft TTL 后：可以先返回旧值，同时刷新；
- hard TTL 后：禁止使用旧值，必须重新获取或失败。

当前源码不一定都显式使用两个 TTL 字段，但“Fresh/Stale + 是否允许使用”的语义可以实现同样的策略。

## 14. Stale-while-revalidate 是什么

`stale-while-revalidate` 常缩写为 SWR：

> 旧值已经过了理想新鲜期，但暂时仍可服务；后台同时获取新值供下一次使用。

优点：

- 用户请求不必等待慢刷新；
- 临时网络故障时仍有可用结果；
- 减少刷新造成的延迟尖峰。

代价：

- 用户会看到有限时间的旧数据；
- 必须定义 stale 最长可用多久；
- 后台刷新 task 需要 owner 和错误观测；
- 安全、权限、撤销类数据往往不能宽松使用旧值。

## 15. 当前实例：远端 Plugin Catalog 会返回 Stale

`codex-rs/core-plugins/src/remote/catalog_cache.rs` 的磁盘缓存 TTL 为 3 小时，并返回：

```rust
enum RemotePluginCatalogCacheFreshness {
    Fresh,
    Stale,
}
```

`codex-rs/core-plugins/src/remote.rs` 在 `PreferCache` 模式下，即使缓存 stale 也可先返回插件目录，同时设置 `cache_refresh_needed`，由上层收集需要刷新的 scope。

这与模型目录的“过期即 miss”不同：

- 模型目录：stale 不直接服务；
- Plugin Catalog：部分读取路径允许 stale，并显式安排刷新。

缓存策略必须服从数据风险，而不是全项目只用一种模板。

## 16. 哪些数据不适合返回 stale

通常应谨慎处理：

- 账号权限和授权撤销；
- 安全策略；
- 额度和支付状态；
- destructive action 的审批结果；
- 已吊销 token；
- 影响数据隔离的租户映射。

旧的插件展示名称可能只造成短暂 UI 过时；旧的“允许访问”判断却可能造成越权。

问法应是：

> 返回旧值的最坏后果是什么？

而不是“用缓存会不会更快”。

## 17. ETag：不下载内容也能重新验证

`ETag` 是服务端为某个资源版本返回的标识。客户端下次可表达：

```text
“我手里是版本 abc；如果仍是 abc，只告诉我没变。”
```

若服务端确认未变，就不必重新下载完整 payload，只需把 freshness 延长。

ETag 的作用是 validation，不等同于本地 cache key：

- cache key 决定“查哪一份缓存”；
- ETag 判断“这份内容是否仍是服务端当前版本”。

## 18. 当前实例：只刷新模型缓存的 TTL

`OpenAiModelsManager::refresh_if_new_etag` 会比较当前 ETag：

- ETag 相同：调用 `cache.refresh_ttl(...)`；
- ETag 不同：执行 online refresh。

`FileModelsCache::refresh_ttl` 保留 models、ETag 和 client version，只更新 `fetched_at`。

这避免了“内容没变却反复写入一大份模型列表”，同时保留了服务端重新验证的证据。

测试 `injected_cache_ttl_refresh_preserves_cached_payload` 会检查 payload 未改变而时间更新。

## 19. Version 是另一种失效边界

TTL 回答“这份数据多久以前获取”，version 回答“它属于哪套解释规则”。

常见版本包括：

- client version；
- schema version；
- config version；
- generation；
- resource revision；
- content hash。

即使刚写入的缓存，如果 schema 或客户端语义不兼容，也必须 miss。

## 20. 当前实例：client version 与 schema version

模型磁盘缓存要求 `client_version` 与当前版本相等；不匹配时不返回。

远端 Plugin Catalog 磁盘缓存带 `schema_version`；版本不匹配时删除文件并 miss。

两者解决不同问题：

- client version：模型目录可能随客户端能力变化；
- schema version：旧 JSON 的结构或解释方法已经变化。

不要只因为 JSON “还能反序列化”就认为语义一定兼容。

## 21. Cache invalidation 的三条路线

缓存失效通常来自：

1. **时间驱动**：TTL 到期；
2. **事件驱动**：配置写入、文件 watcher、登录账号变化、插件安装完成；
3. **版本驱动**：key、ETag、generation、schema/client version 改变。

成熟系统通常组合使用：

- 事件驱动提供及时性；
- TTL 防止事件丢失后永久陈旧；
- 版本边界防止跨身份或跨协议误用。

## 22. “删掉 Map”还不算完成失效

假设一个慢加载正在执行：

```text
Task A 读取旧配置并开始 load
         ↓
管理员 clear cache
         ↓
Task A 完成，把旧结果重新写入 cache
```

虽然 `clear()` 确实执行过，但旧数据被在途 task 复活了。

这叫 invalidation race。解决它需要 generation、version 或取消旧加载，而不只是清空容器。

## 23. Generation fencing 怎样防旧结果复活

通用模式是：

```rust
let generation = cache.current_generation();
let value = load().await;
if cache.current_generation() == generation {
    cache.insert(key, value);
}
```

`clear_cache()` 同时让 generation 加一。这样 clear 前启动的 load 即使后来完成，也不能写入新世代。

generation 不必表示业务版本；它只需要在每次逻辑失效时变化。

## 24. 当前实例：Plugin metadata 的 generation

`codex-rs/core-plugins/src/tool_suggest_metadata.rs` 中：

- cache state 有 `generation` 和 `entries`；
- `clear()` 先增加 generation，再清空 entries；
- loader 开始前记住 generation；
- 加载完成后只在 generation 仍相同时插入；
- 若 generation 已变化，则循环重新读取/加载。

同样的思想也用于 `PluginsManager` 的 loaded plugins cache。

这不是为了避免内存错误，而是为了维持语义不变量：

> clear 之后，clear 之前基于旧输入启动的工作不能重新发布结果。

## 25. 缓存大小也必须有上限

`ToolSuggestMetadataCache` 将最大 entry 数设为 1024。达到上限且要插入新 key 时，它选择清空现有 entries。

这是一种简单的 bounded cache，不是精细 LRU。优点是实现容易、内存上界清楚；缺点是达到阈值时命中率可能突然下降。

可选淘汰策略包括：

- clear-all；
- FIFO；
- LRU；
- LFU；
- TTL sweep；
- 按总字节数淘汰。

选择策略前应先确认 entry 数量、value 大小和访问分布，而不是默认引入复杂 LRU。

## 26. Cache miss 并发会形成“击穿”

如果 100 个请求同时发现 key 过期，然后都去查询远端，就会出现 cache stampede，也称缓存击穿或惊群：

```text
100 个请求同时 miss
        ↓
100 次相同的数据库/API/文件扫描
        ↓
权威源变慢
        ↓
更多请求堆积并超时重试
```

即使每个请求逻辑都正确，合在一起也可能把后端压垮。

## 27. Singleflight：同一个 key 只飞一次

`singleflight` 的含义是：

> 同一 cache key 的并发 miss 共享一个在途加载；一个 leader 真正执行，其他 waiter 等同一个结果。

它不是永久缓存。加载结束后，in-flight entry 可以删除；之后的新请求仍可重新执行。

需要区分：

- result cache：复用已经完成的结果；
- in-flight deduplication：只合并当前正在执行的相同工作；
- 两者可以组合，也可以单独存在。

## 28. 当前实例：Git status 只共享在途 Future

`codex-rs/git-utils/src/status.rs` 的 key 包含 git 路径和 canonicalized repo root。

`share_git_status_run` 把运行中的 future 转成 shared future，并在全局 Map 中只保留 weak handle：

- 相同 key 且旧 future 仍在运行：复用它；
- future 已完成：创建新运行；
- weak handle 已失效：清除旧登记。

因此它不是“Git 状态长期缓存”，而是“相同仓库的并发状态检查只执行一次”。

这很重要，因为 Git working tree 随时可能变化，长期缓存反而容易错误。

## 29. 当前实例：认证 token 用 Mutex 合并刷新

`codex-rs/login/src/auth/external_bearer.rs` 的 `BearerTokenRefresher::resolve` 会持有 `cached_token` mutex：

1. 检查 token 是否仍在 refresh interval 内；
2. miss 时在仍持锁的情况下运行 provider auth command；
3. 把新 token 写回，再释放锁。

等待同一 mutex 的后续请求醒来后会看到新 token，不会重复运行 command。

通常不鼓励跨 `.await` 持锁，但这里是有意用锁构成 per-refresher singleflight。代价是同一个 refresher 的所有 resolve 会串行等待慢命令。

## 30. Double-check 为什么常出现在 singleflight 中

另一种写法是：

```text
第一次查 cache：miss
        ↓
等待 loader permit
        ↓
第二次查 cache：可能已被别人填好
        ↓
仍 miss 才真正 load
```

`ToolSuggestMetadataCache::metadata_for_plugin` 就会在取得单 permit 后再次检查 cache。

如果没有第二次检查，排队的每个 waiter 取得 permit 后仍会重复加载，只是从“并发重复”变成“串行重复”。

## 31. Singleflight 的 key 也必须正确

把所有请求都放进一把全局锁可以避免重复，却会让不同 key 无谓互相等待。

理想粒度通常是 per-key flight：

```text
key A → flight A
key B → flight B
```

但 per-key Map 还要处理：

- flight 结束后删除 entry；
- leader 取消或 panic；
- error 是否共享给 waiter；
- 清理时不要误删后来创建的新 flight；
- key 数量必须有界。

`Arc::ptr_eq` 或 generation 常用于确认“我要删除的还是我创建的那次 flight”。

## 32. 当前实例：推荐插件请求的 OnceCell

`PluginsManager::recommended_plugins_mode_for_config` 同时维护：

- 已完成结果的 `recommended_plugins_cache`；
- 在途刷新的 `recommended_plugins_refreshes`；
- 每个 key 对应一个 `Arc<OnceCell<RecommendedPluginsMode>>`。

并发请求共享 `get_or_init` 的一次远端获取。结束后，仅当 Map 里仍是同一个 `Arc` 时才移除 in-flight entry。

这展示了“完成结果缓存 + 在途请求合并”两层结构。

## 33. 错误要不要缓存

缓存错误称为 negative caching。它可以防止不存在的 key 或持续失败的依赖被高频重复查询。

但错误缓存也可能延长故障：

- 临时网络失败被当成长期失败；
- 用户刚修好配置却仍看到旧错误；
- 权限刚授予但“无权限”结果仍在缓存；
- 后端恢复后客户端迟迟不重试。

更安全的做法通常是：

- 只缓存明确、稳定的负结果；
- negative TTL 比正结果更短；
- 瞬时 transport error 不长期缓存；
- 配置/认证变化主动失效；
- 记录 error cache hit 指标。

## 34. 缓存故障不应自动拖垮权威路径

模型缓存契约明确：

- load error 被当成 miss，继续访问 endpoint；
- store error 只记录，不让成功的远端刷新失败。

这叫 cache-aside 的可用性原则：缓存是优化层，权威读取仍能独立工作。

但它也不是普遍规则。如果缓存承担幂等去重、安全策略或限流状态，绕过缓存可能比失败更危险。此时应 fail closed 或使用一致性更强的存储。

## 35. Cache-aside、Read-through、Write-through

三种常见结构：

### Cache-aside

业务代码先查缓存，miss 后查权威源，再写缓存。当前模型 manager 很接近这种方式。

### Read-through

调用者只访问缓存接口，由缓存层负责 miss loading。

### Write-through

写入先通过缓存层，同时更新权威源和缓存。

还有 write-back：先写缓存，稍后异步写权威源。它性能高，但崩溃时可能丢尚未落地的数据，不应随意用于关键状态。

## 36. 更新数据库再删缓存，还是先删缓存

经典问题：

```text
方案 A：写数据库 → 删缓存
方案 B：删缓存 → 写数据库
```

方案 B 中，删缓存后可能有读请求把旧数据库值重新写回；方案 A 中，数据库写成功而删缓存失败，会暂时读到旧值。

更可靠的方法取决于系统能力：

- 把 version 写入 value/key；
- 使用 change event/outbox 驱动失效；
- 设置有限 TTL 作为兜底；
- 用 compare-and-set 防旧版本覆盖；
- 对关键读直接走权威源；
- 定期 reconciliation。

没有一条固定顺序能在所有分布式故障下自动保证强一致。

## 37. 缓存的是源数据，还是派生数据

Codex 的插件 metadata cache 有一个值得学习的边界：它缓存 source-derived fragment，但当前 skill config 和 auth routing 在每次 lookup 后再 project。

这样做的含义是：

- 解析 manifest、扫描 skill 等昂贵工作可以复用；
- 经常变化的认证和配置不必全部进入底层 cache key；
- 动态策略每次重新应用，不会因旧 projection 造成越权或错误展示。

缓存边界应尽量放在“昂贵且稳定”的中间结果上。

## 38. 不可变的一次初始化不是普通缓存

源码里常见 `OnceLock` / `LazyLock`：

- 编译进程序的 schema；
- 正则表达式；
- V8 初始化结果；
- 进程级静态模板。

它们更接近“延迟初始化的常量”：初始化一次后，生命周期内不失效。

只有当输入在整个进程生命周期确实不变时，才适合 `OnceLock`。如果结果依赖账号、cwd、配置或网络响应，就不能为了方便塞进全局 OnceLock。

## 39. 内存缓存与磁盘缓存的不同风险

| 维度 | 内存缓存 | 磁盘缓存 |
|---|---|---|
| 生命周期 | 通常到进程退出 | 可跨重启 |
| 时间判断 | 可用 `Instant` | 需可序列化时间 |
| 兼容问题 | 较少 | 必须考虑 schema/client version |
| 损坏来源 | 并发逻辑 | 半写入、旧文件、手工修改 |
| 写入方式 | 锁内替换 | 最好 atomic write/rename |
| 隔离 | 进程内 key | 路径、权限、租户 key |

远端 Plugin Catalog 使用原子写工具，解析失败或 schema 不匹配时会删除无效文件。

## 40. Prompt Cache 与本地业务缓存不是一回事

OpenAI API 的 Prompt Caching 复用可重复的输入前缀计算，以降低合适请求的成本和延迟。当前官方模型指南还区分 implicit caching 与 explicit breakpoints/TTL，并建议观察 cached token 相关用量。

它不缓存：

- Codex 的模型目录 JSON；
- Connector 列表；
- Plugin manifest 解析结果；
- shell 命令输出；
- 任意业务函数的最终答案。

Prompt Cache 的 key/命中由 API 请求前缀及相关配置决定；本地业务缓存则由 Codex 自己维护 value、TTL 和 invalidation。

官方参考：[OpenAI Model guidance](https://developers.openai.com/api/docs/guides/latest-model)。

## 41. 当前实例：Codex 怎样提供 Prompt cache key

`codex-rs/core/src/client.rs` 的 `ModelClient` 默认用 session ID 作为 `prompt_cache_key`，也允许 override。

Guardian review session 会基于 parent thread 生成形如 `guardian:{parent_thread_id}` 的 override，使相关 review 请求处在合适的缓存作用域。

同时，WebSocket 增量请求复用还会比较 model、instructions、tools、reasoning、service tier、prompt cache key 等属性。只有请求属性匹配且输入是增量扩展时，才可复用之前请求状态。

因此“相同 cache key”不代表任意请求都可安全视为同一上下文。

## 42. Cache warming 与 prefetch

`warming` 是在真正用户请求前主动填充缓存；`prefetch` 是预测稍后会需要的数据并提前获取。

优点是降低首个请求延迟，风险包括：

- 预热了最终无人使用的数据；
- 启动时形成流量尖峰；
- 多实例同时预热造成后端压力；
- 预热结果在使用前已过期；
- shutdown 时预热 task 未被取消。

当前 app-server 的模型刷新 worker 会周期性 online refresh。第 48 章提到，它用 cancellation token 和 weak manager 管理生命周期。

## 43. Jitter 防止同时过期

如果 10,000 个客户端都按固定整点刷新，TTL 会把流量集中成尖峰。

可以加入 jitter：

```text
effective_ttl = base_ttl ± random_window
```

或让后台 refresh interval 带随机偏移。

Jitter 不解决单实例内的并发 miss，仍需 singleflight；singleflight 也不解决所有实例同时刷新，仍需 jitter、服务端限流或分布式协调。

## 44. Cache observability 应记录什么

建议按 cache name 和受控维度记录：

- hit / miss / stale / error；
- load duration；
- refresh success/failure；
- entries 和估算字节数；
- eviction count；
- singleflight leader/waiter 数；
- stale age；
- invalidation reason；
- generation discard count；
- backend request avoided count。

不要把 account ID、token 或无限多的原始 key 直接做 metric label，否则会泄密并造成高基数。

## 45. 缓存测试应覆盖哪些不变量

至少覆盖：

1. 正确 key 命中；
2. 关键维度变化后 miss；
3. TTL 边界前后；
4. schema/client version 不匹配；
5. cache read/write error 的降级行为；
6. N 个并发 miss 只执行一次 load；
7. invalidate 与慢 load 并发时旧结果不能写回；
8. stale 是否按产品策略可用；
9. 容量达到上限后的 eviction；
10. 账号切换不复用旧身份数据。

不要只写“set 后 get 能取到”的测试，那只证明 Map 会工作。

## 46. 当前源码中的测试样例

可以重点阅读：

- `codex-rs/models-manager/src/manager_tests.rs`
  - fresh cache 避免远端请求；
  - stale cache 重新获取；
  - cache error 不破坏远端 refresh；
  - ETag 相同只刷新 TTL；
- `codex-rs/core-plugins/src/discoverable_tests.rs`
  - manifest 改变后，clear cache 才看到新值；
- `codex-rs/windows-sandbox-rs/src/setup.rs`
  - 相同 setup key 的并发请求只运行一次；
- `codex-rs/core/src/connectors_tests.rs`
  - cache key 与缓存读写；
- `codex-rs/core-plugins/src/remote/catalog_cache_tests.rs`
  - legacy/stale/schema 和身份 scope。

## 47. 常见错误

### 错误一：key 只有业务 ID

遗漏租户、账号、provider、配置版本，造成跨身份命中。

### 错误二：TTL 设得很短就认为一致

在 TTL 窗口内仍可能读旧值，并发击穿也没有解决。

### 错误三：clear 后允许旧 loader 写回

需要 generation/version fencing。

### 错误四：一把全局锁包住所有 key

避免重复的同时，也把互不相关的请求串行化。

### 错误五：永久缓存临时错误

后端恢复后，客户端仍持续返回旧失败。

### 错误六：缓存无界增长

性能优化最终变成内存泄漏。

### 错误七：把安全判断当普通展示数据缓存

旧权限和旧撤销状态可能造成越权。

## 48. 练习：设计一个 Tool Catalog 缓存

假设要缓存“当前用户可见工具列表”，请先写设计表：

| 项目 | 你的答案 |
|---|---|
| 权威来源 | 例如 MCP runtime + plugin config |
| cache key | 账号、workspace、配置 hash、provider…… |
| value | 原始目录还是已应用权限的 projection？ |
| fresh TTL | 多久内直接使用？ |
| stale policy | 断网时可否用旧值？最长多久？ |
| invalidation event | 登录变化、配置写入、MCP refresh…… |
| singleflight | per key 还是全局？ |
| fencing | generation 怎样递增？ |
| capacity | entry/byte 上限和淘汰方式 |
| tests | 哪些竞态必须可重复验证？ |

重点不是给出唯一答案，而是把隐含策略变成可评审契约。

## 49. 本章术语表

| 英文或代码名 | 中文理解 | 在代码里先问什么 |
|---|---|---|
| cache | 缓存 | 它是哪份权威状态的副本？ |
| cache key | 缓存键 | 是否包含全部影响结果的输入？ |
| cache entry | 缓存项 | 除 value 外还有哪些 freshness/version 元数据？ |
| hit / miss | 命中/未命中 | 未命中是不存在、过期、版本错还是错误？ |
| false hit | 错误命中 | 是否复用了别的身份或旧语义的数据？ |
| false miss | 错误未命中 | 是否因无关 key 维度失去复用？ |
| TTL | 生存时间 | 超过后是禁止使用，还是允许 stale？ |
| fresh / stale | 新鲜/陈旧 | 陈旧值的最坏后果是什么？ |
| SWR | 先用旧值并后台验证 | 谁启动并拥有刷新 task？ |
| ETag | 实体版本标签 | 它验证内容，还是用于查找本地 entry？ |
| invalidation | 失效 | 由时间、事件还是版本触发？ |
| generation fencing | 世代栅栏 | clear 前启动的 load 能否写回？ |
| singleflight | 同 key 在途请求合并 | leader 失败或取消时 waiter 怎么办？ |
| cache stampede | 缓存击穿/惊群 | 并发 miss 是否同时冲击权威源？ |
| negative caching | 负结果缓存 | 错误是否稳定，TTL 是否更短？ |
| eviction | 淘汰 | 容量按数量还是字节限制？ |
| cache-aside | 旁路缓存 | 谁在 miss 后加载并回填？ |
| read-through | 穿透式读取 | 缓存层是否隐藏权威读取？ |
| write-through | 同步写穿 | 写入是否同步更新缓存和权威源？ |
| write-back | 延迟回写 | 崩溃前未落地的数据怎么办？ |
| warming | 预热 | 谁控制启动流量和生命周期？ |
| jitter | 随机抖动 | 是否防止大量实例同时刷新？ |
| Prompt Caching | Prompt 前缀计算复用 | 不要与业务数据缓存混淆 |

代码单词可以这样拆：

- `load_fresh_file`：只从文件返回仍 fresh 的 entry；
- `refresh_ttl`：不改变 payload，只延长已验证内容的新鲜期；
- `OnlineIfUncached`：先尝试可用缓存，miss 后联网；
- `cache_entry_if_current`：只有加载世代仍是当前世代才写入；
- `cache_refresh_needed`：当前结果可以使用，但还需要后台更新；
- `recommended_plugins_refreshes`：推荐插件当前正在进行的刷新集合；
- `prompt_cache_key_override`：覆盖默认 Prompt 缓存分组键。

## 50. 本章小结

设计缓存时，不要从 `HashMap` 开始，而应先写出协议：

1. **身份**：key 精确包含哪些依赖维度？
2. **权威**：缓存是哪份事实的副本？
3. **新鲜度**：fresh、stale、hard expiry 怎样划分？
4. **并发**：miss 是否 singleflight，失效是否有 generation fencing？
5. **降级**：缓存故障、权威源故障时返回旧值、绕过还是失败？
6. **边界**：容量、TTL、后台 task 和 metric cardinality 是否有上限？

最值得记住的一句话是：

> 缓存命中只是性能事件；命中了正确身份、正确版本、可接受新鲜度的数据，才是正确性事件。

下一次看到 `OnceCell`、`Mutex<Option<Cached...>>`、`fetched_at` 或 `clear_cache()`，请继续追踪 key、权威来源、并发 miss 和失效竞态，而不是只看容器类型。
