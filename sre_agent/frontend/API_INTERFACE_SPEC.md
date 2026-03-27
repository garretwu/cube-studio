# Frontend 接口规范说明（Wave 1-3 合并后）

## 1. 范围

本文档描述 `sre_agent/frontend` 在 Wave 1-3 严格混合合并后的**实际**前后端契约。

覆盖内容：
- 前端页面与 store 实际调用的 HTTP 接口
- 诊断与告警实时流使用的 WebSocket 契约
- `SREResponse` 包装格式约定
- 本地 Mock 策略（MSW）

## 2. 运行模式

### 2.1 MSW 策略（显式开启）

仅在以下条件同时满足时，前端才启用 MSW：
- `import.meta.env.DEV === true`
- `VITE_USE_MSW === "true"`

实现位置：`src/main.tsx`。

### 2.2 真实后端模式

满足以下任一条件时走真实后端：
- `VITE_USE_MSW` 未设置
- `VITE_USE_MSW !== "true"`

联调/回归建议：
- `VITE_USE_MSW=false`
- `VITE_WS_ENABLED=true`
- `VITE_API_PROXY_TARGET=http://127.0.0.1:8000`
- `VITE_API_TOKEN=<bearer token>`

## 3. 通用契约规则

### 3.1 鉴权

- HTTP 受保护路由：`Authorization: Bearer <token>`
- WebSocket 路由：必须带 `?token=<token>` 查询参数

### 3.2 响应包装

后端主响应格式：

```ts
{
  success: boolean;
  data: T | null;
  error?: {
    code: string;
    message: string;
    details?: unknown;
    trace_id?: string;
  } | null;
  trace_id: string;
  timestamp: string;
}
```

前端 client 默认会解包该 envelope。部分方法仍保留对历史非 envelope 响应的兼容兜底。

## 4. Wave 1-3 主链路契约

### 4.1 Alerts 页面

- `GET /api/alerts`
  - 响应 `data`：

```ts
{
  alerts: Alert[];
  clusters: AlertCluster[];
}
```

- 用户选择告警行后：
  - 前端以所选 `Alert` 调用 `POST /api/handle`
  - 期望在 envelope `data` 中返回 `LoopResult`
  - 跳转到 `/diagnosis?session_id=<loop.session_id>`

### 4.2 Diagnosis 页面

- 从 URL query 读取 `session_id`
- 拉取会话：
  - `GET /api/sessions/{session_id}` -> `DiagnosisSession`

- 实时流：
  - `GET ws(s)://<host>/ws/thinking-trace/{session_id}?token=<token>[&last_event_id=<id>]`
  - 接收 `WSEvent`

### 4.3 Remediation 页面

- 从 URL query 读取 `session_id`
- 拉取 loop 状态：
  - `GET /api/sessions/{session_id}/loop` -> `LoopResult`
- 审批动作：
  - `POST /api/remediate/{session_id}/approve`
  - 请求体：

```json
{
  "approved": true,
  "user": "ui-operator"
}
```

## 5. 实时告警契约

### 5.1 快照

- `GET /api/alerts`

### 5.2 流式

- `GET ws(s)://<host>/ws/alerts?token=<token>[&last_event_id=<id>]`
- 前端在收到 `type === "alert"` 事件时会刷新 `GET /api/alerts`

## 6. 非主链路页面契约

### 6.1 Topology

- `GET /api/topology` -> `TopologySnapshot`

```ts
type TopologySnapshot = {
  nodes: OntologyNode[];
  edges: OntologyEdge[];
  active_alerts: number;
  recent_events: string[];
};
```

### 6.2 Chat

- `POST /api/chat`
- 请求体：

```json
{ "content": "..." }
```

- envelope `data`：

```ts
{ reply: string }
```

### 6.3 Knowledge

- `GET /api/knowledge/search?query=<q>&category=<optional>` -> `KnowledgeDocument[]`
- `GET /api/knowledge/documents` -> `KnowledgeDocument[]`

### 6.4 Memory

- `GET /api/memory/incidents?last=<n>` -> `IncidentRecord[]`
- `GET /api/memory/patterns` -> `LearnedPattern[]`
- `GET /api/memory/baseline` -> `ConfigBaseline`

### 6.5 Skills

- `GET /api/skills` -> `SkillDescriptor[]`

## 7. WebSocket 事件模型

```ts
type WSEvent = {
  schema_version: string;
  type:
    | "thinking_step"
    | "tool_call"
    | "tool_result"
    | "diagnosis_result"
    | "approval_required"
    | "loop_start"
    | "loop_progress"
    | "remediation_progress"
    | "alert"
    | "error"
    | "done";
  session_id: string;
  timestamp: string;
  data: Record<string, unknown>;
};
```

## 8. 回归与 DoD 关注点

1. 端到端回归时保持 MSW 关闭（`VITE_USE_MSW=false`）。
2. 确保后端配置包含 `global.alertmanager_url`，使告警轮询器绑定真实源。
3. 同时验证快照与流：
   - `/api/alerts` 返回实时告警
   - `/ws/alerts` 可推送更新，且支持 `last_event_id` 续传
4. 验证 handle 流转后的诊断流：
   - `/ws/thinking-trace/{session_id}` 可收到事件

## 9. 契约一致性治理（新增）

- 后端契约单一来源：FastAPI OpenAPI + 后端 `EventType` 枚举。
- 前端生成产物：
  - `src/api/generated/openapi.json`
  - `src/api/generated/backend-contract.ts`
- 同步命令：
  - `npm --prefix sre_agent/frontend run contract:sync`
- 校验命令（CI / 本地）：
  - `npm --prefix sre_agent/frontend run contract:check`
  - 若生成后有未提交 diff，命令将失败，用于阻止前后端契约漂移。
