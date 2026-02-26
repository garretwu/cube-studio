"""LoadOrchestrator — coordinates all agents and collects a SessionResult."""
from __future__ import annotations

import asyncio
import copy
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.agents.bottleneck import BottleneckAnalyzer
from load_simulator.agents.finetune import FineTuneAgent
from load_simulator.agents.inference import InferenceAgent
from load_simulator.agents.monitor import MetricsMonitor
from load_simulator.agents.notebook import NotebookAgent
from load_simulator.agents.pipeline import PipelineAgent
from load_simulator.channels.cube_studio import CubeStudioChannel
from load_simulator.channels.inference import InferenceChannel
from load_simulator.channels.notebook import NotebookChannel
from load_simulator.channels.prometheus import PrometheusChannel
from load_simulator.metrics.collector import MetricCollector
from load_simulator.orchestrator.adaptive import AdaptiveRules, AdaptiveThresholds
from load_simulator.orchestrator.platform_monitor import PlatformMonitor
from load_simulator.orchestrator.session import SessionTracker
from load_simulator.orchestrator.session_store import SessionStore

if TYPE_CHECKING:
    from load_simulator.config.schema import LoadSimulatorConfig


@dataclass
class SessionResult:
    session_id: str
    duration_seconds: float
    mode: str = "single"
    agent_results: list[AgentResult] = field(default_factory=list)
    bottlenecks: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    system_metrics: dict[str, float] = field(default_factory=dict)
    preflight: dict[str, dict[str, Any]] = field(default_factory=dict)
    adaptive_events: list[dict[str, Any]] = field(default_factory=list)
    breaking_point: dict[str, Any] | None = None
    system_metrics_series: list[dict[str, Any]] = field(default_factory=list)
    session_tracker: dict[str, Any] = field(default_factory=dict)


