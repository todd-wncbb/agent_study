# 13：模型传输、增量请求与重试

可以先把它想成打电话：`ModelClient` 像长期保存的联系人、账号和拨号偏好；`ModelClientSession` 像当前这一通电话。电话线路可以复用，但本次通话中的临时编号和续接状态不能带到下一通电话。

## 1. 两层客户端

`ModelClient` 与 Codex session 同寿命，持有稳定的 provider、auth、thread ID、transport fallback 和 session-level WebSocket cache。

`ModelClientSession` 与一个 turn 同寿命，持有：

- 当前 turn 的 WebSocket request baseline；
- last response ID/items；
- `x-codex-turn-state` sticky-routing token；
- 同一 turn 内多次 sampling 的连接状态。

必须为每个 turn 创建新的 `ModelClientSession`。否则会把上一个 turn 的 sticky-routing token 带入下一个 turn。

底层 WebSocket 物理连接可以由 session-level client 安全缓存和复用，但 turn state 必须重新建立；“复用连接”不等于“复用 turn-scoped 语义”。

## 2. HTTP 与 WebSocket

两条传输都发送 Responses API 语义并映射为统一 `ResponseStream`：

- HTTP 使用 SSE；
- WebSocket 使用 response.create 事件；
- provider/feature 决定是否支持 WebSocket；
- WebSocket 失败时可能切换 HTTP，fallback 状态可在 session 范围保持。

调用方 `try_run_sampling_request()` 不应关心底层 transport，只消费 `ResponseEvent`。

如果只看直觉：SSE 更像服务器沿一条 HTTP 响应不断往客户端推送事件；WebSocket 更像建立一条双向长连接，后续请求和事件都可以继续走这条连接。两者最终都会被转换成 Codex 内部统一的事件流。

## 3. Preconnect 与 Prewarm

- **preconnect** 只建立 WebSocket，不发送 prompt payload；
- **prewarm** 发送 `generate=false` 的 v2 request，并等待完成，以获得可复用连接和 `previous_response_id`。

Prewarm 是 best effort，也会消耗本 turn 的 WebSocket retry budget；失败后由正常 stream retry/fallback 处理，而不是无限额外重试。

## 4. 增量请求的严格条件

WebSocket follow-up 只有在当前 request 是上一 request 加 server outputs 的严格扩展时，才发送：

```json
{
  "previous_response_id": "resp-1",
  "input": ["only new delta items"]
}
```

`responses_request_properties_match()` 对 model、instructions、tools、tool choice、reasoning、service tier、prompt cache key、text schema 等逐项比较。`get_incremental_items()` 还要求：

- 旧 request input + server output 与新 input 前缀相等；
- response ID 存在；
- 没有不兼容的 history rewrite；
- 仅内部 passthrough metadata 的差异可以忽略。

条件不满足就发送完整 create，不冒险引用错误上下文。

例如第一次请求的 input 是 `[用户问题]`，模型随后要求调用工具；第二次请求变为 `[用户问题, 工具调用, 工具结果]`。因为旧内容仍是完整前缀，第二次可以只发送新增的工具调用与结果。若中间把用户问题改写了，即使意思相近，也不再是严格前缀，必须发送完整请求。

## 5. Prompt cache 与增量传输不是同一件事

- Prompt caching 是服务端对稳定前缀的计算复用；
- WebSocket incremental create 是传输层少发重复 input，并用 previous response 续接。

两者都偏好 append-only history 和稳定 request properties，但命中条件、观察指标和失败回退不同。不要仅凭 payload 较小就断言 cache hit。

一个类比是寄送资料：增量传输表示第二次只寄新增加的几页；prompt cache 表示收件方仍记得前一百页的处理结果。只寄了少量新页，不代表收件方一定命中了缓存；反过来，发送完整资料也可能利用稳定前缀缓存。

## 6. Retry 的两层语义

`try_run_sampling_request()` 处理一次 stream；`run_sampling_request()` 处理可重试错误和次数预算。重试必须保留 turn state、metadata 和 telemetry，同时区分：

- context window exceeded：交给 compaction/上层处理；
- usage limit：更新 rate limit 并停止普通重试；
- unauthorized：走 auth recovery；
- WebSocket connection limit/transport failure：重连或 fallback；
- invalid request：通常不可重试；
- stream 未 completed 就关闭：失败，不能当作成功回答。

## 7. 为什么请求属性比较要 exhaustive destructure

`responses_request_properties_match()` 故意不实现普通 `PartialEq`，并 exhaustive 解构 request。新增字段时，编译器迫使作者决定它是否影响 previous-response reuse。否则一个新语义字段可能被错误忽略，导致服务端在不兼容上下文上续接。

例如以后给请求新增 `response_language` 字段。如果比较函数没有被迫处理它，系统可能错误地把“中文回答”的 previous response 接到“英文回答”的请求上。穷尽解构让新增字段在编译期就提醒维护者作出明确决定。

## 8. 测试入口

- `core/tests/suite/client_websockets.rs`：preconnect、prewarm、incremental、fallback、turn metadata；
- `core/tests/suite/prompt_caching.rs`：history 稳定性与缓存相关请求；
- `core/tests/suite/client.rs`：SSE、错误、retry 和 tool follow-up；
- `core/src/client.rs` 单元测试：property comparison 和 request building。

## 读完后自测

1. 为什么底层 WebSocket 连接可以跨 turn 复用，而 `ModelClientSession` 不可以？
2. “第二次请求 payload 更小”为什么不能证明 prompt cache 命中？
3. 如果新请求只是把旧问题换成了同义句，它还满足严格增量条件吗？为什么？

## 本章词汇表

| 词语 | 直译 | 在模型传输中的意思 |
|---|---|---|
| Transport | 传输层 | HTTP/SSE 或 WebSocket 等通信方式 |
| SSE | 服务端发送事件 | 服务端沿一条 HTTP 响应持续推送事件 |
| Preconnect | 预连接 | 先建立 WebSocket，不发送 prompt payload |
| Prewarm | 预热 | 提前发送不生成回答的准备请求并建立续接状态 |
| Incremental | 增量 | 只发送相对前一请求新增的 input items |
| Fallback | 回退 | WebSocket 不可用时改用 HTTP 等兼容路径 |
| Exhaustive destructure | 穷尽解构 | 显式处理结构体全部字段，让新增字段触发编译提醒 |

完整解释见[术语总表](glossary.md)。

## 源码检查点

1. 给 `responses_request_properties_match()` 中每个字段标注“变化时为何不能复用”。
2. 找 non-prefix 与 non-input-field-change 两个 WebSocket 测试，比较第二个 request。
3. 从 `run_sampling_request()` 画出 retryable、context-limit、usage-limit 三条分支。
