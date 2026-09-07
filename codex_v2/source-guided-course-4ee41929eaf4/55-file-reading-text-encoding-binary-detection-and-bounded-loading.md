# 55：文件读取、文本编码、二进制识别与有界内容加载——Codex 怎样安全地“打开文件”

> 源码基线：`4ee41929eaf4`
>
> OpenAI 官方用例把处理文件和数据列为 Codex 的常见工作流，但没有规定本地实现必须怎样读取、解码或限制文件。本章关于 512 MiB、1 MiB、16 MiB、4 KiB 等限制的描述，来自当前源码基线，不应理解为永久产品承诺。
>
> 官方对照资料：[ChatGPT 与 Codex Use cases](https://learn.chatgpt.com/use-cases)。

## 1. 本章解决什么问题

第 54 章回答了“怎样找到文件”。找到以后仍不能直接假设：

```text
文件存在 → 一次读完 → 当作 UTF-8 → 交给模型
```

因为真实文件可能：

- 有几百 MiB，甚至还在持续增长；
- 是图片、压缩包或任意二进制字节；
- 是 UTF-8，也可能是 Windows-1252、IBM866 等旧编码；
- 在读取期间被另一进程追加、截短或替换；
- 是 JSONL，其中单独一行就大得不适合放进内存；
- 本身不大，解压后却异常巨大；
- 只需要末尾报错，却被错误地从头读到尾；
- 最终要放进模型上下文，因此还有输出和 token 预算。

本章要建立的核心认识是：

> “读到字节”“解释成文本”“挑出有用部分”“放进下游上下文”是四个不同阶段，每个阶段都需要自己的边界。

## 2. 先说人话：读文件像搬家

把一个文件想成仓库里的箱子：

1. 先看标签上的重量——相当于读取 metadata；
2. 决定整箱搬、分批搬，还是只拿最后几件——读取策略；
3. 搬出来的是物品，不是说明书——原始字节；
4. 再判断说明书用哪种语言——文本编码；
5. 最后只把会议需要的部分带进会议室——上下文截断。

标签可能过期，箱子也可能在搬运时继续装东西，所以“标签写着 20 kg”不能替代搬运过程中的实际限制。

## 3. 一条完整的读取链

```text
Path / PathUri
      │
      ▼
打开并取得 File handle
      │
      ├── metadata 预检
      │
      ▼
按用途整读 / 分块 / 逐行 / 尾读
      │
      ▼
Vec<u8>：原始字节
      │
      ├── 保持二进制、base64 传输
      └── 验证或探测编码 → String
                          │
                          ▼
                 解析 JSON / 显示日志 / 截断
                          │
                          ▼
                    UI、协议或模型上下文
```

越靠后，信息越适合人读；越靠前，越忠实于文件本身。

## 4. `Vec<u8>` 和 `String` 到底差在哪

`Vec<u8>` 是任意字节：

```rust
let bytes: Vec<u8> = vec![0xff, 0x00, 0x61];
```

它不承诺内容是文字。`String` 则必须始终是合法 UTF-8。

所以：

- 图片、压缩数据、未知编码输出先用 `Vec<u8>`；
- 已验证的 UTF-8 配置或 Markdown 才适合 `String`；
- 不应仅凭扩展名把任意文件强制转为文本。

## 5. `read_to_end` 是“读完字节”

典型写法：

```rust
let mut bytes = Vec::new();
file.read_to_end(&mut bytes).await?;
```

它会持续读取到 EOF，把内容追加到 byte vector。它不做：

- UTF-8 校验；
- JSON 解析；
- 二进制识别；
- 自动大小限制。

没有其他边界时，文件多大，内存就可能增长到多大。

## 6. `read_to_string` 是“整读并要求 UTF-8”

`read_to_string` 看起来方便，但同时做了两件事：

1. 把剩余内容全部载入内存；
2. 要求读取结果能成为合法 UTF-8。

因此它适合已知较小、格式明确的文本文件。遇到非法 UTF-8，它返回错误，而不是自动猜编码。

## 7. 为什么 Codex 的文件协议传字节而不是强制传文本

Exec-server 的 `fs/readFile` 先得到 `Vec<u8>`，响应中编码为 base64：

```rust
FsReadFileResponse {
    data_base64: STANDARD.encode(bytes),
}
```

这使协议可以无损传输：

- UTF-8 源码；
- 图片；
- 压缩文件；
- 包含 NUL 的数据；
- 任意平台编码文件。

base64 不是文本编码检测，也不是加密；它只是把任意字节表示成可放进 JSON 的 ASCII 字符。

## 8. 整文件读取为什么设 512 MiB 上限

`codex-rs/exec-server/src/local_file_system.rs` 中：

```rust
const MAX_READ_FILE_BYTES: u64 = 512 * 1024 * 1024;
```

`read_file` 在读之前检查 metadata，实际读取时还再次限制。512 MiB 仍然很大，但它把“无限内存增长”变成了明确失败。

注意：这是当前 exec-server 文件接口的实现上限，不代表模型会接收 512 MiB 内容。

## 9. 第一道门：metadata 长度预检

源码先做：

```rust
let metadata = file.metadata().await?;
if metadata.len() > MAX_READ_FILE_BYTES {
    return Err(file_too_large_error());
}
```

价值有两个：

- 已知超限时不用开始昂贵读取；
- 可以用已知长度预分配 vector，减少反复扩容。

但 metadata 只是某个时刻的观察结果。

## 10. metadata 为什么不是最终保护

设想：

```text
t0: stat 得到 100 KiB
t1: 另一进程开始持续追加
t2: 当前进程 read_to_end
```

如果只信 `t0`，读取仍可能无界增长。这是文件读取中的 check/use 时间窗口。

## 11. 第二道门：`take(MAX + 1)`

源码继续做：

```rust
file.take(MAX_READ_FILE_BYTES + 1)
    .read_to_end(&mut bytes)
    .await?;
```

`take(n)` 把 reader 包装成“最多向下游提供 n 个字节”的 reader。

为什么是 `MAX + 1`，而不是 `MAX`？因为多出的一个字节是哨兵：

```text
读到 <= MAX     → 没有观察到超限
读到 MAX + 1    → 可以确定内容超过限制
```

## 12. 第三步：读取后再次比较

```rust
if bytes.len() as u64 > MAX_READ_FILE_BYTES {
    return Err(file_too_large_error());
}
```

三步组合起来是：

```text
metadata 预检 → take(MAX + 1) 硬边界 → 实际长度判定
```

这是本章最值得迁移到其他项目的模式之一。

## 13. 32 MiB connector cache 使用同一模式

`read_bounded_cache_file` 的上限是：

```rust
CODEX_APPS_TOOLS_CACHE_MAX_BYTES = 32 * 1024 * 1024
```

它同样先看 metadata，再 `take(MAX + 1)`，最后检查实际字节数。错误信息还区分：

- 打开时就已经超限；
- 读取过程中增长到超限。

这里读完后才执行 `serde_json::from_slice`，即“先限制字节，再解析结构”。

## 14. `Vec::with_capacity(metadata.len())` 的含义

预分配表示“预计需要这么多空间”，不表示已经读到这么多数据：

```rust
let mut bytes = Vec::with_capacity(metadata.len() as usize);
```

它减少扩容和复制，但前提是已经拒绝异常大的 metadata。否则不可信长度本身可能诱发巨大分配。

还要注意从 `u64` 到 `usize` 的平台转换；生产代码应考虑目标平台可表达范围。

## 15. 什么情况下应该整文件读取

通常同时满足以下条件才合适：

- 格式要求完整输入，例如一个较小 JSON 文档；
- 有明确、合理的 byte limit；
- 下游确实需要全部内容；
- 失败时可以清晰报告“文件过大”；
- 并发读取数量也受到控制。

“单文件有界”不等于“总内存有界”：100 个并发的 512 MiB 读取仍然不可接受。

## 16. 大文件为什么更适合流式读取

Exec-server 还提供 `read_file_stream`：

```rust
ReaderStream::with_capacity(file, FILE_READ_CHUNK_SIZE)
```

当前 `FILE_READ_CHUNK_SIZE` 是 1 MiB。调用方可以：

```text
读一块 → 处理/发送 → 释放或写出 → 再读下一块
```

内存主要随 chunk size 增长，而不是随文件总长度增长。

## 17. 流式不等于自动有总量上限

流式解决峰值缓冲问题，却不自动限制：

- 总共处理多少字节；
- 流持续多久；
- 下游积压多少 chunk；
- 最终是否把所有 chunk 又拼成一个大 vector。

若消费者执行 `collect()`，流式入口仍可能重新变成整文件载入。

## 18. 随机分块读取：open / readBlock / close

另一条接口先创建 handle，再按 offset 和 length 读取：

```text
fs/open(path, handleId)
fs/readBlock(handleId, offset, len)
fs/close(handleId)
```

它适合：

- 只看文件头；
- 读取指定区间；
- 客户端自行分页；
- 远程环境逐块传输。

## 19. 为什么 `readBlock` 也有限制

当前每块长度要求为 `1..=1 MiB`，每个连接最多打开 128 个 read handle，handle ID 最多 32 bytes。

这三个限制分别约束：

| 边界 | 防止什么 |
|---|---|
| block length | 单次分配和协议消息过大 |
| open handle count | 文件描述符与服务端状态无界增长 |
| handle ID length | 不可信标识占用过多协议/内存空间 |

这说明容量限制不只针对“内容”。

## 20. `read_at` 为什么不共享游标

`FileReadHandleManager` 在 Unix 使用 `FileExt::read_at`，Windows 使用 `seek_read`，按显式 offset 读取。

这比多个请求共同修改一个文件 cursor 更容易推理：每次请求都说明“从哪里读”，并发请求不会因为先后改变同一游标而互相串扰。

源码还处理：

- offset 加法溢出；
- `Interrupted` 后继续读；
- EOF 时缩短 vector；
- read error 后关闭 handle。

## 21. `BufReader` 解决的是什么

逐字节或逐行读取如果每次都触发系统调用，会很慢。`BufReader` 在用户态一次读入一块，再从缓冲区满足多次小读取。

```rust
let reader = BufReader::new(file);
for line in reader.lines() {
    // ...
}
```

它改善 I/O 模式，但不会自动限制“某一行有多长”。

## 22. JSONL 为什么适合历史记录

JSONL 是“一行一个 JSON 对象”：

```text
{"session_id":"a","text":"first"}
{"session_id":"b","text":"second"}
```

优点是：

- 可以 append；
- 可以逐条解析；
- 某条损坏后有机会在下一换行重新同步；
- 裁剪旧记录时可以沿完整行边界处理。

它不像一个巨大 JSON array 那样必须先看到文件末尾才能确认整体结构。

## 23. `lines()` 隐含 UTF-8 要求

`BufRead::lines()` 返回 `String`，因此每行必须能解码为 UTF-8。消息历史的 `lookup_history_entry` 用它逐行寻找 offset，再调用 `serde_json::from_str`。

这适合由 Codex 自己写出的 UTF-8 JSONL；对未知编码或任意二进制输入则不合适。

## 24. `read_until(b'\n', Vec<u8>)` 更底层

Rollout migration 使用的是：

```rust
reader.read_until(b'\n', bytes).await?
```

它先按 byte delimiter 找记录，不要求读取过程已经构造合法 `String`。之后才把完整、大小合格的字节交给 line parser。

这把“寻找记录边界”和“解释记录内容”分开了。

## 25. 单行也必须有上限

JSONL 文件可以逐行读，但攻击者或历史异常数据可能制造一条 2 GiB 的“行”。若直接 `read_until`，vector 仍会一直长大。

源码设置：

```rust
const MAX_ROLLOUT_LINE_BYTES: usize = 16 * 1024 * 1024;
```

并对单次记录使用 `take(MAX + 1).read_until(...)`。

## 26. 超大记录为什么要读到下一个换行

检测到超过 16 MiB 后，源码没有把后半段误当成新 JSON 记录，而是循环丢弃，直到：

- 看到 `\n`；或
- 到达 EOF。

然后返回 `line: None`，同时保留实际处理的 byte count。

这叫重新同步记录边界：跳过坏记录，但不要污染下一条好记录。

## 27. 损坏记录与 I/O 错误不是一回事

Rollout migration 对两类失败采取不同策略：

- `read_until` 失败：底层 I/O 错误，操作失败；
- 完整行无法解析：该记录 `line: None`，迁移可继续看下一行。

能否跳过，取决于格式是否有可靠边界，以及业务是否允许丢弃该条历史记录。

## 28. 统计换行时不必解码文本

消息历史为了统计 entry count，循环读取固定 8192-byte buffer，然后用 `memchr_iter(b'\n', ...)` 计数。

这个任务只关心一个 ASCII byte，不需要：

- 构造所有行；
- 解析 JSON；
- 验证 UTF-8。

这是“按问题所需的最低语义读取”的好例子。

## 29. 为什么日志通常只读 tail

进程启动失败时，最有价值的错误通常在 stderr 最后几行。App-server daemon 根据观察到的文件长度，从末尾 4096 bytes 对应的位置开始读：

```text
len → start = len.saturating_sub(4096)
    → seek(start)
    → read_to_end
```

这样日志即使非常大，诊断上下文也保持较小。

## 30. 从任意 byte offset 开始会切断一行

如果文件大于 4096 bytes，`start` 很可能落在某一行中间。源码找到读取结果中的第一个 `\n`，把它和前面的残缺片段丢弃。

于是展示内容从下一条完整日志行开始。

这是一个取舍：宁可少显示半行，也不把无上下文的半行当成完整错误。

## 31. byte offset 还可能切断 UTF-8 字符

中文字符通常占多个 UTF-8 bytes。tail offset 可能落在字符中间。日志读取最终使用：

```rust
String::from_utf8_lossy(&bytes)
```

非法片段会被 Unicode replacement character `�` 替代，读取不会因此完全失败。

## 32. 严格 UTF-8 与 lossy UTF-8

两种常见策略：

```rust
String::from_utf8(bytes)          // 非法 UTF-8 → Err
String::from_utf8_lossy(&bytes)   // 非法部分 → �
```

选择规则通常是：

- 配置、协议、需要精确往返的数据：严格失败；
- stderr、shell 输出、尽力诊断文本：lossy 更实用；
- 原始文件传输：保持 bytes，不应先 lossy。

lossy 是信息损失，不能把结果再当作原始文件写回。

## 33. Codex 的 shell 输出会先尝试智能解码

`codex-rs/protocol/src/exec_output.rs` 的 `bytes_to_string_smart` 顺序是：

```text
空字节流 → 空 String
合法 UTF-8 → 原样使用
否则 → chardetng 猜测编码
     → encoding_rs 解码
     → 解码仍有错误时才 lossy UTF-8
```

这比一律 `from_utf8_lossy` 更能保留 Windows 旧 code page 的可读输出。

## 34. 编码探测为什么只是推断

裸字节通常没有“我是哪种编码”的可靠标签。同一组 bytes 在不同 code page 下可能对应不同字符。

源码甚至专门处理 IBM866 与 Windows-1252 在 `0x80..=0x9F` 范围的冲突：只有出现特定智能标点 byte 且同时有 ASCII 单词时，才将特定猜测改为 Windows-1252。

这说明编码检测需要：

- 保守 heuristic；
- 真实案例测试；
- 最终 fallback；
- 不把猜测说成绝对事实。

## 35. “文本编码”与“base64”不要混淆

| 名称 | 解决的问题 |
|---|---|
| UTF-8 / Windows-1252 / IBM866 | bytes 分别代表哪些字符 |
| base64 | 怎样用 ASCII 文本无损携带任意 bytes |
| JSON escaping | 字符串怎样安全放进 JSON 语法 |
| compression | 怎样减少或组织字节表示 |

base64 解码后仍可能是图片，也可能是某种未知编码文本。

## 36. Codex 有没有一个万能“二进制文件检测器”

在本章追踪的核心读取路径中，没有一个统一函数先宣布“这个文件一定是文本/二进制”，再决定所有行为。

实际边界更明确：

- 文件系统协议直接保留 bytes；
- shell 输出按展示目的尽力解码；
- JSONL/技能文件由格式契约要求文本；
- 图片、音频等由专门媒体类型处理。

因此不要把“当前没找到统一 detector”误写成“Codex 永远不会做任何 MIME 或媒体判断”。这里结论只限于本章检查的读取链。

## 37. 常见二进制 heuristic 为什么都不完美

其他项目常用：

- 是否包含 NUL byte；
- UTF-8 校验是否失败；
- 文件扩展名；
- MIME database；
- magic bytes；
- 可打印字符比例。

它们都只是线索。例如 UTF-16 文本常含 NUL；无 NUL 的压缩数据仍是二进制；`.txt` 也可能内容损坏。最好让协议保留 bytes，再由了解格式的消费者判断。

## 38. UTF-8 字符边界为什么重要

Rust `String` 的下标是 byte offset，但切片边界必须位于字符边界。直接执行：

```rust
&text[..8000]
```

若第 8000 byte 位于一个中文字符中间，会 panic。

`take_bytes_at_char_boundary` 会向前找到最后一个完整字符边界，再返回 prefix。

## 39. Skill prompt 的 8000-byte 限制

单个注入模型的 skill instruction body 当前有：

```rust
MAX_SKILL_PROMPT_BYTES: usize = 8_000
```

`bounded_skill_prompt_contents` 使用 UTF-8 安全的 prefix 截断，并返回 `truncated` 标志。

这里限制的是“模型可见内容”，不等同于磁盘上的 `SKILL.md` 文件大小。

## 40. 为什么 byte budget 和 token budget 都存在

byte limit 适合保护：

- 内存分配；
- 协议消息；
- 文件或记录读取；
- 字符串切片。

token limit 适合保护模型 context。当前工具输出的 token 数是近似估算，并不是精确 tokenizer 结果。

同一个中文字符串，字符数、UTF-8 byte 数、估算 token 数可能完全不同。

## 41. 截断为什么不能悄悄发生

`formatted_truncate_text` 会添加类似：

```text
Warning: truncated output (original token count: ...)
Total output lines: ...
```

Unified exec 的 byte buffer 则在中间加入：

```text
... N bytes omitted ...
```

显式 marker 很重要，否则模型或用户会把不完整内容误认为完整事实。

## 42. 为什么经常保留 head 和 tail

命令输出开头常包含：

- 命令初始化信息；
- 测试套件和环境；
- 第一处关键上下文。

末尾常包含：

- 最终错误；
- summary；
- exit reason。

`HeadTailBuffer` 当前最多保留 1 MiB，将预算大致一半给 head、一半给 tail，丢弃中间并累计 `omitted_bytes`。

## 43. HeadTailBuffer 怎样保持固定内存

逻辑可以简化成：

```text
head 未满 → 先填 head
head 已满 → 新字节进入 tail
tail 超限 → 从 tail 最旧位置丢弃
```

因此无论进程输出 2 MiB 还是 20 GiB，保留 transcript 的主体容量都不会跟着无限增长。

这限制的是 retained output；系统仍要持续从 pipe/PTY 消费数据，避免子进程因管道塞满而阻塞。

## 44. byte 层截断可能切断字符怎么办

`HeadTailBuffer` 负责的是 bytes，所以 head/tail 边缘可能切到 UTF-8 中间。最终展示时使用 lossy conversion，可以产生 `�`。

另一条 `truncate_middle_chars` 路径已经拿到合法 `&str`，会通过 `char_indices()` 选择安全边界。

两者并不矛盾：

- 收集任意进程输出时，先保证 byte 内存有界；
- 已是合法文本时，优先保证字符边界。

## 45. 输出 delta 也要单独限大小

Unified exec 不只限制最终保留 transcript，还把单次 output delta 限为 8192 bytes。它尽量分出合法 UTF-8 prefix；若队首无法解码，则至少推进 1 byte 以避免输出停滞，因此不能把它理解为保证每个事件都单独构成合法 UTF-8 文本。

原因是最终总量有界，并不代表某一个实时 JSON-RPC event 不会过大。资源链上的每个 queue、event 和 aggregate 都要检查自己的边界。

## 46. 文件在读取时增长会发生什么

不同策略观察不同语义：

- 整文件 bounded read：可能读到打开以后追加的部分，但最多 `MAX + 1`；
- tail read：metadata 后继续增长时，`read_to_end` 可能多读新内容；
- offset block read：每块看到该次 `read_at` 时的内容；
- JSONL reader：可能在 EOF 前看到新追加记录，取决于读取时序。

除非显式锁定或读取 immutable snapshot，否则“打开文件”不自动获得事务快照。

## 47. 文件被截短或替换又会怎样

打开的 file handle 通常引用已打开的对象；路径之后被 rename 到另一个文件，不一定改变该 handle 指向的对象。截短则可能让后续读取提前 EOF。

因此需要区分：

- path identity；
- open handle identity；
- metadata 观察时刻；
- 每次 block 的读取时刻。

如果业务需要一致快照，应复制到 staging、持有合适锁，或在完成后复核 size/mtime/version。

## 48. 压缩文件的“流式”价值

Rollout migration 用：

```rust
let mut decoder = zstd::stream::read::Decoder::new(input)?;
io::copy(&mut decoder, &mut output)?;
```

解压数据从 decoder 流向文件，不需要把整个未压缩 rollout 放进一个 vector。压缩时同样用 streaming encoder，完成后 `finish()`。

## 49. 压缩大小上限不等于解压大小上限

一个很小的压缩文件可能展开成巨量内容，这类风险常被称为 decompression bomb。

当前本章展示的 `decompress_rollout_to_path` 是流式写到磁盘，不会按完整解压结果分配同等大小的内存；但从这段函数本身看不到一个“最大解压字节数”检查。

所以应准确表述为：

- 它避免整份解压内容驻留内存；
- 它不单独证明磁盘使用量有界；
- 上游选择、磁盘配额和后续逐行限制仍然重要。

## 50. 为什么压缩后再解压到 `sink()` 验证

发布逻辑压缩完成并同步文件后，又重新打开 compressed file，用 decoder 把结果复制到 `io::sink()`。

`sink()` 丢弃解压数据，但 decoder 仍会完整检查压缩流能否被读完。这验证的是“刚写出的压缩制品可解码”，而不是验证每条 JSON 业务记录都正确。

## 51. 异步代码为什么会出现 `spawn_blocking`

`std::fs`、文件锁、同步 zstd、`fsync` 和某些随机访问读取会阻塞线程。放进 `tokio::task::spawn_blocking` 可以避免长时间占用 Tokio async worker。

但它不使操作自动可取消：future 被取消时，已经运行的 blocking closure 可能继续到结束。调用方仍要管理 deadline、结果丢弃和资源生命周期。

## 52. 不同数据应采用不同失败策略

| 数据 | 常见策略 | 原因 |
|---|---|---|
| 可重建 cache | 读取/JSON 失败时当 cache miss | 权威源可重新获取 |
| 用户 config | 严格报错并指出路径 | 悄悄忽略会造成意外行为 |
| stderr tail | 缺失返回 None，坏字节 lossy | 目标是尽量提供诊断 |
| Rollout 单条旧记录 | 有边界时可跳过并继续 | migration 需要尽量恢复其余记录 |
| 原始文件 API | 返回 bytes 或明确超限错误 | 不应擅自损坏内容 |

“容错”不是统一吞掉所有错误，而是根据数据权威性和用途选择。

## 53. 一个教学版有界文本读取器

下面是简化模型，不是从源码原样复制：

```rust
fn read_bounded_utf8(path: &Path, max: u64) -> anyhow::Result<String> {
    let mut file = File::open(path)?;
    let metadata = file.metadata()?;
    if metadata.len() > max {
        anyhow::bail!("file exceeds {max} bytes");
    }

    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    file.by_ref().take(max + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > max {
        anyhow::bail!("file grew beyond {max} bytes while reading");
    }

    Ok(String::from_utf8(bytes)?)
}
```

它明确选择了：整读、byte 上限、竞态二次检查、严格 UTF-8。

## 54. 设计读取接口前的决策表

| 问题 | 可选答案 |
|---|---|
| 内容必须全部存在吗 | 整读 / 流式 / head-tail / 指定 range |
| 数据是什么 | 任意 bytes / 已知 UTF-8 / 待探测 shell 输出 |
| 最大总量 | 文件、单记录、解压后、最终聚合分别多少 |
| 边界是什么 | byte / line / record / token / duration |
| 文件会变化吗 | 是否需要 lock、version 复核或 staging snapshot |
| 超限怎么办 | fail、skip record、truncate + marker、转为流式 |
| 谁拥有资源 | handle 何时 close，错误/断线是否自动清理 |
| 下游是谁 | 磁盘、UI、JSON-RPC、模型 context 的限制各不同 |

## 55. 常见错误

### 错误一：只看 metadata 就 `read_to_end`

文件可能在预检后增长；用 `take(MAX + 1)` 加读取后检查。

### 错误二：用 `read_to_string` 打开任何用户文件

它既整读又要求 UTF-8；任意文件接口应先保留 bytes。

### 错误三：流式读取后又无界 `collect`

这只把内存风险移到了消费者。

### 错误四：JSONL 逐行就一定安全

单行可能巨大；要限制 record size，并在超限后重新同步到换行。

### 错误五：按 byte 直接切 `String`

可能切到多字节字符中间并 panic；用字符边界 helper。

### 错误六：lossy 转换后再写回原文件

`�` 代表信息已经丢失，不能无损 round-trip。

### 错误七：静默截断

用户和模型会误判完整性；加 marker 和原始规模信息。

### 错误八：只限制压缩文件大小

解压后体积可能完全不同；同时考虑内存、磁盘和业务记录边界。

### 错误九：把 UTF-8 失败等同于二进制

它可能只是合法的旧文本编码。

### 错误十：把单请求上限当成系统总容量

还要限制并发、open handle 数、队列长度和生命周期。

## 56. 理解检查

### 问题一

为什么 metadata 已经小于 32 MiB，connector cache 读取仍要 `take(32 MiB + 1)`？

<details>
<summary>参考答案</summary>

metadata 只是预检时的长度；文件可能随后增长。`MAX + 1` 为真实读取建立硬边界，并用多出的一个 byte 明确检测超限。

</details>

### 问题二

为什么 `fs/readFile` 返回 base64，而不是直接返回 `String`？

<details>
<summary>参考答案</summary>

文件可能是任意二进制或非 UTF-8 内容。base64 能在 JSON 中无损携带 bytes，把格式解释留给了解内容的消费者。

</details>

### 问题三

为什么 Rollout 超大行被发现后还要继续读到换行？

<details>
<summary>参考答案</summary>

当前 reader 只读了该记录的前缀。如果立刻开始下一次解析，超大行的剩余后缀会被误当成新记录；丢弃到换行才能恢复 JSONL 记录边界。

</details>

### 问题四

为什么 stderr tail 可以 lossy 解码，而配置文件通常不应该？

<details>
<summary>参考答案</summary>

stderr 的目标是尽可能展示诊断，个别坏 byte 不应让全部上下文消失；配置需要精确语义，替换字符可能悄悄改变值，应明确失败。

</details>

### 问题五

流式 zstd 解压是否已经证明磁盘使用有上限？

<details>
<summary>参考答案</summary>

没有。它避免把全部解压内容同时放进内存，但若没有独立的解压字节或磁盘配额限制，输出文件仍可能很大。

</details>

### 问题六

为什么保留命令输出的 head 和 tail，通常比只保留 head 更有用？

<details>
<summary>参考答案</summary>

head 提供环境和起始上下文，tail 常包含最终错误、summary 与退出原因；中间省略时用 marker 明示即可。

</details>

## 57. 本章词汇表与代码名称翻译

| 英文或代码名 | 字面意思 | 在本章中的具体含义 |
|---|---|---|
| Byte / `u8` | 字节/8 位无符号整数 | 文件与进程输出的最原始数据单位 |
| `Vec<u8>` | byte vector | 可拥有任意二进制内容的连续缓冲区 |
| `String` / `&str` | UTF-8 字符串 | Rust 中始终要求合法 UTF-8 的拥有/借用文本 |
| Encoding | 字符编码 | bytes 到 Unicode characters 的解释规则 |
| UTF-8 | Unicode 变长编码 | Rust String 使用、字符可占 1–4 bytes 的编码 |
| Code page | 代码页 | Windows 等环境中传统的 byte-to-character 映射 |
| Windows-1252 / IBM866 | 旧字符编码名 | shell 输出智能探测中可能冲突的两种编码 |
| Lossy decoding | 有损解码 | 用 `�` 替代无法解释的 byte sequence |
| Replacement character | 替换字符 | Unicode `�`，表示原字节未能正确解码 |
| `read_to_end` | 读到末尾 | 将 reader 剩余 bytes 全部追加到 vector |
| `read_to_string` | 读到字符串 | 整读并要求内容是合法 UTF-8 |
| `BufReader` | 缓冲读取器 | 用用户态 buffer 减少频繁小系统调用 |
| Metadata / `stat` | 元数据/检查文件状态 | size、mtime、file type 等某一时刻的属性 |
| Capacity | 容量 | vector 无需重新分配即可容纳的元素数，不是已读长度 |
| Bounded read | 有界读取 | 无论输入怎样变化，单次载入都有明确最大值 |
| `take(n)` | 最多取 n 个 byte | 包装 reader，限制向下游交付的字节总数 |
| Sentinel byte | 哨兵字节 | `MAX + 1` 中用于证明“确实超限”的额外 byte |
| EOF | End Of File | 当前 reader 已无更多内容可读 |
| Chunk | 数据块 | 流式处理中一次读取、传输或处理的一段 bytes |
| `FILE_READ_CHUNK_SIZE` | 文件读取块大小 | 当前远程/流式读取每块最多 1 MiB |
| File handle | 文件句柄 | 打开文件后由进程持有、用于后续读取的资源 |
| Offset | 偏移量 | 从文件开头算起的 byte 位置 |
| `read_at` / `seek_read` | 按位置读取 | 不依赖共享 cursor，从指定 offset 读取 bytes |
| Cursor | 文件游标 | 普通顺序读取下一次开始的位置 |
| Base64 | 二进制转 ASCII 表示 | 让任意 bytes 可放入 JSON；不是加密或字符编码探测 |
| JSONL | JSON Lines | 每个换行边界包含一个独立 JSON record |
| Delimiter | 分隔符 | 这里主要指分隔 JSONL records 的 newline byte |
| Record boundary | 记录边界 | 一条结构化记录开始和结束的位置 |
| Resynchronize | 重新同步 | 坏/超大行后读到下一换行，恢复正确记录边界 |
| Tail read | 尾部读取 | seek 到靠近末尾的位置，只读最近内容 |
| Head / tail | 头部/尾部 | 有界输出中保留的开头和结尾片段 |
| Truncation | 截断 | 因预算主动省略部分内容 |
| Omission marker | 省略标记 | 明示有多少 bytes/items 被丢弃的插入文本 |
| Character boundary | 字符边界 | UTF-8 中可安全切片的位置 |
| Byte budget | 字节预算 | 保护内存、消息或字符串长度的上限 |
| Token budget | token 预算 | 约束模型 context 的近似或精确容量 |
| Encoding detection | 编码探测 | 根据 bytes 推测最可能的字符编码 |
| Heuristic | 启发式规则 | 有实用价值但不能保证绝对正确的判断 |
| Binary detection | 二进制识别 | 判断内容是否适合按文本解释的过程或策略 |
| MIME type | 媒体类型 | 对内容格式的声明/推断，不保证内容一定可信 |
| Magic bytes | 魔数 | 某些文件格式开头用于识别类型的固定 byte pattern |
| Streaming | 流式处理 | 分块读取和消费，不要求完整内容同时驻留内存 |
| Backpressure | 背压 | 下游处理速度限制上游继续生产数据的机制 |
| Compression | 压缩 | 用另一种 byte representation 减少体积 |
| zstd decoder/encoder | zstd 解压/压缩器 | Rollout 压缩迁移使用的流式编解码组件 |
| Decompression bomb | 解压炸弹 | 压缩体积小但展开异常巨大的输入 |
| `io::copy` | 流式复制 | 循环从 reader 读并写给 writer，不整份载入内存 |
| `io::sink` | 数据黑洞 writer | 接收并丢弃 bytes，这里用于验证压缩流可完整解码 |
| `spawn_blocking` | 阻塞线程池执行 | 把同步 I/O、压缩等从 async worker 移开 |
| Snapshot semantics | 快照语义 | 一次读取究竟代表哪个时刻的内容 |
| TOCTOU | 检查到使用的竞态 | metadata 预检后文件发生增长或替换 |
| Strict parsing | 严格解析 | 格式或编码错误时返回失败，不自行修补 |
| Best effort | 尽力而为 | 在诊断等场景尽量返回可用部分并标注损失 |

## 58. 源码检查点

建议按以下顺序阅读：

1. `codex-rs/exec-server/src/local_file_system.rs`
   - 看 `MAX_READ_FILE_BYTES`、metadata 预检、`take(MAX+1)` 和 `read_file_stream`。
2. `codex-rs/file-system/src/lib.rs`
   - 看 `FILE_READ_CHUNK_SIZE` 和 `ExecutorFileSystem` 的 byte-oriented contract。
3. `codex-rs/exec-server/src/file_read.rs`
   - 看 handle 上限、block 长度、offset overflow、`read_at`/`seek_read` 和 error cleanup。
4. `codex-rs/exec-server/src/remote_file_stream.rs`
   - 看远程 block 的逐块 offset 推进、响应大小复核、EOF close 和 Drop cleanup。
5. `codex-rs/exec-server/src/server/file_system_handler.rs`
   - 看 `fs/readFile` 的 base64 边界和 open/readBlock/close 协议适配。
6. `codex-rs/connectors/src/connector_runtime/persistence.rs`
   - 看 32 MiB cache、文件增长竞态和“先 byte bound、后 JSON decode”。
7. `codex-rs/message-history/src/lib.rs`
   - 看 BufReader、`lines()`、8192-byte newline scan 和按完整行裁剪。
8. `codex-rs/thread-store/src/local/rollout_migration.rs`
   - 看 `MAX_ROLLOUT_LINE_BYTES`、`read_until`、超大行丢弃和 record resync。
9. `codex-rs/app-server-daemon/src/backend/pid.rs`
   - 看 4096-byte stderr tail、seek、首个 newline 丢弃和 lossy decoding。
10. `codex-rs/protocol/src/exec_output.rs`
   - 看 UTF-8 fast path、chardetng、encoding_rs、Windows-1252/IBM866 heuristic 与 fallback。
11. `codex-rs/core/src/unified_exec/head_tail_buffer.rs`
    - 看 1 MiB head/tail retention、omitted byte 计数和 marker。
12. `codex-rs/core/src/unified_exec/async_watcher.rs`
    - 看 8192-byte delta、合法 UTF-8 prefix 与无法解码时至少推进 1 byte 的活性取舍。
13. `codex-rs/utils/string/src/truncate.rs` 与 `codex-rs/utils/string/src/lib.rs`
    - 看字符安全的 prefix/middle truncation 与近似 token 换算。
14. `codex-rs/core-skills/src/lib.rs` 与 `codex-rs/core-skills/src/injection.rs`
    - 看 model-visible skill instruction 的 8000-byte 上限。
15. `codex-rs/thread-store/src/local/rollout_migration/publish.rs`
    - 看 zstd streaming copy、`finish`、`sync_all` 与 decode-to-sink validation。

## 59. 一句话总结

阅读任何文件加载代码时，依次追问：

```text
输入首先是 bytes 还是已经承诺为 UTF-8 text？
需要整份、逐块、逐行、指定区间，还是只要 tail？
metadata 预检后，真实读取是否仍有 MAX + 1 硬边界？
单文件、单记录、单 chunk、并发 handle 和最终 context 是否都有限制？
编码失败要严格报错、智能探测，还是 lossy 尽力展示？
截断是否保持字符/记录边界，并明确插入 omission marker？
文件读取期间增长、截短或替换时，调用方得到什么快照语义？
压缩数据的内存与解压后磁盘规模是否分别受控？
错误时资源是否关闭，阻塞 I/O 是否离开 async worker？
```

能回答这些问题，才算真正理解“Codex 打开了一个文件”背后的安全、容量和正确性含义。
