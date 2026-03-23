"""Human approval gates for remediation execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sre_agent.models.remediation import RemediationPlan


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    user: str = Field(default="unknown", min_length=1)
    reason: str | None = None


class ApprovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    method: Literal["auto", "human", "blocked", "policy"] = "human"
    reason: str | None = None
    approver: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalGate:
    AUTO_APPROVE = {
        "k8s.delete_pod",
        "network.switch_port_enable",
    }
    HUMAN_CONFIRM = {
        "k8s.scale_deployment",
        "k8s.cordon_node",
        "k8s.drain_node",
        "network.switch_port_disable",
        "network.update_route",
        "network.set_bmc_vlan",
        "network.set_bmc_mtu",
        "remediation.execute_plan",
    }
    BLOCKED = {
        "delete_namespace",
        "factory_reset_bmc",
        "factory_reset_switch",
        "format_disk",
    }

    def __init__(self, timeout_seconds: int = 300, default_policy: str = "human_confirm") -> None:
        self.timeout_seconds = timeout_seconds
        self.default_policy = default_policy
        self.approval_queue: "asyncio.Queue[dict[str, str | bool | None]]" = asyncio.Queue()

    async def request_approval(self, plan: RemediationPlan, session_id: str | None = None) -> ApprovalResult:
        tools_used = {step.tool for step in plan.steps}
        if tools_used & self.BLOCKED:
            return ApprovalResult(
                approved=False,
                method="blocked",
                reason=f"blocked operations: {sorted(tools_used & self.BLOCKED)}",
            )
        if self.default_policy == "disabled":
            return ApprovalResult(approved=False, method="policy", reason="remediation is disabled")
        if self.default_policy == "read_only":
            return ApprovalResult(approved=False, method="policy", reason="read-only mode")

        needs_human = tools_used & self.HUMAN_CONFIRM
        if not needs_human or self.default_policy == "auto_approve":
            return ApprovalResult(approved=True, method="auto")

        try:
            response = await asyncio.wait_for(self.approval_queue.get(), timeout=self.timeout_seconds)
        except TimeoutError:
            return ApprovalResult(approved=False, method="human", reason="approval timeout")

        response_session = response.get("session_id")
        if session_id and response_session not in {None, session_id}:
            return ApprovalResult(approved=False, method="human", reason="approval session mismatch")
        return ApprovalResult(
            approved=bool(response.get("approved", False)),
            method="human",
            reason=str(response.get("reason") or "") or None,
            approver=str(response.get("user") or "") or None,
        )

    async def submit_decision(self, session_id: str, approval: ApprovalInput) -> None:
        await self.approval_queue.put(
            {
                "session_id": session_id,
                "approved": approval.approved,
                "user": approval.user,
                "reason": approval.reason,
            }
        )
