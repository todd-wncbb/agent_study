# grok_build/read_file 源码逐行导读

> 目录：`crates/codegen/xai-grok-tools/src/implementations/grok_build/read_file/`
>
> 共 3 个源文件、约 2551 行。共享底层逻辑在父模块 `implementations/read_file/`（`image.rs`、`pdf.rs`、`pptx.rs`、`metadata.rs`），本目录是 **grok_build 工具集** 下的新架构 `ReadFileTool` 实现。

---

## 目录结构

| 文件 | 行数 | 职责 |
|------|-----:|------|
| `mod.rs` | 2517 | 主实现：`ReadFileTool`、`run_read_file`、`extract_file_content_lines`、大量测试 |
| `versions/mod.rs` | 8 | 版本子模块入口 |
| `versions/legacy_0_4_10.rs` | 26 | legacy-0.4.10 行为策略 |

---

# 一、`versions/mod.rs`（第 1–8 行）

| 行 | 代码 | 说明 |
|----|------|------|
| 1–6 | `//! Version-specific behavior modules...` | 模块文档：说明版本分叉——`legacy_0_4_10` 用泛化错误、不 enforce gitignore；当前行为留在 `read_file/mod.rs` |
| 8 | `pub(crate) mod legacy_0_4_10;` | 仅导出 legacy 子模块，无其他版本文件 |

---

# 二、`versions/legacy_0_4_10.rs`（第 1–26 行）

| 行 | 代码 | 说明 |
|----|------|------|
| 1–8 | 模块文档 | 集中 legacy 策略：`render_read_error`（泛化错误）、`allows_gitignored_reads`（可读 gitignore 文件）；执行路径仍在 `mod.rs` |
| 10 | `use std::path::Path;` | 路径类型 |
| 12–18 | `render_read_error` 文档 | 说明历史 0.4.10 把所有 FS 失败折叠成同一消息，不附带 OS 细节 |
| 19–21 | `pub(crate) fn render_read_error(path: &Path) -> String` | 返回 `Failed to read file: {path}`，与历史行为字节级一致 |
| 23–26 | `allows_gitignored_reads() -> bool` | 恒为 `true`；legacy 不拦截 `.gitignore` 下的文件 |

---

# 三、`mod.rs` — 模块头与依赖（第 1–24 行）

| 行 | 说明 |
|----|------|
| 1–7 | 模块文档：新架构 ReadFile；复用旧 `implementations::read_file` 的核心逻辑；通知经 `NotificationHandle`；Reminder 不在此实现 |
| 9–11 | 从父模块 `implementations::read_file` 导入：`handle_pdf`、`is_pdf_file`、`raw_text_to_file_content`、`run_document_extraction` |
| 12–13 | 输出类型：`TruncationConfig`、`FileContent`、`ReadFileOutput` |
| 14 | `Expr`、`ToolRequirement` — 工具依赖表达式 |
| 15 | `Params` — Resources 中的配置容器 |
| 16–20 | Resources 类型：`Cwd`、`DisplayCwd`、`FileSystem`、`GitignoreFilter`、`PathNotFoundHints`、`RespectGitignore`、`TruncationCfg`、`display_cwd_or_cwd`、`resolve_model_path` |
| 21 | `TemplateRenderer` — 错误消息里解析 `${{ tools.by_kind.* }}` 工具名 |
| 22 | `ToolKind`、`ToolNamespace` |
| 23 | `LazyLock` — 静态 capabilities |
| 24 | `mod versions;` — 版本子模块 |

---

# 四、`mod.rs` — 配置与版本（第 26–59 行）

