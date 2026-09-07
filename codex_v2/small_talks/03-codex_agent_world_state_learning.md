# Codex Agent：World State、History、Baseline、Patch 与 Context Cache

## 1. 核心问题

在学习 Codex / Claude Code 等 Coding Agent 时，经常会看到：

- World State
- History
- Snapshot
- Baseline
- Patch / Diff
- `render_full()`
- `render_diff()`
- Compaction
- Prompt Cache / Prefix Cache

这些概念容易混在一起。

最重要的结论是：

> **LLM API 本身通常是无状态的；Agent Runtime 自己维护 Session、History 和 World State，并在每次 LLM Request 时重新组织 Context。**

因此：

```text
LLM = 无状态推理引擎
Agent = 有状态的运行时
```

---

# 2. LLM API 为什么是无状态的？

可以把一次 LLM API 调用简单理解成：

```text
Request 1
    |
    v
   LLM
    |
    v
Response 1

Request 2
    |
    v
   LLM
    |
    v
Response 2
```

LLM 不会因为 Request 1 调用过一次，就天然记住 Request 1 的内容。

如果：

```text
Request 1:

A = 1
B = 2
```

Request 2 只发送：

```text
B = 3
```

那么模型并不知道 `A = 1`。

因此所谓 Agent 的“记忆”，本质上通常来自：

```text
Agent Runtime
    |
    +-- History
    +-- World State
    +-- Tool Results
    +-- Session State
    |
    v
构造新的 LLM Request
```

---

# 3. Agent 为什么看起来像“LLM 有记忆”？

因为 Agent 自己保存了历史。

例如 Agent Session：

```text
history = [
    System Message,
    User Message,
    Assistant Message,
    Tool Call,
    Tool Result,
    Assistant Message,
    Tool Call,
    Tool Result,
    ...
]
```

每次调用 LLM 时，Agent 根据当前 Session 构造新的 Request。

因此：

```text
第一次：

System
+
World State
+
User
```

第二次可能是：

```text
System
+
World State
+
User
+
Assistant
+
Tool Call
+
Tool Result
```

第三次继续：

```text
System
+
World State
+
User
+
Assistant
+
Tool Call
+
Tool Result
+
Assistant
+
Tool Call
+
Tool Result
```

所以模型“记得之前发生了什么”，实际上是：

> Agent 把之前的信息再次放进了当前 Request 的 Context。

---

# 4. History 和 World State 的区别

这是理解 Coding Agent 最重要的概念之一。

## History：发生过什么？

History 是时间线。

例如：

```text
User:
帮我修复 foo.rs

Assistant:
我先读取 foo.rs

Tool Call:
read_file("foo.rs")

Tool Result:
foo.rs 内容...

Assistant:
发现 bug 在第 42 行

Tool Call:
edit_file(...)

Tool Result:
修改成功
```

它描述：

> **过去发生了什么。**

---

## World State：现在是什么？

World State 是 Agent 对当前环境的规范化状态快照。

例如：

```text
cwd = /repo
branch = feature/foo

skills:
  - rust
  - testing

permissions:
  filesystem = workspace
  network = disabled

foo.rs = modified
```

它描述：

> **现在世界是什么样。**

因此：

```text
History    = 时间线 / 发生过什么
World State = 当前状态 / 现在是什么
```

两者不是一回事。

---

# 5. 为什么需要 World State？

Coding Agent 的上下文里有很多“环境事实”，例如：

- 当前工作目录
- 当前 Git branch
- AGENTS.md
- 项目规则
- Skill catalog
- 当前权限
- 当前任务状态
- 已知的环境信息

这些信息如果全部依赖 History 表达，会很麻烦。

例如：

```text
History:

第一次：
当前目录是 /repo

第 20 个 tool call：
当前目录还是 /repo

第 50 个 tool call：
当前目录还是 /repo

第 80 个 tool call：
当前目录还是 /repo
```

真正想表达的其实只是：

```text
cwd = /repo
```

所以 Agent Runtime 可以把这些信息维护成结构化的：

```text
World State Snapshot
```

---

# 6. World State 不是 History 的副本

这是一个非常重要的理解。

错误理解：

```text
World State = 把所有 History 总结一遍
```

更准确的是：

```text
World State = 当前事实的规范化快照
```

例如：

```text
History:

User 修改 foo.rs
Tool 修改成功
Tool 测试失败
User 要求继续修复
Tool 再次修改
Tool 测试成功
```

最终 World State 可能只需要：

```text
foo.rs = modified
tests = passing
```

History 保留过程。

World State 保存当前结果。

---

# 7. Snapshot 是什么？

Snapshot 就是某一时刻的完整 World State。

例如：

```text
World State Snapshot v1:

cwd = /repo
branch = main

skills:
  - rust
  - testing

permissions:
  network = false
```

