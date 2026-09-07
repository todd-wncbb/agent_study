# Walkthrough：Voice Dictation 如何从麦克风流式转写，并安全合并进 Prompt

> 场景：用户在 Agent Prompt 中按下 Ctrl+Space 开始讲话。Pager 第一次使用时才启动 Voice pipeline，同时打开麦克风并连接 STT WebSocket；连接期间已经录到的 PCM 不能丢失。服务端不断返回 interim transcript，UI 只把它作为预览；`speech_final` 到达后才追加到 Prompt。用户可能在 final 到达前按 Esc 停止、按 Enter 发送、切换 Agent、关闭功能或失去连接，系统必须保证热麦克风及时关闭、尾随结果不会落到错误输入框、未提交 interim 不会悄悄丢失或重复。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
/voice / Ctrl+Space / F8
  -> voice availability + tier + surface gates
  -> bind VoiceTarget at start
  -> VoiceState::ColdStart（first use）
  -> event loop lazily spawns voice pipeline
  -> VoiceCommand::PttPress
  -> in parallel:
       mic open/capture -> PCM backlog
       fresh bearer -> WSS connect -> transcript.created
  -> flush backlog, stream PCM binary frames
  -> STT partial events
       is_final=false      -> interim preview
       is_final=true       -> locked preview prefix
       speech_final=true   -> UtteranceFinal
  -> append final to bound Prompt

Stop
  -> PttRelease -> stop mic -> close PCM -> audio.done
  -> VoiceState::Stopping keeps target for trailing final

Submit
  -> promote remaining interim into bound Prompt
  -> hard reset target/state
  -> send ordinary Prompt; voice never auto-sends
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| Voice 总览 | `xai-grok-pager/src/voice/mod.rs` | layering、行为约定 |
| Pager 状态机 | `xai-grok-pager/src/app/app_view.rs` | `VoiceState`、`VoiceTarget` |
| 启停 Dispatch | `xai-grok-pager/src/app/dispatch/voice.rs` | enable、toggle、stop、submit |
| 键盘与 Lazy Spawn | `xai-grok-pager/src/app/event_loop.rs` | chord、pipeline、event arm |
| Prompt 合并 | `xai-grok-pager/src/voice/handle.rs` | interim/final/error |
| Pipeline | `xai-grok-voice/src/pipeline.rs` | command、capture、backlog、watchdog |
| STT WebSocket | `xai-grok-voice/src/stt/streaming.rs` | connect、writer、reader |
| 麦克风 Capture | `xai-grok-voice/src/audio/capture.rs` | cpal、PCM、resample |
| macOS Helper | `xai-grok-voice/src/audio/capture_subprocess.rs` | self-exec、handshake、fallback |
| 认证桥接 | `xai-grok-pager/src/voice/auth.rs` | refreshing bearer adapter |
| 配置与 TLS | `xai-grok-voice/src/config.rs` | endpoint、language、sample rate |

## 3. Voice 是 Pager-owned 功能

录音、STT 连接、临时转写和 Prompt 合并都由 Pager 与 `xai-grok-voice` 管理；它不是一次 Shell Agent Tool Call，也不进入模型 sampling loop。

## 4. Dictation 只编辑 Prompt

语音 final 与键盘输入一样成为编辑器文字。用户仍需显式按 Enter；系统不会因为检测到停顿或收到 final 就自动发送。

## 5. 为什么“不自动发送”是重要边界

STT 可能误识别、断句或缺少标点。把结果先放进 Draft，让用户有机会检查，避免语音识别直接触发代码修改或命令执行。

## 6. 功能 Gate 默认开启

Voice availability 的默认值为 true，但可由 policy、环境、本地/managed config 和 remote flag 控制。

## 7. Gate 优先级

```text
requirements.toml
  > GROK_VOICE_MODE
  > effective [features].voice_mode
  > remote voice_mode_enabled
  > default true
```

更高层可作为组织策略或本地开发开关。

## 8. API Key 对 Remote-only Off 的例外

若唯一关闭来源是 remote setting，API-key 用户会强制开启；requirements、环境或 config 的 false 仍然获胜。

## 9. 编译能力也是 Gate

`xai_grok_voice::AUDIO_SUPPORTED` 表明当前 build 是否包含音频 capture。没有音频支持时 Voice surface 静默不可用。

## 10. Subscription Tier 另行限制

Free/X Basic 个人用户会进入 SuperGrok upsell，而不是启动注定被服务端零额度拒绝的 Session。Keybinding 绕过 Slash registry，因此 dispatch 还要再次执行 tier gate。

## 11. Voice Shortcut 有独立开关

`[ui].voice_keybind_enabled=false` 只关闭 Ctrl+Space/F8；`/voice` 仍可用。它避免用户快捷键冲突，却不禁用整个功能。

## 12. Session Voice Mode 不是持久配置

`voice_ui_active` 表示本次 CLI 运行已进入 Voice UI，会显示麦克风状态；`/voice` 不直接写 `config.toml`。

## 13. Glossary（一）：功能与 Gate

