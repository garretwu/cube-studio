from __future__ import annotations

import shlex

from channel.ssh import HostSpec
from fault_injector.config import InjectorConfig
from fault_injector.orchestrator.session import FaultStep


class RDMAMtuAnomalyScenario:
    """Describe RDMA/ROCE MTU mismatch steps and parameters only."""

    name = "rdma_anomaly"

    @staticmethod
    def _build_set_mtu_command(interface: str, mtu: int) -> str:
        iface = shlex.quote(interface)
        return f"sudo ip link set dev {iface} mtu {int(mtu)}"

    def build_steps(self, config: InjectorConfig) -> list[FaultStep]:
        steps: list[FaultStep] = []
        for srv in config.servers:
            steps.append(
                FaultStep(
                    host=HostSpec(name=srv.name, host=srv.host, user=srv.user, port=srv.port),
                    inject_command=self._build_set_mtu_command(srv.interface, srv.fault_mtu),
                    rollback_command=self._build_set_mtu_command(srv.interface, srv.original_mtu),
                    parameters={
                        "interface": srv.interface,
                        "fault_mtu": srv.fault_mtu,
                        "original_mtu": srv.original_mtu,
                    },
                )
            )
        return steps
