"""
Session - Session state management for fault injection.

Manages the persistence of fault injection session state.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional
import uuid
import hashlib

logger = logging.getLogger(__name__)


class SessionStatus(str, Enum):
    """Session status."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"
    PAUSED = "paused"


class SessionPhase(str, Enum):
    """Session phase."""
    INIT = "init"
    BASELINE = "baseline"
    INJECT = "inject"
    OBSERVE = "observe"
    RECOVER = "recover"
    VERIFY = "verify"
    REPORT = "report"


@dataclass
class ActiveFault:
    """Active fault information."""
    fault_id: str
    scenario_name: str
    target_node: str
    injected_at: datetime
    params: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "fault_id": self.fault_id,
            "scenario_name": self.scenario_name,
            "target_node": self.target_node,
            "injected_at": self.injected_at.isoformat(),
            "params": self.params,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ActiveFault":
        return cls(
            fault_id=data["fault_id"],
            scenario_name=data["scenario_name"],
            target_node=data["target_node"],
            injected_at=datetime.fromisoformat(data["injected_at"]),
            params=data.get("params", {}),
        )


@dataclass
class ScenarioResult:
    """Result of a scenario execution."""
    scenario_name: str
    inject_success: bool = False
    recover_success: bool = False
    verified: bool = False
    inject_duration_ms: int = 0
    observe_duration_ms: int = 0
    recover_duration_ms: int = 0
    error: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "ScenarioResult":
        return cls(**data)


@dataclass
class Session:
    """
    Session state model.
    
    Persisted to session_dir/session_id/session.json.
    """
    session_id: str
    config_hash: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    status: SessionStatus = SessionStatus.RUNNING
    phase: SessionPhase = SessionPhase.INIT
    active_faults: list[ActiveFault] = field(default_factory=list)
    rollback_journal_path: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    scenario_results: dict[str, ScenarioResult] = field(default_factory=dict)
    baseline_metrics: dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.session_id:
            self.session_id = uuid.uuid4().hex[:8]
    
    @classmethod
    def create(
        cls,
        config_hash: str = "",
        session_dir: str = "./fault-reports/sessions/",
    ) -> "Session":
        """Create a new Session."""
        session_id = uuid.uuid4().hex[:8]
        session = cls(
            session_id=session_id,
            config_hash=config_hash,
        )
        session.rollback_journal_path = f"{session_dir}{session_id}/rollback.jsonl"
        return session
    
    @staticmethod
    def compute_config_hash(config_content: str) -> str:
        """Compute SHA256 hash of config content."""
        return hashlib.sha256(config_content.encode()).hexdigest()[:16]
    
    def save(self, session_dir: str = "./fault-reports/sessions/") -> None:
        """Save Session state to disk."""
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
            "events": self.events,
            "scenario_results": {k: v.to_dict() for k, v in self.scenario_results.items()},
            "baseline_metrics": self.baseline_metrics,
        }
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.debug(f"Session saved: {path}")
    
    @classmethod
    def load(cls, session_id: str, session_dir: str = "./fault-reports/sessions/") -> Optional["Session"]:
        """Load Session state from disk."""
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
            active_faults=[ActiveFault.from_dict(f) for f in data.get("active_faults", [])],
            rollback_journal_path=data.get("rollback_journal_path", ""),
            events=data.get("events", []),
            scenario_results={
                k: ScenarioResult.from_dict(v) 
                for k, v in data.get("scenario_results", {}).items()
            },
            baseline_metrics=data.get("baseline_metrics", {}),
        )
    
    def add_event(self, event: str, details: dict[str, Any] | None = None) -> None:
        """Add an event to the timeline."""
        self.events.append({
            "timestamp": datetime.now().isoformat(),
            "phase": self.phase.value,
            "event": event,
            "details": details or {},
        })
    
    def set_phase(self, phase: SessionPhase) -> None:
        """Set the current phase."""
        old_phase = self.phase
        self.phase = phase
        self.add_event(f"Phase changed: {old_phase.value} → {phase.value}")
        logger.info(f"Session {self.session_id}: Phase {old_phase.value} → {phase.value}")
    
    def add_active_fault(self, fault: ActiveFault) -> None:
        """Add an active fault."""
        self.active_faults.append(fault)
        self.add_event(f"Fault injected: {fault.scenario_name}", {
            "fault_id": fault.fault_id,
            "target": fault.target_node,
        })
    
    def remove_active_fault(self, fault_id: str) -> Optional[ActiveFault]:
        """Remove an active fault."""
        for i, fault in enumerate(self.active_faults):
            if fault.fault_id == fault_id:
                removed = self.active_faults.pop(i)
                self.add_event(f"Fault recovered: {removed.scenario_name}", {
                    "fault_id": fault_id,
                })
                return removed
        return None
    
    def complete(self) -> None:
        """Mark session as completed."""
        self.status = SessionStatus.COMPLETED
        self.finished_at = datetime.now()
        self.add_event("Session completed")
    
    def fail(self, error: str) -> None:
        """Mark session as failed."""
        self.status = SessionStatus.FAILED
        self.finished_at = datetime.now()
        self.add_event("Session failed", {"error": error})
    
    @property
    def session_path(self) -> str:
        """Get the session directory path."""
        return str(Path(self.rollback_journal_path).parent)