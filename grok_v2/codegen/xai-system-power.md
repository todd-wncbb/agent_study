# xai-system-power

> 路径：`crates/codegen/xai-system-power`
> 版本：0.1.0
> 类型：库 crate

---

## 1. 概述

Cross-platform system sleep/wake (suspend) notifications — used to defer work across a suspend boundary

---

## 2. 顶层模块 (`lib.rs` / `main.rs`)

（无可解析 `pub mod`）

---

## 3. 源码文件索引

### `./`

| 文件 | 行数 | 文档 | 公开 API（节选）|
| --- | ---: | --- | --- |
| `lib.rs` | 180 | Cross-platform system **sleep/wake** (suspend/resume) n | fn`current_power_state`, struct`SystemPowerListener`, enum`PowerEvent` |
| `linux.rs` | 93 | Linux system sleep/wake via systemd-logind's `PrepareFo | fn`current_power_state`, struct`Listener` |
| `macos.rs` | 367 | macOS system sleep/wake via IOKit `IORegisterForSystemP | fn`current_power_state`, struct`Listener` |
| `windows.rs` | 103 | Windows system sleep/wake via `PowerRegisterSuspendResu | fn`current_power_state`, struct`Listener` |

---

## 4. 工作区依赖

见 Cargo.toml

---

## 5. 被谁依赖

`xai-grok-pager` · `xai-grok-shell` · `xai-sqlite-journal` · `xai-system-power`

---

## 6. 开发命令

```sh
cargo check -p xai-system-power
cargo test -p xai-system-power
```

---

## 7. 相关阅读

- [01_project_map.md](../01_project_map.md)
- [README.md](./README.md)