| 行 | 说明 |
|----|------|
| 25 | `GrokIntegerSchema` — JSON Schema 用宽松整数类型 |
| 26–32 | **`ReadFileParams`**：`cursor_rules_on_read: bool`，控制读文件后是否追加 cursor rules |
| 33 | `register_resource!` — 把 `ReadFileParams` 注册进 Resources |
| 34–38 | 文档：read_file 有跨版本分叉（gitignore、错误映射），是高风险提取点 |
| 39–43 | **`ReadFileVersion`** 枚举：`Current` / `Legacy0_4_10` |
| 44–50 | `from_contract`：`"legacy-0.4.10"` → Legacy，否则 Current |
| 51–53 | `is_legacy()` |
| 55 | **`MAX_NUM_TOKENS = 25_000`** — 单次读取内容 token 上限（超出 → `FileTooLarge`） |
| 56 | **`MAX_LINES_READ = 1_000`** — 默认最大行数（与 `TruncationCfg` 配合） |
| 57–59 | re-export 父模块：`FileMetadata`、`PDF_MAX_PAGES_PER_READ`、`bytes_to_metadata`、`parse_page_range` |

---

# 五、`mod.rs` — 流式与 PPTX 常量（第 60–101 行）

| 行 | 说明 |
|----|------|
| 60–62 | 文档：`STREAM_DELTA_TARGET_BYTES` 每块 4KiB，低于 `stream_chunk` 16KiB 上限，且按 char 边界对齐 |
| 63 | `STREAM_DELTA_TARGET_BYTES = 4 * 1024` |
| 64–66 | 文档：capabilities 是流式 spec 的单一真相源 |
| 67–76 | **`READ_FILE_CAPABILITIES`**（LazyLock）：`is_read_only: true`、`tool_scope: Read`、streaming subkind `read_file_chunk` |
| 77 | `MAX_PPTX_BYTES = 50MB` |
| 78 | `PPTX_PROCESS_TIMEOUT = 60s` |
| 79–92 | **`handle_pptx`**：调用 `run_document_extraction`，传入 `extract_pptx_text` 回调 |
| 93–96 | `extract_pptx_text` 文档：zip + DrawingML 文本 |
| 97–101 | `extract_pptx_text`：调 `pptx::extract_pptx_text_from_bytes` → `raw_text_to_file_content` |

---

# 六、`mod.rs` — 工具描述与输入 Schema（第 102–149 行）

| 行 | 说明 |
|----|------|
| 102–110 | **`DESCRIPTION_FULL`**：给模型的工具说明模板（含 `${{ params.read.target_file }}`、`{max_lines_read}` 占位） |
| 111–114 | `schema_default_offset()` → `Some(1)`，Schema  advertised 默认从第 1 行读 |
| 115–149 | **`ReadFileInput`** 结构体（wire 名 `target_file`）： |
| 117–121 | `path: String` — 相对 cwd 或绝对路径 |
| 122–132 | `offset: Option<i64>` — 起始行；`deserialize_lenient_i64` 允许字符串 `"-3"` |
| 133–138 | `limit: Option<usize>` — 最多读几行 |
| 139–143 | `pages: Option<String>` — PDF 页范围如 `"1-5"` |
| 144–148 | `format: Option<String>` — PDF：`image`（默认）或 `text` |

---

# 七、`mod.rs` — 辅助函数（第 150–225 行）

### `cursor_rules_on_read_enabled`（150–154）

从 `Resources` 读 `Params<ReadFileParams>`，返回是否开启 cursor rules。

### `resolve_read_start_line`（155–175）

解析 **1-indexed** 起始行，支持负数（从文件末尾倒数）：

| 行 | 逻辑 |
|----|------|
| 162 | `offset_raw = offset.unwrap_or(1)` |
| 163–165 | `offset == 0` → 当作 1 |
| 166–168 | 正数 → 直接 `as usize` |
| 169–172 | 负数：`split('\n').count()` + 若无尾 `\n` 则 +1（phantom 行） |
| 173–174 | `computed = total_fields + offset_raw + 1`，`max(1)` |

与 harness 对齐；负 offset 可能落在「仅 phantom 空行」窗口 → 空内容。

### `stored_read_offset`（176–181）

仅非负 offset 存入 `FileContent.offset`；负数在输出里记为 `None`。

### `is_skill_markdown`（182–212）

