# Walkthrough：一张图片如何从粘贴进入 Prompt、模型上下文与持久化历史

> 场景：用户把截图粘贴到 Prompt 中并问“这个报错怎么修？”。Pager 必须同时维护可编辑的 `[Image #1]` chip、预览状态和真实图片字节；发送时把它转换成 ACP ImageContent。Shell 不能直接信任 MIME、路径或尺寸，而要恢复可能丢失的附件、删除路径信息、验证并压缩图片。随后根据 harness 能力选择原生多模态输入，或先让视觉模型把图片转写为文本。图片还会进入 Session 资产目录、Conversation、Provider wire、重放和请求体预算管理。

本文基于源码版本 `ed6d543643628663873c5de28298e022ed634238`。

---

## 1. 最终调用链

```text
Clipboard / file path / drag-and-drop
  -> ImageData
  -> PastedImage
  -> persist_to_session(.../images/image-UUID.ext)
  -> PromptWidget::insert_image
     -> TextArea atomic [Image #N] chip
     -> PastedImage.element_id + display_number

Submit
  -> drain_images（只取仍有 live chip 的图片）
  -> build_content_blocks_with_workspace
     -> orphan placeholder recovery
     -> strip local paths
     -> load bytes + 50 MB client gate
     -> base64 ACP ImageContent + display-number meta
  -> ACP prompt
  -> parse_prompt_with_skills
     -> text/context/images 分离
  -> server orphan recovery（defence in depth）
  -> normalize_images_with_notices
     -> validate / transcode / downscale / compress / drop
  -> harness branch
     -> multimodal: persist assets + inline ContentPart::Image
     -> text-only: vision describe -> <image> text, no inline image
  -> ChatState Conversation
  -> Provider Chat Completions / Responses conversion

Long conversation
  -> request body near 50 MB
  -> oldest inline images replaced with explicit placeholders
  -> 413 / image processing error
  -> request-local strip_images + retry
```

## 2. 建议同时打开的源码

| 关注点 | 文件 | 关键符号 |
| --- | --- | --- |
| Pager 图片模型 | `xai-grok-pager-render/src/prompt_images.rs` | `PastedImage` |
| Prompt chip | `xai-grok-pager/src/views/prompt_widget/mod.rs` | `insert_image`、`drain_images` |
| 粘贴处理 | `xai-grok-pager/src/app/agent_view/paste.rs` | `handle_image_paste_from_data` |
| ACP block 构建 | `xai-grok-pager-render/src/prompt_images.rs` | `build_content_blocks_with_workspace` |
| 路径恢复安全 | `xai-grok-shared/src/placeholder_images.rs` | loader、allowlist、caps |
| Prompt 解析 | `xai-grok-shell/src/session/prompt_parser.rs` | `ParsedPrompt.images` |
| 图片规范化 | `xai-grok-shell/src/session/image_normalize.rs` | `normalize_images` |
| Turn 接入 | `xai-grok-shell/src/session/acp_session_impl/turn.rs` | `handle_prompt` 图片段 |
| 降级转写 | `xai-grok-shell/src/session/image_describe.rs` | describe pipeline |
| Tool 图片 | `.../tool_layer_images.rs`、`tool_calls.rs` | harvest + followup |
| 请求预算 | `xai-chat-state/src/actor/request_builder.rs` | image compaction |
| Provider 转换 | `xai-grok-sampling-types/src/conversation/*` | `ContentPart::Image` |
| 失败重试 | `xai-grok-sampler/src/actor/request_task.rs` | `RetryWithImageStrip` |

## 3. 一张图片有三种身份

| 层 | 表示 | 目的 |
| --- | --- | --- |
| 编辑器 | `[Image #N]` chip | 用户能移动、删除、撤销 |
| ACP | `ImageContent { data, mime_type, uri, meta }` | 跨进程传递图片 |
| Conversation | `ContentPart::Image { url }` | 发送给模型 Provider |

它们不是同一对象；编号、字节和显示文本必须显式关联。

## 4. 为什么文本里仍要有 `[Image #N]`

图片 part 只能告诉模型“有图片”，chip 还能表达图片在句子中的引用位置，例如“比较 `[Image #1]` 和 `[Image #2]`”。

## 5. PastedImage 保存什么

它同时保存 chip 的 `element_id`、显示编号、MIME、尺寸、字节长度、内存字节、原始路径、临时路径、Session 持久路径和预览状态。

