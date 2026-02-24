from __future__ import annotations

from dataclasses import dataclass
import shlex
import uuid

from channel.ssh import HostSpec, SSHChannel
from fault_injector.config import InjectorConfig
from fault_injector.rollback import RollbackEntry, RollbackJournal


@dataclass(slots=True)
class ActionReport:
    host: str
    inject_command: str
    rollback_command: str
    success: bool


class FaultInjector:
    """RoCE MTU mismatch fault injector with WAL-first rollback flow."""

    def __init__(self, config: InjectorConfig, session_id: str | None = None) -> None:
        self.config = config
        self.session_id = session_id or uuid.uuid4().hex
        self.channel = SSHChannel(mode=config.mode)
        self.journal = RollbackJournal(config.wal_path)

        for srv in config.servers:
            self.channel.seed_simulated_mtu(srv.name, srv.interface, srv.original_mtu)

    def inject_roce_mtu_mismatch(self) -> list[ActionReport]:
        reports: list[ActionReport] = []
        for srv in self.config.servers:
            inject_command = self._build_set_mtu_command(srv.interface, srv.fault_mtu)
            rollback_command = self._build_set_mtu_command(srv.interface, srv.original_mtu)

            self.journal.record(
                RollbackEntry(
                    session_id=self.session_id,
                    host=srv.name,
                    rollback_command=rollback_command,
                )
            )

            result = self.channel.execute(
                HostSpec(name=srv.name, host=srv.host, user=srv.user, port=srv.port),
                inject_command,
                timeout=self.config.timeout,
            )
            reports.append(
                ActionReport(
                    host=srv.name,
                    inject_command=inject_command,
                    rollback_command=rollback_command,
                    success=result.success,
                )
            )
        return reports

    def rollback(self) -> list[ActionReport]:
        reports: list[ActionReport] = []
        entries = self.journal.load(self.session_id)
        for entry in reversed(entries):
            srv = next(s for s in self.config.servers if s.name == entry.host)
            result = self.channel.execute(
                HostSpec(name=srv.name, host=srv.host, user=srv.user, port=srv.port),
                entry.rollback_command,
                timeout=self.config.timeout,
            )
            reports.append(
                ActionReport(
                    host=entry.host,
                    inject_command="",
                    rollback_command=entry.rollback_command,
                    success=result.success,
                )
            )
        self.journal.remove_session(self.session_id)
        return reports

    @staticmethod
    def _build_set_mtu_command(interface: str, mtu: int) -> str:
        iface = shlex.quote(interface)
        return f"sudo ip link set dev {iface} mtu {int(mtu)}"