判断是否 **skill 相关 Markdown**（读时跳过 offset/limit/token 上限）：

| 条件 | 结果 |
|------|------|
| 文件名 == `SKILL.md` | true |
| 扩展名 `.md` 且路径组件含 `skills` | true |
| 手动折叠 `..`/`.`，不匹配 `skills-cursor` 等 |

---

# 八、`mod.rs` — `ExtractedContent` 与 `extract_file_content_lines`（第 213–326 行）

### `ExtractedContent`（213–225）

| 字段 | 含义 |
|------|------|
| `content` | 带行号的默认格式（`N→` 分隔符） |
| `content_concise` | 与 content 相同（历史兼容） |
| `raw_output` | 原始文本切片（无行号装饰） |
| `extracted_images` | 行内 base64 图提取结果，供 session 转 multimodal |

### `extract_file_content_lines`（226–326）

**核心格式化函数**，被 `run_read_file` 与 compaction 重读共用。

| 行段 | 行为 |
|------|------|
| 232–240 | 内部 `strip`：去掉行尾 `\n`、`\r\n` |
| 243–251 | 初始化输出 buffer、`skip`（start line -1）、`take`（limit 或 MAX） |
| 252–256 | 空文件但 `total_lines>0` 且读第一行 → 输出 `1→` |
| 257–267 | `split_inclusive('\n')` 迭代，`.skip(skip).take(take)` |
| 278–284 | 每行尝试 `try_extract_base64_images`：有则替换为占位符并收集 `ExtractedImage` |
| 285–292 | **行号策略**：首行 + 每 10 的倍数行打印 `N→`；中间行只打正文（concise 同款） |
| 294–310 | 有尾 `\n` 时处理 trailing empty line |
| 311–319 | `raw_output = file_content[start..end]`，统一 `\r\n` → `\n` |
| 320–325 | 返回 `ExtractedContent` |

---

# 九、`mod.rs` — `run_read_file` 主流程（第 327–577 行）

`pub(crate) async fn run_read_file(...)` — `ReadFileTool` 与 `ReadFileConciseTool` 共用。

### 9.1 解析 Resources（340–350）

| 行 | 说明 |
|----|------|
| 343–346 | `cwd`：`cwd_override` 或 `Resources::Cwd` |
| 347 | `display_cwd`：给模型看的 cwd（fork 时可能不同） |
| 348 | `FileSystem` Arc |
| 349 | `PathNotFoundHints` 是否启用 |

### 9.2 路径解析（351–362）

| 行 | 说明 |
|----|------|
| 351 | `resolve_model_path` 拼相对/绝对路径 |
| 352 | `is_skill_markdown` 标记 |
| 353–362 | `try_canonicalize`；NotFound 时 `try_resolve_unicode_filename`（易混淆字符） |

### 9.3 版本与 gitignore（363–379）

| 行 | 说明 |
|----|------|
| 363–365 | `ReadFileVersion::from_contract`；legacy 跳过 gitignore |
| 366–378 | 若 `RespectGitignore` + `GitignoreFilter` 且路径被 ignore → `FileReadError` |

### 9.4 读字节与错误映射（380–416）

| 行 | 说明 |
|----|------|
| 380 | `fs.read_file(&path)` |
| 384–387 | **legacy**：任意错误 → `legacy_0_4_10::render_read_error` |
| 391–414 | **current**：按 `io_error_kind` 分 `FileNotFound` / `IsADirectory` / `PermissionDenied` / 泛化 `FileReadError`；NotFound 可走 path hints |

### 9.5 特殊文件类型分支（417–462）

| 行 | 分支 |
|----|------|
| 417–425 | `bytes_to_metadata` + `is_image()` → `image_read_output` |
| 426–446 | PDF → `handle_pdf`；成功后可选 `append_cursor_rules_for_read` |
| 447–449 | `.pptx` → `handle_pptx` |
| 450–461 | `is_binary`（扩展名或内容）→ 拒绝 |

