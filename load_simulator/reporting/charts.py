"""Chart data helpers and Plotly chart rendering for HTML reports."""
from __future__ import annotations

import json
from html import escape
from typing import Any

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def extract_agent_metric_matrix(agent_results: list[Any]) -> dict[str, dict[str, float]]:
    """Extract numeric metrics by agent name."""
    matrix: dict[str, dict[str, float]] = {}
    for result in agent_results:
        numeric: dict[str, float] = {}
        for key, value in (result.metrics or {}).items():
            if isinstance(value, (int, float)):
                numeric[key] = float(value)
        matrix[result.name] = numeric
    return matrix


def render_metric_table(title: str, matrix: dict[str, dict[str, float]]) -> str:
    """Render a compact HTML table for numeric metrics."""
    metric_names: list[str] = sorted({k for row in matrix.values() for k in row.keys()})
    if not metric_names:
        return f"<h3>{escape(title)}</h3><p>No numeric metrics.</p>"

    header = "".join(f"<th>{escape(name)}</th>" for name in metric_names)
    rows = []
    for agent, metrics in matrix.items():
        cells = "".join(f"<td>{metrics.get(name, 0.0):.3f}</td>" for name in metric_names)
        rows.append(f"<tr><td>{escape(agent)}</td>{cells}</tr>")

    return (
        f"<h3>{escape(title)}</h3>"
        "<table>"
        f"<thead><tr><th>agent</th>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def render_bottleneck_table(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "<h3>Bottlenecks</h3><p>No bottlenecks detected.</p>"
    rows = []
    for item in findings:
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('severity', '')))}</td>"
            f"<td>{escape(str(item.get('layer', '')))}</td>"
            f"<td>{escape(str(item.get('metric', '')))}</td>"
            f"<td>{escape(str(item.get('value', '')))}</td>"
            f"<td>{escape(str(item.get('threshold', '')))}</td>"
            "</tr>"
        )
    return (
        "<h3>Bottlenecks</h3>"
        "<table><thead><tr><th>severity</th><th>layer</th><th>metric</th>"
        "<th>value</th><th>threshold</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


# ---------------------------------------------------------------------------
# Plotly chart helpers
# ---------------------------------------------------------------------------

def _plotly_page(title: str, div_id: str, plotly_js: str) -> str:
    """Wrap a Plotly.newPlot() call in a standalone HTML page."""
    return (
        "<!DOCTYPE html>\n"
        f"<html><head><meta charset='utf-8'><title>{escape(title)}</title>\n"
        f"<script src='{PLOTLY_CDN}'></script>\n"
        "</head><body>\n"
        f"<div id='{div_id}' style='width:100%;height:500px;'></div>\n"
        "<script>\n"
        f"{plotly_js}\n"
        "</script>\n"
        "</body></html>"
    )


def _no_data_page(title: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        f"<html><head><meta charset='utf-8'><title>{escape(title)}</title></head>\n"
        f"<body><h3>{escape(title)}</h3><p>No data available for this chart.</p></body></html>"
    )


def _extract_request_logs(agent_results: list[Any]) -> list[dict[str, Any]]:
    """Gather all request_logs from agent results."""
    logs: list[dict[str, Any]] = []
    for ar in agent_results:
        raw = getattr(ar, "raw", None) or {}
        logs.extend(raw.get("request_logs", []))
    return logs


def render_latency_timeline(agent_results: list[Any]) -> str:
    """Scatter plot of per-request latency over time."""
    logs = _extract_request_logs(agent_results)
    if not logs:
        return _no_data_page("Latency Timeline")

    timestamps = [entry.get("timestamp", 0) for entry in logs]
    latencies = [entry.get("latency_ms", 0) for entry in logs]
    # Normalize timestamps to seconds-from-start
    t0 = min(timestamps) if timestamps else 0
    x = [round(t - t0, 3) for t in timestamps]

    trace = {
        "x": x,
        "y": latencies,
        "mode": "markers",
        "type": "scatter",
        "marker": {"size": 4, "opacity": 0.6},
        "name": "latency",
    }
    layout = {
        "title": "Request Latency Timeline",
        "xaxis": {"title": "Time (s)"},
        "yaxis": {"title": "Latency (ms)"},
    }
    js = f"Plotly.newPlot('chart', [{json.dumps(trace)}], {json.dumps(layout)});"
    return _plotly_page("Latency Timeline", "chart", js)


def render_throughput_timeline(agent_results: list[Any], bucket_seconds: int = 5) -> str:
    """Bar chart of requests/sec per time bucket."""
    logs = _extract_request_logs(agent_results)
    if not logs:
        return _no_data_page("Throughput Timeline")

    timestamps = [entry.get("timestamp", 0) for entry in logs]
    t0 = min(timestamps)
    # Bucket requests
    buckets: dict[int, int] = {}
    for t in timestamps:
        bucket = int((t - t0) // bucket_seconds)
        buckets[bucket] = buckets.get(bucket, 0) + 1

    if not buckets:
        return _no_data_page("Throughput Timeline")

    max_bucket = max(buckets.keys())
    x = [i * bucket_seconds for i in range(max_bucket + 1)]
    y = [buckets.get(i, 0) / bucket_seconds for i in range(max_bucket + 1)]

    trace = {"x": x, "y": y, "type": "bar", "name": "req/s"}
    layout = {
        "title": "Throughput Timeline",
        "xaxis": {"title": "Time (s)"},
        "yaxis": {"title": "Requests/sec"},
    }
    js = f"Plotly.newPlot('chart', [{json.dumps(trace)}], {json.dumps(layout)});"
    return _plotly_page("Throughput Timeline", "chart", js)


def render_error_rate_timeline(agent_results: list[Any], bucket_seconds: int = 5) -> str:
    """Line chart of error counts per time bucket."""
    logs = _extract_request_logs(agent_results)
    if not logs:
        return _no_data_page("Error Rate Timeline")

    timestamps = [entry.get("timestamp", 0) for entry in logs]
    t0 = min(timestamps)
    error_buckets: dict[int, int] = {}
    total_buckets: dict[int, int] = {}
    for entry in logs:
        bucket = int((entry.get("timestamp", 0) - t0) // bucket_seconds)
        total_buckets[bucket] = total_buckets.get(bucket, 0) + 1
        if entry.get("error_message"):
            error_buckets[bucket] = error_buckets.get(bucket, 0) + 1

    if not total_buckets:
        return _no_data_page("Error Rate Timeline")

    max_bucket = max(total_buckets.keys())
    x = [i * bucket_seconds for i in range(max_bucket + 1)]
    y = [error_buckets.get(i, 0) for i in range(max_bucket + 1)]

    trace = {"x": x, "y": y, "type": "scatter", "mode": "lines+markers", "name": "errors"}
    layout = {
        "title": "Error Count Timeline",
        "xaxis": {"title": "Time (s)"},
        "yaxis": {"title": "Errors per bucket"},
    }
    js = f"Plotly.newPlot('chart', [{json.dumps(trace)}], {json.dumps(layout)});"
    return _plotly_page("Error Rate Timeline", "chart", js)


def render_resource_utilization(snapshots: list[dict[str, Any]]) -> str:
    """Multi-line chart of CPU/mem/GPU utilization over time."""
    if not snapshots:
        return _no_data_page("Resource Utilization")

    timestamps = [s.get("timestamp", 0) for s in snapshots]
    t0 = min(timestamps) if timestamps else 0
    x = [round(t - t0, 2) for t in timestamps]

    traces = []
    metric_keys = [
        ("cpu_util_pct", "CPU %"),
        ("mem_util_pct", "Memory %"),
        ("gpu_util_pct", "GPU %"),
        ("gpu_mem_util_pct", "GPU Mem %"),
        ("prom_pod_cpu", "Prom Pod CPU"),
        ("prom_gpu_util", "Prom GPU %"),
    ]
    for key, label in metric_keys:
        y = [s.get(key) for s in snapshots]
        if any(v is not None for v in y):
            traces.append({
                "x": x,
                "y": [v if v is not None else None for v in y],
                "type": "scatter",
                "mode": "lines",
                "name": label,
                "connectgaps": True,
            })

    if not traces:
        return _no_data_page("Resource Utilization")

    layout = {
        "title": "Resource Utilization",
        "xaxis": {"title": "Time (s)"},
        "yaxis": {"title": "Utilization"},
    }
    traces_json = ", ".join(json.dumps(t) for t in traces)
    js = f"Plotly.newPlot('chart', [{traces_json}], {json.dumps(layout)});"
    return _plotly_page("Resource Utilization", "chart", js)


def render_bottleneck_heatmap(findings: list[dict[str, Any]]) -> str:
    """Heatmap of bottleneck findings by layer and severity."""
    if not findings:
        return _no_data_page("Bottleneck Heatmap")

    layers = sorted({f.get("layer", "unknown") for f in findings})
    severity_map = {"info": 1, "warning": 2, "critical": 3}
    # Build a matrix: rows=layers, cols=metrics
    metrics_set = sorted({f.get("metric", "unknown") for f in findings})
    z: list[list[int]] = []
    for layer in layers:
        row: list[int] = []
        for metric in metrics_set:
            severity = 0
            for f in findings:
                if f.get("layer") == layer and f.get("metric") == metric:
                    severity = max(severity, severity_map.get(str(f.get("severity", "info")).lower(), 1))
            row.append(severity)
        z.append(row)

    trace = {
        "z": z,
        "x": metrics_set,
        "y": layers,
        "type": "heatmap",
        "colorscale": [[0, "#eee"], [0.33, "#ffffcc"], [0.66, "#fd8d3c"], [1.0, "#e31a1c"]],
        "zmin": 0,
        "zmax": 3,
    }
    layout = {"title": "Bottleneck Heatmap", "xaxis": {"title": "Metric"}, "yaxis": {"title": "Layer"}}
    js = f"Plotly.newPlot('chart', [{json.dumps(trace)}], {json.dumps(layout)});"
    return _plotly_page("Bottleneck Heatmap", "chart", js)


def render_stage_comparison(adaptive_events: list[dict[str, Any]]) -> str:
    """Grouped bar chart of key metrics per stage."""
    if not adaptive_events:
        return _no_data_page("Stage Comparison")

    stages = [e.get("stage", f"stage-{i}") for i, e in enumerate(adaptive_events)]
    error_rates = [e.get("metrics", {}).get("error_rate", 0) for e in adaptive_events]
    p99_latencies = [e.get("metrics", {}).get("latency_p99_ms", 0) for e in adaptive_events]

    trace_err = {"x": stages, "y": error_rates, "type": "bar", "name": "Error Rate"}
    trace_p99 = {"x": stages, "y": p99_latencies, "type": "bar", "name": "P99 Latency (ms)", "yaxis": "y2"}
    layout = {
        "title": "Stage Comparison",
        "barmode": "group",
        "xaxis": {"title": "Stage"},
        "yaxis": {"title": "Error Rate", "side": "left"},
        "yaxis2": {"title": "P99 Latency (ms)", "side": "right", "overlaying": "y"},
    }
    js = f"Plotly.newPlot('chart', [{json.dumps(trace_err)}, {json.dumps(trace_p99)}], {json.dumps(layout)});"
    return _plotly_page("Stage Comparison", "chart", js)
