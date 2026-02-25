from __future__ import annotations

from dataclasses import dataclass
import uuid

from fault_injector.config import InjectorConfig
from fault_injector.orchestrator.engine import FaultOrchestratorEngine


@dataclass(slots=True)
class ActionReport:
    host: str
    inject_command: str
    rollback_command: str
    success: bool


class FaultInjector:
    """Backward-compatible facade that forwards to the new orchestrator engine."""

    def __init__(self, config: InjectorConfig, session_id: str | None = None) -> None:
        self.config = config
        self.session_id = session_id or uuid.uuid4().hex
        self.engine = FaultOrchestratorEngine(config=config, session_id=self.session_id)
        self.channel = self.engine.channel

    def inject_roce_mtu_mismatch(self) -> list[ActionReport]:
        reports = self.engine.run_scenario("rdma_anomaly")
        return [
            ActionReport(
                host=report.host,
                inject_command=report.inject_command,
                rollback_command=report.rollback_command,
                success=report.success,
            )
            for report in reports
        ]

    def rollback(self) -> list[ActionReport]:
        reports = self.engine.rollback()
        return [
            ActionReport(
                host=report.host,
                inject_command=report.inject_command,
                rollback_command=report.rollback_command,
                success=report.success,
            )
            for report in reports
        ]
