"""
Reporting module - Fault injection report generation.

This module provides report generation capabilities:
- Timeline: Event timeline visualization
- HTMLReport: HTML report generation
- Charts: Plotly/metric charts
- Resilience: Resilience scoring algorithm
"""

__all__ = [
    "TimelineBuilder",
    "HTMLReporter",
    "ChartGenerator",
    "ResilienceScorer",
]


import json
from datetime import datetime
from pathlib import Path
from typing import Any


class TimelineBuilder:
    """Build fault injection event timeline."""

    def __init__(self):
        self.events = []

    def add_event(self, timestamp: str, event: str, details: dict | None = None) -> None:
        """Add an event to the timeline."""
        self.events.append({
            "timestamp": timestamp,
            "event": event,
            "details": details or {},
        })

    def build(self) -> list[dict[str, Any]]:
        """Build the timeline sorted by timestamp."""
        return sorted(self.events, key=lambda x: x["timestamp"])

    def to_html(self) -> str:
        """Generate HTML timeline."""
        events = self.build()
        if not events:
            return "<p>No events recorded.</p>"

        html = '<div class="timeline">'
        for ev in events:
            ts = ev.get("timestamp", "")
            event = ev.get("event", "")
            details = ev.get("details", {})
            details_str = ", ".join(f"{k}={v}" for k, v in details.items()) if details else ""
            html += f'''
            <div class="timeline-item">
                <div class="timeline-time">{ts}</div>
                <div class="timeline-event">{event}</div>
                <div class="timeline-details">{details_str}</div>
            </div>'''
        html += "</div>"
        return html


class HTMLReporter:
    """Generate HTML reports from fault injection sessions."""

    def __init__(self, template_dir: str | None = None):
        self.template_dir = Path(template_dir) if template_dir else None

    def generate(self, session: Any, output_path: str) -> str:
        """Generate HTML report from session.

        Args:
            session: Session object containing fault injection results
            output_path: Path to save the HTML report

        Returns:
            Path to the generated HTML file
        """
        html = self._build_html(session)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            f.write(html)
        return str(output)

    def _build_html(self, session: Any) -> str:
        """Build complete HTML document."""
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fault Injection Report - {getattr(session, 'session_id', 'N/A')}</title>
    {self._get_css()}
</head>
<body>
    <div class="container">
        {self._build_header(session)}
        {self._build_summary(session)}
        {self._build_scenario_results(session)}
        {self._build_timeline(session)}
        {self._build_metrics(session)}
        {self._build_wal_status(session)}
        {self._build_footer()}
    </div>
