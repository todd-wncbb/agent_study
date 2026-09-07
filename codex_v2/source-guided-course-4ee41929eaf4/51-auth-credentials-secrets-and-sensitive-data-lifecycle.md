# 51：认证凭据、密钥与敏感数据生命周期——秘密不是“藏起来”就结束了

> 源码基线：`4ee41929eaf4`
>
> OpenAI 公开文档会展示通过环境变量向 API 客户端提供密钥的常见方式，但没有公开 Codex 当前本地凭据存储、刷新锁和日志脱敏的全部实现。本章的 Codex 行为以当前仓库源码与测试为准；通用安全建议不是对任意部署环境的绝对保证。
>
> 官方对照资料：[OpenAI API Authentication](https://developers.openai.com/api/reference/overview#authentication)、[Codex 环境变量中的认证与网络](https://learn.chatgpt.com/docs/config-file/environment-variables#authentication-and-network)。

## 1. 本章解决什么问题

认证代码最容易被误解成一句话：

```text
拿到 token，然后放进 Authorization 请求头。
```

真正的系统还必须回答：

- token 从环境变量、标准输入、文件、系统 keyring，还是外部命令取得？
- 多种来源同时存在时，谁优先？
- 写入 `auth.json` 的权限是怎样的？这算不算加密？
- access token 过期时，谁负责刷新？并发请求会不会重复刷新？
- 新 refresh token 返回后，旧值是否被替换并持久化？
- logout 是只删本地文件，还是也撤销服务端 token？
- 错误、URL、诊断报告、Telemetry 和 `Debug` 会不会把秘密带出去？
- secret 是否被无意传给 shell、MCP server、远程 executor 或子进程？

本章的核心认识是：

> 凭据安全是一条从取得、存储、加载、使用、刷新到撤销和删除的完整生命周期；任何一次多余复制、传播或记录，都可能扩大泄漏面。

## 2. 先用“酒店房卡”理解凭据生命周期

住酒店时，前台确认你的身份后给你一张房卡：

- 身份证用于证明“你是谁”；
- 房卡用于进入被授权的房间；
- 房卡有有效期；
- 丢卡后应立即作废旧卡并补发新卡；
- 退房时交回或销毁本地房卡，还要让门锁系统不再接受它；
- 保洁不应该因为需要打扫大厅，就拿到你的房卡。

对应到程序：

| 酒店概念 | 程序概念 |
|---|---|
| 核验身份证 | authentication，认证身份 |
| 房卡能开哪些门 | authorization，授权范围 |
| 房卡 | access token / API key |
| 补发新卡 | refresh / rotation |
| 门锁端作废 | remote revocation |
| 从钱包取出并交给门锁 | 从存储加载并加入请求头 |
| 保洁只拿工作区钥匙 | least privilege，最小权限 |

这个类比最重要的一点是：安全不只发生在“把房卡放进保险箱”这一刻。

## 3. Authentication 和 Authorization 不一样

两个词经常一起出现，但问题不同：

- **Authentication（认证）**：你是谁，或这个调用方是谁？
- **Authorization（授权）**：已经认出你以后，你被允许做什么？

一个 token 能被服务器接受，只说明认证材料有效；它不表示调用方可以访问所有账号、项目或工具。

源码里的 `forced_chatgpt_workspace_id` 检查就是例子：即使凭据本身可解析，账号不在允许的 workspace 范围中，仍应拒绝。

因此读认证代码时要分两次问：

1. 这个凭据如何证明身份？
2. 这个身份还受到哪些 workspace、scope、policy 或资源限制？

## 4. Credential、Secret、Token、Key 的关系

这些词有重叠，但可以先这样区分：

| 词 | 初学者理解 |
|---|---|
| credential | 用来证明身份或权限的一整类凭据 |
| secret | 泄漏后可能造成损害、必须限制读取的数据 |
| API key | 通常较长期、由调用方直接持有的秘密字符串 |
| access token | 用于实际请求、通常有有效期的访问令牌 |
| refresh token | 用于换取新 access token 的高价值令牌 |
| private key | 用于签名或证明持有者身份的私钥材料 |
| session cookie | 浏览器会话凭据，也可能承担认证作用 |

不是所有敏感数据都是认证凭据。例如用户邮件、任务内容和内部 URL 可能也需要保护；但本章重点是“能代表调用方获得权限”的材料。

## 5. 先画出完整生命周期

```text
用户或外部提供方
       │
       ▼
取得：浏览器 OAuth / stdin / env / provider command
       │
       ▼
选择来源与校验账号范围
       │
       ▼
存储：file / keyring / encrypted secrets / ephemeral
       │
       ▼
加载到进程内缓存
       │
       ▼
最小范围加入目标请求
       │
       ├── 临近过期或 401 ──► 刷新、替换、持久化
       │
       └── logout ──► 远端撤销（尽力）+ 本地删除 + 清缓存
```

横跨整条链路的保护包括：

- 不把值放进日志和 Telemetry；
- 不把值放进命令行参数或配置展示；
- 不向无关子进程和远端环境传播；
- 测试不仅检查“正确值被使用”，还检查“秘密没有出现在其他输出中”。

## 6. Codex 当前有哪些认证材料

`codex-rs/login/src/auth/storage.rs` 中的 `AuthDotJson` 能保存多种材料：

- `OPENAI_API_KEY`；
- ChatGPT `tokens`；
- `personal_access_token`；
- `agent_identity`；
- `bedrock_api_key`；
- `last_refresh` 等辅助状态。

`agent_identity` 又可能是：

- 一个 JWT 字符串；或
- 包含 `agent_private_key`、账号和 runtime 身份信息的 record。

这说明 `auth.json` 不是普通偏好设置。只要其中存在真实认证材料，整个文件都应按 secret container 看待。

## 7. Access Token 和 Refresh Token 的职责不同

典型 OAuth 流程中：

- access token 交给业务 API，证明本次请求有权访问；
- refresh token 交给认证服务，换取新的 access token。

可以把它们想成：

```text
access token  = 短期房卡
refresh token = 能去前台补办新房卡的凭证
```

因此 refresh token 往往更值得保护。攻击者拿到过期很快的 access token，窗口可能有限；拿到仍有效的 refresh token，则可能持续换取新 token。

不要因为 refresh token 不出现在普通 API 请求里，就把它当成“次要字段”。

## 8. ID Token 不等于业务 API 的 Access Token

`id_token` 常用来携带身份声明，例如用户、账号或计划类型；`access_token` 才通常用于访问业务 API。

当前 `TokenData` 会保存解析后的 ID token claims，同时保存 access 和 refresh token。读代码时不要看到三个字段都叫 token，就认为它们可以互换。

判断方法是追踪消费者：

- 哪个 token 被解析 claims？
- 哪个 token 进入 `Authorization: Bearer ...`？
- 哪个 token 被发送到 `/oauth/token`？
- logout 优先撤销哪一个？

用途决定安全边界，名字只是提示。

## 9. 凭据从哪里进入 Codex

当前源码存在多条入口：

- 浏览器 OAuth 登录后，由本地 callback server 收到 code，再交换 tokens；
- `codex login --with-api-key` 从 stdin 读取 API key；
- `codex login --with-access-token` 从 stdin 读取 access token；
- `OPENAI_API_KEY`、`CODEX_API_KEY`、`CODEX_ACCESS_TOKEN` 等环境变量；
- 配置的外部 bearer provider command，从 stdout 返回 token；
- 外部客户端直接提供受管理的 headers 或 ChatGPT token。

这些入口的信任边界不同。审计时不能只搜索 `auth.json`，还要搜索环境变量读取、stdin、外部命令、进程间 API 和 header 注入。

## 10. 为什么 stdin 通常比命令行参数更合适

Codex CLI 要求 `--with-api-key` 的值从 stdin 进入：

```bash
printenv OPENAI_API_KEY | codex login --with-api-key
```

而不是：

```bash
codex login --api-key sk-real-secret
```

后者可能把秘密暴露给：

- shell history；
- 进程参数查看工具；
- 命令审计；
- 崩溃报告或调试输出。

stdin 不是万能保险，但它避免把秘密直接写成 argv。示例命令的 history 只含环境变量名，不含变量值。

## 11. stdin 仍然有边界

即使从 stdin 读取，也要继续问：

- 上游命令会不会把值打印到终端？
- pipeline 是否经过会记录输入的工具？
- 终端处于交互模式时是否误把秘密回显？
- 读取后的 Rust `String` 会被复制几次？
- 错误信息是否拼接了原始输入？

当前 `read_stdin_secret` 会区分 terminal 与 pipe，并拒绝空输入。安全收益主要是避开 argv，不代表内存、终端和上游来源自动安全。

## 12. 环境变量是“注入方式”，不是秘密保险箱

OpenAI 官方 API 示例常从 `os.environ` 读取 key，这体现了一种重要原则：让部署环境提供 secret，不把真实值硬编码进源码。

但环境变量仍可能被：

- 同权限进程或诊断工具读取；
- 无意继承给子进程；
- 打进环境 dump；
- 写进 shell profile、CI 配置或截图；
- 在容器、远程 executor 边界上错误传播。

所以正确结论不是“env 很安全”，而是：

> 环境变量能把代码与 secret value 分离，但仍需要控制注入、继承、展示和生命周期。

## 13. 变量名和变量值必须分开处理

下面两条信息的敏感度不同：

```text
OPENAI_API_KEY is present
OPENAI_API_KEY = sk-real-secret
```

第一条通常只泄露“配置了某种认证”；第二条直接泄露凭据。

`auth_env_telemetry.rs` 只记录：

- 某个变量是否存在；
- provider 是否配置了 env key；
- 相关功能是否启用。

它不记录实际 secret value，甚至把 provider key name 概括成 `configured`。这是 data minimization：诊断只收集回答问题所必需的最少信息。

## 14. 多个来源同时存在时，优先级也是安全策略

`load_auth` 的主要顺序可以概括为：

1. 允许时读取特定 API key 环境来源；
2. 检查进程内 ephemeral 外部 ChatGPT auth；
3. 检查 access token 环境来源；
4. 如果配置为 `Ephemeral`，到此停止；
5. 再读取 file/keyring/auto 持久存储。

这不是单纯便利性。若旧账号的持久凭据意外覆盖调用方刚注入的新凭据，可能造成跨账号访问；反过来，环境变量无意覆盖持久登录也可能造成惊讶。

因此优先级必须：

- 可预测；
- 有测试；
- 与允许的 login methods 和 workspace 限制一起校验；
- 在诊断中只显示来源/存在性，不显示值。

## 15. `AuthCredentialsStoreMode` 是存储策略

当前 `create_auth_storage` 支持四种模式：

| 模式 | 含义 |
|---|---|
| `File` | 保存到 `$CODEX_HOME/auth.json` |
| `Keyring` | 使用系统 keyring 或 secrets 后端 |
| `Auto` | 优先 keyring，失败时回退文件 |
| `Ephemeral` | 只保存在当前进程的内存 map 中 |

调用方面对的是统一的 `AuthStorageBackend`：

```rust
trait AuthStorageBackend {
    fn load(&self) -> io::Result<Option<AuthDotJson>>;
    fn save(&self, auth: &AuthDotJson) -> io::Result<()>;
    fn delete(&self) -> io::Result<bool>;
}
```

抽象的价值是：认证管理逻辑可以谈 load/save/delete，而不必在每个刷新路径重复判断平台存储。

## 16. File 模式做了什么

`FileAuthStorage::save` 会：

1. 创建父目录；
2. 把 `AuthDotJson` 序列化为 pretty JSON；
3. 以 truncate/write/create 打开文件；
4. Unix 上在创建文件时请求 mode `0o600`；
5. 写入并 `flush`。

`0o600` 表示：

```text
owner: read + write
group: no permission
others: no permission
```

这能减少同一机器其他账号直接读取文件的机会。还要注意 `OpenOptions::mode` 主要影响新建文件；若旧文件已经具有过宽权限，不能只凭这行代码假设它一定被自动收紧，仍应通过测试或实际文件 metadata 验证。

## 17. `0600` 不是加密

这是本章最容易混淆的一点。

文件权限回答：

> 哪些 OS 用户身份可以打开这个文件？

加密回答：

> 即使拿到了存储字节，没有解密能力能否理解内容？

`auth.json` 的 pretty JSON 仍是明文。`0600` 不能防御：

- 当前用户权限下运行的恶意程序；
- 已获得账号或磁盘访问的攻击者；
- 被错误复制的备份；
- 主机管理员或更高权限主体；
- 应用自己把内容写入日志。

所以文档应准确说“权限受限的文件存储”，不能写成“凭据已加密”。

## 18. Keyring 模式提供什么边界

`DirectKeyringAuthStorage` 将序列化后的认证数据交给 `KeyringStore`。不同操作系统的具体后端可能不同，但总体意图是使用系统凭据设施，而不是把明文 JSON 留在普通文件中。

它使用：

- service：`Codex Auth`；
- account/key：由 `codex_home` 计算出的稳定短 key。

保存成功后，它会尽力删除旧的文件 fallback；删除时也同时清理 keyring 和文件。

这体现了迁移时的重要原则：新位置写成功以后，旧副本不能永久遗留。

## 19. 为什么 store key 包含 `codex_home`

`compute_store_key` 会：

1. 尽可能 canonicalize `codex_home`；
2. 对路径字符串做 SHA-256；
3. 取前 16 个十六进制字符；
4. 形成 `cli|...` key。

目的不是加密认证数据，而是让不同 Codex home 使用不同的稳定 keyring entry，同时避免把任意长路径直接作为 key。

注意：hash 在这里主要用于命名和隔离，不是把 secret 加密。不要看到 SHA-256 就自动得出“安全存储已经完成”。

## 20. Secrets 后端和 Direct Keyring 的区别

当 `SecretAuthStorage` feature 启用时，配置会选择 `AuthKeyringBackendKind::Secrets`，由 `SecretsManager` 使用 `LocalSecretsNamespace::CodexAuth` 和全局 secret name `CODEX_AUTH` 管理序列化认证数据。

未启用时使用 `Direct` keyring 后端。

源码的错误文字把 secrets 路径称为 `encrypted auth storage`，但读者仍要区分：

- Codex 选择了哪个抽象后端；
- 后端在当前 OS 上如何保护数据；
- 解密能力与当前用户会话如何绑定；
- 备份、同步和解锁策略是什么。

不要从一个类型名推导出所有平台的完整威胁模型。

## 21. `Auto` 模式为什么有回退

`AutoAuthStorage` 的行为是：

- load：先读 keyring；没有值或读取失败时读文件；
- save：先写 keyring；失败时写文件；
- delete：通过 keyring storage 同时清理可能的文件副本。

这样提高可用性：系统 keyring 不可用时，用户仍可能登录。

但回退也是安全决策：原本期望 keyring 的用户可能最后落到权限受限但明文的文件。因此诊断和文档应让用户知道实际采用了什么存储，而不是只显示配置意图。

## 22. `Ephemeral` 到底保证什么

`EphemeralAuthStorage` 把 `AuthDotJson` 放进进程内的全局 `HashMap`，按 `codex_home` 派生 key 隔离。

它保证的是：

- 不通过这个后端写入持久文件或 keyring；
- 进程结束后 map 随进程消失；
- 同进程内不同 manager 可以共享这份外部 auth。

它不保证：

- 内存被密码学清零；
- crash dump、swap 或调试器绝对看不到；
- clone 过的 `String` 立即消失；
- 其他有当前进程读取能力的主体无法访问。

“只在内存”比“永不泄漏”弱得多。

## 23. 进程内缓存为什么存在

`AuthManager` 会保存当前 `CodexAuth`，请求不需要每次都访问磁盘或 keyring。

收益包括：

- 降低读取延迟；
- 统一当前账号和 auth mode；
- 允许请求恢复逻辑比较刷新前后的 auth；
- 让订阅者通过 generation/watch 感知认证变化。

代价是秘密在内存中的驻留时间变长，而且 clone 可能产生额外副本。设计时要让缓存 owner、更新点和清除点明确，不能只关注持久存储。

## 24. Debug 输出也是泄漏通道

Rust 开发中很容易写：

```rust
tracing::debug!(?auth);
```

如果类型自动派生 `Debug`，内部 token 可能被完整打印。

当前 `AuthHeaders` 自定义 `Debug`：

```text
AuthHeaders { headers: "<redacted>", .. }
```

`SecretsKeyringAuthStorage` 的 `Debug` 只显示 `codex_home`，并使用 `finish_non_exhaustive()` 隐去其余字段。

安全类型的 `Debug` 应默认无害；不能把“调用者记得别打印”当成唯一防线。

## 25. 从 Auth 到 HTTP 请求头

业务请求最终通常需要：

```http
Authorization: Bearer <access-token-or-api-key>
```

ChatGPT 账号请求还可能带账号 ID 等路由信息。`CodexAuth::get_token()` 会按认证 variant 选择可作为 bearer 的值；某些模式如 headers、agent identity 或 Bedrock 并不暴露普通 Codex bearer token。

关键安全原则是：

- 只在即将调用对应服务时构造 header；
- 不把完整 header 放进普通日志；
- 不把 OpenAI 凭据复用给不相关 host；
- redirect 时重新检查目标 host 和 HTTP 客户端行为；
- 错误对象、trace 和测试快照都要考虑 header 是否可见。

## 26. “能取得 token”不等于“应传播 token”

进程内许多组件都能传递字符串，但 secret 应遵循 need-to-know：

```text
Auth storage
    ↓
AuthManager
    ↓
目标 API client
```

不应自然扩散成：

```text
AuthManager
 ├─ shell environment
 ├─ arbitrary MCP server
 ├─ remote executor
 ├─ user-visible event
 ├─ rollout history
 └─ telemetry attribute
```

每多一条边，就多一个日志、崩溃、权限和供应链风险面。传播必须由目标功能明确需要，而不是因为“反正当前进程里已经有”。

## 27. 子进程环境是重要的传播边界

启动 shell 或工具时，子进程可能继承父进程环境。如果父进程含 `OPENAI_API_KEY`，一个本来只需读文件的工具也可能意外拿到它。

审计执行代码时，要找：

- 环境是 `inherit all`、allowlist，还是显式构造？
- 哪些变量被移除？
- 用户配置的 env policy 是否在 local 和 remote 都执行？
- command error 会不会打印整个环境？
- 远端执行时，secret 在 control plane 解析还是 executor 侧解析？

不要假设 sandbox 自动隐藏环境变量。文件系统限制、网络限制和 secret filtering 是不同控制层。

## 28. MCP 为什么推荐保存“环境变量名”

HTTP MCP 配置使用：

```toml
[mcp_servers.example]
url = "https://example.test/mcp"
bearer_token_env_var = "EXAMPLE_MCP_TOKEN"
```

这里配置保存的是变量名，不是 token value。`RawMcpServerConfig` 中旧的直接 `bearer_token` 字段甚至被 schema 跳过，配置编辑逻辑会提示使用 `bearer_token_env_var`。

好处是：

- 配置文件可以提交或分享而不包含真实 token；
- CLI 展示配置时只需显示变量名；
- secret rotation 不要求改配置结构。

但运行时仍要在正确执行位置解析该变量。

## 29. `env_http_headers` 也是间接引用

MCP 还允许把 header name 映射到环境变量名：

```toml
env_http_headers = { "X-Token" = "TOKEN_ENV" }
```

`build_default_headers` 在连接建立时读取 `TOKEN_ENV` 的值并构造 header。错误日志会提到变量名和 header 名，而不是打印读到的值。

相比把真实值写进 `http_headers`，间接引用更适合 secret；但仍需检查：

- 变量在 local 还是 remote executor 上存在；
- header 被发往哪个 URL；
- URL 或配置是否可被不可信内容修改；
- 连接诊断是否会 dump headers。

## 30. 执行位置决定 secret 应在哪里解析

如果 HTTP MCP 由远端 executor 拥有，控制端不一定应该读取远端 secret，再把值跨网络传过去。

源码中的 plugin 配置会拒绝某些无法在 executor 侧解析 `bearer_token_env_var` / `env_http_headers` 的组合，并明确提示需要 executor-side environment resolution。

这体现了 placement-aware secret handling：

> secret 应尽量在真正使用它的信任域中解析，不要为了方便先拉到控制平面，再转发到数据平面。

这与第 43 章的 host、target、placement 模型直接相连。

## 31. 外部 Bearer Provider Command

`BearerTokenRefresher` 可以执行配置好的 provider command，从 stdout 读取 access token：

```text
run command
  ├─ stdin = null
  ├─ stdout = piped
  ├─ stderr = piped
  ├─ timeout
  └─ kill_on_drop
```

成功时，stdout 必须是非空 UTF-8，trim 后作为 token。它会在内存中缓存，并按可选 refresh interval 重新获取。

这种设计适合云凭据 helper 或企业认证代理，但 provider command 本身成为高信任组件：它能取得 secret，其 stdout 是协议，stderr 可能进入错误消息。

## 32. 外部命令的 stderr 也可能泄密

当前 provider command 失败时，错误会包含 trim 后的 stderr。

这对诊断有帮助，但意味着 provider 实现必须遵守：

- 失败时不要把 token 输出到 stderr；
- stdout 只输出 token，不混入说明文字；
- 不在 command path/args 中放 secret；
- 错误日志只给安全的原因；
- helper 自己也要限制日志和缓存。

安全不能只由消费方完成。生产 secret 的 provider 和接收 secret 的 Codex 共同构成边界。

## 33. 为什么刷新需要 Singleflight

设 access token 刚过期，20 个请求同时收到 401。如果每个请求都拿 refresh token 去换新 token，可能出现：

- 认证服务瞬时放大 20 倍流量；
- refresh token 是一次性或轮换式时，只有第一个成功；
- 后返回的旧结果覆盖较新的 token；
- 多个文件写入互相覆盖；
- 用户看到随机的 reused/invalidated 错误。

`AuthManager` 使用只有一个 permit 的 `Semaphore` 作为 `refresh_lock`。这相当于同一 auth manager 的刷新 singleflight：一个执行者刷新，其他调用者等待后重新观察状态。

## 34. 刷新前为什么要 guarded reload

取得刷新锁后，`refresh_token()` 不是立刻请求认证服务，而是：

1. 保存当前缓存 auth；
2. 按预期 account ID 从活动来源 reload；
3. 如果 auth 已改变，说明别的进程或来源可能已经刷新，跳过本次请求；
4. 只有 auth 没变时才调用 token authority。

这解决的是跨 manager 或跨进程协调的一部分。单个进程内的 semaphore 不能阻止另一个进程已经写入新凭据，因此必须在发刷新请求前重新读取权威来源。

## 35. 为什么要比较账号身份

如果用户在刷新等待期间退出 A 账号并登录 B 账号，旧刷新任务绝不能把 A 的新 token 写回并覆盖 B。

源码会记录预期 account ID，并在 reload 时检查；账号不匹配会返回要求重新登录的错误。

这是一种 identity fencing：不仅问“凭据字符串是否变了”，还问“当前 owner 是否仍是同一身份”。

它和缓存 chapter 中的 generation fencing、分布式一致性 chapter 中的 ownership token 属于同一类思想。

## 36. 主动刷新和 401 恢复

刷新可能由两类信号触发：

- **proactive refresh**：JWT 过期时间进入刷新窗口，或没有可解析 expiry 时 `last_refresh` 已太旧；
- **reactive recovery**：请求收到 Unauthorized，再按认证模式尝试恢复。

主动刷新减少用户请求撞上过期 token 的概率；401 恢复则处理时钟偏差、服务端提前失效和不可预测撤销。

两者必须共享一致的缓存与刷新协调，不能各自维护一套 token 副本。

## 37. API Key 为什么通常不刷新

`refresh_token()` 遇到 API key 或 personal access token 会直接返回成功，不执行 OAuth refresh。

因为这些凭据的生命周期通常由签发方和用户管理：

- 到期后重新注入；
- 泄漏后撤销并创建新值；
- 更新环境变量、secret manager 或登录存储。

“不自动刷新”不等于“永久有效”。只是 Codex 没有对应 refresh protocol。

## 38. 持久化新 Token 是刷新事务的一部分

认证服务返回后，`persist_tokens` 会：

- 更新返回的 ID token；
- 更新 access token；
- 如果返回了新 refresh token，也替换旧值；
- 更新 `last_refresh`；
- 保存完整 `AuthDotJson`；
- manager 再 reload，让缓存观察到新值。

refresh token rotation 的要点是：新值返回后不能继续依赖旧值。如果只更新内存、不更新持久存储，进程重启会重新拿旧 token；如果只写磁盘、不更新缓存，当前请求还可能继续使用旧 access token。

## 39. 永久失败和瞬时失败要分开

刷新错误可分为：

- transient：网络、暂时服务失败等，稍后可能成功；
- permanent：refresh token expired、reused、invalidated 等，需要重新登录。

`AuthManager` 会把永久失败与“尝试时的 auth snapshot”绑定缓存。后续若还是同一凭据，可以快速失败，避免不停冲击认证服务；一旦 auth 变化，就清除旧失败。

错误缓存的 key 必须包含身份版本，否则用户重新登录后仍可能被旧失败阻塞。

## 40. Logout 包含两个不同动作

真正退出登录包含：

1. **remote revocation**：让认证服务不再接受相关 token；
2. **local deletion**：删除当前设备的 file、keyring、secrets 和内存缓存。

两者解决不同威胁：

- 只删本地：设备不再使用，但已经复制走的 token 可能仍有效；
- 只远端撤销：服务端不接受，但本地仍残留敏感数据和错误状态。

因此不能把 `remove_file(auth.json)` 当作完整注销定义。

## 41. 为什么撤销失败仍要删除本地凭据

`logout_with_revoke` 的策略是 best effort：

- 尝试远端撤销；
- 失败时记录 warning；
- 仍继续删除 ephemeral 与当前配置的 managed storage，以及该后端负责的 fallback 副本；
- `AuthManager` 方法还会清除 external auth 并 reload 缓存。

如果把本地删除依赖于网络撤销成功，那么断网时用户反而无法退出当前设备。

正确的用户沟通应是：本地退出已经完成；若远端撤销失败，可能还需要在账号安全页面或管理端撤销凭据。不要把两种结果混成一个布尔值。

## 42. 为什么优先撤销 Refresh Token

`revoke_auth_tokens` 对 managed ChatGPT auth：

1. refresh token 非空时撤销 refresh token；
2. 否则退回撤销 access token；
3. refresh token 请求还包含 OAuth client ID；
4. 请求有 10 秒 timeout。

优先 refresh token 是因为它通常能继续生成未来的 access token。撤销它更接近切断长期续期能力。

但具体服务端是否连带撤销 token family，必须依据认证服务契约，不能仅从客户端代码推断。

## 43. OAuth Callback URL 为什么特别危险

OAuth callback 和 token endpoint 附近常见敏感字段：

- `code`；
- `code_verifier`；
- `state`；
- `access_token`；
- `refresh_token`；
- `client_secret`；
- URL userinfo、query 和 fragment。

网络错误对象常自动携带完整 URL；开发者如果直接 `%error` 记录，就可能把 query secret 长期写进日志。

所以“代码没有显式 log token”仍不够，还要检查第三方错误类型、URL 的 `Display` 和 debug formatting。

## 44. Codex 如何对 URL 脱敏

`server.rs` 定义敏感 query key 列表，并在日志前：

- 清除 username；
- 清除 password；
- 删除 fragment；
- 把敏感 query value 改成 `<redacted>`；
- 保留 host、path 和安全 query 的形状，便于诊断。

示意：

```text
输入：https://user:pass@example.test/callback?code=abc&redirect_uri=http...
日志：https://example.test/callback?code=%3Credacted%3E&redirect_uri=http...
```

脱敏的目标不是把整条 URL 删掉，而是在“可排障”和“不落 secret”间保留最小安全结构。

## 45. Doctor 报告为什么需要二次脱敏

诊断工具会汇集大量配置和错误，天然是 secret 聚合器。`doctor/output.rs` 的 `redact_detail` 会：

- 对包含 token、secret、authorization 等 label 的 detail 隐去值；
- 保留 `present/absent/true/false` 这类存在性信息；
- 删除 URL userinfo、query、fragment；
- 对较深 URL path 用 `<redacted>` 收缩。

测试明确断言 secret 不出现在最终 rendered report。

这类测试比单纯检查“输出包含 `<redacted>`”更强，因为真正的不变量是敏感原文不存在。

## 46. 测试 Secret 的正确方式

推荐同时测试正面和负面断言：

```rust
assert!(request_has_authorization_header());
assert!(!rendered_report.contains("sk-test-secret"));
```

应覆盖：

1. API key stdin 登录正确保存；
2. 文件模式权限符合平台约定；
3. keyring 保存成功后旧文件被删除；
4. auto 后端失败时按预期回退；
5. 环境来源优先级不串账号；
6. 并发 401 只触发受控刷新；
7. 新 refresh token 被持久化；
8. logout 撤销 refresh token 并清本地；
9. 撤销超时仍清本地；
10. URL、Debug、Doctor、Telemetry 不含 canary secret。

测试 token 必须明显是假值，不能把真实 key 复制进 fixture 或 snapshot。

## 47. 泄漏后的响应步骤

如果真实 secret 已进入 commit、日志、聊天、CI artifact 或截图：

1. 先撤销或轮换，不要先花很久清理历史；
2. 确认泄漏的是 access、refresh、API key 还是 private key；
3. 查明它能访问的账号、scope、环境和有效期；
4. 搜索日志、artifact、fork、cache 和备份中的副本；
5. 清理可清理的副本，但不要把删除历史误当成撤销；
6. 审计泄漏窗口内的异常调用；
7. 修复产生泄漏的路径并加入负面回归测试；
8. 必要时通知安全团队和受影响 owner。

最重要的顺序是：先让凭据失效，再处理痕迹。

## 48. 常见错误与理解检查

### 常见错误

- 把 `0600` 写成“加密存储”；
- 把 secret 直接放在 CLI argv；
- 诊断时打印完整环境或 header map；
- 认为变量名和变量值同样敏感，结果为了隐藏变量名而失去诊断能力；
- 把 OpenAI token 自动传给所有工具或 MCP server；
- 多个 401 并发刷新同一个 refresh token；
- 更新 access token，却忘记保存轮换后的 refresh token；
- remote revoke 失败就拒绝本地 logout；
- 从文件删除凭据后宣称它已在服务端失效；
- 测试只检查 `<redacted>` 存在，不检查原秘密不存在。

### 理解检查

请尝试独立回答：

1. 为什么 stdin 比 argv 好，但仍不是完整 secret manager？
2. `File`、`Keyring`、`Auto`、`Ephemeral` 分别保护什么？
3. 为什么 `Auto` fallback 同时是可用性和安全性决策？
4. access token 与 refresh token 谁用于什么？
5. 为什么刷新锁后还要 reload 和比较账号？
6. logout 的本地删除与远端撤销有什么不同？
7. 为什么日志不能只依靠调用者“记得别打印”？
8. MCP 为什么存 env var name，而不是 token value？

## 49. 本章术语表

| 英文或代码名 | 中文理解 | 在代码里先问什么 |
|---|---|---|
| authentication | 身份认证 | 调用方怎样证明“我是谁”？ |
| authorization | 权限授权 | 已认证身份可以操作什么？ |
| credential | 凭据 | 它能代表哪个主体获得什么权限？ |
| secret | 秘密数据 | 泄漏后能造成什么影响？ |
| API key | API 密钥 | 谁签发、在哪存储、怎样轮换？ |
| access token | 访问令牌 | 发给哪个服务、何时过期？ |
| refresh token | 刷新令牌 | 谁能用它换取新 access token？ |
| ID token | 身份令牌 | 它携带哪些 claims，是否被当成 API token 误用？ |
| private key | 私钥 | 谁能读取，是否用于签名身份？ |
| claim | 令牌声明 | 是服务端验证的事实，还是未验证 JSON？ |
| scope | 权限范围 | token 被限制到哪些动作和资源？ |
| least privilege | 最小权限 | 当前组件是否拿到了超出需要的 secret？ |
| `AuthDotJson` | 认证存储结构 | 哪些字段是真实认证材料？ |
| `AuthStorageBackend` | 认证存储后端接口 | load/save/delete 的真实落点在哪里？ |
| `AuthCredentialsStoreMode` | 凭据存储模式 | File/Keyring/Auto/Ephemeral 哪个生效？ |
| keyring | 系统凭据库 | 当前 OS 后端怎样保护和解锁 entry？ |
| ephemeral | 短暂内存存储 | 是否真的没有持久副本，内存何时释放？ |
| `0o600` | owner 可读写的 Unix 权限 | 它限制访问，但是否误写成加密？ |
| encryption at rest | 静态加密 | 拿到存储字节后还需要什么解密能力？ |
| environment variable | 环境变量 | 值会继承给哪些进程和执行位置？ |
| stdin | 标准输入 | 是否避免 argv，又是否会被上游或终端记录？ |
| argv | 进程参数向量 | secret 是否出现在进程列表或 shell history？ |
| bearer | 持有者令牌 | 是否“谁持有谁可用”，因此必须防复制？ |
| `Authorization` header | HTTP 认证头 | 只发给哪个可信 host？ |
| redaction | 脱敏 | 原始 secret 是否真的从输出中消失？ |
| data minimization | 数据最小化 | 排障究竟只需要值、名字，还是存在性？ |
| rotation | 轮换 | 新凭据生效后旧凭据如何失效？ |
| proactive refresh | 主动刷新 | 根据 expiry/last_refresh 何时提前更新？ |
| unauthorized recovery | 未授权恢复 | 401 后哪些 auth mode 可以刷新？ |
| singleflight | 合并同类在途工作 | 并发刷新是否只让一个 leader 请求 authority？ |
| guarded reload | 受保护的重新加载 | 刷新前是否发现其他 owner 已更新凭据？ |
| identity fencing | 身份栅栏 | 旧账号刷新能否覆盖新账号登录？ |
| transient failure | 瞬时失败 | 稍后重试是否可能成功？ |
| permanent failure | 永久失败 | 是否必须重新登录或轮换？ |
| revocation | 服务端撤销 | 凭据是否在 authority 端失效？ |
| local deletion | 本地删除 | ephemeral、当前 managed store、其 fallback 和 cache 是否都清理？ |
| best effort | 尽力而为 | 某一步失败是否仍要继续安全清理？ |
| `bearer_token_env_var` | bearer token 的环境变量名 | 配置保存的是名字还是秘密值？ |
| `env_http_headers` | 从环境读取 HTTP header | 值在 local 还是 executor 侧解析？ |
| provider command | 外部凭据提供命令 | stdout/stderr/timeout/cache 的协议是什么？ |
| secret canary | 测试用秘密哨兵值 | 输出中是否明确断言它不存在？ |

代码单词可以这样拆：

- `read_openai_api_key_from_env`：从环境读取非空 OpenAI API key；
- `create_auth_storage`：根据 store mode 构造统一认证存储后端；
- `compute_store_key`：从 Codex home 派生稳定 keyring entry 名；
- `refresh_token_from_authority`：向签发凭据的权威服务请求刷新；
- `refresh_and_persist_chatgpt_token`：刷新、保存新 token，再让缓存重新加载；
- `record_permanent_refresh_failure_if_unchanged`：仅当当前凭据仍是失败时那一份时缓存永久错误；
- `revoke_auth_tokens`：远端尽力撤销可撤销的 managed ChatGPT token；
- `logout_all_stores`：清理 ephemeral 与当前配置的 managed store；具体后端再清理它负责的 fallback；
- `redact_sensitive_url_parts`：保留 URL 结构但去除凭据、fragment 和敏感 query value；
- `collect_auth_env_telemetry`：只收集认证环境的存在性元数据。

## 50. 本章小结

读 secret 相关代码时，沿七个问题检查：

1. **取得**：秘密从浏览器、stdin、env、文件还是外部命令进入？
2. **选择**：多来源优先级和账号限制是什么？
3. **存储**：是明文权限文件、系统 keyring、secrets 后端还是内存？
4. **传播**：哪些 HTTP client、子进程、MCP 和 executor 真正需要它？
5. **使用**：在何处构造 header，日志和错误是否默认脱敏？
6. **更新**：过期与 401 怎样触发 singleflight 刷新和 token rotation？
7. **终止**：logout 是否同时考虑远端撤销、本地多存储删除和缓存清除？

最值得记住的一句话是：

> Secret 的安全性不由“它最初放在哪里”单独决定，而由它一生中被谁读取、复制、发送、记录、刷新、撤销和遗忘共同决定。

看到 `api_key`、`token`、`Authorization`、`env`、`Debug` 或 `logout` 时，不要只追正常返回值；继续追踪每一份副本的 owner、信任域、有效期和删除点。