后来状态变化：

```text
branch = feature/login
skills += cargo-audit
```

新的 Snapshot：

```text
World State Snapshot v2:

cwd = /repo
branch = feature/login

skills:
  - rust
  - testing
  - cargo-audit

permissions:
  network = false
```

---

# 8. 为什么不每轮发送完整 World State？

假设：

```text
权限说明 = 3,000 tokens
AGENTS.md = 8,000 tokens
Skill Catalog = 5,000 tokens
```

完整 World State 就可能已经超过：

```text
16,000 tokens
```

如果每次 Tool Call 后都重新发送：

```text
Full World State
+
Tool Call
+
Tool Result
```

那么：

```text
Step 1: Full World State
Step 2: Full World State
Step 3: Full World State
Step 4: Full World State
...
```

会产生大量重复 Token。

问题包括：

1. 浪费上下文窗口
2. 增加 Token 成本
3. 可能降低缓存利用率
4. 让 Prompt 中重复出现同样的指令
5. 不利于状态变化和审计的理解

因此 Agent 可以采用：

```text
第一次：完整注入
之后：只描述变化
```

也就是：

```text
Full Snapshot
    +
Patch
    +
Patch
    +
Patch
```

---

# 9. Baseline 是什么？

Baseline 可以理解为：

> **模型 Context 中最近一次完整知道的 World State。**

例如第一次：

```text
World State v1:

branch = main
skills = [rust, testing]
```

Agent 把这个完整状态注入 LLM。

那么：

```text
baseline = World State v1
```

之后状态变成：

```text
branch = feature/foo
skills = [rust, testing, cargo-audit]
```

Agent 不一定重新发送完整状态。

它可以发送：

```text
Patch:

branch: main -> feature/foo
added skill: cargo-audit
```

于是模型可以根据：

```text
baseline
+
patch
```

理解当前状态。

---

# 10. Patch / Diff 是什么？

Patch 描述：

> **从 baseline 到当前 snapshot 发生了什么变化。**

例如：

```text
Baseline:

branch = main
skills = [rust, testing]
```

当前：

```text
branch = feature/foo
skills = [rust, testing, cargo-audit]
```

Patch：

```text
branch: main -> feature/foo
+ skill: cargo-audit
```

可以把它理解成 Git：

```text
Snapshot A
    +
Diff
    |
    v
Snapshot B
```

因此：

```text
baseline + patch = current state
```

---

# 11. render_full() 和 render_diff()

Agent 内部可以把 World State 渲染成给 LLM 看的文本。

## render_full()

把完整状态渲染出来：

```text
## Current World State

Working directory:
/repo

Git branch:
feature/foo

Skills:
- rust
- testing
- cargo-audit

Permissions:
- read workspace
- write workspace
```

适合：

- 第一次启动
- 新线程
- 恢复 Session
- Context 被 compact 后
- LLM 已经不再拥有旧 baseline

---

## render_diff()

只渲染变化：

```text
## World State Update

Branch:
main -> feature/foo

Added skill:
cargo-audit
```

适合：

- 正常 Agent Step
- Context 中仍然存在 baseline
- 只发生少量状态变化

---

# 12. 为什么 LLM 能理解 Diff？

关键不是 LLM API 有状态。

而是：

> **Agent 把之前的 History / Context 继续带进下一次 Request。**

例如第一次 Request：

```text
System

World State FULL:
branch = main
skills = [rust, testing]

User:
修复 foo.rs
```

第二次 Request 可能包含：

```text
System

World State FULL:
branch = main
skills = [rust, testing]

User:
修复 foo.rs

Assistant:
...

Tool:
...

Tool Result:
...

World State DIFF:
branch: main -> feature/foo
```

因此第二次 Request 中，模型依然能看到：

```text
branch = main
skills = [rust, testing]
```

然后再应用：

```text
branch: main -> feature/foo
```

得到当前状态。

---

# 13. 所以“第二次只发 Diff”容易产生误解

严格来说，不应该简单理解成：

```text
HTTP Request 2:

Diff only
```

更准确的是：

```text
HTTP Request 2:

System
+
Existing Conversation Context
+
Tool Results
+
World State Diff
```

也就是说：

> **只对 World State 部分做增量更新。**

整个 LLM Request 仍然可能包含大量 History。

---

# 14. Agent 为什么要维护 baseline？

因为 Agent 需要知道：

```text
模型当前 Context 里
到底已经知道哪个 World State？
```

例如 Agent Runtime 当前状态：

```text
current_snapshot = v10
```

但是模型 Context 中最近完整注入的是：

```text
baseline = v7
```

那么：

```text
v7 -> v10
```

之间就存在一些 patch。

Agent 可以：

```text
baseline v7
+
patch v8
+
patch v9
+
patch v10
```

继续让模型知道当前状态。

---

