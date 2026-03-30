#!/usr/bin/env python3
"""Generate Wave-5 quantitative acceptance report from archived drill evidence."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Thresholds:
    ws_delivery_p95_max: float = 2.0
    reconnect_success_rate_min: float = 0.99
    event_missing_rate_max: float = 0.01
    approval_to_execution_p95_max: float = 300.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Wave-5 acceptance report from evidence.")
    parser.add_argument("--run-id", required=True, help="Evidence run id under sre_agent/docs/evidence.")
    parser.add_argument("--evidence-root", default="sre_agent/docs/evidence", help="Evidence root directory.")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _format_bool(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _check_max(value: float | None, threshold: float) -> tuple[bool, str]:
    if value is None:
        return False, "missing metric"
    ok = value <= threshold
    return ok, f"{value:.4f} <= {threshold:.4f}"


def _check_min(value: float | None, threshold: float) -> tuple[bool, str]:
    if value is None:
        return False, "missing metric"
    ok = value >= threshold
    return ok, f"{value:.4f} >= {threshold:.4f}"


def main() -> int:
    args = _parse_args()
    thresholds = Thresholds()
    run_dir = Path(args.evidence_root) / args.run_id
    metrics_path = run_dir / "metrics" / "wave5_drill_metrics.json"
    summary_path = run_dir / "api" / "drill_summary.json"
    reconnect_path = run_dir / "ws" / "reconnect_checks.json"

    if not metrics_path.exists():
        raise SystemExit(f"missing metrics file: {metrics_path}")

    metrics = _load_json(metrics_path)
    summary = _load_json(summary_path) if summary_path.exists() else {}
    reconnect = _load_json(reconnect_path) if reconnect_path.exists() else []
    if not isinstance(reconnect, list):
        reconnect = []

    checks: list[dict[str, Any]] = []
    ws_delivery_ok, ws_delivery_detail = _check_max(metrics.get("ws_delivery_seconds_p95"), thresholds.ws_delivery_p95_max)
    checks.append(
        {
            "name": "WS事件到达P95",
            "passed": ws_delivery_ok,
            "detail": ws_delivery_detail,
        }
    )
    reconnect_ok, reconnect_detail = _check_min(
        metrics.get("reconnect_success_rate"),
        thresholds.reconnect_success_rate_min,
    )
    checks.append(
        {
            "name": "WS重连成功率",
            "passed": reconnect_ok,
            "detail": reconnect_detail,
        }
    )
    missing_ok, missing_detail = _check_max(metrics.get("event_missing_rate"), thresholds.event_missing_rate_max)
    checks.append(
        {
            "name": "事件缺失率",
            "passed": missing_ok,
            "detail": missing_detail,
        }
    )
    approval_ok, approval_detail = _check_max(
        metrics.get("execution_duration_seconds"),
        thresholds.approval_to_execution_p95_max,
    )
    checks.append(
        {
            "name": "审批到执行完成耗时",
            "passed": approval_ok,
            "detail": approval_detail,
        }
    )

    api_checks = {
        "diagnose_success": bool(summary.get("diagnose_success")),
        "approve_success": bool(summary.get("approve_success")),
        "rollback_success": bool(summary.get("rollback_success")),
    }
    for key, passed in api_checks.items():
        checks.append({"name": key, "passed": passed, "detail": "true" if passed else "false"})

    overall_passed = all(item["passed"] for item in checks)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_id": args.run_id,
        "overall_passed": overall_passed,
        "thresholds": thresholds.__dict__,
        "metrics": metrics,
        "reconnect_checks": reconnect,
        "checks": checks,
    }

    report_json = run_dir / "metrics" / "wave5_acceptance_report.json"
    report_md = run_dir / "metrics" / "wave5_acceptance_report.md"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# Wave 5 Acceptance Report ({args.run_id})",
        "",
        f"- 生成时间: {report['generated_at']}",
        f"- 总体结论: {_format_bool(overall_passed)}",
        "",
        "## Checks",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for item in checks:
        lines.append(f"| {item['name']} | {_format_bool(bool(item['passed']))} | {item['detail']} |")
    lines.extend(
        [
            "",
            "## Raw Metrics",
            "",
            "```json",
            json.dumps(metrics, ensure_ascii=False, indent=2),
            "```",
        ]
    )
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(str(report_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
