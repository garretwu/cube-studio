"""
Orchestrator module - Fault injection orchestration engine.

This module manages the complete lifecycle of fault injection:
- Session state management
- Phase orchestration (baseline → inject → observe → recover → verify → report)
- Watchdog for auto-recovery
- Scheduler for combined scenarios
"""
from fault_injector.orchestrator.engine import FaultOrchestrator
from fault_injector.orchestrator.scheduler import ScheduledEvent, ScenarioScheduler
from fault_injector.orchestrator.session import Session, SessionPhase, SessionStatus

__all__ = [
    "FaultOrchestrator",
    "ScheduledEvent",
    "ScenarioScheduler",
    "Session",
    "SessionPhase",
    "SessionStatus",
]
