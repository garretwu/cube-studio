"""Session persistence and resume helpers."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class SessionStore:
    """Persist load session progress under `reports/sessions/<session_id>/`."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        return self.root_dir / session_id

    def session_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def events_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "events.jsonl"

    def start(
        self,
        *,
        session_id: str,
        mode: str,
        selected_agents: list[str],
        preflight: dict[str, Any],
        plan: list[dict[str, Any]],
        resumed: bool = False,
    ) -> None:
        self.session_dir(session_id).mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session_id,
            "status": "running",
            "mode": mode,
            "selected_agents": selected_agents,
            "started_at": time.time(),
            "updated_at": time.time(),
            "resumed": resumed,
            "preflight": preflight,
            "plan": plan,
            "current_stage_index": 0,
            "agent_results": [],
            "adaptive_events": [],
            "breaking_point": None,
            "system_metrics": {},
        }
        self._write_json(self.session_file(session_id), payload)
        self.append_event(session_id, {"type": "session_started", "mode": mode, "resumed": resumed})

    def load(self, session_id: str) -> dict[str, Any]:
        path = self.session_file(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def update_stage(
        self,
        session_id: str,
        *,
        stage_name: str,
        stage_index: int,
        agent_results: list[dict[str, Any]],
        adaptive_event: dict[str, Any],
        system_metrics: dict[str, Any],
        breaking_point: dict[str, Any] | None,
    ) -> None:
        data = self.load(session_id)
        data["current_stage_index"] = stage_index + 1
        data["updated_at"] = time.time()
        data["agent_results"].extend(agent_results)
        data["adaptive_events"].append(adaptive_event)
        data["system_metrics"] = system_metrics
        data["breaking_point"] = breaking_point
        self._write_json(self.session_file(session_id), data)
        self.append_event(
            session_id,
            {
                "type": "stage_completed",
                "stage": stage_name,
                "stage_index": stage_index,
                "breaking_point": breaking_point is not None,
            },
        )

    def complete(
        self,
        session_id: str,
        *,
        status: str = "completed",
        summary: str = "",
        bottlenecks: list[dict[str, Any]] | None = None,
    ) -> None:
        data = self.load(session_id)
        data["status"] = status
        data["updated_at"] = time.time()
        data["ended_at"] = time.time()
        data["summary"] = summary
        data["bottlenecks"] = bottlenecks or []
        self._write_json(self.session_file(session_id), data)
        self.append_event(session_id, {"type": "session_finished", "status": status})

    def append_event(self, session_id: str, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload["ts"] = time.time()
        path = self.events_file(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
