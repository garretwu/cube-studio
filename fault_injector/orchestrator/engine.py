"""
Fault orchestrator engine.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fault_injector.agents import (
    HardwareFaultAgent,
    MonitorAgent,
    OSFaultAgent,
    PlatformFaultAgent,
    ServiceFaultAgent,
)
from fault_injector.config.schema import FaultInjectorConfig, ScenarioConfig
from fault_injector.orchestrator.scheduler import ScenarioScheduler
from fault_injector.orchestrator.session import (
    ActiveFault,
    ScenarioResult,
    Session,
    SessionPhase,
    SessionStatus,
)
from fault_injector.orchestrator.watchdog import FaultWatchdog
from fault_injector.safety.guard import SafetyGuard
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.scenarios.base import FaultContext
from fault_injector.scenarios.registry import SCENARIO_REGISTRY
from lib.fchannels.kubernetes import K8sChannel
from lib.fchannels.ipmi import IPMIChannel
from lib.fchannels.prometheus import PrometheusChannel
from lib.fchannels.redfish import RedfishChannel
from lib.fchannels.ssh import SSHChannel
from lib.fchannels.switch import SwitchChannel

logger = logging.getLogger(__name__)
_MONITOR_PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class OrchestrationError(Exception):
    pass


class FaultOrchestrator:
    """Deterministic orchestrator with agent-centric execution."""

    def __init__(self, config: FaultInjectorConfig, dry_run: bool = False, session_dir: str | None = None):
        self.config = config
        self.dry_run = dry_run
        self.session_dir = session_dir or config.global_.session_dir

        self.session: Session | None = None
        self.rollback: RollbackJournal | None = None
        self.guard: SafetyGuard | None = None
        self.watchdog: FaultWatchdog | None = None

        self.ssh: SSHChannel | None = None
        self.prometheus: PrometheusChannel | None = None
        self.kubernetes: K8sChannel | None = None
        self.redfish: RedfishChannel | None = None
        self.ipmi: IPMIChannel | None = None
        self.switch: SwitchChannel | None = None

        self.monitor_agent: MonitorAgent | None = None
        self.layer_agents: dict[str, Any] = {}
        self.inventory: dict[str, Any] = {}
        self.scheduler = ScenarioScheduler(config)

    async def run(self) -> Session:
        self.session = Session.create(config_hash=self.config.config_hash, session_dir=self.session_dir)
        self.session.save(self.session_dir)
        try:
            await self._init_components()
            await self.watchdog.start()
            await self._preflight_check()
            baseline = await self._collect_baseline()
            self.session.baseline_metrics = baseline

            if self.scheduler.has_combined_plan():
                await self._run_combined_plan(baseline)
            else:
                await self._run_sequential_plan(baseline)

            await self._generate_report()
            self.session.complete()
            self.session.save(self.session_dir)
            return self.session
        except Exception as exc:
            if self.session:
                self.session.fail(str(exc))
                self.session.save(self.session_dir)
            if self.rollback:
                await self.rollback.recover_all()
            raise
        finally:
            if self.watchdog:
                self.watchdog.cancel()
            await self._close_channels()

    async def _init_components(self) -> None:
        assert self.session is not None
        session_path = Path(self.session_dir) / self.session.session_id
        session_path.mkdir(parents=True, exist_ok=True)

        self.rollback = RollbackJournal(session_path / "rollback.jsonl")
        self.guard = SafetyGuard(self.config.global_.safety)
        self.watchdog = FaultWatchdog(
            timeout_seconds=self.config.global_.safety.auto_recover_timeout,
            rollback_journal=self.rollback,
            session_id=self.session.session_id,
        )

        inventory = self._build_inventory()
        self.inventory = inventory
        self.ssh = SSHChannel(
            inventory=inventory,
            dry_run=self.dry_run or self.config.global_.safety.dry_run,
            wal=self.rollback,
            guard=self.guard,
        )
        if self.config.monitor.enabled:
            prom_dry_run = self.dry_run or self.config.global_.safety.dry_run
            self.prometheus = PrometheusChannel(
                base_url=self.config.monitor.prometheus_url,
                dry_run=prom_dry_run,
            )
        else:
            self.prometheus = None
        self.kubernetes = K8sChannel(dry_run=self.dry_run, wal=self.rollback)
        self.redfish = RedfishChannel(dry_run=self.dry_run, wal=self.rollback, guard=self.guard)
        self.ipmi = IPMIChannel(dry_run=self.dry_run, wal=self.rollback, guard=self.guard)

        switch_devices = self._build_switch_inventory()
        self.switch = SwitchChannel(
            devices=switch_devices,
            dry_run=self.dry_run or self.config.global_.safety.dry_run,
            wal=self.rollback,
            guard=self.guard,
        )

        channels = {
            "ssh": self.ssh,
            "prometheus": self.prometheus,
            "kubernetes": self.kubernetes,
            "redfish": self.redfish,
            "ipmi": self.ipmi,
            "switch": self.switch,
        }
        self.monitor_agent = MonitorAgent(channels=channels)
        self.layer_agents = {
            "hardware": HardwareFaultAgent(channels=channels),
            "os": OSFaultAgent(channels=channels),
            "platform": PlatformFaultAgent(channels=channels),
            "service": ServiceFaultAgent(channels=channels),
        }
        self.session.add_event("components_initialized")
        self.session.save(self.session_dir)

    def _build_inventory(self) -> dict[str, Any]:
        inventory: dict[str, Any] = {}
        for _, nodes in self.config.inventory.items():
            for node in nodes:
                inventory[node.name] = node
        return inventory

    def _build_switch_inventory(self) -> dict[str, dict[str, Any]]:
        inventory: dict[str, dict[str, Any]] = {}
        for name, switch in self.config.switches.items():
            data: dict[str, Any]
            if hasattr(switch, "model_dump"):
                data = switch.model_dump(exclude_none=True)  # pydantic v2
            else:
                data = switch.dict(exclude_none=True)  # pydantic v1
            inventory[name] = data
        return inventory

    async def _preflight_check(self) -> None:
        assert self.session is not None
        self.session.set_phase(SessionPhase.INIT)
        self.session.add_event("preflight_started")
        self.session.save(self.session_dir)
        if self.prometheus:
            if self.prometheus.dry_run:
                self.session.add_event("preflight_prometheus_skipped", {"dry_run": True})
                self.session.save(self.session_dir)
                self.session.add_event("preflight_completed")
                self.session.save(self.session_dir)
                return
            try:
                await self.prometheus.query_instant("up")
            except Exception as exc:
                logger.warning("Prometheus preflight warning: %s", exc)
        self.session.add_event("preflight_completed")
        self.session.save(self.session_dir)

    async def _collect_baseline(self) -> dict[str, list[float]]:
        assert self.session is not None
        self.session.set_phase(SessionPhase.BASELINE)
        self.session.add_event("baseline_started")
        self.session.save(self.session_dir)

        queries = self._aggregate_monitor_queries()
        duration = self.config.monitor.baseline_duration
        interval = self.config.orchestrator.observe_interval
        baseline = await self.monitor_agent.collect_baseline(
            queries=queries,
            duration=duration,
            interval=interval,
        )

        baseline_path = Path(self.session.baseline_path)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(baseline, f, indent=2, ensure_ascii=False)

        self._evaluate_baseline_quality(baseline)
        self.session.add_event("baseline_completed", {"baseline_path": str(baseline_path)})
        self.session.save(self.session_dir)
        return baseline

    def _aggregate_monitor_queries(self) -> dict[str, str]:
        configured = self.config.monitor.baseline_queries
        if configured:
            return dict(configured)

        queries: dict[str, str] = self._default_monitor_queries()
        for sc_cfg in self._resolve_scenarios():
            scenario_cls = SCENARIO_REGISTRY.get(sc_cfg.name)
            if not scenario_cls:
                continue
            scenario = scenario_cls()
            rendered_queries = self._render_monitor_queries(scenario.monitor_queries(), sc_cfg, sc_cfg.name)
            for key, value in rendered_queries.items():
                queries[f"{sc_cfg.name}:{key}"] = value
        return queries

    def _default_monitor_queries(self) -> dict[str, str]:
        return {
            "cpu_util": "avg(1 - rate(node_cpu_seconds_total{mode='idle'}[1m]))",
            "memory_util": "avg(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))",
            "gpu_util": "avg(DCGM_FI_DEV_GPU_UTIL)",
            "gpu_memory_used_mb": "avg(DCGM_FI_DEV_FB_USED)",
            "gpu_power_watts": "avg(DCGM_FI_DEV_POWER_USAGE)",
        }

    def _evaluate_baseline_quality(self, baseline: dict[str, list[float]]) -> None:
        if self.session is None:
            return

        quality: dict[str, dict[str, Any]] = {}
        warnings: list[dict[str, Any]] = []
        min_samples = max(1, int(self.config.monitor.baseline_min_samples))
        max_error_ratio = float(self.config.monitor.baseline_max_error_ratio)
        required_non_zero = set(self.config.monitor.baseline_require_non_zero)
        stats_map: dict[str, dict[str, Any]] = {}
        if self.prometheus is not None:
            stats_map = getattr(self.prometheus, "last_baseline_stats", {}) or {}

        for metric, values in baseline.items():
            sample_count = len(values)
            has_non_zero = any(abs(v) > 1e-12 for v in values)
            zero_ratio = (
                float(sum(1 for v in values if abs(v) <= 1e-12)) / float(sample_count)
                if sample_count
                else 0.0
            )
            stats = stats_map.get(metric, {})
            error_ratio = float(stats.get("error_ratio", 0.0))
            issues: list[str] = []
            if sample_count < min_samples:
                issues.append(f"sample_count<{min_samples}")
            if error_ratio > max_error_ratio:
                issues.append(f"error_ratio>{max_error_ratio}")
            if metric in required_non_zero and not has_non_zero:
                issues.append("all_zero_but_required_non_zero")

            quality[metric] = {
                "sample_count": sample_count,
                "zero_ratio": zero_ratio,
                "error_ratio": error_ratio,
                "has_non_zero": has_non_zero,
                "status": "ok" if not issues else "warn",
                "issues": issues,
            }
            if issues:
                warning = {"metric": metric, "issues": issues}
                warnings.append(warning)
                self.session.add_event("baseline_quality_warning", warning)

        self.session.add_event(
            "baseline_quality_evaluated",
            {"metrics": len(quality), "warnings": len(warnings), "quality": quality},
        )
        if warnings and self.config.monitor.baseline_strict:
            raise OrchestrationError(
                "Baseline quality check failed in strict mode: "
                + ", ".join(sorted({w["metric"] for w in warnings}))
            )

    def _monitor_render_values(self, config: ScenarioConfig, target_node: str | None = None) -> dict[str, str]:
        if target_node is None:
            target_nodes = config.target_nodes
            node = target_nodes[0] if target_nodes else ""
        else:
            node = target_node
        interface = str(config.params.get("interface", "eth0"))
        device = str(config.params.get("device", interface))
        return {
            "node": node,
            "device": device,
            "interface": interface,
        }

    def _record_query_render_warning(
        self,
        *,
        scenario_name: str,
        metric_key: str,
        query: str,
        unresolved: list[str],
    ) -> None:
        if self.session is None:
            return
        self.session.add_event(
            "monitor_query_render_warning",
            {
                "scenario": scenario_name,
                "metric": metric_key,
                "query": query,
                "unresolved_placeholders": unresolved,
            },
        )
        self.session.save(self.session_dir)

    def _render_monitor_queries(
        self,
        queries: dict[str, str],
        config: ScenarioConfig,
        scenario_name: str,
        target_node: str | None = None,
    ) -> dict[str, str]:
        values = self._monitor_render_values(config, target_node=target_node)
        rendered: dict[str, str] = {}

        for key, query in queries.items():
            placeholders = sorted(set(_MONITOR_PLACEHOLDER_PATTERN.findall(query)))
            if not placeholders:
                rendered[key] = query
                continue

            unresolved = [placeholder for placeholder in placeholders if placeholder not in values]
            if unresolved:
                self._record_query_render_warning(
                    scenario_name=scenario_name,
                    metric_key=key,
                    query=query,
                    unresolved=unresolved,
                )
                rendered[key] = query
                continue

            query_rendered = query
            for placeholder in placeholders:
                query_rendered = query_rendered.replace(f"{{{placeholder}}}", values[placeholder])
            query_rendered = query_rendered.replace("{{", "{").replace("}}", "}")
            rendered[key] = query_rendered

        return rendered

    def _resolve_scenarios(self) -> list[ScenarioConfig]:
        scenarios: list[ScenarioConfig] = []
        for _, sc in self.config.scenarios.items():
            if sc.enabled:
                scenarios.append(sc)
        return scenarios

    def _build_context(self, scenario_name: str, config: ScenarioConfig, target_node: str | None = None) -> FaultContext:
        if target_node is None:
            target_nodes = config.target_nodes
            target = target_nodes[0] if target_nodes else ""
        else:
            target = target_node
        fault_id = f"{scenario_name}_{uuid.uuid4().hex[:8]}"
        target_cfg = self.inventory.get(target)
        return FaultContext(
            ssh=self.ssh,
            rollback=self.rollback,
            guard=self.guard,
            target_node=target,
            params=copy.deepcopy(config.params),
            fault_id=fault_id,
            redfish=self.redfish,
            ipmi=self.ipmi,
            target_redfish=getattr(target_cfg, "redfish", None),
            target_ipmi=getattr(target_cfg, "ipmi", None),
            switch=self.switch,
            k8s=self.kubernetes,
            prometheus=self.prometheus,
            session_id=self.session.session_id if self.session else "",
            interface=config.params.get("interface", "eth0"),
        )

    def _scenario_result_key(self, scenario_name: str, target_node: str, total_targets: int) -> str:
        if total_targets <= 1:
            return scenario_name
        return f"{scenario_name}@{target_node}"

    def _get_layer_agent(self, scenario_name: str) -> Any:
        scenario_class = SCENARIO_REGISTRY.get(scenario_name)
        if scenario_class is None:
            raise OrchestrationError(f"Scenario not found: {scenario_name}")
        layer = scenario_class().layer
        agent = self.layer_agents.get(layer)
        if agent is None:
            raise OrchestrationError(f"No agent for layer: {layer} (scenario={scenario_name})")
        return agent

    async def _run_sequential_plan(self, baseline: dict[str, list[float]]) -> None:
        del baseline  # baseline is persisted and used by reports/verification context.
        scenario_configs = self._resolve_scenarios()
        for config in scenario_configs:
            await self._run_single_scenario(config)

    async def _run_single_scenario(self, config: ScenarioConfig) -> None:
        assert self.session is not None
        scenario_name = config.name
        agent = self._get_layer_agent(scenario_name)
        targets = config.target_nodes if config.target_nodes else [""]
        total_targets = len(targets)

        ls_cfg = config.params.get("load_simulator")
        if isinstance(ls_cfg, dict) and ls_cfg.get("enabled"):
            ls_timeout = int(ls_cfg.get("timeout_seconds", 900) or 900)
            watchdog_timeout = int(self.config.global_.safety.auto_recover_timeout)
            if ls_timeout >= watchdog_timeout:
                logger.warning(
                    "load_simulator timeout_seconds (%s) >= watchdog auto_recover_timeout (%s) for scenario=%s; "
                    "watchdog may trigger before load_simulator finishes",
                    ls_timeout,
                    watchdog_timeout,
                    scenario_name,
                )

        for target_node in targets:
            result_key = self._scenario_result_key(scenario_name, target_node, total_targets)
            result_name = result_key if total_targets > 1 else scenario_name
            ctx = self._build_context(scenario_name, config, target_node=target_node)
            result = ScenarioResult(scenario_name=result_name)

            self.session.set_phase(SessionPhase.INJECT)
            self.session.save(self.session_dir)
            inject_result = await agent.inject(scenario_name, ctx)
            result.inject_success = inject_result.success

            bmc_precheck = ctx.params.get("bmc_precheck")
            if isinstance(bmc_precheck, dict):
                self.session.add_event(
                    "bmc_precheck_completed",
                    {
                        "scenario": scenario_name,
                        "target_node": ctx.target_node,
                        "fault_id": ctx.fault_id,
                        "precheck": bmc_precheck,
                    },
                )
                self.session.save(self.session_dir)

            ls_runs = ctx.params.get("_load_simulator_runs")
            ls_runs_emitted = 0
            if isinstance(ls_runs, list):
                for idx, run_detail in enumerate(ls_runs):
                    if not isinstance(run_detail, dict):
                        continue
                    ls_runs_emitted = idx + 1
                    self.session.add_event(
                        "load_simulator_run",
                        {
                            "scenario": scenario_name,
                            "target_node": ctx.target_node,
                            "fault_id": ctx.fault_id,
                            "index": idx,
                            **run_detail,
                        },
                    )
                self.session.save(self.session_dir)

            ls_warnings = ctx.params.get("_load_simulator_warnings")
            ls_warnings_emitted = 0
            if isinstance(ls_warnings, list):
                for warning in ls_warnings:
                    ls_warnings_emitted += 1
                    self.session.add_event(
                        "load_simulator_warning",
                        {
                            "scenario": scenario_name,
                            "target_node": ctx.target_node,
                            "fault_id": ctx.fault_id,
                            "warning": str(warning),
                        },
                    )
                self.session.save(self.session_dir)

            if not inject_result.success:
                result.error = inject_result.error
                self.session.scenario_results[result_key] = result
                self.session.save(self.session_dir)
                continue

            self.session.add_active_fault(
                ActiveFault(
                    fault_id=ctx.fault_id,
                    scenario_name=scenario_name,
                    target_node=ctx.target_node,
                    injected_at=datetime.now(),
                    params=config.params,
                )
            )
            self.session.save(self.session_dir)

            self.session.set_phase(SessionPhase.OBSERVE)
            self.session.save(self.session_dir)
            observe_duration = int(config.params.get("duration", 60))
            post_inject_hook = getattr(agent, "post_inject", None)
            post_inject_task: asyncio.Task | None = None
            if callable(post_inject_hook):
                post_inject_task = asyncio.create_task(post_inject_hook(scenario_name, ctx))

            if not self.dry_run:
                sleep_task = asyncio.create_task(asyncio.sleep(observe_duration))
                if post_inject_task is not None:
                    await asyncio.gather(sleep_task, post_inject_task)
                else:
                    await sleep_task
            elif post_inject_task is not None:
                await post_inject_task

            if post_inject_task is not None:
                post_inject_result = post_inject_task.result()
                if not post_inject_result.success:
                    self.session.add_event(
                        "load_simulator_warning",
                        {
                            "scenario": scenario_name,
                            "target_node": ctx.target_node,
                            "fault_id": ctx.fault_id,
                            "warning": post_inject_result.error or "post_inject hook failed",
                        },
                    )
                    if not result.error:
                        result.error = post_inject_result.error or "post_inject hook failed"
                    self.session.save(self.session_dir)

            ls_runs_after = ctx.params.get("_load_simulator_runs")
            if isinstance(ls_runs_after, list):
                for idx, run_detail in enumerate(ls_runs_after[ls_runs_emitted:], start=ls_runs_emitted):
                    if not isinstance(run_detail, dict):
                        continue
                    self.session.add_event(
                        "load_simulator_run",
                        {
                            "scenario": scenario_name,
                            "target_node": ctx.target_node,
                            "fault_id": ctx.fault_id,
                            "index": idx,
                            **run_detail,
                        },
                    )
                self.session.save(self.session_dir)

            ls_warnings_after = ctx.params.get("_load_simulator_warnings")
            if isinstance(ls_warnings_after, list):
                for warning in ls_warnings_after[ls_warnings_emitted:]:
                    self.session.add_event(
                        "load_simulator_warning",
                        {
                            "scenario": scenario_name,
                            "target_node": ctx.target_node,
                            "fault_id": ctx.fault_id,
                            "warning": str(warning),
                        },
                    )
                self.session.save(self.session_dir)

            queries = {}
            scenario_class = SCENARIO_REGISTRY.get(scenario_name)
            if scenario_class:
                queries = self._render_monitor_queries(
                    scenario_class().monitor_queries(),
                    config,
                    scenario_name,
                    target_node=ctx.target_node,
                )
            if queries:
                result.metrics["during"] = await self.monitor_agent.observe(
                    queries=queries,
                    duration=observe_duration,
                    interval=self.config.orchestrator.observe_interval,
                )

            self.session.set_phase(SessionPhase.RECOVER)
            self.session.save(self.session_dir)
            recover_result = await agent.recover(scenario_name, ctx)
            result.recover_success = recover_result.success
            self.session.remove_active_fault(ctx.fault_id)
            self.session.save(self.session_dir)

            self.session.set_phase(SessionPhase.VERIFY)
            self.session.save(self.session_dir)
            verify_result = await agent.verify(scenario_name, ctx)
            result.verified = verify_result.success
            if not verify_result.success and not result.error:
                result.error = verify_result.error

            self.session.scenario_results[result_key] = result
            self.session.save(self.session_dir)

    async def _run_combined_plan(self, baseline: dict[str, list[float]]) -> None:
        del baseline
        assert self.session is not None
        events = self.scheduler.build_events()
        config_map = {sc.name: sc for sc in self._resolve_scenarios()}
        active_contexts: dict[str, FaultContext] = {}
        cursor = 0

        for event in events:
            wait_seconds = max(0, event.time_offset - cursor)
            cursor = event.time_offset
            if wait_seconds > 0:
                self.session.set_phase(SessionPhase.OBSERVE)
                self.session.add_event("combined_wait", {"seconds": wait_seconds})
                self.session.save(self.session_dir)
                if not self.dry_run:
                    await asyncio.sleep(wait_seconds)

            for scenario_name in event.targets:
                config = config_map.get(scenario_name, ScenarioConfig(name=scenario_name, enabled=True))
                agent = self._get_layer_agent(scenario_name)

                if event.action == "inject":
                    ctx = self._build_context(scenario_name, config)
                    self.session.set_phase(SessionPhase.INJECT)
                    self.session.save(self.session_dir)
                    inject_result = await agent.inject(scenario_name, ctx)
                    if inject_result.success:
                        active_contexts[scenario_name] = ctx
                        self.session.add_active_fault(
                            ActiveFault(
                                fault_id=ctx.fault_id,
                                scenario_name=scenario_name,
                                target_node=ctx.target_node,
                                injected_at=datetime.now(),
                                params=config.params,
                            )
                        )
                    self.session.add_event(
                        "combined_inject",
                        {"scenario": scenario_name, "success": inject_result.success, "error": inject_result.error},
                    )
                    ls_runs = ctx.params.get("_load_simulator_runs")
                    if isinstance(ls_runs, list):
                        for idx, run_detail in enumerate(ls_runs):
                            if not isinstance(run_detail, dict):
                                continue
                            self.session.add_event(
                                "load_simulator_run",
                                {
                                    "scenario": scenario_name,
                                    "fault_id": ctx.fault_id,
                                    "index": idx,
                                    **run_detail,
                                },
                            )
                    ls_warnings = ctx.params.get("_load_simulator_warnings")
                    if isinstance(ls_warnings, list):
                        for warning in ls_warnings:
                            self.session.add_event(
                                "load_simulator_warning",
                                {
                                    "scenario": scenario_name,
                                    "fault_id": ctx.fault_id,
                                    "warning": str(warning),
                                },
                            )
                    self.session.save(self.session_dir)
                else:
                    ctx = active_contexts.get(scenario_name)
                    if ctx is None:
                        continue
                    self.session.set_phase(SessionPhase.RECOVER)
                    self.session.save(self.session_dir)
                    recover_result = await agent.recover(scenario_name, ctx)
                    self.session.remove_active_fault(ctx.fault_id)
                    self.session.add_event(
                        "combined_recover",
                        {"scenario": scenario_name, "success": recover_result.success, "error": recover_result.error},
                    )
                    self.session.set_phase(SessionPhase.VERIFY)
                    verify_result = await agent.verify(scenario_name, ctx)
                    self.session.add_event(
                        "combined_verify",
                        {"scenario": scenario_name, "verified": verify_result.success, "error": verify_result.error},
                    )
                    self.session.save(self.session_dir)
                    active_contexts.pop(scenario_name, None)

    async def _generate_report(self) -> None:
        assert self.session is not None
        self.session.set_phase(SessionPhase.REPORT)
        self.session.add_event("report_started")
        self.session.save(self.session_dir)

        report_dir = Path(self.session.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        report_payload = {
            "session_id": self.session.session_id,
            "status": self.session.status.value,
            "phase": self.session.phase.value,
            "started_at": self.session.started_at.isoformat(),
            "finished_at": datetime.now().isoformat(),
            "baseline_metrics": self.session.baseline_metrics,
            "scenario_results": {k: v.to_dict() for k, v in self.session.scenario_results.items()},
            "events": self.session.events,
        }
        with open(report_dir / "report.json", "w", encoding="utf-8") as f:
            json.dump(report_payload, f, indent=2, ensure_ascii=False)
        self.session.add_event("report_completed", {"report_path": str(report_dir / "report.json")})
        self.session.save(self.session_dir)

    async def _close_channels(self) -> None:
        if self.ssh:
            await self.ssh.close()
        if self.prometheus:
            await self.prometheus.close()
        if self.switch:
            self.switch.close()
        if self.ipmi:
            await self.ipmi.close()

    @classmethod
    async def resume(cls, session_id: str, session_dir: str = "./fault_injector/fault_reports/sessions/") -> Session:
        session = Session.load(session_id=session_id, session_dir=session_dir)
        if session is None:
            raise OrchestrationError(f"Session not found: {session_id}")
        if session.status == SessionStatus.COMPLETED:
            return session

        rollback = RollbackJournal(Path(session_dir) / session_id / "rollback.jsonl")
        await rollback.recover_all()
        session.active_faults = []
        session.set_phase(SessionPhase.RECOVER)
        session.mark_recovered()
        session.save(session_dir=session_dir)
        return session
