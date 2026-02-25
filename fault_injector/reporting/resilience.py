from __future__ import annotations

from fault_injector.orchestrator.session import ActionResult


def summarize_resilience(reports: list[ActionResult]) -> dict[str, object]:
    total = len(reports)
    succeeded = sum(1 for item in reports if item.success)
    return {
        "total": total,
        "succeeded": succeeded,
        "failed": total - succeeded,
        "success_rate": (succeeded / total) if total else 0.0,
    }
