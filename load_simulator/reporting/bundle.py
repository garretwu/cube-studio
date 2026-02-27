"""Write report bundle files under reports/sessions/<session_id>/report/."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from load_simulator.reporting.html_report import generate_html_report
from load_simulator.reporting.session_output import build_json_payload


def write_report_bundle(session_result: Any, output_dir: Path) -> Path:
    session_id = str(getattr(session_result, "session_id", "unknown"))
    session_root = output_dir / "sessions" / session_id
    report_dir = session_root / "report"
    charts_dir = report_dir / "charts"
    metrics_dir = session_root / "metrics"
    report_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    report_html = report_dir / "report.html"
    report_json = report_dir / "report.json"
    metrics_csv = report_dir / "metrics-raw.csv"

    report_html.write_text(generate_html_report(session_result), encoding="utf-8")
    report_json.write_text(json.dumps(build_json_payload(session_result), indent=2), encoding="utf-8")
    _write_metrics_csv(metrics_csv, getattr(session_result, "agent_results", []))
    _write_charts(charts_dir, session_result)
    _write_request_logs(session_root, getattr(session_result, "agent_results", []))
    _write_session_meta(session_root / "session.json", session_result)
    _write_events_jsonl(session_root / "events.jsonl", session_result)
    return report_html


def _write_metrics_csv(path: Path, agent_results: list[Any]) -> None:
    rows: list[dict[str, Any]] = []
    for ar in agent_results:
        base = {"agent": ar.name, "status": ar.status}
        metrics = ar.metrics or {}
        for k, v in metrics.items():
            row = dict(base)
            row["metric"] = k
            row["value"] = v
            rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["agent", "status", "metric", "value"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_charts(charts_dir: Path, session_result: Any) -> None:
    from load_simulator.reporting.charts import (
        render_bottleneck_heatmap,
        render_error_rate_timeline,
        render_latency_timeline,
        render_resource_utilization,
        render_stage_comparison,
        render_throughput_timeline,
    )

    agent_results = getattr(session_result, "agent_results", [])
    bottlenecks = getattr(session_result, "bottlenecks", [])
    adaptive_events = getattr(session_result, "adaptive_events", [])
    system_metrics_series = getattr(session_result, "system_metrics_series", [])

    (charts_dir / "latency-timeline.html").write_text(
        render_latency_timeline(agent_results), encoding="utf-8"
    )
    (charts_dir / "throughput-timeline.html").write_text(
        render_throughput_timeline(agent_results), encoding="utf-8"
    )
    (charts_dir / "error-rate-timeline.html").write_text(
        render_error_rate_timeline(agent_results), encoding="utf-8"
    )
    (charts_dir / "resource-utilization.html").write_text(
        render_resource_utilization(system_metrics_series), encoding="utf-8"
    )
    (charts_dir / "bottleneck-heatmap.html").write_text(
        render_bottleneck_heatmap(bottlenecks), encoding="utf-8"
    )
    (charts_dir / "stage-comparison.html").write_text(
        render_stage_comparison(adaptive_events), encoding="utf-8"
    )


def _write_request_logs(session_root: Path, agent_results: list[Any]) -> None:
    """Write per-agent request logs as JSONL files."""
    logs_dir = session_root / "request_logs"
    has_logs = False
    for ar in agent_results:
        raw = getattr(ar, "raw", None) or {}
        request_logs = raw.get("request_logs", [])
        if request_logs:
            if not has_logs:
                logs_dir.mkdir(parents=True, exist_ok=True)
                has_logs = True
            log_path = logs_dir / f"{ar.name}.jsonl"
            with log_path.open("a", encoding="utf-8") as f:
                for entry in request_logs:
                    f.write(json.dumps(entry, ensure_ascii=True) + "\n")


def _write_session_meta(path: Path, session_result: Any) -> None:
    payload = {
        "session_id": getattr(session_result, "session_id", ""),
        "mode": getattr(session_result, "mode", "single"),
        "duration_seconds": getattr(session_result, "duration_seconds", 0.0),
        "preflight": getattr(session_result, "preflight", {}),
        "breaking_point": getattr(session_result, "breaking_point", None),
        "adaptive_events_count": len(getattr(session_result, "adaptive_events", []) or []),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_events_jsonl(path: Path, session_result: Any) -> None:
    events = getattr(session_result, "adaptive_events", []) or []
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=True) + "\n")
