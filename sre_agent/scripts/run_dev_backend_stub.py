from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any, AsyncIterator
from uuid import uuid4

import uvicorn

from sre_agent.config import SREAgentConfig
from sre_agent.models.alert import Alert
from sre_agent.models.diagnosis import DiagnosisResult, DiagnosisSession, Observation, RankedRootCause, ThinkingStep, ThinkingTrace
from sre_agent.server import create_app


class DemoDiagnosisRunner:
    async def adiagnose(self, alert: Alert, trace_callback=None) -> DiagnosisSession:  # noqa: ANN001
        if trace_callback is not None:
            await trace_callback(
                {
                    "type": "thinking_step",
                    "session_id": alert.fingerprint,
                    "data": {
                        "step": 1,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "thought": "collecting GPU and pod evidence",
                        "action_type": "tool_call",
                        "tool_name": "gpu.get_metrics",
                        "tool_params": {"node": alert.labels.get("node", "node-a")},
                    },
                }
            )
            await trace_callback(
                {
                    "type": "tool_result",
                    "session_id": alert.fingerprint,
                    "data": {
                        "tool": "gpu.get_metrics",
                        "params": {"node": alert.labels.get("node", "node-a")},
                        "result": {"success": True, "data": {"utilization": 92}},
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                }
            )

        diagnosis = DiagnosisResult(
            root_cause="gpu contention",
            root_cause_layer="service",
            confidence=0.9,
            impact_summary="latency spike on inference service",
            triage_priority="P1",
            diagnosis_certainty="confirmed",
            ranked_candidates=[
                RankedRootCause(
                    rank=1,
                    root_cause="gpu contention",
                    root_cause_layer="service",
                    confidence=0.9,
                    evidence_summary="gpu utilization remains > 90%",
                )
            ],
        )
        trace = ThinkingTrace(
            steps=[
                ThinkingStep(
                    step=1,
                    thought="collecting GPU and pod evidence",
                    action_type="tool_call",
                    tool_name="gpu.get_metrics",
                    tool_params={"node": alert.labels.get("node", "node-a")},
                ),
                Observation(
                    tool="gpu.get_metrics",
                    params={"node": alert.labels.get("node", "node-a")},
                    result={"success": True, "data": {"utilization": 92}},
                ),
                ThinkingStep(
                    step=2,
                    thought="root cause is likely GPU contention",
                    action_type="conclude",
                    confidence=0.9,
                ),
            ]
        )
        return DiagnosisSession(
            session_id=alert.fingerprint or uuid4().hex,
            alert=alert,
            status="diagnosed",
            diagnosis_result=diagnosis,
            trace=trace,
        )


class DemoChatHandler:
    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, Any]]] = {}

    async def __call__(self, message: str) -> str:
        return await self.chat(message, user_id="anonymous")

    async def chat(self, message: str, *, user_id: str = "anonymous") -> str:
        uid = user_id.strip() or "anonymous"
        items = self._history.setdefault(uid, [])
        now = datetime.now(UTC).isoformat()
        items.append(
            {
                "id": f"user-{uuid4().hex}",
                "role": "user",
                "content": message,
                "created_at": now,
            }
        )
        reply = f"[demo-assistant] received: {message}"
        items.append(
            {
                "id": f"assistant-{uuid4().hex}",
                "role": "assistant",
                "content": reply,
                "created_at": now,
            }
        )
        return reply

    async def chat_stream(self, message: str, *, user_id: str = "anonymous") -> AsyncIterator[str]:
        reply = await self.chat(message, user_id=user_id)
        midpoint = max(1, len(reply) // 2)
        yield reply[:midpoint]
        yield reply[midpoint:]

    async def get_history(self, *, user_id: str = "anonymous") -> list[dict[str, Any]]:
        uid = user_id.strip() or "anonymous"
        return list(self._history.get(uid, []))


def build_app() -> Any:
    config = SREAgentConfig()
    return create_app(
        config=config,
        diagnosis_runner=DemoDiagnosisRunner(),
        chat_handler=DemoChatHandler(),
        require_llm_ready=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SRE backend with demo diagnosis/chat handlers.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18090)
    args = parser.parse_args()
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
