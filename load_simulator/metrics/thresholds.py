"""
6-layer bottleneck threshold engine.

Layers:
  1. GPU_COMPUTE   — GPU compute utilization & SM activity
  2. GPU_MEMORY    — GPU memory & KV-cache utilization
  3. NVLINK_PCIE   — NVLink / PCIe bandwidth
  4. NETWORK_RDMA  — RDMA bandwidth, PFC pause frames, P99 latency
  5. STORAGE_IO    — Disk utilization & IO-wait
  6. CPU_SYSTEM    — CPU and system memory utilization
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ThresholdRule:
    layer: str
    metric: str
    warning: float
    critical: float
    unit: str
    description: str


BOTTLENECK_THRESHOLDS: list[ThresholdRule] = [
    # ── Layer 1: GPU Compute ────────────────────────────────────────────────
    ThresholdRule("GPU_COMPUTE", "gpu_util_pct",  85.0,  95.0, "%",       "GPU compute utilization"),
    ThresholdRule("GPU_COMPUTE", "sm_active_pct", 80.0,  90.0, "%",       "SM active cycles"),
    # ── Layer 2: GPU Memory ─────────────────────────────────────────────────
    ThresholdRule("GPU_MEMORY",  "gpu_mem_util_pct",  80.0, 92.0, "%",    "GPU memory utilization"),
    ThresholdRule("GPU_MEMORY",  "kv_cache_util_pct", 85.0, 95.0, "%",    "KV cache utilization"),
    # ── Layer 3: NVLink / PCIe ──────────────────────────────────────────────
    ThresholdRule("NVLINK_PCIE", "nvlink_bw_gbps",    0.0,  0.0,  "GB/s", "NVLink bandwidth (lower=better)"),
    ThresholdRule("NVLINK_PCIE", "pcie_bw_util_pct",  70.0, 85.0, "%",    "PCIe bandwidth utilization"),
    # ── Layer 4: Network / RDMA ─────────────────────────────────────────────
    ThresholdRule("NETWORK_RDMA", "rdma_bw_util_pct", 80.0,  90.0,    "%",         "RDMA bandwidth utilization"),
    ThresholdRule("NETWORK_RDMA", "pfc_pause_frames",  1000.0, 10000.0, "frames/s", "PFC pause frames"),
    ThresholdRule("NETWORK_RDMA", "p99_latency_ms",    50.0,  200.0,   "ms",        "P99 network latency"),
    # ── Layer 5: Storage I/O ────────────────────────────────────────────────
    ThresholdRule("STORAGE_IO",  "disk_util_pct", 70.0, 90.0, "%",       "Disk utilization"),
    ThresholdRule("STORAGE_IO",  "io_wait_pct",   20.0, 40.0, "%",       "IO wait percentage"),
    # ── Layer 6: CPU / System ───────────────────────────────────────────────
    ThresholdRule("CPU_SYSTEM",  "cpu_util_pct",  75.0, 90.0, "%",       "CPU utilization"),
    ThresholdRule("CPU_SYSTEM",  "mem_util_pct",  80.0, 92.0, "%",       "System memory utilization"),
]


def check_bottlenecks(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Check *metrics* against every threshold rule.

    Args:
        metrics: A flat dictionary mapping metric name → numeric value.

    Returns:
        A list of bottleneck finding dicts, one per violated rule, sorted by
        severity (critical first) and then by layer order.
    """
    findings: list[dict[str, Any]] = []
    for rule in BOTTLENECK_THRESHOLDS:
        value = metrics.get(rule.metric)
        if value is None:
            continue
        # NVLink rule has warning=0 and critical=0 — skip automatic threshold check
        if rule.critical <= 0 and rule.warning <= 0:
            continue

        if rule.critical > 0 and value >= rule.critical:
            severity = "critical"
            threshold = rule.critical
        elif rule.warning > 0 and value >= rule.warning:
            severity = "warning"
            threshold = rule.warning
        else:
            continue

        findings.append(
            {
                "layer": rule.layer,
                "metric": rule.metric,
                "value": value,
                "threshold": threshold,
                "severity": severity,
                "unit": rule.unit,
                "description": rule.description,
            }
        )

    # Sort: critical before warning, then by insertion order (layer order)
    findings.sort(key=lambda f: (0 if f["severity"] == "critical" else 1))
    return findings


def get_layer_summary(findings: list[dict[str, Any]]) -> dict[str, str]:
    """Return a mapping of layer → worst severity for that layer.

    Args:
        findings: Output of :func:`check_bottlenecks`.

    Returns:
        Dict like ``{"GPU_COMPUTE": "critical", "CPU_SYSTEM": "warning"}``.
    """
    summary: dict[str, str] = {}
    order = {"critical": 0, "warning": 1, "ok": 2}
    for finding in findings:
        layer = finding["layer"]
        sev = finding["severity"]
        existing = summary.get(layer, "ok")
        if order.get(sev, 2) < order.get(existing, 2):
            summary[layer] = sev
    return summary
