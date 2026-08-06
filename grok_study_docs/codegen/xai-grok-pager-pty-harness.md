# xai-grok-pager-pty-harness

> 路径：`crates/codegen/xai-grok-pager-pty-harness`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Shared PTY harness + scenario library for xai-grok-pager e2e tests and benchmarks.

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `content` | Layer 3: Content controller. |
| `env` | Environment helpers for benchmarking and testing. |
| `flows` | Cross-suite e2e flow helpers over [`PtyHarness`] / [`ContentController`]. |
| `host_clipboard` | REAL host-clipboard plumbing shared by the OS-native paste e2e tests and |
| `leader` | Multi-client leader cluster: one shared leader, N pager clients, plus |
| `pty` | Layer 1: PTY management — spawn, inject keys, resize, drain output. |
| `results` | Aggregated benchmark results, percentile computation, baseline compare. |
| `scenarios` | Named scenarios that drive content into the pager and measure frame timing. |
| `screen` | Layer 2a: Screen state tracking via `alacritty_terminal` (ptyctl). |
| `scripted` | Scripted TUI scenario runner for xai-grok-pager. |
| `scroll_matrix` | Scroll validation matrix. |
| `timing` | Layer 2b: Frame timing via VTE parser. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `content.rs` | 519 | Layer 3: Content controller. | struct`AgentTurnExpectation`, struct`ContentController` |
| `env.rs` | 100 | Environment helpers for benchmarking and testing. | fn`pager_binary` |
| `flows.rs` | 137 | Cross-suite e2e flow helpers over [`PtyHarness`] / [`Co | fn`wait_for_labels_absent`, fn`submit_turn`, fn`inference_request_coun |
| `host_clipboard.rs` | 198 | REAL host-clipboard plumbing shared by the OS-native pa | fn`pbcopy`, fn`pbcopy`, fn`pbpaste`, fn`pbpaste`, fn`set_clipboard_png |
| `leader.rs` | 237 | Multi-client leader cluster: one shared leader, N pager | struct`LeaderCluster` |
| `lib.rs` | 758 | Unified PTY harness for xai-grok-pager. | struct`PtyHarness` |
| `pty.rs` | 1089 | Layer 1: PTY management — spawn, inject keys, resize, d | struct`PtyController`, enum`EnvOp`, enum`PtyRead`, enum`PtyExitPoll` |
| `results.rs` | 266 | Aggregated benchmark results, percentile computation, b | fn`load_baseline`, fn`write_baseline`, fn`compare_baseline`, fn`percen |
| `screen.rs` | 186 | Layer 2a: Screen state tracking via `alacritty_terminal | struct`ScreenTracker` |
| `scripted.rs` | 2362 | Scripted TUI scenario runner for xai-grok-pager. | fn`count_kitty_graphics`, struct`ScriptedScenario`, struct`TerminalCon |
| `timing.rs` | 128 | Layer 2b: Frame timing via VTE parser. | struct`FrameTiming`, struct`FrameTimingParser` |

### `bin/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `pty_scenario.rs` | 65 | `pty-scenario` — scripted TUI regression runner for xai | — |
| `scroll_matrix.rs` | 151 | `scroll-matrix` — scroll validation matrix sweep for xa | — |

### `scenarios/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `empty_enter_send_now.rs` | 133 | Empty-composer Enter sends the top mid-turn queued foll | fn`assert_empty_enter_force_sends_top_queued` |
| `idle_cost.rs` | 30 | `idle_cost` — after content settles, measure frames for | fn`run` |
| `large_codeblock.rs` | 52 | `large_codeblock` — render a large syntax-highlighted R | fn`run` |
| `mixed_interaction.rs` | 46 | `mixed_interaction` — scroll while streaming. The real- | fn`run` |
| `mod.rs` | 90 | Named scenarios that drive content into the pager and m | fn`wait_for_welcome`, enum`Scenario` |
| `plan_approval_resume.rs` | 202 | Plan-approval chrome restored by the shell after quit + | fn`assert_plan_approval_restored_after_resume` |
| `resize_storm.rs` | 42 | `resize_storm` — resize the PTY many times in quick suc | fn`run` |
| `scroll_stress.rs` | 65 | `scroll_stress` — inject rapid `j` keys against a large | fn`run` |
| `streaming_render.rs` | 53 | `streaming_render` — stream a response and measure fram | fn`run` |

### `scroll_matrix/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `cells.rs` | 403 | The cell table: terminal-class × config × gesture rows  | fn`curated`, struct`ExpectedProfile`, struct`MatrixCell`, enum`Tier` |
| `gestures.rs` | 371 | Gesture step tables G1–G11: the timed SGR wheel-report  | fn`direction_counts`, struct`WheelStep`, enum`GestureId` |
| `invariants.rs` | 800 | The invariant suite: predicates over grouped `GROK_SCRO | fn`check_log_invariant`, enum`InvariantId`, enum`InvariantResult` |
| `log.rs` | 502 | `GROK_SCROLL_LOG` JSONL parsing, per-stream grouping, a | fn`parse_jsonl`, fn`parse_jsonl_str`, fn`group_streams`, fn`wait_for_f |
| `mod.rs` | 52 | Scroll validation matrix. | — |
| `report.rs` | 283 | Cell verdicts: the `report.json` artifact, the stdout s | fn`exit_code`, fn`write_report_json`, fn`summary_table`, struct`Invari |
| `runner.rs` | 462 | The per-cell executor: spawn the primed pager, replay t | fn`run_cell` |
| `session.rs` | 322 | Marker-session preambles: spawn the pager into the prim | fn`marker_line`, fn`marker_response`, fn`marker_screen_row`, fn`topmos |

---

## 4. 工作区依赖

`xai-grok-test-support` · `xai-tty-utils`

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-pager-pty-harness`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-pager-pty-harness
cargo test -p xai-grok-pager-pty-harness
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

