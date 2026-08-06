# xai-grok-pager-minimal

> 路径：`crates/codegen/xai-grok-pager-minimal`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

`xai-grok-pager-minimal` 是 Grok Build codegen 工作区成员。

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `auth` | Minimal-mode sign-in / folder-trust rendering for the live region. |
| `commit` | Minimal-mode commit pipeline: which finalized blocks get printed into the |
| `full_view` | Minimal-mode "full view": the complete conversation rendered with **every** |
| `live` | Minimal-mode live region: the small pinned viewport holding the running-turn |
| `overlay` | Minimal-mode inline-overlay host (design K11 / §6.8). |
| `panel` | Minimal-mode below-prompt **list panels**: `/resume` (session picker) and |
| `plan` | Minimal-mode plan-approval host (design PR10). |
| `todo` | Minimal-mode todo panel: the persistent list shown directly above the prompt |
| `welcome` | Minimal-mode welcome card. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `auth.rs` | 545 | Minimal-mode sign-in / folder-trust rendering for the l | — |
| `commit.rs` | 1395 | Minimal-mode commit pipeline: which finalized blocks ge | fn`is_committable`, fn`minimal_commit_display_mode`, fn`scan_frontier` |
| `full_view.rs` | 447 | Minimal-mode "full view": the complete conversation ren | fn`pump_transcript` |
| `guard.rs` | 45 | Compile-time guard for minimal mode's resize strategy ( | — |
| `lib.rs` | 118 | Minimal (scrollback-native) render mode — `grok --minim | fn`draw`, fn`install` |
| `live.rs` | 989 | Minimal-mode live region: the small pinned viewport hol | fn`draw_live` |
| `overlay.rs` | 1089 | Minimal-mode inline-overlay host (design K11 / §6.8). | fn`overlay_rows`, fn`sync_viewport`, fn`render`, fn`active_modal`, fn` |
| `panel.rs` | 801 | Minimal-mode below-prompt **list panels**: `/resume` (s | — |
| `plan.rs` | 264 | Minimal-mode plan-approval host (design PR10). | fn`maybe_commit_plan`, fn`height`, fn`render` |
| `todo.rs` | 279 | Minimal-mode todo panel: the persistent list shown dire | — |
| `welcome.rs` | 143 | Minimal-mode welcome card. | fn`maybe_commit_welcome` |

---

## 4. 工作区依赖

`xai-grok-pager` · `xai-ratatui-inline` · `xai-grok-shell` · `xai-grok-version` · `xai-token-estimation`

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-pager-bin` · `xai-grok-pager-minimal`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-pager-minimal
cargo test -p xai-grok-pager-minimal
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