class LoadOrchestrator:
    _AGENT_FACTORIES: dict[str, type[BaseAgent]] = {
        "inference": InferenceAgent,
        "pipeline": PipelineAgent,
        "finetune": FineTuneAgent,
        "notebook": NotebookAgent,
    }

    def __init__(
        self,
        config: "LoadSimulatorConfig",
        preflight_checker: Callable[[str, str], Awaitable[tuple[bool, str]]] | None = None,
        enable_monitor: bool = True,
        session_dir: str | None = None,
        resume_session_id: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self._config = config
        self._preflight_checker = preflight_checker or self._default_preflight_checker
        self._enable_monitor = enable_monitor
        self._dry_run = dry_run
        self._adaptive = AdaptiveRules(self._adaptive_thresholds_from_config(config))
        self._session_store = SessionStore(session_dir) if session_dir else None
        self._resume_session_id = resume_session_id
        self._session_tracker = SessionTracker()
        self._metric_collector = MetricCollector()

        # Build Prometheus channel if enabled
        prom_cfg = getattr(config, "prometheus_queries", None)
        if prom_cfg and getattr(prom_cfg, "enabled", False):
            global_cfg = getattr(config, "global_config", None)
            prom_url = getattr(global_cfg, "prometheus_url", "http://localhost:9090") if global_cfg else "http://localhost:9090"
            self._prometheus_channel: PrometheusChannel | None = PrometheusChannel(base_url=prom_url)
            self._prometheus_queries: dict[str, str] = {
                "pod_cpu": str(
                    getattr(
                        prom_cfg,
                        "pod_cpu",
                        'sum(rate(container_cpu_usage_seconds_total{namespace=~"$NS"}[1m]))',
                    )
                ),
                "pod_memory": str(
                    getattr(prom_cfg, "pod_memory", 'sum(container_memory_working_set_bytes{namespace=~"$NS"})')
                ),
                "gpu_utilization": str(getattr(prom_cfg, "gpu_utilization", "DCGM_FI_DEV_GPU_UTIL")),
                "gpu_memory": str(getattr(prom_cfg, "gpu_memory", "DCGM_FI_DEV_MEM_COPY_UTIL")),
                "istio_qps": str(
                    getattr(prom_cfg, "istio_qps", 'sum(rate(istio_requests_total{namespace=~"$NS"}[1m]))')
                ),
            }
        else:
            self._prometheus_channel = None
            self._prometheus_queries = {}

        # Build a CubeStudio channel for PlatformMonitor (optional, best-effort)
        self._cube_studio_channel = None
        try:
            global_cfg = getattr(config, "global_config", None)
            if global_cfg is not None:
                self._cube_studio_channel = self._build_cube_channel(
                    base_url=global_cfg.cube_studio_url,
                    timeout=5,
                    retry_count=0,
                )
        except Exception:
            pass

    async def run(self, only: list[str] | None = None) -> SessionResult:
        cfg = self._config
        session_id = self._resume_session_id or cfg.session_id or uuid.uuid4().hex[:12]
        selected: list[str] = only if only else list(cfg.agents)
        selected = [s.lower() for s in selected if s.lower() in self._AGENT_FACTORIES]
        if not selected:
            return SessionResult(session_id=session_id, duration_seconds=0.0, summary="No agents selected.")

        mode = self._effective_mode(selected)
        plan = self._mode_plan(mode)
        preflight = await self._run_preflight(selected)

        # Wire SessionTracker
        self._session_tracker.session_id = session_id
        self._session_tracker.start()

        session_start = time.time()
        all_results: list[AgentResult] = []
        all_snapshots: list[dict[str, Any]] = []
        last_system_metrics: dict[str, float] = {}
        adaptive_events: list[dict[str, Any]] = []
        breaking_point: dict[str, Any] | None = None
        baseline_p99: float | None = None
        start_stage_index = 0
        prev_safe_scale = 0.5
        runtime_concurrency_multiplier = 1.0

        if self._session_store is not None:
            if self._resume_session_id:
                loaded = self._session_store.load(session_id)
                all_results = [self._agent_from_dict(x) for x in loaded.get("agent_results", [])]
                adaptive_events = list(loaded.get("adaptive_events", []))
                breaking_point = loaded.get("breaking_point")
                last_system_metrics = dict(loaded.get("system_metrics", {}))
                start_stage_index = int(loaded.get("current_stage_index", 0) or 0)
                baseline_p99 = self._initial_baseline_from_results(all_results)
                self._session_store.append_event(
                    session_id,
                    {"type": "session_resumed", "from_stage_index": start_stage_index},
                )
            else:
                self._session_store.start(
                    session_id=session_id,
                    mode=mode,
                    selected_agents=selected,
                    preflight=preflight,
                    plan=plan,
                    resumed=False,
                )

        for stage_index, stage in enumerate(plan):
            if stage_index < start_stage_index:
                continue
            effective_concurrency_scale = stage["concurrency_scale"] * runtime_concurrency_multiplier
            stage_results, system_metrics, stage_snapshots = await self._run_stage(
                selected,
                duration_scale=stage["duration_scale"],
                concurrency_scale=effective_concurrency_scale,
            )
            all_results.extend(stage_results)
            all_snapshots.extend(stage_snapshots)
            last_system_metrics = system_metrics

            merged_metrics = self._merge_numeric_metrics(stage_results, system_metrics)
            if baseline_p99 is None:
                baseline_p99 = float(merged_metrics.get("latency_p99_ms", 0.0) or 0.0)

            decision = self._adaptive.evaluate(merged_metrics, baseline_p99_ms=baseline_p99)
            event = {
                "stage": stage["name"],
                "stage_index": stage_index,
                "mode": mode,
                "duration_scale": stage["duration_scale"],
                "concurrency_scale": effective_concurrency_scale,
                "action": decision.action,
                "reason": decision.reason,
                "metrics": {
                    "error_rate": merged_metrics.get("error_rate", 0.0),
                    "latency_p99_ms": merged_metrics.get("latency_p99_ms", merged_metrics.get("p99_latency_ms", 0.0)),
                },
            }
            adaptive_events.append(event)

            if decision.record_breaking_point:
                breaking_point = {
                    "stage": stage["name"],
                    "mode": mode,
                    "concurrency_scale": effective_concurrency_scale,
                    "estimated_concurrency": self._estimated_concurrency(selected, effective_concurrency_scale),
                    "reason": decision.reason,
                    "metrics": merged_metrics,
                }
                if mode == "stress":
                    refine_bp, refine_events = await self._refine_breaking_point(
                        selected=selected,
                        low_scale=prev_safe_scale,
                        high_scale=effective_concurrency_scale,
                        baseline_p99=baseline_p99 or 0.0,
                    )
                    adaptive_events.extend(refine_events)
                    if refine_bp is not None:
                        breaking_point = refine_bp

                if self._session_store is not None:
                    self._session_store.update_stage(
                        session_id,
                        stage_name=stage["name"],
                        stage_index=stage_index,
                        agent_results=[self._agent_to_dict(x) for x in stage_results],
                        adaptive_event=event,
                        system_metrics=system_metrics,
                        breaking_point=breaking_point,
                    )
                break

            if self._session_store is not None:
                self._session_store.update_stage(
                    session_id,
                    stage_name=stage["name"],
                    stage_index=stage_index,
                    agent_results=[self._agent_to_dict(x) for x in stage_results],
                    adaptive_event=event,
                    system_metrics=system_metrics,
                    breaking_point=breaking_point,
                )

            prev_safe_scale = effective_concurrency_scale
            if decision.action == "REDUCE_INFERENCE_CONCURRENCY":
                runtime_concurrency_multiplier *= 0.8
            elif decision.action == "PAUSE_RAMP_UP":
                await asyncio.sleep(0.2)

        session_end = time.time()
        merged_all = self._merge_numeric_metrics(all_results, last_system_metrics)

        bottlenecks: list[dict[str, Any]] = []
        summary_text = ""
        if cfg.bottleneck_analysis:
            analyzer = BottleneckAnalyzer()
            report = analyzer.analyze(merged_all)
            bottlenecks = report.findings
            summary_text = report.text_report

        if self._session_store is not None:
            self._session_store.complete(
                session_id,
                status="completed",
                summary=summary_text,
                bottlenecks=bottlenecks,
            )

        self._session_tracker.complete()

        # Cleanup persistent channels
        if self._cube_studio_channel is not None and hasattr(self._cube_studio_channel, "close"):
            try:
                await self._cube_studio_channel.close()
            except Exception:  # noqa: BLE001
                pass

        return SessionResult(
            session_id=session_id,
            duration_seconds=round(session_end - session_start, 2),
            mode=mode,
            agent_results=all_results,
            bottlenecks=bottlenecks,
            summary=summary_text,
            system_metrics=last_system_metrics,
            preflight=preflight,
            adaptive_events=adaptive_events,
            breaking_point=breaking_point,
            system_metrics_series=all_snapshots,
            session_tracker=self._session_tracker.to_dict(),
        )

    def _effective_mode(self, selected: list[str]) -> str:
        cfg_mode = str(getattr(self._config, "mode", "single"))
        if cfg_mode in {"stress", "soak"}:
            return cfg_mode
        if len(selected) > 1:
            return "mixed"
        return "single"

    def _mode_plan(self, mode: str) -> list[dict[str, Any]]:
        if mode == "stress":
            return [
                {"name": "baseline", "duration_scale": 0.5, "concurrency_scale": 0.5},
                {"name": "ramp-1", "duration_scale": 0.8, "concurrency_scale": 0.8},
                {"name": "ramp-2", "duration_scale": 1.0, "concurrency_scale": 1.0},
                {"name": "stress-1", "duration_scale": 1.2, "concurrency_scale": 1.25},
                {"name": "stress-2", "duration_scale": 1.5, "concurrency_scale": 1.5},
                {"name": "stress-3", "duration_scale": 1.5, "concurrency_scale": 2.0},
            ]
        if mode == "soak":
            return [
                {"name": "soak-warmup", "duration_scale": 1.0, "concurrency_scale": 0.6},
                {"name": "soak-steady-1", "duration_scale": 2.0, "concurrency_scale": 0.6},
                {"name": "soak-steady-2", "duration_scale": 2.0, "concurrency_scale": 0.6},
            ]
        if mode == "mixed":
            return [
                {"name": "mixed-warmup", "duration_scale": 0.7, "concurrency_scale": 0.8},
                {"name": "mixed-main", "duration_scale": 1.0, "concurrency_scale": 1.0},
            ]
        return [{"name": "single", "duration_scale": 1.0, "concurrency_scale": 1.0}]

    async def _run_stage(
        self,
        selected: list[str],
        *,
        duration_scale: float,
        concurrency_scale: float,
    ) -> tuple[list[AgentResult], dict[str, float], list[dict[str, Any]]]:
        agents: list[tuple[str, BaseAgent, int]] = []
        channels_to_close: list[Any] = []
        for name in selected:
            agent, duration = self._build_agent(name, duration_scale=duration_scale, concurrency_scale=concurrency_scale)
            agents.append((name, agent, duration))
            # Track channels that need closing
            channel = getattr(agent, "_channel", None)
            if channel is not None and hasattr(channel, "close"):
                channels_to_close.append(channel)

        monitor_task = None
        monitor = None
        platform_monitor_task = None
        platform_monitor = None
        if self._enable_monitor:
            max_duration = max(dur for _, _, dur in agents)
            monitor = MetricsMonitor(
                interval_seconds=5.0,
                prometheus_channel=self._prometheus_channel,
                prometheus_queries=self._prometheus_queries if self._prometheus_channel else None,
            )
            monitor_task = asyncio.create_task(monitor.start(duration_seconds=max_duration + 5))
            if self._cube_studio_channel is not None:
                platform_monitor = PlatformMonitor(
                    channel=self._cube_studio_channel, interval_seconds=30.0
                )
                platform_monitor_task = asyncio.create_task(
                    platform_monitor.start(duration_seconds=max_duration + 5)
                )

        # Track agent lifecycle
        for agent_name, _, _ in agents:
            self._session_tracker.agent_started(agent_name)

        stage_start = time.time()
        agent_tasks = [asyncio.create_task(agent.run(duration_seconds=dur)) for _, agent, dur in agents]
        raw_results = await asyncio.gather(*agent_tasks, return_exceptions=True)
        agent_results: list[AgentResult] = []
        for name, raw in zip([n for n, _, _ in agents], raw_results):
            if isinstance(raw, BaseException):
                agent_results.append(
                    AgentResult(
                        name=name,
                        status="error",
                        errors=[str(raw)],
                        start_time=stage_start,
                        end_time=time.time(),
                    )
                )
                self._session_tracker.agent_finished(name, "error")
            else:
                agent_results.append(raw)  # type: ignore[arg-type]
                self._session_tracker.agent_finished(name, getattr(raw, "status", "success"))

        # Record numeric metrics into MetricCollector
        for ar in agent_results:
            for k, v in (ar.metrics or {}).items():
                if isinstance(v, (int, float)):
                    self._metric_collector.record(k, float(v), labels={"agent": ar.name})

        snapshots_dicts: list[dict[str, Any]] = []
        if monitor is not None and monitor_task is not None:
            monitor.stop()
            try:
                await asyncio.wait_for(monitor_task, timeout=2.0)
            except asyncio.TimeoutError:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
            system_metrics = monitor.aggregate()
            for snap in monitor.snapshots:
                snap_dict: dict[str, Any] = {
                    "timestamp": snap.timestamp,
                    "cpu_util_pct": snap.cpu_util_pct,
                    "mem_util_pct": snap.mem_util_pct,
                }
                if snap.gpu_util_pct is not None:
                    snap_dict["gpu_util_pct"] = snap.gpu_util_pct
                if snap.gpu_mem_util_pct is not None:
                    snap_dict["gpu_mem_util_pct"] = snap.gpu_mem_util_pct
                if snap.prom_pod_cpu is not None:
                    snap_dict["prom_pod_cpu"] = snap.prom_pod_cpu
                if snap.prom_pod_memory is not None:
                    snap_dict["prom_pod_memory"] = snap.prom_pod_memory
                if snap.prom_gpu_util is not None:
                    snap_dict["prom_gpu_util"] = snap.prom_gpu_util
                if snap.prom_gpu_memory is not None:
                    snap_dict["prom_gpu_memory"] = snap.prom_gpu_memory
                if snap.prom_istio_qps is not None:
                    snap_dict["prom_istio_qps"] = snap.prom_istio_qps
                snapshots_dicts.append(snap_dict)
        else:
            system_metrics = {}

        # Stop and merge PlatformMonitor metrics
        if platform_monitor is not None and platform_monitor_task is not None:
            platform_monitor.stop()
            try:
                await asyncio.wait_for(platform_monitor_task, timeout=2.0)
            except asyncio.TimeoutError:
                platform_monitor_task.cancel()
                try:
                    await platform_monitor_task
                except asyncio.CancelledError:
                    pass
            system_metrics.update(platform_monitor.aggregate())

        # Close channels created for this stage
        for ch in channels_to_close:
            try:
                await ch.close()
            except Exception:  # noqa: BLE001
                pass

        return agent_results, system_metrics, snapshots_dicts

    def _build_agent(
        self,
        name: str,
        *,
        duration_scale: float = 1.0,
        concurrency_scale: float = 1.0,
    ) -> tuple[BaseAgent, int]:
        cfg = self._config

        def _scaled(section: Any) -> Any:
            cpy = copy.deepcopy(section)
            if hasattr(cpy, "concurrency"):
                base = int(getattr(cpy, "concurrency") or 1)
                setattr(cpy, "concurrency", max(1, int(round(base * concurrency_scale))))
            return cpy

        factory = self._AGENT_FACTORIES.get(name)
        if factory is None:
            raise ValueError(f"Unknown agent: {name!r}")

        if name == "inference":
            sec = _scaled(cfg.inference)
            channel = InferenceChannel(
                endpoint=sec.endpoint,
                model=sec.model,
                timeout=int(self._channel_runtime("inference").timeout),
            )
            duration = max(1, int(round(sec.duration_seconds * duration_scale)))
            return self._create_agent(factory, sec, channel), duration
        if name == "pipeline":
            sec = _scaled(cfg.pipeline)
            channel = self._build_cube_channel(base_url=sec.cube_studio_url)
            duration = max(1, int(round(sec.duration_seconds * duration_scale)))
            return self._create_agent(factory, sec, channel), duration
        if name == "finetune":
            sec = _scaled(cfg.finetune)
            channel = self._build_cube_channel(base_url=sec.llama_factory_url)
            duration = max(1, int(round(sec.duration_seconds * duration_scale)))
            return self._create_agent(factory, sec, channel), duration
        if name == "notebook":
            sec = _scaled(cfg.notebook)
            runtime = self._channel_runtime("notebook")
            channel = NotebookChannel(
                base_url=sec.jupyter_url,
                token=sec.token,
                timeout=int(getattr(runtime, "timeout", 30)),
            )
            duration = max(1, int(round(sec.duration_seconds * duration_scale)))
            return self._create_agent(factory, sec, channel), duration
        raise ValueError(f"Unsupported agent: {name!r}")

    def _create_agent(self, factory: type[BaseAgent], section: Any, channel: Any) -> BaseAgent:
        try:
            return factory(section, channel=channel)
        except TypeError:
            # Some test stubs may not accept `channel=...`.
            return factory(section)

    def _channel_runtime(self, name: str) -> Any:
        channels = getattr(self._config, "channels", None)
        if channels is None:
            return type("ChannelRuntime", (), {"timeout": 30, "retry_count": 3, "retry_backoff": 1.0})()
        section = getattr(channels, name, None)
        if section is None:
            return type("ChannelRuntime", (), {"timeout": 30, "retry_count": 3, "retry_backoff": 1.0})()
        return section

    def _build_cube_channel(
        self,
        *,
        base_url: str,
        timeout: int | None = None,
        retry_count: int | None = None,
        retry_backoff: float | None = None,
    ) -> CubeStudioChannel:
        global_cfg = getattr(self._config, "global_config", None)
        runtime = self._channel_runtime("cube_studio")
        auth_method = str(getattr(global_cfg, "auth_method", "username"))
        auth_username = str(getattr(global_cfg, "auth_username", "admin"))
        jwt_secret = getattr(global_cfg, "jwt_password", None)
        return CubeStudioChannel(
            base_url=base_url,
            auth_method=auth_method,
            username=auth_username,
            jwt_secret=jwt_secret or None,
            dry_run=self._dry_run,
            timeout=int(timeout if timeout is not None else getattr(runtime, "timeout", 30)),
            retry_count=int(retry_count if retry_count is not None else getattr(runtime, "retry_count", 3)),
            retry_backoff=float(
                retry_backoff if retry_backoff is not None else getattr(runtime, "retry_backoff", 1.0)
            ),
        )

    async def _refine_breaking_point(
        self,
        *,
        selected: list[str],
        low_scale: float,
        high_scale: float,
        baseline_p99: float,
        iterations: int = 2,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        events: list[dict[str, Any]] = []
        best_bp: dict[str, Any] | None = None
        lo = max(0.1, low_scale)
        hi = max(lo, high_scale)

        for i in range(iterations):
            mid = (lo + hi) / 2.0
            stage_results, system_metrics, _ = await self._run_stage(selected, duration_scale=1.0, concurrency_scale=mid)
            merged = self._merge_numeric_metrics(stage_results, system_metrics)
            decision = self._adaptive.evaluate(merged, baseline_p99_ms=baseline_p99)
            event = {
                "stage": f"refine-{i + 1}",
                "mode": "stress",
                "concurrency_scale": mid,
                "action": decision.action,
                "reason": decision.reason,
                "metrics": {
                    "error_rate": merged.get("error_rate", 0.0),
                    "latency_p99_ms": merged.get("latency_p99_ms", merged.get("p99_latency_ms", 0.0)),
                },
            }
            events.append(event)
            if decision.record_breaking_point:
                hi = mid
                best_bp = {
                    "stage": event["stage"],
                    "mode": "stress",
                    "concurrency_scale": mid,
                    "estimated_concurrency": self._estimated_concurrency(selected, mid),
                    "reason": decision.reason,
                    "metrics": merged,
                }
            else:
                lo = mid
        return best_bp, events

    def _estimated_concurrency(self, selected: list[str], scale: float) -> dict[str, int]:
        cfg = self._config
        out: dict[str, int] = {}
        for name in selected:
            if name == "inference":
                out[name] = max(1, int(round(float(cfg.inference.concurrency) * scale)))
            elif name == "pipeline":
                out[name] = max(1, int(round(float(cfg.pipeline.concurrency) * scale)))
            else:
                out[name] = 1
        return out

    def _adaptive_thresholds_from_config(self, config: Any) -> AdaptiveThresholds:
        rules = getattr(config, "adaptive_rules", None)
        if rules is None:
            return AdaptiveThresholds()
        return AdaptiveThresholds(
            pause_error_rate=float(getattr(rules, "pause_error_rate", 0.05)),
            breaking_error_rate=float(getattr(rules, "breaking_error_rate", 0.10)),
            p99_breaking_multiplier=float(getattr(rules, "p99_breaking_multiplier", 10.0)),
            gpu_mem_reduce_pct=float(getattr(rules, "gpu_mem_reduce_pct", 95.0)),
            cpu_pause_pct=float(getattr(rules, "cpu_pause_pct", 95.0)),
        )

    def _agent_to_dict(self, result: AgentResult) -> dict[str, Any]:
        return {
            "name": result.name,
            "status": result.status,
            "metrics": result.metrics,
            "errors": result.errors,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "raw": result.raw,
        }

    def _agent_from_dict(self, data: dict[str, Any]) -> AgentResult:
        return AgentResult(
            name=str(data.get("name", "")),
            status=str(data.get("status", "error")),
            metrics=dict(data.get("metrics", {}) or {}),
            errors=list(data.get("errors", []) or []),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            raw=dict(data.get("raw", {}) or {}),
        )

    def _initial_baseline_from_results(self, results: list[AgentResult]) -> float | None:
        for r in results:
            v = r.metrics.get("latency_p99_ms") or r.metrics.get("p99_latency_ms")
            if isinstance(v, (int, float)):
                return float(v)
        return None

    def _merge_numeric_metrics(self, agent_results: list[AgentResult], system_metrics: dict[str, float]) -> dict[str, Any]:
        merged: dict[str, Any] = dict(system_metrics)
        for ar in agent_results:
            for k, v in (ar.metrics or {}).items():
                if isinstance(v, (int, float)) and k not in merged:
                    merged[k] = v
        return merged

    async def _run_preflight(self, selected: list[str]) -> dict[str, dict[str, Any]]:
        cfg = self._config
        checks: dict[str, str] = {}
        if "pipeline" in selected:
            checks["cube_studio_pipeline"] = f"{cfg.pipeline.cube_studio_url.rstrip('/')}/pipeline_modelview/api/"
        if "inference" in selected:
            checks["inference_endpoint"] = cfg.inference.endpoint
            checks["cube_studio_inference"] = f"{cfg.pipeline.cube_studio_url.rstrip('/')}/inferenceservice_modelview/api/"
        if "notebook" in selected:
            checks["notebook_api"] = f"{cfg.notebook.jupyter_url.rstrip('/')}/api/kernels"
            checks["cube_studio_notebook"] = f"{cfg.pipeline.cube_studio_url.rstrip('/')}/notebook_modelview/api/list/"
        if "finetune" in selected:
            checks["finetune_endpoint"] = f"{cfg.finetune.llama_factory_url.rstrip('/')}/api/v1/train"
        prom_cfg = getattr(cfg, "prometheus_queries", None)
        global_cfg = getattr(cfg, "global_config", None)
        if prom_cfg and getattr(prom_cfg, "enabled", False) and global_cfg:
            checks["prometheus"] = f"{global_cfg.prometheus_url.rstrip('/')}/api/v1/query?query=up"
        else:
            checks["prometheus"] = "not_configured"

        results: dict[str, dict[str, Any]] = {}
        for name, url in checks.items():
            if url == "not_configured":
                results[name] = {"ok": None, "detail": "not configured in current schema"}
                continue
            ok, detail = await self._preflight_checker(name, url)
            results[name] = {"ok": ok, "detail": detail, "url": url}
        return results

    async def _default_preflight_checker(self, name: str, url: str) -> tuple[bool, str]:
        _ = name
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                status = getattr(resp, "status", 200)
                return True, f"http_status={status}"
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                return True, f"http_status={exc.code}"
            return False, f"http_status={exc.code}"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
