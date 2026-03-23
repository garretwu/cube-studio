"""Plan normalization helpers."""

from __future__ import annotations

from sre_agent.models.remediation import RemediationPlan


def normalize_plan_steps(plan: RemediationPlan) -> RemediationPlan:
    """Return plan unchanged for now, keeping a stable planner hook."""
    return plan
