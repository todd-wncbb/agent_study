# xai-grok-mermaid

> 路径：`crates/codegen/xai-grok-mermaid`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Render Mermaid diagram source to a rasterized PNG behind a swappable engine trait

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `engine.rs` | 298 | The [`MermaidEngine`] trait, error type, resource limit | fn`render_checked`, struct`RenderLimits`, enum`MermaidError`, trait`Me |
| `lib.rs` | 261 | Render [Mermaid](https://mermaid.js.org/) diagram sourc | fn`default_engine`, struct`Rgba`, struct`RenderParams`, struct`Rendere |
| `mmdc.rs` | 257 | Optional `mmdc` (mermaid-cli) engine, detected at runti | fn`detect_mmdc`, struct`MmdcEngine` |
| `pure.rs` | 284 | Pure-Rust engine: Mermaid source -> SVG via the vendore | struct`PureRustEngine` |
| `raster.rs` | 580 | SVG -> PNG rasterization via `resvg`/`usvg`/`tiny-skia` | fn`rasterize` |
| `subprocess.rs` | 310 | Shared subprocess plumbing: spawn a child, optionally f | fn`run_with_timeout`, enum`SubprocessError` |

---

## 4. 工作区依赖

`xai-tty-utils`

---

## 5. 被谁依赖

`xai-grok-mermaid` · `xai-grok-pager`

---

## 6. 开发命令

```sh
cargo check -p xai-grok-mermaid
cargo test -p xai-grok-mermaid
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