# 15. Compaction 是什么？

问题来了：

History 会越来越长。

例如：

```text
System
World State
User
Tool Call
Tool Result
Tool Call
Tool Result
Tool Call
Tool Result
...
```

最终：

```text
Context Window
████████████████████████████████████
                              ↑
                            快满了
```

Agent 需要做：

```text
Compaction
```

即把很长的历史压缩成摘要或更紧凑的表示。

例如：

```text
原始 History:

100 次 Tool Call
200 次 Tool Result
大量 Assistant Message
大量 World State Patch
...
```

变成：

```text
Compaction Summary:

已经完成：
- 找到 foo.rs 中的 bug
- 修改了第 42 行
- 测试第一次失败
- 修复后测试通过
```

---

# 16. 为什么 Compaction 后需要 render_full()？

这是 World State 设计的关键。

假设原来的 Context：

```text
Full World State v1
+
大量 History
+
大量 Patch
```

Compaction 后，旧 Context 可能被清掉。

于是模型可能已经不知道：

```text
branch = main
skills = [rust, testing]
permissions = ...
```

这时继续发送：

```text
Diff:
branch main -> feature/foo
```

可能是不够的。

因为 baseline：

```text
branch = main
```

已经不在模型 Context 里了。

因此 Agent 需要重新发送完整 World State：

```text
New Full World State v2
```

这就建立了新的 baseline。

流程：

```text
Full v1
   |
   +-- Diff
   +-- Diff
   +-- Diff
   +-- Diff
   |
   v
Context 太长
   |
   v
Compaction
   |
   v
Full v2
   |
   +-- Diff
   +-- Diff
   +-- Diff
```

---

# 17. Prompt Cache 为什么也很重要？

除了节省 Token，稳定的前缀还有另一个重要原因：

> **很多 LLM Provider 支持某种形式的 Prompt / Prefix Caching。**

因此 Agent 通常希望：

```text
前面的 Context 尽量稳定
后面的内容不断追加
```

例如：

```text
┌──────────────────────────────┐
│ System Prompt                │
│ AGENTS.md                    │
│ Skill Catalog                │
│ Baseline World State         │
│ Conversation History        │
└──────────────────────────────┘
              +
┌──────────────────────────────┐
│ New Tool Call                │
│ New Tool Result              │
│ New World State Patch        │
└──────────────────────────────┘
```

前面的部分越稳定，越有利于缓存复用。

---

# 18. 为什么直接修改历史 World State 不好？

假设第一次：

```text
World State:

branch = main
skills = [rust, testing]
```

第二次如果 Agent 直接修改原来的内容：

```text
World State:

branch = feature/foo
skills = [rust, testing]
```

那么前缀发生了变化：

```text
Request 1:

System
World State(branch=main)
History...


Request 2:

System
World State(branch=feature/foo)
History...
```

从变化点开始，原来的 Prefix Cache 可能无法继续复用。

而采用：

```text
Baseline:

branch = main

Patch:

branch: main -> feature/foo
```

可以让历史部分保持稳定，只在后面追加变化。

---

# 19. 一个更完整的 Agent Loop

可以把整个设计串起来：

```text
                    Agent Session
                         |
             +-----------+-----------+
             |                       |
          History                World State
             |                       |
             |                +------+------+
             |                |             |
             |            Snapshot       Baseline
             |                |             |
             |                +------+------+
             |                       |
             |                     Patch
             |                       |
             +-----------+-----------+
                         |
                         v
                    Render Context
                         |
             +-----------+-----------+
             |                       |
        render_full()          render_diff()
             |                       |
             +-----------+-----------+
                         |
                         v
                       LLM
                         |
                         v
                    Response
                         |
                         v
                  Tool Execution
                         |
                         v
                 Update World State
                         |
                         v
                   Next Step
```

---

# 20. 最关键的状态关系

建议牢牢记住下面这个关系：

```text
History
= 发生过什么

World State Snapshot
= 当前是什么

Baseline
= 模型 Context 中最近一次完整知道的 World State

Patch
= Baseline -> Current Snapshot 的变化

render_full()
= 把完整 Snapshot 告诉模型

render_diff()
= 把 Snapshot 相对于 Baseline 的变化告诉模型

Compaction
= 压缩过长的 History / Context

Prompt Cache
= 尽量复用稳定的 Context Prefix
```

---

# 21. 一个具体例子

假设 Agent 开始：

```text
Snapshot v1:

cwd = /repo
branch = main

skills:
- rust
- testing
```

第一次 LLM Request：

```text
System
+
Full World State v1
+
User Task
```

此时：

```text
baseline = v1
```

---

Agent 修改 branch：

```text
Snapshot v2:

cwd = /repo
branch = feature/login

skills:
- rust
- testing
```

Diff：

```text
branch: main -> feature/login
```

