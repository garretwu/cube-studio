# 前后端契约（Current）

> 当前生效契约主文档。历史说明见 `frontend_backend_contracts.md`。

## 1. 鉴权与通用信封

- 认证：`Authorization: Bearer <JWT>`
- 追踪：`x-trace-id`
- 统一响应：`{ success, data, error, trace_id, timestamp }`

## 2. 诊断与对话关键接口

### `POST /api/chat`

请求：

```json
{
  "content": "为何判定该根因？",
  "session_id": "<optional>"
}
```

响应（新增 `meta`，可选字段，向后兼容）：

```json
{
  "success": true,
  "data": {
    "reply": "...",
    "meta": {
      "context_applied": true,
      "session_id": "sess-xxx",
      "trace_steps_used": 12,
      "context_tokens_estimate": 280
    },
    "display": {
      "answer": "结构化可读答复",
      "thinking_raw": "<think>...</think>",
      "has_thinking": true
    }
  },
  "trace_id": "trace-xxx",
  "timestamp": "2026-04-01T10:00:00Z"
}
```

说明：

- 当携带有效 `session_id` 时，后端会注入诊断上下文（result/impact/blast 摘要 + 最近 trace）再调用 LLM。
- 当 `session_id` 不存在时，返回 404（明确错误语义，避免空回复）。
- `display` 为可选展示层字段，不影响原始 `reply` 兼容性。

### `GET /api/chat/history`

- 支持 `session_id` 查询参数。
- 每条消息可选携带 `display`（`answer`、`thinking_raw`、`has_thinking`）。
- 语义：
1. 传 `session_id` 时优先返回 scoped 历史。
2. 若当前用户历史中存在 session 化消息，但目标 session 无记录，则返回空数组。
3. 若历史本身无 session 维度，则兼容返回全量历史。

### `GET /api/sessions/{id}/trace`

- 返回该诊断会话 trace 明细（ThinkingStep / Observation），用于诊断工作区渲染过程。

## 3. WebSocket 路径

- `/ws/alerts`：告警流
- `/ws/thinking-trace/{session_id}`：诊断思维/工具事件流
- `/ws/topology`：拓扑同步与增量事件流
- `/ws/chat`：对话流（当前诊断页主链路仍以 REST 追问为主）

## 4. 前端绑定关系（当前设计）

- `sre_agent/frontend/src/pages/Diagnosis.tsx`
- `sre_agent/frontend/src/store/diagnosisStore.ts`
- `sre_agent/frontend/src/api/client.ts`

行为：

1. 诊断页追问走 `/api/chat`（REST 同步）。
2. 工作区并行消费 `/ws/thinking-trace/{session_id}` 事件。
3. 当 `meta.context_applied=true` 时，前端显示“上下文已加载”。

## 5. 错误语义约定

- 401：token 无效或过期。
- 404（`/api/chat`）：`session_id` 不存在。
- 网络错误：前端展示 Network/CORS/后端不可达提示，不伪装成业务空态。

## 6. 变更记录

- 2026-04-01：`/api/chat` 新增可选 `meta`，并接入诊断会话上下文增强。
- 2026-04-01：明确 `/api/chat/history?session_id=` 的 scoped 过滤/回退规则。
- 2026-04-01：前端诊断工作区新增“上下文已加载”可观测状态。
- 2026-04-01：新增 `display` 展示层字段，支持“答案正文 + 原始思维折叠展示”；chat 上下文采用强会话锚定窗口。