- **Voice dictation**：把用户语音转为可编辑 Prompt 文本的功能。
- **STT**：Speech-to-Text，语音转文字服务。
- **Feature gate**：决定功能是否可见和可启动的开关。
- **Kill switch**：远程或策略层快速关闭功能的开关。
- **Tier gate**：依据订阅等级限制功能的准入规则。
- **Session mode**：仅在当前进程/会话 UI 中生效、不一定持久化的状态。
- **AUDIO_SUPPORTED**：当前编译产物是否具备麦克风 capture 后端。

## 14. `/voice` 与键盘入口

Slash command、Ctrl+Space、F8 都能启动；Esc、再次按 toggle chord、录音行 `[stop]` 可停止。Enter 停止并发送。

## 15. 为什么提供 F8

某些系统占用 Ctrl+Space，例如 macOS 输入法切换。F8 是不带 modifier 的备用 chord。

## 16. Capture Mode 有 Toggle 与 Hold

`[ui].voice_capture_mode` 可选 toggle 或 hold。Toggle 是按一次开始、再按一次结束；Hold 是按住录音、释放停止。

## 17. Hold 依赖 Key Release Event

只有支持 Kitty keyboard protocol release report 的终端才能可靠 hold-to-talk；其他终端自动退化为 toggle。

## 18. Release 为什么按 Key Code 宽松识别

用户可能先释放 Ctrl，再释放 Space，第二个 event 已没有 CONTROL modifier。持有中的 Voice Session 已证明 ownership，因此 release 只需匹配 Space/F8 key code。

## 19. Bare Release 为什么不会吞掉普通输入

只有 `hold_owned=true` 时 release 才由 Voice 拦截；普通 Space release 会继续走正常键盘路由。

## 20. Hold Ownership 防止误停 Toggle Session

`/voice` 或 toggle 启动时 `hold=false`。之后偶然收到 Ctrl+Space release，不能停止一个并非该 hold press 所拥有的录音。

## 21. 快速 Tap 在 Cold Start 期间也能取消

Hold press 可能尚在建立 pipeline；release 发现 `ColdStart { hold:true }` 会 hard reset，避免连接完成后才突然打开热麦克风。

## 22. 重复 Press 不接管已有 Session

已经 recording 时，新 start 不会重写 target 或 hold ownership。状态所有权必须由最初启动动作保持。

## 23. Esc 的优先级高于 Agent Cancel

Voice listening 或 pending cold start 时，Esc 先停止/取消 Voice，不会误触发清 Prompt、取消 Turn 或关闭其他 Agent surface。

## 24. Glossary（二）：键盘语义

- **Toggle**：同一按键交替开始和停止。
- **Hold-to-talk**：按下期间录音，释放即停止。
- **Key release report**：终端协议显式报告按键释放事件的能力。
- **Ownership**：哪一次 press 有资格让对应 release 改变 Session。
- **Chord**：按键与 modifier 的组合，如 Ctrl+Space。
- **Cold-start cancellation**：后台组件尚未就绪时撤销启动请求。
- **Input priority**：同一个按键可能有多个含义时的路由先后顺序。

## 25. Voice 只能绑定可见 Prompt Box

合法目标包括 active Agent Prompt、Dashboard new-agent dispatch，以及 Dashboard 当前 top-level peek reply。

## 26. Welcome Screen 的特殊路径

首次启动没有 Prompt box。若认证和 folder trust 允许，Voice dispatch 先走普通 New Session 创建流程，再绑定新 Agent Prompt。

## 27. Startup Gate 不能绕过

若登录或目录信任仍 pending，Voice 在 Welcome 上静默 no-op，不会为方便录音而绕过正常安全启动门。

## 28. Dashboard Popup 会阻止绑定

Attached-agent popup 遮住输入框时不能启动录音，否则用户看不到 interim，final 也像“消失”一样落到隐藏 Draft。

## 29. `VoiceTarget` 在开始时捕获

它不是 final 到达时根据 active view 临时计算。这样停止后延迟到达的 final 仍知道属于哪个 Prompt。

## 30. 三种 Target

```text
VoiceTarget::Agent(agent_id)
VoiceTarget::DashboardDispatch
VoiceTarget::DashboardPeekReply(agent_id)
```

Peek Reply 带 Agent id，因为多个 row 共用同一个 reply widget。

## 31. 为什么 Peek Reply 必须固定 Row

用户切到另一行时 widget 会清空重用。若只记“Dashboard Peek”，旧录音 final 可能被错误发送给新选中的 Agent。

## 32. 导航离开会 Hard Reset

Event loop 每轮调用 `enforce_voice_session_bound`。Active surface 与 bound target 不再一致时，立即关闭 capture 并忘记 target。

## 33. 为什么导航离开不保留 Trailing Final

目标输入框已不再可见或已换身份，落入旧 target 会让用户难以发现；落入新 target 更危险。导航被定义为放弃当前 dictation。

## 34. Agent 被关闭时 Final 安全 No-op

Target 仍带旧 id，但 `append_voice_text_to_prompt` 找不到 Agent 就返回，不会把文字转移给当前 Agent。

## 35. Glossary（三）：Target Binding

