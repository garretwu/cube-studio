"""HTML report renderer for load simulator session results."""
from __future__ import annotations

from html import escape
from typing import Any

from load_simulator.reporting.charts import (
    extract_agent_metric_matrix,
    render_bottleneck_table,
    render_metric_table,
)


def _render_agent_summary(agent_results: list[Any]) -> str:
    if not agent_results:
        return "<p>No agent results.</p>"
    rows = []
    for item in agent_results:
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.name))}</td>"
            f"<td>{escape(str(item.status))}</td>"
            f"<td>{len(item.errors or [])}</td>"
            f"<td>{item.duration_seconds:.2f}s</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>agent</th><th>status</th><th>errors</th><th>duration</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_comparison_block(historical_comparison: dict[str, Any] | None) -> str:
    if not historical_comparison:
        return ""
    duration = historical_comparison.get("duration_seconds", {})
    return (
        "<h3>Historical Comparison</h3>"
        f"<p>Duration delta: {float(duration.get('delta', 0.0)):.2f}s "
        f"(current={float(duration.get('current', 0.0)):.2f}s, "
        f"baseline={float(duration.get('baseline', 0.0)):.2f}s)</p>"
    )


def _render_preflight_block(preflight: dict[str, Any] | None) -> str:
    if not preflight:
        return "<h2>Preflight</h2><p>No preflight results.</p>"
    rows = []
    for name, item in preflight.items():
        ok = item.get("ok")
        if ok is True:
            ok_text = "yes"
            row_cls = "ok"
        elif ok is False:
            ok_text = "no"
            row_cls = "fail"
        else:
            ok_text = "n/a"
            row_cls = "na"
        rows.append(
            f"<tr class='preflight-{row_cls}'>"
            f"<td>{escape(str(name))}</td>"
            f"<td>{escape(ok_text)}</td>"
            f"<td>{escape(str(item.get('detail', '')))}</td>"
            "</tr>"
        )
    return (
        "<h2>Preflight</h2>"
        "<table><thead><tr><th>check</th><th>ok</th><th>detail</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_adaptive_block(adaptive_events: list[dict[str, Any]] | None, breaking_point: dict[str, Any] | None) -> str:
    if not adaptive_events:
        return "<h2>Adaptive Rules</h2><p>No adaptive events recorded.</p>"
    rows = []
    for ev in adaptive_events:
        rows.append(
            "<tr>"
            f"<td>{escape(str(ev.get('stage', '')))}</td>"
            f"<td>{escape(str(ev.get('action', '')))}</td>"
            f"<td>{escape(str(ev.get('reason', '')))}</td>"
            "</tr>"
        )
    bp_html = ""
    if breaking_point:
        bp_html = (
            "<p><strong>Breaking point:</strong> "
            f"stage={escape(str(breaking_point.get('stage', '')))}, "
            f"scale={escape(str(breaking_point.get('concurrency_scale', '')))}, "
            f"reason={escape(str(breaking_point.get('reason', '')))}</p>"
        )
    return (
        "<h2>Adaptive Rules</h2>"
        + bp_html
        + "<table><thead><tr><th>stage</th><th>action</th><th>reason</th></tr></thead>"
        + f"<tbody>{''.join(rows)}</tbody></table>"
    )


def generate_html_report(session_result: Any, historical_comparison: dict[str, Any] | None = None) -> str:
    """Render a standalone HTML report."""
    matrix = extract_agent_metric_matrix(getattr(session_result, "agent_results", []))
    metric_table = render_metric_table("Agent Numeric Metrics", matrix)
    bottlenecks = render_bottleneck_table(getattr(session_result, "bottlenecks", []))
    comparison = _render_comparison_block(historical_comparison)
    preflight = _render_preflight_block(getattr(session_result, "preflight", None))
    adaptive = _render_adaptive_block(
        getattr(session_result, "adaptive_events", None),
        getattr(session_result, "breaking_point", None),
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Load Simulator Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #1f2937; }}
    h1, h2, h3 {{ color: #111827; }}
    table {{ border-collapse: collapse; width: 100%; margin: 10px 0 24px 0; }}
    th, td {{ border: 1px solid #d1d5db; padding: 6px 8px; text-align: left; font-size: 14px; }}
    th {{ background: #f3f4f6; }}
    .meta {{ background: #f9fafb; border: 1px solid #e5e7eb; padding: 12px; }}
    .summary {{ white-space: pre-wrap; }}
    .preflight-fail td {{ background: #fee2e2; }}
    .preflight-ok td {{ background: #dcfce7; }}
  </style>
</head>
<body>
  <h1>Load Simulator Report</h1>
  <div class="meta">
    <p><strong>session_id:</strong> {escape(str(getattr(session_result, "session_id", "")))}</p>
    <p><strong>duration_seconds:</strong> {float(getattr(session_result, "duration_seconds", 0.0)):.2f}</p>
  </div>

  <h2>Agent Summary</h2>
  {_render_agent_summary(getattr(session_result, "agent_results", []))}

  <h2>Metrics</h2>
  {metric_table}

  {preflight}
  {adaptive}

  <h2>Bottleneck Analysis</h2>
  {bottlenecks}

  <h2>Text Summary</h2>
  <div class="summary">{escape(str(getattr(session_result, "summary", "")))}</div>

  {comparison}
</body>
</html>
"""