这是 Pager 的编辑期对象，不是最终 wire DTO。

## 6. element_id 与 display_number 不同

`element_id` 标识 TextArea 中一个具体原子元素；`display_number` 是用户看到的 `#1`。前者解决内部绑定，后者解决跨 wire 和模型引用。

## 7. 为什么不能只按 Vec 位置配对

用户可以把第二张图插到文本开头，TextArea 元素顺序与图片插入时间立即分叉。若用 `zip`，两张图会互换。

## 8. 为什么也不能只按占位符长度

`[Image #1]` 与 `[Image #2]` 字节长度相同。长度不是身份；稳定配对必须依赖 element id，undo 恢复时才按不复用的 display number 回退。

## 9. 图片 chip 是原子元素

`insert_image` 调用 TextArea `insert_element`，让 chip 像一个不可拆散单位被移动或删除，而不是允许用户只删掉 `Image` 的几个字符造成半损坏 token。

## 10. 单 Prompt 图片上限

`PromptWidget::IMAGE_CAP` 当前为 10。第 11 张在 UI 层被拒绝并提示，而不是先全部编码后让后端承担内存压力。

## 11. 最小边长预检查

若已知尺寸且任一边小于 8 px，Pager 拒绝插入。Shell 后面仍会重复检查，因为其他 ACP 客户端不一定经过 Pager。

## 12. image_counter 是高水位

删除 `#2` 后再插图不会重新使用 `#2`；counter 在一个 Prompt 生命周期中单调增加，只有明确清空 Prompt 才重置。

这使 undo stash 按 display number 恢复时不产生歧义。

## 13. undo stash 为什么存在

用户删除 chip 后，图片记录暂存而非立即销毁；若随后 redo，TextArea 可能生成新 element id，系统还能用 display number 找回字节和元数据。

## 14. stash 为什么有上限

stash 最多 `IMAGE_CAP * 2`。长期编辑与反复删除不能让大图片记录无限积累；超出时优先淘汰更旧编号并清理临时文件。

## 15. drain_images 的关键保证

发送前先用 live TextArea element ids 重新 reconcile，再 `mem::take`。用户已经删除的 chip 不会仅因旧 `PastedImage` 仍在 Vec 中而偷偷发送。

## 16. 文本专用 surface 会移除 chip

权限反馈或 question 等不支持图片的输入路径使用 `text_without_image_chips`。这些 surface 不应把无对应附件的 `[Image #N]` 文本泄漏到 wire。

## 17. 图片预览与发送解耦

`PromptImagePreview` 通过共享 `OnceLock` 保存 Ready、Failed 或 Unsupported。终端不支持 Kitty/iTerm 图片显示，只影响预览，不代表图片不能提交给模型。

## 18. 预览准备为什么放到后台

图片解码、缩放或终端 payload 生成可能昂贵；`preview_preparation` 产生可异步执行的工作，绘制阶段只读取完成结果，避免阻塞 TUI render loop。

## 19. 粘贴后先落到 Session images 目录

若 Session id 已知，`persist_to_session` 把图片写到 Session 的 `images/`，随后释放 `encoded_bytes`，避免 Prompt 长时间持有大块内存。

## 20. 为什么保留 source_path

`source_path` 是用户最初看到/粘贴的路径，用于 UI；`session_image_path` 是发送与恢复的稳定副本。持久化不能篡改用户界面的来源说明。

## 21. Pager 的落盘是原子写

代码先写临时文件、`sync_all`，再 rename 到最终 UUID 路径。崩溃更可能留下可清理的 tmp，而不是一个名字正常但内容只有一半的图片。

## 22. 为什么文件名使用 UUID

相同图片粘贴两次仍是两个独立 attachment；UUID 避免覆盖和并发命名冲突，也不把用户原文件名当作可信路径组成。

## 23. 发送时字节从哪里取

`load_for_send` 优先读仍在内存的 `encoded_bytes`，否则读取 `session_image_path`。两者都不存在或磁盘读取失败时跳过该图片并记录 warning。

## 24. Pager 发送前的 50 MB gate

单张原始图片超过 50 MB 会被跳过。这个宽 gate 是客户端防护，不是最终模型预算；Shell 后面会把正常大图压到更低的目标。

## 25. ACP blocks 的顺序