- **VoiceTarget**：一次录音绑定的具体 Prompt box 身份。
- **Target binding**：开始时固定目标，后续事件不随 active view 漂移。
- **Active surface**：当前屏幕上真正可见、可交互的输入区域。
- **Dashboard dispatch**：Dashboard 用于创建新 Agent 的输入框。
- **Peek reply**：Dashboard 针对某一 top-level Agent 的快速回复框。
- **Session-bound UI**：只在目标 Session/row 仍处于同一可见上下文时有效的 UI。
- **Hard reset**：关闭 capture 并丢弃 target/interim，不接受 trailing final。

## 36. `VoiceState` 集中拥有全部生命周期事实

旧设计若用多个 bool 表示 listening、pending、hold、target、interim，容易出现互相矛盾。枚举让每一时刻只能属于一个合法组合。

## 37. `Idle`

没有录音、没有待启动 pipeline，也没有可接收 final 的 target。

## 38. `ColdStart`

用户已请求开始，但 lazy pipeline 尚未存在；保存 hold ownership 和 target，等待 event loop 创建 channels/task。

## 39. `Recording`

逻辑上麦克风已开始，保存 hold、target 和可选 interim。UI 可以显示录音 banner 与 partial text。

## 40. `Stopping`

用户已显式释放麦克风，但仍保存 target 与最后 interim，允许 STT 在 `audio.done` 后发送 trailing final。

## 41. Stop 与 Reset 的本质差异

Stop 是“结束音频但等最终识别”；Reset 是“整个 Session 作废”。这是 Voice 中最重要的状态边界。

## 42. `voice_stop_keeping_final`

只从 Recording 生效，发送 `PttRelease`，取走 interim，进入 Stopping。远程 gate 即使此时关闭也不能阻止 stop，热麦克风必须始终可关。

## 43. `voice_reset`

若仍 listening 先发 `PttRelease`，随后无条件设 Idle。Submit、Error、Kill switch、导航离开使用这条路径。

## 44. Late Interim 为什么不会复活 Overlay

`voice_set_interim` 只接受 Recording 状态。Stop 后到达的 interim 对 Stopping 是 no-op。

## 45. Late Final 为什么仍能落地

`voice_recording_target` 对 Recording 和 Stopping 都返回 target。Final handler 清 interim并向绑定 Prompt append。

## 46. Stopping 不必立刻变 Idle

Pipeline 没有为每次 utterance 单独发“全部结束”UI event；保留 target 让尾随 final 可达。下一次开始或 hard reset 会覆盖/清除它。

## 47. Glossary（四）：状态机

- **State machine**：用有限状态及显式迁移描述生命周期。
- **ColdStart**：用户已启动、依赖组件尚在创建的状态。
- **Recording**：当前接受音频和 interim 的状态。
- **Stopping**：音频已停止、仍允许最终结果落地的状态。
- **Interim**：可变化的临时转写预览。
- **Trailing final**：停止音频后由服务端完成处理再返回的最终转写。
- **Hot mic**：仍在采集音频的麦克风 Session。

## 48. Pipeline 为什么 Lazy Spawn

未使用 Voice 的用户不应承担 WebSocket、channel、音频库初始化和后台 task 成本。第一次 `/voice` 或 chord 才进入 ColdStart。

## 49. Event Loop 创建两组 Channel

Pager 到 pipeline 使用容量 32 的 command channel；pipeline 到 Pager 使用容量 128 的 VoiceEvent channel。

## 50. Pipeline Task 是长生命周期的

它持续接收多次 `PttPress/PttRelease`，每次录音建立新的 capture/STT Session；只有 `Shutdown` 或 channel 结束才整体退出。

## 51. Pipeline Ready 后立即 Begin Recording

Event loop保存 auth、command sender 和 event receiver，再把 ColdStart 的 target/hold 传入 `voice_begin_recording`。

## 52. 启动期间 Surface 仍要复查

若 lazy spawn 时用户已离开 Agent/Dashboard，event loop 防御性地丢弃 ColdStart 并关闭 Voice UI，而不是打开无目标麦克风。

## 53. Command Send 是 Best-effort

Pager 用 `try_send`，channel full/closed 时记录 trace。UI loop不能因控制 Voice pipeline 而 await 阻塞。

## 54. `PttPress` 会 Supersede 正在 Drain 的旧 Session

快速 stop→start 时，旧 reader 可能仍等待 final。新 press abort 旧 reader，防止旧 final 落到新 target，也确保新麦克风真正启动。

## 55. Start 与 Release 发生竞态怎么办

Pipeline 用 `tokio::select!` race `open_session` 与下一 command。若 release 先到，尚未完成的 connect/capture start被取消。

## 56. 为什么 Select 使用 Biased Start-first

若 Session 恰好已完成，优先保留返回的 ActivePtt；否则把刚建立 reader丢掉会泄漏或失去控制句柄。

## 57. Shutdown 会 Abort Active Reader

进程结束不需要等待远端 final；abort 释放 capture 和 STT Session，确保后台任务不脱离生命周期。

## 58. Glossary（五）：Pipeline 并发

- **Lazy initialization**：第一次需要时才创建昂贵组件。
- **Command channel**：Pager 向 pipeline 发送开始、停止、关闭指令的队列。
- **Event channel**：pipeline 向 Pager 返回转写和错误的队列。
- **Supersede**：新 Session 明确取代并终止旧 Session。
- **Race**：多个异步事件谁先完成决定控制流。
- **Biased select**：多个分支同时 ready 时使用确定优先级的 select。
- **Abort**：立即取消异步 task，不等待正常完成协议。

