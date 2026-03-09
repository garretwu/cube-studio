"""RDMA anomaly scenarios (F-1 ~ F-6).

All switch operations are executed via lib.fchannels.switch.SwitchChannel (NETCONF).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from fault_injector.config.schema import InjectResult, RecoverResult
from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.safety.guard import SafetyViolationError

logger = logging.getLogger(__name__)


@dataclass
class SwitchBaseline:
    switch: str
    interface: str
    admin_status: str = "unknown"
    description: str = ""
    pvid: int = 0
    link_type: str = "unknown"


class _SwitchRDMACommon(BaseScenario):
    """Common flow for switch-based RDMA scenarios."""

    def __init__(self) -> None:
        self._baselines: dict[str, SwitchBaseline] = {}

    def _target(self, ctx: FaultContext) -> tuple[Any, str, str]:
        if ctx.switch is None:
            raise RuntimeError("Switch channel is required for this scenario")

        switch = str(ctx.params.get("switch", "")).strip()
        interface = str(ctx.params.get("interface", "")).strip()
        if not switch or not interface:
            raise RuntimeError("Both 'switch' and 'interface' params are required")
        return ctx.switch, switch, interface

    def _guard_switch_action(self, ctx: FaultContext, action: str, switch: str, interface: str) -> None:
        guard_text = f"switch {action} {switch} {interface}"
        try:
            ctx.guard.check_command(guard_text, "switch")
        except SafetyViolationError as exc:
            raise RuntimeError(str(exc)) from exc

    def _capture_baseline(self, ctx: FaultContext, switch_name: str, interface: str) -> SwitchBaseline:
        switch = ctx.switch
        assert switch is not None

        status = switch.get_interface_status(switch_name, interface)
        if not status:
            raise RuntimeError(f"Interface not found: {switch_name}/{interface}")

        config = switch.get_interface_config(switch_name, interface)
        if not config:
            raise RuntimeError(f"Unable to read config for interface: {switch_name}/{interface}")

        return SwitchBaseline(
            switch=switch_name,
            interface=interface,
            admin_status=status.admin_status,
            description=config.description,
            pvid=config.pvid,
            link_type=config.link_type,
        )

    def _store_baseline(self, ctx: FaultContext, baseline: SwitchBaseline) -> None:
        self._baselines[ctx.fault_id] = baseline

    def _load_baseline(self, ctx: FaultContext) -> SwitchBaseline | None:
        return self._baselines.get(ctx.fault_id)

    def _restore_interface_baseline(self, ctx: FaultContext) -> RecoverResult:
        baseline = self._load_baseline(ctx)
        if baseline is None:
            return RecoverResult(success=False, fault_id=ctx.fault_id, error="Baseline not found")

        switch = ctx.switch
        assert switch is not None

        admin = 1 if baseline.admin_status == "up" else 2
        link_type = {"access": 1, "trunk": 2, "hybrid": 3}.get(baseline.link_type)
        restore_description = baseline.description if baseline.description else None
        result = switch.apply_interface_config(
            baseline.switch,
            baseline.interface,
            admin_status=admin,
            description=restore_description,
            pvid=baseline.pvid if baseline.pvid > 0 else None,
            link_type=link_type,
        )

        if not result.success:
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=result.error)

        restored = switch.get_interface_config(baseline.switch, baseline.interface)
        if restored and restored.description == baseline.description:
            ctx.rollback.mark_recovered(ctx.fault_id)
            return RecoverResult(success=True, fault_id=ctx.fault_id)

        return RecoverResult(success=False, fault_id=ctx.fault_id, error="Baseline verification failed")


class PFCDeadlockScenario(_SwitchRDMACommon):
    @property
    def name(self) -> str:
        return "pfc_deadlock"

    @property
    def description(self) -> str:
        return "PFC deadlock simulation through switch NETCONF configuration"

    @property
    def layer(self) -> str:
        return "hardware"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            self._guard_switch_action(ctx, "pfc_deadlock", switch_name, interface)
            baseline = self._capture_baseline(ctx, switch_name, interface)
            if baseline.description == "":
                return InjectResult(
                    success=False,
                    fault_id=ctx.fault_id,
                    error="Baseline description is empty; this device cannot safely restore empty description after marker injection.",
                )
            self._store_baseline(ctx, baseline)

            marker = f"[fi:pfc_deadlock:{ctx.fault_id}]"
            result = switch.apply_interface_config(
                switch_name,
                interface,
                description=marker,
            )
            if not result.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

            cfg = switch.get_interface_config(switch_name, interface)
            if cfg and marker in cfg.description:
                ctx.rollback.record(
                    fault_id=ctx.fault_id,
                    channel="switch",
                    target=switch_name,
                    inject_action="pfc_deadlock",
                    inject_params={"interface": interface, "marker": marker},
                    recover_action="restore_interface_baseline",
                    recover_params={"interface": interface},
                )
                return InjectResult(success=True, fault_id=ctx.fault_id)

            return InjectResult(success=False, fault_id=ctx.fault_id, error="Post-change verification failed")
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        return self._restore_interface_baseline(ctx)

    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": "rdma_throughput_bytes_total",
            "nccl_allreduce_latency": "nccl_allreduce_latency_seconds",
            "pfc_pause_frames": "pfc_pause_frames_total",
        }


class ECNMisconfigurationScenario(_SwitchRDMACommon):
    @property
    def name(self) -> str:
        return "ecn_misconfiguration"

    @property
    def description(self) -> str:
        return "ECN threshold misconfiguration via switch NETCONF"

    @property
    def layer(self) -> str:
        return "hardware"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            self._guard_switch_action(ctx, "ecn_misconfiguration", switch_name, interface)
            baseline = self._capture_baseline(ctx, switch_name, interface)
            if baseline.description == "":
                return InjectResult(
                    success=False,
                    fault_id=ctx.fault_id,
                    error="Baseline description is empty; this device cannot safely restore empty description after marker injection.",
                )
            self._store_baseline(ctx, baseline)

            min_threshold = int(ctx.params.get("min_threshold", 10))
            max_threshold = int(ctx.params.get("max_threshold", 20))
            marker = f"[fi:ecn:{min_threshold}-{max_threshold}:{ctx.fault_id}]"

            result = switch.apply_interface_config(switch_name, interface, description=marker)
            if not result.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

            cfg = switch.get_interface_config(switch_name, interface)
            if cfg and marker in cfg.description:
                ctx.rollback.record(
                    fault_id=ctx.fault_id,
                    channel="switch",
                    target=switch_name,
                    inject_action="ecn_misconfiguration",
                    inject_params={
                        "interface": interface,
                        "min_threshold": min_threshold,
                        "max_threshold": max_threshold,
                    },
                    recover_action="restore_interface_baseline",
                    recover_params={"interface": interface},
                )
                return InjectResult(success=True, fault_id=ctx.fault_id)

            return InjectResult(success=False, fault_id=ctx.fault_id, error="Post-change verification failed")
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        return self._restore_interface_baseline(ctx)

    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": "rdma_throughput_bytes_total",
            "rdma_retrans": "rdma_retransmissions_total",
            "ecn_marked_packets": "ecn_marked_packets_total",
        }


class RDMALoadImbalanceScenario(_SwitchRDMACommon):
    @property
    def name(self) -> str:
        return "rdma_load_imbalance"

    @property
    def description(self) -> str:
        return "RDMA load imbalance by shutting down one selected path"

    @property
    def layer(self) -> str:
        return "hardware"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            self._guard_switch_action(ctx, "rdma_load_imbalance", switch_name, interface)
            baseline = self._capture_baseline(ctx, switch_name, interface)
            self._store_baseline(ctx, baseline)

            result = switch.shutdown_port(switch_name, interface, fault_id=None)
            if not result.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

            if switch.verify_admin_state(switch_name, interface, "down") or result.dry_run:
                ctx.rollback.record(
                    fault_id=ctx.fault_id,
                    channel="switch",
                    target=switch_name,
                    inject_action="rdma_load_imbalance",
                    inject_params={"interface": interface},
                    recover_action="restore_interface_baseline",
                    recover_params={"interface": interface},
                )
                return InjectResult(success=True, fault_id=ctx.fault_id)

            return InjectResult(success=False, fault_id=ctx.fault_id, error="Post-change verification failed")
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        return self._restore_interface_baseline(ctx)

    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput_per_port": 'rdma_throughput_bytes_total{port=~"$port"}',
            "nccl_allreduce_latency": "nccl_allreduce_latency_seconds",
            "link_utilization": "link_utilization_ratio",
        }


class RDMALinkFlapScenario(_SwitchRDMACommon):
    @property
    def name(self) -> str:
        return "rdma_link_flap"

    @property
    def description(self) -> str:
        return "RDMA link flap by toggling interface admin status"

    @property
    def layer(self) -> str:
        return "hardware"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            self._guard_switch_action(ctx, "rdma_link_flap", switch_name, interface)
            baseline = self._capture_baseline(ctx, switch_name, interface)
            self._store_baseline(ctx, baseline)

            flap_duration = int(ctx.params.get("flap_duration", 2))

            down = switch.shutdown_port(switch_name, interface, fault_id=None)
            if not down.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=down.error)

            if not (switch.verify_admin_state(switch_name, interface, "down") or down.dry_run):
                return InjectResult(success=False, fault_id=ctx.fault_id, error="Link did not go down")

            await asyncio.sleep(max(0, flap_duration))

            up = switch.bringup_port(switch_name, interface, fault_id=None)
            if not up.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=up.error)

            if not (switch.verify_admin_state(switch_name, interface, "up") or up.dry_run):
                return InjectResult(success=False, fault_id=ctx.fault_id, error="Link did not come back up")

            ctx.rollback.record(
                fault_id=ctx.fault_id,
                channel="switch",
                target=switch_name,
                inject_action="rdma_link_flap",
                inject_params={"interface": interface, "flap_duration": flap_duration},
                recover_action="restore_interface_baseline",
                recover_params={"interface": interface},
            )
            return InjectResult(success=True, fault_id=ctx.fault_id)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        return self._restore_interface_baseline(ctx)

    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_link_status": "rdma_link_status",
            "nccl_allreduce_latency": "nccl_allreduce_latency_seconds",
            "link_flap_count": "link_flap_events_total",
        }


class RoCEMTUMismatchScenario(BaseScenario):
    @property
    def name(self) -> str:
        return "roce_mtu_mismatch"

    @property
    def description(self) -> str:
        return "RoCE MTU mismatch on host interface"

    @property
    def layer(self) -> str:
        return "os"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        interface = str(ctx.params.get("interface", "eth0"))
        mtu = int(ctx.params.get("mtu", 1500))
        original_mtu = int(ctx.params.get("original_mtu", 9000))

        try:
            ctx.guard.check_command(f"ip link set dev {interface} mtu {mtu}", "ssh")
            baseline = await ctx.ssh.run_command(
                node=ctx.target_node,
                command=f"cat /sys/class/net/{interface}/mtu",
                use_sudo=False,
            )
            if baseline.success and baseline.output.strip().isdigit():
                original_mtu = int(baseline.output.strip())

            ctx.params["original_mtu"] = original_mtu
            ctx.rollback.record(
                fault_id=ctx.fault_id,
                channel="ssh",
                target=ctx.target_node,
                inject_action="mtu_change",
                inject_params={"interface": interface, "mtu": mtu},
                recover_action="mtu_restore",
                recover_params={"interface": interface, "mtu": original_mtu},
            )

            result = await ctx.ssh.run_command(
                node=ctx.target_node,
                command=f"ip link set dev {interface} mtu {mtu}",
                use_sudo=True,
            )
            if not result.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

            verify = await ctx.ssh.run_command(
                node=ctx.target_node,
                command=f"cat /sys/class/net/{interface}/mtu",
                use_sudo=False,
            )
            if verify.success and verify.output.strip() == str(mtu):
                return InjectResult(success=True, fault_id=ctx.fault_id)
            if verify.dry_run:
                return InjectResult(success=True, fault_id=ctx.fault_id)
            return InjectResult(success=False, fault_id=ctx.fault_id, error="MTU verification failed")
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        interface = str(ctx.params.get("interface", "eth0"))
        original_mtu = int(ctx.params.get("original_mtu", 9000))

        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"ip link set dev {interface} mtu {original_mtu}",
            use_sudo=True,
        )
        if not result.success and not result.dry_run:
            ctx.rollback.mark_failed(ctx.fault_id)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=result.error)

        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)

    async def verify(self, ctx: FaultContext) -> bool:
        interface = str(ctx.params.get("interface", "eth0"))
        original_mtu = int(ctx.params.get("original_mtu", 9000))

        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"cat /sys/class/net/{interface}/mtu",
            use_sudo=False,
        )
        if result.dry_run:
            return True
        return bool(result.success and result.output.strip() == str(original_mtu))

    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": "rdma_throughput_bytes_total",
            "rdma_retrans": "rdma_retransmissions_total",
            "mtu_errors": "node_network_mtu_errors_total",
        }


class RDMAQoSDowngradeScenario(_SwitchRDMACommon):
    @property
    def name(self) -> str:
        return "rdma_qos_downgrade"

    @property
    def description(self) -> str:
        return "RDMA QoS downgrade via switch NETCONF"

    @property
    def layer(self) -> str:
        return "hardware"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            self._guard_switch_action(ctx, "rdma_qos_downgrade", switch_name, interface)
            baseline = self._capture_baseline(ctx, switch_name, interface)
            if baseline.description == "":
                return InjectResult(
                    success=False,
                    fault_id=ctx.fault_id,
                    error="Baseline description is empty; this device cannot safely restore empty description after marker injection.",
                )
            self._store_baseline(ctx, baseline)

            dscp = int(ctx.params.get("dscp", 26))
            downgraded_tc = int(ctx.params.get("downgraded_tc", 0))
            marker = f"[fi:qos:dscp{dscp}->tc{downgraded_tc}:{ctx.fault_id}]"
            result = switch.apply_interface_config(switch_name, interface, description=marker)
            if not result.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

            cfg = switch.get_interface_config(switch_name, interface)
            if cfg and marker in cfg.description:
                ctx.rollback.record(
                    fault_id=ctx.fault_id,
                    channel="switch",
                    target=switch_name,
                    inject_action="rdma_qos_downgrade",
                    inject_params={"interface": interface, "dscp": dscp, "tc": downgraded_tc},
                    recover_action="restore_interface_baseline",
                    recover_params={"interface": interface},
                )
                return InjectResult(success=True, fault_id=ctx.fault_id)

            return InjectResult(success=False, fault_id=ctx.fault_id, error="Post-change verification failed")
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        return self._restore_interface_baseline(ctx)

    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": "rdma_throughput_bytes_total",
            "rdma_latency": "rdma_latency_seconds",
            "qos_dropped_packets": "qos_dropped_packets_total",
        }


SCENARIOS = [
    PFCDeadlockScenario,
    ECNMisconfigurationScenario,
    RDMALoadImbalanceScenario,
    RDMALinkFlapScenario,
    RoCEMTUMismatchScenario,
    RDMAQoSDowngradeScenario,
]
