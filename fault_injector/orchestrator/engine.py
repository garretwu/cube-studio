from __future__ import annotations

from channel.ssh import HostSpec, SSHChannel
from fault_injector.agents.base import BaseAgent
from fault_injector.agents.os_fault import OSFaultAgent
from fault_injector.config import InjectorConfig
from fault_injector.orchestrator.scheduler import StepScheduler
from fault_injector.orchestrator.session import ActionResult, FaultStep, OrchestratorSession
from fault_injector.orchestrator.watchdog import ExecutionWatchdog
from fault_injector.reporting.resilience import summarize_resilience
from fault_injector.safety.guard import SafetyGuard
from fault_injector.safety.rollback import RollbackEntry, RollbackJournal
from fault_injector.scenarios.registry import ScenarioRegistry
from fault_injector.scenarios.rdma_anomaly import RDMAMtuAnomalyScenario
from fault_injector.scenarios.vllm_latency import VLLMLatencyScenario


class FaultOrchestratorEngine:
    """Orchestrator composes scenario + guard + agent + rollback/reporting."""

    def __init__(
        self,
        config: InjectorConfig,
        session_id: str,
        channel: SSHChannel | None = None,
        agent: BaseAgent | None = None,
        scheduler: StepScheduler | None = None,
        guard: SafetyGuard | None = None,
        watchdog: ExecutionWatchdog | None = None,
    ) -> None:
        self.config = config
        self.session = OrchestratorSession(session_id=session_id, scenario="")
        self.channel = channel or SSHChannel(mode=config.mode)
        self.agent = agent or OSFaultAgent(self.channel)
        self.scheduler = scheduler or StepScheduler()
        self.guard = guard or SafetyGuard()
        self.watchdog = watchdog or ExecutionWatchdog()
        self.journal = RollbackJournal(config.wal_path)

        self.registry = ScenarioRegistry()
        self.registry.register(RDMAMtuAnomalyScenario())
        self.registry.register(VLLMLatencyScenario())

        for srv in config.servers:
            self.channel.seed_simulated_mtu(srv.name, srv.interface, srv.original_mtu)

    def run_scenario(self, scenario_name: str) -> list[ActionResult]:
        scenario = self.registry.get(scenario_name)
        self.session.scenario = scenario_name
        steps = self.scheduler.schedule(scenario.build_steps(self.config))
        self.session.step_count = len(steps)

        reports: list[ActionResult] = []
        for step in steps:
            self.guard.validate_step(step)
            self.journal.record_step(self.session.session_id, step)
            report = self.agent.execute(step, timeout=self.config.timeout)
            self.session.record(report)
            reports.append(report)
        return reports

    def rollback(self) -> list[ActionResult]:
        reports: list[ActionResult] = []
        entries = self.journal.load(self.session.session_id)
        for entry in reversed(entries):
            step = self._entry_to_step(entry)
            self.guard.validate_step(step)
            report = self.agent.rollback(step, timeout=self.config.timeout)
            reports.append(report)
        self.journal.remove_session(self.session.session_id)
        return reports

    def summarize(self) -> dict[str, object]:
        return summarize_resilience(self.session.reports)

    def _entry_to_step(self, entry: RollbackEntry) -> FaultStep:
        srv = next(s for s in self.config.servers if s.name == entry.host)
        return FaultStep(
            host=HostSpec(name=srv.name, host=srv.host, user=srv.user, port=srv.port),
            inject_command="",
            rollback_command=entry.rollback_command,
        )