## 59. Bearer 在每次连接时重新获取

Voice pipeline 持有 `VoiceAuthProvider`，而不是启动时冻结 token。OAuth/session token 会轮换，长运行 CLI 必须跟随刷新。

## 60. Pager 如何桥接 Shell AuthManager

`AuthManagerVoiceAuth` 适配 `SharedApiKeyProvider`，每次 `bearer()` 调用 `current_api_key_async()`，支持 OAuth、OIDC、API Key 与 BYOK。

## 61. Leader Mode 也能取得新 Token

其 AuthManager 可在文件锁下采用 agent 旋转后的 `auth.json` token；Voice 不需要单独维护第二套认证刷新器。

## 62. 没有 Bearer 时如何失败

返回明确 Auth error，提示登录或设置 key；pipeline转换为 `VoiceEvent::Error`，Pager清状态并展示消息。

## 63. Voice Endpoint 继承 Chat Endpoint

未设置 `[voice].api_base` 时，可继承 `[endpoints].xai_api_base_url`，企业代理不必配置两个重复 base。

## 64. Voice 拒绝 Plaintext Endpoint

`http://` 与 `ws://` 直接返回 Config error。Bearer 和实时音频不能通过明文连接发送。

## 65. HTTPS 如何变为 WSS

Config 去除 scheme/trailing slash，组合 `wss://.../v1/stt`；若 base 已以 `/v1` 结束，会去重 path 的 `v1/`。

## 66. 可选 Identity Header

握手除 Authorization 外可发 `x-grok-client-identifier` 与 User-Agent，用于归因；空值或非法 header 被跳过，不影响 Bearer 授权。

## 67. WebSocket Connect 最多 15 秒

TCP/TLS/WebSocket handshake 超时后返回错误，不能让一次 Voice start 永久停在 Connecting。

## 68. 还要等待 `transcript.created`

连接完成不等于 STT Session ready。Client 最多再等 10 秒收到 Created，之后才认为可以稳定发送音频。

## 69. Glossary（六）：认证与传输

- **Bearer token**：放在 Authorization header 中证明调用身份的凭据。
- **Refreshing provider**：每次请求动态取得可能已轮换 token 的提供者。
- **WSS**：使用 TLS 的 WebSocket 协议。
- **Handshake**：建立连接并协商协议的过程。
- **Session ready event**：服务端确认某个 STT transcript 已创建的事件。
- **Identity header**：用于客户端归因、并非授权必需的附加 header。
- **BYOK**：Bring Your Own Key，用户提供自己的模型/API key。

## 70. STT URL 包含哪些 Query

默认发送 sample_rate=16000、encoding=pcm、interim_results、language 和 endpointing milliseconds。

## 71. Language 默认 `en`

Voice 支持 Catalog 中的 25 种语言 code。UI setting 可覆盖 `[voice].language`。

## 72. `auto` 只存在客户端

STT API 不接受 `auto`。连接前根据 LC_ALL、LC_MESSAGES、LANG 解析为支持的具体 code，不支持则回退 `en`。

## 73. Locale Canonicalization

`en-US`、`pt_BR.UTF-8` 提取 primary subtag；Tagalog `tl` 映射为 API Catalog 的 Filipino `fil`；未知/空值回退 English。

## 74. Endpointing 默认约 400ms

它影响服务端判断 speech boundary 的灵敏度。Endpointing 产生 final，不代表 Pager自动发送 Prompt。

## 75. Interim Results 默认开启

它让用户在讲话时看到实时预览，同时最终提交仍等待更可靠的 speech final。

## 76. Glossary（七）：STT 配置

- **Sample rate**：每秒采集的音频样本数，默认 16kHz。
- **PCM**：未压缩脉冲编码音频数据，这里通过 binary frame 发送。
- **Endpointing**：服务端判定一段发言结束的静音/时序策略。
- **Interim result**：尚可能变化的实时识别文本。
- **Language code**：用于 STT 语言与数字/货币格式规范化的 Catalog code。
- **Locale**：操作系统语言区域标识，如 `pt_BR.UTF-8`。
- **ITN**：Inverse Text Normalization，将口述数字等转换为书写形式。

## 77. 麦克风与网络连接并行启动

`start_capture_session` 同时 spawn blocking 打开 mic，并获取 bearer/连接 STT。串行执行会把第一句话前几百毫秒切掉。

## 78. Mic 为什么先产生 Backlog

Capture 可能比 WebSocket ready 更早。`forward_pcm` 在拿到 live audio sender 前缓存 PCM chunks，连接成功后按原顺序 flush。

## 79. Backlog 有 1024 Chunk Hard Cap

正常 connect 远小于此；病态慢连接时丢最旧 chunk而不是无限增长内存。网络 connect timeout通常会更早终止。

## 80. Capture Channel 为什么不能被连接 Backpressure 卡住

实时音频 callback 若阻塞，会导致设备 buffer overflow和断音。独立 forwarder先持续 drain mic，再异步等待 socket sender。

## 81. Mic 在 Socket 前关闭怎么办

`mic_rx` 关闭说明用户已释放或 capture 失败，forwarder直接退出并丢弃 backlog，不会在无活跃录音后再建立发送链。