下一次 Request：

```text
System
+
Full World State v1
+
User Task
+
Previous History
+
World State Diff:
branch: main -> feature/login
```

---

Agent 又加载一个 Skill：

```text
Snapshot v3:

cwd = /repo
branch = feature/login

skills:
- rust
- testing
- cargo-audit
```

Diff：

```text
+ cargo-audit
```

继续追加：

```text
World State Diff:
+ cargo-audit
```

---

Context 最后快满：

```text
History:
100k tokens
```

执行 Compaction。

旧 baseline 可能已经不在新的 Context 中。

于是：

```text
New Context:

System
+
Compaction Summary
+
Full World State v3
```

此时：

```text
baseline = v3
```

后面再发生变化：

```text
v3 -> v4
```

又可以只发送：

```text
Diff
```

---

# 22. 为什么这种设计适合 Coding Agent？

Coding Agent 通常会执行很多 Tool Call：

```text
LLM
 ↓
read file
 ↓
LLM
 ↓
grep
 ↓
LLM
 ↓
read file
 ↓
LLM
 ↓
edit file
 ↓
LLM
 ↓
run test
 ↓
LLM
 ↓
read error
 ↓
LLM
 ↓
edit file
 ↓
LLM
 ↓
run test
 ↓
...
```

如果每次都重新注入大量固定环境信息：

```text
AGENTS.md
Skill Catalog
Permissions
Environment
...
```

会产生巨大的重复。

所以比较理想的设计是：

```text
首次：
Full State

正常 Step：
Diff

Context 快满：
Compaction

Compaction 后：
Full State 建立新 Baseline

之后：
Diff
```

---

# 23. 最终心智模型

如果只记一张图，记这个：

```text
             LLM API
          （无状态）
               ^
               |
       Agent 构造 Request
               |
       +-------+--------+
       |                |
    History          World State
       |                |
       |          +-----+-----+
       |          |           |
       |       Snapshot    Baseline
       |          |           |
       |          +-----+-----+
       |                |
       |              Patch
       |                |
       +-------+--------+
               |
          Render Context
               |
       +-------+--------+
       |                |
   render_full()   render_diff()
       |                |
       +-------+--------+
               |
               v
              LLM
```

核心思想：

> **LLM 没有 Session Memory；Agent Runtime 才有。**
>
> **World State 是 Agent 对当前环境的规范化状态。**
>
> **Baseline 是模型 Context 中最近一次完整 World State。**
>
> **Patch 是从 Baseline 到当前 Snapshot 的变化。**
>
> **正常情况下使用 Diff，Context 被 Compaction 后重新 Full。**
>
> **这样既节省 Context，又尽量保持稳定 Prefix，有利于 Prompt Cache。**

---

# 24. 阅读 Codex 源码时应该重点追什么？

当你继续读 Codex Agent 源码时，可以重点找这几条调用链：

## A. World State 如何产生

```text
Environment / Session
        ↓
World State
        ↓
Snapshot
```

关注：

- 谁创建 Snapshot？
- 哪些字段属于 World State？
- 什么操作会导致 Snapshot 改变？

## B. Diff 如何产生

```text
Old Snapshot
      +
New Snapshot
      ↓
Patch / Diff
```

关注：

- Diff 是怎么计算的？
- 是手动记录 mutation，还是比较两个 snapshot？
- Patch 是否可以 replay？

## C. Diff 如何进入 LLM Request

```text
World State
      ↓
render_full / render_diff
      ↓
Messages
      ↓
LLM Request
```

这是最值得追的地方。

## D. Compaction 如何影响 Baseline

```text
History 太长
      ↓
Compaction
      ↓
旧 Context 被压缩
      ↓
render_full()
      ↓
New Baseline
```

重点看：

> **Compaction 后，Agent 是如何判断“之前的 baseline 已经不在 Context 中”的？**

## E. Prompt Cache

继续研究：

```text
Request
  ↓
stable prefix
  ↓
cache
  ↓
new suffix
```

可以进一步理解为什么 Coding Agent 的 Prompt 组织方式会非常重视：

- 固定 System Prompt
- 稳定的 Instructions
- Stable Tool Definitions
- Stable World State Baseline
- Append-only History
- Compaction

---

# 25. 一句话总结

整个设计可以压缩成：

```text
Agent 自己保存状态
        ↓
第一次把完整状态告诉 LLM
        ↓
之后只追加状态变化
        ↓
保持 Context 前缀稳定
        ↓
尽可能利用 Prompt Cache
        ↓
Context 太长时 Compaction
        ↓
重新注入完整 World State
        ↓
建立新的 Baseline
        ↓
继续 Diff
```

这就是理解 Codex Agent 中 `World State / Snapshot / Baseline / Patch / render_full / render_diff / Compaction / Prompt Cache` 的核心框架。
