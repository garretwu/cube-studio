"""Service layer fault agent."""
from __future__ import annotations

import time
from typing import Any

from fault_injector.agents.base import AgentResult, BaseAgent
from fault_injector.scenarios.base import FaultContext
from fault_injector.scenarios.registry import SCENARIO_REGISTRY


class ServiceFaultAgent(BaseAgent):
    def __init__(self, channels: dict | None = None):
        super().__init__(name="service_fault_agent", layer="service", channels=channels)
        self._active: dict[str, Any] = {}

    def _resolve(self, scenario_name: str) -> Any:
        scenario_class = SCENARIO_REGISTRY.get(scenario_name)
        if scenario_class is None:
            raise ValueError(f"Scenario not found: {scenario_name}")
        scenario = scenario_class()
        if scenario.layer != self.layer:
            raise ValueError(
                f"Scenario '{scenario_name}' layer mismatch: "
                f"expected '{self.layer}', got '{scenario.layer}'"
            )
        return scenario

    async def inject(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        start = time.monotonic()
        try:
            scenario = self._resolve(scenario_name)
            result = await scenario.inject(ctx)
            if result.success:
                self._active[ctx.fault_id] = scenario
            return AgentResult(
                success=result.success,
                agent_name=self.name,
                operation="inject",
                scenario_name=scenario_name,
                error=result.error or "",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return AgentResult(
                success=False,
                agent_name=self.name,
                operation="inject",
                scenario_name=scenario_name,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def recover(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        start = time.monotonic()
        try:
            scenario = self._active.get(ctx.fault_id) or self._resolve(scenario_name)
            result = await scenario.recover(ctx)
            if result.success:
                self._active.pop(ctx.fault_id, None)
            return AgentResult(
                success=result.success,
                agent_name=self.name,
                operation="recover",
                scenario_name=scenario_name,
                error=result.error or "",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return AgentResult(
                success=False,
                agent_name=self.name,
                operation="recover",
                scenario_name=scenario_name,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def verify(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        start = time.monotonic()
        try:
            scenario = self._active.get(ctx.fault_id) or self._resolve(scenario_name)
            ok = await scenario.verify(ctx)
            return AgentResult(
                success=bool(ok),
                agent_name=self.name,
                operation="verify",
                scenario_name=scenario_name,
                duration_ms=int((time.monotonic() - start) * 1000),
                metadata={"verified": bool(ok)},
            )
        except Exception as exc:
            return AgentResult(
                success=False,
                agent_name=self.name,
                operation="verify",
                scenario_name=scenario_name,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    def status(self) -> dict[str, Any]:
        return {
            "agent_name": self.name,
            "layer": self.layer,
            "active_faults": len(self._active),
            "active_fault_ids": list(self._active.keys()),
        }