## 82. Socket Connect 失败怎么办

oneshot sender dropped，forwarder退出；capture handle随错误路径释放，不把音频留在无人消费的 channel。

## 83. Capture Device 优先原生 16kHz

若设备支持目标 rate，避免 resampling；否则使用默认 input config和线性 resampler，把各种 sample format 转为 mono PCM16。

## 84. Audio Callback 不做阻塞日志

Callback只尝试发送 chunk并累计 dropped counter；独立 polling loop约每秒报告 drop，避免实时线程因 I/O 或锁抖动。

## 85. Capture Start 有 Ready Handshake

设备、权限和 stream play 错误要在进入 steady loop 前返回给 caller，不能只在后台日志中悄悄失败。

## 86. Glossary（八）：音频采集

- **Audio callback**：音频设备实时交付 sample buffer 的函数。
- **Backpressure**：下游消费慢迫使上游等待的压力传播。
- **PCM backlog**：WebSocket ready 前暂存的音频 chunk 队列。
- **Resampling**：把设备原始 sample rate 转换为 STT 目标 rate。
- **Mono PCM16**：单声道、16-bit 整数编码的 PCM。
- **Real-time thread**：必须快速、稳定返回，不能执行阻塞工作的音频线程。
- **Ready handshake**：确认设备已成功打开并开始 capture 的同步信号。

## 87. macOS 为什么默认使用 Helper Process

CoreAudio 在长生命周期 Pager 中初始化后会永久增加数 MB 到数十 MB 内存。短命 self-exec child退出时，OS 可完整回收音频栈。

## 88. Helper 使用同一个 Executable

父进程启动隐藏 `__mic-capture --rate N` 子命令，复用同一个二进制和麦克风权限授权身份。

## 89. Helper 与 TTY 隔离

stdin 设 null、stdout/stderr pipe，并 detach controlling TTY，防止 PCM bytes 或 child 行为污染终端输入输出。

## 90. 一行 Protocol Header

Child 先输出 `READY device`、`INFO ...` 或 `ERR message` 加换行，然后才输出 raw PCM。Reader逐字节读到换行，避免吞掉第一段音频。

## 91. Header 有 5 秒 Timeout 与 4096 Bytes Cap

设备打开不能无限等；损坏 child 也不能无限喂无换行 header。失败会 kill、reap child并 join reader。

## 92. Broken 与 Reported Failure 不同

Spawn失败、旧 binary 不懂协议等 Broken failure可回退 in-process；working helper明确报告设备/权限失败时直接上报，重复 in-process只会再次失败。

## 93. 环境变量可强制 In-process

`GROK_VOICE_CAPTURE=inprocess` 是兼容/调试逃生口，代价是长期进程承担 CoreAudio footprint。

## 94. Linux 后端也可隔离 Recorder

平台 capture实现不同，但上层只依赖统一 `CaptureHandle::stop` 和 PCM channel，不把 OS 细节泄漏进 STT pipeline。

## 95. Glossary（九）：Capture Process

- **Self-exec helper**：当前程序以隐藏子命令重新启动自己执行隔离工作。
- **Controlling TTY**：进程接收终端输入和控制信号的关联终端。
- **Protocol header**：PCM 流之前用于声明 ready/error 的一行元数据。
- **Kill and reap**：终止子进程并回收其退出状态，避免 zombie。
- **Fallback**：首选 helper不可运行时采用 in-process实现。
- **Memory footprint**：进程持有的内存规模，包括库和系统音频 buffer。
- **Permission identity**：操作系统判断哪个 executable 获得麦克风授权的身份。

## 96. STT Writer 发送 Binary Frame

它从容量 64 的 audio channel 接收 PCM chunk并写入 WebSocket。Channel关闭后发送一条 `{"type":"audio.done"}` 文本消息。

## 97. Stop 顺序为什么是先 Mic 后 `audio.done`

Reader拥有 capture handle；收到 finish先 stop capture，让所有 audio sender clone 关闭，再调用 `finish_audio`。这样不会在 done 之后又排入 stray PCM。

## 98. STT Reader 解析四类事件

Created、Partial、Done、Error 映射为内部 `StreamingSttEvent`；未知 server event 被忽略，JSON parse failure 变为 Error。

## 99. 非 Text WebSocket Frame 被忽略

Close/Binary/Ping/Pong 不直接变用户错误；随后 stream None 正常结束。

## 100. 哪些 Disconnect 被视为 Benign

ConnectionClosed、AlreadyClosed 和无 closing handshake reset常见于正常结束，不弹“connection lost”Toast。

## 101. 其他 Transport Failure 会上报

真正的 WebSocket error产生 `VoiceEvent::Error`，Pager清状态并让用户看到短消息。

## 102. Drop 必须 Abort Reader/Writer Task

丢弃 Tokio JoinHandle 只会 detach，不会取消。`StreamingSttSession::drop` 显式 abort 两半，防止失败 setup留下 idle socket。

## 103. Glossary（十）：WebSocket Session