构建结果始终以 Text block 开头，随后是仍可加载的 PastedImages，再是从 orphan placeholders 恢复的图片。

文字和结构化附件因此能独立处理。

## 26. Base64 为什么出现

JSON 不能直接携带任意二进制。Pager 把图片字节编码成 base64 放入 `ImageContent.data`；代价是 wire 大约膨胀到原始字节的 4/3。

## 27. MIME type 的作用

`image/png`、`image/jpeg` 等告诉接收者怎样解释字节和构造 data URL。但 MIME 字符串不能单独作为真实性依据，Shell 会检查真实格式和结构。

## 28. uri 与 data 的分工

ACP ImageContent 可同时带 inline `data` 和来源 `uri`。Pager 持久化成功后通常附 `file://...` URI；真正的模型上传仍以 inline bytes 为 canonical payload。

## 29. display-number meta

每个图片 block 的 `_meta.xai.dev/imageDisplayNumber` 保存其 `N`。服务端构建 AttachedImages resource 时按编号配对，而非假设图片数组位置永远等于 chip 编号。

## 30. orphan placeholder 是什么

文本可能含 `[Image #3: /path/to/a.png]`，但图片 Vec 中没有 `#3`：例如跨 Session 粘贴历史、reload、某次客户端状态丢失。它是“有引用、无附件记录”的孤儿占位符。

## 31. 为什么尝试从路径恢复

若完全删除 orphan，用户可能以为已附图；若只把路径发给模型，模型可能读不到或越过安全边界。受限恢复能重新构造真正的 ImageContent。

## 32. 恢复路径是用户控制输入

聊天文本可以伪造任意 `[Image #N: /etc/passwd]`。因此 placeholder loader 不能成为任意文件读取或数据外带通道。

## 33. canonicalization 防什么

候选路径先 canonicalize，解析 `..` 与 symlink。只检查原字符串前缀会被 `/workspace/link -> /secret` 绕过。

## 34. prefix allowlist 包含什么思路

允许 workspace cwd 和少量常见用户图片目录，而不是整个 home。实际集合由 `default_allowed_prefixes` 生成；测试可注入 hermetic prefixes。

## 35. 敏感子树 denylist

即使父目录允许，Photos Library、Trash、Keychains、`.ssh`、`.aws`、`.gnupg` 等已知敏感路径仍被拒绝。

## 36. 扩展名 allowlist

只允许 png、jpg/jpeg、gif、webp、bmp、tiff/tif。SVG 明确排除，因为它是可含脚本或外部引用的 XML 文本，不能用普通位图 header validation 获得相同信任。

## 37. 为什么还要 sniff 和 decoder

攻击者可以把任意文件改名为 `.png`，甚至伪造开头 magic bytes。loader 使用图片 reader 猜格式并读取尺寸，证明内容确实可作为受支持图片解析。

## 38. placeholder 恢复的资源上限

- 每张最多 50 MB；
- 每个 Prompt 最多检查 16 个 placeholder；
- 一次累计最多 200 MiB。

这些限制防止构造大量路径引发串行 I/O 与内存尖峰。

## 39. 客户端和服务端都做恢复

Pager 构建 blocks 时恢复一次，Shell `handle_prompt` 又用同一 shared helper 做 defence in depth。非 Pager 客户端或传输中的缺失仍不能绕过服务端规则。

## 40. 路径什么时候被删掉

必须先完成 orphan recovery，之后才把 `[Image #N: /path]` 改成 `[Image #N]`。顺序反过来会丢失恢复图片所需的路径。

## 41. 为什么模型不该看到本地路径

路径可能泄露用户名或目录结构，还会诱导模型再次调用 Read 读取已内联的附件。保留短 anchor 足以让文本和图片建立对应。

## 42. Shell Prompt parser 做什么

`parse_prompt_with_skills` 遍历 ACP blocks：Text 合并为 query，Image 克隆到 `ParsedPrompt.images`，ResourceLink 与 embedded Resource 进入文本 context，不支持的 block 直接 invalid params。

## 43. 图片与 Prompt 文本暂时分离

`ParsedPrompt` 保存 context、query、skill information、images 和 harness flag。这样文本可独立做 Skill 展开、文件引用、截断，而图片走二进制处理管线。

## 44. 原始用户 blocks 先用于 UI 与持久化

