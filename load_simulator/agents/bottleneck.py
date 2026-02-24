"""BottleneckAnalyzer — applies threshold rules to collected metrics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from load_simulator.metrics.thresholds import (
    BOTTLENECK_THRESHOLDS,
    check_bottlenecks,
    get_layer_summary,
)


@dataclass
class BottleneckReport:
    """Structured bottleneck analysis output."""

    findings: list[dict[str, Any]]
    layer_summary: dict[str, str]  # layer → "ok" | "warning" | "critical"
    text_report: str
    has_critical: bool
    has_warning: bool


class BottleneckAnalyzer:
    """Applies the 6-layer threshold rules to a flat metrics dict.

    Usage::

        analyzer = BottleneckAnalyzer()
        report = analyzer.analyze({"cpu_util_pct": 88.0, "gpu_util_pct": 96.0})
        print(report.text_report)
    """

    def analyze(self, metrics: dict[str, Any]) -> BottleneckReport:
        """Run all threshold rules against *metrics*.

        Args:
            metrics: Flat dict of metric name → float value.

        Returns:
            A :class:`BottleneckReport` with findings and a text summary.
        """
        findings = check_bottlenecks(metrics)
        layer_summary = get_layer_summary(findings)
        text = self.generate_summary(findings, layer_summary, metrics)
        has_critical = any(f["severity"] == "critical" for f in findings)
        has_warning = any(f["severity"] == "warning" for f in findings)
        return BottleneckReport(
            findings=findings,
            layer_summary=layer_summary,
            text_report=text,
            has_critical=has_critical,
            has_warning=has_warning,
        )

    def generate_summary(
        self,
        findings: list[dict[str, Any]],
        layer_summary: dict[str, str],
        metrics: dict[str, Any],
    ) -> str:
        """Return a human-readable plain-text bottleneck report.

        Args:
            findings:      Output of :func:`check_bottlenecks`.
            layer_summary: Output of :func:`get_layer_summary`.
            metrics:       The original flat metrics dict.

        Returns:
            Multi-line string report.
        """
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("  BOTTLENECK ANALYSIS REPORT  (6-Layer Threshold Engine)")
        lines.append("=" * 72)
        lines.append("")

        # Layer status overview
        all_layers = [r.layer for r in BOTTLENECK_THRESHOLDS]
        seen: set[str] = set()
        ordered_layers: list[str] = []
        for layer in all_layers:
            if layer not in seen:
                ordered_layers.append(layer)
                seen.add(layer)

        lines.append("Layer Status Overview:")
        for layer in ordered_layers:
            status = layer_summary.get(layer, "ok")
            icon = {"ok": "[OK]", "warning": "[WARN]", "critical": "[CRIT]"}.get(status, "[?]")
            lines.append(f"  {icon:8s} {layer}")
        lines.append("")

        if not findings:
            lines.append("  No bottlenecks detected. All metrics within thresholds.")
        else:
            lines.append(f"Findings ({len(findings)} total):")
            lines.append("")
            for f in findings:
                sev_tag = "[CRITICAL]" if f["severity"] == "critical" else "[ WARNING]"
                lines.append(
                    f"  {sev_tag} Layer={f['layer']:<14s} Metric={f['metric']:<22s} "
                    f"Value={f['value']}{f['unit']}  Threshold={f['threshold']}{f['unit']}"
                )
                lines.append(f"             → {f['description']}")
                lines.append("")

        lines.append("=" * 72)
        return "\n".join(lines)

    def enrich_agent_metrics(
        self, agent_metrics: dict[str, Any], system_metrics: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge per-agent metrics with system metrics for threshold analysis.

        Args:
            agent_metrics:  Metrics from one or more agents (may include
                            latency_p99_ms as a proxy for p99_latency_ms, etc.).
            system_metrics: Output of :meth:`MetricsMonitor.aggregate`.

        Returns:
            Merged dict ready for :meth:`analyze`.
        """
        merged = dict(system_metrics)
        # Map agent-level p99 latency to the RDMA metric name if not present
        if "p99_latency_ms" not in merged and "latency_p99_ms" in agent_metrics:
            merged["p99_latency_ms"] = agent_metrics["latency_p99_ms"]
        merged.update({k: v for k, v in agent_metrics.items() if k not in merged})
        return merged
