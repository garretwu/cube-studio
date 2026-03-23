"""Thin NAT wrapper that profiles or evaluates an injected runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class NATRunnable(Protocol):
    async def adiagnose(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass
class NATWrappedSREAgent:
    runner: NATRunnable
    workflow_path: str
    eval_dataset_path: str

    async def run_diagnosis(self, *args: Any, **kwargs: Any) -> Any:
        return await self.runner.adiagnose(*args, **kwargs)
