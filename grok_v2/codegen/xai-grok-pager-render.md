# xai-grok-pager-render

> 路径：`crates/codegen/xai-grok-pager-render`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

`xai-grok-pager-render` 是 Grok Build codegen 工作区成员。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `appearance` | Hot-reloadable appearance configuration. |
| `clipboard` | Clipboard providers for copy/paste. |
| `gboom` | `/gboom` easter egg: a tiny single-level raycaster shooter rendered in |
| `glyphs` | Legacy-console fallbacks for chrome glyphs that don't ship in the |
| `host` | Host platform and display server classification. |
| `link_opener` | Shared URL-opening and scheme validation utilities. |
| `modal_window_state` | Pure data types for modal window chrome state. |
| `prompt_images` | Shared prompt-side image types and helpers. |
| `render` | Low-level rendering utilities. |
| `syntax` | Syntax highlighting initialization. |
| `terminal` | Terminal detection utilities. |
| `theme` | Theming for the pager. |
| `util` | Shared utility functions. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `glyphs.rs` | 819 | Legacy-console fallbacks for chrome glyphs that don't s | fn`prompt_arrow`, fn`record_dot`, fn`collapsed_accent`, fn`ballot_x`,  |
| `lib.rs` | 13 | — | — |
| `link_opener.rs` | 586 | Shared URL-opening and scheme validation utilities. | fn`browser_open_likely_available_from_env`, fn`browser_open_likely_ava |
| `modal_window_state.rs` | 84 | Pure data types for modal window chrome state. | struct`ModalWindowState`, struct`ShortcutHitArea` |
| `prompt_images.rs` | 4808 | Shared prompt-side image types and helpers. | fn`decode_image_dimensions`, fn`load_image_data`, fn`extract_poster_fr |
| `syntax.rs` | 269 | Syntax highlighting initialization. | fn`syntect_to_ratatui_fg`, fn`syntect_rgb_to_fg`, fn`polarity_safe_syn |
| `util.rs` | 484 | Shared utility functions. | fn`pager_toml_path`, fn`display_grok_home_prefix`, fn`display_user_gro |