### 9.6 纯文本路径（463–576）

| 行 | 说明 |
|----|------|
| 463 | UTF-8 lossy 转字符串 |
| 464–476 | 空文件 → 空 `FileContent` |
| 477 | `total_lines = matches('\n') + 1` |
| 478–483 | 从 `TruncationCfg` 取 `max_lines_read` |
| 484–491 | **skill markdown**：`effective_offset/limit = None`（全文件） |
| 492–497 | `extract_file_content_lines` |
| 498–546 | token 估计超 `MAX_NUM_TOKENS` → `FileTooLarge`，消息引用 grep/execute 工具名；单行超长有特殊 hint |
| 547–551 | 存储 offset/limit（skill 为 None） |
| 552–554 | `streamable_out = true`（文本路径可流式） |
| 558–566 | 再次 `append_cursor_rules_for_read` |
| 567–576 | 组装 `ReadFileOutput::FileContent` |

---

# 十、`mod.rs` — `ReadFileTool` 与 Trait 实现（第 578–719 行）

### `ReadFileTool` 结构（578–584）

零大小 `#[derive(Default, Debug)]`，无 per-instance 状态。

### `ToolMetadata`（585–598）

| 方法 | 值 |
|------|-----|
| `kind()` | `ToolKind::Read` |
| `tool_namespace()` | `GrokBuild` |
| `description_template()` | `DESCRIPTION_FULL` |
| `requires_expr()` | `Expr::True`（无额外依赖） |

### `Tool` trait（599–691）

| 行段 | 说明 |
|------|------|
| 600–601 | `Args = ReadFileInput`，`Output = ReadFileOutput` |
| 602–604 | `id()` → `"read_file"` |
| 605–613 | `description()` 用模板渲染 |
| 614–616 | `capabilities()` clone 静态 READ_FILE_CAPABILITIES |
| 617–680 | **`execute()` 流式入口** |
| 626–631 | 无 `WorkspaceViewerContext.stream_tool_progress` → 退化为单次 `run` Terminal |
| 637–678 | 否则 `read_with_streamability`；若 `streamable` 且 `FileContent` 非空 → 按 4KiB char 边界切 `stream_chunk` Progress，最后 Terminal |
| 681–690 | **`run()`** 调 `read_with_streamability`，丢弃 streamable 标志 |
| 692–718 | **`read_with_streamability`**：取 resources、cwd_override、behavior_version、invoking_param_names，调 `run_read_file` |

---

# 十一、`mod.rs` — 测试模块 `tests`（第 720–2517 行）

测试约占 **1800 行**。下面按功能分组列出每个测试的 **行号、名称、验证点**。

## 11.1 测试基础设施（733–739）

`test_resources(cwd)`：构造 `Resources`，插入 `Cwd`、`LocalFs`、`NotificationHandle::noop()`。

## 11.2 基础读写（740–996）

| 行 | 测试名 | 验证 |
|----|--------|------|
| 740–768 | `read_file_basic` | 三行文本，含行号，`total_lines=4` |
| 769–798 | `legacy_read_file_not_found_returns_exact_historical_message` | legacy 精确 `Failed to read file: {path}` |
| 799–820 | `current_read_file_not_found_returns_structured_not_found` | current → `FileNotFound` |
| 821–850 | `legacy_read_file_directory_returns_exact_historical_message` | legacy 读目录 → 泛化 FileReadError |
| 851–873 | `current_read_file_is_directory_returns_structured_error` | current → `IsADirectory` |
| 874–899 | `read_file_empty` | 空文件 content/raw 皆空，`total_lines=0` |
| 900–924 | `read_file_with_offset_and_limit` | offset=2 limit=2 只含 2、3 行 |
| 925–948 | `read_file_absolute_path` | 绝对路径可读 |
| 949–972 | `read_file_trailing_newline` | `"hello\n"` → `1→hello\n`，`total_lines=2` |
| 973–996 | `read_file_concise_output` | `content_concise` 与 content 一致 |

