from __future__ import annotations

from fault_injector.orchestrator.session import ActionResult


def build_timeline(reports: list[ActionResult]) -> list[dict[str, object]]:
    return [
        {
            "host": item.host,
            "inject_command": item.inject_command,
            "rollback_command": item.rollback_command,
            "success": item.success,
        }
        for item in reports
    ]
