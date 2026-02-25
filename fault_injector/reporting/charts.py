from __future__ import annotations


def build_chart_payload(summary: dict[str, object]) -> dict[str, object]:
    return {
        "type": "bar",
        "labels": ["succeeded", "failed"],
        "values": [summary.get("succeeded", 0), summary.get("failed", 0)],
    }