## 11.3 Token 上限与错误文案（997–1129）

| 行 | 测试名 | 验证 |
|----|--------|------|
| 997–1032 | `token_limit_error_references_grep` | 超大文件 → FileTooLarge，提及 Grep |
| 1033–1072 | `token_limit_error_when_range_specified_gives_better_message` | 带 offset/limit 的更具体错误 |
| 1075–1129 | `token_limit_error_uses_invoking_tool_param_names_not_kind_wide` | 用 `InvokingToolParamNames`（start_line/max_lines）而非 kind 级污染名 |

## 11.4 `extract_file_content_lines` 单元测试（1130–1211）

| 行 | 测试名 | 验证 |
|----|--------|------|
| 1130–1136 | `test_extract_file_content_lines_basic` | `\r\n` 与行号格式 |
| 1141–1167 | `extract_captures_long_inline_base64_image_before_truncation` | 50k base64 → extracted_images，正文占位 |
| 1171–1185 | `extract_captures_short_inline_base64_image_without_truncation_pressure` | 短 base64 同样提取 |
| 1187–1197 | `extract_leaves_non_data_uri_lines_untouched` | 普通代码不变 |
| 1198–1204 | `test_extract_file_content_lines_with_offset` | offset=3 从第 3 行 |
| 1205–1211 | `test_extract_file_content_lines_with_offset_and_limit` | offset+limit 组合 |

## 11.5 `FileMetadata::is_image`（1212–1265）

遍历 image / 非 image MIME，验证 `is_image()`。

## 11.6 Gitignore 行为（1266–1476）

| 行 | 辅助/测试 | 验证 |
|----|-----------|------|
| 1266–1271 | `build_gitignore` | 构造 ignore 规则 |
| 1273–1279 | `test_resources_with_gitignore` | 注入 `GitignoreFilter` |
| 1280–1318 | `read_file_allows_gitignored_files_by_default` | 默认**可读** gitignore 内文件 |
| 1319–1354 | `read_file_blocked_when_respect_gitignore_enabled` | `RespectGitignore(true)` → 拒绝 |
| 1355–1389 | `legacy_read_file_allows_gitignored_files` | legacy 始终可读 |
| 1390–1415 | `read_file_allowed_when_not_gitignored` | 非 ignore 路径正常 |
| 1416–1448 | `read_file_allows_gitignored_by_extension` | `*.log` 默认仍可读 |
| 1449–1476 | `read_file_no_gitignore_filter_allows_all` | 无 filter 时不拦截 |

## 11.7 Compaction 重读场景（1477–1628）

| 行 | 测试名 | 验证 |
|----|--------|------|
| 1477–1486 | `extract_file_content_lines_full_file` | 多行 Rust 代码格式化 |
| 1487–1493 | `extract_file_content_lines_with_offset_and_limit` | line2–line3 |
| 1494–1543 | `extract_file_content_lines_compaction_reread_scenario` | 17 行 Config 样例，第 10 行打 decade 号 |
| 1547–1602 | `reread_file_from_disk_for_compaction` | 磁盘读 auth.rs，端到端与 extract 一致 |
| 1605–1628 | `reread_file_from_disk_partial_range` | offset=4 limit=3 → line_four 起 |

## 11.8 图片压缩（1629–1764）

| 行 | 辅助/测试 | 验证 |
|----|-----------|------|
| 1634–1647 | `make_noisy_png` | 随机像素 PNG |
| 1648–1654 | `make_small_png` | 纯色小图 |
| 1655–1662 | `compress_small_image_returns_unchanged` | 小图不压 |
| 1663–1678 | `compress_large_noisy_image_picks_jpeg` | 大图 → JPEG 且 ≤ payload 上限 |
| 1679–1705 | `compress_flat_color_picks_png` | 平坦色 → 仍 PNG |
| 1706–1719 | `compress_oversized_image_preserves_aspect_ratio` | 缩放保宽高比 |
| 1723–1733 | `compress_undecodable_format_fails_closed` | 垃圾字节 → FormatDetectionFailed |
| 1734–1748 | `compress_output_never_exceeds_payload_limit` | 输出不超 cap |
| 1752–1764 | `compress_oversized_garbage_user_message_is_non_legacy` | 用户可见错误文案 |