Turn 开头逐 block 发出/保存 UserMessageChunk，因此重放可以恢复文本与图片显示。之后的模型 normalization 是派生处理，不倒过来改写用户原始输入事件。

## 45. normalize_images 的输出不只是图片

`NormalizeResult` 包含 surviving images、compressed info、re-encode fallback notes 和 dropped reasons。调用方必须同时处理数据和用户/模型可见解释。

## 46. 第一关是合法 base64

解码失败立即成为 `Outcome::Failed`。无效编码不会被拼成 data URL 送到 Provider，避免一条坏历史持续触发 API 400。

## 47. 非原生格式会先转 PNG

`needs_endpoint_transcode` 识别后端不原生接受的图像；Shell 在 blocking worker 中转成 endpoint-compatible PNG，同时保留 ACP annotations/meta。

## 48. 为什么 CPU 工作用 blocking worker

完整解码、缩放和重编码是 CPU 密集操作。放在异步 executor 主线程上会阻塞 SessionActor 和其他 I/O task。

## 49. 结构完整性检查

即使 header 可读，截断的 PNG/JPEG 仍可能在 Provider 端失败。`image_structurally_complete` 在发送前验证尾部/结构完整性。

## 50. 服务端尺寸下限

任一边小于 8 px，或总像素少于 512，都会 drop。16×16 虽满足边长，却只有 256 px，仍会被拒绝。

## 51. 服务端像素上限

历史/外部 payload 超过后端 ceiling 不能直接发送。新图片正常会在更低的 encode pixel budget 下缩小；极端 decode 还受防 decompression-bomb 上限约束。

## 52. 新图的 1.5 MB 目标

decoded attachment 超过 1.5 MB，或尺寸超过 encode caps 时尝试重编码。低单图成本让更多图片能留在约 50 MB 请求体限制内，也减少频繁淘汰旧图造成的 KV-cache bust。

## 53. 默认尺寸目标

默认 harness 使用约 2,408,448 总像素预算和 2000 px 单边 clamp；Cursor 路径使用更严格的 1024 px 单边目标，为后续 captioning 降低成本。

## 54. 压缩参数如何逐步尝试

重编码使用 CatmullRom downscale 和一组递减 JPEG quality steps。目标同时考虑字节、边长与像素面积，而不是只调整 quality。

## 55. 为什么有 NormalizeCache

相同图片可能在重试、重放或不同路径重复规范化。cache 按原始字节和 harness variant 复用结果，避免再次做昂贵 decode/re-encode。

## 56. 重编码后更大怎么办

若结果不比原图小，保留原图；压缩不是为了形式上“处理过”，而是必须实际节省 payload。

## 57. 无法压到目标时怎么办

re-encode 失败或仍超目标时当前策略保留原附件，并生成 fallback notice。后续全请求预算和 413 retry 仍是最后保护层。

## 58. drop 不应该静默

损坏、过小等图片被 drop 时，Shell 把原因包进 system-reminder，并发送 `ImageDropped` notification。模型与用户都能知道缺失上下文。

## 59. compression notice 的作用

压缩可能损失细字或像素级细节。notice 写明原/新尺寸和字节数，让模型不要把模糊当作原图本身没有信息。

## 60. Multimodal harness 的正常路径

非 Cursor harness 会把规范化图片持久化到 Session `assets/`，在文本前加 `<image_files>` 绝对路径清单，同时把每张图作为 `ContentPart::Image` 加入 Conversation。

## 61. 为什么既 inline 又提供 assets path

Inline part 让模型视觉编码器直接看到图片；真实磁盘路径让代码 Agent 能用 Read、复制或进一步处理附件，而不是猜测不存在的 cloud attachment path。

## 62. images/ 与 assets/ 为什么都出现

Pager 的 `images/` 保存编辑/发送期间的客户端持久副本；Shell 的 `assets/` 保存归一化后、供模型工具访问的 Session 资产。它们属于不同生命周期阶段。

## 63. pick_user_image_url 的规则

有 inline data 时构造 `data:<mime>;base64,<data>`；只有 data 为空且 uri 是 HTTP(S) 时才直接转发远端 URL。`file://` 不能直接发给远端 API。

## 64. Provider wire 如何表达图片

Responses API 转成 `InputImage { image_url }`；Chat Completions 转成 image-url content block；Anthropic 风格对 data URI 拆出 media type 与 base64 source。

