from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

UTC = timezone.utc
SESSION_ID = "diag-session-20260330"


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


BASE_TRACE = {
    "steps": [
        {
            "step": 1,
            "timestamp": (datetime.now(UTC) - timedelta(minutes=3)).isoformat().replace("+00:00", "Z"),
            "thought": "已有历史证据表明 node-gpu-01 存在 GPU hotspot，与推理队列上升时间窗高度重叠。",
            "action_type": "tool_call",
            "tool_name": "metrics.gpu_window",
            "tool_params": {"node": "node-gpu-01", "window": "5m"},
            "confidence": 0.81,
        },
        {
            "tool": "metrics.gpu_window",
            "params": {"node": "node-gpu-01", "window": "5m"},
            "result": {
                "gpu_utilization_pct": 92,
                "queue_depth_p95": 37,
                "scheduler_delay_ms_p95": 460,
            },
            "timestamp": (datetime.now(UTC) - timedelta(minutes=2, seconds=30)).isoformat().replace("+00:00", "Z"),
        },
    ]
}

SESSION_TEMPLATE: dict[str, Any] = {
    "session_id": SESSION_ID,
    "started_at": (datetime.now(UTC) - timedelta(minutes=8)).isoformat().replace("+00:00", "Z"),
    "status": "running",
    "summary": "GPU hotspot diagnosis session",
    "trace": deepcopy(BASE_TRACE),
    "diagnosis_result": {
        "root_cause": "node-gpu-01 上的推理负载集中导致 GPU hotspot。",
        "impact_summary": "主要影响高优先级推理任务，队列时延在最近 5 分钟明显抬升。",
        "confidence": 0.82,
        "next_action": "优先检查异常进程与热点实例分布。",
    },
}

INITIAL_HISTORY = [
    {
        "id": "assistant-welcome",
        "role": "assistant",
        "content": "当前诊断会话已经接入实时思考流。你可以直接询问根因，也可以继续追问排查建议。",
        "created_at": (datetime.now(UTC) - timedelta(minutes=7)).isoformat().replace("+00:00", "Z"),
    }
]

message_store: dict[str, list[dict[str, Any]]] = {SESSION_ID: deepcopy(INITIAL_HISTORY)}
session_store: dict[str, dict[str, Any]] = {SESSION_ID: deepcopy(SESSION_TEMPLATE)}
event_store: dict[str, list[dict[str, Any]]] = {SESSION_ID: []}


class ChatPayload(BaseModel):
    session_id: str
    content: str
    search_text: str | None = None


