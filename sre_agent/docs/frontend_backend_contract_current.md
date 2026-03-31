# 前后端契约（Current）

> 该文档是当前生效的前后端契约主入口，历史版本请见 `frontend_backend_contracts.md`。

## 1. 鉴权与通用信封

- 鉴权方式：`Authorization: Bearer <JWT>`
- 追踪头：`x-trace-id`
- 响应信封：`{ success, data, error, trace_id, timestamp }`

## 2. REST / WS 矩阵

<!-- CONTRACT:BEGIN -->
### REST 接口矩阵（自动生成）

| Method | Path | 鉴权 | 响应主体 |
| --- | --- | --- | --- |
| `GET` | `/api/alerts` | Bearer JWT | `SREResponse_AlertSnapshotResponse_` |
| `POST` | `/api/chat` | Bearer JWT | `SREResponse_ChatResponse_` |
| `GET` | `/api/chat/history` | Bearer JWT | `SREResponse_list_ChatHistoryMessage__` |
| `POST` | `/api/diagnose` | Bearer JWT | `SREResponse_DiagnosisSession_` |
| `POST` | `/api/handle` | Bearer JWT | `SREResponse_LoopResult_` |
| `GET` | `/api/knowledge/documents` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `GET` | `/api/knowledge/search` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `GET` | `/api/memory/baseline` | Bearer JWT | `SREResponse_dict_str__Any__` |
| `GET` | `/api/memory/incidents` | Bearer JWT | `SREResponse_list_IncidentRecord__` |
| `GET` | `/api/memory/patterns` | Bearer JWT | `SREResponse_list_LearnedPattern__` |
| `GET` | `/api/ontology` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `POST` | `/api/ontology/blast` | Bearer JWT | `SREResponse_dict_str__Any__` |
| `GET` | `/api/ontology/path` | Bearer JWT | `SREResponse_Union_list_str___NoneType__` |
| `POST` | `/api/ontology/path` | Bearer JWT | `SREResponse_Union_list_str___NoneType__` |
| `POST` | `/api/ontology/query` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `POST` | `/api/ontology/refresh` | Bearer JWT | `SREResponse_dict_str__Any__` |
| `GET` | `/api/ontology/{entity_id}/blast-radius` | Bearer JWT | `SREResponse_dict_str__Any__` |
| `POST` | `/api/remediate/{session_id}/approve` | Bearer JWT | `SREResponse_RemediationResult_` |
| `POST` | `/api/remediate/{session_id}/rollback` | Bearer JWT | `SREResponse_RollbackResult_` |
| `GET` | `/api/sessions` | Bearer JWT | `SREResponse_list_SessionSummary__` |
| `GET` | `/api/sessions/{session_id}` | Bearer JWT | `SREResponse_DiagnosisSession_` |
| `GET` | `/api/sessions/{session_id}/loop` | Bearer JWT | `SREResponse_LoopResult_` |
| `GET` | `/api/sessions/{session_id}/trace` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `GET` | `/api/skills` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `GET` | `/api/tools/channels/status` | Bearer JWT | `SREResponse_ToolChannelsStatusResponse_` |
| `GET` | `/api/topology` | Bearer JWT | `SREResponse_TopologySnapshotResponse_` |
| `POST` | `/api/topology/discover` | Bearer JWT | `SREResponse_TopologyStatusResponse_` |
| `GET` | `/api/topology/status` | Bearer JWT | `SREResponse_TopologyStatusResponse_` |

### WS 路径矩阵（自动生成）

| Path | 用途 |
| --- | --- |
| `/ws/alerts` | 告警实时流 |
| `/ws/chat` | 对话流 |
| `/ws/thinking-trace/{session_id}` | 诊断思维链路流 |
| `/ws/topology` | 拓扑同步与增量更新流 |

### WS 事件类型（自动生成）

| EventType | 说明 |
| --- | --- |
| `thinking_step` | 诊断思维步骤 |
| `tool_call` | 工具调用请求 |
| `tool_result` | 工具调用结果 |
| `diagnosis_result` | 诊断结论事件 |
| `approval_required` | 通用事件 |
| `loop_start` | 通用事件 |
| `loop_progress` | 通用事件 |
| `remediation_progress` | 修复进度事件 |
| `alert` | 告警变更事件 |
| `topology` | 拓扑同步与节点/边增量事件 |
| `error` | 错误事件 |
| `done` | 会话结束事件 |
<!-- CONTRACT:END -->
## 3. 关键 Payload 示例