同一 Conversation 模型隔离了不同 Provider JSON 细节。

## 65. Cursor/text-only harness 的路径

该 harness 不把图片留在 coding model 的 Conversation 中，而是先调用 image-description model，将视觉内容转写成 `<image>` 文本，再把原 query 接在后面。

## 66. 为什么转写模型需要会话上下文

同一截图针对“颜色是否好看”和“这个 stack trace 怎么修”应生成不同描述。describe prompt 包含最近真实用户请求 outline 与当前 query，帮助视觉模型选择相关细节。

## 67. outline 的上限

最多取最近 5 条真实用户请求；每条最多 1500 字符，整体最多 4000 字符。当前 query 另有 12000 字符 cap。

这防止辅助视觉请求本身被长会话淹没。

## 68. 为什么排除 synthetic messages

outline 使用 real user queries，过滤 AutoContinue、Interjection 以外的运行时噪音等 synthetic 内容，避免把系统控制语句当成用户视觉意图。

## 69. 一轮最多转写多少图

`IMAGE_DESCRIPTION_PROCESSING_LIMIT` 为 16；超过时只描述最后 N 张，较旧图片用 `[skipped-due-to-limit]` 标记。

Pager 普通 Prompt 上限 10，但该服务端限制还保护其他客户端和复用路径。

## 70. 描述缓存的 key 为什么不只看图片

同一字节在不同当前问题/outline 下需要不同描述。cache 必须把 describe prompt fingerprint 纳入，否则会复用与本轮任务无关的 caption。

## 71. 视觉请求的采样约束

describe 请求温度为 0.2、最多 4096 output tokens，并有 240 秒 timeout。它是一次受控辅助推理，不继承主 Agent 的 Tool Loop。

## 72. 转写失败的产品语义

普通 Prompt 的 `transcribe_user_images` 返回 ACP error，整个 Turn 失败；它不会静默删除图片后让 coding model 猜测。Interjection 路径则记录 note 并丢图，因为它处于不同的 mid-turn 容错边界。

## 73. envelope 为什么要 scrub

视觉模型输出、路径、MIME 或错误文本会被插进 XML-like envelope。代码清理控制字符和伪造的 `<`/`>`，防止内容提前闭合 tag 或伪造结构。

## 74. Tool Result 也可能带图片

`read_file` 可返回 ImageContent，PDF 可返回 page images，MCP/browser 等工具结果还可能在文本或专用 `extracted_images` 字段中携带 base64 图片。

## 75. read_file 图片的 inline gate

Tool 图片先验证 base64、header、8 px 边长和 512 总像素。过小或 unreadable 时 Tool Result 变成明确文本说明，而不是附一个会毒化后续请求的 image part。

## 76. PDF page images 如何进入 Conversation

每页转换为 data URL，放进同一 ToolResult 的 images；文本记录已渲染页数和总页数。模型获得页面视觉内容，同时保留文件调用语义。

## 77. 为什么 Tool layer 要先 drain extracted_images

`DrainedToolSuccess` 从 ToolOutput 中 `mem::take` 多 MB 图片，再把无图片的 output 交给 PostToolUse Hook 序列化。否则 Hook JSON 会复制巨型 base64，且后续 harvest 可能重复附图。

## 78. Tool 图片在不同 harness 的分流

Multimodal harness 把提取图片规范化后作为独立 followup user image message；text-only harness 丢弃该 inline 分支，保留其已有文字/placeholder 语义。

## 79. 历史加载为何重新验证图片

旧版本、外部同步或损坏 JSONL 可能含无效 data URI。`strip_invalid_images` 在 load 时删除 API 会拒绝的 payload：User image 替换为 `[image removed — invalid data]`，Tool images 直接移除。

## 80. 为什么 HTTP(S) 历史图不在本地验证

本地没有其字节，无法做相同结构检查；函数只检查 data URI。远端 URL 的可用性最终由 Provider 获取行为决定。

## 81. load 时的 20 MiB cap

持久历史 data image 的 decoded sanity cap 是 20 MiB，比 fresh 1.5 MB target 宽松，目的在于恢复既有 Session，而不是强迫所有旧资产满足当前 ingestion 优化目标。

## 82. 长会话请求体预算

ChatState 在每次构建请求时估算精确 serialized body bytes。接近 50 MB ceiling 前才触发图片 compact，避免每轮都改写历史并破坏 Prompt/KV cache prefix。

