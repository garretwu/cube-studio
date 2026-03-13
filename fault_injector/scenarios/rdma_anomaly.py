"""RDMA anomaly scenarios (F-1 ~ F-6).

All switch operations are executed via lib.channels.switch.SwitchChannel (NETCONF).
"""
from __future__ import annotations

import asyncio
import logging
import re
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

    @staticmethod
    def _dot1p_values(params: dict[str, Any]) -> list[int]:
        value = params.get("dot1p_priorities", list(range(8)))
        if isinstance(value, str):
            parts = [part for part in value.replace(",", " ").split(" ") if part]
            priorities = [int(part) for part in parts]
        elif isinstance(value, list):
            priorities = [int(item) for item in value]
        else:
            priorities = [int(value)]

        if not priorities:
            raise RuntimeError("dot1p priorities cannot be empty")
        if any(p < 0 or p > 7 for p in priorities):
            raise RuntimeError("dot1p priorities must be in range 0..7")

        return sorted(set(priorities))

    @staticmethod
    def _dot1p_cli_arg(priorities: list[int]) -> str:
        values = sorted(set(int(p) for p in priorities))
        if not values:
            return ""

        ranges: list[str] = []
        start = values[0]
        prev = values[0]
        for value in values[1:]:
            if value == prev + 1:
                prev = value
                continue
            ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = value
            prev = value
        ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
        return " ".join(ranges)

    @staticmethod
    def _extract_dot1p_from_display(output: str, interface_abbrev: str) -> str:
        lines = [raw.strip() for raw in (output or "").splitlines() if raw.strip()]
        candidates = [line for line in lines if line.startswith(interface_abbrev)]
        if not candidates:
            candidates = [line for line in lines if "/" in line and "Enabled" in line]

        for line in candidates:
            parts = line.split()
            if len(parts) < 4:
                continue
            # Interface AdminMode OperMode [Dot1pList] Prio ...
            if parts[3].isdigit():
                return ""
            return parts[3]
        return ""

    async def inject(self, ctx: FaultContext) -> InjectResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            self._guard_switch_action(ctx, "pfc_deadlock", switch_name, interface)
            baseline = self._capture_baseline(ctx, switch_name, interface)
            self._store_baseline(ctx, baseline)

            status = switch.get_interface_status(switch_name, interface)
            status_name = getattr(status, "name", None) if status is not None else None
            status_abbrev = getattr(status, "abbreviated_name", None) if status is not None else None
            cli_interface = status_name if isinstance(status_name, str) and status_name else interface
            cli_abbrev = status_abbrev if isinstance(status_abbrev, str) and status_abbrev else interface
            ctx.params["_pfc_cli_interface"] = cli_interface
            ctx.params["_pfc_cli_abbrev"] = cli_abbrev

            priorities = self._dot1p_values(ctx.params)
            ctx.params["dot1p_priorities"] = priorities
            dot1p_arg = self._dot1p_cli_arg(priorities)
            show_before = switch.run_cli_execution(
                switch_name,
                f"display priority-flow-control interface {cli_interface}",
            )
            if not show_before.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=show_before.error)
            baseline_dot1p = self._extract_dot1p_from_display(show_before.output or "", cli_abbrev)
            ctx.params["_pfc_baseline_dot1p_text"] = baseline_dot1p

            pfc_result = switch.apply_cli_commands(
                switch_name,
                [
                    f"interface {cli_interface}",
                    f"priority-flow-control no-drop dot1p {dot1p_arg}",
                ],
            )
            if not pfc_result.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=pfc_result.error)

            show_after = switch.run_cli_execution(
                switch_name,
                f"display priority-flow-control interface {cli_interface}",
            )
            if not show_after.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=show_after.error)
            current_dot1p = self._extract_dot1p_from_display(show_after.output or "", cli_abbrev)
            if not current_dot1p:
                return InjectResult(success=False, fault_id=ctx.fault_id, error="PFC post-change verification failed")

            marker = None
            if baseline.description:
                marker = f"[fi:pfc_deadlock:{ctx.fault_id}]"
                result = switch.apply_interface_config(
                    switch_name,
                    interface,
                    description=marker,
                )
                if not result.success:
                    return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

                cfg = switch.get_interface_config(switch_name, interface)
                if not (cfg and marker in cfg.description):
                    return InjectResult(success=False, fault_id=ctx.fault_id, error="Post-change verification failed")

            ctx.rollback.record(
                fault_id=ctx.fault_id,
                channel="switch",
                target=switch_name,
                inject_action="pfc_deadlock",
                inject_params={
                        "interface": interface,
                        "marker": marker,
                        "dot1p_priorities": priorities,
                        "pfc_baseline_dot1p_text": baseline_dot1p,
                    },
                recover_action="pfc_deadlock_recover",
                recover_params={"interface": interface, "dot1p_priorities": priorities},
            )
            return InjectResult(success=True, fault_id=ctx.fault_id)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            priorities = self._dot1p_values(ctx.params)
            dot1p_arg = self._dot1p_cli_arg(priorities)
            cli_interface = str(ctx.params.get("_pfc_cli_interface", interface))
            cli_abbrev = str(ctx.params.get("_pfc_cli_abbrev", interface))
            baseline_dot1p_text = str(ctx.params.get("_pfc_baseline_dot1p_text", ""))

            undo_result = switch.apply_cli_commands(
                switch_name,
                [
                    f"interface {cli_interface}",
                    f"undo priority-flow-control no-drop dot1p {dot1p_arg}",
                ],
            )
            if not undo_result.success:
                ctx.rollback.mark_failed(ctx.fault_id)
                return RecoverResult(success=False, fault_id=ctx.fault_id, error=undo_result.error)

            if baseline_dot1p_text:
                restore_result = switch.apply_cli_commands(
                    switch_name,
                    [
                        f"interface {cli_interface}",
                        f"priority-flow-control no-drop dot1p {baseline_dot1p_text}",
                    ],
                )
                if not restore_result.success:
                    ctx.rollback.mark_failed(ctx.fault_id)
                    return RecoverResult(success=False, fault_id=ctx.fault_id, error=restore_result.error)

            show_after = switch.run_cli_execution(
                switch_name,
                f"display priority-flow-control interface {cli_interface}",
            )
            if not show_after.success:
                ctx.rollback.mark_failed(ctx.fault_id)
                return RecoverResult(success=False, fault_id=ctx.fault_id, error=show_after.error)
            current_dot1p = self._extract_dot1p_from_display(show_after.output or "", cli_abbrev)
            if current_dot1p != baseline_dot1p_text:
                ctx.rollback.mark_failed(ctx.fault_id)
                return RecoverResult(success=False, fault_id=ctx.fault_id, error="PFC baseline verification failed")

            recover_result = self._restore_interface_baseline(ctx)
            if not recover_result.success:
                ctx.rollback.mark_failed(ctx.fault_id)
            return recover_result
        except Exception as exc:
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

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

    @staticmethod
    def _extract_current_interface_lines(output: str) -> list[str]:
        lines: list[str] = []
        for raw in (output or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("<") or line.startswith("#") or line == "return":
                continue
            lines.append(line)
        return lines

    async def inject(self, ctx: FaultContext) -> InjectResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            self._guard_switch_action(ctx, "ecn_misconfiguration", switch_name, interface)
            baseline = self._capture_baseline(ctx, switch_name, interface)
            self._store_baseline(ctx, baseline)

            status = switch.get_interface_status(switch_name, interface)
            status_name = getattr(status, "name", None) if status is not None else None
            cli_interface = status_name if isinstance(status_name, str) and status_name else interface
            ctx.params["_ecn_cli_interface"] = cli_interface

            queue = int(ctx.params.get("queue", 3))
            min_threshold = int(ctx.params.get("min_threshold", 10))
            max_threshold = int(ctx.params.get("max_threshold", 20))
            discard_probability = int(ctx.params.get("discard_probability", 40))

            before = switch.run_cli_execution(
                switch_name, f"display current-configuration interface {cli_interface}"
            )
            if not before.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=before.error)
            baseline_lines = self._extract_current_interface_lines(before.output or "")
            baseline_queue_lines = [line for line in baseline_lines if line.startswith(f"qos wred queue {queue} ")]
            ctx.params["_ecn_baseline_queue_lines"] = baseline_queue_lines
            ctx.params["_ecn_injected_drop_line"] = (
                f"qos wred queue {queue} drop-level 0 "
                f"low-limit {min_threshold} high-limit {max_threshold} discard-probability {discard_probability}"
            )

            inject_commands = [
                f"interface {cli_interface}",
                f"qos wred queue {queue} ecn",
                str(ctx.params["_ecn_injected_drop_line"]),
            ]
            result = switch.apply_cli_commands(switch_name, inject_commands)
            if not result.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

            after = switch.run_cli_execution(switch_name, f"display current-configuration interface {cli_interface}")
            if not after.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=after.error)
            after_lines = self._extract_current_interface_lines(after.output or "")
            if str(ctx.params["_ecn_injected_drop_line"]) not in after_lines:
                return InjectResult(success=False, fault_id=ctx.fault_id, error="Post-change verification failed")

            ctx.rollback.record(
                fault_id=ctx.fault_id,
                channel="switch",
                target=switch_name,
                inject_action="ecn_misconfiguration",
                inject_params={
                    "interface": interface,
                    "queue": queue,
                    "min_threshold": min_threshold,
                    "max_threshold": max_threshold,
                },
                recover_action="ecn_misconfiguration_recover",
                recover_params={"interface": interface, "queue": queue},
            )
            return InjectResult(success=True, fault_id=ctx.fault_id)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            queue = int(ctx.params.get("queue", 3))
            cli_interface = str(ctx.params.get("_ecn_cli_interface", interface))
            injected_line = str(
                ctx.params.get(
                    "_ecn_injected_drop_line",
                    f"qos wred queue {queue} drop-level 0 low-limit 10 high-limit 20 discard-probability 40",
                )
            )
            baseline_queue_lines = [
                str(line) for line in ctx.params.get("_ecn_baseline_queue_lines", []) if isinstance(line, str)
            ]

            undo_commands = [
                f"interface {cli_interface}",
                f"undo qos wred queue {queue} drop-level 0",
                f"undo qos wred queue {queue} ecn",
            ]
            undo_result = switch.apply_cli_commands(switch_name, undo_commands)
            if not undo_result.success:
                ctx.rollback.mark_failed(ctx.fault_id)
                return RecoverResult(success=False, fault_id=ctx.fault_id, error=undo_result.error)

            if baseline_queue_lines:
                restore_commands = [f"interface {cli_interface}", *baseline_queue_lines]
                restore_result = switch.apply_cli_commands(switch_name, restore_commands)
                if not restore_result.success:
                    ctx.rollback.mark_failed(ctx.fault_id)
                    return RecoverResult(success=False, fault_id=ctx.fault_id, error=restore_result.error)

            verify = switch.run_cli_execution(switch_name, f"display current-configuration interface {cli_interface}")
            if not verify.success:
                ctx.rollback.mark_failed(ctx.fault_id)
                return RecoverResult(success=False, fault_id=ctx.fault_id, error=verify.error)
            verify_lines = self._extract_current_interface_lines(verify.output or "")
            if injected_line in verify_lines and injected_line not in baseline_queue_lines:
                ctx.rollback.mark_failed(ctx.fault_id)
                return RecoverResult(success=False, fault_id=ctx.fault_id, error="ECN baseline verification failed")

            recover_result = self._restore_interface_baseline(ctx)
            if not recover_result.success:
                ctx.rollback.mark_failed(ctx.fault_id)
            return recover_result
        except Exception as exc:
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

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

    @staticmethod
    def _extract_dscp_dot1p_mapping(output: str, dscp: int) -> int | None:
        pattern = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")
        for raw in (output or "").splitlines():
            match = pattern.match(raw)
            if not match:
                continue
            imp = int(match.group(1))
            exp = int(match.group(2))
            if imp == dscp:
                return exp
        return None

    async def inject(self, ctx: FaultContext) -> InjectResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            self._guard_switch_action(ctx, "rdma_qos_downgrade", switch_name, interface)
            baseline = self._capture_baseline(ctx, switch_name, interface)
            self._store_baseline(ctx, baseline)

            dscp = int(ctx.params.get("dscp", 26))
            downgraded_tc = int(ctx.params.get("downgraded_tc", 0))
            before = switch.run_cli_execution(switch_name, "display qos map-table dscp-dot1p")
            if not before.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=before.error)
            baseline_tc = self._extract_dscp_dot1p_mapping(before.output or "", dscp)
            if baseline_tc is None:
                return InjectResult(
                    success=False,
                    fault_id=ctx.fault_id,
                    error=f"Unable to read baseline dscp-dot1p mapping for DSCP {dscp}",
                )
            ctx.params["_qos_baseline_tc"] = baseline_tc

            result = switch.apply_cli_commands(
                switch_name,
                [
                    "qos map-table dscp-dot1p",
                    f"import {dscp} export {downgraded_tc}",
                ],
            )
            if not result.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

            after = switch.run_cli_execution(switch_name, "display qos map-table dscp-dot1p")
            if not after.success:
                return InjectResult(success=False, fault_id=ctx.fault_id, error=after.error)
            mapped_tc = self._extract_dscp_dot1p_mapping(after.output or "", dscp)
            if mapped_tc != downgraded_tc:
                return InjectResult(success=False, fault_id=ctx.fault_id, error="Post-change verification failed")

            ctx.rollback.record(
                fault_id=ctx.fault_id,
                channel="switch",
                target=switch_name,
                inject_action="rdma_qos_downgrade",
                inject_params={"interface": interface, "dscp": dscp, "tc": downgraded_tc},
                recover_action="rdma_qos_downgrade_recover",
                recover_params={"interface": interface, "dscp": dscp},
            )
            return InjectResult(success=True, fault_id=ctx.fault_id)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        try:
            switch, switch_name, interface = self._target(ctx)
            dscp = int(ctx.params.get("dscp", 26))
            baseline_tc = int(ctx.params.get("_qos_baseline_tc", 3))
            result = switch.apply_cli_commands(
                switch_name,
                [
                    "qos map-table dscp-dot1p",
                    f"import {dscp} export {baseline_tc}",
                ],
            )
            if not result.success:
                ctx.rollback.mark_failed(ctx.fault_id)
                return RecoverResult(success=False, fault_id=ctx.fault_id, error=result.error)

            after = switch.run_cli_execution(switch_name, "display qos map-table dscp-dot1p")
            if not after.success:
                ctx.rollback.mark_failed(ctx.fault_id)
                return RecoverResult(success=False, fault_id=ctx.fault_id, error=after.error)
            mapped_tc = self._extract_dscp_dot1p_mapping(after.output or "", dscp)
            if mapped_tc != baseline_tc:
                ctx.rollback.mark_failed(ctx.fault_id)
                return RecoverResult(success=False, fault_id=ctx.fault_id, error="QoS baseline verification failed")

            recover_result = self._restore_interface_baseline(ctx)
            if not recover_result.success:
                ctx.rollback.mark_failed(ctx.fault_id)
            return recover_result
        except Exception as exc:
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

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
