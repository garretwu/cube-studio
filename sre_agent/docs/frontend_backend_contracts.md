# Frontend-Backend Contracts for SRE Agent

This document defines the data contracts between the SRE Agent FastAPI backend and the React frontend.

## WebSocket Events

### Endpoint: `/ws/thinking-trace/{session_id}`

Streams diagnosis/remediation trace events for a specific session.

**Query Parameters:**
- `last_event_id` (optional): Resume from this event ID for reconnection support

### Endpoint: `/ws/alerts`

Streams real-time alert updates (upsert/remove actions).

**Query Parameters:**
- `last_event_id` (optional): Resume from this event ID for reconnection support

---

## Core Event Schema

### WSEvent

The unified WebSocket event envelope.

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | `string` | Schema version, currently `"1.0"` |
| `type` | `EventType` | Event type enum value |
| `session_id` | `string` | Session identifier (non-blank) |
| `timestamp` | `datetime` | ISO 8601 UTC timestamp |
| `data` | `object` | Event-specific payload |

### EventType Enum

| Value | Description |
|-------|-------------|
| `thinking_step` | Agent thought/reasoning step |
| `tool_call` | Tool invocation initiated |
| `tool_result` | Tool execution result |
| `diagnosis_result` | Diagnosis completed |
| `approval_required` | Waiting for human approval |
| `loop_start` | Remediation loop started |
| `loop_progress` | Remediation loop iteration update |
| `remediation_progress` | Individual remediation step progress |
| `alert` | Alert state change (upsert/remove) |
| `error` | Error occurred |
| `done` | Session completed |

---

## Diagnosis Domain Models

### ThinkingStep

A single thought/action step in the diagnosis trace.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `step` | `integer` | Yes | Step number (≥1) |
| `timestamp` | `datetime` | Yes | ISO 8601 UTC timestamp |
| `thought` | `string` | Yes | Agent's reasoning content |
| `action_type` | `ActionType` | Yes | One of: `tool_call`, `conclude`, `remediate` |
| `tool_name` | `string` | No | Tool name if action_type is `tool_call` |
| `tool_params` | `object` | No | Tool parameters |
| `confidence` | `number` | No | Confidence score [0.0, 1.0] |

### Observation

