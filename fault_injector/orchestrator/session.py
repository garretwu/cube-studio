"""
Session state persistence for orchestrator runs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"
    PAUSED = "paused"


class SessionPhase(str, Enum):
    INIT = "init"
    BASELINE = "baseline"
    INJECT = "inject"
    OBSERVE = "observe"
    RECOVER = "recover"
    VERIFY = "verify"
    REPORT = "report"


@dataclass
class ActiveFault:
    fault_id: str
    scenario_name: str
    target_node: str
    injected_at: datetime
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "scenario_name": self.scenario_name,
            "target_node": self.target_node,
            "injected_at": self.injected_at.isoformat(),
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActiveFault":
        return cls(
            fault_id=data["fault_id"],
            scenario_name=data["scenario_name"],
            target_node=data["target_node"],
            injected_at=datetime.fromisoformat(data["injected_at"]),
            params=data.get("params", {}),
        )


@dataclass
class ScenarioResult:
    scenario_name: str
    inject_success: bool = False
    recover_success: bool = False
    verified: bool = False
    inject_duration_ms: int = 0
    observe_duration_ms: int = 0
    recover_duration_ms: int = 0
    error: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScenarioResult":
        return cls(**data)


@dataclass
class Session:
    session_id: str
    config_hash: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    status: SessionStatus = SessionStatus.RUNNING
    phase: SessionPhase = SessionPhase.INIT
    active_faults: list[ActiveFault] = field(default_factory=list)
    rollback_journal_path: str = ""
    baseline_path: str = ""
    metrics_dir: str = ""
    report_dir: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    scenario_results: dict[str, ScenarioResult] = field(default_factory=dict)
    baseline_metrics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        config_hash: str = "",
        session_dir: str = "./fault_injector/fault_reports/sessions/",
    ) -> "Session":
        session_id = uuid.uuid4().hex[:8]
        base_dir = Path(session_dir) / session_id
        session = cls(session_id=session_id, config_hash=config_hash)
        session.rollback_journal_path = str(base_dir / "rollback.jsonl")
        session.baseline_path = str(base_dir / "baseline.json")
        session.metrics_dir = str(base_dir / "metrics")
        session.report_dir = str(base_dir / "report")
        return session

    @staticmethod
    def compute_config_hash(config_content: str) -> str:
        return hashlib.sha256(config_content.encode()).hexdigest()[:16]

    def save(self, session_dir: str = "./fault_injector/fault_reports/sessions/") -> None:
        path = Path(session_dir) / self.session_id / "session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": self.session_id,
            "config_hash": self.config_hash,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status.value,
            "phase": self.phase.value,
            "active_faults": [f.to_dict() for f in self.active_faults],
            "rollback_journal_path": self.rollback_journal_path,
            "baseline_path": self.baseline_path,
            "metrics_dir": self.metrics_dir,
            "report_dir": self.report_dir,
            "events": self.events,
            "scenario_results": {k: v.to_dict() for k, v in self.scenario_results.items()},
            "baseline_metrics": self.baseline_metrics,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, session_id: str, session_dir: str = "./fault_injector/fault_reports/sessions/") -> "Session | None":
        path = Path(session_dir) / session_id / "session.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            session_id=data["session_id"],
            config_hash=data.get("config_hash", ""),
            started_at=datetime.fromisoformat(data["started_at"]),
            finished_at=datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else None,
            status=SessionStatus(data["status"]),
            phase=SessionPhase(data["phase"]),
            active_faults=[ActiveFault.from_dict(item) for item in data.get("active_faults", [])],
            rollback_journal_path=data.get("rollback_journal_path", ""),
            baseline_path=data.get("baseline_path", ""),
            metrics_dir=data.get("metrics_dir", ""),
            report_dir=data.get("report_dir", ""),
            events=data.get("events", []),
            scenario_results={
                k: ScenarioResult.from_dict(v)
                for k, v in data.get("scenario_results", {}).items()
            },
            baseline_metrics=data.get("baseline_metrics", {}),
        )

    def add_event(self, event: str, details: dict[str, Any] | None = None) -> None:
        self.events.append(
            {
                "timestamp": datetime.now().isoformat(),
                "phase": self.phase.value,
                "event": event,
                "details": details or {},
            }
        )

    def set_phase(self, phase: SessionPhase) -> None:
        old = self.phase
        self.phase = phase
        self.add_event("phase_changed", {"from": old.value, "to": phase.value})

    def add_active_fault(self, fault: ActiveFault) -> None:
        self.active_faults.append(fault)
        self.add_event("fault_injected", {"fault_id": fault.fault_id, "scenario_name": fault.scenario_name})

    def remove_active_fault(self, fault_id: str) -> ActiveFault | None:
        for idx, fault in enumerate(self.active_faults):
            if fault.fault_id == fault_id:
                removed = self.active_faults.pop(idx)
                self.add_event("fault_recovered", {"fault_id": fault_id, "scenario_name": removed.scenario_name})
                return removed
        return None

    def complete(self) -> None:
        self.status = SessionStatus.COMPLETED
        self.finished_at = datetime.now()
        self.add_event("session_completed")

    def fail(self, error: str) -> None:
        self.status = SessionStatus.FAILED
        self.finished_at = datetime.now()
        self.add_event("session_failed", {"error": error})

    def mark_recovered(self) -> None:
        self.status = SessionStatus.RECOVERED
        self.finished_at = datetime.now()
        self.add_event("session_recovered")

    @property
    def session_path(self) -> str:
        return str(Path(self.rollback_journal_path).parent)