class ApprovalPayload(BaseModel):
    approved: bool
    user: str | None = "ui-operator"
    reason: str | None = None
    plan_version: int | None = None


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(session_id, set()).add(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        sockets = self._connections.get(session_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(session_id, None)

    async def broadcast(self, session_id: str, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for websocket in list(self._connections.get(session_id, set())):
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(session_id, websocket)


manager = ConnectionManager()
app = FastAPI(title="SRE Agent Mock Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/diagnosis/session/current")
async def get_current_session(session_id: str | None = None) -> dict[str, Any]:
    resolved_id = session_id or SESSION_ID
    return deepcopy(session_store.get(resolved_id, session_store[SESSION_ID]))


def build_session_summary(session: dict[str, Any]) -> dict[str, Any]:
    alert = session.get("alert") or {}
    return {
        "session_id": session["session_id"],
        "status": session.get("status", "unknown"),
        "alert_name": alert.get("alert_name", "unknown"),
        "severity": alert.get("severity", "warning"),
        "fingerprint": alert.get("fingerprint", session["session_id"]),
        "outcome": session.get("outcome"),
        "duration_seconds": session.get("duration_seconds", 0),
        "updated_at": now_iso(),
    }


@app.get("/api/sessions")
async def get_sessions(limit: int = 50) -> list[dict[str, Any]]:
    session = session_store[SESSION_ID]
    return [build_session_summary(session)][: max(1, int(limit))]


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    return deepcopy(session_store.get(session_id, session_store[SESSION_ID]))


@app.get("/api/sessions/{session_id}/events")
async def get_session_events(session_id: str, limit: int = 200, after: str | None = None) -> list[dict[str, Any]]:
    events = event_store.get(session_id, [])
    if after:
        try:
            start = next(index + 1 for index, event in enumerate(events) if event.get("event_id") == after)
        except StopIteration:
            start = 0
        events = events[start:]
    return deepcopy(events[-max(1, int(limit)):])


@app.get("/api/diagnosis/sessions")
async def get_diagnosis_sessions(limit: int = 50) -> list[dict[str, Any]]:
    session = session_store[SESSION_ID]
    return [build_session_summary(session)][: max(1, int(limit))]

@app.get("/api/chat/history")
async def get_chat_history(session_id: str | None = None) -> list[dict[str, Any]]:
    resolved_id = session_id or SESSION_ID
    return deepcopy(message_store.get(resolved_id, INITIAL_HISTORY))


@app.post("/api/chat")
async def post_chat_message(payload: ChatPayload) -> dict[str, Any]:
    session_id = payload.session_id or SESSION_ID
    user_message = {
        "id": f"user-{int(datetime.now(UTC).timestamp() * 1000)}",
        "role": "user",
        "content": payload.content,
        "created_at": now_iso(),
        "metadata": {"session_id": session_id},
    }
    message_store.setdefault(session_id, deepcopy(INITIAL_HISTORY)).append(user_message)

    final_reply, diagnosis_result = await stream_diagnosis(session_id, payload.content)

    assistant_message = {
        "id": f"assistant-{int(datetime.now(UTC).timestamp() * 1000)}",
        "role": "assistant",
        "content": final_reply,
        "created_at": now_iso(),
        "metadata": {"session_id": session_id},
    }
    message_store.setdefault(session_id, deepcopy(INITIAL_HISTORY)).append(assistant_message)

    session = session_store.setdefault(session_id, deepcopy(SESSION_TEMPLATE))
    session["diagnosis_result"] = diagnosis_result
    session["status"] = "approval_required"
    return {"reply": assistant_message}


@app.post("/api/remediate/{session_id}/approve")
async def approve_remediation(session_id: str, payload: ApprovalPayload) -> dict[str, Any]:
    session = session_store.setdefault(session_id, deepcopy(SESSION_TEMPLATE))
    if not payload.approved:
        session["status"] = "rejected"
        await emit_event(
            session_id,
            "remediation_progress",
            {"stage": "approval_rejected", "user": payload.user, "reason": payload.reason or ""},
            0,
        )
        return {"success": False, "error": {"message": payload.reason or "approval denied"}}

    session["status"] = "remediating"
    await emit_event(
        session_id,
        "remediation_progress",
        {"stage": "execution_started", "user": payload.user, "plan_version": payload.plan_version or 1},
        0,
    )
    await stream_remediation(session_id)
    return {
        "success": True,
        "data": {
            "plan_id": "plan-gpu-hotspot-v1",
            "success": True,
            "steps_completed": 2,
            "steps_total": 2,
            "duration_seconds": 7,
            "error": None,
        },
    }


@app.websocket("/ws/thinking-trace/{session_id}")
async def thinking_trace_ws(websocket: WebSocket, session_id: str) -> None:
    await manager.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
    except Exception:
        manager.disconnect(session_id, websocket)


def get_turn_context(session_id: str) -> tuple[int, str]:
    history = message_store.get(session_id, [])
    user_turns = [message for message in history if message.get("role") == "user"]
    last_assistant = next(
        (message.get("content", "") for message in reversed(history) if message.get("role") == "assistant"),
        "",
    )
    return len(user_turns), str(last_assistant)


def build_stream_schedule(turn_index: int) -> list[float]:
    if turn_index <= 1:
        return [1.0, 1.3, 1.8, 1.5, 2.0, 0.7]
    if turn_index == 2:
        return [0.9, 1.2, 1.6, 1.3, 1.8, 0.6]
    return [0.8, 1.1, 1.5, 1.2, 1.6, 0.5]


async def emit_event(session_id: str, event_type: str, data: dict[str, Any], delay: float) -> None:
    await asyncio.sleep(delay)
    event = {
        "schema_version": "1.0",
        "type": event_type,
        "session_id": session_id,
        "timestamp": now_iso(),
        "data": data,
    }
    event["event_id"] = f"{session_id}-{len(event_store.setdefault(session_id, [])) + 1}"
    event_store.setdefault(session_id, []).append(deepcopy(event))
    await manager.broadcast(session_id, event)
    persist_trace(session_id, event)


def persist_trace(session_id: str, event: dict[str, Any]) -> None:
    session = session_store.setdefault(session_id, deepcopy(SESSION_TEMPLATE))
    trace = session.setdefault("trace", {"steps": []}).setdefault("steps", [])
    if event["type"] == "thinking_step":
        trace.append(
            {
                "step": event["data"].get("step", len(trace) + 1),
                "timestamp": event["timestamp"],
                "thought": event["data"].get("thought"),
                "action_type": event["data"].get("action_type", "conclude"),
                "tool_name": event["data"].get("tool_name"),
                "tool_params": event["data"].get("tool_params"),
                "confidence": event["data"].get("confidence"),
            }
        )
    elif event["type"] == "tool_result":
        trace.append(
            {
                "tool": event["data"].get("tool_name") or event["data"].get("tool") or "tool_result",
                "params": event["data"].get("params", {}),
                "result": event["data"].get("result", {}),
                "timestamp": event["timestamp"],
            }
        )



async def stream_remediation(session_id: str) -> None:
    async def emit(stage: str, data: dict[str, Any] | None = None, delay: float = 0.6) -> None:
        payload = {"stage": stage}
        if data:
            payload.update(data)
        await emit_event(session_id, "remediation_progress", payload, delay)

    await emit("canary_started", {"skill_id": "builtin-vllm-diagnosis", "progress": 10, "progress_label": "canary 10%"}, 0.6)
    await emit_event(
        session_id,
        "tool_call",
        {"tool_name": "run_skill", "params": {"skill_id": "builtin-vllm-diagnosis", "phase": "canary"}},
        0.2,
    )
    await emit("canary_progress", {"progress": 45, "progress_label": "first batch", "vllm_p95_ms": 1720}, 0.8)
    await emit("canary_progress", {"progress": 80, "progress_label": "metrics converging", "gpu_util": 74}, 0.8)
    await emit_event(
        session_id,
        "tool_result",
        {
            "tool_name": "run_skill",
            "params": {"skill_id": "builtin-vllm-diagnosis", "phase": "canary"},
            "result": {"vllm_p95_ms": 1680, "inference_error_rate": 0.004},
        },
        0.2,
    )
    await emit("canary_succeeded", {"progress": 100, "progress_label": "waiting metrics feedback"}, 0.5)
    await emit("observation_result", {"metrics_improved": True, "alert_cleared": True}, 0.7)
    session_store.setdefault(session_id, deepcopy(SESSION_TEMPLATE))["status"] = "remediating"
    await emit("full_rollout_started", {"progress": 35, "progress_label": "gradual rollout"}, 0.6)
    await emit_event(
        session_id,
        "tool_call",
        {"tool_name": "run_skill", "params": {"skill_id": "builtin-platform-health", "phase": "full_rollout_observation"}},
        0.2,
    )
    await emit("full_rollout_progress", {"progress": 72, "progress_label": "full rollout"}, 0.8)
    await emit("full_rollout_succeeded", {"progress": 100, "progress_label": "waiting full metrics"}, 0.8)
    await emit_event(
        session_id,
        "tool_result",
        {
            "tool_name": "run_skill",
            "params": {"skill_id": "builtin-platform-health", "phase": "full_rollout_observation"},
            "result": {"vllm_p95_ms": 1420, "inference_error_rate": 0.001, "alert_status": "resolved"},
        },
        0.2,
    )
    session_store.setdefault(session_id, deepcopy(SESSION_TEMPLATE))["status"] = "resolved"
    await emit("alert_recovered", {"progress": 100, "progress_label": "alert resolved"}, 0.6)
    session_store.setdefault(session_id, deepcopy(SESSION_TEMPLATE))["status"] = "closed"
    await emit("session_closed", {"progress": 100, "progress_label": "session closed"}, 0.4)


async def stream_diagnosis(session_id: str, content: str) -> tuple[str, dict[str, Any]]:
    prompt = content.strip() or "current alert"
    lowered = prompt.lower()
    turn_index, last_assistant_reply = get_turn_context(session_id)
    schedule = build_stream_schedule(turn_index)
    continuation_mode = turn_index > 1

    if "network" in lowered:
        tool_name = "metrics.network_errors"
        root_cause = "Cross-node network jitter is more likely than a single GPU hotspot."
        impact_summary = "Packet loss and retransmits are increasing end-to-end latency across the distributed inference path."
        tool_result = {
            "packet_loss_pct": 1.9,
            "rdma_retransmits": 214,
            "impacted_nodes": ["node-gpu-01", "node-gpu-03"],
        }
        if continuation_mode:
            final_reply = (
                "Continuing the same diagnosis thread, the new evidence now points more strongly to network jitter than GPU saturation. "
                "Check TOR packet loss, RDMA retransmits, and the impacted-node time window before treating this as a switch-side burst."
            )
        else:
            final_reply = (
                "The live evidence points more strongly to network jitter than GPU saturation. "
                "Check TOR packet loss, RDMA retransmits, and the impacted-node time window before treating this as a switch-side burst."
            )
    else:
        tool_name = "metrics.gpu_hotspot"
        root_cause = "node-gpu-01 still shows a sustained GPU hotspot with queue pressure rising in the same window."
        impact_summary = "The blast radius remains local to one hot node, so this still looks like resource contention instead of a cluster-wide outage."
        tool_result = {
            "gpu_utilization_pct": 94,
            "queue_depth_p95": 41,
            "top_process": "python llm_worker.py --batch 32",
        }
        if continuation_mode:
            final_reply = (
                "Continuing from the previous conclusion, the new live evidence still points to single-node GPU contention. "
                "I would keep focusing on node-gpu-01, inspect abnormal processes, and compare the current hotspot with the last scheduling change."
            )
        else:
            final_reply = (
                "Across the live reasoning chain, this still looks like single-node GPU contention. "
                "Start with node-gpu-01, inspect abnormal processes, and compare the hotspot with the last scheduling change before draining traffic."
            )

    diagnosis_result = {
        "root_cause": root_cause,
        "impact_summary": impact_summary,
        "confidence": 0.9 if continuation_mode else 0.88,
        "next_action": "Keep the investigation in the same session and attach one more round of evidence before remediation.",
        "recommended_fix": {
            "plan_id": "plan-gpu-hotspot-v1",
            "root_cause": root_cause,
            "description": "Canary the hot-node drain, observe metrics, then roll out the repair.",
            "steps": [
                {
                    "step_id": 1,
                    "description": "Shift 10% traffic away from node-gpu-01 and run vLLM diagnosis skill.",
                    "tool": "run_skill",
                    "params": {"skill_id": "builtin-vllm-diagnosis", "phase": "canary"},
                    "verification": {"method": "wait", "wait_seconds": 60},
                    "timeout": 120,
                },
                {
                    "step_id": 2,
                    "description": "Apply the traffic repair globally after metrics confirm the canary.",
                    "tool": "run_skill",
                    "params": {"skill_id": "builtin-platform-health", "phase": "full_rollout"},
                    "verification": {"method": "wait", "wait_seconds": 120},
                    "timeout": 240,
                },
            ],
            "canary": {
                "enabled": True,
                "target_percentage": 10,
                "monitor_duration": 60,
                "success_criteria": [{"metric": "vllm_p95_ms", "operator": "<=", "value": 1800}],
            },
            "estimated_impact": "low",
            "confidence": 0.88,
            "priority": "P1",
        },
    }

    continuity_hint = (
        "This is a follow-up turn in the same diagnosis session, so the chain should reuse the prior conclusion."
        if continuation_mode
        else "This is the first turn in the current diagnosis session, so the chain is building the baseline conclusion."
    )
    memory_hint = (
        f"Last assistant summary: {last_assistant_reply[:80]}"
        if continuation_mode and last_assistant_reply
        else None
    )

    events = [
        (
            "thinking_step",
            {
                "step": 1,
                "thought": f"Received turn {turn_index}: '{prompt}'. First align the new question with the existing session evidence. {continuity_hint}",
                "action_type": "tool_call",
                "tool_name": tool_name,
                "tool_params": {"session_id": session_id, "window": "5m", "turn": turn_index},
                "confidence": 0.74 if continuation_mode else 0.71,
            },
            schedule[0],
        ),
        (
            "tool_call",
            {
                "tool_name": tool_name,
                "params": {"session_id": session_id, "window": "5m", "turn": turn_index},
            },
            schedule[1],
        ),
        (
            "tool_result",
            {
                "tool_name": tool_name,
                "params": {"session_id": session_id, "window": "5m", "turn": turn_index},
                "result": {**tool_result, "conversation_turn": turn_index, "continuation": continuation_mode},
            },
            schedule[2],
        ),
        (
            "thinking_step",
            {
                "step": 2,
                "thought": (
                    f"The main evidence is back. Compressing impact and root cause into an operator-ready summary. {memory_hint}"
                    if memory_hint
                    else "The main evidence is back. Compressing impact and root cause into an operator-ready summary."
                ),
                "action_type": "conclude",
                "confidence": 0.9 if continuation_mode else 0.88,
            },
            schedule[3],
        ),
        (
            "diagnosis_result",
            diagnosis_result,
            schedule[4],
        ),
        (
            "done",
            {"status": "completed", "turn": turn_index},
            schedule[5],
        ),
    ]

    for event_type, data, delay in events:
        await emit_event(session_id, event_type, data, delay)

    return final_reply, diagnosis_result


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(app, host="127.0.0.1", port=8787, log_level="info")