## 11.9 Skill 文件豁免（1765–1876）

| 行 | 测试名 | 验证 |
|----|--------|------|
| 1765–1804 | `skill_file_ignores_offset_and_limit` | SKILL.md 忽略 offset/limit |
| 1805–1836 | `skill_file_skips_token_limit` | 超大 SKILL.md 不 FileTooLarge |
| 1837–1876 | `md_in_skills_dir_ignores_model_offset_and_limit` | `skills/.../reference.md` 全读 |

## 11.10 PDF `parse_page_range`（1877–1960）

| 行 | 测试名 | 验证 |
|----|--------|------|
| 1877–1879 | `parse_single_page` | `"3"` → 0-based [2] |
| 1880–1883 | `parse_page_range_inclusive` | `"2-5"` |
| 1885–1887 | `parse_open_ended_range` | `"8-"` |
| 1889–1894 | `parse_comma_separated_mixed` | `"1,3,7-9"` |
| 1896–1898 | `parse_deduplicates_and_sorts` | 去重排序 |
| 1900–1903 | `parse_page_range_clamps_open_end` | 开区间钳到文档末 |
| 1904–1908 | `parse_page_range_rejects_zero` | 页 0 非法 |
| 1909–1913 | `parse_page_range_rejects_beyond_count` | 超页数 |
| 1914–1918 | `parse_page_range_rejects_start_gt_end` | 倒序范围 |
| 1919–1923 | `parse_page_range_rejects_invalid_number` | 非数字 |
| 1924–1928 | `parse_page_range_rejects_empty` | 空串 |
| 1929–1933 | `parse_page_range_rejects_only_commas` | `,,,` |
| 1934–1938 | `parse_page_range_rejects_too_many_pages` | >20 页 |
| 1939–1943 | `parse_page_range_max_pages_ok` | 恰好 20 页 OK |
| 1944–1947 | `parse_page_range_whitespace_tolerance` | 空格容忍 |
| 1948–1951 | `parse_page_range_single_page_doc` | 单页文档 |
| 1952–1955 | `parse_page_range_range_clamped_to_doc_end` | `1-100` 钳到 5 页 |
| 1956–1960 | `parse_page_range_zero_page_doc` | 0 页文档报错 |

## 11.11 二进制 / PDF 检测（1961–2055）

| 行 | 辅助/测试 | 验证 |
|----|-----------|------|
| 1961–1975 | `run_read_file_on` | 写临时文件并 run |
| 1976–1988 | `read_file_binary_rejected` | `.zip` 拒绝 |
| 1989–2003 | `read_file_binary_by_content` | 无扩展名但二进制内容拒绝 |
| 2004–2016 | `pdf_detection_by_extension` | 假 .pdf |
| 2017–2029 | `pdf_detection_by_magic_bytes` | `%PDF` 魔数 |
| 2030–2044 | `pdf_size_gate_rejects_oversized` | 超 MAX_PDF_BYTES |
| 2045–2055 | `pdf_not_caught_by_binary_guard` | pdf 不在 BINARY_EXTENSIONS |

## 11.12 流式 `execute`（2056–2285）

| 行 | 辅助/测试 | 验证 |
|----|-----------|------|
| 2056–2085 | `execute_collect` | 消费 stream，收集 `read_file_chunk` deltas + Terminal |
| 2087–2124 | `read_file_streams_formatted_text_prefix` | 300 行文件，deltas 拼接 == terminal content |
| 2126–2147 | `read_file_streaming_suppressed_when_gate_off` | 无 stream 上下文 → 无 Progress |
| 2150–2173 | `read_file_pdf_text_path_is_terminal_only` | PDF text 不流式 |
| 2176–2199 | `read_file_pdf_image_path_is_terminal_only` | PDF 默认 image → PdfPageImages，不流式 |
| 2202–2245 | `read_file_concurrent_text_and_pdf_text_do_not_cross_talk` | 并发 10 次，streamable 标志不串 |
| 2248–2285 | `read_file_streams_oversized_line_without_cap_break` | 20k 字符单行，多 delta 且每块 ≤4KiB |