## 83. 为什么淘汰到低水位而非刚低于上限

触发后一次回收到约上限的一半，给后续多轮图片留出空间。若只删到临界值，下一张图又会触发历史改写和 cache miss。

## 84. 淘汰顺序与占位文本

系统从最旧 inline 图片开始，用明确文字替代：该图片已因请求大小移除、不能凭记忆描述，需要时请用户重新分享。

这比静默删除更能约束模型幻觉。

## 85. ImageBudget event

有 inline 图片时 ChatState 发出 body bytes、trigger、reclaim target、图片数量、是否 compact、淘汰数量和压缩后大小；Shell 把它写入 unified log，便于本地诊断。

## 86. 413 是最后一道请求级降级

Provider 返回 Payload Too Large 或 image-processing error 时，Sampler 对本次 request 调用 `strip_images`：User image 换成说明文本，ToolResult images 清空，然后在 retry budget 内重试。

## 87. connection reset 也可能代表 body 被拒

某些代理在返回正式 413 前直接 reset/broken pipe。`is_likely_body_rejected` 为这类错误提前 strip 图片，避免反复上传同一巨大 body。

## 88. request strip 与历史 compact 不同

Request-local strip 只改变正在重试的请求；ChatState image compaction 是构建请求时的长期历史预算策略。不要把临时降级误认为已永久删除 Session 资产。

## 89. 当前图片安全边界

1. UI 数量与最小尺寸预检；
2. 发送字节 cap；
3. placeholder 路径 canonicalize、allowlist、denylist；
4. 扩展名与真实解码校验；
5. Shell base64、结构、尺寸与像素校验；
6. 重编码和请求体预算；
7. load-time poisoned-history 修复；
8. Provider 错误后的 strip-and-retry。

## 90. 状态所有权

| 状态 | 所有者 |
| --- | --- |
| chip、preview、undo stash | Pager PromptWidget |
| 客户端 Session image file | Pager/session `images/` |
| ACP blocks | Prompt request/InputItem |
| normalization cache | Shell NormalizeCache |
| 模型可用 asset files | Shell/session `assets/` |
| inline image history | ChatState Conversation |
| 当前 Provider payload | Sampling request task |

## 91. 常见误解

| 误解 | 实际情况 |
| --- | --- |
| `[Image #1]` 本身包含图片 | 它只是文本 anchor，字节在独立结构中 |
| MIME 写 png 就一定是 png | 必须 sniff、解析并检查结构 |
| terminal 不显示预览就不能发图 | 预览能力与模型输入能力分离 |
| file URI 会直接发给云端模型 | inline data 优先，file URI 仅本地参考 |
| 1.5 MB 是客户端硬上限 | 它是 Shell normalization 目标；Pager gate 是 50 MB |
| 所有模型都原生看图 | text-only harness 先转写成文字 |
| 413 后图片从 Session 永久删除 | sampler 可只剥离当前 retry request |
| Tool 图片与用户图片完全同路 | 它们共享校验思想，但 Conversation 角色与收集点不同 |

## 92. 调试：chip 存在但图片没发送

依次检查 live TextArea element、`drain_images` 结果、PastedImage 是否还有 bytes/session path、50 MB gate、ACP blocks 数量、Shell `ParsedPrompt.images` 与 normalization dropped notification。

## 93. 调试：图片和编号错位

检查 `element_id` 绑定、undo restore、display number meta，以及 `attached_image_references` 是否按 meta 而非列表位置解析。不要先修改显示字符串掩盖身份问题。

## 94. 调试：Session 每轮都 400

重点检查加载历史中的 data URI、格式完整性、最小/最大尺寸。确认 `strip_invalid_images` 是否运行并重新持久化修复后的历史。

## 95. 调试：长会话频繁 cache miss

查看 `shell.image_budget`：若几乎每轮触发 compact，检查单图 normalization 是否失效、旧图是否过大，以及 trigger/reclaim target 是否被改得过近。

## 96. 修改图片管线必须守住的不变量

1. chip 身份不能靠 Vec 位置推断。
2. orphan recovery 必须先于路径删除。
3. 路径恢复不得扩大成任意文件读取。
4. dropped/transcribed/evicted 图片必须有明确文本信号。
5. Tool Result 的 call pairing 不能因图片 followup 破坏。
6. Provider wire 不能收到 `file://` 本地 URI。
7. 大图处理不能阻塞异步 runtime。

