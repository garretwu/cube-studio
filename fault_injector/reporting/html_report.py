from __future__ import annotations

from fault_injector.reporting.charts import build_chart_payload
from fault_injector.reporting.resilience import summarize_resilience
from fault_injector.reporting.timeline import build_timeline
from fault_injector.orchestrator.session import ActionResult


def render_html_report(reports: list[ActionResult]) -> str:
    summary = summarize_resilience(reports)
    timeline = build_timeline(reports)
    chart = build_chart_payload(summary)
    return (
        "<html><body>"
        f"<h1>Fault Injection Report</h1><pre>{summary}</pre>"
        f"<pre>{chart}</pre><pre>{timeline}</pre>"
        "</body></html>"
    )