### `appearance/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `cache.rs` | 1190 | Thread-local caches for the pager's UI settings. | fn`load`, fn`set`, fn`load_timestamps`, fn`set_timestamps`, fn`load_sh |
| `config.rs` | 2467 | Appearance configuration for the pager. | fn`persist_respect_manual_folds`, struct`AppearanceConfig`, struct`Tur |
| `mod.rs` | 54 | Hot-reloadable appearance configuration. | fn`tab_width`, fn`set_tab_width` |
| `permission_cursor.rs` | 455 | Permission-prompt cursor preselection. | fn`load_default_selected_permission`, fn`set_default_selected_permissi |
| `render_mermaid.rs` | 72 | The `render_mermaid` user setting (`auto | on | off`). | enum`RenderMermaid` |
| `scroll_mode.rs` | 67 | The `scroll_mode` user setting (`auto` | `wheel` | `tra | enum`ScrollMode` |
| `text_selection.rs` | 102 | The `keep_text_selection` user setting (`flash` | `hold | enum`TextSelection` |
| `watcher.rs` | 72 | File watcher for appearance configuration. | struct`ConfigWatcher` |

### `clipboard/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 2349 | Clipboard providers for copy/paste. | fn`osc52_sink_active`, fn`osc52_disabled`, fn`clipboard_route`, fn`way |
| `trust.rs` | 602 | Environment-based delivery and toast policy for clipboa | fn`native_clipboard_preflight`, fn`osc52_delivery`, fn`expected_delive |

### `gboom/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `assets.rs` | 711 | Procedural art assets for the `/gboom` easter egg. | — |
| `engine.rs` | 728 | Software renderer for the `/gboom` easter egg. | — |
| `game.rs` | 916 | World simulation for the `/gboom` easter egg: map, mome | — |
| `mod.rs` | 723 | `/gboom` easter egg: a tiny single-level raycaster shoo | struct`GboomHud`, struct`GboomState`, enum`GboomKeyOutcome` |

### `host/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `display_refresh.rs` | 505 | One-shot primary-display refresh probe (OnceLock-cached | fn`probe_display_refresh`, struct`DisplayRefreshProbeResult`, enum`Dis |
| `mod.rs` | 185 | Host platform and display server classification. | fn`collect_unicode_env`, fn`unicode_env_from_os`, enum`HostOs`, enum`D |

### `render/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `color.rs` | 652 | Color blending and fading utilities. | fn`indexed_to_rgb`, fn`nearest_indexed`, fn`resolve_to_rgb`, fn`blend_ |
| `draw.rs` | 771 | Frame drawing with cursor blink preservation. | fn`spawn_writer_thread`, fn`draw_frame`, struct`WriterSync`, struct`Wr |
| `gboom_overlay.rs` | 169 | `/gboom` easter-egg overlay chrome (border, title, HUD  | fn`render_gboom_overlay` |
| `highlight.rs` | 77 | Match-highlight overlay shared by the list pane and oth | fn`paint_match_highlights` |
| `image_overlay.rs` | 223 | Image preview overlay for prompt image chips. | fn`render_image_overlay` |
| `line_utils.rs` | 605 | Line and string utility functions for ratatui text mani | fn`line_to_static`, fn`push_owned_lines`, fn`is_unsafe_display_char`,  |
| `mod.rs` | 22 | Low-level rendering utilities. | — |
| `osc8.rs` | 1829 | Link detection for the scrollback render pass. | fn`resolve_link_target`, fn`resolve_link_target_with_presentation`, fn |
| `preview_overlay.rs` | 604 | Multiline preview overlay widget. | fn`render_preview_overlay`, struct`PreviewStyle`, struct`PreviewConfig |
| `renderable.rs` | 209 | The [`Renderable`] trait for self-rendering content. | enum`RenderableItem`, trait`Renderable` |
| `safe_buf.rs` | 52 | Bounds-checked buffer helpers. | trait`SafeBuf` |
| `scrollbar.rs` | 521 | Smooth scrollbar widget with follow-mode awareness. | fn`set_scrollbars_hidden`, fn`scrollbars_hidden`, fn`split_area_for_sc |
| `terminal_output.rs` | 540 | Native terminal rendering for command output. | fn`render_terminal_lines`, fn`render_terminal_plain`, struct`RenderedL |
| `tool_paths.rs` | 449 | Read/Edit tool-path resolution and surface formatting. | fn`resolve_tool_path_target_with_home`, fn`resolve_tool_path_target`,  |
| `video_overlay.rs` | 148 | Video playback overlay chrome (border, title, progress  | fn`render_video_overlay` |
| `wrapping.rs` | 1559 | Text wrapping utilities with style preservation. | fn`wrap_ranges_trim`, fn`byte_range_to_row_cols`, fn`byte_offset_to_di |

### `render/image_overlay/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `content.rs` | 87 | — | — |
| `geometry.rs` | 101 | — | — |
| `tests.rs` | 201 | — | — |

### `terminal/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `embedded_editor.rs` | 108 | Detects whether grok is running inside an editor's embe | fn`embedded_editor_from_env`, enum`EmbeddedEditor` |
| `hyperlinks.rs` | 306 | Per-terminal hyperlink (OSC 8) capabilities. | fn`hyperlink_capabilities`, struct`HyperlinkCapabilities`, struct`SetP |
| `image.rs` | 651 | Terminal inline image rendering (Kitty / iTerm2 protoco | fn`set_inline_overlay_force_off`, fn`scrollback_inline_overlay_forced_ |
| `keyboard.rs` | 220 | Per-terminal keyboard input capabilities. | fn`keyboard_capabilities`, fn`keyboard_capabilities_for_host`, struct` |
| `mod.rs` | 1101 | Terminal detection utilities. | fn`env_from`, fn`kitty_flags_pushed`, fn`set_kitty_flags_pushed`, fn`t |
| `overlay.rs` | 301 | — | fn`next_owner_id`, fn`reset_owner`, fn`static_image_for_protocol`, fn` |
| `probe.rs` | 159 | Shared startup terminal-probe primitive: write a query, | fn`write_query`, fn`read_tty_reply` |
| `test.rs` | 1957 | — | — |
| `tmux_probe.rs` | 438 | Shared tmux command protocol and result parsing. | fn`query_version`, fn`query_option`, fn`query_option_support`, fn`quer |
| `xtversion.rs` | 212 | Runtime XTVERSION probe (`CSI > 0 q` → `DCS > | text ST | fn`detected`, fn`reply_pending`, fn`record_reply`, fn`record_no_reply` |

### `terminal/image/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `tests.rs` | 121 | — | — |

### `theme/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `cache.rs` | 674 | In-memory theme cache + resolution. | fn`current_kind`, fn`set`, fn`terminal_native_locked`, fn`set_terminal |
| `color_support.rs` | 513 | Terminal color support detection and quantization. | fn`detect`, fn`standalone`, fn`get`, fn`set`, fn`quantize_color` |
| `grokday.rs` | 140 | GrokDay theme — neutral gray base (light) with deepened | — |
| `groknight.rs` | 166 | GrokNight theme — neutral gray base with TokyoNight acc | — |
| `md_style.rs` | 198 | Theme-aware markdown rendering style. | fn`style` |
| `mod.rs` | 1176 | Theming for the pager. | fn`canonical_name`, fn`display_name_for_canonical`, fn`apply_cursor_co |
| `osc11.rs` | 466 | OSC 11 terminal background detection. | fn`detect_via_osc11`, fn`classify_luminance`, fn`parse_osc11_rgb` |
| `oscura.rs` | 149 | Oscura Midnight palette. | — |
| `rosepine.rs` | 117 | — | — |
| `system_appearance.rs` | 415 | System appearance detection for automatic day/night the | fn`detect`, fn`detect_with_osc11_fallback`, fn`to_theme_kind`, fn`set_ |
| `terminal_default.rs` | 302 | Terminal-native palette for minimal mode. | — |
| `tokyonight.rs` | 350 | TokyoNight theme for the pager. | fn`wave_brightness`, fn`pulse_brightness`, struct`Theme` |

---

## 4. 工作区依赖

`xai-tty-utils` · `xai-grok-markdown` · `xai-ratatui-textarea` · `xai-ratatui-inline` · `xai-grok-config` · `xai-grok-shared` · `xai-grok-workspace` · `xai-grok-telemetry` · `xai-grok-tools` · `xai-grok-paths`

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-pager-render`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-pager-render
cargo test -p xai-grok-pager-render
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