- **Binary frame**：WebSocket 中承载 PCM bytes 的二进制消息。
- **`audio.done`**：客户端声明本次音频输入结束的控制消息。
- **Writer task**：从 audio channel向 socket发送数据的异步任务。
- **Reader task**：解析服务端 transcript event 的异步任务。
- **Graceful close**：协议正常结束连接，不作为错误展示。
- **Benign disconnect**：结束 Session 时预期出现、无需打扰用户的断线形态。
- **Detached task**：失去 JoinHandle控制但仍继续运行的后台任务。

## 104. Partial Event 有两个 Final 维度

`is_final` 表示一个短识别 chunk 锁定；`speech_final` 表示整段 utterance 完成。两者不能当成同一个布尔含义。

## 105. 普通 Partial 直接显示 Interim

`is_final=false` 且还没有 locked prefix 时，当前 text成为 live preview，后续 event 可以覆盖它。

## 106. Chunk Final 为什么进入 `locked_prefix`

长时间不停顿讲话时，服务端可能每约数秒锁定一段 delta，但整个 speech尚未结束。Pipeline把这些 delta拼起来，避免 preview只显示最新片段。

## 107. Locked Prefix 仍然只是 Preview

它不会写进 Prompt。服务端在 `speech_final` 时会对整段做更干净的一次 final transcription，比客户端拼接 delta 更可靠。

## 108. Speech Final 产生 `UtteranceFinal`

Pipeline清 locked prefix，把完整文本交给 Pager。麦克风仍保持打开，用户可停顿后继续说下一段，直到显式 Stop。

## 109. Done Event 也可产生 Final

音频关闭后服务端可能用 Done携带最终文本；非空时同样映射为 `UtteranceFinal`。

## 110. 十秒无 Speech Watchdog

若开始后一直没有任何有效 transcript，pipeline停止 mic、发送 done并报“No speech was detected”，避免死麦克风无限 streaming。

## 111. 第一个有效 Transcript 解除 Watchdog

之后长 dictation中的自然停顿不受十秒启动 watchdog影响；它只检测“从未听到任何语音”。

## 112. Permission Denial 为什么可能表现为 No Speech

macOS 某些权限失败只返回静音而不是设备错误，因此 no-speech error附带麦克风权限修复提示。

## 113. Glossary（十一）：Interim 与 Final

- **Partial transcript**：STT 尚在更新的一段识别文本。
- **Chunk final**：局部 chunk 已锁定，但整段发言仍未结束。
- **Speech final**：整段 utterance 已完成的高质量最终转写。
- **Locked prefix**：由多个 chunk-final delta拼成的实时预览前缀。
- **Utterance**：一次自然连续发言片段。
- **Watchdog**：等待关键事件超时后主动清理的保护任务。
- **No-speech timeout**：录音开始后始终没有 transcript 的最长等待时间。

## 114. Voice Event 在 Select 中故意最低优先级

Event loop 使用 biased select，Voice arm放最后。热麦克风每秒可产生 5–20 个 interim，不能饿死 keyboard、ACP stream、取消、task completion或 render timer。

## 115. Event Channel Backlog 有界

容量 128 能吸收短 burst；低优先级消费保证核心交互优先，同时不会让 Voice event无限占内存。

## 116. Interim 只更新 Overlay

Pager收到 `InterimTranscript` 后调用 `voice_set_interim`。如果已经 Stop/Reset，event 被忽略，不写 TextArea。

## 117. Final 先清 Overlay

`UtteranceFinal` 清掉 interim，再把 trimmed非空文本追加到绑定 Prompt。录音仍可保持 Recording跨停顿继续。

## 118. Existing Prompt 与 Voice Fragment 如何连接

空白 Draft 被 replacement；已有文本若末尾是 whitespace直接拼接；否则中间插入一个空格。

## 119. Trailing Newline 会被保留

已有 `line one\n` 时，final `line two` 直接追加到新行，不额外压成空格。

## 120. Final 总是追加在 Prompt 末尾

语音 dictation不尝试插入当前 cursor中间，因为连续 final的目标是累积句子，且 STT没有编辑 range语义。

## 121. Cursor 何时跟随末尾

若原 Prompt 空白或 cursor已在末尾，append后 cursor移到新末尾；若用户正在中间编辑，文本仍追加到末尾，但保留原 cursor位置。

## 122. 为什么允许用户边录边编辑

Final append不会夺走中间 cursor，减少实时转写到达时对键盘编辑的打断。

## 123. Dashboard Peek Final 还会复查 Row

即使 target保存 Agent id，真正 append前仍检查当前 peek row是否相同；不相同就丢弃。

## 124. Voice Interim 占有 Ghost 区域

Prompt renderer显示 interim时跳过 shell completion 和 next-prompt ghost，避免三个预测文本叠在光标后。

## 125. 空 Draft 显示 Interim 时隐藏 Caret

当 TextArea尚空、interim作为唯一可见文字时，隐藏真实 caret，避免用户误以为 preview已经提交进编辑器。

## 126. Glossary（十二）：Prompt 合并

- **Overlay**：显示在编辑器上方/内部但尚未写入 TextArea 的临时内容。
- **Committed text**：已经成为 Prompt Draft 真值的文字。
- **Append-at-end**：无论当前 cursor在哪里，都把 final放到文本末尾。
- **Caret**：编辑器中显示插入位置的光标。
- **Cursor preservation**：异步 append后不改变用户的中间编辑位置。
- **Whitespace-aware join**：依据末尾空白决定是否额外插入一个空格。
- **Prompt ownership**：哪一个具体 PromptWidget 接收本次语音结果。