## 11.13 单行超大与 FileTooLarge 提示（2286–2417）

| 行 | 测试名 | 验证 |
|----|--------|------|
| 2291–2318 | `single_line_payload_reads_in_full_by_default` | ~49.5KB JSON 单行全读（回归 death spiral） |
| 2319–2337 | `read_huge_file` | 辅助：可选 execute 工具名 |
| 2341–2359 | `oversized_single_line_gets_shell_hint` | 120KB 单行 → 提示用 run_terminal_command |
| 2362–2373 | `oversized_single_line_hint_suppressed_without_execute_tool` | 无 execute 工具则无 hint |
| 2377–2403 | `oversized_narrowed_window_single_line_gets_shell_hint` | offset/limit 仍落在超长单行 |
| 2405–2417 | `oversized_multi_line_gets_standard_guidance` | 多行超大 → offset/limit 指引 |

## 11.14 Schema 与负 offset 边界（2418–2517）

| 行 | 测试名 | 验证 |
|----|--------|------|
| 2418–2431 | `read_file_offset_schema_advertises_start_default` | 源码含 schema 描述与 default |
| 2432–2435 | `resolve_read_start_line_negative_trailing_newline` | `-3` on `a\nb\nc\n` → 行 2 |
| 2436–2439 | `resolve_read_start_line_negative_no_trailing_newline` | 无尾换行同上 |
| 2440–2443 | `resolve_read_start_line_very_negative_clamps_to_one` | -999 → 1 |
| 2444–2447 | `resolve_read_start_line_zero_is_one` | 0 → 1 |
| 2448–2454 | `extract_file_content_lines_negative_offset` | offset=-2 limit=2 → 仅 line5 |
| 2455–2460 | `extract_first_line_always_numbered_small_read` | 首行必带 `1→` |
| 2461–2466 | `extract_first_visible_line_numbered_with_offset` | offset=3 → `3→c` |
| 2467–2475 | `extract_decade_line_numbered_in_addition_to_first` | 10、20… 打行号 |
| 2479–2484 | `extract_empty_only_window_still_anchored` | 仅空行窗口 → `2→` |
| 2488–2500 | `extract_file_content_lines_negative_one_no_trailing_newline_stable` | phantom 行 → 空 content |
| 2501–2509 | `read_file_input_accepts_negative_offset_json` | JSON 数字/字符串负 offset |
| 2510–2516 | `stored_read_offset_drops_negatives` | 存储层丢弃负 offset |

---

# 十二、调用链总览

```text
模型 tool_call: read_file
  → ReadFileTool::execute / run
    → read_with_streamability
      → run_read_file
        ├─ 路径 / gitignore / 读字节
        ├─ 图片 → implementations/read_file/image
        ├─ PDF  → handle_pdf
        ├─ PPTX → handle_pptx → pptx::
        ├─ 二进制 → 拒绝
        └─ 文本 → extract_file_content_lines → FileContent
      → (execute) stream_chunk 分片 Progress
  → session: push_tool_result + 可选 extracted_images → vision
```

---

# 十三、与父模块 `implementations/read_file/` 的分工

| 父模块 | grok_build/read_file 如何使用 |
|--------|------------------------------|
| `metadata.rs` | `bytes_to_metadata` 嗅探 MIME |
| `image.rs` | `image_read_output`、`compress_image_for_conversation`（测试） |
| `pdf.rs` | `handle_pdf`、`is_pdf_file`、`parse_page_range` |
| `pptx.rs` | `extract_pptx_text_from_bytes` |
| `mod.rs`（父） | `run_document_extraction`、`raw_text_to_file_content` |