</body>
</html>'''

    def _get_css(self) -> str:
        """Get CSS styles for the report."""
        return '''<style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f7fa;
            color: #333;
            line-height: 1.6;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .card {
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            margin-bottom: 20px;
            overflow: hidden;
        }
        .card-header {
            background: #4a90d9;
            color: white;
            padding: 15px 20px;
            font-size: 18px;
            font-weight: 600;
        }
        .card-body { padding: 20px; }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 20px;
            text-align: center;
        }
        .header h1 { font-size: 28px; margin-bottom: 10px; }
        .header .subtitle { opacity: 0.9; font-size: 14px; }
        .status-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .status-running { background: #ffc107; color: #333; }
        .status-completed { background: #28a745; color: white; }
        .status-failed { background: #dc3545; color: white; }
        .status-recovered { background: #17a2b8; color: white; }
        .status-paused { background: #6c757d; color: white; }
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        .summary-item {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 6px;
            text-align: center;
        }
        .summary-item .label { font-size: 12px; color: #666; text-transform: uppercase; }
        .summary-item .value { font-size: 24px; font-weight: 700; color: #333; }
        .summary-item .value.success { color: #28a745; }
        .summary-item .value.error { color: #dc3545; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #f8f9fa; font-weight: 600; font-size: 12px; text-transform: uppercase; color: #666; }
        tr:hover { background: #f8f9fa; }
        .result-success { color: #28a745; font-weight: 600; }
        .result-error { color: #dc3545; font-weight: 600; }
        .result-pending { color: #ffc107; font-weight: 600; }
        .timeline { position: relative; padding-left: 30px; }
        .timeline::before {
            content: '';
            position: absolute;
            left: 10px;
            top: 0;
            bottom: 0;
            width: 2px;
            background: #e0e0e0;
        }
        .timeline-item {
            position: relative;
            padding: 15px 0;
            border-left: 2px solid #4a90d9;
            margin-left: -12px;
            padding-left: 20px;
        }
        .timeline-item::before {
            content: '';
            position: absolute;
            left: -6px;
            top: 20px;
            width: 10px;
            height: 10px;
            background: #4a90d9;
            border-radius: 50%;
        }
        .timeline-time { font-size: 12px; color: #666; }
        .timeline-event { font-weight: 600; margin: 5px 0; }
        .timeline-details { font-size: 13px; color: #888; font-family: monospace; }
        .metrics-comparison {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }
        .metric-card {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 6px;
        }
        .metric-card h4 { margin-bottom: 10px; color: #333; }
        .metric-row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #eee;
        }
        .metric-row:last-child { border-bottom: none; }
        .metric-label { color: #666; }
        .metric-value { font-weight: 600; }
        .metric-baseline { color: #4a90d9; }
        .metric-observed { color: #e67e22; }
        .wal-status { display: flex; gap: 20px; flex-wrap: wrap; }
        .wal-item {
            flex: 1;
            min-width: 150px;
            background: #f8f9fa;
            padding: 15px;
            border-radius: 6px;
            text-align: center;
        }
        .wal-item .count { font-size: 28px; font-weight: 700; }
        .wal-item .label { font-size: 12px; color: #666; text-transform: uppercase; }
        .footer {
            text-align: center;
            padding: 20px;
            color: #666;
            font-size: 12px;
        }
        </style>'''

    def _build_header(self, session: Any) -> str:
        """Build report header."""
        session_id = getattr(session, "session_id", "N/A")
        status = getattr(session, "status", "unknown")
        status_value = status.value if hasattr(status, "value") else str(status)
        status_class = f"status-{status_value}"

        return f'''
        <div class="header">
            <h1>Fault Injection Report</h1>
            <div class="subtitle">Session: {session_id}</div>
            <span class="status-badge {status_class}">{status_value}</span>
        </div>'''

    def _build_summary(self, session: Any) -> str:
        """Build summary section."""
        started = getattr(session, "started_at", None)
        finished = getattr(session, "finished_at", None)
        duration = ""
        if started and finished:
            delta = finished - started
            duration = str(delta)

        status = getattr(session, "status", "unknown")
        status_value = status.value if hasattr(status, "value") else str(status)

        results = getattr(session, "scenario_results", {})
        total = len(results)
        success = sum(1 for r in results.values() if getattr(r, "inject_success", False))
        failed = sum(1 for r in results.values() if getattr(r, "inject_success", False) == False and getattr(r, "error", ""))
        active_faults = len(getattr(session, "active_faults", []))

        return f'''
        <div class="card">
            <div class="card-header">Session Summary</div>
            <div class="card-body">
                <div class="summary-grid">
                    <div class="summary-item">
                        <div class="label">Status</div>
                        <div class="value">{status_value}</div>
                    </div>
                    <div class="summary-item">
                        <div class="label">Duration</div>
                        <div class="value">{duration or "N/A"}</div>
                    </div>
                    <div class="summary-item">
                        <div class="label">Total Scenarios</div>
                        <div class="value">{total}</div>
                    </div>
                    <div class="summary-item">
                        <div class="label">Successful</div>
                        <div class="value success">{success}</div>
                    </div>
                    <div class="summary-item">
                        <div class="label">Failed</div>
                        <div class="value error">{failed}</div>
                    </div>
                    <div class="summary-item">
                        <div class="label">Active Faults</div>
                        <div class="value">{active_faults}</div>
                    </div>
                </div>
            </div>
        </div>'''

    def _build_scenario_results(self, session: Any) -> str:
        """Build scenario results table."""
        results = getattr(session, "scenario_results", {})

        if not results:
            return '''
            <div class="card">
                <div class="card-header">Scenario Results</div>
                <div class="card-body">
                    <p>No scenario results available.</p>
                </div>
            </div>'''

        rows = ""
        for name, result in results.items():
            inject_status = getattr(result, "inject_success", False)
            recover_status = getattr(result, "recover_success", False)
            verified = getattr(result, "verified", False)
            error = getattr(result, "error", "")

            inject_class = "result-success" if inject_status else "result-error"
            inject_text = "Success" if inject_status else "Failed"

            recover_class = "result-success" if recover_status else "result-pending"
            recover_text = "Success" if recover_status else "Pending"

            verified_class = "result-success" if verified else "result-pending"
            verified_text = "Verified" if verified else "Pending"

            rows += f'''
            <tr>
                <td>{name}</td>
                <td class="{inject_class}">{inject_text}</td>
                <td class="{recover_class}">{recover_text}</td>
                <td class="{verified_class}">{verified_text}</td>
                <td>{error or '-'}</td>
            </tr>'''

        return f'''
        <div class="card">
            <div class="card-header">Scenario Results</div>
            <div class="card-body">
                <table>
                    <thead>
                        <tr>
                            <th>Scenario</th>
                            <th>Inject</th>
                            <th>Recover</th>
                            <th>Verify</th>
                            <th>Error</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        </div>'''

    def _build_timeline(self, session: Any) -> str:
        """Build event timeline."""
        events = getattr(session, "events", [])

        if not events:
            return '''
            <div class="card">
                <div class="card-header">Event Timeline</div>
                <div class="card-body">
                    <p>No events recorded.</p>
                </div>
            </div>'''

        sorted_events = sorted(events, key=lambda x: x.get("timestamp", ""))
        timeline_items = ""

        for ev in sorted_events:
            ts = ev.get("timestamp", "")
            event = ev.get("event", "")
            details = ev.get("details", {})
            details_str = ", ".join(f"{k}={v}" for k, v in details.items()) if details else ""

            timeline_items += f'''
            <div class="timeline-item">
                <div class="timeline-time">{ts}</div>
                <div class="timeline-event">{event}</div>
                <div class="timeline-details">{details_str}</div>
            </div>'''

        return f'''
        <div class="card">
            <div class="card-header">Event Timeline</div>
            <div class="card-body">
                <div class="timeline">
                    {timeline_items}
                </div>
            </div>
        </div>'''

    def _build_metrics(self, session: Any) -> str:
        """Build metrics comparison section."""
        baseline = getattr(session, "baseline_metrics", {})

        if not baseline:
            return '''
            <div class="card">
                <div class="card-header">Metrics Comparison</div>
                <div class="card-body">
                    <p>No baseline metrics available.</p>
                </div>
            </div>'''

        metric_cards = ""
        for key, value in baseline.items():
            if isinstance(value, dict):
                baseline_val = value.get("baseline", "N/A")
                observed_val = value.get("observed", "N/A")
                metric_cards += f'''
                <div class="metric-card">
                    <h4>{key}</h4>
                    <div class="metric-row">
                        <span class="metric-label">Baseline</span>
                        <span class="metric-value metric-baseline">{baseline_val}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Observed</span>
                        <span class="metric-value metric-observed">{observed_val}</span>
                    </div>
                </div>'''

        return f'''
        <div class="card">
            <div class="card-header">Metrics Comparison</div>
            <div class="card-body">
                <div class="metrics-comparison">
                    {metric_cards}
                </div>
            </div>
        </div>'''

    def _build_wal_status(self, session: Any) -> str:
        """Build WAL status section."""
        wal_path = getattr(session, "rollback_journal_path", "")

        if not wal_path:
            return '''
            <div class="card">
                <div class="card-header">WAL Status</div>
                <div class="card-body">
                    <p>No WAL journal path available.</p>
                </div>
            </div>'''

        wal_entries = []
        try:
            wal_file = Path(wal_path)
            if wal_file.exists():
                with open(wal_file, "r") as f:
                    for line in f:
                        if line.strip():
                            wal_entries.append(json.loads(line))
        except Exception:
            pass

        active = sum(1 for e in wal_entries if e.get("status") == "active")
        recovered = sum(1 for e in wal_entries if e.get("status") == "recovered")
        failed = sum(1 for e in wal_entries if e.get("status") == "failed")

        return f'''
        <div class="card">
            <div class="card-header">WAL Status</div>
            <div class="card-body">
                <p style="margin-bottom: 15px;"><strong>Journal:</strong> {wal_path}</p>
                <div class="wal-status">
                    <div class="wal-item">
                        <div class="count">{active}</div>
                        <div class="label">Active</div>
                    </div>
                    <div class="wal-item">
                        <div class="count">{recovered}</div>
                        <div class="label">Recovered</div>
                    </div>
                    <div class="wal-item">
                        <div class="count">{failed}</div>
                        <div class="label">Failed</div>
                    </div>
                </div>
            </div>
        </div>'''

    def _build_footer(self) -> str:
        """Build report footer."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f'''
        <div class="footer">
            <p>Generated by Fault Injector on {now}</p>
        </div>'''


class ChartGenerator:
    """Generate charts for fault injection reports."""

    def __init__(self):
        pass

    def generate_metrics_chart(self, metrics_data: dict[str, Any]) -> str:
        """Generate metrics comparison chart (returns placeholder).

        Args:
            metrics_data: Dictionary of metrics with baseline and observed values

        Returns:
            HTML string for the chart (placeholder)
        """
        # Placeholder - could be extended with Chart.js or Plotly
        return f'''
        <div class="chart-placeholder">
            <p>Metrics Chart (placeholder)</p>
            <pre>{json.dumps(metrics_data, indent=2)}</pre>
        </div>'''

    def generate_resilience_radar(self, scores: dict[str, float]) -> str:
        """Generate resilience radar chart (returns placeholder).

        Args:
            scores: Dictionary of category scores

        Returns:
            HTML string for the radar chart (placeholder)
        """
        # Placeholder - could be extended with Chart.js or Plotly
        return f'''
        <div class="radar-placeholder">
            <p>Resilience Radar (placeholder)</p>
            <pre>{json.dumps(scores, indent=2)}</pre>
        </div>'''


class ResilienceScorer:
    """Calculate resilience scores for fault injection sessions."""

    def __init__(self):
        self.categories = [
            "auto_recovery",
            "degradation_handling",
            "fault_isolation",
            "data_integrity",
        ]

    def calculate(self, session: Any) -> dict[str, Any]:
        """Calculate overall resilience score based on session results.

        Args:
            session: Session object with scenario results

        Returns:
            Dictionary with overall score and category scores
        """
        results = getattr(session, "scenario_results", {})

        if not results:
            return {
                "overall": 0,
                "categories": {cat: 0 for cat in self.categories},
                "message": "No scenario results to calculate score",
            }

        # Calculate category scores
        scores = {}

        # Auto-recovery: ratio of successful recoveries
        recoveries = [getattr(r, "recover_success", False) for r in results.values()]
        scores["auto_recovery"] = (sum(recoveries) / len(recoveries) * 100) if recoveries else 0

        # Degradation handling: scenarios completed without critical errors
        completed = [getattr(r, "inject_success", False) and not getattr(r, "error", "")
                     for r in results.values()]
        scores["degradation_handling"] = (sum(completed) / len(completed) * 100) if completed else 0

        # Fault isolation: scenarios with verified status
        verified = [getattr(r, "verified", False) for r in results.values()]
        scores["fault_isolation"] = (sum(verified) / len(verified) * 100) if verified else 0

        # Data integrity: successful injects (data not corrupted)
        injects = [getattr(r, "inject_success", False) for r in results.values()]
        scores["data_integrity"] = (sum(injects) / len(injects) * 100) if injects else 0

        # Overall score (weighted average)
        weights = {
            "auto_recovery": 0.3,
            "degradation_handling": 0.3,
            "fault_isolation": 0.2,
            "data_integrity": 0.2,
        }
        overall = sum(scores[cat] * weights[cat] for cat in self.categories)

        return {
            "overall": round(overall, 1),
            "categories": {k: round(v, 1) for k, v in scores.items()},
        }
