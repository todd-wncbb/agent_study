# Claude Agent 与 LLM 的网络通信详解

本文档说明 Claude Code / Claude Agent SDK 是如何通过网络与 LLM（Claude API）交互的：请求格式、认证方式、SSE 流式协议、工具调用（tool use）的消息结构，以及如何通过抓包/代理观察整个通信过程。

## 1. 整体架构

```
你的终端/编辑器
     ↓
Claude Code (本地进程，Node.js)  ← 这是"agent"，管理对话历史、执行工具、维护循环
     ↓ HTTPS POST
https://api.anthropic.com/v1/messages   ← 这是"LLM"，纯粹的无状态推理接口
     ↓ SSE 流式返回
Claude Code 解析事件 → 执行 tool_use → 把结果拼回 messages → 再发一次请求
```

**关键点：LLM 本身不"跑"任何东西，也没有记忆**。Claude Code 每次请求都要把完整的对话历史（`messages` 数组）重新发一遍。所谓"agent 循环"就是本地代码里的一个 `while` 循环：

1. 调用 API
2. 如果 `stop_reason == "tool_use"` → 本地执行工具
3. 把工具执行结果塞进 `messages`
4. 再调用 API
5. 直到 `stop_reason == "end_turn"`

## 2. 请求格式

一次典型请求（伪代码）：

```http
POST /v1/messages HTTP/1.1
Host: api.anthropic.com
content-type: application/json
x-api-key: sk-ant-...
anthropic-version: 2023-06-01
anthropic-beta: oauth-2025-04-20   （如果用的是 OAuth 登录而非 API key）

{
  "model": "claude-opus-5",
  "max_tokens": 16000,
  "stream": true,
  "system": [{"type": "text", "text": "<Claude Code 的系统提示词>", "cache_control": {"type": "ephemeral"}}],
  "tools": [ {"name": "Bash", "input_schema": {...}}, {"name": "Read", ...}, ... ],
  "messages": [
    {"role": "user", "content": "帮我改一下这个 bug"},
    {"role": "assistant", "content": [...]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]}
  ]
}
```

字段说明：

- **`system`**：Claude Code 自己的系统提示词（工具使用规范、行为约束等），通常带 `cache_control` 做 prompt caching。
- **`tools`**：Claude Code 内置工具的 JSON Schema 定义（`Read`、`Write`、`Bash`、`Grep` 等），每次请求都会带上（这也是为什么系统提示词 + 工具定义要放在缓存断点前）。
- **`messages`**：完整对话历史，包括之前的工具调用和结果。

## 3. 认证方式

两种模式，二选一：

| 方式 | Header | 场景 |
|---|---|---|
| API Key | `x-api-key: sk-ant-...` | 设置了 `ANTHROPIC_API_KEY` 环境变量 |
| OAuth | `Authorization: Bearer <token>` + `anthropic-beta: oauth-2025-04-20` | `claude /login` 走浏览器登录后拿到的短期令牌 |

OAuth token 存在本地的凭证文件里（`~/.claude/` 或类似路径），Claude Code 会自动刷新。

## 4. SSE 流式协议

`stream: true` 时，响应是标准 Server-Sent Events，格式如下（未加密，纯文本，抓包能直接看到）：

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_...","model":"claude-opus-5",...}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"我"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"来看看"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_01ABC","name":"Bash","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"command\": \"ls"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":42}}

event: message_stop
data: {"type":"message_stop"}
```

**重要事件类型：**

| Event Type | 说明 | 触发时机 |
|---|---|---|
| `message_start` | 包含消息元数据 | 开始时一次 |
| `content_block_start` | 新的内容块开始 | text/tool_use 块开始时 |
| `content_block_delta` | 增量内容更新 | 每个 token/chunk |
| `content_block_stop` | 内容块结束 | 块完成时 |
| `message_delta` | 消息级更新 | 包含 `stop_reason`、usage |
| `message_stop` | 消息完成 | 结束时一次 |

注意工具调用的输入（`input`）也是**流式**吐出来的（`input_json_delta`，累加起来才是完整 JSON）——这就是为什么 Claude Code 里能看到工具参数"打字机效果"地出现。

## 5. 工具调用（tool_use）的消息结构

这是最核心的一环，一次完整的"Claude 调用 Bash 工具"往返：

**第一次响应**（`stop_reason: "tool_use"`）：

```json
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "我先看一下目录结构"},
    {"type": "tool_use", "id": "toolu_01ABC", "name": "Bash", "input": {"command": "ls -la"}}
  ],
  "stop_reason": "tool_use"
}
```

**Claude Code 在本地真正执行 `ls -la`**（这一步完全不经过网络，是本机 shell 执行），然后把结果和历史一起发回：

```json
{
  "messages": [
    ...之前的历史...,
    {"role": "assistant", "content": [<上面那个 text + tool_use>]},
    {"role": "user", "content": [
      {"type": "tool_result", "tool_use_id": "toolu_01ABC", "content": "total 24\ndrwxr-xr-x ..."}
    ]}
  ]
}
```

如此循环。所以在抓包里会看到**大量重复的、越来越长**的请求体——因为每次都是"历史全量 + 新的一轮"。

## 6. 如何抓包/代理观察

### 方法一：mitmproxy（推荐，能看到完整的解密后请求体）

```bash
mitmproxy  # 或 mitmweb，图形界面更方便
```

然后在另一个终端里指定代理并运行 Claude Code：

```bash
export HTTPS_PROXY=http://127.0.0.1:8080
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem   # 让 Node.js 信任 mitmproxy 的证书
claude
```

`NODE_EXTRA_CA_CERTS` 是关键——Claude Code 是 Node.js 程序，默认只信任系统 CA，不装这个会因为证书校验失败连不上。

### 方法二：Claude Code 自带的调试输出

```bash
ANTHROPIC_LOG=debug claude
```

这个不需要代理，会直接把请求/响应元信息打到 stderr（不一定是完整 body，但能看到 header、model、stop_reason 等）。

### 方法三：自己写一个假的 API 端点观察

用 `ANTHROPIC_BASE_URL` 把 Claude Code 指向你自己的一个"回显服务器"（比如用 Python `http.server` 或简单 Express app 记录并转发请求）：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
claude
```

这样能拿到最原始的请求体做逐字段分析，比抓包更直观（缺点是需要自己实现转发逻辑）。

## 7. 补充：Tool Runner / Tool Use 概念延伸阅读

- **Tool Runner ≠ Claude Agent SDK**：Tool Runner 是常规 Anthropic SDK 的一部分（`client.beta.messages.tool_runner`），自动化"请求 → 执行 → 循环"，但没有内置工具、没有文件系统访问、没有 sandbox——所有工具都得自己定义。
- **Claude Agent SDK**（`claude-agent-sdk` / `@anthropic-ai/claude-agent-sdk`）是把 Claude Code 打包成库，自带内置工具（文件读写、bash、grep、web search）、完整 agent 循环、上下文管理、hooks、子代理、权限、会话等。调用 `query(prompt, options)` 即可驱动全部流程。
- 两者都是 **harness-only**——即你需要自己host和部署，Anthropic 只提供"循环骨架"，不提供托管的运行环境（这是 Managed Agents 才提供的）。

## 8. 后续可深入方向

- Claude Agent SDK（区别于 Claude Code CLI）作为库调用时的代码层面细节
- Prompt caching 的断点放置策略（`cache_control`、20-block lookback window）
- 具体某个工具（如 `Bash`/`Read`）的 JSON Schema 长什么样
- Managed Agents（服务端托管的 agent 会话）与本地 Claude Code 的架构差异
