from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass

from .base import CommandResult


@dataclass(slots=True)
class HostSpec:
    name: str
    host: str
    user: str
    port: int = 22
    password: str | None = None


class SSHChannel:
    """SSH command channel with local simulation mode."""

    _MTU_PATTERN = re.compile(r"ip link set dev (?P<iface>\S+) mtu (?P<mtu>\d+)")

    def __init__(self, mode: str = "simulate") -> None:
        if mode not in {"simulate", "ssh"}:
            raise ValueError("mode must be simulate or ssh")
        self.mode = mode
        self._simulated_mtu: dict[tuple[str, str], int] = {}

    def seed_simulated_mtu(self, host: str, interface: str, mtu: int) -> None:
        self._simulated_mtu[(host, interface)] = mtu

    def get_simulated_mtu(self, host: str, interface: str) -> int | None:
        return self._simulated_mtu.get((host, interface))

    def execute(self, host_spec: HostSpec, remote_command: str, timeout: int = 20) -> CommandResult:
        if self.mode == "simulate":
            return self._simulate_execute(host_spec, remote_command)
        ssh_target = f"{host_spec.user}@{host_spec.host}"
        quoted_command = shlex.quote(remote_command)
        cmd = f"ssh -p {host_spec.port} {shlex.quote(ssh_target)} {quoted_command}"
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            success=proc.returncode == 0,
            command=cmd,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
            simulated=False,
        )

    def _simulate_execute(self, host_spec: HostSpec, remote_command: str) -> CommandResult:
        match = self._MTU_PATTERN.search(remote_command)
        if match:
            iface = match.group("iface")
            mtu = int(match.group("mtu"))
            self._simulated_mtu[(host_spec.name, iface)] = mtu
        return CommandResult(
            success=True,
            command=remote_command,
            stdout="simulated",
            simulated=True,
        )
