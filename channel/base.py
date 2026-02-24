from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CommandResult:
    success: bool
    command: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    simulated: bool = False