### 3.1 拓扑快照 `GET /api/topology`

```json
{
  "success": true,
  "data": {
    "nodes": [],
    "edges": [],
    "active_alerts": 0,
    "recent_events": [],
    "snapshot_id": "snapshot-1",
    "last_synced_at": "2026-03-30T10:00:00Z",
    "sync_state": "ready"
  },
  "trace_id": "trace-1",
  "timestamp": "2026-03-30T10:00:00Z"
}
```

### 3.2 拓扑状态 `GET /api/topology/status`

```json
{
  "success": true,
  "data": {
    "snapshot_id": "snapshot-1",
    "sync_state": "ready",
    "mode": "live",
    "last_synced_at": "2026-03-30T10:00:00Z",
    "last_started_at": "2026-03-30T09:59:50Z",
    "last_error": null,
    "scanner_counts": {
      "switch": { "nodes": 8, "edges": 7 },
      "k8s": { "nodes": 24, "edges": 36 }
    }
  },
  "trace_id": "trace-2",
  "timestamp": "2026-03-30T10:00:01Z"
}
```

### 3.3 拓扑 WS 事件 `WS /ws/topology`

```json
{
  "schema_version": "1.0",
  "type": "topology",
  "session_id": "topology",
  "timestamp": "2026-03-30T10:00:00Z",
  "data": {
    "event_id": "12",
    "action": "node_upsert",
    "node": {
      "id": "worker-01",
      "entity_type": "node"
    }
  }
}
```

### 3.4 对话历史 `GET /api/chat/history`

```json
{
  "success": true,
  "data": [
    {
      "id": "assistant-1",
      "role": "assistant",
      "content": "请先确认告警对应的 node 与 pod。",
      "created_at": "2026-03-30T10:00:00Z",
      "tool_name": null,
      "metadata": null
    }
  ],
  "trace_id": "trace-3",
  "timestamp": "2026-03-30T10:00:02Z"
}
```

### 3.5 会话追踪 `GET /api/sessions/{session_id}/trace`

```json
{
  "success": true,
  "data": [
    {
      "step": 1,
      "timestamp": "2026-03-30T10:00:03Z",
      "thought": "先确认告警实体与拓扑关联。",
      "action_type": "tool_call",
      "tool_name": "ontology.blast_radius",
      "tool_params": { "entity_id": "worker-01" },
      "confidence": 0.72
    }
  ],
  "trace_id": "trace-4",
  "timestamp": "2026-03-30T10:00:04Z"
}
```

## 4. 前端绑定关系（页面到接口）

- API Client：`sre_agent/frontend/src/api/client.ts`
- 类型定义：`sre_agent/frontend/src/api/types.ts`
- 拓扑状态管理：`sre_agent/frontend/src/store/topologyStore.ts`
- 拓扑页面：`sre_agent/frontend/src/pages/Topology.tsx`
- WS 管理：`sre_agent/frontend/src/api/ws.ts`
- 告警页面：`sre_agent/frontend/src/pages/Alerts.tsx` 对应 `/api/alerts`、`/ws/alerts`
- 诊断页面：`sre_agent/frontend/src/pages/Diagnosis.tsx` 对应 `/api/sessions/*`、`/ws/thinking-trace/{session_id}`
- 对话页面：`sre_agent/frontend/src/pages/Chat.tsx` 对应 `/api/chat`、`/api/chat/history`、`/ws/chat`

## 5. 错误语义约定

- 前端 `ERR_NETWORK` 映射为：`Network/CORS error: backend unreachable or blocked by browser policy...`
- 页面需区分：
- 请求失败（网络、跨域、后端不可达）
- 请求成功但无数据（业务空态）
- WS 断线重连使用 `last_event_id`（见 `sre_agent/frontend/src/api/ws.ts`）

## 6. 变更记录

- 2026-03-30：新增 topology 运行时发现状态与手工触发接口；新增 `/ws/topology`；契约矩阵改为自动同步。
- 2026-03-30：补充 `/api/topology/status`、`/api/chat/history`、`/api/sessions/{id}/trace` 示例与页面绑定关系。