## 97. 推荐测试矩阵

| 场景 | 期望 |
| --- | --- |
| 插入/删除/undo/redo | chip 与图片始终一一对应 |
| 第 11 张图片 | UI 拒绝 |
| 7×100 图片 | 边长拒绝 |
| 16×16 图片 | 总像素拒绝 |
| 伪装 png | decoder 拒绝 |
| symlink 越界 | canonical prefix gate 拒绝 |
| `.ssh` 内图片 | denylist 拒绝 |
| orphan 合法路径 | 恢复 block，删除文本路径 |
| 大 JPEG | 重编码并发 notice |
| 截断图片 | drop + notice |
| remote HTTP image | 无 inline data 时转发 URL |
| Cursor harness | vision transcription，无 inline part |
| multimodal harness | asset path + inline part |
| Tool screenshot | harvest 一次，不进入 Hook JSON |
| poisoned history | load 时替换/移除 |
| body 接近 ceiling | 淘汰最旧图片至低水位 |
| 413 | strip images 后重试 |

## 98. 如何验证

```sh
cargo test -p xai-grok-pager-render prompt_images --lib
cargo test -p xai-grok-sampling-types strip_images --lib
cargo check -p xai-grok-shell --lib
```

搜索关键限制：

```sh
rg "IMAGE_CAP|MAX_SEND_BYTES|MAX_IMAGE_BYTES|IMAGE_COMPACT" crates
```

## 99. Glossary：图片数据

### Raster Image

由像素网格组成的位图；PNG、JPEG、WebP 等属于此类，与可执行/文本型 SVG 风险不同。

### MIME Type

描述内容媒体类型的字符串，例如 `image/png`；它是声明，必须与真实字节校验配合。

### Base64

把二进制编码成 JSON 可传输字符的方式；会产生约三分之一额外体积。

### Data URL

把 MIME 和 base64 数据装进一个 URL 字符串，如 `data:image/png;base64,...`。

### URI

资源标识；ACP 图片可带 `file://` 来源或 HTTP(S) 远端地址，但 Provider 不接受本地 file URI。

### Pixel Area

宽乘高的总像素数；能比单独边长更准确约束解码内存和视觉 tokenizer 成本。

### Transcode

把一种图片格式转换成 endpoint 支持的格式；本文主要是非原生格式转 PNG。

## 100. Glossary：编辑器与协议

### Chip

Prompt 编辑器中的原子可视元素；`[Image #N]` 可整体移动、删除和撤销。

### ElementId

TextArea 给一个具体 chip 的内部唯一身份，用于绑定真实 PastedImage。

### Display Number

用户看到的 `#N`，也随 ACP meta 传给服务端，用于文字引用和图片对应。

### PastedImage

Pager 编辑期图片记录，包含字节、路径、编号、预览和持久化状态。

### ContentBlock

ACP Prompt 的结构化组成，可分别是 Text、Image、ResourceLink 等。

### ImageContent

ACP 图片 DTO，携带 base64 data、MIME、可选 URI 与 metadata。

### Orphan Placeholder

文本中存在图片路径占位符，但对应 PastedImage/ImageContent 丢失的状态。

## 101. Glossary：验证与安全

### Canonicalization

把路径解析成真实规范路径，消除 `..` 和 symlink 绕过。

### Allowlist

只允许明确列出的目录或格式；不在集合中默认拒绝。

### Denylist

即使父目录允许也明确禁止的敏感子树集合。

### Magic Bytes

文件头部用于识别格式的特征字节；只检查它仍不够，还需结构解析。

### Structural Integrity

图片不仅 header 可读，而且关键数据段和结尾完整，没有被截断。

### Decompression Bomb

压缩文件很小但解码后占用巨量像素/内存的恶意或异常输入。

### Defence in Depth

不同边界重复验证；Pager 检查后 Shell 仍检查，以覆盖其他客户端和状态损坏。

## 102. Glossary：处理与模型能力

### Normalization

把多种合法输入统一到可发送的格式、尺寸和字节预算，并报告压缩或 drop 结果。

### Downscale

降低宽高和像素数量；与只降低 JPEG quality 不同。

### Harness

Agent 面向某种模型/协议兼容行为的运行模板；它决定图片能否原生 inline。