## 127. Enter Submit 必须先处理 Interim

用户可能在 speech final前按 Enter。若只 hard reset，屏幕上看得到的 interim会消失且不进入发送内容。

## 128. 键盘路径先做一次 Commit

`maybe_commit_voice_interim_before_submit_key` 识别真正 send key，把 interim追加到 Prompt；multiline mode只有 modifier+Enter算发送，普通 Enter只是换行。

## 129. Dispatch Funnel 再做一次保护

Mouse send、Dashboard dispatch、Interject等不一定经过相同 key path。`voice_stop_on_submit` 再尝试 commit interim，然后 hard reset。

## 130. 为什么需要 `merge_prompt_with_voice_interim`

某些 dispatch action在停止 Voice 前已 capture了旧 text参数。即使 interim刚写进真实 Prompt，旧参数仍缺它，因此返回 promoted fragment并合并进 payload。

## 131. 双层处理如何避免重复

Key path若已 commit，会清 interim；dispatch再调用得到 None。非 key path则由 dispatch commit并把 fragment合进早先 capture的 payload。

## 132. Submit 使用 Hard Reset

发送开始后 target清除，任何迟到 final都不能追加到已经清空、用于下一轮的新 Prompt。

## 133. Interject 也必须停止 Voice

Mid-turn interjection是一次真实发送边界；若继续录音，后续 final可能在发送后落入不明确的 Draft时代。

## 134. Voice 从不直接构造 ACP Audio Block

音频只发送给 STT，最终进入普通 Prompt的是文本。Agent模型不会收到麦克风 PCM，也无需原生 audio capability。

## 135. Glossary（十三）：Submit 一致性

- **Submit funnel**：所有发送方式最终共享的统一处理入口。
- **Promote interim**：把屏幕预览转为正式 Prompt text。
- **Captured payload**：dispatch前已经复制出来、可能落后于实际 widget的文本参数。
- **Double guard**：key path和dispatch path都防止 interim丢失，但通过清空状态避免重复。
- **Send boundary**：Draft 正式进入 Prompt/Interjection请求的时间点。
- **Multiline mode**：普通 Enter插入换行、组合键才发送的编辑模式。
- **Text-only handoff**：Voice只向 Agent传递转写文本，不传原始音频。

## 136. Error 会 Hard Reset

Capture、Auth、Config、STT 或 WebSocket错误都使 Pager忘记 target/interim，避免 UI仍显示 listening但 pipeline已经死亡。

## 137. 短错误显示 Toast

`Voice: {message}` 适合一行即时反馈。Dashboard dispatch没有 scrollback，因此只能使用 Toast。

## 138. 长修复提示进入 Scrollback

若 error带 hint且 target是 Agent或 Dashboard Peek Reply，可追加一条 System block，提供麦克风权限等步骤。

## 139. Pipeline Channel 关闭的处理

Event loop把 `voice_rx=None`，清 command sender并 reset状态；若当时正在 listening，显示“Voice stopped unexpectedly”。

## 140. 为什么 Receiver 关闭后改为 Pending Future

对已关闭 channel不断 `recv()` 会立即返回 None，造成 event loop hot loop。设为 None后 select arm使用永不 ready的 pending future。

## 141. Remote Gate 中途关闭

配置刷新发现 voice disabled时，先 `voice_reset()` 再关闭 UI和全局 gate。已打开麦克风必须被释放。

## 142. Stop 控制不依赖 Gate

即使 kill switch在录音中翻转，Toggle/Esc仍可 stop；“不允许开始”绝不能等价为“无法关闭”。

## 143. 重连 Auth 与 Voice Pipeline 相互隔离

每次 STT Session获取 fresh bearer，主 ACP重连不会把静态 token冻结在 Voice channel；但整个 Pager退出会随生命周期 shutdown。

## 144. Glossary（十四）：恢复与清理

- **Error convergence**：错误后统一回到不再录音、状态一致的终态。
- **Toast**：短暂的一行 UI 提示。
- **System block**：可在 Agent scrollback中保存较长诊断文字的块。
- **Hot loop**：异步 source持续立即 ready，导致 CPU空转。
- **Kill-switch transition**：功能从可用变不可用时对现有资源的清理过程。
- **Unexpected shutdown**：pipeline非用户预期地关闭 channel/task。
- **Recovery hint**：比错误摘要更长、指导用户修复环境的问题说明。

## 145. Stop、Submit、Navigate、Error 对照

| 事件 | Mic | State/Target | Interim | Trailing Final |
| --- | --- | --- | --- | --- |
| Esc/Toggle Stop | release | Stopping，保留 target | 保留到 final/下次动作 | 允许落地 |
| Hold Release | release | hold-owned才进入 Stopping | 保留 | 允许落地 |
| Enter/Send | release | Idle，忘记 target | 先 promote | 丢弃 |
| Navigate away | release | Idle，忘记 target | 丢弃 | 丢弃 |
| Error/Kill switch | release/reset | Idle | 丢弃 | 丢弃 |
| Speech pause | keep open | Recording | final后清 | 当前 final落地，可继续说 |

## 146. Voice 与其他输入能力对照

