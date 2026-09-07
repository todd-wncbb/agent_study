# xai-grok-markdown

> 路径：`crates/codegen/xai-grok-markdown`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Streaming markdown renderer for terminal UIs

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `checkpoint` | Checkpoint types for incremental markdown rendering. |
| `streaming` | Streaming/incremental markdown renderer. |
| `style` | Markdown styling types. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `buffers.rs` | 373 | Reusable buffers and internal data types for markdown p | fn`unicode_display_width`, fn`floor_char_boundary`, fn`ceil_char_bound |
| `checkpoint.rs` | 58 | Checkpoint types for incremental markdown rendering. | struct`Checkpoint`, enum`CheckpointKind` |
| `colors.rs` | 451 | Terminal color support detection and color conversion u | fn`detect_color_level`, fn`set_color_level_cap`, fn`set_polarity_safe_ |
| `hyperlinks.rs` | 784 | Project parser-emitted `LinkTarget`s onto rendered disp | fn`source_to_chunk_offset`, fn`chunk_link_offsets`, fn`emit_segment_hy |
| `latex_delimiters.rs` | 1305 | Streaming normalization of LaTeX math delimiters into t | fn`normalize_latex_delimiters`, struct`LatexDelimiterNormalizer` |
| `lib.rs` | 183 | Streaming markdown renderer for terminal UIs. | fn`render_markdown_ratatui_full`, fn`render_markdown_ratatui_with_buff |
| `mermaid.rs` | 5237 | Self-contained terminal renderer for Mermaid diagrams. | fn`render`, struct`MermaidStyles`, struct`MermaidArt` |
| `open_code_highlighter.rs` | 439 | Streaming-render syntect caches for fenced code blocks  | struct`OpenCodeHighlighter` |
| `output.rs` | 602 | Render output types for markdown. | fn`build_code_block_spans`, struct`HyperlinkTarget`, struct`CodeBlockS |
| `parse.rs` | 2057 | Markdown parser - transforms markdown text into styled  | fn`cell_word_separator`, struct`MarkdownParser`, struct`ParsedMarkdown |
| `render.rs` | 2753 | Markdown renderer - transforms parsed markdown buffers  | — |
| `source_map.rs` | 141 | Source mapping for rendered markdown back to original s | struct`SourceMap` |
| `streaming.rs` | 2910 | Streaming/incremental markdown renderer. | struct`FrozenState`, struct`StreamingMarkdownRenderer` |
| `style.rs` | 303 | Markdown styling types. | fn`all_hidden`, fn`merge_styles`, struct`TableBorders`, struct`Markdow |
| `syntax.rs` | 211 | Syntax highlighting support using syntect. | fn`syntax_highlight_raw`, fn`test_syntect`, struct`Syntect` |
| `url_scan.rs` | 429 | Plain-URL detection over rendered display ratatui Lines | fn`detect_plain_urls`, fn`detect_plain_urls_with_offset` |

### `latex/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `commands.rs` | 532 | Core renderer: sequences, commands, scripts, fractions, | — |
| `cursor.rs` | 99 | Byte cursor over TeX source. | — |
| `environments.rs` | 325 | `\\begin{env}...\\end{env}` environments: matrices, cas | — |
| `math_box.rs` | 156 | Two-dimensional math layout box. | — |
| `mod.rs` | 96 | Best-effort LaTeX math → Unicode plain-text conversion. | fn`latex_to_unicode_inline`, fn`latex_to_unicode_display` |
| `symbols.rs` | 412 | Character and symbol mapping tables. | — |
| `tests.rs` | 370 | — | — |

---

## 4. 工作区依赖

`xai-grok-markdown-core`

---

## 5. 被谁依赖

`xai-grok-markdown` · `xai-grok-markdown-core` · `xai-grok-pager` · `xai-grok-pager-render`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-markdown
cargo test -p xai-grok-markdown
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

