# xai-grok-voice

> 路径：`crates/codegen/xai-grok-voice`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Voice dictation (streaming STT) for Grok Build CLI

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

| 模块 | 说明 |
| --- | --- |
| `audio` | Microphone capture (optional `audio` feature). |
| `auth` | Bearer resolution for voice STT requests. |
| `config` | Default STT capture rate (Hz). Shared with the `__mic-capture` helper's |
| `error` | — |
| `event` | Events emitted by [`crate::pipeline::run_voice_pipeline`] to the pager event loo |
| `language` | Grok Speech-to-Text language codes. |
| `pipeline` | Voice pipeline: mic → streaming STT → pager events. |
| `probe` | Voice diagnostics: input-device lookup, silent-mic fix text, and an |
| `stt` | xAI Speech-to-Text: streaming `wss://api.x.ai/v1/stt`. |

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `auth.rs` | 82 | Bearer resolution for voice STT requests. | fn`require_bearer`, struct`StaticVoiceAuth`, trait`VoiceAuthProvider` |
| `config.rs` | 292 | Default STT capture rate (Hz). Shared with the `__mic-c | struct`VoiceConfig` |
| `error.rs` | 16 | — | enum`VoiceError` |
| `event.rs` | 17 | Events emitted by [`crate::pipeline::run_voice_pipeline | enum`VoiceEvent` |
| `language.rs` | 311 | Grok Speech-to-Text language codes. | fn`stt_language_by_code`, fn`canonicalize_stt_language`, fn`language_f |
| `lib.rs` | 121 | Voice input for Grok Build CLI: an xAI streaming STT cl | fn`maybe_run_capture_subprocess` |
| `pipeline.rs` | 418 | Voice pipeline: mic → streaming STT → pager events. | fn`run_voice_pipeline`, enum`VoiceCommand` |
| `probe.rs` | 206 | Voice diagnostics: input-device lookup, silent-mic fix  | fn`run_streaming_probe`, fn`run_mic_only_probe`, fn`run_streaming_prob |

### `audio/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `capture.rs` | 673 | PCM16 mono capture via cpal for streaming STT. | fn`spawn_pcm_capture`, fn`input_device_info`, fn`capture_pcm_for_durat |
| `capture_linux.rs` | 348 | Microphone capture on Linux via a subprocess recorder. | fn`spawn_pcm_capture`, fn`input_device_info`, fn`capture_pcm_for_durat |
| `capture_subprocess.rs` | 395 | Microphone capture on macOS via a short-lived self-exec | fn`spawn_pcm_capture`, fn`input_device_info`, enum`CaptureHandle` |
| `mod.rs` | 53 | Microphone capture (optional `audio` feature). | — |
| `pipe.rs` | 169 | Shared PCM-over-pipe plumbing for the subprocess captur | struct`ChildCaptureHandle` |
| `protocol.rs` | 62 | Wire protocol between the `__mic-capture` helper child  | — |

### `bin/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `voice_probe.rs` | 172 | Standalone voice debug harness: mic → streaming STT → t | — |

### `stt/`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `mod.rs` | 7 | xAI Speech-to-Text: streaming `wss://api.x.ai/v1/stt`. | — |
| `streaming.rs` | 330 | Events delivered from an active streaming STT session. | struct`StreamingSttSession`, enum`StreamingSttEvent` |
| `types.rs` | 71 | Parsed server → client STT WebSocket events. | struct`SttTranscriptPartial`, enum`SttServerEvent` |

---

## 4. 工作区依赖

`xai-tty-utils`

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-voice`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-voice
cargo test -p xai-grok-voice
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

