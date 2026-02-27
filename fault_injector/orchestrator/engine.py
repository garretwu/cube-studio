"""
FaultOrchestrator - Deterministic fault injection orchestration engine.

This module manages the complete lifecycle of fault injection:
1. Configuration parsing
2. Preflight connectivity check
3. Baseline collection
4. Fault injection
5. Observation period
6. Fault recovery
7. Recovery verification
8. Report generation
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from fault_injector.config.schema import FaultInjectorConfig, ScenarioConfig
from fault_injector.orchestrator.session import (
    Session,
    SessionPhase,
    SessionStatus,
    ActiveFault,
    ScenarioResult,
)
from fault_injector.orchestrator.watchdog import FaultWatchdog
from lib.channels.ssh import SSHChannel
from lib.channels.prometheus import PrometheusChannel
from lib.channels.kubernetes import K8sChannel
from lib.channels.redfish import RedfishChannel
from lib.channels.switch import SwitchChannel
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.safety.guard import SafetyGuard
from fault_injector.scenarios.registry import SCENARIO_REGISTRY
from fault_injector.scenarios.base import FaultContext

logger = logging.getLogger(__name__)


class OrchestrationError(Exception):
    """Orchestration error."""
    pass


class FaultOrchestrator:
    """
    Deterministic fault injection orchestration engine.
    
    Execution flow:
    1. Config parsing → Session initialization
    2. Preflight check → Connectivity verification
    3. Baseline collection → Metric baseline
    4. Fault injection → Per-scenario execution
    5. Observation → Metric collection
    6. Recovery → Fault rollback
    7. Verification → Baseline comparison
    8. Report generation → HTML/JSON output
    """
    
    def __init__(
        self,
        config: FaultInjectorConfig,
        dry_run: bool = False,
        session_dir: str = "./fault-reports/sessions/",
    ):
        """
        Initialize the orchestrator.
        
        Args:
            config: Fault injector configuration
            dry_run: Dry-run mode (no actual injection)
            session_dir: Directory for session persistence
        """
        self.config = config
        self.dry_run = dry_run
        self.session_dir = session_dir
        
        # Session state
        self.session: Optional[Session] = None
        
        # Channels
        self.ssh: Optional[SSHChannel] = None
        self.prometheus: Optional[PrometheusChannel] = None
        self.kubernetes: Optional[K8sChannel] = None
        self.redfish: Optional[RedfishChannel] = None
        self.switch: Optional[SwitchChannel] = None
        
        # Safety components
        self.rollback: Optional[RollbackJournal] = None
        self.guard: Optional[SafetyGuard] = None
        self.watchdog: Optional[FaultWatchdog] = None
    
    async def run(self) -> Session:
        """
        Execute the complete fault injection flow.
        
        Returns:
            Session: Completed session with results
        """
        # Phase 1: Initialize session
        self.session = Session.create(
            config_hash="",
            session_dir=self.session_dir,
        )
        self.session.save(self.session_dir)
        
        logger.info(f"Session {self.session.session_id}: Starting fault injection")
        
        try:
            # Initialize components
            await self._init_components()
            
            # Start watchdog
            if self.watchdog:
                await self.watchdog.start()
            
            # Phase 2: Preflight check
            self.session.set_phase(SessionPhase.INIT)
            self.session.save(self.session_dir)
            await self._preflight_check()
            
            # Phase 3: Baseline collection
            self.session.set_phase(SessionPhase.BASELINE)
            self.session.save(self.session_dir)
            baseline = await self._collect_baseline()
            self.session.baseline_metrics = baseline
            
            # Phase 4-7: Execute scenarios
            scenarios = self._resolve_scenarios()
            for scenario_config in scenarios:
                await self._run_scenario(scenario_config, baseline)
            
            # Phase 8: Report generation
            self.session.set_phase(SessionPhase.REPORT)
            self.session.save(self.session_dir)
            await self._generate_report()
            
            # Complete session
            self.session.complete()
            self.session.save(self.session_dir)
            
            logger.info(f"Session {self.session.session_id}: Completed successfully")
            
        except Exception as e:
            logger.error(f"Session {self.session.session_id}: Failed - {e}")
            self.session.fail(str(e))
            self.session.save(self.session_dir)
            
            # Trigger recovery on failure
            if self.rollback:
                logger.info("Triggering recovery due to failure")
                await self.rollback.recover_all()
            raise
        
        finally:
            # Stop watchdog
            if self.watchdog:
                self.watchdog.cancel()
            
            # Close channels
            await self._close_channels()
        
        return self.session
    
    async def _init_components(self) -> None:
        """Initialize channels and safety components."""
        session_path = Path(self.session_dir) / self.session.session_id
        session_path.mkdir(parents=True, exist_ok=True)
        
        # Safety components
        self.rollback = RollbackJournal(session_path)
        self.guard = SafetyGuard()
        
        # Watchdog
        timeout = getattr(self.config.global_.safety, 'auto_recover_timeout', 600)
        self.watchdog = FaultWatchdog(
            timeout_seconds=timeout,
            rollback_journal=self.rollback,
            session_id=self.session.session_id,
        )
        
        # Build inventory
        inventory = self._build_inventory()
        
        # SSH Channel
        self.ssh = SSHChannel(
            inventory=inventory,
            dry_run=self.dry_run,
            wal=self.rollback,
            guard=self.guard,
        )
        
        # Prometheus Channel
        prometheus_url = getattr(self.config.global_, 'prometheus_url', 'http://localhost:9090')
        self.prometheus = PrometheusChannel(
            base_url=prometheus_url,
            dry_run=self.dry_run,
        )
        
        # K8s Channel
        kubeconfig = getattr(self.config.channels.kubernetes, 'kubeconfig', '~/.kube/config') if hasattr(self.config, 'channels') else '~/.kube/config'
        self.kubernetes = K8sChannel(
            kubeconfig=kubeconfig,
            dry_run=self.dry_run,
            wal=self.rollback,
        )
        
        # Redfish Channel (BMC)
        bmc_devices = self._build_bmc_inventory()
        if bmc_devices:
            self.redfish = RedfishChannel(
                devices=bmc_devices,
                dry_run=self.dry_run,
                wal=self.rollback,
                guard=self.guard,
            )
        
        # Switch Channel
        switch_devices = self._build_switch_inventory()
        if switch_devices:
            self.switch = SwitchChannel(
                devices=switch_devices,
                dry_run=self.dry_run,
                wal=self.rollback,
                guard=self.guard,
            )
        
        self.session.add_event("Components initialized")
    
    def _build_inventory(self) -> dict:
        """Build node inventory from config."""
        inventory = {}
        if hasattr(self.config, 'inventory') and self.config.inventory:
            for group_name, nodes in self.config.inventory.items():
                if isinstance(nodes, list):
                    for node in nodes:
                        if hasattr(node, 'name'):
                            inventory[node.name] = node
        return inventory
    
    def _build_bmc_inventory(self) -> dict:
        """Build BMC device inventory."""
        devices = {}
        if hasattr(self.config, 'inventory') and self.config.inventory:
            for group_name, nodes in self.config.inventory.items():
                if isinstance(nodes, list):
                    for node in nodes:
                        if hasattr(node, 'bmc') and node.bmc:
                            devices[node.name] = {
                                "host": node.bmc.host,
                                "user": node.bmc.user,
                                "password": node.bmc.password,
                            }
        return devices
    
    def _build_switch_inventory(self) -> dict:
        """Build switch device inventory."""
        devices = {}
        # TODO: Parse switch inventory from config
        return devices
    
    async def _preflight_check(self) -> None:
        """Verify connectivity to all targets."""
        self.session.add_event("Preflight check started")
        
        # Check SSH connectivity
        if self.ssh:
            # TODO: Implement connectivity check
            pass
        
        # Check Prometheus connectivity
        if self.prometheus:
            try:
                await self.prometheus.query_instant("up")
                logger.info("Prometheus connectivity: OK")
            except Exception as e:
                logger.warning(f"Prometheus connectivity check failed: {e}")
        
        self.session.add_event("Preflight check completed")
    
    async def _collect_baseline(self) -> dict:
        """Collect baseline metrics before fault injection."""
        self.session.add_event("Baseline collection started")
        
        baseline = {}
        duration = getattr(self.config.monitor, 'baseline_duration', 60) if hasattr(self.config, 'monitor') else 60
        
        # Collect from Prometheus
        if self.prometheus:
            queries = {
                "cpu_util": "avg(rate(node_cpu_seconds_total{mode!='idle'}[1m]))",
                "memory_util": "avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
            }
            try:
                baseline = await self.prometheus.collect_baseline(queries, duration=duration)
            except Exception as e:
                logger.warning(f"Baseline collection failed: {e}")
        
        self.session.add_event("Baseline collection completed")
        return baseline
    
    def _resolve_scenarios(self) -> list[ScenarioConfig]:
        """Resolve enabled scenarios from config."""
        scenarios = []
        if hasattr(self.config, 'scenarios') and self.config.scenarios:
            for name, config in self.config.scenarios.items():
                if hasattr(config, 'enabled') and config.enabled:
                    scenarios.append(config)
        return scenarios
    
    async def _run_scenario(
        self,
        config: ScenarioConfig,
        baseline: dict,
    ) -> None:
        """Execute a single fault scenario."""
        scenario_name = getattr(config, 'name', 'unknown')
        
        # Get scenario class from registry
        scenario_class = SCENARIO_REGISTRY.get(scenario_name)
        if not scenario_class:
            logger.warning(f"Scenario not found in registry: {scenario_name}")
            return
        
        scenario = scenario_class()
        
        # Create fault context
        target_nodes = getattr(config, 'target_nodes', [])
        target_node = target_nodes[0] if target_nodes else ""
        params = getattr(config, 'params', {})
        
        # Generate fault ID
        import uuid
        fault_id = uuid.uuid4().hex[:8]
        
        ctx = FaultContext(
            ssh=self.ssh,
            rollback=self.rollback,
            guard=self.guard,
            target_node=target_node,
            params=params,
            fault_id=fault_id,
            prometheus=self.prometheus,
            kubernetes=self.kubernetes,
            redfish=self.redfish,
            switch=self.switch,
        )
        
        # Track result
        result = ScenarioResult(scenario_name=scenario_name)
        
        try:
            # Phase 4: Inject
            self.session.set_phase(SessionPhase.INJECT)
            self.session.save(self.session_dir)
            
            inject_result = await scenario.inject(ctx)
            result.inject_success = inject_result.success
            
            if not inject_result.success:
                result.error = inject_result.error or "Injection failed"
                logger.error(f"Scenario {scenario_name} injection failed: {result.error}")
                return
            
            # Track active fault
            from datetime import datetime
            self.session.add_active_fault(ActiveFault(
                fault_id=fault_id,
                scenario_name=scenario_name,
                target_node=target_node,
                injected_at=datetime.now(),
                params=params,
            ))
            self.session.save(self.session_dir)
            
            # Phase 5: Observe
            self.session.set_phase(SessionPhase.OBSERVE)
            self.session.save(self.session_dir)
            
            duration = params.get("duration", 60)
            if not self.dry_run:
                await asyncio.sleep(duration)
            else:
                logger.info(f"[DRY-RUN] Skip observation period ({duration}s)")
            
            # Phase 6: Recover
            self.session.set_phase(SessionPhase.RECOVER)
            self.session.save(self.session_dir)
            
            recover_result = await scenario.recover(ctx)
            result.recover_success = recover_result.success
            
            # Also run WAL recovery
            if self.rollback:
                await self.rollback.recover_fault(fault_id)
            
            # Remove from active faults
            self.session.remove_active_fault(fault_id)
            
            # Phase 7: Verify
            self.session.set_phase(SessionPhase.VERIFY)
            self.session.save(self.session_dir)
            
            result.verified = await scenario.verify(ctx)
            
        except Exception as e:
            logger.error(f"Scenario {scenario_name} execution failed: {e}")
            result.error = str(e)
        
        finally:
            # Store result
            self.session.scenario_results[scenario_name] = result
            self.session.save(self.session_dir)
    
    async def _generate_report(self) -> None:
        """Generate fault injection report."""
        self.session.add_event("Report generation started")
        
        # TODO: Implement report generation
        # - Timeline
        # - HTML report
        # - Charts
        # - Resilience score
        
        self.session.add_event("Report generation completed")
    
    async def _close_channels(self) -> None:
        """Close all channel connections."""
        if self.ssh:
            await self.ssh.close()
        if self.prometheus:
            await self.prometheus.close()
        if self.switch:
            await self.switch.close()
    
    @classmethod
    async def resume(
        cls,
        session_id: str,
        session_dir: str = "./fault-reports/sessions/",
    ) -> Session:
        """
        Resume a paused or interrupted session.
        
        Args:
            session_id: Session ID to resume
            session_dir: Session directory
            
        Returns:
            Session: Resumed session
        """
        session = Session.load(session_id, session_dir)
        if not session:
            raise OrchestrationError(f"Session not found: {session_id}")
        
        # Check if session can be resumed
        if session.status == SessionStatus.COMPLETED:
            logger.info(f"Session {session_id} already completed")
            return session
        
        # Recover active faults first
        if session.active_faults:
            logger.info(f"Recovering {len(session.active_faults)} active faults")
            session_path = Path(session_dir) / session_id
            rollback = RollbackJournal(session_path)
            await rollback.recover_all()
        
        # TODO: Continue from interrupted phase
        
        return session
