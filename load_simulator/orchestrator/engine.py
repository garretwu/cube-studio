"""LoadOrchestrator — coordinates all agents and collects a SessionResult."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.agents.bottleneck import BottleneckAnalyzer
from load_simulator.agents.finetune import FineTuneAgent
from load_simulator.agents.inference import InferenceAgent
from load_simulator.agents.monitor import MetricsMonitor
from load_simulator.agents.notebook import NotebookAgent
from load_simulator.agents.pipeline import PipelineAgent
from load_simulator.config.schema import LoadSimulatorConfig


@dataclass
class SessionResult:
    """Top-level result for a completed load-test session.

    Attributes:
        session_id:       Unique identifier for this session.
        duration_seconds: Approximate wall-clock time of the session.
        agent_results:    One :class:`AgentResult` per agent that ran.
        bottlenecks:      List of bottleneck finding dicts from the analyzer.
        summary:          Human-readable text report (bottleneck analysis).
        system_metrics:   Aggregated system metrics collected during the run.
    """

    session_id: str
    duration_seconds: float
    agent_results: list[AgentResult] = field(default_factory=list)
    bottlenecks: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    system_metrics: dict[str, float] = field(default_factory=dict)


class LoadOrchestrator:
    """Builds and runs all selected agents concurrently.

    Usage::

        cfg = LoadSimulatorConfig()
        orchestrator = LoadOrchestrator(cfg)
        result = await orchestrator.run()
    """

    _AGENT_FACTORIES: dict[str, type[BaseAgent]] = {
        "inference": InferenceAgent,
        "pipeline": PipelineAgent,
        "finetune": FineTuneAgent,
        "notebook": NotebookAgent,
    }

    def __init__(self, config: LoadSimulatorConfig) -> None:
        self._config = config

    async def run(self, only: list[str] | None = None) -> SessionResult:
        """Run the load test session.

        Args:
            only: If provided, run only the named agents.  Otherwise runs all
                  agents listed in ``config.agents``.

        Returns:
            A :class:`SessionResult` with per-agent results and bottleneck info.
        """
        cfg = self._config
        session_id = cfg.session_id or uuid.uuid4().hex[:12]

        # Determine which agents to run
        selected: list[str] = only if only else list(cfg.agents)
        selected = [s.lower() for s in selected if s.lower() in self._AGENT_FACTORIES]

        if not selected:
            # Nothing to run — return an empty session
            return SessionResult(
                session_id=session_id,
                duration_seconds=0.0,
                summary="No agents selected.",
            )

        # Build agent instances
        agents: list[tuple[str, BaseAgent, int]] = []
        for name in selected:
            agent, duration = self._build_agent(name)
            agents.append((name, agent, duration))

        # Start background system monitor
        max_duration = max(dur for _, _, dur in agents)
        monitor = MetricsMonitor(interval_seconds=5.0)

        session_start = time.time()

        # Run monitor + all agents concurrently
        monitor_task = asyncio.create_task(monitor.start(duration_seconds=max_duration + 5))

        agent_tasks = [
            asyncio.create_task(agent.run(duration_seconds=dur))
            for _, agent, dur in agents
        ]

        agent_results: list[AgentResult] = []
        raw_results = await asyncio.gather(*agent_tasks, return_exceptions=True)
        for name, raw in zip([n for n, _, _ in agents], raw_results):
            if isinstance(raw, BaseException):
                agent_results.append(
                    AgentResult(
                        name=name,
                        status="error",
                        errors=[str(raw)],
                        start_time=session_start,
                        end_time=time.time(),
                    )
                )
            else:
                agent_results.append(raw)  # type: ignore[arg-type]

        monitor.stop()
        try:
            await asyncio.wait_for(monitor_task, timeout=2.0)
        except asyncio.TimeoutError:
            pass

        session_end = time.time()

        # Aggregate system metrics
        system_metrics = monitor.aggregate()

        # Build merged metric dict for bottleneck analysis
        merged_metrics: dict[str, Any] = dict(system_metrics)
        for ar in agent_results:
            for k, v in (ar.metrics or {}).items():
                if isinstance(v, (int, float)) and k not in merged_metrics:
                    merged_metrics[k] = v

        # Run bottleneck analysis
        bottlenecks: list[dict[str, Any]] = []
        summary_text = ""
        if cfg.bottleneck_analysis:
            analyzer = BottleneckAnalyzer()
            report = analyzer.analyze(merged_metrics)
            bottlenecks = report.findings
            summary_text = report.text_report

        return SessionResult(
            session_id=session_id,
            duration_seconds=round(session_end - session_start, 2),
            agent_results=agent_results,
            bottlenecks=bottlenecks,
            summary=summary_text,
            system_metrics=system_metrics,
        )

    def _build_agent(self, name: str) -> tuple[BaseAgent, int]:
        """Instantiate the named agent with its config section.

        Returns:
            (agent_instance, duration_seconds)
        """
        cfg = self._config
        if name == "inference":
            return InferenceAgent(cfg.inference), cfg.inference.duration_seconds
        if name == "pipeline":
            return PipelineAgent(cfg.pipeline), cfg.pipeline.duration_seconds
        if name == "finetune":
            return FineTuneAgent(cfg.finetune), cfg.finetune.duration_seconds
        if name == "notebook":
            return NotebookAgent(cfg.notebook), cfg.notebook.duration_seconds
        raise ValueError(f"Unknown agent: {name!r}")