### Multimodal

模型请求可同时接收文字和图片内容 part。

### Transcription / Captioning

先让视觉模型描述图片，再把描述文字交给不能直接看图的 coding model。

### Conversation Outline

提供给视觉模型的近期真实用户问题摘要，用于生成与当前任务相关的描述。

### Auxiliary Sampler

主 Agent 之外的一次受控模型调用；图片 describe 是典型例子。

## 103. Glossary：预算、缓存与恢复

### Request Body

实际上传给 Provider 的序列化 HTTP 内容；base64 图片通常是最大组成部分。

### High-water / Low-water Mark

达到高水位才触发清理，一次降到较低目标，避免在阈值附近反复震荡。

### Image Compaction

按请求字节预算淘汰较旧 inline image，并用明确文字占位，不是对话摘要压缩。

### KV-cache Prefix

Provider 可复用的历史 Prompt 前缀；改写旧图片会使该前缀失效。

### 413 Payload Too Large

HTTP 请求体超过服务端/代理限制；Sampler 可去除 inline 图片后重试。

### Poisoned History

历史里含一个每次发送都会触发确定性错误的坏图片，使 Session 无法继续。

### Retry-local Mutation

只改变当前请求副本的降级，不必等同于永久改写 ChatState 或磁盘资产。

## 104. 一页复习版

```text
Pager:
  PastedImage <-> atomic chip(ElementId, #N)
  -> session/images atomic persistence
  -> submit only live chips

Wire:
  Text + ImageContent(base64, MIME, uri, display-number meta)

Shell ingest:
  recover orphan path under strict policy
  -> strip path
  -> decode/sniff/integrity/dimension checks
  -> transcode/downscale/compress to ~1.5 MB target
  -> explicit notices for compressed/dropped images

Harness:
  multimodal -> assets path + ContentPart::Image
  text-only   -> vision describe -> text envelope

Long-term safety:
  load-time invalid-image repair
  -> near-50MB oldest-image compaction to low water
  -> 413/image-error request-local strip and retry
```

## 105. 源码证据索引

| 结论 | 直接证据 |
| --- | --- |
| chip 与图片身份 | `PastedImage`、`PromptWidget::sync_images_with_textarea` |
| cap 与最小边长 | `PromptWidget::insert_image` |
| 原子客户端落盘 | `persist_to_session` |
| ACP block 和 meta | `build_content_blocks_with_prefixes_and_caps` |
| placeholder threat model | `xai-grok-shared::placeholder_images` |
| Prompt 图片分离 | `parse_prompt_with_skills` |
| 校验与重编码 | `image_normalize::normalize_one_in` |
| notice wiring | `normalize_images_with_notices` |
| multimodal Conversation | `handle_prompt` + `pick_user_image_url` |
| text-only 转写 | `transcribe_user_images`、`image_describe.rs` |
| Tool 图片 harvest | `DrainedToolSuccess` |
| load-time 修复 | `storage::jsonl::strip_invalid_images` |
| 长期字节预算 | `ChatStateActor::build_conversation_request` |
| Provider 转换 | `conversation/messages.rs`、`responses.rs` |
| 413 恢复 | `RetryDecision::RetryWithImageStrip` |

## 106. 阅读完成后应该能回答的问题

1. 为什么 chip、ACP ImageContent 和 Conversation image 必须分层？
2. element id 与 display number 分别解决什么问题？
3. 为什么发送前必须根据 live chip 再 reconcile？
4. orphan placeholder 为什么既要恢复又构成安全风险？
5. canonicalize、prefix allowlist、denylist 和 decoder 各挡住什么攻击？
6. 50 MB、1.5 MB、20 MiB 分别是哪一层的限制？
7. 为什么 normalization 结果还必须携带 notices？
8. Multimodal 与 text-only harness 如何处理同一图片？
9. 为什么视觉描述需要当前 query 和近期 outline？
10. Tool image 为什么要在 Hook 序列化前 drain？
11. Image compaction 为什么要一次回收到低水位？
12. 413 retry 的 strip 与永久历史修改有什么区别？

能回答这些问题，就掌握了图片输入的核心：图片不是“把 base64 塞进 Prompt”这么简单，而是一个同时维护编辑器身份、文件安全、图像完整性、模型能力适配、持久化可恢复性和请求体预算的跨层数据生命周期。