Tool observation in the diagnosis trace.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool` | `string` | Yes | Tool name that produced this observation |
| `params` | `object` | Yes | Parameters passed to the tool |
| `result` | `object` | Yes | Tool execution result |
| `timestamp` | `datetime` | Yes | ISO 8601 UTC timestamp |

### ThinkingTrace

Ordered chain of thought/observation items.

| Field | Type | Description |
|-------|------|-------------|
| `steps` | `array` | Array of `ThinkingStep` or `Observation` items |

**Note:** The `steps` array is heterogeneous. Use the presence of `step` field to distinguish `ThinkingStep` from `Observation`.

### Hypothesis

Diagnosis hypothesis with validation outcome.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | `string` | Yes | Hypothesis description |
| `status` | `string` | Yes | One of: `testing`, `confirmed`, `eliminated` |
| `evidence_for` | `string[]` | Yes | Supporting evidence |
| `evidence_against` | `string[]` | Yes | Contradicting evidence |
| `confidence` | `number` | Yes | Confidence score [0.0, 1.0] |

### DiagnosisResult

Complete diagnosis output.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `root_cause` | `string` | Yes | Primary root cause description |
| `root_cause_layer` | `RootCauseLayer` | Yes | One of: `hardware`, `network`, `os`, `platform`, `service` |
| `root_cause_entities` | `string[]` | Yes | Affected entity identifiers |
| `confidence` | `number` | Yes | Overall confidence [0.0, 1.0] |
| `hypotheses` | `Hypothesis[]` | Yes | Tested hypotheses |
| `propagation_chain` | `PropagationStep[]` | Yes | Fault propagation path |
| `impact_summary` | `string` | Yes | Human-readable impact description |
| `affected_services` | `string[]` | Yes | List of affected services |
| `recommended_fix` | `RemediationPlan` | No | Suggested remediation |
| `triage_priority` | `TriagePriority` | Yes | One of: `P0`, `P1`, `P2`, `P3` |
| `ranked_candidates` | `RankedRootCause[]` | No | Alternative root cause candidates |
| `diagnosis_certainty` | `DiagnosisCertainty` | Yes | One of: `confirmed`, `probable`, `ambiguous` |

**Business Rules:**
- If `diagnosis_certainty` is `confirmed`, then `confidence` must be ≥ 0.85
- If `ranked_candidates` is present, first entry must match `root_cause`
- `ranked_candidates` must be ordered by `rank` ascending

### DiagnosisSession

Lifecycle model for diagnosis/remediation execution.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | `string` | Yes | Unique session identifier |
| `alert` | `Alert` | Yes | Triggering alert |
| `status` | `string` | Yes | Session status (see below) |
| `diagnosis_result` | `DiagnosisResult` | No | Diagnosis output |
| `trace` | `ThinkingTrace` | No | Reasoning trace |
| `re_diagnosis_round` | `integer` | Yes | Re-diagnosis iteration count (≥0) |
| `duration_seconds` | `integer` | Yes | Total duration (≥0) |
| `outcome` | `string` | No | Final outcome description |

**Status Values:**
- `diagnosing` - Active diagnosis in progress
- `diagnosed` - Diagnosis completed
- `approval_required` - Waiting for human approval
- `remediating` - Remediation in progress
- `rejected` - Remediation rejected by user
- `re_diagnosed` - Re-diagnosis completed
- `resolved` - Issue resolved
- `failed` - Remediation failed
- `escalated` - Escalated to human
- `timeout` - Session timed out

---

## Alert Domain Models

### Alert

Alert from monitoring system.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `alert_name` | `string` | Yes | Alert rule name |
| `severity` | `Severity` | Yes | One of: `critical`, `warning`, `info` |
| `labels` | `object` | Yes | Key-value labels |
| `annotations` | `object` | Yes | Key-value annotations |
| `starts_at` | `datetime` | Yes | Alert start timestamp |
| `ends_at` | `datetime` | No | Alert end timestamp |
| `fingerprint` | `string` | Yes | Deduplication fingerprint |
| `status` | `AlertStatus` | Yes | One of: `firing`, `resolved`, `silenced` |
| `source` | `string` | No | Alert source identifier |

### AlertCluster

Grouped alerts for UI display.

| Field | Type | Description |
|-------|------|-------------|
| `cluster_id` | `string` | Cluster identifier |
| `summary` | `string` | Cluster summary text |
| `severity` | `Severity` | Highest severity in cluster |
| `alerts` | `string[]` | List of alert fingerprints |

---

## Remediation Domain Models

### RemediationPlan

Executable remediation plan.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `plan_id` | `string` | Yes | Unique plan identifier |
| `root_cause` | `string` | Yes | Target root cause |
| `description` | `string` | Yes | Human-readable description |
| `steps` | `RemediationAction[]` | Yes | Execution steps |
| `canary` | `CanaryConfig` | No | Canary deployment config |
| `estimated_impact` | `string` | Yes | Impact assessment |
| `confidence` | `number` | Yes | Plan confidence [0.0, 1.0] |
| `priority` | `Priority` | Yes | One of: `P0`, `P1`, `P2` |
| `safety_level` | `string` | No | Safety classification |

### RemediationAction

Single remediation step.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `step_id` | `integer` | Yes | Step number |
| `description` | `string` | Yes | Step description |
| `tool` | `string` | Yes | Tool to execute |
| `params` | `object` | Yes | Tool parameters |
| `rollback_tool` | `string` | No | Tool for rollback |
| `verification` | `VerificationConfig` | Yes | Verification method |
| `timeout` | `integer` | Yes | Timeout in seconds |

### VerificationConfig

Post-action verification.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `method` | `string` | Yes | One of: `promql`, `tool_call`, `wait` |
| `query` | `string` | No | PromQL query (if method is `promql`) |
| `tool` | `string` | No | Tool name (if method is `tool_call`) |
| `wait_seconds` | `integer` | No | Wait duration (if method is `wait`) |

### LoopResult

Remediation loop outcome.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `string` | Session identifier |
| `outcome` | `string` | One of: `resolved`, `partially_resolved`, `exhausted`, `escalated`, `re_diagnosed` |
| `winning_candidate` | `object` | Winning root cause candidate (if resolved) |
| `attempts` | `CandidateAttempt[]` | All remediation attempts |
| `total_duration_seconds` | `number` | Total loop duration |
| `re_diagnosis_context` | `object` | Re-diagnosis context (if applicable) |

---

## WebSocket Event Data Payloads

### thinking_step Event

```json
{
  "schema_version": "1.0",
  "type": "thinking_step",
  "session_id": "abc123",
  "timestamp": "2026-03-27T06:00:00Z",
  "data": {
    "event_id": "1",
    "step": 1,
    "thought": "Analyzing CPU metrics to identify bottleneck...",
    "action_type": "tool_call",
    "tool_name": "prometheus_query",
    "confidence": 0.85
  }
}
```

### tool_call Event

```json
{
  "schema_version": "1.0",
  "type": "tool_call",
  "session_id": "abc123",
  "timestamp": "2026-03-27T06:00:01Z",
  "data": {
    "event_id": "2",
    "tool": "prometheus_query",
    "params": {"query": "rate(cpu_usage[5m])"}
  }
}
```

### tool_result Event

```json
{
  "schema_version": "1.0",
  "type": "tool_result",
  "session_id": "abc123",
  "timestamp": "2026-03-27T06:00:02Z",
  "data": {
    "event_id": "3",
    "tool": "prometheus_query",
    "result": {"value": 0.92}
  }
}
```

### diagnosis_result Event

```json
{
  "schema_version": "1.0",
  "type": "diagnosis_result",
  "session_id": "abc123",
  "timestamp": "2026-03-27T06:00:30Z",
  "data": {
    "event_id": "10",
    "diagnosis_result": { /* DiagnosisResult object */ }
  }
}
```

### alert Event

```json
{
  "schema_version": "1.0",
  "type": "alert",
  "session_id": "alerts",
  "timestamp": "2026-03-27T06:00:00Z",
  "data": {
    "event_id": "1",
    "action": "upsert",
    "fingerprint": "fp-123",
    "status": "firing",
    "alert": { /* Alert object */ }
  }
}
```

**Alert data `action` values:**
- `upsert` - Alert created or updated
- `remove` - Alert resolved/removed

---

## API Response Envelope

### SREApiEnvelope\<T\>

Standard API response wrapper.

| Field | Type | Description |
|-------|------|-------------|
| `success` | `boolean` | Operation success flag |
| `data` | `T` | Response payload (null if failed) |
| `error` | `ErrorInfo` | Error details (null if success) |
| `trace_id` | `string` | Request trace ID |
| `timestamp` | `datetime` | Response timestamp |

### ErrorInfo

| Field | Type | Description |
|-------|------|-------------|
| `code` | `string` | Error code |
| `message` | `string` | Human-readable message |
| `details` | `unknown` | Additional error context |
| `trace_id` | `string` | Request trace ID |

---

## Type Correspondence Table

| Backend (Python) | Frontend (TypeScript) | Notes |
|------------------|----------------------|-------|
| `datetime` | `string` | ISO 8601 format |
| `Enum` members | String union types | Values must match exactly |
| `StrictFrozenModel` subclasses | Interface types | Fields match 1:1 |
| `list[T]` | `T[]` | Array types |
| `dict[str, Any]` | `Record<string, unknown>` | Object types |
| `T | None` | `T \| null` | Nullable types |
| `float` | `number` | Numeric types |

---

## Version Compatibility

- **Schema Version:** `1.0`
- **Breaking changes** require schema version bump
- **Additive changes** (new optional fields, new event types) are backward compatible

---

## Implementation Notes

### Backend

1. Events are published via `InMemoryTracePublisher` (see `sre_agent/server.py`)
2. Auto-generated `event_id` in `data` for reconnection support
3. Max 1000 events per session (configurable via `ws_max_events_per_session`)
4. WebSocket authentication via JWT (see `sre_agent/auth/jwt.py`)

### Frontend

1. Reconnect with `?last_event_id=<last_seen_id>` to resume stream
2. Handle heterogeneous `steps` array in `ThinkingTrace` by checking for `step` field
3. All timestamps are UTC, format as needed for display

---

## Related Files

- `sre_agent/models/events.py` - Backend event definitions
- `sre_agent/models/diagnosis.py` - Backend diagnosis models
- `sre_agent/models/alert.py` - Backend alert models
- `sre_agent/models/remediation.py` - Backend remediation models
- `sre_agent/api/websocket.py` - WebSocket endpoints
- `sre_agent/server.py` - InMemoryTracePublisher implementation
- `sre_agent/frontend/src/api/types.ts` - Frontend type definitions