本目录 **不重复实现** PDF/图片/PPTX 解析，只编排 Resources、版本策略、行号格式、流式与 skill 豁免。

---

# 十四、阅读建议

1. 先读 **`run_read_file`（332–577）** — 主分支一目了然  
2. 再读 **`extract_file_content_lines`（226–326）** — 行号与 base64 提取  
3. 读 **`ReadFileTool::execute`（621–680）** — 流式协议  
4. 需要兼容历史时看 **`versions/legacy_0_4_10.rs`**  
5. 行为边界以 **`tests` 模块** 为准（尤其 gitignore、skill、token、streaming）

---

# 附录 A：`extract_file_content_lines` 逐行（226–326）

| 行 | 代码要点 | 说明 |
|----|----------|------|
| 226 | `pub fn extract_file_content_lines(...)` | 公开函数；`total_lines` 由调用方传入（用于空文件 phantom 行） |
| 232–240 | `fn strip(s)` | 去掉行尾 `\n` 或 `\r\n`，保留行内内容 |
| 241–242 | `use Cow`、`write!` | 可能拥有替换后的行（base64 剥离） |
| 243–244 | `output`、`output_concise` | 两个并行 buffer，目前内容相同 |
| 245 | `(mut start, mut end) = (0, 0)` | raw_output 切片边界 |
| 246 | `first_line: Option<usize>` | 是否已输出首行号 |
| 247 | `extracted_images` | base64 图收集器 |
| 248–249 | `split_count`、`has_trailing_empty` | 用于 trailing `\n` 产生的空行 |
| 250 | `skip = resolve_read_start_line(...).saturating_sub(1)` | 0-based 跳过行数 |
| 251 | `take = limit.unwrap_or(usize::MAX)` | 最多取几行 |
| 252–256 | 空 content 特殊分支 | `total_lines>0` 且读第一行 → 只输出 `1→` |
| 257–264 | `split_inclusive('\n').scan(...)` | 记录每行 byte offset 与 strip 后内容 |
| 265–267 | `.enumerate().skip(skip).take(take)` | 窗口切片 |
| 269 | `is_first_visible` | 窗口内第一行 |
| 270–272 | 记录 `start`、`first_line` | raw_output 起点 |
| 273–276 | 非首行先 `\n` | 行间分隔 |
| 277 | `end = pos + line_len` | raw 终点 |
| 278–284 | `try_extract_base64_images` | 有 data URI → 占位 + 收集 image |
| 285 | `line_num = i + 1` | 1-based 行号（全文件坐标，非窗口内） |
| 286–292 | 行号打印策略 | 首行或 `line_num % 10 == 0` → `N→`；否则只追加正文 |
| 294–310 | `has_trailing_empty` 块 | 文件以 `\n` 结尾时补 phantom 空行 |
| 311–315 | 无可见行 → 空 raw | |
| 316–319 | `\r\n` 归一化为 `\n` | |
| 320–325 | 构造 `ExtractedContent` | |

---

# 附录 B：`run_read_file` 逐段行号索引（332–577）

| 行 | 阶段 |
|----|------|
| 332–339 | 函数签名：`contract_version`、`streamable_out`、`invoking_param_names` |
| 340–350 | 锁 Resources 取 cwd/fs/hints |
| 351–362 | 路径解析 + unicode 文件名 |
| 363–379 | 版本 + gitignore 门控 |
| 380–416 | `read_file` + 错误分型 |
| 417–425 | 图片早返回 |
| 426–446 | PDF + cursor rules |
| 447–449 | PPTX |
| 450–461 | 二进制拒绝 |
| 463–476 | 空文本 |
| 477–491 | total_lines、max_lines、skill 豁免 |
| 492–497 | extract |
| 498–546 | token 上限 + FileTooLarge 文案 |
| 547–554 | streamable 标记 |
| 555–566 | cursor rules（文本路径） |
| 567–576 | 成功 FileContent |
