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
| `POST` | `/api/diagnose/start` | Bearer JWT | `SREResponse_DiagnosisSession_` |
| `POST` | `/api/handle` | Bearer JWT | `SREResponse_LoopResult_` |
| `GET` | `/api/knowledge/bases` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `GET` | `/api/knowledge/bases/{knowledge_base_id}` | Bearer JWT | `SREResponse_dict_str__Any__` |
| `GET` | `/api/knowledge/dataset` | Bearer JWT | `SREResponse_dict_str__Any__` |
| `GET` | `/api/knowledge/datasets` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `GET` | `/api/knowledge/documents` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `GET` | `/api/knowledge/documents/{document_id}` | Bearer JWT | `SREResponse_dict_str__Any__` |
| `GET` | `/api/knowledge/documents/{document_id}/segments` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `GET` | `/api/knowledge/search` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
| `GET` | `/api/knowledge/segments/search` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
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
| `POST` | `/api/remediate/{session_id}/plan/revise` | Bearer JWT | `SREResponse_RevisePlanResponse_` |
| `POST` | `/api/remediate/{session_id}/rollback` | Bearer JWT | `SREResponse_RollbackResult_` |
| `GET` | `/api/runtime/llm/status` | Bearer JWT | `SREResponse_LLMRuntimeStatusResponse_` |
| `GET` | `/api/sessions` | Bearer JWT | `SREResponse_list_SessionSummary__` |
| `GET` | `/api/sessions/{session_id}` | Bearer JWT | `SREResponse_DiagnosisSession_` |
| `GET` | `/api/sessions/{session_id}/events` | Bearer JWT | `SREResponse_list_dict_str__Any___` |
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
| `plan_revised` | 通用事件 |
| `execution_mocked` | 通用事件 |
| `observation_started` | 通用事件 |
| `observation_result` | 通用事件 |
| `escalation_required` | 通用事件 |
| `diagnosis_started` | 通用事件 |
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

### 3.2 拓扑 WS 事件 `WS /ws/topology`

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

### 3.3 修复时间线关键事件 `GET /api/sessions/{session_id}/events`

```json
{
  "type": "remediation_progress",
  "timestamp": "2026-04-03T13:00:00Z",
  "data": {
    "stage": "approval_accepted",
    "user": "operator",
    "plan_version": 2
  }
}
```

- 修复页关键事件过滤口径（前后端对齐）：
  - 事件类型：`approval_required`、`plan_revised`、`remediation_progress`
  - 当 `type=remediation_progress` 时，仅展示 `stage` 属于：
    - `approval_accepted`、`approval_rejected`
    - `execution_started`、`execution_mocked`
    - `observation_started`、`observation_result`
    - `execution_succeeded`、`execution_failed`、`execution_timeout`
    - `escalation_required`
    - `rollback_started`、`rollback_succeeded`、`rollback_failed`
- 执行步骤展示口径：前端优先使用当前会话诊断结果中的 `recommended_fix.steps`（审批后最终版本）展示“执行步骤”。
- 金丝雀进度口径：当前版本为单次执行，前端使用单条 `batch_status`（`batch=一次性执行`）展示进度和终态。

### 3.4 重复告警响应 `POST /api/handle`（兼容成功信封）

```json
{
  "success": true,
  "data": null,
  "error": {
    "code": "ALERT_DUPLICATE",
    "message": "duplicate alert, see session fp-api-1",
    "details": {
      "session_id": "fp-api-1",
      "incident_key": "fpst:fp-api-1|2026-03-18T12:00:00Z",
      "dedup_reason": "same_incident",
      "identity_source": "fingerprint_starts_at"
    }
  }
}
```

### 3.5 自动诊断触发事件 `WS /ws/alerts`（`type=diagnosis_triggered`）

```json
{
  "type": "diagnosis_triggered",
  "session_id": "alerts",
  "data": {
    "incident_key": "fpst:fp-cube-latency-1|2026-03-18T12:00:00Z",
    "fingerprint": "fp-cube-latency-1",
    "starts_at": "2026-03-18T12:00:00Z",
    "identity_source": "fingerprint_starts_at",
    "alert_name": "CubeStudioWebLatencyP95High",
    "severity": "critical",
    "trigger_reason": "whitelist"
  }
}
```

## 4. 前端绑定关系

- API Client：`sre_agent/frontend/src/api/client.ts`
- 类型定义：`sre_agent/frontend/src/api/types.ts`
- 修复状态管理：`sre_agent/frontend/src/store/remediationStore.ts`
- 修复页面：`sre_agent/frontend/src/pages/Remediation.tsx`
- 拓扑状态管理：`sre_agent/frontend/src/store/topologyStore.ts`
- 拓扑页面：`sre_agent/frontend/src/pages/Topology.tsx`
- WS 管理：`sre_agent/frontend/src/api/ws.ts`

## 5. 变更记录

- 2026-04-03：修复页契约补充，明确会话事件过滤口径与 `remediation_progress.stage` 关键阶段集合；补充执行步骤与单次执行金丝雀进度展示约束。
- 2026-03-30：新增 topology 运行时发现状态与手工触发接口；新增 `/ws/topology`；契约矩阵改为自动同步。