| 输入 | 编辑期表示 | 发送给 Agent | 自动发送 |
| --- | --- | --- | --- |
| Keyboard | TextArea text | TextContent | 否 |
| Image paste | atomic chip + bytes | ImageContent/转写 | 否 |
| Voice | interim overlay + committed text | TextContent | 否 |
| Next-prompt ghost | derived suffix | 接受后成为 text | 否 |
| Shell completion | ghost/dropdown token | Bash text | 否 |

## 147. 常见误解：收到 Speech Final 就会发送

不会。Final只写 Prompt；Enter仍是唯一显式提交意图之一。

## 148. 常见误解：Stop 与 Cancel 完全一样

不一样。普通 Stop保留 target等待 trailing final；Submit、导航和错误才是放弃所有迟到结果的 hard reset。

## 149. 常见误解：Final 应落到当前 Active Agent

错误。它必须落到开始录音时绑定的 target，否则导航竞态会把用户语音发送给错误 Session。

## 150. 常见误解：Interim 已经在 Prompt 里

通常没有。它是 overlay，只有 final或发送前 promote才进入 TextArea。

## 151. 常见误解：Voice 复用 Chat WebSocket

不会。它独立连接 `wss://.../v1/stt`，只是复用同一 AuthManager提供的 bearer和 endpoint配置理念。

## 152. 调试“按键没反应”的顺序

1. Voice feature gate 与 AUDIO_SUPPORTED；
2. subscription tier；
3. keybind enabled或改用 `/voice`；
4. 当前 terminal是否报告 release、capture mode如何降级；
5. active surface是否有可见 Prompt box；
6. Welcome auth/folder trust gate；
7. ColdStart是否被快速 release取消；
8. pipeline channel是否仍存在。

## 153. 调试“录音无文字”的顺序

1. capture helper READY/ERR 与 permission；
2. device sample format/rate与 dropped chunk日志；
3. bearer是否成功解析；
4. WSS URL/TLS/proxy；
5. connect和 transcript.created timeout；
6. PCM backlog是否交给 live sender；
7. server partial事件能否解析；
8. 十秒 no-speech watchdog是否触发；
9. VoiceState是否仍 Recording；
10. event-loop low-priority arm是否持续消费。

## 154. 调试“文字落错或丢失”的顺序

1. VoiceTarget在开始时是什么；
2. 是否切 Agent、Peek row或打开 popup触发 hard reset；
3. event是 Interim、Chunk Final、Speech Final还是 Done；
4. Stop后是否仍为 Stopping；
5. Submit是否先 promote interim；
6. captured payload是否通过 merge helper补回 fragment；
7. late final到达时 target是否已被 reset；
8. Prompt append时 cursor/whitespace规则是否符合预期。

## 155. 值得长期守住的测试不变量

- requirements/env/config/remote/default与 API-key remote-only例外正确；
- restricted tier不启动 hot mic；
- toggle release不停止非 hold-owned Session；
- quick hold release取消 ColdStart；
- target始终绑定开始时 Prompt/row；
- 导航离开 hard reset，stop保留 trailing final；
- late interim不能复活 Stopping/Idle overlay；
- submit前 interim恰好 promote一次；
- final追加空格、换行和 mid-text cursor规则稳定；
- mic与connect并行，backlog按序 flush且有界；
- stop先释放 mic再发送 audio.done；
- chunk finals只拼 preview，speech final才 commit；
- no-speech timeout释放设备并给 permission hint；
- plaintext endpoint永远拒绝 bearer；
- WebSocket task Drop会 abort，不留下 detached socket；
- Voice event arm不能饿死 ACP、键盘和取消。

## 156. 一句话记忆

Voice Dictation 是一条独立于 Agent sampling 的“麦克风到可编辑文本”管线：Pager先经策略、tier与surface gate绑定唯一 Prompt target，用枚举状态区分 ColdStart、Recording、Stopping和Idle；pipeline并行打开设备与带 fresh bearer 的 TLS STT连接，用有界 backlog保存开头音频，将 chunk final只作为预览、speech final才追加到绑定 Draft；普通 Stop允许尾随 final，而 Submit、导航和错误先处理或丢弃 interim后 hard reset，从而保证不自动发送、不留热麦克风，也不让迟到语音污染新的输入上下文。

## 157. 复习问题

1. Voice 为什么由 Pager管理，而不是作为 Agent Tool Call？
2. Feature gate、keybind gate、tier gate和 AUDIO_SUPPORTED分别控制什么？
3. Hold ownership为什么必须进入 VoiceState，而不能是独立 bool？
4. VoiceTarget为什么在开始时绑定，Dashboard Peek为什么还要存 Agent id？
5. Stop与Hard Reset对 trailing final的语义有什么区别？
6. Lazy pipeline如何处理 quick press/release竞态？
7. 为什么麦克风打开和WebSocket连接要并行，PCM backlog又如何保持顺序？
8. Capture helper的 Broken与Reported failure为何采用不同 fallback策略？
9. `is_final` 与 `speech_final` 有什么区别？
10. 为什么 locked prefix只能用于preview，不能直接commit Prompt？
11. 键盘pre-commit和dispatch merge如何共同保证interim不丢也不重复？
12. Voice event为什么放在biased select最后？

